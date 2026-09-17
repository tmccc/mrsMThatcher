"""Regression tests for bot reply policy."""

from __future__ import annotations

from mrs_bot_reply_cycle_interfaces import PreparedReplyContext
from tests.helpers.reply_evaluation import legacy_reply_evaluator

import copy
import json
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.helpers.bot_runtime import (
    SCENARIOS,
    SOURCE_DEFAULT_SINGLE_CALL_REPLY,
    bot,
)
from tests.helpers.bot_fixtures import (
    _configure_test_x_base,
    isolate_bot_runtime,
    install_receipt_bound_x_request_stub,
)
from tests.helpers.reply_fixtures import (
    UNIT_REPLY_REPOSITORY,
    unit_reply_context,
    unit_approved_reply,
    unit_confirmed_reply_receipt,
    reply_evaluation_record,
)
from tests.fake_api_server import (
    FakeApiServer,
    load_scenario,
)
from single_call_reply import (
    STRATEGY_VERSION,
    ValidatedReply,
)


pytestmark = pytest.mark.allow_loopback_network


@pytest.mark.parametrize("field", ["enabled", "model", "strategy_version", "timeout_seconds"])
def test_single_call_reply_validator_rejects_invalid_fields(field: str) -> None:
    strategy = json.loads(json.dumps(bot.single_call_reply))
    strategy[field] = {
        "enabled": "yes",
        "model": "another-model",
        "strategy_version": "legacy",
        "timeout_seconds": 0,
    }[field]

    errors = bot.validate_runtime_config_values({"single_call_reply": strategy})

    assert errors


def test_single_call_reply_source_defaults_remain_disabled() -> None:
    example = json.loads(Path("mrsMThatcher.local.example.json").read_text(encoding="utf-8"))

    assert SOURCE_DEFAULT_SINGLE_CALL_REPLY["enabled"] is False
    assert example["single_call_reply"]["enabled"] is False


def test_disabled_single_call_reply_skips_mention_lane_before_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    disabled = copy.deepcopy(bot.single_call_reply)
    disabled["enabled"] = False
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "single_call_reply", disabled)
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)
    monkeypatch.setattr(
        bot,
        "get_mentions",
        lambda _state: pytest.fail("disabled strategy must not discover mentions"),
    )
    monkeypatch.setattr(
        bot,
        "get_hot_post_reply_candidates",
        lambda _state: pytest.fail("disabled strategy must not discover hot-post replies"),
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_DISABLED
    assert state.get("reply_evaluation_records", {}) == {}


def test_disabled_single_call_reply_skips_quote_lane_before_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    disabled = copy.deepcopy(bot.single_call_reply)
    disabled["enabled"] = False
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
    monkeypatch.setattr(bot, "single_call_reply", disabled)
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)
    monkeypatch.setattr(
        bot,
        "lane_paused",
        lambda *_args, **_kwargs: pytest.fail("disabled strategy must stop before quote-tweet lane work"),
    )

    assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_DISABLED
    assert state.get("reply_evaluation_records", {}) == {}


def test_operational_pipeline_failure_does_not_consume_mention_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_reply_epoch"] = 0
    mention = {
        "id": "100",
        "author_id": "200",
        "text": "@MrsMThatcher A substantive question?",
        "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
        "conversation_id": "100",
        "referenced_tweets": [],
    }
    def fail_operationally(
        _context: dict,
        *_args: object,
        evaluation_outcome: dict[str, str],
        **_kwargs: object,
    ) -> None:
        evaluation_outcome.update({
            "status": "operational_failure",
            "reason": "provider_request_failed",
            "error_category": "provider_transport",
            "model_call_count": 1,
        })
        return None

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "MY_USERNAME", "MrsMThatcher")
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "get_mentions", lambda _state: [dict(mention)])
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", lambda _text: False)
    monkeypatch.setattr(
        bot,
        "build_context_for_reply_ai",
        lambda *_args: PreparedReplyContext(unit_reply_context(contribution=mention["text"]), {}),
    )
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: UNIT_REPLY_REPOSITORY)
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(bot, "evaluate_single_call_reply", legacy_reply_evaluator(fail_operationally))
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_API_ERROR
    assert state.get("reply_evaluation_records", {}) == {}
    assert state.get("last_seen_mention_id") is None


def test_single_call_config_rejects_reply_length_tuning_knob() -> None:
    config = copy.deepcopy(bot.single_call_reply)
    config["MAX_REPLY_CHARS"] = 281
    errors = bot.validate_runtime_config_values({"single_call_reply": config})

    assert errors


@pytest.mark.parametrize("value", [True, 270.0, "270"])
def test_runtime_config_requires_integer_reply_length(value: object) -> None:
    config = copy.deepcopy(bot.single_call_reply)
    config["MAX_REPLY_CHARS"] = value
    errors = bot.validate_runtime_config_values({"single_call_reply": config})

    assert errors


@pytest.mark.parametrize("candidate_source", ["mention", "hot_post_reply"])
def test_own_historical_context_reply_is_never_processed_as_incoming_reply(
    monkeypatch: pytest.MonkeyPatch,
    candidate_source: str,
) -> None:
    state = bot.default_state()
    state["last_reply_epoch"] = 0
    own_context_reply = {
        "id": "123456",
        "author_id": str(bot.MY_USER_ID),
        "text": "Context\nSpoken during: A speech.\n\nVerification: Exact wording",
        "conversation_id": "654321",
        "referenced_tweets": [{"type": "replied_to", "id": "654321"}],
        "_source": candidate_source,
        "_hot_original_post_id": "654321",
    }

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "get_mentions", lambda state: [own_context_reply] if candidate_source == "mention" else [])
    monkeypatch.setattr(
        bot,
        "get_hot_post_reply_candidates",
        lambda state: [own_context_reply] if candidate_source == "hot_post_reply" else [],
    )
    monkeypatch.setattr(
        bot,
        "build_context_for_reply_ai",
        lambda *args, **kwargs: pytest.fail("own context reply must not be sent to xAI"),
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda *args, **kwargs: pytest.fail("own context reply must not receive another X reply"),
    )
    monkeypatch.setattr(bot, "save_state", lambda *args, **kwargs: None)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert state["daily_reply_count"] == 0
    assert state["replied_to_ids"] == []


