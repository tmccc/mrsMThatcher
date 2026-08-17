from __future__ import annotations

import copy
from datetime import datetime, timedelta

import pytest

import mrsMThatcher2 as bot
import mrs_log_digest as digest


def mention(tweet_id: int, author_id: int, text: str = "@MrsMThatcher A contribution.") -> dict:
    return {
        "id": str(tweet_id),
        "author_id": str(author_id),
        "conversation_id": str(tweet_id),
        "text": text,
        "entities": {
            "mentions": [
                {"id": str(bot.MY_USER_ID), "username": "MrsMThatcher"}
            ]
        },
        "referenced_tweets": [],
    }


def configure_provider_free_mention_check(
    monkeypatch: pytest.MonkeyPatch,
    candidates: list[dict],
    *,
    current_epoch: int,
) -> None:
    enabled = copy.deepcopy(bot.tested_reply_pipeline)
    enabled["enabled"] = True
    monkeypatch.setattr(bot, "tested_reply_pipeline", enabled)
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "DRY_RUN_REPLIES", False)
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
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(
        bot, "is_probably_spam_or_not_worth_replying", lambda _text: False
    )
    monkeypatch.setattr(
        bot,
        "build_context_for_reply_ai",
        lambda candidate, _state: (
            {
                "target_id": str(candidate["id"]),
                "thread_id": str(candidate["conversation_id"]),
                "lane": "mention",
                "incoming_contribution": str(candidate["text"]),
                "parent_thread": [],
            },
            True,
        ),
    )
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: object())
    monkeypatch.setattr(
        bot, "reply_media_context_for_candidate", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)


def test_three_qualifying_no_replies_start_author_quarantine() -> None:
    state = bot.default_state()
    start = 2_000_000_000

    assert bot.record_qualifying_author_no_reply(state, "200", current_epoch=start) is False
    assert bot.record_qualifying_author_no_reply(state, "200", current_epoch=start + 1) is False
    assert bot.record_qualifying_author_no_reply(state, "200", current_epoch=start + 2) is True

    record = state["author_evaluation_quarantines"]["200"]
    assert record["recent_no_reply_epochs"] == [start, start + 1, start + 2]
    assert record["quarantine_until_epoch"] == start + 2 + 43_200


def test_only_resolved_spam_or_abuse_majorities_qualify() -> None:
    base = {
        "reply_necessity_outcome": "confirm_no_reply_spam_or_abuse",
        "reply_necessity_majority_resolvable": True,
    }
    assert bot.tested_pipeline_no_reply_qualifies_for_author_quarantine(
        status="no_reply",
        reason="reply_necessity_review",
        telemetry=base,
    ) is True
    assert bot.tested_pipeline_no_reply_qualifies_for_author_quarantine(
        status="no_reply",
        reason="reply_necessity_review",
        telemetry={**base, "reply_necessity_majority_resolvable": False},
    ) is False
    assert bot.tested_pipeline_no_reply_qualifies_for_author_quarantine(
        status="no_reply",
        reason="reply_necessity_review",
        telemetry={**base, "reply_necessity_outcome": "confirm_no_reply"},
    ) is False
    assert bot.tested_pipeline_no_reply_qualifies_for_author_quarantine(
        status="no_reply",
        reason="unquoted abusive epithet is grammatically directed at a permitted personal target",
        telemetry={
            "deterministic_suppressed": True,
            "deterministic_reason": "unquoted abusive epithet is grammatically directed at a permitted personal target",
        },
    ) is False
    assert bot.tested_pipeline_no_reply_qualifies_for_author_quarantine(
        status="no_reply",
        reason="group_hostility_suppression",
        telemetry={"group_hostility_outcome": "suppress_group_hostility"},
    ) is False
    assert bot.tested_pipeline_no_reply_qualifies_for_author_quarantine(
        status="no_reply",
        reason="allegation_review_suppression",
        telemetry={
            "allegation_conspiracy_outcome": "confirm_no_reply_spam_or_abuse",
            "allegation_conspiracy_majority_resolvable": True,
        },
    ) is True


def test_strikes_outside_window_do_not_start_quarantine() -> None:
    state = bot.default_state()
    start = 2_000_000_000
    bot.record_qualifying_author_no_reply(state, "200", current_epoch=start)
    bot.record_qualifying_author_no_reply(
        state,
        "200",
        current_epoch=start + bot.AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS,
    )
    started = bot.record_qualifying_author_no_reply(
        state,
        "200",
        current_epoch=start + bot.AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS + 1,
    )

    assert started is False
    assert state["author_evaluation_quarantines"]["200"]["quarantine_until_epoch"] == 0


def test_approved_reply_clears_author_strikes() -> None:
    state = bot.default_state()
    bot.record_qualifying_author_no_reply(state, "200", current_epoch=2_000_000_000)
    assert bot.clear_author_evaluation_quarantine_history(state, "200") is True
    assert state["author_evaluation_quarantines"] == {}


