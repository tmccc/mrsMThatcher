from __future__ import annotations

import ast
import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tools import build_proposition_ledger_phase1 as phase1
from tools import proposition_ledger_semantic_delta as semantic


PROJECT_DIR = Path(__file__).resolve().parents[1]
FIXTURE_DIR = (
    PROJECT_DIR
    / "proposition_ledger_research/phase1/synthetic-fixtures/01-direct-question-answer"
)
SEMANTIC_SCHEMA_PATH = (
    PROJECT_DIR
    / "proposition_ledger_research/schema/proposition-ledger-semantic-delta-v1.schema.json"
)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


@pytest.fixture()
def direct_answer_case() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    transcript = _load_json(FIXTURE_DIR / "transcript.json")
    prior = copy.deepcopy(transcript["ledger_history"][-1])
    current_turn = copy.deepcopy(transcript["turns"][-1])
    evidence = {
        "turn_id": current_turn["turn_id"],
        "start_char": 0,
        "end_char": len(current_turn["text"]),
        "exact_text": current_turn["text"],
    }
    delta = {
        "schema_version": semantic.SEMANTIC_DELTA_SCHEMA_VERSION,
        "conversation_key": prior["conversation_key"],
        "target_turn_id": current_turn["turn_id"],
        "as_of_turn_index": current_turn["turn_index"],
        "prior_ledger_reference": {
            "ledger_id": prior["ledger_id"],
            "as_of_turn_index": prior["as_of_turn_index"],
        },
        "new_propositions": [
            {
                "local_ref": "new-proposition-1",
                "canonical_text": "The village bridge is open today.",
                "speaker_or_attributor": {
                    "kind": "speaker",
                    "participant_id": "account",
                    "attributed_participant_id": None,
                },
                "exact_evidence_spans": [copy.deepcopy(evidence)],
                "original_language": "en",
                "speech_act": "assertion",
                "proposition_kind": "descriptive",
                "polarity": "positive",
                "modality": {"type": "none", "strength": "none"},
                "quantification": {"type": "none", "surface_marker": None},
                "temporal_scope": {
                    "type": "present",
                    "start": None,
                    "end": None,
                    "surface_marker": "today",
                },
                "epistemic_status": "asserted",
                "commitment_status": "speaker_committed",
                "lifecycle_status": "live",
                "proposition_group_ref": None,
                "derivation": {
                    "kind": "direct_span",
                    "source_proposition_refs": [],
                    "normalisation_note": None,
                },
                "confidence": 1.0,
                "uncertainty_reason": None,
            }
        ],
        "proposition_updates": [
            {
                "proposition_id": "p-bridge-question",
                "changes": {"lifecycle_status": "resolved"},
                "reason": "The current turn supplies a direct answer.",
                "exact_evidence_spans": [copy.deepcopy(evidence)],
            }
        ],
        "new_proposition_groups": [],
        "proposition_group_updates": [],
        "new_issue_states": [],
        "issue_state_updates": [
            {
                "issue_id": "i-bridge-open",
                "changes": {
                    "status": "answered",
                    "resolution_type": "direct_answer",
                },
                "reason": "The current turn directly answers the issue.",
                "exact_evidence_spans": [copy.deepcopy(evidence)],
            }
        ],
        "commitment_changes": [
            {
                "operation": "add",
                "local_ref": "new-commitment-1",
                "participant_id": "account",
                "proposition_ref": "new-proposition-1",
                "stance": "asserted",
                "basis": "explicit_speech_act",
                "confidence": 1.0,
                "uncertainty_reason": None,
                "exact_evidence_spans": [copy.deepcopy(evidence)],
            }
        ],
        "obligation_changes": [
            {
                "operation": "update",
                "obligation_id": "o-answer-bridge",
                "changes": {"status": "satisfied"},
                "reason": "The explicit question was answered.",
                "exact_evidence_spans": [copy.deepcopy(evidence)],
            }
        ],
        "new_relations": [
            {
                "local_ref": "new-relation-1",
                "source_proposition_refs": ["new-proposition-1"],
                "target_proposition_refs": ["p-bridge-question"],
                "relation_type": "answers",
                "asserted_or_analysed_by": "account",
                "exact_evidence_spans": [copy.deepcopy(evidence)],
                "confidence": 1.0,
                "uncertainty_reason": None,
                "provenance_kind": "transcript_extraction",
                "analysis_basis": "direct_semantic_content",
            }
        ],
        "answer_target_changes": [
            {
                "operation": "add",
                "local_ref": "new-answer-target-1",
                "issue_refs": ["i-bridge-open"],
                "proposition_refs": ["p-bridge-question"],
                "target_status": "confirmed",
                "confidence": 1.0,
                "exact_evidence_spans": [copy.deepcopy(evidence)],
            }
        ],
        "rejected_answer_target_changes": [],
        "repair_records": [],
        "resolved_items": [
            {
                "item_type": "proposition",
                "item_ref": "p-bridge-question",
                "resolution_type": "direct_answer",
                "exact_evidence_spans": [copy.deepcopy(evidence)],
            },
            {
                "item_type": "issue",
                "item_ref": "i-bridge-open",
                "resolution_type": "direct_answer",
                "exact_evidence_spans": [copy.deepcopy(evidence)],
            },
            {
                "item_type": "obligation",
                "item_ref": "o-answer-bridge",
                "resolution_type": "direct_answer",
                "exact_evidence_spans": [copy.deepcopy(evidence)],
            },
        ],
        "extraction_status": "complete",
        "abstentions": [],
        "unsupported_inferences_rejected": 0,
        "warnings": [],
    }
    return prior, current_turn, delta


