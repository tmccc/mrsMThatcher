"""Keep fetched quote candidates durable until the reply cycle handles them."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import (
    install_receipt_bound_x_request_stub,
    isolate_bot_runtime,  # noqa: F401
)
from tests.helpers.reply_fixtures import (
    configure_quote_cycle,
    patch_reply_owner_method,
    patch_tweet_lookup_method,
    unit_approved_reply,
)


QUERY = "(quotes_of_tweet_id:900) -is:retweet"


def _install_quote_pages(monkeypatch, *, first_ids=("913", "912"), young_id=None,
                         missing_timestamp=False):
    """Run real discovery against synthetic pages and a controllable clock."""
    discovery = bot.get_quote_tweets_for_posts
    original, templates = configure_quote_cycle(monkeypatch)
    lookup = Mock(return_value=original)
    patch_tweet_lookup_method(monkeypatch, "get_cached", lookup)
    monkeypatch.setattr(bot, "get_quote_tweets_for_posts", discovery)
    monkeypatch.setattr(bot, "QUOTE_LOOKUP_MAX_PAGES_PER_POST", 1)
    monkeypatch.setattr(bot, "MAX_QUOTE_POSTS_PER_CHECK", 1)
    monkeypatch.setattr(bot, "QUOTE_REPLY_DELAY_SECONDS", 60)
    clock = [2_000_000_000]
    monkeypatch.setattr(bot, "now_epoch", lambda: clock[0])
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(clock[0]))

    def quote(target):
        tweet = copy.deepcopy(templates[0])
        tweet.update(id=target, conversation_id=target)
        if target == young_id:
            tweet["created_at"] = datetime.fromtimestamp(
                clock[0], timezone.utc,
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        if target == "912":
            tweet["attachments"] = {"media_keys": ["photo-912"]}
            if missing_timestamp:
                tweet.pop("created_at", None)
        return tweet

    pages = {
        None: {
            "data": [quote(target) for target in first_ids],
            "meta": {"next_token": "A"},
            "includes": {
                "users": [{"id": "310", "description": "Interested in politics"}],
                "media": [{
                    "media_key": "photo-912", "type": "photo",
                    "url": "https://example.invalid/quote-photo.jpg",
                }],
            },
        },
        "A": {"data": [quote("911")]},
    }

    def request(path, params):
        assert path == "/2/tweets/search/recent"
        assert params["query"] == QUERY
        return copy.deepcopy(pages[params.get("pagination_token")])

    search = Mock(side_effect=request)
    monkeypatch.setattr(bot, "x_quote_lookup_request", search)
    return search, clock, lookup


def _no_reply():
    return bot.PipelineResult(
        status="no_reply", reason="model_selected_no_reply", model_call_count=1,
    )


def test_model_failure_replays_pending_quotes_before_fetching_older_page(monkeypatch):
    search, _clock, lookup = _install_quote_pages(monkeypatch)
    evaluated = []

    def evaluate(context, _media, *, state):
        target = context["target_id"]
        evaluated.append(target)
        saved = json.loads(bot.STATE_FILE.read_text())
        assert target in saved["quote_pending_candidates"]
        if len(evaluated) == 1:
            assert set(saved["quote_pending_candidates"]) == {"912", "913"}
            assert saved["quote_search_pagination_tokens"] == {QUERY: "A"}
            return bot.PipelineResult(
                status="operational_failure", reason="provider_timeout",
                error_category="provider", model_call_count=1,
            )
        return _no_reply()

    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate", evaluate,
    )
    state = bot.default_state()
    assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_CHECKED
    assert evaluated == ["912"] and search.call_count == 1

    state = bot.load_state()
    pending = state["quote_pending_candidates"]
    assert set(pending) == {"912", "913"}
    assert pending["912"]["_author_user"]["description"] == "Interested in politics"
    assert pending["912"]["_attached_media"][0]["media_key"] == "photo-912"
    assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_CHECKED
    assert evaluated == ["912", "912"] and search.call_count == 1
    assert set(bot.load_state()["quote_pending_candidates"]) == {"913"}

    state = bot.load_state()
    assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_CHECKED
    assert evaluated == ["912", "912", "913"] and search.call_count == 1
    assert bot.load_state()["quote_pending_candidates"] == {}

    state = bot.load_state()
    assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_CHECKED
    assert evaluated == ["912", "912", "913", "911"]
    assert [call.args[1].get("pagination_token") for call in search.call_args_list] == [None, "A"]
    assert bot.load_state()["quote_pending_candidates"] == {}
    bot.create_post.assert_not_called()


@pytest.mark.parametrize("refresh", ["success", "missing", "malformed", "transient"])
def test_missing_creation_time_gets_bounded_refresh_without_stalling_queue(monkeypatch, refresh):
    search, _clock, lookup = _install_quote_pages(monkeypatch, missing_timestamp=True)
    state = bot.default_state()
    bot.get_quote_tweets_for_posts(["900"], state)
    state = bot.load_state()
    original = lookup.return_value
    fresh = dict(state["quote_pending_candidates"]["912"], created_at="2030-01-01T00:00:00Z")

    def fetch_original_or_refresh(target, current, *, include_media=False):
        assert current is state
        if target == "900":
            return original
        assert target == "912" and include_media
        if refresh == "transient":
            raise bot.ApiError("retry later", service="x", status_code=503)
        if refresh == "missing":
            return None
        return dict(fresh, created_at="invalid") if refresh == "malformed" else fresh

    lookup.side_effect = fetch_original_or_refresh
    evaluate = Mock(side_effect=lambda *_args, **_kwargs: _no_reply())
    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate", evaluate,
    )
    assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_CHECKED
    saved = bot.load_state()
    if refresh == "transient":
        evaluate.assert_not_called()
        assert set(saved["quote_pending_candidates"]) == {"912", "913"}
        assert "912" not in saved["skipped_quote_post_ids"]
    elif refresh == "success":
        assert evaluate.call_args.args[0]["target_id"] == "912"
        assert set(saved["quote_pending_candidates"]) == {"913"}
    else:
        assert evaluate.call_args.args[0]["target_id"] == "913"
        assert saved["quote_pending_candidates"] == {}
        assert "912" in saved["skipped_quote_post_ids"]
    assert search.call_count == 1
    bot.create_post.assert_not_called()


def test_unavailable_original_retires_its_queue_and_allows_later_discovery(monkeypatch):
    search, _clock, lookup = _install_quote_pages(monkeypatch)
    state = bot.default_state()
    bot.get_quote_tweets_for_posts(["900"], state)
    lookup.return_value = None
    evaluate = Mock()
    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate", evaluate,
    )
    assert bot.maybe_reply_to_quote_tweets(bot.load_state()) == bot.QUOTE_CHECK_STATUS_CHECKED
    saved = bot.load_state()
    assert saved["quote_pending_candidates"] == {}
    assert set(saved["skipped_quote_post_ids"]) == {"912", "913"}
    assert saved["quote_search_pagination_tokens"] == {QUERY: "A"}
    assert search.call_count == 1
    evaluate.assert_not_called()


def test_candidate_limit_preserves_untouched_quotes_across_restart(monkeypatch):
    search, _clock, lookup = _install_quote_pages(monkeypatch, first_ids=("914", "913", "912"))
    monkeypatch.setattr(bot, "MAX_QUOTE_POSTS_PER_CHECK", 2)
    evaluate = Mock(side_effect=lambda *_args, **_kwargs: _no_reply())
    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate", evaluate,
    )

    assert bot.maybe_reply_to_quote_tweets(bot.default_state()) == bot.QUOTE_CHECK_STATUS_CHECKED
    assert [call.args[0]["target_id"] for call in evaluate.call_args_list] == ["912", "913"]
    state = bot.load_state()
    assert set(state["quote_pending_candidates"]) == {"914"}
    assert state["quote_search_pagination_tokens"] == {QUERY: "A"}

    assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_CHECKED
    assert [call.args[0]["target_id"] for call in evaluate.call_args_list] == ["912", "913", "914"]
    assert search.call_count == 1
    assert bot.load_state()["quote_pending_candidates"] == {}
    bot.create_post.assert_not_called()


@pytest.mark.parametrize("recover_after_save_failure", [False, True], ids=["fresh", "recovered"])
def test_confirmed_post_retires_only_its_target_and_preserves_the_rest(monkeypatch, recover_after_save_failure):
    create_post = bot.create_post
    search, _clock, lookup = _install_quote_pages(monkeypatch)
    monkeypatch.setattr(bot, "MAX_QUOTE_POSTS_PER_CHECK", 3)
    monkeypatch.setattr(bot, "create_post", create_post)
    remote = Mock(return_value={"data": {"id": "950001"}})
    install_receipt_bound_x_request_stub(monkeypatch, remote)
    evaluated = []

    def evaluate(context, _media, *, state):
        evaluated.append(context["target_id"])
        if context["target_id"] == "912":
            return bot.PipelineResult(
                status="reply", reason="useful_reply",
                reply=unit_approved_reply(context), model_call_count=1,
            )
        return _no_reply()

    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate", evaluate,
    )
    if recover_after_save_failure:
        original_save = bot.save_state
        failure = OSError("confirmed quote state could not be saved")

        def save_before_confirmation(document, **kwargs):
            if remote.called:
                raise failure
            return original_save(document, **kwargs)

        monkeypatch.setattr(bot, "save_state", save_before_confirmation)
        with pytest.raises(OSError) as caught:
            bot.maybe_reply_to_quote_tweets(bot.default_state())
        assert caught.value is failure
        receipt_status, receipt = bot.load_confirmed_reply_receipt()
        assert receipt_status == "valid"
        assert receipt["target_id"] == "912" and receipt["reply_post_id"] == "950001"
        state = bot.load_state()
        assert set(state["quote_pending_candidates"]) == {"912", "913"}
        assert "912" not in state["replied_to_quote_post_ids"]
        assert state["daily_reply_count"] == state["daily_quote_reply_count"] == 0
        sibling = state["quote_pending_candidates"]["913"]
        monkeypatch.setattr(bot, "save_state", original_save)
        assert bot.reconcile_confirmed_reply_receipt(state) is True
        assert state["quote_pending_candidates"]["913"] is sibling
        assert set(bot.load_state()["quote_pending_candidates"]) == {"913"}
    else:
        assert bot.maybe_reply_to_quote_tweets(bot.default_state()) == bot.QUOTE_CHECK_STATUS_POSTED
        state = bot.load_state()
    assert evaluated == ["912"]
    assert set(state["quote_pending_candidates"]) == {"913"}
    assert "912" in state["replied_to_quote_post_ids"]
    assert state["daily_reply_count"] == state["daily_quote_reply_count"] == 1
    assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
    assert remote.call_count == search.call_count == 1

    assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_CHECKED
    assert evaluated == ["912", "913"]
    assert bot.load_state()["quote_pending_candidates"] == {}
    assert remote.call_count == search.call_count == 1


@pytest.mark.parametrize("watched_after_restart", [["901"], []], ids=["changed-watch", "empty-watch"])
def test_young_pending_quote_survives_restart_and_watch_changes(monkeypatch, watched_after_restart):
    search, clock, lookup = _install_quote_pages(monkeypatch, young_id="912")
    monkeypatch.setattr(bot, "MAX_QUOTE_POSTS_PER_CHECK", 3)
    evaluate = Mock(side_effect=lambda *_args, **_kwargs: _no_reply())
    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate", evaluate,
    )

    assert bot.maybe_reply_to_quote_tweets(bot.default_state()) == bot.QUOTE_CHECK_STATUS_CHECKED
    assert [call.args[0]["target_id"] for call in evaluate.call_args_list] == ["913"]
    state = bot.load_state()
    assert set(state["quote_pending_candidates"]) == {"912"}
    assert "912" not in state["skipped_quote_post_ids"]

    clock[0] += 60
    patch_reply_owner_method(
        monkeypatch, bot._quote_discovery.QuoteWatchPosts, "lookup",
        Mock(return_value=watched_after_restart),
    )
    lookup.reset_mock()
    assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_CHECKED
    assert [call.args[0]["target_id"] for call in evaluate.call_args_list] == ["913", "912"]
    assert all(call.args[0] == "900" for call in lookup.call_args_list)
    assert bot.load_state()["quote_pending_candidates"] == {}
    assert search.call_count == 1
    bot.create_post.assert_not_called()


def test_quote_owner_handoffs_keep_current_recovery_and_chronological_model_history(monkeypatch):
    """Use actual generation-through-completion owners without root adapters."""
    watch_type = bot._quote_discovery.QuoteWatchPosts
    watch_lookup = watch_type.lookup
    tweets_type = bot._tweet_lookup_cache.TweetLookupCache
    get_cached = tweets_type.get_cached
    media_type = bot._reply_native_media.ReplyMedia
    prepare_media = media_type.context
    create_post = bot.create_post
    search, _clock, lookup = _install_quote_pages(monkeypatch, first_ids=("912",))
    monkeypatch.setattr(bot, "create_post", create_post)
    monkeypatch.setattr(watch_type, "lookup", watch_lookup)
    monkeypatch.setattr(media_type, "context", prepare_media)
    original = lookup.return_value
    monkeypatch.setattr(tweets_type, "get_cached", get_cached)
    state = bot.default_state()

    def fetch_current(tweet_id, **_kwargs):
        if str(tweet_id) == "900":
            return original
        return state["quote_pending_candidates"][str(tweet_id)]

    fetch = Mock(side_effect=fetch_current)
    patch_tweet_lookup_method(monkeypatch, "fetch", fetch)
    state["recent_own_post_ids"] = ["900"]
    target_epoch = bot.parse_x_datetime_to_epoch(original["created_at"])
    state["ai_reply_history"] = [
        {
            "target_id": "801", "reply_post_id": "901", "author_id": "501",
            "candidate_source": "mention", "proposed_reply": "An earlier confirmed reply.",
            "reply_epoch": target_epoch - 100,
        },
        {
            "target_id": "802", "reply_post_id": "902", "author_id": "502",
            "candidate_source": "mention", "proposed_reply": "A later confirmed reply.",
            "reply_epoch": target_epoch + 100,
        },
    ]
    earlier = {"post_id": "901", "text": "An earlier confirmed reply."}
    later = {"post_id": "902", "text": "A later confirmed reply."}
    prepared = []
    collected_media = []
    def observe_media(owner, media_context):
        collected_media.append(media_context)
        return []

    monkeypatch.setattr(media_type, "collect", observe_media)
    contexts_type = bot._reply_context.ReplyContext
    build_quote = contexts_type.build_quote

    def observe_context(owner, original_tweet, quote):
        assert original_tweet["id"] == original["id"]
        assert original_tweet["text"] == original["text"]
        assert quote is state["quote_pending_candidates"]["912"]
        result = build_quote(owner, original_tweet, quote)
        prepared.append(result)
        return result

    monkeypatch.setattr(contexts_type, "build_quote", observe_context)
    recovered = []
    drafts_type = bot._reply_drafts.ReplyDrafts
    recover = drafts_type.recover

    def observe_recovery(owner, current, target_id, lane, *, context, recent_replies):
        assert current is state
        assert context is prepared[0].context
        assert (target_id, lane) == ("912", "quote_tweet")
        assert recent_replies == [earlier, later]
        result = recover(
            owner, current, target_id, lane,
            context=context, recent_replies=recent_replies,
        )
        recovered.append(result)
        return result

    monkeypatch.setattr(drafts_type, "recover", observe_recovery)

    def evaluate_pipeline(**kwargs):
        assert recovered == [None]
        assert kwargs["context"] is prepared[0].context
        assert kwargs["context"]["target_id"] == "912"
        assert kwargs["context"]["quoted_post_id"] == "900"
        assert kwargs["recent_account_replies"] == [earlier]
        assert kwargs["same_author_interactions"] == []
        assert kwargs["supplied_images"] == []
        assert isinstance(kwargs["transport"].__self__, bot._reply_model_transport.ReplyModelTransport)
        saved = json.loads(bot.STATE_FILE.read_text())
        assert set(saved["quote_pending_candidates"]) == {"912"}
        return bot.PipelineResult(
            status="reply", reason="useful_reply",
            reply=unit_approved_reply(kwargs["context"]), model_call_count=1,
        )

    pipeline = Mock(side_effect=evaluate_pipeline)
    monkeypatch.setattr(bot, "run_single_call_reply_pipeline", pipeline)
    remote = Mock(return_value={"data": {"id": "950912"}})
    install_receipt_bound_x_request_stub(monkeypatch, remote)
    relays = {}
    for name in (
        "build_quote_lookup_post_ids", "build_quote_tweet_reply_context",
        "cache_tweet", "get_tweet_by_id_cached",
        "evaluate_single_call_reply", "_record_single_call_result",
        "recovery_comparison_account_replies", "collect_reply_images",
        "openai_responses_reply_call",
        "reply_media_context_for_candidate",
        "load_confirmed_reply_receipt", "reconcile_confirmed_reply_receipt",
        "bind_conversational_reply_attempt_time",
        "reply_target_is_available_immediately_before_send",
        "post_conversational_reply_with_durable_identity",
        "apply_confirmed_reply_receipt",
    ):
        relays[name] = Mock(side_effect=AssertionError(f"root relay used: {name}"))
        monkeypatch.setattr(bot, name, relays[name], raising=False)

    assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_POSTED
    assert len(prepared) == pipeline.call_count == search.call_count == 1
    assert collected_media == [prepared[0].media_context]
    assert prepared[0].media_context == {
        "lane": "quote_tweet",
        "target_id": "912",
        "mode": "multimodal",
        "status": "supplied",
        "photos_expected": 1,
        "photos": [{
            "media_key": "photo-912",
            "url": "https://example.invalid/quote-photo.jpg",
            "attachment_role": "target_contribution",
            "source_post_id": "912",
        }],
    }
    assert state["tweet_cache"]["912"]["text"] == prepared[0].context["incoming_contribution"]
    assert state["quote_pending_candidates"] == {}
    assert "912" in state["replied_to_quote_post_ids"]
    assert state["daily_reply_count"] == state["daily_quote_reply_count"] == 1
    assert state["ai_reply_history"][-1]["reply_post_id"] == "950912"
    assert bot.load_state()["quote_pending_candidates"] == {}
    assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
    for relay in relays.values():
        relay.assert_not_called()
    assert remote.call_count == 1
