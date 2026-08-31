#!/usr/bin/env python3
"""Build the deterministic, wholly invented proposition-ledger fixtures.

The generated conversations are synthetic contract examples.  They contain no
production text, post identifiers, contributor identifiers, annotations, or
model output.  ``--check`` is read-only and verifies that the checked-in pack
is byte-for-byte equal to a fresh in-memory build.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable


SCHEMA_VERSION = "proposition-ledger-v1.0.0"
ZERO_SHA256 = "0" * 64
FIXTURE_FORMAT_VERSION = "proposition-ledger-synthetic-fixture-v1"

STATE_COLLECTION_SPECS: tuple[tuple[str, str | None], ...] = (
    ("source_completeness", None),
    ("participants", "participant_id"),
    ("turn_refs", "turn_id"),
    ("propositions", "proposition_id"),
    ("proposition_groups", "proposition_group_id"),
    ("issue_states", "issue_id"),
    ("participant_commitments", "commitment_id"),
    ("conversational_obligations", "obligation_id"),
    ("proposition_relations", "relation_id"),
    ("answer_targets", "answer_target_id"),
    ("rejected_answer_targets", "rejected_answer_target_id"),
    ("repair_records", "repair_id"),
    ("unresolved_items", "item_type:item_id"),
    ("resolved_items", "item_type:item_id"),
    ("extraction_status", None),
    ("warnings", "warning_id"),
)

COLLECTION_INTRODUCTION_FIELDS = {
    "turn_refs": "turn_id",
    "propositions": "introduced_at_turn_id",
    "proposition_groups": "introduced_at_turn_id",
    "issue_states": "initiating_turn_id",
    "participant_commitments": "introduced_at_turn_id",
    "conversational_obligations": "created_by_turn_id",
    "proposition_relations": "introduced_at_turn_id",
    "answer_targets": "selected_at_turn_id",
    "rejected_answer_targets": "rejected_at_turn_id",
    "repair_records": "trigger_turn_id",
    "unresolved_items": "first_opened_at_turn_id",
    "resolved_items": "resolved_at_turn_id",
    "warnings": "introduced_at_turn_id",
}

DELTA_COLLECTION_FIELDS = {
    "propositions": ("propositions_added", "propositions_updated", "lifecycle_status"),
    "proposition_groups": (
        "proposition_groups_added",
        "proposition_groups_updated",
        None,
    ),
    "issue_states": ("issue_states_added", "issue_states_updated", "status"),
    "participant_commitments": ("commitments_added", "commitments_updated", "stance"),
    "conversational_obligations": (
        "obligations_added",
        "obligations_updated",
        "status",
    ),
    "answer_targets": ("answer_targets_added", "answer_targets_updated", "target_status"),
    "rejected_answer_targets": (
        "rejected_answer_targets_added",
        "rejected_answer_targets_updated",
        "status",
    ),
    "repair_records": ("repair_records_added", "repair_records_updated", "outcome"),
}


def canonical_bytes(value: Any) -> bytes:
    """Return the canonical JSON representation used by fixture hashes."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    """Return the SHA-256 of a canonical JSON value."""

    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def ledger_sha256(ledger: dict[str, Any]) -> str:
    """Compute the schema-design hash with the hash field zeroed."""

    material = copy.deepcopy(ledger)
    material["ledger_sha256"] = ZERO_SHA256
    return sha256_json(material)


def finish_hash(ledger: dict[str, Any]) -> dict[str, Any]:
    """Set and return a ledger's deterministic self-hash."""

    ledger["ledger_sha256"] = ZERO_SHA256
    ledger["ledger_sha256"] = ledger_sha256(ledger)
    return ledger


def json_bytes(value: Any) -> bytes:
    """Render a checked-in JSON document deterministically."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def make_transcript(
    fixture_id: str,
    description: str,
    utterances: list[tuple[str, str]],
    *,
    extra_participants: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Create a small linear synthetic transcript."""

    participants = [
        {"participant_id": "account", "role": "account"},
        {"participant_id": "contributor", "role": "contributor"},
    ]
    participants.extend(extra_participants or [])
    turns = []
    for index, (speaker_id, text) in enumerate(utterances):
        turns.append(
            {
                "language": "en",
                "parent_turn_id": None if index == 0 else f"t{index - 1}",
                "post_id": f"synthetic-{fixture_id}-post-{index}",
                "speaker_id": speaker_id,
                "text": text,
                "turn_id": f"t{index}",
                "turn_index": index,
            }
        )
    return {
        "conversation_key": f"synthetic:{fixture_id}",
        "description": description,
        "fixture_format_version": FIXTURE_FORMAT_VERSION,
        "fixture_id": fixture_id,
        "participants": participants,
        "root_post_id": turns[0]["post_id"],
        "synthetic": True,
        "turns": turns,
    }


def span(
    transcript: dict[str, Any],
    turn_id: str,
    exact_text: str | None = None,
    *,
    occurrence: int = 0,
) -> dict[str, Any]:
    """Create an exact evidence span, failing if the text is not present."""

    turn = next(item for item in transcript["turns"] if item["turn_id"] == turn_id)
    source = turn["text"]
    selected = source if exact_text is None else exact_text
    start = -1
    search_from = 0
    for _ in range(occurrence + 1):
        start = source.find(selected, search_from)
        if start < 0:
            raise ValueError(f"{turn_id!r} does not contain {selected!r}")
        search_from = start + 1
    return {
        "end_char": start + len(selected),
        "exact_text": selected,
        "start_char": start,
        "turn_id": turn_id,
    }


def speaker_ref(
    participant_id: str | None,
    *,
    kind: str = "speaker",
    attributed_participant_id: str | None = None,
) -> dict[str, Any]:
    """Create the explicit speaker/attributor record required by the schema."""

    return {
        "attributed_participant_id": attributed_participant_id,
        "kind": kind,
        "participant_id": participant_id,
    }


