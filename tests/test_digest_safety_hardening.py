from __future__ import annotations

import json
import os
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import mrs_log_digest as digest
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


def test_resume_preserves_in_flight_provider_call_until_usage_arrives(tmp_path):
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
    usage = report["xai_usage"]

    assert usage["events"][0]["call_start_matched"] is True
    assert usage["events"][0]["model"] == "grok-4.3"
    assert usage["call_attempts"][0]["usage_observed"] is True
    assert usage["cost_summary"]["coverage_complete"] is True
    assert usage["cost_summary"]["unmatched_successful_call_count"] == 0
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
    production = tmp_path / "mrsMThatcher.log"
    rotation = tmp_path / "mrsMThatcher.log.1"
    selftest = tmp_path / "mrsMThatcher.selftest.log"
    for path in (production, rotation, selftest):
        path.write_text("", encoding="utf-8")

    discovered = digest.discover_logs(tmp_path, "mrsMThatcher*.log*")

    assert production in discovered
    assert rotation in discovered
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
