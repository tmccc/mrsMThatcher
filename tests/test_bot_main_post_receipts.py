from __future__ import annotations

import copy
import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_main_post_receipts as receipts
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import (
    isolate_bot_runtime,  # noqa: F401
    schema_current_main_attempt,
    valid_regular_receipt_v2,
)


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, dataclasses, datetime, hashlib, io, json, logging, os, random, socket, sys, time, typing
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('main-post receipts import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_main_post_receipts', 'mrs_bot_main_post_attempt_values', 'mrs_bot_durable_json_io', 'mrs_bot_asset_metadata', 'mrs_bot_receipt_primitives'}:
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = os.lstat = os.stat = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = logging.getLogger = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
time.time = time.monotonic = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_main_post_receipts
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


OPERATIONS = {
    "main_post_attempt_is_semantically_valid": "attempt_is_valid",
    "regular_post_receipt_is_semantically_valid": "regular_is_valid",
    "confirmed_pending_schedule_receipt_is_semantically_valid": "pending_is_valid",
    "materialize_bound_regular_schedule_receipt": "materialize_regular",
    "materialize_bound_meme_schedule_receipt": "materialize_meme",
    "meme_post_receipt_is_semantically_valid": "meme_is_valid",
}

OWNER_FIELDS = {
    "schedule_timezone": "MAIN_POST_SCHEDULE_TIMEZONE",
    "schedule_modes": "MEME_SCHEDULE_MODES",
    "schedule_version": "MEME_SCHEDULE_VERSION",
    "bound_meme_state_is_valid": "bound_meme_schedule_state_is_valid",
    "safe_schedule_date": "safe_bound_schedule_date_str",
    "valid_epoch": "valid_receipt_epoch",
    "invalid_regular_receipt": "InvalidRegularPostReceipt",
    "invalid_meme_receipt": "InvalidMemePostReceipt",
    "bound_schedule_datetime": "bound_schedule_datetime",
}


def patch_receipt_operation(monkeypatch, name, callback):
    """Observe owned sibling operations without replacing public adapters."""
    monkeypatch.setattr(
        receipts.MainPostReceiptValues, OPERATIONS[name],
        lambda self, *args, **kwargs: callback(*args, **kwargs),
    )


@pytest.mark.parametrize(
    "name, signature",
    [
        ("main_post_attempt_is_semantically_valid", "(data: 'object') -> 'bool'"),
        ("regular_post_receipt_is_semantically_valid", "(data: 'dict') -> 'bool'"),
        (
            "confirmed_pending_schedule_receipt_is_semantically_valid",
            "(data: 'object', *, expected_lane: 'str | None' = None) -> 'bool'",
        ),
        (
            "materialize_bound_regular_schedule_receipt",
            "(pending: 'dict', *, _validate_result: 'bool' = True) -> 'dict'",
        ),
        (
            "materialize_bound_meme_schedule_receipt",
            "(pending: 'dict', *, _validate_result: 'bool' = True) -> 'dict'",
        ),
        ("meme_post_receipt_is_semantically_valid", "(data: 'dict') -> 'bool'"),
    ],
)
def test_adapters_resolve_current_owner_preserving_defaults_references_and_errors(
    monkeypatch, name, signature,
):
    adapter = getattr(bot, name)
    original_parameters = inspect.signature(adapter).parameters
    assert str(inspect.signature(adapter)) == signature
    owned = inspect.signature(getattr(receipts.MainPostReceiptValues, OPERATIONS[name]))
    assert set(owned.parameters) == {"self", *original_parameters}
    value = {"original": []}
    defaults = {key: parameter.default for key, parameter in original_parameters.items()
                if parameter.kind is inspect.Parameter.KEYWORD_ONLY}
    for explicit in (False, True):
        with monkeypatch.context() as patch:
            operation = Mock(return_value={"original return": []})
            owner = SimpleNamespace(**{OPERATIONS[name]: operation})
            factory = Mock(return_value=owner)
            patch.setattr(bot, "_main_post_receipt_values_owner", factory)
            options = {key: object() for key in defaults} if explicit else {}
            assert adapter(value, **options) is operation.return_value
            factory.assert_called_once_with()
            forwarded = options if explicit else defaults
            operation.assert_called_once_with(value, **forwarded)
            assert operation.call_args.args[0] is value
            assert all(operation.call_args.kwargs[key] is option for key, option in forwarded.items())
            failure = TypeError("current owner failure")
            operation.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(value, **options)
            assert caught.value is failure


