from __future__ import annotations

import json
import copy
import random
from datetime import datetime
from pathlib import Path

import pytest

import mrs_log_digest as digest
from tests.test_unit_helpers import bot, image_analysis_for_paths


def _write_editorial_file(path: Path, image_paths: list[Path], analyses: dict[str, dict] | None = None) -> None:
    analyses = analyses or {}
    items = {}
    for image_path in image_paths:
        items[image_path.name] = {
            "basename": image_path.name,
            "sha256": bot.file_sha256(image_path),
            "analysis": analyses.get(
                image_path.name,
                {
                    "abstract_quote_affinities": [],
                    "editorial_functions": [],
                    "best_quote_types": [],
                    "avoid_quote_types": [],
                    "dimension_scores": {dim: 0 for dim in bot.ORIGINAL_EDITORIAL_DIMENSIONS},
                    "overall_editorial_utility": 5.5,
                },
            ),
        }
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "analysis_kind": "original_editorial_experiment",
                "items": items,
            }
        ),
        encoding="utf-8",
    )


def _basic_quote() -> dict:
    return {
        "quote_hash": "a" * 64,
        "line_no": 12,
        "text": "Freedom and family matter.",
        "analysis": {
            "primary_topics": ["freedom", "family"],
            "secondary_topics": [],
            "tone": ["serious"],
            "visual_energy": "medium",
            "archive_image_preferences": {"visual_affinities": ["freedom", "family"]},
            "seasonality": {"hard_exclude_outside_windows": False},
        },
    }


def _editorial_analysis(**overrides: object) -> dict:
    analysis = {
        "abstract_quote_affinities": [],
        "editorial_functions": [],
        "best_quote_types": [],
        "avoid_quote_types": [],
        "dimension_scores": {dim: 0 for dim in bot.ORIGINAL_EDITORIAL_DIMENSIONS},
        "overall_editorial_utility": 5.5,
    }
    analysis.update(overrides)
    return analysis


def test_shadow_disabled_does_not_require_experimental_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING", False)
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_ANALYSIS_FILE", "/definitely/missing.json")

    bot.validate_original_editorial_shadow_startup()


def test_original_editorial_metadata_validation_rejects_bad_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"image")
    editorial_file = tmp_path / "editorial.json"
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_ANALYSIS_FILE", str(editorial_file))
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_ANALYSIS_CACHE", {})

    _write_editorial_file(editorial_file, [image_path])
    assert "t01.jpg" in bot.load_original_editorial_analysis()

    data = json.loads(editorial_file.read_text(encoding="utf-8"))
    data["analysis_kind"] = "wrong"
    editorial_file.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_ANALYSIS_CACHE", {})
    with pytest.raises(RuntimeError, match="analysis_kind"):
        bot.load_original_editorial_analysis()

    data["analysis_kind"] = "original_editorial_experiment"
    data["items"]["t01.jpg"]["sha256"] = "bad"
    editorial_file.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_ANALYSIS_CACHE", {})
    with pytest.raises(RuntimeError, match="stale SHA"):
        bot.load_original_editorial_analysis()

    generated = "tg_" + ("b" * 64) + ".png"
    data["items"] = {
        generated: {
            "basename": generated,
            "sha256": bot.file_sha256(image_path),
            "analysis": data["items"]["t01.jpg"]["analysis"],
        }
    }
    editorial_file.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_ANALYSIS_CACHE", {})
    with pytest.raises(RuntimeError, match="generated-style basename"):
        bot.load_original_editorial_analysis()


def test_original_editorial_metadata_rejects_boolean_schema_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"image")
    editorial_file = tmp_path / "editorial.json"
    _write_editorial_file(editorial_file, [image_path])
    data = json.loads(editorial_file.read_text(encoding="utf-8"))
    data["schema_version"] = True
    editorial_file.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_ANALYSIS_FILE", str(editorial_file))
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_ANALYSIS_CACHE", {})

    with pytest.raises(RuntimeError, match="schema_version"):
        bot.load_original_editorial_analysis()


