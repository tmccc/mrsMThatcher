from __future__ import annotations

from tests.helpers.reply_evaluation import legacy_reply_evaluator

import copy
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

import mrs_bot_reply_assembly as assembly

import pytest

import mrs_bot_normal_reply_cycle as cycle
import mrs_bot_api_cooldowns as api_cooldowns
import mrs_bot_mention_discovery as mention_discovery
import mrs_bot_author_quarantines as quarantine_owner
import mrs_bot_daily_reply_accounting as accounting_owner
import mrs_bot_reply_context as context_owner
import mrs_bot_reply_cycle_interfaces as interfaces
from mrs_bot_reply_clarifications import ClarificationReplies
import mrs_bot_reply_evaluation_state as evaluation_state
import mrs_bot_reply_drafts as reply_drafts
import mrs_bot_reply_generation as generation_owner
import mrs_bot_reply_model_transport as model_transport_owner
import mrs_bot_reply_native_media as media_owner
import mrs_bot_runtime_control as runtime_control
from tests.helpers.mention_fixtures import (
    editorial_no_reply,
    mention,
    queue_active_mention,
)
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import (
    install_receipt_bound_x_request_stub,
    isolate_bot_runtime,  # noqa: F401
)
from tests.helpers.reply_fixtures import (
    configure_normal_cycle as _configure_cycle,
    patch_reply_owner_method,
    patch_reply_draft_method,
    patch_reply_history_method,
    patch_reply_context_method,
    unit_approved_reply,
    unit_confirmed_v4_reply_receipt,
    unit_reply_context,
)


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, dataclasses, json, io, logging, os, random, socket, sys
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('normal reply cycle import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_author_quarantines', 'mrs_bot_daily_reply_accounting', 'mrs_bot_durable_json_io', 'mrs_bot_mention_authority', 'mrs_bot_normal_reply_cycle', 'mrs_bot_receipt_primitives', 'mrs_bot_reply_context', 'mrs_bot_reply_cycle_interfaces', 'mrs_bot_reply_delivery', 'mrs_bot_reply_drafts', 'mrs_bot_reply_evaluation_state', 'mrs_bot_reply_generation', 'mrs_bot_reply_history', 'mrs_bot_reply_native_media', 'mrs_bot_reply_preparation', 'mrs_bot_reply_state', 'mrs_bot_request_route_values', 'mrs_bot_runtime_state_helpers', 'mrs_bot_state_value_normalisation', 'mrs_bot_tweet_lookup_cache'}:
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_normal_reply_cycle
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
assert 'single_call_reply' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_scheduler_entry_uses_a_fresh_runner_and_preserves_state_identity(monkeypatch):
    _configure_cycle(monkeypatch)
    state = bot.default_state()
    monkeypatch.setattr(mention_discovery, "get_mentions", Mock(return_value=[]))
    seen = []
    original = assembly.ReplyAssembly.normal_runner

    def build(self):
        runner = original(self)
        seen.append(runner)
        return runner

    monkeypatch.setattr(assembly.ReplyAssembly, "normal_runner", build)
    assert bot.maybe_reply_to_mentions(state) == interfaces.NORMAL_CHECK_STATUS_CHECKED
    assert bot.maybe_reply_to_mentions(state) == interfaces.NORMAL_CHECK_STATUS_CHECKED
    assert len(seen) == 2 and seen[0] is not seen[1]
    assert seen[0].generation.history is seen[0].history
    assert seen[0].delivery.cooldowns is seen[0].cooldowns
    assert seen[0].delivery.tweets is seen[0].tweets



def test_discovery_assembly_reuses_cycle_owner_identity(monkeypatch):
    """Discovery callbacks share the exact owners constructed for one cycle."""
    tweets = object()
    mention_queue = object()
    reply_evaluations = object()
    cooldowns = object()
    controls = object()
    drafts = Mock()
    watch_posts = object()
    watch_owner = Mock(return_value=watch_posts)
    monkeypatch.setattr(assembly.ReplyAssembly, "_quote_watch_posts_owner", watch_owner)

    mention_callback = bot._reply_assembly()._mention_discovery_callback(
        tweets=tweets,
        mention_queue=mention_queue,
        reply_evaluations=reply_evaluations,
    )
    hot_post_callback = bot._reply_assembly()._hot_post_discovery_callback(
        tweets=tweets,
        drafts=drafts,
        cooldowns=cooldowns,
        controls=controls,
        reply_evaluations=reply_evaluations,
    )

    assert mention_callback.func is mention_discovery.get_mentions
    assert mention_callback.keywords["tweets"] is tweets
    assert mention_callback.keywords["mention_queue"] is mention_queue
    assert mention_callback.keywords["reply_evaluations"] is reply_evaluations
    assert hot_post_callback.func is bot._hot_post_discovery.get_hot_post_reply_candidates
    assert hot_post_callback.keywords["tweets"] is tweets
    assert hot_post_callback.keywords["retire_ineligible_draft"] is drafts.retire_ineligible
    assert hot_post_callback.keywords["cooldowns"] is cooldowns
    assert hot_post_callback.keywords["controls"] is controls
    assert hot_post_callback.keywords["reply_evaluations"] is reply_evaluations
    assert hot_post_callback.keywords["watch_posts"] is watch_posts
    watch_owner.assert_called_once_with(tweets=tweets)


