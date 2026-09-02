from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from tools import proposition_ledger_evidence_transport as evidence
from tools import proposition_ledger_xai_provider_preflight as established


PROJECT_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_DIR / "tools/proposition_ledger_evidence_transport.py"
GENERATOR_PATH = (
    PROJECT_DIR / "tools/generate_proposition_ledger_phase2b_transport_schema.py"
)
CONTRACT_MANIFEST_PATH = (
    PROJECT_DIR
    / "proposition_ledger_research/phase2b/transport-contract-manifest.json"
)


@pytest.fixture(scope="module")
def schemas() -> tuple[dict[str, Any], dict[str, Any]]:
    canonical = json.loads(evidence.DEFAULT_CANONICAL_SCHEMA_PATH.read_text("utf-8"))
    transport = json.loads(evidence.DEFAULT_TRANSPORT_SCHEMA_PATH.read_text("utf-8"))
    return canonical, transport


def _empty_delta(*, turn_id: str = "synthetic-turn-0") -> dict[str, Any]:
    return {
        "schema_version": evidence.TRANSPORT_SCHEMA_VERSION,
        "canonical_schema_version": evidence.CANONICAL_SCHEMA_VERSION,
        "conversation_key": "synthetic:transport-test",
        "target_turn_id": turn_id,
        "as_of_turn_index": 0,
        "prior_ledger_reference": None,
        "new_propositions": [],
        "proposition_updates": [],
        "new_proposition_groups": [],
        "proposition_group_updates": [],
        "new_issue_states": [],
        "issue_state_updates": [],
        "commitment_changes": [],
        "obligation_changes": [],
        "new_relations": [],
        "answer_target_changes": [],
        "rejected_answer_target_changes": [],
        "repair_records": [],
        "resolved_items": [],
        "extraction_status": "complete",
        "abstentions": [],
        "unsupported_inferences_rejected": 0,
        "warnings": [],
    }


def _minimal_record(
    transport_schema: Mapping[str, Any],
    field: str,
    selectors: list[dict[str, Any]],
) -> dict[str, Any]:
    item_schema = transport_schema["properties"][field]["items"]
    record = established.minimal_instance(transport_schema, item_schema)
    assert isinstance(record, dict)
    record["exact_evidence_spans"] = copy.deepcopy(selectors)
    return record


def _with_evidence(
    transport_schema: Mapping[str, Any],
    *,
    selectors: list[dict[str, Any]],
    field: str = "new_propositions",
    turn_id: str = "synthetic-turn-0",
) -> dict[str, Any]:
    delta = _empty_delta(turn_id=turn_id)
    delta[field] = [_minimal_record(transport_schema, field, selectors)]
    return delta


def _relation_delta(
    transport_schema: Mapping[str, Any],
    *,
    asserted_or_analysed_by: str | None,
    provenance_kind: str,
    analysis_basis: str,
) -> dict[str, Any]:
    delta = _empty_delta()
    relation = _minimal_record(
        transport_schema,
        "new_relations",
        [{"exact_text": "evidence", "occurrence_index": 0}],
    )
    relation.update(
        asserted_or_analysed_by=asserted_or_analysed_by,
        provenance_kind=provenance_kind,
        analysis_basis=analysis_basis,
    )
    delta["new_relations"] = [relation]
    return delta


def _resolve(
    delta: Mapping[str, Any],
    text: str,
    schemas: tuple[dict[str, Any], dict[str, Any]],
    *,
    turn_id: str = "synthetic-turn-0",
) -> evidence.EvidenceResolutionResult:
    canonical, transport = schemas
    return evidence.resolve_transport_delta(
        delta,
        current_turn_id=turn_id,
        current_turn_text=text,
        transport_schema=transport,
        canonical_schema=canonical,
    )


def _only_span(result: evidence.EvidenceResolutionResult) -> dict[str, Any]:
    assert result.succeeded, result.errors
    assert result.canonical_delta is not None
    return result.canonical_delta["new_propositions"][0][
        "exact_evidence_spans"
    ][0]


