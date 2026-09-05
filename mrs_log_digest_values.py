"""Shared scalar conversions and report vocabulary for the log digest.

These helpers have no I/O, environment lookup or runtime initialisation. Both
analysis and Markdown presentation use them; neither imports the other here.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional


GENERATED_POLICIES = ("unrestricted", "small_penalty", "strong_penalty", "origin_quote_only")


REMOTE_WRITE_RECEIPT_ROLE_LABELS = {
    "regular_quote_image_main_post": "regular quote/image main-post receipt",
    "daily_meme_main_post": "daily-meme main-post receipt",
    "conversational_confirmed_reply": (
        "conversational confirmed/sending reply receipt"
    ),
    "historical_context_reply": "historical-context reply receipt",
}


UNKNOWN_MISSING_STATE_FIELD = "unknown (not present in latest snapshot)"


ENGAGEMENT_CORRELATION_WARNING_LIMIT = 100


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    """Parse datetime."""
    if not value:
        return None
    value = value.strip().replace("T", " ")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        value += " 00:00:00"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    raise ValueError(f"Could not parse datetime: {value!r}. Use e.g. '2026-06-25 08:00'.")


def int_or_none(value: Any) -> Optional[int]:
    """Return the int or none."""
    try:
        if value is None:
            return None
        return int(str(value).strip())
    except Exception:
        return None


def short(value: Any, n: int) -> str:
    """Return the short."""
    if value is None:
        return ""
    s = str(value).replace("\n", "\\n")
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)] + "…"


def plural_count(count: Any, singular: str, plural: Optional[str] = None) -> str:
    """Format an integer with a correctly pluralised noun phrase."""
    try:
        number = int(count)
    except (TypeError, ValueError):
        number = 0
    noun = singular if number == 1 else (plural or f"{singular}s")
    return f"{number} {noun}"


def cooldown_state_text(until_epoch: Any, generation_epoch: Any) -> str:
    """Return cooldown state at digest-generation time."""
    until = int_or_none(until_epoch)
    generated = int_or_none(generation_epoch)
    if generated is None:
        return ""
    if until is None or until < 0:
        return "unavailable"
    if until == 0:
        return "cleared"
    return "active" if generated < until else "expired"


def _parse_openai_cost_decimal(value: Any, *, label: str) -> Decimal:
    """Parse one canonical monetary string from the private OpenAI cache."""

    if type(value) is not str or not re.fullmatch(
        r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?", value
    ):
        raise ValueError(f"{label} is not a canonical decimal string")
    parsed = Decimal(value)
    if not parsed.is_finite() or (parsed == 0 and value != "0"):
        raise ValueError(f"{label} is not a canonical finite decimal string")
    return parsed


def _openai_decimal_text(value: Decimal) -> str:
    """Render one finite Decimal without an exponent or redundant zeroes."""

    if not value.is_finite():
        raise ValueError("OpenAI cost is not finite")
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _parse_openai_utc(value: Any, *, label: str) -> datetime:
    """Parse the cache's canonical whole-second UTC timestamp."""

    if type(value) is not str or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value
    ):
        raise ValueError(f"{label} is not a canonical UTC timestamp")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )


def _openai_window_text(start: datetime, end: datetime) -> str:
    """Render one requested UTC cost window compactly."""

    if start.date() == end.date():
        return f"{start:%H:%M}–{end:%H:%M} UTC"
    return f"{start:%Y-%m-%d %H:%M}–{end:%Y-%m-%d %H:%M} UTC"


def _normalise_lane(value: Any) -> str:
    lane = str(value or "unavailable").replace("_reply", "").replace("_", "-")
    return {"hot-post": "hot-post", "quote-tweet": "quote-tweet", "mention": "mention"}.get(lane, "unavailable")


def _human_snapshot_age(seconds: float) -> str:
    """Return a deterministic, whole-second age for state presentation."""
    remaining = max(0, int(seconds))
    parts: List[str] = []
    for unit_seconds, singular in (
        (24 * 60 * 60, "day"),
        (60 * 60, "hour"),
        (60, "minute"),
        (1, "second"),
    ):
        value, remaining = divmod(remaining, unit_seconds)
        if value:
            parts.append(plural_count(value, singular))
    return " ".join(parts) if parts else "0 seconds"


def dt_text(value: datetime) -> str:
    """Return the datetime text."""
    return value.strftime("%Y-%m-%d %H:%M:%S")


def bounded_exception_status(prefix: str, exc: BaseException) -> str:
    """Describe a local read failure without echoing private file content."""

    return f"{prefix}: {type(exc).__name__}"[:320]


MAX_REASONABLE_STATE_EPOCH = 4_102_531_200
PUBLISHED_REPLY_TEXT_MAX_CHARACTERS = 25_000
SHA256_LOWER_RE = re.compile(r"[0-9a-f]{64}\Z")
PUBLIC_POST_ID_RE = re.compile(r"[0-9]{1,30}\Z")


def valid_public_post_id(value: Any) -> bool:
    """Return whether a value is one bounded ASCII decimal post identity."""

    return bool(PUBLIC_POST_ID_RE.fullmatch(str(value or "")))


def valid_string_public_post_id(value: Any) -> bool:
    """Return whether a durable authority stores an exact string post ID."""

    return type(value) is str and valid_public_post_id(value)


def valid_bounded_utf8_text(
    value: Any,
    *,
    allow_empty: bool = False,
) -> bool:
    """Return whether exact public text is bounded and UTF-8 encodable."""

    if (
        not isinstance(value, str)
        or len(value) > PUBLISHED_REPLY_TEXT_MAX_CHARACTERS
        or (not allow_empty and not value)
    ):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def bounded_event_text(
    value: Any,
    *,
    default: Optional[str] = None,
    max_characters: int = 1000,
) -> Optional[str]:
    """Project one structured display string without coercing nested values."""

    if (
        type(value) is str
        and len(value) <= max_characters
        and valid_bounded_utf8_text(value, allow_empty=True)
    ):
        return value
    return default


def bounded_event_nonnegative_integer(
    value: Any,
    *,
    maximum: int = MAX_REASONABLE_STATE_EPOCH,
) -> Optional[int]:
    """Project one bounded non-negative structured display integer."""

    return value if type(value) is int and 0 <= value <= maximum else None


def bounded_event_boolean(value: Any) -> Optional[bool]:
    """Project one structured display boolean without truthiness coercion."""

    return value if type(value) is bool else None


def _count_optional(events: List[Dict[str, Any]], field: str, values: tuple[str, ...]) -> Dict[str, int]:
    counts = Counter({value: 0 for value in values})
    for event in events:
        value = event.get(field)
        key = str(value) if value not in (None, "") else "unavailable"
        counts[key if not values or key in values else "unavailable"] += 1
    return dict(sorted(counts.items()))
