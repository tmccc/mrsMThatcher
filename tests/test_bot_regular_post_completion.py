"""Failure and field-evaluation boundaries of live and recovered completion."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import mrs_bot_main_post_reconciliation as recovery
import mrs_bot_quote_posting as posting


STAGES = ("save", "enqueue", "retire", "remove")


class LocalPersistenceError(RuntimeError):
    """Stand in for the live caller's persistence exception authority."""


def _completion_case(lane, *, fail_at=None, failure=None):
    lines, images, state = set(), set(), {}
    receipt = {"post_id": "950001", "line_no": 1, "image_no": 2,
               "image_basename": "image.jpg", "quote_hash": "quote", "text": "text"}
    image_choice = {"image_hash": "before-save", "score": 1}
    events = []

    def stage(name):
        def invoke(*args, **kwargs):
            events.append(name)
            if name == fail_at:
                raise failure
        return Mock(side_effect=invoke)

    callbacks = {name: stage(name) for name in (*STAGES, "event")}
    root, context, log = Mock(), Mock(), Mock()
    common = dict(
        log=log,
        save_regular_post_protected_state=callbacks["save"],
        enqueue_historical_context_obligation=callbacks["enqueue"],
        retire_lane_transport_journal_if_present=callbacks["retire"],
        REGULAR_POST_RECEIPT_FILE=Path("regular_post_receipt.json"),
        remove_regular_post_receipt=callbacks["remove"],
        emit_account_root_posted=root,
        safely_process_due_historical_context_obligations=context,
    )
    if lane == "live":
        common["log_event"] = callbacks["event"]
        tweets = SimpleNamespace(store=Mock(), record_recent_own_post=Mock())
        def invoke():
            return posting._complete_quote_post(
                lines, images, state, quote_hash="quote", image_basename="image.jpg",
                posted_id="950001", quote_post_epoch=100, quote_schedule_fields={},
                meme_schedule_fields={}, tweet="text", receipt=receipt, line_no=1,
                image_no=2, image_choice=image_choice, canonical_quote_text="canonical",
                tweets=tweets, MY_USER_ID="123",
                ConfirmedPostLocalPersistenceError=LocalPersistenceError, **common,
            )
    else:
        receipt_operations = SimpleNamespace(
            load_regular=Mock(return_value=("valid", receipt)),
            finalize_pending=Mock(),
        )
        receipts = SimpleNamespace(current=lambda: receipt_operations)
        def invoke():
            return recovery.reconcile_regular_post_receipt(
                lines, images, state, InvalidRegularPostReceipt=ValueError,
                apply_regular_post_receipt=Mock(),
                ensure_reconciled_regular_receipt_schedule_is_future=Mock(),
                receipts=receipts,
                verify_lane_transport_source_lineage_if_present=Mock(), **common,
            )
    return SimpleNamespace(
        invoke=invoke, callbacks=callbacks, common=common, events=events,
        receipt=receipt, image_choice=image_choice, lines=lines, images=images,
        state=state, root=root, context=context, log=log,
    )


@pytest.mark.parametrize("lane", ["live", "recovery"])
@pytest.mark.parametrize("fail_at", STAGES)
def test_failed_completion_does_not_advance_or_change_caller_error_policy(lane, fail_at):
    failure = OSError("local completion failed")
    case = _completion_case(lane, fail_at=fail_at, failure=failure)
    expected = LocalPersistenceError if lane == "live" else OSError
    with pytest.raises(expected) as caught:
        case.invoke()
    assert case.events == list(STAGES[:STAGES.index(fail_at) + 1])
    if lane == "live":
        assert caught.value.__cause__ is failure
        assert str(caught.value) == "Confirmed regular quote/image post 950001 but protected local persistence failed"
        case.log.critical.assert_called_once()
    else:
        assert caught.value is failure
        case.log.critical.assert_not_called()
    case.root.assert_not_called()
    case.context.assert_not_called()


@pytest.mark.parametrize("lane", ["live", "recovery"])
@pytest.mark.parametrize("fail_at", ["save", "remove"])
def test_completion_does_not_wrap_interrupts_or_continue(lane, fail_at):
    failure = KeyboardInterrupt("interrupted completion")
    case = _completion_case(lane, fail_at=fail_at, failure=failure)
    with pytest.raises(KeyboardInterrupt) as caught:
        case.invoke()
    assert caught.value is failure
    assert case.events == list(STAGES[:STAGES.index(fail_at) + 1])
    case.log.critical.assert_not_called()
    case.root.assert_not_called()
    case.context.assert_not_called()


@pytest.mark.parametrize("lane", ["live", "recovery"])
def test_completion_reads_event_and_transport_fields_at_their_original_stages(lane):
    case = _completion_case(lane)
    proof = object()

    def save(lines, images, state, *, durable):
        assert lines is case.lines and images is case.images and state is case.state
        assert durable is True
        case.events.append("save")
        case.receipt["line_no"] = 11
        case.receipt["image_no"] = 22
        case.image_choice["image_hash"] = "after-save"
        case.image_choice["score"] = 3
        return proof

    def enqueue(receipt):
        assert receipt is case.receipt
        case.events.append("enqueue")
        receipt["post_id"] = "950002"

    case.callbacks["save"].side_effect = save
    case.callbacks["enqueue"].side_effect = enqueue
    case.invoke()

    assert case.events == [*STAGES, *(["event"] if lane == "live" else [])]
    if lane == "live":
        case.callbacks["event"].assert_called_once_with(
            "main_post_posted", lane="quote_image", post_id="950001", line_no=1,
            image_no=2, image_basename="image.jpg", quote_hash="quote",
            image_hash="after-save", image_score=3,
        )
    else:
        case.callbacks["event"].assert_not_called()
    case.callbacks["retire"].assert_called_once_with(
        receipt_path=case.common["REGULAR_POST_RECEIPT_FILE"], receipt=case.receipt,
        lane="quote_image", post_id="950001" if lane == "live" else "950002",
        commit_proof=proof,
    )
    case.callbacks["remove"].assert_called_once_with(case.receipt, commit_proof=proof)
    case.root.assert_called_once()
    case.context.assert_called_once()
