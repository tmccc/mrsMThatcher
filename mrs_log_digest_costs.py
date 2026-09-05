"""Offline published-cost cache validation and UTC-window accounting.

Paths, observation times, the stable file reader and strict JSON parser are
supplied by the caller. Importing this module has no runtime side effects.
Report preparation is independent of Markdown, event aggregation and the bot.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from mrs_log_digest_values import (
    _human_snapshot_age,
    _openai_decimal_text,
    _openai_window_text,
    _parse_openai_cost_decimal,
    _parse_openai_utc,
)


OPENAI_COST_CACHE_SCHEMA_VERSION = 1
OPENAI_COST_CACHE_SOURCE = "openai_organization_costs"
OPENAI_COST_CACHE_MAX_BYTES = 16 * 1024 * 1024
OPENAI_COST_CACHE_STALE_AFTER_SECONDS = 2 * 60 * 60
OPENAI_COST_SAMPLE_BOUNDARY_MAX_GAP_SECONDS = 2 * 60 * 60


def _validate_openai_money_map(value: Any, *, label: str) -> Decimal:
    """Validate one monetary breakdown and return its exact sum."""

    if not isinstance(value, dict):
        raise ValueError(f"{label} is not an object")
    total = Decimal(0)
    for key, amount in value.items():
        if type(key) is not str or not key:
            raise ValueError(f"{label} contains an invalid key")
        total += _parse_openai_cost_decimal(amount, label=f"{label}.{key}")
    return total


def load_openai_cost_cache(
    cache_path: Path,
    *,
    now_utc: datetime,
    read_bytes: Callable[..., bytes],
    parse_json_object: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    """Read and validate an explicit cache at the supplied observation time.

    The caller supplies the bounded stable regular-file reader and strict JSON
    object parser shared with its other snapshots. No default path or clock is
    resolved here; reads occur only through this explicit call.
    """

    path = cache_path
    observed_now = now_utc.astimezone(timezone.utc)

    def unavailable(reason: str) -> Dict[str, Any]:
        return {
            "available": False,
            "path": str(path),
            "reason": reason,
        }

    try:
        raw = read_bytes(path, maximum=OPENAI_COST_CACHE_MAX_BYTES)
    except FileNotFoundError:
        return unavailable("cache file is missing")
    except (OSError, RuntimeError, ValueError) as exc:
        return unavailable(f"cache file is unavailable: {exc}")
    try:
        cache = parse_json_object(raw, label="OpenAI cost cache")
        if cache.get("schema_version") != OPENAI_COST_CACHE_SCHEMA_VERSION:
            raise ValueError("unsupported cache schema")
        if cache.get("source") != OPENAI_COST_CACHE_SOURCE:
            raise ValueError("unexpected cache source")
        if cache.get("currency") != "usd":
            raise ValueError("unsupported or mixed cache currency")
        updated_at = _parse_openai_utc(
            cache.get("updated_at_utc"), label="updated_at_utc"
        )
        if updated_at > observed_now + timedelta(minutes=5):
            raise ValueError("cache update time is in the future")
        age_seconds = max(0, int((observed_now - updated_at).total_seconds()))
        if age_seconds > OPENAI_COST_CACHE_STALE_AFTER_SECONDS:
            return unavailable(
                f"cache is stale ({_human_snapshot_age(age_seconds)} old)"
            )

        scope = cache.get("scope")
        if not isinstance(scope, dict) or scope.get("kind") not in {
            "project",
            "organization",
        }:
            raise ValueError("cache scope is invalid")
        if type(scope.get("description")) is not str or not scope["description"]:
            raise ValueError("cache scope description is invalid")
        if scope["kind"] == "project":
            if type(scope.get("project_id")) is not str or not scope["project_id"]:
                raise ValueError("project-scoped cache has no project ID")
        elif "project_id" in scope:
            raise ValueError("organization-scoped cache contains a project ID")

        days = cache.get("days")
        if not isinstance(days, dict) or not days or len(days) > 400:
            raise ValueError("cache days are missing or exceed the retention bound")
        for day_key, day_value in days.items():
            if type(day_key) is not str or not isinstance(day_value, dict):
                raise ValueError("cache contains an invalid day")
            day_date = datetime.strptime(day_key, "%Y-%m-%d").date()
            expected_start = int(
                datetime.combine(day_date, time.min, tzinfo=timezone.utc).timestamp()
            )
            if (
                type(day_value.get("start_time")) is not int
                or type(day_value.get("end_time")) is not int
                or day_value["start_time"] != expected_start
                or day_value["end_time"] != expected_start + 86_400
            ):
                raise ValueError(f"cache day {day_key} has invalid UTC boundaries")
            primary = _parse_openai_cost_decimal(
                day_value.get("primary_total"), label=f"days.{day_key}.primary_total"
            )
            organization = _parse_openai_cost_decimal(
                day_value.get("organization_total"),
                label=f"days.{day_key}.organization_total",
            )
            _validate_openai_money_map(
                day_value.get("projects"), label=f"days.{day_key}.projects"
            )
            line_total = _validate_openai_money_map(
                day_value.get("line_items"), label=f"days.{day_key}.line_items"
            )
            organization_line_items = day_value.get("organization_line_items")
            organization_line_total = (
                _validate_openai_money_map(
                    organization_line_items,
                    label=f"days.{day_key}.organization_line_items",
                )
                if organization_line_items is not None
                else None
            )
            if line_total != primary or (
                organization_line_total is not None
                and organization_line_total != organization
            ):
                raise ValueError(f"cache day {day_key} breakdown totals disagree")
            if scope["kind"] == "organization" and primary != organization:
                raise ValueError(f"cache day {day_key} organization primary total disagrees")
            if scope["kind"] == "project":
                expected_primary = _parse_openai_cost_decimal(
                    day_value["projects"].get(scope["project_id"], "0"),
                    label=f"days.{day_key}.selected_project",
                )
                if primary != expected_primary:
                    raise ValueError(f"cache day {day_key} project primary total disagrees")

            samples = day_value.get("samples")
            if not isinstance(samples, list) or not samples or len(samples) > 96:
                raise ValueError(f"cache day {day_key} has invalid samples")
            previous_sample_time: Optional[datetime] = None
            for sample in samples:
                if not isinstance(sample, dict):
                    raise ValueError(f"cache day {day_key} contains an invalid sample")
                sample_time = _parse_openai_utc(
                    sample.get("fetched_at_utc"), label="sample fetched_at_utc"
                )
                _parse_openai_cost_decimal(
                    sample.get("primary_total"), label="sample primary_total"
                )
                if sample_time > updated_at:
                    raise ValueError(f"cache day {day_key} sample is newer than the cache")
                if previous_sample_time is not None and sample_time < previous_sample_time:
                    raise ValueError(f"cache day {day_key} samples are out of order")
                previous_sample_time = sample_time
            if samples[-1].get("primary_total") != day_value.get("primary_total"):
                raise ValueError(f"cache day {day_key} latest sample disagrees")

        current_date = observed_now.date().isoformat()
        if current_date not in days:
            raise ValueError("cache is missing the current UTC date")
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        return unavailable(f"cache validation failed: {exc}")

    return {
        "available": True,
        "path": str(path),
        "currency": "usd",
        "updated_at_utc": cache["updated_at_utc"],
        "updated_at_display": updated_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "age_seconds": age_seconds,
        "age": _human_snapshot_age(age_seconds),
        "scope": dict(scope),
        "days": days,
        "current_utc_date": current_date,
    }


def local_digest_time_to_utc(value: datetime) -> datetime:
    """Convert the digest's existing host-local naive time to aware UTC."""

    if value.tzinfo is None:
        return value.astimezone(timezone.utc)
    return value.astimezone(timezone.utc)


