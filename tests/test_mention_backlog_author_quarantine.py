from __future__ import annotations

from mrs_bot_reply_cycle_interfaces import PreparedReplyContext
from tests.helpers.reply_evaluation import legacy_reply_evaluator
from tests.helpers.reply_fixtures import patch_reply_draft_method, patch_reply_owner_method
from mrs_bot_reply_clarifications import ClarificationReplies

import copy
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import mrsMThatcher2 as bot
import mrs_bot_reply_lane_policy as reply_lane_policy
import mrs_log_digest as digest
from single_call_reply import ValidatedReply
from tests.helpers.mention_fixtures import (
    DIGEST_AUTHOR_NO_REPLY_EVIDENCE_POLICY,
    DIGEST_AUTHOR_NO_REPLY_CONFIG,
    mention,
    queue_active_mention,
    configure_provider_free_mention_check,
    editorial_no_reply,
    install_mention_pages,
    mention_backlog,
    digest_author_no_reply_record,
)


LONDON = ZoneInfo("Europe/London")
F909_SINGLE_SOL_STATE_FIXTURE = (
    Path(__file__).parent / "fixtures" / "f9099605_single_sol_state.json"
)

DIGEST_AUTHOR_NO_REPLY_SEEDED_EVIDENCE_POLICY = (
    "majority_spam_or_abuse_seeded_corroboration_v2"
)
DIGEST_AUTHOR_NO_REPLY_LEGACY_EVIDENCE_POLICY = "majority_spam_or_abuse_v1"
DIGEST_AUTHOR_NO_REPLY_AUTHORITY_SCOPE = (
    "authoritative current state at JSON generation time, independent of "
    "selected log window"
)


def london_epoch(day: int, hour: int, minute: int, second: int) -> int:
    return int(
        datetime(
            2026,
            8,
            day,
            hour,
            minute,
            second,
            tzinfo=LONDON,
        ).timestamp()
    )


@pytest.mark.parametrize("body", [
    b"", b"{}", b'{"errors":[{"detail":"Temporarily unavailable"}]}',
])
def test_incomplete_http_continuation_preserves_durable_unread_mentions(monkeypatch, tmp_path, body):
    """An invalid 2xx continuation must remain retryable across state reload."""
    from unittest.mock import Mock

    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 2)
    monkeypatch.setattr(bot, "MENTIONS_MAX_PAGES_PER_CHECK", 1)
    first = bot.requests.Response()
    first.status_code = 200
    first._content = json.dumps({
        "data": [mention(300, 400)], "meta": {"next_token": "older-page"},
    }).encode()
    incomplete = bot.requests.Response()
    incomplete.status_code = 200
    incomplete._content = body
    empty = bot.requests.Response()
    empty.status_code = 200
    empty._content = b'{"meta":{"result_count":0}}'
    transport = Mock(side_effect=[first, incomplete, empty])
    monkeypatch.setattr(bot.requests, "request", transport)
    state = bot.default_state()
    state["last_seen_mention_id"] = "100"
    assert [row["id"] for row in bot._reply_assembly()._mention_discovery_callback()(state)] == ["300"]
    bot._reply_assembly()._mention_queue_owner().mark_seen(state, {"id": "300"})
    bot.save_state(state, durable=True)
    before = json.loads(bot.STATE_FILE.read_text())
    with pytest.raises(bot.ApiError, match="incomplete paginated response"):
        bot._reply_assembly()._mention_discovery_callback()(state)
    persisted = json.loads(bot.STATE_FILE.read_text())
    for key in ("last_seen_mention_id", "mention_backlog", "mention_pagination"):
        assert state[key] == persisted[key] == before[key]
    assert persisted["last_seen_mention_id"] == "100"
    assert persisted["mention_backlog"]["next_token"] == "older-page"

    bot._reply_assembly()._mention_discovery_callback()(persisted)
    completed = json.loads(bot.STATE_FILE.read_text())
    assert completed["last_seen_mention_id"] == "300"
    assert completed["mention_backlog"] == completed["mention_pagination"] == {}
    assert [call.kwargs["params"].get("pagination_token") for call in transport.call_args_list] == [
        None, "older-page", "older-page",
    ]


def test_three_qualifying_no_replies_start_author_quarantine() -> None:
    state = bot.default_state()
    start = 2_000_000_000

    assert bot.record_qualifying_author_no_reply(state, "200", current_epoch=start) is False
    assert bot.record_qualifying_author_no_reply(state, "200", current_epoch=start + 1) is False
    assert bot.record_qualifying_author_no_reply(state, "200", current_epoch=start + 2) is True

    record = state["author_evaluation_quarantines"]["200"]
    assert record["recent_no_reply_epochs"] == [start, start + 1, start + 2]
    assert record["quarantine_until_epoch"] == start + 2 + 43_200


def test_three_valid_editorial_no_replies_seed_clean_author() -> None:
    state = bot.default_state()
    start = 2_000_000_000
    for offset in range(3):
        assert bot.record_qualifying_author_no_reply(
            state,
            "200",
            current_epoch=start + offset,
            explicit_spam_or_abuse=False,
        ) is (offset == 2)

    assert state["author_evaluation_quarantines"]["200"][
        "recent_no_reply_epochs"
    ] == [start, start + 1, start + 2]


