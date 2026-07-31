from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from tools import priority0_registry as registry_tool
from tools import strict_json


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "production_invariants.json"
SCHEMA_PATH = ROOT / "production_invariants.schema.json"
MARKDOWN_PATH = ROOT / "PRODUCTION_INVARIANTS.md"

EXPECTED_INVARIANT_IDS = (
    "INV-ELIG-001",
    "INV-ELIG-002",
    "INV-ELIG-003",
    "INV-HCTX-001",
    "INV-ART-001",
    "INV-VETO-001",
    "INV-TXN-REG-001",
    "INV-TXN-MEME-001",
    "INV-TXN-REPLY-001",
    "INV-TXN-HCTX-001",
    "INV-TXN-HIST-001",
    "INV-TXN-PAGE-001",
    "INV-TXN-RECEIPT-001",
    "INV-TXN-AUX-001",
    "INV-CONFIG-001",
    "INV-PAUSE-001",
    "INV-API-001",
    "INV-PROC-001",
    "INV-PROC-002",
    "INV-PROC-003",
    "INV-PROC-004",
    "INV-TEST-001",
    "INV-TEST-002",
    "INV-TEST-003",
    "INV-TEST-004",
    "INV-REL-001",
    "INV-REL-JSON-001",
    "INV-REL-TRUST-001",
    "INV-REL-SANDBOX-001",
    "INV-REL-IMPORT-001",
    "INV-REL-CMD-001",
    "INV-REL-ART-001",
)

EXPLICIT_ASSURANCE_FIELDS = {
    "rationale",
    "owner_subsystem",
    "runtime_consumed_artifacts",
    "preconditions",
    "failure_mode",
    "full_suite_relevance",
    "required_production_deployed_path_checks",
    "evidence_references",
    "last_verified_commit",
    "last_verified_tree",
    "accepted_residual_risk",
}


def _load_real_documents() -> tuple[dict, dict]:
    return (
        strict_json.load(REGISTRY_PATH),
        strict_json.load(SCHEMA_PATH),
    )


def _minimal_registry(tmp_path: Path) -> tuple[dict, dict]:
    real_registry, schema = _load_real_documents()
    (tmp_path / "tests").mkdir()
    (tmp_path / "code.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_sample.py").write_text(
        "def test_contract():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    invariant = {
        "id": "INV-DEMO-001",
        "category": "test_deployment",
        "title": "Synthetic contract",
        "statement": "The synthetic contract remains true.",
        "rationale": "The focused validator tests need one complete valid record.",
        "owner_subsystem": "synthetic_contract",
        "runtime_consumed_artifacts": {
            "status": "none",
            "artifacts": [],
            "explanation": "The synthetic contract consumes no runtime artifact.",
        },
        "preconditions": [
            "The synthetic code path and focused test are present.",
        ],
        "failure_if_violated": "The validator test would no longer model a valid entry.",
        "failure_mode": {
            "mode": "fail_closed",
            "explanation": "Validation rejects an incomplete synthetic record.",
        },
        "criticality": "critical",
        "implementation_status": "implemented",
        "status_rationale": "A synthetic code path and test exist.",
        "verification": {
            "status": "verified",
            "rationale": "The named synthetic test exists."
        },
        "full_suite_relevance": {
            "status": "required",
            "explanation": "The full focused file checks interactions among registry rules.",
        },
        "required_production_deployed_path_checks": {
            "status": "not_applicable",
            "checks": [],
            "explanation": "This temporary test fixture is not deployed.",
        },
        "evidence_references": [
            {
                "type": "test",
                "reference": "tests/test_sample.py::test_contract",
                "claim": "The focused synthetic test exercises this contract.",
            }
        ],
        "last_verified_commit": {
            "status": "unknown",
            "value": None,
            "explanation": "A temporary test fixture has no Git commit.",
        },
        "last_verified_tree": {
            "status": "unknown",
            "value": None,
            "explanation": "A temporary test fixture has no Git tree.",
        },
        "accepted_residual_risk": {
            "status": "none",
            "explanation": "No residual risk exists in this synthetic scope.",
        },
        "affected_paths": [
            "code.py"
        ],
        "enforcement": {
            "files": [
                "code.py"
            ],
            "tests": [
                "tests/test_sample.py::test_contract"
            ],
            "validations": [
                {
                    "validation_id": "pytest",
                    "selectors": ["tests/test_sample.py"],
                    "purpose": "Exercise the synthetic contract."
                }
            ]
        },
        "known_gaps": []
    }
    registry = {
        key: copy.deepcopy(value)
        for key, value in real_registry.items()
        if key != "invariants"
    }
    registry["priority0_control_paths"] = ["code.py"]
    registry["generated_artifact_classifications"] = []
    registry["expected_full_suite_skips"] = []
    registry["invariants"] = [invariant]
    return registry, schema


