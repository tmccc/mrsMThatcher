from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import hashlib
import inspect
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

import pytest

import mrs_bot_reply_receipt_values as values
from tests.helpers.legacy_reply_fixtures import (
    CASE_IDS,
    _confirmed_receipt,
    _legacy_case,
)
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401
from tests.helpers.reply_fixtures import (
    unit_approved_reply,
    unit_sending_v4_reply_receipt,
    unit_v4_reply_receipt_template,
)


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, json, dataclasses, hashlib, io, logging, os, random, re, socket, sys
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('reply receipt values import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_reply_receipt_values', 'mrs_bot_durable_json_io'}:
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_reply_receipt_values
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
    assert values.hashlib is bot.hashlib and values.re is bot.re


OWNER_INPUTS = {
    "bounded_tweet_id_value": "bounded_tweet_id_value",
    "valid_string_post_id": "valid_string_post_id", "receipt_int": "receipt_int",
    "valid_receipt_epoch": "valid_receipt_epoch", "safe_reply_cap_date_str": "safe_reply_cap_date_str",
    "legacy_draft_is_valid": "_legacy_ai_reply_receipt_draft_is_valid",
    "draft_is_valid": "ai_reply_receipt_draft_is_valid",
    "legacy_tested_strategy_version": "_LEGACY_TESTED_REPLY_STRATEGY_VERSION",
    "legacy_ai_first_strategy_version": "_LEGACY_AI_FIRST_REPLY_STRATEGY_VERSION",
    "now_epoch": "now_epoch", "reply_cap_date_str": "reply_cap_date_str",
    "log": "log", "invalid_receipt": "InvalidConfirmedReplyReceipt",
}


@pytest.fixture
def make_owner():
    """Compose receipt values with the existing isolated validation boundaries."""
    def build(**overrides):
        current = {field: getattr(bot, name) for field, name in OWNER_INPUTS.items()}
        return values.ReplyReceiptValues(**{**current, **overrides})
    return build


def test_owner_composition_binds_current_dependencies_without_calling_them(monkeypatch):
    snapshots = []
    for _ in range(2):
        current = {field: Mock() for field in OWNER_INPUTS}
        for field, name in OWNER_INPUTS.items():
            monkeypatch.setattr(bot, name, current[field])
        owner = bot._reply_receipt_values_owner()
        assert isinstance(owner, values.ReplyReceiptValues)
        for field, value in current.items():
            assert getattr(owner, field) is value
            value.assert_not_called()
        snapshots.append((owner, current))
    first, inputs = snapshots[0]
    assert first is not snapshots[1][0]
    assert all(getattr(first, field) is value for field, value in inputs.items())
    with pytest.raises(FrozenInstanceError):
        first.legacy_tested_strategy_version = "changed"


def test_adapters_preserve_defaults_arguments_result_identity_and_errors(monkeypatch):
    methods = {
        "mention_pagination_provenance_is_valid": "pagination_is_valid",
        "_conversational_reply_receipt_is_semantically_valid": "validate",
        "conversational_sending_receipt_from_confirmed": "sending_from_confirmed",
        "confirmed_reply_receipt_is_semantically_valid": "confirmed_is_valid",
        "sending_reply_receipt_is_semantically_valid": "sending_is_valid",
        "_legacy_confirmed_reply_receipt_is_semantically_valid": "legacy_confirmed_is_valid",
        "_legacy_sending_reply_receipt_is_semantically_valid": "legacy_sending_is_valid",
        "bind_conversational_reply_attempt_time": "bind_attempt",
        "_confirmed_reply_receipt_from_sending": "confirmed_from_sending",
        "_reply_confirmation_epoch_after_remote_success": "observed_confirmation_epoch",
        "conversational_reply_confirmation_epoch": "confirmation_epoch",
    }
    for name, method_name in methods.items():
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        args = tuple(object() for param in public.values() if param.kind == param.POSITIONAL_OR_KEYWORD)
        for use_defaults in (True, False):
            owner = Mock(spec=values.ReplyReceiptValues)
            factory = Mock(return_value=owner)
            monkeypatch.setattr(bot, "_reply_receipt_values_owner", factory)
            implementation = getattr(owner, method_name)
            result = object()
            implementation.return_value = result
            options = {
                key: object() for key, param in public.items()
                if param.kind == param.KEYWORD_ONLY
                and (not use_defaults or param.default is param.empty)
            }
            expected = {
                key: param.default for key, param in public.items()
                if param.kind == param.KEYWORD_ONLY and param.default is not param.empty
            } | options
            assert adapter(*args, **options) is result
            factory.assert_called_once_with()
            actual_args, actual_kwargs = implementation.call_args
            assert len(actual_args) == len(args)
            assert all(actual is original for actual, original in zip(actual_args, args))
            assert actual_kwargs.keys() == expected.keys()
            assert all(actual_kwargs[key] is value for key, value in expected.items())
            failure = TypeError(name)
            implementation.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(*args, **options)
            assert caught.value is failure


