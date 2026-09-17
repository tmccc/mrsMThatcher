"""Regression tests for bot pending reply drafts."""

from __future__ import annotations

from mrs_bot_reply_cycle_interfaces import PreparedReplyContext
from tests.helpers.reply_evaluation import legacy_reply_evaluator

import copy
import json
import hashlib
from datetime import datetime
from pathlib import Path

import pytest

from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime
from tests.helpers.reply_fixtures import (
    UNIT_REPLY_REPOSITORY,
    UnitReplyEvidenceRepository,
    unit_reply_context,
    unit_approved_reply,
    unit_confirmed_reply_receipt,
)
from single_call_reply import ValidatedReply


pytestmark = pytest.mark.allow_loopback_network


def test_pending_ai_reply_survives_state_round_trip_and_is_reused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    state = bot.default_state()
    context = unit_reply_context(target_id="100", contribution="A stable contribution.")
    reply = unit_approved_reply(context, text="The first draft remains the first draft.")
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context) is True
    bot.save_state(state, durable=True)
    loaded = bot.load_state()
    reused = bot.pending_ai_reply(loaded, "100", "mention", context=context)
    assert reused == reply
    assert isinstance(reused, ValidatedReply)
    assert reused.draft_record == reply.draft_record


def test_confirmed_reply_reconciliation_clears_pending_ai_draft() -> None:
    state = bot.default_state()
    context = unit_reply_context(target_id="100")
    reply = unit_approved_reply(context, text="A stable draft.")
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context) is True
    receipt = unit_confirmed_reply_receipt(
        target_id="100",
        reply_post_id="900000",
        text="A stable draft.",
    )
    bot.apply_confirmed_reply_receipt(state, receipt)
    assert bot.pending_ai_reply(state, "100", "mention", context=context) is None


def test_nonempty_v1_pending_reply_draft_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {
        "pending_reply_drafts": {
            "mention:100": {
                "target_id": "different", "candidate_source": "mention",
                "reply_text": "Unsafe stale draft.",
                "strategy_metadata": {"mode": "wry_reply", "reply_text": "Unsafe stale draft."},
            }
        }
    }
    state_file = tmp_path / "bot_state.json"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)

    with pytest.raises(RuntimeError, match="Legacy V1 reply drafts remain"):
        bot.load_state()


def test_pending_ai_reply_rejects_changed_incoming_context() -> None:
    state = bot.default_state()
    original = unit_reply_context(target_id="100", contribution="A first contribution.")
    changed = unit_reply_context(target_id="100", contribution="A materially different contribution.")
    reply = unit_approved_reply(original, text="Responsibility matters.", mode="opinion_or_principle")
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=original) is True

    assert bot.pending_ai_reply(state, "100", "mention", context=changed) is None


def test_safe_pending_opinion_reply_reuses_the_persisted_context() -> None:
    state = bot.default_state()
    incoming = "Institutions endure when people defend their purpose."
    text = "Institutions endure only when people defend their purpose."
    context = unit_reply_context(target_id="100", contribution=incoming)
    reply = unit_approved_reply(context, text=text, mode="opinion_or_principle")
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context) is True

    assert bot.pending_ai_reply(state, "100", "mention", context=context) == reply


def test_pending_ai_reply_is_retired_if_confirmed_replies_now_duplicate_it() -> None:
    state = bot.default_state()
    incoming = "Institutions endure when people defend their purpose."
    text = "Institutions endure only when people defend their purpose."
    context = unit_reply_context(target_id="100", contribution=incoming)
    reply = unit_approved_reply(context, text=text, mode="opinion_or_principle")
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context) is True

    outcome: dict[str, object] = {}
    reused = bot.pending_ai_reply(
        state,
        "100",
        "mention",
        context=context,
        recent_replies=[text],
        evaluation_outcome=outcome,
    )
    assert reused is None
    assert state.get("pending_ai_reply_drafts") is None
    assert outcome == {
        "status": "operational_failure",
        "reason": "persisted_draft_local_validation_failed",
        "error_category": "local_validation",
        "model_call_count": 0,
    }


