#!/usr/bin/env python3
"""Validate the canonical defect ledger and render its deterministic summary."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import fnmatch
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
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
DEFAULT_LEDGER = Path("defect_ledger.json")
DEFAULT_SCHEMA = Path("defect_ledger.schema.json")
DEFAULT_INVARIANTS = Path("production_invariants.json")
DEFAULT_MARKDOWN = Path("DEFECT_LEDGER.md")
DEFAULT_DIAGNOSIS = Path("why_code_reviews_continue_to_find_major_problems.md")

DEFECT_ID_PATTERN = re.compile(r"^DEF-[0-9]{4}$")
SYSTEM_WIDE_CLAIM_PATTERN = re.compile(
    r"\b(?:system[- ]wide|whole[- ]system|entire[- ]system|"
    r"production[- ]wide|all production paths?|complete(?:ly)? closed|"
    r"fully (?:fixed|verified|closed)|no (?:remaining )?risk|no findings)\b",
    re.IGNORECASE,
)
LOCAL_EVIDENCE_TYPES = frozenset({"code", "commit", "test"})
SUMMARY_PATTERN = re.compile(
    r"(?ms)^## Summary\n.*?(?=^## [^\n]+\n|\Z)"
)
IDENTITY_SCOPE_PATTERN = re.compile(
    r"(?ms)^## Identity and evidence boundary\n.*?(?=^## [^\n]+\n|\Z)"
)
SCOPE_PATTERN = re.compile(
    r"(?ms)^## Scope and incident classification\n.*?(?=^## [^\n]+\n|\Z)"
)
CHRONOLOGY_PATTERN = re.compile(
    r"(?ms)^## Chronology projection\n.*?(?=^## [^\n]+\n|\Z)"
)
DIAGNOSIS_PATTERN = re.compile(
    r"(?ms)^## Finding chronology and current status\n"
    r".*?(?=^## [^\n]+\n|\Z)"
)
INCLUSIVE_RANGE_PATTERN = re.compile(
    r"^inclusive:(unknown|[0-9a-f]{40})\.\.([0-9a-f]{40})$"
)
EXTERNAL_EVIDENCE_ID_PATTERN = re.compile(r"\bEXT-[A-Z0-9-]+\b")
KNOWN_STATUS_RULES = frozenset(
    {
        "active",
        "latent-disabled",
        "repaired-not-deployed",
        "deployed-unverified",
        "deployed-verified",
        "assurance-weakness",
        "superseded",
    }
)


@dataclass(frozen=True)
class ValidationReport:
    """Stable validation result for CLI and tests."""

    schema_backend: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    status_counts: tuple[tuple[str, int], ...]

    @property
    def ok(self) -> bool:
        """Return whether validation produced no errors."""
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic JSON-compatible validation summary."""
        return {
            "ok": self.ok,
            "schema_backend": self.schema_backend,
            "status_counts": dict(self.status_counts),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def load_json_document(path: Path) -> Any:
    """Load one strict UTF-8 JSON document."""

    return strict_json.load(path)


def _path_label(parts: Sequence[Any]) -> str:
    label = "$"
    for part in parts:
        label += f"[{part}]" if isinstance(part, int) else f".{part}"
    return label


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


def _resolve_local_ref(root_schema: Mapping[str, Any], reference: str) -> Any:
    if not reference.startswith("#/"):
        raise ValueError(
            f"built-in validator supports only local references: {reference}"
        )
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
    """Validate the JSON-Schema subset used by defect_ledger.schema.json."""

    def visit(
        value: Any,
        rule: Mapping[str, Any],
        parts: tuple[Any, ...],
        root: Mapping[str, Any],
    ) -> list[str]:
        errors: list[str] = []
        if "$ref" in rule:
            try:
                target = _resolve_local_ref(root, str(rule["$ref"]))
            except ValueError as exc:
                return [f"{_path_label(parts)}: {exc}"]
            if not isinstance(target, Mapping):
                return [
                    f"{_path_label(parts)}: schema reference is not an object"
                ]
            errors.extend(visit(value, target, parts, root))
            return errors

        alternatives = rule.get("oneOf")
        if isinstance(alternatives, list):
            outcomes = [
                visit(value, option, parts, root)
                for option in alternatives
                if isinstance(option, Mapping)
            ]
            matches = sum(not outcome for outcome in outcomes)
            if matches != 1:
                errors.append(
                    f"{_path_label(parts)}: expected exactly one oneOf branch "
                    f"to match, got {matches}"
                )

        if "const" in rule and value != rule["const"]:
            errors.append(
                f"{_path_label(parts)}: {value!r} is not the required "
                f"constant {rule['const']!r}"
            )
        if "enum" in rule and value not in rule["enum"]:
            errors.append(
                f"{_path_label(parts)}: {value!r} is not one of "
                f"{rule['enum']!r}"
            )

        expected_type = rule.get("type")
        if (
            isinstance(expected_type, str)
            and not _json_type_matches(value, expected_type)
        ):
            errors.append(
                f"{_path_label(parts)}: expected {expected_type}, got "
                f"{type(value).__name__}"
            )
            return errors

        if isinstance(value, str):
            minimum = rule.get("minLength")
            if isinstance(minimum, int) and len(value) < minimum:
                errors.append(
                    f"{_path_label(parts)}: string is shorter than {minimum}"
                )
            pattern = rule.get("pattern")
            if isinstance(pattern, str):
                try:
                    matches = re.search(pattern, value)
                except re.error as exc:
                    errors.append(
                        f"{_path_label(parts)}: invalid schema pattern "
                        f"{pattern!r}: {exc}"
                    )
                else:
                    if matches is None:
                        errors.append(
                            f"{_path_label(parts)}: {value!r} does not match "
                            f"{pattern!r}"
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
                    errors.append(
                        f"{_path_label(parts)}: array items are not unique"
                    )
            item_rule = rule.get("items")
            if isinstance(item_rule, Mapping):
                for index, item in enumerate(value):
                    errors.extend(visit(item, item_rule, (*parts, index), root))

        if isinstance(value, dict):
            required = rule.get("required", [])
            if isinstance(required, list):
                for key in required:
                    if key not in value:
                        errors.append(
                            f"{_path_label(parts)}: required property "
                            f"{key!r} is missing"
                        )
            properties = rule.get("properties", {})
            if isinstance(properties, Mapping):
                for key, child_rule in properties.items():
                    if key in value and isinstance(child_rule, Mapping):
                        errors.extend(
                            visit(value[key], child_rule, (*parts, key), root)
                        )
                if rule.get("additionalProperties") is False:
                    for key in value:
                        if key not in properties:
                            errors.append(
                                f"{_path_label((*parts, key))}: additional "
                                "property is not permitted"
                            )
        return errors

    return visit(instance, schema, (), schema)


def schema_validation_errors(
    instance: Any,
    schema: Mapping[str, Any],
    *,
    force_fallback: bool = False,
) -> tuple[str, list[str]]:
    """Use Draft 2020-12 when installed, otherwise the strict built-in subset."""
    if not force_fallback:
        try:
            import jsonschema
        except ImportError:
            pass
        else:
            validator_type = getattr(jsonschema, "Draft202012Validator", None)
            if validator_type is not None:
                try:
                    validator_type.check_schema(schema)
                    validator = validator_type(schema)
                    errors = [
                        (
                            f"{_path_label(tuple(error.absolute_path))}: "
                            f"{error.message}"
                        )
                        for error in sorted(
                            validator.iter_errors(instance),
                            key=lambda item: tuple(
                                str(part) for part in item.absolute_path
                            ),
                        )
                    ]
                    return "jsonschema.Draft202012Validator", errors
                except Exception as exc:
                    return "jsonschema.Draft202012Validator", [
                        "$: schema validation could not run: "
                        f"{type(exc).__name__}: {exc}"
                    ]
    return "built-in-draft2020-subset", _fallback_schema_errors(instance, schema)


def _safe_repository_path(
    repository_root: Path,
    raw_path: str,
) -> tuple[Path | None, str | None]:
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
    path_text: str,
    nodeid: str,
    commit: str,
) -> tuple[bool, str]:
    """Check that a ledger test path and Python node exist at its bound commit."""
    _path, path_error = _safe_repository_path(repository_root, path_text)
    if path_error is not None:
        return False, path_error or "invalid test path"
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        return False, f"test commit is not a full Git commit: {commit!r}"
    source = _git(repository_root, "show", f"{commit}:{path_text}")
    if source.returncode != 0:
        return (
            False,
            f"test file does not exist at recorded commit {commit[:8]}: "
            f"{path_text}",
        )
    try:
        tree = ast.parse(
            source.stdout,
            filename=f"{commit[:8]}:{path_text}",
        )
    except SyntaxError as exc:
        return (
            False,
            f"test file cannot be parsed at recorded commit {commit[:8]}: "
            f"{path_text}: {exc}",
        )
    nodes: Iterable[ast.AST] = tree.body
    for raw_name in nodeid.split("::"):
        name = raw_name.split("[", 1)[0]
        node = _named_ast_child(nodes, name)
        if node is None:
            return False, f"test node does not exist: {path_text}::{nodeid}"
        nodes = getattr(node, "body", ())
    return True, ""


def _unknown_paths(value: Any, prefix: str = "") -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key == "unknowns":
                continue
            path = f"{prefix}.{key}" if prefix else str(key)
            yield from _unknown_paths(child, path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _unknown_paths(child, f"{prefix}[{index}]")
    elif value == "unknown":
        yield prefix


def _git(repository_root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _known_commit_values(ledger: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()

    def add(value: Any) -> None:
        if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value):
            result.add(value)

    identity_scope = ledger.get("identity_scope", {})
    if isinstance(identity_scope, Mapping):
        for section, field in (
            ("production_baseline", "commit"),
            ("ledger_evidence_cutoff", "commit"),
            ("production_deployment_observation", "repository_commit"),
        ):
            record = identity_scope.get(section, {})
            if isinstance(record, Mapping):
                add(record.get(field))
    defects = ledger.get("defects", [])
    if not isinstance(defects, list):
        return result
    for defect in defects:
        if not isinstance(defect, Mapping):
            continue
        introduced = defect.get("introduced", {})
        if isinstance(introduced, Mapping):
            add(introduced.get("first_bad_commit"))
            add(introduced.get("last_known_good_commit"))
            affected_range = introduced.get("affected_range")
            if isinstance(affected_range, str):
                range_match = INCLUSIVE_RANGE_PATTERN.fullmatch(affected_range)
                if range_match is not None:
                    add(range_match.group(1))
                    add(range_match.group(2))
        for event in defect.get("chronology", []):
            if isinstance(event, Mapping):
                add(event.get("commit"))
        for key, field in (
            ("first_review_scope", "reviewed_revision"),
            ("detection", "revision"),
            ("fix", "commit"),
            ("deployment", "observed_commit"),
        ):
            item = defect.get(key, {})
            if isinstance(item, Mapping):
                add(item.get(field))
        for test in defect.get("tests", []):
            if isinstance(test, Mapping):
                add(test.get("commit"))
    return result


def _short_commit(value: Any) -> str:
    text = str(value)
    return text if text == "unknown" else text[:8]


def _markdown_escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _introduced_cell(defect: Mapping[str, Any]) -> str:
    introduced = defect.get("introduced", {})
    if not isinstance(introduced, Mapping):
        return "unknown"
    first_bad = str(introduced.get("first_bad_commit", "unknown"))
    if first_bad == "unknown":
        return "unknown"
    rendered = f"`{_short_commit(first_bad)}`"
    if introduced.get("confidence") != "bounded":
        return rendered
    explanation = str(introduced.get("explanation", "")).lower()
    if "external production checkout" in explanation:
        return f"{rendered} or earlier environment, bounded"
    if (
        "behavior is present in the repository's initial bot commit" in explanation
        and "additional" not in explanation
    ):
        return rendered
    return f"{rendered}, bounded"


def _fix_cell(defect: Mapping[str, Any]) -> str:
    fix = defect.get("fix", {})
    if not isinstance(fix, Mapping):
        return "unknown"
    if fix.get("state") == "fixed":
        return f"`{_short_commit(fix.get('commit', 'unknown'))}`"
    return str(fix.get("state", "unknown"))


def _deployment_cell(defect: Mapping[str, Any]) -> str:
    deployment = defect.get("deployment", {})
    if not isinstance(deployment, Mapping):
        return "unknown"
    state = str(deployment.get("state", "unknown"))
    observed = _short_commit(deployment.get("observed_commit", "unknown"))
    status = defect.get("status")
    fix = defect.get("fix", {})
    fix_state = fix.get("state") if isinstance(fix, Mapping) else None
    if state == "not-deployed":
        if status == "latent-disabled":
            return f"not-deployed; pool disabled; observed `{observed}`"
        return f"not-deployed; observed `{observed}`"
    if state in {"deployed-unverified", "deployed-verified"}:
        return f"{state}; observed `{observed}`"
    if status == "superseded":
        relations = defect.get("relations", {})
        children = (
            relations.get("superseded_by", [])
            if isinstance(relations, Mapping)
            else []
        )
        return "tracked by " + " and ".join(f"`{item}`" for item in children)
    if status == "assurance-weakness":
        return "not applicable"
    if status == "active":
        evidence = str(deployment.get("evidence", "")).lower()
        if "never deployed" in evidence:
            return f"not deployed; absent from observed production `{observed}`"
        if (
            "observed production" in evidence
            and "ledger evidence cut-off" in evidence
        ):
            return "present in observed production and at evidence cut-off"
        return f"observed in production `{observed}`"
    if status == "latent-disabled" and fix_state == "unfixed":
        return "enforcement unsupported; shadow only"
    if state == "not-applicable":
        return "not applicable"
    return f"{state}; observed `{observed}`"


def render_identity_scope(ledger: Mapping[str, Any]) -> str:
    """Render the non-self-referential ledger identity boundary."""
    identity = ledger.get("identity_scope", {})
    if not isinstance(identity, Mapping):
        raise ValueError("ledger identity_scope must be an object")
    baseline = identity.get("production_baseline", {})
    cutoff = identity.get("ledger_evidence_cutoff", {})
    candidate = identity.get("candidate_under_review", {})
    deployment = identity.get("production_deployment_observation", {})
    regeneration = identity.get("post_merge_regeneration", {})
    if not all(
        isinstance(item, Mapping)
        for item in (baseline, cutoff, candidate, deployment, regeneration)
    ):
        raise ValueError("every ledger identity_scope section must be an object")
    required_fields = candidate.get("required_attestation_fields", [])
    triggers = regeneration.get("triggers", [])
    return "\n".join(
        [
            "## Identity and evidence boundary",
            "",
            f"- Production baseline commit: `{baseline.get('commit', '')}`",
            f"- Production baseline tree: `{baseline.get('tree', '')}`",
            f"- Ledger evidence cut-off commit: `{cutoff.get('commit', '')}`",
            f"- Ledger evidence cut-off tree: `{cutoff.get('tree', '')}`",
            f"- Evidence valid through: `{cutoff.get('as_of', '')}`",
            "- Baseline/cut-off relationship: "
            + _markdown_escape(
                cutoff.get("difference_from_production_baseline", "")
            ),
            "- Status-claim boundary: "
            + _markdown_escape(cutoff.get("meaning", "")),
            "- Candidate identity source: "
            f"`{candidate.get('identity_source', '')}`; stored in ledger: "
            f"`{str(candidate.get('stored_in_ledger', '')).lower()}`",
            "- Candidate attestation fields: "
            + ", ".join(f"`{item}`" for item in required_fields),
            "- Candidate identity rule: "
            + _markdown_escape(candidate.get("explanation", "")),
            "- Observed production repository commit/tree: "
            f"`{deployment.get('repository_commit', '')}` / "
            f"`{deployment.get('repository_tree', '')}`",
            f"- Production observation time: `{deployment.get('observed_at', '')}`",
            "- Loaded-process identity: "
            f"`{deployment.get('loaded_process_identity_status', '')}` — "
            + _markdown_escape(
                deployment.get("loaded_process_identity_explanation", "")
            ),
            "- Freshness warning: "
            + _markdown_escape(deployment.get("freshness_warning", "")),
            "- Post-merge regeneration required: "
            f"`{str(regeneration.get('required', '')).lower()}`",
            "- Regeneration triggers: "
            + ", ".join(f"`{item}`" for item in triggers),
            "- Regeneration rule: "
            + _markdown_escape(regeneration.get("requirement", "")),
        ]
    ) + "\n"


def render_summary(ledger: Mapping[str, Any]) -> str:
    """Render the canonical Markdown Summary section from ledger JSON."""
    metadata = ledger.get("markdown", {})
    columns = (
        metadata.get("columns", [])
        if isinstance(metadata, Mapping)
        else []
    )
    defects = ledger.get("defects", [])
    if not isinstance(columns, list) or not isinstance(defects, list):
        raise ValueError("ledger markdown columns and defects must be arrays")
    lines = [
        "## Summary",
        "",
        "| " + " | ".join(str(item) for item in columns) + " |",
        "|" + "|".join("---" for _item in columns) + "|",
    ]
    for defect in defects:
        if not isinstance(defect, Mapping):
            raise ValueError("every defect must be an object")
        invariants = ", ".join(
            f"`{item}`" for item in defect.get("invariant_ids", [])
        )
        cells = [
            f"`{defect.get('id', '')}`",
            str(defect.get("status", "")),
            str(defect.get("severity", "")),
            _markdown_escape(defect.get("title", "")),
            invariants,
            _introduced_cell(defect),
            _fix_cell(defect),
            _deployment_cell(defect),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _incident_cell(defect: Mapping[str, Any]) -> str:
    incident = defect.get("incident", {})
    if not isinstance(incident, Mapping):
        return "unknown"
    occurred = str(incident.get("occurred", "unknown")).lower()
    explanation = _markdown_escape(incident.get("explanation", ""))
    evidence = _markdown_escape(incident.get("evidence", ""))
    return f"{occurred} — {explanation} Evidence: {evidence}"


def render_scope(ledger: Mapping[str, Any]) -> str:
    """Render the scope and incident-classification projection."""
    defects = ledger.get("defects", [])
    if not isinstance(defects, list):
        raise ValueError("ledger defects must be an array")
    lines = [
        "## Scope and incident classification",
        "",
        "The explicit scope fields below project `defect_class`, "
        "`affected_files`, `runtime_lanes` and `incident` from each JSON "
        "record. “Incident unknown” means the defect or gap is established, "
        "but available production history neither proves nor disproves a "
        "live occurrence.",
        "",
        "| ID | Defect class | Affected files / control paths | Runtime lanes "
        "| Incident |",
        "|---|---|---|---|---|",
    ]
    for defect in defects:
        if not isinstance(defect, Mapping):
            raise ValueError("every defect must be an object")
        affected = "; ".join(
            f"`{_markdown_escape(item)}`"
            for item in defect.get("affected_files", [])
        )
        lanes = "; ".join(
            f"`{_markdown_escape(item)}`"
            for item in defect.get("runtime_lanes", [])
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{defect.get('id', '')}`",
                    str(defect.get("defect_class", "")),
                    affected,
                    lanes,
                    _incident_cell(defect),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def render_chronology(ledger: Mapping[str, Any]) -> str:
    """Render every canonical chronology event in stable ledger order."""
    defects = ledger.get("defects", [])
    if not isinstance(defects, list):
        raise ValueError("ledger defects must be an array")
    lines = [
        "## Chronology projection",
        "",
        "This table projects every `chronology` event from "
        "`defect_ledger.json`; it is generated rather than maintained as a "
        "second chronology.",
        "",
        "| ID | Date | Commit | Event | Evidence |",
        "|---|---|---|---|---|",
    ]
    for defect in defects:
        if not isinstance(defect, Mapping):
            raise ValueError("every defect must be an object")
        for event in defect.get("chronology", []):
            if not isinstance(event, Mapping):
                raise ValueError("every chronology event must be an object")
            commit = _short_commit(event.get("commit", "unknown"))
            commit_cell = commit if commit == "unknown" else f"`{commit}`"
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{defect.get('id', '')}`",
                        str(event.get("date", "")),
                        commit_cell,
                        _markdown_escape(event.get("event", "")),
                        _markdown_escape(event.get("evidence", "")),
                    ]
                )
                + " |"
            )
    return "\n".join(lines) + "\n"


def _affected_range_cell(defect: Mapping[str, Any]) -> str:
    introduced = defect.get("introduced", {})
    if not isinstance(introduced, Mapping):
        return "unknown"
    value = str(introduced.get("affected_range", "unknown"))
    if value == "unknown":
        return "unknown"
    match = INCLUSIVE_RANGE_PATTERN.fullmatch(value)
    if match is None:
        return _markdown_escape(value)
    start, end = match.groups()
    start_cell = start if start == "unknown" else f"`{_short_commit(start)}`"
    end_cell = f"`{_short_commit(end)}`"
    confidence = str(introduced.get("confidence", "unknown"))
    last_good = str(introduced.get("last_known_good_commit", "unknown"))
    result = f"{confidence}; inclusive [{start_cell}, {end_cell}]"
    if last_good != "unknown":
        result += f"; last good `{_short_commit(last_good)}`"
    return result


def render_diagnosis_chronology(ledger: Mapping[str, Any]) -> str:
    """Render diagnosis findings' chronology/status table from ledger JSON."""
    defects = ledger.get("defects", [])
    if not isinstance(defects, list):
        raise ValueError("ledger defects must be an array")
    rows = [
        item
        for item in defects
        if isinstance(item, Mapping)
        and item.get("source_class")
        in {"diagnosis-finding", "diagnosis-finding-child"}
    ]
    lines = [
        "## Finding chronology and current status",
        "",
        "This table is a projection of `defect_ledger.json`, not a second "
        "issue database. Commit abbreviations are the first eight "
        "hexadecimal characters. Affected ranges use explicit inclusive "
        "endpoints.",
        "",
        "| Stable ID | Area | Description | Severity | Introduced / affected "
        "range | First review | Subsystem included | First detection | "
        "Reproducer / test | Fix | Deployment evidence | Current status | "
        "Residual risk | Evidence refs |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for defect in rows:
        review = defect.get("first_review_scope", {})
        detection = defect.get("detection", {})
        fix = defect.get("fix", {})
        deployment = defect.get("deployment", {})
        tests = defect.get("tests", [])
        evidence = defect.get("evidence", [])
        area = str(defect.get("diagnosis_finding", "")).replace("-", " ")
        review_cell = (
            f"{review.get('date', '')} at "
            f"`{_short_commit(review.get('reviewed_revision', 'unknown'))}`; "
            f"{_markdown_escape(review.get('scope', ''))}"
            if isinstance(review, Mapping)
            else "unknown"
        )
        detection_cell = (
            f"{detection.get('date', '')} at "
            f"`{_short_commit(detection.get('revision', 'unknown'))}`; "
            f"{_markdown_escape(detection.get('method', ''))}"
            if isinstance(detection, Mapping)
            else "unknown"
        )
        reproducer = (
            _markdown_escape(detection.get("reproducer", ""))
            if isinstance(detection, Mapping)
            else ""
        )
        test_nodes = [
            f"`{item.get('nodeid', '')}`"
            for item in tests
            if isinstance(item, Mapping)
        ]
        if test_nodes:
            reproducer += "; " + "; ".join(test_nodes)
        fix_cell = (
            (
                f"fixed at `{_short_commit(fix.get('commit', 'unknown'))}`: "
                f"{_markdown_escape(fix.get('summary', ''))}"
            )
            if isinstance(fix, Mapping) and fix.get("state") == "fixed"
            else (
                f"{fix.get('state', 'unknown')}: "
                f"{_markdown_escape(fix.get('summary', ''))}"
                if isinstance(fix, Mapping)
                else "unknown"
            )
        )
        deployment_cell = (
            f"{deployment.get('state', 'unknown')} at "
            f"`{_short_commit(deployment.get('observed_commit', 'unknown'))}`; "
            f"{_markdown_escape(deployment.get('evidence', ''))}"
            if isinstance(deployment, Mapping)
            else "unknown"
        )
        references = "; ".join(
            f"`{_markdown_escape(item.get('reference', ''))}`"
            for item in evidence
            if isinstance(item, Mapping)
        )
        cells = [
            f"`{defect.get('id', '')}`",
            area,
            _markdown_escape(defect.get("title", "")),
            str(defect.get("severity", "")),
            _affected_range_cell(defect),
            review_cell,
            "; ".join(
                f"`{_markdown_escape(item)}`"
                for item in defect.get("affected_files", [])
            ),
            detection_cell,
            reproducer,
            fix_cell,
            deployment_cell,
            f"`{defect.get('status', '')}`",
            _markdown_escape(defect.get("residual_risk", "")),
            references,
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _section_block(markdown: str, pattern: re.Pattern[str]) -> str | None:
    match = pattern.search(markdown)
    if match is None:
        return None
    return match.group(0).rstrip("\n") + "\n"


def _summary_block(markdown: str) -> str | None:
    return _section_block(markdown, SUMMARY_PATTERN)


def _validate_markdown(
    ledger: Mapping[str, Any],
    markdown_path: Path,
) -> list[str]:
    errors: list[str] = []
    try:
        markdown = markdown_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [f"defect ledger Markdown does not exist: {markdown_path}"]
    projections = (
        (
            "Identity and evidence boundary",
            IDENTITY_SCOPE_PATTERN,
            render_identity_scope(ledger),
        ),
        ("Summary", SUMMARY_PATTERN, render_summary(ledger)),
        (
            "Scope and incident classification",
            SCOPE_PATTERN,
            render_scope(ledger),
        ),
        (
            "Chronology projection",
            CHRONOLOGY_PATTERN,
            render_chronology(ledger),
        ),
    )
    for name, pattern, expected in projections:
        actual = _section_block(markdown, pattern)
        if actual is None:
            errors.append(f"DEFECT_LEDGER.md has no {name} section")
        elif actual != expected:
            errors.append(
                f"defect ledger Markdown {name} is stale; run "
                "python3 tools/defect_ledger.py render --write"
            )
    defects = ledger.get("defects", [])
    expected_ids = [
        str(item.get("id"))
        for item in defects
        if isinstance(item, Mapping)
    ] if isinstance(defects, list) else []
    record_ids = re.findall(r"(?m)^### (DEF-[0-9]{4})\b", markdown)
    if record_ids != expected_ids:
        errors.append(
            "DEFECT_LEDGER.md record headings must contain every DEF ID once "
            "in canonical order"
        )
    return errors


def _validate_diagnosis(
    ledger: Mapping[str, Any],
    diagnosis_path: Path,
) -> list[str]:
    try:
        markdown = diagnosis_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [f"diagnosis Markdown does not exist: {diagnosis_path}"]
    expected = render_diagnosis_chronology(ledger)
    actual = _section_block(markdown, DIAGNOSIS_PATTERN)
    if actual is None:
        return [
            "why_code_reviews_continue_to_find_major_problems.md has no "
            "Finding chronology and current status section"
        ]
    if actual != expected:
        return [
            "diagnosis Markdown chronology projection is stale; run "
            "python3 tools/defect_ledger.py render --write"
        ]
    return []


def _affected_path_error(
    repository_root: Path,
    raw_path: str,
    *,
    baseline_commit: str,
    baseline_inventory: set[str],
    runtime_source: str,
) -> str | None:
    """Require an affected-file entry to identify a real path or matching glob."""
    if not raw_path or re.search(r"\s", raw_path):
        return f"affected file is not a repository path/glob: {raw_path!r}"
    pure = PurePosixPath(raw_path)
    if pure.is_absolute() or ".." in pure.parts:
        return f"affected file escapes the repository: {raw_path!r}"
    if re.fullmatch(r"[A-Za-z0-9._*?\[\]/-]+", raw_path) is None:
        return f"affected file has invalid path/glob syntax: {raw_path!r}"
    has_glob = any(character in raw_path for character in "*?[")
    if has_glob:
        worktree_matches = list(repository_root.glob(raw_path))
        git_matches = any(
            fnmatch.fnmatchcase(item, raw_path) for item in baseline_inventory
        )
        if not worktree_matches and not git_matches:
            return (
                f"affected file glob matches no worktree or baseline path: "
                f"{raw_path}"
            )
        return None
    if repository_root.joinpath(*pure.parts).exists():
        return None
    if raw_path in baseline_inventory:
        return None
    # Runtime state/control/receipt paths may intentionally be absent. They
    # are still concrete when the baseline source declares their exact name.
    if (
        f'"{raw_path}"' in runtime_source
        or f"'{raw_path}'" in runtime_source
    ):
        return None
    return (
        f"affected file does not exist, is not tracked at {baseline_commit[:8]}, "
        f"and is not an exact runtime-declared path: {raw_path}"
    )


def _external_evidence_checks(
    ledger: Mapping[str, Any],
    *,
    repository_root: Path,
) -> tuple[dict[str, Mapping[str, Any]], list[str], list[str]]:
    records = ledger.get("external_evidence", [])
    evidence_records = records if isinstance(records, list) else []
    by_id: dict[str, Mapping[str, Any]] = {}
    errors: list[str] = []
    warnings: list[str] = []
    for index, record in enumerate(evidence_records):
        if not isinstance(record, Mapping):
            continue
        evidence_id = str(record.get("id", f"index-{index}"))
        if evidence_id in by_id:
            errors.append(f"duplicate external evidence ID: {evidence_id}")
        by_id[evidence_id] = record
        relative = str(record.get("repository_relative_path", ""))
        if repository_root.joinpath(relative).exists():
            errors.append(
                f"{evidence_id}: external-untracked evidence unexpectedly "
                f"resolves inside the ledger worktree: {relative}"
            )
        absolute = Path(str(record.get("observed_absolute_path", "")))
        if not absolute.is_absolute():
            errors.append(
                f"{evidence_id}: observed evidence location is not absolute"
            )
            continue
        if not absolute.is_file():
            warnings.append(
                f"{evidence_id}: external evidence is currently unavailable "
                f"at its recorded observation path; retained digest and "
                "location remain the immutable reference"
            )
            continue
        try:
            actual_digest = hashlib.sha256(absolute.read_bytes()).hexdigest()
        except OSError as exc:
            warnings.append(
                f"{evidence_id}: external evidence could not be re-read: {exc}"
            )
            continue
        expected_digest = str(record.get("sha256", ""))
        if actual_digest != expected_digest:
            errors.append(
                f"{evidence_id}: external evidence SHA-256 mismatch at "
                f"{absolute}"
            )
    defect_references = {
        token
        for defect in ledger.get("defects", [])
        if isinstance(defect, Mapping)
        for token in EXTERNAL_EVIDENCE_ID_PATTERN.findall(
            json.dumps(defect, sort_keys=True)
        )
    } if isinstance(ledger.get("defects"), list) else set()
    for reference in sorted(defect_references - set(by_id)):
        errors.append(f"external evidence reference does not resolve: {reference}")
    return by_id, errors, warnings


def _semantic_errors(
    ledger: Mapping[str, Any],
    schema: Mapping[str, Any],
    invariants: Mapping[str, Any],
    *,
    repository_root: Path,
    release_base: str | None = None,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    defect_values = ledger.get("defects", [])
    defects = defect_values if isinstance(defect_values, list) else []

    taxonomy = ledger.get("status_taxonomy", {})
    taxonomy_statuses = (
        set(str(key) for key in taxonomy)
        if isinstance(taxonomy, Mapping)
        else set()
    )
    schema_statuses_value = (
        schema.get("$defs", {}).get("status", {}).get("enum", [])
        if isinstance(schema.get("$defs"), Mapping)
        and isinstance(schema.get("$defs", {}).get("status"), Mapping)
        else []
    )
    schema_statuses = (
        set(str(item) for item in schema_statuses_value)
        if isinstance(schema_statuses_value, list)
        else set()
    )
    if taxonomy_statuses != schema_statuses:
        errors.append(
            "status taxonomy and schema status enum differ: "
            f"taxonomy={sorted(taxonomy_statuses)!r}, "
            f"schema={sorted(schema_statuses)!r}"
        )
    unsupported_statuses = taxonomy_statuses - KNOWN_STATUS_RULES
    if unsupported_statuses:
        errors.append(
            "status taxonomy has no semantic coherence rule: "
            + ", ".join(sorted(unsupported_statuses))
        )

    invariant_values = invariants.get("invariants", [])
    invariant_ids = {
        str(item.get("id"))
        for item in invariant_values
        if isinstance(invariant_values, list)
        and isinstance(item, Mapping)
        and isinstance(item.get("id"), str)
    }
    duplicate_invariants = [
        item
        for item in invariant_ids
        if sum(
            isinstance(entry, Mapping) and entry.get("id") == item
            for entry in invariant_values
        )
        > 1
    ] if isinstance(invariant_values, list) else []
    if duplicate_invariants:
        errors.append(
            "production invariant registry contains duplicate IDs: "
            + ", ".join(sorted(duplicate_invariants))
        )
    identity_scope = ledger.get("identity_scope", {})
    production_baseline = (
        identity_scope.get("production_baseline", {})
        if isinstance(identity_scope, Mapping)
        else {}
    )
    evidence_cutoff = (
        identity_scope.get("ledger_evidence_cutoff", {})
        if isinstance(identity_scope, Mapping)
        else {}
    )
    candidate_scope = (
        identity_scope.get("candidate_under_review", {})
        if isinstance(identity_scope, Mapping)
        else {}
    )
    production_observation = (
        identity_scope.get("production_deployment_observation", {})
        if isinstance(identity_scope, Mapping)
        else {}
    )
    regeneration = (
        identity_scope.get("post_merge_regeneration", {})
        if isinstance(identity_scope, Mapping)
        else {}
    )
    external_by_id, external_errors, external_warnings = (
        _external_evidence_checks(
            ledger,
            repository_root=repository_root,
        )
    )
    errors.extend(external_errors)
    warnings.extend(external_warnings)
    baseline_commit = (
        str(production_baseline.get("commit", "unknown"))
        if isinstance(production_baseline, Mapping)
        else "unknown"
    )
    cutoff_commit = (
        str(evidence_cutoff.get("commit", "unknown"))
        if isinstance(evidence_cutoff, Mapping)
        else "unknown"
    )
    inventory_commit = cutoff_commit
    inventory_result = _git(
        repository_root,
        "ls-tree",
        "-r",
        "--name-only",
        inventory_commit,
    )
    baseline_inventory = (
        set(inventory_result.stdout.splitlines())
        if inventory_result.returncode == 0
        else set()
    )
    runtime_result = _git(
        repository_root,
        "show",
        f"{inventory_commit}:mrsMThatcher2.py",
    )
    runtime_source = (
        runtime_result.stdout if runtime_result.returncode == 0 else ""
    )
    if isinstance(production_baseline, Mapping):
        registry_baseline = invariants.get("baseline_commit")
        if (
            isinstance(registry_baseline, str)
            and production_baseline.get("commit") != registry_baseline
        ):
            errors.append(
                "ledger production baseline does not match the production "
                "invariant registry baseline"
            )
    if isinstance(evidence_cutoff, Mapping):
        if evidence_cutoff.get("as_of") != ledger.get("as_of"):
            errors.append(
                "ledger evidence cut-off date does not match top-level as_of"
            )
    if isinstance(candidate_scope, Mapping) and (
        candidate_scope.get("identity_source")
        != "external-release-attestation"
        or candidate_scope.get("stored_in_ledger") is not False
    ):
        errors.append(
            "candidate identity must remain external to the committed ledger"
        )
    if isinstance(regeneration, Mapping) and (
        regeneration.get("required") is not True
        or regeneration.get("release_base_must_equal_evidence_cutoff") is not True
    ):
        errors.append(
            "post-merge regeneration and exact release-base comparison must "
            "remain mandatory"
        )
    if release_base is not None:
        if re.fullmatch(r"[0-9a-f]{40}", release_base) is None:
            errors.append("supplied release base is not an exact 40-hex commit")
        elif release_base != cutoff_commit:
            errors.append(
                "supplied release base differs from ledger evidence cut-off; "
                "post-merge ledger regeneration is required before attestation"
            )

    defect_ids = [
        str(item.get("id"))
        for item in defects
        if isinstance(item, Mapping)
    ]
    if defect_ids != sorted(defect_ids):
        errors.append("defect IDs must be sorted lexicographically")
    seen_ids: set[str] = set()
    defect_by_id: dict[str, Mapping[str, Any]] = {}
    for index, defect in enumerate(defects):
        if not isinstance(defect, Mapping):
            continue
        defect_id = str(defect.get("id") or f"index-{index}")
        if DEFECT_ID_PATTERN.fullmatch(defect_id) is None:
            errors.append(f"$.defects[{index}].id: invalid stable DEF ID {defect_id!r}")
        if defect_id in seen_ids:
            errors.append(f"$.defects[{index}].id: duplicate defect ID {defect_id}")
        seen_ids.add(defect_id)
        defect_by_id[defect_id] = defect

    for index, defect in enumerate(defects):
        if not isinstance(defect, Mapping):
            continue
        defect_id = str(defect.get("id") or f"index-{index}")
        status = defect.get("status")
        fix = defect.get("fix", {})
        deployment = defect.get("deployment", {})
        relations = defect.get("relations", {})
        tests = defect.get("tests", [])
        evidence = defect.get("evidence", [])
        introduced = defect.get("introduced", {})
        chronology = defect.get("chronology", [])

        for affected_path in defect.get("affected_files", []):
            path_error = _affected_path_error(
                repository_root,
                str(affected_path),
                baseline_commit=inventory_commit,
                baseline_inventory=baseline_inventory,
                runtime_source=runtime_source,
            )
            if path_error is not None:
                errors.append(f"{defect_id}: {path_error}")

        for invariant_id in defect.get("invariant_ids", []):
            if invariant_id not in invariant_ids:
                errors.append(
                    f"{defect_id}: invariant reference does not resolve: "
                    f"{invariant_id}"
                )

        unknown_records = defect.get("unknowns", [])
        unknown_fields: dict[str, str] = {}
        for unknown in unknown_records:
            if not isinstance(unknown, Mapping):
                continue
            field = str(unknown.get("field", ""))
            explanation = str(unknown.get("explanation", "")).strip()
            if field in unknown_fields:
                errors.append(f"{defect_id}: duplicate unknown explanation for {field}")
            unknown_fields[field] = explanation
            if len(explanation) < 12:
                errors.append(
                    f"{defect_id}: unknown value {field} lacks a substantive "
                    "explanation"
                )
        inline_explained_unknowns = {"introduced.confidence"}
        incident = defect.get("incident", {})
        if (
            isinstance(incident, Mapping)
            and incident.get("occurred") == "unknown"
            and len(str(incident.get("explanation", "")).strip()) >= 12
        ):
            inline_explained_unknowns.add("incident.occurred")
        actual_unknowns = {
            path
            for path in _unknown_paths(defect)
            if path not in inline_explained_unknowns
        }
        for field in sorted(actual_unknowns - set(unknown_fields)):
            errors.append(
                f"{defect_id}: unknown value {field} has no recorded explanation"
            )
        for field in sorted(set(unknown_fields) - actual_unknowns):
            errors.append(
                f"{defect_id}: unknown explanation targets a field that is not "
                f"unknown: {field}"
            )

        if isinstance(introduced, Mapping):
            confidence = introduced.get("confidence")
            first_bad = introduced.get("first_bad_commit")
            last_good = introduced.get("last_known_good_commit")
            affected_range = str(introduced.get("affected_range", ""))
            if confidence == "exact" and (
                first_bad == "unknown" or last_good == "unknown"
            ):
                errors.append(
                    f"{defect_id}: exact introduction requires known first-bad "
                    "and last-known-good commits"
                )
            if confidence == "unknown" and first_bad != "unknown":
                errors.append(
                    f"{defect_id}: unknown introduction confidence cannot claim "
                    "a known first-bad commit"
                )
            if first_bad != "unknown" and first_bad == last_good:
                errors.append(
                    f"{defect_id}: first-bad and last-known-good commits are equal"
                )
            if affected_range == "unknown":
                if first_bad != "unknown":
                    errors.append(
                        f"{defect_id}: known first-bad commit requires an "
                        "explicit inclusive affected range"
                    )
            else:
                range_match = INCLUSIVE_RANGE_PATTERN.fullmatch(affected_range)
                if range_match is None:
                    errors.append(
                        f"{defect_id}: affected range must use "
                        "inclusive:<first>..<last> notation"
                    )
                else:
                    range_start, _range_end = range_match.groups()
                    expected_start = str(first_bad)
                    if range_start != expected_start:
                        errors.append(
                            f"{defect_id}: affected-range first endpoint "
                            "does not match first_bad_commit"
                        )

        if isinstance(chronology, list):
            dates = [
                str(event.get("date", ""))
                for event in chronology
                if isinstance(event, Mapping)
            ]
            if dates != sorted(dates):
                errors.append(f"{defect_id}: chronology is not sorted by date")

        fix_state = fix.get("state") if isinstance(fix, Mapping) else None
        fix_commit = fix.get("commit") if isinstance(fix, Mapping) else None
        deployment_state = (
            deployment.get("state") if isinstance(deployment, Mapping) else None
        )
        observed_commit = (
            deployment.get("observed_commit")
            if isinstance(deployment, Mapping)
            else None
        )
        observed_at = (
            deployment.get("observed_at")
            if isinstance(deployment, Mapping)
            else None
        )
        deployed_behavior_ok = False
        if fix_state == "fixed":
            if fix_commit == "unknown":
                errors.append(f"{defect_id}: fixed state requires a fix commit")
            if not tests:
                errors.append(f"{defect_id}: fixed state requires regression evidence")
            if isinstance(chronology, list) and fix_commit not in {
                item.get("commit") for item in chronology if isinstance(item, Mapping)
            }:
                errors.append(
                    f"{defect_id}: fix commit is absent from chronology"
                )
            if isinstance(tests, list) and fix_commit not in {
                item.get("commit") for item in tests if isinstance(item, Mapping)
            }:
                errors.append(
                    f"{defect_id}: no recorded test is bound to the fix commit"
                )
        elif fix_state in {"unfixed", "not-applicable"} and fix_commit != "unknown":
            errors.append(
                f"{defect_id}: {fix_state} fix state must use commit unknown"
            )

        if deployment_state in {"not-deployed", "deployed-unverified", "deployed-verified"}:
            if fix_state != "fixed" or fix_commit == "unknown":
                errors.append(
                    f"{defect_id}: {deployment_state} requires a known fixed repair"
                )
            if observed_commit == "unknown" or observed_at == "unknown":
                errors.append(
                    f"{defect_id}: {deployment_state} requires a dated observed "
                    "deployment commit"
                )
        if deployment_state == "not-deployed" and observed_commit == fix_commit:
            errors.append(
                f"{defect_id}: not-deployed observation cannot equal the fix commit"
            )
        if deployment_state == "deployed-verified":
            behavior_evidence = [
                item
                for item in evidence
                if isinstance(item, Mapping)
                and item.get("type")
                in {"runtime-observation", "artifact", "report"}
                and str(item.get("reference", "")) in external_by_id
                and "post-deployment" in str(item.get("claim", "")).lower()
                and any(
                    invariant_id in str(item.get("claim", ""))
                    for invariant_id in defect.get("invariant_ids", [])
                )
            ] if isinstance(evidence, list) else []
            deployed_behavior_ok = bool(behavior_evidence)
            if not deployed_behavior_ok:
                errors.append(
                    f"{defect_id}: deployed-verified requires structured "
                    "post-deployment evidence that resolves to an immutable "
                    "external record and names an affected invariant"
                )

        if status == "active":
            if fix_state != "unfixed":
                errors.append(f"{defect_id}: active status requires an unfixed repair")
            if deployment_state == "deployed-verified":
                errors.append(
                    f"{defect_id}: active status cannot be deployed-verified"
                )
        elif status == "latent-disabled":
            text = " ".join(
                [
                    str(defect.get("summary", "")),
                    str(defect.get("residual_risk", "")),
                    str(deployment.get("evidence", ""))
                    if isinstance(deployment, Mapping)
                    else "",
                ]
            )
            if not re.search(r"\b(?:disabled|shadow|unsupported)\b", text, re.I):
                errors.append(
                    f"{defect_id}: latent-disabled status must name the disabled "
                    "or shadow boundary"
                )
            if deployment_state == "deployed-verified":
                errors.append(
                    f"{defect_id}: latent-disabled cannot claim deployed-verified"
                )
        elif status == "repaired-not-deployed":
            if fix_state != "fixed" or deployment_state != "not-deployed":
                errors.append(
                    f"{defect_id}: repaired-not-deployed requires fixed plus "
                    "deployment.state=not-deployed"
                )
        elif status == "deployed-unverified":
            if fix_state != "fixed" or deployment_state != "deployed-unverified":
                errors.append(
                    f"{defect_id}: deployed-unverified requires fixed plus "
                    "deployment.state=deployed-unverified"
                )
        elif status == "deployed-verified":
            if fix_state != "fixed" or deployment_state != "deployed-verified":
                errors.append(
                    f"{defect_id}: deployed-verified status/deployment states differ"
                )
        elif status == "assurance-weakness":
            if defect.get("severity") != "assurance":
                errors.append(
                    f"{defect_id}: assurance-weakness requires assurance severity"
                )
            if deployment_state != "not-applicable":
                errors.append(
                    f"{defect_id}: assurance-weakness deployment must be "
                    "not-applicable"
                )
        elif status == "superseded":
            superseded_by = (
                relations.get("superseded_by", [])
                if isinstance(relations, Mapping)
                else []
            )
            if (
                fix_state != "not-applicable"
                or deployment_state != "not-applicable"
                or not superseded_by
            ):
                errors.append(
                    f"{defect_id}: superseded requires not-applicable fix/"
                    "deployment and at least one superseding record"
                )

        for test_index, test in enumerate(tests if isinstance(tests, list) else []):
            if not isinstance(test, Mapping):
                continue
            exists, reason = test_node_exists(
                repository_root,
                str(test.get("path", "")),
                str(test.get("nodeid", "")),
                str(test.get("commit", "")),
            )
            if not exists:
                errors.append(f"{defect_id}: test {test_index}: {reason}")

        independent_evidence = deployed_behavior_ok or any(
            isinstance(item, Mapping)
            and item.get("type") in {"runtime-observation", "artifact", "report"}
            for item in evidence
        ) if isinstance(evidence, list) else False
        for item in evidence if isinstance(evidence, list) else []:
            if not isinstance(item, Mapping):
                continue
            claim = str(item.get("claim", ""))
            if (
                item.get("type") in LOCAL_EVIDENCE_TYPES
                and SYSTEM_WIDE_CLAIM_PATTERN.search(claim)
            ):
                errors.append(
                    f"{defect_id}: patch-local {item.get('type')} evidence "
                    "overclaims system-wide closure"
                )
        if (
            status == "deployed-verified"
            and not independent_evidence
        ):
            errors.append(
                f"{defect_id}: deployed-verified cannot rely only on patch-local "
                "tests/code/commit evidence"
            )

        if isinstance(relations, Mapping):
            for relation_name in ("supersedes", "superseded_by", "related"):
                for target in relations.get(relation_name, []):
                    if target == defect_id:
                        errors.append(
                            f"{defect_id}: {relation_name} cannot reference itself"
                        )
                    elif target not in defect_by_id:
                        errors.append(
                            f"{defect_id}: {relation_name} does not resolve: {target}"
                        )
            for target in relations.get("supersedes", []):
                other = defect_by_id.get(str(target), {})
                reverse = (
                    other.get("relations", {}).get("superseded_by", [])
                    if isinstance(other.get("relations"), Mapping)
                    else []
                )
                if defect_id not in reverse:
                    errors.append(
                        f"{defect_id}: supersedes relation to {target} is not reciprocal"
                    )
            for target in relations.get("superseded_by", []):
                other = defect_by_id.get(str(target), {})
                reverse = (
                    other.get("relations", {}).get("supersedes", [])
                    if isinstance(other.get("relations"), Mapping)
                    else []
                )
                if defect_id not in reverse:
                    errors.append(
                        f"{defect_id}: superseded_by relation to {target} is not "
                        "reciprocal"
                    )

    git_probe = _git(repository_root, "rev-parse", "--is-inside-work-tree")
    if git_probe.returncode != 0:
        warnings.append("Git commit and deployment ancestry checks were skipped")
    else:
        for commit in sorted(_known_commit_values(ledger)):
            result = _git(repository_root, "cat-file", "-e", f"{commit}^{{commit}}")
            if result.returncode != 0:
                errors.append(f"ledger commit does not resolve in Git: {commit}")
        for label, record, commit_field, tree_field in (
            (
                "production baseline",
                production_baseline,
                "commit",
                "tree",
            ),
            (
                "ledger evidence cut-off",
                evidence_cutoff,
                "commit",
                "tree",
            ),
            (
                "production deployment observation",
                production_observation,
                "repository_commit",
                "repository_tree",
            ),
        ):
            if isinstance(record, Mapping):
                current_commit = record.get(commit_field)
                expected_tree = record.get(tree_field)
            else:
                current_commit = None
                expected_tree = None
            if isinstance(current_commit, str) and current_commit != "unknown":
                actual_tree = _git(
                    repository_root, "rev-parse", f"{current_commit}^{{tree}}"
                )
                if (
                    actual_tree.returncode == 0
                    and actual_tree.stdout.strip() != expected_tree
                ):
                    errors.append(
                        f"{label} tree does not belong to its recorded commit"
                    )
        if (
            isinstance(baseline_commit, str)
            and isinstance(cutoff_commit, str)
            and baseline_commit != "unknown"
            and cutoff_commit != "unknown"
        ):
            baseline_to_cutoff = _git(
                repository_root,
                "merge-base",
                "--is-ancestor",
                baseline_commit,
                cutoff_commit,
            )
            if baseline_to_cutoff.returncode != 0:
                errors.append(
                    "production baseline is not an ancestor of the ledger "
                    "evidence cut-off"
                )
        observed_production_commit = (
            production_observation.get("repository_commit")
            if isinstance(production_observation, Mapping)
            else None
        )
        if (
            isinstance(observed_production_commit, str)
            and observed_production_commit != "unknown"
            and isinstance(cutoff_commit, str)
            and cutoff_commit != "unknown"
        ):
            observation_to_cutoff = _git(
                repository_root,
                "merge-base",
                "--is-ancestor",
                observed_production_commit,
                cutoff_commit,
            )
            if observation_to_cutoff.returncode != 0:
                errors.append(
                    "production deployment observation is newer than or "
                    "unrelated to the ledger evidence cut-off"
                )
        for defect in defects:
            if not isinstance(defect, Mapping):
                continue
            defect_id = str(defect.get("id", "unknown"))
            fix = defect.get("fix", {})
            deployment = defect.get("deployment", {})
            introduced = defect.get("introduced", {})
            if not isinstance(fix, Mapping) or not isinstance(deployment, Mapping):
                continue
            fix_commit = fix.get("commit")
            observed_commit = deployment.get("observed_commit")
            state = deployment.get("state")
            affected_range = (
                str(introduced.get("affected_range", "unknown"))
                if isinstance(introduced, Mapping)
                else "unknown"
            )
            range_match = INCLUSIVE_RANGE_PATTERN.fullmatch(affected_range)
            if range_match is not None:
                range_start, range_end = range_match.groups()
                current_commit = cutoff_commit
                if range_start != "unknown":
                    start_ancestor = _git(
                        repository_root,
                        "merge-base",
                        "--is-ancestor",
                        range_start,
                        range_end,
                    )
                    if start_ancestor.returncode != 0:
                        errors.append(
                            f"{defect_id}: affected-range first endpoint is "
                            "not an ancestor of its inclusive last endpoint"
                        )
                if isinstance(current_commit, str):
                    end_on_baseline = _git(
                        repository_root,
                        "merge-base",
                        "--is-ancestor",
                        range_end,
                        current_commit,
                    )
                    if end_on_baseline.returncode != 0:
                        errors.append(
                            f"{defect_id}: affected-range last endpoint is "
                            "outside the ledger evidence cut-off"
                        )
                if fix.get("state") == "fixed" and isinstance(fix_commit, str):
                    parent = _git(
                        repository_root,
                        "rev-parse",
                        f"{fix_commit}^",
                    )
                    if (
                        parent.returncode == 0
                        and range_end != parent.stdout.strip()
                    ):
                        errors.append(
                            f"{defect_id}: fixed record's inclusive affected "
                            "range must end at the fix commit's parent"
                        )
                elif (
                    fix.get("state") == "unfixed"
                    and isinstance(current_commit, str)
                    and range_end != current_commit
                ):
                    errors.append(
                        f"{defect_id}: unfixed record's inclusive affected "
                        "range must end at the ledger evidence cut-off"
                    )
            for claim_name, claim_commit in (
                ("fix", fix_commit),
                ("deployment", observed_commit),
            ):
                if (
                    isinstance(claim_commit, str)
                    and claim_commit != "unknown"
                    and isinstance(cutoff_commit, str)
                    and cutoff_commit != "unknown"
                ):
                    claim_at_cutoff = _git(
                        repository_root,
                        "merge-base",
                        "--is-ancestor",
                        claim_commit,
                        cutoff_commit,
                    )
                    if claim_at_cutoff.returncode != 0:
                        errors.append(
                            f"{defect_id}: {claim_name} claim commit is outside "
                            "the ledger evidence cut-off"
                        )
            if (
                isinstance(fix_commit, str)
                and isinstance(observed_commit, str)
                and fix_commit != "unknown"
                and observed_commit != "unknown"
            ):
                ancestor = _git(
                    repository_root,
                    "merge-base",
                    "--is-ancestor",
                    fix_commit,
                    observed_commit,
                )
                if state in {"deployed-unverified", "deployed-verified"}:
                    if ancestor.returncode != 0:
                        errors.append(
                            f"{defect_id}: deployed observation does not contain "
                            "the recorded fix commit"
                        )
                elif state == "not-deployed" and ancestor.returncode == 0:
                    errors.append(
                        f"{defect_id}: not-deployed observation already contains "
                        "the recorded fix commit"
                    )
    return errors, warnings


def validate_ledger(
    ledger: Mapping[str, Any],
    schema: Mapping[str, Any],
    invariants: Mapping[str, Any],
    *,
    repository_root: Path,
    markdown_path: Path | None = None,
    diagnosis_path: Path | None = None,
    force_fallback_schema: bool = False,
    release_base: str | None = None,
) -> ValidationReport:
    """Validate schema, evidence semantics, references, tests, Git, and Markdown."""
    backend, errors = schema_validation_errors(
        ledger,
        schema,
        force_fallback=force_fallback_schema,
    )
    semantic_errors, warnings = _semantic_errors(
        ledger,
        schema,
        invariants,
        repository_root=repository_root,
        release_base=release_base,
    )
    errors.extend(semantic_errors)
    if markdown_path is not None:
        errors.extend(_validate_markdown(ledger, markdown_path))
    if diagnosis_path is not None:
        errors.extend(_validate_diagnosis(ledger, diagnosis_path))
    counts: dict[str, int] = {}
    defects = ledger.get("defects", [])
    if isinstance(defects, list):
        for defect in defects:
            if isinstance(defect, Mapping):
                status = str(defect.get("status", "unknown"))
                counts[status] = counts.get(status, 0) + 1
    return ValidationReport(
        schema_backend=backend,
        errors=tuple(sorted(set(errors))),
        warnings=tuple(sorted(set(warnings))),
        status_counts=tuple(sorted(counts.items())),
    )


def _resolved_path(repository_root: Path, supplied: Path) -> Path:
    return supplied if supplied.is_absolute() else repository_root / supplied


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    validate = subparsers.add_parser(
        "validate",
        help="validate ledger structure, semantics, evidence, and Markdown",
    )
    validate.add_argument("--repo-root", type=Path, default=DEFAULT_REPOSITORY_ROOT)
    validate.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    validate.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    validate.add_argument("--invariants", type=Path, default=DEFAULT_INVARIANTS)
    validate.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    validate.add_argument("--diagnosis", type=Path, default=DEFAULT_DIAGNOSIS)
    validate.add_argument("--json", action="store_true")
    validate.add_argument("--no-markdown-check", action="store_true")
    validate.add_argument("--no-diagnosis-check", action="store_true")
    validate.add_argument("--force-fallback-schema", action="store_true")
    validate.add_argument(
        "--release-base",
        help=(
            "exact frozen release base; must equal the ledger evidence cut-off "
            "or validation requires ledger regeneration"
        ),
    )

    render = subparsers.add_parser(
        "render",
        help="render deterministic ledger and diagnosis projections",
    )
    render.add_argument("--repo-root", type=Path, default=DEFAULT_REPOSITORY_ROOT)
    render.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    render.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    render.add_argument("--output", type=Path, default=DEFAULT_MARKDOWN)
    render.add_argument("--diagnosis", type=Path, default=DEFAULT_DIAGNOSIS)
    mode = render.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    return parser


def _load_inputs(
    repository_root: Path,
    ledger_path: Path,
    schema_path: Path,
    invariants_path: Path | None = None,
) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any] | None]:
    ledger = load_json_document(_resolved_path(repository_root, ledger_path))
    schema = load_json_document(_resolved_path(repository_root, schema_path))
    invariants = (
        load_json_document(_resolved_path(repository_root, invariants_path))
        if invariants_path is not None
        else None
    )
    if not isinstance(ledger, Mapping) or not isinstance(schema, Mapping):
        raise ValueError("ledger and schema must both be JSON objects")
    if invariants_path is not None and not isinstance(invariants, Mapping):
        raise ValueError("production invariant registry must be a JSON object")
    return ledger, schema, invariants


def _replace_projection(
    markdown: str,
    pattern: re.Pattern[str],
    rendered: str,
    *,
    label: str,
) -> str:
    match = pattern.search(markdown)
    if match is None:
        raise ValueError(f"Markdown has no {label} section")
    return (
        markdown[: match.start()]
        + rendered.rstrip("\n")
        + "\n\n"
        + markdown[match.end() :]
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run validation or deterministic summary rendering."""
    args = _build_parser().parse_args(argv)
    repository_root = args.repo_root.resolve()
    try:
        if args.action == "render":
            ledger, schema, _ = _load_inputs(
                repository_root, args.ledger, args.schema
            )
            backend, errors = schema_validation_errors(ledger, schema)
            if errors:
                print(f"schema backend: {backend}", file=sys.stderr)
                for error in errors:
                    print(f"ERROR: {error}", file=sys.stderr)
                return 1
            ledger_projections = (
                (
                    "Identity and evidence boundary",
                    IDENTITY_SCOPE_PATTERN,
                    render_identity_scope(ledger),
                ),
                ("Summary", SUMMARY_PATTERN, render_summary(ledger)),
                (
                    "Scope and incident classification",
                    SCOPE_PATTERN,
                    render_scope(ledger),
                ),
                (
                    "Chronology projection",
                    CHRONOLOGY_PATTERN,
                    render_chronology(ledger),
                ),
            )
            diagnosis_rendered = render_diagnosis_chronology(ledger)
            output = _resolved_path(repository_root, args.output)
            diagnosis_output = _resolved_path(repository_root, args.diagnosis)
            if args.check:
                ledger_errors = _validate_markdown(ledger, output)
                diagnosis_errors = _validate_diagnosis(
                    ledger,
                    diagnosis_output,
                )
                if ledger_errors or diagnosis_errors:
                    for error in ledger_errors + diagnosis_errors:
                        print(f"ERROR: {error}", file=sys.stderr)
                    return 1
                print(
                    "defect ledger and diagnosis Markdown projections are "
                    "synchronized"
                )
                return 0
            if args.write:
                current = output.read_text(encoding="utf-8")
                updated = current
                for label, pattern, rendered in ledger_projections:
                    updated = _replace_projection(
                        updated,
                        pattern,
                        rendered,
                        label=label,
                    )
                output.write_text(updated, encoding="utf-8")
                diagnosis_current = diagnosis_output.read_text(encoding="utf-8")
                diagnosis_updated = _replace_projection(
                    diagnosis_current,
                    DIAGNOSIS_PATTERN,
                    diagnosis_rendered,
                    label="Finding chronology and current status",
                )
                diagnosis_output.write_text(
                    diagnosis_updated,
                    encoding="utf-8",
                )
                print(
                    f"updated ledger projections in {output} and chronology "
                    f"projection in {diagnosis_output}"
                )
                return 0
            sys.stdout.write("\n".join(item[2].rstrip() for item in ledger_projections))
            sys.stdout.write("\n\n")
            sys.stdout.write(diagnosis_rendered)
            return 0

        ledger, schema, invariants = _load_inputs(
            repository_root,
            args.ledger,
            args.schema,
            args.invariants,
        )
        assert invariants is not None
        markdown_path = (
            None
            if args.no_markdown_check
            else _resolved_path(repository_root, args.markdown)
        )
        diagnosis_path = (
            None
            if args.no_diagnosis_check
            else _resolved_path(repository_root, args.diagnosis)
        )
        report = validate_ledger(
            ledger,
            schema,
            invariants,
            repository_root=repository_root,
            markdown_path=markdown_path,
            diagnosis_path=diagnosis_path,
            force_fallback_schema=args.force_fallback_schema,
            release_base=args.release_base,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"defect ledger input error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(strict_json.canonical_dumps(report.to_dict()))
    else:
        print(f"schema backend: {report.schema_backend}")
        print(f"defect ledger valid: {'yes' if report.ok else 'no'}")
        print(
            "statuses: "
            + " ".join(f"{name}={count}" for name, count in report.status_counts)
        )
        for error in report.errors:
            print(f"ERROR: {error}")
        for warning in report.warnings:
            print(f"WARNING: {warning}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
