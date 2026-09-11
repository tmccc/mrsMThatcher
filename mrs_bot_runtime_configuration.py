"""Runtime configuration validation, application and credential checks.

The root supplies current runtime dependencies explicitly on each call. This
module performs no runtime work at import and retains no runtime authority.
"""
from __future__ import annotations

from typing import Any


def validate_runtime_config_values(
    values: dict[str, object],
    *,
    _runtime_config_namespace: Any,
    math: Any,
    validate_single_call_reply_config: Any,
) -> list[str]:
    """Return validation errors for runtime config values.

    This is intentionally conservative for local overrides. Script defaults are
    expected to pass, and invalid local override sets are rejected atomically.
    """
    errors: list[str] = []

    experiment_enabled = values.get(
        "engagement_question_experiment_enabled",
        _runtime_config_namespace().get("engagement_question_experiment_enabled"),
    )
    experiment_plan_path = values.get(
        "engagement_question_experiment_plan_path",
        _runtime_config_namespace().get("engagement_question_experiment_plan_path"),
    )
    notification_path = values.get(
        "engagement_question_notification_output_path",
        _runtime_config_namespace().get("engagement_question_notification_output_path"),
    )
    if type(experiment_enabled) is not bool:
        errors.append("engagement_question_experiment_enabled must be boolean")
    if (
        type(experiment_plan_path) is not str
        or not experiment_plan_path
        or experiment_plan_path != experiment_plan_path.strip()
        or any(ord(character) < 32 for character in experiment_plan_path)
    ):
        errors.append(
            "engagement_question_experiment_plan_path must be a non-empty clean path"
        )
    if (
        type(notification_path) is not str
        or notification_path != notification_path.strip()
        or any(ord(character) < 32 for character in notification_path)
    ):
        errors.append(
            "engagement_question_notification_output_path must be an empty or clean path"
        )
    elif experiment_enabled is True and not notification_path:
        errors.append(
            "engagement_question_notification_output_path is required when the experiment is enabled"
        )

    context_config = values.get("historical_context_reply", _runtime_config_namespace().get("historical_context_reply"))
    context_keys = {"enabled", "maximum_length", "include_meaning", "include_source", "include_verification"}
    if not isinstance(context_config, dict):
        errors.append("historical_context_reply must be an object")
    elif set(context_config) != context_keys:
        errors.append("historical_context_reply fields mismatch")
    else:
        for key in ("enabled", "include_meaning", "include_source", "include_verification"):
            if type(context_config.get(key)) is not bool:
                errors.append(f"historical_context_reply.{key} must be boolean")
        maximum = context_config.get("maximum_length")
        if type(maximum) is not int or not 120 <= maximum <= 25_000:
            errors.append("historical_context_reply.maximum_length must be an integer from 120 to 25000")

    reply_config = values.get(
        "single_call_reply",
        _runtime_config_namespace().get("single_call_reply"),
    )
    errors.extend(validate_single_call_reply_config(reply_config))

    def int_value(key: str) -> int:
        return int(values.get(key, _runtime_config_namespace().get(key, 0)))

    positive_keys = {
        "POST_SLEEP_MIN",
        "POST_SLEEP_MAX",
        "REPLY_CHECK_EVERY_SECONDS",
        "QUOTE_CHECK_EVERY_SECONDS",
        "MIN_SECONDS_BETWEEN_REPLIES",
        "MAX_AUTO_REPLIES_PER_DAY",
        "MAX_QUOTE_REPLIES_PER_DAY",
        "MAX_REPLIES_PER_AUTHOR_PER_DAY",
        "AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD",
        "AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS",
        "AUTHOR_NO_REPLY_QUARANTINE_SECONDS",
        "MAX_HOT_POST_REPLIES_PER_CHECK",
        "QUOTE_POST_LOOKBACK_MAIN_POSTS",
        "RECENT_OWN_POST_IDS_MAX",
        "QUOTE_LOOKUP_MAX_PAGES_PER_POST",
        "MENTIONS_MAX_PAGES_PER_CHECK",
        "HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK",
        "TWEET_CACHE_MAX_AGE_SECONDS",
        "TWEET_CACHE_MAX_ITEMS",
        "ERROR_WINDOW_SECONDS",
        "MAX_X_ERRORS_PER_WINDOW",
        "MAX_OPENAI_ERRORS_PER_WINDOW",
        "COOLDOWN_AFTER_REPEATED_ERRORS_SECONDS",
        "COOLDOWN_AFTER_429_SECONDS",
    }

    for key in sorted(positive_keys):
        try:
            if int_value(key) <= 0:
                errors.append(f"{key} must be positive")
        except Exception:
            errors.append(f"{key} must be an integer")

    for key, low, high in (
        ("MAX_MENTIONS_PER_CHECK", 5, 100),
        ("QUOTE_LOOKUP_API_MAX_RESULTS", 10, 100),
        ("HOT_POST_REPLY_SEARCH_API_MAX_RESULTS", 10, 100),
    ):
        try:
            value = int_value(key)
            if value < low or value > high:
                errors.append(f"{key} must be between {low} and {high}")
        except Exception:
            errors.append(f"{key} must be an integer")

    for key in (
        "ORIGINAL_EDITORIAL_SHADOW_WEIGHT",
        "ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT",
    ):
        raw_value = values.get(key, _runtime_config_namespace().get(key, 0.0))
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            errors.append(f"{key} must be a number")
            continue
        if not math.isfinite(float(raw_value)):
            errors.append(f"{key} must be finite")
        elif float(raw_value) < 0:
            errors.append(f"{key} must be non-negative")

    for key in ("MEME_TRIGGER_AFTER_HOUR", "MEME_FALLBACK_HOUR"):
        try:
            value = int_value(key)
            if value < 0 or value > 23:
                errors.append(f"{key} must be between 0 and 23")
        except Exception:
            errors.append(f"{key} must be an integer")

    try:
        value = int_value("MEME_FALLBACK_MINUTE")
        if value < 0 or value > 59:
            errors.append("MEME_FALLBACK_MINUTE must be between 0 and 59")
    except Exception:
        errors.append("MEME_FALLBACK_MINUTE must be an integer")

    for min_key, max_key in (
        ("POST_SLEEP_MIN", "POST_SLEEP_MAX"),
        ("MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS", "MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS"),
    ):
        try:
            if int_value(min_key) > int_value(max_key):
                errors.append(f"{min_key} must be <= {max_key}")
        except Exception:
            errors.append(f"{min_key}/{max_key} must be integers")

    return errors


