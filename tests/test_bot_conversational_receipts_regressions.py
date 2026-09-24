"""Regression tests for bot conversational receipts."""

from __future__ import annotations

from mrs_bot_reply_cycle_interfaces import PreparedReplyContext
from mrs_bot_reply_drafts import ReplyDrafts
from mrs_bot_reply_receipt_values import ReplyReceiptValues
from mrs_bot_main_post_assembly import MainPostAssembly
from mrs_bot_main_post_reconciliation import MainPostRecovery
from tests.helpers.reply_evaluation import legacy_reply_evaluator

import copy
import json
import os
import signal
from datetime import datetime
from pathlib import Path

import pytest

from tests.helpers.bot_runtime import (
    SCENARIOS,
    bot,
)
from tests.helpers.bot_fixtures import (
    _configure_test_x_base,
    default_sigint_handler,
    isolate_bot_runtime,
    install_receipt_bound_x_request_stub,
)
from tests.helpers.reply_fixtures import (
    patch_tweet_lookup_method,
    UNIT_REPLY_REPOSITORY,
    patch_reply_owner_method,
    patch_reply_receipt_method,
    unit_reply_context,
    unit_approved_reply,
    unit_confirmed_reply_receipt,
    unit_sending_reply_receipt,
    unit_confirmed_v3_reply_receipt,
    unit_v4_reply_receipt_template,
    unit_sending_v4_reply_receipt,
    unit_confirmed_v4_reply_receipt,
)
from tests.fake_api_server import (
    FakeApiServer,
    load_scenario,
)
import exact_receipt_retirement as exact_retirement_module
from single_call_reply import STRATEGY_VERSION


pytestmark = pytest.mark.allow_loopback_network


def test_schema_v4_source_lineage_accepts_real_ai_reply_string_subclass() -> None:
    sending = unit_sending_v4_reply_receipt()
    reply = unit_approved_reply(
        sending["reply_context"],
        text=str(sending["reply_text"]),
    )
    sending["reply_text"] = reply
    sending["ai_reply_draft"] = reply.draft_record
    assert bot.sending_reply_receipt_is_semantically_valid(sending)

    confirmed = bot._reply_assembly()._reply_receipt_values_owner().confirmed_from_sending(
        sending,
        reply_post_id="999",
        confirmation_epoch=2_000_000_005,
    )

    assert bot._reply_assembly()._reply_receipt_values_owner().confirmed_is_valid(confirmed)
    reconstructed = bot.conversational_sending_receipt_from_confirmed(confirmed)
    assert reconstructed["reply_text"] is reply
    assert reconstructed == sending


@pytest.mark.parametrize("schema_version", (4.0, True))
def test_conversational_source_lineage_helpers_require_integer_schema(
    schema_version: object,
) -> None:
    sending = unit_sending_v4_reply_receipt()
    confirmed = bot._reply_assembly()._reply_receipt_values_owner().confirmed_from_sending(
        sending,
        reply_post_id="999",
        confirmation_epoch=2_000_000_005,
    )
    confirmed["schema_version"] = schema_version
    with pytest.raises(ValueError, match="exact source lineage"):
        bot.conversational_sending_receipt_from_confirmed(confirmed)

    template = unit_v4_reply_receipt_template()
    template["schema_version"] = schema_version
    with pytest.raises(RuntimeError, match="schema-v4 sending template"):
        bot._reply_assembly()._reply_receipt_values_owner().bind_attempt(template)


def test_current_conversational_receipt_requires_string_identifier_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ReplyDrafts, "receipt_draft_is_valid", lambda *_: True)
    receipt = {
        "schema_version": 4,
        "lifecycle_state": "sending",
        "target_id": "111",
        "author_id": "222",
        "candidate_source": "mention",
        "conversation_id": "111",
        "reply_text": "A reviewed reply.",
        "reply_context": {
            "target_id": "111",
            "target_author_id": "222",
            "thread_id": "111",
            "lane": "mention",
        },
        "ai_reply_draft": {},
        "attempt_epoch": 1_800_000_000,
        "reply_epoch": 1_800_000_000,
        "daily_reply_date": bot.epoch_date_str(1_800_000_000),
    }
    assert bot.sending_reply_receipt_is_semantically_valid(receipt)

    for field in ("target_id", "author_id", "conversation_id"):
        changed = copy.deepcopy(receipt)
        changed[field] = 111
        if field == "target_id":
            changed["reply_context"]["target_id"] = 111
        if field == "conversation_id":
            changed["reply_context"]["thread_id"] = 111
        assert bot.sending_reply_receipt_is_semantically_valid(changed) is False

    mismatched_author = copy.deepcopy(receipt)
    mismatched_author["reply_context"]["target_author_id"] = "333"
    assert (
        bot.sending_reply_receipt_is_semantically_valid(mismatched_author)
        is False
    )

    confirmed = bot._reply_assembly()._reply_receipt_values_owner().confirmed_from_sending(
        receipt,
        reply_post_id="950002",
        confirmation_epoch=1_800_000_001,
    )
    confirmed["reply_post_id"] = 950002
    assert bot._reply_assembly()._reply_receipt_values_owner().confirmed_is_valid(confirmed) is False


def test_malformed_reply_post_id_is_not_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    state = bot.default_state()
    state["last_reply_epoch"] = 0

    mention = {
        "id": "100",
        "author_id": "200",
        "text": "@MrsMThatcher hello",
        "conversation_id": "100",
        "referenced_tweets": [],
    }

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MARK_AI_REPLIES_AS_AI", False)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        bot._mention_discovery, "get_mentions", lambda state, **_kwargs: [mention],
    )
    monkeypatch.setattr(
        bot._hot_post_discovery, "get_hot_post_reply_candidates",
        lambda state, **_kwargs: [],
    )
    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", lambda text: False)
    context = unit_reply_context(target_id="100", contribution="@MrsMThatcher hello")
    patch_reply_owner_method(
        monkeypatch, bot._reply_context.ReplyContext, "build",
        lambda mention, state: PreparedReplyContext(context, {}),
    )
    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(lambda actual_context, *_args, **_kwargs: unit_approved_reply(actual_context)),
    )
    install_receipt_bound_x_request_stub(
        monkeypatch,
        lambda *args, **kwargs: {"data": {"id": "banana"}},
    )
    monkeypatch.setattr(bot, "save_state", lambda state, **_kwargs: None)

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.maybe_reply_to_mentions(state)

    assert bot.ambiguous_remote_post_is_blocking() is True
    receipt_status, receipt = bot.load_confirmed_reply_receipt()
    assert receipt_status == "sending"
    assert receipt is not None
    assert receipt["target_id"] == "100"
    assert "reply_post_id" not in receipt
    assert state["daily_reply_count"] == 0
    assert state["replied_to_ids"] == []
    assert state["own_auto_reply_ids"] == []
    assert state["tweet_cache"] == {}


def test_confirmed_mention_reply_save_failure_replays_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_scenario(SCENARIOS / "normal_mention_reply.json")
    scenario["enable_pagination"] = True
    scenario["mentions"] = [
        {
            "id": str(tweet_id),
            "author_id": str(400 + tweet_id),
            "conversation_id": str(tweet_id),
            "text": "@a @b @MrsMThatcher",
        }
        for tweet_id in (204, 203, 202, 201)
    ]
    scenario["mentions"].extend(
        [
            {
                "id": "200",
                "author_id": "600",
                "conversation_id": "200",
                "text": "@MrsMThatcher Good sense still matters.",
            },
            {
                "id": "150",
                "author_id": "550",
                "conversation_id": "150",
                "text": "@a @b @MrsMThatcher",
            },
        ]
    )
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        state_file = tmp_path / "bot_state.json"
        control_file = tmp_path / "mrsMThatcher.control.json"
        watch_file = tmp_path / "extra_quote_watch_post_ids.txt"

        monkeypatch.setattr(bot, "STATE_FILE", state_file)
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", control_file)
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", watch_file)
        _configure_test_x_base(monkeypatch, server.url)
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "MARK_AI_REPLIES_AS_AI", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "MAX_MENTIONS_PER_CHECK", 5)
        monkeypatch.setattr(bot, "MENTIONS_MAX_PAGES_PER_CHECK", 1)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))
        patch_reply_owner_method(
            monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
            legacy_reply_evaluator(lambda context, *_args, **_kwargs: unit_approved_reply(
                context,
                text="Quite right. Good sense still matters.",
                mode="opinion_or_principle",
            )),
        )

        original_save_state = bot.save_state
        initial_state = bot.default_state()
        initial_state.update(
            {
                "last_seen_mention_id": "99",
                "daily_reply_date": bot.current_datetime().strftime("%Y-%m-%d"),
                "daily_reply_count": 0,
                "last_reply_epoch": 0,
                "replied_to_ids": [],
                "daily_replied_author_ids": [],
                "daily_replied_author_counts": {},
                "own_auto_reply_ids": [],
                "tweet_cache": {},
            }
        )
        original_save_state(initial_state)

        def fail_first_post_success_save(state: dict, **kwargs: object) -> None:
            if server.posts:
                raise OSError("injected post-success save failure")
            return original_save_state(state, **kwargs)

        monkeypatch.setattr(bot, "save_state", fail_first_post_success_save)
        first_state = bot.load_state()

        with pytest.raises(OSError, match="injected post-success save failure"):
            bot.maybe_reply_to_mentions(first_state)

        assert len(server.posts) == 1
        first_reply_id = "900000"
        assert server.posts[0]["reply"]["in_reply_to_tweet_id"] == "200"

        durable_after_failed_save = json.loads(state_file.read_text(encoding="utf-8"))
        assert "200" not in durable_after_failed_save.get("replied_to_ids", [])
        assert durable_after_failed_save.get("last_seen_mention_id") == "99"
        assert durable_after_failed_save.get("mention_pagination") == {
            "base_since_id": "99",
            "next_token": "5",
        }
        assert durable_after_failed_save.get("daily_reply_count") == 0
        assert durable_after_failed_save.get("last_reply_epoch") == 0
        assert durable_after_failed_save.get("daily_replied_author_counts", {}) == {}
        assert first_reply_id not in durable_after_failed_save.get("own_auto_reply_ids", [])
        receipt_status, receipt = bot.load_confirmed_reply_receipt()
        assert receipt_status == "valid"
        assert receipt is not None
        assert receipt["mention_pagination"] == {
            "base_since_id": "99",
            "next_token": "5",
        }

        monkeypatch.setattr(bot, "save_state", original_save_state)
        restarted_state = bot.load_state()
        assert "200" not in restarted_state.get("replied_to_ids", [])
        assert restarted_state.get("last_seen_mention_id") == "99"
        assert restarted_state["mention_pagination"] == {
            "base_since_id": "99",
            "next_token": "5",
        }

        second_status = bot.maybe_reply_to_mentions(restarted_state)

        assert second_status == bot.NORMAL_CHECK_STATUS_CHECKED
        assert [post["reply"]["in_reply_to_tweet_id"] for post in server.posts] == ["200"]
        mention_requests = [
            request
            for request in server.requests
            if request["path"].endswith("/mentions")
        ]
        assert mention_requests[1]["query"]["since_id"] == ["99"]
        assert mention_requests[1]["query"]["pagination_token"] == ["5"]
        assert restarted_state["mention_pagination"] == {}
    finally:
        server.stop()


