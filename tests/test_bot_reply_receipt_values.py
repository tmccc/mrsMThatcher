from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

import pytest

import mrs_bot_reply_receipt_values as values
from tests.test_legacy_conversational_reply_recovery import (
    CASE_IDS,
    _confirmed_receipt,
    _legacy_case,
)
from tests.test_unit_helpers import (
    bot,
    isolate_regular_post_receipt,
    unit_approved_reply,
    unit_sending_v4_reply_receipt,
    unit_v4_reply_receipt_template,
)


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, hashlib, io, logging, os, random, re, socket, sys
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('reply receipt values import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name != 'mrs_bot_reply_receipt_values':
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


def test_adapters_forward_current_dependencies_arguments_results_and_errors(monkeypatch):
    names = (
        "mention_pagination_provenance_is_valid",
        "_conversational_reply_receipt_is_semantically_valid",
        "conversational_sending_receipt_from_confirmed",
        "confirmed_reply_receipt_is_semantically_valid",
        "sending_reply_receipt_is_semantically_valid",
        "_legacy_confirmed_reply_receipt_is_semantically_valid",
        "_legacy_sending_reply_receipt_is_semantically_valid",
        "bind_conversational_reply_attempt_time",
        "_confirmed_reply_receipt_from_sending",
        "_reply_confirmation_epoch_after_remote_success",
        "conversational_reply_confirmation_epoch",
    )
    for name in names:
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        dependencies = inspect.signature(getattr(values, name)).parameters.keys() - public.keys()
        args = tuple(object() for parameter in public.values() if parameter.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD)
        options = {key: object() for key, parameter in public.items() if parameter.kind == inspect.Parameter.KEYWORD_ONLY}
        result = object()
        owner = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch.setattr(values, name, owner)
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


def test_lifecycle_dispatch_uses_current_root_callback_and_exact_flags(monkeypatch):
    data, result = object(), object()
    for name, lifecycle, legacy in (
        ("confirmed_reply_receipt_is_semantically_valid", "confirmed", False),
        ("sending_reply_receipt_is_semantically_valid", "sending", False),
        ("_legacy_confirmed_reply_receipt_is_semantically_valid", "confirmed", True),
        ("_legacy_sending_reply_receipt_is_semantically_valid", "sending", True),
    ):
        callback = Mock(return_value=result)
        monkeypatch.setattr(bot, "_conversational_reply_receipt_is_semantically_valid", callback)
        assert getattr(bot, name)(data) is result
        options = {"lifecycle_state": lifecycle}
        if legacy:
            options["legacy_recovery"] = True
        callback.assert_called_once_with(data, **options)


@pytest.fixture(params=["current", *CASE_IDS])
def receipt_family(request):
    if request.param == "current":
        sending = unit_sending_v4_reply_receipt()
        confirmed = bot._confirmed_reply_receipt_from_sending(
            sending, reply_post_id="999", confirmation_epoch=2_000_000_005,
        )
        return sending, confirmed, False
    case = _legacy_case(request.param)
    return case["sending_receipt"], _confirmed_receipt(case), True


def test_source_validation_uses_current_family_callbacks_in_order(monkeypatch, receipt_family):
    sending, confirmed, legacy = receipt_family
    validate = (
        bot._legacy_confirmed_reply_receipt_is_semantically_valid
        if legacy else bot.confirmed_reply_receipt_is_semantically_valid
    )
    assert validate(confirmed)
    if legacy:
        assert not bot.sending_reply_receipt_is_semantically_valid(sending)
    trace = Mock()
    trace.draft = Mock(return_value=True)
    trace.reconstruct = Mock(wraps=bot.conversational_sending_receipt_from_confirmed)
    trace.sending = Mock(return_value=True)
    trace.canonical = Mock(wraps=bot.canonical_atomic_json_bytes)
    prefix = "_legacy_" if legacy else ""
    other_prefix = "" if legacy else "_legacy_"
    monkeypatch.setattr(bot, prefix + "ai_reply_receipt_draft_is_valid", trace.draft)
    monkeypatch.setattr(bot, other_prefix + "ai_reply_receipt_draft_is_valid", Mock(side_effect=AssertionError("wrong draft family")))
    monkeypatch.setattr(bot, "conversational_sending_receipt_from_confirmed", trace.reconstruct)
    monkeypatch.setattr(bot, prefix + "sending_reply_receipt_is_semantically_valid", trace.sending)
    monkeypatch.setattr(bot, other_prefix + "sending_reply_receipt_is_semantically_valid", Mock(side_effect=AssertionError("wrong source family")))
    monkeypatch.setattr(bot, "canonical_atomic_json_bytes", trace.canonical)

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


def test_source_reconstruction_errors_are_caught_before_hashing(monkeypatch):
    sending = unit_sending_v4_reply_receipt()
    confirmed = bot._confirmed_reply_receipt_from_sending(
        sending, reply_post_id="999", confirmation_epoch=2_000_000_005,
    )
    reconstruct = Mock(side_effect=ValueError("invalid source"))
    canonical = Mock(side_effect=UnicodeError("unencodable source"))
    monkeypatch.setattr(bot, "conversational_sending_receipt_from_confirmed", reconstruct)
    monkeypatch.setattr(bot, "canonical_atomic_json_bytes", canonical)
    assert not bot.confirmed_reply_receipt_is_semantically_valid(confirmed)
    canonical.assert_not_called()
    reconstruct.side_effect = None
    reconstruct.return_value = sending
    with pytest.raises(UnicodeError) as caught:
        bot.confirmed_reply_receipt_is_semantically_valid(confirmed)
    assert caught.value is canonical.side_effect
    assert canonical.call_args.args[0] is sending


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
def test_attempt_binding_preserves_references_and_clock_date_validation_order(monkeypatch, lane):
    template = unit_v4_reply_receipt_template(lane=lane)
    reply = unit_approved_reply(template["reply_context"], text=template["reply_text"])
    template["reply_text"], template["ai_reply_draft"] = reply, reply.draft_record
    before = bot.canonical_atomic_json_bytes(template)
    trace = Mock()
    trace.clock.return_value = 2_000_000_000
    trace.date = Mock(wraps=bot.reply_cap_date_str)
    trace.validate = Mock(wraps=bot.sending_reply_receipt_is_semantically_valid)
    monkeypatch.setattr(bot, "now_epoch", trace.clock)
    monkeypatch.setattr(bot, "reply_cap_date_str", trace.date)
    monkeypatch.setattr(bot, "sending_reply_receipt_is_semantically_valid", trace.validate)

    prepared = bot.bind_conversational_reply_attempt_time(template)
    # The real validator also reaches the current date helper through its safe wrapper.
    assert [entry[0] for entry in trace.mock_calls][:3] == ["clock", "date", "validate"]
    assert trace.date.call_args.args == (2_000_000_000,)
    assert trace.validate.call_args.args[0] is prepared
    assert prepared is not template
    assert all(prepared[key] is value for key, value in template.items())
    assert bot.canonical_atomic_json_bytes(template) == before

    trace.reset_mock()
    trace.validate.return_value = False
    with pytest.raises(RuntimeError, match="^Internal error: prepared reply attempt failed validation$"):
        bot.bind_conversational_reply_attempt_time(template)
    assert [entry[0] for entry in trace.mock_calls] == ["clock", "date", "validate"]
    assert bot.canonical_atomic_json_bytes(template) == before
    trace.reset_mock()
    with pytest.raises(RuntimeError, match="^Reply attempt template already contains timing fields$"):
        bot.bind_conversational_reply_attempt_time({**template, "confirmation_epoch": None})
    assert not trace.mock_calls


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
def test_projection_and_reconstruction_keep_shallow_copies_and_exact_hash_input(monkeypatch, lane):
    sending = unit_sending_v4_reply_receipt(lane=lane)
    reply = unit_approved_reply(sending["reply_context"], text=sending["reply_text"])
    sending["reply_text"], sending["ai_reply_draft"] = reply, reply.draft_record
    before = dict(sending)
    canonical = Mock(return_value=b"exact current canonical sending bytes\n")
    date = Mock(return_value="confirmation-date")
    monkeypatch.setattr(bot, "canonical_atomic_json_bytes", canonical)
    monkeypatch.setattr(bot, "reply_cap_date_str", date)
    confirmed = bot._confirmed_reply_receipt_from_sending(
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
    monkeypatch.setattr(bot, "receipt_int", integer)
    monkeypatch.setattr(bot, "safe_reply_cap_date_str", safe_date)
    reconstructed = bot.conversational_sending_receipt_from_confirmed(confirmed)
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


def test_confirmation_projection_preserves_existing_version_equality(monkeypatch):
    sending = unit_sending_v4_reply_receipt()
    sending["schema_version"] = 4.0
    canonical = Mock(wraps=bot.canonical_atomic_json_bytes)
    monkeypatch.setattr(bot, "canonical_atomic_json_bytes", canonical)
    confirmed = bot._confirmed_reply_receipt_from_sending(
        sending, reply_post_id=999, confirmation_epoch=2_000_000_005,
    )
    assert confirmed["confirmation_epoch"] == 2_000_000_005
    assert canonical.call_args.args[0] is sending
    canonical.reset_mock()
    sending["schema_version"] = 3
    confirmed = bot._confirmed_reply_receipt_from_sending(
        sending, reply_post_id=999, confirmation_epoch=2_000_000_005,
    )
    assert confirmed == {**sending, "lifecycle_state": "confirmed", "reply_post_id": "999"}
    canonical.assert_not_called()


def test_observed_confirmation_uses_current_clock_converter_and_exact_warning(monkeypatch):
    trace = Mock()
    trace.clock.return_value = "19"
    trace.integer.return_value = 20
    monkeypatch.setattr(bot, "now_epoch", trace.clock)
    monkeypatch.setattr(bot, "receipt_int", trace.integer)
    monkeypatch.setattr(bot, "log", trace.log)
    attempt = object()
    sending = {"schema_version": 4.0, "attempt_epoch": attempt}
    assert bot._reply_confirmation_epoch_after_remote_success(sending) == 20
    assert trace.mock_calls == [
        call.clock(), call.integer(attempt),
        call.log.warning(
            "Wall clock moved backward during conversational reply creation; "
            "using durable attempt epoch as conservative confirmation time "
            "attempt_epoch=%s observed_epoch=%s", 20, 19,
        ),
    ]
    trace.reset_mock()
    assert bot._reply_confirmation_epoch_after_remote_success({"schema_version": 3}, 23.9) == 23
    assert not trace.mock_calls
    with pytest.raises(ValueError):
        bot._reply_confirmation_epoch_after_remote_success(sending, "invalid")
    assert not trace.mock_calls


@pytest.mark.parametrize("version, field, message", [
    (4.0, "confirmation_epoch", "Schema-v4 confirmed reply receipt lacks a confirmation epoch"),
    (3, "reply_epoch", "Legacy confirmed reply receipt lacks its best-known reply epoch"),
])
def test_best_confirmation_time_uses_current_converter_and_exception(monkeypatch, version, field, message):
    class CurrentReceiptError(Exception):
        pass

    raw, converted = object(), object()
    integer = Mock(return_value=converted)
    monkeypatch.setattr(bot, "receipt_int", integer)
    monkeypatch.setattr(bot, "InvalidConfirmedReplyReceipt", CurrentReceiptError)
    receipt = {"schema_version": version, field: raw}
    assert bot.conversational_reply_confirmation_epoch(receipt) is converted
    integer.assert_called_once_with(raw)
    integer.return_value = None
    with pytest.raises(CurrentReceiptError) as caught:
        bot.conversational_reply_confirmation_epoch(receipt)
    assert type(caught.value) is CurrentReceiptError
    assert str(caught.value) == message


def test_pagination_uses_current_id_callback_before_token_validation(monkeypatch):
    bounded = Mock(return_value=0)
    monkeypatch.setattr(bot, "bounded_tweet_id_value", bounded)
    assert bot.mention_pagination_provenance_is_valid({"base_since_id": "", "next_token": "page-2"})
    bounded.assert_called_once_with("", allow_empty=True)
    bounded.reset_mock()
    assert not bot.mention_pagination_provenance_is_valid({"base_since_id": ""})
    bounded.assert_not_called()
    failure = ValueError("current ID validator failed")
    bounded.side_effect = failure
    with pytest.raises(ValueError) as caught:
        bot.mention_pagination_provenance_is_valid({"base_since_id": "", "next_token": None})
    assert caught.value is failure
