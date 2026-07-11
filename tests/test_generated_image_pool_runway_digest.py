from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import mrs_log_digest as digest
import pytest
from tests.test_generated_image_pool_health_digest import pool, quarantine, report_text


def log_line(ts: datetime, message: str) -> str:
    return f"{ts.strftime('%Y-%m-%d %H:%M:%S')} INFO     test:1 - {message}\n"


def post(ts: datetime, post_id: str, basename: str) -> str:
    event = {"event": "main_post_posted", "lane": "quote_image", "post_id": post_id, "image_basename": basename}
    return log_line(ts, "EVENT " + json.dumps(event, separators=(",", ":")))


def test_observed_rates_windows_dedup_and_selected_window_independence(tmp_path):
    now = datetime(2026, 7, 10, 12)
    generated = "tg_" + "a" * 64 + ".png"
    current = tmp_path / "bot.log"; rotated = tmp_path / "bot.log.1"
    rows = [post(datetime(2026, 7, 9, 12), "1", generated), post(datetime(2026, 7, 8, 12), "2", "t01.jpg"), post(datetime(2026, 6, 20, 12), "3", generated)]
    current.write_text("".join(rows[:2])); rotated.write_text("".join([rows[0], rows[2]]))
    result = digest.generated_post_rate_history([current, rotated], now)
    assert result["windows"]["trailing_7d"]["regular_posts"] == 2
    assert result["windows"]["trailing_7d"]["generated_posts"] == 1
    assert result["windows"]["trailing_7d"]["generated_share_percent"] == 50
    assert result["windows"]["trailing_30d"]["regular_posts"] == 3
    assert result["unique_regular_posts"] == 3


def test_contaminated_burst_excluded_legitimate_retained(tmp_path):
    now = datetime(2026, 7, 10, 12); generated = "tg_" + "a" * 64 + ".png"; path = tmp_path / "bot.log"
    contaminated = datetime(2026, 7, 9, 10); legitimate = datetime(2026, 7, 9, 11)
    path.write_text(log_line(contaminated, "Loaded files from /tmp/pytest-of-user/pytest-1/test_x") + post(contaminated, "fake", generated) + post(legitimate, "real", generated))
    result = digest.generated_post_rate_history([path], now)
    assert result["unique_regular_posts"] == 1 and result["contaminated_seconds_excluded"] == 1


