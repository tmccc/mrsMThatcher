"""Historical event projection, truncation and publication-boundary regressions."""

from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys

import pytest

import historical_context_formatter as formatter
import mrs_log_digest as digest

from tests.helpers.digest_records import event


BASE = datetime(2026, 9, 4, 12)
FAMILIES = (
    "historical_context_semantic_gate",
    "historical_context_runtime",
    "historical_context_reply",
    "historical_context_obligation",
    "historical_context_outbox",
)
DIMENSIONS = {
    "attribution": "high", "wording": "medium", "source_event": "low",
    "date": "unknown", "historical_context": "unavailable", "interpretation": "high",
}


def _record(offset, payload, *, selftest=False):
    return digest.Record(
        BASE + timedelta(seconds=offset), "INFO", "log_event", offset + 10,
        "EVENT " + (payload if isinstance(payload, str) else json.dumps(payload)),
        "mrsMThatcher.selftest.log" if selftest else "mrsMThatcher.log", offset + 1,
    )


def _reply(status="completed", **fields):
    return {
        "event": "historical_context_reply", "status": status,
        "parent_post_id": "123", "quote_id": "a" * 64,
        "character_count": 180, "raw_character_count": 200,
        "verification_label": "Exact wording verified", "source_class": "Hansard",
        "historical_confidence": "high", "confidence_dimensions": DIMENSIONS,
        "formatter_version": "historical_context_reply_schema_v5",
        "source_role_audit_version": "historical-context-source-roles-v9-archive-provenance",
        "rendering_mode": "public", "template_variant": "compact_context",
        "shortening_applied": True, "meaning_omitted": False,
        "source_omitted": True, "verification_omitted": False,
        "semantic_review_disposition": "approved",
        "semantic_review_ledger_sha256": "b" * 64,
        "semantic_review_projection_sha256": "c" * 64,
        "reply_preview": "Context — A synthetic occasion.\nMeaning — Synthetic context.",
        **fields,
    }


def _confirmation(**fields):
    return {
        "event": "historical_context_reply_posted", "event_version": 1,
        "lane": "historical_context_reply", "parent_post_id": "123",
        "root_post_id": "123", "conversation_id": "123", "reply_post_id": "456",
        "quote_id": "a" * 64, "publication_authority": "confirmed_transport",
        "reply_text": "Context — Exact confirmed text.\n\nMeaning — Preserve spacing.",
        **fields,
    }


def _synthetic_records():
    records = []

    def add(payload, **kwargs):
        records.append(_record(len(records), payload, **kwargs))

    for selftest in (False, True):
        add({"event": FAMILIES[0], "status": "loaded", "policy_version": "gate-v1",
             "ledger_sha256": "b" * 64, "projection_sha256": "c" * 64,
             "blocked_quote_count": 1_000_000}, selftest=selftest)
        add({"event": FAMILIES[1], "status": "ready",
             "regular_post_eligibility_unchanged": True}, selftest=selftest)
        for version, status in zip(range(1, 6), (
            "dry_run", "completed", "already_completed", "failed", "skipped_future_policy",
        )):
            add(_reply(status, formatter_version=f"historical_context_reply_schema_v{version}",
                       verification_label="Reported in a memoir; no primary Thatcher transcript located",
                       reason="synthetic reason", parent_post_id=str(120 + version)), selftest=selftest)
            add({"event": "reply_evidence_unavailable", "lane": "mention_reply", "target_id": "321"})
        add({"event": "posting_transaction_state", "parent_post_id": "123",
             "context_reply_state": "pending", "context_state_persisted": True})
        add({"event": FAMILIES[3], "status": "retry", "context_reply_state": "pending",
             "parent_post_id": "123", "attempt_number": 1_000_000,
             "remote_work_repeated": False}, selftest=selftest)
        add({"event": FAMILIES[4], "status": "failed", "parent_post_id": "123",
             "main_post_success_preserved": True, "unrelated_lanes_available": False}, selftest=selftest)

    for family in FAMILIES:
        add({"event": family})
        add({"event": family, "status": None, "reason": None, "parent_post_id": 123,
             "character_count": True, "raw_character_count": 25_001,
             "attempt_number": -1, "blocked_quote_count": False, "quote_id": "A" * 64,
             "confidence_dimensions": {"date": "high"}, "shortening_applied": 1,
             "remote_work_repeated": "false", "main_post_success_preserved": 0})
        add({"event": family, "status": {"nested": "failed"}, "reason": ["nested"],
             "attempt_number": 1.0, "blocked_quote_count": 1_000_001,
             "regular_post_eligibility_unchanged": 1, "character_count": "180",
             "raw_character_count": -1, "confidence_dimensions": [], "reply_preview": 123})
        add({"event": family, "status": "future_" + "x" * 90, "reason": "r" * 1000,
             "unknown_field": {"ignored": True}, "parent_post_id": "0" * 31,
             "character_count": 0, "raw_character_count": 25_000, "attempt_number": 0})
        add({"event": family, "status": "x" * 101, "reason": "r" * 1001,
             "character_count": 25_001, "raw_character_count": 1.0,
             "source_class": "s" * 501, "formatter_version": "f" * 201})
    add(_reply())
    add(_confirmation())
    add(_reply("already_completed", parent_post_id="223", character_count=True))
    add(_confirmation(parent_post_id="223", root_post_id="999", reply_post_id="556"))
    add(_reply(parent_post_id="323"), selftest=True)
    add(_confirmation(parent_post_id="323", root_post_id="323", conversation_id="323"), selftest=True)
    for fragment in (
        '{"event":"historical_context_reply","status":"completed","status":"dry_run"}',
        'junk {"event":"historical_context_reply","status":"completed"}',
        '{"event":"historical_context_reply","character_count":NaN}',
        '{"event":"historical_context_outbox","reason":"\\ud800"}',
    ):
        add(fragment)
    return records


