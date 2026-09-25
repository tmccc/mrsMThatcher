"""Exercise reply-lane orchestration with explicit, root-free collaborators."""

from __future__ import annotations

from logging import Logger
from unittest.mock import Mock

import pytest

from mrs_bot_normal_reply_cycle import ContinueNormalReplyPass, NormalReplyCycle
from mrs_bot_quote_reply_cycle import QuoteReplyCycle
from mrs_bot_reply_cycle_interfaces import NormalReplyConfig, PreparedReplyContext, QuoteReplyConfig
from mrs_bot_reply_delivery import ReplyDeliveryStop
from mrs_bot_runtime_state_helpers import default_state
from single_call_reply import PipelineResult, ValidatedReply, STRATEGY_VERSION
from tests.helpers.mention_values import mention
from tests.helpers.reply_values import UnitReplyEvidenceRepository, unit_approved_reply, unit_reply_context


class ApiError(Exception):
    """Represent a local API error independently of root registration."""


class RemotePaused(Exception):
    """Represent a current runtime-control pause."""


class EvidenceUnavailable(Exception):
    """Represent unavailable local evidence."""


def _owner(*methods: str) -> Mock:
    """Create an owner that rejects unsupported operations."""
    return Mock(spec_set=list(methods))


def normal_case(*, candidates=None, maximum_fresh=2):
    """Build one fresh normal runner with a validated candidate and delivery seam."""
    state = default_state(STATE_MINIMUM_READER_VERSION=1)
    candidate = mention(105, 205)
    candidates = [candidate] if candidates is None else candidates
    context = unit_reply_context(target_id="105", target_author_id="205")
    media = {"native": object()}
    prepared = PreparedReplyContext(context, media)
    reply = unit_approved_reply(context)
    confirmed = {"lifecycle_state": "confirmed", "reply_post_id": "999"}
    delivery = _owner("load_receipt", "reconcile_receipt", "block_ambiguous", "bind_attempt", "deliver", "finalise")
    delivery.load_receipt.return_value = ("absent", None)
    delivery.bind_attempt.side_effect = lambda receipt: receipt
    delivery.deliver.return_value = confirmed
    delivery.finalise.return_value = "999"
    accounting = _owner("reset", "author_count")
    accounting.author_count.return_value = 0
    quarantine = _owner("prune", "active", "record_no_reply")
    quarantine.prune.return_value = False
    quarantine.active.return_value = None
    clarifications = _owner("thread_is_terminal", "context")
    clarifications.thread_is_terminal.return_value = False
    clarifications.context.return_value = None
    persistence = _owner("save", "recover", "store", "clear", "retire_ineligible")
    persistence.recover.return_value = None
    persistence.store.return_value = True
    generation = _owner("evaluate", "record_result")
    generation.evaluate.return_value = PipelineResult(status="reply", reason="useful_reply", reply=reply, model_call_count=1)
    contexts = _owner("build")
    contexts.build.return_value = prepared
    queue = _owner("pending", "mark_seen")
    queue.pending.return_value = False
    evaluations = _owner("record", "prune", "prune_completed_mentions")
    history = _owner("recovery_replies")
    history.recovery_replies.return_value = []
    cooldowns = _owner("active", "record_error")
    cooldowns.active.return_value = False
    controls = _owner("lane_paused")
    controls.lane_paused.return_value = False
    repository = UnitReplyEvidenceRepository()
    runner = NormalReplyCycle(
        delivery=delivery, author_quarantines=quarantine, ApiError=ApiError,
        config=NormalReplyConfig(True, True, 10, 3, 0, "12345", maximum_fresh, 1000),
        PipelineResult=PipelineResult, RemoteOperationsPaused=RemotePaused,
        ReplyEvidenceUnavailable=EvidenceUnavailable, SINGLE_CALL_STRATEGY_VERSION=STRATEGY_VERSION,
        ValidatedReply=ValidatedReply, _log_validated_single_call_reply=Mock(),
        generation=generation, reply_contexts=contexts, tweets=_owner("store"),
        clarifications=clarifications, persistence=persistence,
        conversational_reply_pipeline_enabled=lambda: True, accounting=accounting,
        dedupe_reply_candidates=lambda mentions, hot: [*mentions, *hot],
        get_hot_post_reply_candidates=Mock(return_value=[]), get_mentions=Mock(return_value=candidates),
        cooldowns=cooldowns, is_probably_spam_or_not_worth_replying=lambda _text: False,
        controls=controls, log=Mock(spec=Logger), log_ai_reply_posting_outcome=Mock(),
        log_event=Mock(), mention_queue=queue, maybe_mark_hot_post_reply_skipped=Mock(),
        now_epoch=lambda: 2_000_000_000, reply_evaluations=evaluations, history=history,
        reply_evidence_repository=lambda: repository, reply_target_is_directly_eligible=lambda _tweet: True,
        valid_tweets_sorted_by_id=lambda tweets, **_kwargs: tweets,
    )
    return runner, state, candidate, prepared, reply, confirmed