def test_receipt_owner_binds_external_references_and_refreshes_without_eager_runtime_access(monkeypatch):
    factory = bot._main_post_receipt_values_owner
    assert set(inspect.signature(receipts.MainPostReceiptValues).parameters) == {*OWNER_FIELDS, "current"}
    prior = None
    for _ in range(2):
        current = {field: object() for field in OWNER_FIELDS}
        for field, root_name in OWNER_FIELDS.items():
            monkeypatch.setattr(bot, root_name, current[field])
        owner = factory()
        assert all(getattr(owner, field) is value for field, value in current.items())
        if prior is not None:
            assert owner is not prior
            assert all(getattr(prior, field) is value for field, value in prior_values.items())
        prior, prior_values = owner, current
    replacement = Mock(return_value=object())
    monkeypatch.setattr(bot, "_main_post_receipt_values_owner", replacement)
    replacement.assert_not_called()
    assert owner.current() is replacement.return_value
    replacement.assert_called_once_with()
    failure = LookupError("owner composition failed")
    replacement.side_effect = failure
    with pytest.raises(LookupError) as caught:
        owner.current()
    assert caught.value is failure


def _pending(lane):
    attempt = schema_current_main_attempt(lane)
    attempt["lifecycle_state"] = "attempting"
    return bot.build_confirmed_pending_schedule_receipt(
        attempt, post_id="950001", confirmation_epoch=1_800_000_100,
        image_summary=attempt["recovery_plan"].get("image_summary", ""),
    )


def _lane_functions(lane):
    prefix = "regular" if lane == "quote_image" else "meme"
    return (
        getattr(bot, f"materialize_bound_{prefix}_schedule_receipt"),
        getattr(bot, f"{prefix}_post_receipt_is_semantically_valid"),
    )


@pytest.mark.parametrize("history", [[[]], ["a" * 64, 1]])
def test_attempt_history_native_errors_precede_payload_but_confirmed_history_rejects(
    monkeypatch, history,
):
    attempt = schema_current_main_attempt("quote_image")
    attempt["recovery_plan"]["quote_history_after"] = history
    payload = Mock(side_effect=AssertionError("history must precede payload"))
    monkeypatch.setattr(receipts, "main_post_attempt_payload", payload)
    with pytest.raises(TypeError):
        bot.main_post_attempt_is_semantically_valid(attempt)
    payload.assert_not_called()
    assert not bot.regular_post_receipt_is_semantically_valid(
        valid_regular_receipt_v2(quote_history_after=history)
    )


