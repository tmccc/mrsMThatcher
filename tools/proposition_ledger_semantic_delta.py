#!/usr/bin/env python3
"""Deterministically materialise one provider semantic delta into a ledger.

This module has no provider, network, X, or production-bot imports.  The model
response is semantic input only: deterministic code owns durable identifiers,
the replay patch, the predecessor link, and both persistence hashes.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from tools import build_proposition_ledger_phase1 as phase1


SEMANTIC_DELTA_SCHEMA_VERSION = "proposition-ledger-semantic-delta-v1.1.2"
MATERIALISER_VERSION = "proposition-ledger-semantic-delta-materialiser-v2.0.2"
PERSISTED_LEDGER_SCHEMA_VERSION = "proposition-ledger-v1.0.1"
SUCCESS_STATUS = "ok"
FAILURE_STATUSES = {
    "semantic_delta_schema_invalid",
    "semantic_reference_invalid",
    "semantic_evidence_invalid",
    "semantic_transition_invalid",
    "materialisation_invariant_failure",
    "persisted_ledger_validation_failure",
}
MAX_FAILURE_ERRORS = 32
MAX_FAILURE_ERROR_LENGTH = 512
OPAQUE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
LOCAL_REF_RE = re.compile(r"^new-[a-z-]+-[1-9][0-9]{0,2}$")
PARTICIPANT_FIELDS = {
    "author_key",
    "identity_confidence",
    "participant_id",
    "role",
}
GENESIS_CONTEXT_FIELDS = {
    "conversation_key",
    "current_participant",
    "root_post_id",
    "source_completeness",
}

PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SEMANTIC_SCHEMA_PATH = (
    PROJECT_DIR
    / "proposition_ledger_research/schema/proposition-ledger-semantic-delta-v1.schema.json"
)
DEFAULT_LEDGER_SCHEMA_PATH = (
    PROJECT_DIR / "proposition_ledger_research/schema/proposition-ledger-v1.schema.json"
)


@dataclass(frozen=True)
class MaterialisationResult:
    """A successful ledger or one bounded, typed failure."""

    status: str
    ledger: dict[str, Any] | None = None
    errors: tuple[str, ...] = ()
    local_id_map: dict[str, str] | None = None

    @property
    def succeeded(self) -> bool:
        """Return whether materialisation produced a persisted ledger."""

        return self.status == SUCCESS_STATUS


class _MaterialisationFailure(RuntimeError):
    """Internal control flow for a public typed failure result."""

    def __init__(self, status: str, errors: Iterable[str]):
        super().__init__(status)
        self.status = status
        self.errors = tuple(errors)


NAMESPACE_SPECS: dict[str, tuple[str, str, str]] = {
    "proposition": ("propositions", "proposition_id", "proposition"),
    "proposition_group": (
        "proposition_groups",
        "proposition_group_id",
        "proposition-group",
    ),
    "issue": ("issue_states", "issue_id", "issue"),
    "commitment": (
        "participant_commitments",
        "commitment_id",
        "commitment",
    ),
    "obligation": (
        "conversational_obligations",
        "obligation_id",
        "obligation",
    ),
    "relation": ("proposition_relations", "relation_id", "relation"),
    "answer_target": ("answer_targets", "answer_target_id", "answer-target"),
    "rejected_answer_target": (
        "rejected_answer_targets",
        "rejected_answer_target_id",
        "rejected-answer-target",
    ),
    "repair": ("repair_records", "repair_id", "repair"),
    "warning": ("warnings", "warning_id", "warning"),
}

NEW_ARRAY_SPECS: tuple[tuple[str, str, str], ...] = (
    ("new_propositions", "proposition", "local_ref"),
    ("new_proposition_groups", "proposition_group", "local_ref"),
    ("new_issue_states", "issue", "local_ref"),
    ("new_relations", "relation", "local_ref"),
    ("warnings", "warning", "local_ref"),
)

CHANGE_ARRAY_SPECS: tuple[tuple[str, str], ...] = (
    ("commitment_changes", "commitment"),
    ("obligation_changes", "obligation"),
    ("answer_target_changes", "answer_target"),
    ("rejected_answer_target_changes", "rejected_answer_target"),
    ("repair_records", "repair"),
)

UPDATE_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("proposition_updates", "proposition", "proposition_id", "propositions"),
    (
        "proposition_group_updates",
        "proposition_group",
        "proposition_group_id",
        "proposition_groups",
    ),
    ("issue_state_updates", "issue", "issue_id", "issue_states"),
)

ITEM_TYPE_NAMESPACES = {
    "proposition": "proposition",
    "issue": "issue",
    "commitment": "commitment",
    "obligation": "obligation",
    "answer_target": "answer_target",
    "repair": "repair",
    "compound_structure": "proposition_group",
}

TERMINAL_ISSUE_STATUSES = {
    "answered",
    "superseded",
    "abandoned",
    "expired",
    "no_stable_issue",
}
TERMINAL_OBLIGATION_STATUSES = {"satisfied", "waived", "expired", "superseded"}


def _bounded_errors(errors: Iterable[Any]) -> tuple[str, ...]:
    bounded: list[str] = []
    for error in errors:
        if len(bounded) >= MAX_FAILURE_ERRORS:
            break
        text = str(error).replace("\n", " ")
        bounded.append(text[:MAX_FAILURE_ERROR_LENGTH])
    return tuple(bounded or ["unspecified_failure"])


def _raise(status: str, *errors: str) -> None:
    if status not in FAILURE_STATUSES:
        raise RuntimeError(f"unknown materialisation status: {status}")
    raise _MaterialisationFailure(status, errors)


def _load_json_schema(path: Path) -> dict[str, Any]:
    value = phase1._read_json(path)
    if not isinstance(value, dict):
        raise phase1.Phase1Error(f"schema is not an object: {path}")
    return value


def _namespace_index(
    ledger: Mapping[str, Any], namespace: str
) -> dict[str, dict[str, Any]]:
    collection, id_field, _ = NAMESPACE_SPECS[namespace]
    result: dict[str, dict[str, Any]] = {}
    for record in ledger.get(collection, []):
        if not isinstance(record, Mapping):
            continue
        identifier = str(record.get(id_field) or "")
        if not identifier or identifier in result:
            _raise(
                "materialisation_invariant_failure",
                f"invalid_prior_namespace:{namespace}",
            )
        result[identifier] = copy.deepcopy(dict(record))
    return result


def _derived_id(
    prefix: str,
    conversation_key: str,
    target_turn_id: str,
    discriminator: str,
) -> str:
    material = {
        "conversation_key": conversation_key,
        "discriminator": discriminator,
        "namespace": prefix,
        "schema_version": SEMANTIC_DELTA_SCHEMA_VERSION,
        "target_turn_id": target_turn_id,
    }
    digest = phase1.sha256_bytes(phase1.canonical_json_bytes(material))
    identifier = f"{prefix}-{digest}"
    if not OPAQUE_ID_RE.fullmatch(identifier):
        _raise("materialisation_invariant_failure", "derived_id_out_of_contract")
    return identifier


def _collect_local_ids(
    prior_ledger: Mapping[str, Any], semantic_delta: Mapping[str, Any]
) -> tuple[dict[str, str], dict[str, str], dict[str, set[str]]]:
    conversation_key = str(semantic_delta["conversation_key"])
    target_turn_id = str(semantic_delta["target_turn_id"])
    prior_ids = {
        namespace: set(_namespace_index(prior_ledger, namespace))
        for namespace in NAMESPACE_SPECS
    }
    local_ids: dict[str, str] = {}
    local_namespaces: dict[str, str] = {}

    def add(namespace: str, local_ref: Any) -> None:
        reference = str(local_ref or "")
        if reference in local_ids:
            _raise(
                "semantic_reference_invalid",
                f"duplicate_local_reference:{reference}",
            )
        _, _, prefix = NAMESPACE_SPECS[namespace]
        identifier = _derived_id(
            prefix,
            conversation_key,
            target_turn_id,
            reference,
        )
        if identifier in prior_ids[namespace]:
            _raise(
                "materialisation_invariant_failure",
                f"deterministic_id_collision:{namespace}",
            )
        local_ids[reference] = identifier
        local_namespaces[reference] = namespace

    for field, namespace, ref_field in NEW_ARRAY_SPECS:
        for record in semantic_delta.get(field, []):
            add(namespace, record.get(ref_field))
    for field, namespace in CHANGE_ARRAY_SPECS:
        for record in semantic_delta.get(field, []):
            if record.get("operation") == "add":
                add(namespace, record.get("local_ref"))
    return local_ids, local_namespaces, prior_ids


class _ReferenceResolver:
    """Resolve only prior stable IDs or declared same-turn local references."""

    def __init__(
        self,
        local_ids: Mapping[str, str],
        local_namespaces: Mapping[str, str],
        prior_ids: Mapping[str, set[str]],
        participants: set[str],
    ) -> None:
        self.local_ids = local_ids
        self.local_namespaces = local_namespaces
        self.prior_ids = prior_ids
        self.participants = participants

    def resolve(self, value: Any, namespace: str, location: str) -> str:
        reference = str(value or "")
        if reference in self.local_ids:
            if self.local_namespaces[reference] != namespace:
                _raise(
                    "semantic_reference_invalid",
                    f"wrong_local_reference_namespace:{location}",
                )
            return self.local_ids[reference]
        if LOCAL_REF_RE.fullmatch(reference):
            _raise(
                "semantic_reference_invalid",
                f"orphan_local_reference:{location}:{reference}",
            )
        if reference not in self.prior_ids[namespace]:
            _raise(
                "semantic_reference_invalid",
                f"unknown_existing_reference:{location}:{reference}",
            )
        return reference

    def participant(self, value: Any, location: str, *, nullable: bool = False) -> str | None:
        if value is None and nullable:
            return None
        identifier = str(value or "")
        if identifier not in self.participants:
            _raise(
                "semantic_reference_invalid",
                f"unknown_participant_reference:{location}:{identifier}",
            )
        return identifier


def _validate_current_turn(current_turn: Mapping[str, Any]) -> None:
    """Validate the harness-owned fields needed at every turn boundary."""

    required_turn_fields = {
        "turn_id",
        "turn_index",
        "post_id",
        "parent_turn_id",
        "speaker_id",
        "text",
    }
    if not isinstance(current_turn, Mapping) or not required_turn_fields <= current_turn.keys():
        _raise(
            "semantic_reference_invalid",
            "current_turn_missing_required_field",
        )
    turn_id = current_turn.get("turn_id")
    turn_index = current_turn.get("turn_index")
    speaker_id = current_turn.get("speaker_id")
    parent_turn_id = current_turn.get("parent_turn_id")
    text = current_turn.get("text")
    if not isinstance(turn_id, str) or not OPAQUE_ID_RE.fullmatch(turn_id):
        _raise("semantic_reference_invalid", "current_turn_id_invalid")
    if isinstance(turn_index, bool) or not isinstance(turn_index, int) or turn_index < 0:
        _raise("semantic_reference_invalid", "current_turn_index_invalid")
    if not isinstance(text, str):
        _raise("semantic_evidence_invalid", "current_turn_text_not_string")
    if current_turn.get("post_id") is not None and not isinstance(
        current_turn.get("post_id"), str
    ):
        _raise("semantic_reference_invalid", "current_turn_post_id_invalid")
    if not isinstance(speaker_id, str) or not OPAQUE_ID_RE.fullmatch(speaker_id):
        _raise("semantic_reference_invalid", "current_turn_speaker_invalid")
    if parent_turn_id is not None and not isinstance(parent_turn_id, str):
        _raise("semantic_reference_invalid", "current_turn_parent_invalid")


def _validated_participant_descriptor(
    current_participant: Mapping[str, Any],
    ledger_schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Return one exact persisted participant supplied by trusted metadata."""

    if not isinstance(current_participant, Mapping):
        _raise("semantic_reference_invalid", "current_participant_not_object")
    descriptor = copy.deepcopy(dict(current_participant))
    if set(descriptor) != PARTICIPANT_FIELDS:
        _raise(
            "semantic_reference_invalid",
            "current_participant_descriptor_fields_invalid",
        )
    participant_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": "#/$defs/participant",
        "$defs": copy.deepcopy(dict(ledger_schema.get("$defs", {}))),
    }
    errors = phase1._jsonschema_errors(descriptor, participant_schema)
    if errors:
        _raise(
            "semantic_reference_invalid",
            "current_participant_descriptor_schema_invalid",
        )
    return descriptor