def test_confirmed_reply_receipt_reconciliation_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

    state = bot.default_state()
    state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")
    state["last_seen_mention_id"] = "99"
    receipt = unit_confirmed_reply_receipt(
        target_id="100",
        reply_post_id="900000",
        author_id="200",
        epoch=fixed_epoch,
    )

    bot._reply_assembly()._reply_receipts_owner().write(receipt, confirmed=True)
    assert bot.reconcile_confirmed_reply_receipt(state) is True
    assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()

    bot._reply_assembly()._reply_receipts_owner().write(receipt, confirmed=True)
    assert bot.reconcile_confirmed_reply_receipt(state) is True

    assert state["daily_reply_count"] == 1
    assert state["daily_replied_author_counts"] == {"200": 1}
    assert state["replied_to_ids"].count("100") == 1
    assert state["own_auto_reply_ids"].count("900000") == 1
    assert state["last_seen_mention_id"] == "100"


def test_confirmed_receipt_recovery_does_not_change_no_reply_strikes() -> None:
    fixed_epoch = 2_000_000_000
    state = bot.default_state()
    state["daily_reply_date"] = bot.epoch_date_str(fixed_epoch)
    bot.record_qualifying_author_no_reply(
        state,
        "200",
        current_epoch=fixed_epoch - 2,
        explicit_spam_or_abuse=False,
    )
    bot.record_qualifying_author_no_reply(
        state,
        "200",
        current_epoch=fixed_epoch - 1,
        explicit_spam_or_abuse=False,
    )
    before = copy.deepcopy(state["author_evaluation_quarantines"])
    receipt = unit_confirmed_reply_receipt(
        target_id="100",
        reply_post_id="900000",
        author_id="200",
        epoch=fixed_epoch,
    )

    bot._reply_assembly()._confirmed_reply_state_applier()(state, receipt)

    assert state["author_evaluation_quarantines"] == before


def test_conversational_reply_receipt_schema_v3_lifecycle_is_explicit() -> None:
    legacy = unit_confirmed_reply_receipt()
    sending = unit_sending_reply_receipt()
    confirmed = {
        **sending,
        "lifecycle_state": "confirmed",
        "reply_post_id": "999",
    }

    assert bot._reply_assembly()._reply_receipt_values_owner().confirmed_is_valid(legacy) is True
    assert bot.sending_reply_receipt_is_semantically_valid(legacy) is False
    assert bot.sending_reply_receipt_is_semantically_valid(sending) is True
    assert bot._reply_assembly()._reply_receipt_values_owner().confirmed_is_valid(sending) is False
    assert bot._reply_assembly()._reply_receipt_values_owner().confirmed_is_valid(confirmed) is True
    assert bot.sending_reply_receipt_is_semantically_valid(confirmed) is False


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
def test_conversational_reply_receipt_schema_v4_separates_attempt_and_confirmation(
    lane: str,
) -> None:
    attempt_epoch = int(datetime(2026, 7, 6, 23, 59, 50).timestamp())
    confirmation_epoch = int(datetime(2026, 7, 7, 0, 0, 5).timestamp())
    sending = unit_sending_v4_reply_receipt(
        lane=lane,
        attempt_epoch=attempt_epoch,
    )
    confirmed = unit_confirmed_v4_reply_receipt(
        lane=lane,
        attempt_epoch=attempt_epoch,
        confirmation_epoch=confirmation_epoch,
    )

    assert bot.sending_reply_receipt_is_semantically_valid(sending) is True
    assert "confirmation_epoch" not in sending
    assert sending["reply_epoch"] == attempt_epoch
    assert sending["daily_reply_date"] == bot.epoch_date_str(attempt_epoch)
    assert bot._reply_assembly()._reply_receipt_values_owner().confirmed_is_valid(confirmed) is True
    assert confirmed["attempt_epoch"] == attempt_epoch
    assert confirmed["confirmation_epoch"] == confirmation_epoch
    assert confirmed["reply_epoch"] == confirmation_epoch
    assert confirmed["daily_reply_date"] == bot.epoch_date_str(
        confirmation_epoch
    )
    if lane == "quote_tweet":
        assert confirmed["daily_quote_reply_date"] == bot.epoch_date_str(
            confirmation_epoch
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("attempt_epoch", 2_000_000_010),
        ("confirmation_epoch", 1_999_999_999),
        ("reply_epoch", 2_000_000_004),
        ("daily_reply_date", "2033-05-19"),
    ],
)
def test_schema_v4_confirmed_receipt_rejects_inconsistent_timing(
    field: str,
    value: object,
) -> None:
    confirmed = unit_confirmed_v4_reply_receipt()
    confirmed[field] = value

    assert bot._reply_assembly()._reply_receipt_values_owner().confirmed_is_valid(confirmed) is False


def test_schema_v4_sending_receipt_rejects_invented_confirmation() -> None:
    sending = unit_sending_v4_reply_receipt()
    sending["confirmation_epoch"] = sending["attempt_epoch"]

    assert bot.sending_reply_receipt_is_semantically_valid(sending) is False


def test_schema_v4_mention_receipt_accepts_pagination_provenance() -> None:
    sending = unit_sending_v4_reply_receipt()
    sending["mention_pagination"] = {
        "base_since_id": "99",
        "next_token": "page-4",
    }
    confirmed = unit_confirmed_v4_reply_receipt()
    confirmed["mention_pagination"] = {
        "base_since_id": "99",
        "next_token": "page-4",
    }

    assert bot.sending_reply_receipt_is_semantically_valid(sending) is True
    assert bot._reply_assembly()._reply_receipt_values_owner().confirmed_is_valid(confirmed) is True


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
def test_reply_post_helper_captures_confirmation_after_remote_success(
    monkeypatch: pytest.MonkeyPatch,
    lane: str,
) -> None:
    attempt_epoch = int(datetime(2026, 7, 6, 23, 59, 50).timestamp())
    confirmation_epoch = int(datetime(2026, 7, 7, 0, 0, 5).timestamp())
    clock = {"epoch": attempt_epoch}
    monkeypatch.setattr(bot, "now_epoch", lambda: clock["epoch"])
    sending = bot._reply_assembly()._reply_receipt_values_owner().bind_attempt(
        unit_v4_reply_receipt_template(lane=lane)
    )

    def confirmed_remote(
        *_args: object,
        **_kwargs: object,
    ) -> dict[str, object]:
        assert bot.load_confirmed_reply_receipt() == ("sending", sending)
        clock["epoch"] = confirmation_epoch
        return {"data": {"id": "999"}}

    install_receipt_bound_x_request_stub(monkeypatch, confirmed_remote)
    response, confirmed = bot._reply_assembly().post_with_current_owners(
        state=bot.default_state(),
        receipt_template=sending,
        reply_text=str(sending["reply_text"]),
        reply_to_id=str(sending["target_id"]),
        made_with_ai=False,
        lane=lane,
    )

    assert response == {"data": {"id": "999"}}
    assert confirmed["attempt_epoch"] == attempt_epoch
    assert confirmed["confirmation_epoch"] == confirmation_epoch
    assert confirmed["reply_epoch"] == confirmation_epoch
    assert confirmed["daily_reply_date"] == bot.epoch_date_str(
        confirmation_epoch
    )
    assert bot.load_confirmed_reply_receipt() == ("valid", confirmed)


