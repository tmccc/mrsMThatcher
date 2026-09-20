from __future__ import annotations

from tests.helpers.reply_evaluation import legacy_reply_evaluator

import copy
import inspect
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

import pytest

import mrs_bot_normal_reply_cycle as cycle
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
from tests.helpers.mention_fixtures import (
    editorial_no_reply,
    mention,
    queue_active_mention,
)
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401
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


def test_adapter_forwards_current_dependencies_arguments_results_and_errors(monkeypatch):
    adapter = bot.maybe_reply_to_mentions
    public = inspect.signature(adapter).parameters
    parameters = inspect.signature(cycle.maybe_reply_to_mentions).parameters
    assert tuple(public) == ("state", "_fresh_mention_ai_evaluations", "_skip_hot_post_fetch")
    assert public["state"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert public["_fresh_mention_ai_evaluations"].default == 0
    assert public["_skip_hot_post_fetch"].default is False
    assert len(parameters) == 39
    assert sum(param.kind is inspect.Parameter.KEYWORD_ONLY for param in parameters.values()) == 38
    removed = {
        name for name in vars(interfaces) if name.startswith("NORMAL_CHECK_STATUS_")
    } | {
        "AmbiguousRemotePostOutcome", "ConfirmedReplyLocalPersistenceError",
        "ProvedRemotePostNonSuccess", "UnrecoverableConfirmedReplyPersistenceError",
        "api_error_is_reply_not_allowed",
        "_is_terminal_candidate_local_failure",
        "pending_ai_reply_draft_key", "completed_mention_watermark_covers_target",
        "mention_pagination_provenance_is_valid",
        "terminal_reply_evaluation", "trim_context_text", "append_unique_durable",
        "pending_mention_candidates", "mark_mention_seen_if_applicable",
        "AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY",
        "active_author_evaluation_quarantine", "clarification_reply_context",
        "clarification_thread_is_terminal", "clear_author_evaluation_quarantine_history",
        "daily_author_reply_count", "daily_author_reply_counts",
        "prune_author_evaluation_quarantines", "prune_completed_mention_quarantine_evaluations",
        "prune_reply_evaluation_records", "record_qualifying_author_no_reply",
        "record_terminal_reply_evaluation", "reset_daily_reply_count_if_needed",
        "build_context_for_reply_ai", "cache_tweet", "evaluate_single_call_reply",
        "_record_single_call_result", "recovery_comparison_account_replies",
    }
    for name, function in inspect.getmembers(cycle, inspect.isfunction):
        if function.__module__ == cycle.__name__:
            assert removed.isdisjoint(inspect.signature(function).parameters), name
    dependencies = parameters.keys() - public.keys()
    owner_factories = {
        "author_quarantines": "_author_quarantine_owner",
        "reply_evaluations": "_reply_evaluation_owner",
        "clarifications": "_clarification_reply_owner",
        "accounting": "_daily_reply_accounting_owner",
        "mention_queue": "_mention_queue_owner",
        "reply_contexts": "_reply_context_owner",
        "tweets": "_tweet_lookup_cache_owner",
        "generation": "_reply_generation_owner",
        "history": "_reply_history_owner",
    }
    assert {"config", "persistence", "delivery", *owner_factories} <= dependencies
    state, result = {}, object()
    owner = Mock(return_value=result)
    monkeypatch.setattr(cycle, "maybe_reply_to_mentions", owner)
    for options in ({}, {"_fresh_mention_ai_evaluations": 3, "_skip_hot_post_fetch": True}):
        current = {key: object() for key in dependencies}
        factories = {}
        for key, value in current.items():
            if key == "config":
                monkeypatch.setattr(bot._reply_cycle_interfaces, "NormalReplyConfig", Mock(return_value=value))
            elif key in {"persistence", "delivery"}:
                monkeypatch.setattr(bot, f"_reply_cycle_{key}", Mock(return_value=value))
            elif key in owner_factories:
                factories[key] = Mock(return_value=value)
                monkeypatch.setattr(bot, owner_factories[key], factories[key])
            else:
                monkeypatch.setattr(bot, key, value)
        draft_owner = Mock(return_value=Mock(retire_ineligible=Mock()))
        watch_owner = Mock(return_value=object())
        monkeypatch.setattr(bot, "_reply_draft_owner", draft_owner)
        monkeypatch.setattr(bot, "_quote_watch_posts_owner", watch_owner)
        assert adapter(state, **options) is result
        for factory in factories.values():
            factory.assert_called_once_with()
        args, kwargs = owner.call_args
        assert len(args) == 1 and args[0] is state
        expected = {
            "_fresh_mention_ai_evaluations": 0, "_skip_hot_post_fetch": False,
            **options, **current,
        }
        assert kwargs.keys() == expected.keys()
        discovery_callbacks = {"get_mentions", "get_hot_post_reply_candidates"}
        assert all(
            kwargs[key] is value
            for key, value in expected.items()
            if key not in discovery_callbacks
        )
        assert kwargs["get_mentions"].func is mention_discovery.get_mentions
        assert kwargs["get_mentions"].keywords["tweets"] is current["tweets"]
        assert kwargs["get_mentions"].keywords["reply_evaluations"] is current["reply_evaluations"]
        assert kwargs["get_mentions"].keywords["mention_queue"] is current["mention_queue"]
        assert kwargs["get_hot_post_reply_candidates"].func is bot._hot_post_discovery.get_hot_post_reply_candidates
        assert kwargs["get_hot_post_reply_candidates"].keywords["tweets"] is current["tweets"]
        assert kwargs["get_hot_post_reply_candidates"].keywords["reply_evaluations"] is current["reply_evaluations"]
        assert kwargs["get_hot_post_reply_candidates"].keywords["watch_posts"] is watch_owner.return_value
        draft_owner.assert_called_once_with()
        watch_owner.assert_called_once_with(tweets=current["tweets"])
    failure = TypeError("current owner failure")
    owner.side_effect = failure
    with pytest.raises(TypeError) as caught:
        adapter(state)
    assert caught.value is failure


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


@pytest.mark.parametrize("initial_count,model_calls", [(2, 1), (2, 0), (4, 1)])
def test_backlog_continuation_uses_current_root_state_and_budget(monkeypatch, initial_count, model_calls):
    _configure_cycle(monkeypatch)
    adapter = bot.maybe_reply_to_mentions
    cycle_entry = Mock(wraps=cycle.maybe_reply_to_mentions)
    monkeypatch.setattr(cycle, "maybe_reply_to_mentions", cycle_entry)

    def generate(context, *args, evaluation_outcome, **kwargs):
        editorial_no_reply(context, *args, evaluation_outcome=evaluation_outcome, **kwargs)
        evaluation_outcome["model_call_count"] = model_calls

    patch_reply_owner_method(monkeypatch, generation_owner.ReplyGeneration, "evaluate", legacy_reply_evaluator(generate))
    for _ in range(2):
        state = bot.default_state()
        queue_active_mention(state, mention(105, 205), base_since_id="99")
        result = object()

        def continue_backlog(actual_state, **kwargs):
            assert actual_state is state
            assert kwargs == {
                "_fresh_mention_ai_evaluations": initial_count + model_calls,
                "_skip_hot_post_fetch": True,
            }
            saved = json.loads(bot.STATE_FILE.read_text())
            assert saved["mention_pending_candidates"] == {}
            assert saved["mention_backlog"] == state["mention_backlog"]
            assert saved["reply_evaluation_records"]["105"]["outcome"] == "no_reply"
            previous = cycle_entry.call_args.kwargs
            current_clock = Mock(return_value=bot.now_epoch())
            with monkeypatch.context() as patch:
                patch.setattr(bot, "ENABLE_AUTO_REPLIES", False)
                patch.setattr(bot, "now_epoch", current_clock)
                assert adapter(actual_state, **kwargs) == bot.NORMAL_CHECK_STATUS_DISABLED
            continued = cycle_entry.call_args.kwargs
            for name in ("author_quarantines", "reply_evaluations", "clarifications", "accounting",
                         "reply_contexts", "tweets", "generation", "history"):
                assert continued[name] is not previous[name]
            assert continued["author_quarantines"].now_epoch is current_clock
            assert continued["reply_evaluations"].now_epoch is current_clock
            for name in ("tweets", "generation", "history"):
                assert continued[name].now_epoch is current_clock
            return result

        current_callback = Mock(side_effect=continue_backlog)
        monkeypatch.setattr(bot, "maybe_reply_to_mentions", current_callback)
        actual = adapter(state, _fresh_mention_ai_evaluations=initial_count)
        if initial_count + model_calls < bot.MAX_MENTIONS_PER_CHECK:
            assert actual is result
            current_callback.assert_called_once_with(
                state, _fresh_mention_ai_evaluations=initial_count + model_calls,
                _skip_hot_post_fetch=True,
            )
        else:
            assert actual == bot.NORMAL_CHECK_STATUS_CHECKED
            current_callback.assert_not_called()
        assert state["mention_pending_candidates"] == {}
        assert state["last_seen_mention_id"] == "99"
    assert bot._hot_post_discovery.get_hot_post_reply_candidates.call_count == 2
    bot.x_request.assert_not_called()
    bot.create_post.assert_not_called()


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
    accounted = Mock(wraps=bot.record_api_error)
    monkeypatch.setattr(bot, "save_state", saved)
    monkeypatch.setattr(bot, "record_api_error", accounted)

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
    callback = (
        "bind_conversational_reply_attempt_time"
        if boundary == "attempt_binding" else "apply_confirmed_reply_receipt"
    )
    monkeypatch.setattr(bot, callback, Mock(side_effect=failure))
    transport = Mock(return_value=({}, {"reply_post_id": "900"}))
    monkeypatch.setattr(bot, "post_conversational_reply_with_durable_identity", transport)
    accounted = Mock(wraps=bot.record_api_error)
    outcomes = Mock(wraps=bot.log_ai_reply_posting_outcome)
    cleanup = Mock(wraps=bot.remove_confirmed_reply_receipt)
    monkeypatch.setattr(bot, "record_api_error", accounted)
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
            bot._reply_draft_owner().clear if label == "clear"
            else bot._reply_evaluation_owner().record if label == "terminal"
            else bot._mention_queue_owner().mark_seen if label == "seen"
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

    assert bot.maybe_reply_to_mentions(
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
    bot.reply_media_context_for_candidate.assert_not_called()
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
    bot.write_confirmed_reply_receipt(receipt)
    state = bot.default_state()
    state.update(daily_reply_date="2000-01-01", daily_reply_count=48)
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", False)
    trace = Mock()
    for label, name in (
        ("reset", "reset_daily_reply_count_if_needed"),
        ("reconcile", "reconcile_confirmed_reply_receipt"),
    ):
        original = bot._daily_reply_accounting_owner().reset if label == "reset" else getattr(bot, name)
        callback = Mock(wraps=original)
        trace.attach_mock(callback, label)
        if label == "reset":
            patch_reply_owner_method(monkeypatch, accounting_owner.DailyReplyAccounting, "reset", callback)
        else:
            monkeypatch.setattr(bot, name, callback)
    original_barrier = bot.block_if_ambiguous_remote_post

    def barrier():
        saved = json.loads(bot.STATE_FILE.read_text())
        assert saved["daily_reply_count"] == state["daily_reply_count"] == 1
        assert saved["daily_reply_date"] == receipt["daily_reply_date"]
        assert saved["replied_to_ids"] == [receipt["target_id"]]
        assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
        return original_barrier()

    trace.attach_mock(Mock(side_effect=barrier), "barrier")
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", trace.barrier)
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
    accounted = Mock(wraps=bot.record_api_error)
    monkeypatch.setattr(bot, "save_state", saved)
    monkeypatch.setattr(bot, "record_api_error", accounted)
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
    monkeypatch.setattr(bot, "reply_target_is_available_immediately_before_send", preflight)

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
        assert bot.store_pending_ai_reply(state, "105", "mention", reply, context=draft_context)
    media = {"native_media": []}
    prepared = interfaces.PreparedReplyContext(context, media)
    patch_reply_context_method(monkeypatch, "build", Mock(return_value=prepared))
    prepare_context = cycle._prepare_reply_context

    def prepare(*args, **kwargs):
        result = prepare_context(*args, **kwargs)
        assert result is prepared
        assert result.context is context
        assert result.media_context is media
        return result

    preparation = Mock(side_effect=prepare)
    monkeypatch.setattr(cycle, "_prepare_reply_context", preparation)
    recovery = Mock(wraps=bot._reply_draft_owner().recover)
    patch_reply_draft_method(monkeypatch, "recover", recovery)
    evaluator = Mock(return_value=bot.PipelineResult(
        status="reply", reason="useful_reply", reply=reply, model_call_count=1,
    ))
    patch_reply_owner_method(monkeypatch, generation_owner.ReplyGeneration, "evaluate", evaluator)
    monkeypatch.setattr(bot, "reply_target_is_available_immediately_before_send", Mock(return_value=False))

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
    """Exercise real context, cache, history and generation owners without root relays."""
    build = context_owner.ReplyContext.build
    evaluate = generation_owner.ReplyGeneration.evaluate
    _configure_cycle(monkeypatch)
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
            status="no_reply", reason="completed_exchange", model_call_count=1,
        )

    pipeline = Mock(side_effect=evaluate_pipeline)
    monkeypatch.setattr(bot, "run_single_call_reply_pipeline", pipeline)
    relays = {}
    for name in (
        "build_context_for_reply_ai", "cache_tweet", "evaluate_single_call_reply",
        "get_mentions", "get_hot_post_reply_candidates",
        "_record_single_call_result", "recovery_comparison_account_replies",
        "collect_reply_images", "openai_responses_reply_call", "_openai_api_error",
        "reply_media_context_for_candidate",
    ):
        relays[name] = Mock(side_effect=AssertionError(f"root relay used: {name}"))
        monkeypatch.setattr(bot, name, relays[name])

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert len(prepared) == pipeline.call_count == 1
    assert collected_media == [prepared[0].media_context]
    assert prepared[0].media_context == {
        "lane": "mention", "target_id": "105", "mode": "none",
        "status": "none", "photos_expected": 0, "photos": [],
    }
    assert state["reply_evaluation_records"]["105"]["outcome"] == "no_reply"
    assert state["last_seen_mention_id"] == "99"
    assert state["mention_pending_candidates"] == {}
    assert state["daily_reply_count"] == 0
    saved = json.loads(bot.STATE_FILE.read_text())
    assert saved["reply_evaluation_records"]["105"]["outcome"] == "no_reply"
    for relay in relays.values():
        relay.assert_not_called()
    bot.x_request.assert_not_called()
    bot.create_post.assert_not_called()


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