def _materialise(
    case: tuple[dict[str, Any], dict[str, Any], dict[str, Any]]
) -> semantic.MaterialisationResult:
    prior, current_turn, delta = case
    participant = next(
        copy.deepcopy(record)
        for record in prior["participants"]
        if record["participant_id"] == current_turn["speaker_id"]
    )
    return semantic.materialise_semantic_delta(
        prior,
        current_turn,
        delta,
        current_participant=participant,
    )


def _materialise_existing(
    prior: dict[str, Any],
    current_turn: dict[str, Any],
    delta: dict[str, Any],
) -> semantic.MaterialisationResult:
    participant = next(
        copy.deepcopy(record)
        for record in prior["participants"]
        if record["participant_id"] == current_turn["speaker_id"]
    )
    return semantic.materialise_semantic_delta(
        prior,
        current_turn,
        delta,
        current_participant=participant,
    )


def _synthetic_participant(participant_id: str, role: str) -> dict[str, Any]:
    return {
        "author_key": f"synthetic-author-{participant_id}",
        "identity_confidence": 1.0,
        "participant_id": participant_id,
        "role": role,
    }


def _semantic_noop(
    conversation_key: str,
    turn: dict[str, Any],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "abstentions": [],
        "answer_target_changes": [],
        "as_of_turn_index": turn["turn_index"],
        "commitment_changes": [],
        "conversation_key": conversation_key,
        "extraction_status": "complete",
        "issue_state_updates": [],
        "new_issue_states": [],
        "new_proposition_groups": [],
        "new_propositions": [],
        "new_relations": [],
        "obligation_changes": [],
        "prior_ledger_reference": (
            {
                "as_of_turn_index": previous["as_of_turn_index"],
                "ledger_id": previous["ledger_id"],
            }
            if previous is not None
            else None
        ),
        "proposition_group_updates": [],
        "proposition_updates": [],
        "rejected_answer_target_changes": [],
        "repair_records": [],
        "resolved_items": [],
        "schema_version": semantic.SEMANTIC_DELTA_SCHEMA_VERSION,
        "target_turn_id": turn["turn_id"],
        "unsupported_inferences_rejected": 0,
        "warnings": [],
    }