def test_lifecycle_dispatch_uses_owned_validator_and_exact_flags(monkeypatch, make_owner):
    data, result = object(), object()
    owner = make_owner()
    for name, lifecycle, legacy in (
        ("confirmed_is_valid", "confirmed", False),
        ("sending_is_valid", "sending", False),
        ("legacy_confirmed_is_valid", "confirmed", True),
        ("legacy_sending_is_valid", "sending", True),
    ):
        callback = Mock(return_value=result)
        monkeypatch.setattr(values.ReplyReceiptValues, "validate", callback)
        assert getattr(owner, name)(data) is result
        options = {"lifecycle_state": lifecycle}
        if legacy:
            options["legacy_recovery"] = True
        callback.assert_called_once_with(data, **options)


@pytest.fixture(params=["current", *CASE_IDS])
def receipt_family(request, make_owner):
    if request.param == "current":
        sending = unit_sending_v4_reply_receipt()
        confirmed = make_owner().confirmed_from_sending(
            sending, reply_post_id="999", confirmation_epoch=2_000_000_005,
        )
        return sending, confirmed, False
    case = _legacy_case(request.param)
    return case["sending_receipt"], _confirmed_receipt(case), True


def test_source_validation_uses_current_family_callbacks_in_order(monkeypatch, make_owner, receipt_family):
    sending, confirmed, legacy = receipt_family
    owner = make_owner()
    validator_name = "legacy_confirmed_is_valid" if legacy else "confirmed_is_valid"
    assert getattr(owner, validator_name)(confirmed)
    if legacy:
        assert not owner.sending_is_valid(sending)
    trace = Mock()
    trace.draft = Mock(return_value=True)
    trace.reconstruct = Mock(wraps=owner.sending_from_confirmed)
    trace.sending = Mock(return_value=True)
    trace.canonical = Mock(wraps=bot.canonical_atomic_json_bytes)
    monkeypatch.setattr(values, "canonical_atomic_json_bytes", trace.canonical)
    wrong_family = Mock(side_effect=AssertionError("wrong draft family"))
    owner = replace(
        owner,
        legacy_draft_is_valid=trace.draft if legacy else wrong_family,
        draft_is_valid=wrong_family if legacy else trace.draft,
    )
    sending_method = "legacy_sending_is_valid" if legacy else "sending_is_valid"
    other_method = "sending_is_valid" if legacy else "legacy_sending_is_valid"
    monkeypatch.setattr(values.ReplyReceiptValues, "sending_from_confirmed", trace.reconstruct)
    monkeypatch.setattr(values.ReplyReceiptValues, sending_method, trace.sending)
    monkeypatch.setattr(values.ReplyReceiptValues, other_method, Mock(side_effect=AssertionError("wrong source family")))
    validate = getattr(owner, validator_name)

    assert validate(confirmed)
    assert [entry[0] for entry in trace.mock_calls] == ["draft", "reconstruct", "sending", "canonical"]
    assert trace.draft.call_args.args[0] is confirmed
    assert trace.draft.call_args.args[1] is confirmed["reply_text"]
    assert trace.reconstruct.call_args.args[0] is confirmed
    source = trace.sending.call_args.args[0]
    assert source == sending and source is not confirmed
    assert source["reply_context"] is confirmed["reply_context"]
    assert source["ai_reply_draft"] is confirmed["ai_reply_draft"]
    assert trace.canonical.call_args.args[0] is source

    trace.reset_mock()
    trace.sending.return_value = False
    assert not validate(confirmed)
    assert [entry[0] for entry in trace.mock_calls] == ["draft", "reconstruct", "sending"]

    trace.reset_mock()
    failure = UnicodeError("current draft callback failed")
    trace.draft.side_effect = failure
    if legacy:
        assert not validate(confirmed)
    else:
        with pytest.raises(UnicodeError) as caught:
            validate(confirmed)
        assert caught.value is failure
    assert [entry[0] for entry in trace.mock_calls] == ["draft"]


