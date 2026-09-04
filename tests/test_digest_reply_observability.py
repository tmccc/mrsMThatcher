import json
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest

import historical_context_formatter as formatter
import mrs_log_digest as digest


def event(kind, **values):
    return {"kind": kind, "time": "2026-07-15 12:00:00", **values}


def structured_record(offset, payload, *, level="INFO"):
    return digest.Record(
        datetime(2026, 9, 4, 12) + timedelta(seconds=offset),
        level,
        "log_event",
        offset + 1,
        "EVENT " + json.dumps(payload, sort_keys=True),
        "mrsMThatcher.log",
        offset + 1,
    )


def test_source_classification_uses_stable_authoritative_classes():
    assert formatter.classify_source({"title": "HC Deb", "url": "https://hansard.parliament.uk/x", "source_type": "transcript"}) == "Hansard"
    assert formatter.classify_source({"title": "Redirect", "url": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/x"}) == "no public URL"
    assert formatter.classify_source({"title": "Speech", "url": "https://www.gov.uk/government/speeches/x"}) == "original speech transcript"


def test_historical_context_quality_aggregates_lengths_labels_and_omissions():
    events = [
        event("historical_context_reply", status="completed", character_count=300, raw_character_count=350,
              verification_label="Exact wording", source_class="Hansard", historical_confidence="high",
              shortening_applied=True, meaning_omitted=False, source_omitted=False, verification_omitted=False),
        event("historical_context_reply", status="failed", character_count=200, raw_character_count=220,
              verification_label="Historically verified variant", source_class="Margaret Thatcher Foundation",
              historical_confidence="medium", shortening_applied=False, meaning_omitted=True,
              source_omitted=False, verification_omitted=False),
        event("historical_context_reply", status="already_completed", character_count=300),
    ]
    result = digest.historical_context_quality_summary(events)
    assert result["attempted_count"] == 2
    assert result["average_weighted_characters"] == 300
    assert result["minimum_weighted_characters"] == 300
    assert result["shortened_count"] == 1
    assert result["meaning_omitted_count"] == 0
    assert result["verification_counts"]["unavailable"] == 0


def test_historical_context_v5_metadata_is_retained_and_summarised():
    dimensions = {
        "attribution": "high",
        "wording": "medium",
        "source_event": "low",
        "date": "unknown",
        "historical_context": "high",
        "interpretation": "medium",
    }
    records = [
        digest.Record(
            ts=datetime(2026, 7, 21, 12),
            level="INFO",
            src="log_event",
            line=1,
            msg="EVENT " + json.dumps({
                "event": "historical_context_reply",
                "status": "completed",
                "quote_id": "a" * 64,
                "character_count": 300,
                "raw_character_count": 320,
                "verification_label": "Exact wording verified",
                "source_class": "Margaret Thatcher Foundation",
                "historical_confidence": "high",
                "formatter_version": "historical_context_reply_schema_v5",
                "rendering_mode": "public",
                "confidence_dimensions": dimensions,
                "source_role_audit_version": (
                    "historical-context-source-roles-v5-independent-review-and-exclusive-counts"
                ),
                "shortening_applied": False,
                "meaning_omitted": False,
                "source_omitted": False,
                "verification_omitted": False,
            }),
            path="mrsMThatcher.log",
            ordinal=1,
        ),
    ]

    report = digest.analyse(records)
    context_event = next(
        item for item in report["events"]
        if item["kind"] == "historical_context_reply"
    )
    quality = report["historical_context_quality"]

    assert context_event["formatter_version"] == "historical_context_reply_schema_v5"
    assert context_event["rendering_mode"] == "public"
    assert context_event["confidence_dimensions"] == dimensions
    assert context_event["source_role_audit_version"] == (
        "historical-context-source-roles-v5-independent-review-and-exclusive-counts"
    )
    assert quality["verification_counts"]["Exact wording verified"] == 1
    assert quality["verification_counts"]["unavailable"] == 0
    assert quality["formatter_version_counts"]["historical_context_reply_schema_v5"] == 1
    assert quality["rendering_mode_counts"]["public"] == 1
    assert quality["source_role_audit_version_counts"][
        "historical-context-source-roles-v5-independent-review-and-exclusive-counts"
    ] == 1
    for field, value in dimensions.items():
        assert quality["confidence_dimension_counts"][field][value] == 1
        assert quality["confidence_dimension_counts"][field]["unavailable"] == 0

    rendered = digest.render_markdown(report)
    assert "Exact wording verified=1" in rendered
    assert "historical_context_reply_schema_v5=1" in rendered
    assert "Rendering modes: public=1" in rendered
    assert (
        "Source-role audit versions: "
        "historical-context-source-roles-v5-independent-review-and-exclusive-counts=1"
    ) in rendered
    assert "Confidence attribution: high=1" in rendered


def test_historical_context_semantic_gate_metadata_is_retained_and_skip_is_summarised():
    quote_id = "b" * 64
    ledger_sha256 = "c" * 64
    projection_sha256 = "d" * 64
    records = [
        digest.Record(
            ts=datetime(2026, 7, 22, 12),
            level="INFO",
            src="log_event",
            line=1,
            msg="EVENT " + json.dumps({
                "event": "historical_context_semantic_gate",
                "status": "loaded",
                "policy_version": (
                    "historical-context-semantic-gate-v1-open-review-whole-reply"
                ),
                "ledger_sha256": ledger_sha256,
                "projection_sha256": projection_sha256,
                "blocked_quote_count": 23,
            }),
            path="mrsMThatcher.log",
            ordinal=1,
        ),
        digest.Record(
            ts=datetime(2026, 7, 22, 12, 1),
            level="INFO",
            src="log_event",
            line=2,
            msg="EVENT " + json.dumps({
                "event": "historical_context_reply",
                "status": "skipped_future_policy",
                "reason": "open_semantic_review",
                "quote_id": quote_id,
                "semantic_review_disposition": "future_correction_needed",
                "semantic_review_ledger_sha256": ledger_sha256,
                "semantic_review_projection_sha256": projection_sha256,
            }),
            path="mrsMThatcher.log",
            ordinal=2,
        ),
    ]

    report = digest.analyse(records)
    gate_event = next(
        item for item in report["events"]
        if item["kind"] == "historical_context_semantic_gate"
    )
    reply_event = next(
        item for item in report["events"]
        if item["kind"] == "historical_context_reply"
    )
    quality = report["historical_context_quality"]

    assert gate_event["status"] == "loaded"
    assert gate_event["policy_version"] == (
        "historical-context-semantic-gate-v1-open-review-whole-reply"
    )
    assert gate_event["ledger_sha256"] == ledger_sha256
    assert gate_event["projection_sha256"] == projection_sha256
    assert gate_event["blocked_quote_count"] == 23
    assert reply_event["semantic_review_disposition"] == "future_correction_needed"
    assert reply_event["semantic_review_ledger_sha256"] == ledger_sha256
    assert reply_event["semantic_review_projection_sha256"] == projection_sha256
    assert quality["status_counts"]["skipped"] == 1
    assert quality["skip_reason_counts"] == {"open_semantic_review": 1}
    assert quality["attempted_count"] == 0

    rendered = digest.render_markdown(report)
    assert "## Historical context semantic gate" in rendered
    assert "historical-context-semantic-gate-v1-open-review-whole-reply" in rendered
    assert ledger_sha256 in rendered
    assert projection_sha256 in rendered
    assert "future_correction_needed" in rendered


def test_completed_historical_context_semantic_metadata_is_rendered():
    ledger_sha256 = "c" * 64
    projection_sha256 = "d" * 64
    record = digest.Record(
        ts=datetime(2026, 7, 22, 12),
        level="INFO",
        src="log_event",
        line=1,
        msg="EVENT " + json.dumps({
            "event": "historical_context_reply",
            "status": "completed",
            "quote_id": "b" * 64,
            "semantic_review_disposition": "supported_as_published",
            "semantic_review_ledger_sha256": ledger_sha256,
            "semantic_review_projection_sha256": projection_sha256,
        }),
        path="mrsMThatcher.log",
        ordinal=1,
    )

    report = digest.analyse([record])
    rendered = digest.render_markdown(report)

    assert "supported_as_published" in rendered
    assert ledger_sha256 in rendered
    assert projection_sha256 in rendered


def test_old_historical_context_event_uses_explicit_missing_semantic_metadata():
    record = digest.Record(
        ts=datetime(2026, 7, 20, 12),
        level="INFO",
        src="log_event",
        line=1,
        msg=(
            'EVENT {"event":"historical_context_reply","status":"completed",'
            '"quote_id":"' + "b" * 64 + '"}'
        ),
        path="old.log",
        ordinal=1,
    )

    report = digest.analyse([record])
    event = next(
        item for item in report["events"]
        if item["kind"] == "historical_context_reply"
    )

    assert event["semantic_review_disposition"] == "unavailable"
    assert event["semantic_review_ledger_sha256"] == "unavailable"
    assert event["semantic_review_projection_sha256"] == "unavailable"


def test_historical_context_v5_public_labels_are_not_downgraded_to_unavailable():
    labels = (
        "Exact wording verified",
        "Historically verified variant",
        "Verified excerpt",
        "Attributed, but exact wording not independently verified",
        "Exact wording not independently verified",
        "Research incomplete",
    )
    result = digest.historical_context_quality_summary([
        event(
            "historical_context_reply",
            status="completed",
            character_count=100,
            verification_label=label,
            formatter_version="historical_context_reply_schema_v5",
        )
        for label in labels
    ])

    for label in labels:
        assert result["verification_counts"][label] == 1
    assert result["verification_counts"]["unavailable"] == 0
    assert result["formatter_version_counts"]["historical_context_reply_schema_v5"] == len(labels)


def test_controlled_source_role_versions_include_deployed_v8_and_current_v9():
    versions = (
        "historical-context-source-roles-v7-curated-source-adjudications",
        "historical-context-source-roles-v8-claim-specific-public-context",
        "historical-context-source-roles-v9-archive-provenance",
    )
    result = digest.historical_context_quality_summary([
        event(
            "historical_context_reply",
            status="completed",
            character_count=100,
            verification_label="Attributed, but exact wording not independently verified",
            source_role_audit_version=version,
        )
        for version in versions
    ])

    assert all(
        result["source_role_audit_version_counts"][version] == 1
        for version in versions
    )
    assert result["source_role_audit_version_counts"]["unavailable"] == 0


def test_legacy_v2_v3_and_v4_historical_context_metadata_remains_compatible():
    result = digest.historical_context_quality_summary(
        [
            event(
                "historical_context_reply",
                status="completed",
                character_count=100,
                verification_label="Exact wording",
                formatter_version="historical_context_reply_schema_v2",
            ),
            event(
                "historical_context_reply",
                status="completed",
                character_count=100,
                verification_label=(
                    "Exact wording not independently verified by the retained evidence"
                ),
                formatter_version="historical_context_reply_schema_v3",
            ),
            event(
                "historical_context_reply",
                status="completed",
                character_count=100,
                verification_label="Exact wording verified",
                formatter_version="historical_context_reply_schema_v4",
            ),
            event(
                "historical_context_reply",
                status="completed",
                character_count=100,
                verification_label=(
                    "Reported in Jim Prior, 'A Balance of Power' (1986), p. 106; "
                    "no primary Thatcher transcript located"
                ),
                formatter_version="historical_context_reply_schema_v3",
            ),
        ]
    )

    assert result["verification_counts"]["Exact wording"] == 1
    assert result["verification_counts"][
        "Exact wording not independently verified by the retained evidence"
    ] == 1
    assert result["verification_counts"][
        "Secondary recollection; no primary Thatcher transcript located"
    ] == 1
    assert result["formatter_version_counts"]["historical_context_reply_schema_v2"] == 1
    assert result["formatter_version_counts"]["historical_context_reply_schema_v3"] == 2
    assert result["formatter_version_counts"]["historical_context_reply_schema_v4"] == 1


def test_malformed_confidence_dimensions_are_reported_as_unavailable():
    result = digest.historical_context_quality_summary([
        event(
            "historical_context_reply",
            status="completed",
            character_count=100,
            confidence_dimensions={"attribution": "certain"},
        ),
    ])

    for counts in result["confidence_dimension_counts"].values():
        assert counts["unavailable"] == 1


def test_missing_context_boolean_metadata_is_not_reported_as_zero_quality():
    result = digest.historical_context_quality_summary([
        event("historical_context_reply", status="completed", character_count=300),
    ])
    assert result["shortening_metadata_unavailable_count"] == 1
    assert result["omission_metadata_unavailable_count"] == 1


def test_malformed_boolean_length_is_not_counted_as_one_character():
    result = digest.historical_context_quality_summary([
        event("historical_context_reply", status="completed", character_count=True, raw_character_count=False),
    ])
    assert result["average_weighted_characters"] is None
    assert result["average_raw_characters"] is None


def test_context_skip_status_is_aggregated_once_with_separate_reason():
    result = digest.historical_context_quality_summary([
        event("historical_context_reply", status="skipped_no_completed_packet", reason="no_completed_canonical_packet"),
    ])
    assert result["status_counts"]["skipped"] == 1
    assert "skipped_no_completed_packet" not in result["status_counts"]
    assert result["skip_reason_counts"] == {"no_completed_canonical_packet": 1}


def test_all_skip_statuses_are_excluded_from_attempted_count():
    result = digest.historical_context_quality_summary([
        event("historical_context_reply", status="skipped_future_policy", reason="future_policy"),
        event("historical_context_reply", status="completed", character_count=100),
        event("historical_context_reply", status="failed"),
        event("historical_context_reply", status="dry_run", character_count=100),
        event("historical_context_reply", status="unknown_future_status"),
    ])
    assert result["attempted_count"] == 3


def test_unknown_optional_enums_are_counted_as_unavailable():
    context = digest.historical_context_quality_summary([
        event("historical_context_reply", status="completed", character_count=100,
              verification_label={"malformed": True}, source_class="future-unknown",
              historical_confidence="certain"),
    ])
    assert context["verification_counts"]["unavailable"] == 1
    assert "{'malformed': True}" not in context["verification_counts"]
    assert context["source_class_counts"]["unavailable"] == 1
    assert context["confidence_counts"]["unavailable"] == 1


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


def test_old_multi_stage_logs_are_only_counted_as_legacy():
    report = digest.analyse([
        structured_record(0, {
            "event": "reply_strategy_decision",
            "lane": "mention",
            "target_id": "301",
            "mode": "social",
        }),
        structured_record(1, {
            "event": "ai_reply_pipeline_stage_summary",
            "lane": "mention",
            "target_id": "301",
            "status": "reply",
        }),
    ])
    assert report["legacy_multi_stage"] == {
        "decision_count": 1,
        "stage_summary_event_count": 1,
    }
    rendered = digest.render_markdown(report)
    assert "Legacy multi-stage events in this window" in rendered
    assert "## Conversational reply strategy" not in rendered
    assert "## Tested reply-pipeline stages" not in rendered



def test_cli_refuses_colliding_output_paths(tmp_path):
    log = tmp_path / "bot.log"
    log.write_text("2026-07-15 12:00:00 INFO main:1 - Bot started\n", encoding="utf-8")
    output = tmp_path / "same-output"
    result = subprocess.run(
        [sys.executable, "mrs_log_digest.py", str(log), "--no-state", "--json", "--output", str(output),
         "--markdown-output", str(output)],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode != 0
    assert "output paths must be distinct" in result.stderr


def test_secondary_output_is_locked_when_state_is_disabled(tmp_path, monkeypatch):
    log = tmp_path / "bot.log"
    log.write_text("2026-07-15 12:00:00 INFO main:1 - Bot started\n", encoding="utf-8")
    output = tmp_path / "digest.md"
    locks = []

    @contextmanager
    def fake_lock(path):
        locks.append(path)
        yield

    monkeypatch.setattr(digest, "digest_execution_lock", fake_lock)
    monkeypatch.setattr(digest, "run_digest", lambda *_args, **_kwargs: 0)
    assert digest.main([str(log), "--no-state", "--markdown-output", str(output)]) == 0
    assert locks == [output.with_suffix(".md.lock")]


def test_digest_distinguishes_confirmed_main_context_states_and_meme_stage():
    structured = [
        {
            "event": "posting_transaction_state",
            "parent_post_id": "900001",
            "main_post_state": "main_post_confirmed",
            "context_reply_state": "context_reply_pending",
        },
        {
            "event": "historical_context_obligation",
            "status": "failed",
            "parent_post_id": "900001",
            "context_reply_state": "context_reply_failed_retryable",
            "attempt_number": 1,
        },
        {
            "event": "historical_context_obligation",
            "status": "failed_terminal",
            "parent_post_id": "900002",
            "context_reply_state": "context_reply_failed_terminal",
            "attempt_number": 5,
        },
        {
            "event": "daily_meme_failure",
            "status": "failed",
            "stage": "media_upload",
            "error_type": "OSError",
            "reason": "fixture failure",
        },
    ]
    records = [
        digest.Record(
            ts=datetime(2026, 7, 24, 1, index),
            level="INFO",
            src="log_event",
            line=index,
            msg="EVENT " + json.dumps(payload),
            path="mrsMThatcher.log",
            ordinal=index,
        )
        for index, payload in enumerate(structured, start=1)
    ]

    report = digest.analyse(records)
    consistency = report["production_consistency"]
    assert consistency["context_transaction_state_counts"] == {
        "context_reply_pending": 1
    }
    assert consistency["context_obligation_state_counts"] == {
        "context_reply_failed_retryable": 1,
        "context_reply_failed_terminal": 1,
    }
    assert consistency["daily_meme_failure_stage_counts"] == {"media_upload": 1}
    rendered = digest.render_markdown(report)
    assert "Confirmed-main/context transaction states" in rendered
    assert "context_reply_pending" in rendered
    assert "context_reply_failed_retryable" in rendered
    assert "context_reply_failed_terminal" in rendered
    assert "Daily meme failures by stage" in rendered
    assert "media_upload" in rendered


def test_input_retention_coverage_warns_when_requested_start_predates_logs():
    result = digest.input_retention_coverage(
        [
            {
                "first_timestamp": "2026-07-21 00:51:23",
                "last_timestamp": "2026-07-25 01:00:00",
            }
        ],
        datetime(2026, 7, 18, 0, 0),
    )

    assert result["requested_start_covered"] is False
    assert result["retention_gap_seconds"] == 262283
    assert "coverage of the preceding interval cannot be verified" in result["warning"]
    rendered = digest.render_markdown(
        {
            **digest.analyse([]),
            "requested_since": "2026-07-18 00:00:00",
            "since_source": "manual --since",
            "since_exclusive": False,
            "input_retention_coverage": result,
            "input_warning": result["warning"],
        }
    )
    assert "Retained-log coverage of requested start: **no**" in rendered


def test_input_retention_coverage_accepts_a_covered_boundary():
    result = digest.input_retention_coverage(
        [{"first_timestamp": "2026-07-17 23:59:59"}],
        datetime(2026, 7, 18, 0, 0),
    )

    assert result["requested_start_covered"] is True
    assert result["warning"] == ""


def test_new_runtime_pause_evidence_and_repair_events_are_structured():
    payloads = [
        {
            "event": "historical_context_runtime",
            "status": "unavailable",
            "reason": "source-role audit policy is incompatible",
            "regular_post_eligibility_unchanged": True,
        },
        {
            "event": "reply_evidence_unavailable",
            "lane": "mention",
            "target_id": "101",
        },
        {
            "event": "runtime_control_pause",
            "key": "pause_all",
            "lanes": ["disable_quote_posts", "disable_meme_posts"],
            "until_epoch": 123456,
        },
        {
            "event": "clarification_reply_cap_override",
            "target_id": "102",
            "thread_id": "202",
            "author_id": "302",
            "bypassed_cap": "per_author_daily",
        },
        {
            "event": "clarification_reply_used",
            "target_id": "102",
            "thread_id": "202",
            "author_id": "302",
            "reply_post_id": "402",
            "trigger": "corrected_question",
        },
        {
            "event": "repair_reply_completed",
            "target_id": "102",
            "thread_id": "202",
            "author_id": "302",
            "reply_post_id": "402",
        },
    ]
    records = [
        digest.Record(
            ts=datetime(2026, 7, 25, 1, index),
            level="INFO",
            src="log_event",
            line=index,
            msg="EVENT " + json.dumps(payload),
            path="mrsMThatcher.log",
            ordinal=index,
        )
        for index, payload in enumerate(payloads, start=1)
    ]

    report = digest.analyse(records)
    kinds = {item["kind"] for item in report["events"]}
    assert {
        "historical_context_runtime",
        "reply_evidence_unavailable",
        "runtime_control_pause",
        "clarification_reply_cap_override",
        "clarification_reply_used",
        "repair_reply_completed",
    } <= kinds
    consistency = report["production_consistency"]
    assert consistency["historical_context_runtime_status_counts"] == {
        "unavailable": 1
    }
    assert consistency["reply_evidence_unavailable_lane_counts"] == {"mention": 1}
    rendered = digest.render_markdown(report)
    assert "Historical-context runtime availability" in rendered
    assert "Reply evidence unavailable" in rendered
    assert "Runtime control pauses" in rendered
    assert "Clarification reply cap overrides" in rendered
    assert "Repair replies completed" in rendered


def test_current_corpus_snapshot_reports_counts_policies_and_hashes(tmp_path):
    paths = {
        "packets": (
            tmp_path
            / "semantic_alignment_research"
            / "quote_research_full_001"
            / "research_packets.json"
        ),
        "unresolved": (
            tmp_path
            / "semantic_alignment_research"
            / "quote_research_full_001"
            / "final_unresolved"
            / "unresolved_cases.json"
        ),
        "eligible": (
            tmp_path
            / "semantic_alignment_research"
            / "quote_attribution_cleanup_001"
            / "deployment_candidate"
            / "runtime_eligible_quote_manifest.json"
        ),
        "roles": (
            tmp_path
            / "semantic_alignment_research"
            / "quote_research_full_001"
            / "historical_context_source_role_audit.json"
        ),
        "gate": tmp_path / "historical_context_reply_semantic_gate_audit.json",
        "ledger": tmp_path / "historical_context_published_reply_semantic_review.json",
    }
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    paths["packets"].write_text(json.dumps({"items": [{"id": 1}, {"id": 2}]}))
    paths["unresolved"].write_text(json.dumps({"case_count": 1, "cases": [{}]}))
    paths["eligible"].write_text(
        json.dumps({"runtime_eligible_quote_count": 2, "runtime_eligible_quote_ids": ["a", "b"]})
    )
    paths["roles"].write_text(
        json.dumps({"policy_version": "roles-v9", "attribution_eligible_quote_count": 2})
    )
    paths["gate"].write_text(
        json.dumps(
            {
                "policy_version": "gate-v1",
                "coverage": {
                    "attribution_eligible_count": 2,
                    "completed_attribution_ineligible_count": 0,
                },
                "decision_counts": {
                    "eligible_allow": 1,
                    "blocked_open_semantic_review": 1,
                },
                "gate": {
                    "blocked_quote_count": 1,
                    "semantic_review_ledger_sha256": "a" * 64,
                    "blocked_projection_sha256": "b" * 64,
                },
            }
        )
    )
    paths["ledger"].write_text(json.dumps({"records": []}))

    snapshot = digest.historical_context_corpus_snapshot(tmp_path)

    assert snapshot["available"] is True
    assert snapshot["completed_packet_count"] == 2
    assert snapshot["ordinary_post_cycle_count"] == 2
    assert snapshot["unresolved_quote_count"] == 1
    assert snapshot["historical_context_blocked_count"] == 1
    assert snapshot["historical_context_allowed_count"] == 1
    assert snapshot["source_role_policy_version"] == "roles-v9"
    assert set(snapshot["file_sha256"]) == {
        "research_packets",
        "unresolved_cases",
        "runtime_eligible_manifest",
        "source_role_audit",
        "semantic_gate_audit",
        "semantic_review_ledger",
    }
