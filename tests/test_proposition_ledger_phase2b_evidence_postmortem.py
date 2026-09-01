from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tools import proposition_ledger_phase2b_evidence_postmortem as postmortem


PROJECT_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_DIR / "tools/proposition_ledger_phase2b_evidence_postmortem.py"


def _span(
    exact_text: str,
    *,
    start: Any,
    end: Any,
    turn_id: str = "synthetic-turn",
) -> dict[str, Any]:
    return {
        "end_char": end,
        "exact_text": exact_text,
        "start_char": start,
        "turn_id": turn_id,
    }


def _parsed(*spans: dict[str, Any], marker: Any = None) -> dict[str, Any]:
    return {
        "new_propositions": [
            {
                "exact_evidence_spans": list(spans),
                "synthetic_non_evidence_marker": marker,
            }
        ]
    }


def _diagnose(
    text: str, span: dict[str, Any], *, turn_id: str = "synthetic-turn"
) -> dict[str, Any]:
    return postmortem.diagnose_evidence_span(
        span,
        current_turn_id=turn_id,
        current_turn_text=text,
        pointer="/new_propositions/0/exact_evidence_spans/0",
    )


# 38. A sole literal match is recoverable even when old coordinates are wrong.
def test_unique_exact_text_with_wrong_coordinates_is_diagnostically_recoverable() -> None:
    parsed = _parsed(_span("target", start=0, end=6), marker={"keep": [1, 2]})
    analysis = postmortem.analyse_parsed_evidence(
        parsed,
        current_turn_id="synthetic-turn",
        current_turn_text="prefix target suffix",
    )
    assert analysis["diagnostic_recovery_status"] == (
        "uniquely_recoverable_from_exact_text"
    )
    assert analysis["evidence_span_count"] == 1
    assert "exact_text_present_once_coordinates_wrong" in analysis[
        "evidence_spans"
    ][0]["diagnostic_categories"]
    recovered = postmortem.build_diagnostic_canonical_delta(
        parsed,
        current_turn_id="synthetic-turn",
        current_turn_text="prefix target suffix",
    )
    assert recovered["new_propositions"][0]["exact_evidence_spans"] == [
        {
            "end_char": 13,
            "exact_text": "target",
            "start_char": 7,
            "turn_id": "synthetic-turn",
        }
    ]
    assert recovered["new_propositions"][0]["synthetic_non_evidence_marker"] == {
        "keep": [1, 2]
    }


# 39. Repeated text is never guessed from the old response.
def test_repeated_exact_text_requires_occurrence_disambiguation() -> None:
    parsed = _parsed(_span("echo", start=0, end=4))
    analysis = postmortem.analyse_parsed_evidence(
        parsed,
        current_turn_id="synthetic-turn",
        current_turn_text="echo then echo",
    )
    assert analysis["diagnostic_recovery_status"] == (
        "recoverable_only_with_occurrence_disambiguation"
    )
    occurrence = analysis["evidence_spans"][0]["literal_occurrence_analysis"]
    assert occurrence == {
        "match_count": 2,
        "matches": [
            {"end": 4, "occurrence_index": 0, "start": 0},
            {"end": 14, "occurrence_index": 1, "start": 10},
        ],
        "unique": False,
    }
    with pytest.raises(postmortem.PostmortemError, match="not uniquely"):
        postmortem.build_diagnostic_canonical_delta(
            parsed,
            current_turn_id="synthetic-turn",
            current_turn_text="echo then echo",
        )


def test_overlapping_occurrences_are_all_counted() -> None:
    assert postmortem.find_overlapping_occurrences("aaa", "aa") == [(0, 2), (1, 3)]


# 40. Literal absence fails closed; no normalised or fuzzy substitute is used.
@pytest.mark.parametrize(
    ("current", "supplied"),
    [
        ("nothing applicable", "missing"),
        ("Cafe\u0301", "Caf\u00e9"),
        ("curly \u201cquote\u201d", 'curly "quote"'),
        ("space  differs", "space differs"),
    ],
)
def test_absent_exact_text_is_unrecoverable_without_normalisation_or_fuzzing(
    current: str, supplied: str
) -> None:
    parsed = _parsed(_span(supplied, start=0, end=len(current)))
    analysis = postmortem.analyse_parsed_evidence(
        parsed,
        current_turn_id="synthetic-turn",
        current_turn_text=current,
    )
    assert analysis["diagnostic_recovery_status"] == (
        "not_recoverable_exact_text_absent"
    )
    assert analysis["evidence_spans"][0]["literal_occurrence_analysis"][
        "match_count"
    ] == 0


