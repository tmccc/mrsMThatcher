from __future__ import annotations

import json
import hashlib
import os
import tempfile
from datetime import datetime
from pathlib import Path

import pytest


UNIT_BASE = Path(tempfile.gettempdir()) / "mrsMThatcher-unit-import"
UNIT_BASE.mkdir(parents=True, exist_ok=True)

IMPORT_ENV = {
    "MRS_TEST_MODE": "1",
    "MRS_BASE_DIR": str(UNIT_BASE),
    "MRS_LOG_FILE": str(UNIT_BASE / "unit-test.log"),
    "X_API_BASE_URL": "http://127.0.0.1:9",
    "X_UPLOAD_BASE_URL": "http://127.0.0.1:9",
    "XAI_API_BASE_URL": "http://127.0.0.1:9/v1",
    "X_CONSUMER_KEY": "dummy",
    "X_CONSUMER_SECRET": "dummy",
    "X_ACCESS_TOKEN": "dummy",
    "X_ACCESS_SECRET": "dummy",
    "X_MY_USER_ID": "12345",
    "XAI_API_KEY": "dummy",
    "X_BEARER_TOKEN": "dummy",
}
ORIGINAL_ENV = {key: os.environ.get(key) for key in IMPORT_ENV}
os.environ.update(IMPORT_ENV)

import mrsMThatcher2 as bot  # noqa: E402

for key, value in ORIGINAL_ENV.items():
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value


@pytest.fixture(autouse=True)
def isolate_regular_post_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", tmp_path / "regular_post_receipt.json")
    monkeypatch.setattr(bot, "MEME_POST_RECEIPT_FILE", tmp_path / "meme_post_receipt.json")


def quote_analysis_for_lines(lines: list[str], analyses: dict[int, dict] | None = None) -> dict:
    analyses = analyses or {}
    items: dict[str, dict] = {}
    line_index: dict[str, str] = {}
    for idx, text in enumerate(lines):
        if not text.strip():
            continue
        quote_hash = bot.quote_text_hash(text)
        line_index[str(idx + 1)] = quote_hash
        items.setdefault(
            quote_hash,
            {
                "text": bot.collapse_quote_whitespace(text),
                "quote_hash": quote_hash,
                "line_numbers": [idx + 1],
                "analysis": analyses.get(idx, {"seasonality": {"hard_exclude_outside_windows": False, "preferred_windows": [], "relevance": "none"}}),
            },
        )
    return {
        "analysis_kind": "quotes",
        "schema_version": 2,
        "source": {"source_sha256": hashlib.sha256(("".join(line + "\n" for line in lines)).encode("utf-8")).hexdigest()},
        "line_index": line_index,
        "items": items,
    }


def image_analysis_for_paths(paths: list[Path], analyses: dict[str, dict] | None = None) -> dict:
    analyses = analyses or {}
    path_index: dict[str, str] = {}
    items: dict[str, dict] = {}
    for path in paths:
        image_hash = bot.file_sha256(path)
        path_index[path.name] = image_hash
        items[image_hash] = {
            "paths": [path.name],
            "image_hash": image_hash,
            "analysis": analyses.get(path.name, {"pairing": {}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}, "seasonality": {"avoid_outside_season_or_occasion": False}}),
        }
    return {"analysis_kind": "images", "schema_version": 3, "path_index": path_index, "items": items}


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


def test_choose_unused_line_returns_non_empty_line_and_marks_empty_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines_file = tmp_path / "mrsMThatcher.txt"
    lines_file.write_text("\nA usable quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(["", "A usable quote."]))
    monkeypatch.setattr(bot.random, "shuffle", lambda values: values.sort())

    used: set[str] = set()
    line_no, text = bot.choose_unused_line(used)

    assert (line_no, text) == (1, "A usable quote.")
    assert used == set()


def test_choose_unused_line_resets_when_all_lines_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines_file = tmp_path / "mrsMThatcher.txt"
    lines_file.write_text("Only quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    only_hash = bot.quote_text_hash("Only quote.")
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(["Only quote."]))

    used = {only_hash}
    line_no, text = bot.choose_unused_line(used)

    assert (line_no, text) == (0, "Only quote.")
    assert used == set()


def test_choose_unused_image_resets_when_all_images_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"fake")
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))

    used = {"t01.jpg"}
    image_no, selected = bot.choose_unused_image(used)

    assert image_no == 0
    assert selected == str(image_path)
    assert used == set()


