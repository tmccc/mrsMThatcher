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


def test_default_log_discovery_excludes_selftest_logs(tmp_path):
    production = tmp_path / "mrsMThatcher.log"
    rotation = tmp_path / "mrsMThatcher.log.1"
    selftest = tmp_path / "mrsMThatcher.selftest.log"
    for path in (production, rotation, selftest):
        path.write_text("", encoding="utf-8")

    discovered = digest.discover_logs(tmp_path, "mrsMThatcher*.log*")

    assert production in discovered
    assert rotation in discovered
    assert selftest not in discovered


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


def test_latest_state_summary_includes_last_meme_post_epoch():
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
