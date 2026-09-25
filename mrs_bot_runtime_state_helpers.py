"""Runtime state defaults and scheduling helpers.

QuoteSchedule owns delay selection and schedule application with current clock,
configuration, logger and persistence inputs. Runtime loading calls the supplied
cooldown owner directly before priority sanitisation; one-shot main-post
preparation calls its MemeSchedule directly. Fixed random/calendar
helpers and state-field application stay local. Other maintenance boundaries
remain supplied per call. Import and owner construction perform no runtime work.
"""
from __future__ import annotations

from dataclasses import dataclass

import math
import random
from collections.abc import Callable
from datetime import datetime
from logging import Logger
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from mrs_bot_core_contracts import BotState
    from mrs_bot_api_cooldowns import ApiCooldowns
    from mrs_bot_daily_meme import MemeSchedule


def default_state(
    *,
    STATE_MINIMUM_READER_VERSION: int,
) -> BotState:
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
        "last_quote_post_epoch": 0,
        "next_quote_post_epoch": 0,

        "recent_own_post_ids": [],

        "seen_quote_post_ids": [],
        "replied_to_quote_post_ids": [],
        "skipped_quote_post_ids": [],
        "quote_pending_candidates": {},
        "quote_lookup_pagination_tokens": {},
        "quote_search_pagination_tokens": {},
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
        # Older spacing settings could write a future quote-check epoch. Repair
        # once and wait a normal interval; zero would immediately poll again.
        value = current if key == "last_quote_tweet_check_epoch" else 0

    if malformed or type(raw_value) is not int or raw_value != value:
        state[key] = value
        return value, True

    return value, False


def load_runtime_state(
    *,
    cooldowns: ApiCooldowns,
    load_state: Any,
    sanitize_next_reply_lane_priority: Any,
) -> dict:
    """Load runtime state and apply daily maintenance safely."""
    state = load_state()
    cooldowns.clear_expired(state)
    sanitize_next_reply_lane_priority(state)
    return state


def apply_state_fields(state: dict, fields: dict) -> None:
    """Apply state fields."""
    for key, value in fields.items():
        state[key] = value


def prepare_test_main_post_state(
    state: dict,
    *,
    ENABLE_DAILY_MEME_POSTS: Any,
    meme_schedule: MemeSchedule,
) -> None:
    """Prepare test main post state."""
    if ENABLE_DAILY_MEME_POSTS:
        meme_schedule.ensure_initialized(state)


@dataclass(frozen=True)
class QuoteSchedule:
    """Own quotation delay selection and schedule application/persistence."""

    minimum_delay: int
    maximum_delay: int
    now_epoch: Callable[[], int]
    save_state: Callable[..., Any]
    log: Logger

    def next_fields(
        self,
        from_epoch: int | None = None,
        *,
        delay: int | None = None,
    ) -> tuple[dict, int]:
        """Return the next quote schedule fields."""
        if from_epoch is None:
            from_epoch = self.now_epoch()

        if delay is None:
            delay = random.randint(self.minimum_delay, self.maximum_delay)
        return {"next_quote_post_epoch": int(from_epoch) + delay}, delay

    def schedule(
        self,
        state: dict,
        from_epoch: int | None = None,
        *,
        save: bool = True,
    ) -> None:
        """Perform the schedule next quote post operation."""
        fields, delay = self.next_fields(from_epoch)
        apply_state_fields(state, fields)
        if save:
            self.save_state(state)

        self.log.info(
            "Next quote/image post in %d seconds at %s",
            delay,
            datetime.fromtimestamp(state["next_quote_post_epoch"]).strftime("%Y-%m-%d %H:%M:%S"),
        )
