from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

import pytest

from tests.helpers.adapter_assertions import assert_adapters_forward_current_dependencies

import mrs_bot_legacy_reply_validation as legacy
import single_call_reply
from tests.helpers.legacy_reply_fixtures import (
    CASE_IDS,
    _legacy_case,
    bot,
    isolated_recovery_paths,
)


def test_import_needs_no_runtime_access_and_root_aliases_share_owner_objects():
    code = """
import builtins, collections.abc, dataclasses, datetime, hashlib, io, json, os, random, re, socket, sys
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('legacy validation import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_legacy_reply_validation', 'mrs_bot_receipt_primitives'}:
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
before = random.getstate()
import mrs_bot_legacy_reply_validation
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'single_call_reply' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    constants = {name: value for name, value in vars(legacy).items() if name.startswith("_LEGACY_")}
    assert len(constants) == 19
    for name, value in constants.items():
        assert type(value) in {str, frozenset}
        assert getattr(bot, name) is value
    for name in (
        "_legacy_reply_value_sha256", "_legacy_reply_sha256_is_valid",
        "_legacy_reply_utc_timestamp_is_valid", "_legacy_ai_first_claim_is_valid",
        "_legacy_multi_model_context_post_is_valid", "_legacy_multi_model_reply_context_is_valid",
    ):
        assert getattr(bot, name) is getattr(legacy, name)
    assert legacy.hashlib is bot.hashlib and legacy.json is bot.json
    assert legacy.re is bot.re and legacy.datetime is bot.datetime


def test_adapters_forward_current_dependencies_arguments_results_and_errors(monkeypatch):
    names = (
        "_legacy_tested_reply_draft_is_valid", "_legacy_ai_first_sentence_assessment_is_valid",
        "_legacy_ai_first_claim_audit_is_valid", "_legacy_ai_first_reply_draft_is_valid",
        "_legacy_single_sol_reply_draft_is_valid", "_legacy_ai_reply_receipt_draft_is_valid",
    )
    assert_adapters_forward_current_dependencies(
        monkeypatch, bot=bot, implementation=legacy, names=names,
    )


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_dispatch_uses_owned_fixed_and_current_runtime_validators_in_order(case_id, monkeypatch):
    data = _legacy_case(case_id)["sending_receipt"]
    draft, context, text = data["ai_reply_draft"], data["reply_context"], data["reply_text"]
    events = Mock()
    result = object()
    context_check = Mock(return_value=True)
    events.attach_mock(context_check, "context")
    monkeypatch.setattr(legacy, "_legacy_multi_model_reply_context_is_valid", context_check)
    for family in ("tested", "ai_first", "single_sol"):
        callback = Mock(return_value=result)
        events.attach_mock(callback, family)
        target = bot if family == "single_sol" else legacy
        monkeypatch.setattr(target, f"_legacy_{family}_reply_draft_is_valid", callback)
    family = "tested" if case_id == "tested_reply_pipeline" else "ai_first" if case_id == "ai_first_reply_v3" else "single_sol"
    single = family == "single_sol"
    args = (data, draft) if single else (draft,)
    expected = getattr(call, family)(*args, context=context, text=text)
    assert bot._legacy_ai_reply_receipt_draft_is_valid(data, text) is (result if single else True)
    assert events.mock_calls == ([] if single else [call.context(context)]) + [expected]
    actual_args, actual_kwargs = getattr(events, family).call_args
    assert all(actual is original for actual, original in zip(actual_args, args))
    assert actual_kwargs["context"] is context and actual_kwargs["text"] is text
    if not single:
        events.reset_mock()
        context_check.return_value = False
        assert bot._legacy_ai_reply_receipt_draft_is_valid(data, text) is False
        assert events.mock_calls == [call.context(context)]
    events.reset_mock()
    draft["strategy_version"] = "unrecognised frozen strategy"
    assert bot._legacy_ai_reply_receipt_draft_is_valid(data, text) is False
    assert events.mock_calls == []


def test_context_uses_owned_schema_and_nested_post_validation(monkeypatch):
    context = _legacy_case("tested_reply_pipeline")["sending_receipt"]["reply_context"]
    quoted = {"post_id": "4000", "author_role": "user", "text": "Earlier contribution."}
    parent = {"post_id": "4001", "author_role": "account", "text": "Earlier reply."}
    context.update(quoted_post=quoted, parent_thread=[parent])
    check = Mock(wraps=bot._legacy_multi_model_context_post_is_valid)
    monkeypatch.setattr(legacy, "_legacy_multi_model_context_post_is_valid", check)
    assert bot._legacy_multi_model_reply_context_is_valid(context) is True
    assert check.call_args_list == [call(quoted), call(parent)]
    assert check.call_args_list[0].args[0] is quoted
    assert check.call_args_list[1].args[0] is parent
    check.reset_mock()
    original = legacy._LEGACY_MULTI_MODEL_REPLY_CONTEXT_FIELDS
    monkeypatch.setattr(legacy, "_LEGACY_MULTI_MODEL_REPLY_CONTEXT_FIELDS", original | {"extra_field"})
    assert bot._legacy_multi_model_reply_context_is_valid(context) is False
    check.assert_not_called()
    assert bot._LEGACY_MULTI_MODEL_REPLY_CONTEXT_FIELDS is original


@pytest.mark.parametrize("case_id", ["single_sol_schema_1", "single_sol_schema_2"])
def test_visible_conversation_uses_current_callback_and_preserves_native_empty_result_error(case_id, monkeypatch):
    data = _legacy_case(case_id)["sending_receipt"]
    context, text = data["reply_context"], data["reply_text"]
    bound = Mock(wraps=bot.bound_visible_conversation)
    monkeypatch.setattr(bot, "bound_visible_conversation", bound)
    monkeypatch.setattr(single_call_reply, "bound_visible_conversation", bound)
    assert bot._legacy_ai_reply_receipt_draft_is_valid(data, text) is True
    bound.assert_called_once_with(context["visible_conversation"], target_post_id=context["target_id"])
    assert bound.call_args.args[0] is context["visible_conversation"]
    bound.return_value = []
    with pytest.raises(IndexError, match="list index out of range"):
        bot._legacy_ai_reply_receipt_draft_is_valid(data, text)
    assert bot._reply_assembly()._reply_receipt_values_owner().legacy_sending_is_valid(data) is False


def test_single_sol_reads_current_size_limits_on_each_call(monkeypatch):
    data = _legacy_case("single_sol_schema_2")["sending_receipt"]
    draft, text = data["ai_reply_draft"], data["reply_text"]
    draft["trusted_fact_ids"] = ["F1"]
    draft["supplied_images"] = [{"identity": "fixture image", "sha256": "a" * 64, "mime_type": "image/png", "byte_count": 1}]
    draft["validated_draft_hash"] = bot._legacy_reply_value_sha256(
        {key: value for key, value in draft.items() if key != "validated_draft_hash"}
    )
    assert bot._legacy_ai_reply_receipt_draft_is_valid(data, text) is True
    for name in ("MAX_TRUSTED_FACTS", "MAX_SUPPLIED_IMAGES", "SINGLE_CALL_MAX_IMAGE_BYTES"):
        with monkeypatch.context() as patch:
            patch.setattr(bot, name, 0)
            assert bot._legacy_ai_reply_receipt_draft_is_valid(data, text) is False, name
        assert bot._legacy_ai_reply_receipt_draft_is_valid(data, text) is True


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_native_unhashable_field_error_reaches_existing_outer_receipt_boundary(case_id):
    data = _legacy_case(case_id)["sending_receipt"]
    draft, text = data["ai_reply_draft"], data["reply_text"]
    assert bot._legacy_ai_reply_receipt_draft_is_valid(data, text) is True
    draft["reply_kind" if case_id.startswith("single_sol") else "mode"] = []
    with pytest.raises(TypeError, match="unhashable type: 'list'"):
        bot._legacy_ai_reply_receipt_draft_is_valid(data, text)
    assert bot._reply_assembly()._reply_receipt_values_owner().legacy_sending_is_valid(data) is False


def test_json_hashing_and_contribution_hashing_keep_distinct_utf8_error_boundaries():
    data = _legacy_case("tested_reply_pipeline")["sending_receipt"]
    context, text = data["reply_context"], data["reply_text"]
    assert bot._legacy_reply_value_sha256(context) == data["ai_reply_draft"]["context_hash"]
    context["incoming_contribution"] = "Café — déjà vu."
    encoded = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8", errors="strict")
    assert bot._legacy_reply_value_sha256(context) == hashlib.sha256(encoded).hexdigest()
    context["incoming_contribution"] = "\ud800"
    assert bot._legacy_reply_value_sha256(context) is None
    with pytest.raises(UnicodeEncodeError):
        bot._legacy_ai_reply_receipt_draft_is_valid(data, text)
    assert bot._reply_assembly()._reply_receipt_values_owner().legacy_sending_is_valid(data) is False
