from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta

import pytest

import mrsMThatcher2 as bot
import mrs_log_digest as digest
import tested_reply_pipeline as pipeline
from reply_strategy import AIReply


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


def majority_no_reply_pipeline_result(outcome: str) -> pipeline.PipelineResult:
    """Return one resolved reviewer result for the real production wrapper."""

    assert outcome in {
        "confirm_no_reply",
        "confirm_no_reply_spam_or_abuse",
    }
    return pipeline.PipelineResult(
        None,
        "no_reply",
        "reply_necessity_review",
        4,
        0,
        (
            {
                "stage": "candidate_backed_engagement",
                "provider": "xAI",
                "schema_valid": True,
            },
            *(
                {
                    "stage": f"reply_necessity_{index}",
                    "provider": "OpenAI",
                    "schema_valid": True,
                }
                for index in range(1, 4)
            ),
            {
                "stage": "reply_necessity_resolution",
                "majority_outcome": outcome,
                "majority_resolvable": True,
                "invalid_or_refused_calls": 0,
            },
        ),
    )


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
        reason="reply_necessity_review",
        telemetry={**base, "reply_necessity_outcome": "confirm_no_reply"},
        allow_corroborating_no_reply=True,
    ) is True
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


def test_three_ordinary_no_reply_majorities_do_not_seed_clean_author() -> None:
    state = bot.default_state()
    start = 2_000_000_000
    ordinary = {
        "reply_necessity_outcome": "confirm_no_reply",
        "reply_necessity_majority_resolvable": True,
    }

    assert bot.tested_pipeline_no_reply_qualifies_for_author_quarantine(
        status="no_reply",
        reason="reply_necessity_review",
        telemetry=ordinary,
        allow_corroborating_no_reply=True,
    ) is True
    for offset in range(3):
        assert bot.record_qualifying_author_no_reply(
            state,
            "200",
            current_epoch=start + offset,
            explicit_spam_or_abuse=False,
        ) is False

    assert state["author_evaluation_quarantines"] == {}


def test_explicit_spam_plus_two_ordinary_no_replies_starts_quarantine_and_skips_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = 2_000_000_000
    candidates = [mention(tweet_id, 200) for tweet_id in range(100, 104)]
    state = bot.default_state()
    configure_provider_free_mention_check(
        monkeypatch,
        candidates,
        current_epoch=current,
    )
    monkeypatch.setattr(bot, "clarification_reply_context", lambda *_args, **_kwargs: None)
    context_targets: list[str] = []

    def build_context(candidate: dict, _state: dict) -> tuple[dict, bool]:
        target_id = str(candidate["id"])
        context_targets.append(target_id)
        return (
            {
                "target_id": target_id,
                "thread_id": str(candidate["conversation_id"]),
                "lane": "mention",
                "incoming_contribution": str(candidate["text"]),
                "parent_thread": [],
            },
            True,
        )

    monkeypatch.setattr(bot, "build_context_for_reply_ai", build_context)
    pipeline_targets: list[str] = []
    pipeline_results = iter(
        [
            majority_no_reply_pipeline_result(
                "confirm_no_reply_spam_or_abuse"
            ),
            majority_no_reply_pipeline_result("confirm_no_reply"),
            majority_no_reply_pipeline_result("confirm_no_reply"),
        ]
    )

    def run_pipeline(**kwargs: object) -> pipeline.PipelineResult:
        context = kwargs["context"]
        assert isinstance(context, dict)
        pipeline_targets.append(str(context["target_id"]))
        return next(pipeline_results)

    monkeypatch.setattr(pipeline, "run_reply_pipeline", run_pipeline)
    monkeypatch.setattr(
        bot,
        "tested_pipeline_structured_call",
        lambda **_kwargs: pytest.fail("the fixture must make no provider call"),
    )
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **values: events.append((name, values)),
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert pipeline_targets == ["100", "101", "102"]
    assert context_targets == pipeline_targets
    record = state["author_evaluation_quarantines"]["200"]
    assert record["recent_no_reply_epochs"] == [current, current, current]
    assert record["latest_explicit_spam_or_abuse_epoch"] == current
    assert record["quarantine_until_epoch"] == current + 43_200
    quarantine_events = [
        values
        for name, values in events
        if name == "author_evaluation_quarantine_skip"
    ]
    assert len(quarantine_events) == 1
    assert quarantine_events[0]["target_id"] == "103"
    assert quarantine_events[0]["pipeline_evaluations_skipped"] == 1


