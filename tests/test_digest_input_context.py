"""Digest input records, saved context, time boundaries and output destinations."""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, MISSING, fields
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import hashlib
import io
import json
import re
import subprocess
import sys

import pytest

import mrs_log_digest as digest
import mrs_log_digest_records as record_owner

from tests.helpers.digest_records import BASE, record


def test_complete_cursor_reads_current_sections_and_partial_inputs_keep_absence(tmp_path, monkeypatch):
    row = record(0, "INFO", "worker", "boundary")
    report = digest.analyse([row])
    summary = dict(report["summary"])
    summary["record_count"] = 17
    report["summary"] = summary
    resume = dict(report["resume_context"])
    resume["pending_mention"] = {"considered_seq": 4}
    report["resume_context"] = resume
    path = tmp_path / "resume.json"

    def replace_sections(_row):
        report["summary"] = {**summary, "record_count": 19}
        report["resume_context"] = {**resume, "pending_mention": {"considered_seq": 5}}
        return "fingerprint"

    monkeypatch.setattr(digest, "record_fingerprint", replace_sections)

    digest.save_resume_time(path, row.ts, [row], report, [], complete_report=report)
    saved = json.loads(path.read_text())
    assert saved["last_run_record_count"] == 19
    assert saved["last_pending_mention"] == {"considered_seq": 5}

    digest.save_resume_time(path, row.ts, [], {}, [], preserve_existing_context=False)
    partial = json.loads(path.read_text())
    assert partial["last_run_record_count"] is None
    assert partial["last_pending_mention"] is None
    with pytest.raises(AttributeError):
        digest.save_resume_time(path, row.ts, [], {"summary": None}, [], preserve_existing_context=False)


def test_context_stripping_keeps_current_recursive_helper_and_key_set(monkeypatch):
    import mrs_log_digest_context as context_owner

    assert digest.INTERNAL_CONTEXT_KEYS is context_owner.INTERNAL_CONTEXT_KEYS
    assert context_owner.Record is digest.Record
    assert digest.extract_config_pairs is context_owner.extract_config_pairs
    assert digest.parse_partial_state_from_msg is context_owner.parse_partial_state_from_msg
    strip = digest.strip_internal_context_markers
    leaf = ({"drop": "noncontainer tuple"},)
    original = {"drop": 1, "nested": [{"drop": 2, "leaf": leaf}], "_partial": True}
    monkeypatch.setattr(digest, "INTERNAL_CONTEXT_KEYS", {"drop"})
    clean = strip(original)
    assert clean == {"nested": [{"leaf": leaf}], "_partial": True}
    assert clean is not original and clean["nested"] is not original["nested"]
    assert clean["nested"][0] is not original["nested"][0]
    assert clean["nested"][0]["leaf"] is leaf
    assert original["drop"] == 1 and original["nested"][0]["drop"] == 2
    assert strip(leaf) is leaf

    calls = []
    for marker in ("first", "second"):
        monkeypatch.setattr(digest, "strip_internal_context_markers", lambda value: (calls.append(value), marker)[1])
        monkeypatch.setattr(digest, "INTERNAL_CONTEXT_KEYS", {"_partial"})
        assert strip(original) == {"drop": marker, "nested": marker}
        assert calls[-2:] == [1, original["nested"]]
        assert strip([leaf, original]) == [marker, marker]
        assert calls[-2] is leaf and calls[-1] is original


@pytest.mark.parametrize("backscan", [False, True])
def test_context_merges_keep_current_callbacks_fill_only_and_sharing(monkeypatch, backscan):
    merge = digest.merge_context_from_log_backscan if backscan else digest.merge_context
    nested = {"items": []}
    current = {"keep": nested, "zero": 0, "empty": [], "fill": None}
    previous = {"raw": True}
    cleaned = {"fill": nested, "zero": 3, "empty": [1], "added": nested, "omit": 9}
    calls = []
    kwargs = {"backscan_ts": BASE} if backscan else {}
    annotation = "_filled_from_log_backscan" if backscan else "_filled_from_previous"
    for timestamp in ("first", "second"):
        monkeypatch.setattr(digest, "strip_internal_context_markers", lambda value: (calls.append(("strip", value)), cleaned)[1])
        monkeypatch.setattr(digest, "INTERNAL_CONTEXT_KEYS", {"omit"})
        monkeypatch.setattr(digest, "dt_text", lambda value: (calls.append(("time", value)), timestamp)[1])
        result = merge(current, previous, **kwargs)
        assert result == {**current, "fill": nested, "added": nested, annotation: True,
                          **({"_log_backscan_timestamp": timestamp} if backscan else {})}
        assert result is not current
        assert result["fill"] is result["added"] is result["keep"] is nested
        assert result["empty"] is current["empty"] and current["fill"] is None
        assert calls[-(2 if backscan else 1):] == [("strip", previous)] + ([("time", BASE)] if backscan else [])
    if backscan:
        calls.clear()
        assert "_log_backscan_timestamp" not in merge({}, previous, backscan_ts=None)
        assert calls == [("strip", previous)]
        monkeypatch.setattr(digest, "strip_internal_context_markers", lambda value: {})
        calls.clear()
        assert merge(current, previous, backscan_ts=BASE) == current
        assert calls == []


def test_config_backscan_keeps_current_callbacks_duplicate_and_read_order(monkeypatch):
    calls = []

    def path(name, exists=True):
        return SimpleNamespace(name=name, exists=lambda: (calls.append(("exists", name)), exists)[1])

    paths = [path("selftest"), path("missing", False), path("z"), path("a")]

    def rec(message, filename, ordinal, ts=BASE):
        return digest.Record(ts, "INFO", "worker", 1, message, filename, ordinal)

    rows = {
        "z": [rec("z", "z", 5), rec("duplicate", "z", 2),
              rec("cutoff", "z", 6, BASE + timedelta(seconds=1)), rec("ignore", "z", 7)],
        "a": [rec("duplicate", "a", 1), rec("a5", "a", 5), rec("a2", "a", 2)],
    }
    for invocation in (1, 2):
        calls.clear()
        monkeypatch.setattr(digest, "is_selftest_log_path", lambda value: (calls.append(("selftest", value.name)), value.name == "selftest")[1])
        monkeypatch.setattr(digest, "iter_records", lambda value: (calls.append(("iter", value.name)), iter(rows[value.name]))[1])
        monkeypatch.setattr(digest, "extract_config_pairs", lambda msg: (calls.append(("extract", msg)), {} if msg == "ignore" else {"KEY": f"{invocation}:{msg}"})[1])
        assert digest.find_latest_config_before(paths, None) == ({}, None)
        assert calls == []
        assert digest.find_latest_config_before(paths, BASE + timedelta(seconds=1)) == ({"KEY": f"{invocation}:z"}, BASE)
        assert calls == [
            ("selftest", "selftest"), ("selftest", "missing"), ("exists", "missing"),
            ("selftest", "z"), ("exists", "z"), ("iter", "z"),
            ("extract", "z"), ("extract", "duplicate"), ("extract", "ignore"),
            ("selftest", "a"), ("exists", "a"), ("iter", "a"),
            ("extract", "duplicate"), ("extract", "a5"), ("extract", "a2"),
            ("extract", "a2"), ("extract", "a5"), ("extract", "duplicate"), ("extract", "z"),
        ]


