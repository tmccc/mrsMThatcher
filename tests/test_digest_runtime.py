"""Focused compatibility and side-effect checks for runtime observations."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys

import pytest

import mrs_log_digest as digest
import mrs_log_digest_runtime as runtime
from mrs_log_digest_contracts import RuntimeConfigSnapshot, RuntimeStateSnapshot

from tests.helpers.digest_records import BASE, record


def test_current_snapshot_tags_keep_dictionary_identity(tmp_path):
    source = {"daily_reply_count": 1}
    path = tmp_path / "bot_state.json"
    path.write_bytes(b"{}")
    state, _, _, status = runtime.load_current_runtime_state(
        tmp_path,
        read_snapshot=lambda path, *, maximum: (b"{}", path.stat()),
        parse_json_object=lambda data, *, label: source,
        fromtimestamp=datetime.fromtimestamp,
    )
    assert status == "available" and state is source
    assert RuntimeStateSnapshot(source) is source
    config = {"MAX_AUTO_REPLIES_PER_DAY": 12}
    assert RuntimeConfigSnapshot(config) is config


def test_state_epoch_wrappers_keep_current_conversion_and_timezone(monkeypatch):
    calls = []
    stamp = datetime(2026, 8, 31, 12)

    class ConversionTime(datetime):
        @classmethod
        def fromtimestamp(cls, *args, **kwargs):
            calls.append((args, kwargs))
            if args[0] == 999:
                raise OverflowError("fixture overflow")
            return stamp

    monkeypatch.setattr(digest, "datetime", ConversionTime)
    monkeypatch.setattr(digest, "LONDON", timezone.utc)
    assert digest.epoch_to_human("invalid") is None
    assert digest.epoch_to_human(0) is None
    assert calls == []
    assert digest.epoch_to_human("123") == "2026-08-31 12:00:00"
    assert digest.epoch_to_london_text(321) == "2026-08-31 12:00:00"
    assert calls == [((123,), {}), ((321,), {"tz": timezone.utc})]
    with pytest.raises(OverflowError, match="fixture overflow"):
        digest.epoch_to_human(999)
    assert digest.epoch_to_london_text(999) is None


def test_state_summary_keeps_clock_order_current_helpers_and_shallow_copies(monkeypatch):
    calls = []
    stamp = datetime(2026, 8, 31, 12)
    nested = {"filename": "fixture.png"}
    state = {"posted_meme_filenames": [nested], "recent_own_post_ids": [nested]}

    class ObservationTime(datetime):
        @classmethod
        def now(cls):
            calls.append("now")
            state["mention_backlog"] = {"started_epoch": int(stamp.timestamp()) - 10}
            return stamp

    def epoch(value):
        calls.append("epoch")
        return "converted"

    def wrap(name):
        original = getattr(digest, name)

        def current(*args):
            calls.append(name)
            assert args[0] is state
            return original(*args)

        monkeypatch.setattr(digest, name, current)

    monkeypatch.setattr(digest, "datetime", ObservationTime)
    monkeypatch.setattr(digest, "epoch_to_human", epoch)
    for name in ("state_list_count", "state_list_tail", "state_list_head"):
        wrap(name)
    summary = digest.summarize_latest_state(state, stamp, source_path=Path("fixture.json"))
    assert calls == ["now", *(["epoch"] * 10), "state_list_count", "state_list_count",
                     "state_list_tail", "state_list_head", "state_list_count"]
    assert summary["mention_backlog_age_seconds"] == 10
    assert summary["_state_source_path"] == "fixture.json"
    assert summary["posted_meme_filenames_tail"] is not state["posted_meme_filenames"]
    assert summary["posted_meme_filenames_tail"][0] is nested
    assert summary["recent_own_post_ids_head"][0] is nested


def test_state_summary_keeps_current_unknown_labels(monkeypatch):
    monkeypatch.setattr(digest, "UNKNOWN_MISSING_STATE_FIELD", "missing fixture")
    monkeypatch.setattr(digest, "UNKNOWN_INVALID_STATE_FIELD", "invalid fixture")
    assert digest.state_list_count({}, "items") == "missing fixture"
    assert digest.state_list_count({"items": None}, "items") == "invalid fixture"


def test_author_progress_keeps_current_callbacks_limits_and_observation_time(monkeypatch):
    from tests.helpers.mention_fixtures import (
        DIGEST_AUTHOR_NO_REPLY_CONFIG, digest_author_no_reply_record,
    )

    stamp = datetime(2026, 8, 31, 12)
    observed = datetime(2026, 8, 31, 12, 5)
    epoch = int(observed.timestamp())
    calls = []

    class NoClock(datetime):
        @classmethod
        def now(cls, *args, **kwargs):
            pytest.fail("author progress already has observation and generation times")

    def convert(value):
        calls.append(value)
        return f"epoch {value}"

    records = {key: digest_author_no_reply_record([epoch - 10]) for key in ("bbb", "a")}
    for record in records.values():
        record["evidence_policy"] = "fixture policy"
    monkeypatch.setattr(digest, "datetime", NoClock)
    monkeypatch.setattr(digest, "epoch_to_london_text", convert)
    monkeypatch.setattr(digest, "valid_public_post_id", lambda value: value in records)
    monkeypatch.setattr(digest, "AUTHOR_NO_REPLY_PROGRESS_MAX_AUTHORS", 1)
    monkeypatch.setattr(digest, "AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY", "fixture policy")
    result = digest.current_author_no_reply_strike_progress(
        {"author_evaluation_quarantines": records}, "available",
        DIGEST_AUTHOR_NO_REPLY_CONFIG, "available", stamp, state_observed_at=observed,
    )
    assert result["available"] is True
    assert result["as_of_epoch"] == epoch
    assert [item["author_id"] for item in result["authors"]] == ["a"]
    assert result["omitted_author_count"] == 1
    assert calls == [epoch, epoch - 10, epoch - 10 + 21600, epoch - 10, epoch - 10 + 21600]
    assert result["authors"][0]["recent_qualifying_no_reply_epochs"] is not records["a"]["recent_no_reply_epochs"]
    assert list(records) == ["bbb", "a"]
    assert records["a"]["recent_no_reply_epochs"] == [epoch - 10]


def test_state_refresh_keeps_report_identity_and_current_helper_order(monkeypatch):
    calls = []
    state = {"api_cooldown_until_epoch": 123, "openai_api_cooldown_until_epoch": 0,
             "openai_api_cooldown_until_human": "stale", "openai_api_cooldown_reason": "stale"}
    summary = {
        "_headline_without_current_cooldown": ["current health: stale"],
        "_headline_components": {
            "activity": [], "reply_quality": [],
            "current_health": "current health: stale",
            "observations": [], "cooldown": [],
        },
    }
    report = {"latest_state": state, "summary": summary, "generation_epoch": 100,
              "runtime_state_status": {"status": "available"},
              "error_health": {"current_independent_incident_count": 2}}
    refresh = digest.refresh_current_health_headline
    parse_int = digest.int_or_none

    def integer(value):
        calls.append(("integer", value))
        return parse_int(value)

    def human(value):
        calls.append(("human", value))
        return "converted"

    def headline(value):
        calls.append(("headline",))
        assert value is report
        assert state["openai_api_cooldown_until_human"] is None
        assert state["openai_api_cooldown_reason"] == ""
        refresh(value)

    monkeypatch.setattr(digest, "int_or_none", integer)
    monkeypatch.setattr(digest, "epoch_to_human", human)
    monkeypatch.setattr(digest, "refresh_current_health_headline", headline)
    monkeypatch.setattr(digest, "plural_count", lambda *args: "fixture incidents")
    monkeypatch.setattr(digest, "cooldown_state_text", lambda *args: "active")
    monkeypatch.setattr(digest, "CURRENT_COOLDOWN_FIELDS", (("api_cooldown_until_epoch", "fixture API"),))
    assert digest.refresh_derived(report) is None
    assert report["latest_state"] is state
    assert report["summary"] is summary
    assert calls == [("integer", 123), ("human", 123), ("integer", 0),
                     *([("integer", None)] * 5), ("headline",), ("integer", 100)]
    assert state["api_cooldown_until_human"] == "converted"
    assert summary["headline"] == "current health: fixture incidents; fixture API cooldown active now"
    assert summary["_headline_without_current_cooldown"] == ["current health: stale"]
    assert report["current_cooldown_status"] == {"api_cooldown_until_epoch": "active"}


@pytest.mark.parametrize(
    "loader_name,filename,field,mtime_calls",
    [
        ("load_current_runtime_state", "bot_state.json", "daily_reply_count", 1),
        ("load_current_runtime_config", "mrsMThatcher.local.json", "MAX_AUTO_REPLIES_PER_DAY", 2),
    ],
)
def test_runtime_wrappers_keep_reader_parser_and_file_time_seams(
    tmp_path, monkeypatch, loader_name, filename, field, mtime_calls,
):
    path = tmp_path / filename
    path.write_text(json.dumps({field: 1, "POST_SLEEP_MIN": 1.5}))
    os.utime(path, (1_700_000_000, 1_700_000_000))
    replacement = tmp_path / "replacement.json"
    replacement.write_text(json.dumps({field: 2}))
    os.utime(replacement, (1_700_003_600, 1_700_003_600))
    events = []
    read_snapshot = digest.read_stable_regular_snapshot
    parse_json = digest._strict_native_json_object

    def read_then_replace(requested, *, maximum):
        events.append("read")
        assert requested == path
        data, metadata = read_snapshot(requested, maximum=maximum)
        # A later pathname change must not detach the already observed mtime
        # from the returned bytes. Changes during the read have separate tests.
        replacement.replace(path)
        return data, metadata

    def parse(raw, *, label):
        events.append("parse")
        assert label == filename
        return parse_json(raw, label=label)

    class FileTime(datetime):
        @classmethod
        def fromtimestamp(cls, epoch, tz=None):
            events.append("mtime")
            assert epoch == 1_700_000_000
            return super().fromtimestamp(epoch, tz)

        @classmethod
        def now(cls, tz=None):
            pytest.fail("state/config readers must not sample observation time")

    monkeypatch.setattr(digest, "read_stable_regular_snapshot", read_then_replace)
    monkeypatch.setattr(digest, "_strict_native_json_object", parse)
    monkeypatch.setattr(digest, "datetime", FileTime)

    value, observed_path, mtime, status = getattr(digest, loader_name)(tmp_path)

    assert status == "available"
    assert value[field] == 1
    assert type(value[field]) is int
    assert type(value["POST_SLEEP_MIN"]) is float
    assert observed_path == path
    assert type(mtime) is FileTime
    assert mtime.timestamp() == 1_700_000_000
    assert events == ["read", "parse", *(["mtime"] * mtime_calls)]
    assert path.stat().st_mtime == 1_700_003_600


@pytest.mark.parametrize(
    "raw,clock_sampled,valid,active",
    [
        (None, False, True, []),
        (b"[]", False, False, ["fail_closed_invalid_control"]),
        (b'{"unsupported":true}', False, False, ["fail_closed_invalid_control"]),
        (b'{"generation":true}', False, False, ["fail_closed_invalid_control"]),
        (b'{"generation":null}', False, False, ["fail_closed_invalid_control"]),
        (b'{"pause_all":1}', True, False, ["fail_closed_invalid_control"]),
        (b'{"pause_all_until":1.0000000000000000000000000001}', True, False, ["fail_closed_invalid_control"]),
        (
            b'{"pause_replies":" yes ","disable_replies":true,"pause_all_until":1788177900.0,"disable_all_until":1788177901}',
            True, True, ["disable_replies", "pause_replies", "disable_all_until"],
        ),
    ],
)
def test_pause_wrapper_samples_patched_clock_at_original_validation_boundary(
    tmp_path, monkeypatch, raw, clock_sampled, valid, active,
):
    events = []
    parse_json = digest._strict_json_object

    def read_bytes(path, *, maximum):
        events.append("read")
        assert path == tmp_path / "mrsMThatcher.control.json"
        assert maximum == 64 * 1024
        if raw is None:
            raise FileNotFoundError(path)
        return raw

    def parse(data, *, label):
        events.append("parse")
        assert label == "runtime control"
        value = parse_json(data, label=label)
        if "pause_all_until" in value:
            assert type(value["pause_all_until"]) is Decimal
        return value

    class PauseTime(datetime):
        @classmethod
        def now(cls, tz=None):
            events.append("now")
            return cls.fromtimestamp(1_788_177_900, tz=timezone.utc)

    monkeypatch.setattr(digest, "read_stable_regular_bytes", read_bytes)
    monkeypatch.setattr(digest, "_strict_json_object", parse)
    monkeypatch.setattr(digest, "datetime", PauseTime)

    result = digest.runtime_control_snapshot(tmp_path)

    assert result["valid"] is valid
    assert result["active_keys"] == active
    assert result["global_pause_active"] is bool(active)
    assert events == ["read", *(["parse"] if raw is not None else []), *(["now"] if clock_sampled else [])]
    if raw is not None:
        assert result["sha256"] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("module_name", [
    "runtime_control_contract",
    "mrs_log_digest_input_io",
    "mrs_log_digest_context",
    "mrs_log_digest_runtime", "mrs_log_digest_remote_write",
    "mrs_log_digest_state_reporting",
    "mrs_log_digest_records",
    "mrs_log_digest_transactions",
    "mrs_log_digest_provider_observations",
    "mrs_log_digest_api_health",
    "mrs_log_digest_legacy_posts",
    "mrs_log_digest_snapshot_incidents",
])
def test_runtime_module_import_has_no_runtime_effects_or_upward_dependencies(tmp_path, module_name):
    script = """
