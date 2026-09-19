"""Generated-identity observation and summary compatibility regressions."""
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
import inspect
import os
from pathlib import Path
import subprocess
import sys

import pytest

import mrs_log_digest as digest
from tests.helpers.digest_generated_identity import identity_event, policy_event


BASE = datetime(2026, 9, 4, 12)
FAMILIES = (
    ("GENERATED_IDENTITY_POLICY_SHADOW_RESULT", "generated_identity_shadow", identity_event),
    ("GENERATED_IDENTITY_POLICY_APPLIED", "generated_identity_policy", policy_event),
)


def _record(marker, payload, offset=0, *, selftest=False, level="INFO"):
    raw = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=True)
    return digest.Record(
        BASE + timedelta(seconds=offset), level, "fixture", 42 + offset,
        "prefix " + marker + " \t " + raw + "  \t",
        "mrsMThatcher.selftest.log" if selftest else "mrsMThatcher.log", offset + 1,
    )


def _analyse(records, max_text=280):
    return digest.analyse(
        records, max_text=max_text, generation_time=BASE + timedelta(hours=1),
        input_file_indexes={"mrsMThatcher.log": 0, "mrsMThatcher.selftest.log": 1},
    )


@pytest.mark.parametrize("marker,section,factory", FAMILIES)
@pytest.mark.parametrize("raw", [
    "{broken", "[]", "null", "1", '{"x":1,"x":2}',
    '{"x":NaN}', '{"x":1e999}', '{"x":"\\ud800"}',
])
def test_strict_parser_errors_preserve_exact_details_and_provenance(marker, section, factory, raw):
    record = _record(marker, raw, selftest=True, level="WARNING")
    report = _analyse([record], max_text=7)
    with pytest.raises((ValueError, UnicodeError)) as caught:
        digest._strict_native_json_object(raw.encode("utf-8"), label=marker)
    errors = [item for item in report["errors_and_warnings"] if item["message"].startswith("Malformed ")]
    assert errors == [{
        "time": "2026-09-04 12:00:00", "level": "WARNING",
        "message": f"Malformed {marker}: {caught.value}: {raw}",
        "source_refs": [{"input_file_index": 1, "record_number": 1,
                         "timestamp": "2026-09-04 12:00:00", "logger": "fixture",
                         "logged_source_line_number": 42}],
    }]
    stats = report["summary"]["stats"]
    assert stats[section + "_parse_errors"] == 1
    assert stats.get(section + "_observations", 0) == 0
    assert report[section]["events"] == []
    assert report["events"] == []


@pytest.mark.parametrize("marker,section,factory", FAMILIES)
def test_error_raw_truncation_is_240_with_escaped_newlines(marker, section, factory):
    raw = "broken\n" + "é" * 300
    report = _analyse([_record(marker, raw)], max_text=1)
    assert report["errors_and_warnings"][0]["message"] == (
        f"Malformed {marker}: Expecting value: line 1 column 1 (char 0): "
        + "broken\\n" + "é" * 231 + "…"
    )


@pytest.mark.parametrize("marker,section,factory", FAMILIES)
def test_observations_keep_native_types_unknown_fields_and_input_order(marker, section, factory):
    payload = factory(time="payload time", unknown={"integer": 4, "float": 4.0, "bool": True},
                      small_penalty_count="2", strong_penalty_count=1.9,
                      origin_quote_only_excluded_count=True)
    records = [_record(marker, payload), _record(marker, {}, 1, selftest=True)]
    original = deepcopy(records)
    report = _analyse(records, max_text=1)
    expected = deepcopy(payload)
    expected["time"] = "2026-09-04 12:00:00"
    assert report[section]["events"] == [expected, {"time": "2026-09-04 12:00:01"}]
    assert list(report[section]["events"][0]) == list(expected)
    extra = report[section]["events"][0]["unknown"]
    assert [type(value) for value in extra.values()] == [int, float, bool]
    assert report[section]["summary"]["cross_quote_candidates_penalised"] == 3
    assert report[section]["summary"]["cross_quote_candidates_excluded"] == 1
    assert report["summary"]["stats"][section + "_observations"] == 2
    assert report["events"] == []
    assert records == original
    assert _analyse(records, max_text=1) == report
    assert _analyse([])[section]["events"] == []


