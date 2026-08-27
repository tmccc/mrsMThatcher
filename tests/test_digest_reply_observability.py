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


def reply_visual_record(offset, payload=None, *, message=None, level="INFO"):
    if message is None:
        message = "EVENT " + json.dumps(payload, sort_keys=True)
    return digest.Record(
        datetime(2026, 8, 26, 12) + timedelta(seconds=offset),
        level,
        "log_event",
        offset + 1,
        message,
        "mrsMThatcher.log",
        offset + 1,
    )


def reply_visual_payload(
    target_id,
    *,
    lane="mention",
    status="analysed",
    supplied_image_count=2,
    description_sha256=None,
    visual_analysis_call_count=None,
    **extra,
):
    if description_sha256 is None:
        description_sha256 = "a" * 64 if status == "analysed" else ""
    if visual_analysis_call_count is None:
        visual_analysis_call_count = (
            1 if status in {"analysed", "provider_error", "invalid_response"} else 0
        )
    return {
        "event": "reply_visual_description",
        "lane": lane,
        "target_id": target_id,
        "supplied_image_count": supplied_image_count,
        "status": status,
        "analysis_schema_version": 1,
        "description_sha256": description_sha256,
        "visual_analysis_call_count": visual_analysis_call_count,
        **extra,
    }


MAJORITY_TELEMETRY_MISSING = object()


def majority_review_entry(family, calls=2, valid_votes=None):
    if calls == 2:
        return {
            "family": family,
            "reviewer_calls_attempted": 2,
            "valid_votes_obtained": 2 if valid_votes is None else valid_votes,
            "first_two_valid_votes_agreed": True,
            "reviewer_3_called": False,
            "reviewer_3_skipped_first_two_agreement": True,
        }
    return {
        "family": family,
        "reviewer_calls_attempted": 3,
        "valid_votes_obtained": 3 if valid_votes is None else valid_votes,
        "first_two_valid_votes_agreed": False,
        "reviewer_3_called": True,
        "reviewer_3_skipped_first_two_agreement": False,
    }


def analyse_majority_review_payloads(*payloads):
    base = datetime(2026, 8, 21, 12)
    records = []
    for index, majority_payload in enumerate(payloads, 1):
        payload = {
            "event": "ai_reply_pipeline_stage_summary",
            "lane": "mention",
            "target_id": str(700 + index),
            "strategy_version": "tested-reply-pipeline-20260817",
            "status": "no_reply",
            "terminal_reason": "reply_necessity_review",
        }
        if majority_payload is not MAJORITY_TELEMETRY_MISSING:
            payload["majority_review_summaries"] = majority_payload
        records.append(digest.Record(
            base + timedelta(seconds=index),
            "INFO",
            "log_event",
            index,
            "EVENT " + json.dumps(payload),
            "mrsMThatcher.log",
            index,
        ))
    return digest.analyse(records)


def analyse_provider_usage_records(*provider_usages):
    base = datetime(2026, 8, 21, 13)
    records = [
        digest.Record(
            base + timedelta(seconds=index),
            "INFO",
            "tested_pipeline_structured_call",
            index,
            f"Tested reply stage=coverage_{index} provider={provider} usage="
            + repr(usage),
            "mrsMThatcher.log",
            index,
        )
        for index, (provider, usage) in enumerate(provider_usages, 1)
    ]
    return digest.analyse(records)


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

    strategy = digest.reply_strategy_summary([
        event("reply_strategy_outcome", reply_post_id="1", mode=["bad"], humour_tone="sarcastic",
              evidence_confidence="certain", retrieved_count=None),
    ])
    assert strategy["mode_counts"]["strategy metadata unavailable"] == 1
    assert "['bad']" not in strategy["mode_counts"]
    assert strategy["humour_tone_counts"]["unavailable"] == 1
    assert strategy["confidence_counts"]["unavailable"] == 1


def test_reply_strategy_separates_editorial_rejections_and_routine_skips():
    events = [
        event("reply_strategy_decision", lane="mention", mode="historical_correction", humour_tone="dry",
              evidence_confidence="high", retrieved_count=2, factual_claim=True, grounded=True, no_reply_reason=""),
        event("reply_strategy_decision", lane="quote_tweet", mode="no_reply", humour_tone="none",
              evidence_confidence="low", retrieved_count=0, factual_claim=False, grounded=False,
              no_reply_reason="weak evidence"),
        event("reply_strategy_rejection", lane="mention", reason="insufficient_confidence"),
        event("candidate_skipped", lane="mention", reason="author_daily_cap"),
        event("mention_reply_posted"),
    ]
    result = digest.reply_strategy_summary(events)
    assert result["mode_counts"]["historical_correction"] == 1
    assert result["mode_counts_by_lane"]["quote-tweet"]["no_reply"] == 1
    assert result["grounded_count"] == 1
    assert result["factual_claim_count"] == 1
    assert result["rejection_reason_counts"] == {"weak evidence": 1, "insufficient_confidence": 1}
    assert result["routine_skip_reason_counts"] == {"author_daily_cap": 1}


def test_reply_strategy_reports_principle_mode_and_stable_no_reply_categories():
    result = digest.reply_strategy_summary([
        event("reply_strategy_decision", lane="mention", target_id="principle", mode="principle_reply",
              humour_tone="none", evidence_confidence="none", retrieved_count=0,
              factual_claim=False, grounded=False, no_reply_reason=""),
        event("reply_strategy_outcome", status="confirmed", lane="mention", target_id="principle",
              reply_post_id="99", mode="principle_reply", humour_tone="none",
              evidence_confidence="none", retrieved_count=0, factual_claim=False, grounded=False),
        event("reply_strategy_decision", lane="quote_tweet", target_id="unsupported", mode="no_reply",
              humour_tone="none", evidence_confidence="none", retrieved_count=0,
              factual_claim=False, grounded=False,
              no_reply_reason="no_reply_due_to_unverifiable_claim"),
        event("reply_strategy_decision", lane="mention", target_id="bait", mode="no_reply",
              humour_tone="none", evidence_confidence="none", retrieved_count=0,
              factual_claim=False, grounded=False,
              no_reply_reason="Abusive bait would prolong conflict."),
        event("reply_strategy_decision", lane="mention", target_id="gibberish", mode="no_reply",
              humour_tone="none", evidence_confidence="none", retrieved_count=0,
              factual_claim=False, grounded=False,
              no_reply_reason="The post is incoherent gibberish."),
    ])

    assert result["mode_counts"]["principle_reply"] == 1
    assert result["generated_mode_counts"]["principle_reply"] == 1
    assert result["no_reply_category_counts"] == {
        "no_reply_due_to_unverifiable_claim": 1,
        "no_reply_due_to_bait_or_abuse": 1,
        "no_reply_due_to_incoherent": 1,
    }


def test_strategy_metadata_is_correlated_by_target_not_just_lane_count():
    result = digest.reply_strategy_summary([
        event("reply_strategy_decision", lane="mention", target_id="draft-only", mode="wry_reply",
              humour_tone="wry", evidence_confidence="none", retrieved_count=0,
              factual_claim=False, grounded=False, no_reply_reason=""),
        event("mention_reply_posted", mention_id="different-post"),
    ])
    assert result["mode_counts"]["strategy metadata unavailable"] == 1
    assert result["mode_counts_by_lane"]["mention"]["strategy metadata unavailable"] == 1
    assert result["grounding_metadata_unavailable_count"] == 1
    assert result["factual_claim_metadata_unavailable_count"] == 1
    assert result["retrieved_packet_metadata_unavailable_count"] == 1


def test_model_no_reply_is_not_double_counted_as_generic_no_usable_skip():
    result = digest.reply_strategy_summary([
        event("reply_strategy_decision", lane="mention", target_id="123", mode="no_reply",
              humour_tone="none", evidence_confidence="low", retrieved_count=0,
              factual_claim=False, grounded=False, no_reply_reason="weak evidence"),
        event("candidate_skipped", lane="mention", target_id="123", reason="no_usable_reply_generated"),
    ])
    assert result["rejection_reason_counts"] == {"weak evidence": 1}


def test_repetition_control_outcomes_have_stable_counts():
    result = digest.reply_strategy_summary([
        event("reply_strategy_rejection", reason="exact_duplicate_rejected"),
        event("reply_strategy_rejection", reason="highly_similar_reply_rejected"),
        event("reply_strategy_rejection", reason="canned_formulation_rejected"),
        event("candidate_skipped", lane="mention", target_id="1", reason="no_usable_reply_generated"),
    ])
    assert result["repetition_control_counts"] == {
        "exact_duplicate_rejected": 1,
        "highly_similar_reply_rejected": 1,
        "canned_formulation_rejected": 1,
        "regenerated_after_style_rejection": 0,
        "no_acceptable_reply": 1,
    }


def test_old_and_malformed_optional_metadata_remain_safe_and_unavailable():
    record = digest.Record(
        ts=datetime(2026, 7, 15, 12), level="INFO", src="log_event", line=1,
        msg='EVENT {"event":"reply_strategy_outcome","status":"confirmed","reply_post_id":"9","mode":"wry_reply","retrieved_quote_ids":"bad"}',
        path="old.log", ordinal=1,
    )
    report = digest.analyse([record])
    assert report["reply_strategy"]["confidence_counts"]["unavailable"] == 1
    assert report["reply_strategy"]["average_retrieved_packet_count"] is None
    assert report["historical_context_quality"]["average_weighted_characters"] is None
    json.dumps(report)


def test_ai_first_events_report_native_modes_tones_and_reviewer_separately():
    records = [
        digest.Record(
            ts=datetime(2026, 7, 20, 12), level="INFO", src="log_event", line=1,
            msg=(
                'EVENT {"event":"ai_reply_pipeline_decision","lane":"mention",'
                '"target_id":"100","status":"approved","strategy_version":"ai-first-reply-v2",'
                '"mode":"direct_factual_answer","tone":"firm","factual_claim_count":1,'
                '"evidence_ids":["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],'
                '"evidence_confidence":"high","retrieved_count":3,'
                '"evidence_reference_count":1,'
                '"reviewer_verdict":"approve","model_call_count":3,"revision_count":0}'
            ),
            path="mrsMThatcher.log", ordinal=1,
        ),
        digest.Record(
            ts=datetime(2026, 7, 20, 12, 0, 1), level="INFO", src="log_event", line=2,
            msg=(
                'EVENT {"event":"ai_reply_pipeline_outcome","lane":"mention",'
                '"target_id":"100","reply_post_id":"900","status":"confirmed",'
                '"strategy_version":"ai-first-reply-v2","mode":"direct_factual_answer",'
                '"tone":"firm","factual_claim_count":1,'
                '"evidence_ids":["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],'
                '"evidence_confidence":"high","retrieved_count":3,'
                '"evidence_reference_count":1,'
                '"reviewer_verdict":"approve","model_call_count":3,"revision_count":0}'
            ),
            path="mrsMThatcher.log", ordinal=2,
        ),
    ]

    report = digest.analyse(records)
    strategy = report["reply_strategy"]
    decisions = [event for event in report["events"] if event["kind"] == "reply_strategy_decision"]
    outcomes = [event for event in report["events"] if event["kind"] == "reply_strategy_outcome"]

    assert strategy["mode_counts"]["direct_factual_answer"] == 1
    assert strategy["generated_mode_counts"]["direct_factual_answer"] == 1
    assert strategy["humour_tone_counts"]["firm"] == 1
    assert strategy["confidence_counts"]["high"] == 1
    assert strategy["posted_grounded_count"] == 1
    assert strategy["generated_average_evidence_reference_count"] == 1
    assert strategy["average_evidence_reference_count"] == 1
    assert strategy["generated_average_retrieved_packet_count"] == 3
    assert strategy["average_retrieved_packet_count"] == 3
    assert decisions[0]["evidence_reference_count"] == 1
    assert outcomes[0]["evidence_reference_count"] == 1
    assert decisions[0]["evidence_confidence"] == "high"
    assert outcomes[0]["evidence_confidence"] == "high"
    assert decisions[0]["reviewer_verdict"] == "approve"
    assert outcomes[0]["reviewer_verdict"] == "approve"
    rendered = digest.render_markdown(report)
    assert "ai-first-reply-v2" in rendered
    assert "direct_factual_answer" in rendered
    assert "Facts actually referenced/used" in rendered
    assert "Generated tones:" in rendered
    assert "Published/terminal tones:" in rendered
    assert "humour tones" not in rendered