@pytest.mark.parametrize(
    "payload",
    [
        '{"invariants":[],"invariants":[]}',
        '{"outer":{"id":"one","id":"two"}}',
        '{"value":NaN}',
    ],
)
def test_registry_control_json_is_strict(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "registry.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(strict_json.StrictJSONError):
        registry_tool.load_json_document(path)


def test_registry_uses_the_agreed_stable_ids() -> None:
    registry, _schema = _load_real_documents()

    assert tuple(item["id"] for item in registry["invariants"]) == (
        EXPECTED_INVARIANT_IDS
    )
    assert len(set(EXPECTED_INVARIANT_IDS)) == len(EXPECTED_INVARIANT_IDS)


def test_every_record_exposes_explicit_assurance_semantics() -> None:
    registry, _schema = _load_real_documents()
    records = {
        invariant["id"]: invariant
        for invariant in registry["invariants"]
    }

    assert set(records) == set(EXPECTED_INVARIANT_IDS)
    for invariant_id, invariant in records.items():
        assert EXPLICIT_ASSURANCE_FIELDS <= invariant.keys(), invariant_id
        assert invariant["rationale"].strip(), invariant_id
        assert re.fullmatch(
            r"[a-z0-9]+(?:_[a-z0-9]+)*",
            invariant["owner_subsystem"],
        ), invariant_id
        assert invariant["preconditions"], invariant_id
        assert all(item.strip() for item in invariant["preconditions"]), invariant_id

        runtime_artifacts = invariant["runtime_consumed_artifacts"]
        assert runtime_artifacts["explanation"].strip(), invariant_id
        if runtime_artifacts["status"] in {"direct", "indirect"}:
            assert runtime_artifacts["artifacts"], invariant_id
        else:
            assert runtime_artifacts["artifacts"] == [], invariant_id

        assert invariant["failure_mode"]["explanation"].strip(), invariant_id
        assert invariant["full_suite_relevance"]["explanation"].strip(), invariant_id

        deployed_checks = invariant[
            "required_production_deployed_path_checks"
        ]
        assert deployed_checks["explanation"].strip(), invariant_id
        if deployed_checks["status"] == "required":
            assert deployed_checks["checks"], invariant_id
        else:
            assert deployed_checks["checks"] == [], invariant_id

        evidence = invariant["evidence_references"]
        assert evidence, invariant_id
        assert all(item["reference"].strip() for item in evidence), invariant_id
        assert all(item["claim"].strip() for item in evidence), invariant_id

        for field in ("last_verified_commit", "last_verified_tree"):
            revision = invariant[field]
            assert revision["explanation"].strip(), (invariant_id, field)
            if revision["status"] == "known":
                assert re.fullmatch(r"[0-9a-f]{40}", revision["value"]), (
                    invariant_id,
                    field,
                )
            else:
                assert revision["value"] is None, (invariant_id, field)
        assert (
            invariant["last_verified_commit"]["status"]
            == invariant["last_verified_tree"]["status"]
        ), invariant_id

        residual_risk = invariant["accepted_residual_risk"]
        assert residual_risk["explanation"].strip(), invariant_id
        if invariant["implementation_status"] in {"partial", "missing"}:
            assert residual_risk["status"] in {"unaccepted", "unknown"}, invariant_id
        else:
            assert residual_risk["status"] != "unaccepted", invariant_id

    assert records["INV-ELIG-001"]["failure_mode"]["mode"] == "fail_closed"
    assert records["INV-ART-001"]["failure_mode"]["mode"] == "mixed"
    assert records["INV-VETO-001"]["failure_mode"]["mode"] == "fail_open"
    assert records["INV-PROC-004"]["failure_mode"]["mode"] == "unknown"
    assert (
        records["INV-PROC-004"]["required_production_deployed_path_checks"][
            "status"
        ]
        == "unknown"
    )
    assert {
        invariant_id
        for invariant_id, invariant in records.items()
        if invariant["last_verified_commit"]["status"] == "unknown"
    } == {
        "INV-PROC-002",
        "INV-PROC-004",
        "INV-API-001",
        "INV-REL-001",
        "INV-REL-ART-001",
        "INV-REL-CMD-001",
        "INV-REL-IMPORT-001",
        "INV-REL-JSON-001",
        "INV-REL-SANDBOX-001",
        "INV-REL-TRUST-001",
        "INV-TEST-003",
        "INV-TXN-HCTX-001",
        "INV-TXN-MEME-001",
        "INV-TXN-RECEIPT-001",
        "INV-TXN-REG-001",
        "INV-TXN-REPLY-001",
    }
    assert records["INV-PROC-004"]["accepted_residual_risk"]["status"] == (
        "unaccepted"
    )


def test_process_lock_invariant_requires_continuous_ownership_and_offline_exclusion() -> None:
    registry = strict_json.load(REGISTRY_PATH)
    invariant = next(
        item
        for item in registry["invariants"]
        if item["id"] == "INV-PROC-002"
    )

    assert invariant["last_verified_commit"] == {
        "status": "unknown",
        "value": None,
        "explanation": (
            "The debc079 evidence cut-off retains the lock-namespace and "
            "continuous-ownership repair recorded as DEF-0033, but that "
            "candidate was rejected for the separate literal second-restart "
            "durable-barrier defect recorded as DEF-0035. A final replacement "
            "candidate identity and its external validation are deliberately "
            "supplied after the candidate is frozen."
        ),
    }
    statement = invariant["statement"]
    assert "without following symbolic links" in statement
    assert "ordinary, single-link lock pathname" in statement
    assert "separate descriptors" in statement
    assert "Before every non-read remote operation" in statement
    assert "offline marker reconciler" in statement
    assert {
        "mrsMThatcher2.py",
        "tools/reconcile_remote_write_safety_marker.py",
        "tests/test_pending_receipt_directory_fsync.py",
        "tests/test_remote_write_safety_marker_reconciliation.py",
        "README.md",
    } <= set(invariant["affected_paths"])
    assert {
        "tests/test_pending_receipt_directory_fsync.py::"
        "test_instance_lock_acquisition_binds_path_inode_and_continuous_ownership",
        "tests/test_pending_receipt_directory_fsync.py::"
        "test_unlocked_matching_descriptor_cannot_self_authorise_acknowledgement",
        "tests/test_remote_write_safety_marker_reconciliation.py::"
        "test_reconciliation_refuses_while_daemon_instance_lock_is_held",
        "tests/test_remote_write_safety_marker_reconciliation.py::"
        "test_reconciliation_rejects_hard_linked_operational_files",
        "tests/test_remote_write_safety_marker_reconciliation.py::"
        "test_project_path_replacement_after_acquisition_preserves_marker",
        "tests/test_pending_receipt_directory_fsync.py::"
        "test_instance_lock_fdinfo_proof_failure_blocks_remote_preflight",
    } <= set(invariant["enforcement"]["tests"])


def test_transaction_invariants_cover_restart_persistent_successor_barrier() -> None:
    registry = strict_json.load(REGISTRY_PATH)
    records = {item["id"]: item for item in registry["invariants"]}
    required_tests = {
        "tests/test_pending_receipt_directory_fsync.py::"
        "test_fresh_process_marker_disappearance_latches_and_blocks_preflight",
        "tests/test_pending_receipt_directory_fsync.py::"
        "test_fresh_process_marker_inspection_failure_latches_both_barriers",
        "tests/test_pending_receipt_directory_fsync.py::"
        "test_fresh_process_marker_probe_latches_before_later_disappearance",
        "tests/test_pending_receipt_directory_fsync.py::"
        "test_durability_uncertainty_blocks_low_level_remote_preflight",
        "tests/test_pending_receipt_directory_fsync.py::"
        "test_durability_uncertainty_blocks_x_and_provider_transports",
        "tests/test_pending_receipt_directory_fsync.py::"
        "test_fresh_process_marker_disappearance_blocks_multiple_real_daemon_ticks",
        "tests/test_remote_write_safety_second_restart.py::"
        "test_literal_second_process_blocks_all_remote_lanes_after_marker_loss_"
        "and_hard_exit",
        "tests/test_remote_write_safety_second_restart.py::"
        "test_literal_clean_process_allows_preflight_after_supported_offline_"
        "reconciliation",
    }

    for invariant_id in (
        "INV-TXN-REG-001",
        "INV-TXN-MEME-001",
        "INV-TXN-RECEIPT-001",
    ):
        invariant = records[invariant_id]
        statement = invariant["statement"]
        rationale = invariant["status_rationale"]
        assert "marker namespace entry" in statement
        assert "ambiguous_post_outcome.restart_barrier.json" in statement
        assert "process-local latch alone does not satisfy restart safety" in statement
        assert "final synchronised namespace transition" in statement
        assert "either" in statement
        assert (
            "block every remote-write lane" in statement
            or "block all remote writes" in statement
        )
        assert "same marker inode" in statement
        assert "successor" in rationale
        assert required_tests <= set(invariant["enforcement"]["tests"])
        assert "DEF-0035" in invariant["last_verified_commit"]["explanation"]
        assert (
            "ambiguous_post_outcome.restart_barrier.json"
            in invariant["runtime_consumed_artifacts"]["artifacts"]
        )

    cross_lane_test = (
        "tests/test_followup_fail_safe_hardening.py::"
        "test_existing_ambiguity_marker_blocks_each_lane_before_preparation"
    )
    assert cross_lane_test in records["INV-TXN-REG-001"]["enforcement"]["tests"]
    assert cross_lane_test in records["INV-TXN-MEME-001"]["enforcement"]["tests"]
    assert cross_lane_test in records["INV-TXN-RECEIPT-001"]["enforcement"]["tests"]


def test_v3_shadow_audit_is_historical_not_runtime_consumed() -> None:
    registry, _schema = _load_real_documents()
    audit_path = (
        "semantic_alignment_research/quote_attribution_cleanup_001/"
        "deployment_candidate/v3_shadow_manifest_audit.json"
    )
    manifest_path = (
        "semantic_alignment_research/quote_attribution_cleanup_001/"
        "deployment_candidate/material_veto_v3_shadow_manifest.json"
    )
    declarations = registry["generated_artifact_classifications"]

    assert len(declarations) == 1
    declaration = declarations[0]
    assert declaration["id"] == "ARTIFACT-CLASS-V3-AUDIT-001"
    assert declaration["artifact"] == audit_path
    assert declaration["bound_artifact"] == manifest_path
    assert declaration["classification"] == "historical_build_time"
    assert declaration["runtime_relationship_required"] is False
    assert declaration["current_companion"] is False
    assert (
        declaration["bound_artifact_sha256"]
        != declaration["observed_current_bound_artifact_sha256"]
    )

    runtime_artifacts = {
        artifact
        for invariant in registry["invariants"]
        for artifact in invariant["runtime_consumed_artifacts"]["artifacts"]
    }
    assert manifest_path in runtime_artifacts
    assert audit_path not in runtime_artifacts


def test_historical_artifact_classification_fails_if_declared_runtime(
    tmp_path: Path,
) -> None:
    registry, schema = _minimal_registry(tmp_path)
    artifact = tmp_path / "historical_audit.json"
    current = tmp_path / "current_manifest.json"
    bound_sha = "a" * 64
    current.write_text('{"current":true}\n', encoding="utf-8")
    artifact.write_text(
        json.dumps(
            {
                "manifest_sha256": bound_sha,
                "policy_version": "policy-v3",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    registry["generated_artifact_classifications"] = [
        {
            "id": "ARTIFACT-CLASS-DEMO-001",
            "artifact": "historical_audit.json",
            "artifact_sha256": registry_tool.sha256_file(artifact),
            "classification": "historical_build_time",
            "runtime_relationship_required": False,
            "current_companion": False,
            "owner": "synthetic_contract",
            "bound_artifact": "current_manifest.json",
            "bound_artifact_sha256": bound_sha,
            "observed_current_bound_artifact_sha256": (
                registry_tool.sha256_file(current)
            ),
            "bound_commit": "1" * 40,
            "policy_version": "policy-v3",
            "builder_evidence": [
                {
                    "type": "code",
                    "reference": "code.py",
                    "claim": "Synthetic offline builder evidence.",
                }
            ],
            "runtime_loader_evidence": [
                {
                    "type": "code",
                    "reference": "code.py",
                    "claim": "Synthetic loader inspection proves non-consumption.",
                }
            ],
            "validator_tests": ["tests/test_sample.py::test_contract"],
            "invariant_ids": ["INV-DEMO-001"],
            "reason": "Synthetic historical evidence for validator coverage.",
        }
    ]
    registry["invariants"][0]["runtime_consumed_artifacts"] = {
        "status": "direct",
        "artifacts": ["historical_audit.json"],
        "explanation": "Deliberately incorrect direct declaration.",
    }

    report = registry_tool.validate_registry(
        registry,
        schema,
        repository_root=tmp_path,
    )

    assert not report.ok
    assert any(
        "historical build-time evidence is also declared runtime-consumed"
        in error
        for error in report.errors
    )


def test_expected_complete_suite_skips_are_exact_and_evidence_bound() -> None:
    registry, _schema = _load_real_documents()
    skips = registry["expected_full_suite_skips"]

    assert len(skips) == 9
    assert len({record["node_id"] for record in skips}) == 9
    assert all(
        record["condition"]
        == "inside_outer_release_gate_containment"
        for record in skips
    )
    assert all(record["prevents_release_qualification"] is False for record in skips)
    assert all(record["reason_regex"].startswith("^") for record in skips)
    assert all(record["reason_regex"].endswith("$") for record in skips)
    assert all(record["compensating_evidence"].strip() for record in skips)
    assert {
        record["node_id"]
        for record in skips
        if record["reason_code"] == "outer_containment_blocks_pathname_unix_fixture"
    } == {
        "tests/test_release_gate.py::test_host_filesystem_unix_socket_inventory_is_sorted_and_path_bound",
        "tests/test_release_gate.py::test_containment_masks_host_unix_socket_and_allows_anonymous_ipc",
        "tests/test_release_gate.py::test_containment_blocks_uninventoried_host_unix_socket[False]",
        "tests/test_release_gate.py::test_containment_blocks_uninventoried_host_unix_socket[True]",
    }


def test_skip_declarations_reject_duplicates_unknown_invariants_and_loose_reasons(
    tmp_path: Path,
) -> None:
    registry, schema = _minimal_registry(tmp_path)
    record = {
        "node_id": "tests/test_sample.py::test_contract",
        "reason_code": "nested_containment_unavailable",
        "reason_regex": "not anchored",
        "condition": "inside_outer_release_gate_containment",
        "invariant_ids": ["INV-UNKNOWN-001"],
        "prevents_release_qualification": False,
        "justification": "short",
        "compensating_evidence": "Synthetic outer-preflight evidence.",
    }
    registry["expected_full_suite_skips"] = [record, copy.deepcopy(record)]

    report = registry_tool.validate_registry(
        registry,
        schema,
        repository_root=tmp_path,
    )

    assert not report.ok
    combined = "\n".join(report.errors)
    assert "duplicate test node" in combined
    assert "unknown invariant reference INV-UNKNOWN-001" in combined
    assert "skip-reason pattern must be fully anchored" in combined
    assert "requires a narrow justification" in combined


def test_registry_candidate_attestation_wording_is_external_and_generic() -> None:
    registry, _schema = _load_real_documents()
    release = next(
        item for item in registry["invariants"] if item["id"] == "INV-REL-001"
    )
    encoded = json.dumps(release, sort_keys=True)

    assert "external frozen-candidate gate run" in encoded
    assert "current Priority-0 candidate has no valid release attestation" not in encoded
    assert "4ae2044" not in encoded


def test_real_registry_is_valid_and_markdown_is_synchronised() -> None:
    registry, schema = _load_real_documents()

    report = registry_tool.validate_registry(
        registry,
        schema,
        repository_root=ROOT,
        markdown_path=MARKDOWN_PATH,
    )

    assert report.ok, "\n".join(report.errors)
    assert report.unsupported == ("INV-PROC-004",)
    assert report.unverified == ("INV-PROC-004",)
    assert set(report.partial) == {
        "INV-ART-001",
        "INV-CONFIG-001",
            "INV-PAUSE-001",
            "INV-REL-001",
            "INV-REL-ART-001",
            "INV-REL-CMD-001",
            "INV-REL-IMPORT-001",
            "INV-REL-JSON-001",
            "INV-REL-SANDBOX-001",
            "INV-REL-TRUST-001",
            "INV-TEST-004",
            "INV-PROC-002",
            "INV-TXN-HCTX-001",
            "INV-TXN-MEME-001",
            "INV-TXN-RECEIPT-001",
            "INV-TXN-REG-001",
            "INV-TXN-REPLY-001",
        }


def test_builtin_schema_fallback_accepts_valid_registry(tmp_path: Path) -> None:
    registry, schema = _minimal_registry(tmp_path)

    report = registry_tool.validate_registry(
        registry,
        schema,
        repository_root=tmp_path,
        force_fallback_schema=True,
    )

    assert report.schema_backend == "built-in-draft7-subset"
    assert report.ok, "\n".join(report.errors)


def test_schema_rejects_unknown_fields_with_jsonschema_and_fallback(
    tmp_path: Path,
) -> None:
    registry, schema = _minimal_registry(tmp_path)
    registry["invariants"][0]["unsupported_field"] = True

    for force_fallback in (False, True):
        report = registry_tool.validate_registry(
            registry,
            schema,
            repository_root=tmp_path,
            force_fallback_schema=force_fallback,
        )
        assert not report.ok
        assert any(
            "additional" in error.lower() and "unsupported_field" in error
            for error in report.errors
        )


def test_validator_rejects_duplicate_ids_missing_files_tests_and_validations(
    tmp_path: Path,
) -> None:
    registry, schema = _minimal_registry(tmp_path)
    duplicate = copy.deepcopy(registry["invariants"][0])
    duplicate["affected_paths"] = ["missing.py"]
    duplicate["enforcement"]["files"] = ["missing.py"]
    duplicate["enforcement"]["tests"] = [
        "tests/test_sample.py::test_missing"
    ]
    duplicate["enforcement"]["validations"] = []
    registry["invariants"].append(duplicate)

    report = registry_tool.validate_registry(
        registry,
        schema,
        repository_root=tmp_path,
    )

    assert not report.ok
    combined = "\n".join(report.errors)
    assert "duplicate invariant ID INV-DEMO-001" in combined
    assert "referenced file does not exist: missing.py" in combined
    assert "test node does not exist" in combined
    assert "critical invariant must name a validation" in combined


def test_validator_rejects_unmapped_control_paths_and_globs(
    tmp_path: Path,
) -> None:
    registry, schema = _minimal_registry(tmp_path)
    (tmp_path / "unmapped.txt").write_text("unmapped\n", encoding="utf-8")
    registry["priority0_control_paths"].append("unmapped.txt")
    registry["invariants"][0]["affected_paths"].append("*.py")

    report = registry_tool.validate_registry(
        registry,
        schema,
        repository_root=tmp_path,
    )

    assert not report.ok
    combined = "\n".join(report.errors)
    assert "not mapped by any invariant: unmapped.txt" in combined
    assert "explicit rather than a glob" in combined


def test_validator_reports_unsupported_and_unverified_without_hiding_them(
    tmp_path: Path,
) -> None:
    registry, schema = _minimal_registry(tmp_path)
    invariant = registry["invariants"][0]
    invariant["implementation_status"] = "missing"
    invariant["status_rationale"] = "The synthetic implementation was removed."
    invariant["verification"] = {
        "status": "unverified",
        "rationale": "No test can establish the absent implementation."
    }
    invariant["enforcement"]["tests"] = []
    invariant["known_gaps"] = ["The synthetic implementation is absent."]
    invariant["accepted_residual_risk"] = {
        "status": "unaccepted",
        "explanation": "No acceptance is recorded for the absent implementation.",
    }

    report = registry_tool.validate_registry(
        registry,
        schema,
        repository_root=tmp_path,
    )

    assert report.ok, "\n".join(report.errors)
    assert report.unsupported == ("INV-DEMO-001",)
    assert report.unverified == ("INV-DEMO-001",)
    assert any("unsupported invariants: INV-DEMO-001" == item for item in report.warnings)
    assert any("unverified invariants: INV-DEMO-001" == item for item in report.warnings)


def test_markdown_drift_is_a_validation_error(tmp_path: Path) -> None:
    registry, schema = _minimal_registry(tmp_path)
    markdown = tmp_path / "PRODUCTION_INVARIANTS.md"
    markdown.write_text("stale\n", encoding="utf-8")

    report = registry_tool.validate_registry(
        registry,
        schema,
        repository_root=tmp_path,
        markdown_path=markdown,
    )

    assert not report.ok
    assert any("generated Markdown is stale" in error for error in report.errors)


def test_renderer_is_deterministic_and_includes_status_gaps(
    tmp_path: Path,
) -> None:
    registry, _schema = _minimal_registry(tmp_path)
    invariant = registry["invariants"][0]
    invariant["implementation_status"] = "partial"
    invariant["status_rationale"] = "One synthetic boundary remains."
    invariant["verification"] = {
        "status": "partial",
        "rationale": "Only one synthetic boundary is exercised."
    }
    invariant["known_gaps"] = ["A synthetic boundary remains unimplemented."]
    invariant["accepted_residual_risk"] = {
        "status": "unaccepted",
        "explanation": "No acceptance is recorded for the synthetic gap.",
    }

    first = registry_tool.render_markdown(registry)
    second = registry_tool.render_markdown(copy.deepcopy(registry))

    assert first == second
    assert "INV-DEMO-001" in first
    assert "A synthetic boundary remains unimplemented." in first
    assert "**Owner subsystem.** `synthetic_contract`" in first
    assert "**Runtime-consumed artifacts.** `none`" in first
    assert "**Last verified commit.** `unknown`" in first
    assert "**Accepted residual risk.** `unaccepted`" in first
    assert "| 1 | 0 | 1 | 0 | 0 | 1 | 0 |" in first
