"""Pure runtime-control schema shared by the bot and diagnostic readers.

Own supported keys, timestamp bounds and value validation. Callers retain their
file readers, strict JSON parsing, clocks, caches and failure policies. Explicit
parser dependencies preserve current date and epoch policy; fixed numeric grammar is local;
importing this module performs no runtime I/O.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from decimal import Decimal


MAX_CONTROL_EPOCH = 4_102_531_200


CONTROL_BOOLEAN_KEYS = frozenset({
    "disable_all",
    "pause_all",
    "disable_replies",
    "pause_replies",
    "disable_normal_replies",
    "pause_normal_replies",
    "disable_quote_replies",
    "pause_quote_replies",
    "disable_hot_post_replies",
    "pause_hot_post_replies",
    "disable_quote_posts",
    "pause_quote_posts",
    "disable_meme_posts",
    "pause_meme_posts",
})


CONTROL_TIME_KEYS = frozenset(
    f"{key}_until" for key in CONTROL_BOOLEAN_KEYS
)


CONTROL_METADATA_KEYS = frozenset({"generation"})


CONTROL_ALLOWED_KEYS = (
    CONTROL_BOOLEAN_KEYS | CONTROL_TIME_KEYS | CONTROL_METADATA_KEYS
)


def parse_control_time(
    value: object,
    *,
    MAX_REASONABLE_STATE_EPOCH: int,
    datetime: type,
) -> int:
    """Parse a runtime-control timestamp into an epoch value."""
    if isinstance(value, bool) or value is None:
        raise ValueError("control timestamp must not be a boolean or null")
    if type(value) is int:
        epoch = value
    elif type(value) is Decimal:
        if not value.is_finite() or value != value.to_integral_value():
            raise ValueError(
                "control timestamp exact number must be finite and integral"
            )
        if value < 0 or value > MAX_REASONABLE_STATE_EPOCH:
            raise ValueError(
                f"control timestamp is outside the supported epoch range: {value}"
            )
        epoch = int(value)
    elif type(value) is float:
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError("control timestamp float must be finite and integral")
        epoch = int(value)
    else:
        if type(value) is not str:
            raise ValueError("control timestamp must be an integer epoch or documented date string")
        text = value.strip()
        if not text or text.isdigit():
            raise ValueError("control timestamp string must use a documented date format")
        try:
            # Python 3.10 needs the explicit offset spelling for UTC Z.
            canonical = text[:-1] + "+00:00" if text.endswith("Z") else text
            parsed = datetime.fromisoformat(canonical)
        except ValueError as exc:
            raise ValueError(f"Cannot parse control time {value!r}") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("control timestamp must include an explicit timezone offset")
        epoch = int(parsed.timestamp())
    if epoch < 0 or epoch > MAX_REASONABLE_STATE_EPOCH:
        raise ValueError(f"control timestamp is outside the supported epoch range: {epoch}")
    return epoch


def validate_control_metadata(
    data: object,
    *,
    CONTROL_ALLOWED_KEYS: frozenset[str],
) -> None:
    """Validate the document shape, supported keys and optional generation."""
    if not isinstance(data, dict):
        raise ValueError("control document must be a JSON object")
    for key, value in data.items():
        if type(key) is not str or key not in CONTROL_ALLOWED_KEYS:
            raise ValueError(
                f"unsupported runtime-control key {key!r}; "
                "refusing to ignore a possible safety-setting typo"
            )
        if key == "generation" and (type(value) is not int or value < 0):
            raise ValueError("generation must be a non-negative integer")


def validate_control_values(
    data: dict,
    *,
    CONTROL_BOOLEAN_KEYS: frozenset[str],
    CONTROL_TIME_KEYS: frozenset[str],
    parse_control_time: Callable,
) -> dict:
    """Validate pause values after metadata, copying integral Decimal epochs."""
    validated = dict(data)
    for key, value in data.items():
        key_text = key
        if key_text in CONTROL_TIME_KEYS:
            parsed_epoch = parse_control_time(value)
            if type(value) is Decimal:
                validated[key_text] = parsed_epoch
        elif key_text in CONTROL_BOOLEAN_KEYS:
            if isinstance(value, bool):
                continue
            if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
                continue
            raise ValueError(f"{key_text} must be a boolean")
    return validated


def validate_control_document(
    data: object,
    *,
    CONTROL_ALLOWED_KEYS: frozenset[str],
    CONTROL_BOOLEAN_KEYS: frozenset[str],
    CONTROL_TIME_KEYS: frozenset[str],
    parse_control_time: Callable,
) -> dict:
    """Validate one complete control document without changing caller data."""
    validate_control_metadata(data, CONTROL_ALLOWED_KEYS=CONTROL_ALLOWED_KEYS)
    return validate_control_values(
        data,
        CONTROL_BOOLEAN_KEYS=CONTROL_BOOLEAN_KEYS,
        CONTROL_TIME_KEYS=CONTROL_TIME_KEYS,
        parse_control_time=parse_control_time,
    )