def test_full_receipt_native_get_errors_remain_distinct_from_attempt_and_pending():
    for validator in (bot.regular_post_receipt_is_semantically_valid,
                      bot.meme_post_receipt_is_semantically_valid):
        with pytest.raises(AttributeError):
            validator(None)
    assert bot.main_post_attempt_is_semantically_valid(None) is False
    assert bot.confirmed_pending_schedule_receipt_is_semantically_valid(None) is False


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_pending_uses_owned_scalar_rules_and_current_callbacks_before_source_and_lane_gates(
    monkeypatch, lane,
):
    pending = _pending(lane)
    events = Mock()
    for key in ("valid_string_post_id", "receipt_int", "valid_receipt_epoch",
                "main_post_attempt_is_semantically_valid"):
        callback = (Mock(return_value=True) if key == "main_post_attempt_is_semantically_valid"
                    else Mock(wraps=getattr(bot, key)))
        events.attach_mock(callback, key)
        if key in OPERATIONS:
            patch_receipt_operation(monkeypatch, key, callback)
        else:
            target = receipts if key in {"receipt_int", "valid_string_post_id"} else bot
            monkeypatch.setattr(target, key, callback)
    validator = bot.confirmed_pending_schedule_receipt_is_semantically_valid
    assert validator(pending, expected_lane=lane)
    assert events.mock_calls == [
        call.valid_string_post_id(pending["post_id"]),
        call.receipt_int(pending["confirmation_epoch"]),
        call.valid_receipt_epoch(pending["confirmation_epoch"]),
        call.main_post_attempt_is_semantically_valid(pending["source_attempt"]),
    ]
    events.main_post_attempt_is_semantically_valid.assert_called_once_with(
        pending["source_attempt"]
    )
    assert events.main_post_attempt_is_semantically_valid.call_args.args[0] is pending["source_attempt"]
    events.reset_mock()
    pending["source_attempt"] = None
    events.main_post_attempt_is_semantically_valid.side_effect = None
    events.main_post_attempt_is_semantically_valid.return_value = False
    assert validator(pending, expected_lane="wrong lane") is False
    assert events.mock_calls[-1] == call.main_post_attempt_is_semantically_valid(None)
    failure = LookupError("current source validator failure")
    events.main_post_attempt_is_semantically_valid.side_effect = failure
    with pytest.raises(LookupError) as caught:
        validator(pending)
    assert caught.value is failure