def test_source_reconstruction_errors_are_caught_before_hashing(monkeypatch, make_owner):
    sending = unit_sending_v4_reply_receipt()
    confirmed = bot._confirmed_reply_receipt_from_sending(
        sending, reply_post_id="999", confirmation_epoch=2_000_000_005,
    )
    reconstruct = Mock(side_effect=ValueError("invalid source"))
    canonical = Mock(side_effect=UnicodeError("unencodable source"))
    monkeypatch.setattr(values.ReplyReceiptValues, "sending_from_confirmed", reconstruct)
    monkeypatch.setattr(values, "canonical_atomic_json_bytes", canonical)
    owner = make_owner()
    assert not owner.confirmed_is_valid(confirmed)
    canonical.assert_not_called()
    reconstruct.side_effect = None
    reconstruct.return_value = sending
    with pytest.raises(UnicodeError) as caught:
        owner.confirmed_is_valid(confirmed)
    assert caught.value is canonical.side_effect
    assert canonical.call_args.args[0] is sending


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
def test_attempt_binding_preserves_references_and_clock_date_validation_order(monkeypatch, make_owner, lane):
    template = unit_v4_reply_receipt_template(lane=lane)
    reply = unit_approved_reply(template["reply_context"], text=template["reply_text"])
    template["reply_text"], template["ai_reply_draft"] = reply, reply.draft_record
    before = bot.canonical_atomic_json_bytes(template)
    trace = Mock()
    trace.clock.return_value = 2_000_000_000
    trace.date = Mock(wraps=bot.reply_cap_date_str)
    owner = make_owner(now_epoch=trace.clock, reply_cap_date_str=trace.date)
    trace.validate = Mock(wraps=owner.sending_is_valid)
    monkeypatch.setattr(values.ReplyReceiptValues, "sending_is_valid", trace.validate)

    prepared = owner.bind_attempt(template)
    # Validation retains its separate safe date boundary.
    assert [entry[0] for entry in trace.mock_calls][:3] == ["clock", "date", "validate"]
    assert trace.date.call_args.args == (2_000_000_000,)
    assert trace.validate.call_args.args[0] is prepared
    assert prepared is not template
    assert all(prepared[key] is value for key, value in template.items())
    assert bot.canonical_atomic_json_bytes(template) == before

    trace.reset_mock()
    trace.validate.return_value = False
    with pytest.raises(RuntimeError, match="^Internal error: prepared reply attempt failed validation$"):
        owner.bind_attempt(template)
    assert [entry[0] for entry in trace.mock_calls] == ["clock", "date", "validate"]
    assert bot.canonical_atomic_json_bytes(template) == before
    trace.reset_mock()
    with pytest.raises(RuntimeError, match="^Reply attempt template already contains timing fields$"):
        owner.bind_attempt({**template, "confirmation_epoch": None})
    assert not trace.mock_calls


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
def test_projection_and_reconstruction_keep_shallow_copies_and_exact_hash_input(monkeypatch, make_owner, lane):
    sending = unit_sending_v4_reply_receipt(lane=lane)
    reply = unit_approved_reply(sending["reply_context"], text=sending["reply_text"])
    sending["reply_text"], sending["ai_reply_draft"] = reply, reply.draft_record
    before = dict(sending)
    canonical = Mock(return_value=b"exact current canonical sending bytes\n")
    date = Mock(return_value="confirmation-date")
    monkeypatch.setattr(values, "canonical_atomic_json_bytes", canonical)
    owner = make_owner(reply_cap_date_str=date)
    confirmed = owner.confirmed_from_sending(
        sending, reply_post_id=999, confirmation_epoch=2_000_000_005,
    )
    assert canonical.call_args.args[0] is sending
    date.assert_called_once_with(2_000_000_005)
    assert confirmed["reply_post_id"] == "999"
    assert confirmed["source_receipt_sha256"] == hashlib.sha256(canonical.return_value).hexdigest()
    assert confirmed["daily_reply_date"] == "confirmation-date"
    if lane == "quote_tweet":
        assert confirmed["daily_quote_reply_date"] == "confirmation-date"
    else:
        # Reconstruction removes this field even if the confirmed input has it.
        confirmed["daily_quote_reply_date"] = "unused-date"
    confirmed_before = dict(confirmed)
    integer = Mock(wraps=bot.receipt_int)
    safe_date = Mock(return_value="attempt-date")
    owner = replace(owner, receipt_int=integer, safe_reply_cap_date_str=safe_date)
    reconstructed = owner.sending_from_confirmed(confirmed)
    integer.assert_called_once_with(confirmed["attempt_epoch"])
    safe_date.assert_called_once_with(2_000_000_000)
    assert reconstructed is not confirmed and confirmed is not sending
    for key in ("reply_text", "reply_context", "ai_reply_draft"):
        assert reconstructed[key] is confirmed[key] is sending[key]
    assert reconstructed.keys() == sending.keys()
    assert reconstructed["attempt_epoch"] is sending["attempt_epoch"]
    assert reconstructed["reply_epoch"] == 2_000_000_000
    assert reconstructed["daily_reply_date"] == "attempt-date"
    assert reconstructed["lifecycle_state"] == "sending"
    if lane == "quote_tweet":
        assert reconstructed["daily_quote_reply_date"] == "attempt-date"
    assert sending == before and confirmed == confirmed_before


