from __future__ import annotations

import ast
import copy
import importlib.metadata
import json
import os
import socket
import sys
import types
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from tools import proposition_ledger_provider_schema as provider_neutral
from tools import proposition_ledger_xai_provider_preflight as xai_preflight


PROJECT_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_DIR / "tools/proposition_ledger_xai_provider_preflight.py"
CANONICAL_SCHEMA_PATH = (
    PROJECT_DIR
    / "proposition_ledger_research/schema/"
    "proposition-ledger-semantic-delta-v1.schema.json"
)
PROFILES_PATH = (
    PROJECT_DIR / "proposition_ledger_research/phase1_3/xai-provider-profiles.json"
)
RULES_PATH = (
    PROJECT_DIR
    / "proposition_ledger_research/phase1_3/xai-structured-output-rules.json"
)
LOCK_PATH = (
    PROJECT_DIR
    / "proposition_ledger_research/phase1_3/xai-sdk-environment-lock.json"
)

EXPECTED_TRANSFORM_POINTERS = {
    "/$defs/newAnswerTarget/anyOf/0",
    "/$defs/newAnswerTarget/anyOf/1",
    "/allOf/0/else",
    "/allOf/0/if",
    "/allOf/0/then",
}
EXPECTED_PROVIDER_SCHEMA_SHA256 = (
    "b01fb87d6b4786d6d4866f920ea06a6d1e7257ff6d0c6bd901fa9d8e0099bb3c"
)
REAL_SDK_TEST_ENV = "MRS_XAI_PINNED_SDK_TEST"


@pytest.fixture(scope="module")
def canonical_schema() -> dict[str, Any]:
    value = xai_preflight.load_json(CANONICAL_SCHEMA_PATH)
    assert isinstance(value, dict)
    return value


@pytest.fixture(scope="module")
def transformed_schema(
    canonical_schema: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return xai_preflight.transform_provider_schema(canonical_schema)


def _reverse_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _reverse_json(value[key])
            for key in reversed(tuple(value))
        }
    if isinstance(value, list):
        return [_reverse_json(item) for item in value]
    return copy.deepcopy(value)


def _canonical_profiles() -> tuple[dict[str, Any], ...]:
    manifest = xai_preflight.load_json(PROFILES_PATH)
    return tuple(
        dict(profile)
        for profile in xai_preflight.validate_profiles_manifest(manifest)
    )


def _synthetic_private_artifacts(
    *,
    generated_at: str = "2026-01-01T00:00:00Z",
    private_run_path: str = "/synthetic/private-run-a",
    python_executable_path: str = "/synthetic/venv-a/bin/python",
    git_commit: str = "a" * 40,
) -> dict[str, dict[str, Any]]:
    """Build a text-free minimal artifact set for verifier unit tests."""

    artifacts: dict[str, dict[str, Any]] = {
        name: {"artifact_name": name, "synthetic": True}
        for name in xai_preflight.PRIVATE_ARTIFACT_NAMES
    }
    artifacts["run-manifest.json"] = {
        "generated_at_utc": generated_at,
        "git_commit": git_commit,
        "held_out_records_read": 0,
        "private_run_path": private_run_path,
        "provider_calls": 0,
        "python_executable_path": python_executable_path,
        "real_conversation_records_read": 0,
        "synthetic_only": True,
    }
    artifacts["validation.json"] = {
        "held_out_records_read": 0,
        "passed": True,
        "production_writes": 0,
        "provider_calls": 0,
        "real_conversation_records_read": 0,
    }
    return artifacts


def _write_synthetic_private_run(
    path: Path,
    artifacts: dict[str, dict[str, Any]],
) -> None:
    xai_preflight.write_artifacts(path, artifacts)


class _FakeResponseFormat:
    def __init__(self, *, format_type: int, schema: str):
        self.format_type = format_type
        self.schema = schema


class _FakeToolChoice:
    def __init__(self, mode: int):
        self.mode = mode


