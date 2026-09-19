"""Keep lookup failures out of terminal retirement and X write cooldowns."""

from __future__ import annotations

import copy
import json
from unittest.mock import Mock

import pytest

from tests.helpers.bot_runtime import SOURCE_GET_TWEET_BY_ID, bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401
from tests.helpers.mention_fixtures import mention, queue_active_mention
from tests.helpers.reply_evaluation import legacy_reply_evaluator
from tests.helpers.reply_fixtures import (
    configure_normal_cycle,
    configure_quote_cycle,
    unit_approved_reply,
)


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
@pytest.mark.parametrize("outcome", [
    "empty", "error_only", "null_data", "malformed_data", "malformed_envelope",
    "mismatched", "unavailable", "get_rate_limited", "post_rate_limited", "post_failed",
])
def test_reply_preflight_and_create_keep_separate_outcomes(monkeypatch, lane, outcome):
    """Exercise real lookup, lane, cooldown and receipt code with fake HTTP only."""
    source_request, source_create = bot.x_request, bot.create_post
    state = bot.default_state()
    if lane == "mention":
        configure_normal_cycle(monkeypatch)
        candidate = mention(105, 205)
        queue_active_mention(state, candidate, base_since_id="100")
        monkeypatch.setattr(bot, "get_mentions", Mock(return_value=[candidate]))
        run = bot.maybe_reply_to_mentions
        read_scope, read_epochs, read_until = "api", "x_error_epochs", "api_cooldown_until_epoch"
    else:
        _, candidates = configure_quote_cycle(monkeypatch)
        candidate = candidates[0]
        run = bot.maybe_reply_to_quote_tweets
        read_scope, read_epochs, read_until = "quote", "quote_x_error_epochs", "quote_api_cooldown_until_epoch"
    target = candidate["id"]
    post_failure = outcome.startswith("post_")
    permanent = outcome == "unavailable"
    current = bot.now_epoch()
    draft_before_lookup = []
    generate = Mock(side_effect=lambda context, *_args, **_kwargs: unit_approved_reply(context))
    monkeypatch.setattr(bot, "evaluate_single_call_reply", legacy_reply_evaluator(generate))
    monkeypatch.setattr(bot, "x_request", source_request)
    monkeypatch.setattr(bot, "create_post", Mock(wraps=source_create))
    monkeypatch.setattr(bot, "get_tweet_by_id", SOURCE_GET_TWEET_BY_ID)
    health = Mock(wraps=bot.record_api_error)
    monkeypatch.setattr(bot, "record_api_error", health)

    def respond(method, url, **kwargs):
        """Return controlled provider envelopes without opening a socket."""
        response = bot.requests.Response()
        response.status_code = 200
        if method == "GET":
            assert url == f"{bot.X_BASE}/2/tweets/{target}"
            draft_before_lookup.append(copy.deepcopy(state["pending_ai_reply_drafts"]))
            assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
            target_error = {"errors": [{
                "resource_type": "tweet", "parameter": "id", "resource_id": target,
                "title": "Not Found Error", "detail": f"Could not find tweet with id: [{target}].",
                "type": "https://api.twitter.com/2/problems/resource-not-found",
            }]}
            document = {
                "empty": {}, "error_only": target_error, "null_data": {"data": None},
                "malformed_data": {"data": []}, "malformed_envelope": [],
                "mismatched": {"data": {"id": "999999"}},
                "unavailable": target_error,
                "get_rate_limited": {"title": "Too Many Requests"},
            }.get(outcome, {"data": {"id": target}})
            if permanent:
                response.status_code = 404
            elif outcome == "get_rate_limited":
                response.status_code = 429
                response.headers["x-rate-limit-reset"] = str(current + 600)
        else:
            assert method == "POST" and post_failure
            assert url == f"{bot.X_BASE}/2/tweets"
            assert kwargs["json"]["reply"]["in_reply_to_tweet_id"] == target
            assert bot.load_confirmed_reply_receipt()[0] == "sending"
            response.status_code = 429 if outcome == "post_rate_limited" else 503
            document = {"title": "Too Many Requests" if response.status_code == 429 else "Service Unavailable"}
        response._content = json.dumps(document).encode()
        return response

    transport = Mock(side_effect=respond)
    monkeypatch.setattr(bot.requests, "request", transport)
    if post_failure:
        with pytest.raises(bot.AmbiguousRemotePostOutcome):
            run(state)
    else:
        expected = (bot.NORMAL_CHECK_STATUS_API_ERROR
                    if lane == "mention" and not permanent else "checked")
        assert run(state) == expected

    assert [call.args[0] for call in transport.call_args_list] == (["GET", "POST"] if post_failure else ["GET"])
    assert bot.create_post.call_count == int(post_failure)
    assert generate.call_count == 1
    assert state["daily_reply_count"] == state["daily_quote_reply_count"] == 0
    saved = json.loads(bot.STATE_FILE.read_text())
    if permanent:
        health.assert_not_called()
        assert not state.get("pending_ai_reply_drafts")
        assert state["reply_evaluation_records"][target]["reason"] == "x_target_unavailable_pre_send"
        if lane == "mention":
            assert target not in state["mention_pending_candidates"]
            assert target in state["replied_to_ids"]
            assert state["last_seen_mention_id"] == "100"
        else:
            assert target in state["seen_quote_post_ids"]
            assert target in state["skipped_quote_post_ids"]
            assert not state["replied_to_quote_post_ids"]
    else:
        assert state["pending_ai_reply_drafts"] == draft_before_lookup[0]
        assert saved["pending_ai_reply_drafts"] == draft_before_lookup[0]
        assert target not in state.get("reply_evaluation_records", {})
        if lane == "mention":
            assert target in state["mention_pending_candidates"]
            assert state["last_seen_mention_id"] == "100"
            assert target not in state["replied_to_ids"]
        else:
            assert target not in state["seen_quote_post_ids"]
            assert target not in state["skipped_quote_post_ids"]
            assert target not in state["replied_to_quote_post_ids"]
        assert health.call_count == 1
        assert health.call_args.kwargs["scope"] == ("write" if post_failure else read_scope)
        assert state["x_write_error_epochs" if post_failure else read_epochs] == [current]

    if outcome == "post_rate_limited":
        assert state["x_write_api_cooldown_until_epoch"] > current
    else:
        assert state["x_write_api_cooldown_until_epoch"] == 0
    assert state[read_until] == (current + 660 if outcome == "get_rate_limited" else 0)
    assert saved[read_until] == state[read_until]
    assert saved["x_write_api_cooldown_until_epoch"] == state["x_write_api_cooldown_until_epoch"]
    if post_failure:
        assert bot.load_confirmed_reply_receipt()[0] == "sending"
        assert bot.ambiguous_remote_post_is_blocking()
    else:
        assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
        assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
        assert not bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()
