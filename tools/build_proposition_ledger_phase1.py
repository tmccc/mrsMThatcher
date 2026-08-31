#!/usr/bin/env python3
"""Build and verify the read-only proposition-ledger Phase 1.2 preflight pack.

The tool deliberately has no production-module imports and no network or model
client. All source locations are explicit, frozen inputs. Conversation text is
written only beneath the caller-supplied mode-0700 private output directory.
"""

from __future__ import annotations

import argparse
import ast
import copy
import csv
import hashlib
import hmac
import importlib
import json
import math
import os
import re
import stat
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


LEDGER_SCHEMA_VERSION = "proposition-ledger-v1.0.0"
SEMANTIC_DELTA_SCHEMA_VERSION = "proposition-ledger-semantic-delta-v1.1.0"
EXPERIMENT_SCHEMA_VERSION = "proposition-ledger-experiment-v1.2.0"
SOURCE_MANIFEST_VERSION = "proposition-ledger-phase1-source-manifest-v1"
OUTPUT_SCHEMA_VERSION = "proposition-ledger-phase1.2-output-v2"
PHASE1_1_BASE_SHA = "47cd7579fdbe2da07bd032ae23be7fad763626f6"
MATERIALISER_ID = "proposition-ledger-semantic-delta-materialiser-v2"
FRESH_BUILD_DETERMINISM_EVIDENCE = (
    "two fresh full private-directory builds compared byte-for-byte; "
    "only run path/timestamp metadata was isolated"
)
ZERO_SHA256 = "0" * 64
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RAW_ID_KEYS = {
    "author_id",
    "author_name",
    "author_username",
    "contributor_id",
    "raw_author_id",
    "raw_contributor_id",
    "screen_name",
    "user_id",
    "username",
}
PRIVATE_TEXT_KEYS = {
    "candidate_a",
    "candidate_b",
    "canonical_text",
    "exact_text",
    "final_ordinary_candidate_reply",
    "historical_published_reply",
    "incoming_text",
    "parent_bot_text",
    "public_text",
    "reply_text",
    "text",
}
EXPOSED_STATUSES = {
    "development_labelled",
    "development_unlabelled_but_seen",
    "calibration",
    "prior_model_experiment",
    "prior_human_review",
    "report_excerpt",
    "current_manual_incident_review",
}
ALL_EXPOSURE_STATUSES = EXPOSED_STATUSES | {
    "structurally_mined_only",
    "unexposed_candidate",
}
OUTCOME_EVIDENCE_CLASSES = (
    "confirmed_published_reply",
    "confirmed_pipeline_terminal_no_reply",
    "confirmed_local_skip",
    "quiescent_unreplied_tip_outcome_unknown",
    "outcome_evidence_unavailable",
    "conflicting_outcome_evidence",
)
TARGET_SEQUENCE_CLASSES = (
    "initial_user_target",
    "pre_account_user_follow_up",
    "account_root_response",
    "persistent_multiturn_target",
    "other_sequence",
)
OUTCOME_TARGET_ID_FIELDS = {
    "confirmed_published_reply": "published_reply_target_turn_ids",
    "confirmed_pipeline_terminal_no_reply": (
        "confirmed_pipeline_terminal_no_reply_target_turn_ids"
    ),
    "confirmed_local_skip": "confirmed_local_skip_target_turn_ids",
    "quiescent_unreplied_tip_outcome_unknown": (
        "quiescent_unreplied_tip_outcome_unknown_target_turn_ids"
    ),
    "outcome_evidence_unavailable": (
        "outcome_evidence_unavailable_target_turn_ids"
    ),
    "conflicting_outcome_evidence": (
        "conflicting_outcome_evidence_target_turn_ids"
    ),
}
TARGET_STRUCTURAL_EXCLUSION_REASONS = (
    "declared_root_identity_unavailable",
    "target_absent_from_canonical_conversation",
    "target_role_not_user",
    "target_post_identity_missing",
    "target_ancestry_cycle",
    "target_ancestry_does_not_reach_declared_root",
    "target_prefix_parent_graph_ambiguous_or_incomplete",
    "target_prefix_text_incomplete",
    "target_prefix_immutable_identity_incomplete",
    "target_prefix_role_assignment_incomplete",
    "target_prefix_turn_order_ambiguous",
)
STABILITY_STATUSES = (
    "frozen_historical",
    "quiescent_at_frozen_cutoff",
    "open_at_frozen_cutoff",
    "stability_unknown",
)
PROPOSITION_LIFECYCLE_TRANSITIONS = {
    "introduced": {"live", "challenged", "qualified", "conceded", "withdrawn", "resolved", "superseded", "abandoned"},
    "live": {"challenged", "qualified", "conceded", "withdrawn", "resolved", "superseded", "abandoned"},
    "challenged": {"live", "qualified", "conceded", "withdrawn", "resolved", "superseded", "abandoned"},
    "qualified": {"live", "challenged", "conceded", "withdrawn", "resolved", "superseded", "abandoned"},
    "conceded": {"resolved", "superseded"},
    "withdrawn": set(),
    "resolved": set(),
    "superseded": set(),
    "abandoned": set(),
}
LEDGER_STATE_COLLECTIONS = {
    "source_completeness": None,
    "participants": "participant_id",
    "turn_refs": "turn_id",
    "propositions": "proposition_id",
    "proposition_groups": "proposition_group_id",
    "issue_states": "issue_id",
    "participant_commitments": "commitment_id",
    "conversational_obligations": "obligation_id",
    "proposition_relations": "relation_id",
    "answer_targets": "answer_target_id",
    "rejected_answer_targets": "rejected_answer_target_id",
    "repair_records": "repair_id",
    "unresolved_items": "item_ref",
    "resolved_items": "item_ref",
    "extraction_status": None,
    "warnings": "warning_id",
}
TRANSITION_ADDED_COLLECTIONS = {
    "propositions_added": "propositions",
    "proposition_groups_added": "proposition_groups",
    "issue_states_added": "issue_states",
    "commitments_added": "participant_commitments",
    "obligations_added": "conversational_obligations",
    "relations_added": "proposition_relations",
    "answer_targets_added": "answer_targets",
    "rejected_answer_targets_added": "rejected_answer_targets",
    "repair_records_added": "repair_records",
    "warnings_added": "warnings",
}
TRANSITION_UPDATED_COLLECTIONS = {
    "propositions_updated": ("propositions", "lifecycle_status"),
    "proposition_groups_updated": ("proposition_groups", None),
    "issue_states_updated": ("issue_states", "status"),
    "commitments_updated": ("participant_commitments", "stance"),
    "obligations_updated": ("conversational_obligations", "status"),
    "answer_targets_updated": ("answer_targets", "target_status"),
    "rejected_answer_targets_updated": ("rejected_answer_targets", "status"),
    "repair_records_updated": ("repair_records", "outcome"),
}


class Phase1Error(RuntimeError):
    """Describe a deterministic Phase 1 validation or input failure."""


def _import_research_tool(module_name: str) -> Any:
    """Import a sibling research helper in package and direct-script modes."""
    try:
        return importlib.import_module(f"tools.{module_name}")
    except ModuleNotFoundError as exc:
        if exc.name != "tools":
            raise
        return importlib.import_module(module_name)


def _reject_json_constant(value: str) -> None:
    raise Phase1Error(f"non-finite JSON value is forbidden: {value}")