import builtins
import logging
import os
from pathlib import Path
import sys

handlers = list(logging.getLogger().handlers)
loggers = set(logging.Logger.manager.loggerDict)
original_import = builtins.__import__

def reject(*args, **kwargs):
    raise AssertionError((args, kwargs))

def import_guard(name, *args, **kwargs):
    assert name not in {
        "mrs_log_digest", "mrs_log_digest_markdown", "mrsMThatcher2",
        "remote_write_safety_protocol", "exact_receipt_retirement",
        "remote_write_transport_journal", "remote_media_upload_receipt",
        "shadow_lifecycle",
    }, name
    return original_import(name, *args, **kwargs)

def audit(event, args):
    if event == "open":
        path, mode, flags = args
        assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC), args
        assert isinstance(path, str) and path.endswith((".py", ".pyc", ".so")), args
    assert not event.startswith(("socket.", "subprocess.")), event
    assert event not in {"os.mkdir", "os.remove", "os.rename", "os.system"}, event

Path.home = classmethod(reject)
for name in ("expanduser", "stat", "lstat", "exists", "is_file", "is_dir", "open"):
    setattr(Path, name, reject)
os.stat = os.lstat = logging.basicConfig = reject
builtins.__import__ = import_guard
sys.addaudithook(audit)
__import__(sys.argv[1])

