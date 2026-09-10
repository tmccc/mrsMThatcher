"""Check publication correlation ownership and shared evidence boundaries."""

from __future__ import annotations

from dataclasses import replace
import json
import re

import mrs_log_digest as digest
import mrs_log_digest_quote_publication as publication

from tests.helpers.digest_records import record


def correlation(**overrides):
    callbacks = {
        "source_is_selftest": lambda: False,
        "valid_string_public_post_id": digest.valid_string_public_post_id,
        "valid_bounded_utf8_text": digest.valid_bounded_utf8_text,
        "bounded_source_refs": digest.bounded_source_refs,
        "warning_limit": lambda: digest.ENGAGEMENT_CORRELATION_WARNING_LIMIT,
        "question_separator": lambda: digest.ENGAGEMENT_QUESTION_PUBLIC_TEXT_SEPARATOR,
    }
    return publication.QuotePublicationCorrelation(**{**callbacks, **overrides})


def test_publication_validators_keep_current_digest_helpers_and_vocabulary(monkeypatch):
    calls = []

    def public_id(value):
        calls.append(value)
        return value == "opaque"

    monkeypatch.setattr(digest, "valid_string_public_post_id", public_id)
    monkeypatch.setattr(digest, "SHA256_LOWER_RE", re.compile("hash\\Z"))
    monkeypatch.setattr(digest, "ENGAGEMENT_PAIR_ID_RE", re.compile("pair\\Z"))
    monkeypatch.setattr(digest, "ENGAGEMENT_ARMS", {"trial"})
    monkeypatch.setattr(digest, "ENGAGEMENT_PUBLICATION_ORDERS", {"order"})
    monkeypatch.setattr(digest, "ENGAGEMENT_QUESTION_EXPERIMENT_ID", "experiment")
    monkeypatch.setattr(digest, "ENGAGEMENT_MAX_CONFIRMED_PUBLICATIONS", 2)
    root = {
        "event": "account_root_posted", "event_version": 1, "lane": "quote_image",
        "post_id": "opaque", "root_post_id": "opaque", "conversation_id": "opaque",
        "publication_authority": "confirmed_transport",
    }
    confirmation = {
        "event": "engagement_question_experimental_member_confirmed",
        "post_id": "opaque", "plan_sha256": "hash", "pair_id": "pair",
        "member_position": 1, "arm": "trial", "publication_sequence": 2,
    }
    metadata = {
        "engagement_experiment_id": "experiment",
        "engagement_experiment_plan_sha256": "hash",
        "engagement_experiment_pair_id": "pair",
        "engagement_experiment_arm": "trial",
        "engagement_experiment_member_position": 1,
        "engagement_experiment_publication_order": "order",
        "engagement_experiment_sequence": 2,
        "engagement_question_present": False,
        "engagement_approved_question_sha256": "hash",
        "engagement_public_text_sha256": "hash",
    }
    assert digest.valid_account_root_publication_identity(root) is True
    assert digest.valid_engagement_confirmation_event(confirmation) is True
    assert digest.engagement_main_metadata_status(metadata) == "valid"
    monkeypatch.setattr(digest, "ENGAGEMENT_MAX_CONFIRMED_PUBLICATIONS", 1)
    assert digest.valid_engagement_confirmation_event(confirmation) is False
    assert digest.engagement_main_metadata_status(metadata) == "invalid"
    assert calls == ["opaque"] * 3


def test_analyse_handlers_keep_current_validators_and_lazy_source_references(monkeypatch):
    calls = []
    reference = {"fixture": "shared reference"}

    def source_ref(source, indexes):
        calls.append(("source", source.ordinal, indexes))
        return reference

    def validator(event):
        calls.append(("validator", event["event"]))
        return False

    def metadata(event):
        calls.append(("metadata", event["event"]))
        return "invalid"

    monkeypatch.setattr(digest, "record_source_ref", source_ref)
    monkeypatch.setattr(digest, "valid_account_root_publication_identity", validator)
    monkeypatch.setattr(digest, "valid_engagement_confirmation_event", validator)
    monkeypatch.setattr(digest, "engagement_main_metadata_status", metadata)
    names = [
        "main_post_posted", "account_root_posted",
        "engagement_question_experimental_member_confirmed",
        "engagement_question_experimental_member_deferred",
    ]
    records = [record(i, "INFO", "log_event", "EVENT " + json.dumps({
        "event": name, "post_id": "123", "lane": "quote_image",
    })) for i, name in enumerate(names)]
    indexes = {"fixture.log": 7}
    report = digest.analyse(records, input_file_indexes=indexes)
    assert calls == [
        ("source", 1, indexes), ("metadata", names[0]),
        ("validator", names[1]), ("validator", names[2]), ("source", 4, indexes),
    ]
    trial = report["engagement_question_trial"]
    assert trial["outcomes"][0]["source_refs"][0] is reference
    assert {warning["field"] for warning in trial["correlation_warnings"]} == {
        "producer_schema",
    }
    assert len(trial["correlation_warnings"]) == 3


