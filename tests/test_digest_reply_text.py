"""Focused checks for the prepared public reply-text callback boundary."""
from __future__ import annotations

import copy

import pytest

import mrs_log_digest as digest
import mrs_log_digest_reply_text as reply_text


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


def test_confirmation_index_keeps_order_duplicates_references_and_receipt_sequence():
    shared = []
    first = {"reply_post_id": "20", "target_id": "first", "source_refs": shared}
    second = {"reply_post_id": "20", "target_id": "second"}
    third = {"reply_post_id": "10"}
    ignored = {}
    structured = [first, ignored, second, first, third]
    receipts = [
        {"reply_post_id": reply_id, "reply_epoch": epoch}
        for reply_id, epoch in [("20", "11"), ("skip", None), ("30", 12.9),
                                ("30", True), ("40", 0)]
    ]
    before = copy.deepcopy((structured, receipts))
    calls = []
    projected = []

    class Events(list):
        def __len__(self):
            size = super().__len__()
            calls.append(("len", size))
            return size

    events = Events([{}])

    def normalise(value):
        calls.append(("normalise", value))
        if "publication_authority" in value:
            projected.append(value)
            events.append({})
        return None if value is ignored or value.get("reply_post_id") == "skip" else value

    def epoch(value):
        assert type(value) is int
        calls.append(("epoch", value))
        return str(value) if value else None

    result = reply_text._index_reply_confirmations(
        events=events, structured_reply_confirmations=structured,
        confirmed_receipt_evidence=receipts, normalise_confirmation=normalise,
        epoch_to_london_text=epoch,
    )
    assert type(result) is dict and list(result) == ["20", "10", "30", "40"]
    assert len(result["20"]) == 3
    assert all(actual is expected for actual, expected in zip(result["20"], [first, second, first]))
    assert result["10"][0] is third
    assert result["30"] == [projected[2]] and result["30"][0] is projected[2]
    assert result["40"] == [projected[4]] and result["40"][0] is projected[4]
    expected_calls = [("normalise", item) for item in structured]
    for index, (item, converted) in enumerate(zip(projected, [11, 0, 12, 1, 0])):
        expected_calls += [("epoch", converted), ("len", index + 1),
                           ("len", index + 1), ("normalise", item)]
        assert item == {
            "time": str(converted) if converted else "",
            "lane": None, "target_id": None,
            "reply_post_id": receipts[index]["reply_post_id"], "original_post_id": None,
            "publication_authority": "confirmed_reply_receipt.json",
            "current_snapshot_authority": True,
            "_event_insertion_index": index + 1, "_source_sequence": 2 * index + 1,
        }
        assert item["current_snapshot_authority"] is True
    assert calls == expected_calls
    assert all(calls[index][1] is item for index, item in enumerate(structured))
    assert (structured, receipts) == before
    shared.append({"record_number": 7})
    assert result["20"][0]["source_refs"] is result["20"][2]["source_refs"] is shared
    projected[2]["late"] = shared
    assert result["30"][0]["late"] is shared


@pytest.mark.parametrize("boundary", [
    "structured-key", "receipt-key", "integer-value", "integer-type", "epoch",
])
def test_confirmation_index_preserves_failure_prefix_and_native_errors(boundary):
    calls = []
    failure = RuntimeError("epoch callback failed")
    structured = [{}] if boundary == "structured-key" else [{"reply_post_id": "20"}]
    receipt = {"reply_post_id": "20", "reply_epoch": "bad" if boundary == "integer-value"
               else [1] if boundary == "integer-type" else 1}

    def normalise(value):
        calls.append("normalise")
        return {} if boundary == "receipt-key" and "time" in value else value

    def epoch(value):
        calls.append("epoch")
        if boundary == "epoch":
            raise failure
        return "converted"

    error = {"structured-key": KeyError, "receipt-key": KeyError,
             "integer-value": ValueError, "integer-type": TypeError, "epoch": RuntimeError}[boundary]
    with pytest.raises(error) as caught:
        reply_text._index_reply_confirmations(
            events=[], structured_reply_confirmations=structured,
            confirmed_receipt_evidence=[receipt, {}], normalise_confirmation=normalise,
            epoch_to_london_text=epoch,
        )
    assert calls == (["normalise", "epoch", "normalise"] if boundary == "receipt-key"
                     else ["normalise", "epoch"] if boundary == "epoch" else ["normalise"])
    if error is KeyError:
        assert caught.value.args == ("reply_post_id",)
    if boundary == "epoch":
        assert caught.value is failure


