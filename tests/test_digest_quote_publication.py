"""Check publication correlation ownership and shared evidence boundaries."""

from __future__ import annotations

from dataclasses import replace
import json

import pytest

import mrs_log_digest as digest
import mrs_log_digest_quote_publication as publication

from tests.helpers.digest_records import record


def correlation(**overrides):
    callbacks = {
        "source_is_selftest": lambda: False,
        "valid_string_public_post_id": digest.valid_string_public_post_id,
        "valid_bounded_utf8_text": digest.valid_bounded_utf8_text,
        "bounded_source_refs": digest.bounded_source_refs,
        "warning_limit": lambda: digest.QUOTE_PUBLICATION_CORRELATION_WARNING_LIMIT,
    }
    return publication.QuotePublicationCorrelation(**{**callbacks, **overrides})


def test_publication_validators_keep_current_digest_helpers_and_vocabulary(monkeypatch):
    calls = []

    def public_id(value):
        calls.append(value)
        return value == "opaque"

    monkeypatch.setattr(digest, "valid_string_public_post_id", public_id)
    root = {
        "event": "account_root_posted", "event_version": 1, "lane": "quote_image",
        "post_id": "opaque", "root_post_id": "opaque", "conversation_id": "opaque",
        "publication_authority": "confirmed_transport",
    }
    assert digest.valid_account_root_publication_identity(root) is True
    assert calls == ["opaque"]


def test_analyse_handlers_keep_current_validators_and_lazy_source_references(monkeypatch):
    calls = []
    reference = {"fixture": "shared reference"}

    def source_ref(source, indexes):
        calls.append(("source", source.ordinal, indexes))
        return reference

    def validator(event):
        calls.append(("validator", event["event"]))
        return False

    monkeypatch.setattr(digest, "record_source_ref", source_ref)
    monkeypatch.setattr(digest, "valid_account_root_publication_identity", validator)
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
    assert calls == [("source", 1, indexes), ("validator", names[1])]
    publication_report = report["quote_publication"]
    assert {warning["field"] for warning in publication_report["correlation_warnings"]} == {
        "producer_schema",
    }
    assert len(publication_report["correlation_warnings"]) == 1
    assert "engagement_question_trial" not in report


def test_analyse_warning_omissions_keep_current_limit_and_duplicate_accounting(monkeypatch):
    monkeypatch.setattr(digest, "QUOTE_PUBLICATION_CORRELATION_WARNING_LIMIT", 1)
    records = [record(i, "INFO", "log_event", "EVENT " + json.dumps({
        "event": "main_post_posted", "post_id": "123", "lane": "quote_image",
        "line_no": i, "image_no": i,
    })) for i in range(3)]
    records.append(record(3, "INFO", "post_random_quote",
                          "Quote/image posted successfully. posted_id=123"))
    report = digest.analyse(records)
    publication_report = report["quote_publication"]
    assert [warning["field"] for warning in publication_report["correlation_warnings"]] == ["line_no"]
    event = next(item for item in report["events"] if item["kind"] == "quote_image_posted")
    assert event["correlation_warning_count"] == 2
    assert publication_report["correlation_warning_omitted_count"] == 1
    markdown = digest.render_markdown(report)
    assert "## Quote publication correlation warnings" in markdown
    assert "1 additional correlation warning(s) omitted" in markdown
    assert "Engagement-question trial" not in markdown


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
    later, earlier = {"time": "z"}, {"time": "a"}
    warnings, calls = [later, earlier], []

    def correlate(post_id, **kwargs):
        assert post_id == "123"
        calls.append(kwargs)
        return correlated

    def bounded_refs(left, right):
        assert left is None and right is refs
        return merged_refs, 2

    owner = correlation(bounded_source_refs=bounded_refs)
    owner.warnings.extend(warnings)
    owner.correlated_fields = correlate
    result = owner.prepare_report(events, {id(event)})
    assert calls[0]["legacy"] is event
    assert calls[0]["warning_time"] == "first"
    assert len(calls) == 1
    assert events[0] is event and events[1] is selftest
    assert event["text"] is public_text and event["source_refs"] is merged_refs
    assert event["source_ref_omitted_count"] == 5
    assert "public_text" not in selftest
    assert result is None
    assert owner.warnings == [earlier, later]
    assert owner.warnings[0] is earlier


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
    assert owner.evidence["123"]["main_post_posted"] is payload
    assert owner.invalid_evidence == {"123": {"main_post_posted"}}
    assert owner.warning_counts == {"123": 1}
    assert owner.warnings[0]["time"] == "production"
    other = correlation()
    assert not other.evidence and not other.invalid_evidence
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
    assert report["quote_publication"]["correlation_warnings"] == []


@pytest.mark.parametrize("max_text", [0, 1, 7, 80, 280])
def test_retired_trial_logs_and_state_keep_only_generic_publication_evidence(max_text):
    quote = "The original quotation."
    public_text = quote + "\n\nQuestion — What do you think?"
    main = {
        "event": "main_post_posted", "post_id": "123", "lane": "quote_image",
        "engagement_experiment_id": "substantive-question-v1",
        "engagement_question_present": True,
    }
    root = {
        "event": "account_root_posted", "event_version": 1, "lane": "quote_image",
        "post_id": "123", "root_post_id": "123", "conversation_id": "123",
        "publication_authority": "confirmed_transport", "quote_text": quote,
        "public_text": public_text,
    }
    retired = {
        "event": "engagement_question_experimental_member_confirmed",
        "post_id": "123", "arm": "treatment",
    }
    records = [record(i, "INFO", "log_event", "EVENT " + json.dumps(payload))
               for i, payload in enumerate([main, retired, root])]
    records.append(record(3, "INFO", "post_random_quote",
                          "Quote/image posted successfully. posted_id=123"))
    report = digest.analyse(records, max_text=max_text)
    event, = [row for row in report["events"] if row["kind"] == "quote_image_posted"]
    assert event["text"] == event["public_text"] == public_text
    assert event["public_text_status"] == "confirmed"
    assert not any(key.startswith("engagement_") for key in event)
    assert "engagement_question_trial" not in report
    assert report["quote_publication"]["correlation_warnings"] == []
    report["latest_state"] = digest.summarize_latest_state({
        "engagement_question_experiment": {"schema_version": 1,
            "experiment_id": "substantive-question-v1", "status": "paused"},
    }, None)
    assert "engagement_question_experiment" not in report["latest_state"]
    markdown = digest.render_markdown(report)
    assert "Question — What do you think?" in markdown
    assert "Engagement-question trial" not in markdown
    assert "question_present" not in markdown
