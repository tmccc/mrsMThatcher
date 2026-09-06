"""Runtime state defaults and scheduling helpers.

The root supplies current runtime dependencies explicitly on each call. This
module performs no runtime work at import and retains no runtime authority.
"""
from __future__ import annotations

from typing import Any


def default_state(
    *,
    GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN: Any,
    STATE_MINIMUM_READER_VERSION: Any,
) -> dict:
    """Build a new runtime-state document with safe defaults."""
    return {
        "minimum_reader_version": STATE_MINIMUM_READER_VERSION,
        "last_seen_mention_id": None,
        "mention_pagination": {},
        "mention_backlog": {},
        "mention_backlog_reset_guard": {},
        "mention_pending_candidates": {},
        "author_evaluation_quarantines": {},
        "replied_to_ids": [],
        "dry_run_seen_mention_ids": [],
        "skipped_hot_reply_ids": [],
        "skipped_hot_reply_records": {},
        "hot_post_reply_since_ids": {},
        "hot_post_reply_pagination_tokens": {},
        "hot_post_reply_check_counts": {},

        "daily_reply_date": None,
        "daily_reply_count": 0,
        "daily_replied_author_ids": [],
        "daily_replied_author_counts": {},

        "own_auto_reply_ids": [],
        "tweet_cache": {},
        "pending_ai_reply_drafts": {},
        "ai_reply_history": [],

        "posted_meme_filenames": [],
        "last_meme_post_epoch": 0,
        "next_meme_post_epoch": 0,
        "meme_schedule_version": 0,
        "next_meme_schedule_mode": "",
        "next_meme_schedule_date": "",
        "meme_anchor_quote_post_epoch": 0,

        "last_reply_epoch": 0,
        "last_reply_check_epoch": 0,
        "next_reply_lane_priority": "normal",
        "last_main_post_id": None,
        "last_regular_image_filename": None,
        "original_regular_posts_since_generated_image": GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN,
        "last_quote_post_epoch": 0,
        "next_quote_post_epoch": 0,

        "recent_own_post_ids": [],

        "seen_quote_post_ids": [],
        "replied_to_quote_post_ids": [],
        "skipped_quote_post_ids": [],
        "quote_lookup_pagination_tokens": {},
        "quote_lookup_repeated_cursor_suppressions": {},
        "quote_spam_author_ids": [],
        "daily_quote_reply_date": None,
        "daily_quote_reply_count": 0,
        "last_quote_tweet_check_epoch": 0,

        "x_error_epochs": [],
        "x_write_error_epochs": [],
        "openai_error_epochs": [],
        "api_cooldown_until_epoch": 0,
        "api_cooldown_reason": "",
        "x_write_api_cooldown_until_epoch": 0,
        "x_write_api_cooldown_reason": "",
        "openai_api_cooldown_until_epoch": 0,
        "openai_api_cooldown_reason": "",
        "quote_x_error_epochs": [],
        "quote_api_cooldown_until_epoch": 0,
        "quote_api_cooldown_reason": "",
    }


def append_unique_capped(values: object, item: object, max_items: int) -> list[str]:
    """Append unique capped."""
    item_text = str(item)
    existing = [str(value) for value in values] if isinstance(values, list) else []
    existing = [value for value in existing if value != item_text]
    existing.append(item_text)
    return existing[-max_items:]


def append_unique_durable(values: object, item: object) -> list[str]:
    """Append once without evicting an authoritative completed-target record."""
    item_text = str(item)
    existing = [str(value) for value in values] if isinstance(values, list) else []
    deduplicated = list(dict.fromkeys(existing))
    if item_text not in deduplicated:
        deduplicated.append(item_text)
    return deduplicated


def scheduler_epoch_from_state(
    state: dict,
    key: str,
    *,
    current: int | None = None,
    log: Any,
    math: Any,
) -> tuple[int, bool]:
    """Return the scheduler epoch from state."""
    raw_value = state.get(key, 0)
    malformed = isinstance(raw_value, bool) or (
        isinstance(raw_value, float)
        and (not math.isfinite(raw_value) or not raw_value.is_integer())
    )
    try:
        value = 0 if malformed else int(raw_value or 0)
    except (TypeError, ValueError, OverflowError):
        malformed = True
        value = 0
    if malformed:
        log.warning("Ignoring malformed scheduler epoch %s=%r", key, raw_value)

    if value < 0:
        log.warning("Ignoring negative scheduler epoch %s=%r", key, raw_value)
        value = 0

    if current is not None and value > current:
        log.warning("Ignoring future scheduler epoch %s=%r current=%s", key, raw_value, current)
        value = 0

    if malformed or type(raw_value) is not int or raw_value != value:
        state[key] = value
        return value, True

    return value, False


def load_runtime_state(
    *,
    clear_expired_api_cooldowns: Any,
    load_state: Any,
    sanitize_next_reply_lane_priority: Any,
) -> dict:
    """Load runtime state and apply daily maintenance safely."""
    state = load_state()
    clear_expired_api_cooldowns(state)
    sanitize_next_reply_lane_priority(state)
    return state


def apply_state_fields(state: dict, fields: dict) -> None:
    """Apply state fields."""
    for key, value in fields.items():
        state[key] = value


def next_quote_schedule_fields(
    from_epoch: int | None = None,
    *,
    delay: int | None = None,
    POST_SLEEP_MAX: Any,
    POST_SLEEP_MIN: Any,
    now_epoch: Any,
    random: Any,
) -> tuple[dict, int]:
    """Return the next quote schedule fields."""
    if from_epoch is None:
        from_epoch = now_epoch()

    if delay is None:
        delay = random.randint(POST_SLEEP_MIN, POST_SLEEP_MAX)
    return {"next_quote_post_epoch": int(from_epoch) + delay}, delay


def schedule_next_quote_post(
    state: dict,
    from_epoch: int | None = None,
    *,
    save: bool = True,
    apply_state_fields: Any,
    datetime: Any,
    log: Any,
    next_quote_schedule_fields: Any,
    save_state: Any,
) -> None:
    """Perform the schedule next quote post operation."""
    fields, delay = next_quote_schedule_fields(from_epoch)
    apply_state_fields(state, fields)
    if save:
        save_state(state)

    log.info(
        "Next quote/image post in %d seconds at %s",
        delay,
        datetime.fromtimestamp(state["next_quote_post_epoch"]).strftime("%Y-%m-%d %H:%M:%S"),
    )


def prepare_test_main_post_state(
    state: dict,
    *,
    ENABLE_DAILY_MEME_POSTS: Any,
    ensure_meme_schedule_initialized: Any,
) -> None:
    """Prepare test main post state."""
    if ENABLE_DAILY_MEME_POSTS:
        ensure_meme_schedule_initialized(state)
