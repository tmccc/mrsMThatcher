"""Focused checks for the durable reply-evidence callback boundary."""
from __future__ import annotations

import copy
import hashlib
import importlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import mrs_log_digest as digest
import mrs_log_digest_reply_evidence as evidence


@pytest.mark.parametrize("kind", ["receipt", "history"])
def test_loaders_keep_private_read_parse_encode_validation_order_and_identity(
    tmp_path, monkeypatch, kind,
):
    row = {"count": 7, "ratio": 1.25, "enabled": True}
    if kind == "receipt":
        basename = "confirmed_reply_receipt.json"
        maximum = digest.CONFIRMED_REPLY_RECEIPT_MAX_BYTES
        encoder_name = "canonical_atomic_json_bytes"
        value = row
        load = digest.load_confirmed_reply_receipt_evidence
    else:
        basename = "historical_context_reply_history.json"
        maximum = digest.HISTORICAL_REPLY_HISTORY_MAX_BYTES
        encoder_name = "canonical_private_json_bytes"
        value = {"schema_version": 1, "items": {"123": row, "456": {}}}
        load = digest.load_historical_reply_history_evidence
    raw = getattr(digest, encoder_name)(value)
    native_parser = digest._strict_native_json_object
    calls = []
    parsed = []
    projection = {"shared": []}

    def read_bytes(path, *, maximum):
        calls.append(("read", path, maximum))
        return raw

    def parse(data, *, label):
        calls.append(("parse", label))
        assert data is raw
        result = native_parser(data, label=label)
        observed = result if kind == "receipt" else result["items"]["123"]
        assert type(observed["count"]) is int
        assert type(observed["ratio"]) is float
        assert type(observed["enabled"]) is bool
        parsed.append(result)
        return result

    def encode(value):
        calls.append("encode")
        assert value is parsed[-1]
        return raw

    def validate_receipt(value):
        calls.append("receipt")
        assert value is parsed[-1]
        return projection

    def validate_completed(key, value):
        calls.append(("completed", key))
        assert value is parsed[-1]["items"][key]
        return projection if key == "123" else None

    def validate_failed(key, value):
        calls.append(("failed", key))
        assert value is parsed[-1]["items"][key]
        return True

    monkeypatch.setattr(digest, "read_stable_private_json_bytes", read_bytes)
    monkeypatch.setattr(digest, "_strict_native_json_object", parse)
    monkeypatch.setattr(digest, encoder_name, encode)
    monkeypatch.setattr(digest, "_confirmed_conversational_receipt_evidence", validate_receipt)
    monkeypatch.setattr(digest, "_valid_historical_completed_item", validate_completed)
    monkeypatch.setattr(digest, "_valid_historical_failed_item", validate_failed)
    rows, status = load(tmp_path)
    assert rows == [projection] and rows[0] is projection
    assert status["available"] is True and status["status"] == "available"
    prefix = [("read", tmp_path / basename, maximum), ("parse", basename), "encode"]
    assert calls == prefix + (
        ["receipt"] if kind == "receipt"
        else [("completed", "123"), ("completed", "456"), ("failed", "456")]
    )
    if kind == "history":
        assert status["completed_record_count"] == status["failed_record_count"] == 1

    def noncanonical(value):
        encode(value)
        return raw + b" "

    calls.clear()
    monkeypatch.setattr(digest, encoder_name, noncanonical)
    rows, status = load(tmp_path)
    assert rows == [] and status["status"] == "unavailable"
    assert status["reason"] == "unavailable: ValueError"
    assert calls == prefix