class _FakeRequest:
    def __init__(self, values: dict[str, Any], n: int):
        self.model = values["model"]
        self.messages = copy.deepcopy(values["messages"])
        self.reasoning_effort = 1 if values["reasoning_effort"] == "low" else -1
        self.tools = copy.deepcopy(values["tools"])
        self.tool_choice = _FakeToolChoice(0 if values["tool_choice"] == "none" else -1)
        self.response_format = copy.deepcopy(values["response_format"])
        self.search_parameters = copy.deepcopy(values["search_parameters"])
        self.parallel_tool_calls = values["parallel_tool_calls"]
        self.store_messages = values["store_messages"]
        self.n = n

    def HasField(self, name: str) -> bool:
        if name != "search_parameters":
            raise ValueError(name)
        return self.search_parameters is not None

    def SerializeToString(self, deterministic: bool = False) -> bytes:
        assert deterministic is True
        return json.dumps(
            {
                "messages": self.messages,
                "model": self.model,
                "n": self.n,
                "parallel_tool_calls": self.parallel_tool_calls,
                "reasoning_effort": self.reasoning_effort,
                "response_format": {
                    "format_type": self.response_format.format_type,
                    "schema": self.response_format.schema,
                },
                "search_parameters": self.search_parameters,
                "store_messages": self.store_messages,
                "tool_choice": self.tool_choice.mode,
                "tools": self.tools,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")


def _install_fake_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install an in-memory SDK facade that can only construct local requests."""

    grpc_module = types.ModuleType("grpc")

    def forbidden_channel(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise AssertionError("unguarded fake gRPC channel creation")

    grpc_module.secure_channel = forbidden_channel  # type: ignore[attr-defined]
    grpc_module.insecure_channel = forbidden_channel  # type: ignore[attr-defined]
    grpc_module.aio = types.SimpleNamespace(  # type: ignore[attr-defined]
        secure_channel=forbidden_channel,
        insecure_channel=forbidden_channel,
    )

    xai_package = types.ModuleType("xai_sdk")
    xai_package.__path__ = []  # type: ignore[attr-defined]
    chat_module = types.ModuleType("xai_sdk.chat")
    chat_module.__file__ = str(MODULE_PATH)
    proto_package = types.ModuleType("xai_sdk.proto")
    proto_package.__path__ = []  # type: ignore[attr-defined]
    chat_pb2_module = types.ModuleType("xai_sdk.proto.chat_pb2")
    sync_package = types.ModuleType("xai_sdk.sync")
    sync_package.__path__ = []  # type: ignore[attr-defined]
    sync_chat_module = types.ModuleType("xai_sdk.sync.chat")

    def system(message: str) -> dict[str, str]:
        return {"role": "system", "content": message}

    def user(message: str) -> dict[str, str]:
        return {"role": "user", "content": message}

    class BaseChat:
        @staticmethod
        def _make_request(chat: Any, n: int) -> _FakeRequest:
            return _FakeRequest(chat.values, n)

    class ChatClient:
        def __init__(self, channel: Any):
            self._registered_rpc = channel.unary_unary(
                "/xai.synthetic.Chat/Sample"
            )

        def create(self, **kwargs: Any) -> Any:
            return types.SimpleNamespace(values=copy.deepcopy(kwargs))

    class ReasoningEffort:
        @staticmethod
        def Name(value: int) -> str:
            assert value == 1
            return "EFFORT_LOW"

    class ToolMode:
        @staticmethod
        def Name(value: int) -> str:
            assert value == 0
            return "TOOL_MODE_NONE"

    chat_module.BaseChat = BaseChat  # type: ignore[attr-defined]
    chat_module.system = system  # type: ignore[attr-defined]
    chat_module.user = user  # type: ignore[attr-defined]
    chat_pb2_module.FORMAT_TYPE_JSON_SCHEMA = 1  # type: ignore[attr-defined]
    chat_pb2_module.ResponseFormat = _FakeResponseFormat  # type: ignore[attr-defined]
    chat_pb2_module.ReasoningEffort = ReasoningEffort  # type: ignore[attr-defined]
    chat_pb2_module.ToolMode = ToolMode  # type: ignore[attr-defined]
    proto_package.chat_pb2 = chat_pb2_module  # type: ignore[attr-defined]
    sync_chat_module.Client = ChatClient  # type: ignore[attr-defined]

    for name, module in {
        "grpc": grpc_module,
        "xai_sdk": xai_package,
        "xai_sdk.chat": chat_module,
        "xai_sdk.proto": proto_package,
        "xai_sdk.proto.chat_pb2": chat_pb2_module,
        "xai_sdk.sync": sync_package,
        "xai_sdk.sync.chat": sync_chat_module,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    versions = {
        "xai-sdk": "1.19.0",
        "pydantic": "2.12.5",
        "protobuf": "6.33.5",
        "grpcio": "1.78.0",
    }
    monkeypatch.setattr(
        xai_preflight,
        "_installed_version",
        lambda name: versions.get(name),
    )


@pytest.fixture()
def fake_sdk_compilation(
    monkeypatch: pytest.MonkeyPatch,
    transformed_schema: tuple[dict[str, Any], list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, Any], bool]:
    _install_fake_sdk(monkeypatch)
    provider_schema, _ = transformed_schema
    return xai_preflight.compile_sdk_profiles(
        _canonical_profiles(),
        provider_schema,
    )


def test_canonical_schema_identity_and_independent_inventory_reconciliation(
    canonical_schema: dict[str, Any],
) -> None:
    raw = CANONICAL_SCHEMA_PATH.read_bytes()
    neutral = provider_neutral.build_schema_feature_inventory(CANONICAL_SCHEMA_PATH)
    audit = xai_preflight.audit_canonical_schema(
        canonical_schema,
        raw,
        provider_neutral_inventory=neutral,
    )

    assert xai_preflight.sha256_bytes(raw) == xai_preflight.EXPECTED_CANONICAL_SHA256
    assert audit["schema_version"] == xai_preflight.EXPECTED_CANONICAL_VERSION
    assert audit["schema_byte_size"] == 47_726
    assert audit["schema_node_count"] == 426
    assert audit["maximum_schema_node_depth"] == 3
    assert audit["maximum_object_depth"] == 4
    assert audit["maximum_object_property_count"] == 22
    assert audit["maximum_array_nesting"] == 3
    assert audit["unbounded_array_count"] == 31
    assert audit["object_schema_count"] == 43
    assert audit["typed_object_schema_count"] == 38
    assert audit["implicit_object_applicator_schema_count"] == 5
    assert audit["implicit_additional_properties_count"] == 5
    assert audit["nullable_union_count"] == 14
    assert audit["null_admitting_field_schema_count"] == 15
    assert audit["provider_neutral_reconciliation"]["exact_match"] is True
    assert audit["provider_neutral_reconciliation"]["independent_inventory"] == neutral


def test_canonical_enum_const_annotation_and_boolean_inventory_is_exact(
    canonical_schema: dict[str, Any],
) -> None:
    audit = xai_preflight.audit_canonical_schema(
        canonical_schema,
        CANONICAL_SCHEMA_PATH.read_bytes(),
    )

    assert len(audit["enums"]) == 37
    assert sum(len(entry["values"]) for entry in audit["enums"]) == 291
    assert not any(not entry["values"] for entry in audit["enums"])
    assert len(audit["consts"]) == 12
    operation_consts = {
        entry["value"]
        for entry in audit["consts"]
        if entry["pointer"].endswith("/properties/operation/const")
    }
    assert operation_consts == {"add", "update"}
    assert {entry["keyword"] for entry in audit["annotations"]} == {
        "description",
        "title",
    }
    assert {entry["keyword"] for entry in audit["dialect_and_identifier_metadata"]} == {
        "$id",
        "$schema",
    }
    assert len(audit["boolean_schemas"]) == 38
    assert all(entry["value"] is False for entry in audit["boolean_schemas"])


def test_provider_transformation_is_pure_and_does_not_mutate_canonical(
    canonical_schema: dict[str, Any],
) -> None:
    source_before = CANONICAL_SCHEMA_PATH.read_bytes()
    value_before = copy.deepcopy(canonical_schema)

    provider_schema, ledger = xai_preflight.transform_provider_schema(
        canonical_schema
    )

    assert canonical_schema == value_before
    assert CANONICAL_SCHEMA_PATH.read_bytes() == source_before
    assert xai_preflight.sha256_bytes(source_before) == (
        xai_preflight.EXPECTED_CANONICAL_SHA256
    )
    assert xai_preflight.value_sha256(provider_schema) == (
        EXPECTED_PROVIDER_SCHEMA_SHA256
    )
    assert len(ledger) == 5
    assert {entry["canonical_json_pointer"] for entry in ledger} == (
        EXPECTED_TRANSFORM_POINTERS
    )
    assert all(entry["proof_result"] == "exactly_equivalent" for entry in ledger)


def test_implicit_additional_properties_is_expanded_to_preserve_openness() -> None:
    canonical = {
        "type": "object",
        "properties": {"known": {"type": "string"}},
    }
    provider, ledger = xai_preflight.transform_provider_schema(canonical)
    witness = {"known": "synthetic", "unexpected": 1}

    assert provider["additionalProperties"] is True
    assert ledger == [
        {
            "canonical_json_pointer": "",
            "transformation_kind": "insert_explicit_additional_properties_true",
            "canonical_value": {
                "keyword_present": False,
                "json_schema_default": True,
            },
            "provider_value": True,
            "reason": "xAI defaults omitted additionalProperties to false",
            "xai_rule_id": "additional_properties_provider_default_false",
            "semantic_proof_type": "json_schema_default_expansion",
            "proof_result": "exactly_equivalent",
        }
    ]
    assert not xai_preflight.validation_errors(canonical, witness)
    assert not xai_preflight.validation_errors(provider, witness)


def test_explicitly_closed_object_remains_closed() -> None:
    canonical = {
        "type": "object",
        "properties": {"known": {"type": "string"}},
        "additionalProperties": False,
    }
    provider, ledger = xai_preflight.transform_provider_schema(canonical)

    assert provider == canonical
    assert ledger[0]["transformation_kind"] == "identity"
    assert xai_preflight.validation_errors(
        provider, {"known": "synthetic", "unexpected": 1}
    )


def test_required_const_discriminant_proves_disjoint_oneof() -> None:
    schema = {
        "oneOf": [
            {
                "type": "object",
                "required": ["kind"],
                "properties": {"kind": {"const": "left"}},
            },
            {
                "type": "object",
                "required": ["kind"],
                "properties": {"kind": {"enum": ["right", "other"]}},
            },
        ]
    }

    proof = xai_preflight.prove_oneof_disjointness(schema)

    assert proof["alternative_pair_count"] == 1
    assert proof["proved_pair_count"] == 1
    pair = proof["proofs"][0]["pair_proofs"][0]
    assert pair["proof_type"] == "required_discriminant_disjoint_const_or_enum"
    assert pair["discriminant_property"] == "kind"


def test_overlapping_oneof_fails_structural_proof() -> None:
    schema = {
        "oneOf": [
            {"type": "object", "properties": {"value": {"type": "string"}}},
            {"type": "object", "properties": {"other": {"type": "string"}}},
        ]
    }

    proof = xai_preflight.prove_oneof_disjointness(schema)

    assert proof["proved_pair_count"] == 0
    assert proof["all_oneof_pairs_structurally_disjoint"] is False
    assert proof["proofs"][0]["pair_proofs"][0]["proof_type"] == (
        "no_structural_disjointness_proof"
    )


def test_integer_and_number_oneof_domains_are_not_falsely_disjoint() -> None:
    schema = {"oneOf": [{"type": "integer"}, {"type": "number"}]}

    proof = xai_preflight.prove_oneof_disjointness(schema)

    assert proof["proved_pair_count"] == 0
    assert proof["all_oneof_pairs_structurally_disjoint"] is False


def test_numerically_equal_const_domains_are_not_falsely_disjoint() -> None:
    schema = {
        "oneOf": [
            {
                "type": "object",
                "required": ["kind"],
                "properties": {"kind": {"const": 1}},
            },
            {
                "type": "object",
                "required": ["kind"],
                "properties": {"kind": {"const": 1.0}},
            },
        ]
    }

    proof = xai_preflight.prove_oneof_disjointness(schema)

    assert proof["proved_pair_count"] == 0
    assert proof["all_oneof_pairs_structurally_disjoint"] is False


def test_untyped_required_discriminants_do_not_prove_global_disjointness() -> None:
    schema = {
        "oneOf": [
            {
                "required": ["kind"],
                "properties": {"kind": {"const": "left"}},
            },
            {
                "required": ["kind"],
                "properties": {"kind": {"const": "right"}},
            },
        ]
    }

    proof = xai_preflight.prove_oneof_disjointness(schema)

    assert proof["proved_pair_count"] == 0
    assert proof["all_oneof_pairs_structurally_disjoint"] is False


def test_oneof_ref_siblings_are_retained_and_proved_conservatively() -> None:
    schema = {
        "$defs": {"object": {"type": "object"}},
        "oneOf": [
            {
                "$ref": "#/$defs/object",
                "required": ["kind"],
                "properties": {"kind": {"const": "left"}},
            },
            {
                "$ref": "#/$defs/object",
                "required": ["kind"],
                "properties": {"kind": {"const": "right"}},
            },
        ],
    }

    resolved = xai_preflight._resolved_schema(schema, schema["oneOf"][0])
    proof = xai_preflight.prove_oneof_disjointness(schema)

    assert resolved == {
        "allOf": [
            {"type": "object"},
            {
                "required": ["kind"],
                "properties": {"kind": {"const": "left"}},
            },
        ]
    }
    assert proof["proved_pair_count"] == 0
    assert proof["all_oneof_pairs_structurally_disjoint"] is False


def test_all_canonical_oneof_pairs_have_exact_structural_proofs(
    canonical_schema: dict[str, Any],
) -> None:
    proof = xai_preflight.prove_oneof_disjointness(canonical_schema)

    assert proof["oneof_occurrence_count"] == 6
    assert proof["alternative_pair_count"] == 6
    assert proof["proved_pair_count"] == 6
    assert proof["all_oneof_pairs_structurally_disjoint"] is True
    proof_types = {
        pair["proof_type"]
        for occurrence in proof["proofs"]
        for pair in occurrence["pair_proofs"]
    }
    assert proof_types == {
        "disjoint_json_type_domains",
        "required_discriminant_disjoint_const_or_enum",
    }
    discriminants = [
        pair.get("discriminant_property")
        for occurrence in proof["proofs"]
        for pair in occurrence["pair_proofs"]
        if pair["proof_type"] == "required_discriminant_disjoint_const_or_enum"
    ]
    assert discriminants == ["operation"] * 5


def test_empty_enum_and_empty_anyof_are_rejected() -> None:
    schema = {
        "type": "object",
        "properties": {
            "empty_enum": {"enum": []},
            "empty_anyof": {"anyOf": []},
        },
    }

    audit = xai_preflight.audit_provider_keywords(schema)

    assert audit["rejected_construct_count"] == 2
    assert {item["kind"] for item in audit["rejected_constructs"]} == {
        "empty_anyof",
        "empty_enum",
    }


def test_circular_reference_graph_is_rejected() -> None:
    schema = {
        "$defs": {
            "left": {"$ref": "#/$defs/right"},
            "right": {"$ref": "#/$defs/left"},
        },
        "$ref": "#/$defs/left",
    }

    audit = xai_preflight.audit_reference_graph(schema)

    assert audit["reference_count"] == 3
    assert audit["circular_reference_count"] == 1
    assert audit["acyclic"] is False


def test_shared_acyclic_definitions_remain_representable() -> None:
    schema = {
        "$defs": {
            "leaf": {"type": "string"},
            "left": {"$ref": "#/$defs/leaf"},
            "right": {
                "type": "object",
                "properties": {"value": {"$ref": "#/$defs/leaf"}},
            },
        },
        "type": "object",
        "properties": {
            "left": {"$ref": "#/$defs/left"},
            "right": {"$ref": "#/$defs/right"},
        },
    }

    provider, ledger = xai_preflight.transform_provider_schema(schema)
    audit = xai_preflight.audit_reference_graph(provider)

    assert audit["reference_count"] == 4
    assert audit["all_references_local_and_resolved"] is True
    assert audit["acyclic"] is True
    assert {
        entry["transformation_kind"] for entry in ledger
    } == {"insert_explicit_additional_properties_true"}
    assert {
        entry["canonical_json_pointer"] for entry in ledger
    } == {"", "/$defs/right"}
    assert "additionalProperties" not in schema
    assert "additionalProperties" not in schema["$defs"]["right"]
    assert provider["additionalProperties"] is True
    assert provider["$defs"]["right"]["additionalProperties"] is True
    assert [
        (entry["pointer"], entry["reference"])
        for entry in audit["occurrences"]
    ] == [
        (entry["pointer"], entry["reference"])
        for entry in xai_preflight.audit_reference_graph(schema)["occurrences"]
    ]


def test_canonical_reference_graph_is_complete_and_acyclic(
    canonical_schema: dict[str, Any],
) -> None:
    audit = xai_preflight.audit_reference_graph(canonical_schema)

    assert audit["reference_count"] == 186
    assert audit["unique_reference_target_count"] == 91
    assert audit["graph_vertex_count"] == 62
    assert audit["graph_edge_count"] == 156
    assert audit["all_references_local_and_resolved"] is True
    assert audit["circular_reference_count"] == 0
    assert audit["acyclic"] is True
    assert audit["longest_owner_chain"] == 6


@pytest.mark.parametrize("boolean_value", [True, False])
def test_boolean_property_schema_is_rejected(boolean_value: bool) -> None:
    schema = {
        "type": "object",
        "properties": {"rejected": boolean_value},
        "additionalProperties": False,
    }

    audit = xai_preflight.audit_provider_keywords(schema)

    assert audit["rejected_construct_count"] == 1
    assert audit["rejected_constructs"][0]["kind"] == "boolean_property_schema"
    assert audit["rejected_constructs"][0]["value"] is boolean_value


def test_items_array_fails_and_prefixitems_is_supported() -> None:
    tuple_items = {"type": "array", "items": [{"type": "string"}]}
    prefix_items = {
        "type": "array",
        "prefixItems": [{"type": "string"}, {"type": "integer"}],
        "items": False,
    }

    rejected = xai_preflight.audit_provider_keywords(tuple_items)
    supported = xai_preflight.audit_provider_keywords(prefix_items)

    assert rejected["items_array_occurrence_count"] == 1
    assert rejected["rejected_constructs"][0]["kind"] == (
        "items_array_tuple_syntax"
    )
    assert supported["items_array_occurrence_count"] == 0
    assert supported["prefix_items_occurrence_count"] == 1
    assert supported["rejected_construct_count"] == 0


def test_mincontains_and_maxcontains_are_rejected() -> None:
    schema = {
        "type": "array",
        "contains": {"type": "string"},
        "minContains": 1,
        "maxContains": 2,
    }

    audit = xai_preflight.audit_provider_keywords(schema)

    assert audit["rejected_construct_count"] == 2
    assert {item["kind"] for item in audit["rejected_constructs"]} == {
        "maxContains",
        "minContains",
    }


def test_allof_single_is_supported_and_multiple_is_best_effort() -> None:
    single = {"allOf": [{"type": "string"}]}
    multiple = {"allOf": [{"type": "string"}, {"minLength": 1}]}
    empty = {"allOf": []}

    single_audit = xai_preflight.audit_provider_keywords(single)
    multiple_audit = xai_preflight.audit_provider_keywords(multiple)
    empty_audit = xai_preflight.audit_provider_keywords(empty)

    assert single_audit["best_effort_occurrence_count"] == 0
    assert single_audit["rejected_construct_count"] == 0
    assert multiple_audit["best_effort_occurrences"] == [
        {"pointer": "/allOf", "kind": "multiple_allof"}
    ]
    assert empty_audit["rejected_constructs"] == [
        {"pointer": "/allOf", "kind": "empty_allof_unsupported"}
    ]


def test_regex_supported_subset_and_rejected_advanced_constructs() -> None:
    supported_unanchored_patterns = [
        r"[A-Za-z0-9_.:-]{1,8}",
        r"(foo|bar).+\d\s?",
        r"(?:foo){1,2}",
        r"\n\t\r\f\x41\u0042",
    ]
    anchored_patterns = [
        r"^[A-Za-z0-9_.:-]{1,8}$",
        r"^(foo|bar).+\d\s?$",
        r"^(?:foo){1,2}$",
        r"^\n\t\r\f\x41\u0042$",
    ]
    rejected_patterns = [
        r"^(a)\1$",
        r"^\p{L}+$",
        r"^\bword\b$",
        r"^a(?=b)b$",
        r"^a(?<=a)b$",
        r"(?i)^abc$",
        r"^(?(1)a|b)$",
    ]
    supported_unanchored_schema = {
        "type": "object",
        "properties": {
            f"p{index}": {"type": "string", "pattern": pattern}
            for index, pattern in enumerate(supported_unanchored_patterns)
        },
    }
    anchored_schema = {
        "type": "object",
        "properties": {
            f"p{index}": {"type": "string", "pattern": pattern}
            for index, pattern in enumerate(anchored_patterns)
        },
    }
    rejected_schema = {
        "type": "object",
        "properties": {
            f"p{index}": {"type": "string", "pattern": pattern}
            for index, pattern in enumerate(rejected_patterns)
        },
    }

    supported = xai_preflight.audit_provider_keywords(supported_unanchored_schema)
    anchored = xai_preflight.audit_provider_keywords(anchored_schema)
    rejected = xai_preflight.audit_provider_keywords(rejected_schema)

    assert supported["pattern_count"] == len(supported_unanchored_patterns)
    assert supported["all_patterns_in_supported_subset"] is True
    assert supported["regex_exact_semantics_compatible"] is True
    assert supported["rejected_construct_count"] == 0
    assert anchored["pattern_count"] == len(anchored_patterns)
    assert anchored["all_patterns_avoid_documented_rejections"] is True
    assert anchored["all_patterns_in_supported_subset"] is False
    assert anchored["regex_exact_semantics_compatible"] is False
    assert anchored["regex_semantic_uncertainty_count"] == len(anchored_patterns)
    assert anchored["rejected_construct_count"] == 0
    assert all(item["explicit_outer_anchors"] for item in anchored["patterns"])
    assert rejected["pattern_count"] == len(rejected_patterns)
    assert rejected["all_patterns_avoid_documented_rejections"] is False
    assert rejected["all_patterns_in_supported_subset"] is False
    assert rejected["rejected_construct_count"] == len(rejected_patterns)
    assert {item["kind"] for item in rejected["rejected_constructs"]} == {
        "unsupported_regex_backreference",
        "unsupported_regex_conditional_expression",
        "unsupported_regex_inline_modifier",
        "unsupported_regex_lookaround",
        "unsupported_regex_unicode_property_escape",
        "unsupported_regex_word_boundary",
    }


def test_supported_and_unlisted_formats_are_distinguished_without_removal() -> None:
    formats = sorted(xai_preflight.ENFORCED_FORMATS) + ["hostname"]
    schema = {
        "type": "object",
        "properties": {
            value: {"type": "string", "format": value} for value in formats
        },
    }

    audit = xai_preflight.audit_provider_keywords(schema)
    provider, _ = xai_preflight.transform_provider_schema(schema)

    assert audit["format_count"] == 9
    assert {
        item["format"] for item in audit["formats"] if item["provider_enforced"]
    } == xai_preflight.ENFORCED_FORMATS
    assert audit["best_effort_occurrences"] == [
        {"pointer": "/properties/hostname/format", "kind": "unlisted_format"}
    ]
    assert provider["properties"]["hostname"]["format"] == "hostname"


def test_constraint_thresholds_are_classified_at_and_beyond_limits() -> None:
    schema = {
        "type": "object",
        "properties": {
            "string_at": {"type": "string", "maxLength": 2048},
            "string_over": {"type": "string", "maxLength": 2049},
            "array_at": {"type": "array", "maxItems": 256},
            "array_over": {"type": "array", "maxItems": 257},
            "object_at": {"type": "object", "maxProperties": 64},
            "object_over": {"type": "object", "maxProperties": 65},
            "number": {"type": "number", "minimum": -10**100, "maximum": 10**100},
        },
    }

    audit = xai_preflight.audit_provider_keywords(schema)
    by_pointer = {entry["pointer"]: entry for entry in audit["constraints"]}

    for pointer in (
        "/properties/string_at/maxLength",
        "/properties/array_at/maxItems",
        "/properties/object_at/maxProperties",
    ):
        assert by_pointer[pointer]["provider_guarantee"] == (
            "guaranteed_within_documented_threshold"
        )
    for pointer in (
        "/properties/string_over/maxLength",
        "/properties/array_over/maxItems",
        "/properties/object_over/maxProperties",
    ):
        assert by_pointer[pointer]["provider_guarantee"] == (
            "accepted_best_effort_above_documented_threshold"
        )
    assert by_pointer["/properties/number/minimum"]["provider_guarantee"] == (
        "guaranteed_no_documented_threshold"
    )
    assert by_pointer["/properties/number/maximum"]["provider_guarantee"] == (
        "guaranteed_no_documented_threshold"
    )
    assert sum(
        item["kind"] == "over_threshold_constraint"
        for item in audit["best_effort_occurrences"]
    ) == 3


def test_canonical_keyword_audit_has_only_documented_incompleteness(
    canonical_schema: dict[str, Any],
) -> None:
    audit = xai_preflight.audit_provider_keywords(canonical_schema)

    assert audit["rejected_construct_count"] == 0
    assert audit["best_effort_occurrence_count"] == 3
    assert {item["kind"] for item in audit["best_effort_occurrences"]} == {
        "else",
        "if",
        "then",
    }
    assert audit["undocumented_guarantee_occurrence_count"] == 17
    assert {item["kind"] for item in audit["undocumented_guarantee_occurrences"]} == {
        "uniqueItems"
    }
    assert audit["array_schema_count"] == 31
    assert audit["items_array_occurrence_count"] == 0
    assert audit["prefix_items_occurrence_count"] == 0
    assert audit["pattern_count"] == 14
    assert audit["all_patterns_avoid_documented_rejections"] is True
    assert audit["all_patterns_in_supported_subset"] is False
    assert audit["regex_exact_semantics_compatible"] is False
    assert audit["regex_semantic_uncertainty_count"] == 14
    assert {
        item["pointer"] for item in audit["regex_semantic_uncertainties"]
    } == {item["pointer"] for item in audit["patterns"]}
    assert all(item["explicit_outer_anchors"] for item in audit["patterns"])
    assert audit["format_count"] == 0


def test_canonical_and_provider_fixture_corpus_agrees_exactly(
    canonical_schema: dict[str, Any],
    transformed_schema: tuple[dict[str, Any], list[dict[str, Any]]],
) -> None:
    provider_schema, ledger = transformed_schema

    audit = xai_preflight.build_semantic_equivalence_audit(
        canonical_schema,
        provider_schema,
        ledger,
    )

    assert audit["structurally_proved_transformation_equivalence"] is True
    assert audit["transformation_ledger_proofs_complete"] is True
    assert audit["structurally_proved_equivalence"] is False
    assert audit["overall_documented_provider_semantics_equivalent"] is False
    assert audit["documented_provider_regex_semantics_compatible"] is False
    assert audit["all_transformed_locations_proved"] is True
    assert len(audit["transformed_location_proofs"]) == 5
    assert audit["existing_wholly_synthetic_semantic_delta_fixture_count"] == 6
    assert audit["existing_wholly_synthetic_semantic_delta_fixture_ids"] == [
        "semantic_module_behavioral",
        "semantic_module_empty_genesis",
        "semantic_module_empty_later",
        "semantic_test_direct_answer",
        "semantic_test_noop_genesis",
        "semantic_test_noop_later",
    ]
    assert audit["root_fixture_case_count"] >= 18
    assert audit["boundary_case_count"] >= 274
    assert audit["combinator_coverage"] == {
        "oneOf_occurrences": 6,
        "anyOf_occurrences": 15,
        "allOf_occurrences": 1,
        "allOf_exercised_by_root_genesis_and_later_cases": True,
    }
    assert audit["actual_format_occurrence_count"] == 0
    assert audit["bounded_fixture_agreement"] is True
    assert audit["bounded_fixture_expectations_met"] is True
    cases = {item["case_id"]: item for item in audit["cases"]}
    for fixture_id in audit[
        "existing_wholly_synthetic_semantic_delta_fixture_ids"
    ]:
        case = cases[f"existing_fixture:{fixture_id}"]
        assert case["canonical_valid"] is True
        assert case["provider_valid"] is True
        assert case["validators_agree"] is True
    for case_id, expected_valid in (
        ("optional_field_absent_valid", True),
        ("optional_object_unexpected_property_invalid", False),
        ("root_unbounded_array_257_valid", True),
    ):
        case = cases[case_id]
        assert case["expected_valid"] is expected_valid
        assert case["canonical_valid"] is expected_valid
        assert case["provider_valid"] is expected_valid
    terminal_newline_cases = [
        item
        for item in audit["cases"]
        if item["case_id"].startswith("pattern:terminal_newline_canonical:")
    ]
    assert len(terminal_newline_cases) == 14
    assert all(item["canonical_valid"] for item in terminal_newline_cases)
    assert all(item["provider_valid"] for item in terminal_newline_cases)
    assert audit["regex_semantic_mismatch_count"] == 14
    assert len(audit["regex_semantic_evidence"]) == 14
    assert all(
        item["canonical_validator_accepts"]
        and item["provider_schema_standard_validator_accepts"]
        and not item["documented_xai_full_string_interpretation_accepts"]
        and not item["exact_semantics_proved"]
        for item in audit["regex_semantic_evidence"]
    )
    assert len(audit["unresolved_semantic_uncertainty"]) == 14
    assert {
        item["kind"] for item in audit["unresolved_semantic_uncertainty"]
    } == {"documented_xai_full_string_regex_semantics_differ"}


def test_provider_rule_manifest_has_exact_nonweakened_rule_sets() -> None:
    rules = xai_preflight.load_json(RULES_PATH)
    xai_preflight.validate_rules_manifest(rules)
    subset = rules["json_schema_subset"]

    assert {item["rule_id"] for item in subset["supported_forms"]} == {
        "supported_allof_single",
        "supported_anyof",
        "supported_array",
        "supported_boolean",
        "supported_const",
        "supported_defs_acyclic",
        "supported_enum",
        "supported_integer",
        "supported_null",
        "supported_number",
        "supported_object",
        "supported_oneof_as_anyof",
        "supported_prefixitems",
        "supported_ref_acyclic",
        "supported_string",
    }
    assert {item["rule_id"] for item in subset["rejected_constructs"]} == {
        "rejected_array_items_tuple",
        "rejected_boolean_property_schema",
        "rejected_empty_anyof",
        "rejected_empty_enum",
        "rejected_maxcontains",
        "rejected_mincontains",
    }
    assert {item["rule_id"] for item in subset["best_effort_constructs"]} == {
        "best_effort_allof_multiple",
        "best_effort_conditionals",
        "best_effort_not",
        "best_effort_over_threshold_constraint",
        "best_effort_unlisted_format",
    }
    assert set(subset["enforced_formats"]) == xai_preflight.ENFORCED_FORMATS
    assert rules["integrity_expectations"][
        "silent_rule_removal_or_weakening_forbidden"
    ] is True
    assert rules["integrity_expectations"]["stable_rule_ids_required"] is True
    assert "verbatim" not in rules["user_scope_resolution"]
    assert all(
        len(source["content_sha256"]) == 64
        and source["authority"]
        in {"official_xai_documentation", "official_pypi_metadata"}
        for source in rules["source_capture"]["sources"]
    )


def test_rule_manifest_removal_is_detected() -> None:
    rules = copy.deepcopy(xai_preflight.load_json(RULES_PATH))
    rules["json_schema_subset"]["rejected_constructs"].pop()

    with pytest.raises(
        xai_preflight.PreflightError,
        match="frozen semantic digest|integrity mismatch",
    ):
        xai_preflight.validate_rules_manifest(rules)


def test_exact_profiles_are_frozen_and_differ_only_by_model_identity() -> None:
    manifest = xai_preflight.load_json(PROFILES_PATH)
    profiles = xai_preflight.validate_profiles_manifest(manifest)

    assert [profile["profile_id"] for profile in profiles] == [
        "xai-grok-4.3-low-ledger-v1",
        "xai-grok-4.6-low-ledger-v1",
    ]
    assert [profile["model"] for profile in profiles] == ["grok-4.3", "grok-4.6"]
    assert all(profile["reasoning_effort"] == "low" for profile in profiles)
    assert all(profile["sdk_version"] == "1.19.0" for profile in profiles)
    assert manifest["profile_comparison"] == {
        "arm_interpretation": "two_execution_profiles_for_one_machine_ledger_role",
        "status": "pending_not_run",
        "winner": None,
    }
    assert manifest["downstream_reviewer_writer_constraint"] == {
        "model": "gpt-5.6-sol",
        "provider": "OpenAI",
        "reasoning": "medium",
        "status": "preserved_unchanged_not_validated_in_phase_1_3",
    }


def test_fake_sdk_compiled_profiles_differ_only_by_model_id(
    fake_sdk_compilation: tuple[list[dict[str, Any]], dict[str, Any], bool],
) -> None:
    results, _, differ_only_by_model = fake_sdk_compilation

    assert differ_only_by_model is True
    assert [result["model"] for result in results] == ["grok-4.3", "grok-4.6"]
    assert all(
        result["request_construction_status"] == "succeeded_without_transport"
        for result in results
    )
    assert results[0]["serialized_request_sha256"] != (
        results[1]["serialized_request_sha256"]
    )


def test_fake_sdk_serialises_exact_low_reasoning_setting(
    fake_sdk_compilation: tuple[list[dict[str, Any]], dict[str, Any], bool],
) -> None:
    results, _, _ = fake_sdk_compilation

    assert [result["reasoning_effort"] for result in results] == ["low", "low"]
    assert all(result["message_count"] == 2 for result in results)
    assert all(result["synthetic_messages_only"] is True for result in results)


def test_fake_sdk_requests_have_no_tools_search_code_or_streaming(
    fake_sdk_compilation: tuple[list[dict[str, Any]], dict[str, Any], bool],
) -> None:
    results, network, _ = fake_sdk_compilation

    for result in results:
        assert result["tool_count"] == 0
        assert result["tool_choice"] == "none"
        assert result["search_parameters_present"] is False
        assert result["web_search_enabled"] is False
        assert result["x_search_enabled"] is False
        assert result["code_execution_enabled"] is False
        assert result["streaming"] is False
        assert result["provider_calls_made"] == 0
        assert result["inference_requests_made"] == 0
        assert result["model_list_requests_made"] == 0
    assert network["transport_rpc_invocations"] == 0


def test_network_guard_blocks_all_deliberate_connection_categories_while_compiling(
    fake_sdk_compilation: tuple[list[dict[str, Any]], dict[str, Any], bool],
) -> None:
    results, network, _ = fake_sdk_compilation

    assert len(results) == 2
    assert network["deliberate_connection_attempt_blocked"] is True
    assert set(network["attempts_observed"]) == {"dns", "grpc", "http", "socket"}
    assert all(count >= 1 for count in network["attempts_observed"].values())
    assert network["provider_calls_made"] == 0
    assert network["xai_inference_calls"] == 0
    assert network["openai_calls"] == 0
    assert network["x_calls"] == 0
    assert network["model_list_calls"] == 0


def test_sdk_compilation_requires_no_credential_or_api_key_in_request(
    fake_sdk_compilation: tuple[list[dict[str, Any]], dict[str, Any], bool],
) -> None:
    results, network, _ = fake_sdk_compilation

    assert all(result["api_key_in_request"] is False for result in results)
    assert network["provider_key_environment_names_removed_before_sdk_import"] == list(
        xai_preflight.PROVIDER_KEY_ENV_NAMES
    )
    assert os.environ["XAI_SDK_DISABLE_TRACING"] == "1"
    assert os.environ[
        "XAI_SDK_DISABLE_SENSITIVE_TELEMETRY_ATTRIBUTES"
    ] == "1"


def test_schema_and_profile_input_order_do_not_change_substantive_hashes(
    canonical_schema: dict[str, Any],
) -> None:
    reversed_schema = _reverse_json(canonical_schema)
    first_provider, first_ledger = xai_preflight.transform_provider_schema(
        canonical_schema
    )
    second_provider, second_ledger = xai_preflight.transform_provider_schema(
        reversed_schema
    )
    manifest = xai_preflight.load_json(PROFILES_PATH)
    reverse_manifest = copy.deepcopy(manifest)
    reverse_manifest["profiles"].reverse()

    assert xai_preflight.value_sha256(first_provider) == (
        xai_preflight.value_sha256(second_provider)
    )
    assert xai_preflight.value_sha256(first_provider) == (
        EXPECTED_PROVIDER_SCHEMA_SHA256
    )
    assert first_ledger == second_ledger
    assert xai_preflight.validate_profiles_manifest(manifest) == (
        xai_preflight.validate_profiles_manifest(reverse_manifest)
    )


def test_two_independent_fake_sdk_compilations_are_byte_reproducible(
    monkeypatch: pytest.MonkeyPatch,
    transformed_schema: tuple[dict[str, Any], list[dict[str, Any]]],
) -> None:
    _install_fake_sdk(monkeypatch)
    provider_schema, _ = transformed_schema
    profiles = _canonical_profiles()

    first, first_network, first_equal = xai_preflight.compile_sdk_profiles(
        profiles, provider_schema
    )
    second, second_network, second_equal = xai_preflight.compile_sdk_profiles(
        tuple(reversed(profiles)), provider_schema
    )
    first_by_model = {item["model"]: item for item in first}
    second_by_model = {item["model"]: item for item in second}

    assert first_equal is second_equal is True
    assert first_network["provider_calls_made"] == 0
    assert second_network["provider_calls_made"] == 0
    for model in ("grok-4.3", "grok-4.6"):
        assert first_by_model[model]["sdk_emitted_schema_sha256"] == (
            second_by_model[model]["sdk_emitted_schema_sha256"]
        )
        assert first_by_model[model]["serialized_request_sha256"] == (
            second_by_model[model]["serialized_request_sha256"]
        )
        assert first_by_model[model]["serialized_request_byte_length"] == (
            second_by_model[model]["serialized_request_byte_length"]
        )


def test_dependency_lock_is_exact_hashed_and_sdk_pin_is_direct() -> None:
    assert LOCK_PATH.is_file(), "tracked exact dependency lock is required"
    lock = xai_preflight.load_json(LOCK_PATH)
    packages = lock["packages"]

    assert lock["lock_format"] == (
        "proposition-ledger-xai-sdk-environment-lock-v1"
    )
    assert xai_preflight.value_sha256(lock) == (
        xai_preflight.EXPECTED_LOCK_SEMANTIC_SHA256
    )
    assert lock["python"]["implementation"] == "CPython"
    assert lock["python"]["full_version"].startswith("3.10.")
    assert lock["reconstruction"]["python_requirement"].startswith(
        "CPython==3.10."
    )
    expected_scrubbed_names = (
        "ANTHROPIC_API_KEY",
        "BRAVE_API_KEY",
        "BRAVE_SEARCH_API_KEY",
        "COHERE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_CUSTOM_SEARCH_API_KEY",
        "GOOGLE_SEARCH_API_KEY",
        "MISTRAL_API_KEY",
        "OPENAI_ADMIN_API_KEY",
        "OPENAI_API_KEY",
        "XAI_API_KEY",
        "XAI_MANAGEMENT_KEY",
        "X_ACCESS_SECRET",
        "X_ACCESS_TOKEN",
        "X_BEARER_TOKEN",
        "X_CONSUMER_KEY",
        "X_CONSUMER_SECRET",
    )
    assert xai_preflight.PROVIDER_KEY_ENV_NAMES == expected_scrubbed_names
    assert lock["reconstruction"][
        "credential_variables_to_unset_before_provider_import"
    ] == list(expected_scrubbed_names)
    assert len(packages) == len({package["name"] for package in packages})
    assert len(packages) == 37
    assert all(package["version"] and package["wheel_sha256"] for package in packages)
    assert all(
        len(package["wheel_sha256"]) == 64
        for package in packages
    )
    sdk = next(package for package in packages if package["name"] == "xai-sdk")
    assert sdk["version"] == "1.19.0"
    assert sdk["dependency_roles"] == ["provider_runtime_root"]
    assert sdk["wheel_sha256"] == xai_preflight.EXPECTED_XAI_WHEEL_SHA256
    assert lock["integrity"]["binary_wheels_only"] is True
    assert lock["integrity"]["official_pypi_only"] is True
    assert lock["integrity"]["requirements_hash_enforcement_status"] == "passed"
    assert lock["integrity"]["wheel_hash_verification_status"] == (
        "all_37_passed"
    )
    assert lock["environment_bootstrap"] == {
        "pip": {
            "classification": "venv_bootstrap_not_part_of_resolved_wheel_set",
            "version": "22.0.2",
        },
        "setuptools": {
            "classification": "venv_bootstrap_not_part_of_resolved_wheel_set",
            "version": "59.6.0",
        },
        "virtual_environment": {
            "creation_mode": "python3 -m venv --copies",
            "lib64_symlink_removed_when_created_by_venv": True,
            "symlinks_permitted": False,
        },
    }
    required_source_digests = {
        "dependency_graph_sha256",
        "installed_inventory_sha256",
        "requirements_lock_sha256",
        "wheel_hash_manifest_sha256",
        "xai_sdk_client_py_sha256",
        "xai_sdk_distribution_metadata_sha256",
        "xai_sdk_distribution_record_sha256",
        "xai_sdk_distribution_wheel_metadata_sha256",
        "xai_sdk_installed_source_tree_sha256",
        "xai_sdk_primary_chat_py_sha256",
        "xai_sdk_proto_init_py_sha256",
        "xai_sdk_proto_v6_chat_pb2_grpc_py_sha256",
        "xai_sdk_proto_v6_chat_pb2_py_sha256",
        "xai_sdk_proto_v6_chat_pb2_pyi_sha256",
        "xai_sdk_sync_chat_py_sha256",
        "xai_sdk_sync_client_py_sha256",
        "xai_sdk_types_chat_py_sha256",
    }
    assert set(lock["source_digests"]) == required_source_digests
    assert all(
        len(digest) == 64
        and set(digest) <= set("0123456789abcdef")
        for digest in lock["source_digests"].values()
    )


def test_dependency_lock_is_authenticated_before_wheel_paths_are_consumed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock = copy.deepcopy(xai_preflight.load_json(LOCK_PATH))
    lock["packages"][0]["wheel_filename"] = "../escaped.whl"

    with pytest.raises(xai_preflight.PreflightError, match="frozen semantic digest"):
        xai_preflight._validated_lock_packages(lock)

    monkeypatch.setattr(
        xai_preflight,
        "EXPECTED_LOCK_SEMANTIC_SHA256",
        xai_preflight.value_sha256(lock),
    )
    with pytest.raises(xai_preflight.PreflightError, match="unsafe wheel_filename"):
        xai_preflight._validated_lock_packages(lock)


def test_dependency_lock_rejects_duplicate_wheel_basenames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock = copy.deepcopy(xai_preflight.load_json(LOCK_PATH))
    lock["packages"][1]["wheel_filename"] = lock["packages"][0][
        "wheel_filename"
    ]
    monkeypatch.setattr(
        xai_preflight,
        "EXPECTED_LOCK_SEMANTIC_SHA256",
        xai_preflight.value_sha256(lock),
    )

    with pytest.raises(xai_preflight.PreflightError, match="duplicate wheel_filename"):
        xai_preflight._validated_lock_packages(lock)


def test_dependency_lock_cannot_weaken_provider_credential_scrubbing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock = copy.deepcopy(xai_preflight.load_json(LOCK_PATH))
    lock["reconstruction"][
        "credential_variables_to_unset_before_provider_import"
    ].pop()
    monkeypatch.setattr(
        xai_preflight,
        "EXPECTED_LOCK_SEMANTIC_SHA256",
        xai_preflight.value_sha256(lock),
    )

    with pytest.raises(xai_preflight.PreflightError, match="scrub list differs"):
        xai_preflight._validated_lock_packages(lock)


def test_environment_ancillary_reader_requires_real_regular_files(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    rebuild_dir = run_dir / "rebuild"
    rebuild_dir.mkdir(parents=True)
    inventory = run_dir / "sdk-installed-inventory.json"
    inventory.write_bytes(b'{"synthetic":true}\n')

    content = xai_preflight._read_environment_ancillary(
        rebuild_dir, "sdk-installed-inventory.json"
    )
    assert content == b'{"synthetic":true}\n'
    assert xai_preflight.sha256_bytes(content) == xai_preflight.sha256_bytes(
        inventory.read_bytes()
    )

    outside = tmp_path / "outside.json"
    outside.write_bytes(b'{"outside":true}\n')
    inventory.unlink()
    inventory.symlink_to(outside)
    with pytest.raises(xai_preflight.PreflightError, match="non-symlink"):
        xai_preflight._read_environment_ancillary(
            rebuild_dir, "sdk-installed-inventory.json"
        )


def test_all_emittable_status_constants_use_the_frozen_vocabulary() -> None:
    allowed = {
        "locally_compatible_exact",
        "locally_compatible_with_mandatory_canonical_postvalidation",
        "incompatible_with_documented_xai_schema_subset",
        "local_profile_construction_incompatible",
        "sdk_local_compilation_unavailable",
        "provider_rules_unavailable_or_conflicting",
        "dependency_environment_not_reproducible",
    }
    defined = {
        value
        for name, value in vars(xai_preflight).items()
        if name.startswith("STATUS_") and isinstance(value, str)
    }

    assert defined <= allowed


def test_local_sdk_profile_rejection_emits_distinct_incompatible_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        xai_preflight,
        "_environment_record",
        lambda lock, output_dir: {"lock_matches_environment": True},
    )

    def reject_profiles(
        profiles: Sequence[Mapping[str, Any]],
        provider_schema: Mapping[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, Any], bool]:
        del provider_schema
        results = [
            {
                "profile_id": profile["profile_id"],
                "model": profile["model"],
                "request_construction_status": "rejected_locally",
                "exception_type": "SyntheticProfileRejection",
                "exception_message": "synthetic local rejection only",
                "provider_calls_made": 0,
            }
            for profile in profiles
        ]
        network = {
            "deliberate_connection_attempt_blocked": True,
            "provider_calls_made": 0,
            "xai_inference_calls": 0,
            "openai_calls": 0,
            "x_calls": 0,
            "model_list_calls": 0,
            "transport_rpc_invocations": 0,
        }
        return results, network, False

    monkeypatch.setattr(xai_preflight, "compile_sdk_profiles", reject_profiles)
    artifacts = xai_preflight.build_artifacts(
        output_dir=tmp_path,
        generated_at_utc="2026-01-01T00:00:00Z",
        git_commit="a" * 40,
    )
    compatibility = artifacts["compatibility-record.json"]
    validation = artifacts["validation.json"]

    assert compatibility["status"] == (
        "local_profile_construction_incompatible"
    )
    assert compatibility["local_profile_construction_status"] == (
        "local_profile_construction_incompatible"
    )
    assert compatibility["provider_request_compatible"] is False
    assert compatibility["provider_schema_locally_serializable"] is False
    assert validation["preflight_execution_valid"] is True
    assert validation["compatibility_passed"] is False
    assert validation["compatibility_failure_correctly_recorded"] is True


def test_verify_stored_artifacts_requires_exact_checksum_filename_set(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "private-run"
    artifacts = _synthetic_private_artifacts()
    _write_synthetic_private_run(run_dir, artifacts)
    assert xai_preflight.verify_stored_artifacts(run_dir, artifacts) == ()

    checksum_path = run_dir / "SHA256SUMS"
    lines = checksum_path.read_text(encoding="utf-8").splitlines()
    checksum_path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")

    errors = xai_preflight.verify_stored_artifacts(run_dir, artifacts)

    assert any("checksum filename set mismatch" in error for error in errors)


def test_verify_stored_artifacts_rejects_checksum_path_traversal(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "private-run"
    artifacts = _synthetic_private_artifacts()
    _write_synthetic_private_run(run_dir, artifacts)
    checksum_path = run_dir / "SHA256SUMS"
    lines = checksum_path.read_text(encoding="utf-8").splitlines()
    digest, _ = lines[0].split("  ", 1)
    lines[0] = f"{digest}  ../synthetic-outside.json"
    checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    errors = xai_preflight.verify_stored_artifacts(run_dir, artifacts)

    assert any("unsafe/malformed checksum line: 1" in error for error in errors)
    assert any("checksum filename set mismatch" in error for error in errors)


def test_verify_stored_artifacts_rejects_any_private_run_symlink(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "private-run"
    artifacts = _synthetic_private_artifacts()
    _write_synthetic_private_run(run_dir, artifacts)
    (run_dir / "synthetic-link").symlink_to(run_dir / "validation.json")

    errors = xai_preflight.verify_stored_artifacts(run_dir, artifacts)

    assert any("private run contains symlinks" in error for error in errors)
    assert any("synthetic-link" in error for error in errors)


def test_private_artifact_writers_reject_symlink_root_and_target(
    tmp_path: Path,
) -> None:
    artifacts = _synthetic_private_artifacts()
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(real_root, target_is_directory=True)
    with pytest.raises(xai_preflight.PreflightError, match="symlink"):
        xai_preflight.write_artifacts(linked_root, artifacts)

    run_root = tmp_path / "run-root"
    run_root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"preserve-me")
    (run_root / "run-manifest.json").symlink_to(outside)
    with pytest.raises(xai_preflight.PreflightError, match="non-symlink"):
        xai_preflight.write_artifacts(run_root, artifacts)
    assert outside.read_bytes() == b"preserve-me"


def test_checksum_writer_hashes_only_regular_nonsymlink_artifacts(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "private-run"
    artifacts = _synthetic_private_artifacts()
    _write_synthetic_private_run(run_dir, artifacts)
    target = run_dir / "validation.json"
    outside = tmp_path / "outside.json"
    outside.write_bytes(target.read_bytes())
    target.unlink()
    target.symlink_to(outside)

    with pytest.raises(xai_preflight.PreflightError, match="checksum source"):
        xai_preflight._write_checksums(run_dir)


def test_verify_only_checks_run_manifest_before_reading_or_rebuilding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "private-run"
    run_dir.mkdir()
    outside = tmp_path / "outside-manifest.json"
    outside.write_text('{"generated_at_utc":"2026-01-01T00:00:00Z"}\n')
    (run_dir / "run-manifest.json").symlink_to(outside)
    rebuilt = False

    def forbidden_build(**kwargs: Any) -> dict[str, Any]:
        del kwargs
        nonlocal rebuilt
        rebuilt = True
        return {}

    monkeypatch.setattr(xai_preflight, "build_artifacts", forbidden_build)
    with pytest.raises(xai_preflight.PreflightError, match="non-symlink"):
        xai_preflight.main(["--output-dir", str(run_dir), "--verify-only"])
    assert rebuilt is False


def test_determinism_normalises_only_permitted_run_manifest_metadata(
    tmp_path: Path,
) -> None:
    first_dir = tmp_path / "run-a"
    second_dir = tmp_path / "run-b"
    first = _synthetic_private_artifacts()
    second = _synthetic_private_artifacts(
        generated_at="2026-01-02T00:00:00Z",
        private_run_path="/synthetic/private-run-b",
        python_executable_path="/synthetic/venv-b/bin/python",
    )
    _write_synthetic_private_run(first_dir, first)
    _write_synthetic_private_run(second_dir, second)

    permitted = xai_preflight.compare_determinism(first_dir, second_dir)

    assert permitted["normalized_run_manifest_equal"] is True
    assert permitted["all_substantive_outputs_byte_identical"] is True
    assert permitted["normalized_run_manifest_ignored_fields"] == [
        "generated_at_utc",
        "private_run_path",
        "python_executable_path",
    ]

    changed = _synthetic_private_artifacts(
        generated_at="2026-01-02T00:00:00Z",
        private_run_path="/synthetic/private-run-b",
        python_executable_path="/synthetic/venv-b/bin/python",
        git_commit="b" * 40,
    )
    _write_synthetic_private_run(second_dir, changed)
    forbidden = xai_preflight.compare_determinism(first_dir, second_dir)

    assert forbidden["normalized_run_manifest_equal"] is False
    assert forbidden["all_substantive_outputs_byte_identical"] is False
    assert forbidden["normalized_run_manifest_first_sha256"] != (
        forbidden["normalized_run_manifest_second_sha256"]
    )


def test_determinism_requires_matching_manifest_keys_and_typed_metadata(
    tmp_path: Path,
) -> None:
    first_dir = tmp_path / "run-a"
    second_dir = tmp_path / "run-b"
    first = _synthetic_private_artifacts()
    second = _synthetic_private_artifacts()
    second["run-manifest.json"].pop("git_commit")
    _write_synthetic_private_run(first_dir, first)
    _write_synthetic_private_run(second_dir, second)
    with pytest.raises(xai_preflight.PreflightError, match="key sets differ"):
        xai_preflight.compare_determinism(first_dir, second_dir)

    second = _synthetic_private_artifacts()
    first["run-manifest.json"].pop("generated_at_utc")
    second["run-manifest.json"].pop("generated_at_utc")
    _write_synthetic_private_run(first_dir, first)
    _write_synthetic_private_run(second_dir, second)
    with pytest.raises(xai_preflight.PreflightError, match="omit required metadata"):
        xai_preflight.compare_determinism(first_dir, second_dir)

    first = _synthetic_private_artifacts()
    second = _synthetic_private_artifacts()
    second["run-manifest.json"]["private_run_path"] = 42
    _write_synthetic_private_run(first_dir, first)
    _write_synthetic_private_run(second_dir, second)
    with pytest.raises(xai_preflight.PreflightError, match="invalid type"):
        xai_preflight.compare_determinism(first_dir, second_dir)


def test_determinism_rejects_symlink_roots_and_artifacts(tmp_path: Path) -> None:
    first_dir = tmp_path / "run-a"
    second_dir = tmp_path / "run-b"
    artifacts = _synthetic_private_artifacts()
    _write_synthetic_private_run(first_dir, artifacts)
    _write_synthetic_private_run(second_dir, artifacts)
    linked_second = tmp_path / "linked-run-b"
    linked_second.symlink_to(second_dir, target_is_directory=True)
    with pytest.raises(xai_preflight.PreflightError, match="symlink"):
        xai_preflight.compare_determinism(first_dir, linked_second)

    target = second_dir / xai_preflight.SUBSTANTIVE_ARTIFACT_NAMES[0]
    outside = tmp_path / "outside-artifact.json"
    outside.write_bytes(target.read_bytes())
    target.unlink()
    target.symlink_to(outside)
    with pytest.raises(xai_preflight.PreflightError, match="non-symlink"):
        xai_preflight.compare_determinism(first_dir, second_dir)


def test_git_head_subprocess_receives_a_credential_free_minimal_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> Any:
        observed["args"] = args
        observed["kwargs"] = kwargs
        return types.SimpleNamespace(stdout="c" * 40 + "\n")

    monkeypatch.setattr(xai_preflight.shutil, "which", lambda name: "/usr/bin/git")
    monkeypatch.setattr(xai_preflight.subprocess, "run", fake_run)

    assert xai_preflight._git_head() == "c" * 40
    assert observed["args"] == (
        ["/usr/bin/git", "rev-parse", "--verify", "HEAD^{commit}"],
    )
    assert observed["kwargs"]["cwd"] == xai_preflight.PROJECT_DIR
    assert observed["kwargs"]["check"] is True
    assert observed["kwargs"]["capture_output"] is True
    assert observed["kwargs"]["text"] is True
    assert observed["kwargs"]["env"] == {
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
    }
    assert set(observed["kwargs"]["env"]).isdisjoint(
        xai_preflight.PROVIDER_KEY_ENV_NAMES
    )


@pytest.mark.parametrize(
    "stdout",
    (
        "c" * 39 + "\n",
        "c" * 40 + "\n" + "d" * 40 + "\n",
        "c" * 40 + " trailing\n",
        "g" * 40 + "\n",
        "\n",
    ),
)
def test_git_head_rejects_ambiguous_or_non_commit_output(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
) -> None:
    monkeypatch.setattr(xai_preflight.shutil, "which", lambda name: "/usr/bin/git")
    monkeypatch.setattr(
        xai_preflight.subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(stdout=stdout),
    )
    with pytest.raises(xai_preflight.PreflightError, match="full commit ID"):
        xai_preflight._git_head()


def test_git_head_normalises_uppercase_full_commit_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(xai_preflight.shutil, "which", lambda name: "/usr/bin/git")
    monkeypatch.setattr(
        xai_preflight.subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(stdout="ABCDEF12" * 5 + "\n"),
    )
    assert xai_preflight._git_head() == "abcdef12" * 5


def test_provider_neutral_inventory_remains_backward_compatible(
    canonical_schema: dict[str, Any],
) -> None:
    del canonical_schema
    inventory = provider_neutral.build_schema_feature_inventory(
        CANONICAL_SCHEMA_PATH
    )

    assert tuple(inventory) == provider_neutral.FEATURE_FIELDS
    assert inventory == {
        "schema_version": "proposition-ledger-semantic-delta-v1.1.0",
        "schema_sha256": xai_preflight.EXPECTED_CANONICAL_SHA256,
        "schema_size_bytes": 47_726,
        "maximum_object_depth": 4,
        "property_count": 249,
        "required_property_count": 190,
        "ref_count": 186,
        "one_of_count": 6,
        "any_of_count": 15,
        "all_of_count": 1,
        "enum_count": 37,
        "const_count": 12,
        "nullable_union_count": 14,
        "additional_properties_false_count": 38,
        "recursive_reference_count": 0,
        "maximum_array_item_nesting": 3,
        "unbounded_array_count": 31,
    }
    assert provider_neutral.schema_feature_inventory_sha256(inventory) == (
        "a64da99c0e2bc3c1b74b5b0f6bb36ed3f594ae12cdf2920b774c36143163d9c5"
    )


def test_preflight_code_and_tracked_manifests_are_synthetic_only_and_nonexecuting() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MODULE_PATH))
    imported_roots: set[str] = set()
    called_attributes: set[str] = set()
    string_literals: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called_attributes.add(node.func.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            string_literals.append(node.value)

    assert "mrsMThatcher2" not in imported_roots
    assert called_attributes.isdisjoint(
        {"sample", "parse", "stream", "responses_create", "models_list"}
    )
    assert "api.x.ai" not in source
    assert "/disks/disk1/etc" not in source
    assert not any(
        value == ".env"
        or value.endswith("/.env")
        or "/.env/" in value
        for value in string_literals
    )
    assert "Synthetic proposition-ledger schema preflight only." in source
    assert "Return the wholly synthetic structured result." in source
    assert "provider_calls_made\": 0" in source

    manifest_text = PROFILES_PATH.read_text(encoding="utf-8") + RULES_PATH.read_text(
        encoding="utf-8"
    )
    assert "api.x.ai" not in manifest_text
    assert "/disks/disk1/etc" not in manifest_text
    assert ".env" not in manifest_text


def test_real_pinned_sdk_local_compilation_when_explicitly_enabled(
    canonical_schema: dict[str, Any],
) -> None:
    if os.environ.get(REAL_SDK_TEST_ENV) != "1":
        assert REAL_SDK_TEST_ENV == "MRS_XAI_PINNED_SDK_TEST"
        assert xai_preflight.EXPECTED_XAI_SDK_VERSION == "1.19.0"
        assert callable(xai_preflight.compile_sdk_profiles)
        return

    assert importlib.metadata.version("xai-sdk") == "1.19.0"
    provider_schema, _ = xai_preflight.transform_provider_schema(canonical_schema)
    results, network, differ_only_by_model = xai_preflight.compile_sdk_profiles(
        _canonical_profiles(),
        provider_schema,
    )

    assert len(results) == 2
    assert all(
        result["request_construction_status"] == "succeeded_without_transport"
        for result in results
    )
    assert all(result["reasoning_effort"] == "low" for result in results)
    assert all(result["tool_count"] == 0 for result in results)
    assert all(result["sdk_emitted_schema_equal"] is True for result in results)
    assert differ_only_by_model is True
    assert network["deliberate_connection_attempt_blocked"] is True
    assert network["provider_calls_made"] == 0