def test_truncated_pagination_no_reply_is_not_evaluated_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_reply_epoch"] = 0
    mention = {
        "id": "100",
        "author_id": "200",
        "text": "@MrsMThatcher Yep",
        "conversation_id": "100",
        "referenced_tweets": [],
        "_pagination_truncated": True,
    }
    calls: list[str] = []

    def no_reply(_context: dict, *_args: object, evaluation_outcome=None, **_kwargs: object):
        calls.append("sol")
        evaluation_outcome.update({
            "status": "no_reply",
            "reason": "completed_exchange",
            "reason_code": "completed_exchange",
            "model_call_count": 1,
        })
        return None

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "get_mentions", lambda _state: [dict(mention)])
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", lambda _text: False)
    context = unit_reply_context(target_id="100", contribution=mention["text"])
    monkeypatch.setattr(bot, "build_context_for_reply_ai", lambda *_args: PreparedReplyContext(context, {}))
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(bot, "evaluate_single_call_reply", legacy_reply_evaluator(no_reply))
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED

    assert calls == ["sol"]
    assert state["reply_evaluation_records"]["100"]["outcome"] == "no_reply"


def test_local_validation_failure_is_terminal_and_does_not_block_later_mention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["daily_reply_count"] = 2
    mentions = [
        {
            "id": target_id,
            "author_id": author_id,
            "text": text,
            "conversation_id": target_id,
            "referenced_tweets": [],
        }
        for target_id, author_id, text in (
            ("101", "201", "@MrsMThatcher A mechanically invalid answer"),
            ("102", "202", "@MrsMThatcher A later eligible contribution"),
        )
    ]
    calls: list[str] = []

    def decide(context: dict, *_args: object, evaluation_outcome=None, **_kwargs: object):
        target_id = str(context["target_id"])
        calls.append(target_id)
        if target_id == "101":
            evaluation_outcome.update({
                "status": "operational_failure",
                "reason": "model_response_validation_failed",
                "error_category": "local_validation",
                "model_call_count": 1,
            })
        else:
            evaluation_outcome.update({
                "status": "no_reply",
                "reason": "completed_exchange",
                "reason_code": "completed_exchange",
                "model_call_count": 1,
            })
        return None

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        bot,
        "get_mentions",
        lambda _state: [dict(candidate) for candidate in mentions],
    )
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", lambda _text: False)
    monkeypatch.setattr(
        bot,
        "build_context_for_reply_ai",
        lambda candidate, _state: PreparedReplyContext(
            unit_reply_context(
                target_id=str(candidate["id"]),
                contribution=str(candidate["text"]),
                target_author_id=str(candidate["author_id"]),
            ),
            {},
        ),
    )
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(bot, "evaluate_single_call_reply", legacy_reply_evaluator(decide))
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    state["daily_reply_date"] = bot.reply_cap_date_str(1_800_000_000)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED

    assert calls == ["101", "102"]
    assert state["reply_evaluation_records"]["101"]["outcome"] == (
        "operational_failure"
    )
    assert state["reply_evaluation_records"]["102"]["outcome"] == "no_reply"
    assert state["openai_error_epochs"] == []
    assert state["openai_api_cooldown_until_epoch"] == 0
    assert state["daily_reply_count"] == 2
    assert state["author_evaluation_quarantines"] == {}


def test_strategy_persistence_failure_blocks_quote_tweet_x_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_scenario(SCENARIOS / "quote_tweet_reply.json")
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        _configure_test_x_base(monkeypatch, server.url)
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_QUOTE_REPLIES_PER_DAY", 10)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "QUOTE_REPLY_DELAY_SECONDS", 0)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))
        monkeypatch.setattr(
            bot,
            "get_tweet_by_id",
            lambda tweet_id, **_kwargs: copy.deepcopy(
                scenario["tweets"].get(str(tweet_id))
            ),
        )
        monkeypatch.setattr(
            bot,
            "evaluate_single_call_reply",
            legacy_reply_evaluator(lambda context, *_args, **_kwargs: unit_approved_reply(
                context,
                text="Conviction matters more than applause.",
                mode="opinion_or_principle",
            )),
        )
        monkeypatch.setattr(bot, "store_pending_ai_reply", lambda *_args, **_kwargs: False)
        monkeypatch.setattr(
            bot,
            "create_post",
            lambda *_args, **_kwargs: pytest.fail("X write must not be called"),
        )
        monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

        state = bot.default_state()
        state["recent_own_post_ids"] = ["900"]
        state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")
        state["daily_quote_reply_date"] = state["daily_reply_date"]

        assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_CHECKED
        assert state["daily_reply_count"] == 0
        assert "910" not in state["seen_quote_post_ids"]
    finally:
        server.stop()


def test_quote_tweet_model_no_reply_is_durable_beyond_bounded_scan_lists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    state = bot.default_state()
    state["recent_own_post_ids"] = ["900"]
    state["daily_reply_date"] = datetime.fromtimestamp(fixed_epoch).strftime("%Y-%m-%d")
    state["daily_quote_reply_date"] = state["daily_reply_date"]
    own_post = {
        "id": "900", "author_id": "12345", "text": "An original post.",
        "conversation_id": "900", "referenced_tweets": [],
    }
    quote_post = {
        "id": "910", "author_id": "777", "text": "A substantive but unproductive claim.",
        "conversation_id": "910",
        "referenced_tweets": [{"type": "quoted", "id": "900"}],
    }
    model_calls: list[str] = []

    def no_reply(*_args: object, **kwargs: object) -> None:
        model_calls.append("called")
        outcome = kwargs.get("evaluation_outcome")
        if isinstance(outcome, dict):
            outcome.update({
                "status": "no_reply",
                "reason": "unsupported_or_unverifiable",
                "reason_code": "unsupported_or_unverifiable",
                "model_call_count": 1,
            })
        return None

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
    monkeypatch.setattr(bot, "MAX_QUOTE_REPLIES_PER_DAY", 10)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))
    monkeypatch.setattr(bot, "lane_paused", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "reconcile_confirmed_reply_receipt", lambda _state: False)
    monkeypatch.setattr(bot, "build_quote_lookup_post_ids", lambda _state: ["900"])
    monkeypatch.setattr(bot, "get_tweet_by_id_cached", lambda *_args, **_kwargs: dict(own_post))
    monkeypatch.setattr(bot, "get_quote_tweets_for_posts", lambda *_args, **_kwargs: {"900": [dict(quote_post)]})
    monkeypatch.setattr(bot, "quote_tweet_is_old_enough", lambda _tweet: True)
    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", lambda _text: False)
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(bot, "evaluate_single_call_reply", legacy_reply_evaluator(no_reply))
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_CHECKED
    state["seen_quote_post_ids"] = []
    state["skipped_quote_post_ids"] = []
    assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_CHECKED

    assert model_calls == ["called"]
    assert state["reply_evaluation_records"]["910"]["outcome"] == "no_reply"