def quote_case():
    """Build one fresh quote runner with a direct reference and controlled age."""
    state = default_state(STATE_MINIMUM_READER_VERSION=1)
    original = {"id": "900", "author_id": "12345", "text": "Original account post."}
    candidate = mention(105, 205, "A useful quote contribution.")
    candidate["referenced_tweets"] = [{"type": "quoted", "id": "900"}]
    candidate["created_at"] = "2026-07-20T12:00:00Z"
    context = unit_reply_context(target_id="105", target_author_id="205", lane="quote_tweet")
    media = {"native": object()}
    prepared = PreparedReplyContext(context, media)
    reply = unit_approved_reply(context)
    confirmed = {"lifecycle_state": "confirmed", "reply_post_id": "999"}
    delivery = _owner("load_receipt", "reconcile_receipt", "block_ambiguous", "bind_attempt", "deliver", "finalise")
    delivery.load_receipt.return_value = ("absent", None)
    delivery.bind_attempt.side_effect = lambda receipt: receipt
    delivery.deliver.return_value = confirmed
    delivery.finalise.return_value = "999"
    accounting = _owner("reset", "reset_quotes", "author_count")
    accounting.author_count.return_value = 0
    persistence = _owner("save", "recover", "store", "clear", "retire_ineligible")
    persistence.recover.return_value = None
    persistence.store.return_value = True
    generation = _owner("evaluate", "record_result")
    generation.evaluate.return_value = PipelineResult(status="reply", reason="useful_reply", reply=reply, model_call_count=1)
    contexts = _owner("build_quote")
    contexts.build_quote.return_value = prepared
    tweets = _owner("get_cached", "store")
    tweets.get_cached.return_value = original
    cooldowns = _owner("active", "record_error")
    cooldowns.active.return_value = False
    controls = _owner("lane_paused")
    controls.lane_paused.return_value = False
    evaluations = _owner("record")
    history = _owner("recovery_replies")
    history.recovery_replies.return_value = []
    repository = UnitReplyEvidenceRepository()
    runner = QuoteReplyCycle(
        delivery=delivery, ApiError=ApiError, ContextValidationError=ValueError,
        config=QuoteReplyConfig(True, True, 10, 3, 0, "12345", True, 100, 3, 3),
        PipelineResult=PipelineResult, RemoteOperationsPaused=RemotePaused,
        ReplyEvidenceUnavailable=EvidenceUnavailable, SINGLE_CALL_STRATEGY_VERSION=STRATEGY_VERSION,
        ValidatedReply=ValidatedReply, _log_validated_single_call_reply=Mock(),
        generation=generation, api_error_is_permanent_target_failure=lambda _exc: False,
        watch_posts=_owner("lookup"), reply_contexts=contexts, tweets=tweets,
        persistence=persistence, conversational_reply_pipeline_enabled=lambda: True,
        accounting=accounting, get_quote_tweets_for_posts=Mock(return_value={"900": [candidate]}),
        cooldowns=cooldowns, is_probably_spam_or_not_worth_replying=lambda _text: False,
        controls=controls, log=Mock(spec=Logger), log_ai_reply_posting_outcome=Mock(),
        log_event=Mock(), now_epoch=lambda: 2_000_000_000,
        parse_x_datetime_to_epoch=lambda _value: 2_000_000_000 - 200,
        reply_evaluations=evaluations, history=history,
        reply_evidence_repository=lambda: repository,
        valid_tweets_sorted_by_id=lambda tweets, **_kwargs: tweets,
    )
    runner.watch_posts.lookup.return_value = ["900"]
    return runner, state, candidate, prepared, reply, confirmed


@pytest.mark.parametrize("lane", ["normal", "quote"])
def test_validated_candidate_reaches_delivery_with_original_context_and_draft(lane):
    runner, state, candidate, prepared, reply, confirmed = (
        normal_case() if lane == "normal" else quote_case()
    )
    assert runner.run(state) == "posted"
    runner.generation.evaluate.assert_called_once_with(prepared.context, prepared.media_context, state=state)
    runner.persistence.store.assert_called_once()
    assert runner.persistence.store.call_args.args[3] is reply
    assert runner.persistence.store.call_args.kwargs["context"] is prepared.context
    runner.delivery.deliver.assert_called_once()
    args = runner.delivery.deliver.call_args.args
    assert args[:3] == (state, "105", reply)
    receipt = args[3]
    assert receipt["schema_version"] == 4 and receipt["lifecycle_state"] == "sending"
    assert receipt["reply_context"] == prepared.context
    assert receipt["reply_context"] is not prepared.context
    assert receipt["ai_reply_draft"] == reply.draft_record
    assert receipt["ai_reply_draft"] is not reply.draft_record
    assert runner.delivery.deliver.call_args.kwargs["lane"] == ("mention" if lane == "normal" else "quote_tweet")
    runner.delivery.finalise.assert_called_once_with(state, confirmed, target_id="105", quote_reply=lane == "quote")


