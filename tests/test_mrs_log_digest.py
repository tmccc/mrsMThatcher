from __future__ import annotations

import builtins
import hashlib
import io
import json
import re
from collections import Counter
from dataclasses import MISSING, FrozenInstanceError, fields
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import mrs_log_digest as digest
import mrs_log_digest_api_health as api_health_owner
import mrs_log_digest_records as record_owner
import mrs_log_digest_state_reporting as state_reporting_owner
import mrs_log_digest_transactions as transaction_owner


BASE = datetime(2026, 7, 25, 9, 0, 0)


def record(
    offset: int,
    level: str,
    source: str,
    message: str,
    *,
    line: int = 1,
) -> digest.Record:
    return digest.Record(
        ts=BASE + timedelta(seconds=offset),
        level=level,
        src=source,
        line=line,
        msg=message,
        path="fixture.log",
        ordinal=offset + 1,
    )


def traceback(message: str, exception: str) -> str:
    return (
        f"{message}\n"
        "Traceback (most recent call last):\n"
        '  File "/srv/mrsMThatcher2.py", line 100, in worker\n'
        f"{exception}"
    )


def loaded_gate(offset: int) -> digest.Record:
    return record(
        offset,
        "INFO",
        "log_event",
        'EVENT {"event":"historical_context_semantic_gate","status":"loaded",'
        '"policy_version":"gate-v1","ledger_sha256":"ledger",'
        '"projection_sha256":"projection","blocked_quote_count":21}',
    )


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


def test_reply_visual_description_contract_rejects_unsafe_shapes() -> None:
    valid = {
        "event": "reply_visual_description",
        "lane": "mention_reply",
        "target_id": "123",
        "supplied_image_count": 1,
        "status": "analysed",
        "analysis_schema_version": 1,
        "description_sha256": "a" * 64,
        "visual_analysis_call_count": 1,
    }

    parsed = digest.parse_reply_visual_description_event(valid)

    assert parsed == {
        "analysis_schema_version": 1,
        "description_sha256": "a" * 64,
        "lane": "mention",
        "status": "analysed",
        "supplied_image_count": 1,
        "target_id": "123",
        "visual_analysis_call_count": 1,
    }
    paused = digest.parse_reply_visual_description_event(
        {
            **valid,
            "description_sha256": "",
            "status": "paused",
            "visual_analysis_call_count": 0,
        }
    )
    invalid_media = digest.parse_reply_visual_description_event(
        {
            **valid,
            "description_sha256": None,
            "status": "invalid_supplied_media",
            "supplied_image_count": 0,
            "visual_analysis_call_count": 0,
        }
    )
    assert paused is not None and paused["visual_analysis_call_count"] == 0
    assert invalid_media is not None and invalid_media[
        "visual_analysis_call_count"
    ] == 0
    malformed = [
        {**valid, "supplied_image_count": True},
        {**valid, "visual_analysis_call_count": False},
        {**valid, "analysis_schema_version": True},
        {**valid, "description_sha256": "A" * 64},
        {**valid, "status": "provider_error"},
        {**valid, "image_url": "https://private.invalid/image.jpg"},
        {**valid, "lane": "unknown"},
        {**valid, "target_id": ""},
        {**valid, "description_sha256": "", "status": "paused"},
        {
            **valid,
            "description_sha256": "",
            "status": "provider_error",
            "visual_analysis_call_count": 0,
        },
    ]
    assert all(
        digest.parse_reply_visual_description_event(event) is None
        for event in malformed
    )


def reconciled_remote_write_safety(*, archive_offset: int = 120) -> dict:
    """Return a current clear snapshot with evidence tied to t64.jpg."""

    archive_epoch = int((BASE + timedelta(seconds=archive_offset)).timestamp())
    marker_audit = {
        "archived_at_epoch": archive_epoch,
        "audit_path": "archive/marker.reconciliation.json",
        "marker_sha256": "a" * 64,
    }
    return {
        "configured": True,
        "available": True,
        "status": "operator_paused",
        "blocking": False,
        "ready_for_remote_writes": False,
        "protocol": {"valid": True},
        "control": {
            "valid": True,
            "generation": 3,
            "active_keys": ["disable_all"],
            "global_pause_active": True,
        },
        "reconciliation_proven": True,
        "media_reconciliation_proven": True,
        "reconciliation_archive": {
            "valid": True,
            "valid_marker_reconciliation_count": 1,
            "valid_media_reconciliation_count": 1,
            "marker_reconciliations": [marker_audit],
            "latest_marker_reconciliation": marker_audit,
            "latest_media_reconciliation": {
                "archived_at_epoch": archive_epoch - 1,
                "audit_path": "archive/media.reconciliation.json",
                "image_basename": "t64.jpg",
            },
        },
        "transport": {"classification": "clear", "blocking": False},
        "media": {"classification": "clear", "blocking": False},
        "retirement_ledgers": [
            {"valid": True, "blocking": False} for _ in range(4)
        ],
        "active_entries": [],
        "active_marker_names": [],
    }


def ambiguous_media_records() -> list[digest.Record]:
    """Represent the production receipt-bound media 503 cascade."""

    return [
        record(
            0,
            "INFO",
            "upload_media_v2",
            "Uploading receipt-bound media via X API v2: t64.jpg",
        ),
        record(
            1,
            "DEBUG",
            "x_request",
            "X request: POST https://api.x.com/2/media/upload",
        ),
        record(
            2,
            "ERROR",
            "x_request",
            'X API error 503: {"detail":"Service Unavailable","status":503}',
        ),
        record(
            3,
            "CRITICAL",
            "upload_media",
            "X media upload outcome is ambiguous; blocking every subsequent "
            "remote write pending manual reconciliation. image=t64.jpg",
        ),
        record(
            4,
            "CRITICAL",
            "record_ambiguous_remote_post",
            "AMBIGUOUS REMOTE X POST OUTCOME: X may have accepted the write, "
            "but a usable confirmation was not received. Automatic posting is "
            "blocked pending manual reconciliation: /srv/ambiguous_post_outcome.json",
        ),
        record(
            5,
            "ERROR",
            "main",
            traceback(
                "Quote/image remote outcome is ambiguous; the remote-write "
                "safety barrier is active and no retry will be scheduled",
                "AmbiguousRemotePostOutcome: X write outcome is not proved by "
                "HTTP status alone; received HTTP 503",
            ),
        ),
        record(
            6,
            "CRITICAL",
            "maintain_global_remote_write_barrier_tick",
            "All remote posting and reply lanes are paused by the durable "
            "remote-write safety barrier; manual reconciliation is required "
            "before a controlled restart",
        ),
    ]


RECONCILED_REPLY_TARGET = "2090236705231995281"


def digest044_reply_ambiguity_records() -> list[digest.Record]:
    """Represent the correlated reply receipt/barrier shape from digest044."""

    return [
        record(
            0,
            "WARNING",
            "write_sending_reply_receipt",
            "Wrote conversational reply sending receipt source=mention "
            f"target_id={RECONCILED_REPLY_TARGET} path=/srv/reply.json",
        ),
        record(
            1,
            "INFO",
            "create_post",
            "Creating X post with durable transport journal. "
            f"lane=conversational_reply transaction_id={'e' * 64} "
            f"reply_to_id={RECONCILED_REPLY_TARGET} media_count=0 "
            "made_with_ai=True",
        ),
        record(
            1,
            "DEBUG",
            "x_request",
            "X request: POST https://api.x.com/2/tweets",
            line=2,
        ),
        record(
            2,
            "ERROR",
            "x_request",
            "X API error 403: "
            '{"detail":"You attempted to reply to a Tweet that is deleted or '
            'not visible to you.","status":403,"title":"Forbidden",'
            '"type":"about:blank"}',
        ),
        record(
            2,
            "CRITICAL",
            "record_ambiguous_remote_post",
            "AMBIGUOUS REMOTE X POST OUTCOME: X may have accepted the write, "
            "but a usable confirmation was not received. Automatic posting is "
            "blocked pending manual reconciliation: /srv/ambiguous_post_outcome.json",
            line=2,
        ),
        record(
            2,
            "INFO",
            "log_event",
            'EVENT {"event":"ai_reply_pipeline_outcome",'
            '"status":"posting_failed_retryable","lane":"mention",'
            f'"target_id":"{RECONCILED_REPLY_TARGET}",'
            '"failure_reason":"ambiguous_remote_outcome"}',
            line=3,
        ),
        record(
            3,
            "CRITICAL",
            "run_normal_check",
            "Normal reply lane stopped by the global remote-write safety barrier",
        ),
        record(
            60,
            "CRITICAL",
            "maintain_global_remote_write_barrier_tick",
            "All remote posting and reply lanes are paused by the durable "
            "remote-write safety barrier; manual reconciliation is required "
            "before a controlled restart",
        ),
        record(
            180,
            "INFO",
            "maybe_reply_to_mentions",
            "Considering mention id=2090237404426694900 author_id=42 "
            "text='later candidate'",
        ),
        record(
            181,
            "INFO",
            "maybe_reply_to_mentions",
            "Reply posted successfully",
        ),
    ]


def test_media_503_uses_exact_endpoint_and_one_durably_resolved_incident():
    safety = reconciled_remote_write_safety()
    report = digest.analyse(
        ambiguous_media_records(),
        current_remote_write_safety=safety,
    )
    report["remote_write_safety"] = safety
    rendered = digest.render_markdown(report)

    assert report["api_health"]["media_upload_request_count"] == 1
    assert report["api_health"]["tweet_create_request_count"] == 0
    assert report["api_health"]["posting_attempt_count"] == 0
    assert report["api_health"]["errors"][0]["endpoint"] == "media/upload"
    assert report["api_health"]["errors"][0]["request_method"] == "POST"
    health = report["error_health"]
    assert health["raw_serious_error_record_count"] == 5
    assert health["current_independent_incident_count"] == 0
    assert health["historical_resolved_incident_count"] == 1
    incident = health["historical_resolved_incidents"][0]
    assert incident["category"] == "remote_write_ambiguity_barrier"
    assert incident["record_count"] == 5
    assert report["media_upload"]["handled_fallbacks"] == []
    assert len(report["media_upload"]["reconciled_incidents"]) == 1
    assert report["media_upload"]["unrecovered_failures"] == []
    assert report["media_upload"]["incidents"][0]["post_result"] == (
        "no later tweet-create request observed in window (uncorrelated)"
    )
    assert [item["phase"] for item in report["remote_write_transactions"]] == [
        "request_started",
        "ambiguous",
    ]
    assert "## Remote-write safety" in rendered
    assert "media/upload" in rendered
    assert "reconciled_ambiguities = 1" in rendered


def test_stale_reconciliation_evidence_cannot_resolve_new_ambiguity():
    safety = reconciled_remote_write_safety(archive_offset=-60)
    report = digest.analyse(
        ambiguous_media_records(),
        current_remote_write_safety=safety,
    )

    assert report["error_health"]["current_independent_incident_count"] == 1
    assert report["error_health"]["historical_resolved_incident_count"] == 0
    assert report["media_upload"]["reconciled_incidents"] == []
    assert report["media_upload"]["incidents"][0]["status"] == "blocked"


def test_digest044_reply_barrier_symptoms_join_one_reconciled_root_everywhere():
    safety = reconciled_remote_write_safety()
    report = digest.analyse(
        digest044_reply_ambiguity_records(),
        current_remote_write_safety=safety,
    )
    rendered = digest.render_markdown(report)
    json_report = json.loads(json.dumps(report))
    health = report["error_health"]

    assert health["current_independent_incident_count"] == 0
    assert health["historical_resolved_incident_count"] == 1
    assert health["current_incidents"] == []
    incident = health["historical_resolved_incidents"][0]
    assert incident["category"] == "remote_write_ambiguity_barrier"
    assert incident["record_count"] == 4
    assert incident["correlated_subordinate_symptom_counts"] == {
        "conversational_reply_receipt_barrier": 1,
        "normal reply lane stopped by the global remote-write safety barrier": 1,
    }
    assert incident["correlated_reply_receipt_identities"] == [
        {"lane": "mention", "target_id": RECONCILED_REPLY_TARGET}
    ]
    assert report["confirmed_reply_recovery"][
        "durably_reconciled_ambiguity_receipts"
    ][0]["target_id"] == RECONCILED_REPLY_TARGET
    assert report["confirmed_reply_recovery"][
        "durably_reconciled_ambiguity_receipts"
    ][0]["source_time"] == digest.dt_text(BASE)
    assert "current health: no unresolved operational incidents" in report[
        "summary"
    ]["headline"]
    current_section = rendered.split("## Current independent errors", 1)[1].split(
        "## Historical/resolved incident errors", 1
    )[0]
    historical_section = rendered.split(
        "## Historical/resolved incident errors", 1
    )[1].split("## Other warnings", 1)[0]
    assert "None unresolved in the selected window." in current_section
    assert "remote write ambiguity barrier" in historical_section
    assert "conversational reply receipt barrier" not in current_section
    assert "normal reply lane stopped" not in current_section
    assert "Stale or unresolved confirmed-reply receipts:" not in rendered
    assert (
        "Sending-receipt barrier observations durably reconciled with their "
        "remote-write ambiguity: **1**."
        in rendered
    )
    assert json_report["error_health"][
        "current_independent_incident_count"
    ] == 0
    assert json_report["error_health"][
        "historical_resolved_incident_count"
    ] == 1
    assert len(json_report["error_health"]["historical_resolved_incidents"]) == 1


def test_same_reply_receipt_identity_after_reconciliation_remains_current():
    records = [
        *digest044_reply_ambiguity_records(),
        record(
            240,
            "WARNING",
            "write_sending_reply_receipt",
            "Wrote conversational reply sending receipt source=mention "
            f"target_id={RECONCILED_REPLY_TARGET} path=/srv/reply.json",
        ),
    ]
    report = digest.analyse(
        records,
        current_remote_write_safety=reconciled_remote_write_safety(),
    )
    rendered = digest.render_markdown(report)
    json_report = json.loads(json.dumps(report))

    health = report["error_health"]
    assert health["current_independent_incident_count"] == 1
    assert health["historical_resolved_incident_count"] == 1
    assert health["current_incidents"][0]["category"] == (
        "conversational_reply_receipt_barrier"
    )
    assert health["current_incidents"][0]["first_seen"] == digest.dt_text(
        BASE + timedelta(seconds=240)
    )
    assert "current health: 1 unresolved operational incident" in report[
        "summary"
    ]["headline"]
    assert json_report["error_health"][
        "current_independent_incident_count"
    ] == 1
    current_section = rendered.split("## Current independent errors", 1)[1].split(
        "## Historical/resolved incident errors", 1
    )[0]
    assert "conversational reply receipt barrier" in current_section
    assert (
        "Sending-receipt barrier observations durably reconciled with their "
        "remote-write ambiguity: **1**."
        in rendered
    )
    assert "Stale or unresolved confirmed-reply receipts:" in rendered
    assert digest.dt_text(BASE + timedelta(seconds=240)) in rendered