def test_digest_author_no_reply_chronology_survives_restarts_and_skips_quarantine(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    author_176 = "1762401049766436864"
    author_476 = "476806362"
    qualifying_targets = [
        "2092924492615987668",
        "2092954605537604084",
        "2092955150528794820",
        "2092969945369850169",
        "2092970858088153133",
        "2092973785041228042",
    ]
    skipped_176_targets = [
        "2092955663647338737",
        "2092958691884519759",
        "2093075215056073018",
        "2093084438821310514",
        "2093087593885782348",
        "2093088479995433111",
    ]
    synthetic_476_target = "2093100000000000000"
    clock = {"epoch": london_epoch(27, 11, 52, 47)}
    candidate_buffer: list[dict] = []
    real_save_state = bot.save_state
    configure_provider_free_mention_check(
        monkeypatch,
        candidate_buffer,
        current_epoch=clock["epoch"],
    )
    monkeypatch.setattr(bot, "save_state", real_save_state)
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "now_epoch", lambda: clock["epoch"])
    monkeypatch.setattr(
        bot,
        "current_datetime",
        lambda: datetime.fromtimestamp(clock["epoch"], tz=LONDON),
    )
    patch_reply_owner_method(
        monkeypatch, ClarificationReplies, "context",
        lambda *_args, **_kwargs: None,
    )

    context_targets: list[str] = []

    def build_context(candidate: dict, _state: dict) -> PreparedReplyContext | None:
        target_id = str(candidate["id"])
        context_targets.append(target_id)
        return PreparedReplyContext(
            {
                "target_id": target_id,
                "thread_id": str(candidate["conversation_id"]),
                "lane": "mention",
                "incoming_contribution": str(candidate["text"]),
                "parent_thread": [],
            },
            {},
        )

    patch_reply_owner_method(monkeypatch, bot._reply_context.ReplyContext, "build", build_context)
    pipeline_targets: list[str] = []
    trace: list[tuple[str, str]] = []

    def run_pipeline(
        context: dict,
        *_args: object,
        evaluation_outcome: dict | None = None,
        **_kwargs: object,
    ) -> None:
        target_id = str(context["target_id"])
        assert target_id in qualifying_targets
        pipeline_targets.append(target_id)
        trace.append(("pipeline", target_id))
        return editorial_no_reply(
            context,
            evaluation_outcome=evaluation_outcome,
            reason_code="spam_or_abuse",
        )

    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(run_pipeline),
    )
    events: list[tuple[str, dict]] = []

    def capture_event(name: str, **values: object) -> None:
        events.append((name, dict(values)))
        trace.append(
            (
                name,
                str(values.get("target_id") or values.get("author_id") or ""),
            )
        )

    monkeypatch.setattr(bot, "log_event", capture_event)
    state = bot.default_state()

    def process_candidate(
        *,
        day: int,
        hour: int,
        minute: int,
        second: int,
        target_id: str,
        author_id: str,
    ) -> None:
        clock["epoch"] = london_epoch(day, hour, minute, second)
        candidate_buffer[:] = [mention(int(target_id), int(author_id))]
        assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
        candidate_buffer.clear()

    def restart(
        *,
        stop: tuple[int, int, int, int],
        start: tuple[int, int, int, int],
    ) -> None:
        nonlocal state
        clock["epoch"] = london_epoch(*stop)
        bot.save_state(state, durable=True)
        clock["epoch"] = london_epoch(*start)
        state = bot.load_state()

    process_candidate(
        day=27,
        hour=11,
        minute=52,
        second=47,
        target_id=qualifying_targets[0],
        author_id=author_176,
    )
    restart(stop=(27, 12, 29, 30), start=(27, 12, 31, 54))
    assert state["author_evaluation_quarantines"][author_176][
        "recent_no_reply_epochs"
    ] == [london_epoch(27, 11, 52, 47)]

    process_candidate(
        day=27,
        hour=13,
        minute=49,
        second=33,
        target_id=qualifying_targets[1],
        author_id=author_176,
    )
    process_candidate(
        day=27,
        hour=13,
        minute=49,
        second=43,
        target_id=qualifying_targets[2],
        author_id=author_176,
    )
    expected_176_until = london_epoch(28, 1, 49, 43)
    expected_176_strikes = [
        london_epoch(27, 11, 52, 47),
        london_epoch(27, 13, 49, 33),
        london_epoch(27, 13, 49, 43),
    ]
    assert state["author_evaluation_quarantines"][author_176][
        "quarantine_until_epoch"
    ] == expected_176_until
    assert trace.index(("pipeline", qualifying_targets[2])) < next(
        index
        for index, item in enumerate(trace)
        if item == ("author_evaluation_quarantine_started", author_176)
    )

    for target_id, timestamp in zip(
        skipped_176_targets[:2],
        [(27, 13, 49, 54), (27, 14, 5, 16)],
        strict=True,
    ):
        process_candidate(
            day=timestamp[0],
            hour=timestamp[1],
            minute=timestamp[2],
            second=timestamp[3],
            target_id=target_id,
            author_id=author_176,
        )
        assert state["author_evaluation_quarantines"][author_176][
            "quarantine_until_epoch"
        ] == expected_176_until
        assert state["author_evaluation_quarantines"][author_176][
            "recent_no_reply_epochs"
        ] == expected_176_strikes

    process_candidate(
        day=27,
        hour=14,
        minute=51,
        second=37,
        target_id=qualifying_targets[3],
        author_id=author_476,
    )
    restart(stop=(27, 15, 3, 12), start=(27, 15, 5, 26))
    assert state["author_evaluation_quarantines"][author_476][
        "recent_no_reply_epochs"
    ] == [london_epoch(27, 14, 51, 37)]
    assert bot.active_author_evaluation_quarantine(
        state,
        author_176,
        current_epoch=clock["epoch"],
    ) is not None

    process_candidate(
        day=27,
        hour=15,
        minute=7,
        second=40,
        target_id=qualifying_targets[4],
        author_id=author_476,
    )
    process_candidate(
        day=27,
        hour=15,
        minute=22,
        second=54,
        target_id=qualifying_targets[5],
        author_id=author_476,
    )
    expected_476_until = london_epoch(28, 3, 22, 54)
    assert state["author_evaluation_quarantines"][author_476][
        "quarantine_until_epoch"
    ] == expected_476_until

    restart(stop=(27, 16, 47, 3), start=(27, 16, 47, 42))
    assert bot.active_author_evaluation_quarantine(
        state,
        author_176,
        current_epoch=clock["epoch"],
    ) is not None
    assert bot.active_author_evaluation_quarantine(
        state,
        author_476,
        current_epoch=clock["epoch"],
    ) is not None

    # Exercise another real round trip after both six-hour strike windows have
    # elapsed: active quarantine expiry remains independently authoritative.
    clock["epoch"] = london_epoch(27, 21, 44, 41)
    bot.save_state(state, durable=True)
    state = bot.load_state()
    assert state["author_evaluation_quarantines"][author_176][
        "recent_no_reply_epochs"
    ] == []
    assert state["author_evaluation_quarantines"][author_476][
        "recent_no_reply_epochs"
    ] == []

    for target_id, timestamp in zip(
        skipped_176_targets[2:],
        [
            (27, 21, 44, 42),
            (27, 22, 15, 5),
            (27, 22, 30, 21),
            (27, 22, 30, 30),
        ],
        strict=True,
    ):
        process_candidate(
            day=timestamp[0],
            hour=timestamp[1],
            minute=timestamp[2],
            second=timestamp[3],
            target_id=target_id,
            author_id=author_176,
        )
        assert state["author_evaluation_quarantines"][author_176][
            "quarantine_until_epoch"
        ] == expected_176_until
        assert state["author_evaluation_quarantines"][author_176][
            "recent_no_reply_epochs"
        ] == []

    process_candidate(
        day=28,
        hour=2,
        minute=31,
        second=51,
        target_id=synthetic_476_target,
        author_id=author_476,
    )
    digest_end = london_epoch(28, 2, 31, 51)
    assert bot.active_author_evaluation_quarantine(
        state,
        author_176,
        current_epoch=digest_end,
    ) is None
    assert bot.active_author_evaluation_quarantine(
        state,
        author_476,
        current_epoch=digest_end,
    ) is not None
    assert state["author_evaluation_quarantines"][author_476][
        "recent_no_reply_epochs"
    ] == []
    assert state["author_evaluation_quarantines"][author_476][
        "quarantine_until_epoch"
    ] == expected_476_until
    active_authors = {
        author_id
        for author_id, record in state["author_evaluation_quarantines"].items()
        if int(record["quarantine_until_epoch"]) > digest_end
    }
    assert active_authors == {author_476}
    assert datetime.fromtimestamp(expected_176_until, tz=LONDON) == datetime(
        2026, 8, 28, 1, 49, 43, tzinfo=LONDON
    )
    assert datetime.fromtimestamp(expected_476_until, tz=LONDON) == datetime(
        2026, 8, 28, 3, 22, 54, tzinfo=LONDON
    )

    assert pipeline_targets == qualifying_targets
    assert context_targets == qualifying_targets
    starts = [
        values
        for name, values in events
        if name == "author_evaluation_quarantine_started"
    ]
    assert [values["author_id"] for values in starts] == [author_176, author_476]
    skips_176 = [
        values["target_id"]
        for name, values in events
        if name == "author_evaluation_quarantine_skip"
        and values["author_id"] == author_176
    ]
    assert skips_176 == skipped_176_targets
    skips_476 = [
        values["target_id"]
        for name, values in events
        if name == "author_evaluation_quarantine_skip"
        and values["author_id"] == author_476
    ]
    assert skips_476 == [synthetic_476_target]


