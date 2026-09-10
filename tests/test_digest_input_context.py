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
        ("exists", "read.log"), ("exists", "read.log"), ("stat", "read.log"),
        ("mtime", 123), ("read", "read.log"), ("format", records[0].ts), ("format", BASE),
        ("exists", "missing.log"), ("exists", "missing.log"),
        ("exists", "stat-error.log"), ("exists", "stat-error.log"), ("stat", "stat-error.log"),
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


def test_explicit_since_is_exact_and_boundary_is_inclusive(tmp_path):
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
