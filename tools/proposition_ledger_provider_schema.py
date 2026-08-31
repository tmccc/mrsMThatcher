#!/usr/bin/env python3
"""Provider-neutral JSON Schema feature inventory and compatibility gate.

This module deliberately uses only the Python standard library.  It inventories
the canonical schema without selecting a provider, importing a provider SDK, or
opening a network client.  Provider compatibility remains pending until a later
phase supplies an explicit, local, provider/model-specific validation record.

The depth metrics describe the effective schema graph rather than the JSON
document's formatting.  Object depth counts object schemas on a root-to-node
path after following local references.  Array-item nesting similarly counts
array schemas on such a path.  Feature counts are syntactic counts over the
canonical document, so each keyword occurrence is counted once regardless of
how many times a definition is referenced.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any


PENDING_MODEL_PROFILE_SELECTION = "pending_model_profile_selection"
PENDING_PROVIDER_SPECIFIC_VALIDATION = "pending_provider_specific_validation"
PROVIDER_SCHEMA_COMPATIBILITY_PASSED = "passed"
PROVIDER_SCHEMA_COMPATIBILITY_FAILED = "provider_specific_validation_failed"
NEXT_REQUIRED_ACTION = (
    "freeze provider/model profile and run its local schema compilation or "
    "dry-validation before any provider call"
)

FEATURE_FIELDS = (
    "schema_version",
    "schema_sha256",
    "schema_size_bytes",
    "maximum_object_depth",
    "property_count",
    "required_property_count",
    "ref_count",
    "one_of_count",
    "any_of_count",
    "all_of_count",
    "enum_count",
    "const_count",
    "nullable_union_count",
    "additional_properties_false_count",
    "recursive_reference_count",
    "maximum_array_item_nesting",
    "unbounded_array_count",
)

_SCHEMA_CHILD_SINGLETON_KEYWORDS = (
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
)
_SCHEMA_CHILD_MAPPING_KEYWORDS = (
    "$defs",
    "definitions",
    "dependentSchemas",
    "patternProperties",
    "properties",
)
_SCHEMA_CHILD_SEQUENCE_KEYWORDS = (
    "allOf",
    "anyOf",
    "oneOf",
    "prefixItems",
)


class ProviderSchemaInventoryError(ValueError):
    """The schema or a provider-specific validation record is malformed."""


def _reject_duplicate_object_members(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProviderSchemaInventoryError(
                f"duplicate JSON object member: {key!r}"
            )
        result[key] = value
    return result


def _reject_non_finite_number(value: str) -> None:
    raise ProviderSchemaInventoryError(f"non-finite JSON number: {value}")


def _load_schema(schema_path: str | Path) -> tuple[dict[str, Any], bytes]:
    path = Path(schema_path)
    raw = path.read_bytes()
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProviderSchemaInventoryError("schema is not valid UTF-8") from exc
    try:
        value = json.loads(
            decoded,
            object_pairs_hook=_reject_duplicate_object_members,
            parse_constant=_reject_non_finite_number,
        )
    except json.JSONDecodeError as exc:
        raise ProviderSchemaInventoryError(f"schema is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ProviderSchemaInventoryError("schema document root must be an object")
    return value, raw


def canonical_json_bytes(value: Any) -> bytes:
    """Return the deterministic UTF-8 JSON representation used for digests."""

    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ProviderSchemaInventoryError(
            f"value cannot be represented as canonical JSON: {exc}"
        ) from exc
    return encoded.encode("utf-8")


def _schema_version(schema: Mapping[str, Any]) -> str:
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        raise ProviderSchemaInventoryError(
            "schema properties must expose schema_version"
        )
    version_schema = properties.get("schema_version")
    if not isinstance(version_schema, Mapping):
        raise ProviderSchemaInventoryError(
            "schema properties must expose schema_version"
        )
    version = version_schema.get("const")
    if not isinstance(version, str) or not version:
        raise ProviderSchemaInventoryError(
            "schema_version must have a non-empty string const"
        )
    return version


def _walk_json_objects(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_json_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json_objects(child)


def _type_includes(schema: Mapping[str, Any], expected: str) -> bool:
    declared_type = schema.get("type")
    if declared_type == expected:
        return True
    return (
        isinstance(declared_type, list)
        and expected in declared_type
        and all(isinstance(item, str) for item in declared_type)
    )


def _is_null_only_schema(schema: Any) -> bool:
    if not isinstance(schema, Mapping):
        return False
    declared_type = schema.get("type")
    if declared_type == "null":
        return True
    if isinstance(declared_type, list):
        return declared_type == ["null"]
    return schema.get("const", object()) is None


def _is_nullable_union(schema: Mapping[str, Any]) -> bool:
    declared_type = schema.get("type")
    if isinstance(declared_type, list):
        unique_types = set(declared_type)
        if "null" in unique_types and len(unique_types) > 1:
            return True
    for keyword in ("oneOf", "anyOf"):
        alternatives = schema.get(keyword)
        if not isinstance(alternatives, list) or len(alternatives) < 2:
            continue
        has_null = any(_is_null_only_schema(item) for item in alternatives)
        has_non_null = any(not _is_null_only_schema(item) for item in alternatives)
        if has_null and has_non_null:
            return True
    return False


def _escape_json_pointer_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _document_nodes(
    value: Any,
    pointer: str = "",
) -> tuple[dict[str, Mapping[str, Any]], dict[str, set[str]]]:
    nodes: dict[str, Mapping[str, Any]] = {}
    structural_edges: dict[str, set[str]] = {}

    def visit(current: Any, current_pointer: str) -> None:
        if isinstance(current, Mapping):
            nodes[current_pointer] = current
            edges = structural_edges.setdefault(current_pointer, set())
            for key, child in current.items():
                child_pointer = (
                    f"{current_pointer}/{_escape_json_pointer_token(key)}"
                )
                if isinstance(child, Mapping):
                    edges.add(child_pointer)
                    visit(child, child_pointer)
                elif isinstance(child, list):
                    for index, item in enumerate(child):
                        item_pointer = f"{child_pointer}/{index}"
                        if isinstance(item, Mapping):
                            edges.add(item_pointer)
                            visit(item, item_pointer)
                        elif isinstance(item, list):
                            visit(item, item_pointer)
        elif isinstance(current, list):
            for index, item in enumerate(current):
                item_pointer = f"{current_pointer}/{index}"
                if isinstance(item, (Mapping, list)):
                    visit(item, item_pointer)

    visit(value, pointer)
    return nodes, structural_edges


def _resolve_local_reference(
    reference: Any,
    nodes: Mapping[str, Mapping[str, Any]],
) -> str | None:
    if reference == "#":
        return ""
    if not isinstance(reference, str) or not reference.startswith("#/"):
        return None
    pointer = reference[1:]
    return pointer if pointer in nodes else None


def _recursive_reference_count(schema: Mapping[str, Any]) -> int:
    nodes, structural_edges = _document_nodes(schema)
    edges = {pointer: set(targets) for pointer, targets in structural_edges.items()}
    references: list[tuple[str, str]] = []
    for pointer, node in nodes.items():
        target = _resolve_local_reference(node.get("$ref"), nodes)
        if target is None:
            continue
        edges.setdefault(pointer, set()).add(target)
        references.append((pointer, target))

    def target_reaches_source(target: str, source: str) -> bool:
        pending = [target]
        visited: set[str] = set()
        while pending:
            pointer = pending.pop()
            if pointer == source:
                return True
            if pointer in visited:
                continue
            visited.add(pointer)
            pending.extend(edges.get(pointer, ()))
        return False

    return sum(
        1
        for source, target in references
        if target_reaches_source(target, source)
    )


def _iter_effective_schema_children(
    schema: Mapping[str, Any],
) -> Iterable[Mapping[str, Any]]:
    for keyword in _SCHEMA_CHILD_SINGLETON_KEYWORDS:
        child = schema.get(keyword)
        if isinstance(child, Mapping):
            yield child
    for keyword in _SCHEMA_CHILD_MAPPING_KEYWORDS:
        children = schema.get(keyword)
        if isinstance(children, Mapping):
            for child in children.values():
                if isinstance(child, Mapping):
                    yield child
    for keyword in _SCHEMA_CHILD_SEQUENCE_KEYWORDS:
        children = schema.get(keyword)
        if isinstance(children, list):
            for child in children:
                if isinstance(child, Mapping):
                    yield child


def _effective_depths(schema: Mapping[str, Any]) -> tuple[int, int]:
    nodes, _ = _document_nodes(schema)

    def visit(
        current: Mapping[str, Any],
        object_depth: int,
        array_depth: int,
        active_nodes: frozenset[int],
    ) -> tuple[int, int]:
        identity = id(current)
        if identity in active_nodes:
            return object_depth, array_depth
        active_nodes = active_nodes | {identity}

        current_object_depth = object_depth + int(
            _type_includes(current, "object") or "properties" in current
        )
        current_array_depth = array_depth + int(
            _type_includes(current, "array") or "items" in current
        )
        maximum_object_depth = current_object_depth
        maximum_array_depth = current_array_depth

        reference_target = _resolve_local_reference(current.get("$ref"), nodes)
        if reference_target is not None:
            target = nodes[reference_target]
            child_depths = visit(
                target,
                current_object_depth,
                current_array_depth,
                active_nodes,
            )
            maximum_object_depth = max(maximum_object_depth, child_depths[0])
            maximum_array_depth = max(maximum_array_depth, child_depths[1])

        for child in _iter_effective_schema_children(current):
            child_depths = visit(
                child,
                current_object_depth,
                current_array_depth,
                active_nodes,
            )
            maximum_object_depth = max(maximum_object_depth, child_depths[0])
            maximum_array_depth = max(maximum_array_depth, child_depths[1])
        return maximum_object_depth, maximum_array_depth

    return visit(schema, 0, 0, frozenset())


def build_schema_feature_inventory(schema_path: str | Path) -> dict[str, Any]:
    """Return a deterministic provider-neutral inventory of one JSON Schema.

    ``schema_sha256`` and ``schema_size_bytes`` describe the exact source file
    bytes.  All other fields are derived solely from the parsed schema.
    """

    schema, raw = _load_schema(schema_path)
    objects = tuple(_walk_json_objects(schema))
    maximum_object_depth, maximum_array_depth = _effective_depths(schema)

    inventory: dict[str, Any] = {
        "schema_version": _schema_version(schema),
        "schema_sha256": hashlib.sha256(raw).hexdigest(),
        "schema_size_bytes": len(raw),
        "maximum_object_depth": maximum_object_depth,
        "property_count": sum(
            len(value)
            for node in objects
            if isinstance((value := node.get("properties")), Mapping)
        ),
        "required_property_count": sum(
            len(value)
            for node in objects
            if isinstance((value := node.get("required")), list)
        ),
        "ref_count": sum("$ref" in node for node in objects),
        "one_of_count": sum("oneOf" in node for node in objects),
        "any_of_count": sum("anyOf" in node for node in objects),
        "all_of_count": sum("allOf" in node for node in objects),
        "enum_count": sum("enum" in node for node in objects),
        "const_count": sum("const" in node for node in objects),
        "nullable_union_count": sum(_is_nullable_union(node) for node in objects),
        "additional_properties_false_count": sum(
            node.get("additionalProperties") is False for node in objects
        ),
        "recursive_reference_count": _recursive_reference_count(schema),
        "maximum_array_item_nesting": maximum_array_depth,
        "unbounded_array_count": sum(
            (_type_includes(node, "array") or "items" in node)
            and "maxItems" not in node
            for node in objects
        ),
    }
    if tuple(inventory) != FEATURE_FIELDS:
        raise AssertionError("provider schema inventory field order drifted")
    return inventory


def schema_feature_inventory_sha256(inventory: Mapping[str, Any]) -> str:
    """Hash a complete feature inventory using deterministic canonical JSON."""

    missing = [field for field in FEATURE_FIELDS if field not in inventory]
    extras = sorted(set(inventory) - set(FEATURE_FIELDS))
    if missing or extras:
        raise ProviderSchemaInventoryError(
            f"invalid feature inventory fields; missing={missing!r}, extras={extras!r}"
        )
    return hashlib.sha256(canonical_json_bytes(dict(inventory))).hexdigest()


def _profile_value(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderSchemaInventoryError(f"{field} must be a non-empty string")
    return value


def _validation_record_errors(
    validation_record: Mapping[str, Any],
    *,
    selected_provider: str,
    selected_model: str,
    schema_sha256: str,
) -> list[str]:
    errors: list[str] = []
    if validation_record.get("selected_provider") != selected_provider:
        errors.append("validation record provider does not match selected_provider")
    if validation_record.get("selected_model") != selected_model:
        errors.append("validation record model does not match selected_model")
    sdk_version = validation_record.get("provider_sdk_version")
    if not isinstance(sdk_version, str) or not sdk_version.strip():
        errors.append("validation record lacks a frozen provider_sdk_version")
    if validation_record.get("canonical_schema_sha256") != schema_sha256:
        errors.append("validation record canonical schema hash does not match")
    validator_run = validation_record.get("provider_specific_validator_run") is True
    compilation_run = validation_record.get("provider_sdk_compilation_run") is True
    if not (validator_run or compilation_run):
        errors.append("no provider-specific local validation or compilation ran")
    if validation_record.get("network_call_made") is not False:
        errors.append("provider-specific validation must record zero network calls")
    if "schema_transformation" not in validation_record:
        errors.append("validation record does not state the schema transformation")
    if validation_record.get("transformed_and_canonical_semantics_agree") is not True:
        errors.append("transformed and canonical schema semantics were not verified")
    if validation_record.get("validation_status") != "passed":
        errors.append("provider-specific validation status is not passed")
    return errors


def build_provider_schema_compatibility(
    feature_inventory: Mapping[str, Any],
    *,
    selected_provider: str | None = None,
    selected_model: str | None = None,
    provider_specific_validation_record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive an honest compatibility record without performing validation.

    With no provider/model profile this always returns
    ``pending_model_profile_selection``.  A passed result is possible only when
    the caller explicitly supplies matching evidence of a provider-specific
    local validation; this function never runs that validation itself.
    """

    inventory_sha256 = schema_feature_inventory_sha256(feature_inventory)
    if (selected_provider is None) != (selected_model is None):
        raise ProviderSchemaInventoryError(
            "selected_provider and selected_model must both be null or both be set"
        )
    if selected_provider is None:
        if provider_specific_validation_record is not None:
            raise ProviderSchemaInventoryError(
                "provider-specific validation cannot precede profile selection"
            )
        record = {
            "status": PENDING_MODEL_PROFILE_SELECTION,
            "selected_provider": None,
            "selected_model": None,
            "provider_specific_validator_run": False,
            "provider_sdk_compilation_run": False,
            "network_call_made": False,
            "feature_inventory_sha256": inventory_sha256,
            "provider_specific_validation_record_sha256": None,
            "next_required_action": NEXT_REQUIRED_ACTION,
        }
        return record

    provider = _profile_value(selected_provider, "selected_provider")
    model = _profile_value(selected_model, "selected_model")
    if provider_specific_validation_record is None:
        return {
            "status": PENDING_PROVIDER_SPECIFIC_VALIDATION,
            "selected_provider": provider,
            "selected_model": model,
            "provider_specific_validator_run": False,
            "provider_sdk_compilation_run": False,
            "network_call_made": False,
            "feature_inventory_sha256": inventory_sha256,
            "provider_specific_validation_record_sha256": None,
            "next_required_action": NEXT_REQUIRED_ACTION,
        }

    validation_record = dict(provider_specific_validation_record)
    errors = _validation_record_errors(
        validation_record,
        selected_provider=provider,
        selected_model=model,
        schema_sha256=str(feature_inventory["schema_sha256"]),
    )
    validation_digest = hashlib.sha256(
        canonical_json_bytes(validation_record)
    ).hexdigest()
    passed = not errors
    return {
        "status": (
            PROVIDER_SCHEMA_COMPATIBILITY_PASSED
            if passed
            else PROVIDER_SCHEMA_COMPATIBILITY_FAILED
        ),
        "selected_provider": provider,
        "selected_model": model,
        "provider_specific_validator_run": validation_record.get(
            "provider_specific_validator_run"
        )
        is True,
        "provider_sdk_compilation_run": validation_record.get(
            "provider_sdk_compilation_run"
        )
        is True,
        "network_call_made": validation_record.get("network_call_made") is True,
        "feature_inventory_sha256": inventory_sha256,
        "provider_specific_validation_record_sha256": validation_digest,
        "next_required_action": None if passed else NEXT_REQUIRED_ACTION,
    }