def test_fixed_statuses_and_dependency_free_helpers_use_their_owners():
    statuses = {
        "NORMAL_CHECK_STATUS_CHECKED": "checked",
        "NORMAL_CHECK_STATUS_POSTED": "posted",
        "NORMAL_CHECK_STATUS_SKIPPED_SPACING": "skipped_spacing",
        "NORMAL_CHECK_STATUS_SKIPPED_CAP": "skipped_cap",
        "NORMAL_CHECK_STATUS_SKIPPED_COOLDOWN": "skipped_cooldown",
        "NORMAL_CHECK_STATUS_DISABLED": "disabled",
        "NORMAL_CHECK_STATUS_API_ERROR": "api_error",
    }
    for name, expected in statuses.items():
        value = getattr(interfaces, name)
        assert value == expected
        assert getattr(cycle, name) is value
        assert getattr(bot, name) is value
    for name, owner in (
        ("completed_mention_watermark_covers_target", evaluation_state),
        ("terminal_reply_evaluation", evaluation_state),
        ("clear_author_evaluation_quarantine_history", quarantine_owner),
        ("daily_author_reply_counts", accounting_owner),
        ("trim_context_text", context_owner),
    ):
        assert getattr(cycle, name) is getattr(owner, name)
        assert getattr(bot, name) is getattr(owner, name)


def test_cycle_hands_cooldown_and_control_work_to_typed_owners(monkeypatch):
    _configure_cycle(monkeypatch)
    state = bot.default_state()
    failure = bot.ApiError("mention lookup failed", service="x", status_code=503)
    monkeypatch.setattr(
        mention_discovery, "get_mentions", Mock(side_effect=failure),
    )
    active = Mock(return_value=False)
    paused = Mock(return_value=False)
    record = Mock()
    patch_reply_owner_method(monkeypatch, api_cooldowns.ApiCooldowns, "active", active)
    patch_reply_owner_method(monkeypatch, api_cooldowns.ApiCooldowns, "record_error", record)
    patch_reply_owner_method(monkeypatch, runtime_control.RuntimeControls, "lane_paused", paused)
    for relay in ("in_api_cooldown", "record_api_error", "lane_paused"):
        monkeypatch.setattr(
            bot, relay, Mock(side_effect=AssertionError(f"obsolete root relay used: {relay}")),
        )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_API_ERROR

    paused.assert_called_once_with("disable_replies", "disable_normal_replies")
    assert active.call_args_list == [
        call(state), call(state, scope="write"), call(state, scope="openai"),
    ]
    record.assert_called_once_with(state, failure, "x")


@pytest.mark.parametrize("initial_count,model_calls", [(2, 1), (2, 0), (4, 1)])
def test_backlog_continuation_preserves_budget_and_skips_hot_fetch(monkeypatch, initial_count, model_calls):
    _configure_cycle(monkeypatch)
    state = bot.default_state()
    queue_active_mention(state, mention(105, 205), base_since_id="99")

    def generate(context, *args, evaluation_outcome, **kwargs):
        editorial_no_reply(context, *args, evaluation_outcome=evaluation_outcome, **kwargs)
        evaluation_outcome["model_call_count"] = model_calls

    patch_reply_owner_method(
        monkeypatch, generation_owner.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(generate),
    )
    runner = bot._reply_assembly().normal_runner()
    result = runner.run(state, _fresh_mention_ai_evaluations=initial_count)
    if initial_count + model_calls < bot.MAX_MENTIONS_PER_CHECK:
        assert result == cycle.ContinueNormalReplyPass(initial_count + model_calls, True)
    else:
        assert result == interfaces.NORMAL_CHECK_STATUS_CHECKED
    saved = json.loads(bot.STATE_FILE.read_text())
    assert saved["mention_pending_candidates"] == {}
    assert saved["mention_backlog"] == state["mention_backlog"]
    assert saved["reply_evaluation_records"]["105"]["outcome"] == "no_reply"
    assert state["last_seen_mention_id"] == "99"
    bot._hot_post_discovery.get_hot_post_reply_candidates.assert_called_once()
    bot.x_request.assert_not_called()
    bot.create_post.assert_not_called()