def test_old_ai_first_factual_event_keeps_missing_metrics_explicit():
    record = digest.Record(
        ts=datetime(2026, 7, 20, 12),
        level="INFO",
        src="log_event",
        line=1,
        msg=(
            'EVENT {"event":"ai_reply_pipeline_decision","lane":"mention",'
            '"target_id":"100","mode":"direct_factual_answer",'
            '"factual_claim_count":1,'
            '"evidence_ids":["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]}'
        ),
        path="old.log",
        ordinal=1,
    )

    report = digest.analyse([record])
    decision = next(
        item for item in report["events"]
        if item["kind"] == "reply_strategy_decision"
    )

    assert decision["evidence_confidence"] == "unavailable"
    assert decision["retrieved_count"] is None
    assert decision["evidence_reference_count"] == 1


def test_ai_first_usage_log_format_is_counted_with_pending_context():
    records = [
        digest.Record(
            ts=datetime(2026, 7, 20, 19, 30),
            level="INFO",
            src="maybe_reply_to_mentions",
            line=1,
            msg="Considering mention id=123 author_id=456 text='hello'",
            path="mrsMThatcher.log",
            ordinal=1,
        ),
        digest.Record(
            ts=datetime(2026, 7, 20, 19, 30, 1),
            level="INFO",
            src="xai_structured_reply_call",
            line=2,
            msg="Calling AI-first reply stage=proposer model=grok-4-1-fast-reasoning",
            path="mrsMThatcher.log",
            ordinal=2,
        ),
        digest.Record(
            ts=datetime(2026, 7, 20, 19, 30, 2),
            level="INFO",
            src="xai_structured_reply_call",
            line=3,
            msg=(
                "xAI reply stage=proposer usage={'prompt_tokens': 1779, "
                "'completion_tokens': 123, 'total_tokens': 2608, "
                "'prompt_tokens_details': {'cached_tokens': 192}, "
                "'completion_tokens_details': {'reasoning_tokens': 706}, "
                "'num_sources_used': 0, 'cost_in_usd_ticks': 40946500}"
            ),
            path="mrsMThatcher.log",
            ordinal=3,
        ),
    ]

    report = digest.analyse(records)

    assert report["summary"]["stats"]["xai_usage_successes"] == 1
    assert report["xai_usage"]["totals"] == {
        "successful_provider_calls": 1,
        "successful_xai_calls": 1,
        "successful_openai_calls": 0,
        "prompt_tokens": 1779,
        "cached_tokens": 192,
        "cache_read_input_tokens": 192,
        "cache_creation_input_tokens": None,
        "cache_write_input_tokens": None,
        "cache_metric_coverage": {
            "cache_creation_input_tokens": {
                "coverage_status": "unavailable",
                "successful_call_count": 1,
                "reporting_call_count": 0,
                "missing_call_count": 1,
                "reported_subtotal": None,
                "by_provider": {
                    "OpenAI": {
                        "coverage_status": "unavailable",
                        "successful_call_count": 0,
                        "reporting_call_count": 0,
                        "missing_call_count": 0,
                        "reported_subtotal": None,
                    },
                    "xAI": {
                        "coverage_status": "unavailable",
                        "successful_call_count": 1,
                        "reporting_call_count": 0,
                        "missing_call_count": 1,
                        "reported_subtotal": None,
                    },
                },
            },
            "cache_write_input_tokens": {
                "coverage_status": "unavailable",
                "successful_call_count": 1,
                "reporting_call_count": 0,
                "missing_call_count": 1,
                "reported_subtotal": None,
                "by_provider": {
                    "OpenAI": {
                        "coverage_status": "unavailable",
                        "successful_call_count": 0,
                        "reporting_call_count": 0,
                        "missing_call_count": 0,
                        "reported_subtotal": None,
                    },
                    "xAI": {
                        "coverage_status": "unavailable",
                        "successful_call_count": 1,
                        "reporting_call_count": 0,
                        "missing_call_count": 1,
                        "reported_subtotal": None,
                    },
                },
            },
        },
        "image_tokens": 0,
        "reasoning_tokens": 706,
        "completion_tokens": 123,
        "total_tokens": 2608,
        "sources_used": 0,
        "cost_in_usd_ticks": 40946500,
        "costed_call_count": 1,
        "uncosted_successful_call_count": 0,
    }
    assert report["xai_usage"]["events"][0]["lane"] == "mention"
    assert report["xai_usage"]["events"][0]["context_id"] == "123"
    assert report["xai_usage"]["events"][0]["stage"] == "proposer"
    assert report["xai_usage"]["events"][0]["model"] == "grok-4-1-fast-reasoning"


def test_provider_cache_tokens_are_cache_reads_and_writes_stay_nullable():
    usage = {
        "prompt_tokens": 100,
        "prompt_tokens_details": {"cached_tokens": 25},
        "completion_tokens": 20,
        "completion_tokens_details": {"reasoning_tokens": 7},
        "total_tokens": 120,
    }
    record = digest.Record(
        datetime(2026, 8, 21, 11),
        "INFO",
        "tested_pipeline_structured_call",
        1,
        "Tested reply stage=writer_v3_initial provider=OpenAI usage="
        + repr(usage),
        "mrsMThatcher.log",
        1,
    )
    item = digest.summarize_xai_usage_event(
        record,
        usage,
        {"lane": "mention", "context_id": "700"},
        provider="OpenAI",
    )
    totals = digest.xai_usage_totals([item])

    assert item["cached_tokens"] == 25
    assert item["cache_read_input_tokens"] == 25
    assert item["cache_creation_input_tokens"] is None
    assert item["cache_write_input_tokens"] is None
    assert totals["cached_tokens"] == 25
    assert totals["cache_read_input_tokens"] == 25
    assert totals["cache_creation_input_tokens"] is None
    assert totals["cache_write_input_tokens"] is None
    assert totals["prompt_tokens"] == 100
    assert totals["reasoning_tokens"] == 7
    assert totals["completion_tokens"] == 20
    assert totals["total_tokens"] == 120

    report = digest.analyse([record])
    rendered = digest.render_markdown(report)
    assert "cache-read input" in rendered
    assert "cache_read_input_tokens = 25" in rendered
    assert (
        "cached_tokens        = 25 (compatibility alias for cache-read input; "
        "not cache-write usage)"
    ) in rendered
    assert "cache_creation_input_tokens = unavailable" in rendered
    assert "cache_write_input_tokens = unavailable" in rendered


def test_distinct_cache_creation_and_write_metrics_remain_separate():
    usage = {
        "prompt_tokens": 80,
        "prompt_tokens_details": {
            "cached_tokens": 20,
            "cache_creation_tokens": 6,
            "cache_write_tokens": 4,
        },
        "completion_tokens": 10,
        "total_tokens": 90,
    }
    record = digest.Record(
        datetime(2026, 8, 21, 11),
        "INFO",
        "tested_pipeline_structured_call",
        1,
        "Tested reply stage=writer_v3_initial provider=OpenAI usage="
        + repr(usage),
        "mrsMThatcher.log",
        1,
    )
    item = digest.summarize_xai_usage_event(
        record,
        usage,
        {"lane": "mention", "context_id": "700"},
        provider="OpenAI",
    )
    totals = digest.xai_usage_totals([item])

    assert item["cache_read_input_tokens"] == 20
    assert item["cache_creation_input_tokens"] == 6
    assert item["cache_write_input_tokens"] == 4
    assert totals["cache_read_input_tokens"] == 20
    assert totals["cache_creation_input_tokens"] == 6
    assert totals["cache_write_input_tokens"] == 4
    assert totals["prompt_tokens"] == 80
    assert totals["completion_tokens"] == 10
    assert totals["total_tokens"] == 90


def test_cache_write_coverage_is_partial_for_mixed_provider_omissions():
    provider_usages = [
        (
            "xAI",
            {
                "prompt_tokens": 10,
                "cache_write_input_tokens": 0,
                "completion_tokens": 2,
                "total_tokens": 12,
            },
        )
        for _ in range(4)
    ] + [
        (
            "OpenAI",
            {
                "prompt_tokens": 20,
                "completion_tokens": 3,
                "total_tokens": 23,
            },
        )
        for _ in range(5)
    ]

    report = analyse_provider_usage_records(*provider_usages)
    totals = report["provider_usage"]["totals"]
    creation = totals["cache_metric_coverage"][
        "cache_creation_input_tokens"
    ]
    write = totals["cache_metric_coverage"]["cache_write_input_tokens"]

    assert creation["coverage_status"] == "unavailable"
    assert creation["successful_call_count"] == 9
    assert creation["reporting_call_count"] == 0
    assert creation["missing_call_count"] == 9
    assert creation["reported_subtotal"] is None
    assert write["coverage_status"] == "partial"
    assert write["successful_call_count"] == 9
    assert write["reporting_call_count"] == 4
    assert write["missing_call_count"] == 5
    assert write["reported_subtotal"] == 0
    assert totals["cache_write_input_tokens"] is None
    assert list(write["by_provider"]) == ["OpenAI", "xAI"]
    assert write["by_provider"]["OpenAI"] == {
        "coverage_status": "unavailable",
        "successful_call_count": 5,
        "reporting_call_count": 0,
        "missing_call_count": 5,
        "reported_subtotal": None,
    }
    assert write["by_provider"]["xAI"] == {
        "coverage_status": "complete",
        "successful_call_count": 4,
        "reporting_call_count": 4,
        "missing_call_count": 0,
        "reported_subtotal": 0,
    }

    rendered = digest.render_markdown(report)
    assert (
        "cache_creation_input_tokens = unavailable "
        "(0/9 successful calls reported the metric)"
    ) in rendered
    assert (
        "cache_write_input_tokens = partial; reported subtotal 0 across "
        "4/9 successful calls"
    ) in rendered
    assert "\ncache_write_input_tokens = 0" not in rendered
    assert (
        "OpenAI cache_write_input_tokens = unavailable "
        "(0/5 calls reported the metric)"
    ) in rendered
    assert (
        "xAI cache_write_input_tokens = 0 "
        "(complete coverage: 4/4 calls)"
    ) in rendered


def test_cache_write_complete_zero_coverage_keeps_numeric_compatibility():
    report = analyse_provider_usage_records(
        ("OpenAI", {"cache_write_input_tokens": 0}),
        ("OpenAI", {"cache_write_input_tokens": 0}),
    )
    totals = report["provider_usage"]["totals"]
    write = totals["cache_metric_coverage"]["cache_write_input_tokens"]

    assert write["coverage_status"] == "complete"
    assert write["successful_call_count"] == 2
    assert write["reporting_call_count"] == 2
    assert write["missing_call_count"] == 0
    assert write["reported_subtotal"] == 0
    assert write["by_provider"]["OpenAI"]["coverage_status"] == "complete"
    assert write["by_provider"]["OpenAI"]["reported_subtotal"] == 0
    assert totals["cache_write_input_tokens"] == 0

    rendered = digest.render_markdown(report)
    assert (
        "cache_write_input_tokens = 0 "
        "(complete coverage: 2/2 successful calls)"
    ) in rendered
    assert (
        "OpenAI cache_write_input_tokens = 0 "
        "(complete coverage: 2/2 calls)"
    ) in rendered


def test_cache_write_partial_nonzero_coverage_retains_reported_subtotal():
    report = analyse_provider_usage_records(
        ("OpenAI", {"cache_write_input_tokens": 3}),
        ("OpenAI", {"cache_write_input_tokens": 4}),
        ("OpenAI", {}),
    )
    totals = report["provider_usage"]["totals"]
    write = totals["cache_metric_coverage"]["cache_write_input_tokens"]

    assert write["coverage_status"] == "partial"
    assert write["successful_call_count"] == 3
    assert write["reporting_call_count"] == 2
    assert write["missing_call_count"] == 1
    assert write["reported_subtotal"] == 7
    assert totals["cache_write_input_tokens"] is None

    rendered = digest.render_markdown(report)
    assert (
        "cache_write_input_tokens = partial; reported subtotal 7 across "
        "2/3 successful calls"
    ) in rendered
    assert (
        "OpenAI cache_write_input_tokens = partial; reported subtotal 7 "
        "across 2/3 calls"
    ) in rendered