def test_quote_metadata_uses_current_quote_hash_not_stale_line_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Quote text\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    quote_hash = bot.quote_text_hash("Quote text")
    analysis = {"primary_topics": ["government"]}
    data = {"line_index": {"1": "stale"}, "items": {quote_hash: {"text": "Quote text", "analysis": analysis}}}

    assert bot.quote_metadata_for_line(data, 0) == (quote_hash, analysis)


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


def test_image_cycle_remaining_set_prevents_repeat_until_unused_exhausted(tmp_path: Path) -> None:
    images = [str(tmp_path / "t01.jpg"), str(tmp_path / "t02.jpg")]

    available, reset = bot.available_image_basenames(images, {"t01.jpg"}, {})

    assert available == ["t02.jpg"]
    assert reset is False


def test_image_cycle_resets_only_after_all_images_used_and_avoids_boundary_duplicate(tmp_path: Path) -> None:
    images = [str(tmp_path / "t01.jpg"), str(tmp_path / "t02.jpg")]
    used = {"t01.jpg", "t02.jpg"}

    available, reset = bot.available_image_basenames(images, used, {"last_regular_image_filename": "t02.jpg"})

    assert reset is True
    assert used == set()
    assert available == ["t01.jpg"]


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


def test_post_random_quote_retries_alternate_quote_when_first_has_no_image_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: {"data": {"id": "950001"}})
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(bot, "maybe_schedule_meme_after_quote_post", lambda state, quote_post_epoch=None, **kwargs: None)
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        bot,
        "load_quote_analysis",
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
    monkeypatch.setattr(
        bot,
        "load_image_analysis",
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
    monkeypatch.setattr(bot, "upload_media", lambda path: pytest.fail("upload_media should not be called"))
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: pytest.fail("create_post should not be called"))
    monkeypatch.setattr(
        bot,
        "load_quote_analysis",
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
    monkeypatch.setattr(
        bot,
        "load_image_analysis",
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
    monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: {"data": {}})
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


def configure_simple_quote_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    create_response: dict | None = None,
) -> tuple[set[str], set[str], dict, Path, Path, Path, Path]:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"fake")
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    lines_used_file = tmp_path / "lines_used.json"
    images_used_file = tmp_path / "images_used.json"
    receipt_file = tmp_path / "regular_post_receipt.json"
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "LINES_USED_FILE", lines_used_file)
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", images_used_file)
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", receipt_file)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: create_response or {"data": {"id": "950001"}})
    monkeypatch.setattr(bot, "maybe_schedule_meme_after_quote_post", lambda state, quote_post_epoch=None, **kwargs: None)
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(["Good quote."]))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: image_analysis_for_paths([image_path]))
    return set(), set(), {}, lines_used_file, images_used_file, receipt_file, lines_file


def valid_regular_receipt(**overrides: object) -> dict:
    receipt = {
        "schema_version": 1,
        "post_id": "950001",
        "quote_hash": bot.quote_text_hash("Good quote."),
        "image_basename": "t01.jpg",
        "quote_post_epoch": 1_800_000_000,
        "next_quote_post_epoch": 1_800_007_200,
        "text": "Good quote.",
    }
    receipt.update(overrides)
    return receipt


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

    with pytest.raises(RuntimeError, match="failed local recovery receipt"):
        bot.post_random_quote(lines_used, images_used, state)

    assert quote_hash in lines_used
    assert "t01.jpg" in images_used
    assert state["next_quote_post_epoch"] > state["last_quote_post_epoch"]


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
    monkeypatch.setattr(bot, "write_regular_post_receipt", lambda receipt: (_ for _ in ()).throw(OSError("receipt failed")))

    def maybe_fail(name: str) -> None:
        attempts.append(name)
        if name in failed_components:
            raise OSError(f"{name} failed")

    monkeypatch.setattr(bot, "save_quote_used_hashes", lambda *args, **kwargs: maybe_fail("quote_history"))
    monkeypatch.setattr(bot, "save_image_used_basenames", lambda *args, **kwargs: maybe_fail("image_history"))
    monkeypatch.setattr(bot, "save_state", lambda *args, **kwargs: maybe_fail("state"))

    with pytest.raises(RuntimeError) as excinfo:
        bot.post_random_quote(lines_used, images_used, state)

    assert attempts == ["quote_history", "image_history", "state"]
    for name in failed_components:
        assert name in str(excinfo.value)
    assert state["next_quote_post_epoch"] > state["last_quote_post_epoch"]


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


