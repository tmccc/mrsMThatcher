from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import os
import time as time_module

import mrs_log_digest as digest

from tests.helpers.digest_costs import (
    NOW,
    PROJECT_ID,
    cost_report,
    current_day,
    day_payload,
    render_cost,
    write_cache,
)


def test_missing_cache_is_unknown_and_never_zero(tmp_path: Path) -> None:
    report = cost_report(tmp_path / "missing.json")
    rendered = render_cost(report)
    assert report["available"] is False
    assert "OpenAI published cost: unknown" in rendered
    assert "US$0" not in rendered


def test_malformed_and_wrong_schema_caches_are_unavailable(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{broken", encoding="utf-8")
    assert digest.load_openai_cost_cache(malformed, now_utc=NOW)["available"] is False

    wrong_schema = write_cache(
        tmp_path / "wrong-schema.json", current_day(), schema_version=2
    )
    result = digest.load_openai_cost_cache(wrong_schema, now_utc=NOW)
    assert result["available"] is False
    assert "schema" in result["reason"]


def test_symlinked_cache_is_unavailable(tmp_path: Path) -> None:
    target = write_cache(tmp_path / "target.json", current_day())
    link = tmp_path / "link.json"
    link.symlink_to(target)
    result = digest.load_openai_cost_cache(link, now_utc=NOW)
    assert result["available"] is False
    assert "regular file" in result["reason"]


def test_stale_cache_is_unavailable(tmp_path: Path) -> None:
    path = write_cache(
        tmp_path / "daily.json",
        current_day(),
        updated=NOW - timedelta(hours=2, seconds=1),
    )
    result = digest.load_openai_cost_cache(path, now_utc=NOW)
    assert result["available"] is False
    assert "stale" in result["reason"]


def test_mixed_or_unsupported_currency_is_unavailable(tmp_path: Path) -> None:
    path = write_cache(tmp_path / "daily.json", current_day(), currency="gbp")
    result = digest.load_openai_cost_cache(path, now_utc=NOW)
    assert result["available"] is False
    assert "currency" in result["reason"]


def test_organization_wide_labelling_is_honest_and_not_combined(tmp_path: Path) -> None:
    path = write_cache(
        tmp_path / "daily.json",
        current_day(scope_kind="organization"),
        scope_kind="organization",
    )
    report = cost_report(path)
    rendered = render_cost(report)
    assert report["scope"]["kind"] == "organization"
    assert "organisation-wide (not bot-exclusive)" in rendered
    assert "xAI" not in rendered
    assert "Combined selected-window estimate:" not in rendered


def test_project_scoped_labelling_and_current_day_total(tmp_path: Path) -> None:
    path = write_cache(tmp_path / "daily.json", current_day(total="2.125"))
    report = cost_report(path)
    rendered = render_cost(report)
    assert report["scope"]["project_id"] == PROJECT_ID
    assert report["current_day"] == {
        "available": True,
        "utc_date": "2026-08-18",
        "primary_total": "2.125",
        "provisional": True,
        "status": "provisional",
    }
    assert f"project {PROJECT_ID}" in rendered
    assert "Current UTC-day provider-published total: **US$2.125**" in rendered
    assert "Status: **provisional**" in rendered
    assert "days" not in report


def test_bracketing_sample_delta_uses_no_interpolation(tmp_path: Path) -> None:
    path = write_cache(tmp_path / "daily.json", current_day())
    selected = cost_report(path)["selected_window"]
    assert selected["status"] == "complete"
    assert selected["amount"] == "0.75"
    assert selected["requested_window"] == "08:05–10:25 UTC"
    assert selected["segments"][0]["sample_start_utc"] == "2026-08-18T08:00:00Z"
    assert selected["segments"][0]["sample_end_utc"] == "2026-08-18T10:30:00Z"
    assert "trailing_uncovered_seconds" not in selected["segments"][0]


def test_incomplete_trailing_coverage_uses_latest_sample(tmp_path: Path) -> None:
    samples = [
        (datetime(2026, 8, 18, 0, 0, tzinfo=timezone.utc), "1"),
        (datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc), "1.1968775"),
    ]
    path = write_cache(
        tmp_path / "daily.json", current_day(total="1.1968775", samples=samples)
    )
    selected = cost_report(
        path,
        start=datetime(2026, 8, 18, 0, 9, tzinfo=timezone.utc),
        end=datetime(2026, 8, 18, 4, 11, tzinfo=timezone.utc),
    )["selected_window"]
    segment = selected["segments"][0]
    assert selected["status"] == "partial"
    assert selected["amount"] == "0.1968775"
    assert segment["status"] == "partial"
    assert segment["requested_start_utc"] == "2026-08-18T00:09:00Z"
    assert segment["requested_end_utc"] == "2026-08-18T04:11:00Z"
    assert segment["sample_start_utc"] == "2026-08-18T00:00:00Z"
    assert segment["sample_end_utc"] == "2026-08-18T04:00:00Z"
    assert segment["trailing_uncovered_seconds"] == 660
    assert "latest cumulative sample" in segment["partial_coverage_reason"]


def test_trailing_partial_coverage_is_explicit_in_markdown(tmp_path: Path) -> None:
    samples = [
        (datetime(2026, 8, 18, 0, 0, tzinfo=timezone.utc), "1"),
        (datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc), "1.1968775"),
    ]
    path = write_cache(
        tmp_path / "daily.json", current_day(total="1.1968775", samples=samples)
    )
    report = cost_report(
        path,
        start=datetime(2026, 8, 18, 0, 9, tzinfo=timezone.utc),
        end=datetime(2026, 8, 18, 4, 11, tzinfo=timezone.utc),
    )
    rendered = render_cost(report)
    assert (
        "OpenAI selected-window estimate (partial coverage): **US$0.1968775**."
        in rendered
    )
    assert "Sample coverage: **2026-08-18: 00:00–04:00 UTC**." in rendered
    assert (
        "Requested coverage represented: approximately "
        "**2026-08-18: 00:09–04:00 UTC**."
    ) in rendered
    assert "Trailing period unavailable: **11 minutes**." in rendered
    assert "samples are collected at intervals" in rendered


