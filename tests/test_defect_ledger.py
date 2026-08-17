"""Focused tests for the canonical defect-ledger validator."""

from __future__ import annotations

import copy
import hashlib
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
        diagnosis_path=None,
        force_fallback_schema=fallback,
        release_base=release_base,
    )


def _fixture_git(repository_root: Path, *arguments: str) -> str:
    """Run one bounded Git command for a synthetic history fixture."""

    result = ledger_tool._git(repository_root, *arguments)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _commit_parameterized_test_fixture(tmp_path: Path) -> tuple[str, Path]:
    """Commit an old parameter case and return its commit and live test path."""

    tests = tmp_path / "tests"
    tests.mkdir()
    test_file = tests / "test_sample.py"
    test_file.write_text(
        "import pytest\n\n"
        "@pytest.mark.parametrize('value', [1], ids=['old'])\n"
        "def test_contract(value):\n"
        "    assert value\n",
        encoding="utf-8",
    )
    _fixture_git(tmp_path, "init", "--quiet")
    _fixture_git(tmp_path, "add", "--", "tests/test_sample.py")
    _fixture_git(
        tmp_path,
        "-c",
        "user.name=Defect ledger fixture",
        "-c",
        "user.email=defect-ledger-fixture@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "Create historical defect-ledger test fixture",
    )
    return _fixture_git(tmp_path, "rev-parse", "HEAD"), test_file


