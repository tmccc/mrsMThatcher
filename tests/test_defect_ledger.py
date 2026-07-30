"""Focused tests for the canonical defect-ledger validator."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools import defect_ledger as ledger_tool
from tools import strict_json


ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "defect_ledger.json"
SCHEMA_PATH = ROOT / "defect_ledger.schema.json"
INVARIANTS_PATH = ROOT / "production_invariants.json"
MARKDOWN_PATH = ROOT / "DEFECT_LEDGER.md"
DIAGNOSIS_PATH = ROOT / "why_code_reviews_continue_to_find_major_problems.md"


def _documents() -> tuple[dict, dict, dict]:
    return (
        strict_json.load(LEDGER_PATH),
        strict_json.load(SCHEMA_PATH),
        strict_json.load(INVARIANTS_PATH),
    )


def _validate(
    ledger: dict,
    schema: dict,
    invariants: dict,
    *,
    markdown: bool = False,
    fallback: bool = True,
    release_base: str | None = None,
) -> ledger_tool.ValidationReport:
    return ledger_tool.validate_ledger(
        ledger,
        schema,
        invariants,
        repository_root=ROOT,
        markdown_path=MARKDOWN_PATH if markdown else None,
        diagnosis_path=DIAGNOSIS_PATH if markdown else None,
        force_fallback_schema=fallback,
        release_base=release_base,
    )


@pytest.mark.parametrize(
    "payload",
    [
        '{"defects":[],"defects":[]}',
        '{"outer":{"status":"one","status":"two"}}',
        '{"value":Infinity}',
    ],
)
def test_defect_control_json_is_strict(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "ledger.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(strict_json.StrictJSONError):
        ledger_tool.load_json_document(path)


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


def test_release_assurance_findings_are_explicit_and_runtime_json_gap_remains() -> None:
    ledger, _schema, _invariants = _documents()
    records = {item["id"]: item for item in ledger["defects"]}
    expected = {
        "DEF-0018": "INV-REL-JSON-001",
        "DEF-0019": "INV-REL-TRUST-001",
        "DEF-0020": "INV-REL-SANDBOX-001",
        "DEF-0021": "INV-REL-IMPORT-001",
        "DEF-0022": "INV-REL-CMD-001",
        "DEF-0023": "INV-REL-ART-001",
    }
    for defect_id, invariant_id in expected.items():
        record = records[defect_id]
        assert record["status"] == "assurance-weakness"
        assert record["severity"] == "assurance"
        assert record["invariant_ids"] == [invariant_id]
        assert record["fix"]["state"] == "unfixed"

    runtime_gap = records["DEF-0017"]
    assert runtime_gap["status"] == "active"
    assert runtime_gap["defect_class"] == "runtime-defect"
    assert "INV-REL-JSON-001" not in runtime_gap["invariant_ids"]


def test_ledger_separates_baseline_cutoff_candidate_and_deployment_identity() -> None:
    ledger, schema, invariants = _documents()
    identity = ledger["identity_scope"]
    baseline = identity["production_baseline"]
    cutoff = identity["ledger_evidence_cutoff"]
    candidate = identity["candidate_under_review"]
    deployment = identity["production_deployment_observation"]
    regeneration = identity["post_merge_regeneration"]

    assert baseline["commit"] != cutoff["commit"]
    assert baseline["tree"] != cutoff["tree"]
    assert "does not claim" in cutoff["difference_from_production_baseline"]
    assert candidate["identity_source"] == "external-release-attestation"
    assert candidate["stored_in_ledger"] is False
    assert deployment["repository_commit"] == baseline["commit"]
    assert deployment["loaded_process_identity_status"].endswith("-unattested")
    assert regeneration["required"] is True
    assert regeneration["release_base_must_equal_evidence_cutoff"] is True

    report = _validate(
        ledger,
        schema,
        invariants,
        release_base=cutoff["commit"],
    )
    assert report.ok, "\n".join(report.errors)


def test_release_base_after_evidence_cutoff_requires_regeneration() -> None:
    ledger, schema, invariants = _documents()
    cutoff = ledger["identity_scope"]["ledger_evidence_cutoff"]["commit"]
    outside_commit = ledger_tool._git(
        ROOT, "rev-list", "--all", "--not", cutoff, "--max-count=1"
    ).stdout.strip()
    assert outside_commit

    report = _validate(
        ledger,
        schema,
        invariants,
        release_base=outside_commit,
    )

    assert not report.ok
    assert any(
        "release base differs from ledger evidence cut-off" in error
        and "regeneration is required" in error
        for error in report.errors
    )


def test_committed_candidate_identity_is_rejected_as_self_referential() -> None:
    ledger, schema, invariants = _documents()
    candidate = ledger["identity_scope"]["candidate_under_review"]
    candidate["stored_in_ledger"] = True
    candidate["candidate_commit"] = ledger_tool._git(
        ROOT, "rev-parse", "HEAD"
    ).stdout.strip()

    report = _validate(ledger, schema, invariants)

    assert not report.ok
    combined = "\n".join(report.errors)
    assert "stored_in_ledger" in combined or "additional property" in combined
    assert "candidate identity must remain external" in combined


def test_cutoff_tree_must_belong_to_cutoff_commit() -> None:
    ledger, schema, invariants = _documents()
    ledger["identity_scope"]["ledger_evidence_cutoff"]["tree"] = "0" * 40

    report = _validate(ledger, schema, invariants)

    assert not report.ok
    assert any(
        "ledger evidence cut-off tree does not belong" in error
        for error in report.errors
    )


def test_fix_deployment_and_verification_claims_cannot_postdate_cutoff() -> None:
    ledger, schema, invariants = _documents()
    cutoff = ledger["identity_scope"]["ledger_evidence_cutoff"]["commit"]
    outside_commit = ledger_tool._git(
        ROOT, "rev-list", "--all", "--not", cutoff, "--max-count=1"
    ).stdout.strip()
    assert outside_commit

    fixed = copy.deepcopy(ledger)
    fixed_defect = next(
        item for item in fixed["defects"] if item["fix"]["state"] == "fixed"
    )
    fixed_defect["fix"]["commit"] = outside_commit
    fixed_report = _validate(fixed, schema, invariants)
    assert any(
        "fix claim commit is outside the ledger evidence cut-off" in error
        for error in fixed_report.errors
    )

    deployed = copy.deepcopy(ledger)
    deployed_defect = next(
        item
        for item in deployed["defects"]
        if item["deployment"]["state"] == "deployed-unverified"
    )
    deployed_defect["deployment"]["observed_commit"] = outside_commit
    deployed_report = _validate(deployed, schema, invariants)
    assert any(
        "deployment claim commit is outside the ledger evidence cut-off" in error
        for error in deployed_report.errors
    )

    verified = copy.deepcopy(ledger)
    verified_defect = next(
        item
        for item in verified["defects"]
        if item["deployment"]["state"] == "deployed-verified"
    )
    verified_defect["deployment"]["observed_commit"] = outside_commit
    verified_report = _validate(verified, schema, invariants)
    assert any(
        "deployment claim commit is outside the ledger evidence cut-off" in error
        for error in verified_report.errors
    )


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
    assert ledger_tool.render_identity_scope(
        ledger
    ) == ledger_tool.render_identity_scope(copy.deepcopy(ledger))
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
    assert ledger_tool._section_block(
        updated,
        ledger_tool.IDENTITY_SCOPE_PATTERN,
    ) == ledger_tool.render_identity_scope(ledger)
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


def test_candidate_lineage_defects_are_not_rendered_as_observed_production() -> None:
    ledger, _schema, _invariants = _documents()
    repaired = next(
        item for item in ledger["defects"] if item["id"] == "DEF-0030"
    )
    active = next(
        item for item in ledger["defects"] if item["id"] == "DEF-0031"
    )

    assert ledger_tool._deployment_cell(repaired) == (
        "not-deployed; observed `be882e81`"
    )
    assert ledger_tool._deployment_cell(active) == (
        "not deployed; absent from observed production `be882e81`"
    )