def test_schema_v4_clock_rollback_uses_conservative_confirmation_time(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    attempt_epoch = 2_000_000_000
    clock = {"epoch": attempt_epoch}
    monkeypatch.setattr(bot, "now_epoch", lambda: clock["epoch"])
    sending = bot._reply_assembly()._reply_receipt_values_owner().bind_attempt(
        unit_v4_reply_receipt_template()
    )

    def confirmed_after_clock_rollback(
        *_args: object,
        **_kwargs: object,
    ) -> dict[str, object]:
        clock["epoch"] = attempt_epoch - 60
        return {"data": {"id": "999"}}

    install_receipt_bound_x_request_stub(
        monkeypatch,
        confirmed_after_clock_rollback,
    )
    _, confirmed = bot._reply_assembly().post_with_current_owners(
        state=bot.default_state(),
        receipt_template=sending,
        reply_text=str(sending["reply_text"]),
        reply_to_id=str(sending["target_id"]),
        made_with_ai=False,
        lane="mention",
    )

    assert confirmed["attempt_epoch"] == attempt_epoch
    assert confirmed["confirmation_epoch"] == attempt_epoch
    assert confirmed["reply_epoch"] == attempt_epoch
    assert bot._reply_assembly()._reply_receipt_values_owner().confirmed_is_valid(confirmed) is True


def test_invalid_v4_confirmation_never_mutates_fallback_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "now_epoch", lambda: 2_000_000_000)
    sending = bot._reply_assembly()._reply_receipt_values_owner().bind_attempt(
        unit_v4_reply_receipt_template()
    )
    state = bot.default_state()
    baseline = copy.deepcopy(state)
    original_validator = bot._reply_assembly()._reply_receipt_values_owner().confirmed_is_valid

    install_receipt_bound_x_request_stub(
        monkeypatch,
        lambda *_args, **_kwargs: {"data": {"id": "999"}},
    )
    patch_reply_owner_method(
        monkeypatch,
        ReplyReceiptValues,
        "confirmed_is_valid",
        lambda receipt: (
            False
            if receipt.get("schema_version") == 4
            else original_validator(receipt)
        ),
    )

    with pytest.raises(bot.UnrecoverableConfirmedReplyPersistenceError):
        bot._reply_assembly().post_with_current_owners(
            state=state,
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="mention",
        )

    assert state == baseline
    assert bot.load_confirmed_reply_receipt() == ("sending", sending)


def test_schema_v4_promotion_failure_fallback_uses_confirmation_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt_epoch = int(datetime(2026, 7, 6, 23, 59, 50).timestamp())
    confirmation_epoch = int(datetime(2026, 7, 7, 0, 0, 5).timestamp())
    clock = {"epoch": attempt_epoch}
    monkeypatch.setattr(bot, "now_epoch", lambda: clock["epoch"])
    sending = bot._reply_assembly()._reply_receipt_values_owner().bind_attempt(
        unit_v4_reply_receipt_template()
    )
    state = bot.default_state()
    state["daily_reply_date"] = bot.epoch_date_str(attempt_epoch)
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)

    def confirmed_remote(
        *_args: object,
        **_kwargs: object,
    ) -> dict[str, object]:
        clock["epoch"] = confirmation_epoch
        return {"data": {"id": "999"}}

    install_receipt_bound_x_request_stub(monkeypatch, confirmed_remote)
    patch_reply_receipt_method(
        monkeypatch,
        bot,
        "promote",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("promotion failed")
        ),
    )

    with pytest.raises(bot.ConfirmedReplyLocalPersistenceError):
        bot._reply_assembly().post_with_current_owners(
            state=state,
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="mention",
        )

    assert state["last_reply_epoch"] == confirmation_epoch
    assert state["daily_reply_date"] == bot.epoch_date_str(confirmation_epoch)
    assert state["daily_reply_count"] == 1
    assert state["ai_reply_history"][0]["attempt_epoch"] == attempt_epoch
    assert state["ai_reply_history"][0]["confirmation_epoch"] == confirmation_epoch
    assert bot.load_confirmed_reply_receipt() == ("absent", None)


@pytest.mark.parametrize(
    ("remote_error", "expected_exception"),
    [
        (
            bot.ApiError("definite failure", service="x", status_code=400),
            bot.AmbiguousRemotePostOutcome,
        ),
        (
            bot.AmbiguousRemotePostOutcome("uncertain", service="x"),
            bot.AmbiguousRemotePostOutcome,
        ),
    ],
)
def test_schema_v4_failed_remote_outcome_never_invents_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    remote_error: Exception,
    expected_exception: type[Exception],
) -> None:
    monkeypatch.setattr(bot, "now_epoch", lambda: 2_000_000_000)
    sending = bot._reply_assembly()._reply_receipt_values_owner().bind_attempt(
        unit_v4_reply_receipt_template()
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(remote_error),
    )

    with pytest.raises(expected_exception):
        bot._reply_assembly().post_with_current_owners(
            state=bot.default_state(),
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="mention",
        )

    status, receipt = bot.load_confirmed_reply_receipt()
    assert status == "sending"
    if receipt is not None:
        assert "confirmation_epoch" not in receipt
        assert receipt["reply_epoch"] == receipt["attempt_epoch"]


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
def test_schema_v4_confirmation_advances_daily_counters_once_across_midnight(
    lane: str,
) -> None:
    attempt_epoch = int(datetime(2026, 7, 6, 23, 59, 50).timestamp())
    confirmation_epoch = int(datetime(2026, 7, 7, 0, 0, 5).timestamp())
    receipt = unit_confirmed_v4_reply_receipt(
        lane=lane,
        attempt_epoch=attempt_epoch,
        confirmation_epoch=confirmation_epoch,
        author_id="200",
    )
    state = bot.default_state()
    state["daily_reply_date"] = bot.epoch_date_str(attempt_epoch)
    state["daily_reply_count"] = 7
    state["daily_replied_author_ids"] = ["old-author"]
    state["daily_replied_author_counts"] = {"old-author": 2}
    state["daily_quote_reply_date"] = bot.epoch_date_str(attempt_epoch)
    state["daily_quote_reply_count"] = 3

    bot._reply_assembly()._confirmed_reply_state_applier()(state, receipt)
    bot._reply_assembly()._confirmed_reply_state_applier()(state, receipt)

    confirmation_date = bot.epoch_date_str(confirmation_epoch)
    assert state["daily_reply_date"] == confirmation_date
    assert state["daily_reply_count"] == 1
    assert state["daily_replied_author_ids"] == ["200"]
    assert state["daily_replied_author_counts"] == {"200": 1}
    assert state["last_reply_epoch"] == confirmation_epoch
    assert state["ai_reply_history"][0]["attempt_epoch"] == attempt_epoch
    assert state["ai_reply_history"][0]["confirmation_epoch"] == confirmation_epoch
    assert state["ai_reply_history"][0]["reply_epoch"] == confirmation_epoch
    assert state["tweet_cache"]["999"]["created_at"] == datetime.fromtimestamp(
        confirmation_epoch
    ).isoformat()
    if lane == "quote_tweet":
        assert state["daily_quote_reply_date"] == confirmation_date
        assert state["daily_quote_reply_count"] == 1
    else:
        assert state["daily_quote_reply_date"] == bot.epoch_date_str(attempt_epoch)
        assert state["daily_quote_reply_count"] == 3


def test_confirmed_factual_reply_outcome_logs_compact_fact_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: UNIT_REPLY_REPOSITORY)
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **values: events.append((name, values)),
    )
    receipt = unit_confirmed_reply_receipt(
        factual=True,
        text=(
            "People moved from East Germany towards West Germany in November "
            "1989."
        ),
    )

    bot._reply_assembly()._confirmed_reply_state_applier()(bot.default_state(), receipt)

    outcome = next(
        values
        for name, values in events
        if name == "single_call_reply_posting_outcome"
    )
    assert outcome["status"] == "confirmed"
    assert outcome["reply_kind"] == "direct_factual"
    assert outcome["used_fact_count"] == 1
    assert outcome["model_call_count"] == 1


def test_schema_v4_reconciliation_never_rolls_newer_daily_state_backward() -> None:
    attempt_epoch = int(datetime(2026, 7, 6, 23, 59, 50).timestamp())
    confirmation_epoch = int(datetime(2026, 7, 7, 0, 0, 5).timestamp())
    receipt = unit_confirmed_v4_reply_receipt(
        lane="quote_tweet",
        attempt_epoch=attempt_epoch,
        confirmation_epoch=confirmation_epoch,
    )
    state = bot.default_state()
    state["daily_reply_date"] = "2026-07-08"
    state["daily_reply_count"] = 4
    state["daily_replied_author_ids"] = ["later-author"]
    state["daily_replied_author_counts"] = {"later-author": 1}
    state["daily_quote_reply_date"] = "2026-07-08"
    state["daily_quote_reply_count"] = 2

    bot._reply_assembly()._confirmed_reply_state_applier()(state, receipt)

    assert state["daily_reply_date"] == "2026-07-08"
    assert state["daily_reply_count"] == 4
    assert state["daily_replied_author_counts"] == {"later-author": 1}
    assert state["daily_quote_reply_date"] == "2026-07-08"
    assert state["daily_quote_reply_count"] == 2


def test_reply_spacing_is_measured_from_schema_v4_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt_epoch = 2_000_000_000
    confirmation_epoch = attempt_epoch + 120
    state = bot.default_state()
    state["daily_reply_date"] = bot.epoch_date_str(confirmation_epoch)
    state["daily_reply_count"] = 0
    bot._reply_assembly()._confirmed_reply_state_applier()(
        state,
        unit_confirmed_v4_reply_receipt(
            attempt_epoch=attempt_epoch,
            confirmation_epoch=confirmation_epoch,
        ),
    )
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setitem(bot.single_call_reply, "enabled", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 1800)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
    monkeypatch.setattr(bot, "lane_paused", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        bot,
        "now_epoch",
        lambda: confirmation_epoch + 1799,
    )
    monkeypatch.setattr(
        bot,
        "current_datetime",
        lambda: datetime.fromtimestamp(confirmation_epoch + 1799),
    )
    monkeypatch.setattr(
        bot._mention_discovery,
        "get_mentions",
        lambda _state, **_kwargs: pytest.fail("spacing must block candidate retrieval"),
    )

    assert (
        bot.maybe_reply_to_mentions(state)
        == bot.NORMAL_CHECK_STATUS_SKIPPED_SPACING
    )


@pytest.mark.parametrize(
    "pagination",
    [
        {"base_since_id": "", "next_token": "page-4"},
        {"base_since_id": "99", "next_token": "page-4"},
    ],
)
def test_schema_v3_mention_receipt_accepts_exact_pagination_provenance(
    pagination: dict[str, str],
) -> None:
    sending = unit_sending_reply_receipt()
    sending["mention_pagination"] = pagination
    confirmed = {
        **sending,
        "lifecycle_state": "confirmed",
        "reply_post_id": "999",
    }

    assert bot.sending_reply_receipt_is_semantically_valid(sending) is True
    assert bot._reply_assembly()._reply_receipt_values_owner().confirmed_is_valid(confirmed) is True