def test_regular_eager_epochs_and_date_closure_use_current_authorities(monkeypatch):
    receipt = valid_regular_receipt_v2(
        next_meme_post_epoch=1_800_086_400,
        next_meme_schedule_mode="fallback",
        next_meme_schedule_date="callback-date",
        meme_anchor_quote_post_epoch=0,
        meme_schedule_changed_by_quote=False,
    )
    events = Mock()
    events.attach_mock(Mock(wraps=bot.receipt_int), "integer")
    events.attach_mock(Mock(return_value=False), "post_id")
    monkeypatch.setattr(receipts, "receipt_int", events.integer)
    monkeypatch.setattr(receipts, "valid_string_post_id", events.post_id)
    assert bot.regular_post_receipt_is_semantically_valid(receipt) is False
    assert events.mock_calls == [
        call.integer(receipt["quote_post_epoch"]),
        call.integer(receipt["next_quote_post_epoch"]),
        call.post_id(receipt["post_id"]),
    ]
    events.post_id.return_value = True
    date = Mock(return_value="callback-date")
    monkeypatch.setattr(bot, "safe_bound_schedule_date_str", date)
    monkeypatch.setattr(bot, "MEME_SCHEDULE_MODES", {"fallback"})
    for zone in ("first-zone", "second-zone"):
        monkeypatch.setattr(bot, "MAIN_POST_SCHEDULE_TIMEZONE", zone)
        assert bot.regular_post_receipt_is_semantically_valid(receipt)
        date.assert_called_with(receipt["next_meme_post_epoch"], zone)
    failure = ValueError("current calendar callback failure")
    date.side_effect = failure
    with pytest.raises(ValueError) as caught:
        bot.regular_post_receipt_is_semantically_valid(receipt)
    assert caught.value is failure


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_lineage_uses_owned_hash_copy_and_current_pending_nonrecursive_materializer(
    monkeypatch, lane,
):
    pending = _pending(lane)
    materialize, validator = _lane_functions(lane)
    receipt = materialize(pending)
    source = receipt["source_attempt"]
    prefix = "regular" if lane == "quote_image" else "meme"
    events = Mock()
    callbacks = {
        "attempt": Mock(return_value=True),
        "canonical": Mock(wraps=bot.canonical_atomic_json_bytes),
        "deepcopy": Mock(wraps=copy.deepcopy),
        "pending": Mock(return_value=True),
        "materialize": Mock(return_value=receipt),
    }
    for key, callback in callbacks.items():
        events.attach_mock(callback, key)
    patch_receipt_operation(monkeypatch, "main_post_attempt_is_semantically_valid", events.attempt)
    monkeypatch.setattr(receipts, "canonical_atomic_json_bytes", events.canonical)
    monkeypatch.setattr(receipts, "copy", SimpleNamespace(deepcopy=events.deepcopy))
    patch_receipt_operation(monkeypatch, "confirmed_pending_schedule_receipt_is_semantically_valid", events.pending)
    patch_receipt_operation(monkeypatch, f"materialize_bound_{prefix}_schedule_receipt", events.materialize)
    assert validator(receipt)
    reconstructed = events.pending.call_args.args[0]
    assert reconstructed == pending
    assert reconstructed["source_attempt"] == source
    assert reconstructed["source_attempt"] is not source
    assert reconstructed["source_attempt"]["recovery_plan"] is not source["recovery_plan"]
    assert events.canonical.call_args.args[0] is source
    assert events.deepcopy.call_args.args[0] is source
    assert events.materialize.call_args.args[0] is reconstructed
    assert events.mock_calls == [
        call.attempt(source), call.canonical(source), call.deepcopy(source),
        call.pending(reconstructed, expected_lane=lane),
        call.materialize(reconstructed, _validate_result=False),
    ]
    events.materialize.return_value = {**receipt, "text": "different"}
    assert validator(receipt) is False
    events.reset_mock()
    events.pending.return_value = False
    assert validator(receipt) is False
    events.materialize.assert_not_called()
    events.reset_mock()
    events.attempt.return_value = False
    assert validator(receipt) is False
    assert events.mock_calls == [call.attempt(source)]


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_materializers_gate_before_derivation_and_use_current_final_validator_and_error(
    monkeypatch, lane,
):
    pending = _pending(lane)
    materialize, _validator = _lane_functions(lane)
    prefix = "regular" if lane == "quote_image" else "meme"
    error = type("CurrentReceiptError", (RuntimeError,), {})
    monkeypatch.setattr(bot, f"Invalid{prefix.title()}PostReceipt", error)
    gate = Mock(return_value=False)
    final = Mock(return_value=False)
    copy_spy = Mock(wraps=copy.deepcopy)
    patch_receipt_operation(monkeypatch, "confirmed_pending_schedule_receipt_is_semantically_valid", gate)
    patch_receipt_operation(monkeypatch, f"{prefix}_post_receipt_is_semantically_valid", final)
    monkeypatch.setattr(receipts, "copy", SimpleNamespace(deepcopy=copy_spy))
    with pytest.raises(error, match=f"^Invalid confirmed {prefix} pending-schedule receipt$"):
        materialize(pending, _validate_result=False)
    gate.assert_called_once_with(pending, expected_lane=lane)
    assert gate.call_args.args[0] is pending
    copy_spy.assert_not_called()
    final.assert_not_called()
    gate.return_value = True
    with pytest.raises(error, match=f"^Bound {prefix} schedule produced an invalid confirmed receipt$"):
        materialize(pending)
    final.assert_called_once()
    final.reset_mock()
    receipt = materialize(pending, _validate_result=False)
    final.assert_not_called()
    final.return_value = True
    checked = materialize(pending)
    assert checked == receipt
    assert final.call_args.args[0] is checked
    failure = OverflowError("current final validator failure")
    final.side_effect = failure
    with pytest.raises(OverflowError) as caught:
        materialize(pending)
    assert caught.value is failure


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_materialization_preserves_copy_boundaries_and_hashes_original_source_in_order(
    monkeypatch, lane,
):
    pending = _pending(lane)
    materialize, _validator = _lane_functions(lane)
    attempt = pending["source_attempt"]
    plan = attempt["recovery_plan"]
    canonical = bot.canonical_atomic_json_bytes
    events = Mock()
    events.attach_mock(Mock(return_value=True), "gate")
    events.attach_mock(Mock(wraps=copy.deepcopy), "deepcopy")
    events.attach_mock(Mock(wraps=bot.canonical_atomic_json_bytes), "canonical")
    patch_receipt_operation(monkeypatch, "confirmed_pending_schedule_receipt_is_semantically_valid", events.gate)
    monkeypatch.setattr(receipts, "copy", SimpleNamespace(deepcopy=events.deepcopy))
    monkeypatch.setattr(receipts, "canonical_atomic_json_bytes", events.canonical)
    if lane == "quote_image":
        # Mutable children expose list copying separately from deep source copying.
        plan["quote_history_after"].append({"child": []})
        plan["image_history_after"].append({"child": []})
    original = copy.deepcopy(pending)
    expected_hash = bot.hashlib.sha256(canonical(attempt)).hexdigest()
    result = materialize(pending, _validate_result=False)
    assert pending == original
    assert result["source_attempt"] == attempt
    assert result["source_attempt"] is not attempt
    assert result["source_attempt"]["recovery_plan"] is not plan
    assert events.canonical.call_args.args[0] is attempt
    assert result["source_attempt_sha256"] == expected_hash
    expected = [call.gate(pending, expected_lane=lane)]
    if lane == "quote_image":
        expected += [call.deepcopy(plan["meme_schedule_before"])]
    expected += [call.deepcopy(attempt), call.canonical(attempt)]
    if lane == "quote_image":
        assert result["schema_version"] == 3
        for key in ("quote_history_after", "image_history_after"):
            assert result[key] is not plan[key]
            assert result[key][-1] is plan[key][-1]
            assert result["source_attempt"]["recovery_plan"][key][-1] is not plan[key][-1]
    assert events.mock_calls == expected


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_unsupported_attempt_version_cannot_authorise_transport_or_recovery(lane):
    attempt = schema_current_main_attempt(lane)
    attempt["schema_version"] = 6
    assert not bot.main_post_attempt_is_semantically_valid(attempt)
    assert not bot.current_main_post_attempt_is_semantically_valid(attempt)
    assert not bot.main_post_attempt_binds_payload(attempt, bot.main_post_attempt_payload(attempt))
    attempt["lifecycle_state"] = "attempting"
    with pytest.raises(RuntimeError, match="Refusing an invalid main-post attempt"):
        bot.build_confirmed_pending_schedule_receipt(
            attempt, post_id="950001", confirmation_epoch=1_800_000_100,
        )