def test_three_explicit_spam_no_replies_start_quarantine_and_skip_next(
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
    patch_reply_owner_method(
        monkeypatch, ClarificationReplies, "context",
        lambda *_args, **_kwargs: None,
    )
    context_targets: list[str] = []

    def build_context(candidate: dict, _state: dict) -> PreparedReplyContext | None:
        target_id = str(candidate["id"])
        context_targets.append(target_id)
        return PreparedReplyContext(
            {
                "target_id": target_id,
                "thread_id": str(candidate["conversation_id"]),
                "lane": "mention",
                "incoming_contribution": str(candidate["text"]),
                "parent_thread": [],
            },
            {},
        )

    patch_reply_owner_method(monkeypatch, bot._reply_context.ReplyContext, "build", build_context)
    pipeline_targets: list[str] = []
    reason_codes = iter(["spam_or_abuse"] * 3)

    def run_pipeline(
        context: dict,
        *_args: object,
        evaluation_outcome: dict | None = None,
        **_kwargs: object,
    ) -> None:
        pipeline_targets.append(str(context["target_id"]))
        return editorial_no_reply(
            context,
            evaluation_outcome=evaluation_outcome,
            reason_code=next(reason_codes),
        )

    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(run_pipeline),
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


def test_non_spam_editorial_no_replies_do_not_create_quarantine_strikes(
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
    patch_reply_owner_method(
        monkeypatch, ClarificationReplies, "context",
        lambda *_args, **_kwargs: None,
    )
    reasons = iter(
        ["completed_exchange", "irrelevant", "insufficient_context", "already_answered"]
    )

    def run_pipeline(
        context: dict,
        *_args: object,
        evaluation_outcome: dict | None = None,
        **_kwargs: object,
    ) -> None:
        return editorial_no_reply(
            context,
            evaluation_outcome=evaluation_outcome,
            reason_code=next(reasons),
        )

    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(run_pipeline),
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert state["author_evaluation_quarantines"] == {}


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
    assert state["author_evaluation_quarantines"]["200"] == {
        "recent_no_reply_epochs": [
            start + bot.AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS,
            start + bot.AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS + 1,
        ],
        "quarantine_until_epoch": 0,
        "last_updated_epoch": (
            start + bot.AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS + 1
        ),
        "latest_explicit_spam_or_abuse_epoch": 0,
        "evidence_policy": bot.AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY,
    }


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
    patch_reply_owner_method(
        monkeypatch, ClarificationReplies, "context",
        lambda *_args, **_kwargs: None,
    )
    generation_targets: list[str] = []
    approved_reply = ValidatedReply(
        "Thank you for the contribution.",
        {},
        {},
    )

    def generate_approved(
        context: dict,
        *_args: object,
        **_kwargs: object,
    ) -> ValidatedReply:
        generation_targets.append(str(context["target_id"]))
        return approved_reply

    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(generate_approved),
    )

    class ApprovedBranchReached(Exception):
        pass

    def stop_after_approved_branch(
        current_state: dict,
        *_args: object,
        **_kwargs: object,
    ) -> bool:
        assert current_state["author_evaluation_quarantines"] == {}
        raise ApprovedBranchReached

    patch_reply_draft_method(monkeypatch, "store", stop_after_approved_branch)

    with pytest.raises(ApprovedBranchReached):
        bot.maybe_reply_to_mentions(state)
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
        raise bot.ApiError("provider unavailable", service="openai")

    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(fail),
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_API_ERROR
    assert state["author_evaluation_quarantines"]["200"][
        "recent_no_reply_epochs"
    ] == [1_999_999_999]


@pytest.mark.parametrize(
    "error_category",
    [
        "context_validation",
        "draft_validation",
        "image_input",
        "local_validation",
    ],
)
def test_candidate_local_operational_failure_retires_without_strike_or_quota(
    monkeypatch: pytest.MonkeyPatch,
    error_category: str,
) -> None:
    state = bot.default_state()
    state["daily_reply_count"] = 2
    state["daily_quote_reply_count"] = 1
    bot.record_qualifying_author_no_reply(
        state,
        "200",
        current_epoch=1_999_999_999,
    )
    candidate = mention(100, 200)
    configure_provider_free_mention_check(
        monkeypatch,
        [candidate],
        current_epoch=2_000_000_000,
    )
    state["daily_reply_date"] = bot.reply_cap_date_str(2_000_000_000)
    state["daily_quote_reply_date"] = bot.reply_cap_date_str(2_000_000_000)

    def operational_failure(
        _context: dict,
        *_args: object,
        evaluation_outcome: dict | None = None,
        **_kwargs: object,
    ) -> None:
        assert evaluation_outcome is not None
        evaluation_outcome.update(
            {
                "status": "operational_failure",
                "reason": "invalid_attempt",
                "error_category": error_category,
                "model_call_count": int(error_category != "image_input"),
            }
        )
        return None

    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(operational_failure),
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert state["author_evaluation_quarantines"]["200"][
        "recent_no_reply_epochs"
    ] == [1_999_999_999]
    assert bot.terminal_reply_evaluation(state, "100")["outcome"] == (
        "operational_failure"
    )
    assert state["daily_reply_count"] == 2
    assert state["daily_quote_reply_count"] == 1
    assert state["openai_error_epochs"] == []
    assert state["openai_api_cooldown_until_epoch"] == 0


def test_hot_post_local_validation_failure_is_terminal_and_not_provider_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    candidate = mention(100, 200)
    candidate.update(
        {
            "_source": "hot_post_reply",
            "_hot_original_post_id": "90",
        }
    )
    configure_provider_free_mention_check(
        monkeypatch,
        [],
        current_epoch=2_000_000_000,
    )
    monkeypatch.setattr(
        bot._hot_post_discovery, "get_hot_post_reply_candidates",
        lambda _state, **_kwargs: [candidate],
    )

    def operational_failure(
        _context: dict,
        *_args: object,
        evaluation_outcome: dict | None = None,
        **_kwargs: object,
    ) -> None:
        assert evaluation_outcome is not None
        evaluation_outcome.update(
            {
                "status": "operational_failure",
                "reason": "model_response_validation_failed",
                "error_category": "local_validation",
                "model_call_count": 1,
            }
        )
        return None

    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(operational_failure),
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert bot.terminal_reply_evaluation(state, "100")["outcome"] == (
        "operational_failure"
    )
    assert state["skipped_hot_reply_records"]["100"] == {
        "reason": "operational_local_validation",
        "retryable": False,
        "skipped_epoch": 2_000_000_000,
        "original_post_id": "90",
    }
    assert state["openai_error_epochs"] == []
    assert state["daily_reply_count"] == 0
    assert state["author_evaluation_quarantines"] == {}


def test_permanent_context_failure_retires_candidate_and_reaches_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = 2_000_000_000
    candidates = [mention(100, 200), mention(101, 201)]
    state = bot.default_state()
    configure_provider_free_mention_check(
        monkeypatch,
        candidates,
        current_epoch=current,
    )
    context_calls: list[str] = []

    def build_context(candidate: dict, _state: dict) -> PreparedReplyContext | None:
        target_id = str(candidate["id"])
        context_calls.append(target_id)
        if target_id == "100":
            return None
        return PreparedReplyContext(
            {
                "target_id": target_id,
                "thread_id": target_id,
                "lane": "mention",
                "incoming_contribution": str(candidate["text"]),
                "parent_thread": [],
            },
            {},
        )

    model_calls: list[str] = []

    def run_pipeline(
        context: dict,
        *_args: object,
        evaluation_outcome: dict | None = None,
        **_kwargs: object,
    ) -> None:
        model_calls.append(str(context["target_id"]))
        return editorial_no_reply(
            context,
            evaluation_outcome=evaluation_outcome,
        )

    patch_reply_owner_method(monkeypatch, bot._reply_context.ReplyContext, "build", build_context)
    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(run_pipeline),
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert context_calls == ["100", "101"]
    assert model_calls == ["101"]
    assert bot.terminal_reply_evaluation(state, "100") == {
        "target_id": "100",
        "lane": "mention",
        "outcome": "operational_failure",
        "reason": "canonical_context_unavailable",
        "evaluated_epoch": current,
    }
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
    queue_active_mention(state, candidate, base_since_id="99")
    configure_provider_free_mention_check(monkeypatch, [candidate], current_epoch=current)
    patch_reply_owner_method(
        monkeypatch, ClarificationReplies, "context",
        lambda *_args, **_kwargs: None,
    )
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **values: events.append((name, values)),
    )
    patch_reply_owner_method(
        monkeypatch, bot._reply_context.ReplyContext, "build",
        lambda *_args, **_kwargs: pytest.fail("quarantine must precede context work"),
    )
    monkeypatch.setattr(
        bot,
        "reply_evidence_repository",
        lambda: pytest.fail("quarantine must precede evidence preparation"),
    )
    patch_reply_owner_method(monkeypatch, bot._reply_native_media.ReplyMedia, "context", lambda *_args, **_kwargs: pytest.fail(
            "quarantine must precede media preparation"
        ))
    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(lambda *_args, **_kwargs: pytest.fail("quarantine must make zero provider calls")),
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
    patch_reply_owner_method(monkeypatch, ClarificationReplies, "context", lambda *_args, **_kwargs: {
            "thread_id": "100",
            "prior_bot_reply_id": "90",
            "original_question_id": "80",
            "question_text": "What policy follows from that?",
            "trigger": "explicit_correction",
        })
    evaluated: list[str] = []

    def run_pipeline(
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
        return editorial_no_reply(
            context,
            evaluation_outcome=evaluation_outcome,
        )

    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(run_pipeline),
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
    assert terminal["reason"] == "completed_exchange"
    record = state["author_evaluation_quarantines"]["200"]
    assert record["recent_no_reply_epochs"][-1] == current - 1
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
    patch_reply_owner_method(monkeypatch, ClarificationReplies, "context", lambda *_args, **_kwargs: {
            "thread_id": "100",
            "prior_bot_reply_id": "90",
            "original_question_id": "80",
            "question_text": "What policy follows from that?",
            "trigger": "explicit_correction",
        })
    patch_reply_owner_method(
        monkeypatch, bot._reply_context.ReplyContext, "build",
        lambda *_args, **_kwargs: pytest.fail(
            "the author cap must precede clarification context work"
        ),
    )
    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(lambda *_args, **_kwargs: pytest.fail(
            "the author cap must block clarification provider work"
        )),
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
    patch_reply_owner_method(monkeypatch, ClarificationReplies, "context", lambda *_args, **_kwargs: {
            "thread_id": "100",
            "prior_bot_reply_id": "90",
            "original_question_id": "80",
            "question_text": "What policy follows from that?",
            "trigger": "explicit_correction",
        })
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
                "reason": "completed_exchange",
                "reason_code": "completed_exchange",
            }
        )
        return None

    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(no_reply),
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert evaluated == ["100"]
    assert state["author_evaluation_quarantines"] == {}
    assert bot.terminal_reply_evaluation(state, "100")["reason"] == "completed_exchange"


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