def test_resume_reader_keeps_current_parser_warning_and_exception_boundary(monkeypatch):
    calls = []
    path = Path("unused-cursor")
    def read(value, *, maximum):
        assert value is path and maximum == 8 * 1024 * 1024
        calls.append("read")
        return b"cursor"
    monkeypatch.setattr(digest, "read_stable_regular_bytes", read)
    for result in ({"first": []}, {"second": []}):
        def parse(raw, *, label):
            calls.append((raw, label))
            return result
        monkeypatch.setattr(digest, "_strict_native_json_object", parse)
        assert digest.read_resume_data(path) is result
        assert calls[-2:] == ["read", (b"cursor", "digest resume state")]
    warning = io.StringIO()
    monkeypatch.setattr(digest.sys, "stderr", warning)
    def fail(*args, **kwargs):
        raise ValueError("bad cursor")
    monkeypatch.setattr(digest, "_strict_native_json_object", fail)
    assert digest.read_resume_data(path) == {}
    assert warning.getvalue() == f"WARNING: could not read state file {path}: bad cursor\n"
    monkeypatch.setattr(digest, "read_stable_regular_bytes", fail)
    assert digest.read_resume_data(path) == {}
    def absent(*args, **kwargs):
        raise FileNotFoundError(path)
    monkeypatch.setattr(digest, "read_stable_regular_bytes", absent)
    warning.seek(0)
    warning.truncate()
    assert digest.read_resume_data(path) == {} and warning.getvalue() == ""


def test_state_window_uses_current_parser_only_when_bounded(monkeypatch):
    state = {"time": "observed"}
    calls = []
    for value, expected in ((BASE, True), (BASE + timedelta(seconds=1), False), (None, True)):
        monkeypatch.setattr(digest, "parse_dt", lambda text: (calls.append(text), value)[1])
        assert digest.state_context_is_within_window(state, None)
        assert digest.state_context_is_within_window(state, BASE) is expected
    assert calls == ["observed"] * 3

    def fail(value):
        raise ValueError("invalid timestamp")

    monkeypatch.setattr(digest, "parse_dt", fail)
    assert digest.state_context_is_within_window(state, BASE)


def test_saved_context_keeps_current_helpers_historical_identity_and_refresh_order(monkeypatch):
    previous_state = {"time": "2999-01-01 00:00:00", "daily_reply_count": 999}
    previous_config = {"MAX_AUTO_REPLIES_PER_DAY": 999}
    nested = {"value": []}
    previous_spacing = {"nested": nested}
    old = {"last_known_latest_state": previous_state, "last_known_latest_config": previous_config,
           "last_known_generated_image_spacing": previous_spacing,
           "last_log_entry_time": "cursor time", "updated_at": "save time"}
    calls = []
    path = Path("unused-cursor")
    for marker in ("first", "second"):
        cleaned_state, cleaned_config = {marker: nested}, {marker: nested}
        report = {"latest_state": {}, "latest_config": {}, "generated_image_spacing": {}}
        current_state, current_config = report["latest_state"], report["latest_config"]
        monkeypatch.setattr(digest, "read_resume_data", lambda value: (calls.append(("read", value)), old)[1])
        monkeypatch.setattr(digest, "strip_internal_context_markers", lambda value: (calls.append(("strip", value)), cleaned_state if value is previous_state else cleaned_config)[1])

        def refresh(value):
            calls.append(("refresh", value))
            assert value is report
            assert value["historical_retained_state"] is cleaned_state
            assert value["historical_retained_config"] is cleaned_config
            assert value["generated_image_spacing"]["latest"]["nested"] is nested
            assert value["generated_image_spacing"]["latest"] is not previous_spacing
            assert value["latest_state"] is current_state and value["latest_config"] is current_config

        monkeypatch.setattr(digest, "refresh_derived", refresh)
        monkeypatch.setattr(digest, "state_context_is_within_window", lambda *args: pytest.fail("unused window filter"))
        assert digest.apply_saved_context(report, path) is None
        assert calls[-4:] == [("read", path), ("strip", previous_state), ("strip", previous_config), ("refresh", report)]
        assert report["digest_resume_context"] == {"available": True, "last_log_entry_time": "cursor time", "updated_at": "save time"}
        assert report["latest_state"] == report["latest_config"] == {}
        assert "_carried_forward" not in previous_spacing