def estimate_openai_cost_window(
    cache: Dict[str, Any],
    *,
    window_start_utc: datetime,
    window_end_utc: datetime,
    now_utc: datetime,
) -> Dict[str, Any]:
    """Estimate a UTC window from cumulative samples without interpolation."""

    start = window_start_utc.astimezone(timezone.utc)
    end = window_end_utc.astimezone(timezone.utc)
    result: Dict[str, Any] = {
        "status": "unknown",
        "method": "cumulative provider-published cost delta",
        "requested_start_utc": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "requested_end_utc": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "requested_window": _openai_window_text(start, end),
        "segments": [],
        "reason": None,
    }
    if end <= start:
        result["reason"] = "selected log window has no positive duration"
        return result

    cursor = start
    total = Decimal(0)
    valid_count = 0
    while cursor < end:
        next_midnight = datetime.combine(
            cursor.date() + timedelta(days=1), time.min, tzinfo=timezone.utc
        )
        segment_end = min(end, next_midnight)
        day_key = cursor.date().isoformat()
        segment: Dict[str, Any] = {
            "utc_date": day_key,
            "requested_start_utc": cursor.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "requested_end_utc": segment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "unavailable",
        }
        day = cache.get("days", {}).get(day_key)
        if not isinstance(day, dict):
            segment["reason"] = "required UTC date is missing from the cache"
            result["segments"].append(segment)
            cursor = segment_end
            continue

        day_start = datetime.combine(cursor.date(), time.min, tzinfo=timezone.utc)
        is_complete_closed_day = (
            cursor == day_start
            and segment_end == next_midnight
            and cursor.date() < now_utc.astimezone(timezone.utc).date()
        )
        if is_complete_closed_day:
            amount = _parse_openai_cost_decimal(
                day["primary_total"], label=f"days.{day_key}.primary_total"
            )
            segment.update(
                {
                    "status": "complete",
                    "method": "latest provider-published daily total",
                    "amount": _openai_decimal_text(amount),
                    "sample_start_utc": cursor.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "sample_end_utc": segment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            )
            total += amount
            valid_count += 1
            result["segments"].append(segment)
            cursor = segment_end
            continue

        samples: list[tuple[datetime, Decimal]] = [
            (
                _parse_openai_utc(sample["fetched_at_utc"], label="sample time"),
                _parse_openai_cost_decimal(
                    sample["primary_total"], label="sample primary_total"
                ),
            )
            for sample in day.get("samples", [])
        ]
        before = [item for item in samples if item[0] <= cursor]
        after = [item for item in samples if item[0] >= segment_end]
        if not before:
            segment["reason"] = "no cumulative sample at or before segment start"
        else:
            start_sample = max(before, key=lambda item: item[0])
            start_gap = (cursor - start_sample[0]).total_seconds()
            if start_gap > OPENAI_COST_SAMPLE_BOUNDARY_MAX_GAP_SECONDS:
                segment["reason"] = "start boundary sample is too old"
            elif after:
                end_sample = min(after, key=lambda item: item[0])
                end_gap = (end_sample[0] - segment_end).total_seconds()
                if end_gap > OPENAI_COST_SAMPLE_BOUNDARY_MAX_GAP_SECONDS:
                    segment["reason"] = "end boundary sample is too late"
                elif end_sample[1] < start_sample[1]:
                    segment["reason"] = (
                        "provider-published cumulative total changed non-monotonically"
                    )
                else:
                    amount = end_sample[1] - start_sample[1]
                    segment.update(
                        {
                            "status": "complete",
                            "method": result["method"],
                            "amount": _openai_decimal_text(amount),
                            "sample_start_utc": start_sample[0].strftime(
                                "%Y-%m-%dT%H:%M:%SZ"
                            ),
                            "sample_end_utc": end_sample[0].strftime(
                                "%Y-%m-%dT%H:%M:%SZ"
                            ),
                        }
                    )
                    total += amount
                    valid_count += 1
            elif segment_end == end:
                latest_sample = max(
                    (item for item in samples if item[0] <= segment_end),
                    key=lambda item: item[0],
                )
                trailing_gap = (segment_end - latest_sample[0]).total_seconds()
                if (
                    latest_sample[0] <= start_sample[0]
                    or latest_sample[0] <= cursor
                ):
                    segment["reason"] = (
                        "no later cumulative sample before segment end"
                    )
                elif trailing_gap > OPENAI_COST_SAMPLE_BOUNDARY_MAX_GAP_SECONDS:
                    segment["reason"] = (
                        "latest cumulative sample is too old for trailing coverage"
                    )
                elif latest_sample[1] < start_sample[1]:
                    segment["reason"] = (
                        "provider-published cumulative total changed non-monotonically"
                    )
                else:
                    amount = latest_sample[1] - start_sample[1]
                    partial_reason = (
                        "requested segment ends after the latest cumulative sample"
                    )
                    segment.update(
                        {
                            "status": "partial",
                            "method": result["method"],
                            "amount": _openai_decimal_text(amount),
                            "sample_start_utc": start_sample[0].strftime(
                                "%Y-%m-%dT%H:%M:%SZ"
                            ),
                            "sample_end_utc": latest_sample[0].strftime(
                                "%Y-%m-%dT%H:%M:%SZ"
                            ),
                            "trailing_uncovered_seconds": int(trailing_gap),
                            "partial_coverage_reason": partial_reason,
                            "reason": partial_reason,
                        }
                    )
                    total += amount
                    valid_count += 1
            else:
                segment["reason"] = "no cumulative sample at or after segment end"
        result["segments"].append(segment)
        cursor = segment_end

    if all(item.get("status") == "complete" for item in result["segments"]):
        result["status"] = "complete"
        result["amount"] = _openai_decimal_text(total)
    elif valid_count:
        result["status"] = "partial"
        result["amount"] = _openai_decimal_text(total)
        result["reason"] = "one or more UTC-date segments lack boundary coverage"
    else:
        result["reason"] = "no UTC-date segment has suitable boundary coverage"
    return result


def prepare_openai_published_cost_report(
    cache: Dict[str, Any],
    *,
    window_start_local: Optional[datetime],
    window_end_local: Optional[datetime],
    now_utc: datetime,
) -> Dict[str, Any]:
    """Prepare current-day and window costs from a validated cache observation.

    This does not read files or observe the clock. Naive window boundaries use
    the host-local conversion retained by the digest.
    """

    report: Dict[str, Any] = {
        key: value for key, value in cache.items() if key != "days"
    }
    report["current_day"] = {"available": False}
    report["selected_window"] = {
        "status": "unknown",
        "reason": "OpenAI published-cost cache is unavailable",
    }
    if not cache.get("available"):
        return report

    current = cache["days"][cache["current_utc_date"]]
    report["current_day"] = {
        "available": True,
        "utc_date": cache["current_utc_date"],
        "primary_total": current["primary_total"],
        "provisional": True,
        "status": "provisional",
    }
    if window_start_local is not None and window_end_local is not None:
        report["selected_window"] = estimate_openai_cost_window(
            cache,
            window_start_utc=local_digest_time_to_utc(window_start_local),
            window_end_utc=local_digest_time_to_utc(window_end_local),
            now_utc=now_utc,
        )
    else:
        report["selected_window"] = {
            "status": "unknown",
            "reason": "selected log window boundaries are unavailable",
        }

    return report
