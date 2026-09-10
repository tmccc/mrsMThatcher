"""Authority and provenance contracts for structured publication observations."""
from __future__ import annotations

import copy
import json
from datetime import datetime

import pytest

import mrs_log_digest as digest
import mrs_log_digest_reply_evidence as evidence
from mrs_log_digest_values import (
    SHA256_LOWER_RE,
    valid_bounded_utf8_text,
    valid_string_public_post_id,
)


def observation_metadata():
    return {
        "time_text": lambda: "2026-09-10 12:00:00",
        "event_insertion_index": lambda: 7,
        "source_sequence": 13,
        "make_source_ref": lambda: {"input_file_index": 2, "record_number": 14},
    }


def validators():
    return {
        "valid_string_public_post_id": valid_string_public_post_id,
        "valid_bounded_utf8_text": valid_bounded_utf8_text,
        "sha256_fullmatch": SHA256_LOWER_RE.fullmatch,
    }


def historical_publication(**changes):
    return {
        "event": "historical_context_reply_posted",
        "event_version": 1,
        "lane": "historical_context_reply",
        "publication_authority": "confirmed_transport",
        "parent_post_id": "101",
        "reply_post_id": "202",
        "root_post_id": "101",
        "conversation_id": "101",
        "quote_id": "a" * 64,
        "reply_text": "Exact published text.\nA second paragraph.",
        **changes,
    }


def test_reply_confirmation_preserves_fields_for_later_identity_validation():
    event = {
        "event": "reply_posted", "lane": "mention", "target_id": "101",
        "reply_post_id": "202", "author_id": "303", "original_post_id": 404,
    }
    original = copy.deepcopy(event)
    result = evidence.prepare_structured_reply_confirmation(
        event, production_record=True, **observation_metadata(),
    )

    assert result == {
        "time": "2026-09-10 12:00:00", "lane": "mention", "target_id": "101",
        "reply_post_id": "202", "author_id": "303", "original_post_id": 404,
        "_event_insertion_index": 7, "_source_sequence": 13,
        "source_refs": [{"input_file_index": 2, "record_number": 14}],
    }
    assert event == original


@pytest.mark.parametrize("event, production", [
    (None, True), ({}, True), ({"event": "draft_reply"}, True),
    ({"event": "reply_posted"}, False),
])
def test_untrusted_reply_confirmation_has_no_observation(event, production):
    def unavailable():
        pytest.fail("Rejected confirmations must not create provenance")

    assert evidence.prepare_structured_reply_confirmation(
        event, production_record=production, time_text=unavailable,
        event_insertion_index=unavailable, source_sequence=13,
        make_source_ref=unavailable,
    ) is None


def test_historical_confirmation_prefers_strict_evidence_and_keeps_exact_text():
    strict = historical_publication()
    display = historical_publication(reply_text="Display-only text", reply_post_id="999")
    original = copy.deepcopy(strict)
    result = evidence.prepare_structured_historical_publication_evidence(
        display, strict, production_record=True,
        **validators(), **observation_metadata(),
    )

    assert result == {
        "time": "2026-09-10 12:00:00", "parent_post_id": "101",
        "reply_post_id": "202", "quote_id": "a" * 64,
        "authoritative": True, "reply_text": strict["reply_text"],
        "source": "structured historical_context_reply_posted", "durable_only": False,
        "_event_insertion_index": 7, "_source_sequence": 13,
        "source_refs": [{"input_file_index": 2, "record_number": 14}],
    }
    assert strict == original


@pytest.mark.parametrize("changes", [
    {"event_version": True}, {"event_version": 1.0},
    {"parent_post_id": 101}, {"reply_post_id": True},
    {"root_post_id": "102"}, {"conversation_id": "102"},
    {"quote_id": "A" * 64}, {"reply_text": "invalid\ud800text"},
    {"lane": "mention"}, {"publication_authority": "sending"},
])
def test_malformed_historical_confirmation_cannot_supply_public_text(changes):
    event = historical_publication(**changes)
    result = evidence.prepare_structured_historical_publication_evidence(
        event, event, production_record=True, **validators(), **observation_metadata(),
    )

    assert result["authoritative"] is False
    assert result["reply_text"] is None
    assert result["durable_only"] is False
    assert result["_source_sequence"] == 13
    if not isinstance(event["parent_post_id"], str):
        assert result["parent_post_id"] == ""
    if not isinstance(event["reply_post_id"], str):
        assert result["reply_post_id"] == ""