@pytest.mark.parametrize("marker,section,factory", FAMILIES)
@pytest.mark.parametrize("value,error", [("bad", ValueError), ({"x": 1}, TypeError), ([1], TypeError)])
def test_invalid_counts_fail_in_summary_after_successful_observation(monkeypatch, marker, section, factory, value, error):
    records = [_record(marker, factory(small_penalty_count=value))]
    original_summary = getattr(digest, section + "_summary")
    observed = []

    def summary(events):
        observed.extend(events)
        return original_summary(events)

    monkeypatch.setattr(digest, section + "_summary", summary)
    with pytest.raises(error):
        _analyse(records)
    assert len(observed) == 1
    assert observed[0]["small_penalty_count"] == value
    assert observed[0]["time"] == "2026-09-04 12:00:00"


@pytest.mark.parametrize("marker,section,factory", FAMILIES)
def test_parser_result_is_mutated_and_dependency_errors_keep_their_layer(monkeypatch, marker, section, factory):
    payload = factory(time="replace me")
    calls = []

    def parse(data, *, label):
        calls.append((data, label))
        return payload

    monkeypatch.setattr(digest, "_strict_native_json_object", parse)
    report = _analyse([_record(marker, "{}")])
    assert calls == [(b"{}", marker)]
    assert report[section]["events"][0] is payload
    assert payload["time"] == "2026-09-04 12:00:00"

    def failing_parser(data, *, label):
        raise RuntimeError("parser detail")

    monkeypatch.setattr(digest, "_strict_native_json_object", failing_parser)
    monkeypatch.setattr(digest, "short", lambda raw, limit: f"raw={raw},limit={limit}")
    monkeypatch.setattr(digest, "record_source_ref", lambda record, indexes: {"custom": record.ordinal})
    report = _analyse([_record(marker, "{}")])
    assert report["errors_and_warnings"] == [{
        "time": "2026-09-04 12:00:00", "level": "INFO",
        "message": f"Malformed {marker}: parser detail: raw={{}},limit=240",
        "source_refs": [{"custom": 1}],
    }]


@pytest.mark.parametrize("marker,section,factory", FAMILIES)
@pytest.mark.parametrize("result", [None, []])
def test_invalid_parser_result_fails_outside_parse_diagnostics(marker, section, factory, result):
    import mrs_log_digest_generated_identity as identity

    observations, errors, stats = [], [], Counter()

    def forbidden(*args):
        raise AssertionError("post-parse failure must not format a parse diagnostic")

    with pytest.raises(TypeError):
        getattr(identity, "record_" + section)(
            marker + " {}", BASE, "INFO", observations=observations, stats=stats,
            errors=errors, parse_json_object=lambda *args, **kwargs: result,
            short_text=forbidden, source_ref=forbidden,
        )
    assert observations == errors == []
    assert stats == Counter()


def _category_events(factory, *, shadow):
    unchanged = {"winner_changed_by_policy": False, "winner_changed": False,
                 "baseline_winner_differs": False, "counterfactual_policy_winner": "tg_a.png"}
    neutral = dict(unchanged, small_penalty_count=0, strong_penalty_count=0,
                   origin_quote_only_excluded_count=0)
    events = [factory(), factory(**unchanged), factory(counterfactual_comparison_version="legacy"),
              factory(counterfactual_comparison_valid=1), factory(**neutral)]
    if not shadow:
        events.extend([
            factory(**dict(neutral, counterfactual_comparison_version="legacy",
                           baseline_winner_differs=True, baseline_winner_score="88")),
            factory(**dict(neutral, counterfactual_comparison_version="legacy",
                           baseline_winner_differs=True, baseline_winner_score=None)),
            factory(**dict(neutral, counterfactual_comparison_version="legacy")),
        ])
    return events