def test_invalid_reconciliation_evidence_resolves_no_reply_barrier_symptoms():
    safety = reconciled_remote_write_safety()
    safety["reconciliation_archive"]["valid"] = False

    report = digest.analyse(
        digest044_reply_ambiguity_records(),
        current_remote_write_safety=safety,
    )

    health = report["error_health"]
    assert health["current_independent_incident_count"] == 1
    assert health["historical_resolved_incident_count"] == 0
    assert health["current_incidents"][0]["category"] == (
        "remote_write_ambiguity_barrier"
    )
    assert report["confirmed_reply_recovery"][
        "durably_reconciled_ambiguity_receipts"
    ] == []


def test_clean_snapshot_does_not_resolve_unmatched_transaction_barrier():
    report = digest.analyse(
        [
            record(
                0,
                "CRITICAL",
                "remote_write_guard",
                "Remote-write transport journal blocks this unrelated write",
            )
        ],
        current_remote_write_safety=reconciled_remote_write_safety(),
    )

    health = report["error_health"]
    assert health["current_independent_incident_count"] == 1
    assert health["historical_resolved_incident_count"] == 0
    assert health["current_incidents"][0]["category"] == (
        "remote_write_transaction_barrier"
    )


@pytest.mark.parametrize("active_barrier", ["receipt", "journal", "marker"])
def test_current_reply_receipt_journal_or_marker_keeps_root_current(
    active_barrier: str,
):
    safety = reconciled_remote_write_safety()
    safety["blocking"] = True
    if active_barrier == "journal":
        safety["transport"] = {
            "classification": "attempting",
            "blocking": True,
        }
    else:
        safety["active_entries"] = [
            {
                "name": (
                    "confirmed_reply_receipt.json"
                    if active_barrier == "receipt"
                    else "ambiguous_post_outcome.json"
                ),
                "kind": (
                    "source_receipt"
                    if active_barrier == "receipt"
                    else "ambiguity_marker"
                ),
                "safe_regular": True,
            }
        ]
        if active_barrier == "marker":
            safety["active_marker_names"] = ["ambiguous_post_outcome.json"]

    report = digest.analyse(
        digest044_reply_ambiguity_records(),
        current_remote_write_safety=safety,
    )

    assert report["error_health"]["current_independent_incident_count"] == 1
    assert report["error_health"]["historical_resolved_incident_count"] == 0


def test_unrelated_current_error_stays_current_after_reply_root_reconciliation():
    report = digest.analyse(
        [
            *digest044_reply_ambiguity_records(),
            record(
                220,
                "ERROR",
                "unrelated_worker",
                "ValueError: unrelated current failure",
            ),
        ],
        current_remote_write_safety=reconciled_remote_write_safety(),
    )

    health = report["error_health"]
    assert health["current_independent_incident_count"] == 1
    assert health["historical_resolved_incident_count"] == 1
    assert health["current_incidents"][0]["category"] == "valueerror"
    assert health["historical_resolved_incidents"][0]["category"] == (
        "remote_write_ambiguity_barrier"
    )


@pytest.mark.parametrize("age,parse_error,source", [
    (300, None, "x_request"), (300.001, None, "x_request"),
    (-300, None, "x_bearer_request"), (-300.001, None, "x_bearer_request"),
    (0, ValueError, "x_request"), (0, TypeError, "x_request"),
    (0, None, "unrelated"),
])
def test_x_error_observation_keeps_request_window_callbacks_and_mutation(age, parse_error, source):
    message = f"X{' bearer' if source == 'x_bearer_request' else ''} API error 503: fixture"
    r = record(0, "INFO", source, message)
    request = {"time": "request time", "endpoint": "custom/path-name", "method": object(), "url": object()}
    requests = [request]
    indexes = {r.path: 2}
    reference = {"fixture": "source"}
    errors, restrictions, calls = [], [], []
    stats = Counter()

    def parse(value):
        assert value == "request time" and stats["x_api_errors"] == 1
        calls.append("parse")
        if parse_error:
            raise parse_error("request time")
        return BASE - timedelta(seconds=age)

    def seconds(left, right):
        calls.append("seconds")
        assert right is r.ts
        return digest.seconds_between(left, right)

    def short(value, limit):
        assert value == message and limit == 240
        calls.append("short")
        return "formatted error"

    def source_ref(item, mapping):
        assert item is r and mapping is indexes and "failed" not in request
        calls.append("source")
        return reference

    def deleted(value):
        pytest.fail("503 must not invoke the 403 classifier")

    inputs = dict(
        latest_x_request_by_source={source: request},
        pending_mention={"mention_id": "123", "source": "hot_post_reply"},
        pending_qt={"quote_tweet_id": "456"}, is_handled_reply_restriction=False,
        api_errors=errors, handled_api_restrictions=restrictions, stats=stats,
        input_file_indexes=indexes, parse_dt=parse, seconds_between=seconds,
        short=short, record_source_ref=source_ref,
        is_deleted_or_inaccessible_tweet_403=deleted,
    )
    if parse_error is TypeError:
        with pytest.raises(TypeError, match="request time"):
            api_health_owner.handle_x_api_error(r, message, **inputs)
        assert calls == ["parse"] and not errors and "failed" not in request
        return
    assert api_health_owner.handle_x_api_error(r, message, **inputs) is False
    if source == "unrelated":
        assert not calls and not errors and not stats and "failed" not in request
        return
    assert calls == ["parse", *([] if parse_error else ["seconds"]), "short", "source"]
    matched = parse_error is None and abs(age) <= 300
    error = errors[0]
    assert requests[0] is request and not restrictions
    assert error["source_refs"][0] is reference and error["message"] == "formatted error"
    assert (error["target_id"], error["lane"]) == ("123", "hot_post_reply")
    assert error["endpoint"] == ("custom/path-name" if matched else "quote_tweets" if source == "x_bearer_request" else "unknown_oauth")
    if matched:
        assert request["failed"] is True and request["status"] == "503"
        assert error["request_method"] is request["method"] and error["request_url"] is request["url"]
        assert stats["x_api_503_custom_path_name"] == 1
    else:
        assert "failed" not in request and "status" not in request
        assert error["request_method"] == error["request_url"] == ""


def test_api_dispatch_keeps_handled_exits_latest_error_and_shared_rows(monkeypatch):
    messages = [
        "X request: POST https://api.x.com/2/tweets",
        "X API error 503: fixture",
        "Rate Limit: 100", "Remaining: 7",
        "Recorded x API error. status_code=503 errors_in_window=1/3",
        "API cooldown active until 2026-07-25 09:00:05: No mentions returned",
        "Traceback No mentions returned Entering API cooldown after repeated errors until 2026-07-25 09:00:05",
        "Traceback No mentions returned Normalized used-history JSON ordering in fixture.json",
        "X API error 403: Tweet is unavailable. Traceback No mentions returned",
        "X API error 403: not allowed to reply. Traceback No mentions returned",
        "xAI error 503: fixture Traceback No mentions returned",
    ]
    rows = [record(i, "INFO", "xai_request" if i == 10 else "x_request", msg) for i, msg in enumerate(messages)]
    calls, captured = [], {}
    for name in ("handle_cooldown_message", "handle_x_api_error", "observe_provider_error", "enrich_latest_api_error"):
        original = getattr(digest, name)

        def observe(*args, _name=name, _original=original, **kwargs):
            msg = args[0] if _name == "enrich_latest_api_error" else args[1]
            calls.append((_name, msg))
            if _name in {"handle_x_api_error", "observe_provider_error"}:
                for helper in ("short", "record_source_ref"):
                    assert kwargs[helper] is getattr(digest, helper)
                captured["errors"] = kwargs["api_errors"]
            result = _original(*args, **kwargs)
            if _name == "handle_cooldown_message":
                captured["active"] = kwargs["cooldown_active"]
            if _name == "handle_x_api_error":
                captured["restrictions"] = kwargs["handled_api_restrictions"]
                captured["request"] = kwargs["latest_x_request_by_source"]["x_request"]
                assert result is (msg == messages[8])
            return result

        monkeypatch.setattr(digest, name, observe)
    report = digest.analyse(rows, initial_pending_mention={"mention_id": "123"})
    for i, msg in enumerate(messages):
        expected = ["handle_cooldown_message"]
        if i not in {6, 7}:
            expected.append("handle_x_api_error")
            if i != 8:
                expected += ["observe_provider_error", "enrich_latest_api_error"]
        assert [name for name, value in calls if value == msg] == expected
    health = report["api_health"]
    assert health["errors"] is captured["errors"]
    assert health["handled_restrictions"] is captured["restrictions"]
    assert health["cooldown_active"] is captured["active"]
    assert health["x_requests"][0] is captured["request"]
    assert captured["request"]["failed"] is True and captured["request"]["status"] == "403"
    assert health["errors"][0]["rate_limit"] == "100"
    assert health["errors"][0]["remaining"] == "7"
    assert health["errors"][0]["errors_in_window"] == "1/3"
    assert [item["restriction_kind"] for item in health["handled_restrictions"]] == ["deleted_or_inaccessible_tweet", "reply_target_eligibility"]
    assert health["post_cooldown_errors"][0] is health["errors"][1]
    assert report["summary"]["stats"]["tracebacks"] == 2
    assert report["summary"]["stats"]["no_mentions_checks"] == 3
    assert report["summary"]["stats"]["used_history_normalized"] == 1


@pytest.mark.parametrize("source,message,cleared", [
    ("xai_request", "xAI error 503: fixture", True),
    ("ask_grok_for_reply", "xAI error without status", True),
    ("ask_grok_for_reply", "Grok generated usable reply: fixture", True),
    ("ask_grok_for_reply", "Grok chose to skip", True),
    ("unrelated", "xAI error 503: fixture", False),
])
def test_provider_error_reset_keeps_context_identity_order_and_attempt_index(monkeypatch, source, message, cleared):
    context = {"lane": "mention", "context_id": "123", "author_id": "456"}
    attempt = {**context, "time": digest.dt_text(BASE), "stage": "proposer", "model": "fixture", "usage_observed": False}
    captured, calls = {}, []
    original_usage = digest.observe_provider_message
    original_error = digest.observe_provider_error

    def usage(*args, **kwargs):
        result = original_usage(*args, **kwargs)
        captured["context"] = result[0]
        assert result[1] == 0
        calls.append("usage")
        return result

    def error(*args, **kwargs):
        assert calls == ["usage"] and kwargs["active_xai_context"] is captured["context"]
        result = original_error(*args, **kwargs)
        assert result is (None if cleared else captured["context"])
        calls.append("error")
        return result

    monkeypatch.setattr(digest, "observe_provider_message", usage)
    monkeypatch.setattr(digest, "observe_provider_error", error)
    report = digest.analyse([record(1, "INFO", source, message)], initial_active_xai_context=context, initial_active_xai_call_attempt=attempt)
    assert calls == ["usage", "error"]
    assert report["resume_context"]["active_xai_context"] is (None if cleared else captured["context"])
    assert report["resume_context"]["active_xai_call_attempt"] == attempt


def test_api_preparation_keeps_current_helpers_production_identity_and_late_report_effects(monkeypatch):
    calls, captured = [], {}
    helpers = ("bounded_event_text", "valid_string_public_post_id", "_normalised_structured_reply_confirmation", "parse_dt", "seconds_between")
    for name in helpers:
        original = getattr(digest, name)

        def helper(*args, _name=name, _original=original, **kwargs):
            calls.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(digest, name, helper)

    class CurrentCounter(Counter):
        def items(self):
            calls.append(("items", id(self)))
            return super().items()

    class CurrentDatetime(datetime):
        @classmethod
        def strptime(cls, value, fmt):
            calls.append("strptime")
            return datetime.strptime(value, fmt)

    original_prepare = digest.prepare_api_health
    original_quality = digest.historical_context_quality_summary
    original_report = digest.api_health_report

    def prepare(**inputs):
        for name in helpers:
            assert inputs[name] is getattr(digest, name)
        assert inputs["event_counter"] is CurrentCounter
        assert inputs["strptime"] == CurrentDatetime.strptime
        assert inputs["datetime_min"] is CurrentDatetime.min
        assert inputs["SHA256_LOWER_RE"] is digest.SHA256_LOWER_RE
        excluded = {"kind": "remote_write_succeeded", "post_id": "900"}
        included = {"kind": "remote_write_succeeded", "post_id": "901"}
        inputs["events"].extend([dict(excluded), included])
        inputs["production_event_object_ids"].update([id(excluded), id(included)])
        first = {"kind": "tweet_transport", "phase": "request_started", "transaction_id": "a" * 64, "lane": "quote_image"}
        inputs["remote_write_transactions"].extend([first, {**first, "lane": "mention"}])
        inputs["structured_reply_confirmations"].append({})
        calls.clear()
        prepared = original_prepare(**inputs)
        assert set(helpers) | {"strptime"} <= set(calls)
        assert prepared.observed_success_post_ids == {"901"}
        assert prepared.observed_tweet_transport_by_id["a" * 64] is first
        assert prepared.observed_tweet_transport_lane_conflict_ids == ["a" * 64]
        assert prepared.all_api_failures[0] is inputs["api_errors"][0]
        assert prepared.post_cooldown_errors[0] is inputs["api_errors"][0]
        assert type(prepared.api_status_counts) is CurrentCounter
        captured.update(inputs=inputs, prepared=prepared)
        calls.append("prepared")
        return prepared

    def quality(events):
        assert calls[-1] == "prepared"
        errors = captured["inputs"]["api_errors"]
        errors[0]["status"] = "502"
        errors.append({"status": "429", "time": digest.dt_text(BASE), "message": "late"})
        calls.append("quality")
        return original_quality(events)

    def report(prepared, **inputs):
        assert prepared is captured["prepared"] and "quality" in calls
        counter_ids = {id(prepared.api_status_counts), id(prepared.observed_tweet_transport_lane_counts)}
        assert not any(("items", identity) in calls for identity in counter_ids)
        result = original_report(prepared, **inputs)
        assert all(("items", identity) in calls for identity in counter_ids)
        assert result["has_5xx_failures"] is True
        assert result["status_counts"] == {"403": 1, "408": 1}
        assert result["errors"] is captured["inputs"]["api_errors"] and len(result["errors"]) == 2
        assert result["handled_restrictions"] is captured["inputs"]["handled_api_restrictions"]
        assert result["x_requests"] is captured["inputs"]["x_requests"]
        assert result["counter_semantics"] is prepared.api_counter_semantics
        assert result["post_cooldown_errors"] is prepared.post_cooldown_errors
        assert result["rate_limit_failure_count"] == 0
        calls.append("report")
        return result

    monkeypatch.setattr(digest, "Counter", CurrentCounter)
    monkeypatch.setattr(digest, "datetime", CurrentDatetime)
    monkeypatch.setattr(digest, "prepare_api_health", prepare)
    monkeypatch.setattr(digest, "historical_context_quality_summary", quality)
    monkeypatch.setattr(digest, "api_health_report", report)
    digest.analyse([
        record(0, "INFO", "fixture", "Entering API cooldown after repeated errors until 2026-07-25 09:00:00"),
        record(1, "INFO", "x_request", "X API error 408: fixture"),
        record(2, "INFO", "x_request", "X API error 403: not allowed to reply"),
    ])
    assert "report" in calls