def test_assembly_continuation_rebuilds_runner_with_same_state_and_budget(monkeypatch):
    state = {}
    first = Mock()
    second = Mock()
    first.run.return_value = cycle.ContinueNormalReplyPass(3, True)
    second.run.return_value = interfaces.NORMAL_CHECK_STATUS_CHECKED
    build = Mock(side_effect=[first, second])
    monkeypatch.setattr(assembly.ReplyAssembly, "normal_runner", build)
    assert bot._reply_assembly().run_normal(state) == interfaces.NORMAL_CHECK_STATUS_CHECKED
    assert first.run.call_args.args[0] is state
    assert second.run.call_args.args[0] is state
    assert first.run.call_args.kwargs == {
        "_fresh_mention_ai_evaluations": 0, "_skip_hot_post_fetch": False,
    }
    assert second.run.call_args.kwargs == {
        "_fresh_mention_ai_evaluations": 3, "_skip_hot_post_fetch": True,
    }
    assert build.call_count == 2



@pytest.mark.parametrize("callback", ["recovery_comparison_account_replies", "recover_pending_ai_reply"])
def test_draft_recovery_errors_propagate_before_generation(monkeypatch, callback):
    evaluate = _configure_cycle(monkeypatch)
    state = bot.default_state()
    queue_active_mention(state, mention(105, 205), base_since_id="99")
    failure = RuntimeError("draft recovery failed locally")
    if callback == "recover_pending_ai_reply":
        patch_reply_draft_method(monkeypatch, "recover", Mock(side_effect=failure))
    else:
        patch_reply_history_method(monkeypatch, "recovery_replies", Mock(side_effect=failure))
    saved = Mock(wraps=bot.save_state)
    accounted = Mock()
    monkeypatch.setattr(bot, "save_state", saved)
    patch_reply_owner_method(
        monkeypatch, api_cooldowns.ApiCooldowns, "record_error", accounted,
    )

    with pytest.raises(RuntimeError) as caught:
        bot.maybe_reply_to_mentions(state)

    assert caught.value is failure
    assert "105" in state["mention_pending_candidates"]
    assert state["daily_reply_count"] == 0
    assert state.get("reply_evaluation_records", {}) == {}
    saved.assert_not_called()
    accounted.assert_not_called()
    evaluate.assert_not_called()
    bot.x_request.assert_not_called()
    bot.create_post.assert_not_called()


@pytest.mark.parametrize("boundary", ["attempt_binding", "confirmed_state"])
def test_receipt_preparation_and_confirmed_state_errors_are_not_transport_errors(
    monkeypatch, boundary,
):
    _configure_cycle(monkeypatch)
    state = bot.default_state()
    queue_active_mention(state, mention(105, 205), base_since_id="99")
    patch_reply_owner_method(
        monkeypatch, generation_owner.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(lambda context, *_args, **_kwargs: unit_approved_reply(context)),
    )
    failure = RuntimeError("receipt boundary failed locally")
    if boundary == "attempt_binding":
        patch_reply_owner_method(
            monkeypatch, bot._reply_receipt_values.ReplyReceiptValues,
            "bind_attempt", Mock(side_effect=failure),
        )
    else:
        monkeypatch.setattr(
            assembly.ReplyAssembly, "_confirmed_reply_state_applier",
            Mock(return_value=Mock(side_effect=failure)),
        )
    transport = Mock(return_value=({}, {"reply_post_id": "900"}))
    monkeypatch.setattr(assembly.ReplyAssembly, "post_with_current_owners", transport)
    accounted = Mock()
    outcomes = Mock(wraps=bot.log_ai_reply_posting_outcome)
    cleanup = Mock(wraps=bot.remove_confirmed_reply_receipt)
    patch_reply_owner_method(
        monkeypatch, api_cooldowns.ApiCooldowns, "record_error", accounted,
    )
    monkeypatch.setattr(bot, "log_ai_reply_posting_outcome", outcomes)
    monkeypatch.setattr(bot, "remove_confirmed_reply_receipt", cleanup)

    with pytest.raises(RuntimeError) as caught:
        bot.maybe_reply_to_mentions(state)

    assert caught.value is failure
    saved = json.loads(bot.STATE_FILE.read_text())
    assert "mention:105" in saved["pending_ai_reply_drafts"]
    assert "105" in saved["mention_pending_candidates"]
    assert saved["daily_reply_count"] == state["daily_reply_count"] == 0
    assert transport.call_count == int(boundary == "confirmed_state")
    accounted.assert_not_called()
    outcomes.assert_not_called()
    cleanup.assert_not_called()
    bot.x_request.assert_not_called()
    bot.create_post.assert_not_called()