def test_quote_tweet_generic_403_remains_ambiguous_and_durable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    state = bot.default_state()
    state["recent_own_post_ids"] = ["900"]
    state["daily_reply_date"] = datetime.fromtimestamp(fixed_epoch).strftime("%Y-%m-%d")
    state["daily_quote_reply_date"] = state["daily_reply_date"]
    own_post = {
        "id": "900", "author_id": "12345", "text": "An original post.",
        "conversation_id": "900", "referenced_tweets": [],
    }
    quote_post = {
        "id": "910", "author_id": "777", "text": "A substantive comment.",
        "conversation_id": "910",
        "referenced_tweets": [{"type": "quoted", "id": "900"}],
    }
    model_calls: list[str] = []
    post_calls: list[str] = []

    def reply(context: dict, *_args: object, **_kwargs: object) -> ValidatedReply:
        model_calls.append("called")
        return unit_approved_reply(
            context,
            text="Conviction still matters.",
            mode="opinion_or_principle",
        )

    def forbidden_post(*_args: object, **_kwargs: object) -> dict:
        post_calls.append("called")
        raise bot.ApiError(
            "You can only reply to or quote posts where you are mentioned or are the author",
            service="x",
            status_code=403,
        )

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
    monkeypatch.setattr(bot, "MAX_QUOTE_REPLIES_PER_DAY", 10)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))
    monkeypatch.setattr(bot, "lane_paused", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "reconcile_confirmed_reply_receipt", lambda _state: False)
    monkeypatch.setattr(bot, "build_quote_lookup_post_ids", lambda _state: ["900"])
    monkeypatch.setattr(bot, "get_tweet_by_id_cached", lambda *_args, **_kwargs: dict(own_post))
    monkeypatch.setattr(bot, "get_quote_tweets_for_posts", lambda *_args, **_kwargs: {"900": [dict(quote_post)]})
    monkeypatch.setattr(bot, "quote_tweet_is_old_enough", lambda _tweet: True)
    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", lambda _text: False)
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(bot, "evaluate_single_call_reply", legacy_reply_evaluator(reply))
    monkeypatch.setattr(bot, "create_post", forbidden_post)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.maybe_reply_to_quote_tweets(state)

    assert model_calls == ["called"]
    assert post_calls == ["called"]
    status, receipt = bot.load_confirmed_reply_receipt()
    assert status == "sending"
    assert receipt is not None
    assert receipt["target_id"] == "910"
    assert state.get("reply_evaluation_records", {}).get("910", {}).get("outcome") != (
        "reply_not_permitted"
    )