@pytest.mark.parametrize(
    "pagination",
    [
        None,
        [],
        {},
        {"base_since_id": "99"},
        {"next_token": "page-4"},
        {"base_since_id": "99", "next_token": ""},
        {"base_since_id": "not-a-post-id", "next_token": "page-4"},
        {"base_since_id": 99, "next_token": "page-4"},
        {"base_since_id": "99", "next_token": 15},
        {
            "base_since_id": "99",
            "next_token": "page-4",
            "unexpected": "field",
        },
    ],
)
def test_schema_v3_mention_receipt_rejects_malformed_pagination_provenance(
    pagination: object,
) -> None:
    sending = unit_sending_reply_receipt()
    sending["mention_pagination"] = pagination

    assert bot.sending_reply_receipt_is_semantically_valid(sending) is False


@pytest.mark.parametrize("lane", ["hot_post_reply", "quote_tweet"])
def test_schema_v3_nonmention_receipt_rejects_mention_pagination_provenance(
    lane: str,
) -> None:
    sending = unit_sending_reply_receipt(lane=lane)
    sending["mention_pagination"] = {
        "base_since_id": "99",
        "next_token": "page-4",
    }

    assert bot.sending_reply_receipt_is_semantically_valid(sending) is False


def test_legacy_schema_v2_receipt_rejects_new_pagination_provenance() -> None:
    receipt = unit_confirmed_reply_receipt()
    receipt["mention_pagination"] = {
        "base_since_id": "99",
        "next_token": "page-4",
    }

    assert bot._reply_assembly()._reply_receipt_values_owner().confirmed_is_valid(receipt) is False


def test_confirmed_mention_receipt_restores_pagination_without_advancing_watermark() -> None:
    pagination = {
        "base_since_id": "99",
        "next_token": "page-4",
    }
    receipt = unit_confirmed_v3_reply_receipt(target_id="100")
    receipt["mention_pagination"] = pagination
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["mention_pagination"] = {}

    bot._reply_assembly()._confirmed_reply_state_applier()(state, receipt)
    bot._reply_assembly()._confirmed_reply_state_applier()(state, receipt)

    assert state["last_seen_mention_id"] == "99"
    assert state["mention_pagination"] == pagination
    assert state["replied_to_ids"].count("100") == 1
    assert state["own_auto_reply_ids"].count("999") == 1


def test_confirmed_mention_receipt_after_backlog_reset_cannot_skip_unseen_ids(
    tmp_path: Path,
) -> None:
    pagination = {
        "base_since_id": "99",
        "next_token": "page-A",
    }
    receipt = unit_confirmed_v4_reply_receipt(target_id="105")
    assert bot._reply_assembly()._reply_receipt_values_owner().confirmed_is_valid(receipt) is True

    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["mention_backlog"] = {
        "since_id": "99",
        "next_token": "page-A",
        "highest_mention_id": "105",
        "pages_completed": bot.MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT + 1,
        "started_epoch": 2_000_000_000,
        "seen_tokens": [
            f"token-{index}"
            for index in range(bot.MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT + 1)
        ],
        "announced": True,
    }
    state["mention_pagination"] = dict(pagination)
    state["mention_pending_candidates"] = {
        "105": {
            "id": "105",
            "author_id": "200",
            "conversation_id": "105",
            "text": "A queued mention.",
        }
    }

    reset_state = bot.normalise_state_candidate(
        state,
        path=tmp_path / "bot_state.json",
    )
    assert reset_state is not None
    assert reset_state["mention_backlog"] == {}
    assert reset_state["mention_pagination"] == {}
    assert reset_state["mention_pending_candidates"] == {}
    assert reset_state["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }

    bot._reply_assembly()._confirmed_reply_state_applier()(reset_state, receipt)

    assert reset_state["last_seen_mention_id"] == "99"
    assert reset_state["mention_pagination"] == {}
    assert reset_state["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }
    assert "105" in reset_state["replied_to_ids"]


def test_confirmed_truncated_mention_receipt_reconciles_after_restart_without_x(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    receipt = unit_confirmed_v3_reply_receipt(
        target_id="100",
        reply_post_id="999",
    )
    receipt["mention_pagination"] = {
        "base_since_id": "99",
        "next_token": "page-4",
    }
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda *_args, **_kwargs: pytest.fail(
            "confirmed receipt reconciliation must not repeat the X write"
        ),
    )

    bot._reply_assembly()._reply_receipts_owner().write(receipt, confirmed=True)
    assert bot.reconcile_confirmed_reply_receipt(state) is True

    assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
    assert state["last_seen_mention_id"] == "99"
    assert state["mention_pagination"] == {
        "base_since_id": "99",
        "next_token": "page-4",
    }
    assert state["replied_to_ids"] == ["100"]
    assert state["own_auto_reply_ids"] == ["999"]


def test_confirmed_receipt_reconciliation_cannot_authorise_stale_pending_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    state = bot.default_state()
    state["daily_reply_date"] = bot.epoch_date_str(fixed_epoch)
    state["last_seen_mention_id"] = "99"
    state["mention_backlog"] = {
        "since_id": "98",
        "next_token": "page-A",
        "highest_mention_id": "105",
        "pages_completed": 1,
        "started_epoch": fixed_epoch - 10,
        "seen_tokens": [],
        "announced": True,
    }
    state["mention_pagination"] = {
        "base_since_id": "98",
        "next_token": "page-A",
    }
    state["mention_pending_candidates"] = {
        "105": {
            "id": "105",
            "author_id": "205",
            "conversation_id": "105",
            "text": "A queued mention.",
        }
    }
    receipt = unit_confirmed_v3_reply_receipt(
        target_id="105",
        reply_post_id="999",
        author_id="205",
        epoch=fixed_epoch,
    )
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda *_args, **_kwargs: pytest.fail(
            "confirmed receipt reconciliation must not repeat the X write"
        ),
    )
    monkeypatch.setattr(
        bot,
        "x_request",
        lambda *_args, **_kwargs: pytest.fail(
            "confirmed receipt reconciliation requires no X read"
        ),
    )
    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(lambda *_args, **_kwargs: pytest.fail(
            "stale pending state must not reach the reply provider"
        )),
    )

    bot._reply_assembly()._reply_receipts_owner().write(receipt, confirmed=True)
    assert bot.reconcile_confirmed_reply_receipt(state) is True

    restarted = bot.load_state()
    assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
    assert restarted["last_seen_mention_id"] == "99"
    assert restarted["mention_backlog"] == {}
    assert restarted["mention_pagination"] == {}
    assert restarted["mention_pending_candidates"] == {}
    assert restarted["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }
    assert restarted["replied_to_ids"] == ["105"]
    assert restarted["own_auto_reply_ids"] == ["999"]
    assert restarted["daily_reply_count"] == 1
    assert restarted["daily_replied_author_counts"] == {"205": 1}


@pytest.mark.parametrize("generation_ordered", [False, True], ids=["legacy-refusal", "sealed-recovery"])
def test_receipt_recovery_from_backup_without_page_ownership_is_guarded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    generation_ordered: bool,
) -> None:
    from mrs_bot_state_generation import encode_generation

    state_path = tmp_path / "bot_state.json"
    backup_path = tmp_path / "bot_state.json.bak1"
    monkeypatch.setattr(bot, "STATE_FILE", state_path)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    primary = {
        "last_seen_mention_id": "99",
        "mention_pending_candidates": {
            "105": {
                "id": "106", "author_id": "205", "conversation_id": "105",
                "text": "Corrupt primary identity.",
            }
        },
    }
    backup = {"last_seen_mention_id": "99", "replied_to_ids": []}
    for sequence, (path, document) in enumerate(((state_path, primary), (backup_path, backup)), start=1):
        if generation_ordered:
            document, _encoded = encode_generation(
                bot.state_document_for_persistence(document), sequence,
                bot.DURABLE_RUNTIME_JSON_MAX_BYTES,
            )
        bot.atomic_write_json(path, document, durable=True)
    if not generation_ordered:
        before = {path: path.read_bytes() for path in (state_path, backup_path)}
        with pytest.raises(RuntimeError, match="diverge|ambiguous"):
            bot.load_state()
        assert {path: path.read_bytes() for path in (state_path, backup_path)} == before
        return

    state = bot.load_state()
    assert state["last_seen_mention_id"] == "99"
    assert state["mention_backlog"] == {}
    assert state["mention_pagination"] == {}
    assert state["mention_backlog_reset_guard"] == {}
    assert state["_state_generation"]["sequence"] > 2
    assert state_path.read_bytes() == backup_path.read_bytes()

    receipt = unit_confirmed_v3_reply_receipt(
        target_id="105",
        reply_post_id="999",
        author_id="205",
    )
    receipt["mention_pagination"] = {
        "base_since_id": "99",
        "next_token": "page-A",
    }
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda *_args, **_kwargs: pytest.fail(
            "backup receipt recovery must not repeat the X write"
        ),
    )
    monkeypatch.setattr(
        bot,
        "x_request",
        lambda *_args, **_kwargs: pytest.fail(
            "backup receipt recovery requires no X read"
        ),
    )
    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(lambda *_args, **_kwargs: pytest.fail(
            "backup receipt recovery must not call the reply provider"
        )),
    )

    bot._reply_assembly()._reply_receipts_owner().write(receipt, confirmed=True)
    assert bot.reconcile_confirmed_reply_receipt(state) is True

    restarted = bot.load_state()
    assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
    assert restarted["last_seen_mention_id"] == "99"
    assert restarted["mention_backlog"] == {}
    assert restarted["mention_pagination"] == {
        "base_since_id": "99",
        "next_token": "page-A",
    }
    assert restarted["mention_pending_candidates"] == {}
    assert restarted["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }
    assert restarted["replied_to_ids"] == ["105"]
    assert restarted["own_auto_reply_ids"] == ["999"]


def test_confirmed_mention_receipt_rejects_pagination_base_mismatch() -> None:
    receipt = unit_confirmed_v3_reply_receipt(target_id="100")
    receipt["mention_pagination"] = {
        "base_since_id": "99",
        "next_token": "page-4",
    }
    state = bot.default_state()
    state["last_seen_mention_id"] = "98"

    with pytest.raises(
        bot.InvalidConfirmedReplyReceipt,
        match="pagination base does not match",
    ):
        bot._reply_assembly()._confirmed_reply_state_applier()(state, receipt)

    assert state["last_seen_mention_id"] == "98"
    assert state["mention_pagination"] == {}


@pytest.mark.parametrize("schema_version", [2, 3])
def test_legacy_mention_receipt_without_pagination_remains_valid_and_advances_watermark(
    schema_version: int,
) -> None:
    if schema_version == 2:
        receipt = unit_confirmed_reply_receipt(target_id="100")
    else:
        receipt = unit_confirmed_v3_reply_receipt(target_id="100")
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"

    assert bot._reply_assembly()._reply_receipt_values_owner().confirmed_is_valid(receipt) is True
    bot._reply_assembly()._confirmed_reply_state_applier()(state, receipt)

    assert state["last_seen_mention_id"] == "100"
    assert state["mention_pagination"] == {}


@pytest.mark.parametrize("schema_version", [2, 3])
def test_legacy_mention_receipt_preserves_matching_active_pagination(
    schema_version: int,
) -> None:
    if schema_version == 2:
        receipt = unit_confirmed_reply_receipt(target_id="100")
    else:
        receipt = unit_confirmed_v3_reply_receipt(target_id="100")
    pagination = {
        "base_since_id": "99",
        "next_token": "page-4",
    }
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["mention_pagination"] = pagination

    bot._reply_assembly()._confirmed_reply_state_applier()(state, receipt)

    assert state["last_seen_mention_id"] == "99"
    assert state["mention_pagination"] == pagination


def test_conversational_reply_receipt_is_durable_before_remote_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sending = unit_sending_v4_reply_receipt()
    state = bot.default_state()
    observed: list[dict[str, object]] = []

    def confirmed_remote(
        method: str,
        path: str,
        **kwargs: object,
    ) -> dict[str, object]:
        status, current = bot.load_confirmed_reply_receipt()
        assert status == "sending"
        assert current == sending
        observed.append({"method": method, "path": path, **kwargs})
        return {"data": {"id": "999"}}

    install_receipt_bound_x_request_stub(monkeypatch, confirmed_remote)

    response, confirmed = bot._reply_assembly().post_with_current_owners(
        state=state,
        receipt_template=sending,
        reply_text=str(sending["reply_text"]),
        reply_to_id=str(sending["target_id"]),
        made_with_ai=False,
        lane="mention",
    )

    assert response == {"data": {"id": "999"}}
    assert confirmed["lifecycle_state"] == "confirmed"
    assert confirmed["reply_post_id"] == "999"
    assert bot.load_confirmed_reply_receipt() == ("valid", confirmed)
    assert observed[0]["method"] == "POST"
    assert observed[0]["path"] == "/2/tweets"


def test_prepared_reply_bypass_requires_exact_receipt_text_and_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sending = unit_sending_reply_receipt()
    bot._reply_assembly()._reply_receipts_owner().write(sending, confirmed=False)
    remote_calls = 0

    def remote(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal remote_calls
        remote_calls += 1
        return {"data": {"id": "999"}}

    install_receipt_bound_x_request_stub(monkeypatch, remote)

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.create_post(
            str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
        )
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="does not exactly bind"):
        bot.create_post(
            "Different reply text.",
            reply_to_id=str(sending["target_id"]),
            prepared_conversational_reply_receipt=sending,
        )
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="does not exactly bind"):
        bot.create_post(
            str(sending["reply_text"]),
            reply_to_id="101",
            prepared_conversational_reply_receipt=sending,
        )
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="does not exactly bind"):
        bot.create_post(
            str(sending["reply_text"]),
            media_ids=["media-1"],
            reply_to_id=str(sending["target_id"]),
            prepared_conversational_reply_receipt=sending,
        )

    assert remote_calls == 0