def test_cursor_save_keeps_current_callbacks_sharing_and_late_clock(tmp_path, monkeypatch):
    calls = []
    nested = {"shared": []}
    old = {"last_log_entry_time": "old", "last_known_latest_state": {"old": nested},
           "last_known_latest_config": {"OLD": nested}}
    report = {"latest_state": {"current": nested}, "latest_config": {"CURRENT": nested},
              "runtime_state_status": {"status": "absent"},
              "runtime_config_status": {"status": "malformed"},
              "generated_image_spacing": {"latest": {"nested": nested, "_private": 1}},
              "summary": {"record_count": 2}}
    rows = [record(0, "INFO", "worker", "boundary"), record(-1, "INFO", "worker", "earlier")]
    encoded = []
    dumps = json.dumps
    path = tmp_path / "resume.json"

    class EarlyClock:
        @classmethod
        def now(cls):
            pytest.fail("clock sampled before final resume-context read")

    class SaveClock:
        @classmethod
        def now(cls):
            calls.append(("clock",))
            return BASE

    class ResumeContext(dict):
        def get(self, key, default=None):
            calls.append(("context", key))
            if key == "pending_qt":
                monkeypatch.setattr(digest, "datetime", SaveClock)
            return super().get(key, default)

    report["resume_context"] = ResumeContext(pending_qt=nested)

    def encode(data, **kwargs):
        calls.append(("encode", kwargs))
        encoded.append(data)
        assert data["last_known_generated_image_spacing"] == {"nested": nested}
        assert data["last_known_generated_image_spacing"]["nested"] is nested
        assert data["last_known_generated_image_spacing"] is not report["generated_image_spacing"]["latest"]
        assert data["last_pending_qt"] is nested
        return dumps(data, **kwargs)

    monkeypatch.setattr(digest.json, "dumps", encode)
    for preserve in (True, False):
        calls.clear()
        clean_results = []
        monkeypatch.setattr(digest, "datetime", EarlyClock)
        monkeypatch.setattr(digest, "RESUME_FINGERPRINT_TAIL_LIMIT", 2 if preserve else 1)
        monkeypatch.setattr(digest, "read_resume_data", lambda value: (calls.append(("read", value)), old)[1])

        def strip(value):
            calls.append(("strip", value))
            source = (old["last_known_latest_state"] if not clean_results else old["last_known_latest_config"]) if preserve else (report["latest_state"] if not clean_results else report["latest_config"])
            assert value == source and value is not source
            assert next(iter(value.values())) is nested
            result = {"clean": nested}
            clean_results.append(result)
            return result

        def parse(value):
            calls.append(("parse", value))
            if value is None:
                raise ValueError("missing old timestamp")
            return BASE

        monkeypatch.setattr(digest, "strip_internal_context_markers", strip)
        monkeypatch.setattr(digest, "Counter", lambda values: (calls.append(("counter",)), Counter(values))[1])
        monkeypatch.setattr(digest, "record_fingerprint", lambda row: (calls.append(("fingerprint", row)), row.msg)[1])
        monkeypatch.setattr(digest, "parse_dt", parse)
        monkeypatch.setattr(digest, "resume_boundary_fingerprint_counts", lambda value: (calls.append(("boundary", value)), Counter(boundary=2))[1])
        monkeypatch.setattr(digest, "resume_fingerprint_tail", lambda value: (calls.append(("tail", value)), ["old-tail"])[1])
        monkeypatch.setattr(digest, "dt_text", lambda value: (calls.append(("format", value)), "formatted")[1])
        tail = None if preserve else ["supplied-a", "supplied-b"]
        assert digest.save_resume_time(path, BASE, rows, report, [Path("test.log")],
                                       preserve_existing_context=preserve,
                                       merge_existing_boundary_occurrences=True,
                                       cursor_fingerprint_tail=tail) is None
        data = encoded[-1]
        assert data["last_known_latest_state"] is clean_results[0]
        assert data["last_known_latest_config"] is clean_results[1]
        assert data["last_log_entry_fingerprint_counts"] == {"boundary": 3 if preserve else 1}
        assert data["last_log_entry_fingerprint_tail"] == (["boundary", "earlier"] if preserve else ["supplied-b"])
        assert tail is None or tail == ["supplied-a", "supplied-b"]
        names = [call[0] for call in calls]
        assert names == (["read"] if preserve else []) + ["strip", "strip", "counter", "fingerprint", "parse"] + (["boundary", "tail", "fingerprint", "fingerprint"] if preserve else []) + ["format", "context", "context", "context", "context", "clock", "encode"]
        assert path.read_text() == dumps(data, indent=2, ensure_ascii=False) + "\n"
        assert not list(tmp_path.glob(".resume.json.*.tmp"))

    failure = RuntimeError("counter failed")

    def fail(values):
        raise failure

    monkeypatch.setattr(digest, "Counter", fail)
    monkeypatch.setattr(digest, "strip_internal_context_markers", lambda value: value)
    monkeypatch.setattr(digest, "datetime", EarlyClock)
    calls.clear()
    with pytest.raises(RuntimeError) as caught:
        digest.save_resume_time(path, BASE, rows, report, [], preserve_existing_context=False)
    assert caught.value is failure
    assert calls == []


def test_record_owner_shares_frozen_type_and_lazy_parser_delegation(tmp_path, monkeypatch):
    assert digest.Record is record_owner.Record
    assert [field.name for field in fields(digest.Record)] == [
        "ts", "level", "src", "line", "msg", "path", "ordinal",
    ]
    assert all(field.default is MISSING and field.default_factory is MISSING
               for field in fields(digest.Record))
    path = tmp_path / "input.log"
    path.write_bytes(
        b"leading junk\n2026-07-25 09:00:00 INFO worker:9 - first\n"
        b"  continuation\xff\n2026-07-25 09:00:01 ERROR other - last"
    )
    calls = []
    original_regex = digest.LOG_RE

    class HeaderRegex:
        def match(self, value):
            calls.append(("match", value))
            return original_regex.match(value)

    class ParserTime(datetime):
        @classmethod
        def strptime(cls, value, fmt):
            calls.append(("parse", value, fmt))
            return datetime.strptime(value, fmt) + timedelta(days=1)

    def construct(**values):
        calls.append(("construct", values))
        return record_owner.Record(**values)

    records = digest.iter_records(path)
    assert calls == []
    monkeypatch.setattr(digest, "LOG_RE", HeaderRegex())
    monkeypatch.setattr(digest, "datetime", ParserTime)
    monkeypatch.setattr(digest, "Record", construct)
    first = next(records)
    assert first == record_owner.Record(
        BASE + timedelta(days=1), "INFO", "worker", 9,
        "first\n  continuation\ufffd", str(path), 1,
    )
    assert [call[0] for call in calls] == ["match", "match", "parse", "match", "match", "construct"]
    second = next(records)
    assert second == record_owner.Record(
        BASE + timedelta(days=1, seconds=1), "ERROR", "other", 0, "last", str(path), 2,
    )
    assert [call[0] for call in calls[-2:]] == ["parse", "construct"]
    assert list(records) == []
    with pytest.raises(FrozenInstanceError):
        first.msg = "changed"
    path.write_text("2026-99-25 09:00:00 INFO worker - invalid timestamp\n")
    with pytest.raises(ValueError, match="does not match format"):
        list(digest.iter_records(path))


def test_record_reader_preserves_current_iterator_warning_and_stat_order(tmp_path, monkeypatch):
    a, b, missing = (tmp_path / name for name in ("a.txt", "b.txt", "missing.txt"))
    first = digest.Record(BASE, "INFO", "worker", 9, "a", str(a), 1)
    second = digest.Record(BASE + timedelta(seconds=1), "INFO", "worker", 9, "b", str(b), 1)
    calls = []
    original_exists, original_stat = Path.exists, Path.stat

    def exists(path):
        if path not in (a, b, missing):
            return original_exists(path)
        calls.append(("exists", path.name))
        return path != missing

    def read(path):
        calls.append(("read", path.name))
        return iter([first] if path == a else [second])

    def stat(path, *args, **kwargs):
        if path not in (a, b, missing):
            return original_stat(path, *args, **kwargs)
        calls.append(("stat", path.name))
        if path == missing:
            raise OSError("missing")
        return SimpleNamespace(st_mtime_ns=7)

    class Warnings(io.StringIO):
        def write(self, text):
            if text.strip():
                calls.append(("warning", text))
            return super().write(text)

    warnings = Warnings()
    monkeypatch.setattr(Path, "exists", exists)
    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(digest, "iter_records", read)
    monkeypatch.setattr(digest.sys, "stderr", warnings)
    result = digest.read_records([b, missing, a], BASE, second.ts, physical_order=True)
    assert result[0] is first and result[1] is second
    warning = f"WARNING: missing log file: {missing}"
    assert warnings.getvalue() == warning + "\n"
    assert calls == [
        ("exists", "b.txt"), ("read", "b.txt"), ("exists", "missing.txt"),
        ("warning", warning), ("exists", "a.txt"), ("read", "a.txt"),
        ("stat", "b.txt"), ("stat", "missing.txt"), ("stat", "a.txt"),
    ]


