from __future__ import annotations

from pathlib import Path
import functools
import inspect
import signal
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from tests.helpers.adapter_assertions import assert_adapters_forward_current_dependencies

import mrs_bot_reply_delivery as delivery
import mrs_bot_reply_receipt_values as values
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import (
    install_receipt_bound_x_request_stub,
    isolate_bot_runtime,  # noqa: F401
)
from tests.helpers.reply_fixtures import (
    patch_reply_owner_method, patch_reply_receipt_method, unit_approved_reply, unit_sending_v4_reply_receipt,
)
from tests.helpers.x_response_fixtures import (
    _existing_reply_target_then_deleted_create,
    isolate_remote_write_state,
)


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, copy, dataclasses, json, io, logging, os, random, re, socket, sys
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('reply delivery import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_reply_delivery', 'mrs_bot_durable_json_io'}:
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
        "retire_proved_rejected_conversational_reply_receipt",
    )
    assert_adapters_forward_current_dependencies(
        monkeypatch, bot=bot, implementation=delivery, names=names,
    )


def test_receipt_owner_binds_current_runtime_without_running_operations(monkeypatch):
    bindings = {
        "path": "CONFIRMED_REPLY_RECEIPT_FILE",
        "read_json": "load_receipt_json_no_follow",
        "log": "log",
        "retirement_is_blocking": "remote_receipt_retirement_is_blocking",
        "invalid_receipt": "InvalidConfirmedReplyReceipt",
        "namespace_entry_exists": "receipt_namespace_entry_exists",
        "create_json": "durable_create_receipt_json",
        "unresolved_sending": "UnresolvedSendingReplyReceipt",
        "bind_confirmed_source": "bind_confirmed_transport_source",
        "journal_path": "journal_path_for_receipt",
        "validator_id": "TRANSPORT_SOURCE_VALIDATOR_ID",
        "transport_validator": "transport_source_semantic_validator",
        "legacy_transport_validator": "_legacy_conversational_transport_source_semantic_validator",
        "transport_journal_error": "TransportJournalError",
        "replace_bound_source": "replace_bound_source_receipt",
        "mutation_authority": "transaction_mutation_authority",
    }
    value_factory = Mock()
    monkeypatch.setattr(bot, "_reply_receipt_values_owner", value_factory)
    owners = []
    for _ in range(2):
        current = {field: Mock() for field in bindings}
        for field, root_name in bindings.items():
            monkeypatch.setattr(bot, root_name, current[field])
        value_factory.return_value = Mock(spec=values.ReplyReceiptValues)
        owner = bot._reply_receipts_owner()
        owners.append(owner)
        assert set(vars(owner)) == set(bindings) | {"values", "current_receipts"}
        assert all(getattr(owner, name) is value for name, value in current.items())
        assert owner.values is value_factory.return_value
        value_factory.assert_called_once_with()
        value_factory.reset_mock()
        assert not owner.values.mock_calls
        assert all(not value.mock_calls for value in current.values())
    assert owners[0].path is not owners[1].path
    newer_factory = Mock(return_value=object())
    monkeypatch.setattr(bot, "_reply_receipts_owner", newer_factory)
    assert owners[0].current_receipts() is newer_factory.return_value
    newer_factory.assert_called_once_with()


@pytest.mark.parametrize("name,method,operation_options", [
    ("load_confirmed_reply_receipt", "load", {}),
    ("write_confirmed_reply_receipt", "write", {"confirmed": True}),
    ("write_sending_reply_receipt", "write", {"confirmed": False}),
    ("promote_sending_reply_receipt", "promote", {}),
    ("_promote_legacy_sending_reply_receipt_from_confirmed_transport", "promote", {"legacy_recovery": True}),
])
def test_receipt_adapters_bind_current_owner_and_preserve_arguments_results_errors(
    monkeypatch, name, method, operation_options,
):
    adapter = getattr(bot, name)
    public = inspect.signature(adapter).parameters
    args = tuple(object() for parameter in public.values()
                 if parameter.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD)
    options = {key: object() for key, parameter in public.items()
               if parameter.kind == inspect.Parameter.KEYWORD_ONLY}
    factory = Mock()
    monkeypatch.setattr(bot, "_reply_receipts_owner", factory)
    for _ in range(2):
        owner = Mock(spec=delivery.ReplyReceipts)
        factory.return_value = owner
        operation = getattr(owner, method)
        assert adapter(*args, **options) is operation.return_value
        factory.assert_called_once_with()
        operation.assert_called_once_with(*args, **options, **operation_options)
        assert all(actual is expected for actual, expected in zip(operation.call_args.args, args))
        assert all(operation.call_args.kwargs[key] is value for key, value in options.items())
        factory.reset_mock()
    failure = TypeError("current receipt owner adapter")
    operation.side_effect = failure
    with pytest.raises(TypeError) as caught:
        adapter(*args, **options)
    assert caught.value is failure