def _validate_prior_and_bindings(
    prior_ledger: Mapping[str, Any] | None,
    current_turn: Mapping[str, Any],
    semantic_delta: Mapping[str, Any],
    current_participant: Mapping[str, Any],
    genesis_context: Mapping[str, Any] | None,
    ledger_schema: Mapping[str, Any],
) -> None:
    _validate_current_turn(current_turn)
    if current_participant.get("participant_id") != current_turn.get("speaker_id"):
        _raise(
            "semantic_reference_invalid",
            "current_participant_speaker_binding_mismatch",
        )

    turn_id = str(current_turn["turn_id"])
    turn_index = int(current_turn["turn_index"])
    parent_turn_id = current_turn.get("parent_turn_id")
    if semantic_delta.get("target_turn_id") != turn_id:
        _raise("semantic_reference_invalid", "target_turn_binding_mismatch")
    if semantic_delta.get("as_of_turn_index") != turn_index:
        _raise("semantic_reference_invalid", "target_index_binding_mismatch")

    if prior_ledger is None:
        if genesis_context is None or not isinstance(genesis_context, Mapping):
            _raise("semantic_reference_invalid", "genesis_context_required")
        if set(genesis_context) != GENESIS_CONTEXT_FIELDS:
            _raise("semantic_reference_invalid", "genesis_context_fields_invalid")
        if turn_index != 0:
            _raise("semantic_reference_invalid", "genesis_turn_index_not_zero")
        if parent_turn_id is not None:
            _raise("semantic_reference_invalid", "genesis_parent_not_null")
        if semantic_delta.get("prior_ledger_reference") is not None:
            _raise("semantic_reference_invalid", "genesis_predecessor_not_null")
        if genesis_context.get("current_participant") != current_participant:
            _raise(
                "semantic_reference_invalid",
                "genesis_current_participant_mismatch",
            )
        conversation_key = genesis_context.get("conversation_key")
        if not isinstance(conversation_key, str) or not conversation_key:
            _raise("semantic_reference_invalid", "genesis_conversation_key_invalid")
        if semantic_delta.get("conversation_key") != conversation_key:
            _raise("semantic_reference_invalid", "conversation_binding_mismatch")
        if (
            current_turn.get("conversation_key") is not None
            and current_turn.get("conversation_key") != conversation_key
        ):
            _raise("semantic_reference_invalid", "conversation_binding_mismatch")
        if genesis_context.get("root_post_id") != current_turn.get("post_id"):
            _raise("semantic_reference_invalid", "genesis_root_post_binding_mismatch")
        if not isinstance(genesis_context.get("source_completeness"), Mapping):
            _raise("semantic_reference_invalid", "genesis_source_completeness_invalid")
        return

    if genesis_context is not None:
        _raise("semantic_reference_invalid", "genesis_context_for_non_genesis")
    prior_schema_errors = phase1._jsonschema_errors(prior_ledger, ledger_schema)
    if prior_schema_errors:
        _raise(
            "materialisation_invariant_failure",
            "prior_ledger_schema_invalid",
        )
    if prior_ledger.get("schema_version") != PERSISTED_LEDGER_SCHEMA_VERSION:
        _raise(
            "materialisation_invariant_failure",
            "prior_ledger_version_mismatch",
        )
    if prior_ledger.get("ledger_sha256") != phase1.ledger_sha256(prior_ledger):
        _raise(
            "materialisation_invariant_failure",
            "prior_ledger_self_hash_mismatch",
        )

    prior_index = prior_ledger.get("as_of_turn_index")
    if not isinstance(prior_index, int) or turn_index != prior_index + 1:
        _raise("semantic_reference_invalid", "nonconsecutive_turn_boundary")
    if semantic_delta.get("conversation_key") != prior_ledger.get("conversation_key"):
        _raise("semantic_reference_invalid", "conversation_binding_mismatch")
    prior_reference = semantic_delta.get("prior_ledger_reference")
    if not isinstance(prior_reference, Mapping):
        _raise("semantic_reference_invalid", "non_genesis_predecessor_is_null")
    if (
        prior_reference.get("ledger_id") != prior_ledger.get("ledger_id")
        or prior_reference.get("as_of_turn_index") != prior_index
    ):
        _raise("semantic_reference_invalid", "prior_ledger_reference_mismatch")

    prior_turns = {
        str(record.get("turn_id")): record
        for record in prior_ledger.get("turn_refs", [])
        if isinstance(record, Mapping)
    }
    if turn_id in prior_turns:
        _raise("semantic_reference_invalid", "duplicate_current_turn_id")
    if parent_turn_id is not None:
        if parent_turn_id not in prior_turns:
            _raise("semantic_reference_invalid", "current_parent_not_in_prior_prefix")
        if prior_turns[parent_turn_id].get("turn_index", turn_index) >= turn_index:
            _raise("semantic_reference_invalid", "current_parent_not_backward")


