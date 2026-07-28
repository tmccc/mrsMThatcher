from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from tools import priority0_registry as registry_tool


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
        json.loads(REGISTRY_PATH.read_text(encoding="utf-8")),
        json.loads(SCHEMA_PATH.read_text(encoding="utf-8")),
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
            "commands": [
                {
                    "command": "python3 -m pytest -q tests/test_sample.py",
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
    registry["invariants"] = [invariant]
    return registry, schema


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
    } == {"INV-PROC-004", "INV-REL-001", "INV-TEST-003"}
    assert records["INV-PROC-004"]["accepted_residual_risk"]["status"] == (
        "unaccepted"
    )


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
        "INV-TEST-004",
        "INV-TXN-RECEIPT-001",
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


def test_validator_rejects_duplicate_ids_missing_files_tests_and_commands(
    tmp_path: Path,
) -> None:
    registry, schema = _minimal_registry(tmp_path)
    duplicate = copy.deepcopy(registry["invariants"][0])
    duplicate["affected_paths"] = ["missing.py"]
    duplicate["enforcement"]["files"] = ["missing.py"]
    duplicate["enforcement"]["tests"] = [
        "tests/test_sample.py::test_missing"
    ]
    duplicate["enforcement"]["commands"] = []
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
    assert "critical invariant must name a command" in combined


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
