"""Focused offline tests for the frozen-candidate release gate."""

from __future__ import annotations

import argparse
import dataclasses
import errno
import hashlib
import json
import multiprocessing
import os
import socket
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
        "index_flagged_files": (),
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


@pytest.mark.parametrize(
    ("flag", "expected_marker"),
    [
        ("--assume-unchanged", "h helper.py"),
        ("--skip-worktree", "S helper.py"),
    ],
)
def test_nondefault_index_flags_cannot_hide_candidate_drift(
    tmp_path: Path, flag: str, expected_marker: str
) -> None:
    repo = _git_repo(tmp_path)
    _run(["git", "update-index", flag, "helper.py"], repo)
    snapshot, _bindings, _unresolved = release_gate.take_snapshot(repo)
    assert expected_marker in snapshot.index_flagged_files
    with pytest.raises(release_gate.ReleaseGateError, match="index flag"):
        release_gate.require_frozen(snapshot, development=False)


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


def test_new_registry_records_are_explicit_when_base_has_no_registry(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path)
    base = _run(["git", "rev-parse", "HEAD"], repo)
    transition = release_gate.registry_record_transition(
        repo,
        base_commit=base,
        candidate_registry={"invariants": [_invariant()]},
    )
    assert transition["base_registry_present"] is False
    assert transition["added_invariant_ids"] == ["INV-REL-001"]


def test_registry_record_removal_or_rename_fails_closed(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "production_invariants.json").write_text(
        json.dumps(
            {
                "invariants": [
                    _invariant("INV-REL-001"),
                    _invariant("INV-TEST-001"),
                ]
            }
        ),
        encoding="utf-8",
    )
    _run(["git", "add", "production_invariants.json"], repo)
    _run(["git", "commit", "-qm", "add registry"], repo)
    base = _run(["git", "rev-parse", "HEAD"], repo)
    with pytest.raises(release_gate.ReleaseGateError, match="removes or renames"):
        release_gate.registry_record_transition(
            repo,
            base_commit=base,
            candidate_registry={"invariants": [_invariant("INV-REL-001")]},
        )


def test_unmapped_runtime_path_is_rejected() -> None:
    with pytest.raises(release_gate.ReleaseGateError, match="unmapped"):
        release_gate.map_changed_paths(
            ["mrsMThatcher2.py"],
            [_invariant(paths=["historical_context_*.py"])],
            runtime_paths={"mrsMThatcher2.py"},
            generated_paths=set(),
        )


def test_registry_definition_change_affects_every_invariant() -> None:
    invariants = [
        _invariant("INV-ART-001", paths=["artifact.json"]),
        _invariant("INV-REL-001", paths=["tools/release_gate.py"]),
    ]
    mapped, uncovered, affected = release_gate.map_changed_paths(
        ["production_invariants.json"],
        invariants,
        runtime_paths=set(),
        generated_paths=set(),
    )
    assert uncovered == []
    assert mapped == [
        {
            "path": "production_invariants.json",
            "category": "control",
            "invariant_ids": ["INV-ART-001", "INV-REL-001"],
        }
    ]
    assert [record["invariant_id"] for record in affected] == [
        "INV-ART-001",
        "INV-REL-001",
    ]


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
    common = release_gate.git_common_dir(repo)
    before = release_gate.recursive_metadata_identity(common)
    with release_gate.integration_lock(repo):
        assert release_gate.recursive_metadata_identity(common) == before
        with pytest.raises(release_gate.ReleaseGateError, match="already held"):
            with release_gate.integration_lock(repo):
                raise AssertionError("unreachable")
    assert release_gate.recursive_metadata_identity(common) == before
    assert not (common / "mrsMThatcher.release-gate.lock").exists()


def test_runtime_python_discovery_follows_local_imports(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    assert release_gate.discover_runtime_python_files(repo) == (
        "helper.py",
        "mrsMThatcher2.py",
    )


def test_runtime_python_discovery_follows_package_and_relative_imports(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path)
    package = repo / "runtime_package"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "worker.py").write_text(
        "from .io import atomic_write\n", encoding="utf-8"
    )
    (package / "io.py").write_text(
        "def atomic_write():\n    return None\n", encoding="utf-8"
    )
    (repo / "mrsMThatcher2.py").write_text(
        "from runtime_package.worker import atomic_write\n", encoding="utf-8"
    )
    _run(["git", "add", "."], repo)
    _run(["git", "commit", "-qm", "package imports"], repo)

    assert release_gate.discover_runtime_python_files(repo) == (
        "mrsMThatcher2.py",
        "runtime_package/__init__.py",
        "runtime_package/io.py",
        "runtime_package/worker.py",
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


def test_dynamic_runtime_root_resolves_ambiguous_loader_literal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _git_repo(tmp_path)
    research = repo / "semantic_alignment_research" / "quote_research_full_001"
    other = repo / "other"
    research.mkdir(parents=True)
    other.mkdir()
    (research / "research_packets.json").write_text("{}\n", encoding="utf-8")
    (other / "research_packets.json").write_text("{}\n", encoding="utf-8")
    (repo / "helper.py").write_text(
        'NAME = "research_packets.json"\n', encoding="utf-8"
    )
    _run(["git", "add", "."], repo)
    _run(["git", "commit", "-qm", "ambiguous artifacts"], repo)
    monkeypatch.setitem(
        release_gate.RUNTIME_LOADER_ROOTS,
        "helper.py",
        ("semantic_alignment_research/quote_research_full_001",),
    )

    generated, bindings, unresolved = release_gate.discover_generated_artifacts(
        repo, ("helper.py",)
    )

    assert generated == (
        "semantic_alignment_research/quote_research_full_001/research_packets.json",
    )
    assert bindings == [
        {
            "loader": "helper.py",
            "literal": "research_packets.json",
            "resolved_path": (
                "semantic_alignment_research/quote_research_full_001/"
                "research_packets.json"
            ),
            "resolution": "loader_runtime_root",
        }
    ]
    assert unresolved == []


def test_validator_backed_offline_ambiguity_is_classified_not_selected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _git_repo(tmp_path)
    for directory in ("one", "two"):
        path = repo / directory
        path.mkdir()
        (path / "run_manifest.json").write_text("{}\n", encoding="utf-8")
    (repo / "helper.py").write_text(
        'NAME = "run_manifest.json"\n', encoding="utf-8"
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_helper.py").write_text(
        "def test_manifest_relationship():\n    assert True\n",
        encoding="utf-8",
    )
    _run(["git", "add", "."], repo)
    _run(["git", "commit", "-qm", "offline ambiguity"], repo)
    monkeypatch.setitem(
        release_gate.VALIDATOR_BACKED_OFFLINE_LITERALS,
        ("helper.py", "run_manifest.json"),
        "tests/test_helper.py",
    )

    generated, bindings, unresolved = release_gate.discover_generated_artifacts(
        repo, ("helper.py",)
    )

    assert generated == ()
    assert bindings[0]["resolution"] == "validator_backed_offline_literal"
    assert bindings[0]["validator"] == "tests/test_helper.py"
    assert unresolved == []


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
                "status": "direct",
                "artifacts": ["runtime.txt"],
            },
        }
    ]
    declared = release_gate.registry_declared_runtime_artifacts(repo, records)
    assert declared == ("runtime.txt",)
    snapshot, _bindings, _unresolved = release_gate.take_snapshot(repo, declared)
    assert dict(snapshot.declared_artifact_hashes)["runtime.txt"] == hashlib.sha256(
        b"runtime\n"
    ).hexdigest()