@pytest.mark.parametrize(
    "analysis_patch, message",
    [
        ({"dimension_scores": {"conviction": 10.1}}, "outside 0..10"),
        ({"overall_editorial_utility": 10.1}, "overall_editorial_utility"),
    ],
)
def test_original_editorial_metadata_validation_rejects_out_of_range_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    analysis_patch: dict,
    message: str,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"image")
    analysis = {
        "abstract_quote_affinities": [],
        "editorial_functions": [],
        "best_quote_types": [],
        "avoid_quote_types": [],
        "dimension_scores": {dim: 0 for dim in bot.ORIGINAL_EDITORIAL_DIMENSIONS},
        "overall_editorial_utility": 5.5,
    }
    if "dimension_scores" in analysis_patch:
        analysis["dimension_scores"].update(analysis_patch["dimension_scores"])
    else:
        analysis.update(analysis_patch)
    editorial_file = tmp_path / "editorial.json"
    _write_editorial_file(editorial_file, [image_path], {"t01.jpg": analysis})
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_ANALYSIS_FILE", str(editorial_file))
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_ANALYSIS_CACHE", {})

    with pytest.raises(RuntimeError, match=message):
        bot.load_original_editorial_analysis()


@pytest.mark.parametrize(
    "removed, expected",
    [
        (["conviction"], "conviction"),
        (["conviction", "warning"], "conviction.*warning|warning.*conviction"),
    ],
)
def test_original_editorial_metadata_requires_complete_dimension_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    removed: list[str],
    expected: str,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"image")
    dimensions = {dim: 0 for dim in bot.ORIGINAL_EDITORIAL_DIMENSIONS}
    for dim in removed:
        dimensions.pop(dim)
    editorial_file = tmp_path / "editorial.json"
    _write_editorial_file(editorial_file, [image_path], {"t01.jpg": _editorial_analysis(dimension_scores=dimensions)})
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_ANALYSIS_FILE", str(editorial_file))
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_ANALYSIS_CACHE", {})

    with pytest.raises(RuntimeError, match=expected):
        bot.load_original_editorial_analysis()


def test_original_editorial_metadata_rejects_unexpected_dimension_and_accepts_complete_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"image")
    editorial_file = tmp_path / "editorial.json"
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_ANALYSIS_FILE", str(editorial_file))

    _write_editorial_file(editorial_file, [image_path], {"t01.jpg": _editorial_analysis()})
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_ANALYSIS_CACHE", {})
    assert set(bot.load_original_editorial_analysis()["t01.jpg"]["dimension_scores"]) == set(bot.ORIGINAL_EDITORIAL_DIMENSIONS)

    dimensions = {dim: 0 for dim in bot.ORIGINAL_EDITORIAL_DIMENSIONS}
    dimensions["unexpected_dimension"] = 1
    _write_editorial_file(editorial_file, [image_path], {"t01.jpg": _editorial_analysis(dimension_scores=dimensions)})
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_ANALYSIS_CACHE", {})
    with pytest.raises(RuntimeError, match="unexpected.*unexpected_dimension"):
        bot.load_original_editorial_analysis()


def test_original_editorial_config_validation_rejects_bad_numeric_values() -> None:
    assert not bot.validate_runtime_config_values({"ORIGINAL_EDITORIAL_SHADOW_WEIGHT": 0.32, "ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT": 4.0})
    assert "ORIGINAL_EDITORIAL_SHADOW_WEIGHT must be a number" in bot.validate_runtime_config_values({"ORIGINAL_EDITORIAL_SHADOW_WEIGHT": True})
    assert "ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT must be non-negative" in bot.validate_runtime_config_values({"ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT": -1.0})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("key", ["ORIGINAL_EDITORIAL_SHADOW_WEIGHT", "ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT"])
def test_original_editorial_runtime_config_rejects_non_finite_values(key: str, value: float) -> None:
    assert f"{key} must be finite" in bot.validate_runtime_config_values({key: value})


