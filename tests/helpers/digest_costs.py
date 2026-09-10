"""Synthetic published-cost cache files and report inputs."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from pathlib import Path
import json

import mrs_log_digest as digest


PROJECT_ID = "proj_digest_fixture"

NOW = datetime(2026, 8, 18, 12, 30, tzinfo=timezone.utc)


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
