"""Focused tests for the canonical defect-ledger validator."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from tools import defect_ledger as ledger_tool


ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "defect_ledger.json"
SCHEMA_PATH = ROOT / "defect_ledger.schema.json"
INVARIANTS_PATH = ROOT / "production_invariants.json"
MARKDOWN_PATH = ROOT / "DEFECT_LEDGER.md"
DIAGNOSIS_PATH = ROOT / "why_code_reviews_continue_to_find_major_problems.md"


def _documents() -> tuple[dict, dict, dict]:
    return (
        json.loads(LEDGER_PATH.read_text(encoding="utf-8")),
        json.loads(SCHEMA_PATH.read_text(encoding="utf-8")),
        json.loads(INVARIANTS_PATH.read_text(encoding="utf-8")),
    )


def _validate(
    ledger: dict,
    schema: dict,
    invariants: dict,
    *,
    markdown: bool = False,
    fallback: bool = True,
) -> ledger_tool.ValidationReport:
    return ledger_tool.validate_ledger(
        ledger,
        schema,
        invariants,
        repository_root=ROOT,
        markdown_path=MARKDOWN_PATH if markdown else None,
        diagnosis_path=DIAGNOSIS_PATH if markdown else None,
        force_fallback_schema=fallback,
    )


def test_real_ledger_is_valid_and_markdown_summary_is_synchronized() -> None:
    ledger, schema, invariants = _documents()

    report = _validate(
        ledger,
        schema,
        invariants,
        markdown=True,
    )

    assert report.schema_backend == "built-in-draft2020-subset"
    assert report.ok, "\n".join(report.errors)
    assert dict(report.status_counts)


def test_builtin_schema_fallback_rejects_unknown_fields() -> None:
    ledger, schema, invariants = _documents()
    ledger["defects"][0]["unreviewed_field"] = True

    report = _validate(ledger, schema, invariants)

    assert not report.ok
    assert any(
        "unreviewed_field" in error and "additional property" in error
        for error in report.errors
    )


def test_duplicate_and_unsorted_stable_defect_ids_are_rejected() -> None:
    ledger, schema, invariants = _documents()
    first, second = ledger["defects"][:2]
    second["id"] = first["id"]

    duplicate = _validate(ledger, schema, invariants)

    assert not duplicate.ok
    assert any("duplicate defect ID" in error for error in duplicate.errors)

    ledger, schema, invariants = _documents()
    ledger["defects"][0], ledger["defects"][1] = (
        ledger["defects"][1],
        ledger["defects"][0],
    )
    unsorted = _validate(ledger, schema, invariants)
    assert not unsorted.ok
    assert any("must be sorted" in error for error in unsorted.errors)


def test_unknown_invariant_reference_is_rejected() -> None:
    ledger, schema, invariants = _documents()
    ledger["defects"][0]["invariant_ids"] = ["INV-NOT-REGISTERED-999"]

    report = _validate(ledger, schema, invariants)

    assert not report.ok
    assert any(
        "invariant reference does not resolve" in error for error in report.errors
    )


def test_unknown_values_require_exact_substantive_explanations() -> None:
    ledger, schema, invariants = _documents()
    defect = ledger["defects"][0]
    defect["fix"]["commit"] = "unknown"

    missing = _validate(ledger, schema, invariants)

    assert not missing.ok
    assert any(
        "fix.commit has no recorded explanation" in error
        for error in missing.errors
    )

    defect["unknowns"].append(
        {"field": "fix.commit", "explanation": "too short"}
    )
    shallow = _validate(ledger, schema, invariants)
    assert not shallow.ok
    assert any("lacks a substantive explanation" in error for error in shallow.errors)


def test_fixed_and_repaired_not_deployed_states_require_bound_evidence() -> None:
    ledger, schema, invariants = _documents()
    defect = next(
        item for item in ledger["defects"] if item["fix"]["state"] == "fixed"
    )
    defect["status"] = "repaired-not-deployed"
    defect["tests"] = []
    defect["deployment"]["state"] = "deployed-unverified"

    report = _validate(ledger, schema, invariants)

    assert not report.ok
    combined = "\n".join(report.errors)
    assert "fixed state requires regression evidence" in combined
    assert (
        "repaired-not-deployed requires fixed plus "
        "deployment.state=not-deployed"
    ) in combined


def test_deployed_unverified_status_requires_fixed_matching_deployment() -> None:
    ledger, schema, invariants = _documents()
    defect = next(
        item for item in ledger["defects"] if item["fix"]["state"] == "fixed"
    )
    defect["status"] = "deployed-unverified"
    defect["deployment"]["state"] = "not-deployed"

    report = _validate(ledger, schema, invariants)

    assert not report.ok
    assert any(
        "deployed-unverified requires fixed plus" in error
        for error in report.errors
    )


def test_deployed_verified_needs_post_deploy_behavior_evidence() -> None:
    ledger, schema, invariants = _documents()
    defect = next(
        item for item in ledger["defects"] if item["fix"]["state"] == "fixed"
    )
    defect["status"] = "deployed-verified"
    defect["deployment"]["state"] = "deployed-verified"
    defect["deployment"]["observed_commit"] = defect["fix"]["commit"]
    defect["deployment"]["evidence"] = "Commit ancestry was inspected."
    defect["evidence"] = [
        item
        for item in defect["evidence"]
        if item["type"] in ledger_tool.LOCAL_EVIDENCE_TYPES
    ]

    report = _validate(ledger, schema, invariants)

    assert not report.ok
    combined = "\n".join(report.errors)
    assert "structured post-deployment evidence" in combined
    assert "cannot rely only on patch-local" in combined


def test_patch_local_evidence_cannot_claim_system_wide_closure() -> None:
    ledger, schema, invariants = _documents()
    ledger["defects"][0]["evidence"][0]["claim"] = (
        "This commit proves system-wide closure with no remaining risk."
    )

    report = _validate(ledger, schema, invariants)

    assert not report.ok
    assert any(
        "patch-local commit evidence overclaims system-wide closure" in error
        for error in report.errors
    )


def test_supersession_relations_must_resolve_and_be_reciprocal() -> None:
    ledger, schema, invariants = _documents()
    child = next(item for item in ledger["defects"] if item["id"] == "DEF-0006")
    child["relations"]["supersedes"] = ["DEF-9999"]

    report = _validate(ledger, schema, invariants)

    assert not report.ok
    assert any("supersedes does not resolve: DEF-9999" in error for error in report.errors)

    ledger, schema, invariants = _documents()
    child = next(item for item in ledger["defects"] if item["id"] == "DEF-0006")
    child["relations"]["supersedes"] = []
    report = _validate(ledger, schema, invariants)
    assert not report.ok
    assert any("superseded_by relation" in error and "not reciprocal" in error for error in report.errors)


def test_missing_test_node_is_rejected() -> None:
    ledger, schema, invariants = _documents()
    ledger["defects"][0]["tests"][0]["nodeid"] = "test_does_not_exist"

    report = _validate(ledger, schema, invariants)

    assert not report.ok
    assert any("test node does not exist" in error for error in report.errors)


def test_test_nodes_are_resolved_at_the_recorded_commit() -> None:
    ledger, schema, invariants = _documents()
    test = ledger["defects"][0]["tests"][0]
    test["path"] = "tests/test_defect_ledger.py"
    test["nodeid"] = "test_real_ledger_is_valid_and_markdown_summary_is_synchronized"

    report = _validate(ledger, schema, invariants)

    assert not report.ok
    assert any(
        "does not exist at recorded commit" in error
        for error in report.errors
    )


def test_renderer_is_deterministic_and_markdown_drift_is_detected(
    tmp_path: Path,
) -> None:
    ledger, schema, invariants = _documents()
    first = ledger_tool.render_summary(ledger)
    second = ledger_tool.render_summary(copy.deepcopy(ledger))
    assert first == second
    assert "## Summary" in first
    assert "DEF-0001" in first
    assert ledger_tool.render_scope(ledger) == ledger_tool.render_scope(
        copy.deepcopy(ledger)
    )
    assert ledger_tool.render_chronology(ledger) == ledger_tool.render_chronology(
        copy.deepcopy(ledger)
    )
    assert ledger_tool.render_diagnosis_chronology(
        ledger
    ) == ledger_tool.render_diagnosis_chronology(copy.deepcopy(ledger))

    stale = tmp_path / "DEFECT_LEDGER.md"
    stale.write_text("# Ledger\n\n## Summary\n\nstale\n", encoding="utf-8")
    report = ledger_tool.validate_ledger(
        ledger,
        schema,
        invariants,
        repository_root=ROOT,
        markdown_path=stale,
        force_fallback_schema=True,
    )
    assert not report.ok
    assert any("Markdown Summary is stale" in error for error in report.errors)

    output = tmp_path / "rendered.md"
    output.write_text(MARKDOWN_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    diagnosis_output = tmp_path / "diagnosis.md"
    diagnosis_output.write_text(
        DIAGNOSIS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    result = ledger_tool.main(
        [
            "render",
            "--repo-root",
            str(ROOT),
            "--output",
            str(output),
            "--diagnosis",
            str(diagnosis_output),
            "--write",
        ]
    )
    assert result == 0
    updated = output.read_text(encoding="utf-8")
    assert ledger_tool._summary_block(updated) == first
    assert ledger_tool._section_block(
        updated,
        ledger_tool.SCOPE_PATTERN,
    ) == ledger_tool.render_scope(ledger)
    assert ledger_tool._section_block(
        updated,
        ledger_tool.CHRONOLOGY_PATTERN,
    ) == ledger_tool.render_chronology(ledger)
    assert "## Records" in updated
    assert ledger_tool._section_block(
        diagnosis_output.read_text(encoding="utf-8"),
        ledger_tool.DIAGNOSIS_PATTERN,
    ) == ledger_tool.render_diagnosis_chronology(ledger)