def test_legacy_quote_only_target_is_retired_before_normal_eligibility(monkeypatch):
    evaluate = _configure_cycle(monkeypatch)
    state = bot.default_state()
    normal_ledger = state["replied_to_ids"]
    quote_ledger = state["replied_to_quote_post_ids"] = ["105"]
    candidate = mention(105, 205)
    monkeypatch.setattr(
        mention_discovery, "get_mentions", Mock(return_value=[candidate]),
    )
    eligible = Mock(side_effect=AssertionError("handled target reached eligibility"))
    context = Mock(side_effect=AssertionError("handled target reached context"))
    monkeypatch.setattr(bot, "reply_target_is_directly_eligible", eligible)
    patch_reply_context_method(monkeypatch, "build", context)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED

    eligible.assert_not_called()
    context.assert_not_called()
    evaluate.assert_not_called()
    bot.x_request.assert_not_called()
    bot.create_post.assert_not_called()
    assert state["replied_to_ids"] is normal_ledger and normal_ledger == []
    assert state["replied_to_quote_post_ids"] is quote_ledger and quote_ledger == ["105"]
    assert state["last_seen_mention_id"] == "105"
    assert state["daily_reply_count"] == state["daily_quote_reply_count"] == 0


def test_ineligible_mention_draft_retirement_precedes_seen_marker_and_durable_save(monkeypatch):
    evaluate = _configure_cycle(monkeypatch)
    state = bot.default_state()
    candidate = mention(105, 205)
    candidate["entities"] = {"mentions": []}
    queue_active_mention(state, candidate, base_since_id="99")
    state["pending_ai_reply_drafts"] = {"mention:105": {
        "strategy_version": "stored-strategy", "reply_kind": "direct_reply",
        "reason_code": "answer_question", "validated_draft_hash": "stored-hash",
    }}
    key = Mock(wraps=reply_drafts.pending_ai_reply_draft_key)
    monkeypatch.setattr(reply_drafts, "pending_ai_reply_draft_key", key)
    trace = []

    for label, name in (
        ("clear", "clear_pending_ai_reply"),
        ("terminal", "record_terminal_reply_evaluation"),
        ("mark", "maybe_mark_hot_post_reply_skipped"),
        ("seen", "mark_mention_seen_if_applicable"),
        ("save", "save_state"),
    ):
        original = (
            bot._reply_assembly()._reply_draft_owner().clear if label == "clear"
            else bot._reply_assembly()._reply_evaluation_owner().record if label == "terminal"
            else bot._reply_assembly()._mention_queue_owner().mark_seen if label == "seen"
            else getattr(bot, name)
        )

        def observe(*args, _label=label, _original=original, **kwargs):
            trace.append((_label, kwargs.get("durable")))
            return _original(*args, **kwargs)

        if label == "clear":
            patch_reply_draft_method(monkeypatch, "clear", observe)
        elif label == "terminal":
            patch_reply_owner_method(monkeypatch, evaluation_state.ReplyEvaluations, "record", observe)
        elif label == "seen":
            patch_reply_owner_method(monkeypatch, mention_discovery.MentionQueue, "mark_seen", observe)
        else:
            monkeypatch.setattr(bot, name, observe)
    monkeypatch.setattr(bot, "log_event", lambda event, **kwargs: trace.append((event, None)))
    context = Mock(side_effect=AssertionError("ineligible target must not build context"))
    patch_reply_context_method(monkeypatch, "build", context)

    assert bot._reply_assembly().normal_runner().run(
        state, _fresh_mention_ai_evaluations=bot.MAX_MENTIONS_PER_CHECK,
    ) == bot.NORMAL_CHECK_STATUS_CHECKED

    assert trace == [
        ("single_call_reply_posting_outcome", None), ("clear", None),
        ("terminal", None), ("mark", None), ("reply_target_terminal", None),
        ("seen", None), ("save", True), ("save", None),
    ]
    assert key.call_args_list == [call("105", "mention"), call("105", "mention")]
    saved = json.loads(bot.STATE_FILE.read_text())
    assert "pending_ai_reply_drafts" not in saved
    assert saved["mention_pending_candidates"] == {}
    assert saved["last_seen_mention_id"] == "99"
    assert saved["reply_evaluation_records"]["105"]["outcome"] == "reply_not_permitted"
    context.assert_not_called()
    evaluate.assert_not_called()
    bot.x_request.assert_not_called()
    bot.create_post.assert_not_called()