@pytest.mark.parametrize(
    "lane",
    [
        "regular_quote",
        "meme",
        "mention",
        "quote_tweet",
        "historical_context",
        "direct_create",
        "direct_media_upload",
    ],
)
def test_sending_reply_receipt_blocks_each_remote_lane_before_preparation(
    monkeypatch: pytest.MonkeyPatch,
    lane: str,
) -> None:
    import historical_context_formatter

    sending = unit_sending_reply_receipt()
    bot._reply_assembly()._reply_receipts_owner().write(sending, confirmed=False)
    calls: list[str] = []

    def prepared(name: str) -> None:
        calls.append(name)
        pytest.fail(f"{name} preparation must not run through a sending receipt")

    if lane == "regular_quote":
        monkeypatch.setattr(
            MainPostRecovery,
            "reconcile",
            lambda _recovery, *_args, **_kwargs: prepared("receipt reconciliation"),
        )
        invoke = lambda: bot.post_random_quote(set(), set(), bot.default_state())
    elif lane == "meme":
        monkeypatch.setattr(
            MainPostAssembly,
            "both_receipts_exist",
            lambda _assembly: prepared("meme receipt check"),
        )
        invoke = lambda: bot.post_next_meme(bot.default_state())
    elif lane == "mention":
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(
            bot._mention_discovery,
            "get_mentions",
            lambda *_args, **_kwargs: prepared("mention fetch"),
        )
        invoke = lambda: bot.maybe_reply_to_mentions(bot.default_state())
    elif lane == "quote_tweet":
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
        monkeypatch.setattr(
            bot._quote_discovery.QuoteWatchPosts,
            "lookup",
            lambda *_args, **_kwargs: prepared("quote lookup"),
        )
        invoke = lambda: bot.maybe_reply_to_quote_tweets(bot.default_state())
    elif lane == "historical_context":
        monkeypatch.setattr(
            bot,
            "historical_context_reply",
            {**bot.historical_context_reply, "enabled": True},
        )
        monkeypatch.setattr(
            historical_context_formatter,
            "load_and_validate_corpus",
            lambda *_args: prepared("historical research load"),
        )
        invoke = lambda: bot.maybe_post_historical_context_reply(
            quote_hash="a" * 64,
            quote_text="Quote",
            parent_post_id="123",
        )
    elif lane == "direct_create":
        monkeypatch.setattr(
            bot,
            "x_request",
            lambda *_args, **_kwargs: prepared("X request"),
        )
        invoke = lambda: bot.create_post("test")
    else:
        monkeypatch.setattr(
            bot,
            "upload_media_v2",
            lambda *_args, **_kwargs: prepared("media upload"),
        )
        monkeypatch.setattr(
            bot,
            "upload_media_v1_1",
            lambda *_args, **_kwargs: prepared("media upload fallback"),
        )
        invoke = lambda: bot.upload_media("image.png", lane="quote_image")

    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="unresolved conversational-reply",
    ):
        invoke()

    assert calls == []


def test_conversational_reply_template_must_match_declared_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sending = unit_sending_v4_reply_receipt(lane="mention")
    monkeypatch.setattr(
        bot,
        "x_request",
        lambda *_args, **_kwargs: pytest.fail("invalid lane must fail before X"),
    )

    with pytest.raises(RuntimeError, match="invalid reply receipt template"):
        bot._reply_assembly().post_with_current_owners(
            state=bot.default_state(),
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="quote_tweet",
        )

    assert bot.load_confirmed_reply_receipt() == ("absent", None)


def test_conversational_post_rejects_legacy_receipt_before_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sending = unit_sending_reply_receipt()
    monkeypatch.setattr(
        bot,
        "x_request",
        lambda *_args, **_kwargs: pytest.fail(
            "legacy conversational receipt must fail before transport"
        ),
    )

    with pytest.raises(RuntimeError, match="current schema-v4"):
        bot._reply_assembly().post_with_current_owners(
            state=bot.default_state(),
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="mention",
        )

    assert bot.load_confirmed_reply_receipt() == ("absent", None)


def test_generic_reply_rejection_preserves_sending_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sending = unit_sending_v4_reply_receipt()

    def rejected(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise bot.ApiError(
            "X API error 403: reply forbidden",
            service="x",
            status_code=403,
        )

    install_receipt_bound_x_request_stub(monkeypatch, rejected)

    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="outcome is unproved"):
        bot._reply_assembly().post_with_current_owners(
            state=bot.default_state(),
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="mention",
        )

    status, receipt = bot.load_confirmed_reply_receipt()
    assert status == "sending"
    assert receipt == sending