@pytest.mark.parametrize("value", ["nan", "NaN", "inf", "+inf", "-inf"])
@pytest.mark.parametrize("key", ["ORIGINAL_EDITORIAL_SHADOW_WEIGHT", "ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT"])
def test_original_editorial_local_config_rejects_non_finite_values(key: str, value: str) -> None:
    with pytest.raises(ValueError, match="finite"):
        bot._coerce_local_config_value(key, value, 0.32)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_original_editorial_metadata_rejects_non_finite_values(value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        bot.original_editorial_numeric(value, key="dimension")


@pytest.mark.parametrize("field", ["dimension_scores", "overall_editorial_utility"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_original_editorial_file_rejects_non_finite_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: float,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"image")
    analysis = _editorial_analysis()
    if field == "dimension_scores":
        analysis["dimension_scores"]["conviction"] = value
    else:
        analysis[field] = value
    editorial_file = tmp_path / "editorial.json"
    _write_editorial_file(editorial_file, [image_path], {"t01.jpg": analysis})
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_ANALYSIS_FILE", str(editorial_file))
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_ANALYSIS_CACHE", {})

    with pytest.raises(RuntimeError, match="finite"):
        bot.load_original_editorial_analysis()


def test_shadow_scoring_scale_affinity_cap_and_determinism(monkeypatch: pytest.MonkeyPatch) -> None:
    quote = _basic_quote()["analysis"]
    editorial = {
        "abstract_quote_affinities": ["freedom", "family", "leadership"],
        "editorial_functions": ["duty_and_responsibility"],
        "best_quote_types": [],
        "avoid_quote_types": [],
        "dimension_scores": {"conviction": 10, "human_warmth": 10, "leadership": 10},
        "overall_editorial_utility": 9,
    }
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_SHADOW_WEIGHT", 1.0)
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT", 2.0)

    first, first_detail = bot.original_editorial_shadow_score(quote, editorial)
    second, second_detail = bot.original_editorial_shadow_score(quote, editorial)

    assert first == second
    assert first_detail == second_detail
    assert first == 2.0
    assert first_detail["cap_hit"] is True
    assert "freedom" in first_detail["affinity_matches"]
    assert "family" in first_detail["affinity_matches"]
    assert "leadership" not in first_detail["affinity_matches"]
    assert bot.original_editorial_quote_dimension_profile({"primary_topics": ["government"], "tone": [], "visual_energy": "medium"})["leadership"] == 0


def test_shadow_result_uses_actual_original_candidate_set_and_replaces_production_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    t01 = image_dir / "t01.jpg"
    t02 = image_dir / "t02.jpg"
    generated = image_dir / ("tg_" + ("c" * 64) + ".png")
    t01.write_bytes(b"one")
    t02.write_bytes(b"two")
    generated.write_bytes(b"generated")
    image_analysis = image_analysis_for_paths(
        [t01, t02, generated],
        {
            "t01.jpg": {"description": "production", "seasonality": {"avoid_outside_season_or_occasion": False}},
            "t02.jpg": {"description": "shadow", "seasonality": {"avoid_outside_season_or_occasion": False}},
            generated.name: {"description": "generated", "seasonality": {"avoid_outside_season_or_occasion": False}},
        },
    )
    editorial_file = tmp_path / "editorial.json"
    _write_editorial_file(
        editorial_file,
        [t01, t02],
        {
            "t01.jpg": _editorial_analysis(),
            "t02.jpg": _editorial_analysis(
                abstract_quote_affinities=["freedom", "family"],
                dimension_scores={**{dim: 0 for dim in bot.ORIGINAL_EDITORIAL_DIMENSIONS}, "conviction": 10, "human_warmth": 10},
                overall_editorial_utility=9,
            ),
        },
    )
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: image_analysis)
    monkeypatch.setattr(bot, "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING", True)
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_ANALYSIS_FILE", str(editorial_file))
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_ANALYSIS_CACHE", {})
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_SHADOW_WEIGHT", 0.32)
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT", 4.0)
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 10))

    def fake_score(_quote: dict, analysis: dict, _idf: dict | None = None) -> tuple[float, dict, bool]:
        scores = {"production": 10.0, "shadow": 9.0, "generated": 12.0}
        return scores[analysis["description"]], {"topics": scores[analysis["description"]]}, True

    monkeypatch.setattr(bot, "score_image_for_quote", fake_score)
    images_used = {generated.name}
    state = {"original_regular_posts_since_generated_image": 2}
    caplog.set_level("INFO", logger=bot.log.name)

    chosen = bot.choose_matched_unused_image(images_used, _basic_quote(), state)

    assert chosen["basename"] == "t02.jpg"
    assert chosen["score"] > 9.0
    assert chosen["original_editorial_adjustment"] > 0.0
    assert images_used == {generated.name}
    assert "ORIGINAL_EDITORIAL_SELECTION_RESULT" in caplog.text
    payload = json.loads(caplog.text.split("ORIGINAL_EDITORIAL_SELECTION_RESULT ", 1)[1].splitlines()[0])
    assert payload["production_winner"] == "t01.jpg"
    assert payload["shadow_original_winner"] == "t02.jpg"
    assert payload["winner_changed"] is True
    assert payload["selection_applied"] is True
    assert payload["selected_winner"] == "t02.jpg"
    assert payload["eligible_original_count"] == 2