def _run_synthetic_chain(
    speakers: list[tuple[str, str]],
) -> tuple[list[dict[str, Any]], list[semantic.MaterialisationResult]]:
    conversation_key = "synthetic:test-incremental-chain"
    descriptors = {
        participant_id: _synthetic_participant(participant_id, role)
        for participant_id, role in speakers
    }
    previous: dict[str, Any] | None = None
    snapshots: list[dict[str, Any]] = []
    results: list[semantic.MaterialisationResult] = []
    for index, (participant_id, _role) in enumerate(speakers):
        turn = {
            "conversation_key": conversation_key,
            "parent_turn_id": f"synthetic-chain-t{index - 1}" if index else None,
            "post_id": f"synthetic-chain-post-{index}",
            "speaker_id": participant_id,
            "text": f"Wholly invented turn {index} by {participant_id}.",
            "turn_id": f"synthetic-chain-t{index}",
            "turn_index": index,
        }
        descriptor = descriptors[participant_id]
        genesis_context = None
        if index == 0:
            genesis_context = {
                "conversation_key": conversation_key,
                "current_participant": copy.deepcopy(descriptor),
                "root_post_id": turn["post_id"],
                "source_completeness": {
                    "account_publication_confirmed": True,
                    "chronology_complete": True,
                    "complete_prefix_through_turn": True,
                    "exact_text_complete": True,
                    "limitations": ["Wholly invented unit-test chain."],
                    "parent_graph_complete": True,
                    "reconstruction_grade": "A",
                },
            }
        result = semantic.materialise_semantic_delta(
            previous,
            turn,
            _semantic_noop(conversation_key, turn, previous),
            current_participant=descriptor,
            genesis_context=genesis_context,
        )
        results.append(result)
        if result.ledger is None:
            break
        snapshots.append(result.ledger)
        previous = result.ledger
    return snapshots, results


@pytest.mark.parametrize(
    ("participant_id", "role"),
    [("contributor", "contributor"), ("account", "account")],
)
def test_turn_zero_materialises_for_contributor_and_account_roots(
    participant_id: str,
    role: str,
) -> None:
    snapshots, results = _run_synthetic_chain([(participant_id, role)])

    assert [result.status for result in results] == ["ok"]
    assert len(snapshots) == 1
    genesis = snapshots[0]
    assert genesis["as_of_turn_index"] == 0
    assert genesis["previous_ledger_sha256"] is None
    assert genesis["state_transitions"][0]["from_ledger_sha256"] is None
    assert genesis["participants"] == [
        _synthetic_participant(participant_id, role)
    ]
    assert genesis["turn_refs"][0]["parent_turn_id"] is None
    assert genesis["ledger_sha256"] == phase1.ledger_sha256(genesis)


def test_first_seen_contributor_is_registered_after_account_root() -> None:
    snapshots, results = _run_synthetic_chain(
        [("account", "account"), ("contributor", "contributor")]
    )

    assert [result.status for result in results] == ["ok", "ok"]
    assert [record["participant_id"] for record in snapshots[0]["participants"]] == [
        "account"
    ]
    assert [record["participant_id"] for record in snapshots[1]["participants"]] == [
        "account",
        "contributor",
    ]
    participant_patch = [
        operation
        for operation in snapshots[1]["state_transitions"][0]["state_patch"]
        if operation["collection"] == "participants"
    ]
    assert participant_patch == [
        {
            "after_record": _synthetic_participant("contributor", "contributor"),
            "before_record_sha256": None,
            "collection": "participants",
            "item_id": "contributor",
            "operation": "add",
        }
    ]


def test_third_participant_is_registered_only_at_first_appearance() -> None:
    snapshots, results = _run_synthetic_chain(
        [
            ("account", "account"),
            ("contributor", "contributor"),
            ("third", "unknown"),
        ]
    )

    assert [result.status for result in results] == ["ok", "ok", "ok"]
    assert [
        [record["participant_id"] for record in snapshot["participants"]]
        for snapshot in snapshots
    ] == [["account"], ["account", "contributor"], ["account", "contributor", "third"]]


def test_complete_three_and_four_turn_chains_preserve_seen_participants_only() -> None:
    three_snapshots, three_results = _run_synthetic_chain(
        [
            ("account", "account"),
            ("contributor", "contributor"),
            ("account", "account"),
        ]
    )
    four_snapshots, four_results = _run_synthetic_chain(
        [
            ("account", "account"),
            ("contributor", "contributor"),
            ("account", "account"),
            ("contributor", "contributor"),
        ]
    )

    assert [result.status for result in three_results] == ["ok"] * 3
    assert [result.status for result in four_results] == ["ok"] * 4
    assert [
        [record["participant_id"] for record in snapshot["participants"]]
        for snapshot in four_snapshots
    ] == [
        ["account"],
        ["account", "contributor"],
        ["account", "contributor"],
        ["account", "contributor"],
    ]
    assert all(
        len(snapshot["turn_refs"]) == index + 1
        for index, snapshot in enumerate(three_snapshots)
    )
    assert not any(
        operation["collection"] == "participants"
        for operation in four_snapshots[3]["state_transitions"][0]["state_patch"]
    )