def test_no_start_sample_does_not_extrapolate(tmp_path: Path) -> None:
    samples = [
        (datetime(2026, 8, 18, 8, 30, tzinfo=timezone.utc), "1"),
        (datetime(2026, 8, 18, 10, 30, tzinfo=timezone.utc), "1.5"),
    ]
    path = write_cache(
        tmp_path / "daily.json", current_day(total="1.5", samples=samples)
    )
    selected = cost_report(path)["selected_window"]
    assert selected["status"] == "unknown"
    assert "at or before segment start" in selected["segments"][0]["reason"]


def test_start_sample_without_later_sample_remains_unknown(tmp_path: Path) -> None:
    samples = [
        (datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc), "1"),
    ]
    path = write_cache(
        tmp_path / "daily.json", current_day(total="1", samples=samples)
    )
    selected = cost_report(path)["selected_window"]
    assert selected["status"] == "unknown"
    assert "no later cumulative sample" in selected["segments"][0]["reason"]
    assert "amount" not in selected


def test_latest_sample_too_stale_for_trailing_coverage_is_unknown(
    tmp_path: Path,
) -> None:
    latest = datetime(2026, 8, 18, 8, 15, tzinfo=timezone.utc)
    samples = [
        (datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc), "1"),
        (latest, "1.25"),
    ]
    path = write_cache(
        tmp_path / "daily.json", current_day(total="1.25", samples=samples)
    )
    selected = cost_report(
        path,
        start=datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc),
        end=latest
        + timedelta(
            seconds=digest.OPENAI_COST_SAMPLE_BOUNDARY_MAX_GAP_SECONDS + 1
        ),
    )["selected_window"]
    assert selected["status"] == "unknown"
    assert "too old for trailing coverage" in selected["segments"][0]["reason"]
    assert "amount" not in selected


def test_window_crossing_utc_midnight_is_split_and_summed(tmp_path: Path) -> None:
    day_17_samples = [
        (datetime(2026, 8, 17, 22, 30, tzinfo=timezone.utc), "1"),
        (datetime(2026, 8, 18, 0, 0, tzinfo=timezone.utc), "1.2"),
    ]
    day_18_samples = [
        (datetime(2026, 8, 18, 0, 0, tzinfo=timezone.utc), "0"),
        (datetime(2026, 8, 18, 1, 0, tzinfo=timezone.utc), "0.3"),
        (NOW, "2"),
    ]
    days = {
        "2026-08-17": day_payload(date(2026, 8, 17), "1.2", day_17_samples),
        "2026-08-18": day_payload(date(2026, 8, 18), "2", day_18_samples),
    }
    path = write_cache(tmp_path / "daily.json", days)
    selected = cost_report(
        path,
        start=datetime(2026, 8, 17, 23, 0, tzinfo=timezone.utc),
        end=datetime(2026, 8, 18, 1, 0, tzinfo=timezone.utc),
    )["selected_window"]
    assert selected["status"] == "complete"
    assert selected["amount"] == "0.5"
    assert [item["utc_date"] for item in selected["segments"]] == [
        "2026-08-17",
        "2026-08-18",
    ]