def config(enabled=True, spacing=2, low=7200, high=9000):
    return {"ENABLE_GENERATED_IMAGE_POOL": enabled, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": spacing, "POST_SLEEP_MIN": low, "POST_SLEEP_MAX": high}


def rates(regular=14, generated=4, coverage=7):
    window = {"regular_posts": regular, "generated_posts": generated, "generated_share_percent": generated / regular * 100 if regular else None,
              "coverage_days": coverage, "regular_posts_per_day": regular / coverage if coverage else None, "generated_posts_per_day": generated / coverage if coverage else None}
    return {"windows": {"trailing_7d": window, "trailing_30d": dict(window)}}


def test_runway_normal_and_schedule_model():
    pool_health = {"active_never_used": 70}; result = digest.generated_pool_runway(pool_health, rates(), config())
    assert result["primary_basis"] == "trailing_7d"
    assert result["observed"]["trailing_7d"]["regular_posts_to_cycle_exhaustion"] == 245
    assert result["observed"]["trailing_7d"]["days_to_cycle_exhaustion"] == 122.5
    assert result["schedule"]["midpoint_regular_interval_seconds"] == 8100
    assert result["schedule"]["maximum_generated_share_percent"] == pytest.approx(100 / 3)
    assert result["schedule"]["regular_posts_to_cycle_exhaustion"] == 210


def test_runway_zero_rates_disabled_complete_and_fallback():
    empty = rates(regular=0, generated=0)
    result = digest.generated_pool_runway({"active_never_used": 10}, empty, config())
    assert result["primary_basis"] == "schedule_model" and not result["observed"]["trailing_7d"]["available"]
    disabled = digest.generated_pool_runway({"active_never_used": 10}, rates(), config(enabled=False))
    assert disabled["primary_basis"] is None and "disabled" in disabled["observed"]["trailing_7d"]["reason"]
    complete = digest.generated_pool_runway({"active_never_used": 0}, empty, config())
    assert complete["primary_basis"] == "complete" and complete["observed"]["trailing_7d"]["days_to_cycle_exhaustion"] == 0
    short7 = rates(); short7["windows"]["trailing_7d"]["coverage_days"] = .5
    assert digest.generated_pool_runway({"active_never_used": 10}, short7, config())["primary_basis"] == "trailing_30d"


def test_malformed_schedule_config_is_unavailable():
    result = digest.generated_pool_runway({"active_never_used": 10}, rates(0, 0), {"POST_SLEEP_MIN": "bad"})
    assert not result["schedule"]["available"] and "malformed" in result["schedule"]["reason"]


def test_runway_config_uses_source_defaults_with_sparse_local_overrides(tmp_path):
    (tmp_path / "mrsMThatcher.local.json").write_text(json.dumps({"ENABLE_GENERATED_IMAGE_POOL": True}))
    resolved = digest.load_runway_config(tmp_path, {})
    assert resolved == {
        "ENABLE_GENERATED_IMAGE_POOL": True,
        "POST_SLEEP_MIN": 7200,
        "POST_SLEEP_MAX": 9000,
        "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": 2,
    }
    result = digest.generated_pool_runway({"active_never_used": 72}, rates(0, 0), resolved)
    assert result["schedule"]["available"] is True
    assert result["schedule"]["days_to_cycle_exhaustion"] == pytest.approx(20.25)


def test_runway_config_rejects_malformed_existing_local_config(tmp_path):
    (tmp_path / "mrsMThatcher.local.json").write_text("{")
    resolved = digest.load_runway_config(tmp_path, {})
    result = digest.generated_pool_runway({"active_never_used": 1}, rates(), resolved)
    assert result["primary_basis"] is None
    assert "cannot read valid local config" in result["schedule"]["reason"]


def test_curation_trends_and_restored_active_reentry(tmp_path):
    base, names = pool(tmp_path); directory = quarantine(base, [names[0], names[1]])
    manifest = json.load(open(directory / "manifest.json")); manifest["created_at"] = "2026-07-09T12:00:00+00:00"; (directory / "manifest.json").write_text(json.dumps(manifest))
    entry = manifest["images"][0]; (directory / "images" / names[0]).rename(base / "generated_review_approved_images" / names[0])
    analysis = json.load(open(base / "generated_image_analysis.json")); audit = json.load(open(base / "generated_image_identity_dependence_audit.json")); analysis["path_index"][names[0]] = entry["sha256"]; analysis["items"][entry["sha256"]] = entry["analysis_record"]; audit["items"][names[0]] = entry["audit_record"]
    (base / "generated_image_analysis.json").write_text(json.dumps(analysis)); (base / "generated_image_identity_dependence_audit.json").write_text(json.dumps(audit))
    restore = base / "generated_image_quarantine" / "transactions" / "restore"; restore.mkdir(parents=True); (restore / "manifest.json").write_text(json.dumps({"transaction_id": "restore", "kind": "restore", "status": "completed", "created_at": "2026-07-10T12:00:00+00:00", "images": [names[0], names[0]]}))
    failed = base / "generated_image_quarantine" / "transactions" / "failed"; failed.mkdir(); (failed / "manifest.json").write_text(json.dumps({"kind": "quarantine", "status": "failed_rolled_back", "created_at": "2026-07-10T12:00:00+00:00", "images": [names[2]]}))
    snapshot = digest.generated_pool_health_snapshot(base, datetime(2026, 7, 10, 13, tzinfo=timezone.utc))
    assert snapshot["active_generated_images"] == 3 and snapshot["quarantined_generated_images"] == 1
    assert snapshot["curation_7d"] == {"quarantined": 2, "restored": 1, "net_active_change": -1}


def test_output_labels_estimates_and_insufficient_data(tmp_path):
    base, _ = pool(tmp_path); snapshot = digest.generated_pool_health_snapshot(base, datetime(2026, 7, 10, tzinfo=timezone.utc))
    report = digest.analyse([]); report["generated_image_pool_health"] = snapshot; report["generated_image_post_rates"] = rates(0, 0)
    report["generated_image_pool_runway"] = digest.generated_pool_runway(snapshot, rates(0, 0), config())
    text = digest.render_markdown(report)
    assert "active_unused_in_current_cycle" in text and "Estimated current-cycle runway" in text and "not an all-time posting claim" in text
    assert "primary_basis" in text and "schedule_model" in text and "Curation trend" in text


def test_history_scan_reads_each_log_once(tmp_path, monkeypatch):
    path = tmp_path / "bot.log"; path.write_text(post(datetime(2026, 7, 9), "1", "t01.jpg")); calls = []
    original = digest.iter_records
    monkeypatch.setattr(digest, "iter_records", lambda value: (calls.append(value) or original(value)))
    digest.generated_post_rate_history([path], datetime(2026, 7, 10))
    assert calls == [path]