def test_confirmation_projection_preserves_existing_version_equality(monkeypatch, make_owner):
    sending = unit_sending_v4_reply_receipt()
    sending["schema_version"] = 4.0
    canonical = Mock(wraps=bot.canonical_atomic_json_bytes)
    monkeypatch.setattr(values, "canonical_atomic_json_bytes", canonical)
    owner = make_owner()
    confirmed = owner.confirmed_from_sending(
        sending, reply_post_id=999, confirmation_epoch=2_000_000_005,
    )
    assert confirmed["confirmation_epoch"] == 2_000_000_005
    assert canonical.call_args.args[0] is sending
    canonical.reset_mock()
    sending["schema_version"] = 3
    confirmed = owner.confirmed_from_sending(
        sending, reply_post_id=999, confirmation_epoch=2_000_000_005,
    )
    assert confirmed == {**sending, "lifecycle_state": "confirmed", "reply_post_id": "999"}
    canonical.assert_not_called()


def test_observed_confirmation_uses_current_clock_converter_and_exact_warning(make_owner):
    trace = Mock()
    trace.clock.return_value = "19"
    trace.integer.return_value = 20
    owner = make_owner(now_epoch=trace.clock, receipt_int=trace.integer, log=trace.log)
    attempt = object()
    sending = {"schema_version": 4.0, "attempt_epoch": attempt}
    assert owner.observed_confirmation_epoch(sending) == 20
    assert trace.mock_calls == [
        call.clock(), call.integer(attempt),
        call.log.warning(
            "Wall clock moved backward during conversational reply creation; "
            "using durable attempt epoch as conservative confirmation time "
            "attempt_epoch=%s observed_epoch=%s", 20, 19,
        ),
    ]
    trace.reset_mock()
    assert owner.observed_confirmation_epoch({"schema_version": 3}, 23.9) == 23
    assert not trace.mock_calls
    with pytest.raises(ValueError):
        owner.observed_confirmation_epoch(sending, "invalid")
    assert not trace.mock_calls


@pytest.mark.parametrize("version, field, message", [
    (4.0, "confirmation_epoch", "Schema-v4 confirmed reply receipt lacks a confirmation epoch"),
    (3, "reply_epoch", "Legacy confirmed reply receipt lacks its best-known reply epoch"),
])
def test_best_confirmation_time_uses_current_converter_and_exception(make_owner, version, field, message):
    class CurrentReceiptError(Exception):
        pass

    raw, converted = object(), object()
    integer = Mock(return_value=converted)
    owner = make_owner(receipt_int=integer, invalid_receipt=CurrentReceiptError)
    receipt = {"schema_version": version, field: raw}
    assert owner.confirmation_epoch(receipt) is converted
    integer.assert_called_once_with(raw)
    integer.return_value = None
    with pytest.raises(CurrentReceiptError) as caught:
        owner.confirmation_epoch(receipt)
    assert type(caught.value) is CurrentReceiptError
    assert str(caught.value) == message


