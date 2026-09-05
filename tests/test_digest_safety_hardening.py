from __future__ import annotations

import json
import os
import time
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

import mrs_log_digest as digest
import remote_write_safety_protocol as remote_protocol
from tests.helpers.protocol_activation import create_test_protocol_activation
from tests.test_generated_image_pool_health_digest import pool
from tests.test_generated_image_pool_runway_digest import log_line, post


NOW = datetime(2026, 7, 10, 12)


@pytest.fixture
def london_local_time():
    """Run a test with deterministic Europe/London process-local time."""
    original = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/London"
    time.tzset()
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()


def project_with_log(tmp_path: Path) -> tuple[Path, Path]:
    project, names = pool(tmp_path, 2)
    log = project / "mrsMThatcher.log"
    log.write_text(post(NOW - timedelta(minutes=2), "123", names[0]))
    return project, log


def main_args(project: Path, log: Path, *extra: str) -> list[str]:
    return ["--project-dir", str(project), "--state-file", ".resume.json", "--since", "2026-07-10 00:00:00", *extra, str(log)]


def publish_readonly_json(path: Path, value: dict) -> bytes:
    """Publish one deterministic mode-0400 JSON fixture."""

    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(data)
    path.chmod(0o400)
    return data


def test_stable_snapshot_uses_current_identity_callback(tmp_path, monkeypatch):
    path = tmp_path / "observation.json"
    raw = b'{"value":1}'
    path.write_bytes(raw)
    original = digest._stable_file_identity
    calls = []

    for invocation in range(2):
        def identity(metadata, *, invocation=invocation):
            calls.append((invocation, metadata))
            return original(metadata)

        monkeypatch.setattr(digest, "_stable_file_identity", identity)
        data, metadata = digest.read_stable_regular_snapshot(path, maximum=len(raw))
        assert data == raw
        observed = calls[invocation * 6:]
        assert len(observed) == 6
        assert all(index == invocation for index, _value in observed)
        assert observed[2][1] is observed[0][1]
        assert observed[3][1] is observed[4][1] is metadata

    failure = OSError("identity callback failed")

    def fail(metadata):
        raise failure

    monkeypatch.setattr(digest, "_stable_file_identity", fail)
    with pytest.raises(OSError) as caught:
        digest.read_stable_regular_snapshot(path, maximum=len(raw))
    assert caught.value is failure


@pytest.mark.parametrize("reader_name,private", [
    ("read_stable_regular_bytes", False),
    ("read_stable_private_json_bytes", True),
])
def test_byte_readers_use_current_snapshot_callback(tmp_path, monkeypatch, reader_name, private):
    path = tmp_path / "observation.json"
    path.write_bytes(b"{}")
    path.chmod(0o600)
    metadata = path.stat()
    calls = []
    reader = getattr(digest, reader_name)
    expected = {"maximum": 7, **({"require_private": True} if private else {})}

    for raw in (b"first", b"second"):
        def snapshot(requested, *, raw=raw, **kwargs):
            calls.append((requested, kwargs))
            return raw, metadata

        monkeypatch.setattr(digest, "read_stable_regular_snapshot", snapshot)
        assert reader(path, maximum=7) is raw
    assert calls == [(path, expected), (path, expected)]

    failure = RuntimeError("snapshot callback failed")

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(digest, "read_stable_regular_snapshot", fail)
    with pytest.raises(RuntimeError) as caught:
        reader(path, maximum=7)
    assert caught.value is failure


def test_native_object_uses_current_value_parser_and_keeps_root_check(monkeypatch):
    raw = b'{"value":1}'
    calls = []
    for result in ({"shared": []}, {"replacement": True}, [1]):
        def parse(data, *, label, result=result):
            calls.append((data, label))
            return result

        monkeypatch.setattr(digest, "_strict_native_json_value", parse)
        if isinstance(result, dict):
            assert digest._strict_native_json_object(raw, label="fixture") is result
        else:
            with pytest.raises(ValueError, match="^fixture root is not an object$"):
                digest._strict_native_json_object(raw, label="fixture")
    assert calls == [(raw, "fixture")] * 3

    failure = ValueError("parser callback failed")

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(digest, "_strict_native_json_value", fail)
    with pytest.raises(ValueError) as caught:
        digest._strict_native_json_object(raw, label="fixture")
    assert caught.value is failure


def test_runtime_control_snapshot_is_strict_and_fail_closed(tmp_path):
    path = tmp_path / "mrsMThatcher.control.json"
    path.write_text('{"disable_all":true,"generation":3}', encoding="utf-8")

    valid = digest.runtime_control_snapshot(tmp_path)

    assert valid["valid"] is True
    assert valid["generation"] == 3
    assert valid["active_keys"] == ["disable_all"]
    assert valid["global_pause_active"] is True

    path.write_text(
        '{"disable_all_until":1.0000000000000000000000000000000001}',
        encoding="utf-8",
    )
    fractional = digest.runtime_control_snapshot(tmp_path)
    assert fractional["valid"] is False
    assert fractional["global_pause_active"] is True
    assert fractional["active_keys"] == ["fail_closed_invalid_control"]

    path.write_text(
        '{"disable_all":true,"disable_all":false}',
        encoding="utf-8",
    )
    duplicate = digest.runtime_control_snapshot(tmp_path)
    assert duplicate["valid"] is False
    assert duplicate["global_pause_active"] is True