def apply_local_config(
    *,
    LOCAL_CONFIG_FILE: Any,
    _runtime_config_namespace: Any,
    load_validated_local_config_overrides: Any,
    log: Any,
    log_json_debug: Any,
) -> None:
    """Apply optional local JSON config overrides without editing the bot script."""
    proposed = load_validated_local_config_overrides()
    if proposed is None:
        log.info("Local config file not present; using script defaults. path=%s", LOCAL_CONFIG_FILE)
        return

    if proposed:
        for key, value in proposed.items():
            _runtime_config_namespace()[key] = value
        log.info("Applied %d local config override(s) from %s", len(proposed), LOCAL_CONFIG_FILE)
        log_json_debug("Local config overrides applied", proposed)
    else:
        log.info("Local config file present but no valid overrides applied: %s", LOCAL_CONFIG_FILE)


def validate_production_credentials(
    *,
    ACCESS_SECRET: Any,
    ACCESS_TOKEN: Any,
    CONSUMER_KEY: Any,
    CONSUMER_SECRET: Any,
    ENABLE_AUTO_REPLIES: Any,
    MY_USER_ID: Any,
    OPENAI_API_KEY: Any,
    single_call_reply: Any,
) -> None:
    """Validate required credentials without logging their values."""
    if not all(
        isinstance(value, str) and value.strip()
        for value in (CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_SECRET, MY_USER_ID)
    ):
        raise RuntimeError(
            "Missing X credentials. Set X_CONSUMER_KEY, X_CONSUMER_SECRET, "
            "X_ACCESS_TOKEN, X_ACCESS_SECRET, X_MY_USER_ID"
        )
    if not MY_USER_ID.isascii() or not MY_USER_ID.isdigit():
        raise RuntimeError("X_MY_USER_ID must be a numeric X account ID")
    if (
        ENABLE_AUTO_REPLIES
        and single_call_reply.get("enabled") is True
        and not (isinstance(OPENAI_API_KEY, str) and OPENAI_API_KEY.strip())
    ):
        raise RuntimeError(
            "single_call_reply is enabled, but OPENAI_API_KEY is not set"
        )