def test_cache_creation_and_write_are_unavailable_when_no_calls_report_them():
    report = analyse_provider_usage_records(
        ("OpenAI", {"prompt_tokens": 5, "total_tokens": 5}),
        ("xAI", {"prompt_tokens": 7, "total_tokens": 7}),
        ("xAI", {"prompt_tokens": 11, "total_tokens": 11}),
    )
    totals = report["provider_usage"]["totals"]

    for metric_name in (
        "cache_creation_input_tokens",
        "cache_write_input_tokens",
    ):
        coverage = totals["cache_metric_coverage"][metric_name]
        assert coverage["coverage_status"] == "unavailable"
        assert coverage["successful_call_count"] == 3
        assert coverage["reporting_call_count"] == 0
        assert coverage["missing_call_count"] == 3
        assert coverage["reported_subtotal"] is None
        assert totals[metric_name] is None

    rendered = digest.render_markdown(report)
    assert (
        "cache_creation_input_tokens = unavailable "
        "(0/3 successful calls reported the metric)"
    ) in rendered
    assert (
        "cache_write_input_tokens = unavailable "
        "(0/3 successful calls reported the metric)"
    ) in rendered
    assert "\ncache_creation_input_tokens = 0" not in rendered
    assert "\ncache_write_input_tokens = 0" not in rendered


def test_cache_creation_write_and_read_accounting_remain_independent():
    report = analyse_provider_usage_records(
        (
            "OpenAI",
            {
                "prompt_tokens": 10,
                "prompt_tokens_details": {
                    "cached_tokens": 4,
                    "cache_creation_tokens": 5,
                    "cache_write_tokens": 2,
                },
                "completion_tokens": 3,
                "completion_tokens_details": {"reasoning_tokens": 1},
                "total_tokens": 13,
            },
        ),
        (
            "OpenAI",
            {
                "prompt_tokens": 20,
                "prompt_tokens_details": {
                    "cached_tokens": 6,
                    "cache_creation_tokens": 7,
                },
                "completion_tokens": 4,
                "completion_tokens_details": {"reasoning_tokens": 2},
                "total_tokens": 24,
            },
        ),
    )
    totals = report["provider_usage"]["totals"]
    coverage = totals["cache_metric_coverage"]

    assert coverage["cache_creation_input_tokens"]["coverage_status"] == "complete"
    assert coverage["cache_creation_input_tokens"]["reported_subtotal"] == 12
    assert totals["cache_creation_input_tokens"] == 12
    assert coverage["cache_write_input_tokens"]["coverage_status"] == "partial"
    assert coverage["cache_write_input_tokens"]["reporting_call_count"] == 1
    assert coverage["cache_write_input_tokens"]["reported_subtotal"] == 2
    assert totals["cache_write_input_tokens"] is None
    assert totals["cached_tokens"] == 10
    assert totals["cache_read_input_tokens"] == 10
    assert totals["prompt_tokens"] == 30
    assert totals["completion_tokens"] == 7
    assert totals["reasoning_tokens"] == 3
    assert totals["total_tokens"] == 37
    json.dumps(report)


def test_invalid_cache_metric_values_do_not_count_as_reports():
    invalid_values = (True, False, -1, "3", {"tokens": 3})
    report = analyse_provider_usage_records(
        ("OpenAI", {"cache_write_input_tokens": 0}),
        *(
            ("OpenAI", {"cache_write_input_tokens": value})
            for value in invalid_values
        ),
    )
    events = report["provider_usage"]["events"]
    totals = report["provider_usage"]["totals"]
    write = totals["cache_metric_coverage"]["cache_write_input_tokens"]

    assert [item["cache_write_input_tokens"] for item in events] == [
        0,
        None,
        None,
        None,
        None,
        None,
    ]
    assert write["coverage_status"] == "partial"
    assert write["successful_call_count"] == 6
    assert write["reporting_call_count"] == 1
    assert write["missing_call_count"] == 5
    assert write["reported_subtotal"] == 0
    assert totals["cache_write_input_tokens"] is None


def test_tested_pipeline_provider_usage_and_stage_summary_are_complete():
    base = datetime(2026, 8, 16, 11)
    rows = [
        (0, "maybe_reply_to_mentions", "Considering mention id=700 author_id=800 text='fixture'"),
        (
            1,
            "tested_pipeline_structured_call",
            "Calling tested reply pipeline stage=candidate_backed_engagement "
            "provider=xAI model=grok-4.3 reasoning_effort=low",
        ),
        (
            2,
            "tested_pipeline_structured_call",
            "Tested reply stage=candidate_backed_engagement provider=xAI "
            "usage={'prompt_tokens': 100, 'completion_tokens': 10, "
            "'total_tokens': 110, 'cost_in_usd_ticks': 20000000}",
        ),
        (
            3,
            "tested_pipeline_structured_call",
            "Calling tested reply pipeline stage=writer_v3_initial "
            "provider=OpenAI model=gpt-5.6-sol reasoning_effort=medium",
        ),
        (
            4,
            "tested_pipeline_structured_call",
            "Tested reply stage=writer_v3_initial provider=OpenAI "
            "usage={'prompt_tokens': 200, 'completion_tokens': 20, "
            "'total_tokens': 220, 'completion_tokens_details': "
            "{'reasoning_tokens': 5}}",
        ),
        (
            5,
            "log_event",
            "EVENT " + json.dumps({
                "event": "ai_reply_pipeline_stage_summary",
                "lane": "mention",
                "target_id": "700",
                "strategy_version": "tested-reply-pipeline-20260817",
                "status": "approved",
                "terminal_reason": "pipeline_approved",
                "model_call_count": 2,
                "revision_count": 0,
                "provider_call_counts": {"xAI": 1, "OpenAI": 1},
                "schema_invalid_stages": [],
                "deterministic_suppressed": False,
                "deterministic_reason": None,
                "xai_gate_decision": "reply",
                "reply_necessity_outcome": "confirm_no_reply",
                "reply_necessity_majority_resolvable": False,
                "reply_necessity_invalid_calls": 0,
                "group_hostility_candidate": False,
                "group_hostility_outcome": None,
                "allegation_conspiracy_candidate": True,
                "allegation_conspiracy_categories": ["corruption_or_fraud"],
                "allegation_conspiracy_outcome": "confirm_no_reply_spam_or_abuse",
                "allegation_conspiracy_majority_resolvable": True,
                "allegation_conspiracy_invalid_calls": 0,
                "attribution_route": "none",
                "attribution_reply_requirement": None,
                "authentication_outcome": None,
                "claim_risk_categories": ["private_motive"],
                "claim_audit_outcomes": [
                    {"stage": "narrow_claim_audit_outcome", "outcome": "pass"}
                ],
                "claim_cleanup_called": False,
                "exact_duplicate_detected": False,
                "near_duplicate_count": 1,
                "duplicate_repair_called": False,
                "duplicate_repair_outcome": None,
                "final_validation": "passed",
            }, sort_keys=True),
        ),
        (
            6,
            "log_event",
            'EVENT {"event":"ai_reply_pipeline_decision","lane":"mention",'
            '"target_id":"700","status":"approved","mode":"opinion_or_principle",'
            '"model_call_count":2}',
        ),
        (
            7,
            "log_event",
            'EVENT {"event":"ai_reply_pipeline_outcome","lane":"mention",'
            '"target_id":"700","reply_post_id":"701","status":"confirmed",'
            '"mode":"opinion_or_principle","model_call_count":2}',
        ),
    ]
    records = [
        digest.Record(
            base + timedelta(seconds=offset),
            "INFO",
            source,
            index,
            message,
            "mrsMThatcher.log",
            index,
        )
        for index, (offset, source, message) in enumerate(rows, 1)
    ]

    report = digest.analyse(records)
    usage = report["provider_usage"]
    totals = usage["totals"]
    cost = usage["cost_summary"]
    stages = {(row["provider"], row["stage"]): row for row in cost["stages"]}

    assert usage == report["xai_usage"]
    assert totals["successful_provider_calls"] == 2
    assert totals["successful_xai_calls"] == 1
    assert totals["successful_openai_calls"] == 1
    assert totals["total_tokens"] == 330
    assert report["summary"]["stats"]["provider_usage_successes"] == 2
    assert report["summary"]["stats"]["xai_usage_successes"] == 1
    assert report["summary"]["stats"]["openai_usage_successes"] == 1
    assert cost["unattributed_successful_call_count"] == 0
    assert cost["unmatched_successful_call_count"] == 0
    assert cost["candidates"][0]["call_coverage"] == "complete"
    assert stages[("xAI", "candidate_backed_engagement")]["successful_usage_records"] == 1
    assert stages[("OpenAI", "writer_v3_initial")]["successful_usage_records"] == 1
    assert cost["coverage_complete"] is False
    assert cost["coverage_reasons"] == ["successful responses lack provider cost"]

    stage_summary = report["reply_pipeline_stages"]
    assert stage_summary["evaluation_count"] == 1
    assert stage_summary["provider_call_counts"] == {"OpenAI": 1, "xAI": 1}
    assert stage_summary["allegation_conspiracy_candidate_count"] == 1
    assert stage_summary["allegation_conspiracy_review_count"] == 1
    assert stage_summary["allegation_conspiracy_suppression_count"] == 1
    assert stage_summary["reply_necessity_majority_resolvable_counts"] == {
        "false": 1
    }
    assert stage_summary["allegation_conspiracy_majority_resolvable_counts"] == {
        "true": 1
    }
    assert stage_summary["claim_risk_category_counts"] == {"private_motive": 1}
    assert stage_summary["near_duplicate_candidate_count"] == 1
    assert stage_summary["final_validation_counts"] == {"passed": 1}

    rendered = digest.render_markdown(report)
    assert "## xAI usage, OpenAI usage, and conversational reply cost" in rendered
    assert "Successful calls by provider: OpenAI=1, xAI=1." in rendered
    assert "## Tested reply-pipeline stages" in rendered
    assert "Allegation/conspiracy candidates/reviews/suppressions" in rendered
    assert "reply-necessity outcomes: confirm_no_reply=1; majority resolvability: false=1" in rendered
    assert "outcomes: confirm_no_reply_spam_or_abuse=1; majority resolvability: true=1" in rendered


def test_majority_review_empty_and_legacy_coverage_are_distinct():
    report = analyse_majority_review_payloads(
        [],
        MAJORITY_TELEMETRY_MISSING,
    )
    utilisation = report["reply_pipeline_stages"][
        "majority_review_utilisation"
    ]
    coverage = utilisation["coverage"]

    assert coverage == {
        "stage_summary_events_examined": 2,
        "events_with_majority_review_summaries": 1,
        "events_without_majority_review_summaries": 1,
        "events_with_empty_majority_review_summaries": 1,
        "valid_majority_family_entries": 0,
        "malformed_entries": 0,
        "events_with_duplicate_family_entries": 0,
    }
    assert utilisation["overall"]["completed_family_resolutions"] == 0
    assert utilisation["overall"]["reviewer_call_reduction_percentage"] is None
    assert utilisation["overall"]["short_circuit_rate_percentage"] is None


def test_majority_review_valid_entries_cover_all_families_and_shared_event():
    report = analyse_majority_review_payloads(
        [
            majority_review_entry("reply_necessity"),
            majority_review_entry("allegation_review", calls=3),
        ],
        [majority_review_entry(
            "authentication_review", calls=3, valid_votes=2
        )],
    )
    utilisation = report["reply_pipeline_stages"][
        "majority_review_utilisation"
    ]
    overall = utilisation["overall"]

    assert utilisation["coverage"]["valid_majority_family_entries"] == 3
    assert list(utilisation["per_family"]) == [
        "reply_necessity",
        "allegation_review",
        "authentication_review",
    ]
    assert utilisation["per_family"]["reply_necessity"][
        "two_call_resolutions"
    ] == 1
    assert utilisation["per_family"]["allegation_review"][
        "three_call_resolutions_with_all_three_votes_valid"
    ] == 1
    assert utilisation["per_family"]["authentication_review"][
        "valid_votes_obtained"
    ] == 2
    assert overall["actual_reviewer_calls_attempted"] == 8
    assert overall["reviewer_calls_saved"] == 1
    assert overall["total_invalid_or_unusable_votes"] == 1
    assert overall[
        "three_call_resolutions_with_all_three_votes_valid"
    ] == 1
    assert overall[
        "three_call_resolutions_with_invalid_or_unusable_votes"
    ] == 1


