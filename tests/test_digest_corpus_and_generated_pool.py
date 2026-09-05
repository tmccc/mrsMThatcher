from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import inspect
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

import mrs_log_digest as digest
import mrs_log_digest_generated_pool as generated_pool
from tests.test_digest_reply_observability import write_corpus
from tests.test_generated_image_pool_health_digest import pool, quarantine


def test_snapshot_wrappers_preserve_signatures_and_explicit_dependencies(monkeypatch, tmp_path):
    calls = []
    result = {"sentinel": object()}

    def capture(*args, **kwargs):
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr(digest, "_historical_context_corpus_snapshot", capture)
    monkeypatch.setattr(digest, "_generated_pool_health_snapshot", capture)
    assert str(inspect.signature(digest.historical_context_corpus_snapshot)) == "(project_dir: 'Path') -> 'Dict[str, Any]'"
    assert str(inspect.signature(digest.generated_pool_health_snapshot)) == "(base_dir: 'Path', now: 'Optional[datetime]' = None) -> 'Dict[str, Any]'"
    now = datetime(2026, 7, 10, 13, tzinfo=timezone.utc)
    assert digest.historical_context_corpus_snapshot(tmp_path) is result
    assert digest.generated_pool_health_snapshot(tmp_path, now) is result
    assert digest.generated_pool_health_snapshot(tmp_path) is result
    assert calls[0] == ((tmp_path,), {
        "parse_json_object": digest._strict_native_json_object,
        "sha256_file": digest.file_sha256,
    })
    expected_dependencies = {
        "parse_json_object": digest._strict_native_json_object,
        "parse_json_value": digest._strict_native_json_value,
        "sha256_file": digest.file_sha256,
        "clock_now": digest.datetime.now,
        "fromisoformat": digest.datetime.fromisoformat,
    }
    assert calls[1:] == [((tmp_path, now), expected_dependencies), ((tmp_path, None), expected_dependencies)]
    for name in ("GENERATED_BASENAME_RE", "GENERATED_ANALYSIS_SCHEMA_VERSION", "GENERATED_ANALYSIS_KIND", "GENERATED_AUDIT_SCHEMA_VERSION", "GENERATED_AUDIT_KIND"):
        assert getattr(digest, name) is getattr(generated_pool, name)


def test_corpus_preserves_patched_parsing_then_separate_hash_reads(monkeypatch, tmp_path):
    paths = write_corpus(tmp_path)
    parse = digest._strict_native_json_object
    file_hash = digest.file_sha256
    events = []

    def parse_json(raw, *, label):
        events.append(("parse", label))
        return parse(raw, label=label)

    def hash_after_change(path):
        events.append(("hash", path))
        if path == paths["packets"]:
            path.write_bytes(b'{"items":[]}')
        return file_hash(path)

    monkeypatch.setattr(digest, "_strict_native_json_object", parse_json)
    monkeypatch.setattr(digest, "file_sha256", hash_after_change)
    result = digest.historical_context_corpus_snapshot(tmp_path)
    labels = ["research_packets", "unresolved_cases", "runtime_eligible_manifest", "source_role_audit", "semantic_gate_audit", "semantic_review_ledger"]
    assert events == [event for label, path in zip(labels, paths.values()) for event in (("parse", f"historical corpus {label}"), ("hash", path))]
    assert result["available"] is True
    assert result["completed_packet_count"] == 2
    assert result["file_sha256"]["research_packets"] == hashlib.sha256(b'{"items":[]}').hexdigest()
    assert list(result["file_sha256"]) == sorted(labels)


@pytest.mark.parametrize("failure,reason", [(FileNotFoundError, "missing: research_packets"), (PermissionError, "malformed: research_packets:PermissionError")])
def test_corpus_hash_failure_retains_already_parsed_counts(monkeypatch, tmp_path, failure, reason):
    paths = write_corpus(tmp_path)
    file_hash = digest.file_sha256

    def failing_hash(path):
        if path == paths["packets"]:
            raise failure("synthetic hash failure")
        return file_hash(path)

    monkeypatch.setattr(digest, "file_sha256", failing_hash)
    result = digest.historical_context_corpus_snapshot(tmp_path)
    assert result["available"] is False
    assert result["completed_packet_count"] == 2
    assert result["reason"] == reason
    assert "research_packets" not in result["file_sha256"]