def test_unsupported_regular_receipt_version_is_rejected():
    receipt = bot.materialize_bound_regular_schedule_receipt(_pending("quote_image"))
    receipt["schema_version"] = 4
    assert not bot.regular_post_receipt_is_semantically_valid(receipt)


def test_active_receipt_policy_is_stable_while_next_owned_operation_binds_current_policy(monkeypatch):
    pending = _pending("quote_image")
    receipt = bot.materialize_bound_regular_schedule_receipt(pending)
    # Exercise the active operation's version, mode and date checks before lineage.
    receipt.update(
        next_meme_post_epoch=receipt["quote_post_epoch"] + 3600,
        next_meme_schedule_mode="fallback", next_meme_schedule_date="bound-date",
        meme_anchor_quote_post_epoch=0, meme_schedule_changed_by_quote=False,
        meme_schedule_version=bot.MEME_SCHEDULE_VERSION,
    )
    old_zone = bot.MAIN_POST_SCHEDULE_TIMEZONE
    original_modes = {"fallback"}
    old_date = Mock(return_value="bound-date")
    new_regular_error = type("NewRegularError", (RuntimeError,), {})
    new_meme_error = type("NewMemeError", (RuntimeError,), {})
    changed = {
        "schedule_timezone": "new-zone", "schedule_modes": set(), "schedule_version": 0,
        "bound_meme_state_is_valid": Mock(), "safe_schedule_date": Mock(),
        "valid_epoch": Mock(), "invalid_regular_receipt": new_regular_error,
        "invalid_meme_receipt": new_meme_error, "bound_schedule_datetime": Mock(),
    }
    epochs = []

    def original_epoch(value):
        epochs.append(value)
        for field, root_name in OWNER_FIELDS.items():
            monkeypatch.setattr(bot, root_name, changed[field])
        return True

    observed = []

    def inspect_next_operation(owner, source):
        observed.append(source)
        assert source is receipt["source_attempt"]
        assert all(getattr(owner, field) is value for field, value in changed.items())
        return False

    monkeypatch.setattr(bot, "MEME_SCHEDULE_MODES", original_modes)
    monkeypatch.setattr(bot, "valid_receipt_epoch", original_epoch)
    monkeypatch.setattr(bot, "safe_bound_schedule_date_str", old_date)
    monkeypatch.setattr(receipts.MainPostReceiptValues, "attempt_is_valid", inspect_next_operation)
    assert bot.regular_post_receipt_is_semantically_valid(receipt) is False
    assert observed == [receipt["source_attempt"]]
    assert epochs == [receipt["quote_post_epoch"], receipt["next_quote_post_epoch"], receipt["next_meme_post_epoch"]]
    old_date.assert_called_once_with(receipt["next_meme_post_epoch"], old_zone)
    assert original_modes == {"fallback"}
    changed["valid_epoch"].assert_not_called()
    changed["safe_schedule_date"].assert_not_called()


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_materializer_keeps_its_error_class_when_pending_validation_rebinds_errors(monkeypatch, lane):
    pending = _pending(lane)
    materialize, _ = _lane_functions(lane)
    prefix = "regular" if lane == "quote_image" else "meme"
    old_error = type("OldReceiptError", (RuntimeError,), {})
    new_error = type("NewReceiptError", (RuntimeError,), {})
    monkeypatch.setattr(bot, f"Invalid{prefix.title()}PostReceipt", old_error)

    def reject(_owner, value, *, expected_lane):
        assert value is pending and expected_lane == lane
        monkeypatch.setattr(bot, f"Invalid{prefix.title()}PostReceipt", new_error)
        return False

    monkeypatch.setattr(receipts.MainPostReceiptValues, "pending_is_valid", reject)
    with pytest.raises(old_error, match=f"^Invalid confirmed {prefix} pending-schedule receipt$"):
        materialize(pending)
    with pytest.raises(new_error, match=f"^Invalid confirmed {prefix} pending-schedule receipt$"):
        materialize(pending)


