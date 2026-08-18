from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import openai_cost_cache as collector
import pytest


NOW = datetime(2026, 8, 18, 12, 30, tzinfo=timezone.utc)


def result(
    value: str,
    *,
    project: str | None = "proj_a",
    line_item: str | None = "model_usage",
    currency: str = "usd",
) -> dict:
    return {
        "object": "organization.costs.result",
        "amount": {"value": Decimal(value), "currency": currency},
        "project_id": project,
        "line_item": line_item,
    }


def bucket(day_offset: int = 0, *rows: dict) -> dict:
    start_dt = collector.utc_midnight((NOW + timedelta(days=day_offset)).date())
    start = int(start_dt.timestamp())
    return {
        "object": "bucket",
        "start_time": start,
        "end_time": start + 86_400,
        "results": list(rows) or [result("0.125")],
    }


def page(*buckets: dict, has_more: bool = False, next_page: str | None = None) -> dict:
    return {
        "object": "page",
        "data": list(buckets),
        "has_more": has_more,
        "next_page": next_page,
    }


def getter_for(*responses: dict):
    calls: list[dict] = []

    def get(**kwargs):
        calls.append(kwargs)
        return responses[len(calls) - 1]

    get.calls = calls
    return get


def update(
    path: Path,
    response: dict,
    *,
    environment: dict[str, str] | None = None,
    now: datetime = NOW,
) -> dict:
    return collector.update_cache(
        path,
        environ=environment or {"OPENAI_ADMIN_API_KEY": "test-admin-key"},
        now=now,
        page_getter=getter_for(response),
    )


def test_missing_key_fails_without_touching_existing_cache(tmp_path: Path) -> None:
    path = tmp_path / "daily.json"
    path.write_bytes(b"existing-cache\n")

    with pytest.raises(collector.CollectionError, match="missing OPENAI_ADMIN_API_KEY"):
        collector.update_cache(path, environ={}, now=NOW)

    assert path.read_bytes() == b"existing-cache\n"


def test_key_is_never_printed_or_copied(tmp_path: Path, monkeypatch, capsys) -> None:
    secret = "admin-key-that-must-not-escape"
    monkeypatch.setenv("OPENAI_ADMIN_API_KEY", secret)
    monkeypatch.setattr(
        collector,
        "_request_costs_page",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError(secret)),
    )

    assert collector.main(["update", "--cache", str(tmp_path / "daily.json")]) == 1
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err
    assert not (tmp_path / "daily.json").exists()


def test_one_daily_bucket_is_cached_with_private_permissions(tmp_path: Path) -> None:
    path = tmp_path / "state" / "daily.json"
    cache = update(path, page(bucket(0, result("0.12345678"))))

    day = cache["days"]["2026-08-18"]
    assert day["primary_total"] == "0.12345678"
    assert day["organization_total"] == "0.12345678"
    assert day["samples"] == [
        {"fetched_at_utc": "2026-08-18T12:30:00Z", "primary_total": "0.12345678"}
    ]
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_multiple_line_items_are_summed_exactly(tmp_path: Path) -> None:
    cache = update(
        tmp_path / "daily.json",
        page(
            bucket(
                0,
                result("0.1", line_item="model_usage"),
                result("0.0025", line_item="storage"),
                result("0.000000000000000001", line_item="model_usage"),
            )
        ),
    )
    day = cache["days"]["2026-08-18"]
    assert day["primary_total"] == "0.102500000000000001"
    assert day["line_items"] == {
        "model_usage": "0.100000000000000001",
        "storage": "0.0025",
    }


def test_multiple_projects_and_configured_project_selection(tmp_path: Path) -> None:
    response = page(
        bucket(
            0,
            result("0.25", project="proj_a", line_item="responses"),
            result("1.75", project="proj_b", line_item="responses"),
            result("0.5", project=None, line_item="shared"),
        )
    )
    cache = update(
        tmp_path / "daily.json",
        response,
        environment={
            "OPENAI_ADMIN_API_KEY": "test-admin-key",
            "OPENAI_COST_PROJECT_ID": "proj_a",
            "OPENAI_PROJECT_ID": "proj_b",
        },
    )
    day = cache["days"]["2026-08-18"]
    assert cache["scope"] == {
        "kind": "project",
        "project_id": "proj_a",
        "description": "configured OpenAI project",
    }
    assert day["primary_total"] == "0.25"
    assert day["organization_total"] == "2.5"
    assert day["projects"] == {"proj_a": "0.25", "proj_b": "1.75"}
    assert day["line_items"] == {"responses": "0.25"}
    assert day["organization_line_items"] == {"responses": "2", "shared": "0.5"}