def test_missing_ephemeral_registry_runtime_artifact_is_recorded_not_required(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path)
    records = [
        {
            "invariant_id": "INV-ART-001",
            "runtime_consumed_artifacts": {
                "status": "direct",
                "artifacts": ["missing.json"],
            },
        }
    ]
    declared, inventory = release_gate.registry_runtime_artifact_inventory(
        repo, records
    )
    assert declared == ()
    assert inventory == [
        {
            "invariant_id": "INV-ART-001",
            "path": "missing.json",
            "runtime_status": "direct",
            "frozen_candidate_required": False,
            "disposition": "production_only_or_ephemeral_absent",
        }
    ]


def test_missing_required_frozen_registry_artifact_fails_closed(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path)
    records = [
        {
            "invariant_id": "INV-ART-001",
            "runtime_consumed_artifacts": {
                "status": "direct",
                "artifacts": ["missing.json"],
                "frozen_candidate_required_artifacts": ["missing.json"],
            },
        }
    ]
    with pytest.raises(release_gate.ReleaseGateError, match="required frozen"):
        release_gate.registry_runtime_artifact_inventory(repo, records)


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
    assert any('exec setpriv ' in token for token in observed[0])
    assert any("--no-new-privs" in token for token in observed[0])
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


def test_command_runner_never_inherits_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}

    def fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed.update(kwargs)
        return subprocess.CompletedProcess(["true"], 0, b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    release_gate._run(("true",), cwd=tmp_path)
    assert observed["stdin"] is subprocess.DEVNULL


def test_containment_wrapper_binds_production_root_read_only(tmp_path: Path) -> None:
    dependency = tmp_path / "dependencies"
    dependency.mkdir()
    wrapped = release_gate.containment_namespace_command(
        ("python3", "-m", "pytest"),
        production_root=tmp_path,
        dependency_roots=(dependency,),
    )
    assert str(tmp_path) in wrapped
    assert str(dependency) in wrapped
    script = wrapped[wrapped.index("-c") + 1]
    assert 'mount --bind "$1" "$1"' in script
    assert "remount,bind,ro" in script
    assert "--pid" in wrapped
    assert "--fork" in wrapped
    assert "--mount-proc" in wrapped


def test_real_containment_denies_candidate_and_git_mutation(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path)
    production = tmp_path / "production"
    production.mkdir()
    dependency = tmp_path / "dependencies"
    dependency.mkdir()
    unit_file = tmp_path / "mrsMThatcher.service"
    unit_file.write_text("[Service]\n", encoding="utf-8")
    sockets = release_gate.service_control_socket_paths()
    result = release_gate.containment_preflight(
        cwd=repo,
        production_root=production,
        candidate_root=repo,
        dependency_roots=(dependency,),
        additional_read_only_paths=(unit_file,),
        blocked_unix_sockets=sockets,
    )
    if not result["available"]:
        pytest.skip(f"OS containment unavailable on this test host: {result['reason']}")
    assert result["candidate_root_read_only"] is True
    assert result["git_common_root_read_only"] is True
    assert result["production_root_read_only"] is True
    assert result["validation_dependency_roots_read_only"] is True
    assert result["additional_protected_paths_read_only"] is True
    assert result["user_service_control_sockets_blocked"] is True
    assert result["pathname_unix_socket_creation_denied"] is True
    assert result["unix_socketpair_creation_denied"] is True
    assert result["unix_socket_high_bits_alias_denied"] is True
    assert result["io_uring_setup_denied"] is True
    assert result["blocked_unix_socket_paths"] == [
        str(path) for path in sockets
    ]
    assert result["effective_capabilities_dropped"] is True
    assert result["no_new_privileges"] is True
    assert result["read_only_remount_denied_after_capability_drop"] is True
    assert not any(repo.glob(".mrs-release-gate-readonly-probe-*"))
    assert not any(production.glob(".mrs-release-gate-readonly-probe-*"))
    assert not any(dependency.glob(".mrs-release-gate-readonly-probe-*"))


def test_containment_denies_all_host_unix_socket_connections(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path)
    socket_path = tmp_path / "harmless-host-control.sock"
    try:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except PermissionError as exc:
        if exc.errno == errno.EPERM:
            pytest.skip("outer release-gate containment already denies AF_UNIX")
        raise
    try:
        listener.bind(str(socket_path))
        listener.listen(1)
        listener.settimeout(0.2)
        script = (
            "import errno,socket,sys\n"
            "try:\n"
            " client=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)\n"
            "except OSError as exc:\n"
            " if exc.errno != errno.EPERM: raise SystemExit(92)\n"
            " try:\n"
            "  socket.socketpair(socket.AF_UNIX,socket.SOCK_DGRAM)\n"
            " except OSError as pair_exc:\n"
            "  raise SystemExit(0 if pair_exc.errno == errno.EPERM else 93)\n"
            " raise SystemExit(94)\n"
            "client.connect(sys.argv[1])\n"
            "raise SystemExit(91)\n"
        )
        command = release_gate.containment_namespace_command(
            (sys.executable, "-c", script, str(socket_path)),
            candidate_root=repo,
        )
        result = release_gate._run(
            command, cwd=repo, check=False, timeout=10
        )
        if result.returncode in {1, 75} and b"Operation not permitted" in result.stdout:
            pytest.skip("OS containment unavailable on this test host")
        assert result.returncode == 0, result.stdout.decode("utf-8", "replace")
        with pytest.raises(TimeoutError):
            listener.accept()
    finally:
        listener.close()
        socket_path.unlink(missing_ok=True)


def test_containment_makes_distinct_source_worktree_read_only(
    tmp_path: Path,
) -> None:
    candidate = _git_repo(tmp_path)
    source = tmp_path / "source-worktree"
    source.mkdir()
    protected = source / "tools.py"
    protected.write_text("VALUE = 1\n", encoding="utf-8")
    script = (
        "import errno,os,sys\n"
        "try:\n"
        " descriptor=os.open(sys.argv[1],os.O_WRONLY)\n"
        "except OSError as exc:\n"
        " raise SystemExit(0 if exc.errno in {errno.EROFS,errno.EACCES} else 92)\n"
        "os.close(descriptor)\n"
        "raise SystemExit(91)\n"
    )
    command = release_gate.containment_namespace_command(
        (sys.executable, "-c", script, str(protected)),
        candidate_root=candidate,
        additional_read_only_paths=(source,),
    )
    result = release_gate._run(command, cwd=candidate, check=False, timeout=10)
    if result.returncode in {1, 75} and b"Operation not permitted" in result.stdout:
        pytest.skip("OS containment unavailable on this test host")
    assert result.returncode == 0, result.stdout.decode("utf-8", "replace")
    assert protected.read_text(encoding="utf-8") == "VALUE = 1\n"


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
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["PYTHONSAFEPATH"] == "1"


def test_validation_environment_uses_only_attested_dependency_roots(
    tmp_path: Path,
) -> None:
    dependency_path = tmp_path / "dependencies"
    dependency_path.mkdir()
    toolchain = release_gate.ValidationToolchain(
        semantic_inventory_json='{"schema_version":1}\n',
        python_paths=(str(dependency_path.resolve()),),
    )

    environment = release_gate.sanitized_validation_environment_for_toolchain(
        tmp_path / "home", toolchain
    )

    assert environment["PYTHONPATH"] == str(dependency_path.resolve())
    assert environment["PYTHONNOUSERSITE"] == "1"


def test_validation_toolchain_is_versioned_and_content_bound() -> None:
    toolchain = release_gate.validation_toolchain_inventory()
    semantic = toolchain.semantic_dict()
    assert semantic["python"]["executable_sha256"]
    distributions = {
        item["distribution"] for item in semantic["distributions"]
    }
    assert {"pytest", "pytest-xdist", "jsonschema"} == distributions
    assert all(item["version"] for item in semantic["distributions"])
    assert semantic["schema_version"] == 2
    assert all(item["content_sha256"] for item in semantic["import_roots"])
    assert all(item["entry_count"] > 0 for item in semantic["import_roots"])
    assert {
        item["module"] for item in semantic["module_distribution_bindings"]
    } == {"pytest", "_pytest", "xdist", "jsonschema"}
    assert toolchain.python_paths
    assert set(toolchain.python_paths) <= set(toolchain.protected_paths)
    containment = semantic["os_containment_dependencies"]
    assert len(containment) == 1
    assert containment[0]["purpose"].startswith("deny AF_UNIX")
    seccomp_path = release_gate.seccomp_library_path()
    assert containment[0]["path"] == str(seccomp_path)
    assert containment[0]["sha256"] == release_gate.sha256_file(seccomp_path)
    assert str(seccomp_path) in toolchain.protected_paths


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
        "registry_transition": {
            "base_registry_present": False,
            "added_invariant_ids": ["INV-REL-001"],
            "removed_invariant_ids": [],
            "changed_invariant_ids": [],
            "unchanged_invariant_ids": [],
        },
        "path_mapping": [],
        "affected": [],
        "focused": [result],
        "full": result,
        "network": {
            "available": True,
            "mechanism": "netns",
            "subprocess_egress_denied": True,
            "network_route_isolated": True,
            "production_root_read_only": True,
            "candidate_root_read_only": True,
            "git_common_root_read_only": True,
            "validation_dependency_roots_read_only": True,
            "additional_protected_paths_read_only": True,
            "installed_service_unit_read_only": True,
            "user_service_control_sockets_blocked": True,
            "pathname_unix_socket_creation_denied": True,
            "unix_socketpair_creation_denied": True,
            "unix_socket_high_bits_alias_denied": True,
            "io_uring_setup_denied": True,
            "effective_capabilities_dropped": True,
            "no_new_privileges": True,
            "read_only_remount_denied_after_capability_drop": True,
            "exit_status": 0,
            "output_sha256": "9" * 64,
            "volatile_reason": "ignored",
        },
        "toolchain": {
            "schema_version": 1,
            "python": {"version": "3.10.12"},
        },
        "relationships": {
            "artifact_hashes": {},
            "unresolved_loader_literals": [],
            "all_claimed_runtime_bindings_resolved": True,
            "all_discovered_bindings_resolved": True,
            "all_validator_backed_classifications_valid": True,
            "all_required_source_file_pins_valid": True,
        },
        "deployed_checks": [],
        "scope": "patch-local release candidate",
    }
    attestation = release_gate.deterministic_attestation(**values)
    assert attestation["release_candidate_validation_passed"] is True
    first = release_gate.canonical_json_bytes(attestation)
    second = release_gate.canonical_json_bytes(
        release_gate.deterministic_attestation(**values)
    )
    assert first == second
    assert b"123.456" not in first
    assert b"output_sha256" not in first
    values["relationships"] = {
        **values["relationships"],
        "all_required_source_file_pins_valid": False,
    }
    assert (
        release_gate.deterministic_attestation(**values)[
            "release_candidate_validation_passed"
        ]
        is False
    )


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


