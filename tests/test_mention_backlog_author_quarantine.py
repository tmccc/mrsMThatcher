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


def test_only_complete_policy_silence_outcomes_qualify() -> None:
    base = {
        "schema_invalid_stages": [],
        "deterministic_suppressed": False,
        "reply_necessity_outcome": "confirm_no_reply",
        "reply_necessity_invalid_calls": 1,
    }
    assert bot.tested_pipeline_no_reply_qualifies_for_author_quarantine(
        status="no_reply",
        reason="reply_necessity_review",
        telemetry=base,
    ) is True
    assert bot.tested_pipeline_no_reply_qualifies_for_author_quarantine(
        status="no_reply",
        reason="reply_necessity_review",
        telemetry={**base, "reply_necessity_invalid_calls": 2},
    ) is False
    assert bot.tested_pipeline_no_reply_qualifies_for_author_quarantine(
        status="no_reply",
        reason="incoming contribution has no lexical content beyond handle(s) and/or URL(s)",
        telemetry={
            "deterministic_suppressed": True,
            "deterministic_reason": "incoming contribution has no lexical content beyond handle(s) and/or URL(s)",
        },
    ) is False
    assert bot.tested_pipeline_no_reply_qualifies_for_author_quarantine(
        status="no_reply",
        reason="unquoted abusive epithet is grammatically directed at a permitted personal target",
        telemetry={
            "deterministic_suppressed": True,
            "deterministic_reason": "unquoted abusive epithet is grammatically directed at a permitted personal target",
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


def test_active_quarantine_uses_zero_provider_calls_and_records_terminally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = 2_000_000_000
    state = bot.default_state()
    for offset in range(3):
        bot.record_qualifying_author_no_reply(
            state, "200", current_epoch=current - 3 + offset
        )
    candidate = mention(100, 200)
    configure_provider_free_mention_check(monkeypatch, [candidate], current_epoch=current)
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
    }
    assert bot.prune_author_evaluation_quarantines(
        loaded,
        current_epoch=start + bot.AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS,
    ) is True
    assert loaded["author_evaluation_quarantines"] == {}


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
    assert [
        bot.terminal_reply_evaluation(state, str(tweet_id))["reason"]
        for tweet_id in range(4, 7)
    ] == ["author_evaluation_quarantine"] * 3
    assert bot.terminal_reply_evaluation(state, "7") is not None
    assert bot.terminal_reply_evaluation(state, "8") is not None

    print(
        "DEMO quarantine: spam_mentions=6 full_pipeline_spam=3 "
        "quarantine_skips=3 later_contributors_reached=2 "
        "modeled_provider_calls_before=32 modeled_provider_calls_after=20"
    )


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


def test_invalid_and_repeated_continuations_reset_without_advancing_watermark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["mention_backlog"] = {
        "since_id": "99",
        "next_token": "expired",
        "highest_mention_id": "105",
        "pages_completed": 1,
        "started_epoch": 1_999_999_000,
        "seen_tokens": ["A"],
        "announced": True,
    }
    state["mention_pagination"] = {
        "base_since_id": "99",
        "next_token": "expired",
    }

    def invalid(_method: str, _path: str, *, params: dict) -> dict:
        raise bot.ApiError(
            'X API error 400: {"errors":[{"message":"Invalid pagination_token"}]}',
            service="x",
            status_code=400,
        )

    monkeypatch.setattr(bot, "x_request", invalid)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "MENTIONS_MAX_PAGES_PER_CHECK", 1)
    monkeypatch.setattr(bot, "now_epoch", lambda: 2_000_000_000)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    assert bot.get_mentions(state) == []
    assert state["last_seen_mention_id"] == "99"
    assert state["mention_backlog"] == {}

    state["mention_backlog"] = {
        "since_id": "99",
        "next_token": "B",
        "highest_mention_id": "105",
        "pages_completed": 1,
        "started_epoch": 1_999_999_000,
        "seen_tokens": ["A"],
        "announced": True,
    }
    state["mention_pagination"] = {"base_since_id": "99", "next_token": "B"}
    calls: list[str] = []

    def repeated(_method: str, _path: str, *, params: dict) -> dict:
        calls.append(str(params["pagination_token"]))
        return {"data": [mention(103, 203)], "meta": {"next_token": "A"}}

    monkeypatch.setattr(bot, "x_request", repeated)
    assert bot.get_mentions(state) == []
    assert calls == ["B"]
    assert state["last_seen_mention_id"] == "99"
    assert state["mention_backlog"] == {}


def test_digest_reports_backlog_quarantines_and_actual_avoided_calls() -> None:
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
                "provider_calls_avoided": 4,
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
    assert observed["provider_calls_avoided"] == 4
    assert observed["event_counts"]["mention_backlog_reset"] == 1
    assert report["latest_state"]["mention_backlog_active"] is True
    assert report["latest_state"]["active_author_evaluation_quarantine_author_ids"] == ["200"]
    assert "Provider calls avoided by quarantine (explicit event counts only): 4" in rendered
    assert "Active quarantined author IDs: 200" in rendered
    assert "secret-token" not in rendered