def test_enrichment_uses_current_index_callbacks_inputs_and_returned_dictionary(monkeypatch):
    original_index = reply_text._index_reply_confirmations
    original_normalise = digest._normalised_structured_reply_confirmation
    visited, returned = [], []

    class Index(dict):
        def items(self):
            visited.append(self)
            return super().items()

    def index(**inputs):
        assert inputs["events"] is events
        assert inputs["structured_reply_confirmations"] is structured
        assert inputs["confirmed_receipt_evidence"] is receipts
        assert inputs["normalise_confirmation"] is normalise
        assert inputs["epoch_to_london_text"] is epoch
        result = Index(original_index(**inputs))
        returned.append(result)
        return result

    monkeypatch.setattr(reply_text, "_index_reply_confirmations", index)
    for time_text in ("first callback", "replacement callback"):
        events, structured, calls = [], [], []
        receipts = [{"reply_epoch": 1, "lane": "mention", "target_id": "123",
                     "reply_post_id": "456", "reply_text": "Exact receipt text"}]

        def normalise(value):
            calls.append("normalise")
            return original_normalise(value)

        def epoch(value):
            calls.append("epoch")
            assert value == 1 and type(value) is int
            return time_text

        monkeypatch.setattr(digest, "_normalised_structured_reply_confirmation", normalise)
        monkeypatch.setattr(digest, "epoch_to_london_text", epoch)
        report = {"events": events}
        assert digest.enrich_published_reply_text(
            report, runtime_state={}, structured_reply_confirmations=structured,
            historical_reply_text_evidence=[], confirmed_receipt_evidence=receipts,
        ) is None
        assert report["events"] is events and len(events) == 1
        assert events[0]["time"] == time_text
        assert events[0]["public_reply_text"] == "Exact receipt text"
        assert calls == ["epoch", "normalise"]
    assert len(visited) == len(returned) == 2
    assert all(actual is expected for actual, expected in zip(visited, returned))


def test_enrichment_passes_current_historical_inputs_after_legacy_and_shares_sets(monkeypatch):
    original_phase = reply_text._enrich_selected_historical_reply_text
    observed = []

    def phase(**inputs):
        assert inputs["historical_reply_text_evidence"] is history
        assert inputs["events"] is events
        assert inputs["production_ids"] is production_ids
        assert inputs["resolve_text"] is resolve
        assert inputs["bounded_source_refs"] is sources
        assert callable(inputs["warn"])
        assert inputs["consumed_historical_evidence"] == set()
        assert inputs["enriched_records"] == {id(legacy)}
        assert legacy["public_reply_text_status"] == "unavailable"
        observed.append(inputs)
        assert original_phase(**inputs) is None
        assert inputs["consumed_historical_evidence"] == {id(history[0])}
        assert inputs["enriched_records"] == {id(legacy), id(selected)}
        # The following synthesis pass must consume this same set.
        inputs["consumed_historical_evidence"].add(id(history[1]))

    monkeypatch.setattr(reply_text, "_enrich_selected_historical_reply_text", phase)
    for exact_text in ("Current evidence", "Replacement callback evidence"):
        legacy = {"kind": "mention_reply_posted", "mention_id": "123"}
        selected = {"kind": "historical_context_reply", "status": "completed",
                    "parent_post_id": "789", "quote_id": "a" * 64}
        excluded = dict(selected)
        events = [legacy, selected, excluded]
        production_ids = {id(legacy), id(selected)}
        refs, combined = [{"record_number": 1}], [{"record_number": 2}]
        history = [
            {"authoritative": True, "parent_post_id": "789", "quote_id": "a" * 64,
             "reply_post_id": "987", "reply_text": "Before legacy callback", "source_refs": refs},
            {"authoritative": True, "parent_post_id": "321", "quote_id": "b" * 64,
             "reply_post_id": "654", "reply_text": "Would otherwise be synthesized"},
        ]
        calls = []

        def resolve(candidates, *, unavailable_reason):
            calls.append("resolve")
            if not candidates:
                history[0]["reply_text"] = exact_text
                return {"public_reply_text_status": "unavailable"}
            assert observed[-1]["consumed_historical_evidence"] == {id(history[0])}
            assert candidates[0]["text"] is exact_text
            assert candidates[0]["source_refs"] is refs
            assert unavailable_reason == "no retained exact historical_context_reply_posted evidence"
            return {"public_reply_text_status": "confirmed", "public_reply_text": exact_text,
                    "public_reply_text_complete": True, "source_refs": refs}

        def sources(*collections):
            calls.append("sources")
            assert collections[0] is None and collections[1] is refs
            assert selected["reply_post_id"] == "987"
            return combined, 0

        monkeypatch.setattr(digest, "_public_reply_text_result", resolve)
        monkeypatch.setattr(digest, "bounded_source_refs", sources)
        section = {}
        report = {"events": events, "historical_context_replies": section}
        assert digest.enrich_published_reply_text(
            report, runtime_state={}, structured_reply_confirmations=[],
            historical_reply_text_evidence=history, production_event_object_ids=production_ids,
        ) is None
        assert report["events"] is events and len(events) == 3
        assert events[0] is legacy and events[1] is selected and events[2] is excluded
        assert "public_reply_text_status" not in excluded
        assert selected["public_reply_text"] is exact_text
        assert selected["source_refs"] is combined
        assert section["events"][0] is selected and section["events"][1] is excluded
        assert report["published_reply_text_health"]["confirmed_record_count"] == 2
        assert report["published_reply_text_health"]["complete_text_record_count"] == 1
        assert calls == ["resolve", "resolve", "sources"]
    assert len(observed) == 2
    assert observed[0]["consumed_historical_evidence"] is not observed[1]["consumed_historical_evidence"]
    assert observed[0]["enriched_records"] is not observed[1]["enriched_records"]