def _replace_fixture_with_new_parameter_case(test_file: Path) -> None:
    """Replace the live fixture with a differently identified current case."""

    test_file.write_text(
        "import pytest\n\n"
        "@pytest.mark.parametrize('value', [1], ids=['new'])\n"
        "def test_contract(value):\n"
        "    assert value\n",
        encoding="utf-8",
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


def test_review_revisions_are_evidence_identities_not_repository_commits() -> None:
    external_review = "f" * 40
    local_fix = "e" * 40
    ledger = {
        "defects": [
            {
                "first_review_scope": {
                    "reviewed_revision": external_review,
                    "source": "Independent review package",
                },
                "detection": {
                    "revision": external_review,
                    "source": "Immutable reproduction record",
                },
                "fix": {"commit": local_fix},
            }
        ]
    }

    commits = ledger_tool._repository_commit_values(ledger)

    assert commits == {local_fix}
    assert external_review not in commits


@pytest.mark.parametrize(
    "record_name",
    ["first_review_scope", "detection"],
)
def test_external_review_revision_retains_required_source_metadata(
    record_name: str,
) -> None:
    ledger, schema, invariants = _documents()
    defect = next(item for item in ledger["defects"] if item["id"] == "DEF-0018")
    defect[record_name]["source"] = ""

    report = _validate(ledger, schema, invariants)

    assert not report.ok
    assert any(
        record_name in error and "shorter than 1" in error
        for error in report.errors
    )


def test_external_evidence_accepts_exact_ignored_observation_not_shadow(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _fixture_git(repository, "init", "--quiet")
    (repository / ".gitignore").write_text(
        "production_deployments/\n",
        encoding="utf-8",
    )
    relative = "production_deployments/run/evidence.json"
    observed = repository / relative
    observed.parent.mkdir(parents=True)
    observed.write_bytes(b"exact ignored deployment evidence\n")
    record = {
        "id": "EXT-TEST",
        "repository_relative_path": relative,
        "observed_absolute_path": str(observed),
        "sha256": hashlib.sha256(observed.read_bytes()).hexdigest(),
    }

    _records, errors, warnings = ledger_tool._external_evidence_checks(
        {"external_evidence": [record], "defects": []},
        repository_root=repository,
    )

    assert errors == []
    assert warnings == []

    _fixture_git(repository, "add", "-f", "--", relative)
    _records, errors, _warnings = ledger_tool._external_evidence_checks(
        {"external_evidence": [record], "defects": []},
        repository_root=repository,
    )

    assert any("is tracked by Git" in error for error in errors)

    nonignored_relative = "local/evidence.json"
    nonignored = repository / nonignored_relative
    nonignored.parent.mkdir()
    nonignored.write_bytes(b"exact non-ignored evidence\n")
    nonignored_record = {
        "id": "EXT-NONIGNORED",
        "repository_relative_path": nonignored_relative,
        "observed_absolute_path": str(nonignored),
        "sha256": hashlib.sha256(nonignored.read_bytes()).hexdigest(),
    }
    _records, errors, _warnings = ledger_tool._external_evidence_checks(
        {"external_evidence": [nonignored_record], "defects": []},
        repository_root=repository,
    )

    assert any(
        "in-worktree external evidence is not ignored" in error
        for error in errors
    )

    outside = tmp_path / "external-evidence.json"
    outside.write_bytes(observed.read_bytes())
    shadowed = copy.deepcopy(record)
    shadowed["observed_absolute_path"] = str(outside)
    _records, errors, _warnings = ledger_tool._external_evidence_checks(
        {"external_evidence": [shadowed], "defects": []},
        repository_root=repository,
    )

    assert any("resolves to a different file" in error for error in errors)


def test_release_assurance_findings_and_json_defect_boundaries_are_explicit() -> None:
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
    assert runtime_gap["invariant_ids"] == ["INV-CONFIG-001", "INV-PAUSE-001"]
    assert "DEF-0043" in runtime_gap["relations"]["related"]
    assert "DEF-0044" in records["DEF-0019"]["relations"]["related"]


def test_ledger_separates_baseline_cutoff_candidate_and_deployment_identity() -> None:
    ledger, schema, invariants = _documents()
    identity = ledger["identity_scope"]
    baseline = identity["production_baseline"]
    cutoff = identity["ledger_evidence_cutoff"]
    candidate = identity["candidate_under_review"]
    deployment = identity["production_deployment_observation"]
    regeneration = identity["post_merge_regeneration"]

    assert cutoff["commit"] == "4e548b0a5723a1f0c75e9646953b3f92c7db89ad"
    assert cutoff["tree"] == "a751f4b488ee20d268a082c7daf835d5786e7e23"
    assert baseline["commit"] != cutoff["commit"]
    assert baseline["tree"] != cutoff["tree"]
    assert "does not claim" in cutoff["difference_from_production_baseline"]
    assert "external proposals" in cutoff["meaning"]
    assert candidate["identity_source"] == "external-release-attestation"
    assert candidate["stored_in_ledger"] is False
    assert "remain external and unfixed" in candidate["explanation"]
    assert deployment["repository_commit"] == baseline["commit"]
    assert deployment["loaded_process_identity_status"].endswith("-unattested")
    assert regeneration["required"] is True
    assert regeneration["release_base_must_equal_evidence_cutoff"] is True
    assert "post-4e548b0a" in regeneration["requirement"]

    report = _validate(
        ledger,
        schema,
        invariants,
        release_base=cutoff["commit"],
    )
    assert report.ok, "\n".join(report.errors)


def test_release_line_status_fix_and_chronology_shas_are_cutoff_ancestors() -> None:
    ledger, _schema, _invariants = _documents()
    cutoff = ledger["identity_scope"]["ledger_evidence_cutoff"]["commit"]
    claims: list[tuple[str, str]] = []

    for record in ledger["defects"]:
        defect_id = record["id"]
        introduced = record["introduced"]
        for field in ("first_bad_commit", "last_known_good_commit"):
            value = introduced[field]
            if value != "unknown":
                claims.append((f"{defect_id}.introduced.{field}", value))
        range_match = ledger_tool.INCLUSIVE_RANGE_PATTERN.fullmatch(
            introduced["affected_range"]
        )
        if range_match is not None:
            for index, value in enumerate(range_match.groups()):
                if value != "unknown":
                    claims.append(
                        (f"{defect_id}.introduced.affected_range[{index}]", value)
                    )
        for field, value in (
            ("fix.commit", record["fix"]["commit"]),
            ("deployment.observed_commit", record["deployment"]["observed_commit"]),
        ):
            if value != "unknown":
                claims.append((f"{defect_id}.{field}", value))
        for index, event in enumerate(record["chronology"]):
            if event["commit"] != "unknown":
                claims.append(
                    (f"{defect_id}.chronology[{index}].commit", event["commit"])
                )
        for index, test in enumerate(record["tests"]):
            if test["commit"] != "unknown":
                claims.append((f"{defect_id}.tests[{index}].commit", test["commit"]))

    assert claims
    for field, commit in claims:
        ancestry = ledger_tool._git(
            ROOT,
            "merge-base",
            "--is-ancestor",
            commit,
            cutoff,
        )
        assert ancestry.returncode == 0, f"{field} is outside cut-off: {commit}"


@pytest.mark.parametrize(
    ("field_name", "expected_error"),
    [
        (
            "production_observation",
            "production deployment observation date exceeds top-level as_of",
        ),
        ("chronology", "chronology[4] date exceeds top-level as_of"),
        (
            "first_review_scope",
            "first_review_scope.date date exceeds top-level as_of",
        ),
        ("detection", "detection.date date exceeds top-level as_of"),
        (
            "deployment",
            "deployment.observed_at date exceeds top-level as_of",
        ),
    ],
)
def test_evidence_dates_cannot_exceed_ledger_as_of(
    field_name: str,
    expected_error: str,
) -> None:
    ledger, schema, invariants = _documents()
    defect = next(item for item in ledger["defects"] if item["id"] == "DEF-0032")
    if field_name == "production_observation":
        ledger["identity_scope"]["production_deployment_observation"][
            "observed_at"
        ] = "2099-01-01T00:00:00+00:00"
    elif field_name == "chronology":
        defect["chronology"][-1]["date"] = "2099-01-01"
    elif field_name == "deployment":
        defect["deployment"]["observed_at"] = "2099-01-01T00:00:00+00:00"
    else:
        defect[field_name]["date"] = "2099-01-01"

    report = _validate(ledger, schema, invariants)

    assert not report.ok
    assert any(expected_error in error for error in report.errors)


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


def test_commit_bound_test_rejects_unattested_historical_parameter_case(
    tmp_path: Path,
) -> None:
    commit, test_file = _commit_parameterized_test_fixture(tmp_path)
    _replace_fixture_with_new_parameter_case(test_file)
    selector = "tests/test_sample.py::test_contract[new]"

    exists, reason = ledger_tool.test_node_exists(
        tmp_path,
        "tests/test_sample.py",
        "test_contract[new]",
        commit,
    )

    assert not exists
    assert reason == (
        "historical parameter-specific pytest selector requires exact "
        "historical collection attestation (none available): "
        f"{selector}"
    )


def test_commit_bound_test_accepts_unparameterized_historical_function(
    tmp_path: Path,
) -> None:
    commit, test_file = _commit_parameterized_test_fixture(tmp_path)
    _replace_fixture_with_new_parameter_case(test_file)

    exists, reason = ledger_tool.test_node_exists(
        tmp_path,
        "tests/test_sample.py",
        "test_contract",
        commit,
    )

    assert exists
    assert reason == ""


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


def test_cutoff_defects_use_structured_production_presence_without_inference() -> None:
    ledger, _schema, _invariants = _documents()
    records = {item["id"]: item for item in ledger["defects"]}

    assert records["DEF-0056"]["deployment"][
        "production_presence_at_observed_commit"
    ] == "present"
    assert records["DEF-0057"]["deployment"][
        "production_presence_at_observed_commit"
    ] == "present"
    for defect_id in ("DEF-0058", "DEF-0059"):
        record = records[defect_id]
        assert record["deployment"][
            "production_presence_at_observed_commit"
        ] == "not-established"
        assert ledger_tool._deployment_cell(record) == (
            "active at the ledger evidence cut-off; production presence "
            "not established"
        )

    mutated = copy.deepcopy(records["DEF-0058"])
    mutated["deployment"]["evidence"] = (
        "Observed production words deliberately appear here but are not "
        "structured proof."
    )
    assert ledger_tool._deployment_cell(mutated) == (
        "active at the ledger evidence cut-off; production presence "
        "not established"
    )


def test_repaired_remote_write_defects_render_honestly() -> None:
    ledger, _schema, _invariants = _documents()
    pending_receipt_repair = next(
        item for item in ledger["defects"] if item["id"] == "DEF-0030"
    )
    daemon_loop_repair = next(
        item for item in ledger["defects"] if item["id"] == "DEF-0031"
    )
    marker_identity_repair = next(
        item for item in ledger["defects"] if item["id"] == "DEF-0032"
    )
    process_lock_repair = next(
        item for item in ledger["defects"] if item["id"] == "DEF-0033"
    )
    fresh_process_defect = next(
        item for item in ledger["defects"] if item["id"] == "DEF-0034"
    )
    second_restart_defect = next(
        item for item in ledger["defects"] if item["id"] == "DEF-0035"
    )
    receipt_authority_defect = next(
        item for item in ledger["defects"] if item["id"] == "DEF-0036"
    )
    activation_pair_defect = next(
        item for item in ledger["defects"] if item["id"] == "DEF-0037"
    )

    assert ledger_tool._deployment_cell(pending_receipt_repair) == (
        "not-deployed; observed `be882e81`"
    )
    assert ledger_tool._deployment_cell(daemon_loop_repair) == (
        "not-deployed; observed `be882e81`"
    )
    assert ledger_tool._deployment_cell(marker_identity_repair) == (
        "not-deployed; observed `be882e81`"
    )
    assert ledger_tool._deployment_cell(process_lock_repair) == (
        "not-deployed; observed `be882e81`"
    )
    assert ledger_tool._deployment_cell(fresh_process_defect) == (
        "not-deployed; observed `be882e81`"
    )
    assert ledger_tool._deployment_cell(second_restart_defect) == (
        "not-deployed; observed `be882e81`"
    )
    assert marker_identity_repair["status"] == "repaired-not-deployed"
    assert process_lock_repair["status"] == "repaired-not-deployed"
    assert fresh_process_defect["status"] == "repaired-not-deployed"
    assert second_restart_defect["status"] == "repaired-not-deployed"
    assert receipt_authority_defect["status"] == "repaired-not-deployed"
    assert activation_pair_defect["status"] == "repaired-not-deployed"
    assert marker_identity_repair["fix"]["commit"] == (
        "2ad0f79feb0d54be1b1687546449deac6bd1a0c1"
    )
    assert process_lock_repair["fix"]["commit"] == (
        "2ad0f79feb0d54be1b1687546449deac6bd1a0c1"
    )
    introduction = marker_identity_repair["introduced"]
    assert (
        introduction["first_bad_commit"]
        == "7f76c11325c79682d45382009295bcb5628ecdd8"
    )
    assert (
        introduction["last_known_good_commit"]
        == "db84eebf17247236f0dee85007ad928109875143"
    )
    assert introduction["affected_range"] == (
        "inclusive:7f76c11325c79682d45382009295bcb5628ecdd8"
        "..ee7539c2b5bcf41faa07bbbc9ecc53d91eb2ec22"
    )
    chronology_commits = {
        event["commit"] for event in marker_identity_repair["chronology"]
    }
    assert {
        "7f76c11325c79682d45382009295bcb5628ecdd8",
        "acfc4f69ef9503c6bf842d0ab2897c919a455dbe",
        "dd8aa52c93982de561b460832baa77b90ddb95a7",
        "ee7539c2b5bcf41faa07bbbc9ecc53d91eb2ec22",
        "2ad0f79feb0d54be1b1687546449deac6bd1a0c1",
    } <= chronology_commits
    lock_introduction = process_lock_repair["introduced"]
    assert (
        lock_introduction["first_bad_commit"]
        == "f0be0b5de09ca6b75f4701bb34e690675e17106e"
    )
    assert (
        lock_introduction["last_known_good_commit"]
        == "4f268ed666b5cad7e7a76e1a0b4cd6963645a534"
    )
    assert lock_introduction["affected_range"] == (
        "inclusive:f0be0b5de09ca6b75f4701bb34e690675e17106e"
        "..ee7539c2b5bcf41faa07bbbc9ecc53d91eb2ec22"
    )
    assert process_lock_repair["invariant_ids"] == ["INV-PROC-002"]
    assert process_lock_repair["incident"]["occurred"] is False
    assert "DEF-0033" in marker_identity_repair["relations"]["related"]
    assert "DEF-0032" in process_lock_repair["relations"]["related"]
    assert "DEF-0034" in marker_identity_repair["relations"]["related"]
    assert "DEF-0034" in process_lock_repair["relations"]["related"]
    assert fresh_process_defect["introduced"]["affected_range"] == (
        "inclusive:2ad0f79feb0d54be1b1687546449deac6bd1a0c1"
        "..2ad0f79feb0d54be1b1687546449deac6bd1a0c1"
    )
    assert fresh_process_defect["fix"]["state"] == "fixed"
    assert fresh_process_defect["fix"]["commit"] == (
        "debc079949b567362ce7c451ea43fd52ffedfa4d"
    )
    assert "process-local" in fresh_process_defect["fix"]["summary"]
    assert "DEF-0035" in fresh_process_defect["fix"]["summary"]
    assert set(fresh_process_defect["invariant_ids"]) == {
        "INV-TXN-MEME-001",
        "INV-TXN-RECEIPT-001",
        "INV-TXN-REG-001",
    }
    assert fresh_process_defect["incident"]["occurred"] is False
    assert second_restart_defect["introduced"]["affected_range"] == (
        "inclusive:debc079949b567362ce7c451ea43fd52ffedfa4d"
        "..debc079949b567362ce7c451ea43fd52ffedfa4d"
    )
    assert second_restart_defect["fix"]["state"] == "fixed"
    assert second_restart_defect["fix"]["commit"] == (
        "78b5c5b316c20c35bc863ff0c77062d9ad628eb8"
    )
    assert "ambiguous_post_outcome.restart_barrier.json" in (
        second_restart_defect["fix"]["summary"]
    )
    assert set(second_restart_defect["invariant_ids"]) == {
        "INV-TXN-MEME-001",
        "INV-TXN-RECEIPT-001",
        "INV-TXN-REG-001",
    }
    assert second_restart_defect["incident"]["occurred"] is False
    assert receipt_authority_defect["introduced"]["affected_range"] == (
        "inclusive:unknown..a65c91bf5b72b986f48b020d9d3096f8b57895c9"
    )
    assert activation_pair_defect["introduced"]["affected_range"] == (
        "inclusive:unknown..a65c91bf5b72b986f48b020d9d3096f8b57895c9"
    )
    assert receipt_authority_defect["fix"]["commit"] == (
        "5b0b61080ec2deac4ca3f49cf8aec6e51fc27575"
    )
    assert activation_pair_defect["fix"]["commit"] == (
        "5b0b61080ec2deac4ca3f49cf8aec6e51fc27575"
    )


def test_cutoff_pause_repair_is_recorded_not_deployed() -> None:
    ledger, _schema, _invariants = _documents()
    pause_finding = next(
        item for item in ledger["defects"] if item["id"] == "DEF-0051"
    )

    assert ledger_tool._deployment_cell(pause_finding) == (
        "not-deployed; observed `be882e81`"
    )


def test_transport_cutoff_repairs_and_post_cutoff_findings_remain_distinct() -> None:
    ledger, _schema, _invariants = _documents()
    records = {item["id"]: item for item in ledger["defects"]}
    cutoff = ledger["identity_scope"]["ledger_evidence_cutoff"]["commit"]
    transport_fix = "7ebcc09699a13848d55a33fd84d66cc8ce56d95c"
    transport_parent = "40ab83ef77e75549a08ee0dc2082985ca36b361b"

    for defect_id in ("DEF-0038", "DEF-0039"):
        record = records[defect_id]
        assert record["status"] == "repaired-not-deployed"
        assert record["fix"] == {
            "state": "fixed",
            "commit": transport_fix,
            "summary": record["fix"]["summary"],
        }
        assert record["introduced"]["affected_range"] == (
            f"inclusive:unknown..{transport_parent}"
        )
        assert any(item["commit"] == transport_fix for item in record["tests"])
        assert any(item["commit"] == transport_fix for item in record["chronology"])

    repaired = {
        "DEF-0040": (
            "634fd6cd92b52a9ae8786f4b8c672dba42c58dcb",
            "7ebcc09699a13848d55a33fd84d66cc8ce56d95c",
        ),
        "DEF-0041": (
            "634fd6cd92b52a9ae8786f4b8c672dba42c58dcb",
            "7ebcc09699a13848d55a33fd84d66cc8ce56d95c",
        ),
        "DEF-0042": (
            "634fd6cd92b52a9ae8786f4b8c672dba42c58dcb",
            "7ebcc09699a13848d55a33fd84d66cc8ce56d95c",
        ),
        "DEF-0043": (
            "634fd6cd92b52a9ae8786f4b8c672dba42c58dcb",
            "7ebcc09699a13848d55a33fd84d66cc8ce56d95c",
        ),
        "DEF-0046": (
            "5a11bbf4ef4b3e788f176d64fb8455763a09c937",
            "634fd6cd92b52a9ae8786f4b8c672dba42c58dcb",
        ),
        "DEF-0047": (
            "5a11bbf4ef4b3e788f176d64fb8455763a09c937",
            "634fd6cd92b52a9ae8786f4b8c672dba42c58dcb",
        ),
        "DEF-0048": (
            "5a11bbf4ef4b3e788f176d64fb8455763a09c937",
            "634fd6cd92b52a9ae8786f4b8c672dba42c58dcb",
        ),
        "DEF-0049": (
            "ce970f81f4d83e751936c9dae9261c72a2c5c0c6",
            "5a11bbf4ef4b3e788f176d64fb8455763a09c937",
        ),
        "DEF-0050": (
            "ce970f81f4d83e751936c9dae9261c72a2c5c0c6",
            "5a11bbf4ef4b3e788f176d64fb8455763a09c937",
        ),
        "DEF-0051": (
            "fb8eb25fefb4e4a8b281a152521c1b1f4e08eb04",
            "ce970f81f4d83e751936c9dae9261c72a2c5c0c6",
        ),
    }
    for defect_id, (fix_commit, affected_parent) in repaired.items():
        record = records[defect_id]
        assert record["status"] == "repaired-not-deployed"
        assert record["fix"]["state"] == "fixed"
        assert record["fix"]["commit"] == fix_commit
        assert record["deployment"]["state"] == "not-deployed"
        assert record["introduced"]["affected_range"] == (
            f"inclusive:unknown..{affected_parent}"
        )
        assert any(item["commit"] == fix_commit for item in record["tests"])
        assert any(item["commit"] == fix_commit for item in record["chronology"])

    assurance = records["DEF-0044"]
    assert assurance["status"] == "assurance-weakness"
    assert assurance["severity"] == "assurance"
    assert assurance["fix"]["state"] == "unfixed"
    assert assurance["tests"] == []
    assert "DEF-0019" in assurance["relations"]["related"]
    assert "without an independent observer" in assurance["summary"]

    liveness = records["DEF-0045"]
    assert liveness["status"] == "active"
    assert liveness["severity"] == "medium"
    assert liveness["fix"]["state"] == "unfixed"
    assert liveness["introduced"]["affected_range"] == (
        f"inclusive:unknown..{cutoff}"
    )
    assert liveness["incident"]["occurred"] is False
    assert "fail-closed" in liveness["impact"]
    assert "does not claim" in liveness["impact"]

    for defect_id in ("DEF-0052", "DEF-0053", "DEF-0054", "DEF-0055"):
        record = records[defect_id]
        assert record["status"] == "active"
        assert record["fix"]["state"] == "unfixed"
        assert record["fix"]["commit"] == "unknown"
        assert record["tests"] == []
        assert record["introduced"]["affected_range"] == (
            f"inclusive:unknown..{cutoff}"
        )
        assert any(item["commit"] == cutoff for item in record["chronology"])

    phase = records["DEF-0053"]
    assert "pre-remote versus remote-started" in phase["title"]
    assert "remote_transaction_started phase" in phase["components"]
    assert any(
        item["reference"].endswith(
            "test_interrupted_remote_started_context_claim_without_history_stays_blocked"
        )
        for item in phase["evidence"]
    )
    assert any(
        item["reference"].endswith(
            "test_remote_started_claim_cannot_consume_stale_failed_history"
        )
        for item in phase["evidence"]
    )
    assert any(
        item["reference"].endswith(
            "test_pre_remote_claim_cannot_consume_stale_failure_over_transport_journal"
        )
        for item in phase["evidence"]
    )

    stable_source = records["DEF-0054"]
    assert "stable canonical private file generation" in stable_source["title"]
    assert "same-inode content stability" in stable_source["components"]
    assert "final-path presence and identity" in stable_source["components"]
    assert any(
        item["reference"].endswith(
            "test_common_receipt_loader_rejects_same_inode_mutation_after_read"
        )
        for item in stable_source["evidence"]
    )

    exact_identity = records["DEF-0055"]
    assert "Durable public identifiers" in exact_identity["title"]
    assert "source and image basenames" in exact_identity["components"]
    assert "'.'/'..'" in exact_identity["detection"]["method"]
    assert any(
        item["reference"].endswith(
            "test_transport_handoff_owner_requires_exact_source_identity"
        )
        for item in exact_identity["evidence"]
    )


def test_explicit_unfixed_ranges_end_at_the_evidence_cutoff() -> None:
    ledger, _schema, _invariants = _documents()
    cutoff = ledger["identity_scope"]["ledger_evidence_cutoff"]["commit"]

    for record in ledger["defects"]:
        affected_range = record["introduced"]["affected_range"]
        if record["fix"]["state"] != "unfixed" or affected_range == "unknown":
            continue
        assert affected_range.endswith(f"..{cutoff}"), record["id"]