def test_post_adapter_preserves_current_dependencies_and_lazy_receipt_operations(monkeypatch):
    adapter = bot.post_conversational_reply_with_durable_identity
    public = inspect.signature(adapter).parameters
    dependencies = (
        inspect.signature(delivery.post_conversational_reply_with_durable_identity).parameters.keys()
        - public.keys() - {"receipt_values", "completion", "receipts"}
    )
    options = {key: object() for key in public}
    implementation = Mock(return_value=object())
    value_factory = Mock()
    completion_factory = Mock()
    receipt_factory = Mock()
    monkeypatch.setattr(bot, "_reply_completion_owner", completion_factory)
    monkeypatch.setattr(bot, "_reply_receipts_owner", receipt_factory)
    monkeypatch.setattr(bot, "_reply_receipt_values_owner", value_factory)
    monkeypatch.setattr(delivery, "post_conversational_reply_with_durable_identity", implementation)
    for _ in range(2):
        value_factory.return_value = Mock(spec=values.ReplyReceiptValues)
        completion_factory.return_value = object()
        current = {key: object() for key in dependencies}
        for key, value in current.items():
            monkeypatch.setattr(bot, key, value)
        assert adapter(**options) is implementation.return_value
        value_factory.assert_called_once_with()
        completion_factory.assert_called_once_with()
        value_factory.reset_mock()
        completion_factory.reset_mock()
        receipt_factory.assert_not_called()
        actual_args, actual_kwargs = implementation.call_args
        assert not actual_args
        expected = {
            **options, **current, "receipt_values": value_factory.return_value,
            "completion": completion_factory.return_value,
        }
        assert actual_kwargs.keys() == expected.keys() | {"receipts"}
        assert all(actual_kwargs[key] is value for key, value in expected.items())
        assert not value_factory.return_value.mock_calls
    newer_factory = Mock(return_value=object())
    monkeypatch.setattr(bot, "_reply_receipts_owner", newer_factory)
    assert actual_kwargs["receipts"]() is newer_factory.return_value
    newer_factory.assert_called_once_with()
    failure = TypeError("current delivery dependency")
    implementation.side_effect = failure
    with pytest.raises(TypeError) as caught:
        adapter(**options)
    assert caught.value is failure


@pytest.mark.parametrize("accepted,status", [(0, "sending"), (1, "legacy_sending"), (2, "valid"), (3, "valid"), (None, "invalid")])
def test_loader_preserves_validation_order_and_original_receipt(monkeypatch, accepted, status):
    receipt = unit_sending_v4_reply_receipt()
    reader = Mock(return_value=(True, receipt))
    monkeypatch.setattr(bot, "load_receipt_json_no_follow", reader)
    trace = Mock()
    names = (
        "sending_is_valid", "legacy_sending_is_valid",
        "confirmed_is_valid", "legacy_confirmed_is_valid",
    )
    for index, name in enumerate(names):
        validator = Mock(return_value=index == accepted)
        trace.attach_mock(validator, name)
        patch_reply_owner_method(monkeypatch, values.ReplyReceiptValues, name, validator)
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
    patch_reply_owner_method(monkeypatch, values.ReplyReceiptValues, "sending_is_valid", validator)
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
        if label == "validation":
            patch_reply_owner_method(monkeypatch, values.ReplyReceiptValues, f"{lifecycle}_is_valid", callback)
        else:
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
    trace.reset_mock()
    trace.namespace.return_value = False
    trace.validation.return_value = False
    with pytest.raises(RuntimeError, match="failed .*validation"):
        writer(receipt)
    assert [entry[0] for entry in trace.mock_calls] == ["retirement", "namespace", "validation"]
    trace.reset_mock()
    failure = TypeError("receipt validation failed")
    trace.validation.side_effect = failure
    with pytest.raises(TypeError) as caught:
        writer(receipt)
    assert caught.value is failure
    assert [entry[0] for entry in trace.mock_calls] == ["retirement", "namespace", "validation"]
    trace.validation.side_effect = None
    trace.validation.return_value = True
    race = FileExistsError("entry appeared")
    trace.create.side_effect = race
    with pytest.raises(bot.InvalidConfirmedReplyReceipt, match="appeared during publication") as caught:
        writer(receipt)
    assert caught.value.__cause__ is race


