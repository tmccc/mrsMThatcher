"""Single-call reply telemetry projection, attempt accounting and reporting."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import copy
import os
import subprocess
import sys

import pytest

import mrs_log_digest as digest
from single_call_reply_validation import (
    MAX_REJECTED_REPLY_TEXT_CHARACTERS,
    MAX_VALIDATION_ERROR_CODES,
)

from tests.helpers.digest_records import structured_record


def _validation_failure_record(offset, **fields):
    return structured_record(offset, {
        "event": "single_call_reply_decision", "lane": "mention",
        "target_id": str(100 + offset), "model_call_count": 1,
        "provider_request_attempt_count": 1,
        "pipeline_status": "operational_failure", "outcome_type": "operational",
        "error_category": "local_validation", "local_validation_status": "failed",
        "failure_reason": "model_response_validation_failed", **fields,
    })


def test_validation_failure_summary_counts_distinct_rules_and_preserves_categories():
    report = digest.analyse([
        _validation_failure_record(0, lane="quote_tweet", validation_error_codes=[
            "reply_contains_mention", "reply_contains_hashtag", "reply_contains_mention",
        ]),
        _validation_failure_record(1, error_category="schema_validation", validation_error_codes=[
            "invalid_used_fact_ids", "direct_factual_missing_fact_id",
        ]),
        _validation_failure_record(2, error_category="provider_schema", validation_error_codes=[]),
        _validation_failure_record(
            3, pipeline_status="no_reply", outcome_type="editorial",
            error_category=None, local_validation_status="passed", validation_error_codes=[],
        ),
    ])
    summary = report["single_call_reply"]
    details = summary["validation_failure_details"]

    # Preserve the legacy count, which also includes a failed provider envelope.
    # Only schema/mechanical reply-rule failures contribute detailed candidates.
    assert summary["local_validation_failure_count"] == 2
    assert summary["schema_validation_failure_count"] == 1
    assert details["candidate_count"] == 2
    assert details["omitted_candidate_count"] == 0
    assert details["rule_counts"] == {
        "reply_contains_hashtag": 1, "reply_contains_mention": 1,
        "direct_factual_missing_fact_id": 1, "invalid_used_fact_ids": 1,
    }
    assert details["rule_counts_by_category"] == {
        "schema_validation": {"invalid_used_fact_ids": 1},
        "local_validation": {
            "reply_contains_hashtag": 1, "reply_contains_mention": 1,
            "direct_factual_missing_fact_id": 1,
        },
    }
    assert details["details_status_counts"] == {"available": 2}
    assert details["candidates"][0] == {
        "time": "2026-09-04 12:00:00", "lane": "quote-tweet", "target_id": "100",
        "error_category": "local_validation",
        "failure_reason": "model_response_validation_failed",
        "validation_error_codes": ["reply_contains_hashtag", "reply_contains_mention"],
        "validation_error_details_status": "available",
        "validation_error_codes_omitted_count": 0,
        "rejected_reply_text": None,
        "rejected_reply_text_status": "unavailable",
        "rejected_reply_text_character_count": None,
    }
    assert details["candidates"][1]["error_category"] == "schema_validation"


@pytest.mark.parametrize(
    ("fields", "codes", "status", "omitted"),
    [
        ({}, [], "missing", 0),
        ({"validation_error_codes": []}, [], "empty", 0),
        ({"validation_error_codes": None}, [], "malformed", 1),
        ({"validation_error_codes": True}, [], "malformed", 1),
        ({"validation_error_codes": "PRIVATE prose"}, [], "malformed", 1),
        ({"validation_error_codes": {"secret": "PRIVATE prose"}}, [], "malformed", 1),
        (
            {"validation_error_codes": [
                "reply_contains_mention", "PRIVATE prose", None, True,
                {"secret": "PRIVATE prose"}, "reply_contains_mention",
            ]},
            ["reply_contains_mention"], "partial", 4,
        ),
        (
            {"validation_error_codes": ["PRIVATE prose", {"secret": "PRIVATE prose"}]},
            [], "malformed", 2,
        ),
        (
            {"validation_error_codes": ["reply_contains_mention"] * MAX_VALIDATION_ERROR_CODES
             + ["reply_contains_hashtag", "PRIVATE prose"]},
            ["reply_contains_mention"], "partial", 2,
        ),
    ],
)
def test_validation_failure_projection_redacts_and_preserves_missing_or_omitted_details(
    fields, codes, status, omitted,
):
    report = digest.analyse([_validation_failure_record(
        0, validation_error_details_status="PRIVATE forged status",
        validation_error_codes_omitted_count=999, **fields,
    )])

    event = report["events"][0]
    details = report["single_call_reply"]["validation_failure_details"]
    for row in (event, details["candidates"][0]):
        assert row["validation_error_codes"] == codes
        assert row["validation_error_details_status"] == status
        assert row["validation_error_codes_omitted_count"] == omitted
    assert details["details_status_counts"] == {status: 1}
    assert details["rule_counts"] == {code: 1 for code in codes}
    assert "PRIVATE" not in repr(report)


def test_validation_failure_summary_bounds_rows_without_losing_counts_or_zero_call_drafts():
    records = [
        _validation_failure_record(index, validation_error_codes=["exact_duplicate_reply"])
        for index in range(41)
    ]
    records.append(_validation_failure_record(
        41, model_call_count=0, provider_request_attempt_count=0,
        failure_reason="persisted_draft_local_validation_failed",
        validation_error_codes=["exact_duplicate_reply"],
    ))

    summary = digest.analyse(records)["single_call_reply"]
    details = summary["validation_failure_details"]

    assert details["candidate_count"] == 42
    assert details["rule_counts"] == {"exact_duplicate_reply": 42}
    assert details["details_status_counts"] == {"available": 42}
    assert details["omitted_candidate_count"] == 2
    assert len(details["candidates"]) == 40
    assert [row["target_id"] for row in details["candidates"]] == [
        str(value) for value in range(102, 142)
    ]
    assert details["candidates"][-1]["failure_reason"] == "persisted_draft_local_validation_failed"
    assert summary["one_call_compliance"] == "passed"


def test_validation_failure_summary_sanitizes_raw_decisions_without_mutation():
    events = [{
        "kind": "single_call_reply_decision", "time": "2026-09-04 12:00:00",
        "target_id": "100", "lane": "mention", "error_category": "local_validation",
        "pipeline_status": "operational_failure", "local_validation_status": "failed",
        "validation_error_codes": ["reply_contains_mention", "PRIVATE prose", False],
    }]
    original = copy.deepcopy(events)

    details = digest.single_call_reply_summary(events)["validation_failure_details"]

    assert events == original
    assert details["rule_counts"] == {"reply_contains_mention": 1}
    assert details["details_status_counts"] == {"partial": 1}
    assert details["candidates"][0]["validation_error_codes_omitted_count"] == 2
    assert "PRIVATE" not in repr(details)


@pytest.mark.parametrize("category", ["local_validation", "schema_validation"])
def test_rejected_reply_text_preserves_unicode_and_newlines_without_publication(category):
    rejected = "  Ja, ik ben een bot.\n\n日本語 — naïef | tekst\n"
    report = digest.analyse([_validation_failure_record(
        0, error_category=category, rejected_reply_text=rejected,
        rejected_reply_text_status="forged status",
    )])
    candidate = report["single_call_reply"]["validation_failure_details"]["candidates"][0]

    for row in (report["events"][0], candidate):
        assert row["rejected_reply_text"] == rejected
        assert row["rejected_reply_text_status"] == "available"
        assert row["rejected_reply_text_character_count"] == len(rejected)
        assert "public_reply_text" not in row
    assert report["single_call_reply"]["replies_posted_count"] == 0
    assert report["published_reply_text_health"]["confirmed_record_count"] == 0


@pytest.mark.parametrize("producer_truncated", [False, True])
def test_rejected_reply_truncation_survives_event_to_summary(producer_truncated):
    rejected = "é" * (MAX_REJECTED_REPLY_TEXT_CHARACTERS + 37)
    retained = rejected[:MAX_REJECTED_REPLY_TEXT_CHARACTERS]
    report = digest.analyse([_validation_failure_record(
        0, rejected_reply_text=retained if producer_truncated else rejected,
        rejected_reply_text_character_count=len(rejected),
        rejected_reply_text_status="available",
    )])
    candidate = report["single_call_reply"]["validation_failure_details"]["candidates"][0]

    for row in (report["events"][0], candidate):
        assert row["rejected_reply_text"] == retained
        assert row["rejected_reply_text_status"] == "truncated"
        assert row["rejected_reply_text_character_count"] == len(rejected)


@pytest.mark.parametrize("text", [None, False, 23, ["not reply text"], {"reply": "text"}])
def test_malformed_rejected_reply_fields_are_explicitly_unavailable(text):
    report = digest.analyse([_validation_failure_record(
        0, rejected_reply_text=text, rejected_reply_text_status="available",
        rejected_reply_text_character_count={"invalid": 5000},
    )])
    candidate = report["single_call_reply"]["validation_failure_details"]["candidates"][0]

    for row in (report["events"][0], candidate):
        assert row["rejected_reply_text"] is None
        assert row["rejected_reply_text_status"] == "unavailable"
        assert row["rejected_reply_text_character_count"] is None


@pytest.mark.parametrize("character_count", [None, False, -1, "5000", {"invalid": 5000}])
def test_malformed_rejected_reply_count_is_recomputed_from_text(character_count):
    report = digest.analyse([_validation_failure_record(
        0, rejected_reply_text="Rejected draft.",
        rejected_reply_text_character_count=character_count,
    )])
    candidate = report["single_call_reply"]["validation_failure_details"]["candidates"][0]

    assert candidate["rejected_reply_text_character_count"] == len("Rejected draft.")
    assert candidate["rejected_reply_text_status"] == "available"


@pytest.mark.parametrize("fields", [
    {"pipeline_status": "reply"},
    {"pipeline_status": "no_reply"},
    {"local_validation_status": "passed"},
    {"error_category": "provider_http_429"},
    {"error_category": ["local_validation"]},
])
def test_rejected_reply_text_is_ignored_outside_actual_validation_failures(fields):
    report = digest.analyse([_validation_failure_record(
        0, rejected_reply_text="Unrelated diagnostic prose.",
        rejected_reply_text_status="available", **fields,
    )])

    assert report["events"][0]["rejected_reply_text"] is None
    assert report["events"][0]["rejected_reply_text_status"] == "unavailable"
    assert "Unrelated diagnostic prose." not in repr(report)


def test_single_call_digest_reports_version_three_architecture():
    common = {
        "strategy_version": "single-sol-reply-20260904",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
        "temperature": 1,
        "prompt_sha256": "7" * 64,
        "response_schema_sha256": "3" * 64,
        "payload_sha256": "a" * 64,
        "used_fact_count": 1,
        "visible_turn_count": 4,
        "visible_character_count": 640,
        "same_author_interaction_count": 2,
        "recent_conversational_reply_count": 6,
        "trusted_fact_count": 3,
        "supplied_image_count": 1,
        "model_call_count": 1,
        "provider_request_attempt_count": 1,
        "local_validation_status": "passed",
        "outcome_type": "editorial",
    }
    records = [
        structured_record(0, {
            "event": "single_call_reply_decision",
            "lane": "mention",
            "target_id": "101",
            "decision": "reply",
            "reply_kind": "principle",
            "reason_code": "useful_reply",
            "pipeline_status": "reply",
            **common,
        }),
        structured_record(1, {
            "event": "single_call_reply_provider_usage",
            "lane": "mention",
            "target_id": "101",
            "strategy_version": "single-sol-reply-20260904",
            "model": "gpt-5.6-sol",
            "provider_response_id": "resp_1",
            "provider_latency_ms": 1200,
            "request_attempt_count": 1,
            "input_tokens": 100,
            "cached_input_tokens": 64,
            "cache_write_input_tokens": 0,
            "output_tokens": 20,
            "reasoning_tokens": 8,
            "total_tokens": 120,
        }),
        structured_record(2, {
            "event": "single_call_reply_posting_outcome",
            "status": "confirmed",
            "lane": "mention",
            "target_id": "101",
            "reply_post_id": "901",
            "strategy_version": "single-sol-reply-20260904",
            "reply_kind": "principle",
            "reason_code": "useful_reply",
            "used_fact_count": 1,
            "supplied_image_count": 1,
        }),
        structured_record(3, {
            "event": "single_call_reply_decision",
            "lane": "quote-tweet",
            "target_id": "102",
            "decision": "no_reply",
            "reply_kind": "no_reply",
            "reason_code": "completed_exchange",
            "pipeline_status": "no_reply",
            **{**common, "used_fact_count": 0, "supplied_image_count": 0},
        }),
        structured_record(4, {
            "event": "single_call_reply_decision",
            "lane": "hot-post",
            "target_id": "103",
            **common,
            "decision": None,
            "reply_kind": None,
            "reason_code": None,
            "pipeline_status": "operational_failure",
            "outcome_type": "operational",
            "local_validation_status": "failed",
            "error_category": "schema_validation",
            "failure_reason": "invalid_model_response",
        }),
        structured_record(5, {
            "event": "single_call_reply_draft_recovered",
            "lane": "mention",
            "target_id": "104",
            "strategy_version": "single-sol-reply-20260904",
            "model": "gpt-5.6-sol",
            "validated_draft_hash": "b" * 64,
            "model_call_count": 0,
        }),
    ]

    report = digest.analyse(records)
    summary = report["single_call_reply"]
    assert digest.DIGEST_JSON_SCHEMA_VERSION == 3
    assert summary["candidate_evaluation_count"] == 3
    assert summary["reply_decision_count"] == 1
    assert summary["replies_posted_count"] == 1
    assert summary["editorial_no_reply_count"] == 1
    assert summary["operational_failure_count"] == 1
    assert summary["recovered_draft_count"] == 1
    assert summary["one_call_compliance"] == "passed"
    assert summary["one_call_compliant_count"] == 3
    assert summary["one_call_violation_count"] == 0
    assert summary["reply_kind_counts"] == {"principle": 1}
    assert summary["no_reply_reason_counts"] == {"completed_exchange": 1}
    assert summary["operational_failure_reason_counts"] == {
        "invalid_model_response": 1,
    }
    assert summary["schema_validation_failure_count"] == 1
    assert summary["average_visible_turn_count"] == 4
    assert summary["average_visible_character_count"] == 640
    assert summary["average_recent_conversational_reply_count"] == 6
    assert summary["average_supplied_image_count"] == pytest.approx(2 / 3)
    assert summary["token_totals"]["input_tokens"] == 100
    assert summary["token_totals"]["cached_input_tokens"] == 64
    assert summary["provider_latency_average_ms"] == 1200
    assert "reply_strategy" not in report
    assert "reply_pipeline_stages" not in report
    assert "reply_visual_context_summary" not in report

    rendered = digest.render_markdown(report)
    assert "## Single-call conversational replies" in rendered
    assert "3 candidates evaluated; 1 reply posted" in rendered
    assert "One-call compliance: **passed**" in rendered
    assert "recent conversational replies" in rendered
    assert "## Conversational reply strategy" not in rendered
    assert "## Tested reply-pipeline stages" not in rendered
    assert "## Reply image context" not in rendered


def test_single_call_digest_flags_duplicate_provider_usage_as_call_violation():
    decision = structured_record(0, {
        "event": "single_call_reply_decision",
        "lane": "mention",
        "target_id": "201",
        "strategy_version": "single-sol-reply-20260904",
        "model": "gpt-5.6-sol",
        "decision": "reply",
        "reply_kind": "social",
        "reason_code": "useful_reply",
        "used_fact_count": 0,
        "model_call_count": 1,
        "provider_request_attempt_count": 1,
        "local_validation_status": "passed",
        "outcome_type": "editorial",
        "pipeline_status": "reply",
    })
    usage = {
        "event": "single_call_reply_provider_usage",
        "lane": "mention",
        "target_id": "201",
        "strategy_version": "single-sol-reply-20260904",
        "model": "gpt-5.6-sol",
        "request_attempt_count": 1,
    }
    report = digest.analyse([
        decision,
        structured_record(1, usage),
        structured_record(2, usage),
    ])
    summary = report["single_call_reply"]
    assert summary["one_call_compliance"] == "failed"
    assert summary["one_call_violation_count"] == 1
    assert summary["one_call_compliant_count"] == 0


def test_single_call_digest_allows_authorised_pre_execution_retry():
    decision = structured_record(0, {
        "event": "single_call_reply_decision",
        "lane": "mention",
        "target_id": "202",
        "strategy_version": "single-sol-reply-20260904",
        "model": "gpt-5.6-sol",
        "decision": "reply",
        "reply_kind": "social",
        "reason_code": "useful_reply",
        "used_fact_count": 0,
        "model_call_count": 1,
        "provider_request_attempt_count": 2,
        "local_validation_status": "passed",
        "outcome_type": "editorial",
        "pipeline_status": "reply",
    })
    usage = structured_record(1, {
        "event": "single_call_reply_provider_usage",
        "lane": "mention",
        "target_id": "202",
        "strategy_version": "single-sol-reply-20260904",
        "model": "gpt-5.6-sol",
        "request_attempt_count": 2,
    })

    summary = digest.analyse([decision, usage])["single_call_reply"]

    assert summary["one_call_compliance"] == "passed"
    assert summary["one_call_compliant_count"] == 1
    assert summary["one_call_violation_count"] == 0
    assert summary["authorised_pre_execution_retry_count"] == 1


@pytest.mark.parametrize("projected", [False, True])
@pytest.mark.parametrize(
    ("decision_attempts", "usage_attempts", "expected_mismatches"),
    [
        ([1], [2], 1),
        ([2], [1], 1),
        ([1, 1], [2, 2], 0),
    ],
)
def test_single_call_attempt_mismatches_require_one_decision_and_usage(
    projected, decision_attempts, usage_attempts, expected_mismatches,
):
    payloads = [
        {
            "event": "single_call_reply_decision",
            "lane": "mention",
            "target_id": "209",
            "model_call_count": 1,
            "provider_request_attempt_count": attempt_count,
            "pipeline_status": "reply",
        }
        for attempt_count in decision_attempts
    ] + [
        {
            "event": "single_call_reply_provider_usage",
            "lane": "mention",
            "target_id": "209",
            "request_attempt_count": attempt_count,
        }
        for attempt_count in usage_attempts
    ]
    if projected:
        summary = digest.analyse([
            structured_record(index, payload)
            for index, payload in enumerate(payloads)
        ])["single_call_reply"]
    else:
        summary = digest.single_call_reply_summary([
            {**payload, "kind": payload["event"]} for payload in payloads
        ])

    assert summary["provider_request_attempt_mismatch_candidate_count"] == expected_mismatches
    assert summary["one_call_compliance"] == (
        "failed" if expected_mismatches else "passed"
    )
    assert summary["one_call_violation_count"] == expected_mismatches
    assert summary["one_call_incomplete_count"] == 0
    assert summary["one_call_compliant_count"] == (
        0 if expected_mismatches else len(decision_attempts)
    )


def test_single_call_digest_preserves_and_rejects_more_than_two_attempts():
    decision = structured_record(0, {
        "event": "single_call_reply_decision",
        "lane": "mention",
        "target_id": "203",
        "model_call_count": 1,
        "provider_request_attempt_count": 3,
        "provider_status_code": 429,
        "provider_reset_epoch": 1_788_534_120,
        "provider_retry_after_seconds": 120,
        "pipeline_status": "operational_failure",
        "outcome_type": "operational",
    })
    usage = structured_record(1, {
        "event": "single_call_reply_provider_usage",
        "lane": "mention",
        "target_id": "203",
        "request_attempt_count": 3,
    })

    report = digest.analyse([decision, usage])
    summary = report["single_call_reply"]
    parsed_decision = next(
        item for item in report["events"]
        if item["kind"] == "single_call_reply_decision"
    )

    assert parsed_decision["provider_request_attempt_count"] == 3
    assert parsed_decision["provider_request_attempt_count_status"] == "available"
    assert parsed_decision["provider_status_code"] == 429
    assert parsed_decision["provider_reset_epoch"] == 1_788_534_120
    assert parsed_decision["provider_retry_after_seconds"] == 120
    assert summary["provider_request_attempt_counts"] == {"3": 1}
    assert summary["one_call_compliance"] == "failed"
    assert summary["one_call_compliant_count"] == 0
    assert summary["one_call_violation_count"] == 1


@pytest.mark.parametrize(
    ("attempt_value", "expected_status"),
    [
        (None, "missing"),
        ("1", "malformed"),
    ],
)
def test_single_call_digest_does_not_pass_incomplete_attempt_telemetry(
    attempt_value,
    expected_status,
):
    payload = {
        "event": "single_call_reply_decision",
        "lane": "mention",
        "target_id": "204",
        "model_call_count": 1,
        "pipeline_status": "reply",
        "outcome_type": "editorial",
    }
    if attempt_value is not None:
        payload["provider_request_attempt_count"] = attempt_value

    report = digest.analyse([structured_record(0, payload)])
    summary = report["single_call_reply"]
    parsed = next(
        item for item in report["events"]
        if item["kind"] == "single_call_reply_decision"
    )

    assert parsed["provider_request_attempt_count"] is None
    assert parsed["provider_request_attempt_count_status"] == expected_status
    assert summary["one_call_compliance"] == "incomplete"
    assert summary["one_call_compliant_count"] == 0
    assert summary["one_call_violation_count"] == 0
    assert summary["one_call_incomplete_count"] == 1
    assert summary["provider_request_attempt_metadata_status_counts"] == {
        expected_status: 1,
    }


def test_single_call_digest_reports_retryable_later_attempts_without_failing():
    decisions = [
        structured_record(index, {
            "event": "single_call_reply_decision",
            "lane": "mention",
            "target_id": "205",
            "model_call_count": 1,
            "provider_request_attempt_count": 1,
            "pipeline_status": "operational_failure",
            "outcome_type": "operational",
        })
        for index in range(2)
    ]
    usage = [
        structured_record(index + 2, {
            "event": "single_call_reply_provider_usage",
            "lane": "mention",
            "target_id": "205",
            "request_attempt_count": 1,
        })
        for index in range(2)
    ]

    summary = digest.analyse([*decisions, *usage])["single_call_reply"]

    assert summary["one_call_compliance"] == "passed"
    assert summary["one_call_compliant_count"] == 2
    assert summary["one_call_violation_count"] == 0
    assert summary["repeated_model_attempt_candidate_count"] == 1
    assert summary["excess_provider_usage_candidate_count"] == 0


@pytest.mark.parametrize(
    ("value", "projected", "status"),
    [
        (None, None, "malformed"),
        (True, None, "malformed"),
        (-1, None, "malformed"),
        (1_000_000, 1_000_000, "available"),
        (1_000_001, None, "out_of_range"),
    ],
)
def test_single_call_attempt_projection_keeps_invalid_and_range_observations(
    value, projected, status,
):
    records = [
        structured_record(0, {
            "event": "single_call_reply_decision",
            "target_id": "206",
            "model_call_count": 1,
            "provider_request_attempt_count": value,
        }),
        structured_record(1, {
            "event": "single_call_reply_provider_usage",
            "target_id": "206",
            "request_attempt_count": value,
        }),
    ]

    report = digest.analyse(records)

    for item, field in zip(report["events"], (
        "provider_request_attempt_count", "request_attempt_count",
    )):
        assert item[field] == projected
        assert type(item[field]) is type(projected)
        assert item[f"{field}_status"] == status
    summary = report["single_call_reply"]
    assert summary["provider_request_attempt_metadata_status_counts"] == {status: 2}
    assert summary["one_call_compliance"] == (
        "incomplete" if status == "malformed" else "failed"
    )
    assert summary["one_call_compliant_count"] == 0


@pytest.mark.parametrize(
    ("fields", "temperature", "recent_count"),
    [
        ({"temperature": 1}, 1, 30),
        ({"temperature": 1.25, "recent_conversational_reply_count": None}, 1.25, None),
        ({"temperature": True, "recent_conversational_reply_count": False}, None, None),
    ],
)
def test_single_call_projection_keeps_numeric_types_and_explicit_alias_precedence(
    fields, temperature, recent_count,
):
    report = digest.analyse([structured_record(0, {
        "event": "single_call_reply_decision",
        "recent_reply_count": 30,
        "used_fact_count": True,
        "visible_turn_count": 13,
        "provider_status_code": True,
        "target_id": 207,
        "private_extra": "not a report field",
        **fields,
    })])

    item = report["events"][0]
    assert item["temperature"] == temperature
    assert type(item["temperature"]) is type(temperature)
    assert item["recent_conversational_reply_count"] == recent_count
    assert type(item["recent_conversational_reply_count"]) is type(recent_count)
    assert item["used_fact_count"] is None
    assert item["visible_turn_count"] is None
    assert item["provider_status_code"] is None
    assert item["target_id"] == ""
    assert "private_extra" not in item


def test_single_call_summary_consumes_original_emitted_and_truncated_events(monkeypatch):
    captured = []
    original_summary = digest.single_call_reply_summary

    def capture(events):
        captured.extend(events)
        before = copy.deepcopy(events)
        result = original_summary(events)
        assert events == before
        return result

    monkeypatch.setattr(digest, "single_call_reply_summary", capture)
    common = {"lane": " HOT_POST_REPLY ", "target_id": "208"}
    payloads = [
        {"event": "historical_context_runtime", "status": "disabled"},
        {
            "event": "single_call_reply_decision", **common,
            "strategy_version": "s" * 30, "pipeline_status": "reply",
            "model_call_count": 1, "provider_request_attempt_count": 1,
        },
        {
            "event": "single_call_reply_provider_usage", **common,
            "request_attempt_count": 1, "input_tokens": 100,
        },
        {
            "event": "single_call_reply_posting_outcome", **common,
            "status": "confirmed", "reply_post_id": "908",
        },
        {
            "event": "single_call_reply_posting_outcome", **common,
            "lane": "hot_post", "status": "confirmed", "reply_post_id": "909",
        },
        {"event": "single_call_reply_draft_recovered", **common, "model_call_count": 0},
        {"event": "ai_reply_pipeline_decision", "status": "no_reply"},
    ]
    records = [structured_record(index, payload) for index, payload in enumerate(payloads)]
    records.append(replace(
        structured_record(7, payloads[5]), path="mrsMThatcher.selftest.log",
    ))

    report = digest.analyse(records, max_text=9)

    assert len(captured) == len(report["events"]) == 8
    assert all(left is right for left, right in zip(captured, report["events"]))
    assert [item["kind"] for item in captured] == [
        "historical_context_runtime", "single_call_reply_decision",
        "single_call_reply_provider_usage", "single_call_reply_posting_outcome",
        "single_call_reply_posting_outcome", "single_call_reply_draft_recovered",
        "reply_strategy_decision", "single_call_reply_draft_recovered",
    ]
    assert captured[1]["strategy_version"] == "ssssssss…"
    assert captured[1]["lane"] == "hot-post"
    assert captured[1]["time"] == "2026-09-04 12:00:01"
    assert "source_refs" not in captured[1]
    summary = report["single_call_reply"]
    assert summary["strategy_version_counts"] == {"ssssssss…": 1}
    assert summary["replies_posted_count"] == 1
    assert summary["recovered_draft_count"] == 2
    assert summary["one_call_compliance"] == "passed"
    assert report["summary"]["stats"]["single_call_reply_posting_outcome"] == 2
    assert report["summary"]["stats"]["single_call_reply_draft_recovered"] == 2
    assert report["summary"]["stats"].get("hot_post_reply_posted", 0) == 0


def test_single_call_module_import_and_reporting_have_no_runtime_dependencies(tmp_path):
    import mrs_log_digest_single_call as single_call
    import mrs_log_digest_values as values

    assert digest.single_call_reply_summary is single_call.single_call_reply_summary
    assert digest.normalise_reply_lane is values.normalise_reply_lane
    assert (
        digest.bounded_event_nonnegative_integer_observation
        is values.bounded_event_nonnegative_integer_observation
    )
    script = """