def test_remote_write_snapshot_reports_protocol_pause_and_active_marker(tmp_path):
    create_test_protocol_activation(tmp_path / remote_protocol.ACTIVATION_BASENAME)
    (tmp_path / "mrsMThatcher.control.json").write_text(
        '{"disable_all":true,"generation":3}',
        encoding="utf-8",
    )

    paused = digest.remote_write_safety_snapshot(tmp_path)

    assert paused["status"] == "operator_paused"
    assert paused["blocking"] is False
    assert paused["ready_for_remote_writes"] is False
    assert paused["protocol"]["valid"] is True
    assert len(paused["retirement_ledgers"]) == 4
    assert all(
        item["valid"] is True and item["blocking"] is False
        for item in paused["retirement_ledgers"]
    )
    assert paused["transport"]["classification"] == "clear"
    assert paused["media"]["classification"] == "clear"

    marker = tmp_path / digest.REMOTE_WRITE_MARKER_BASENAMES[0]
    marker.write_text('{"fixture":true}', encoding="utf-8")
    blocked = digest.remote_write_safety_snapshot(tmp_path)

    assert blocked["status"] == "blocked"
    assert blocked["blocking"] is True
    assert blocked["ready_for_remote_writes"] is False
    assert blocked["active_marker_names"] == [marker.name]
    assert blocked["reconciliation_proven"] is False


def test_remote_write_wrapper_keeps_path_clock_callback_and_read_order(tmp_path, monkeypatch):
    events = []
    control = {"present": True, "valid": True, "global_pause_active": False}
    archive = {"present": False}
    marker = tmp_path / digest.REMOTE_WRITE_MARKER_BASENAMES[0]
    marker.write_bytes(b'{"target_id":1.0000000000000000000000000001,"recorded_at_epoch":2.0}')
    original_reader = digest.read_stable_regular_bytes
    original_parser = digest._strict_json_object

    class ProjectPath:
        def __fspath__(self):
            events.append("path")
            return str(tmp_path)

    class SnapshotTime(datetime):
        @classmethod
        def now(cls):
            events.append("now")
            return NOW

    def control_snapshot(path):
        events.append("control")
        assert isinstance(path, Path) and path == tmp_path
        return control

    def archive_snapshot(path):
        events.append("archive")
        assert isinstance(path, Path) and path == tmp_path
        return archive

    def read_bytes(path, *, maximum):
        events.append("read")
        assert path == marker
        assert maximum == 256 * 1024
        return original_reader(path, maximum=maximum)

    def parse(data, *, label):
        events.append("parse")
        assert label == marker.name
        value = original_parser(data, label=label)
        assert value["target_id"] == Decimal("1.0000000000000000000000000001")
        assert type(value["recorded_at_epoch"]) is Decimal
        return value

    monkeypatch.setattr(digest, "datetime", SnapshotTime)
    monkeypatch.setattr(digest, "runtime_control_snapshot", control_snapshot)
    monkeypatch.setattr(digest, "reconciliation_archive_snapshot", archive_snapshot)
    monkeypatch.setattr(digest, "read_stable_regular_bytes", read_bytes)
    monkeypatch.setattr(digest, "_strict_json_object", parse)

    result = digest.remote_write_safety_snapshot(ProjectPath())

    assert events == ["path", "now", "control", "archive", "read", "parse"]
    assert result["observed_at"] == "2026-07-10 12:00:00"
    assert result["control"] is control
    assert result["reconciliation_archive"] is archive
    assert result["active_entries"][0]["target_id"] == ""
    assert result["active_entries"][0]["recorded_at_epoch"] is None
    assert result["status"] == "blocked"


@pytest.mark.parametrize("callback", ["runtime_control_snapshot", "reconciliation_archive_snapshot"])
def test_remote_write_wrapper_preserves_snapshot_callback_failure(tmp_path, monkeypatch, callback):
    failure = PermissionError("synthetic snapshot failure")

    def fail(path):
        assert path == tmp_path
        raise failure

    monkeypatch.setattr(digest, callback, fail)
    with pytest.raises(PermissionError) as caught:
        digest.remote_write_safety_snapshot(tmp_path)
    assert caught.value is failure


def test_archive_wrapper_keeps_private_reader_parser_and_diagnostic_callbacks(tmp_path, monkeypatch):
    archive = tmp_path / digest.REMOTE_WRITE_ARCHIVE_BASENAME
    archive.mkdir()
    audit = archive / "marker.reconciliation.json"
    marker_data = b"malformed marker"
    marker_relative = f"{archive.name}/marker.json"
    reference = f"{archive.name}/resolution.json"
    publish_readonly_json(audit, {
        "schema_version": 3,
        "operation": "offline_remote_write_safety_marker_archive",
        "archived_at_epoch": 2_000_000_001,
        "archive_path": marker_relative,
        "marker_sha256": digest.hashlib.sha256(marker_data).hexdigest(),
        "reconciliation_reference": reference,
        "archive_and_receipt_durable_before_source_removal": True,
        "restart_barrier_retired_last": True,
        "successful_return_requires_source_absent": True,
        "successful_return_requires_all_active_barriers_absent": True,
        "fixture_ratio": 1.5,
    })
    events = []
    original_reader = digest.read_stable_regular_bytes
    original_parser = digest._strict_json_object
    failure = PermissionError("synthetic reference failure")
    errors = []

    def read_bytes(path, *, maximum):
        events.append(("read", path.name))
        assert path == audit and maximum == 256 * 1024
        return original_reader(path, maximum=maximum)

    def read_archive(path, value):
        events.append(("archive", value))
        assert path == tmp_path
        if value == marker_relative:
            return marker_data
        assert value == reference
        raise failure

    def parse(data, *, label):
        events.append(("parse", label))
        value = original_parser(data, label=label)
        assert type(value["fixture_ratio"]) is Decimal
        return value

    def diagnostic(exc):
        events.append(("diagnostic", type(exc).__name__))
        errors.append(exc)
        return f"patched {type(exc).__name__}"

    monkeypatch.setattr(digest, "read_stable_regular_bytes", read_bytes)
    monkeypatch.setattr(digest, "_read_readonly_archive_bytes", read_archive)
    monkeypatch.setattr(digest, "_strict_json_object", parse)
    monkeypatch.setattr(digest, "reconciliation_inspection_error", diagnostic)

    result = digest.reconciliation_archive_snapshot(str(tmp_path))

    assert result["valid"] is True
    row = result["latest_marker_reconciliation"]
    assert row["identity_error"] == "patched JSONDecodeError"
    assert row["reference_identity_error"] == "patched PermissionError"
    assert errors[1] is failure
    assert events == [
        ("read", audit.name), ("parse", audit.name),
        ("archive", marker_relative), ("parse", marker_relative),
        ("diagnostic", "JSONDecodeError"), ("archive", reference),
        ("diagnostic", "PermissionError"),
    ]