@pytest.mark.parametrize("marker,section,factory", FAMILIES)
def test_categories_denominators_aliases_and_repeated_objects(marker, section, factory):
    shadow = section.endswith("shadow")
    events = _category_events(factory, shadow=shadow)
    events += [events[0]]  # Repeated object identity is also a separate observation.
    original = deepcopy(events)
    summary = getattr(digest, section + "_summary")(events)
    categories = ["identity_policy_winner_change", "identity_policy_scores_or_eligibility_only",
                  "legacy_policy_causation_unverified", "counterfactual_invariant_failure",
                  "no_policy_effect"]
    if not shadow:
        categories += ["policy_neutral_equal_score_tie_resolution",
                       "policy_neutral_downstream_winner_difference", "winner_unchanged"]
        assert summary["identity_policy_winner_changes"] == summary["winner_changes"]
        assert summary["policy_neutral_baseline_differences"] == 2
        assert summary["no_valid_candidate_events"] == 0
    assert summary["event_categories"] == categories + [categories[0]]
    assert summary["winner_changes"] == 2
    assert summary["policy_relevant_observations"] == 5
    assert summary["winner_change_percent"] == 2 / 3 * 100
    assert events == original
    assert getattr(digest, section + "_summary")([])["winner_change_percent"] == 0.0


def test_shared_ranking_keeps_cutoff_ties_and_top_image_limits():
    names = [f"t{i:02}.jpg" for i in reversed(range(12))]
    counts = Counter(names + ["t00.jpg"])
    expected = [("t00.jpg", 2)] + [(name, 1) for name in sorted(names) if name != "t00.jpg"]
    assert digest.most_common_with_cutoff_ties(counts) == expected
    events = [identity_event(shadow_winner=name, excluded_generated_basenames=[name],
                             penalised_generated_basenames=[name]) for name in names + ["t00.jpg"]]
    summary = digest.generated_identity_shadow_summary(events)
    assert summary["most_frequent_shadow_winners"] == expected
    assert summary["most_frequent_excluded_images"] == [("t00.jpg", 2)] + [(name, 1) for name in names[:7]]
    assert len(summary["most_frequent_penalised_images"]) == 8
    editorial = digest.original_editorial_shadow_summary([{"shadow_original_winner": name} for name in names + ["t00.jpg"]])
    assert editorial["most_frequent_shadow_winners"] == expected


def test_marker_precedence_first_split_and_continue_with_interleaved_records():
    shadow, policy = FAMILIES[0][0], FAMILIES[1][0]
    records = [
        _record(policy, policy_event(note=shadow + " {}")),  # Shadow branch wins, despite marker position.
        _record(shadow, identity_event(note=shadow + " {}"), 1),
        _record(policy, policy_event(note="GENERATED_IMAGE_SPACING_STATUS pool_enabled=true allowed=true original_posts_since_generated=9 required=8"), 2),
        _record(shadow, "{broken GENERATED_IMAGE_SPACING_STATUS pool_enabled=true allowed=true original_posts_since_generated=9 required=8", 3),
        _record("ORIGINAL_EDITORIAL_SHADOW_RESULT", {"note": policy + " {}"}, 4),
        _record("EVENT", {"event": "historical_context_runtime", "status": "ready"}, 5),
        _record(policy, policy_event(), 6, selftest=True),
    ]
    # Structured EVENT matching requires its exact envelope.
    records[5] = replace(records[5], msg='EVENT {"event":"historical_context_runtime","status":"ready"}')
    report = _analyse(records)
    stats = report["summary"]["stats"]
    assert stats["generated_identity_shadow_parse_errors"] == 2
    assert stats["generated_identity_shadow_observations"] == 1
    assert stats["generated_identity_policy_observations"] == 2
    assert stats["original_editorial_shadow_observations"] == 1
    assert [item["kind"] for item in report["events"]] == ["historical_context_runtime"]
    assert report["generated_image_spacing"]["events"] == []
    assert _analyse(records) == report


def test_compatibility_entry_points_are_explicit_reexports():
    import mrs_log_digest_generated_identity as identity
    import mrs_log_digest_values as values

    for name in ("generated_identity_shadow_summary", "generated_identity_policy_summary"):
        assert getattr(digest, name) is getattr(identity, name)
        assert str(inspect.signature(getattr(digest, name))) == "(events: 'List[Dict[str, Any]]') -> 'Dict[str, Any]'"
    assert digest.most_common_with_cutoff_ties is values.most_common_with_cutoff_ties
    assert identity.most_common_with_cutoff_ties is values.most_common_with_cutoff_ties