@pytest.mark.parametrize(
    "remote_error",
    [
        RuntimeError("unexpected transport implementation failure"),
        ValueError("response decoder failed after accepted write"),
        KeyboardInterrupt(),
    ],
)
def test_unclassified_reply_interruption_preserves_sending_receipt(
    monkeypatch: pytest.MonkeyPatch,
    remote_error: BaseException,
) -> None:
    sending = unit_sending_v4_reply_receipt()

    def interrupted(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise remote_error

    install_receipt_bound_x_request_stub(monkeypatch, interrupted)
    expected = (
        bot.AmbiguousRemotePostOutcome
        if isinstance(remote_error, Exception)
        else type(remote_error)
    )

    with pytest.raises(expected):
        bot._reply_assembly().post_with_current_owners(
            state=bot.default_state(),
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="mention",
        )

    assert bot.load_confirmed_reply_receipt() == ("sending", sending)
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    assert bot.ambiguous_remote_post_is_blocking() is True


def test_reply_ambiguity_marker_and_state_failure_preserve_restart_barrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sending = unit_sending_v4_reply_receipt()
    original_atomic_write = bot.atomic_write_json
    remote_calls = 0

    def selective_atomic_write(
        path: Path,
        data: object,
        **kwargs: object,
    ) -> None:
        if path == bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE:
            raise OSError("marker failed")
        original_atomic_write(path, data, **kwargs)

    def accepted_without_response(
        *_args: object,
        **_kwargs: object,
    ) -> dict[str, object]:
        nonlocal remote_calls
        remote_calls += 1
        raise bot.AmbiguousRemotePostOutcome(
            "accepted then connection closed",
            service="x",
        )

    monkeypatch.setattr(bot, "atomic_write_json", selective_atomic_write)
    install_receipt_bound_x_request_stub(monkeypatch, accepted_without_response)
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("state failed")),
    )

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot._reply_assembly().post_with_current_owners(
            state=bot.default_state(),
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="mention",
        )

    assert remote_calls == 1
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert bot.load_confirmed_reply_receipt() == ("sending", sending)

    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.create_post("must not be sent")
    assert remote_calls == 1


def test_reply_promotion_state_and_marker_failure_blocks_restart_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sending = unit_sending_v4_reply_receipt()
    original_atomic_write = bot.atomic_write_json
    remote_calls = 0

    def selective_atomic_write(
        path: Path,
        data: object,
        **kwargs: object,
    ) -> None:
        if path == bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE:
            raise OSError("marker failed")
        original_atomic_write(path, data, **kwargs)

    def confirmed_remote(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal remote_calls
        remote_calls += 1
        return {"data": {"id": "999"}}

    monkeypatch.setattr(bot, "atomic_write_json", selective_atomic_write)
    install_receipt_bound_x_request_stub(monkeypatch, confirmed_remote)
    patch_reply_receipt_method(
        monkeypatch,
        bot,
        "promote",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("promotion failed")
        ),
    )
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("state failed")),
    )

    with pytest.raises(bot.UnrecoverableConfirmedReplyPersistenceError):
        bot._reply_assembly().post_with_current_owners(
            state=bot.default_state(),
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="mention",
        )

    assert remote_calls == 1
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert bot.load_confirmed_reply_receipt() == ("sending", sending)

    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.create_post("must not be sent")
    assert remote_calls == 1


def test_reply_promotion_failure_uses_confirmed_state_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sending = unit_sending_v4_reply_receipt()
    state = bot.default_state()
    state["daily_reply_date"] = str(sending["daily_reply_date"])
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    install_receipt_bound_x_request_stub(
        monkeypatch,
        lambda *_args, **_kwargs: {"data": {"id": "999"}},
    )
    patch_reply_receipt_method(
        monkeypatch,
        bot,
        "promote",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("promotion failed")
        ),
    )

    with pytest.raises(bot.ConfirmedReplyLocalPersistenceError):
        bot._reply_assembly().post_with_current_owners(
            state=state,
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="mention",
        )

    assert bot.load_confirmed_reply_receipt() == ("absent", None)
    assert bot._reply_assembly().emergency_representation_is_complete(
        bot._reply_assembly()._reply_receipt_values_owner().confirmed_from_sending(
            sending,
            reply_post_id="999",
            confirmation_epoch=int(sending["attempt_epoch"]),
        ),
        state,
    )
    assert bot.json_file_matches(bot.STATE_FILE, state) is True
    assert "disposition=confirmed_state_fallback" in caplog.text
    assert "after definite non-success" not in caplog.text


def test_reply_sigint_is_delivered_only_after_confirmed_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sending = unit_sending_v4_reply_receipt()
    state = bot.default_state()
    remote_calls = 0
    guard = object()

    def confirmed_remote(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal remote_calls
        remote_calls += 1
        assert bot.load_confirmed_reply_receipt() == ("sending", sending)
        return {"data": {"id": "999"}}

    def deliver_sigint(actual_guard: object) -> None:
        assert actual_guard is guard
        status, receipt = bot.load_confirmed_reply_receipt()
        assert status == "valid"
        assert receipt is not None
        assert receipt["lifecycle_state"] == "confirmed"
        assert receipt["reply_post_id"] == "999"
        raise KeyboardInterrupt

    install_receipt_bound_x_request_stub(monkeypatch, confirmed_remote)
    monkeypatch.setattr(bot, "begin_confirmed_post_sigint_deferral", lambda: guard)
    monkeypatch.setattr(bot, "end_confirmed_post_sigint_deferral", deliver_sigint)

    with pytest.raises(KeyboardInterrupt):
        bot._reply_assembly().post_with_current_owners(
            state=state,
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="mention",
        )

    assert remote_calls == 1
    assert bot.reconcile_confirmed_reply_receipt(state) is True
    assert bot.load_confirmed_reply_receipt() == ("absent", None)
    assert state["replied_to_ids"] == ["100"]
    assert state["own_auto_reply_ids"] == ["999"]


def test_reply_post_return_inspection_failure_restores_guard_and_keeps_barriers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sending = unit_sending_v4_reply_receipt()
    state = bot.default_state()
    baseline = copy.deepcopy(state)
    guard = object()
    ended: list[object] = []
    install_receipt_bound_x_request_stub(
        monkeypatch,
        lambda *_args, **_kwargs: {"data": {"id": "999"}},
    )
    monkeypatch.setattr(bot, "begin_confirmed_post_sigint_deferral", lambda: guard)
    monkeypatch.setattr(
        bot,
        "end_confirmed_post_sigint_deferral",
        lambda actual: ended.append(actual),
    )
    monkeypatch.setattr(
        bot,
        "inspect_confirmed_transport_transaction",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            bot.TransportJournalError("injected post-return inspection failure")
        ),
    )

    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="identity could not"):
        bot._reply_assembly().post_with_current_owners(
            state=state,
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="mention",
        )

    assert ended == [guard]
    assert state == baseline
    assert bot.load_confirmed_reply_receipt() == ("sending", sending)
    assert bot.inspect_transport_state(
        bot.journal_path_for_receipt(bot.CONFIRMED_REPLY_RECEIPT_FILE)
    ).blocking


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
def test_reply_sigint_during_confirmed_promotion_reconciles_without_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lane: str,
    default_sigint_handler: None,
) -> None:
    sending = unit_sending_v4_reply_receipt(lane=lane)
    original_replace = bot.replace_bound_source_receipt
    original_begin = bot.begin_confirmed_post_sigint_deferral
    guard_holder: dict[str, bot.ConfirmedPostSigintDeferral] = {}
    remote_calls = 0

    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)

    def begin_deferral() -> bot.ConfirmedPostSigintDeferral:
        guard = original_begin()
        guard_holder["guard"] = guard
        return guard

    def interrupt_after_confirmed_promotion(
        binding: object,
        replacement: bytes,
        **kwargs: object,
    ) -> None:
        original_replace(binding, replacement, **kwargs)
        guard_holder["guard"].handle(signal.SIGINT, None)

    def confirmed_remote(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal remote_calls
        remote_calls += 1
        return {"data": {"id": "999"}}

    monkeypatch.setattr(bot, "begin_confirmed_post_sigint_deferral", begin_deferral)
    monkeypatch.setattr(
        bot,
        "replace_bound_source_receipt",
        interrupt_after_confirmed_promotion,
    )
    install_receipt_bound_x_request_stub(monkeypatch, confirmed_remote)

    with pytest.raises(KeyboardInterrupt):
        bot._reply_assembly().post_with_current_owners(
            state=bot.default_state(),
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane=lane,
        )
    assert signal.getsignal(signal.SIGINT) == signal.default_int_handler

    status, receipt = bot.load_confirmed_reply_receipt()
    assert status == "valid"
    assert receipt is not None
    assert receipt["reply_post_id"] == "999"
    assert remote_calls == 1

    restarted_state = bot.default_state()
    restarted_state["daily_reply_date"] = str(receipt["daily_reply_date"])
    if lane == "quote_tweet":
        restarted_state["daily_quote_reply_date"] = str(
            receipt["daily_quote_reply_date"]
        )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda *_args, **_kwargs: pytest.fail(
            "confirmed receipt restart reconciliation must not repeat X"
        ),
    )

    assert bot.reconcile_confirmed_reply_receipt(restarted_state) is True
    assert bot.load_confirmed_reply_receipt() == ("absent", None)
    assert restarted_state["own_auto_reply_ids"] == ["999"]
    if lane == "quote_tweet":
        assert restarted_state["replied_to_quote_post_ids"] == ["100"]
        assert restarted_state["seen_quote_post_ids"] == ["100"]
    else:
        assert restarted_state["replied_to_ids"] == ["100"]
    assert remote_calls == 1


def test_remove_reply_receipt_refuses_changed_transaction() -> None:
    sending = unit_sending_reply_receipt()
    bot._reply_assembly()._reply_receipts_owner().write(sending, confirmed=False)
    changed = {**sending, "target_id": "101"}

    with pytest.raises(
        bot.InvalidConfirmedReplyReceipt,
        match="transaction identity changed",
    ):
        bot.remove_confirmed_reply_receipt(changed)

    assert bot.load_confirmed_reply_receipt() == ("sending", sending)


