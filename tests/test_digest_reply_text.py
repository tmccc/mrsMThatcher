"""Focused checks for the prepared public reply-text callback boundary."""
from __future__ import annotations

import copy

import mrs_log_digest as digest


def test_text_result_keeps_provenance_callback_and_returned_reference_identity(monkeypatch):
    text = "Exact published text — " + "界" * 280 + "\nSecond line."
    refs = [{"record_number": 1}]
    candidates = [{"text": text, "source": "confirmed evidence", "source_refs": refs}]
    before = copy.deepcopy(candidates)
    bounded = [{"record_number": 2}]
    calls = []

    def source_refs(*collections):
        calls.append(collections)
        return bounded, 7

    monkeypatch.setattr(digest, "bounded_source_refs", source_refs)
    result = digest._public_reply_text_result(candidates, unavailable_reason="missing")
    assert calls == [(refs,)] and calls[0][0] is refs
    assert result["public_reply_text"] is text
    assert result["source_refs"] is bounded
    assert result["source_ref_omitted_count"] == 7
    assert candidates == before
    unavailable = digest._public_reply_text_result([], unavailable_reason="missing")
    assert calls[-1] == ()
    assert unavailable["public_reply_text_status"] == "unavailable"
    assert unavailable["source_refs"] is bounded


def test_durable_candidates_keep_validator_order_and_copied_candidate_identity(monkeypatch):
    receipt_text = "Receipt text\n" + "界" * 280
    history_text = "History text"
    cache_text = "Cache text"
    receipt = {"lane": "mention", "target_id": "123", "reply_post_id": "456",
               "reply_text": receipt_text}
    history = {"candidate_source": "mention", "target_id": "123", "reply_post_id": "456",
               "proposed_reply": history_text}
    cached = {"id": "456", "post_type": "auto_reply", "text": cache_text,
              "referenced_tweets": [{"type": "replied_to", "id": "123"}]}
    state = {"ai_reply_history": [history], "tweet_cache": {"456": cached}}
    before = copy.deepcopy((receipt, state))
    calls = []

    def validate(value):
        calls.append(value)
        return value is not history_text

    monkeypatch.setattr(digest, "valid_conversational_public_reply_text", validate)
    result = digest._durable_public_reply_text_candidates(
        state, lane="mention", target_id="123", reply_post_id="456",
        confirmed_receipt_evidence=[receipt],
    )
    assert len(calls) == 3
    assert all(actual is expected for actual, expected in zip(
        calls, [receipt_text, history_text, cache_text],
    ))
    assert result[0]["text"] is receipt_text
    assert result[1]["text"] is None
    assert result[1]["invalid_reason"] == "ai_reply_history text violates the conversational public-text contract"
    assert result[2]["text"] is cache_text
    assert [item["source"] for item in result] == [
        "confirmed_reply_receipt.json", "bot_state.json.ai_reply_history", "bot_state.json.tweet_cache",
    ]
    assert all(candidate is not original for candidate, original in zip(result, [receipt, history, cached]))
    assert (receipt, state) == before


def test_enrichment_keeps_delegation_order_event_identity_and_shallow_sharing(monkeypatch):
    event = {"kind": "mention_reply_posted", "mention_id": "123", "reply_post_id": "456"}
    excluded = dict(event)
    historical = {"kind": "historical_context_reply", "status": "completed",
                  "parent_post_id": "789", "quote_id": "a" * 64}
    events = [event, excluded, historical]
    section = {"events": [historical]}
    status = {"receipt": {"available": True}}
    report = {"events": events, "historical_context_replies": section}
    runtime = {"prepared": []}
    confirmation = {"lane": "mention+hot_post_reply", "target_id": "123", "reply_post_id": "456"}
    receipt = {"lane": "mention", "target_id": "999", "reply_post_id": "456", "reply_epoch": 12}
    receipts = [receipt]
    history = [{"authoritative": True, "parent_post_id": "789", "quote_id": "a" * 64,
                "reply_post_id": "987", "reply_text": "Historical text"}]
    inputs_before = copy.deepcopy((runtime, confirmation, receipts, history, status))
    production_ids = {id(event), id(historical)}
    candidates = [{"text": "not used by the substituted resolver"}]
    shared = []
    calls = []
    normalise = digest._normalised_structured_reply_confirmation
    sources = digest.bounded_source_refs

    def normalise_confirmation(value):
        calls.append("normalise")
        result = normalise(value)
        if value is confirmation:
            assert result is not value and result["lane"] == "mention"
        else:
            assert value["time"] == "converted epoch"
        return result

    def epoch(value):
        calls.append("epoch")
        assert value == 12 and type(value) is int
        return "converted epoch"

    def source_refs(*collections):
        calls.append("sources")
        return sources(*collections)

    def durable_candidates(value, **kwargs):
        calls.append("candidates")
        assert value is runtime and kwargs["confirmed_receipt_evidence"] is receipts
        # The prepared structured confirmation takes precedence over the receipt.
        assert kwargs["target_id"] == "123" and kwargs["lane"] == "mention"
        return candidates

    def resolve_text(value, *, unavailable_reason):
        calls.append("resolve")
        if len([call for call in calls if call == "resolve"]) == 1:
            assert value is candidates
        else:
            assert value[0]["text"] is history[0]["reply_text"]
        return {"public_reply_text_status": "conflict", "public_reply_text_reason": "test conflict",
                "public_reply_text_complete": False, "shared": shared}

    monkeypatch.setattr(digest, "_normalised_structured_reply_confirmation", normalise_confirmation)
    monkeypatch.setattr(digest, "epoch_to_london_text", epoch)
    monkeypatch.setattr(digest, "bounded_source_refs", source_refs)
    monkeypatch.setattr(digest, "_durable_public_reply_text_candidates", durable_candidates)
    monkeypatch.setattr(digest, "_public_reply_text_result", resolve_text)
    monkeypatch.setattr(digest, "PUBLISHED_REPLY_WARNING_LIMIT", 1)
    result = digest.enrich_published_reply_text(
        report, runtime_state=runtime, structured_reply_confirmations=[confirmation],
        historical_reply_text_evidence=history, confirmed_receipt_evidence=receipts,
        durable_evidence_status=status, production_event_object_ids=production_ids,
    )
    assert result is None and report["events"] is events
    assert len(events) == 3
    assert events[0] is event and events[1] is excluded and events[2] is historical
    assert excluded == {"kind": "mention_reply_posted", "mention_id": "123", "reply_post_id": "456"}
    assert event["shared"] is historical["shared"] is shared
    assert report["historical_context_replies"] is section
    assert section["events"][0] is historical
    health = report["published_reply_text_health"]
    assert health["durable_evidence"] is not status
    assert health["durable_evidence"]["receipt"] is status["receipt"]
    assert health["confirmed_record_count"] == health["conflict_count"] == 2
    assert len(health["warnings"]) == health["warning_omitted_count"] == 1
    assert health["warnings"][0]["reply_post_id"] == "456"
    assert production_ids == {id(event), id(historical)}
    assert (runtime, confirmation, receipts, history, status) == inputs_before
    assert calls == ["normalise", "epoch", "normalise", "sources", "candidates",
                     "resolve", "sources", "resolve", "sources"]