def test_prepared_headlines_keep_current_callbacks_timing_and_shared_results(monkeypatch):
    calls, captured = [], {}
    media = [{"status": status} for status in ("handled", "reconciled", "failed")]
    records = [record(0, "INFO", "fixture", "prepared headline fixture")]
    state = {
        "time": digest.dt_text(BASE + timedelta(seconds=60)),
        "api_cooldown_until_epoch": int(BASE.timestamp()) + 120,
        "x_write_api_cooldown_until_epoch": int(BASE.timestamp()),
        "openai_api_cooldown_until_epoch": 0,
        "quote_api_cooldown_until_epoch": int(BASE.timestamp()) - 1,
        "daily_reply_count": "5", "daily_quote_reply_count": "2",
        "next_reply_lane_priority": ["shared priority"],
    }
    original_plural = digest.plural_count
    original_prepare = digest.prepare_headline_and_derived
    original_finalise = digest.prepare_reply_quality_headline
    helper_calls = []
    for name in ("plural_count", "int_or_none", "parse_dt"):
        original = getattr(digest, name)

        def helper(*args, _name=name, _original=original, **kwargs):
            helper_calls.append((_name, args))
            return _original(*args, **kwargs)

        monkeypatch.setattr(digest, name, helper)

    def prepare(**inputs):
        assert inputs["records"] is records
        for name in ("plural_count", "int_or_none", "parse_dt"):
            assert inputs[name] is getattr(digest, name)
        inputs["media_upload_incidents"].extend(media)
        inputs["latest_state_summary"].update(state)
        inputs["configs"].update(MAX_AUTO_REPLIES_PER_DAY="4", MAX_REPLIES_PER_AUTHOR_PER_DAY="3", MAX_QUOTE_REPLIES_PER_DAY="7")
        inputs["error_health"]["transient_provider_timeout_count"] = 2
        helper_calls.clear()
        result = original_prepare(**inputs)
        assert result[1] == 2
        assert "0 Grok skips" in result[0]
        assert result[0][-2:] == ["X read API cooldown active now", "X write API cooldown occurred, now expired"]
        assert [args[0] for name, args in helper_calls if name == "int_or_none"] == [
            state["api_cooldown_until_epoch"], state["x_write_api_cooldown_until_epoch"],
            0, state["quote_api_cooldown_until_epoch"], "4", "3", "7", "5", "2",
        ]
        assert [(name, args) for name, args in helper_calls if name == "parse_dt"] == [("parse_dt", (state["time"],))]
        assert helper_calls[0] == ("plural_count", (0, "quote/image post"))
        assert result[5]["reply_budget"]["auto_remaining"] == -1
        assert result[5]["reply_budget"]["quote_remaining"] == 5
        assert result[5]["reply_lane_priority"]["current_next_priority"] is state["next_reply_lane_priority"]
        captured.update(initial=result, inputs=inputs)
        calls.append("headline")
        return result

    def single_quality(events):
        calls.append("single quality")
        monkeypatch.setattr(digest, "plural_count", lambda *args: "current " + original_plural(*args))
        return {"candidate_evaluation_count": 1}

    def finalise(**inputs):
        assert inputs["headline"] is captured["initial"][0]
        assert inputs["plural_count"] is digest.plural_count
        result = original_finalise(**inputs)
        assert result[1] is not inputs["headline"]
        assert result[2] is not result[1]
        assert "0 Grok skips" in inputs["headline"]
        assert not any("Grok skip" in item for item in result[1])
        assert result[1][6].startswith("current 1 single-call candidate evaluated")
        captured["final"] = result
        calls.append("finalise")
        return result

    def watch(name, label):
        original = getattr(digest, name)

        def call(*args, **kwargs):
            calls.append(label)
            if label == "api":
                assert kwargs["transient_provider_timeouts"] == 2
            return original(*args, **kwargs)

        monkeypatch.setattr(digest, name, call)

    for name, label in (("prepare_api_health", "api"), ("prepare_inferred_reply_strategy_outcomes", "inference"), ("historical_context_quality_summary", "context quality"), ("prepare_mention_control_observations", "mention"), ("api_health_report", "api report")):
        watch(name, label)
    monkeypatch.setattr(digest, "prepare_headline_and_derived", prepare)
    monkeypatch.setattr(digest, "single_call_reply_summary", single_quality)
    monkeypatch.setattr(digest, "prepare_reply_quality_headline", finalise)
    report = digest.analyse(records, generation_time=BASE + timedelta(days=1))
    assert calls == ["headline", "api", "inference", "context quality", "single quality", "finalise", "mention", "api report"]
    for index, key in enumerate(("handled_fallbacks", "reconciled_incidents", "unrecovered_failures"), 2):
        assert report["media_upload"][key] is captured["initial"][index]
        assert report["media_upload"][key][0] is media[index - 2]
    assert report["media_upload"]["incidents"] is captured["inputs"]["media_upload_incidents"]
    assert report["derived"] is captured["initial"][5]
    assert report["legacy_multi_stage"] is captured["final"][0]
    assert report["summary"]["_headline_without_current_cooldown"] is captured["final"][2]


@pytest.mark.parametrize("health", [[], ["current health: supplied"]])
def test_reply_quality_headline_replaces_lists_and_excludes_each_cooldown_claim(health):
    claims = [
        "API cooldown occurred", "no API cooldown", "X read API cooldown active now",
        "X write API cooldown occurred, now expired", "OpenAI cooldown active now",
        "quote API cooldown active now", "current API cooldown state unavailable",
    ]
    headline = ["keep", "1 Grok skip", "2 Grok skips", *health, *claims]
    original = list(headline)
    legacy, replacement, base = state_reporting_owner.prepare_reply_quality_headline(
        events=[{"kind": "reply_strategy_decision"}, {"kind": "reply_pipeline_stage_summary"}],
        headline=headline, single_call_quality={"candidate_evaluation_count": 1},
        plural_count=lambda *args: str(args[1]),
    )
    assert headline == original and replacement is not headline and base is not replacement
    assert legacy == {"decision_count": 1, "stage_summary_event_count": 1}
    inserted = ["single-call candidate evaluated; reply posted; editorial no-reply decision; operational failure; one-call compliance None", "legacy multi-stage decisions 1; legacy stage summaries 1"]
    assert replacement == (["keep", *inserted, *health, *claims] if health else ["keep", *claims, *inserted])
    assert base == ["keep", *inserted, *health]


def test_x_request_endpoint_classification_is_path_and_method_specific():
    assert digest.parse_x_request_start(
        "X request: POST https://api.x.com/2/media/upload?command=INIT"
    )["endpoint"] == "media/upload"
    assert digest.parse_x_request_start(
        "X request: POST https://api.x.com/2/tweets"
    )["endpoint"] == "tweet/create"
    assert digest.parse_x_request_start(
        "X bearer request: GET https://api.x.com/2/users/123/mentions"
    )["endpoint"] == "mentions"
    assert digest.parse_x_request_start("unrelated") is None


def test_transaction_helpers_keep_current_callbacks_and_shared_record(monkeypatch):
    assert transaction_owner.Record is record_owner.Record is digest.Record
    calls = []

    def classify(method, url):
        calls.append((method, url))
        return "current-endpoint"

    monkeypatch.setattr(digest, "classify_x_request_endpoint", classify)
    assert digest.parse_x_request_start("unrelated") is None
    assert calls == []
    assert digest.parse_x_request_start("X request: POST https://example.test/2/tweets") == {
        "method": "POST", "url": "https://example.test/2/tweets",
        "endpoint": "current-endpoint",
    }
    assert calls == [("POST", "https://example.test/2/tweets")]
    calls.clear()

    def short(value, limit):
        calls.append((value, limit))
        return "current-text"

    monkeypatch.setattr(digest, "short", short)
    unmatched = record(0, "INFO", "fixture", "unrelated")
    assert digest.parse_remote_write_transaction_event(unmatched) is None
    matched = record(1, "INFO", "fixture", "Uploading receipt-bound media via X API v2: /tmp/a.png")
    assert digest.parse_remote_write_transaction_event(matched)["message"] == "current-text"
    assert calls == [(unmatched.msg, 500), (matched.msg, 500)]


def test_media_correlation_keeps_current_callbacks_source_order_and_identity(monkeypatch):
    records = [
        record(0, "ERROR", "x_request", "X request failed before receiving response"),
        record(1, "WARNING", "upload_media", "v2 media upload failed; trying v1.1 fallback"),
        record(2, "INFO", "upload_media_v1_1", "Uploaded media via v1.1."),
        record(3, "INFO", "fixture", "Quote/image posted successfully."),
    ]
    indexes = {"fixture.log": 7}
    calls = []
    refs = [{"record_number": i + 1} for i in range(4)]
    for name in (
        "is_media_fallback_warning", "is_media_v2_request_failure",
        "is_media_v1_success", "is_main_post_success", "is_media_v1_failure",
    ):
        original = getattr(digest, name)

        def observe(item, name=name, original=original):
            assert any(item is r for r in records)
            calls.append(name)
            return original(item)

        monkeypatch.setattr(digest, name, observe)

    def recent(items, index):
        assert items is records and index == 1
        return "current-path"

    def seconds(left, right):
        assert right is records[1].ts
        calls.append("seconds")
        return 0

    def fingerprint(item):
        index = next(i for i, r in enumerate(records) if item is r)
        calls.append(("fingerprint", index))
        return str(index)

    def source(item, supplied_indexes):
        assert supplied_indexes is indexes
        index = next(i for i, r in enumerate(records) if item is r)
        calls.append(("source", index))
        return refs[index]

    def bounded(*items):
        assert all(item is refs[index] for item, index in zip(items, (1, 0, 2, 3)))
        calls.append("bounded")
        return list(items), 2

    monkeypatch.setattr(digest, "find_recent_media_path", recent)
    monkeypatch.setattr(digest, "seconds_between", seconds)
    monkeypatch.setattr(digest, "record_fingerprint", fingerprint)
    monkeypatch.setattr(digest, "record_source_ref", source)
    monkeypatch.setattr(digest, "bounded_source_refs", bounded)
    monkeypatch.setattr(digest, "short", lambda value, limit: f"{limit}:{value}")
    incidents, suppressed = digest.correlate_media_upload_incidents(records, 19, indexes)
    incident = incidents[0]
    assert suppressed == {"0", "1", "2", "3"}
    assert incident["status"] == "handled" and incident["media"] == "current-path"
    assert incident["fallback"] == f"19:{records[1].msg}"
    assert incident["v2_failure"] == f"19:{records[0].msg}"
    assert incident["source_ref_omitted_count"] == 2
    assert incident["source_refs"][0] is refs[1]
    assert calls.count("seconds") == 3
    assert {name for name in calls if isinstance(name, str)} >= {
        "is_media_fallback_warning", "is_media_v2_request_failure",
        "is_media_v1_success", "is_main_post_success", "is_media_v1_failure",
    }
    assert [call for call in calls if isinstance(call, tuple)] == [
        *(("fingerprint", i) for i in (1, 0, 2, 3)),
        *(("source", i) for i in (1, 0, 2, 3)),
    ]
    assert calls.index("bounded") > calls.index(("source", 3))