def test_removal_keeps_secure_reader_equality_disposition_and_canonical_retirement(monkeypatch):
    receipt = unit_sending_v4_reply_receipt()
    reader = Mock(return_value=(True, dict(receipt)))
    retire = Mock()
    monkeypatch.setattr(bot, "load_receipt_json_no_follow", reader)
    monkeypatch.setattr(bot, "retire_current_source_receipt", retire)
    canonical = Mock(return_value=b"current canonical receipt")
    monkeypatch.setattr(delivery, "canonical_atomic_json_bytes", canonical)
    bot.remove_confirmed_reply_receipt(receipt, sending_disposition="definite_non_success")
    reader.assert_called_once_with(bot.CONFIRMED_REPLY_RECEIPT_FILE)
    canonical.assert_called_once_with(receipt)
    assert canonical.call_args.args[0] is receipt
    retire.assert_called_once_with(bot.CONFIRMED_REPLY_RECEIPT_FILE, b"current canonical receipt",
                                   commit_proof=None, disposition="definite_non_success")
    retire.reset_mock()
    reader.return_value = (True, {**receipt, "target_id": "101"})
    with pytest.raises(bot.InvalidConfirmedReplyReceipt, match="transaction identity changed"):
        bot.remove_confirmed_reply_receipt(receipt)
    reader.return_value = (True, dict(receipt))
    with pytest.raises(ValueError, match="explicit disposition"):
        bot.remove_confirmed_reply_receipt(receipt)
    failure = ValueError("current JSON decoder failed")
    reader.side_effect = failure
    with pytest.raises(ValueError) as caught:
        bot.remove_confirmed_reply_receipt(receipt)
    assert caught.value is failure
    reader.side_effect = None
    reader.return_value = (False, None)
    with pytest.raises(FileNotFoundError):
        bot.remove_confirmed_reply_receipt(receipt)
    retire.assert_not_called()


def test_root_removal_binds_real_commit_proof_and_current_secure_reader(monkeypatch):
    from mrs_bot_state_generation import record_receipt_commit

    sending = unit_sending_v4_reply_receipt()
    receipt = bot._confirmed_reply_receipt_from_sending(
        sending, reply_post_id="999", confirmation_epoch=sending["attempt_epoch"],
    )
    state = bot.default_state()
    record_receipt_commit(state, receipt)
    proof = bot.save_state(state, durable=True)
    name = "remove_confirmed_reply_receipt"
    dependencies = inspect.signature(delivery.remove_confirmed_reply_receipt).parameters.keys() - inspect.signature(bot.remove_confirmed_reply_receipt).parameters.keys()
    for _ in range(2):
        with monkeypatch.context() as patch:
            callback = Mock(return_value=object())
            patch.setattr(delivery, name, callback)
            current = {key: Mock() for key in dependencies}
            for key, value in current.items():
                patch.setattr(bot, key, value)
            assert bot.remove_confirmed_reply_receipt(receipt, commit_proof=proof) is callback.return_value
            authority = callback.call_args.kwargs["retire_current_source_receipt"]
            assert isinstance(authority, functools.partial)
            assert authority.func is current["retire_current_source_receipt"]
            assert authority.args == () and authority.keywords == {"commit_proof": proof}
            callback.assert_called_once_with(receipt, sending_disposition=None,
                **{**current, "retire_current_source_receipt": authority})
            with pytest.raises(RuntimeError, match="exact durable state commit"):
                bot.remove_confirmed_reply_receipt(receipt)
            with pytest.raises(RuntimeError, match="does not bind this exact receipt"):
                bot.remove_confirmed_reply_receipt({**receipt, "reply_post_id": "1000"}, commit_proof=proof)
            failure = TypeError("current owner failure")
            callback.side_effect = failure
            with pytest.raises(TypeError) as caught:
                bot.remove_confirmed_reply_receipt(receipt, commit_proof=proof)
            assert caught.value is failure


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
        method = {"write": "write", "promote": "promote"}.get(label)
        original_callback = (
            getattr(bot._reply_receipts_owner(), method) if method else getattr(bot, name)
        )

        def observe(*args, _label=label, _callback=original_callback, **kwargs):
            if _label == "begin":
                assert bot.load_confirmed_reply_receipt() == ("sending", receipt)
            trace.append(_label)
            result = _callback(*args, **kwargs)
            observed[_label] = (args, kwargs, result)
            return result

        if method:
            patch_reply_receipt_method(monkeypatch, bot, method, observe)
        else:
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


