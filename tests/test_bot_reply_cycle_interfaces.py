from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from unittest.mock import Mock

import pytest

from mrs_bot_reply_cycle_interfaces import (
    FinishReplyCheck, PreparedReplyContext, SkipReplyCandidate,
)
import mrs_bot_normal_reply_cycle as normal_cycle
import mrs_bot_quote_reply_cycle as quote_cycle
import mrs_bot_reply_generation as generation
from single_call_reply import PipelineResult
from tests.helpers.reply_fixtures import configure_normal_cycle as configure_normal
from tests.helpers.reply_fixtures import configure_quote_cycle as configure_quote
from tests.helpers.mention_fixtures import mention
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401
from tests.helpers.reply_fixtures import (
    patch_reply_draft_method,
    patch_reply_context_method,
    patch_reply_owner_method,
    unit_approved_reply,
    unit_reply_context,
)


def test_candidate_skip_and_finished_check_have_distinct_contracts():
    skipped = SkipReplyCandidate()
    finished = FinishReplyCheck("checked")
    assert not hasattr(skipped, "status")
    assert finished.status == "checked"
    with pytest.raises(TypeError):
        FinishReplyCheck()
    with pytest.raises(FrozenInstanceError):
        finished.status = "posted"


def prepare_cycle(monkeypatch, lane):
    state = bot.default_state()
    if lane == "quote_tweet":
        original, quotes = configure_quote(monkeypatch)
        prepared_context = bot._reply_context_owner().build_quote(original, quotes[0])
        assert prepared_context is not None
        context = prepared_context.context
        return state, context, bot.maybe_reply_to_quote_tweets
    configure_normal(monkeypatch)
    candidate = mention(105, 205)
    monkeypatch.setattr(bot._mention_discovery, "get_mentions", Mock(return_value=[candidate]))
    context = unit_reply_context(
        target_id=candidate["id"], contribution=candidate["text"],
        target_author_id=candidate["author_id"],
    )
    return state, context, bot.maybe_reply_to_mentions


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
def test_cycle_boundaries_capture_current_callbacks_and_config_between_calls(monkeypatch, lane):
    module = normal_cycle if lane == "mention" else quote_cycle
    name = "maybe_reply_to_mentions" if lane == "mention" else "maybe_reply_to_quote_tweets"
    adapter = getattr(bot, name)
    result = object()
    owner = Mock(return_value=result)
    monkeypatch.setattr(module, name, owner)
    draft_owner_factory = bot._reply_draft_owner
    draft_owners = []

    def make_drafts(**kwargs):
        drafts = draft_owner_factory(**kwargs)
        draft_owners.append(drafts)
        return drafts

    factory = Mock(side_effect=make_drafts)
    history_factory = Mock(wraps=bot._reply_history_owner)
    monkeypatch.setattr(bot, "_reply_draft_owner", factory)
    monkeypatch.setattr(bot, "_reply_history_owner", history_factory)
    state = {}
    snapshots = []
    histories = []
    for index in range(2):
        save, evidence, post, clock = Mock(), Mock(), Mock(), Mock()
        monkeypatch.setattr(bot, "save_state", save)
        monkeypatch.setattr(bot, "reply_evidence_repository", evidence)
        monkeypatch.setattr(bot, "post_conversational_reply_with_durable_identity", post)
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", bool(index))
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 20 + index)
        monkeypatch.setattr(bot, "now_epoch", clock)
        monkeypatch.setattr(bot, "MAX_RECENT_ACCOUNT_REPLIES", 10 + index)
        assert adapter(state) is result
        assert owner.call_args.args[0] is state
        supplied = owner.call_args.kwargs
        config, persistence, delivery = (supplied[key] for key in ("config", "persistence", "delivery"))
        assert config.enabled is bool(index)
        assert config.maximum_daily_replies == 20 + index
        assert factory.call_count == index + 1
        assert history_factory.call_count == index + 1
        history = supplied["history"]
        assert isinstance(history, bot._reply_history.ReplyHistory)
        generation_owner = supplied["generation"]
        assert generation_owner.cooldowns is supplied["cooldowns"]
        assert generation_owner.cooldowns.now_epoch is clock
        assert generation_owner.evidence_repository is evidence
        generation_history = generation_owner.history
        assert generation_history.now_epoch is clock
        assert generation_history.maximum_recent_replies == 10 + index
        assert supplied["tweets"].now_epoch is clock
        assert supplied["tweets"].save_state is save
        assert history.now_epoch is clock
        assert history.maximum_recent_replies == 10 + index
        clock.assert_not_called()
        histories.append(history)
        drafts = draft_owners[-1]
        assert drafts.history is history
        assert drafts.generation is generation_owner
        assert drafts.history.now_epoch is clock
        assert drafts.history.maximum_recent_replies == 10 + index
        assert persistence.save is save
        for method in ("recover", "store", "clear", "retire_ineligible"):
            bound_method = getattr(persistence, method)
            assert bound_method.__self__ is drafts
            assert bound_method.__func__ is getattr(type(drafts), method)
        assert drafts.evidence_repository is evidence
        evidence.assert_not_called()
        assert delivery.post is bot._post_conversational_reply_with_current_owners
        assert delivery.cooldowns is supplied["cooldowns"]
        assert delivery.completion.apply_state.func is bot._reply_reconciliation.apply_confirmed_reply_receipt
        assert delivery.receipt_values.drafts is drafts
        assert delivery.tweets is supplied["tweets"]
        post.assert_not_called()
        with pytest.raises(FrozenInstanceError):
            persistence.save = Mock()
        snapshots.append((persistence, save, evidence))
    assert snapshots[0][0].save is snapshots[0][1]
    assert snapshots[0][0].save is not snapshots[1][0].save
    assert draft_owners[0] is not draft_owners[1]
    assert draft_owners[0].evidence_repository is snapshots[0][2]
    assert draft_owners[0].evidence_repository is not draft_owners[1].evidence_repository
    assert histories[0] is not histories[1]
    assert histories[0].now_epoch is not histories[1].now_epoch
    assert state == {}


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
@pytest.mark.parametrize("decision", ["reply", "recovered", "no_reply", "zero_call_failure"])
def test_cycles_consume_typed_results_through_durable_outcomes(monkeypatch, lane, decision):
    state, context, run = prepare_cycle(monkeypatch, lane)
    target = str(context["target_id"])
    builder = (
        bot._reply_context_owner().build if lane == "mention"
        else bot._reply_context_owner().build_quote
    )
    prepared_results = []

    def prepare(*args, **kwargs):
        prepared = builder(*args, **kwargs)
        prepared_results.append(prepared)
        return prepared

    if lane == "quote_tweet":
        patch_reply_context_method(monkeypatch, "build_quote", prepare)
    else:
        patch_reply_context_method(monkeypatch, "build", prepare)
    bot.reply_media_context_for_candidate.reset_mock()
    if decision == "recovered":
        reply = unit_approved_reply(context)
        assert bot.store_pending_ai_reply(state, target, lane, reply, context=context)

    def evaluate(actual_context, media, *, state: dict):
        assert decision != "recovered", "Recovered drafts must not call the model"
        assert actual_context is prepared_results[0].context
        assert media is prepared_results[0].media_context
        assert "_prepared_media_context" not in actual_context
        if decision == "reply":
            result = PipelineResult(
                status="reply", reason="useful_reply", model_call_count=1,
                reply=unit_approved_reply(actual_context),
            )
        elif decision == "no_reply":
            result = PipelineResult(
                status="no_reply", reason="completed_exchange",
                reason_code="completed_exchange", model_call_count=1,
            )
        else:
            result = PipelineResult(
                status="operational_failure", reason="material_image_unavailable",
                error_category="image_input", model_call_count=0,
            )
        return result

    evaluator = Mock(side_effect=evaluate)
    patch_reply_owner_method(monkeypatch, generation.ReplyGeneration, "evaluate", evaluator)
    monkeypatch.setattr(bot, "generate_single_call_reply", Mock(side_effect=AssertionError("legacy evaluator used")))
    monkeypatch.setattr(bot, "pending_ai_reply", Mock(side_effect=AssertionError("legacy recovery used")))
    patch_reply_owner_method(
        monkeypatch, bot._tweet_lookup_cache.TweetLookupCache,
        "target_is_available", Mock(return_value=True),
    )
    monkeypatch.setattr(
        bot, "reply_target_is_available_immediately_before_send",
        Mock(side_effect=AssertionError("obsolete availability relay used")),
    )
    recovery = Mock(wraps=bot._reply_draft_owner().recover)
    patch_reply_draft_method(monkeypatch, "recover", recovery)
    for name in (
        "recover_pending_ai_reply", "store_pending_ai_reply", "validate_current_ai_reply_draft",
        "recovery_comparison_account_replies",
    ):
        monkeypatch.setattr(bot, name, Mock(side_effect=AssertionError("reply owner used root adapter")))

    def post(**kwargs):
        assert kwargs["state"] is state
        persisted = json.loads(bot.STATE_FILE.read_text())
        assert f"{lane}:{target}" in persisted["pending_ai_reply_drafts"]
        assert kwargs["receipt_template"]["reply_context"] == prepared_results[0].context
        assert "_prepared_media_context" not in kwargs["receipt_template"]["reply_context"]
        receipt = bot._confirmed_reply_receipt_from_sending(
            kwargs["receipt_template"], reply_post_id="990",
            confirmation_epoch=bot.now_epoch(),
        )
        bot.write_confirmed_reply_receipt(receipt)
        return {}, receipt

    send = Mock(side_effect=post)
    monkeypatch.setattr(bot, "_post_conversational_reply_with_current_owners", send)
    monkeypatch.setattr(
        bot, "post_conversational_reply_with_durable_identity",
        Mock(side_effect=AssertionError("obsolete delivery relay used")),
    )
    status = run(state)
    persisted = json.loads(bot.STATE_FILE.read_text())
    if decision in {"reply", "recovered"}:
        assert status == (bot.NORMAL_CHECK_STATUS_POSTED if lane == "mention" else bot.QUOTE_CHECK_STATUS_POSTED)
        assert send.call_count == 1
        assert persisted["daily_reply_count"] == 1
        assert persisted["daily_quote_reply_count"] == int(lane == "quote_tweet")
        history_key = "replied_to_ids" if lane == "mention" else "replied_to_quote_post_ids"
        assert persisted[history_key] == [target]
        assert not persisted.get("pending_ai_reply_drafts")
        assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
    else:
        assert status == (bot.NORMAL_CHECK_STATUS_CHECKED if lane == "mention" else bot.QUOTE_CHECK_STATUS_CHECKED)
        assert persisted["reply_evaluation_records"][target]["outcome"] == (
            "no_reply" if decision == "no_reply" else "operational_failure"
        )
        assert persisted["daily_reply_count"] == persisted["daily_quote_reply_count"] == 0
        send.assert_not_called()
    assert len(prepared_results) == 1
    assert "_prepared_media_context" not in prepared_results[0].context
    assert "_prepared_media_context" not in json.dumps(persisted)
    assert bot.reply_media_context_for_candidate.call_count == int(lane == "quote_tweet")
    assert evaluator.call_count == int(decision != "recovered")
    assert recovery.call_count == 1
    assert state["openai_error_epochs"] == []
    bot.create_post.assert_not_called()
    bot.x_request.assert_not_called()


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
def test_zero_call_result_preserves_each_lanes_budget_rule(monkeypatch, lane):
    state, _context, run = prepare_cycle(monkeypatch, lane)
    if lane == "mention":
        monkeypatch.setattr(
            bot._mention_discovery, "get_mentions",
            Mock(return_value=[mention(n, n + 100) for n in (105, 106, 107)]),
        )
        monkeypatch.setattr(bot, "MAX_MENTIONS_PER_CHECK", 1)
    else:
        quotes = bot.get_quote_tweets_for_posts.return_value["900"]
        quotes[:] = [dict(quotes[0], id=str(n), conversation_id=str(n)) for n in (105, 106, 107)]
        monkeypatch.setattr(bot, "MAX_QUOTE_POSTS_PER_CHECK", 1)
    calls = []

    def evaluate(context, media, *, state):
        calls.append(context["target_id"])
        if context["target_id"] == "105":
            return PipelineResult(
                status="operational_failure", reason="material_image_unavailable",
                error_category="image_input", model_call_count=0,
            )
        return PipelineResult(
            status="no_reply", reason="completed_exchange", model_call_count=1,
        )

    patch_reply_owner_method(monkeypatch, generation.ReplyGeneration, "evaluate", evaluate)
    run(state)
    assert calls == (["105", "106"] if lane == "mention" else ["105"])
    assert state["reply_evaluation_records"]["105"]["outcome"] == "operational_failure"
    assert "107" not in state["reply_evaluation_records"]
    assert state["daily_reply_count"] == 0


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
def test_typed_local_failure_keeps_prior_429_cooldown_and_terminal_retirement(monkeypatch, lane):
    evaluate = generation.ReplyGeneration.evaluate
    state, context, run = prepare_cycle(monkeypatch, lane)
    current = bot.now_epoch()
    result = PipelineResult(
        status="operational_failure", reason="local_reply_validation_failed",
        error_category="local_validation", model_call_count=1,
        provider_status_code=429, provider_reset_epoch=current + 120,
        provider_retry_after_seconds=120, provider_request_attempt_count=2,
    )
    monkeypatch.setattr(generation.ReplyGeneration, "evaluate", evaluate)
    patch_reply_owner_method(
        monkeypatch, bot._reply_native_media.ReplyMedia, "collect", lambda _media: [],
    )
    monkeypatch.setattr(bot, "run_single_call_reply_pipeline", Mock(return_value=result))
    recorded = Mock(wraps=bot._reply_generation_owner().record_result)
    patch_reply_owner_method(monkeypatch, generation.ReplyGeneration, "record_result", recorded)
    run(state)
    target = str(context["target_id"])
    assert recorded.call_args.args[0] is result
    assert state["reply_evaluation_records"][target]["outcome"] == "operational_failure"
    assert state["openai_error_epochs"] == [current]
    assert state["openai_api_cooldown_until_epoch"] == current + 180
    assert state["daily_reply_count"] == state["daily_quote_reply_count"] == 0
    saved = json.loads(bot.STATE_FILE.read_text())
    assert saved["reply_evaluation_records"][target]["outcome"] == "operational_failure"
    assert saved["openai_api_cooldown_until_epoch"] == current + 180
    bot.create_post.assert_not_called()