def test_genesis_rejects_future_participant_preseeding() -> None:
    participant = _synthetic_participant("account", "account")
    turn = {
        "conversation_key": "synthetic:future-preseed",
        "parent_turn_id": None,
        "post_id": "synthetic-future-post-0",
        "speaker_id": "account",
        "text": "Wholly invented root.",
        "turn_id": "synthetic-future-t0",
        "turn_index": 0,
    }
    context = {
        "conversation_key": turn["conversation_key"],
        "current_participant": participant,
        "participants": [
            participant,
            _synthetic_participant("future", "contributor"),
        ],
        "root_post_id": turn["post_id"],
        "source_completeness": {
            "account_publication_confirmed": True,
            "chronology_complete": True,
            "complete_prefix_through_turn": True,
            "exact_text_complete": True,
            "limitations": ["Wholly invented unit-test root."],
            "parent_graph_complete": True,
            "reconstruction_grade": "A",
        },
    }

    result = semantic.materialise_semantic_delta(
        None,
        turn,
        _semantic_noop(turn["conversation_key"], turn, None),
        current_participant=participant,
        genesis_context=context,
    )

    assert result.status == "semantic_reference_invalid"
    assert "genesis_context_fields_invalid" in result.errors


def test_existing_participant_descriptor_must_agree_exactly() -> None:
    snapshots, results = _run_synthetic_chain([("account", "account")])
    assert results[0].status == "ok"
    prior = snapshots[0]
    turn = {
        "conversation_key": prior["conversation_key"],
        "parent_turn_id": prior["target_turn_id"],
        "post_id": "synthetic-mismatch-post-1",
        "speaker_id": "account",
        "text": "Wholly invented returning turn.",
        "turn_id": "synthetic-mismatch-t1",
        "turn_index": 1,
    }
    mismatch = _synthetic_participant("account", "account")
    mismatch["identity_confidence"] = 0.5

    result = semantic.materialise_semantic_delta(
        prior,
        turn,
        _semantic_noop(prior["conversation_key"], turn, prior),
        current_participant=mismatch,
    )

    assert result.status == "semantic_reference_invalid"
    assert "current_participant_descriptor_mismatch" in result.errors


def test_genesis_ids_and_hashes_are_deterministic() -> None:
    first, first_results = _run_synthetic_chain([("account", "account")])
    second, second_results = _run_synthetic_chain([("account", "account")])

    assert first_results[0].status == second_results[0].status == "ok"
    assert first[0]["participants"] == second[0]["participants"]
    assert first[0]["ledger_id"] == second[0]["ledger_id"]
    assert first[0]["ledger_sha256"] == second[0]["ledger_sha256"]
    assert phase1.canonical_json_bytes(first[0]) == phase1.canonical_json_bytes(
        second[0]
    )


def test_semantic_predecessor_nullability_is_exactly_turn_zero() -> None:
    schema = _load_json(SEMANTIC_SCHEMA_PATH)
    turn_zero = {
        "turn_id": "semantic-null-t0",
        "turn_index": 0,
    }
    genesis_delta = _semantic_noop("synthetic:nullability", turn_zero, None)
    assert phase1._jsonschema_errors(genesis_delta, schema) == []

    invalid_genesis = copy.deepcopy(genesis_delta)
    invalid_genesis["prior_ledger_reference"] = {
        "as_of_turn_index": 0,
        "ledger_id": "ledger-invalid",
    }
    assert phase1._jsonschema_errors(invalid_genesis, schema)

    invalid_later = copy.deepcopy(genesis_delta)
    invalid_later["as_of_turn_index"] = 1
    invalid_later["target_turn_id"] = "semantic-null-t1"
    assert phase1._jsonschema_errors(invalid_later, schema)


