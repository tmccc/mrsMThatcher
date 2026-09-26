"""Frozen selected-section output and phase boundaries from the pre-typing digest."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest

import mrs_log_digest as digest
from mrs_log_digest_analysis import DigestCurrentSnapshots, DigestInputSelection
from mrs_log_digest_contracts import RuntimeConfigSnapshot, RuntimeStateSnapshot
from mrs_log_digest_records import ResumeWindowSelection
from tests.helpers.digest_records import _normal_main_post_record, record, structured_record


BASELINE = Path(__file__).parent / "fixtures" / "digest_sections_baseline.json"
SECTIONS = (
    "summary", "resume_context", "mention_backlog_and_quarantine",
    "main_post_recovery", "confirmed_reply_recovery", "structured_event_diagnostics",
)
NOW = datetime(2026, 9, 26, 12)


def _mixed_records() -> list[digest.Record]:
    """Supply known, confirmed, unknown and malformed evidence with fixed sources."""
    return [
        _normal_main_post_record(),
        record(1, "WARNING", "write_sending_reply_receipt",
               "Wrote conversational reply sending receipt source=mention target_id=123 path=/tmp/reply.json"),
        record(2, "WARNING", "promote_sending_reply_receipt",
               "Promoted conversational reply receipt to confirmed source=mention target_id=123 reply_post_id=999 path=/tmp/reply.json"),
        structured_record(3, {"event": "future_event"}),
        structured_record(4, {"event": 7}),
    ]


@pytest.mark.parametrize("name,records", [("empty", []), ("mixed", _mixed_records())])
def test_selected_sections_match_pre_typing_values_and_identity(name, records):
    """Compare all selected keys and nested values without normalising evidence."""
    expected = json.loads(BASELINE.read_text(encoding="utf-8"))[name]
    report = digest.analyse(records, generation_time=NOW)
    assert {section: report[section] for section in SECTIONS} == expected
    for section in SECTIONS:
        assert digest.report_section(report, section) is report[section]
    assert json.loads(json.dumps(report, ensure_ascii=False, allow_nan=False))["summary"] == expected["summary"]
    assert digest.render_markdown(report, complete_report=report) == digest.render_markdown(report)
    assert "runtime_state_status" not in report
    assert "runtime_config_status" not in report
    assert "provider_request_coverage" not in report
    assert report["single_call_reply"]["cost_total"] == {
        "status": "unavailable_during_log_analysis", "amount": None,
    }


@pytest.mark.parametrize("status", ["absent", "available"])
def test_overlay_only_sections_appear_at_their_existing_phases(tmp_path, monkeypatch, status):
    """Current provenance and optional costs are absent until their overlays."""
    report = digest.analyse([], generation_time=NOW)
    selected = ResumeWindowSelection([], "timestamp", 0, False)
    inputs = DigestInputSelection([], None, None, None, False, Counter(), {}, [], [], selected)
    snapshots = DigestCurrentSnapshots(
        RuntimeStateSnapshot({"daily_reply_count": 0}) if status == "available" else None,
        tmp_path / "bot_state.json", None, status, NOW,
        RuntimeConfigSnapshot({"MAX_AUTO_REPLIES_PER_DAY": "1"}) if status == "available" else None,
        tmp_path / "mrsMThatcher.local.json", None, status,
        [], [], {}, {},
    )
    args = argparse.Namespace(no_state=True, reset_state=False, verbose_replies=False,
                              detailed_appendix=False, request_record_dir=tmp_path / "requests")
    digest.overlay_current_runtime(report, inputs, snapshots, args, tmp_path, tmp_path / "cursor.json", NOW)
    assert report["runtime_state_status"] == {
        "status": status, "path": str(tmp_path / "bot_state.json"), "observed_at": digest.dt_text(NOW),
    }
    assert report["runtime_config_status"] == {
        "status": status, "path": str(tmp_path / "mrsMThatcher.local.json"), "time": None,
    }
    assert "provider_request_coverage" not in report
    assert report["single_call_reply"]["cost_total"] == {
        "status": "unavailable_during_log_analysis", "amount": None,
    }

    monkeypatch.setattr(digest, "OPENAI_COST_CACHE_PATH", tmp_path / "absent-cost-cache.json")
    digest.add_provider_and_cost_evidence(report, inputs, args, tmp_path, NOW)
    assert report["provider_request_coverage"] == {
        "logical_call_denominator": 0,
        "physical_attempt_denominator": 0,
        "category_counts": {"historical_not_recorded": 0},
        "historical_not_recorded_calls": [],
    }
    assert report["single_call_reply"]["cost_total"] == {
        "status": "unknown", "amount": None, "scope": None, "method": None,
    }
    assert digest.render_markdown(report, complete_report=report) == digest.render_markdown(report)


def test_empty_window_restored_resume_context_keeps_nullable_fields():
    """No selected records still publishes restored pending context verbatim."""
    pending = {"considered_seq": 3, "nested": {"source": "saved"}}
    report = digest.analyse([], generation_time=NOW, initial_pending_mention=pending)
    assert report["summary"]["record_count"] == 0
    assert report["resume_context"] == {
        "active_xai_context": None, "active_xai_call_attempt": None,
        "pending_mention": pending, "pending_qt": None,
    }