def test_same_thread_clarification_at_author_cap_is_skipped_before_model_or_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    state = bot.default_state()
    state["daily_reply_date"] = datetime.fromtimestamp(
        fixed_epoch, ZoneInfo("Europe/London")
    ).strftime("%Y-%m-%d")
    state["daily_reply_count"] = 6
    state["daily_replied_author_ids"] = ["200"]
    state["daily_replied_author_counts"] = {"200": 6}
    state["own_auto_reply_ids"] = ["900"]
    state["tweet_cache"] = {
        "100": {
            "id": "100", "author_id": "200", "conversation_id": "700",
            "text": "@MrsMThatcher Where did people run towards when the Berlin Wall fell?",
            "referenced_tweets": [{"type": "replied_to", "id": "700"}],
        },
        "900": {
            "id": "900", "author_id": "12345", "conversation_id": "700",
            "text": "When free to choose, people choose freedom.",
            "post_type": "auto_reply",
            "referenced_tweets": [{"type": "replied_to", "id": "100"}],
        },
    }
    correction = {
        "id": "101", "author_id": "200", "conversation_id": "700",
        "text": "@MrsMThatcher That did not answer my question.",
        "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
        "referenced_tweets": [{"type": "replied_to", "id": "900"}],
    }
    current_candidates = [correction]
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    enabled_strategy = copy.deepcopy(bot.single_call_reply)
    enabled_strategy["enabled"] = True
    monkeypatch.setattr(bot, "single_call_reply", enabled_strategy)
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MARK_AI_REPLIES_AS_AI", False)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 48)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 6)
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "get_mentions", lambda _state: list(current_candidates))
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", lambda _text: False)
    monkeypatch.setattr(
        bot,
        "build_context_for_reply_ai",
        lambda *_args, **_kwargs: pytest.fail("context/model work must not start"),
    )
    monkeypatch.setattr(
        bot,
        "evaluate_single_call_reply",
        legacy_reply_evaluator(lambda *_args, **_kwargs: pytest.fail("model must not be called")),
    )
    install_receipt_bound_x_request_stub(
        monkeypatch,
        lambda *_args, **_kwargs: pytest.fail("posting API must not be called"),
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert state["daily_reply_count"] == 6
    assert state["daily_replied_author_counts"]["200"] == 6
    assert "700" not in state.get("clarification_reply_records", {})


def test_author_cap_context_is_terminal_but_available_to_next_eligible_reply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert bot.MAX_REPLIES_PER_AUTHOR_PER_DAY == 6
    clock = [2_000_000_000]
    state = bot.default_state()
    state["daily_reply_date"] = datetime.fromtimestamp(clock[0]).strftime("%Y-%m-%d")
    state["daily_reply_count"] = 6
    state["daily_replied_author_ids"] = ["200"]
    state["daily_replied_author_counts"] = {"200": 6}
    state["tweet_cache"] = {
        "100": {
            "id": "100",
            "text_is_complete": True, "cached_epoch": clock[0],
            "author_id": "12345",
            "conversation_id": "100",
            "text": "The opening contribution.",
            "referenced_tweets": [],
        },
    }
    capped = {
        "id": "200",
        "author_id": "200",
        "conversation_id": "100",
        "text": "Please also account for the effect on small businesses.",
        "created_at": "2026-01-01T12:00:00Z",
        "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
        "referenced_tweets": [{"type": "replied_to", "id": "100"}],
    }
    eligible = {
        "id": "201",
        "author_id": "200",
        "conversation_id": "100",
        "text": "What practical policy follows from that?",
        "created_at": "2026-01-02T12:00:00Z",
        "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
        "referenced_tweets": [{"type": "replied_to", "id": "100"}],
    }
    current_candidates = [capped]
    ai_contexts: list[dict[str, object]] = []

    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    enabled_strategy = copy.deepcopy(bot.single_call_reply)
    enabled_strategy["enabled"] = True
    monkeypatch.setattr(bot, "single_call_reply", enabled_strategy)
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MARK_AI_REPLIES_AS_AI", False)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 48)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 6)
    monkeypatch.setattr(bot, "now_epoch", lambda: clock[0])
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(clock[0]))
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "get_mentions", lambda _state: list(current_candidates))
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", lambda _text: False)
    monkeypatch.setattr(
        bot,
        "get_tweet_by_id",
        lambda tweet_id, **_kwargs: (
            {"id": "201"}
            if str(tweet_id) == "201"
            else pytest.fail("parent context must use tweet_cache")
        ),
    )
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", lambda *_args, **_kwargs: {})

    def answer(context: dict[str, object], *_args: object, **_kwargs: object) -> ValidatedReply:
        ai_contexts.append(context)
        return unit_approved_reply(context, text="A practical policy answer.")

    monkeypatch.setattr(bot, "evaluate_single_call_reply", legacy_reply_evaluator(answer))
    install_receipt_bound_x_request_stub(
        monkeypatch,
        lambda *_args, **_kwargs: {"data": {"id": "900001"}},
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert ai_contexts == []
    assert state["last_seen_mention_id"] == "200"
    assert state["tweet_cache"]["200"]["post_type"] == "author_cap_context"

    clock[0] += 24 * 60 * 60
    current_candidates[:] = [eligible]
    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_POSTED
    assert len(ai_contexts) == 1
    assert ai_contexts[0]["target_id"] == "201"
    assert [post["post_id"] for post in ai_contexts[0]["parent_thread"]] == ["100"]
    assert [
        turn["post_id"] for turn in ai_contexts[0]["visible_conversation"]
    ] == ["100", "201"]
    assert state["replied_to_ids"] == ["201"]


def test_unrelated_follow_up_does_not_bypass_author_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    state = bot.default_state()
    state["daily_reply_date"] = datetime.fromtimestamp(fixed_epoch).strftime("%Y-%m-%d")
    state["daily_reply_count"] = 1
    state["daily_replied_author_counts"] = {"200": 1}
    state["own_auto_reply_ids"] = ["900"]
    state["tweet_cache"] = {
        "100": {
            "id": "100", "author_id": "200", "conversation_id": "700",
            "text": "@MrsMThatcher Where did people run towards when the Berlin Wall fell?",
            "referenced_tweets": [],
        },
        "900": {
            "id": "900", "author_id": "12345", "conversation_id": "700",
            "text": "A prior reply.", "post_type": "auto_reply",
            "referenced_tweets": [{"type": "replied_to", "id": "100"}],
        },
    }
    follow_up = {
        "id": "101", "author_id": "200", "conversation_id": "700",
        "text": "@MrsMThatcher What is your favourite film?",
        "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
        "referenced_tweets": [{"type": "replied_to", "id": "900"}],
    }
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 1)
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "get_mentions", lambda _state: [follow_up])
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(bot, "evaluate_single_call_reply", legacy_reply_evaluator(lambda *_args, **_kwargs: pytest.fail("xAI must not be called")))
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED


def test_clarification_ledger_survives_state_restart_and_blocks_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    state = bot.default_state()
    clarification_request = {
        "original_question": "Where did people move when the Berlin Wall fell?",
        "correction": "That did not answer my question.",
    }
    receipt = unit_confirmed_reply_receipt(
        target_id="101",
        reply_post_id="900001",
        author_id="200",
        contribution=clarification_request["correction"],
        text="People moved from East Berlin towards West Berlin.",
        epoch=fixed_epoch,
        factual=True,
        clarification_request=clarification_request,
        conversation_id="700",
    )
    receipt["clarification_reply"] = {
        "thread_id": "700",
        "prior_bot_reply_id": "900",
        "original_question_id": "100",
        "trigger": "explicit_correction",
    }
    assert bot.confirmed_reply_receipt_is_semantically_valid(receipt) is True
    bot.apply_confirmed_reply_receipt(state, receipt)
    bot.save_state(state, durable=True)

    recovered = bot.load_state()
    candidate = {"id": "102", "conversation_id": "700"}
    assert recovered["clarification_reply_records"]["700"]["clarification_reply_used"] is True
    assert bot.clarification_thread_is_terminal(recovered, candidate) is True
    assert bot.author_used_clarification_recently(recovered, "200", current=fixed_epoch + 60) is True


def test_author_clarification_window_expires_at_exactly_24_hours() -> None:
    completed_epoch = 2_000_000_000
    state = bot.default_state()
    state["clarification_reply_records"] = {
        "700": {
            "thread_id": "700",
            "author_id": "200",
            "completed_epoch": completed_epoch,
            "status": "repair_reply_completed",
            "clarification_reply_used": True,
            "thread_terminal": True,
        }
    }

    assert bot.author_used_clarification_recently(
        state,
        "200",
        current=completed_epoch + bot.CLARIFICATION_REPLY_WINDOW_SECONDS - 1,
    ) is True
    assert bot.author_used_clarification_recently(
        state,
        "200",
        current=completed_epoch + bot.CLARIFICATION_REPLY_WINDOW_SECONDS,
    ) is False


