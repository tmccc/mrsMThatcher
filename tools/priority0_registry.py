#!/usr/bin/env python3
"""Validate and render the canonical Priority-0 production invariant registry."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import site
import subprocess
import sys
from typing import Any, Iterable, Mapping, Sequence

if __package__:
    from tools import strict_json
else:
    # Direct-script execution must use the sibling release-control parser,
    # regardless of any unrelated installed package named ``tools``.
    import strict_json  # type: ignore[no-redef]


DEFAULT_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = Path("production_invariants.json")
DEFAULT_SCHEMA = Path("production_invariants.schema.json")
DEFAULT_MARKDOWN = Path("PRODUCTION_INVARIANTS.md")

CATEGORY_TITLES = {
    "eligibility_evidence": "Eligibility and evidence",
    "transaction": "Durable transactions",
    "configuration_process": "Configuration and process",
    "test_deployment": "Testing and deployment",
}
IMPLEMENTATION_STATUSES = ("implemented", "partial", "missing")
VERIFICATION_STATUSES = ("verified", "partial", "unverified")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
VALIDATION_IDS = frozenset({"pytest", "registry_validate"})
TEST_SELECTOR = re.compile(
    r"^tests/[A-Za-z0-9_./-]+\.py"
    r"(?:::[A-Za-z_][A-Za-z0-9_]*(?:\[[A-Za-z0-9_.:/=-]+\])?)*$"
)
GIT_METADATA_TIMEOUT_SECONDS = 10
MAXIMUM_HISTORICAL_TEST_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class ValidationReport:
    """Describe structural errors and honestly unresolved registry entries."""

    schema_backend: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    implemented: tuple[str, ...]
    partial: tuple[str, ...]
    unsupported: tuple[str, ...]
    verified: tuple[str, ...]
    partially_verified: tuple[str, ...]
    unverified: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """Return whether the registry is structurally valid and synchronized."""
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        """Return a stable JSON-serializable representation."""
        return {
            "ok": self.ok,
            "schema_backend": self.schema_backend,
            "counts": {
                "implemented": len(self.implemented),
                "partial": len(self.partial),
                "unsupported": len(self.unsupported),
                "verified": len(self.verified),
                "partially_verified": len(self.partially_verified),
                "unverified": len(self.unverified),
            },
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "implemented": list(self.implemented),
            "partial": list(self.partial),
            "unsupported": list(self.unsupported),
            "verified": list(self.verified),
            "partially_verified": list(self.partially_verified),
            "unverified": list(self.unverified),
        }


def load_json_document(path: Path) -> Any:
    """Load one strict UTF-8 JSON document."""

    return strict_json.load(path)


def sha256_file(path: Path) -> str:
    """Return the SHA-256 of one file without following registry indirection."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_blob_sha256(
    repository_root: Path, commit: str, relative: str
) -> tuple[str | None, str | None]:
    """Hash one historical Git blob without changing repository state."""
    if HEX40.fullmatch(commit) is None:
        return None, "historical commit is malformed"
    path, path_error = _safe_repository_path(repository_root, relative)
    if path_error or path is None:
        return None, path_error or "historical path is invalid"
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repository_root),
            "show",
            f"{commit}:{relative}",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        return None, "historical Git blob is unavailable"
    return hashlib.sha256(result.stdout).hexdigest(), None