def test_post_scan_media_preparation_keeps_current_callbacks_and_error_rebinding(monkeypatch):
    records = [
        record(0, "ERROR", "fixture", "Missing X credentials."),
        record(0, "ERROR", "fixture", "SELFTEST FAIL: fixture credentials"),
        record(1, "ERROR", "fixture", "suppressed media error"),
        record(2, "ERROR", "x_request", 'X API error 403: {"detail":"You attempted to reply to a Tweet that is deleted or not visible to you."}'),
        record(3, "ERROR", "post_generated_reply", "Failed to post generated reply"),
        record(8, "ERROR", "post_generated_reply", "Failed to post generated reply after six seconds"),
        *ambiguous_media_records(),
    ]
    indexes = {"fixture.log": 4}
    incidents = []
    calls = []
    captured = {}
    original_parse = digest.parse_dt
    original_seconds = digest.seconds_between

    def parse(value):
        calls.append("parse")
        return original_parse(value)

    class CurrentDatetime(datetime):
        @classmethod
        def strptime(cls, value, fmt):
            calls.append("strptime")
            return datetime.strptime(value, fmt)

    def seconds(left, right):
        calls.append("seconds")
        return original_seconds(left, right)

    def correlate(items, max_text, input_file_indexes):
        assert items is records and max_text == 37 and input_file_indexes is indexes
        calls.append("correlate")
        return incidents, {digest.record_fingerprint(records[2])}

    original_prepare = digest.prepare_media_incidents_and_errors

    def prepare(**inputs):
        assert inputs["correlate_media_upload_incidents"] is correlate
        assert inputs["parse_dt"] is parse and inputs["seconds_between"] is seconds
        assert inputs["strptime"] == CurrentDatetime.strptime
        assert inputs["self_test_times"] == {digest.dt_text(BASE)}
        assert inputs["handled_restriction_times"] == [BASE + timedelta(seconds=2)]
        before = list(inputs["errors"])
        calls.clear()
        result = original_prepare(**inputs)
        assert calls[0] == "correlate"
        assert {"parse", "strptime", "seconds"} <= set(calls)
        assert result[0] is incidents and result[1] is not inputs["errors"]
        assert all(left is right for left, right in zip(before, inputs["errors"]))
        assert inputs["self_test_errors"][-1] is before[0]
        assert before[1] not in result[1] and before[2] not in result[1]
        assert any(item is before[3] for item in result[1])
        captured["errors"] = result[1]
        return result

    original_health = digest.summarise_operational_error_health

    def health(errors, *args, **kwargs):
        assert errors is captured["errors"]
        return original_health(errors, *args, **kwargs)

    monkeypatch.setattr(digest, "datetime", CurrentDatetime)
    monkeypatch.setattr(digest, "parse_dt", parse)
    monkeypatch.setattr(digest, "seconds_between", seconds)
    monkeypatch.setattr(digest, "correlate_media_upload_incidents", correlate)
    monkeypatch.setattr(digest, "prepare_media_incidents_and_errors", prepare)
    monkeypatch.setattr(digest, "summarise_operational_error_health", health)
    report = digest.analyse(records, max_text=37, input_file_indexes=indexes)
    assert report["media_upload"]["incidents"] is incidents


def test_pending_reply_receipt_errors_keep_latest_match_order_and_shared_sources():
    def receipt(kind, lane, target, reply="", **extra):
        return {
            "kind": kind, "lane": lane, "target_id": target, "reply_post_id": reply,
            "time": str(len(receipts)), "source_refs": [{"record_number": len(receipts)}],
            **extra,
        }

    receipts = []
    for fields in [
        ("sending", "quote_tweet", "2"),
        ("sending", "mention", "1"),
        ("sending", "mention", "1"),
        ("promoted", "mention", "1"),
        ("removed", "mention", "unmatched", "9"),
        ("reconciled", "mention", "1", "9"),
        ("reconciled", "mention", "1", "9"),
        ("removed", "mention", "1", "9"),
        ("reconciled", "quote_tweet", "2", "8"),
        ("reconciled", "mention", "3", "7"),
        ("replay_suppressed_mention_check", "mention", ""),
    ]:
        receipts.append(receipt(*fields))
    receipts.append(receipt("sending_removed", "mention", "1", source_class="selftest"))
    receipts.append(receipt("removed", "quote_tweet", "2", "8", source_class="selftest"))
    original_receipts = list(receipts)
    existing = {"message": "earlier error"}
    errors = [existing]

    result = transaction_owner.append_unresolved_reply_receipt_errors(
        confirmed_reply_receipts=receipts, errors=errors,
    )

    assert result is None and errors[0] is existing
    assert len(errors) == 5
    assert all(left is right for left, right in zip(receipts, original_receipts))
    for error, source in zip(errors[1:], (receipts[i] for i in (1, 0, 5, 8))):
        assert error["time"] == source["time"]
        assert error["source_refs"] is not source["source_refs"]
        assert error["source_refs"][0] is source["source_refs"][0]


def test_prepared_reply_recovery_keeps_health_order_callbacks_and_nested_lists(monkeypatch):
    records = [record(
        0, "WARNING", "write_sending_reply_receipt",
        "Wrote conversational reply sending receipt source=mention target_id=123 path=/tmp/reply.json",
    )]
    component = {
        "receipt_roles": ["conversational_confirmed_reply"],
        "lanes": ["conversational_reply", "mention"],
        "transaction_ids": ["a" * 64], "target_ids": [], "artifact_names": ["reply.json"],
    }
    safety = {"available": False}
    calls = []
    captured = {}
    original_health = digest.summarise_operational_error_health
    original_parse = digest.parse_dt
    original_lane = digest._normalise_lane
    labels = {"conversational_confirmed_reply": "current role label"}

    def health(errors, *args, **kwargs):
        assert errors[-1]["where"] == "confirmed_reply_receipt_lifecycle"
        result = original_health(errors, *args, **kwargs)
        result["historical_resolved_incidents"] = [{
            "category": "remote_write_ambiguity_barrier", "resolution_time": "resolved",
            "correlated_reply_receipt_events": [
                {"lane": "mention", "target_id": "123", "source_time": "source"},
                {"source_time": "invalid"}, {"source_time": "later"}, None,
            ],
        }]
        result["resolution_unavailable_incidents"] = [{
            "lane": "MENTION", "target_id": "123", "resolution_reason": "unavailable",
        }]
        safety["active_transaction_identities"] = [component]
        captured["health"] = result
        calls.append("health")
        return result

    def parse(value):
        if value == "invalid":
            calls.append(value)
            raise ValueError(value)
        if value in {"resolved", "source", "later"}:
            calls.append(value)
            return BASE + timedelta(seconds={"source": 0, "resolved": 1, "later": 2}[value])
        return original_parse(value)

    def lane(value):
        calls.append(("lane", value))
        return original_lane(str(value).lower())

    original_prepare = digest.prepare_reply_receipt_recovery_reporting

    def prepare(**inputs):
        assert inputs["error_health"] is captured["health"]
        assert inputs["current_remote_write_safety"] is safety
        assert inputs["parse_dt"] is parse and inputs["_normalise_lane"] is lane
        assert inputs["REMOTE_WRITE_RECEIPT_ROLE_LABELS"] is labels
        calls.clear()
        result = original_prepare(**inputs)
        assert calls == ["resolved", "source", "invalid", "later", ("lane", "mention"), ("lane", "MENTION")]
        captured["inputs"] = inputs
        captured["result"] = result
        return result

    monkeypatch.setattr(digest, "summarise_operational_error_health", health)
    monkeypatch.setattr(digest, "parse_dt", parse)
    monkeypatch.setattr(digest, "_normalise_lane", lane)
    monkeypatch.setattr(digest, "REMOTE_WRITE_RECEIPT_ROLE_LABELS", labels)
    monkeypatch.setattr(digest, "prepare_reply_receipt_recovery_reporting", prepare)
    recovery = digest.analyse(records, current_remote_write_safety=safety)["confirmed_reply_recovery"]
    for key, result in zip(
        ("durably_reconciled_ambiguity_receipts", "status_unavailable_receipts", "active_snapshot_receipts"),
        captured["result"],
    ):
        assert recovery[key] is result and len(result) == 1
    assert recovery["status_unavailable_receipts"][0]["source_time"] == digest.dt_text(BASE)
    active = recovery["active_snapshot_receipts"][0]
    assert active["receipt_role_label"] == "current role label" and active["lane"] == "mention"
    assert active["transaction_ids"] is component["transaction_ids"]
    assert active["artifact_names"] is component["artifact_names"]
    assert active["target_ids"] == [] and active["target_ids"] is not component["target_ids"]
    with pytest.raises(TypeError):
        original_prepare(**{**captured["inputs"], "parse_dt": lambda value: None})


def test_request_transaction_projection_keeps_parser_event_and_callback_order(monkeypatch):
    r = record(0, "INFO", "x_request", "X request: POST https://example.test/2/tweets")
    indexes = {r.path: 3}
    requests, transactions, receipt_calls, trace = [], [], [], []
    latest, stats = {}, Counter()
    source_ref = {"record_number": 1}
    request = {"endpoint": "tweet/create", "extra": []}
    transaction = {"kind": "main_post_receipt", "phase": "attempting", "lane": []}

    def source(item, supplied_indexes):
        assert item is r and supplied_indexes is indexes
        trace.append("source")
        return source_ref

    def receipt(kind, item, **kwargs):
        assert item is r and transactions[-1] is transaction
        assert stats["remote_write_transaction_attempting"] == 1
        assert kwargs["lane"] is transaction["lane"]
        receipt_calls.append((kind, kwargs))

    transaction_owner.record_x_request_start(
        request, r, input_file_indexes=indexes, x_requests=requests,
        latest_x_request_by_source=latest, stats=stats, record_source_ref=source,
    )
    assert requests[0] is latest[r.src] and requests[0] is not request
    assert requests[0]["extra"] is request["extra"]
    transaction_owner.record_remote_write_transaction(
        transaction, r, input_file_indexes=indexes,
        remote_write_transactions=transactions, stats=stats,
        record_source_ref=source, add_receipt_event=receipt,
    )
    assert transactions[0] is transaction
    assert transaction["source_refs"][0] is requests[0]["source_refs"][0] is source_ref
    assert receipt_calls == [("main_post_receipt", {"phase": "attempting", "lane": []})]
    assert stats == {"x_request_endpoint_tweet_create": 1, "remote_write_transaction_attempting": 1}

    def parse_request(message):
        trace.append("parse request")
        return request

    def project_request(*args, **kwargs):
        assert args[0] is request and args[1] is r
        trace.append("project request")

    def parse_transaction(item):
        assert item is r
        trace.append("parse transaction")
        return transaction

    def project_transaction(*args, **kwargs):
        assert args[0] is transaction and args[1] is r
        trace.append("project transaction")

    monkeypatch.setattr(digest, "parse_x_request_start", parse_request)
    monkeypatch.setattr(digest, "record_x_request_start", project_request)
    monkeypatch.setattr(digest, "parse_remote_write_transaction_event", parse_transaction)
    monkeypatch.setattr(digest, "record_remote_write_transaction", project_transaction)
    trace.clear()
    selftest = digest.Record(
        r.ts, r.level, r.src, r.line, r.msg, "fixture.selftest.log", r.ordinal,
    )
    digest.analyse([r, selftest])
    assert trace == ["parse request", "project request", "parse transaction", "project transaction"]


def test_current_remote_write_transaction_lifecycle_shapes_are_parsed():
    transaction_id = "a" * 64
    cases = [
        (
            "Creating X post with durable transport journal. lane=quote_image "
            f"transaction_id={transaction_id} reply_to_id=none media_count=1 "
            "made_with_ai=false",
            ("tweet_transport", "request_started"),
        ),
        (
            "Wrote main-post sending receipt lane=quote_image attempt_id=attempt-1 "
            "path=/srv/regular_post_receipt.json",
            ("main_post_receipt", "sending_published"),
        ),
        (
            "Promoted main-post receipt to attempting lane=quote_image "
            "attempt_id=attempt-1 path=/srv/regular_post_receipt.json",
            ("main_post_receipt", "attempting"),
        ),
        (
            "Handed confirmed media upload to durable main-post attempt "
            "lane=quote_image attempt_id=attempt-1 media_id=999",
            ("media_upload", "confirmed_handoff"),
        ),
        (
            "Promoted main-post attempt to confirmed pending-schedule receipt "
            "lane=quote_image attempt_id=attempt-1 post_id=123 "
            "path=/srv/regular_post_receipt.json",
            ("main_post_receipt", "confirmed_pending_schedule"),
        ),
        (
            "Removed main-post sending receipt disposition=definite_non_success "
            "lane=quote_image attempt_id=attempt-1 "
            "path=/srv/regular_post_receipt.json",
            ("main_post_receipt", "sending_retired"),
        ),
        (
            "Finalised confirmed pending-schedule receipt lane=quote_image "
            "post_id=123 path=/srv/regular_post_receipt.json",
            ("main_post_receipt", "schedule_finalised"),
        ),
        (
            "Resumed interrupted exact source-receipt retirement "
            "path=/srv/regular_post_receipt.json phase=exchange",
            ("source_receipt_retirement", "exchange"),
        ),
        (
            "Resumed interrupted confirmed-media fence retirement "
            f"lane=quote_image media_transaction_id={transaction_id} media_id=999",
            ("media_retirement", "resumed"),
        ),
        (
            "Recovered crash-left permanent retirement-ledger exchanges count=1",
            ("retirement_ledger", "exchange_recovered"),
        ),
    ]

    for offset, (message, expected) in enumerate(cases):
        parsed = digest.parse_remote_write_transaction_event(
            record(offset, "INFO", "fixture", message)
        )
        assert parsed is not None
        assert (parsed["kind"], parsed["phase"]) == expected


def test_one_root_incident_groups_several_tracebacks_and_resolves():
    root = (
        "RuntimeError: historical-context source-role audit policy is incompatible"
    )
    records = [
        record(
            0,
            "ERROR",
            "post_historical_context_reply",
            traceback("Historical context reply failed independently", root),
        ),
        record(
            1,
            "ERROR",
            "post_quote_image",
            traceback("Quote/image posting failed unexpectedly", root),
        ),
        record(
            2,
            "ERROR",
            "scheduler",
            traceback("Auxiliary worker failed", root),
        ),
        loaded_gate(10),
    ]

    report = digest.analyse(records)
    health = report["error_health"]

    assert health["current_independent_incident_count"] == 0
    assert health["historical_resolved_incident_count"] == 1
    assert health["raw_serious_error_record_count"] == 3
    assert health["raw_traceback_count"] == 3
    incident = health["historical_resolved_incidents"][0]
    assert incident["category"] == "historical_context_source_role_incompatibility"
    assert incident["record_count"] == 3
    assert "no unresolved operational incidents" in report["summary"]["headline"]


def test_mixed_historical_and_current_errors_are_separated():
    records = [
        record(
            0,
            "ERROR",
            "post_historical_context_reply",
            traceback(
                "Historical context reply failed independently",
                "RuntimeError: historical-context source-role audit policy is incompatible",
            ),
        ),
        loaded_gate(10),
        record(
            20,
            "ERROR",
            "daily_worker",
            traceback("Current worker failed", "ValueError: current fixture failure"),
        ),
    ]

    report = digest.analyse(records)
    health = report["error_health"]
    rendered = digest.render_markdown(report)

    assert health["current_independent_incident_count"] == 1
    assert health["historical_resolved_incident_count"] == 1
    assert "1 unresolved operational incident" in report["summary"]["headline"]
    assert "## Current independent errors" in rendered
    assert "## Historical/resolved incident errors" in rendered
    assert "source role incompatibility" in rendered