@pytest.mark.parametrize("conflict", ["parent", "reply"])
def test_selected_historical_conflicts_expand_shared_evidence_before_resolution(conflict):
    refs, merged, shared = [{"record_number": 1}], [{"record_number": 2}], []
    first = {"authoritative": True, "parent_post_id": "123", "quote_id": "a" * 64,
             "reply_post_id": "456", "reply_text": "First exact text", "source_refs": refs}
    other = dict(first, parent_post_id="789", reply_text="Cross-parent text", source=7)
    history = [first, other, first]
    if conflict == "reply":
        second = dict(first, reply_post_id="987", reply_text="Second reply text")
        history.append(second)
        expected = [first, other, first, second] if next(iter({"456", "987"})) == "456" else [second, first, other, first]
    else:
        expected = [first, other, first]
    selected = {"kind": "historical_context_reply", "status": "completed",
                "parent_post_id": "123", "quote_id": "a" * 64, "source_refs": refs}
    later = dict(selected, status="already_completed")
    skipped = dict(selected, status="dry_run")
    events = [selected, skipped, later]
    skipped_before = copy.deepcopy(skipped)
    production_ids = {id(selected), id(skipped)}
    consumed, enriched, calls, results = {17}, {19}, [], []
    reason = "structured historical-context evidence disagrees on reply or parent identity"

    def resolve(candidates, *, unavailable_reason):
        calls.append("resolve")
        assert consumed == {17, *(id(item) for item in history)}
        assert enriched == ({19} if len(results) == 0 else {19, id(selected)})
        assert unavailable_reason == "no retained exact historical_context_reply_posted evidence"
        assert len(candidates) == len(expected)
        for candidate, evidence in zip(candidates, expected):
            assert candidate is not evidence
            assert candidate["text"] is evidence["reply_text"]
            assert candidate["source_refs"] is refs
        assert [item["source"] for item in candidates] == [
            "7" if item is other else "structured historical_context_reply_posted" for item in expected
        ]
        result = {"public_reply_text": "Resolver choice", "public_reply_text_status": "confirmed",
                  "public_reply_text_source": "resolver source", "source_refs": refs,
                  "source_ref_omitted_count": "4", "shared": shared}
        results.append(result)
        return result

    def sources(*collections):
        calls.append("sources")
        assert all(value is refs for value in collections) and len(collections) == 2
        event = selected if len(results) == 1 else later
        assert event["reply_post_id"] == ("456" if conflict == "parent" else None)
        assert results[-1]["public_reply_text_status"] == "conflict"
        return merged, 3

    def warn(**warning):
        calls.append("warn")
        event = selected if len(results) == 1 else later
        assert id(event) not in enriched
        assert event["source_refs"] is merged and event["source_ref_omitted_count"] == 7
        assert warning == {"reply_post_id": "456" if conflict == "parent" else "",
                           "target_id": "123", "lane": "historical_context_reply", "reason": reason}
        # Later iterations must see current membership and the same indexed row.
        production_ids.add(id(later))
        first["reply_text"] = "Changed by the first warning callback"

    assert reply_text._enrich_selected_historical_reply_text(
        historical_reply_text_evidence=history, events=events, production_ids=production_ids,
        consumed_historical_evidence=consumed, enriched_records=enriched,
        resolve_text=resolve, bounded_source_refs=sources, warn=warn,
    ) is None
    assert calls == ["resolve", "sources", "warn"] * 2
    assert enriched == {19, id(selected), id(later)} and skipped == skipped_before
    assert events[0] is selected and events[1] is skipped and events[2] is later
    for event in (selected, later):
        assert event["public_reply_text"] is event["public_reply_text_sha256"] is None
        assert event["public_reply_text_character_count"] is None
        assert event["public_reply_text_complete"] is False
        assert event["public_reply_text_status"] == event["correlation_status"] == "conflict"
        assert event["public_reply_text_reason"] == reason
        assert event["public_reply_text_source"] == "resolver source"
        assert event["shared"] is shared and event["source_refs"] is merged