def test_delivery_owned_template_failure_precedes_namespace_barrier_and_guard(monkeypatch):
    receipt = unit_sending_v4_reply_receipt()
    failure = ValueError("reviewed receipt values invalid")
    prepare = Mock(side_effect=failure)
    patch_reply_owner_method(monkeypatch, values.ReplyReceiptValues, "prepare_sending_template", prepare)
    runtime = Mock(side_effect=AssertionError("runtime boundary entered for invalid template"))
    for name in ("receipt_namespace_entry_exists", "block_if_ambiguous_remote_post",
                 "_reply_receipts_owner", "begin_confirmed_post_sigint_deferral"):
        monkeypatch.setattr(bot, name, runtime)
    with pytest.raises(ValueError) as caught:
        _deliver(receipt)
    assert caught.value is failure
    prepare.assert_called_once_with(receipt, lane="mention")
    assert prepare.call_args.args[0] is receipt
    runtime.assert_not_called()


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
    patch_reply_receipt_method(monkeypatch, bot, "promote", Mock(side_effect=promotion_error))
    original_save = bot.save_state

    def save_then_fail(actual_state, *, durable):
        assert actual_state is state and durable is True
        if canonical_committed:
            backup_error.commit_proof = original_save(actual_state, durable=True)
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
        assert trace.receipt.call_args.kwargs == {"sending_disposition": "confirmed_state_fallback", "commit_proof": backup_error.commit_proof}
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

    patch_reply_receipt_method(monkeypatch, bot, "promote", lose_source)
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