def test_arbitrary_unknown_provider_participant_reference_is_rejected(
    direct_answer_case: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    prior, current_turn, delta = direct_answer_case
    delta["new_propositions"][0]["speaker_or_attributor"][
        "participant_id"
    ] = "future-participant"

    result = _materialise_existing(prior, current_turn, delta)

    assert result.status == "semantic_reference_invalid"
    assert any("unknown_participant_reference" in error for error in result.errors)


def test_exact_first_seen_current_speaker_may_be_referenced_semantically(
    direct_answer_case: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    prior, current_turn, delta = direct_answer_case
    current_turn["speaker_id"] = "newcomer"
    newcomer = _synthetic_participant("newcomer", "contributor")
    delta["new_propositions"][0]["speaker_or_attributor"][
        "participant_id"
    ] = "newcomer"
    delta["commitment_changes"][0]["participant_id"] = "newcomer"
    delta["new_relations"][0]["asserted_or_analysed_by"] = "newcomer"

    result = semantic.materialise_semantic_delta(
        prior,
        current_turn,
        delta,
        current_participant=newcomer,
    )

    assert result.status == "ok", result.errors
    assert result.ledger is not None
    assert result.ledger["participants"][-1] == newcomer


@pytest.mark.parametrize("field", ["participants", "participant_id"])
def test_provider_cannot_supply_participant_collection_or_permanent_id(
    direct_answer_case: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
    field: str,
) -> None:
    prior, current_turn, delta = direct_answer_case
    delta[field] = [] if field == "participants" else "provider-participant-id"

    result = _materialise_existing(prior, current_turn, delta)

    assert result.status == "semantic_delta_schema_invalid"


def test_raw_identity_field_cannot_enter_participant_record() -> None:
    snapshots, results = _run_synthetic_chain([("account", "account")])
    assert results[0].status == "ok"
    prior = snapshots[0]
    turn = {
        "conversation_key": prior["conversation_key"],
        "parent_turn_id": prior["target_turn_id"],
        "post_id": "synthetic-raw-field-post-1",
        "speaker_id": "contributor",
        "text": "Wholly invented contributor turn.",
        "turn_id": "synthetic-raw-field-t1",
        "turn_index": 1,
    }
    descriptor = _synthetic_participant("contributor", "contributor")
    descriptor["raw_contributor_id"] = "forbidden-raw-value"

    result = semantic.materialise_semantic_delta(
        prior,
        turn,
        _semantic_noop(prior["conversation_key"], turn, prior),
        current_participant=descriptor,
    )

    assert result.status == "semantic_reference_invalid"
    assert result.ledger is None


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "ledger_sha256",
        "previous_ledger_sha256",
        "state_patch",
        "propositions",
        "issue_states",
        "turn_refs",
        "source_completeness",
    ],
)
def test_semantic_schema_rejects_hashes_patches_and_cumulative_state(
    direct_answer_case: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
    forbidden_field: str,
) -> None:
    prior, current_turn, delta = direct_answer_case
    delta[forbidden_field] = [] if forbidden_field.endswith("s") else "forbidden"

    result = _materialise_existing(prior, current_turn, delta)

    assert result.status == "semantic_delta_schema_invalid"