def test_operational_failure_does_not_add_strike(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    candidate = mention(100, 200)
    configure_provider_free_mention_check(
        monkeypatch, [candidate], current_epoch=2_000_000_000
    )

    def fail(*_args: object, **_kwargs: object) -> None:
        raise bot.ApiError("provider unavailable", service="xai")

    monkeypatch.setattr(bot, "generate_ai_first_reply", fail)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_API_ERROR
    assert state["author_evaluation_quarantines"] == {}


def test_active_quarantine_reports_one_skipped_pipeline_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = 2_000_000_000
    state = bot.default_state()
    for offset in range(3):
        bot.record_qualifying_author_no_reply(
            state, "200", current_epoch=current - 3 + offset
        )
    candidate = mention(100, 200)
    state["last_seen_mention_id"] = "99"
    state["mention_pending_candidates"] = {"100": copy.deepcopy(candidate)}
    configure_provider_free_mention_check(monkeypatch, [candidate], current_epoch=current)
    monkeypatch.setattr(bot, "clarification_reply_context", lambda *_args, **_kwargs: None)
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **values: events.append((name, values)),
    )
    monkeypatch.setattr(
        bot,
        "build_context_for_reply_ai",
        lambda *_args, **_kwargs: pytest.fail("quarantine must precede context work"),
    )
    monkeypatch.setattr(
        bot,
        "generate_ai_first_reply",
        lambda *_args, **_kwargs: pytest.fail("quarantine must make zero provider calls"),
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    terminal = bot.terminal_reply_evaluation(state, "100")
    assert terminal is not None
    assert terminal["reason"] == "author_evaluation_quarantine"
    assert (
        terminal["evidence_policy"]
        == bot.AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY
    )
    quarantine_events = [
        values
        for name, values in events
        if name == "author_evaluation_quarantine_skip"
    ]
    assert len(quarantine_events) == 1
    assert quarantine_events[0]["pipeline_evaluations_skipped"] == 1


def test_active_quarantine_blocks_valid_clarification_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = 2_000_000_000
    state = bot.default_state()
    for offset in range(3):
        bot.record_qualifying_author_no_reply(
            state, "200", current_epoch=current - 3 + offset
        )
    candidate = mention(100, 200, "@MrsMThatcher That did not answer my question.")
    state["last_seen_mention_id"] = "99"
    state["mention_pending_candidates"] = {"100": copy.deepcopy(candidate)}
    configure_provider_free_mention_check(monkeypatch, [candidate], current_epoch=current)
    monkeypatch.setattr(
        bot,
        "clarification_reply_context",
        lambda *_args, **_kwargs: {
            "thread_id": "100",
            "prior_bot_reply_id": "90",
            "original_question_id": "80",
            "question_text": "What policy follows from that?",
            "trigger": "explicit_correction",
        },
    )
    monkeypatch.setattr(
        bot,
        "build_context_for_reply_ai",
        lambda *_args, **_kwargs: pytest.fail(
            "active quarantine must block clarification context work"
        ),
    )
    monkeypatch.setattr(
        bot,
        "generate_ai_first_reply",
        lambda *_args, **_kwargs: pytest.fail(
            "active quarantine must block clarification provider work"
        ),
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    terminal = bot.terminal_reply_evaluation(state, "100")
    assert terminal is not None
    assert terminal["reason"] == "author_evaluation_quarantine"


def test_expired_quarantine_allows_valid_clarification_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = 2_000_000_000
    state = bot.default_state()
    for offset in range(3):
        bot.record_qualifying_author_no_reply(
            state, "200", current_epoch=start + offset
        )
    current = start + 2 + bot.AUTHOR_NO_REPLY_QUARANTINE_SECONDS
    candidate = mention(100, 200, "@MrsMThatcher That did not answer my question.")
    configure_provider_free_mention_check(monkeypatch, [candidate], current_epoch=current)
    monkeypatch.setattr(
        bot,
        "clarification_reply_context",
        lambda *_args, **_kwargs: {
            "thread_id": "100",
            "prior_bot_reply_id": "90",
            "original_question_id": "80",
            "question_text": "What policy follows from that?",
            "trigger": "explicit_correction",
        },
    )
    evaluated: list[str] = []

    def no_reply(
        context: dict,
        *_args: object,
        evaluation_outcome: dict | None = None,
        **_kwargs: object,
    ) -> None:
        assert context["clarification_request"] == {
            "original_question": "What policy follows from that?",
            "correction": "@MrsMThatcher That did not answer my question.",
        }
        evaluated.append(str(context["target_id"]))
        assert evaluation_outcome is not None
        evaluation_outcome.update(
            {
                "status": "no_reply",
                "reason": "reply_necessity_review",
                "qualifying_author_no_reply": False,
            }
        )
        return None

    monkeypatch.setattr(bot, "generate_ai_first_reply", no_reply)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert evaluated == ["100"]
    assert state["author_evaluation_quarantines"] == {}
    assert bot.terminal_reply_evaluation(state, "100")["reason"] == "reply_necessity_review"


def test_quarantine_survives_reload_expires_and_is_pruned(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = 2_000_000_000
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "now_epoch", lambda: start + 3)
    state = bot.default_state()
    for offset in range(3):
        bot.record_qualifying_author_no_reply(
            state, "200", current_epoch=start + offset
        )
    bot.save_state(state, durable=True)

    loaded = bot.load_state()
    assert bot.active_author_evaluation_quarantine(
        loaded, "200", current_epoch=start + 3
    ) is not None
    assert bot.active_author_evaluation_quarantine(
        loaded,
        "200",
        current_epoch=start + 2 + bot.AUTHOR_NO_REPLY_QUARANTINE_SECONDS,
    ) is None
    assert loaded["author_evaluation_quarantines"] == {}

    loaded["author_evaluation_quarantines"]["201"] = {
        "recent_no_reply_epochs": [start],
        "quarantine_until_epoch": 0,
        "last_updated_epoch": start,
        "evidence_policy": bot.AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY,
    }
    assert bot.prune_author_evaluation_quarantines(
        loaded,
        current_epoch=start + bot.AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS,
    ) is True
    assert loaded["author_evaluation_quarantines"] == {}


def test_qualifying_no_reply_epochs_stay_bounded_through_five_backup_generations(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = 2_000_000_000
    insertion_count = bot.AUTHOR_NO_REPLY_EPOCH_LIMIT + 5
    current = start + insertion_count
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 5)
    monkeypatch.setattr(bot, "now_epoch", lambda: current)
    state = bot.default_state()

    for offset in range(insertion_count):
        bot.record_qualifying_author_no_reply(
            state,
            "200",
            current_epoch=start + offset,
        )

    expected_epochs = list(range(start + 5, start + insertion_count))
    record = state["author_evaluation_quarantines"]["200"]
    assert len(record["recent_no_reply_epochs"]) == bot.AUTHOR_NO_REPLY_EPOCH_LIMIT
    assert record["recent_no_reply_epochs"] == expected_epochs
    assert bot.normalise_author_evaluation_quarantines(
        state["author_evaluation_quarantines"],
        path=state_file,
    ) == state["author_evaluation_quarantines"]

    for _generation in range(bot.STATE_BACKUP_COUNT + 1):
        bot.save_state(state, durable=True)
        state = bot.load_state()
        record = state["author_evaluation_quarantines"]["200"]
        assert record["recent_no_reply_epochs"] == expected_epochs
        assert bot.active_author_evaluation_quarantine(
            state,
            "200",
            current_epoch=current,
        ) is not None

    assert all(
        state_file.with_name(f"{state_file.name}.bak{index}").is_file()
        for index in range(1, bot.STATE_BACKUP_COUNT + 1)
    )


def test_legacy_broad_quarantine_records_are_dropped_during_load_normalisation(
    tmp_path,
) -> None:
    legacy = {
        "200": {
            "recent_no_reply_epochs": [2_000_000_000],
            "quarantine_until_epoch": 2_000_043_200,
            "last_updated_epoch": 2_000_000_000,
        }
    }

    assert bot.normalise_author_evaluation_quarantines(
        legacy,
        path=tmp_path / "bot_state.json",
    ) == {}

    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["author_evaluation_quarantines"] = legacy
    state["reply_evaluation_records"] = {
        "100": {
            "target_id": "100",
            "lane": "mention",
            "outcome": "no_reply",
            "reason": "author_evaluation_quarantine",
            "evaluated_epoch": 2_000_000_000,
        }
    }
    normalised = bot.normalise_state_candidate(
        state,
        path=tmp_path / "bot_state.json",
    )
    assert normalised is not None
    assert normalised["author_evaluation_quarantines"] == {}
    assert normalised["reply_evaluation_records"] == {}


def test_direct_skips_do_not_consume_fresh_evaluation_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = 2_000_000_000
    own = [mention(tweet_id, int(bot.MY_USER_ID)) for tweet_id in range(1, 7)]
    useful = [mention(tweet_id, 300 + tweet_id) for tweet_id in range(7, 10)]
    state = bot.default_state()
    configure_provider_free_mention_check(
        monkeypatch, own + useful, current_epoch=current
    )
    monkeypatch.setattr(bot, "MAX_MENTIONS_PER_CHECK", 2)
    calls: list[str] = []

    def no_reply(
        context: dict,
        *_args: object,
        evaluation_outcome: dict | None = None,
        **_kwargs: object,
    ) -> None:
        calls.append(str(context["target_id"]))
        assert evaluation_outcome is not None
        evaluation_outcome.update(
            {
                "status": "no_reply",
                "reason": "reply_necessity_review",
                "qualifying_author_no_reply": True,
            }
        )
        return None

    monkeypatch.setattr(bot, "generate_ai_first_reply", no_reply)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert calls == ["7", "8"]
    assert state["last_seen_mention_id"] == "8"


def test_mocked_high_volume_spam_author_does_not_block_later_contributors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = 2_000_000_000
    spam_mentions = [
        mention(tweet_id, 200, "@MrsMThatcher abusive spam")
        for tweet_id in range(1, 7)
    ]
    useful_mentions = [
        mention(7, 301, "@MrsMThatcher A useful question."),
        mention(8, 302, "@MrsMThatcher Another useful contribution."),
    ]
    state = bot.default_state()
    state["last_seen_mention_id"] = "8"
    configure_provider_free_mention_check(
        monkeypatch,
        spam_mentions + useful_mentions,
        current_epoch=current,
    )
    monkeypatch.setattr(bot, "MAX_MENTIONS_PER_CHECK", 5)
    full_pipeline_targets: list[str] = []

    def policy_no_reply(
        context: dict,
        *_args: object,
        evaluation_outcome: dict | None = None,
        **_kwargs: object,
    ) -> None:
        target_id = str(context["target_id"])
        full_pipeline_targets.append(target_id)
        assert evaluation_outcome is not None
        evaluation_outcome.update(
            {
                "status": "no_reply",
                "reason": "reply_necessity_review",
                "qualifying_author_no_reply": target_id in {"1", "2", "3"},
            }
        )
        return None

    monkeypatch.setattr(bot, "generate_ai_first_reply", policy_no_reply)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert full_pipeline_targets == ["1", "2", "3", "7", "8"]
    assert all(
        bot.terminal_reply_evaluation(state, str(tweet_id)) is None
        for tweet_id in range(4, 7)
    )
    assert bot.terminal_reply_evaluation(state, "7") is not None
    assert bot.terminal_reply_evaluation(state, "8") is not None

    print(
        "DEMO quarantine: spam_mentions=6 full_pipeline_spam=3 "
        "quarantine_skips=3 later_contributors_reached=2"
    )


def test_quarantine_skips_batch_one_durable_state_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = 2_000_000_000
    candidates = [mention(tweet_id, 200) for tweet_id in range(1, 101)]
    state = bot.default_state()
    state["last_seen_mention_id"] = "100"
    for offset in range(3):
        bot.record_qualifying_author_no_reply(
            state, "200", current_epoch=current - 3 + offset
        )
    configure_provider_free_mention_check(
        monkeypatch,
        candidates,
        current_epoch=current,
    )
    monkeypatch.setattr(bot, "clarification_reply_context", lambda *_args, **_kwargs: None)
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **values: events.append((name, values)),
    )
    saves: list[bool] = []
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda _state, *, durable=False: saves.append(bool(durable)),
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert saves == [True]
    assert state.get("reply_evaluation_records", {}) == {}
    quarantine_events = [
        values
        for name, values in events
        if name == "author_evaluation_quarantine_skip"
    ]
    assert len(quarantine_events) == len(candidates)
    assert all(event.get("pipeline_evaluations_skipped") == 1 for event in quarantine_events)
    assert all("provider_calls_avoided" not in event for event in quarantine_events)


@pytest.mark.parametrize(
    ("author_cap_reached", "local_spam_rejection"),
    [
        (True, False),
        (False, True),
        (True, True),
    ],
)
def test_quarantine_does_not_credit_deterministic_gate_overlap(
    monkeypatch: pytest.MonkeyPatch,
    author_cap_reached: bool,
    local_spam_rejection: bool,
) -> None:
    current = 2_000_000_000
    state = bot.default_state()
    for offset in range(3):
        bot.record_qualifying_author_no_reply(
            state, "200", current_epoch=current - 3 + offset
        )
    if author_cap_reached:
        state["daily_reply_date"] = bot.reply_cap_date_str(current)
        state["daily_reply_count"] = bot.MAX_REPLIES_PER_AUTHOR_PER_DAY
        state["daily_replied_author_ids"] = ["200"]
        state["daily_replied_author_counts"] = {
            "200": bot.MAX_REPLIES_PER_AUTHOR_PER_DAY
        }
    candidate = mention(100, 200)
    state["last_seen_mention_id"] = "99"
    state["mention_pending_candidates"] = {"100": copy.deepcopy(candidate)}
    configure_provider_free_mention_check(monkeypatch, [candidate], current_epoch=current)
    monkeypatch.setattr(bot, "clarification_reply_context", lambda *_args, **_kwargs: None)
    local_filter_calls: list[str] = []

    def local_filter(text: str) -> bool:
        local_filter_calls.append(text)
        return local_spam_rejection

    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", local_filter)
    monkeypatch.setattr(
        bot,
        "build_context_for_reply_ai",
        lambda *_args, **_kwargs: pytest.fail(
            "quarantine and deterministic gates must precede context work"
        ),
    )
    monkeypatch.setattr(
        bot,
        "generate_ai_first_reply",
        lambda *_args, **_kwargs: pytest.fail(
            "quarantine must make zero provider calls"
        ),
    )
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **values: events.append((name, values)),
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert local_filter_calls == [candidate["text"]]
    quarantine_events = [
        values
        for name, values in events
        if name == "author_evaluation_quarantine_skip"
    ]
    assert len(quarantine_events) == 1
    assert quarantine_events[0]["pipeline_evaluations_skipped"] == 0
    terminal = bot.terminal_reply_evaluation(state, "100")
    assert terminal is not None
    assert terminal["reason"] == "author_evaluation_quarantine"


def test_completed_watermark_prunes_sustained_quarantine_terminal_volume() -> None:
    current = 2_000_000_000
    count = bot.REPLY_EVALUATION_MAX_RECORDS + 1_000
    state = bot.default_state()
    state["last_seen_mention_id"] = str(count)
    state["reply_evaluation_records"] = {
        str(target_id): {
            "target_id": str(target_id),
            "lane": "mention",
            "outcome": "no_reply",
            "reason": "author_evaluation_quarantine",
            "evaluated_epoch": current,
            "evidence_policy": bot.AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY,
        }
        for target_id in range(1, count + 1)
    }

    bot.prune_reply_evaluation_records(state, current_epoch=current)

    assert state["reply_evaluation_records"] == {}


def test_incomplete_backlog_quarantine_terminal_volume_stays_bounded() -> None:
    current = 2_000_000_000
    count = bot.REPLY_EVALUATION_MAX_RECORDS + 1_000
    state = bot.default_state()
    state["last_seen_mention_id"] = "0"
    state["reply_evaluation_records"] = {
        str(target_id): {
            "target_id": str(target_id),
            "lane": "mention",
            "outcome": "no_reply",
            "reason": "author_evaluation_quarantine",
            "evaluated_epoch": current - count + target_id,
            "evidence_policy": bot.AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY,
        }
        for target_id in range(1, count + 1)
    }

    bot.prune_reply_evaluation_records(state, current_epoch=current)

    assert len(state["reply_evaluation_records"]) == bot.REPLY_EVALUATION_MAX_RECORDS
    assert "1" not in state["reply_evaluation_records"]
    assert str(count) in state["reply_evaluation_records"]


def install_mention_pages(
    monkeypatch: pytest.MonkeyPatch,
    pages: dict[str | None, tuple[list[dict], str | None]],
) -> list[dict]:
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


def retire_all_pending(state: dict) -> None:
    for candidate in bot.pending_mention_candidates(state):
        bot.mark_mention_seen_if_applicable(state, candidate)


def test_truncated_pagination_resumes_across_cycles_and_advances_only_on_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = {
        None: ([mention(105, 205), mention(104, 204)], "A"),
        "A": ([mention(103, 203), mention(102, 202)], "B"),
        "B": ([mention(101, 201)], None),
    }
    requests = install_mention_pages(monkeypatch, pages)
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"

    assert [row["id"] for row in bot.get_mentions(state)] == ["104", "105"]
    assert state["last_seen_mention_id"] == "99"
    assert state["mention_backlog"]["next_token"] == "A"
    assert state["mention_backlog"]["pages_completed"] == 1
    assert state["mention_backlog"]["highest_mention_id"] == "105"
    retire_all_pending(state)

    assert [row["id"] for row in bot.get_mentions(state)] == ["102", "103"]
    assert state["last_seen_mention_id"] == "99"
    assert state["mention_backlog"]["next_token"] == "B"
    retire_all_pending(state)

    assert [row["id"] for row in bot.get_mentions(state)] == ["101"]
    assert state["last_seen_mention_id"] == "105"
    assert state["mention_backlog"] == {}
    assert [request.get("pagination_token") for request in requests] == [None, "A", "B"]
    print(
        "DEMO backlog: pages=3 checks=3 resumed_tokens=A,B "
        "watermark_before=99 watermark_during=99 watermark_after=105"
    )


def test_new_mentions_after_backlog_are_fetched_and_duplicates_are_not_reevaluated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = {
        None: ([mention(105, 205)], "A"),
        "A": ([mention(105, 205), mention(101, 201)], None),
    }
    requests = install_mention_pages(monkeypatch, pages)
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    first = bot.get_mentions(state)
    bot.record_terminal_reply_evaluation(
        state, target_id="105", lane="mention", reason="confirmed_no_reply"
    )
    retire_all_pending(state)
    second = bot.get_mentions(state)

    assert [row["id"] for row in first] == ["105"]
    assert [row["id"] for row in second] == ["101"]
    retire_all_pending(state)
    pages[None] = ([mention(106, 206)], None)
    third = bot.get_mentions(state)
    assert [row["id"] for row in third] == ["106"]
    assert requests[-1]["since_id"] == "105"
    assert state["last_seen_mention_id"] == "106"


def test_active_backlog_survives_restart(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_save_state = bot.save_state
    pages = {
        None: ([mention(105, 205)], "A"),
        "A": ([mention(101, 201)], None),
    }
    requests = install_mention_pages(monkeypatch, pages)
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "save_state", original_save_state)
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    bot.get_mentions(state)
    bot.save_state(state, durable=True)

    restarted = bot.load_state()
    assert restarted["mention_backlog"]["next_token"] == "A"
    assert [row["id"] for row in bot.get_mentions(restarted)] == ["105"]
    assert len(requests) == 1
    retire_all_pending(restarted)
    bot.save_state(restarted, durable=True)
    assert [row["id"] for row in bot.get_mentions(restarted)] == ["101"]
    assert requests[-1]["pagination_token"] == "A"
    assert restarted["last_seen_mention_id"] == "105"


def test_final_page_commits_watermark_and_pending_together_before_restart(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_save_state = bot.save_state
    requests = install_mention_pages(
        monkeypatch,
        {None: ([mention(105, 205)], None)},
    )
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)

    class SimulatedCrash(RuntimeError):
        pass

    crashed = False

    def save_then_crash_after_final_commit(state: dict, *, durable: bool = False) -> None:
        nonlocal crashed
        original_save_state(state, durable=durable)
        if (
            not crashed
            and state.get("last_seen_mention_id") == "105"
            and state.get("mention_backlog") == {}
            and set(state.get("mention_pending_candidates", {})) == {"105"}
        ):
            crashed = True
            raise SimulatedCrash("process stopped after final-page state commit")

    monkeypatch.setattr(bot, "save_state", save_then_crash_after_final_commit)
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"

    with pytest.raises(SimulatedCrash):
        bot.get_mentions(state)

    restarted = bot.load_state()
    assert restarted["last_seen_mention_id"] == "105"
    assert restarted["mention_backlog"] == {}
    assert restarted["mention_pagination"] == {}
    assert set(restarted["mention_pending_candidates"]) == {"105"}
    assert [row["id"] for row in bot.get_mentions(restarted)] == ["105"]
    assert len(requests) == 1


def test_repeated_token_persists_every_page_and_resets_after_queue_drains(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_save_state = bot.save_state
    pages = {
        None: ([mention(105, 205)], "A"),
        "A": ([mention(103, 203)], "A"),
    }
    requests = install_mention_pages(monkeypatch, pages)
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "MENTIONS_MAX_PAGES_PER_CHECK", 2)
    monkeypatch.setattr(bot, "save_state", original_save_state)
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"

    returned = bot.get_mentions(state)
    assert [row["id"] for row in returned] == ["103", "105"]
    assert [request.get("pagination_token") for request in requests] == [None, "A"]
    assert state["last_seen_mention_id"] == "99"
    assert state["mention_backlog"]["next_token"] == "A"
    assert state["mention_backlog"]["seen_tokens"] == ["A"]
    assert state["mention_pagination"] == {
        "base_since_id": "99",
        "next_token": "A",
    }
    assert set(state["mention_pending_candidates"]) == {
        row["id"] for row in returned
    }

    restarted = bot.load_state()
    assert [row["id"] for row in bot.get_mentions(restarted)] == ["103", "105"]
    assert len(requests) == 2
    bot.record_terminal_reply_evaluation(
        restarted,
        target_id="105",
        lane="mention",
        reason="confirmed_no_reply",
    )
    retire_all_pending(restarted)
    bot.save_state(restarted, durable=True)

    assert bot.get_mentions(restarted) == []
    assert len(requests) == 2
    assert restarted["last_seen_mention_id"] == "99"
    assert restarted["mention_backlog"] == {}
    assert restarted["mention_pagination"] == {}
    assert restarted["mention_pending_candidates"] == {}
    assert restarted["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }
    reset_state = bot.load_state()
    assert reset_state["mention_backlog"] == {}
    assert reset_state["mention_pagination"] == {}
    assert reset_state["mention_pending_candidates"] == {}
    assert reset_state["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }

    pages[None] = ([mention(105, 205), mention(103, 203)], None)
    restarted_again = bot.load_state()
    assert [row["id"] for row in bot.get_mentions(restarted_again)] == ["103"]
    assert len(requests) == 3
    assert requests[-1]["since_id"] == "99"
    assert bot.terminal_reply_evaluation(restarted_again, "105") is not None
    assert restarted_again["mention_backlog_reset_guard"] == {}


def test_invalid_continuation_discards_saved_page_without_advancing_watermark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    calls: list[str | None] = []

    def invalid(_method: str, _path: str, *, params: dict) -> dict:
        token = params.get("pagination_token")
        calls.append(token)
        if token is None:
            return {
                "data": [mention(105, 205)],
                "meta": {"next_token": "expired"},
            }
        raise bot.ApiError(
            'X API error 400: {"errors":[{"message":"Invalid pagination_token"}]}',
            service="x",
            status_code=400,
        )

    monkeypatch.setattr(bot, "x_request", invalid)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "MENTIONS_MAX_PAGES_PER_CHECK", 2)
    monkeypatch.setattr(bot, "now_epoch", lambda: 2_000_000_000)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    with pytest.raises(bot.ApiError, match="Invalid pagination_token"):
        bot.get_mentions(state)
    assert calls == [None, "expired"]
    assert state["last_seen_mention_id"] == "99"
    assert state["mention_backlog"] == {}
    assert state["mention_pagination"] == {}
    assert state["mention_pending_candidates"] == {}
    assert state["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }

    bot.apply_confirmed_reply_receipt(
        state,
        {
            "target_id": "105",
            "reply_post_id": "999",
            "author_id": "205",
            "reply_epoch": 2_000_000_000,
            "candidate_source": "mention",
            "conversation_id": "105",
            "reply_text": "A confirmed reply.",
        },
    )
    assert state["last_seen_mention_id"] == "99"
    assert state["mention_pending_candidates"] == {}
    assert state["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }

    head_requests: list[dict] = []

    def refetch(_method: str, _path: str, *, params: dict) -> dict:
        head_requests.append(dict(params))
        return {
            "data": [mention(tweet_id, 200 + tweet_id) for tweet_id in range(100, 106)],
            "meta": {},
        }

    monkeypatch.setattr(bot, "x_request", refetch)
    assert [row["id"] for row in bot.get_mentions(state)] == [
        "100",
        "101",
        "102",
        "103",
        "104",
    ]
    assert head_requests[0]["since_id"] == "99"
    assert "pagination_token" not in head_requests[0]
    assert state["last_seen_mention_id"] == "105"
    assert state["mention_backlog_reset_guard"] == {}


def test_restored_cursor_after_reset_cannot_clear_guard_before_head_refetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = install_mention_pages(
        monkeypatch,
        {
            "A": ([mention(103, 203)], None),
            None: (
                [mention(tweet_id, 200 + tweet_id) for tweet_id in range(100, 106)],
                None,
            ),
        },
    )
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["mention_backlog_reset_guard"] = {
        "base_since_id": "99",
        "head_traversal_started": False,
    }

    bot.apply_confirmed_reply_receipt(
        state,
        {
            "target_id": "105",
            "reply_post_id": "999",
            "author_id": "205",
            "reply_epoch": 2_000_000_000,
            "candidate_source": "mention",
            "conversation_id": "105",
            "reply_text": "A confirmed reply.",
            "mention_pagination": {
                "base_since_id": "99",
                "next_token": "A",
            },
        },
    )

    assert [row["id"] for row in bot.get_mentions(state)] == ["103"]
    assert state["last_seen_mention_id"] == "99"
    assert state["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }
    retire_all_pending(state)

    assert [row["id"] for row in bot.get_mentions(state)] == [
        "100",
        "101",
        "102",
        "103",
        "104",
    ]
    assert [request.get("pagination_token") for request in requests] == ["A", None]
    assert state["last_seen_mention_id"] == "105"
    assert state["mention_backlog_reset_guard"] == {}


def test_legacy_orphaned_pending_candidate_is_guarded_before_receipt_reconciliation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_path)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    bot.atomic_write_json(
        state_path,
        {
            "last_seen_mention_id": "99",
            "mention_backlog": {},
            "mention_pagination": {},
            "mention_pending_candidates": {
                "98": mention(98, 198),
                "105": mention(105, 205),
            },
        },
    )
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **values: events.append((name, values)),
    )

    state = bot.load_state()

    assert set(state["mention_pending_candidates"]) == {"98"}
    assert state["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }
    assert (
        "mention_backlog_reset",
        {
            "reason": "orphaned_pending_candidates",
            "since_id": "99",
            "discarded_candidates": 1,
        },
    ) in events

    bot.apply_confirmed_reply_receipt(
        state,
        {
            "target_id": "105",
            "reply_post_id": "999",
            "author_id": "205",
            "reply_epoch": 2_000_000_000,
            "candidate_source": "mention",
            "conversation_id": "105",
            "reply_text": "A confirmed reply.",
        },
    )
    assert state["last_seen_mention_id"] == "99"
    assert set(state["mention_pending_candidates"]) == {"98"}

    bot.save_state(state, durable=True)
    state = bot.load_state()
    requests = install_mention_pages(
        monkeypatch,
        {
            None: (
                [mention(tweet_id, 200 + tweet_id) for tweet_id in range(100, 106)],
                None,
            ),
        },
    )
    assert [row["id"] for row in bot.get_mentions(state)] == ["98"]
    assert requests == []
    retire_all_pending(state)

    assert [row["id"] for row in bot.get_mentions(state)] == [
        "100",
        "101",
        "102",
        "103",
        "104",
    ]
    assert requests[0]["since_id"] == "99"
    assert state["last_seen_mention_id"] == "105"
    assert state["mention_backlog_reset_guard"] == {}


def test_continuation_token_limit_is_shared_by_loader_and_writer(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limit = bot.MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT
    seen_tokens = [f"token-{index}" for index in range(limit)]
    backlog = {
        "since_id": "99",
        "next_token": "overflow-token",
        "highest_mention_id": "105",
        "pages_completed": limit,
        "started_epoch": 1_999_999_000,
        "seen_tokens": seen_tokens,
        "announced": True,
    }
    path = tmp_path / "bot_state.json"

    assert bot.normalise_mention_backlog(backlog, path=path) == backlog

    monkeypatch.setattr(bot, "STATE_FILE", path)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "MAX_MENTIONS_PER_CHECK", 5)
    monkeypatch.setattr(bot, "MENTIONS_MAX_PAGES_PER_CHECK", 1)
    monkeypatch.setattr(bot, "now_epoch", lambda: 2_000_000_000)
    events: list[tuple[str, dict]] = []
    requests: list[dict] = []

    def request(_method: str, _path: str, *, params: dict) -> dict:
        requests.append(dict(params))
        return {"data": [mention(103, 203)], "meta": {}}

    monkeypatch.setattr(bot, "x_request", request)
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **values: events.append((name, values)),
    )
    overflow_state = bot.default_state()
    overflow_state["last_seen_mention_id"] = "99"
    overflow_state["mention_backlog"] = {
        **backlog,
        "seen_tokens": [*seen_tokens, "one-too-many"],
    }
    overflow_state["mention_pagination"] = {
        "base_since_id": "99",
        "next_token": "overflow-token",
    }
    overflow_state["mention_pending_candidates"] = {
        "102": mention(102, 202),
    }
    bot.save_state(overflow_state, durable=True)

    recovered_overflow = bot.load_state()
    assert recovered_overflow["last_seen_mention_id"] == "99"
    assert recovered_overflow["mention_backlog"] == {}
    assert recovered_overflow["mention_pagination"] == {}
    assert recovered_overflow["mention_pending_candidates"] == {}
    assert recovered_overflow["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }
    assert sum(
        name == "mention_backlog_reset"
        and values.get("reason") == "continuation_token_limit"
        for name, values in events
    ) == 1
    malformed_overflow = {
        **backlog,
        "seen_tokens": [*seen_tokens, "one-too-many"],
        "announced": "yes",
    }
    assert bot.normalise_mention_backlog(
        malformed_overflow,
        path=path,
        reset_token_overflow=True,
    ) is None

    events.clear()
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["mention_backlog"] = copy.deepcopy(backlog)
    state["mention_pagination"] = {
        "base_since_id": "99",
        "next_token": "overflow-token",
    }
    bot.save_state(state, durable=True)

    assert bot.get_mentions(state) == []
    assert state["last_seen_mention_id"] == "99"
    assert state["mention_backlog"] == {}
    assert state["mention_pagination"] == {}
    assert state["mention_pending_candidates"] == {}
    assert state["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }
    assert any(
        name == "mention_backlog_reset"
        and values.get("reason") == "continuation_token_limit"
        for name, values in events
    )

    restarted = bot.load_state()
    assert [row["id"] for row in bot.get_mentions(restarted)] == ["103"]
    assert requests[-1].get("since_id") == "99"
    assert "pagination_token" not in requests[-1]
    assert len(requests) == 2
    assert restarted["mention_backlog_reset_guard"] == {}


def test_failed_backlog_reset_leaves_only_consistent_state_generations(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_save_state = bot.save_state
    state_path = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_path)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "MAX_MENTIONS_PER_CHECK", 5)
    monkeypatch.setattr(bot, "MENTIONS_MAX_PAGES_PER_CHECK", 2)
    monkeypatch.setattr(bot, "now_epoch", lambda: 2_000_000_000)
    requests: list[str | None] = []

    def request(_method: str, _path: str, *, params: dict) -> dict:
        token = params.get("pagination_token")
        requests.append(token)
        if token is None:
            return {
                "data": [mention(105, 205)],
                "meta": {"next_token": "A"},
            }
        raise bot.ApiError(
            'X API error 400: {"errors":[{"message":"Invalid pagination_token"}]}',
            service="x",
            status_code=400,
        )

    def fail_reset_save(current: dict, *, durable: bool = False) -> None:
        if (
            current.get("mention_backlog") == {}
            and current.get("mention_pagination") == {}
            and current.get("mention_pending_candidates") == {}
        ):
            raise OSError("injected mention backlog reset save failure")
        original_save_state(current, durable=durable)

    monkeypatch.setattr(bot, "x_request", request)
    monkeypatch.setattr(bot, "save_state", fail_reset_save)
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"

    with pytest.raises(OSError, match="injected mention backlog reset save failure"):
        bot.get_mentions(state)

    assert requests == [None, "A"]
    assert state["last_seen_mention_id"] == "99"
    assert state["mention_backlog"] == {}
    assert state["mention_pagination"] == {}
    assert state["mention_pending_candidates"] == {}
    assert state["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }

    durable_before_failed_reset = bot.load_state()
    assert durable_before_failed_reset["last_seen_mention_id"] == "99"
    assert durable_before_failed_reset["mention_backlog"]["next_token"] == "A"
    assert durable_before_failed_reset["mention_pagination"] == {
        "base_since_id": "99",
        "next_token": "A",
    }
    assert set(durable_before_failed_reset["mention_pending_candidates"]) == {
        "105"
    }


def test_digest_reports_backlog_quarantines_and_skipped_pipeline_evaluations() -> None:
    base = datetime(2026, 8, 16, 12, 0, 0)

    def record(offset: int, event: dict) -> digest.Record:
        return digest.Record(
            ts=base + timedelta(seconds=offset),
            level="INFO",
            src="log_event",
            line=1,
            msg="EVENT " + digest.json.dumps(event, separators=(",", ":")),
            path="fixture.log",
            ordinal=offset + 1,
        )

    records = [
        record(0, {"event": "mention_backlog_started", "pages_completed": 3}),
        record(1, {"event": "mention_backlog_progress", "pages_completed": 4}),
        record(2, {"event": "mention_backlog_completed", "pages_completed": 5}),
        record(3, {"event": "mention_backlog_reset", "reason": "invalid_continuation_token"}),
        record(4, {"event": "author_evaluation_quarantine_started", "author_id": "200"}),
        record(
            5,
            {
                "event": "author_evaluation_quarantine_skip",
                "author_id": "200",
                "target_id": "100",
                "pipeline_evaluations_skipped": 1,
                "provider_calls_avoided": 4,
            },
        ),
        record(
            6,
            {
                "event": "author_evaluation_quarantine_skip",
                "author_id": "200",
                "target_id": "101",
                "pipeline_evaluations_skipped": 0,
            },
        ),
        record(
            7,
            {
                "event": "author_evaluation_quarantine_skip",
                "author_id": "200",
                "target_id": "102",
                "pipeline_evaluations_skipped": 0,
            },
        ),
    ]
    report = digest.analyse(records)
    report["latest_state"] = digest.summarize_latest_state(
        {
            "last_seen_mention_id": "99",
            "mention_backlog": {
                "started_epoch": int(datetime.now().timestamp()) - 60,
                "pages_completed": 4,
                "highest_mention_id": "105",
                "next_token": "secret-token",
            },
            "mention_pending_candidates": {"101": mention(101, 200)},
            "author_evaluation_quarantines": {
                "200": {
                    "quarantine_until_epoch": int(datetime.now().timestamp()) + 600
                }
            },
        },
        base,
    )
    rendered = digest.render_markdown(report)

    observed = report["mention_backlog_and_quarantine"]
    assert observed["pipeline_evaluations_skipped"] == 1
    assert "provider_calls_avoided" not in observed
    assert "provider_calls_avoided" not in observed["events"][-1]
    assert observed["event_counts"]["mention_backlog_reset"] == 1
    assert observed["event_counts"]["author_evaluation_quarantine_skip"] == 3
    assert report["latest_state"]["mention_backlog_active"] is True
    assert report["latest_state"]["active_author_evaluation_quarantine_author_ids"] == ["200"]
    assert (
        "Pipeline evaluations skipped by active author quarantine "
        "(explicit event counts only): 1"
    ) in rendered
    assert "Active quarantined author IDs: 200" in rendered
    assert "secret-token" not in rendered