def test_completed_clarification_thread_stays_terminal_after_restart_and_cap_reset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed_epoch = 2_000_000_000
    later_epoch = completed_epoch + (2 * 24 * 60 * 60)
    state = bot.default_state()
    clarification_request = {
        "original_question": "Where did people move when the Berlin Wall fell?",
        "correction": "That did not answer my question.",
    }
    receipt = unit_confirmed_reply_receipt(
        target_id="101",
        reply_post_id="900001",
        author_id="200",
        contribution=clarification_request["correction"],
        text="People moved from East Berlin towards West Berlin.",
        epoch=completed_epoch,
        factual=True,
        clarification_request=clarification_request,
        conversation_id="700",
    )
    receipt["clarification_reply"] = {
        "thread_id": "700",
        "prior_bot_reply_id": "900",
        "original_question_id": "100",
        "trigger": "explicit_correction",
    }
    bot.apply_confirmed_reply_receipt(state, receipt)
    bot.save_state(state, durable=True)
    recovered = bot.load_state()

    terminal_thread_candidate = {
        "id": "102", "author_id": "200", "conversation_id": "700",
        "text": "@MrsMThatcher What happened next?",
        "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
        "referenced_tweets": [{"type": "replied_to", "id": "900001"}],
    }
    unrelated_thread_candidate = {
        "id": "103", "author_id": "200", "conversation_id": "800",
        "text": "@MrsMThatcher A thoughtful observation in a different thread.",
        "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
        "referenced_tweets": [{"type": "replied_to", "id": "800"}],
    }
    context_ids: list[str] = []
    media_ids: list[str] = []
    model_contexts: list[str] = []

    def build_context(candidate: dict, _state: dict) -> PreparedReplyContext:
        context_ids.append(str(candidate["id"]))
        return PreparedReplyContext(
            unit_reply_context(
                target_id=str(candidate["id"]),
                thread_id=str(candidate["conversation_id"]),
                contribution=str(candidate["text"]),
            ),
            bot.reply_media_context_for_candidate(
                candidate, lane="mention", target_id=str(candidate["id"]),
            ),
        )

    def prepare_media(candidate: dict, **_kwargs: object) -> dict:
        media_ids.append(str(candidate["id"]))
        return {}

    def answer(context: dict[str, object], *_args: object, **_kwargs: object) -> ValidatedReply:
        model_contexts.append(str(context["target_id"]))
        return unit_approved_reply(context, text="Quite so.", mode="courtesy")

    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MARK_AI_REPLIES_AS_AI", False)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 1)
    monkeypatch.setattr(bot, "now_epoch", lambda: later_epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(later_epoch))
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        bot,
        "get_mentions",
        lambda _state: [terminal_thread_candidate, unrelated_thread_candidate],
    )
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", lambda _text: False)
    monkeypatch.setattr(bot, "build_context_for_reply_ai", build_context)
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", prepare_media)
    monkeypatch.setattr(bot, "evaluate_single_call_reply", legacy_reply_evaluator(answer))
    install_receipt_bound_x_request_stub(
        monkeypatch,
        lambda *_args, **_kwargs: {"data": {"id": "900002"}},
    )

    assert bot.maybe_reply_to_mentions(recovered) == bot.NORMAL_CHECK_STATUS_POSTED
    assert context_ids == ["103"]
    assert media_ids == ["103"]
    assert model_contexts == ["103"]
    assert recovered["daily_reply_count"] == 1
    assert recovered["daily_replied_author_counts"] == {"200": 1}
    assert recovered["clarification_reply_records"]["700"]["thread_terminal"] is True


def test_completed_clarification_threads_are_not_evicted_from_terminal_ledger() -> None:
    state = bot.default_state()
    state["clarification_reply_records"] = {
        str(thread_id): {
            "thread_id": str(thread_id),
            "author_id": str(thread_id),
            "reply_post_id": str(900_000 + thread_id),
            "completed_epoch": thread_id,
            "status": "repair_reply_completed",
            "clarification_reply_used": True,
            "thread_terminal": True,
        }
        for thread_id in range(1, 2001)
    }
    clarification_request = {
        "original_question": "What happened?",
        "correction": "That did not answer the question.",
    }
    receipt = unit_confirmed_reply_receipt(
        target_id="3001",
        reply_post_id="903001",
        author_id="3001",
        contribution=clarification_request["correction"],
        text="People moved from East Germany towards West Germany in November 1989.",
        epoch=3001,
        factual=True,
        clarification_request=clarification_request,
        conversation_id="3001",
    )
    receipt["clarification_reply"] = {
        "thread_id": "3001",
        "prior_bot_reply_id": "903000",
        "original_question_id": "3000",
        "trigger": "explicit_correction",
    }

    bot.apply_confirmed_reply_receipt(state, receipt)

    assert "1" in state["clarification_reply_records"]
    assert "3001" in state["clarification_reply_records"]
    assert len(state["clarification_reply_records"]) == 2001


def test_quote_tweet_missing_created_at_is_not_old_enough() -> None:
    assert bot.quote_tweet_is_old_enough({"id": "123"}) is False
    assert bot.quote_tweet_is_old_enough({"id": "123", "created_at": "not a date"}) is False


def test_completed_reply_target_ledger_never_evicts_old_ids() -> None:
    existing = ["oldest", *(str(index) for index in range(2500))]
    updated = bot.append_unique_durable(existing, "newest")

    assert updated[0] == "oldest"
    assert updated[-1] == "newest"
    assert len(updated) == 2502
    assert bot.append_unique_durable(updated, "oldest") == updated


def test_quote_tweet_completed_ledger_is_not_a_bounded_seen_cache() -> None:
    state = bot.default_state()
    state["replied_to_quote_post_ids"] = ["oldest", *(str(index) for index in range(2500))]

    bot.mark_quote_tweet_replied(state, "newest")

    assert "oldest" in state["replied_to_quote_post_ids"]
    assert state["replied_to_quote_post_ids"][-1] == "newest"
    assert len(state["replied_to_quote_post_ids"]) == 2502