def _analyse(records, max_text=280, **kwargs):
    return digest.analyse(
        records, max_text=max_text, generation_time=BASE + timedelta(hours=1),
        input_file_indexes={"mrsMThatcher.log": 0, "mrsMThatcher.selftest.log": 1},
        **kwargs,
    )


@pytest.mark.parametrize("max_text,reply_count", [(0, 19), (7, 19), (80, 18), (280, 18), (25_000, 18)])
def test_interleaved_events_keep_counts_provenance_and_independent_analysis(max_text, reply_count):
    records = _synthetic_records()
    original = deepcopy(records)
    report = _analyse(records, max_text)
    counts = report["summary"]["stats"]
    emitted = Counter(event["kind"] for event in report["events"])
    for family in FAMILIES:
        assert counts[family] == emitted[family]
    replies = report["historical_context_replies"]["events"]
    assert report["historical_context_replies"]["status_counts"] == {
        "already_completed": 3, "completed": 4, "dry_run": 2, "failed": 2,
        "future_" + "x" * 90: 1, "skipped_future_policy": 2, "unknown": 4,
    }
    # With short identity fields, existing correlation adds a confirmed public
    # reply to this section without changing the structured family counters.
    assert len(replies) == reply_count
    assert counts["historical_context_reply"] == 18
    assert sum(item["kind"] == "confirmed_public_reply" for item in replies) == reply_count - 18
    assert [item["kind"] for item in report["events"][:15]] == [
        FAMILIES[0], FAMILIES[1],
        *[kind for _ in range(5) for kind in (FAMILIES[2], "reply_evidence_unavailable")],
        "posting_transaction_state", FAMILIES[3], FAMILIES[4],
    ]
    assert all(any(event is reply for event in report["events"]) for reply in replies)
    assert {ref["input_file_index"] for reply in replies for ref in reply["source_refs"]} == {0, 1}
    assert records == original
    # A different invocation must not carry counters or metadata into a replay.
    assert _analyse([])["historical_context_replies"]["events"] == []
    assert _analyse(records, max_text) == report
    assert digest.render_markdown(_analyse(records, max_text)) == digest.render_markdown(report)


def test_quality_consumes_emitted_fields_but_family_counts_precede_truncation(monkeypatch):
    observed = []
    summary = digest.historical_context_quality_summary

    def capture(events):
        observed.extend(events)
        return summary(events)

    monkeypatch.setattr(digest, "historical_context_quality_summary", capture)
    report = _analyse([_record(0, _reply()), _record(1, _reply("skipped_future_policy", reason="long skip reason"))], 12)
    replies = report["historical_context_replies"]["events"]
    assert all(any(item is reply for item in observed) for reply in replies)
    assert replies[0]["verification_label"] == "Exact wordi…"
    assert report["historical_context_quality"]["verification_counts"]["unavailable"] == 1
    assert report["historical_context_quality"]["skip_reason_counts"] == {"long skip r…": 1}
    assert report["historical_context_replies"]["status_counts"] == {"completed": 1, "skipped_future_policy": 1}


@pytest.mark.parametrize("fields", [
    {"character_count": True}, {"character_count": None}, {"character_count": 25_001},
    {"parent_post_id": 123}, {"quote_id": "A" * 64}, {"reply_preview": None},
])
@pytest.mark.parametrize("status", ["completed", "already_completed"])
def test_displayable_completed_metadata_cannot_activate_durable_text(fields, status):
    evidence = {
        "time": "2026-09-04 12:00:00", "parent_post_id": "123", "reply_post_id": "456",
        "quote_id": "a" * 64, "authoritative": True, "durable_only": True,
        "reply_text": "Durable exact text requires a canonical production anchor.",
        "source": "historical_context_reply_history.json", "source_refs": [],
    }
    report = _analyse([_record(0, _reply(status, **fields))], historical_history_evidence=[evidence])
    reply = report["historical_context_replies"]["events"][0]
    assert reply["status"] == status
    assert reply["public_reply_text_status"] == "unavailable"
    assert reply["reply_post_id"] is None
    assert evidence["reply_text"] not in json.dumps(report)
    assert report["api_health"]["observed_remote_write_success_count"] == 0