def test_private_archive_wrapper_keeps_stable_reader_and_permission_failure(tmp_path, monkeypatch):
    archive = tmp_path / digest.REMOTE_WRITE_ARCHIVE_BASENAME
    archive.mkdir()
    path = archive / "marker.json"
    data = publish_readonly_json(path, {"fixture": True})
    relative = str(path.relative_to(tmp_path))
    calls = []
    failure = OSError("synthetic stable read failure")

    def read_bytes(requested, *, maximum):
        calls.append(requested)
        assert requested == path and maximum == 256 * 1024
        if len(calls) == 1:
            return data
        raise failure

    monkeypatch.setattr(digest, "read_stable_regular_bytes", read_bytes)
    assert digest._read_readonly_archive_bytes(tmp_path, relative) is data
    with pytest.raises(OSError) as caught:
        digest._read_readonly_archive_bytes(tmp_path, relative)
    assert caught.value is failure
    path.chmod(0o600)
    with pytest.raises(ValueError, match="^archive is not a mode-0400 regular file$"):
        digest._read_readonly_archive_bytes(tmp_path, relative)
    assert calls == [path, path]


def test_reconciliation_archive_requires_readonly_hash_bound_evidence(tmp_path):
    archive = tmp_path / digest.REMOTE_WRITE_ARCHIVE_BASENAME
    archive.mkdir()
    marker_hash = "2" * 64
    marker_path = archive / f"ambiguous_post_outcome.{marker_hash}.json"
    marker_data = b'{"incident":"fixture"}'
    marker_path.write_bytes(marker_data)
    marker_path.chmod(0o400)
    marker_hash = digest.hashlib.sha256(marker_data).hexdigest()
    renamed_marker_path = archive / f"ambiguous_post_outcome.{marker_hash}.json"
    marker_path.rename(renamed_marker_path)

    transaction_id = "4" * 64
    receipt_data = b'{"receipt":"fixture"}'
    fence_data = b'{"fence":"fixture"}'
    receipt_hash = digest.hashlib.sha256(receipt_data).hexdigest()
    fence_hash = digest.hashlib.sha256(fence_data).hexdigest()
    receipt_path = archive / (
        f"unattached_media_upload.{transaction_id}.{receipt_hash}.receipt.json"
    )
    fence_path = archive / (
        f"unattached_media_upload.{transaction_id}.{fence_hash}.fence.json"
    )
    receipt_path.write_bytes(receipt_data)
    fence_path.write_bytes(fence_data)
    receipt_path.chmod(0o400)
    fence_path.chmod(0o400)

    media_audit_path = archive / (
        f"unattached_media_upload.{transaction_id}.reconciliation.json"
    )
    publish_readonly_json(
        media_audit_path,
        {
            "schema_version": 1,
            "operation": "offline_unattached_media_upload_archive",
            "archived_at_epoch": 2_000_000_000,
            "accepted_media_disposition": "unattached_and_abandoned",
            "media_transaction_id": transaction_id,
            "image_basename": "t64.jpg",
            "marker_sha256": marker_hash,
            "receipt_archive_path": str(receipt_path.relative_to(tmp_path)),
            "receipt_sha256": receipt_hash,
            "fence_archive_path": str(fence_path.relative_to(tmp_path)),
            "fence_sha256": fence_hash,
            "no_tweet_create_authority_present": True,
            "operator_confirmed_no_tweet_create_attempted": True,
            "operator_confirmed_unattached_media_abandoned": True,
            "remote_media_id_absent": True,
            "media_archives_and_audit_durable_before_active_removal": True,
            "media_receipt_retired_before_fence": True,
            "active_marker_preserved_after_media_reconciliation": True,
            "successful_return_requires_active_media_pair_absent": True,
        },
    )
    marker_audit_path = archive / (
        f"ambiguous_post_outcome.{marker_hash}.json.reconciliation.json"
    )
    publish_readonly_json(
        marker_audit_path,
        {
            "schema_version": 3,
            "operation": "offline_remote_write_safety_marker_archive",
            "archived_at_epoch": 2_000_000_001,
            "archive_path": str(renamed_marker_path.relative_to(tmp_path)),
            "marker_sha256": marker_hash,
            "reconciliation_reference": str(media_audit_path.relative_to(tmp_path)),
            "archive_and_receipt_durable_before_source_removal": True,
            "restart_barrier_retired_last": True,
            "successful_return_requires_source_absent": True,
            "successful_return_requires_all_active_barriers_absent": True,
        },
    )

    valid = digest.reconciliation_archive_snapshot(tmp_path)

    assert valid["valid"] is True
    assert valid["valid_marker_reconciliation_count"] == 1
    assert valid["valid_media_reconciliation_count"] == 1
    assert valid["latest_media_reconciliation"]["image_basename"] == "t64.jpg"

    receipt_path.chmod(0o600)
    invalid = digest.reconciliation_archive_snapshot(tmp_path)
    assert invalid["valid"] is False
    assert invalid["valid_media_reconciliation_count"] == 0
    assert "mode-0400" in " ".join(invalid["invalid_audits"])