def test_seeded_v2_history_migrates_without_losing_strikes_or_active_expiry(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = 2_000_000_000
    quarantine_until = start + bot.AUTHOR_NO_REPLY_QUARANTINE_SECONDS
    previous = {
        "200": {
            "recent_no_reply_epochs": [start, start + 1, start + 2],
            "quarantine_until_epoch": quarantine_until,
            "last_updated_epoch": start + 2,
            "latest_explicit_spam_or_abuse_epoch": start,
            "evidence_policy": (
                bot.AUTHOR_EVALUATION_QUARANTINE_SEEDED_EVIDENCE_POLICY
            ),
        }
    }

    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "now_epoch", lambda: start + 3)
    state = bot.default_state()
    state["author_evaluation_quarantines"] = previous
    bot.save_state(state, durable=True)

    assert bot.load_state()["author_evaluation_quarantines"] == {
        "200": {
            "recent_no_reply_epochs": [start, start + 1, start + 2],
            "quarantine_until_epoch": quarantine_until,
            "last_updated_epoch": start + 2,
            "latest_explicit_spam_or_abuse_epoch": start,
            "evidence_policy": bot.AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY,
        }
    }


def test_immediately_previous_v3_quarantine_migrates_without_rejecting_state(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = 2_000_000_000
    quarantine_until = start + bot.AUTHOR_NO_REPLY_QUARANTINE_SECONDS
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "now_epoch", lambda: start + 3)
    state = bot.default_state()
    state["author_evaluation_quarantines"] = {
        "200": {
            "recent_no_reply_epochs": [start, start + 1, start + 2],
            "quarantine_until_epoch": quarantine_until,
            "last_updated_epoch": start + 2,
            "latest_explicit_spam_or_abuse_epoch": start,
            "evidence_policy": (
                bot.AUTHOR_EVALUATION_QUARANTINE_PREVIOUS_EVIDENCE_POLICY
            ),
        }
    }
    bot.save_state(state, durable=True)

    loaded = bot.load_state()

    assert loaded["author_evaluation_quarantines"] == {
        "200": {
            "recent_no_reply_epochs": [start, start + 1, start + 2],
            "quarantine_until_epoch": quarantine_until,
            "last_updated_epoch": start + 2,
            "latest_explicit_spam_or_abuse_epoch": start,
            "evidence_policy": bot.AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY,
        }
    }