@pytest.mark.parametrize("namespace", ["symlink", "hardlink", "writable", "unsafe_directory"])
def test_root_reply_retirement_rejects_unsafe_reader_authority(monkeypatch, tmp_path, namespace):
    import os

    receipt = unit_sending_v4_reply_receipt()
    path = tmp_path / "receipt.json"
    path.write_bytes(bot.canonical_atomic_json_bytes(receipt))
    if namespace == "symlink":
        target = tmp_path / "target.json"
        path.rename(target)
        path.symlink_to(target)
    elif namespace == "hardlink":
        os.link(path, tmp_path / "extra-link.json")
    elif namespace == "writable":
        path.chmod(0o660)
    else:
        tmp_path.chmod(0o770)
    retire = Mock(side_effect=AssertionError("unsafe receipt reached retirement"))
    monkeypatch.setattr(bot, "CONFIRMED_REPLY_RECEIPT_FILE", path)
    monkeypatch.setattr(bot, "retire_current_source_receipt", retire)
    try:
        with pytest.raises(bot.UnsafeReceiptNamespace):
            bot.remove_confirmed_reply_receipt(receipt, sending_disposition="definite_non_success")
    finally:
        tmp_path.chmod(0o700)
    retire.assert_not_called()


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("failure_stage", [None, "source", "validation", "replace"])
def test_promotion_families_share_source_checks_before_projection_and_replacement(monkeypatch, legacy, failure_stage):
    sending = unit_sending_v4_reply_receipt()
    confirmed = {**sending, "reply_post_id": "999"}
    epoch = sending["attempt_epoch"]
    binding = SimpleNamespace(receipt_document=sending, receipt_bytes=b"sending")
    recovery = SimpleNamespace(
        details=SimpleNamespace(lane="conversational_reply", post_id="999", confirmation_epoch=epoch),
        source_binding=binding,
    )
    trace = Mock()
    trace.load.return_value = ("legacy_sending" if legacy else "sending", sending)
    trace.path.return_value = Path("unit-journal.json")
    trace.bind.return_value = recovery
    trace.canonical.side_effect = lambda receipt: b"sending" if receipt is sending else b"confirmed"
    trace.project.return_value = confirmed
    trace.validate.return_value = failure_stage != "validation"
    trace.authority.return_value = object()
    owner = Mock(spec=values.ReplyReceiptValues)
    owner.confirmed_from_sending = trace.project
    setattr(owner, "legacy_confirmed_is_valid" if legacy else "confirmed_is_valid", trace.validate)
    monkeypatch.setattr(bot, "_reply_receipt_values_owner", Mock(return_value=owner))
    for name, callback in (
        ("journal_path_for_receipt", trace.path),
        ("bind_confirmed_transport_source", trace.bind),
        ("canonical_atomic_json_bytes", trace.canonical),
        ("transaction_mutation_authority", trace.authority),
        ("replace_bound_source_receipt", trace.replace),
    ):
        target = delivery if name == "canonical_atomic_json_bytes" else bot
        monkeypatch.setattr(target, name, callback)
    patch_reply_receipt_method(monkeypatch, bot, "load", trace.load)
    logger = Mock()
    monkeypatch.setattr(bot, "log", logger)
    transport_validator = object()
    validator_name = ("_legacy_conversational_transport_source_semantic_validator"
                      if legacy else "transport_source_semantic_validator")
    monkeypatch.setattr(bot, validator_name, transport_validator)
    promote = (bot._promote_legacy_sending_reply_receipt_from_confirmed_transport
               if legacy else bot.promote_sending_reply_receipt)
    if failure_stage == "source":
        binding.receipt_bytes = b"different exact transaction"
    replacement_error = OSError("atomic replace failed")
    if failure_stage == "replace":
        trace.replace.side_effect = replacement_error

    if failure_stage is None:
        assert promote(sending, reply_post_id="999", confirmation_epoch=epoch) is confirmed
    else:
        expected = {"source": bot.TransportJournalError, "validation": RuntimeError, "replace": OSError}[failure_stage]
        with pytest.raises(expected) as caught:
            promote(sending, reply_post_id="999", confirmation_epoch=epoch)
        if failure_stage == "replace":
            assert caught.value is replacement_error
        logger.warning.assert_not_called()

    stages = ["load", "path", "bind", "canonical"]
    if failure_stage != "source":
        stages += ["project", "validate"]
    if failure_stage not in {"source", "validation"}:
        stages += ["canonical", "authority", "replace"]
    assert [entry[0] for entry in trace.mock_calls] == stages
    assert trace.bind.call_args.kwargs["validator"] is transport_validator
    assert trace.canonical.call_args_list[0].args[0] is sending
    if failure_stage != "source":
        assert trace.project.call_args.args[0] is sending
        trace.project.assert_called_once_with(sending, reply_post_id="999", confirmation_epoch=epoch)
        assert trace.validate.call_args.args[0] is confirmed
    if failure_stage not in {"source", "validation"}:
        assert trace.canonical.call_args_list[1].args[0] is confirmed
        trace.authority.assert_called_once_with(
            "confirmed legacy conversational source receipt promotion"
            if legacy else "confirmed conversational source receipt promotion"
        )
        trace.replace.assert_called_once_with(binding, b"confirmed", mutation_authority=trace.authority.return_value)
    if failure_stage is None:
        logger.warning.assert_called_once()


def test_delivery_binds_publication_after_barrier_runtime_changes(monkeypatch, tmp_path):
    receipt = unit_sending_v4_reply_receipt()
    current_path = tmp_path / "current-reply-receipt.json"
    failure = OSError("publication interrupted")
    current_create = Mock(side_effect=failure)
    current_values = Mock(spec=values.ReplyReceiptValues)
    current_values.sending_is_valid.return_value = True
    old_create = Mock(side_effect=AssertionError("stale publication callback"))
    begin = Mock(side_effect=AssertionError("guard entered before publication"))
    namespace = Mock(return_value=False)
    monkeypatch.setattr(bot, "receipt_namespace_entry_exists", namespace)
    monkeypatch.setattr(bot, "remote_receipt_retirement_is_blocking", Mock(return_value=False))
    monkeypatch.setattr(bot, "durable_create_receipt_json", old_create)
    monkeypatch.setattr(bot, "begin_confirmed_post_sigint_deferral", begin)

    def refresh_runtime():
        monkeypatch.setattr(bot, "CONFIRMED_REPLY_RECEIPT_FILE", current_path)
        monkeypatch.setattr(bot, "durable_create_receipt_json", current_create)
        monkeypatch.setattr(bot, "_reply_receipt_values_owner", Mock(return_value=current_values))

    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", refresh_runtime)
    with pytest.raises(OSError) as caught:
        _deliver(receipt)
    assert caught.value is failure
    prepared = current_create.call_args.args[1]
    assert prepared == receipt and prepared is not receipt
    assert current_create.call_args.args[0] is current_path
    assert namespace.call_args.args[0] is current_path
    current_values.sending_is_valid.assert_called_once_with(prepared)
    assert current_values.sending_is_valid.call_args.args[0] is prepared
    old_create.assert_not_called()
    begin.assert_not_called()