# 41. The obsolete provider turn identity is diagnostic input, not a blocker.
def test_wrong_provider_turn_id_does_not_prevent_exact_text_diagnosis() -> None:
    parsed = _parsed(
        _span("only match", start=0, end=10, turn_id="obsolete-wrong-turn")
    )
    analysis = postmortem.analyse_parsed_evidence(
        parsed,
        current_turn_id="synthetic-current-turn",
        current_turn_text="only match",
    )
    assert analysis["diagnostic_recovery_status"] == (
        "uniquely_recoverable_from_exact_text"
    )
    assert "wrong_current_turn_reference" in analysis["evidence_spans"][0][
        "diagnostic_categories"
    ]
    recovered = postmortem.build_diagnostic_canonical_delta(
        parsed,
        current_turn_id="synthetic-current-turn",
        current_turn_text="only match",
    )
    assert recovered["new_propositions"][0]["exact_evidence_spans"][0][
        "turn_id"
    ] == "synthetic-current-turn"


# 42. End-inclusive offsets have one exact, bounded signature.
def test_codepoint_inclusive_end_signature() -> None:
    diagnostic = _diagnose(
        "prefix target suffix", _span("target", start=7, end=12)
    )
    assert "codepoint_end_inclusive_signature" in diagnostic[
        "coordinate_signatures"
    ]
    assert diagnostic["literal_occurrence_analysis"]["matches"] == [
        {"end": 13, "occurrence_index": 0, "start": 7}
    ]


# 43. UTF-16 is recognised only when both unit boundaries are exact.
def test_utf16_code_unit_signature_and_invalid_boundary_rejection() -> None:
    diagnostic = _diagnose("\U0001f600target", _span("target", start=2, end=8))
    assert "utf16_code_unit_signature" in diagnostic["coordinate_signatures"]
    invalid = _diagnose("\U0001f600target", _span("target", start=1, end=7))
    assert "utf16_code_unit_signature" not in invalid["coordinate_signatures"]


# 44. UTF-8 likewise requires exact byte boundaries and an exact resulting slice.
def test_utf8_byte_offset_signature_and_invalid_boundary_rejection() -> None:
    diagnostic = _diagnose("\u00e9target", _span("target", start=2, end=8))
    assert "utf8_byte_offset_signature" in diagnostic["coordinate_signatures"]
    invalid = _diagnose("\u00e9target", _span("target", start=1, end=7))
    assert "utf8_byte_offset_signature" not in invalid["coordinate_signatures"]


# 45. More than one exact convention remains explicitly ambiguous.
def test_ambiguous_coordinate_signatures_are_preserved() -> None:
    # At supplied boundaries 4:5, UTF-16 selects the second "a" and UTF-8
    # selects the first; canonical code points select "c". Both signatures are
    # mechanically plausible and neither is privileged.
    diagnostic = _diagnose("\U0001f600abac", _span("a", start=4, end=5))
    assert diagnostic["coordinate_signatures"] == [
        "utf16_code_unit_signature",
        "utf8_byte_offset_signature",
        "several_plausible_coordinate_conventions",
    ]


def test_invalid_integer_forms_have_bounded_categories() -> None:
    boolean = _diagnose("target", _span("target", start=True, end=6))
    assert "boolean_used_as_integer" in boolean["diagnostic_categories"]
    assert "start_or_end_not_integer" in boolean["diagnostic_categories"]
    malformed = _diagnose("target", _span("target", start="0", end=None))
    assert malformed["diagnostic_categories"] == [
        "start_or_end_not_integer",
        "exact_text_present_once_coordinates_wrong",
    ]


def test_duplicate_unique_resolutions_are_flagged_without_mutation() -> None:
    parsed = _parsed(
        _span("target", start=0, end=6),
        _span("target", start=7, end=13),
    )
    analysis = postmortem.analyse_parsed_evidence(
        parsed,
        current_turn_id="synthetic-turn",
        current_turn_text="prefix target suffix",
    )
    assert all(
        "duplicate_resolved_span" in item["diagnostic_categories"]
        for item in analysis["evidence_spans"]
    )


def test_same_resolved_span_in_different_evidence_arrays_is_not_duplicate() -> None:
    parsed = {
        "new_propositions": [
            {"exact_evidence_spans": [_span("target", start=7, end=13)]},
            {"exact_evidence_spans": [_span("target", start=7, end=13)]},
        ]
    }
    analysis = postmortem.analyse_parsed_evidence(
        parsed,
        current_turn_id="synthetic-turn",
        current_turn_text="prefix target suffix",
    )
    assert all(
        "duplicate_resolved_span" not in item["diagnostic_categories"]
        for item in analysis["evidence_spans"]
    )