def test_input_summary_keeps_stat_reader_conversion_order_and_physical_endpoints(tmp_path, monkeypatch):
    paths = [tmp_path / name for name in ("read.log", "missing.log", "stat-error.log")]
    calls = []
    records = [record(1, "INFO", "worker", "later"), record(0, "INFO", "worker", "earlier")]
    original_exists, original_stat = Path.exists, Path.stat

    def exists(path):
        if path not in paths:
            return original_exists(path)
        calls.append(("exists", path.name))
        return path != paths[1]

    def stat(path, *args, **kwargs):
        if path not in paths:
            return original_stat(path, *args, **kwargs)
        calls.append(("stat", path.name))
        if path == paths[2]:
            raise OSError("unavailable metadata")
        return SimpleNamespace(st_size=42, st_mtime=123)

    def read(path):
        calls.append(("read", path.name))
        return iter(records)

    class FileTime(datetime):
        @classmethod
        def fromtimestamp(cls, value):
            calls.append(("mtime", value))
            return BASE

    def format_time(value):
        calls.append(("format", value))
        return value.isoformat()

    monkeypatch.setattr(Path, "exists", exists)
    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(digest, "iter_records", read)
    monkeypatch.setattr(digest, "datetime", FileTime)
    monkeypatch.setattr(digest, "dt_text", format_time)
    summaries = digest.summarize_input_files(paths, BASE, BASE, since_exclusive=True)
    assert calls == [
        ("exists", "read.log"), ("stat", "read.log"),
        ("mtime", 123), ("read", "read.log"), ("format", records[0].ts), ("format", BASE),
        ("exists", "missing.log"),
        ("exists", "stat-error.log"), ("stat", "stat-error.log"),
        ("read", "stat-error.log"), ("format", records[0].ts), ("format", BASE),
    ]
    assert summaries[0] == {
        "path": str(paths[0]), "exists": True, "size": 42, "mtime": "2026-07-25 09:00:00",
        "total_records": 2, "first_timestamp": records[0].ts.isoformat(),
        "last_timestamp": BASE.isoformat(), "records_after_since": 1, "records_in_window": 0,
    }
    assert summaries[1]["exists"] is False and summaries[1]["total_records"] == 0
    assert summaries[2]["size"] is None and summaries[2]["mtime"] is None
    assert summaries[2]["total_records"] == 2


@pytest.mark.parametrize("since,exclusive", [(None, False), (BASE, False), (BASE, True)])
@pytest.mark.parametrize("until", [None, BASE + timedelta(seconds=1)])
def test_combined_reader_preserves_raw_summaries_and_physical_resume_records(
    tmp_path, monkeypatch, capsys, since, exclusive, until,
):
    current, rotation, empty, missing = [
        tmp_path / name for name in (
            "mrsMThatcher.log", "mrsMThatcher.log.1",
            "mrsMThatcher.log.2", "mrsMThatcher.log.3",
        )
    ]

    def line(offset, message):
        timestamp = (BASE + timedelta(seconds=offset)).strftime("%Y-%m-%d %H:%M:%S")
        return f"{timestamp} INFO worker:9 - {message}\n"

    current.write_text(line(0, "duplicate") * 2 + line(1, "new"))
    rotation.write_text(
        line(-1, "old") + line(0, "duplicate")
        + line(3, "beyond upper bound") + line(-2, "clock rollback")
    )
    empty.touch()
    paths = [current, missing, empty, rotation]
    expected_records = digest.read_records(paths, None, until, physical_order=True)
    expected_summaries = digest.summarize_input_files(
        paths, since, until, since_exclusive=exclusive,
    )
    capsys.readouterr()
    original_reader = digest.iter_records
    parsed = []

    def read_once(path):
        parsed.append(path)
        return original_reader(path)

    monkeypatch.setattr(digest, "iter_records", read_once)
    records, summaries = digest.read_records_and_summaries(
        paths, since, until, since_exclusive=exclusive,
    )

    assert parsed == [current, empty, rotation]
    assert records == expected_records
    assert summaries == expected_summaries
    assert [item["total_records"] for item in summaries] == [3, 0, 0, 4]
    assert summaries[3]["first_timestamp"] == digest.dt_text(BASE - timedelta(seconds=1))
    assert summaries[3]["last_timestamp"] == digest.dt_text(BASE - timedelta(seconds=2))
    assert [item.ordinal for item in records if item.msg == "duplicate"] == [2, 1, 2]
    assert [item.path for item in records if item.msg == "duplicate"] == [
        str(rotation), str(current), str(current),
    ]
    assert capsys.readouterr().err == f"WARNING: missing log file: {missing}\n"


def test_cli_analysis_and_input_coverage_share_the_same_parse(tmp_path, monkeypatch):
    log = tmp_path / "mrsMThatcher.log"
    output = tmp_path / "digest.json"
    log.write_text(
        "2026-07-25 09:00:00 INFO worker:9 - initial record\n"
        "2026-07-25 09:00:01 INFO worker:9 - last observed record\n"
    )
    original_reader = digest.iter_records
    parsed = []

    def append_after_read(path):
        parsed.append(path)
        yield from original_reader(path)
        # A second parse would now include an observation absent from analysis.
        with path.open("a", encoding="utf-8") as source:
            source.write("2026-07-25 09:00:02 ERROR worker:9 - appended after read\n")

    monkeypatch.setattr(digest, "iter_records", append_after_read)
    assert digest.main([
        "--project-dir", str(tmp_path), "--no-state", "--json",
        "--since", "2026-07-25 09:00:00", "--output", str(output), str(log),
    ]) == 0

    report = json.loads(output.read_text())
    assert parsed == [log]
    assert report["summary"]["record_count"] == 2
    assert report["input_files"][0]["total_records"] == 2
    assert report["input_files"][0]["records_in_window"] == 2
    assert report["input_files"][0]["last_timestamp"] == "2026-07-25 09:00:01"
    assert "appended after read" in log.read_text()