def test_regular_post_does_not_call_mutating_schedule_helpers_after_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    monkeypatch.setattr(bot, "schedule_next_quote_post", lambda *args, **kwargs: pytest.fail("schedule_next_quote_post should not be called"))
    monkeypatch.setattr(bot, "maybe_schedule_meme_after_quote_post", lambda *args, **kwargs: pytest.fail("maybe_schedule_meme_after_quote_post should not be called"))

    bot.post_random_quote(lines_used, images_used, state)

    assert state["next_quote_post_epoch"] > state["last_quote_post_epoch"]


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
    monkeypatch.setattr(bot, "remove_regular_post_receipt", lambda: calls.append("remove"))

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

    def capture_receipt(receipt: dict) -> None:
        assert bot.regular_post_receipt_is_semantically_valid(receipt)
        receipts.append(json.loads(json.dumps(receipt)))

    monkeypatch.setattr(bot, "write_regular_post_receipt", capture_receipt)

    bot.post_random_quote(lines_used, images_used, state)

    assert receipts
    assert receipts[0]["next_meme_schedule_mode"] == "delayed_recent_quote"
    assert receipts[0]["meme_anchor_quote_post_epoch"] == 0
    assert receipts[0]["meme_schedule_changed_by_quote"] is False


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

    assert bot.reconcile_regular_post_receipt(lines_used, images_used, state) is True
    assert bot.reconcile_regular_post_receipt(lines_used, images_used, state) is False
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

    for bad_schema in [{}, {"schema_version": 2}, {"schema_version": "1"}]:
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
    assert "temporarily_missing.jpg" not in bot.available_image_basenames([str(t01)], used, {})[0]

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
    monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: {"data": {"id": "950001"}})
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
    monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: {"data": {"id": "970001"}})
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: saved_states.append(json.loads(json.dumps(state))))
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
    monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: {"data": {"id": "970001"}})
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, helper, lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(f"{helper} failed")))
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_next_meme(state)

    assert bot.MEME_POST_RECEIPT_FILE.exists()
    assert json.loads(bot.MEME_POST_RECEIPT_FILE.read_text(encoding="utf-8"))["post_id"] == "970001"


def test_regular_post_commits_future_quote_schedule_and_receipt_epoch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    receipts: list[dict] = []
    monkeypatch.setattr(bot.random, "randint", lambda low, high: 7200)
    monkeypatch.setattr(bot, "write_regular_post_receipt", lambda receipt: receipts.append(json.loads(json.dumps(receipt))))

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
        remote_confirmed["value"] = True
        return {"data": {"id": "950001"}}

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
    monkeypatch.setattr(
        bot,
        "meme_schedule_fields_after_quote_post",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("schedule finalisation failed")),
    )

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    assert quote_hash in lines_used
    assert "t01.jpg" in images_used
    assert state["last_main_post_id"] == "950001"
    assert not receipt_file.exists()
    assert state["next_quote_post_epoch"] > state["last_quote_post_epoch"]


def test_regular_quote_schedule_failure_after_confirmation_suppresses_quote_lane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    quote_hash = bot.quote_text_hash("Good quote.")
    monkeypatch.setattr(bot.random, "randint", lambda low, high: 7200)
    monkeypatch.setattr(
        bot,
        "next_quote_schedule_fields",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("quote schedule failed")),
    )

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    assert quote_hash in lines_used
    assert "t01.jpg" in images_used
    assert state["last_main_post_id"] == "950001"
    assert state["last_quote_post_epoch"] == 1_800_000_000
    assert state["next_quote_post_epoch"] == 1_800_007_200
    assert state["next_quote_post_epoch"] > state["last_quote_post_epoch"]
    assert not receipt_file.exists()


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
    monkeypatch.setattr(bot.random, "randint", lambda low, high: 7200)
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
    monkeypatch.setattr(bot.random, "randint", lambda low, high: 7200)
    monkeypatch.setattr(bot, "remove_regular_post_receipt", lambda: (_ for _ in ()).throw(OSError("remove failed")))

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    assert state["next_quote_post_epoch"] == 1_800_007_200
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
    "failure",
    ["global_image", "unsafe_image_migration", "unexpected_image", "upload", "create", "invalid_post_id"],
)
def test_pre_confirmation_failures_restore_histories_after_quote_cycle_reset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    lines_used = {"old-line"}
    images_used = {"old-image"}
    state: dict = {}
    monkeypatch.setattr(bot, "reconcile_main_post_receipts", lambda *args, **kwargs: {"regular": False, "meme": False})
    monkeypatch.setattr(bot, "quote_used_history_has_legacy_indices", lambda used: False)

    def reset_then_quote(used: set, *, excluded_quote_hashes: set[str] | None = None) -> dict:
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
            monkeypatch.setattr(bot, "upload_media", lambda path: (_ for _ in ()).throw(OSError("upload failed")))
        else:
            monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
            if failure == "create":
                monkeypatch.setattr(bot, "create_post", lambda **kwargs: (_ for _ in ()).throw(OSError("create failed")))
            elif failure == "invalid_post_id":
                monkeypatch.setattr(bot, "create_post", lambda **kwargs: {"data": {"id": "banana"}})

    with pytest.raises(Exception):
        bot.post_random_quote(lines_used, images_used, state)

    assert lines_used == {"old-line"}
    assert images_used == {"old-image"}
    assert "last_main_post_id" not in state