def test_recent_reply_evaluations_survive_nominal_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "REPLY_EVALUATION_MAX_RECORDS", 2)
    monkeypatch.setattr(bot, "REPLY_EVALUATION_MIN_RETENTION_SECONDS", 100)
    warnings: list[str] = []
    monkeypatch.setattr(
        bot.log,
        "warning",
        lambda message, *args: warnings.append(message % args),
    )
    state = bot.default_state()
    state["reply_evaluation_records"] = {
        target_id: reply_evaluation_record(target_id, epoch)
        for target_id, epoch in (("a", 950), ("b", 960), ("c", 970))
    }

    bot.prune_reply_evaluation_records(state, current_epoch=1000)

    assert list(state["reply_evaluation_records"]) == ["a", "b", "c"]
    assert any("retaining all protected records" in warning for warning in warnings)


def test_old_reply_evaluation_overflow_prunes_oldest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "REPLY_EVALUATION_MAX_RECORDS", 3)
    monkeypatch.setattr(bot, "REPLY_EVALUATION_MIN_RETENTION_SECONDS", 100)
    state = {
        "reply_evaluation_records": {
            target_id: reply_evaluation_record(target_id, epoch)
            for target_id, epoch in (("e", 5), ("a", 1), ("d", 4), ("b", 2), ("c", 3))
        }
    }

    bot.prune_reply_evaluation_records(state, current_epoch=1000)

    assert list(state["reply_evaluation_records"]) == ["c", "d", "e"]


def test_reply_evaluation_ties_are_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "REPLY_EVALUATION_MAX_RECORDS", 2)
    monkeypatch.setattr(bot, "REPLY_EVALUATION_MIN_RETENTION_SECONDS", 100)
    first = {
        "reply_evaluation_records": {
            target_id: reply_evaluation_record(target_id, 1)
            for target_id in ("c", "a", "b")
        }
    }
    second = {
        "reply_evaluation_records": {
            target_id: reply_evaluation_record(target_id, 1)
            for target_id in ("b", "c", "a")
        }
    }

    bot.prune_reply_evaluation_records(first, current_epoch=1000)
    bot.prune_reply_evaluation_records(second, current_epoch=1000)

    assert list(first["reply_evaluation_records"]) == ["b", "c"]
    assert first["reply_evaluation_records"] == second["reply_evaluation_records"]


def test_recorded_terminal_reply_evaluation_remains_replay_protection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "REPLY_EVALUATION_MAX_RECORDS", 2)
    monkeypatch.setattr(bot, "REPLY_EVALUATION_MIN_RETENTION_SECONDS", 100)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1000)
    state = {
        "reply_evaluation_records": {
            target_id: reply_evaluation_record(target_id, epoch)
            for target_id, epoch in (("oldest", 1), ("older", 2))
        }
    }
    bot.record_terminal_reply_evaluation(
        state,
        target_id="newest",
        lane="mention",
        reason="terminal",
    )

    assert bot.terminal_reply_evaluation(state, "oldest") is None
    assert bot.terminal_reply_evaluation(state, "newest") is not None
    assert list(state["reply_evaluation_records"]) == ["older", "newest"]


def test_normalised_loaded_state_prunes_oversized_reply_evaluations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(bot, "REPLY_EVALUATION_MAX_RECORDS", 2)
    monkeypatch.setattr(bot, "REPLY_EVALUATION_MIN_RETENTION_SECONDS", 100)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1000)
    state = {
        "reply_evaluation_records": {
            target_id: reply_evaluation_record(target_id, epoch)
            for target_id, epoch in (("one", 1), ("three", 3), ("two", 2))
        }
    }

    normalised = bot.normalise_state_candidate(state, path=tmp_path / "bot_state.json")

    assert normalised is not None
    assert list(normalised["reply_evaluation_records"]) == ["two", "three"]


def test_reply_daily_cap_dates_ignore_ambient_timezone_and_reset_authors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    epoch = int(datetime(2026, 7, 1, 0, 30, tzinfo=ZoneInfo("Europe/London")).timestamp())
    original_tz = os.environ.get("TZ")
    try:
        for ambient_tz in ("UTC", "America/Los_Angeles"):
            os.environ["TZ"] = ambient_tz
            time.tzset()
            monkeypatch.setattr(bot, "now_epoch", lambda: epoch)
            assert bot.reply_cap_date_str() == "2026-07-01"
            state = bot.default_state()
            state.update(
                {
                    "daily_reply_date": "2026-06-30",
                    "daily_reply_count": 4,
                    "daily_replied_author_ids": ["42"],
                    "daily_replied_author_counts": {"42": 2},
                    "daily_quote_reply_date": "2026-06-30",
                    "daily_quote_reply_count": 3,
                }
            )
            bot.reset_daily_reply_count_if_needed(state)
            bot.reset_daily_quote_reply_count_if_needed(state)
            assert state["daily_reply_date"] == "2026-07-01"
            assert state["daily_quote_reply_date"] == "2026-07-01"
            assert state["daily_replied_author_ids"] == []
            assert state["daily_replied_author_counts"] == {}
            assert state["daily_reply_count"] == 0
            assert state["daily_quote_reply_count"] == 0
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time.tzset()


def test_pre_send_reply_target_revalidation_bypasses_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fresh_lookup(target_id: str) -> dict[str, str]:
        calls.append(target_id)
        return {"id": target_id}

    monkeypatch.setattr(bot, "get_tweet_by_id", fresh_lookup)

    assert bot.reply_target_is_available_immediately_before_send("123") is True
    assert calls == ["123"]


