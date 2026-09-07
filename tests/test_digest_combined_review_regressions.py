"""Synthetic regressions for resumed reply evidence and current digest reporting."""
import copy
import json
from datetime import datetime, timedelta

import pytest
import mrs_log_digest as digest

START = datetime(2026, 9, 7, 12)
TEXT = "PUBLIC-EXACT " + "A complete synthetic sentence. " * 12 + "PUBLIC-END"


def record(offset, message, level="INFO", source="log_event"):
    return digest.Record(START + timedelta(seconds=offset), level, source, 1,
                         message, "mrsMThatcher.log", offset + 1)


def event(offset, name, **values):
    return record(offset, "EVENT " + json.dumps({"event": name, **values}))


def write_log(path, records):
    path.write_text("".join(
        f"{r.ts:%Y-%m-%d %H:%M:%S} {r.level:<8} {r.src}:1 - {r.msg}\n"
        for r in records), encoding="utf-8")


def runtime_text(text=TEXT):
    return {"ai_reply_history": [{"candidate_source": "mention", "target_id": "101",
                                 "reply_post_id": "901", "proposed_reply": text}]}


@pytest.mark.parametrize("lane", ["mention", "hot_post_reply", "quote_tweet"])
@pytest.mark.parametrize("current", [False, True], ids=["legacy", "current"])
def test_resume_preserves_reply_detail_and_single_publication(tmp_path, lane, current):
    quote = lane == "quote_tweet"
    description = "quote tweet" if quote else "mention"
    considering = (
        "Considering quote tweet id=101 author_id=201 original_post_id=301 text='input'"
        if quote else f"Considering {lane} id=101 author_id=201 text='input'"
    )
    generated = (
        f"Generated validated reply to {description} 101 character_count=5 utf8_byte_count=5 sha256="
        + "a" * 64 if current else f"Generated reply to {description} 101: 'draft'"
    )
    prefix = [record(0, considering, source="maybe_reply_to_mentions"),
              record(1, generated, source="_log_validated_single_call_reply")]
    suffix = []
    if current:
        suffix.append(event(2, "single_call_reply_posting_outcome", status="confirmed",
                            lane=lane, target_id="101", reply_post_id="901",
                            strategy_version="single-sol-reply-20260904"))
    suffix += [
        record(3, "Recorded and cached own " + ("quote-tweet " if quote else "")
               + "auto-reply id=901"),
    ]
    if current:
        suffix.append(event(4, "reply_posted", lane=lane, target_id="101",
                            reply_post_id="901", author_id="201",
                            **({"original_post_id": "301"} if quote else {})))
    suffix.append(record(5, ("Quote-tweet reply" if quote else "Reply")
                         + " posted successfully"))
    first = digest.analyse(prefix)
    key = "pending_qt" if quote else "pending_mention"
    assert first["resume_context"][key]
    restored = digest.analyse(suffix, **{
        "initial_" + key: first["resume_context"][key],
        "initial_active_xai_context": first["resume_context"]["active_xai_context"],
    })
    full = digest.analyse(prefix + suffix)
    kind = {"mention": "mention_reply_posted", "hot_post_reply": "hot_post_reply_posted",
            "quote_tweet": "quote_tweet_reply_posted"}[lane]
    assert sum(e["kind"] == kind for e in full["events"]) == 1
    assert sum(e["kind"] == kind for e in restored["events"]) == 1
    confirmations = sum(e["kind"] == "confirmed_public_reply" for e in restored["events"])
    assert confirmations == 0
    posted = restored["single_call_reply"]["replies_posted_count"]
    assert posted == int(current)
    if current:
        assert "Single-call posting outcomes" in digest.render_markdown(restored)
    if current and lane == "mention":
        # One actual two-invocation CLI check of persisted resume state.
        log = tmp_path / "mrsMThatcher.log"
        output = tmp_path / "report.json"
        args = ["--project-dir", str(tmp_path), "--state-file", ".resume.json",
                "--json", "--output", str(output), str(log)]
        write_log(log, prefix)
        assert digest.main(args) == 0
        saved = json.loads((tmp_path / ".resume.json").read_text())
        assert saved.get("last_pending_mention")
        write_log(log, prefix + suffix)
        assert digest.main(args) == 0
        resumed = json.loads(output.read_text())
        assert resumed["resume_cursor_mode"] == "fingerprint_tail"
        assert sum(e["kind"] == kind for e in resumed["events"]) == 1
        assert resumed["single_call_reply"]["replies_posted_count"] == 1