def test_daily_meme_missing_post_id_fails_without_success_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    events: list[str] = []
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: {"data": {}})
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: pytest.fail("save_state should not be called"))
    monkeypatch.setattr(bot, "log_event", lambda event, **kwargs: events.append(event))

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(RuntimeError, match="valid post id"):
        bot.post_next_meme(state)

    assert state == {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    assert events == []


def test_meme_post_uses_confirmed_time_across_midnight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)
    pre_confirm_epoch = int(datetime(2026, 7, 6, 23, 59, 50).timestamp())
    confirmed_epoch = int(datetime(2026, 7, 7, 0, 0, 5).timestamp())
    remote_confirmed = {"value": False}

    def fake_create_post(**kwargs: object) -> dict:
        remote_confirmed["value"] = True
        return {"data": {"id": "970001"}}

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


def test_meme_schedule_finalisation_failure_after_confirmation_is_confirmed_local_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: {"data": {"id": "970001"}})
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(
        bot,
        "next_meme_schedule_fields",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("schedule finalisation failed")),
    )
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_next_meme(state)

    assert state["last_main_post_id"] == "970001"
    assert state["last_meme_post_epoch"] == 1_800_000_000
    assert state["posted_meme_filenames"] == ["001_meme.png"]
    assert state["next_meme_post_epoch"] > state["last_meme_post_epoch"]
    assert state["meme_anchor_quote_post_epoch"] == 0
    assert not bot.MEME_POST_RECEIPT_FILE.exists()


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
    monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: {"data": {"id": "970001"}})
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: (_ for _ in ()).throw(OSError("state failed")))
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_next_meme(state)

    assert receipt_file.exists()
    receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert receipt["next_meme_post_epoch"] > 1_800_000_000
    assert state["next_meme_post_epoch"] > state["last_meme_post_epoch"]

    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    recovered: dict = {}
    assert bot.reconcile_meme_post_receipt(recovered) is True
    assert bot.reconcile_meme_post_receipt(recovered) is False
    assert recovered["last_main_post_id"] == "970001"
    assert recovered["posted_meme_filenames"] == ["001_meme.png"]
    assert recovered["next_meme_post_epoch"] == receipt["next_meme_post_epoch"]


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
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: {"data": {"id": "970001"}})
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "write_meme_post_receipt", lambda receipt: (_ for _ in ()).throw(OSError("receipt failed")))
    saved_states: list[dict] = []
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: saved_states.append(json.loads(json.dumps(state))))
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_next_meme(state)

    assert state["posted_meme_filenames"] == ["001_meme.png"]
    assert state["next_meme_post_epoch"] > 1_800_000_000
    assert state["next_meme_post_epoch"] > state["last_meme_post_epoch"]
    assert saved_states[-1]["next_meme_post_epoch"] == state["next_meme_post_epoch"]
    assert state["next_meme_schedule_mode"] == "fallback"


def test_meme_receipt_removal_failure_keeps_future_meme_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: {"data": {"id": "970001"}})
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(bot, "remove_meme_post_receipt", lambda: (_ for _ in ()).throw(OSError("remove failed")))
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_next_meme(state)

    assert state["next_meme_post_epoch"] > state["last_meme_post_epoch"]
    assert state["meme_anchor_quote_post_epoch"] == 0


def test_test_post_quote_reports_confirmed_local_failure_distinctly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_test_post_quote_migrates_old_meme_schedule_before_post_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: (_ for _ in ()).throw(RuntimeError("future meme schedule version")))
    monkeypatch.setattr(bot, "post_random_quote", lambda *args, **kwargs: pytest.fail("post_random_quote should not be called"))

    with pytest.raises(RuntimeError, match="future meme schedule version"):
        bot.run_test_post_quote()


