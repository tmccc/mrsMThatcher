from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import backtest_original_editorial_matching as bt

bot = bt.bot


def test_module_import_is_safe_without_inherited_openai_environment(
    tmp_path: Path,
) -> None:
    env = os.environ.copy()
    env.pop("OPENAI_API_BASE_URL", None)
    env.pop("OPENAI_API_KEY", None)
    env["TMPDIR"] = str(tmp_path)
    script = """
from tools import backtest_original_editorial_matching as backtest

assert backtest.bot.OPENAI_BASE == "http://127.0.0.1:9/v1"
assert backtest.bot.OPENAI_API_KEY == "offline"
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout


def test_vocabulary_normalisation_collapses_synonyms() -> None:
    assert bt.canonical_concept("leadership_display") == "leadership"
    assert bt.canonical_concept("leadership-presence") == "leadership"
    assert bt.canonical_concept("assertion_of_principle") == "principle"
    assert bt.canonical_concept("principle framing") == "principle"
    assert bt.canonical_concept("argumentative_emphasis") == "argumentative_emphasis"
    assert bt.canonical_concepts("duty_and_responsibility") == {"duty"}
    assert bt.canonical_concepts("conviction_and_principle") == {"conviction", "principle"}
    assert bt.canonical_concepts("leadership_and_duty") == {"leadership", "duty"}


def test_idf_downweights_common_concepts() -> None:
    images = {
        f"t{i}.jpg": bt.ImageRecord(
            basename=f"t{i}.jpg",
            path=Path(f"t{i}.jpg"),
            sha256="x",
            production_analysis={},
            editorial_analysis={
                "abstract_quote_affinities": ["leadership", "rare_signal" if i == 0 else "common_signal"],
                "editorial_functions": [],
                "best_quote_types": [],
            },
        )
        for i in range(6)
    }
    idf, df = bt.build_editorial_idf(images)

    assert df["leadership"] == 6
    assert df["rare_signal"] == 1
    assert idf["rare_signal"] > idf["leadership"]


def test_quote_dimension_profile_is_grounded_not_universal() -> None:
    economic = {
        "primary_topics": ["tax", "enterprise"],
        "secondary_topics": [],
        "tone": ["serious"],
        "visual_energy": "low",
    }
    personal = {
        "primary_topics": ["family"],
        "secondary_topics": [],
        "tone": ["warm"],
        "visual_energy": "low",
    }

    econ_profile = bt.quote_dimension_profile(economic)
    personal_profile = bt.quote_dimension_profile(personal)

    assert econ_profile["economic_seriousness"] > 0
    assert personal_profile["human_warmth"] > 0
    assert personal_profile["economic_seriousness"] == 0


def test_editorial_score_is_deterministic_and_avoid_penalises() -> None:
    quote = {
        "primary_topics": ["freedom"],
        "secondary_topics": [],
        "tone": ["serious"],
        "archive_image_preferences": {"visual_affinities": ["principle"]},
    }
    editorial = {
        "abstract_quote_affinities": ["freedom", "principle"],
        "editorial_functions": ["assertion_of_principle"],
        "best_quote_types": [],
        "avoid_quote_types": ["freedom"],
        "overall_editorial_utility": 7.0,
    }
    idf = {"freedom": 2.0, "principle": 1.0}

    score1, detail1 = bt.editorial_affinity_score(quote, editorial, idf)
    score2, detail2 = bt.editorial_affinity_score(quote, editorial, idf)

    assert score1 == score2
    assert detail1 == detail2
    assert "freedom" in detail1["avoid_matches"]
    no_avoid = dict(editorial)
    no_avoid["avoid_quote_types"] = []
    score_without_avoid, _ = bt.editorial_affinity_score(quote, no_avoid, idf)
    assert score_without_avoid > score1


def test_dimension_score_uses_zero_to_ten_scale() -> None:
    assert bt.normalise_dimension_score(9.5, "conviction") == 0.95
    assert bt.normalise_dimension_score(10, "conviction") == 1.0
    assert bt.normalise_dimension_score(0, "conviction") == 0.0


def test_dimension_compatibility_has_positive_and_negative_range() -> None:
    quote = {"primary_topics": ["freedom"], "tone": ["serious"], "visual_energy": "medium"}
    high_editorial = {"dimension_scores": {"conviction": 10}}
    low_editorial = {"dimension_scores": {"conviction": 0}}

    high_score, high_detail = bt.editorial_dimension_score(quote, high_editorial)
    low_score, low_detail = bt.editorial_dimension_score(quote, low_editorial)

    assert high_score > 0
    assert low_score < 0
    assert high_detail["dimension_terms"][0][3] == 1.0
    assert low_detail["dimension_terms"][0][3] == 0.0


def test_editorial_utility_uses_zero_to_ten_scale() -> None:
    quote = {"primary_topics": ["freedom"], "tone": ["serious"]}
    idf = {"freedom": 1.0}
    high_score, high_detail = bt.editorial_affinity_score(
        quote,
        {"abstract_quote_affinities": ["freedom"], "overall_editorial_utility": 9},
        idf,
    )
    neutral_score, neutral_detail = bt.editorial_affinity_score(
        quote,
        {"abstract_quote_affinities": ["freedom"], "overall_editorial_utility": 5.5},
        idf,
    )
    low_score, low_detail = bt.editorial_affinity_score(
        quote,
        {"abstract_quote_affinities": ["freedom"], "overall_editorial_utility": 0},
        idf,
    )

    assert high_detail["utility_adj"] > 0
    assert neutral_detail["utility_adj"] == 0
    assert low_detail["utility_adj"] == -0.5
    assert high_score > neutral_score > low_score


def test_ubiquitous_concepts_do_not_divide_by_zero() -> None:
    images = {
        "t1.jpg": bt.ImageRecord("t1.jpg", Path("t1.jpg"), "x", {}, {"abstract_quote_affinities": ["leadership"]}),
        "t2.jpg": bt.ImageRecord("t2.jpg", Path("t2.jpg"), "x", {}, {"abstract_quote_affinities": ["leadership"]}),
    }

    idf, _df = bt.build_editorial_idf(images)

    assert idf["leadership"] > 0


def _write_fixture_files(
    tmp_path: Path,
    *,
    stale_sha: bool = False,
    generated: bool = False,
    bad_kind: bool = False,
    dimension_score: object = 8.0,
    utility: object = 7.5,
) -> SimpleNamespace:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    basename = "tg_" + ("a" * 64) + ".png" if generated else "t01.jpg"
    image_path = image_dir / basename
    image_path.write_bytes(b"image-bytes")
    sha = hashlib.sha256(b"image-bytes").hexdigest()
    prod_hash = sha
    quote = "A serious quote."
    quote_hash = bot.quote_text_hash(quote)
    quotes_file = tmp_path / "quotes.txt"
    quotes_file.write_text(quote + "\n", encoding="utf-8")
    quote_analysis = tmp_path / "quote_analysis.json"
    quote_analysis.write_text(
        json.dumps(
            {
                "analysis_kind": "quotes",
                "schema_version": 2,
                "items": {quote_hash: {"analysis": {"primary_topics": ["freedom"], "tone": ["serious"], "visual_energy": "low"}}},
            }
        ),
        encoding="utf-8",
    )
    image_analysis = tmp_path / "image_analysis.json"
    image_analysis.write_text(
        json.dumps(
            {
                "analysis_kind": "images",
                "schema_version": 3,
                "path_index": {basename: prod_hash},
                "items": {
                    prod_hash: {
                        "analysis": {
                            "pairing": {"best_for_topics": ["freedom"], "general_reusability": 70, "semantic_specificity": 40},
                            "themes": ["freedom"],
                            "tone": ["serious"],
                            "visual_energy": "low",
                            "quality": {"overall": 80, "crop_suitability_for_x": 80},
                            "seasonality": {"avoid_outside_season_or_occasion": False},
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    editorial = tmp_path / "editorial.json"
    editorial.write_text(
        json.dumps(
            {
                "analysis_kind": "wrong" if bad_kind else bt.EXPECTED_ANALYSIS_KIND,
                "items": {
                    basename: {
                        "basename": basename,
                        "path": str(image_path),
                        "sha256": "bad" if stale_sha else sha,
                        "analysis": {
                            "abstract_quote_affinities": ["freedom"],
                            "editorial_functions": ["assertion_of_principle"],
                            "best_quote_types": ["principle"],
                            "avoid_quote_types": [],
                            "dimension_scores": {"conviction": dimension_score},
                            "overall_editorial_utility": utility,
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return SimpleNamespace(
        quotes_file=str(quotes_file),
        quote_analysis=str(quote_analysis),
        image_analysis=str(image_analysis),
        editorial_analysis=str(editorial),
        image_dir=str(image_dir),
    )


def test_malformed_experimental_json_fails_clearly(tmp_path: Path) -> None:
    args = _write_fixture_files(tmp_path, bad_kind=True)

    with pytest.raises(bt.BacktestError, match="analysis_kind"):
        bt.validate_inputs(args)


def test_stale_sha256_fails_clearly(tmp_path: Path) -> None:
    args = _write_fixture_files(tmp_path, stale_sha=True)

    with pytest.raises(bt.BacktestError, match="stale SHA"):
        bt.validate_inputs(args)


def test_generated_images_are_excluded_from_original_only_experiment(tmp_path: Path) -> None:
    args = _write_fixture_files(tmp_path, generated=True)

    with pytest.raises(bt.BacktestError, match="generated image"):
        bt.validate_inputs(args)


def test_out_of_range_dimension_score_fails_clearly(tmp_path: Path) -> None:
    args = _write_fixture_files(tmp_path, dimension_score=10.1)

    with pytest.raises(bt.BacktestError, match="outside 0..10"):
        bt.validate_inputs(args)


def test_out_of_range_editorial_utility_fails_clearly(tmp_path: Path) -> None:
    args = _write_fixture_files(tmp_path, utility=10.1)

    with pytest.raises(bt.BacktestError, match="overall_editorial_utility outside 0..10"):
        bt.validate_inputs(args)


def test_baseline_score_remains_unchanged_by_editorial_layer(tmp_path: Path) -> None:
    args = _write_fixture_files(tmp_path)
    _lines, quote_analysis, image_analysis, images = bt.validate_inputs(args)
    quote = bt.quote_records(["A serious quote."], quote_analysis)[0]
    idf, _df = bt.build_editorial_idf(images)

    rows = bt.rank_images_for_quote(quote, images, image_analysis, idf)
    direct_score, direct_components, eligible = bot.score_image_for_quote(
        quote.analysis,
        next(iter(images.values())).production_analysis,
        bot.build_image_topic_idf(image_analysis),
    )

    assert eligible is True
    assert rows[0]["baseline_score"] == direct_score
    assert rows[0]["baseline_components"] == direct_components


def test_repeated_runs_are_deterministic_with_fixed_seed(tmp_path: Path) -> None:
    args = _write_fixture_files(tmp_path)
    args.log_dir = str(tmp_path)
    args.recent_limit = 30
    args.broad_sample = 1
    args.seed = 123

    first = bt.build_outputs(args)
    second = bt.build_outputs(args)

    assert first["results"] == second["results"]


def test_recent_post_parser_recovers_successful_quote_image_post(tmp_path: Path) -> None:
    quote = "There is an increasing belief that freedom is divisible. No myth is more dangerous. Freedom is indivisible."
    qhash = bot.quote_text_hash(quote)
    quote_record = bt.QuoteRecord(qhash, quote, 594, {"primary_topics": ["freedom"]}, "broad")
    log = tmp_path / "mrsMThatcher.log"
    log.write_text(
        "\n".join(
            [
                f"2026-07-09 19:35:01 INFO     select_quote_candidate:4663 - Selected quote line_no=594 quote_hash={qhash} weight=1.18 seasonal_boost=False",
                "2026-07-09 19:35:02 INFO     choose_matched_unused_image:5060 - Selected matched image basename=t70.jpg image_no=68 score=39.76 components=historical=0.0, mismatches=0.0, quality=1.8, scene_activity_symbols=16.0, tone_mood=14.0, topics=0.0, visual_energy=8.0",
                "2026-07-09 19:35:03 INFO     post_random_quote:5332 - Quote/image posted successfully. posted_id=2075287633131864264",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    posts = bt.parse_recent_posts(tmp_path, {qhash: quote_record}, 30)

    assert len(posts) == 1
    assert posts[0].sample == "recent"
    assert posts[0].recent_post["actual_image"] == "t70.jpg"
    assert posts[0].recent_post["actual_logged_score"] == 39.76


def test_diagnostic_freedom_quote_is_found_when_present() -> None:
    qhash = bot.quote_text_hash(bt.DIAGNOSTIC_T70_QUOTE)
    records = [
        bt.QuoteRecord("other", "Other quote.", 1, {}, "broad"),
        bt.QuoteRecord(qhash, bt.DIAGNOSTIC_T70_QUOTE, 594, {"primary_topics": ["freedom"]}, "broad"),
    ]

    diagnostic = bt.diagnostic_quote_record(records)

    assert diagnostic is not None
    assert diagnostic.sample == "diagnostic"
    assert diagnostic.quote_hash == qhash


def test_t70_actual_corpus_record_scores_on_correct_scale() -> None:
    args = SimpleNamespace(
        quotes_file=str(bt.ROOT / "mrsMThatcher.txt"),
        quote_analysis=str(bt.ROOT / "quote_analysis.json"),
        image_analysis=str(bt.ROOT / "image_analysis.json"),
        editorial_analysis=str(bt.ROOT / "original_image_editorial_analysis_experiment_v1.json"),
        image_dir=str(bt.ROOT / "images"),
    )
    lines, quote_analysis, _image_analysis, images = bt.validate_inputs(args)
    quote = next(q for q in bt.quote_records(lines, quote_analysis) if q.text == bt.DIAGNOSTIC_T70_QUOTE)
    t70 = images["t70.jpg"]

    assert bt.normalise_dimension_score(t70.editorial_analysis["dimension_scores"]["conviction"], "conviction") == 0.95
    assert bt.normalise_editorial_utility(t70.editorial_analysis["overall_editorial_utility"]) == 9.0
    b_score, b_detail = bt.editorial_dimension_score(quote.analysis, t70.editorial_analysis)
    a_score, a_detail = bt.editorial_affinity_score(quote.analysis, t70.editorial_analysis, {"freedom": 1.0, "authority": 1.0, "resolve": 1.0})

    assert b_detail["dimension_terms"]
    assert b_score > -4.3687499999999995
    assert a_detail["utility_adj"] > 0
    assert a_score > 0
