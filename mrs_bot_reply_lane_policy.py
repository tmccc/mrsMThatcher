"""Apply reply-lane counters and deterministic eligibility/spam gates.

Clarification policy and completed repair history belong to the clarification
owner; fixed definitions and thread-ID lookup remain reexported for compatibility.
Root adapters supply current remaining runtime dependencies. Imports perform no
file, environment, clock, provider or RNG work.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from types import ModuleType

from mrs_bot_reply_clarifications import (
    CLARIFICATION_CUE_RE,
    CLARIFICATION_TOKEN_RE,
    CLARIFICATION_TOKEN_STOPWORDS,
    clarification_thread_id,
)


def reset_daily_reply_count_if_needed(
    state: dict,
    *,
    log: logging.Logger,
    reply_cap_date_str: Callable,
) -> None:
    """Reset daily reply count if needed."""
    today = reply_cap_date_str()

    if state.get("daily_reply_date") != today:
        log.info(
            "Resetting daily reply count. Previous date=%s new date=%s previous count=%s",
            state.get("daily_reply_date"),
            today,
            state.get("daily_reply_count"),
        )
        state["daily_reply_date"] = today
        state["daily_reply_count"] = 0
        state["daily_replied_author_ids"] = []
        state["daily_replied_author_counts"] = {}


def reset_daily_quote_reply_count_if_needed(
    state: dict,
    *,
    log: logging.Logger,
    reply_cap_date_str: Callable,
) -> None:
    """Reset daily quote reply count if needed."""
    today = reply_cap_date_str()

    if state.get("daily_quote_reply_date") != today:
        log.info(
            "Resetting daily quote-reply count. Previous date=%s new date=%s previous count=%s",
            state.get("daily_quote_reply_date"),
            today,
            state.get("daily_quote_reply_count"),
        )
        state["daily_quote_reply_date"] = today
        state["daily_quote_reply_count"] = 0


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


def daily_author_reply_count(
    state: dict,
    author_id: str,
    *,
    daily_author_reply_counts: Callable,
) -> int:
    """Return the daily author reply count."""
    return daily_author_reply_counts(state).get(str(author_id), 0)


def mark_daily_author_replied(
    state: dict,
    author_id: str,
    *,
    append_unique_capped: Callable,
    daily_author_reply_counts: Callable,
) -> None:
    """Mark daily author replied."""
    author_id = str(author_id)
    counts = daily_author_reply_counts(state)
    counts[author_id] = counts.get(author_id, 0) + 1
    state["daily_replied_author_counts"] = counts

    state["daily_replied_author_ids"] = append_unique_capped(
        state.get("daily_replied_author_ids", []),
        author_id,
        1000,
    )


def reply_target_is_directly_eligible(
    tweet: dict,
    *,
    MY_USERNAME: str,
    MY_USER_ID: str,
    re: ModuleType,
) -> bool:
    """Check only the target post itself for X reply eligibility evidence."""
    if str(tweet.get("author_id") or "") == str(MY_USER_ID):
        return True

    entities = tweet.get("entities")
    if isinstance(entities, dict):
        mentions = entities.get("mentions")
        if isinstance(mentions, list):
            for mention in mentions:
                if not isinstance(mention, dict):
                    continue
                if str(mention.get("id") or "") == str(MY_USER_ID):
                    return True
                username = str(mention.get("username") or "").lstrip("@")
                if MY_USERNAME and username.casefold() == MY_USERNAME.casefold():
                    return True
        return False

    text = str(tweet.get("text") or "")
    if MY_USERNAME and re.search(
        rf"(?<![A-Za-z0-9_])@{re.escape(MY_USERNAME)}(?![A-Za-z0-9_])",
        text,
        flags=re.IGNORECASE,
    ):
        return True
    return False


def is_probably_spam_or_not_worth_replying(
    text: str,
    *,
    SPAMMY_PATTERNS: list[str],
    log: logging.Logger,
    re: ModuleType,
) -> bool:
    """Return whether is probably spam or not worth replying."""
    low = text.lower().strip()
    log.debug("Spam check for text=%r", text)

    for pattern in SPAMMY_PATTERNS:
        if re.search(pattern, low):
            log.info("Ignoring post: matched spam pattern %s", pattern)
            return True

    if text.count("!") >= 5:
        log.info("Ignoring post: too many exclamation marks")
        return True

    words = low.split()
    if words:
        link_or_mention_count = sum(1 for w in words if w.startswith("@") or w.startswith("http"))
        ratio = link_or_mention_count / len(words)
        log.debug("Post link/mention ratio=%s", ratio)
        if ratio > 0.5:
            log.info("Ignoring post: mostly links/mentions")
            return True

    log.debug("Post passed spam check")
    return False