@pytest.mark.parametrize("local_zone", ["UTC", "Europe/London"])
@pytest.mark.parametrize("explicit_now", [None, datetime(2026, 7, 17, 12, 30), datetime(2026, 7, 17, 12, 30, tzinfo=timezone.utc)])
def test_pool_preserves_patched_dependencies_and_clock_order(monkeypatch, tmp_path, local_zone, explicit_now):
    base, names = pool(tmp_path)
    transaction = quarantine(base, names[:1])
    parse_object = digest._strict_native_json_object
    parse_value = digest._strict_native_json_value
    file_hash = digest.file_sha256
    events = []

    def object_parser(raw, *, label):
        events.append(label)
        return parse_object(raw, label=label)

    def value_parser(raw, *, label):
        if label == "images_used.json":
            events.append(label)
        return parse_value(raw, label=label)

    def hash_reader(path):
        events.append(path)
        return file_hash(path)

    class Clock(datetime):
        @classmethod
        def now(cls):
            events.append("now")
            return cls(2026, 7, 17, 12, 30)

        @classmethod
        def fromisoformat(cls, value):
            events.append(("fromisoformat", value))
            return super().fromisoformat(value)

    previous_zone = os.environ.get("TZ")
    try:
        os.environ["TZ"] = local_zone
        time.tzset()
        monkeypatch.setattr(digest, "_strict_native_json_object", object_parser)
        monkeypatch.setattr(digest, "_strict_native_json_value", value_parser)
        monkeypatch.setattr(digest, "file_sha256", hash_reader)
        monkeypatch.setattr(digest, "datetime", Clock)
        snapshot = digest.generated_pool_health_snapshot(base, explicit_now)
    finally:
        if previous_zone is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous_zone
        time.tzset()

    assert events == [
        "generated image analysis", "generated image audit",
        *(base / "generated_review_approved_images" / name for name in names[1:]),
        *(["now"] if explicit_now is None else []),
        "generated image curation manifest",
        ("fromisoformat", "2026-07-10T12:00:00+00:00"),
        transaction / "images" / names[0], "images_used.json",
    ]
    assert snapshot["health"] == "OK"
    # Noon UTC is inside seven days of 12:30 London time, outside 12:30 UTC.
    recent = int(local_zone == "Europe/London" and (explicit_now is None or explicit_now.tzinfo is None))
    assert snapshot["curation_7d"] == {"quarantined": recent, "restored": 0, "net_active_change": -recent}
    assert snapshot["active_previously_used"] == 0
    assert snapshot["quarantined_previously_used"] == 1


@pytest.mark.parametrize("module", ["mrs_log_digest_corpus", "mrs_log_digest_generated_pool"])
def test_snapshot_module_import_has_no_runtime_effects_or_upward_dependencies(tmp_path, module):
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
    assert name not in {"mrs_log_digest", "mrs_log_digest_markdown", "mrsMThatcher2"}, name
    return original_import(name, *args, **kwargs)

def audit(event, args):
    if event == "open":
        path, mode, flags = args
        assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC), args
        assert isinstance(path, str) and path.endswith((".py", ".pyc", ".so")), args
    assert not event.startswith(("socket.", "subprocess.")), event
    assert event not in {"os.mkdir", "os.remove", "os.rename", "os.system", "os.listdir", "os.scandir"}, event

Path.home = classmethod(reject)
for name in ("expanduser", "stat", "lstat", "exists", "is_file", "is_dir", "open", "iterdir", "glob", "rglob"):
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
        [sys.executable, "-B", "-c", script, module], cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(Path(digest.__file__).resolve().parent)),
        capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == b""
    assert list(tmp_path.iterdir()) == []


def test_snapshot_modules_only_read_explicit_synthetic_files(tmp_path):
    base, names = pool(tmp_path)
    quarantine(base, names[:1])
    write_corpus(base)
    before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in base.rglob("*") if p.is_file()}
    script = """
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import mrs_log_digest as digest
import mrs_log_digest_corpus as corpus
import mrs_log_digest_generated_pool as generated_pool

project = Path.cwd()
paths = {str(p) for p in project.rglob("*") if p.is_file()}
directories = {str(p) for p in project.rglob("*") if p.is_dir()} | {str(project)}
opened = []

def audit(event, args):
    if event == "open":
        path, mode, flags = args
        assert str(path) in paths, args
        assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC), args
        opened.append(str(path))
    if event in {"os.listdir", "os.scandir"}:
        assert str(args[0]) in directories, args
    assert not event.startswith(("socket.", "subprocess.")), event
    assert event not in {"os.mkdir", "os.remove", "os.rename", "os.chmod", "os.utime", "os.system"}, event

def reject(*args, **kwargs):
    raise AssertionError((args, kwargs))

Path.home = classmethod(reject)
sys.addaudithook(audit)
historical = corpus.historical_context_corpus_snapshot(
    project, parse_json_object=digest._strict_native_json_object,
    sha256_file=digest.file_sha256,
)
images = generated_pool.generated_pool_health_snapshot(
    project, datetime(2026, 7, 10, 13, tzinfo=timezone.utc),
    parse_json_object=digest._strict_native_json_object,
    parse_json_value=digest._strict_native_json_value,
    sha256_file=digest.file_sha256, clock_now=reject,
    fromisoformat=datetime.fromisoformat,
)
assert historical["available"] is True
assert images["health"] == "OK"
assert images["active_generated_images"] == 3
assert images["quarantined_generated_images"] == 1
assert set(opened) == paths
assert len(opened) == len(paths) + 6  # Corpus parsing and hashing are separate reads.
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script], cwd=base,
        env=dict(os.environ, PYTHONPATH=str(Path(digest.__file__).resolve().parent)),
        capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == b""
    assert before == {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in base.rglob("*") if p.is_file()}
