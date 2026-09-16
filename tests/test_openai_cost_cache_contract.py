"""Keep collector and digest monetary validation aligned without merging policy."""

from __future__ import annotations

import copy
from datetime import timedelta
import json
from pathlib import Path

import pytest

import mrs_log_digest_costs as digest_costs
import openai_cost_cache as collector
from tests.helpers.digest_costs import NOW, PROJECT_ID, current_day, day_payload, utc_text


def _cache():
    return {
        "schema_version": 1,
        "source": "openai_organization_costs",
        "currency": "usd",
        "updated_at_utc": utc_text(NOW),
        "scope": collector.cache_scope(PROJECT_ID),
        "days": current_day(),
    }


def _observe(cache):
    before = copy.deepcopy(cache)
    collector_accepts = collector._validate_existing_cache(cache)
    digest_result = digest_costs.load_openai_cost_cache(
        Path("in-memory-cost-cache.json"),
        now_utc=NOW,
        read_bytes=lambda *_args, **_kwargs: json.dumps(cache).encode(),
        parse_json_object=lambda raw, **_kwargs: json.loads(raw),
    )
    assert cache == before
    return collector_accepts, digest_result


@pytest.mark.parametrize("total", ["0", "2.125", "-0.125"])
def test_canonical_amounts_and_credits_are_accepted_by_both_consumers(total):
    cache = _cache()
    cache["days"] = current_day(total=total)
    accepted, result = _observe(cache)
    assert accepted is True and result["available"] is True
    assert result["days"]["2026-08-18"]["primary_total"] == total


@pytest.mark.parametrize("value", ["2.0", "02", "2e0", "-0", "NaN", 2, True])
def test_noncanonical_cached_money_is_rejected_by_both_consumers(value):
    cache = _cache()
    cache["days"]["2026-08-18"]["line_items"]["model_usage"] = value
    accepted, result = _observe(cache)
    assert accepted is False and result["available"] is False
    assert "canonical" in result["reason"]


@pytest.mark.parametrize("field,reason", [
    ("line_items", "breakdown totals disagree"),
    ("organization_line_items", "breakdown totals disagree"),
    ("projects", "project primary total disagrees"),
])
def test_inconsistent_breakdowns_are_rejected_by_both_consumers(field, reason):
    cache = _cache()
    key = PROJECT_ID if field == "projects" else "model_usage"
    cache["days"]["2026-08-18"][field][key] = "999"
    accepted, result = _observe(cache)
    assert accepted is False and result["available"] is False
    assert reason in result["reason"]


def test_organization_scope_requires_its_primary_total_to_match():
    cache = _cache()
    cache["scope"] = collector.cache_scope(None)
    day = cache["days"]["2026-08-18"]
    day["organization_total"] = "3"
    day["organization_line_items"] = {"model_usage": "3"}
    accepted, result = _observe(cache)
    assert accepted is False and result["available"] is False
    assert "organization primary total disagrees" in result["reason"]


@pytest.mark.parametrize("problem,reason", [
    ("noncanonical_update", "canonical UTC timestamp"),
    ("noncanonical_sample", "canonical UTC timestamp"),
    ("sample_after_update", "sample is newer than the cache"),
    ("sample_order", "samples are out of order"),
    ("last_sample_total", "latest sample disagrees"),
])
def test_timestamp_and_sample_contract_is_shared(problem, reason):
    cache = _cache()
    samples = cache["days"]["2026-08-18"]["samples"]
    if problem == "noncanonical_update":
        cache["updated_at_utc"] = "2026-8-18T12:30:00Z"
    elif problem == "noncanonical_sample":
        samples[-1]["fetched_at_utc"] = "2026-8-18T12:30:00Z"
    elif problem == "sample_after_update":
        samples[-1]["fetched_at_utc"] = utc_text(NOW + timedelta(seconds=1))
    elif problem == "sample_order":
        samples[0], samples[1] = samples[1], samples[0]
    else:
        samples[-1]["primary_total"] = "3"
    accepted, result = _observe(cache)
    assert accepted is False and result["available"] is False
    assert reason in result["reason"]


@pytest.mark.parametrize("missing", [True, False])
def test_digest_still_accepts_legacy_missing_organization_breakdown(missing):
    cache = _cache()
    day = cache["days"]["2026-08-18"]
    if missing:
        day.pop("organization_line_items")
    else:
        day["organization_line_items"] = None
    accepted, result = _observe(cache)
    assert accepted is False and result["available"] is True


@pytest.mark.parametrize("policy,reason", [
    ("stale", "cache is stale"),
    ("future", "cache update time is in the future"),
    ("historical_only", "cache is missing the current UTC date"),
])
def test_digest_observation_policy_does_not_prevent_collector_history_merges(policy, reason):
    cache = _cache()
    if policy == "stale":
        updated = NOW - timedelta(hours=3)
        cache["updated_at_utc"] = utc_text(updated)
        cache["days"] = current_day(samples=[(updated, "2")])
    elif policy == "future":
        cache["updated_at_utc"] = utc_text(NOW + timedelta(minutes=6))
    else:
        day = (NOW - timedelta(days=1)).date()
        cache["days"] = {day.isoformat(): day_payload(day, "2", [(NOW, "2")])}
    accepted, result = _observe(cache)
    assert accepted is True and result["available"] is False
    assert reason in result["reason"]


def test_stale_digest_status_still_precedes_body_validation():
    cache = _cache()
    cache.update(updated_at_utc=utc_text(NOW - timedelta(hours=3)), days={})
    _accepted, result = _observe(cache)
    assert result["reason"].startswith("cache is stale")


def test_sample_retention_is_relaxed_only_for_collector_merge():
    cache = _cache()
    day = cache["days"]["2026-08-18"]
    day["samples"] = [dict(day["samples"][-1]) for _ in range(97)]
    accepted, result = _observe(cache)
    assert accepted is False and result["available"] is False
    assert collector._validate_existing_cache(cache, allow_over_retention=True) is True
    day["line_items"]["model_usage"] = "999"
    assert collector._validate_existing_cache(cache, allow_over_retention=True) is False


def test_collector_status_rejects_inconsistent_breakdown(tmp_path):
    cache = _cache()
    cache["days"]["2026-08-18"]["line_items"]["model_usage"] = "999"
    path = tmp_path / "costs.json"
    path.write_text(json.dumps(cache))
    assert collector.cache_status(path, now=NOW)["available"] is False
