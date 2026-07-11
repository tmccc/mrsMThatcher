from __future__ import annotations

from datetime import datetime
import json

import mrs_log_digest as digest
import pytest
from tests.test_generated_image_pool_health_digest import pool, quarantine
from tests.test_generated_image_pool_runway_digest import post


NOW = datetime(2026, 7, 10, 12)


def history(tmp_path, rows, rotated_rows=()):
    current = tmp_path / "bot.log"
    rotated = tmp_path / "bot.log.1"
    current.write_text("".join(rows), encoding="utf-8")
    rotated.write_text("".join(rotated_rows), encoding="utf-8")
    return digest.generated_post_rate_history([current, rotated], NOW)


def render(snapshot, rates):
    report = digest.analyse([])
    report["generated_image_pool_health"] = snapshot
    report["generated_image_post_rates"] = rates
    report["generated_image_utilisation"] = digest.generated_image_utilisation(snapshot, rates)
    return digest.render_markdown(report)


def test_no_generated_usage_is_clean_and_bounded(tmp_path):
    base, names = pool(tmp_path, 3)
    rates = history(tmp_path, [post(datetime(2026, 7, 9), "original", "t01.jpg")])
    result = digest.generated_image_utilisation(digest.generated_pool_health_snapshot(base), rates)
    assert result["active_generated_images"] == 3
    assert result["active_images_used_ever"] == 0
    assert result["active_images_never_used"] == 3
    assert result["active_pool_ever_used_percentage"] == 0
    assert result["total_successful_generated_posts_observed"] == 0
    assert result["top_10_share_of_successful_generated_posts"] is None
    text = render(digest.generated_pool_health_snapshot(base), rates)
    assert "## Generated image utilisation" in text
    assert "not guaranteed to be all-time" in text
    assert names[0] in text


def test_no_generated_images_or_optional_files_is_nonfatal(tmp_path):
    base = tmp_path / "empty"
    (base / "generated_review_approved_images").mkdir(parents=True)
    (base / "generated_image_analysis.json").write_text(json.dumps({"schema_version": 3, "analysis_kind": "images", "path_index": {}, "items": {}}))
    (base / "generated_image_identity_dependence_audit.json").write_text(json.dumps({"schema_version": 1, "analysis_kind": "generated_image_identity_dependence_audit", "items": {}}))
    snapshot = digest.generated_pool_health_snapshot(base)
    result = digest.generated_image_utilisation(snapshot, history(tmp_path, []))
    assert result["active_generated_images"] == 0
    assert result["active_pool_ever_used_percentage"] is None
    assert result["most_frequently_used"] == []
    assert "none observed" in render(snapshot, history(tmp_path, []))


def test_one_image_once_and_current_cycle_agreement(tmp_path):
    base, names = pool(tmp_path, 3)
    rates = history(tmp_path, [post(datetime(2026, 7, 9), "one", names[0])])
    snapshot = digest.generated_pool_health_snapshot(base)
    result = digest.generated_image_utilisation(snapshot, rates)
    assert result["active_images_used_ever"] == 1
    assert result["active_images_never_used"] == 2
    assert result["active_pool_ever_used_percentage"] == pytest.approx(100 / 3)
    assert result["active_images_used_in_current_cycle"] == snapshot["active_previously_used"] == 1
    assert result["active_images_unused_in_current_cycle"] == snapshot["active_never_used"] == 2


def test_repeated_use_concentration_and_median(tmp_path):
    base, names = pool(tmp_path, 3)
    rows = [post(datetime(2026, 7, 8, hour), str(hour), names[0]) for hour in (1, 2, 3)]
    rows.append(post(datetime(2026, 7, 9, 1), "four", names[1]))
    result = digest.generated_image_utilisation(digest.generated_pool_health_snapshot(base), history(tmp_path, rows))
    assert result["total_successful_generated_posts_observed"] == 4
    assert result["median_successful_posts_per_used_image"] == 2
    assert result["maximum_successful_posts_for_one_image"] == 3
    assert result["top_10_share_of_successful_generated_posts"] == 100
    assert result["most_frequently_used"][0]["image"] == names[0]


def test_ranking_ties_are_recent_then_basename(tmp_path):
    base, names = pool(tmp_path, 3)
    rows = [post(datetime(2026, 7, 8), "a", names[0]), post(datetime(2026, 7, 9), "b", names[1]), post(datetime(2026, 7, 9), "c", names[2])]
    result = digest.generated_image_utilisation(digest.generated_pool_health_snapshot(base), history(tmp_path, rows))
    assert [item["image"] for item in result["most_frequently_used"]] == [names[1], names[2], names[0]]


def test_active_and_quarantined_are_separate(tmp_path):
    base, names = pool(tmp_path, 3)
    quarantine(base, [names[0]])
    rates = history(tmp_path, [post(datetime(2026, 7, 8), "q", names[0]), post(datetime(2026, 7, 9), "a", names[1])])
    result = digest.generated_image_utilisation(digest.generated_pool_health_snapshot(base), rates)
    assert result["active_generated_images"] == 2
    assert result["quarantined_generated_images"] == 1
    assert result["total_successful_generated_posts_observed"] == 1
    assert names[0] not in {item["image"] for item in result["most_frequently_used"]}


def test_never_used_and_unused_longest_order(tmp_path):
    base, names = pool(tmp_path, 4)
    rows = [post(datetime(2026, 7, 8), "old", names[0]), post(datetime(2026, 7, 9), "new", names[1])]
    result = digest.generated_image_utilisation(digest.generated_pool_health_snapshot(base), history(tmp_path, rows))
    assert [item["image"] for item in result["never_used"]] == names[2:]
    assert [item["image"] for item in result["unused_longest"]] == [names[2], names[3], names[0], names[1]]


def test_duplicate_rotated_records_and_bounded_coverage(tmp_path):
    base, names = pool(tmp_path, 2)
    duplicate = post(datetime(2026, 7, 9), "same", names[0])
    rates = history(tmp_path, [duplicate], [duplicate, post(datetime(2026, 5, 1), "too-old", names[1])])
    result = digest.generated_image_utilisation(digest.generated_pool_health_snapshot(base), rates)
    assert result["total_successful_generated_posts_observed"] == 1
    assert result["history_coverage_start"].startswith("2026-07-09")
    assert "bounded available" in result["history_scope"]


def test_missing_optional_origin_metadata_is_nonfatal(tmp_path):
    base, names = pool(tmp_path, 1)
    snapshot = digest.generated_pool_health_snapshot(base)
    snapshot.pop("active_origin_quote_hashes")
    result = digest.generated_image_utilisation(snapshot, history(tmp_path, []))
    assert result["never_used"] == [{"image": names[0], "origin_quote_hash": None}]
    assert "unavailable" in render(snapshot, history(tmp_path, []))
