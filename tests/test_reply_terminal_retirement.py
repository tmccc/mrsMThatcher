"""Exercise lane delivery outcomes and retirement ordering without bot setup."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest

import mrs_bot_normal_reply_cycle as normal_cycle
import mrs_bot_quote_reply_cycle as quote_cycle
from mrs_bot_reply_cycle_interfaces import (
    NormalReplyConfig,
    QuoteReplyConfig,
    ReplyCycleDelivery,
    ReplyCyclePersistence,
)


class ApiError(Exception):
    """Represent a terminal API refusal in these isolated delivery tests."""


class ProvedRejection(ApiError):
    """Represent a refusal that permits retirement of its sending journal."""


class RemoteOperationsPaused(RuntimeError):
    """Represent a local runtime-control pause before remote transmission."""


def prepare_delivery(lane, outcome, *, save_failure=None, retirement_failure=None):
    """Build observable delivery dependencies with only in-memory state effects."""
    target = "105"
    state = {
        "replied_to_ids": ["100"],
        "skipped_quote_post_ids": ["99"],
        "last_seen_mention_id": "100",
        "pending_ai_reply_drafts": {f"{lane}:{target}": {"text": "reply"}},
        "daily_reply_count": 2,
        "daily_quote_reply_count": 1,
    }
    replied_ids = {"100"}
    receipt = {"target_id": target, "lifecycle_state": "sending"}
    reply = "Validated reply"
    trace = Mock()
    snapshots = []
    rejection = ProvedRejection("denied") if outcome == "proved" else ApiError("denied")
    trace.available.return_value = outcome != "unavailable"
    trace.post.side_effect = rejection
    trace.retire.side_effect = retirement_failure

    def save(actual_state, *, durable=False):
        assert actual_state is state
        if durable and save_failure is not None:
            raise save_failure
        snapshots.append(copy.deepcopy(actual_state))

    def clear(actual_state, target_id, source):
        assert actual_state is state
        if lane != "quote_tweet":
            assert target_id in replied_ids
        actual_state["pending_ai_reply_drafts"].pop(f"{source}:{target_id}")

    def record(actual_state, **fields):
        actual_state["reply_evaluation_records"] = {fields["target_id"]: fields}

    def mark_seen(actual_state, candidate):
        if lane == "mention":
            actual_state["last_seen_mention_id"] = candidate["id"]

    def mark_skipped(actual_state, target_id):
        actual_state["skipped_quote_post_ids"].append(target_id)

    trace.save.side_effect = save
    trace.clear.side_effect = clear
    trace.record.side_effect = record
    trace.mark_seen.side_effect = mark_seen
    trace.mark_skipped.side_effect = mark_skipped
    trace.append.side_effect = lambda values, target_id: [*values, target_id]
    persistence = ReplyCyclePersistence(
        save=trace.save, recover=Mock(), store=Mock(), clear=trace.clear,
        retire_ineligible=Mock(),
    )
    delivery = ReplyCycleDelivery(
        receipts=SimpleNamespace(load=Mock()),
        completion=SimpleNamespace(reconcile=Mock(), finalise=Mock()),
        block_ambiguous=Mock(),
        receipt_values=SimpleNamespace(bind_attempt=Mock()),
        tweets=SimpleNamespace(target_is_available=trace.available),
        post=trace.post, retire_rejected=trace.retire,
        ambiguous_outcome=LookupError,
        remote_operations_paused=RemoteOperationsPaused, api_error=ApiError,
        confirmed_local_failure=ArithmeticError, proved_non_success=ProvedRejection,
        unrecoverable_confirmed=EOFError,
        reply_not_allowed=lambda error: error is rejection,
        save_state=trace.save, log=trace.log, posting_outcome=trace.posting_outcome,
        cooldowns=SimpleNamespace(record_error=trace.api_error),
    )
    settings = dict(
        enabled=True, mark_as_ai=True, maximum_daily_replies=10,
        maximum_daily_author_replies=2, minimum_reply_spacing=0, user_id="12345",
    )
    dependencies = dict(
        persistence=persistence, delivery=delivery, log=trace.log,
        log_ai_reply_posting_outcome=trace.posting_outcome,
        log_event=trace.event,
        reply_evaluations=SimpleNamespace(record=trace.record),
    )
    if lane == "quote_tweet":
        dependencies.update(
            config=QuoteReplyConfig(
                **settings, quote_checks_enabled=True,
                maximum_candidates=3, maximum_daily_quote_replies=3,
            ),
        )

        def run():
            with patch.object(quote_cycle, "mark_quote_tweet_skipped", trace.mark_skipped):
                return quote_cycle._deliver_reply(target, reply, receipt, state, **dependencies)
    else:
        candidate = normal_cycle._ReplyCandidate(
            mention={"id": target, "_candidate_source": lane}, mention_id=target,
            author_id="205", incoming_text="question", source=lane,
            log_source="mention" if lane == "mention" else "hot-post",
        )
        dependencies.update(
            config=NormalReplyConfig(**settings, maximum_fresh_evaluations=3, incoming_max_chars=1000),
            mention_queue=SimpleNamespace(mark_seen=trace.mark_seen),
        )

        def run():
            with patch.object(normal_cycle, "append_unique_durable", trace.append):
                return normal_cycle._deliver_reply(state, candidate, replied_ids, reply, receipt, **dependencies)

    return SimpleNamespace(
        run=run, state=state, trace=trace, snapshots=snapshots,
        receipt=receipt, rejection=rejection, reply=reply, replied_ids=replied_ids,
    )


@pytest.mark.parametrize("lane", ["mention", "hot_post", "quote_tweet"])
@pytest.mark.parametrize("outcome", ["unavailable", "unproved", "proved"])
def test_terminal_delivery_preserves_lane_bookkeeping_and_save_before_journal(lane, outcome):
    scenario = prepare_delivery(lane, outcome)
    assert scenario.run().status == "checked"
    trace, state = scenario.trace, scenario.state
    names = [entry[0] for entry in trace.mock_calls]
    expected = ["available"] + ([] if outcome == "unavailable" else ["post"])
    expected += ["log.warning", "posting_outcome"]
    if lane == "quote_tweet":
        expected += ["event", "record", "mark_skipped", "clear", "save"]
        assert state["replied_to_ids"] == ["100"]
        assert scenario.replied_ids == {"100"}
        assert state["skipped_quote_post_ids"] == ["99", "105"]
        trace.mark_seen.assert_not_called()
    else:
        expected += ["record", "event", "clear", "append", "mark_seen", "save"]
        assert state["replied_to_ids"] == ["100", "105"]
        assert scenario.replied_ids == {"100", "105"}
        assert state["skipped_quote_post_ids"] == ["99"]
        trace.mark_skipped.assert_not_called()
    if outcome == "proved":
        expected.append("retire")
        trace.retire.assert_called_once_with(scenario.receipt, scenario.rejection)
    else:
        trace.retire.assert_not_called()
    assert names == expected
    failure_reason = "target_unavailable_pre_send" if outcome == "unavailable" else "reply_not_permitted"
    trace.posting_outcome.assert_called_once_with(
        reply=scenario.reply, status="posting_failed_terminal", lane=lane,
        target_id="105", failure_reason=failure_reason,
    )
    trace.event.assert_called_once_with(
        "reply_target_terminal", lane="hot-post" if lane == "hot_post" else lane,
        target_id="105", outcome="reply_not_permitted", reason=f"x_{failure_reason}",
    )
    assert state["reply_evaluation_records"]["105"] == {
        "target_id": "105", "lane": lane, "outcome": "reply_not_permitted",
        "reason": f"x_{failure_reason}",
    }
    assert state["last_seen_mention_id"] == ("105" if lane == "mention" else "100")
    assert state["daily_reply_count"] == 2
    assert state["daily_quote_reply_count"] == 1
    assert state["pending_ai_reply_drafts"] == {}
    trace.save.assert_called_once_with(state, durable=True)
    assert scenario.snapshots == [state]
    trace.api_error.assert_not_called()


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
@pytest.mark.parametrize("outcome", ["unavailable", "proved"])
def test_terminal_save_error_preserves_the_original_delivery_exception_boundary(lane, outcome):
    failure = OSError("terminal state could not be persisted")
    scenario = prepare_delivery(lane, outcome, save_failure=failure)
    if outcome == "proved":
        with pytest.raises(OSError) as caught:
            scenario.run()
        assert caught.value is failure
        scenario.trace.api_error.assert_not_called()
        assert scenario.trace.save.call_count == 1
        assert scenario.snapshots == []
    else:
        # Pre-send retirement stays inside the existing transport try block.
        assert scenario.run().status == ("checked" if lane == "quote_tweet" else "api_error")
        scenario.trace.api_error.assert_called_once_with(scenario.state, failure, "x", scope="write")
        assert scenario.trace.save.call_args_list == [
            call(scenario.state, durable=True), call(scenario.state),
        ]
        assert scenario.trace.posting_outcome.call_args.kwargs["failure_reason"] == "unexpected_posting_error"
    scenario.trace.retire.assert_not_called()


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
@pytest.mark.parametrize("outcome", ["unavailable", "proved"])
def test_terminal_retirement_does_not_swallow_interrupts(lane, outcome):
    failure = KeyboardInterrupt()
    scenario = prepare_delivery(lane, outcome, save_failure=failure)
    with pytest.raises(KeyboardInterrupt) as caught:
        scenario.run()
    assert caught.value is failure
    assert scenario.trace.save.call_count == 1
    scenario.trace.retire.assert_not_called()
    scenario.trace.api_error.assert_not_called()


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
def test_rejected_journal_cleanup_failure_propagates_after_durable_terminal_state(lane):
    failure = OSError("sending journal could not be retired")
    scenario = prepare_delivery(lane, "proved", retirement_failure=failure)
    with pytest.raises(OSError) as caught:
        scenario.run()
    assert caught.value is failure
    assert scenario.snapshots == [scenario.state]
    assert not scenario.snapshots[0]["pending_ai_reply_drafts"]
    assert [entry[0] for entry in scenario.trace.mock_calls][-2:] == ["save", "retire"]
    scenario.trace.api_error.assert_not_called()


@pytest.mark.parametrize("lane", ["mention", "hot_post", "quote_tweet"])
def test_successful_delivery_passes_original_values_and_returns_the_confirmed_receipt(lane):
    scenario = prepare_delivery(lane, "proved")
    confirmed = {"lifecycle_state": "confirmed", "reply_post_id": "999"}
    scenario.trace.post.side_effect = None
    scenario.trace.post.return_value = ({"data": {"id": "999"}}, confirmed)

    assert scenario.run() is confirmed
    assert [entry[0] for entry in scenario.trace.mock_calls] == ["available", "post"]
    scenario.trace.available.assert_called_once_with("105")
    scenario.trace.post.assert_called_once_with(
        state=scenario.state, receipt_template=scenario.receipt,
        reply_text=scenario.reply, reply_to_id="105", made_with_ai=True, lane=lane,
    )
    assert scenario.trace.post.call_args.kwargs["state"] is scenario.state
    assert scenario.trace.post.call_args.kwargs["receipt_template"] is scenario.receipt
    assert scenario.state["pending_ai_reply_drafts"]


@pytest.mark.parametrize("lane", ["mention", "hot_post", "quote_tweet"])
@pytest.mark.parametrize("stage", ["available", "post"])
@pytest.mark.parametrize("api_failure", [False, True])
def test_retryable_delivery_failure_records_matching_cooldown_and_keeps_the_pending_draft(
    lane, stage, api_failure,
):
    scenario = prepare_delivery(lane, "proved")
    before = copy.deepcopy(scenario.state)
    failure = ApiError("rate limited") if api_failure else OSError("transport unavailable")
    failure.status_code = 429
    getattr(scenario.trace, stage).side_effect = failure

    assert scenario.run().status == ("checked" if lane == "quote_tweet" else "api_error")
    scope = "write" if stage == "post" else ("quote" if lane == "quote_tweet" else "api")
    scenario.trace.api_error.assert_called_once_with(scenario.state, failure, "x", scope=scope)
    scenario.trace.save.assert_called_once_with(scenario.state)
    scenario.trace.posting_outcome.assert_called_once_with(
        reply=scenario.reply, status="posting_failed_retryable", lane=lane,
        target_id="105",
        failure_reason=("x_api_429" if api_failure else (
            "unexpected_posting_error" if stage == "post" else "unexpected_pre_send_lookup_error"
        )),
    )
    assert scenario.state == before
    if stage == "available":
        scenario.trace.post.assert_not_called()
    assert [entry[0] for entry in scenario.trace.mock_calls] == (
        ["available"] + (["post"] if stage == "post" else [])
        + ["log.exception", "posting_outcome", "api_error", "save"]
    )


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
@pytest.mark.parametrize("stage", ["available", "post"])
def test_runtime_pause_defers_without_api_error_or_posting_failure(lane, stage):
    scenario = prepare_delivery(lane, "proved")
    scope = "quote" if lane == "quote_tweet" else "api"
    scenario.state.update({
        ("x_write_error_epochs" if stage == "post" else
         "quote_x_error_epochs" if scope == "quote" else "x_error_epochs"): [100, 200],
        ("x_write_api_cooldown_until_epoch" if stage == "post" else
         "quote_api_cooldown_until_epoch" if scope == "quote" else
         "api_cooldown_until_epoch"): 0,
    })
    before = copy.deepcopy(scenario.state)
    pause = RemoteOperationsPaused("local maintenance pause")
    getattr(scenario.trace, stage).side_effect = pause

    assert scenario.run().status == (
        "checked" if lane == "quote_tweet" else "api_error"
    )
    assert scenario.state == before
    scenario.trace.api_error.assert_not_called()
    scenario.trace.save.assert_not_called()
    scenario.trace.posting_outcome.assert_not_called()
    scenario.trace.retire.assert_not_called()
    if stage == "available":
        scenario.trace.post.assert_not_called()
    assert [entry[0] for entry in scenario.trace.mock_calls] == (
        ["available"] + (["post"] if stage == "post" else []) + ["log.info"]
    )


@pytest.mark.parametrize("lane", ["mention", "hot_post", "quote_tweet"])
@pytest.mark.parametrize("stage", ["available", "post"])
@pytest.mark.parametrize("failure_type", [EOFError, ArithmeticError, LookupError])
def test_confirmed_and_ambiguous_delivery_failures_propagate_without_retry_bookkeeping(
    lane, stage, failure_type,
):
    scenario = prepare_delivery(lane, "proved")
    before = copy.deepcopy(scenario.state)
    failure = failure_type("recovery authority must handle this outcome")
    getattr(scenario.trace, stage).side_effect = failure

    with pytest.raises(failure_type) as caught:
        scenario.run()
    assert caught.value is failure
    expected = ["available"] + (["post"] if stage == "post" else []) + ["log.critical"]
    if failure_type is LookupError:
        expected.append("posting_outcome")
        scenario.trace.posting_outcome.assert_called_once_with(
            reply=scenario.reply, status="posting_failed_retryable", lane=lane,
            target_id="105", failure_reason="ambiguous_remote_outcome",
        )
    assert [entry[0] for entry in scenario.trace.mock_calls] == expected
    assert scenario.state == before
    log_args = scenario.trace.log.critical.call_args.args
    message = log_args[0] % log_args[1:] if len(log_args) > 1 else log_args[0]
    label = {"mention": "mention", "hot_post": "hot-post", "quote_tweet": "quote-tweet"}[lane]
    if failure_type is EOFError:
        assert message == (
            f"Confirmed {label} reply lost every complete durable local identity; "
            "the global remote-write safety barrier remains active"
        )
    elif failure_type is ArithmeticError:
        assert message == f"Confirmed {label} reply required its durable state fallback"
    else:
        assert message == (
            f"{'Quote-tweet' if lane == 'quote_tweet' else label} reply stopped after an "
            "ambiguous remote outcome; the global remote-write safety barrier remains active"
        )


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
@pytest.mark.parametrize("stage", ["api_error", "save"])
def test_retryable_failure_bookkeeping_error_propagates_without_a_second_attempt(lane, stage):
    scenario = prepare_delivery(lane, "proved")
    scenario.trace.post.side_effect = OSError("transport unavailable")
    failure = RuntimeError("retry bookkeeping failed")
    getattr(scenario.trace, stage).side_effect = failure

    with pytest.raises(RuntimeError) as caught:
        scenario.run()
    assert caught.value is failure
    scenario.trace.post.assert_called_once()
    scenario.trace.api_error.assert_called_once()
    assert scenario.trace.save.call_count == (1 if stage == "save" else 0)
    scenario.trace.retire.assert_not_called()
