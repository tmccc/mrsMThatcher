"""Original-editorial observation, companion and summary boundaries."""

from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import inspect
import json
import os
import subprocess
import sys

import pytest

import mrs_log_digest as digest


BASE = datetime(2026, 9, 4, 12)
MODES = ("selection", "shadow")


def _payload(**changes):
    return dict({
        "quote_hash": "a" * 64, "line_no": 12, "selection_phase": "normal",
        "production_source": "original", "production_winner": "t01.jpg",
        "shadow_original_winner": "t02.jpg",
    }, **changes)


def _record(mode, payload, offset=0, *, selftest=False):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return digest.Record(
        BASE + timedelta(seconds=offset), "INFO", "fixture", 42 + offset,
        f"prefix ORIGINAL_EDITORIAL_{mode.upper()}_RESULT \t {raw}  \t",
        "mrsMThatcher.selftest.log" if selftest else "mrsMThatcher.log", offset + 1,
    )


def _analyse(records, max_text=280):
    return digest.analyse(
        records, max_text=max_text, generation_time=BASE + timedelta(hours=1),
        input_file_indexes={"mrsMThatcher.log": 0, "mrsMThatcher.selftest.log": 1},
    )


def test_comparison_key_keeps_exact_fields_order_and_values():
    payload = _payload(line_no="12", time="ignored", event_mode="selection",
                       selection_applied=True, selected_winner="ignored")
    assert digest.original_editorial_comparison_key(payload) == (
        "a" * 64, "12", "normal", "original", "t01.jpg", "t02.jpg",
    )
    assert digest.original_editorial_comparison_key({}) == (None,) * 6
    payload["line_no"] = []
    assert digest.original_editorial_comparison_key(payload)[1] is payload["line_no"]


def test_companions_are_consumed_in_order_with_multiplicity_and_local_state():
    sequence = [("shadow", 12), ("selection", 12), ("selection", 13),
                ("selection", 12), ("shadow", 12), ("shadow", 13),
                ("shadow", 12), ("shadow", 12), ("selection", 12)]
    records = [
        _record(mode, _payload(line_no=line, tag=index, time="payload time",
                              event_mode="payload mode", selection_applied=True,
                              selected_winner=f"selected-{index}.jpg"), index,
                selftest=index in {2, 4})
        for index, (mode, line) in enumerate(sequence)
    ]
    original = deepcopy(records)
    report = _analyse(records, max_text=1)
    editorial = report["original_editorial_shadow"]
    assert [item["tag"] for item in editorial["events"]] == [0, 1, 2, 3, 7, 8]
    assert [item["event_mode"] for item in editorial["events"]] == [
        "shadow", "selection", "selection", "selection", "shadow", "selection",
    ]
    assert [item["time"] for item in editorial["events"]] == [
        f"2026-09-04 12:00:0{index}" for index in (0, 1, 2, 3, 7, 8)
    ]
    assert {key: value for key, value in report["summary"]["stats"].items()
            if key.startswith("original_editorial")} == {
        "original_editorial_selection_observations": 4,
        "original_editorial_shadow_observations": 5,
        "original_editorial_shadow_companion_observations": 3,
    }
    assert editorial["summary"]["active_selection_observations"] == 4
    assert editorial["summary"]["legacy_shadow_observations"] == 2
    assert report["events"] == []
    assert all("source_refs" not in item and "kind" not in item for item in editorial["events"])
    assert records == original
    assert _analyse(records, max_text=1) == report
    assert len(_analyse([records[0]])["original_editorial_shadow"]["events"]) == 1
    assert _analyse([])["original_editorial_shadow"]["events"] == []


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("raw", ["{broken", "[]", '{"x":1,"x":2}', '{"x":"\\ud800"}', "\ud800"])
def test_parse_and_utf8_errors_keep_exact_diagnostics(mode, raw):
    marker = f"ORIGINAL_EDITORIAL_{mode.upper()}_RESULT"
    with pytest.raises((ValueError, UnicodeError)) as caught:
        digest._strict_native_json_object(raw.encode("utf-8"), label=marker)
    report = _analyse([_record(mode, raw, selftest=True)], max_text=1)
    assert report["errors_and_warnings"] == [{
        "time": "2026-09-04 12:00:00", "level": "INFO",
        "message": f"Malformed {marker}: {caught.value}: {raw}",
        "source_refs": [{"input_file_index": 1, "record_number": 1,
                         "timestamp": "2026-09-04 12:00:00", "logger": "fixture",
                         "logged_source_line_number": 42}],
    }]
    assert {key: value for key, value in report["summary"]["stats"].items()
            if key.startswith("original_editorial")} == {f"original_editorial_{mode}_parse_errors": 1}
    assert report["original_editorial_shadow"]["events"] == report["events"] == []


