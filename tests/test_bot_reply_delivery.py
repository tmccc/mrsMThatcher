from __future__ import annotations

import inspect
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call, mock_open

import pytest

import mrs_bot_reply_delivery as delivery
from tests.test_unit_helpers import (
    bot,
    install_receipt_bound_x_request_stub,
    isolate_regular_post_receipt,
    unit_approved_reply,
    unit_sending_v4_reply_receipt,
)
from tests.test_x_write_outcome_conservatism import (
    _existing_reply_target_then_deleted_create,
    isolate_remote_write_state,
)


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, copy, io, logging, os, random, re, socket, sys
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('reply delivery import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name != 'mrs_bot_reply_delivery':
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_reply_delivery
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


def test_adapters_forward_current_dependencies_arguments_results_and_errors(monkeypatch):
    names = (
        "load_confirmed_reply_receipt",
        "write_confirmed_reply_receipt",
        "write_sending_reply_receipt",
        "promote_sending_reply_receipt",
        "_promote_legacy_sending_reply_receipt_from_confirmed_transport",
        "remove_confirmed_reply_receipt",
        "retire_proved_rejected_conversational_reply_receipt",
        "post_conversational_reply_with_durable_identity",
    )
    for name in names:
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        dependencies = inspect.signature(getattr(delivery, name)).parameters.keys() - public.keys()
        args = tuple(object() for parameter in public.values() if parameter.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD)
        options = {key: object() for key, parameter in public.items() if parameter.kind == inspect.Parameter.KEYWORD_ONLY}
        result = object()
        owner = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch.setattr(delivery, name, owner)
            for _ in range(2):
                current = {key: object() for key in dependencies}
                for key, value in current.items():
                    patch.setattr(bot, key, value)
                assert adapter(*args, **options) is result, name
                actual_args, actual_kwargs = owner.call_args
                assert len(actual_args) == len(args)
                assert all(actual is expected for actual, expected in zip(actual_args, args)), name
                expected = {**options, **current}
                assert actual_kwargs.keys() == expected.keys(), name
                assert all(actual_kwargs[key] is value for key, value in expected.items()), name
            failure = TypeError(name)
            owner.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(*args, **options)
            assert caught.value is failure


@pytest.mark.parametrize("accepted,status", [(0, "sending"), (1, "legacy_sending"), (2, "valid"), (3, "valid"), (None, "invalid")])
def test_loader_preserves_validation_order_and_original_receipt(monkeypatch, accepted, status):
    receipt = unit_sending_v4_reply_receipt()
    reader = Mock(return_value=(True, receipt))
    monkeypatch.setattr(bot, "load_receipt_json_no_follow", reader)
    trace = Mock()
    names = (
        "sending_reply_receipt_is_semantically_valid",
        "_legacy_sending_reply_receipt_is_semantically_valid",
        "confirmed_reply_receipt_is_semantically_valid",
        "_legacy_confirmed_reply_receipt_is_semantically_valid",
    )
    for index, name in enumerate(names):
        validator = Mock(return_value=index == accepted)
        trace.attach_mock(validator, name)
        monkeypatch.setattr(bot, name, validator)
    actual_status, actual_receipt = bot.load_confirmed_reply_receipt()
    assert actual_status == status and actual_receipt is receipt
    reader.assert_called_once_with(bot.CONFIRMED_REPLY_RECEIPT_FILE)
    assert reader.call_args.args[0] is bot.CONFIRMED_REPLY_RECEIPT_FILE
    expected = names if accepted is None else names[:accepted + 1]
    assert [entry[0] for entry in trace.mock_calls] == list(expected)
    assert all(entry.args[0] is receipt for entry in trace.mock_calls)


def test_loader_keeps_read_errors_and_dictionary_guard_before_validation(monkeypatch):
    reader = Mock()
    logger = Mock()
    validator = Mock(side_effect=AssertionError("non-dictionary reached validation"))
    monkeypatch.setattr(bot, "load_receipt_json_no_follow", reader)
    monkeypatch.setattr(bot, "sending_reply_receipt_is_semantically_valid", validator)
    monkeypatch.setattr(bot, "log", logger)
    reader.return_value = (False, object())
    assert bot.load_confirmed_reply_receipt() == ("absent", None)
    reader.return_value = (True, [])
    assert bot.load_confirmed_reply_receipt() == ("invalid", None)
    logger.critical.assert_called_once()
    reader.side_effect = OSError("unsafe read")
    assert bot.load_confirmed_reply_receipt() == ("invalid", None)
    logger.exception.assert_called_once()
    interrupt = KeyboardInterrupt("read interrupted")
    reader.side_effect = interrupt
    with pytest.raises(KeyboardInterrupt) as caught:
        bot.load_confirmed_reply_receipt()
    assert caught.value is interrupt
    validator.assert_not_called()


@pytest.mark.parametrize("lifecycle", ["sending", "confirmed"])
def test_writer_checks_retirement_namespace_validation_then_create_and_preserves_cause(monkeypatch, lifecycle):
    receipt = unit_sending_v4_reply_receipt()
    if lifecycle == "confirmed":
        receipt = bot._confirmed_reply_receipt_from_sending(
            receipt, reply_post_id="999", confirmation_epoch=receipt["attempt_epoch"],
        )
    trace = Mock()
    for label, name, result in (
        ("retirement", "remote_receipt_retirement_is_blocking", False),
        ("namespace", "receipt_namespace_entry_exists", False),
        ("validation", f"{lifecycle}_reply_receipt_is_semantically_valid", True),
        ("create", "durable_create_receipt_json", None),
    ):
        callback = Mock(return_value=result)
        trace.attach_mock(callback, label)
        monkeypatch.setattr(bot, name, callback)
    writer = getattr(bot, f"write_{lifecycle}_reply_receipt")
    writer(receipt)
    assert trace.mock_calls == [call.retirement(), call.namespace(bot.CONFIRMED_REPLY_RECEIPT_FILE),
                                call.validation(receipt), call.create(bot.CONFIRMED_REPLY_RECEIPT_FILE, receipt)]
    assert trace.create.call_args.args[1] is receipt
    trace.reset_mock()
    trace.retirement.return_value = True
    with pytest.raises(bot.InvalidConfirmedReplyReceipt, match="during source-receipt retirement"):
        writer(receipt)
    assert trace.mock_calls == [call.retirement()]
    trace.reset_mock()
    trace.retirement.return_value = False
    trace.namespace.return_value = True
    with pytest.raises(bot.InvalidConfirmedReplyReceipt, match="unresolved"):
        writer(receipt)
    assert [entry[0] for entry in trace.mock_calls] == ["retirement", "namespace"]
    trace.namespace.return_value = False
    race = FileExistsError("entry appeared")
    trace.create.side_effect = race
    with pytest.raises(bot.InvalidConfirmedReplyReceipt, match="appeared during publication") as caught:
        writer(receipt)
    assert caught.value.__cause__ is race


def test_removal_keeps_open_json_equality_disposition_and_canonical_retirement(monkeypatch):
    receipt = unit_sending_v4_reply_receipt()
    opened = mock_open()
    decoder = Mock(return_value=dict(receipt))
    retire = Mock()
    monkeypatch.setattr(delivery, "open", opened, raising=False)
    monkeypatch.setattr(bot, "json", SimpleNamespace(load=decoder))
    monkeypatch.setattr(bot, "retire_current_source_receipt", retire)
    canonical = Mock(return_value=b"current canonical receipt")
    monkeypatch.setattr(bot, "canonical_atomic_json_bytes", canonical)
    bot.remove_confirmed_reply_receipt(receipt, sending_disposition="definite_non_success")
    opened.assert_called_once_with(bot.CONFIRMED_REPLY_RECEIPT_FILE, "r", encoding="utf-8")
    decoder.assert_called_once_with(opened.return_value.__enter__.return_value)
    canonical.assert_called_once_with(receipt)
    assert canonical.call_args.args[0] is receipt
    retire.assert_called_once_with(bot.CONFIRMED_REPLY_RECEIPT_FILE, b"current canonical receipt")
    retire.reset_mock()
    decoder.return_value = {**receipt, "target_id": "101"}
    with pytest.raises(bot.InvalidConfirmedReplyReceipt, match="transaction identity changed"):
        bot.remove_confirmed_reply_receipt(receipt)
    decoder.return_value = dict(receipt)
    with pytest.raises(ValueError, match="explicit disposition"):
        bot.remove_confirmed_reply_receipt(receipt)
    failure = ValueError("current JSON decoder failed")
    decoder.side_effect = failure
    with pytest.raises(ValueError) as caught:
        bot.remove_confirmed_reply_receipt(receipt)
    assert caught.value is failure
    retire.assert_not_called()


def _deliver(receipt, state=None):
    return bot.post_conversational_reply_with_durable_identity(
        state=bot.default_state() if state is None else state,
        receipt_template=receipt, reply_text=receipt["reply_text"],
        reply_to_id=receipt["target_id"], made_with_ai=False,
        lane=receipt["candidate_source"],
    )


def test_delivery_keeps_shallow_template_plain_transport_text_and_return_objects(monkeypatch):
    receipt = unit_sending_v4_reply_receipt()
    reply = unit_approved_reply(receipt["reply_context"], text=receipt["reply_text"])
    receipt.update(reply_text=reply, ai_reply_draft=reply.draft_record)
    original = dict(receipt)
    response = {"data": {"id": "999"}}
    remote = Mock(return_value=response)
    install_receipt_bound_x_request_stub(monkeypatch, remote)
    monkeypatch.setattr(bot, "now_epoch", lambda: receipt["attempt_epoch"] + 5)
    trace = []
    observed = {}
    for label, name in (
        ("barrier", "block_if_ambiguous_remote_post"),
        ("write", "write_sending_reply_receipt"),
        ("begin", "begin_confirmed_post_sigint_deferral"),
        ("create", "create_post"),
        ("promote", "promote_sending_reply_receipt"),
        ("end", "end_confirmed_post_sigint_deferral"),
    ):
        original_callback = getattr(bot, name)

        def observe(*args, _label=label, _callback=original_callback, **kwargs):
            if _label == "begin":
                assert bot.load_confirmed_reply_receipt() == ("sending", receipt)
            trace.append(_label)
            result = _callback(*args, **kwargs)
            observed[_label] = (args, kwargs, result)
            return result

        monkeypatch.setattr(bot, name, observe)
    actual_response, confirmed = _deliver(receipt)
    prepared = observed["write"][0][0]
    assert prepared == receipt and prepared is not receipt
    assert all(prepared[key] is value for key, value in original.items())
    assert observed["create"][1]["prepared_conversational_reply_receipt"] is prepared
    assert type(observed["create"][1]["text"]) is str
    assert observed["create"][1]["text"] == reply
    assert observed["promote"][0][0] is prepared
    assert actual_response is observed["create"][2] is response
    assert confirmed is observed["promote"][2]
    assert confirmed["reply_context"] is receipt["reply_context"]
    assert confirmed["confirmation_epoch"] == receipt["attempt_epoch"] + 5
    assert receipt == original
    assert trace[0:4] == ["barrier", "write", "begin", "create"]
    assert trace[-2:] == ["promote", "end"]
    assert observed["end"][0][0] is observed["begin"][2]
    remote.assert_called_once()


def test_delivery_namespace_and_durable_create_failure_precede_sigint_guard(monkeypatch):
    receipt = unit_sending_v4_reply_receipt()
    bot.write_sending_reply_receipt(receipt)
    barrier = Mock(wraps=bot.block_if_ambiguous_remote_post)
    begin = Mock(side_effect=AssertionError("guard started before durable sending receipt"))
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", barrier)
    monkeypatch.setattr(bot, "begin_confirmed_post_sigint_deferral", begin)
    with pytest.raises(bot.InvalidConfirmedReplyReceipt, match="unresolved"):
        _deliver(receipt)
    barrier.assert_not_called()
    bot.remove_confirmed_reply_receipt(receipt, sending_disposition="definite_non_success")
    failure = OSError("durable create failed")
    monkeypatch.setattr(bot, "durable_create_receipt_json", Mock(side_effect=failure))
    with pytest.raises(OSError) as caught:
        _deliver(receipt)
    assert caught.value is failure
    barrier.assert_called_once()
    begin.assert_not_called()


@pytest.mark.parametrize("removal_fails", [False, True])
def test_local_pause_retires_before_ending_guard_and_keeps_native_error_or_cause(monkeypatch, removal_fails):
    receipt = unit_sending_v4_reply_receipt()
    pause = bot.RemoteOperationsPaused("local pause")
    failure = OSError("retirement failed")
    monkeypatch.setattr(bot, "create_post", Mock(side_effect=pause))
    trace = Mock()
    remove = Mock(side_effect=failure) if removal_fails else Mock(wraps=bot.remove_confirmed_reply_receipt)
    end = Mock(wraps=bot.end_confirmed_post_sigint_deferral)
    trace.attach_mock(remove, "remove")
    trace.attach_mock(end, "end")
    monkeypatch.setattr(bot, "remove_confirmed_reply_receipt", remove)
    monkeypatch.setattr(bot, "end_confirmed_post_sigint_deferral", end)
    expected = bot.ConfirmedReplyLocalPersistenceError if removal_fails else bot.RemoteOperationsPaused
    with pytest.raises(expected) as caught:
        _deliver(receipt)
    assert [entry[0] for entry in trace.mock_calls] == ["remove", "end"]
    assert remove.call_args.kwargs == {"sending_disposition": "definite_non_success"}
    if removal_fails:
        assert caught.value.__cause__ is failure
        assert bot.load_confirmed_reply_receipt() == ("sending", receipt)
    else:
        assert caught.value is pause
        assert bot.load_confirmed_reply_receipt() == ("absent", None)


@pytest.mark.parametrize("error_type", [OSError, KeyboardInterrupt])
def test_proved_rejection_claims_before_removal_and_records_ambiguity_before_error(monkeypatch, error_type):
    receipt = unit_sending_v4_reply_receipt()
    monkeypatch.setattr(bot.requests, "request", _existing_reply_target_then_deleted_create(receipt["target_id"]))
    with pytest.raises(bot.ProvedRemotePostNonSuccess) as rejection:
        _deliver(receipt)
    failure = error_type("retirement interrupted")
    trace = Mock()
    for label, name, callback in (
        ("claim", "claim_reply_create_rejection_for_receipt_retirement", Mock(wraps=bot.claim_reply_create_rejection_for_receipt_retirement)),
        ("remove", "remove_confirmed_reply_receipt", Mock(side_effect=failure)),
        ("ambiguity", "record_ambiguous_remote_post", Mock(wraps=bot.record_ambiguous_remote_post)),
    ):
        trace.attach_mock(callback, label)
        monkeypatch.setattr(bot, name, callback)
    expected = bot.ConfirmedReplyLocalPersistenceError if error_type is OSError else error_type
    with pytest.raises(expected) as caught:
        bot.retire_proved_rejected_conversational_reply_receipt(receipt, rejection.value)
    assert [entry[0] for entry in trace.mock_calls] == ["claim", "remove", "ambiguity"]
    assert trace.claim.call_args.args[0] is rejection.value.remote_non_success_proof
    assert trace.claim.call_args.kwargs["receipt"] is receipt
    assert trace.remove.call_args.args[0] is receipt
    if error_type is OSError:
        assert caught.value.__cause__ is failure
    else:
        assert caught.value is failure
    assert bot.load_confirmed_reply_receipt() == ("sending", receipt)
    assert bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()
    assert bot.ambiguous_remote_post_is_blocking()


@pytest.mark.parametrize("canonical_committed", [True, False])
def test_backup_failure_allows_fallback_only_with_matching_canonical_state(monkeypatch, canonical_committed):
    receipt = unit_sending_v4_reply_receipt()
    state = bot.default_state()
    remote = Mock(return_value={"data": {"id": "999"}})
    install_receipt_bound_x_request_stub(monkeypatch, remote)
    promotion_error = OSError("promotion failed")
    backup_error = bot.StateBackupWriteError("backup failed")
    monkeypatch.setattr(bot, "promote_sending_reply_receipt", Mock(side_effect=promotion_error))
    original_save = bot.save_state

    def save_then_fail(actual_state, *, durable):
        assert actual_state is state and durable is True
        if canonical_committed:
            original_save(actual_state, durable=True)
        raise backup_error

    monkeypatch.setattr(bot, "save_state", save_then_fail)
    trace = Mock()
    for label, name in (
        ("journal", "retire_lane_transport_journal_if_present"),
        ("receipt", "remove_confirmed_reply_receipt"),
        ("end", "end_confirmed_post_sigint_deferral"),
    ):
        callback = Mock(wraps=getattr(bot, name))
        trace.attach_mock(callback, label)
        monkeypatch.setattr(bot, name, callback)
    expected = bot.ConfirmedReplyLocalPersistenceError if canonical_committed else bot.UnrecoverableConfirmedReplyPersistenceError
    with pytest.raises(expected) as caught:
        _deliver(receipt, state)
    assert caught.value.__cause__ is (promotion_error if canonical_committed else backup_error)
    remote.assert_called_once()
    if canonical_committed:
        assert [entry[0] for entry in trace.mock_calls] == ["journal", "receipt", "end"]
        assert trace.receipt.call_args.kwargs == {"sending_disposition": "confirmed_state_fallback"}
        assert trace.journal.call_args.kwargs["receipt"] is trace.receipt.call_args.args[0]
        assert bot.json_file_matches(bot.STATE_FILE, state)
        assert state["replied_to_ids"] == [receipt["target_id"]]
        assert bot.load_confirmed_reply_receipt() == ("absent", None)
    else:
        assert [entry[0] for entry in trace.mock_calls] == ["end"]
        assert bot.load_confirmed_reply_receipt() == ("sending", receipt)
        assert bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()
        assert bot.ambiguous_remote_post_is_blocking()


def test_incomplete_fallback_without_receipt_or_marker_retains_sigint_and_promotion_cause(monkeypatch):
    receipt = unit_sending_v4_reply_receipt()
    remote = Mock(return_value={"data": {"id": "999"}})
    install_receipt_bound_x_request_stub(monkeypatch, remote)
    promotion_error = OSError("source disappeared during promotion")
    original_atomic_write = bot.atomic_write_json
    original_handler = signal.getsignal(signal.SIGINT)

    def lose_source(*_args, **_kwargs):
        bot.CONFIRMED_REPLY_RECEIPT_FILE.unlink()
        raise promotion_error

    def fail_marker(path, value, **kwargs):
        if path == bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE:
            raise OSError("marker failed")
        return original_atomic_write(path, value, **kwargs)

    monkeypatch.setattr(bot, "promote_sending_reply_receipt", lose_source)
    monkeypatch.setattr(bot, "atomic_write_json", fail_marker)
    # An acknowledged save without its canonical file must fail completeness.
    monkeypatch.setattr(bot, "save_state", Mock())
    ended = Mock(wraps=bot.end_confirmed_post_sigint_deferral)
    retained = Mock(wraps=bot.retain_sigint_deferral_without_durable_barrier)
    monkeypatch.setattr(bot, "end_confirmed_post_sigint_deferral", ended)
    monkeypatch.setattr(bot, "retain_sigint_deferral_without_durable_barrier", retained)
    try:
        with pytest.raises(bot.UnrecoverableConfirmedReplyPersistenceError) as caught:
            _deliver(receipt)
        assert caught.value.__cause__ is promotion_error
        ended.assert_not_called()
        retained.assert_called_once()
        guard = retained.call_args.kwargs["guard"]
        assert retained.call_args.kwargs["lane"] == "mention"
        assert bot._RETAINED_CONFIRMED_POST_SIGINT_GUARD is guard
        assert getattr(signal.getsignal(signal.SIGINT), "__self__", None) is guard
        assert bot.load_confirmed_reply_receipt() == ("absent", None)
        assert not bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()
        assert bot.ambiguous_remote_post_is_blocking()
        remote.assert_called_once()
    finally:
        signal.signal(signal.SIGINT, original_handler)
