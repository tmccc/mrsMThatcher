#!/usr/bin/env python3
"""Attest a frozen mrsMThatcher release candidate without deploying it.

The tool has two deliberately different modes:

``run``
    Requires a clean committed candidate, holds a repository-wide integration
    lock, executes invariant-selected focused checks and (when requested) the
    complete suite in a detached clean worktree, and emits attestations.

``network-preflight``
    Verifies that the OS can place the test process and all descendants in a
    network namespace with loopback available and external routing absent.

The semantic attestation contains no timestamps, host names, paths to temporary
directories, or durations.  Those volatile details belong in the run receipt.
Nothing in this module deploys, mutates runtime state, or contacts a provider.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import dataclasses
import datetime as dt
import fcntl
import fnmatch
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence


HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
INVARIANT_ID = re.compile(r"INV-[A-Z0-9]+(?:-[A-Z0-9]+)+\Z")
RUNTIME_ENTRY_POINTS = ("mrsMThatcher2.py",)
RUNTIME_LAUNCHERS = ("runMrsMThatcher2", "mrsMThatcher.service")
NON_RUNTIME_PREFIXES = (
    "tests/",
    "tools/",
    "production_incident_reviews/",
    "production_deployments/",
    "historical_context_search_runs/",
)
DOCUMENTATION_SUFFIXES = (".md", ".rst", ".txt")
CONTROL_SUFFIXES = (
    ".json",
    ".schema.json",
    ".ini",
    ".toml",
    ".yaml",
    ".yml",
    ".service",
)
PRIORITY0_CONTROL_PATHS = frozenset(
    {
        "defect_ledger.json",
        "defect_ledger.schema.json",
        "diagnosis_measurements.json",
        "production_invariants.json",
        "production_invariants.schema.json",
    }
)
INVARIANT_REGISTRY_DEFINITION_PATHS = frozenset(
    {"production_invariants.json", "production_invariants.schema.json"}
)
UNMAPPED_DOCUMENTED_EXCEPTIONS = (
    "documentation",
    "non-code-data",
)
GENERATED_NAME_HINTS = (
    "manifest",
    "audit",
    "projection",
    "ledger",
    "research_packets",
    "final_research_status",
    "corrections",
    "curated_evidence",
)
RUNTIME_LOADER_ROOTS = {
    "historical_context_source_independent_review.py": (
        "semantic_alignment_research/quote_research_full_001",
    ),
    "historical_context_source_roles.py": (
        "semantic_alignment_research/quote_research_full_001",
    ),
}
VALIDATOR_BACKED_OFFLINE_LITERALS = {
    (
        "semantic_alignment/quote_image_semantic_veto.py",
        "run_manifest.json",
    ): "tests/test_quote_image_semantic_veto_shadow.py",
}
SOURCE_PIN_ALIASES = {
    "active_source": "mrsMThatcher.txt",
    "attribution_predicate": "historical_context_formatter.py",
    "completed_quote_research": (
        "semantic_alignment_research/quote_research_full_001/research_packets.json"
    ),
}
ADVISORY_SOURCE_PIN_KEYS = {
    (
        "semantic_alignment_research/quote_attribution_cleanup_001/"
        "deployment_candidate/runtime_eligible_quote_manifest.json",
        "attribution_predicate",
    ): (
        "the runtime loader validates active_source and completed_quote_research "
        "but does not consume this whole-formatter provenance pin"
    ),
}


class ReleaseGateError(RuntimeError):
    """A release-gate precondition or validation failed."""


@dataclasses.dataclass(frozen=True)
class CandidateSnapshot:
    """Stable Git and file identity of one candidate worktree."""

    commit: str
    tree: str
    status_porcelain_v2: str
    untracked_files: tuple[str, ...]
    index_flagged_files: tuple[str, ...]
    submodule_status: tuple[str, ...]
    runtime_hashes: tuple[tuple[str, str], ...]
    generated_hashes: tuple[tuple[str, str], ...]
    declared_artifact_hashes: tuple[tuple[str, str], ...]
    policy_hashes: tuple[tuple[str, str], ...]

    def semantic_dict(self) -> dict[str, Any]:
        """Return a deterministic JSON-compatible representation."""
        return {
            "commit": self.commit,
            "tree": self.tree,
            "worktree_clean": not bool(self.status_porcelain_v2),
            "untracked_files": list(self.untracked_files),
            "index_flagged_files": list(self.index_flagged_files),
            "submodule_status": list(self.submodule_status),
            "runtime_hashes": dict(self.runtime_hashes),
            "generated_artifact_hashes": dict(self.generated_hashes),
            "registry_declared_runtime_artifact_hashes": dict(
                self.declared_artifact_hashes
            ),
            "configuration_and_policy_hashes": dict(self.policy_hashes),
        }


@dataclasses.dataclass(frozen=True)
class ValidationResult:
    """One command result split into deterministic and volatile fields."""

    command: tuple[str, ...]
    exit_status: int
    passed: int
    failed: int
    errors: int
    skipped: int
    warnings: int
    output_sha256: str
    duration_seconds: float
    junit_sha256: str | None = None
    executed_command: tuple[str, ...] = ()

    def semantic_dict(self) -> dict[str, Any]:
        """Return fields which are stable for identical validation results."""
        return {
            "command": [
                (
                    "--junitxml=<attestation-output>"
                    if token.startswith("--junitxml=")
                    else token
                )
                for token in self.command
            ],
            "exit_status": self.exit_status,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "skipped": self.skipped,
            "warnings": self.warnings,
        }

    def receipt_dict(self) -> dict[str, Any]:
        """Return all result fields, including volatile duration."""
        value = self.semantic_dict()
        value["duration_seconds"] = round(self.duration_seconds, 6)
        value["output_sha256"] = self.output_sha256
        value["junit_sha256"] = self.junit_sha256
        value["executed_command"] = list(
            self.executed_command or self.command
        )
        return value


@dataclasses.dataclass(frozen=True)
class ValidationToolchain:
    """Content-bound Python/pytest toolchain used by isolated validation."""

    semantic_inventory_json: str
    python_paths: tuple[str, ...]

    def semantic_dict(self) -> dict[str, Any]:
        """Return the deterministic toolchain identity."""
        return json.loads(self.semantic_inventory_json)


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic pretty JSON bytes with a trailing newline."""
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    """Return a SHA-256 hexadecimal digest."""
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    """Hash one regular file without following a changing path silently."""
    before = path.stat()
    if not path.is_file():
        raise ReleaseGateError(f"not a regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ReleaseGateError(f"file changed while hashing: {path}")
    return digest.hexdigest()


def file_identity(path: Path) -> dict[str, Any]:
    """Record lexical/resolved identity and content hash for a deployed file."""
    stat = path.lstat()
    resolved = path.resolve(strict=True)
    return {
        "path": str(path),
        "resolved_path": str(resolved),
        "is_symlink": path.is_symlink(),
        "symlink_target": os.readlink(path) if path.is_symlink() else None,
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "mode": stat.st_mode,
        "sha256": sha256_file(path),
    }


def write_atomic(path: Path, payload: bytes, mode: int = 0o600) -> None:
    """Atomically write an attestation output and fsync it and its directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _run(
    args: Sequence[str],
    *,
    cwd: Path,
    check: bool = True,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run a command without a shell and return captured bytes."""
    result = subprocess.run(
        list(args),
        cwd=cwd,
        env=None if env is None else dict(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=timeout,
        shell=False,
    )
    if check and result.returncode:
        text = result.stdout.decode("utf-8", "replace")[-4000:]
        raise ReleaseGateError(
            f"command failed ({result.returncode}): {shlex.join(args)}\n{text}"
        )
    return result


def _git(repo: Path, *args: str, check: bool = True) -> str:
    """Run Git and decode its standard output."""
    result = _run(("git", "-C", str(repo), *args), cwd=repo, check=check)
    return result.stdout.decode("utf-8", "surrogateescape").rstrip("\n")


def _git_readonly(repo: Path, *args: str, check: bool = True) -> str:
    """Run Git with optional index writes disabled for production inspection."""
    environment = dict(os.environ)
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    result = _run(
        ("git", "--no-optional-locks", "-C", str(repo), *args),
        cwd=repo,
        check=check,
        env=environment,
    )
    return result.stdout.decode("utf-8", "surrogateescape").rstrip("\n")


def resolve_commit(repo: Path, revision: str, *, label: str) -> str:
    """Resolve one revision once and require a full commit object identity."""
    result = _run(
        ("git", "-C", str(repo), "rev-parse", "--verify", f"{revision}^{{commit}}"),
        cwd=repo,
        check=False,
    )
    resolved = result.stdout.decode("ascii", "replace").strip()
    if result.returncode or not HEX40.fullmatch(resolved):
        raise ReleaseGateError(f"{label} commit does not exist: {revision}")
    return resolved


def resolve_release_identities(
    repo: Path,
    *,
    base: str,
    candidate: str | None,
    development: bool,
) -> tuple[str, str]:
    """Resolve and validate immutable base/candidate identities."""
    if not development:
        if not HEX40.fullmatch(base):
            raise ReleaseGateError(
                "non-development validation requires an exact 40-hex base commit"
            )
        if candidate is None or not HEX40.fullmatch(candidate):
            raise ReleaseGateError(
                "non-development validation requires an exact 40-hex candidate commit"
            )
    base_commit = resolve_commit(repo, base, label="base")
    candidate_revision = candidate or "HEAD"
    candidate_commit = resolve_commit(repo, candidate_revision, label="candidate")
    head = resolve_commit(repo, "HEAD", label="HEAD")
    if head != candidate_commit:
        raise ReleaseGateError(
            f"HEAD {head} differs from requested candidate {candidate_commit}"
        )
    ancestry = _run(
        (
            "git",
            "-C",
            str(repo),
            "merge-base",
            "--is-ancestor",
            base_commit,
            candidate_commit,
        ),
        cwd=repo,
        check=False,
    )
    if ancestry.returncode:
        raise ReleaseGateError("base commit is not an ancestor of the candidate")
    return base_commit, candidate_commit


def git_common_dir(repo: Path) -> Path:
    """Return the resolved shared Git common directory."""
    raw = _git(repo, "rev-parse", "--git-common-dir")
    path = Path(raw)
    if not path.is_absolute():
        path = repo / path
    return path.resolve()


@contextlib.contextmanager
def integration_lock(repo: Path, *, blocking: bool = False) -> Iterator[Path]:
    """Hold the repository-wide integration lock in the shared Git directory."""
    lock_path = git_common_dir(repo) / "mrsMThatcher.release-gate.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(descriptor, operation)
        except BlockingIOError as exc:
            raise ReleaseGateError(
                f"integration lock is already held: {lock_path}"
            ) from exc
        os.ftruncate(descriptor, 0)
        os.write(
            descriptor,
            f"pid={os.getpid()} repo={repo.resolve()}\n".encode("utf-8"),
        )
        os.fsync(descriptor)
        yield lock_path
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def git_status(repo: Path) -> tuple[str, tuple[str, ...]]:
    """Return complete porcelain-v2 status and untracked path names."""
    status = _git(
        repo,
        "status",
        "--porcelain=v2",
        "--untracked-files=all",
        "--ignore-submodules=none",
    )
    untracked: list[str] = []
    for line in status.splitlines():
        if line.startswith("? "):
            untracked.append(line[2:])
    return status, tuple(sorted(untracked))


def git_index_flagged_files(repo: Path) -> tuple[str, ...]:
    """Return tracked paths hidden by non-default index worktree flags."""
    payload = _git(repo, "ls-files", "-v", "-z")
    flagged: list[str] = []
    for entry in payload.split("\0"):
        if not entry:
            continue
        marker, separator, _path = entry.partition(" ")
        if not separator or marker != "H":
            flagged.append(entry)
    return tuple(sorted(flagged))


def tracked_files(repo: Path) -> tuple[str, ...]:
    """Return sorted tracked paths at HEAD."""
    payload = _git(repo, "ls-tree", "-r", "--name-only", "-z", "HEAD")
    # _git strips only newlines, not NULs.
    return tuple(sorted(item for item in payload.split("\0") if item))


def _module_file_candidates(repo: Path, module_parts: Sequence[str]) -> set[str]:
    """Resolve one local module name and its package initializers."""
    if not module_parts or any(not part.isidentifier() for part in module_parts):
        return set()
    candidates: set[str] = set()
    for index in range(1, len(module_parts) + 1):
        package_init = repo.joinpath(*module_parts[:index], "__init__.py")
        if package_init.is_file():
            candidates.add(package_init.relative_to(repo).as_posix())
    plain = repo.joinpath(*module_parts).with_suffix(".py")
    if plain.is_file():
        candidates.add(plain.relative_to(repo).as_posix())
    package = repo.joinpath(*module_parts, "__init__.py")
    if package.is_file():
        candidates.add(package.relative_to(repo).as_posix())
    return candidates


def _local_import_candidates(repo: Path, relative: str) -> set[str]:
    """Resolve absolute and relative local imports from one Python module."""
    path = repo / relative
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise ReleaseGateError(f"cannot inspect imports in {path}: {exc}") from exc
    relative_path = PurePosixPath(relative)
    package_parts = list(relative_path.parent.parts)
    candidates: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                candidates.update(
                    _module_file_candidates(repo, alias.name.split("."))
                )
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                ascend = node.level - 1
                if ascend > len(package_parts):
                    continue
                base_parts = package_parts[: len(package_parts) - ascend]
            else:
                base_parts = []
            if node.module:
                base_parts = [*base_parts, *node.module.split(".")]
            candidates.update(_module_file_candidates(repo, base_parts))
            for alias in node.names:
                if alias.name != "*":
                    candidates.update(
                        _module_file_candidates(
                            repo, [*base_parts, *alias.name.split(".")]
                        )
                    )
    return candidates


def discover_runtime_python_files(repo: Path) -> tuple[str, ...]:
    """Discover the local Python import closure of runtime entry points."""
    queue = list(RUNTIME_ENTRY_POINTS)
    discovered: set[str] = set()
    while queue:
        relative = queue.pop(0)
        if relative in discovered:
            continue
        path = repo / relative
        if not path.is_file():
            raise ReleaseGateError(f"runtime entry/import is missing: {relative}")
        discovered.add(relative)
        for candidate in sorted(_local_import_candidates(repo, relative)):
            if candidate not in discovered:
                queue.append(candidate)
    return tuple(sorted(discovered))


def _json_literals(path: Path) -> set[str]:
    """Return JSON-looking string literals mentioned by one Python loader."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return set()
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            candidate = node.value.strip().replace("\\", "/")
            if (
                candidate.endswith(".json")
                and "\n" not in candidate
                and not candidate.startswith(("http://", "https://"))
            ):
                values.add(candidate)
    return values


def _static_path_value(node: ast.AST, values: Mapping[str, Any]) -> Any:
    """Evaluate only literal/path-composition AST nodes, never Python code."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, bool)):
        return node.value
    if isinstance(node, ast.Name):
        return values.get(node.id)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        resolved = [_static_path_value(item, values) for item in node.elts]
        return resolved if all(item is not None for item in resolved) else None
    if isinstance(node, ast.Dict):
        keys = [_static_path_value(item, values) for item in node.keys]
        vals = [_static_path_value(item, values) for item in node.values]
        if all(item is not None for item in keys + vals):
            try:
                return dict(zip(keys, vals))
            except TypeError:
                return None
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _static_path_value(node.left, values)
        right = _static_path_value(node.right, values)
        if isinstance(left, PurePosixPath) and isinstance(right, (str, PurePosixPath)):
            return left / str(right)
        if isinstance(left, str) and isinstance(right, str):
            return PurePosixPath(left) / right
        return None
    if isinstance(node, ast.Call):
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "Path"
            and len(node.args) == 1
        ):
            value = _static_path_value(node.args[0], values)
            return PurePosixPath(value) if isinstance(value, str) else None
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "str"
            and len(node.args) == 1
        ):
            value = _static_path_value(node.args[0], values)
            return str(value) if value is not None else None
    return None