def test_europe_london_bst_local_window_converts_to_utc(monkeypatch) -> None:
    original_tz = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "Europe/London")
    time_module.tzset()
    try:
        assert digest.local_digest_time_to_utc(
            datetime(2026, 8, 18, 9, 5)
        ) == datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
        assert digest.local_digest_time_to_utc(
            datetime(2026, 1, 18, 9, 5)
        ) == datetime(2026, 1, 18, 9, 5, tzinfo=timezone.utc)
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time_module.tzset()


def test_non_monotonic_trailing_total_makes_segment_unavailable(
    tmp_path: Path,
) -> None:
    samples = [
        (datetime(2026, 8, 18, 0, 0, tzinfo=timezone.utc), "1"),
        (datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc), "0.5"),
    ]
    path = write_cache(
        tmp_path / "daily.json", current_day(total="0.5", samples=samples)
    )
    selected = cost_report(
        path,
        start=datetime(2026, 8, 18, 0, 9, tzinfo=timezone.utc),
        end=datetime(2026, 8, 18, 4, 11, tzinfo=timezone.utc),
    )["selected_window"]
    assert selected["status"] == "unknown"
    assert "non-monotonically" in selected["segments"][0]["reason"]
    assert "amount" not in selected["segments"][0]
    assert "amount" not in selected


def test_partial_coverage_reports_only_valid_segments(tmp_path: Path) -> None:
    day_17_samples = [
        (datetime(2026, 8, 17, 22, 30, tzinfo=timezone.utc), "1"),
        (datetime(2026, 8, 18, 0, 0, tzinfo=timezone.utc), "1.2"),
    ]
    day_18_samples = [(NOW, "2")]
    days = {
        "2026-08-17": day_payload(date(2026, 8, 17), "1.2", day_17_samples),
        "2026-08-18": day_payload(date(2026, 8, 18), "2", day_18_samples),
    }
    path = write_cache(tmp_path / "daily.json", days)
    selected = cost_report(
        path,
        start=datetime(2026, 8, 17, 23, 0, tzinfo=timezone.utc),
        end=datetime(2026, 8, 18, 1, 0, tzinfo=timezone.utc),
    )["selected_window"]
    assert selected["status"] == "partial"
    assert selected["amount"] == "0.2"
    assert selected["segments"][1]["status"] == "unavailable"


def test_utc_midnight_window_sums_complete_and_trailing_partial_segments(
    tmp_path: Path,
) -> None:
    day_17_samples = [
        (datetime(2026, 8, 17, 22, 30, tzinfo=timezone.utc), "1"),
        (datetime(2026, 8, 18, 0, 0, tzinfo=timezone.utc), "1.2"),
    ]
    day_18_samples = [
        (datetime(2026, 8, 18, 0, 0, tzinfo=timezone.utc), "0"),
        (datetime(2026, 8, 18, 1, 0, tzinfo=timezone.utc), "0.3"),
    ]
    days = {
        "2026-08-17": day_payload(date(2026, 8, 17), "1.2", day_17_samples),
        "2026-08-18": day_payload(date(2026, 8, 18), "0.3", day_18_samples),
    }
    path = write_cache(tmp_path / "daily.json", days)
    selected = cost_report(
        path,
        start=datetime(2026, 8, 17, 23, 0, tzinfo=timezone.utc),
        end=datetime(2026, 8, 18, 1, 11, tzinfo=timezone.utc),
    )["selected_window"]
    assert selected["status"] == "partial"
    assert selected["amount"] == "0.5"
    assert [segment["status"] for segment in selected["segments"]] == [
        "complete",
        "partial",
    ]
    assert selected["segments"][1]["trailing_uncovered_seconds"] == 660


def test_complete_closed_utc_day_uses_published_daily_total(tmp_path: Path) -> None:
    past = day_payload(
        date(2026, 8, 17),
        "3.25",
        [(datetime(2026, 8, 18, 0, 30, tzinfo=timezone.utc), "3.25")],
    )
    days = {"2026-08-17": past, **current_day()}
    path = write_cache(tmp_path / "daily.json", days)
    selected = cost_report(
        path,
        start=datetime(2026, 8, 17, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 8, 18, 0, 0, tzinfo=timezone.utc),
    )["selected_window"]
    assert selected["status"] == "complete"
    assert selected["amount"] == "3.25"
    assert selected["segments"][0]["method"] == "latest provider-published daily total"