def test_pagination_uses_current_id_callback_before_token_validation(make_owner):
    bounded = Mock(return_value=0)
    owner = make_owner(bounded_tweet_id_value=bounded)
    assert owner.pagination_is_valid({"base_since_id": "", "next_token": "page-2"})
    bounded.assert_called_once_with("", allow_empty=True)
    bounded.reset_mock()
    assert not owner.pagination_is_valid({"base_since_id": ""})
    bounded.assert_not_called()
    failure = ValueError("current ID validator failed")
    bounded.side_effect = failure
    with pytest.raises(ValueError) as caught:
        owner.pagination_is_valid({"base_since_id": "", "next_token": None})
    assert caught.value is failure


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
def test_send_preparation_preserves_reviewed_objects_and_validates_before_lane(monkeypatch, make_owner, lane):
    template = unit_sending_v4_reply_receipt(lane=lane)
    reply = unit_approved_reply(template["reply_context"], text=template["reply_text"])
    template["reply_text"], template["ai_reply_draft"] = reply, reply.draft_record
    original = dict(template)
    owner = make_owner()
    validator = Mock(return_value=True)
    monkeypatch.setattr(values.ReplyReceiptValues, "sending_is_valid", validator)
    prepared = owner.prepare_sending_template(template, lane=lane)
    assert prepared == template and prepared is not template
    assert all(prepared[key] is value for key, value in original.items())
    validator.assert_called_once_with(prepared)
    assert validator.call_args.args[0] is prepared
    assert template == original

    validator.reset_mock()
    with pytest.raises(RuntimeError, match="invalid reply receipt template"):
        owner.prepare_sending_template(template, lane="other")
    validator.assert_called_once()
    assert validator.call_args.args[0] is not template
    validator.side_effect = TypeError("native validation failure")
    with pytest.raises(TypeError) as caught:
        owner.prepare_sending_template(template, lane=lane)
    assert caught.value is validator.side_effect
    assert template == original


@pytest.mark.parametrize("changes,exception,message", [
    ({"reply_post_id": "999", "schema_version": 3}, ValueError, "must not contain reply_post_id"),
    ({"schema_version": 3}, RuntimeError, "require a current schema-v4"),
    ({"schema_version": 4.0}, RuntimeError, "require a current schema-v4"),
])
def test_send_preparation_rejects_authority_mismatch_before_validation(monkeypatch, make_owner, changes, exception, message):
    template = {**unit_sending_v4_reply_receipt(), **changes}
    validator = Mock(side_effect=AssertionError("invalid send authority reached draft validation"))
    monkeypatch.setattr(values.ReplyReceiptValues, "sending_is_valid", validator)
    with pytest.raises(exception, match=message):
        make_owner().prepare_sending_template(template, lane="mention")
    validator.assert_not_called()


@pytest.mark.parametrize("lifecycle,changes", [
    ("sending", {"confirmation_epoch": 2_000_000_000}),
    ("sending", {"reply_epoch": 2_000_000_001}),
    ("sending", {"attempt_epoch": None}),
    ("confirmed", {"confirmation_epoch": 1_999_999_999, "reply_epoch": 1_999_999_999}),
    ("confirmed", {"confirmation_epoch": 2_000_000_004}),
    ("confirmed", {"reply_epoch": None}),
    ("confirmed", {"daily_reply_date": "wrong date"}),
    ("confirmed", {"daily_quote_reply_date": "unexpected bucket"}),
])
def test_receipt_time_failure_precedes_draft_and_lineage_work(monkeypatch, make_owner, lifecycle, changes):
    sending = unit_sending_v4_reply_receipt()
    owner = make_owner()
    receipt = sending if lifecycle == "sending" else owner.confirmed_from_sending(
        sending, reply_post_id="999", confirmation_epoch=2_000_000_005,
    )
    assert owner.validate(receipt, lifecycle_state=lifecycle)
    draft = Mock(side_effect=AssertionError("invalid time reached draft validation"))
    canonical = Mock(side_effect=AssertionError("invalid time reached source hashing"))
    monkeypatch.setattr(values, "canonical_atomic_json_bytes", canonical)
    owner = replace(owner, draft_is_valid=draft)
    assert not owner.validate({**receipt, **changes}, lifecycle_state=lifecycle)
    draft.assert_not_called()
    canonical.assert_not_called()


def test_missing_source_hash_keeps_distinct_current_and_legacy_rules(monkeypatch, make_owner, receipt_family):
    _sending, confirmed, legacy = receipt_family
    receipt = dict(confirmed)
    receipt.pop("source_receipt_sha256", None)
    draft = Mock(return_value=True)
    canonical = Mock(side_effect=AssertionError("unbound receipt reached source hashing"))
    monkeypatch.setattr(values, "canonical_atomic_json_bytes", canonical)
    owner = make_owner(
        draft_is_valid=draft, legacy_draft_is_valid=draft,
    )
    assert owner.validate(receipt, lifecycle_state="confirmed", legacy_recovery=legacy) is (not legacy)
    draft.assert_called_once_with(receipt, receipt["reply_text"])
    assert draft.call_args.args[0] is receipt
    canonical.assert_not_called()
