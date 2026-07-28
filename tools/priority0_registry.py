#!/usr/bin/env python3
"""Validate and render the canonical Priority-0 production invariant registry."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any, Iterable, Mapping, Sequence


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
    """Load a UTF-8 JSON document without accepting trailing content."""
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


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


def test_node_exists(repository_root: Path, nodeid: str) -> tuple[bool, str]:
    """Check both the test file and named Python test node."""
    parts = nodeid.split("::")
    path, path_error = _safe_repository_path(repository_root, parts[0])
    if path_error is not None or path is None:
        return False, path_error or "invalid test path"
    if not path.is_file():
        return False, f"test file does not exist: {parts[0]}"
    if len(parts) == 1:
        return True, ""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        return False, f"test file cannot be parsed: {parts[0]}: {exc}"
    current_nodes: Iterable[ast.AST] = tree.body
    for raw_name in parts[1:]:
        name = raw_name.split("[", 1)[0]
        node = _named_ast_child(current_nodes, name)
        if node is None:
            return False, f"test node does not exist: {nodeid}"
        current_nodes = getattr(node, "body", ())
    return True, ""


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
            lines.extend(["", "**Commands.**", ""])
            commands = enforcement.get("commands", []) if isinstance(enforcement, Mapping) else []
            for command in commands:
                if isinstance(command, Mapping):
                    lines.append(
                        f"- `{command.get('command', '')}` — {command.get('purpose', '')}"
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
    """Validate schema, IDs, evidence paths, test nodes, commands, and Markdown."""
    backend, errors = schema_validation_errors(
        registry,
        schema,
        force_fallback=force_fallback_schema,
    )
    warnings: list[str] = []
    invariants_value = registry.get("invariants", [])
    invariants = invariants_value if isinstance(invariants_value, list) else []

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
        commands = invariant.get("enforcement", {}).get("commands", []) if isinstance(
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
        if invariant.get("criticality") == "critical" and not commands:
            errors.append(f"{invariant_id}: critical invariant must name a command")

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
            for nodeid in enforcement.get("tests", []):
                exists, reason = test_node_exists(repository_root, nodeid)
                if not exists:
                    errors.append(f"{invariant_id}: {reason}")
            for command_index, command in enumerate(enforcement.get("commands", [])):
                if not isinstance(command, Mapping):
                    continue
                text = command.get("command")
                if not isinstance(text, str) or not text.strip():
                    errors.append(
                        f"{invariant_id}: command {command_index} is empty"
                    )
                elif "\n" in text or "\r" in text:
                    errors.append(
                        f"{invariant_id}: command {command_index} must be one line"
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
        help="validate schema, evidence, tests, commands, and Markdown synchronization",
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
    except (OSError, json.JSONDecodeError) as exc:
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
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
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