def _ledger_base_with_current_participant(
    previous_ledger: Mapping[str, Any] | None,
    current_participant: Mapping[str, Any],
    genesis_context: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    """Create the cumulative base and register only the exact current speaker."""

    descriptor = copy.deepcopy(dict(current_participant))
    if previous_ledger is None:
        if genesis_context is None:  # guarded before this helper
            _raise("materialisation_invariant_failure", "genesis_context_lost")
        return (
            {
                "answer_targets": [],
                "as_of_turn_index": 0,
                "conversation_key": genesis_context["conversation_key"],
                "conversational_obligations": [],
                "extraction_status": {
                    "abstentions": [],
                    "status": "complete",
                    "unsupported_inferences_rejected": 0,
                },
                "issue_states": [],
                "ledger_id": "ledger-genesis-placeholder",
                "ledger_sha256": phase1.ZERO_SHA256,
                "participant_commitments": [],
                "participants": [descriptor],
                "previous_ledger_sha256": None,
                "proposition_groups": [],
                "proposition_relations": [],
                "propositions": [],
                "rejected_answer_targets": [],
                "repair_records": [],
                "resolved_items": [],
                "root_post_id": genesis_context["root_post_id"],
                "schema_version": PERSISTED_LEDGER_SCHEMA_VERSION,
                "source_completeness": copy.deepcopy(
                    genesis_context["source_completeness"]
                ),
                "state_transitions": [],
                "target_turn_id": "genesis-placeholder",
                "turn_refs": [],
                "unresolved_items": [],
                "warnings": [],
            },
            True,
        )

    candidate = copy.deepcopy(dict(previous_ledger))
    matching = [
        record
        for record in candidate.get("participants", [])
        if isinstance(record, Mapping)
        and record.get("participant_id") == descriptor["participant_id"]
    ]
    if len(matching) > 1:
        _raise("materialisation_invariant_failure", "duplicate_prior_participant")
    if matching:
        if dict(matching[0]) != descriptor:
            _raise(
                "semantic_reference_invalid",
                "current_participant_descriptor_mismatch",
            )
        return candidate, False
    candidate["participants"].append(descriptor)
    return candidate, True


def _iter_evidence_spans(
    value: Any, location: str = "$"
) -> Iterable[tuple[str, Mapping[str, Any]]]:
    if isinstance(value, Mapping):
        spans = value.get("exact_evidence_spans")
        if isinstance(spans, list):
            for index, span in enumerate(spans):
                if isinstance(span, Mapping):
                    yield f"{location}.exact_evidence_spans[{index}]", span
        for key, child in value.items():
            if key != "exact_evidence_spans":
                yield from _iter_evidence_spans(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_evidence_spans(child, f"{location}[{index}]")


def _validate_current_evidence(
    semantic_delta: Mapping[str, Any], current_turn: Mapping[str, Any]
) -> None:
    turn_id = str(current_turn["turn_id"])
    text = str(current_turn["text"])
    errors: list[str] = []
    for location, span in _iter_evidence_spans(semantic_delta):
        if span.get("turn_id") != turn_id:
            errors.append(f"non_current_evidence_turn:{location}")
            continue
        start = span.get("start_char")
        end = span.get("end_char")
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or end <= start
            or end > len(text)
        ):
            errors.append(f"evidence_span_out_of_bounds:{location}")
            continue
        if text[start:end] != span.get("exact_text"):
            errors.append(f"evidence_span_mismatch:{location}")
    if errors:
        raise _MaterialisationFailure("semantic_evidence_invalid", errors)


def _validate_new_proposition_commitments(
    semantic_delta: Mapping[str, Any],
    current_turn: Mapping[str, Any],
    participants: set[str],
) -> None:
    """Require same-turn asserted commitments to agree with new propositions."""

    propositions = {
        str(item["local_ref"]): item
        for item in semantic_delta.get("new_propositions", [])
    }
    additions = [
        item
        for item in semantic_delta.get("commitment_changes", [])
        if item.get("operation") == "add"
    ]
    asserted_by_proposition: dict[str, list[Mapping[str, Any]]] = {}
    errors: list[str] = []
    for commitment in additions:
        if commitment.get("stance") != "asserted":
            continue
        proposition_ref = str(commitment.get("proposition_ref"))
        proposition = propositions.get(proposition_ref)
        if proposition is None:
            continue
        asserted_by_proposition.setdefault(proposition_ref, []).append(commitment)
        attribution = proposition["speaker_or_attributor"]
        attributed_participant = attribution.get("participant_id")
        if commitment.get("participant_id") not in participants or (
            isinstance(attributed_participant, str)
            and attributed_participant not in participants
        ):
            continue
        if (
            attribution.get("kind") != "speaker"
            or proposition.get("commitment_status") != "speaker_committed"
            or commitment.get("participant_id") != attribution.get("participant_id")
        ):
            errors.append(
                f"asserted_commitment_proposition_status_mismatch:{proposition_ref}"
            )

    current_speaker = str(current_turn["speaker_id"])
    for proposition_ref, proposition in propositions.items():
        attribution = proposition["speaker_or_attributor"]
        if not (
            attribution.get("kind") == "speaker"
            and attribution.get("participant_id") == current_speaker
            and proposition.get("commitment_status") == "speaker_committed"
        ):
            continue
        matching = [
            commitment
            for commitment in asserted_by_proposition.get(proposition_ref, [])
            if commitment.get("participant_id") == current_speaker
        ]
        if len(matching) > 1:
            errors.append(
                f"duplicate_commitment_record_for_new_proposition:{proposition_ref}"
            )
        elif not matching and proposition_ref not in asserted_by_proposition:
            errors.append(
                "speaker_committed_proposition_missing_commitment_record:"
                f"{proposition_ref}"
            )

    if errors:
        _raise("semantic_transition_invalid", *sorted(set(errors)))


def _resolve_speaker(
    value: Mapping[str, Any], resolver: _ReferenceResolver, location: str
) -> dict[str, Any]:
    result = copy.deepcopy(dict(value))
    result["participant_id"] = resolver.participant(
        value.get("participant_id"), f"{location}.participant_id", nullable=True
    )
    result["attributed_participant_id"] = resolver.participant(
        value.get("attributed_participant_id"),
        f"{location}.attributed_participant_id",
        nullable=True,
    )
    return result


def _resolve_derivation(
    value: Mapping[str, Any], resolver: _ReferenceResolver, location: str
) -> dict[str, Any]:
    result = copy.deepcopy(dict(value))
    result["source_proposition_ids"] = [
        resolver.resolve(reference, "proposition", f"{location}.source_proposition_refs")
        for reference in value.get("source_proposition_refs", [])
    ]
    result.pop("source_proposition_refs", None)
    return result


def _resolve_group_members(
    members: Sequence[Mapping[str, Any]],
    resolver: _ReferenceResolver,
    location: str,
) -> list[dict[str, Any]]:
    return [
        {
            "ordinal": member["ordinal"],
            "proposition_id": resolver.resolve(
                member["proposition_ref"],
                "proposition",
                f"{location}[{index}].proposition_ref",
            ),
            "role": member["role"],
        }
        for index, member in enumerate(members)
    ]


def _alternative_id(
    owner_id: str,
    local_ref: str,
    conversation_key: str,
    target_turn_id: str,
) -> str:
    return _derived_id(
        "alternative",
        conversation_key,
        target_turn_id,
        f"{owner_id}:{local_ref}",
    )


def _resolve_alternatives(
    alternatives: Sequence[Mapping[str, Any]],
    owner_id: str,
    resolver: _ReferenceResolver,
    conversation_key: str,
    target_turn_id: str,
    location: str,
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for index, alternative in enumerate(alternatives):
        local_ref = str(alternative["local_ref"])
        if local_ref in seen:
            _raise(
                "semantic_reference_invalid",
                f"duplicate_local_alternative:{location}:{local_ref}",
            )
        seen.add(local_ref)
        result.append(
            {
                "alternative_id": _alternative_id(
                    owner_id,
                    local_ref,
                    conversation_key,
                    target_turn_id,
                ),
                "label": alternative["label"],
                "proposition_ids": [
                    resolver.resolve(
                        reference,
                        "proposition",
                        f"{location}[{index}].proposition_refs",
                    )
                    for reference in alternative.get("proposition_refs", [])
                ],
                "status": alternative["status"],
            }
        )
    return result


def _append_unique_spans(record: dict[str, Any], spans: Sequence[Mapping[str, Any]]) -> None:
    if "exact_evidence_spans" not in record:
        return
    existing = list(record.get("exact_evidence_spans", []))
    for span in spans:
        copied = copy.deepcopy(dict(span))
        if copied not in existing:
            existing.append(copied)
    record["exact_evidence_spans"] = existing


def _record_index(
    candidate: Mapping[str, Any], collection: str, id_field: str
) -> dict[str, dict[str, Any]]:
    return {
        str(record[id_field]): record
        for record in candidate.get(collection, [])
        if isinstance(record, dict) and id_field in record
    }


def _apply_propositions(
    candidate: dict[str, Any],
    delta: Mapping[str, Any],
    resolver: _ReferenceResolver,
    local_ids: Mapping[str, str],
    current_turn_id: str,
    reasons: dict[tuple[str, str], str],
) -> None:
    additions: list[dict[str, Any]] = []
    for item in delta.get("new_propositions", []):
        record = copy.deepcopy(dict(item))
        local_ref = str(record.pop("local_ref"))
        record["proposition_id"] = local_ids[local_ref]
        record["introduced_at_turn_id"] = current_turn_id
        group_ref = record.pop("proposition_group_ref")
        record["proposition_group_id"] = (
            None
            if group_ref is None
            else resolver.resolve(
                group_ref,
                "proposition_group",
                f"new_propositions.{local_ref}.proposition_group_ref",
            )
        )
        record["speaker_or_attributor"] = _resolve_speaker(
            record["speaker_or_attributor"],
            resolver,
            f"new_propositions.{local_ref}.speaker_or_attributor",
        )
        record["derivation"] = _resolve_derivation(
            record["derivation"],
            resolver,
            f"new_propositions.{local_ref}.derivation",
        )
        additions.append(record)
    candidate["propositions"].extend(
        sorted(additions, key=lambda record: record["proposition_id"])
    )

    records = _record_index(candidate, "propositions", "proposition_id")
    seen: set[str] = set()
    prior_ids = resolver.prior_ids["proposition"]
    for update in delta.get("proposition_updates", []):
        identifier = str(update["proposition_id"])
        if identifier in seen:
            _raise(
                "semantic_reference_invalid",
                f"duplicate_existing_update:proposition:{identifier}",
            )
        seen.add(identifier)
        if identifier not in prior_ids:
            _raise(
                "semantic_reference_invalid",
                f"unknown_existing_reference:proposition_updates:{identifier}",
            )
        changes = copy.deepcopy(dict(update["changes"]))
        if "lifecycle_status" not in changes:
            _raise(
                "semantic_transition_invalid",
                f"proposition_update_requires_lifecycle_transition:{identifier}",
            )
        old_status = str(records[identifier].get("lifecycle_status"))
        new_status = str(changes["lifecycle_status"])
        if new_status not in phase1.PROPOSITION_LIFECYCLE_TRANSITIONS.get(
            old_status, set()
        ):
            _raise(
                "semantic_transition_invalid",
                f"invalid_lifecycle_transition:{identifier}:{old_status}:{new_status}",
            )
        if "speaker_or_attributor" in changes:
            changes["speaker_or_attributor"] = _resolve_speaker(
                changes["speaker_or_attributor"],
                resolver,
                f"proposition_updates.{identifier}.speaker_or_attributor",
            )
        if "derivation" in changes:
            changes["derivation"] = _resolve_derivation(
                changes["derivation"],
                resolver,
                f"proposition_updates.{identifier}.derivation",
            )
        if "proposition_group_ref" in changes:
            group_ref = changes.pop("proposition_group_ref")
            changes["proposition_group_id"] = (
                None
                if group_ref is None
                else resolver.resolve(
                    group_ref,
                    "proposition_group",
                    f"proposition_updates.{identifier}.proposition_group_ref",
                )
            )
        records[identifier].update(changes)
        _append_unique_spans(records[identifier], update["exact_evidence_spans"])
        reasons[("propositions", identifier)] = str(update["reason"])


def _apply_groups(
    candidate: dict[str, Any],
    delta: Mapping[str, Any],
    resolver: _ReferenceResolver,
    local_ids: Mapping[str, str],
    current_turn_id: str,
    reasons: dict[tuple[str, str], str],
) -> None:
    additions: list[dict[str, Any]] = []
    for item in delta.get("new_proposition_groups", []):
        record = copy.deepcopy(dict(item))
        local_ref = str(record.pop("local_ref"))
        record["proposition_group_id"] = local_ids[local_ref]
        record["introduced_at_turn_id"] = current_turn_id
        record["members"] = _resolve_group_members(
            record["members"],
            resolver,
            f"new_proposition_groups.{local_ref}.members",
        )
        additions.append(record)
    candidate["proposition_groups"].extend(
        sorted(additions, key=lambda record: record["proposition_group_id"])
    )
    records = _record_index(
        candidate, "proposition_groups", "proposition_group_id"
    )
    seen: set[str] = set()
    for update in delta.get("proposition_group_updates", []):
        identifier = str(update["proposition_group_id"])
        if identifier in seen:
            _raise(
                "semantic_reference_invalid",
                f"duplicate_existing_update:proposition_group:{identifier}",
            )
        seen.add(identifier)
        if identifier not in resolver.prior_ids["proposition_group"]:
            _raise(
                "semantic_reference_invalid",
                f"unknown_existing_reference:proposition_group_updates:{identifier}",
            )
        changes = copy.deepcopy(dict(update["changes"]))
        if "members" in changes:
            changes["members"] = _resolve_group_members(
                changes["members"],
                resolver,
                f"proposition_group_updates.{identifier}.members",
            )
        records[identifier].update(changes)
        _append_unique_spans(records[identifier], update["exact_evidence_spans"])
        reasons[("proposition_groups", identifier)] = str(update["reason"])


def _issue_resolution_fields(record: dict[str, Any], current_turn_id: str) -> None:
    status = record.get("status")
    if status in TERMINAL_ISSUE_STATUSES:
        record["resolution_turn_id"] = current_turn_id
    elif status in {"open", "partly_answered", "challenged"}:
        record["resolution_turn_id"] = None
        record["resolution_type"] = None


def _resolve_issue_changes(
    changes: dict[str, Any],
    owner_id: str,
    resolver: _ReferenceResolver,
    conversation_key: str,
    target_turn_id: str,
    location: str,
) -> dict[str, Any]:
    if "addressed_participant" in changes:
        changes["addressed_participant"] = resolver.participant(
            changes["addressed_participant"],
            f"{location}.addressed_participant",
            nullable=True,
        )
    if "related_proposition_refs" in changes:
        changes["related_proposition_ids"] = [
            resolver.resolve(
                reference,
                "proposition",
                f"{location}.related_proposition_refs",
            )
            for reference in changes.pop("related_proposition_refs")
        ]
    if "live_alternatives" in changes:
        changes["live_alternatives"] = _resolve_alternatives(
            changes["live_alternatives"],
            owner_id,
            resolver,
            conversation_key,
            target_turn_id,
            f"{location}.live_alternatives",
        )
    return changes


def _apply_issues(
    candidate: dict[str, Any],
    delta: Mapping[str, Any],
    resolver: _ReferenceResolver,
    local_ids: Mapping[str, str],
    current_turn_id: str,
    conversation_key: str,
    reasons: dict[tuple[str, str], str],
) -> None:
    additions: list[dict[str, Any]] = []
    for item in delta.get("new_issue_states", []):
        record = copy.deepcopy(dict(item))
        local_ref = str(record.pop("local_ref"))
        identifier = local_ids[local_ref]
        record["issue_id"] = identifier
        record["initiating_turn_id"] = current_turn_id
        record["initiating_speaker"] = resolver.participant(
            record["initiating_speaker"],
            f"new_issue_states.{local_ref}.initiating_speaker",
        )
        record = _resolve_issue_changes(
            record,
            identifier,
            resolver,
            conversation_key,
            current_turn_id,
            f"new_issue_states.{local_ref}",
        )
        _issue_resolution_fields(record, current_turn_id)
        additions.append(record)
    candidate["issue_states"].extend(
        sorted(additions, key=lambda record: record["issue_id"])
    )

    records = _record_index(candidate, "issue_states", "issue_id")
    seen: set[str] = set()
    for update in delta.get("issue_state_updates", []):
        identifier = str(update["issue_id"])
        if identifier in seen:
            _raise(
                "semantic_reference_invalid",
                f"duplicate_existing_update:issue:{identifier}",
            )
        seen.add(identifier)
        if identifier not in resolver.prior_ids["issue"]:
            _raise(
                "semantic_reference_invalid",
                f"unknown_existing_reference:issue_state_updates:{identifier}",
            )
        changes = _resolve_issue_changes(
            copy.deepcopy(dict(update["changes"])),
            identifier,
            resolver,
            conversation_key,
            current_turn_id,
            f"issue_state_updates.{identifier}",
        )
        records[identifier].update(changes)
        if "status" in changes:
            _issue_resolution_fields(records[identifier], current_turn_id)
        _append_unique_spans(records[identifier], update["exact_evidence_spans"])
        reasons[("issue_states", identifier)] = str(update["reason"])


def _resolve_reference_list(
    values: Sequence[Any], namespace: str, resolver: _ReferenceResolver, location: str
) -> list[str]:
    return [
        resolver.resolve(value, namespace, f"{location}[{index}]")
        for index, value in enumerate(values)
    ]


def _apply_commitments(
    candidate: dict[str, Any],
    delta: Mapping[str, Any],
    resolver: _ReferenceResolver,
    local_ids: Mapping[str, str],
    current_turn_id: str,
    reasons: dict[tuple[str, str], str],
) -> None:
    additions: list[dict[str, Any]] = []
    records = _record_index(
        candidate, "participant_commitments", "commitment_id"
    )
    seen_updates: set[str] = set()
    for item in delta.get("commitment_changes", []):
        if item["operation"] == "add":
            local_ref = str(item["local_ref"])
            additions.append(
                {
                    "basis": item["basis"],
                    "commitment_id": local_ids[local_ref],
                    "confidence": item["confidence"],
                    "introduced_at_turn_id": current_turn_id,
                    "last_updated_at_turn_id": current_turn_id,
                    "participant_id": resolver.participant(
                        item["participant_id"],
                        f"commitment_changes.{local_ref}.participant_id",
                    ),
                    "proposition_id": resolver.resolve(
                        item["proposition_ref"],
                        "proposition",
                        f"commitment_changes.{local_ref}.proposition_ref",
                    ),
                    "stance": item["stance"],
                    "uncertainty_reason": item["uncertainty_reason"],
                }
            )
            continue
        identifier = str(item["commitment_id"])
        if identifier in seen_updates:
            _raise(
                "semantic_reference_invalid",
                f"duplicate_existing_update:commitment:{identifier}",
            )
        seen_updates.add(identifier)
        if identifier not in resolver.prior_ids["commitment"]:
            _raise(
                "semantic_reference_invalid",
                f"unknown_existing_reference:commitment_changes:{identifier}",
            )
        records[identifier].update(copy.deepcopy(dict(item["changes"])))
        records[identifier]["last_updated_at_turn_id"] = current_turn_id
        reasons[("participant_commitments", identifier)] = str(item["reason"])
    candidate["participant_commitments"].extend(
        sorted(additions, key=lambda record: record["commitment_id"])
    )


def _resolve_obligation_changes(
    changes: dict[str, Any], resolver: _ReferenceResolver, location: str
) -> dict[str, Any]:
    if "related_issue_refs" in changes:
        changes["related_issue_ids"] = _resolve_reference_list(
            changes.pop("related_issue_refs"),
            "issue",
            resolver,
            f"{location}.related_issue_refs",
        )
    if "related_proposition_refs" in changes:
        changes["related_proposition_ids"] = _resolve_reference_list(
            changes.pop("related_proposition_refs"),
            "proposition",
            resolver,
            f"{location}.related_proposition_refs",
        )
    return changes


def _obligation_resolution_fields(record: dict[str, Any], current_turn_id: str) -> None:
    if record.get("status") in TERMINAL_OBLIGATION_STATUSES:
        record["resolved_by_turn_id"] = current_turn_id
    elif record.get("status") in {"open", "partly_satisfied"}:
        record["resolved_by_turn_id"] = None


def _apply_obligations(
    candidate: dict[str, Any],
    delta: Mapping[str, Any],
    resolver: _ReferenceResolver,
    local_ids: Mapping[str, str],
    current_turn_id: str,
    reasons: dict[tuple[str, str], str],
) -> None:
    additions: list[dict[str, Any]] = []
    records = _record_index(
        candidate, "conversational_obligations", "obligation_id"
    )
    seen_updates: set[str] = set()
    for item in delta.get("obligation_changes", []):
        if item["operation"] == "add":
            local_ref = str(item["local_ref"])
            record = {
                "confidence": item["confidence"],
                "created_by_turn_id": current_turn_id,
                "expiry_policy": item["expiry_policy"],
                "obligation_id": local_ids[local_ref],
                "obligation_type": item["obligation_type"],
                "owed_by_participant": resolver.participant(
                    item["owed_by_participant"],
                    f"obligation_changes.{local_ref}.owed_by_participant",
                ),
                "owed_to_participant": resolver.participant(
                    item["owed_to_participant"],
                    f"obligation_changes.{local_ref}.owed_to_participant",
                ),
                "related_issue_ids": _resolve_reference_list(
                    item["related_issue_refs"],
                    "issue",
                    resolver,
                    f"obligation_changes.{local_ref}.related_issue_refs",
                ),
                "related_proposition_ids": _resolve_reference_list(
                    item["related_proposition_refs"],
                    "proposition",
                    resolver,
                    f"obligation_changes.{local_ref}.related_proposition_refs",
                ),
                "status": item["status"],
                "resolved_by_turn_id": None,
            }
            _obligation_resolution_fields(record, current_turn_id)
            additions.append(record)
            continue
        identifier = str(item["obligation_id"])
        if identifier in seen_updates:
            _raise(
                "semantic_reference_invalid",
                f"duplicate_existing_update:obligation:{identifier}",
            )
        seen_updates.add(identifier)
        if identifier not in resolver.prior_ids["obligation"]:
            _raise(
                "semantic_reference_invalid",
                f"unknown_existing_reference:obligation_changes:{identifier}",
            )
        changes = _resolve_obligation_changes(
            copy.deepcopy(dict(item["changes"])),
            resolver,
            f"obligation_changes.{identifier}",
        )
        records[identifier].update(changes)
        if "status" in changes:
            _obligation_resolution_fields(records[identifier], current_turn_id)
        reasons[("conversational_obligations", identifier)] = str(item["reason"])
    candidate["conversational_obligations"].extend(
        sorted(additions, key=lambda record: record["obligation_id"])
    )


def _apply_relations(
    candidate: dict[str, Any],
    delta: Mapping[str, Any],
    resolver: _ReferenceResolver,
    local_ids: Mapping[str, str],
    current_turn_id: str,
) -> None:
    additions: list[dict[str, Any]] = []
    for item in delta.get("new_relations", []):
        local_ref = str(item["local_ref"])
        evaluator_diagnosis = (
            item.get("provenance_kind")
            in {"human_annotation", "machine_diagnostic"}
            and item.get("analysis_basis") == "evaluator_diagnosis"
        )
        asserted_or_analysed_by = item.get("asserted_or_analysed_by")
        if (asserted_or_analysed_by is None) != evaluator_diagnosis:
            _raise(
                "semantic_reference_invalid",
                f"relation_evaluator_attribution_mismatch:new_relations.{local_ref}",
            )
        additions.append(
            {
                "analysis_basis": item["analysis_basis"],
                "asserted_or_analysed_by": resolver.participant(
                    asserted_or_analysed_by,
                    f"new_relations.{local_ref}.asserted_or_analysed_by",
                    nullable=evaluator_diagnosis,
                ),
                "confidence": item["confidence"],
                "exact_evidence_spans": copy.deepcopy(item["exact_evidence_spans"]),
                "introduced_at_turn_id": current_turn_id,
                "provenance_kind": item["provenance_kind"],
                "relation_id": local_ids[local_ref],
                "relation_type": item["relation_type"],
                "source_proposition_ids": _resolve_reference_list(
                    item["source_proposition_refs"],
                    "proposition",
                    resolver,
                    f"new_relations.{local_ref}.source_proposition_refs",
                ),
                "target_proposition_ids": _resolve_reference_list(
                    item["target_proposition_refs"],
                    "proposition",
                    resolver,
                    f"new_relations.{local_ref}.target_proposition_refs",
                ),
                "uncertainty_reason": item["uncertainty_reason"],
            }
        )
    candidate["proposition_relations"].extend(
        sorted(additions, key=lambda record: record["relation_id"])
    )


def _resolve_answer_target_changes(
    changes: dict[str, Any], resolver: _ReferenceResolver, location: str
) -> dict[str, Any]:
    if "issue_refs" in changes:
        changes["issue_ids"] = _resolve_reference_list(
            changes.pop("issue_refs"),
            "issue",
            resolver,
            f"{location}.issue_refs",
        )
    if "proposition_refs" in changes:
        changes["proposition_ids"] = _resolve_reference_list(
            changes.pop("proposition_refs"),
            "proposition",
            resolver,
            f"{location}.proposition_refs",
        )
    return changes


def _apply_answer_targets(
    candidate: dict[str, Any],
    delta: Mapping[str, Any],
    resolver: _ReferenceResolver,
    local_ids: Mapping[str, str],
    current_turn_id: str,
    reasons: dict[tuple[str, str], str],
) -> None:
    additions: list[dict[str, Any]] = []
    records = _record_index(candidate, "answer_targets", "answer_target_id")
    seen_updates: set[str] = set()
    for item in delta.get("answer_target_changes", []):
        if item["operation"] == "add":
            local_ref = str(item["local_ref"])
            additions.append(
                {
                    "answer_target_id": local_ids[local_ref],
                    "confidence": item["confidence"],
                    "issue_ids": _resolve_reference_list(
                        item["issue_refs"],
                        "issue",
                        resolver,
                        f"answer_target_changes.{local_ref}.issue_refs",
                    ),
                    "proposition_ids": _resolve_reference_list(
                        item["proposition_refs"],
                        "proposition",
                        resolver,
                        f"answer_target_changes.{local_ref}.proposition_refs",
                    ),
                    "reply_turn_id": current_turn_id,
                    "selected_at_turn_id": current_turn_id,
                    "target_status": item["target_status"],
                }
            )
            continue
        identifier = str(item["answer_target_id"])
        if identifier in seen_updates:
            _raise(
                "semantic_reference_invalid",
                f"duplicate_existing_update:answer_target:{identifier}",
            )
        seen_updates.add(identifier)
        if identifier not in resolver.prior_ids["answer_target"]:
            _raise(
                "semantic_reference_invalid",
                f"unknown_existing_reference:answer_target_changes:{identifier}",
            )
        changes = _resolve_answer_target_changes(
            copy.deepcopy(dict(item["changes"])),
            resolver,
            f"answer_target_changes.{identifier}",
        )
        records[identifier].update(changes)
        reasons[("answer_targets", identifier)] = str(item["reason"])
    candidate["answer_targets"].extend(
        sorted(additions, key=lambda record: record["answer_target_id"])
    )


def _resolve_rejected_changes(
    changes: dict[str, Any], resolver: _ReferenceResolver, location: str
) -> dict[str, Any]:
    if "answer_target_ref" in changes:
        changes["answer_target_id"] = resolver.resolve(
            changes.pop("answer_target_ref"),
            "answer_target",
            f"{location}.answer_target_ref",
        )
    if "replacement_answer_target_ref" in changes:
        reference = changes.pop("replacement_answer_target_ref")
        changes["replacement_answer_target_id"] = (
            None
            if reference is None
            else resolver.resolve(
                reference,
                "answer_target",
                f"{location}.replacement_answer_target_ref",
            )
        )
    if "related_proposition_refs" in changes:
        changes["related_proposition_ids"] = _resolve_reference_list(
            changes.pop("related_proposition_refs"),
            "proposition",
            resolver,
            f"{location}.related_proposition_refs",
        )
    return changes


def _apply_rejected_targets(
    candidate: dict[str, Any],
    delta: Mapping[str, Any],
    resolver: _ReferenceResolver,
    local_ids: Mapping[str, str],
    current_turn_id: str,
    reasons: dict[tuple[str, str], str],
) -> None:
    additions: list[dict[str, Any]] = []
    records = _record_index(
        candidate, "rejected_answer_targets", "rejected_answer_target_id"
    )
    seen_updates: set[str] = set()
    for item in delta.get("rejected_answer_target_changes", []):
        if item["operation"] == "add":
            local_ref = str(item["local_ref"])
            changes = _resolve_rejected_changes(
                {
                    key: copy.deepcopy(item[key])
                    for key in (
                        "answer_target_ref",
                        "rejection_kind",
                        "replacement_answer_target_ref",
                        "related_proposition_refs",
                        "status",
                        "confidence",
                    )
                },
                resolver,
                f"rejected_answer_target_changes.{local_ref}",
            )
            changes.update(
                {
                    "exact_evidence_spans": copy.deepcopy(
                        item["exact_evidence_spans"]
                    ),
                    "rejected_answer_target_id": local_ids[local_ref],
                    "rejected_at_turn_id": current_turn_id,
                }
            )
            additions.append(changes)
            continue
        identifier = str(item["rejected_answer_target_id"])
        if identifier in seen_updates:
            _raise(
                "semantic_reference_invalid",
                f"duplicate_existing_update:rejected_answer_target:{identifier}",
            )
        seen_updates.add(identifier)
        if identifier not in resolver.prior_ids["rejected_answer_target"]:
            _raise(
                "semantic_reference_invalid",
                f"unknown_existing_reference:rejected_answer_target_changes:{identifier}",
            )
        changes = _resolve_rejected_changes(
            copy.deepcopy(dict(item["changes"])),
            resolver,
            f"rejected_answer_target_changes.{identifier}",
        )
        records[identifier].update(changes)
        _append_unique_spans(records[identifier], item["exact_evidence_spans"])
        reasons[("rejected_answer_targets", identifier)] = str(item["reason"])
    candidate["rejected_answer_targets"].extend(
        sorted(additions, key=lambda record: record["rejected_answer_target_id"])
    )


def _resolve_repair_changes(
    changes: dict[str, Any],
    resolver: _ReferenceResolver,
    current_turn_id: str,
    location: str,
) -> dict[str, Any]:
    if "rejected_answer_target_refs" in changes:
        changes["rejected_answer_target_ids"] = _resolve_reference_list(
            changes.pop("rejected_answer_target_refs"),
            "rejected_answer_target",
            resolver,
            f"{location}.rejected_answer_target_refs",
        )
    if "replacement_answer_target_refs" in changes:
        changes["replacement_answer_target_ids"] = _resolve_reference_list(
            changes.pop("replacement_answer_target_refs"),
            "answer_target",
            resolver,
            f"{location}.replacement_answer_target_refs",
        )
    if "acknowledged_at_current_turn" in changes:
        acknowledged = changes.pop("acknowledged_at_current_turn")
        changes["acknowledgement_turn_id"] = current_turn_id if acknowledged else None
    return changes


def _apply_repairs(
    candidate: dict[str, Any],
    delta: Mapping[str, Any],
    resolver: _ReferenceResolver,
    local_ids: Mapping[str, str],
    current_turn_id: str,
    reasons: dict[tuple[str, str], str],
) -> None:
    additions: list[dict[str, Any]] = []
    records = _record_index(candidate, "repair_records", "repair_id")
    seen_updates: set[str] = set()
    for item in delta.get("repair_records", []):
        if item["operation"] == "add":
            local_ref = str(item["local_ref"])
            changes = _resolve_repair_changes(
                {
                    key: copy.deepcopy(item[key])
                    for key in (
                        "repair_type",
                        "rejected_answer_target_refs",
                        "replacement_answer_target_refs",
                        "acknowledged_at_current_turn",
                        "outcome",
                        "confidence",
                    )
                },
                resolver,
                current_turn_id,
                f"repair_records.{local_ref}",
            )
            changes.update(
                {
                    "exact_evidence_spans": copy.deepcopy(
                        item["exact_evidence_spans"]
                    ),
                    "repair_id": local_ids[local_ref],
                    "trigger_turn_id": current_turn_id,
                }
            )
            additions.append(changes)
            continue
        identifier = str(item["repair_id"])
        if identifier in seen_updates:
            _raise(
                "semantic_reference_invalid",
                f"duplicate_existing_update:repair:{identifier}",
            )
        seen_updates.add(identifier)
        if identifier not in resolver.prior_ids["repair"]:
            _raise(
                "semantic_reference_invalid",
                f"unknown_existing_reference:repair_records:{identifier}",
            )
        changes = _resolve_repair_changes(
            copy.deepcopy(dict(item["changes"])),
            resolver,
            current_turn_id,
            f"repair_records.{identifier}",
        )
        records[identifier].update(changes)
        _append_unique_spans(records[identifier], item["exact_evidence_spans"])
        reasons[("repair_records", identifier)] = str(item["reason"])
    candidate["repair_records"].extend(
        sorted(additions, key=lambda record: record["repair_id"])
    )


def _apply_resolved_items(
    candidate: dict[str, Any],
    delta: Mapping[str, Any],
    resolver: _ReferenceResolver,
    current_turn_id: str,
) -> None:
    existing = {
        (str(item.get("item_type")), str(item.get("item_id")))
        for item in candidate.get("resolved_items", [])
    }
    additions: list[dict[str, Any]] = []
    for item in delta.get("resolved_items", []):
        item_type = str(item["item_type"])
        reference = str(item["item_ref"])
        namespace = ITEM_TYPE_NAMESPACES.get(item_type)
        if namespace is None:
            if LOCAL_REF_RE.fullmatch(reference):
                _raise(
                    "semantic_reference_invalid",
                    f"unsupported_local_reference:resolved_items:{item_type}",
                )
            is_known_reference = any(
                unresolved.get("item_type") == item_type
                and unresolved.get("item_id") == reference
                for unresolved in candidate.get("unresolved_items", [])
            )
            if not is_known_reference:
                _raise(
                    "semantic_reference_invalid",
                    f"unknown_existing_reference:resolved_items:{item_type}:{reference}",
                )
            identifier = reference
        else:
            identifier = resolver.resolve(
                reference,
                namespace,
                f"resolved_items:{item_type}:{reference}",
            )
        pair = (item_type, identifier)
        if pair in existing:
            _raise(
                "semantic_transition_invalid",
                f"item_already_resolved:{item_type}:{identifier}",
            )
        existing.add(pair)
        additions.append(
            {
                "item_id": identifier,
                "item_type": item_type,
                "resolution_type": item["resolution_type"],
                "resolved_at_turn_id": current_turn_id,
            }
        )
    for issue in candidate.get("issue_states", []):
        if (
            issue.get("initiating_turn_id") == current_turn_id
            and issue.get("issue_type") == "no_stable_issue"
            and issue.get("status") == "no_stable_issue"
            and issue.get("resolution_type") == "no_stable_issue"
        ):
            pair = ("issue", str(issue["issue_id"]))
            if pair not in existing:
                existing.add(pair)
                additions.append(
                    {
                        "item_id": pair[1],
                        "item_type": pair[0],
                        "resolution_type": "no_stable_issue",
                        "resolved_at_turn_id": current_turn_id,
                    }
                )
    candidate["resolved_items"].extend(
        sorted(additions, key=lambda item: (item["item_type"], item["item_id"]))
    )
    resolved_pairs = {
        (item["item_type"], item["item_id"])
        for item in additions
    }
    candidate["unresolved_items"] = [
        item
        for item in candidate.get("unresolved_items", [])
        if (item.get("item_type"), item.get("item_id")) not in resolved_pairs
    ]


def _apply_warnings(
    candidate: dict[str, Any],
    delta: Mapping[str, Any],
    local_ids: Mapping[str, str],
    current_turn_id: str,
) -> None:
    additions = [
        {
            "code": item["code"],
            "introduced_at_turn_id": current_turn_id,
            "message": item["message"],
            "severity": item["severity"],
            "warning_id": local_ids[str(item["local_ref"])],
        }
        for item in delta.get("warnings", [])
    ]
    candidate["warnings"].extend(
        sorted(additions, key=lambda record: record["warning_id"])
    )


def _transition_from_patch(
    previous_ledger: Mapping[str, Any] | None,
    candidate: Mapping[str, Any],
    current_turn_id: str,
    current_turn_index: int,
    reasons: Mapping[tuple[str, str], str],
    conversation_key: str,
) -> dict[str, Any]:
    patch = phase1.build_state_patch(previous_ledger, candidate)
    transition: dict[str, Any] = {
        "answer_targets_added": [],
        "answer_targets_updated": [],
        "commitments_added": [],
        "commitments_updated": [],
        "current_turn_id": current_turn_id,
        "current_turn_index": current_turn_index,
        "from_ledger_sha256": (
            previous_ledger["ledger_sha256"]
            if previous_ledger is not None
            else None
        ),
        "issue_states_added": [],
        "issue_states_updated": [],
        "items_resolved": [],
        "obligations_added": [],
        "obligations_updated": [],
        "proposition_groups_added": [],
        "proposition_groups_updated": [],
        "propositions_added": [],
        "propositions_updated": [],
        "rejected_answer_targets_added": [],
        "rejected_answer_targets_updated": [],
        "relations_added": [],
        "repair_records_added": [],
        "repair_records_updated": [],
        "state_patch": patch,
        "transition_id": _derived_id(
            "transition",
            conversation_key,
            current_turn_id,
            "current-turn-transition",
        ),
        "warnings_added": [],
    }
    added_by_collection = {
        collection: field
        for field, collection in phase1.TRANSITION_ADDED_COLLECTIONS.items()
    }
    updated_by_collection = {
        collection: (field, status_field)
        for field, (collection, status_field) in phase1.TRANSITION_UPDATED_COLLECTIONS.items()
    }
    previous = (
        phase1.ledger_state_projection(previous_ledger)
        if previous_ledger is not None
        else {collection: {} for collection in phase1.LEDGER_STATE_COLLECTIONS}
    )
    current = phase1.ledger_state_projection(candidate)
    allowed_remove_collections = {"unresolved_items"}
    for operation in patch:
        collection = str(operation["collection"])
        identifier = str(operation["item_id"])
        kind = str(operation["operation"])
        if kind == "remove" and collection not in allowed_remove_collections:
            _raise(
                "materialisation_invariant_failure",
                f"unexpected_materialiser_removal:{collection}:{identifier}",
            )
        if kind == "add" and collection in added_by_collection:
            transition[added_by_collection[collection]].append(identifier)
        elif kind == "replace" and collection in updated_by_collection:
            field, status_field = updated_by_collection[collection]
            reason = reasons.get((collection, identifier))
            if reason is None:
                _raise(
                    "materialisation_invariant_failure",
                    f"missing_semantic_update_reason:{collection}:{identifier}",
                )
            before = previous[collection][identifier]
            after = current[collection][identifier]
            from_status = (
                str(before.get(status_field, "present"))
                if status_field is not None
                else "present"
            )
            to_status = (
                str(after.get(status_field, "present"))
                if status_field is not None
                else "present"
            )
            transition[field].append(
                {
                    "from_status": from_status,
                    "item_id": identifier,
                    "reason": reason,
                    "to_status": to_status,
                }
            )
        elif collection == "resolved_items" and kind == "add":
            transition["items_resolved"].append(operation["after_record"]["item_id"])
        elif collection == "warnings" and kind == "add":
            transition["warnings_added"].append(identifier)
        elif collection == "proposition_relations" and kind != "add":
            _raise(
                "materialisation_invariant_failure",
                f"relation_mutation_forbidden:{identifier}",
            )
    return transition


def _build_candidate(
    previous_ledger: Mapping[str, Any] | None,
    base_ledger: Mapping[str, Any],
    current_turn: Mapping[str, Any],
    semantic_delta: Mapping[str, Any],
    resolver: _ReferenceResolver,
    local_ids: Mapping[str, str],
) -> dict[str, Any]:
    candidate = copy.deepcopy(dict(base_ledger))
    turn_id = str(current_turn["turn_id"])
    turn_index = int(current_turn["turn_index"])
    conversation_key = str(base_ledger["conversation_key"])
    candidate["ledger_id"] = _derived_id(
        "ledger", conversation_key, turn_id, "persisted-snapshot"
    )
    candidate["target_turn_id"] = turn_id
    candidate["as_of_turn_index"] = turn_index
    candidate["previous_ledger_sha256"] = (
        previous_ledger["ledger_sha256"]
        if previous_ledger is not None
        else None
    )
    candidate["ledger_sha256"] = phase1.ZERO_SHA256
    candidate["turn_refs"].append(
        {
            "parent_turn_id": current_turn["parent_turn_id"],
            "post_id": current_turn["post_id"],
            "speaker_id": current_turn["speaker_id"],
            "text_sha256": phase1.sha256_bytes(
                str(current_turn["text"]).encode("utf-8")
            ),
            "turn_id": turn_id,
            "turn_index": turn_index,
        }
    )
    candidate["state_transitions"] = []
    reasons: dict[tuple[str, str], str] = {}

    _apply_propositions(
        candidate,
        semantic_delta,
        resolver,
        local_ids,
        turn_id,
        reasons,
    )
    _apply_groups(
        candidate,
        semantic_delta,
        resolver,
        local_ids,
        turn_id,
        reasons,
    )
    _apply_issues(
        candidate,
        semantic_delta,
        resolver,
        local_ids,
        turn_id,
        conversation_key,
        reasons,
    )
    _apply_commitments(
        candidate,
        semantic_delta,
        resolver,
        local_ids,
        turn_id,
        reasons,
    )
    _apply_obligations(
        candidate,
        semantic_delta,
        resolver,
        local_ids,
        turn_id,
        reasons,
    )
    _apply_relations(candidate, semantic_delta, resolver, local_ids, turn_id)
    _apply_answer_targets(
        candidate,
        semantic_delta,
        resolver,
        local_ids,
        turn_id,
        reasons,
    )
    _apply_rejected_targets(
        candidate,
        semantic_delta,
        resolver,
        local_ids,
        turn_id,
        reasons,
    )
    _apply_repairs(
        candidate,
        semantic_delta,
        resolver,
        local_ids,
        turn_id,
        reasons,
    )
    _apply_resolved_items(candidate, semantic_delta, resolver, turn_id)
    _apply_warnings(candidate, semantic_delta, local_ids, turn_id)
    candidate["extraction_status"] = {
        "abstentions": copy.deepcopy(semantic_delta["abstentions"]),
        "status": semantic_delta["extraction_status"],
        "unsupported_inferences_rejected": semantic_delta[
            "unsupported_inferences_rejected"
        ],
    }
    candidate["state_transitions"] = [
        _transition_from_patch(
            previous_ledger,
            candidate,
            turn_id,
            turn_index,
            reasons,
            conversation_key,
        )
    ]
    candidate["ledger_sha256"] = phase1.ledger_sha256(candidate)
    return candidate


def _participant_patch_valid(
    candidate: Mapping[str, Any],
    participant_id: str,
    participant_registered: bool,
) -> bool:
    """Check that the authoritative patch records exactly the registration."""

    transitions = candidate.get("state_transitions", [])
    if not isinstance(transitions, list) or len(transitions) != 1:
        return False
    participant_operations = [
        operation
        for operation in transitions[0].get("state_patch", [])
        if isinstance(operation, Mapping)
        and operation.get("collection") == "participants"
    ]
    if not participant_registered:
        return not participant_operations
    return (
        len(participant_operations) == 1
        and participant_operations[0].get("operation") == "add"
        and participant_operations[0].get("item_id") == participant_id
        and participant_operations[0].get("before_record_sha256") is None
        and isinstance(participant_operations[0].get("after_record"), Mapping)
    )


def materialise_semantic_delta(
    previous_ledger: Mapping[str, Any] | None,
    current_turn: Mapping[str, Any],
    semantic_delta: Mapping[str, Any],
    *,
    current_participant: Mapping[str, Any],
    genesis_context: Mapping[str, Any] | None = None,
    semantic_schema: Mapping[str, Any] | None = None,
    ledger_schema: Mapping[str, Any] | None = None,
) -> MaterialisationResult:
    """Materialise one current-turn semantic delta without trusting persistence data.

    Failure details are capped and carry one of the six public failure statuses.
    ``previous_ledger`` is nullable only at the exact turn-zero genesis
    boundary. Participant identity and first-seen registration are supplied by
    trusted harness metadata, never by the provider semantic delta.
    """

    try:
        effective_semantic_schema = (
            dict(semantic_schema)
            if semantic_schema is not None
            else _load_json_schema(DEFAULT_SEMANTIC_SCHEMA_PATH)
        )
        effective_ledger_schema = (
            dict(ledger_schema)
            if ledger_schema is not None
            else _load_json_schema(DEFAULT_LEDGER_SCHEMA_PATH)
        )
        schema_errors = phase1._jsonschema_errors(
            semantic_delta, effective_semantic_schema
        )
        if schema_errors:
            raise _MaterialisationFailure(
                "semantic_delta_schema_invalid", schema_errors
            )
        descriptor = _validated_participant_descriptor(
            current_participant,
            effective_ledger_schema,
        )
        _validate_prior_and_bindings(
            previous_ledger,
            current_turn,
            semantic_delta,
            descriptor,
            genesis_context,
            effective_ledger_schema,
        )
        _validate_current_evidence(semantic_delta, current_turn)
        base_ledger, participant_registered = _ledger_base_with_current_participant(
            previous_ledger,
            descriptor,
            genesis_context,
        )
        local_ids, local_namespaces, prior_ids = _collect_local_ids(
            base_ledger if previous_ledger is None else previous_ledger,
            semantic_delta,
        )
        participants = {
            str(record.get("participant_id"))
            for record in base_ledger.get("participants", [])
            if isinstance(record, Mapping)
        }
        _validate_new_proposition_commitments(
            semantic_delta,
            current_turn,
            participants,
        )
        resolver = _ReferenceResolver(
            local_ids,
            local_namespaces,
            prior_ids,
            participants,
        )
        candidate = _build_candidate(
            previous_ledger,
            base_ledger,
            current_turn,
            semantic_delta,
            resolver,
            local_ids,
        )
        if not _participant_patch_valid(
            candidate,
            str(descriptor["participant_id"]),
            participant_registered,
        ):
            _raise(
                "materialisation_invariant_failure",
                "participant_registration_patch_invalid",
            )
        if previous_ledger is None:
            full_validator = getattr(phase1, "validate_ledger", None)
            if not callable(full_validator):
                _raise(
                    "materialisation_invariant_failure",
                    "genesis_full_ledger_validator_unavailable",
                )
            persisted_errors = full_validator(
                candidate,
                {
                    "conversation_key": candidate["conversation_key"],
                    "turns": [copy.deepcopy(dict(current_turn))],
                },
                effective_ledger_schema,
                _immediate_previous=None,
                _validate_history=False,
            )
        else:
            incremental_validator = getattr(
                phase1, "validate_ledger_incremental", None
            )
            if not callable(incremental_validator):
                _raise(
                    "materialisation_invariant_failure",
                    "incremental_full_ledger_validator_unavailable",
                )
            persisted_errors = incremental_validator(
                candidate,
                previous_ledger,
                current_turn,
                effective_ledger_schema,
            )
        if persisted_errors:
            raise _MaterialisationFailure(
                "persisted_ledger_validation_failure", persisted_errors
            )
        return MaterialisationResult(
            status=SUCCESS_STATUS,
            ledger=candidate,
            errors=(),
            local_id_map=dict(sorted(local_ids.items())),
        )
    except _MaterialisationFailure as exc:
        return MaterialisationResult(
            status=exc.status,
            ledger=None,
            errors=_bounded_errors(exc.errors),
            local_id_map=None,
        )
    except (KeyError, TypeError, ValueError, phase1.Phase1Error) as exc:
        return MaterialisationResult(
            status="materialisation_invariant_failure",
            ledger=None,
            errors=_bounded_errors([f"{type(exc).__name__}:{exc}"]),
            local_id_map=None,
        )
    except Exception as exc:  # pragma: no cover - final bounded safety boundary
        return MaterialisationResult(
            status="materialisation_invariant_failure",
            ledger=None,
            errors=_bounded_errors([f"unexpected:{type(exc).__name__}"]),
            local_id_map=None,
        )


def _synthetic_behavioral_validation_case(
    project_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Build one wholly synthetic current-turn materialisation case."""

    fixture_dir = (
        project_dir
        / "proposition_ledger_research/phase1/synthetic-fixtures/01-direct-question-answer"
    )
    prior = phase1._read_json(fixture_dir / "expected-ledger.json")
    if not isinstance(prior, dict):
        raise phase1.Phase1Error("synthetic behavioral predecessor is not an object")
    prior["schema_version"] = PERSISTED_LEDGER_SCHEMA_VERSION
    prior["ledger_sha256"] = phase1.ledger_sha256(prior)
    current_text = "The village bridge remains open today."
    current_turn = {
        "language": "en",
        "parent_turn_id": prior["target_turn_id"],
        "post_id": "synthetic-semantic-materialiser-post-2",
        "speaker_id": "contributor",
        "text": current_text,
        "turn_id": "semantic-self-test-t2",
        "turn_index": prior["as_of_turn_index"] + 1,
    }
    evidence = {
        "end_char": len(current_text),
        "exact_text": current_text,
        "start_char": 0,
        "turn_id": current_turn["turn_id"],
    }
    delta = {
        "abstentions": [],
        "answer_target_changes": [],
        "as_of_turn_index": current_turn["turn_index"],
        "commitment_changes": [
            {
                "basis": "explicit_speech_act",
                "confidence": 1.0,
                "exact_evidence_spans": [copy.deepcopy(evidence)],
                "local_ref": "new-commitment-1",
                "operation": "add",
                "participant_id": "contributor",
                "proposition_ref": "new-proposition-1",
                "stance": "asserted",
                "uncertainty_reason": None,
            }
        ],
        "conversation_key": prior["conversation_key"],
        "extraction_status": "complete",
        "issue_state_updates": [],
        "new_issue_states": [],
        "new_proposition_groups": [],
        "new_propositions": [
            {
                "canonical_text": "The village bridge remains open today.",
                "commitment_status": "speaker_committed",
                "confidence": 1.0,
                "derivation": {
                    "kind": "direct_span",
                    "normalisation_note": None,
                    "source_proposition_refs": [],
                },
                "epistemic_status": "asserted",
                "exact_evidence_spans": [copy.deepcopy(evidence)],
                "lifecycle_status": "live",
                "local_ref": "new-proposition-1",
                "modality": {"strength": "none", "type": "none"},
                "original_language": "en",
                "polarity": "positive",
                "proposition_group_ref": None,
                "proposition_kind": "descriptive",
                "quantification": {"surface_marker": None, "type": "none"},
                "speaker_or_attributor": {
                    "attributed_participant_id": None,
                    "kind": "speaker",
                    "participant_id": "contributor",
                },
                "speech_act": "assertion",
                "temporal_scope": {
                    "end": None,
                    "start": None,
                    "surface_marker": "today",
                    "type": "present",
                },
                "uncertainty_reason": None,
            }
        ],
        "new_relations": [
            {
                "analysis_basis": "direct_semantic_content",
                "asserted_or_analysed_by": "contributor",
                "confidence": 1.0,
                "exact_evidence_spans": [copy.deepcopy(evidence)],
                "local_ref": "new-relation-1",
                "provenance_kind": "transcript_extraction",
                "relation_type": "supports",
                "source_proposition_refs": ["new-proposition-1"],
                "target_proposition_refs": ["p-bridge-open"],
                "uncertainty_reason": None,
            }
        ],
        "obligation_changes": [],
        "prior_ledger_reference": {
            "as_of_turn_index": prior["as_of_turn_index"],
            "ledger_id": prior["ledger_id"],
        },
        "proposition_group_updates": [],
        "proposition_updates": [],
        "rejected_answer_target_changes": [],
        "repair_records": [],
        "resolved_items": [],
        "schema_version": SEMANTIC_DELTA_SCHEMA_VERSION,
        "target_turn_id": current_turn["turn_id"],
        "unsupported_inferences_rejected": 0,
        "warnings": [],
    }
    return prior, current_turn, delta


def _synthetic_empty_delta(
    conversation_key: str,
    current_turn: Mapping[str, Any],
    previous_ledger: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return a schema-complete semantic no-op for one invented turn."""

    return {
        "abstentions": [],
        "answer_target_changes": [],
        "as_of_turn_index": current_turn["turn_index"],
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
                "as_of_turn_index": previous_ledger["as_of_turn_index"],
                "ledger_id": previous_ledger["ledger_id"],
            }
            if previous_ledger is not None
            else None
        ),
        "proposition_group_updates": [],
        "proposition_updates": [],
        "rejected_answer_target_changes": [],
        "repair_records": [],
        "resolved_items": [],
        "schema_version": SEMANTIC_DELTA_SCHEMA_VERSION,
        "target_turn_id": current_turn["turn_id"],
        "unsupported_inferences_rejected": 0,
        "warnings": [],
    }


def _synthetic_incremental_chain_validation() -> dict[str, Any]:
    """Exercise genesis, first-seen registration, and a returning speaker."""

    conversation_key = "synthetic:semantic-materialiser-chain-v2"
    participants = {
        "account": {
            "author_key": "synthetic-chain-account",
            "identity_confidence": 1.0,
            "participant_id": "account",
            "role": "account",
        },
        "contributor": {
            "author_key": "synthetic-chain-contributor",
            "identity_confidence": 1.0,
            "participant_id": "contributor",
            "role": "contributor",
        },
    }
    turns = [
        {
            "conversation_key": conversation_key,
            "parent_turn_id": None,
            "post_id": "synthetic-chain-post-0",
            "speaker_id": "account",
            "text": "An invented account root.",
            "turn_id": "synthetic-chain-t0",
            "turn_index": 0,
        },
        {
            "conversation_key": conversation_key,
            "parent_turn_id": "synthetic-chain-t0",
            "post_id": "synthetic-chain-post-1",
            "speaker_id": "contributor",
            "text": "An invented contributor reply.",
            "turn_id": "synthetic-chain-t1",
            "turn_index": 1,
        },
        {
            "conversation_key": conversation_key,
            "parent_turn_id": "synthetic-chain-t1",
            "post_id": "synthetic-chain-post-2",
            "speaker_id": "account",
            "text": "An invented account response.",
            "turn_id": "synthetic-chain-t2",
            "turn_index": 2,
        },
        {
            "conversation_key": conversation_key,
            "parent_turn_id": "synthetic-chain-t2",
            "post_id": "synthetic-chain-post-3",
            "speaker_id": "contributor",
            "text": "The invented contributor returns.",
            "turn_id": "synthetic-chain-t3",
            "turn_index": 3,
        },
    ]
    genesis_context = {
        "conversation_key": conversation_key,
        "current_participant": copy.deepcopy(participants["account"]),
        "root_post_id": turns[0]["post_id"],
        "source_completeness": {
            "account_publication_confirmed": True,
            "chronology_complete": True,
            "complete_prefix_through_turn": True,
            "exact_text_complete": True,
            "limitations": ["Wholly invented materialiser validation chain."],
            "parent_graph_complete": True,
            "reconstruction_grade": "A",
        },
    }
    snapshots: list[dict[str, Any]] = []
    statuses: list[str] = []
    previous: dict[str, Any] | None = None
    for index, turn in enumerate(turns):
        participant = participants[str(turn["speaker_id"])]
        result = materialise_semantic_delta(
            previous,
            turn,
            _synthetic_empty_delta(conversation_key, turn, previous),
            current_participant=participant,
            genesis_context=genesis_context if index == 0 else None,
        )
        statuses.append(result.status)
        if not isinstance(result.ledger, dict):
            break
        snapshots.append(result.ledger)
        previous = result.ledger

    genesis_valid = (
        len(snapshots) >= 1
        and statuses[0] == SUCCESS_STATUS
        and snapshots[0].get("previous_ledger_sha256") is None
        and snapshots[0].get("state_transitions", [{}])[0].get(
            "from_ledger_sha256"
        )
        is None
        and [
            record.get("participant_id")
            for record in snapshots[0].get("participants", [])
        ]
        == ["account"]
    )
    first_seen_valid = (
        len(snapshots) >= 2
        and statuses[1] == SUCCESS_STATUS
        and [
            record.get("participant_id")
            for record in snapshots[1].get("participants", [])
        ]
        == ["account", "contributor"]
        and _participant_patch_valid(snapshots[1], "contributor", True)
    )
    expected_participants = [
        ["account"],
        ["account", "contributor"],
        ["account", "contributor"],
        ["account", "contributor"],
    ]
    complete_chain_valid = (
        len(snapshots) == len(turns)
        and statuses == [SUCCESS_STATUS] * len(turns)
        and all(
            [
                record.get("participant_id")
                for record in snapshot.get("participants", [])
            ]
            == expected_participants[index]
            and snapshot.get("ledger_sha256") == phase1.ledger_sha256(snapshot)
            and len(snapshot.get("turn_refs", [])) == index + 1
            for index, snapshot in enumerate(snapshots)
        )
        and _participant_patch_valid(snapshots[2], "account", False)
        and _participant_patch_valid(snapshots[3], "contributor", False)
    )
    return {
        "complete_incremental_chain_valid": complete_chain_valid,
        "first_seen_participant_registration_valid": first_seen_valid,
        "genesis_materialisation_valid": genesis_valid,
        "incremental_chain_statuses": statuses,
    }


def behavioral_materialiser_validation(project_dir: Path) -> dict[str, Any]:
    """Run a bounded, wholly synthetic behavioral materialiser validation."""

    base_result = {
        "case_id": "synthetic-semantic-materialiser-current-turn-v2",
        "materialiser_version": MATERIALISER_VERSION,
        "provider_calls": 0,
        "real_conversation_inputs": 0,
        "schema_version": "proposition-ledger-semantic-materialiser-validation-v2",
    }
    try:
        prior, current_turn, delta = _synthetic_behavioral_validation_case(
            project_dir
        )
        current_participant = next(
            copy.deepcopy(record)
            for record in prior["participants"]
            if record["participant_id"] == current_turn["speaker_id"]
        )
        first = materialise_semantic_delta(
            prior,
            current_turn,
            delta,
            current_participant=current_participant,
        )
        second = materialise_semantic_delta(
            copy.deepcopy(prior),
            copy.deepcopy(current_turn),
            copy.deepcopy(delta),
            current_participant=copy.deepcopy(current_participant),
        )
        first_ledger = first.ledger if isinstance(first.ledger, dict) else None
        second_ledger = second.ledger if isinstance(second.ledger, dict) else None
        ledger_schema = _load_json_schema(
            project_dir
            / "proposition_ledger_research/schema/proposition-ledger-v1.schema.json"
        )
        full_validator_errors = (
            phase1.validate_ledger_incremental(
                first_ledger,
                prior,
                current_turn,
                ledger_schema,
            )
            if first_ledger is not None
            else ["valid_materialisation_did_not_return_ledger"]
        )
        deterministic_bytes = (
            first_ledger is not None
            and second_ledger is not None
            and phase1.canonical_json_bytes(first_ledger)
            == phase1.canonical_json_bytes(second_ledger)
        )
        deterministic_hash = (
            first_ledger is not None
            and second_ledger is not None
            and first_ledger.get("ledger_sha256")
            == phase1.ledger_sha256(first_ledger)
            == second_ledger.get("ledger_sha256")
        )
        local_id_map = first.local_id_map or {}
        new_proposition_id = local_id_map.get("new-proposition-1")
        new_relation_id = local_id_map.get("new-relation-1")
        new_commitment_id = local_id_map.get("new-commitment-1")
        relation = next(
            (
                record
                for record in first_ledger.get("proposition_relations", [])
                if record.get("relation_id") == new_relation_id
            ),
            None,
        ) if first_ledger is not None else None
        commitment = next(
            (
                record
                for record in first_ledger.get("participant_commitments", [])
                if record.get("commitment_id") == new_commitment_id
            ),
            None,
        ) if first_ledger is not None else None
        local_references_resolved = (
            isinstance(new_proposition_id, str)
            and isinstance(relation, Mapping)
            and relation.get("source_proposition_ids") == [new_proposition_id]
            and isinstance(commitment, Mapping)
            and commitment.get("proposition_id") == new_proposition_id
        )
        chain_validation = _synthetic_incremental_chain_validation()

        failure_inputs: dict[
            str, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]
        ] = {}
        schema_invalid = copy.deepcopy(delta)
        schema_invalid["ledger_sha256"] = "0" * 64
        failure_inputs["semantic_delta_schema_invalid"] = (
            prior,
            current_turn,
            schema_invalid,
        )

        reference_invalid = copy.deepcopy(delta)
        reference_invalid["new_relations"][0]["target_proposition_refs"] = [
            "p-missing-from-prior"
        ]
        failure_inputs["semantic_reference_invalid"] = (
            prior,
            current_turn,
            reference_invalid,
        )

        evidence_invalid = copy.deepcopy(delta)
        evidence_invalid["new_propositions"][0]["exact_evidence_spans"][0][
            "exact_text"
        ] = "This does not match the synthetic current turn."
        failure_inputs["semantic_evidence_invalid"] = (
            prior,
            current_turn,
            evidence_invalid,
        )

        transition_invalid = copy.deepcopy(delta)
        transition_invalid["proposition_updates"] = [
            {
                "changes": {"lifecycle_status": "introduced"},
                "exact_evidence_spans": copy.deepcopy(
                    delta["new_propositions"][0]["exact_evidence_spans"]
                ),
                "proposition_id": "p-bridge-open",
                "reason": "Synthetic invalid reopening transition.",
            }
        ]
        failure_inputs["semantic_transition_invalid"] = (
            prior,
            current_turn,
            transition_invalid,
        )

        invariant_prior = copy.deepcopy(prior)
        invariant_prior["ledger_sha256"] = "0" * 64
        failure_inputs["materialisation_invariant_failure"] = (
            invariant_prior,
            current_turn,
            delta,
        )

        persisted_invalid = copy.deepcopy(delta)
        persisted_invalid["new_propositions"][0]["proposition_group_ref"] = (
            "new-proposition-group-1"
        )
        persisted_invalid["new_proposition_groups"] = [
            {
                "confidence": 1.0,
                "decomposition_complete": True,
                "exact_evidence_spans": copy.deepcopy(
                    delta["new_propositions"][0]["exact_evidence_spans"]
                ),
                "local_ref": "new-proposition-group-1",
                "members": [
                    {
                        "ordinal": 0,
                        "proposition_ref": "new-proposition-1",
                        "role": "conjunct",
                    },
                    {
                        "ordinal": 1,
                        "proposition_ref": "p-bridge-open",
                        "role": "conjunct",
                    },
                ],
                "structure_type": "conjunction",
                "uncertainty_reason": None,
            }
        ]
        failure_inputs["persisted_ledger_validation_failure"] = (
            prior,
            current_turn,
            persisted_invalid,
        )

        failure_results = {
            expected: materialise_semantic_delta(
                copy.deepcopy(case_prior),
                copy.deepcopy(case_turn),
                copy.deepcopy(case_delta),
                current_participant=copy.deepcopy(current_participant),
            )
            for expected, (case_prior, case_turn, case_delta) in failure_inputs.items()
        }
        observed_failure_statuses = {
            expected: result.status
            for expected, result in sorted(failure_results.items())
        }
        failures_distinguishable = (
            set(observed_failure_statuses) == FAILURE_STATUSES
            and all(
                observed == expected
                for expected, observed in observed_failure_statuses.items()
            )
        )
        failures_bounded = all(
            result.ledger is None
            and 0 < len(result.errors) <= MAX_FAILURE_ERRORS
            and all(len(error) <= MAX_FAILURE_ERROR_LENGTH for error in result.errors)
            for result in failure_results.values()
        )
        result = {
            **base_result,
            **chain_validation,
            "deterministic_bytes": deterministic_bytes,
            "deterministic_ledger_hash": deterministic_hash,
            "distinguishable_failure_statuses": failures_distinguishable,
            "failure_errors_bounded": failures_bounded,
            "full_persisted_validator_errors": _bounded_errors(
                full_validator_errors
            ) if full_validator_errors else [],
            "full_persisted_validator_passed": not full_validator_errors,
            "local_references_resolved": local_references_resolved,
            "observed_failure_statuses": observed_failure_statuses,
            "repeated_materialisation_status": second.status,
            "valid_materialisation_status": first.status,
        }
        result["passed"] = all(
            (
                first.status == SUCCESS_STATUS,
                second.status == SUCCESS_STATUS,
                result["full_persisted_validator_passed"],
                deterministic_bytes,
                deterministic_hash,
                local_references_resolved,
                result["genesis_materialisation_valid"],
                result["first_seen_participant_registration_valid"],
                result["complete_incremental_chain_valid"],
                failures_distinguishable,
                failures_bounded,
            )
        )
        return result
    except Exception as exc:  # pragma: no cover - bounded validation harness failure
        return {
            **base_result,
            "harness_error": f"unexpected:{type(exc).__name__}",
            "passed": False,
        }


__all__ = [
    "behavioral_materialiser_validation",
    "FAILURE_STATUSES",
    "MaterialisationResult",
    "MATERIALISER_VERSION",
    "PERSISTED_LEDGER_SCHEMA_VERSION",
    "SEMANTIC_DELTA_SCHEMA_VERSION",
    "SUCCESS_STATUS",
    "materialise_semantic_delta",
]
