import json
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime

import historical_context_formatter as formatter
import mrs_log_digest as digest


def event(kind, **values):
    return {"kind": kind, "time": "2026-07-15 12:00:00", **values}


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


def test_current_source_role_version_is_reported():
    version = "historical-context-source-roles-v7-curated-source-adjudications"
    result = digest.historical_context_quality_summary([
        event(
            "historical_context_reply",
            status="completed",
            character_count=100,
            verification_label="Attributed, but exact wording not independently verified",
            source_role_audit_version=version,
        )
    ])

    assert result["source_role_audit_version_counts"][version] == 1
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
    assert strategy["confidence_counts"]["unavailable"] == 1
    assert strategy["posted_grounded_count"] == 1
    assert strategy["generated_average_evidence_reference_count"] == 1
    assert strategy["average_evidence_reference_count"] == 1
    assert strategy["generated_average_retrieved_packet_count"] is None
    assert strategy["average_retrieved_packet_count"] is None
    assert decisions[0]["evidence_reference_count"] == 1
    assert outcomes[0]["evidence_reference_count"] == 1
    assert decisions[0]["reviewer_verdict"] == "approve"
    assert outcomes[0]["reviewer_verdict"] == "approve"
    rendered = digest.render_markdown(report)
    assert "ai-first-reply-v2" in rendered
    assert "direct_factual_answer" in rendered
    assert "AI-first evidence references" in rendered
    assert "Generated tones:" in rendered
    assert "Published/terminal tones:" in rendered
    assert "humour tones" not in rendered


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
        "successful_xai_calls": 1,
        "prompt_tokens": 1779,
        "cached_tokens": 192,
        "image_tokens": 0,
        "reasoning_tokens": 706,
        "completion_tokens": 123,
        "total_tokens": 2608,
        "sources_used": 0,
        "cost_in_usd_ticks": 40946500,
    }
    assert report["xai_usage"]["events"][0]["lane"] == "mention"
    assert report["xai_usage"]["events"][0]["context_id"] == "123"


def test_ai_first_event_without_strategy_version_is_not_mislabelled_v2():
    record = digest.Record(
        ts=datetime(2026, 7, 20, 12),
        level="INFO",
        src="log_event",
        line=1,
        msg=(
            'EVENT {"event":"ai_reply_pipeline_decision","lane":"mention",'
            '"target_id":"100","mode":"no_reply","tone":"neutral",'
            '"factual_claim_count":0,"evidence_ids":[],"reason":"not_warranted"}'
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
            '"model_call_count":2,"revision_count":0}'
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
    assert "Target-eligibility 403 responses: **2**" in rendered
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