assert list(logging.getLogger().handlers) == handlers
assert set(logging.Logger.manager.loggerDict) == loggers
assert not {"mrs_log_digest", "mrs_log_digest_markdown", "mrsMThatcher2"} & sys.modules.keys()
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, module_name], cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(Path(digest.__file__).resolve().parent)),
        capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == b""
    assert list(tmp_path.iterdir()) == []


def test_runtime_boundary_only_reads_explicit_files(tmp_path):
    documents = {
        "bot_state.json": b'{"daily_reply_count":4}',
        "mrsMThatcher.local.json": b'{"MAX_AUTO_REPLIES_PER_DAY":48,"OPENAI_API_KEY":"synthetic-secret"}',
        "mrsMThatcher.control.json": b'{"pause_all":true}',
    }
    for filename, raw in documents.items():
        (tmp_path / filename).write_bytes(raw)
    script = """
from datetime import datetime
import os
from pathlib import Path
import sys
import mrs_log_digest as digest
import mrs_log_digest_runtime as runtime

project = Path.cwd()
paths = {str(project / name) for name in (
    "bot_state.json", "mrsMThatcher.local.json", "mrsMThatcher.control.json"
)}
before = {p: digest._stable_file_identity(os.stat(p)) for p in paths}
opened = []

def audit(event, args):
    if event == "open":
        path, mode, flags = args
        assert path in paths, args
        assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC), args
        opened.append(path)
    assert not event.startswith(("socket.", "subprocess.")), event
    assert event not in {"os.mkdir", "os.remove", "os.rename", "os.chmod", "os.utime", "os.system"}, event

sys.addaudithook(audit)
state = runtime.load_current_runtime_state(
    project, read_snapshot=digest.read_stable_regular_snapshot,
    parse_json_object=digest._strict_native_json_object,
    fromtimestamp=datetime.fromtimestamp,
)
config = runtime.load_current_runtime_config(
    project, read_snapshot=digest.read_stable_regular_snapshot,
    parse_json_object=digest._strict_native_json_object,
    fromtimestamp=datetime.fromtimestamp,
)
control = runtime.runtime_control_snapshot(
    project, read_bytes=digest.read_stable_regular_bytes,
    parse_json_object=digest._strict_json_object, now=lambda: datetime(2026, 8, 31, 12),
)
assert state[3] == config[3] == "available"
assert state[0]["daily_reply_count"] == 4
assert config[0]["MAX_AUTO_REPLIES_PER_DAY"] == 48
assert "OPENAI_API_KEY" not in config[0]
assert control["valid"] and control["global_pause_active"]
assert set(opened) == paths and len(opened) == 3
assert before == {p: digest._stable_file_identity(os.stat(p)) for p in paths}
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script], cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(Path(digest.__file__).resolve().parent)),
        capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == b""
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == documents


def test_lifecycle_snapshot_keeps_current_lazy_helpers_and_shared_rows(monkeypatch, tmp_path):
    from types import SimpleNamespace

    features = [{"feature_name": "supplied"}]
    register = {"schema_version": 7, "features": features}
    schedule = [{"decision_overdue": False}, {"decision_overdue": True}]
    calls = []

    def load(path):
        calls.append(("load", path))
        return register

    def decide(value):
        assert value is register
        calls.append("schedule")
        return schedule

    monkeypatch.setitem(sys.modules, "shadow_lifecycle", SimpleNamespace(
        load_lifecycle_register=load, lifecycle_decision_schedule=decide,
    ))
    result = digest.shadow_lifecycle_snapshot(tmp_path)
    assert calls == [("load", tmp_path / "shadow_feature_lifecycle.json"), "schedule"]
    assert list(result) == ["available", "schema_version", "features", "decision_schedule", "overdue_decisions"]
    assert result["schema_version"] == 7
    assert result["features"] is features
    assert result["decision_schedule"] is schedule
    assert result["overdue_decisions"] == [schedule[1]]
    assert result["overdue_decisions"][0] is schedule[1]


@pytest.mark.parametrize("phase", ["import", "load", "schedule"])
def test_lifecycle_snapshot_catches_lazy_import_and_helper_failures(monkeypatch, tmp_path, phase):
    import builtins
    from types import SimpleNamespace

    original_import = builtins.__import__
    calls = []

    def step(name, result):
        calls.append(name)
        if name == phase:
            raise ImportError("supplied failure")
        return result

    def importing(name, *args, **kwargs):
        if name == "shadow_lifecycle":
            step("import", None)
        return original_import(name, *args, **kwargs)

    monkeypatch.setitem(sys.modules, "shadow_lifecycle", SimpleNamespace(
        load_lifecycle_register=lambda path: step("load", {}),
        lifecycle_decision_schedule=lambda value: step("schedule", []),
    ))
    monkeypatch.setattr(builtins, "__import__", importing)
    assert digest.shadow_lifecycle_snapshot(tmp_path) == {
        "available": False,
        "reason": "lifecycle register unavailable: ImportError: supplied failure",
        "features": [],
    }
    assert calls == ["import", "load", "schedule"][:["import", "load", "schedule"].index(phase) + 1]


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