@pytest.mark.parametrize("state_args", [[], ["--reset-state"], ["--no-state"]])
@pytest.mark.parametrize("json_primary", [False, True])
def test_cli_refreshes_once_after_live_overlay_in_all_output_modes(
    tmp_path, monkeypatch, state_args, json_primary,
):
    log = tmp_path / "mrsMThatcher.log"
    log.write_text("2026-07-25 09:00:00 INFO worker:9 - selected record\n")
    (tmp_path / "bot_state.json").write_text(json.dumps({"daily_reply_count": 3}))
    (tmp_path / "mrsMThatcher.local.json").write_text(json.dumps({"MAX_AUTO_REPLIES_PER_DAY": 10}))
    cursor = tmp_path / ".mrs_log_digest_state.json"
    cursor.write_text(json.dumps({
        "last_known_latest_state": {"daily_reply_count": 999, "time": "2999-01-01 00:00:00"},
        "last_known_latest_config": {"MAX_AUTO_REPLIES_PER_DAY": 999},
    }))
    original_cursor = cursor.read_bytes()
    refreshed = []
    refresh = digest.refresh_derived

    def observe_refresh(report, *, complete_report=None):
        assert report["latest_state"]["daily_reply_count"] == 3
        assert report["latest_config"]["MAX_AUTO_REPLIES_PER_DAY"] == 10
        assert complete_report is report
        refresh(report, complete_report=complete_report)
        refreshed.append(report)

    monkeypatch.setattr(digest, "refresh_derived", observe_refresh)
    output = tmp_path / "digest.json"
    markdown = tmp_path / "digest.md"
    output_args = (
        ["--json", "--output", str(output), "--markdown-output", str(markdown)]
        if json_primary else ["--output", str(markdown), "--json-output", str(output)]
    )
    assert digest.main([
        "--project-dir", str(tmp_path), "--since", "2026-07-25 09:00:00",
        "--until", "2026-07-25 09:00:00", "--no-update-state",
        *state_args, *output_args,
    ]) == 0

    assert len(refreshed) == 1
    report = json.loads(output.read_text())
    assert report["summary"]["record_count"] == 1
    assert report["derived"]["reply_budget"]["auto_remaining"] == 7
    if not state_args:
        assert report["historical_retained_state"]["daily_reply_count"] == 999
        assert report["historical_retained_config"]["MAX_AUTO_REPLIES_PER_DAY"] == 999
    else:
        assert "historical_retained_state" not in report
        assert "historical_retained_config" not in report
    assert markdown.read_text() == digest.render_markdown(report) + "\n"
    assert cursor.read_bytes() == original_cursor


def test_source_and_fingerprint_helpers_use_current_formatter_and_logger(monkeypatch):
    item = record(0, "INFO", "worker", "exact\x1fbytes\n\ud800", line=9)
    monkeypatch.setattr(digest, "SAFE_SOURCE_LOGGER_RE", re.compile(r"custom\Z"))
    assert digest.safe_source_logger("custom") == "custom"
    assert digest.safe_source_logger("worker") == "unavailable"
    calls = []

    def format_time(value):
        calls.append(("time", value))
        return "formatted"

    def logger(value):
        calls.append(("logger", value))
        return "safe"

    monkeypatch.setattr(digest, "dt_text", format_time)
    monkeypatch.setattr(digest, "safe_source_logger", logger)
    reference = digest.record_source_ref(item, {item.path: 0})
    assert reference == {
        "input_file_index": 0, "record_number": 1, "timestamp": "formatted",
        "logger": "safe", "logged_source_line_number": 9,
    }
    assert digest.record_fingerprint(item) == hashlib.sha256(
        b"formatted\x1fINFO\x1fworker\x1f9\x1fexact\x1fbytes\n?"
    ).hexdigest()
    assert calls == [("time", BASE), ("logger", "worker"), ("time", BASE)]
    assert digest.bounded_source_refs is record_owner.bounded_source_refs
    references, omitted = digest.bounded_source_refs(reference, [reference])
    assert references == [reference] and omitted == 0


def test_resume_helpers_keep_current_fingerprint_and_tail_limit(monkeypatch):
    records = [record(0, "INFO", "worker", "same") for _ in range(3)]
    calls = []

    def fingerprint(item):
        calls.append(item)
        return "f" * 64

    monkeypatch.setattr(digest, "record_fingerprint", fingerprint)
    monkeypatch.setattr(digest, "RESUME_FINGERPRINT_TAIL_LIMIT", 2)
    assert digest.resume_fingerprint_tail({
        "last_log_entry_fingerprint_tail": ["a" * 64, "invalid", "f" * 64],
    }) == ["f" * 64]
    assert digest.locate_resume_fingerprint_tail(records, ["f" * 64] * 2) == (2, 2)
    counts = Counter({"f" * 64: 2})
    filtered = digest.filter_resume_boundary_records(records, BASE, counts)
    assert len(filtered) == 1 and filtered[0] is records[2]
    assert counts == Counter({"f" * 64: 2})
    assert len(calls) == 6
    assert all(actual is expected for actual, expected in zip(calls, records * 2))


def test_input_coverage_uses_current_parser_and_formatter(monkeypatch):
    calls = []

    def parse(value):
        calls.append(("parse", value))
        if value == "invalid":
            raise ValueError("invalid")
        return {"later": BASE + timedelta(seconds=1), "none": None}[value]

    def format_time(value):
        calls.append(("format", value))
        return "later" if value > BASE else "start"

    monkeypatch.setattr(digest, "parse_dt", parse)
    monkeypatch.setattr(digest, "dt_text", format_time)
    result = digest.input_retention_coverage(
        [{"first_timestamp": value} for value in ("", "invalid", "none", "later")], BASE,
    )
    assert result == {
        "requested_since": "start", "earliest_retained_timestamp": "later",
        "requested_start_covered": False, "retention_gap_seconds": 1,
        "warning": "requested window starts at start, but the earliest retained timestamp is later; "
                   "coverage of the preceding interval cannot be verified from retained logs",
    }
    assert calls == [
        ("parse", "invalid"), ("parse", "none"), ("parse", "later"),
        ("format", BASE), ("format", BASE + timedelta(seconds=1)),
        ("format", BASE), ("format", BASE + timedelta(seconds=1)),
    ]


def _select_window(records, since, *, tail=(), counts=None, exclusive=False):
    return record_owner.select_resume_window(
        records, since, since_exclusive=exclusive, saved_resume_tail=list(tail),
        resume_boundary_counts=counts if counts is not None else Counter(),
        locate_resume_fingerprint_tail=digest.locate_resume_fingerprint_tail,
        filter_records_by_time=digest.filter_records_by_time,
        filter_resume_boundary_records=digest.filter_resume_boundary_records,
        warn_timestamp_fallback=lambda: None,
    )


@pytest.mark.parametrize("appended", [False, True])
def test_resume_selection_reports_partial_tail_and_preserves_clock_rollback(appended):
    saved = [record(i, "INFO", "worker", f"saved {i}") for i in range(10)]
    tail = [digest.record_fingerprint(item) for item in saved]
    rollback = record(-10, "INFO", "worker", "appended after clock rollback")
    retained = saved[2:] + ([rollback] if appended else [])
    original = list(retained)

    selection = _select_window(retained, saved[-1].ts, tail=tail, exclusive=True)

    assert selection.cursor_mode == "fingerprint_tail"
    assert selection.tail_match_length == 8
    assert selection.timestamp_fallback is False
    assert selection.records == ([rollback] if appended else [])
    if appended:
        assert selection.records[0] is rollback
    assert retained == original
    assert tail == [digest.record_fingerprint(item) for item in saved]


