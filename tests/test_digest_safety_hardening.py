from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import mrs_log_digest as digest
from tests.test_generated_image_pool_health_digest import pool
from tests.test_generated_image_pool_runway_digest import log_line, post


NOW = datetime(2026, 7, 10, 12)


def project_with_log(tmp_path: Path) -> tuple[Path, Path]:
    project, names = pool(tmp_path, 2)
    log = project / "mrsMThatcher.log"
    log.write_text(post(NOW - timedelta(minutes=2), "123", names[0]))
    return project, log


def main_args(project: Path, log: Path, *extra: str) -> list[str]:
    return ["--project-dir", str(project), "--state-file", ".resume.json", "--since", "2026-07-10 00:00:00", *extra, str(log)]


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


def test_successful_output_advances_resume_once(tmp_path, monkeypatch):
    project, log = project_with_log(tmp_path)
    calls = []
    original = digest.save_resume_time
    monkeypatch.setattr(digest, "save_resume_time", lambda *a, **k: (calls.append(1), original(*a, **k))[1])
    output = tmp_path / "report.md"
    assert digest.main(main_args(project, log, "--output", str(output))) == 0
    assert output.exists() and "# MrsMThatcher log digest" in output.read_text()
    assert len(calls) == 1 and (project / ".resume.json").exists()


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