def test_warning_count_ignores_unbounded_candidate_controlled_integer() -> None:
    assert release_gate._warning_count(b"12 warnings") == 12
    assert release_gate._warning_count(b"9" * 5000 + b" warnings") == 0


def test_pytest_junit_counts_require_real_reconciled_testcases() -> None:
    payload = (
        b'<testsuite tests="3" failures="1" errors="0" skipped="1">'
        b'<testcase name="passed"/>'
        b'<testcase name="failed"><failure/></testcase>'
        b'<testcase name="skipped"><skipped/></testcase>'
        b"</testsuite>"
    )
    assert release_gate._pytest_counts_bytes(payload) == (1, 1, 0, 1)
    with pytest.raises(
        release_gate.ReleaseGateError, match="testcase outcomes differ"
    ):
        release_gate._pytest_counts_bytes(
            payload.replace(b'failures="1"', b'failures="0"')
        )
    with pytest.raises(release_gate.ReleaseGateError, match="no test passed"):
        release_gate._pytest_counts_bytes(
            b'<testsuite tests="1" failures="0" errors="0" skipped="1">'
            b'<testcase name="skipped"><skipped/></testcase>'
            b"</testsuite>"
        )


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
    parent = _run(["git", "rev-parse", "HEAD"], repo)
    (repo / "history.py").write_text("VALUE = 1\n", encoding="utf-8")
    _run(["git", "add", "history.py"], repo)
    _run(["git", "commit", "-qm", "history fixture"], repo)
    commit = _run(["git", "rev-parse", "HEAD"], repo)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    common = release_gate.git_common_dir(repo)
    common_before = release_gate.recursive_metadata_identity(common)
    worktrees_before = _run(["git", "worktree", "list", "--porcelain"], repo)
    with release_gate.detached_candidate_worktree(repo, commit, scratch) as checkout:
        assert _run(["git", "rev-parse", "HEAD"], checkout) == commit
        assert _run(["git", "rev-parse", "HEAD^"], checkout) == parent
        assert (
            _run(
                ["git", "merge-base", "--is-ancestor", parent, commit],
                checkout,
            )
            == ""
        )
        assert _run(["git", "status", "--porcelain"], checkout) == ""
        assert release_gate.git_common_dir(checkout) != common
        assert _run(["git", "worktree", "list", "--porcelain"], repo) == (
            worktrees_before
        )
        assert release_gate.recursive_metadata_identity(common) == common_before
        remembered = checkout
    assert not remembered.exists()
    assert _run(["git", "worktree", "list", "--porcelain"], repo) == (
        worktrees_before
    )
    assert release_gate.recursive_metadata_identity(common) == common_before


def test_detached_candidate_fetches_ledger_commit_from_side_branch(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path)
    main_branch = _run(["git", "branch", "--show-current"], repo)
    _run(["git", "checkout", "-qb", "ledger-history"], repo)
    (repo / "side.py").write_text("SIDE = 1\n", encoding="utf-8")
    _run(["git", "add", "side.py"], repo)
    _run(["git", "commit", "-qm", "side-only ledger commit"], repo)
    side_commit = _run(["git", "rev-parse", "HEAD"], repo)
    _run(["git", "checkout", "-q", main_branch], repo)
    (repo / "candidate.py").write_text("CANDIDATE = 1\n", encoding="utf-8")
    _run(["git", "add", "candidate.py"], repo)
    _run(["git", "commit", "-qm", "candidate commit"], repo)
    candidate = _run(["git", "rev-parse", "HEAD"], repo)
    assert (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", side_commit, candidate],
            cwd=repo,
            check=False,
        ).returncode
        != 0
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    common = release_gate.git_common_dir(repo)
    common_before = release_gate.recursive_metadata_identity(common)
    with release_gate.detached_candidate_worktree(
        repo,
        candidate,
        scratch,
        required_commits=(side_commit,),
    ) as checkout:
        assert _run(["git", "rev-parse", "HEAD"], checkout) == candidate
        assert (
            _run(["git", "cat-file", "-t", f"{side_commit}^{{commit}}"], checkout)
            == "commit"
        )
        assert _run(["git", "status", "--porcelain"], checkout) == ""
    assert release_gate.recursive_metadata_identity(common) == common_before