@pytest.mark.parametrize(
    "lane",
    [
        "quote_image",
        "daily_meme",
        "conversational_reply",
        "historical_context_reply",
    ],
)
def test_final_receipt_cleanup_fsync_failure_latches_every_public_lane(
    lane: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pathname absence cannot reopen provider work after uncertain cleanup."""

    if lane == "quote_image":
        path = bot.REGULAR_POST_RECEIPT_FILE
        receipt = {"unit": lane}
        bot.atomic_write_json(path, receipt, durable=True)
        retire = lambda: bot.remove_regular_post_receipt(receipt, commit_proof=proof)
    elif lane == "daily_meme":
        path = bot.MEME_POST_RECEIPT_FILE
        receipt = {"unit": lane}
        bot.atomic_write_json(path, receipt, durable=True)
        retire = lambda: bot.remove_meme_post_receipt(receipt, commit_proof=proof)
    elif lane == "conversational_reply":
        path = bot.CONFIRMED_REPLY_RECEIPT_FILE
        receipt = unit_confirmed_reply_receipt()
        bot.atomic_write_json(path, receipt, durable=True)
        retire = lambda: bot.remove_confirmed_reply_receipt(receipt, commit_proof=proof)
    else:
        from historical_context_formatter import HistoricalContextReplyStore

        path = bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
        receipt = {"unit": lane}
        bot.atomic_write_json(path, receipt, durable=True)
        store = HistoricalContextReplyStore(
            tmp_path / "context-history.json",
            path,
            mutation_authority_provider=bot.transaction_mutation_authority,
            retirement_uncertainty_callback=(
                bot.latch_source_receipt_retirement_uncertainty
            ),
        )
        retire = lambda: store._retire_exact_receipt(path.read_bytes())

    if lane != "historical_context":
        from mrs_bot_state_generation import record_receipt_commit
        state = bot.default_state()
        record_receipt_commit(state, receipt)
        proof = bot.save_state(state, durable=True)

    paths = exact_retirement_module.retirement_barrier_paths(path)
    real_fsync = exact_retirement_module._fsync_directory

    def fail_after_final_namespace_cleanup(directory_fd: int) -> None:
        real_fsync(directory_fd)
        if not any(os.path.lexists(candidate) for candidate in paths):
            raise OSError("final receipt namespace fsync failed")

    monkeypatch.setattr(
        exact_retirement_module,
        "_fsync_directory",
        fail_after_final_namespace_cleanup,
    )

    with pytest.raises(OSError, match="final receipt namespace fsync failed"):
        retire()

    assert not any(os.path.lexists(candidate) for candidate in paths)
    assert bot.remote_write_safety_incident_is_latched() is True
    assert bot.ambiguous_remote_post_is_blocking() is True
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.require_remote_operation_unpaused("auxiliary provider request")


def test_clarification_receipt_requires_grounded_direct_reply_metadata() -> None:
    receipt = {
        "schema_version": 1,
        "target_id": "101",
        "reply_post_id": "900001",
        "author_id": "200",
        "reply_epoch": 2_000_000_000,
        "daily_reply_date": "2033-05-18",
        "candidate_source": "mention",
        "conversation_id": "700",
        "reply_text": "A rhetorical diversion.",
        "clarification_reply": {
            "thread_id": "700",
            "prior_bot_reply_id": "900",
            "original_question_id": "100",
            "trigger": "explicit_correction",
        },
    }

    assert bot._reply_assembly()._reply_receipt_values_owner().confirmed_is_valid(receipt) is False


def test_clarification_receipt_accepts_valid_single_call_clarification_draft() -> None:
    request = {
        "original_question": "Where did people run when the Berlin Wall fell?",
        "correction": "That did not answer my question.",
    }
    receipt = unit_confirmed_reply_receipt(
        target_id="101",
        reply_post_id="900001",
        contribution=request["correction"],
        text="When free to choose, people choose freedom.",
        clarification_request=request,
        conversation_id="700",
    )
    receipt["clarification_reply"] = {
        "thread_id": "700",
        "prior_bot_reply_id": "900",
        "original_question_id": "100",
        "trigger": "explicit_correction",
    }

    assert bot._reply_assembly()._reply_receipt_values_owner().confirmed_is_valid(receipt) is True


def test_confirmed_reply_receipt_preserves_ai_draft_after_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))
    state = bot.default_state()
    state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")
    receipt = unit_confirmed_reply_receipt(
        target_id="100",
        reply_post_id="900000",
        author_id="200",
        text="People moved from East Germany towards West Germany in November 1989.",
        epoch=fixed_epoch,
        factual=True,
    )

    bot._reply_assembly()._reply_receipts_owner().write(receipt, confirmed=True)
    assert bot.reconcile_confirmed_reply_receipt(state) is True
    bot._reply_assembly()._reply_receipts_owner().write(receipt, confirmed=True)
    assert bot.reconcile_confirmed_reply_receipt(state) is True

    assert len(state["ai_reply_history"]) == 1
    history = state["ai_reply_history"][0]
    assert history["target_id"] == "100"
    assert history["reply_post_id"] == "900000"
    assert history["author_id"] == "200"
    assert history["conversation_id"] == "100"
    assert history["incoming_contribution"] == "A contribution."
    assert history["strategy_version"] == STRATEGY_VERSION
    assert history["reply_kind"] == "direct_factual"
    assert history["used_fact_ids"] == ["F1"]
    assert history["validated_draft_hash"] == receipt["ai_reply_draft"][
        "validated_draft_hash"
    ]
    assert bot._reply_assembly()._reply_history_owner().recent_same_author_interactions(
        state,
        author_id="200",
        conversation_id="different-conversation",
        target_id="new-target",
        before_epoch=fixed_epoch + 1,
    ) == [
        {
            "contributor": "A contribution.",
            "account_reply": (
                "People moved from East Germany towards West Germany in November "
                "1989."
            ),
        }
    ]


def test_confirmed_reply_receipt_rejects_malformed_ai_draft() -> None:
    receipt = unit_confirmed_reply_receipt(
        text="People moved from East Germany towards West Germany in November 1989.",
        factual=True,
    )
    receipt["ai_reply_draft"]["proposed_reply"] = {"not": "a string"}
    assert bot._reply_assembly()._reply_receipt_values_owner().confirmed_is_valid(receipt) is False

    receipt = unit_confirmed_reply_receipt(
        text="People moved from East Germany towards West Germany in November 1989.",
        factual=True,
    )
    receipt["ai_reply_draft"]["mode"] = []
    assert bot._reply_assembly()._reply_receipt_values_owner().confirmed_is_valid(receipt) is False


def test_confirmed_reply_receipt_rejects_unexpected_legacy_approval_field() -> None:
    receipt = unit_confirmed_reply_receipt(text="An alleged correction.")
    receipt["ai_reply_draft"]["legacy_approval"] = "revise"
    assert bot._reply_assembly()._reply_receipt_values_owner().confirmed_is_valid(receipt) is False


@pytest.mark.parametrize("field", ["used_fact_sources", "used_fact_ids", "trusted_fact_ids"])
def test_confirmed_reply_receipt_rejects_incomplete_or_changed_evidence(field: str) -> None:
    receipt = unit_confirmed_reply_receipt(
        text="People moved from East Germany towards West Germany in November 1989.",
        factual=True,
    )
    receipt["ai_reply_draft"][field] = []
    assert bot._reply_assembly()._reply_receipt_values_owner().confirmed_is_valid(receipt) is False


@pytest.mark.parametrize(
    ("outer_field", "bad_value"),
    [
        ("target_id", "101"),
        ("conversation_id", "101"),
        ("candidate_source", "hot_post_reply"),
    ],
)
def test_confirmed_reply_receipt_binds_outer_identity_to_approved_context(
    outer_field: str,
    bad_value: str,
) -> None:
    receipt = unit_confirmed_reply_receipt()
    receipt[outer_field] = bad_value

    assert bot._reply_assembly()._reply_receipt_values_owner().confirmed_is_valid(receipt) is False


def test_confirmed_quote_tweet_receipt_binds_original_post_to_context() -> None:
    receipt = unit_confirmed_reply_receipt(lane="quote_tweet", original_post_id="900")
    receipt["original_post_id"] = "901"

    assert bot._reply_assembly()._reply_receipt_values_owner().confirmed_is_valid(receipt) is False


def test_confirmed_quote_tweet_reply_receipt_reconciliation_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

    state = bot.default_state()
    state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")
    state["daily_quote_reply_date"] = state["daily_reply_date"]
    receipt = unit_confirmed_reply_receipt(
        target_id="910",
        reply_post_id="900000",
        author_id="310",
        lane="quote_tweet",
        epoch=fixed_epoch,
    )

    bot._reply_assembly()._reply_receipts_owner().write(receipt, confirmed=True)
    assert bot.reconcile_confirmed_reply_receipt(state) is True
    bot._reply_assembly()._reply_receipts_owner().write(receipt, confirmed=True)
    assert bot.reconcile_confirmed_reply_receipt(state) is True

    assert state["daily_reply_count"] == 1
    assert state["daily_quote_reply_count"] == 1
    assert state["daily_replied_author_counts"] == {"310": 1}
    assert state["replied_to_quote_post_ids"].count("910") == 1
    assert state["seen_quote_post_ids"].count("910") == 1
    assert state["own_auto_reply_ids"].count("900000") == 1


def test_confirmed_reply_receipt_persistence_failure_keeps_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))
    receipt = unit_confirmed_reply_receipt(
        target_id="100",
        reply_post_id="900000",
        author_id="200",
        epoch=fixed_epoch,
    )
    state = bot.default_state()
    state["daily_reply_date"] = receipt["daily_reply_date"]

    bot._reply_assembly()._reply_receipts_owner().write(receipt, confirmed=True)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: (_ for _ in ()).throw(OSError("state failed")))

    with pytest.raises(bot.ConfirmedReplyLocalPersistenceError):
        bot.reconcile_confirmed_reply_receipt(state)

    assert bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
    assert "100" in state["replied_to_ids"]


def test_confirmed_reply_normal_success_uses_durable_state_before_receipt_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_scenario(SCENARIOS / "normal_mention_reply.json")
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", tmp_path / "extra_quote_watch_post_ids.txt")
        _configure_test_x_base(monkeypatch, server.url)
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "MARK_AI_REPLIES_AS_AI", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))
        patch_reply_owner_method(
            monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
            legacy_reply_evaluator(lambda context, *_args, **_kwargs: unit_approved_reply(context)),
        )

        original_save_state = bot.save_state
        save_calls: list[bool] = []
        receipt_remove_seen = False

        def tracking_save_state(state: dict, **kwargs: object) -> None:
            save_calls.append(bool(kwargs.get("durable", False)))
            return original_save_state(state, **kwargs)

        def tracking_remove_receipt(receipt: dict | None = None, *, commit_proof=None) -> None:
            nonlocal receipt_remove_seen
            assert save_calls and save_calls[-1] is True
            receipt_remove_seen = True
            bot.CONFIRMED_REPLY_RECEIPT_FILE.unlink()

        state = bot.default_state()
        state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")
        monkeypatch.setattr(bot, "save_state", tracking_save_state)
        monkeypatch.setattr(bot, "remove_confirmed_reply_receipt", tracking_remove_receipt)

        assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_POSTED
        assert receipt_remove_seen is True
    finally:
        server.stop()


def test_confirmed_reply_latest_backup_recovers_suppression_after_primary_corruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_scenario(SCENARIOS / "normal_mention_reply.json")
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        state_file = tmp_path / "bot_state.json"
        monkeypatch.setattr(bot, "STATE_FILE", state_file)
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 2)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", tmp_path / "extra_quote_watch_post_ids.txt")
        _configure_test_x_base(monkeypatch, server.url)
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "MARK_AI_REPLIES_AS_AI", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))
        patch_reply_owner_method(
            monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
            legacy_reply_evaluator(lambda context, *_args, **_kwargs: unit_approved_reply(context)),
        )

        state = bot.default_state()
        state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")

        assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_POSTED
        assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
        latest_backup = json.loads((state_file.with_name("bot_state.json.bak1")).read_text(encoding="utf-8"))
        assert "100" in latest_backup["replied_to_ids"]

        state_file.write_text("{bad json", encoding="utf-8")
        recovered = bot.load_state()

        assert "100" in recovered["replied_to_ids"]
        assert recovered["last_seen_mention_id"] == "100"
        assert recovered["own_auto_reply_ids"] == ["900000"]
        assert recovered["daily_reply_count"] == 1
    finally:
        server.stop()


def test_malformed_confirmed_reply_receipt_blocks_mention_replies(tmp_path: Path) -> None:
    bot.CONFIRMED_REPLY_RECEIPT_FILE.write_text("{bad json", encoding="utf-8")
    state = bot.default_state()

    with pytest.raises(bot.InvalidConfirmedReplyReceipt):
        bot.reconcile_confirmed_reply_receipt(state)

    assert bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()


def test_confirmed_quote_tweet_reply_save_failure_replays_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_scenario(SCENARIOS / "quote_tweet_reply.json")
    scenario["grok_replies"] = [
        "Responsibility matters more than rhetoric.",
        "Responsibility matters more than rhetoric.",
    ]
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        state_file = tmp_path / "bot_state.json"
        monkeypatch.setattr(bot, "STATE_FILE", state_file)
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        _configure_test_x_base(monkeypatch, server.url)
        patch_tweet_lookup_method(monkeypatch, "fetch", lambda tweet_id, **_kwargs: copy.deepcopy(
                scenario["tweets"].get(str(tweet_id))
                or scenario["quote_tweets"]["900"]["data"][0]
                if str(tweet_id) == "910"
                else scenario["tweets"].get(str(tweet_id))
            ))
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
        monkeypatch.setattr(bot, "MARK_AI_REPLIES_AS_AI", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_QUOTE_REPLIES_PER_DAY", 10)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "QUOTE_REPLY_DELAY_SECONDS", 0)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))
        patch_reply_owner_method(
            monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
            legacy_reply_evaluator(lambda context, *_args, **_kwargs: unit_approved_reply(
                context,
                text="Responsibility matters.",
                mode="opinion_or_principle",
            )),
        )

        original_save_state = bot.save_state
        initial_state = bot.default_state()
        initial_state.update(
            {
                "recent_own_post_ids": ["900"],
                "daily_reply_date": bot.current_datetime().strftime("%Y-%m-%d"),
                "daily_quote_reply_date": bot.current_datetime().strftime("%Y-%m-%d"),
                "daily_reply_count": 0,
                "daily_quote_reply_count": 0,
                "last_reply_epoch": 0,
                "replied_to_quote_post_ids": [],
                "seen_quote_post_ids": [],
                "daily_replied_author_ids": [],
                "daily_replied_author_counts": {},
                "own_auto_reply_ids": [],
                "tweet_cache": {},
            }
        )
        original_save_state(initial_state)

        def fail_first_post_success_save(state: dict, **kwargs: object) -> None:
            if server.posts:
                raise OSError("injected quote post-success save failure")
            return original_save_state(state, **kwargs)

        monkeypatch.setattr(bot, "save_state", fail_first_post_success_save)
        first_state = bot.load_state()

        with pytest.raises(OSError, match="injected quote post-success save failure"):
            bot.maybe_reply_to_quote_tweets(first_state)

        assert len(server.posts) == 1
        first_reply_id = "900000"
        assert server.posts[0]["reply"]["in_reply_to_tweet_id"] == "910"

        durable_after_failed_save = json.loads(state_file.read_text(encoding="utf-8"))
        assert "910" not in durable_after_failed_save.get("replied_to_quote_post_ids", [])
        assert "910" not in durable_after_failed_save.get("seen_quote_post_ids", [])
        assert durable_after_failed_save.get("daily_reply_count") == 0
        assert durable_after_failed_save.get("daily_quote_reply_count") == 0
        assert durable_after_failed_save.get("last_reply_epoch") == 0
        assert durable_after_failed_save.get("daily_replied_author_counts", {}) == {}
        assert first_reply_id not in durable_after_failed_save.get("own_auto_reply_ids", [])

        monkeypatch.setattr(bot, "save_state", original_save_state)
        restarted_state = bot.load_state()
        second_status = bot.maybe_reply_to_quote_tweets(restarted_state)

        assert second_status == bot.QUOTE_CHECK_STATUS_CHECKED
        assert [post["reply"]["in_reply_to_tweet_id"] for post in server.posts] == ["910"]
    finally:
        server.stop()


def test_quote_tweet_receipt_reconciled_by_mention_lane_counts_quote_reply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_scenario(SCENARIOS / "quote_tweet_reply.json")
    scenario["grok_replies"] = [
        "Responsibility matters more than rhetoric.",
    ]
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        reply_date = datetime.fromtimestamp(fixed_epoch).strftime("%Y-%m-%d")
        prior_date = "2033-05-17"
        state_file = tmp_path / "bot_state.json"
        monkeypatch.setattr(bot, "STATE_FILE", state_file)
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", tmp_path / "extra_quote_watch_post_ids.txt")
        _configure_test_x_base(monkeypatch, server.url)
        patch_tweet_lookup_method(monkeypatch, "fetch", lambda tweet_id, **_kwargs: copy.deepcopy(
                scenario["tweets"].get(str(tweet_id))
                or scenario["quote_tweets"]["900"]["data"][0]
                if str(tweet_id) == "910"
                else scenario["tweets"].get(str(tweet_id))
            ))
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
        monkeypatch.setattr(bot, "MARK_AI_REPLIES_AS_AI", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_QUOTE_REPLIES_PER_DAY", 10)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "QUOTE_REPLY_DELAY_SECONDS", 0)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))
        patch_reply_owner_method(
            monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
            legacy_reply_evaluator(lambda context, *_args, **_kwargs: unit_approved_reply(
                context,
                text="Responsibility matters.",
                mode="opinion_or_principle",
            )),
        )

        original_save_state = bot.save_state
        initial_state = bot.default_state()
        initial_state.update(
            {
                "recent_own_post_ids": ["900"],
                "daily_reply_date": reply_date,
                "daily_quote_reply_date": prior_date,
                "daily_reply_count": 0,
                "daily_quote_reply_count": 0,
                "last_reply_epoch": 0,
                "replied_to_ids": [],
                "replied_to_quote_post_ids": [],
                "seen_quote_post_ids": [],
                "daily_replied_author_ids": [],
                "daily_replied_author_counts": {},
                "own_auto_reply_ids": [],
                "tweet_cache": {},
            }
        )
        original_save_state(initial_state)

        def fail_first_post_success_save(state: dict, **kwargs: object) -> None:
            if server.posts:
                raise OSError("injected quote post-success save failure")
            return original_save_state(state, **kwargs)

        monkeypatch.setattr(bot, "save_state", fail_first_post_success_save)
        first_state = bot.load_state()

        with pytest.raises(OSError, match="injected quote post-success save failure"):
            bot.maybe_reply_to_quote_tweets(first_state)

        assert [post["reply"]["in_reply_to_tweet_id"] for post in server.posts] == ["910"]
        durable_after_failed_save = json.loads(state_file.read_text(encoding="utf-8"))
        assert durable_after_failed_save["daily_quote_reply_date"] == reply_date
        assert durable_after_failed_save["daily_quote_reply_count"] == 0
        assert bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()

        monkeypatch.setattr(bot, "save_state", original_save_state)
        restarted_state = bot.load_state()

        assert bot.maybe_reply_to_mentions(restarted_state) == bot.NORMAL_CHECK_STATUS_CHECKED
        assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
        assert restarted_state["daily_quote_reply_date"] == reply_date
        assert restarted_state["daily_quote_reply_count"] == 1

        bot.reset_daily_quote_reply_count_if_needed(restarted_state)
        assert restarted_state["daily_quote_reply_date"] == reply_date
        assert restarted_state["daily_quote_reply_count"] == 1
        assert [post["reply"]["in_reply_to_tweet_id"] for post in server.posts] == ["910"]
    finally:
        server.stop()
