from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

import pytest

import mrs_log_digest as digest
import mrs_log_digest_costs as costs
import mrs_log_digest_provider_costs as provider_costs
from tests.test_openai_cost_digest import NOW, cost_report, current_day, write_cache


def test_pytest_isolates_default_cache_before_digest_import(monkeypatch, tmp_path):
    home = Path(os.environ["MRS_PYTEST_RUNTIME_ROOT"]) / "home"
    expected = home / ".local/state/mrsMThatcher/openai-costs/daily_costs.json"
    assert Path.home() == home
    assert digest.OPENAI_COST_CACHE_PATH == expected
    assert not expected.exists()
    decoy = write_cache(tmp_path / "environment.json", current_day())
    monkeypatch.setenv("OPENAI_COST_CACHE", str(decoy))

    assert digest.load_openai_cost_cache(now_utc=NOW) == {
        "available": False,
        "path": str(expected),
        "reason": "cache file is missing",
    }


def test_digest_wrapper_resolves_paths_and_observes_its_clock_once(tmp_path, monkeypatch):
    calls = []

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            calls.append(tz)
            return NOW.astimezone(tz)

    default = write_cache(tmp_path / "default.json", current_day())
    explicit = write_cache(tmp_path / "explicit.json", current_day(total="3"))
    monkeypatch.setattr(digest, "OPENAI_COST_CACHE_PATH", default)
    monkeypatch.setattr(digest, "datetime", FixedDateTime)
    monkeypatch.setenv("OPENAI_COST_CACHE", str(tmp_path / "ignored.json"))
    monkeypatch.setenv("HOME", str(tmp_path))

    loaded = digest.load_openai_cost_cache()
    assert loaded["available"] is True
    assert loaded["path"] == str(default)
    assert loaded["age_seconds"] == 0
    assert calls == [timezone.utc]
    assert digest.load_openai_cost_cache(None, now_utc=NOW) == loaded
    assert digest.load_openai_cost_cache("", now_utc=NOW) == loaded
    for path in (explicit, str(explicit), "~/explicit.json"):
        selected = digest.load_openai_cost_cache(path, now_utc=NOW)
        assert selected["path"] == str(explicit)
        assert selected["days"]["2026-08-18"]["primary_total"] == "3"
    assert calls == [timezone.utc]


def test_module_uses_explicit_dependencies_and_prepares_without_mutation(tmp_path):
    path = write_cache(tmp_path / "costs.json", current_day(total="2.125"))
    calls = []

    def read_bytes(path, *, maximum):
        calls.append((path, maximum))
        return digest.read_stable_regular_bytes(path, maximum=maximum)

    def parse_json_object(raw, *, label):
        calls.append(label)
        return digest._strict_json_object(raw, label=label)

    cache = costs.load_openai_cost_cache(
        path, now_utc=NOW, read_bytes=read_bytes, parse_json_object=parse_json_object
    )
    original = copy.deepcopy(cache)
    report = costs.prepare_openai_published_cost_report(
        cache,
        window_start_local=datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc),
        window_end_local=datetime(2026, 8, 18, 10, 25, tzinfo=timezone.utc),
        now_utc=NOW,
    )

    assert calls == [(path, 16 * 1024 * 1024), "OpenAI cost cache"]
    assert cache == original
    assert report == cost_report(path)
    assert report["current_day"]["primary_total"] == "2.125"
    assert report["selected_window"]["amount"] == "0.75"
    assert digest.estimate_openai_cost_window is costs.estimate_openai_cost_window
    assert digest.local_digest_time_to_utc is costs.local_digest_time_to_utc
    assert digest._validate_openai_money_map is costs._validate_openai_money_map


def test_cost_loader_keeps_regular_file_size_and_consistency_checks(tmp_path, monkeypatch):
    oversized = tmp_path / "oversized.json"
    with oversized.open("wb") as handle:
        handle.truncate(digest.OPENAI_COST_CACHE_MAX_BYTES + 1)
    result = digest.load_openai_cost_cache(oversized, now_utc=NOW)
    assert result["available"] is False
    assert "file exceeds 16777216 bytes" in result["reason"]

    path = write_cache(tmp_path / "changing.json", current_day())
    original_read = os.read
    changed = False

    def change_during_read(descriptor, size):
        nonlocal changed
        data = original_read(descriptor, size)
        if not changed:
            changed = True
            with path.open("ab") as handle:
                handle.write(b" ")
        return data

    monkeypatch.setattr(os, "read", change_during_read)
    result = digest.load_openai_cost_cache(path, now_utc=NOW)
    assert changed
    assert result["available"] is False
    assert "file changed while read" in result["reason"]