def test_strikes_outside_window_do_not_start_quarantine() -> None:
    state = bot.default_state()
    start = 2_000_000_000
    bot.record_qualifying_author_no_reply(state, "200", current_epoch=start)
    bot.record_qualifying_author_no_reply(
        state,
        "200",
        current_epoch=start + bot.AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS,
        explicit_spam_or_abuse=False,
    )
    started = bot.record_qualifying_author_no_reply(
        state,
        "200",
        current_epoch=start + bot.AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS + 1,
        explicit_spam_or_abuse=False,
    )

    assert started is False
    assert state["author_evaluation_quarantines"] == {}


def test_approved_reply_production_branch_clears_author_strikes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    start = 2_000_000_000
    bot.record_qualifying_author_no_reply(state, "200", current_epoch=start)
    bot.record_qualifying_author_no_reply(
        state,
        "200",
        current_epoch=start + 1,
        explicit_spam_or_abuse=False,
    )
    candidate = mention(100, 200)
    configure_provider_free_mention_check(
        monkeypatch,
        [candidate],
        current_epoch=start + 2,
    )
    monkeypatch.setattr(
        bot,
        "clarification_reply_context",
        lambda *_args, **_kwargs: None,
    )
    generation_targets: list[str] = []
    approved_reply = AIReply(
        "Thank you for the contribution.",
        {"mode": "opinion_or_principle"},
        {},
    )

    def generate_approved(
        context: dict,
        *_args: object,
        **_kwargs: object,
    ) -> AIReply:
        generation_targets.append(str(context["target_id"]))
        return approved_reply

    monkeypatch.setattr(bot, "generate_ai_first_reply", generate_approved)
    monkeypatch.setattr(
        bot,
        "tested_pipeline_structured_call",
        lambda **_kwargs: pytest.fail("the fixture must make no provider call"),
    )

    def stop_after_approved_branch(
        current_state: dict,
        *_args: object,
        **_kwargs: object,
    ) -> bool:
        assert current_state["author_evaluation_quarantines"] == {}
        return False

    monkeypatch.setattr(bot, "store_pending_ai_reply", stop_after_approved_branch)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert generation_targets == ["100"]
    assert state["author_evaluation_quarantines"] == {}


