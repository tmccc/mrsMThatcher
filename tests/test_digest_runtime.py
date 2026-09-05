"""Focused compatibility and side-effect checks for runtime observations."""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import mrs_log_digest as digest


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
    experiment = {"fixture": "current helper result"}

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

    def summarize_experiment(value):
        calls.append("experiment")
        assert value is None
        return experiment

    monkeypatch.setattr(digest, "datetime", ObservationTime)
    monkeypatch.setattr(digest, "epoch_to_human", epoch)
    for name in ("state_list_count", "state_list_tail", "state_list_head"):
        wrap(name)
    monkeypatch.setattr(digest, "summarize_engagement_question_experiment_state", summarize_experiment)
    summary = digest.summarize_latest_state(state, stamp, source_path=Path("fixture.json"))
    assert calls == ["now", *(["epoch"] * 10), "state_list_count", "state_list_count",
                     "state_list_tail", "state_list_head", "state_list_count", "experiment"]
    assert summary["mention_backlog_age_seconds"] == 10
    assert summary["_state_source_path"] == "fixture.json"
    assert summary["engagement_question_experiment"] is experiment
    assert summary["posted_meme_filenames_tail"] is not state["posted_meme_filenames"]
    assert summary["posted_meme_filenames_tail"][0] is nested
    assert summary["recent_own_post_ids_head"][0] is nested


def test_state_summary_keeps_current_unknown_labels_and_experiment_vocabulary(monkeypatch):
    monkeypatch.setattr(digest, "UNKNOWN_MISSING_STATE_FIELD", "missing fixture")
    monkeypatch.setattr(digest, "UNKNOWN_INVALID_STATE_FIELD", "invalid fixture")
    monkeypatch.setattr(digest, "ENGAGEMENT_QUESTION_EXPERIMENT_ID", "fixture experiment")
    monkeypatch.setattr(digest, "ENGAGEMENT_QUESTION_EXPERIMENT_STATE_SCHEMA_VERSION", 7)
    monkeypatch.setattr(digest, "ENGAGEMENT_QUESTION_EXPERIMENT_STATUSES", {"fixture status"})
    nested = {"fixture": "shared"}
    deferral = {"code": nested, "unprojected": "omitted"}
    summary = digest.summarize_engagement_question_experiment_state({
        "schema_version": 7, "experiment_id": "fixture experiment",
        "status": "fixture status", "current_pair_index": True,
        "current_deferral_reason": deferral,
    })
    assert summary["status"] == "fixture status"
    assert summary["current_pair_index"] == "invalid fixture"
    assert summary["completed_pair_count"] == "missing fixture"
    assert summary["current_deferral_reason"] == {"code": nested}
    assert summary["current_deferral_reason"] is not deferral
    assert summary["current_deferral_reason"]["code"] is nested
    assert digest.state_list_count({}, "items") == "missing fixture"
    assert digest.state_list_count({"items": None}, "items") == "invalid fixture"


def test_author_progress_keeps_current_callbacks_limits_and_observation_time(monkeypatch):
    from tests.test_mention_backlog_author_quarantine import (
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
    summary = {"_headline_without_current_cooldown": ["current health: stale"]}
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
    "mrs_log_digest_runtime", "mrs_log_digest_remote_write",
    "mrs_log_digest_state_reporting",
    "mrs_log_digest_records",
    "mrs_log_digest_transactions",
    "mrs_log_digest_provider_observations",
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