def test_chained_403_keeps_outer_ambiguity_and_current_outcome(tmp_path):
    raw = ('X API error 403: {"detail":"You attempted to reply to a Tweet '
           'that is deleted or not visible to you."}')
    critical = (
        "mention reply stopped after an ambiguous remote outcome; the global "
        "remote-write safety barrier remains active\n"
        "Traceback (most recent call last):\nApiError: " + raw + "\n\n"
        "The above exception was the direct cause of the following exception:\n\n"
        "Traceback (most recent call last):\n"
        "AmbiguousRemotePostOutcome: Conversational reply received a post-transmission "
        "error whose remote-create outcome is unproved; its sending receipt remains"
    )
    records = [record(0, raw, "ERROR", "x_request"),
               record(1, critical, "CRITICAL", "maybe_reply_to_mentions"),
               event(2, "single_call_reply_posting_outcome", lane="mention", target_id="101",
                     status="posting_failed_retryable", failure_reason="ambiguous_remote_outcome")]
    write_log(tmp_path / "chained-403.log", records)
    report = digest.analyse(records)
    assert digest.classify_operational_error(critical) == "remote_write_ambiguity_barrier"
    assert report["error_health"]["current_independent_incident_count"] == 1
    assert len(report["errors_and_warnings"]) == 1
    assert not digest.analyse([records[0]])["errors_and_warnings"]
    assert report["single_call_reply"]["posting_failure_count"] == 1
    assert report["single_call_reply"]["operational_failure_count"] == 0
    assert any(e["kind"] == "single_call_reply_posting_outcome" for e in report["events"])
    markdown = digest.render_markdown(report)
    assert "ambiguous_remote_outcome" in markdown


def test_unexpected_snapshot_failure_discloses_unknown_health(tmp_path, monkeypatch):
    log = tmp_path / "mrsMThatcher.log"
    write_log(log, [record(0, "Synthetic quiet window")])
    (tmp_path / "ambiguous_post_outcome.restart_barrier.json").write_text("{}")
    output, markdown = tmp_path / "report.json", tmp_path / "report.md"
    def fail(_path):
        raise PermissionError("synthetic snapshot failure")
    monkeypatch.setattr(digest, "runtime_control_snapshot", fail)
    assert digest.main(["--project-dir", str(tmp_path), "--no-state", "--json",
                        "--output", str(output), "--markdown-output", str(markdown),
                        str(log)]) == 0
    report = json.loads(output.read_text())
    headline = report["summary"]["headline"]
    assert report["remote_write_safety"]["status"] == "inspection_failed"
    assert report["remote_write_safety"]["blocking"] is None
    assert "current health: remote-write safety unknown" in headline
    assert "UNKNOWN / unavailable" in headline
    assert report["error_health"]["current_independent_incident_count"] == 0
    assert "Current remote-write safety is **unknown / unavailable**" in markdown.read_text()
    assert "None unresolved in the selected window." not in markdown.read_text()