def test_confirmed_text_enriches_the_original_emitted_reply():
    report = _analyse([_record(0, _reply()), _record(1, _confirmation())])
    reply = report["historical_context_replies"]["events"][0]
    assert next(item for item in report["events"] if item["kind"] == FAMILIES[2]) is reply
    assert reply["public_reply_text"] == _confirmation()["reply_text"]
    assert reply["public_reply_text_sha256"] == hashlib.sha256(reply["public_reply_text"].encode()).hexdigest()
    assert reply["reply_post_id"] == "456"
    assert reply["source_refs"][0]["record_number"] == 1
    assert report["api_health"]["observed_remote_write_success_count"] == 1


def test_resumed_analysis_retains_original_record_provenance():
    records = _synthetic_records()
    first = _analyse(records[:15])
    tail = [digest.record_fingerprint(record) for record in records[:15]]
    assert digest.locate_resume_fingerprint_tail(records, tail) == (15, 15)
    initial = {
        "initial_" + key: first["resume_context"][key]
        for key in ("active_xai_context", "active_xai_call_attempt", "pending_mention", "pending_qt")
    }
    resumed = _analyse(records[15:], **initial)
    independent = _analyse(records[15:])
    assert resumed == independent
    assert digest.render_markdown(resumed) == digest.render_markdown(independent)
    reply = resumed["historical_context_replies"]["events"][0]
    assert reply["source_refs"][0] == {
        "input_file_index": 1, "record_number": 18,
        "timestamp": "2026-09-04 12:00:17", "logger": "log_event",
        "logged_source_line_number": 27,
    }


def test_historical_module_import_has_no_io_or_upward_dependencies(tmp_path):
    script = """
import builtins
import logging
import os
from pathlib import Path
import sys

handlers = list(logging.getLogger().handlers)
loggers = set(logging.Logger.manager.loggerDict)
original_import = builtins.__import__
forbidden = {"mrs_log_digest", "mrs_log_digest_markdown", "mrsMThatcher2",
             "mrs_log_digest_runtime", "mrs_log_digest_corpus", "mrs_log_digest_generated_pool"}
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
for name in ("expanduser", "stat", "lstat", "exists", "is_file", "is_dir", "open", "iterdir", "glob", "rglob"):
    setattr(Path, name, reject)
os.stat = os.lstat = logging.basicConfig = reject
builtins.__import__ = import_guard
sys.addaudithook(audit)
import mrs_log_digest_historical_events as historical
assert historical.historical_context_quality_summary([])["attempted_count"] == 0
assert list(logging.getLogger().handlers) == handlers
assert set(logging.Logger.manager.loggerDict) == loggers
assert not forbidden & sys.modules.keys()
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script], cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(Path(digest.__file__).resolve().parent)),
        capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == b""
    assert list(tmp_path.iterdir()) == []


def test_historical_rendering_summary_and_empty_semantic_columns():
    events = [
        {
            "kind": "historical_context_reply",
            "status": "completed",
            "template_variant": "compact_with_meaning",
            "reply_preview": "Context — Speech at Chelsea, 19 September 1975.\\n\\nMeaning — X",
        },
        {
            "kind": "historical_context_reply",
            "status": "completed",
            "template_variant": "compact_with_meaning",
            "reply_preview": "Context — The surviving record dates this wording to 5 June 1987, but does not establish its occasion.",
        },
        {
            "kind": "historical_context_reply",
            "status": "completed",
            "template_variant": "compact_generic_context_omitted",
            "reply_preview": "Meaning — X",
        },
        {
            "kind": "historical_context_reply",
            "status": "completed",
            "reply_preview": "Context — The surviving attribution does not establish an occasion, date or immediate historical issue.",
        },
    ]
    summary = digest.historical_context_quality_summary(events)
    assert summary["rendering_context_counts"] == {
        "concrete_event_or_date_context_included": 1,
        "date_only_qualified_context_included": 1,
        "context_omitted_no_useful_event_or_date": 1,
        "old_generic_fallback_used": 1,
        "rendering_metadata_unavailable": 0,
    }
    report = digest.analyse([])
    report["events"] = events
    report["historical_context_quality"] = summary
    rendered = digest.render_markdown(report)
    historical_table = rendered.split("## Historical context replies", 1)[1]
    historical_header = historical_table.splitlines()[1]
    assert "overall_reply_confidence" in historical_header
    assert "historical_confidence" not in historical_header
    assert "semantic_review_disposition" not in historical_table
    assert "empty columns are omitted" in historical_table


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