def test_shadow_result_handles_generated_production_winner_without_treating_it_as_editorial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    t01 = image_dir / "t01.jpg"
    generated = image_dir / ("tg_" + ("d" * 64) + ".png")
    t01.write_bytes(b"one")
    generated.write_bytes(b"generated")
    image_analysis = image_analysis_for_paths(
        [t01, generated],
        {
            "t01.jpg": {"description": "original", "seasonality": {"avoid_outside_season_or_occasion": False}},
            generated.name: {"description": "generated", "seasonality": {"avoid_outside_season_or_occasion": False}},
        },
    )
    editorial_file = tmp_path / "editorial.json"
    _write_editorial_file(
        editorial_file,
        [t01],
        {
            "t01.jpg": _editorial_analysis(
                abstract_quote_affinities=["freedom"],
                dimension_scores={**{dim: 0 for dim in bot.ORIGINAL_EDITORIAL_DIMENSIONS}, "conviction": 10},
                overall_editorial_utility=9,
            ),
        },
    )
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: image_analysis)
    monkeypatch.setattr(bot, "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING", True)
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_ANALYSIS_FILE", str(editorial_file))
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_ANALYSIS_CACHE", {})
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 10))

    def fake_score(_quote: dict, analysis: dict, _idf: dict | None = None) -> tuple[float, dict, bool]:
        scores = {"original": 8.0, "generated": 12.0}
        return scores[analysis["description"]], {"topics": scores[analysis["description"]]}, True

    monkeypatch.setattr(bot, "score_image_for_quote", fake_score)
    caplog.set_level("INFO", logger=bot.log.name)

    chosen = bot.choose_matched_unused_image(set(), _basic_quote(), {"original_regular_posts_since_generated_image": 2})

    assert chosen["basename"] == generated.name
    payload = json.loads(caplog.text.split("ORIGINAL_EDITORIAL_SELECTION_RESULT ", 1)[1].splitlines()[0])
    assert payload["production_source"] == "generated"
    assert payload["production_winner"] == generated.name
    assert payload["production_editorial_adjustment"] is None
    assert payload["production_shadow_rank"] is None
    assert payload["shadow_original_winner"] == "t01.jpg"
    assert payload["winner_changed"] is False
    assert payload["selection_applied"] is False
    assert payload["selected_winner"] == generated.name


def test_shadow_digest_parses_and_renders_changed_winner() -> None:
    payload = {
        "quote_hash": "a" * 64,
        "line_no": 12,
        "selection_phase": "normal",
        "production_source": "original",
        "production_winner": "t01.jpg",
        "production_baseline_score": 10.0,
        "production_editorial_adjustment": 0.0,
        "production_shadow_score": 10.0,
        "production_shadow_rank": 2,
        "shadow_original_winner": "t02.jpg",
        "shadow_winner_baseline_score": 9.0,
        "shadow_winner_editorial_adjustment": 2.5,
        "shadow_winner_score": 11.5,
        "winner_changed": True,
        "eligible_original_count": 2,
        "weight": 0.32,
        "max_abs_adjustment": 4.0,
        "cap_hit": False,
        "dimension_matches": ["conviction"],
        "affinity_matches": ["freedom"],
        "penalties": [],
    }
    records = [
        digest.Record(
            ts=datetime(2026, 7, 10, 12, 0, 0),
            level="INFO",
            src="mrs",
            line=1,
            msg="ORIGINAL_EDITORIAL_SHADOW_RESULT " + json.dumps(payload, separators=(",", ":")),
            path="test.log",
            ordinal=1,
        )
    ]

    report = digest.analyse(records)
    rendered = digest.render_markdown(report)

    assert report["original_editorial_shadow"]["summary"]["winner_changes"] == 1
    assert "## Original editorial shadow scoring" in rendered
    assert "shadow-only" in rendered
    assert "t02.jpg" in rendered
    assert "does not imply the shadow image was posted" in rendered
    assert "| time | line_no | production | shadow | production rank | adjustment | reason |" in rendered
    assert "Most frequent positive shadow-winner dimensions:" in rendered