def test_majority_review_aggregate_call_savings_arithmetic():
    report = analyse_majority_review_payloads(
        [majority_review_entry("reply_necessity")],
        [majority_review_entry("allegation_review")],
        [majority_review_entry("authentication_review", calls=3)],
        [majority_review_entry("reply_necessity", calls=3, valid_votes=2)],
    )
    overall = report["reply_pipeline_stages"][
        "majority_review_utilisation"
    ]["overall"]

    assert overall["completed_family_resolutions"] == 4
    assert overall["fixed_three_call_baseline"] == 12
    assert overall["actual_reviewer_calls_attempted"] == 10
    assert overall["reviewer_calls_saved"] == 2
    assert overall["reviewer_call_reduction_percentage"] == 2 / 12 * 100
    assert overall["two_call_resolutions"] == 2
    assert overall["three_call_resolutions"] == 2
    assert overall["first_two_agreement_resolutions"] == 2
    assert overall[
        "reviewer_3_skips_due_to_matching_first_two_votes"
    ] == 2
    assert overall["reviewer_3_calls"] == 2
    assert overall[
        "three_call_resolutions_with_all_three_votes_valid"
    ] == 1
    assert overall[
        "family_resolutions_with_invalid_or_unusable_votes"
    ] == 1
    assert overall["total_invalid_or_unusable_votes"] == 1


def test_majority_review_markdown_pluralises_resolutions_and_saved_calls():
    singular = digest.render_markdown(analyse_majority_review_payloads(
        [majority_review_entry("reply_necessity")],
    ))
    plural = digest.render_markdown(analyse_majority_review_payloads(
        [majority_review_entry("reply_necessity")],
        [majority_review_entry("allegation_review")],
    ))

    assert (
        "Overall: **1 resolution; 2 calls made versus 3 fixed-three baseline; "
        "1 call saved; 0 invalid/unusable votes**."
    ) in singular
    assert "1 resolutions" not in singular
    assert "1 calls saved" not in singular
    assert (
        "Overall: **2 resolutions; 4 calls made versus 6 fixed-three baseline; "
        "2 calls saved; 0 invalid/unusable votes**."
    ) in plural


def test_majority_review_malformed_payloads_are_excluded_strictly():
    valid = majority_review_entry("reply_necessity")
    malformed_payloads = [
        "not-a-list",
        ["not-a-dictionary"],
        [{**valid, "family": "unknown_family"}],
        [{**valid, "first_two_valid_votes_agreed": 1}],
        [{**valid, "reviewer_calls_attempted": 4}],
        [{**valid, "valid_votes_obtained": 3}],
        [{**valid, "reviewer_3_called": True}],
        [{**valid, "valid_votes_obtained": 1}],
        [{**valid, "private_extra_field": "PRIVATE-MAJORITY-MARKER"}],
    ]
    for malformed in malformed_payloads:
        normalised = digest.normalise_majority_review_telemetry({
            "majority_review_summaries": malformed,
        })
        assert normalised["valid_entries"] == []
        assert normalised["malformed_entry_count"] == 1

    duplicate = digest.normalise_majority_review_telemetry({
        "majority_review_summaries": [valid, dict(valid)],
    })
    assert duplicate["duplicate_family"] is True
    assert duplicate["valid_entries"] == []
    assert duplicate["malformed_entry_count"] == 2

    duplicate_report = analyse_majority_review_payloads([valid, dict(valid)])
    duplicate_coverage = duplicate_report["reply_pipeline_stages"][
        "majority_review_utilisation"
    ]["coverage"]
    assert duplicate_coverage["events_with_duplicate_family_entries"] == 1
    assert duplicate_coverage["malformed_entries"] == 2
    assert duplicate_coverage["valid_majority_family_entries"] == 0


def test_majority_review_malformed_private_content_cannot_leak():
    private_marker = "PRIVATE-MAJORITY-CONTENT-MUST-NOT-LEAK"
    malformed = {
        **majority_review_entry("reply_necessity"),
        "arbitrary_private_text": private_marker,
    }
    report = analyse_majority_review_payloads([malformed])
    rendered_json = json.dumps(report, ensure_ascii=False)
    rendered_markdown = digest.render_markdown(report)

    assert private_marker not in rendered_json
    assert private_marker not in rendered_markdown
    assert report["reply_pipeline_stages"]["majority_review_utilisation"][
        "coverage"
    ]["malformed_entries"] == 1


def test_majority_review_markdown_reports_rows_rates_and_coverage_warnings():
    malformed = {
        **majority_review_entry("authentication_review"),
        "unexpected": "excluded",
    }
    report = analyse_majority_review_payloads(
        [majority_review_entry("reply_necessity")],
        [majority_review_entry("allegation_review")],
        [majority_review_entry("authentication_review", calls=3)],
        [majority_review_entry("reply_necessity", calls=3, valid_votes=2)],
        MAJORITY_TELEMETRY_MISSING,
        [malformed],
    )
    rendered = digest.render_markdown(report)

    assert "### Majority-review utilisation" in rendered
    assert "Valid majority-family entries: **4**" in rendered
    assert "2 calls saved" in rendered
    assert "Overall short-circuit rate: **50.0%**" in rendered
    assert "reviewer-call reduction: **16.7%**" in rendered
    assert "| reply_necessity | 2 | 1 | 1 | 1 | 1 | 5 | 1 | 0 | 1 |" in rendered
    assert "| allegation_review | 1 | 1 | 0 | 1 | 0 | 2 | 1 | 0 | 0 |" in rendered
    assert "| authentication_review | 1 | 0 | 1 | 0 | 1 | 3 | 0 | 1 | 0 |" in rendered
    assert "| Overall | 4 | 2 | 2 | 2 | 2 | 10 | 2 | 1 | 1 |" in rendered
    assert "**Coverage warning:** 1 legacy/incomplete" in rendered
    assert "**Malformed telemetry warning:** 1 malformed" in rendered


def test_majority_review_markdown_legacy_only_is_unavailable_without_table():
    report = analyse_majority_review_payloads(MAJORITY_TELEMETRY_MISSING)
    rendered = digest.render_markdown(report)

    assert "### Majority-review utilisation" in rendered
    assert "unavailable for this window" in rendered
    assert "| Family | Resolutions |" not in rendered
    assert "zero-call" in rendered

    no_stage_summary = digest.render_markdown(digest.analyse([]))
    assert "### Majority-review utilisation" not in no_stage_summary


