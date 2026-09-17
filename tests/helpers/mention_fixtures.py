"""Build mention queues and author-quarantine state with provider-free test setup."""

from __future__ import annotations

import copy
from datetime import datetime

import pytest

from mrs_bot_reply_cycle_interfaces import PreparedReplyContext
import mrsMThatcher2 as bot


DIGEST_AUTHOR_NO_REPLY_EVIDENCE_POLICY = (
    "single_sol_explicit_spam_or_abuse_v2"
)


DIGEST_AUTHOR_NO_REPLY_CONFIG = {
    "AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD": 3,
    "AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS": 21_600,
    "AUTHOR_NO_REPLY_QUARANTINE_SECONDS": 43_200,
}


def mention(tweet_id: int, author_id: int, text: str = "@MrsMThatcher A contribution.") -> dict:
    """Return a complete synthetic mention using the current bot account ID."""
    return {
        "id": str(tweet_id),
        "author_id": str(author_id),
        "conversation_id": str(tweet_id),
        "text": text,
        "text_is_complete": True,
        "entities": {
            "mentions": [
                {"id": str(bot.MY_USER_ID), "username": "MrsMThatcher"}
            ]
        },
        "referenced_tweets": [],
    }


def queue_active_mention(state: dict, candidate: dict, *, base_since_id: str) -> None:
    """Install one pending test candidate with exact active-page ownership."""
    state["last_seen_mention_id"] = base_since_id
    state["mention_backlog"] = {
        "since_id": base_since_id,
        "next_token": "A",
        "highest_mention_id": str(candidate["id"]),
        "pages_completed": 1,
        "started_epoch": 1_999_999_000,
        "seen_tokens": [],
        "announced": True,
    }
    state["mention_pagination"] = {
        "base_since_id": base_since_id,
        "next_token": "A",
    }
    state["mention_pending_candidates"] = {
        str(candidate["id"]): copy.deepcopy(candidate)
    }


def configure_provider_free_mention_check(
    monkeypatch: pytest.MonkeyPatch,
    candidates: list[dict],
    *,
    current_epoch: int,
) -> None:
    """Set deterministic mention limits and replace reads and writes with local doubles."""
    enabled = copy.deepcopy(bot.single_call_reply)
    enabled["enabled"] = True
    monkeypatch.setattr(bot, "single_call_reply", enabled)
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 48)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 6)
    monkeypatch.setattr(bot, "now_epoch", lambda: current_epoch)
    monkeypatch.setattr(
        bot,
        "current_datetime",
        lambda: datetime.fromtimestamp(current_epoch),
    )
    monkeypatch.setattr(bot, "lane_paused", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)
    monkeypatch.setattr(bot, "get_mentions", lambda _state: copy.deepcopy(candidates))
    monkeypatch.setattr(
        bot,
        "get_tweet_by_id",
        lambda tweet_id: {"id": str(tweet_id)},
    )
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(
        bot, "is_probably_spam_or_not_worth_replying", lambda _text: False
    )
    monkeypatch.setattr(
        bot,
        "build_context_for_reply_ai",
        lambda candidate, _state: PreparedReplyContext(
            {
                "target_id": str(candidate["id"]),
                "thread_id": str(candidate["conversation_id"]),
                "lane": "mention",
                "incoming_contribution": str(candidate["text"]),
                "parent_thread": [],
            },
            {},
        ),
    )
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: object())
    monkeypatch.setattr(
        bot, "reply_media_context_for_candidate", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)


def editorial_no_reply(
    context: dict,
    *_args: object,
    evaluation_outcome: dict | None = None,
    reason_code: str = "completed_exchange",
    **_kwargs: object,
) -> None:
    """Return one valid single-call editorial no-reply test outcome."""

    assert context.get("target_id")
    assert evaluation_outcome is not None
    evaluation_outcome.update(
        {
            "status": "no_reply",
            "reason": reason_code,
            "reason_code": reason_code,
            "model_call_count": 1,
        }
    )
    return None


def install_mention_pages(
    monkeypatch: pytest.MonkeyPatch,
    pages: dict[str | None, tuple[list[dict], str | None]],
) -> list[dict]:
    """Serve deterministic continuation pages and return the captured request parameters."""
    requests: list[dict] = []

    def request(_method: str, _path: str, *, params: dict) -> dict:
        requests.append(dict(params))
        key = params.get("pagination_token")
        data, next_token = pages[key]
        return {
            "data": copy.deepcopy(data),
            "meta": {"next_token": next_token} if next_token else {},
        }

    monkeypatch.setattr(bot, "x_request", request)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "MAX_MENTIONS_PER_CHECK", 5)
    monkeypatch.setattr(bot, "MENTIONS_MAX_PAGES_PER_CHECK", 1)
    monkeypatch.setattr(bot, "now_epoch", lambda: 2_000_000_000)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    return requests


def mention_backlog(
    *,
    since_id: str,
    next_token: str = "A",
    highest_mention_id: str = "105",
) -> dict:
    """Build one active mention-page ownership record."""
    return {
        "since_id": since_id,
        "next_token": next_token,
        "highest_mention_id": highest_mention_id,
        "pages_completed": 1,
        "started_epoch": 1_999_999_000,
        "seen_tokens": [],
        "announced": True,
    }


def digest_author_no_reply_record(
    epochs: list[int],
    *,
    quarantine_until_epoch: int = 0,
    last_updated_epoch: int | None = None,
) -> dict:
    """Build the exact current durable author-quarantine record shape."""
    updated = last_updated_epoch
    if updated is None:
        updated = max([*epochs, quarantine_until_epoch, 0])
    return {
        "recent_no_reply_epochs": list(epochs),
        "quarantine_until_epoch": quarantine_until_epoch,
        "last_updated_epoch": updated,
        "latest_explicit_spam_or_abuse_epoch": epochs[-1] if epochs else 0,
        "evidence_policy": DIGEST_AUTHOR_NO_REPLY_EVIDENCE_POLICY,
    }