def test_receipt_keeps_text_draft_time_timezone_and_atomic_encoder_callbacks(monkeypatch):
    text = "Exact published text — " + "界" * 280 + "\nSecond line."
    context = {
        "lane": "quote_tweet", "target_id": "123", "thread_id": "456",
        "incoming_contribution": "A civil contribution.", "quoted_post": {"post_id": "789"},
    }
    draft = {
        "schema_version": 1, "strategy_version": "tested-reply-pipeline-20260817",
        "target_id": "123", "thread_id": "456", "candidate_source": "quote_tweet",
        "contribution_hash": hashlib.sha256(context["incoming_contribution"].encode()).hexdigest(),
        "context_hash": digest._structured_value_sha256(context),
        "trusted_facts_hash": digest._structured_value_sha256([]),
        "proposed_reply": text, "mode": "opinion_or_principle", "final_reply_kind": "unknown",
        "tone": "unknown", "factual_claims": [], "evidence_ids": None,
        "trusted_facts_supplied_count": 0, "trusted_fact_ids_supplied": [],
        "used_fact_count": "unknown", "used_fact_ids": None, "claim_risk_categories": [],
        "reviewer_verdict": "approve", "model_call_count": 3, "revision_count": 0,
        "reply_requirement": "general", "route_source": "xai_gate",
        "creation_time": "2026-08-30T22:59:00Z",
    }
    draft["approval_hash"] = digest._structured_value_sha256(draft)
    london = timezone(timedelta(hours=1), "test-london")
    attempt = int(datetime(2026, 8, 30, 22, 59, 59, tzinfo=timezone.utc).timestamp())
    confirmed = attempt + 2
    sending = {
        "schema_version": 4, "lifecycle_state": "sending", "target_id": "123",
        "author_id": "101", "candidate_source": "quote_tweet", "conversation_id": "456",
        "original_post_id": "789", "reply_text": text, "reply_context": context,
        "ai_reply_draft": draft, "attempt_epoch": attempt, "reply_epoch": attempt,
        "daily_reply_date": "2026-08-30", "daily_quote_reply_date": "2026-08-30",
    }
    atomic_encoder = digest.canonical_atomic_json_bytes
    receipt = {
        **sending, "lifecycle_state": "confirmed", "reply_post_id": "999",
        "reply_epoch": confirmed, "confirmation_epoch": confirmed,
        "daily_reply_date": "2026-08-31", "daily_quote_reply_date": "2026-08-31",
        "source_receipt_sha256": hashlib.sha256(atomic_encoder(sending)).hexdigest(),
    }
    before = copy.deepcopy(receipt)
    calls = []
    original_draft_validator = digest._valid_durable_ai_reply_draft

    def valid_text(value):
        calls.append("text")
        assert value is text
        return evidence.valid_conversational_public_reply_text(value)

    def valid_draft(value, **kwargs):
        calls.append("draft")
        assert value is draft and kwargs["context"] is context
        return original_draft_validator(value, **kwargs)

    def fromisoformat(value):
        calls.append(("iso", value))
        return datetime.fromisoformat(value)

    def fromtimestamp(value, *, tz):
        calls.append(("epoch", value))
        assert tz is london
        return datetime.fromtimestamp(value, tz=tz)

    def encode(value):
        calls.append("encode")
        assert value == sending and value is not receipt
        assert value["reply_context"] is context and value["ai_reply_draft"] is draft
        return atomic_encoder(value)

    monkeypatch.setattr(digest, "valid_conversational_public_reply_text", valid_text)
    monkeypatch.setattr(digest, "_valid_durable_ai_reply_draft", valid_draft)
    monkeypatch.setattr(digest, "datetime", SimpleNamespace(
        fromisoformat=fromisoformat, fromtimestamp=fromtimestamp,
    ))
    monkeypatch.setattr(digest, "LONDON", london)
    monkeypatch.setattr(digest, "canonical_atomic_json_bytes", encode)
    result = digest._confirmed_conversational_receipt_evidence(receipt)
    assert result["reply_text"] is text and result["reply_epoch"] is confirmed
    assert receipt == before
    assert calls == [
        "text", "draft", ("iso", "2026-08-30T22:59:00+00:00"),
        ("epoch", confirmed), ("epoch", attempt), "encode",
    ]
    calls.clear()
    monkeypatch.setattr(digest, "_valid_canonical_utc_timestamp", lambda value: False)
    assert digest._confirmed_conversational_receipt_evidence(receipt) is None
    assert calls == ["text", "draft"]
    calls.clear()
    monkeypatch.setattr(digest, "valid_conversational_public_reply_text", lambda value: False)
    assert digest._confirmed_conversational_receipt_evidence(receipt) is None
    assert calls == []


def test_historical_validators_keep_private_encoder_sharing_and_utc_delegation(monkeypatch):
    metadata = {
        "formatter_version": "historical_context_reply_schema_v2", "template_variant": "test",
        "meaning_included": False, "meaning_decision_reason": "omitted",
        "raw_character_count": 1, "weighted_character_count": 1, "verification_label": None,
        "source_class": "test", "historical_confidence": "high", "shortening_applied": False,
    }
    sending = {
        "schema_version": 1, "lifecycle_state": "sending", "parent_post_id": "123",
        "quote_id": "a" * 64, "reply_text": "Published context\n" + "界" * 280,
        "reply_epoch": 1_788_125_250, "started_at": "retained historical time",
        "attempt_number": 1, "formatter_metadata": metadata,
    }
    private_encoder = digest.canonical_private_json_bytes
    item = {
        **sending, "status": "completed", "lifecycle_state": "confirmed",
        "reply_post_id": "999", "confirmed_at": "retained confirmation time",
        "source_receipt_sha256": hashlib.sha256(private_encoder(sending)).hexdigest(),
    }
    before = copy.deepcopy(item)
    calls = []

    def encode(value):
        calls.append("encode")
        assert value == sending and value is not item
        assert value["formatter_metadata"] is metadata
        return private_encoder(value)

    monkeypatch.setattr(digest, "canonical_private_json_bytes", encode)
    result = digest._valid_historical_completed_item("123", item)
    assert result is not item and result["reply_text"] is item["reply_text"]
    assert result["time"] == "retained confirmation time"
    assert item == before and calls == ["encode"]
    failed = {
        "parent_post_id": "123", "quote_id": "a" * 64, "reply_text": "Failed text.",
        "status": "failed", "failure": "proved non-success", "attempt_count": 1,
        "updated_at": "2026-08-30T20:44:10Z",
    }

    def reject(value):
        calls.append(value)
        return False

    monkeypatch.setattr(digest, "_valid_canonical_utc_timestamp", reject)
    assert digest._valid_historical_failed_item("123", failed) is False
    assert calls == ["encode", failed["updated_at"]]