def test_majority_review_cli_json_is_serialisable_and_ordered(tmp_path):
    payload = {
        "event": "ai_reply_pipeline_stage_summary",
        "lane": "mention",
        "target_id": "701",
        "strategy_version": "tested-reply-pipeline-20260817",
        "status": "no_reply",
        "majority_review_summaries": [
            majority_review_entry("reply_necessity")
        ],
    }
    log = tmp_path / "mrsMThatcher.log"
    log.write_text(
        "2026-08-21 12:00:00 INFO log_event:1 - EVENT "
        + json.dumps(payload)
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "digest.json"

    assert digest.main([
        str(log),
        "--project-dir",
        str(tmp_path),
        "--no-state",
        "--json",
        "--output",
        str(output),
    ]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    utilisation = report["reply_pipeline_stages"][
        "majority_review_utilisation"
    ]

    assert utilisation["overall"]["reviewer_calls_saved"] == 1
    assert list(utilisation["per_family"]) == [
        "reply_necessity",
        "allegation_review",
        "authentication_review",
    ]
    assert "provider_call_counts" in report["reply_pipeline_stages"]


def test_stage_approval_is_reconciled_with_effective_local_rejection_and_drafts():
    base = datetime(2026, 8, 17, 9)
    target_id = "2089269156994523171"
    incoming = (
        "@MrsMThatcher What if the private economy isn’t healthy? "
        "Who creates the demand needed to get it moving?"
    )
    proposed = "A healthy economy rests on confidence, enterprise and sound money."
    repaired = "Consumers and businesses create demand through spending and investment."
    payloads = [
        (
            "INFO",
            "maybe_reply_to_mentions",
            f"Considering mention id={target_id} author_id=800 text={incoming!r}",
        ),
        (
            "INFO",
            "log_event",
            "EVENT " + json.dumps({
                "event": "ai_reply_pipeline_stage_summary",
                "lane": "mention",
                "target_id": target_id,
                "strategy_version": "tested-reply-pipeline-20260817",
                "status": "approved",
                "pipeline_stage_status": "approved",
                "terminal_reason": "pipeline_approved",
                "effective_status": "local_rejection",
                "effective_reason": "claim_audit_not_passed:rewrite_supported_factual",
                "direct_answer_repair_attempted": True,
                "direct_answer_repair_outcome": (
                    "claim_audit_not_passed:rewrite_supported_factual"
                ),
                "original_local_rejection_reason": (
                    "clarification_not_direct_factual_answer"
                ),
                "provider_call_counts": {"xAI": 2, "OpenAI": 2},
                "final_validation": "passed",
            }),
        ),
        (
            "INFO",
            "log_event",
            "EVENT " + json.dumps({
                "event": "ai_reply_pipeline_decision",
                "lane": "mention",
                "target_id": target_id,
                "strategy_version": "tested-reply-pipeline-20260817",
                "status": "approved",
                "pipeline_stage_status": "approved",
                "effective_status": "local_rejection",
                "effective_reason": "claim_audit_not_passed:rewrite_supported_factual",
                "mode": "opinion_or_principle",
                "final_reply_kind": "unknown",
                "factual_claim_count": 0,
                "model_call_count": 4,
                "original_local_rejection_reason": (
                    "clarification_not_direct_factual_answer"
                ),
                "direct_answer_repair_attempted": True,
                "direct_answer_repair_outcome": (
                    "claim_audit_not_passed:rewrite_supported_factual"
                ),
                "incoming_contribution": incoming,
                "proposed_draft": proposed,
                "repaired_draft": repaired,
            }),
        ),
        (
            "ERROR",
            "maybe_reply_to_mentions",
            "Clarification reply lacks direct_factual_answer mode; refusing "
            f"target_id={target_id}",
        ),
    ]
    records = [
        digest.Record(
            ts=base + timedelta(seconds=index),
            level=level,
            src=source,
            line=index,
            msg=message,
            path="mrsMThatcher.log",
            ordinal=index,
        )
        for index, (level, source, message) in enumerate(payloads, 1)
    ]

    report = digest.analyse(records)
    local = [
        event
        for event in report["events"]
        if event["kind"] == "reply_strategy_local_rejection"
    ]
    stage = next(
        event
        for event in report["events"]
        if event["kind"] == "reply_pipeline_stage_summary"
    )

    assert len(local) == 1
    assert local[0]["incoming_contribution"] == incoming
    assert local[0]["proposed_draft"] == proposed
    assert local[0]["repaired_draft"] == repaired
    assert local[0]["direct_answer_repair_attempted"] is True
    assert local[0]["effective_status"] == "local_rejection"
    assert stage["pipeline_stage_status"] == "approved"
    assert stage["final_validation"] == "passed"
    assert stage["effective_status"] == "local_rejection"
    assert report["reply_strategy"][
        "terminal_clarification_mode_rejection_count"
    ] == 1
    assert report["provider_usage"]["cost_summary"]["candidates"][0][
        "outcome"
    ] == "terminal_clarification_mode_rejection"

    rendered = digest.render_markdown(report)
    assert "## Effective local reply rejections" in rendered
    assert target_id in rendered
    assert incoming in rendered
    assert proposed in rendered
    assert repaired in rendered
    assert "Final effective outcome: `local_rejection`" in rendered
    assert "pipeline_stage_status" in rendered
    assert "effective_status" in rendered


def test_legacy_target_rejection_reports_lost_draft_as_unavailable_not_invented():
    target_id = "2089269156994523171"
    incoming = (
        "@MrsMThatcher What if the private economy isn’t healthy? "
        "Who creates the demand needed to get it moving?"
    )
    records = [
        digest.Record(
            datetime(2026, 8, 17, 9, 0, index),
            level,
            source,
            index,
            message,
            "mrsMThatcher.log",
            index,
        )
        for index, (level, source, message) in enumerate([
            (
                "INFO",
                "maybe_reply_to_mentions",
                f"Considering mention id={target_id} author_id=800 text={incoming!r}",
            ),
            (
                "INFO",
                "log_event",
                "EVENT " + json.dumps({
                    "event": "ai_reply_pipeline_stage_summary",
                    "lane": "mention",
                    "target_id": target_id,
                    "strategy_version": "tested-reply-pipeline-20260816",
                    "status": "approved",
                    "terminal_reason": "pipeline_approved",
                    "final_validation": "passed",
                }),
            ),
            (
                "INFO",
                "log_event",
                "EVENT " + json.dumps({
                    "event": "ai_reply_pipeline_decision",
                    "lane": "mention",
                    "target_id": target_id,
                    "strategy_version": "tested-reply-pipeline-20260816",
                    "status": "approved",
                    "mode": "opinion_or_principle",
                    "factual_claim_count": 0,
                }),
            ),
            (
                "ERROR",
                "maybe_reply_to_mentions",
                "Clarification reply lacks direct_factual_answer mode; refusing "
                f"target_id={target_id}",
            ),
        ], 1)
    ]

    report = digest.analyse(records)
    local = next(
        event
        for event in report["events"]
        if event["kind"] == "reply_strategy_local_rejection"
    )
    stage = next(
        event
        for event in report["events"]
        if event["kind"] == "reply_pipeline_stage_summary"
    )

    assert local["incoming_contribution"] == incoming
    assert local["proposed_draft"] is None
    assert stage["pipeline_stage_status"] == "approved"
    assert stage["effective_status"] == "local_rejection"
    rendered = digest.render_markdown(report)
    assert "provider response was not retained" in rendered
    assert "Final effective outcome: `local_rejection`" in rendered


def test_nine_tested_decisions_reconcile_across_headline_sections_and_raw_counts():
    base = datetime(2026, 8, 17, 3, 24, 9)
    records = []
    ordinal = 0
    for index in range(9):
        version = (
            "tested-reply-pipeline-20260816"
            if index < 8
            else "tested-reply-pipeline-20260817"
        )
        for event_payload in (
            {
                "event": "ai_reply_pipeline_stage_summary",
                "lane": "mention",
                "target_id": str(700 + index),
                "strategy_version": version,
                "status": "no_reply",
                "terminal_reason": "reply_necessity_review",
                "provider_call_counts": {"xAI": 1, "OpenAI": 3},
            },
            {
                "event": "ai_reply_pipeline_decision",
                "lane": "mention",
                "target_id": str(700 + index),
                "strategy_version": version,
                "status": "no_reply",
                "mode": "no_reply",
                "factual_claim_count": 0,
                "reason": "reply_necessity_review",
            },
        ):
            ordinal += 1
            records.append(digest.Record(
                base + timedelta(seconds=ordinal),
                "INFO",
                "log_event",
                ordinal,
                "EVENT " + json.dumps(event_payload),
                "mrsMThatcher.log",
                ordinal,
            ))

    report = digest.analyse(records)
    stages = report["reply_pipeline_stages"]
    stats = report["summary"]["stats"]

    assert report["reply_strategy"]["conversational_candidate_count"] == 9
    assert stages["tested_pipeline_decision_count"] == 9
    assert stages["all_stage_summary_event_count"] == 9
    assert stages["complete_stage_telemetry_count"] == 9
    assert stages["latest_strategy_version_decision_count"] == 1
    assert stats["reply_strategy_decision"] == 9
    assert stats["reply_pipeline_stage_summary"] == 9
    assert stats["tested_pipeline_decisions_all_versions"] == 9
    assert stats["tested_pipeline_stage_summary_events_all_versions"] == 9
    assert "9 tested-pipeline decisions across all versions" in (
        report["summary"]["headline"]
    )

    rendered = digest.render_markdown(report)
    assert "All tested-pipeline decisions: **9**" in rendered
    assert "All stage-summary events: **9**" in rendered
    assert "Complete stage telemetry: **9**" in rendered
    assert "tested-reply-pipeline-20260816 | 8 | 8 | 8 | 0" in rendered
    assert "tested-reply-pipeline-20260817 | 1 | 1 | 1 | 0" in rendered
    assert "Current/latest strategy-version subtotal" in rendered


def test_majority_resolvability_is_strict_boolean_and_requires_review_outcome():
    version = "tested-reply-pipeline-20260817"
    summary = digest.reply_pipeline_stage_summary([
        {
            "kind": "reply_pipeline_stage_summary",
            "strategy_version": version,
            "reply_necessity_outcome": None,
            "reply_necessity_majority_resolvable": False,
            "allegation_conspiracy_outcome": "confirm_no_reply",
            "allegation_conspiracy_majority_resolvable": 1,
        }
    ])

    assert summary["reply_necessity_majority_resolvable_counts"] == {}
    assert summary["allegation_conspiracy_majority_resolvable_counts"] == {}


def test_ai_first_event_without_strategy_version_is_not_mislabelled_v2():
    record = digest.Record(
        ts=datetime(2026, 7, 20, 12),
        level="INFO",
        src="log_event",
        line=1,
        msg=(
            'EVENT {"event":"ai_reply_pipeline_decision","lane":"mention",'
            '"target_id":"100","mode":"no_reply","tone":"neutral",'
            '"factual_claim_count":0,"evidence_ids":[],"reason":"not_warranted",'
            '"author_quarantine_evidence":"unavailable_ai_first_reply_strategy"}'
        ),
        path="mrsMThatcher.log",
        ordinal=1,
    )

    report = digest.analyse([record])
    decision = next(
        item for item in report["events"]
        if item["kind"] == "reply_strategy_decision"
    )

    assert decision["strategy_version"] == "unavailable"
    assert (
        decision["author_quarantine_evidence"]
        == "unavailable_ai_first_reply_strategy"
    )
    assert "unavailable_ai_first_reply_strategy" in digest.render_markdown(report)


def test_ai_first_operational_failure_is_not_reported_as_editorial_no_reply():
    record = digest.Record(
        ts=datetime(2026, 7, 20, 12),
        level="INFO",
        src="log_event",
        line=1,
        msg=(
            'EVENT {"event":"ai_reply_pipeline_failure","lane":"mention",'
            '"target_id":"100","status":"operational_failure",'
            '"strategy_version":"ai-first-reply-v3","reason":"proposer_invalid",'
            '"model_call_count":2,"revision_count":0,'
            '"author_quarantine_evidence":"unavailable_ai_first_reply_strategy"}'
        ),
        path="mrsMThatcher.log",
        ordinal=1,
    )

    report = digest.analyse([record])
    strategy = report["reply_strategy"]
    failures = [
        event for event in report["events"]
        if event["kind"] == "reply_strategy_failure"
    ]

    assert strategy["pipeline_failure_count"] == 1
    assert strategy["pipeline_failure_reason_counts"] == {"proposer_invalid": 1}
    assert strategy["rejection_reason_counts"] == {}
    assert strategy["outcome_status_counts"].get("terminal_no_reply", 0) == 0
    assert failures[0]["status"] == "operational_failure"
    assert (
        failures[0]["author_quarantine_evidence"]
        == "unavailable_ai_first_reply_strategy"
    )
    rendered = digest.render_markdown(report)
    assert "Operational AI-first pipeline failures" in rendered
    assert "retryable, not editorial no-reply" in rendered


def test_unposted_draft_is_excluded_and_confirmed_outcome_is_deduplicated():
    result = digest.reply_strategy_summary([
        event("reply_strategy_decision", lane="mention", target_id="draft", mode="wry_reply",
              humour_tone="wry", evidence_confidence="none", retrieved_count=0,
              factual_claim=False, grounded=False),
        event("reply_strategy_outcome", lane="mention", target_id="posted", reply_post_id="99",
              mode="historical_context", humour_tone="dry", evidence_confidence="medium",
              retrieved_count=2, factual_claim=True, grounded=True),
        event("reply_strategy_outcome", lane="mention", target_id="posted", reply_post_id="99",
              mode="historical_context", humour_tone="dry", evidence_confidence="medium",
              retrieved_count=2, factual_claim=True, grounded=True),
    ])
    assert result["mode_counts"]["wry_reply"] == 0
    assert result["mode_counts"]["historical_context"] == 1
    assert result["confirmed_outcome_count"] == 1


def test_generated_strategy_and_public_outcomes_are_reported_separately():
    result = digest.reply_strategy_summary([
        event("reply_strategy_decision", lane="mention", target_id="skip", mode="no_reply",
              humour_tone="none", evidence_confidence="low", retrieved_count=0,
              factual_claim=False, grounded=False, no_reply_reason="weak evidence"),
        event("reply_strategy_decision", lane="quote_tweet", target_id="posted", mode="warm_reply",
              humour_tone="warm", evidence_confidence="none", retrieved_count=0,
              factual_claim=False, grounded=False),
        event("reply_strategy_outcome", status="confirmed", lane="quote_tweet", target_id="posted",
              reply_post_id="99", mode="warm_reply", humour_tone="warm",
              evidence_confidence="none", retrieved_count=0, factual_claim=False, grounded=False),
        event("reply_strategy_decision", lane="mention", target_id="failed", mode="historical_context",
              humour_tone="dry", evidence_confidence="high", retrieved_count=1,
              factual_claim=True, grounded=True),
        event("reply_strategy_outcome", status="posting_failed_terminal", lane="mention",
              target_id="failed", mode="historical_context", humour_tone="dry",
              evidence_confidence="high", retrieved_count=1, factual_claim=True, grounded=True,
              failure_reason="reply_not_permitted"),
    ])

    assert result["generated_mode_counts"]["historical_context"] == 1
    assert result["generated_mode_counts"]["warm_reply"] == 1
    assert result["generated_mode_counts"]["no_reply"] == 1
    assert result["outcome_status_counts"] == {
        "posted": 1,
        "posting_failed": 1,
        "terminal_no_reply": 1,
    }
    assert result["generated_grounded_count"] == 1
    assert result["posted_grounded_count"] == 0
    assert result["generated_average_retrieved_packet_count"] == 1 / 3
    assert result["generated_confidence_counts"]["high"] == 1


def test_xai_usage_includes_image_tokens():
    record = digest.Record(
        ts=datetime(2026, 7, 16, 12), level="INFO", src="ask_grok_for_reply", line=1,
        msg="", path="mrsMThatcher.log", ordinal=1,
    )
    item = digest.summarize_xai_usage_event(
        record,
        {"prompt_tokens": 100, "prompt_tokens_details": {"image_tokens": 37}},
        {"lane": "mention", "context_id": "123"},
    )

    assert item["image_tokens"] == 37
    assert digest.xai_usage_totals([item])["image_tokens"] == 37


def test_conversational_ai_cost_is_attributed_by_candidate_outcome_and_stage():
    records = []
    ordinal = 0

    def add(offset, source, message):
        nonlocal ordinal
        ordinal += 1
        records.append(
            digest.Record(
                ts=datetime(2026, 7, 28, 12) + timedelta(seconds=offset),
                level="INFO",
                src=source,
                line=ordinal,
                msg=message,
                path="mrsMThatcher.log",
                ordinal=ordinal,
            )
        )

    def consider(offset, target_id):
        add(
            offset,
            "maybe_reply_to_mentions",
            f"Considering mention id={target_id} author_id=456 text='fixture'",
        )

    def provider_call(offset, stage, ticks, tokens):
        add(
            offset,
            "xai_structured_reply_call",
            f"Calling AI-first reply stage={stage} model=grok-4.3",
        )
        add(
            offset + 1,
            "xai_structured_reply_call",
            (
                f"xAI reply stage={stage} usage={{'prompt_tokens': 10, "
                f"'completion_tokens': 2, 'total_tokens': {tokens}, "
                "'prompt_tokens_details': {'cached_tokens': 1}, "
                "'completion_tokens_details': {'reasoning_tokens': 3}, "
                f"'num_sources_used': 0, 'cost_in_usd_ticks': {ticks}}}"
            ),
        )

    consider(0, "101")
    provider_call(1, "proposer", 10_000_000, 100)
    add(
        3,
        "log_event",
        'EVENT {"event":"ai_reply_pipeline_decision","lane":"mention",'
        '"target_id":"101","status":"no_reply","mode":"no_reply",'
        '"model_call_count":1,"reason":"not_warranted"}',
    )

    consider(10, "202")
    provider_call(11, "proposer", 20_000_000, 200)
    provider_call(13, "claim_auditor", 30_000_000, 300)
    provider_call(15, "reviewer", 40_000_000, 400)
    add(
        17,
        "log_event",
        'EVENT {"event":"ai_reply_pipeline_decision","lane":"mention",'
        '"target_id":"202","status":"approved","mode":"opinion_or_principle",'
        '"model_call_count":3}',
    )
    add(
        18,
        "log_event",
        'EVENT {"event":"ai_reply_pipeline_outcome","lane":"mention",'
        '"target_id":"202","reply_post_id":"900","status":"confirmed",'
        '"mode":"opinion_or_principle","model_call_count":3}',
    )

    consider(30, "303")
    provider_call(31, "proposer", 50_000_000, 500)
    provider_call(33, "claim_auditor", 60_000_000, 600)
    provider_call(35, "revision_proposer", 70_000_000, 700)
    provider_call(37, "revision_claim_auditor", 80_000_000, 800)
    provider_call(39, "revision_reviewer", 90_000_000, 900)
    add(
        41,
        "log_event",
        'EVENT {"event":"ai_reply_pipeline_decision","lane":"mention",'
        '"target_id":"303","status":"approved","mode":"opinion_or_principle",'
        '"model_call_count":5}',
    )
    add(
        42,
        "log_event",
        'EVENT {"event":"ai_reply_pipeline_outcome","lane":"mention",'
        '"target_id":"303","status":"posting_failed_retryable",'
        '"mode":"opinion_or_principle","model_call_count":5}',
    )

    report = digest.analyse(records)
    summary = report["xai_usage"]["cost_summary"]
    by_outcome = {row["outcome"]: row for row in summary["outcomes"]}
    by_stage = {row["stage"]: row for row in summary["stages"]}

    assert summary["coverage_complete"] is True
    assert summary["candidate_count"] == 3
    assert summary["published_candidate_count"] == 1
    assert summary["known_cost_in_usd_ticks"] == 450_000_000
    assert summary["per_reviewed_candidate"] == {
        "ticks": 450_000_000,
        "divisor": 3,
    }
    assert summary["effective_per_published_reply"] == {
        "ticks": 450_000_000,
        "divisor": 1,
    }
    assert by_outcome["deliberately_declined"]["known_cost_in_usd_ticks"] == 10_000_000
    assert by_outcome["published"]["known_cost_in_usd_ticks"] == 90_000_000
    assert by_outcome["posting_failed"]["known_cost_in_usd_ticks"] == 350_000_000
    assert by_stage["proposer"]["known_cost_in_usd_ticks"] == 80_000_000
    assert by_stage["claim_auditor"]["known_cost_in_usd_ticks"] == 90_000_000
    assert by_stage["reviewer"]["known_cost_in_usd_ticks"] == 40_000_000

    rendered = digest.render_markdown(report)
    assert "Provider-reported known cost lower bound: US$0.04500000" in rendered
    assert "Mean provider cost per AI-reviewed candidate: **US$0.01500000**" in rendered
    assert "Effective provider cost per published conversational reply" in rendered
    assert "posting failed" in rendered
    assert digest.render_markdown(report) == rendered


def test_exact_duplicate_terminal_accounting_reconciles_headline_and_costs():
    records = []
    ordinal = 0
    base = datetime(2026, 7, 28, 15)

    def add(offset, source, message):
        nonlocal ordinal
        ordinal += 1
        records.append(
            digest.Record(
                ts=base + timedelta(seconds=offset),
                level="INFO",
                src=source,
                line=ordinal,
                msg=message,
                path="mrsMThatcher.log",
                ordinal=ordinal,
            )
        )

    for index in range(21):
        target_id = str(1000 + index)
        offset = index * 10
        ticks = (index + 1) * 1_000_000
        tokens = 100 + index
        add(
            offset,
            "maybe_reply_to_mentions",
            f"Considering mention id={target_id} author_id=456 text='fixture'",
        )
        add(
            offset + 1,
            "xai_structured_reply_call",
            "Calling AI-first reply stage=reviewer model=grok-4.3",
        )
        add(
            offset + 2,
            "xai_structured_reply_call",
            (
                "xAI reply stage=reviewer usage={'prompt_tokens': 10, "
                "'completion_tokens': 2, "
                f"'total_tokens': {tokens}, "
                "'prompt_tokens_details': {'cached_tokens': 1}, "
                "'completion_tokens_details': {'reasoning_tokens': 3}, "
                f"'num_sources_used': 0, 'cost_in_usd_ticks': {ticks}}}"
            ),
        )
        if index < 11:
            add(
                offset + 3,
                "log_event",
                'EVENT {"event":"ai_reply_pipeline_decision",'
                '"status":"approved","lane":"mention",'
                f'"target_id":"{target_id}",'
                '"mode":"opinion_or_principle","model_call_count":1}',
            )
            add(
                offset + 4,
                "log_event",
                'EVENT {"event":"ai_reply_pipeline_outcome",'
                '"status":"confirmed","lane":"mention",'
                f'"target_id":"{target_id}","reply_post_id":"{9000 + index}",'
                '"mode":"opinion_or_principle","tone":"firm",'
                '"factual_claim_count":0,"retrieved_count":0,'
                '"evidence_reference_count":0,"model_call_count":1}',
            )
        elif index < 19:
            add(
                offset + 3,
                "log_event",
                'EVENT {"event":"ai_reply_pipeline_decision",'
                '"status":"no_reply","lane":"mention",'
                f'"target_id":"{target_id}","mode":"no_reply",'
                '"tone":"none","factual_claim_count":0,'
                '"retrieved_count":0,"evidence_reference_count":0,'
                '"reviewer_verdict":"confirm_no_reply",'
                '"reason":"independent_no_reply_confirmed",'
                '"model_call_count":1}',
            )
        else:
            add(
                offset + 3,
                "log_event",
                'EVENT {"event":"ai_reply_pipeline_decision",'
                '"status":"no_reply","lane":"mention",'
                f'"target_id":"{target_id}","mode":"courtesy",'
                '"tone":"neutral","reason":"exact_duplicate_reply",'
                '"reviewer_verdict":"not_run","factual_claim_count":0,'
                '"retrieved_count":0,"evidence_reference_count":0,'
                '"model_call_count":1}',
            )

    report = digest.analyse(records)
    strategy = report["reply_strategy"]
    cost = report["xai_usage"]["cost_summary"]
    cost_outcomes = {row["outcome"]: row for row in cost["outcomes"]}
    rendered = digest.render_markdown(report)

    assert strategy["conversational_candidate_count"] == 21
    assert strategy["confirmed_outcome_count"] == 11
    assert strategy["deliberately_declined_count"] == 8
    assert strategy["terminal_repetition_rejection_count"] == 2
    assert strategy["outcome_status_counts"] == {
        "posted": 11,
        "terminal_no_reply": 8,
        "terminal_repetition_rejection": 2,
    }
    assert strategy["repetition_control_counts"]["exact_duplicate_rejected"] == 2
    assert sum(strategy["mode_counts"].values()) == 21
    assert strategy["mode_counts"]["courtesy"] == 2
    assert strategy["mode_counts"]["no_reply"] == 8
    assert sum(
        sum(counts.values())
        for counts in strategy["mode_counts_by_lane"].values()
    ) == 21
    assert sum(strategy["humour_tone_counts"].values()) == 21
    assert strategy["grounding_metadata_unavailable_count"] == 0
    assert strategy["factual_claim_metadata_unavailable_count"] == 0
    assert strategy["no_retrieved_packets_count"] == 21
    assert strategy["retrieved_packet_metadata_unavailable_count"] == 0
    assert strategy["no_evidence_references_count"] == 21
    assert strategy["evidence_reference_metadata_unavailable_count"] == 0
    assert cost["coverage_complete"] is True
    assert cost["candidate_count"] == 21
    assert cost["published_candidate_count"] == 11
    assert {
        outcome: row["candidate_count"]
        for outcome, row in cost_outcomes.items()
    } == {
        "deliberately_declined": 8,
        "published": 11,
        "terminal_repetition_rejection": 2,
    }
    assert cost_outcomes["terminal_repetition_rejection"] == {
        "outcome": "terminal_repetition_rejection",
        "candidate_count": 2,
        "observed_successful_calls": 2,
        "total_tokens": 239,
        "known_cost_in_usd_ticks": 41_000_000,
        "costed_successful_calls": 2,
        "uncosted_successful_calls": 0,
    }
    assert all(
        candidate["outcome"] != "approved_not_confirmed_in_window"
        for candidate in cost["candidates"]
    )
    assert sum(row["candidate_count"] for row in cost["outcomes"]) == 21
    assert "21 conversational candidates AI-reviewed" in report["summary"]["headline"]
    assert "11 replies posted" in report["summary"]["headline"]
    assert "2 terminal repetition rejections" in report["summary"]["headline"]
    assert "8 deliberately declined" in report["summary"]["headline"]
    assert (
        "Public outcomes: posted=11, terminal_no_reply=8, "
        "terminal_repetition_rejection=2"
    ) in rendered
    assert "| terminal repetition rejection | 2 | 2 | 239 | US$0.00410000 |" in rendered


def test_conversational_ai_missing_usage_and_cost_are_not_reported_as_zero():
    records = [
        digest.Record(
            datetime(2026, 7, 28, 13), "INFO", "maybe_reply_to_mentions", 1,
            "Considering mention id=404 author_id=456 text='fixture'",
            "mrsMThatcher.log", 1,
        ),
        digest.Record(
            datetime(2026, 7, 28, 13, 0, 1), "INFO", "xai_structured_reply_call", 2,
            "Calling AI-first reply stage=proposer model=grok-4.3",
            "mrsMThatcher.log", 2,
        ),
        digest.Record(
            datetime(2026, 7, 28, 13, 0, 2), "INFO", "xai_structured_reply_call", 3,
            "xAI reply stage=proposer usage={'total_tokens': 100, "
            "'cost_in_usd_ticks': 10000000}",
            "mrsMThatcher.log", 3,
        ),
        digest.Record(
            datetime(2026, 7, 28, 13, 0, 3), "INFO", "xai_structured_reply_call", 4,
            "Calling AI-first reply stage=claim_auditor model=grok-4.3",
            "mrsMThatcher.log", 4,
        ),
        digest.Record(
            datetime(2026, 7, 28, 13, 0, 4), "INFO", "xai_structured_reply_call", 5,
            "Calling AI-first reply stage=reviewer model=grok-4.3",
            "mrsMThatcher.log", 5,
        ),
        digest.Record(
            datetime(2026, 7, 28, 13, 0, 5), "INFO", "xai_structured_reply_call", 6,
            "xAI reply stage=reviewer usage={'total_tokens': 200}",
            "mrsMThatcher.log", 6,
        ),
        digest.Record(
            datetime(2026, 7, 28, 13, 0, 6), "INFO", "log_event", 7,
            'EVENT {"event":"ai_reply_pipeline_decision","lane":"mention",'
            '"target_id":"404","status":"approved","mode":"courtesy",'
            '"model_call_count":3}',
            "mrsMThatcher.log", 7,
        ),
    ]

    report = digest.analyse(records)
    usage = report["xai_usage"]
    summary = usage["cost_summary"]
    stages = {row["stage"]: row for row in summary["stages"]}

    assert usage["events"][1]["cost_in_usd_ticks"] is None
    assert usage["totals"]["cost_in_usd_ticks"] == 10_000_000
    assert usage["totals"]["costed_call_count"] == 1
    assert usage["totals"]["uncosted_successful_call_count"] == 1
    assert summary["coverage_complete"] is False
    assert summary["per_reviewed_candidate"] is None
    assert summary["effective_per_published_reply"] is None
    assert stages["claim_auditor"]["call_starts_without_usage"] == 1
    assert stages["reviewer"]["uncosted_successful_calls"] == 1

    rendered = digest.render_markdown(report)
    assert "Provider-reported known cost lower bound: US$0.00100000" in rendered
    assert "Exact per-candidate and effective-per-published-reply averages are unavailable" in rendered
    assert "| unknown | 0 | xAI | reviewer | grok-4.3 | unknown |" in rendered


def test_conversational_usage_without_matching_call_start_is_incomplete():
    records = [
        digest.Record(
            datetime(2026, 7, 28, 14), "INFO", "maybe_reply_to_mentions", 1,
            "Considering mention id=505 author_id=606 text='fixture'",
            "mrsMThatcher.log", 1,
        ),
        digest.Record(
            datetime(2026, 7, 28, 14, 0, 1), "INFO", "ask_grok_for_reply", 2,
            "Asking Grok for reply. context fixture",
            "mrsMThatcher.log", 2,
        ),
        digest.Record(
            datetime(2026, 7, 28, 14, 0, 2), "INFO", "xai_structured_reply_call", 3,
            "xAI reply stage=proposer usage={'total_tokens': 100, "
            "'cost_in_usd_ticks': 10000000}",
            "mrsMThatcher.log", 3,
        ),
        digest.Record(
            datetime(2026, 7, 28, 14, 0, 3), "INFO", "log_event", 4,
            'EVENT {"event":"ai_reply_pipeline_decision","lane":"mention",'
            '"target_id":"505","status":"no_reply","mode":"no_reply",'
            '"model_call_count":1,"reason":"not_warranted"}',
            "mrsMThatcher.log", 4,
        ),
    ]

    report = digest.analyse(records)
    usage_event = report["xai_usage"]["events"][0]
    summary = report["xai_usage"]["cost_summary"]
    candidate = summary["candidates"][0]

    assert usage_event["call_start_matched"] is False
    assert summary["coverage_complete"] is False
    assert summary["unmatched_successful_call_count"] == 1
    assert candidate["unmatched_successful_calls"] == 1
    assert candidate["call_coverage"] == "successful_usage_without_call_start"
    assert (
        "one or more successful usage records lack a matching provider call start"
        in summary["coverage_reasons"]
    )
    rendered = digest.render_markdown(report)
    assert "successful_usage_without_call_start" in rendered
    assert "Provider-reported known cost lower bound" in rendered


def test_xai_stage_and_usd_helpers_are_deterministic():
    assert (
        digest.xai_usage_stage_from_msg(
            "xAI reply stage=evidence usage={'cost_in_usd_ticks': 1}"
        )
        == "evidence"
    )
    assert (
        digest.xai_usage_stage_from_msg(
            "xAI usage={'cost_in_usd_ticks': 1}"
        )
        == "legacy_or_unavailable"
    )
    assert digest.format_usd_ticks(289_866_500) == "US$0.02898665"
    assert digest.optional_int_usage_value(0) == 0
    assert digest.optional_int_usage_value(10) == 10
    assert digest.optional_int_usage_value(-1) is None
    assert digest.optional_int_usage_value(1.5) is None
    assert digest.optional_int_usage_value("10") is None
    assert digest.xai_usage_context_from_pending(
        {
            "source": "hot_post_reply",
            "hot_post_reply_id": "505",
            "author_id": "606",
        },
        {},
    ) == {
        "lane": "hot-post",
        "context_id": "505",
        "author_id": "606",
    }


def test_candidate_event_without_in_window_usage_makes_cost_coverage_incomplete():
    usage = [
        {
            "time": "2026-07-28 12:00:01",
            "lane": "mention",
            "context_id": "one",
            "stage": "proposer",
            "total_tokens": 100,
            "cost_in_usd_ticks": 10_000_000,
        }
    ]
    reply_events = [
        event(
            "reply_strategy_decision",
            lane="mention",
            target_id="one",
            mode="no_reply",
            model_call_count=1,
        ),
        event(
            "reply_strategy_decision",
            lane="mention",
            target_id="two",
            mode="courtesy",
            model_call_count=1,
        ),
        event(
            "reply_strategy_outcome",
            lane="mention",
            target_id="two",
            status="posted",
            mode="courtesy",
            model_call_count=1,
        ),
    ]

    summary = digest.xai_reply_cost_summary(usage, reply_events)
    candidates = {
        item["context_id"]: item for item in summary["candidates"]
    }

    assert summary["candidate_count"] == 2
    assert summary["published_candidate_count"] == 1
    assert summary["coverage_complete"] is False
    assert summary["per_reviewed_candidate"] is None
    assert summary["effective_per_published_reply"] is None
    assert candidates["two"]["outcome"] == "published"
    assert candidates["two"]["observed_successful_calls"] == 0
    assert candidates["two"]["call_coverage"] == "successful_usage_missing"


def test_repeated_pipeline_execution_for_one_target_is_reported_as_ambiguous():
    usage = [
        {
            "time": "2026-07-28 12:00:01",
            "lane": "mention",
            "context_id": "repeat",
            "stage": "proposer",
            "total_tokens": 100,
            "cost_in_usd_ticks": 10_000_000,
        },
        {
            "time": "2026-07-28 12:01:01",
            "lane": "mention",
            "context_id": "repeat",
            "stage": "proposer",
            "total_tokens": 100,
            "cost_in_usd_ticks": 10_000_000,
        },
    ]
    reply_events = [
        event(
            "reply_strategy_failure",
            lane="mention",
            target_id="repeat",
            model_call_count=1,
        ),
        event(
            "reply_strategy_decision",
            lane="mention",
            target_id="repeat",
            mode="no_reply",
            model_call_count=1,
        ),
    ]

    summary = digest.xai_reply_cost_summary(usage, reply_events)

    assert summary["coverage_complete"] is False
    assert summary["candidates"][0]["pipeline_execution_event_count"] == 2
    assert (
        summary["candidates"][0]["call_coverage"]
        == "multiple_pipeline_executions"
    )


def test_403_target_eligibility_incident_is_not_labelled_as_5xx():
    records = [
        digest.Record(datetime(2026, 7, 16, 12, 19), "INFO", "maybe_reply_to_mentions", 1,
                      "Considering mention id=2077713953983987776 author_id=352335305 text='not a mention'",
                      "mrsMThatcher.log", 1),
        digest.Record(datetime(2026, 7, 16, 12, 19, 1), "ERROR", "x_request", 2,
                      "X API error 403: You can only reply to or quote posts where you are mentioned or are the author.",
                      "mrsMThatcher.log", 2),
        digest.Record(datetime(2026, 7, 16, 12, 19, 1), "ERROR", "maybe_reply_to_mentions", 3,
                      "Failed to post generated reply\nTraceback (most recent call last): ...",
                      "mrsMThatcher.log", 3),
        digest.Record(datetime(2026, 7, 16, 12, 19, 1), "ERROR", "record_api_error", 4,
                      "Entering API cooldown after repeated errors until 2026-07-16 13:19:01",
                      "mrsMThatcher.log", 4),
        digest.Record(datetime(2026, 7, 16, 12, 34), "INFO", "maybe_reply_to_mentions", 3,
                      "Considering mention id=2077713953983987776 author_id=352335305 text='not a mention'",
                      "mrsMThatcher.log", 3),
        digest.Record(datetime(2026, 7, 16, 12, 34, 1), "ERROR", "x_request", 4,
                      "X API error 403: You can only reply to or quote posts where you are mentioned or are the author.",
                      "mrsMThatcher.log", 4),
    ]

    report = digest.analyse(records)
    rendered = digest.render_markdown(report)

    assert report["api_health"]["target_eligibility_403_count"] == 2
    assert report["api_health"]["unique_incident_count"] == 1
    assert report["api_health"]["transient_failure_count"] == 0
    assert report["api_health"]["legacy_cooldown_from_target_restriction_count"] == 1
    assert "operational error" not in report["summary"]["headline"]
    assert "503/5xx summary" not in rendered
    assert "reply-target eligibility 403 responses: **2**" in rendered
    assert "legacy cooldown activation" in rendered


def test_legacy_target_403_is_correlated_with_generated_grounded_decision():
    records = [
        digest.Record(
            datetime(2026, 7, 16, 12, 19), "INFO", "maybe_reply_to_mentions", 1,
            "Considering mention id=2077713953983987776 author_id=352335305 text='not a mention'",
            "mrsMThatcher.log", 1,
        ),
        digest.Record(
            datetime(2026, 7, 16, 12, 19, 1), "INFO", "log_event", 2,
            'EVENT {"event":"reply_strategy_decision","lane":"mention",'
            '"target_id":"2077713953983987776","mode":"historical_context",'
            '"humour_tone":"dry","evidence_confidence":"high",'
            '"retrieved_quote_ids":["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],'
            '"factual_claim_made":true,"grounded":true,"no_reply_reason":""}',
            "mrsMThatcher.log", 2,
        ),
        digest.Record(
            datetime(2026, 7, 16, 12, 19, 2), "ERROR", "x_request", 3,
            "X API error 403: You can only reply to or quote posts where you are mentioned or are the author.",
            "mrsMThatcher.log", 3,
        ),
    ]

    report = digest.analyse(records)

    assert report["reply_strategy"]["generated_grounded_count"] == 1
    assert report["reply_strategy"]["posted_grounded_count"] == 0
    assert report["reply_strategy"]["outcome_status_counts"]["posting_failed"] == 1
    inferred = [
        event for event in report["events"]
        if event.get("kind") == "reply_strategy_outcome"
    ]
    assert len(inferred) == 1
    assert inferred[0]["legacy_inferred"] is True


def test_markdown_escapes_optional_event_metadata():
    record = digest.Record(
        ts=datetime(2026, 7, 15, 12), level="INFO", src="log_event", line=1,
        msg='EVENT {"event":"historical_context_reply","status":"completed","verification_label":"Exact | wording","source_class":"Hansard"}',
        path="new.log", ordinal=1,
    )
    rendered = digest.render_markdown(digest.analyse([record]))
    assert "Exact \\| wording" in rendered
    assert "## Historical context reply quality" in rendered
    assert "## Conversational reply strategy" in rendered


def test_routine_cap_and_spacing_events_are_in_compact_strategy_summary():
    records = [
        digest.Record(datetime(2026, 7, 15, 12), "INFO", "main", 1,
                      "Daily generated/replied cap reached", "old.log", 1),
        digest.Record(datetime(2026, 7, 15, 12, 1), "INFO", "main", 2,
                      "Skipping mention check: minimum interval between replies not reached", "old.log", 2),
    ]
    result = digest.analyse(records)["reply_strategy"]["routine_skip_reason_counts"]
    assert result == {"daily_cap": 1, "spacing": 1}


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


def test_configured_veto_health_is_visible_before_any_runtime_observation(
    tmp_path,
    monkeypatch,
):
    import semantic_alignment.quote_image_semantic_veto as veto

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
                {
                    "policy_version": "current-test-policy",
                    "quote_count": 611,
                    "image_count": 91,
                    "pair_count": 22066,
                    "total_authorised_pair_count": 55601,
                    "allow_count": 22000,
                    "veto_count": 66,
                    "adjudicated_unknown_pair_count": 167,
                    "not_adjudicated_pair_count": 33368,
                }
        ),
        encoding="utf-8",
    )
    (tmp_path / "mrsMThatcher.local.json").write_text(
        json.dumps(
            {
                "quote_image_semantic_veto": {
                    "enabled": True,
                    "mode": "shadow",
                    "fail_open": True,
                    "manifest_path": "manifest.json",
                    "maximum_shadow_history": 10000,
                    "record_best_allowed_alternative": True,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(veto, "validate_shadow_config", lambda _config: [])
    monkeypatch.setattr(
        veto,
        "validate_compiled_manifest",
        lambda _manifest: {
            "quote_count": 611,
            "image_count": 91,
            "pair_count": 22066,
        },
    )
    monkeypatch.setattr(veto, "manifest_source_hash_mismatches", lambda *_args: [])

    summary = digest.quote_image_semantic_veto_shadow_snapshot(tmp_path)

    assert summary["available"] is False
    assert summary["configured_manifest_available"] is True
    assert summary["configured_manifest_policy_version"] == "current-test-policy"
    assert summary["configured_manifest_quote_count"] == 611
    report = digest.analyse([])
    report["quote_image_semantic_veto_shadow"]["runtime_summary"] = summary
    rendered = digest.render_markdown(report)
    assert "Loaded configured manifest: **loaded**" in rendered
    assert "current-test-policy" in rendered
    assert "611 quotations × 91 images = 55601 pairs" in rendered


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


def test_reply_visual_context_correlates_collection_and_window_gaps_safely():
    full_hash = "b" * 64
    records = [
        reply_visual_record(
            0,
            message=(
                "Reply media context lane=mention_reply target_id=100 photos=2 "
                "mode=multimodal status=supplied"
            ),
        ),
        reply_visual_record(
            1,
            reply_visual_payload("100", description_sha256=full_hash),
        ),
        reply_visual_record(
            2,
            message=(
                "Reply media context lane=mention target_id=101 photos=1 "
                "mode=multimodal status=supplied"
            ),
        ),
        reply_visual_record(
            3,
            reply_visual_payload(
                "102",
                lane="quote_tweet",
                status="provider_error",
                supplied_image_count=1,
            ),
        ),
        reply_visual_record(
            4,
            message=(
                "Reply media context unavailable lane=mention target_id=103 "
                "photos_expected=2 mode=multimodal status=unavailable"
            ),
            level="WARNING",
        ),
    ]

    report = digest.analyse(records)
    targets = {
        (row["lane"], row["target_id"]): row
        for row in report["reply_visual_context_targets"]
    }
    summary = report["reply_visual_context_summary"]

    analysed = targets[("mention", "100")]
    assert analysed["correlation_status"] == (
        "collection_supplied_analysis_analysed"
    )
    assert analysed["latest_successful_description_sha256"] == full_hash
    assert analysed["successful_description_sha256s"] == [full_hash]
    assert targets[("mention", "101")]["analysis_observation_status"] == (
        "not_observed_in_selected_window"
    )
    assert targets[("quote-tweet", "102")]["correlation_status"] == (
        "analysis_observed_collection_not_observed_in_selected_window"
    )
    assert targets[("mention", "103")]["correlation_status"] == (
        "collection_unavailable"
    )
    assert summary["targets_with_supplied_native_photos"] == 2
    assert summary["targets_with_successful_visual_analysis"] == 1
    assert summary[
        "targets_with_no_analysis_event_observed_in_selected_window"
    ] == 1
    assert summary[
        "targets_with_analysis_but_no_collection_observation_in_selected_window"
    ] == 1
    assert summary[
        "targets_with_visual_events_but_no_collection_observation_in_selected_window"
    ] == 1

    rendered = digest.render_markdown(report)
    assert "no visual-analysis event observed in the selected window" in rendered
    assert "no collection observation in the selected window" in rendered
    assert full_hash[:12] in rendered
    assert full_hash not in rendered
    assert full_hash in json.dumps(report)


@pytest.mark.parametrize(
    ("status", "supplied_image_count"),
    [("paused", 1), ("invalid_supplied_media", 0)],
)
def test_reply_visual_zero_call_without_collection_is_a_lifecycle_event_only(
    status,
    supplied_image_count,
):
    report = digest.analyse(
        [
            reply_visual_record(
                0,
                reply_visual_payload(
                    "zero-call",
                    status=status,
                    supplied_image_count=supplied_image_count,
                ),
            ),
            reply_visual_record(
                1,
                reply_visual_payload(
                    "one-call",
                    status="provider_error",
                    supplied_image_count=1,
                ),
            ),
        ]
    )
    by_target = {
        row["target_id"]: row for row in report["reply_visual_context_targets"]
    }
    zero_call = by_target["zero-call"]
    one_call = by_target["one-call"]
    summary = report["reply_visual_context_summary"]

    assert zero_call["analysis_observation_status"] == "not_attempted"
    assert zero_call["visual_analysis_event_count"] == 1
    assert zero_call["visual_analysis_attempt_count"] == 0
    assert zero_call["correlation_status"] == (
        "visual_event_observed_collection_not_observed_in_selected_window"
    )
    assert one_call["visual_analysis_attempt_count"] == 1
    assert one_call["correlation_status"] == (
        "analysis_observed_collection_not_observed_in_selected_window"
    )
    assert summary[
        "targets_with_visual_events_but_no_collection_observation_in_selected_window"
    ] == 2
    assert summary[
        "targets_with_analysis_but_no_collection_observation_in_selected_window"
    ] == 1


def test_reply_visual_failure_statuses_and_repeated_hashes_are_objective():
    records = [
        reply_visual_record(
            0,
            reply_visual_payload(
                "200", status="provider_error", supplied_image_count=1
            ),
        ),
        reply_visual_record(
            1,
            reply_visual_payload(
                "201", status="invalid_response", supplied_image_count=1
            ),
        ),
        reply_visual_record(
            2,
            reply_visual_payload(
                "202", status="invalid_supplied_media", supplied_image_count=0
            ),
        ),
        reply_visual_record(
            3,
            message=(
                "Reply media context lane=mention target_id=203 photos=1 "
                "mode=multimodal status=supplied"
            ),
        ),
        reply_visual_record(
            4,
            reply_visual_payload(
                "203", status="paused", supplied_image_count=1
            ),
        ),
        reply_visual_record(
            5, reply_visual_payload("204", description_sha256="5" * 64)
        ),
        reply_visual_record(
            6, reply_visual_payload("204", description_sha256="5" * 64)
        ),
        reply_visual_record(
            7, reply_visual_payload("205", description_sha256="6" * 64)
        ),
        reply_visual_record(
            8, reply_visual_payload("205", description_sha256="7" * 64)
        ),
    ]

    report = digest.analyse(records)
    by_target = {
        row["target_id"]: row for row in report["reply_visual_context_targets"]
    }
    summary = report["reply_visual_context_summary"]

    for target_id, status in {
        "200": "provider_error",
        "201": "invalid_response",
    }.items():
        assert by_target[target_id]["latest_visual_analysis_status"] == status
        assert by_target[target_id]["successful_analysis_count"] == 0
        assert by_target[target_id]["analysis_observation_status"] == (
            "attempted_not_analysed"
        )
        assert by_target[target_id]["visual_analysis_event_count"] == 1
        assert by_target[target_id]["visual_analysis_attempt_count"] == 1
    for target_id, status in {
        "202": "invalid_supplied_media",
        "203": "paused",
    }.items():
        assert by_target[target_id]["latest_visual_analysis_status"] == status
        assert by_target[target_id]["successful_analysis_count"] == 0
        assert by_target[target_id]["analysis_observation_status"] == (
            "not_attempted"
        )
        assert by_target[target_id]["visual_analysis_event_count"] == 1
        assert by_target[target_id]["visual_analysis_attempt_count"] == 0
    assert by_target["203"]["correlation_status"] == (
        "collection_supplied_analysis_not_attempted"
    )
    assert by_target["204"]["visual_analysis_attempt_count"] == 2
    assert by_target["204"]["successful_analysis_count"] == 2
    assert by_target["204"]["distinct_successful_description_count"] == 1
    assert by_target["205"]["visual_analysis_attempt_count"] == 2
    assert by_target["205"]["distinct_successful_description_count"] == 2
    assert summary["targets_with_attempted_but_unsuccessful_analysis"] == 2
    assert summary["targets_with_visual_events_but_no_analysis_call"] == 2
    assert summary["targets_with_more_than_one_analysis_attempt"] == 2
    assert summary[
        "targets_with_more_than_one_distinct_successful_description_hash"
    ] == 1
    assert summary["visual_analysis_status_counts"] == {
        "analysed": 4,
        "invalid_response": 1,
        "invalid_supplied_media": 1,
        "paused": 1,
        "provider_error": 1,
    }
    assert summary["visual_analysis_event_count"] == 8
    assert summary["visual_analysis_attempt_count"] == 6

    rendered = digest.render_markdown(report)
    paused_row = next(
        line
        for line in rendered.splitlines()
        if "| 203 |" in line and "| paused |" in line
    )
    invalid_media_row = next(
        line
        for line in rendered.splitlines()
        if "| 202 |" in line and "| invalid_supplied_media |" in line
    )
    assert "| visual events | analysis calls |" in rendered
    assert "| paused | 1 | 0 |" in paused_row
    assert "| invalid_supplied_media | 1 | 0 |" in invalid_media_row


def test_reply_visual_malformed_shapes_fail_safely_without_raw_material():
    secret_description = "PRIVATE DESCRIPTION MUST NOT LEAK"
    secret_ocr = "PRIVATE OCR MUST NOT LEAK"
    secret_url = "https://private.invalid/image.jpg"
    invalid_json_secret = "INVALID JSON VISUAL DESCRIPTION"
    malformed = [
        reply_visual_payload("300", supplied_image_count=True),
        reply_visual_payload("300", visual_analysis_call_count=True),
        reply_visual_payload("300", description_sha256="A" * 64),
        reply_visual_payload(
            "300",
            image_url=secret_url,
            visual_description=secret_description,
            ocr_text=secret_ocr,
            media_key="private-media-key",
            prompt="private prompt",
        ),
    ]
    records = [
        reply_visual_record(
            0,
            message=(
                "Reply media context lane=mention target_id=300 photos=1 "
                "mode=multimodal status=supplied"
            ),
        ),
        *[
            reply_visual_record(index + 1, payload)
            for index, payload in enumerate(malformed)
        ],
        reply_visual_record(
            5,
            message=(
                "EVENT {\"event\":\"reply_visual_description\","
                f"\"visual_description\":\"{invalid_json_secret}\""
            ),
            level="ERROR",
        ),
    ]

    report = digest.analyse(records)
    rendered_json = json.dumps(report)
    rendered_markdown = digest.render_markdown(report)

    assert report["reply_visual_context_summary"][
        "malformed_visual_description_event_count"
    ] == 5
    assert report["reply_visual_context_summary"]["visual_analysis_event_count"] == 0
    assert report["reply_visual_context_targets"][0][
        "analysis_observation_status"
    ] == "not_observed_in_selected_window"
    for forbidden in (
        secret_description,
        secret_ocr,
        secret_url,
        invalid_json_secret,
        "private-media-key",
        "private prompt",
    ):
        assert forbidden not in rendered_json
        assert forbidden not in rendered_markdown


def test_reply_visual_events_do_not_change_provider_or_pipeline_call_accounting():
    stage_payload = {
        "event": "ai_reply_pipeline_stage_summary",
        "lane": "mention",
        "target_id": "400",
        "status": "approved",
        "strategy_version": "tested-reply-pipeline-20260817",
        "model_call_count": 2,
        "revision_count": 0,
    }
    base_records = [
        reply_visual_record(
            0,
            message=(
                "Tested reply stage=proposer provider=xAI usage="
                "{'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}"
            ),
        ),
        reply_visual_record(1, stage_payload),
    ]
    visual_records = [
        reply_visual_record(
            2,
            message=(
                "Reply media context lane=mention target_id=400 photos=1 "
                "mode=multimodal status=supplied"
            ),
        ),
        reply_visual_record(
            3,
            reply_visual_payload(
                "400", supplied_image_count=1, description_sha256="8" * 64
            ),
        ),
    ]

    without_visual = digest.analyse(base_records)
    with_visual = digest.analyse([*base_records, *visual_records])

    assert with_visual["provider_usage"] == without_visual["provider_usage"]
    assert with_visual["reply_pipeline_stages"] == without_visual[
        "reply_pipeline_stages"
    ]
    stage = next(
        event
        for event in with_visual["events"]
        if event["kind"] == "reply_pipeline_stage_summary"
    )
    assert stage["model_call_count"] == 2
    assert with_visual["reply_visual_context_summary"][
        "visual_analysis_call_count"
    ] == 1
    assert with_visual["reply_visual_context_summary"][
        "visual_analysis_attempt_count"
    ] == 1