def test_organization_wide_fallback_does_not_guess_a_project(tmp_path: Path) -> None:
    cache = update(
        tmp_path / "daily.json",
        page(
            bucket(
                0,
                result("0.25", project="proj_small"),
                result("9.75", project="proj_large"),
            )
        ),
    )
    assert cache["scope"]["kind"] == "organization"
    assert "not bot-exclusive" in cache["scope"]["description"]
    assert cache["days"]["2026-08-18"]["primary_total"] == "10"


def test_json_decimal_parser_preserves_provider_precision() -> None:
    parsed = collector.strict_json_loads(
        b'{"amount":{"value":0.123456789012345678901,"currency":"usd"}}',
        label="fixture",
    )
    assert collector.canonical_decimal(parsed["amount"]["value"]) == (
        "0.123456789012345678901"
    )


def test_pagination_follows_next_page_and_stops(tmp_path: Path) -> None:
    getter = getter_for(
        page(bucket(-1), has_more=True, next_page="cursor-2"),
        page(bucket(0), has_more=False),
    )
    cache = collector.update_cache(
        tmp_path / "daily.json",
        environ={"OPENAI_ADMIN_API_KEY": "test-admin-key"},
        now=NOW,
        page_getter=getter,
    )
    assert sorted(cache["days"]) == ["2026-08-17", "2026-08-18"]
    assert [call["page"] for call in getter.calls] == [None, "cursor-2"]


def test_repeated_cursor_is_rejected(tmp_path: Path) -> None:
    getter = getter_for(
        page(bucket(-1), has_more=True, next_page="same"),
        page(bucket(0), has_more=True, next_page="same"),
    )
    with pytest.raises(collector.CollectionError, match="repeated a pagination cursor"):
        collector.update_cache(
            tmp_path / "daily.json",
            environ={"OPENAI_ADMIN_API_KEY": "test-admin-key"},
            now=NOW,
            page_getter=getter,
        )
    assert not (tmp_path / "daily.json").exists()


@pytest.mark.parametrize(
    "response",
    [
        {"object": "page", "data": "bad", "has_more": False, "next_page": None},
        page({"object": "bucket", "start_time": 1, "end_time": 2, "results": []}),
        page(bucket(0, {"object": "wrong"})),
    ],
)
def test_malformed_response_is_rejected(tmp_path: Path, response: dict) -> None:
    with pytest.raises(collector.CollectionError):
        update(tmp_path / "daily.json", response)


def test_mixed_currency_is_rejected(tmp_path: Path) -> None:
    response = page(
        bucket(
            0,
            result("1", currency="usd"),
            result("2", currency="gbp"),
        )
    )
    with pytest.raises(collector.CollectionError, match="mixed currencies"):
        update(tmp_path / "daily.json", response)


def test_api_failure_preserves_old_cache_byte_for_byte(tmp_path: Path) -> None:
    path = tmp_path / "daily.json"
    update(path, page(bucket(0, result("1"))))
    before = path.read_bytes()

    def failure(**_kwargs):
        raise collector.CollectionError("OpenAI Costs API request failed")

    with pytest.raises(collector.CollectionError):
        collector.update_cache(
            path,
            environ={"OPENAI_ADMIN_API_KEY": "test-admin-key"},
            now=NOW + timedelta(minutes=30),
            page_getter=failure,
        )
    assert path.read_bytes() == before


def test_successful_update_atomically_replaces_cache(tmp_path: Path) -> None:
    path = tmp_path / "daily.json"
    update(path, page(bucket(0, result("1"))))
    old_inode = path.stat().st_ino
    update(path, page(bucket(0, result("2"))), now=NOW + timedelta(minutes=30))

    assert path.stat().st_ino != old_inode
    assert json.loads(path.read_text(encoding="utf-8"))["days"]["2026-08-18"][
        "primary_total"
    ] == "2"
    assert not list(tmp_path.glob(".daily.json.*.tmp"))


