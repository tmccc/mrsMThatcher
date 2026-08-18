#!/usr/bin/env python3
"""Collect provider-published OpenAI organization costs into a private cache."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import stat
import sys
import tempfile
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


COSTS_ENDPOINT = "https://api.openai.com/v1/organization/costs"
DEFAULT_CACHE_PATH = (
    Path.home() / ".local/state/mrsMThatcher/openai-costs/daily_costs.json"
)
CACHE_SCHEMA_VERSION = 1
CACHE_SOURCE = "openai_organization_costs"
SUPPORTED_CURRENCY = "usd"
REFRESH_DAY_COUNT = 7
MAX_PAGES = 64
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_CACHE_BYTES = 16 * 1024 * 1024
MAX_SAMPLES_PER_DAY = 96
MAX_RETAINED_DAYS = 400


class CollectionError(RuntimeError):
    """A safe-to-report collector failure which never contains credentials."""


def absolute_cache_path(path: Path) -> Path:
    """Make a cache path absolute without resolving a possibly symlinked leaf."""

    return Path(os.path.abspath(os.fspath(path.expanduser())))


def utc_text(value: datetime) -> str:
    """Render an aware timestamp in the cache's canonical UTC form."""

    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_midnight(value: date) -> datetime:
    """Return the UTC midnight beginning one date."""

    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def canonical_decimal(value: Decimal) -> str:
    """Return a finite Decimal as a minimal, exponent-free JSON string."""

    if not isinstance(value, Decimal) or not value.is_finite():
        raise CollectionError("Costs API returned a non-finite monetary value")
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def parse_decimal(value: Any, *, label: str) -> Decimal:
    """Validate one exact monetary value parsed from provider JSON."""

    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise CollectionError(f"Costs API {label} is not an exact JSON number")
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CollectionError(f"Costs API {label} is invalid") from exc
    if not result.is_finite():
        raise CollectionError(f"Costs API {label} is not finite")
    return result