def test_materializer_keeps_bound_calendar_while_final_validation_refreshes(monkeypatch):
    pending = _pending("daily_meme")
    old_calendar = Mock(wraps=bot.bound_schedule_datetime)
    new_calendar = Mock(side_effect=AssertionError("outer materializer rebound its calendar"))
    final_owners = []

    def admit(_owner, value, *, expected_lane):
        assert value is pending and expected_lane == "daily_meme"
        monkeypatch.setattr(bot, "bound_schedule_datetime", new_calendar)
        return True

    def validate(owner, result):
        assert owner.bound_schedule_datetime is new_calendar
        final_owners.append((owner, result))
        return True

    monkeypatch.setattr(bot, "bound_schedule_datetime", old_calendar)
    monkeypatch.setattr(receipts.MainPostReceiptValues, "pending_is_valid", admit)
    monkeypatch.setattr(receipts.MainPostReceiptValues, "meme_is_valid", validate)
    result = bot.materialize_bound_meme_schedule_receipt(pending)
    old_calendar.assert_called_once_with(
        pending["confirmation_epoch"], pending["source_attempt"]["recovery_plan"]["schedule_timezone"],
    )
    new_calendar.assert_not_called()
    assert len(final_owners) == 1 and final_owners[0][1] is result


def test_nested_owner_composition_failure_propagates_after_original_epoch_check(monkeypatch):
    pending = _pending("daily_meme")
    failure = LookupError("current receipt authority unavailable")
    next_owner = Mock(side_effect=failure)
    epoch_calls = []

    def valid_epoch(value):
        epoch_calls.append(value)
        monkeypatch.setattr(bot, "_main_post_receipt_values_owner", next_owner)
        return True

    monkeypatch.setattr(bot, "valid_receipt_epoch", valid_epoch)
    with pytest.raises(LookupError) as caught:
        bot.confirmed_pending_schedule_receipt_is_semantically_valid(pending)
    assert caught.value is failure
    assert epoch_calls == [pending["confirmation_epoch"]]
    next_owner.assert_called_once_with()