@pytest.mark.parametrize("marker,section,factory", FAMILIES)
def test_specific_handlers_keep_summary_failures_and_success_provenance_separate(marker, section, factory):
    import mrs_log_digest_generated_identity as identity

    handler = getattr(identity, "record_" + section)
    payload = factory(small_penalty_count="invalid summary count")
    observations, errors, stats = [], [], Counter()

    def forbidden_source_ref():
        raise AssertionError("successful observations do not acquire provenance")

    handler(marker + " {}", BASE, "INFO", observations=observations, stats=stats,
            errors=errors, parse_json_object=lambda data, **kwargs: payload,
            short_text=digest.short, source_ref=forbidden_source_ref)
    assert observations == [payload]
    assert observations[0] is payload
    assert errors == []
    assert stats == {section + "_observations": 1}
    with pytest.raises(ValueError, match="invalid summary count"):
        getattr(identity, section + "_summary")(observations)


def test_independent_import_has_no_runtime_effects_or_upward_dependencies(tmp_path):
    # Warm stdlib package import machinery before rejecting directory scans.
    # Clean venvs do not preload importlib.machinery; project imports stay guarded.
    script = """
import builtins
import importlib.machinery
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import sys
handlers = list(logging.getLogger().handlers)
loggers = set(logging.Logger.manager.loggerDict)
original_import = builtins.__import__
forbidden = {"mrs_log_digest", "mrs_log_digest_markdown", "mrsMThatcher2",
             "mrs_log_digest_runtime", "mrs_log_digest_corpus", "mrs_log_digest_generated_pool",
             "mrs_log_digest_costs", "mrs_log_digest_historical_events"}
def reject(*args, **kwargs):
    raise AssertionError((args, kwargs))
def import_guard(name, *args, **kwargs):
    assert name not in forbidden, name
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
import mrs_log_digest_generated_identity as identity
assert identity.generated_identity_shadow_summary([])["winner_changes"] == 0
assert identity.generated_identity_policy_summary([])["winner_change_percent"] == 0.0
assert list(logging.getLogger().handlers) == handlers
assert set(logging.Logger.manager.loggerDict) == loggers
assert not forbidden & sys.modules.keys()
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script], cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(Path(digest.__file__).resolve().parent)),
        capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == b""
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("as_json", [False, True])
def test_foreign_directory_cli_outputs_and_resumed_observations(tmp_path, as_json):
    project, foreign = tmp_path / "project", tmp_path / "foreign"
    project.mkdir()
    foreign.mkdir()
    log, state, secondary = project / "fixture.log", project / "resume.json", project / "secondary"
    script = Path(digest.__file__).resolve()
    assert Path.home() == Path(os.environ["MRS_PYTEST_RUNTIME_ROOT"]) / "home"

    def append(record):
        with log.open("a") as stream:
            stream.write(f"{record.ts:%Y-%m-%d %H:%M:%S} {record.level} {record.src}:{record.line} - {record.msg}\n")

    def run():
        args = [sys.executable, "-B", str(script), "--project-dir", str(project),
                "--state-file", str(state)]
        args += (["--json", "--markdown-output"] if as_json else ["--json-output"]) + [str(secondary), str(log)]
        result = subprocess.run(args, cwd=foreign, env=os.environ.copy(), capture_output=True, timeout=15)
        assert result.returncode == 0, result.stderr
        assert result.stderr == b""
        report = json.loads(result.stdout if as_json else secondary.read_bytes())
        markdown = secondary.read_bytes() if as_json else result.stdout
        assert markdown == (digest.render_markdown(report) + "\n").encode()
        assert report["digest_contract"]["producer_source_sha256"] == hashlib.sha256(script.read_bytes()).hexdigest()
        assert report["digest_contract"]["repository_head_sha"] == subprocess.check_output(
            ["git", "-C", str(script.parent), "rev-parse", "HEAD"], text=True,
        ).strip()
        return report

    for offset, (marker, section, factory) in enumerate(FAMILIES):
        append(_record(marker, factory(time="payload time"), offset))
    first = run()
    for _, section, _ in FAMILIES:
        assert first[section]["summary"]["observations"] == 1
    append(replace(_record("EVENT", {}, 2), msg='EVENT {"event":"historical_context_runtime","status":"ready"}'))
    for offset, (marker, section, factory) in enumerate(FAMILIES, start=3):
        append(_record(marker, factory(time="payload time"), offset))
    append(_record(FAMILIES[1][0], "[]", 5))
    resumed = run()
    assert resumed["resume_cursor_mode"] == "fingerprint_tail"
    assert resumed["summary"]["record_count"] == 4
    assert [item["kind"] for item in resumed["events"]] == ["historical_context_runtime"]
    for offset, (_, section, _) in enumerate(FAMILIES, start=3):
        assert resumed[section]["summary"]["observations"] == 1
        assert resumed[section]["events"][0]["time"] == f"2026-09-04 12:00:0{offset}"
    assert resumed["summary"]["stats"]["generated_identity_policy_parse_errors"] == 1
    assert resumed["errors_and_warnings"][0]["source_refs"] == [{
        "input_file_index": 0, "record_number": 6, "timestamp": "2026-09-04 12:00:05",
        "logger": "fixture", "logged_source_line_number": 47,
    }]


def test_digest_policy_relevant_denominator_and_zero_safe() -> None:
    effect_only = identity_event(
        production_source="original", production_winner="t02.jpg",
        production_identity_policy=None, production_identity_action="original_unchanged",
        baseline_winner="t02.jpg", counterfactual_policy_winner="t02.jpg",
        shadow_winner_source="original", shadow_winner="t02.jpg",
        winner_changed_by_policy=False, winner_changed=False,
        policy_effect="scores_or_eligibility_only",
        origin_quote_only_excluded_count=0, excluded_generated_basenames=[],
        small_penalty_count=1,
    )
    relevant = [identity_event() for _ in range(2)] + [effect_only for _ in range(3)]
    irrelevant = [identity_event(
        production_source="original", production_identity_policy=None,
        production_winner="t02.jpg", production_identity_action="original_unchanged",
        baseline_winner="t02.jpg", counterfactual_policy_winner="t02.jpg",
        shadow_winner_source="original", shadow_winner="t02.jpg",
        winner_changed_by_policy=False, winner_changed=False, policy_effect="none",
        origin_quote_only_excluded_count=0, excluded_generated_basenames=[],
    ) for _ in range(5)]
    summary = digest.generated_identity_shadow_summary(relevant + irrelevant)
    assert summary["observations"] == 10
    assert summary["policy_relevant_observations"] == 5
    assert summary["winner_changes"] == 2
    assert summary["winner_change_percent"] == 40.0
    assert digest.generated_identity_shadow_summary(irrelevant)["winner_change_percent"] == 0.0


def test_legacy_shadow_difference_is_not_claimed_as_policy_causation() -> None:
    event = identity_event()
    for key in (
        "baseline_winner", "counterfactual_policy_winner",
        "counterfactual_comparison_version", "counterfactual_comparison_valid",
        "policy_effect", "winner_changed_by_policy",
    ):
        event.pop(key, None)
    summary = digest.generated_identity_shadow_summary([event])
    assert summary["winner_changes"] == 0
    assert summary["legacy_policy_causation_unverified"] == 1

    record = digest.Record(
        ts=datetime(2026, 7, 10, 8, 0), level="INFO", src="mrs", line=1,
        msg="GENERATED_IDENTITY_POLICY_SHADOW_RESULT " + json.dumps(event, separators=(",", ":")),
        path="test.log", ordinal=1,
    )
    rendered = digest.render_markdown(digest.analyse([record]))
    assert "Legacy shadow winner differences with unverified policy causation:" in rendered
    assert "Counterfactual winners changed by the generated-identity shadow policy:" not in rendered


def test_digest_parses_and_renders_shadow_only_tables() -> None:
    payload = identity_event(line_no=105)
    records = [digest.Record(
        ts=datetime(2026, 7, 10, 8, 0), level="INFO", src="mrs", line=1,
        msg="GENERATED_IDENTITY_POLICY_SHADOW_RESULT " + json.dumps(payload, separators=(",", ":")),
        path="test.log", ordinal=1,
    )]
    report = digest.analyse(records)
    rendered = digest.render_markdown(report)
    assert report["generated_identity_shadow"]["summary"]["production_winner_origin_only_excluded"] == 1
    assert "## Generated identity-policy shadow scoring" in rendered
    assert "shadow-only and hypothetical" in rendered
    assert "does not imply that the identity-policy shadow winner was posted" in rendered
    assert "| time | line_no | production | action | shadow | production score | shadow score | phase |" in rendered
    assert "Production winners excluded by origin-quote-only shadow policy:" in rendered


def test_digest_policy_denominator_and_zero_safe() -> None:
    assert digest.generated_identity_policy_summary([])["winner_change_percent"] == 0
    summary = digest.generated_identity_policy_summary([policy_event(), policy_event(origin_quote_only_excluded_count=0, winner_changed_by_policy=False)])
    assert summary["policy_relevant_observations"] == 1
    assert summary["winner_changes"] == 1
    assert summary["winner_change_percent"] == 100
    assert summary["replacement_source_transitions"] == [("generated->original", 1)]


def test_policy_neutral_equal_score_tie_is_not_reported_as_policy_change() -> None:
    event = policy_event(
        baseline_winner="t34.jpg",
        baseline_winner_source="original",
        baseline_winner_score=31.8,
        baseline_identity_action="original_unchanged",
        production_winner="t45.jpg",
        production_winner_source="original",
        production_policy_score=31.8,
        production_identity_action="original_unchanged",
        winner_changed_by_policy=True,  # Legacy event emitted before telemetry correction.
        origin_quote_only_excluded_count=0,
        excluded_generated_basenames=[],
        replacement_source_transition="original->original",
    )
    for key in (
        "counterfactual_comparison_version", "counterfactual_comparison_valid",
        "counterfactual_policy_winner", "policy_effect", "baseline_winner_differs",
    ):
        event.pop(key, None)
    summary = digest.generated_identity_policy_summary([event])
    assert summary["policy_relevant_observations"] == 0
    assert summary["identity_policy_winner_changes"] == summary["winner_changes"] == 0
    assert summary["policy_neutral_baseline_differences"] == 1
    assert summary["policy_neutral_equal_score_tie_resolutions"] == 1

    record = digest.Record(
        ts=digest.parse_dt("2026-07-18 09:04:04"), level="INFO", src="test", line=1,
        msg="GENERATED_IDENTITY_POLICY_APPLIED " + json.dumps(event), path="test.log", ordinal=1,
    )
    rendered = digest.render_markdown(digest.analyse([record]))
    assert "identity_policy_winner_changed         = 0 (0.0%)" in rendered
    assert "Baseline winners changed by the generated-identity policy:" not in rendered
    assert "Policy-neutral baseline differences:" in rendered
    assert "equal-score production tie resolution" in rendered
    assert "original_unchanged" not in rendered


def test_legacy_policy_difference_is_not_claimed_as_counterfactual_causation() -> None:
    event = policy_event()
    for key in (
        "counterfactual_comparison_version", "counterfactual_comparison_valid",
        "counterfactual_policy_winner", "policy_effect", "baseline_winner_differs",
    ):
        event.pop(key, None)
    summary = digest.generated_identity_policy_summary([event])
    assert summary["identity_policy_winner_changes"] == 0
    assert summary["legacy_policy_causation_unverified"] == 1


def test_policy_digest_is_deterministic_for_identical_events() -> None:
    event = policy_event()
    record = digest.Record(
        ts=digest.parse_dt("2026-07-18 09:04:04"), level="INFO", src="test", line=1,
        msg="GENERATED_IDENTITY_POLICY_APPLIED " + json.dumps(event), path="test.log", ordinal=1,
    )
    assert digest.render_markdown(digest.analyse([record])) == digest.render_markdown(digest.analyse([record]))


def test_digest_parses_policy_event_and_never_describes_baseline_as_posted() -> None:
    record = digest.Record(ts=digest.parse_dt("2026-07-10 12:00:00"), level="INFO", src="test", line=1, msg="GENERATED_IDENTITY_POLICY_APPLIED " + json.dumps(policy_event()), path="test.log", ordinal=1)
    report = digest.analyse([record])
    assert report["generated_identity_policy"]["summary"]["observations"] == 1
    rendered = digest.render_markdown(report)
    assert "## Generated identity policy" in rendered
    assert "active in real production" in rendered
    assert "counterfactual baseline image was not posted" in rendered


@pytest.mark.parametrize("phase", ["normal", "forced_cycle_reset", "last_image_fallback"])
def test_event_preserves_selection_phase(phase: str) -> None:
    summary = digest.generated_identity_policy_summary([policy_event(selection_phase=phase, recovery_effect=phase if phase != "normal" else "none")])
    assert (phase, 1) in summary["selection_phases"]