@pytest.mark.parametrize("boundary", ["resolve", "sources", "integer-value", "integer-type", "warn"])
def test_selected_historical_failure_preserves_consumption_and_partial_row(boundary):
    refs, merged, shared = [{"record_number": 1}], [{"record_number": 2}], []
    evidence = {"authoritative": True, "parent_post_id": "123", "quote_id": "a" * 64,
                "reply_post_id": "456", "reply_text": "Exact text", "source_refs": refs}
    event = {"kind": "historical_context_reply", "status": "completed",
             "parent_post_id": "123", "quote_id": "a" * 64, "reply_post_id": "old",
             "source_refs": refs, "source_ref_omitted_count": 11}
    later = dict(event)
    before = copy.deepcopy(event)
    consumed, enriched, calls = {17}, {19}, []
    failure = RuntimeError("callback failure")
    result = {"public_reply_text_status": "conflict", "public_reply_text_reason": "text conflict",
              "source_refs": refs, "shared": shared,
              "source_ref_omitted_count": "bad" if boundary == "integer-value"
              else [1] if boundary == "integer-type" else "4"}

    def resolve(candidates, *, unavailable_reason):
        calls.append("resolve")
        assert consumed == {17, id(evidence)} and enriched == {19}
        assert event == before
        if boundary == "resolve":
            raise failure
        return result

    def sources(*collections):
        calls.append("sources")
        assert event == dict(before, reply_post_id="456")
        assert collections[0] is refs and collections[1] is refs
        if boundary == "sources":
            raise failure
        return merged, 3

    def warn(**warning):
        calls.append("warn")
        assert enriched == {19} and event["source_ref_omitted_count"] == 7
        assert event["shared"] is shared and event["source_refs"] is merged
        raise failure

    error = ValueError if boundary == "integer-value" else TypeError if boundary == "integer-type" else RuntimeError
    with pytest.raises(error) as caught:
        reply_text._enrich_selected_historical_reply_text(
            historical_reply_text_evidence=[evidence], events=[event, later],
            production_ids={id(event), id(later)}, consumed_historical_evidence=consumed,
            enriched_records=enriched, resolve_text=resolve, bounded_source_refs=sources, warn=warn,
        )
    assert calls == (["resolve"] if boundary == "resolve" else ["resolve", "sources", "warn"]
                     if boundary == "warn" else ["resolve", "sources"])
    assert consumed == {17, id(evidence)} and enriched == {19}
    assert later == before
    if error is RuntimeError:
        assert caught.value is failure
    if boundary in {"resolve", "sources"}:
        assert event == (before if boundary == "resolve" else dict(before, reply_post_id="456"))
        assert event["source_refs"] is refs
    else:
        assert event["reply_post_id"] == "456" and event["shared"] is shared
        assert event["source_refs"] is merged and event["public_reply_text_status"] == "conflict"
        assert event["source_ref_omitted_count"] == (7 if boundary == "warn" else 11)


@pytest.mark.parametrize("candidates,status,source,reason", [
    ([], "unavailable", None, "missing evidence"),
    ([{"text": "First text", "source": "receipt"},
      {"text": "Other text", "source": "history"}],
     "conflict", "receipt + history", "authoritative text sources disagree"),
    ([{"text": None, "source": "receipt", "invalid_reason": "invalid identity"}],
     "conflict", "receipt", "invalid identity"),
])
def test_unavailable_and_conflicting_text_do_not_expose_exact_text_fields(
    candidates, status, source, reason,
):
    result = digest._public_reply_text_result(
        candidates, unavailable_reason="missing evidence",
    )
    assert result["public_reply_text"] is None
    assert result["public_reply_text_sha256"] is None
    assert result["public_reply_text_character_count"] is None
    assert result["public_reply_text_complete"] is False
    assert result["public_reply_text_status"] == result["correlation_status"] == status
    assert result["public_reply_text_source"] == source
    assert result["public_reply_text_reason"] == reason