def test_publication_keeps_its_bindings_when_retirement_check_changes_runtime(monkeypatch, tmp_path):
    receipt = unit_sending_v4_reply_receipt()
    path = tmp_path / "bound-reply-receipt.json"
    later_path = tmp_path / "later-reply-receipt.json"
    original_values = Mock(spec=values.ReplyReceiptValues)
    original_values.sending_is_valid.return_value = True
    later_values = Mock(spec=values.ReplyReceiptValues)
    later_values.sending_is_valid.return_value = True
    namespace = Mock(return_value=False)
    create = Mock()
    logger = Mock()
    later_reader = Mock(return_value=(True, receipt))
    forbidden = Mock(side_effect=AssertionError("active publication rebound its runtime"))
    monkeypatch.setattr(bot, "CONFIRMED_REPLY_RECEIPT_FILE", path)
    monkeypatch.setattr(bot, "_reply_receipt_values_owner", Mock(return_value=original_values))
    monkeypatch.setattr(bot, "receipt_namespace_entry_exists", namespace)
    monkeypatch.setattr(bot, "durable_create_receipt_json", create)
    monkeypatch.setattr(bot, "log", logger)

    def retirement_check():
        monkeypatch.setattr(bot, "CONFIRMED_REPLY_RECEIPT_FILE", later_path)
        monkeypatch.setattr(bot, "_reply_receipt_values_owner", Mock(return_value=later_values))
        monkeypatch.setattr(bot, "load_receipt_json_no_follow", later_reader)
        monkeypatch.setattr(bot, "receipt_namespace_entry_exists", forbidden)
        monkeypatch.setattr(bot, "durable_create_receipt_json", forbidden)
        return False

    monkeypatch.setattr(bot, "remote_receipt_retirement_is_blocking", retirement_check)
    bot.write_sending_reply_receipt(receipt)
    namespace.assert_called_once_with(path)
    create.assert_called_once_with(path, receipt)
    assert create.call_args.args[0] is path and create.call_args.args[1] is receipt
    original_values.sending_is_valid.assert_called_once_with(receipt)
    logger.warning.assert_called_once()
    assert logger.warning.call_args.args[-1] is path
    assert not later_values.mock_calls
    assert bot.load_confirmed_reply_receipt() == ("sending", receipt)
    later_reader.assert_called_once_with(later_path)
    later_values.sending_is_valid.assert_called_once_with(receipt)
    forbidden.assert_not_called()