def test_test_post_quote_receipt_replay_does_not_create_second_post(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_test_post_meme_migrates_old_meme_schedule_before_post_path(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_load_state_rejects_absurd_epoch_primary_and_recovers_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 2)
    bot.atomic_write_json(
        state_file,
        {
            "next_meme_post_epoch": 10**30,
            "next_meme_schedule_mode": "fallback",
            "next_meme_schedule_date": "9999-01-01",
            "meme_anchor_quote_post_epoch": 0,
        },
    )
    bot.atomic_write_json(
        tmp_path / "bot_state.json.bak1",
        {
            "replied_to_ids": ["from-bak1"],
            "next_quote_post_epoch": 1_800_000_000,
        },
    )

    recovered = bot.load_state()

    assert recovered["replied_to_ids"] == ["from-bak1"]
    assert recovered["next_quote_post_epoch"] == 1_800_000_000


def test_load_state_rejects_malformed_tweet_cache_epoch_and_recovers_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 2)
    bot.atomic_write_json(
        state_file,
        {"tweet_cache": {"123": {"cached_epoch": "banana"}}},
    )
    bot.atomic_write_json(
        tmp_path / "bot_state.json.bak1",
        {"replied_to_ids": ["from-bak1"], "tweet_cache": {}},
    )

    recovered = bot.load_state()

    assert recovered["replied_to_ids"] == ["from-bak1"]
    assert recovered["tweet_cache"] == {}


@pytest.mark.parametrize(
    "cache_entry",
    [
        {"referenced_tweets": "banana"},
        {"referenced_tweets": ["banana"]},
    ],
)
def test_load_state_rejects_malformed_tweet_cache_references(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cache_entry: dict,
) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    bot.atomic_write_json(state_file, {"tweet_cache": {"123": cache_entry}})
    bot.atomic_write_json(tmp_path / "bot_state.json.bak1", {"tweet_cache": {"456": {"cached_epoch": 1_800_000_000}}})

    recovered = bot.load_state()

    assert "123" not in recovered["tweet_cache"]
    assert recovered["tweet_cache"]["456"]["cached_epoch"] == 1_800_000_000


def test_tweet_cache_normalisation_stringifies_scalars_and_preserves_valid_context(tmp_path: Path) -> None:
    state = {
        "tweet_cache": {
            123: {
                "id": 123,
                "author_id": 456,
                "conversation_id": 789,
                "text": 12345,
                "image_summary": 67890,
                "created_at": 111,
                "post_type": 222,
                "cached_epoch": "1800000000",
                "referenced_tweets": [{"type": 333, "id": 444, "extra": 555}],
            }
        }
    }

    normalised = bot.normalise_state_candidate(state, path=tmp_path / "bot_state.json")

    assert normalised is not None
    entry = normalised["tweet_cache"]["123"]
    assert entry["id"] == "123"
    assert entry["author_id"] == "456"
    assert entry["conversation_id"] == "789"
    assert entry["text"] == "12345"
    assert entry["image_summary"] == "67890"
    assert entry["created_at"] == "111"
    assert entry["post_type"] == "222"
    assert entry["cached_epoch"] == 1_800_000_000
    assert entry["referenced_tweets"] == [{"type": "333", "id": "444", "extra": "555"}]


def test_valid_tweet_cache_round_trips_through_state_loading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    bot.atomic_write_json(
        state_file,
        {
            "tweet_cache": {
                "123": {
                    "id": "123",
                    "author_id": "456",
                    "conversation_id": "789",
                    "text": "Useful cached context",
                    "image_summary": "A useful image summary",
                    "created_at": "2026-07-06T10:00:00",
                    "post_type": "quote",
                    "cached_epoch": 1_800_000_000,
                    "referenced_tweets": [{"type": "replied_to", "id": "111"}],
                }
            }
        },
    )

    recovered = bot.load_state()

    assert recovered["tweet_cache"]["123"] == {
        "id": "123",
        "author_id": "456",
        "conversation_id": "789",
        "text": "Useful cached context",
        "image_summary": "A useful image summary",
        "created_at": "2026-07-06T10:00:00",
        "post_type": "quote",
        "cached_epoch": 1_800_000_000,
        "referenced_tweets": [{"type": "replied_to", "id": "111"}],
    }


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


@pytest.mark.parametrize("post_id", ["123456", 123456])
def test_create_post_accepts_valid_numeric_ids(monkeypatch: pytest.MonkeyPatch, post_id: object) -> None:
    monkeypatch.setattr(bot, "x_request", lambda *args, **kwargs: {"data": {"id": post_id}})

    assert bot.create_post("hello") == {"data": {"id": post_id}}


@pytest.mark.parametrize("response", [{"data": {"id": "banana"}}, {"data": {"id": ""}}, {"data": {}}, {}])
def test_create_post_rejects_invalid_or_missing_ids(monkeypatch: pytest.MonkeyPatch, response: dict) -> None:
    monkeypatch.setattr(bot, "x_request", lambda *args, **kwargs: response)

    with pytest.raises(bot.ApiError):
        bot.create_post("hello")