def test_legacy_provider_usage_is_not_combined_for_any_scope(tmp_path: Path) -> None:
    usage = {
        "events": [
            {
                "provider": "xAI",
                "cost_in_usd_ticks": 1_000_000_000,
            }
        ]
    }
    project_path = write_cache(tmp_path / "project.json", current_day())
    project_report = cost_report(project_path, provider_usage=usage)
    assert project_report["selected_window"]["amount"] == "0.75"
    assert "combined_selected_window" not in project_report
    assert "xai_component" not in project_report
    project_rendered = render_cost(project_report)
    assert "OpenAI selected-window estimate: **US$0.75**" in project_rendered
    assert "xAI" not in project_rendered
    assert "Combined selected-window estimate:" not in project_rendered

    organization_path = write_cache(
        tmp_path / "organization.json",
        current_day(scope_kind="organization"),
        scope_kind="organization",
    )
    organization_report = cost_report(organization_path, provider_usage=usage)
    assert "combined_selected_window" not in organization_report
    assert "xai_component" not in organization_report
    assert "xAI" not in render_cost(organization_report)
    assert "Combined selected-window estimate:" not in render_cost(organization_report)


def test_organization_wide_partial_cost_remains_openai_only(
    tmp_path: Path,
) -> None:
    usage = {
        "events": [
            {
                "provider": "xAI",
                "cost_in_usd_ticks": 1_000_000_000,
            }
        ]
    }
    samples = [
        (datetime(2026, 8, 18, 0, 0, tzinfo=timezone.utc), "1"),
        (datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc), "1.1968775"),
    ]
    path = write_cache(
        tmp_path / "organization-partial.json",
        current_day(
            total="1.1968775", samples=samples, scope_kind="organization"
        ),
        scope_kind="organization",
    )
    report = cost_report(
        path,
        start=datetime(2026, 8, 18, 0, 9, tzinfo=timezone.utc),
        end=datetime(2026, 8, 18, 4, 11, tzinfo=timezone.utc),
        provider_usage=usage,
    )
    rendered = render_cost(report)
    assert report["selected_window"]["status"] == "partial"
    assert report["selected_window"]["amount"] == "0.1968775"
    assert "combined_selected_window" not in report
    assert "OpenAI organisation-wide selected-window estimate (partial coverage)" in rendered
    assert "xAI" not in rendered
    assert "Combined selected-window estimate:" not in rendered


def test_project_partial_cost_remains_openai_only(tmp_path: Path) -> None:
    usage = {
        "events": [
            {
                "provider": "xAI",
                "cost_in_usd_ticks": 1_000_000_000,
            }
        ]
    }
    samples = [
        (datetime(2026, 8, 18, 0, 0, tzinfo=timezone.utc), "1"),
        (datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc), "1.1968775"),
    ]
    path = write_cache(
        tmp_path / "project-partial.json",
        current_day(total="1.1968775", samples=samples),
    )
    report = cost_report(
        path,
        start=datetime(2026, 8, 18, 0, 9, tzinfo=timezone.utc),
        end=datetime(2026, 8, 18, 4, 11, tzinfo=timezone.utc),
        provider_usage=usage,
    )
    rendered = render_cost(report)
    assert report["selected_window"]["status"] == "partial"
    assert "combined_selected_window" not in report
    assert "xai_component" not in report
    assert "OpenAI selected-window estimate (partial coverage)" in rendered
    assert "Combined selected-window estimate:" not in rendered
    assert "xAI" not in rendered


def test_digest_path_override_uses_monkeypatched_fixture_not_environment(
    tmp_path: Path, monkeypatch
) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return NOW.astimezone().replace(tzinfo=None)
            return NOW.astimezone(tz)

    monkeypatch.setattr(digest, "datetime", FixedDateTime)
    cache_path = write_cache(tmp_path / "daily.json", current_day())
    monkeypatch.setattr(digest, "OPENAI_COST_CACHE_PATH", cache_path)
    monkeypatch.setenv("OPENAI_COST_CACHE", str(tmp_path / "must-not-be-read.json"))
    log = tmp_path / "bot.log"
    log.write_text(
        "2026-08-18 09:05:00 INFO fixture:1 - Main loop tick\n"
        "2026-08-18 11:25:00 INFO fixture:2 - Main loop tick\n",
        encoding="utf-8",
    )
    output = tmp_path / "digest.md"

    assert digest.main(
        [
            "--project-dir",
            str(tmp_path),
            "--no-state",
            "--output",
            str(output),
            str(log),
        ]
    ) == 0
    rendered = output.read_text(encoding="utf-8")
    assert "Current UTC-day provider-published total: **US$2**" in rendered
    assert str(tmp_path / "must-not-be-read.json") not in rendered