@pytest.mark.parametrize("missing_tail", [False, True])
def test_resume_selection_keeps_unprocessed_boundary_occurrences(missing_tail):
    duplicate = record(0, "INFO", "worker", "duplicate")
    rows = [record(-1, "INFO", "worker", "old"), duplicate,
            record(0, "INFO", "worker", "duplicate"),
            record(0, "INFO", "worker", "different"),
            record(1, "INFO", "worker", "later")]
    counts = Counter({digest.record_fingerprint(duplicate): 1})
    original_counts = counts.copy()

    selection = _select_window(
        rows, BASE, counts=counts, tail=["0" * 64] if missing_tail else [],
    )

    assert selection.cursor_mode == "timestamp"
    assert selection.tail_match_length == 0
    assert selection.timestamp_fallback is missing_tail
    expected = rows[1:] if missing_tail else rows[2:]
    assert selection.records == expected
    assert all(actual is expected_record for actual, expected_record in zip(selection.records, expected))
    assert counts == original_counts


@pytest.mark.parametrize("since,exclusive,start", [(None, True, 0), (BASE, False, 1), (BASE, True, 2)])
def test_resume_selection_retains_timestamp_boundary_semantics(since, exclusive, start):
    rows = [record(i, "INFO", "worker", str(i)) for i in (-1, 0, 1)]
    selection = _select_window(rows, since, exclusive=exclusive)
    assert selection.records == rows[start:]
    assert selection.records is not rows
    assert selection.cursor_mode == "timestamp"
    assert selection.tail_match_length == 0
    assert selection.timestamp_fallback is False


@pytest.mark.parametrize("failure", ["warning", "time_filter"])
def test_resume_fallback_warning_precedes_filter_failures(failure):
    calls = []
    rows = [record(0, "INFO", "worker", "boundary")]

    def step(name, result):
        calls.append(name)
        if name == failure:
            raise RuntimeError(failure)
        return result

    with pytest.raises(RuntimeError, match=failure):
        record_owner.select_resume_window(
            rows, BASE, since_exclusive=False, saved_resume_tail=["0" * 64],
            resume_boundary_counts=Counter({digest.record_fingerprint(rows[0]): 1}),
            locate_resume_fingerprint_tail=lambda *args: step("locate", None),
            warn_timestamp_fallback=lambda: step("warning", None),
            filter_records_by_time=lambda *args, **kwargs: step("time_filter", rows),
            filter_resume_boundary_records=lambda *args: step("boundary_filter", rows),
        )
    expected = ["locate", "warning", "time_filter"]
    assert calls == expected[:expected.index(failure) + 1]


def test_legacy_timestamp_boundary_filter_failure_still_propagates():
    rows = [record(0, "INFO", "worker", "boundary")]
    with pytest.raises(RuntimeError, match="boundary filter failed"):
        record_owner.select_resume_window(
            rows, BASE, since_exclusive=False, saved_resume_tail=[],
            resume_boundary_counts=Counter({digest.record_fingerprint(rows[0]): 1}),
            locate_resume_fingerprint_tail=lambda *_args: None,
            warn_timestamp_fallback=lambda: None,
            filter_records_by_time=lambda *_args, **_kwargs: rows,
            filter_resume_boundary_records=lambda *_args: (_ for _ in ()).throw(
                RuntimeError("boundary filter failed")),
        )


def test_explicit_since_is_exact_and_boundary_is_inclusive(tmp_path, monkeypatch):
    observed_at = datetime(2026, 7, 26, 12, 0, 0)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return observed_at if tz is None else observed_at.astimezone(tz)

    # Byte equality includes current snapshots as well as the fixed log window.
    monkeypatch.setattr(digest, "datetime", FixedDateTime)
    requested = "2026-07-26 10:54:03"
    log = tmp_path / "mrsMThatcher.log"
    log.write_text(
        f"{requested} INFO     fixture:1 - exact-boundary-event\n"
        "2026-07-26 10:55:03 INFO     fixture:2 - later-event\n",
        encoding="utf-8",
    )
    first = tmp_path / "digest-regression-a.md"
    second = tmp_path / "digest-regression-b.md"
    arguments = [
        "--project-dir", str(tmp_path),
        "--since", requested,
        "--until", "2026-07-26 10:55:03",
        "--no-state",
    ]
    assert digest.main([*arguments, "--output", str(first)]) == 0
    assert digest.main([*arguments, "--output", str(second)]) == 0
    rendered = first.read_text(encoding="utf-8")
    assert f"Requested since: `{requested}` (inclusive, source=manual --since)" in rendered
    assert (
        f"Observed event window: `{requested}` → `2026-07-26 10:55:03`"
        in rendered
    )
    assert "Records parsed: `2`" in rendered
    assert "Current filesystem/configuration snapshot at `2026-07-26 12:00:00`" in rendered
    assert first.read_bytes() == second.read_bytes()


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


def assert_preflight_rejects_without_writes(tmp_path, monkeypatch, args, message):
    """Check a path error before locks or report work, including file contents."""
    def snapshot():
        return {
            str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else None
            for path in tmp_path.rglob("*")
        }

    before = snapshot()
    lock_calls = []

    def forbidden_lock(path):
        lock_calls.append(path)
        pytest.fail("digest lock acquisition reached before path validation")

    monkeypatch.setattr(digest, "digest_execution_lock", forbidden_lock)
    monkeypatch.setattr(digest, "run_digest", lambda *_a, **_k: pytest.fail("digest execution reached"))
    with pytest.raises(SystemExit, match=message):
        digest.main(args)
    assert lock_calls == []
    assert snapshot() == before


@pytest.mark.parametrize("arguments", [
    ["--state-file", "resume.json", "--output", "resume.json.lock"],
    ["--no-state", "--output", "report.md", "--markdown-output", "report.md.lock"],
])
def test_cli_refuses_outputs_that_replace_digest_lock_files(tmp_path, monkeypatch, arguments):
    log = tmp_path / "bot.log"
    log.write_text("2026-07-15 12:00:00 INFO worker:9 - existing log\n")
    resolved = [str(tmp_path / value) if value.endswith((".md", ".lock")) else value
                for value in arguments]
    assert_preflight_rejects_without_writes(
        tmp_path, monkeypatch, ["--project-dir", str(tmp_path), *resolved, str(log)],
        "output paths must not alias digest lock files",
    )