def test_duplicate_pending_draft_is_retired_and_later_mention_proceeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovery performs no paid retry and cannot starve a later candidate."""

    current = 2_000_000_000
    text = "Institutions endure only when people defend their purpose."
    candidates = [
        {
            "id": target_id,
            "author_id": author_id,
            "conversation_id": target_id,
            "text": contribution,
            "referenced_tweets": [],
            "entities": {
                "mentions": [
                    {"id": str(bot.MY_USER_ID), "username": "MrsMThatcher"}
                ]
            },
        }
        for target_id, author_id, contribution in (
            ("101", "201", "@MrsMThatcher A first contribution."),
            ("102", "202", "@MrsMThatcher A later contribution."),
        )
    ]

    def candidate_context(candidate: dict) -> dict[str, object]:
        return unit_reply_context(
            target_id=str(candidate["id"]),
            contribution=str(candidate["text"]),
            target_author_id=str(candidate["author_id"]),
        )

    state = bot.default_state()
    first_context = candidate_context(candidates[0])
    reply = unit_approved_reply(
        first_context,
        text=text,
        mode="opinion_or_principle",
    )
    assert bot.store_pending_ai_reply(
        state,
        "101",
        "mention",
        reply,
        context=first_context,
    ) is True
    state["ai_reply_history"] = [
        {
            "target_id": "90",
            "reply_post_id": "9000",
            "candidate_source": "mention",
            "reply_epoch": current - 1,
            "proposed_reply": text,
        }
    ]
    state["daily_reply_date"] = bot.reply_cap_date_str(current)
    state["daily_reply_count"] = 2
    calls: list[str] = []
    events: list[tuple[str, dict[str, object]]] = []

    def decide(
        context: dict[str, object],
        *_args: object,
        evaluation_outcome: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> None:
        calls.append(str(context["target_id"]))
        assert evaluation_outcome is not None
        evaluation_outcome.update(
            {
                "status": "no_reply",
                "reason": "completed_exchange",
                "reason_code": "completed_exchange",
                "model_call_count": 1,
            }
        )
        return None

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "now_epoch", lambda: current)
    monkeypatch.setattr(
        bot,
        "current_datetime",
        lambda: datetime.fromtimestamp(current),
    )
    monkeypatch.setattr(bot, "lane_paused", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "get_mentions", lambda _state: copy.deepcopy(candidates))
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(
        bot,
        "is_probably_spam_or_not_worth_replying",
        lambda _text: False,
    )
    monkeypatch.setattr(
        bot,
        "build_context_for_reply_ai",
        lambda candidate, _state: PreparedReplyContext(candidate_context(candidate), {}),
    )
    monkeypatch.setattr(
        bot,
        "reply_media_context_for_candidate",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        bot,
        "reply_evidence_repository",
        lambda: UNIT_REPLY_REPOSITORY,
    )
    monkeypatch.setattr(bot, "evaluate_single_call_reply", legacy_reply_evaluator(decide))
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **values: events.append((name, values)),
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert calls == ["102"]
    assert state.get("pending_ai_reply_drafts") is None
    assert bot.terminal_reply_evaluation(state, "101")["outcome"] == (
        "operational_failure"
    )
    assert bot.terminal_reply_evaluation(state, "102")["outcome"] == "no_reply"
    assert state["daily_reply_count"] == 2
    assert state["author_evaluation_quarantines"] == {}
    recovery_events = [
        values
        for name, values in events
        if name == "single_call_reply_decision"
        and values.get("target_id") == "101"
    ]
    assert len(recovery_events) == 1
    assert recovery_events[0]["model_call_count"] == 0
    assert recovery_events[0]["error_category"] == "local_validation"

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert calls == ["102"]


def test_recovery_duplicate_comparisons_include_same_author_beyond_latest_30(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not lose an author's older confirmed prose behind global volume."""

    current = 2_000_000_000
    same_author_text = "An older same-author confirmed reply."
    same_author_contribution = "An older contribution from this author."
    state = bot.default_state()
    state["ai_reply_history"] = [
        {
            "target_id": "50",
            "reply_post_id": "8000",
            "author_id": "201",
            "conversation_id": "50",
            "root_post_id": "50",
            "incoming_contribution": same_author_contribution,
            "incoming_contribution_sha256": hashlib.sha256(
                same_author_contribution.encode("utf-8")
            ).hexdigest(),
            "candidate_source": "mention",
            "reply_epoch": current - 100,
            "proposed_reply": same_author_text,
        },
        *[
            {
                "target_id": str(100 + index),
                "reply_post_id": str(9000 + index),
                "candidate_source": "hot_post_reply",
                "reply_epoch": current - 40 + index,
                "proposed_reply": f"Newer global reply {index}.",
            }
            for index in range(31)
        ],
    ]
    monkeypatch.setattr(bot, "now_epoch", lambda: current)
    context = unit_reply_context(
        target_id="500",
        target_author_id="201",
    )

    comparisons = bot.recovery_comparison_account_replies(
        state,
        context=context,
    )

    assert len(comparisons) == 31
    assert comparisons[0] == {
        "post_id": "8000",
        "text": same_author_text,
    }
    assert [row["text"] for row in comparisons[1:]] == [
        f"Newer global reply {index}." for index in range(1, 31)
    ]


