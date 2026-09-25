"""Build local reply evidence, drafts, receipts and isolated reply-cycle fixtures."""

from __future__ import annotations

import copy
from datetime import datetime
from unittest.mock import Mock
from mrs_bot_reply_assembly import ReplyAssembly
from mrs_bot_reply_delivery import ReplyReceipts

import mrs_bot_reply_assembly as assembly

import pytest

from mrs_bot_reply_cycle_interfaces import PreparedReplyContext
from tests.helpers.bot_runtime import SCENARIOS, SOURCE_TWEET_LOOKUP_FETCH, bot
from tests.fake_api_server import load_scenario
from tests.helpers.mention_fixtures import editorial_no_reply
from tests.helpers.reply_evaluation import legacy_reply_evaluator
from tests.helpers.single_call_fixtures import FakeHttpResponse
from tests.helpers.reply_values import (
    UnitReplyEvidenceRepository, unit_reply_context, unit_approved_reply,
    unit_confirmed_reply_receipt, unit_sending_reply_receipt,
    unit_historical_context_sending_receipt, unit_confirmed_v3_reply_receipt,
    unit_v4_reply_receipt_template, unit_sending_v4_reply_receipt,
    unit_confirmed_v4_reply_receipt, reply_evaluation_record,
)


UNIT_REPLY_REPOSITORY = UnitReplyEvidenceRepository()


def patch_reply_owner_method(monkeypatch, owner_type, method: str, callback) -> None:
    """Replace an owned operation without adding self to observed arguments."""

    def invoke(_owner, *args, **kwargs):
        return callback(*args, **kwargs)

    monkeypatch.setattr(owner_type, method, invoke)


def patch_reply_receipt_method(monkeypatch, bot, method: str, callback) -> None:
    """Patch the explicit receipt component for an integration scenario."""

    def invoke(_owner, *args, **kwargs):
        return callback(*args, **kwargs)

    monkeypatch.setattr(ReplyReceipts, method, invoke)


def patch_tweet_lookup_method(monkeypatch, method: str, callback) -> None:
    """Replace an owned lookup/cache operation while preserving observed arguments."""
    patch_reply_owner_method(monkeypatch, bot._tweet_lookup_cache.TweetLookupCache, method, callback)


def restore_tweet_lookup_fetch(monkeypatch) -> None:
    """Restore real isolated transport lookup after the shared fixture's fake fetch."""
    monkeypatch.setattr(bot._tweet_lookup_cache.TweetLookupCache, "fetch", SOURCE_TWEET_LOOKUP_FETCH)


def patch_reply_draft_method(monkeypatch, method: str, callback) -> None:
    """Replace an owned draft operation using its existing public argument shape."""

    from mrs_bot_reply_drafts import ReplyDrafts

    def invoke(_owner, *args, **kwargs):
        return callback(*args, **kwargs)

    monkeypatch.setattr(ReplyDrafts, method, invoke)


def patch_reply_history_method(monkeypatch, method: str, callback) -> None:
    """Replace a history operation without adding the owner to callback arguments."""

    from mrs_bot_reply_history import ReplyHistory

    def invoke(_owner, *args, **kwargs):
        return callback(*args, **kwargs)

    monkeypatch.setattr(ReplyHistory, method, invoke)


def patch_reply_context_method(monkeypatch, method: str, callback) -> None:
    """Replace an owned context operation using its caller argument shape."""

    from mrs_bot_reply_context import ReplyContext

    def invoke(_owner, *args, **kwargs):
        return callback(*args, **kwargs)

    monkeypatch.setattr(ReplyContext, method, invoke)


def prepare_unit_historical_context_create(
    *,
    text: str = "Context",
    parent_post_id: str = "111",
) -> dict[str, object]:
    """Persist and return one exact historical-context sending receipt."""

    receipt = unit_historical_context_sending_receipt(
        text=text,
        parent_post_id=parent_post_id,
    )
    bot.atomic_write_json(bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE, receipt)
    return receipt


def configure_normal_cycle(monkeypatch):
    """Configure a provider-free normal cycle and return its evaluation stub."""
    epoch = 2_000_000_000
    monkeypatch.setattr(bot, "now_epoch", lambda: epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(epoch))
    monkeypatch.setattr(bot, "single_call_reply", {**bot.single_call_reply, "enabled": True})
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 48)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 6)
    monkeypatch.setattr(bot, "MAX_MENTIONS_PER_CHECK", 5)
    monkeypatch.setattr(
        bot._hot_post_discovery, "get_hot_post_reply_candidates", Mock(return_value=[]),
    )
    monkeypatch.setattr(bot, "x_request", Mock(side_effect=AssertionError("unexpected provider request")))
    monkeypatch.setattr(bot, "create_post", Mock(side_effect=AssertionError("unexpected remote write")))
    patch_reply_context_method(
        monkeypatch, "build",
        lambda candidate, _state: PreparedReplyContext(
            unit_reply_context(
                target_id=candidate["id"], contribution=candidate["text"],
                target_author_id=candidate["author_id"],
            ),
            {},
        ),
    )
    evaluate = legacy_reply_evaluator(Mock(side_effect=editorial_no_reply))
    patch_reply_owner_method(monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate", evaluate)
    return evaluate


def configure_quote_cycle(monkeypatch):
    """Configure one quote-reply scenario with no unexpected provider access."""
    scenario = load_scenario(SCENARIOS / "quote_tweet_reply.json")
    original = scenario["tweets"]["900"]
    quotes = scenario["quote_tweets"]["900"]["data"]
    epoch = 2_000_000_000
    monkeypatch.setattr(bot, "now_epoch", lambda: epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(epoch))
    monkeypatch.setattr(bot, "single_call_reply", {**bot.single_call_reply, "enabled": True})
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    patch_reply_owner_method(
        monkeypatch, bot._quote_discovery.QuoteWatchPosts, "lookup",
        Mock(return_value=["900"]),
    )
    patch_tweet_lookup_method(monkeypatch, "get_cached", Mock(return_value=original))
    monkeypatch.setattr(assembly.ReplyAssembly, "get_quote_tweets_for_posts", Mock(return_value={"900": quotes}))
    media_context = Mock(return_value={})
    patch_reply_owner_method(monkeypatch, bot._reply_native_media.ReplyMedia, "context", media_context)
    monkeypatch.setattr(bot._reply_native_media.ReplyMedia, "_fixture_media_context", media_context, raising=False)
    monkeypatch.setattr(bot, "x_request", Mock(side_effect=AssertionError("unexpected provider request")))
    monkeypatch.setattr(bot, "create_post", Mock(side_effect=AssertionError("unexpected remote write")))
    return original, quotes


@pytest.fixture
def image_case():
    """Return a fresh HTTP image response and its bound candidate media context."""
    response = FakeHttpResponse(200, headers={"Content-Type": "image/png"})
    media = bot._reply_assembly()._reply_media_owner().context(
        {"id": "target", "_attached_media": [{
            "media_key": "native-photo", "type": "photo",
            "url": "http://127.0.0.1/media/native.png",
        }]},
        lane="mention", target_id="target",
    )
    return response, media
