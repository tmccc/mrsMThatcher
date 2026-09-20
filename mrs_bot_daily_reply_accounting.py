"""Own daily reply buckets, per-author counts and confirmed-reply accounting.

DailyReplyAccounting binds current date, logging and ID-list helper
boundaries without retaining caller state. Daily reset and confirmation-date
advancement deliberately keep their distinct time rules. Confirmation recording
uses the caller's prior idempotency decision and resolved receipt dates at its
existing position in reconciliation. Counter normalization preserves replacement
maps and legacy-ID fallback; marking preserves mutation and callback ordering.
Durable saves, reply markers and receipt lifecycle remain with their existing
owners. Import and construction perform no runtime access.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime


def daily_author_reply_counts(state: dict) -> dict[str, int]:
    """Return the daily author reply counts."""
    counts = state.get("daily_replied_author_counts", {})
    if isinstance(counts, dict):
        cleaned: dict[str, int] = {}
        for author_id, count in counts.items():
            try:
                cleaned[str(author_id)] = max(0, int(count))
            except Exception:
                continue
        if not cleaned:
            legacy_authors = set(str(x) for x in state.get("daily_replied_author_ids", []))
            cleaned = {author_id: 1 for author_id in legacy_authors}
        state["daily_replied_author_counts"] = cleaned
        return cleaned

    legacy_authors = set(str(x) for x in state.get("daily_replied_author_ids", []))
    cleaned = {author_id: 1 for author_id in legacy_authors}
    state["daily_replied_author_counts"] = cleaned
    return cleaned


@dataclass(frozen=True)
class DailyReplyAccounting:
    """Maintain daily reply accounting with explicit state and resolved dates."""

    log: logging.Logger
    reply_cap_date_str: Callable
    append_unique_capped: Callable

    def reset(self, state: dict) -> None:
        """Reset daily reply count if needed."""
        today = self.reply_cap_date_str()

        if state.get("daily_reply_date") != today:
            self.log.info(
                "Resetting daily reply count. Previous date=%s new date=%s previous count=%s",
                state.get("daily_reply_date"),
                today,
                state.get("daily_reply_count"),
            )
            state["daily_reply_date"] = today
            state["daily_reply_count"] = 0
            state["daily_replied_author_ids"] = []
            state["daily_replied_author_counts"] = {}

    def reset_quotes(self, state: dict) -> None:
        """Reset daily quote reply count if needed."""
        today = self.reply_cap_date_str()

        if state.get("daily_quote_reply_date") != today:
            self.log.info(
                "Resetting daily quote-reply count. Previous date=%s new date=%s previous count=%s",
                state.get("daily_quote_reply_date"),
                today,
                state.get("daily_quote_reply_count"),
            )
            state["daily_quote_reply_date"] = today
            state["daily_quote_reply_count"] = 0

    def author_count(self, state: dict, author_id: str) -> int:
        """Return the daily author reply count."""
        return daily_author_reply_counts(state).get(str(author_id), 0)

    def mark_author(self, state: dict, author_id: str) -> None:
        """Mark daily author replied."""
        author_id = str(author_id)
        counts = daily_author_reply_counts(state)
        counts[author_id] = counts.get(author_id, 0) + 1
        state["daily_replied_author_counts"] = counts

        state["daily_replied_author_ids"] = self.append_unique_capped(
            state.get("daily_replied_author_ids", []),
            author_id,
            1000,
        )

    def valid_date(self, value: object) -> bool:
        """Return whether a value is a canonical calendar date."""
        if not isinstance(value, str):
            return False
        try:
            return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d") == value
        except ValueError:
            return False

    def advance(
        self,
        state: dict,
        confirmation_date: str,
        *,
        include_quote_lane: bool,
    ) -> None:
        """Advance stale daily reply buckets without rolling newer state backward."""
        state_reply_date = state.get("daily_reply_date")
        if not self.valid_date(state_reply_date) or state_reply_date < confirmation_date:
            self.log.info(
                "Advancing daily reply accounting to confirmation date. "
                "previous_date=%s new_date=%s previous_count=%s",
                state_reply_date,
                confirmation_date,
                state.get("daily_reply_count"),
            )
            state["daily_reply_date"] = confirmation_date
            state["daily_reply_count"] = 0
            state["daily_replied_author_ids"] = []
            state["daily_replied_author_counts"] = {}
        if not include_quote_lane:
            return
        state_quote_date = state.get("daily_quote_reply_date")
        if not self.valid_date(state_quote_date) or state_quote_date < confirmation_date:
            self.log.info(
                "Advancing daily quote-reply accounting to confirmation date. "
                "previous_date=%s new_date=%s previous_count=%s",
                state_quote_date,
                confirmation_date,
                state.get("daily_quote_reply_count"),
            )
            state["daily_quote_reply_date"] = confirmation_date
            state["daily_quote_reply_count"] = 0

    def record_confirmed(
        self,
        state: dict,
        *,
        already_recorded: bool,
        candidate_source: str,
        author_id: str,
        receipt_reply_date: str,
        receipt_quote_reply_date: str,
    ) -> None:
        """Count an unseen confirmation in matching current daily buckets."""

        if not already_recorded and state.get("daily_reply_date") == receipt_reply_date:
            state["daily_reply_count"] = int(state.get("daily_reply_count", 0) or 0) + 1
            if author_id:
                self.mark_author(state, author_id)
        if (
            candidate_source == "quote_tweet"
            and not already_recorded
            and state.get("daily_quote_reply_date") == receipt_quote_reply_date
        ):
            state["daily_quote_reply_count"] = int(state.get("daily_quote_reply_count", 0) or 0) + 1