def test_utc_parser_keeps_short_circuit_and_exception_identity(monkeypatch):
    failure = ValueError("synthetic ISO parser failure")
    calls = []

    def parse(value):
        calls.append(value)
        raise failure

    monkeypatch.setattr(digest, "datetime", SimpleNamespace(fromisoformat=parse))
    assert digest._valid_canonical_utc_timestamp(True) is False
    assert calls == []
    assert digest._valid_canonical_utc_timestamp("2026-08-30T20:44:10Z") is False
    failure = TypeError("synthetic ISO parser failure")
    with pytest.raises(TypeError) as caught:
        digest._valid_canonical_utc_timestamp("2026-08-30T20:44:10Z")
    assert caught.value is failure
    assert calls == ["2026-08-30T20:44:10+00:00"] * 2


@pytest.mark.parametrize("module_name", [
    "mrs_log_digest_reply_evidence", "mrs_log_digest_reply_text", "mrs_log_digest_incidents",
    "mrs_log_digest_quote_publication",
])
def test_evidence_import_is_inert_and_pure_digest_aliases_keep_identity(tmp_path, module_name):
    script = """
import datetime
import decimal
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import typing

handlers = list(logging.getLogger().handlers)
loggers = set(logging.Logger.manager.loggerDict)

def no_home(*args, **kwargs):
    raise AssertionError("home lookup")

Path.home = no_home
os.path.expanduser = no_home

def audit(event, args):
    if event == "open":
        path, mode, flags = args
        assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC), args
        assert isinstance(path, str) and path.endswith((".py", ".pyc", ".so")), args
    assert not event.startswith(("socket.", "subprocess.")), event
    assert event not in {"os.mkdir", "os.remove", "os.rename", "os.system", "os.listdir", "os.scandir"}, event

# Resolve the import directory cache before forbidding runtime directory scans.
import importlib.util
assert importlib.util.find_spec("mrs_log_digest_reply_evidence") is not None
sys.addaudithook(audit)
import mrs_log_digest_reply_evidence
assert not {"mrs_log_digest", "mrs_log_digest_markdown", "mrsMThatcher2", "single_call_reply", "historical_context_formatter"} & sys.modules.keys()
assert list(logging.getLogger().handlers) == handlers
assert set(logging.Logger.manager.loggerDict) == loggers
"""
    script = script.replace("mrs_log_digest_reply_evidence", module_name)
    env = {**os.environ, "PYTHONPATH": str(Path(digest.__file__).resolve().parent)}
    result = subprocess.run(
        [sys.executable, "-B", "-c", script], cwd=tmp_path, env=env,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    names = (
        "ENGAGEMENT_QUESTION_PUBLIC_TEXT_SEPARATOR", "ENGAGEMENT_QUESTION_EXPERIMENT_ID",
        "ENGAGEMENT_QUESTION_EXPERIMENT_STATE_SCHEMA_VERSION",
        "ENGAGEMENT_QUESTION_EXPERIMENT_STATUSES", "ENGAGEMENT_PAIR_ID_RE",
        "ENGAGEMENT_PUBLICATION_ORDERS", "ENGAGEMENT_ARMS",
        "ENGAGEMENT_MAX_CONFIRMED_PUBLICATIONS",
    ) if module_name == "mrs_log_digest_quote_publication" else (
        "_incident_exception_line", "_normalise_incident_text", "_event_time",
        "REMOTE_OPERATION_SCOPE_LABELS", "REMOTE_CONTROL_SCOPE_BY_KEY", "REMOTE_LANE_SCOPE",
    ) if module_name == "mrs_log_digest_incidents" else (
        "valid_conversational_public_reply_text", "_structured_value_sha256",
        "_valid_historical_formatter_metadata",
    ) if module_name == evidence.__name__ else (
        "_normalised_structured_reply_confirmation", "PUBLISHED_REPLY_WARNING_LIMIT",
    )
    module = importlib.import_module(module_name)
    for name in names:
        assert getattr(digest, name) is getattr(module, name)