def test_analyse_warning_omissions_keep_current_limit_and_duplicate_accounting(monkeypatch):
    monkeypatch.setattr(digest, "ENGAGEMENT_CORRELATION_WARNING_LIMIT", 1)
    records = [record(i, "INFO", "log_event", "EVENT " + json.dumps({
        "event": "main_post_posted", "post_id": "123", "lane": "quote_image",
        "line_no": i, "image_no": i,
    })) for i in range(3)]
    records.append(record(3, "INFO", "post_random_quote",
                          "Quote/image posted successfully. posted_id=123"))
    report = digest.analyse(records)
    trial = report["engagement_question_trial"]
    assert [warning["field"] for warning in trial["correlation_warnings"]] == ["line_no"]
    event = next(item for item in report["events"] if item["kind"] == "quote_image_posted")
    assert event["correlation_warning_count"] == 2
    assert trial["correlation_warning_omitted_count"] == 1


def test_retention_preserves_payload_conflict_and_source_reference_identity():
    calls = []
    first_refs, second_refs, merged_refs = [{"first": 1}], [{"second": 2}], [{"merged": 3}]
    payload = {"event": "main_post_posted", "time": "first", "line_no": 1,
               "source_refs": first_refs}
    duplicate = {"event": "main_post_posted", "time": "later", "line_no": 2,
                 "source_refs": second_refs, "missing": None}
    selftest = False

    def source_is_selftest():
        calls.append("source")
        return selftest

    def bounded_refs(left, right):
        assert left is first_refs and right is second_refs
        calls.append("references")
        return merged_refs, 4

    owner = correlation(source_is_selftest=source_is_selftest, bounded_source_refs=bounded_refs)

    def retain(value):
        owner.retain("123", "main_post_posted", value)

    retain(payload)
    conflicts = payload["_conflicted_fields"]
    retain(duplicate)
    assert owner.evidence["123"]["main_post_posted"] is payload
    assert payload["_conflicted_fields"] is conflicts
    assert conflicts == {"line_no"} and payload["line_no"] is None
    assert payload["source_refs"] is merged_refs
    assert payload["source_ref_omitted_count"] == 4
    assert "missing" not in payload and "_conflicted_fields" not in duplicate
    assert duplicate["line_no"] == 2 and duplicate["source_refs"] is second_refs
    assert [warning["field"] for warning in owner.warnings] == ["line_no"]
    selftest = True
    retain({"line_no": 3, "image_no": 9})
    assert "image_no" not in payload
    assert calls == ["source", "source", "references", "source"]


def test_enrichment_preserves_event_identity_projection_sharing_and_sort_mutation():
    public_text = "Exact quote\n\nQuestion — Multiline\nquestion?"
    refs, merged_refs = [{"correlated": 1}], [{"merged": 2}]
    correlated = {
        "public_text": public_text, "public_text_status": "confirmed",
        "source_refs": refs, "source_ref_omitted_count": 3,
    }
    event = {"kind": "quote_image_posted", "post_id": "123", "time": "first"}
    selftest = dict(event)
    events = [event, selftest]
    confirmation = {"time": "second"}
    correlations = {"123": {"engagement_question_experimental_member_confirmed": confirmation}}
    later, earlier = {"time": "z"}, {"time": "a"}
    outcomes, warnings, calls = [later, earlier], [later, earlier], []

    def correlate(post_id, **kwargs):
        assert post_id == "123"
        calls.append(kwargs)
        return correlated

    def bounded_refs(left, right):
        assert left is None and right is refs
        return merged_refs, 2

    owner = correlation(bounded_source_refs=bounded_refs)
    owner.evidence.update(correlations)
    owner.trial_outcomes.extend(outcomes)
    owner.warnings.extend(warnings)
    owner.correlated_fields = correlate
    result = owner.prepare_report(events, {id(event)})
    assert calls[0]["legacy"] is event
    assert calls[0]["warning_time"] == "first"
    assert calls[1] == {"warning_time": "second"}
    assert events[0] is event and events[1] is selftest
    assert event["text"] is public_text and event["source_refs"] is merged_refs
    assert event["source_ref_omitted_count"] == 5
    assert "public_text" not in selftest
    assert result[0]["source_refs"] is refs and result[0]["public_text"] is public_text
    assert result[0]["source_ref_omitted_count"] == 3
    assert correlations["123"]["engagement_question_experimental_member_confirmed"] is confirmation
    assert owner.trial_outcomes == owner.warnings == [earlier, later]
    assert owner.trial_outcomes[0] is owner.warnings[0] is earlier