def test_digest_prefers_active_selection_result_over_legacy_shadow_companion() -> None:
    payload = {
        "quote_hash": "a" * 64,
        "line_no": 12,
        "selection_phase": "normal",
        "production_source": "original",
        "production_winner": "t01.jpg",
        "production_baseline_score": 10.0,
        "production_editorial_adjustment": 0.0,
        "production_shadow_score": 10.0,
        "production_shadow_rank": 2,
        "shadow_original_winner": "t02.jpg",
        "shadow_winner_baseline_score": 9.0,
        "shadow_winner_editorial_adjustment": 2.5,
        "shadow_winner_score": 11.5,
        "winner_changed": True,
        "eligible_original_count": 2,
        "weight": 0.32,
        "max_abs_adjustment": 4.0,
        "cap_hit": False,
        "dimension_matches": ["conviction"],
        "affinity_matches": ["freedom"],
        "penalties": [],
        "selection_applied": True,
        "selected_winner": "t02.jpg",
    }
    records = [
        digest.Record(
            ts=datetime(2026, 7, 10, 12, 0, 0),
            level="INFO",
            src="mrs",
            line=1,
            msg="ORIGINAL_EDITORIAL_SELECTION_RESULT " + json.dumps(payload, separators=(",", ":")),
            path="test.log",
            ordinal=1,
        ),
        digest.Record(
            ts=datetime(2026, 7, 10, 12, 0, 0),
            level="INFO",
            src="mrs",
            line=2,
            msg="ORIGINAL_EDITORIAL_SHADOW_RESULT " + json.dumps(
                {key: value for key, value in payload.items() if key not in {"selection_applied", "selected_winner"}},
                separators=(",", ":"),
            ),
            path="test.log",
            ordinal=2,
        ),
    ]

    report = digest.analyse(records)
    editorial = report["original_editorial_shadow"]
    rendered = digest.render_markdown(report)

    assert len(editorial["events"]) == 1
    assert editorial["events"][0]["event_mode"] == "selection"
    assert editorial["events"][0]["selected_winner"] == "t02.jpg"
    assert editorial["summary"]["active_selection_observations"] == 1
    assert editorial["summary"]["legacy_shadow_observations"] == 0
    assert editorial["summary"]["selector_applied_observations"] == 1
    assert editorial["summary"]["selected_winner_changes"] == 1
    assert "## Original editorial image selection" in rendered
    assert "the image actually selected" in rendered
    assert "selector_applied                       = 1" in rendered
    assert "| active | 12 | t01.jpg (original) | t02.jpg | t02.jpg |" in rendered
    assert "This section is shadow-only" not in rendered


def test_digest_reports_when_active_editorial_selector_is_not_applied() -> None:
    summary = digest.original_editorial_shadow_summary(
        [
            {
                "event_mode": "selection",
                "production_source": "generated",
                "production_winner": "tg_fixture.png",
                "shadow_original_winner": "t01.jpg",
                "selected_winner": "tg_fixture.png",
                "selection_applied": False,
                "winner_changed": False,
            }
        ]
    )

    assert summary["active_selection_observations"] == 1
    assert summary["selector_applied_observations"] == 0
    assert summary["selector_not_applied_observations"] == 1
    assert summary["selected_winner_changes"] == 0