@pytest.mark.parametrize("state_option", [[], ["--no-update-state"], ["--no-state"]])
def test_output_lock_cannot_truncate_relative_cursor_before_preflight(
    tmp_path, monkeypatch, state_option,
):
    project = tmp_path / "project"
    cwd = tmp_path / "cwd"
    project.mkdir()
    cwd.mkdir()
    log = project / "bot.log"
    log.write_text("2026-07-15 12:00:00 INFO worker:9 - existing log\n")
    cursor = cwd / "report.md.lock"
    cursor.write_text('{"last_log_entry_time":"2026-07-14 12:00:00"}\n')
    monkeypatch.chdir(cwd)

    assert_preflight_rejects_without_writes(
        tmp_path, monkeypatch,
        ["--project-dir", str(project), "--state-file", "../cwd/report.md.lock",
         "--output", "report.md", *state_option, "bot.log"],
        "lock path aliases the resume-state file",
    )


@pytest.mark.parametrize("source", ["output", "cursor", "expanded_rotation", "discovered"])
def test_selected_input_log_cannot_be_truncated_by_derived_lock(
    tmp_path, monkeypatch, source,
):
    project = tmp_path / "project"
    project.mkdir()
    state = project / "resume.json"
    output = project / "report.md"
    arguments = ["--project-dir", str(project), "--state-file", state.name]
    line = "2026-07-15 12:00:00 INFO worker:9 - protected input\n"
    if source == "output":
        log = project / "report.md.lock"
        log.write_text(line)
        arguments += ["--output", str(output), log.name]
    elif source == "cursor":
        log = project / "resume.json.lock"
        log.write_text(line)
        arguments += [log.name]
    elif source == "expanded_rotation":
        current = project / "mrsMThatcher.log"
        current.write_text(line)
        rotation = project / "mrsMThatcher.log.1"
        rotation.write_text(line)
        (project / "report.md.lock").hardlink_to(rotation)
        arguments += ["--output", str(output), current.name]
    else:
        log = project / "mrsMThatcher.log.lock"
        log.write_text(line)
        arguments += ["--output", str(project / "mrsMThatcher.log")]

    assert_preflight_rejects_without_writes(
        tmp_path, monkeypatch, arguments, "lock path aliases an input log",
    )


def test_hard_linked_cursor_and_output_lock_are_rejected_before_truncation(tmp_path, monkeypatch):
    log = tmp_path / "bot.log"
    log.write_text("2026-07-15 12:00:00 INFO worker:9 - existing log\n")
    state = tmp_path / "resume.json"
    state.write_text('{"last_log_entry_time":"2026-07-14 12:00:00"}\n')
    (tmp_path / "report.md.lock").hardlink_to(state)

    assert_preflight_rejects_without_writes(
        tmp_path, monkeypatch,
        ["--project-dir", str(tmp_path), "--state-file", state.name,
         "--output", str(tmp_path / "report.md"), log.name],
        "lock path aliases the resume-state file",
    )


@pytest.mark.parametrize("protected", ["input", "state", "output"])
def test_hard_linked_outputs_keep_existing_collision_protections(
    tmp_path, monkeypatch, protected,
):
    log = tmp_path / "bot.log"
    log.write_text("2026-07-15 12:00:00 INFO worker:9 - existing log\n")
    state = tmp_path / "resume.json"
    state.write_text('{"last_log_entry_time":"2026-07-14 12:00:00"}\n')
    first = tmp_path / "first.md"
    if protected == "input":
        first.hardlink_to(log)
        message = "output path aliases an input log"
    elif protected == "state":
        first.hardlink_to(state)
        message = "output path aliases the resume-state file"
    else:
        first.write_text("existing report\n")
        message = "output destinations alias each other"
    arguments = ["--project-dir", str(tmp_path), "--state-file", state.name,
                 "--output", str(first), log.name]
    if protected == "output":
        second = tmp_path / "second.md"
        second.hardlink_to(first)
        arguments[6:6] = ["--markdown-output", str(second)]

    assert_preflight_rejects_without_writes(tmp_path, monkeypatch, arguments, message)


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


@pytest.mark.parametrize("other_state", [None, "second-state.json"])
def test_shared_output_serializes_different_state_modes(tmp_path, monkeypatch, other_state):
    output = tmp_path / "shared.md"
    log = tmp_path / "bot.log"
    log.write_text("2026-07-15 12:00:00 INFO worker:9 - existing log\n")
    outer = ["--project-dir", str(tmp_path), "--state-file", "first-state.json",
             "--output", str(output), str(log)]
    inner = ["--project-dir", str(tmp_path), "--output", str(output), str(log)]
    inner += ["--no-state"] if other_state is None else ["--state-file", other_state]
    entries = []

    def outer_run(*_args, **_kwargs):
        entries.append("entered")
        if len(entries) > 1:
            return 0
        with pytest.raises(RuntimeError, match="Another digest process holds"):
            digest.main(inner)
        return 0

    monkeypatch.setattr(digest, "run_digest", outer_run)
    assert digest.main(outer) == 0
    assert entries == ["entered"]