@pytest.mark.parametrize("mode", MODES)
def test_error_raw_text_uses_escaped_newlines_and_240_character_limit(mode):
    report = _analyse([_record(mode, "broken\n" + "é" * 300)], max_text=1)
    assert report["errors_and_warnings"][0]["message"] == (
        f"Malformed ORIGINAL_EDITORIAL_{mode.upper()}_RESULT: "
        "Expecting value: line 1 column 1 (char 0): broken\\n" + "é" * 231 + "…"
    )


@pytest.mark.parametrize("mode", MODES)
def test_parser_result_identity_overwrites_and_lazy_diagnostic_dependencies(monkeypatch, mode):
    payload = _payload(time="replace", event_mode="replace",
                       unknown={"integer": 4, "float": 4.0, "bool": True})
    calls = []

    def parse(data, *, label):
        calls.append((data, label))
        return payload

    def forbidden(*args):
        raise AssertionError("successful observations do not acquire provenance")

    monkeypatch.setattr(digest, "_strict_native_json_object", parse)
    monkeypatch.setattr(digest, "record_source_ref", forbidden)
    report = _analyse([_record(mode, "{}")])
    assert calls == [(b"{}", f"ORIGINAL_EDITORIAL_{mode.upper()}_RESULT")]
    assert report["original_editorial_shadow"]["events"][0] is payload
    assert payload["time"] == "2026-09-04 12:00:00"
    assert payload["event_mode"] == mode
    assert [type(value) for value in payload["unknown"].values()] == [int, float, bool]

    def failing_parser(data, *, label):
        raise RuntimeError("parser detail")

    monkeypatch.setattr(digest, "_strict_native_json_object", failing_parser)
    monkeypatch.setattr(digest, "short", lambda raw, limit: f"raw={raw},limit={limit}")
    monkeypatch.setattr(digest, "record_source_ref", lambda record, indexes: {"custom": record.ordinal})
    report = _analyse([_record(mode, "{}")])
    assert report["errors_and_warnings"] == [{
        "time": "2026-09-04 12:00:00", "level": "INFO",
        "message": f"Malformed ORIGINAL_EDITORIAL_{mode.upper()}_RESULT: parser detail: raw={{}},limit=240",
        "source_refs": [{"custom": 1}],
    }]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("fields,error", [
    ({"production_shadow_rank": "bad"}, ValueError),
    ({"affinity_matches": 1}, TypeError),
    ({"quote_hash": []}, TypeError),
])
def test_schema_invalid_objects_fail_after_parsing_without_diagnostics(monkeypatch, mode, fields, error):
    def forbidden(*args):
        raise AssertionError("schema failures must not become parse diagnostics")

    monkeypatch.setattr(digest, "record_source_ref", forbidden)
    with pytest.raises(error):
        _analyse([_record(mode, _payload(**fields))])


def test_marker_precedence_first_split_and_both_continue_paths():
    selection = "ORIGINAL_EDITORIAL_SELECTION_RESULT"
    shadow = "ORIGINAL_EDITORIAL_SHADOW_RESULT"
    identity = "GENERATED_IDENTITY_POLICY_APPLIED"
    records = [
        _record("selection", _payload(note=selection + " {}")),
        _record("shadow", _payload(note=selection + " {}"), 1),
        _record("selection", _payload(note=shadow + " {}"), 2),
        _record("selection", "{broken " + identity + " {}", 3),
        _record("shadow", _payload(line_no=14, note=identity + " {}"), 4),
    ]
    report = _analyse(records)
    stats = report["summary"]["stats"]
    assert stats["original_editorial_selection_observations"] == 2
    assert stats["original_editorial_selection_parse_errors"] == 2
    assert stats["original_editorial_shadow_observations"] == 1
    assert len(report["original_editorial_shadow"]["events"]) == 3
    assert report["generated_identity_policy"]["events"] == report["events"] == []