def proposition(
    transcript: dict[str, Any],
    proposition_id: str,
    canonical_text: str,
    turn_id: str,
    evidence_text: str | None,
    speaker_id: str | None,
    *,
    proposition_kind: str = "descriptive",
    speech_act: str = "assertion",
    polarity: str = "positive",
    epistemic_status: str = "asserted",
    commitment_status: str = "speaker_committed",
    lifecycle_status: str = "live",
    proposition_group_id: str | None = None,
    modality_type: str = "none",
    modality_strength: str = "none",
    quantification_type: str = "none",
    quantification_marker: str | None = None,
    temporal_type: str = "timeless",
    temporal_marker: str | None = None,
    derivation_kind: str = "direct_span",
    derivation_sources: list[str] | None = None,
    normalisation_note: str | None = None,
    confidence: float = 1.0,
    uncertainty_reason: str | None = None,
    speaker_kind: str = "speaker",
    attributed_participant_id: str | None = None,
    extra_spans: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create one evidence-linked proposition record."""

    evidence = [span(transcript, turn_id, evidence_text)]
    evidence.extend(extra_spans or [])
    return {
        "canonical_text": canonical_text,
        "commitment_status": commitment_status,
        "confidence": confidence,
        "derivation": {
            "kind": derivation_kind,
            "normalisation_note": normalisation_note,
            "source_proposition_ids": derivation_sources or [],
        },
        "epistemic_status": epistemic_status,
        "exact_evidence_spans": evidence,
        "introduced_at_turn_id": turn_id,
        "lifecycle_status": lifecycle_status,
        "modality": {"strength": modality_strength, "type": modality_type},
        "original_language": "en",
        "polarity": polarity,
        "proposition_group_id": proposition_group_id,
        "proposition_id": proposition_id,
        "proposition_kind": proposition_kind,
        "quantification": {
            "surface_marker": quantification_marker,
            "type": quantification_type,
        },
        "speaker_or_attributor": speaker_ref(
            speaker_id,
            kind=speaker_kind,
            attributed_participant_id=attributed_participant_id,
        ),
        "speech_act": speech_act,
        "temporal_scope": {
            "end": None,
            "start": None,
            "surface_marker": temporal_marker,
            "type": temporal_type,
        },
        "uncertainty_reason": uncertainty_reason,
    }


def issue(
    transcript: dict[str, Any],
    issue_id: str,
    turn_id: str,
    speaker_id: str,
    evidence_text: str | None,
    *,
    canonical_question: str | None,
    issue_type: str,
    addressed_participant: str | None,
    related_proposition_ids: list[str],
    status: str = "open",
    live_alternatives: list[dict[str, Any]] | None = None,
    answer_requirements: list[dict[str, str]] | None = None,
    resolution_turn_id: str | None = None,
    resolution_type: str | None = None,
    confidence: float = 1.0,
) -> dict[str, Any]:
    """Create an evidence-linked issue-state record."""

    return {
        "addressed_participant": addressed_participant,
        "answer_requirements": answer_requirements or [],
        "canonical_question": canonical_question,
        "confidence": confidence,
        "exact_evidence_spans": [span(transcript, turn_id, evidence_text)],
        "initiating_speaker": speaker_id,
        "initiating_turn_id": turn_id,
        "issue_id": issue_id,
        "issue_type": issue_type,
        "live_alternatives": live_alternatives or [],
        "related_proposition_ids": related_proposition_ids,
        "resolution_turn_id": resolution_turn_id,
        "resolution_type": resolution_type,
        "status": status,
    }


def commitment(
    commitment_id: str,
    participant_id: str,
    proposition_id: str,
    turn_id: str,
    *,
    stance: str = "asserted",
    basis: str = "explicit_speech_act",
    confidence: float = 1.0,
    uncertainty_reason: str | None = None,
) -> dict[str, Any]:
    """Create a participant-commitment record."""

    return {
        "basis": basis,
        "commitment_id": commitment_id,
        "confidence": confidence,
        "introduced_at_turn_id": turn_id,
        "last_updated_at_turn_id": turn_id,
        "participant_id": participant_id,
        "proposition_id": proposition_id,
        "stance": stance,
        "uncertainty_reason": uncertainty_reason,
    }


def obligation(
    obligation_id: str,
    obligation_type: str,
    created_by_turn_id: str,
    owed_by_participant: str,
    owed_to_participant: str,
    *,
    related_issue_ids: list[str],
    related_proposition_ids: list[str],
    status: str = "open",
    resolved_by_turn_id: str | None = None,
    expiry_policy: str = "on_issue_resolution",
    confidence: float = 1.0,
) -> dict[str, Any]:
    """Create a conversational obligation separate from truth state."""

    return {
        "confidence": confidence,
        "created_by_turn_id": created_by_turn_id,
        "expiry_policy": expiry_policy,
        "obligation_id": obligation_id,
        "obligation_type": obligation_type,
        "owed_by_participant": owed_by_participant,
        "owed_to_participant": owed_to_participant,
        "related_issue_ids": related_issue_ids,
        "related_proposition_ids": related_proposition_ids,
        "resolved_by_turn_id": resolved_by_turn_id,
        "status": status,
    }


def relation(
    transcript: dict[str, Any],
    relation_id: str,
    source_ids: list[str],
    target_ids: list[str],
    relation_type: str,
    analysed_by: str,
    turn_id: str,
    evidence_text: str | None,
    *,
    provenance_kind: str = "transcript_extraction",
    analysis_basis: str | None = None,
    confidence: float = 1.0,
    uncertainty_reason: str | None = None,
) -> dict[str, Any]:
    """Create a directed, provenance-qualified proposition relation."""

    if analysis_basis is None:
        if provenance_kind == "machine_diagnostic":
            analysis_basis = "evaluator_diagnosis"
        elif provenance_kind == "human_correction":
            analysis_basis = "human_correction"
        elif relation_type in {"fails_to_answer", "substitutes_for", "fails_to_address"}:
            analysis_basis = "speaker_explicit_metadiscourse"
        else:
            analysis_basis = "direct_semantic_content"

    return {
        "analysis_basis": analysis_basis,
        "asserted_or_analysed_by": analysed_by,
        "confidence": confidence,
        "exact_evidence_spans": [span(transcript, turn_id, evidence_text)],
        "introduced_at_turn_id": turn_id,
        "provenance_kind": provenance_kind,
        "relation_id": relation_id,
        "relation_type": relation_type,
        "source_proposition_ids": source_ids,
        "target_proposition_ids": target_ids,
        "uncertainty_reason": uncertainty_reason,
    }


def answer_target(
    answer_target_id: str,
    reply_turn_id: str,
    selected_at_turn_id: str,
    *,
    issue_ids: list[str],
    proposition_ids: list[str],
    target_status: str,
    confidence: float = 1.0,
) -> dict[str, Any]:
    """Create an apparent or corrected answer-target record."""

    return {
        "answer_target_id": answer_target_id,
        "confidence": confidence,
        "issue_ids": issue_ids,
        "proposition_ids": proposition_ids,
        "reply_turn_id": reply_turn_id,
        "selected_at_turn_id": selected_at_turn_id,
        "target_status": target_status,
    }


def transition(
    transcript: dict[str, Any],
    previous_sha256: str | None,
    *,
    propositions_added: list[str] | None = None,
    propositions_updated: list[dict[str, str]] | None = None,
    proposition_groups_added: list[str] | None = None,
    proposition_groups_updated: list[dict[str, str]] | None = None,
    issue_states_added: list[str] | None = None,
    issue_states_updated: list[dict[str, str]] | None = None,
    commitments_added: list[str] | None = None,
    commitments_updated: list[dict[str, str]] | None = None,
    obligations_added: list[str] | None = None,
    obligations_updated: list[dict[str, str]] | None = None,
    relations_added: list[str] | None = None,
    answer_targets_added: list[str] | None = None,
    answer_targets_updated: list[dict[str, str]] | None = None,
    rejected_answer_targets_added: list[str] | None = None,
    rejected_answer_targets_updated: list[dict[str, str]] | None = None,
    repair_records_added: list[str] | None = None,
    repair_records_updated: list[dict[str, str]] | None = None,
    items_resolved: list[str] | None = None,
    warnings_added: list[str] | None = None,
) -> dict[str, Any]:
    """Create the explicit delta for the final turn in a fixture."""

    current = transcript["turns"][-1]
    return {
        "answer_targets_added": answer_targets_added or [],
        "answer_targets_updated": answer_targets_updated or [],
        "commitments_added": commitments_added or [],
        "commitments_updated": commitments_updated or [],
        "current_turn_id": current["turn_id"],
        "current_turn_index": current["turn_index"],
        "from_ledger_sha256": previous_sha256,
        "issue_states_added": issue_states_added or [],
        "issue_states_updated": issue_states_updated or [],
        "items_resolved": items_resolved or [],
        "obligations_added": obligations_added or [],
        "obligations_updated": obligations_updated or [],
        "proposition_groups_added": proposition_groups_added or [],
        "proposition_groups_updated": proposition_groups_updated or [],
        "propositions_added": propositions_added or [],
        "propositions_updated": propositions_updated or [],
        "rejected_answer_targets_added": rejected_answer_targets_added or [],
        "rejected_answer_targets_updated": rejected_answer_targets_updated or [],
        "relations_added": relations_added or [],
        "repair_records_added": repair_records_added or [],
        "repair_records_updated": repair_records_updated or [],
        "transition_id": f"transition-{current['turn_id']}",
        "warnings_added": warnings_added or [],
    }


def status_update(item_id: str, old: str, new: str, reason: str) -> dict[str, str]:
    """Create one explicit status update for a transition delta."""

    return {"from_status": old, "item_id": item_id, "reason": reason, "to_status": new}


def state_item_id(collection: str, record: dict[str, Any]) -> str:
    """Return the schema-defined stable key for one state-bearing record."""

    key_field = dict(STATE_COLLECTION_SPECS)[collection]
    if key_field is None:
        return collection
    if key_field == "item_type:item_id":
        return f"{record['item_type']}:{record['item_id']}"
    return str(record[key_field])


def state_records(ledger: dict[str, Any] | None) -> dict[str, dict[str, dict[str, Any]]]:
    """Index all 16 replayable state collections by their stable item keys."""

    indexed: dict[str, dict[str, dict[str, Any]]] = {}
    for collection, key_field in STATE_COLLECTION_SPECS:
        records: list[dict[str, Any]]
        if ledger is None:
            records = []
        elif key_field is None:
            value = ledger.get(collection)
            records = [value] if isinstance(value, dict) else []
        else:
            records = list(ledger.get(collection, []))
        collection_index: dict[str, dict[str, Any]] = {}
        for record in records:
            item_id = state_item_id(collection, record)
            if item_id in collection_index:
                raise ValueError(f"duplicate state key {collection}:{item_id}")
            collection_index[item_id] = copy.deepcopy(record)
        indexed[collection] = collection_index
    return indexed


def make_state_patch(
    previous_ledger: dict[str, Any] | None,
    current_ledger: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return the canonical full-record patch from predecessor to current state."""

    previous = state_records(previous_ledger)
    current = state_records(current_ledger)
    operations: list[dict[str, Any]] = []
    for collection, _ in STATE_COLLECTION_SPECS:
        before_records = previous[collection]
        after_records = current[collection]
        for item_id in sorted(set(before_records) | set(after_records)):
            before = before_records.get(item_id)
            after = after_records.get(item_id)
            if before == after:
                continue
            if before is None:
                operation = "add"
                before_hash = None
            elif after is None:
                operation = "remove"
                before_hash = sha256_json(before)
            else:
                operation = "replace"
                before_hash = sha256_json(before)
            operations.append(
                {
                    "after_record": copy.deepcopy(after),
                    "before_record_sha256": before_hash,
                    "collection": collection,
                    "item_id": item_id,
                    "operation": operation,
                }
            )
    if not operations:
        raise ValueError("a fixture state transition must contain at least one patch operation")
    return operations


def apply_state_patch(
    previous_ledger: dict[str, Any] | None,
    patch: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Apply a canonical full-record patch and return its indexed state."""

    state = state_records(previous_ledger)
    collection_order = {name: index for index, (name, _) in enumerate(STATE_COLLECTION_SPECS)}
    expected_order = sorted(
        patch,
        key=lambda operation: (
            collection_order[operation["collection"]],
            operation["item_id"],
        ),
    )
    if patch != expected_order:
        raise ValueError("state patch is not in canonical collection/item order")
    seen: set[tuple[str, str]] = set()
    for operation in patch:
        collection = operation["collection"]
        item_id = operation["item_id"]
        pair = (collection, item_id)
        if pair in seen:
            raise ValueError(f"duplicate state patch operation {collection}:{item_id}")
        seen.add(pair)
        existing = state[collection].get(item_id)
        kind = operation["operation"]
        if kind == "add":
            if existing is not None or operation["before_record_sha256"] is not None:
                raise ValueError(f"invalid add operation {collection}:{item_id}")
            state[collection][item_id] = copy.deepcopy(operation["after_record"])
            continue
        if existing is None or sha256_json(existing) != operation["before_record_sha256"]:
            raise ValueError(f"state patch precondition mismatch {collection}:{item_id}")
        if kind == "remove":
            if operation["after_record"] is not None:
                raise ValueError(f"invalid remove operation {collection}:{item_id}")
            del state[collection][item_id]
        elif kind == "replace":
            state[collection][item_id] = copy.deepcopy(operation["after_record"])
        else:
            raise ValueError(f"unknown state patch operation {kind!r}")
    return state


def synchronise_delta_projection(
    transition_record: dict[str, Any],
    patch: list[dict[str, Any]],
    previous_ledger: dict[str, Any] | None,
) -> None:
    """Make the concise typed delta arrays an exact projection of a state patch."""

    prior_reasons = {
        (field, update["item_id"]): update["reason"]
        for _, (added_field, updated_field, _) in DELTA_COLLECTION_FIELDS.items()
        for field in (added_field, updated_field)
        for update in transition_record.get(field, [])
        if isinstance(update, dict) and "item_id" in update
    }
    for added_field, updated_field, _ in DELTA_COLLECTION_FIELDS.values():
        transition_record[added_field] = []
        transition_record[updated_field] = []
    transition_record["relations_added"] = []
    transition_record["items_resolved"] = []
    transition_record["warnings_added"] = []
    previous = state_records(previous_ledger)
    for operation in patch:
        collection = operation["collection"]
        item_id = operation["item_id"]
        if collection in DELTA_COLLECTION_FIELDS:
            added_field, updated_field, status_field = DELTA_COLLECTION_FIELDS[collection]
            if operation["operation"] == "add":
                transition_record[added_field].append(item_id)
            elif operation["operation"] == "replace":
                before = previous[collection][item_id]
                after = operation["after_record"]
                old_status = before.get(status_field, "present") if status_field else "present"
                new_status = after.get(status_field, "present") if status_field else "present"
                transition_record[updated_field].append(
                    status_update(
                        item_id,
                        str(old_status),
                        str(new_status),
                        prior_reasons.get(
                            (updated_field, item_id),
                            "The complete state record changed at the current turn.",
                        ),
                    )
                )
        elif collection == "proposition_relations" and operation["operation"] == "add":
            transition_record["relations_added"].append(item_id)
        elif collection == "resolved_items" and operation["operation"] == "add":
            transition_record["items_resolved"].append(operation["after_record"]["item_id"])
        elif collection == "warnings" and operation["operation"] == "add":
            transition_record["warnings_added"].append(item_id)
    transition_record["state_patch"] = patch


def _turn_is_visible(turn_indexes: dict[str, int], turn_id: Any, boundary: int) -> bool:
    """Return whether a referenced turn is present at a predecessor boundary."""

    return isinstance(turn_id, str) and turn_id in turn_indexes and turn_indexes[turn_id] <= boundary


def make_boundary_snapshot(
    transcript: dict[str, Any],
    current_ledger: dict[str, Any],
    seed_transition: dict[str, Any],
    boundary: int,
) -> dict[str, Any]:
    """Derive one plausible turn-bounded state from the fixture's final state."""

    turn_indexes = {turn["turn_id"]: turn["turn_index"] for turn in transcript["turns"]}
    final_boundary = current_ledger["as_of_turn_index"]
    if not 0 <= boundary <= final_boundary:
        raise ValueError(f"fixture boundary {boundary} falls outside the transcript")
    boundary_turn = next(turn for turn in transcript["turns"] if turn["turn_index"] == boundary)
    predecessor = copy.deepcopy(current_ledger)
    if boundary != final_boundary:
        predecessor["ledger_id"] = f"{current_ledger['ledger_id']}-history-{boundary}"
    predecessor["target_turn_id"] = boundary_turn["turn_id"]
    predecessor["as_of_turn_index"] = boundary
    predecessor["previous_ledger_sha256"] = None
    predecessor["ledger_sha256"] = ZERO_SHA256

    for collection, introduction_field in COLLECTION_INTRODUCTION_FIELDS.items():
        predecessor[collection] = [
            record
            for record in predecessor.get(collection, [])
            if _turn_is_visible(turn_indexes, record.get(introduction_field), boundary)
        ]

    update_specs = {
        "propositions_updated": ("propositions", "proposition_id", "lifecycle_status"),
        "proposition_groups_updated": (
            "proposition_groups",
            "proposition_group_id",
            None,
        ),
        "issue_states_updated": ("issue_states", "issue_id", "status"),
        "commitments_updated": (
            "participant_commitments",
            "commitment_id",
            "stance",
        ),
        "obligations_updated": (
            "conversational_obligations",
            "obligation_id",
            "status",
        ),
        "answer_targets_updated": ("answer_targets", "answer_target_id", "target_status"),
        "rejected_answer_targets_updated": (
            "rejected_answer_targets",
            "rejected_answer_target_id",
            "status",
        ),
        "repair_records_updated": ("repair_records", "repair_id", "outcome"),
    }
    if boundary < final_boundary:
        for field, (collection, id_field, status_field) in update_specs.items():
            records = {record[id_field]: record for record in predecessor.get(collection, [])}
            for update in seed_transition.get(field, []):
                record = records.get(update["item_id"])
                if record is not None and status_field is not None:
                    record[status_field] = update["from_status"]

    if transcript["fixture_id"] == "02-explicit-correction" and boundary == 0:
        for proposition_record in predecessor["propositions"]:
            if proposition_record["proposition_id"] == "p-ban-question":
                proposition_record["lifecycle_status"] = "live"

    for proposition_record in predecessor["propositions"]:
        proposition_record["exact_evidence_spans"] = [
            evidence
            for evidence in proposition_record["exact_evidence_spans"]
            if _turn_is_visible(turn_indexes, evidence["turn_id"], boundary)
        ]
        proposition_record["derivation"]["source_proposition_ids"] = [
            proposition_id
            for proposition_id in proposition_record["derivation"]["source_proposition_ids"]
            if any(
                candidate["proposition_id"] == proposition_id
                for candidate in predecessor["propositions"]
            )
        ]
        if (
            boundary < final_boundary
            and proposition_record["proposition_id"] == "p-board-made-process-fairer"
        ):
            proposition_record["canonical_text"] = "The new board made it fairer."
            proposition_record["derivation"] = {
                "kind": "direct_span",
                "normalisation_note": "The predecessor does not resolve the pronoun before clarification.",
                "source_proposition_ids": [],
            }

    proposition_ids = {record["proposition_id"] for record in predecessor["propositions"]}
    group_ids = {record["proposition_group_id"] for record in predecessor["proposition_groups"]}
    issue_ids = {record["issue_id"] for record in predecessor["issue_states"]}
    answer_target_ids = {record["answer_target_id"] for record in predecessor["answer_targets"]}
    rejected_target_ids = {
        record["rejected_answer_target_id"] for record in predecessor["rejected_answer_targets"]
    }
    for proposition_record in predecessor["propositions"]:
        if proposition_record["proposition_group_id"] not in group_ids:
            proposition_record["proposition_group_id"] = None
    for group_record in predecessor["proposition_groups"]:
        group_record["members"] = [
            member for member in group_record["members"] if member["proposition_id"] in proposition_ids
        ]
        for ordinal, member in enumerate(group_record["members"]):
            member["ordinal"] = ordinal
    for issue_record in predecessor["issue_states"]:
        issue_record["related_proposition_ids"] = [
            proposition_id
            for proposition_id in issue_record["related_proposition_ids"]
            if proposition_id in proposition_ids
        ]
        for alternative in issue_record["live_alternatives"]:
            alternative["proposition_ids"] = [
                proposition_id
                for proposition_id in alternative["proposition_ids"]
                if proposition_id in proposition_ids
            ]
        if not _turn_is_visible(turn_indexes, issue_record["resolution_turn_id"], boundary):
            issue_record["resolution_turn_id"] = None
            issue_record["resolution_type"] = None
    for commitment_record in predecessor["participant_commitments"]:
        if not _turn_is_visible(
            turn_indexes,
            commitment_record["last_updated_at_turn_id"],
            boundary,
        ):
            commitment_record["last_updated_at_turn_id"] = commitment_record["introduced_at_turn_id"]
    predecessor["participant_commitments"] = [
        record
        for record in predecessor["participant_commitments"]
        if record["proposition_id"] in proposition_ids
    ]
    for obligation_record in predecessor["conversational_obligations"]:
        obligation_record["related_issue_ids"] = [
            issue_id for issue_id in obligation_record["related_issue_ids"] if issue_id in issue_ids
        ]
        obligation_record["related_proposition_ids"] = [
            proposition_id
            for proposition_id in obligation_record["related_proposition_ids"]
            if proposition_id in proposition_ids
        ]
        if not _turn_is_visible(turn_indexes, obligation_record["resolved_by_turn_id"], boundary):
            obligation_record["resolved_by_turn_id"] = None
    predecessor["proposition_relations"] = [
        record
        for record in predecessor["proposition_relations"]
        if set(record["source_proposition_ids"]) <= proposition_ids
        and set(record["target_proposition_ids"]) <= proposition_ids
    ]
    predecessor["answer_targets"] = [
        record
        for record in predecessor["answer_targets"]
        if set(record["issue_ids"]) <= issue_ids
        and set(record["proposition_ids"]) <= proposition_ids
    ]
    answer_target_ids = {record["answer_target_id"] for record in predecessor["answer_targets"]}
    predecessor["rejected_answer_targets"] = [
        record
        for record in predecessor["rejected_answer_targets"]
        if record["answer_target_id"] in answer_target_ids
        and (
            record["replacement_answer_target_id"] is None
            or record["replacement_answer_target_id"] in answer_target_ids
        )
        and set(record["related_proposition_ids"]) <= proposition_ids
    ]
    rejected_target_ids = {
        record["rejected_answer_target_id"] for record in predecessor["rejected_answer_targets"]
    }
    predecessor["repair_records"] = [
        record
        for record in predecessor["repair_records"]
        if set(record["rejected_answer_target_ids"]) <= rejected_target_ids
        and set(record["replacement_answer_target_ids"]) <= answer_target_ids
    ]

    for collection in (
        "proposition_groups",
        "issue_states",
        "proposition_relations",
        "rejected_answer_targets",
        "repair_records",
    ):
        for record in predecessor[collection]:
            record["exact_evidence_spans"] = [
                evidence
                for evidence in record["exact_evidence_spans"]
                if _turn_is_visible(turn_indexes, evidence["turn_id"], boundary)
            ]
    predecessor["resolved_items"] = [
        record
        for record in predecessor["resolved_items"]
        if _turn_is_visible(turn_indexes, record["resolved_at_turn_id"], boundary)
    ]
    valid_unresolved_ids = proposition_ids | issue_ids | {
        record["commitment_id"] for record in predecessor["participant_commitments"]
    } | {
        record["obligation_id"] for record in predecessor["conversational_obligations"]
    } | answer_target_ids | {
        record["repair_id"] for record in predecessor["repair_records"]
    }
    predecessor["unresolved_items"] = [
        record for record in predecessor["unresolved_items"] if record["item_id"] in valid_unresolved_ids
    ]

    predecessor["state_transitions"] = []
    return predecessor


def build_ledger_chain(
    transcript: dict[str, Any],
    current_ledger: dict[str, Any],
    seed_transition: dict[str, Any],
) -> dict[str, Any]:
    """Build and hash the genuine turn-by-turn chain ending at the current ledger."""

    snapshots: list[dict[str, Any]] = []
    final_boundary = current_ledger["as_of_turn_index"]
    previous: dict[str, Any] | None = None
    for boundary in range(final_boundary + 1):
        snapshot = make_boundary_snapshot(
            transcript,
            current_ledger,
            seed_transition,
            boundary,
        )
        snapshot["previous_ledger_sha256"] = (
            previous["ledger_sha256"] if previous is not None else None
        )
        if boundary == final_boundary:
            transition_record = copy.deepcopy(seed_transition)
            transition_record["current_turn_id"] = snapshot["target_turn_id"]
            transition_record["current_turn_index"] = boundary
            transition_record["from_ledger_sha256"] = snapshot["previous_ledger_sha256"]
        else:
            transition_record = transition(
                {"turns": transcript["turns"][: boundary + 1]},
                snapshot["previous_ledger_sha256"],
            )
        patch = make_state_patch(previous, snapshot)
        synchronise_delta_projection(transition_record, patch, previous)
        snapshot["state_transitions"] = [transition_record]
        finish_hash(snapshot)
        snapshots.append(snapshot)
        previous = snapshot
    transcript["ledger_history"] = copy.deepcopy(snapshots[:-1])
    transcript.pop("previous_ledger", None)
    return snapshots[-1]


def base_ledger(
    transcript: dict[str, Any],
    *,
    propositions: list[dict[str, Any]],
    proposition_groups: list[dict[str, Any]] | None = None,
    issue_states: list[dict[str, Any]] | None = None,
    participant_commitments: list[dict[str, Any]] | None = None,
    conversational_obligations: list[dict[str, Any]] | None = None,
    proposition_relations: list[dict[str, Any]] | None = None,
    answer_targets: list[dict[str, Any]] | None = None,
    rejected_answer_targets: list[dict[str, Any]] | None = None,
    repair_records: list[dict[str, Any]] | None = None,
    state_transition: dict[str, Any] | None = None,
    unresolved_items: list[dict[str, Any]] | None = None,
    resolved_items: list[dict[str, Any]] | None = None,
    extraction_status: dict[str, Any] | None = None,
    warnings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble and hash a structurally complete final-turn snapshot."""

    turns = transcript["turns"]
    target = turns[-1]
    previous_sha256 = sha256_json(
        {
            "fixture_id": transcript["fixture_id"],
            "synthetic_predecessor_through_turn": target["turn_index"] - 1,
        }
    )
    participants = []
    for item in transcript["participants"]:
        participants.append(
            {
                "author_key": f"synthetic-{item['participant_id']}",
                "identity_confidence": 1.0,
                "participant_id": item["participant_id"],
                "role": item["role"],
            }
        )
    ledger = {
        "answer_targets": answer_targets or [],
        "as_of_turn_index": target["turn_index"],
        "conversation_key": f"synthetic:{transcript['fixture_id']}",
        "conversational_obligations": conversational_obligations or [],
        "extraction_status": extraction_status
        or {"abstentions": [], "status": "complete", "unsupported_inferences_rejected": 0},
        "issue_states": issue_states or [],
        "ledger_id": f"ledger-{transcript['fixture_id']}",
        "ledger_sha256": ZERO_SHA256,
        "participants": participants,
        "participant_commitments": participant_commitments or [],
        "previous_ledger_sha256": previous_sha256,
        "proposition_groups": proposition_groups or [],
        "proposition_relations": proposition_relations or [],
        "propositions": propositions,
        "rejected_answer_targets": rejected_answer_targets or [],
        "repair_records": repair_records or [],
        "resolved_items": resolved_items or [],
        "root_post_id": turns[0]["post_id"],
        "schema_version": SCHEMA_VERSION,
        "source_completeness": {
            "account_publication_confirmed": True,
            "chronology_complete": True,
            "complete_prefix_through_turn": True,
            "exact_text_complete": True,
            "limitations": ["Wholly invented schema fixture; not historical evidence."],
            "parent_graph_complete": True,
            "reconstruction_grade": "A",
        },
        "state_transitions": [
            state_transition or transition(transcript, previous_sha256)
        ],
        "target_turn_id": target["turn_id"],
        "turn_refs": [
            {
                "parent_turn_id": turn["parent_turn_id"],
                "post_id": turn["post_id"],
                "speaker_id": turn["speaker_id"],
                "text_sha256": hashlib.sha256(turn["text"].encode("utf-8")).hexdigest(),
                "turn_id": turn["turn_id"],
                "turn_index": turn["turn_index"],
            }
            for turn in turns
        ],
        "unresolved_items": unresolved_items or [],
        "warnings": warnings or [],
    }
    seed_transition = ledger["state_transitions"][0]
    return build_ledger_chain(transcript, ledger, seed_transition)


def unresolved(item_type: str, item_id: str, turn_id: str, reason: str) -> dict[str, str]:
    """Create an unresolved-item reference."""

    return {
        "first_opened_at_turn_id": turn_id,
        "item_id": item_id,
        "item_type": item_type,
        "reason": reason,
    }


def resolved(item_type: str, item_id: str, turn_id: str, resolution_type: str) -> dict[str, str]:
    """Create a resolved-item reference."""

    return {
        "item_id": item_id,
        "item_type": item_type,
        "resolution_type": resolution_type,
        "resolved_at_turn_id": turn_id,
    }


def group(
    transcript: dict[str, Any],
    group_id: str,
    structure_type: str,
    turn_id: str,
    evidence_text: str | None,
    members: list[tuple[str, str]],
) -> dict[str, Any]:
    """Create a proposition group that preserves compound structure."""

    return {
        "confidence": 1.0,
        "decomposition_complete": True,
        "exact_evidence_spans": [span(transcript, turn_id, evidence_text)],
        "introduced_at_turn_id": turn_id,
        "members": [
            {"ordinal": index, "proposition_id": proposition_id, "role": role}
            for index, (proposition_id, role) in enumerate(members)
        ],
        "proposition_group_id": group_id,
        "structure_type": structure_type,
        "uncertainty_reason": None,
    }


def rejected_target(
    transcript: dict[str, Any],
    rejected_id: str,
    answer_target_id: str,
    turn_id: str,
    evidence_text: str | None,
    *,
    replacement_id: str | None,
    related_proposition_ids: list[str],
    rejection_kind: str = "wrong_proposition",
    status: str = "active",
) -> dict[str, Any]:
    """Create an explicit rejected-answer-target record."""

    return {
        "answer_target_id": answer_target_id,
        "confidence": 1.0,
        "exact_evidence_spans": [span(transcript, turn_id, evidence_text)],
        "rejected_answer_target_id": rejected_id,
        "rejected_at_turn_id": turn_id,
        "rejection_kind": rejection_kind,
        "related_proposition_ids": related_proposition_ids,
        "replacement_answer_target_id": replacement_id,
        "status": status,
    }


def repair(
    transcript: dict[str, Any],
    repair_id: str,
    turn_id: str,
    evidence_text: str | None,
    *,
    repair_type: str,
    rejected_ids: list[str],
    replacement_ids: list[str],
    outcome: str,
    acknowledgement_turn_id: str | None = None,
) -> dict[str, Any]:
    """Create a correction or topic-boundary repair record."""

    return {
        "acknowledgement_turn_id": acknowledgement_turn_id,
        "confidence": 1.0,
        "exact_evidence_spans": [span(transcript, turn_id, evidence_text)],
        "outcome": outcome,
        "rejected_answer_target_ids": rejected_ids,
        "repair_id": repair_id,
        "repair_type": repair_type,
        "replacement_answer_target_ids": replacement_ids,
        "trigger_turn_id": turn_id,
    }


def warning(warning_id: str, turn_id: str, code: str, message: str) -> dict[str, Any]:
    """Create a bounded abstention warning."""

    return {
        "code": code,
        "introduced_at_turn_id": turn_id,
        "message": message,
        "severity": "warning",
        "warning_id": warning_id,
    }


def invalid_example(
    fixture_id: str,
    valid_ledger: dict[str, Any],
    invalid_example_id: str,
    expected_error_code: str,
    description: str,
    mutation: Callable[[dict[str, Any]], None],
    *,
    violation_kind: str = "semantic_invariant",
) -> dict[str, Any]:
    """Clone and deterministically hash one intentionally invalid ledger."""

    candidate = copy.deepcopy(valid_ledger)
    mutation(candidate)
    finish_hash(candidate)
    return {
        "description": description,
        "expected_error_code": expected_error_code,
        "fixture_id": fixture_id,
        "invalid_example_id": invalid_example_id,
        "ledger": candidate,
        "violation_kind": violation_kind,
    }


def invalid_document(fixture_id: str, examples: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap invalid examples in the common machine-readable format."""

    return {
        "examples": examples,
        "fixture_format_version": FIXTURE_FORMAT_VERSION,
        "fixture_id": fixture_id,
        "synthetic": True,
    }


def build_direct_question_answer() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Fixture 1: a polar question receives a direct answer."""

    fixture_id = "01-direct-question-answer"
    transcript = make_transcript(
        fixture_id,
        "A direct polar question followed by a direct affirmative answer.",
        [
            ("contributor", "Is the village bridge open today?"),
            ("account", "Yes, the village bridge is open today."),
        ],
    )
    p_question = proposition(
        transcript,
        "p-bridge-question",
        "The village bridge is open today.",
        "t0",
        "the village bridge open today",
        "contributor",
        speech_act="question",
        polarity="open",
        epistemic_status="questioned",
        commitment_status="questioned_only",
        lifecycle_status="resolved",
        temporal_type="present",
        temporal_marker="today",
    )
    p_answer = proposition(
        transcript,
        "p-bridge-open",
        "The village bridge is open today.",
        "t1",
        "the village bridge is open today",
        "account",
        temporal_type="present",
        temporal_marker="today",
    )
    i_bridge = issue(
        transcript,
        "i-bridge-open",
        "t0",
        "contributor",
        None,
        canonical_question="Is the village bridge open today?",
        issue_type="polar",
        addressed_participant="account",
        related_proposition_ids=["p-bridge-question"],
        status="answered",
        live_alternatives=[
            {
                "alternative_id": "alt-open",
                "label": "The bridge is open.",
                "proposition_ids": ["p-bridge-question"],
                "status": "supported",
            },
            {
                "alternative_id": "alt-closed",
                "label": "The bridge is not open.",
                "proposition_ids": [],
                "status": "rejected",
            },
        ],
        answer_requirements=[
            {"description": "Answer whether the bridge is open.", "requirement_type": "yes_no"}
        ],
        resolution_turn_id="t1",
        resolution_type="direct_answer",
    )
    o_question = obligation(
        "o-answer-bridge",
        "explicit_question",
        "t0",
        "account",
        "contributor",
        related_issue_ids=["i-bridge-open"],
        related_proposition_ids=["p-bridge-question"],
        status="satisfied",
        resolved_by_turn_id="t1",
    )
    r_answer = relation(
        transcript,
        "r-direct-answer",
        ["p-bridge-open"],
        ["p-bridge-question"],
        "answers",
        "account",
        "t1",
        None,
    )
    ledger = base_ledger(
        transcript,
        propositions=[p_question, p_answer],
        issue_states=[i_bridge],
        participant_commitments=[
            commitment(
                "c-question-bridge",
                "contributor",
                "p-bridge-question",
                "t0",
                stance="questioned",
            ),
            commitment("c-answer-bridge", "account", "p-bridge-open", "t1"),
        ],
        conversational_obligations=[o_question],
        proposition_relations=[r_answer],
        answer_targets=[
            answer_target(
                "at-bridge",
                "t1",
                "t1",
                issue_ids=["i-bridge-open"],
                proposition_ids=["p-bridge-question"],
                target_status="confirmed",
            )
        ],
        state_transition=transition(
            transcript,
            None,
            propositions_added=["p-bridge-open"],
            propositions_updated=[
                status_update("p-bridge-question", "live", "resolved", "Direct answer supplied.")
            ],
            issue_states_updated=[
                status_update("i-bridge-open", "open", "answered", "Direct answer supplied.")
            ],
            commitments_added=["c-answer-bridge"],
            obligations_updated=[
                status_update("o-answer-bridge", "open", "satisfied", "Question answered.")
            ],
            relations_added=["r-direct-answer"],
            answer_targets_added=["at-bridge"],
            items_resolved=["p-bridge-question", "i-bridge-open", "o-answer-bridge"],
        ),
        resolved_items=[
            resolved("proposition", "p-bridge-question", "t1", "direct_answer"),
            resolved("issue", "i-bridge-open", "t1", "direct_answer"),
            resolved("obligation", "o-answer-bridge", "t1", "question_answered"),
        ],
    )

    def make_future(candidate: dict[str, Any]) -> None:
        candidate["as_of_turn_index"] = 0

    invalid = invalid_document(
        fixture_id,
        [
            invalid_example(
                fixture_id,
                ledger,
                "future-turn-reference",
                "future_turn_reference",
                "The snapshot boundary is moved before its target turn and evidence.",
                make_future,
            )
        ],
    )
    return transcript, ledger, invalid


def build_explicit_correction() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Fixture 2: a contributor replaces an over-broad answer target."""

    fixture_id = "02-explicit-correction"
    transcript = make_transcript(
        fixture_id,
        "An explicit not-X-but-Y correction rejects an over-broad target.",
        [
            ("contributor", "Does the new rule ban bicycles?"),
            ("account", "It bans bicycles on every path."),
            (
                "contributor",
                "It does not ban bicycles on every path, but it does ban them only in the glasshouse.",
            ),
        ],
    )
    p_question = proposition(
        transcript,
        "p-ban-question",
        "The new rule bans bicycles.",
        "t0",
        "the new rule ban bicycles",
        "contributor",
        speech_act="question",
        polarity="open",
        epistemic_status="questioned",
        commitment_status="questioned_only",
        lifecycle_status="challenged",
    )
    p_everywhere = proposition(
        transcript,
        "p-ban-every-path",
        "The rule bans bicycles on every path.",
        "t1",
        "bans bicycles on every path",
        "account",
        lifecycle_status="challenged",
        quantification_type="universal",
        quantification_marker="every",
    )
    p_not_everywhere = proposition(
        transcript,
        "p-not-every-path",
        "The rule does not ban bicycles on every path.",
        "t2",
        "does not ban bicycles on every path",
        "contributor",
        speech_act="correction",
        polarity="negative",
        proposition_group_id="g-correction",
        derivation_kind="compound_decomposition",
        normalisation_note="Expands the explicit elliptical correction without changing its scope.",
    )
    p_glasshouse = proposition(
        transcript,
        "p-ban-glasshouse",
        "The rule bans bicycles only in the glasshouse.",
        "t2",
        "it does ban them only in the glasshouse",
        "contributor",
        speech_act="correction",
        proposition_group_id="g-correction",
        derivation_kind="compound_decomposition",
    )
    i_ban = issue(
        transcript,
        "i-ban-scope",
        "t0",
        "contributor",
        None,
        canonical_question="Where does the new rule ban bicycles?",
        issue_type="wh",
        addressed_participant="account",
        related_proposition_ids=["p-ban-question", "p-ban-every-path", "p-ban-glasshouse"],
        status="partly_answered",
        answer_requirements=[
            {"description": "Identify the scope of the bicycle ban.", "requirement_type": "supply_value"}
        ],
    )
    targets = [
        answer_target(
            "at-every-path",
            "t1",
            "t1",
            issue_ids=["i-ban-scope"],
            proposition_ids=["p-ban-every-path"],
            target_status="rejected",
        ),
        answer_target(
            "at-glasshouse",
            "t1",
            "t2",
            issue_ids=["i-ban-scope"],
            proposition_ids=["p-ban-glasshouse"],
            target_status="confirmed",
        ),
    ]
    rejected = rejected_target(
        transcript,
        "rat-every-path",
        "at-every-path",
        "t2",
        None,
        replacement_id="at-glasshouse",
        related_proposition_ids=["p-ban-every-path", "p-not-every-path", "p-ban-glasshouse"],
        rejection_kind="not_x_but_y",
    )
    repair_record = repair(
        transcript,
        "repair-ban-scope",
        "t2",
        None,
        repair_type="contributor_correction",
        rejected_ids=["rat-every-path"],
        replacement_ids=["at-glasshouse"],
        outcome="pending",
    )
    ledger = base_ledger(
        transcript,
        propositions=[p_question, p_everywhere, p_not_everywhere, p_glasshouse],
        proposition_groups=[
            group(
                transcript,
                "g-correction",
                "conjunction",
                "t2",
                None,
                [("p-not-every-path", "qualification"), ("p-ban-glasshouse", "conclusion")],
            )
        ],
        issue_states=[i_ban],
        participant_commitments=[
            commitment("c-ban-question", "contributor", "p-ban-question", "t0", stance="questioned"),
            commitment("c-every-path", "account", "p-ban-every-path", "t1"),
            commitment(
                "c-not-every-path",
                "contributor",
                "p-not-every-path",
                "t2",
                basis="explicit_correction",
            ),
            commitment(
                "c-glasshouse",
                "contributor",
                "p-ban-glasshouse",
                "t2",
                basis="explicit_correction",
            ),
        ],
        conversational_obligations=[
            obligation(
                "o-ban-answer",
                "explicit_question",
                "t0",
                "account",
                "contributor",
                related_issue_ids=["i-ban-scope"],
                related_proposition_ids=["p-ban-question"],
                status="partly_satisfied",
            ),
            obligation(
                "o-acknowledge-ban-correction",
                "correction_requiring_acknowledgement",
                "t2",
                "account",
                "contributor",
                related_issue_ids=["i-ban-scope"],
                related_proposition_ids=["p-not-every-path", "p-ban-glasshouse"],
                expiry_policy="never_implicit",
            ),
        ],
        proposition_relations=[
            relation(
                transcript,
                "r-denies-every-path",
                ["p-not-every-path"],
                ["p-ban-every-path"],
                "contradicts",
                "contributor",
                "t2",
                "does not ban bicycles on every path",
            ),
            relation(
                transcript,
                "r-corrects-scope",
                ["p-ban-glasshouse"],
                ["p-ban-every-path"],
                "corrects",
                "contributor",
                "t2",
                None,
                provenance_kind="human_correction",
            ),
        ],
        answer_targets=targets,
        rejected_answer_targets=[rejected],
        repair_records=[repair_record],
        state_transition=transition(
            transcript,
            None,
            propositions_added=["p-not-every-path", "p-ban-glasshouse"],
            propositions_updated=[
                status_update("p-ban-every-path", "live", "challenged", "Contributor corrected its scope.")
            ],
            issue_states_updated=[
                status_update("i-ban-scope", "open", "partly_answered", "A narrower scope was supplied.")
            ],
            commitments_added=["c-not-every-path", "c-glasshouse"],
            obligations_added=["o-acknowledge-ban-correction"],
            obligations_updated=[
                status_update("o-ban-answer", "open", "partly_satisfied", "Scope was narrowed but not acknowledged.")
            ],
            relations_added=["r-denies-every-path", "r-corrects-scope"],
            proposition_groups_added=["g-correction"],
            answer_targets_added=["at-glasshouse"],
            answer_targets_updated=[
                status_update(
                    "at-every-path",
                    "apparent",
                    "rejected",
                    "The contributor explicitly rejected the over-broad target.",
                )
            ],
            rejected_answer_targets_added=["rat-every-path"],
            repair_records_added=["repair-ban-scope"],
        ),
        unresolved_items=[
            unresolved("issue", "i-ban-scope", "t0", "The corrected scope has not been acknowledged."),
            unresolved(
                "obligation",
                "o-acknowledge-ban-correction",
                "t2",
                "The account owes acknowledgement of the correction.",
            ),
            unresolved("repair", "repair-ban-scope", "t2", "Correction awaits acknowledgement."),
        ],
    )

    def orphan_replacement(candidate: dict[str, Any]) -> None:
        candidate["rejected_answer_targets"][0]["replacement_answer_target_id"] = "at-missing"

    invalid = invalid_document(
        fixture_id,
        [
            invalid_example(
                fixture_id,
                ledger,
                "orphan-replacement-answer-target",
                "orphan_replacement_answer_target",
                "The correction names a replacement answer target that does not exist.",
                orphan_replacement,
            )
        ],
    )
    return transcript, ledger, invalid


def build_existence_vs_security() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Fixture 3: existence remains distinct from institutional security."""

    fixture_id = "03-existence-versus-security"
    transcript = make_transcript(
        fixture_id,
        "A security answer is rejected as a substitute for an existence question.",
        [
            ("contributor", "Can civic liberty exist without secure courts?"),
            ("account", "Secure courts protect civic liberty."),
            ("contributor", "I asked whether liberty can exist without them, not whether courts protect it."),
        ],
    )
    p_exists = proposition(
        transcript,
        "p-liberty-exists",
        "Civic liberty can exist without secure courts.",
        "t0",
        "civic liberty exist without secure courts",
        "contributor",
        proposition_kind="modal",
        speech_act="question",
        polarity="open",
        epistemic_status="questioned",
        commitment_status="questioned_only",
        modality_type="possible",
        modality_strength="unknown",
    )
    p_secure = proposition(
        transcript,
        "p-courts-protect-liberty",
        "Secure courts protect civic liberty.",
        "t1",
        None,
        "account",
        proposition_kind="causal",
    )
    p_not_security_question = proposition(
        transcript,
        "p-not-security-question",
        "The live question is not whether courts protect liberty.",
        "t2",
        "not whether courts protect it",
        "contributor",
        speech_act="correction",
        polarity="negative",
    )
    i_exists = issue(
        transcript,
        "i-liberty-existence",
        "t0",
        "contributor",
        None,
        canonical_question="Can civic liberty exist without secure courts?",
        issue_type="polar",
        addressed_participant="account",
        related_proposition_ids=["p-liberty-exists", "p-courts-protect-liberty"],
        status="open",
        answer_requirements=[
            {"description": "Address possible existence, not merely security.", "requirement_type": "yes_no"}
        ],
    )
    targets = [
        answer_target(
            "at-security",
            "t1",
            "t1",
            issue_ids=["i-liberty-existence"],
            proposition_ids=["p-courts-protect-liberty"],
            target_status="rejected",
        ),
        answer_target(
            "at-existence",
            "t1",
            "t2",
            issue_ids=["i-liberty-existence"],
            proposition_ids=["p-liberty-exists"],
            target_status="confirmed",
        ),
    ]
    rejection = rejected_target(
        transcript,
        "rat-security",
        "at-security",
        "t2",
        None,
        replacement_id="at-existence",
        related_proposition_ids=["p-courts-protect-liberty", "p-liberty-exists"],
        rejection_kind="wrong_proposition",
    )
    ledger = base_ledger(
        transcript,
        propositions=[p_exists, p_secure, p_not_security_question],
        issue_states=[i_exists],
        participant_commitments=[
            commitment("c-existence-question", "contributor", "p-liberty-exists", "t0", stance="questioned"),
            commitment("c-security", "account", "p-courts-protect-liberty", "t1"),
            commitment(
                "c-not-security-target",
                "contributor",
                "p-not-security-question",
                "t2",
                basis="explicit_correction",
            ),
        ],
        conversational_obligations=[
            obligation(
                "o-answer-existence",
                "explicit_question",
                "t0",
                "account",
                "contributor",
                related_issue_ids=["i-liberty-existence"],
                related_proposition_ids=["p-liberty-exists"],
                expiry_policy="never_implicit",
            ),
            obligation(
                "o-acknowledge-existence-repair",
                "correction_requiring_acknowledgement",
                "t2",
                "account",
                "contributor",
                related_issue_ids=["i-liberty-existence"],
                related_proposition_ids=["p-liberty-exists", "p-not-security-question"],
                expiry_policy="never_implicit",
            ),
        ],
        proposition_relations=[
            relation(
                transcript,
                "r-security-fails-existence",
                ["p-courts-protect-liberty"],
                ["p-liberty-exists"],
                "fails_to_answer",
                "contributor",
                "t2",
                None,
                provenance_kind="human_correction",
            ),
            relation(
                transcript,
                "r-distinguishes-security",
                ["p-not-security-question"],
                ["p-courts-protect-liberty", "p-liberty-exists"],
                "distinguishes",
                "contributor",
                "t2",
                None,
            ),
        ],
        answer_targets=targets,
        rejected_answer_targets=[rejection],
        repair_records=[
            repair(
                transcript,
                "repair-existence-target",
                "t2",
                None,
                repair_type="answer_target_replacement",
                rejected_ids=["rat-security"],
                replacement_ids=["at-existence"],
                outcome="pending",
            )
        ],
        state_transition=transition(
            transcript,
            None,
            propositions_added=["p-not-security-question"],
            commitments_added=["c-not-security-target"],
            obligations_added=["o-acknowledge-existence-repair"],
            relations_added=["r-security-fails-existence", "r-distinguishes-security"],
            answer_targets_added=["at-existence"],
            answer_targets_updated=[
                status_update(
                    "at-security",
                    "apparent",
                    "rejected",
                    "The contributor explicitly restored the existence question.",
                )
            ],
            rejected_answer_targets_added=["rat-security"],
            repair_records_added=["repair-existence-target"],
        ),
        unresolved_items=[
            unresolved("proposition", "p-liberty-exists", "t0", "The existence proposition remains unanswered."),
            unresolved("issue", "i-liberty-existence", "t0", "The existence question remains open."),
            unresolved("obligation", "o-answer-existence", "t0", "A direct answer is still owed."),
            unresolved("repair", "repair-existence-target", "t2", "The replacement target awaits acknowledgement."),
        ],
    )

    def contradict_rejection(candidate: dict[str, Any]) -> None:
        candidate["answer_targets"][0]["target_status"] = "confirmed"

    invalid = invalid_document(
        fixture_id,
        [
            invalid_example(
                fixture_id,
                ledger,
                "rejected-target-still-confirmed",
                "rejected_target_marked_confirmed",
                "A target is simultaneously registered as rejected and confirmed.",
                contradict_rejection,
            )
        ],
    )
    return transcript, ledger, invalid


def build_desirability_vs_feasibility() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Fixture 4: feasibility evidence does not answer desirability."""

    fixture_id = "04-desirability-versus-feasibility"
    transcript = make_transcript(
        fixture_id,
        "A feasibility response is distinguished from a desirability question.",
        [
            ("contributor", "Would a rooftop garden be desirable?"),
            ("account", "The roof can support the weight."),
            ("contributor", "Feasibility is not desirability."),
        ],
    )
    p_desirable = proposition(
        transcript,
        "p-garden-desirable",
        "A rooftop garden would be desirable.",
        "t0",
        "a rooftop garden be desirable",
        "contributor",
        proposition_kind="evaluative",
        speech_act="question",
        polarity="open",
        epistemic_status="questioned",
        commitment_status="questioned_only",
        modality_type="hypothetical",
        modality_strength="unknown",
    )
    p_feasible = proposition(
        transcript,
        "p-roof-supports-weight",
        "The roof can support the garden's weight.",
        "t1",
        None,
        "account",
        proposition_kind="modal",
        modality_type="possible",
        modality_strength="strong",
    )
    p_distinction = proposition(
        transcript,
        "p-feasibility-not-desirability",
        "Feasibility is not desirability.",
        "t2",
        None,
        "contributor",
        proposition_kind="definitional",
        speech_act="correction",
        polarity="negative",
    )
    i_desirable = issue(
        transcript,
        "i-garden-desirable",
        "t0",
        "contributor",
        None,
        canonical_question="Would a rooftop garden be desirable?",
        issue_type="polar",
        addressed_participant="account",
        related_proposition_ids=["p-garden-desirable", "p-roof-supports-weight"],
        status="open",
        answer_requirements=[
            {"description": "Assess desirability independently of feasibility.", "requirement_type": "yes_no"}
        ],
    )
    targets = [
        answer_target(
            "at-feasibility",
            "t1",
            "t1",
            issue_ids=["i-garden-desirable"],
            proposition_ids=["p-roof-supports-weight"],
            target_status="rejected",
        ),
        answer_target(
            "at-desirability",
            "t1",
            "t2",
            issue_ids=["i-garden-desirable"],
            proposition_ids=["p-garden-desirable"],
            target_status="confirmed",
        ),
    ]
    ledger = base_ledger(
        transcript,
        propositions=[p_desirable, p_feasible, p_distinction],
        issue_states=[i_desirable],
        participant_commitments=[
            commitment("c-desirability-question", "contributor", "p-garden-desirable", "t0", stance="questioned"),
            commitment("c-feasibility", "account", "p-roof-supports-weight", "t1"),
            commitment(
                "c-feasibility-distinction",
                "contributor",
                "p-feasibility-not-desirability",
                "t2",
                basis="explicit_correction",
            ),
        ],
        conversational_obligations=[
            obligation(
                "o-answer-desirability",
                "explicit_question",
                "t0",
                "account",
                "contributor",
                related_issue_ids=["i-garden-desirable"],
                related_proposition_ids=["p-garden-desirable"],
                expiry_policy="never_implicit",
            )
        ],
        proposition_relations=[
            relation(
                transcript,
                "r-feasibility-fails-desirability",
                ["p-roof-supports-weight"],
                ["p-garden-desirable"],
                "fails_to_answer",
                "contributor",
                "t2",
                None,
                provenance_kind="human_correction",
            ),
            relation(
                transcript,
                "r-feasibility-distinction",
                ["p-feasibility-not-desirability"],
                ["p-roof-supports-weight", "p-garden-desirable"],
                "distinguishes",
                "contributor",
                "t2",
                None,
            ),
        ],
        answer_targets=targets,
        rejected_answer_targets=[
            rejected_target(
                transcript,
                "rat-feasibility",
                "at-feasibility",
                "t2",
                None,
                replacement_id="at-desirability",
                related_proposition_ids=["p-roof-supports-weight", "p-garden-desirable"],
            )
        ],
        repair_records=[
            repair(
                transcript,
                "repair-desirability-target",
                "t2",
                None,
                repair_type="answer_target_replacement",
                rejected_ids=["rat-feasibility"],
                replacement_ids=["at-desirability"],
                outcome="pending",
            )
        ],
        state_transition=transition(
            transcript,
            None,
            propositions_added=["p-feasibility-not-desirability"],
            commitments_added=["c-feasibility-distinction"],
            relations_added=["r-feasibility-fails-desirability", "r-feasibility-distinction"],
            answer_targets_added=["at-desirability"],
            answer_targets_updated=[
                status_update(
                    "at-feasibility",
                    "apparent",
                    "rejected",
                    "The contributor explicitly distinguished feasibility from desirability.",
                )
            ],
            rejected_answer_targets_added=["rat-feasibility"],
            repair_records_added=["repair-desirability-target"],
        ),
        unresolved_items=[
            unresolved("issue", "i-garden-desirable", "t0", "Desirability remains unanswered."),
            unresolved("obligation", "o-answer-desirability", "t0", "A desirability judgement is still owed."),
            unresolved("repair", "repair-desirability-target", "t2", "The distinction awaits acknowledgement."),
        ],
    )

    def add_bad_diagnostic(candidate: dict[str, Any]) -> None:
        candidate["proposition_relations"].append(
            relation(
                transcript,
                "r-invalid-substitution",
                ["p-roof-supports-weight"],
                ["p-garden-desirable"],
                "substitutes_for",
                "account",
                "t1",
                None,
                provenance_kind="transcript_extraction",
                analysis_basis="direct_semantic_content",
            )
        )
        candidate["state_transitions"][0]["relations_added"].append("r-invalid-substitution")

    invalid = invalid_document(
        fixture_id,
        [
            invalid_example(
                fixture_id,
                ledger,
                "diagnosis-as-transcript-fact",
                "diagnostic_relation_has_transcript_provenance",
                "An evaluator-only substitution diagnosis is recorded as a direct transcript fact.",
                add_bad_diagnostic,
            )
        ],
    )
    return transcript, ledger, invalid


def build_compound_allegation() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Fixture 5: conduct, causal result, and motive remain separable."""

    fixture_id = "05-compound-allegation"
    transcript = make_transcript(
        fixture_id,
        "A compound allegation is decomposed without treating decomposition as substitution.",
        [
            (
                "contributor",
                "The curator hid the notice, which delayed the vote, because she wanted the rival plan to win.",
            ),
            (
                "account",
                "Which part should I address first: the hiding, the delay, or the alleged motive?",
            ),
        ],
    )
    common = {
        "proposition_group_id": "g-compound-allegation",
        "derivation_kind": "compound_decomposition",
    }
    p_conduct = proposition(
        transcript,
        "p-curator-hid-notice",
        "The curator hid the notice.",
        "t0",
        "The curator hid the notice",
        "contributor",
        **common,
    )
    p_outcome = proposition(
        transcript,
        "p-vote-delayed",
        "The vote was delayed.",
        "t0",
        "delayed the vote",
        "contributor",
        temporal_type="past",
        **common,
    )
    p_cause = proposition(
        transcript,
        "p-hiding-caused-delay",
        "Hiding the notice caused the vote to be delayed.",
        "t0",
        "which delayed the vote",
        "contributor",
        proposition_kind="causal",
        temporal_type="past",
        **common,
    )
    p_motive = proposition(
        transcript,
        "p-curator-wanted-rival-win",
        "The curator wanted the rival plan to win.",
        "t0",
        "she wanted the rival plan to win",
        "contributor",
        proposition_kind="descriptive",
        temporal_type="past",
        **common,
    )
    p_choose_part = proposition(
        transcript,
        "p-select-part",
        "The contributor should select which part is addressed first.",
        "t1",
        None,
        "account",
        proposition_kind="proposal",
        speech_act="request",
        polarity="not_applicable",
        epistemic_status="not_applicable",
        commitment_status="not_applicable",
        modality_type="obligatory",
        modality_strength="weak",
    )
    i_parts = issue(
        transcript,
        "i-select-allegation-part",
        "t1",
        "account",
        None,
        canonical_question="Which part of the allegation should be addressed first?",
        issue_type="clarification",
        addressed_participant="contributor",
        related_proposition_ids=[
            "p-curator-hid-notice",
            "p-vote-delayed",
            "p-hiding-caused-delay",
            "p-curator-wanted-rival-win",
        ],
        status="open",
        live_alternatives=[
            {
                "alternative_id": "alt-conduct",
                "label": "Address alleged conduct.",
                "proposition_ids": ["p-curator-hid-notice"],
                "status": "live",
            },
            {
                "alternative_id": "alt-causation",
                "label": "Address alleged causation and outcome.",
                "proposition_ids": ["p-hiding-caused-delay", "p-vote-delayed"],
                "status": "live",
            },
            {
                "alternative_id": "alt-motive",
                "label": "Address alleged motive.",
                "proposition_ids": ["p-curator-wanted-rival-win"],
                "status": "live",
            },
        ],
        answer_requirements=[
            {"description": "Select one allegation component.", "requirement_type": "select_alternative"}
        ],
    )
    p_group = group(
        transcript,
        "g-compound-allegation",
        "compound_accusation",
        "t0",
        None,
        [
            ("p-curator-hid-notice", "conduct"),
            ("p-hiding-caused-delay", "cause"),
            ("p-vote-delayed", "outcome"),
            ("p-curator-wanted-rival-win", "motive"),
        ],
    )
    ledger = base_ledger(
        transcript,
        propositions=[p_conduct, p_outcome, p_cause, p_motive, p_choose_part],
        proposition_groups=[p_group],
        issue_states=[i_parts],
        participant_commitments=[
            commitment("c-conduct", "contributor", "p-curator-hid-notice", "t0"),
            commitment("c-outcome", "contributor", "p-vote-delayed", "t0"),
            commitment("c-causation", "contributor", "p-hiding-caused-delay", "t0"),
            commitment("c-motive", "contributor", "p-curator-wanted-rival-win", "t0"),
        ],
        conversational_obligations=[
            obligation(
                "o-select-allegation-part",
                "requested_clarification",
                "t1",
                "contributor",
                "account",
                related_issue_ids=["i-select-allegation-part"],
                related_proposition_ids=[
                    "p-curator-hid-notice",
                    "p-hiding-caused-delay",
                    "p-vote-delayed",
                    "p-curator-wanted-rival-win",
                ],
                expiry_policy="on_topic_abandonment",
            )
        ],
        proposition_relations=[
            relation(
                transcript,
                "r-request-targets-components",
                ["p-select-part"],
                [
                    "p-curator-hid-notice",
                    "p-hiding-caused-delay",
                    "p-vote-delayed",
                    "p-curator-wanted-rival-win",
                ],
                "targets",
                "account",
                "t1",
                None,
            )
        ],
        state_transition=transition(
            transcript,
            None,
            propositions_added=["p-select-part"],
            issue_states_added=["i-select-allegation-part"],
            obligations_added=["o-select-allegation-part"],
            relations_added=["r-request-targets-components"],
        ),
        unresolved_items=[
            unresolved(
                "issue",
                "i-select-allegation-part",
                "t1",
                "The contributor has not selected a component.",
            ),
            unresolved(
                "obligation",
                "o-select-allegation-part",
                "t1",
                "The requested clarification is unanswered.",
            ),
        ],
    )

    def collapse_group(candidate: dict[str, Any]) -> None:
        candidate["proposition_groups"][0]["members"] = [
            {
                "ordinal": 0,
                "proposition_id": "p-curator-hid-notice",
                "role": "member",
            }
        ]

    invalid = invalid_document(
        fixture_id,
        [
            invalid_example(
                fixture_id,
                ledger,
                "collapsed-compound-allegation",
                "compound_collapse",
                "The four-part allegation is reduced to a one-member compound group.",
                collapse_group,
                violation_kind="json_schema",
            )
        ],
    )
    return transcript, ledger, invalid


def build_live_counterfactual() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Fixture 6: evidence discussion does not close a counterfactual."""

    fixture_id = "06-live-counterfactual"
    transcript = make_transcript(
        fixture_id,
        "A counterfactual remains live while related evidence is discussed.",
        [
            ("contributor", "If the gate had stayed open, would the market have continued?"),
            ("account", "Attendance fell after the gate closed."),
            ("contributor", "That is evidence, but the counterfactual is still my question."),
        ],
    )
    p_counterfactual = proposition(
        transcript,
        "p-market-counterfactual",
        "If the gate had stayed open, the market would have continued.",
        "t0",
        None,
        "contributor",
        proposition_kind="counterfactual",
        speech_act="question",
        polarity="open",
        epistemic_status="hypothetical",
        commitment_status="questioned_only",
        modality_type="hypothetical",
        modality_strength="unknown",
        temporal_type="counterfactual",
        temporal_marker="If the gate had stayed open",
    )
    p_attendance = proposition(
        transcript,
        "p-attendance-fell",
        "Attendance fell after the gate closed.",
        "t1",
        None,
        "account",
        temporal_type="past",
        temporal_marker="after the gate closed",
    )
    p_evidence = proposition(
        transcript,
        "p-attendance-is-evidence",
        "The attendance decline is evidence relevant to the counterfactual.",
        "t2",
        "That is evidence",
        "contributor",
        proposition_kind="evaluative",
        derivation_kind="anaphora_resolution",
        derivation_sources=["p-attendance-fell"],
        normalisation_note="Resolves 'That' only to the immediately preceding attendance claim.",
        confidence=0.95,
    )
    p_still_live = proposition(
        transcript,
        "p-counterfactual-still-question",
        "The counterfactual remains the contributor's question.",
        "t2",
        "the counterfactual is still my question",
        "contributor",
        proposition_kind="descriptive",
    )
    i_counterfactual = issue(
        transcript,
        "i-market-counterfactual",
        "t0",
        "contributor",
        None,
        canonical_question="Would the market have continued if the gate had stayed open?",
        issue_type="counterfactual",
        addressed_participant="account",
        related_proposition_ids=["p-market-counterfactual", "p-attendance-fell"],
        status="open",
        answer_requirements=[
            {"description": "Address the counterfactual explicitly.", "requirement_type": "yes_no"}
        ],
    )
    ledger = base_ledger(
        transcript,
        propositions=[p_counterfactual, p_attendance, p_evidence, p_still_live],
        issue_states=[i_counterfactual],
        participant_commitments=[
            commitment(
                "c-counterfactual-question",
                "contributor",
                "p-market-counterfactual",
                "t0",
                stance="questioned",
            ),
            commitment("c-attendance", "account", "p-attendance-fell", "t1"),
            commitment("c-evidence", "contributor", "p-attendance-is-evidence", "t2"),
            commitment("c-still-live", "contributor", "p-counterfactual-still-question", "t2"),
        ],
        conversational_obligations=[
            obligation(
                "o-answer-counterfactual",
                "explicit_question",
                "t0",
                "account",
                "contributor",
                related_issue_ids=["i-market-counterfactual"],
                related_proposition_ids=["p-market-counterfactual"],
                expiry_policy="never_implicit",
            )
        ],
        proposition_relations=[
            relation(
                transcript,
                "r-evidence-supports-counterfactual",
                ["p-attendance-fell", "p-attendance-is-evidence"],
                ["p-market-counterfactual"],
                "supports",
                "contributor",
                "t2",
                "That is evidence",
                confidence=0.9,
            ),
            relation(
                transcript,
                "r-evidence-not-answer",
                ["p-attendance-fell"],
                ["p-market-counterfactual"],
                "fails_to_answer",
                "contributor",
                "t2",
                "the counterfactual is still my question",
                provenance_kind="human_correction",
            ),
        ],
        answer_targets=[
            answer_target(
                "at-counterfactual",
                "t1",
                "t1",
                issue_ids=["i-market-counterfactual"],
                proposition_ids=["p-market-counterfactual"],
                target_status="partially_addressed",
                confidence=0.9,
            )
        ],
        state_transition=transition(
            transcript,
            None,
            propositions_added=["p-attendance-is-evidence", "p-counterfactual-still-question"],
            commitments_added=["c-evidence", "c-still-live"],
            relations_added=["r-evidence-supports-counterfactual", "r-evidence-not-answer"],
        ),
        unresolved_items=[
            unresolved(
                "proposition",
                "p-market-counterfactual",
                "t0",
                "Evidence was discussed but the counterfactual was not answered.",
            ),
            unresolved(
                "issue",
                "i-market-counterfactual",
                "t0",
                "The counterfactual remains explicitly live.",
            ),
            unresolved(
                "obligation",
                "o-answer-counterfactual",
                "t0",
                "The account still owes a direct answer.",
            ),
        ],
    )

    def prematurely_resolve(candidate: dict[str, Any]) -> None:
        candidate["issue_states"][0]["status"] = "answered"
        candidate["issue_states"][0]["resolution_turn_id"] = "t1"
        candidate["issue_states"][0]["resolution_type"] = "direct_answer"

    invalid = invalid_document(
        fixture_id,
        [
            invalid_example(
                fixture_id,
                ledger,
                "premature-counterfactual-resolution",
                "answered_issue_listed_unresolved",
                "The issue is marked answered while the same ledger retains it as explicitly unresolved.",
                prematurely_resolve,
            )
        ],
    )
    return transcript, ledger, invalid


def build_quoted_without_endorsement() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Fixture 7: quoted speech is attributed without speaker endorsement."""

    fixture_id = "07-quoted-without-endorsement"
    transcript = make_transcript(
        fixture_id,
        "A participant quotes a pamphlet without endorsing its claim.",
        [
            ("contributor", 'The pamphlet says, "The moon governs harvest prices."'),
            ("account", "Quoting that claim does not show that you endorse it."),
        ],
        extra_participants=[{"participant_id": "pamphlet", "role": "quoted_source"}],
    )
    p_quote = proposition(
        transcript,
        "p-pamphlet-moon",
        "The moon governs harvest prices.",
        "t0",
        "The moon governs harvest prices.",
        "contributor",
        proposition_kind="quoted_claim",
        speech_act="quotation",
        epistemic_status="quoted_only",
        commitment_status="attributed_to_another",
        lifecycle_status="introduced",
        speaker_kind="attributor",
        attributed_participant_id="pamphlet",
    )
    p_no_endorsement = proposition(
        transcript,
        "p-quotation-no-endorsement",
        "Quoting the claim does not establish the contributor's endorsement.",
        "t1",
        None,
        "account",
        polarity="negative",
    )
    ledger = base_ledger(
        transcript,
        propositions=[p_quote, p_no_endorsement],
        participant_commitments=[
            commitment(
                "c-attribution-only",
                "contributor",
                "p-pamphlet-moon",
                "t0",
                stance="attributed_to_another",
                basis="attribution_only",
            ),
            commitment("c-no-endorsement", "account", "p-quotation-no-endorsement", "t1"),
        ],
        proposition_relations=[
            relation(
                transcript,
                "r-quotation-clarified",
                ["p-quotation-no-endorsement"],
                ["p-pamphlet-moon"],
                "clarifies",
                "account",
                "t1",
                None,
            )
        ],
        state_transition=transition(
            transcript,
            None,
            propositions_added=["p-quotation-no-endorsement"],
            commitments_added=["c-no-endorsement"],
            relations_added=["r-quotation-clarified"],
        ),
    )

    def invent_endorsement(candidate: dict[str, Any]) -> None:
        candidate["propositions"][0]["commitment_status"] = "speaker_committed"

    invalid = invalid_document(
        fixture_id,
        [
            invalid_example(
                fixture_id,
                ledger,
                "quoted-claim-treated-as-endorsement",
                "quoted_claim_implies_commitment",
                "The ledger silently promotes quotation to the contributor's assertion.",
                invent_endorsement,
            )
        ],
    )
    return transcript, ledger, invalid


def build_rhetorical_no_stable_issue() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Fixture 8: rhetorical criticism can yield no stable polar issue."""

    fixture_id = "08-rhetorical-no-stable-issue"
    transcript = make_transcript(
        fixture_id,
        "Rhetorical frustration is retained without inventing a factual yes-or-no issue.",
        [
            ("contributor", "Oh splendid - another meeting about scheduling meetings!"),
            ("account", "That sounds like frustration, not a factual yes-or-no claim."),
        ],
    )
    p_rhetorical = proposition(
        transcript,
        "p-rhetorical-meeting",
        "Another meeting about scheduling meetings!",
        "t0",
        None,
        "contributor",
        proposition_kind="rhetorical",
        speech_act="rhetorical",
        polarity="not_applicable",
        epistemic_status="not_applicable",
        commitment_status="not_applicable",
        lifecycle_status="introduced",
    )
    p_classification = proposition(
        transcript,
        "p-no-factual-polar-claim",
        "The contribution expresses frustration rather than a factual polar claim.",
        "t1",
        None,
        "account",
        proposition_kind="expressive",
    )
    i_none = issue(
        transcript,
        "i-no-stable-issue",
        "t0",
        "contributor",
        None,
        canonical_question=None,
        issue_type="no_stable_issue",
        addressed_participant=None,
        related_proposition_ids=["p-rhetorical-meeting"],
        status="no_stable_issue",
        resolution_turn_id="t0",
        resolution_type="no_stable_issue",
    )
    ledger = base_ledger(
        transcript,
        propositions=[p_rhetorical, p_classification],
        issue_states=[i_none],
        participant_commitments=[
            commitment("c-classification", "account", "p-no-factual-polar-claim", "t1")
        ],
        proposition_relations=[
            relation(
                transcript,
                "r-classifies-rhetoric",
                ["p-no-factual-polar-claim"],
                ["p-rhetorical-meeting"],
                "clarifies",
                "account",
                "t1",
                None,
            )
        ],
        state_transition=transition(
            transcript,
            None,
            propositions_added=["p-no-factual-polar-claim"],
            commitments_added=["c-classification"],
            relations_added=["r-classifies-rhetoric"],
        ),
        resolved_items=[resolved("issue", "i-no-stable-issue", "t0", "no_stable_issue")],
        extraction_status={
            "abstentions": ["no_stable_issue"],
            "status": "abstained",
            "unsupported_inferences_rejected": 1,
        },
    )

    def force_polar_issue(candidate: dict[str, Any]) -> None:
        candidate["issue_states"][0]["canonical_question"] = "Are scheduling meetings always bad?"
        candidate["issue_states"][0]["issue_type"] = "polar"

    invalid = invalid_document(
        fixture_id,
        [
            invalid_example(
                fixture_id,
                ledger,
                "invented-polar-issue",
                "schema:$.issue_states[0].canonical_question",
                "A no-stable-issue state is forced into a factual polar question.",
                force_polar_issue,
                violation_kind="json_schema",
            )
        ],
    )
    return transcript, ledger, invalid


def build_clarification_then_answer() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Fixture 9: an answer resolves the account's clarification request."""

    fixture_id = "09-clarification-then-answer"
    transcript = make_transcript(
        fixture_id,
        "An account-requested referent clarification receives an apparent direct answer.",
        [
            ("contributor", "The new board made it fairer."),
            ("account", "What does 'it' refer to?"),
            ("contributor", "The permit appeal process."),
        ],
    )
    p_fairer = proposition(
        transcript,
        "p-board-made-process-fairer",
        "The new board made the permit appeal process fairer.",
        "t0",
        None,
        "contributor",
        proposition_kind="comparative",
        lifecycle_status="qualified",
        derivation_kind="anaphora_resolution",
        derivation_sources=["p-it-refers-to-process"],
        normalisation_note="The final-turn answer supplies the previously unresolved referent of 'it'.",
        extra_spans=[span(transcript, "t2", None)],
    )
    p_question = proposition(
        transcript,
        "p-it-has-referent",
        "The pronoun 'it' has a referent that can be identified.",
        "t1",
        None,
        "account",
        proposition_kind="question_presupposition",
        speech_act="question",
        polarity="open",
        epistemic_status="presupposed_only",
        commitment_status="questioned_only",
        lifecycle_status="resolved",
    )
    p_referent = proposition(
        transcript,
        "p-it-refers-to-process",
        "The pronoun 'it' refers to the permit appeal process.",
        "t2",
        None,
        "contributor",
        proposition_kind="definitional",
    )
    i_referent = issue(
        transcript,
        "i-referent-it",
        "t1",
        "account",
        None,
        canonical_question="What does 'it' refer to?",
        issue_type="clarification",
        addressed_participant="contributor",
        related_proposition_ids=["p-it-has-referent", "p-it-refers-to-process"],
        status="answered",
        answer_requirements=[
            {"description": "Supply the referent of 'it'.", "requirement_type": "clarify_reference"}
        ],
        resolution_turn_id="t2",
        resolution_type="direct_answer",
    )
    o_clarify = obligation(
        "o-clarify-it",
        "requested_clarification",
        "t1",
        "contributor",
        "account",
        related_issue_ids=["i-referent-it"],
        related_proposition_ids=["p-it-has-referent"],
        status="satisfied",
        resolved_by_turn_id="t2",
    )
    r_answer = relation(
        transcript,
        "r-answer-referent",
        ["p-it-refers-to-process"],
        ["p-it-has-referent"],
        "answers",
        "contributor",
        "t2",
        None,
    )
    r_clarifies = relation(
        transcript,
        "r-clarifies-fairer",
        ["p-it-refers-to-process"],
        ["p-board-made-process-fairer"],
        "clarifies",
        "contributor",
        "t2",
        None,
    )
    ledger = base_ledger(
        transcript,
        propositions=[p_fairer, p_question, p_referent],
        issue_states=[i_referent],
        participant_commitments=[
            commitment("c-fairer", "contributor", "p-board-made-process-fairer", "t0"),
            commitment("c-referent-question", "account", "p-it-has-referent", "t1", stance="questioned"),
            commitment("c-referent-answer", "contributor", "p-it-refers-to-process", "t2"),
        ],
        conversational_obligations=[o_clarify],
        proposition_relations=[r_answer, r_clarifies],
        answer_targets=[
            answer_target(
                "at-referent",
                "t2",
                "t2",
                issue_ids=["i-referent-it"],
                proposition_ids=["p-it-has-referent"],
                target_status="confirmed",
            )
        ],
        state_transition=transition(
            transcript,
            None,
            propositions_added=["p-it-refers-to-process"],
            propositions_updated=[
                status_update(
                    "p-board-made-process-fairer",
                    "introduced",
                    "qualified",
                    "The pronoun was resolved by the clarification answer.",
                ),
                status_update("p-it-has-referent", "live", "resolved", "The referent was supplied."),
            ],
            issue_states_updated=[
                status_update("i-referent-it", "open", "answered", "The referent was supplied.")
            ],
            commitments_added=["c-referent-answer"],
            obligations_updated=[
                status_update("o-clarify-it", "open", "satisfied", "Clarification was supplied.")
            ],
            relations_added=["r-answer-referent", "r-clarifies-fairer"],
            answer_targets_added=["at-referent"],
            items_resolved=["p-it-has-referent", "i-referent-it", "o-clarify-it"],
        ),
        resolved_items=[
            resolved("proposition", "p-it-has-referent", "t2", "clarification_answer"),
            resolved("issue", "i-referent-it", "t2", "direct_answer"),
            resolved("obligation", "o-clarify-it", "t2", "clarification_supplied"),
        ],
    )

    def orphan_relation(candidate: dict[str, Any]) -> None:
        candidate["proposition_relations"][0]["target_proposition_ids"] = ["p-missing"]

    invalid = invalid_document(
        fixture_id,
        [
            invalid_example(
                fixture_id,
                ledger,
                "orphan-answer-relation",
                "orphan_relation",
                "The direct-answer relation targets a proposition absent from the ledger.",
                orphan_relation,
            )
        ],
    )
    return transcript, ledger, invalid


def build_unrelated_topic_change() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Fixture 10: the same author explicitly changes to an unrelated topic."""

    fixture_id = "10-unrelated-topic-change"
    transcript = make_transcript(
        fixture_id,
        "The same contributor leaves a library issue and introduces an unrelated bicycle fact.",
        [
            ("contributor", "Should the library extend its hours?"),
            ("account", "What closing time would you prefer?"),
            ("contributor", "Unrelatedly, my bicycle bell is broken."),
        ],
    )
    p_library = proposition(
        transcript,
        "p-library-extend-hours",
        "The library should extend its hours.",
        "t0",
        "the library extend its hours",
        "contributor",
        proposition_kind="normative",
        speech_act="question",
        polarity="open",
        epistemic_status="questioned",
        commitment_status="questioned_only",
        lifecycle_status="abandoned",
        modality_type="obligatory",
        modality_strength="unknown",
    )
    p_closing = proposition(
        transcript,
        "p-preferred-closing-time",
        "The contributor has a preferred library closing time.",
        "t1",
        None,
        "account",
        proposition_kind="question_presupposition",
        speech_act="question",
        polarity="open",
        epistemic_status="presupposed_only",
        commitment_status="questioned_only",
        lifecycle_status="abandoned",
    )
    p_bell = proposition(
        transcript,
        "p-bicycle-bell-broken",
        "The contributor's bicycle bell is broken.",
        "t2",
        "my bicycle bell is broken",
        "contributor",
    )
    i_hours = issue(
        transcript,
        "i-library-hours",
        "t0",
        "contributor",
        None,
        canonical_question="Should the library extend its hours?",
        issue_type="polar",
        addressed_participant="account",
        related_proposition_ids=["p-library-extend-hours"],
        status="abandoned",
        resolution_turn_id="t2",
        resolution_type="topic_change",
    )
    i_closing = issue(
        transcript,
        "i-closing-time",
        "t1",
        "account",
        None,
        canonical_question="What closing time would the contributor prefer?",
        issue_type="clarification",
        addressed_participant="contributor",
        related_proposition_ids=["p-preferred-closing-time"],
        status="abandoned",
        answer_requirements=[
            {"description": "Supply a preferred closing time.", "requirement_type": "supply_value"}
        ],
        resolution_turn_id="t2",
        resolution_type="topic_change",
    )
    o_closing = obligation(
        "o-closing-time",
        "explicit_question",
        "t1",
        "contributor",
        "account",
        related_issue_ids=["i-closing-time"],
        related_proposition_ids=["p-preferred-closing-time"],
        status="expired",
        resolved_by_turn_id="t2",
        expiry_policy="on_topic_abandonment",
    )
    r_topic = relation(
        transcript,
        "r-topic-change",
        ["p-bicycle-bell-broken"],
        ["p-library-extend-hours", "p-preferred-closing-time"],
        "changes_topic",
        "contributor",
        "t2",
        None,
    )
    ledger = base_ledger(
        transcript,
        propositions=[p_library, p_closing, p_bell],
        issue_states=[i_hours, i_closing],
        participant_commitments=[
            commitment("c-library-question", "contributor", "p-library-extend-hours", "t0", stance="questioned"),
            commitment("c-closing-question", "account", "p-preferred-closing-time", "t1", stance="questioned"),
            commitment("c-bell", "contributor", "p-bicycle-bell-broken", "t2"),
        ],
        conversational_obligations=[o_closing],
        proposition_relations=[r_topic],
        repair_records=[
            repair(
                transcript,
                "repair-topic-boundary",
                "t2",
                None,
                repair_type="topic_boundary",
                rejected_ids=[],
                replacement_ids=[],
                outcome="incorporated",
            )
        ],
        state_transition=transition(
            transcript,
            None,
            propositions_added=["p-bicycle-bell-broken"],
            propositions_updated=[
                status_update("p-library-extend-hours", "live", "abandoned", "Explicit topic change."),
                status_update("p-preferred-closing-time", "live", "abandoned", "Explicit topic change."),
            ],
            issue_states_updated=[
                status_update("i-library-hours", "open", "abandoned", "Explicit topic change."),
                status_update("i-closing-time", "open", "abandoned", "Explicit topic change."),
            ],
            commitments_added=["c-bell"],
            obligations_updated=[
                status_update("o-closing-time", "open", "expired", "The topic was explicitly abandoned.")
            ],
            relations_added=["r-topic-change"],
            repair_records_added=["repair-topic-boundary"],
            items_resolved=[
                "p-library-extend-hours",
                "p-preferred-closing-time",
                "i-library-hours",
                "i-closing-time",
                "o-closing-time",
            ],
        ),
        resolved_items=[
            resolved("proposition", "p-library-extend-hours", "t2", "topic_change"),
            resolved("proposition", "p-preferred-closing-time", "t2", "topic_change"),
            resolved("issue", "i-library-hours", "t2", "topic_change"),
            resolved("issue", "i-closing-time", "t2", "topic_change"),
            resolved("obligation", "o-closing-time", "t2", "topic_abandonment"),
        ],
    )

    def duplicate_resolved_as_open(candidate: dict[str, Any]) -> None:
        candidate["unresolved_items"].append(
            unresolved("issue", "i-closing-time", "t1", "Incorrectly retained after explicit topic change.")
        )

    invalid = invalid_document(
        fixture_id,
        [
            invalid_example(
                fixture_id,
                ledger,
                "resolved-and-unresolved-topic",
                "item_both_resolved_and_unresolved",
                "The closing-time issue appears in both resolved and unresolved sets.",
                duplicate_resolved_as_open,
            )
        ],
    )
    return transcript, ledger, invalid


def build_partial_concession_rebuttal() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Fixture 11: one premise is conceded while its conclusion is rebutted."""

    fixture_id = "11-partial-concession-rebuttal"
    transcript = make_transcript(
        fixture_id,
        "A participant concedes lower cost without conceding that lower cost proves superiority.",
        [
            ("contributor", "The plan is cheaper, so it must be better."),
            ("account", "I concede it is cheaper, but lower cost alone does not make it better."),
        ],
    )
    p_cheaper = proposition(
        transcript,
        "p-plan-cheaper",
        "The plan is cheaper.",
        "t0",
        "The plan is cheaper",
        "contributor",
        proposition_kind="comparative",
        proposition_group_id="g-cheaper-better",
        derivation_kind="compound_decomposition",
    )
    p_better = proposition(
        transcript,
        "p-plan-better",
        "The plan must be better.",
        "t0",
        "it must be better",
        "contributor",
        proposition_kind="evaluative",
        lifecycle_status="challenged",
        proposition_group_id="g-cheaper-better",
        modality_type="necessary",
        modality_strength="strong",
        derivation_kind="compound_decomposition",
    )
    p_not_sufficient = proposition(
        transcript,
        "p-cost-not-sufficient",
        "Lower cost alone does not make the plan better.",
        "t1",
        "lower cost alone does not make it better",
        "account",
        proposition_kind="causal",
        speech_act="denial",
        polarity="negative",
    )
    i_inference = issue(
        transcript,
        "i-cheaper-implies-better",
        "t0",
        "contributor",
        None,
        canonical_question="Does the plan's lower cost by itself make it better?",
        issue_type="other",
        addressed_participant="account",
        related_proposition_ids=["p-plan-cheaper", "p-plan-better", "p-cost-not-sufficient"],
        status="challenged",
        answer_requirements=[
            {"description": "Assess the premise-to-conclusion inference.", "requirement_type": "supply_reason"}
        ],
    )
    ledger = base_ledger(
        transcript,
        propositions=[p_cheaper, p_better, p_not_sufficient],
        proposition_groups=[
            group(
                transcript,
                "g-cheaper-better",
                "premise_conclusion",
                "t0",
                None,
                [("p-plan-cheaper", "premise"), ("p-plan-better", "conclusion")],
            )
        ],
        issue_states=[i_inference],
        participant_commitments=[
            commitment("c-contributor-cheaper", "contributor", "p-plan-cheaper", "t0"),
            commitment("c-contributor-better", "contributor", "p-plan-better", "t0"),
            commitment(
                "c-account-cheaper",
                "account",
                "p-plan-cheaper",
                "t1",
                stance="conceded",
                basis="explicit_concession",
            ),
            commitment("c-account-rebuttal", "account", "p-cost-not-sufficient", "t1"),
        ],
        proposition_relations=[
            relation(
                transcript,
                "r-cheaper-supports-better",
                ["p-plan-cheaper"],
                ["p-plan-better"],
                "supports",
                "contributor",
                "t0",
                None,
            ),
            relation(
                transcript,
                "r-rebuttal-challenges-better",
                ["p-cost-not-sufficient"],
                ["p-plan-better"],
                "challenges",
                "account",
                "t1",
                None,
            ),
        ],
        state_transition=transition(
            transcript,
            None,
            propositions_added=["p-cost-not-sufficient"],
            propositions_updated=[
                status_update("p-plan-better", "live", "challenged", "The account rebuts the inference.")
            ],
            issue_states_updated=[
                status_update("i-cheaper-implies-better", "open", "challenged", "Premise conceded; inference disputed.")
            ],
            commitments_added=["c-account-cheaper", "c-account-rebuttal"],
            relations_added=["r-rebuttal-challenges-better"],
        ),
        unresolved_items=[
            unresolved(
                "proposition",
                "p-plan-better",
                "t0",
                "The conclusion is challenged despite the premise being conceded.",
            ),
            unresolved(
                "issue",
                "i-cheaper-implies-better",
                "t0",
                "The sufficiency of the premise remains disputed.",
            ),
        ],
    )

    def invalid_reopen(candidate: dict[str, Any]) -> None:
        candidate["propositions"][1]["lifecycle_status"] = "live"
        candidate["state_transitions"][0]["propositions_updated"] = [
            status_update(
                "p-plan-better",
                "resolved",
                "live",
                "Silently reopen a resolved proposition under the same ID.",
            )
        ]

    invalid = invalid_document(
        fixture_id,
        [
            invalid_example(
                fixture_id,
                ledger,
                "silently-reopened-proposition",
                "invalid_lifecycle_transition",
                "A resolved proposition returns to live under the same identifier.",
                invalid_reopen,
            )
        ],
    )
    return transcript, ledger, invalid


def build_ambiguous_pronoun_abstention() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Fixture 12: ambiguous attribution is preserved as unknown."""

    fixture_id = "12-ambiguous-pronoun-abstention"
    transcript = make_transcript(
        fixture_id,
        "An ambiguous pronoun requires attribution abstention rather than a guessed referent.",
        [
            ("contributor", "Morgan told Riley that they had misplaced the key."),
            ("account", "I cannot tell whether 'they' means Morgan or Riley."),
        ],
        extra_participants=[
            {"participant_id": "morgan", "role": "reported_source"},
            {"participant_id": "riley", "role": "reported_source"},
        ],
    )
    p_report = proposition(
        transcript,
        "p-ambiguous-key-report",
        "Either Morgan or Riley had misplaced the key.",
        "t0",
        "they had misplaced the key",
        "contributor",
        proposition_kind="reported_claim",
        speech_act="report",
        epistemic_status="reported_only",
        commitment_status="left_uncertain",
        lifecycle_status="introduced",
        derivation_kind="direct_span",
        normalisation_note="Preserves both grammatically available referents without selecting one.",
        confidence=0.5,
        uncertainty_reason="The plural-form pronoun can refer to Morgan or Riley in this sentence.",
        speaker_kind="attributor",
        attributed_participant_id=None,
    )
    p_cannot_resolve = proposition(
        transcript,
        "p-pronoun-unresolved",
        "The pronoun's referent cannot be determined from the available context.",
        "t1",
        None,
        "account",
        epistemic_status="uncertain",
        commitment_status="left_uncertain",
        confidence=1.0,
        uncertainty_reason="The transcript supplies no disambiguating information.",
    )
    i_pronoun = issue(
        transcript,
        "i-pronoun-referent",
        "t0",
        "contributor",
        None,
        canonical_question="Who does 'they' refer to: Morgan or Riley?",
        issue_type="clarification",
        addressed_participant="contributor",
        related_proposition_ids=["p-ambiguous-key-report"],
        status="open",
        live_alternatives=[
            {
                "alternative_id": "alt-morgan",
                "label": "The pronoun refers to Morgan.",
                "proposition_ids": [],
                "status": "unknown",
            },
            {
                "alternative_id": "alt-riley",
                "label": "The pronoun refers to Riley.",
                "proposition_ids": [],
                "status": "unknown",
            },
        ],
        answer_requirements=[
            {"description": "Supply disambiguating context.", "requirement_type": "clarify_reference"}
        ],
        confidence=0.5,
    )
    w_ambiguous = warning(
        "w-ambiguous-pronoun",
        "t0",
        "ambiguous_attribution",
        "The pronoun cannot be assigned to Morgan or Riley from the transcript prefix.",
    )
    ledger = base_ledger(
        transcript,
        propositions=[p_report, p_cannot_resolve],
        issue_states=[i_pronoun],
        participant_commitments=[
            commitment(
                "c-reported-ambiguously",
                "contributor",
                "p-ambiguous-key-report",
                "t0",
                stance="left_uncertain",
                basis="uncertain",
                confidence=0.5,
                uncertainty_reason="The reported subject is ambiguous.",
            ),
            commitment(
                "c-account-cannot-resolve",
                "account",
                "p-pronoun-unresolved",
                "t1",
                stance="left_uncertain",
                basis="uncertain",
                uncertainty_reason="No disambiguating context is available.",
            ),
        ],
        proposition_relations=[
            relation(
                transcript,
                "r-flags-ambiguity",
                ["p-pronoun-unresolved"],
                ["p-ambiguous-key-report"],
                "clarifies",
                "account",
                "t1",
                None,
            )
        ],
        state_transition=transition(
            transcript,
            None,
            propositions_added=["p-pronoun-unresolved"],
            commitments_added=["c-account-cannot-resolve"],
            relations_added=["r-flags-ambiguity"],
        ),
        unresolved_items=[
            unresolved(
                "proposition",
                "p-ambiguous-key-report",
                "t0",
                "The report's subject remains ambiguous.",
            ),
            unresolved(
                "issue",
                "i-pronoun-referent",
                "t0",
                "No referent can be selected from the available context.",
            ),
        ],
        extraction_status={
            "abstentions": [
                "ambiguous_attribution",
                "unresolved_reference",
                "unsupported_inference_rejected",
            ],
            "status": "partial",
            "unsupported_inferences_rejected": 2,
        },
        warnings=[w_ambiguous],
    )

    def guess_morgan(candidate: dict[str, Any]) -> None:
        candidate["propositions"][0]["canonical_text"] = "Morgan had misplaced the key."
        candidate["propositions"][0]["speaker_or_attributor"]["attributed_participant_id"] = "morgan"
        candidate["propositions"][0]["derivation"]["kind"] = "anaphora_resolution"
        candidate["propositions"][0]["confidence"] = 1.0
        candidate["propositions"][0]["uncertainty_reason"] = None

    invalid = invalid_document(
        fixture_id,
        [
            invalid_example(
                fixture_id,
                ledger,
                "guessed-pronoun-referent",
                "unsupported_pronoun_disambiguation",
                "The ledger guesses Morgan despite the explicit unresolved ambiguity.",
                guess_morgan,
            )
        ],
    )
    return transcript, ledger, invalid


BUILDERS: tuple[
    Callable[[], tuple[dict[str, Any], dict[str, Any], dict[str, Any]]], ...
] = (
    build_direct_question_answer,
    build_explicit_correction,
    build_existence_vs_security,
    build_desirability_vs_feasibility,
    build_compound_allegation,
    build_live_counterfactual,
    build_quoted_without_endorsement,
    build_rhetorical_no_stable_issue,
    build_clarification_then_answer,
    build_unrelated_topic_change,
    build_partial_concession_rebuttal,
    build_ambiguous_pronoun_abstention,
)


def validate_expected_ledger(
    transcript: dict[str, Any],
    ledger: dict[str, Any],
    schema: dict[str, Any],
) -> None:
    """Validate schema, hashes, bounds, and exact spans for one valid fixture."""

    try:
        import jsonschema
    except ImportError as exc:  # pragma: no cover - repository test env provides it
        raise RuntimeError("jsonschema is required to validate generated fixtures") from exc

    validator_class = getattr(jsonschema, "Draft202012Validator", jsonschema.Draft7Validator)
    turns = {turn["turn_id"]: turn for turn in transcript["turns"]}
    history = transcript.get("ledger_history")
    if not isinstance(history, list) or len(history) != ledger["as_of_turn_index"]:
        raise ValueError(f"{transcript['fixture_id']}: incomplete ledger history")
    snapshots = [*history, ledger]
    previous: dict[str, Any] | None = None
    chain_identity = (
        ledger["schema_version"],
        transcript["conversation_key"],
        transcript["root_post_id"],
    )
    for boundary, snapshot in enumerate(snapshots):
        validator_class(schema).validate(snapshot)
        if (
            snapshot["schema_version"],
            snapshot["conversation_key"],
            snapshot["root_post_id"],
        ) != chain_identity:
            raise ValueError(f"{transcript['fixture_id']}: ledger chain identity changed")
        if ledger_sha256(snapshot) != snapshot["ledger_sha256"]:
            raise ValueError(
                f"{transcript['fixture_id']}: non-deterministic ledger hash at turn {boundary}"
            )
        if snapshot["as_of_turn_index"] != boundary:
            raise ValueError(f"{transcript['fixture_id']}: non-contiguous ledger history")
        if snapshot["target_turn_id"] != transcript["turns"][boundary]["turn_id"]:
            raise ValueError(f"{transcript['fixture_id']}: target boundary mismatch")
        expected_previous_hash = previous["ledger_sha256"] if previous is not None else None
        if snapshot["previous_ledger_sha256"] != expected_previous_hash:
            raise ValueError(f"{transcript['fixture_id']}: predecessor chain hash mismatch")
        transition_record = snapshot["state_transitions"][0]
        if transition_record["from_ledger_sha256"] != expected_previous_hash:
            raise ValueError(f"{transcript['fixture_id']}: transition predecessor hash mismatch")
        replayed = apply_state_patch(previous, transition_record["state_patch"])
        if replayed != state_records(snapshot):
            raise ValueError(
                f"{transcript['fixture_id']}: patch does not reconstruct turn {boundary}"
            )
        previous = snapshot

    for snapshot in snapshots:
        evidence_containers: list[dict[str, Any]] = []
        for field in (
            "propositions",
            "proposition_groups",
            "issue_states",
            "proposition_relations",
            "rejected_answer_targets",
            "repair_records",
        ):
            evidence_containers.extend(snapshot[field])
        for container in evidence_containers:
            for evidence in container["exact_evidence_spans"]:
                turn = turns[evidence["turn_id"]]
                if turn["turn_index"] > snapshot["as_of_turn_index"]:
                    raise ValueError(f"{transcript['fixture_id']}: future evidence span")
                start = evidence["start_char"]
                end = evidence["end_char"]
                if not (0 <= start < end <= len(turn["text"])):
                    raise ValueError(
                        f"{transcript['fixture_id']}: evidence bounds invalid in {evidence['turn_id']}"
                    )
                actual = turn["text"][start:end]
                if actual != evidence["exact_text"]:
                    raise ValueError(
                        f"{transcript['fixture_id']}: evidence mismatch in {evidence['turn_id']}"
                    )

        turn_ref_ids = {turn["turn_id"] for turn in snapshot["turn_refs"]}
        expected_turn_ids = {
            turn_id
            for turn_id, turn in turns.items()
            if turn["turn_index"] <= snapshot["as_of_turn_index"]
        }
        if turn_ref_ids != expected_turn_ids:
            raise ValueError(f"{transcript['fixture_id']}: turn-ref set mismatch")
        for turn_ref in snapshot["turn_refs"]:
            source = turns[turn_ref["turn_id"]]
            if turn_ref["turn_index"] > snapshot["as_of_turn_index"]:
                raise ValueError(f"{transcript['fixture_id']}: future turn ref")
            expected_text_hash = hashlib.sha256(source["text"].encode("utf-8")).hexdigest()
            if turn_ref["text_sha256"] != expected_text_hash:
                raise ValueError(f"{transcript['fixture_id']}: turn text hash mismatch")


def build_documents(schema: dict[str, Any]) -> dict[str, dict[str, bytes]]:
    """Build and validate every fixture document in memory."""

    documents: dict[str, dict[str, bytes]] = {}
    for builder in BUILDERS:
        transcript, ledger, invalid = builder()
        fixture_id = transcript["fixture_id"]
        if fixture_id in documents:
            raise ValueError(f"duplicate fixture identifier: {fixture_id}")
        if invalid["fixture_id"] != fixture_id:
            raise ValueError(f"{fixture_id}: invalid-document identity mismatch")
        validate_expected_ledger(transcript, ledger, schema)
        documents[fixture_id] = {
            "expected-ledger.json": json_bytes(ledger),
            "invalid-ledger-examples.json": json_bytes(invalid),
            "transcript.json": json_bytes(transcript),
        }
    if len(documents) != 12:
        raise ValueError(f"expected exactly 12 fixtures, built {len(documents)}")
    return documents


def write_documents(output_root: Path, documents: dict[str, dict[str, bytes]]) -> None:
    """Write the generated pack, refusing unexpected pre-existing entries."""

    output_root.mkdir(parents=True, exist_ok=True)
    expected_dirs = set(documents)
    unexpected = {item.name for item in output_root.iterdir()} - expected_dirs
    if unexpected:
        raise ValueError(f"unexpected entries under {output_root}: {sorted(unexpected)}")
    for fixture_id, files in sorted(documents.items()):
        fixture_dir = output_root / fixture_id
        fixture_dir.mkdir(exist_ok=True)
        unexpected_files = {item.name for item in fixture_dir.iterdir()} - set(files)
        if unexpected_files:
            raise ValueError(f"unexpected files in {fixture_dir}: {sorted(unexpected_files)}")
        for filename, content in sorted(files.items()):
            (fixture_dir / filename).write_bytes(content)


def check_documents(output_root: Path, documents: dict[str, dict[str, bytes]]) -> None:
    """Verify the checked-in pack without writing to it."""

    expected_paths = {
        output_root / fixture_id / filename
        for fixture_id, files in documents.items()
        for filename in files
    }
    actual_paths = {path for path in output_root.glob("*/*") if path.is_file()}
    if expected_paths != actual_paths:
        missing = sorted(str(path) for path in expected_paths - actual_paths)
        unexpected = sorted(str(path) for path in actual_paths - expected_paths)
        raise ValueError(f"fixture path mismatch; missing={missing}, unexpected={unexpected}")
    for fixture_id, files in sorted(documents.items()):
        for filename, expected in sorted(files.items()):
            path = output_root / fixture_id / filename
            actual = path.read_bytes()
            if actual != expected:
                raise ValueError(f"generated fixture differs: {path}")


def parse_args() -> argparse.Namespace:
    """Parse the generator's deliberately narrow command line."""

    default_schema = Path(__file__).resolve().parents[1] / "schema" / "proposition-ledger-v1.schema.json"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schema",
        type=Path,
        default=default_schema,
        help="Draft 2020-12 proposition-ledger schema used for validation.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(__file__).resolve().with_name("synthetic-fixtures"),
        help="Tracked synthetic-fixture directory (never a production path).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare the existing pack to an in-memory rebuild without writing.",
    )
    return parser.parse_args()


def main() -> int:
    """Generate or read-only verify the deterministic fixture pack."""

    args = parse_args()
    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    documents = build_documents(schema)
    if args.check:
        check_documents(args.output_root, documents)
    else:
        write_documents(args.output_root, documents)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
