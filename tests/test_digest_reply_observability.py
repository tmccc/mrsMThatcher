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