@pytest.mark.parametrize("path", [
    "conversational", "selected_historical", "synthesized_historical",
])
def test_identity_conflict_clears_text_but_preserves_the_appropriate_provenance(path):
    stale_fields = {
        "public_reply_text": "Stale confirmed text",
        "public_reply_text_sha256": "f" * 64,
        "public_reply_text_character_count": 20,
        "public_reply_text_complete": True,
        "public_reply_text_status": "confirmed",
        "public_reply_text_source": "stale source",
        "correlation_status": "exact",
    }
    historical = [
        {"authoritative": True, "parent_post_id": parent, "quote_id": "a" * 64,
         "reply_post_id": "456", "reply_text": "The same valid text",
         "source": source, "source_refs": [{"record_number": index}]}
        for index, (parent, source) in enumerate([
            ("123", "first evidence"), ("789", "second evidence"),
        ], 1)
    ]
    confirmations = []
    if path == "conversational":
        event = dict(stale_fields, kind="mention_reply_posted", mention_id="789",
                     reply_post_id="456", source_refs=[{"record_number": 0}])
        confirmations = [{"lane": "mention", "target_id": "123", "reply_post_id": "456",
                          "source_refs": [{"record_number": 1}]}]
        historical = []
        expected_source = None
        expected_reason = "legacy posted record identity disagrees with structured confirmation"
        expected_refs = [0, 1]
    else:
        event = dict(stale_fields, kind="historical_context_reply", status="completed",
                     parent_post_id="123", quote_id="a" * 64,
                     source_refs=[{"record_number": 0}])
        expected_source = "first evidence + second evidence"
        expected_reason = (
            "structured historical-context evidence disagrees on reply or parent identity"
            if path == "selected_historical"
            else "structured historical-context confirmations disagree on immutable identity"
        )
        expected_refs = [0, 1, 2] if path == "selected_historical" else [1, 2]
    report = {"events": [] if path == "synthesized_historical" else [event]}
    digest.enrich_published_reply_text(
        report, runtime_state={}, structured_reply_confirmations=confirmations,
        historical_reply_text_evidence=historical,
    )
    if path == "synthesized_historical":
        event, = report["events"]
        assert event["kind"] == "confirmed_public_reply"
        assert event["parent_post_id"] is event["quote_id"] is None
    assert event["public_reply_text"] is None
    assert event["public_reply_text_sha256"] is None
    assert event["public_reply_text_character_count"] is None
    assert event["public_reply_text_complete"] is False
    assert event["public_reply_text_status"] == event["correlation_status"] == "conflict"
    assert event["public_reply_text_source"] == expected_source
    assert event["public_reply_text_reason"] == expected_reason
    assert [ref["record_number"] for ref in event["source_refs"]] == expected_refs
    assert report["published_reply_text_health"]["conflict_count"] == 1


@pytest.mark.parametrize("historical", [False, True])
@pytest.mark.parametrize("bounded", [False, True])
def test_reply_enrichment_merges_provenance_and_keeps_omissions_across_bounds(
    historical, bounded,
):
    evidence_refs = [{"record_number": index} for index in range(10 if bounded else 2)]
    event_refs = [{"record_number": index} for index in ([8, 9, 99] if bounded else [0])]
    event = {
        "source_refs": event_refs,
        "source_ref_omitted_count": 17,
    }
    if historical:
        event.update(kind="historical_context_reply", status="completed",
                     parent_post_id="123", quote_id="a" * 64)
        confirmations = []
        history = [{"authoritative": True, "parent_post_id": "123", "quote_id": "a" * 64,
                    "reply_post_id": "456", "reply_text": "Exact confirmed text",
                    "source_refs": evidence_refs}]
    else:
        event.update(kind="mention_reply_posted", mention_id="123", reply_post_id="456")
        confirmations = [{"lane": "mention", "target_id": "123", "reply_post_id": "456",
                          "source_refs": evidence_refs}]
        history = []
    report = {"events": [event]}
    digest.enrich_published_reply_text(
        report,
        runtime_state={"ai_reply_history": [{
            "candidate_source": "mention", "target_id": "123", "reply_post_id": "456",
            "proposed_reply": "Exact confirmed text",
        }]},
        structured_reply_confirmations=confirmations,
        historical_reply_text_evidence=history,
    )
    assert report["events"][0] is event
    assert event["public_reply_text"] == "Exact confirmed text"
    assert event["public_reply_text_status"] == "confirmed"
    assert event["public_reply_text_complete"] is True
    assert [ref["record_number"] for ref in event["source_refs"]] == (
        [8, 9, 99, 0, 1, 2, 3, 4] if bounded else [0, 1]
    )
    # Newly omitted references include both bounding passes. If neither pass
    # omits anything, retain the row's existing omission marker.
    assert event["source_ref_omitted_count"] == (5 if bounded else 17)