def test_malformed_reply_post_id_is_not_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    state = bot.default_state()
    state["last_reply_epoch"] = 0

    mention = {
        "id": "100",
        "author_id": "200",
        "text": "@MrsMThatcher hello",
        "conversation_id": "100",
        "referenced_tweets": [],
    }

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "DRY_RUN_REPLIES", False)
    monkeypatch.setattr(bot, "MARK_AI_REPLIES_AS_AI", False)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "get_mentions", lambda state: [mention])
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda state: [])
    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", lambda text: False)
    monkeypatch.setattr(bot, "build_context_for_grok", lambda mention, state: ("context", True))
    monkeypatch.setattr(bot, "ask_grok_for_reply", lambda context: "A reply.")
    monkeypatch.setattr(bot, "x_request", lambda *args, **kwargs: {"data": {"id": "banana"}})
    monkeypatch.setattr(bot, "save_state", lambda state: None)

    status = bot.maybe_reply_to_mentions(state)

    assert status == bot.NORMAL_CHECK_STATUS_API_ERROR
    assert state["daily_reply_count"] == 0
    assert state["replied_to_ids"] == []
    assert state["own_auto_reply_ids"] == []
    assert state["tweet_cache"] == {}


def test_cache_tweet_uses_fake_clock_for_generated_created_at(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed_epoch = 1_800_000_000
    state = bot.default_state()
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)

    cached = bot.cache_tweet(
        state,
        tweet_id="123",
        text="hello",
        author_id="456",
    )

    assert cached["cached_epoch"] == fixed_epoch
    assert cached["created_at"] == datetime.fromtimestamp(fixed_epoch).isoformat()


def test_cache_tweet_normalises_safe_scalar_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed_epoch = 1_800_000_000
    state = bot.default_state()
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)

    cached = bot.cache_tweet(
        state,
        tweet_id=123,
        text=["not", "a", "string"],
        author_id=456,
        conversation_id=789,
        referenced_tweets=[{"type": 1, "id": 2}],
        created_at=111,
        image_summary=222,
        post_type=333,
    )

    assert cached == {
        "id": "123",
        "author_id": "456",
        "conversation_id": "789",
        "created_at": "111",
        "referenced_tweets": [{"type": "1", "id": "2"}],
        "text": "['not', 'a', 'string']",
        "cached_epoch": fixed_epoch,
        "image_summary": "222",
        "post_type": "333",
    }


@pytest.mark.parametrize("referenced_tweets", ["banana", ["banana"]])
def test_cache_tweet_rejects_malformed_referenced_tweets(referenced_tweets: object) -> None:
    state = bot.default_state()

    with pytest.raises(ValueError):
        bot.cache_tweet(
            state,
            tweet_id="123",
            text="hello",
            author_id="456",
            referenced_tweets=referenced_tweets,
        )

    assert state["tweet_cache"] == {}


def test_load_state_rejects_malformed_last_seen_mention_id_and_recovers_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    bot.atomic_write_json(state_file, {"last_seen_mention_id": []})
    bot.atomic_write_json(tmp_path / "bot_state.json.bak1", {"last_seen_mention_id": "123"})

    recovered = bot.load_state()

    assert recovered["last_seen_mention_id"] == "123"


def test_load_state_normalises_optional_scalar_ids(tmp_path: Path) -> None:
    state = {
        "last_seen_mention_id": 123,
        "last_main_post_id": 456,
        "last_regular_image_filename": 789,
    }

    normalised = bot.normalise_state_candidate(state, path=tmp_path / "bot_state.json")

    assert normalised is not None
    assert normalised["last_seen_mention_id"] == "123"
    assert normalised["last_main_post_id"] == "456"
    assert normalised["last_regular_image_filename"] == "789"


def test_quote_tweet_missing_created_at_is_not_old_enough() -> None:
    assert bot.quote_tweet_is_old_enough({"id": "123"}) is False
    assert bot.quote_tweet_is_old_enough({"id": "123", "created_at": "not a date"}) is False


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

    with pytest.raises(bot.InvalidMemePostReceipt):
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


def test_state_backup_bak1_contains_newest_committed_meme_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 5)
    bot.save_state({"posted_meme_filenames": [], "next_meme_post_epoch": 1_700_000_000}, durable=True)
    committed = {
        "posted_meme_filenames": ["001_meme.png"],
        "last_main_post_id": "970001",
        "last_meme_post_epoch": 1_800_000_000,
        "next_meme_post_epoch": 1_800_086_400,
    }
    bot.save_state(committed, durable=True)
    state_file.write_text("{bad json", encoding="utf-8")

    recovered = bot.load_state()

    assert recovered["posted_meme_filenames"] == ["001_meme.png"]
    assert recovered["last_main_post_id"] == "970001"
    assert recovered["next_meme_post_epoch"] == 1_800_086_400