def test_stateful_output_lock_blocks_no_state_in_another_process(tmp_path, monkeypatch):
    output = tmp_path / "shared.md"
    log = tmp_path / "bot.log"
    log.write_text("2026-07-15 12:00:00 INFO worker:9 - existing log\n")
    script = """
import sys
import mrs_log_digest as digest
digest.run_digest = lambda *_args, **_kwargs: 0
try:
    digest.main(sys.argv[1:])
except RuntimeError as exc:
    if "Another digest process holds" in str(exc):
        sys.exit(0)
    raise
sys.exit(7)
"""

    def while_locked(*_args, **_kwargs):
        result = subprocess.run(
            [sys.executable, "-c", script, "--no-state", "--output", str(output), str(log)],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stderr
        return 0

    monkeypatch.setattr(digest, "run_digest", while_locked)
    assert digest.main(["--project-dir", str(tmp_path), "--state-file", "first-state.json",
                        "--output", str(output), str(log)]) == 0


def test_unrelated_output_and_state_locks_can_run_together(tmp_path, monkeypatch):
    log = tmp_path / "bot.log"
    log.write_text("2026-07-15 12:00:00 INFO worker:9 - existing log\n")
    outer = ["--project-dir", str(tmp_path), "--state-file", "first-state.json",
             "--output", str(tmp_path / "first.md"), str(log)]
    inner = ["--project-dir", str(tmp_path), "--state-file", "second-state.json",
             "--output", str(tmp_path / "second.md"), str(log)]
    calls = []

    def fake_run(*_args, **_kwargs):
        calls.append("run")
        if len(calls) == 1:
            assert digest.main(inner) == 0
        return 0

    monkeypatch.setattr(digest, "run_digest", fake_run)
    assert digest.main(outer) == 0
    assert calls == ["run", "run"]


def test_multiple_outputs_and_state_locks_acquire_in_deterministic_order(tmp_path, monkeypatch):
    log = tmp_path / "bot.log"
    log.write_text("2026-07-15 12:00:00 INFO worker:9 - existing log\n")
    primary = tmp_path / "z.md"
    markdown = tmp_path / "b.md"
    json_output = tmp_path / "m.json"
    state = tmp_path / "a-state.json"
    acquired = []
    released = []

    @contextmanager
    def fake_lock(path):
        acquired.append(path)
        try:
            yield
        finally:
            released.append(path)

    monkeypatch.setattr(digest, "digest_execution_lock", fake_lock)
    monkeypatch.setattr(digest, "run_digest", lambda *_args, **_kwargs: 0)
    assert digest.main([
        "--project-dir", str(tmp_path), "--state-file", str(state),
        "--output", str(primary), "--markdown-output", str(markdown),
        "--json-output", str(json_output), str(log),
    ]) == 0
    expected = sorted({path.with_suffix(path.suffix + ".lock") for path in
                       (state, primary, markdown, json_output)}, key=str)
    assert acquired == expected
    assert released == list(reversed(expected))


def test_hard_linked_state_and_output_locks_are_acquired_once(tmp_path, monkeypatch):
    log = tmp_path / "bot.log"
    log.write_text("2026-07-15 12:00:00 INFO worker:9 - existing log\n")
    state = tmp_path / "a-state.json"
    output = tmp_path / "z-report.md"
    state_lock = state.with_suffix(".json.lock")
    output_lock = output.with_suffix(".md.lock")
    state_lock.write_text("old lock owner\n")
    output_lock.hardlink_to(state_lock)
    real_lock = digest.digest_execution_lock
    acquired = []

    @contextmanager
    def recording_lock(path):
        acquired.append(path)
        with real_lock(path):
            yield

    monkeypatch.setattr(digest, "digest_execution_lock", recording_lock)
    assert digest.main(["--project-dir", str(tmp_path), "--state-file", state.name,
                        "--output", str(output), log.name]) == 0
    assert acquired == [state_lock]
    assert output.exists() and state.exists()
    assert output_lock.samefile(state_lock)


def test_other_process_holding_either_lock_alias_blocks_digest(tmp_path):
    log = tmp_path / "bot.log"
    log.write_text("2026-07-15 12:00:00 INFO worker:9 - existing log\n")
    state = tmp_path / "a-state.json"
    output = tmp_path / "z-report.md"
    state_lock = state.with_suffix(".json.lock")
    output_lock = output.with_suffix(".md.lock")
    state_lock.touch()
    output_lock.hardlink_to(state_lock)
    script = """
from pathlib import Path
import sys
import mrs_log_digest as digest
with digest.digest_execution_lock(Path(sys.argv[1])):
    print('ready', flush=True)
    sys.stdin.readline()
"""
    for held_alias in (state_lock, output_lock):
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(held_alias)],
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert process.stdout is not None
            assert process.stdout.readline().strip() == "ready"
            with pytest.raises(RuntimeError, match="Another digest process holds"):
                digest.main(["--project-dir", str(tmp_path), "--state-file", state.name,
                             "--output", str(output), log.name])
        finally:
            stdout, stderr = process.communicate("\n", timeout=10)
            assert process.returncode == 0, stderr + stdout


@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_lock_identity_preflight_keeps_no_follow_and_regular_file_guards(tmp_path, kind):
    log = tmp_path / "bot.log"
    log.write_text("2026-07-15 12:00:00 INFO worker:9 - existing log\n")
    state = tmp_path / "state.json"
    lock = state.with_suffix(".json.lock")
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("unchanged")
    if kind == "symlink":
        lock.symlink_to(sentinel)
    else:
        lock.mkdir()
    with pytest.raises(RuntimeError, match="Digest lock is not a regular file"):
        digest.main(["--project-dir", str(tmp_path), "--state-file", state.name,
                     log.name])
    assert sentinel.read_text() == "unchanged"
    assert not state.exists()


def test_lock_identity_inspection_error_propagates_before_acquisition(tmp_path, monkeypatch):
    log = tmp_path / "bot.log"
    log.write_text("2026-07-15 12:00:00 INFO worker:9 - existing log\n")
    state = tmp_path / "state.json"
    lock = state.with_suffix(".json.lock")
    original_lstat = Path.lstat

    def inspect(path):
        if path == lock:
            raise PermissionError("lock inspection denied")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", inspect)
    monkeypatch.setattr(digest, "digest_execution_lock", lambda _path: pytest.fail(
        "lock acquisition reached after an inspection error"))
    with pytest.raises(PermissionError, match="lock inspection denied"):
        digest.main(["--project-dir", str(tmp_path), "--state-file", state.name,
                     log.name])
    assert not state.exists() and not lock.exists()
    assert log.read_text() == "2026-07-15 12:00:00 INFO worker:9 - existing log\n"


def test_input_retention_coverage_warns_when_requested_start_predates_logs():
    result = digest.input_retention_coverage(
        [
            {
                "first_timestamp": "2026-07-21 00:51:23",
                "last_timestamp": "2026-07-25 01:00:00",
            }
        ],
        datetime(2026, 7, 18, 0, 0),
    )

    assert result["requested_start_covered"] is False
    assert result["retention_gap_seconds"] == 262283
    assert "coverage of the preceding interval cannot be verified" in result["warning"]
    rendered = digest.render_markdown(
        {
            **digest.analyse([]),
            "requested_since": "2026-07-18 00:00:00",
            "since_source": "manual --since",
            "since_exclusive": False,
            "input_retention_coverage": result,
            "input_warning": result["warning"],
        }
    )
    assert "Retained-log coverage of requested start: **no**" in rendered


def test_input_retention_coverage_accepts_a_covered_boundary():
    result = digest.input_retention_coverage(
        [{"first_timestamp": "2026-07-17 23:59:59"}],
        datetime(2026, 7, 18, 0, 0),
    )

    assert result["requested_start_covered"] is True
    assert result["warning"] == ""


def test_retired_trial_state_is_removed_from_retained_digest_context(tmp_path):
    cursor = tmp_path / "resume.json"
    original = {
        "last_log_entry_time": "2026-08-30 10:00:00",
        "last_known_latest_state": {
            "daily_reply_count": 3,
            "engagement_question_experiment": {"status": "paused"},
        },
    }
    cursor.write_text(json.dumps(original))
    report = {"runtime_state_status": {"status": "missing"}}
    digest.apply_saved_context(report, cursor)
    assert report["historical_retained_state"] == {"daily_reply_count": 3}
    assert json.loads(cursor.read_text()) == original
    digest.save_resume_time(cursor, BASE, [], report, [])
    saved = json.loads(cursor.read_text())
    assert saved["last_known_latest_state"] == {"daily_reply_count": 3}