def strict_json_loads(data: bytes, *, label: str) -> dict[str, Any]:
    """Parse one finite, duplicate-free JSON object with Decimal floats."""

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite number {value}")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            result[key] = value
        return result

    try:
        parsed = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_float=Decimal,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise CollectionError(f"{label} is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise CollectionError(f"{label} root is not an object")
    return parsed


def configured_project_id(environ: Mapping[str, str]) -> Optional[str]:
    """Resolve the explicit collector scope without guessing a project."""

    for name in ("OPENAI_COST_PROJECT_ID", "OPENAI_PROJECT_ID"):
        value = environ.get(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def cache_scope(project_id: Optional[str]) -> dict[str, Any]:
    """Return the explicit primary scope stored with every cache update."""

    if project_id:
        return {
            "kind": "project",
            "project_id": project_id,
            "description": "configured OpenAI project",
        }
    return {
        "kind": "organization",
        "description": "OpenAI organization-wide; not bot-exclusive",
    }


def _request_costs_page(
    *,
    api_key: str,
    start_time: int,
    end_time: int,
    page: Optional[str],
) -> dict[str, Any]:
    """Make one bounded read-only Costs API request."""

    query: list[tuple[str, str | int]] = [
        ("start_time", start_time),
        ("end_time", end_time),
        ("bucket_width", "1d"),
        ("group_by", "project_id"),
        ("group_by", "line_item"),
        ("limit", REFRESH_DAY_COUNT),
    ]
    if page is not None:
        query.append(("page", page))
    request = Request(
        f"{COSTS_ENDPOINT}?{urlencode(query)}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise CollectionError(f"OpenAI Costs API returned HTTP {exc.code}") from None
    except URLError:
        raise CollectionError("OpenAI Costs API request failed") from None
    except TimeoutError:
        raise CollectionError("OpenAI Costs API request timed out") from None
    if len(payload) > MAX_RESPONSE_BYTES:
        raise CollectionError("OpenAI Costs API response exceeded the size limit")
    return strict_json_loads(payload, label="Costs API response")


PageGetter = Callable[..., dict[str, Any]]


def fetch_cost_buckets(
    *,
    api_key: str,
    start_time: int,
    end_time: int,
    page_getter: Optional[PageGetter] = None,
) -> list[dict[str, Any]]:
    """Fetch and validate all pages of daily Costs API buckets."""

    getter = page_getter or _request_costs_page
    cursor: Optional[str] = None
    seen_cursors: set[str] = set()
    buckets: list[dict[str, Any]] = []
    seen_bucket_starts: set[int] = set()

    for _page_number in range(MAX_PAGES):
        response = getter(
            api_key=api_key,
            start_time=start_time,
            end_time=end_time,
            page=cursor,
        )
        if not isinstance(response, dict):
            raise CollectionError("Costs API response root is not an object")
        if response.get("object") != "page":
            raise CollectionError("Costs API response has an invalid object type")
        data = response.get("data")
        has_more = response.get("has_more")
        if not isinstance(data, list) or type(has_more) is not bool:
            raise CollectionError("Costs API pagination fields are malformed")
        for bucket in data:
            if not isinstance(bucket, dict):
                raise CollectionError("Costs API bucket is not an object")
            bucket_start = bucket.get("start_time")
            if type(bucket_start) is not int:
                raise CollectionError("Costs API bucket start_time is invalid")
            if bucket_start in seen_bucket_starts:
                raise CollectionError("Costs API returned a duplicate daily bucket")
            seen_bucket_starts.add(bucket_start)
            buckets.append(bucket)
        if not has_more:
            break
        next_page = response.get("next_page")
        if not isinstance(next_page, str) or not next_page:
            raise CollectionError("Costs API pagination cursor is malformed")
        if next_page in seen_cursors:
            raise CollectionError("Costs API repeated a pagination cursor")
        seen_cursors.add(next_page)
        cursor = next_page
    else:
        raise CollectionError("Costs API pagination exceeded the page limit")

    if not buckets:
        raise CollectionError("Costs API returned no daily buckets")
    return buckets


def _money_map(values: Mapping[str, Decimal]) -> dict[str, str]:
    """Return a deterministically ordered map of canonical monetary strings."""

    return {
        key: canonical_decimal(values[key])
        for key in sorted(values)
    }


def normalise_cost_buckets(
    buckets: list[dict[str, Any]],
    *,
    query_start: int,
    query_end: int,
    project_id: Optional[str],
) -> tuple[str, dict[str, dict[str, Any]]]:
    """Validate provider buckets and aggregate exact daily cost breakdowns."""

    currencies: set[str] = set()
    days: dict[str, dict[str, Any]] = {}
    for bucket in sorted(buckets, key=lambda item: item.get("start_time", -1)):
        if bucket.get("object") != "bucket":
            raise CollectionError("Costs API bucket has an invalid object type")
        start = bucket.get("start_time")
        end = bucket.get("end_time")
        results = bucket.get("results")
        if type(start) is not int or type(end) is not int or not isinstance(results, list):
            raise CollectionError("Costs API bucket shape is malformed")
        if start < query_start or end > query_end or end - start != 86_400:
            raise CollectionError("Costs API returned a bucket outside the requested UTC dates")
        start_dt = datetime.fromtimestamp(start, timezone.utc)
        if start_dt != utc_midnight(start_dt.date()) or end != start + 86_400:
            raise CollectionError("Costs API returned a non-UTC daily bucket")

        organization_total = Decimal(0)
        project_totals: defaultdict[str, Decimal] = defaultdict(Decimal)
        organization_lines: defaultdict[str, Decimal] = defaultdict(Decimal)
        project_lines: defaultdict[str, Decimal] = defaultdict(Decimal)
        for result in results:
            if not isinstance(result, dict) or result.get("object") != "organization.costs.result":
                raise CollectionError("Costs API result shape is malformed")
            amount = result.get("amount")
            if not isinstance(amount, dict):
                raise CollectionError("Costs API result is missing an amount")
            currency = amount.get("currency")
            if not isinstance(currency, str) or not currency:
                raise CollectionError("Costs API result currency is invalid")
            currencies.add(currency)
            value = parse_decimal(amount.get("value"), label="amount.value")
            result_project = result.get("project_id")
            line_item = result.get("line_item")
            if result_project is not None and (
                not isinstance(result_project, str) or not result_project
            ):
                raise CollectionError("Costs API result project_id is invalid")
            if line_item is not None and (
                not isinstance(line_item, str) or not line_item
            ):
                raise CollectionError("Costs API result line_item is invalid")
            line_key = line_item if line_item is not None else "unattributed"
            organization_total += value
            organization_lines[line_key] += value
            if result_project is not None:
                project_totals[result_project] += value
            if project_id is None or result_project == project_id:
                project_lines[line_key] += value

        primary_total = (
            project_totals.get(project_id, Decimal(0))
            if project_id is not None
            else organization_total
        )
        day_key = start_dt.date().isoformat()
        days[day_key] = {
            "start_time": start,
            "end_time": end,
            "primary_total": canonical_decimal(primary_total),
            "organization_total": canonical_decimal(organization_total),
            "projects": _money_map(project_totals),
            "line_items": _money_map(project_lines),
            "organization_line_items": _money_map(organization_lines),
        }

    if currencies != {SUPPORTED_CURRENCY}:
        if len(currencies) > 1:
            raise CollectionError("Costs API returned mixed currencies")
        raise CollectionError("Costs API returned an unsupported currency")
    return SUPPORTED_CURRENCY, days


def _canonical_cache_decimal(value: Any) -> bool:
    """Return whether a cache value is one canonical finite decimal string."""

    if type(value) is not str:
        return False
    try:
        parsed = Decimal(value)
        return parsed.is_finite() and canonical_decimal(parsed) == value
    except (CollectionError, InvalidOperation, ValueError):
        return False


def _valid_cache_money_map(value: Any) -> bool:
    """Return whether a cached monetary breakdown is structurally valid."""

    return isinstance(value, dict) and all(
        type(key) is str and bool(key) and _canonical_cache_decimal(amount)
        for key, amount in value.items()
    )


def _validate_existing_cache(
    value: dict[str, Any], *, allow_over_retention: bool = False
) -> bool:
    """Return whether an existing cache is safe to report or merge."""

    scope = value.get("scope")
    days = value.get("days")
    if (
        value.get("schema_version") != CACHE_SCHEMA_VERSION
        or value.get("source") != CACHE_SOURCE
        or value.get("currency") != SUPPORTED_CURRENCY
        or not isinstance(scope, dict)
        or scope.get("kind") not in {"project", "organization"}
        or type(scope.get("description")) is not str
        or not scope.get("description")
        or not isinstance(days, dict)
        or not days
        or (not allow_over_retention and len(days) > MAX_RETAINED_DAYS)
    ):
        return False
    if scope["kind"] == "project" and (
        type(scope.get("project_id")) is not str or not scope.get("project_id")
    ):
        return False
    if scope["kind"] == "organization" and "project_id" in scope:
        return False
    try:
        datetime.strptime(value.get("updated_at_utc", ""), "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return False
    for day_key, day_value in days.items():
        if type(day_key) is not str or not isinstance(day_value, dict):
            return False
        try:
            day_date = datetime.strptime(day_key, "%Y-%m-%d").date()
        except ValueError:
            return False
        expected_start = int(utc_midnight(day_date).timestamp())
        if (
            type(day_value.get("start_time")) is not int
            or type(day_value.get("end_time")) is not int
            or day_value["start_time"] != expected_start
            or day_value["end_time"] != expected_start + 86_400
            or not _canonical_cache_decimal(day_value.get("primary_total"))
            or not _canonical_cache_decimal(day_value.get("organization_total"))
            or not _valid_cache_money_map(day_value.get("projects"))
            or not _valid_cache_money_map(day_value.get("line_items"))
            or not _valid_cache_money_map(day_value.get("organization_line_items"))
        ):
            return False
        samples = day_value.get("samples")
        if (
            not isinstance(samples, list)
            or not samples
            or (not allow_over_retention and len(samples) > MAX_SAMPLES_PER_DAY)
        ):
            return False
        previous_time: Optional[datetime] = None
        for sample in samples:
            if not isinstance(sample, dict) or not _canonical_cache_decimal(
                sample.get("primary_total")
            ):
                return False
            try:
                sample_time = datetime.strptime(
                    sample.get("fetched_at_utc", ""), "%Y-%m-%dT%H:%M:%SZ"
                )
            except (TypeError, ValueError):
                return False
            if previous_time is not None and sample_time < previous_time:
                return False
            previous_time = sample_time
        if samples[-1].get("primary_total") != day_value.get("primary_total"):
            return False
    return True


def _read_existing_cache(path: Path) -> Optional[dict[str, Any]]:
    """Read a bounded regular cache without following a final symlink."""

    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode):
        raise CollectionError("existing cache is not a regular file")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise CollectionError("this platform cannot safely open the cache")
    descriptor = os.open(path, os.O_RDONLY | nofollow)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise CollectionError("existing cache changed while opening")
        chunks: list[bytes] = []
        observed = 0
        while observed <= MAX_CACHE_BYTES:
            chunk = os.read(
                descriptor,
                min(64 * 1024, MAX_CACHE_BYTES + 1 - observed),
            )
            if not chunk:
                break
            chunks.append(chunk)
            observed += len(chunk)
        data = b"".join(chunks)
        if len(data) > MAX_CACHE_BYTES or len(data) != opened.st_size:
            raise CollectionError("existing cache exceeds the size limit")
    finally:
        os.close(descriptor)
    try:
        return strict_json_loads(data, label="existing cache")
    except CollectionError:
        return None


def ensure_private_directory(directory: Path) -> None:
    """Create and enforce the collector's private state directory."""

    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = os.lstat(directory)
    if not stat.S_ISDIR(metadata.st_mode):
        raise CollectionError("cache directory is not a regular directory")
    os.chmod(directory, 0o700)


@contextmanager
def cache_lock(cache_path: Path) -> Iterator[None]:
    """Hold one private, exclusive local update lock."""

    ensure_private_directory(cache_path.parent)
    lock_path = cache_path.with_name(f".{cache_path.name}.lock")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise CollectionError("cache lock is not a regular file")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _merge_days(
    old_cache: Optional[dict[str, Any]],
    fresh_days: dict[str, dict[str, Any]],
    *,
    scope: dict[str, Any],
    fetched_at: datetime,
) -> dict[str, dict[str, Any]]:
    """Merge new totals and bounded cumulative samples deterministically."""

    old_days: dict[str, Any] = {}
    if (
        old_cache is not None
        and _validate_existing_cache(old_cache, allow_over_retention=True)
        and old_cache.get("scope") == scope
    ):
        old_days = dict(old_cache["days"])
    merged: dict[str, dict[str, Any]] = {
        key: value for key, value in old_days.items() if isinstance(value, dict)
    }
    today = fetched_at.date().isoformat()
    sample_time = utc_text(fetched_at)
    for day_key in sorted(fresh_days):
        fresh = dict(fresh_days[day_key])
        previous = old_days.get(day_key)
        previous_samples = (
            list(previous.get("samples") or [])
            if isinstance(previous, dict)
            else []
        )
        should_append = (
            day_key == today
            or not isinstance(previous, dict)
            or previous.get("primary_total") != fresh["primary_total"]
        )
        if should_append:
            previous_samples.append(
                {
                    "fetched_at_utc": sample_time,
                    "primary_total": fresh["primary_total"],
                }
            )
        fresh["samples"] = previous_samples[-MAX_SAMPLES_PER_DAY:]
        merged[day_key] = fresh
    for day_key, day_value in list(merged.items()):
        samples = day_value.get("samples")
        if isinstance(samples, list) and len(samples) > MAX_SAMPLES_PER_DAY:
            trimmed = dict(day_value)
            trimmed["samples"] = samples[-MAX_SAMPLES_PER_DAY:]
            merged[day_key] = trimmed
    newest_keys = sorted(merged)[-MAX_RETAINED_DAYS:]
    return {key: merged[key] for key in newest_keys}


def atomic_write_cache(path: Path, value: dict[str, Any]) -> None:
    """Durably publish one private cache without exposing a partial file."""

    payload = (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")
    if len(payload) > MAX_CACHE_BYTES:
        raise CollectionError("new cache exceeds the size limit")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        handle = os.fdopen(descriptor, "wb")
        descriptor = -1
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def update_cache(
    cache_path: Path,
    *,
    environ: Optional[Mapping[str, str]] = None,
    now: Optional[datetime] = None,
    page_getter: Optional[PageGetter] = None,
) -> dict[str, Any]:
    """Collect seven UTC dates and atomically update the private cache."""

    environment = os.environ if environ is None else environ
    api_key = environment.get("OPENAI_ADMIN_API_KEY", "")
    if not api_key or not api_key.strip():
        raise CollectionError("missing OPENAI_ADMIN_API_KEY")
    api_key = api_key.strip()
    fetched_at = now or datetime.now(timezone.utc)
    if fetched_at.tzinfo is None:
        raise CollectionError("collector time must include a timezone")
    fetched_at = fetched_at.astimezone(timezone.utc)
    project_id = configured_project_id(environment)
    scope = cache_scope(project_id)
    first_date = fetched_at.date() - timedelta(days=REFRESH_DAY_COUNT - 1)
    query_start = int(utc_midnight(first_date).timestamp())
    query_end = int(utc_midnight(fetched_at.date() + timedelta(days=1)).timestamp())
    cache_path = absolute_cache_path(cache_path)

    with cache_lock(cache_path):
        old_cache = _read_existing_cache(cache_path)
        buckets = fetch_cost_buckets(
            api_key=api_key,
            start_time=query_start,
            end_time=query_end,
            page_getter=page_getter,
        )
        currency, fresh_days = normalise_cost_buckets(
            buckets,
            query_start=query_start,
            query_end=query_end,
            project_id=project_id,
        )
        cache = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "source": CACHE_SOURCE,
            "currency": currency,
            "updated_at_utc": utc_text(fetched_at),
            "scope": scope,
            "days": _merge_days(
                old_cache,
                fresh_days,
                scope=scope,
                fetched_at=fetched_at,
            ),
        }
        atomic_write_cache(cache_path, cache)
    return cache


def cache_status(cache_path: Path, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """Return concise network-free status for an operator or installer."""

    cache_path = absolute_cache_path(cache_path)
    try:
        cache = _read_existing_cache(cache_path)
    except CollectionError as exc:
        return {"available": False, "path": str(cache_path), "reason": str(exc)}
    if cache is None or not _validate_existing_cache(cache):
        return {
            "available": False,
            "path": str(cache_path),
            "reason": "cache missing or invalid",
        }
    days = cache["days"]
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()
    current_day = days.get(current) if isinstance(days.get(current), dict) else None
    return {
        "available": True,
        "path": str(cache_path),
        "schema_version": cache["schema_version"],
        "currency": cache["currency"],
        "updated_at_utc": cache.get("updated_at_utc"),
        "scope": cache["scope"],
        "date_start": min(days) if days else None,
        "date_end": max(days) if days else None,
        "day_count": len(days),
        "current_utc_date": current,
        "current_primary_total": (
            current_day.get("primary_total") if current_day is not None else None
        ),
        "current_day_provisional": current_day is not None,
    }


def main(argv: Optional[list[str]] = None) -> int:
    """Run the collector command-line interface."""

    parser = argparse.ArgumentParser(
        description="Maintain a private provider-published OpenAI daily cost cache."
    )
    parser.add_argument("command", choices=("update", "status"))
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH)
    args = parser.parse_args(argv)

    if args.command == "status":
        status = cache_status(args.cache)
        print(json.dumps(status, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
        return 0 if status["available"] else 1
    try:
        cache = update_cache(args.cache)
    except CollectionError as exc:
        message = str(exc)
        inherited_key = os.environ.get("OPENAI_ADMIN_API_KEY", "")
        if inherited_key and inherited_key in message:
            message = "collection failed"
        print(f"openai cost cache update failed: {message}", file=sys.stderr)
        return 1
    except Exception:
        # Unknown exceptions are deliberately not rendered: a failing injected
        # transport must not be able to reflect a credential into logs.
        print("openai cost cache update failed: unexpected collection error", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "updated": True,
                "path": str(absolute_cache_path(args.cache)),
                "updated_at_utc": cache["updated_at_utc"],
                "scope": cache["scope"],
                "day_count": len(cache["days"]),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