def test_ledger_commit_identities_cover_all_commit_bearing_fields() -> None:
    commits = [f"{index:040x}" for index in range(1, 12)]
    ledger = {
        "baseline": {
            "current_master_commit": commits[0],
            "observed_production_commit": commits[1],
        },
        "defects": [
            {
                "introduced": {
                    "first_bad_commit": commits[2],
                    "last_known_good_commit": commits[3],
                    "affected_range": (
                        f"inclusive:{commits[4]}..{commits[5]}"
                    ),
                },
                "chronology": [{"commit": commits[6]}],
                "first_review_scope": {"reviewed_revision": commits[7]},
                "detection": {"revision": commits[8]},
                "fix": {"commit": commits[9]},
                "deployment": {"observed_commit": commits[10]},
                "tests": [{"commit": commits[5]}],
            }
        ],
    }
    assert release_gate.ledger_commit_identities(ledger) == tuple(commits)


def test_detached_candidate_reverification_rejects_tracked_mutation(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path)
    commit = _run(["git", "rev-parse", "HEAD"], repo)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with release_gate.detached_candidate_worktree(
        repo, commit, scratch
    ) as checkout:
        before, bindings, unresolved = release_gate.take_snapshot(checkout)
        release_gate.assert_candidate_checkout_unchanged(
            checkout,
            expected_snapshot=before,
            expected_bindings=bindings,
            expected_unresolved=unresolved,
            declared_runtime_artifacts=(),
        )
        (checkout / "helper.py").write_text("VALUE = 2\n", encoding="utf-8")
        with pytest.raises(
            release_gate.ReleaseGateError,
            match="candidate changed while validation was running",
        ):
            release_gate.assert_candidate_checkout_unchanged(
                checkout,
                expected_snapshot=before,
                expected_bindings=bindings,
                expected_unresolved=unresolved,
                declared_runtime_artifacts=(),
            )


def test_detached_checkout_ignores_inherited_git_hooks_and_filters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _git_repo(tmp_path)
    (repo / ".gitattributes").write_text(
        "*.py filter=release-gate-evil\n", encoding="utf-8"
    )
    _run(["git", "add", ".gitattributes"], repo)
    _run(["git", "commit", "-qm", "filter fixture"], repo)
    commit = _run(["git", "rev-parse", "HEAD"], repo)
    marker = tmp_path / "git-config-executed"
    filter_program = tmp_path / "filter.sh"
    filter_program.write_text(
        f"#!/bin/sh\nprintf hit >> {marker}\ncat\n",
        encoding="utf-8",
    )
    filter_program.chmod(0o700)
    template = tmp_path / "template"
    hooks = template / "hooks"
    hooks.mkdir(parents=True)
    post_checkout = hooks / "post-checkout"
    post_checkout.write_text(
        f"#!/bin/sh\nprintf hook >> {marker}\n", encoding="utf-8"
    )
    post_checkout.chmod(0o700)
    global_config = tmp_path / "malicious.gitconfig"
    global_config.write_text(
        "[init]\n"
        f"\ttemplateDir = {template}\n"
        '[filter "release-gate-evil"]\n'
        f"\tsmudge = {filter_program}\n"
        f"\tclean = {filter_program}\n"
        "\trequired = true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(template))
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "wrong-git-dir"))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with release_gate.detached_candidate_worktree(
        repo, commit, scratch
    ) as checkout:
        assert release_gate._git(checkout, "rev-parse", "HEAD") == commit
        assert (checkout / ".gitattributes").is_file()
    assert not marker.exists()


def test_independent_checkout_does_not_mutate_linked_source_common_dir(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path)
    linked = tmp_path / "linked-source"
    _run(["git", "worktree", "add", "--detach", str(linked), "HEAD"], repo)
    commit = _run(["git", "rev-parse", "HEAD"], linked)
    common = release_gate.git_common_dir(linked)
    before = release_gate.recursive_metadata_identity(common)
    worktrees_before = _run(["git", "worktree", "list", "--porcelain"], repo)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with release_gate.detached_candidate_worktree(
        linked, commit, scratch
    ) as checkout:
        assert release_gate._git(checkout, "rev-parse", "HEAD") == commit
        assert release_gate.recursive_metadata_identity(common) == before
    assert release_gate.recursive_metadata_identity(common) == before
    assert _run(["git", "worktree", "list", "--porcelain"], repo) == (
        worktrees_before
    )


