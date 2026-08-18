from __future__ import annotations

import json
import os
import time as time_module
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import mrs_log_digest as digest


NOW = datetime(2026, 8, 18, 12, 30, tzinfo=timezone.utc)
PROJECT_ID = "proj_digest_fixture"


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_payload(
    day: date,
    total: str,
    samples: list[tuple[datetime, str]],
    *,
    scope_kind: str = "project",
) -> dict:
    start = int(datetime.combine(day, time.min, tzinfo=timezone.utc).timestamp())
    return {
        "start_time": start,
        "end_time": start + 86_400,
        "primary_total": total,
        "organization_total": total,
        "projects": {PROJECT_ID: total},
        "line_items": {"model_usage": total},
        "organization_line_items": {"model_usage": total},
        "samples": [
            {"fetched_at_utc": utc_text(timestamp), "primary_total": amount}
            for timestamp, amount in samples
        ],
    }


def write_cache(
    path: Path,
    days: dict[str, dict],
    *,
    scope_kind: str = "project",
    updated: datetime = NOW,
    currency: str = "usd",
    schema_version: int = 1,
) -> Path:
    scope = (
        {
            "kind": "project",
            "project_id": PROJECT_ID,
            "description": "configured OpenAI project",
        }
        if scope_kind == "project"
        else {
            "kind": "organization",
            "description": "OpenAI organization-wide; not bot-exclusive",
        }
    )
    payload = {
        "schema_version": schema_version,
        "source": "openai_organization_costs",
        "currency": currency,
        "updated_at_utc": utc_text(updated),
        "scope": scope,
        "days": days,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def current_day(
    *,
    total: str = "2",
    samples: list[tuple[datetime, str]] | None = None,
    scope_kind: str = "project",
) -> dict[str, dict]:
    rows = samples or [
        (datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc), "1"),
        (datetime(2026, 8, 18, 10, 30, tzinfo=timezone.utc), "1.75"),
        (NOW, total),
    ]
    return {
        "2026-08-18": day_payload(
            date(2026, 8, 18), total, rows, scope_kind=scope_kind
        )
    }


def cost_report(
    path: Path,
    *,
    start: datetime | None = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc),
    end: datetime | None = datetime(2026, 8, 18, 10, 25, tzinfo=timezone.utc),
    provider_usage: dict | None = None,
) -> dict:
    return digest.openai_published_cost_report(
        cache_path=path,
        window_start_local=start,
        window_end_local=end,
        generation_time_local=NOW,
        provider_usage=provider_usage or {"events": []},
    )


def render_cost(report: dict) -> str:
    full = digest.analyse([])
    full["project_dir"] = "/fixture"
    full["openai_published_cost"] = report
    return digest.render_markdown(full)


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
    assert "not combined with the bot's xAI component" in rendered
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


def test_incomplete_trailing_coverage_has_no_end_sample(tmp_path: Path) -> None:
    samples = [
        (datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc), "1"),
        (datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc), "1.5"),
    ]
    path = write_cache(
        tmp_path / "daily.json", current_day(total="1.5", samples=samples)
    )
    selected = cost_report(path)["selected_window"]
    assert selected["status"] == "unknown"
    assert "at or after segment end" in selected["segments"][0]["reason"]
    assert "amount" not in selected


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


def test_no_end_sample_does_not_extrapolate(tmp_path: Path) -> None:
    samples = [
        (datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc), "1"),
        (datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc), "1.25"),
    ]
    path = write_cache(
        tmp_path / "daily.json", current_day(total="1.25", samples=samples)
    )
    selected = cost_report(path)["selected_window"]
    assert selected["status"] == "unknown"
    assert "at or after segment end" in selected["segments"][0]["reason"]


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


def test_non_monotonic_total_makes_segment_unavailable(tmp_path: Path) -> None:
    samples = [
        (datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc), "1"),
        (datetime(2026, 8, 18, 10, 30, tzinfo=timezone.utc), "0.5"),
    ]
    path = write_cache(
        tmp_path / "daily.json", current_day(total="0.5", samples=samples)
    )
    selected = cost_report(path)["selected_window"]
    assert selected["status"] == "unknown"
    assert "non-monotonically" in selected["segments"][0]["reason"]
    assert "amount" not in selected["segments"][0]


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


def test_combined_reporting_exists_only_for_project_scope(tmp_path: Path) -> None:
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
    assert project_report["combined_selected_window"] == {
        "available": True,
        "amount": "0.85",
    }
    project_rendered = render_cost(project_report)
    assert "Combined selected-window estimate: **US$0.85**" in project_rendered
    assert "xAI component (provider-reported): **US$0.1**" in project_rendered
    assert "OpenAI component (published-cost delta estimate): **US$0.75**" in project_rendered

    organization_path = write_cache(
        tmp_path / "organization.json",
        current_day(scope_kind="organization"),
        scope_kind="organization",
    )
    organization_report = cost_report(organization_path, provider_usage=usage)
    assert "combined_selected_window" not in organization_report
    assert "Combined selected-window estimate:" not in render_cost(organization_report)


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