def test_operational_failure_does_not_add_strike(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    bot.record_qualifying_author_no_reply(
        state,
        "200",
        current_epoch=1_999_999_999,
    )
    candidate = mention(100, 200)
    configure_provider_free_mention_check(
        monkeypatch, [candidate], current_epoch=2_000_000_000
    )

    def fail(*_args: object, **_kwargs: object) -> None:
        raise bot.ApiError("provider unavailable", service="xai")

    monkeypatch.setattr(bot, "generate_ai_first_reply", fail)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_API_ERROR
    assert state["author_evaluation_quarantines"]["200"][
        "recent_no_reply_epochs"
    ] == [1_999_999_999]


@pytest.mark.parametrize(
    ("reason", "telemetry"),
    [
        (
            "reply_necessity_review",
            {
                "reply_necessity_outcome": "confirm_no_reply",
                "reply_necessity_majority_resolvable": False,
            },
        ),
        (
            "allegation_review_suppression",
            {
                "allegation_conspiracy_outcome": "confirm_no_reply",
                "allegation_conspiracy_majority_resolvable": True,
            },
        ),
        (
            "unsupported_authentication_suppression",
            {"authentication_outcome": "suppress_unsupported_authentication"},
        ),
        (
            "group_hostility_suppression",
            {"group_hostility_outcome": "suppress_group_hostility"},
        ),
        (
            "unquoted abusive epithet is grammatically directed at a permitted personal target",
            {
                "deterministic_suppressed": True,
                "deterministic_reason": "unquoted abusive epithet is grammatically directed at a permitted personal target",
            },
        ),
    ],
    ids=[
        "unresolved-review",
        "allegation-safety-suppression",
        "evidence-authentication-suppression",
        "group-hostility-suppression",
        "deterministic-local-suppression",
    ],
)
def test_non_qualifying_failures_and_suppressions_do_not_corroborate(
    reason: str,
    telemetry: dict,
) -> None:
    assert bot.tested_pipeline_no_reply_qualifies_for_author_quarantine(
        status="no_reply",
        reason=reason,
        telemetry=telemetry,
        allow_corroborating_no_reply=True,
    ) is False


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
    queue_active_mention(state, candidate, base_since_id="99")
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


def test_active_quarantine_permits_valid_clarification_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = 2_000_000_000
    state = bot.default_state()
    for offset in range(3):
        bot.record_qualifying_author_no_reply(
            state, "200", current_epoch=current - 3 + offset
        )
    candidate = mention(100, 200, "@MrsMThatcher That did not answer my question.")
    queue_active_mention(state, candidate, base_since_id="99")
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

    def run_pipeline(**kwargs: object) -> pipeline.PipelineResult:
        context = kwargs["context"]
        assert isinstance(context, dict)
        assert context["clarification_request"] == {
            "original_question": "What policy follows from that?",
            "correction": "@MrsMThatcher That did not answer my question.",
        }
        evaluated.append(str(context["target_id"]))
        return majority_no_reply_pipeline_result("confirm_no_reply")

    monkeypatch.setattr(pipeline, "run_reply_pipeline", run_pipeline)
    monkeypatch.setattr(
        bot,
        "tested_pipeline_structured_call",
        lambda **_kwargs: pytest.fail("the fixture must make no provider call"),
    )
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **values: events.append((name, values)),
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert evaluated == ["100"]
    terminal = bot.terminal_reply_evaluation(state, "100")
    assert terminal is not None
    assert terminal["reason"] == "reply_necessity_review"
    record = state["author_evaluation_quarantines"]["200"]
    assert record["recent_no_reply_epochs"][-1] == current
    assert all(
        name != "author_evaluation_quarantine_skip"
        for name, _values in events
    )


def test_active_quarantine_clarification_still_obeys_author_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = 2_000_000_000
    state = bot.default_state()
    for offset in range(3):
        bot.record_qualifying_author_no_reply(
            state, "200", current_epoch=current - 3 + offset
        )
    candidate = mention(100, 200, "@MrsMThatcher That did not answer my question.")
    configure_provider_free_mention_check(monkeypatch, [candidate], current_epoch=current)
    state["daily_reply_date"] = bot.reply_cap_date_str(current)
    state["daily_replied_author_ids"] = ["200"]
    state["daily_replied_author_counts"] = {
        "200": bot.MAX_REPLIES_PER_AUTHOR_PER_DAY
    }
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
            "the author cap must precede clarification context work"
        ),
    )
    monkeypatch.setattr(
        bot,
        "generate_ai_first_reply",
        lambda *_args, **_kwargs: pytest.fail(
            "the author cap must block clarification provider work"
        ),
    )
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **values: events.append((name, values)),
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert bot.terminal_reply_evaluation(state, "100") is None
    assert any(
        name == "candidate_skipped" and values.get("reason") == "author_daily_cap"
        for name, values in events
    )
    assert all(name != "author_evaluation_quarantine_skip" for name, _ in events)


def test_configured_quarantine_threshold_101_is_reachable(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = 2_000_000_000
    config_path = tmp_path / "mrsMThatcher.local.json"
    bot.atomic_write_json(
        config_path,
        {"AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD": 101},
    )
    monkeypatch.setattr(bot, "LOCAL_CONFIG_FILE", config_path)
    monkeypatch.setattr(
        bot,
        "AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD",
        bot.AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD,
    )
    bot.apply_local_config()
    assert bot.AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD == 101
    assert bot.author_no_reply_epoch_limit() == 404
    state = bot.default_state()

    for offset in range(100):
        assert bot.record_qualifying_author_no_reply(
            state,
            "200",
            current_epoch=start + offset,
        ) is False

    assert bot.record_qualifying_author_no_reply(
        state,
        "200",
        current_epoch=start + 100,
    ) is True
    record = state["author_evaluation_quarantines"]["200"]
    assert len(record["recent_no_reply_epochs"]) == 101
    assert bot.normalise_author_evaluation_quarantines(
        state["author_evaluation_quarantines"],
        path=tmp_path / "configured-state.json",
    ) == state["author_evaluation_quarantines"]


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
        "latest_explicit_spam_or_abuse_epoch": start,
        "evidence_policy": bot.AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY,
    }
    assert bot.prune_author_evaluation_quarantines(
        loaded,
        current_epoch=start + bot.AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS,
    ) is True
    assert loaded["author_evaluation_quarantines"] == {}