def test_deterministic_day_and_sample_pruning(tmp_path: Path) -> None:
    path = tmp_path / "daily.json"
    days = {}
    oldest = NOW.date() - timedelta(days=450)
    for offset in range(401):
        day = oldest + timedelta(days=offset)
        start = int(collector.utc_midnight(day).timestamp())
        days[day.isoformat()] = {
            "start_time": start,
            "end_time": start + 86_400,
            "primary_total": "1",
            "organization_total": "1",
            "projects": {"proj_a": "1"},
            "line_items": {"model_usage": "1"},
            "organization_line_items": {"model_usage": "1"},
            "samples": [
                {
                    "fetched_at_utc": collector.utc_text(
                        datetime(2026, 1, 1, tzinfo=timezone.utc)
                        + timedelta(minutes=minute)
                    ),
                    "primary_total": "1",
                }
                for minute in range(99)
            ],
        }
    existing = {
        "schema_version": 1,
        "source": collector.CACHE_SOURCE,
        "currency": "usd",
        "updated_at_utc": "2026-08-18T12:00:00Z",
        "scope": collector.cache_scope(None),
        "days": days,
    }
    path.write_text(json.dumps(existing), encoding="utf-8")

    cache = update(path, page(bucket(0, result("1"))))

    assert len(cache["days"]) == 400
    assert list(cache["days"]) == sorted(cache["days"])
    assert min(cache["days"]) > min(days)
    assert len(cache["days"]["2026-08-18"]["samples"]) <= 96


def test_current_day_appends_a_sample_on_every_successful_run(tmp_path: Path) -> None:
    path = tmp_path / "daily.json"
    response = page(bucket(0, result("1.5")))
    update(path, response)
    cache = update(path, response, now=NOW + timedelta(minutes=30))
    assert cache["days"]["2026-08-18"]["samples"] == [
        {"fetched_at_utc": "2026-08-18T12:30:00Z", "primary_total": "1.5"},
        {"fetched_at_utc": "2026-08-18T13:00:00Z", "primary_total": "1.5"},
    ]


def test_unchanged_historical_total_does_not_accumulate_samples(tmp_path: Path) -> None:
    path = tmp_path / "daily.json"
    response = page(bucket(-1, result("1.5")))
    update(path, response)
    cache = update(path, response, now=NOW + timedelta(minutes=30))
    assert len(cache["days"]["2026-08-17"]["samples"]) == 1


def test_changed_historical_total_is_retained_as_a_new_sample(tmp_path: Path) -> None:
    path = tmp_path / "daily.json"
    update(path, page(bucket(-1, result("1.5"))))
    cache = update(
        path,
        page(bucket(-1, result("1.75"))),
        now=NOW + timedelta(minutes=30),
    )
    assert cache["days"]["2026-08-17"]["samples"] == [
        {"fetched_at_utc": "2026-08-18T12:30:00Z", "primary_total": "1.5"},
        {"fetched_at_utc": "2026-08-18T13:00:00Z", "primary_total": "1.75"},
    ]


def test_status_is_network_free_and_concise(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "daily.json"
    update(path, page(bucket(0, result("0.25"))))
    monkeypatch.setattr(
        collector,
        "_request_costs_page",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("network attempted")),
    )
    status = collector.cache_status(path, now=NOW)
    assert status["available"] is True
    assert status["current_primary_total"] == "0.25"
    assert status["date_start"] == status["date_end"] == "2026-08-18"


def test_status_rejects_a_malformed_monetary_value(tmp_path: Path) -> None:
    path = tmp_path / "daily.json"
    update(path, page(bucket(0, result("0.25"))))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["days"]["2026-08-18"]["primary_total"] = 0.25
    path.write_text(json.dumps(payload), encoding="utf-8")

    status = collector.cache_status(path, now=NOW)
    assert status["available"] is False
    assert status["reason"] == "cache missing or invalid"