def test_real_f909_single_sol_state_migrates_without_backup_rollback(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Load state emitted by f9099605's real writer and retain its sentinels."""

    state_file = tmp_path / "bot_state.json"
    fixture = json.loads(F909_SINGLE_SOL_STATE_FIXTURE.read_text(encoding="utf-8"))
    state_file.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    state_file.with_name("bot_state.json.bak1").write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    monkeypatch.setattr(bot, "now_epoch", lambda: 2_000_000_003)

    loaded = bot.load_state()

    assert loaded["last_seen_mention_id"] == "900"
    assert loaded["replied_to_ids"] == ["501", "502"]
    assert loaded["own_auto_reply_ids"] == ["601"]
    assert loaded["daily_reply_count"] == 2
    assert loaded["daily_replied_author_counts"] == {"200": 1, "201": 1}
    assert loaded["hot_post_reply_since_ids"] == {"700": "800"}
    assert loaded["hot_post_reply_check_counts"] == {"700": 4}
    assert loaded["quote_lookup_pagination_tokens"] == {"600": "cursor-f909"}
    assert loaded["author_evaluation_quarantines"] == {
        "200": {
            "recent_no_reply_epochs": [2_000_000_000],
            "quarantine_until_epoch": 0,
            "last_updated_epoch": 2_000_000_002,
            "latest_explicit_spam_or_abuse_epoch": 2_000_000_000,
            "evidence_policy": bot.AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY,
        }
    }


def test_f909_aged_explicit_epoch_does_not_reject_current_state(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accept the exact aged-explicit shape produced by f909 pruning."""

    start = 2_000_000_000
    later_editorial_epoch = start + 5 * 60 * 60
    prune_epoch = start + bot.AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS + 1
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "now_epoch", lambda: prune_epoch)
    state = bot.default_state()
    state["last_seen_mention_id"] = "900"
    # f909 could write this after an explicit spam decision at ``start``, a
    # non-spam editorial decline five hours later, and a normal prune just
    # beyond the six-hour window.  Its pruner removed the first timestamp but
    # left latest_explicit_spam_or_abuse_epoch unchanged while the later broad
    # timestamp kept the author record alive.
    state["author_evaluation_quarantines"] = {
        "200": {
            "recent_no_reply_epochs": [later_editorial_epoch],
            "quarantine_until_epoch": 0,
            "last_updated_epoch": later_editorial_epoch,
            "latest_explicit_spam_or_abuse_epoch": start,
            "evidence_policy": (
                bot.AUTHOR_EVALUATION_QUARANTINE_SINGLE_SOL_V1_EVIDENCE_POLICY
            ),
        }
    }
    bot.save_state(state, durable=True)

    loaded = bot.load_state()

    assert loaded["last_seen_mention_id"] == "900"
    assert loaded["author_evaluation_quarantines"] == {}


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
                "reason": "completed_exchange",
                "reason_code": "completed_exchange",
            }
        )
        return None

    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(no_reply),
    )

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
                "reason": "spam_or_abuse" if target_id in {"1", "2", "3"} else "completed_exchange",
                "reason_code": "spam_or_abuse" if target_id in {"1", "2", "3"} else "completed_exchange",
            }
        )
        return None

    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(policy_no_reply),
    )

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
    patch_reply_owner_method(
        monkeypatch, ClarificationReplies, "context",
        lambda *_args, **_kwargs: None,
    )
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
    patch_reply_owner_method(
        monkeypatch, ClarificationReplies, "context",
        lambda *_args, **_kwargs: None,
    )
    local_filter_calls: list[str] = []

    def local_filter(text: str) -> bool:
        local_filter_calls.append(text)
        return local_spam_rejection

    monkeypatch.setattr(
        reply_lane_policy, "is_probably_spam_or_not_worth_replying",
        lambda text, **_settings: local_filter(text),
    )
    patch_reply_owner_method(
        monkeypatch, bot._reply_context.ReplyContext, "build",
        lambda *_args, **_kwargs: pytest.fail(
            "quarantine and deterministic gates must precede context work"
        ),
    )
    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(lambda *_args, **_kwargs: pytest.fail(
            "quarantine must make zero provider calls"
        )),
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

    bot._reply_assembly().reply_evaluations().prune(state, current_epoch=current)

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

    bot._reply_assembly().reply_evaluations().prune(state, current_epoch=current)

    assert len(state["reply_evaluation_records"]) == bot.REPLY_EVALUATION_MAX_RECORDS
    assert "1" not in state["reply_evaluation_records"]
    assert str(count) in state["reply_evaluation_records"]


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
    for candidate in bot._reply_assembly()._mention_queue_owner().pending(state):
        bot._reply_assembly()._mention_queue_owner().mark_seen(state, candidate)


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
    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(lambda *_args, **_kwargs: pytest.fail(
            "an untrusted pending candidate must not reach the provider"
        )),
    )

    assert bot._reply_assembly()._mention_queue_owner().pending(state) == []
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

    recovered = bot._reply_assembly()._mention_discovery_callback()(state)

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
    bot._reply_assembly().reply_evaluations().record(
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

    assert bot._reply_assembly()._mention_queue_owner().pending(state) == []
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
@pytest.mark.parametrize("generation_ordered", [False, True], ids=["ambiguous-legacy", "sealed-newer-backup"])
def test_corrupt_pending_identity_requires_provable_backup_authority(
    pending: dict,
    generation_ordered: bool,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discardable pending corruption cannot erase durable reply-history ambiguity."""
    from mrs_bot_state_generation import encode_generation

    state_path = tmp_path / "bot_state.json"
    backup_path = tmp_path / "bot_state.json.bak1"
    monkeypatch.setattr(bot, "STATE_FILE", state_path)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    primary = {
        "last_seen_mention_id": "99",
        "mention_backlog": mention_backlog(since_id="99"),
        "mention_pagination": {"base_since_id": "99", "next_token": "A"},
        "mention_pending_candidates": pending,
        "replied_to_ids": ["from-primary"],
    }
    backup = {
        "last_seen_mention_id": "77",
        "replied_to_ids": ["from-backup"],
    }
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
    assert state["last_seen_mention_id"] == "77"
    assert state["replied_to_ids"] == ["from-backup"]
    assert state["mention_pending_candidates"] == {}
    assert state["_state_generation"]["sequence"] > 2
    assert state_path.read_bytes() == backup_path.read_bytes()
    assert bot.load_state()["replied_to_ids"] == ["from-backup"]


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
    assert [candidate["id"] for candidate in bot._reply_assembly()._mention_discovery_callback()(state)] == ["100"]
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
    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(lambda *_args, **_kwargs: pytest.fail(
            "corrupt queue identity must not reach provider work"
        )),
    )

    assert bot._reply_assembly()._mention_queue_owner().pending(state) == []
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

    assert bot._reply_assembly()._mention_queue_owner().pending(state) == []
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

    assert bot._reply_assembly()._mention_queue_owner().pending(state) == []
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

    assert bot._reply_assembly()._mention_discovery_callback()(state) == []
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

    assert bot._reply_assembly()._mention_queue_owner().pending(state) == []
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
    assert [candidate["id"] for candidate in bot._reply_assembly()._mention_discovery_callback()(state)] == ["103"]
    assert state["last_seen_mention_id"] == "99"
    assert state["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }
    retire_all_pending(state)

    assert [candidate["id"] for candidate in bot._reply_assembly()._mention_discovery_callback()(state)] == [
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

    assert bot._reply_assembly()._mention_queue_owner().pending(state) == []
    assert state["mention_pending_candidates"] == {}
    assert state["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }


def test_full_mention_loop_resets_stale_queue_before_provider_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_get_mentions = bot._mention_discovery.get_mentions
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
    monkeypatch.setattr(bot._mention_discovery, "get_mentions", real_get_mentions)
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
    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(lambda *_args, **_kwargs: pytest.fail(
            "stale candidates must not reach the reply provider"
        )),
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

    assert [row["id"] for row in bot._reply_assembly()._mention_discovery_callback()(state)] == ["104", "105"]
    assert state["last_seen_mention_id"] == "99"
    assert state["mention_backlog"]["next_token"] == "A"
    assert state["mention_backlog"]["pages_completed"] == 1
    assert state["mention_backlog"]["highest_mention_id"] == "105"
    retire_all_pending(state)

    assert [row["id"] for row in bot._reply_assembly()._mention_discovery_callback()(state)] == ["102", "103"]
    assert state["last_seen_mention_id"] == "99"
    assert state["mention_backlog"]["next_token"] == "B"
    retire_all_pending(state)

    assert [row["id"] for row in bot._reply_assembly()._mention_discovery_callback()(state)] == ["101"]
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
    first = bot._reply_assembly()._mention_discovery_callback()(state)
    bot._reply_assembly().reply_evaluations().record(
        state, target_id="105", lane="mention", reason="confirmed_no_reply"
    )
    retire_all_pending(state)
    second = bot._reply_assembly()._mention_discovery_callback()(state)

    assert [row["id"] for row in first] == ["105"]
    assert [row["id"] for row in second] == ["101"]
    retire_all_pending(state)
    pages[None] = ([mention(106, 206)], None)
    third = bot._reply_assembly()._mention_discovery_callback()(state)
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
    bot._reply_assembly()._mention_discovery_callback()(state)
    bot.save_state(state, durable=True)

    restarted = bot.load_state()
    assert restarted["mention_backlog"]["next_token"] == "A"
    assert [row["id"] for row in bot._reply_assembly()._mention_discovery_callback()(restarted)] == ["105"]
    assert len(requests) == 1
    retire_all_pending(restarted)
    bot.save_state(restarted, durable=True)
    assert [row["id"] for row in bot._reply_assembly()._mention_discovery_callback()(restarted)] == ["101"]
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
        bot._reply_assembly()._mention_discovery_callback()(state)

    restarted = bot.load_state()
    assert restarted["last_seen_mention_id"] == "105"
    assert restarted["mention_backlog"] == {}
    assert restarted["mention_pagination"] == {}
    assert set(restarted["mention_pending_candidates"]) == {"105"}
    assert [row["id"] for row in bot._reply_assembly()._mention_discovery_callback()(restarted)] == ["105"]
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

    returned = bot._reply_assembly()._mention_discovery_callback()(state)
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
    assert [row["id"] for row in bot._reply_assembly()._mention_discovery_callback()(restarted)] == ["103", "105"]
    assert len(requests) == 2
    bot._reply_assembly().reply_evaluations().record(
        restarted,
        target_id="105",
        lane="mention",
        reason="confirmed_no_reply",
    )
    retire_all_pending(restarted)
    bot.save_state(restarted, durable=True)

    assert bot._reply_assembly()._mention_discovery_callback()(restarted) == []
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
    assert [row["id"] for row in bot._reply_assembly()._mention_discovery_callback()(restarted_again)] == ["103"]
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
        bot._reply_assembly()._mention_discovery_callback()(state)
    assert calls == [None, "expired"]
    assert state["last_seen_mention_id"] == "99"
    assert state["mention_backlog"] == {}
    assert state["mention_pagination"] == {}
    assert state["mention_pending_candidates"] == {}
    assert state["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }

    bot._reply_assembly()._confirmed_reply_state_applier()(
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
    assert [row["id"] for row in bot._reply_assembly()._mention_discovery_callback()(state)] == [
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

    bot._reply_assembly()._confirmed_reply_state_applier()(
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

    assert [row["id"] for row in bot._reply_assembly()._mention_discovery_callback()(state)] == ["103"]
    assert state["last_seen_mention_id"] == "99"
    assert state["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }
    retire_all_pending(state)

    assert [row["id"] for row in bot._reply_assembly()._mention_discovery_callback()(state)] == [
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

    bot._reply_assembly()._confirmed_reply_state_applier()(
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
    assert [row["id"] for row in bot._reply_assembly()._mention_discovery_callback()(state)] == ["98"]
    assert requests == []
    retire_all_pending(state)

    assert [row["id"] for row in bot._reply_assembly()._mention_discovery_callback()(state)] == [
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
    bot._reply_assembly()._confirmed_reply_state_applier()(state, receipt)

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

    assert [row["id"] for row in bot._reply_assembly()._mention_discovery_callback()(restarted)] == [
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

    bot._reply_assembly()._confirmed_reply_state_applier()(
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

    assert [row["id"] for row in bot._reply_assembly()._mention_discovery_callback()(state)] == ["103"]
    assert state["last_seen_mention_id"] == "99"
    assert state["mention_backlog_reset_guard"] == {
        "base_since_id": "99",
        "head_traversal_started": False,
    }
    retire_all_pending(state)

    assert [row["id"] for row in bot._reply_assembly()._mention_discovery_callback()(state)] == [
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

    assert bot._reply_assembly()._mention_discovery_callback()(state) == []
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
    assert [row["id"] for row in bot._reply_assembly()._mention_discovery_callback()(restarted)] == ["103"]
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
        bot._reply_assembly()._mention_discovery_callback()(state)

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


def digest_author_no_reply_progress(
    state: object,
    generation_time: datetime,
    *,
    state_status: str = "available",
    config: object = DIGEST_AUTHOR_NO_REPLY_CONFIG,
    config_status: str = "available",
) -> dict:
    """Call the digest-only reporter without importing production bot logic."""
    return digest.current_author_no_reply_strike_progress(
        state,
        state_status,
        config,
        config_status,
        generation_time,
    )


def assert_digest_author_no_reply_progress_header(
    progress: dict,
    generation_time: datetime,
    *,
    discarded_legacy_author_count: int = 0,
    migrated_prior_policy_author_count: int = 0,
) -> None:
    """Assert the stable metadata shared by available progress snapshots."""
    assert progress["available"] is True
    assert progress["reason"] == ""
    assert progress["source"] == "bot_state.json"
    assert progress["authority_scope"] == DIGEST_AUTHOR_NO_REPLY_AUTHORITY_SCOPE
    assert progress["as_of_epoch"] == int(generation_time.timestamp())
    assert progress["as_of_time"] == generation_time.strftime("%Y-%m-%d %H:%M:%S")
    assert progress["threshold"] == 3
    assert progress["window_seconds"] == 21_600
    assert progress["quarantine_seconds"] == 43_200
    assert progress["omitted_author_count"] == 0
    assert (
        progress["discarded_legacy_author_count"]
        == discarded_legacy_author_count
    )
    assert (
        progress["migrated_prior_policy_author_count"]
        == migrated_prior_policy_author_count
    )


@pytest.mark.parametrize(
    ("epochs_ago", "expected_strikes_remaining"),
    [
        ([], None),
        ([3_600], 2),
        ([7_200, 3_600], 1),
    ],
    ids=("zero", "one", "two"),
)
def test_digest_current_author_no_reply_strike_progress_zero_one_and_two(
    epochs_ago: list[int],
    expected_strikes_remaining: int | None,
) -> None:
    generation_time = datetime(2026, 8, 31, 12, 0, 0, tzinfo=LONDON)
    now = int(generation_time.timestamp())
    author_id = "1762401049766436864"
    epochs = [now - seconds for seconds in epochs_ago]
    state = {
        "author_evaluation_quarantines": (
            {
                author_id: digest_author_no_reply_record(
                    epochs,
                    last_updated_epoch=now,
                )
            }
            if epochs
            else {}
        )
    }

    progress = digest_author_no_reply_progress(state, generation_time)

    assert_digest_author_no_reply_progress_header(progress, generation_time)
    if not epochs:
        assert progress["authors"] == []
        assert progress["author_count"] == 0
        return
    oldest_expiry = epochs[0] + 21_600
    assert progress["authors"] == [
        {
            "author_id": author_id,
            "recent_qualifying_no_reply_epochs": epochs,
            "recent_qualifying_no_reply_times": [
                datetime.fromtimestamp(epoch, tz=LONDON).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                for epoch in epochs
            ],
            "strike_count": len(epochs),
            "strikes_remaining": expected_strikes_remaining,
            "oldest_strike_expires_epoch": oldest_expiry,
            "oldest_strike_expires_time": datetime.fromtimestamp(
                oldest_expiry,
                tz=LONDON,
            ).strftime("%Y-%m-%d %H:%M:%S"),
            "quarantine_active": False,
            "quarantine_until_epoch": None,
            "quarantine_until_time": None,
        }
    ]
    assert progress["author_count"] == 1


def test_digest_current_author_no_reply_strike_progress_active_quarantine() -> None:
    generation_time = datetime(2026, 8, 31, 12, 0, 0, tzinfo=LONDON)
    now = int(generation_time.timestamp())
    epochs = [now - 20, now - 10, now]
    quarantine_until = now + 43_200
    state = {
        "author_evaluation_quarantines": {
            "900": digest_author_no_reply_record(
                epochs,
                quarantine_until_epoch=quarantine_until,
                last_updated_epoch=now,
            )
        }
    }

    progress = digest_author_no_reply_progress(state, generation_time)

    assert_digest_author_no_reply_progress_header(progress, generation_time)
    assert progress["author_count"] == 1
    assert progress["authors"][0] == {
        "author_id": "900",
        "recent_qualifying_no_reply_epochs": epochs,
        "recent_qualifying_no_reply_times": [
            datetime.fromtimestamp(epoch, tz=LONDON).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            for epoch in epochs
        ],
        "strike_count": 3,
        "strikes_remaining": 0,
        "oldest_strike_expires_epoch": epochs[0] + 21_600,
        "oldest_strike_expires_time": datetime.fromtimestamp(
            epochs[0] + 21_600,
            tz=LONDON,
        ).strftime("%Y-%m-%d %H:%M:%S"),
        "quarantine_active": True,
        "quarantine_until_epoch": quarantine_until,
        "quarantine_until_time": datetime.fromtimestamp(
            quarantine_until,
            tz=LONDON,
        ).strftime("%Y-%m-%d %H:%M:%S"),
    }


@pytest.mark.parametrize(
    "quarantine_until_offset",
    [-1, 0],
    ids=("already-expired", "expires-exactly-now"),
)
def test_digest_current_author_no_reply_strike_progress_expired_quarantine_clears_strikes(
    quarantine_until_offset: int,
) -> None:
    generation_time = datetime(2026, 8, 31, 12, 0, 0, tzinfo=LONDON)
    now = int(generation_time.timestamp())
    state = {
        "author_evaluation_quarantines": {
            "900": digest_author_no_reply_record(
                [now - 20, now - 10, now],
                quarantine_until_epoch=now + quarantine_until_offset,
                last_updated_epoch=now,
            )
        }
    }

    progress = digest_author_no_reply_progress(state, generation_time)

    assert_digest_author_no_reply_progress_header(progress, generation_time)
    assert progress["authors"] == []
    assert progress["author_count"] == 0


def test_digest_current_author_no_reply_strike_progress_filters_stale_epochs() -> None:
    generation_time = datetime(2026, 8, 31, 12, 0, 0, tzinfo=LONDON)
    now = int(generation_time.timestamp())
    state = {
        "author_evaluation_quarantines": {
            "900": digest_author_no_reply_record(
                [now - 21_602, now - 21_601],
                last_updated_epoch=now - 21_601,
            )
        }
    }

    progress = digest_author_no_reply_progress(state, generation_time)

    assert_digest_author_no_reply_progress_header(progress, generation_time)
    assert progress["authors"] == []
    assert progress["author_count"] == 0


def test_digest_current_author_no_reply_strike_progress_uses_exact_window_boundaries() -> None:
    generation_time = datetime(2026, 8, 31, 12, 0, 0, tzinfo=LONDON)
    now = int(generation_time.timestamp())
    cutoff = now - 21_600
    state = {
        "author_evaluation_quarantines": {
            "900": digest_author_no_reply_record(
                [cutoff, cutoff + 1, now],
                last_updated_epoch=now,
            )
        }
    }

    progress = digest_author_no_reply_progress(state, generation_time)

    assert_digest_author_no_reply_progress_header(progress, generation_time)
    author = progress["authors"][0]
    assert author["recent_qualifying_no_reply_epochs"] == [cutoff + 1, now]
    assert author["recent_qualifying_no_reply_times"] == [
        datetime.fromtimestamp(epoch, tz=LONDON).strftime("%Y-%m-%d %H:%M:%S")
        for epoch in (cutoff + 1, now)
    ]
    assert author["strike_count"] == 2
    assert author["strikes_remaining"] == 1
    assert author["oldest_strike_expires_epoch"] == now + 1
    assert author["oldest_strike_expires_time"] == datetime.fromtimestamp(
        now + 1,
        tz=LONDON,
    ).strftime("%Y-%m-%d %H:%M:%S")


@pytest.mark.parametrize(
    ("state", "state_status"),
    [
        (None, "absent"),
        (None, "malformed: ValueError: invalid state"),
        (None, "unstable: changed during read"),
        ({}, "available"),
        ({"author_evaluation_quarantines": []}, "available"),
    ],
    ids=(
        "missing-state",
        "malformed-state",
        "unstable-state",
        "missing-quarantine-field",
        "malformed-quarantine-container",
    ),
)
def test_digest_current_author_no_reply_strike_progress_state_unavailable(
    state: object,
    state_status: str,
) -> None:
    generation_time = datetime(2026, 8, 31, 12, 0, 0, tzinfo=LONDON)

    progress = digest_author_no_reply_progress(
        state,
        generation_time,
        state_status=state_status,
    )

    assert progress["available"] is False
    assert 0 < len(progress["reason"]) <= 512
    assert progress["source"] == "bot_state.json"
    assert progress["authority_scope"] == DIGEST_AUTHOR_NO_REPLY_AUTHORITY_SCOPE
    assert progress["as_of_epoch"] == int(generation_time.timestamp())
    assert progress["as_of_time"] == generation_time.strftime("%Y-%m-%d %H:%M:%S")
    assert progress["authors"] is None
    assert progress["author_count"] is None
    assert progress["omitted_author_count"] is None


@pytest.mark.parametrize(
    ("config", "config_status"),
    [
        (None, "absent"),
        (None, "malformed: ValueError: invalid config"),
        (
            {
                "AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD": 3,
                "AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS": 21_600,
            },
            "available",
        ),
        (
            {
                **DIGEST_AUTHOR_NO_REPLY_CONFIG,
                "AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD": "3",
            },
            "available",
        ),
    ],
    ids=(
        "missing-config",
        "malformed-config-status",
        "missing-required-setting",
        "malformed-required-setting",
    ),
)
def test_digest_current_author_no_reply_strike_progress_config_unavailable(
    config: object,
    config_status: str,
) -> None:
    generation_time = datetime(2026, 8, 31, 12, 0, 0, tzinfo=LONDON)
    state = {"author_evaluation_quarantines": {}}

    progress = digest_author_no_reply_progress(
        state,
        generation_time,
        config=config,
        config_status=config_status,
    )

    assert progress["available"] is False
    assert 0 < len(progress["reason"]) <= 512
    assert progress["authors"] is None
    assert progress["author_count"] is None
    assert progress["omitted_author_count"] is None
    assert progress["threshold"] is None
    assert progress["window_seconds"] is None
    assert progress["quarantine_seconds"] is None


@pytest.mark.parametrize(
    "records",
    [
        {"not-an-author-id": {}},
        {"900": []},
        {
            "900": {
                **digest_author_no_reply_record([1_788_173_999]),
                "evidence_policy": "obsolete-policy",
            }
        },
        {
            "900": {
                **digest_author_no_reply_record([1_788_173_999]),
                "recent_no_reply_epochs": ["1788173999"],
            }
        },
    ],
    ids=("invalid-author-id", "non-object", "wrong-policy", "malformed-epoch"),
)
def test_digest_current_author_no_reply_strike_progress_rejects_malformed_author(
    records: dict,
) -> None:
    generation_time = datetime(2026, 8, 31, 12, 0, 0, tzinfo=LONDON)

    progress = digest_author_no_reply_progress(
        {"author_evaluation_quarantines": records},
        generation_time,
    )

    assert progress["available"] is False
    assert 0 < len(progress["reason"]) <= 512
    assert progress["authors"] is None
    assert progress["author_count"] is None
    assert progress["omitted_author_count"] is None


def test_digest_strike_progress_discards_bare_legacy_record_without_losing_snapshot(
) -> None:
    generation_time = datetime(2026, 8, 31, 12, 0, 0, tzinfo=LONDON)
    now = int(generation_time.timestamp())
    state = {
        "author_evaluation_quarantines": {
            "800": {
                "recent_no_reply_epochs": [now - 30],
                "quarantine_until_epoch": 0,
                "last_updated_epoch": now - 30,
            },
            "900": digest_author_no_reply_record(
                [now - 20],
                last_updated_epoch=now,
            ),
        }
    }

    progress = digest_author_no_reply_progress(state, generation_time)

    assert_digest_author_no_reply_progress_header(
        progress,
        generation_time,
        discarded_legacy_author_count=1,
    )
    assert progress["author_count"] == 1
    assert [author["author_id"] for author in progress["authors"]] == ["900"]
    assert progress["authors"][0]["recent_qualifying_no_reply_epochs"] == [
        now - 20
    ]


@pytest.mark.parametrize(
    "active_quarantine_without_live_strikes",
    [False, True],
    ids=("live-strikes", "active-quarantine-only"),
)
def test_digest_strike_progress_migrates_valid_prior_v1_policy_record(
    active_quarantine_without_live_strikes: bool,
) -> None:
    generation_time = datetime(2026, 8, 31, 12, 0, 0, tzinfo=LONDON)
    now = int(generation_time.timestamp())
    epochs = [] if active_quarantine_without_live_strikes else [now - 20, now - 10]
    quarantine_until = (
        now + 43_200 if active_quarantine_without_live_strikes else 0
    )
    state = {
        "author_evaluation_quarantines": {
            "900": {
                "recent_no_reply_epochs": epochs,
                "quarantine_until_epoch": quarantine_until,
                "last_updated_epoch": now,
                "evidence_policy": DIGEST_AUTHOR_NO_REPLY_LEGACY_EVIDENCE_POLICY,
            }
        }
    }

    progress = digest_author_no_reply_progress(state, generation_time)

    assert_digest_author_no_reply_progress_header(
        progress,
        generation_time,
        migrated_prior_policy_author_count=1,
    )
    assert progress["author_count"] == 1
    author = progress["authors"][0]
    assert author["author_id"] == "900"
    assert author["recent_qualifying_no_reply_epochs"] == epochs
    assert author["quarantine_active"] is (
        active_quarantine_without_live_strikes
    )
    assert author["quarantine_until_epoch"] == (
        quarantine_until
        if active_quarantine_without_live_strikes
        else None
    )


def test_digest_strike_progress_migrates_valid_seeded_v2_policy_record() -> None:
    generation_time = datetime(2026, 8, 31, 12, 0, 0, tzinfo=LONDON)
    now = int(generation_time.timestamp())
    epochs = [now - 20, now - 10]
    record = digest_author_no_reply_record(
        epochs,
        last_updated_epoch=now,
    )
    record.update(
        {
            "latest_explicit_spam_or_abuse_epoch": epochs[0],
            "evidence_policy": DIGEST_AUTHOR_NO_REPLY_SEEDED_EVIDENCE_POLICY,
        }
    )

    progress = digest_author_no_reply_progress(
        {"author_evaluation_quarantines": {"900": record}},
        generation_time,
    )

    assert_digest_author_no_reply_progress_header(
        progress,
        generation_time,
        migrated_prior_policy_author_count=1,
    )
    assert progress["author_count"] == 1
    assert progress["authors"][0]["author_id"] == "900"
    assert progress["authors"][0]["recent_qualifying_no_reply_epochs"] == epochs
    assert progress["authors"][0]["strike_count"] == 2


def test_digest_accepts_real_f909_single_sol_quarantine_record() -> None:
    """Report only the explicit strike retained by the f909 writer."""

    state = json.loads(F909_SINGLE_SOL_STATE_FIXTURE.read_text(encoding="utf-8"))
    generation_time = datetime.fromtimestamp(2_000_000_003, tz=LONDON)

    progress = digest_author_no_reply_progress(state, generation_time)

    assert_digest_author_no_reply_progress_header(
        progress,
        generation_time,
        migrated_prior_policy_author_count=1,
    )
    assert progress["author_count"] == 1
    assert progress["authors"][0]["author_id"] == "200"
    assert progress["authors"][0]["recent_qualifying_no_reply_epochs"] == [
        2_000_000_000
    ]
    assert progress["authors"][0]["quarantine_active"] is False


def test_digest_accepts_f909_record_after_explicit_epoch_ages_out() -> None:
    """Mirror production migration for a valid old-writer prune result."""

    start = 2_000_000_000
    later_editorial_epoch = start + 5 * 60 * 60
    generation_time = datetime.fromtimestamp(
        start + bot.AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS + 1,
        tz=LONDON,
    )
    record = {
        "recent_no_reply_epochs": [later_editorial_epoch],
        "quarantine_until_epoch": 0,
        "last_updated_epoch": later_editorial_epoch,
        "latest_explicit_spam_or_abuse_epoch": start,
        "evidence_policy": (
            bot.AUTHOR_EVALUATION_QUARANTINE_SINGLE_SOL_V1_EVIDENCE_POLICY
        ),
    }

    progress = digest_author_no_reply_progress(
        {"author_evaluation_quarantines": {"200": record}},
        generation_time,
    )

    assert_digest_author_no_reply_progress_header(
        progress,
        generation_time,
        migrated_prior_policy_author_count=1,
    )
    assert progress["author_count"] == 0
    assert progress["authors"] == []


def test_digest_strike_progress_rejects_seeded_v2_epoch_outside_strike_membership(
) -> None:
    generation_time = datetime(2026, 8, 31, 12, 0, 0, tzinfo=LONDON)
    now = int(generation_time.timestamp())
    record = digest_author_no_reply_record(
        [now - 20, now - 10],
        last_updated_epoch=now,
    )
    record.update(
        {
            "latest_explicit_spam_or_abuse_epoch": now - 30,
            "evidence_policy": DIGEST_AUTHOR_NO_REPLY_SEEDED_EVIDENCE_POLICY,
        }
    )

    progress = digest_author_no_reply_progress(
        {"author_evaluation_quarantines": {"900": record}},
        generation_time,
    )

    assert progress["available"] is False
    assert "malformed author quarantine record" in progress["reason"]
    assert progress["authors"] is None
    assert progress["author_count"] is None
    assert progress["omitted_author_count"] is None
    assert progress["discarded_legacy_author_count"] is None
    assert progress["migrated_prior_policy_author_count"] is None


@pytest.mark.parametrize(
    "setting",
    [
        "AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD",
        "AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS",
        "AUTHOR_NO_REPLY_QUARANTINE_SECONDS",
    ],
)
def test_digest_strike_progress_rejects_extreme_positive_quarantine_configuration(
    setting: str,
) -> None:
    """Unbounded positive integers must not become trusted reporting policy."""
    generation_time = datetime(2026, 8, 31, 12, 0, 0, tzinfo=LONDON)
    config = {
        **DIGEST_AUTHOR_NO_REPLY_CONFIG,
        setting: 10**100,
    }

    progress = digest_author_no_reply_progress(
        {"author_evaluation_quarantines": {}},
        generation_time,
        config=config,
    )

    assert progress["available"] is False
    assert 0 < len(progress["reason"]) <= 512
    assert progress["authors"] is None
    assert progress["author_count"] is None
    assert progress["omitted_author_count"] is None


def test_digest_strike_progress_reports_derived_epoch_overflow_as_unavailable(
) -> None:
    """An overflowing strike-expiry derivation must not abort JSON generation."""
    generation_time = datetime(2026, 8, 31, 12, 0, 0, tzinfo=LONDON)
    now = int(generation_time.timestamp())
    config = {
        **DIGEST_AUTHOR_NO_REPLY_CONFIG,
        "AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS": 10**100,
    }
    state = {
        "author_evaluation_quarantines": {
            "900": digest_author_no_reply_record(
                [now - 10],
                last_updated_epoch=now,
            )
        }
    }

    progress = digest_author_no_reply_progress(
        state,
        generation_time,
        config=config,
    )

    assert progress["available"] is False
    assert 0 < len(progress["reason"]) <= 512
    assert progress["authors"] is None
    assert progress["author_count"] is None
    assert progress["omitted_author_count"] is None


def test_digest_strike_progress_uses_state_observation_time_for_as_of_boundary(
) -> None:
    """The current-state projection must not use a pre-log-analysis clock."""
    generation_time = datetime(2026, 8, 31, 12, 0, 0, tzinfo=LONDON)
    state_observed_at = generation_time + timedelta(minutes=5)
    observed_epoch = int(state_observed_at.timestamp())
    strike_epoch = observed_epoch - 10
    state = {
        "author_evaluation_quarantines": {
            "900": digest_author_no_reply_record(
                [strike_epoch],
                last_updated_epoch=strike_epoch,
            )
        }
    }

    progress = digest.current_author_no_reply_strike_progress(
        state,
        "available",
        DIGEST_AUTHOR_NO_REPLY_CONFIG,
        "available",
        generation_time,
        state_observed_at=state_observed_at,
    )

    assert progress["available"] is True
    assert progress["as_of_epoch"] == observed_epoch
    assert progress["as_of_time"] == state_observed_at.strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    assert progress["authors"][0][
        "recent_qualifying_no_reply_epochs"
    ] == [strike_epoch]


def test_digest_run_passes_post_parse_state_observation_to_strike_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI path must not discard the reporter's observation-time input."""
    generation_time = datetime(2026, 8, 31, 12, 0, 0)
    state_observed_at = generation_time + timedelta(minutes=5)
    observed_epoch = int(state_observed_at.timestamp())
    strike_epoch = observed_epoch - 10
    (tmp_path / "bot_state.json").write_text(
        json.dumps(
            {
                "author_evaluation_quarantines": {
                    "900": digest_author_no_reply_record(
                        [strike_epoch],
                        last_updated_epoch=strike_epoch,
                    )
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "mrsMThatcher.local.json").write_text(
        json.dumps(DIGEST_AUTHOR_NO_REPLY_CONFIG),
        encoding="utf-8",
    )
    log_path = tmp_path / "test.log"
    log_path.write_text(
        "2026-08-31 11:59:59 INFO log_event:10 - harmless fixture\n",
        encoding="utf-8",
    )
    now_calls = 0

    class SequencedDateTime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> "SequencedDateTime":
            nonlocal now_calls
            now_calls += 1
            selected = generation_time if now_calls == 1 else state_observed_at
            if tz is not None:
                selected = selected.astimezone(tz)  # type: ignore[arg-type]
            return cls.fromtimestamp(selected.timestamp(), tz=selected.tzinfo)

    monkeypatch.setattr(digest, "datetime", SequencedDateTime)

    return_code = digest.main(
        [
            "--project-dir",
            str(tmp_path),
            "--no-state",
            "--json",
            str(log_path),
        ]
    )

    captured = capsys.readouterr()
    assert return_code == 0, captured.err
    payload = json.loads(captured.out)
    progress = payload["mention_backlog_and_quarantine"][
        "current_author_no_reply_strike_progress"
    ]
    assert progress["available"] is True
    assert progress["as_of_epoch"] == observed_epoch
    assert progress["authors"][0][
        "recent_qualifying_no_reply_epochs"
    ] == [strike_epoch]
    assert payload["runtime_state_status"]["observed_at"] == (
        state_observed_at.strftime("%Y-%m-%d %H:%M:%S")
    )


@pytest.mark.parametrize(
    ("loader_name", "filename", "old_document", "new_document"),
    [
        (
            "load_current_runtime_state",
            "bot_state.json",
            {"author_evaluation_quarantines": {}, "daily_reply_count": 1},
            {"author_evaluation_quarantines": {}, "daily_reply_count": 2},
        ),
        (
            "load_current_runtime_config",
            "mrsMThatcher.local.json",
            {
                **DIGEST_AUTHOR_NO_REPLY_CONFIG,
                "MAX_AUTO_REPLIES_PER_DAY": 11,
            },
            {
                **DIGEST_AUTHOR_NO_REPLY_CONFIG,
                "MAX_AUTO_REPLIES_PER_DAY": 12,
            },
        ),
    ],
    ids=("state", "config"),
)
def test_digest_current_loader_binds_content_and_mtime_to_one_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    loader_name: str,
    filename: str,
    old_document: dict,
    new_document: dict,
) -> None:
    """A pathname replacement after reading is detected as unstable."""
    runtime_path = tmp_path / filename
    replacement = tmp_path / f"replacement-{filename}"
    runtime_path.write_text(json.dumps(old_document), encoding="utf-8")
    replacement.write_text(json.dumps(new_document), encoding="utf-8")
    old_epoch = 1_700_000_000
    new_epoch = old_epoch + 3_600
    os.utime(runtime_path, ns=(old_epoch * 1_000_000_000,) * 2)
    os.utime(replacement, ns=(new_epoch * 1_000_000_000,) * 2)

    real_lstat = digest.os.lstat
    matching_lstat_calls = 0

    def replace_before_detached_metadata_read(
        path: os.PathLike[str] | str,
        *args: object,
        **kwargs: object,
    ) -> os.stat_result:
        nonlocal matching_lstat_calls
        if Path(path) == runtime_path:
            matching_lstat_calls += 1
            if matching_lstat_calls == 2:
                replacement.replace(runtime_path)
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(digest.os, "lstat", replace_before_detached_metadata_read)

    loaded, loaded_path, observed_mtime, status = getattr(
        digest, loader_name
    )(tmp_path)

    assert loaded_path == runtime_path
    assert matching_lstat_calls == 2
    assert not replacement.exists()
    assert status.startswith("unstable")
    assert loaded is None
    assert observed_mtime is None


@pytest.mark.parametrize(
    "unsafe_logger",
    [
        "sk-live-provenance-secret",
        "sk-live-provenance-secret../" + ("x" * 4_096),
    ],
    ids=("short-token-like", "oversized-path-like"),
)
def test_digest_source_ref_rejects_sensitive_logger_values(
    unsafe_logger: str,
) -> None:
    """Unsafe logger text must not be copied or prefix-truncated into JSON."""
    sensitive_marker = "sk-live-provenance-secret"
    record = digest.Record(
        ts=datetime(2026, 8, 31, 12, 0, 0),
        level="ERROR",
        src=unsafe_logger,
        line=99,
        msg="safe synthetic error",
        path="/tmp/test.log",
        ordinal=7,
    )

    reference = digest.record_source_ref(record, {record.path: 0})
    rendered = json.dumps(reference)

    assert sensitive_marker not in rendered
    assert unsafe_logger not in rendered
    logger = reference.get("logger")
    assert logger is None or (
        isinstance(logger, str)
        and 0 < len(logger) <= 128
        and sensitive_marker not in logger
    )


def test_digest_source_ref_preserves_bounded_identifier_logger() -> None:
    record = digest.Record(
        ts=datetime(2026, 8, 31, 12, 0, 0),
        level="INFO",
        src="maybe_reply_to_mentions",
        line=100,
        msg="safe synthetic event",
        path="/tmp/test.log",
        ordinal=8,
    )

    reference = digest.record_source_ref(record, {record.path: 0})

    assert reference["logger"] == "maybe_reply_to_mentions"