# 46. Truncation needs structural and/or finish-reason evidence.
def test_strict_json_truncation_classification() -> None:
    parsed, diagnostic = postmortem.strict_json_diagnostic(
        b'{"answer":"unterminated', finish_reason="REASON_MAX_LEN"
    )
    assert parsed is None
    assert diagnostic["strict_json_failure_category"] == "truncated_json"
    assert diagnostic["appears_truncated"] is True
    assert "unterminated_string" in diagnostic["structural_truncation_evidence"]


@pytest.mark.parametrize(
    ("raw", "category"),
    [
        (b"", "empty_response"),
        (b"\xff", "invalid_utf8"),
        (b'{"a":1,"a":2}', "duplicate_member"),
        (b'{"a":NaN}', "non_finite_number"),
        (b"```json\n{}\n```", "code_fence_or_surrounding_prose"),
        (b"{} {}", "multiple_top_level_values"),
        (b'{"a":,}', "syntactically_invalid_json"),
    ],
)
def test_strict_json_failure_taxonomy(raw: bytes, category: str) -> None:
    parsed, diagnostic = postmortem.strict_json_diagnostic(
        raw, finish_reason="REASON_STOP"
    )
    assert parsed is None
    assert diagnostic["strict_json_failure_category"] == category


def test_strict_json_success_requires_one_object() -> None:
    parsed, diagnostic = postmortem.strict_json_diagnostic(
        b'{"synthetic":true}', finish_reason="REASON_STOP"
    )
    assert parsed == {"synthetic": True}
    assert diagnostic["strict_json_status"] == "passed"


# 47. Diagnostic construction is copy-on-write and never alters its input.
def test_original_synthetic_response_is_never_mutated() -> None:
    parsed = _parsed(
        _span("target", start=999, end=1005, turn_id="wrong-turn"),
        marker={"nested": ["unchanged"]},
    )
    before = copy.deepcopy(parsed)
    before_bytes = postmortem.pretty_json_bytes(parsed)
    recovered = postmortem.build_diagnostic_canonical_delta(
        parsed,
        current_turn_id="synthetic-turn",
        current_turn_text="prefix target suffix",
    )
    assert parsed == before
    assert postmortem.pretty_json_bytes(parsed) == before_bytes
    assert recovered is not parsed


# 48. Every diagnostic replay is unmistakably counterfactual.
def test_diagnostic_materialisation_label_is_counterfactual() -> None:
    assert postmortem.diagnostic_counterfactual_metadata() == {
        "counterfactual_label": "diagnostic_counterfactual_only",
        "diagnostic_method": "posthoc_unique_exact_text_resolution",
        "phase2a_experimental_outcome_revised": False,
    }


# 49. Recovered counts cannot be turned into a model selection.
def test_no_model_winner_is_inferred_from_recovered_counts() -> None:
    assert postmortem.research_guardrails() == {
        "model_profile_selected": False,
        "model_winner_inferred": False,
        "phase2a_experimental_outcome_revised": False,
        "provider_calls": 0,
    }


def test_no_stable_issue_abstention_drives_mechanical_cross_tabs() -> None:
    call = {
        "original_materialised_ledger": {
            "answer_targets": [],
            "conversational_obligations": [],
            "issue_states": [],
            "participant_commitments": [],
            "proposition_relations": [],
            "propositions": [],
            "warnings": [],
        },
        "parsed": {
            **{field: [] for field in postmortem.SEMANTIC_CHANGE_ARRAYS},
            "abstentions": ["no_stable_issue"],
            "extraction_status": "abstained",
        },
        "pilot_conversation_id": "synthetic-conversation",
        "pilot_turn_id": "synthetic-turn",
        "profile_id": "synthetic-profile",
        "turn_index": 0,
    }
    audit = postmortem._audit_materialised_ledgers([call])
    assert audit["aggregate"] == {
        "abstained_extraction_with_no_semantic_additions": 1,
        "complete_extraction_with_no_semantic_additions": 0,
        "no_stable_issue_with_one_or_more_commitments": 0,
        "no_stable_issue_with_one_or_more_propositions": 0,
        "no_stable_issue_with_zero_commitments": 1,
        "no_stable_issue_with_zero_propositions": 1,
    }
    assert audit[
        "mechanical_evidence_supports_further_no_stable_issue_investigation"
    ] is True
    assert audit["no_stable_issue_prompt_revision_made"] is False