def test_shadow_digest_winner_change_percentage_uses_comparable_original_observations() -> None:
    original = [{"production_source": "original", "winner_changed": index < 2} for index in range(5)]
    generated = [{"production_source": "generated", "winner_changed": False} for _ in range(5)]
    summary = digest.original_editorial_shadow_summary(original + generated)

    assert summary["observations"] == 10
    assert summary["production_original"] == 5
    assert summary["production_generated"] == 5
    assert summary["comparable_original_observations"] == 5
    assert summary["winner_changes"] == 2
    assert summary["winner_change_percent"] == 40.0


def test_shadow_digest_winner_change_percentage_handles_generated_only_and_unchanged_originals() -> None:
    generated_only = digest.original_editorial_shadow_summary(
        [{"production_source": "generated", "winner_changed": False} for _ in range(3)]
    )
    unchanged_originals = digest.original_editorial_shadow_summary(
        [{"production_source": "original", "winner_changed": False} for _ in range(3)]
    )

    assert generated_only["comparable_original_observations"] == 0
    assert generated_only["winner_change_percent"] == 0.0
    assert unchanged_originals["comparable_original_observations"] == 3
    assert unchanged_originals["winner_change_percent"] == 0.0


def test_shadow_logger_does_not_mutate_or_reorder_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    candidates = [
        {"basename": "t02.jpg", "image_source": "original", "score": 9.0},
        {"basename": "t01.jpg", "image_source": "original", "score": 10.0},
    ]
    before = copy.deepcopy(candidates)
    analyses = {name: _editorial_analysis() for name in ("t01.jpg", "t02.jpg")}
    monkeypatch.setattr(bot, "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING", True)
    monkeypatch.setattr(bot, "load_original_editorial_analysis", lambda: analyses)

    bot.log_original_editorial_shadow_result(_basic_quote(), candidates[1], candidates, selection_phase="normal")

    assert candidates == before


def test_editorial_selection_keeps_adjusted_score_when_winner_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = {
        "basename": "t01.jpg",
        "image_source": "original",
        "score": 10.0,
        "components": {"topics": 10.0},
    }
    winner = {
        "basename": "t01.jpg",
        "baseline_score": 10.0,
        "editorial_adjustment": 1.25,
        "shadow_score": 11.25,
    }
    payload = {
        "production_source": "original",
        "production_shadow_rank": 1,
        "winner_changed": False,
    }
    monkeypatch.setattr(bot, "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING", True)
    monkeypatch.setattr(
        bot,
        "original_editorial_shadow_result",
        lambda *_args, **_kwargs: (payload, winner),
    )

    selected = bot.apply_original_editorial_selection(
        _basic_quote(), baseline, [baseline], selection_phase="normal"
    )

    assert selected["basename"] == "t01.jpg"
    assert selected["score"] == 11.25
    assert selected["components"]["original_editorial"] == 1.25
    assert payload["selection_applied"] is True


def test_editorial_selection_uses_stable_basename_tie_break_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    paths = [image_dir / "t01.jpg", image_dir / "t02.jpg"]
    for index, path in enumerate(paths):
        path.write_bytes(f"image-{index}".encode())
    metadata = image_analysis_for_paths(
        paths,
        {path.name: {"description": path.name, "seasonality": {"avoid_outside_season_or_occasion": False}} for path in paths},
    )
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: metadata)
    monkeypatch.setattr(bot, "score_image_for_quote", lambda *_args: (10.0, {"topics": 10.0}, True))
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 10))
    monkeypatch.setattr(bot, "load_original_editorial_analysis", lambda: {path.name: _editorial_analysis() for path in paths})
    state = {"original_regular_posts_since_generated_image": 2}

    random.seed(8675309)
    monkeypatch.setattr(bot, "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING", False)
    without_shadow = bot.choose_matched_unused_image(set(), _basic_quote(), copy.deepcopy(state))
    random.seed(8675309)
    monkeypatch.setattr(bot, "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING", True)
    with_shadow = bot.choose_matched_unused_image(set(), _basic_quote(), copy.deepcopy(state))

    assert with_shadow["basename"] == "t01.jpg"
    assert with_shadow["score"] == pytest.approx(9.028)
    assert without_shadow["score"] == 10.0
