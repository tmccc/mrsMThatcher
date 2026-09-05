"""Historical event projection, truncation and publication-boundary regressions."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import mrs_log_digest as digest


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