@pytest.mark.parametrize("lane", ["normal", "quote"])
def test_recovered_draft_skips_fresh_evaluation(lane):
    runner, state, _candidate, _prepared, reply, _confirmed = (
        normal_case() if lane == "normal" else quote_case()
    )
    runner.persistence.recover.return_value = PipelineResult(status="reply", reason="recovered", reply=reply, model_call_count=0)
    assert runner.run(state) == "posted"
    runner.generation.evaluate.assert_not_called()
    runner.delivery.deliver.assert_called_once()


def test_normal_zero_call_evaluation_does_not_consume_next_candidate_budget():
    first = mention(105, 205)
    second = mention(106, 206)
    runner, state, _candidate, _prepared, _reply, _confirmed = normal_case(candidates=[first, second], maximum_fresh=1)
    runner.generation.evaluate.side_effect = [
        PipelineResult(status="no_reply", reason="completed_exchange", reason_code="completed_exchange", model_call_count=0),
        PipelineResult(status="no_reply", reason="completed_exchange", reason_code="completed_exchange", model_call_count=1),
    ]
    assert runner.run(state) == "checked"
    assert runner.generation.evaluate.call_count == 2
    assert runner.reply_evaluations.record.call_count == 2
    assert runner.mention_queue.mark_seen.call_count == 2
    runner.delivery.deliver.assert_not_called()


def test_quote_young_candidate_waits_without_model_or_durable_skip():
    runner, state, _candidate, _prepared, _reply, _confirmed = quote_case()
    runner.parse_x_datetime_to_epoch = lambda _value: 2_000_000_000 - 99
    assert runner.run(state) == "checked"
    runner.generation.evaluate.assert_not_called()
    runner.delivery.deliver.assert_not_called()
    assert state["skipped_quote_post_ids"] == []


def test_runtime_pause_after_admission_defers_normal_candidate():
    runner, state, _candidate, _prepared, _reply, _confirmed = normal_case()
    runner.generation.evaluate.side_effect = RemotePaused("paused")
    assert runner.run(state) == "checked"
    runner.persistence.save.assert_called_with(state, durable=True)
    runner.delivery.deliver.assert_not_called()
    assert runner.controls.lane_paused.call_count == 1


def test_normal_continuation_keeps_budget_and_suppresses_second_hot_post_fetch():
    runner, state, _candidate, _prepared, _reply, _confirmed = normal_case(maximum_fresh=2)
    state["mention_backlog"] = {"next_token": "A"}
    runner.mention_queue.pending.side_effect = [True, False]
    runner.generation.evaluate.return_value = PipelineResult(
        status="no_reply", reason="completed_exchange",
        reason_code="completed_exchange", model_call_count=1,
    )
    continuation = runner.run(state)
    assert continuation == ContinueNormalReplyPass(1, True)
    runner.get_hot_post_reply_candidates.assert_called_once_with(state)

    next_runner, _new_state, _next_candidate, _next_prepared, _next_reply, _next_confirmed = normal_case(
        candidates=[mention(106, 206), mention(107, 207)], maximum_fresh=2,
    )
    next_runner.generation.evaluate.return_value = PipelineResult(
        status="no_reply", reason="completed_exchange",
        reason_code="completed_exchange", model_call_count=1,
    )
    assert next_runner.run(
        state, _fresh_mention_ai_evaluations=continuation.fresh_evaluations,
        _skip_hot_post_fetch=continuation.skip_hot_post_fetch,
    ) == "checked"
    next_runner.get_hot_post_reply_candidates.assert_not_called()
    next_runner.generation.evaluate.assert_called_once()
    next_runner.delivery.deliver.assert_not_called()


@pytest.mark.parametrize("lane", ["normal", "quote"])
@pytest.mark.parametrize("gate,expected", [
    ("cap", "skipped_cap"), ("spacing", "skipped_spacing"),
    ("cooldown", "skipped_cooldown"), ("pause", "disabled"),
])
def test_current_limits_stop_before_candidate_evaluation(lane, gate, expected):
    runner, state, _candidate, _prepared, _reply, _confirmed = (
        normal_case() if lane == "normal" else quote_case()
    )
    if gate == "cap":
        state["daily_reply_count"] = 10
    elif gate == "spacing":
        state["last_reply_epoch"] = 2_000_000_000
        from dataclasses import replace
        runner.config = replace(runner.config, minimum_reply_spacing=60)
    elif gate == "cooldown":
        runner.cooldowns.active.return_value = True
    else:
        runner.controls.lane_paused.return_value = True
    assert runner.run(state) == expected
    runner.generation.evaluate.assert_not_called()
    runner.delivery.deliver.assert_not_called()