@pytest.mark.parametrize("strict_kind, production", [
    ("absent", True), ("wrong_event", True), ("valid", False),
])
def test_display_or_selftest_historical_confirmation_retains_no_public_text(strict_kind, production):
    event = historical_publication()
    strict = {"absent": None, "wrong_event": {"event": "reply_posted"}, "valid": event}[strict_kind]
    result = evidence.prepare_structured_historical_publication_evidence(
        event, strict, production_record=production,
        **validators(), **observation_metadata(),
    )

    assert result["parent_post_id"] == "101"
    assert result["reply_post_id"] == "202"
    assert result["authoritative"] is False
    assert result["reply_text"] is None


def completion_anchor(**changes):
    return {
        "event": "historical_context_reply", "status": "completed",
        "parent_post_id": "101", "quote_id": "a" * 64, "character_count": 42,
        **changes,
    }


@pytest.mark.parametrize("status, fields", [
    ("completed", {"character_count": 0}),
    ("already_completed", {"character_count": 25_000, "reply_preview": ""}),
    ("completed", {"reply_preview": "A bounded preview"}),
])
def test_valid_completion_anchor_accepts_optional_preview_and_boundary_lengths(status, fields):
    event = completion_anchor(status=status, **fields)
    original = copy.deepcopy(event)
    assert evidence.valid_structured_historical_completion_anchor(
        event, status, **validators(),
    ) is True
    assert event == original


@pytest.mark.parametrize("fields", [
    {"event": "historical_context_reply_posted"}, {"status": "already_completed"},
    {"parent_post_id": 101}, {"quote_id": "A" * 64},
    {"character_count": True}, {"character_count": 42.0},
    {"character_count": -1}, {"character_count": 25_001},
    {"reply_preview": None}, {"reply_preview": "invalid\ud800text"},
])
def test_malformed_completion_anchor_cannot_authorize_durable_text(fields):
    assert evidence.valid_structured_historical_completion_anchor(
        completion_anchor(**fields), "completed", **validators(),
    ) is False


def test_absent_strict_completion_anchor_is_not_display_authority():
    assert evidence.valid_structured_historical_completion_anchor(
        None, "completed", **validators(),
    ) is False


def test_historical_provenance_is_captured_after_validation_and_time_formatting():
    pending_events = []
    source_ref = {"record_number": 14}

    def validate_text(text):
        pending_events.append("validated publication")
        return valid_bounded_utf8_text(text)

    def time_text():
        pending_events.append("formatted observation")
        return "2026-09-10 12:00:00"

    event = historical_publication()
    result = evidence.prepare_structured_historical_publication_evidence(
        event, event, production_record=True,
        valid_string_public_post_id=valid_string_public_post_id,
        valid_bounded_utf8_text=validate_text, sha256_fullmatch=SHA256_LOWER_RE.fullmatch,
        time_text=time_text, event_insertion_index=lambda: len(pending_events),
        source_sequence=13, make_source_ref=lambda: source_ref,
    )

    assert result["authoritative"] is True
    assert result["_event_insertion_index"] == 2
    assert result["source_refs"][0] is source_ref


def test_coordinator_uses_current_validator_for_each_historical_identity(monkeypatch):
    retained = []

    def replace_validator(value):
        monkeypatch.setattr(digest, "valid_string_public_post_id", lambda _value: False)
        return valid_string_public_post_id(value)

    def retain_evidence(report, **kwargs):
        retained.extend(kwargs["historical_reply_text_evidence"])

    monkeypatch.setattr(digest, "valid_string_public_post_id", replace_validator)
    monkeypatch.setattr(digest, "enrich_published_reply_text", retain_evidence)
    timestamp = datetime(2026, 9, 10, 12)
    record = digest.Record(
        timestamp, "INFO", "log_event", 1,
        "EVENT " + json.dumps(historical_publication()), "fixture.log", 14,
    )
    digest.analyse([record], generation_time=timestamp)

    assert len(retained) == 1
    assert retained[0]["authoritative"] is False
    assert retained[0]["reply_text"] is None