def test_seeded_corroboration_history_survives_restart_and_reaches_threshold(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = 2_000_000_000
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "now_epoch", lambda: start + 2)
    state = bot.default_state()
    assert bot.record_qualifying_author_no_reply(
        state,
        "200",
        current_epoch=start,
    ) is False
    assert bot.record_qualifying_author_no_reply(
        state,
        "200",
        current_epoch=start + 1,
        explicit_spam_or_abuse=False,
    ) is False
    bot.save_state(state, durable=True)

    loaded = bot.load_state()
    record = loaded["author_evaluation_quarantines"]["200"]
    assert record["recent_no_reply_epochs"] == [start, start + 1]
    assert record["latest_explicit_spam_or_abuse_epoch"] == start
    assert record["evidence_policy"] == (
        bot.AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY
    )
    assert bot.record_qualifying_author_no_reply(
        loaded,
        "200",
        current_epoch=start + 2,
        explicit_spam_or_abuse=False,
    ) is True


def test_previous_explicit_only_policy_history_migrates_to_live_seed(tmp_path) -> None:
    start = 2_000_000_000
    previous = {
        "200": {
            "recent_no_reply_epochs": [start, start + 1],
            "quarantine_until_epoch": 0,
            "last_updated_epoch": start + 1,
            "evidence_policy": (
                bot.AUTHOR_EVALUATION_QUARANTINE_LEGACY_EVIDENCE_POLICY
            ),
        }
    }

    normalised = bot.normalise_author_evaluation_quarantines(
        previous,
        path=tmp_path / "bot_state.json",
    )

    assert normalised == {
        "200": {
            "recent_no_reply_epochs": [start, start + 1],
            "quarantine_until_epoch": 0,
            "last_updated_epoch": start + 1,
            "latest_explicit_spam_or_abuse_epoch": start + 1,
            "evidence_policy": (
                bot.AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY
            ),
        }
    }


def test_previous_policy_active_quarantine_without_live_strikes_survives_restart(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = 2_000_000_000
    current = start + bot.AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS + 1
    quarantine_until = start + bot.AUTHOR_NO_REPLY_QUARANTINE_SECONDS
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "now_epoch", lambda: current)
    state = bot.default_state()
    state["author_evaluation_quarantines"] = {
        "200": {
            "recent_no_reply_epochs": [],
            "quarantine_until_epoch": quarantine_until,
            "last_updated_epoch": start,
            "evidence_policy": (
                bot.AUTHOR_EVALUATION_QUARANTINE_LEGACY_EVIDENCE_POLICY
            ),
        },
        "201": {
            "recent_no_reply_epochs": [],
            "quarantine_until_epoch": 0,
            "last_updated_epoch": start,
            "evidence_policy": (
                bot.AUTHOR_EVALUATION_QUARANTINE_LEGACY_EVIDENCE_POLICY
            ),
        },
    }
    bot.save_state(state, durable=True)

    loaded = bot.load_state()

    assert set(loaded["author_evaluation_quarantines"]) == {"200"}
    record = loaded["author_evaluation_quarantines"]["200"]
    assert record == {
        "recent_no_reply_epochs": [],
        "quarantine_until_epoch": quarantine_until,
        "last_updated_epoch": start,
        "latest_explicit_spam_or_abuse_epoch": 0,
        "evidence_policy": bot.AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY,
    }
    assert bot.active_author_evaluation_quarantine(
        loaded,
        "200",
        current_epoch=current,
    ) is not None
    assert bot.record_qualifying_author_no_reply(
        loaded,
        "200",
        current_epoch=current,
        explicit_spam_or_abuse=False,
    ) is False
    assert loaded["author_evaluation_quarantines"]["200"] == record
    assert bot.active_author_evaluation_quarantine(
        loaded,
        "200",
        current_epoch=quarantine_until,
    ) is None
    assert loaded["author_evaluation_quarantines"] == {}