@pytest.mark.parametrize("fail_first_save", [False, True])
def test_mixed_quarantine_retirements_are_durable_before_later_context(
    monkeypatch, fail_first_save,
):
    evaluate = _configure_cycle(monkeypatch)
    state = bot.default_state()
    candidates = [mention(105, 205), mention(106, 206), mention(107, 205), mention(108, 208)]
    candidates[1]["entities"] = {"mentions": []}
    queue_active_mention(state, candidates[0], base_since_id="99")
    state["mention_pending_candidates"].update({item["id"]: item for item in candidates[1:]})
    state["mention_backlog"]["highest_mention_id"] = "108"
    for offset in range(3):
        bot.record_qualifying_author_no_reply(state, "205", current_epoch=bot.now_epoch() - 3 + offset)
    original_save = bot.save_state
    snapshots = []
    failure = OSError("retirement save failed")

    def save(actual_state, *, durable=False):
        assert durable is True
        if fail_first_save:
            raise failure
        original_save(actual_state, durable=durable)
        snapshots.append(json.loads(bot.STATE_FILE.read_text()))

    context_failure = RuntimeError("stop at later context")
    context = Mock(side_effect=context_failure)
    monkeypatch.setattr(bot, "save_state", save)
    patch_reply_context_method(monkeypatch, "build", context)

    with pytest.raises((OSError, RuntimeError)) as caught:
        bot.maybe_reply_to_mentions(state)

    if fail_first_save:
        assert caught.value is failure
        context.assert_not_called()
        assert set(state["mention_pending_candidates"]) == {"107", "108"}
        assert snapshots == []
    else:
        assert caught.value is context_failure
        assert [set(item["mention_pending_candidates"]) for item in snapshots] == [
            {"107", "108"}, {"108"},
        ]
        assert set(snapshots[-1]["reply_evaluation_records"]) == {"105", "106", "107"}
        assert snapshots[-1]["reply_evaluation_records"]["106"]["outcome"] == "reply_not_permitted"
        assert snapshots[-1]["last_seen_mention_id"] == "99"
        assert context.call_count == 1
        assert context.call_args.args[0]["id"] == "108"
    assert state["daily_reply_count"] == 0
    evaluate.assert_not_called()
    bot.x_request.assert_not_called()
    bot.create_post.assert_not_called()


def test_daily_reset_and_confirmed_reconciliation_precede_barrier_when_disabled(monkeypatch):
    _configure_cycle(monkeypatch)
    receipt = unit_confirmed_v4_reply_receipt(confirmation_epoch=bot.now_epoch())
    bot._reply_assembly()._reply_receipts_owner().write(receipt, confirmed=True)
    state = bot.default_state()
    state.update(daily_reply_date="2000-01-01", daily_reply_count=48)
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", False)
    trace = Mock()
    for label, name in (
        ("reset", "reset_daily_reply_count_if_needed"),
        ("reconcile", "reconcile_confirmed_reply_receipt"),
    ):
        original = (
            bot._reply_assembly()._daily_reply_accounting_owner().reset
            if label == "reset" else bot._reply_assembly()._reply_completion_owner().reconcile
        )
        callback = Mock(wraps=original)
        trace.attach_mock(callback, label)
        if label == "reset":
            patch_reply_owner_method(monkeypatch, accounting_owner.DailyReplyAccounting, "reset", callback)
        else:
            patch_reply_owner_method(
                monkeypatch, bot._reply_reconciliation.ReplyCompletion, "reconcile", callback,
            )
            monkeypatch.setattr(
                bot, name, Mock(side_effect=AssertionError("obsolete reconciliation relay used")),
            )
    original_barrier = bot._reply_remote_write_barrier()

    def barrier():
        saved = json.loads(bot.STATE_FILE.read_text())
        assert saved["daily_reply_count"] == state["daily_reply_count"] == 1
        assert saved["daily_reply_date"] == receipt["daily_reply_date"]
        assert saved["replied_to_ids"] == [receipt["target_id"]]
        assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
        return original_barrier()

    trace.attach_mock(Mock(side_effect=barrier), "barrier")
    monkeypatch.setattr(bot, "_reply_remote_write_barrier", Mock(return_value=trace.barrier))
    monkeypatch.setattr(
        bot, "block_if_ambiguous_remote_post",
        Mock(side_effect=AssertionError("obsolete reply barrier relay used")),
    )
    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_DISABLED
    assert trace.mock_calls == [call.reset(state), call.reconcile(state), call.barrier()]
    bot._hot_post_discovery.get_hot_post_reply_candidates.assert_not_called()
    bot.x_request.assert_not_called()
    bot.create_post.assert_not_called()


