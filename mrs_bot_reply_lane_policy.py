"""Apply deterministic reply eligibility and spam gates.

Daily accounting and clarification behavior live in their respective owners;
legacy helper names remain re-exported here for compatibility. Root adapters
supply current eligibility and logging dependencies. Import performs no file,
environment, clock, provider or RNG work.
"""

from __future__ import annotations

import logging
from types import ModuleType

from mrs_bot_daily_reply_accounting import daily_author_reply_counts

from mrs_bot_reply_clarifications import (
    CLARIFICATION_CUE_RE,
    CLARIFICATION_TOKEN_RE,
    CLARIFICATION_TOKEN_STOPWORDS,
    clarification_thread_id,
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