@pytest.mark.parametrize("populated", [False, True])
def test_cli_inherits_isolated_cache_and_preserves_both_outputs(tmp_path, populated):
    home = Path(os.environ["MRS_PYTEST_RUNTIME_ROOT"]) / "home"
    cache = home / ".local/state/mrsMThatcher/openai-costs/daily_costs.json"
    assert Path.home() == home
    assert digest.OPENAI_COST_CACHE_PATH == cache
    assert not cache.exists()
    if populated:
        write_cache(cache, current_day(total="2.125"))
    log = tmp_path / "fixture.log"
    log.write_text(
        "2026-08-18 09:05:00 INFO fixture:1 - Main loop tick\n"
        "2026-08-18 11:25:00 INFO fixture:2 - Main loop tick\n"
    )
    markdown_path = tmp_path / "report.md"
    script = """
from datetime import datetime, timezone
import os
from pathlib import Path
import mrs_log_digest as digest

class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 8, 18, 12, 30, tzinfo=timezone.utc)
        return value.astimezone(tz) if tz else value.astimezone().replace(tzinfo=None)

digest.datetime = FixedDateTime
home = Path(os.environ["MRS_PYTEST_RUNTIME_ROOT"]) / "home"
assert Path.home() == home
assert digest.OPENAI_COST_CACHE_PATH == home / ".local/state/mrsMThatcher/openai-costs/daily_costs.json"
raise SystemExit(digest.main())
"""
    environment = dict(
        os.environ,
        PYTHONPATH=str(Path(digest.__file__).resolve().parent),
        TZ="Europe/London",
        OPENAI_COST_CACHE=str(tmp_path / "ignored.json"),
    )
    try:
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, "--project-dir", str(tmp_path),
             "--no-state", "--json", "--markdown-output", str(markdown_path), str(log)],
            cwd=tmp_path, env=environment, capture_output=True, timeout=15,
        )
        assert result.returncode == 0, result.stderr
        assert result.stderr == b""
        report = json.loads(result.stdout)
        assert report["openai_published_cost"] == cost_report(cache)
        assert report["openai_published_cost"]["available"] is populated
        assert markdown_path.read_bytes() == (digest.render_markdown(report) + "\n").encode()
        assert report["digest_contract"]["producer_source_sha256"] == hashlib.sha256(
            Path(digest.__file__).read_bytes()
        ).hexdigest()
        assert report["digest_contract"]["schema_version"] == 3
    finally:
        if populated:
            cache.unlink()


@pytest.mark.parametrize("module_name", [
    "mrs_log_digest_costs", "mrs_log_digest_provider_costs",
])
def test_cost_module_import_has_no_runtime_effects_or_upward_dependencies(tmp_path, module_name):
    script = """
import builtins
from datetime import datetime, timezone
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
    assert event not in {"os.mkdir", "os.remove", "os.rename", "os.system"}, event

Path.home = classmethod(reject)
for name in ("expanduser", "stat", "lstat", "exists", "is_file", "is_dir", "open"):
    setattr(Path, name, reject)
os.stat = os.lstat = logging.basicConfig = reject
builtins.__import__ = import_guard
sys.addaudithook(audit)
import mrs_log_digest_costs as costs

assert list(logging.getLogger().handlers) == handlers
assert set(logging.Logger.manager.loggerDict) == loggers
assert not {"mrs_log_digest", "mrs_log_digest_markdown", "mrsMThatcher2"} & sys.modules.keys()
"""
    script = script.replace("import mrs_log_digest_costs as costs", f"import {module_name} as costs")
    result = subprocess.run(
        [sys.executable, "-B", "-c", script], cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(Path(digest.__file__).resolve().parent)),
        capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == b""
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("decision,outcome,failure,local_rejection,expected", [
    ({"no_reply_reason": "exact_duplicate_reply"}, {"status": "confirmed"}, {}, {"reason": "writer_link_repair_failed"}, "published"),
    ({"status": "operational_failure"}, {"status": "posted"}, {}, None, "published"),
    ({"no_reply_reason": "exact_duplicate_reply"}, {"status": "posting_failed_retryable"}, {}, {"reason": "writer_link_repair_failed"}, "posting_failed"),
    ({"mode": "no_reply"}, {"status": " Confirmed "}, None, None, "posting_failed"),
    ({"no_reply_reason": "exact_duplicate_reply"}, {"status": 0}, {}, {"reason": "writer_link_repair_failed"}, "writer_local_failure"),
    ({"no_reply_reason": " EXACT_DUPLICATE_REPLY ", "status": "operational_failure"}, None, {}, None, "terminal_repetition_rejection"),
    ({"mode": "no_reply"}, {}, {}, {"reason": "clarification_not_direct_factual_answer"}, "terminal_clarification_mode_rejection"),
    ({"mode": "no_reply"}, None, {}, None, "deliberately_declined"),
    ({"status": "no_reply", "reason": "revision_limit_reached"}, None, None, None, "pipeline_failed"),
    (None, None, {}, None, "pipeline_failed"),
    ({}, {"status": ""}, None, None, "approved_not_confirmed_in_window"),
    (None, {}, None, None, "outcome_unavailable"),
])
def test_candidate_disposition_preserves_competing_outcomes(decision, outcome, failure, local_rejection, expected):
    inputs = (decision, outcome, failure, local_rejection)
    before = copy.deepcopy(inputs)
    assert provider_costs._candidate_reply_disposition(*inputs) == expected
    assert inputs == before