@pytest.mark.parametrize("legacy", [False, True])
def test_promotion_refreshes_nested_load_but_keeps_bound_promotion_authorities(monkeypatch, tmp_path, legacy):
    sending = unit_sending_v4_reply_receipt()
    confirmed = {**sending, "reply_post_id": "999"}
    epoch = sending["attempt_epoch"]
    path = tmp_path / "bound-promotion.json"
    nested_path = tmp_path / "nested-load.json"
    later_path = tmp_path / "after-read.json"
    promotion_values = Mock(spec=values.ReplyReceiptValues)
    promotion_values.confirmed_from_sending.return_value = confirmed
    setattr(promotion_values, "legacy_confirmed_is_valid" if legacy else "confirmed_is_valid", Mock(return_value=True))
    load_values = Mock(spec=values.ReplyReceiptValues)
    load_values.sending_is_valid.return_value = not legacy
    load_values.legacy_sending_is_valid.return_value = legacy
    forbidden = Mock(side_effect=AssertionError("an active operation rebound its authority"))
    binding = SimpleNamespace(receipt_document=sending, receipt_bytes=b"sending")
    recovery = SimpleNamespace(
        details=SimpleNamespace(lane="conversational_reply", post_id="999", confirmation_epoch=epoch),
        source_binding=binding,
    )
    bind = Mock(return_value=recovery)
    journal = Mock(return_value=tmp_path / "journal.json")
    replace = Mock()
    authority = Mock(return_value=object())
    validator = object()
    validator_name = ("_legacy_conversational_transport_source_semantic_validator"
                      if legacy else "transport_source_semantic_validator")
    monkeypatch.setattr(bot, "CONFIRMED_REPLY_RECEIPT_FILE", path)
    monkeypatch.setattr(bot, "_reply_receipt_values_owner", Mock(return_value=promotion_values))
    monkeypatch.setattr(bot, "load_receipt_json_no_follow", forbidden)
    monkeypatch.setattr(bot, "bind_confirmed_transport_source", bind)
    monkeypatch.setattr(bot, "journal_path_for_receipt", journal)
    monkeypatch.setattr(bot, "replace_bound_source_receipt", replace)
    monkeypatch.setattr(bot, "transaction_mutation_authority", authority)
    monkeypatch.setattr(bot, validator_name, validator)
    monkeypatch.setattr(delivery, "canonical_atomic_json_bytes", lambda receipt: b"sending" if receipt is sending else b"confirmed")
    promotion = bot._reply_receipts_owner()
    monkeypatch.setattr(bot, "CONFIRMED_REPLY_RECEIPT_FILE", nested_path)
    monkeypatch.setattr(bot, "_reply_receipt_values_owner", Mock(return_value=load_values))

    def read_current(actual_path):
        assert actual_path is nested_path
        monkeypatch.setattr(bot, "CONFIRMED_REPLY_RECEIPT_FILE", later_path)
        monkeypatch.setattr(bot, "_reply_receipt_values_owner", forbidden)
        monkeypatch.setattr(bot, "bind_confirmed_transport_source", forbidden)
        monkeypatch.setattr(bot, "replace_bound_source_receipt", forbidden)
        monkeypatch.setattr(bot, "transaction_mutation_authority", forbidden)
        monkeypatch.setattr(bot, validator_name, object())
        return True, sending

    reader = Mock(side_effect=read_current)
    monkeypatch.setattr(bot, "load_receipt_json_no_follow", reader)
    result = promotion.promote(
        sending, reply_post_id="999", confirmation_epoch=epoch, legacy_recovery=legacy,
    )
    assert result is confirmed
    reader.assert_called_once_with(nested_path)
    load_values.sending_is_valid.assert_called_once_with(sending)
    if legacy:
        load_values.legacy_sending_is_valid.assert_called_once_with(sending)
    else:
        load_values.legacy_sending_is_valid.assert_not_called()
    journal.assert_called_once_with(path)
    assert bind.call_args.kwargs["receipt_path"] is path
    assert bind.call_args.kwargs["validator"] is validator
    assert promotion_values.confirmed_from_sending.call_args.args[0] is sending
    replace.assert_called_once_with(binding, b"confirmed", mutation_authority=authority.return_value)
    forbidden.assert_not_called()


@pytest.mark.parametrize("sending_fallback", [False, True])
def test_retirement_proof_gate_precedes_receipt_read_and_authority(monkeypatch, sending_fallback):
    from mrs_bot_state_generation import record_receipt_commit

    sending = unit_sending_v4_reply_receipt()
    receipt = sending if sending_fallback else bot._confirmed_reply_receipt_from_sending(
        sending, reply_post_id="999", confirmation_epoch=sending["attempt_epoch"],
    )
    state = bot.default_state()
    record_receipt_commit(state, receipt)
    proof = bot.save_state(state, durable=True)
    read = Mock(side_effect=AssertionError("unproved receipt was read"))
    retire = Mock(side_effect=AssertionError("unproved receipt was retired"))
    monkeypatch.setattr(bot, "load_receipt_json_no_follow", read)
    monkeypatch.setattr(bot, "retire_current_source_receipt", retire)
    disposition = "confirmed_state_fallback" if sending_fallback else None
    with pytest.raises(RuntimeError, match="exact durable state commit"):
        bot.remove_confirmed_reply_receipt(receipt, sending_disposition=disposition)
    with pytest.raises(RuntimeError, match="does not bind this exact receipt"):
        bot.remove_confirmed_reply_receipt(
            {**receipt, "target_id": "101"},
            sending_disposition=disposition,
            commit_proof=proof,
        )
    read.assert_not_called()
    retire.assert_not_called()
