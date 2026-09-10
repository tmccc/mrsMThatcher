"""Regression tests for bot asset selection."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

import pytest

from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import (
    isolate_regular_post_receipt,
    quote_analysis_for_lines,
    image_analysis_for_paths,
    mock_confirmed_main_post,
    write_image_analysis,
)


pytestmark = pytest.mark.allow_loopback_network


def test_used_history_json_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "used.json"

    bot.save_used_set(path, {3, 1, 2, 11})

    assert json.loads(path.read_text(encoding="utf-8")) == [1, 2, 3, 11]
    assert bot.load_used_set(path) == {1, 2, 3, 11}


def test_used_history_refuses_legacy_pickle_when_json_missing(tmp_path: Path) -> None:
    json_path = tmp_path / "used.json"
    pickle_path = tmp_path / "used.pickle"
    pickle_path.write_bytes(b"legacy pickle no longer trusted")

    with pytest.raises(bot.CorruptUsedHistoryError):
        bot.load_used_set(json_path, legacy_pickle_path=pickle_path)
    assert not json_path.exists()


def test_used_history_bad_json_with_legacy_pickle_fails_closed(tmp_path: Path) -> None:
    json_path = tmp_path / "used.json"
    pickle_path = tmp_path / "used.pickle"
    json_path.write_text("{bad json", encoding="utf-8")
    pickle_path.write_bytes(b"legacy pickle no longer trusted")

    with pytest.raises(bot.CorruptUsedHistoryError):
        bot.load_used_set(json_path, legacy_pickle_path=pickle_path)


def test_used_history_json_order_is_normalized_on_load(tmp_path: Path) -> None:
    json_path = tmp_path / "used.json"
    json_path.write_text("[11, 2, 1]", encoding="utf-8")

    assert bot.load_used_set(json_path) == {1, 2, 11}
    assert json.loads(json_path.read_text(encoding="utf-8")) == [1, 2, 11]


def test_used_history_missing_without_legacy_pickle_returns_empty_set(tmp_path: Path) -> None:
    json_path = tmp_path / "missing.json"

    assert bot.load_used_set(json_path) == set()


def test_quote_metadata_uses_current_quote_hash_not_stale_line_index() -> None:
    quote_hash = bot.quote_text_hash("Quote text")
    analysis = {"primary_topics": ["government"]}
    data = {"line_index": {"1": "stale"}, "items": {quote_hash: {"text": "Quote text", "analysis": analysis}}}

    assert bot.quote_metadata_for_hash(data, quote_hash, "Quote text") == analysis


def test_quote_override_deep_merges_only_requested_field() -> None:
    quote_hash = "hash1"
    raw = {
        "items": {
            quote_hash: {
                "text": "Quote text",
                "line_numbers": [175],
                "analysis": {
                    "seasonality": {
                        "hard_exclude_outside_windows": True,
                        "relevance": "strong",
                    },
                    "scores": {"general_post_suitability": 80},
                },
            }
        }
    }
    overrides = {
        "quote_overrides": {
            quote_hash: {
                "expected_line_numbers": [175],
                "expected_text": "Quote text",
                "analysis_patch": {"seasonality": {"hard_exclude_outside_windows": False}},
            }
        }
    }

    merged = bot.apply_quote_analysis_overrides(raw, overrides)

    assert raw["items"][quote_hash]["analysis"]["seasonality"]["hard_exclude_outside_windows"] is True
    assert merged["items"][quote_hash]["analysis"]["seasonality"]["hard_exclude_outside_windows"] is False
    assert merged["items"][quote_hash]["analysis"]["seasonality"]["relevance"] == "strong"
    assert merged["items"][quote_hash]["analysis"]["scores"]["general_post_suitability"] == 80


def test_stale_quote_override_is_warned_and_skipped(caplog: pytest.LogCaptureFixture) -> None:
    quote_hash = "hash1"
    raw = {
        "items": {
            quote_hash: {
                "text": "Current text",
                "line_numbers": [175],
                "analysis": {"seasonality": {"hard_exclude_outside_windows": True}},
            }
        }
    }
    overrides = {
        "quote_overrides": {
            quote_hash: {
                "expected_line_numbers": [175],
                "expected_text": "Old text",
                "analysis_patch": {"seasonality": {"hard_exclude_outside_windows": False}},
            }
        }
    }

    merged = bot.apply_quote_analysis_overrides(raw, overrides)

    assert merged["items"][quote_hash]["analysis"]["seasonality"]["hard_exclude_outside_windows"] is True
    assert "expected_text does not match" in caplog.text


def test_mm_dd_window_matching_supports_normal_and_cross_year_windows() -> None:
    assert bot.mm_dd_in_window("12-20", "12-10", "12-28")
    assert not bot.mm_dd_in_window("01-05", "12-10", "12-28")
    assert bot.mm_dd_in_window("01-05", "12-01", "02-28")
    assert bot.mm_dd_in_window("12-20", "12-01", "02-28")
    assert not bot.mm_dd_in_window("03-01", "12-01", "02-28")


def test_hard_seasonal_exclusion_outside_window() -> None:
    analysis = {
        "seasonality": {
            "hard_exclude_outside_windows": True,
            "relevance": "strong",
            "preferred_windows": [{"start_mm_dd": "12-10", "end_mm_dd": "12-28"}],
        }
    }

    status = bot.quote_season_status(analysis, today_mm_dd="07-05")

    assert status["hard_excluded"] is True
    assert bot.quote_candidate_weight(analysis, today_mm_dd="07-05")[0] == 0


def test_quote_cycle_seasonal_exhaustion_resets_without_selecting_hard_excluded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Normal quote.\nChristmas quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    normal_hash = bot.quote_text_hash("Normal quote.")
    christmas_analysis = {
        "seasonality": {
            "hard_exclude_outside_windows": True,
            "preferred_windows": [{"start_mm_dd": "12-10", "end_mm_dd": "12-28"}],
            "relevance": "strong",
        }
    }
    monkeypatch.setattr(
        bot,
        "load_quote_analysis",
        lambda: quote_analysis_for_lines(["Normal quote.", "Christmas quote."], {1: christmas_analysis}),
    )

    used = {normal_hash}
    chosen = bot.choose_unused_line_candidate(used)

    assert chosen["line_no"] == 0
    assert used == set()


def test_quote_cycle_resets_when_only_unused_quote_is_unanalysed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = ["Analysed quote.", "New unanalysed quote."]
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    analysed = quote_analysis_for_lines(["Analysed quote."])
    analysed_hash = bot.quote_text_hash("Analysed quote.")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: analysed)

    chosen = bot.choose_unused_line_candidate({analysed_hash})

    assert chosen["text"] == "Analysed quote."


def test_quote_cycle_resets_when_remaining_unused_quotes_are_unanalysed_and_hard_seasonal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = ["Analysed quote.", "Edited unanalysed quote.", "Christmas quote."]
    christmas = {
        "seasonality": {
            "hard_exclude_outside_windows": True,
            "preferred_windows": [{"start_mm_dd": "12-10", "end_mm_dd": "12-28"}],
            "relevance": "strong",
        }
    }
    analysed = quote_analysis_for_lines(["Analysed quote.", "Christmas quote."], {1: christmas})
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: analysed)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))

    chosen = bot.choose_unused_line_candidate({bot.quote_text_hash("Analysed quote.")})

    assert chosen["text"] == "Analysed quote."


def test_strong_in_window_quote_weight_exceeds_ordinary_weight() -> None:
    ordinary = {"seasonality": {"hard_exclude_outside_windows": False, "preferred_windows": [], "relevance": "none"}}
    strong = {
        "seasonality": {
            "hard_exclude_outside_windows": False,
            "relevance": "strong",
            "preferred_windows": [{"start_mm_dd": "12-10", "end_mm_dd": "12-28"}],
        }
    }

    assert bot.quote_candidate_weight(strong, today_mm_dd="12-20")[0] > bot.quote_candidate_weight(ordinary, today_mm_dd="12-20")[0]


def test_image_used_history_integer_entries_migrate_to_basenames(tmp_path: Path) -> None:
    paths = [tmp_path / "t01.jpg", tmp_path / "t02.jpg"]
    for path in paths:
        path.write_bytes(path.name.encode("utf-8"))
    images = [str(path) for path in paths]

    migrated, changed = bot.normalise_image_used_basenames({0, 1}, images, image_analysis_for_paths(paths))

    assert migrated == {"t01.jpg", "t02.jpg"}
    assert changed is True


def test_image_used_history_mixed_entries_migrate_and_preserve_missing_basenames(tmp_path: Path) -> None:
    paths = [tmp_path / "t01.jpg", tmp_path / "t02.jpg"]
    for path in paths:
        path.write_bytes(path.name.encode("utf-8"))
    images = [str(path) for path in paths]

    migrated, changed = bot.normalise_image_used_basenames({0, "t02.jpg", "missing.jpg"}, images, image_analysis_for_paths(paths))

    assert migrated == {"t01.jpg", "t02.jpg", "missing.jpg"}
    assert changed is True


def test_currently_eligible_image_cycle_resets_without_marking_seasonal_image_used(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_paths = []
    for name in ["t01.jpg", "t02.jpg", "t23.jpg"]:
        path = image_dir / name
        path.write_bytes(name.encode("utf-8"))
        image_paths.append(path)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(
        bot,
        "load_image_analysis",
        lambda: image_analysis_for_paths(
            image_paths,
            {
                "t01.jpg": {"pairing": {}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}, "seasonality": {"avoid_outside_season_or_occasion": False}},
                "t02.jpg": {"pairing": {}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}, "seasonality": {"avoid_outside_season_or_occasion": False}},
                "t23.jpg": {
                        "pairing": {},
                        "themes": [],
                        "tone": [],
                        "visual_energy": "low",
                        "quality": {},
                        "seasonality": {
                            "avoid_outside_season_or_occasion": True,
                            "occasions": ["christmas"],
                            "visible_season": "winter",
                        },
                },
            },
        ),
    )

    used = {"t01.jpg", "t02.jpg"}
    chosen = bot.choose_matched_unused_image(used, {"analysis": {"primary_topics": [], "secondary_topics": [], "tone": [], "visual_energy": "low"}}, {})

    assert chosen["basename"] in {"t01.jpg", "t02.jpg"}
    assert "t23.jpg" not in used
    assert used == set()


def test_seasonal_image_re_enters_when_current_date_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_paths = []
    for name in ["t01.jpg", "t23.jpg"]:
        path = image_dir / name
        path.write_bytes(name.encode("utf-8"))
        image_paths.append(path)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 12, 20))
    monkeypatch.setattr(
        bot,
        "load_image_analysis",
        lambda: image_analysis_for_paths(
            image_paths,
            {
                "t01.jpg": {"pairing": {}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}, "seasonality": {"avoid_outside_season_or_occasion": False}},
                "t23.jpg": {
                        "pairing": {"best_for_topics": ["christmas"]},
                        "themes": ["christmas"],
                        "tone": [],
                        "visual_energy": "low",
                        "quality": {},
                        "seasonality": {
                            "avoid_outside_season_or_occasion": True,
                            "occasions": ["christmas"],
                            "visible_season": "winter",
                        },
                },
            },
        ),
    )

    chosen = bot.choose_matched_unused_image(set(), {"analysis": {"primary_topics": ["christmas"], "secondary_topics": [], "tone": [], "visual_energy": "low"}}, {})

    assert chosen["basename"] == "t23.jpg"


def test_generated_image_pool_disabled_keeps_original_image_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    generated_dir = tmp_path / "generated"
    image_dir.mkdir()
    generated_dir.mkdir()
    original = image_dir / "t01.jpg"
    generated = generated_dir / ("tg_" + "a" * 64 + ".png")
    original.write_bytes(b"original")
    generated.write_bytes(b"generated")

    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", False)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))

    assert bot.current_image_paths() == [str(original)]


def test_generated_image_pool_merges_separate_analysis_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    generated_dir = tmp_path / "generated"
    image_dir.mkdir()
    generated_dir.mkdir()
    original = image_dir / "t01.jpg"
    quote_hash = "b" * 64
    generated = generated_dir / f"tg_{quote_hash}.png"
    original.write_bytes(b"original")
    generated.write_bytes(b"generated")
    original_analysis_path = tmp_path / "image_analysis.json"
    generated_analysis_path = tmp_path / "generated_image_analysis.json"
    write_image_analysis(original_analysis_path, image_analysis_for_paths([original]))
    write_image_analysis(generated_analysis_path, image_analysis_for_paths([generated]))

    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "*.png")
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", original_analysis_path)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ANALYSIS_FILE", str(generated_analysis_path))

    paths = bot.current_image_paths()
    analysis = bot.load_image_analysis()

    assert paths == [str(original), str(generated)]
    assert set(analysis["path_index"]) == {"t01.jpg", f"tg_{quote_hash}.png"}


def test_generated_image_pool_rejects_paths_outside_generated_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    generated_dir = tmp_path / "generated"
    outside_dir = tmp_path / "outside"
    image_dir.mkdir()
    generated_dir.mkdir()
    outside_dir.mkdir()
    original = image_dir / "t01.jpg"
    outside = outside_dir / ("tg_" + "e" * 64 + ".png")
    original.write_bytes(b"original")
    outside.write_bytes(b"outside")

    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "../outside/*.png")

    assert bot.current_image_paths() == [str(original)]


def test_generated_image_pool_fails_closed_on_unclassifiable_basename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    generated_dir = tmp_path / "generated"
    image_dir.mkdir()
    generated_dir.mkdir()
    original = image_dir / "t01.jpg"
    unclassifiable = generated_dir / "reviewed_ai.png"
    original.write_bytes(b"original")
    unclassifiable.write_bytes(b"generated")

    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "*.png")

    with pytest.raises(RuntimeError, match="unclassifiable"):
        bot.current_image_paths()


def test_generated_image_source_classification() -> None:
    quote_hash = "a" * 64

    assert bot.GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN == 2
    assert bot.image_selection_observability("t44.jpg") == {
        "image_source": "original",
        "origin_quote_hash": None,
        "origin_quote_match": False,
        "origin_quote_boost": 0.0,
    }
    assert bot.image_selection_observability(f"tg_{quote_hash}.png", quote_hash, 4) == {
        "image_source": "generated",
        "origin_quote_hash": quote_hash,
        "origin_quote_match": True,
        "origin_quote_boost": 4.0,
    }
    assert bot.image_selection_observability(f"tg_{quote_hash}.png", "b" * 64, 4) == {
        "image_source": "generated",
        "origin_quote_hash": quote_hash,
        "origin_quote_match": False,
        "origin_quote_boost": 0.0,
    }
    assert bot.generated_image_origin_quote_hash("tg_not-a-real-hash.png") is None
    assert bot.generated_image_origin_quote_hash("tg_" + ("a" * 63) + ".png") is None


def test_generated_image_spacing_helpers_and_state_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    generated = "tg_" + ("a" * 64) + ".png"
    state = {"original_regular_posts_since_generated_image": 0}
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 2)

    assert bot.generated_images_allowed_by_spacing(state) is False
    bot.update_regular_generated_image_spacing_state(state, "t01.jpg")
    assert state["original_regular_posts_since_generated_image"] == 1
    assert bot.generated_images_allowed_by_spacing(state) is False
    bot.update_regular_generated_image_spacing_state(state, "t02.jpg")
    assert state["original_regular_posts_since_generated_image"] == 2
    assert bot.generated_images_allowed_by_spacing(state) is True
    bot.update_regular_generated_image_spacing_state(state, "t03.jpg")
    assert state["original_regular_posts_since_generated_image"] == 2
    bot.update_regular_generated_image_spacing_state(state, generated)
    assert state["original_regular_posts_since_generated_image"] == 0


@pytest.mark.parametrize("value", [0, 2])
def test_generated_image_spacing_required_accepts_integers(monkeypatch: pytest.MonkeyPatch, value: int) -> None:
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", value)

    assert bot.generated_image_spacing_required() == value


def test_generated_image_spacing_zero_disables_restriction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 0)

    assert bot.generated_images_allowed_by_spacing({"original_regular_posts_since_generated_image": 0}) is True


@pytest.mark.parametrize("bad_value", [True, False, -1, 2.0, 2.5, "2", "x"])
def test_generated_image_spacing_helper_rejects_non_integer_values(
    monkeypatch: pytest.MonkeyPatch,
    bad_value: object,
) -> None:
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", bad_value)

    with pytest.raises(ValueError):
        bot.generated_image_spacing_required()


@pytest.mark.parametrize(
    ("last_image", "expected_count"),
    [
        ("tg_" + ("a" * 64) + ".png", 0),
        ("t01.jpg", 2),
        (None, 2),
    ],
)
def test_legacy_state_initialises_generated_spacing_from_last_regular_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    last_image: str | None,
    expected_count: int,
) -> None:
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 2)
    state = bot.default_state()
    state.pop("original_regular_posts_since_generated_image", None)
    state["last_regular_image_filename"] = last_image

    normalised = bot.normalise_state_candidate(state, path=tmp_path / "state.json")

    assert normalised is not None
    assert normalised["original_regular_posts_since_generated_image"] == expected_count


def test_state_rejects_boolean_generated_spacing_counter(tmp_path: Path) -> None:
    state = bot.default_state()
    state["original_regular_posts_since_generated_image"] = True

    assert bot.normalise_state_candidate(state, path=tmp_path / "state.json") is None


@pytest.mark.parametrize("value", [0, 2])
def test_runtime_config_validation_accepts_integer_generated_spacing(value: int) -> None:
    assert not bot.validate_runtime_config_values({"GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": value})


@pytest.mark.parametrize("bad_value", [True, False, 2.0, 2.5, "2", "x"])
def test_runtime_config_validation_rejects_non_integer_generated_spacing(bad_value: object) -> None:
    errors = bot.validate_runtime_config_values({"GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": bad_value})
    assert "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN must be an integer" in errors


def test_runtime_config_validation_rejects_negative_generated_spacing() -> None:
    errors = bot.validate_runtime_config_values({"GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": -1})

    assert "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN must be non-negative" in errors


def test_original_image_selection_returns_observability_and_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    original = image_dir / "t44.jpg"
    original.write_bytes(b"original")
    analysis_path = tmp_path / "image_analysis.json"
    analysis = image_analysis_for_paths([original])
    write_image_analysis(analysis_path, analysis)

    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", False)
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", analysis_path)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    caplog.set_level(logging.INFO, logger=bot.log.name)

    chosen = bot.choose_matched_unused_image(
        set(),
        {"quote_hash": "f" * 64, "analysis": {"primary_topics": [], "secondary_topics": [], "tone": [], "visual_energy": "low"}},
        {},
    )

    assert chosen["basename"] == "t44.jpg"
    assert chosen["image_source"] == "original"
    assert chosen["origin_quote_hash"] is None
    assert chosen["origin_quote_match"] is False
    assert chosen["origin_quote_boost"] == 0.0
    assert "REGULAR_IMAGE_SELECTED source=original basename=t44.jpg" in caplog.text
    assert "origin_quote_match=false origin_quote_boost=0.0" in caplog.text


def test_generated_image_origin_quote_boost_can_select_matching_generated_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    generated_dir = tmp_path / "generated"
    image_dir.mkdir()
    generated_dir.mkdir()
    original = image_dir / "t01.jpg"
    quote_hash = "c" * 64
    generated = generated_dir / f"tg_{quote_hash}.png"
    original.write_bytes(b"original")
    generated.write_bytes(b"generated")
    original_analysis_path = tmp_path / "image_analysis.json"
    generated_analysis_path = tmp_path / "generated_image_analysis.json"
    neutral = {
        "pairing": {},
        "themes": [],
        "tone": [],
        "visual_energy": "low",
        "quality": {},
        "seasonality": {"avoid_outside_season_or_occasion": False},
    }
    write_image_analysis(original_analysis_path, image_analysis_for_paths([original], {"t01.jpg": neutral}))
    write_image_analysis(generated_analysis_path, image_analysis_for_paths([generated], {generated.name: neutral}))

    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "*.png")
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", original_analysis_path)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ANALYSIS_FILE", str(generated_analysis_path))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST", 10)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))

    chosen = bot.choose_matched_unused_image(
        set(),
        {
            "quote_hash": quote_hash,
            "analysis": {"primary_topics": [], "secondary_topics": [], "tone": [], "visual_energy": "low"},
        },
        {},
    )

    assert chosen["basename"] == generated.name
    assert chosen["components"]["generated_origin_quote"] == 10.0
    assert chosen["image_source"] == "generated"
    assert chosen["origin_quote_hash"] == quote_hash
    assert chosen["origin_quote_match"] is True
    assert chosen["origin_quote_boost"] == 10.0


def test_generated_image_cross_quote_selection_gets_no_origin_boost_and_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    generated_dir = tmp_path / "generated"
    image_dir = tmp_path / "images"
    generated_dir.mkdir()
    image_dir.mkdir()
    origin_quote_hash = "a" * 64
    selected_quote_hash = "b" * 64
    generated = generated_dir / f"tg_{origin_quote_hash}.png"
    generated.write_bytes(b"generated")
    original_analysis_path = tmp_path / "image_analysis.json"
    generated_analysis_path = tmp_path / "generated_image_analysis.json"
    write_image_analysis(original_analysis_path, image_analysis_for_paths([]))
    write_image_analysis(generated_analysis_path, image_analysis_for_paths([generated]))

    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "*.png")
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", original_analysis_path)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ANALYSIS_FILE", str(generated_analysis_path))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST", 10)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    caplog.set_level(logging.INFO, logger=bot.log.name)

    chosen = bot.choose_matched_unused_image(
        set(),
        {
            "quote_hash": selected_quote_hash,
            "analysis": {"primary_topics": [], "secondary_topics": [], "tone": [], "visual_energy": "low"},
        },
        {},
    )

    assert chosen["basename"] == generated.name
    assert chosen["image_source"] == "generated"
    assert chosen["origin_quote_hash"] == origin_quote_hash
    assert chosen["origin_quote_match"] is False
    assert chosen["origin_quote_boost"] == 0.0
    assert "generated_origin_quote" not in chosen["components"]
    assert f"REGULAR_IMAGE_SELECTED source=generated basename={generated.name}" in caplog.text
    assert f"origin_quote_hash={origin_quote_hash} origin_quote_match=false origin_quote_boost=0.0" in caplog.text


def test_generated_image_blocked_by_spacing_selects_original_without_marking_generated_used(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    image_dir = tmp_path / "images"
    generated_dir = tmp_path / "generated"
    image_dir.mkdir()
    generated_dir.mkdir()
    original = image_dir / "t01.jpg"
    generated_hash = "a" * 64
    generated = generated_dir / f"tg_{generated_hash}.png"
    original.write_bytes(b"original")
    generated.write_bytes(b"generated")
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Tax quote.\n", encoding="utf-8")
    quote_analysis = {"primary_topics": ["tax"], "secondary_topics": [], "tone": [], "visual_energy": "low"}
    original_analysis = {"pairing": {}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}, "seasonality": {"avoid_outside_season_or_occasion": False}}
    generated_analysis = {"pairing": {"best_for_topics": ["tax"]}, "themes": ["tax"], "tone": [], "visual_energy": "low", "quality": {}, "seasonality": {"avoid_outside_season_or_occasion": False}}
    original_analysis_path = tmp_path / "image_analysis.json"
    generated_analysis_path = tmp_path / "generated_image_analysis.json"
    write_image_analysis(original_analysis_path, image_analysis_for_paths([original], {original.name: original_analysis}))
    write_image_analysis(generated_analysis_path, image_analysis_for_paths([generated], {generated.name: generated_analysis}))

    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "*.png")
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", original_analysis_path)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ANALYSIS_FILE", str(generated_analysis_path))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 2)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(["Tax quote."], {0: quote_analysis}))
    caplog.set_level(logging.INFO, logger=bot.log.name)
    images_used: set[str] = set()
    state = {"original_regular_posts_since_generated_image": 0}

    chosen = bot.choose_regular_quote_image_pair(
        set(),
        images_used,
        state,
    )[1]

    assert chosen["basename"] == "t01.jpg"
    assert chosen["image_source"] == "original"
    assert generated.name not in images_used
    assert "GENERATED_IMAGE_SPACING_STATUS pool_enabled=true allowed=false original_posts_since_generated=0 required=2" in caplog.text
    assert "GENERATED_IMAGE_POOL_BLOCKED_BY_SPACING original_posts_since_generated=0 required=2" in caplog.text


def test_generated_image_spacing_expiry_allows_generated_to_compete_with_origin_boost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    generated_dir = tmp_path / "generated"
    image_dir.mkdir()
    generated_dir.mkdir()
    original = image_dir / "t01.jpg"
    quote = "Origin boost quote."
    quote_hash = bot.quote_text_hash(quote)
    generated = generated_dir / f"tg_{quote_hash}.png"
    original.write_bytes(b"original")
    generated.write_bytes(b"generated")
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text(quote + "\n", encoding="utf-8")
    neutral = {"pairing": {}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}, "seasonality": {"avoid_outside_season_or_occasion": False}}
    original_analysis_path = tmp_path / "image_analysis.json"
    generated_analysis_path = tmp_path / "generated_image_analysis.json"
    write_image_analysis(original_analysis_path, image_analysis_for_paths([original], {original.name: neutral}))
    write_image_analysis(generated_analysis_path, image_analysis_for_paths([generated], {generated.name: neutral}))

    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "*.png")
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", original_analysis_path)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ANALYSIS_FILE", str(generated_analysis_path))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 2)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST", 4)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines([quote]))

    chosen = bot.choose_regular_quote_image_pair(
        set(),
        set(),
        {"original_regular_posts_since_generated_image": 2},
    )[1]

    assert chosen["basename"] == generated.name
    assert chosen["image_source"] == "generated"
    assert chosen["origin_quote_match"] is True
    assert chosen["origin_quote_boost"] == 4.0


def test_generated_origin_boost_preserves_expected_final_score(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated_dir = tmp_path / "generated"
    image_dir = tmp_path / "images"
    generated_dir.mkdir()
    image_dir.mkdir()
    quote_hash = "c" * 64
    generated = generated_dir / f"tg_{quote_hash}.png"
    generated.write_bytes(b"generated")
    analysis_obj = {
        "pairing": {"best_for_topics": ["trade"]},
        "themes": [],
        "tone": [],
        "visual_energy": "low",
        "quality": {},
        "seasonality": {"avoid_outside_season_or_occasion": False},
    }
    quote_analysis = {"primary_topics": ["trade"], "secondary_topics": [], "tone": [], "visual_energy": "low"}
    original_analysis_path = tmp_path / "image_analysis.json"
    generated_analysis_path = tmp_path / "generated_image_analysis.json"
    write_image_analysis(original_analysis_path, image_analysis_for_paths([]))
    write_image_analysis(generated_analysis_path, image_analysis_for_paths([generated], {generated.name: analysis_obj}))

    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "*.png")
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", original_analysis_path)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ANALYSIS_FILE", str(generated_analysis_path))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST", 6)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))

    base_score = bot.score_image_for_quote(quote_analysis, analysis_obj, bot.build_image_topic_idf(bot.load_image_analysis()))[0]
    origin_choice = bot.choose_matched_unused_image(set(), {"quote_hash": quote_hash, "analysis": quote_analysis}, {})
    cross_choice = bot.choose_matched_unused_image(set(), {"quote_hash": "d" * 64, "analysis": quote_analysis}, {})

    assert origin_choice["score"] == pytest.approx(base_score + 6)
    assert origin_choice["origin_quote_boost"] == 6.0
    assert cross_choice["score"] == pytest.approx(base_score)
    assert cross_choice["origin_quote_boost"] == 0.0


def test_generated_image_pool_enabled_does_not_migrate_legacy_image_indices(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    generated_dir = tmp_path / "generated"
    image_dir.mkdir()
    generated_dir.mkdir()
    original = image_dir / "t01.jpg"
    generated = generated_dir / ("tg_" + "d" * 64 + ".png")
    original.write_bytes(b"original")
    generated.write_bytes(b"generated")
    analysis = bot.merge_image_analysis(
        image_analysis_for_paths([original]),
        image_analysis_for_paths([generated]),
    )
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)

    normalised, changed = bot.normalise_image_used_basenames({0}, [str(original), str(generated)], analysis)

    assert normalised == {0}
    assert not changed


def test_highest_scoring_unused_image_is_selected_even_if_global_best_is_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_paths = []
    for name in ["t01.jpg", "t02.jpg"]:
        path = image_dir / name
        path.write_bytes(name.encode("utf-8"))
        image_paths.append(path)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(
        bot,
        "load_image_analysis",
        lambda: image_analysis_for_paths(
            image_paths,
            {
                "t01.jpg": {"pairing": {"best_for_topics": ["rare_topic"]}, "themes": ["rare_topic"], "visual_energy": "low", "quality": {}},
                "t02.jpg": {"pairing": {"best_for_topics": ["government"]}, "themes": [], "visual_energy": "low", "quality": {}},
            },
        ),
    )

    chosen = bot.choose_matched_unused_image(
        {"t01.jpg"},
        {"analysis": {"primary_topics": ["rare_topic", "government"], "secondary_topics": [], "tone": [], "visual_energy": "low"}},
        {},
    )

    assert chosen["basename"] == "t02.jpg"


def test_idf_makes_rare_topic_match_more_valuable_than_ubiquitous_topic() -> None:
    image_analysis = {
        "items": {
            "a": {"analysis": {"pairing": {"best_for_topics": ["leadership", "rare_topic"]}, "themes": []}},
            "b": {"analysis": {"pairing": {"best_for_topics": ["leadership"]}, "themes": []}},
            "c": {"analysis": {"pairing": {"best_for_topics": ["leadership"]}, "themes": []}},
        }
    }
    idf = bot.build_image_topic_idf(image_analysis)
    quote = {"primary_topics": ["rare_topic"], "secondary_topics": [], "tone": [], "visual_energy": "low"}
    rare_image = {"pairing": {"best_for_topics": ["rare_topic"]}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}}
    common_image = {"pairing": {"best_for_topics": ["leadership"]}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}}

    rare_score = bot.score_image_for_quote(quote, rare_image, idf)[0]
    common_score = bot.score_image_for_quote({"primary_topics": ["leadership"], "secondary_topics": [], "tone": [], "visual_energy": "low"}, common_image, idf)[0]

    assert rare_score > common_score


def test_visual_energy_exact_match_beats_opposite_mismatch() -> None:
    quote = {"primary_topics": [], "secondary_topics": [], "tone": [], "visual_energy": "high"}
    exact = {"pairing": {}, "themes": [], "tone": [], "visual_energy": "high", "quality": {}}
    opposite = {"pairing": {}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}}

    assert bot.score_image_for_quote(quote, exact)[0] > bot.score_image_for_quote(quote, opposite)[0]


def test_strong_visual_contradiction_is_ineligible() -> None:
    quote = {
        "primary_topics": [],
        "secondary_topics": [],
        "tone": [],
        "visual_energy": "low",
        "archive_image_preferences": {"strong_visual_mismatches": ["crowd scenes"]},
    }
    image = {"description": "A large crowd scene outside a building.", "pairing": {}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}}

    score, components, eligible = bot.score_image_for_quote(quote, image)

    assert eligible is False
    assert score <= bot.IMAGE_STRONG_MISMATCH_PENALTY
    assert "strong_mismatch" in components


def test_hard_mismatch_requires_both_tokens_for_two_token_phrase() -> None:
    assert not bot.hard_mismatch_phrase_matches_text("armed conflict", "An armed police officer stands nearby.")
    assert bot.hard_mismatch_phrase_matches_text("armed conflict", "An armed conflict scene is shown.")


def test_hard_mismatch_longer_phrase_uses_ceil_coverage() -> None:
    assert not bot.hard_mismatch_phrase_matches_text("large military parade crowd", "Large military display")
    assert bot.hard_mismatch_phrase_matches_text("large military parade crowd", "Large military parade crowd")


def test_phrase_match_requires_both_tokens_for_two_token_phrase() -> None:
    assert not bot.phrase_matches_text(
        "informal portrait",
        "A formal portrait against a conference backdrop.",
    )
    assert not bot.phrase_matches_text(
        "parliamentary setting",
        "A domestic setting with patterned wallpaper.",
    )
    assert bot.phrase_matches_text(
        "informal portrait",
        "A warm informal portrait in a private room.",
    )
    assert bot.phrase_matches_text(
        "parliamentary setting",
        "The photograph shows a parliamentary setting.",
    )


def test_phrase_match_preserves_longer_phrase_partial_coverage() -> None:
    phrase = "formal political conference address"

    assert bot.phrase_matches_text(
        phrase,
        "A formal political address by the party leader.",
    )
    assert not bot.phrase_matches_text(
        phrase,
        "A formal address in a domestic room.",
    )


def test_load_image_used_basenames_empty_scan_preserves_existing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    history = tmp_path / "images_used.json"
    history.write_text(json.dumps(["t01.jpg"]) + "\n", encoding="utf-8")
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", history)
    before = history.read_text(encoding="utf-8")

    assert bot.load_image_used_basenames([]) == {"t01.jpg"}
    assert history.read_text(encoding="utf-8") == before


def test_image_history_preserves_missing_basenames_and_reuses_when_file_reappears(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    t01 = image_dir / "t01.jpg"
    t01.write_bytes(b"one")
    history = tmp_path / "images_used.json"
    history.write_text(json.dumps(["t01.jpg", "temporarily_missing.jpg"]) + "\n", encoding="utf-8")
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", history)

    used = bot.load_image_used_basenames([str(t01)])
    assert used == {"t01.jpg", "temporarily_missing.jpg"}
    missing = image_dir / "temporarily_missing.jpg"
    missing.write_bytes(b"two")
    used = bot.load_image_used_basenames([str(t01), str(missing)])
    assert "temporarily_missing.jpg" in used


def test_partial_visible_image_scan_does_not_migrate_legacy_indices_or_rewrite_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    visible = []
    for name in ["t40.jpg", "t41.jpg", "t42.jpg"]:
        path = image_dir / name
        path.write_bytes(name.encode("utf-8"))
        visible.append(path)
    expected = visible + [image_dir / "t43.jpg"]
    expected[-1].write_bytes(b"hidden")
    analysis = image_analysis_for_paths(expected)
    expected[-1].unlink()
    history = tmp_path / "images_used.json"
    history.write_text("[0, 1, 2]\n", encoding="utf-8")
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", history)
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)
    before = history.read_text(encoding="utf-8")

    used = bot.load_image_used_basenames([str(path) for path in visible])

    assert used == {0, 1, 2}
    assert history.read_text(encoding="utf-8") == before


def test_regular_posting_blocks_unsafe_legacy_image_index_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    visible = image_dir / "t40.jpg"
    hidden = image_dir / "t41.jpg"
    visible.write_bytes(b"visible")
    hidden.write_bytes(b"hidden")
    analysis = image_analysis_for_paths([visible, hidden])
    hidden.unlink()
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)
    used = {0}

    with pytest.raises(bot.UnsafeImageHistoryMigration):
        bot.choose_matched_unused_image(used, {"analysis": {"primary_topics": []}}, {})

    assert used == {0}


def test_unsafe_first_quote_index_migration_is_preserved_and_blocks_posting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = ["First quote.", "Second quote."]
    reordered = ["Inserted quote.", "First quote.", "Second quote."]
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("\n".join(reordered) + "\n", encoding="utf-8")
    history = tmp_path / "lines_used.json"
    history.write_text("[1]\n", encoding="utf-8")
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"fake")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "LINES_USED_FILE", history)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(original))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: image_analysis_for_paths([image_path]))

    used = bot.load_quote_used_hashes([line + "\n" for line in reordered])

    assert used == {1}
    assert history.read_text(encoding="utf-8") == "[1]\n"
    with pytest.raises(bot.CorruptUsedHistoryError):
        bot.post_random_quote(used, set(), {})


def test_corrupt_current_used_history_does_not_reduce_to_stale_pickle(tmp_path: Path) -> None:
    json_path = tmp_path / "lines_used.json"
    pickle_path = tmp_path / "lines_used.pickle"
    json_path.write_text("{bad", encoding="utf-8")
    pickle_path.write_bytes(b"legacy pickle no longer trusted")

    with pytest.raises(bot.CorruptUsedHistoryError):
        bot.load_used_set(json_path, legacy_pickle_path=pickle_path)


def test_quote_hash_candidates_deduplicate_identical_source_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines = ["Duplicate quote.", "Another quote.", "Duplicate quote."]
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(lines))

    candidates = bot.quote_candidates_for_current_cycle(set())

    assert [candidate["text"] for candidate in candidates].count("Duplicate quote.") == 1


def test_quote_cycle_resets_when_only_research_ineligible_source_records_remain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = ["Eligible first.", "Unresolved retained source.", "Eligible second."]
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    eligible_hashes = {
        bot.quote_text_hash(lines[0]),
        bot.quote_text_hash(lines[2]),
    }
    used = set(eligible_hashes)
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(lines))
    monkeypatch.setattr(bot, "completed_research_quote_hashes", lambda: eligible_hashes)

    candidates = bot.quote_candidates_for_current_cycle(used)

    assert used == set()
    assert {candidate["quote_hash"] for candidate in candidates} == eligible_hashes


def test_posting_duplicate_quote_marks_hash_and_blocks_identical_line_same_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"fake")
    lines = ["Duplicate quote.", "Another quote.", "Duplicate quote."]
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "LINES_USED_FILE", tmp_path / "lines_used.json")
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", tmp_path / "images_used.json")
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", tmp_path / "regular_post_receipt.json")
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(lines))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: image_analysis_for_paths([image_path]))
    monkeypatch.setattr(bot.random, "uniform", lambda low, high: low)
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
            kwargs, {"data": {"id": "950001"}}
        ),
    )
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(bot, "maybe_schedule_meme_after_quote_post", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)

    lines_used: set[str] = set()
    bot.post_random_quote(lines_used, set(), {})

    duplicate_hash = bot.quote_text_hash("Duplicate quote.")
    assert lines_used == {duplicate_hash}
    candidates = bot.quote_candidates_for_current_cycle(lines_used)
    assert [candidate["text"] for candidate in candidates] == ["Another quote."]


def test_quote_hash_history_migrates_legacy_lines_and_survives_reordering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_lines = ["First quote.", "Second quote."]
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("\n".join(original_lines) + "\n", encoding="utf-8")
    history = tmp_path / "lines_used.json"
    history.write_text("[1]\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "LINES_USED_FILE", history)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(original_lines))

    used = bot.load_quote_used_hashes([line + "\n" for line in original_lines])
    second_hash = bot.quote_text_hash("Second quote.")
    assert used == {second_hash}

    reordered = ["Second quote.", "First quote.", "Brand new quote."]
    lines_file.write_text("\n".join(reordered) + "\n", encoding="utf-8")
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(reordered))
    candidates = bot.quote_candidates_for_current_cycle(used)
    assert "Second quote." not in [candidate["text"] for candidate in candidates]
    assert "Brand new quote." in [candidate["text"] for candidate in candidates]


def test_image_metadata_is_used_only_when_current_content_matches(tmp_path: Path) -> None:
    image_path = tmp_path / "t01.jpg"
    image_path.write_bytes(b"original")
    analysis = image_analysis_for_paths([image_path], {"t01.jpg": {"description": "Original metadata"}})

    _, metadata = bot.image_metadata_for_basename(analysis, "t01.jpg", str(image_path))
    assert metadata == {"description": "Original metadata"}

    image_path.write_bytes(b"changed")
    with pytest.raises(bot.StaleImageMetadata):
        bot.image_metadata_for_basename(analysis, "t01.jpg", str(image_path))


def test_image_hash_cache_detects_changed_bytes_with_preserved_size_and_mtime(tmp_path: Path) -> None:
    image_path = tmp_path / "t01.jpg"
    image_path.write_bytes(b"abcdef")
    first = bot.current_image_sha256(str(image_path))
    stat = image_path.stat()
    image_path.write_bytes(b"ghijkl")
    os.utime(image_path, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    second = bot.current_image_sha256(str(image_path))

    assert second != first


def test_stale_image_is_skipped_while_valid_image_remains_selectable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    stale_path = image_dir / "t01.jpg"
    valid_path = image_dir / "t02.jpg"
    stale_path.write_bytes(b"original")
    valid_path.write_bytes(b"valid")
    analysis = image_analysis_for_paths([stale_path, valid_path])
    stale_path.write_bytes(b"changed")
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)

    chosen = bot.choose_matched_unused_image(set(), {"analysis": {"primary_topics": ["anything"]}}, {})

    assert chosen["basename"] == "t02.jpg"


def test_new_unanalysed_image_is_excluded_while_valid_image_remains_selectable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    used_path = image_dir / "t01.jpg"
    valid_path = image_dir / "t02.jpg"
    new_path = image_dir / "t03.jpg"
    used_path.write_bytes(b"used")
    valid_path.write_bytes(b"valid")
    analysis = image_analysis_for_paths([used_path, valid_path])
    new_path.write_bytes(b"new")
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)

    chosen = bot.choose_matched_unused_image({"t01.jpg"}, {"analysis": {"primary_topics": ["anything"]}}, {})

    assert chosen["basename"] == "t02.jpg"


def test_all_stale_images_fail_without_mutating_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"original")
    analysis = image_analysis_for_paths([image_path])
    image_path.write_bytes(b"changed")
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)
    used: set[str] = set()

    with pytest.raises(bot.NoEligibleImageForQuote):
        bot.choose_matched_unused_image(used, {"analysis": {"primary_topics": ["anything"]}}, {})

    assert used == set()


def test_missing_whole_quote_analysis_fails_regular_quote_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Christmas quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: None)

    with pytest.raises(RuntimeError, match="Quote analysis unavailable"):
        bot.choose_unused_line_candidate(set())


def test_unanalysed_current_christmas_quote_is_skipped_in_july(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines = ["Ordinary quote.", "Christmas quote edited."]
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    analysed = quote_analysis_for_lines(["Ordinary quote.", "Old Christmas quote."])
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: analysed)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))

    candidates = bot.quote_candidates_for_current_cycle(set())

    assert [candidate["text"] for candidate in candidates] == ["Ordinary quote."]


def test_missing_image_analysis_fails_regular_image_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"fake")
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: None)

    with pytest.raises(bot.NoEligibleImageForQuote, match="Image analysis unavailable"):
        bot.choose_matched_unused_image(set(), {"analysis": {"primary_topics": ["government"]}}, {})


def test_missing_per_image_analysis_is_excluded_with_valid_alternative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    missing_path = image_dir / "t01.jpg"
    valid_path = image_dir / "t02.jpg"
    missing_path.write_bytes(b"missing")
    valid_path.write_bytes(b"valid")
    analysis = image_analysis_for_paths([missing_path, valid_path])
    missing_hash = bot.file_sha256(missing_path)
    analysis["items"].pop(missing_hash)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)

    chosen = bot.choose_matched_unused_image(set(), {"analysis": {"primary_topics": ["anything"]}}, {})

    assert chosen["basename"] == "t02.jpg"


def test_image_hash_failure_excludes_image_with_valid_alternative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    bad_path = image_dir / "t01.jpg"
    good_path = image_dir / "t02.jpg"
    bad_path.write_bytes(b"bad")
    good_path.write_bytes(b"good")
    analysis = image_analysis_for_paths([bad_path, good_path])
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)
    real_hash = bot.current_image_sha256

    def maybe_fail(path: str) -> str:
        if path.endswith("t01.jpg"):
            raise OSError("read failed")
        return real_hash(path)

    monkeypatch.setattr(bot, "current_image_sha256", maybe_fail)

    chosen = bot.choose_matched_unused_image(set(), {"analysis": {"primary_topics": ["anything"]}}, {})

    assert chosen["basename"] == "t02.jpg"


def test_all_image_hash_failures_raise_global_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"bad")
    analysis = image_analysis_for_paths([image_path])
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)
    monkeypatch.setattr(bot, "current_image_sha256", lambda path, **_kwargs: (_ for _ in ()).throw(OSError("read failed")))

    with pytest.raises(bot.GlobalImageUnavailable):
        bot.choose_matched_unused_image(set(), {"analysis": {"primary_topics": ["anything"]}}, {})


def test_invalid_per_image_analysis_type_is_excluded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"invalid")
    analysis = image_analysis_for_paths([image_path])
    image_hash = bot.file_sha256(image_path)
    analysis["items"][image_hash]["analysis"] = "bad"
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)

    with pytest.raises(bot.GlobalImageUnavailable):
        bot.choose_matched_unused_image(set(), {"analysis": {"primary_topics": ["anything"]}}, {})


def test_global_image_failure_is_not_retried_across_quotes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_quote(lines_used: set, *, excluded_quote_hashes: set[str] | None = None, allow_cycle_reset: bool = True) -> dict:
        assert allow_cycle_reset is True
        nonlocal calls
        calls += 1
        return {"line_no": calls - 1, "quote_hash": f"{calls:064x}", "text": f"Quote {calls}.", "analysis": {}}

    monkeypatch.setattr(bot, "reconcile_main_post_receipts", lambda *args, **kwargs: {"regular": False, "meme": False})
    monkeypatch.setattr(bot, "quote_used_history_has_legacy_indices", lambda used: False)
    monkeypatch.setattr(bot, "choose_unused_line_candidate", fake_quote)
    monkeypatch.setattr(bot, "choose_matched_unused_image", lambda *args, **kwargs: (_ for _ in ()).throw(bot.GlobalImageUnavailable("no corpus")))

    with pytest.raises(bot.GlobalImageUnavailable):
        bot.post_random_quote(set(), set(), {})

    assert calls == 1


def test_image_cycle_status_log_formats_without_argument_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"valid")
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: image_analysis_for_paths([image_path]))

    bot.choose_matched_unused_image(set(), {"analysis": {"primary_topics": ["anything"]}}, {})

    assert "Image cycle status:" in caplog.text