def test_process_crash_is_historical_after_later_successful_startup():
    records = [
        record(
            0,
            "ERROR",
            "<module>",
            traceback(
                "Bot crashed with unhandled exception",
                "RuntimeError: Missing X credentials.",
            ),
        ),
        record(10, "INFO", "main", "Bot started successfully"),
    ]

    report = digest.analyse(records)

    assert report["error_health"]["current_independent_incident_count"] == 0
    incident = report["error_health"]["historical_resolved_incidents"][0]
    assert incident["category"] == "process_crash"
    assert incident["resolution_reason"] == "later successful bot startup observed"


def test_deleted_or_inaccessible_tweet_403_is_handled_separately():
    records = [
        record(
            0,
            "ERROR",
            "x_request",
            'X API error 403: {"detail":"You attempted to reply to a Tweet '
            'that is deleted or not visible to you."}',
        ),
        record(
            1,
            "ERROR",
            "post_generated_reply",
            "Failed to post generated reply",
        ),
    ]

    report = digest.analyse(records)
    rendered = digest.render_markdown(report)

    assert report["error_health"]["current_independent_incident_count"] == 0
    assert report["api_health"]["deleted_or_inaccessible_tweet_403_count"] == 1
    assert report["api_health"]["target_eligibility_403_count"] == 0
    assert "deleted/inaccessible-tweet 403 responses: **1**" in rendered
    assert "deleted or inaccessible tweet" in rendered


def _retired_observer_event_name() -> str:
    return "_".join(("quote", "image", "semantic", "veto", "shadow"))


def _retired_load_lifecycle_field() -> str:
    return "_".join(("semantic", "veto", "load", "lifecycle"))


def _normal_main_post_record() -> digest.Record:
    return record(
        0,
        "INFO",
        "log_event",
        "EVENT "
        + json.dumps(
            {
                "event": "main_post_posted",
                "lane": "quote_image",
                "post_id": "123",
                "quote_hash": "a" * 64,
                "image_hash": "b" * 64,
                "image_basename": "t01.jpg",
            }
        ),
    )


def test_normal_main_post_analysis_has_no_retired_observer_output() -> None:
    report = digest.analyse([_normal_main_post_record()])

    assert report["summary"]["record_count"] == 1
    assert _retired_observer_event_name() not in report
    assert _retired_load_lifecycle_field() not in report
    rendered = digest.render_markdown(report)
    retired_heading = "## Quote/image " + "semantic veto shadow"
    assert retired_heading not in rendered


def test_old_retired_observer_event_is_ignored_safely() -> None:
    historical = {
        "event": _retired_observer_event_name(),
        "quote_hash": "a" * 64,
        "selected_image_hash": "b" * 64,
        "selected_image_basename": "t01.jpg",
        "shadow_status": "veto",
        "would_" + "veto_production_winner": True,
    }
    report = digest.analyse(
        [
            record(0, "INFO", "log_event", "EVENT " + json.dumps(historical)),
            record(1, "INFO", "log_event", _normal_main_post_record().msg),
        ]
    )

    assert report["summary"]["record_count"] == 2
    assert all(
        event.get("kind") != _retired_observer_event_name()
        for event in report["events"]
    )
    assert _retired_observer_event_name() not in report
    assert _retired_load_lifecycle_field() not in report