@pytest.mark.parametrize("boundary", ["context", "generation"])
def test_native_context_and_generation_errors_keep_their_distinct_boundaries(monkeypatch, boundary):
    _configure_cycle(monkeypatch)
    state = bot.default_state()
    queue_active_mention(state, mention(105, 205), base_since_id="99")
    failure = RuntimeError("local evaluation failure")
    if boundary == "context":
        patch_reply_context_method(monkeypatch, "build", Mock(side_effect=failure))
    else:
        patch_reply_owner_method(monkeypatch, generation_owner.ReplyGeneration, "evaluate", Mock(side_effect=failure))
    saved = Mock(wraps=bot.save_state)
    accounted = Mock()
    monkeypatch.setattr(bot, "save_state", saved)
    patch_reply_owner_method(
        monkeypatch, api_cooldowns.ApiCooldowns, "record_error", accounted,
    )
    if boundary == "context":
        with pytest.raises(RuntimeError) as caught:
            bot.maybe_reply_to_mentions(state)
        assert caught.value is failure
        saved.assert_not_called()
    else:
        assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_API_ERROR
        saved.assert_called_once_with(state)
        assert "105" in json.loads(bot.STATE_FILE.read_text())["mention_pending_candidates"]
    assert "105" in state["mention_pending_candidates"]
    assert state["daily_reply_count"] == 0
    assert "105" not in state.get("reply_evaluation_records", {})
    accounted.assert_not_called()
    bot.x_request.assert_not_called()
    bot.create_post.assert_not_called()


def test_clarification_refresh_failure_defers_without_losing_candidate(monkeypatch):
    evaluate = _configure_cycle(monkeypatch)
    state = bot.default_state()
    queue_active_mention(state, mention(105, 205), base_since_id="99")
    failure = bot.ApiError("temporary question lookup failure", service="x", status_code=503, request_method="GET", request_path="/2/tweets/100")
    patch_reply_owner_method(monkeypatch, ClarificationReplies, "context", Mock(side_effect=failure))
    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_API_ERROR
    assert "105" in state["mention_pending_candidates"]
    assert "105" in json.loads(bot.STATE_FILE.read_text())["mention_pending_candidates"]
    evaluate.assert_not_called()
    bot.create_post.assert_not_called()