def test_state_backup_bak1_contains_newest_committed_regular_quote_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 5)
    bot.save_state({"next_quote_post_epoch": 1}, durable=True)
    committed = {
        "last_main_post_id": "950001",
        "last_quote_post_epoch": 1_800_000_000,
        "next_quote_post_epoch": 1_800_007_200,
    }
    bot.save_state(committed, durable=True)
    state_file.write_text("{bad json", encoding="utf-8")

    recovered = bot.load_state()

    assert recovered["last_main_post_id"] == "950001"
    assert recovered["last_quote_post_epoch"] == 1_800_000_000
    assert recovered["next_quote_post_epoch"] == 1_800_007_200


def test_protected_durable_write_fails_when_parent_fsync_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "protected.json"
    real_open = bot.os.open

    def failing_open(path: str, flags: int) -> int:
        if Path(path) == tmp_path:
            raise OSError("directory fsync unavailable")
        return real_open(path, flags)

    monkeypatch.setattr(bot.os, "open", failing_open)

    with pytest.raises(OSError, match="directory fsync unavailable"):
        bot.atomic_write_json(target, {"ok": True}, durable=True)


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
    monkeypatch.setattr(bot, "current_image_sha256", lambda path: (_ for _ in ()).throw(OSError("read failed")))

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

    def fake_quote(lines_used: set, *, excluded_quote_hashes: set[str] | None = None) -> dict:
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


def test_append_unique_capped_preserves_order_and_moves_existing_item_to_tail() -> None:
    assert bot.append_unique_capped(["a", "b", "c"], "b", 3) == ["a", "c", "b"]


def test_append_unique_capped_discards_oldest_items() -> None:
    assert bot.append_unique_capped(["a", "b", "c"], "d", 3) == ["b", "c", "d"]


@pytest.mark.parametrize(
    "reply,expected",
    [
        ("", False),
        ("Too few", False),
        ("SKIP", False),
        ("A plain reply with enough length.", True),
        ("As Margaret Thatcher, I must respond.", False),
        ("I am Margaret Thatcher and I approve.", False),
        ("margaret thatcher said exactly this.", False),
    ],
)
def test_generated_reply_is_safe_enough(reply: str, expected: bool) -> None:
    assert bot.generated_reply_is_safe_enough(reply) is expected


def test_meme_summary_context_limit_covers_current_analysis_shape() -> None:
    summary = (
        "2x2 grid meme: top-left Cuba 2016 rundown street, bottom-left Venezuela 2019 street scene with man on rubble, "
        "bottom cartoon 'Fantasy Land' candy castle; right column shows same man saying 'I PREFER REAL SOCIALISM', "
        "'I SAID REAL SOCIALISM', then 'PERFECTION' over the fantasy image Anti-socialist message: Real-world socialism "
        "produces poverty and failure; the only 'real socialism' that works is pure fantasy Analysis metadata: ranking 17, "
        "shareability high."
    )
    context = bot.tweet_context_text({"image_summary": summary})

    assert len(context) <= bot.THREAD_CONTEXT_MAX_CHARS_PER_POST
    assert bot.trim_context_text(context, bot.THREAD_CONTEXT_MAX_CHARS_PER_POST) == context


def test_schedule_next_quote_post_uses_configured_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    state: dict = {}
    monkeypatch.setattr(bot.random, "randint", lambda low, high: 123)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)

    bot.schedule_next_quote_post(state, from_epoch=1_000)

    assert state["next_quote_post_epoch"] == 1_123


def test_rate_limit_cooldown_uses_future_reset_with_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    state: dict = {}
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_000)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)

    bot.record_api_error(state, bot.ApiError("rate limited", service="x", status_code=429, reset_epoch=1_200), "x")

    assert state["api_cooldown_until_epoch"] == 1_260
    assert state["api_cooldown_reason"] == "x returned 429/rate limit"


@pytest.mark.parametrize("reset_epoch", [None, 900])
def test_rate_limit_cooldown_falls_back_for_missing_or_past_reset(
    monkeypatch: pytest.MonkeyPatch,
    reset_epoch: int | None,
) -> None:
    state: dict = {}
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_000)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)

    bot.record_api_error(state, bot.ApiError("rate limited", service="x", status_code=429, reset_epoch=reset_epoch), "x")

    assert state["api_cooldown_until_epoch"] == 1_000 + bot.COOLDOWN_AFTER_429_SECONDS