def test_digest_does_not_load_retired_observer_code_or_runtime_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module_name = ".".join(
        ("semantic_alignment", "_".join(("quote", "image", "semantic", "veto")))
    )
    runtime_name = "_".join(("quote", "image", "semantic", "veto", "runtime"))
    runtime_dir = tmp_path / runtime_name
    runtime_dir.mkdir()
    (runtime_dir / "shadow_status.json").write_text("{invalid", encoding="utf-8")
    log_path = tmp_path / "fixture.log"
    log_path.write_text(
        "2026-07-25 09:00:00 INFO main:1 - Main loop tick\n",
        encoding="utf-8",
    )
    imported: list[str] = []
    read_paths: list[Path] = []
    original_import = builtins.__import__
    original_read_bytes = Path.read_bytes

    def guarded_import(name, *args, **kwargs):
        imported.append(str(name))
        if name == module_name:
            raise AssertionError("retired observer module import attempted")
        return original_import(name, *args, **kwargs)

    def tracked_read_bytes(path: Path) -> bytes:
        read_paths.append(path)
        return original_read_bytes(path)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(Path, "read_bytes", tracked_read_bytes)

    result = digest.main(
        [
            "--project-dir",
            str(tmp_path),
            "--no-state",
            "--json",
            str(log_path),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert module_name not in imported
    assert not any(runtime_name in path.parts for path in read_paths)
    assert _retired_observer_event_name() not in payload
    assert _retired_load_lifecycle_field() not in payload


def test_reply_summary_classifies_declines_duplicates_and_posted_modes():
    events = []
    reasons = (
        ["exact_duplicate_reply"] * 3
        + ["no substantive prompt"] * 2
        + ["editorially declined"] * 4
    )
    for index, reason in enumerate(reasons):
        events.append({
            "kind": "reply_strategy_decision",
            "lane": "mention",
            "target_id": f"decline-{index}",
            "mode": "no_reply",
            "no_reply_reason": reason,
            "factual_claim": False,
            "grounded": False,
        })
    for index in range(2):
        events.append({
            "kind": "reply_strategy_decision",
            "lane": "mention",
            "target_id": f"posted-{index}",
            "mode": "opinion_or_principle",
            "factual_claim": False,
            "grounded": False,
        })
        events.append({
            "kind": "reply_strategy_outcome",
            "status": "posted",
            "lane": "mention",
            "target_id": f"posted-{index}",
            "reply_post_id": f"reply-{index}",
            "mode": "opinion_or_principle",
            "factual_claim": False,
            "grounded": False,
        })

    summary = digest.reply_strategy_summary(events)
    assert summary["conversational_candidate_count"] == 11
    assert summary["confirmed_outcome_count"] == 2
    assert summary["deliberately_declined_count"] == 6
    assert summary["terminal_repetition_rejection_count"] == 3
    assert summary["outcome_status_counts"] == {
        "posted": 2,
        "terminal_no_reply": 6,
        "terminal_repetition_rejection": 3,
    }
    assert summary["no_reply_category_counts"] == {
        "no_substantive_prompt": 2,
        "low_value_or_repetitive_engagement": 4,
    }
    assert summary["repetition_control_counts"]["exact_duplicate_rejected"] == 3
    assert summary["claim_free_opinion_or_principle_count"] == 2
    assert summary["humour_reply_count"] == 0


def test_pipeline_failure_and_apierror_wrapper_are_one_current_incident():
    target_id = "2086177789732958385"
    records = [
        record(
            0,
            "INFO",
            "log_event",
            'EVENT {"event":"ai_reply_pipeline_failure",'
            '"status":"operational_failure","lane":"mention",'
            f'"target_id":"{target_id}",'
            '"reason":"no_reply_reviewer_invalid"}',
        ),
        record(
            0,
            "ERROR",
            "maybe_reply_to_mentions",
            traceback(
                "Failed to ask Grok for reply",
                "APIError: reviewer returned an invalid no-reply verdict",
            ),
        ),
    ]

    report = digest.analyse(records)
    health = report["error_health"]

    assert health["current_independent_incident_count"] == 1
    assert health["historical_resolved_incident_count"] == 0
    assert health["raw_serious_error_record_count"] == 1
    assert health["raw_traceback_count"] == 1
    incident = health["current_incidents"][0]
    assert incident["category"] == "reply_strategy_pipeline_failure"
    assert incident["lane"] == "mention"
    assert incident["target_id"] == target_id
    assert incident["pipeline_failure_event_count"] == 1
    assert incident["wrapper_record_count"] == 1
    assert incident["record_count"] == 1
    assert incident["pipeline_failure_reason_counts"] == {
        "no_reply_reviewer_invalid": 1
    }
    raw_errors = "\n".join(
        row["message"] for row in report["errors_and_warnings"]
    )
    assert "Failed to ask Grok for reply" in raw_errors
    assert "APIError" in raw_errors
    assert "Traceback" in raw_errors


def test_pipeline_failure_raw_evidence_resolves_as_one_incident():
    target_id = "2086177789732958385"
    records = [
        record(
            0,
            "ERROR",
            "build_ai_reply",
            "AI-first reply pipeline ended status=operational_failure lane=mention "
            f"target_id={target_id} reason=no_reply_reviewer_invalid "
            "calls=3 revisions=0",
        ),
        record(
            0,
            "INFO",
            "log_event",
            'EVENT {"event":"ai_reply_pipeline_failure",'
            '"status":"operational_failure","lane":"mention",'
            f'"target_id":"{target_id}",'
            '"reason":"no_reply_reviewer_invalid"}',
        ),
        record(
            0,
            "ERROR",
            "maybe_reply_to_mentions",
            traceback(
                "Failed to ask Grok for reply",
                "APIError: reviewer returned an invalid no-reply verdict",
            ),
        ),
        record(
            10,
            "INFO",
            "log_event",
            'EVENT {"event":"ai_reply_pipeline_decision",'
            '"status":"no_reply","lane":"mention",'
            f'"target_id":"{target_id}","mode":"no_reply",'
            '"reviewer_verdict":"confirm_no_reply",'
            '"reason":"independent_no_reply_confirmed"}',
        ),
    ]

    report = digest.analyse(records)
    health = report["error_health"]

    assert health["current_independent_incident_count"] == 0
    assert health["historical_resolved_incident_count"] == 1
    incident = health["historical_resolved_incidents"][0]
    assert incident["category"] == "reply_strategy_pipeline_failure"
    assert incident["record_count"] == 2
    assert incident["wrapper_record_count"] == 1
    assert incident["pipeline_failure_event_count"] == 1
    assert incident["traceback_count"] == 1
    assert incident["pipeline_failure_reason_counts"] == {
        "no_reply_reviewer_invalid": 1
    }
    raw_errors = "\n".join(
        row["message"] for row in report["errors_and_warnings"]
    )
    assert "AI-first reply pipeline ended" in raw_errors
    assert "Failed to ask Grok for reply" in raw_errors
    assert "APIError" in raw_errors
    assert "Traceback" in raw_errors


def test_pipeline_failure_raw_error_without_structured_match_stays_independent():
    target_id = "2086177789732958385"
    report = digest.analyse(
        [
            record(
                0,
                "ERROR",
                "build_ai_reply",
                "AI-first reply pipeline ended status=operational_failure "
                f"lane=mention target_id={target_id} "
                "reason=no_reply_reviewer_invalid calls=3 revisions=0",
            )
        ]
    )

    health = report["error_health"]
    assert health["current_independent_incident_count"] == 1
    assert health["historical_resolved_incident_count"] == 0
    assert health["current_incidents"][0]["record_count"] == 1
    raw_errors = "\n".join(
        row["message"] for row in report["errors_and_warnings"]
    )
    assert "AI-first reply pipeline ended" in raw_errors


def test_pipeline_failure_wrapper_resolves_after_same_target_terminal_no_reply():
    target_id = "2086177789732958385"
    records = [
        record(
            0,
            "INFO",
            "log_event",
            'EVENT {"event":"ai_reply_pipeline_failure",'
            '"status":"operational_failure","lane":"mention",'
            f'"target_id":"{target_id}",'
            '"reason":"no_reply_reviewer_invalid"}',
        ),
        record(
            0,
            "ERROR",
            "maybe_reply_to_mentions",
            traceback(
                "Failed to ask Grok for reply",
                "APIError: reviewer returned an invalid no-reply verdict",
            ),
        ),
        record(
            10,
            "INFO",
            "log_event",
            'EVENT {"event":"ai_reply_pipeline_decision",'
            '"status":"no_reply","lane":"mention",'
            f'"target_id":"{target_id}","mode":"no_reply",'
            '"reviewer_verdict":"confirm_no_reply",'
            '"reason":"independent_no_reply_confirmed"}',
        ),
    ]

    report = digest.analyse(records)
    health = report["error_health"]
    rendered = digest.render_markdown(report)

    assert health["current_independent_incident_count"] == 0
    assert health["historical_resolved_incident_count"] == 1
    incident = health["historical_resolved_incidents"][0]
    assert incident["category"] == "reply_strategy_pipeline_failure"
    assert incident["target_id"] == target_id
    assert incident["record_count"] == 1
    assert incident["resolution_reason"] == (
        "later terminal no-reply decision observed for mention target " + target_id
    )
    assert incident["resolution_time"] == "2026-07-25 09:00:10"
    assert "None unresolved in the selected window." in rendered
    assert "reply strategy pipeline failure" in rendered
    raw_errors = "\n".join(
        row["message"] for row in report["errors_and_warnings"]
    )
    assert "Failed to ask Grok for reply" in raw_errors
    assert "APIError" in raw_errors
    assert "Traceback" in raw_errors


def test_later_terminal_pipeline_failure_does_not_resolve_pipeline_incident():
    target_id = "2086177789732958385"
    records = [
        record(
            0,
            "INFO",
            "log_event",
            'EVENT {"event":"ai_reply_pipeline_failure",'
            '"status":"operational_failure","lane":"mention",'
            f'"target_id":"{target_id}","reason":"writer_invalid"}}',
        ),
        record(
            0,
            "ERROR",
            "maybe_reply_to_mentions",
            traceback(
                "Failed to ask Grok for reply",
                "APIError: writer returned an invalid reply",
            ),
        ),
        record(
            10,
            "INFO",
            "log_event",
            'EVENT {"event":"ai_reply_pipeline_decision",'
            '"status":"no_reply","lane":"mention",'
            f'"target_id":"{target_id}","mode":"no_reply",'
            '"reason":"revision_limit_reached"}',
        ),
    ]

    health = digest.analyse(records)["error_health"]

    assert health["current_independent_incident_count"] == 1
    assert health["historical_resolved_incident_count"] == 0
    assert health["current_incidents"][0]["target_id"] == target_id


@pytest.mark.parametrize("duplicate_reason", ["exact_duplicate_reply", "near_duplicate_reply"])
def test_pipeline_failure_wrapper_resolves_after_terminal_duplicate_decision(
    duplicate_reason: str,
):
    target_id = "2086177789732958385"
    records = [
        record(
            0,
            "INFO",
            "log_event",
            'EVENT {"event":"ai_reply_pipeline_failure",'
            '"status":"operational_failure","lane":"mention",'
            f'"target_id":"{target_id}",'
            '"reason":"no_reply_reviewer_invalid"}',
        ),
        record(
            0,
            "ERROR",
            "maybe_reply_to_mentions",
            traceback(
                "Failed to ask Grok for reply",
                "APIError: reviewer returned an invalid no-reply verdict",
            ),
        ),
        record(
            10,
            "INFO",
            "log_event",
            'EVENT {"event":"ai_reply_pipeline_decision",'
            '"status":"no_reply","lane":"mention",'
            f'"target_id":"{target_id}","mode":"courtesy",'
            f'"reason":"{duplicate_reason}","reviewer_verdict":"not_run"}}',
        ),
    ]

    report = digest.analyse(records)
    health = report["error_health"]

    assert health["current_independent_incident_count"] == 0
    assert health["historical_resolved_incident_count"] == 1
    incident = health["historical_resolved_incidents"][0]
    assert incident["category"] == "reply_strategy_pipeline_failure"
    assert incident["lane"] == "mention"
    assert incident["target_id"] == target_id
    assert incident["pipeline_failure_event_count"] == 1
    assert incident["wrapper_record_count"] == 1
    assert incident["record_count"] == 1
    assert incident["resolution_reason"] == (
        "later terminal local decision observed for mention target " + target_id
    )
    assert incident["resolution_time"] == "2026-07-25 09:00:10"
    raw_errors = "\n".join(
        row["message"] for row in report["errors_and_warnings"]
    )
    assert "Failed to ask Grok for reply" in raw_errors
    assert "APIError" in raw_errors
    assert "Traceback" in raw_errors


def test_hot_post_pipeline_wrapper_from_combined_handler_resolves_once():
    target_id = "2086177789732958386"
    records = [
        record(
            0,
            "INFO",
            "log_event",
            'EVENT {"event":"ai_reply_pipeline_failure",'
            '"status":"operational_failure","lane":"hot-post",'
            f'"target_id":"{target_id}",'
            '"reason":"no_reply_reviewer_invalid"}',
        ),
        record(
            0,
            "ERROR",
            "maybe_reply_to_mentions",
            traceback(
                "Failed to ask Grok for reply",
                "APIError: reviewer returned an invalid no-reply verdict",
            ),
        ),
        record(
            10,
            "INFO",
            "log_event",
            'EVENT {"event":"ai_reply_pipeline_decision",'
            '"status":"no_reply","lane":"hot-post",'
            f'"target_id":"{target_id}","mode":"courtesy",'
            '"reason":"exact_duplicate_reply","reviewer_verdict":"not_run"}',
        ),
    ]

    report = digest.analyse(records)
    health = report["error_health"]

    assert health["current_independent_incident_count"] == 0
    assert health["historical_resolved_incident_count"] == 1
    incident = health["historical_resolved_incidents"][0]
    assert incident["category"] == "reply_strategy_pipeline_failure"
    assert incident["lane"] == "hot-post"
    assert incident["target_id"] == target_id
    assert incident["pipeline_failure_event_count"] == 1
    assert incident["wrapper_record_count"] == 1
    assert incident["record_count"] == 1
    assert incident["resolution_reason"] == (
        "later terminal local decision observed for hot-post target " + target_id
    )


def test_unrelated_apierror_remains_independent_of_resolved_pipeline_wrapper():
    target_id = "2086177789732958385"
    report = digest.analyse(
        [
            record(
                0,
                "INFO",
                "log_event",
                'EVENT {"event":"ai_reply_pipeline_failure",'
                '"status":"operational_failure","lane":"mention",'
                f'"target_id":"{target_id}",'
                '"reason":"no_reply_reviewer_invalid"}',
            ),
            record(
                0,
                "ERROR",
                "maybe_reply_to_mentions",
                traceback(
                    "Failed to ask Grok for reply",
                    "APIError: reviewer returned an invalid no-reply verdict",
                ),
            ),
            record(
                10,
                "INFO",
                "log_event",
                'EVENT {"event":"ai_reply_pipeline_decision",'
                '"status":"no_reply","lane":"mention",'
                f'"target_id":"{target_id}","mode":"no_reply",'
                '"reviewer_verdict":"confirm_no_reply",'
                '"reason":"independent_no_reply_confirmed"}',
            ),
            record(
                20,
                "ERROR",
                "unrelated_worker",
                traceback(
                    "Unrelated provider operation failed",
                    "APIError: unrelated fixture failure",
                ),
            ),
        ]
    )

    health = report["error_health"]
    assert health["current_independent_incident_count"] == 1
    assert health["historical_resolved_incident_count"] == 1
    assert health["current_incidents"][0]["category"] == "apierror"
    assert health["current_incidents"][0]["record_count"] == 1
    assert health["raw_serious_error_record_count"] == 2


def carried_state_report(state_timestamp: str | None) -> dict:
    report = digest.analyse([])
    report["summary"]["time_start"] = "2026-07-25 09:00:00"
    report["summary"]["time_end"] = "2026-07-25 12:00:00"
    report["latest_state"] = {
        "time": state_timestamp,
        "_carried_forward": True,
        "daily_reply_date": "2026-07-24",
        "daily_reply_count": 7,
        "daily_quote_reply_date": "2026-07-24",
        "daily_quote_reply_count": 3,
        "next_reply_lane_priority": "quote-tweet",
        "next_quote_post_epoch": 1784984400,
        "next_quote_post_human": "2026-07-25 13:00:00",
        "next_meme_post_epoch": 1784988000,
        "next_meme_post_human": "2026-07-25 14:00:00",
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": "2026-07-25",
    }
    report["latest_config"] = {
        "MAX_AUTO_REPLIES_PER_DAY": "12",
        "MAX_QUOTE_REPLIES_PER_DAY": "6",
        "ENABLE_DAILY_MEME_POSTS": "true",
    }
    digest.refresh_derived(report)
    return report


def test_carried_forward_state_before_window_is_stale_snapshot():
    rendered = digest.render_markdown(
        carried_state_report("2026-07-24 09:00:00")
    )

    assert "## Latest state (stale carried-forward snapshot)" in rendered
    assert "stale snapshot age at window end: 1 day 3 hours" in rendered
    assert "counters and schedules below are not current" in rendered
    assert "snapshot_daily_reply_count       = 7" in rendered
    assert "snapshot_next_quote_post" in rendered
    assert "snapshot_next_meme_post" in rendered
    assert "snapshot_next_meme" in rendered
    assert "current_next_meme" not in rendered
    assert "snapshot auto replies used  = 7 / 12" in rendered
    assert "snapshot quote replies used = 3 / 6" in rendered
    assert "snapshot_next_priority" in rendered
    assert "current_next_priority" not in rendered


def test_carried_forward_state_timestamp_within_window_is_not_stale():
    rendered = digest.render_markdown(
        carried_state_report("2026-07-25 10:00:00")
    )

    assert "## Latest state\n" in rendered
    assert (
        "State timestamp: `2026-07-25 10:00:00` "
        "(carried forward from previous digest state)"
    ) in rendered
    assert "stale snapshot" not in rendered
    assert "daily_reply_count       = 7" in rendered
    assert "snapshot_daily_reply_count" not in rendered
    assert "current_next_meme" in rendered
    assert "auto replies used  = 7 / 12" in rendered
    assert "current_next_priority" in rendered


def test_carried_forward_state_without_timestamp_has_unknown_age():
    rendered = digest.render_markdown(carried_state_report(None))

    assert "## Latest state (carried-forward snapshot; age unavailable)" in rendered
    assert "age and staleness unavailable" in rendered
    assert "without a state timestamp their currentness cannot be established" in rendered
    assert "snapshot_daily_reply_count       = 7" in rendered
    assert "snapshot_next_quote_post" in rendered
    assert "snapshot_next_meme_post" in rendered
    assert "snapshot auto replies used  = 7 / 12" in rendered


def test_current_runtime_state_fresh_absent_and_malformed(tmp_path):
    state_path = tmp_path / "bot_state.json"

    state, path, timestamp, status = digest.load_current_runtime_state(tmp_path)
    assert state is None
    assert path == state_path
    assert timestamp is None
    assert status == "absent"

    state_path.write_text("not json", encoding="utf-8")
    state, _path, timestamp, status = digest.load_current_runtime_state(tmp_path)
    assert state is None
    assert timestamp is None
    assert status.startswith("malformed:")

    state_path.write_text(
        json.dumps(
            {
                "daily_reply_count": 4,
                "daily_quote_reply_count": 2,
                "daily_reply_date": "2026-08-16",
            }
        ),
        encoding="utf-8",
    )
    state, _path, timestamp, status = digest.load_current_runtime_state(tmp_path)
    assert status == "available"
    assert state["daily_reply_count"] == 4
    assert timestamp is not None


def test_digest_resume_state_never_falls_back_as_current_runtime_state(tmp_path):
    resume_path = tmp_path / "digest-resume.json"
    resume_path.write_text(
        json.dumps(
            {
                "last_log_entry_time": "2026-08-07 12:00:00",
                "last_known_latest_state": {
                    "time": "2026-08-07 12:00:00",
                    "daily_reply_count": 999,
                    "next_reply_lane_priority": "quote",
                },
                "last_known_latest_config": {
                    "MAX_AUTO_REPLIES_PER_DAY": "999"
                },
            }
        ),
        encoding="utf-8",
    )
    report = digest.analyse([])
    report["latest_state"] = {}
    report["latest_config"] = {}

    digest.apply_saved_context(report, resume_path)

    assert report["latest_state"] == {}
    assert report["latest_config"] == {}
    assert report["historical_retained_state"]["daily_reply_count"] == 999
    assert report["historical_retained_config"]["MAX_AUTO_REPLIES_PER_DAY"] == "999"
    report["runtime_state_status"] = {
        "status": "absent",
        "path": str(tmp_path / "bot_state.json"),
    }
    report["runtime_config_status"] = {
        "status": "absent",
        "path": str(tmp_path / "mrsMThatcher.local.json"),
    }
    rendered = digest.render_markdown(report)
    assert "Current bot runtime state: **unavailable**" in rendered
    assert "## Historical retained diagnostic snapshots" in rendered
    assert "for historical diagnosis only" in rendered
    assert '"daily_reply_count": 999' in rendered
    assert '"MAX_AUTO_REPLIES_PER_DAY": "999"' in rendered
    assert "daily_reply_count       = 999" not in rendered
    assert "MAX_AUTO_REPLIES_PER_DAY=999" not in rendered


def test_unavailable_runtime_reads_retain_historical_snapshots_across_runs(tmp_path):
    resume_path = tmp_path / "digest-resume.json"
    log_path = tmp_path / "bot.log"
    original_state = {
        "time": "2026-07-25 08:00:00",
        "daily_reply_count": 7,
    }
    original_config = {"MAX_AUTO_REPLIES_PER_DAY": 48}
    resume_path.write_text(
        json.dumps(
            {
                "last_log_entry_time": "2026-07-25 08:00:00",
                "last_known_latest_state": original_state,
                "last_known_latest_config": original_config,
            }
        ),
        encoding="utf-8",
    )

    for offset in (0, 1):
        records = [record(offset, "INFO", "worker", "still running")]
        report = digest.analyse(records)
        digest.apply_saved_context(report, resume_path)
        report["latest_state"] = {}
        report["latest_config"] = {}
        report["runtime_state_status"] = {"status": "absent"}
        report["runtime_config_status"] = {
            "status": "malformed: JSONDecodeError"
        }

        digest.save_resume_time(
            resume_path,
            records[-1].ts,
            records,
            report,
            [log_path],
        )
        saved = digest.read_resume_data(resume_path)
        assert saved["last_known_latest_state"] == original_state
        assert saved["last_known_latest_config"] == original_config

    empty_override = tmp_path / "mrsMThatcher.local.json"
    empty_override.write_text("{}", encoding="utf-8")
    current_config, _path, _timestamp, config_status = (
        digest.load_current_runtime_config(tmp_path)
    )
    records = [record(2, "INFO", "worker", "runtime reads recovered")]
    report = digest.analyse(records)
    digest.apply_saved_context(report, resume_path)
    report["latest_state"] = {"daily_reply_count": 2}
    report["latest_config"] = current_config or {}
    report["runtime_state_status"] = {"status": "available"}
    report["runtime_config_status"] = {"status": config_status}

    digest.save_resume_time(
        resume_path,
        records[-1].ts,
        records,
        report,
        [log_path],
    )
    saved = digest.read_resume_data(resume_path)
    assert saved["last_known_latest_state"] == {"daily_reply_count": 2}
    assert saved["last_known_latest_config"] == {}


def test_current_runtime_config_is_allow_listed_and_validated(tmp_path):
    config_path = tmp_path / "mrsMThatcher.local.json"
    config_path.write_text(
        json.dumps(
            {
                "MAX_AUTO_REPLIES_PER_DAY": 48,
                "MAX_REPLIES_PER_AUTHOR_PER_DAY": 6,
                "MAX_QUOTE_REPLIES_PER_DAY": 12,
                "OPENAI_API_KEY": "must-not-appear",
            }
        ),
        encoding="utf-8",
    )

    config, path, timestamp, status = digest.load_current_runtime_config(tmp_path)

    assert status == "available"
    assert path == config_path
    assert timestamp is not None
    assert config["MAX_AUTO_REPLIES_PER_DAY"] == 48
    assert config["MAX_REPLIES_PER_AUTHOR_PER_DAY"] == 6
    assert config["MAX_QUOTE_REPLIES_PER_DAY"] == 12
    assert "OPENAI_API_KEY" not in config


@pytest.mark.parametrize(
    ("runtime_status", "state", "headline_text", "body_text"),
    [
        (
            "absent",
            None,
            "current API cooldown state unavailable",
            "Current bot runtime state: **unavailable**",
        ),
        (
            "available",
            {
                "api_cooldown_until_epoch": 0,
                "x_write_api_cooldown_until_epoch": 0,
                "openai_api_cooldown_until_epoch": int(BASE.timestamp()) + 60,
                "quote_api_cooldown_until_epoch": 0,
            },
            "OpenAI cooldown active now",
            f"openai_api_cooldown_until = {int(BASE.timestamp()) + 60}  2026-07-25 09:01:00  active",
        ),
        (
            "available",
            {
                "api_cooldown_until_epoch": 0,
                "x_write_api_cooldown_until_epoch": 0,
                "openai_api_cooldown_until_epoch": int(BASE.timestamp()) - 60,
                "quote_api_cooldown_until_epoch": 0,
            },
            "OpenAI cooldown occurred, now expired",
            f"openai_api_cooldown_until = {int(BASE.timestamp()) - 60}  2026-07-25 08:59:00  expired",
        ),
        (
            "available",
            {
                "api_cooldown_until_epoch": 0,
                "x_write_api_cooldown_until_epoch": 0,
                "openai_api_cooldown_until_epoch": 0,
                "quote_api_cooldown_until_epoch": 0,
            },
            "no API cooldown",
            "openai_api_cooldown_until = 0  none  cleared",
        ),
    ],
)
def test_current_cooldown_headline_and_body_use_generation_time(
    runtime_status,
    state,
    headline_text,
    body_text,
    tmp_path,
):
    generation_epoch = int(BASE.timestamp())
    report = digest.analyse([], generation_time=BASE)
    report["generation_epoch"] = generation_epoch
    report["runtime_state_status"] = {
        "status": runtime_status,
        "path": str(tmp_path / "bot_state.json"),
    }
    # Deliberately use a state-file timestamp on the opposite side of the
    # cooldown to prove that current health is evaluated at generation time.
    state_file_time = BASE - timedelta(days=30)
    if state and state.get("openai_api_cooldown_until_epoch", 0) < generation_epoch:
        state_file_time = BASE + timedelta(days=30)
    report["latest_state"] = (
        digest.summarize_latest_state(
            state,
            state_file_time,
            source="bot_state.json",
            source_path=tmp_path / "bot_state.json",
        )
        if state is not None
        else {}
    )

    digest.refresh_derived(report)
    rendered = digest.render_markdown(report)

    assert headline_text in report["summary"]["headline"]
    assert headline_text in rendered
    assert body_text in rendered


@pytest.mark.parametrize("contents", [None, {}, {"MAX_AUTO_REPLIES_PER_DAY": 48}])
def test_local_config_is_described_as_on_disk_not_effective_live(
    contents,
    tmp_path,
):
    config_path = tmp_path / "mrsMThatcher.local.json"
    if contents is not None:
        config_path.write_text(json.dumps(contents), encoding="utf-8")
    config, path, timestamp, status = digest.load_current_runtime_config(tmp_path)
    report = digest.analyse([])
    report["latest_config"] = config or {}
    report["runtime_config_status"] = {
        "status": status,
        "path": str(path),
        "time": digest.dt_text(timestamp) if timestamp else None,
    }
    rendered = digest.render_markdown(report)

    assert "## On-disk local configuration overrides" in rendered
    assert "Current production configuration" not in rendered
    if contents is None:
        assert "On-disk local overrides: **unavailable**" in rendered
        assert "Effective live configuration is not established" in rendered
    else:
        assert "read-only file observation does not establish" in rendered
        assert "effective in a live process" in rendered



def test_lone_x_transient_failure_is_observed_without_claiming_resolution():
    report = digest.analyse(
        [record(0, "ERROR", "x_request", "X API error 503: Service Unavailable")]
    )

    health = report["error_health"]
    assert health["current_independent_incident_count"] == 0
    assert health["historical_resolved_incident_count"] == 0
    assert health["transient_provider_observation_count"] == 1
    assert health["transient_provider_observations"][0]["category"] == (
        "x_api_transient_failure"
    )
    rendered = digest.render_markdown(report)
    assert "Provider recovery is unverified" in rendered
    assert "historically resolved incidents" in rendered


def test_x_429_and_cooldown_are_one_resolved_incident_after_later_success():
    records = [
        record(
            0,
            "ERROR",
            "maybe_reply_to_quote_tweets",
            traceback(
                "Failed to fetch quote tweets for post 900",
                'ApiError: X bearer API error 429: {"status":429}',
            ),
        ),
        record(
            1,
            "ERROR",
            "record_api_error",
            "Entering API cooldown after 429 until 2026-07-25 09:01:00",
        ),
        record(120, "INFO", "get_mentions", "Fetched 0 mentions"),
    ]

    health = digest.analyse(records)["error_health"]

    assert health["current_independent_incident_count"] == 0
    assert health["historical_resolved_incident_count"] == 1
    incident = health["historical_resolved_incidents"][0]
    assert incident["category"] == "x_api_rate_limit"
    assert incident["record_count"] == 2
    assert incident["resolution_reason"] == (
        "cooldown deadline passed and later successful X activity was observed"
    )


def test_success_before_later_429_does_not_resolve_that_incident():
    records = [
        record(0, "INFO", "get_mentions", "Fetched 0 mentions"),
        record(
            60,
            "ERROR",
            "x_request",
            'X bearer API error 429: {"status":429}',
        ),
        record(
            61,
            "ERROR",
            "record_api_error",
            "Entering API cooldown after 429 until 2026-07-25 09:02:00",
        ),
    ]

    health = digest.analyse(
        records,
        generation_time=BASE + timedelta(minutes=10),
    )["error_health"]

    assert health["current_independent_incident_count"] == 1
    assert health["historical_resolved_incident_count"] == 0
    assert health["current_incidents"][0]["category"] == "x_api_rate_limit"


def test_active_x_429_cooldown_remains_one_current_incident():
    records = [
        record(
            0,
            "ERROR",
            "x_request",
            'X bearer API error 429: {"status":429}',
        ),
        record(
            1,
            "ERROR",
            "record_api_error",
            "Entering API cooldown after 429 until 2099-07-25 09:01:00",
        ),
    ]

    health = digest.analyse(records)["error_health"]

    assert health["current_independent_incident_count"] == 1
    assert health["historical_resolved_incident_count"] == 0
    assert health["current_incidents"][0]["category"] == "x_api_rate_limit"
    assert health["current_incidents"][0]["record_count"] == 2


def test_repeated_quote_token_event_is_warning_not_failure():
    payload = {
        "event": "quote_pagination_repeated_token",
        "post_id": "900",
        "token_fingerprint": "abc123",
        "pages_completed": 2,
        "results_retained": 7,
    }
    report = digest.analyse(
        [
            record(
                0,
                "INFO",
                "log_event",
                "EVENT " + json.dumps(payload, separators=(",", ":")),
            )
        ]
    )

    assert report["error_health"]["current_independent_incident_count"] == 0
    assert report["events"][0]["kind"] == "quote_pagination_repeated_token"
    rendered = digest.render_markdown(report)
    assert "## Bounded protocol warnings" in rendered
    assert "warnings, not operational failures" in rendered


def test_prefixed_quote_pagination_traceback_stays_visible_but_resolves():
    report = digest.analyse(
        [
            record(
                0,
                "ERROR",
                "maybe_reply_to_quote_tweets",
                traceback(
                    "Failed to fetch quote tweets for post 900",
                    "PaginationCursorProtocolError: X quote tweets for 900 "
                    "returned a repeated pagination token",
                ),
            ),
            record(
                60,
                "INFO",
                "get_quote_tweets_for_post",
                "Fetched 0 quote tweet(s) for post_id=901",
            ),
        ]
    )

    health = report["error_health"]
    assert health["current_independent_incident_count"] == 0
    assert health["historical_resolved_incident_count"] == 1
    incident = health["historical_resolved_incidents"][0]
    assert incident["category"] == "quote_pagination_protocol_anomaly"
    assert incident["traceback_count"] == 1
    assert "PaginationCursorProtocolError" in report["errors_and_warnings"][0]["message"]



def test_regular_image_scores_are_formatted_without_mutating_report_values():
    image_event = {
        "kind": "regular_image_selected",
        "time": "2026-07-27 04:00:00",
        "source": "generated",
        "basename": "fixture.png",
        "score": "1.1111111111111112",
        "origin_quote_hash": "a" * 64,
        "origin_quote_match": "true",
        "origin_quote_boost": "0.050000000000000003",
        "made_with_ai": "true",
    }
    report = digest.analyse([])
    report["events"] = [dict(image_event)]
    report["regular_image_usage"] = {
        "events": [dict(image_event)],
        "summary": digest.regular_image_usage_summary([image_event]),
    }

    rendered = digest.render_markdown(report)

    assert rendered.count("1.11") == 2
    assert rendered.count("0.05") == 2
    assert "1.1111111111111112" not in rendered
    assert "0.050000000000000003" not in rendered
    assert report["events"][0]["score"] == "1.1111111111111112"
    assert report["events"][0]["origin_quote_boost"] == "0.050000000000000003"
    assert report["regular_image_usage"]["events"][0]["score"] == "1.1111111111111112"
    assert digest.render_markdown(report) == rendered


def test_editorial_shadow_rank_distribution_is_robust_to_outliers():
    events = [
        {
            "production_source": "original",
            "production_shadow_rank": rank,
            "winner_changed": rank != 1,
            "production_winner": f"production-{index}",
            "shadow_original_winner": f"shadow-{index}",
        }
        for index, rank in enumerate([1, 1, 1, 1, 1, 1, 1, 1, 2])
    ]
    summary = digest.original_editorial_shadow_summary(events)
    assert summary["average_production_winner_shadow_rank"] == 10 / 9
    assert summary["median_production_winner_shadow_rank"] == 1
    assert summary["worst_production_winner_shadow_rank"] == 2
    assert summary["production_rank_1"] == 8
    assert summary["production_rank_2_or_3"] == 1
    assert summary["production_rank_10_or_worse"] == 0
    assert summary["severe_disagreements"] == []
    report = digest.analyse([])
    report["original_editorial_shadow"] = {"events": events, "summary": summary}
    rendered = digest.render_markdown(report)
    assert "mean_production_winner_shadow_rank    = 1.11" in rendered
    assert "median_production_winner_shadow_rank  = 1" in rendered
    assert "worst_production_winner_shadow_rank   = 2" in rendered
    assert "1.1111111111111112" not in rendered


def test_equally_frequent_shadow_winners_are_not_silently_capped():
    events = [
        {
            "production_source": "original",
            "production_shadow_rank": 1,
            "winner_changed": False,
            "shadow_original_winner": f"winner-{index:02}.jpg",
        }
        for index in range(9)
    ]
    summary = digest.original_editorial_shadow_summary(events)
    assert summary["most_frequent_shadow_winners"] == [
        (f"winner-{index:02}.jpg", 1) for index in range(9)
    ]
    report = digest.analyse([])
    report["original_editorial_shadow"] = {"events": events, "summary": summary}
    rendered = digest.render_markdown(report)
    for index in range(9):
        assert f"winner-{index:02}.jpg (1)" in rendered


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


def generated_image_report(*, detailed: bool = False) -> dict:
    report = digest.analyse([])
    report["generated_image_spacing"] = {
        "latest": {
            "pool_enabled": False,
            "allowed": True,
            "required": 3,
            "original_posts_since_generated": 4,
        },
        "events": [
            {
                "time": "2026-07-26 12:00:00",
                "kind": "state",
                "pool_enabled": False,
                "allowed": True,
                "required": 3,
                "original_posts_since_generated": 4,
            },
            {
                "time": "2026-07-27 04:00:00",
                "kind": "state",
                "pool_enabled": False,
                "allowed": True,
                "required": 3,
                "original_posts_since_generated": 4,
            },
        ],
    }
    report["generated_image_pool_health"] = {
        "active_generated_images": 12,
        "quarantined_generated_images": 0,
        "total_known_generated_images": 12,
        "active_analysis_records": 12,
        "active_identity_records": 12,
        "metadata_coverage": "complete",
        "hash_valid": 12,
        "hash_total": 12,
        "hash_validation": "complete",
        "health": "OK",
        "active_never_used": 12,
    }
    rows = [
        {
            "image": f"unused-{index:02}.png",
            "origin_quote_hash": f"quote-{index:02}",
            "last_successful_post": None,
            "successful_posts": 0,
        }
        for index in range(12)
    ]
    report["generated_image_utilisation"] = {
        "active_generated_images": 12,
        "active_images_used_in_observed_logs": 0,
        "active_images_not_seen_in_observed_logs": 12,
        "active_pool_observed_usage_percentage": 0.0,
        "active_images_used_in_current_cycle": 0,
        "active_images_unused_in_current_cycle": 12,
        "total_successful_generated_posts_observed": 0,
        "maximum_successful_posts_for_one_image": 0,
        "most_frequently_used": [],
        "never_used": rows,
        "never_used_total": 12,
        "unused_longest": rows,
    }
    report["detailed_appendix"] = detailed
    return report


def test_disabled_generated_pool_and_filename_samples_are_clear():
    rendered = digest.render_markdown(generated_image_report())

    assert "generated-image pool is intentionally disabled" in rendered
    assert "`allowed=true` means a spacing rule would permit selection" in rendered
    assert "Current-cycle history records whether an image is marked used" in rendered
    assert "Bounded structured-log observations count successful post records" in rendered
    assert "count: **12**; sample: **5**" in rendered
    assert "7 additional active images omitted" in rendered
    assert "unused-04.png" in rendered
    assert "unused-05.png" not in rendered
    assert "Detailed generated-image filename appendix" not in rendered
    assert "same population is therefore also unused longest" in rendered
    assert rendered.count("Active generated images unused longest") == 0
    assert "All **2** spacing observations had the same state" in rendered


def test_receipt_pairs_and_pending_then_confirmed_are_not_incidents():
    report = digest.analyse([])
    report["main_post_recovery"] = {
        "receipt_events": [
            {"time": "t1", "kind": "regular_written", "level": "WARNING", "lane": "quote_image"},
            {"time": "t2", "kind": "regular_removed", "level": "INFO", "lane": "quote_image"},
        ],
        "confirmed_post_recovery": [],
    }
    report["confirmed_reply_recovery"] = {
        "receipt_events": [
            {
                "time": "t2",
                "kind": "written",
                "level": "WARNING",
                "lane": "mention",
                "target_id": "target-1",
                "reply_post_id": "reply-1",
            },
            {
                "time": "t3",
                "kind": "removed",
                "level": "INFO",
                "lane": "mention",
                "target_id": "target-1",
                "reply_post_id": "reply-1",
            },
        ],
        "warnings": [],
    }
    report["events"] = [
        {
            "kind": "posting_transaction_state",
            "time": "t1",
            "parent_post_id": "post-1",
            "main_post_state": "main_post_confirmed",
            "context_reply_state": "context_reply_pending",
        },
        {
            "kind": "historical_context_obligation",
            "time": "t2",
            "parent_post_id": "post-1",
            "status": "completed",
            "context_reply_state": "context_reply_confirmed",
        },
    ]
    rendered = digest.render_markdown(report)
    assert "## Transactional receipt lifecycle" in rendered
    assert "Routine two-phase receipt write/remove pairs completed: **1**" in rendered
    assert "Routine confirmed-reply receipt write/remove pairs completed: **1**" in rendered
    assert "## Confirmed-reply receipt lifecycle" in rendered
    assert "## Confirmed-reply recovery" not in rendered
    assert "intermediate `context_reply_pending` states subsequently reached" in rendered
    assert "they are not outstanding" in rendered


def structured_main_post_lifecycle(
    start: int,
    *,
    lane: str,
    attempt_id: str,
) -> list[digest.Record]:
    receipt_name = (
        "regular_post_receipt.json"
        if lane == "quote_image"
        else "meme_post_receipt.json"
    )
    removal_label = "regular-post" if lane == "quote_image" else "meme-post"
    path = f"/srv/{receipt_name}"
    return [
        record(
            start,
            "WARNING",
            "write_main_post_attempt",
            f"Wrote main-post sending receipt lane={lane} "
            f"attempt_id={attempt_id} path={path}",
        ),
        record(
            start + 1,
            "WARNING",
            "mark_main_post_attempt_attempting",
            f"Promoted main-post receipt to attempting lane={lane} "
            f"attempt_id={attempt_id} path={path}",
        ),
        record(
            start + 2,
            "WARNING",
            "promote_main_post_attempt_to_pending_schedule",
            "Promoted main-post attempt to confirmed pending-schedule receipt "
            f"lane={lane} attempt_id={attempt_id} post_id={start + 1000} "
            f"path={path}",
        ),
        record(
            start + 3,
            "WARNING",
            "finalize_confirmed_pending_schedule_receipt",
            "Finalised confirmed pending-schedule receipt "
            f"lane={lane} post_id={start + 1000} path={path}",
        ),
        record(
            start + 4,
            "INFO",
            f"remove_{removal_label.replace('-', '_')}_receipt",
            f"Removed reconciled {removal_label} receipt: {path}",
        ),
    ]


@pytest.mark.parametrize(
    ("lane", "lane_summary"),
    [
        ("quote_image", "regular quote/image **1**; daily-meme **0**"),
        ("daily_meme", "regular quote/image **0**; daily-meme **1**"),
    ],
)
def test_structured_main_post_lifecycle_is_completed_not_unresolved(
    lane: str,
    lane_summary: str,
):
    report = digest.analyse(
        structured_main_post_lifecycle(0, lane=lane, attempt_id="attempt-1")
    )
    rendered = digest.render_markdown(report)

    assert "Routine two-phase receipt write/remove pairs completed: **1**" in rendered
    assert lane_summary in rendered
    assert "Stale or unresolved receipt events:" not in rendered


def test_main_post_removal_at_window_boundary_is_not_unresolved():
    report = digest.analyse([
        record(
            0,
            "INFO",
            "remove_regular_post_receipt",
            "Removed reconciled regular-post receipt: "
            "/srv/regular_post_receipt.json",
        )
    ])
    rendered = digest.render_markdown(report)

    assert "opening write was outside the selected window: **1**" in rendered
    assert "Stale or unresolved receipt events:" not in rendered


def test_structured_main_post_write_without_removal_remains_unresolved():
    records = structured_main_post_lifecycle(
        0,
        lane="quote_image",
        attempt_id="attempt-unresolved",
    )[:-1]
    rendered = digest.render_markdown(digest.analyse(records))

    assert "Routine two-phase receipt write/remove pairs completed: **0**" in rendered
    assert "Stale or unresolved receipt events:" in rendered
    assert "attempt-unresolved" in rendered


def test_mixed_structured_main_post_fixture_counts_fifteen_regular_and_one_meme():
    records: list[digest.Record] = []
    for index in range(15):
        records.extend(
            structured_main_post_lifecycle(
                index * 10,
                lane="quote_image",
                attempt_id=f"regular-{index}",
            )
        )
    records.extend(
        structured_main_post_lifecycle(
            200,
            lane="daily_meme",
            attempt_id="meme-1",
        )
    )

    rendered = digest.render_markdown(digest.analyse(records))

    assert "Routine two-phase receipt write/remove pairs completed: **16**" in rendered
    assert "regular quote/image **15**; daily-meme **1**" in rendered
    assert "Stale or unresolved receipt events:" not in rendered


def test_schema_v3_reply_receipt_lifecycle_is_routine_and_observable():
    records = [
        record(
            0,
            "WARNING",
            "write_sending_reply_receipt",
            "Wrote conversational reply sending receipt "
            "source=mention target_id=123 path=/tmp/reply.json",
        ),
        record(
            1,
            "WARNING",
            "promote_sending_reply_receipt",
            "Promoted conversational reply receipt to confirmed "
            "source=mention target_id=123 reply_post_id=999 "
            "path=/tmp/reply.json",
        ),
        record(
            2,
            "INFO",
            "remove_confirmed_reply_receipt",
            "Removed reconciled confirmed-reply receipt "
            "source=mention target_id=123 reply_post_id=999 "
            "path=/tmp/reply.json",
        ),
    ]

    report = digest.analyse(records)
    rendered = digest.render_markdown(report)

    assert [
        item["kind"]
        for item in report["confirmed_reply_recovery"]["receipt_events"]
    ] == ["sending", "promoted", "removed"]
    assert "Routine confirmed-reply receipt write/remove pairs completed: **1**" in rendered
    assert "## Confirmed-reply receipt lifecycle" in rendered
    assert "## Confirmed-reply recovery" not in rendered
    assert report["error_health"]["current_independent_incident_count"] == 0


def test_unresolved_schema_v3_sending_receipt_stays_current_after_other_reply():
    report = digest.analyse(
        [
            record(
                0,
                "WARNING",
                "write_sending_reply_receipt",
                "Wrote conversational reply sending receipt "
                "source=quote_tweet target_id=456 path=/tmp/reply.json",
            ),
            record(
                1,
                "INFO",
                "maybe_reply_to_mentions",
                "Considering mention id=789 author_id=42 text='different transaction'",
            ),
            record(
                2,
                "INFO",
                "maybe_reply_to_mentions",
                "Reply posted successfully",
            ),
        ]
    )

    rendered = digest.render_markdown(report)

    assert "## Confirmed-reply recovery" in rendered
    assert "sending_unresolved" in rendered
    assert "Pre-send reply receipt remains unresolved" in rendered
    assert report["error_health"]["current_independent_incident_count"] == 1
    assert report["error_health"]["historical_resolved_incident_count"] == 0
    assert report["error_health"]["current_incidents"][0]["category"] == (
        "conversational_reply_receipt_barrier"
    )
    assert "1 unresolved operational incident" in report["summary"]["headline"]
    assert "no unresolved operational incidents" not in report["summary"]["headline"]


def test_confirmed_state_fallback_is_not_labelled_definite_non_success():
    report = digest.analyse(
        [
            record(
                0,
                "WARNING",
                "write_sending_reply_receipt",
                "Wrote conversational reply sending receipt "
                "source=mention target_id=123 path=/tmp/reply.json",
            ),
            record(
                1,
                "WARNING",
                "remove_confirmed_reply_receipt",
                "Removed conversational reply sending receipt after confirmed "
                "identity was preserved in canonical state "
                "source=mention target_id=123 path=/tmp/reply.json",
            ),
            record(
                2,
                "CRITICAL",
                "maybe_reply_to_mentions",
                traceback(
                    "Confirmed mention reply required its durable state fallback",
                    "ConfirmedReplyLocalPersistenceError: promotion failed",
                ),
            ),
        ]
    )

    rendered = digest.render_markdown(report)
    receipt_events = report["confirmed_reply_recovery"]["receipt_events"]

    assert "## Confirmed-reply recovery" in rendered
    assert [item["kind"] for item in receipt_events] == [
        "sending",
        "confirmed_state_fallback_removed",
    ]
    assert receipt_events[1]["disposition"] == "confirmed_state_fallback"
    assert (
        report["summary"]["stats"][
            "confirmed_reply_receipt_confirmed_state_fallback_removed"
        ]
        == 1
    )
    assert (
        report["summary"]["stats"].get(
            "confirmed_reply_receipt_sending_removed",
            0,
        )
        == 0
    )
    assert (
        "Confirmed replies preserved through the durable canonical-state "
        "fallback: **1**."
        in rendered
    )
    assert "Prepared reply receipts cleared after a definite non-success" not in rendered
    assert len(report["confirmed_reply_recovery"]["warnings"]) == 1
    assert report["error_health"]["current_independent_incident_count"] == 0


def test_confirmed_state_fallback_removal_is_terminal_across_window_boundary():
    report = digest.analyse(
        [
            record(
                0,
                "WARNING",
                "remove_confirmed_reply_receipt",
                "Removed conversational reply sending receipt after confirmed "
                "identity was preserved in canonical state "
                "source=quote_tweet target_id=456 path=/tmp/reply.json",
            )
        ]
    )

    rendered = digest.render_markdown(report)

    assert "## Confirmed-reply recovery" in rendered
    assert (
        "Confirmed replies preserved through the durable canonical-state "
        "fallback: **1**."
        in rendered
    )
    assert "Stale or unresolved confirmed-reply receipts:" not in rendered
    assert report["error_health"]["current_independent_incident_count"] == 0


def test_definite_non_success_removal_is_terminal_across_window_boundary():
    report = digest.analyse(
        [
            record(
                0,
                "INFO",
                "remove_confirmed_reply_receipt",
                "Removed conversational reply sending receipt after definite "
                "non-success source=mention target_id=123 path=/tmp/reply.json",
            )
        ]
    )

    rendered = digest.render_markdown(report)

    assert "## Confirmed-reply receipt lifecycle" in rendered
    assert (
        "Prepared reply receipts cleared after a definite non-success: **1**."
        in rendered
    )
    assert "Stale or unresolved confirmed-reply receipts:" not in rendered
    assert report["error_health"]["current_independent_incident_count"] == 0


def test_confirmed_receipt_removal_is_terminal_across_window_boundary():
    report = digest.analyse(
        [
            record(
                0,
                "INFO",
                "remove_confirmed_reply_receipt",
                "Removed reconciled confirmed-reply receipt "
                "source=mention target_id=123 reply_post_id=999 "
                "path=/tmp/reply.json",
            )
        ]
    )

    rendered = digest.render_markdown(report)

    assert "## Confirmed-reply receipt lifecycle" in rendered
    assert (
        "Confirmed-reply receipt removals whose opening write was outside "
        "the observed window: **1**."
        in rendered
    )
    assert "Stale or unresolved confirmed-reply receipts:" not in rendered
    assert report["error_health"]["current_independent_incident_count"] == 0


def test_reconciliation_start_without_terminal_event_remains_current():
    report = digest.analyse(
        [
            record(
                0,
                "WARNING",
                "reconcile_confirmed_reply_receipt",
                "Reconciling confirmed reply receipt "
                "source=quote_tweet target_id=456 reply_post_id=999",
            )
        ]
    )

    rendered = digest.render_markdown(report)

    assert "## Confirmed-reply recovery" in rendered
    assert "reconciliation_unresolved" in rendered
    assert (
        "no terminal receipt removal or completion was observed"
        in rendered
    )
    assert report["error_health"]["current_independent_incident_count"] == 1
    assert report["error_health"]["current_incidents"][0]["category"] == (
        "conversational_reply_receipt_barrier"
    )
    assert "1 unresolved operational incident" in report["summary"]["headline"]


def test_explicit_replay_suppression_completes_reconciliation_observation():
    report = digest.analyse(
        [
            record(
                0,
                "WARNING",
                "reconcile_confirmed_reply_receipt",
                "Reconciling confirmed reply receipt "
                "source=mention target_id=123 reply_post_id=999",
            ),
            record(
                1,
                "WARNING",
                "maybe_reply_to_mentions",
                "Reconciled confirmed reply receipt before checking new "
                "mention candidates",
            ),
        ]
    )

    rendered = digest.render_markdown(report)

    assert "## Confirmed-reply receipt lifecycle" in rendered
    assert "Stale or unresolved confirmed-reply receipts:" not in rendered
    assert report["error_health"]["current_independent_incident_count"] == 0


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


def test_engagement_metric_labels_state_exact_denominators():
    report = digest.analyse([])
    report["historical_context_engagement"] = {
        "available": True,
        "unavailable_impressions_count": 1,
        "unavailable_click_metrics_count": 295,
    }
    rendered = digest.render_markdown(report)
    assert "Latest analytics snapshots with unavailable impressions: **1**" in rendered
    assert "latest analytics snapshots with unavailable URL-link clicks: **295**" in rendered
    assert "Unavailable impressions/click metrics" not in rendered


def test_detailed_filename_appendix_is_optional():
    rendered = digest.render_markdown(generated_image_report(detailed=True))

    assert "Detailed generated-image filename appendix" in rendered
    assert "unused-11.png" in rendered


def test_singular_and_plural_wording_is_deterministic():
    assert digest.plural_count(1, "reply", "replies") == "1 reply"
    assert digest.plural_count(2, "reply", "replies") == "2 replies"
    assert digest.plural_count(1, "daily meme") == "1 daily meme"
    assert digest.plural_count(2, "daily meme") == "2 daily memes"


def test_repeated_output_is_byte_identical():
    report = digest.analyse([_normal_main_post_record()])

    assert digest.render_markdown(report).encode() == digest.render_markdown(report).encode()
    assert json.dumps(report, sort_keys=True) == json.dumps(report, sort_keys=True)