def validate_provider_schema_compatibility(
    compatibility: Mapping[str, Any],
    *,
    feature_inventory: Mapping[str, Any],
    provider_specific_validation_record: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """Return bounded invariant errors for a recorded compatibility result.

    Passing compatibility is rejected unless the explicit validation record is
    supplied again and reproduces the recorded digest and local-validation
    evidence.  This prevents a readiness consumer from trusting a bare
    ``{"status": "passed"}`` assertion.
    """

    errors: list[str] = []
    expected_inventory_digest = schema_feature_inventory_sha256(feature_inventory)
    if compatibility.get("feature_inventory_sha256") != expected_inventory_digest:
        errors.append("feature inventory hash does not match")
    if compatibility.get("network_call_made") is not False:
        errors.append("compatibility gate must record zero network calls")

    status = compatibility.get("status")
    provider = compatibility.get("selected_provider")
    model = compatibility.get("selected_model")
    if status == PENDING_MODEL_PROFILE_SELECTION:
        if provider is not None or model is not None:
            errors.append("pending_model_profile_selection requires null profile")
        if compatibility.get("provider_specific_validator_run") is not False:
            errors.append("pending profile cannot claim provider validation")
        if compatibility.get("provider_sdk_compilation_run") is not False:
            errors.append("pending profile cannot claim SDK compilation")
        if provider_specific_validation_record is not None:
            errors.append("pending profile cannot have a validation record")
        if compatibility.get("provider_specific_validation_record_sha256") is not None:
            errors.append("pending profile cannot have a validation record hash")
        if compatibility.get("next_required_action") != NEXT_REQUIRED_ACTION:
            errors.append("pending profile next action is incorrect")
        return tuple(errors)

    if not isinstance(provider, str) or not provider.strip():
        errors.append("selected_provider must be frozen for this status")
    if not isinstance(model, str) or not model.strip():
        errors.append("selected_model must be frozen for this status")

    if status == PROVIDER_SCHEMA_COMPATIBILITY_PASSED:
        if provider_specific_validation_record is None:
            errors.append("passed compatibility lacks explicit validation record")
            return tuple(errors)
        if not isinstance(provider, str) or not isinstance(model, str):
            return tuple(errors)
        validation_errors = _validation_record_errors(
            provider_specific_validation_record,
            selected_provider=provider,
            selected_model=model,
            schema_sha256=str(feature_inventory["schema_sha256"]),
        )
        errors.extend(validation_errors)
        expected_validation_digest = hashlib.sha256(
            canonical_json_bytes(dict(provider_specific_validation_record))
        ).hexdigest()
        if (
            compatibility.get("provider_specific_validation_record_sha256")
            != expected_validation_digest
        ):
            errors.append("provider-specific validation record hash does not match")
        if compatibility.get("provider_specific_validator_run") is not (
            provider_specific_validation_record.get(
                "provider_specific_validator_run"
            )
            is True
        ):
            errors.append("provider validator flag does not reproduce evidence")
        if compatibility.get("provider_sdk_compilation_run") is not (
            provider_specific_validation_record.get("provider_sdk_compilation_run")
            is True
        ):
            errors.append("provider SDK compilation flag does not reproduce evidence")
    elif status not in {
        PENDING_PROVIDER_SPECIFIC_VALIDATION,
        PROVIDER_SCHEMA_COMPATIBILITY_FAILED,
    }:
        errors.append(f"unknown provider schema compatibility status: {status!r}")
    return tuple(errors)


def provider_schema_compatibility_passed(
    compatibility: Mapping[str, Any],
    *,
    feature_inventory: Mapping[str, Any],
    provider_specific_validation_record: Mapping[str, Any] | None = None,
) -> bool:
    """Return true only for a fully evidenced provider-specific local pass."""

    return (
        compatibility.get("status") == PROVIDER_SCHEMA_COMPATIBILITY_PASSED
        and not validate_provider_schema_compatibility(
            compatibility,
            feature_inventory=feature_inventory,
            provider_specific_validation_record=provider_specific_validation_record,
        )
    )