def test_qualifying_no_reply_epochs_stay_bounded_through_five_backup_generations(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = 2_000_000_000
    assert bot.AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD == 3
    assert bot.author_no_reply_epoch_limit() == 100
    insertion_count = bot.author_no_reply_epoch_limit() + 5
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
    assert len(record["recent_no_reply_epochs"]) == bot.author_no_reply_epoch_limit()
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


def test_legacy_quarantine_terminal_does_not_discard_completed_page_candidate(
    tmp_path,
) -> None:
    state = bot.default_state()
    state["last_seen_mention_id"] = "105"
    state["mention_pending_candidates"] = {
        "105": mention(105, 205),
    }
    state["reply_evaluation_records"] = {
        "105": {
            "target_id": "105",
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
    assert normalised["reply_evaluation_records"] == {}
    assert normalised["mention_pending_candidates"] == {
        "105": mention(105, 205),
    }


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
    queue_active_mention(state, candidate, base_since_id="99")
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


def mention_backlog(
    *,
    since_id: str,
    next_token: str = "A",
    highest_mention_id: str = "105",
) -> dict:
    return {
        "since_id": since_id,
        "next_token": next_token,
        "highest_mention_id": highest_mention_id,
        "pages_completed": 1,
        "started_epoch": 1_999_999_000,
        "seen_tokens": [],
        "announced": True,
    }


CORRUPT_PENDING_IDENTITIES = (
    pytest.param(
        {"105": mention(106, 205)},
        id="map-key-does-not-match-embedded-id",
    ),
    pytest.param(
        {
            "105": mention(105, 205),
            "000105": {
                **mention(105, 206),
                "id": "000105",
            },
        },
        id="duplicate-parsed-id",
    ),
    pytest.param(
        {
            "9" * 5_000: {
                **mention(105, 205),
                "id": "9" * 5_000,
            },
        },
        id="oversized-numeric-id",
    ),
)


def retire_all_pending(state: dict) -> None:
    for candidate in bot.pending_mention_candidates(state):
        bot.mark_mention_seen_if_applicable(state, candidate)


def test_stale_pending_traversal_is_reset_before_queue_or_provider_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["mention_backlog"] = mention_backlog(since_id="98")
    state["mention_pagination"] = {
        "base_since_id": "98",
        "next_token": "A",
    }
    state["mention_pending_candidates"] = {
        "98": mention(98, 198),
        "105": mention(105, 205),
    }
    saves: list[tuple[bool, dict]] = []
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda current, *, durable=False: saves.append(
            (bool(durable), copy.deepcopy(current))
        ),
    )
    monkeypatch.setattr(
        bot,
        "x_request",
        lambda *_args, **_kwargs: pytest.fail(
            "queue authority validation must not call X"
        ),
    )
    monkeypatch.setattr(
        bot,
        "generate_ai_first_reply",
        lambda *_args, **_kwargs: pytest.fail(
            "an untrusted pending candidate must not reach the provider"
        ),
    )

    assert bot.pending_mention_candidates(state) == []
    assert state["last_seen_mention_id"] == "99"
    assert state["mention_backlog"] == {}
    assert state["mention_pagination"] == {}
    assert state["mention_pending_candidates"] == {}
    assert state["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }
    assert saves and saves[-1][0] is True
    assert saves[-1][1]["last_seen_mention_id"] == "99"

    head_requests: list[dict] = []

    def refetch_head(_method: str, _path: str, *, params: dict) -> dict:
        head_requests.append(dict(params))
        assert state["last_seen_mention_id"] == "99"
        return {
            "data": [
                mention(tweet_id, 200 + tweet_id)
                for tweet_id in range(100, 106)
            ],
            "meta": {},
        }

    monkeypatch.setattr(bot, "x_request", refetch_head)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "MENTIONS_MAX_PAGES_PER_CHECK", 1)
    monkeypatch.setattr(bot, "now_epoch", lambda: 2_000_000_000)

    recovered = bot.get_mentions(state)

    assert [row["id"] for row in recovered] == [
        "100",
        "101",
        "102",
        "103",
        "104",
        "105",
    ]
    assert head_requests[0]["since_id"] == "99"
    assert "pagination_token" not in head_requests[0]
    assert state["last_seen_mention_id"] == "105"
    assert state["mention_backlog_reset_guard"] == {}


def test_stale_traversal_disposes_covered_and_deduplicated_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["mention_backlog"] = mention_backlog(since_id="98")
    state["mention_pagination"] = {
        "base_since_id": "98",
        "next_token": "A",
    }
    state["mention_pending_candidates"] = {
        "98": mention(98, 198),
        "100": mention(100, 200),
        "105": mention(105, 205),
    }
    state["replied_to_ids"] = ["100"]
    bot.record_terminal_reply_evaluation(
        state,
        target_id="98",
        lane="mention",
        reason="already_handled",
    )
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        bot,
        "x_request",
        lambda *_args, **_kwargs: pytest.fail(
            "covered and deduplicated candidates require no provider work"
        ),
    )

    assert bot.pending_mention_candidates(state) == []
    assert state["mention_pending_candidates"] == {}
    assert state["mention_backlog"] == {}
    assert state["mention_pagination"] == {}
    assert state["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }


def test_stale_pending_traversal_is_recovered_during_state_load(
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
            "mention_backlog": mention_backlog(since_id="98"),
            "mention_pagination": {
                "base_since_id": "98",
                "next_token": "A",
            },
            "mention_pending_candidates": {
                "105": mention(105, 205),
            },
        },
    )

    state = bot.load_state()

    assert state["last_seen_mention_id"] == "99"
    assert state["mention_backlog"] == {}
    assert state["mention_pagination"] == {}
    assert state["mention_pending_candidates"] == {}
    assert state["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["mention_backlog"] == {}
    assert persisted["mention_pagination"] == {}
    assert persisted["mention_pending_candidates"] == {}
    assert persisted["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }


@pytest.mark.parametrize("pending", CORRUPT_PENDING_IDENTITIES)
def test_corrupt_pending_identity_prefers_usable_backup(
    pending: dict,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_path)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    bot.atomic_write_json(
        state_path,
        {
            "last_seen_mention_id": "99",
            "mention_backlog": mention_backlog(since_id="99"),
            "mention_pagination": {
                "base_since_id": "99",
                "next_token": "A",
            },
            "mention_pending_candidates": pending,
            "replied_to_ids": ["from-primary"],
        },
    )
    bot.atomic_write_json(
        tmp_path / "bot_state.json.bak1",
        {
            "last_seen_mention_id": "77",
            "replied_to_ids": ["from-backup"],
        },
    )

    state = bot.load_state()

    assert state["last_seen_mention_id"] == "77"
    assert state["replied_to_ids"] == ["from-backup"]
    assert state["mention_pending_candidates"] == {}


@pytest.mark.parametrize("pending", CORRUPT_PENDING_IDENTITIES)
def test_corrupt_pending_identity_without_backup_uses_guarded_head_recovery(
    pending: dict,
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
            "mention_backlog": mention_backlog(since_id="99"),
            "mention_pagination": {
                "base_since_id": "99",
                "next_token": "A",
            },
            "mention_pending_candidates": pending,
        },
    )

    state = bot.load_state()

    assert state["last_seen_mention_id"] == "99"
    assert state["mention_backlog"] == {}
    assert state["mention_pagination"] == {}
    assert state["mention_pending_candidates"] == {}
    assert state["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["mention_backlog"] == {}
    assert persisted["mention_pagination"] == {}
    assert persisted["mention_pending_candidates"] == {}
    assert persisted["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }

    requests = install_mention_pages(
        monkeypatch,
        {None: ([mention(100, 200)], None)},
    )
    assert [candidate["id"] for candidate in bot.get_mentions(state)] == ["100"]
    assert requests[0]["since_id"] == "99"
    assert "pagination_token" not in requests[0]


@pytest.mark.parametrize("pending", CORRUPT_PENDING_IDENTITIES)
def test_queue_retrieval_discards_corrupt_pending_identity_without_provider_work(
    pending: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["mention_backlog"] = mention_backlog(since_id="99")
    state["mention_pagination"] = {
        "base_since_id": "99",
        "next_token": "A",
    }
    state["mention_pending_candidates"] = copy.deepcopy(pending)
    saves: list[dict] = []
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda current, *, durable=False: saves.append(
            {"durable": durable, "state": copy.deepcopy(current)}
        ),
    )
    monkeypatch.setattr(
        bot,
        "generate_ai_first_reply",
        lambda *_args, **_kwargs: pytest.fail(
            "corrupt queue identity must not reach provider work"
        ),
    )

    assert bot.pending_mention_candidates(state) == []
    assert state["mention_backlog"] == {}
    assert state["mention_pagination"] == {}
    assert state["mention_pending_candidates"] == {}
    assert state["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }
    assert saves[-1]["durable"] is True


@pytest.mark.parametrize(
    "pagination",
    [
        pytest.param(
            {"base_since_id": "99", "next_token": "B"},
            id="different-continuation-token",
        ),
        pytest.param({}, id="missing-canonical-pagination"),
    ],
)
def test_backlog_and_pagination_must_be_internally_coherent(
    pagination: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["mention_backlog"] = mention_backlog(since_id="99")
    state["mention_pagination"] = pagination
    state["mention_pending_candidates"] = {"105": mention(105, 205)}
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    assert bot.pending_mention_candidates(state) == []
    assert state["mention_backlog"] == {}
    assert state["mention_pagination"] == {}
    assert state["mention_pending_candidates"] == {}
    assert state["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }


def test_active_backlog_highest_identity_must_cover_its_watermark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["mention_backlog"] = mention_backlog(
        since_id="99",
        highest_mention_id="",
    )
    state["mention_pagination"] = {
        "base_since_id": "99",
        "next_token": "A",
    }
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    assert bot.pending_mention_candidates(state) == []
    assert state["mention_backlog"] == {}
    assert state["mention_pagination"] == {}
    assert state["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }


def test_unfetched_head_backlog_cannot_seed_watermark_from_unowned_highest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["mention_backlog"] = mention_backlog(
        since_id="99",
        next_token="",
        highest_mention_id="105",
    )
    state["mention_backlog"]["pages_completed"] = 0
    requests = install_mention_pages(monkeypatch, {None: ([], None)})

    assert bot.get_mentions(state) == []
    assert requests[0]["since_id"] == "99"
    assert "pagination_token" not in requests[0]
    assert state["last_seen_mention_id"] == "99"


@pytest.mark.parametrize(
    "initial_guard",
    [
        pytest.param({}, id="missing-guard"),
        pytest.param(
            {"base_since_id": "99", "head_traversal_started": True},
            id="interrupted-head-guard",
        ),
    ],
)
def test_pagination_alone_requires_head_refetch_before_watermark_advancement(
    initial_guard: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["mention_pagination"] = {
        "base_since_id": "99",
        "next_token": "A",
    }
    state["mention_backlog_reset_guard"] = initial_guard
    state["mention_pending_candidates"] = {"105": mention(105, 205)}
    saved: list[dict] = []
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda current, *, durable=False: saved.append(
            {"durable": durable, "state": copy.deepcopy(current)}
        ),
    )

    assert bot.pending_mention_candidates(state) == []
    assert state["mention_pagination"] == {
        "base_since_id": "99",
        "next_token": "A",
    }
    assert state["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }
    assert saved[-1]["durable"] is True

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
    assert [candidate["id"] for candidate in bot.get_mentions(state)] == ["103"]
    assert state["last_seen_mention_id"] == "99"
    assert state["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }
    retire_all_pending(state)

    assert [candidate["id"] for candidate in bot.get_mentions(state)] == [
        "100",
        "101",
        "102",
        "103",
        "104",
        "105",
    ]
    assert [request.get("pagination_token") for request in requests] == ["A", None]
    assert requests[-1]["since_id"] == "99"
    assert state["last_seen_mention_id"] == "105"
    assert state["mention_backlog_reset_guard"] == {}


def test_interrupted_head_guard_without_backlog_discards_pending_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["mention_backlog_reset_guard"] = {
        "base_since_id": "99",
        "head_traversal_started": True,
    }
    state["mention_pending_candidates"] = {"105": mention(105, 205)}
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    assert bot.pending_mention_candidates(state) == []
    assert state["mention_pending_candidates"] == {}
    assert state["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }


def test_full_mention_loop_resets_stale_queue_before_provider_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_get_mentions = bot.get_mentions
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["mention_backlog"] = mention_backlog(since_id="98")
    state["mention_pagination"] = {
        "base_since_id": "98",
        "next_token": "A",
    }
    state["mention_pending_candidates"] = {"105": mention(105, 205)}
    configure_provider_free_mention_check(
        monkeypatch,
        [],
        current_epoch=2_000_000_000,
    )
    monkeypatch.setattr(bot, "get_mentions", real_get_mentions)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "MENTIONS_MAX_PAGES_PER_CHECK", 1)
    requests: list[dict] = []

    def empty_head(_method: str, _path: str, *, params: dict) -> dict:
        requests.append(dict(params))
        return {"data": [], "meta": {}}

    snapshots: list[dict] = []
    monkeypatch.setattr(bot, "x_request", empty_head)
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda current, *, durable=False: snapshots.append(
            {"durable": durable, "state": copy.deepcopy(current)}
        ),
    )
    monkeypatch.setattr(
        bot,
        "generate_ai_first_reply",
        lambda *_args, **_kwargs: pytest.fail(
            "stale candidates must not reach the reply provider"
        ),
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert requests[0]["since_id"] == "99"
    assert "pagination_token" not in requests[0]
    assert snapshots[0]["durable"] is True
    assert snapshots[0]["state"]["mention_pending_candidates"] == {}
    assert snapshots[0]["state"]["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }
    assert state["last_seen_mention_id"] == "99"


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


def test_confirmed_receipt_cannot_authorise_stale_pending_after_restart(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_path)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["mention_backlog"] = mention_backlog(since_id="98")
    state["mention_pagination"] = {
        "base_since_id": "98",
        "next_token": "A",
    }
    state["mention_pending_candidates"] = {
        "105": mention(105, 205),
    }
    receipt = {
        "target_id": "105",
        "reply_post_id": "999",
        "author_id": "205",
        "reply_epoch": 2_000_000_000,
        "candidate_source": "mention",
        "conversation_id": "105",
        "reply_text": "A confirmed reply.",
    }
    bot.apply_confirmed_reply_receipt(state, receipt)

    assert state["last_seen_mention_id"] == "99"
    assert state["mention_backlog"] == {}
    assert state["mention_pagination"] == {}
    assert state["mention_pending_candidates"] == {}
    assert state["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }
    assert state["replied_to_ids"] == ["105"]

    bot.save_state(state, durable=True)
    restarted = bot.load_state()
    requests = install_mention_pages(
        monkeypatch,
        {
            None: (
                [
                    mention(tweet_id, 200 + tweet_id)
                    for tweet_id in range(100, 106)
                ],
                None,
            ),
        },
    )

    assert [row["id"] for row in bot.get_mentions(restarted)] == [
        "100",
        "101",
        "102",
        "103",
        "104",
    ]
    assert requests[0]["since_id"] == "99"
    assert "pagination_token" not in requests[0]
    assert restarted["last_seen_mention_id"] == "105"


def test_receipt_pagination_without_page_ownership_installs_reset_guard(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_path)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    older_state = bot.default_state()
    older_state["last_seen_mention_id"] = "99"
    bot.save_state(older_state, durable=True)
    state = bot.load_state()

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

    assert state["last_seen_mention_id"] == "99"
    assert state["mention_pagination"] == {
        "base_since_id": "99",
        "next_token": "A",
    }
    assert state["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }

    requests = install_mention_pages(
        monkeypatch,
        {
            "A": ([mention(103, 203)], None),
            None: (
                [
                    mention(tweet_id, 200 + tweet_id)
                    for tweet_id in range(100, 106)
                ],
                None,
            ),
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
    assert requests[-1]["since_id"] == "99"
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
