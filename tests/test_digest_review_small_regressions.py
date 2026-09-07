"""Synthetic current readiness, cursor and media-observation regressions."""
from datetime import datetime

import pytest

import mrs_log_digest as digest
import mrs_log_digest_transactions as transactions


@pytest.mark.parametrize("kind, expected", [
    ("source_receipt", 0), ("media_receipt", 0),
    ("transport_journal", 0), ("ambiguity_marker", 1),
])
def test_current_blocking_component_incident_projection(kind, expected):
    now = datetime(2026, 9, 7, 12)
    safety = {
        "configured": True, "available": True, "identity_snapshot_available": True,
        "blocking": True, "status": "blocked", "observed_at": digest.dt_text(now),
        "protocol": {"valid": True}, "control": {"valid": True},
        "active_entries": [], "snapshot_incident_evidence": [],
        "active_transaction_identities": [{
            "artifact_kinds": [kind], "artifact_names": [kind + ".json"],
            "transaction_ids": ["synthetic-transaction"], "lanes": ["mention"],
            "transaction_states": ["sending"], "document_sha256s": ["a" * 64],
            "receipt_roles": ["conversational_confirmed_reply"] if kind == "source_receipt" else [],
        }],
    }
    report = digest.summarise_operational_error_health(
        [], [], [], generation_time=now, selected_window_end=now,
        current_remote_write_safety=safety, current_snapshot_authoritative=True,
    )
    assert report["current_independent_incident_count"] == expected
    if kind in {"media_receipt", "transport_journal"}:
        safety["media" if kind == "media_receipt" else "transport"] = {
            "blocking": True, "classification": "sending",
            "transaction_id": "synthetic-transaction", "lane": "mention",
        }
    report = digest.analyse([], current_remote_write_safety=safety,
                            current_snapshot_authoritative=True)
    report["remote_write_safety"] = safety
    markdown = digest.render_markdown(report)
    assert "remote-write safety: BLOCKED" in report["summary"]["headline"]
    assert "Current remote writes are **blocked**" in markdown
    if kind in {"media_receipt", "transport_journal"}:
        assert "Active logical remote-write safety components:" in markdown
        assert f"{kind} / sending" in markdown
        assert "active_safety_components = 1" in markdown
        assert "source_marker_entries    = 0" in markdown
    if expected == 0:
        assert "current health: remote writes blocked" in report["summary"]["headline"]
        assert "without themselves establishing an operational incident" in markdown


def test_resume_temp_symlink_cannot_overwrite_synthetic_sentinel(tmp_path):
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("keep me")
    cursor = tmp_path / "resume.json"
    cursor.with_suffix(".json.tmp").symlink_to(sentinel)
    digest.save_resume_time(cursor, datetime(2026, 9, 7), [], {}, [])
    assert sentinel.read_text() == "keep me"
    assert not cursor.is_symlink()
    assert "last_log_entry_time" in cursor.read_text()


def test_execution_lock_rejects_symlink_without_overwriting_target(tmp_path):
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("keep me")
    lock = tmp_path / "resume.json.lock"
    lock.symlink_to(sentinel)
    with pytest.raises(OSError):
        with digest.digest_execution_lock(lock):
            pytest.fail("lock symlink was accepted")
    assert sentinel.read_text() == "keep me"


@pytest.mark.parametrize("request_time", ["2026-09-07 20:00:00", ""])
def test_media_post_result_labels_unrelated_create_as_uncorrelated(request_time):
    incidents, errors = transactions.prepare_media_incidents_and_errors(
        records=[], max_text=280, input_file_indexes=None,
        remote_write_transactions=[{
            "kind": "media_upload", "phase": "ambiguous", "time": "2026-09-07 12:00:00",
            "transaction_id": "media-A", "image": "synthetic.jpg",
        }],
        x_requests=[{
            "endpoint": "tweet/create", "time": request_time,
            "transaction_id": "reply-B", "lane": "mention",
        }],
        current_remote_write_safety=None, errors=[], self_test_errors=[],
        self_test_times=set(), api_error_times=set(), handled_restriction_times=[],
        correlate_media_upload_incidents=lambda *args: ([], set()),
        parse_dt=digest.parse_dt, strptime=datetime.strptime,
        seconds_between=digest.seconds_between,
    )
    assert errors == []
    assert incidents[0]["status"] == "blocked"
    assert incidents[0]["post_result"] == "uncorrelated tweet-create request observed in window"


def test_resume_read_is_bounded_nofollow_and_fail_open(tmp_path, capsys):
    cursor = tmp_path / "resume.json"
    cursor.write_text("{}")
    sentinel = tmp_path / "sentinel"
    cursor.rename(sentinel)
    cursor.symlink_to(sentinel)
    assert digest.read_resume_data(cursor) == {}
    assert "could not read state file" in capsys.readouterr().err
    cursor.unlink()
    with cursor.open("wb") as handle:
        handle.truncate(8 * 1024 * 1024 + 1)
    assert digest.read_resume_data(cursor) == {}
    assert "could not read state file" in capsys.readouterr().err
    cursor.write_text("corrupt")
    assert digest.read_resume_data(cursor) == {}
    cursor.unlink()
    assert digest.read_resume_data(cursor) == {}


def test_outer_protocol_and_transaction_diagnoses_survive_handled_403():
    raw = 'X API error 403: {"detail":"You attempted to reply to a Tweet that is deleted or not visible to you."}'
    for outer, category in [
        ("remote-write protocol is not activated", "remote_write_protocol_barrier"),
        ("transport journal cannot be inspected", "remote_write_transaction_barrier"),
    ]:
        message = outer + "\nTraceback (most recent call last):\nApiError: " + raw
        assert digest.classify_operational_error(message) == category
        row = digest.Record(datetime(2026, 9, 7, 12), "CRITICAL", "worker", 1, message, "synthetic.log", 1)
        report = digest.analyse([row])
        assert len(report["errors_and_warnings"]) == 1


def test_markdown_discloses_failed_durable_evidence_without_confirmation_rows():
    report = digest.analyse([], durable_reply_evidence_status={
        "confirmed_reply_receipt": {"available": False, "status": "unavailable", "reason": "synthetic unreadable receipt"},
        "historical_context_reply_history": {"available": False, "status": "absent"},
    })
    markdown = digest.render_markdown(report)
    assert "Durable reply evidence unavailable" in markdown
    assert "synthetic unreadable receipt" in markdown
    assert "historical_context_reply_history | absent" not in markdown