def test_clear_expired_api_cooldowns_clears_only_expired_values(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {
        "api_cooldown_until_epoch": 900,
        "api_cooldown_reason": "old",
        "quote_api_cooldown_until_epoch": 1_100,
        "quote_api_cooldown_reason": "still active",
    }
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_000)

    assert bot.clear_expired_api_cooldowns(state) is True
    assert state["api_cooldown_until_epoch"] == 0
    assert state["api_cooldown_reason"] == ""
    assert state["quote_api_cooldown_until_epoch"] == 1_100
    assert state["quote_api_cooldown_reason"] == "still active"


def test_sanitize_next_reply_lane_priority_normalizes_invalid_value() -> None:
    state = {"next_reply_lane_priority": "sideways"}

    assert bot.sanitize_next_reply_lane_priority(state) is True
    assert state["next_reply_lane_priority"] == "normal"


def test_sanitize_next_reply_lane_priority_accepts_valid_value() -> None:
    state = {"next_reply_lane_priority": "quote"}

    assert bot.sanitize_next_reply_lane_priority(state) is False
    assert state["next_reply_lane_priority"] == "quote"


def test_local_config_coercion_accepts_boolean_strings_and_rejects_boolean_ints() -> None:
    assert bot._coerce_local_config_value("ENABLE_AUTO_REPLIES", "false", True) is False
    assert bot._coerce_local_config_value("ENABLE_AUTO_REPLIES", "yes", False) is True

    with pytest.raises(ValueError):
        bot._coerce_local_config_value("MAX_AUTO_REPLIES_PER_DAY", True, 24)


def test_local_config_coercion_rejects_negative_and_nonpositive_timings() -> None:
    with pytest.raises(ValueError):
        bot._coerce_local_config_value("POST_SLEEP_MIN", -1, bot.POST_SLEEP_MIN)

    with pytest.raises(ValueError):
        bot._coerce_local_config_value("MAX_AUTO_REPLIES_PER_DAY", 0, bot.MAX_AUTO_REPLIES_PER_DAY)


def apply_local_config_for_test(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    data: dict[str, object],
    *,
    initial: dict[str, object] | None = None,
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
    bot.apply_local_config()
    return before


def test_local_config_interacting_invalid_overrides_are_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {"POST_SLEEP_MIN": 10000, "POST_SLEEP_MAX": 5000},
        initial={"POST_SLEEP_MIN": 7200, "POST_SLEEP_MAX": 9000},
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
    )

    assert bot.POST_SLEEP_MIN == before["POST_SLEEP_MIN"] == 7200
    assert bot.POST_SLEEP_MAX == before["POST_SLEEP_MAX"] == 9000
    assert bot.MAX_MENTIONS_PER_CHECK == before["MAX_MENTIONS_PER_CHECK"] == 5


def test_local_config_unsupported_key_cannot_override_arbitrary_globals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_func = bot.log_json_debug
    apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {"log_json_debug": None, "X_API_BASE_URL": "https://evil.invalid", "POST_SLEEP_MIN": 7300, "POST_SLEEP_MAX": 7400},
        initial={"POST_SLEEP_MIN": 7200, "POST_SLEEP_MAX": 9000},
    )

    assert bot.log_json_debug is original_func
    assert bot.POST_SLEEP_MIN == 7300
    assert bot.POST_SLEEP_MAX == 7400
    assert not hasattr(bot, "X_API_BASE_URL")


def test_local_config_existing_production_style_overrides_still_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {
            "ENABLE_AUTO_REPLIES": True,
            "MIN_SECONDS_BETWEEN_REPLIES": 3600,
            "MAX_AUTO_REPLIES_PER_DAY": 24,
            "MAX_QUOTE_REPLIES_PER_DAY": 12,
            "POST_SLEEP_MIN": 7200,
            "POST_SLEEP_MAX": 9000,
        },
        initial={
            "ENABLE_AUTO_REPLIES": False,
            "MIN_SECONDS_BETWEEN_REPLIES": 1,
            "MAX_AUTO_REPLIES_PER_DAY": 2,
            "MAX_QUOTE_REPLIES_PER_DAY": 1,
            "POST_SLEEP_MIN": 100,
            "POST_SLEEP_MAX": 200,
        },
    )

    assert bot.ENABLE_AUTO_REPLIES is True
    assert bot.MIN_SECONDS_BETWEEN_REPLIES == 3600
    assert bot.MAX_AUTO_REPLIES_PER_DAY == 24
    assert bot.MAX_QUOTE_REPLIES_PER_DAY == 12
    assert bot.POST_SLEEP_MIN == 7200
    assert bot.POST_SLEEP_MAX == 9000