def test_pending_ai_reply_rejects_overlong_incoming_context() -> None:
    incoming = (
        "Institutions endure when people defend their purpose. "
        + ("context " * 3000).strip()
    )
    text = "Institutions endure only when people defend their purpose."
    context = unit_reply_context(target_id="100", contribution=incoming)

    assert len(incoming) > 10_000
    with pytest.raises(RuntimeError, match="visible-context character bound"):
        unit_approved_reply(context, text=text, mode="opinion_or_principle")


def test_pending_ai_reply_rejects_context_beyond_schema_limit() -> None:
    text = "Institutions endure only when people defend their purpose."
    context = unit_reply_context(target_id="100", contribution="x" * 20_001)

    with pytest.raises(RuntimeError, match="visible-context character bound"):
        unit_approved_reply(context, text=text, mode="opinion_or_principle")


def test_pending_reply_created_under_an_older_strategy_version_is_not_reused() -> None:
    state = bot.default_state()
    context = unit_reply_context(target_id="100")
    reply = unit_approved_reply(context, text="An old draft.")
    record = dict(reply.draft_record)
    record["strategy_version"] = "ai-first-reply-v1"
    state["pending_ai_reply_drafts"] = {"mention:100": record}

    assert bot.pending_ai_reply(state, "100", "mention", context=context) is None


def test_pending_factual_reply_is_not_reused_when_evidence_disappears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    context = unit_reply_context(target_id="100", contribution="What happened at the event?")
    reply = unit_approved_reply(
        context,
        text="People moved from East Germany towards West Germany in November 1989.",
        mode="direct_factual_answer",
        factual=True,
    )
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context) is True
    missing_repository = UnitReplyEvidenceRepository()
    missing_repository.passages = {}
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: missing_repository)

    assert bot.pending_ai_reply(state, "100", "mention", context=context) is None


def test_pending_factual_reply_fails_closed_when_local_corpus_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    context = unit_reply_context(target_id="100", contribution="What happened at the event?")
    reply = unit_approved_reply(
        context,
        text="People moved from East Germany towards West Germany in November 1989.",
        mode="direct_factual_answer",
        factual=True,
    )
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context) is True
    monkeypatch.setattr(
        bot,
        "reply_evidence_repository",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("invalid local corpus")),
    )

    assert bot.pending_ai_reply(state, "100", "mention", context=context) is None


def test_pending_reply_is_preserved_when_evidence_repository_is_temporarily_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    context = unit_reply_context(target_id="100")
    reply = unit_approved_reply(
        context,
        text="Institutions endure only when people defend their purpose.",
        mode="opinion_or_principle",
    )
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context)
    saved = copy.deepcopy(state["pending_ai_reply_drafts"]["mention:100"])
    monkeypatch.setattr(
        bot,
        "reply_evidence_repository",
        lambda: (_ for _ in ()).throw(bot.ReplyEvidenceUnavailable("corpus unavailable")),
    )

    with pytest.raises(bot.ReplyEvidenceUnavailable, match="corpus unavailable"):
        bot.pending_ai_reply(state, "100", "mention", context=context)

    assert state["pending_ai_reply_drafts"]["mention:100"] == saved


def test_pending_factual_reply_is_reused_after_source_hash_revalidation() -> None:
    state = bot.default_state()
    context = unit_reply_context(target_id="100", contribution="What happened at the event?")
    reply = unit_approved_reply(
        context,
        text="People moved from East Germany towards West Germany in November 1989.",
        mode="direct_factual_answer",
        factual=True,
    )
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context) is True

    reused = bot.pending_ai_reply(state, "100", "mention", context=context)

    assert reused == reply
    assert reused.draft_record["used_fact_sources"] == reply.draft_record["used_fact_sources"]


def test_pending_ai_reply_drafts_are_bounded() -> None:
    state = bot.default_state()
    for target in range(101, 202):
        context = unit_reply_context(target_id=str(target))
        reply = unit_approved_reply(context, text="Stable draft.")
        assert bot.store_pending_ai_reply(state, str(target), "mention", reply, context=context)
    assert len(state["pending_ai_reply_drafts"]) == 100
    assert "mention:101" not in state["pending_ai_reply_drafts"]
    assert "mention:201" in state["pending_ai_reply_drafts"]