@pytest.mark.parametrize("failure_kind", ["missing_data", "target_lookup_error"])
def test_pre_send_reply_target_revalidation_returns_false_only_for_missing_target(
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    if failure_kind == "missing_data":
        monkeypatch.setattr(bot, "get_tweet_by_id", lambda _target_id: None)
    else:
        unavailable = bot.ApiError(
            "X API error 404: post not found",
            service="x",
            status_code=404,
            request_method="GET",
            request_path="/2/tweets/123",
        )
        monkeypatch.setattr(
            bot,
            "get_tweet_by_id",
            lambda _target_id: (_ for _ in ()).throw(unavailable),
        )

    assert bot.reply_target_is_available_immediately_before_send("123") is False


def test_pre_send_reply_target_revalidation_propagates_global_lookup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    denial = bot.ApiError(
        "X API error 403: Invalid or expired token",
        service="x",
        status_code=403,
        request_method="GET",
        request_path="/2/tweets/123",
    )
    monkeypatch.setattr(
        bot,
        "get_tweet_by_id",
        lambda _target_id: (_ for _ in ()).throw(denial),
    )

    with pytest.raises(bot.ApiError, match="expired token"):
        bot.reply_target_is_available_immediately_before_send("123")


def test_reply_target_eligibility_uses_only_target_author_or_direct_mention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "MY_USERNAME", "MrsMThatcher")

    assert bot.reply_target_is_directly_eligible({"author_id": "12345", "text": "Own post"})
    assert bot.reply_target_is_directly_eligible({
        "author_id": "200",
        "text": "A direct reply",
        "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
    })
    assert bot.reply_target_is_directly_eligible({
        "author_id": "200",
        "text": "@MrsMThatcher a cached direct mention",
    })
    assert not bot.reply_target_is_directly_eligible({
        "author_id": "200",
        "text": "The bot appears only in the parent",
        "referenced_tweets": [{"type": "replied_to", "id": "900"}],
    })
    assert not bot.reply_target_is_directly_eligible({
        "author_id": "200",
        "text": "Quoted text says @MrsMThatcher, but X did not mark it as a direct mention",
        "entities": {"mentions": [{"id": "999", "username": "SomeoneElse"}]},
    })


def test_hot_post_search_skips_ineligible_targets_before_candidate_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    replies = [
        {
            "id": "101",
            "author_id": "201",
            "text": "An organic sub-thread contribution.",
            "conversation_id": "900",
            "referenced_tweets": [{"type": "replied_to", "id": "900"}],
            "entities": {"mentions": []},
        },
        {
            "id": "102",
            "author_id": "202",
            "text": "@MrsMThatcher a directly eligible contribution.",
            "conversation_id": "900",
            "referenced_tweets": [{"type": "replied_to", "id": "900"}],
            "entities": {
                "mentions": [{"id": "12345", "username": "MrsMThatcher"}],
            },
        },
    ]

    monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", True)
    monkeypatch.setattr(bot, "MAX_HOT_POST_REPLIES_PER_CHECK", 1)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "MY_USERNAME", "MrsMThatcher")
    monkeypatch.setattr(bot, "lane_paused", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "load_extra_quote_watch_post_ids", lambda: ["900"])
    monkeypatch.setattr(
        bot,
        "x_paginated_get",
        lambda *_args, **_kwargs: {"data": [dict(row) for row in replies], "_pagination": {}},
    )
    monkeypatch.setattr(bot, "attach_media_to_tweets", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)

    candidates = bot.get_hot_post_reply_candidates(state)

    assert [candidate["id"] for candidate in candidates] == ["102"]
    assert state["reply_evaluation_records"]["101"]["outcome"] == "reply_not_permitted"
    assert state["skipped_hot_reply_records"]["101"]["reason"] == (
        "target_does_not_directly_mention_account"
    )


def test_deterministic_spam_skip_precedes_context_media_retrieval_and_xai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_reply_epoch"] = 0
    mention = {
        "id": "100",
        "author_id": "200",
        "text": "@MrsMThatcher guaranteed profit!!!!!",
        "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
        "conversation_id": "100",
        "referenced_tweets": [],
    }

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "MY_USERNAME", "MrsMThatcher")
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "reconcile_confirmed_reply_receipt", lambda _state: False)
    monkeypatch.setattr(bot, "get_mentions", lambda _state: [dict(mention)])
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(bot, "build_context_for_reply_ai", lambda *_args: pytest.fail("context must not be built"))
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", lambda *_args, **_kwargs: pytest.fail("media must not be prepared"))
    monkeypatch.setattr(bot, "evaluate_single_call_reply", legacy_reply_evaluator(lambda *_args, **_kwargs: pytest.fail("retrieval/xAI must not be called")))
    monkeypatch.setattr(bot, "create_post", lambda *_args, **_kwargs: pytest.fail("X write must not be called"))
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert state["daily_reply_count"] == 0


def test_strategy_persistence_failure_blocks_mention_x_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_reply_epoch"] = 0
    mention = {
        "id": "100",
        "author_id": "200",
        "text": "@MrsMThatcher Institutions endure when people defend their purpose.",
        "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
        "conversation_id": "100",
        "referenced_tweets": [],
    }
    text = "Institutions endure only when people defend their purpose."
    context = unit_reply_context(target_id="100", contribution=mention["text"])

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "MY_USERNAME", "MrsMThatcher")
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "reconcile_confirmed_reply_receipt", lambda _state: False)
    monkeypatch.setattr(bot, "get_mentions", lambda _state: [dict(mention)])
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", lambda _text: False)
    monkeypatch.setattr(bot, "build_context_for_reply_ai", lambda *_args: PreparedReplyContext(context, {}))
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        bot,
        "evaluate_single_call_reply",
        legacy_reply_evaluator(lambda actual_context, *_args, **_kwargs: unit_approved_reply(
            actual_context,
            text=text,
            mode="opinion_or_principle",
        )),
    )
    monkeypatch.setattr(bot, "store_pending_ai_reply", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda *_args, **_kwargs: pytest.fail("X write must not be called"),
    )
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_API_ERROR
    assert state["daily_reply_count"] == 0
    assert "100" not in state.get("reply_evaluation_records", {})