def test_valid_delta_materialises_to_a_full_valid_ledger(
    direct_answer_case: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    result = _materialise(direct_answer_case)

    assert result.status == "ok", result.errors
    assert result.ledger is not None
    assert result.ledger["schema_version"] == semantic.PERSISTED_LEDGER_SCHEMA_VERSION
    assert result.ledger["ledger_sha256"] == phase1.ledger_sha256(result.ledger)
    assert phase1.validate_ledger_incremental(
        result.ledger,
        direct_answer_case[0],
        direct_answer_case[1],
        _load_json(semantic.DEFAULT_LEDGER_SCHEMA_PATH),
    ) == []


def test_stable_ids_are_deterministic(
    direct_answer_case: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    first = _materialise(direct_answer_case)
    second = _materialise(copy.deepcopy(direct_answer_case))

    assert first.status == second.status == "ok"
    assert first.local_id_map == second.local_id_map
    assert first.local_id_map is not None
    assert first.local_id_map["new-proposition-1"].startswith("proposition-")


def test_same_turn_local_references_resolve(
    direct_answer_case: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    result = _materialise(direct_answer_case)
    assert result.ledger is not None and result.local_id_map is not None

    relation = result.ledger["proposition_relations"][-1]
    assert relation["source_proposition_ids"] == [
        result.local_id_map["new-proposition-1"]
    ]
    commitment = result.ledger["participant_commitments"][-1]
    assert commitment["proposition_id"] == result.local_id_map["new-proposition-1"]


def test_existing_item_references_bind_only_to_prior_ids(
    direct_answer_case: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    prior, current_turn, delta = direct_answer_case
    delta["new_relations"][0]["target_proposition_refs"] = ["p-not-in-prior"]

    result = _materialise_existing(prior, current_turn, delta)

    assert result.status == "semantic_reference_invalid"
    assert any("unknown_existing_reference" in error for error in result.errors)


def test_duplicate_local_references_are_rejected(
    direct_answer_case: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    prior, current_turn, delta = direct_answer_case
    duplicate = copy.deepcopy(delta["new_propositions"][0])
    duplicate["canonical_text"] = "A second semantic item must not reuse the local ref."
    delta["new_propositions"].append(duplicate)

    result = _materialise_existing(prior, current_turn, delta)

    assert result.status == "semantic_reference_invalid"
    assert any("duplicate_local_reference" in error for error in result.errors)


def test_orphan_local_references_are_rejected(
    direct_answer_case: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    prior, current_turn, delta = direct_answer_case
    delta["new_relations"][0]["source_proposition_refs"] = ["new-proposition-99"]

    result = _materialise_existing(prior, current_turn, delta)

    assert result.status == "semantic_reference_invalid"
    assert any("orphan_local_reference" in error for error in result.errors)


def test_evidence_span_mismatch_is_rejected(
    direct_answer_case: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    prior, current_turn, delta = direct_answer_case
    delta["new_propositions"][0]["exact_evidence_spans"][0]["exact_text"] = (
        "This text is not present."
    )

    result = _materialise_existing(prior, current_turn, delta)

    assert result.status == "semantic_evidence_invalid"
    assert any("evidence_span_mismatch" in error for error in result.errors)


def test_future_turn_reference_is_rejected(
    direct_answer_case: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    prior, current_turn, delta = direct_answer_case
    delta["new_relations"][0]["exact_evidence_spans"][0]["turn_id"] = "t2"

    result = _materialise_existing(prior, current_turn, delta)

    assert result.status == "semantic_evidence_invalid"
    assert any("non_current_evidence_turn" in error for error in result.errors)


@pytest.mark.parametrize("field", ["proposition_id", "ledger_sha256"])
def test_model_supplied_permanent_id_or_hash_is_rejected(
    direct_answer_case: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
    field: str,
) -> None:
    prior, current_turn, delta = direct_answer_case
    delta["new_propositions"][0][field] = "provider-controlled-value"

    result = _materialise_existing(prior, current_turn, delta)

    assert result.status == "semantic_delta_schema_invalid"


def test_repeated_materialisation_is_byte_identical(
    direct_answer_case: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    first = _materialise(direct_answer_case)
    second = _materialise(copy.deepcopy(direct_answer_case))

    assert first.ledger is not None and second.ledger is not None
    assert phase1.canonical_json_bytes(first.ledger) == phase1.canonical_json_bytes(
        second.ledger
    )
    assert first.ledger["ledger_sha256"] == second.ledger["ledger_sha256"]


def test_all_failure_classes_remain_distinguishable(
    direct_answer_case: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior, current_turn, delta = direct_answer_case
    observed: set[str] = set()

    invalid_schema = copy.deepcopy(delta)
    invalid_schema["ledger_sha256"] = "0" * 64
    observed.add(
        _materialise_existing(prior, current_turn, invalid_schema).status
    )

    invalid_reference = copy.deepcopy(delta)
    invalid_reference["new_relations"][0]["target_proposition_refs"] = ["missing"]
    observed.add(
        _materialise_existing(prior, current_turn, invalid_reference).status
    )

    invalid_evidence = copy.deepcopy(delta)
    invalid_evidence["new_relations"][0]["exact_evidence_spans"][0][
        "exact_text"
    ] = "mismatch"
    observed.add(
        _materialise_existing(prior, current_turn, invalid_evidence).status
    )

    invalid_transition = copy.deepcopy(delta)
    invalid_transition["proposition_updates"][0]["changes"][
        "lifecycle_status"
    ] = "introduced"
    observed.add(
        _materialise_existing(prior, current_turn, invalid_transition).status
    )

    invalid_prior = copy.deepcopy(prior)
    invalid_prior["ledger_sha256"] = "0" * 64
    observed.add(
        _materialise_existing(invalid_prior, current_turn, delta).status
    )

    monkeypatch.setattr(
        phase1,
        "validate_ledger_incremental",
        lambda *_args, **_kwargs: ["synthetic_persisted_failure"],
    )
    observed.add(
        _materialise_existing(prior, current_turn, delta).status
    )

    assert observed == semantic.FAILURE_STATUSES


def test_no_stable_issue_is_schema_valid(
    direct_answer_case: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    _, current_turn, delta = direct_answer_case
    evidence = copy.deepcopy(
        delta["new_propositions"][0]["exact_evidence_spans"]
    )
    delta["new_issue_states"] = [
        {
            "local_ref": "new-issue-1",
            "initiating_speaker": "account",
            "canonical_question": None,
            "issue_type": "no_stable_issue",
            "live_alternatives": [],
            "addressed_participant": None,
            "answer_requirements": [],
            "related_proposition_refs": [],
            "status": "no_stable_issue",
            "resolution_type": "no_stable_issue",
            "confidence": 1.0,
            "exact_evidence_spans": evidence,
        }
    ]
    schema = _load_json(SEMANTIC_SCHEMA_PATH)

    assert phase1._jsonschema_errors(delta, schema) == []
    assert current_turn["turn_id"] == evidence[0]["turn_id"]


def test_new_no_stable_issue_materialises_without_explicit_resolved_item(
    direct_answer_case: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    prior, current_turn, _ = direct_answer_case
    delta = _semantic_noop(prior["conversation_key"], current_turn, prior)
    evidence = {
        "turn_id": current_turn["turn_id"],
        "start_char": 0,
        "end_char": len(current_turn["text"]),
        "exact_text": current_turn["text"],
    }
    delta["new_issue_states"] = [
        {
            "local_ref": "new-issue-1",
            "initiating_speaker": current_turn["speaker_id"],
            "canonical_question": None,
            "issue_type": "no_stable_issue",
            "live_alternatives": [],
            "addressed_participant": None,
            "answer_requirements": [],
            "related_proposition_refs": [],
            "status": "no_stable_issue",
            "resolution_type": "no_stable_issue",
            "confidence": 1.0,
            "exact_evidence_spans": [evidence],
        }
    ]

    schema = _load_json(SEMANTIC_SCHEMA_PATH)
    assert phase1._jsonschema_errors(delta, schema) == []
    assert delta["resolved_items"] == []

    result = _materialise_existing(prior, current_turn, delta)

    assert result.status == "ok", result.errors
    assert result.ledger is not None and result.local_id_map is not None
    issue_id = result.local_id_map["new-issue-1"]
    assert any(
        item == {
            "item_id": issue_id,
            "item_type": "issue",
            "resolution_type": "no_stable_issue",
            "resolved_at_turn_id": current_turn["turn_id"],
        }
        for item in result.ledger["resolved_items"]
    )


def test_behavioral_materialiser_validation_exercises_persisted_boundary() -> None:
    validation = semantic.behavioral_materialiser_validation(PROJECT_DIR)

    assert validation["passed"] is True
    assert validation["materialiser_version"] == semantic.MATERIALISER_VERSION
    assert validation["genesis_materialisation_valid"] is True
    assert validation["first_seen_participant_registration_valid"] is True
    assert validation["complete_incremental_chain_valid"] is True
    assert validation["incremental_chain_statuses"] == ["ok"] * 4
    assert validation["valid_materialisation_status"] == "ok"
    assert validation["repeated_materialisation_status"] == "ok"
    assert validation["full_persisted_validator_passed"] is True
    assert validation["full_persisted_validator_errors"] == []
    assert validation["deterministic_bytes"] is True
    assert validation["deterministic_ledger_hash"] is True
    assert validation["local_references_resolved"] is True
    assert validation["distinguishable_failure_statuses"] is True
    assert validation["failure_errors_bounded"] is True
    assert set(validation["observed_failure_statuses"]) == semantic.FAILURE_STATUSES
    assert all(
        expected == observed
        for expected, observed in validation["observed_failure_statuses"].items()
    )
    assert validation["provider_calls"] == 0
    assert validation["real_conversation_inputs"] == 0


def test_materialiser_imports_no_provider_network_x_or_production_modules() -> None:
    source = semantic.__file__
    assert source is not None
    tree = ast.parse(Path(source).read_text(encoding="utf-8"))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.partition(".")[0])

    assert "mrsMThatcher2" not in sys.modules
    assert imported_roots.isdisjoint(
        {
            "mrsMThatcher2",
            "openai",
            "anthropic",
            "google",
            "xai",
            "tweepy",
            "requests",
            "urllib",
            "httpx",
        }
    )