def test_recovery_distinguishes_absent_obsolete_invalid_and_recovered_drafts(monkeypatch):
    context = unit_reply_context()
    validate = Mock(wraps=bot._reply_draft_owner().validate)
    patch_reply_draft_method(monkeypatch, "validate", validate)
    assert bot.recover_pending_ai_reply({}, "100", "mention", context=context) is None
    validate.assert_not_called()
    obsolete_state = {"pending_ai_reply_drafts": {"mention:100": {"schema_version": 0}}}
    discarded = bot.recover_pending_ai_reply(obsolete_state, "100", "mention", context=context)
    assert discarded.status == "draft_discarded" and discarded.model_call_count == 0
    assert obsolete_state == {}
    reply = unit_approved_reply(context)
    state = {}
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context)
    recovered = bot.recover_pending_ai_reply(state, "100", "mention", context=context)
    assert recovered.status == "reply" and recovered.reply == reply
    assert recovered.model_call_count == 0
    invalid = bot.recover_pending_ai_reply(
        state, "100", "mention", context=context, recent_replies=[str(reply)],
    )
    assert invalid.status == "operational_failure" and invalid.error_category == "local_validation"
    assert invalid.model_call_count == 0 and state == {}


@pytest.mark.parametrize("quote_reply", [False, True])
@pytest.mark.parametrize("failed_step", [None, "apply", "save", "retire", "remove"])
def test_shared_finaliser_preserves_order_identity_and_exception_boundaries(monkeypatch, quote_reply, failed_step):
    state, receipt = {}, {"reply_post_id": "990"}
    trace = []
    failure = RuntimeError("local transaction failed")

    def callback(step):
        def invoke(*args, **kwargs):
            trace.append(step)
            if step == "apply":
                assert args[0] is state and args[1] is receipt
            elif step == "save":
                assert args[0] is state and kwargs == {"durable": True}
            elif step == "retire":
                assert kwargs["receipt"] is receipt
                assert kwargs["post_id"] == "990" and kwargs["lane"] == "conversational_reply"
            else:
                assert args[0] is receipt
            if step == failed_step:
                raise failure
        return invoke

    monkeypatch.setattr(
        bot, "_confirmed_reply_state_applier", Mock(return_value=callback("apply")),
    )
    monkeypatch.setattr(
        bot, "apply_confirmed_reply_receipt",
        Mock(side_effect=AssertionError("obsolete application relay used")),
    )
    for name, step in (
        ("save_state", "save"),
        ("retire_lane_transport_journal_if_present", "retire"), ("remove_confirmed_reply_receipt", "remove"),
    ):
        monkeypatch.setattr(bot, name, callback(step))
    if failed_step is None:
        assert bot.finalise_confirmed_reply(state, receipt, target_id="100", quote_reply=quote_reply) == "990"
    else:
        expected = bot.ConfirmedReplyLocalPersistenceError if failed_step in {"retire", "remove"} else RuntimeError
        with pytest.raises(expected) as caught:
            bot.finalise_confirmed_reply(state, receipt, target_id="100", quote_reply=quote_reply)
        if failed_step in {"apply", "save"}:
            assert caught.value is failure
        else:
            assert caught.value.__cause__ is failure
    steps = ["apply", "save", "retire", "remove"]
    assert trace == (steps if failed_step is None else steps[:steps.index(failed_step) + 1])


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
def test_explicit_absent_media_does_not_trigger_fallback_collection(monkeypatch, lane):
    state, context, run = prepare_cycle(monkeypatch, lane)
    prepared = PreparedReplyContext(context, None)
    builder = Mock(return_value=prepared)
    if lane == "quote_tweet":
        patch_reply_context_method(monkeypatch, "build_quote", builder)
    else:
        patch_reply_context_method(monkeypatch, "build", builder)
    collect_media = Mock(side_effect=AssertionError("Prepared media must not be collected again"))
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", collect_media)
    evaluator = Mock(return_value=PipelineResult(
        status="no_reply", reason="completed_exchange",
        reason_code="completed_exchange", model_call_count=1,
    ))
    patch_reply_owner_method(monkeypatch, generation.ReplyGeneration, "evaluate", evaluator)

    assert run(state) == (
        bot.NORMAL_CHECK_STATUS_CHECKED if lane == "mention"
        else bot.QUOTE_CHECK_STATUS_CHECKED
    )

    assert evaluator.call_args.args[0] is context
    assert evaluator.call_args.args[1] is None
    assert "_prepared_media_context" not in context
    collect_media.assert_not_called()