def canonical_json_bytes(value: Any) -> bytes:
    """Return stable compact UTF-8 JSON bytes with non-finite numbers rejected."""
    _assert_finite(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    """Return the lowercase SHA-256 digest of *data*."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Hash one regular file without following a final-component symlink."""
    opened = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        digest = hashlib.sha256()
        while True:
            block = os.read(opened, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        return digest.hexdigest()
    finally:
        os.close(opened)


def _collection_files(entry: Mapping[str, Any]) -> list[Path]:
    root = Path(str(entry.get("path") or ""))
    pattern = str(entry.get("collection_pattern") or "*")
    iterator = root.rglob(pattern) if entry.get("collection_recursive") else root.glob(pattern)
    return sorted(path for path in iterator if path.is_file() and not path.is_symlink())


def _source_digest(entry: Mapping[str, Any]) -> str:
    path = Path(str(entry.get("path") or ""))
    if entry.get("source_collection"):
        material = [
            {
                "relative_path": str(file_path.relative_to(path)),
                "sha256": sha256_file(file_path),
                "size_bytes": file_path.stat().st_size,
            }
            for file_path in _collection_files(entry)
        ]
        return sha256_bytes(canonical_json_bytes(material))
    return sha256_file(path)


def ledger_sha256(ledger: Mapping[str, Any]) -> str:
    """Hash a ledger after replacing its self-referential digest with zeroes."""
    material = copy.deepcopy(dict(ledger))
    material["ledger_sha256"] = ZERO_SHA256
    return sha256_bytes(canonical_json_bytes(material))


def state_record_sha256(record: Mapping[str, Any]) -> str:
    """Hash one canonical ledger-state record for an incremental patch."""
    return sha256_bytes(canonical_json_bytes(dict(record)))


def _state_item_id(collection: str, record: Mapping[str, Any], index: int) -> str:
    id_field = LEDGER_STATE_COLLECTIONS[collection]
    if id_field is None:
        return collection
    if id_field == "item_ref":
        item_type = record.get("item_type")
        item_id = record.get("item_id")
        if isinstance(item_type, str) and item_type and isinstance(item_id, str) and item_id:
            return f"{item_type}:{item_id}"
        return f"invalid:{index}"
    value = record.get(id_field)
    return str(value) if isinstance(value, str) and value else f"invalid:{index}"


def ledger_state_projection(ledger: Mapping[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    """Return the canonical state reconstructed by a transition patch.

    Arrays are keyed by their schema identifier.  This deliberately excludes
    snapshot identity, self-hashes, and the transition itself: those are
    derived from the previous snapshot and current transcript turn.
    """
    projection: dict[str, dict[str, dict[str, Any]]] = {}
    for collection, id_field in LEDGER_STATE_COLLECTIONS.items():
        value = ledger.get(collection)
        records: dict[str, dict[str, Any]] = {}
        if id_field is None:
            if isinstance(value, Mapping):
                records[collection] = copy.deepcopy(dict(value))
        elif isinstance(value, list):
            for index, record in enumerate(value):
                if isinstance(record, Mapping):
                    item_id = _state_item_id(collection, record, index)
                    if item_id in records:
                        raise Phase1Error(f"duplicate_state_key:{collection}:{item_id}")
                    records[item_id] = copy.deepcopy(dict(record))
        projection[collection] = records
    return projection


def build_state_patch(
    previous_ledger: Mapping[str, Any] | None,
    current_ledger: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Build the complete deterministic record patch between two snapshots."""
    before = (
        ledger_state_projection(previous_ledger)
        if previous_ledger is not None
        else {collection: {} for collection in LEDGER_STATE_COLLECTIONS}
    )
    after = ledger_state_projection(current_ledger)
    patch: list[dict[str, Any]] = []
    for collection in LEDGER_STATE_COLLECTIONS:
        before_records = before[collection]
        after_records = after[collection]
        for item_id in sorted(set(before_records) | set(after_records)):
            old = before_records.get(item_id)
            new = after_records.get(item_id)
            if old == new:
                continue
            if old is None:
                operation = "add"
            elif new is None:
                operation = "remove"
            else:
                operation = "replace"
            patch.append(
                {
                    "after_record": copy.deepcopy(new),
                    "before_record_sha256": None if old is None else state_record_sha256(old),
                    "collection": collection,
                    "item_id": item_id,
                    "operation": operation,
                }
            )
    return patch


def apply_state_patch(
    previous_ledger: Mapping[str, Any] | None,
    patch: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, dict[str, Any]]], list[str]]:
    """Apply an explicit patch and return its projection plus deterministic errors."""
    projection = (
        ledger_state_projection(previous_ledger)
        if previous_ledger is not None
        else {collection: {} for collection in LEDGER_STATE_COLLECTIONS}
    )
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for index, operation in enumerate(patch):
        collection = str(operation.get("collection") or "")
        item_id = str(operation.get("item_id") or "")
        key = (collection, item_id)
        if key in seen:
            errors.append(f"duplicate_state_patch_target:{collection}:{item_id}")
            continue
        seen.add(key)
        if collection not in projection:
            errors.append(f"unknown_state_patch_collection:{index}:{collection}")
            continue
        records = projection[collection]
        old = records.get(item_id)
        expected_before = None if old is None else state_record_sha256(old)
        if operation.get("before_record_sha256") != expected_before:
            errors.append(f"state_patch_before_hash_mismatch:{collection}:{item_id}")
            continue
        kind = operation.get("operation")
        after_record = operation.get("after_record")
        if kind == "add":
            if old is not None or not isinstance(after_record, Mapping):
                errors.append(f"invalid_state_patch_add:{collection}:{item_id}")
                continue
            records[item_id] = copy.deepcopy(dict(after_record))
        elif kind == "remove":
            if old is None or after_record is not None:
                errors.append(f"invalid_state_patch_remove:{collection}:{item_id}")
                continue
            del records[item_id]
        elif kind == "replace":
            if old is None or not isinstance(after_record, Mapping):
                errors.append(f"invalid_state_patch_replace:{collection}:{item_id}")
                continue
            records[item_id] = copy.deepcopy(dict(after_record))
        else:
            errors.append(f"invalid_state_patch_operation:{collection}:{item_id}")
    return projection, errors


def hmac_author_key(secret: bytes, source_family: str, raw_value: str) -> str:
    """Return a source-scoped HMAC-SHA256 contributor pseudonym."""
    message = f"proposition-ledger-phase1\0{source_family}\0{raw_value}".encode("utf-8")
    return "author-hmac-" + hmac.new(secret, message, hashlib.sha256).hexdigest()


def _assert_finite(value: Any, location: str = "$") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise Phase1Error(f"non-finite number at {location}")
    if isinstance(value, dict):
        for key, child in value.items():
            _assert_finite(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_finite(child, f"{location}[{index}]")


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle, parse_constant=_reject_json_constant)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.endswith("\n"):
                raise Phase1Error(f"incomplete JSONL line: {path}:{line_number}")
            if not line.strip():
                continue
            value = json.loads(line, parse_constant=_reject_json_constant)
            if not isinstance(value, dict):
                raise Phase1Error(f"JSONL row is not an object: {path}:{line_number}")
            rows.append(value)
    return rows


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    actual = stat.S_IMODE(path.stat().st_mode)
    if actual != 0o700:
        raise Phase1Error(f"private directory must be mode 0700: {path} ({actual:o})")


def _write_private(path: Path, payload: bytes) -> None:
    _ensure_private_directory(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _write_json(path: Path, value: Any) -> None:
    _assert_finite(value)
    payload = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write_private(path, payload)


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    payload = b"".join(canonical_json_bytes(dict(row)) + b"\n" for row in rows)
    _write_private(path, payload)


def _write_markdown(path: Path, lines: Iterable[str]) -> None:
    _write_private(path, ("\n".join(lines).rstrip() + "\n").encode("utf-8"))


def _draft7_compatible_schema(value: Any) -> Any:
    if isinstance(value, list):
        return [_draft7_compatible_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _draft7_compatible_schema(child) for key, child in value.items() if key != "prefixItems"}
    if "prefixItems" in value:
        result["items"] = [_draft7_compatible_schema(item) for item in value["prefixItems"]]
        if value.get("items") is False:
            result["additionalItems"] = False
    return result


def _jsonschema_validator(schema: Mapping[str, Any]) -> Any:
    try:
        import jsonschema
    except ImportError as exc:  # pragma: no cover - dependency is in requirements-dev
        raise Phase1Error("jsonschema is required for Phase 1 validation") from exc
    validator_class = getattr(jsonschema, "Draft202012Validator", jsonschema.Draft7Validator)
    effective_schema = schema if hasattr(jsonschema, "Draft202012Validator") else _draft7_compatible_schema(schema)
    return validator_class(effective_schema, format_checker=jsonschema.FormatChecker())


def _jsonschema_errors(instance: Any, schema: Mapping[str, Any]) -> list[str]:
    validator = _jsonschema_validator(schema)
    errors: list[str] = []
    for error in sorted(validator.iter_errors(instance), key=lambda item: list(item.absolute_path)):
        location = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in error.absolute_path
        )
        errors.append(f"schema:{location}:{error.message}")
    return errors


def _turn_text_map(transcript: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, int]]:
    texts: dict[str, str] = {}
    indexes: dict[str, int] = {}
    for fallback_index, turn in enumerate(transcript.get("turns", [])):
        turn_id = str(turn.get("turn_id") or "")
        if not turn_id or turn_id in texts:
            raise Phase1Error("synthetic transcript turn IDs must be non-empty and unique")
        text = turn.get("text")
        if not isinstance(text, str):
            raise Phase1Error(f"turn text must be a string: {turn_id}")
        texts[turn_id] = text
        indexes[turn_id] = int(turn.get("turn_index", fallback_index))
    return texts, indexes


def _all_evidence_spans(ledger: Mapping[str, Any]) -> Iterable[tuple[str, Mapping[str, Any]]]:
    collections = (
        "propositions",
        "proposition_groups",
        "issue_states",
        "proposition_relations",
        "rejected_answer_targets",
        "repair_records",
    )
    for collection in collections:
        for record in ledger.get(collection, []):
            for span in record.get("exact_evidence_spans", []):
                yield str(record.get(next((key for key in record if key.endswith("_id")), ""), collection)), span


def _referenced_turn_ids(ledger: Mapping[str, Any]) -> Iterable[tuple[str, str]]:
    direct_fields = {
        "target_turn_id",
        "introduced_at_turn_id",
        "initiating_turn_id",
        "resolution_turn_id",
        "last_updated_at_turn_id",
        "created_by_turn_id",
        "resolved_by_turn_id",
        "reply_turn_id",
        "selected_at_turn_id",
        "rejected_at_turn_id",
        "trigger_turn_id",
        "acknowledgement_turn_id",
        "current_turn_id",
        "first_opened_at_turn_id",
        "resolved_at_turn_id",
    }

    def walk(value: Any, location: str) -> Iterable[tuple[str, str]]:
        if isinstance(value, dict):
            for key, child in value.items():
                child_location = f"{location}.{key}"
                if key in direct_fields and isinstance(child, str):
                    yield child_location, child
                if key == "turn_id" and isinstance(child, str) and "exact_evidence_spans" in location:
                    yield child_location, child
                yield from walk(child, child_location)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                yield from walk(child, f"{location}[{index}]")

    yield from walk(ledger, "$")


def _id_sets(ledger: Mapping[str, Any]) -> dict[str, set[str]]:
    mapping = {
        "participants": ("participants", "participant_id"),
        "turns": ("turn_refs", "turn_id"),
        "propositions": ("propositions", "proposition_id"),
        "groups": ("proposition_groups", "proposition_group_id"),
        "issues": ("issue_states", "issue_id"),
        "commitments": ("participant_commitments", "commitment_id"),
        "obligations": ("conversational_obligations", "obligation_id"),
        "relations": ("proposition_relations", "relation_id"),
        "answer_targets": ("answer_targets", "answer_target_id"),
        "rejected_targets": ("rejected_answer_targets", "rejected_answer_target_id"),
        "repairs": ("repair_records", "repair_id"),
        "warnings": ("warnings", "warning_id"),
    }
    result: dict[str, set[str]] = {}
    for label, (collection, id_field) in mapping.items():
        identifiers = [str(item[id_field]) for item in ledger.get(collection, []) if id_field in item]
        result[label] = set(identifiers)
        if len(identifiers) != len(result[label]):
            result[label].add("__duplicate_identifier__")
    return result


def validate_ledger(
    ledger: Mapping[str, Any],
    transcript: Mapping[str, Any],
    schema: Mapping[str, Any],
    *,
    _immediate_previous: Mapping[str, Any] | None = None,
    _validate_history: bool = True,
) -> list[str]:
    """Return deterministic structural and cross-record errors for one ledger."""
    errors = _jsonschema_errors(ledger, schema)
    try:
        transcript_texts, transcript_indexes = _turn_text_map(transcript)
    except Phase1Error as exc:
        return sorted([*errors, f"transcript:{exc}"])
    as_of = ledger.get("as_of_turn_index")
    if not isinstance(as_of, int):
        return sorted(errors)
    ids = _id_sets(ledger)
    for label, values in ids.items():
        if "__duplicate_identifier__" in values:
            errors.append(f"duplicate_id:{label}")
            values.discard("__duplicate_identifier__")
    participant_ids = ids["participants"]
    turn_refs = {str(turn.get("turn_id")): turn for turn in ledger.get("turn_refs", [])}
    target_turn_id = str(ledger.get("target_turn_id") or "")
    if target_turn_id not in transcript_indexes or transcript_indexes.get(target_turn_id) != as_of:
        errors.append(f"target_as_of_mismatch:{target_turn_id}:{as_of}")
    transcript_turns = [
        turn for turn in transcript.get("turns", []) if isinstance(turn, Mapping)
    ]
    if transcript_turns:
        root_turn = min(
            enumerate(transcript_turns),
            key=lambda pair: int(pair[1].get("turn_index", pair[0])),
        )[1]
        transcript_root = root_turn.get("post_id")
        if isinstance(transcript_root, str) and ledger.get("root_post_id") != transcript_root:
            errors.append("transcript_root_post_id_mismatch")
    transcript_conversation_key = transcript.get("conversation_key")
    if (
        isinstance(transcript_conversation_key, str)
        and ledger.get("conversation_key") != transcript_conversation_key
    ):
        errors.append("transcript_conversation_key_mismatch")
    expected_prefix = [
        turn_id
        for turn_id, index in sorted(transcript_indexes.items(), key=lambda item: item[1])
        if index <= as_of
    ]
    actual_prefix = [str(turn.get("turn_id") or "") for turn in ledger.get("turn_refs", [])]
    if actual_prefix != expected_prefix:
        errors.append("incomplete_turn_prefix")
    for turn_id, turn in turn_refs.items():
        if turn_id not in transcript_texts:
            errors.append(f"orphan_turn_ref:{turn_id}")
            continue
        if turn.get("turn_index") != transcript_indexes[turn_id]:
            errors.append(f"turn_index_mismatch:{turn_id}")
        if turn.get("turn_index", as_of + 1) > as_of:
            errors.append(f"future_turn_reference:{turn_id}")
        expected_text_hash = sha256_bytes(transcript_texts[turn_id].encode("utf-8"))
        if turn.get("text_sha256") != expected_text_hash:
            errors.append(f"turn_text_hash_mismatch:{turn_id}")
        if turn.get("speaker_id") not in participant_ids:
            errors.append(f"orphan_turn_speaker:{turn_id}:{turn.get('speaker_id')}")
        parent_turn = turn.get("parent_turn_id")
        if parent_turn is not None:
            if parent_turn not in turn_refs:
                errors.append(f"orphan_parent_turn:{turn_id}:{parent_turn}")
            elif turn_refs[parent_turn].get("turn_index", as_of + 1) >= turn.get("turn_index", -1):
                errors.append(f"non_backward_parent:{turn_id}:{parent_turn}")
    for location, turn_id in _referenced_turn_ids(ledger):
        if turn_id not in turn_refs:
            errors.append(f"orphan_turn_reference:{location}:{turn_id}")
        elif turn_refs[turn_id].get("turn_index", as_of + 1) > as_of:
            errors.append(f"future_turn_reference:{location}:{turn_id}")
    for owner_id, span in _all_evidence_spans(ledger):
        turn_id = str(span.get("turn_id") or "")
        if turn_id not in transcript_texts:
            errors.append(f"orphan_evidence_turn:{owner_id}:{turn_id}")
            continue
        start = span.get("start_char")
        end = span.get("end_char")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if start < 0 or end <= start or end > len(transcript_texts[turn_id]):
            errors.append(f"evidence_span_out_of_bounds:{owner_id}:{turn_id}:{start}:{end}")
            continue
        actual = transcript_texts[turn_id][start:end]
        if actual != span.get("exact_text"):
            errors.append(f"evidence_span_mismatch:{owner_id}:{turn_id}:{start}:{end}")
    proposition_ids = ids["propositions"]
    group_ids = ids["groups"]
    issue_ids = ids["issues"]
    answer_target_ids = ids["answer_targets"]
    rejected_target_ids = ids["rejected_targets"]
    for proposition in ledger.get("propositions", []):
        group_id = proposition.get("proposition_group_id")
        if group_id is not None and group_id not in group_ids:
            errors.append(f"orphan_group:{proposition.get('proposition_id')}:{group_id}")
        attribution = proposition.get("speaker_or_attributor", {})
        for key in ("participant_id", "attributed_participant_id"):
            participant = attribution.get(key)
            if participant is not None and participant not in participant_ids:
                errors.append(f"orphan_participant:{proposition.get('proposition_id')}:{participant}")
        for source_proposition_id in proposition.get("derivation", {}).get("source_proposition_ids", []):
            if source_proposition_id not in proposition_ids:
                errors.append(
                    f"orphan_derivation_source:{proposition.get('proposition_id')}:{source_proposition_id}"
                )
        if proposition.get("proposition_kind") in {"quoted_claim", "reported_claim"}:
            speaker = attribution.get("participant_id")
            if proposition.get("commitment_status") == "speaker_committed":
                explicit_endorsement = any(
                    item.get("participant_id") == speaker
                    and item.get("proposition_id") == proposition.get("proposition_id")
                    and item.get("stance") == "asserted"
                    and item.get("basis") == "explicit_speech_act"
                    for item in ledger.get("participant_commitments", [])
                )
                if not explicit_endorsement:
                    errors.append(f"quoted_claim_implies_commitment:{proposition.get('proposition_id')}")
    for group in ledger.get("proposition_groups", []):
        members = group.get("members", [])
        member_ids = [member.get("proposition_id") for member in members]
        member_ordinals = [member.get("ordinal") for member in members]
        if len(member_ids) != len(set(member_ids)) or len(member_ordinals) != len(set(member_ordinals)):
            errors.append(f"duplicate_group_membership:{group.get('proposition_group_id')}")
        for proposition_id in member_ids:
            if proposition_id not in proposition_ids:
                errors.append(f"orphan_group_member:{group.get('proposition_group_id')}:{proposition_id}")
            else:
                proposition = next(
                    (item for item in ledger.get("propositions", []) if item.get("proposition_id") == proposition_id),
                    None,
                )
                if proposition and proposition.get("proposition_group_id") != group.get("proposition_group_id"):
                    errors.append(f"nonreciprocal_group_membership:{group.get('proposition_group_id')}:{proposition_id}")
        if group.get("structure_type") == "compound_accusation":
            # A compound accusation preserves each evidenced component, but
            # the transcript need not allege motive.  Two distinct members are
            # sufficient; duplicate identifiers/ordinals are rejected above.
            if len(member_ids) < 2:
                errors.append(f"compound_collapse:{group.get('proposition_group_id')}")
    for proposition in ledger.get("propositions", []):
        group_id = proposition.get("proposition_group_id")
        if group_id is None or group_id not in group_ids:
            continue
        group = next((item for item in ledger.get("proposition_groups", []) if item.get("proposition_group_id") == group_id), None)
        if group and proposition.get("proposition_id") not in {member.get("proposition_id") for member in group.get("members", [])}:
            errors.append(f"nonreciprocal_group_membership:{group_id}:{proposition.get('proposition_id')}")
    for issue in ledger.get("issue_states", []):
        for participant_field in ("initiating_speaker", "addressed_participant"):
            participant = issue.get(participant_field)
            if participant is not None and participant not in participant_ids:
                errors.append(f"orphan_issue_participant:{issue.get('issue_id')}:{participant}")
        for proposition_id in issue.get("related_proposition_ids", []):
            if proposition_id not in proposition_ids:
                errors.append(f"orphan_issue_proposition:{issue.get('issue_id')}:{proposition_id}")
        for alternative in issue.get("live_alternatives", []):
            for proposition_id in alternative.get("proposition_ids", []):
                if proposition_id not in proposition_ids:
                    errors.append(
                        f"orphan_live_alternative_proposition:{issue.get('issue_id')}:{proposition_id}"
                    )
    for commitment in ledger.get("participant_commitments", []):
        if commitment.get("participant_id") not in participant_ids:
            errors.append(f"orphan_commitment_participant:{commitment.get('commitment_id')}")
        if commitment.get("proposition_id") not in proposition_ids:
            errors.append(f"orphan_commitment_proposition:{commitment.get('commitment_id')}")
    for obligation in ledger.get("conversational_obligations", []):
        if obligation.get("owed_by_participant") not in participant_ids or obligation.get("owed_to_participant") not in participant_ids:
            errors.append(f"orphan_obligation_participant:{obligation.get('obligation_id')}")
        for issue_id in obligation.get("related_issue_ids", []):
            if issue_id not in issue_ids:
                errors.append(f"orphan_obligation_issue:{obligation.get('obligation_id')}:{issue_id}")
        for proposition_id in obligation.get("related_proposition_ids", []):
            if proposition_id not in proposition_ids:
                errors.append(f"orphan_obligation_proposition:{obligation.get('obligation_id')}:{proposition_id}")
    for relation in ledger.get("proposition_relations", []):
        for proposition_id in [*relation.get("source_proposition_ids", []), *relation.get("target_proposition_ids", [])]:
            if proposition_id not in proposition_ids:
                errors.append(f"orphan_relation:{relation.get('relation_id')}:{proposition_id}")
        if (
            relation.get("relation_type") in {"substitutes_for", "fails_to_address", "fails_to_answer"}
            and relation.get("provenance_kind") == "transcript_extraction"
            and relation.get("analysis_basis") != "speaker_explicit_metadiscourse"
        ):
            errors.append(f"diagnostic_relation_has_transcript_provenance:{relation.get('relation_id')}")
    for answer_target in ledger.get("answer_targets", []):
        if not answer_target.get("issue_ids") and not answer_target.get("proposition_ids"):
            errors.append(f"empty_answer_target:{answer_target.get('answer_target_id')}")
        for issue_id in answer_target.get("issue_ids", []):
            if issue_id not in issue_ids:
                errors.append(f"orphan_answer_issue:{answer_target.get('answer_target_id')}:{issue_id}")
        for proposition_id in answer_target.get("proposition_ids", []):
            if proposition_id not in proposition_ids:
                errors.append(f"orphan_answer_proposition:{answer_target.get('answer_target_id')}:{proposition_id}")
    for rejected in ledger.get("rejected_answer_targets", []):
        if rejected.get("answer_target_id") not in answer_target_ids:
            errors.append(f"orphan_rejected_answer_target:{rejected.get('rejected_answer_target_id')}")
        replacement = rejected.get("replacement_answer_target_id")
        if replacement is not None and replacement not in answer_target_ids:
            errors.append(f"orphan_replacement_answer_target:{rejected.get('rejected_answer_target_id')}:{replacement}")
        for proposition_id in rejected.get("related_proposition_ids", []):
            if proposition_id not in proposition_ids:
                errors.append(
                    f"orphan_rejected_reference:{rejected.get('rejected_answer_target_id')}:{proposition_id}"
                )
        answer_target = next(
            (item for item in ledger.get("answer_targets", []) if item.get("answer_target_id") == rejected.get("answer_target_id")),
            None,
        )
        if answer_target and answer_target.get("target_status") == "confirmed":
            errors.append(f"rejected_target_marked_confirmed:{rejected.get('rejected_answer_target_id')}")
    for repair in ledger.get("repair_records", []):
        for rejected_target_id in repair.get("rejected_answer_target_ids", []):
            if rejected_target_id not in rejected_target_ids:
                errors.append(f"orphan_repair_reference:{repair.get('repair_id')}:{rejected_target_id}")
        for replacement_target_id in repair.get("replacement_answer_target_ids", []):
            if replacement_target_id not in answer_target_ids:
                errors.append(f"orphan_repair_reference:{repair.get('repair_id')}:{replacement_target_id}")
    unresolved_pairs = {
        (str(item.get("item_type")), str(item.get("item_id")))
        for item in ledger.get("unresolved_items", [])
    }
    resolved_pairs = {
        (str(item.get("item_type")), str(item.get("item_id")))
        for item in ledger.get("resolved_items", [])
    }
    for item_type, item_id in sorted(unresolved_pairs & resolved_pairs):
        errors.append(f"item_both_resolved_and_unresolved:{item_type}:{item_id}")
    item_namespaces = {
        "proposition": ids["propositions"],
        "issue": ids["issues"],
        "commitment": ids["commitments"],
        "obligation": ids["obligations"],
        "answer_target": ids["answer_targets"],
        "repair": ids["repairs"],
        "compound_structure": ids["groups"],
    }
    for item_type, item_id in sorted(unresolved_pairs | resolved_pairs):
        namespace = item_namespaces.get(item_type)
        if namespace is not None and item_id not in namespace:
            errors.append(f"orphan_item_reference:{item_type}:{item_id}")
    terminal_statuses = {
        "proposition": {
            str(item["proposition_id"]): item.get("lifecycle_status") in {"withdrawn", "resolved", "superseded", "abandoned"}
            for item in ledger.get("propositions", [])
        },
        "issue": {
            str(item["issue_id"]): item.get("status") in {"answered", "superseded", "abandoned", "expired", "no_stable_issue"}
            for item in ledger.get("issue_states", [])
        },
        "obligation": {
            str(item["obligation_id"]): item.get("status") in {"satisfied", "waived", "expired", "superseded"}
            for item in ledger.get("conversational_obligations", [])
        },
    }
    for item_type, statuses in terminal_statuses.items():
        for item_id, is_terminal in statuses.items():
            pair = (item_type, item_id)
            if is_terminal and pair not in resolved_pairs:
                errors.append(f"resolved_state_missing_item:{item_type}:{item_id}")
            if not is_terminal and pair in resolved_pairs:
                errors.append(f"resolved_item_state_mismatch:{item_type}:{item_id}")
            if is_terminal and pair in unresolved_pairs:
                errors.append(f"terminal_item_listed_unresolved:{item_type}:{item_id}")
                if item_type == "issue" and next(
                    (item.get("status") for item in ledger.get("issue_states", []) if item.get("issue_id") == item_id),
                    None,
                ) == "answered":
                    errors.append(f"answered_issue_listed_unresolved:{item_id}")
    if "unresolved_reference" in ledger.get("extraction_status", {}).get("abstentions", []):
        for proposition in ledger.get("propositions", []):
            if proposition.get("derivation", {}).get("kind") == "anaphora_resolution":
                errors.append(f"unsupported_pronoun_disambiguation:{proposition.get('proposition_id')}")
    transitions = ledger.get("state_transitions", [])
    if len(transitions) != 1:
        errors.append(f"state_transition_count:{len(transitions)}")
    transition_added_fields = {
        "propositions_added": ids["propositions"],
        "proposition_groups_added": ids["groups"],
        "issue_states_added": ids["issues"],
        "commitments_added": ids["commitments"],
        "obligations_added": ids["obligations"],
        "relations_added": ids["relations"],
        "answer_targets_added": ids["answer_targets"],
        "rejected_answer_targets_added": ids["rejected_targets"],
        "repair_records_added": ids["repairs"],
        "warnings_added": ids["warnings"],
    }
    transition_updated_fields = {
        "propositions_updated": (ids["propositions"], "lifecycle_status"),
        "proposition_groups_updated": (ids["groups"], None),
        "issue_states_updated": (ids["issues"], "status"),
        "commitments_updated": (ids["commitments"], "stance"),
        "obligations_updated": (ids["obligations"], "status"),
        "answer_targets_updated": (ids["answer_targets"], "target_status"),
        "rejected_answer_targets_updated": (ids["rejected_targets"], "status"),
        "repair_records_updated": (ids["repairs"], "outcome"),
    }
    record_by_namespace = {
        "propositions_updated": {str(item["proposition_id"]): item for item in ledger.get("propositions", [])},
        "proposition_groups_updated": {str(item["proposition_group_id"]): item for item in ledger.get("proposition_groups", [])},
        "issue_states_updated": {str(item["issue_id"]): item for item in ledger.get("issue_states", [])},
        "commitments_updated": {str(item["commitment_id"]): item for item in ledger.get("participant_commitments", [])},
        "obligations_updated": {str(item["obligation_id"]): item for item in ledger.get("conversational_obligations", [])},
        "answer_targets_updated": {str(item["answer_target_id"]): item for item in ledger.get("answer_targets", [])},
        "rejected_answer_targets_updated": {str(item["rejected_answer_target_id"]): item for item in ledger.get("rejected_answer_targets", [])},
        "repair_records_updated": {str(item["repair_id"]): item for item in ledger.get("repair_records", [])},
    }
    all_state_ids = set().union(*item_namespaces.values(), ids["warnings"], ids["relations"], ids["rejected_targets"])
    resolved_item_ids = {item_id for _, item_id in resolved_pairs}
    history: list[Mapping[str, Any]] = []
    if _validate_history:
        history_value = transcript.get("ledger_history", [])
        if not isinstance(history_value, list):
            errors.append("ledger_history_not_array")
        else:
            for history_index, snapshot in enumerate(history_value):
                if not isinstance(snapshot, Mapping):
                    errors.append(f"ledger_history_non_object:{history_index}")
                    continue
                history.append(snapshot)
                if snapshot.get("as_of_turn_index") != history_index:
                    errors.append(f"ledger_history_index_mismatch:{history_index}")
            if len(history_value) != as_of:
                errors.append(f"ledger_history_length_mismatch:{len(history_value)}:{as_of}")
            for history_index, snapshot in enumerate(history):
                prior_snapshot = history[history_index - 1] if history_index else None
                history_errors = validate_ledger(
                    snapshot,
                    transcript,
                    schema,
                    _immediate_previous=prior_snapshot,
                    _validate_history=False,
                )
                errors.extend(
                    f"ledger_history:{history_index}:{history_error}"
                    for history_error in history_errors
                )
        previous_ledger = history[-1] if history else None
    else:
        previous_ledger = _immediate_previous
    declared_previous_hash = ledger.get("previous_ledger_sha256")
    if declared_previous_hash is None:
        if previous_ledger is not None:
            errors.append("unexpected_previous_ledger")
        if as_of != 0:
            errors.append(f"nonzero_genesis_boundary:{as_of}")
    elif previous_ledger is None:
        errors.append("missing_previous_ledger")
    else:
        actual_previous_hash = ledger_sha256(previous_ledger)
        if previous_ledger.get("ledger_sha256") != actual_previous_hash:
            errors.append("previous_ledger_self_hash_mismatch")
        if declared_previous_hash != previous_ledger.get("ledger_sha256"):
            errors.append("previous_ledger_identity_mismatch")
        previous_as_of = previous_ledger.get("as_of_turn_index")
        if not isinstance(previous_as_of, int) or previous_as_of != as_of - 1:
            errors.append("previous_ledger_boundary_mismatch")
        for envelope_field in ("schema_version", "conversation_key", "root_post_id"):
            if previous_ledger.get(envelope_field) != ledger.get(envelope_field):
                errors.append(f"previous_ledger_envelope_mismatch:{envelope_field}")
    try:
        current_state_projection = ledger_state_projection(ledger)
    except Phase1Error as exc:
        errors.append(str(exc))
        current_state_projection = None
    try:
        previous_state_projection = (
            ledger_state_projection(previous_ledger)
            if previous_ledger is not None
            else {collection: {} for collection in LEDGER_STATE_COLLECTIONS}
        )
    except Phase1Error as exc:
        errors.append(f"previous_{exc}")
        previous_state_projection = None
    for transition in transitions:
        if transition.get("current_turn_index") != as_of or transition.get("current_turn_id") != ledger.get("target_turn_id"):
            errors.append(f"transition_boundary_mismatch:{transition.get('transition_id')}")
        for field, namespace in transition_added_fields.items():
            for item_id in transition.get(field, []):
                if item_id not in namespace:
                    errors.append(f"orphan_transition_reference:{field}:{item_id}")
        for field, (namespace, status_field) in transition_updated_fields.items():
            update_item_ids: list[str] = []
            for update in transition.get(field, []):
                if not isinstance(update, Mapping):
                    continue
                item_id = str(update.get("item_id") or "")
                update_item_ids.append(item_id)
                if item_id not in namespace:
                    errors.append(f"orphan_transition_reference:{field}:{item_id}")
                    continue
                if status_field is not None and record_by_namespace[field][item_id].get(status_field) != update.get("to_status"):
                    errors.append(f"transition_final_state_mismatch:{field}:{item_id}")
            duplicate_update_ids = {
                item_id
                for item_id in update_item_ids
                if update_item_ids.count(item_id) > 1
            }
            for item_id in sorted(duplicate_update_ids):
                errors.append(f"duplicate_transition_update:{field}:{item_id}")
        for item_id in transition.get("items_resolved", []):
            if item_id not in all_state_ids or item_id not in resolved_item_ids:
                errors.append(f"orphan_transition_reference:items_resolved:{item_id}")
        for update in transition.get("propositions_updated", []):
            source = str(update.get("from_status"))
            target = str(update.get("to_status"))
            if target not in PROPOSITION_LIFECYCLE_TRANSITIONS.get(source, set()):
                errors.append(f"invalid_lifecycle_transition:{update.get('item_id')}:{source}:{target}")
        actual_patch = transition.get("state_patch", [])
        if not isinstance(actual_patch, list):
            actual_patch = []
        expected_patch: list[dict[str, Any]] = []
        if current_state_projection is not None and previous_state_projection is not None:
            expected_patch = build_state_patch(previous_ledger, ledger)
        try:
            patch_matches = canonical_json_bytes(actual_patch) == canonical_json_bytes(expected_patch)
        except Phase1Error:
            patch_matches = False
        if not patch_matches:
            errors.append(f"state_patch_incomplete_or_inexact:{transition.get('transition_id')}")
        try:
            reconstructed, patch_errors = apply_state_patch(previous_ledger, actual_patch)
            errors.extend(patch_errors)
            if current_state_projection is None or reconstructed != current_state_projection:
                errors.append(f"state_reconstruction_mismatch:{transition.get('transition_id')}")
        except Phase1Error as exc:
            errors.append(str(exc))
            errors.append(f"state_reconstruction_mismatch:{transition.get('transition_id')}")
        patch_adds: dict[str, set[str]] = defaultdict(set)
        patch_replaces: dict[str, set[str]] = defaultdict(set)
        for operation in expected_patch:
            if operation["operation"] == "add":
                patch_adds[str(operation["collection"])].add(str(operation["item_id"]))
            elif operation["operation"] == "replace":
                patch_replaces[str(operation["collection"])].add(str(operation["item_id"]))
        for field, collection in TRANSITION_ADDED_COLLECTIONS.items():
            if set(map(str, transition.get(field, []))) != patch_adds.get(collection, set()):
                errors.append(f"transition_delta_mismatch:{field}")
        for field, (collection, status_field) in TRANSITION_UPDATED_COLLECTIONS.items():
            updates = transition.get(field, [])
            update_ids = {
                str(update.get("item_id") or "")
                for update in updates
                if isinstance(update, Mapping)
            }
            if update_ids != patch_replaces.get(collection, set()):
                errors.append(f"transition_delta_mismatch:{field}")
            if previous_ledger is None or status_field is None:
                continue
            if previous_state_projection is None or current_state_projection is None:
                continue
            previous_records = previous_state_projection[collection]
            current_records = current_state_projection[collection]
            for update in updates:
                if not isinstance(update, Mapping):
                    continue
                item_id = str(update.get("item_id") or "")
                before_record = previous_records.get(item_id)
                after_record = current_records.get(item_id)
                if before_record is None or after_record is None:
                    continue
                if (
                    update.get("from_status") != before_record.get(status_field)
                    or update.get("to_status") != after_record.get(status_field)
                ):
                    errors.append(f"transition_status_mismatch:{field}:{item_id}")
        expected_resolved = patch_adds.get("resolved_items", set())
        expected_resolved_ids = {item_id.partition(":")[2] for item_id in expected_resolved}
        if set(map(str, transition.get("items_resolved", []))) != expected_resolved_ids:
            errors.append("transition_delta_mismatch:items_resolved")
    expected_previous = ledger.get("previous_ledger_sha256")
    for transition in ledger.get("state_transitions", []):
        if transition.get("from_ledger_sha256") != expected_previous:
            errors.append(f"previous_hash_mismatch:{transition.get('transition_id')}")
    expected_hash = ledger_sha256(ledger)
    if ledger.get("ledger_sha256") != expected_hash:
        errors.append("ledger_hash_mismatch")
    return sorted(set(errors))


def validate_ledger_incremental(
    ledger: Mapping[str, Any],
    previous_ledger: Mapping[str, Any],
    current_turn: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> list[str]:
    """Validate one appended snapshot from a trusted validated predecessor.

    The persisted predecessor deliberately contains hashes rather than prior
    raw transcript text.  This wrapper therefore preserves every predecessor
    turn reference and evidence span byte-for-byte, validates newly introduced
    evidence only against the exact current turn, and delegates all remaining
    schema, reference, lifecycle, state-patch, predecessor, and ledger-hash
    checks to :func:`validate_ledger`.
    """
    errors: list[str] = []
    previous_turn_refs = list(previous_ledger.get("turn_refs", []))
    candidate_turn_refs = list(ledger.get("turn_refs", []))
    if not isinstance(current_turn.get("text"), str):
        return ["incremental_current_turn_text_missing"]
    previous_as_of = previous_ledger.get("as_of_turn_index")
    if not isinstance(previous_as_of, int):
        return ["incremental_previous_boundary_invalid"]
    expected_index = previous_as_of + 1
    expected_current_ref = {
        "turn_id": current_turn.get("turn_id"),
        "turn_index": expected_index,
        "post_id": current_turn.get("post_id"),
        "parent_turn_id": current_turn.get("parent_turn_id"),
        "speaker_id": current_turn.get("speaker_id"),
        "text_sha256": sha256_bytes(str(current_turn["text"]).encode("utf-8")),
    }
    if candidate_turn_refs != [*previous_turn_refs, expected_current_ref]:
        errors.append("incremental_turn_refs_not_exact_predecessor_plus_current")
    if ledger.get("target_turn_id") != current_turn.get("turn_id"):
        errors.append("incremental_target_turn_binding_mismatch")
    if ledger.get("as_of_turn_index") != expected_index:
        errors.append("incremental_target_index_binding_mismatch")
    current_conversation = current_turn.get("conversation_key")
    if current_conversation is not None and current_conversation != ledger.get("conversation_key"):
        errors.append("incremental_conversation_binding_mismatch")

    previous_span_material = Counter(
        canonical_json_bytes(dict(span))
        for _, span in _all_evidence_spans(previous_ledger)
    )
    candidate_span_material = Counter(
        canonical_json_bytes(dict(span)) for _, span in _all_evidence_spans(ledger)
    )
    for material, count in previous_span_material.items():
        if candidate_span_material[material] < count:
            errors.append("incremental_predecessor_evidence_removed_or_changed")
            break
    prior_turn_ids = {
        str(turn.get("turn_id") or "") for turn in previous_turn_refs
    }
    introduced_spans = candidate_span_material - previous_span_material
    for material, count in introduced_spans.items():
        span = json.loads(material.decode("utf-8"))
        if count and str(span.get("turn_id") or "") in prior_turn_ids:
            errors.append("incremental_new_evidence_references_prior_turn")

    synthetic_turns: list[dict[str, Any]] = []
    for turn_ref in candidate_turn_refs:
        turn_id = str(turn_ref.get("turn_id") or "")
        synthetic_turns.append(
            {
                "turn_id": turn_id,
                "turn_index": turn_ref.get("turn_index"),
                "post_id": turn_ref.get("post_id"),
                "text": current_turn["text"]
                if turn_id == str(current_turn.get("turn_id") or "")
                else "",
            }
        )
    delegated = validate_ledger(
        ledger,
        {
            "conversation_key": ledger.get("conversation_key"),
            "turns": synthetic_turns,
        },
        schema,
        _immediate_previous=previous_ledger,
        _validate_history=False,
    )
    for error in delegated:
        if any(
            error == f"turn_text_hash_mismatch:{turn_id}"
            for turn_id in prior_turn_ids
        ):
            continue
        if error.startswith(("evidence_span_out_of_bounds:", "evidence_span_mismatch:")) and any(
            f":{turn_id}:" in error for turn_id in prior_turn_ids
        ):
            continue
        errors.append(error)
    return sorted(set(errors))


def validate_synthetic_fixtures(project_dir: Path) -> dict[str, Any]:
    """Validate all tracked invented transcripts, ledgers, and negative examples."""
    schema_path = project_dir / "proposition_ledger_research/schema/proposition-ledger-v1.schema.json"
    fixtures_root = project_dir / "proposition_ledger_research/phase1/synthetic-fixtures"
    schema = _read_json(schema_path)
    fixture_dirs = sorted(path for path in fixtures_root.iterdir() if path.is_dir())
    results: list[dict[str, Any]] = []
    invalid_code_counts: Counter[str] = Counter()
    for fixture_dir in fixture_dirs:
        transcript = _read_json(fixture_dir / "transcript.json")
        ledger = _read_json(fixture_dir / "expected-ledger.json")
        invalid_pack = _read_json(fixture_dir / "invalid-ledger-examples.json")
        valid_errors = validate_ledger(ledger, transcript, schema)
        invalid_results: list[dict[str, Any]] = []
        for example in invalid_pack.get("examples", []):
            invalid_errors = validate_ledger(example["ledger"], transcript, schema)
            expected_code = str(example["expected_error_code"])
            matched = any(error == expected_code or error.startswith(expected_code + ":") for error in invalid_errors)
            invalid_code_counts[expected_code] += 1
            invalid_results.append(
                {
                    "invalid_example_id": example["invalid_example_id"],
                    "expected_error_code": expected_code,
                    "matched": matched,
                    "error_count": len(invalid_errors),
                }
            )
        results.append(
            {
                "fixture_id": transcript.get("fixture_id"),
                "valid": not valid_errors,
                "valid_errors": valid_errors,
                "invalid_examples": invalid_results,
            }
        )
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "fixture_count": len(fixture_dirs),
        "valid_fixture_count": sum(result["valid"] for result in results),
        "invalid_example_count": sum(len(result["invalid_examples"]) for result in results),
        "all_invalid_examples_detected": all(
            example["matched"]
            for result in results
            for example in result["invalid_examples"]
        ),
        "invalid_error_code_counts": dict(sorted(invalid_code_counts.items())),
        "results": results,
    }


def _coverage(covered: int | None, total: int | None, status: str, reason: str | None = None) -> dict[str, Any]:
    fraction = None
    if covered is not None and total:
        fraction = round(covered / total, 6)
    return {
        "covered": covered,
        "total": total,
        "fraction": fraction,
        "status": status,
        "reason": reason,
    }


def _text_for_turn(turn: Mapping[str, Any]) -> str | None:
    for field in ("public_text", "text", "incoming_text"):
        value = turn.get(field)
        if isinstance(value, str) and value:
            return value
    return None


def _time_span(values: Iterable[Any]) -> dict[str, Any]:
    available = sorted(str(value) for value in values if value not in (None, ""))
    if not available:
        return {"start": None, "end": None, "status": "unavailable", "reason": "source does not expose a usable time field"}
    return {"start": available[0], "end": available[-1], "status": "measured", "reason": None}


def _declared_or_unknown(entry: Mapping[str, Any], name: str) -> Any:
    declared = entry.get("declared_metrics") or {}
    if name in declared:
        return copy.deepcopy(declared[name])
    return None


def _analyse_structured_source(path: Path, kind: str) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "record_count": None,
        "independent_conversation_count": None,
        "turn_count": None,
        "observed_time_span": {"start": None, "end": None, "status": "unavailable", "reason": "not a conversational row source"},
        "native_x_creation_time_coverage": _coverage(None, None, "unavailable", "not measured for this source kind"),
        "exact_text_coverage": _coverage(None, None, "unavailable", "not measured for this source kind"),
        "parent_id_coverage": _coverage(None, None, "unavailable", "not measured for this source kind"),
        "conversation_root_id_coverage": _coverage(None, None, "unavailable", "not measured for this source kind"),
        "publication_confirmation_coverage": _coverage(None, None, "unavailable", "not measured for this source kind"),
        "pipeline_telemetry_coverage": _coverage(None, None, "unavailable", "not measured for this source kind"),
        "known_annotation_coverage": _coverage(0, 0, "not_applicable", "source is not an annotation set"),
    }
    if path.suffix == ".jsonl":
        rows = _read_jsonl(path)
        metrics["record_count"] = len(rows)
    elif path.suffix == ".json":
        document = _read_json(path)
        rows = document if isinstance(document, list) else []
        metrics["record_count"] = len(rows) if rows else 1
    elif path.suffix == ".csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        metrics["record_count"] = len(rows)
    else:
        metrics["record_count"] = 1
        return metrics

    if kind in {"benchmark_conversations", "audit_conversations", "prospective_conversations", "review_pack_conversations"}:
        conversations = rows
        turns = [turn for row in conversations for turn in row.get("turns", [])]
        if kind == "review_pack_conversations" and not turns:
            turns = [turn for row in conversations for turn in row.get("path_turns", [])]
        metrics["independent_conversation_count"] = len({str(row.get("conversation_key")) for row in conversations})
        metrics["turn_count"] = len(turns)
        metrics["observed_time_span"] = _time_span(
            value
            for row in conversations
            for value in (
                row.get("start_time"),
                row.get("end_time"),
                row.get("last_activity_time"),
            )
        )
        text_count = sum(_text_for_turn(turn) is not None for turn in turns)
        parent_count = sum(
            (turn.get("parent_id") if "parent_id" in turn else turn.get("parent_post_id")) is not None
            for turn in turns
        )
        root_count = sum(row.get("root_post_id") is not None for row in conversations)
        account_turns = [turn for turn in turns if turn.get("author_role") == "account"]
        confirmed_account = sum(turn.get("publication_status") in {"published", "observed"} for turn in account_turns)
        native_time = sum(
            turn.get("timestamp_basis") == "x_created_at"
            or turn.get("creation_time_source") in {"x_created_at", "x_snowflake"}
            or turn.get("creation_time_confidence") == "high"
            for turn in turns
        )
        telemetry = sum(bool(turn.get("pipeline_stage_summaries") or turn.get("tested_pipeline_stage_summaries")) for turn in turns)
        metrics.update(
            {
                "native_x_creation_time_coverage": _coverage(native_time, len(turns), "measured"),
                "exact_text_coverage": _coverage(text_count, len(turns), "measured"),
                "parent_id_coverage": _coverage(parent_count, len(turns), "measured", "root turns legitimately have no parent"),
                "conversation_root_id_coverage": _coverage(root_count, len(conversations), "measured"),
                "publication_confirmation_coverage": _coverage(confirmed_account, len(account_turns), "measured"),
                "pipeline_telemetry_coverage": _coverage(telemetry, len(turns), "measured"),
            }
        )
    elif kind == "prospective_canonical_posts":
        posts = rows
        metrics["turn_count"] = len(posts)
        metrics["observed_time_span"] = _time_span(post.get("created_at") or post.get("first_observed_at") for post in posts)
        account_posts = [post for post in posts if post.get("author_role") == "account"]
        metrics.update(
            {
                "native_x_creation_time_coverage": _coverage(sum(post.get("created_at") is not None for post in posts), len(posts), "measured"),
                "exact_text_coverage": _coverage(sum(_text_for_turn(post) is not None for post in posts), len(posts), "measured"),
                "parent_id_coverage": _coverage(sum(post.get("parent_post_id") is not None for post in posts), len(posts), "measured", "root and quote posts legitimately have no reply parent"),
                "conversation_root_id_coverage": _coverage(sum(post.get("root_post_id") is not None and post.get("conversation_id") is not None for post in posts), len(posts), "measured"),
                "publication_confirmation_coverage": _coverage(sum(post.get("publication_status") == "published" for post in account_posts), len(account_posts), "measured"),
                "pipeline_telemetry_coverage": _coverage(sum(bool(post.get("pipeline_stage_summaries")) for post in posts), len(posts), "measured"),
            }
        )
    elif kind in {"prospective_review_candidates", "review_pack_candidates"}:
        candidates = rows
        path_turns = [turn for row in candidates for turn in row.get("path_turns", [])]
        metrics["independent_conversation_count"] = len({str(row.get("conversation_key")) for row in candidates})
        metrics["turn_count"] = len(path_turns)
        metrics["observed_time_span"] = _time_span(
            value for row in candidates for value in (row.get("start_time"), row.get("last_activity_time"))
        )
        account_turns = [turn for turn in path_turns if turn.get("author_role") == "account"]
        metrics.update(
            {
                "native_x_creation_time_coverage": _coverage(sum(turn.get("created_at") is not None for turn in path_turns), len(path_turns), "measured"),
                "exact_text_coverage": _coverage(sum(_text_for_turn(turn) is not None for turn in path_turns), len(path_turns), "measured"),
                "parent_id_coverage": _coverage(sum(turn.get("parent_post_id") is not None for turn in path_turns), len(path_turns), "measured", "each path has a legitimate root"),
                "conversation_root_id_coverage": _coverage(sum(row.get("root_post_id") is not None for row in candidates), len(candidates), "measured"),
                "publication_confirmation_coverage": _coverage(sum(turn.get("publication_status") == "published" for turn in account_turns), len(account_turns), "measured"),
                "pipeline_telemetry_coverage": _coverage(sum(bool(row.get("pipeline_stage_summaries")) for row in candidates), len(candidates), "measured"),
            }
        )
    elif kind == "historical_reply_targets":
        targets = rows
        metrics["turn_count"] = None
        metrics["observed_time_span"] = _time_span(row.get("created_at") for row in targets)
        context_turns = [turn for row in targets for turn in row.get("thread_context", [])]
        metrics.update(
            {
                "independent_conversation_count": None,
                "native_x_creation_time_coverage": _coverage(sum(row.get("timestamp_source") == "x_created_at" for row in targets), len(targets), "measured"),
                "exact_text_coverage": _coverage(sum(bool(row.get("incoming_text")) for row in targets), len(targets), "measured"),
                "parent_id_coverage": _coverage(sum(row.get("parent_id") is not None for row in targets), len(targets), "measured", "quote rows overload parent_id with quoted-account identity"),
                "conversation_root_id_coverage": _coverage(0, len(targets), "unavailable", "rows are isolated pipeline target reconstructions, not complete conversations"),
                "publication_confirmation_coverage": _coverage(sum(bool(row.get("parent_bot_text")) for row in targets), len(targets), "measured", "confirms the account target, not the later account response"),
                "pipeline_telemetry_coverage": _coverage(sum(bool(row.get("local_metadata") or row.get("trusted_facts")) for row in targets), len(targets), "measured"),
            }
        )
        if context_turns:
            metrics["turn_count"] = len(targets) + len(context_turns)
    elif kind in {"audit_turn_reviews", "audit_user_annotations"}:
        metrics["known_annotation_coverage"] = _coverage(len(rows), len(rows), "measured")
        metrics["independent_conversation_count"] = len({str(row.get("conversation_key")) for row in rows if row.get("conversation_key")})
    elif kind == "qud_cases":
        metrics["turn_count"] = sum(len(row.get("issue_transcript", [])) for row in rows)
        metrics["exact_text_coverage"] = _coverage(metrics["turn_count"], metrics["turn_count"], "measured")
        metrics["parent_id_coverage"] = _coverage(
            sum(turn.get("parent_turn_id") is not None for row in rows for turn in row.get("issue_transcript", [])),
            metrics["turn_count"],
            "measured",
            "one path root per case legitimately has no parent",
        )
    return metrics


def _inventory_sources(manifest: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    inventory: list[dict[str, Any]] = []
    before_hashes: dict[str, str] = {}
    seen_ids: set[str] = set()
    for entry in manifest.get("sources", []):
        source_id = str(entry.get("source_id") or "")
        if not source_id or source_id in seen_ids:
            raise Phase1Error(f"source IDs must be non-empty and unique: {source_id!r}")
        seen_ids.add(source_id)
        path = Path(str(entry.get("path") or ""))
        is_collection = bool(entry.get("source_collection"))
        collection_files = _collection_files(entry) if is_collection and path.is_dir() and not path.is_symlink() else []
        available = (
            path.is_dir() and not path.is_symlink() and bool(collection_files)
            if is_collection
            else path.is_file() and not path.is_symlink()
        )
        status = "inspected" if available else "unavailable"
        reason = None if available else str(entry.get("unavailable_reason") or "path is absent or is not a regular non-symlink file")
        digest = _source_digest(entry) if available else None
        expected = entry.get("expected_sha256")
        if available and expected is not None and digest != expected:
            raise Phase1Error(f"source hash mismatch for {source_id}: expected {expected}, found {digest}")
        if available:
            before_hashes[source_id] = str(digest)
            metrics = (
                {
                    "record_count": len(collection_files),
                    "independent_conversation_count": None,
                    "turn_count": None,
                    "observed_time_span": {"start": None, "end": None, "status": "unavailable", "reason": "collection inventory does not inspect provider payload fields"},
                    "native_x_creation_time_coverage": _coverage(None, None, "not_applicable", "provider response cache"),
                    "exact_text_coverage": _coverage(None, None, "not_inspected", "provider payload text deliberately excluded"),
                    "parent_id_coverage": _coverage(None, None, "not_applicable", "provider response cache"),
                    "conversation_root_id_coverage": _coverage(None, None, "not_applicable", "provider response cache"),
                    "publication_confirmation_coverage": _coverage(None, None, "not_applicable", "provider response cache"),
                    "pipeline_telemetry_coverage": _coverage(len(collection_files), len(collection_files), "measured", "file identity and cache presence only"),
                    "known_annotation_coverage": _coverage(None, None, "not_applicable", "provider response cache"),
                }
                if is_collection
                else _analyse_structured_source(path, str(entry.get("structured_kind") or "generic"))
            )
        else:
            metrics = {
                "record_count": None,
                "independent_conversation_count": None,
                "turn_count": None,
                "observed_time_span": {"start": None, "end": None, "status": "unavailable", "reason": reason},
                "native_x_creation_time_coverage": _coverage(None, None, "unavailable", reason),
                "exact_text_coverage": _coverage(None, None, "unavailable", reason),
                "parent_id_coverage": _coverage(None, None, "unavailable", reason),
                "conversation_root_id_coverage": _coverage(None, None, "unavailable", reason),
                "publication_confirmation_coverage": _coverage(None, None, "unavailable", reason),
                "pipeline_telemetry_coverage": _coverage(None, None, "unavailable", reason),
                "known_annotation_coverage": _coverage(None, None, "unavailable", reason),
            }
        for metric_name in (
            "record_count",
            "independent_conversation_count",
            "turn_count",
            "observed_time_span",
            "native_x_creation_time_coverage",
            "exact_text_coverage",
            "parent_id_coverage",
            "conversation_root_id_coverage",
            "publication_confirmation_coverage",
            "pipeline_telemetry_coverage",
            "known_annotation_coverage",
        ):
            declared = _declared_or_unknown(entry, metric_name)
            if declared is not None:
                metrics[metric_name] = declared
        inventory.append(
            {
                "source_id": source_id,
                "absolute_path": str(path),
                "source_type": entry.get("source_type", "unspecified"),
                "storage_class": entry.get("storage_class", "unspecified"),
                "available": available,
                "status": status,
                "reason": reason,
                "schema": entry.get("schema", "not_declared"),
                "producer_version": entry.get("producer_version"),
                "producer_commit": entry.get("producer_commit"),
                "producer_script_sha256": entry.get("producer_script_sha256"),
                "file_sha256": digest,
                **metrics,
                "author_pseudonym_scheme": entry.get("author_pseudonym_scheme", "not_applicable_or_unavailable"),
                "known_prior_exposure": sorted(set(entry.get("known_prior_exposure", []))),
                "synthetic_or_model_generated_replicate_status": entry.get("replicate_status", "not_a_replicate"),
                "relationship_to_other_sources": entry.get("relationship_to_other_sources", "not_declared"),
                "suitability_for_ledger_construction": entry.get("suitability_for_ledger_construction", "not_assessed"),
                "limitations": entry.get("limitations") or ["no additional limitation declared"],
            }
        )
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "source_count": len(inventory),
        "sources": inventory,
    }, before_hashes


def _inventory_markdown(inventory: Mapping[str, Any]) -> list[str]:
    lines = [
        "# Phase 1 source inventory",
        "",
        "This inventory contains source identities and coverage only; it does not reproduce conversation text.",
        "",
        "| Source | Status | Records | Conversations | Turns | SHA-256 | Suitability |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for source in inventory["sources"]:
        lines.append(
            "| {source_id} | {status} | {record_count} | {independent_conversation_count} | {turn_count} | `{file_sha256}` | {suitability_for_ledger_construction} |".format(
                **{key: ("unknown" if value is None else value) for key, value in source.items()}
            )
        )
    lines.extend(
        [
            "",
            "Coverage objects in `source-inventory.json` distinguish measured, unavailable, and not-applicable values. No empty list is used to imply an inspection failure.",
        ]
    )
    return lines


def _normalised_transcript_hash(turns: Iterable[Mapping[str, Any]]) -> str | None:
    material: list[dict[str, str]] = []
    for turn in turns:
        text = _text_for_turn(turn)
        if text is None:
            return None
        material.append(
            {
                "role": str(turn.get("author_role") or turn.get("speaker_id") or "unknown"),
                "text": " ".join(text.split()),
            }
        )
    return sha256_bytes(canonical_json_bytes(material)) if material else None


def _empty_identity_bundle(source_id: str) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "conversation_ids": set(),
        "root_post_ids": set(),
        "parent_graph_edges": set(),
        "target_or_reply_post_ids": set(),
        "normalised_transcript_hashes": set(),
        "entities": {},
    }


def _identity_bundle(entry: Mapping[str, Any]) -> dict[str, Any]:
    source_id = str(entry["source_id"])
    bundle = _empty_identity_bundle(source_id)
    path = Path(str(entry["path"]))
    if not path.is_file() or path.is_symlink():
        return bundle
    kind = str(entry.get("structured_kind") or "")
    if kind in {"benchmark_conversations", "audit_conversations", "prospective_conversations", "review_pack_conversations"}:
        rows = _read_jsonl(path)
        for row_index, row in enumerate(rows):
            entity_key = str(row.get("conversation_key") or f"row-{row_index}")
            identities = {
                "conversation_ids": set(),
                "root_post_ids": set(),
                "post_ids": set(),
                "transcript_hashes": set(),
            }
            conversation_id = row.get("conversation_id")
            root_id = row.get("root_post_id")
            if conversation_id is not None:
                bundle["conversation_ids"].add(str(conversation_id))
                identities["conversation_ids"].add(str(conversation_id))
            if root_id is not None:
                bundle["root_post_ids"].add(str(root_id))
                identities["root_post_ids"].add(str(root_id))
            turns = row.get("turns") or row.get("path_turns") or []
            transcript_hash = _normalised_transcript_hash(turns)
            if transcript_hash:
                bundle["normalised_transcript_hashes"].add(transcript_hash)
                identities["transcript_hashes"].add(transcript_hash)
            for turn in turns:
                post_id = turn.get("post_id")
                parent_id = turn.get("parent_id") if "parent_id" in turn else turn.get("parent_post_id")
                if post_id is not None:
                    post_id = str(post_id)
                    bundle["target_or_reply_post_ids"].add(post_id)
                    identities["post_ids"].add(post_id)
                if post_id is not None and parent_id is not None:
                    bundle["parent_graph_edges"].add((str(post_id), str(parent_id)))
            bundle["entities"][entity_key] = identities
    elif kind in {"prospective_canonical_posts", "benchmark_canonical_posts"}:
        for row in _read_jsonl(path):
            post_id = row.get("post_id")
            parent_id = row.get("parent_post_id")
            if post_id is not None:
                bundle["target_or_reply_post_ids"].add(str(post_id))
            if post_id is not None and parent_id is not None:
                bundle["parent_graph_edges"].add((str(post_id), str(parent_id)))
            if row.get("conversation_id") is not None:
                bundle["conversation_ids"].add(str(row["conversation_id"]))
            if row.get("root_post_id") is not None:
                bundle["root_post_ids"].add(str(row["root_post_id"]))
            if row.get("conversation_or_root_id") is not None:
                bundle["root_post_ids"].add(str(row["conversation_or_root_id"]))
    elif kind in {"prospective_review_candidates", "review_pack_candidates"}:
        for row_index, row in enumerate(_read_jsonl(path)):
            entity_key = str(
                row.get("conversation_key")
                or row.get("candidate_key")
                or row.get("branch_key")
                or f"row-{row_index}"
            )
            identities = bundle["entities"].setdefault(
                entity_key,
                {"conversation_ids": set(), "root_post_ids": set(), "post_ids": set(), "transcript_hashes": set()},
            )
            root_id = row.get("root_post_id")
            if root_id is not None:
                identities["root_post_ids"].add(str(root_id))
                bundle["root_post_ids"].add(str(root_id))
            turns = row.get("path_turns", [])
            transcript_hash = _normalised_transcript_hash(turns)
            if transcript_hash:
                identities["transcript_hashes"].add(transcript_hash)
                bundle["normalised_transcript_hashes"].add(transcript_hash)
            for turn in turns:
                post_id = turn.get("post_id")
                parent_id = turn.get("parent_post_id")
                if post_id is not None:
                    identities["post_ids"].add(str(post_id))
                    bundle["target_or_reply_post_ids"].add(str(post_id))
                if post_id is not None and parent_id is not None:
                    bundle["parent_graph_edges"].add((str(post_id), str(parent_id)))
    elif kind == "historical_reply_targets":
        for row in _read_jsonl(path):
            target_id = row.get("reply_id")
            parent_id = row.get("parent_id")
            if target_id is not None:
                bundle["target_or_reply_post_ids"].add(str(target_id))
            if target_id is not None and parent_id is not None and row.get("interaction_type") == "replied_to":
                bundle["parent_graph_edges"].add((str(target_id), str(parent_id)))
    elif kind == "qud_cases":
        for row in _read_jsonl(path):
            target_id = row.get("target_turn_id") or row.get("target_identity")
            if target_id is not None:
                bundle["target_or_reply_post_ids"].add(str(target_id).removeprefix("x-post:"))
            for turn in row.get("issue_transcript", []):
                turn_id = turn.get("turn_id")
                parent_id = turn.get("parent_turn_id")
                if turn_id is not None:
                    bundle["target_or_reply_post_ids"].add(str(turn_id))
                if turn_id is not None and parent_id is not None:
                    bundle["parent_graph_edges"].add((str(turn_id), str(parent_id)))
    elif kind == "benchmark_prior_registry":
        document = _read_json(path)
        for row in document.get("conversations", []):
            entity_key = str(row.get("conversation_id") or row.get("root_post_id"))
            identities = {"conversation_ids": set(), "root_post_ids": set(), "post_ids": set(), "transcript_hashes": set()}
            for label, field in (("conversation_ids", "conversation_id"), ("root_post_ids", "root_post_id")):
                value = row.get(field)
                if value is not None:
                    identities[label].add(str(value))
                    bundle[label].add(str(value))
            for post_id in [*row.get("target_post_ids", []), *row.get("shared_observed_post_ids", [])]:
                identities["post_ids"].add(str(post_id))
                bundle["target_or_reply_post_ids"].add(str(post_id))
            bundle["entities"][entity_key] = identities
    elif kind == "writer_judge_manifest":
        document = _read_json(path)
        for case in document.get("cases", []):
            if case.get("case_id") is not None:
                bundle["target_or_reply_post_ids"].add(str(case["case_id"]))
    return bundle


def _touching_entities(bundle: Mapping[str, Any], shared_ids: set[str]) -> list[str]:
    touched: list[str] = []
    for entity_key, identities in bundle.get("entities", {}).items():
        authoritative = set(identities.get("conversation_ids", set())) | set(identities.get("root_post_ids", set())) | set(identities.get("post_ids", set()))
        if authoritative & shared_ids:
            touched.append(str(entity_key))
    return sorted(touched)


def _build_overlap(manifest: Mapping[str, Any]) -> dict[str, Any]:
    entries = [entry for entry in manifest.get("sources", []) if entry.get("overlap_eligible")]
    bundles = {str(entry["source_id"]): _identity_bundle(entry) for entry in entries}
    pairs: list[dict[str, Any]] = []
    source_ids = sorted(bundles)
    for left_index, left_id in enumerate(source_ids):
        for right_id in source_ids[left_index + 1 :]:
            left = bundles[left_id]
            right = bundles[right_id]
            conversation_ids = set(left["conversation_ids"]) & set(right["conversation_ids"])
            root_ids = set(left["root_post_ids"]) & set(right["root_post_ids"])
            graph_edges = set(left["parent_graph_edges"]) & set(right["parent_graph_edges"])
            target_ids = set(left["target_or_reply_post_ids"]) & set(right["target_or_reply_post_ids"])
            transcript_hashes = set(left["normalised_transcript_hashes"]) & set(right["normalised_transcript_hashes"])
            graph_post_ids = {post_id for edge in graph_edges for post_id in edge}
            authoritative_ids = conversation_ids | root_ids | target_ids | graph_post_ids
            if conversation_ids:
                strongest_authority = "exact_conversation_id"
            elif root_ids:
                strongest_authority = "exact_root_post_id"
            elif graph_edges:
                strongest_authority = "exact_parent_linked_post_graph"
            elif target_ids:
                strongest_authority = "exact_target_or_reply_post_id"
            elif transcript_hashes:
                strongest_authority = "normalised_transcript_hash_secondary_only"
            else:
                strongest_authority = None
            pairs.append(
                {
                    "left_source_id": left_id,
                    "right_source_id": right_id,
                    "exact_conversation_id_count": len(conversation_ids),
                    "exact_root_post_id_count": len(root_ids),
                    "exact_parent_graph_edge_count": len(graph_edges),
                    "exact_target_or_reply_post_id_count": len(target_ids),
                    "normalised_transcript_hash_count_secondary_only": len(transcript_hashes),
                    "left_independent_conversations_touched": _touching_entities(left, authoritative_ids),
                    "right_independent_conversations_touched": _touching_entities(right, authoritative_ids),
                    "strongest_overlap_authority": strongest_authority,
                    "matched_conversation_merge_permitted": bool(conversation_ids or root_ids or graph_edges or target_ids),
                    "normalised_text_merge_permitted": False,
                    "matched_conversation_rule": "conversation, root, parent-linked graph, or exact target/reply identity may establish overlap in descending authority; transcript hashes only corroborate",
                }
            )
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "authority_order": [
            "exact_conversation_id",
            "exact_root_post_id",
            "exact_parent_linked_post_graph",
            "exact_target_or_reply_post_id",
            "normalised_transcript_hash_secondary_only",
        ],
        "author_pseudonym_is_never_a_merge_key": True,
        "chronology_or_semantic_similarity_is_never_a_merge_key": True,
        "source_ids": source_ids,
        "pairs": pairs,
    }


def _overlap_markdown(overlap: Mapping[str, Any]) -> list[str]:
    lines = [
        "# Phase 1 source overlap",
        "",
        "Pairs are reconciled by immutable graph identity. Transcript hashes are secondary corroboration and never authorize a merge.",
        "",
        "| Left | Right | Conv IDs | Roots | Graph edges | Target/reply IDs | Transcript hashes |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for pair in overlap["pairs"]:
        if not any(
            pair[field]
            for field in (
                "exact_conversation_id_count",
                "exact_root_post_id_count",
                "exact_parent_graph_edge_count",
                "exact_target_or_reply_post_id_count",
                "normalised_transcript_hash_count_secondary_only",
            )
        ):
            continue
        lines.append(
            f"| {pair['left_source_id']} | {pair['right_source_id']} | {pair['exact_conversation_id_count']} | {pair['exact_root_post_id_count']} | {pair['exact_parent_graph_edge_count']} | {pair['exact_target_or_reply_post_id_count']} | {pair['normalised_transcript_hash_count_secondary_only']} |"
        )
    return lines


def _source_entry(manifest: Mapping[str, Any], source_id: str) -> Mapping[str, Any]:
    matches = [entry for entry in manifest.get("sources", []) if entry.get("source_id") == source_id]
    if len(matches) != 1:
        raise Phase1Error(f"source manifest must contain exactly one {source_id!r} entry")
    return matches[0]


def _source_path(manifest: Mapping[str, Any], source_id: str) -> Path:
    return Path(str(_source_entry(manifest, source_id)["path"]))


def _chronology_complete(turns: Sequence[Mapping[str, Any]]) -> bool:
    timestamps = [turn.get("timestamp") or turn.get("created_at") for turn in turns]
    return all(timestamp not in (None, "") for timestamp in timestamps) and timestamps == sorted(timestamps)


def _account_publication_confirmed(turns: Sequence[Mapping[str, Any]]) -> bool:
    return all(
        turn.get("publication_status") in {"published", "observed"}
        for turn in turns
        if turn.get("author_role") == "account"
    )


def _immutable_turn_identities_complete(turns: Sequence[Mapping[str, Any]]) -> bool:
    post_ids = [str(turn.get("post_id") or "") for turn in turns]
    turn_ids = [str(turn.get("turn_id") or "") for turn in turns]
    return bool(turns) and all(post_ids) and all(turn_ids) and len(post_ids) == len(set(post_ids)) and len(turn_ids) == len(set(turn_ids))


def _roles_complete(turns: Sequence[Mapping[str, Any]]) -> bool:
    return bool(turns) and all(turn.get("author_role") in {"user", "account"} for turn in turns)


def _turn_order_unambiguous(turns: Sequence[Mapping[str, Any]]) -> bool:
    if not turns:
        return False
    chronology_indexes = [turn.get("chronology_index") for turn in turns]
    if all(isinstance(index, int) for index in chronology_indexes):
        return chronology_indexes == sorted(chronology_indexes) and len(set(chronology_indexes)) == len(chronology_indexes)
    timestamps = [turn.get("timestamp") or turn.get("created_at") for turn in turns]
    return all(timestamp not in (None, "") for timestamp in timestamps) and timestamps == sorted(timestamps)


def _parent_post_id(turn: Mapping[str, Any]) -> Any:
    return turn.get("parent_id") if "parent_id" in turn else turn.get("parent_post_id")


def _graph_properties(
    turns: Sequence[Mapping[str, Any]],
    root_post_id: Any,
) -> tuple[bool, bool, bool]:
    if not _immutable_turn_identities_complete(turns):
        return False, False, False
    post_indexes = {str(turn["post_id"]): index for index, turn in enumerate(turns)}
    declared_root = str(root_post_id or "")
    root_identity_complete = bool(declared_root) and declared_root in post_indexes
    segment_root = declared_root if root_identity_complete else str(turns[0]["post_id"])
    graph_unambiguous = post_indexes.get(segment_root) == 0
    for index, turn in enumerate(turns):
        post_id = str(turn["post_id"])
        parent_id = _parent_post_id(turn)
        if post_id == segment_root:
            # The source-defined conversation segment may begin at a post whose
            # public parent predates the bounded segment. That external edge is
            # provenance, not a missing in-segment turn.
            continue
        if parent_id is None or str(parent_id) not in post_indexes or post_indexes[str(parent_id)] >= index:
            graph_unambiguous = False
            break
    complete_prefix = graph_unambiguous
    if complete_prefix:
        for turn in turns:
            current = str(turn["post_id"])
            visited: set[str] = set()
            while current != segment_root:
                if current in visited:
                    complete_prefix = False
                    break
                visited.add(current)
                parent = _parent_post_id(turns[post_indexes[current]])
                if parent is None or str(parent) not in post_indexes:
                    complete_prefix = False
                    break
                current = str(parent)
            if not complete_prefix:
                break
    return root_identity_complete, graph_unambiguous, complete_prefix


def _assign_reconstruction_grade(
    *,
    exact_text_complete: bool,
    immutable_post_identity_complete: bool,
    role_assignment_complete: bool,
    chronology_complete: bool,
    turn_order_unambiguous: bool,
    account_publication_confirmed: bool,
    root_identity_complete: bool,
    parent_graph_unambiguous: bool,
    complete_prefix_through_targets: bool,
    source_complete: bool,
    reconstruction_confidence: str,
    hard_exclusion_reasons: Sequence[str],
) -> tuple[str, list[str]]:
    if hard_exclusion_reasons:
        return "C", []
    primary_invariants = (
        exact_text_complete,
        immutable_post_identity_complete,
        role_assignment_complete,
        chronology_complete,
        turn_order_unambiguous,
        account_publication_confirmed,
        root_identity_complete,
        parent_graph_unambiguous,
        complete_prefix_through_targets,
        source_complete,
        reconstruction_confidence == "high",
    )
    if all(primary_invariants):
        return "A", []
    secondary_required = (
        exact_text_complete,
        immutable_post_identity_complete,
        role_assignment_complete,
        account_publication_confirmed,
        parent_graph_unambiguous,
        complete_prefix_through_targets,
        source_complete,
        reconstruction_confidence in {"high", "medium"},
    )
    limited_gaps: list[str] = []
    if not chronology_complete:
        limited_gaps.append("one_limited_timing_gap")
    if not turn_order_unambiguous:
        limited_gaps.append("one_limited_turn_order_gap")
    if not root_identity_complete:
        limited_gaps.append("one_limited_root_identity_gap")
    if reconstruction_confidence == "medium":
        limited_gaps.append("one_limited_source_identity_or_timing_confidence_gap")
    if all(secondary_required) and len(limited_gaps) == 1:
        return "B", limited_gaps
    return "C", []


def _published_reply_target_turns(turns: Sequence[Mapping[str, Any]]) -> set[str]:
    posts = {str(turn.get("post_id")): turn for turn in turns if turn.get("post_id") is not None}
    target_turn_ids: set[str] = set()
    for turn in turns:
        if turn.get("author_role") != "account" or turn.get("publication_status") not in {"published", "observed"}:
            continue
        parent_id = _parent_post_id(turn)
        parent = posts.get(str(parent_id)) if parent_id is not None else None
        if parent and parent.get("author_role") == "user" and parent.get("turn_id"):
            target_turn_ids.add(str(parent["turn_id"]))
    return target_turn_ids


def _quiescent_unreplied_tip_target(
    candidate: Mapping[str, Any],
    record: Mapping[str, Any],
    conversation_turns: Sequence[Mapping[str, Any]],
) -> str | None:
    path_turns = candidate.get("path_turns") or []
    if (
        record.get("reconstruction_grade") != "A"
        or candidate.get("activity_status") != "quiescent"
        or candidate.get("prospective_status") != "eligible"
        or not path_turns
    ):
        return None
    target = path_turns[-1]
    target_turn_id = str(target.get("turn_id") or "")
    target_post_id = str(target.get("post_id") or "")
    if (
        target.get("author_role") != "user"
        or not target_turn_id
        or not target_post_id
        or str(candidate.get("branch_tip_post_id") or "") != target_post_id
        or str(candidate.get("source_branch_tip_post_id") or "") != target_post_id
    ):
        return None
    if not (
        all(_text_for_turn(turn) is not None for turn in path_turns)
        and _immutable_turn_identities_complete(path_turns)
        and _roles_complete(path_turns)
        and _turn_order_unambiguous(path_turns)
    ):
        return None
    _, graph_unambiguous, complete_prefix = _graph_properties(path_turns, path_turns[0].get("post_id"))
    conversation_turn_ids = {str(turn.get("turn_id") or "") for turn in conversation_turns}
    if not graph_unambiguous or not complete_prefix or target_turn_id not in conversation_turn_ids:
        return None
    if target_turn_id in set(record.get("published_reply_target_turn_ids", [])):
        return None
    return target_turn_id


def _outcome_event_reason(event: Mapping[str, Any]) -> str | None:
    """Return the most specific retained structured outcome reason."""
    for field in (
        "effective_reason",
        "terminal_reason",
        "deterministic_reason",
        "original_local_rejection_reason",
    ):
        value = event.get(field)
        if isinstance(value, str) and value:
            return value
    return None


def _deduplicated_pipeline_events(target: Mapping[str, Any]) -> list[tuple[str, int, Mapping[str, Any]]]:
    """Return target-bound structured events without duplicated tested aliases."""
    events: list[tuple[str, int, Mapping[str, Any]]] = []
    seen: set[bytes] = set()
    for collection in ("pipeline_stage_summaries", "tested_pipeline_stage_summaries"):
        values = target.get(collection) or []
        if not isinstance(values, list):
            continue
        for index, event in enumerate(values):
            if not isinstance(event, Mapping):
                continue
            identity = canonical_json_bytes(dict(event))
            if identity in seen:
                continue
            seen.add(identity)
            events.append((collection, index, event))
    return events


def _classify_target_outcome(
    target: Mapping[str, Any],
    record: Mapping[str, Any],
    conversation_turns: Sequence[Mapping[str, Any]],
    candidate: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Classify one target using only exact target-bound retained evidence."""
    target_turn_id = str(target.get("turn_id") or "")
    target_post_id = str(target.get("post_id") or "")
    source_ids = [str(value) for value in record.get("source_ids", [])]
    published: list[dict[str, Any]] = []
    for turn in conversation_turns:
        if (
            turn.get("author_role") == "account"
            and turn.get("publication_status") in {"published", "observed"}
            and str(_parent_post_id(turn) or "") == target_post_id
        ):
            published.append(
                {
                    "record_ref": f"account-reply-turn:{turn.get('turn_id')}",
                    "reason": "exact_parent_linked_account_reply_with_confirmed_or_observed_publication",
                    "strategy_version": None,
                    "timestamp": turn.get("timestamp")
                    or turn.get("created_at")
                    or turn.get("first_observed_at"),
                }
            )

    pipeline_terminal: list[dict[str, Any]] = []
    local_skip: list[dict[str, Any]] = []
    approved_decisions: list[dict[str, Any]] = []
    for collection, index, event in _deduplicated_pipeline_events(target):
        reason = _outcome_event_reason(event)
        ref = str(event.get("event_id") or f"{target_turn_id}:{collection}:{index}")
        retained = {
            "record_ref": ref,
            "reason": reason,
            "strategy_version": event.get("strategy_version"),
            "timestamp": event.get("observed_at"),
        }
        no_reply_status = (
            event.get("status") == "no_reply"
            or event.get("effective_status") == "no_reply"
            or event.get("pipeline_stage_status") == "no_reply"
            or event.get("final_reply_kind") == "no_reply"
        )
        local_status = any(
            event.get(field)
            in {"candidate_terminal", "local_rejection", "local_skip"}
            for field in ("status", "effective_status", "pipeline_stage_status")
        )
        local_reason = isinstance(reason, str) and reason.startswith("writer_local_rejection:")
        is_local_skip = (
            event.get("event_kind") == "reply_strategy_local_rejection"
            or local_status
            or (
                no_reply_status
                and (event.get("deterministic_suppressed") is True or local_reason)
            )
        )
        is_pipeline_terminal = no_reply_status and (
            (
                event.get("event_kind") == "ai_reply_pipeline_decision"
                and event.get("reviewer_verdict")
                in {"confirm_no_reply", "pipeline_no_reply"}
            )
            or (
                event.get("event_kind") == "ai_reply_pipeline_stage_summary"
                and event.get("status") == "no_reply"
                and reason is not None
            )
        )
        if is_local_skip:
            # A successful repair may retain an original rejection reason, so
            # that field alone cannot prove a terminal local skip.  Once an
            # exact local terminal record is established above, however, it is
            # the source-faithful skip reason to preserve.
            local_skip.append(
                {
                    **retained,
                    "reason": event.get("original_local_rejection_reason")
                    or reason,
                }
            )
        elif is_pipeline_terminal:
            pipeline_terminal.append(retained)
        if (
            event.get("event_kind") == "ai_reply_pipeline_decision"
            and event.get("status") == "approved"
            and not is_local_skip
        ):
            approved_decisions.append(retained)

    def result(
        outcome_class: str,
        status: str,
        evidence: Sequence[Mapping[str, Any]],
        *,
        default_reason: str | None,
        conflict_details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        selected = next((item for item in reversed(evidence) if item.get("reason")), None)
        if selected is None and evidence:
            selected = evidence[-1]
        return {
            "outcome_evidence_class": outcome_class,
            "outcome_evidence_status": status,
            "outcome_evidence_source_ids": sorted(set(source_ids)),
            "outcome_evidence_record_refs": sorted(
                {str(item["record_ref"]) for item in evidence if item.get("record_ref")}
            ),
            "outcome_reason": selected.get("reason") if selected and selected.get("reason") else default_reason,
            "outcome_strategy_version": selected.get("strategy_version") if selected else None,
            "outcome_timestamp": selected.get("timestamp") if selected else None,
            "outcome_conflict_details": copy.deepcopy(conflict_details),
        }

    no_reply_evidence = [*local_skip, *pipeline_terminal]
    if published and no_reply_evidence:
        evidence = [*published, *no_reply_evidence]
        return result(
            "conflicting_outcome_evidence",
            "conflicting_authoritative_evidence",
            evidence,
            default_reason=None,
            conflict_details={
                "published_reply_record_refs": sorted(item["record_ref"] for item in published),
                "no_reply_record_refs": sorted(item["record_ref"] for item in no_reply_evidence),
            },
        )
    if local_skip and pipeline_terminal:
        evidence = [*local_skip, *pipeline_terminal]
        return result(
            "conflicting_outcome_evidence",
            "conflicting_structured_no_reply_evidence",
            evidence,
            default_reason=None,
            conflict_details={
                "local_skip_record_refs": sorted(
                    item["record_ref"] for item in local_skip
                ),
                "pipeline_terminal_no_reply_record_refs": sorted(
                    item["record_ref"] for item in pipeline_terminal
                ),
            },
        )
    if no_reply_evidence and approved_decisions:
        evidence = [*no_reply_evidence, *approved_decisions]
        return result(
            "conflicting_outcome_evidence",
            "conflicting_structured_pipeline_evidence",
            evidence,
            default_reason=None,
            conflict_details={
                "approved_decision_record_refs": sorted(item["record_ref"] for item in approved_decisions),
                "no_reply_record_refs": sorted(item["record_ref"] for item in no_reply_evidence),
            },
        )
    if published:
        return result(
            "confirmed_published_reply",
            "confirmed",
            published,
            default_reason="exact_parent_linked_account_reply_with_confirmed_or_observed_publication",
        )
    if local_skip:
        return result(
            "confirmed_local_skip",
            "confirmed",
            local_skip,
            default_reason="exact_target_bound_structured_local_skip",
        )
    if pipeline_terminal:
        return result(
            "confirmed_pipeline_terminal_no_reply",
            "confirmed",
            pipeline_terminal,
            default_reason="exact_target_bound_final_pipeline_no_reply",
        )
    unknown_target = (
        _quiescent_unreplied_tip_target(candidate, record, conversation_turns)
        if candidate is not None
        else None
    )
    if unknown_target == target_turn_id and not approved_decisions:
        return result(
            "quiescent_unreplied_tip_outcome_unknown",
            "observed_without_decision_evidence",
            [],
            default_reason="quiescent_unreplied_user_tip_without_structured_outcome_evidence",
        )
    unavailable_reason = (
        "pipeline_approval_without_confirmed_publication_outcome"
        if approved_decisions
        else None
    )
    return result(
        "outcome_evidence_unavailable",
        "retained_sources_do_not_establish_usable_outcome",
        approved_decisions,
        default_reason=unavailable_reason,
    )


def _conversation_record_from_benchmark(
    row: Mapping[str, Any],
    prospective_canonical_posts: Mapping[str, Mapping[str, Any]],
    benchmark_canonical_posts: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    turns = [dict(turn) for turn in row.get("turns", [])]
    for turn in turns:
        post_id = str(turn.get("post_id"))
        canonical = benchmark_canonical_posts.get(post_id)
        if not canonical:
            continue
        turn_role = turn.get("author_role")
        canonical_role = canonical.get("author_role")
        if (
            turn_role not in (None, "")
            and canonical_role not in (None, "")
            and turn_role != canonical_role
        ):
            raise Phase1Error(
                "benchmark turn and exact canonical post have conflicting roles"
            )
        if turn_role != "user":
            continue
        turn_author = turn.get("author_key")
        turn_author = (
            turn_author
            if isinstance(turn_author, str) and turn_author.strip()
            else None
        )
        canonical_author = canonical.get("author_key")
        canonical_author = (
            canonical_author
            if isinstance(canonical_author, str) and canonical_author.strip()
            else None
        )
        if (
            turn_author is not None
            and canonical_author is not None
            and turn_author != canonical_author
        ):
            raise Phase1Error(
                "benchmark turn and exact canonical post have conflicting "
                "pseudonymous authors"
            )
        if canonical_author is not None:
            turn["author_key"] = canonical_author
    exact_text_complete = bool(turns) and all(_text_for_turn(turn) is not None for turn in turns)
    chronology_complete = _chronology_complete(turns)
    turn_order_unambiguous = _turn_order_unambiguous(turns)
    publication_confirmed = _account_publication_confirmed(turns)
    immutable_identities = _immutable_turn_identities_complete(turns)
    roles_complete = _roles_complete(turns)
    reconciliation: list[dict[str, Any]] = []
    material_unresolved = False
    for turn in turns:
        post_id = str(turn.get("post_id"))
        canonical = prospective_canonical_posts.get(post_id)
        if not canonical:
            continue
        benchmark_parent = turn.get("parent_id")
        canonical_parent = canonical.get("parent_post_id")
        if benchmark_parent == canonical_parent:
            continue
        if canonical_parent is None and str(canonical.get("quoted_post_id")) == str(benchmark_parent):
            reconciliation.append(
                {
                    "post_id": post_id,
                    "kind": "quote_not_reply_parent",
                    "benchmark_parent_id": str(benchmark_parent),
                    "authoritative_parent_id": None,
                    "authoritative_quoted_post_id": str(canonical.get("quoted_post_id")),
                    "resolution": "prospective_v4_quote_graph_authority",
                }
            )
        else:
            material_unresolved = True
            reconciliation.append(
                {
                    "post_id": post_id,
                    "kind": "unresolved_parent_conflict",
                    "benchmark_parent_id": benchmark_parent,
                    "authoritative_parent_id": canonical_parent,
                    "authoritative_quoted_post_id": canonical.get("quoted_post_id"),
                    "resolution": "unresolved",
                }
            )
    root_identity_complete, graph_unambiguous, complete_prefix = _graph_properties(turns, row.get("root_post_id"))
    parent_graph_complete = row.get("completeness") == "complete" and graph_unambiguous and not material_unresolved
    exclusion_reasons: list[str] = []
    if row.get("completeness") != "complete":
        exclusion_reasons.append("materially_missing_preceding_context")
    if material_unresolved:
        exclusion_reasons.append("unresolved_material_graph_conflict")
    if not exact_text_complete:
        exclusion_reasons.append("missing_substantive_text")
    if not chronology_complete:
        exclusion_reasons.append("chronology_incomplete")
    if not publication_confirmed:
        exclusion_reasons.append("account_publication_unconfirmed")
    if not immutable_identities:
        exclusion_reasons.append("missing_or_duplicate_immutable_post_identity")
    if not roles_complete:
        exclusion_reasons.append("ambiguous_user_or_account_role")
    if not graph_unambiguous:
        exclusion_reasons.append("ambiguous_or_incomplete_parent_graph")
    if not complete_prefix:
        exclusion_reasons.append("incomplete_target_prefix")
    grade, secondary_limitations = _assign_reconstruction_grade(
        exact_text_complete=exact_text_complete,
        immutable_post_identity_complete=immutable_identities,
        role_assignment_complete=roles_complete,
        chronology_complete=chronology_complete,
        turn_order_unambiguous=turn_order_unambiguous,
        account_publication_confirmed=publication_confirmed,
        root_identity_complete=root_identity_complete,
        parent_graph_unambiguous=graph_unambiguous and not material_unresolved,
        complete_prefix_through_targets=complete_prefix,
        source_complete=row.get("completeness") == "complete",
        reconstruction_confidence=str(row.get("reconstruction_confidence") or ""),
        hard_exclusion_reasons=exclusion_reasons,
    )
    record = {
        "conversation_key": row["conversation_key"],
        "root_post_id": row.get("root_post_id"),
        "conversation_id": None,
        "source_ids": ["benchmark_conversations"],
        "start_time": row.get("start_time"),
        "end_time": row.get("end_time"),
        "turn_count": len(turns),
        "user_turn_count": sum(turn.get("author_role") == "user" for turn in turns),
        "account_turn_count": sum(turn.get("author_role") == "account" for turn in turns),
        "originating_lane": row.get("lane_sequence", []),
        "reconstruction_grade": grade,
        "exact_text_complete": exact_text_complete,
        "parent_graph_complete": parent_graph_complete,
        "account_publication_confirmed": publication_confirmed,
        "chronology_complete": chronology_complete,
        "immutable_post_identity_complete": immutable_identities,
        "role_assignment_complete": roles_complete,
        "root_identity_complete": root_identity_complete,
        "turn_order_unambiguous": turn_order_unambiguous,
        "complete_prefix_through_targets": complete_prefix,
        "principal_author_key": row.get("author_key"),
        "author_key_scheme": "benchmark_v1_truncated_domain_separated_sha256_not_cross_family_comparable",
        "prior_exposure_status": "pending_registry",
        "prior_exposure_categories": [],
        "prior_exposure_reasons": [],
        "known_label_status": "no_authoritative_gold_proposition_annotations",
        "prospective_boundary_status": "pre_boundary",
        "possible_experimental_roles": [],
        "exclusion_reasons": exclusion_reasons,
        "secondary_quality_limitations": secondary_limitations,
        "source_provenance": row.get("source_provenance", []),
        "graph_reconciliation": reconciliation,
        "published_reply_target_turn_ids": sorted(_published_reply_target_turns(turns)),
        "confirmed_pipeline_terminal_no_reply_target_turn_ids": [],
        "confirmed_local_skip_target_turn_ids": [],
        "quiescent_unreplied_tip_outcome_unknown_target_turn_ids": [],
        "outcome_evidence_unavailable_target_turn_ids": [],
        "conflicting_outcome_evidence_target_turn_ids": [],
    }
    return record, turns


def _conversation_record_from_prospective(row: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    turns = [dict(turn) for turn in row.get("turns", [])]
    exact_text_complete = bool(turns) and all(_text_for_turn(turn) is not None for turn in turns)
    chronology_complete = _chronology_complete(turns)
    turn_order_unambiguous = _turn_order_unambiguous(turns)
    publication_confirmed = _account_publication_confirmed(turns)
    immutable_identities = _immutable_turn_identities_complete(turns)
    roles_complete = _roles_complete(turns)
    root_identity_complete, graph_unambiguous, complete_prefix = _graph_properties(turns, row.get("root_post_id"))
    parent_graph_complete = row.get("completeness") == "complete" and graph_unambiguous
    exclusion_reasons: list[str] = []
    if not exact_text_complete:
        exclusion_reasons.append("missing_substantive_text")
    if row.get("completeness") != "complete":
        exclusion_reasons.append("materially_incomplete_parent_or_root_context")
    if row.get("reconstruction_confidence") not in {"high", "medium"}:
        exclusion_reasons.append("low_reconstruction_confidence")
    if row.get("prospective_status") == "start_unknown":
        exclusion_reasons.append("conversation_start_or_root_unknown")
    if not chronology_complete:
        exclusion_reasons.append("chronology_incomplete")
    if not publication_confirmed:
        exclusion_reasons.append("account_publication_unconfirmed")
    if not immutable_identities:
        exclusion_reasons.append("missing_or_duplicate_immutable_post_identity")
    if not roles_complete:
        exclusion_reasons.append("ambiguous_user_or_account_role")
    if not graph_unambiguous:
        exclusion_reasons.append("ambiguous_or_incomplete_parent_graph")
    if not complete_prefix:
        exclusion_reasons.append("incomplete_target_prefix")
    grade, secondary_limitations = _assign_reconstruction_grade(
        exact_text_complete=exact_text_complete,
        immutable_post_identity_complete=immutable_identities,
        role_assignment_complete=roles_complete,
        chronology_complete=chronology_complete,
        turn_order_unambiguous=turn_order_unambiguous,
        account_publication_confirmed=publication_confirmed,
        root_identity_complete=root_identity_complete,
        parent_graph_unambiguous=graph_unambiguous,
        complete_prefix_through_targets=complete_prefix,
        source_complete=row.get("completeness") == "complete",
        reconstruction_confidence=str(row.get("reconstruction_confidence") or ""),
        hard_exclusion_reasons=exclusion_reasons,
    )
    record = {
        "conversation_key": row["conversation_key"],
        "root_post_id": row.get("root_post_id"),
        "conversation_id": row.get("conversation_id"),
        "source_ids": ["prospective_conversations"],
        "start_time": row.get("start_time"),
        "end_time": row.get("last_activity_time"),
        "turn_count": len(turns),
        "user_turn_count": sum(turn.get("author_role") == "user" for turn in turns),
        "account_turn_count": sum(turn.get("author_role") == "account" for turn in turns),
        "originating_lane": row.get("lane_sequence", []),
        "reconstruction_grade": grade,
        "exact_text_complete": exact_text_complete,
        "parent_graph_complete": parent_graph_complete,
        "account_publication_confirmed": publication_confirmed,
        "chronology_complete": chronology_complete,
        "immutable_post_identity_complete": immutable_identities,
        "role_assignment_complete": roles_complete,
        "root_identity_complete": root_identity_complete,
        "turn_order_unambiguous": turn_order_unambiguous,
        "complete_prefix_through_targets": complete_prefix,
        "principal_author_key": row.get("author_key"),
        "author_key_scheme": "prospective_v4_hmac_sha256_not_cross_family_comparable",
        "prior_exposure_status": "pending_registry",
        "prior_exposure_categories": [],
        "prior_exposure_reasons": [],
        "known_label_status": "no_authoritative_gold_proposition_annotations",
        "prospective_boundary_status": row.get("prospective_status"),
        "possible_experimental_roles": [],
        "exclusion_reasons": exclusion_reasons,
        "secondary_quality_limitations": secondary_limitations,
        "source_provenance": row.get("source_provenance", []),
        "graph_reconciliation": [],
        "activity_status": row.get("activity_status"),
        "published_reply_target_turn_ids": sorted(_published_reply_target_turns(turns)),
        "confirmed_pipeline_terminal_no_reply_target_turn_ids": [],
        "confirmed_local_skip_target_turn_ids": [],
        "quiescent_unreplied_tip_outcome_unknown_target_turn_ids": [],
        "outcome_evidence_unavailable_target_turn_ids": [],
        "conflicting_outcome_evidence_target_turn_ids": [],
    }
    return record, turns


def _load_exposure_evidence(manifest: Mapping[str, Any]) -> dict[str, Any]:
    audit_conversations = {
        str(row["conversation_key"])
        for row in _read_jsonl(_source_path(manifest, "audit_conversations"))
    }
    benchmark_registry = _read_json(_source_path(manifest, "benchmark_prior_registry"))
    benchmark_registry_conversations = {
        str(row.get("conversation_id")): row
        for row in benchmark_registry.get("conversations", [])
        if row.get("conversation_id")
    }
    benchmark_registry_roots = {
        str(row.get("root_post_id")): row
        for row in benchmark_registry.get("conversations", [])
        if row.get("root_post_id")
    }
    review_sample_conversations = {
        str(row["conversation_key"])
        for row in _read_jsonl(_source_path(manifest, "benchmark_review_sample"))
    }
    review_pack_conversations = {
        str(row["conversation_key"])
        for row in _read_jsonl(_source_path(manifest, "qud_review_pack_conversations"))
    }
    qud_cases = _read_jsonl(_source_path(manifest, "qud_cases"))
    qud_targets = {str(row["target_turn_id"]) for row in qud_cases}
    paid_targets = set(str(value) for value in manifest.get("qud_paid_target_ids", []))
    replay_cases = _read_jsonl(_source_path(manifest, "multiturn_replay_cases"))
    structured_replay_cases = _read_jsonl(_source_path(manifest, "structured_focus_replay_cases"))
    replay_identity = {(str(row["case_id"]), str(row["conversation_key"])) for row in replay_cases}
    structured_identity = {(str(row["case_id"]), str(row["conversation_key"])) for row in structured_replay_cases}
    if replay_identity != structured_identity:
        raise Phase1Error("writer replay and structured-focus replay case identities differ")
    compact_document = _read_json(_source_path(manifest, "compact_oracle_selected_cases"))
    compact_cases = compact_document.get("cases", [])
    critic_rows = _read_jsonl(_source_path(manifest, "automatic_critic_packets"))
    critic_case_counts = Counter(str(row["case_id"]) for row in critic_rows)
    writer_manifest = _read_json(_source_path(manifest, "writer_judge_manifest"))
    writer_targets = {str(case["case_id"]) for case in writer_manifest.get("cases", [])}
    writer_judgements = _read_jsonl(_source_path(manifest, "writer_judge_judgements"))
    if writer_targets != {str(row["case_id"]) for row in writer_judgements}:
        raise Phase1Error("writer-judge manifest and judgement identities differ")
    review_pack_candidates = _read_jsonl(_source_path(manifest, "qud_review_pack_candidates"))
    recurrence_cases = _read_jsonl(_source_path(manifest, "recurrence_cases"))
    recurrence_repairs = _read_jsonl(_source_path(manifest, "recurrence_repair_records"))
    if {str(row["case_id"]) for row in recurrence_cases} != {str(row["case_id"]) for row in recurrence_repairs}:
        raise Phase1Error("recurrence case and repair-record identities differ")
    assertions = list(manifest.get("exposure_assertions", []))
    report_text = "\n".join(
        _source_path(manifest, source_id).read_text(encoding="utf-8")
        for source_id in ("benchmark_report", "audit_report", "qud_diagnostic_review", "qud_comparison_markdown")
    )
    return {
        "audit_conversations": audit_conversations,
        "benchmark_registry": benchmark_registry,
        "benchmark_registry_conversations": benchmark_registry_conversations,
        "benchmark_registry_roots": benchmark_registry_roots,
        "benchmark_review_sample_conversations": review_sample_conversations,
        "review_pack_conversations": review_pack_conversations,
        "qud_targets": qud_targets,
        "qud_cases": qud_cases,
        "qud_paid_targets": paid_targets,
        "replay_cases": replay_cases,
        "compact_cases": compact_cases,
        "critic_case_counts": critic_case_counts,
        "writer_judge_targets": writer_targets,
        "writer_judge_cases": writer_manifest.get("cases", []),
        "review_pack_candidates": review_pack_candidates,
        "recurrence_cases": recurrence_cases,
        "report_text": report_text,
        "report_conversation_keys": set(re.findall(r"conversation-[0-9a-f]+", report_text)),
        "calibration_conversations": {
            str(selection["conversation_key"])
            for selection in manifest.get("calibration_selection", [])
            if selection.get("conversation_key")
        },
        "assertions": assertions,
    }


def _post_to_conversation(
    records: Sequence[Mapping[str, Any]],
    turns_by_conversation: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for record in records:
        conversation_key = str(record["conversation_key"])
        for turn in turns_by_conversation[conversation_key]:
            for identity_field in ("post_id", "turn_id"):
                if turn.get(identity_field) is None:
                    continue
                turn_identity = str(turn[identity_field])
                existing = mapping.get(turn_identity)
                if existing and existing != conversation_key:
                    raise Phase1Error(f"turn identity occurs in multiple feasibility conversations: {turn_identity}")
                mapping[turn_identity] = conversation_key
    return mapping


def _row_with_hash(row: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(row))
    result.pop("row_sha256", None)
    result["row_sha256"] = sha256_bytes(canonical_json_bytes(result))
    return result


def _record_with_hash(row: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(row))
    result.pop("record_sha256", None)
    result["record_sha256"] = sha256_bytes(canonical_json_bytes(result))
    return result


def _target_candidate_map(
    manifest: Mapping[str, Any],
    known_conversation_keys: set[str],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    """Load exact frozen candidates and retain all principal observations.

    Candidate principal metadata is a consistency check only.  Records which
    differ solely in that field are retained as one deterministic structural
    candidate with a complete multiset of principal observations; other
    differences for the same exact target remain an ambiguity error.
    """
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    candidate_principals: dict[tuple[str, str], list[str]] = defaultdict(list)
    candidate_missing_principals: Counter[tuple[str, str]] = Counter()
    candidate_record_counts: Counter[tuple[str, str]] = Counter()
    for index, candidate in enumerate(
        _read_jsonl(_source_path(manifest, "prospective_review_candidates"))
    ):
        conversation_key = str(candidate.get("conversation_key") or "")
        path_turns = candidate.get("path_turns") or []
        if not conversation_key or conversation_key not in known_conversation_keys:
            raise Phase1Error(
                f"prospective target candidate has no canonical conversation: row {index}"
            )
        if not isinstance(path_turns, list) or not path_turns:
            raise Phase1Error(
                f"prospective target candidate has no target path: {conversation_key}:row-{index}"
            )
        target = path_turns[-1]
        target_turn_id = str(target.get("turn_id") or "")
        target_post_id = str(target.get("post_id") or "")
        if not target_turn_id or not target_post_id:
            raise Phase1Error(
                "prospective review candidate tip lacks an exact identity: "
                f"{conversation_key}:row-{index}"
            )
        for branch_tip_field in ("branch_tip_post_id", "source_branch_tip_post_id"):
            if str(candidate.get(branch_tip_field) or "") != target_post_id:
                raise Phase1Error(
                    "prospective review candidate tip identity is inconsistent: "
                    f"{conversation_key}:row-{index}:{branch_tip_field}"
                )
        if target.get("author_role") == "account":
            # Review candidates also retain completed account-tip branches.  They
            # are validated source rows, but they are not user target prefixes.
            if target.get("publication_status") not in {"published", "observed"}:
                raise Phase1Error(
                    "prospective account-tip review candidate lacks confirmed "
                    f"publication: {conversation_key}:row-{index}"
                )
            continue
        if target.get("author_role") != "user":
            raise Phase1Error(
                "prospective target candidate tip has no exact user role: "
                f"{conversation_key}:row-{index}"
            )
        key = (conversation_key, target_turn_id)
        structural_candidate = copy.deepcopy(dict(candidate))
        principal = structural_candidate.pop("principal_author_key", None)
        if key in candidates and canonical_json_bytes(candidates[key]) != (
            canonical_json_bytes(structural_candidate)
        ):
            raise Phase1Error(
                f"ambiguous prospective target candidate: {conversation_key}:{target_turn_id}"
            )
        candidates[key] = structural_candidate
        candidate_record_counts[key] += 1
        if isinstance(principal, str) and principal.strip():
            candidate_principals[key].append(principal)
        else:
            candidate_missing_principals[key] += 1

    result: dict[tuple[str, str], Mapping[str, Any]] = {}
    for key in sorted(candidates):
        candidate = copy.deepcopy(candidates[key])
        candidate["_principal_author_key_observations"] = sorted(
            candidate_principals[key]
        )
        candidate["_missing_principal_author_key_count"] = int(
            candidate_missing_principals[key]
        )
        candidate["_candidate_record_count"] = int(candidate_record_counts[key])
        result[key] = candidate
    return result


def _target_author_identity(
    target: Mapping[str, Any],
    candidate: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Bind one target only to its exact canonical user-turn pseudonym."""

    exact_value = target.get("author_key")
    exact_author_key = (
        exact_value
        if isinstance(exact_value, str) and exact_value.strip()
        else None
    )
    candidate_principals = [
        str(value)
        for value in (candidate or {}).get(
            "_principal_author_key_observations", []
        )
        if isinstance(value, str) and value.strip()
    ]
    candidate_count = int((candidate or {}).get("_candidate_record_count") or 0)
    missing_count = int(
        (candidate or {}).get("_missing_principal_author_key_count") or 0
    )
    agreement_count = sum(
        exact_author_key is not None and value == exact_author_key
        for value in candidate_principals
    )
    contradiction_count = sum(
        exact_author_key is not None and value != exact_author_key
        for value in candidate_principals
    )

    if exact_author_key is None:
        status = "unavailable"
        reasons = ["target_author_identity_unavailable"]
    elif contradiction_count:
        status = "conflicting"
        reasons = ["target_author_identity_conflicting"]
    else:
        status = "available"
        reasons = []

    if exact_author_key is None:
        candidate_status = "target_author_identity_unavailable"
    elif candidate_count == 0:
        candidate_status = "not_present"
    elif contradiction_count:
        candidate_status = "conflicting"
    elif missing_count and agreement_count:
        candidate_status = "incomplete_agreement"
    elif missing_count:
        candidate_status = "principal_unavailable"
    else:
        candidate_status = "agreement"

    return {
        "principal_author_key": exact_author_key,
        "target_author_identity_status": status,
        "target_author_identity_reasons": reasons,
        "review_candidate_author_consistency_status": candidate_status,
        "review_candidate_record_count": candidate_count,
        "review_candidate_principal_agreement_count": agreement_count,
        "review_candidate_principal_conflict_count": contradiction_count,
        "review_candidate_principal_missing_count": missing_count,
    }


def _source_target_pairs(
    records: Sequence[Mapping[str, Any]],
    turns_by_conversation: Mapping[str, Sequence[Mapping[str, Any]]],
    candidates: Mapping[tuple[str, str], Mapping[str, Any]],
) -> set[tuple[str, str]]:
    """Derive the frozen target universe independently from source evidence."""
    pairs = set(candidates)
    for record in records:
        conversation_key = str(record["conversation_key"])
        pairs.update(
            (conversation_key, target_turn_id)
            for target_turn_id in _published_reply_target_turns(
                turns_by_conversation[conversation_key]
            )
        )
    return pairs


def _declared_target_pairs(
    records: Sequence[Mapping[str, Any]],
) -> set[tuple[str, str]]:
    """Return the target universe frozen on the conversation audit rows."""
    pairs: set[tuple[str, str]] = set()
    for record in records:
        conversation_key = str(record.get("conversation_key") or "")
        expected_ids = record.get("expected_target_turn_ids")
        if not conversation_key or not isinstance(expected_ids, list):
            raise Phase1Error(
                f"conversation lacks an expected target universe: {conversation_key!r}"
            )
        for value in expected_ids:
            target_turn_id = str(value or "")
            if not target_turn_id:
                raise Phase1Error(
                    f"conversation has an empty expected target identity: {conversation_key}"
                )
            pairs.add((conversation_key, target_turn_id))
    return pairs


def _target_partition_errors(
    expected_pairs: set[tuple[str, str]],
    rows: Sequence[Mapping[str, Any]],
    structural_exclusions: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Validate the usable/excluded partition of all potential source targets."""
    usable_pairs = [
        (str(row.get("conversation_key") or ""), str(row.get("target_turn_id") or ""))
        for row in rows
    ]
    excluded_pairs = [
        (str(row.get("conversation_key") or ""), str(row.get("target_turn_id") or ""))
        for row in structural_exclusions
    ]
    usable_set = set(usable_pairs)
    excluded_set = set(excluded_pairs)
    actual_set = usable_set | excluded_set
    errors: list[str] = []
    if len(usable_pairs) != len(usable_set):
        errors.append("target_partition_duplicate_usable_pair")
    if len(excluded_pairs) != len(excluded_set):
        errors.append("target_partition_duplicate_excluded_pair")
    if usable_set & excluded_set:
        errors.append("target_partition_usable_excluded_overlap")
    if expected_pairs - actual_set:
        errors.append("target_partition_missing_expected_pair")
    if actual_set - expected_pairs:
        errors.append("target_partition_unexpected_pair")
    return errors


def _target_universe_errors(
    expected_pairs: set[tuple[str, str]],
    rows: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Backward-compatible validation for an all-usable target universe."""
    return _target_partition_errors(expected_pairs, rows, [])


def _target_structural_reconciliation_summary(
    expected_pairs: set[tuple[str, str]],
    rows: Sequence[Mapping[str, Any]],
    structural_exclusions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Summarise the explicit partition without folding exclusions into targets."""
    partition_errors = _target_partition_errors(
        expected_pairs, rows, structural_exclusions
    )
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "potential_source_target_count": len(expected_pairs),
        "structurally_usable_target_prefix_count": len(rows),
        "structurally_excluded_target_count": len(structural_exclusions),
        "structural_exclusion_reason_counts": dict(
            sorted(
                Counter(
                    str(reason)
                    for row in structural_exclusions
                    for reason in row.get("structural_exclusion_reasons", [])
                ).items()
            )
        ),
        "partition_complete": not partition_errors,
        "partition_errors": partition_errors,
        "structural_exclusions_outside_target_outcome_and_crosstab_counts": True,
    }


def _target_structural_reconciliation_errors(
    expected_pairs: set[tuple[str, str]],
    rows: Sequence[Mapping[str, Any]],
    structural_exclusions: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
) -> list[str]:
    """Validate exclusion vocabulary, partition, and the derived summary."""
    errors = _target_partition_errors(expected_pairs, rows, structural_exclusions)
    permitted_reasons = set(TARGET_STRUCTURAL_EXCLUSION_REASONS)
    exclusion_keys = [
        str(row.get("structural_exclusion_key") or "")
        for row in structural_exclusions
    ]
    if not all(exclusion_keys) or len(exclusion_keys) != len(set(exclusion_keys)):
        errors.append("target_structural_exclusion_keys_not_unique")
    for row in structural_exclusions:
        reasons = row.get("structural_exclusion_reasons")
        if (
            not isinstance(reasons, list)
            or not reasons
            or len(reasons) != len(set(reasons))
            or not set(reasons) <= permitted_reasons
        ):
            errors.append("target_structural_exclusion_reason_invalid")
            break
        if (
            row.get("structural_exclusion_status")
            != "excluded_from_structurally_usable_target_universe"
            or "outcome_evidence_class" in row
        ):
            errors.append("target_structural_exclusion_contract_invalid")
            break
    expected_summary = _target_structural_reconciliation_summary(
        expected_pairs, rows, structural_exclusions
    )
    if canonical_json_bytes(expected_summary) != canonical_json_bytes(summary):
        errors.append("target_structural_reconciliation_summary_mismatch")
    return errors


def _build_feasibility_and_exposure(
    manifest: Mapping[str, Any],
) -> tuple[
    list[dict[str, Any]],
    dict[str, Any],
    list[dict[str, Any]],
    dict[str, Any],
    dict[str, list[dict[str, Any]]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    benchmark_rows = _read_jsonl(_source_path(manifest, "benchmark_conversations"))
    prospective_rows = _read_jsonl(_source_path(manifest, "prospective_conversations"))
    prospective_posts = {
        str(row["post_id"]): row
        for row in _read_jsonl(_source_path(manifest, "prospective_canonical_posts"))
    }
    benchmark_posts = {
        str(row["post_id"]): row
        for row in _read_jsonl(_source_path(manifest, "benchmark_canonical_posts"))
    }
    records: list[dict[str, Any]] = []
    turns_by_conversation: dict[str, list[dict[str, Any]]] = {}
    for row in benchmark_rows:
        record, turns = _conversation_record_from_benchmark(
            row, prospective_posts, benchmark_posts
        )
        records.append(record)
        turns_by_conversation[str(record["conversation_key"])] = turns
    for row in prospective_rows:
        record, turns = _conversation_record_from_prospective(row)
        records.append(record)
        turns_by_conversation[str(record["conversation_key"])] = turns
    conversation_keys = [str(record["conversation_key"]) for record in records]
    if len(conversation_keys) != len(set(conversation_keys)):
        raise Phase1Error("canonical benchmark/prospective union has duplicate conversation keys")
    records_by_key = {str(record["conversation_key"]): record for record in records}
    candidate_map = _target_candidate_map(manifest, set(records_by_key))
    for record in records:
        conversation_key = str(record["conversation_key"])
        turns = turns_by_conversation[conversation_key]
        target_ids = set(record.get("published_reply_target_turn_ids", []))
        target_ids.update(
            target_turn_id
            for candidate_conversation, target_turn_id in candidate_map
            if candidate_conversation == conversation_key
        )
        record["expected_target_turn_ids"] = sorted(target_ids)
    evidence = _load_exposure_evidence(manifest)
    post_to_conversation = _post_to_conversation(records, turns_by_conversation)
    target_statuses: dict[str, set[str]] = defaultdict(set)
    target_reasons: dict[str, set[str]] = defaultdict(set)
    for target in evidence["qud_targets"]:
        target_statuses[target].add("prior_model_experiment")
        target_reasons[target].add("prior QUD candidate pool")
    for target in evidence["qud_paid_targets"]:
        target_statuses[target].update({"development_labelled", "prior_human_review", "prior_model_experiment"})
        target_reasons[target].add("QUD paid case and later completed-human-review negative control")
    for target in evidence["writer_judge_targets"]:
        target_statuses[target].update({"development_labelled", "prior_human_review"})
        target_reasons[target].add("writer-judge audit sampled and judged case")
    for case in evidence["replay_cases"]:
        target = str(case["case_id"])
        target_statuses[target].update({"development_labelled", "prior_human_review", "prior_model_experiment"})
        target_reasons[target].add("multi-turn writer replay and structured-focus replay target with audit labels")
    for case in evidence["compact_cases"]:
        target = str(case["case_id"])
        target_statuses[target].update({"development_unlabelled_but_seen", "prior_model_experiment"})
        target_reasons[target].add("compact-oracle-focus selected replay case")
    for target, replicate_count in evidence["critic_case_counts"].items():
        target_statuses[target].update({"development_unlabelled_but_seen", "prior_model_experiment"})
        target_reasons[target].add(f"automatic alignment critic case with {replicate_count} model replicate packet(s)")
    for case in evidence["recurrence_cases"]:
        target = str(case.get("repair_candidate_turn_id") or "")
        if not target:
            continue
        target_statuses[target].update({"development_unlabelled_but_seen", "prior_model_experiment"})
        target_reasons[target].add("rejected-answer recurrence and repair evaluation target")
        if case.get("carried_forward_human_label") is not None:
            target_statuses[target].update({"development_labelled", "prior_human_review"})
            target_reasons[target].add("recurrence case carries a completed human-review label")
    for target in list(target_statuses):
        if target in evidence["report_text"]:
            target_statuses[target].add("report_excerpt")
            target_reasons[target].add("exact target identity appears in a retained research report or manual comparison")
    for assertion in evidence["assertions"]:
        target = str(assertion.get("post_id") or assertion.get("target_turn_id") or "")
        if not target:
            continue
        statuses = set(assertion.get("statuses", []))
        if not statuses <= ALL_EXPOSURE_STATUSES:
            raise Phase1Error(f"unknown exposure status in assertion for {target}")
        target_statuses[target].update(statuses)
        target_reasons[target].update(str(reason) for reason in assertion.get("reasons", []))
    conversation_target_statuses: dict[str, set[str]] = defaultdict(set)
    conversation_target_reasons: dict[str, set[str]] = defaultdict(set)
    for target, statuses in target_statuses.items():
        conversation_key = post_to_conversation.get(target)
        if conversation_key:
            conversation_target_statuses[conversation_key].update(statuses)
            conversation_target_reasons[conversation_key].update(target_reasons[target])

    exposure_rows: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda item: (item.get("start_time") or "", item["conversation_key"])):
        conversation_key = str(record["conversation_key"])
        conversation_statuses: set[str] = set()
        conversation_reasons: set[str] = set()
        is_benchmark = "benchmark_conversations" in record["source_ids"]
        if conversation_key in evidence["audit_conversations"]:
            conversation_statuses.update({"development_labelled", "prior_model_experiment", "prior_human_review"})
            conversation_reasons.update(
                {
                    "multi-turn audit contains conversation and per-reply development labels",
                    "writer/structured-focus replay family contains the conversation",
                }
            )
            record["known_label_status"] = "non_gold_development_conversation_and_reply_labels"
        if is_benchmark and (
            conversation_key in evidence["benchmark_registry_conversations"]
            or str(record.get("root_post_id")) in evidence["benchmark_registry_roots"]
        ):
            conversation_statuses.add("prior_model_experiment")
            conversation_reasons.add("frozen benchmark prior-experiment identity registry")
        if conversation_key in evidence["benchmark_review_sample_conversations"]:
            conversation_statuses.add("structurally_mined_only")
            conversation_reasons.add("benchmark next-stage unlabelled review sample")
        if conversation_key in evidence["review_pack_conversations"]:
            conversation_statuses.add("structurally_mined_only")
            conversation_reasons.add("QUD frozen prospective review pack")
        if conversation_key in evidence["report_conversation_keys"]:
            conversation_statuses.add("report_excerpt")
            conversation_reasons.add("exact conversation identity appears in a retained research report")
        if conversation_key in evidence["calibration_conversations"]:
            conversation_statuses.add("calibration")
            conversation_reasons.add("selected for the private non-blind Phase 1 calibration pack")
        if not conversation_statuses:
            if record["reconstruction_grade"] == "A":
                conversation_statuses.add("unexposed_candidate")
                conversation_reasons.add("no reliable prior model, human, report, incident, or review-pack identity found")
            else:
                conversation_statuses.add("structurally_mined_only")
                conversation_reasons.add("structurally reconstructed but ineligible for primary experiment")

        record["conversation_wide_exposure_status"] = _normalise_exposure_status(
            conversation_statuses
        )
        record["conversation_wide_exposure_categories"] = sorted(
            conversation_statuses
        )
        record["conversation_wide_exposure_reasons"] = sorted(
            conversation_reasons
        )
        statuses = conversation_statuses | set(
            conversation_target_statuses.get(conversation_key, set())
        )
        reasons = conversation_reasons | set(
            conversation_target_reasons.get(conversation_key, set())
        )
        directly_exposed = bool(statuses & EXPOSED_STATUSES)
        if directly_exposed:
            record["prior_exposure_status"] = "exposed"
        elif "structurally_mined_only" in statuses:
            record["prior_exposure_status"] = "structurally_mined_only"
        else:
            record["prior_exposure_status"] = "unexposed_candidate"
        record["prior_exposure_categories"] = sorted(statuses)
        record["prior_exposure_reasons"] = sorted(reasons)
        if record["reconstruction_grade"] == "A" and record["prior_exposure_status"] == "exposed":
            record["possible_experimental_roles"] = ["development", "calibration", "arm_d_adjudication_candidate"]
        elif record["reconstruction_grade"] == "A" and record["prior_exposure_status"] == "structurally_mined_only":
            record["possible_experimental_roles"] = ["development_or_future_split_candidate_subject_to_leakage_adjudication"]
        elif record["reconstruction_grade"] == "A":
            record["possible_experimental_roles"] = ["future_split_candidate_not_selected_or_opened_in_phase1"]
        else:
            record["possible_experimental_roles"] = ["exclusion_and_sensitivity_accounting_only"]
        exposure_rows.append(
            _row_with_hash(
                {
                    "exposure_key": f"conversation:{conversation_key}",
                    "entity_type": "conversation",
                    "conversation_key": conversation_key,
                    "root_post_id": record.get("root_post_id"),
                    "conversation_id": record.get("conversation_id"),
                    "branch_key": None,
                    "target_post_id": None,
                    "case_id": None,
                    "exposure_statuses": sorted(conversation_statuses),
                    "exposure_reasons": sorted(conversation_reasons),
                    "known_label_status": record["known_label_status"],
                    "reconstruction_grade": record["reconstruction_grade"],
                }
            )
        )
    # Branch, target, and case rows retain the lower-level distinctions even
    # though Phase 2 must split and block by whole conversation.
    records_by_key = {str(record["conversation_key"]): record for record in records}
    branch_entities: dict[str, dict[str, Any]] = {}
    case_entities: dict[str, dict[str, Any]] = {}

    def add_branch(
        branch_key: str,
        *,
        conversation_key: str | None,
        root_post_id: Any,
        target_id: str | None,
        case_id: str | None,
        statuses: Iterable[str],
        reasons: Iterable[str],
    ) -> None:
        if not branch_key:
            return
        entity = branch_entities.setdefault(
            branch_key,
            {
                "conversation_keys": set(),
                "root_post_ids": set(),
                "target_ids": set(),
                "case_ids": set(),
                "statuses": set(),
                "reasons": set(),
            },
        )
        if conversation_key:
            entity["conversation_keys"].add(conversation_key)
        if root_post_id not in (None, ""):
            entity["root_post_ids"].add(str(root_post_id))
        if target_id:
            entity["target_ids"].add(target_id)
        if case_id:
            entity["case_ids"].add(case_id)
        entity["statuses"].update(statuses)
        entity["reasons"].update(reasons)

    def add_case(
        case_id: str,
        *,
        conversation_key: str | None,
        root_post_id: Any,
        branch_key: str | None,
        target_id: str | None,
        statuses: Iterable[str],
        reasons: Iterable[str],
        model_replicate_count: int = 0,
    ) -> None:
        if not case_id:
            return
        entity = case_entities.setdefault(
            case_id,
            {
                "conversation_keys": set(),
                "root_post_ids": set(),
                "branch_keys": set(),
                "target_ids": set(),
                "statuses": set(),
                "reasons": set(),
                "model_replicate_count": 0,
            },
        )
        if conversation_key:
            entity["conversation_keys"].add(conversation_key)
        if root_post_id not in (None, ""):
            entity["root_post_ids"].add(str(root_post_id))
        if branch_key:
            entity["branch_keys"].add(branch_key)
        if target_id:
            entity["target_ids"].add(target_id)
        entity["statuses"].update(statuses)
        entity["reasons"].update(reasons)
        entity["model_replicate_count"] = max(entity["model_replicate_count"], model_replicate_count)

    review_branch_to_conversation: dict[str, str] = {}
    for candidate in evidence["review_pack_candidates"]:
        branch_key = str(candidate.get("branch_key") or "")
        conversation_key = str(candidate.get("conversation_key") or "") or None
        if branch_key and conversation_key:
            review_branch_to_conversation[branch_key] = conversation_key
        path_turns = candidate.get("path_turns") or []
        target_id = str(path_turns[-1].get("post_id") or "") if path_turns else None
        add_branch(
            branch_key,
            conversation_key=conversation_key,
            root_post_id=candidate.get("root_post_id"),
            target_id=target_id,
            case_id=None,
            statuses={"structurally_mined_only"},
            reasons={"prospective QUD review-pack branch was structurally mined"},
        )
    for case in evidence["qud_cases"]:
        branch_key = str(case.get("branch_key") or "")
        target = str(case.get("target_turn_id") or "")
        case_id = str(case.get("case_id") or "")
        conversation_key = post_to_conversation.get(target) or review_branch_to_conversation.get(branch_key)
        statuses = {"prior_model_experiment"}
        reasons = {"prior QUD candidate and shadow-evaluation branch"}
        if target in evidence["qud_paid_targets"]:
            statuses.update({"development_labelled", "prior_human_review"})
            reasons.add("QUD paid case later received completed human-review labels")
        add_branch(
            branch_key,
            conversation_key=conversation_key,
            root_post_id=records_by_key.get(conversation_key or "", {}).get("root_post_id"),
            target_id=target,
            case_id=case_id,
            statuses=statuses,
            reasons=reasons,
        )
        add_case(
            case_id,
            conversation_key=conversation_key,
            root_post_id=records_by_key.get(conversation_key or "", {}).get("root_post_id"),
            branch_key=branch_key,
            target_id=target,
            statuses=statuses,
            reasons=reasons,
        )
    for case in evidence["recurrence_cases"]:
        branch_key = str(case.get("branch_identity") or "")
        target = str(case.get("repair_candidate_turn_id") or "")
        conversation_identity = str(case.get("conversation_identity") or "")
        conversation_key = post_to_conversation.get(target) or post_to_conversation.get(conversation_identity) or review_branch_to_conversation.get(branch_key)
        statuses = {"development_unlabelled_but_seen", "prior_model_experiment"}
        reasons = {"rejected-answer recurrence and repair evaluation branch"}
        if case.get("carried_forward_human_label") is not None:
            statuses.update({"development_labelled", "prior_human_review"})
            reasons.add("recurrence branch carries completed human-review labels")
        add_branch(
            branch_key,
            conversation_key=conversation_key,
            root_post_id=records_by_key.get(conversation_key or "", {}).get("root_post_id"),
            target_id=target,
            case_id=str(case.get("case_id") or ""),
            statuses=statuses,
            reasons=reasons,
        )
        add_case(
            str(case.get("case_id") or ""),
            conversation_key=conversation_key,
            root_post_id=records_by_key.get(conversation_key or "", {}).get("root_post_id"),
            branch_key=branch_key,
            target_id=target,
            statuses=statuses,
            reasons=reasons,
        )
    for assertion in evidence["assertions"]:
        target = str(assertion.get("post_id") or assertion.get("target_turn_id") or "")
        conversation_key = post_to_conversation.get(target)
        add_branch(
            str(assertion.get("branch_key") or ""),
            conversation_key=conversation_key,
            root_post_id=records_by_key.get(conversation_key or "", {}).get("root_post_id"),
            target_id=target or None,
            case_id=None,
            statuses=assertion.get("statuses", []),
            reasons=assertion.get("reasons", []),
        )

    prior_registry = evidence["benchmark_registry"]
    for conversation in prior_registry.get("conversations", []):
        conversation_key = str(conversation.get("conversation_id") or "") or None
        for case in conversation.get("cases", []):
            add_case(
                str(case.get("case_id") or case.get("target_post_id") or ""),
                conversation_key=conversation_key,
                root_post_id=conversation.get("root_post_id"),
                branch_key=None,
                target_id=str(case.get("target_post_id") or "") or None,
                statuses={"development_labelled", "prior_human_review", "prior_model_experiment"},
                reasons={"benchmark prior-experiment registry and replay/audit family"},
            )
    for case in evidence["replay_cases"]:
        add_case(
            str(case["case_id"]),
            conversation_key=str(case["conversation_key"]),
            root_post_id=records_by_key.get(str(case["conversation_key"]), {}).get("root_post_id"),
            branch_key=None,
            target_id=str(case["case_id"]),
            statuses={"development_labelled", "prior_human_review", "prior_model_experiment"},
            reasons={"multi-turn and structured-focus replay case with audit labels"},
        )
    for case in evidence["compact_cases"]:
        add_case(
            str(case["case_id"]),
            conversation_key=str(case.get("conversation_key") or "") or None,
            root_post_id=records_by_key.get(str(case.get("conversation_key") or ""), {}).get("root_post_id"),
            branch_key=None,
            target_id=str(case["case_id"]),
            statuses={"development_unlabelled_but_seen", "prior_model_experiment"},
            reasons={"compact-oracle-focus selected replay case"},
            model_replicate_count=int(case.get("replicate_count") or 0),
        )
    for case_id, replicate_count in evidence["critic_case_counts"].items():
        add_case(
            case_id,
            conversation_key=post_to_conversation.get(case_id),
            root_post_id=records_by_key.get(post_to_conversation.get(case_id, ""), {}).get("root_post_id"),
            branch_key=None,
            target_id=case_id,
            statuses={"development_unlabelled_but_seen", "prior_model_experiment"},
            reasons={"automatic alignment critic evaluation case"},
            model_replicate_count=replicate_count,
        )
    for case in evidence["writer_judge_cases"]:
        case_id = str(case["case_id"])
        conversation_key = post_to_conversation.get(case_id)
        add_case(
            case_id,
            conversation_key=conversation_key,
            root_post_id=records_by_key.get(conversation_key or "", {}).get("root_post_id"),
            branch_key=None,
            target_id=case_id,
            statuses={"development_labelled", "prior_human_review"},
            reasons={"writer-judge audit sampled and judged case"},
        )
    for target in sorted(target_statuses):
        exposure_rows.append(
            _row_with_hash(
                {
                    "exposure_key": f"target:{target}",
                    "entity_type": "target",
                    "conversation_key": post_to_conversation.get(target),
                    "root_post_id": next((record.get("root_post_id") for record in records if record["conversation_key"] == post_to_conversation.get(target)), None),
                    "conversation_id": next((record.get("conversation_id") for record in records if record["conversation_key"] == post_to_conversation.get(target)), None),
                    "branch_key": next((assertion.get("branch_key") for assertion in evidence["assertions"] if str(assertion.get("post_id") or assertion.get("target_turn_id")) == target), None),
                    "target_post_id": target,
                    "case_id": None,
                    "exposure_statuses": sorted(target_statuses[target]),
                    "exposure_reasons": sorted(target_reasons[target]),
                    "known_label_status": "non_gold_development_label" if "development_labelled" in target_statuses[target] else "seen_without_phase1_gold_label",
                    "reconstruction_grade": next((record["reconstruction_grade"] for record in records if record["conversation_key"] == post_to_conversation.get(target)), None),
                }
            )
        )
    for branch_key, entity in sorted(branch_entities.items()):
        conversation_keys_for_branch = sorted(entity["conversation_keys"])
        conversation_key = conversation_keys_for_branch[0] if len(conversation_keys_for_branch) == 1 else None
        statuses = set(entity["statuses"])
        reasons = set(entity["reasons"])
        if not statuses <= ALL_EXPOSURE_STATUSES or not reasons:
            raise Phase1Error(f"invalid branch exposure evidence: {branch_key}")
        exposure_rows.append(
            _row_with_hash(
                {
                    "exposure_key": f"branch:{branch_key}",
                    "entity_type": "branch",
                    "conversation_key": conversation_key,
                    "conversation_keys": conversation_keys_for_branch,
                    "root_post_id": sorted(entity["root_post_ids"])[0] if len(entity["root_post_ids"]) == 1 else None,
                    "conversation_id": records_by_key.get(conversation_key or "", {}).get("conversation_id"),
                    "branch_key": branch_key,
                    "target_post_id": sorted(entity["target_ids"])[0] if len(entity["target_ids"]) == 1 else None,
                    "target_identities": sorted(entity["target_ids"]),
                    "case_id": sorted(entity["case_ids"])[0] if len(entity["case_ids"]) == 1 else None,
                    "case_ids": sorted(entity["case_ids"]),
                    "exposure_statuses": sorted(statuses),
                    "exposure_reasons": sorted(reasons),
                    "known_label_status": "non_gold_development_label" if "development_labelled" in statuses else "seen_without_phase1_gold_label",
                    "reconstruction_grade": records_by_key.get(conversation_key or "", {}).get("reconstruction_grade"),
                    "counted_as_independent_conversation": False,
                }
            )
        )
    for case_id, entity in sorted(case_entities.items()):
        conversation_keys_for_case = sorted(entity["conversation_keys"])
        conversation_key = conversation_keys_for_case[0] if len(conversation_keys_for_case) == 1 else None
        statuses = set(entity["statuses"])
        reasons = set(entity["reasons"])
        if not statuses <= ALL_EXPOSURE_STATUSES or not reasons:
            raise Phase1Error(f"invalid case exposure evidence: {case_id}")
        exposure_rows.append(
            _row_with_hash(
                {
                    "exposure_key": f"case:{case_id}",
                    "entity_type": "case",
                    "conversation_key": conversation_key,
                    "conversation_keys": conversation_keys_for_case,
                    "root_post_id": sorted(entity["root_post_ids"])[0] if len(entity["root_post_ids"]) == 1 else None,
                    "conversation_id": records_by_key.get(conversation_key or "", {}).get("conversation_id"),
                    "branch_key": sorted(entity["branch_keys"])[0] if len(entity["branch_keys"]) == 1 else None,
                    "branch_keys": sorted(entity["branch_keys"]),
                    "target_post_id": sorted(entity["target_ids"])[0] if len(entity["target_ids"]) == 1 else None,
                    "target_identities": sorted(entity["target_ids"]),
                    "case_id": case_id,
                    "exposure_statuses": sorted(statuses),
                    "exposure_reasons": sorted(reasons),
                    "known_label_status": "non_gold_development_label" if "development_labelled" in statuses else "seen_without_phase1_gold_label",
                    "reconstruction_grade": records_by_key.get(conversation_key or "", {}).get("reconstruction_grade"),
                    "model_generated_replicate_count": entity["model_replicate_count"],
                    "counted_as_independent_conversation": False,
                }
            )
        )
    target_rows, target_structural_exclusions = _build_target_prefix_rows(
        manifest,
        records,
        exposure_rows,
        turns_by_conversation,
    )
    contributor_exposure_observations, contributor_binding_audit = (
        _build_contributor_exposure_observations(
            records,
            turns_by_conversation,
            exposure_rows,
        )
    )
    author_groups = _import_research_tool("proposition_ledger_author_groups")

    unhashed_target_rows: list[dict[str, Any]] = []
    for target_row in target_rows:
        unhashed = copy.deepcopy(dict(target_row))
        unhashed.pop("row_sha256", None)
        unhashed_target_rows.append(unhashed)
    author_group_analysis = author_groups.apply_author_group_exposure(
        unhashed_target_rows,
        records,
        contributor_exposure_observations,
    )
    if author_group_analysis.get("author_group_split_performed") is not False:
        raise Phase1Error("Phase 1.2 must not assign an author-group split")
    author_group_errors = author_groups.author_group_crosstab_errors(
        author_group_analysis["target_rows"],
        author_group_analysis["conversation_rows"],
        author_group_analysis["within_family_author_groups"],
        author_group_analysis["cross_family_author_groups"],
        author_group_analysis["crosstab"],
        author_group_analysis["contributor_exposure_observations"],
    )
    if author_group_errors:
        raise Phase1Error(
            "author-group exposure reconciliation failed: "
            + ",".join(author_group_errors)
        )
    author_binding_audit = _author_binding_audit(
        author_groups,
        unhashed_target_rows,
        records,
        author_group_analysis,
        contributor_binding_audit,
    )
    target_rows = []
    for annotated_target in author_group_analysis["target_rows"]:
        target_row = copy.deepcopy(dict(annotated_target))
        target_row["preliminary_held_out_eligibility"] = target_row[
            "preliminary_within_family_held_out_eligibility"
        ]
        target_row["preliminary_held_out_exclusion_reasons"] = list(
            target_row["preliminary_within_family_held_out_exclusion_reasons"]
        )
        target_rows.append(_row_with_hash(target_row))
    records = [dict(row) for row in author_group_analysis["conversation_rows"]]
    records_by_key = {str(row["conversation_key"]): row for row in records}
    for observation in author_group_analysis[
        "contributor_exposure_observations"
    ]:
        conversation_key = str(observation["conversation_key"])
        exposure_rows.append(
            _row_with_hash(
                {
                    "exposure_key": (
                        "conversation-contributor:"
                        + str(
                            observation[
                                "contributor_exposure_observation_key"
                            ]
                        )
                    ),
                    "entity_type": "conversation_contributor",
                    "conversation_key": conversation_key,
                    "root_post_id": records_by_key.get(
                        conversation_key, {}
                    ).get("root_post_id"),
                    "conversation_id": records_by_key.get(
                        conversation_key, {}
                    ).get("conversation_id"),
                    "branch_key": None,
                    "target_post_id": None,
                    "case_id": None,
                    "exposure_statuses": list(
                        observation.get("prior_exposure_categories", [])
                    ),
                    "exposure_reasons": list(
                        observation.get("prior_exposure_reasons", [])
                    ),
                    "known_label_status": (
                        "pseudonymous_contributor_exposure_observation"
                    ),
                    "reconstruction_grade": records_by_key.get(
                        conversation_key, {}
                    ).get("reconstruction_grade"),
                    "counted_as_independent_conversation": False,
                    **copy.deepcopy(dict(observation)),
                }
            )
        )
    for group in author_group_analysis["within_family_author_groups"]:
        group_key = str(group["within_family_author_group_key"])
        exposure_rows.append(
            _row_with_hash(
                {
                    "exposure_key": f"within-family-author-group:{group_key}",
                    "entity_type": "within_family_author_group",
                    "conversation_key": None,
                    "root_post_id": None,
                    "conversation_id": None,
                    "branch_key": None,
                    "target_post_id": None,
                    "case_id": None,
                    "exposure_statuses": list(
                        group["author_group_exposure_categories"]
                    ),
                    "exposure_reasons": list(
                        group["author_group_exposure_reasons"]
                    ),
                    "known_label_status": "groupwise_exposure_aggregate",
                    "reconstruction_grade": None,
                    **copy.deepcopy(dict(group)),
                }
            )
        )
    target_crosstab = _build_target_prefix_crosstab(target_rows)
    classified_by_conversation: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: {outcome: [] for outcome in OUTCOME_EVIDENCE_CLASSES}
    )
    for row in target_rows:
        classified_by_conversation[str(row["conversation_key"])][
            str(row["outcome_evidence_class"])
        ].append(str(row["target_turn_id"]))
    for record in records:
        classified = classified_by_conversation[str(record["conversation_key"])]
        for outcome, field in OUTCOME_TARGET_ID_FIELDS.items():
            record[field] = sorted(classified[outcome])
    expected_target_pairs = _declared_target_pairs(records)
    target_structural_reconciliation = _target_structural_reconciliation_summary(
        expected_target_pairs,
        target_rows,
        target_structural_exclusions,
    )
    if target_structural_reconciliation["partition_complete"] is not True:
        raise Phase1Error("potential source target partition is incomplete")
    records = [_row_with_hash(record) for record in sorted(records, key=lambda item: (item.get("start_time") or "", item["conversation_key"]))]
    grade_counts = Counter(record["reconstruction_grade"] for record in records)
    outcome_counts = target_crosstab["dimensions"]["outcome_evidence_class"]
    target_counts = {
        "total_usable_target_prefixes": len(target_rows),
        **{
            f"{outcome}_target_prefixes": outcome_counts.get(outcome, 0)
            for outcome in OUTCOME_EVIDENCE_CLASSES
        },
    }
    direct_exposed = [record for record in records if record["prior_exposure_status"] == "exposed"]
    structural_only = [record for record in records if record["prior_exposure_status"] == "structurally_mined_only"]
    unexposed = [record for record in records if record["prior_exposure_status"] == "unexposed_candidate"]
    feasibility_summary = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "canonical_union_conversation_count": len(records),
        "grade_counts": {grade: grade_counts.get(grade, 0) for grade in ("A", "B", "C")},
        "target_prefix_counts": target_counts,
        "target_structural_reconciliation": target_structural_reconciliation,
        "directly_exposed_conversation_count": len(direct_exposed),
        "structurally_mined_only_conversation_count": len(structural_only),
        "potential_unexposed_candidate_count": len(unexposed),
        "potential_unexposed_by_grade": {grade: sum(record["reconstruction_grade"] == grade for record in unexposed) for grade in ("A", "B", "C")},
        "author_grouping": {
            "feasible_within_benchmark_family": True,
            "feasible_within_prospective_v4_family": True,
            "cross_family_comparable": False,
            "cross_family_author_identity_status": "unavailable",
            "author_group_split_performed": False,
            "eventual_split_policy": (
                "whole conversation and whole author group in every available identity domain"
            ),
            "limitation": (
                "held-out claims are conversation-level and within-family grouped; "
                "cross-family author overlap is unresolvable because authoritative raw "
                "identity is not available in both frozen source families"
            ),
            "crosstab": author_group_analysis["crosstab"],
        },
        "canonical_base_recommendation": "Use the frozen benchmark for pre-boundary conversations after explicit quote-parent reconciliation, and prospective-v4 for boundary/post-boundary graph evidence; use historical target corpora only as corroboration.",
        "final_held_out_selected": False,
    }
    exposure_summary = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "registry_row_count": len(exposure_rows),
        "registry_entity_type_counts": dict(sorted(Counter(row["entity_type"] for row in exposure_rows).items())),
        "model_generated_replicate_rows_never_counted_as_independent_conversations": sum(
            int(row.get("model_generated_replicate_count") or 0)
            for row in exposure_rows
            if row.get("entity_type") == "case"
        ),
        "conversation_row_count": len(records),
        "conversation_status_counts": {
            "directly_exposed": len(direct_exposed),
            "structurally_mined_only": len(structural_only),
            "unexposed_candidate": len(unexposed),
        },
        "potential_unexposed_by_grade": feasibility_summary["potential_unexposed_by_grade"],
        "status_category_counts": dict(sorted(Counter(status for record in records for status in record["prior_exposure_categories"]).items())),
        "author_group_crosstab": author_group_analysis["crosstab"],
        "within_family_author_group_count": len(
            author_group_analysis["within_family_author_groups"]
        ),
        "cross_family_author_group_count": len(
            author_group_analysis["cross_family_author_groups"]
        ),
        "contributor_exposure_observation_count": len(
            author_group_analysis["contributor_exposure_observations"]
        ),
        "author_binding_audit": author_binding_audit,
        "final_held_out_selected_or_opened": False,
        "newly_mined_is_not_held_out": True,
        "prospective_is_not_automatically_held_out": True,
    }
    return (
        records,
        feasibility_summary,
        sorted(exposure_rows, key=lambda row: row["exposure_key"]),
        exposure_summary,
        turns_by_conversation,
        target_rows,
        target_structural_exclusions,
        target_crosstab,
    )


def _feasibility_markdown(summary: Mapping[str, Any]) -> list[str]:
    grades = summary["grade_counts"]
    targets = summary["target_prefix_counts"]
    structural = summary["target_structural_reconciliation"]
    outcome_summary = ", ".join(
        f"{name}={targets.get(name + '_target_prefixes', 0)}"
        for name in OUTCOME_EVIDENCE_CLASSES
    )
    return [
        "# Corpus feasibility audit",
        "",
        f"The structural union contains **{summary['canonical_union_conversation_count']}** independent conversations: Grade A **{grades['A']}**, Grade B **{grades['B']}**, and Grade C **{grades['C']}**.",
        "",
        f"There are **{targets['total_usable_target_prefixes']}** structurally usable target prefixes. Outcome evidence is partitioned into {outcome_summary}.",
        "",
        f"The **{structural['potential_source_target_count']}** potential source targets partition into **{structural['structurally_usable_target_prefix_count']}** usable prefixes and **{structural['structurally_excluded_target_count']}** private structural-exclusion records. Structural exclusions are reported separately and are not assigned outcome classes or included in the cross-tab.",
        "",
        f"Directly exposed conversations: **{summary['directly_exposed_conversation_count']}**. Structurally mined-only conversations: **{summary['structurally_mined_only_conversation_count']}**. Potential unexposed candidates, without selecting or opening a final test set: **{summary['potential_unexposed_candidate_count']}**.",
        "",
        summary["canonical_base_recommendation"],
        "",
        "Author-grouped splitting is enforceable within each source family using the domain-qualified pseudonym scheme and value. Authoritative identity is unavailable in both frozen families, so cross-family overlap is not inferred and cross-family-clean eligibility remains unavailable.",
        "",
        "No author-group split or final held-out set was selected or opened in Phase 1.2.",
    ]


def _parent_turn_id(turn: Mapping[str, Any], post_to_turn: Mapping[str, Mapping[str, Any]]) -> str | None:
    parent_post_id = turn.get("parent_id") if "parent_id" in turn else turn.get("parent_post_id")
    if parent_post_id is None:
        return None
    parent = post_to_turn.get(str(parent_post_id))
    return str(parent.get("turn_id")) if parent and parent.get("turn_id") else None


def _ancestor_chain(
    turns: Sequence[Mapping[str, Any]], target_identity: str
) -> list[Mapping[str, Any]]:
    """Return the exact in-segment ancestor chain ending at one target."""
    post_to_turn = {str(turn.get("post_id")): turn for turn in turns if turn.get("post_id") is not None}
    turn_to_turn = {str(turn.get("turn_id")): turn for turn in turns if turn.get("turn_id") is not None}
    target = turn_to_turn.get(target_identity) or post_to_turn.get(target_identity)
    if target is None:
        raise Phase1Error(f"target is absent from conversation: {target_identity}")
    chain: list[Mapping[str, Any]] = []
    seen_posts: set[str] = set()
    cursor: Mapping[str, Any] | None = target
    while cursor is not None:
        post_id = str(cursor.get("post_id") or cursor.get("turn_id"))
        if post_id in seen_posts:
            raise Phase1Error(f"parent cycle while constructing target prefix: {target_identity}")
        seen_posts.add(post_id)
        chain.append(cursor)
        parent_id = cursor.get("parent_id") if "parent_id" in cursor else cursor.get("parent_post_id")
        cursor = post_to_turn.get(str(parent_id)) if parent_id is not None else None
    chain.reverse()
    return chain


def _target_sequence_classification(
    chain: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Classify one exact target ancestry without treating an account root as a reply."""
    if not chain:
        raise Phase1Error("target sequence classification requires a non-empty chain")
    preceding = list(chain[:-1])
    preceding_user_turn_count = sum(
        turn.get("author_role") == "user" for turn in preceding
    )
    preceding_account_turn_count = sum(
        turn.get("author_role") == "account" for turn in preceding
    )
    preceding_account_root_count = sum(
        turn.get("author_role") == "account"
        and _parent_post_id(turn) is None
        for turn in preceding
    )

    parent_linked_account_reply_indexes: list[int] = []
    for index, turn in enumerate(preceding):
        if (
            index == 0
            or turn.get("author_role") != "account"
            or turn.get("publication_status") not in {"published", "observed"}
        ):
            continue
        parent = preceding[index - 1]
        if (
            parent.get("author_role") == "user"
            and _parent_post_id(turn) is not None
            and str(_parent_post_id(turn)) == str(parent.get("post_id"))
        ):
            parent_linked_account_reply_indexes.append(index)

    preceding_account_reply_count = len(parent_linked_account_reply_indexes)
    target_follows_prior_account_reply = bool(
        parent_linked_account_reply_indexes
        and parent_linked_account_reply_indexes[-1] == len(preceding) - 1
    )
    reason: str | None = None
    if not preceding:
        sequence_class = "initial_user_target"
    elif preceding_user_turn_count > 0 and preceding_account_turn_count == 0:
        sequence_class = "pre_account_user_follow_up"
    elif preceding_account_reply_count > 0 and preceding_user_turn_count > 0:
        sequence_class = "persistent_multiturn_target"
    elif (
        preceding_account_turn_count > 0
        and preceding_user_turn_count == 0
        and preceding_account_reply_count == 0
    ):
        sequence_class = "account_root_response"
    else:
        sequence_class = "other_sequence"
        if preceding_user_turn_count and preceding_account_turn_count:
            reason = "preceding_user_and_account_turns_without_parent_linked_published_reply"
        else:
            reason = "structurally_valid_sequence_outside_defined_target_strata"
    return {
        "target_sequence_class": sequence_class,
        "target_sequence_other_reason": reason,
        "preceding_account_root_count": preceding_account_root_count,
        "preceding_user_turn_count": preceding_user_turn_count,
        "preceding_account_turn_count": preceding_account_turn_count,
        "preceding_account_reply_count": preceding_account_reply_count,
        "target_follows_prior_account_reply": target_follows_prior_account_reply,
        "account_root_response_control": sequence_class == "account_root_response",
        "persistent_multiturn_evaluation_candidate": (
            sequence_class == "persistent_multiturn_target"
        ),
        "multi_turn_evaluation_candidate": (
            sequence_class == "persistent_multiturn_target"
        ),
        "first_response_control": sequence_class
        in {"initial_user_target", "account_root_response"},
    }


def _ancestor_prefix(turns: Sequence[Mapping[str, Any]], target_identity: str) -> list[dict[str, Any]]:
    post_to_turn = {str(turn.get("post_id")): turn for turn in turns if turn.get("post_id") is not None}
    chain = _ancestor_chain(turns, target_identity)
    result: list[dict[str, Any]] = []
    for index, turn in enumerate(chain):
        text = _text_for_turn(turn)
        if text is None:
            raise Phase1Error(f"calibration target prefix has missing text: {target_identity}")
        result.append(
            {
                "turn_index": index,
                "turn_id": str(turn.get("turn_id")),
                "post_id": str(turn.get("post_id")),
                "parent_turn_id": _parent_turn_id(turn, post_to_turn),
                "author_role": turn.get("author_role"),
                "author_key": turn.get("author_key"),
                "timestamp": turn.get("timestamp") or turn.get("created_at"),
                "publication_status": turn.get("publication_status"),
                "text": text,
            }
        )
    return result


def _normalise_exposure_status(statuses: Iterable[str]) -> str:
    values = set(statuses)
    if values & EXPOSED_STATUSES:
        return "exposed"
    if "structurally_mined_only" in values:
        return "structurally_mined_only"
    if "unexposed_candidate" in values or not values:
        return "genuinely_unexposed"
    return "exposure_unknown"


def _target_specific_exposure_statuses(
    exposure_rows: Sequence[Mapping[str, Any]],
    conversation_key: str,
    target_turn_id: str,
    target_post_id: str,
) -> set[str]:
    identities = {target_turn_id, target_post_id}
    statuses: set[str] = set()
    for row in exposure_rows:
        if row.get("entity_type") == "conversation" or row.get("conversation_key") != conversation_key:
            continue
        row_identities = {
            str(value)
            for value in [row.get("target_post_id"), *(row.get("target_identities") or [])]
            if value not in (None, "")
        }
        if identities & row_identities:
            statuses.update(str(value) for value in row.get("exposure_statuses", []))
    return statuses


def _build_contributor_exposure_observations(
    records: Sequence[Mapping[str, Any]],
    turns_by_conversation: Mapping[str, Sequence[Mapping[str, Any]]],
    exposure_rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Bind existing exposure evidence to exact pseudonymous contributors.

    Canonical conversation rows remain singular.  Conversation-wide evidence
    is copied once to every distinct user contributor in the transcript;
    target/branch/case evidence is copied only to an exactly resolved user
    contributor.  Ambiguous scoped evidence is retained for every plausible
    contributor with an unresolved binding marker so eligibility fails closed.
    """

    records_by_key = {
        str(record["conversation_key"]): record for record in records
    }
    contributors_by_conversation: dict[str, set[str]] = defaultdict(set)
    identity_to_contributor: dict[str, tuple[str, str]] = {}
    for conversation_key, turns in turns_by_conversation.items():
        key = str(conversation_key)
        for turn in turns:
            if turn.get("author_role") != "user":
                continue
            author_value = turn.get("author_key")
            if not isinstance(author_value, str) or not author_value.strip():
                continue
            contributors_by_conversation[key].add(author_value)
            for field in ("turn_id", "post_id"):
                value = turn.get(field)
                if value in (None, ""):
                    continue
                identity = str(value)
                resolved = (key, author_value)
                previous = identity_to_contributor.get(identity)
                if previous is not None and previous != resolved:
                    raise Phase1Error(
                        "user-turn identity resolves to multiple contributors"
                    )
                identity_to_contributor[identity] = resolved

    observations_by_identity: dict[tuple[str, str, str], dict[str, Any]] = {}

    def merge_observation(
        *,
        conversation_key: str,
        principal_author_key: str,
        observation_scope: str,
        source_exposure_key: str,
        statuses: Iterable[str],
        reasons: Iterable[str],
        binding_status: str,
        binding_reasons: Iterable[str] = (),
    ) -> None:
        record = records_by_key[conversation_key]
        author_key_scheme = str(record.get("author_key_scheme") or "")
        if not author_key_scheme:
            return
        identity = {
            "conversation_key": conversation_key,
            "author_key_scheme": author_key_scheme,
            "principal_author_key": principal_author_key,
        }
        identity_key = (
            conversation_key,
            author_key_scheme,
            principal_author_key,
        )
        observation = observations_by_identity.setdefault(
            identity_key,
            {
                "contributor_exposure_observation_key": (
                    "contributor-exposure-observation-sha256-v1-"
                    + sha256_bytes(
                        canonical_json_bytes(
                            {
                                "purpose": (
                                    "mrsMThatcher/proposition-ledger/phase1.2/"
                                    "contributor-exposure-observation/v1"
                                ),
                                **identity,
                            }
                        )
                    )
                ),
                "conversation_key": conversation_key,
                "observation_scopes": [],
                "source_exposure_keys": [],
                "author_key_scheme": author_key_scheme,
                "principal_author_key": principal_author_key,
                "within_family_author_identity_status": "available",
                "contributor_identity_binding_status": "available",
                "contributor_identity_binding_reasons": [],
                "prior_exposure_status": "genuinely_unexposed",
                "prior_exposure_categories": [],
                "prior_exposure_reasons": [],
                "cross_family_author_group_key": None,
                "cross_family_author_identity_status": "unavailable",
                "cross_family_author_identity_reasons": [
                    "authoritative_raw_identity_unavailable_in_both_source_families"
                ],
            },
        )
        observation["observation_scopes"] = sorted(
            {*observation["observation_scopes"], observation_scope}
        )
        observation["source_exposure_keys"] = sorted(
            {*observation["source_exposure_keys"], source_exposure_key}
        )
        observation["prior_exposure_categories"] = sorted(
            {
                *observation["prior_exposure_categories"],
                *(str(value) for value in statuses if value),
            }
        )
        observation["prior_exposure_reasons"] = sorted(
            {
                *observation["prior_exposure_reasons"],
                *(
                    str(value).replace("\n", " ")[:256]
                    for value in reasons
                    if value
                ),
            }
        )[:64]
        observation["prior_exposure_status"] = _normalise_exposure_status(
            observation["prior_exposure_categories"]
        )
        if binding_status == "unresolved":
            observation["contributor_identity_binding_status"] = "unresolved"
        observation["contributor_identity_binding_reasons"] = sorted(
            {
                *observation["contributor_identity_binding_reasons"],
                *(
                    str(value).replace("\n", " ")[:256]
                    for value in binding_reasons
                    if value
                ),
            }
        )[:64]

    for conversation_key in sorted(records_by_key):
        record = records_by_key[conversation_key]
        statuses = record.get(
            "conversation_wide_exposure_categories",
            record.get("prior_exposure_categories", []),
        )
        reasons = record.get(
            "conversation_wide_exposure_reasons",
            record.get("prior_exposure_reasons", []),
        )
        for author_key in sorted(contributors_by_conversation[conversation_key]):
            merge_observation(
                conversation_key=conversation_key,
                principal_author_key=author_key,
                observation_scope="conversation",
                source_exposure_key=f"conversation:{conversation_key}",
                statuses=statuses,
                reasons=reasons,
                binding_status="available",
            )

    scoped_bound_record_count = 0
    scoped_unresolved_record_count = 0
    for exposure_row in sorted(
        (
            row
            for row in exposure_rows
            if row.get("entity_type") in {"target", "branch", "case"}
        ),
        key=lambda row: str(row.get("exposure_key") or ""),
    ):
        source_exposure_key = str(exposure_row.get("exposure_key") or "")
        identities = {
            str(value)
            for value in [
                exposure_row.get("target_post_id"),
                *(exposure_row.get("target_identities") or []),
            ]
            if value not in (None, "")
        }
        resolved = {
            identity_to_contributor[identity]
            for identity in identities
            if identity in identity_to_contributor
        }
        declared_conversation_keys = {
            str(value)
            for value in [
                exposure_row.get("conversation_key"),
                *(exposure_row.get("conversation_keys") or []),
            ]
            if value not in (None, "")
        }
        declared_binding_conflict = bool(
            resolved
            and declared_conversation_keys
            and any(
                conversation_key not in declared_conversation_keys
                for conversation_key, _author_key in resolved
            )
        )
        if len(resolved) == 1 and not declared_binding_conflict:
            scoped_bound_record_count += 1
            conversation_key, author_key = next(iter(resolved))
            merge_observation(
                conversation_key=conversation_key,
                principal_author_key=author_key,
                observation_scope=str(exposure_row.get("entity_type")),
                source_exposure_key=source_exposure_key,
                statuses=exposure_row.get("exposure_statuses", []),
                reasons=exposure_row.get("exposure_reasons", []),
                binding_status="available",
            )
            continue

        scoped_unresolved_record_count += 1
        plausible = set(resolved)
        if declared_binding_conflict or not plausible:
            plausible.update(
                {
                (conversation_key, author_key)
                for conversation_key in declared_conversation_keys
                for author_key in contributors_by_conversation.get(
                    conversation_key, set()
                )
                }
            )
        for conversation_key, author_key in sorted(plausible):
            merge_observation(
                conversation_key=conversation_key,
                principal_author_key=author_key,
                observation_scope=str(exposure_row.get("entity_type")),
                source_exposure_key=source_exposure_key,
                statuses=exposure_row.get("exposure_statuses", []),
                reasons=exposure_row.get("exposure_reasons", []),
                binding_status="unresolved",
                binding_reasons=["scoped_exposure_contributor_unresolved"],
            )

    observations = list(observations_by_identity.values())
    observation_keys = [
        str(row["contributor_exposure_observation_key"]) for row in observations
    ]
    if len(observation_keys) != len(set(observation_keys)):
        raise Phase1Error("duplicate contributor exposure observation key")
    observations.sort(
        key=lambda row: str(row["contributor_exposure_observation_key"])
    )
    distribution = Counter(
        min(len(contributors_by_conversation[key]), 2)
        for key in records_by_key
    )
    return observations, {
        "conversation_external_contributor_counts": {
            "zero": distribution[0],
            "one": distribution[1],
            "multiple": distribution[2],
        },
        "conversation_wide_contributor_observation_count": sum(
            "conversation" in row["observation_scopes"] for row in observations
        ),
        "contributor_exposure_observation_count": len(observations),
        "scoped_exposure_record_bound_count": scoped_bound_record_count,
        "scoped_exposure_record_unresolved_count": (
            scoped_unresolved_record_count
        ),
    }


def _author_group_aggregate_counts(
    analysis: Mapping[str, Any],
) -> dict[str, int]:
    groups = list(analysis.get("within_family_author_groups", []))
    return {
        "comparable_within_family_author_group_count": len(groups),
        "clean_group_count": sum(
            row.get("author_group_exposure_status")
            == "clean_genuinely_unexposed_group"
            for row in groups
        ),
        "directly_exposed_group_count": sum(
            row.get("author_group_contains_directly_exposed_material") is True
            for row in groups
        ),
        "structurally_mined_group_count": sum(
            row.get("author_group_contains_structurally_mined_material") is True
            for row in groups
        ),
        "mixed_structural_unexposed_group_count": sum(
            row.get("author_group_exposure_status")
            == "mixed_unexposed_and_structurally_mined"
            for row in groups
        ),
        "groupwise_split_required_count": sum(
            row.get("author_group_requires_groupwise_split") is True
            for row in groups
        ),
    }


def _author_binding_audit(
    author_groups: Any,
    target_rows_before_grouping: Sequence[Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
    corrected_analysis: Mapping[str, Any],
    contributor_binding_audit: Mapping[str, Any],
) -> dict[str, Any]:
    """Return aggregate-only old/corrected author-binding reconciliation."""

    records_by_key = {
        str(record["conversation_key"]): record for record in records
    }
    legacy_targets: list[dict[str, Any]] = []
    for source_row in target_rows_before_grouping:
        row = copy.deepcopy(dict(source_row))
        record = records_by_key[str(row["conversation_key"])]
        legacy_value = record.get("principal_author_key")
        legacy_author = (
            legacy_value
            if isinstance(legacy_value, str) and legacy_value.strip()
            else None
        )
        scheme = str(record.get("author_key_scheme") or "")
        available = bool(scheme and legacy_author is not None)
        row["principal_author_key"] = legacy_author
        row["target_author_identity_status"] = (
            "available" if available else "unavailable"
        )
        row["target_author_identity_reasons"] = (
            [] if available else ["target_author_identity_unavailable"]
        )
        row["author_group_comparability_status"] = (
            "comparable_within_source_family_only"
            if available
            else "not_comparable"
        )
        row["within_family_author_group_key"] = (
            f"{scheme}:{legacy_author}" if available else None
        )
        conversation_exposure = _normalise_exposure_status(
            record.get("prior_exposure_categories", [])
        )
        target_exposure = str(
            row.get("target_exposure_status") or "exposure_unknown"
        )
        row["conversation_exposure_status"] = conversation_exposure
        row["author_group_conversation_exposure_status"] = (
            conversation_exposure
        )
        row["author_group_target_exposure_status"] = target_exposure
        row["effective_exposure_status"] = (
            "exposed"
            if "exposed" in {conversation_exposure, target_exposure}
            else "structurally_mined_only"
            if "structurally_mined_only"
            in {conversation_exposure, target_exposure}
            else "genuinely_unexposed"
            if conversation_exposure == target_exposure == "genuinely_unexposed"
            else "exposure_unknown"
        )
        row["author_group_effective_exposure_status"] = row[
            "effective_exposure_status"
        ]
        legacy_targets.append(row)

    legacy_analysis = author_groups.apply_author_group_exposure(
        legacy_targets,
        records,
    )
    corrected_targets = list(corrected_analysis["target_rows"])
    legacy_eligible_keys = {
        str(row["target_key"])
        for row in legacy_analysis["target_rows"]
        if row.get("preliminary_within_family_held_out_eligibility") is True
    }
    corrected_eligible_keys = {
        str(row["target_key"])
        for row in corrected_targets
        if row.get("preliminary_within_family_held_out_eligibility") is True
    }

    def eligibility_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
        eligible = [
            row
            for row in rows
            if row.get("preliminary_within_family_held_out_eligibility") is True
        ]
        return {
            "eligible_target_prefix_count": len(eligible),
            "eligible_conversation_count": len(
                {str(row["conversation_key"]) for row in eligible}
            ),
            "eligible_contributor_group_count": len(
                {
                    str(row["within_family_author_group_key"])
                    for row in eligible
                    if row.get("within_family_author_group_key")
                }
            ),
        }

    forbidden_fields: set[str] = set()

    def scan_keys(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if str(key).lower() in RAW_ID_KEYS:
                    forbidden_fields.add(str(key).lower())
                scan_keys(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                scan_keys(child)

    scan_keys(corrected_analysis["contributor_exposure_observations"])
    scan_keys(corrected_targets)
    crosstab_errors = author_groups.author_group_crosstab_errors(
        corrected_targets,
        corrected_analysis["conversation_rows"],
        corrected_analysis["within_family_author_groups"],
        corrected_analysis["cross_family_author_groups"],
        corrected_analysis["crosstab"],
        corrected_analysis["contributor_exposure_observations"],
    )
    changed_author_count = sum(
        row.get("principal_author_key")
        != records_by_key[str(row["conversation_key"])].get(
            "principal_author_key"
        )
        for row in corrected_targets
    )
    available_changed_author_count = sum(
        row.get("target_author_identity_status") == "available"
        and row.get("principal_author_key")
        != records_by_key[str(row["conversation_key"])].get(
            "principal_author_key"
        )
        for row in corrected_targets
    )
    return {
        "schema_version": "proposition-ledger-author-binding-audit-v1",
        "canonical_conversation_count": len(records),
        **copy.deepcopy(dict(contributor_binding_audit)),
        "target_exact_author_differs_from_legacy_principal_count": (
            changed_author_count
        ),
        "target_available_exact_author_differs_from_legacy_principal_count": (
            available_changed_author_count
        ),
        "target_author_identity_available_count": sum(
            row.get("target_author_identity_status") == "available"
            for row in corrected_targets
        ),
        "candidate_target_author_agreement_record_count": sum(
            int(row.get("review_candidate_principal_agreement_count") or 0)
            for row in corrected_targets
        ),
        "candidate_target_author_conflicting_target_count": sum(
            row.get("target_author_identity_status") == "conflicting"
            for row in corrected_targets
        ),
        "target_author_identity_unavailable_count": sum(
            row.get("target_author_identity_status") == "unavailable"
            for row in corrected_targets
        ),
        "legacy_author_group_totals": _author_group_aggregate_counts(
            legacy_analysis
        ),
        "corrected_author_group_totals": _author_group_aggregate_counts(
            corrected_analysis
        ),
        "legacy_preliminary_eligibility_totals": eligibility_counts(
            legacy_analysis["target_rows"]
        ),
        "corrected_preliminary_eligibility_totals": eligibility_counts(
            corrected_targets
        ),
        "former_eligible_prefix_count": len(legacy_eligible_keys),
        "former_eligible_prefixes_retained_count": len(
            legacy_eligible_keys & corrected_eligible_keys
        ),
        "former_eligible_prefixes_changed_status_count": len(
            legacy_eligible_keys - corrected_eligible_keys
        ),
        "newly_eligible_prefix_count": len(
            corrected_eligible_keys - legacy_eligible_keys
        ),
        "cross_family_identity_fabricated": False,
        "full_crosstab_reconciliation_status": (
            "passed" if not crosstab_errors else "failed"
        ),
        "full_crosstab_reconciliation_error_count": len(crosstab_errors),
        "privacy_raw_identity_field_scan_status": (
            "passed" if not forbidden_fields else "failed"
        ),
        "privacy_raw_identity_field_match_count": len(forbidden_fields),
        "final_held_out_selected_or_opened": False,
    }


def _prefix_turn_count_band(count: int) -> str:
    if count == 1:
        return "1"
    if count <= 3:
        return "2-3"
    if count <= 5:
        return "4-5"
    return "6+"


def _stability_for_record(record: Mapping[str, Any]) -> tuple[str, str]:
    if "benchmark_conversations" in record.get("source_ids", []):
        return "frozen_historical", "frozen_historical"
    activity = record.get("activity_status")
    if activity == "quiescent":
        return "quiescent_at_frozen_cutoff", "quiescent"
    if activity == "open":
        return "open_at_frozen_cutoff", "open"
    return "stability_unknown", "unknown"


def _source_family_for_record(record: Mapping[str, Any]) -> str:
    source_ids = [str(value) for value in record.get("source_ids", [])]
    if source_ids == ["benchmark_conversations"]:
        return "benchmark"
    if source_ids == ["prospective_conversations"]:
        return "prospective-v4"
    return "source_family_unknown"


def _target_structural_assessment(
    record: Mapping[str, Any],
    turns: Sequence[Mapping[str, Any]],
    target_turn_id: str,
) -> tuple[Mapping[str, Any] | None, list[Mapping[str, Any]], list[str]]:
    """Return one target, its ancestry, and bounded structural exclusions."""
    turns_by_id = {str(turn.get("turn_id") or ""): turn for turn in turns}
    target = turns_by_id.get(target_turn_id)
    if target is None:
        return None, [], ["target_absent_from_canonical_conversation"]

    reasons: set[str] = set()
    if target.get("author_role") != "user":
        reasons.add("target_role_not_user")
    if not target.get("post_id"):
        reasons.add("target_post_identity_missing")
    try:
        chain = _ancestor_chain(turns, target_turn_id)
    except Phase1Error:
        chain = []
        reasons.add("target_ancestry_cycle")

    root_post_id = str(record.get("root_post_id") or "")
    if not root_post_id:
        reasons.add("declared_root_identity_unavailable")
    if chain:
        chain_root_post_id = str(chain[0].get("post_id") or "")
        if root_post_id and chain_root_post_id != root_post_id:
            reasons.add("target_ancestry_does_not_reach_declared_root")
        _, graph_unambiguous, complete_prefix = _graph_properties(
            chain, chain[0].get("post_id")
        )
        if not graph_unambiguous or not complete_prefix:
            reasons.add("target_prefix_parent_graph_ambiguous_or_incomplete")
        if not all(_text_for_turn(turn) is not None for turn in chain):
            reasons.add("target_prefix_text_incomplete")
        if not _immutable_turn_identities_complete(chain):
            reasons.add("target_prefix_immutable_identity_incomplete")
        if not _roles_complete(chain):
            reasons.add("target_prefix_role_assignment_incomplete")
        if not _turn_order_unambiguous(chain):
            reasons.add("target_prefix_turn_order_ambiguous")
    elif "target_ancestry_cycle" not in reasons:
        reasons.add("target_prefix_parent_graph_ambiguous_or_incomplete")

    reason_order = {
        reason: index
        for index, reason in enumerate(TARGET_STRUCTURAL_EXCLUSION_REASONS)
    }
    return target, chain, sorted(reasons, key=reason_order.__getitem__)


def _build_target_prefix_rows(
    manifest: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    exposure_rows: Sequence[Mapping[str, Any]],
    turns_by_conversation: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition potential targets into usable rows and structural exclusions.

    The frozen target universe is the union of exact user turns with a
    parent-linked published/observed account reply and user branch tips in the
    frozen prospective review-candidate source.  This extends the reviewed
    Phase 1 target universe only far enough to retain open tips for explicit
    exclusion; it performs no new mining.  Every source target is emitted
    exactly once as either a structurally usable target row or a private,
    text-free exclusion row with bounded reasons.
    """
    conversation_keys = {str(record["conversation_key"]) for record in records}
    candidates = _target_candidate_map(manifest, conversation_keys)
    expected_pairs = _source_target_pairs(records, turns_by_conversation, candidates)
    declared_pairs = _declared_target_pairs(records)
    if declared_pairs != expected_pairs:
        raise Phase1Error(
            "declared target universe differs from independently derived source targets"
        )

    rows: list[dict[str, Any]] = []
    structural_exclusions: list[dict[str, Any]] = []
    for record in records:
        conversation_key = str(record["conversation_key"])
        turns = list(turns_by_conversation[conversation_key])
        target_ids = {
            target_turn_id
            for candidate_conversation, target_turn_id in expected_pairs
            if candidate_conversation == conversation_key
        }
        for target_turn_id in sorted(target_ids):
            target, chain, structural_reasons = _target_structural_assessment(
                record, turns, target_turn_id
            )
            if structural_reasons:
                candidate = candidates.get((conversation_key, target_turn_id))
                candidate_tip = (
                    (candidate.get("path_turns") or [])[-1]
                    if candidate and candidate.get("path_turns")
                    else {}
                )
                target_post_id = str(
                    (target or {}).get("post_id")
                    or candidate_tip.get("post_id")
                    or ""
                )
                source_kinds: list[str] = []
                if candidate is not None:
                    source_kinds.append("prospective_review_candidate_tip")
                if target_turn_id in _published_reply_target_turns(turns):
                    source_kinds.append("confirmed_published_reply_parent")
                structural_exclusions.append(
                    _row_with_hash(
                        {
                            "structural_exclusion_key": (
                                "target-structural-exclusion-"
                                + sha256_bytes(
                                    f"{conversation_key}\0{target_turn_id}".encode(
                                        "utf-8"
                                    )
                                )
                            ),
                            "conversation_key": conversation_key,
                            "target_turn_id": target_turn_id,
                            "target_post_id": target_post_id or None,
                            "source_family": _source_family_for_record(record),
                            "reconstruction_grade": record.get(
                                "reconstruction_grade"
                            ),
                            "potential_target_source_kinds": sorted(source_kinds),
                            "structural_exclusion_status": (
                                "excluded_from_structurally_usable_target_universe"
                            ),
                            "structural_exclusion_reasons": structural_reasons,
                            "source_provenance": [
                                str(value)
                                for value in record.get("source_ids", [])
                            ],
                        }
                    )
                )
                continue
            if target is None or not chain:
                raise Phase1Error(
                    "structural assessment emitted no row or exclusion: "
                    f"{conversation_key}:{target_turn_id}"
                )
            complete_target_ancestry = True
            target_post_id = str(target.get("post_id") or "")
            candidate = candidates.get((conversation_key, target_turn_id))
            outcome = _classify_target_outcome(
                target,
                record,
                turns,
                candidate,
            )
            sequence = _target_sequence_classification(chain)
            conversation_exposure = _normalise_exposure_status(
                record.get("prior_exposure_categories", [])
            )
            author_group_conversation_exposure = _normalise_exposure_status(
                record.get(
                    "conversation_wide_exposure_categories",
                    record.get("prior_exposure_categories", []),
                )
            )
            target_statuses = _target_specific_exposure_statuses(
                exposure_rows,
                conversation_key,
                target_turn_id,
                target_post_id,
            )
            target_exposure = _normalise_exposure_status(target_statuses)
            effective_exposure = (
                "exposed"
                if "exposed" in {conversation_exposure, target_exposure}
                else "structurally_mined_only"
                if "structurally_mined_only"
                in {conversation_exposure, target_exposure}
                else "genuinely_unexposed"
                if conversation_exposure == target_exposure == "genuinely_unexposed"
                else "exposure_unknown"
            )
            author_group_effective_exposure = (
                "exposed"
                if "exposed"
                in {author_group_conversation_exposure, target_exposure}
                else "structurally_mined_only"
                if "structurally_mined_only"
                in {author_group_conversation_exposure, target_exposure}
                else "genuinely_unexposed"
                if author_group_conversation_exposure
                == target_exposure
                == "genuinely_unexposed"
                else "exposure_unknown"
            )
            stability_status, activity_status = _stability_for_record(record)
            source_ids = [str(value) for value in record.get("source_ids", [])]
            source_family = _source_family_for_record(record)
            author_scheme = str(record.get("author_key_scheme") or "")
            target_author_identity = _target_author_identity(target, candidate)
            author_key = target_author_identity["principal_author_key"]
            target_author_status = target_author_identity[
                "target_author_identity_status"
            ]
            within_family_identity_available = bool(
                author_scheme
                and author_key not in (None, "")
                and target_author_status == "available"
            )
            exclusion_reasons: list[str] = []
            if record.get("reconstruction_grade") != "A":
                exclusion_reasons.append("reconstruction_grade_not_a")
            if effective_exposure != "genuinely_unexposed":
                exclusion_reasons.append("effective_exposure_not_genuinely_unexposed")
            if stability_status not in {
                "frozen_historical",
                "quiescent_at_frozen_cutoff",
            }:
                exclusion_reasons.append("conversation_not_frozen_or_quiescent")
            if sequence["target_sequence_class"] != "persistent_multiturn_target":
                exclusion_reasons.append("not_a_persistent_multiturn_target")
            if not complete_target_ancestry:
                exclusion_reasons.append("incomplete_target_ancestry")
            if outcome["outcome_evidence_class"] == "conflicting_outcome_evidence":
                exclusion_reasons.append("outcome_evidence_conflict")
            if target_author_status == "conflicting":
                exclusion_reasons.extend(
                    [
                        "target_author_identity_conflicting",
                        "within_family_identity_group_conflicting",
                    ]
                )
            elif not within_family_identity_available:
                exclusion_reasons.append("target_author_identity_unavailable")
                exclusion_reasons.append("within_family_identity_group_unavailable")
            row = {
                "target_key": "target-"
                + sha256_bytes(f"{conversation_key}\0{target_turn_id}".encode("utf-8")),
                "conversation_key": conversation_key,
                "target_turn_id": target_turn_id,
                "target_post_id": target_post_id,
                "source_family": source_family,
                "reconstruction_grade": record.get("reconstruction_grade"),
                "conversation_exposure_status": conversation_exposure,
                "target_exposure_status": target_exposure,
                "effective_exposure_status": effective_exposure,
                "author_group_conversation_exposure_status": (
                    author_group_conversation_exposure
                ),
                "author_group_target_exposure_status": target_exposure,
                "author_group_effective_exposure_status": (
                    author_group_effective_exposure
                ),
                "stability_status": stability_status,
                "activity_status_at_frozen_cutoff": activity_status,
                **outcome,
                "prefix_turn_count": len(chain),
                "prefix_turn_count_band": _prefix_turn_count_band(len(chain)),
                **sequence,
                **target_author_identity,
                "author_key_scheme": author_scheme,
                "author_group_comparability_status": (
                    "comparable_within_source_family_only"
                    if within_family_identity_available
                    else "identity_group_conflicting"
                    if target_author_status == "conflicting"
                    else "not_comparable"
                ),
                "within_family_author_group_key": (
                    f"{author_scheme}:{author_key}"
                    if within_family_identity_available
                    else None
                ),
                "complete_target_ancestry": complete_target_ancestry,
                "cross_family_author_group_key": None,
                "cross_family_author_identity_status": "unavailable",
                "cross_family_author_group_exposure_status": "identity_group_unavailable",
                "cross_family_author_group_exposure_reasons": [
                    "authoritative_raw_identity_is_not_available_in_both_frozen_source_families"
                ],
                "cross_family_identity_or_leakage_uncertainty": True,
                "preliminary_within_family_held_out_eligibility": not exclusion_reasons,
                "preliminary_within_family_held_out_exclusion_reasons": exclusion_reasons,
                "preliminary_cross_family_clean_held_out_eligibility": False,
                "preliminary_cross_family_clean_held_out_exclusion_reasons": [
                    "cross_family_author_identity_unavailable"
                ],
                "preliminary_held_out_eligibility": not exclusion_reasons,
                "preliminary_held_out_exclusion_reasons": exclusion_reasons,
                "source_provenance": source_ids,
            }
            rows.append(row)
    rows = [_row_with_hash(row) for row in rows]
    rows.sort(key=lambda row: (row["conversation_key"], row["target_turn_id"]))
    structural_exclusions.sort(
        key=lambda row: (row["conversation_key"], row["target_turn_id"])
    )
    target_keys = [str(row["target_key"]) for row in rows]
    if len(target_keys) != len(set(target_keys)):
        raise Phase1Error("target-prefix index contains duplicate target keys")
    exclusion_keys = [
        str(row["structural_exclusion_key"]) for row in structural_exclusions
    ]
    if len(exclusion_keys) != len(set(exclusion_keys)):
        raise Phase1Error("target structural exclusions contain duplicate keys")
    partition_errors = _target_partition_errors(
        expected_pairs, rows, structural_exclusions
    )
    if partition_errors:
        raise Phase1Error(
            "target-prefix partition does not reproduce the expected source universe: "
            + ",".join(partition_errors)
        )
    return rows, structural_exclusions


def _build_target_prefix_crosstab(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    def counts(field: str) -> dict[str, int]:
        return dict(
            sorted(Counter(str(row.get(field)) for row in rows).items())
        )

    sequence_counts = {
        sequence_class: sum(
            row.get("target_sequence_class") == sequence_class for row in rows
        )
        for sequence_class in TARGET_SEQUENCE_CLASSES
    }
    response_types = {
        "first_response_control": sum(
            row.get("first_response_control") is True for row in rows
        ),
        "multi_turn_evaluation_candidate": sum(
            row.get("persistent_multiturn_evaluation_candidate") is True
            or row.get("multi_turn_evaluation_candidate") is True
            for row in rows
        ),
        "other_pre_account_reply_target": sum(
            row.get("first_response_control") is not True
            and row.get("persistent_multiturn_evaluation_candidate") is not True
            and row.get("multi_turn_evaluation_candidate") is not True
            for row in rows
        ),
    }
    author_groups = Counter(
        str(
            row.get("within_family_author_group_key")
            or f"{row.get('author_key_scheme')}:{row.get('principal_author_key')}"
        )
        for row in rows
        if row.get("author_group_comparability_status")
        == "comparable_within_source_family_only"
    )
    outcome_counts = {
        outcome: sum(row.get("outcome_evidence_class") == outcome for row in rows)
        for outcome in OUTCOME_EVIDENCE_CLASSES
    }
    within_family_eligible = [
        row
        for row in rows
        if row.get("preliminary_within_family_held_out_eligibility") is True
        or (
            "preliminary_within_family_held_out_eligibility" not in row
            and row.get("preliminary_held_out_eligibility") is True
        )
    ]
    cross_family_eligible = [
        row
        for row in rows
        if row.get("preliminary_cross_family_clean_held_out_eligibility") is True
    ]
    grade_a = [row for row in rows if row.get("reconstruction_grade") == "A"]
    unexposed = [
        row for row in grade_a if row.get("effective_exposure_status") == "genuinely_unexposed"
    ]
    stable = [
        row
        for row in grade_a
        if row.get("stability_status")
        in {"frozen_historical", "quiescent_at_frozen_cutoff"}
    ]
    persistent = [
        row
        for row in rows
        if row.get("target_sequence_class") == "persistent_multiturn_target"
        or (
            "target_sequence_class" not in row
            and row.get("multi_turn_evaluation_candidate") is True
        )
    ]
    grade_a_persistent = [
        row for row in persistent if row.get("reconstruction_grade") == "A"
    ]
    source_outcomes: dict[str, dict[str, int]] = {}
    for family in sorted({str(row.get("source_family")) for row in rows}):
        source_outcomes[family] = {
            outcome: sum(
                row.get("source_family") == family
                and row.get("outcome_evidence_class") == outcome
                for row in rows
            )
            for outcome in OUTCOME_EVIDENCE_CLASSES
        }
    eligible_author_groups = {
        str(
            row.get("within_family_author_group_key")
            or f"{row.get('author_key_scheme')}:{row.get('principal_author_key')}"
        )
        for row in within_family_eligible
        if row.get("author_group_comparability_status")
        == "comparable_within_source_family_only"
    }
    comparable_group_keys = {
        str(row.get("within_family_author_group_key"))
        for row in rows
        if row.get("within_family_author_group_key") not in (None, "")
    }
    direct_exposure_groups = {
        str(row.get("within_family_author_group_key"))
        for row in rows
        if row.get("within_family_author_group_key") not in (None, "")
        and row.get("author_group_contains_directly_exposed_material") is True
    }
    groupwise_split_groups = {
        str(row.get("within_family_author_group_key"))
        for row in rows
        if row.get("within_family_author_group_key") not in (None, "")
        and row.get("author_group_requires_groupwise_split") is True
    }
    dimensions = {
        "reconstruction_grade": counts("reconstruction_grade"),
        "effective_exposure_status": counts("effective_exposure_status"),
        "source_family": counts("source_family"),
        "stability_status": counts("stability_status"),
        "outcome_evidence_class": outcome_counts,
        "target_sequence_class": sequence_counts,
        "prefix_turn_count_band": counts("prefix_turn_count_band"),
        "preceding_account_reply_count": counts("preceding_account_reply_count"),
        "preceding_account_root_count": counts("preceding_account_root_count"),
        "preceding_user_turn_count": counts("preceding_user_turn_count"),
        "preceding_account_turn_count": counts("preceding_account_turn_count"),
        "first_response_versus_multi_turn": dict(sorted(response_types.items())),
        "principal_author_group_where_comparable": dict(sorted(author_groups.items())),
        "author_group_exposure_status": counts("author_group_exposure_status"),
        "cross_family_author_identity_status": counts(
            "cross_family_author_identity_status"
        ),
        "preliminary_within_family_held_out_eligibility": counts(
            "preliminary_within_family_held_out_eligibility"
        ),
        "preliminary_cross_family_clean_held_out_eligibility": counts(
            "preliminary_cross_family_clean_held_out_eligibility"
        ),
        "preliminary_held_out_eligibility": counts(
            "preliminary_held_out_eligibility"
        ),
    }
    exposed_persistent = [
        row
        for row in persistent
        if row.get("effective_exposure_status") == "exposed"
    ]
    structural_persistent = [
        row
        for row in persistent
        if row.get("effective_exposure_status") == "structurally_mined_only"
    ]
    unexposed_stable_grade_a_persistent = [
        row
        for row in persistent
        if row.get("reconstruction_grade") == "A"
        and row.get("effective_exposure_status") == "genuinely_unexposed"
        and row.get("stability_status")
        in {"frozen_historical", "quiescent_at_frozen_cutoff"}
    ]
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "target_prefix_count": len(rows),
        "dimensions": dimensions,
        "source_family_by_outcome_evidence_class": source_outcomes,
        "headline_counts": {
            "grade_a_target_prefix_count": len(grade_a),
            "grade_a_genuinely_unexposed_target_prefix_count": len(unexposed),
            "grade_a_frozen_or_quiescent_target_prefix_count": len(stable),
            "grade_a_persistent_multiturn_target_prefix_count": len(
                grade_a_persistent
            ),
            "grade_a_multi_turn_target_prefix_count": len(grade_a_persistent),
            "initial_user_target_count": sequence_counts["initial_user_target"],
            "pre_account_user_follow_up_count": sequence_counts[
                "pre_account_user_follow_up"
            ],
            "account_root_response_count": sequence_counts[
                "account_root_response"
            ],
            "persistent_multiturn_target_count": sequence_counts[
                "persistent_multiturn_target"
            ],
            "other_sequence_count": sequence_counts["other_sequence"],
            "preliminary_within_family_held_out_eligible_target_prefix_count": len(
                within_family_eligible
            ),
            "preliminary_within_family_held_out_eligible_conversation_count": len(
                {str(row["conversation_key"]) for row in within_family_eligible}
            ),
            "preliminary_cross_family_clean_held_out_eligible_target_prefix_count": len(
                cross_family_eligible
            ),
            "preliminary_cross_family_clean_held_out_eligible_conversation_count": len(
                {str(row["conversation_key"]) for row in cross_family_eligible}
            ),
            "preliminary_held_out_eligible_target_prefix_count": len(
                within_family_eligible
            ),
            "preliminary_held_out_eligible_conversation_count": len(
                {str(row["conversation_key"]) for row in within_family_eligible}
            ),
            "preliminary_held_out_eligible_author_group_count": len(eligible_author_groups),
            "first_response_control_count": sum(
                row.get("first_response_control") is True for row in rows
            ),
            "open_conversation_target_prefix_count": sum(
                row.get("stability_status") == "open_at_frozen_cutoff" for row in rows
            ),
            "open_conversation_count": len(
                {
                    str(row["conversation_key"])
                    for row in rows
                    if row.get("stability_status") == "open_at_frozen_cutoff"
                }
            ),
            "exposed_persistent_multiturn_target_prefix_count": len(
                exposed_persistent
            ),
            "exposed_multi_turn_target_prefix_count": len(exposed_persistent),
            "structurally_mined_only_persistent_multiturn_target_prefix_count": len(
                structural_persistent
            ),
            "structurally_mined_only_multi_turn_target_prefix_count": len(
                structural_persistent
            ),
            "genuinely_unexposed_stable_grade_a_persistent_multiturn_target_prefix_count": len(
                unexposed_stable_grade_a_persistent
            ),
            "genuinely_unexposed_stable_grade_a_multi_turn_target_prefix_count": len(
                unexposed_stable_grade_a_persistent
            ),
            "genuinely_unexposed_stable_grade_a_multi_turn_conversation_count": len(
                {
                    str(row["conversation_key"])
                    for row in unexposed_stable_grade_a_persistent
                }
            ),
            "within_family_group_clean_persistent_target_prefix_count": sum(
                row in persistent for row in within_family_eligible
            ),
            "cross_family_clean_persistent_target_prefix_count": sum(
                row in persistent for row in cross_family_eligible
            ),
            "comparable_within_family_author_group_count": len(
                comparable_group_keys
            ),
            "cross_family_identity_available_target_count": sum(
                row.get("cross_family_author_identity_status") == "available"
                for row in rows
            ),
            "cross_family_identity_unavailable_target_count": sum(
                row.get("cross_family_author_identity_status") == "unavailable"
                for row in rows
            ),
            "author_group_contains_direct_exposure_count": len(
                direct_exposure_groups
            ),
            "author_group_requires_groupwise_split_count": len(
                groupwise_split_groups
            ),
            **{f"outcome_{key}_count": value for key, value in outcome_counts.items()},
        },
        "eligible_distinct_conversation_count": len(
            {str(row["conversation_key"]) for row in within_family_eligible}
        ),
        "eligible_comparable_author_group_count": len(eligible_author_groups),
        "author_grouping_cross_family_performed": False,
        "author_group_split_selected": False,
        "sample_size_sufficiency": "pending_development_only_power_analysis",
        "final_held_out_selected": False,
    }


def _target_prefix_crosstab_errors(
    rows: Sequence[Mapping[str, Any]], crosstab: Mapping[str, Any]
) -> list[str]:
    expected = _build_target_prefix_crosstab(rows)
    errors: list[str] = []
    if canonical_json_bytes(expected) != canonical_json_bytes(crosstab):
        errors.append("crosstab_does_not_reproduce_rows")
    target_keys = [str(row.get("target_key") or "") for row in rows]
    if len(target_keys) != len(set(target_keys)) or not all(target_keys):
        errors.append("target_keys_not_unique")
    outcome_total = sum(
        int(value)
        for value in crosstab.get("dimensions", {})
        .get("outcome_evidence_class", {})
        .values()
    )
    if outcome_total != len(rows):
        errors.append("outcome_partition_total")
    sequence_total = sum(
        int(value)
        for value in crosstab.get("dimensions", {})
        .get("target_sequence_class", {})
        .values()
    )
    if sequence_total != len(rows):
        errors.append("target_sequence_partition_total")
    if any(
        row.get("outcome_evidence_class") not in OUTCOME_EVIDENCE_CLASSES
        for row in rows
    ):
        errors.append("unknown_outcome_evidence_class")
    if any(
        row.get("target_sequence_class") not in TARGET_SEQUENCE_CLASSES
        for row in rows
    ):
        errors.append("unknown_target_sequence_class")
    if any(
        row.get("multi_turn_evaluation_candidate")
        != row.get("persistent_multiturn_evaluation_candidate")
        or row.get("persistent_multiturn_evaluation_candidate")
        != (row.get("target_sequence_class") == "persistent_multiturn_target")
        for row in rows
    ):
        errors.append("persistent_multiturn_alias_mismatch")
    if any(
        row.get("preliminary_within_family_held_out_eligibility") is True
        and row.get("target_sequence_class") != "persistent_multiturn_target"
        for row in rows
    ):
        errors.append("nonpersistent_target_preliminarily_eligible")
    if any(
        row.get("preliminary_held_out_eligibility") is True
        and row.get("stability_status") == "open_at_frozen_cutoff"
        for row in rows
    ):
        errors.append("open_target_preliminarily_eligible")
    if crosstab.get("author_group_split_selected") is not False:
        errors.append("author_group_split_selected_in_phase1_2")
    return errors


def _target_prefix_crosstab_markdown(crosstab: Mapping[str, Any]) -> list[str]:
    h = crosstab["headline_counts"]
    outcomes = crosstab["dimensions"]["outcome_evidence_class"]
    lines = [
        "# Target-prefix feasibility cross-tab",
        "",
        f"Total structurally usable target prefixes: **{crosstab['target_prefix_count']}**.",
        "",
        f"Grade A: **{h['grade_a_target_prefix_count']}**; genuinely unexposed Grade A: **{h['grade_a_genuinely_unexposed_target_prefix_count']}**; frozen or quiescent Grade A: **{h['grade_a_frozen_or_quiescent_target_prefix_count']}**.",
        "",
        f"Persistent multi-turn targets: **{h['persistent_multiturn_target_count']}** (Grade A **{h['grade_a_persistent_multiturn_target_prefix_count']}**). Account-root response controls: **{h['account_root_response_count']}**; initial user targets: **{h['initial_user_target_count']}**; pre-account user follow-ups: **{h['pre_account_user_follow_up_count']}**; other sequences: **{h['other_sequence_count']}**.",
        "",
        f"Within-family preliminary requirements are met by **{h['preliminary_within_family_held_out_eligible_target_prefix_count']}** persistent prefixes across **{h['preliminary_within_family_held_out_eligible_conversation_count']}** conversations and **{h['preliminary_held_out_eligible_author_group_count']}** comparable groups. Cross-family-clean eligibility is **{h['preliminary_cross_family_clean_held_out_eligible_target_prefix_count']}** because common authoritative identity is unavailable.",
        "",
        f"Open conversations contribute **{h['open_conversation_target_prefix_count']}** prefixes across **{h['open_conversation_count']}** conversations; all are excluded from preliminary eligibility.",
        "",
        "Outcome evidence: "
        + "; ".join(f"{name} **{outcomes.get(name, 0)}**" for name in OUTCOME_EVIDENCE_CLASSES)
        + ".",
        "",
        "## Exact dimensions",
        "",
    ]
    for dimension, values in crosstab["dimensions"].items():
        if dimension == "principal_author_group_where_comparable":
            lines.append(
                f"- `{dimension}`: {len(values)} distinct domain-qualified groups (individual pseudonyms omitted from Markdown)."
            )
        else:
            rendered = ", ".join(f"{key}={value}" for key, value in values.items())
            lines.append(f"- `{dimension}`: {rendered}.")
    lines.extend(
        [
            "",
            "Counts are structural preflight evidence only. Account roots never count as account replies. Statistical sufficiency remains pending a later development-only power analysis; no author-group split or final held-out set was selected.",
        ]
    )
    return lines


def _derive_disposition(readiness_gates: Mapping[str, Any]) -> str:
    """Derive the Phase 1.2 disposition from machine-readable gate statuses."""
    failed = {
        name
        for name, gate in readiness_gates.items()
        if name != "disposition"
        and isinstance(gate, Mapping)
        and gate.get("status") == "failed"
    }
    if "privacy_validation" in failed:
        return "phase1_2_blocked_by_privacy_failure"
    source_or_leakage = {
        "source_identity_validation",
        "deterministic_rebuild_validation",
        "target_outcome_classification_complete",
        "target_prefix_cross_tab_complete",
        "persistent_multiturn_classification_complete",
        "within_family_author_group_exposure_enforced",
        "no_future_turn_leakage",
    }
    if failed & source_or_leakage:
        return "phase1_2_blocked_by_source_identity_or_classification_failure"
    schema_or_materialiser = {
        "schema_validation",
        "synthetic_fixture_validation",
        "semantic_delta_schema_valid",
        "deterministic_materialiser_valid",
        "genesis_materialisation_valid",
        "first_seen_participant_registration_valid",
        "complete_incremental_chain_valid",
        "provider_schema_feature_inventory_valid",
        "provider_schema_compatibility_status",
        "transcript_first_gold_protocol_defined",
    }
    if failed & schema_or_materialiser:
        return "phase1_2_blocked_by_schema_or_materialiser_failure"
    if (
        "preliminary_within_family_unexposed_stable_persistent_target_count"
        in failed
    ):
        return "phase1_2_blocked_no_within_family_eligible_persistent_targets"
    return "phase1_2_complete_sample_and_provider_preflight_pending"


def _derive_readiness_gates(
    *,
    source_identity_valid: bool,
    deterministic_rebuild_valid: bool,
    privacy_valid: bool,
    schema_valid: bool,
    synthetic_fixtures_valid: bool,
    target_outcomes_valid: bool,
    target_crosstab_valid: bool,
    no_future_turn_leakage: bool,
    semantic_schema_valid: bool,
    materialiser_valid: bool,
    genesis_materialisation_valid: bool,
    first_seen_participant_registration_valid: bool,
    complete_incremental_chain_valid: bool,
    persistent_multiturn_classification_complete: bool,
    within_family_author_group_exposure_enforced: bool,
    cross_family_author_identity_status: str,
    provider_schema_feature_inventory_valid: bool,
    provider_schema_compatibility_status: str,
    transcript_first_protocol_valid: bool,
    preliminary_within_family_target_count: int,
    preliminary_cross_family_target_count: int,
    deterministic_rebuild_evidence: Any = FRESH_BUILD_DETERMINISM_EVIDENCE,
    provider_schema_compatibility_record: Mapping[str, Any] | None = None,
    provider_schema_feature_inventory: Mapping[str, Any] | None = None,
    provider_specific_validation_record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    def gate(passed: bool, evidence: Any) -> dict[str, Any]:
        return {"status": "passed" if passed else "failed", "evidence": evidence}

    compatibility_errors: list[str] = []
    if provider_schema_compatibility_record is not None or provider_schema_feature_inventory is not None:
        if provider_schema_compatibility_record is None or provider_schema_feature_inventory is None:
            compatibility_errors.append(
                "compatibility record and feature inventory must be supplied together"
            )
        else:
            provider_schema = _import_research_tool(
                "proposition_ledger_provider_schema"
            )

            if (
                provider_schema_compatibility_record.get("status")
                != provider_schema_compatibility_status
            ):
                compatibility_errors.append(
                    "readiness status does not match the compatibility record"
                )
            compatibility_errors.extend(
                provider_schema.validate_provider_schema_compatibility(
                    provider_schema_compatibility_record,
                    feature_inventory=provider_schema_feature_inventory,
                    provider_specific_validation_record=(
                        provider_specific_validation_record
                    ),
                )
            )
    if (
        provider_schema_compatibility_status == "passed"
        and provider_specific_validation_record is None
    ):
        compatibility_errors.append(
            "passed compatibility lacks an explicit provider-specific validation record"
        )
    effective_provider_compatibility_status = (
        "failed" if compatibility_errors else provider_schema_compatibility_status
    )

    gates: dict[str, Any] = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "source_identity_validation": gate(
            source_identity_valid, "all frozen source identities matched manifest"
        ),
        "deterministic_rebuild_validation": gate(
            deterministic_rebuild_valid,
            deterministic_rebuild_evidence,
        ),
        "privacy_validation": gate(privacy_valid, "private-output privacy validator"),
        "schema_validation": gate(schema_valid, "persisted, semantic, and protocol schemas"),
        "synthetic_fixture_validation": gate(
            synthetic_fixtures_valid, "all tracked synthetic fixtures and negative cases"
        ),
        "target_outcome_classification_complete": gate(
            target_outcomes_valid, "every indexed target has exactly one evidence class"
        ),
        "target_prefix_cross_tab_complete": gate(
            target_crosstab_valid, "all required cross-tab totals derive from target rows"
        ),
        "no_future_turn_leakage": gate(
            no_future_turn_leakage, "all indexed target chains terminate at the target"
        ),
        "semantic_delta_schema_valid": gate(
            semantic_schema_valid, SEMANTIC_DELTA_SCHEMA_VERSION
        ),
        "deterministic_materialiser_valid": gate(
            materialiser_valid, "pure local materialiser validation"
        ),
        "genesis_materialisation_valid": gate(
            genesis_materialisation_valid,
            "turn-zero ledger materialises from empty state with a null predecessor",
        ),
        "first_seen_participant_registration_valid": gate(
            first_seen_participant_registration_valid,
            "only the exact current speaker is registered at first appearance",
        ),
        "complete_incremental_chain_valid": gate(
            complete_incremental_chain_valid,
            "synthetic account-root, contributor, account-response, contributor-return chain",
        ),
        "persistent_multiturn_classification_complete": gate(
            persistent_multiturn_classification_complete,
            "every usable target has exactly one exact-ancestry sequence class",
        ),
        "within_family_author_group_exposure_enforced": gate(
            within_family_author_group_exposure_enforced,
            "domain-qualified groups propagate direct exposure and groupwise-split requirements",
        ),
        "cross_family_author_identity_status": {
            "status": cross_family_author_identity_status,
            "evidence": (
                "authoritative raw identity is unavailable in both frozen source families; "
                "no cross-family match is inferred"
                if cross_family_author_identity_status == "unavailable"
                else "cross-family identity availability derived from frozen authoritative metadata"
            ),
        },
        "provider_schema_feature_inventory_valid": gate(
            provider_schema_feature_inventory_valid,
            "deterministic provider-neutral semantic-schema feature inventory",
        ),
        "provider_schema_compatibility_status": {
            "status": effective_provider_compatibility_status,
            "evidence": (
                "; ".join(compatibility_errors)
                if compatibility_errors
                else "no provider/model profile selected; local provider-specific schema validation is required before any call"
            ),
        },
        "transcript_first_gold_protocol_defined": gate(
            transcript_first_protocol_valid,
            "two independent transcript-first raters, blinded adjudication, lock before reveal",
        ),
        "preliminary_within_family_unexposed_stable_persistent_target_count": gate(
            preliminary_within_family_target_count > 0,
            preliminary_within_family_target_count,
        ),
        "preliminary_cross_family_clean_persistent_target_count": {
            "status": (
                "unavailable"
                if cross_family_author_identity_status == "unavailable"
                else "passed"
                if preliminary_cross_family_target_count > 0
                else "failed"
            ),
            "evidence": preliminary_cross_family_target_count,
        },
        "sample_size_threshold_status": {
            "status": "pending_development_only_power_analysis",
            "evidence": "no minimum threshold invented; a later development-only power analysis is required",
        },
    }
    gates["disposition"] = _derive_disposition(gates)
    return gates


def _emitted_blocking_disposition(readiness: Mapping[str, Any]) -> str | None:
    """Return a gate failure that must be reported before the build exits."""
    disposition = str(readiness.get("disposition") or "")
    if disposition.startswith("phase1_2_blocked_"):
        return disposition
    return None


def _build_calibration_pack(
    manifest: Mapping[str, Any],
    feasibility_rows: Sequence[Mapping[str, Any]],
    turns_by_conversation: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    records_by_key = {str(row["conversation_key"]): row for row in feasibility_rows}
    selections = list(manifest.get("calibration_selection", []))
    conversation_keys = {str(selection["conversation_key"]) for selection in selections}
    target_count = sum(len(selection.get("targets", [])) for selection in selections)
    if len(conversation_keys) > 20 or target_count > 40:
        raise Phase1Error("calibration selection exceeds 20 conversations or 40 target prefixes")
    calibration_records: list[dict[str, Any]] = []
    for selection in selections:
        conversation_key = str(selection["conversation_key"])
        feasibility = records_by_key.get(conversation_key)
        if feasibility is None:
            raise Phase1Error(f"calibration conversation is absent from canonical union: {conversation_key}")
        if feasibility["reconstruction_grade"] != "A":
            raise Phase1Error(f"calibration conversation is not Grade A: {conversation_key}")
        if feasibility["prior_exposure_status"] != "exposed":
            raise Phase1Error(f"calibration conversation is not directly exposed: {conversation_key}")
        turns = turns_by_conversation[conversation_key]
        for target in selection.get("targets", []):
            target_turn_id = str(target["target_turn_id"])
            prefix = _ancestor_prefix(turns, target_turn_id)
            record = {
                "calibration_case_id": str(target["calibration_case_id"]),
                "conversation_key": conversation_key,
                "target_turn_id": str(prefix[-1]["turn_id"]),
                "target_post_id": str(prefix[-1]["post_id"]),
                "transcript_prefix": prefix,
                "source_grade": "A",
                "source_provenance": feasibility["source_provenance"],
                "prior_exposure_reasons": feasibility["prior_exposure_reasons"],
                "intended_schema_stressors": [
                    {
                        "stressor": str(stressor),
                        "status": "non_blind_non_test_development_stressor",
                    }
                    for stressor in target.get("intended_schema_stressors", [])
                ],
            }
            calibration_records.append(_record_with_hash(record))
    case_ids = [record["calibration_case_id"] for record in calibration_records]
    if len(case_ids) != len(set(case_ids)):
        raise Phase1Error("calibration case IDs must be unique")
    index = [
        {
            "calibration_case_id": record["calibration_case_id"],
            "conversation_key": record["conversation_key"],
            "target_turn_id": record["target_turn_id"],
            "target_post_id": record["target_post_id"],
            "source_grade": record["source_grade"],
            "prefix_turn_count": len(record["transcript_prefix"]),
            "prior_exposure_reason_count": len(record["prior_exposure_reasons"]),
            "intended_schema_stressors": [item["stressor"] for item in record["intended_schema_stressors"]],
            "record_sha256": record["record_sha256"],
        }
        for record in calibration_records
    ]
    records_payload = b"".join(canonical_json_bytes(record) + b"\n" for record in calibration_records)
    pack_manifest = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "pack_id": "proposition-ledger-phase1-exposed-calibration-v1",
        "conversation_count": len(conversation_keys),
        "target_prefix_count": len(calibration_records),
        "transcript_only": True,
        "contains_generated_ledgers": False,
        "contains_generated_replies": False,
        "all_cases_previously_exposed": True,
        "all_cases_grade_a": True,
        "records_sha256": sha256_bytes(records_payload),
        "index_sha256": sha256_bytes(canonical_json_bytes(index)),
        "pack_content_sha256": sha256_bytes(
            canonical_json_bytes(
                {
                    "record_hashes": [record["record_sha256"] for record in calibration_records],
                    "conversation_keys": sorted(conversation_keys),
                }
            )
        ),
    }
    return calibration_records, pack_manifest, index


def _measurement(measurement_id: str, direction: str, unit: str, source: str) -> dict[str, str]:
    return {
        "measurement_id": measurement_id,
        "direction": direction,
        "unit": unit,
        "adjudication_source": source,
    }


def _provider_schema_preflight(
    project_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the provider-neutral inventory and an honestly pending gate."""
    provider_schema = _import_research_tool("proposition_ledger_provider_schema")

    inventory = provider_schema.build_schema_feature_inventory(
        project_dir
        / "proposition_ledger_research/schema/proposition-ledger-semantic-delta-v1.schema.json"
    )
    compatibility = provider_schema.build_provider_schema_compatibility(inventory)
    errors = provider_schema.validate_provider_schema_compatibility(
        compatibility,
        feature_inventory=inventory,
    )
    if errors:
        raise Phase1Error(
            "invalid provider schema compatibility preflight: " + ",".join(errors)
        )
    return inventory, compatibility


def _build_protocol(
    source_manifest_sha256: str,
    exposure_registry_sha256: str,
    provider_schema_compatibility: Mapping[str, Any],
) -> dict[str, Any]:
    compatibility = copy.deepcopy(dict(provider_schema_compatibility))
    ledger_measurements = [
        _measurement("proposition_precision", "higher_better", "proportion", "combined"),
        _measurement("proposition_recall", "higher_better", "proportion", "combined"),
        _measurement("speaker_attribution_accuracy", "higher_better", "proportion", "combined"),
        _measurement("polarity_accuracy", "higher_better", "proportion", "combined"),
        _measurement("modality_accuracy", "higher_better", "proportion", "combined"),
        _measurement("issue_state_accuracy", "higher_better", "proportion", "combined"),
        _measurement("commitment_accuracy", "higher_better", "proportion", "combined"),
        _measurement("obligation_accuracy", "higher_better", "proportion", "combined"),
        _measurement("proposition_relation_accuracy", "higher_better", "proportion", "combined"),
        _measurement("invented_proposition_rate", "lower_better", "proportion", "combined"),
        _measurement("missed_live_proposition_rate", "lower_better", "proportion", "combined"),
        _measurement("compound_collapse_rate", "lower_better", "proportion", "combined"),
        _measurement("future_information_leakage_count", "lower_better", "count", "deterministic_validator"),
        _measurement("incremental_consistency", "higher_better", "proportion", "deterministic_validator"),
        _measurement("state_size_per_turn", "descriptive", "canonical_json_bytes", "deterministic_validator"),
    ]
    downstream_measurements = [
        _measurement("proposition_substitution_rate", "lower_better", "proportion", "blinded_human"),
        _measurement("missed_answer_rate", "lower_better", "proportion", "blinded_human"),
        _measurement("ignored_correction_rate", "lower_better", "proportion", "blinded_human"),
        _measurement("ignored_clarification_rate", "lower_better", "proportion", "blinded_human"),
        _measurement("unnecessary_reply_rate", "lower_better", "proportion", "blinded_human"),
        _measurement("direct_question_completion", "higher_better", "proportion", "blinded_human"),
        _measurement("conversational_progression_score", "higher_better", "ordinal_score", "blinded_human"),
        _measurement("factual_safety_regressions", "lower_better", "count", "combined"),
        _measurement("premise_authentication_regressions", "lower_better", "count", "combined"),
        _measurement("unsupported_claim_rate", "lower_better", "proportion", "combined"),
        _measurement("human_paired_preference", "higher_better", "paired_preference_share", "blinded_human"),
        _measurement("provider_calls", "lower_better", "count", "provider_telemetry"),
        _measurement("tokens", "lower_better", "tokens", "provider_telemetry"),
        _measurement("latency", "lower_better", "milliseconds", "provider_telemetry"),
        _measurement("cost", "lower_better", "minor_currency_units", "provider_telemetry"),
    ]
    return {
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "protocol_id": "proposition-ledger-phase2-four-arm-v1",
        "protocol_status": "draft",
        "phase1_execution_boundary": {
            "provider_calls_permitted": False,
            "model_outputs_permitted": False,
            "experimental_scoring_permitted": False,
            "held_out_selection_permitted": False,
        },
        "provider_response_schema": {
            "schema_version": SEMANTIC_DELTA_SCHEMA_VERSION,
            "schema_path": "proposition_ledger_research/schema/proposition-ledger-semantic-delta-v1.schema.json",
            "contract_role": "current_turn_semantic_analysis_only",
            "provider_emits_cumulative_state": False,
            "provider_emits_persistence_hashes": False,
            "provider_emits_state_patch": False,
            "provider_assigns_permanent_ids": False,
        },
        "provider_schema_compatibility": compatibility,
        "deterministic_materialiser": {
            "materialiser_id": MATERIALISER_ID,
            "implementation_path": "tools/proposition_ledger_semantic_delta.py",
            "contract_role": "semantic_delta_to_authoritative_persisted_ledger",
            "assigns_permanent_ids": True,
            "materialises_genesis": True,
            "registers_current_participant_on_first_seen": True,
            "provider_controls_participant_identity": False,
            "resolves_same_turn_local_references": True,
            "constructs_state_patch": True,
            "calculates_predecessor_and_ledger_hashes": True,
            "validates_persisted_ledger": True,
        },
        "persisted_ledger_schema": {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "schema_path": "proposition_ledger_research/schema/proposition-ledger-v1.schema.json",
            "contract_role": "authoritative_cumulative_persisted_state",
            "contains_cumulative_state": True,
            "contains_state_patch": True,
            "contains_persistence_hashes": True,
        },
        "research_questions": [
            "Can the ledger extract propositions, issue state, commitments, obligations, and relations accurately?",
            "Does a correct ledger improve downstream reasoning?",
            "Does a machine-generated ledger improve reasoning relative to transcript only?",
            "Does a machine-generated ledger outperform an equal-budget ordinary summary?",
            "Does a ledger reduce proposition substitution and ignored corrections?",
            "Does a ledger improve recognition of answers to the account's own questions?",
            "Does a ledger introduce unsupported inferences, false continuity, or needless replies?",
            "Which errors arise in ledger construction and which arise in the downstream consumer?",
        ],
        "corpus_policy": {
            "source_manifest_sha256": source_manifest_sha256,
            "exposure_registry_sha256": exposure_registry_sha256,
            "allowed_reconstruction_grades": ["A", "B"],
            "primary_analysis_reconstruction_grades": ["A"],
            "secondary_robustness_reconstruction_grades": ["B"],
            "grade_b_analysis_policy": "separate_secondary_robustness_never_pooled_with_or_promoted_to_primary",
            "split_unit": "whole_conversation",
            "author_grouping": "required_by_domain_qualified_within_family_identity_and_any_later_available_cross_family_identity",
            "previously_seen_policy": "development_or_calibration_only",
            "held_out_policy": "persistent_multiturn_targets_only; select only after protocol freeze without opening in Phase 1.2",
            "synthetic_policy": "schema_validation_only_never_historical_test_cases",
        },
        "target_prefix_policy": {
            "same_prefix_all_arms": True,
            "future_turns_forbidden": True,
            "prefix_ends_at_target": True,
            "complete_branch_required": True,
            "sibling_branch_policy": "include_visible_context_with_explicit_graph_refs",
        },
        "arms": [
            {
                "arm_id": "A",
                "label": "transcript only",
                "transcript_prefix": "exact_target_bounded_transcript",
                "additional_representation": "none",
                "future_information_allowed": False,
                "population": "full_frozen_evaluation_sample",
            },
            {
                "arm_id": "B",
                "label": "transcript plus equal-budget ordinary summary",
                "transcript_prefix": "exact_target_bounded_transcript",
                "additional_representation": "ordinary_summary",
                "future_information_allowed": False,
                "population": "full_frozen_evaluation_sample",
                "budget_rule": "additional summary tokens must be approximately equal to the Arm C machine-ledger tokens for that prefix under the frozen tolerance",
                "neutral_summary_policy": "neutral_conversation_summary_without_ledger_fields_outcome_labels_or_condition_cues",
                "equal_budget_control": {
                    "reference_arm_id": "C",
                    "budget_unit": "additional_representation_tokens_per_target_prefix",
                    "approximately_equal": True,
                    "tolerance_status": "pending_phase2_freeze",
                    "maximum_relative_difference": "pending_phase2_freeze",
                    "freeze_gate": "freeze_before_any_provider_call",
                },
            },
            {
                "arm_id": "C",
                "label": "transcript plus machine proposition ledger",
                "transcript_prefix": "exact_target_bounded_transcript",
                "additional_representation": "machine_proposition_ledger",
                "future_information_allowed": False,
                "population": "full_frozen_evaluation_sample",
                "budget_rule": "machine ledger is incremental, target-bounded, and measured before Arm B equal-budget material is created",
            },
            {
                "arm_id": "D",
                "label": "transcript plus transcript-first independently adjudicated proposition ledger",
                "transcript_prefix": "exact_target_bounded_transcript",
                "additional_representation": "transcript_first_independently_adjudicated_proposition_ledger",
                "future_information_allowed": False,
                "population": "smaller_adjudicated_subset",
                "budget_rule": "locked adjudicated ledger preserves the same schema and target boundary; size is reported rather than forced to match extraction errors",
                "gold_representation_source": "locked_transcript_first_independently_adjudicated_proposition_ledger",
            },
        ],
        "paired_comparisons": {
            "A_vs_B": {
                "comparison_id": "A_vs_B",
                "left_arm_id": "A",
                "right_arm_id": "B",
                "population": "full_frozen_evaluation_sample",
                "pairing_key": "conversation_key_and_target_turn_id",
                "paired_within_target_prefix": True,
                "analysis_role": "compression_control",
                "task_family_ids": ["continuity_reasoning", "reply_composition_quality"],
                "estimand": "effect of an equal-budget neutral compressed representation relative to transcript alone",
                "purpose": "control for the benefit of supplying a second compressed representation",
            },
            "A_vs_C": {
                "comparison_id": "A_vs_C",
                "left_arm_id": "A",
                "right_arm_id": "C",
                "population": "full_frozen_evaluation_sample",
                "pairing_key": "conversation_key_and_target_turn_id",
                "paired_within_target_prefix": True,
                "analysis_role": "primary",
                "task_family_ids": ["continuity_reasoning", "reply_composition_quality"],
                "estimand": "effect of the machine ledger relative to transcript alone",
                "purpose": "test whether a machine ledger changes downstream reasoning or composition",
            },
            "B_vs_C": {
                "comparison_id": "B_vs_C",
                "left_arm_id": "B",
                "right_arm_id": "C",
                "population": "full_frozen_evaluation_sample",
                "pairing_key": "conversation_key_and_target_turn_id",
                "paired_within_target_prefix": True,
                "analysis_role": "primary",
                "task_family_ids": ["continuity_reasoning", "reply_composition_quality"],
                "estimand": "effect of ledger structure beyond an equal-budget neutral summary",
                "purpose": "distinguish ledger-specific value from compression value",
            },
            "A_vs_D": {
                "comparison_id": "A_vs_D",
                "left_arm_id": "A",
                "right_arm_id": "D",
                "population": "smaller_adjudicated_subset",
                "pairing_key": "conversation_key_and_target_turn_id",
                "paired_within_target_prefix": True,
                "analysis_role": "primary",
                "task_family_ids": ["continuity_reasoning", "reply_composition_quality"],
                "estimand": "effect of a transcript-first independently adjudicated ledger relative to transcript alone",
                "purpose": "estimate the downstream value of a correct ledger",
            },
            "B_vs_D": {
                "comparison_id": "B_vs_D",
                "left_arm_id": "B",
                "right_arm_id": "D",
                "population": "smaller_adjudicated_subset",
                "pairing_key": "conversation_key_and_target_turn_id",
                "paired_within_target_prefix": True,
                "analysis_role": "secondary",
                "task_family_ids": ["continuity_reasoning", "reply_composition_quality"],
                "estimand": "effect of a transcript-first independently adjudicated ledger relative to an equal-budget neutral summary",
                "purpose": "separate correct ledger structure from ordinary compression",
            },
            "C_vs_D": {
                "comparison_id": "C_vs_D",
                "left_arm_id": "C",
                "right_arm_id": "D",
                "population": "smaller_adjudicated_subset",
                "pairing_key": "conversation_key_and_target_turn_id",
                "paired_within_target_prefix": True,
                "analysis_role": "extraction_diagnostic",
                "task_family_ids": ["ledger_construction_accuracy", "continuity_reasoning", "reply_composition_quality"],
                "estimand": "effect attributable to machine extraction error relative to a locked transcript-first independently adjudicated ledger",
                "purpose": "separate ledger-construction error from downstream-consumer error",
            },
        },
        "task_families": [
            {
                "task_id": "ledger_construction_accuracy",
                "label": "Ledger construction accuracy",
                "scored_separately": True,
                "inputs": ["exact transcript prefix"],
                "outputs": ["schema-valid semantic delta materialised into a validated persisted ledger", "abstention or typed validation failure"],
            },
            {
                "task_id": "continuity_reasoning",
                "label": "Reply/no-reply and continuity reasoning",
                "scored_separately": True,
                "inputs": ["condition-specific context"],
                "outputs": ["reply/no-reply decision", "answer target", "continuity rationale fields"],
            },
            {
                "task_id": "reply_composition_quality",
                "label": "Reply composition quality",
                "scored_separately": True,
                "inputs": ["condition-specific context", "frozen reply decision"],
                "outputs": ["candidate reply for private blinded evaluation only"],
            },
        ],
        "ledger_measurements": ledger_measurements,
        "downstream_measurements": downstream_measurements,
        "splitting_and_leakage_controls": {
            "whole_conversation_split": True,
            "principal_author_grouping": "domain_qualified_within_family_and_any_authoritative_cross_family_domain",
            "previously_labelled_excluded_from_test": True,
            "manually_reviewed_excluded_from_test": True,
            "final_test_opened_in_phase1": False,
            "prompt_tuning_on_test": False,
            "future_turn_checks": "deterministic_preflight_and_postflight",
            "exposure_registry_required": True,
        },
        "freeze_before_provider_calls": [
            "source_corpus_hashes",
            "exposure_registry_hash",
            "split_manifest",
            "model_profiles",
            "prompt_texts_and_hashes",
            "provider_response_schema",
            "deterministic_materialiser",
            "persisted_ledger_schema",
            "token_budgets",
            "arm_b_budget_tolerance",
            "randomisation",
            "adjudication_rubric",
            "success_thresholds",
            "failure_thresholds",
            "blinding_method",
            "unblinding_sequence",
        ],
        "randomisation": {
            "status": "pending_phase2_freeze",
            "unit": "target_prefix_with_conversation_blocking",
            "seed_commitment": "pending_phase2_freeze",
            "arm_order_balance": True,
            "replicate_policy": "freeze replicate count and aggregation before calls; never count generations as independent conversations",
        },
        "adjudication": {
            "rubric_status": "pending_phase2_freeze",
            "construction_input": "exact_transcript_prefix",
            "independent_raters": 2,
            "rater_annotations_independently_authored": True,
            "rater_hidden_information": [
                "machine_ledger",
                "other_rater_annotation",
                "production_pipeline_decision",
                "historical_account_reply",
                "arm_identity",
                "provider_or_model_identity",
            ],
            "annotation_dimensions": [
                "propositions",
                "compound_structure",
                "participant_commitments",
                "issue_state",
                "obligations",
                "proposition_relations",
                "answer_targets",
                "rejected_answer_targets",
                "uncertainty_and_abstentions",
            ],
            "disagreement_resolution": "blinded_adjudicator_resolves_from_transcript_and_two_independent_annotations",
            "adjudicator_input": "exact_transcript_prefix_and_two_independent_annotations",
            "adjudicator_hidden_information": [
                "machine_ledger",
                "production_pipeline_decision",
                "historical_account_reply",
                "arm_identity",
                "provider_or_model_identity",
            ],
            "adjudication_before_machine_comparison": True,
            "gold_lock": {
                "artifact": "transcript_first_independently_adjudicated_proposition_ledger",
                "hash_algorithm": "sha256",
                "locked_before_machine_ledger_reveal": True,
                "locked_before_machine_scoring": True,
            },
            "machine_reveal": {
                "permitted_only_after_gold_lock": True,
                "locked_gold_supplies_arm_d_representation": True,
            },
            "ledger_gold_boundary": "transcript_first_independently_adjudicated_subset_only",
            "arm_d_scope": "smaller_adjudicated_subset",
        },
        "success_thresholds": {
            "status": "pending_phase2_freeze",
            "must_freeze_before_calls": True,
            "criteria": ["pre-register primary comparisons and minimum effect/uncertainty requirements after a development-only power analysis"],
        },
        "failure_thresholds": {
            "status": "pending_phase2_freeze",
            "must_freeze_before_calls": True,
            "criteria": ["pre-register leakage, invention, factual-safety, premise-authentication, schema-failure, and cost stop rules"],
        },
        "blinding": {
            "condition_labels_hidden": True,
            "source_identity_hidden_where_safe": True,
            "rater_assignment_hidden": True,
            "unblinding_sequence": [
                "lock all ratings and adjudications",
                "verify hashes, exclusions, and cost ledger",
                "unblind arm labels",
                "unblind provider/model profiles only after arm analysis",
            ],
            "status": "pending_phase2_freeze",
        },
        "resource_measurement": ["provider_calls", "input_tokens", "output_tokens", "latency_ms", "cost_minor_units"],
        "execution_sequence": [
            "freeze source, exposure, split, prompt, schema, model, budget, randomisation, rubric, thresholds, and blinding artefacts",
            "two raters independently annotate exact transcript prefixes without machine ledgers, historical replies, production outcomes, arm identity, or provider identity",
            "a blinded adjudicator resolves the two annotations and locks and hashes transcript-first gold before any machine ledger is revealed",
            "construct semantic deltas, deterministically materialise persisted ledgers, and validate them without downstream outcome access",
            "reveal and score machine ledgers only after transcript-first gold is locked",
            "run continuity reasoning in paired blinded conditions",
            "run reply composition only where the frozen task design calls for a reply",
            "complete blinded downstream ratings and downstream-output adjudication",
            "verify resource and leakage ledgers before staged unblinding",
        ],
        "limitations": [
            "Phase 1.2 does not select or inspect the final held-out set.",
            "Model profiles, prompts, token tolerance, random seed, rubrics, and numeric thresholds remain to be frozen in Phase 2 before any provider call.",
            "No Phase 1.2 result estimates ledger effectiveness or supports production integration.",
        ],
    }


def _protocol_markdown(protocol: Mapping[str, Any]) -> list[str]:
    lines = [
        "# Proposition-ledger Phase 2 protocol draft",
        "",
        "This hash-bound draft defines the experiment design. It authorizes no provider call, model output, held-out selection, or experimental scoring in Phase 1.2.",
        "",
        "## Paired arms",
        "",
    ]
    for arm in protocol["arms"]:
        lines.append(f"- **Arm {arm['arm_id']} — {arm['label']}:** `{arm['additional_representation']}` with the same exact target-bounded transcript and no future information.")
    lines.extend(
        [
            "",
            "## Separately scored tasks",
            "",
            "1. ledger construction accuracy;",
            "2. reply/no-reply and continuity reasoning; and",
            "3. reply composition quality.",
            "",
            "Primary analyses use Grade A only. Grade B is a separate secondary robustness stratum and is never pooled with or promoted into the primary analysis.",
            "",
            "Arm B must be a neutral ordinary summary with no ledger fields, outcome labels, or condition cues. Its per-prefix additional token budget is paired to Arm C under a tolerance frozen before any provider call.",
            "",
            "All A/B/C comparisons are paired within the same conversation and target prefix; Arm D comparisons use the smaller adjudicated subset. Splits are by whole conversation and whole domain-qualified author group wherever an identity domain is available. Previously labelled or manually reviewed material remains development/calibration only. The final test set is neither selected nor opened in Phase 1.2.",
            "",
            "Arm D uses a transcript-first independently adjudicated proposition ledger. Two raters work independently from the exact transcript prefix while machine ledgers, historical replies, production decisions, arms, and provider identities remain hidden; a blinded adjudicator locks and hashes gold before any machine comparison or reveal.",
            "",
            "Provider output is only a current-turn semantic delta. Deterministic local code assigns permanent IDs, resolves references, constructs state patches, computes hashes, and validates the cumulative persisted ledger.",
            "",
            f"Provider structured-output compatibility is `{protocol['provider_schema_compatibility']['status']}`. No provider/model profile is selected; provider-specific local compilation or dry-validation must pass before any later provider call.",
            "",
            "The operational split, model profiles, prompts, response schemas, budgets, randomisation, rubric, numeric success/failure thresholds, blinding method, and unblinding sequence must all be frozen before paid calls.",
        ]
    )
    return lines


def _jsonl_payload(rows: Iterable[Mapping[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(dict(row)) + b"\n" for row in rows)


def _collect_raw_contributor_ids(manifest: Mapping[str, Any]) -> set[str]:
    values: set[str] = set()
    for source_id in ("historical_replay_266", "historical_shadow_union_293"):
        for row in _read_jsonl(_source_path(manifest, source_id)):
            author = row.get("author") or {}
            raw_id = author.get("id") if isinstance(author, dict) else None
            if raw_id not in (None, ""):
                values.add(str(raw_id))
    return values


def _collect_private_conversation_texts(manifest: Mapping[str, Any]) -> set[str]:
    texts: set[str] = set()
    conversation_kinds = {
        "benchmark_conversations",
        "audit_conversations",
        "prospective_conversations",
        "review_pack_conversations",
        "prospective_review_candidates",
        "review_pack_candidates",
        "historical_reply_targets",
        "qud_cases",
    }
    explicit_source_ids = {
        "multiturn_replay_cases",
        "structured_focus_replay_cases",
        "automatic_critic_packets",
        "writer_judge_packets",
        "recurrence_cases",
    }

    def collect(value: Any, key: str | None = None) -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                collect(child, str(child_key).lower())
        elif isinstance(value, list):
            for child in value:
                collect(child, key)
        elif isinstance(value, str) and key in PRIVATE_TEXT_KEYS and len(value.strip()) >= 32:
            texts.add(value)

    for entry in manifest.get("sources", []):
        if entry.get("structured_kind") not in conversation_kinds and entry.get("source_id") not in explicit_source_ids:
            continue
        path = Path(str(entry.get("path") or ""))
        if not path.is_file() or path.is_symlink():
            continue
        if path.suffix == ".jsonl":
            for row in _read_jsonl(path):
                collect(row)
        elif path.suffix == ".json":
            collect(_read_json(path))
    return texts


def _privacy_validation(
    project_dir: Path,
    private_output: Path,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    raw_ids = _collect_raw_contributor_ids(manifest)
    private_texts = _collect_private_conversation_texts(manifest)
    output_files = sorted(path for path in private_output.rglob("*") if path.is_file() and path.name != "private-author-key")
    tracked_research_files = sorted(
        [
            project_dir / "tools/build_proposition_ledger_phase1.py",
            project_dir / "tools/proposition_ledger_semantic_delta.py",
            project_dir / "tools/proposition_ledger_author_groups.py",
            project_dir / "tools/proposition_ledger_provider_schema.py",
        ]
        + list((project_dir / "proposition_ledger_research").rglob("*"))
        + [
            path
            for path in (
                project_dir / "tests/test_proposition_ledger_phase1.py",
                project_dir / "tests/test_proposition_ledger_semantic_delta.py",
                project_dir / "tests/test_proposition_ledger_author_groups.py",
                project_dir / "tests/test_proposition_ledger_provider_schema.py",
            )
            if path.exists()
        ]
    )
    scanned_files = [path for path in [*output_files, *tracked_research_files] if path.is_file()]
    tracked_files = [path for path in tracked_research_files if path.is_file()]
    forbidden_raw_id_fields: list[str] = []

    def find_forbidden_keys(value: Any, location: str, relative_path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_location = f"{location}.{key}"
                if str(key).lower() in RAW_ID_KEYS:
                    forbidden_raw_id_fields.append(f"{relative_path}:{child_location}")
                find_forbidden_keys(child, child_location, relative_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                find_forbidden_keys(child, f"{location}[{index}]", relative_path)

    for path in output_files:
        relative = str(path.relative_to(private_output))
        if path.suffix == ".json":
            find_forbidden_keys(_read_json(path), "$", relative)
        elif path.suffix == ".jsonl":
            for index, row in enumerate(_read_jsonl(path)):
                find_forbidden_keys(row, f"$[{index}]", relative)
    raw_matches = 0
    for path in scanned_files:
        data = path.read_bytes()
        for raw_id in raw_ids:
            encoded = raw_id.encode("utf-8")
            if len(encoded) >= 6 and encoded in data:
                raw_matches += 1
    private_text_matches = 0
    for path in tracked_files:
        data = path.read_bytes()
        for conversation_text in private_texts:
            raw_encoded = conversation_text.encode("utf-8")
            json_encoded = json.dumps(conversation_text, ensure_ascii=False)[1:-1].encode("utf-8")
            if raw_encoded in data or json_encoded in data:
                private_text_matches += 1
    private_conversation_files = [
        private_output / "calibration-pack/calibration-records.jsonl",
        private_output / "target-prefix-feasibility-index.jsonl",
        private_output / "target-prefix-structural-exclusions.jsonl",
    ]
    bad_file_modes = [
        {"path": str(path), "mode": oct(stat.S_IMODE(path.stat().st_mode))}
        for path in private_conversation_files
        if path.exists() and stat.S_IMODE(path.stat().st_mode) not in {0o600, 0o400}
    ]
    bad_directory_modes = [
        {"path": str(path), "mode": oct(stat.S_IMODE(path.stat().st_mode))}
        for path in [private_output, private_output / "calibration-pack"]
        if path.exists() and stat.S_IMODE(path.stat().st_mode) != 0o700
    ]
    key_path = private_output / "private-author-key"
    key_valid = key_path.is_file() and not key_path.is_symlink() and key_path.stat().st_size == 32 and stat.S_IMODE(key_path.stat().st_mode) == 0o600
    return {
        "raw_contributor_id_source_value_count_checked": len(raw_ids),
        "files_scanned": len(scanned_files),
        "raw_contributor_id_matches": raw_matches,
        "private_conversation_text_source_value_count_checked": len(private_texts),
        "private_conversation_text_minimum_length_checked": 32,
        "tracked_private_conversation_text_matches": private_text_matches,
        "forbidden_raw_contributor_id_field_locations": sorted(forbidden_raw_id_fields),
        "bad_conversation_file_modes": bad_file_modes,
        "bad_private_directory_modes": bad_directory_modes,
        "private_author_key_valid_and_unreported": key_valid,
        "private_author_key_included_in_hash_manifest": False,
        "passed": raw_matches == 0
        and private_text_matches == 0
        and not forbidden_raw_id_fields
        and not bad_file_modes
        and not bad_directory_modes
        and key_valid,
    }


def _schema_validation(
    project_dir: Path,
    protocol: Mapping[str, Any],
) -> dict[str, Any]:
    ledger_schema_path = project_dir / "proposition_ledger_research/schema/proposition-ledger-v1.schema.json"
    experiment_schema_path = project_dir / "proposition_ledger_research/schema/proposition-ledger-experiment-v1.schema.json"
    semantic_schema_path = project_dir / "proposition_ledger_research/schema/proposition-ledger-semantic-delta-v1.schema.json"
    ledger_schema = _read_json(ledger_schema_path)
    experiment_schema = _read_json(experiment_schema_path)
    semantic_schema = _read_json(semantic_schema_path)
    try:
        import jsonschema
    except ImportError as exc:  # pragma: no cover
        raise Phase1Error("jsonschema is required for Phase 1 validation") from exc
    meta_errors: list[str] = []
    validator_class = getattr(jsonschema, "Draft202012Validator", jsonschema.Draft7Validator)
    for name, schema in (
        ("ledger", ledger_schema),
        ("semantic_delta", semantic_schema),
        ("experiment", experiment_schema),
    ):
        try:
            effective_schema = schema if hasattr(jsonschema, "Draft202012Validator") else _draft7_compatible_schema(schema)
            validator_class.check_schema(effective_schema)
        except Exception as exc:  # jsonschema raises several schema subclasses
            meta_errors.append(f"{name}:{type(exc).__name__}:{exc}")
    fixture_validation = validate_synthetic_fixtures(project_dir)
    protocol_errors = _jsonschema_errors(protocol, experiment_schema)
    semantic_meta_errors = [
        error for error in meta_errors if error.startswith("semantic_delta:")
    ]
    semantic_validation = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "semantic_delta_schema_version": semantic_schema.get("properties", {})
        .get("schema_version", {})
        .get("const"),
        "semantic_delta_schema_sha256": sha256_file(semantic_schema_path),
        "meta_schema_errors": semantic_meta_errors,
        "forbidden_persistence_fields_absent_from_root": all(
            field not in semantic_schema.get("properties", {})
            for field in (
                "participants",
                "participant",
                "participant_records",
                "participant_id",
                "permanent_participant_id",
                "ledger_id",
                "ledger_sha256",
                "previous_ledger_sha256",
                "state_patch",
                "transition_id",
                "ledger_history",
                "turn_refs",
                "source_path",
                "source_paths",
                "cumulative_state",
                "cumulative_propositions",
                "cumulative_relations",
                "unchanged_state",
                "proposition_id",
                "relation_id",
                "permanent_proposition_id",
                "permanent_relation_id",
            )
        ),
    }
    semantic_validation["passed"] = (
        semantic_validation["semantic_delta_schema_version"]
        == SEMANTIC_DELTA_SCHEMA_VERSION
        and not semantic_meta_errors
        and semantic_validation["forbidden_persistence_fields_absent_from_root"]
    )
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "ledger_schema_sha256": sha256_file(ledger_schema_path),
        "semantic_delta_schema_sha256": sha256_file(semantic_schema_path),
        "experiment_schema_sha256": sha256_file(experiment_schema_path),
        "meta_schema_errors": meta_errors,
        "protocol_schema_errors": protocol_errors,
        "semantic_delta_schema_validation": semantic_validation,
        "synthetic_fixtures": fixture_validation,
        "passed": not meta_errors
        and not protocol_errors
        and semantic_validation["passed"]
        and fixture_validation["fixture_count"] == 12
        and fixture_validation["valid_fixture_count"] == 12
        and fixture_validation["all_invalid_examples_detected"],
    }


def _validation_file_sha256(project_dir: Path, relative: str) -> str:
    """Return a tracked validation-input hash without following a symlink."""
    path = project_dir / relative
    if not path.is_file() or path.is_symlink():
        return "unavailable"
    try:
        return sha256_file(path)
    except OSError:
        return "unavailable"


def _schema_validation_for_readiness(
    project_dir: Path, protocol: Mapping[str, Any]
) -> dict[str, Any]:
    """Convert a bounded schema-check exception into a failed gate record."""
    try:
        return _schema_validation(project_dir, protocol)
    except Exception as exc:
        error = f"{type(exc).__name__}:{exc}"
        semantic_hash = _validation_file_sha256(
            project_dir,
            "proposition_ledger_research/schema/proposition-ledger-semantic-delta-v1.schema.json",
        )
        return {
            "schema_version": OUTPUT_SCHEMA_VERSION,
            "ledger_schema_sha256": _validation_file_sha256(
                project_dir,
                "proposition_ledger_research/schema/proposition-ledger-v1.schema.json",
            ),
            "semantic_delta_schema_sha256": semantic_hash,
            "experiment_schema_sha256": _validation_file_sha256(
                project_dir,
                "proposition_ledger_research/schema/proposition-ledger-experiment-v1.schema.json",
            ),
            "meta_schema_errors": [error],
            "protocol_schema_errors": [],
            "semantic_delta_schema_validation": {
                "schema_version": OUTPUT_SCHEMA_VERSION,
                "semantic_delta_schema_version": None,
                "semantic_delta_schema_sha256": semantic_hash,
                "meta_schema_errors": [error],
                "forbidden_persistence_fields_absent_from_root": False,
                "passed": False,
            },
            "synthetic_fixtures": {
                "fixture_count": 0,
                "valid_fixture_count": 0,
                "invalid_example_count": 0,
                "all_invalid_examples_detected": False,
                "validation_error": error,
                "results": [],
            },
            "validation_error": error,
            "passed": False,
        }


def _semantic_materialiser_validation(project_dir: Path) -> dict[str, Any]:
    """Validate the pure materialiser statically and with synthetic behavior."""
    path = project_dir / "tools/proposition_ledger_semantic_delta.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots: set[str] = set()
    defined_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.partition(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined_names.add(node.name)
    forbidden_roots = {
        "mrsMThatcher2",
        "tested_reply_pipeline",
        "openai",
        "anthropic",
        "google",
        "xai",
        "tweepy",
        "requests",
        "urllib",
        "httpx",
        "socket",
    }
    required_statuses = {
        "semantic_delta_schema_invalid",
        "semantic_reference_invalid",
        "semantic_evidence_invalid",
        "semantic_transition_invalid",
        "materialisation_invariant_failure",
        "persisted_ledger_validation_failure",
    }
    project_import_root = str(project_dir.resolve())
    import_root_added = project_import_root not in sys.path
    implementation_materialiser_id = "unavailable"
    try:
        if import_root_added:
            sys.path.insert(0, project_import_root)
        semantic_delta = _import_research_tool(
            "proposition_ledger_semantic_delta"
        )

        implementation_materialiser_id = str(
            getattr(semantic_delta, "MATERIALISER_VERSION", "unavailable")
        )
        behavioral_validation = semantic_delta.behavioral_materialiser_validation(
            project_dir
        )
    except Exception as exc:  # pragma: no cover - bounded gate failure
        behavioral_validation = {
            "harness_error": f"unexpected:{type(exc).__name__}",
            "passed": False,
        }
    finally:
        if import_root_added:
            sys.path.remove(project_import_root)
    result = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "materialiser_id": MATERIALISER_ID,
        "implementation_materialiser_id": implementation_materialiser_id,
        "materialiser_identity_matches": (
            implementation_materialiser_id == MATERIALISER_ID
            and behavioral_validation.get("materialiser_version")
            == MATERIALISER_ID
        ),
        "implementation_path": "tools/proposition_ledger_semantic_delta.py",
        "source_sha256": sha256_file(path),
        "forbidden_import_roots_found": sorted(imported_roots & forbidden_roots),
        "materialise_entrypoint_present": "materialise_semantic_delta" in defined_names,
        "incremental_full_ledger_validator_hook_present": "validate_ledger_incremental" in source,
        "typed_failure_statuses_present": sorted(
            status for status in required_statuses if status in source
        ),
        "provider_invocation_code_present": False,
        "behavioral_validation": behavioral_validation,
        "behavioral_validation_passed": behavioral_validation.get("passed") is True,
        "genesis_materialisation_valid": behavioral_validation.get(
            "genesis_materialisation_valid"
        )
        is True,
        "first_seen_participant_registration_valid": behavioral_validation.get(
            "first_seen_participant_registration_valid"
        )
        is True,
        "complete_incremental_chain_valid": behavioral_validation.get(
            "complete_incremental_chain_valid"
        )
        is True,
    }
    result["passed"] = (
        not result["forbidden_import_roots_found"]
        and result["materialise_entrypoint_present"]
        and result["incremental_full_ledger_validator_hook_present"]
        and result["materialiser_identity_matches"]
        and set(result["typed_failure_statuses_present"]) == required_statuses
        and result["behavioral_validation_passed"]
        and result["genesis_materialisation_valid"]
        and result["first_seen_participant_registration_valid"]
        and result["complete_incremental_chain_valid"]
    )
    return result


def _semantic_materialiser_validation_for_readiness(
    project_dir: Path,
) -> dict[str, Any]:
    """Convert a bounded materialiser-check exception into a failed gate record."""
    try:
        return _semantic_materialiser_validation(project_dir)
    except Exception as exc:
        return {
            "schema_version": OUTPUT_SCHEMA_VERSION,
            "materialiser_id": MATERIALISER_ID,
            "implementation_materialiser_id": "unavailable",
            "materialiser_identity_matches": False,
            "implementation_path": "tools/proposition_ledger_semantic_delta.py",
            "source_sha256": _validation_file_sha256(
                project_dir, "tools/proposition_ledger_semantic_delta.py"
            ),
            "forbidden_import_roots_found": [],
            "materialise_entrypoint_present": False,
            "incremental_full_ledger_validator_hook_present": False,
            "typed_failure_statuses_present": [],
            "provider_invocation_code_present": False,
            "genesis_materialisation_valid": False,
            "first_seen_participant_registration_valid": False,
            "complete_incremental_chain_valid": False,
            "validation_error": f"{type(exc).__name__}:{exc}",
            "passed": False,
        }


def _validate_row_hashes(rows: Sequence[Mapping[str, Any]], label: str) -> list[str]:
    errors: list[str] = []
    for index, row in enumerate(rows):
        stored = row.get("row_sha256")
        material = copy.deepcopy(dict(row))
        material.pop("row_sha256", None)
        expected = sha256_bytes(canonical_json_bytes(material))
        if stored != expected:
            errors.append(f"{label}:{index}:row_sha256")
    return errors


def _validate_record_hashes(rows: Sequence[Mapping[str, Any]], label: str) -> list[str]:
    errors: list[str] = []
    for index, row in enumerate(rows):
        stored = row.get("record_sha256")
        material = copy.deepcopy(dict(row))
        material.pop("record_sha256", None)
        expected = sha256_bytes(canonical_json_bytes(material))
        if stored != expected:
            errors.append(f"{label}:{index}:record_sha256")
    return errors


def _validate_sha256s(private_output: Path) -> list[str]:
    manifest_path = private_output / "SHA256SUMS"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        return ["SHA256SUMS:missing_or_symlink"]
    errors: list[str] = []
    declared: set[str] = set()
    with manifest_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.endswith("\n") or "  " not in line:
                errors.append(f"SHA256SUMS:{line_number}:format")
                continue
            digest, relative = line[:-1].split("  ", 1)
            relative_path = Path(relative)
            if (
                len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
                or relative_path.is_absolute()
                or ".." in relative_path.parts
                or relative in declared
                or relative in {"private-author-key", "SHA256SUMS"}
            ):
                errors.append(f"SHA256SUMS:{line_number}:entry")
                continue
            declared.add(relative)
            target = private_output / relative_path
            if not target.is_file() or target.is_symlink():
                errors.append(f"SHA256SUMS:{line_number}:missing_or_symlink")
            elif sha256_file(target) != digest:
                errors.append(f"SHA256SUMS:{line_number}:digest")
    expected = {
        str(path.relative_to(private_output))
        for path in private_output.rglob("*")
        if path.is_file() and path.name not in {"private-author-key", "SHA256SUMS"}
    }
    if declared != expected:
        errors.append("SHA256SUMS:coverage")
    return errors


def _validate_outputs(
    private_output: Path,
    project_dir: Path,
    expected_source_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    required = [
        "run-manifest.json",
        "frozen-source-manifest.json",
        "source-inventory.json",
        "source-inventory.md",
        "source-overlap.json",
        "source-overlap.md",
        "conversation-feasibility-index.jsonl",
        "corpus-feasibility.json",
        "corpus-feasibility.md",
        "prior-exposure-registry.jsonl",
        "prior-exposure-summary.json",
        "author-binding-audit.json",
        "target-prefix-feasibility-index.jsonl",
        "target-prefix-structural-exclusions.jsonl",
        "target-prefix-crosstab.json",
        "target-prefix-crosstab.md",
        "provider-schema-feature-inventory.json",
        "calibration-pack/calibration-records.jsonl",
        "calibration-pack/calibration-index.json",
        "calibration-pack/manifest.json",
        "experiment-protocol-draft.json",
        "experiment-protocol-draft.md",
        "schema-validation.json",
        "semantic-delta-schema-validation.json",
        "semantic-materialiser-validation.json",
        "readiness-gates.json",
        "phase1.2-report.md",
        "SHA256SUMS",
    ]
    missing = [relative for relative in required if not (private_output / relative).is_file()]
    parse_errors: list[str] = []
    for path in sorted(private_output.rglob("*.json")):
        try:
            _read_json(path)
        except Exception as exc:
            parse_errors.append(f"{path.relative_to(private_output)}:{type(exc).__name__}")
    for path in sorted(private_output.rglob("*.jsonl")):
        try:
            _read_jsonl(path)
        except Exception as exc:
            parse_errors.append(f"{path.relative_to(private_output)}:{type(exc).__name__}")
    feasibility_rows = _read_jsonl(private_output / "conversation-feasibility-index.jsonl") if not missing else []
    exposure_rows = _read_jsonl(private_output / "prior-exposure-registry.jsonl") if not missing else []
    target_rows = _read_jsonl(private_output / "target-prefix-feasibility-index.jsonl") if not missing else []
    target_structural_exclusions = _read_jsonl(
        private_output / "target-prefix-structural-exclusions.jsonl"
    ) if not missing else []
    calibration_rows = _read_jsonl(private_output / "calibration-pack/calibration-records.jsonl") if not missing else []
    row_hash_errors = [
        *_validate_row_hashes(feasibility_rows, "feasibility"),
        *_validate_row_hashes(exposure_rows, "exposure"),
        *_validate_row_hashes(target_rows, "target_prefix"),
        *_validate_row_hashes(
            target_structural_exclusions, "target_structural_exclusion"
        ),
        *_validate_record_hashes(calibration_rows, "calibration"),
    ]
    conversation_keys = [row.get("conversation_key") for row in feasibility_rows]
    duplicate_conversation_keys = len(conversation_keys) != len(set(conversation_keys))
    grade_a_errors = [
        row["conversation_key"]
        for row in feasibility_rows
        if row.get("reconstruction_grade") == "A"
        and not all(
            row.get(field) is True
            for field in (
                "exact_text_complete",
                "parent_graph_complete",
                "account_publication_confirmed",
                "chronology_complete",
                "immutable_post_identity_complete",
                "role_assignment_complete",
                "root_identity_complete",
                "turn_order_unambiguous",
                "complete_prefix_through_targets",
            )
        )
    ]
    grade_b_errors = [
        row["conversation_key"]
        for row in feasibility_rows
        if row.get("reconstruction_grade") == "B"
        and (
            not all(
                row.get(field) is True
                for field in (
                    "exact_text_complete",
                    "parent_graph_complete",
                    "account_publication_confirmed",
                    "immutable_post_identity_complete",
                    "role_assignment_complete",
                    "complete_prefix_through_targets",
                )
            )
            or len(row.get("secondary_quality_limitations", [])) != 1
            or (
                row.get("turn_order_unambiguous") is not True
                and row.get("secondary_quality_limitations")
                != ["one_limited_turn_order_gap"]
            )
        )
    ]
    exposure_reason_errors = [
        row.get("exposure_key")
        for row in exposure_rows
        if set(row.get("exposure_statuses", [])) & EXPOSED_STATUSES and not row.get("exposure_reasons")
    ]
    future_prefix_errors: list[str] = []
    for row in calibration_rows:
        prefix = row.get("transcript_prefix", [])
        if not prefix or prefix[-1].get("turn_id") != row.get("target_turn_id"):
            future_prefix_errors.append(str(row.get("calibration_case_id")))
        if [turn.get("turn_index") for turn in prefix] != list(range(len(prefix))):
            future_prefix_errors.append(str(row.get("calibration_case_id")))
    schema_validation = _read_json(private_output / "schema-validation.json") if not missing else {"passed": False}
    sha256sum_errors = _validate_sha256s(private_output) if not missing else []
    schema_hash_errors: list[str] = []
    if not missing:
        for field, relative in (
            ("ledger_schema_sha256", "proposition_ledger_research/schema/proposition-ledger-v1.schema.json"),
            ("semantic_delta_schema_sha256", "proposition_ledger_research/schema/proposition-ledger-semantic-delta-v1.schema.json"),
            ("experiment_schema_sha256", "proposition_ledger_research/schema/proposition-ledger-experiment-v1.schema.json"),
        ):
            if schema_validation.get(field) != sha256_file(project_dir / relative):
                schema_hash_errors.append(field)
    provider_preflight_errors: list[str] = []
    if not missing:
        provider_schema = _import_research_tool(
            "proposition_ledger_provider_schema"
        )

        provider_inventory = _read_json(
            private_output / "provider-schema-feature-inventory.json"
        )
        expected_provider_inventory = provider_schema.build_schema_feature_inventory(
            project_dir
            / "proposition_ledger_research/schema/proposition-ledger-semantic-delta-v1.schema.json"
        )
        if canonical_json_bytes(provider_inventory) != canonical_json_bytes(
            expected_provider_inventory
        ):
            provider_preflight_errors.append("feature_inventory_not_reproducible")
        protocol_document = _read_json(
            private_output / "experiment-protocol-draft.json"
        )
        compatibility = protocol_document.get("provider_schema_compatibility", {})
        compatibility_errors = provider_schema.validate_provider_schema_compatibility(
            compatibility,
            feature_inventory=provider_inventory,
        )
        provider_preflight_errors.extend(
            f"compatibility:{error}" for error in compatibility_errors
        )
        if compatibility.get("status") != "pending_model_profile_selection":
            provider_preflight_errors.append("compatibility_status_not_pending")
        if compatibility.get("network_call_made") is not False:
            provider_preflight_errors.append("provider_network_call_recorded")
    source_manifest_binding_errors: list[str] = []
    if not missing and expected_source_manifest_sha256 is not None:
        run_manifest = _read_json(private_output / "run-manifest.json")
        protocol = _read_json(private_output / "experiment-protocol-draft.json")
        if run_manifest.get("source_manifest_sha256") != expected_source_manifest_sha256:
            source_manifest_binding_errors.append("run_manifest")
        if sha256_file(private_output / "frozen-source-manifest.json") != expected_source_manifest_sha256:
            source_manifest_binding_errors.append("frozen_source_manifest_copy")
        if run_manifest.get("schema_version") != OUTPUT_SCHEMA_VERSION:
            source_manifest_binding_errors.append("run_manifest_schema_version")
        if run_manifest.get("phase1_1_base_sha") != PHASE1_1_BASE_SHA:
            source_manifest_binding_errors.append("phase1_1_base_sha")
        if protocol.get("corpus_policy", {}).get("source_manifest_sha256") != expected_source_manifest_sha256:
            source_manifest_binding_errors.append("experiment_protocol")
        exposure_hash = sha256_file(private_output / "prior-exposure-registry.jsonl")
        if protocol.get("corpus_policy", {}).get("exposure_registry_sha256") != exposure_hash:
            source_manifest_binding_errors.append("exposure_registry")
    target_prefix_count_errors: list[str] = []
    if not missing:
        feasibility_summary = _read_json(private_output / "corpus-feasibility.json")
        target_crosstab = _read_json(private_output / "target-prefix-crosstab.json")
        target_prefix_count_errors.extend(
            _target_prefix_crosstab_errors(target_rows, target_crosstab)
        )
        try:
            expected_target_pairs = _declared_target_pairs(feasibility_rows)
        except Phase1Error:
            target_prefix_count_errors.append("invalid_declared_target_universe")
        else:
            target_prefix_count_errors.extend(
                _target_structural_reconciliation_errors(
                    expected_target_pairs,
                    target_rows,
                    target_structural_exclusions,
                    feasibility_summary.get("target_structural_reconciliation", {}),
                )
            )
        expected_counts = feasibility_summary.get("target_prefix_counts", {})
        if len(target_rows) != expected_counts.get("total_usable_target_prefixes"):
            target_prefix_count_errors.append("total_usable_target_prefixes")
        for outcome in OUTCOME_EVIDENCE_CLASSES:
            actual = sum(row.get("outcome_evidence_class") == outcome for row in target_rows)
            if actual != expected_counts.get(f"{outcome}_target_prefixes"):
                target_prefix_count_errors.append(f"outcome_count:{outcome}")
            row_pairs = {
                (str(row.get("conversation_key") or ""), str(row.get("target_turn_id") or ""))
                for row in target_rows
                if row.get("outcome_evidence_class") == outcome
            }
            field = OUTCOME_TARGET_ID_FIELDS[outcome]
            declared_pairs = {
                (str(record.get("conversation_key") or ""), str(target_turn_id or ""))
                for record in feasibility_rows
                for target_turn_id in record.get(field, [])
            }
            if row_pairs != declared_pairs:
                target_prefix_count_errors.append(
                    f"conversation_outcome_partition:{outcome}"
                )
        target_pairs = [
            (row.get("conversation_key"), row.get("target_turn_id"))
            for row in target_rows
        ]
        if len(target_pairs) != len(set(target_pairs)):
            target_prefix_count_errors.append("duplicate_conversation_target_pair")
        author_groups = _import_research_tool("proposition_ledger_author_groups")

        exposure_summary = _read_json(
            private_output / "prior-exposure-summary.json"
        )
        within_group_rows = [
            row
            for row in exposure_rows
            if row.get("entity_type") == "within_family_author_group"
        ]
        contributor_observation_rows = [
            row
            for row in exposure_rows
            if row.get("entity_type") == "conversation_contributor"
        ]
        target_prefix_count_errors.extend(
            author_groups.author_group_crosstab_errors(
                target_rows,
                feasibility_rows,
                within_group_rows,
                [],
                exposure_summary.get("author_group_crosstab", {}),
                contributor_observation_rows,
            )
        )
        author_binding_audit = _read_json(
            private_output / "author-binding-audit.json"
        )
        if canonical_json_bytes(author_binding_audit) != canonical_json_bytes(
            exposure_summary.get("author_binding_audit", {})
        ):
            target_prefix_count_errors.append("author_binding_audit_mismatch")
        if author_binding_audit.get("schema_version") != (
            "proposition-ledger-author-binding-audit-v1"
        ):
            target_prefix_count_errors.append("author_binding_audit_schema")
        if author_binding_audit.get("canonical_conversation_count") != len(
            feasibility_rows
        ):
            target_prefix_count_errors.append(
                "author_binding_audit_conversation_count"
            )
        if author_binding_audit.get(
            "contributor_exposure_observation_count"
        ) != len(contributor_observation_rows):
            target_prefix_count_errors.append(
                "author_binding_audit_contributor_observation_count"
            )
        if author_binding_audit.get(
            "full_crosstab_reconciliation_status"
        ) != "passed":
            target_prefix_count_errors.append(
                "author_binding_audit_crosstab_reconciliation"
            )
        if author_binding_audit.get(
            "privacy_raw_identity_field_scan_status"
        ) != "passed":
            target_prefix_count_errors.append(
                "author_binding_audit_privacy_scan"
            )
        if any(
            row.get("author_group_split_assignment") is not None
            or row.get("cross_family_author_group_split_assignment") is not None
            for row in [
                *target_rows,
                *feasibility_rows,
                *contributor_observation_rows,
            ]
        ):
            target_prefix_count_errors.append("author_group_split_assigned")
    readiness_errors: list[str] = []
    if not missing:
        readiness = _read_json(private_output / "readiness-gates.json")
        if readiness.get("disposition") != _derive_disposition(readiness):
            readiness_errors.append("disposition_not_derived_from_gates")
        semantic_validation_output = _read_json(
            private_output / "semantic-delta-schema-validation.json"
        )
        if semantic_validation_output.get("passed") is not True:
            readiness_errors.append("semantic_delta_schema_validation")
        if canonical_json_bytes(semantic_validation_output) != canonical_json_bytes(
            schema_validation.get("semantic_delta_schema_validation", {})
        ):
            readiness_errors.append("semantic_delta_schema_validation_binding")
        if _read_json(private_output / "semantic-materialiser-validation.json").get("passed") is not True:
            readiness_errors.append("semantic_materialiser_validation")
        materialiser_output = _read_json(
            private_output / "semantic-materialiser-validation.json"
        )
        for field in (
            "genesis_materialisation_valid",
            "first_seen_participant_registration_valid",
            "complete_incremental_chain_valid",
        ):
            if materialiser_output.get(field) is not True:
                readiness_errors.append(field)
            if readiness.get(field, {}).get("status") != "passed":
                readiness_errors.append(f"readiness:{field}")
        protocol_document = _read_json(
            private_output / "experiment-protocol-draft.json"
        )
        compatibility_status = protocol_document.get(
            "provider_schema_compatibility", {}
        ).get("status")
        if compatibility_status != "pending_model_profile_selection":
            readiness_errors.append("provider_schema_compatibility_status")
        if (
            readiness.get("provider_schema_compatibility_status", {}).get(
                "status"
            )
            != compatibility_status
        ):
            readiness_errors.append(
                "provider_schema_compatibility_readiness_binding"
            )
    return {
        "missing_required_outputs": missing,
        "strict_json_parse_errors": parse_errors,
        "row_hash_errors": row_hash_errors,
        "duplicate_conversation_keys": duplicate_conversation_keys,
        "grade_a_invariant_errors": grade_a_errors,
        "grade_b_invariant_errors": grade_b_errors,
        "exposed_records_without_reasons": exposure_reason_errors,
        "calibration_future_prefix_errors": sorted(set(future_prefix_errors)),
        "sha256sum_errors": sha256sum_errors,
        "schema_hash_errors": schema_hash_errors,
        "provider_schema_preflight_errors": provider_preflight_errors,
        "source_manifest_binding_errors": source_manifest_binding_errors,
        "target_prefix_count_errors": target_prefix_count_errors,
        "readiness_errors": readiness_errors,
        "schema_validation_passed": schema_validation.get("passed") is True,
        "passed": not any(
            (
                missing,
                parse_errors,
                row_hash_errors,
                duplicate_conversation_keys,
                grade_a_errors,
                grade_b_errors,
                exposure_reason_errors,
                future_prefix_errors,
                sha256sum_errors,
                schema_hash_errors,
                provider_preflight_errors,
                source_manifest_binding_errors,
                target_prefix_count_errors,
                readiness_errors,
                schema_validation.get("passed") is not True,
            )
        ),
    }


def _phase1_2_metric_comparison(
    feasibility: Mapping[str, Any],
    target_crosstab: Mapping[str, Any],
) -> list[tuple[str, str, str]]:
    """Return the requested Phase 1.1 baseline versus Phase 1.2 metrics."""
    grades = feasibility["grade_counts"]
    h = target_crosstab["headline_counts"]
    outcomes = target_crosstab["dimensions"]["outcome_evidence_class"]
    author_crosstab = feasibility["author_grouping"]["crosstab"]
    author_headline = author_crosstab["headline_counts"]
    not_measured = "not measured in Phase 1.1"
    return [
        (
            "Canonical conversation count",
            "155",
            str(feasibility["canonical_union_conversation_count"]),
        ),
        ("Grade A conversation count", "144", str(grades["A"])),
        ("Grade B conversation count", "0", str(grades["B"])),
        ("Grade C conversation count", "11", str(grades["C"])),
        (
            "Total structurally usable target-prefix count",
            "219",
            str(target_crosstab["target_prefix_count"]),
        ),
        (
            "Confirmed published-reply target-prefix count",
            "188",
            str(outcomes.get("confirmed_published_reply", 0)),
        ),
        (
            "Confirmed pipeline-terminal no-reply target-prefix count",
            "30",
            str(outcomes.get("confirmed_pipeline_terminal_no_reply", 0)),
        ),
        (
            "Confirmed local-skip target-prefix count",
            "1",
            str(outcomes.get("confirmed_local_skip", 0)),
        ),
        (
            "Quiescent unreplied-tip outcome-unknown target-prefix count",
            "0",
            str(outcomes.get("quiescent_unreplied_tip_outcome_unknown", 0)),
        ),
        (
            "Outcome-evidence-unavailable target-prefix count",
            "0",
            str(outcomes.get("outcome_evidence_unavailable", 0)),
        ),
        (
            "Conflicting-outcome-evidence target-prefix count",
            "0",
            str(outcomes.get("conflicting_outcome_evidence", 0)),
        ),
        (
            "Initial user target count",
            "not distinguished within 62 bundled first-response controls",
            str(h["initial_user_target_count"]),
        ),
        (
            "Pre-account user follow-up count",
            not_measured,
            str(h["pre_account_user_follow_up_count"]),
        ),
        (
            "Account-root response count",
            "not distinguished within 156 overbroad multi-turn labels",
            str(h["account_root_response_count"]),
        ),
        (
            "Persistent multi-turn target count",
            "156 overbroad multi-turn labels",
            str(h["persistent_multiturn_target_count"]),
        ),
        (
            "Other sequence count",
            "1 residual old response-type row",
            str(h["other_sequence_count"]),
        ),
        (
            "Exposed persistent target-prefix count",
            not_measured,
            str(h["exposed_persistent_multiturn_target_prefix_count"]),
        ),
        (
            "Structurally mined-only persistent target-prefix count",
            not_measured,
            str(
                h[
                    "structurally_mined_only_persistent_multiturn_target_prefix_count"
                ]
            ),
        ),
        (
            "Genuinely unexposed stable Grade-A persistent target-prefix count",
            not_measured,
            str(
                h[
                    "genuinely_unexposed_stable_grade_a_persistent_multiturn_target_prefix_count"
                ]
            ),
        ),
        (
            "Within-family group-clean persistent target-prefix count",
            not_measured,
            str(h["within_family_group_clean_persistent_target_prefix_count"]),
        ),
        (
            "Cross-family-clean persistent target-prefix count",
            not_measured,
            str(h["cross_family_clean_persistent_target_prefix_count"]),
        ),
        (
            "Preliminarily eligible conversation count",
            "2",
            str(h["preliminary_within_family_held_out_eligible_conversation_count"]),
        ),
        (
            "Preliminarily eligible target-prefix count",
            "5",
            str(h["preliminary_within_family_held_out_eligible_target_prefix_count"]),
        ),
        (
            "Comparable within-family author-group count",
            not_measured,
            str(author_crosstab["comparable_within_family_author_group_count"]),
        ),
        (
            "Cross-family identities available (conversations)",
            not_measured,
            str(author_headline["cross_family_identity_available_conversation_count"]),
        ),
        (
            "Cross-family identities unavailable (conversations)",
            not_measured,
            str(author_headline["cross_family_identity_unavailable_conversation_count"]),
        ),
        (
            "Author groups containing direct exposure",
            not_measured,
            str(author_headline["within_family_groups_containing_direct_exposure"]),
        ),
        (
            "Author groups requiring groupwise split",
            not_measured,
            str(author_headline["within_family_groups_requiring_groupwise_split"]),
        ),
    ]


def _phase1_2_report(
    inventory: Mapping[str, Any],
    feasibility: Mapping[str, Any],
    target_crosstab: Mapping[str, Any],
    protocol_sha256: str,
    schema_validation: Mapping[str, Any],
    materialiser_validation: Mapping[str, Any],
    provider_inventory: Mapping[str, Any],
    readiness: Mapping[str, Any],
    privacy: Mapping[str, Any],
) -> list[str]:
    grades = feasibility["grade_counts"]
    h = target_crosstab["headline_counts"]
    outcomes = target_crosstab["dimensions"]["outcome_evidence_class"]
    author_crosstab = feasibility["author_grouping"]["crosstab"]
    author_headline = author_crosstab["headline_counts"]
    metric_comparison = _phase1_2_metric_comparison(feasibility, target_crosstab)
    inventory_sha256 = sha256_bytes(canonical_json_bytes(provider_inventory))
    lines = [
        "# Proposition-ledger Phase 1.2 execution-preflight amendment report",
        "",
        "## Disposition",
        "",
        f"**{readiness['disposition']}**",
        "",
        "Phase 1.2 fixes four execution-preflight defects only. It does not establish that a proposition ledger improves reasoning, authorise a provider call, select a held-out set, or begin Phase 2.",
        "",
        "## Frozen corpus and target outcomes",
        "",
        f"The inventory revalidated **{inventory['source_count']}** exact frozen source artefacts. The canonical union remains **{feasibility['canonical_union_conversation_count']}** conversations: Grade A **{grades['A']}**, Grade B **{grades['B']}**, Grade C **{grades['C']}**.",
        "",
        "The Phase 1.1 outcome-evidence taxonomy is preserved unchanged. Phase 1.2 changes sequence, identity-group, genesis, and provider-compatibility preflight only.",
        "",
        "## Old-versus-new metric comparison",
        "",
        "| Metric | Phase 1.1 | Phase 1.2 |",
        "| --- | ---: | ---: |",
        *(
            f"| {metric} | {phase1_1_value} | {phase1_2_value} |"
            for metric, phase1_1_value, phase1_2_value in metric_comparison
        ),
        "",
        f"Phase 1.2 structurally usable target prefixes: **{target_crosstab['target_prefix_count']}**; Grade A: **{h['grade_a_target_prefix_count']}**. Outcome counts: "
        + "; ".join(f"{name}=**{outcomes.get(name, 0)}**" for name in OUTCOME_EVIDENCE_CLASSES)
        + ".",
        "",
        "Silence alone is never classified as a confirmed no-reply. Confirmed pipeline no-reply and local-skip classes require exact structured records bound to the target; a quiescent unreplied tip without such evidence remains outcome-unknown.",
        "",
        "## Exact target-sequence strata",
        "",
        f"Initial user targets **{h['initial_user_target_count']}**; pre-account user follow-ups **{h['pre_account_user_follow_up_count']}**; account-root responses **{h['account_root_response_count']}**; persistent multi-turn targets **{h['persistent_multiturn_target_count']}**; other sequences **{h['other_sequence_count']}**. Only a parent-linked published/observed account reply to an earlier user turn creates persistent multi-turn evidence.",
        "",
        f"Exposed persistent prefixes **{h['exposed_persistent_multiturn_target_prefix_count']}**; structurally mined-only persistent prefixes **{h['structurally_mined_only_persistent_multiturn_target_prefix_count']}**; genuinely unexposed stable Grade-A persistent prefixes **{h['genuinely_unexposed_stable_grade_a_persistent_multiturn_target_prefix_count']}**.",
        "",
        f"Within-family preliminary eligibility remains for **{h['preliminary_within_family_held_out_eligible_target_prefix_count']}** prefixes across **{h['preliminary_within_family_held_out_eligible_conversation_count']}** conversations. Cross-family-clean eligibility is **{h['preliminary_cross_family_clean_held_out_eligible_target_prefix_count']}**. These are eligibility counts, not a split or final selection.",
        "",
        f"Comparable within-family groups across the canonical conversations **{author_crosstab['comparable_within_family_author_group_count']}**; groups containing direct exposure **{author_headline['within_family_groups_containing_direct_exposure']}**; groups requiring a later whole-group split **{author_headline['within_family_groups_requiring_groupwise_split']}**. The usable target rows cover **{h['comparable_within_family_author_group_count']}** of those groups. No group was split in Phase 1.2.",
        "",
        f"Benchmark and prospective-v4 pseudonyms remain incompatible. Cross-family identity is available for **{author_headline['cross_family_identity_available_conversation_count']}** conversations and unavailable for **{author_headline['cross_family_identity_unavailable_conversation_count']}**. Authoritative raw identity is not available in both frozen families, so no common key is fabricated and the claim remains conversation-level and within-family grouped with cross-family overlap unresolvable.",
        "",
        "## Semantic and gold boundaries",
        "",
        f"Provider response schema: `{SEMANTIC_DELTA_SCHEMA_VERSION}`, SHA-256 `{schema_validation['semantic_delta_schema_sha256']}`. Persisted ledger schema: `{LEDGER_SCHEMA_VERSION}`, SHA-256 `{schema_validation['ledger_schema_sha256']}`. Experiment protocol: `{EXPERIMENT_SCHEMA_VERSION}`, document SHA-256 `{protocol_sha256}`, schema SHA-256 `{schema_validation['experiment_schema_sha256']}`.",
        "",
        f"Deterministic materialiser `{MATERIALISER_ID}` passed: **{materialiser_validation['passed']}**; source SHA-256 `{materialiser_validation['source_sha256']}`. Genesis **{materialiser_validation['genesis_materialisation_valid']}**; first-seen participant registration **{materialiser_validation['first_seen_participant_registration_valid']}**; complete incremental chain **{materialiser_validation['complete_incremental_chain_valid']}**. Persisted schema remains `{LEDGER_SCHEMA_VERSION}` because its authoritative state patch already represents participant additions.",
        "",
        f"Provider-schema feature inventory SHA-256 `{inventory_sha256}`; canonical schema SHA-256 `{provider_inventory['schema_sha256']}`; size **{provider_inventory['schema_size_bytes']}** bytes; object depth **{provider_inventory['maximum_object_depth']}**; properties **{provider_inventory['property_count']}**; required properties **{provider_inventory['required_property_count']}**; refs **{provider_inventory['ref_count']}**; oneOf/anyOf/allOf **{provider_inventory['one_of_count']}/{provider_inventory['any_of_count']}/{provider_inventory['all_of_count']}**; enums/consts **{provider_inventory['enum_count']}/{provider_inventory['const_count']}**; nullable unions **{provider_inventory['nullable_union_count']}**; additionalProperties false **{provider_inventory['additional_properties_false_count']}**; recursive refs **{provider_inventory['recursive_reference_count']}**; maximum array-item nesting **{provider_inventory['maximum_array_item_nesting']}**; unbounded arrays **{provider_inventory['unbounded_array_count']}**.",
        "",
        f"Provider compatibility remains **{readiness['provider_schema_compatibility_status']['status']}**. No provider/model profile or SDK was selected; local provider-specific compilation or dry-validation is required before any call.",
        "",
        "Gold construction requires two independent transcript-first raters who cannot see the machine ledger, one another's work, production outcomes, historical replies, arm identity, or provider identity. A blinded adjudicator locks and hashes the adjudicated ledger before any machine reveal or scoring; that locked ledger supplies Arm D. The design remains four-arm.",
        "",
        "A compound accusation requires at least two materially distinct evidenced propositions. Motive is represented only when alleged; absence of motive is valid. Grade A now directly requires unambiguous turn order.",
        "",
        "## Readiness gates",
        "",
    ]
    for name, gate in readiness.items():
        if name in {"schema_version", "disposition"}:
            continue
        lines.append(f"- `{name}`: **{gate['status']}** — {gate['evidence']}.")
    lines.extend(
        [
            "",
            "## Remaining limitations and boundary",
            "",
            "- Sample-size sufficiency remains pending a later development-only power analysis; Phase 1.2 invents no threshold.",
            "- No authoritative real-conversation gold annotations were created in Phase 1.2.",
            "- Cross-family author overlap is unresolvable from the bounded frozen material.",
            "- The final held-out set was neither selected nor opened.",
            "",
            f"Schema validation passed: **{schema_validation['passed']}**. Privacy validation passed: **{privacy['passed']}**. No provider/model or X call, production write, production import, service change, real annotation, held-out selection, merge, deployment, Phase 2 experiment, generated experimental reply, ordinary summary, or real-conversation machine ledger occurred.",
        ]
    )
    return lines


def _privacy_blocked_phase1_2_report(
    readiness: Mapping[str, Any],
) -> list[str]:
    """Return a bounded report containing no corpus-derived detail."""
    lines = [
        "# Proposition-ledger Phase 1.2 execution-preflight amendment report",
        "",
        "## Disposition",
        "",
        f"**{readiness['disposition']}**",
        "",
        "Privacy validation failed. Corpus metrics, source details, hashes, pseudonyms, and other derived research content are deliberately omitted from this report.",
        "",
        "## Readiness gate statuses",
        "",
    ]
    for name, gate in readiness.items():
        if name in {"schema_version", "disposition"}:
            continue
        lines.append(f"- `{name}`: **{gate['status']}**.")
    lines.extend(
        [
            "",
            "No provider/model or X call, production write, production import, service change, held-out selection, merge, or deployment was authorised by this blocked run.",
        ]
    )
    return lines


def _phase1_2_report_for_readiness(
    inventory: Mapping[str, Any],
    feasibility: Mapping[str, Any],
    target_crosstab: Mapping[str, Any],
    protocol_sha256: str,
    schema_validation: Mapping[str, Any],
    materialiser_validation: Mapping[str, Any],
    provider_inventory: Mapping[str, Any],
    readiness: Mapping[str, Any],
    privacy: Mapping[str, Any],
) -> list[str]:
    """Select the full or privacy-minimal Phase 1.2 report."""
    if privacy.get("passed") is not True:
        return _privacy_blocked_phase1_2_report(readiness)
    return _phase1_2_report(
        inventory,
        feasibility,
        target_crosstab,
        protocol_sha256,
        schema_validation,
        materialiser_validation,
        provider_inventory,
        readiness,
        privacy,
    )


def _write_sha256s(private_output: Path) -> None:
    files = sorted(
        path
        for path in private_output.rglob("*")
        if path.is_file() and path.name not in {"private-author-key", "SHA256SUMS"}
    )
    lines = [f"{sha256_file(path)}  {path.relative_to(private_output)}" for path in files]
    _write_private(private_output / "SHA256SUMS", ("\n".join(lines) + "\n").encode("utf-8"))


def _verify_source_hashes_unchanged(manifest: Mapping[str, Any], before_hashes: Mapping[str, str]) -> None:
    for source_id, before in before_hashes.items():
        after = _source_digest(_source_entry(manifest, source_id))
        if after != before:
            raise Phase1Error(f"source changed while Phase 1 was reading it: {source_id}")


def _normalised_fresh_build_file(path: Path) -> bytes:
    """Return bytes suitable for comparing independently rendered private runs."""
    if path.name == "run-manifest.json":
        manifest = _read_json(path)
        if not isinstance(manifest, dict):
            raise Phase1Error("run manifest must be an object during determinism comparison")
        for field in ("generated_at", "private_output", "source_manifest_path"):
            manifest.pop(field, None)
        return canonical_json_bytes(manifest)
    if path.name == "SHA256SUMS":
        lines = path.read_bytes().splitlines(keepends=True)
        return b"".join(
            line
            for line in lines
            if not line.rstrip(b"\r\n").endswith(b"  run-manifest.json")
        )
    return path.read_bytes()


def _fresh_build_directory_comparison(left: Path, right: Path) -> dict[str, Any]:
    """Compare two complete fresh private builds while isolating run metadata."""
    errors: list[str] = []
    entries: list[dict[str, Path]] = []
    for root in (left, right):
        if not root.is_dir() or root.is_symlink():
            errors.append(f"fresh build is not a regular directory: {root.name}")
            entries.append({})
            continue
        if stat.S_IMODE(root.stat().st_mode) != 0o700:
            errors.append(f"fresh-build root directory mode is not 0700: {root.name}")
        root_entries = {
            str(path.relative_to(root)): path
            for path in root.rglob("*")
        }
        entries.append(root_entries)
    left_entries, right_entries = entries
    left_names = set(left_entries)
    right_names = set(right_entries)
    if left_names != right_names:
        errors.append("fresh build file/directory sets differ")
    substantive_file_count = 0
    for relative in sorted(left_names & right_names):
        left_path = left_entries[relative]
        right_path = right_entries[relative]
        if left_path.is_symlink() or right_path.is_symlink():
            errors.append(f"fresh build contains a symlink: {relative}")
            continue
        if left_path.is_dir() != right_path.is_dir():
            errors.append(f"fresh build entry types differ: {relative}")
            continue
        if left_path.is_dir():
            if stat.S_IMODE(left_path.stat().st_mode) != 0o700:
                errors.append(f"left fresh-build directory mode is not 0700: {relative}")
            if stat.S_IMODE(right_path.stat().st_mode) != 0o700:
                errors.append(f"right fresh-build directory mode is not 0700: {relative}")
            continue
        if not left_path.is_file() or not right_path.is_file():
            errors.append(f"fresh build entry is not a regular file: {relative}")
            continue
        if stat.S_IMODE(left_path.stat().st_mode) != 0o600:
            errors.append(f"left fresh-build file mode is not 0600: {relative}")
        if stat.S_IMODE(right_path.stat().st_mode) != 0o600:
            errors.append(f"right fresh-build file mode is not 0600: {relative}")
        if relative == "private-author-key":
            if left_path.read_bytes() != right_path.read_bytes():
                errors.append("fresh builds did not reuse identical private key bytes")
            continue
        if relative not in {"run-manifest.json", "SHA256SUMS"}:
            substantive_file_count += 1
        if _normalised_fresh_build_file(left_path) != _normalised_fresh_build_file(right_path):
            errors.append(f"fresh build output differs: {relative}")
    return {
        "passed": not errors,
        "errors": errors,
        "substantive_file_count": substantive_file_count,
        "isolated_run_metadata": [
            "run-manifest.generated_at",
            "run-manifest.private_output",
            "run-manifest.source_manifest_path",
            "SHA256SUMS run-manifest entry",
        ],
    }


def _fresh_build_determinism_validation(
    args: argparse.Namespace,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Render and byte-compare two fresh complete private builds."""
    source_key = args.private_output.resolve() / "private-author-key"
    key_bytes = source_key.read_bytes()
    if len(key_bytes) != 32:
        raise Phase1Error("determinism source key is not exactly 32 bytes")
    scratch_parent = args.private_output.resolve().parent
    with tempfile.TemporaryDirectory(
        dir=scratch_parent,
        prefix="proposition-ledger-phase1.2-internal-determinism-",
    ) as scratch_name:
        scratch = Path(scratch_name)
        os.chmod(scratch, 0o700)
        builds = [scratch / "build-a", scratch / "build-b"]
        for build in builds:
            _ensure_private_directory(build)
            _write_private(build / "private-author-key", key_bytes)
            probe_args = copy.copy(args)
            probe_args.private_output = build
            _build_outputs(
                probe_args,
                manifest,
                perform_fresh_determinism_check=False,
            )
        return _fresh_build_directory_comparison(builds[0], builds[1])


def _build_outputs(
    args: argparse.Namespace,
    manifest: Mapping[str, Any],
    *,
    perform_fresh_determinism_check: bool = True,
) -> dict[str, Any]:
    project_dir = args.project_dir.resolve()
    private_output = args.private_output.resolve()
    _ensure_private_directory(private_output)
    key_path = private_output / "private-author-key"
    if not key_path.is_file() or key_path.is_symlink() or key_path.stat().st_size != 32 or stat.S_IMODE(key_path.stat().st_mode) != 0o600:
        raise Phase1Error("private output must already contain a mode-0600 32-byte private-author-key")
    fresh_determinism = (
        _fresh_build_determinism_validation(args, manifest)
        if perform_fresh_determinism_check
        else {
            "passed": True,
            "errors": [],
            "substantive_file_count": 0,
            "isolated_run_metadata": [],
        }
    )
    inventory, before_hashes = _inventory_sources(manifest)
    overlap = _build_overlap(manifest)
    (
        feasibility_rows,
        feasibility_summary,
        exposure_rows,
        exposure_summary,
        turns_by_conversation,
        target_rows,
        target_structural_exclusions,
        target_crosstab,
    ) = _build_feasibility_and_exposure(manifest)
    calibration_records, calibration_manifest, calibration_index = _build_calibration_pack(
        manifest, feasibility_rows, turns_by_conversation
    )
    source_manifest_hash = sha256_file(args.source_manifest.resolve())
    exposure_registry_payload = _jsonl_payload(exposure_rows)
    exposure_registry_hash = sha256_bytes(exposure_registry_payload)
    provider_inventory, provider_compatibility = _provider_schema_preflight(
        project_dir
    )
    protocol = _build_protocol(
        source_manifest_hash,
        exposure_registry_hash,
        provider_compatibility,
    )
    schema_validation = _schema_validation_for_readiness(project_dir, protocol)
    materialiser_validation = _semantic_materialiser_validation_for_readiness(
        project_dir
    )

    second_inventory, _ = _inventory_sources(manifest)
    second_overlap = _build_overlap(manifest)
    (
        second_feasibility_rows,
        second_feasibility_summary,
        second_exposure_rows,
        second_exposure_summary,
        second_turns,
        second_target_rows,
        second_target_structural_exclusions,
        second_target_crosstab,
    ) = _build_feasibility_and_exposure(manifest)
    second_calibration_records, second_calibration_manifest, second_calibration_index = _build_calibration_pack(
        manifest, second_feasibility_rows, second_turns
    )
    second_provider_inventory, second_provider_compatibility = (
        _provider_schema_preflight(project_dir)
    )
    second_protocol = _build_protocol(
        source_manifest_hash,
        sha256_bytes(_jsonl_payload(second_exposure_rows)),
        second_provider_compatibility,
    )
    second_schema_validation = _schema_validation_for_readiness(
        project_dir, second_protocol
    )
    in_process_rebuild_valid = canonical_json_bytes(
        {
            "inventory": inventory,
            "overlap": overlap,
            "feasibility_rows": feasibility_rows,
            "feasibility_summary": feasibility_summary,
            "exposure_rows": exposure_rows,
            "exposure_summary": exposure_summary,
            "target_rows": target_rows,
            "target_structural_exclusions": target_structural_exclusions,
            "target_crosstab": target_crosstab,
            "provider_schema_feature_inventory": provider_inventory,
            "provider_schema_compatibility": provider_compatibility,
            "calibration_records": calibration_records,
            "calibration_manifest": calibration_manifest,
            "calibration_index": calibration_index,
            "protocol": protocol,
            "schema_validation": schema_validation,
        }
    ) == canonical_json_bytes(
        {
            "inventory": second_inventory,
            "overlap": second_overlap,
            "feasibility_rows": second_feasibility_rows,
            "feasibility_summary": second_feasibility_summary,
            "exposure_rows": second_exposure_rows,
            "exposure_summary": second_exposure_summary,
            "target_rows": second_target_rows,
            "target_structural_exclusions": second_target_structural_exclusions,
            "target_crosstab": second_target_crosstab,
            "provider_schema_feature_inventory": second_provider_inventory,
            "provider_schema_compatibility": second_provider_compatibility,
            "calibration_records": second_calibration_records,
            "calibration_manifest": second_calibration_manifest,
            "calibration_index": second_calibration_index,
            "protocol": second_protocol,
            "schema_validation": second_schema_validation,
        }
    )
    deterministic_rebuild_valid = (
        in_process_rebuild_valid and fresh_determinism["passed"] is True
    )

    _write_private(
        private_output / "frozen-source-manifest.json",
        args.source_manifest.resolve().read_bytes(),
    )
    _write_json(private_output / "source-inventory.json", inventory)
    _write_markdown(private_output / "source-inventory.md", _inventory_markdown(inventory))
    _write_json(private_output / "source-overlap.json", overlap)
    _write_markdown(private_output / "source-overlap.md", _overlap_markdown(overlap))
    _write_jsonl(private_output / "conversation-feasibility-index.jsonl", feasibility_rows)
    _write_json(private_output / "corpus-feasibility.json", feasibility_summary)
    _write_markdown(private_output / "corpus-feasibility.md", _feasibility_markdown(feasibility_summary))
    _write_private(private_output / "prior-exposure-registry.jsonl", exposure_registry_payload)
    _write_json(private_output / "prior-exposure-summary.json", exposure_summary)
    _write_json(
        private_output / "author-binding-audit.json",
        exposure_summary["author_binding_audit"],
    )
    _write_jsonl(private_output / "target-prefix-feasibility-index.jsonl", target_rows)
    _write_jsonl(
        private_output / "target-prefix-structural-exclusions.jsonl",
        target_structural_exclusions,
    )
    _write_json(private_output / "target-prefix-crosstab.json", target_crosstab)
    _write_markdown(
        private_output / "target-prefix-crosstab.md",
        _target_prefix_crosstab_markdown(target_crosstab),
    )
    _write_json(
        private_output / "provider-schema-feature-inventory.json",
        provider_inventory,
    )
    _write_jsonl(private_output / "calibration-pack/calibration-records.jsonl", calibration_records)
    _write_json(private_output / "calibration-pack/calibration-index.json", calibration_index)
    _write_json(private_output / "calibration-pack/manifest.json", calibration_manifest)
    _write_json(private_output / "experiment-protocol-draft.json", protocol)
    _write_markdown(private_output / "experiment-protocol-draft.md", _protocol_markdown(protocol))
    _write_json(private_output / "schema-validation.json", schema_validation)
    _write_json(
        private_output / "semantic-delta-schema-validation.json",
        schema_validation["semantic_delta_schema_validation"],
    )
    _write_json(
        private_output / "semantic-materialiser-validation.json",
        materialiser_validation,
    )
    privacy = _privacy_validation(project_dir, private_output, manifest)
    target_crosstab_errors = _target_prefix_crosstab_errors(target_rows, target_crosstab)
    outcome_complete = (
        not target_crosstab_errors
        and sum(
            target_crosstab["dimensions"]["outcome_evidence_class"].values()
        )
        == len(target_rows)
    )
    transcript_first_protocol_valid = (
        protocol.get("adjudication", {}).get("independent_raters") == 2
        and protocol.get("adjudication", {})
        .get("gold_lock", {})
        .get("locked_before_machine_ledger_reveal")
        is True
        and protocol.get("adjudication", {})
        .get("machine_reveal", {})
        .get("permitted_only_after_gold_lock")
        is True
        and len(protocol.get("arms", [])) == 4
    )
    preliminary_within_family_target_count = int(
        target_crosstab["headline_counts"][
            "preliminary_within_family_held_out_eligible_target_prefix_count"
        ]
    )
    preliminary_cross_family_target_count = int(
        target_crosstab["headline_counts"][
            "preliminary_cross_family_clean_held_out_eligible_target_prefix_count"
        ]
    )
    provider_inventory_valid = (
        provider_inventory.get("schema_version") == SEMANTIC_DELTA_SCHEMA_VERSION
        and provider_inventory.get("schema_sha256")
        == schema_validation.get("semantic_delta_schema_sha256")
        and provider_compatibility.get("feature_inventory_sha256")
        == sha256_bytes(canonical_json_bytes(provider_inventory))
        and provider_compatibility.get("network_call_made") is False
    )
    readiness = _derive_readiness_gates(
        source_identity_valid=True,
        deterministic_rebuild_valid=deterministic_rebuild_valid,
        privacy_valid=privacy["passed"] is True,
        schema_valid=schema_validation["passed"] is True,
        synthetic_fixtures_valid=(
            schema_validation["synthetic_fixtures"]["fixture_count"] == 12
            and schema_validation["synthetic_fixtures"]["valid_fixture_count"] == 12
            and schema_validation["synthetic_fixtures"]["all_invalid_examples_detected"]
        ),
        target_outcomes_valid=outcome_complete,
        target_crosstab_valid=not target_crosstab_errors,
        no_future_turn_leakage=all(
            row.get("complete_target_ancestry") is True for row in target_rows
        ),
        semantic_schema_valid=(
            schema_validation["semantic_delta_schema_validation"]["passed"] is True
        ),
        materialiser_valid=materialiser_validation["passed"] is True,
        genesis_materialisation_valid=(
            materialiser_validation.get("genesis_materialisation_valid") is True
        ),
        first_seen_participant_registration_valid=(
            materialiser_validation.get("first_seen_participant_registration_valid")
            is True
        ),
        complete_incremental_chain_valid=(
            materialiser_validation.get("complete_incremental_chain_valid") is True
        ),
        persistent_multiturn_classification_complete=(
            not target_crosstab_errors
            and sum(
                target_crosstab["dimensions"]["target_sequence_class"].values()
            )
            == len(target_rows)
        ),
        within_family_author_group_exposure_enforced=all(
            row.get("author_group_exposure_status") is not None
            and row.get("author_group_requires_groupwise_split") is not None
            for row in target_rows
        ),
        cross_family_author_identity_status="unavailable",
        provider_schema_feature_inventory_valid=provider_inventory_valid,
        provider_schema_compatibility_status=str(
            provider_compatibility.get("status")
        ),
        provider_schema_compatibility_record=provider_compatibility,
        provider_schema_feature_inventory=provider_inventory,
        transcript_first_protocol_valid=transcript_first_protocol_valid,
        preliminary_within_family_target_count=(
            preliminary_within_family_target_count
        ),
        preliminary_cross_family_target_count=(
            preliminary_cross_family_target_count
        ),
        deterministic_rebuild_evidence=FRESH_BUILD_DETERMINISM_EVIDENCE,
    )
    _write_json(private_output / "readiness-gates.json", readiness)
    protocol_sha256 = sha256_file(private_output / "experiment-protocol-draft.json")
    report_lines = _phase1_2_report_for_readiness(
        inventory,
        feasibility_summary,
        target_crosstab,
        protocol_sha256,
        schema_validation,
        materialiser_validation,
        provider_inventory,
        readiness,
        privacy,
    )
    _write_markdown(private_output / "phase1.2-report.md", report_lines)
    run_manifest = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "run_kind": "proposition-ledger-phase1.2-execution-preflight-amendment",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "private_output": str(private_output),
        "verified_origin_master_sha": manifest.get("verified_origin_master_sha"),
        "phase1_1_base_sha": PHASE1_1_BASE_SHA,
        "prospective_batch_resolved_once": manifest.get("prospective_batch_resolved_once"),
        "prospective_batch_manifest_sha256": manifest.get("prospective_batch_manifest_sha256"),
        "source_manifest_input_path": str(args.source_manifest.resolve()),
        "source_manifest_path": str(private_output / "frozen-source-manifest.json"),
        "source_manifest_sha256": source_manifest_hash,
        "cutoff": args.cutoff,
        "network_calls": 0,
        "provider_calls": 0,
        "x_calls": 0,
        "model_generated_real_ledgers": 0,
        "model_generated_experimental_replies": 0,
        "production_writes": 0,
        "final_held_out_selected": False,
        "phase2_started": False,
    }
    _write_json(private_output / "run-manifest.json", run_manifest)
    _write_sha256s(private_output)
    emitted_block = _emitted_blocking_disposition(readiness)
    if emitted_block is not None:
        _verify_source_hashes_unchanged(manifest, before_hashes)
        if not perform_fresh_determinism_check:
            return {
                "private_output": str(private_output),
                "readiness_disposition": emitted_block,
                "blocked_after_artifact_emission": True,
            }
        raise Phase1Error(
            f"Phase 1.2 preflight blocked after readiness/report emission: {emitted_block}"
        )
    validation = _validate_outputs(private_output, project_dir, source_manifest_hash)
    if not validation["passed"]:
        raise Phase1Error(f"generated output validation failed: {validation}")
    final_privacy = _privacy_validation(project_dir, private_output, manifest)
    if not final_privacy["passed"]:
        raise Phase1Error("final post-report privacy validation failed")
    _verify_source_hashes_unchanged(manifest, before_hashes)
    return {
        "private_output": str(private_output),
        "grade_counts": feasibility_summary["grade_counts"],
        "target_prefix_counts": feasibility_summary["target_prefix_counts"],
        "target_structural_reconciliation": feasibility_summary[
            "target_structural_reconciliation"
        ],
        "exposure_counts": exposure_summary["conversation_status_counts"],
        "protocol_sha256": protocol_sha256,
        "ledger_schema_sha256": schema_validation["ledger_schema_sha256"],
        "semantic_delta_schema_sha256": schema_validation["semantic_delta_schema_sha256"],
        "experiment_schema_sha256": schema_validation["experiment_schema_sha256"],
        "materialiser_sha256": materialiser_validation["source_sha256"],
        "readiness_disposition": readiness["disposition"],
        "target_prefix_crosstab": target_crosstab["headline_counts"],
        "calibration_pack_sha256": calibration_manifest["pack_content_sha256"],
        "fixture_count": schema_validation["synthetic_fixtures"]["fixture_count"],
        "privacy": final_privacy,
        "validation": validation,
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the explicit, no-production-default command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--research-root", type=Path, required=True)
    parser.add_argument("--prospective-root", type=Path, required=True)
    parser.add_argument("--benchmark-run", type=Path, required=True)
    parser.add_argument("--multi-turn-audit-run", type=Path, required=True)
    parser.add_argument("--private-output", type=Path, required=True)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    return parser


def _validate_arguments(args: argparse.Namespace, manifest: Mapping[str, Any]) -> None:
    project_dir = args.project_dir.resolve()
    production_dir = Path("/disks/disk1/etc/mrsMThatcher")
    private_output = args.private_output.resolve()
    if project_dir == production_dir:
        raise Phase1Error("Phase 1 must run from the isolated research worktree, never production")
    research_root = args.research_root.resolve()
    for label, path in (
        ("project directory", project_dir),
        ("prospective root", args.prospective_root.resolve()),
        ("benchmark run", args.benchmark_run.resolve()),
        ("multi-turn audit run", args.multi_turn_audit_run.resolve()),
        ("private output", private_output),
        ("source manifest", args.source_manifest.resolve()),
    ):
        if path != research_root and research_root not in path.parents:
            raise Phase1Error(f"{label} must be beneath the explicit research root")
    if project_dir in private_output.parents or private_output == project_dir:
        raise Phase1Error("private conversation output must be outside the tracked worktree")
    if manifest.get("schema_version") != SOURCE_MANIFEST_VERSION:
        raise Phase1Error("unsupported frozen source manifest version")
    if args.cutoff != manifest.get("cutoff"):
        raise Phase1Error("CLI cutoff does not match the frozen source manifest")
    resolved_batch = Path(str(manifest.get("prospective_batch_resolved_once"))).resolve()
    if resolved_batch.parent.parent != args.prospective_root.resolve():
        raise Phase1Error("frozen prospective batch is not beneath the explicit prospective root")
    expected_locations = {
        "benchmark_conversations": args.benchmark_run.resolve(),
        "audit_conversations": args.multi_turn_audit_run.resolve(),
    }
    for source_id, expected_parent in expected_locations.items():
        if expected_parent not in _source_path(manifest, source_id).resolve().parents:
            raise Phase1Error(f"{source_id} is outside the explicit run directory")
    batch_manifest = resolved_batch / "manifest.json"
    if sha256_file(batch_manifest) != manifest.get("prospective_batch_manifest_sha256"):
        raise Phase1Error("frozen prospective batch manifest hash mismatch")
    if _source_path(manifest, "prospective_conversations").resolve().parent != resolved_batch:
        raise Phase1Error("prospective conversation source is not the once-resolved immutable batch")
    allowed_production_sources = {
        "prospective_extractor_tool": production_dir / "tools/extract_prospective_conversations.py",
        "prospective_extractor_documentation": production_dir / "docs/prospective_conversation_extractor.md",
        "prospective_extractor_tests": production_dir / "tests/test_prospective_conversation_extractor.py",
    }
    for entry in manifest.get("sources", []):
        source_id = str(entry.get("source_id") or "")
        source_path = Path(str(entry.get("path") or "")).resolve()
        if source_path == allowed_production_sources.get(source_id):
            continue
        if source_path != research_root and research_root not in source_path.parents:
            raise Phase1Error(f"source {source_id!r} is outside the bounded research root")


def _substantive_rebuild_errors(
    args: argparse.Namespace,
    manifest: Mapping[str, Any],
    inventory: Mapping[str, Any],
) -> list[str]:
    private_output = args.private_output.resolve()
    project_dir = args.project_dir.resolve()
    overlap = _build_overlap(manifest)
    (
        feasibility_rows,
        feasibility_summary,
        exposure_rows,
        exposure_summary,
        turns_by_conversation,
        target_rows,
        target_structural_exclusions,
        target_crosstab,
    ) = _build_feasibility_and_exposure(manifest)
    calibration_records, calibration_manifest, calibration_index = _build_calibration_pack(
        manifest,
        feasibility_rows,
        turns_by_conversation,
    )
    exposure_payload = _jsonl_payload(exposure_rows)
    provider_inventory, provider_compatibility = _provider_schema_preflight(
        project_dir
    )
    protocol = _build_protocol(
        sha256_file(args.source_manifest.resolve()),
        sha256_bytes(exposure_payload),
        provider_compatibility,
    )
    schema_validation = _schema_validation_for_readiness(project_dir, protocol)
    materialiser_validation = _semantic_materialiser_validation_for_readiness(
        project_dir
    )
    privacy = _privacy_validation(project_dir, private_output, manifest)
    crosstab_errors = _target_prefix_crosstab_errors(target_rows, target_crosstab)
    readiness = _derive_readiness_gates(
        source_identity_valid=True,
        deterministic_rebuild_valid=True,
        privacy_valid=privacy["passed"] is True,
        schema_valid=schema_validation["passed"] is True,
        synthetic_fixtures_valid=(
            schema_validation["synthetic_fixtures"]["fixture_count"] == 12
            and schema_validation["synthetic_fixtures"]["valid_fixture_count"] == 12
            and schema_validation["synthetic_fixtures"]["all_invalid_examples_detected"]
        ),
        target_outcomes_valid=(
            not crosstab_errors
            and sum(target_crosstab["dimensions"]["outcome_evidence_class"].values())
            == len(target_rows)
        ),
        target_crosstab_valid=not crosstab_errors,
        no_future_turn_leakage=all(
            row.get("complete_target_ancestry") is True for row in target_rows
        ),
        semantic_schema_valid=(
            schema_validation["semantic_delta_schema_validation"]["passed"] is True
        ),
        materialiser_valid=materialiser_validation["passed"] is True,
        genesis_materialisation_valid=(
            materialiser_validation.get("genesis_materialisation_valid") is True
        ),
        first_seen_participant_registration_valid=(
            materialiser_validation.get("first_seen_participant_registration_valid")
            is True
        ),
        complete_incremental_chain_valid=(
            materialiser_validation.get("complete_incremental_chain_valid") is True
        ),
        persistent_multiturn_classification_complete=(
            not crosstab_errors
            and sum(
                target_crosstab["dimensions"]["target_sequence_class"].values()
            )
            == len(target_rows)
        ),
        within_family_author_group_exposure_enforced=all(
            row.get("author_group_exposure_status") is not None
            and row.get("author_group_requires_groupwise_split") is not None
            for row in target_rows
        ),
        cross_family_author_identity_status="unavailable",
        provider_schema_feature_inventory_valid=(
            provider_inventory.get("schema_version")
            == SEMANTIC_DELTA_SCHEMA_VERSION
            and provider_inventory.get("schema_sha256")
            == schema_validation.get("semantic_delta_schema_sha256")
            and provider_compatibility.get("feature_inventory_sha256")
            == sha256_bytes(canonical_json_bytes(provider_inventory))
            and provider_compatibility.get("network_call_made") is False
        ),
        provider_schema_compatibility_status=str(
            provider_compatibility.get("status")
        ),
        provider_schema_compatibility_record=provider_compatibility,
        provider_schema_feature_inventory=provider_inventory,
        transcript_first_protocol_valid=(
            protocol.get("adjudication", {}).get("independent_raters") == 2
            and protocol.get("adjudication", {})
            .get("gold_lock", {})
            .get("locked_before_machine_ledger_reveal")
            is True
            and protocol.get("adjudication", {})
            .get("machine_reveal", {})
            .get("permitted_only_after_gold_lock")
            is True
            and len(protocol.get("arms", [])) == 4
        ),
        preliminary_within_family_target_count=int(
            target_crosstab["headline_counts"][
                "preliminary_within_family_held_out_eligible_target_prefix_count"
            ]
        ),
        preliminary_cross_family_target_count=int(
            target_crosstab["headline_counts"][
                "preliminary_cross_family_clean_held_out_eligible_target_prefix_count"
            ]
        ),
        deterministic_rebuild_evidence=FRESH_BUILD_DETERMINISM_EVIDENCE,
    )
    protocol_sha256 = sha256_file(private_output / "experiment-protocol-draft.json")
    expected_json = {
        "source-inventory.json": inventory,
        "source-overlap.json": overlap,
        "corpus-feasibility.json": feasibility_summary,
        "prior-exposure-summary.json": exposure_summary,
        "author-binding-audit.json": exposure_summary[
            "author_binding_audit"
        ],
        "target-prefix-crosstab.json": target_crosstab,
        "provider-schema-feature-inventory.json": provider_inventory,
        "calibration-pack/calibration-index.json": calibration_index,
        "calibration-pack/manifest.json": calibration_manifest,
        "experiment-protocol-draft.json": protocol,
        "schema-validation.json": schema_validation,
        "semantic-delta-schema-validation.json": schema_validation[
            "semantic_delta_schema_validation"
        ],
        "semantic-materialiser-validation.json": materialiser_validation,
        "readiness-gates.json": readiness,
    }
    expected_jsonl = {
        "conversation-feasibility-index.jsonl": feasibility_rows,
        "prior-exposure-registry.jsonl": exposure_rows,
        "target-prefix-feasibility-index.jsonl": target_rows,
        "target-prefix-structural-exclusions.jsonl": (
            target_structural_exclusions
        ),
        "calibration-pack/calibration-records.jsonl": calibration_records,
    }
    errors: list[str] = []
    for relative, expected in expected_json.items():
        if canonical_json_bytes(_read_json(private_output / relative)) != canonical_json_bytes(expected):
            errors.append(relative)
    for relative, expected in expected_jsonl.items():
        if _jsonl_payload(_read_jsonl(private_output / relative)) != _jsonl_payload(expected):
            errors.append(relative)
    expected_markdown = {
        "source-inventory.md": _inventory_markdown(inventory),
        "source-overlap.md": _overlap_markdown(overlap),
        "corpus-feasibility.md": _feasibility_markdown(feasibility_summary),
        "target-prefix-crosstab.md": _target_prefix_crosstab_markdown(target_crosstab),
        "experiment-protocol-draft.md": _protocol_markdown(protocol),
        "phase1.2-report.md": _phase1_2_report_for_readiness(
            inventory,
            feasibility_summary,
            target_crosstab,
            protocol_sha256,
            schema_validation,
            materialiser_validation,
            provider_inventory,
            readiness,
            privacy,
        ),
    }
    for relative, lines in expected_markdown.items():
        expected_payload = ("\n".join(lines) + "\n").encode("utf-8")
        if (private_output / relative).read_bytes() != expected_payload:
            errors.append(relative)
    if (private_output / "frozen-source-manifest.json").read_bytes() != args.source_manifest.resolve().read_bytes():
        errors.append("frozen-source-manifest.json")
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    """Build or verify the local Phase 1 evidence artefacts."""
    args = build_parser().parse_args(argv)
    try:
        manifest = _read_json(args.source_manifest.resolve())
        if not isinstance(manifest, dict):
            raise Phase1Error("source manifest must be a JSON object")
        _validate_arguments(args, manifest)
        if args.verify_only:
            inventory, before_hashes = _inventory_sources(manifest)
            source_manifest_hash = sha256_file(args.source_manifest.resolve())
            result = _validate_outputs(
                args.private_output.resolve(),
                args.project_dir.resolve(),
                source_manifest_hash,
            )
            privacy = _privacy_validation(args.project_dir.resolve(), args.private_output.resolve(), manifest)
            rebuild_errors = _substantive_rebuild_errors(args, manifest, inventory)
            _verify_source_hashes_unchanged(manifest, before_hashes)
            result["source_count_verified"] = inventory["source_count"]
            result["privacy_validation"] = privacy
            result["substantive_rebuild_errors"] = rebuild_errors
            result["passed"] = result["passed"] and privacy["passed"] and not rebuild_errors
            if not result["passed"]:
                raise Phase1Error(f"verification failed: {result}")
            print(json.dumps(result, sort_keys=True))
            return 0
        result = _build_outputs(args, manifest)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, Phase1Error) as exc:
        print(f"phase1 error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