import builtins
from datetime import datetime
import logging
import os
from pathlib import Path
import sys

handlers = list(logging.getLogger().handlers)
loggers = set(logging.Logger.manager.loggerDict)
original_import = builtins.__import__
forbidden = {"mrs_log_digest", "mrs_log_digest_markdown", "mrsMThatcher2",
             "mrs_log_digest_runtime", "mrs_log_digest_corpus",
             "mrs_log_digest_generated_pool", "single_call_reply"}
def reject(*args, **kwargs):
    raise AssertionError((args, kwargs))
def import_guard(name, *args, **kwargs):
    assert name not in forbidden, name
    return original_import(name, *args, **kwargs)
def audit(event, args):
    if event == "open":
        path, mode, flags = args
        assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC), args
        assert isinstance(path, str) and path.endswith((".py", ".pyc", ".so")), args
    assert not event.startswith(("socket.", "subprocess.")), event
    assert event not in {"os.mkdir", "os.remove", "os.rename", "os.system", "os.listdir", "os.scandir"}, event
Path.home = classmethod(reject)
logging.basicConfig = reject
builtins.__import__ = import_guard
sys.addaudithook(audit)
import mrs_log_digest_single_call as single_call

events = []
def add_event(kind, ts, **fields):
    item = {"kind": kind, **fields}
    events.append(item)
    return item
single_call.record_single_call_reply_draft_recovered(
    {"lane": "hot_post_reply", "target_id": "209", "model_call_count": 0},
    datetime(2026, 9, 4, 12), add_event=add_event,
)
assert single_call.single_call_reply_summary(events)["recovered_draft_count"] == 1
assert single_call.single_call_reply_summary([])["recovered_draft_count"] == 0
assert not forbidden & sys.modules.keys()
assert list(logging.getLogger().handlers) == handlers
assert set(logging.Logger.manager.loggerDict) == loggers
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script], cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(Path(digest.__file__).resolve().parent)),
        capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == b""
    assert list(tmp_path.iterdir()) == []