def test_import_and_help_make_no_network_or_production_import() -> None:
    script = f"""
import importlib.util, sys
def audit(event, args):
    if event in {{'socket.connect', 'socket.getaddrinfo', 'http.client.connect'}}:
        raise AssertionError('network access during import: ' + event)
sys.addaudithook(audit)
spec = importlib.util.spec_from_file_location('isolated_phase2b_postmortem', {str(MODULE_PATH)!r})
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
if 'mrsMThatcher2' in sys.modules or any(name.startswith('mrsMThatcher2.') for name in sys.modules):
    raise AssertionError('production bot imported')
print('imported-without-network')
"""
    environment = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(PROJECT_DIR),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    imported = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_DIR,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert imported.returncode == 0, imported.stderr
    assert imported.stdout.strip() == "imported-without-network"
    helped = subprocess.run(
        [sys.executable, str(MODULE_PATH), "--help"],
        cwd=PROJECT_DIR,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert helped.returncode == 0, helped.stderr
    assert "--verify-only" in helped.stdout
    assert helped.stderr == ""


def test_source_guard_rejects_any_other_directory_before_artifact_read(
    tmp_path: Path,
) -> None:
    source = tmp_path / "synthetic-source"
    source.mkdir(mode=0o700)
    sentinel = source / "must-not-be-read.txt"
    sentinel.write_text("wholly synthetic sentinel", encoding="utf-8")
    with pytest.raises(postmortem.PostmortemError, match="authorized completed"):
        postmortem.verify_source_run(source)
    assert sentinel.read_text(encoding="utf-8") == "wholly synthetic sentinel"


def test_private_write_then_verify_only_is_byte_stable_and_write_free(
    tmp_path: Path,
) -> None:
    output = tmp_path / "synthetic-private-run"
    output.mkdir(mode=0o700)
    result = {
        "aggregate": {"evidence_span_count": 0, "response_count": 1},
        "artifacts": {
            name: postmortem.pretty_json_bytes(
                {"artifact": name, "synthetic": True}
            )
            for name in postmortem.ROOT_ARTIFACT_NAMES
        },
        "per_calls": [{"pilot_turn_id": "synthetic-turn"}],
        "recoveries": [
            {
                "counterfactual_label": "diagnostic_counterfactual_only",
                "synthetic": True,
            }
        ],
    }
    built = postmortem.write_postmortem(result, output)
    assert built["provider_calls"] == 0
    before = {
        path.relative_to(output).as_posix(): (
            path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode & 0o777
        )
        for path in output.rglob("*")
        if path.is_file()
    }
    verified = postmortem.verify_postmortem(result, output)
    after = {
        path.relative_to(output).as_posix(): (
            path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode & 0o777
        )
        for path in output.rglob("*")
        if path.is_file()
    }
    assert verified == {
        "artifact_count": len(before),
        "byte_equality_verified": True,
        "provider_calls": 0,
        "source_run_unchanged": True,
        "status": "phase2b_postmortem_verify_only_passed",
        "writes_performed": 0,
    }
    assert before == after
    assert all(mode == 0o600 for _, _, mode in before.values())


def test_taxonomy_and_recoverability_vocabulary_is_closed() -> None:
    assert set(postmortem.COORDINATE_SIGNATURES) <= set(
        postmortem.EVIDENCE_FAILURE_TAXONOMY
    )
    assert postmortem.RECOVERABILITY_CLASSES == (
        "uniquely_recoverable_from_exact_text",
        "recoverable_only_with_occurrence_disambiguation",
        "not_recoverable_exact_text_absent",
        "not_recoverable_unparsed",
        "not_recoverable_other",
    )
    assert len(postmortem.STRICT_JSON_FAILURE_CATEGORIES) == 9
    assert set(postmortem._taxonomy_definitions()) == set(
        postmortem.EVIDENCE_FAILURE_TAXONOMY
    )


def test_postmortem_markdown_never_contains_case_identity_or_exact_text() -> None:
    aggregate = {
        "response_count": 1,
        "parsed_response_count": 1,
        "evidence_span_count": 1,
        "original_materialised_count": 0,
        "response_recoverability_counts": {
            "uniquely_recoverable_from_exact_text": 1
        },
        "evidence_failure_category_counts": {},
        "coordinate_signature_counts": {},
    }
    rendered = postmortem._render_postmortem_markdown(aggregate).decode("utf-8")
    assert "synthetic-turn" not in rendered
    assert "target" not in rendered
    assert "provider calls" in rendered.lower()


def test_json_encodings_are_stable_and_do_not_escape_unicode() -> None:
    value = {"combining": "Cafe\u0301", "emoji": "\U0001f600"}
    assert json.loads(postmortem.canonical_json_bytes(value)) == value
    assert postmortem.pretty_json_bytes(value).endswith(b"\n")
    assert b"\\ud83d" not in postmortem.pretty_json_bytes(value)