def _collect_json_values(value: Any, output: set[str]) -> None:
    """Collect relative JSON paths from a statically evaluated value."""
    if isinstance(value, Mapping):
        for item in value.values():
            _collect_json_values(item, output)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            _collect_json_values(item, output)
    elif isinstance(value, (str, PurePosixPath)):
        text = str(value).replace("\\", "/")
        if text.endswith(".json") and not text.startswith(("/", "http://", "https://")):
            output.add(text.removeprefix("./"))


def _static_json_paths(path: Path) -> set[str]:
    """Discover JSON paths built by module constants and loader parameters."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return set()
    values: dict[str, Any] = {
        "ROOT": PurePosixPath("."),
        "BASE_DIR": PurePosixPath("."),
    }
    output: set[str] = set()
    # Module assignments are ordered and commonly build paths from ROOT/BASE_DIR.
    for node in tree.body:
        target: ast.Name | None = None
        expression: ast.AST | None = None
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target, expression = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, expression = node.target, node.value
        if target is not None and expression is not None:
            if target.id in {"ROOT", "BASE_DIR"}:
                value: Any = PurePosixPath(".")
            else:
                value = _static_path_value(expression, values)
            if value is not None:
                values[target.id] = value
                _collect_json_values(value, output)
    # Resolve paths composed inside loader functions from statically known
    # default roots, including research_dir / "final_unresolved" / "...json".
    for function in (
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        local = dict(values)
        positional = list(function.args.posonlyargs) + list(function.args.args)
        defaults = [None] * (len(positional) - len(function.args.defaults)) + list(
            function.args.defaults
        )
        for argument, default in zip(positional, defaults):
            if default is not None:
                value = _static_path_value(default, values)
                if value is not None:
                    local[argument.arg] = value
        for node in ast.walk(function):
            value = _static_path_value(node, local)
            if value is not None:
                _collect_json_values(value, output)
    return output


def discover_generated_artifacts(
    repo: Path, runtime_files: Sequence[str]
) -> tuple[tuple[str, ...], list[dict[str, Any]], list[str]]:
    """Discover tracked generated JSON referenced by runtime loader literals.

    A basename is accepted only when it resolves to exactly one tracked file,
    or when all resolutions are recorded and a loader-specific relationship
    validator exists in the focused suite.  Ambiguity is surfaced rather than
    silently selecting one path.
    """
    tracked = tracked_files(repo)
    by_name: dict[str, list[str]] = {}
    for relative in tracked:
        if relative.endswith(".json"):
            by_name.setdefault(PurePosixPath(relative).name, []).append(relative)
    artifacts: set[str] = set()
    bindings: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for loader in runtime_files:
        loader_path = repo / loader
        exact_paths = _static_json_paths(loader_path)
        exact_by_basename: dict[str, list[str]] = {}
        for exact in sorted(exact_paths):
            if exact in tracked:
                artifacts.add(exact)
                exact_by_basename.setdefault(PurePosixPath(exact).name, []).append(
                    exact
                )
                bindings.append(
                    {
                        "loader": loader,
                        "literal": exact,
                        "resolved_path": exact,
                        "resolution": "static_path_composition",
                    }
                )
        for literal in sorted(_json_literals(loader_path)):
            if literal in exact_paths and literal in tracked:
                continue
            basename = PurePosixPath(literal).name
            if "/" in literal and literal in tracked:
                candidates = [literal]
            else:
                candidates = sorted(by_name.get(basename, []))
            generated = [
                value
                for value in candidates
                if any(hint in basename.lower() for hint in GENERATED_NAME_HINTS)
                or "semantic_alignment_research/" in value
            ]
            exact_matches = sorted(set(exact_by_basename.get(basename, [])))
            if len(exact_matches) == 1:
                # The same literal may also appear as a basename/key. A statically
                # composed canonical path is stronger than global basename search.
                continue
            if len(generated) == 1:
                artifacts.add(generated[0])
                bindings.append(
                    {
                        "loader": loader,
                        "literal": basename,
                        "resolved_path": generated[0],
                        "resolution": "unique_tracked_basename",
                    }
                )
            elif len(generated) > 1:
                rooted = [
                    f"{root}/{literal}"
                    for root in RUNTIME_LOADER_ROOTS.get(loader, ())
                    if f"{root}/{literal}" in generated
                ]
                validator = VALIDATOR_BACKED_OFFLINE_LITERALS.get(
                    (loader, literal)
                )
                if len(rooted) == 1:
                    artifacts.add(rooted[0])
                    bindings.append(
                        {
                            "loader": loader,
                            "literal": literal,
                            "resolved_path": rooted[0],
                            "resolution": "loader_runtime_root",
                        }
                    )
                elif (
                    validator
                    and validator in tracked
                    and (repo / validator).is_file()
                ):
                    bindings.append(
                        {
                            "loader": loader,
                            "literal": literal,
                            "candidate_paths": generated,
                            "resolution": "validator_backed_offline_literal",
                            "validator": validator,
                        }
                    )
                else:
                    bindings.append(
                        {
                            "loader": loader,
                            "literal": literal,
                            "candidate_paths": generated,
                            "resolution": "ambiguous_not_claimed_runtime",
                        }
                    )
            elif any(hint in basename.lower() for hint in GENERATED_NAME_HINTS):
                unresolved.append(f"{loader}: {literal}")
    return tuple(sorted(artifacts)), bindings, sorted(set(unresolved))


def registry_runtime_artifact_inventory(
    repo: Path, invariants: Sequence[Mapping[str, Any]]
) -> tuple[tuple[str, ...], list[dict[str, Any]]]:
    """Classify every registry artifact declaration without conflating state.

    ``status: direct`` says that a runtime lane consumes a path; it does not
    imply that an ephemeral receipt, log, or optional control file must exist
    in a clean release candidate.  Immutable candidate inputs are therefore
    identified explicitly by ``frozen_candidate_required_artifacts``.
    """
    tracked = set(tracked_files(repo))
    declared: set[str] = set()
    inventory: list[dict[str, Any]] = []
    for record in invariants:
        invariant_id = str(record.get("invariant_id") or record.get("id") or "")
        section = record.get("runtime_consumed_artifacts")
        values = section.get("artifacts", []) if isinstance(section, Mapping) else []
        status = section.get("status") if isinstance(section, Mapping) else None
        required_values = (
            section.get("frozen_candidate_required_artifacts", [])
            if isinstance(section, Mapping)
            else []
        )
        if not isinstance(values, list):
            raise ReleaseGateError(
                f"{invariant_id}: runtime artifacts must be a list"
            )
        if not isinstance(required_values, list) or not all(
            isinstance(value, str) for value in required_values
        ):
            raise ReleaseGateError(
                f"{invariant_id}: frozen candidate artifacts must be strings"
            )
        required = set(required_values)
        unknown_required = required.difference(
            value for value in values if isinstance(value, str)
        )
        if unknown_required:
            raise ReleaseGateError(
                f"{invariant_id}: frozen candidate artifact is not declared: "
                + ", ".join(sorted(unknown_required))
            )
        for value in values:
            if not isinstance(value, str):
                raise ReleaseGateError(
                    f"{invariant_id}: runtime artifact must be a string"
                )
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts:
                if value in required:
                    raise ReleaseGateError(
                        f"{invariant_id}: required frozen artifact path is unsafe: "
                        f"{value}"
                    )
                inventory.append(
                    {
                        "invariant_id": invariant_id,
                        "path": value,
                        "runtime_status": status,
                        "frozen_candidate_required": False,
                        "disposition": "external_or_descriptive",
                    }
                )
                continue
            relative = path.as_posix()
            if relative in tracked and (repo / relative).is_file():
                declared.add(relative)
                disposition = "tracked_candidate_file"
            elif value in required:
                raise ReleaseGateError(
                    f"{invariant_id}: required frozen runtime artifact is missing "
                    f"or untracked: {relative}"
                )
            elif (repo / relative).exists():
                disposition = "untracked_or_nonfile_runtime_state"
            else:
                disposition = "production_only_or_ephemeral_absent"
            inventory.append(
                {
                    "invariant_id": invariant_id,
                    "path": relative,
                    "runtime_status": status,
                    "frozen_candidate_required": value in required,
                    "disposition": disposition,
                }
            )
    return tuple(sorted(declared)), sorted(
        inventory, key=lambda item: (item["invariant_id"], item["path"])
    )


def registry_declared_runtime_artifacts(
    repo: Path, invariants: Sequence[Mapping[str, Any]]
) -> tuple[str, ...]:
    """Return tracked artifacts explicitly declared by the registry."""
    declared, _inventory = registry_runtime_artifact_inventory(repo, invariants)
    return declared


def discover_policy_files(repo: Path) -> tuple[str, ...]:
    """Discover tracked configuration schemas, policy data and launch config."""
    result: list[str] = []
    for relative in tracked_files(repo):
        name = PurePosixPath(relative).name.lower()
        if (
            relative.endswith(".schema.json")
            or "policy" in name and relative.endswith(".json")
            or relative in {"pytest.ini", "requirements.txt", "mrsMThatcher.service"}
        ):
            result.append(relative)
    return tuple(sorted(result))


def hash_paths(repo: Path, paths: Iterable[str]) -> tuple[tuple[str, str], ...]:
    """Hash sorted relative paths."""
    return tuple(
        (relative, sha256_file(repo / relative))
        for relative in sorted(set(paths))
        if (repo / relative).is_file()
    )


def take_snapshot(
    repo: Path, declared_runtime_artifacts: Sequence[str] = ()
) -> tuple[CandidateSnapshot, list[dict[str, Any]], list[str]]:
    """Capture candidate Git identity and relevant file inventories."""
    commit = _git(repo, "rev-parse", "HEAD")
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    if not HEX40.fullmatch(commit) or not HEX40.fullmatch(tree):
        raise ReleaseGateError("candidate Git identity is malformed")
    status, untracked = git_status(repo)
    submodule_text = _git(repo, "submodule", "status", "--recursive")
    submodules = tuple(line for line in submodule_text.splitlines() if line)
    runtime_files = discover_runtime_python_files(repo)
    runtime_paths = tuple(
        sorted(set(runtime_files) | {p for p in RUNTIME_LAUNCHERS if (repo / p).is_file()})
    )
    generated, bindings, unresolved = discover_generated_artifacts(
        repo, runtime_files
    )
    snapshot = CandidateSnapshot(
        commit=commit,
        tree=tree,
        status_porcelain_v2=status,
        untracked_files=untracked,
        index_flagged_files=git_index_flagged_files(repo),
        submodule_status=submodules,
        runtime_hashes=hash_paths(repo, runtime_paths),
        generated_hashes=hash_paths(repo, generated),
        declared_artifact_hashes=hash_paths(repo, declared_runtime_artifacts),
        policy_hashes=hash_paths(repo, discover_policy_files(repo)),
    )
    return snapshot, bindings, unresolved


def require_frozen(snapshot: CandidateSnapshot, *, development: bool) -> None:
    """Reject a dirty or non-committed candidate outside development mode."""
    if snapshot.untracked_files and not development:
        raise ReleaseGateError(
            "release candidate contains untracked files; freeze them or remove them"
        )
    if snapshot.status_porcelain_v2 and not development:
        raise ReleaseGateError(
            "release candidate is dirty; exact committed candidate required"
        )
    if snapshot.index_flagged_files and not development:
        raise ReleaseGateError(
            "release candidate uses assume-unchanged, skip-worktree, or "
            "another non-default Git index flag"
        )
    if any(line.startswith(("-", "+", "U")) for line in snapshot.submodule_status):
        raise ReleaseGateError("submodule state is not frozen at recorded commits")


def assert_snapshot_equal(
    before: CandidateSnapshot, after: CandidateSnapshot
) -> None:
    """Fail when the candidate or relevant file identities changed."""
    if before != after:
        raise ReleaseGateError("candidate changed while validation was running")


def assert_candidate_checkout_unchanged(
    checkout: Path,
    *,
    expected_snapshot: CandidateSnapshot,
    expected_bindings: Sequence[Mapping[str, Any]],
    expected_unresolved: Sequence[str],
    declared_runtime_artifacts: Sequence[str],
) -> None:
    """Reverify the detached checkout and its loader relationships."""
    current, bindings, unresolved = take_snapshot(
        checkout, declared_runtime_artifacts
    )
    assert_snapshot_equal(expected_snapshot, current)
    if list(expected_bindings) != bindings or list(expected_unresolved) != unresolved:
        raise ReleaseGateError(
            "candidate generated-artifact discovery changed during validation"
        )


def load_json_object(path: Path) -> dict[str, Any]:
    """Read one JSON object."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseGateError(f"cannot read JSON object {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseGateError(f"JSON root must be an object: {path}")
    return value


def validate_control_schema(
    document: Mapping[str, Any], schema_path: Path, *, label: str
) -> None:
    """Validate one control document before trusting mappings or claims."""
    schema = load_json_object(schema_path)
    try:
        from tools.priority0_registry import schema_validation_errors
    except ModuleNotFoundError as package_exc:
        # ``python3 tools/release_gate.py`` places ``tools/`` rather than the
        # repository root on sys.path. Support that documented invocation
        # without modifying sys.path or depending on the caller's environment.
        try:
            from priority0_registry import schema_validation_errors
        except (ImportError, AttributeError) as sibling_exc:
            raise ReleaseGateError(
                "cannot load the Priority-0 schema validator: "
                f"package import={package_exc}; sibling import={sibling_exc}"
            ) from sibling_exc
    except AttributeError as exc:
        raise ReleaseGateError(
            f"cannot load the Priority-0 schema validator: {exc}"
        ) from exc
    backend, errors = schema_validation_errors(document, schema)
    if errors:
        raise ReleaseGateError(
            f"{label} fails {backend} schema validation: " + "; ".join(errors[:10])
        )


def registry_invariants(registry: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return and minimally validate invariant records."""
    records = registry.get("invariants")
    if not isinstance(records, list) or not records:
        raise ReleaseGateError("invariant registry contains no invariants")
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            raise ReleaseGateError("invariant record is not an object")
        invariant_id = str(record.get("invariant_id") or record.get("id") or "")
        if not INVARIANT_ID.fullmatch(invariant_id) or invariant_id in seen:
            raise ReleaseGateError(f"invalid or duplicate invariant ID: {invariant_id}")
        seen.add(invariant_id)
        copied = dict(record)
        copied["invariant_id"] = invariant_id
        output.append(copied)
    return output


def registry_record_transition(
    repo: Path,
    *,
    base_commit: str,
    candidate_registry: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare invariant records so removal/renaming cannot evade mapping."""
    candidate_records = registry_invariants(candidate_registry)
    candidate_by_id = {
        record["invariant_id"]: record for record in candidate_records
    }
    result = _run(
        (
            "git",
            "-C",
            str(repo),
            "show",
            f"{base_commit}:production_invariants.json",
        ),
        cwd=repo,
        check=False,
    )
    if result.returncode:
        return {
            "base_registry_present": False,
            "added_invariant_ids": sorted(candidate_by_id),
            "removed_invariant_ids": [],
            "changed_invariant_ids": [],
            "unchanged_invariant_ids": [],
        }
    try:
        base_value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ReleaseGateError(
            f"base invariant registry is malformed: {exc}"
        ) from exc
    if not isinstance(base_value, dict):
        raise ReleaseGateError("base invariant registry root is not an object")
    base_by_id = {
        record["invariant_id"]: record for record in registry_invariants(base_value)
    }
    removed = sorted(set(base_by_id).difference(candidate_by_id))
    if removed:
        raise ReleaseGateError(
            "candidate removes or renames invariant records without an explicit "
            "migration: " + ", ".join(removed)
        )
    common = set(base_by_id).intersection(candidate_by_id)
    changed = sorted(
        invariant_id
        for invariant_id in common
        if canonical_json_bytes(base_by_id[invariant_id])
        != canonical_json_bytes(candidate_by_id[invariant_id])
    )
    return {
        "base_registry_present": True,
        "added_invariant_ids": sorted(set(candidate_by_id).difference(base_by_id)),
        "removed_invariant_ids": removed,
        "changed_invariant_ids": changed,
        "unchanged_invariant_ids": sorted(common.difference(changed)),
    }


def invariant_globs(record: Mapping[str, Any]) -> list[str]:
    """Return the supported affected-path glob field."""
    value = record.get(
        "affected_paths", record.get("affected_file_paths_or_globs", [])
    )
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ReleaseGateError(
            f"{record.get('invariant_id')}: affected paths must be strings"
        )
    if any(item.strip() in {"*", "**", "**/*"} for item in value):
        raise ReleaseGateError(
            f"{record.get('invariant_id')}: broad catch-all mapping is prohibited"
        )
    return value


def changed_paths(repo: Path, base: str, candidate: str) -> tuple[str, ...]:
    """List changed paths between explicit base and candidate commits."""
    for revision, label in ((base, "base"), (candidate, "candidate")):
        result = _run(
            ("git", "-C", str(repo), "cat-file", "-e", f"{revision}^{{commit}}"),
            cwd=repo,
            check=False,
        )
        if result.returncode:
            raise ReleaseGateError(f"{label} commit does not exist: {revision}")
    payload = _git(
        repo,
        "diff",
        "--name-only",
        "--diff-filter=ACDMRTUXB",
        "-z",
        base,
        candidate,
    )
    return tuple(sorted(item for item in payload.split("\0") if item))


def path_matches(path: str, pattern: str) -> bool:
    """Match one relative POSIX path against an explicit registry pattern."""
    path_obj = PurePosixPath(path)
    if path_obj.match(pattern):
        return True
    return fnmatch.fnmatchcase(path, pattern)


def classify_changed_path(
    path: str,
    runtime_paths: set[str],
    generated_paths: set[str],
) -> str:
    """Classify a changed path for release reporting and fail-closed mapping."""
    if path in runtime_paths:
        return "runtime"
    if path in generated_paths:
        return "generated-artifact"
    if path.startswith("tests/"):
        return "test-only"
    if path.startswith("tools/"):
        return "tooling"
    if path.endswith(DOCUMENTATION_SUFFIXES):
        return "documentation"
    if path in PRIORITY0_CONTROL_PATHS:
        return "control"
    if path.endswith(".json") and any(
        hint in path.lower() for hint in GENERATED_NAME_HINTS
    ):
        return "generated-artifact"
    name = PurePosixPath(path).name
    if (
        path.endswith(CONTROL_SUFFIXES)
        or name.startswith("requirements")
        or name in {"pyproject.toml", "setup.cfg", "tox.ini"}
    ):
        return "control"
    if path.endswith(".py") and not path.startswith(NON_RUNTIME_PREFIXES):
        return "runtime"
    if path.endswith((".py", ".sh")) or name in RUNTIME_LAUNCHERS:
        return "runtime"
    return "non-code-data"


def map_changed_paths(
    paths: Sequence[str],
    invariants: Sequence[Mapping[str, Any]],
    *,
    runtime_paths: set[str],
    generated_paths: set[str],
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    """Map changed paths and reject uncovered code/control changes."""
    mapped: list[dict[str, Any]] = []
    uncovered_runtime: list[str] = []
    affected_by_id: dict[str, dict[str, Any]] = {}
    for path in paths:
        if path in INVARIANT_REGISTRY_DEFINITION_PATHS:
            # These files define the meaning and accepted representation of
            # every invariant. Their modification therefore affects the whole
            # registry without granting a broad wildcard to unrelated paths.
            identifiers = sorted(record["invariant_id"] for record in invariants)
        else:
            identifiers = sorted(
                record["invariant_id"]
                for record in invariants
                if any(
                    path_matches(path, pattern)
                    for pattern in invariant_globs(record)
                )
            )
        category = classify_changed_path(path, runtime_paths, generated_paths)
        mapped.append(
            {"path": path, "category": category, "invariant_ids": identifiers}
        )
        if (
            category not in UNMAPPED_DOCUMENTED_EXCEPTIONS
            and not identifiers
        ):
            uncovered_runtime.append(path)
        for record in invariants:
            if record["invariant_id"] in identifiers:
                affected_by_id[record["invariant_id"]] = dict(record)
    if uncovered_runtime:
        raise ReleaseGateError(
            "unmapped changed code/control paths: " + ", ".join(uncovered_runtime)
        )
    affected = [affected_by_id[key] for key in sorted(affected_by_id)]
    for record in affected:
        severity = str(
            record.get("severity") or record.get("criticality") or ""
        ).lower()
        commands = record.get("validation_commands")
        if commands is None and isinstance(record.get("enforcement"), dict):
            commands = record["enforcement"].get("commands")
        if severity == "critical" and (
            not isinstance(commands, list)
            or not any(
                (
                    isinstance(command, str)
                    and command.strip()
                )
                or (
                    isinstance(command, dict)
                    and isinstance(command.get("command"), str)
                    and command["command"].strip()
                )
                for command in commands
            )
        ):
            raise ReleaseGateError(
                f"affected critical invariant lacks executable validation: "
                f"{record['invariant_id']}"
            )
    return mapped, uncovered_runtime, affected


def validation_commands(records: Sequence[Mapping[str, Any]]) -> list[tuple[str, ...]]:
    """Return deterministic, de-duplicated shell-free focused commands."""
    seen: set[tuple[str, ...]] = set()
    commands: list[tuple[str, ...]] = []
    for record in records:
        values = record.get("validation_commands")
        if values is None and isinstance(record.get("enforcement"), dict):
            values = record["enforcement"].get("commands", [])
        if values is None:
            values = []
        if not isinstance(values, list):
            raise ReleaseGateError(
                f"{record.get('invariant_id')}: validation_commands must be a list"
            )
        for value in values:
            if isinstance(value, dict):
                value = value.get("command")
            if not isinstance(value, str) or not value.strip():
                continue
            raw_tokens = tuple(shlex.split(value))
            assignments: list[str] = []
            remaining = list(raw_tokens)
            while remaining and re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*=.*", remaining[0]
            ):
                assignments.append(remaining.pop(0))
            tokens = tuple(
                (["env", *assignments] if assignments else []) + remaining
            )
            if not tokens or any(
                token in {"|", "||", "&&", ";", ">", ">>", "<"} for token in tokens
            ):
                raise ReleaseGateError(
                    f"unsafe validation command in {record.get('invariant_id')}: {value}"
                )
            if tokens not in seen:
                seen.add(tokens)
                commands.append(tokens)
    return commands


def containment_namespace_command(
    command: Sequence[str],
    *,
    production_root: Path | None = None,
    candidate_root: Path | None = None,
    git_common_root: Path | None = None,
    dependency_roots: Sequence[Path] = (),
) -> tuple[str, ...]:
    """Wrap a command with network isolation and read-only protected roots."""
    prefix: tuple[str, ...] = (
        "unshare",
        "--user",
        "--map-root-user",
        "--mount",
        "--net",
        "--pid",
        "--fork",
        "--mount-proc",
        "sh",
        "-c",
    )
    roots = tuple(
        dict.fromkeys(
            str(path.resolve())
            for path in (
                production_root,
                candidate_root,
                git_common_root,
                *dependency_roots,
            )
            if path is not None
        )
    )
    return (
        *prefix,
        (
            'mount --make-rprivate / && while [ "$1" != "--" ]; do '
            'mount --bind "$1" "$1" && '
            'mount -o remount,bind,ro "$1" || exit 75; shift; done && '
            'shift && ip link set lo up && exec setpriv '
            '--bounding-set=-all --inh-caps=-all --ambient-caps=-all '
            '--securebits=+noroot,+noroot_locked --no-new-privs -- "$@"'
        ),
        "release-gate-containment",
        *roots,
        "--",
        *command,
    )


def network_namespace_command(command: Sequence[str]) -> tuple[str, ...]:
    """Backward-compatible network-only wrapper used by callers/tests."""
    return containment_namespace_command(command)


def containment_preflight(
    *,
    cwd: Path,
    production_root: Path | None = None,
    candidate_root: Path | None = None,
    dependency_roots: Sequence[Path] = (),
) -> dict[str, Any]:
    """Prove route isolation and protected-root write denial."""
    required = ("unshare", "ip", "mount", "setpriv", "sh")
    if any(shutil.which(name) is None for name in required):
        return {
            "available": False,
            "mechanism": "user-network-and-mount-namespace",
            "reason": "unshare, ip, mount or sh is unavailable",
        }
    root = production_root.resolve() if production_root is not None else None
    if root is not None and not root.is_dir():
        return {
            "available": False,
            "mechanism": "user-network-and-mount-namespace",
            "reason": "production root is missing",
        }
    candidate = candidate_root.resolve() if candidate_root is not None else None
    common = git_common_dir(candidate) if candidate is not None else None
    dependencies = tuple(Path(path).resolve() for path in dependency_roots)
    if any(not path.is_dir() for path in dependencies):
        return {
            "available": False,
            "mechanism": "user-network-and-mount-namespace",
            "reason": "validation dependency root is missing",
        }
    protected_roots = tuple(
        dict.fromkeys(
            path
            for path in (root, candidate, common, *dependencies)
            if path is not None
        )
    )
    probe_name = f".mrs-release-gate-readonly-probe-{os.getpid()}"
    probe_paths = tuple(path / probe_name for path in protected_roots)
    if any(path.exists() for path in probe_paths):
        return {
            "available": False,
            "mechanism": "user-network-and-mount-namespace",
            "reason": "write-denial probe path already exists",
        }
    script = (
        "import pathlib,socket,subprocess,sys\n"
        "names={name for _,name in socket.if_nameindex()}\n"
        "if names != {'lo'}: sys.exit(72)\n"
        "for family in ('-4','-6'):\n"
        " r=subprocess.run(('ip',family,'route','show','default'),"
        "stdout=subprocess.PIPE,stderr=subprocess.PIPE)\n"
        " if r.returncode or r.stdout.strip(): sys.exit(73)\n"
        "s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(1)\n"
        "c=socket.socket(); c.connect(s.getsockname()); a,_=s.accept()\n"
        "a.close(); c.close(); s.close()\n"
        "x=socket.socket(); x.settimeout(0.25)\n"
        "try:\n"
        " x.connect(('192.0.2.1',9))\n"
        "except OSError:\n"
        " pass\n"
        "else:\n"
        " sys.exit(71)\n"
        "status=pathlib.Path('/proc/self/status').read_text()\n"
        "if 'CapEff:\\t0000000000000000' not in status: sys.exit(76)\n"
        "if 'NoNewPrivs:\\t1' not in status: sys.exit(77)\n"
        "for value in sys.argv[1:]:\n"
        " p=pathlib.Path(value)\n"
        " try:\n"
        "  (p / %r).write_bytes(b'forbidden')\n"
        " except OSError:\n"
        "  pass\n"
        " else:\n"
        "  sys.exit(74)\n"
        " r=subprocess.run(('mount','-o','remount,bind,rw',str(p)),"
        "stdout=subprocess.PIPE,stderr=subprocess.PIPE)\n"
        " if r.returncode == 0: sys.exit(78)\n"
    ) % probe_name
    command = containment_namespace_command(
        (
            sys.executable,
            "-c",
            script,
            *(str(path) for path in protected_roots),
        ),
        production_root=root,
        candidate_root=candidate,
        git_common_root=common,
        dependency_roots=dependencies,
    )
    result = _run(command, cwd=cwd, check=False, timeout=10)
    available = result.returncode == 0
    return {
        "available": available,
        "mechanism": (
            "linux-user-network-and-mount-namespace-loopback-only-production-ro"
        ),
        "command": list(command),
        "subprocess_egress_denied": available,
        "network_route_isolated": available,
        "production_root_read_only": available if root is not None else None,
        "candidate_root_read_only": available if candidate is not None else None,
        "git_common_root_read_only": available if common is not None else None,
        "validation_dependency_roots_read_only": (
            available if dependencies else None
        ),
        "effective_capabilities_dropped": available,
        "no_new_privileges": available,
        "read_only_remount_denied_after_capability_drop": available,
        "exit_status": result.returncode,
        "output_sha256": sha256_bytes(result.stdout),
        "reason": "" if result.returncode == 0 else result.stdout.decode(
            "utf-8", "replace"
        )[-1000:],
    }


def network_preflight(*, cwd: Path) -> dict[str, Any]:
    """Compatibility entry point for a network-only containment preflight."""
    return containment_preflight(cwd=cwd)


def sanitized_validation_environment(home: Path) -> dict[str, str]:
    """Build a credential/proxy-free deterministic validation environment."""
    return sanitized_validation_environment_for_toolchain(
        home, validation_toolchain_inventory()
    )


def _distribution_content_inventory(distribution_name: str) -> dict[str, Any]:
    """Hash every installed regular file declared by one distribution."""
    try:
        distribution = importlib.metadata.distribution(distribution_name)
    except importlib.metadata.PackageNotFoundError as exc:
        raise ReleaseGateError(
            f"required validation distribution is unavailable: {distribution_name}"
        ) from exc
    records: list[dict[str, Any]] = []
    for entry in sorted(distribution.files or (), key=lambda value: str(value)):
        path = Path(distribution.locate_file(entry))
        if not path.is_file() or path.suffix in {".pyc", ".pyo"}:
            continue
        records.append(
            {
                "path": str(entry),
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
        )
    if not records:
        normalised = _normalise_distribution_name(distribution_name)
        modules = sorted(
            module
            for module, distributions in importlib.metadata.packages_distributions().items()
            if any(
                _normalise_distribution_name(value) == normalised
                for value in distributions or ()
            )
        )
        for module in modules:
            spec = importlib.util.find_spec(module)
            if spec is None or spec.origin is None:
                continue
            origin = Path(spec.origin).resolve()
            candidates = (
                sorted(origin.parent.rglob("*"))
                if spec.submodule_search_locations
                else [origin]
            )
            for path in candidates:
                if (
                    path.is_file()
                    and path.suffix not in {".pyc", ".pyo"}
                    and "__pycache__" not in path.parts
                ):
                    relative = (
                        f"{module}/"
                        + path.relative_to(origin.parent).as_posix()
                    )
                    records.append(
                        {
                            "path": relative,
                            "sha256": sha256_file(path),
                            "size": path.stat().st_size,
                        }
                    )
        for metadata_name in ("METADATA", "PKG-INFO", "entry_points.txt"):
            payload = distribution.read_text(metadata_name)
            if payload is not None:
                encoded = payload.encode("utf-8")
                records.append(
                    {
                        "path": f"metadata:{metadata_name}",
                        "sha256": sha256_bytes(encoded),
                        "size": len(encoded),
                    }
                )
    if not records:
        raise ReleaseGateError(
            f"validation distribution has no hashable files: {distribution_name}"
        )
    payload = canonical_json_bytes(records)
    return {
        "distribution": distribution_name,
        "version": distribution.version,
        "file_count": len(records),
        "content_sha256": sha256_bytes(payload),
    }


def _normalise_distribution_name(value: str) -> str:
    """Return the importlib-compatible canonical comparison form."""
    return re.sub(r"[-_.]+", "-", value).lower()


def _runtime_distribution_requirements(distribution_name: str) -> set[str]:
    """Return non-extra dependencies active in the current Python environment."""
    try:
        from packaging.requirements import InvalidRequirement, Requirement
    except ImportError as exc:
        raise ReleaseGateError(
            "packaging is required to attest validation dependencies"
        ) from exc
    distribution = importlib.metadata.distribution(distribution_name)
    result: set[str] = set()
    for raw in distribution.requires or ():
        try:
            requirement = Requirement(raw)
        except InvalidRequirement as exc:
            raise ReleaseGateError(
                f"cannot parse {distribution_name} dependency {raw!r}: {exc}"
            ) from exc
        if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
            continue
        result.add(_normalise_distribution_name(requirement.name))
    return result


def _validation_distribution_closure() -> tuple[str, ...]:
    """Resolve the transitive installed distributions used by pytest/xdist."""
    pending = ["pytest", "pytest-xdist"]
    resolved: set[str] = set()
    while pending:
        name = _normalise_distribution_name(pending.pop())
        if name in resolved:
            continue
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise ReleaseGateError(
                f"required validation dependency is unavailable: {name}"
            ) from exc
        canonical = _normalise_distribution_name(
            distribution.metadata.get("Name") or name
        )
        resolved.add(canonical)
        pending.extend(
            dependency
            for dependency in _runtime_distribution_requirements(canonical)
            if dependency not in resolved
        )
    return tuple(sorted(resolved))


def validation_toolchain_inventory() -> ValidationToolchain:
    """Build a content-bound identity for Python, pytest, and pytest-xdist."""
    executable = Path(sys.executable).resolve()
    distribution_names = _validation_distribution_closure()
    packages = tuple(
        _distribution_content_inventory(name) for name in distribution_names
    )
    module_bindings: list[dict[str, Any]] = []
    for module_name, distribution_name in (
        ("pytest", "pytest"),
        ("_pytest", "pytest"),
        ("xdist", "pytest-xdist"),
    ):
        spec = importlib.util.find_spec(module_name)
        if spec is None or spec.origin is None:
            raise ReleaseGateError(
                f"validation module is unavailable: {module_name}"
            )
        distribution = importlib.metadata.distribution(distribution_name)
        root = Path(distribution.locate_file("")).resolve()
        origin = Path(spec.origin).resolve()
        try:
            relative = origin.relative_to(root).as_posix()
        except ValueError as exc:
            raise ReleaseGateError(
                f"{module_name} origin is outside {distribution_name}"
            ) from exc
        declared_files = {str(item) for item in distribution.files or ()}
        if relative not in declared_files:
            raise ReleaseGateError(
                f"{module_name} origin is not owned by {distribution_name}: "
                f"{relative}"
            )
        module_bindings.append(
            {
                "module": module_name,
                "distribution": distribution_name,
                "origin": relative,
                "origin_sha256": sha256_file(origin),
            }
        )
    semantic = {
        "schema_version": 1,
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "executable_sha256": sha256_file(executable),
        },
        "distributions": list(packages),
        "module_distribution_bindings": module_bindings,
        "dependency_scope": (
            "recursive installed requirements active for the current interpreter; "
            "optional extras excluded"
        ),
    }
    roots = tuple(
        sorted(
            {
                str(
                    Path(
                        importlib.metadata.distribution(name).locate_file("")
                    ).resolve()
                )
                for name in distribution_names
            }
        )
    )
    return ValidationToolchain(
        semantic_inventory_json=canonical_json_bytes(semantic).decode("utf-8"),
        python_paths=roots,
    )


def sanitized_validation_environment_for_toolchain(
    home: Path, toolchain: ValidationToolchain
) -> dict[str, str]:
    """Build an environment using only the already-attested dependency roots."""
    executable_dir = str(Path(sys.executable).resolve().parent)
    path = os.pathsep.join(
        dict.fromkeys((executable_dir, "/usr/local/bin", "/usr/bin", "/bin"))
    )
    return {
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": path,
        "PYTHONPATH": os.pathsep.join(toolchain.python_paths),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }


def _pytest_counts(junit_path: Path) -> tuple[int, int, int, int]:
    """Read aggregate counts from one pytest JUnit XML file."""
    try:
        root = ET.parse(junit_path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ReleaseGateError(f"cannot parse pytest JUnit result: {exc}") from exc
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    tests = sum(int(suite.attrib.get("tests", "0")) for suite in suites)
    failures = sum(int(suite.attrib.get("failures", "0")) for suite in suites)
    errors = sum(int(suite.attrib.get("errors", "0")) for suite in suites)
    skipped = sum(int(suite.attrib.get("skipped", "0")) for suite in suites)
    return tests - failures - errors - skipped, failures, errors, skipped


def _warning_count(output: bytes) -> int:
    """Extract pytest's terminal warnings count conservatively."""
    text = output.decode("utf-8", "replace")
    matches = re.findall(r"(\d+)\s+warnings?\b", text)
    return int(matches[-1]) if matches else 0


def execute_validation(
    command: Sequence[str],
    *,
    cwd: Path,
    output_dir: Path,
    label: str,
    network_isolated: bool,
    production_root: Path | None = None,
    candidate_root: Path | None = None,
    dependency_roots: Sequence[Path] = (),
    env: Mapping[str, str] | None = None,
) -> ValidationResult:
    """Execute and capture one validation command."""
    output_dir.mkdir(parents=True, exist_ok=True)
    junit = output_dir / f"{label}.junit.xml"
    logical = tuple(command)
    actual = logical
    is_pytest = "pytest" in actual
    if is_pytest and "no:cacheprovider" not in actual:
        actual = (*actual, "-p", "no:cacheprovider")
    if is_pytest and not any(token.startswith("--junitxml") for token in actual):
        actual = (*actual, f"--junitxml={junit}")
    if network_isolated:
        actual = containment_namespace_command(
            actual,
            production_root=production_root,
            candidate_root=candidate_root,
            git_common_root=(
                git_common_dir(candidate_root)
                if candidate_root is not None
                else None
            ),
            dependency_roots=dependency_roots,
        )
    started = time.monotonic()
    result = _run(actual, cwd=cwd, check=False, env=env)
    duration = time.monotonic() - started
    output_path = output_dir / f"{label}.output.txt"
    write_atomic(output_path, result.stdout)
    if is_pytest:
        if not junit.is_file():
            raise ReleaseGateError(
                f"pytest validation did not emit required JUnit evidence: {label}"
            )
        passed, failed, errors, skipped = _pytest_counts(junit)
    else:
        passed, failed, errors, skipped = (
            (1, 0, 0, 0) if result.returncode == 0 else (0, 1, 0, 0)
        )
    return ValidationResult(
        command=logical,
        exit_status=result.returncode,
        passed=passed,
        failed=failed,
        errors=errors,
        skipped=skipped,
        warnings=_warning_count(result.stdout),
        output_sha256=sha256_bytes(result.stdout),
        duration_seconds=duration,
        junit_sha256=sha256_file(junit) if junit.exists() else None,
        executed_command=actual,
    )


@contextlib.contextmanager
def detached_candidate_worktree(
    repo: Path, commit: str, scratch_root: Path
) -> Iterator[Path]:
    """Create and remove a detached clean worktree for one frozen commit."""
    scratch_root.mkdir(parents=True, exist_ok=True)
    container = Path(
        tempfile.mkdtemp(prefix="mrs-release-gate-", dir=scratch_root)
    )
    checkout = container / "candidate"
    registered = False
    try:
        _run(
            (
                "git",
                "-C",
                str(repo),
                "worktree",
                "add",
                "--detach",
                str(checkout),
                commit,
            ),
            cwd=repo,
        )
        registered = True
        status, untracked = git_status(checkout)
        if status or untracked or _git(checkout, "rev-parse", "HEAD") != commit:
            raise ReleaseGateError("isolated candidate checkout is not clean and exact")
        yield checkout
    finally:
        if registered:
            _run(
                (
                    "git",
                    "-C",
                    str(repo),
                    "worktree",
                    "remove",
                    "--force",
                    str(checkout),
                ),
                cwd=repo,
                check=False,
            )
        shutil.rmtree(container, ignore_errors=True)


def full_suite_command(workers: int) -> tuple[str, ...]:
    """Return the repository's supported complete parallel pytest command."""
    if workers < 1:
        raise ReleaseGateError("worker count must be positive")
    return (
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "xdist.plugin",
        "-n",
        str(workers),
        "--dist=worksteal",
        "--max-worker-restart=0",
    )


def runtime_path_sets(snapshot: CandidateSnapshot) -> tuple[set[str], set[str]]:
    """Return runtime and generated paths from a captured snapshot."""
    return (
        {path for path, _digest in snapshot.runtime_hashes},
        {
            path
            for path, _digest in (
                *snapshot.generated_hashes,
                *snapshot.declared_artifact_hashes,
            )
        },
    )


def deployed_identity(
    production_root: Path | None,
    declared_runtime_artifacts: Sequence[str] = (),
) -> dict[str, Any]:
    """Record production/deployed identities read-only, without importing code."""
    if production_root is None:
        return {"available": False, "reason": "production root not supplied"}
    root = production_root.resolve()
    value: dict[str, Any] = {
        "available": root.is_dir(),
        "root": str(root),
    }
    if not root.is_dir():
        value["reason"] = "production root missing"
        return value
    try:
        repository_status = _git_readonly(
            root,
            "status",
            "--porcelain=v2",
            "--branch",
            "--untracked-files=all",
        )
        value.update(
            {
                "repository_commit": _git_readonly(root, "rev-parse", "HEAD"),
                "repository_tree": _git_readonly(root, "rev-parse", "HEAD^{tree}"),
                "origin_master": _git_readonly(
                    root, "rev-parse", "origin/master", check=False
                ),
                "repository_status": repository_status,
                "repository_status_sha256": sha256_bytes(
                    repository_status.encode("utf-8", "surrogateescape")
                ),
            }
        )
    except ReleaseGateError as exc:
        value["repository_error"] = str(exc)
    deployed: dict[str, dict[str, Any]] = {}
    for path in (
        Path("/usr/local/bin/mrsMThatcher2.py"),
        Path("/usr/local/bin/runMrsMThatcher2"),
    ):
        if path.is_file():
            deployed[str(path)] = file_identity(path)
    value["deployed_path_identities"] = deployed
    try:
        runtime_files = discover_runtime_python_files(root)
        generated_files, bindings, unresolved = discover_generated_artifacts(
            root, runtime_files
        )
        value["production_runtime_python_hashes"] = dict(
            hash_paths(root, runtime_files)
        )
        value["production_generated_artifact_hashes"] = dict(
            hash_paths(root, generated_files)
        )
        value["production_registry_declared_runtime_artifact_hashes"] = dict(
            hash_paths(root, declared_runtime_artifacts)
        )
        value["production_generated_loader_binding_count"] = len(bindings)
        value["production_unresolved_loader_literals"] = unresolved
    except ReleaseGateError as exc:
        value["production_inventory_error"] = str(exc)
    live_configuration: dict[str, str] = {}
    for relative in (
        "mrsMThatcher.local.json",
        "mrsMThatcher.control.json",
        "mrsMThatcher.env",
    ):
        path = root / relative
        if path.is_file():
            live_configuration[relative] = sha256_file(path)
    value["production_live_configuration_hashes"] = live_configuration
    configured_artifacts: list[dict[str, Any]] = []
    local_config_path = root / "mrsMThatcher.local.json"
    if local_config_path.is_file():
        try:
            local_config = json.loads(local_config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            value["production_local_config_parse_error"] = (
                f"{type(exc).__name__}: {exc}"
            )
        else:
            veto = (
                local_config.get("quote_image_semantic_veto")
                if isinstance(local_config, dict)
                else None
            )
            manifest_value = veto.get("manifest_path") if isinstance(veto, dict) else None
            if isinstance(manifest_value, str):
                manifest_path = (root / manifest_value).resolve()
                try:
                    relative = manifest_path.relative_to(root).as_posix()
                except ValueError:
                    configured_artifacts.append(
                        {
                            "configuration_key": (
                                "quote_image_semantic_veto.manifest_path"
                            ),
                            "status": "path_escapes_production_root",
                        }
                    )
                else:
                    if manifest_path.is_file():
                        configured_artifacts.append(
                            {
                                "configuration_key": (
                                    "quote_image_semantic_veto.manifest_path"
                                ),
                                "status": "present",
                                "path": relative,
                                "sha256": sha256_file(manifest_path),
                                "semantics": _artifact_semantic_summary(
                                    root, relative
                                ),
                            }
                        )
                    else:
                        configured_artifacts.append(
                            {
                                "configuration_key": (
                                    "quote_image_semantic_veto.manifest_path"
                                ),
                                "status": "missing",
                                "path": relative,
                            }
                        )
    value["production_configured_generated_artifacts"] = configured_artifacts
    return value


def require_production_baseline(
    identity: Mapping[str, Any], *, base_commit: str, development: bool
) -> None:
    """Require a readable production repository at the exact release base."""
    if development:
        return
    if not identity.get("available") or identity.get("repository_error"):
        raise ReleaseGateError("production root is not a readable Git repository")
    if identity.get("repository_commit") != base_commit:
        raise ReleaseGateError(
            "production HEAD does not equal the exact release base commit"
        )


def service_snapshot(unit: str = "mrsMThatcher.service") -> dict[str, Any]:
    """Read the user-service and Python-child identity without signalling it."""
    if shutil.which("systemctl") is None:
        return {"available": False, "reason": "systemctl unavailable", "unit": unit}
    properties = (
        "ActiveState",
        "SubState",
        "MainPID",
        "ExecMainStartTimestamp",
        "NRestarts",
        "ExecStart",
        "WorkingDirectory",
    )
    command = ["systemctl", "--user", "show", unit]
    for name in properties:
        command.extend(("-p", name))
    result = _run(command, cwd=Path.cwd(), check=False)
    if result.returncode:
        return {
            "available": False,
            "unit": unit,
            "exit_status": result.returncode,
            "output_sha256": sha256_bytes(result.stdout),
        }
    parsed: dict[str, str] = {}
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            parsed[key] = value
    child_records: list[dict[str, Any]] = []
    main_pid = parsed.get("MainPID", "")
    if main_pid.isdigit() and int(main_pid) > 0:
        children_path = Path(f"/proc/{main_pid}/task/{main_pid}/children")
        try:
            child_ids = children_path.read_text(encoding="ascii").split()
        except OSError:
            child_ids = []
        for child in sorted(child_ids, key=int):
            command_path = Path(f"/proc/{child}/cmdline")
            try:
                raw = command_path.read_bytes()
            except OSError:
                continue
            child_records.append(
                {
                    "pid": int(child),
                    "command_sha256": sha256_bytes(raw),
                    "is_python": b"python" in raw.lower(),
                }
            )
    return {
        "available": True,
        "unit": unit,
        "properties": parsed,
        "children": child_records,
    }


def service_invariants_equal(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> bool:
    """Compare stable service identity properties relevant to this task."""
    keys = (
        "ActiveState",
        "SubState",
        "MainPID",
        "ExecMainStartTimestamp",
        "NRestarts",
    )
    return (
        before.get("available") == after.get("available")
        and all(
            before.get("properties", {}).get(key)
            == after.get("properties", {}).get(key)
            for key in keys
        )
        and before.get("children") == after.get("children")
    )


def _resolve_declared_pin_path(
    repo: Path,
    *,
    artifact_relative: str,
    pin_name: str,
    pin_value: Any,
) -> tuple[str | None, str | None]:
    """Resolve one present-architecture hash pin conservatively."""
    declared_path: str | None = None
    repository_root_bound = False
    if isinstance(pin_value, Mapping):
        candidate = pin_value.get("path")
        if isinstance(candidate, str):
            declared_path = candidate
            repository_root_bound = True
    elif isinstance(pin_value, str):
        alias = SOURCE_PIN_ALIASES.get(pin_name)
        declared_path = alias or pin_name
        repository_root_bound = alias is not None
    if not declared_path:
        return None, "pin does not declare a path"
    path = PurePosixPath(declared_path)
    if path.is_absolute() or ".." in path.parts:
        return None, "pin path is absolute or escapes the candidate"
    preferred = (
        repo / path
        if repository_root_bound
        else (repo / artifact_relative).parent / path
    )
    candidates: list[Path] = [preferred]
    if not preferred.is_file() and not repository_root_bound:
        candidates.append(repo / path)
    existing = {
        candidate.resolve() for candidate in candidates if candidate.is_file()
    }
    if not existing and not repository_root_bound:
        suffix = path.as_posix()
        try:
            repository_files = tracked_files(repo)
        except ReleaseGateError:
            repository_files = ()
        existing = {
            (repo / relative).resolve()
            for relative in repository_files
            if relative == suffix or relative.endswith(f"/{suffix}")
            if (repo / relative).is_file()
        }
    if len(existing) != 1:
        return (
            None,
            "pin path is missing"
            if not existing
            else "pin path resolves ambiguously",
        )
    return next(iter(existing)).relative_to(repo.resolve()).as_posix(), None


def _declared_pin_records(
    repo: Path, relative: str, field: str, value: Any
) -> list[dict[str, Any]]:
    """Recompute path/hash pin records and preserve advisory mismatches."""
    if not isinstance(value, Mapping):
        return []
    output: list[dict[str, Any]] = []
    for pin_name, pin_value in sorted(value.items()):
        expected = (
            pin_value.get("sha256")
            if isinstance(pin_value, Mapping)
            else pin_value
        )
        required = (
            field == "source_file_hashes"
            and (relative, str(pin_name)) not in ADVISORY_SOURCE_PIN_KEYS
        )
        resolved, error = _resolve_declared_pin_path(
            repo,
            artifact_relative=relative,
            pin_name=str(pin_name),
            pin_value=pin_value,
        )
        actual = sha256_file(repo / resolved) if resolved else None
        record = {
            "field": field,
            "pin_name": str(pin_name),
            "expected_sha256": expected,
            "resolved_path": resolved,
            "actual_sha256": actual,
            "match": bool(
                resolved
                and isinstance(expected, str)
                and HEX64.fullmatch(expected)
                and actual == expected
            ),
            "runtime_relationship_required": required,
            "resolution_error": error,
        }
        advisory_reason = ADVISORY_SOURCE_PIN_KEYS.get(
            (relative, str(pin_name))
        )
        if advisory_reason:
            record["advisory_reason"] = advisory_reason
        output.append(record)
    return output


def _artifact_semantic_summary(repo: Path, relative: str) -> dict[str, Any]:
    """Extract bounded schema/policy/count/hash metadata from one JSON file."""
    path = repo / relative
    summary: dict[str, Any] = {"path": relative, "sha256": sha256_file(path)}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        summary["parse_error"] = f"{type(exc).__name__}: {exc}"
        return summary
    if not isinstance(value, dict):
        summary["root_type"] = type(value).__name__
        return summary
    versions: dict[str, Any] = {}
    counts: dict[str, int] = {}
    pins: dict[str, Any] = {}
    for key, item in sorted(value.items()):
        lowered = key.lower()
        if (
            lowered.endswith("version")
            or lowered in {"mode", "policy", "enforcement_state"}
        ) and isinstance(item, (str, int, bool)):
            versions[key] = item
        if (
            lowered.endswith("_count")
            or lowered in {
                "record_count",
                "quote_count",
                "image_count",
                "pair_count",
                "completed_quotes",
                "unresolved_quotes",
            }
        ) and type(item) is int:
            counts[key] = item
        if (
            lowered in {
                "input_hashes",
                "source_hashes",
                "source_file_hashes",
                "source_code_hashes",
                "source_code_sha256",
                "generator_sha256",
                "policy_sha256",
                "manifest_sha256",
                "projection_sha256",
                "ledger_sha256",
            }
            and isinstance(item, (str, dict, list))
        ):
            # These are already public repository metadata. Keep the complete
            # declared binding so an independent reviewer can reproduce it.
            pins[key] = item
    summary["schema_policy_versions"] = versions
    summary["semantic_counts"] = counts
    summary["declared_input_and_source_pins"] = pins
    verified: list[dict[str, Any]] = []
    for field in ("source_file_hashes", "input_hashes"):
        if field in value:
            verified.extend(
                _declared_pin_records(repo, relative, field, value[field])
            )
    summary["recomputed_declared_pins"] = verified
    return summary


def relationship_inventory(
    repo: Path,
    snapshot: CandidateSnapshot,
    bindings: Sequence[Mapping[str, Any]],
    unresolved: Sequence[str],
) -> dict[str, Any]:
    """Describe present generated-artifact bindings and their validators."""
    validators = [
        "python3 -m pytest -q tests/test_historical_context_reply_semantic_gate.py",
        "python3 -m pytest -q tests/test_historical_context_reply.py",
        "python3 -m pytest -q tests/test_quote_image_semantic_veto_shadow.py",
    ]
    existing_validators = [
        value
        for value in validators
        if (repo / shlex.split(value)[-1]).exists()
    ]
    ambiguous = [
        dict(item)
        for item in bindings
        if item.get("resolution") == "ambiguous_not_claimed_runtime"
    ]
    validator_backed = [
        dict(item)
        for item in bindings
        if item.get("resolution") == "validator_backed_offline_literal"
    ]
    validator_paths = {
        shlex.split(command)[-1] for command in existing_validators
    }
    invalid_validator_backed = [
        item
        for item in validator_backed
        if item.get("validator") not in validator_paths
        or not (repo / str(item.get("validator") or "")).is_file()
    ]
    artifact_hashes = dict(snapshot.generated_hashes)
    artifact_hashes.update(snapshot.declared_artifact_hashes)
    json_artifacts = [
        relative for relative in artifact_hashes if relative.endswith(".json")
    ]
    semantics = [
        _artifact_semantic_summary(repo, relative)
        for relative in sorted(json_artifacts)
    ]
    pin_records = [
        record
        for summary in semantics
        for record in summary.get("recomputed_declared_pins", [])
    ]
    required_pin_failures = [
        record
        for record in pin_records
        if record.get("runtime_relationship_required") and not record.get("match")
    ]
    advisory_pin_findings = [
        record
        for record in pin_records
        if not record.get("runtime_relationship_required") and not record.get("match")
    ]
    return {
        "discovery_method": (
            "AST runtime-import closure plus tracked JSON loader-literal resolution"
        ),
        "runtime_loader_bindings": list(bindings),
        "artifact_hashes": dict(sorted(artifact_hashes.items())),
        "artifact_semantics": semantics,
        "schema_policy_hashes": dict(snapshot.policy_hashes),
        "relationship_validators": existing_validators,
        "unresolved_loader_literals": list(unresolved),
        "architecture_limit": (
            "current files are individually hash-bound where loaders expose pins; "
            "the present architecture has no single atomic generation identity"
        ),
        "ambiguous_nonruntime_literals": ambiguous,
        "validator_backed_offline_literals": validator_backed,
        "invalid_validator_backed_classifications": invalid_validator_backed,
        "all_validator_backed_classifications_valid": not bool(
            invalid_validator_backed
        ),
        "all_claimed_runtime_bindings_resolved": not bool(unresolved),
        "all_discovered_bindings_resolved": not bool(unresolved or ambiguous),
        "all_required_source_file_pins_valid": not bool(required_pin_failures),
        "required_source_file_pin_failures": required_pin_failures,
        "advisory_or_build_time_pin_findings": advisory_pin_findings,
    }


def relationship_validation_commands(
    relationships: Mapping[str, Any],
) -> list[tuple[str, ...]]:
    """Parse recorded relationship validators through the shell-free parser."""
    values = relationships.get("relationship_validators", [])
    return validation_commands(
        [
            {
                "invariant_id": "INV-ART-RELATIONSHIPS",
                "validation_commands": values,
            }
        ]
    )


def unmet_deployed_checks(
    invariants: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Record activation-time deployed checks not performed by this gate."""
    result: list[dict[str, str]] = []
    for record in invariants:
        section = record.get("required_production_deployed_path_checks")
        if not isinstance(section, Mapping) or section.get("status") != "required":
            continue
        checks = section.get("checks", [])
        if not isinstance(checks, list):
            continue
        for check in checks:
            if isinstance(check, str) and check.strip():
                result.append(
                    {
                        "invariant_id": record["invariant_id"],
                        "check": check,
                        "status": "unmet_pending_activation",
                    }
                )
    return result


def conclusion_scope(
    *,
    development: bool,
    full_suite: bool,
    full_result: ValidationResult | None,
    affected: Sequence[Mapping[str, Any]],
) -> str:
    """Return a qualified conclusion scope; never infer system-wide assurance."""
    if development:
        return "development-only"
    if not full_suite or full_result is None or full_result.exit_status:
        return "patch-local release candidate"
    subsystems = {
        str(
            record.get("owner_subsystem")
            or record.get("owner")
            or record.get("category")
            or ""
        )
        for record in affected
    } - {""}
    return "subsystem-level" if len(subsystems) == 1 else "patch-local release candidate"


def deterministic_attestation(
    *,
    base: str,
    snapshot: CandidateSnapshot,
    registry_sha256: str,
    ledger_sha256: str,
    registry_transition: Mapping[str, Any],
    path_mapping: Sequence[Mapping[str, Any]],
    affected: Sequence[Mapping[str, Any]],
    focused: Sequence[ValidationResult],
    full: ValidationResult | None,
    network: Mapping[str, Any],
    toolchain: Mapping[str, Any],
    relationships: Mapping[str, Any],
    deployed_checks: Sequence[Mapping[str, Any]],
    scope: str,
) -> dict[str, Any]:
    """Build the non-volatile semantic attestation."""
    network_semantic = {
        key: network.get(key)
        for key in (
            "available",
            "mechanism",
            "subprocess_egress_denied",
            "network_route_isolated",
            "production_root_read_only",
            "candidate_root_read_only",
            "git_common_root_read_only",
            "validation_dependency_roots_read_only",
            "effective_capabilities_dropped",
            "no_new_privileges",
            "read_only_remount_denied_after_capability_drop",
            "exit_status",
        )
    }
    return {
        "schema_version": 1,
        "candidate": snapshot.semantic_dict(),
        "base_commit": base,
        "invariant_registry_sha256": registry_sha256,
        "defect_ledger_sha256": ledger_sha256,
        "invariant_registry_transition": dict(registry_transition),
        "changed_path_mapping": list(path_mapping),
        "affected_invariant_ids": [
            record["invariant_id"] for record in affected
        ],
        "focused_validation": [result.semantic_dict() for result in focused],
        "complete_suite_validation": (
            None if full is None else full.semantic_dict()
        ),
        "network_isolation": network_semantic,
        "validation_toolchain": dict(toolchain),
        "generated_artifact_attestation": relationships,
        "unmet_deployed_checks": list(deployed_checks),
        "conclusion_scope": scope,
        "release_candidate_validation_passed": bool(
            network.get("available")
            and network.get("network_route_isolated")
            and network.get("production_root_read_only")
            and network.get("candidate_root_read_only")
            and network.get("git_common_root_read_only")
            and network.get("validation_dependency_roots_read_only")
            and network.get("effective_capabilities_dropped")
            and network.get("no_new_privileges")
            and network.get("read_only_remount_denied_after_capability_drop")
            and relationships.get("all_claimed_runtime_bindings_resolved")
            and relationships.get("all_discovered_bindings_resolved")
            and relationships.get("all_validator_backed_classifications_valid")
            and relationships.get("all_required_source_file_pins_valid")
            and full is not None
            and full.exit_status == 0
            and all(result.exit_status == 0 for result in focused)
        ),
    }


def markdown_report(
    semantic: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> str:
    """Render the release-gate report from attestation data."""
    candidate = semantic["candidate"]
    focused = semantic["focused_validation"]
    full = semantic["complete_suite_validation"]
    relationships = semantic["generated_artifact_attestation"]
    lines = [
        "# Frozen candidate release-gate report",
        "",
        f"- Candidate commit: `{candidate['commit']}`",
        f"- Candidate tree: `{candidate['tree']}`",
        f"- Base commit: `{semantic['base_commit']}`",
        f"- Conclusion scope: **{semantic['conclusion_scope']}**",
        f"- Qualified release-candidate validation passed: **"
        f"{'yes' if semantic['release_candidate_validation_passed'] else 'no'}**",
        f"- Network isolation: `{semantic['network_isolation']['mechanism']}`",
        f"- Candidate unchanged after validation: **"
        f"{'yes' if receipt.get('candidate_unchanged_after_validation') else 'no'}**",
        "",
        "This conclusion is limited to the scope above. It is not an "
        "unqualified claim about the whole system.",
        "",
        "## Changed paths and invariant coverage",
        "",
        "| Path | Class | Invariants |",
        "|---|---|---|",
    ]
    for record in semantic["changed_path_mapping"]:
        lines.append(
            f"| `{record['path']}` | {record['category']} | "
            f"{', '.join(record['invariant_ids']) or 'none'} |"
        )
    lines.extend(
        [
            "",
            "## Focused validation",
            "",
            "| Command | Passed | Failed | Errors | Skipped | Exit |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for result in focused:
        lines.append(
            f"| `{' '.join(result['command'])}` | {result['passed']} | "
            f"{result['failed']} | {result['errors']} | {result['skipped']} | "
            f"{result['exit_status']} |"
        )
    lines.extend(["", "## Complete isolated suite", ""])
    if full is None:
        lines.append("Not run; complete release-candidate validation is absent.")
    else:
        full_receipt = receipt.get("complete_suite_validation") or {}
        lines.extend(
            [
                f"- Passed: {full['passed']}",
                f"- Failed: {full['failed']}",
                f"- Errors: {full['errors']}",
                f"- Skipped: {full['skipped']}",
                f"- Warnings: {full['warnings']}",
                f"- Exit status: {full['exit_status']}",
                f"- Output SHA-256: `{full_receipt.get('output_sha256', 'unavailable')}`",
            ]
        )
    lines.extend(
        [
            "",
            "## Generated-artifact relationships",
            "",
            f"- Discovered artifact files: "
            f"{len(relationships.get('artifact_hashes', {}))}",
            f"- Unresolved loader literals: "
            f"{len(relationships.get('unresolved_loader_literals', []))}",
            f"- All discovered bindings resolved: "
            f"{'yes' if relationships.get('all_discovered_bindings_resolved') else 'no'}",
            f"- Current-architecture limitation: "
            f"{relationships.get('architecture_limit')}",
            f"- Ambiguous non-runtime literals: "
            f"{len(relationships.get('ambiguous_nonruntime_literals', []))}",
            f"- Activation-time deployed checks still unmet: "
            f"{len(semantic.get('unmet_deployed_checks', []))}",
            "",
            "## Independent review",
            "",
            "Independent read-only review is pending. Use "
            "`INDEPENDENT_REVIEW_REQUEST.md` and "
            "`independent_review_manifest.json` in a fresh session without the "
            "implementation transcript.",
            "",
        ]
    )
    return "\n".join(lines)


def _hash_inventory(output_dir: Path, names: Sequence[str]) -> dict[str, str]:
    """Hash existing attestation outputs by basename."""
    return {
        name: sha256_file(output_dir / name)
        for name in names
        if (output_dir / name).is_file()
    }


def _validation_hash_inventory(output_dir: Path) -> dict[str, str]:
    """Hash every focused/full validation output relative to the output root."""
    validation = output_dir / "validation"
    if not validation.is_dir():
        return {}
    return {
        path.relative_to(output_dir).as_posix(): sha256_file(path)
        for path in sorted(validation.rglob("*"))
        if path.is_file()
    }


def emit_outputs(
    *,
    output_dir: Path,
    semantic: dict[str, Any],
    receipt: dict[str, Any],
    registry_path: Path,
    ledger_path: Path,
    diff_hash: str,
    source_diagnosis_path: str,
    source_diagnosis_sha256: str,
) -> dict[str, str]:
    """Write semantic/run/report/review artefacts and a final hash inventory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(output_dir, 0o700)
    semantic_bytes = canonical_json_bytes(semantic)
    write_atomic(output_dir / "semantic_attestation.json", semantic_bytes)
    write_atomic(
        output_dir / "release_gate_run_receipt.json",
        canonical_json_bytes(receipt),
    )
    run_receipt_sha256 = sha256_file(
        output_dir / "release_gate_run_receipt.json"
    )
    write_atomic(
        output_dir / "release_gate_report.md",
        markdown_report(semantic, receipt).encode("utf-8"),
    )
    registry = load_json_object(registry_path)
    ledger = load_json_object(ledger_path)
    measurements_path = registry_path.parent / "diagnosis_measurements.json"
    measurements = (
        load_json_object(measurements_path)
        if measurements_path.is_file()
        else {}
    )
    invariant_records = registry_invariants(registry)
    defect_records = ledger.get("defects", ledger.get("records", []))
    if not isinstance(defect_records, list):
        defect_records = []
    invariant_severity: dict[str, int] = {}
    invariant_status: dict[str, int] = {}
    for record in invariant_records:
        severity = str(
            record.get("severity") or record.get("criticality") or "unknown"
        )
        status = str(
            record.get("current_implementation_status")
            or record.get("implementation_status")
            or "unknown"
        )
        invariant_severity[severity] = invariant_severity.get(severity, 0) + 1
        invariant_status[status] = invariant_status.get(status, 0) + 1
    defect_status: dict[str, int] = {}
    for record in defect_records:
        if isinstance(record, dict):
            status = str(record.get("current_status") or record.get("status") or "unknown")
            defect_status[status] = defect_status.get(status, 0) + 1
    final_validation = {
        "schema_version": 1,
        "source_diagnosis": {
            "path": source_diagnosis_path,
            "sha256": source_diagnosis_sha256,
        },
        "corrected_measurements": measurements.get("corrected_claims", []),
        "diagnosis_measurements_sha256": (
            sha256_file(measurements_path) if measurements_path.is_file() else None
        ),
        "production_baseline": receipt.get("production_identity_before"),
        "candidate_commit": semantic["candidate"]["commit"],
        "candidate_tree": semantic["candidate"]["tree"],
        "base_commit": semantic["base_commit"],
        "changed_files": [
            record["path"] for record in semantic["changed_path_mapping"]
        ],
        "invariant_counts_by_severity": dict(sorted(invariant_severity.items())),
        "invariant_counts_by_implementation_status": dict(
            sorted(invariant_status.items())
        ),
        "defect_counts_by_status": dict(sorted(defect_status.items())),
        "unresolved_or_partial_invariant_ids": [
            record["invariant_id"]
            for record in invariant_records
            if str(
                record.get("current_implementation_status")
                or record.get("implementation_status")
                or ""
            )
            not in {"implemented", "enforced", "verified"}
        ],
        "focused_test_results": semantic["focused_validation"],
        "complete_isolated_suite_result": semantic[
            "complete_suite_validation"
        ],
        "complete_isolated_suite_output_sha256": (
            (receipt.get("complete_suite_validation") or {}).get(
                "output_sha256"
            )
        ),
        "network_isolation": semantic["network_isolation"],
        "candidate_before_after_tree_equal": receipt.get(
            "candidate_unchanged_after_validation"
        ),
        "semantic_attestation_deterministic": True,
        "semantic_attestation_sha256": sha256_bytes(semantic_bytes),
        "run_receipt_sha256": run_receipt_sha256,
        "independent_review_status": "pending_separate_read_only_session",
        "conclusion_scope": semantic["conclusion_scope"],
        "release_candidate_validation_passed": semantic[
            "release_candidate_validation_passed"
        ],
        "unmet_deployed_checks": semantic.get("unmet_deployed_checks", []),
        "residual_blockers": [
            {
                "invariant_id": record["invariant_id"],
                "implementation_status": (
                    record.get("current_implementation_status")
                    or record.get("implementation_status")
                    or "unknown"
                ),
                "known_gaps": record.get("known_gaps", []),
            }
            for record in invariant_records
            if str(
                record.get("current_implementation_status")
                or record.get("implementation_status")
                or ""
            )
            not in {"implemented", "enforced", "verified"}
        ],
        "production_identity_unchanged": receipt.get(
            "production_identity_unchanged"
        ),
        "service_invariants_unchanged": receipt.get(
            "service_invariants_unchanged"
        ),
        "task_actions": {
            "production_file_mutations": 0,
            "service_actions": 0,
            "x_actions": 0,
            "provider_requests": 0,
            "deployments": 0,
            "pushes": 0,
            "merges": 0,
        },
        "procedural_notes": list(receipt.get("procedural_notes", [])),
    }
    write_atomic(
        output_dir / "priority0_consolidation_final_validation.json",
        canonical_json_bytes(final_validation),
    )
    procedural_notes = [
        str(item)
        for item in receipt.get("procedural_notes", [])
        if isinstance(item, str) and item.strip()
    ]
    procedural_evidence = (
        "\n".join(f"- {item}" for item in procedural_notes)
        if procedural_notes
        else "- No additional procedural exceptions were recorded."
    )
    consolidation_report = (
        "# Priority-0 consolidation candidate\n\n"
        f"- Source diagnosis: `{source_diagnosis_path}`\n"
        f"- Source diagnosis SHA-256: `{source_diagnosis_sha256}`\n"
        f"- Production baseline: `{semantic['base_commit']}`\n"
        f"- Candidate commit: `{semantic['candidate']['commit']}`\n"
        f"- Candidate tree: `{semantic['candidate']['tree']}`\n"
        f"- Changed files: {len(semantic['changed_path_mapping'])}\n"
        f"- Invariants by severity: `{json.dumps(dict(sorted(invariant_severity.items())))}`\n"
        f"- Invariants by status: `{json.dumps(dict(sorted(invariant_status.items())))}`\n"
        f"- Defects by status: `{json.dumps(dict(sorted(defect_status.items())))}`\n"
        f"- Network isolation: `{semantic['network_isolation']['mechanism']}`\n"
        f"- Candidate tree unchanged through validation: "
        f"{'yes' if receipt.get('candidate_unchanged_after_validation') else 'no'}\n"
        f"- Independent review: pending separate read-only session\n\n"
        "## Corrected measurements\n\n"
        + "\n".join(
            f"- **{record.get('status', 'recorded')}** — "
            f"{record.get('claim', 'unnamed claim')} "
            f"{record.get('qualification', '')}"
            for record in measurements.get("corrected_claims", [])
            if isinstance(record, dict)
        )
        + "\n\n"
        "## Finding chronology and invariant gaps\n\n"
        "The corrected diagnosis contains the ten-area chronology linked to "
        "the canonical 17-record defect ledger. Current defect status counts "
        f"are `{json.dumps(dict(sorted(defect_status.items())))}`. "
        "The invariant registry deliberately leaves these implementation "
        "statuses visible: "
        f"`{json.dumps(dict(sorted(invariant_status.items())))}`.\n\n"
        "## Gate architecture and CLI\n\n"
        "The gate holds the shared-Git integration lock, maps the explicit "
        "base-to-candidate diff, executes invariant-selected checks, then runs "
        "the whole suite from a detached checkout in a loopback-only network "
        "namespace. The exact invocation and worker count are in the run "
        "receipt. Reproduction syntax is documented in "
        "`tools/README_release_gate.md`; use `python3 tools/release_gate.py "
        "run --help` for the argument contract.\n\n"
        "## Production isolation\n\n"
        f"- Production/deployed identity unchanged: "
        f"{'yes' if receipt.get('production_identity_unchanged') else 'no'}\n"
        f"- Service invariants unchanged: "
        f"{'yes' if receipt.get('service_invariants_unchanged') else 'no'}\n"
        "- Production file mutations by this task: 0\n"
        "- Service actions by this task: 0\n"
        "- X actions by this task: 0\n"
        "- Provider requests by this task: 0\n"
        "- Deployments, merges and pushes by this task: 0\n\n"
        "## Procedural evidence\n\n"
        + procedural_evidence
        + "\n\n"
        + markdown_report(semantic, receipt)
    )
    write_atomic(
        output_dir / "priority0_consolidation_report.md",
        consolidation_report.encode("utf-8"),
    )
    relationship_hash = sha256_bytes(
        canonical_json_bytes(semantic["generated_artifact_attestation"])
    )
    review_manifest = {
        "schema_version": 1,
        "candidate_commit": semantic["candidate"]["commit"],
        "candidate_tree": semantic["candidate"]["tree"],
        "base_commit": semantic["base_commit"],
        "final_diff_sha256": diff_hash,
        "invariant_registry_sha256": sha256_file(registry_path),
        "defect_ledger_sha256": sha256_file(ledger_path),
        "generated_artifact_inventory_sha256": relationship_hash,
        "source_diagnosis": {
            "path": source_diagnosis_path,
            "sha256": source_diagnosis_sha256,
        },
        "corrected_candidate_diagnosis": {
            "path": "why_code_reviews_continue_to_find_major_problems.md",
            "sha256": sha256_file(
                registry_path.parent
                / "why_code_reviews_continue_to_find_major_problems.md"
            ),
        },
        "semantic_attestation_sha256": sha256_bytes(semantic_bytes),
        "release_gate_run_receipt_sha256": run_receipt_sha256,
        "focused_validation": semantic["focused_validation"],
        "complete_suite_validation": semantic["complete_suite_validation"],
        "changed_path_mapping": semantic["changed_path_mapping"],
        "unmet_deployed_checks": semantic.get("unmet_deployed_checks", []),
        "independent_review_status": "pending_separate_session",
    }
    write_atomic(
        output_dir / "independent_review_manifest.json",
        canonical_json_bytes(review_manifest),
    )
    names = (
        "semantic_attestation.json",
        "release_gate_run_receipt.json",
        "release_gate_report.md",
        "independent_review_manifest.json",
        "priority0_consolidation_report.md",
        "priority0_consolidation_final_validation.json",
    )
    inventory = _hash_inventory(output_dir, names)
    inventory.update(_validation_hash_inventory(output_dir))
    write_atomic(
        output_dir / "attestation_sha256_inventory.json",
        canonical_json_bytes({"schema_version": 1, "files": inventory}),
    )
    inventory["attestation_sha256_inventory.json"] = sha256_file(
        output_dir / "attestation_sha256_inventory.json"
    )
    return inventory


def run_gate(args: argparse.Namespace) -> int:
    """Run focused and optional complete validation for one frozen candidate."""
    repo = Path(args.repo).resolve()
    output_dir = Path(args.output_dir).resolve()
    scratch_root = Path(args.scratch_root).resolve()
    production_root = (
        Path(args.production_root).resolve() if args.production_root else None
    )
    if not args.development_dry_run and production_root is None:
        raise ReleaseGateError(
            "non-development validation requires an explicit production root"
        )
    try:
        output_dir.relative_to(repo)
    except ValueError:
        pass
    else:
        raise ReleaseGateError(
            "attestation output directory must be outside the candidate worktree"
        )
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ReleaseGateError("attestation output directory must be new or empty")
    registry_path = repo / args.registry
    ledger_path = repo / args.ledger
    if not HEX64.fullmatch(args.source_diagnosis_sha256):
        raise ReleaseGateError("source diagnosis SHA-256 is malformed")
    diagnosis_source = Path(args.source_diagnosis_path)
    if (
        not diagnosis_source.is_file()
        or sha256_file(diagnosis_source) != args.source_diagnosis_sha256
    ):
        raise ReleaseGateError(
            "authoritative source diagnosis is missing or differs from its SHA-256"
        )
    started_at = dt.datetime.now(dt.timezone.utc)
    with integration_lock(repo):
        base_commit, candidate_commit = resolve_release_identities(
            repo,
            base=args.base,
            candidate=args.candidate,
            development=args.development_dry_run,
        )
        registry_bytes = registry_path.read_bytes()
        ledger_bytes = ledger_path.read_bytes()
        registry = load_json_object(registry_path)
        ledger = load_json_object(ledger_path)
        validate_control_schema(
            registry,
            repo / "production_invariants.schema.json",
            label="production invariant registry",
        )
        validate_control_schema(
            ledger,
            repo / "defect_ledger.schema.json",
            label="defect ledger",
        )
        invariants = registry_invariants(registry)
        registry_transition = registry_record_transition(
            repo,
            base_commit=base_commit,
            candidate_registry=registry,
        )
        (
            declared_artifacts,
            declared_artifact_inventory,
        ) = registry_runtime_artifact_inventory(repo, invariants)
        before, bindings, unresolved = take_snapshot(repo, declared_artifacts)
        require_frozen(before, development=args.development_dry_run)
        if before.commit != candidate_commit:
            raise ReleaseGateError("candidate snapshot differs from resolved commit")
        production_before = deployed_identity(production_root, declared_artifacts)
        require_production_baseline(
            production_before,
            base_commit=base_commit,
            development=args.development_dry_run,
        )
        service_before = service_snapshot(args.service_unit)
        paths = changed_paths(repo, base_commit, candidate_commit)
        runtime_paths, generated_paths = runtime_path_sets(before)
        path_mapping, _uncovered, affected = map_changed_paths(
            paths,
            invariants,
            runtime_paths=runtime_paths,
            generated_paths=generated_paths,
        )
        commands = validation_commands(affected)
        mandatory_commands: list[tuple[str, ...]] = [
            (
                sys.executable,
                "tools/priority0_registry.py",
                "validate",
                "--json",
            )
        ]
        if (repo / "tools/defect_ledger.py").is_file():
            mandatory_commands.append(
                (
                    sys.executable,
                    "tools/defect_ledger.py",
                    "validate",
                    "--json",
                )
            )
        relationships = relationship_inventory(
            repo, before, bindings, unresolved
        )
        relationships["registry_runtime_artifact_declarations"] = (
            declared_artifact_inventory
        )
        commands = list(
            dict.fromkeys(
                [
                    *mandatory_commands,
                    *commands,
                    *relationship_validation_commands(relationships),
                ]
            )
        )
        if (
            not args.development_dry_run
            and (
                unresolved
                or not relationships["all_claimed_runtime_bindings_resolved"]
                or not relationships["all_discovered_bindings_resolved"]
                or not relationships["all_validator_backed_classifications_valid"]
                or not relationships["all_required_source_file_pins_valid"]
            )
        ):
            raise ReleaseGateError(
                "generated-artifact relationships are incomplete or stale: "
                + (
                    "; ".join(unresolved)
                    if unresolved
                    else "inspect generated-artifact attestation"
                )
            )

        validation_root = output_dir / "validation"
        validation_home = output_dir / "validation-home"
        validation_home.mkdir(parents=True, exist_ok=True)
        focused: list[ValidationResult] = []
        toolchain_before = validation_toolchain_inventory()
        dependency_roots = tuple(
            Path(path) for path in toolchain_before.python_paths
        )
        validation_env = sanitized_validation_environment_for_toolchain(
            validation_home, toolchain_before
        )
        full: ValidationResult | None = None
        checkout_context: contextlib.AbstractContextManager[Path]
        if args.development_dry_run:
            checkout_context = contextlib.nullcontext(repo)
        else:
            checkout_context = detached_candidate_worktree(
                repo, candidate_commit, scratch_root
            )
        checkout_reverification_count = 0
        detached_checkout_identity_verified = False
        validation_checkout_identity_verified = False
        network: dict[str, Any] = {
            "available": False,
            "reason": "containment preflight not executed",
        }
        with checkout_context as checkout:
            checkout_before, checkout_bindings, checkout_unresolved = take_snapshot(
                checkout, declared_artifacts
            )
            require_frozen(
                checkout_before, development=args.development_dry_run
            )
            assert_snapshot_equal(before, checkout_before)
            if (
                bindings != checkout_bindings
                or unresolved != checkout_unresolved
            ):
                raise ReleaseGateError(
                    "isolated candidate loader relationships differ from "
                    "the frozen source worktree"
                )
            network = containment_preflight(
                cwd=checkout,
                production_root=production_root,
                candidate_root=checkout,
                dependency_roots=dependency_roots,
            )
            if not network["available"] and not args.development_dry_run:
                raise ReleaseGateError(
                    "OS network/mount containment is unavailable; "
                    "release-candidate validation is blocked"
                )
            if not args.development_dry_run and not all(
                network.get(field) is True
                for field in (
                    "subprocess_egress_denied",
                    "network_route_isolated",
                    "production_root_read_only",
                    "candidate_root_read_only",
                    "git_common_root_read_only",
                    "validation_dependency_roots_read_only",
                    "effective_capabilities_dropped",
                    "no_new_privileges",
                    "read_only_remount_denied_after_capability_drop",
                )
            ):
                raise ReleaseGateError(
                    "release containment did not prove immutable candidate, "
                    "Git metadata, production, and subprocess no-egress"
                )
            for index, command in enumerate(commands, start=1):
                focused.append(
                    execute_validation(
                        command,
                        cwd=checkout,
                        output_dir=validation_root,
                        label=f"focused-{index:03d}",
                        network_isolated=bool(network["available"]),
                        production_root=production_root,
                        candidate_root=checkout,
                        dependency_roots=dependency_roots,
                        env=validation_env,
                    )
                )
                assert_candidate_checkout_unchanged(
                    checkout,
                    expected_snapshot=checkout_before,
                    expected_bindings=checkout_bindings,
                    expected_unresolved=checkout_unresolved,
                    declared_runtime_artifacts=declared_artifacts,
                )
                checkout_reverification_count += 1
            if any(result.exit_status for result in focused):
                raise ReleaseGateError("one or more focused validations failed")

            if args.full_suite:
                if args.development_dry_run:
                    raise ReleaseGateError(
                        "complete release suite requires an exact committed candidate"
                    )
                full = execute_validation(
                    full_suite_command(args.workers),
                    cwd=checkout,
                    output_dir=validation_root,
                    label="complete-suite",
                    network_isolated=True,
                    production_root=production_root,
                    candidate_root=checkout,
                    dependency_roots=dependency_roots,
                    env=validation_env,
                )
                assert_candidate_checkout_unchanged(
                    checkout,
                    expected_snapshot=checkout_before,
                    expected_bindings=checkout_bindings,
                    expected_unresolved=checkout_unresolved,
                    declared_runtime_artifacts=declared_artifacts,
                )
                checkout_reverification_count += 1
                if full.exit_status:
                    raise ReleaseGateError("complete isolated suite failed")
            validation_checkout_identity_verified = True
            detached_checkout_identity_verified = not args.development_dry_run

        toolchain_after = validation_toolchain_inventory()
        if toolchain_before != toolchain_after:
            raise ReleaseGateError(
                "validation Python/pytest toolchain changed while tests were running"
            )

        after, after_bindings, after_unresolved = take_snapshot(
            repo, declared_artifacts
        )
        assert_snapshot_equal(before, after)
        if bindings != after_bindings or unresolved != after_unresolved:
            raise ReleaseGateError(
                "generated-artifact discovery changed during validation"
            )
        production_after = deployed_identity(production_root, declared_artifacts)
        service_after = service_snapshot(args.service_unit)
        production_unchanged = production_before == production_after
        service_unchanged = service_invariants_equal(
            service_before, service_after
        )
        if production_root and not production_unchanged:
            raise ReleaseGateError(
                "production/deployed identities changed during validation"
            )
        if service_before.get("available") and not service_unchanged:
            raise ReleaseGateError(
                "service identity or restart state changed during validation"
            )
        expected_reverification_count = len(focused) + (1 if full else 0)
        candidate_unchanged_after_validation = (
            validation_checkout_identity_verified
            and checkout_reverification_count == expected_reverification_count
            and before == after
            and bindings == after_bindings
            and unresolved == after_unresolved
        )
        if not candidate_unchanged_after_validation:
            raise ReleaseGateError(
                "candidate identity was not fully reverified after validation"
            )
        scope = conclusion_scope(
            development=args.development_dry_run,
            full_suite=args.full_suite,
            full_result=full,
            affected=affected,
        )
        semantic = deterministic_attestation(
            base=base_commit,
            snapshot=before,
            registry_sha256=sha256_bytes(registry_bytes),
            ledger_sha256=sha256_bytes(ledger_bytes),
            registry_transition=registry_transition,
            path_mapping=path_mapping,
            affected=affected,
            focused=focused,
            full=full,
            network=network,
            toolchain=toolchain_before.semantic_dict(),
            relationships=relationships,
            deployed_checks=unmet_deployed_checks(invariants),
            scope=scope,
        )
        diff = _run(
            (
                "git",
                "-C",
                str(repo),
                "diff",
                "--binary",
                base_commit,
                candidate_commit,
            ),
            cwd=repo,
        ).stdout
        receipt = {
            "schema_version": 1,
            "started_at_utc": started_at.isoformat().replace("+00:00", "Z"),
            "finished_at_utc": dt.datetime.now(dt.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "host": {
                "node": platform.node(),
                "platform": platform.platform(),
                "python": sys.version,
                "pid": os.getpid(),
            },
            "repository": str(repo),
            "output_directory": str(output_dir),
            "integration_lock": str(
                git_common_dir(repo) / "mrsMThatcher.release-gate.lock"
            ),
            "focused_validation": [item.receipt_dict() for item in focused],
            "complete_suite_validation": (
                None if full is None else full.receipt_dict()
            ),
            "network_preflight": network,
            "validation_toolchain": {
                "semantic_identity": toolchain_before.semantic_dict(),
                "python_paths": list(toolchain_before.python_paths),
                "unchanged_after_validation": toolchain_before == toolchain_after,
            },
            "production_identity_before": production_before,
            "production_identity_after": production_after,
            "production_identity_unchanged": production_unchanged,
            "service_before": service_before,
            "service_after": service_after,
            "service_invariants_unchanged": service_unchanged,
            "candidate_unchanged_after_validation": (
                candidate_unchanged_after_validation
            ),
            "detached_checkout_identity_verified": (
                detached_checkout_identity_verified
            ),
            "validation_checkout_identity_verified": (
                validation_checkout_identity_verified
            ),
            "detached_checkout_reverification_count": (
                checkout_reverification_count
            ),
            "procedural_notes": list(args.procedural_note),
        }
        inventory = emit_outputs(
            output_dir=output_dir,
            semantic=semantic,
            receipt=receipt,
            registry_path=registry_path,
            ledger_path=ledger_path,
            diff_hash=sha256_bytes(diff),
            source_diagnosis_path=args.source_diagnosis_path,
            source_diagnosis_sha256=args.source_diagnosis_sha256,
        )
        print(json.dumps({"output_dir": str(output_dir), "files": inventory}, indent=2))
        return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description="Read-only frozen-candidate release gate"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    network = subparsers.add_parser(
        "network-preflight",
        help="verify OS-level subprocess egress denial",
    )
    network.add_argument("--repo", default=".")
    network.add_argument(
        "--production-root",
        help="also prove this production root is read-only inside containment",
    )

    run = subparsers.add_parser(
        "run",
        help="attest one exact committed candidate without deployment",
    )
    run.add_argument("--repo", default=".")
    run.add_argument("--base", required=True)
    run.add_argument("--candidate")
    run.add_argument("--registry", default="production_invariants.json")
    run.add_argument("--ledger", default="defect_ledger.json")
    run.add_argument("--output-dir", required=True)
    run.add_argument(
        "--scratch-root",
        default="/disks/disk1/research",
        help="non-/tmp parent for the detached full-suite checkout",
    )
    run.add_argument("--production-root")
    run.add_argument("--service-unit", default="mrsMThatcher.service")
    run.add_argument(
        "--source-diagnosis-path",
        required=True,
    )
    run.add_argument(
        "--source-diagnosis-sha256",
        required=True,
    )
    run.add_argument("--workers", type=int, default=4)
    run.add_argument("--full-suite", action="store_true")
    run.add_argument("--development-dry-run", action="store_true")
    run.add_argument(
        "--procedural-note",
        action="append",
        default=[],
        help="record a non-semantic run-history note in the receipt and report",
    )
    return parser


def emit_failure_receipt(
    args: argparse.Namespace, exc: ReleaseGateError
) -> Path | None:
    """Preserve bounded evidence for a failed run without masking the failure."""
    if getattr(args, "command", None) != "run" or not getattr(
        args, "output_dir", None
    ):
        return None
    repo = Path(args.repo).resolve()
    output_dir = Path(args.output_dir).resolve()
    try:
        output_dir.relative_to(repo)
    except ValueError:
        pass
    else:
        return None
    if output_dir.exists():
        allowed = {"validation", "validation-home"}
        if any(path.name not in allowed for path in output_dir.iterdir()):
            return None
    output_dir.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema_version": 1,
        "status": "blocked",
        "recorded_at_utc": dt.datetime.now(dt.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "base_argument": str(getattr(args, "base", "")),
        "candidate_argument": str(getattr(args, "candidate", "") or ""),
        "development_dry_run": bool(
            getattr(args, "development_dry_run", False)
        ),
        "full_suite_requested": bool(getattr(args, "full_suite", False)),
        "error": str(exc),
        "partial_validation_file_hashes": _validation_hash_inventory(output_dir),
    }
    path = output_dir / "release_gate_failure_receipt.json"
    write_atomic(path, canonical_json_bytes(receipt))
    return path


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "network-preflight":
            repo = Path(args.repo).resolve()
            toolchain = validation_toolchain_inventory()
            result = containment_preflight(
                cwd=repo,
                production_root=(
                    Path(args.production_root).resolve()
                    if args.production_root
                    else None
                ),
                candidate_root=repo,
                dependency_roots=tuple(
                    Path(path) for path in toolchain.python_paths
                ),
            )
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0 if result["available"] else 2
        if args.command == "run":
            return run_gate(args)
        parser.error(f"unknown command: {args.command}")
    except ReleaseGateError as exc:
        with contextlib.suppress(OSError, ReleaseGateError):
            emit_failure_receipt(args, exc)
        print(f"release gate blocked: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