def test_canonical_schema_hash_and_version_are_frozen(
    schemas: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    canonical, _ = schemas
    source = evidence.DEFAULT_CANONICAL_SCHEMA_PATH.read_bytes()
    assert (
        evidence.CANONICAL_SCHEMA_VERSION
        == "proposition-ledger-semantic-delta-v1.1.2"
    )
    assert (
        evidence.EVIDENCE_RESOLVER_VERSION
        == "proposition-ledger-evidence-transport-v1.0.1"
    )
    assert hashlib.sha256(source).hexdigest() == evidence.CANONICAL_SCHEMA_FILE_SHA256
    assert (
        canonical["properties"]["schema_version"]["const"]
        == evidence.CANONICAL_SCHEMA_VERSION
    )


def test_transport_schema_is_exact_deterministic_derivation(
    schemas: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    canonical, tracked = schemas
    assert (
        evidence.TRANSPORT_SCHEMA_VERSION
        == "proposition-ledger-xai-transport-delta-v2.0.2"
    )
    first, first_ledger = evidence.derive_transport_schema(canonical)
    second, second_ledger = evidence.derive_transport_schema(canonical)
    assert first == second == tracked
    assert first_ledger == second_ledger
    assert evidence.generated_schema_bytes(first) == (
        evidence.DEFAULT_TRANSPORT_SCHEMA_PATH.read_bytes()
    )
    audit = evidence.build_transport_schema_equivalence_audit(canonical, tracked)
    assert audit["status"] == "passed"
    assert audit["all_non_boundary_validation_structure_unchanged"] is True
    selector = tracked["$defs"]["evidenceSpan"]
    assert selector["additionalProperties"] is False
    assert selector["required"] == ["exact_text", "occurrence_index"]
    assert set(selector["properties"]) == {"exact_text", "occurrence_index"}
    serialized_selector = evidence.canonical_json_bytes(selector)
    for forbidden in (b'"turn_id"', b'"start_char"', b'"end_char"'):
        assert forbidden not in serialized_selector


def test_tracked_transport_contract_manifest_matches_generated_contract(
    schemas: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _, transport_schema = schemas
    provider_schema, _ = established.transform_provider_schema(transport_schema)
    expected = evidence.build_response_contract_manifest(
        transport_schema=transport_schema,
        xai_provider_schema=provider_schema,
    )
    tracked = json.loads(CONTRACT_MANIFEST_PATH.read_text("utf-8"))

    assert tracked == expected


@pytest.mark.parametrize(
    (
        "asserted_or_analysed_by",
        "provenance_kind",
        "analysis_basis",
        "valid",
    ),
    [
        (None, "machine_diagnostic", "evaluator_diagnosis", True),
        (None, "human_annotation", "evaluator_diagnosis", True),
        ("account", "transcript_extraction", "direct_semantic_content", True),
        (
            "account",
            "transcript_extraction",
            "speaker_explicit_metadiscourse",
            True,
        ),
        ("account", "human_correction", "human_correction", True),
        ("account", "human_annotation", "direct_semantic_content", True),
        ("account", "machine_diagnostic", "evaluator_diagnosis", False),
        ("account", "human_annotation", "evaluator_diagnosis", False),
        (None, "transcript_extraction", "direct_semantic_content", False),
        (None, "transcript_extraction", "speaker_explicit_metadiscourse", False),
        (None, "human_correction", "human_correction", False),
        (None, "human_annotation", "direct_semantic_content", False),
    ],
)
def test_evaluator_relation_attribution_contract_is_preserved_by_transport(
    schemas: tuple[dict[str, Any], dict[str, Any]],
    asserted_or_analysed_by: str | None,
    provenance_kind: str,
    analysis_basis: str,
    valid: bool,
) -> None:
    canonical_schema, transport_schema = schemas
    delta = _relation_delta(
        transport_schema,
        asserted_or_analysed_by=asserted_or_analysed_by,
        provenance_kind=provenance_kind,
        analysis_basis=analysis_basis,
    )
    transport_errors = evidence.intended_validation_errors(transport_schema, delta)
    result = _resolve(delta, "quoted evidence", schemas)

    assert bool(transport_errors) is not valid
    if valid:
        assert result.succeeded, result.errors
        assert result.canonical_delta is not None
        assert not evidence.intended_validation_errors(
            canonical_schema, result.canonical_delta
        )
        assert (
            result.canonical_delta["new_relations"][0][
                "asserted_or_analysed_by"
            ]
            == asserted_or_analysed_by
        )
    else:
        assert result.status == "transport_schema_invalid"
        assert result.canonical_delta is None


def test_schema_generator_check_and_verify_only_are_read_only() -> None:
    before = evidence.DEFAULT_TRANSPORT_SCHEMA_PATH.read_bytes()
    for flag in ("--check", "--verify-only"):
        completed = subprocess.run(
            [sys.executable, str(MODULE_PATH), flag],
            cwd=PROJECT_DIR,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        assert evidence.DEFAULT_TRANSPORT_SCHEMA_PATH.read_bytes() == before


def test_established_xai_transform_is_deterministic_and_bounded(
    schemas: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _, transport_schema = schemas
    first, first_ledger = established.transform_provider_schema(transport_schema)
    second, second_ledger = established.transform_provider_schema(transport_schema)
    assert evidence.canonical_json_bytes(first) == evidence.canonical_json_bytes(second)
    assert first_ledger == second_ledger
    assert len(first_ledger) == 26
    assert sum(
        row["transformation_kind"]
        == "insert_explicit_additional_properties_true"
        for row in first_ledger
    ) == 12
    assert sum(
        row["transformation_kind"]
        == "remove_redundant_outer_anchors_for_xai_full_string_pattern"
        for row in first_ledger
    ) == 14
    selector = first["$defs"]["evidenceSpan"]
    assert selector == transport_schema["$defs"]["evidenceSpan"]


@pytest.mark.parametrize(
    ("case_id", "turn_text", "exact_text", "occurrence_index", "expected"),
    [
        ("unique_ascii", "alpha beta", "beta", 0, (6, 10)),
        ("whole_turn", "whole turn", "whole turn", 0, (0, 10)),
        ("repeated_zero", "go go", "go", 0, (0, 2)),
        ("repeated_one", "go go", "go", 1, (3, 5)),
        ("overlap_zero", "aaa", "aa", 0, (0, 2)),
        ("overlap_one", "aaa", "aa", 1, (1, 3)),
        ("outside_bmp_before", "😀 evidence", "evidence", 0, (2, 10)),
        ("outside_bmp_inside", "say 😀 now", "😀", 0, (4, 5)),
        ("combining", "Cafe\u0301 noir", "e\u0301", 0, (3, 5)),
        ("curly_quotes", "She said “yes”.", "“yes”", 0, (9, 14)),
        ("crlf", "first\r\nsecond", "\r\n", 0, (5, 7)),
        ("edge_whitespace", "  exact  ", "  exact  ", 0, (0, 9)),
        ("punctuation", "Wait... wait...", "...", 1, (12, 15)),
        ("multiline", "one\ntwo\nthree", "two\nthree", 0, (4, 13)),
    ],
)
def test_literal_codepoint_resolution_corpus(
    case_id: str,
    turn_text: str,
    exact_text: str,
    occurrence_index: int,
    expected: tuple[int, int],
    schemas: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    del case_id
    _, transport_schema = schemas
    delta = _with_evidence(
        transport_schema,
        selectors=[
            {"exact_text": exact_text, "occurrence_index": occurrence_index}
        ],
    )
    span = _only_span(_resolve(delta, turn_text, schemas))
    assert (span["start_char"], span["end_char"]) == expected
    assert span["turn_id"] == "synthetic-turn-0"
    assert turn_text[span["start_char"] : span["end_char"]] == exact_text


def test_overlapping_search_does_not_use_nonoverlapping_count() -> None:
    assert evidence.find_overlapping_occurrences("aaa", "aa") == ((0, 2), (1, 3))
    assert "aaa".count("aa") == 1


def test_occurrence_out_of_range_fails_closed(
    schemas: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _, transport_schema = schemas
    delta = _with_evidence(
        transport_schema,
        selectors=[{"exact_text": "aa", "occurrence_index": 2}],
    )
    result = _resolve(delta, "aaa", schemas)
    assert result.status == "evidence_occurrence_index_out_of_range"
    assert result.canonical_delta is None
    assert result.resolution_manifest is None


def test_absent_exact_text_fails_closed(
    schemas: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _, transport_schema = schemas
    delta = _with_evidence(
        transport_schema,
        selectors=[{"exact_text": "absent", "occurrence_index": 0}],
    )
    result = _resolve(delta, "present", schemas)
    assert result.status == "evidence_exact_text_not_found"
    assert result.canonical_delta is None


def test_empty_exact_text_is_transport_schema_invalid(
    schemas: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _, transport_schema = schemas
    delta = _with_evidence(
        transport_schema,
        selectors=[{"exact_text": "", "occurrence_index": 0}],
    )
    assert _resolve(delta, "anything", schemas).status == "transport_schema_invalid"


def test_no_unicode_normalisation_precomposed_vs_decomposed(
    schemas: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _, transport_schema = schemas
    delta = _with_evidence(
        transport_schema,
        selectors=[{"exact_text": "é", "occurrence_index": 0}],
    )
    result = _resolve(delta, "e\u0301", schemas)
    assert result.status == "evidence_exact_text_not_found"


def test_two_different_selectors_in_one_proposition(
    schemas: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _, transport_schema = schemas
    delta = _with_evidence(
        transport_schema,
        selectors=[
            {"exact_text": "alpha", "occurrence_index": 0},
            {"exact_text": "beta", "occurrence_index": 0},
        ],
    )
    result = _resolve(delta, "alpha beta", schemas)
    assert result.succeeded, result.errors
    assert result.resolution_manifest is not None
    assert result.resolution_manifest["selector_count"] == 2
    spans = result.canonical_delta["new_propositions"][0]["exact_evidence_spans"]
    assert [(span["start_char"], span["end_char"]) for span in spans] == [
        (0, 5),
        (6, 10),
    ]


def test_identical_resolved_selectors_have_typed_duplicate_failure(
    schemas: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _, transport_schema = schemas
    selector = {"exact_text": "same", "occurrence_index": 0}
    delta = _with_evidence(
        transport_schema,
        selectors=[copy.deepcopy(selector), copy.deepcopy(selector)],
    )
    result = _resolve(delta, "same", schemas)
    assert result.status == "evidence_selector_duplicate"
    assert result.canonical_delta is None


@pytest.mark.parametrize(
    "field",
    [
        "new_propositions",
        "proposition_updates",
        "new_proposition_groups",
        "proposition_group_updates",
        "new_issue_states",
        "issue_state_updates",
        "commitment_changes",
        "obligation_changes",
        "new_relations",
        "answer_target_changes",
        "rejected_answer_target_changes",
        "repair_records",
        "resolved_items",
        "warnings",
    ],
)
def test_schema_guided_resolution_at_every_authorised_runtime_path(
    field: str,
    schemas: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _, transport_schema = schemas
    delta = _with_evidence(
        transport_schema,
        field=field,
        selectors=[{"exact_text": "x", "occurrence_index": 0}],
    )
    result = _resolve(delta, "x", schemas)
    assert result.succeeded, (field, result.errors)
    assert result.resolution_manifest is not None
    pointer = result.resolution_manifest["selectors"][0]["selector_json_pointer"]
    assert pointer.startswith(f"/{field}/0/exact_evidence_spans/0")


def test_several_nested_semantic_records_resolve_together(
    schemas: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _, transport_schema = schemas
    delta = _empty_delta()
    fields = (
        "new_propositions",
        "proposition_updates",
        "new_issue_states",
        "issue_state_updates",
        "commitment_changes",
        "new_relations",
        "warnings",
    )
    for index, field in enumerate(fields):
        text = f"evidence-{index}"
        delta[field] = [
            _minimal_record(
                transport_schema,
                field,
                [{"exact_text": text, "occurrence_index": 0}],
            )
        ]
    turn_text = " | ".join(f"evidence-{index}" for index in range(len(fields)))
    result = _resolve(delta, turn_text, schemas)
    assert result.succeeded, result.errors
    assert result.resolution_manifest["selector_count"] == len(fields)


@pytest.mark.parametrize("forbidden", ["turn_id", "start_char", "end_char"])
def test_provider_supplied_canonical_evidence_fields_are_rejected(
    forbidden: str,
    schemas: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _, transport_schema = schemas
    selector: dict[str, Any] = {"exact_text": "x", "occurrence_index": 0}
    selector[forbidden] = "turn-x" if forbidden == "turn_id" else 0
    delta = _with_evidence(transport_schema, selectors=[selector])
    assert _resolve(delta, "x", schemas).status == "transport_schema_invalid"


def test_selector_at_unauthorised_object_location_is_rejected(
    schemas: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _, transport_schema = schemas
    delta = _empty_delta()
    delta["response_contract_manifest"] = {
        "exact_text": "x",
        "occurrence_index": 0,
    }
    result = _resolve(delta, "x", schemas)
    assert result.status in {
        "transport_schema_invalid",
        "evidence_selector_unauthorised",
    }
    assert result.canonical_delta is None


def test_every_non_evidence_semantic_field_is_preserved_exactly(
    schemas: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _, transport_schema = schemas
    delta = _with_evidence(
        transport_schema,
        selectors=[{"exact_text": "evidence", "occurrence_index": 0}],
    )
    before_record = copy.deepcopy(delta["new_propositions"][0])
    before_record.pop("exact_evidence_spans")
    result = _resolve(delta, "quoted evidence", schemas)
    assert result.succeeded, result.errors
    after_record = copy.deepcopy(result.canonical_delta["new_propositions"][0])
    after_record.pop("exact_evidence_spans")
    assert after_record == before_record
    for field, value in delta.items():
        if field not in {"schema_version", "canonical_schema_version", "new_propositions"}:
            assert result.canonical_delta[field] == value


def test_versions_are_verified_and_only_code_rewrites_to_canonical(
    schemas: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _, transport_schema = schemas
    valid = _empty_delta()
    result = _resolve(valid, "", schemas)
    assert result.succeeded
    assert "canonical_schema_version" not in result.canonical_delta
    assert result.canonical_delta["schema_version"] == evidence.CANONICAL_SCHEMA_VERSION

    wrong_transport = copy.deepcopy(valid)
    wrong_transport["schema_version"] = evidence.CANONICAL_SCHEMA_VERSION
    assert _resolve(wrong_transport, "", schemas).status == "transport_schema_invalid"
    wrong_target = copy.deepcopy(valid)
    wrong_target["canonical_schema_version"] = evidence.TRANSPORT_SCHEMA_VERSION
    assert _resolve(wrong_target, "", schemas).status == "transport_schema_invalid"


def test_current_turn_binding_is_deterministic_code_owned(
    schemas: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    delta = _empty_delta(turn_id="wrong-turn")
    result = _resolve(delta, "x", schemas, turn_id="actual-turn")
    assert result.status == "transport_binding_invalid"


def _reverse_mapping_order(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _reverse_mapping_order(value[key])
            for key in reversed(tuple(value.keys()))
        }
    if isinstance(value, list):
        return [_reverse_mapping_order(item) for item in value]
    return copy.deepcopy(value)


def test_order_independent_resolution_and_stable_hashes(
    schemas: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _, transport_schema = schemas
    delta = _with_evidence(
        transport_schema,
        selectors=[
            {"exact_text": "alpha", "occurrence_index": 0},
            {"exact_text": "beta", "occurrence_index": 0},
        ],
    )
    first = _resolve(delta, "alpha beta", schemas)
    second = _resolve(_reverse_mapping_order(delta), "alpha beta", schemas)
    assert first.succeeded and second.succeeded
    assert evidence.canonical_json_bytes(first.canonical_delta) == (
        evidence.canonical_json_bytes(second.canonical_delta)
    )
    assert first.resolution_manifest == second.resolution_manifest


def _response_manifest(
    transport_schema: Mapping[str, Any], provider_schema: Mapping[str, Any]
) -> dict[str, Any]:
    return evidence.build_response_contract_manifest(
        transport_schema=transport_schema,
        xai_provider_schema=provider_schema,
    )


def _user_payload(
    manifest: Mapping[str, Any], *, turn_index: int = 0
) -> dict[str, Any]:
    return evidence.build_phase2b_user_payload(
        protocol_version="synthetic-protocol-v1",
        protocol_hash="a" * 64,
        conversation_key="synthetic:request",
        current_turn_id=f"synthetic-turn-{turn_index}",
        turn_index=turn_index,
        parent_turn_id=None if turn_index == 0 else f"synthetic-turn-{turn_index - 1}",
        current_turn_text="Synthetic current text.",
        speaker_descriptor={"participant_id": "synthetic-speaker"},
        prior_ledger=None,
        response_contract_manifest=manifest,
    )


def test_request_payload_contains_manifest_but_not_complete_schema(
    schemas: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _, transport_schema = schemas
    provider_schema, _ = established.transform_provider_schema(transport_schema)
    manifest = _response_manifest(transport_schema, provider_schema)
    payload = _user_payload(manifest)
    assert payload["response_contract_manifest"] == manifest
    assert "provider_response_schema" not in payload
    user_bytes = evidence.canonical_json_bytes(payload)
    assert evidence.canonical_json_bytes(transport_schema) not in user_bytes
    assert evidence.canonical_json_bytes(provider_schema) not in user_bytes
    assert set(manifest) == {
        "transport_schema_version",
        "transport_schema_sha256",
        "xai_provider_schema_sha256",
        "canonical_semantic_schema_version",
        "canonical_semantic_schema_sha256",
        "evidence_selector_contract_version",
    }


def test_two_local_request_representations_differ_only_by_model(
    schemas: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _, transport_schema = schemas
    provider_schema, _ = established.transform_provider_schema(transport_schema)
    payload = _user_payload(_response_manifest(transport_schema, provider_schema))
    first, second = evidence.build_retained_profile_request_representations(
        user_payload=payload,
        system_prompt="Synthetic system prompt.",
        xai_provider_schema=provider_schema,
    )
    assert first["model"] == "grok-4.3"
    assert second["model"] == "grok-4.6"
    first_without_model = copy.deepcopy(first)
    second_without_model = copy.deepcopy(second)
    first_without_model.pop("model")
    second_without_model.pop("model")
    assert first_without_model == second_without_model
    for request in (first, second):
        assert "tool_choice" not in request
        assert request["tool_choice_parameter_sent"] is False
        assert request["tools"] == []
        assert request["max_tokens"] == 4096
        assert request["reasoning_effort"] == "low"
        assert request["store_messages"] is False
        assert request["fallback_model"] is None
        assert request["application_retry_count"] == 0
        assert request["response_format"]["schema"] == provider_schema
        assert evidence.canonical_json_bytes(provider_schema) not in request[
            "messages"
        ][1]["content"].encode("utf-8")


def test_pure_module_has_no_provider_http_grpc_or_production_imports() -> None:
    tree = ast.parse(MODULE_PATH.read_text("utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    forbidden = {
        "http",
        "http.client",
        "grpc",
        "xai_sdk",
        "requests",
        "urllib",
        "urllib3",
        "mrsMThatcher2",
        "tools.proposition_ledger_xai_provider_preflight",
    }
    assert imports.isdisjoint(forbidden)
    source = MODULE_PATH.read_text("utf-8")
    assert "XAI_API_KEY" not in source
    assert ".env" not in source


def test_import_help_and_validation_make_no_network_calls(
    monkeypatch: pytest.MonkeyPatch,
    schemas: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    attempts: list[str] = []

    def denied(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        attempts.append("network")
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    spec = importlib.util.spec_from_file_location(
        "_synthetic_phase2b_transport_import", MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    assert _resolve(_empty_delta(), "", schemas).succeeded
    assert attempts == []

    completed = subprocess.run(
        [sys.executable, str(MODULE_PATH), "--help"],
        cwd=PROJECT_DIR,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(PROJECT_DIR),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert "--verify-only" in completed.stdout


def test_generator_help_does_not_import_provider_sdk() -> None:
    completed = subprocess.run(
        [sys.executable, str(GENERATOR_PATH), "--help"],
        cwd=PROJECT_DIR,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(PROJECT_DIR),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--local-sdk-check" in completed.stdout
