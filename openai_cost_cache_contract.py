"""Pure structure and monetary validation for the published OpenAI cost cache.

The collector and digest share canonical values, scope, daily totals and sample
checks. Callers retain file access, freshness, retention policy and availability
reporting. The digest may read legacy rows lacking organization line items;
the collector requires that breakdown before merging an existing cache.
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from decimal import Decimal
import re
from typing import Any


CACHE_SCHEMA_VERSION = 1
CACHE_SOURCE = "openai_organization_costs"
SUPPORTED_CURRENCY = "usd"
MAX_CACHE_BYTES = 16 * 1024 * 1024
MAX_SAMPLES_PER_DAY = 96
MAX_RETAINED_DAYS = 400


def canonical_decimal(value: Decimal) -> str:
    """Render one finite Decimal without an exponent or redundant zeroes."""

    if not value.is_finite():
        raise ValueError("OpenAI cost is not finite")
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def parse_cache_decimal(value: Any, *, label: str) -> Decimal:
    """Parse one canonical monetary string from the private OpenAI cache."""

    if type(value) is not str or not re.fullmatch(
        r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?", value
    ):
        raise ValueError(f"{label} is not a canonical decimal string")
    parsed = Decimal(value)
    if not parsed.is_finite() or (parsed == 0 and value != "0"):
        raise ValueError(f"{label} is not a canonical finite decimal string")
    return parsed


def parse_cache_utc(value: Any, *, label: str) -> datetime:
    """Parse the cache's canonical whole-second UTC timestamp."""

    if type(value) is not str or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value
    ):
        raise ValueError(f"{label} is not a canonical UTC timestamp")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )


def validate_money_map(value: Any, *, label: str) -> Decimal:
    """Validate one monetary breakdown and return its exact sum."""

    if not isinstance(value, dict):
        raise ValueError(f"{label} is not an object")
    total = Decimal(0)
    for key, amount in value.items():
        if type(key) is not str or not key:
            raise ValueError(f"{label} contains an invalid key")
        total += parse_cache_decimal(amount, label=f"{label}.{key}")
    return total


def validate_cache_header(value: Any) -> datetime:
    """Validate the shared cache header and return its declared update time."""

    if not isinstance(value, dict):
        raise ValueError("cache is not an object")
    if value.get("schema_version") != CACHE_SCHEMA_VERSION:
        raise ValueError("unsupported cache schema")
    if value.get("source") != CACHE_SOURCE:
        raise ValueError("unexpected cache source")
    if value.get("currency") != SUPPORTED_CURRENCY:
        raise ValueError("unsupported or mixed cache currency")
    return parse_cache_utc(value.get("updated_at_utc"), label="updated_at_utc")


def validate_cache_body(
    value: dict[str, Any],
    *,
    updated_at: datetime,
    maximum_days: int | None = MAX_RETAINED_DAYS,
    maximum_samples: int | None = MAX_SAMPLES_PER_DAY,
    require_organization_line_items: bool = False,
) -> None:
    """Validate scope, day boundaries, breakdown totals and cumulative samples.

    Pass no retention limits only when the collector will trim an older cache
    during its merge. This does not relax any structure or monetary checks.
    Validation does not modify the supplied cache or sample order.
    """

    scope = value.get("scope")
    if not isinstance(scope, dict) or scope.get("kind") not in {
        "project", "organization",
    }:
        raise ValueError("cache scope is invalid")
    if type(scope.get("description")) is not str or not scope["description"]:
        raise ValueError("cache scope description is invalid")
    if scope["kind"] == "project":
        if type(scope.get("project_id")) is not str or not scope["project_id"]:
            raise ValueError("project-scoped cache has no project ID")
    elif "project_id" in scope:
        raise ValueError("organization-scoped cache contains a project ID")

    days = value.get("days")
    if (
        not isinstance(days, dict) or not days
        or (maximum_days is not None and len(days) > maximum_days)
    ):
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
        primary = parse_cache_decimal(
            day_value.get("primary_total"), label=f"days.{day_key}.primary_total"
        )
        organization = parse_cache_decimal(
            day_value.get("organization_total"),
            label=f"days.{day_key}.organization_total",
        )
        validate_money_map(day_value.get("projects"), label=f"days.{day_key}.projects")
        line_total = validate_money_map(
            day_value.get("line_items"), label=f"days.{day_key}.line_items"
        )
        organization_line_items = day_value.get("organization_line_items")
        organization_line_total = (
            validate_money_map(
                organization_line_items, label=f"days.{day_key}.organization_line_items"
            )
            if require_organization_line_items or organization_line_items is not None
            else None
        )
        if line_total != primary or (
            organization_line_total is not None and organization_line_total != organization
        ):
            raise ValueError(f"cache day {day_key} breakdown totals disagree")
        if scope["kind"] == "organization" and primary != organization:
            raise ValueError(f"cache day {day_key} organization primary total disagrees")
        if scope["kind"] == "project":
            expected_primary = parse_cache_decimal(
                day_value["projects"].get(scope["project_id"], "0"),
                label=f"days.{day_key}.selected_project",
            )
            if primary != expected_primary:
                raise ValueError(f"cache day {day_key} project primary total disagrees")

        samples = day_value.get("samples")
        if (
            not isinstance(samples, list) or not samples
            or (maximum_samples is not None and len(samples) > maximum_samples)
        ):
            raise ValueError(f"cache day {day_key} has invalid samples")
        previous_sample_time: datetime | None = None
        for sample in samples:
            if not isinstance(sample, dict):
                raise ValueError(f"cache day {day_key} contains an invalid sample")
            sample_time = parse_cache_utc(
                sample.get("fetched_at_utc"), label="sample fetched_at_utc"
            )
            parse_cache_decimal(sample.get("primary_total"), label="sample primary_total")
            if sample_time > updated_at:
                raise ValueError(f"cache day {day_key} sample is newer than the cache")
            if previous_sample_time is not None and sample_time < previous_sample_time:
                raise ValueError(f"cache day {day_key} samples are out of order")
            previous_sample_time = sample_time
        if samples[-1].get("primary_total") != day_value.get("primary_total"):
            raise ValueError(f"cache day {day_key} latest sample disagrees")