@pytest.mark.parametrize("case", ["short_circuit", "fallback", "pipeline_exception"])
def test_candidate_disposition_uses_current_globals_in_eager_order(monkeypatch, case):
    calls = []

    class Row(dict):
        def __init__(self, label, **fields):
            super().__init__(fields)
            self.label = label

        def get(self, key, default=None):
            calls.append((self.label, key))
            return super().get(key, default)

    class Status:
        def __str__(self):
            calls.append(("status", "str"))
            monkeypatch.setattr(provider_costs, "_is_terminal_pipeline_failure", pipeline)
            return "confirmed"

    def current_terminal(reason):
        calls.append(("current_terminal", reason))
        return "terminal_clarification_mode_rejection"

    def terminal(reason):
        calls.append(("terminal", reason))
        monkeypatch.setattr(provider_costs, "_terminal_local_rejection_outcome", current_terminal)
        return "terminal_repetition_rejection" if case == "short_circuit" else None

    def current_writer(reason):
        calls.append(("current_writer", reason))
        return reason == "decline"

    def writer(reason):
        calls.append(("writer", reason))
        monkeypatch.setattr(provider_costs, "_is_writer_local_failure", current_writer)
        return case == "short_circuit"

    error = KeyError("pipeline classifier")

    def pipeline(reason, status):
        calls.append(("pipeline", reason, status))
        if case == "pipeline_exception":
            raise error
        return True

    def stale_pipeline(*args):
        pytest.fail("pipeline classifier was bound before outcome status conversion")

    monkeypatch.setattr(provider_costs, "_terminal_local_rejection_outcome", terminal)
    monkeypatch.setattr(provider_costs, "_is_writer_local_failure", writer)
    monkeypatch.setattr(provider_costs, "_is_terminal_pipeline_failure", stale_pipeline)
    inputs = (
        Row("decision", effective_reason="effective", reason="", no_reply_reason="decline", status="no_reply"),
        Row("outcome", status=Status()),
        {},
        Row("local", effective_reason="local-effective", reason="local-reason"),
    )
    if case == "pipeline_exception":
        with pytest.raises(KeyError) as raised:
            provider_costs._candidate_reply_disposition(*inputs)
        assert raised.value is error
    else:
        assert provider_costs._candidate_reply_disposition(*inputs) == "published"
    expected = [("decision", "no_reply_reason"), ("terminal", "decline")]
    if case != "short_circuit":
        expected += [("local", "reason"), ("current_terminal", "local-reason")]
    expected += [
        ("decision", "effective_reason"), ("decision", "reason"),
        ("decision", "no_reply_reason"), ("local", "effective_reason"),
        ("local", "reason"), ("writer", "effective"),
    ]
    if case != "short_circuit":
        expected += [("current_writer", ""), ("current_writer", "decline")]
    expected += [
        ("outcome", "status"), ("status", "str"), ("decision", "reason"),
        ("decision", "no_reply_reason"), ("decision", "status"),
        ("pipeline", "decline", "no_reply"),
    ]
    assert calls == expected


def test_cost_summary_delegates_prepared_rows_before_call_coverage(monkeypatch):
    decision = {"kind": "reply_strategy_decision", "lane": "mention", "target_id": "101", "model_call_count": 1}
    outcome = {"kind": "reply_strategy_outcome", "lane": "mention", "target_id": "101", "status": "confirmed", "model_call_count": 1}
    failure = {"kind": "reply_strategy_failure", "lane": "mention", "target_id": "101"}
    local = {"kind": "reply_strategy_local_rejection", "lane": "unknown", "target_id": "101", "reason": "writer_link_repair_failed"}
    usage = [{"lane": "mention", "context_id": "101", "cost_in_usd_ticks": None, "call_start_matched": True}]
    calls = []
    selected = "published"
    classify = provider_costs._candidate_reply_disposition
    integer = provider_costs.optional_int_usage_value

    def disposition(*rows):
        assert all(actual is expected for actual, expected in zip(rows, (decision, outcome, failure, local)))
        assert classify(*rows) == selected
        calls.append("disposition")
        return selected

    def observed_integer(value):
        calls.append(("integer", value))
        return integer(value)

    monkeypatch.setattr(provider_costs, "_candidate_reply_disposition", disposition)
    monkeypatch.setattr(provider_costs, "optional_int_usage_value", observed_integer)
    inputs = (usage, [decision, outcome, failure, local])
    before = copy.deepcopy(inputs)
    summary = digest.xai_reply_cost_summary(*inputs)
    assert digest.xai_reply_cost_summary is provider_costs.xai_reply_cost_summary
    assert not hasattr(digest, "_candidate_reply_disposition")
    assert calls[:5] == [("integer", 1), ("integer", 1), ("integer", None), "disposition", ("integer", 1)]
    candidate = summary["candidates"][0]
    assert candidate["outcome"] is selected
    assert candidate["call_coverage"] == "multiple_pipeline_executions"
    assert candidate["uncosted_successful_calls"] == 1
    assert provider_costs.format_reported_cost(candidate) == "unknown"
    assert summary["effective_per_published_reply"] is None
    assert summary["per_reviewed_candidate"] is None
    assert summary["known_cost_per_costed_call"] is None
    assert summary["known_cost_in_usd_ticks"] == 0
    assert inputs == before
