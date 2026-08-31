from __future__ import annotations

import ast
import hashlib
import json
import socket
from pathlib import Path
from typing import Any

import pytest

from tools import proposition_ledger_provider_schema as provider_schema


PROJECT_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_DIR / "tools/proposition_ledger_provider_schema.py"
SEMANTIC_SCHEMA_PATH = (
    PROJECT_DIR
    / "proposition_ledger_research/schema/proposition-ledger-semantic-delta-v1.schema.json"
)


def _inventory() -> dict[str, Any]:
    return provider_schema.build_schema_feature_inventory(SEMANTIC_SCHEMA_PATH)


def test_feature_inventory_and_digest_are_deterministic() -> None:
    first = _inventory()
    second = _inventory()

    assert first == second
    assert provider_schema.canonical_json_bytes(first) == (
        provider_schema.canonical_json_bytes(second)
    )
    assert provider_schema.schema_feature_inventory_sha256(first) == (
        provider_schema.schema_feature_inventory_sha256(second)
    )
    assert tuple(first) == provider_schema.FEATURE_FIELDS
    assert all(
        isinstance(first[field], int) and first[field] >= 0
        for field in provider_schema.FEATURE_FIELDS[3:]
    )


def test_inventory_uses_exact_source_schema_hash_and_size() -> None:
    source = SEMANTIC_SCHEMA_PATH.read_bytes()
    schema = json.loads(source)
    inventory = _inventory()

    assert inventory["schema_sha256"] == hashlib.sha256(source).hexdigest()
    assert inventory["schema_size_bytes"] == len(source)
    assert inventory["schema_version"] == (
        schema["properties"]["schema_version"]["const"]
    )


def test_module_has_only_provider_neutral_non_network_imports() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])

    assert imported_roots <= {
        "__future__",
        "collections",
        "hashlib",
        "json",
        "pathlib",
        "typing",
    }
    assert imported_roots.isdisjoint(
        {
            "aiohttp",
            "anthropic",
            "boto3",
            "google",
            "http",
            "httpx",
            "openai",
            "requests",
            "socket",
            "urllib",
            "xai",
        }
    )


def test_unselected_profile_produces_exact_pending_gate() -> None:
    inventory = _inventory()
    compatibility = provider_schema.build_provider_schema_compatibility(inventory)

    assert compatibility == {
        "status": "pending_model_profile_selection",
        "selected_provider": None,
        "selected_model": None,
        "provider_specific_validator_run": False,
        "provider_sdk_compilation_run": False,
        "network_call_made": False,
        "feature_inventory_sha256": (
            provider_schema.schema_feature_inventory_sha256(inventory)
        ),
        "provider_specific_validation_record_sha256": None,
        "next_required_action": (
            "freeze provider/model profile and run its local schema compilation or "
            "dry-validation before any provider call"
        ),
    }
    assert not provider_schema.validate_provider_schema_compatibility(
        compatibility,
        feature_inventory=inventory,
    )
    assert not provider_schema.provider_schema_compatibility_passed(
        compatibility,
        feature_inventory=inventory,
    )


def test_selected_profile_cannot_use_model_selection_pending_status() -> None:
    inventory = _inventory()
    compatibility = provider_schema.build_provider_schema_compatibility(
        inventory,
        selected_provider="local-test-provider",
        selected_model="local-test-model",
    )

    assert compatibility["status"] == "pending_provider_specific_validation"
    assert compatibility["status"] != "pending_model_profile_selection"

    forged = dict(compatibility)
    forged["status"] = "pending_model_profile_selection"
    errors = provider_schema.validate_provider_schema_compatibility(
        forged,
        feature_inventory=inventory,
    )
    assert "pending_model_profile_selection requires null profile" in errors


def test_readiness_cannot_pass_without_explicit_local_validation_record() -> None:
    inventory = _inventory()
    forged = {
        "status": "passed",
        "selected_provider": "local-test-provider",
        "selected_model": "local-test-model",
        "provider_specific_validator_run": True,
        "provider_sdk_compilation_run": False,
        "network_call_made": False,
        "feature_inventory_sha256": (
            provider_schema.schema_feature_inventory_sha256(inventory)
        ),
        "provider_specific_validation_record_sha256": "0" * 64,
        "next_required_action": None,
    }

    errors = provider_schema.validate_provider_schema_compatibility(
        forged,
        feature_inventory=inventory,
    )
    assert "passed compatibility lacks explicit validation record" in errors
    assert not provider_schema.provider_schema_compatibility_passed(
        forged,
        feature_inventory=inventory,
    )


def test_no_network_request_occurs(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_socket(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", forbidden_socket)
    inventory = _inventory()
    compatibility = provider_schema.build_provider_schema_compatibility(inventory)

    assert compatibility["network_call_made"] is False


def test_feature_metrics_have_stable_meanings_on_small_schema(tmp_path: Path) -> None:
    schema_path = tmp_path / "schema.json"
    schema = {
        "type": "object",
        "required": ["schema_version", "payload"],
        "properties": {
            "schema_version": {"const": "example-v1"},
            "payload": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "maybe": {"type": ["string", "null"]},
                        "nested": {
                            "type": "array",
                            "maxItems": 4,
                            "items": {"$ref": "#/$defs/node"},
                        },
                    },
                    "required": ["maybe"],
                    "additionalProperties": False,
                },
            },
        },
        "$defs": {
            "node": {
                "type": "object",
                "properties": {"child": {"$ref": "#/$defs/node"}},
            }
        },
        "additionalProperties": False,
    }
    schema_path.write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    inventory = provider_schema.build_schema_feature_inventory(schema_path)

    assert inventory["property_count"] == 5
    assert inventory["required_property_count"] == 3
    assert inventory["ref_count"] == 2
    assert inventory["nullable_union_count"] == 1
    assert inventory["additional_properties_false_count"] == 2
    assert inventory["recursive_reference_count"] == 1
    assert inventory["maximum_object_depth"] == 3
    assert inventory["maximum_array_item_nesting"] == 2
    assert inventory["unbounded_array_count"] == 1