def test_markdown_and_json_load_the_same_bounded_durable_evidence(tmp_path, monkeypatch):
    # Receipt uses a prevalidated projection; historical history is a real synthetic file.
    receipt = {"lane": "mention", "target_id": "101", "reply_post_id": "901",
               "reply_text": TEXT, "reply_epoch": 1788692400}
    calls, reports = [], []
    def receipt_loader(path):
        calls.append("receipt")
        return [receipt], {"available": True, "status": "available"}
    history_path = tmp_path / "historical_context_reply_history.json"
    history_path.write_bytes(digest.canonical_private_json_bytes({
        "schema_version": 1,
        "items": {"301": {
            "status": "completed", "schema_version": 1, "parent_post_id": "301",
            "reply_post_id": "902", "quote_id": "a" * 64,
            "reply_text": "HISTORICAL-EXACT", "reply_epoch": 1788778800,
            "confirmed_at": "2026-09-07T11:00:00Z",
        }},
    }))
    history_path.chmod(0o600)
    real_history_loader = digest.load_historical_reply_history_evidence
    def history_loader(path):
        calls.append("history")
        rows, status = real_history_loader(path)
        assert status["available"] is True
        return rows, status
    original = digest.analyse
    def analyse(*args, **kwargs):
        report = original(*args, **kwargs)
        reports.append(copy.deepcopy(report))
        return report
    monkeypatch.setattr(digest, "load_confirmed_reply_receipt_evidence", receipt_loader)
    monkeypatch.setattr(digest, "load_historical_reply_history_evidence", history_loader)
    monkeypatch.setattr(digest, "analyse", analyse)
    log = tmp_path / "mrsMThatcher.log"
    write_log(log, [event(0, "historical_context_reply", status="completed",
                         parent_post_id="301", quote_id="a" * 64, character_count=16)])
    base = ["--project-dir", str(tmp_path), "--no-state", "--output",
            str(tmp_path / "report.md"), str(log)]
    assert digest.main(base) == 0
    assert calls == ["receipt", "history"]
    calls.clear()
    assert digest.main(base + ["--markdown-output", str(tmp_path / "also.md")]) == 0
    assert calls == ["receipt", "history"]
    calls.clear()
    assert digest.main(base + ["--json-output", str(tmp_path / "report.json")]) == 0
    assert calls == ["receipt", "history"]
    before = reports[0]
    after = reports[-1]
    assert any(e["kind"] == "confirmed_public_reply" for e in before["events"])
    receipt_row = next(e for e in after["events"] if e.get("reply_post_id") == "901")
    assert receipt_row["public_reply_text"] == TEXT
    assert "independent" in receipt_row["evidence_scope"]
    old_history = next(e for e in before["events"] if e["kind"] == "historical_context_reply")
    new_history = next(e for e in after["events"] if e["kind"] == "historical_context_reply")
    assert old_history["public_reply_text"] == "HISTORICAL-EXACT"
    assert new_history["public_reply_text"] == "HISTORICAL-EXACT"
    markdown = (tmp_path / "report.md").read_text()
    assert "PUBLIC-EXACT" in markdown and TEXT not in markdown
    assert "HISTORICAL-EXACT" in markdown and "902" in markdown
    assert "independent of the selected log window" in markdown
    assert after["summary"]["stats"].get("mention_reply_posted", 0) == 0


def test_markdown_prefers_confirmed_text_and_displays_conflicts():
    records = [
        record(0, "Considering mention id=101 author_id=201 text='input'"),
        record(1, "Generated reply to mention 101: 'LEGACY-DRAFT " + "x" * 300 + " DRAFT-END'"),
        record(2, "Recorded and cached own auto-reply id=901"),
        event(3, "reply_posted", lane="mention", target_id="101", reply_post_id="901"),
        record(4, "Reply posted successfully"),
    ]
    report = digest.analyse(records, current_runtime_state=runtime_text())
    row = next(e for e in report["events"] if e["kind"] == "mention_reply_posted")
    assert row["public_reply_text"] == TEXT
    markdown = digest.render_markdown(report)
    assert "LEGACY-DRAFT" not in markdown and "DRAFT-END" not in markdown
    assert "PUBLIC-EXACT" in markdown and "PUBLIC-END" not in markdown
    report["verbose_replies"] = True
    assert TEXT in digest.render_markdown(report)
    conflict = digest.analyse(
        records, current_runtime_state=runtime_text(),
        confirmed_receipt_evidence=[{"lane": "mention", "target_id": "101",
                                    "reply_post_id": "901", "reply_text": "DIFFERENT",
                                    "reply_epoch": 1788692400}])
    assert conflict["published_reply_text_health"]["conflict_count"] == 1
    assert "authoritative text sources disagree" in digest.render_markdown(conflict)
    assert "LEGACY-DRAFT" not in digest.render_markdown(conflict)
