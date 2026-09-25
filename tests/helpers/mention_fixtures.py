"""Build mention queues and author-quarantine state with provider-free test setup."""

from __future__ import annotations

import copy
from datetime import datetime

import pytest

from mrs_bot_reply_cycle_interfaces import PreparedReplyContext
from tests.helpers.mention_values import (
    DIGEST_AUTHOR_NO_REPLY_EVIDENCE_POLICY, DIGEST_AUTHOR_NO_REPLY_CONFIG,
    mention, queue_active_mention, editorial_no_reply, mention_backlog,
    digest_author_no_reply_record,
)
import mrsMThatcher2 as bot


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
    monkeypatch.setattr(
        bot._runtime_control.RuntimeControls,
        "lane_paused",
        lambda _owner, *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        bot._api_cooldowns.ApiCooldowns,
        "active",
        lambda _owner, *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        bot._remote_write_barriers,
        "block_if_ambiguous_remote_post",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        bot,
        "block_if_ambiguous_remote_post",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        bot._mention_discovery, "get_mentions",
        lambda _state, **_kwargs: copy.deepcopy(candidates),
    )
    monkeypatch.setattr(bot._tweet_lookup_cache.TweetLookupCache, "fetch",
                        lambda _owner, tweet_id: {"id": str(tweet_id)})
    monkeypatch.setattr(
        bot._hot_post_discovery, "get_hot_post_reply_candidates",
        lambda _state, **_kwargs: [],
    )
    monkeypatch.setattr(
        bot, "is_probably_spam_or_not_worth_replying", lambda _text: False
    )
    monkeypatch.setattr(
        bot._reply_context.ReplyContext,
        "build",
        lambda _owner, candidate, _state: PreparedReplyContext(
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
    monkeypatch.setattr(bot._reply_native_media.ReplyMedia, "context", lambda _owner, *args, **kwargs: (lambda *_args, **_kwargs: {})(*args, **kwargs))
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)


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
