"""Apply reply-lane counters, clarification eligibility and deterministic gates.

Root adapters supply current configuration, callbacks, exception, date, logger
and regex authorities on each call. The three fixed clarification definitions
live here and share their initial objects with the root. Original bodies retain
caller references, mutation/logging order and native error boundaries.

Persistence, context/lookup, own-reply identity and pipeline authority remain in
their existing locations. Import uses only the standard library and constructs
the fixed regexes and stopword set without file, environment, provider or RNG
work. No callbacks, configuration, clients or state are retained.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from types import ModuleType


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


CLARIFICATION_CUE_RE = re.compile(
    r"\b(?:you\s+)?(?:did(?:n't|\s+not)|does(?:n't|\s+not)|have(?:n't|\s+not))\s+answer(?:ed)?\b"
    r"|\b(?:your|that|the)\s+(?:reply|answer)\s+(?:did(?:n't|\s+not)|does(?:n't|\s+not))\s+answer\b"
    r"|\b(?:that(?:'s|\s+is|\s+was)\s+)?not\s+(?:what|the\s+question)\s+(?:i\s+)?asked\b"
    r"|\banswer\s+(?:my|the)\s+question\b"
    r"|\b(?:you\s+)?(?:avoided|evaded)\s+(?:my|the)\s+question\b",
    re.IGNORECASE,
)
CLARIFICATION_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'-]{2,}")
CLARIFICATION_TOKEN_STOPWORDS = {
    "answer", "asked", "did", "does", "from", "have", "people", "question",
    "that", "the", "their", "then", "they", "this", "towards", "what", "when",
    "where", "which", "who", "with", "you", "your",
}


def clarification_thread_id(candidate: dict) -> str:
    """Return the clarification thread ID."""
    return str(candidate.get("conversation_id") or candidate.get("id") or "")


def clarification_thread_is_terminal(
    state: dict,
    candidate: dict,
    *,
    clarification_thread_id: Callable,
) -> bool:
    """Return whether clarification thread is terminal."""
    records = state.get("clarification_reply_records", {})
    return isinstance(records, dict) and clarification_thread_id(candidate) in records


def author_used_clarification_recently(
    state: dict,
    author_id: str,
    *,
    current: int,
    CLARIFICATION_REPLY_WINDOW_SECONDS: int,
) -> bool:
    """Return the author used clarification recently."""
    records = state.get("clarification_reply_records", {})
    if not isinstance(records, dict):
        return False
    cutoff = int(current) - CLARIFICATION_REPLY_WINDOW_SECONDS
    for record in records.values():
        if not isinstance(record, dict) or str(record.get("author_id") or "") != str(author_id):
            continue
        try:
            if int(record.get("completed_epoch", 0) or 0) > cutoff:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _clarification_tokens(
    text: object,
    *,
    CLARIFICATION_TOKEN_RE: re.Pattern[str],
    CLARIFICATION_TOKEN_STOPWORDS: set[str],
    re: ModuleType,
) -> set[str]:
    without_handles = re.sub(r"(?<![A-Za-z0-9_])@[A-Za-z0-9_]+", " ", str(text or ""))
    return {
        token.lower() for token in CLARIFICATION_TOKEN_RE.findall(without_handles)
        if token.lower() not in CLARIFICATION_TOKEN_STOPWORDS
    }


def clarification_reply_context(
    state: dict,
    candidate: dict,
    *,
    current: int,
    ApiError: type[Exception],
    CLARIFICATION_CUE_RE: re.Pattern[str],
    _clarification_tokens: Callable,
    author_used_clarification_recently: Callable,
    clarification_thread_id: Callable,
    clarification_thread_is_terminal: Callable,
    conversational_reply_pipeline_enabled: Callable,
    get_immediate_parent_id: Callable,
    get_tweet_by_id_cached: Callable,
    tweet_text_is_complete: Callable,
    api_error_is_permanent_target_failure: Callable,
    is_our_auto_reply: Callable,
) -> dict | None:
    """Return bounded repair metadata only for a direct follow-up to our confirmed reply."""
    if not conversational_reply_pipeline_enabled() or clarification_thread_is_terminal(state, candidate):
        return None
    author_id = str(candidate.get("author_id") or "")
    if not author_id or author_used_clarification_recently(state, author_id, current=current):
        return None

    try:
        prior_bot_reply_id = get_immediate_parent_id(candidate)
    except ApiError:
        return None
    if not prior_bot_reply_id or prior_bot_reply_id not in {
        str(item) for item in state.get("own_auto_reply_ids", [])
    }:
        return None

    cache = state.get("tweet_cache", {})
    if not isinstance(cache, dict):
        return None
    prior_bot_reply = cache.get(prior_bot_reply_id)
    if not is_our_auto_reply(prior_bot_reply, state):
        return None
    try:
        original_question_id = get_immediate_parent_id(prior_bot_reply)
    except ApiError:
        return None
    original_question = cache.get(str(original_question_id or ""))
    if not isinstance(original_question, dict):
        return None
    if str(original_question.get("author_id") or "") != author_id:
        return None

    thread_id = clarification_thread_id(candidate)
    if not thread_id or str(original_question.get("conversation_id") or original_question_id) != thread_id:
        return None
    if not tweet_text_is_complete(original_question):
        try:
            original_question = get_tweet_by_id_cached(str(original_question_id), state)
        except ApiError as exc:
            if api_error_is_permanent_target_failure(exc):
                return None
            raise
        if (
            not isinstance(original_question, dict)
            or str(original_question.get("author_id") or "") != author_id
            or str(original_question.get("conversation_id") or original_question_id) != thread_id
        ):
            return None
    question_text = str(original_question.get("text") or "")
    incoming_text = str(candidate.get("text") or "")
    if "?" not in question_text:
        return None

    explicit_correction = bool(CLARIFICATION_CUE_RE.search(incoming_text))
    restated_question = "?" in incoming_text
    if restated_question:
        restated_question = bool(
            _clarification_tokens(question_text) & _clarification_tokens(incoming_text)
        )
    if not explicit_correction and not restated_question:
        return None

    return {
        "thread_id": thread_id,
        "prior_bot_reply_id": prior_bot_reply_id,
        "original_question_id": str(original_question_id),
        "question_text": question_text,
        "trigger": "explicit_correction" if explicit_correction else "restated_question",
    }


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