@pytest.mark.parametrize("target", ["pool", "rates", "markdown", "stdout", "interrupt"])
def test_digest_failures_do_not_advance_resume(tmp_path, monkeypatch, target):
    project, log = project_with_log(tmp_path)
    state = project / ".resume.json"
    state.write_text('{"last_log_entry_time":"2026-07-09 00:00:00"}\n')
    before = state.read_bytes()
    if target == "pool":
        monkeypatch.setattr(digest, "generated_pool_health_snapshot", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("pool failure")))
    elif target == "rates":
        monkeypatch.setattr(digest, "generated_post_rate_history", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("rate failure")))
    elif target == "markdown":
        monkeypatch.setattr(digest, "render_markdown", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("render failure")))
    elif target == "stdout":
        monkeypatch.setattr(digest, "deliver_report", lambda *_a, **_k: (_ for _ in ()).throw(OSError("stdout failure")))
    else:
        monkeypatch.setattr(digest, "deliver_report", lambda *_a, **_k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises((RuntimeError, OSError, KeyboardInterrupt)):
        digest.main(main_args(project, log))
    assert state.read_bytes() == before


def test_json_serialisation_failure_does_not_advance_resume(tmp_path, monkeypatch):
    project, log = project_with_log(tmp_path)
    state = project / ".resume.json"
    state.write_text('{"last_log_entry_time":"2026-07-09 00:00:00"}\n')
    before = state.read_bytes()
    original = digest.json.dumps
    monkeypatch.setattr(digest.json, "dumps", lambda *_a, **_k: (_ for _ in ()).throw(TypeError("json failure")))
    with pytest.raises(TypeError, match="json failure"):
        digest.main(main_args(project, log, "--json"))
    monkeypatch.setattr(digest.json, "dumps", original)
    assert state.read_bytes() == before


def test_output_file_failure_does_not_advance_resume(tmp_path, monkeypatch):
    project, log = project_with_log(tmp_path)
    state = project / ".resume.json"
    state.write_text('{"last_log_entry_time":"2026-07-09 00:00:00"}\n')
    before = state.read_bytes()
    monkeypatch.setattr(digest.os, "replace", lambda *_a, **_k: (_ for _ in ()).throw(OSError("replace failure")))
    with pytest.raises(OSError, match="replace failure"):
        digest.main(main_args(project, log, "--output", str(tmp_path / "report.md")))
    assert state.read_bytes() == before


def test_output_cannot_alias_input_log(tmp_path):
    project, log = project_with_log(tmp_path)
    before = log.read_bytes()

    with pytest.raises(SystemExit, match="output path aliases an input log"):
        digest.main(main_args(project, log, "--no-state", "--output", str(log)))

    assert log.read_bytes() == before


def test_output_cannot_alias_resume_state(tmp_path):
    project, log = project_with_log(tmp_path)
    state = project / ".resume.json"
    state.write_text('{"last_log_entry_time":"2026-07-09 00:00:00"}\n')
    before = state.read_bytes()

    with pytest.raises(SystemExit, match="output path aliases the resume-state file"):
        digest.main(main_args(project, log, "--output", str(state)))

    assert state.read_bytes() == before


def test_adjacent_output_names_do_not_alias_temporary_files(tmp_path):
    project, log = project_with_log(tmp_path)
    primary = tmp_path / "report.md.tmp"
    markdown = tmp_path / "report.md"

    assert digest.main(main_args(
        project,
        log,
        "--no-state",
        "--output",
        str(primary),
        "--markdown-output",
        str(markdown),
    )) == 0

    assert primary.is_file()
    assert markdown.is_file()
    assert "# MrsMThatcher log digest" in primary.read_text(encoding="utf-8")
    assert "# MrsMThatcher log digest" in markdown.read_text(encoding="utf-8")


def test_successful_output_advances_resume_once(tmp_path, monkeypatch):
    project, log = project_with_log(tmp_path)
    calls = []
    original = digest.save_resume_time
    monkeypatch.setattr(digest, "save_resume_time", lambda *a, **k: (calls.append(1), original(*a, **k))[1])
    output = tmp_path / "report.md"
    assert digest.main(main_args(project, log, "--output", str(output))) == 0
    assert output.exists() and "# MrsMThatcher log digest" in output.read_text()
    assert len(calls) == 1 and (project / ".resume.json").exists()


def test_legacy_provider_resume_clears_without_restoring_old_usage_report(tmp_path):
    project, _names = pool(tmp_path, 2)
    log = project / "mrsMThatcher.log"
    state = project / ".resume.json"
    first_json = project / "first.json"
    second_json = project / "second.json"

    def row(ts: datetime, source: str, message: str) -> str:
        return (
            f"{ts.strftime('%Y-%m-%d %H:%M:%S')} INFO     "
            f"{source}:1 - {message}\n"
        )

    start = datetime(2026, 7, 28, 14)
    log.write_text(
        row(
            start,
            "maybe_reply_to_mentions",
            "Considering mention id=505 author_id=606 text='fixture'",
        )
        + row(
            start + timedelta(seconds=1),
            "xai_structured_reply_call",
            "Calling AI-first reply stage=proposer model=grok-4.3",
        ),
        encoding="utf-8",
    )
    common_args = [
        "--project-dir", str(project),
        "--state-file", state.name,
        str(log),
    ]

    assert digest.main([
        *common_args,
        "--output", str(project / "first.md"),
        "--json-output", str(first_json),
    ]) == 0
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["last_active_xai_call_attempt"] == {
        "time": "2026-07-28 14:00:01",
        "lane": "mention",
        "context_id": "505",
        "author_id": "606",
        "stage": "proposer",
        "model": "grok-4.3",
        "usage_observed": False,
    }

    with log.open("a", encoding="utf-8") as handle:
        handle.write(
            row(
                start + timedelta(seconds=2),
                "xai_structured_reply_call",
                "xAI reply stage=proposer usage={'total_tokens': 100, "
                "'cost_in_usd_ticks': 10000000}",
            )
            + row(
                start + timedelta(seconds=3),
                "log_event",
                'EVENT {"event":"ai_reply_pipeline_decision","lane":"mention",'
                '"target_id":"505","status":"no_reply","mode":"no_reply",'
                '"model_call_count":1,"reason":"not_warranted"}',
            )
        )

    assert digest.main([
        *common_args,
        "--output", str(project / "second.md"),
        "--json-output", str(second_json),
    ]) == 0
    report = json.loads(second_json.read_text(encoding="utf-8"))
    assert "xai_usage" not in report
    assert "provider_usage" not in report
    assert report["legacy_multi_stage"] == {
        "decision_count": 1,
        "stage_summary_event_count": 0,
    }
    assert (
        json.loads(state.read_text(encoding="utf-8"))[
            "last_active_xai_call_attempt"
        ]
        is None
    )


def test_invalid_persisted_resume_timestamp_warns_and_recovers(tmp_path, capsys):
    project, log = project_with_log(tmp_path)
    state = project / ".resume.json"
    state.write_text('{"last_log_entry_time":"not-a-time"}\n', encoding="utf-8")
    output = tmp_path / "report.md"

    assert digest.main([
        "--project-dir", str(project),
        "--state-file", str(state),
        "--output", str(output),
        str(log),
    ]) == 0

    assert "ignoring invalid resume timestamp" in capsys.readouterr().err
    assert "# MrsMThatcher log digest" in output.read_text(encoding="utf-8")
    assert digest.parse_dt(json.loads(state.read_text(encoding="utf-8"))["last_log_entry_time"])


def test_invalid_manual_datetime_remains_a_cli_error(tmp_path):
    project, log = project_with_log(tmp_path)

    with pytest.raises(SystemExit, match="Could not parse datetime"):
        digest.main([
            "--project-dir", str(project),
            "--no-state",
            "--since", "not-a-time",
            str(log),
        ])


def test_project_dir_is_explicit_from_foreign_cwd(tmp_path, monkeypatch):
    project, log = project_with_log(tmp_path)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    monkeypatch.chdir(foreign)
    output = tmp_path / "foreign.md"
    digest.main(main_args(project, log, "--no-state", "--output", str(output)))
    text = output.read_text()
    assert f"Project directory: `{project.resolve()}`" in text
    assert "active_generated_images       = 2" in text


def test_default_project_dir_is_script_directory(monkeypatch):
    captured = {}
    monkeypatch.setattr(digest, "discover_logs", lambda directory, _pattern: (captured.setdefault("directory", directory), [Path(__file__)])[1])
    monkeypatch.setattr(digest, "read_records", lambda *_a, **_k: [])
    monkeypatch.setattr(digest, "summarize_input_files", lambda *_a, **_k: [])
    monkeypatch.setattr(digest, "generated_pool_health_snapshot", lambda base: {"snapshot_base_dir": str(base)})
    monkeypatch.setattr(digest, "generated_post_rate_history", lambda *_a, **_k: {"windows": {}})
    monkeypatch.setattr(digest, "deliver_report", lambda *_a, **_k: None)
    digest.main(["--no-state"])
    assert captured["directory"] == Path(digest.__file__).resolve().parent


def test_default_log_discovery_excludes_selftest_logs(tmp_path):
    primary_names = {
        "mrsMThatcher.log",
        "mrsMThatcher.log.1",
        "mrsMThatcher.log.2",
        "mrsMThatcher.log.5",
        "mrsMThatcher.log.9",
        "mrsMThatcher.log.10",
        "mrsMThatcher.log.11",
        "mrsMThatcher.log.99",
        "mrsMThatcher.log.100",
    }
    selftest = tmp_path / "mrsMThatcher.selftest.log"
    for name in (*primary_names, selftest.name):
        (tmp_path / name).write_text("", encoding="utf-8")

    discovered = digest.discover_logs(tmp_path, "mrsMThatcher*.log*")

    assert {path.name for path in discovered} == primary_names
    assert len(discovered) == 9
    assert {"mrsMThatcher.log.10", "mrsMThatcher.log.99", "mrsMThatcher.log.100"} <= {
        path.name for path in discovered
    }
    assert selftest not in discovered


def test_repeated_identical_records_in_one_log_are_preserved(tmp_path: Path) -> None:
    path = tmp_path / "mrsMThatcher.log"
    line = "2026-07-10 12:00:00 INFO     worker:9 - identical event\n"
    path.write_text(line + line, encoding="utf-8")

    records = digest.read_records([path], None, None)

    assert len(records) == 2
    assert [record.ordinal for record in records] == [1, 2]


def test_overlapping_rotations_preserve_maximum_occurrence_cardinality(tmp_path: Path) -> None:
    current = tmp_path / "mrsMThatcher.log"
    rotation = tmp_path / "mrsMThatcher.log.1"
    line = "2026-07-10 12:00:00 INFO     worker:9 - identical event\n"
    current.write_text(line + line, encoding="utf-8")
    rotation.write_text(line, encoding="utf-8")

    records = digest.read_records([current, rotation], None, None)

    assert len(records) == 2
    assert all(record.path == str(current) for record in records)


def test_physical_record_order_preserves_clock_rollback_append_order(tmp_path: Path) -> None:
    path = tmp_path / "mrsMThatcher.log"
    path.write_text(
        "2026-10-25 01:59:50 ERROR    worker:9 - before fallback\n"
        "2026-10-25 01:00:10 ERROR    worker:9 - after fallback\n",
        encoding="utf-8",
    )

    records = digest.read_records([path], None, None, physical_order=True)

    assert [record.msg for record in records] == ["before fallback", "after fallback"]


def test_physical_record_order_uses_numeric_three_digit_rotation_suffixes(
    tmp_path: Path,
) -> None:
    physical_names = [
        "mrsMThatcher.log.100",
        "mrsMThatcher.log.99",
        "mrsMThatcher.log.10",
        "mrsMThatcher.log.2",
        "mrsMThatcher.log.1",
        "mrsMThatcher.log",
    ]
    identical_mtime_ns = 1_700_000_000_000_000_000
    for index, name in enumerate(physical_names):
        path = tmp_path / name
        path.write_text(
            log_line(
                NOW + timedelta(seconds=len(physical_names) - index),
                f"physical record from {name}",
            ),
            encoding="utf-8",
        )
        os.utime(path, ns=(identical_mtime_ns, identical_mtime_ns))

    paths = digest.discover_logs(tmp_path, "mrsMThatcher*.log*")
    records = digest.read_records(paths, None, None, physical_order=True)

    assert len({path.stat().st_mtime_ns for path in paths}) == 1
    assert [Path(record.path).name for record in records] == physical_names
    assert [record.msg for record in records] == [
        f"physical record from {name}" for name in physical_names
    ]


def test_active_plus_one_hundred_rotations_are_all_discovered_and_read(
    tmp_path: Path,
) -> None:
    for rotation in range(101):
        name = "mrsMThatcher.log" if rotation == 0 else f"mrsMThatcher.log.{rotation}"
        (tmp_path / name).write_text(
            post(
                NOW - timedelta(seconds=rotation),
                str(10_000 + rotation),
                "t01.jpg",
            ),
            encoding="utf-8",
        )

    paths = digest.discover_logs(tmp_path, "mrsMThatcher*.log*")
    records = digest.read_records(paths, None, None, physical_order=True)
    history = digest.generated_post_rate_history(paths, NOW)
    post_ids = {item["post_id"] for item in history["successful_regular_posts"]}

    assert len(paths) == 101
    assert len(records) == 101
    assert {Path(record.path).name for record in records} == {
        "mrsMThatcher.log",
        *(f"mrsMThatcher.log.{rotation}" for rotation in range(1, 101)),
    }
    assert Path(records[0].path).name == "mrsMThatcher.log.100"
    assert Path(records[-1].path).name == "mrsMThatcher.log"
    assert history["files_scanned"] == 101
    assert history["unique_regular_posts"] == 101
    assert {"10000", "10100"} <= post_ids


def test_latest_configuration_wins_across_distant_rotation(tmp_path: Path) -> None:
    active = tmp_path / "mrsMThatcher.log"
    oldest = tmp_path / "mrsMThatcher.log.100"
    active.write_text(
        log_line(
            NOW - timedelta(minutes=5),
            "Config: MAX_AUTO_REPLIES_PER_DAY=17",
        ),
        encoding="utf-8",
    )
    oldest.write_text(
        log_line(
            NOW - timedelta(hours=2),
            "Config: MAX_AUTO_REPLIES_PER_DAY=11",
        ),
        encoding="utf-8",
    )

    paths = digest.discover_logs(tmp_path, "mrsMThatcher*.log*")
    config, timestamp = digest.find_latest_config_before(paths, NOW)

    assert [path.name for path in paths] == ["mrsMThatcher.log", "mrsMThatcher.log.100"]
    assert config["MAX_AUTO_REPLIES_PER_DAY"] == "17"
    assert timestamp == NOW - timedelta(minutes=5)


def test_resume_tail_keeps_post_fallback_record_after_rotation(tmp_path: Path) -> None:
    project, _names = pool(tmp_path, 2)
    current = project / "mrsMThatcher.log"
    rotation = project / "mrsMThatcher.log.1"
    state = project / ".resume.json"
    report = project / "report.md"
    current.write_text(
        "2026-10-25 01:59:50 ERROR    worker:9 - before fallback\n",
        encoding="utf-8",
    )
    args = [
        "--project-dir", str(project),
        "--state-file", state.name,
        "--output", str(report),
        str(current),
    ]

    assert digest.main(args) == 0
    assert "before fallback" in report.read_text(encoding="utf-8")
    current.replace(rotation)
    current.write_text(
        "2026-10-25 01:00:10 ERROR    worker:9 - after fallback\n",
        encoding="utf-8",
    )

    assert digest.main(args) == 0
    second = report.read_text(encoding="utf-8")
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert "after fallback" in second
    assert "before fallback" not in second
    assert saved["last_log_entry_time"] == "2026-10-25 01:59:50"
    assert saved["last_log_entry_fingerprint_tail"]

    assert digest.main(args) == 0
    assert "no matching records" in report.read_text(encoding="utf-8")


def test_resume_boundary_counts_preserve_new_identical_occurrence(tmp_path: Path) -> None:
    timestamp = datetime(2026, 7, 10, 12, 0, 0)
    first = digest.Record(timestamp, "INFO", "worker", 9, "identical event", "bot.log", 1)
    second = digest.Record(timestamp, "INFO", "worker", 9, "identical event", "bot.log", 2)
    fingerprint = digest.record_fingerprint(first)

    filtered = digest.filter_resume_boundary_records(
        [first, second],
        timestamp,
        Counter({fingerprint: 1}),
    )

    assert filtered == [second]

    state_file = tmp_path / ".resume.json"
    report = {
        "latest_state": {},
        "latest_config": {},
        "summary": {},
        "generated_image_spacing": {},
        "resume_context": {},
    }
    digest.save_resume_time(state_file, timestamp, [first], report, [tmp_path / "bot.log"])
    digest.save_resume_time(
        state_file,
        timestamp,
        [second],
        report,
        [tmp_path / "bot.log"],
        merge_existing_boundary_occurrences=True,
    )
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["last_log_entry_fingerprint_counts"] == {fingerprint: 2}


def test_resume_tail_preserves_new_identical_occurrence() -> None:
    timestamp = datetime(2026, 7, 10, 12, 0, 0)
    first = digest.Record(timestamp, "INFO", "worker", 9, "identical event", "bot.log", 1)
    second = digest.Record(timestamp, "INFO", "worker", 9, "identical event", "bot.log", 2)
    fingerprint = digest.record_fingerprint(first)

    assert digest.locate_resume_fingerprint_tail([first, second], [fingerprint]) == (1, 1)
    assert digest.locate_resume_fingerprint_tail(
        [first, second],
        [fingerprint, fingerprint],
    ) == (2, 2)


def test_legacy_resume_fingerprint_list_maps_to_one_occurrence_each() -> None:
    assert digest.resume_boundary_fingerprint_counts(
        {"last_log_entry_fingerprints": ["a", "b"]}
    ) == Counter({"a": 1, "b": 1})


def test_selftest_records_cannot_supply_production_state_or_config():
    production = digest.Record(
        datetime(2026, 7, 15, 17, 17),
        "DEBUG",
        "save_state",
        1,
        'State being saved: {"last_main_post_id":"2077427274274533828"}',
        "/project/mrsMThatcher.log",
        1,
    )
    selftest = digest.Record(
        datetime(2026, 7, 15, 18, 28),
        "DEBUG",
        "save_state",
        1,
        'State being saved: {"last_main_post_id":"950001"}',
        "/project/mrsMThatcher.selftest.log",
        1,
    )
    selftest_config = digest.Record(
        datetime(2026, 7, 15, 18, 28, 1),
        "INFO",
        "main",
        1,
        "Config: MAX_AUTO_REPLIES_PER_DAY=999",
        "/project/mrsMThatcher.selftest.log",
        2,
    )

    report = digest.analyse([production, selftest, selftest_config])

    assert report["latest_state"]["last_main_post_id"] == "2077427274274533828"
    assert "MAX_AUTO_REPLIES_PER_DAY" not in report["latest_config"]


def test_explicit_selftest_log_cannot_load_neighbor_state_or_backscan_config(tmp_path):
    selftest = tmp_path / "mrsMThatcher.log.selftest"
    selftest.write_text(
        "2026-07-15 18:28:00 INFO     main:1 - Config: MAX_AUTO_REPLIES_PER_DAY=999\n",
        encoding="utf-8",
    )
    (tmp_path / "bot_state.json").write_text(
        '{"last_main_post_id":"950001"}\n',
        encoding="utf-8",
    )

    state, source, timestamp = digest.load_authoritative_state_for_logs([selftest])
    config, config_timestamp = digest.find_latest_config_before(
        [selftest],
        datetime(2026, 7, 15, 19, 0),
    )

    assert (state, source, timestamp) == (None, None, None)
    assert config == {}
    assert config_timestamp is None


def test_latest_state_summary_includes_last_meme_post_epoch(
    london_local_time,
):
    summary = digest.summarize_latest_state(
        {"last_meme_post_epoch": 1_784_119_355},
        datetime(2026, 7, 15, 18, 17, 20),
    )

    assert summary["last_meme_post_epoch"] == 1_784_119_355
    assert summary["last_meme_post_human"] == "2026-07-15 13:42:35"


def test_reset_resume_save_does_not_carry_forward_old_state(tmp_path):
    state_file = tmp_path / ".resume.json"
    state_file.write_text(
        json.dumps(
            {
                "last_known_latest_state": {
                    "last_main_post_id": "950001",
                    "meme_anchor_quote_post_human": "2027-01-15 00:00:00",
                }
            }
        ),
        encoding="utf-8",
    )
    timestamp = datetime(2026, 7, 15, 22, 24, 56)
    record = digest.Record(timestamp, "INFO", "main", 1, "Main loop tick", str(tmp_path / "bot.log"), 1)
    report = {
        "latest_state": {
            "last_main_post_id": "2077504708474704163",
            "meme_anchor_quote_post_human": None,
        },
        "latest_config": {},
        "summary": {},
        "generated_image_spacing": {},
        "resume_context": {},
    }

    digest.save_resume_time(
        state_file,
        timestamp,
        [record],
        report,
        [tmp_path / "bot.log"],
        preserve_existing_context=False,
    )

    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["last_known_latest_state"]["last_main_post_id"] == "2077504708474704163"
    assert saved["last_known_latest_state"]["meme_anchor_quote_post_human"] is None
    assert "950001" not in json.dumps(saved)


@pytest.mark.parametrize("value", [{}, "bad", None])
def test_wrong_used_history_schema_is_invalid(tmp_path, value):
    base, _ = pool(tmp_path, 1)
    (base / "images_used.json").write_text(json.dumps(value))
    snapshot = digest.generated_pool_health_snapshot(base)
    assert snapshot["active_previously_used"] == 0
    assert snapshot["health"] == "WARNING"
    assert any(item["kind"] == "used_history_malformed" for item in snapshot["warnings"])


def test_duplicate_used_history_entries_follow_set_semantics(tmp_path):
    base, names = pool(tmp_path, 1)
    (base / "images_used.json").write_text(json.dumps([names[0], names[0]]))
    snapshot = digest.generated_pool_health_snapshot(base)
    assert snapshot["active_previously_used"] == 1


def test_valid_empty_pool_has_complete_metadata_coverage(tmp_path):
    base = tmp_path / "empty"
    (base / "generated_review_approved_images").mkdir(parents=True)
    (base / "generated_image_analysis.json").write_text(json.dumps({"schema_version": 3, "analysis_kind": "images", "path_index": {}, "items": {}}))
    (base / "generated_image_identity_dependence_audit.json").write_text(json.dumps({"schema_version": 1, "analysis_kind": "generated_image_identity_dependence_audit", "items": {}}))
    (base / "images_used.json").write_text("[]")
    snapshot = digest.generated_pool_health_snapshot(base)
    assert snapshot["metadata_coverage"] == "complete"
    assert snapshot["hash_validation"] == "complete"


@pytest.mark.parametrize("file_name,patch", [
    ("generated_image_analysis.json", {"schema_version": 99}),
    ("generated_image_analysis.json", {"analysis_kind": "wrong"}),
    ("generated_image_analysis.json", {"items": []}),
    ("generated_image_identity_dependence_audit.json", {"schema_version": 99}),
    ("generated_image_identity_dependence_audit.json", {"analysis_kind": "wrong"}),
    ("generated_image_identity_dependence_audit.json", {"items": []}),
])
def test_metadata_schema_errors_warn(tmp_path, file_name, patch):
    base, _ = pool(tmp_path, 1)
    path = base / file_name
    payload = json.loads(path.read_text())
    payload.update(patch)
    path.write_text(json.dumps(payload))
    snapshot = digest.generated_pool_health_snapshot(base)
    assert snapshot["metadata_coverage"] != "complete" and snapshot["health"] == "WARNING"


def heartbeat(ts: datetime) -> str:
    return log_line(ts, "Main loop tick")


def test_continuous_coverage_and_no_posts(tmp_path):
    path = tmp_path / "bot.log"
    rows = [heartbeat(NOW - timedelta(minutes=value)) for value in range(60, -1, -5)]
    path.write_text("".join(rows))
    window = digest.generated_post_rate_history([path], NOW)["windows"]["trailing_7d"]
    assert window["coverage_quality"] == "continuous"
    assert window["regular_posts"] == 0


@pytest.mark.parametrize("tail_hours", [2, 24])
def test_stopped_or_missing_rotation_is_reported_as_gapped(tmp_path, tail_hours):
    path = tmp_path / "bot.log"
    path.write_text(heartbeat(NOW - timedelta(hours=tail_hours)))
    window = digest.generated_post_rate_history([path], NOW)["windows"]["trailing_7d"]
    assert window["coverage_quality"] == "gapped"
    assert window["largest_detected_gap_seconds"] >= tail_hours * 3600


def test_overlapping_rotations_do_not_create_false_gap(tmp_path):
    current = tmp_path / "bot.log"
    rotated = tmp_path / "bot.log.1"
    rows = [heartbeat(NOW - timedelta(minutes=value)) for value in range(60, -1, -5)]
    current.write_text("".join(rows[5:]))
    rotated.write_text("".join(rows[:7]))
    window = digest.generated_post_rate_history([current, rotated], NOW)["windows"]["trailing_7d"]
    assert window["coverage_quality"] == "continuous"


def test_sparse_valid_log_is_honestly_gapped(tmp_path):
    path = tmp_path / "bot.log"
    path.write_text(heartbeat(NOW - timedelta(days=2)) + heartbeat(NOW))
    window = digest.generated_post_rate_history([path], NOW)["windows"]["trailing_7d"]
    assert window["coverage_quality"] == "gapped"
    assert window["observed_logging_days"] < window["calendar_span_days"]