def test_deleted_target_after_generation_is_retired_before_any_x_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_reply_epoch"] = 0
    mention = {
        "id": "100",
        "author_id": "200",
        "text": "@MrsMThatcher a substantive direct mention",
        "entities": {
            "mentions": [{"id": "12345", "username": "MrsMThatcher"}],
        },
        "conversation_id": "100",
        "referenced_tweets": [],
    }
    state["mention_pending_candidates"] = {"100": copy.deepcopy(mention)}
    context = unit_reply_context(target_id="100", contribution=mention["text"])
    events: list[tuple[str, dict]] = []
    durable_saves: list[bool] = []

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "MY_USERNAME", "MrsMThatcher")
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "lane_paused", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "get_mentions", lambda _state: [copy.deepcopy(mention)])
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", lambda _text: False)
    monkeypatch.setattr(bot, "build_context_for_reply_ai", lambda *_args: PreparedReplyContext(context, {}))
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        bot,
        "evaluate_single_call_reply",
        legacy_reply_evaluator(lambda actual_context, *_args, **_kwargs: unit_approved_reply(
            actual_context,
            mode="opinion_or_principle",
        )),
    )
    monkeypatch.setattr(bot, "get_tweet_by_id", lambda _target_id: None)
    monkeypatch.setattr(
        bot,
        "post_conversational_reply_with_durable_identity",
        lambda **_kwargs: pytest.fail("X write must not be prepared or attempted"),
    )
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **values: events.append((name, values)),
    )
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda _state, **kwargs: durable_saves.append(
            bool(kwargs.get("durable", False))
        ),
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert "100" not in state["mention_pending_candidates"]
    assert "mention:100" not in state.get("pending_ai_reply_drafts", {})
    assert "100" in state["replied_to_ids"]
    assert state["daily_reply_count"] == 0
    assert state["reply_evaluation_records"]["100"] == {
        "target_id": "100",
        "lane": "mention",
        "outcome": "reply_not_permitted",
        "reason": "x_target_unavailable_pre_send",
        "evaluated_epoch": 1_800_000_000,
    }
    assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert durable_saves[-1] is True
    terminal_events = [
        values for name, values in events if name == "reply_target_terminal"
    ]
    assert terminal_events == [
        {
            "lane": "mention",
            "target_id": "100",
            "outcome": "reply_not_permitted",
            "reason": "x_target_unavailable_pre_send",
        }
    ]
    outcome_events = [
        values
        for name, values in events
        if name == "single_call_reply_posting_outcome"
    ]
    assert len(outcome_events) == 1
    assert outcome_events[0]["status"] == "posting_failed_terminal"
    assert outcome_events[0]["failure_reason"] == "target_unavailable_pre_send"


def test_ineligible_truncated_mention_is_terminal_before_context_media_or_xai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_reply_epoch"] = 0
    mention = {
        "id": "2077713953983987776",
        "author_id": "352335305",
        "text": "Politics has no place in sport.",
        "entities": {"mentions": [{"id": "999", "username": "Argentina"}]},
        "conversation_id": "2077713953983987776",
        "referenced_tweets": [],
        "_pagination_truncated": True,
    }
    events: list[tuple[str, dict]] = []
    context = unit_reply_context(target_id=mention["id"], contribution=mention["text"])
    reply = unit_approved_reply(context, text="A persisted approved reply.")
    assert bot.store_pending_ai_reply(
        state, mention["id"], "mention", reply, context=context,
    )

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "MY_USERNAME", "MrsMThatcher")
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "get_mentions", lambda _state: [dict(mention)])
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(bot, "build_context_for_reply_ai", lambda *_args: pytest.fail("context must not be built"))
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", lambda *_args, **_kwargs: pytest.fail("media must not be prepared"))
    monkeypatch.setattr(bot, "evaluate_single_call_reply", legacy_reply_evaluator(lambda *_args, **_kwargs: pytest.fail("xAI must not be called")))
    monkeypatch.setattr(bot, "create_post", lambda *_args, **_kwargs: pytest.fail("X write must not be called"))
    monkeypatch.setattr(bot, "log_event", lambda name, **values: events.append((name, values)))
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED

    record = state["reply_evaluation_records"][mention["id"]]
    assert record["outcome"] == "reply_not_permitted"
    assert record["reason"] == "target_does_not_directly_mention_account"
    assert sum(name == "reply_target_terminal" for name, _values in events) == 1
    assert not state.get("pending_ai_reply_drafts")
    strategy_events = [
        values
        for name, values in events
        if name == "single_call_reply_posting_outcome"
    ]
    assert len(strategy_events) == 1
    assert strategy_events[0]["status"] == "posting_failed_terminal"
    assert strategy_events[0]["failure_reason"] == "reply_not_permitted_preflight"


def test_posting_generic_reply_403_is_retry_blocking_not_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_reply_epoch"] = 0
    mention = {
        "id": "100",
        "author_id": "200",
        "text": "@MrsMThatcher a substantive direct mention",
        "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
        "conversation_id": "100",
        "referenced_tweets": [],
    }
    context = unit_reply_context(target_id="100", contribution=mention["text"])
    events: list[tuple[str, dict]] = []
    error = bot.ApiError(
        "X API error 403: You can only reply to or quote posts where you are mentioned or are the author.",
        service="x",
        status_code=403,
    )

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "MY_USERNAME", "MrsMThatcher")
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "get_mentions", lambda _state: [dict(mention)])
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", lambda _text: False)
    monkeypatch.setattr(bot, "build_context_for_reply_ai", lambda *_args: PreparedReplyContext(context, {}))
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        bot,
        "evaluate_single_call_reply",
        legacy_reply_evaluator(lambda actual_context, *_args, **_kwargs: unit_approved_reply(actual_context)),
    )
    monkeypatch.setattr(bot, "create_post", lambda **_kwargs: (_ for _ in ()).throw(error))
    monkeypatch.setattr(bot, "log_event", lambda name, **values: events.append((name, values)))
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.maybe_reply_to_mentions(state)

    status, receipt = bot.load_confirmed_reply_receipt()
    assert status == "sending"
    assert receipt is not None
    assert receipt["target_id"] == "100"
    strategy_events = [
        values
        for name, values in events
        if name == "single_call_reply_posting_outcome"
    ]
    assert len(strategy_events) == 1
    assert strategy_events[0]["status"] == "posting_failed_retryable"
    assert strategy_events[0]["strategy_version"] == STRATEGY_VERSION
    assert strategy_events[0]["validated_draft_hash"]
    assert strategy_events[0]["failure_reason"] == "ambiguous_remote_outcome"
