#!/usr/bin/env python3
"""Pure, offline transport-to-canonical evidence resolution.

The provider-facing transport delta contains literal evidence selectors.  This
module binds those selectors to one already-available current turn and derives
the canonical Unicode-code-point, end-exclusive evidence spans.  It has no
provider SDK, HTTP, gRPC, X, or production imports.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TRANSPORT_SCHEMA_VERSION = "proposition-ledger-xai-transport-delta-v2.0.0"
CANONICAL_SCHEMA_VERSION = "proposition-ledger-semantic-delta-v1.1.0"
CANONICAL_SCHEMA_FILE_SHA256 = (
    "eea15c28f5019cea405bfdd65b924ee18c76dc428f16a1fe502592b5cb953d8a"
)
EVIDENCE_SELECTOR_CONTRACT_VERSION = "exact-evidence-selector-v1"
REQUEST_CONTRACT_REVISION = "phase2b-exact-evidence-selector-v1"
EVIDENCE_RESOLVER_VERSION = "proposition-ledger-evidence-transport-v1"

SUCCESS_STATUS = "ok"
FAILURE_STATUSES = frozenset(
    {
        "transport_schema_invalid",
        "transport_binding_invalid",
        "evidence_exact_text_not_found",
        "evidence_occurrence_index_out_of_range",
        "evidence_selector_duplicate",
        "evidence_selector_unauthorised",
        "canonical_delta_validation_failed",
        "evidence_resolution_invariant_failure",
    }
)
MAX_ERRORS = 32
MAX_ERROR_LENGTH = 512
PROVIDER_EVIDENCE_FIELDS = frozenset({"turn_id", "start_char", "end_char"})
SELECTOR_FIELDS = frozenset({"exact_text", "occurrence_index"})
CANONICAL_SPAN_FIELDS = frozenset(
    {"turn_id", "start_char", "end_char", "exact_text"}
)

PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CANONICAL_SCHEMA_PATH = (
    PROJECT_DIR
    / "proposition_ledger_research/schema/"
    "proposition-ledger-semantic-delta-v1.schema.json"
)
DEFAULT_TRANSPORT_SCHEMA_PATH = (
    PROJECT_DIR
    / "proposition_ledger_research/schema/"
    "proposition-ledger-xai-transport-delta-v2.schema.json"
)

# These are the complete frozen canonical pattern set.  Full matching the
# anchor-free interior implements the intended ECMA-262 boundary semantics for
# this deliberately narrow ASCII subset, avoiding Python re's special handling
# of ``$`` immediately before a final newline.
PROVED_CANONICAL_PATTERNS = frozenset(
    {
        "^[A-Za-z][A-Za-z0-9_.:-]{0,127}$",
        "^new-proposition-[1-9][0-9]{0,2}$",
        "^new-proposition-group-[1-9][0-9]{0,2}$",
        "^new-issue-[1-9][0-9]{0,2}$",
        "^new-commitment-[1-9][0-9]{0,2}$",
        "^new-obligation-[1-9][0-9]{0,2}$",
        "^new-relation-[1-9][0-9]{0,2}$",
        "^new-answer-target-[1-9][0-9]{0,2}$",
        "^new-rejected-answer-target-[1-9][0-9]{0,2}$",
        "^new-repair-[1-9][0-9]{0,2}$",
        "^new-warning-[1-9][0-9]{0,2}$",
        "^new-alternative-[1-9][0-9]{0,2}$",
        "^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$",
    }
)
PROVED_PROVIDER_PATTERNS = frozenset(
    pattern[1:-1] for pattern in PROVED_CANONICAL_PATTERNS
)
_INTENDED_VALIDATOR_CLASSES: dict[tuple[type[Any], str], type[Any]] = {}
_CHECKED_SCHEMA_DIGESTS: set[tuple[type[Any], str]] = set()


class EvidenceTransportError(RuntimeError):
    """Raised only by schema generation or caller-contract helpers."""


@dataclass(frozen=True)
class EvidenceResolutionResult:
    """A successful canonical delta or one bounded, typed failure."""

    status: str
    canonical_delta: dict[str, Any] | None = None
    resolution_manifest: dict[str, Any] | None = None
    errors: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        """Return whether a complete canonical delta was produced."""

        return self.status == SUCCESS_STATUS

    def as_dict(self) -> dict[str, Any]:
        """Return a deterministic JSON-compatible representation."""

        return {
            "status": self.status,
            "canonical_delta": copy.deepcopy(self.canonical_delta),
            "resolution_manifest": copy.deepcopy(self.resolution_manifest),
            "errors": list(self.errors),
        }


def canonical_json_bytes(value: Any) -> bytes:
    """Return the repository's canonical compact JSON encoding."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def generated_schema_bytes(schema: Mapping[str, Any]) -> bytes:
    """Return the deterministic tracked-file encoding for a generated schema."""

    return (
        json.dumps(schema, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    """Return a lower-case SHA-256 hex digest."""

    return hashlib.sha256(value).hexdigest()


def value_sha256(value: Any) -> str:
    """Hash a JSON value independently of object member insertion order."""

    return sha256_bytes(canonical_json_bytes(value))


def generated_schema_file_sha256(schema: Mapping[str, Any]) -> str:
    """Hash the exact deterministic tracked schema file bytes."""

    return sha256_bytes(generated_schema_bytes(schema))


def _bounded_errors(errors: Iterable[Any]) -> tuple[str, ...]:
    result: list[str] = []
    for error in errors:
        if len(result) >= MAX_ERRORS:
            break
        text = str(error).replace("\n", " ")
        result.append(text[:MAX_ERROR_LENGTH])
    return tuple(result or ["unspecified_failure"])


def _failure(status: str, *errors: Any) -> EvidenceResolutionResult:
    if status not in FAILURE_STATUSES:
        raise RuntimeError(f"unknown evidence-transport failure status: {status}")
    return EvidenceResolutionResult(status=status, errors=_bounded_errors(errors))


def _escape_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _pointer(path: Sequence[str | int]) -> str:
    return "".join(f"/{_escape_pointer(str(part))}" for part in path)


def _pointer_join(pointer: str, part: str | int) -> str:
    return f"{pointer}/{_escape_pointer(str(part))}"


def _resolve_local_ref(root: Mapping[str, Any], reference: str) -> Any:
    if reference == "#":
        return root
    if not reference.startswith("#/"):
        raise EvidenceTransportError(f"non-local schema reference: {reference}")
    value: Any = root
    for encoded in reference[2:].split("/"):
        part = encoded.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, Mapping) or part not in value:
            raise EvidenceTransportError(f"unresolved schema reference: {reference}")
        value = value[part]
    return value


def _iter_schema_nodes(value: Any, pointer: str = "") -> Iterable[tuple[str, Any]]:
    """Walk only JSON Schema positions, not ordinary annotation values."""

    if not isinstance(value, (Mapping, bool)):
        return
    yield pointer, value
    if not isinstance(value, Mapping):
        return
    for key in (
        "additionalProperties",
        "contains",
        "contentSchema",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    ):
        child = value.get(key)
        if isinstance(child, (Mapping, bool)):
            yield from _iter_schema_nodes(child, _pointer_join(pointer, key))
    for key in (
        "$defs",
        "definitions",
        "dependentSchemas",
        "patternProperties",
        "properties",
    ):
        children = value.get(key)
        if isinstance(children, Mapping):
            for name, child in children.items():
                if isinstance(child, (Mapping, bool)):
                    yield from _iter_schema_nodes(
                        child,
                        _pointer_join(_pointer_join(pointer, key), name),
                    )
    for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
        children = value.get(key)
        if isinstance(children, list):
            for index, child in enumerate(children):
                if isinstance(child, (Mapping, bool)):
                    yield from _iter_schema_nodes(
                        child,
                        _pointer_join(_pointer_join(pointer, key), index),
                    )


def _pattern_inventory(schema: Mapping[str, Any]) -> frozenset[str]:
    return frozenset(
        node["pattern"]
        for _, node in _iter_schema_nodes(schema)
        if isinstance(node, Mapping) and isinstance(node.get("pattern"), str)
    )


def _intended_validation_errors(
    schema: Mapping[str, Any],
    instance: Any,
    *,
    pattern_mode: str = "canonical_outer_anchors",
) -> tuple[str, ...]:
    """Validate using the proved whole-string semantics of the frozen patterns."""

    try:
        import jsonschema
        from jsonschema import validators
        from jsonschema.exceptions import SchemaError, ValidationError
    except ImportError as exc:  # pragma: no cover - validated environment owns this
        raise EvidenceTransportError("jsonschema is required for validation") from exc

    if pattern_mode == "canonical_outer_anchors":
        expected = PROVED_CANONICAL_PATTERNS
    elif pattern_mode == "xai_full_string":
        expected = PROVED_PROVIDER_PATTERNS
    else:
        raise EvidenceTransportError(f"unknown pattern mode: {pattern_mode}")

    unexpected = sorted(_pattern_inventory(schema) - expected)
    if unexpected:
        return tuple(f"schema_pattern_outside_proved_subset:{item!r}" for item in unexpected)

    base = getattr(jsonschema, "Draft202012Validator", jsonschema.Draft7Validator)

    def validate_pattern(
        validator: Any,
        pattern: Any,
        candidate: Any,
        pattern_schema: Any,
    ) -> Iterable[Any]:
        del validator, pattern_schema
        if not isinstance(candidate, str) or not isinstance(pattern, str):
            return
        if pattern_mode == "canonical_outer_anchors":
            if pattern not in PROVED_CANONICAL_PATTERNS:
                yield ValidationError("pattern outside proved canonical subset")
                return
            expression = pattern[1:-1]
        else:
            if pattern not in PROVED_PROVIDER_PATTERNS:
                yield ValidationError("pattern outside proved provider subset")
                return
            expression = pattern
        if re.fullmatch(expression, candidate, flags=re.ASCII) is None:
            yield ValidationError(
                f"{candidate!r} does not match the proved full-string pattern"
            )

    validator_key = (base, pattern_mode)
    validator_class = _INTENDED_VALIDATOR_CLASSES.get(validator_key)
    if validator_class is None:
        validator_class = validators.extend(base, {"pattern": validate_pattern})
        _INTENDED_VALIDATOR_CLASSES[validator_key] = validator_class
    try:
        schema_digest = value_sha256(schema)
        checked_key = (validator_class, schema_digest)
        if checked_key not in _CHECKED_SCHEMA_DIGESTS:
            validator_class.check_schema(schema)
            _CHECKED_SCHEMA_DIGESTS.add(checked_key)
        errors = sorted(
            validator_class(schema).iter_errors(instance),
            key=lambda error: (list(error.absolute_path), error.message),
        )
    except SchemaError as exc:
        return (f"invalid_json_schema:{exc.message}",)
    return tuple(
        f"/{'/'.join(_escape_pointer(str(part)) for part in error.absolute_path)}: "
        f"{error.message}"
        for error in errors
    )


def intended_validation_errors(
    schema: Mapping[str, Any],
    instance: Any,
    *,
    pattern_mode: str = "canonical_outer_anchors",
) -> tuple[str, ...]:
    """Public pure wrapper for corrected intended-pattern validation."""

    return _intended_validation_errors(schema, instance, pattern_mode=pattern_mode)


def _evidence_selector_schema() -> dict[str, Any]:
    return {
        "description": (
            "Provider-visible exact current-turn evidence selector. Literal Python "
            "Unicode string matches include overlaps and are ordered by increasing "
            "code-point start index; no normalisation or case folding occurs."
        ),
        "type": "object",
        "additionalProperties": False,
        "required": ["exact_text", "occurrence_index"],
        "properties": {
            "exact_text": {
                "description": (
                    "Non-empty literal substring copied exactly from the current turn."
                ),
                "type": "string",
                "minLength": 1,
            },
            "occurrence_index": {
                "description": (
                    "Zero-based index among all exact, potentially overlapping matches "
                    "ordered by increasing Unicode-code-point start position."
                ),
                "type": "integer",
                "minimum": 0,
            },
        },
    }


def derive_transport_schema(
    canonical_schema: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Derive transport schema v2 and its deterministic transformation ledger."""

    if not isinstance(canonical_schema, Mapping):
        raise EvidenceTransportError("canonical schema must be an object")
    canonical_version = (
        canonical_schema.get("properties", {})
        .get("schema_version", {})
        .get("const")
    )
    if canonical_version != CANONICAL_SCHEMA_VERSION:
        raise EvidenceTransportError(
            "canonical semantic schema version mismatch: "
            f"{canonical_version!r}"
        )
    definitions = canonical_schema.get("$defs")
    if not isinstance(definitions, Mapping) or "evidenceSpan" not in definitions:
        raise EvidenceTransportError("canonical evidenceSpan definition is absent")
    required = canonical_schema.get("required")
    properties = canonical_schema.get("properties")
    if not isinstance(required, list) or not isinstance(properties, Mapping):
        raise EvidenceTransportError("canonical root contract is malformed")
    if "canonical_schema_version" in required or "canonical_schema_version" in properties:
        raise EvidenceTransportError("canonical schema already has transport target version")

    result = copy.deepcopy(dict(canonical_schema))
    ledger: list[dict[str, Any]] = []

    old_id = result.get("$id")
    result["$id"] = (
        "https://mrs-m-thatcher.invalid/research/"
        "proposition-ledger-xai-transport-delta-v2.schema.json"
    )
    ledger.append(
        {
            "json_pointer": "/$id",
            "transformation_kind": "replace_schema_identifier_annotation",
            "canonical_value": old_id,
            "transport_value": result["$id"],
            "semantic_effect": "none_annotation_only",
        }
    )
    old_title = result.get("title")
    result["title"] = "Proposition ledger xAI transport delta v2.0"
    ledger.append(
        {
            "json_pointer": "/title",
            "transformation_kind": "replace_title_annotation",
            "canonical_value": old_title,
            "transport_value": result["title"],
            "semantic_effect": "none_annotation_only",
        }
    )
    old_description = result.get("description")
    result["description"] = (
        "Provider-facing semantic analysis for one exact transcript turn. Evidence "
        "uses literal exact-text occurrence selectors; deterministic local code binds "
        "the current turn and calculates canonical code-point offsets. Persistence "
        "identifiers, hashes, replay patches, and cumulative state remain absent."
    )
    ledger.append(
        {
            "json_pointer": "/description",
            "transformation_kind": "replace_transport_boundary_description",
            "canonical_value": old_description,
            "transport_value": result["description"],
            "semantic_effect": "none_annotation_only",
        }
    )

    result["properties"]["schema_version"]["const"] = TRANSPORT_SCHEMA_VERSION
    ledger.append(
        {
            "json_pointer": "/properties/schema_version/const",
            "transformation_kind": "replace_top_level_schema_version_const",
            "canonical_value": CANONICAL_SCHEMA_VERSION,
            "transport_value": TRANSPORT_SCHEMA_VERSION,
            "semantic_effect": "transport_identity",
        }
    )

    new_required: list[Any] = []
    for name in result["required"]:
        new_required.append(name)
        if name == "schema_version":
            new_required.append("canonical_schema_version")
    result["required"] = new_required
    ledger.append(
        {
            "json_pointer": "/required",
            "transformation_kind": "insert_required_canonical_schema_version",
            "canonical_value": "absent",
            "transport_value": "canonical_schema_version",
            "semantic_effect": "transport_target_identity",
        }
    )

    new_properties: dict[str, Any] = {}
    for name, child in result["properties"].items():
        new_properties[name] = child
        if name == "schema_version":
            new_properties["canonical_schema_version"] = {
                "const": CANONICAL_SCHEMA_VERSION
            }
    result["properties"] = new_properties
    ledger.append(
        {
            "json_pointer": "/properties/canonical_schema_version",
            "transformation_kind": "add_canonical_schema_version_const",
            "canonical_value": "absent",
            "transport_value": {"const": CANONICAL_SCHEMA_VERSION},
            "semantic_effect": "transport_target_identity",
        }
    )

    canonical_evidence = copy.deepcopy(result["$defs"]["evidenceSpan"])
    selector = _evidence_selector_schema()
    result["$defs"]["evidenceSpan"] = selector
    ledger.append(
        {
            "json_pointer": "/$defs/evidenceSpan",
            "transformation_kind": "replace_canonical_span_with_exact_text_selector",
            "canonical_value_sha256": value_sha256(canonical_evidence),
            "transport_value_sha256": value_sha256(selector),
            "removed_provider_fields": ["end_char", "start_char", "turn_id"],
            "added_provider_fields": ["occurrence_index"],
            "retained_provider_fields": ["exact_text"],
            "semantic_effect": "deterministically_resolved_transport_boundary",
        }
    )
    ledger.sort(key=lambda item: (item["json_pointer"], item["transformation_kind"]))
    return result, ledger


def build_transport_schema_equivalence_audit(
    canonical_schema: Mapping[str, Any],
    transport_schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove exact reversal after the bounded transport substitutions."""

    derived, ledger = derive_transport_schema(canonical_schema)
    exact_derivation = dict(transport_schema) == derived
    restored = copy.deepcopy(dict(transport_schema))
    for key in ("$id", "title", "description"):
        if key in canonical_schema:
            restored[key] = copy.deepcopy(canonical_schema[key])
        else:
            restored.pop(key, None)
    restored["properties"]["schema_version"] = copy.deepcopy(
        canonical_schema["properties"]["schema_version"]
    )
    restored["properties"].pop("canonical_schema_version", None)
    restored["required"] = [
        item for item in restored["required"] if item != "canonical_schema_version"
    ]
    restored["$defs"]["evidenceSpan"] = copy.deepcopy(
        canonical_schema["$defs"]["evidenceSpan"]
    )
    structurally_equivalent = restored == dict(canonical_schema)
    permitted_change_kinds = sorted(
        item["transformation_kind"] for item in ledger
    )
    return {
        "audit_version": "proposition-ledger-transport-schema-equivalence-v1",
        "status": (
            "passed"
            if exact_derivation and structurally_equivalent
            else "failed"
        ),
        "exact_deterministic_derivation": exact_derivation,
        "canonical_structure_restored_after_bounded_inverse": structurally_equivalent,
        "canonical_schema_version": CANONICAL_SCHEMA_VERSION,
        "transport_schema_version": TRANSPORT_SCHEMA_VERSION,
        "canonical_schema_value_sha256": value_sha256(canonical_schema),
        "transport_schema_value_sha256": value_sha256(transport_schema),
        "transport_schema_generated_file_sha256": generated_schema_file_sha256(
            transport_schema
        ),
        "permitted_transformation_count": len(ledger),
        "permitted_transformation_kinds": permitted_change_kinds,
        "all_non_boundary_validation_structure_unchanged": structurally_equivalent,
    }


def generate_transport_schema_artifacts(
    canonical_schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Return all deterministic tracked/private transport-schema ingredients."""

    schema, ledger = derive_transport_schema(canonical_schema)
    audit = build_transport_schema_equivalence_audit(canonical_schema, schema)
    return {
        "transport_schema": schema,
        "transport_schema_transformation_ledger": ledger,
        "transport_schema_equivalence_audit": audit,
        "transport_schema_generated_file_sha256": generated_schema_file_sha256(
            schema
        ),
        "transport_schema_value_sha256": value_sha256(schema),
    }


def _schema_contract_error(
    transport_schema: Mapping[str, Any], canonical_schema: Mapping[str, Any]
) -> str | None:
    canonical_version = (
        canonical_schema.get("properties", {})
        .get("schema_version", {})
        .get("const")
    )
    if canonical_version != CANONICAL_SCHEMA_VERSION:
        return "canonical_schema_version_constant_mismatch"
    try:
        expected, _ = derive_transport_schema(canonical_schema)
    except EvidenceTransportError as exc:
        return f"canonical_schema_derivation_failed:{exc}"
    if dict(transport_schema) != expected:
        return "transport_schema_not_exact_deterministic_derivation"
    return None


def find_overlapping_occurrences(text: str, exact_text: str) -> tuple[tuple[int, int], ...]:
    """Find all literal matches, including overlaps, in code-point order."""

    if not isinstance(text, str) or not isinstance(exact_text, str):
        raise TypeError("text and exact_text must be strings")
    if not exact_text:
        return ()
    result: list[tuple[int, int]] = []
    search_from = 0
    while search_from <= len(text) - len(exact_text):
        start = text.find(exact_text, search_from)
        if start < 0:
            break
        result.append((start, start + len(exact_text)))
        search_from = start + 1
    return tuple(result)


def _subschema_matches(
    root: Mapping[str, Any], subschema: Any, instance: Any
) -> bool:
    wrapper: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "allOf": [subschema],
    }
    if "$defs" in root:
        wrapper["$defs"] = root["$defs"]
    return not _intended_validation_errors(wrapper, instance)


def _selector_paths(
    instance: Any, transport_schema: Mapping[str, Any]
) -> tuple[tuple[str | int, ...], ...]:
    """Locate only instance positions derived from the evidenceSpan definition."""

    found: set[tuple[str | int, ...]] = set()
    visited: set[tuple[tuple[str | int, ...], str]] = set()

    def visit(
        value: Any,
        schema: Any,
        path: tuple[str | int, ...],
        schema_pointer: str,
    ) -> None:
        if not isinstance(schema, Mapping):
            return
        reference = schema.get("$ref")
        if isinstance(reference, str):
            if reference == "#/$defs/evidenceSpan":
                found.add(path)
                return
            marker = (path, reference)
            if marker in visited:
                return
            visited.add(marker)
            visit(value, _resolve_local_ref(transport_schema, reference), path, reference)
            return

        alternatives = schema.get("allOf")
        if isinstance(alternatives, list):
            for index, child in enumerate(alternatives):
                visit(
                    value,
                    child,
                    path,
                    _pointer_join(_pointer_join(schema_pointer, "allOf"), index),
                )
        for keyword in ("anyOf", "oneOf"):
            alternatives = schema.get(keyword)
            if isinstance(alternatives, list):
                for index, child in enumerate(alternatives):
                    if _subschema_matches(transport_schema, child, value):
                        visit(
                            value,
                            child,
                            path,
                            _pointer_join(
                                _pointer_join(schema_pointer, keyword), index
                            ),
                        )
        condition = schema.get("if")
        if isinstance(condition, Mapping):
            branch_name = "then" if _subschema_matches(
                transport_schema, condition, value
            ) else "else"
            branch = schema.get(branch_name)
            if isinstance(branch, Mapping):
                visit(
                    value,
                    branch,
                    path,
                    _pointer_join(schema_pointer, branch_name),
                )

        if isinstance(value, Mapping):
            properties = schema.get("properties")
            if isinstance(properties, Mapping):
                for name, child in properties.items():
                    if name in value:
                        visit(
                            value[name],
                            child,
                            (*path, name),
                            _pointer_join(
                                _pointer_join(schema_pointer, "properties"), name
                            ),
                        )
        elif isinstance(value, list):
            items = schema.get("items")
            if isinstance(items, (Mapping, bool)):
                for index, child_value in enumerate(value):
                    visit(
                        child_value,
                        items,
                        (*path, index),
                        _pointer_join(schema_pointer, "items"),
                    )

    visit(instance, transport_schema, (), "")
    return tuple(sorted(found, key=lambda path: _pointer(path)))


def _instance_objects(
    value: Any, path: tuple[str | int, ...] = ()
) -> Iterable[tuple[tuple[str | int, ...], Mapping[str, Any]]]:
    if isinstance(value, Mapping):
        yield path, value
        for name, child in value.items():
            yield from _instance_objects(child, (*path, name))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _instance_objects(child, (*path, index))


def _value_at_path(root: Any, path: Sequence[str | int]) -> Any:
    value = root
    for part in path:
        value = value[part]
    return value


def _set_at_path(root: Any, path: Sequence[str | int], value: Any) -> None:
    if not path:
        raise EvidenceTransportError("cannot replace the transport root as evidence")
    parent = _value_at_path(root, path[:-1])
    parent[path[-1]] = value


def _unauthorised_selector_errors(
    instance: Any,
    authorised_paths: set[tuple[str | int, ...]],
) -> tuple[str, ...]:
    errors: list[str] = []
    for path, value in _instance_objects(instance):
        keys = set(value)
        if keys & PROVIDER_EVIDENCE_FIELDS:
            errors.append(
                f"provider_evidence_coordinate_field_at_unauthorised_location:{_pointer(path)}"
            )
        if keys & SELECTOR_FIELDS and path not in authorised_paths:
            errors.append(f"evidence_selector_at_unauthorised_location:{_pointer(path)}")
    return tuple(errors)


def _identical_selector_array_paths(value: Any) -> tuple[str, ...]:
    """Find exact duplicate selectors before uniqueItems short-circuits typing.

    A duplicate is reported only for an ``exact_evidence_spans`` array whose
    members all have the exact closed selector shape.  The caller additionally
    requires every schema error to be a uniqueItems error, so malformed or
    unauthorised structures retain the general schema-invalid classification.
    """

    duplicates: list[str] = []

    def visit(candidate: Any, path: tuple[str | int, ...]) -> None:
        if isinstance(candidate, Mapping):
            for name, child in candidate.items():
                child_path = (*path, name)
                if name == "exact_evidence_spans" and isinstance(child, list):
                    encodings: set[bytes] = set()
                    closed = all(
                        isinstance(item, Mapping)
                        and set(item) == SELECTOR_FIELDS
                        for item in child
                    )
                    if closed:
                        for index, item in enumerate(child):
                            encoding = canonical_json_bytes(item)
                            if encoding in encodings:
                                duplicates.append(_pointer((*child_path, index)))
                            encodings.add(encoding)
                visit(child, child_path)
        elif isinstance(candidate, list):
            for index, child in enumerate(candidate):
                visit(child, (*path, index))

    visit(value, ())
    return tuple(duplicates)


def resolve_transport_delta(
    transport_delta: Any,
    *,
    current_turn_id: str,
    current_turn_text: str,
    transport_schema: Mapping[str, Any],
    canonical_schema: Mapping[str, Any],
) -> EvidenceResolutionResult:
    """Resolve a validated transport delta into one complete canonical delta.

    All failures are fail-closed and return no partial canonical object or
    partial resolution manifest.
    """

    if not isinstance(transport_schema, Mapping) or not isinstance(
        canonical_schema, Mapping
    ):
        return _failure("transport_schema_invalid", "schemas_must_be_objects")
    contract_error = _schema_contract_error(transport_schema, canonical_schema)
    if contract_error:
        return _failure("transport_schema_invalid", contract_error)
    if not isinstance(current_turn_id, str) or not current_turn_id:
        return _failure("transport_binding_invalid", "current_turn_id_must_be_nonempty")
    if not isinstance(current_turn_text, str):
        return _failure("transport_binding_invalid", "current_turn_text_must_be_string")

    transport_errors = _intended_validation_errors(transport_schema, transport_delta)
    if transport_errors:
        duplicate_paths = _identical_selector_array_paths(transport_delta)
        if duplicate_paths and all(
            "has non-unique elements" in error for error in transport_errors
        ):
            return _failure(
                "evidence_selector_duplicate",
                *(f"duplicate_transport_selector:{path}" for path in duplicate_paths),
            )
        return _failure("transport_schema_invalid", *transport_errors)
    if not isinstance(transport_delta, Mapping):
        return _failure("transport_schema_invalid", "transport_delta_must_be_object")
    if transport_delta.get("schema_version") != TRANSPORT_SCHEMA_VERSION:
        return _failure("transport_schema_invalid", "transport_schema_version_mismatch")
    if transport_delta.get("canonical_schema_version") != CANONICAL_SCHEMA_VERSION:
        return _failure(
            "transport_schema_invalid", "canonical_target_schema_version_mismatch"
        )
    if transport_delta.get("target_turn_id") != current_turn_id:
        return _failure("transport_binding_invalid", "target_turn_id_binding_mismatch")

    try:
        paths = _selector_paths(transport_delta, transport_schema)
    except EvidenceTransportError as exc:
        return _failure("evidence_resolution_invariant_failure", exc)
    authorised = set(paths)
    unauthorised = _unauthorised_selector_errors(transport_delta, authorised)
    if unauthorised:
        return _failure("evidence_selector_unauthorised", *unauthorised)

    replacements: list[tuple[tuple[str | int, ...], dict[str, Any]]] = []
    selector_records: list[dict[str, Any]] = []
    spans_by_parent: dict[tuple[str | int, ...], set[bytes]] = {}
    for path in paths:
        selector = _value_at_path(transport_delta, path)
        if not isinstance(selector, Mapping) or set(selector) != SELECTOR_FIELDS:
            return _failure(
                "evidence_resolution_invariant_failure",
                f"selector_shape_mismatch:{_pointer(path)}",
            )
        exact_text = selector.get("exact_text")
        occurrence_index = selector.get("occurrence_index")
        if not isinstance(exact_text, str) or not exact_text:
            return _failure(
                "transport_schema_invalid",
                f"selector_exact_text_must_be_nonempty:{_pointer(path)}",
            )
        if (
            isinstance(occurrence_index, bool)
            or not isinstance(occurrence_index, int)
            or occurrence_index < 0
        ):
            return _failure(
                "transport_schema_invalid",
                f"selector_occurrence_index_invalid:{_pointer(path)}",
            )
        occurrences = find_overlapping_occurrences(current_turn_text, exact_text)
        if not occurrences:
            return _failure(
                "evidence_exact_text_not_found",
                f"exact_text_not_found:{_pointer(path)}",
            )
        if occurrence_index >= len(occurrences):
            return _failure(
                "evidence_occurrence_index_out_of_range",
                f"occurrence_index_out_of_range:{_pointer(path)}:"
                f"requested={occurrence_index}:count={len(occurrences)}",
            )
        start, end = occurrences[occurrence_index]
        span = {
            "turn_id": current_turn_id,
            "start_char": start,
            "end_char": end,
            "exact_text": exact_text,
        }
        if current_turn_text[start:end] != exact_text:
            return _failure(
                "evidence_resolution_invariant_failure",
                f"resolved_slice_mismatch:{_pointer(path)}",
            )
        span_encoding = canonical_json_bytes(span)
        parent = path[:-1]
        parent_spans = spans_by_parent.setdefault(parent, set())
        if span_encoding in parent_spans:
            return _failure(
                "evidence_selector_duplicate",
                f"duplicate_resolved_span:{_pointer(path)}",
            )
        parent_spans.add(span_encoding)
        replacements.append((path, span))
        selector_records.append(
            {
                "selector_json_pointer": _pointer(path),
                "exact_text_sha256": sha256_bytes(exact_text.encode("utf-8")),
                "exact_text_length": len(exact_text),
                "occurrence_count": len(occurrences),
                "selected_occurrence_index": occurrence_index,
                "selected_start": start,
                "selected_end": end,
                "resolved_span_sha256": sha256_bytes(span_encoding),
            }
        )

    canonical_delta = copy.deepcopy(dict(transport_delta))
    canonical_delta.pop("canonical_schema_version", None)
    canonical_delta["schema_version"] = CANONICAL_SCHEMA_VERSION
    for path, span in replacements:
        _set_at_path(canonical_delta, path, copy.deepcopy(span))

    for path, value in _instance_objects(canonical_delta):
        keys = set(value)
        if "occurrence_index" in keys:
            return _failure(
                "evidence_resolution_invariant_failure",
                f"unresolved_occurrence_index:{_pointer(path)}",
            )
        if keys & SELECTOR_FIELDS and keys != CANONICAL_SPAN_FIELDS:
            return _failure(
                "evidence_resolution_invariant_failure",
                f"unresolved_or_malformed_evidence_object:{_pointer(path)}",
            )

    canonical_errors = _intended_validation_errors(canonical_schema, canonical_delta)
    if canonical_errors:
        return _failure("canonical_delta_validation_failed", *canonical_errors)

    manifest_core = {
        "resolver_version": EVIDENCE_RESOLVER_VERSION,
        "selector_count": len(selector_records),
        "selectors": selector_records,
    }
    manifest = copy.deepcopy(manifest_core)
    manifest["resolution_manifest_sha256"] = value_sha256(manifest_core)
    return EvidenceResolutionResult(
        status=SUCCESS_STATUS,
        canonical_delta=canonical_delta,
        resolution_manifest=manifest,
    )


def build_response_contract_manifest(
    *,
    transport_schema: Mapping[str, Any],
    xai_provider_schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Build bounded schema identity data, never a conversational schema copy.

    The transport digest hashes exact generated tracked-file bytes.  The xAI
    provider digest follows the established canonical-JSON value convention.
    The canonical digest is the frozen tracked-file byte digest.
    """

    transport_properties = transport_schema.get("properties")
    provider_properties = xai_provider_schema.get("properties")
    if not isinstance(transport_properties, Mapping) or not isinstance(
        provider_properties, Mapping
    ):
        raise EvidenceTransportError("response schemas must have root properties")
    expected_versions = {
        "schema_version": TRANSPORT_SCHEMA_VERSION,
        "canonical_schema_version": CANONICAL_SCHEMA_VERSION,
    }
    for label, properties in (
        ("transport", transport_properties),
        ("xai_provider", provider_properties),
    ):
        for field, expected in expected_versions.items():
            constraint = properties.get(field)
            if not isinstance(constraint, Mapping) or constraint.get("const") != expected:
                raise EvidenceTransportError(
                    f"{label} schema {field} constant mismatch"
                )
    return {
        "transport_schema_version": TRANSPORT_SCHEMA_VERSION,
        "transport_schema_sha256": generated_schema_file_sha256(transport_schema),
        "xai_provider_schema_sha256": value_sha256(xai_provider_schema),
        "canonical_semantic_schema_version": CANONICAL_SCHEMA_VERSION,
        "canonical_semantic_schema_sha256": CANONICAL_SCHEMA_FILE_SHA256,
        "evidence_selector_contract_version": EVIDENCE_SELECTOR_CONTRACT_VERSION,
    }


def build_phase2b_user_payload(
    *,
    protocol_version: str,
    protocol_hash: str,
    conversation_key: str,
    current_turn_id: str,
    turn_index: int,
    parent_turn_id: str | None,
    current_turn_text: str,
    speaker_descriptor: Mapping[str, Any],
    prior_ledger: Mapping[str, Any] | None,
    response_contract_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the corrected model-neutral conversational user payload."""

    if not isinstance(protocol_version, str) or not protocol_version:
        raise EvidenceTransportError("protocol_version must be non-empty")
    if not isinstance(protocol_hash, str) or not protocol_hash:
        raise EvidenceTransportError("protocol_hash must be non-empty")
    if not isinstance(conversation_key, str) or not conversation_key:
        raise EvidenceTransportError("conversation_key must be non-empty")
    if not isinstance(current_turn_id, str) or not current_turn_id:
        raise EvidenceTransportError("current_turn_id must be non-empty")
    if isinstance(turn_index, bool) or not isinstance(turn_index, int) or turn_index < 0:
        raise EvidenceTransportError("turn_index must be a non-negative integer")
    if parent_turn_id is not None and (
        not isinstance(parent_turn_id, str) or not parent_turn_id
    ):
        raise EvidenceTransportError("parent_turn_id must be null or non-empty")
    if not isinstance(current_turn_text, str):
        raise EvidenceTransportError("current_turn_text must be a string")
    if not isinstance(speaker_descriptor, Mapping):
        raise EvidenceTransportError("speaker_descriptor must be an object")
    if prior_ledger is not None and not isinstance(prior_ledger, Mapping):
        raise EvidenceTransportError("prior_ledger must be null or an object")
    if not isinstance(response_contract_manifest, Mapping):
        raise EvidenceTransportError("response_contract_manifest must be an object")
    expected_manifest_fields = {
        "transport_schema_version",
        "transport_schema_sha256",
        "xai_provider_schema_sha256",
        "canonical_semantic_schema_version",
        "canonical_semantic_schema_sha256",
        "evidence_selector_contract_version",
    }
    if set(response_contract_manifest) != expected_manifest_fields:
        raise EvidenceTransportError("response_contract_manifest field set mismatch")
    expected_manifest_constants = {
        "transport_schema_version": TRANSPORT_SCHEMA_VERSION,
        "canonical_semantic_schema_version": CANONICAL_SCHEMA_VERSION,
        "canonical_semantic_schema_sha256": CANONICAL_SCHEMA_FILE_SHA256,
        "evidence_selector_contract_version": EVIDENCE_SELECTOR_CONTRACT_VERSION,
    }
    for field, expected in expected_manifest_constants.items():
        if response_contract_manifest.get(field) != expected:
            raise EvidenceTransportError(
                f"response_contract_manifest {field} mismatch"
            )
    for field in ("transport_schema_sha256", "xai_provider_schema_sha256"):
        digest = response_contract_manifest.get(field)
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise EvidenceTransportError(
                f"response_contract_manifest {field} is not a SHA-256 hex digest"
            )

    return {
        "protocol_version": protocol_version,
        "protocol_hash": protocol_hash,
        "pilot_local_conversation_key": conversation_key,
        "pilot_local_current_turn_id": current_turn_id,
        "turn_index": turn_index,
        "pilot_local_parent_turn_id": parent_turn_id,
        "exact_current_visible_text": current_turn_text,
        "trusted_current_speaker_participant_descriptor": copy.deepcopy(
            dict(speaker_descriptor)
        ),
        "validated_prior_persisted_ledger": (
            None if prior_ledger is None else copy.deepcopy(dict(prior_ledger))
        ),
        "response_contract_manifest": copy.deepcopy(
            dict(response_contract_manifest)
        ),
    }


def build_request_representation(
    *,
    model: str,
    user_payload: Mapping[str, Any],
    system_prompt: str,
    xai_provider_schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the exact credential-free Phase 2B local request contract."""

    if model not in {"grok-4.3", "grok-4.6"}:
        raise EvidenceTransportError("model must be one of the two retained profiles")
    if not isinstance(user_payload, Mapping):
        raise EvidenceTransportError("user_payload must be an object")
    if not isinstance(system_prompt, str) or not system_prompt:
        raise EvidenceTransportError("system_prompt must be non-empty")
    if not isinstance(xai_provider_schema, Mapping):
        raise EvidenceTransportError("xai_provider_schema must be an object")
    user_content = canonical_json_bytes(user_payload).decode("utf-8")
    provider_schema_text = canonical_json_bytes(xai_provider_schema).decode("utf-8")
    if provider_schema_text in user_content:
        raise EvidenceTransportError(
            "complete provider schema must not appear in the conversational payload"
        )
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": 4096,
        "reasoning_effort": "low",
        "tools": [],
        "parallel_tool_calls": False,
        "response_format": {
            "format_type": "json_schema",
            "schema": copy.deepcopy(dict(xai_provider_schema)),
        },
        "search_parameters": None,
        "store_messages": False,
        "streaming": False,
        "code_execution": False,
        "sampling_parameters_set": [],
        "fallback_model": None,
        "application_retry_count": 0,
        "sdk_grpc_retries": False,
        "request_contract_revision": REQUEST_CONTRACT_REVISION,
        "tool_choice_parameter_sent": False,
    }


def build_retained_profile_request_representations(
    *,
    user_payload: Mapping[str, Any],
    system_prompt: str,
    xai_provider_schema: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build both retained representations, differing only by model identity."""

    results = tuple(
        build_request_representation(
            model=model,
            user_payload=user_payload,
            system_prompt=system_prompt,
            xai_provider_schema=xai_provider_schema,
        )
        for model in ("grok-4.3", "grok-4.6")
    )
    first = copy.deepcopy(results[0])
    second = copy.deepcopy(results[1])
    first.pop("model")
    second.pop("model")
    if first != second:
        raise EvidenceTransportError("retained requests differ beyond model identity")
    return results


def _read_json_object(path: Path) -> dict[str, Any]:
    source = path.read_bytes()
    try:
        value = json.loads(source.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceTransportError(f"invalid JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise EvidenceTransportError(f"JSON root is not an object: {path}")
    return value


def _atomic_write(path: Path, source: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(source)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate or byte-check the Phase 2B transport schema offline."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="write the generated schema")
    mode.add_argument("--check", action="store_true", help="require exact tracked bytes")
    mode.add_argument(
        "--verify-only",
        action="store_true",
        help="alias for --check; performs no writes",
    )
    parser.add_argument(
        "--canonical-schema",
        type=Path,
        default=DEFAULT_CANONICAL_SCHEMA_PATH,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_TRANSPORT_SCHEMA_PATH,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for deterministic generation and verify-only checks."""

    args = _build_parser().parse_args(argv)
    canonical_source = args.canonical_schema.read_bytes()
    if args.canonical_schema == DEFAULT_CANONICAL_SCHEMA_PATH:
        digest = sha256_bytes(canonical_source)
        if digest != CANONICAL_SCHEMA_FILE_SHA256:
            raise EvidenceTransportError(
                "canonical tracked schema file SHA-256 mismatch: "
                f"expected={CANONICAL_SCHEMA_FILE_SHA256} actual={digest}"
            )
    canonical = _read_json_object(args.canonical_schema)
    transport, _ledger = derive_transport_schema(canonical)
    expected = generated_schema_bytes(transport)
    if args.write:
        _atomic_write(args.output, expected)
        return 0
    if not args.output.is_file():
        raise EvidenceTransportError(f"transport schema is absent: {args.output}")
    actual = args.output.read_bytes()
    if actual != expected:
        raise EvidenceTransportError(
            "transport schema differs from deterministic generation: "
            f"expected_sha256={sha256_bytes(expected)} "
            f"actual_sha256={sha256_bytes(actual)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