def _git_commit_tree(
    repository_root: Path,
    commit: str,
) -> tuple[str | None, str | None]:
    """Return the exact tree for one existing commit without reading history."""

    if HEX40.fullmatch(commit) is None:
        return None, "historical commit is malformed"
    try:
        exists = subprocess.run(
            [
                "git",
                "-C",
                str(repository_root),
                "cat-file",
                "-e",
                f"{commit}^{{commit}}",
            ],
            shell=False,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=GIT_METADATA_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"historical commit inspection failed: {type(exc).__name__}"
    if exists.returncode:
        return None, "historical commit does not exist"
    try:
        resolved = subprocess.run(
            [
                "git",
                "-C",
                str(repository_root),
                "rev-parse",
                "--verify",
                f"{commit}^{{tree}}",
            ],
            shell=False,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=GIT_METADATA_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"historical tree inspection failed: {type(exc).__name__}"
    if resolved.returncode or len(resolved.stdout) > 64:
        return None, "historical commit tree is unavailable"
    tree = resolved.stdout.decode("ascii", errors="strict").strip()
    if HEX40.fullmatch(tree) is None:
        return None, "historical commit tree is malformed"
    return tree, None


def _git_blob_bytes_bounded(
    repository_root: Path,
    commit: str,
    relative: str,
    *,
    maximum_bytes: int = MAXIMUM_HISTORICAL_TEST_BYTES,
) -> tuple[bytes | None, str | None]:
    """Read one historical blob only after Git proves its bounded byte size."""

    path, path_error = _safe_repository_path(repository_root, relative)
    if path_error is not None or path is None:
        return None, path_error or "historical path is invalid"
    specifier = f"{commit}:{relative}"
    try:
        sized = subprocess.run(
            ["git", "-C", str(repository_root), "cat-file", "-s", specifier],
            shell=False,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=GIT_METADATA_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"historical test path inspection failed: {type(exc).__name__}"
    if sized.returncode or len(sized.stdout) > 32:
        return None, f"historical test file does not exist: {relative}"
    try:
        size = int(sized.stdout.strip())
    except ValueError:
        return None, f"historical test size is malformed: {relative}"
    if size < 0 or size > maximum_bytes:
        return None, (
            f"historical test file exceeds {maximum_bytes} bytes: {relative}"
        )
    try:
        loaded = subprocess.run(
            ["git", "-C", str(repository_root), "cat-file", "blob", specifier],
            shell=False,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=GIT_METADATA_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"historical test read failed: {type(exc).__name__}"
    if loaded.returncode or len(loaded.stdout) != size:
        return None, f"historical test blob read is incomplete: {relative}"
    return loaded.stdout, None


def _json_type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _path_label(parts: Sequence[Any]) -> str:
    label = "$"
    for part in parts:
        if isinstance(part, int):
            label += f"[{part}]"
        else:
            label += f".{part}"
    return label


def _resolve_local_ref(root_schema: Mapping[str, Any], reference: str) -> Any:
    if not reference.startswith("#/"):
        raise ValueError(f"fallback validator supports only local references: {reference}")
    current: Any = root_schema
    for encoded in reference[2:].split("/"):
        key = encoded.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, Mapping) or key not in current:
            raise ValueError(f"unresolvable schema reference: {reference}")
        current = current[key]
    return current


def _fallback_schema_errors(
    instance: Any,
    schema: Mapping[str, Any],
) -> list[str]:
    """Validate the schema subset used here when jsonschema is unavailable."""
    errors: list[str] = []

    def visit(
        value: Any,
        rule: Mapping[str, Any],
        parts: tuple[Any, ...],
        root_schema: Mapping[str, Any],
    ) -> None:
        if "$ref" in rule:
            try:
                target = _resolve_local_ref(root_schema, str(rule["$ref"]))
            except ValueError as exc:
                errors.append(f"{_path_label(parts)}: {exc}")
                return
            if not isinstance(target, Mapping):
                errors.append(
                    f"{_path_label(parts)}: schema reference does not resolve to an object"
                )
                return
            visit(value, target, parts, root_schema)
            return

        if "const" in rule and value != rule["const"]:
            errors.append(
                f"{_path_label(parts)}: {value!r} is not the required constant "
                f"{rule['const']!r}"
            )
        if "enum" in rule and value not in rule["enum"]:
            errors.append(
                f"{_path_label(parts)}: {value!r} is not one of {rule['enum']!r}"
            )

        expected = rule.get("type")
        type_matches = True
        if isinstance(expected, str):
            type_matches = _json_type_matches(value, expected)
        elif isinstance(expected, list) and all(
            isinstance(item, str) for item in expected
        ):
            type_matches = any(
                _json_type_matches(value, item) for item in expected
            )
        if not type_matches:
            errors.append(
                f"{_path_label(parts)}: expected {expected}, got "
                f"{type(value).__name__}"
            )
            return

        if isinstance(value, str):
            minimum = rule.get("minLength")
            if isinstance(minimum, int) and len(value) < minimum:
                errors.append(
                    f"{_path_label(parts)}: string is shorter than {minimum}"
                )
            pattern = rule.get("pattern")
            if isinstance(pattern, str):
                try:
                    matched = re.search(pattern, value)
                except re.error as exc:
                    errors.append(
                        f"{_path_label(parts)}: invalid schema pattern {pattern!r}: {exc}"
                    )
                else:
                    if matched is None:
                        errors.append(
                            f"{_path_label(parts)}: {value!r} does not match {pattern!r}"
                        )

        if isinstance(value, list):
            minimum = rule.get("minItems")
            if isinstance(minimum, int) and len(value) < minimum:
                errors.append(
                    f"{_path_label(parts)}: array has fewer than {minimum} items"
                )
            if rule.get("uniqueItems") is True:
                encoded = [
                    json.dumps(item, sort_keys=True, separators=(",", ":"))
                    for item in value
                ]
                if len(encoded) != len(set(encoded)):
                    errors.append(f"{_path_label(parts)}: array items are not unique")
            item_rule = rule.get("items")
            if isinstance(item_rule, Mapping):
                for index, item in enumerate(value):
                    visit(item, item_rule, (*parts, index), root_schema)

        if isinstance(value, dict):
            required = rule.get("required", [])
            if isinstance(required, list):
                for key in required:
                    if key not in value:
                        errors.append(
                            f"{_path_label(parts)}: required property {key!r} is missing"
                        )
            properties = rule.get("properties", {})
            if isinstance(properties, Mapping):
                for key, child_rule in properties.items():
                    if key in value and isinstance(child_rule, Mapping):
                        visit(value[key], child_rule, (*parts, key), root_schema)
                if rule.get("additionalProperties") is False:
                    for key in value:
                        if key not in properties:
                            errors.append(
                                f"{_path_label((*parts, key))}: additional property "
                                "is not permitted"
                            )

    visit(instance, schema, (), schema)
    return errors


def schema_validation_errors(
    instance: Any,
    schema: Mapping[str, Any],
    *,
    force_fallback: bool = False,
) -> tuple[str, list[str]]:
    """Return the schema backend and sorted validation errors."""
    if not force_fallback:
        try:
            import jsonschema
        except ImportError:
            pass
        else:
            try:
                jsonschema.Draft7Validator.check_schema(schema)
                validator = jsonschema.Draft7Validator(schema)
                errors = []
                for error in sorted(
                    validator.iter_errors(instance),
                    key=lambda item: tuple(str(part) for part in item.absolute_path),
                ):
                    errors.append(
                        f"{_path_label(tuple(error.absolute_path))}: {error.message}"
                    )
                return "jsonschema.Draft7Validator", errors
            except Exception as exc:
                return "jsonschema.Draft7Validator", [
                    f"$: schema validation could not run: {type(exc).__name__}: {exc}"
                ]
    return "built-in-draft7-subset", _fallback_schema_errors(instance, schema)


def _safe_repository_path(repository_root: Path, raw_path: str) -> tuple[Path | None, str | None]:
    if not isinstance(raw_path, str) or not raw_path:
        return None, "path is not a non-empty string"
    pure = PurePosixPath(raw_path)
    if pure.is_absolute() or ".." in pure.parts:
        return None, f"path escapes the repository: {raw_path!r}"
    if any(character in raw_path for character in "*?["):
        return None, f"path must be explicit rather than a glob: {raw_path!r}"
    candidate = repository_root.joinpath(*pure.parts)
    try:
        candidate.resolve(strict=False).relative_to(repository_root.resolve())
    except ValueError:
        return None, f"path resolves outside the repository: {raw_path!r}"
    return candidate, None


def _named_ast_child(nodes: Iterable[ast.AST], name: str) -> ast.AST | None:
    for node in nodes:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                return node
    return None


def test_node_exists(
    repository_root: Path,
    nodeid: str,
    *,
    tree_cache: dict[Path, tuple[ast.Module | None, str]] | None = None,
) -> tuple[bool, str]:
    """Check the test file and statically reject obvious non-test helpers.

    This is only a cheap structural preflight.  ``validate_registry`` also
    performs one batched pytest collection and treats that result as the
    authority for exact parameter IDs and custom collection behaviour.
    """
    if not isinstance(nodeid, str):
        return False, "test node is not a string"
    parts = nodeid.split("::")
    path, path_error = _safe_repository_path(repository_root, parts[0])
    if path_error is not None or path is None:
        return False, path_error or "invalid test path"
    if not path.is_file():
        return False, f"test file does not exist: {parts[0]}"
    if len(parts) == 1:
        return True, ""
    cached = tree_cache.get(path) if tree_cache is not None else None
    if cached is None:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            parse_error = f"test file cannot be parsed: {parts[0]}: {exc}"
            if tree_cache is not None:
                tree_cache[path] = (None, parse_error)
            return False, parse_error
        if tree_cache is not None:
            tree_cache[path] = (tree, "")
    else:
        tree, parse_error = cached
        if tree is None:
            return False, parse_error
    current_nodes: Iterable[ast.AST] = tree.body
    for index, raw_name in enumerate(parts[1:]):
        name = raw_name.split("[", 1)[0]
        node = _named_ast_child(current_nodes, name)
        if node is None:
            return False, f"test node does not exist: {nodeid}"
        if index == 0:
            if isinstance(node, ast.ClassDef):
                if not name.startswith("Test"):
                    return False, f"test node is not pytest-collectable: {nodeid}"
            elif not name.startswith("test"):
                return False, f"test node is not pytest-collectable: {nodeid}"
        elif isinstance(node, ast.ClassDef):
            if not name.startswith("Test"):
                return False, f"test node is not pytest-collectable: {nodeid}"
        elif not name.startswith("test"):
            return False, f"test node is not pytest-collectable: {nodeid}"
        current_nodes = getattr(node, "body", ())
    return True, ""


def historical_test_selector_exists(
    repository_root: Path,
    commit: str,
    selector: str,
    *,
    blob_cache: dict[tuple[str, str], tuple[bytes | None, str]] | None = None,
    tree_cache: dict[tuple[str, str], tuple[ast.Module | None, str]] | None = None,
) -> tuple[bool, str]:
    """Prove that one current enforcement selector exists in an exact commit.

    Historical verification is deliberately structural.  It reads at most one
    bounded Git blob per referenced test path and never imports or executes the
    historical candidate.
    """

    if not isinstance(selector, str):
        return False, "historical test selector is not a string"
    parts = selector.split("::")
    relative = parts[0]
    blob_key = (commit, relative)
    cached_blob = blob_cache.get(blob_key) if blob_cache is not None else None
    if cached_blob is None:
        data, error = _git_blob_bytes_bounded(
            repository_root,
            commit,
            relative,
        )
        cached_blob = (data, error or "")
        if blob_cache is not None:
            blob_cache[blob_key] = cached_blob
    data, blob_error = cached_blob
    if data is None:
        return False, blob_error
    if len(parts) == 1:
        return True, ""

    cached_tree = tree_cache.get(blob_key) if tree_cache is not None else None
    if cached_tree is None:
        try:
            source = data.decode("utf-8")
            tree = ast.parse(source, filename=f"{commit}:{relative}")
        except (UnicodeDecodeError, SyntaxError) as exc:
            parse_error = (
                f"historical test file cannot be parsed: {relative}: "
                f"{type(exc).__name__}"
            )
            cached_tree = (None, parse_error)
        else:
            cached_tree = (tree, "")
        if tree_cache is not None:
            tree_cache[blob_key] = cached_tree
    tree, parse_error = cached_tree
    if tree is None:
        return False, parse_error

    current_nodes: Iterable[ast.AST] = tree.body
    for index, raw_name in enumerate(parts[1:]):
        name = raw_name.split("[", 1)[0]
        node = _named_ast_child(current_nodes, name)
        if node is None:
            return False, f"historical test node does not exist: {selector}"
        if index == 0:
            if isinstance(node, ast.ClassDef):
                if not name.startswith("Test"):
                    return False, (
                        f"historical test node is not pytest-collectable: {selector}"
                    )
            elif not name.startswith("test"):
                return False, (
                    f"historical test node is not pytest-collectable: {selector}"
                )
        elif isinstance(node, ast.ClassDef):
            if not name.startswith("Test"):
                return False, (
                    f"historical test node is not pytest-collectable: {selector}"
                )
        elif not name.startswith("test"):
            return False, (
                f"historical test node is not pytest-collectable: {selector}"
            )
        current_nodes = getattr(node, "body", ())
    return True, ""


def _sanitized_pytest_environment() -> dict[str, str]:
    """Return an environment without inherited Python/pytest path injection."""
    environment = os.environ.copy()
    for name in (
        "COVERAGE_PROCESS_START",
        "PYTHONBREAKPOINT",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONUSERBASE",
        "PYTEST_ADDOPTS",
        "PYTEST_PLUGINS",
    ):
        environment.pop(name, None)
    environment.update(
        {
            "PYTHONHASHSEED": "0",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }
    )
    user_site = site.getusersitepackages()
    user_site_paths = [user_site] if isinstance(user_site, str) else list(user_site)
    explicit_dependency_roots = [
        str(Path(item).resolve())
        for item in user_site_paths
        if Path(item).is_dir()
    ]
    if explicit_dependency_roots:
        # ``-s`` prevents Python from processing user-site ``.pth`` files.  The
        # explicit root keeps installed test dependencies importable without
        # inheriting arbitrary source paths injected by those files.
        environment["PYTHONPATH"] = os.pathsep.join(explicit_dependency_roots)
    return environment


def _collect_pytest_nodes(
    repository_root: Path,
    selectors: Iterable[str],
) -> tuple[frozenset[str], str | None]:
    """Collect all referenced files in one sanitized pytest subprocess."""
    files: set[str] = set()
    for selector in selectors:
        relative = selector.split("::", 1)[0]
        path, path_error = _safe_repository_path(repository_root, relative)
        if path_error is not None or path is None or not path.is_file():
            continue
        files.add(relative)
    if not files:
        return frozenset(), None

    command = [
        sys.executable,
        "-s",
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        "--color=no",
        "-p",
        "no:cacheprovider",
        *sorted(files),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=repository_root,
            env=_sanitized_pytest_environment(),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return frozenset(), (
            "batched pytest collection could not run: "
            f"{type(exc).__name__}"
        )
    if completed.returncode:
        stdout_sha = hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest()
        stderr_sha = hashlib.sha256(completed.stderr.encode("utf-8")).hexdigest()
        return frozenset(), (
            "batched pytest collection failed with exit status "
            f"{completed.returncode} (stdout_sha256={stdout_sha}, "
            f"stderr_sha256={stderr_sha})"
        )

    collected = frozenset(
        line.strip()
        for line in completed.stdout.splitlines()
        if "::" in line and line.split("::", 1)[0] in files
    )
    return collected, None


def _selector_matches_collection(
    selector: str,
    collected: frozenset[str],
) -> bool:
    """Return whether an exact selector denotes at least one collected test."""
    if "::" not in selector:
        prefix = f"{selector}::"
        return any(nodeid.startswith(prefix) for nodeid in collected)
    if "[" in selector.rsplit("::", 1)[-1]:
        return selector in collected
    return selector in collected or any(
        nodeid.startswith(f"{selector}[")
        or nodeid.startswith(f"{selector}::")
        for nodeid in collected
    )


def _markdown_escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _status_ids(registry: Mapping[str, Any], field: str, value: str) -> tuple[str, ...]:
    invariants = registry.get("invariants", [])
    if not isinstance(invariants, list):
        return ()
    selected = []
    for invariant in invariants:
        if not isinstance(invariant, Mapping):
            continue
        actual: Any
        if field == "verification":
            verification = invariant.get("verification", {})
            actual = verification.get("status") if isinstance(verification, Mapping) else None
        else:
            actual = invariant.get(field)
        if actual == value and isinstance(invariant.get("id"), str):
            selected.append(str(invariant["id"]))
    return tuple(sorted(selected))


def render_markdown(registry: Mapping[str, Any]) -> str:
    """Render deterministic human-readable Markdown from canonical JSON."""
    invariants = registry.get("invariants", [])
    if not isinstance(invariants, list):
        raise ValueError("registry invariants must be an array")
    lines = [
        "<!-- Generated by tools/priority0_registry.py from production_invariants.json. -->",
        "<!-- Edit the JSON registry, then run: python3 tools/priority0_registry.py render --write -->",
        "",
        "# Production invariants",
        "",
        str(registry.get("scope") or ""),
        "",
        f"- Registry: `{registry.get('registry_id', '')}` version `{registry.get('registry_version', '')}`",
        f"- Production baseline: `{registry.get('baseline_commit', '')}`",
        f"- Reviewed: `{registry.get('reviewed_on', '')}`",
        "",
        "## Status vocabulary",
        "",
        "| Implementation status | Meaning |",
        "|---|---|",
    ]
    status_definitions = registry.get("status_definitions", {})
    for status in IMPLEMENTATION_STATUSES:
        meaning = status_definitions.get(status, "") if isinstance(status_definitions, Mapping) else ""
        lines.append(f"| `{status}` | {_markdown_escape(meaning)} |")
    lines.extend(
        [
            "",
            "| Verification status | Meaning |",
            "|---|---|",
        ]
    )
    verification_definitions = registry.get("verification_definitions", {})
    for status in VERIFICATION_STATUSES:
        meaning = (
            verification_definitions.get(status, "")
            if isinstance(verification_definitions, Mapping)
            else ""
        )
        lines.append(f"| `{status}` | {_markdown_escape(meaning)} |")

    classifications = registry.get("generated_artifact_classifications", [])
    lines.extend(
        [
            "",
            "## Generated-artifact classifications",
            "",
            "| ID | Artifact | Classification | Runtime relationship required | Current companion | Bound manifest |",
            "|---|---|---|---:|---:|---|",
        ]
    )
    for record in classifications:
        if not isinstance(record, Mapping):
            continue
        lines.append(
            f"| `{record.get('id', '')}` | `{record.get('artifact', '')}` | "
            f"`{record.get('classification', '')}` | "
            f"{'yes' if record.get('runtime_relationship_required') else 'no'} | "
            f"{'yes' if record.get('current_companion') else 'no'} | "
            f"`{record.get('bound_artifact_sha256', '')}` |"
        )
    for record in classifications:
        if not isinstance(record, Mapping):
            continue
        lines.append(
            f"\n- **{record.get('id', '')} rationale:** {record.get('reason', '')}"
        )
        lines.append(
            f"- Observed current bound-artifact SHA-256: "
            f"`{record.get('observed_current_bound_artifact_sha256', '')}`"
        )
        lines.append(
            f"- Historical build commit: `{record.get('bound_commit', '')}`"
        )
        lines.append(
            f"- Policy version: `{record.get('policy_version', '')}`; "
            f"owner: `{record.get('owner', '')}`"
        )
        for label, field in (
            ("Builder evidence", "builder_evidence"),
            ("Runtime-loader evidence", "runtime_loader_evidence"),
        ):
            for evidence in record.get(field, []):
                if not isinstance(evidence, Mapping):
                    continue
                lines.append(
                    f"- {label}: `{evidence.get('type', '')}` "
                    f"`{evidence.get('reference', '')}` — "
                    f"{evidence.get('claim', '')}"
                )
        for nodeid in record.get("validator_tests", []):
            lines.append(f"- Validator: `{nodeid}`")

    expected_skips = registry.get("expected_full_suite_skips", [])
    lines.extend(
        [
            "",
            "## Expected complete-suite skips under outer containment",
            "",
            "These declarations apply only when the complete suite is already nested "
            "inside the release gate's successfully preflighted containment. Any "
            "unlisted skip, reason mismatch, or skip outside that environment remains "
            "unexplained.",
            "",
            "| Test node | Reason code | Invariants | Blocks qualification |",
            "|---|---|---|---:|",
        ]
    )
    for record in expected_skips:
        if not isinstance(record, Mapping):
            continue
        invariant_ids = ", ".join(
            f"`{value}`" for value in record.get("invariant_ids", [])
        )
        lines.append(
            f"| `{record.get('node_id', '')}` | `{record.get('reason_code', '')}` | "
            f"{invariant_ids} | "
            f"{'yes' if record.get('prevents_release_qualification') else 'no'} |"
        )
    for record in expected_skips:
        if not isinstance(record, Mapping):
            continue
        lines.append(
            f"\n- **{record.get('node_id', '')} condition:** "
            f"`{record.get('condition', '')}`"
        )
        lines.append(
            f"- Allowed reason regex: `{record.get('reason_regex', '')}`"
        )
        lines.append(f"- Justification: {record.get('justification', '')}")
        lines.append(
            f"- Compensating evidence: {record.get('compensating_evidence', '')}"
        )
    implementation_counts = {
        status: sum(
            isinstance(item, Mapping) and item.get("implementation_status") == status
            for item in invariants
        )
        for status in IMPLEMENTATION_STATUSES
    }
    verification_counts = {
        status: sum(
            isinstance(item, Mapping)
            and isinstance(item.get("verification"), Mapping)
            and item["verification"].get("status") == status
            for item in invariants
        )
        for status in VERIFICATION_STATUSES
    }
    lines.extend(
        [
            "",
            "## Current support summary",
            "",
            "| Total | Implemented | Partial | Missing / unsupported | Verified | Partially verified | Unverified |",
            "|---:|---:|---:|---:|---:|---:|---:|",
            (
                f"| {len(invariants)} | {implementation_counts['implemented']} | "
                f"{implementation_counts['partial']} | {implementation_counts['missing']} | "
                f"{verification_counts['verified']} | {verification_counts['partial']} | "
                f"{verification_counts['unverified']} |"
            ),
            "",
            "Missing means the invariant is explicitly unsupported, not silently assumed. "
            "Partial and unverified entries remain release-review inputs.",
        ]
    )

    for category, title in CATEGORY_TITLES.items():
        category_items = [
            item
            for item in invariants
            if isinstance(item, Mapping) and item.get("category") == category
        ]
        lines.extend(
            [
                "",
                f"## {title}",
                "",
                "| ID | Contract | Implementation | Verification | Criticality |",
                "|---|---|---|---|---|",
            ]
        )
        for item in category_items:
            verification = item.get("verification", {})
            verification_status = (
                verification.get("status", "")
                if isinstance(verification, Mapping)
                else ""
            )
            lines.append(
                f"| `{item.get('id', '')}` | {_markdown_escape(item.get('title', ''))} | "
                f"`{item.get('implementation_status', '')}` | `{verification_status}` | "
                f"`{item.get('criticality', '')}` |"
            )

        for item in category_items:
            verification = item.get("verification", {})
            enforcement = item.get("enforcement", {})
            failure_mode = item.get("failure_mode", {})
            artifact_set = item.get("runtime_consumed_artifacts", {})
            full_suite = item.get("full_suite_relevance", {})
            deployed_checks = item.get(
                "required_production_deployed_path_checks",
                {},
            )
            last_commit = item.get("last_verified_commit", {})
            last_tree = item.get("last_verified_tree", {})
            residual_risk = item.get("accepted_residual_risk", {})
            lines.extend(
                [
                    "",
                    f"### {item.get('id', '')}: {item.get('title', '')}",
                    "",
                    f"**Invariant.** {item.get('statement', '')}",
                    "",
                    f"**Rationale.** {item.get('rationale', '')}",
                    "",
                    f"**Owner subsystem.** `{item.get('owner_subsystem', '')}`",
                    "",
                    f"**Failure consequence.** {item.get('failure_if_violated', '')}",
                    "",
                    (
                        f"**Failure mode.** `{failure_mode.get('mode', '')}` — "
                        f"{failure_mode.get('explanation', '')}"
                        if isinstance(failure_mode, Mapping)
                        else "**Failure mode.** Invalid registry entry."
                    ),
                    "",
                    (
                        f"**Status.** `{item.get('implementation_status', '')}` — "
                        f"{item.get('status_rationale', '')}"
                    ),
                    "",
                    (
                        f"**Verification.** `{verification.get('status', '')}` — "
                        f"{verification.get('rationale', '')}"
                        if isinstance(verification, Mapping)
                        else "**Verification.** Invalid registry entry."
                    ),
                    "",
                    "**Preconditions.**",
                    "",
                ]
            )
            for precondition in item.get("preconditions", []):
                lines.append(f"- {precondition}")

            artifact_status = (
                artifact_set.get("status", "")
                if isinstance(artifact_set, Mapping)
                else ""
            )
            artifact_explanation = (
                artifact_set.get("explanation", "")
                if isinstance(artifact_set, Mapping)
                else "Invalid registry entry."
            )
            lines.extend(
                [
                    "",
                    (
                        f"**Runtime-consumed artifacts.** `{artifact_status}` — "
                        f"{artifact_explanation}"
                    ),
                    "",
                ]
            )
            artifact_values = (
                artifact_set.get("artifacts", [])
                if isinstance(artifact_set, Mapping)
                else []
            )
            if artifact_values:
                for artifact in artifact_values:
                    lines.append(f"- `{artifact}`")
            else:
                lines.append("- None recorded.")

            full_suite_status = (
                full_suite.get("status", "")
                if isinstance(full_suite, Mapping)
                else ""
            )
            full_suite_explanation = (
                full_suite.get("explanation", "")
                if isinstance(full_suite, Mapping)
                else "Invalid registry entry."
            )
            lines.extend(
                [
                    "",
                    (
                        f"**Full-suite relevance.** `{full_suite_status}` — "
                        f"{full_suite_explanation}"
                    ),
                    "",
                ]
            )

            deployed_status = (
                deployed_checks.get("status", "")
                if isinstance(deployed_checks, Mapping)
                else ""
            )
            deployed_explanation = (
                deployed_checks.get("explanation", "")
                if isinstance(deployed_checks, Mapping)
                else "Invalid registry entry."
            )
            lines.extend(
                [
                    (
                        f"**Required production deployed-path checks.** "
                        f"`{deployed_status}` — {deployed_explanation}"
                    ),
                    "",
                ]
            )
            check_values = (
                deployed_checks.get("checks", [])
                if isinstance(deployed_checks, Mapping)
                else []
            )
            if check_values:
                for check in check_values:
                    lines.append(f"- {check}")
            else:
                lines.append("- None established.")

            lines.extend(["", "**Evidence references.**", ""])
            evidence_references = item.get("evidence_references", [])
            for reference in evidence_references:
                if not isinstance(reference, Mapping):
                    continue
                lines.append(
                    f"- `{reference.get('type', '')}` "
                    f"`{reference.get('reference', '')}` — "
                    f"{reference.get('claim', '')}"
                )

            commit_status = (
                last_commit.get("status", "")
                if isinstance(last_commit, Mapping)
                else ""
            )
            commit_value = (
                last_commit.get("value")
                if isinstance(last_commit, Mapping)
                else None
            )
            commit_explanation = (
                last_commit.get("explanation", "")
                if isinstance(last_commit, Mapping)
                else "Invalid registry entry."
            )
            tree_status = (
                last_tree.get("status", "")
                if isinstance(last_tree, Mapping)
                else ""
            )
            tree_value = (
                last_tree.get("value")
                if isinstance(last_tree, Mapping)
                else None
            )
            tree_explanation = (
                last_tree.get("explanation", "")
                if isinstance(last_tree, Mapping)
                else "Invalid registry entry."
            )
            lines.extend(
                [
                    "",
                    (
                        f"**Last verified commit.** `{commit_value or 'unknown'}` "
                        f"(`{commit_status}`) — {commit_explanation}"
                    ),
                    "",
                    (
                        f"**Last verified tree.** `{tree_value or 'unknown'}` "
                        f"(`{tree_status}`) — {tree_explanation}"
                    ),
                    "",
                ]
            )

            residual_status = (
                residual_risk.get("status", "")
                if isinstance(residual_risk, Mapping)
                else ""
            )
            residual_explanation = (
                residual_risk.get("explanation", "")
                if isinstance(residual_risk, Mapping)
                else "Invalid registry entry."
            )
            lines.extend(
                [
                    (
                        f"**Accepted residual risk.** `{residual_status}` — "
                        f"{residual_explanation}"
                    ),
                    "",
                    "**Affected paths.**",
                    "",
                ]
            )
            for path in item.get("affected_paths", []):
                lines.append(f"- `{path}`")
            lines.extend(["", "**Enforcement files.**", ""])
            files = enforcement.get("files", []) if isinstance(enforcement, Mapping) else []
            for path in files:
                lines.append(f"- `{path}`")
            lines.extend(["", "**Verification tests.**", ""])
            tests = enforcement.get("tests", []) if isinstance(enforcement, Mapping) else []
            if tests:
                for nodeid in tests:
                    lines.append(f"- `{nodeid}`")
            else:
                lines.append("- None recorded.")
            lines.extend(["", "**Validation requests.**", ""])
            validations = (
                enforcement.get("validations", [])
                if isinstance(enforcement, Mapping)
                else []
            )
            for validation in validations:
                if isinstance(validation, Mapping):
                    selectors = validation.get("selectors", [])
                    lines.append(
                        f"- `{validation.get('validation_id', '')}`"
                        f"{' — ' + ', '.join(f'`{item}`' for item in selectors) if selectors else ''}"
                        f" — {validation.get('purpose', '')}"
                    )
            lines.extend(["", "**Known gaps.**", ""])
            gaps = item.get("known_gaps", [])
            if gaps:
                for gap in gaps:
                    lines.append(f"- {gap}")
            else:
                lines.append("- None recorded for this contract.")

    lines.extend(["", "## Priority-0 control files", ""])
    for path in registry.get("priority0_control_paths", []):
        lines.append(f"- `{path}`")
    lines.append("")
    return "\n".join(lines)


def validate_registry(
    registry: Mapping[str, Any],
    schema: Mapping[str, Any],
    *,
    repository_root: Path,
    markdown_path: Path | None = None,
    force_fallback_schema: bool = False,
) -> ValidationReport:
    """Validate schema, IDs, evidence, test nodes, requests, and Markdown."""
    backend, errors = schema_validation_errors(
        registry,
        schema,
        force_fallback=force_fallback_schema,
    )
    warnings: list[str] = []
    invariants_value = registry.get("invariants", [])
    invariants = invariants_value if isinstance(invariants_value, list) else []
    collection_references: dict[str, set[str]] = {}
    test_tree_cache: dict[Path, tuple[ast.Module | None, str]] = {}
    test_node_cache: dict[str, tuple[bool, str]] = {}
    historical_blob_cache: dict[tuple[str, str], tuple[bytes | None, str]] = {}
    historical_tree_cache: dict[
        tuple[str, str], tuple[ast.Module | None, str]
    ] = {}

    def require_collected(selector: str, context: str) -> None:
        collection_references.setdefault(selector, set()).add(context)

    def check_test_node(nodeid: str) -> tuple[bool, str]:
        cached = test_node_cache.get(nodeid)
        if cached is None:
            cached = test_node_exists(
                repository_root,
                nodeid,
                tree_cache=test_tree_cache,
            )
            test_node_cache[nodeid] = cached
        return cached

    seen_ids: set[str] = set()
    affected_paths: set[str] = set()
    for index, invariant in enumerate(invariants):
        if not isinstance(invariant, Mapping):
            continue
        invariant_id = str(invariant.get("id") or f"index-{index}")
        if invariant_id in seen_ids:
            errors.append(f"$.invariants[{index}].id: duplicate invariant ID {invariant_id}")
        seen_ids.add(invariant_id)

        status = invariant.get("implementation_status")
        verification = invariant.get("verification", {})
        verification_status = (
            verification.get("status") if isinstance(verification, Mapping) else None
        )
        gaps = invariant.get("known_gaps", [])
        tests = invariant.get("enforcement", {}).get("tests", []) if isinstance(
            invariant.get("enforcement"), Mapping
        ) else []
        validations = invariant.get("enforcement", {}).get("validations", []) if isinstance(
            invariant.get("enforcement"), Mapping
        ) else []
        if status in {"partial", "missing"} and not gaps:
            errors.append(
                f"{invariant_id}: {status} implementation must state at least one known gap"
            )
        if status == "implemented" and gaps:
            errors.append(
                f"{invariant_id}: implemented status cannot retain contract-level known gaps"
            )
        if status == "missing" and verification_status != "unverified":
            errors.append(
                f"{invariant_id}: missing implementation must be marked unverified"
            )
        if verification_status == "verified" and not tests:
            errors.append(f"{invariant_id}: verified invariant must name a test")
        if status == "implemented" and not tests:
            errors.append(f"{invariant_id}: implemented invariant must name a test")
        if invariant.get("criticality") == "critical" and not validations:
            errors.append(f"{invariant_id}: critical invariant must name a validation")

        artifacts = invariant.get("runtime_consumed_artifacts", {})
        if isinstance(artifacts, Mapping):
            artifact_status = artifacts.get("status")
            artifact_values = artifacts.get("artifacts", [])
            if (
                artifact_status in {"direct", "indirect"}
                and not artifact_values
            ):
                errors.append(
                    f"{invariant_id}: {artifact_status} runtime artifact status "
                    "must name at least one artifact"
                )
            if artifact_status == "none" and artifact_values:
                errors.append(
                    f"{invariant_id}: runtime artifact status none cannot name artifacts"
                )

        deployed_checks = invariant.get(
            "required_production_deployed_path_checks",
            {},
        )
        if isinstance(deployed_checks, Mapping):
            deployed_status = deployed_checks.get("status")
            check_values = deployed_checks.get("checks", [])
            if deployed_status == "required" and not check_values:
                errors.append(
                    f"{invariant_id}: required deployed-path status must name a check"
                )
            if deployed_status in {"not_applicable", "unknown"} and check_values:
                errors.append(
                    f"{invariant_id}: deployed-path status {deployed_status} "
                    "cannot claim executable checks"
                )

        revision_values: dict[str, tuple[Any, Any]] = {}
        for revision_field in ("last_verified_commit", "last_verified_tree"):
            revision = invariant.get(revision_field, {})
            if not isinstance(revision, Mapping):
                continue
            revision_status = revision.get("status")
            revision_value = revision.get("value")
            revision_values[revision_field] = (
                revision_status,
                revision_value,
            )
            if revision_status == "known" and (
                not isinstance(revision_value, str)
                or HEX40.fullmatch(revision_value) is None
            ):
                errors.append(
                    f"{invariant_id}: known {revision_field} must contain a "
                    "40-character lowercase hexadecimal value"
                )
            if revision_status == "unknown" and revision_value is not None:
                errors.append(
                    f"{invariant_id}: unknown {revision_field} must use null value"
                )
        if (
            revision_values.get("last_verified_commit", (None,))[0]
            != revision_values.get("last_verified_tree", (None,))[0]
        ):
            errors.append(
                f"{invariant_id}: verified commit and tree knowledge status differ"
            )
        historical_commit: str | None = None
        if (
            revision_values.get("last_verified_commit", (None,))[0] == "known"
            and revision_values.get("last_verified_tree", (None,))[0] == "known"
        ):
            declared_commit = revision_values["last_verified_commit"][1]
            declared_tree = revision_values["last_verified_tree"][1]
            if (
                isinstance(declared_commit, str)
                and HEX40.fullmatch(declared_commit) is not None
                and isinstance(declared_tree, str)
                and HEX40.fullmatch(declared_tree) is not None
            ):
                actual_tree, history_error = _git_commit_tree(
                    repository_root,
                    declared_commit,
                )
                if history_error is not None:
                    errors.append(
                        f"{invariant_id}: last verified {history_error}: "
                        f"{declared_commit}"
                    )
                elif actual_tree != declared_tree:
                    errors.append(
                        f"{invariant_id}: last verified tree mismatch for "
                        f"{declared_commit}: declared {declared_tree}, "
                        f"actual {actual_tree}"
                    )
                else:
                    historical_commit = declared_commit

        residual = invariant.get("accepted_residual_risk", {})
        if isinstance(residual, Mapping):
            residual_status = residual.get("status")
            if status in {"partial", "missing"} and residual_status not in {
                "unaccepted",
                "unknown",
            }:
                errors.append(
                    f"{invariant_id}: {status} implementation cannot report "
                    f"residual risk as {residual_status}"
                )
            if status == "implemented" and residual_status == "unaccepted":
                errors.append(
                    f"{invariant_id}: implemented status conflicts with an "
                    "unaccepted contract-level residual risk"
                )

        full_suite = invariant.get("full_suite_relevance", {})
        if (
            invariant.get("criticality") == "critical"
            and isinstance(full_suite, Mapping)
            and full_suite.get("status") == "not_applicable"
        ):
            errors.append(
                f"{invariant_id}: critical invariant cannot make the complete "
                "suite irrelevant"
            )

        for raw_path in invariant.get("affected_paths", []):
            if isinstance(raw_path, str):
                affected_paths.add(raw_path)
        enforcement = invariant.get("enforcement", {})
        all_paths = list(invariant.get("affected_paths", []))
        if isinstance(enforcement, Mapping):
            all_paths.extend(enforcement.get("files", []))
        for raw_path in all_paths:
            path, path_error = _safe_repository_path(repository_root, raw_path)
            if path_error:
                errors.append(f"{invariant_id}: {path_error}")
            elif path is not None and not path.is_file():
                errors.append(f"{invariant_id}: referenced file does not exist: {raw_path}")
        if isinstance(enforcement, Mapping):
            enforcement_tests = enforcement.get("tests", [])
            if isinstance(enforcement_tests, list):
                duplicate_tests = sorted(
                    {
                        nodeid
                        for nodeid in enforcement_tests
                        if isinstance(nodeid, str)
                        and enforcement_tests.count(nodeid) > 1
                    }
                )
                for nodeid in duplicate_tests:
                    errors.append(
                        f"{invariant_id}: duplicate enforcement test {nodeid}"
                    )
            for nodeid in enforcement.get("tests", []):
                exists, reason = check_test_node(nodeid)
                if not exists:
                    errors.append(f"{invariant_id}: {reason}")
                else:
                    require_collected(nodeid, invariant_id)
            for validation_index, validation in enumerate(
                enforcement.get("validations", [])
            ):
                if not isinstance(validation, Mapping):
                    continue
                validation_id = validation.get("validation_id")
                selectors = validation.get("selectors")
                if validation_id not in VALIDATION_IDS:
                    errors.append(
                        f"{invariant_id}: validation {validation_index} has unknown ID"
                    )
                    continue
                if not isinstance(selectors, list) or not all(
                    isinstance(selector, str) for selector in selectors
                ):
                    errors.append(
                        f"{invariant_id}: validation {validation_index} selectors "
                        "must be strings"
                    )
                    continue
                if validation_id == "pytest":
                    if not selectors:
                        errors.append(
                            f"{invariant_id}: pytest validation {validation_index} "
                            "must name selectors"
                        )
                    duplicate_selectors = sorted(
                        {
                            selector
                            for selector in selectors
                            if selectors.count(selector) > 1
                        }
                    )
                    for selector in duplicate_selectors:
                        errors.append(
                            f"{invariant_id}: duplicate pytest selector "
                            f"{selector}"
                        )
                    for selector in selectors:
                        if (
                            not TEST_SELECTOR.fullmatch(selector)
                            or ".." in PurePosixPath(
                                selector.split("::", 1)[0]
                            ).parts
                            or any(character in selector for character in "*?[]{}")
                        ):
                            errors.append(
                                f"{invariant_id}: unsafe pytest selector "
                                f"{selector!r}"
                            )
                        else:
                            exists, reason = check_test_node(selector)
                            if not exists:
                                errors.append(f"{invariant_id}: {reason}")
                            else:
                                require_collected(selector, invariant_id)
                elif selectors:
                    errors.append(
                        f"{invariant_id}: {validation_id} does not accept selectors"
                    )

            if historical_commit is not None:
                historical_selectors = {
                    selector
                    for selector in enforcement.get("tests", [])
                    if isinstance(selector, str)
                }
                for validation in enforcement.get("validations", []):
                    if (
                        not isinstance(validation, Mapping)
                        or validation.get("validation_id") != "pytest"
                    ):
                        continue
                    historical_selectors.update(
                        selector
                        for selector in validation.get("selectors", [])
                        if isinstance(selector, str)
                    )
                for selector in sorted(historical_selectors):
                    exists, reason = historical_test_selector_exists(
                        repository_root,
                        historical_commit,
                        selector,
                        blob_cache=historical_blob_cache,
                        tree_cache=historical_tree_cache,
                    )
                    if not exists:
                        errors.append(
                            f"{invariant_id}: last verified commit "
                            f"{historical_commit}: {reason}"
                        )

        for evidence in invariant.get("evidence_references", []):
            if not isinstance(evidence, Mapping) or evidence.get("type") != "test":
                continue
            nodeid = evidence.get("reference")
            if not isinstance(nodeid, str):
                continue
            exists, reason = check_test_node(nodeid)
            if not exists:
                errors.append(f"{invariant_id}: evidence reference: {reason}")
            else:
                require_collected(nodeid, f"{invariant_id}: evidence reference")

    runtime_artifact_declarations = {
        str(artifact)
        for invariant in invariants
        if isinstance(invariant, Mapping)
        for artifact_set in [invariant.get("runtime_consumed_artifacts", {})]
        if isinstance(artifact_set, Mapping)
        for artifact in artifact_set.get("artifacts", [])
        if isinstance(artifact, str)
    }
    classification_ids: set[str] = set()
    for index, record in enumerate(
        registry.get("generated_artifact_classifications", [])
    ):
        if not isinstance(record, Mapping):
            continue
        record_id = str(record.get("id") or f"index-{index}")
        if record_id in classification_ids:
            errors.append(
                "generated_artifact_classifications: duplicate classification ID "
                f"{record_id}"
            )
        classification_ids.add(record_id)
        artifact = record.get("artifact")
        artifact_relative = artifact if isinstance(artifact, str) else ""
        artifact_path, artifact_error = _safe_repository_path(
            repository_root, artifact_relative
        )
        if artifact_error:
            errors.append(f"{record_id}: {artifact_error}")
            artifact_path = None
        elif artifact_path is None or not artifact_path.is_file():
            errors.append(f"{record_id}: classified artifact does not exist: {artifact}")
            artifact_path = None
        elif sha256_file(artifact_path) != record.get("artifact_sha256"):
            errors.append(f"{record_id}: classified artifact SHA-256 mismatch")

        bound_artifact = record.get("bound_artifact")
        bound_value = bound_artifact if isinstance(bound_artifact, str) else ""
        bound_path, bound_error = _safe_repository_path(
            repository_root, bound_value
        )
        if bound_error:
            errors.append(f"{record_id}: {bound_error}")
            bound_path = None
        elif bound_path is None or not bound_path.is_file():
            errors.append(
                f"{record_id}: current bound artifact does not exist: "
                f"{bound_artifact}"
            )
            bound_path = None
        elif sha256_file(bound_path) != record.get(
            "observed_current_bound_artifact_sha256"
        ):
            errors.append(
                f"{record_id}: observed current bound-artifact SHA-256 mismatch"
            )

        bound_commit = record.get("bound_commit")
        if isinstance(bound_commit, str):
            historical_artifact_sha, historical_artifact_error = git_blob_sha256(
                repository_root,
                bound_commit,
                artifact_relative,
            )
            if historical_artifact_error:
                errors.append(
                    f"{record_id}: historical classified artifact cannot be "
                    f"verified: {historical_artifact_error}"
                )
            elif historical_artifact_sha != record.get("artifact_sha256"):
                errors.append(
                    f"{record_id}: classified artifact differs from its recorded "
                    "historical commit"
                )
            historical_bound_sha, historical_bound_error = git_blob_sha256(
                repository_root,
                bound_commit,
                bound_value,
            )
            if historical_bound_error:
                errors.append(
                    f"{record_id}: historical bound artifact cannot be verified: "
                    f"{historical_bound_error}"
                )
            elif historical_bound_sha != record.get("bound_artifact_sha256"):
                errors.append(
                    f"{record_id}: bound artifact SHA-256 differs from its "
                    "recorded historical commit"
                )

        if record.get("classification") == "historical_build_time":
            if record.get("runtime_relationship_required") is not False:
                errors.append(
                    f"{record_id}: historical build-time evidence cannot require "
                    "a runtime relationship"
                )
            if record.get("current_companion") is not False:
                errors.append(
                    f"{record_id}: historical build-time evidence cannot be a "
                    "current companion"
                )
            if artifact_relative in runtime_artifact_declarations:
                errors.append(
                    f"{record_id}: historical build-time evidence is also declared "
                    "runtime-consumed"
                )
            if record.get("bound_artifact_sha256") == record.get(
                "observed_current_bound_artifact_sha256"
            ):
                errors.append(
                    f"{record_id}: historical build-time evidence unexpectedly "
                    "matches the current bound artifact"
                )
        elif record.get("classification") == "direct_companion":
            if record.get("runtime_relationship_required") is not True:
                errors.append(
                    f"{record_id}: direct companion must require a runtime relationship"
                )
            if record.get("current_companion") is not True:
                errors.append(
                    f"{record_id}: direct companion must be marked current"
                )
            if artifact_relative not in runtime_artifact_declarations:
                errors.append(
                    f"{record_id}: direct companion is not declared runtime-consumed"
                )

        if artifact_path is not None:
            try:
                parsed_artifact = load_json_document(artifact_path)
            except (OSError, strict_json.StrictJSONError) as exc:
                errors.append(
                    f"{record_id}: classified artifact is not valid JSON: {exc}"
                )
            else:
                if not isinstance(parsed_artifact, Mapping):
                    errors.append(
                        f"{record_id}: classified artifact root is not an object"
                    )
                else:
                    if parsed_artifact.get("manifest_sha256") != record.get(
                        "bound_artifact_sha256"
                    ):
                        errors.append(
                            f"{record_id}: artifact manifest_sha256 does not match "
                            "its recorded historical binding"
                        )
                    if parsed_artifact.get("policy_version") != record.get(
                        "policy_version"
                    ):
                        errors.append(
                            f"{record_id}: artifact policy version does not match "
                            "its classification"
                        )

        for evidence_field in ("builder_evidence", "runtime_loader_evidence"):
            for evidence in record.get(evidence_field, []):
                if not isinstance(evidence, Mapping):
                    continue
                if evidence.get("type") not in {"artifact", "code"}:
                    continue
                reference = evidence.get("reference")
                reference_value = reference if isinstance(reference, str) else ""
                path, path_error = _safe_repository_path(
                    repository_root, reference_value
                )
                if path_error:
                    errors.append(f"{record_id}: {path_error}")
                elif path is None or not path.is_file():
                    errors.append(
                        f"{record_id}: evidence file does not exist: {reference}"
                    )
        for nodeid in record.get("validator_tests", []):
            exists, reason = check_test_node(nodeid)
            if not exists:
                errors.append(f"{record_id}: {reason}")
            else:
                require_collected(nodeid, record_id)
        for invariant_id in record.get("invariant_ids", []):
            if invariant_id not in seen_ids:
                errors.append(
                    f"{record_id}: unknown invariant reference {invariant_id}"
                )

    skip_node_ids: set[str] = set()
    for index, record in enumerate(registry.get("expected_full_suite_skips", [])):
        if not isinstance(record, Mapping):
            continue
        nodeid = str(record.get("node_id") or f"index-{index}")
        if nodeid in skip_node_ids:
            errors.append(f"expected_full_suite_skips: duplicate test node {nodeid}")
        skip_node_ids.add(nodeid)
        exists, reason = check_test_node(nodeid)
        if not exists:
            errors.append(f"expected_full_suite_skips: {reason}")
        else:
            require_collected(nodeid, "expected_full_suite_skips")
        for invariant_id in record.get("invariant_ids", []):
            if invariant_id not in seen_ids:
                errors.append(
                    f"{nodeid}: unknown invariant reference {invariant_id}"
                )
        pattern = record.get("reason_regex")
        if isinstance(pattern, str):
            try:
                re.compile(pattern)
            except re.error as exc:
                errors.append(f"{nodeid}: invalid skip-reason pattern: {exc}")
            if not pattern.startswith("^") or not pattern.endswith("$"):
                errors.append(
                    f"{nodeid}: skip-reason pattern must be fully anchored"
                )
        justification = record.get("justification")
        if (
            record.get("prevents_release_qualification") is False
            and (
                not isinstance(justification, str)
                or len(justification.strip()) < 40
            )
        ):
            errors.append(
                f"{nodeid}: non-blocking expected skip requires a narrow justification"
            )

    for raw_path in registry.get("priority0_control_paths", []):
        path, path_error = _safe_repository_path(repository_root, raw_path)
        if path_error:
            errors.append(f"priority0_control_paths: {path_error}")
        elif path is not None and not path.is_file():
            errors.append(f"priority0 control file does not exist: {raw_path}")
        if raw_path not in affected_paths:
            errors.append(
                f"priority0 control file is not mapped by any invariant: {raw_path}"
            )

    # A structurally invalid registry is already rejected; avoid importing its
    # test modules merely to accumulate secondary diagnostics.  Every registry
    # which would otherwise pass receives exactly one authoritative collection.
    if not errors:
        collected_nodes, collection_error = _collect_pytest_nodes(
            repository_root,
            collection_references,
        )
        if collection_error is not None:
            errors.append(collection_error)
        else:
            for selector, contexts in sorted(collection_references.items()):
                if _selector_matches_collection(selector, collected_nodes):
                    continue
                for context in sorted(contexts):
                    errors.append(
                        f"{context}: pytest selector was not collected: {selector}"
                    )

    unsupported = _status_ids(registry, "implementation_status", "missing")
    partial = _status_ids(registry, "implementation_status", "partial")
    implemented = _status_ids(registry, "implementation_status", "implemented")
    unverified = _status_ids(registry, "verification", "unverified")
    partially_verified = _status_ids(registry, "verification", "partial")
    verified = _status_ids(registry, "verification", "verified")
    if unsupported:
        warnings.append(
            "unsupported invariants: " + ", ".join(unsupported)
        )
    if partial:
        warnings.append(
            "partially implemented invariants: " + ", ".join(partial)
        )
    if unverified:
        warnings.append(
            "unverified invariants: " + ", ".join(unverified)
        )
    if partially_verified:
        warnings.append(
            "partially verified invariants: " + ", ".join(partially_verified)
        )

    if markdown_path is not None:
        expected = render_markdown(registry)
        try:
            actual = markdown_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            errors.append(f"generated Markdown does not exist: {markdown_path}")
        else:
            if actual != expected:
                errors.append(
                    f"generated Markdown is stale; run "
                    "python3 tools/priority0_registry.py render --write"
                )

    return ValidationReport(
        schema_backend=backend,
        errors=tuple(sorted(set(errors))),
        warnings=tuple(sorted(set(warnings))),
        implemented=implemented,
        partial=partial,
        unsupported=unsupported,
        verified=verified,
        partially_verified=partially_verified,
        unverified=unverified,
    )


def _resolved_path(repository_root: Path, supplied: Path) -> Path:
    return supplied if supplied.is_absolute() else repository_root / supplied


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    validate = subparsers.add_parser(
        "validate",
        help="validate schema, evidence, validation requests, and Markdown synchronization",
    )
    validate.add_argument("--repo-root", type=Path, default=DEFAULT_REPOSITORY_ROOT)
    validate.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    validate.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    validate.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    validate.add_argument("--json", action="store_true", help="emit the report as JSON")
    validate.add_argument(
        "--strict-status",
        action="store_true",
        help="fail when any invariant is partial, missing, partially verified, or unverified",
    )
    validate.add_argument(
        "--no-markdown-check",
        action="store_true",
        help="skip the generated Markdown synchronization check",
    )

    render = subparsers.add_parser(
        "render",
        help="render Markdown deterministically from the canonical JSON registry",
    )
    render.add_argument("--repo-root", type=Path, default=DEFAULT_REPOSITORY_ROOT)
    render.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    render.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    render.add_argument("--output", type=Path, default=DEFAULT_MARKDOWN)
    mode = render.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="fail if output is stale")
    mode.add_argument("--write", action="store_true", help="replace output with rendered Markdown")
    return parser


def _print_human_report(report: ValidationReport) -> None:
    print(f"schema backend: {report.schema_backend}")
    print(f"registry valid: {'yes' if report.ok else 'no'}")
    print(
        "implementation: "
        f"implemented={len(report.implemented)} "
        f"partial={len(report.partial)} "
        f"unsupported={len(report.unsupported)}"
    )
    print(
        "verification: "
        f"verified={len(report.verified)} "
        f"partial={len(report.partially_verified)} "
        f"unverified={len(report.unverified)}"
    )
    for error in report.errors:
        print(f"ERROR: {error}")
    for warning in report.warnings:
        print(f"WARNING: {warning}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the registry validation or Markdown rendering command."""
    args = _build_parser().parse_args(argv)
    repository_root = args.repo_root.resolve()
    registry_path = _resolved_path(repository_root, args.registry)
    schema_path = _resolved_path(repository_root, args.schema)
    try:
        registry = load_json_document(registry_path)
        schema = load_json_document(schema_path)
    except (OSError, strict_json.StrictJSONError) as exc:
        print(f"registry input error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(registry, Mapping) or not isinstance(schema, Mapping):
        print("registry and schema must both be JSON objects", file=sys.stderr)
        return 1

    if args.action == "render":
        backend, schema_errors = schema_validation_errors(registry, schema)
        if schema_errors:
            print(f"schema backend: {backend}", file=sys.stderr)
            for error in schema_errors:
                print(f"ERROR: {error}", file=sys.stderr)
            return 1
        rendered = render_markdown(registry)
        output_path = _resolved_path(repository_root, args.output)
        if args.check:
            try:
                current = output_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                print(f"generated Markdown does not exist: {output_path}", file=sys.stderr)
                return 1
            if current != rendered:
                print(
                    "generated Markdown is stale; run "
                    "python3 tools/priority0_registry.py render --write",
                    file=sys.stderr,
                )
                return 1
            print(f"generated Markdown is synchronized: {output_path}")
            return 0
        if args.write:
            output_path.write_text(rendered, encoding="utf-8")
            print(f"wrote {output_path}")
            return 0
        sys.stdout.write(rendered)
        return 0

    markdown_path = None
    if not args.no_markdown_check:
        markdown_path = _resolved_path(repository_root, args.markdown)
    report = validate_registry(
        registry,
        schema,
        repository_root=repository_root,
        markdown_path=markdown_path,
    )
    if args.json:
        print(strict_json.canonical_dumps(report.to_dict()))
    else:
        _print_human_report(report)
    if not report.ok:
        return 1
    if args.strict_status and (
        report.partial
        or report.unsupported
        or report.partially_verified
        or report.unverified
    ):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