def test_fresh_duplicate_draft_is_rejected_before_delivery(monkeypatch):
    _configure_cycle(monkeypatch)
    state = bot.default_state()
    candidate = mention(105, 205)
    context = unit_reply_context(
        target_id="105", target_author_id="205", contribution=candidate["text"],
    )
    reply = unit_approved_reply(context, text="Responsibility matters more than rhetoric.")
    state["ai_reply_history"] = [{
        "target_id": "104",
        "reply_post_id": "9000",
        "candidate_source": "mention",
        "reply_epoch": bot.now_epoch() - 1,
        "proposed_reply": str(reply),
    }]
    monkeypatch.setattr(
        mention_discovery, "get_mentions", Mock(return_value=[candidate]),
    )
    evaluator = Mock(return_value=bot.PipelineResult(
        status="reply", reason="useful_reply", reply=reply, model_call_count=1,
    ))
    patch_reply_owner_method(monkeypatch, generation_owner.ReplyGeneration, "evaluate", evaluator)
    preflight = Mock()
    patch_reply_owner_method(
        monkeypatch, bot._tweet_lookup_cache.TweetLookupCache,
        "target_is_available", preflight,
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_API_ERROR

    evaluator.assert_called_once()
    assert not state.get("pending_ai_reply_drafts")
    assert state["daily_reply_count"] == 0
    assert "105" not in state.get("reply_evaluation_records", {})
    assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
    preflight.assert_not_called()
    bot.x_request.assert_not_called()
    bot.create_post.assert_not_called()


@pytest.mark.parametrize("recovered", [False, True])
def test_prepared_context_preserves_clarification_and_media_through_recovery(monkeypatch, recovered):
    _configure_cycle(monkeypatch)
    state = bot.default_state()
    candidate = mention(105, 205)
    monkeypatch.setattr(
        mention_discovery, "get_mentions", Mock(return_value=[candidate]),
    )
    monkeypatch.setattr(bot, "REPLY_INCOMING_MAX_CHARS", 40)
    clarification = {
        "question_text": "The original question needs clarification before an answer.",
        "thread_id": "105",
        "prior_bot_reply_id": "104",
        "original_question_id": "103",
        "trigger": "explicit_correction",
    }
    patch_reply_owner_method(
        monkeypatch, ClarificationReplies, "context", Mock(return_value=clarification),
    )
    context = unit_reply_context(
        target_id="105", target_author_id="205", contribution=candidate["text"],
    )
    expected_clarification = {
        "original_question": bot.trim_context_text(clarification["question_text"], 40),
        "correction": candidate["text"],
    }
    draft_context = {**context, "clarification_request": expected_clarification}
    reply = unit_approved_reply(draft_context)
    if recovered:
        assert bot._reply_assembly()._reply_draft_owner().store(state, "105", "mention", reply, context=draft_context)
    media = {"native_media": []}
    prepared = interfaces.PreparedReplyContext(context, media)
    patch_reply_context_method(monkeypatch, "build", Mock(return_value=prepared))
    prepare_context = cycle.NormalReplyCycle._prepare_reply_context

    def prepare(runner, *args, **kwargs):
        result = prepare_context(runner, *args, **kwargs)
        assert result is prepared
        assert result.context is context
        assert result.media_context is media
        return result

    preparation = Mock(side_effect=prepare)
    monkeypatch.setattr(
        cycle.NormalReplyCycle, "_prepare_reply_context",
        lambda runner, *args, **kwargs: preparation(runner, *args, **kwargs),
    )
    recovery = Mock(wraps=bot._reply_assembly()._reply_draft_owner().recover)
    patch_reply_draft_method(monkeypatch, "recover", recovery)
    evaluator = Mock(return_value=bot.PipelineResult(
        status="reply", reason="useful_reply", reply=reply, model_call_count=1,
    ))
    patch_reply_owner_method(monkeypatch, generation_owner.ReplyGeneration, "evaluate", evaluator)
    patch_reply_owner_method(
        monkeypatch, bot._tweet_lookup_cache.TweetLookupCache,
        "target_is_available", Mock(return_value=False),
    )
    monkeypatch.setattr(
        bot, "reply_target_is_available_immediately_before_send",
        Mock(side_effect=AssertionError("obsolete availability relay used")),
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    preparation.assert_called_once()
    assert context["clarification_request"] == expected_clarification
    assert recovery.call_args.kwargs["context"] is context
    if recovered:
        evaluator.assert_not_called()
    else:
        evaluator.assert_called_once()
        assert evaluator.call_args.args[0] is context
        assert evaluator.call_args.args[1] is media
    assert not state.get("pending_ai_reply_drafts")
    assert state["reply_evaluation_records"]["105"]["reason"] == "x_target_unavailable_pre_send"
    assert state["daily_reply_count"] == 0
    bot.x_request.assert_not_called()
    bot.create_post.assert_not_called()


def test_normal_owner_handoffs_keep_current_recovery_and_chronological_model_history(monkeypatch):
    """Exercise real generation-through-completion owners without root relays."""
    build = context_owner.ReplyContext.build
    evaluate = generation_owner.ReplyGeneration.evaluate
    create_post = bot.create_post
    _configure_cycle(monkeypatch)
    monkeypatch.setattr(bot, "create_post", create_post)
    monkeypatch.setattr(bot, "MAX_MENTIONS_PER_CHECK", 1)
    monkeypatch.setattr(generation_owner.ReplyGeneration, "evaluate", evaluate)
    capped, candidate = mention(104, 204), mention(105, 205)
    candidate["created_at"] = "2033-05-18T03:30:00Z"
    state = bot.default_state()
    queue_active_mention(state, candidate, base_since_id="99")
    state["mention_pending_candidates"]["104"] = copy.deepcopy(capped)
    state["daily_reply_date"] = bot.reply_cap_date_str()
    state["daily_replied_author_counts"] = {"204": bot.MAX_REPLIES_PER_AUTHOR_PER_DAY}
    target_epoch = bot.parse_x_datetime_to_epoch(candidate["created_at"])
    state["ai_reply_history"] = [
        {
            "target_id": "801", "reply_post_id": "901", "author_id": "501",
            "candidate_source": "mention", "proposed_reply": "An earlier confirmed reply.",
            "reply_epoch": target_epoch - 100,
        },
        {
            "target_id": "802", "reply_post_id": "902", "author_id": "502",
            "candidate_source": "mention", "proposed_reply": "A later confirmed reply.",
            "reply_epoch": target_epoch + 100,
        },
    ]
    earlier = {"post_id": "901", "text": "An earlier confirmed reply."}
    later = {"post_id": "902", "text": "A later confirmed reply."}
    prepared = []
    collected_media = []
    collect = media_owner.ReplyMedia.collect

    def observe_media(owner, media_context):
        collected_media.append(media_context)
        return collect(owner, media_context)

    monkeypatch.setattr(media_owner.ReplyMedia, "collect", observe_media)

    def observe_context(owner, current_candidate, current_state):
        assert current_state is state
        assert current_candidate["id"] == candidate["id"]
        result = build(owner, current_candidate, current_state)
        assert result is not None
        prepared.append(result)
        return result

    monkeypatch.setattr(context_owner.ReplyContext, "build", observe_context)
    recovered = []
    recover = reply_drafts.ReplyDrafts.recover

    def observe_recovery(owner, current_state, target_id, lane, *, context, recent_replies):
        assert current_state is state
        assert context is prepared[0].context
        assert (target_id, lane) == ("105", "mention")
        assert recent_replies == [earlier, later]
        result = recover(
            owner, current_state, target_id, lane,
            context=context, recent_replies=recent_replies,
        )
        recovered.append(result)
        return result

    monkeypatch.setattr(reply_drafts.ReplyDrafts, "recover", observe_recovery)

    def evaluate_pipeline(**kwargs):
        assert recovered == [None]
        assert kwargs["context"] is prepared[0].context
        assert kwargs["context"]["target_id"] == "105"
        assert kwargs["recent_account_replies"] == [earlier]
        assert kwargs["same_author_interactions"] == []
        assert kwargs["supplied_images"] == []
        assert isinstance(kwargs["transport"].__self__, model_transport_owner.ReplyModelTransport)
        saved = json.loads(bot.STATE_FILE.read_text())
        assert saved["tweet_cache"]["104"]["post_type"] == "author_cap_context"
        return bot.PipelineResult(
            status="reply", reason="useful_reply",
            reply=unit_approved_reply(kwargs["context"]), model_call_count=1,
        )

    pipeline = Mock(side_effect=evaluate_pipeline)
    monkeypatch.setattr(bot, "run_single_call_reply_pipeline", pipeline)
    patch_reply_owner_method(
        monkeypatch, bot._tweet_lookup_cache.TweetLookupCache, "fetch",
        Mock(return_value=candidate),
    )
    remote = Mock(return_value={"data": {"id": "950105"}})
    install_receipt_bound_x_request_stub(monkeypatch, remote)
    relays = {}
    for name in (
        "build_context_for_reply_ai", "cache_tweet", "evaluate_single_call_reply",
        "get_mentions", "get_hot_post_reply_candidates",
        "_record_single_call_result", "recovery_comparison_account_replies",
        "collect_reply_images", "openai_responses_reply_call",
        "reply_media_context_for_candidate",
        "load_confirmed_reply_receipt", "reconcile_confirmed_reply_receipt",
        "reply_target_is_available_immediately_before_send",
    ):
        relays[name] = Mock(side_effect=AssertionError(f"root relay used: {name}"))
        monkeypatch.setattr(bot, name, relays[name], raising=False)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_POSTED
    assert len(prepared) == pipeline.call_count == 1
    assert collected_media == [prepared[0].media_context]
    assert prepared[0].media_context == {
        "lane": "mention", "target_id": "105", "mode": "none",
        "status": "none", "photos_expected": 0, "photos": [],
    }
    assert "105" not in state.get("reply_evaluation_records", {})
    assert state["mention_pending_candidates"] == {}
    assert state["daily_reply_count"] == 1
    assert state["ai_reply_history"][-1]["reply_post_id"] == "950105"
    saved = json.loads(bot.STATE_FILE.read_text())
    assert "105" not in saved.get("reply_evaluation_records", {})
    assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
    for relay in relays.values():
        relay.assert_not_called()
    assert remote.call_count == 1


@pytest.mark.parametrize("failed_step", ["prune", "save"])
def test_quarantine_batch_keeps_unfinished_steps_retryable(failed_step):
    state = {}
    progress = cycle._ReplyCycleProgress(
        3, quarantine_retirements_pending=True, evaluation_record_pruning_pending=True,
    )
    trace = Mock()
    evaluations = Mock(prune=trace.prune, prune_completed_mentions=trace.prune_completed)
    persistence = Mock(save=trace.save)
    failure = OSError("pending retirement step failed")
    getattr(trace, failed_step).side_effect = failure

    with pytest.raises(OSError) as caught:
        progress.flush_quarantine_retirements(state, evaluations, persistence)

    assert caught.value is failure
    assert progress.quarantine_retirements_pending is True
    assert progress.evaluation_record_pruning_pending is (failed_step == "prune")
    expected = [call.prune(state)]
    if failed_step == "save":
        expected.append(call.save(state, durable=True))
    assert trace.mock_calls == expected

    trace.reset_mock(side_effect=True)
    progress.flush_quarantine_retirements(state, evaluations, persistence)
    prune = call.prune(state) if failed_step == "prune" else call.prune_completed(state)
    assert trace.mock_calls == [prune, call.save(state, durable=True)]
    assert all(item.args[0] is state for item in trace.mock_calls)
    assert progress.quarantine_retirements_pending is False
    assert progress.evaluation_record_pruning_pending is False
    assert progress.fresh_mention_ai_evaluations == 3