def test_warning_owner_counts_unique_omissions_and_keeps_current_display_limit():
    limit = 1
    owner = correlation(warning_limit=lambda: limit)
    warning = dict(time_text="first", post_id="123", field="lane", left_event="b", right_event="a")
    owner.add_warning(**warning)
    owner.add_warning(**{**warning, "left_event": "a", "right_event": "b", "time_text": "duplicate"})
    owner.add_warning(**{**warning, "field": "quote_hash"})
    owner.add_warning(**{**warning, "field": "quote_hash", "time_text": "omitted duplicate"})
    limit = 2
    owner.add_warning(**{**warning, "status": "invalid"})
    assert owner.warning_counts == {"123": 3}
    assert owner.warning_omitted_count == 1
    assert [(row["field"], row["status"], row["event_types"], row["time"]) for row in owner.warnings] == [
        ("lane", "conflict", ["a", "b"], "first"),
        ("lane", "invalid", ["a", "b"], "first"),
    ]


def test_conflicted_fields_remain_poisoned_after_later_matching_evidence():
    owner = correlation()
    original = {"post_id": "123", "lane": "quote_image", "line_no": 1, "quote_hash": "a" * 64}
    owner.retain("123", "main_post_posted", original)
    owner.retain("123", "main_post_posted", {"line_no": 2, "quote_hash": "b" * 64})
    owner.retain("123", "main_post_posted", {"line_no": 1, "quote_hash": "a" * 64})
    owner.retain("123", "account_root_posted", {"root_post_id": "123", "quote_id": "a" * 64})
    fields = owner.correlated_fields("123", legacy={"line_no": 999, "quote_hash": "a" * 64})
    assert owner.evidence["123"]["main_post_posted"] is original
    assert original["_conflicted_fields"] == {"line_no", "quote_hash"}
    assert fields["line_no"] is fields["quote_hash"] is None
    assert fields["correlation_status"] == "inconsistent"
    assert fields["correlation_warning_count"] == 2
    assert owner.warning_omitted_count == 0


def test_each_owner_keeps_source_filtered_evidence_and_warning_state_independent():
    selftest = True
    owner = correlation(source_is_selftest=lambda: selftest)
    payload = {"post_id": "123", "line_no": 1}
    owner.retain("123", "main_post_posted", payload)
    owner.note_invalid("123", "main_post_posted", "selftest")
    assert payload == {"post_id": "123", "line_no": 1}
    assert not owner.evidence and not owner.invalid_evidence and not owner.warnings
    selftest = False
    owner.retain("123", "main_post_posted", payload)
    owner.note_invalid("123", "main_post_posted", "production")
    owner.note_invalid("123", "main_post_posted", "production duplicate")
    owner.note_invalid("invalid-id", "main_post_posted", "invalid public id")
    owner.trial_outcomes.append({"time": "production"})
    assert owner.evidence["123"]["main_post_posted"] is payload
    assert owner.invalid_evidence == {"123": {"main_post_posted"}}
    assert owner.warning_counts == {"123": 1}
    assert owner.warnings[0]["time"] == "production"
    other = correlation()
    assert not other.evidence and not other.invalid_evidence and not other.trial_outcomes
    assert not other.warnings and not other.warning_counts
    assert other.warning_omitted_count == 0
    other.note_invalid("123", "main_post_posted", "other analysis")
    assert other.warnings[0]["time"] == "other analysis"
    assert len(owner.warnings) == 1


def test_interleaved_selftest_publications_cannot_poison_production_correlation():
    main = {"event": "main_post_posted", "post_id": "123", "lane": "quote_image", "line_no": 1}
    root = {
        "event": "account_root_posted", "event_version": 1, "lane": "quote_image",
        "post_id": "123", "root_post_id": "123", "conversation_id": "123",
        "publication_authority": "confirmed_transport", "quote_text": "Production text",
        "public_text": "Production text",
    }
    observations = [
        (True, {**main, "line_no": 99}),
        (False, main),
        (True, {**root, "public_text": "Selftest text"}),
        (False, root),
        (True, {**main, "lane": "invalid"}),
    ]
    records = [
        replace(
            record(i, "INFO", "log_event", "EVENT " + json.dumps(payload)),
            path="fixture.selftest.log" if selftest else "fixture.log",
        )
        for i, (selftest, payload) in enumerate(observations)
    ]
    records.append(record(5, "INFO", "post_random_quote", "Quote/image posted successfully. posted_id=123"))
    report = digest.analyse(records)
    event, = [item for item in report["events"] if item["kind"] == "quote_image_posted"]
    assert event["text"] == "Production text"
    assert event["line_no"] == 1
    assert event["correlation_status"] == "consistent"
    assert event["correlation_warning_count"] == 0
    assert report["engagement_question_trial"]["correlation_warnings"] == []
