"""Focused offline tests for the frozen-candidate release gate."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import multiprocessing
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools import release_gate


def _run(args: list[str], cwd: Path) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q"], repo)
    _run(["git", "config", "user.name", "Release Gate Test"], repo)
    _run(["git", "config", "user.email", "release-gate@example.invalid"], repo)
    (repo / "mrsMThatcher2.py").write_text("import helper\n", encoding="utf-8")
    (repo / "helper.py").write_text(
        'MANIFEST = "runtime_manifest.json"\n', encoding="utf-8"
    )
    (repo / "runtime_manifest.json").write_text(
        '{"manifest":true}\n', encoding="utf-8"
    )
    (repo / "pytest.ini").write_text("[pytest]\ntestpaths=tests\n", encoding="utf-8")
    _run(["git", "add", "."], repo)
    _run(["git", "commit", "-qm", "fixture"], repo)
    return repo


def _snapshot(**overrides: object) -> release_gate.CandidateSnapshot:
    values: dict[str, object] = {
        "commit": "1" * 40,
        "tree": "2" * 40,
        "status_porcelain_v2": "",
        "untracked_files": (),
        "submodule_status": (),
        "runtime_hashes": (("mrsMThatcher2.py", "3" * 64),),
        "generated_hashes": (("runtime_manifest.json", "4" * 64),),
        "declared_artifact_hashes": (),
        "policy_hashes": (),
    }
    values.update(overrides)
    return release_gate.CandidateSnapshot(**values)


def test_documented_script_invocation_loads_sibling_schema_validator() -> None:
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    program = (
        "from pathlib import Path\n"
        "import release_gate\n"
        "document = release_gate.load_json_object(Path('../production_invariants.json'))\n"
        "release_gate.validate_control_schema(\n"
        "    document,\n"
        "    Path('../production_invariants.schema.json'),\n"
        "    label='production invariant registry',\n"
        ")\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=root / "tools",
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout


def _invariant(
    invariant_id: str = "INV-REL-001",
    *,
    paths: list[str] | None = None,
    severity: str = "critical",
    commands: list[str] | None = None,
) -> dict[str, object]:
    return {
        "invariant_id": invariant_id,
        "severity": severity,
        "owner_subsystem": "release",
        "affected_paths": paths or ["tools/release_gate.py"],
        "validation_commands": (
            ["python3 -m pytest -q tests/test_release_gate.py"]
            if commands is None
            else commands
        ),
    }


def test_dirty_candidate_is_rejected_outside_development_mode() -> None:
    candidate = _snapshot(
        status_porcelain_v2="1 .M N... 100644 100644 100644 a b helper.py"
    )
    with pytest.raises(release_gate.ReleaseGateError, match="dirty"):
        release_gate.require_frozen(candidate, development=False)
    release_gate.require_frozen(candidate, development=True)


def test_untracked_candidate_is_rejected_outside_development_mode() -> None:
    candidate = _snapshot(
        status_porcelain_v2="? untracked.txt",
        untracked_files=("untracked.txt",),
    )
    with pytest.raises(release_gate.ReleaseGateError, match="untracked"):
        release_gate.require_frozen(candidate, development=False)


def test_candidate_tree_drift_during_validation_is_rejected() -> None:
    before = _snapshot()
    after = dataclasses.replace(before, tree="9" * 40)
    with pytest.raises(release_gate.ReleaseGateError, match="changed"):
        release_gate.assert_snapshot_equal(before, after)


def test_identical_candidate_snapshot_is_accepted() -> None:
    snapshot = _snapshot()
    release_gate.assert_snapshot_equal(snapshot, snapshot)


def test_changed_path_mapping_is_precise() -> None:
    records = [
        _invariant(paths=["mrsMThatcher2.py"]),
        _invariant("INV-TEST-001", paths=["tests/test_*.py"], severity="high"),
    ]
    mapped, uncovered, affected = release_gate.map_changed_paths(
        ["mrsMThatcher2.py", "tests/test_release_gate.py"],
        records,
        runtime_paths={"mrsMThatcher2.py"},
        generated_paths=set(),
    )
    assert uncovered == []
    assert mapped == [
        {
            "path": "mrsMThatcher2.py",
            "category": "runtime",
            "invariant_ids": ["INV-REL-001"],
        },
        {
            "path": "tests/test_release_gate.py",
            "category": "test-only",
            "invariant_ids": ["INV-TEST-001"],
        },
    ]
    assert [item["invariant_id"] for item in affected] == [
        "INV-REL-001",
        "INV-TEST-001",
    ]


def test_unmapped_runtime_path_is_rejected() -> None:
    with pytest.raises(release_gate.ReleaseGateError, match="unmapped"):
        release_gate.map_changed_paths(
            ["mrsMThatcher2.py"],
            [_invariant(paths=["historical_context_*.py"])],
            runtime_paths={"mrsMThatcher2.py"},
            generated_paths=set(),
        )


def test_unmapped_generated_artifact_is_rejected() -> None:
    with pytest.raises(release_gate.ReleaseGateError, match="unmapped"):
        release_gate.map_changed_paths(
            ["runtime_manifest.json"],
            [_invariant(paths=["mrsMThatcher2.py"])],
            runtime_paths=set(),
            generated_paths={"runtime_manifest.json"},
        )


@pytest.mark.parametrize("path", ["tools/new_gate.py", "new_policy.json"])
def test_unmapped_tooling_and_control_paths_are_rejected(path: str) -> None:
    with pytest.raises(release_gate.ReleaseGateError, match="code/control"):
        release_gate.map_changed_paths(
            [path],
            [_invariant(paths=["mrsMThatcher2.py"])],
            runtime_paths=set(),
            generated_paths=set(),
        )


@pytest.mark.parametrize(
    "path",
    [
        "defect_ledger.json",
        "defect_ledger.schema.json",
        "diagnosis_measurements.json",
        "production_invariants.json",
        "production_invariants.schema.json",
    ],
)
def test_priority0_json_documents_are_controls_not_generated(path: str) -> None:
    assert release_gate.classify_changed_path(path, set(), set()) == "control"


def test_unmapped_documentation_is_a_documented_exception() -> None:
    mapped, uncovered, affected = release_gate.map_changed_paths(
        ["notes.md"],
        [_invariant(paths=["mrsMThatcher2.py"])],
        runtime_paths=set(),
        generated_paths=set(),
    )
    assert mapped[0]["category"] == "documentation"
    assert uncovered == []
    assert affected == []


def test_broad_catch_all_invariant_mapping_is_rejected() -> None:
    with pytest.raises(release_gate.ReleaseGateError, match="catch-all"):
        release_gate.invariant_globs(_invariant(paths=["*"]))


def test_affected_critical_invariant_requires_executable_validation() -> None:
    with pytest.raises(release_gate.ReleaseGateError, match="lacks executable"):
        release_gate.map_changed_paths(
            ["mrsMThatcher2.py"],
            [_invariant(paths=["mrsMThatcher2.py"], commands=[])],
            runtime_paths={"mrsMThatcher2.py"},
            generated_paths=set(),
        )


def test_shell_control_in_validation_command_is_rejected() -> None:
    with pytest.raises(release_gate.ReleaseGateError, match="unsafe"):
        release_gate.validation_commands(
            [_invariant(commands=["python3 -m pytest ; curl example.invalid"])]
        )


def test_duplicate_validation_commands_are_suppressed_deterministically() -> None:
    records = [
        _invariant("INV-REL-001", commands=["python3 -m pytest -q tests/test_release_gate.py"]),
        _invariant("INV-TEST-001", commands=["python3 -m pytest -q tests/test_release_gate.py"]),
    ]
    assert release_gate.validation_commands(records) == [
        ("python3", "-m", "pytest", "-q", "tests/test_release_gate.py")
    ]


def test_registry_enforcement_command_and_environment_prefix_are_shell_free() -> None:
    record = {
        "id": "INV-TEST-002",
        "criticality": "critical",
        "affected_paths": ["tests/conftest.py"],
        "enforcement": {
            "commands": [
                {
                    "command": (
                        "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "
                        "python3 -m pytest -q tests/test_release_gate.py"
                    )
                }
            ]
        },
    }
    assert release_gate.validation_commands([record]) == [
        (
            "env",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
            "python3",
            "-m",
            "pytest",
            "-q",
            "tests/test_release_gate.py",
        )
    ]


def test_integration_lock_contention_fails_closed(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    with release_gate.integration_lock(repo):
        with pytest.raises(release_gate.ReleaseGateError, match="already held"):
            with release_gate.integration_lock(repo):
                raise AssertionError("unreachable")


def test_runtime_python_discovery_follows_local_imports(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    assert release_gate.discover_runtime_python_files(repo) == (
        "helper.py",
        "mrsMThatcher2.py",
    )


def test_missing_generated_binding_is_reported(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "helper.py").write_text(
        'MANIFEST = "missing_runtime_manifest.json"\n', encoding="utf-8"
    )
    generated, bindings, unresolved = release_gate.discover_generated_artifacts(
        repo, ("helper.py",)
    )
    assert generated == ()
    assert bindings == []
    assert unresolved == ["helper.py: missing_runtime_manifest.json"]


def test_existing_generated_binding_is_content_hashed(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    runtime = release_gate.discover_runtime_python_files(repo)
    generated, bindings, unresolved = release_gate.discover_generated_artifacts(
        repo, runtime
    )
    assert generated == ("runtime_manifest.json",)
    assert not unresolved
    assert bindings[0]["resolution"] in {
        "static_path_composition",
        "unique_tracked_basename",
    }
    digest = release_gate.hash_paths(repo, generated)
    assert digest == (
        (
            "runtime_manifest.json",
            hashlib.sha256(b'{"manifest":true}\n').hexdigest(),
        ),
    )


def test_registry_declared_existing_runtime_artifacts_are_hashed(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path)
    (repo / "runtime.txt").write_text("runtime\n", encoding="utf-8")
    _run(["git", "add", "runtime.txt"], repo)
    _run(["git", "commit", "-qm", "runtime artifact"], repo)
    records = [
        {
            "invariant_id": "INV-ART-001",
            "runtime_consumed_artifacts": {
                "artifacts": ["runtime.txt", "missing.json"]
            },
        }
    ]
    declared = release_gate.registry_declared_runtime_artifacts(repo, records)
    assert declared == ("runtime.txt",)
    snapshot, _bindings, _unresolved = release_gate.take_snapshot(repo, declared)
    assert dict(snapshot.declared_artifact_hashes)["runtime.txt"] == hashlib.sha256(
        b"runtime\n"
    ).hexdigest()


def test_ambiguous_bindings_are_not_reported_as_fully_resolved(
    tmp_path: Path,
) -> None:
    (tmp_path / "runtime_manifest.json").write_text("{}\n", encoding="utf-8")
    inventory = release_gate.relationship_inventory(
        tmp_path,
        _snapshot(),
        [
            {
                "loader": "loader.py",
                "literal": "manifest.json",
                "candidate_paths": ["a/manifest.json", "b/manifest.json"],
                "resolution": "ambiguous_not_claimed_runtime",
            }
        ],
        [],
    )
    assert inventory["all_claimed_runtime_bindings_resolved"] is True
    assert inventory["all_discovered_bindings_resolved"] is False


def test_unavailable_os_level_network_denial_is_explicit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(release_gate.shutil, "which", lambda _name: None)
    result = release_gate.network_preflight(cwd=tmp_path)
    assert result == {
        "available": False,
        "mechanism": "user-network-and-mount-namespace",
        "reason": "unshare, ip, mount or sh is unavailable",
    }


def test_subprocess_egress_preflight_uses_namespace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(release_gate.shutil, "which", lambda name: f"/usr/bin/{name}")
    observed: list[tuple[str, ...]] = []

    def fake_run(
        args: tuple[str, ...],
        *,
        cwd: Path,
        check: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        del cwd, check, timeout
        observed.append(tuple(args))
        return subprocess.CompletedProcess(args, 0, b"")

    monkeypatch.setattr(release_gate, "_run", fake_run)
    result = release_gate.network_preflight(cwd=tmp_path)
    assert result["available"] is True
    assert result["subprocess_egress_denied"] is True
    assert observed[0][:5] == (
        "unshare",
        "--user",
        "--map-root-user",
        "--mount",
        "--net",
    )
    assert any('exec "$@"' in token for token in observed[0])
    assert "--pid" in observed[0]
    assert "--fork" in observed[0]
    assert "--mount-proc" in observed[0]


def test_namespace_wrapper_preserves_arguments_without_shell_interpolation() -> None:
    wrapped = release_gate.network_namespace_command(
        ("python3", "-m", "pytest", "tests/a file.py")
    )
    assert wrapped[-1] == "tests/a file.py"
    assert wrapped[:5] == (
        "unshare",
        "--user",
        "--map-root-user",
        "--mount",
        "--net",
    )


def test_containment_wrapper_binds_production_root_read_only(tmp_path: Path) -> None:
    wrapped = release_gate.containment_namespace_command(
        ("python3", "-m", "pytest"), production_root=tmp_path
    )
    assert str(tmp_path) in wrapped
    script = wrapped[wrapped.index("-c") + 1]
    assert 'mount --bind "$1" "$1"' in script
    assert "remount,bind,ro" in script
    assert "--pid" in wrapped
    assert "--fork" in wrapped
    assert "--mount-proc" in wrapped


def test_validation_environment_does_not_inherit_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("X_API_KEY", "secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://live.invalid")
    environment = release_gate.sanitized_validation_environment(tmp_path)
    assert "X_API_KEY" not in environment
    assert "HTTPS_PROXY" not in environment
    assert environment["HOME"] == str(tmp_path)
    dependency_paths = [
        Path(value) for value in environment["PYTHONPATH"].split(os.pathsep)
    ]
    assert any((path / "pytest").is_dir() for path in dependency_paths)
    assert any(
        (path / "xdist" / "plugin.py").is_file() for path in dependency_paths
    )


def test_validation_environment_fails_when_xdist_dependency_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(release_gate.site, "getusersitepackages", lambda: str(tmp_path))
    monkeypatch.setattr(release_gate.site, "getsitepackages", lambda: [])
    monkeypatch.setattr(release_gate.sys, "path", [str(tmp_path)])
    with pytest.raises(release_gate.ReleaseGateError, match="pytest/xdist"):
        release_gate.sanitized_validation_environment(tmp_path / "home")


def test_validation_environment_finds_dependencies_from_current_sys_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dependency_path = tmp_path / "dependencies"
    (dependency_path / "pytest").mkdir(parents=True)
    (dependency_path / "xdist").mkdir()
    (dependency_path / "xdist" / "plugin.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        release_gate.site,
        "getusersitepackages",
        lambda: str(tmp_path / "missing-user-site"),
    )
    monkeypatch.setattr(release_gate.site, "getsitepackages", lambda: [])
    monkeypatch.setattr(release_gate.sys, "path", [str(dependency_path)])

    environment = release_gate.sanitized_validation_environment(tmp_path / "home")

    assert environment["PYTHONPATH"] == str(dependency_path.resolve())


def test_deterministic_semantic_attestation_is_byte_identical() -> None:
    result = release_gate.ValidationResult(
        command=("python3", "-m", "pytest"),
        exit_status=0,
        passed=12,
        failed=0,
        errors=0,
        skipped=1,
        warnings=2,
        output_sha256="5" * 64,
        duration_seconds=123.456,
    )
    values = {
        "base": "6" * 40,
        "snapshot": _snapshot(),
        "registry_sha256": "7" * 64,
        "ledger_sha256": "8" * 64,
        "path_mapping": [],
        "affected": [],
        "focused": [result],
        "full": result,
        "network": {
            "available": True,
            "mechanism": "netns",
            "subprocess_egress_denied": True,
            "exit_status": 0,
            "output_sha256": "9" * 64,
            "volatile_reason": "ignored",
        },
        "relationships": {
            "artifact_hashes": {},
            "unresolved_loader_literals": [],
        },
        "deployed_checks": [],
        "scope": "patch-local release candidate",
    }
    first = release_gate.canonical_json_bytes(
        release_gate.deterministic_attestation(**values)
    )
    second = release_gate.canonical_json_bytes(
        release_gate.deterministic_attestation(**values)
    )
    assert first == second
    assert b"123.456" not in first
    assert b"output_sha256" not in first


def test_validation_result_binds_output_and_junit_hashes() -> None:
    result = release_gate.ValidationResult(
        command=("pytest",),
        exit_status=0,
        passed=1,
        failed=0,
        errors=0,
        skipped=0,
        warnings=0,
        output_sha256="a" * 64,
        duration_seconds=1,
        junit_sha256="b" * 64,
    )
    assert "output_sha256" not in result.semantic_dict()
    assert result.receipt_dict()["output_sha256"] == "a" * 64
    assert result.receipt_dict()["junit_sha256"] == "b" * 64


@pytest.mark.parametrize(
    ("development", "full_suite", "exit_status", "expected"),
    [
        (True, False, None, "development-only"),
        (False, False, None, "patch-local release candidate"),
        (False, True, 1, "patch-local release candidate"),
    ],
)
def test_scope_wording_does_not_claim_system_wide(
    development: bool,
    full_suite: bool,
    exit_status: int | None,
    expected: str,
) -> None:
    result = None
    if exit_status is not None:
        result = release_gate.ValidationResult(
            command=("pytest",),
            exit_status=exit_status,
            passed=0,
            failed=int(bool(exit_status)),
            errors=0,
            skipped=0,
            warnings=0,
            output_sha256="0" * 64,
            duration_seconds=1,
        )
    scope = release_gate.conclusion_scope(
        development=development,
        full_suite=full_suite,
        full_result=result,
        affected=[],
    )
    assert scope == expected
    assert scope != "system-wide"


def test_subsystem_scope_requires_one_identified_subsystem() -> None:
    result = release_gate.ValidationResult(
        command=("pytest",),
        exit_status=0,
        passed=1,
        failed=0,
        errors=0,
        skipped=0,
        warnings=0,
        output_sha256="0" * 64,
        duration_seconds=1,
    )
    assert (
        release_gate.conclusion_scope(
            development=False,
            full_suite=True,
            full_result=result,
            affected=[{"owner_subsystem": "release"}],
        )
        == "subsystem-level"
    )


def test_markdown_report_qualifies_conclusion_scope() -> None:
    semantic = {
        "candidate": {"commit": "1" * 40, "tree": "2" * 40},
        "base_commit": "3" * 40,
        "conclusion_scope": "patch-local release candidate",
        "release_candidate_validation_passed": False,
        "network_isolation": {"mechanism": "netns"},
        "changed_path_mapping": [],
        "focused_validation": [],
        "complete_suite_validation": None,
        "generated_artifact_attestation": {
            "artifact_hashes": {},
            "unresolved_loader_literals": ["loader: missing.json"],
            "all_discovered_bindings_resolved": False,
            "architecture_limit": "not atomic",
        },
        "unmet_deployed_checks": [],
    }
    report = release_gate.markdown_report(
        semantic, {"candidate_unchanged_after_validation": True}
    )
    assert "patch-local release candidate" in report
    assert "system-wide" not in report
    assert "unqualified claim" in report


def test_detached_candidate_worktree_is_exact_and_removed(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    commit = _run(["git", "rev-parse", "HEAD"], repo)
    scratch = tmp_path / "scratch"
    with release_gate.detached_candidate_worktree(repo, commit, scratch) as checkout:
        assert _run(["git", "rev-parse", "HEAD"], checkout) == commit
        assert _run(["git", "status", "--porcelain"], checkout) == ""
        remembered = checkout
    assert not remembered.exists()
    assert remembered.as_posix() not in _run(
        ["git", "worktree", "list", "--porcelain"], repo
    )


def test_changed_paths_are_bound_to_explicit_base(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    base = _run(["git", "rev-parse", "HEAD"], repo)
    (repo / "helper.py").write_text("VALUE = 2\n", encoding="utf-8")
    _run(["git", "add", "helper.py"], repo)
    _run(["git", "commit", "-qm", "change"], repo)
    candidate = _run(["git", "rev-parse", "HEAD"], repo)
    assert release_gate.changed_paths(repo, base, candidate) == ("helper.py",)


def test_non_development_identities_require_exact_base_and_candidate(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path)
    commit = _run(["git", "rev-parse", "HEAD"], repo)
    assert release_gate.resolve_release_identities(
        repo, base=commit, candidate=commit, development=False
    ) == (commit, commit)
    with pytest.raises(release_gate.ReleaseGateError, match="exact 40-hex base"):
        release_gate.resolve_release_identities(
            repo, base="HEAD", candidate=commit, development=False
        )
    with pytest.raises(release_gate.ReleaseGateError, match="candidate commit"):
        release_gate.resolve_release_identities(
            repo, base=commit, candidate=None, development=False
        )


def test_production_head_must_equal_exact_base() -> None:
    release_gate.require_production_baseline(
        {"available": True, "repository_commit": "a" * 40},
        base_commit="a" * 40,
        development=False,
    )
    with pytest.raises(release_gate.ReleaseGateError, match="does not equal"):
        release_gate.require_production_baseline(
            {"available": True, "repository_commit": "b" * 40},
            base_commit="a" * 40,
            development=False,
        )


def test_canonical_json_ignores_mapping_insertion_order() -> None:
    assert release_gate.canonical_json_bytes({"b": 2, "a": 1}) == (
        release_gate.canonical_json_bytes({"a": 1, "b": 2})
    )


def test_full_suite_command_records_current_parallel_strategy() -> None:
    command = release_gate.full_suite_command(4)
    assert command[-3:] == (
        "4",
        "--dist=worksteal",
        "--max-worker-restart=0",
    )
    assert "-p" in command and "xdist.plugin" in command


def test_generated_relationship_inventory_reports_architecture_limit(
    tmp_path: Path,
) -> None:
    (tmp_path / "runtime_manifest.json").write_text("{}\n", encoding="utf-8")
    inventory = release_gate.relationship_inventory(
        tmp_path,
        _snapshot(),
        [{"loader": "mrsMThatcher2.py", "resolved_path": "runtime_manifest.json"}],
        ["loader.py: unknown_manifest.json"],
    )
    assert inventory["all_discovered_bindings_resolved"] is False
    assert "no single atomic generation identity" in inventory["architecture_limit"]
    commands = release_gate.relationship_validation_commands(
        {
            "relationship_validators": [
                "python3 -m pytest -q tests/test_relationship.py"
            ]
        }
    )
    assert commands == (
        [("python3", "-m", "pytest", "-q", "tests/test_relationship.py")]
    )


def test_artifact_semantics_capture_versions_counts_and_pins(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "policy_version": "policy-v3",
                "quote_count": 611,
                "input_hashes": {"quotes.json": "a" * 64},
            }
        ),
        encoding="utf-8",
    )
    summary = release_gate._artifact_semantic_summary(tmp_path, "manifest.json")
    assert summary["schema_policy_versions"] == {
        "policy_version": "policy-v3",
        "schema_version": 3,
    }
    assert summary["semantic_counts"] == {"quote_count": 611}
    assert summary["declared_input_and_source_pins"] == {
        "input_hashes": {"quotes.json": "a" * 64}
    }


def test_service_identity_comparison_uses_stable_process_fields() -> None:
    before = {
        "available": True,
        "properties": {
            "ActiveState": "active",
            "SubState": "running",
            "MainPID": "10",
            "ExecMainStartTimestamp": "today",
            "NRestarts": "0",
            "Unrelated": "before",
        },
        "children": [{"pid": 11, "command_sha256": "a", "is_python": True}],
    }
    after = json.loads(json.dumps(before))
    after["properties"]["Unrelated"] = "after"
    assert release_gate.service_invariants_equal(before, after)
    after["properties"]["NRestarts"] = "1"
    assert not release_gate.service_invariants_equal(before, after)


def test_emit_outputs_includes_required_priority0_deliverables(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "production_invariants.json"
    ledger_path = tmp_path / "defect_ledger.json"
    registry_path.write_text(
        json.dumps(
            {
                "invariants": [
                    {
                        "id": "INV-REL-001",
                        "criticality": "critical",
                        "implementation_status": "partial",
                        "affected_paths": ["tools/release_gate.py"],
                        "enforcement": {
                            "commands": [{"command": "python3 -m pytest -q"}]
                        },
                        "known_gaps": ["independent review pending"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    ledger_path.write_text(
        json.dumps({"defects": [{"id": "DEF-001", "status": "verified"}]}),
        encoding="utf-8",
    )
    semantic = {
        "candidate": _snapshot().semantic_dict(),
        "base_commit": "6" * 40,
        "invariant_registry_sha256": "7" * 64,
        "defect_ledger_sha256": "8" * 64,
        "changed_path_mapping": [],
        "affected_invariant_ids": [],
        "focused_validation": [],
        "complete_suite_validation": None,
        "network_isolation": {
            "available": True,
            "mechanism": "netns",
            "subprocess_egress_denied": True,
            "exit_status": 0,
            "output_sha256": "9" * 64,
        },
        "generated_artifact_attestation": {
            "artifact_hashes": {},
            "unresolved_loader_literals": [],
            "all_discovered_bindings_resolved": True,
            "architecture_limit": "current generation is not atomic",
        },
        "conclusion_scope": "patch-local release candidate",
        "release_candidate_validation_passed": False,
    }
    receipt = {
        "candidate_unchanged_after_validation": True,
        "production_identity_before": {"repository_commit": "6" * 40},
        "production_identity_unchanged": True,
        "service_invariants_unchanged": True,
        "procedural_notes": ["A preflight-only predecessor stopped before tests."],
    }
    output = tmp_path / "output"
    (output / "validation").mkdir(parents=True)
    (output / "validation" / "focused-001.output.txt").write_text(
        "passed\n", encoding="utf-8"
    )
    inventory = release_gate.emit_outputs(
        output_dir=output,
        semantic=semantic,
        receipt=receipt,
        registry_path=registry_path,
        ledger_path=ledger_path,
        diff_hash="a" * 64,
        source_diagnosis_path="/evidence/diagnosis.md",
        source_diagnosis_sha256="b" * 64,
    )
    assert "priority0_consolidation_report.md" in inventory
    assert "priority0_consolidation_final_validation.json" in inventory
    assert "validation/focused-001.output.txt" in inventory
    final = json.loads(
        (output / "priority0_consolidation_final_validation.json").read_text()
    )
    assert final["candidate_commit"] == "1" * 40
    assert final["unresolved_or_partial_invariant_ids"] == ["INV-REL-001"]
    assert final["task_actions"]["x_actions"] == 0
    assert final["procedural_notes"] == [
        "A preflight-only predecessor stopped before tests."
    ]
    report = (output / "priority0_consolidation_report.md").read_text()
    assert "preflight-only predecessor stopped before tests" in report
    review = json.loads((output / "independent_review_manifest.json").read_text())
    assert review["changed_path_mapping"] == []


def test_control_schema_is_validated_before_mapping(tmp_path: Path) -> None:
    schema = tmp_path / "schema.json"
    schema.write_text(
        json.dumps(
            {
                "type": "object",
                "required": ["invariants"],
                "properties": {"invariants": {"type": "array"}},
                "additionalProperties": False,
            }
        ),
        encoding="utf-8",
    )
    release_gate.validate_control_schema(
        {"invariants": []}, schema, label="fixture"
    )
    with pytest.raises(release_gate.ReleaseGateError, match="schema validation"):
        release_gate.validate_control_schema(
            {"wrong": []}, schema, label="fixture"
        )