def test_summary_preserves_truthiness_numeric_conversions_and_severe_payload_identity():
    events = [
        _payload(event_mode="selection", selection_applied=True, selected_winner="t02.jpg",
                 winner_changed=True, production_shadow_rank=10,
                 shadow_winner_editorial_adjustment=-2, cap_hit="false", affinity_matches=["a", 1]),
        _payload(event_mode="selection", selection_applied=1, winner_changed=1,
                 production_shadow_rank="10", shadow_winner_editorial_adjustment="9",
                 affinity_matches="ab"),
        _payload(event_mode="selection", selection_applied=True, production_source="generated",
                 production_winner=None, selected_winner=0, production_shadow_rank=10.9,
                 shadow_winner_editorial_adjustment=True),
        _payload(event_mode="unexpected", winner_changed=True, production_shadow_rank=True),
        _payload(event_mode="selection", selection_applied=False, production_shadow_rank=2.9,
                 shadow_winner_editorial_adjustment=4.0),
        {},
    ]
    original = deepcopy(events)
    summary = digest.original_editorial_shadow_summary(events)
    assert summary == {
        "observations": 6, "active_selection_observations": 4, "legacy_shadow_observations": 2,
        "selector_applied_observations": 2, "selector_not_applied_observations": 2,
        "selected_winner_changes": 1, "production_original": 4, "production_generated": 1,
        "comparable_original_observations": 4, "winner_changes": 2, "winner_change_percent": 50.0,
        "average_production_winner_shadow_rank": 6.6, "median_production_winner_shadow_rank": 10,
        "worst_production_winner_shadow_rank": 10, "production_rank_1": 1,
        "production_rank_2_or_3": 1, "production_rank_10_or_worse": 3,
        "severe_disagreements": [events[0]], "shadow_winner_differed": 2,
        "most_frequent_shadow_winners": [("t02.jpg", 5)],
        "most_frequent_affinity_concepts": [("a", 2), ("1", 1), ("b", 1)],
        "most_frequent_active_dimensions": [],
        "average_abs_editorial_adjustment": 7 / 3, "max_abs_editorial_adjustment": 4.0,
        "cap_hit_count": 1,
    }
    assert summary["severe_disagreements"][0] is events[0]
    assert events == original


def test_compatibility_entry_points_and_shared_ranking_are_reexports():
    import mrs_log_digest_original_editorial as editorial
    import mrs_log_digest_values as values

    for name, signature in (
        ("original_editorial_comparison_key", "(item: 'Dict[str, Any]') -> 'Tuple[Any, ...]'"),
        ("original_editorial_shadow_summary", "(events: 'List[Dict[str, Any]]') -> 'Dict[str, Any]'"),
    ):
        assert getattr(digest, name) is getattr(editorial, name)
        assert str(inspect.signature(getattr(digest, name))) == signature
    assert editorial.most_common_with_cutoff_ties is values.most_common_with_cutoff_ties
    assert digest.most_common_with_cutoff_ties is values.most_common_with_cutoff_ties


@pytest.mark.parametrize("mode", MODES)
def test_unhashable_comparison_key_keeps_original_partial_mutation_order(mode):
    import mrs_log_digest_original_editorial as editorial

    payload = _payload(quote_hash=[])
    observations, pending, stats, errors = [], Counter(), Counter(), []

    def forbidden(*args):
        raise AssertionError("comparison-key failures are outside parse handling")

    with pytest.raises(TypeError, match="unhashable type: 'list'"):
        getattr(editorial, f"record_original_editorial_{mode}")(
            f"ORIGINAL_EDITORIAL_{mode.upper()}_RESULT {{}}", BASE, "INFO",
            observations=observations, pending_shadow_companions=pending,
            stats=stats, errors=errors, parse_json_object=lambda data, **kwargs: payload,
            short_text=forbidden, source_ref=forbidden,
        )
    assert payload["time"] == "2026-09-04 12:00:00"
    assert payload["event_mode"] == mode
    if mode == "selection":
        assert len(observations) == 1 and observations[0] is payload
    else:
        assert observations == []
    assert pending == stats == {} and errors == []


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("result", [None, []])
def test_invalid_parser_result_fails_outside_parse_diagnostics(mode, result):
    import mrs_log_digest_original_editorial as editorial

    observations, errors, stats, pending = [], [], Counter(), Counter()

    def forbidden(*args):
        raise AssertionError("post-parse failure must not format a parse diagnostic")

    with pytest.raises(TypeError):
        getattr(editorial, "record_original_editorial_" + mode)(
            f"ORIGINAL_EDITORIAL_{mode.upper()}_RESULT {{}}", BASE, "INFO",
            observations=observations, pending_shadow_companions=pending,
            stats=stats, errors=errors,
            parse_json_object=lambda *args, **kwargs: result,
            short_text=forbidden, source_ref=forbidden,
        )
    assert observations == errors == []
    assert stats == pending == Counter()


def test_independent_import_has_no_runtime_effects_or_upward_dependencies(tmp_path):
    # Warm the stdlib JSON package before rejecting directory scans, including
    # importlib's scan for its decoder; all project imports remain guarded.
    script = """
import builtins
import json
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
    assert not (name == "mrsMThatcher2" or name == "mrs_log_digest" or
                name.startswith("mrs_log_digest_") and name not in {
                    "mrs_log_digest_original_editorial", "mrs_log_digest_values",
                    "mrs_log_digest_records"}), name
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
import mrs_log_digest_original_editorial as editorial
assert editorial.original_editorial_comparison_key({}) == (None,) * 6
assert editorial.original_editorial_shadow_summary([])["observations"] == 0
assert list(logging.getLogger().handlers) == handlers
assert set(logging.Logger.manager.loggerDict) == loggers
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script], cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(Path(digest.__file__).resolve().parent)),
        capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == b""
    assert list(tmp_path.iterdir()) == []


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