def test_changed_paths_are_bound_to_explicit_base(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    base = _run(["git", "rev-parse", "HEAD"], repo)
    (repo / "helper.py").write_text("VALUE = 2\n", encoding="utf-8")
    _run(["git", "add", "helper.py"], repo)
    _run(["git", "commit", "-qm", "change"], repo)
    candidate = _run(["git", "rev-parse", "HEAD"], repo)
    assert release_gate.changed_paths(repo, base, candidate) == ("helper.py",)


def test_changed_paths_ignores_hostile_inherited_git_redirection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _git_repo(tmp_path)
    base = _run(["git", "rev-parse", "HEAD"], repo)
    (repo / "helper.py").write_text("VALUE = 2\n", encoding="utf-8")
    _run(["git", "add", "helper.py"], repo)
    _run(["git", "commit", "-qm", "change"], repo)
    candidate = _run(["git", "rev-parse", "HEAD"], repo)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "wrong-git-dir"))
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(tmp_path / "wrong-objects"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.fsmonitor")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "/bin/false")
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


def test_artifact_source_file_pins_are_recomputed(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text('{"source":true}\n', encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "source_file_hashes": {
                    "source": {
                        "path": "source.json",
                        "sha256": release_gate.sha256_file(source),
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    summary = release_gate._artifact_semantic_summary(tmp_path, "manifest.json")
    assert summary["recomputed_declared_pins"] == [
        {
            "field": "source_file_hashes",
            "pin_name": "source",
            "expected_sha256": release_gate.sha256_file(source),
            "resolved_path": "source.json",
            "actual_sha256": release_gate.sha256_file(source),
            "match": True,
            "runtime_relationship_required": True,
            "resolution_error": None,
        }
    ]


def test_required_source_file_pin_mismatch_is_not_fully_attested(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.json"
    source.write_text('{"source":true}\n', encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "source_file_hashes": {
                    "source": {
                        "path": "source.json",
                        "sha256": "0" * 64,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    inventory = release_gate.relationship_inventory(
        tmp_path,
        _snapshot(
            generated_hashes=(),
            declared_artifact_hashes=(
                ("manifest.json", release_gate.sha256_file(manifest)),
            )
        ),
        [],
        [],
    )
    assert inventory["all_required_source_file_pins_valid"] is False
    assert inventory["required_source_file_pin_failures"][0]["match"] is False


def test_service_identity_comparison_uses_stable_process_fields() -> None:
    before = {
        "available": True,
        "properties": {
            "ActiveState": "active",
            "SubState": "running",
            "MainPID": "10",
            "ExecMainStartTimestamp": "today",
            "NRestarts": "0",
            "ExecStart": "/usr/local/bin/runMrsMThatcher2",
            "WorkingDirectory": "/production",
            "FragmentPath": "/unit/mrsMThatcher.service",
            "DropInPaths": "",
            "Unrelated": "before",
        },
        "children": [{"pid": 11, "command_sha256": "a", "is_python": True}],
        "unit_file_identities": {
            "/unit/mrsMThatcher.service": {"sha256": "b" * 64}
        },
    }
    after = json.loads(json.dumps(before))
    after["properties"]["Unrelated"] = "after"
    assert release_gate.service_invariants_equal(before, after)
    after["properties"]["NRestarts"] = "1"
    assert not release_gate.service_invariants_equal(before, after)
    for key in ("ExecStart", "WorkingDirectory", "FragmentPath", "DropInPaths"):
        changed = json.loads(json.dumps(before))
        changed["properties"][key] += "-changed"
        assert not release_gate.service_invariants_equal(before, changed)
    changed = json.loads(json.dumps(before))
    changed["unit_file_identities"]["/unit/mrsMThatcher.service"][
        "sha256"
    ] = "c" * 64
    assert not release_gate.service_invariants_equal(before, changed)


def test_emit_outputs_includes_required_priority0_deliverables(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "production_invariants.json"
    ledger_path = tmp_path / "defect_ledger.json"
    registry = {
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
    ledger = {"defects": [{"id": "DEF-001", "status": "verified"}]}
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    (tmp_path / "why_code_reviews_continue_to_find_major_problems.md").write_text(
        "# Diagnosis\n", encoding="utf-8"
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
    diagnosis_bytes = b"# Original diagnosis\n"
    diagnosis_sha256 = release_gate.sha256_bytes(diagnosis_bytes)
    registry_sha256 = release_gate.sha256_file(registry_path)
    ledger_sha256 = release_gate.sha256_file(ledger_path)
    # Emission must use the generation captured by the caller rather than
    # rereading these mutable paths after validation.
    registry_path.write_text('{"invariants":[]}\n', encoding="utf-8")
    ledger_path.write_text('{"defects":[]}\n', encoding="utf-8")
    inventory = release_gate.emit_outputs(
        output_dir=output,
        output_directory=release_gate.bind_existing_directory(output),
        validation_directory=release_gate.bind_existing_directory(
            output / "validation"
        ),
        semantic=semantic,
        receipt=receipt,
        registry=registry,
        ledger=ledger,
        registry_sha256=registry_sha256,
        ledger_sha256=ledger_sha256,
        measurements={},
        measurements_sha256=None,
        corrected_diagnosis_sha256=release_gate.sha256_file(
            tmp_path / "why_code_reviews_continue_to_find_major_problems.md"
        ),
        diff_hash="a" * 64,
        source_diagnosis_path="/evidence/diagnosis.md",
        source_diagnosis_sha256=diagnosis_sha256,
        source_diagnosis_bytes=diagnosis_bytes,
        expected_validation_hashes={
            "focused-001.output.txt": release_gate.sha256_file(
                output / "validation" / "focused-001.output.txt"
            )
        },
    )
    assert "priority0_consolidation_report.md" in inventory
    assert "priority0_consolidation_final_validation.json" in inventory
    assert "validation/focused-001.output.txt" in inventory
    assert "source_diagnosis_original.md" in inventory
    assert (output / "source_diagnosis_original.md").read_bytes() == diagnosis_bytes
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
    assert review["invariant_registry_sha256"] == registry_sha256
    assert review["defect_ledger_sha256"] == ledger_sha256
    assert review["source_diagnosis"] == {
        "path": "/evidence/diagnosis.md",
        "sha256": diagnosis_sha256,
        "packaged_path": "source_diagnosis_original.md",
        "packaged_sha256": diagnosis_sha256,
    }
    assert review["corrected_candidate_diagnosis"]["path"] == (
        "why_code_reviews_continue_to_find_major_problems.md"
    )
    actual_files = {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file()
    }
    assert set(inventory) == actual_files
    assert all(
        release_gate.sha256_file(output / relative) == digest
        for relative, digest in inventory.items()
    )
    inventory_document = json.loads(
        (output / "attestation_sha256_inventory.json").read_text(
            encoding="utf-8"
        )
    )
    assert inventory_document["files"] == {
        key: value
        for key, value in inventory.items()
        if key != "attestation_sha256_inventory.json"
    }


def test_failed_gate_preserves_bounded_failure_receipt(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    output = tmp_path / "attestation"
    (output / "validation").mkdir(parents=True)
    evidence = output / "validation" / "focused-001.output.txt"
    evidence.write_text("failed\n", encoding="utf-8")
    args = argparse.Namespace(
        command="run",
        repo=str(repo),
        output_dir=str(output),
        base="a" * 40,
        candidate="b" * 40,
        development_dry_run=False,
        full_suite=True,
        _gate_partial_validation_results=[
            {
                "command": ["python3", "-m", "pytest", "-q", "tests/test_x.py"],
                "exit_status": 1,
                "passed": 0,
                "failed": 1,
                "errors": 0,
                "skipped": 0,
            },
            {
                "label": "focused-002",
                "command": ["python3", "-m", "pytest", "-q", "tests/test_y.py"],
                "exit_status": 0,
                "passed": None,
                "failed": None,
                "errors": None,
                "skipped": None,
                "junit_capture_error": "missing JUnit",
            }
        ],
    )
    parent_stat = output.parent.stat()
    output_stat = output.stat()
    args._gate_path_authorization = release_gate.GatePathAuthorization(
        output_dir=output.absolute(),
        output_parent=output.parent.resolve(),
        output_parent_device=parent_stat.st_dev,
        output_parent_inode=parent_stat.st_ino,
        output_existed=True,
        output_device=output_stat.st_dev,
        output_inode=output_stat.st_ino,
    )
    path = release_gate.emit_failure_receipt(
        args, release_gate.ReleaseGateError("focused validation failed")
    )
    assert path == output / "release_gate_failure_receipt.json"
    receipt = json.loads(path.read_text(encoding="utf-8"))
    assert receipt["status"] == "blocked"
    assert receipt["error"] == "focused validation failed"
    assert receipt["failed_validations"] == [
        {
            "command": ["python3", "-m", "pytest", "-q", "tests/test_x.py"],
            "exit_status": 1,
            "passed": 0,
            "failed": 1,
            "errors": 0,
            "skipped": 0,
        },
        {
            "label": "focused-002",
            "command": ["python3", "-m", "pytest", "-q", "tests/test_y.py"],
            "exit_status": 0,
            "passed": None,
            "failed": None,
            "errors": None,
            "skipped": None,
            "junit_capture_error": "missing JUnit",
        },
    ]
    assert receipt["partial_validation_file_hashes"] == {
        "validation/focused-001.output.txt": release_gate.sha256_file(evidence)
    }


def test_failure_receipt_does_not_write_into_candidate_or_unknown_output(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    args = argparse.Namespace(
        command="run",
        repo=str(repo),
        output_dir=str(repo / "output"),
        base="a" * 40,
        candidate="b" * 40,
        development_dry_run=False,
        full_suite=True,
    )
    assert (
        release_gate.emit_failure_receipt(
            args, release_gate.ReleaseGateError("blocked")
        )
        is None
    )
    output = tmp_path / "existing"
    output.mkdir()
    (output / "foreign.txt").write_text("do not touch\n", encoding="utf-8")
    args.output_dir = str(output)
    assert (
        release_gate.emit_failure_receipt(
            args, release_gate.ReleaseGateError("blocked")
        )
        is None
    )
    assert sorted(path.name for path in output.iterdir()) == ["foreign.txt"]


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


def test_required_truth_audit_packet_correction_pin_fails_closed(
    tmp_path: Path,
) -> None:
    corrections = tmp_path / "historical_context_packet_corrections.json"
    corrections.write_text('{"corrections":[]}\n', encoding="utf-8")
    records = release_gate._declared_pin_records(
        tmp_path,
        "historical_context_evidence_truth_audit.json",
        "input_hashes",
        {
            "historical_context_packet_corrections.json": (
                release_gate.sha256_file(corrections)
            )
        },
    )
    assert len(records) == 1
    assert records[0]["match"] is True
    assert records[0]["runtime_relationship_required"] is True
    assert "fail closed" in records[0]["required_reason"]
    records = release_gate._declared_pin_records(
        tmp_path,
        "historical_context_evidence_truth_audit.json",
        "input_hashes",
        {"historical_context_packet_corrections.json": "0" * 64},
    )
    assert records[0]["match"] is False
    assert records[0]["runtime_relationship_required"] is True


def test_isolated_runner_ignores_candidate_and_home_toolchain_shadows(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    pytest_marker = tmp_path / "candidate-pytest-imported"
    user_marker = tmp_path / "usercustomize-imported"
    (candidate / "pytest.py").write_text(
        f"from pathlib import Path\nPath({str(pytest_marker)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    (candidate / "jsonschema.py").write_text(
        "raise RuntimeError('candidate jsonschema shadow loaded')\n",
        encoding="utf-8",
    )
    (candidate / "probe.py").write_text(
        "import jsonschema, pytest\n"
        "print(pytest.__file__)\n"
        "print(jsonschema.__file__)\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    user_site = home / ".local/lib/python3.10/site-packages"
    user_site.mkdir(parents=True)
    (user_site / "usercustomize.py").write_text(
        f"from pathlib import Path\nPath({str(user_marker)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    toolchain = release_gate.validation_toolchain_inventory(
        excluded_roots=(Path.cwd(), candidate)
    )
    command = release_gate.isolated_python_validation_command(
        (sys.executable, "probe.py"),
        candidate_root=candidate,
        toolchain=toolchain,
    )
    result = subprocess.run(
        command,
        cwd=candidate,
        env=release_gate.sanitized_validation_environment_for_toolchain(
            home, toolchain
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stdout
    assert not pytest_marker.exists()
    assert not user_marker.exists()
    assert str(candidate) not in result.stdout


def test_validation_evidence_hashes_detect_later_mutation(
    tmp_path: Path,
) -> None:
    validation = tmp_path / "validation"
    validation.mkdir()
    output = validation / "focused-001.output.txt"
    output.write_text("first\n", encoding="utf-8")
    evidence = release_gate.validation_evidence_hashes(
        validation, ["focused-001"]
    )
    release_gate.assert_validation_evidence_unchanged(evidence)
    output.write_text("changed\n", encoding="utf-8")
    with pytest.raises(
        release_gate.ReleaseGateError, match="sealed validation evidence"
    ):
        release_gate.assert_validation_evidence_unchanged(evidence)


@pytest.mark.parametrize(
    "label",
    ("validation evidence", "generated attestation output"),
)
def test_pre_emission_expected_inventory_rejects_replacement(
    label: str,
) -> None:
    with pytest.raises(
        release_gate.ReleaseGateError,
        match="pre-emission expected hashes",
    ):
        release_gate.require_expected_inventory(
            actual={"result.json": "a" * 64},
            expected={"result.json": "b" * 64},
            label=label,
        )


def test_gate_paths_reject_protected_overlap_and_allow_sibling_output(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path)
    production = tmp_path / "production"
    production.mkdir()
    dependency = tmp_path / "dependency"
    dependency.mkdir()
    output = tmp_path / "attestation"
    resolved_output, resolved_scratch, authorization = (
        release_gate.validate_gate_paths(
            repo=repo,
            output_argument=output,
            scratch_argument=tmp_path,
            production_root=production,
            dependency_roots=(dependency,),
        )
    )
    assert resolved_output == output
    assert resolved_scratch == tmp_path
    assert authorization.output_existed is False
    for unsafe_output in (
        repo / "attestation",
        production / "attestation",
        dependency / "attestation",
    ):
        with pytest.raises(
            release_gate.ReleaseGateError, match="output overlaps protected"
        ):
            release_gate.validate_gate_paths(
                repo=repo,
                output_argument=unsafe_output,
                scratch_argument=tmp_path,
                production_root=production,
                dependency_roots=(dependency,),
            )
    linked_output = tmp_path / "linked-output"
    linked_output.symlink_to(production, target_is_directory=True)
    with pytest.raises(release_gate.ReleaseGateError, match="symbolic link"):
        release_gate.validate_gate_paths(
            repo=repo,
            output_argument=linked_output,
            scratch_argument=tmp_path,
            production_root=production,
            dependency_roots=(dependency,),
        )
    unsafe_scratch = repo / "scratch"
    unsafe_scratch.mkdir()
    with pytest.raises(
        release_gate.ReleaseGateError, match="scratch root overlaps protected"
    ):
        release_gate.validate_gate_paths(
            repo=repo,
            output_argument=output,
            scratch_argument=unsafe_scratch,
            production_root=production,
            dependency_roots=(dependency,),
        )


def test_authorized_scratch_rejects_replacement_and_dirfd_creation_does_not_redirect(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path)
    production = tmp_path / "production"
    production.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    output = tmp_path / "attestation"
    _resolved, _scratch, authorization = release_gate.validate_gate_paths(
        repo=repo,
        output_argument=output,
        scratch_argument=scratch,
        production_root=production,
        dependency_roots=(),
    )
    bound = release_gate.bind_authorized_scratch(authorization)
    moved = tmp_path / "scratch-original"
    scratch.rename(moved)
    scratch.symlink_to(production, target_is_directory=True)
    with pytest.raises(
        release_gate.ReleaseGateError, match="scratch directory identity"
    ):
        release_gate.bind_authorized_scratch(authorization)
    with pytest.raises(release_gate.ReleaseGateError):
        release_gate.materialize_bound_temporary_subdirectory(
            bound, "validation-"
        )
    assert not any(production.iterdir())
    assert not any(moved.iterdir())


def test_source_diagnosis_stable_read_detects_identity_or_content_drift(
    tmp_path: Path,
) -> None:
    source = tmp_path / "diagnosis.md"
    source.write_bytes(b"original\n")
    payload, identity = release_gate.stable_file_bytes(source)
    release_gate.assert_stable_file(
        source, expected_bytes=payload, expected_identity=identity
    )
    source.write_bytes(b"changed!\n")
    with pytest.raises(
        release_gate.ReleaseGateError, match="required evidence changed"
    ):
        release_gate.assert_stable_file(
            source, expected_bytes=payload, expected_identity=identity
        )


def test_candidate_control_path_requires_tracked_candidate_file(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path)
    controls = repo / "controls"
    controls.mkdir()
    tracked = controls / "registry.json"
    tracked.write_text("{}\n", encoding="utf-8")
    external = tmp_path / "external.json"
    external.write_text("{}\n", encoding="utf-8")
    linked = controls / "linked.json"
    linked.symlink_to(external)
    _run(["git", "add", "controls"], repo)
    _run(["git", "commit", "-qm", "add control fixture"], repo)

    assert release_gate.candidate_control_path(
        repo, "controls/registry.json", label="registry"
    ) == tracked.resolve()
    with pytest.raises(release_gate.ReleaseGateError, match="relative candidate"):
        release_gate.candidate_control_path(
            repo, str(external), label="registry"
        )
    with pytest.raises(release_gate.ReleaseGateError, match="relative candidate"):
        release_gate.candidate_control_path(
            repo, "../external.json", label="registry"
        )
    untracked = repo / "untracked.json"
    untracked.write_text("{}\n", encoding="utf-8")
    with pytest.raises(release_gate.ReleaseGateError, match="not tracked"):
        release_gate.candidate_control_path(
            repo, "untracked.json", label="registry"
        )
    with pytest.raises(release_gate.ReleaseGateError, match="symbolic link"):
        release_gate.candidate_control_path(
            repo, "controls/linked.json", label="registry"
        )


def test_materialized_output_identity_rejects_removal_and_replacement(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path)
    production = tmp_path / "production"
    production.mkdir()
    output = tmp_path / "attestation"
    _resolved, _scratch, authorization = release_gate.validate_gate_paths(
        repo=repo,
        output_argument=output,
        scratch_argument=tmp_path,
        production_root=production,
        dependency_roots=(),
    )
    authorization = release_gate.materialize_gate_output(authorization)
    release_gate.assert_gate_output_authorized(output, authorization)

    moved = tmp_path / "attestation-original"
    output.rename(moved)
    with pytest.raises(release_gate.ReleaseGateError, match="disappeared"):
        release_gate.assert_gate_output_authorized(output, authorization)

    output.mkdir()
    with pytest.raises(release_gate.ReleaseGateError, match="identity changed"):
        release_gate.assert_gate_output_authorized(output, authorization)
    output.rmdir()
    output.symlink_to(tmp_path / "missing-target", target_is_directory=True)
    with pytest.raises(release_gate.ReleaseGateError, match="became unsafe"):
        release_gate.assert_gate_output_authorized(output, authorization)


def test_materialized_output_rechecks_identity_and_emptiness_through_fd(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path)
    production = tmp_path / "production"
    production.mkdir()
    output = tmp_path / "attestation"
    output.mkdir()
    _resolved, _scratch, authorization = release_gate.validate_gate_paths(
        repo=repo,
        output_argument=output,
        scratch_argument=tmp_path,
        production_root=production,
        dependency_roots=(),
    )
    (output / "foreign.txt").write_text("injected\n", encoding="utf-8")
    mode_before = output.stat().st_mode
    with pytest.raises(release_gate.ReleaseGateError, match="no longer empty"):
        release_gate.materialize_gate_output(authorization)
    assert output.stat().st_mode == mode_before
    assert (output / "foreign.txt").read_text(encoding="utf-8") == "injected\n"

    (output / "foreign.txt").unlink()
    original = tmp_path / "original-attestation"
    output.rename(original)
    output.mkdir()
    with pytest.raises(
        release_gate.ReleaseGateError, match="identity changed"
    ):
        release_gate.materialize_gate_output(authorization)


def test_materialized_output_does_not_replace_concurrent_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _git_repo(tmp_path)
    production = tmp_path / "production"
    production.mkdir()
    output = tmp_path / "attestation"
    _resolved, _scratch, authorization = release_gate.validate_gate_paths(
        repo=repo,
        output_argument=output,
        scratch_argument=tmp_path,
        production_root=production,
        dependency_roots=(),
    )
    original = release_gate.rename_noreplace_at
    competitor_identity: tuple[int, int] | None = None

    def race(
        source_fd: int, source: str, destination_fd: int, destination: str
    ) -> None:
        nonlocal competitor_identity
        output.mkdir()
        identity = output.stat()
        competitor_identity = (identity.st_dev, identity.st_ino)
        original(source_fd, source, destination_fd, destination)

    monkeypatch.setattr(release_gate, "rename_noreplace_at", race)
    with pytest.raises(release_gate.ReleaseGateError, match="exists"):
        release_gate.materialize_gate_output(authorization)
    assert output.is_dir()
    current = output.stat()
    assert competitor_identity == (current.st_dev, current.st_ino)
    assert not any(
        path.name.startswith(".mrs-release-gate-output-")
        for path in tmp_path.iterdir()
    )


def test_materialized_existing_output_rejects_open_time_identity_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _git_repo(tmp_path)
    production = tmp_path / "production"
    production.mkdir()
    output = tmp_path / "attestation"
    output.mkdir(mode=0o750)
    _resolved, _scratch, authorization = release_gate.validate_gate_paths(
        repo=repo,
        output_argument=output,
        scratch_argument=tmp_path,
        production_root=production,
        dependency_roots=(),
    )
    original_open = os.open
    moved = tmp_path / "attestation-original"
    replacement_identity: tuple[int, int, int] | None = None
    raced = False

    def swap_before_child_open(
        path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal raced, replacement_identity
        if path == output.name and dir_fd is not None and not raced:
            raced = True
            output.rename(moved)
            output.mkdir(mode=0o711)
            identity = output.stat()
            replacement_identity = (
                identity.st_dev,
                identity.st_ino,
                identity.st_mode,
            )
        if dir_fd is None:
            return original_open(path, flags, mode)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swap_before_child_open)
    with pytest.raises(
        release_gate.ReleaseGateError, match="identity changed while binding"
    ):
        release_gate.materialize_gate_output(authorization)
    current = output.stat()
    assert replacement_identity == (
        current.st_dev,
        current.st_ino,
        current.st_mode,
    )


def test_validation_directory_rejects_foreign_and_nonregular_entries(
    tmp_path: Path,
) -> None:
    validation = tmp_path / "validation"
    validation.mkdir()
    output = validation / "focused-001.output.txt"
    output.write_text("ok\n", encoding="utf-8")
    result = release_gate.ValidationResult(
        command=("python3", "-m", "compileall"),
        exit_status=0,
        passed=1,
        failed=0,
        errors=0,
        skipped=0,
        warnings=0,
        output_sha256=release_gate.sha256_file(output),
        duration_seconds=0,
    )
    bound = release_gate.bind_existing_directory(validation)
    release_gate.assert_validation_directory_exact(
        bound, focused=(result,), full=None
    )
    (validation / "foreign.txt").write_text("unexpected\n", encoding="utf-8")
    with pytest.raises(release_gate.ReleaseGateError, match="unexpected entries"):
        release_gate.assert_validation_directory_exact(
            bound, focused=(result,), full=None
        )
    (validation / "foreign.txt").unlink()
    output.unlink()
    output.symlink_to(tmp_path / "missing")
    with pytest.raises(
        release_gate.ReleaseGateError, match="not a regular file"
    ):
        release_gate.assert_validation_directory_exact(
            bound, focused=(result,), full=None
        )


def test_bound_directory_write_does_not_follow_replaced_path(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "evidence"
    directory.mkdir()
    bound = release_gate.bind_existing_directory(directory)
    moved = tmp_path / "evidence-original"
    directory.rename(moved)
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    directory.symlink_to(redirected, target_is_directory=True)

    with pytest.raises(release_gate.ReleaseGateError, match="identity changed"):
        release_gate.write_atomic_bound(bound, "result.txt", b"forbidden")
    assert not (redirected / "result.txt").exists()


def test_execute_validation_rejects_staging_path_replacement_before_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    evidence = output / "validation"
    evidence.mkdir()
    evidence_bound = release_gate.bind_existing_directory(evidence)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def replace_staging(
        args: object, **_kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        values = [str(value) for value in args]  # type: ignore[arg-type]
        junit_argument = next(
            value for value in values if value.startswith("--junitxml=")
        )
        junit = Path(junit_argument.split("=", 1)[1])
        staging = junit.parent
        moved = workspace / f"{staging.name}-moved"
        staging.rename(moved)
        staging.symlink_to(output, target_is_directory=True)
        return subprocess.CompletedProcess(values, 0, b"candidate output\n")

    monkeypatch.setattr(release_gate, "_run", replace_staging)
    with pytest.raises(
        release_gate.ReleaseGateError, match="bound output directory identity changed"
    ):
        release_gate.execute_validation(
            (sys.executable, "-m", "pytest", "-q"),
            cwd=candidate,
            evidence_directory=evidence_bound,
            workspace_root=workspace,
            label="focused-001",
            network_isolated=False,
            env={"PATH": os.environ.get("PATH", "")},
        )
    assert not (evidence / "focused-001.output.txt").exists()
    assert not (output / "result.junit.xml").exists()


def test_validation_home_creation_uses_bound_staging_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    original = release_gate.materialize_bound_subdirectory_at
    moved_staging: Path | None = None

    def replace_staging(
        parent: release_gate.BoundDirectory, parent_fd: int, name: str
    ) -> release_gate.BoundDirectory:
        nonlocal moved_staging
        if name != "home":
            return original(parent, parent_fd, name)
        moved = workspace / f"{parent.path.name}-moved"
        parent.path.rename(moved)
        parent.path.symlink_to(redirected, target_is_directory=True)
        moved_staging = moved
        return original(parent, parent_fd, name)

    monkeypatch.setattr(
        release_gate, "materialize_bound_subdirectory_at", replace_staging
    )
    with pytest.raises(release_gate.ReleaseGateError):
        release_gate.execute_validation(
            (sys.executable, "-m", "pytest", "-q"),
            cwd=candidate,
            evidence_directory=release_gate.bind_existing_directory(evidence),
            workspace_root=workspace,
            label="focused-001",
            network_isolated=False,
            env={"PATH": os.environ.get("PATH", "")},
        )
    assert not (redirected / "home").exists()
    assert moved_staging is not None
    assert (moved_staging / "home").is_dir()
    assert not any(evidence.iterdir())


@pytest.mark.parametrize(
    ("junit_payload", "exit_status"),
    [
        (None, 7),
        (b"<not-junit>", 7),
        (b"<unexpected/>", 0),
        (b"<testsuites/>", 0),
        (b"<testsuite/>", 0),
        (
            b'<testsuite tests="x" failures="0" errors="0" skipped="0"/>',
            0,
        ),
        (
            b'<testsuite tests="-1" failures="0" errors="0" skipped="0"/>',
            0,
        ),
        (
            b'<testsuite tests="1" failures="1" errors="1" skipped="0"/>',
            0,
        ),
        (
            b'<testsuite tests="1" failures="1" errors="0" skipped="0"/>',
            0,
        ),
    ],
)
def test_validation_crash_preserves_stdout_when_junit_is_missing_or_malformed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    junit_payload: bytes | None,
    exit_status: int,
) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def crash(
        args: object, **_kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        values = [str(value) for value in args]  # type: ignore[arg-type]
        if junit_payload is not None:
            junit_argument = next(
                value for value in values if value.startswith("--junitxml=")
            )
            Path(junit_argument.split("=", 1)[1]).write_bytes(junit_payload)
        return subprocess.CompletedProcess(
            values, exit_status, b"lost-marker\n"
        )

    monkeypatch.setattr(release_gate, "_run", crash)
    with pytest.raises(release_gate.ValidationCaptureError) as captured:
        release_gate.execute_validation(
            (sys.executable, "-m", "pytest", "-q"),
            cwd=candidate,
            evidence_directory=release_gate.bind_existing_directory(evidence),
            workspace_root=workspace,
            label="focused-001",
            network_isolated=False,
            env={"PATH": os.environ.get("PATH", "")},
        )
    partial = captured.value.partial_result
    assert partial["label"] == "focused-001"
    assert partial["exit_status"] == exit_status
    assert partial["junit_capture_error"]
    assert partial["output_sha256"] == release_gate.sha256_bytes(b"lost-marker\n")
    assert (evidence / "focused-001.output.txt").read_bytes() == b"lost-marker\n"
    assert not (evidence / "focused-001.junit.xml").exists()


def test_contained_validation_cannot_replace_bound_evidence_directory(
    tmp_path: Path,
) -> None:
    candidate = _git_repo(tmp_path)
    production = tmp_path / "production"
    production.mkdir()
    output = tmp_path / "attestation"
    output.mkdir()
    evidence = output / "validation"
    evidence.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    preflight = release_gate.containment_preflight(
        cwd=candidate,
        production_root=production,
        candidate_root=candidate,
        additional_read_only_paths=(output,),
    )
    if not preflight["available"]:
        pytest.skip(f"OS containment unavailable: {preflight['reason']}")
    script = (
        "import os, pathlib, sys\n"
        "source=pathlib.Path(sys.argv[1])\n"
        "target=source.parent/'hijacked'\n"
        "try:\n"
        " os.rename(source,target)\n"
        "except OSError:\n"
        " print('rename denied')\n"
        "else:\n"
        " print('rename unexpectedly succeeded')\n"
        " raise SystemExit(91)\n"
    )
    result = release_gate.execute_validation(
        (sys.executable, "-c", script, str(evidence)),
        cwd=candidate,
        evidence_directory=release_gate.bind_existing_directory(evidence),
        workspace_root=workspace,
        label="focused-001",
        network_isolated=True,
        production_root=production,
        candidate_root=candidate,
        additional_read_only_paths=(output,),
        env={"PATH": os.environ.get("PATH", "")},
    )
    assert result.exit_status == 0
    assert evidence.is_dir() and not evidence.is_symlink()
    assert not (output / "hijacked").exists()
    assert (evidence / "focused-001.output.txt").read_text(
        encoding="utf-8"
    ) == "rename denied\n"


def test_main_converts_filesystem_failure_to_bounded_gate_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[release_gate.ReleaseGateError] = []

    def fail(_args: argparse.Namespace) -> int:
        raise PermissionError("denied")

    def record(
        _args: argparse.Namespace, error: release_gate.ReleaseGateError
    ) -> None:
        captured.append(error)

    monkeypatch.setattr(release_gate, "run_gate", fail)
    monkeypatch.setattr(release_gate, "emit_failure_receipt", record)
    result = release_gate.main(
        [
            "run",
            "--repo",
            str(tmp_path),
            "--base",
            "a" * 40,
            "--output-dir",
            str(tmp_path / "output"),
            "--source-diagnosis-path",
            str(tmp_path / "diagnosis.md"),
            "--source-diagnosis-sha256",
            "b" * 64,
            "--development-dry-run",
        ]
    )
    assert result == 2
    assert len(captured) == 1
    assert "PermissionError: denied" in str(captured[0])
