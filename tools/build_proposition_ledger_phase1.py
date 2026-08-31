#!/usr/bin/env python3
"""Build and verify the read-only proposition-ledger Phase 1 evidence pack.

The tool deliberately has no production-module imports and no network or model
client. All source locations are explicit, frozen inputs. Conversation text is
written only beneath the caller-supplied mode-0700 private output directory.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import hmac
import json
import math
import os
import re
import stat
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


LEDGER_SCHEMA_VERSION = "proposition-ledger-v1.0.0"
EXPERIMENT_SCHEMA_VERSION = "proposition-ledger-experiment-v1.0.0"
SOURCE_MANIFEST_VERSION = "proposition-ledger-phase1-source-manifest-v1"
OUTPUT_SCHEMA_VERSION = "proposition-ledger-phase1-output-v1"
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
            member_roles = {member.get("role") for member in members}
            if len(member_ids) < 3 or not {"conduct", "cause", "motive"}.issubset(member_roles):
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
        turn_order_unambiguous,
        account_publication_confirmed,
        parent_graph_unambiguous,
        complete_prefix_through_targets,
        source_complete,
        reconstruction_confidence in {"high", "medium"},
    )
    limited_gaps: list[str] = []
    if not chronology_complete:
        limited_gaps.append("one_limited_timing_gap")
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


def _quiescent_terminal_no_reply_target(
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


def _conversation_record_from_benchmark(
    row: Mapping[str, Any],
    canonical_posts: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    turns = [dict(turn) for turn in row.get("turns", [])]
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
        canonical = canonical_posts.get(post_id)
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
        "terminal_no_reply_target_turn_ids": [],
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
        "terminal_no_reply_target_turn_ids": [],
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


def _build_feasibility_and_exposure(
    manifest: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, list[dict[str, Any]]]]:
    benchmark_rows = _read_jsonl(_source_path(manifest, "benchmark_conversations"))
    prospective_rows = _read_jsonl(_source_path(manifest, "prospective_conversations"))
    prospective_posts = {
        str(row["post_id"]): row
        for row in _read_jsonl(_source_path(manifest, "prospective_canonical_posts"))
    }
    records: list[dict[str, Any]] = []
    turns_by_conversation: dict[str, list[dict[str, Any]]] = {}
    for row in benchmark_rows:
        record, turns = _conversation_record_from_benchmark(row, prospective_posts)
        records.append(record)
        turns_by_conversation[str(record["conversation_key"])] = turns
    for row in prospective_rows:
        record, turns = _conversation_record_from_prospective(row)
        records.append(record)
        turns_by_conversation[str(record["conversation_key"])] = turns
    conversation_keys = [str(record["conversation_key"]) for record in records]
    if len(conversation_keys) != len(set(conversation_keys)):
        raise Phase1Error("canonical benchmark/prospective union has duplicate conversation keys")
    terminal_candidate_map: dict[str, set[str]] = defaultdict(set)
    records_by_key = {str(record["conversation_key"]): record for record in records}
    candidate_rows = _read_jsonl(_source_path(manifest, "prospective_review_candidates"))
    for candidate in candidate_rows:
        conversation_key = str(candidate.get("conversation_key") or "")
        record = records_by_key.get(conversation_key)
        if record is None:
            continue
        target_turn_id = _quiescent_terminal_no_reply_target(
            candidate,
            record,
            turns_by_conversation[conversation_key],
        )
        if target_turn_id is not None:
            terminal_candidate_map[conversation_key].add(target_turn_id)
    for record in records:
        record["terminal_no_reply_target_turn_ids"] = sorted(terminal_candidate_map.get(str(record["conversation_key"]), set()))
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
        statuses: set[str] = set(conversation_target_statuses.get(conversation_key, set()))
        reasons: set[str] = set(conversation_target_reasons.get(conversation_key, set()))
        is_benchmark = "benchmark_conversations" in record["source_ids"]
        if conversation_key in evidence["audit_conversations"]:
            statuses.update({"development_labelled", "prior_model_experiment", "prior_human_review"})
            reasons.update(
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
            statuses.add("prior_model_experiment")
            reasons.add("frozen benchmark prior-experiment identity registry")
        if conversation_key in evidence["benchmark_review_sample_conversations"]:
            statuses.add("structurally_mined_only")
            reasons.add("benchmark next-stage unlabelled review sample")
        if conversation_key in evidence["review_pack_conversations"]:
            statuses.add("structurally_mined_only")
            reasons.add("QUD frozen prospective review pack")
        if conversation_key in evidence["report_conversation_keys"]:
            statuses.add("report_excerpt")
            reasons.add("exact conversation identity appears in a retained research report")
        if conversation_key in evidence["calibration_conversations"]:
            statuses.add("calibration")
            reasons.add("selected for the private non-blind Phase 1 calibration pack")
        if not statuses:
            if record["reconstruction_grade"] == "A":
                statuses.add("unexposed_candidate")
                reasons.add("no reliable prior model, human, report, incident, or review-pack identity found")
            else:
                statuses.add("structurally_mined_only")
                reasons.add("structurally reconstructed but ineligible for primary experiment")
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
                    "exposure_statuses": sorted(statuses),
                    "exposure_reasons": sorted(reasons),
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
    records = [_row_with_hash(record) for record in sorted(records, key=lambda item: (item.get("start_time") or "", item["conversation_key"]))]
    grade_counts = Counter(record["reconstruction_grade"] for record in records)
    target_counts = {
        "published_reply_target_prefixes": sum(len(record["published_reply_target_turn_ids"]) for record in records if record["reconstruction_grade"] == "A"),
        "terminal_no_reply_target_prefixes": sum(len(record["terminal_no_reply_target_turn_ids"]) for record in records if record["reconstruction_grade"] == "A"),
    }
    target_counts["total_usable_target_prefixes"] = sum(target_counts.values())
    direct_exposed = [record for record in records if record["prior_exposure_status"] == "exposed"]
    structural_only = [record for record in records if record["prior_exposure_status"] == "structurally_mined_only"]
    unexposed = [record for record in records if record["prior_exposure_status"] == "unexposed_candidate"]
    feasibility_summary = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "canonical_union_conversation_count": len(records),
        "grade_counts": {grade: grade_counts.get(grade, 0) for grade in ("A", "B", "C")},
        "target_prefix_counts": target_counts,
        "directly_exposed_conversation_count": len(direct_exposed),
        "structurally_mined_only_conversation_count": len(structural_only),
        "potential_unexposed_candidate_count": len(unexposed),
        "potential_unexposed_by_grade": {grade: sum(record["reconstruction_grade"] == grade for record in unexposed) for grade in ("A", "B", "C")},
        "author_grouping": {
            "feasible_within_benchmark_family": True,
            "feasible_within_prospective_v4_family": True,
            "cross_family_comparable": False,
            "raw_identity_required": False,
            "limitation": "benchmark truncated SHA-256 and prospective HMAC keys use different domains and cannot prove cross-family author identity",
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
        "final_held_out_selected_or_opened": False,
        "newly_mined_is_not_held_out": True,
        "prospective_is_not_automatically_held_out": True,
    }
    return records, feasibility_summary, sorted(exposure_rows, key=lambda row: row["exposure_key"]), exposure_summary, turns_by_conversation


def _feasibility_markdown(summary: Mapping[str, Any]) -> list[str]:
    grades = summary["grade_counts"]
    targets = summary["target_prefix_counts"]
    return [
        "# Corpus feasibility audit",
        "",
        f"The structural union contains **{summary['canonical_union_conversation_count']}** independent conversations: Grade A **{grades['A']}**, Grade B **{grades['B']}**, and Grade C **{grades['C']}**.",
        "",
        f"There are **{targets['total_usable_target_prefixes']}** structurally usable Grade-A target prefixes: {targets['published_reply_target_prefixes']} confirmed published-reply targets and {targets['terminal_no_reply_target_prefixes']} quiescent terminal no-reply targets.",
        "",
        f"Directly exposed conversations: **{summary['directly_exposed_conversation_count']}**. Structurally mined-only conversations: **{summary['structurally_mined_only_conversation_count']}**. Potential unexposed candidates, without selecting or opening a final test set: **{summary['potential_unexposed_candidate_count']}**.",
        "",
        summary["canonical_base_recommendation"],
        "",
        "Author-grouped splitting is feasible within each source family without raw identities. The benchmark and prospective pseudonym schemes are not cross-family comparable.",
        "",
        "No final held-out set was selected or opened in Phase 1.",
    ]


def _parent_turn_id(turn: Mapping[str, Any], post_to_turn: Mapping[str, Mapping[str, Any]]) -> str | None:
    parent_post_id = turn.get("parent_id") if "parent_id" in turn else turn.get("parent_post_id")
    if parent_post_id is None:
        return None
    parent = post_to_turn.get(str(parent_post_id))
    return str(parent.get("turn_id")) if parent and parent.get("turn_id") else None


def _ancestor_prefix(turns: Sequence[Mapping[str, Any]], target_identity: str) -> list[dict[str, Any]]:
    post_to_turn = {str(turn.get("post_id")): turn for turn in turns if turn.get("post_id") is not None}
    turn_to_turn = {str(turn.get("turn_id")): turn for turn in turns if turn.get("turn_id") is not None}
    target = turn_to_turn.get(target_identity) or post_to_turn.get(target_identity)
    if target is None:
        raise Phase1Error(f"calibration target is absent from conversation: {target_identity}")
    chain: list[Mapping[str, Any]] = []
    seen_posts: set[str] = set()
    cursor: Mapping[str, Any] | None = target
    while cursor is not None:
        post_id = str(cursor.get("post_id") or cursor.get("turn_id"))
        if post_id in seen_posts:
            raise Phase1Error(f"parent cycle while constructing calibration prefix: {target_identity}")
        seen_posts.add(post_id)
        chain.append(cursor)
        parent_id = cursor.get("parent_id") if "parent_id" in cursor else cursor.get("parent_post_id")
        cursor = post_to_turn.get(str(parent_id)) if parent_id is not None else None
    chain.reverse()
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


def _build_protocol(source_manifest_sha256: str, exposure_registry_sha256: str) -> dict[str, Any]:
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
            "author_grouping": "required_where_comparable_pseudonym_exists",
            "previously_seen_policy": "development_or_calibration_only",
            "held_out_policy": "select_only_after_protocol_freeze_without_opening_in_phase1",
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
                "label": "transcript plus human-corrected proposition ledger",
                "transcript_prefix": "exact_target_bounded_transcript",
                "additional_representation": "human_corrected_proposition_ledger",
                "future_information_allowed": False,
                "population": "smaller_adjudicated_subset",
                "budget_rule": "corrected ledger preserves the same schema and target boundary; size is reported rather than forced to match extraction errors",
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
                "estimand": "effect of a human-corrected ledger relative to transcript alone",
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
                "estimand": "effect of a corrected ledger relative to an equal-budget neutral summary",
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
                "estimand": "effect attributable to machine extraction error relative to a corrected ledger",
                "purpose": "separate ledger-construction error from downstream-consumer error",
            },
        },
        "task_families": [
            {
                "task_id": "ledger_construction_accuracy",
                "label": "Ledger construction accuracy",
                "scored_separately": True,
                "inputs": ["exact transcript prefix"],
                "outputs": ["schema-valid incremental ledger", "abstention or validation failure"],
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
            "principal_author_grouping": "where_cross_record_pseudonym_comparability_is_proven",
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
            "response_schemas",
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
            "independent_raters": 2,
            "disagreement_resolution": "independent ratings followed by blinded adjudication; adjudicator sees neither arm labels nor provider identity",
            "ledger_gold_boundary": "human_corrected_subset_only",
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
            "construct ledgers and validate them without downstream outcome access",
            "score ledger construction accuracy separately on the adjudicated subset",
            "run continuity reasoning in paired blinded conditions",
            "run reply composition only where the frozen task design calls for a reply",
            "complete blinded ratings and adjudication",
            "verify resource and leakage ledgers before staged unblinding",
        ],
        "limitations": [
            "Phase 1 does not select or inspect the final held-out set.",
            "Model profiles, prompts, token tolerance, random seed, rubrics, and numeric thresholds remain to be frozen in Phase 2 before any provider call.",
            "No Phase 1 result estimates ledger effectiveness or supports production integration.",
        ],
    }


def _protocol_markdown(protocol: Mapping[str, Any]) -> list[str]:
    lines = [
        "# Proposition-ledger Phase 2 protocol draft",
        "",
        "This hash-bound draft defines the experiment design. It authorizes no provider call, model output, held-out selection, or experimental scoring in Phase 1.",
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
            "All A/B/C comparisons are paired within the same conversation and target prefix; Arm D comparisons use the smaller adjudicated subset. Splits are by whole conversation, grouped by principal-author pseudonym where that pseudonym is demonstrably comparable. Previously labelled or manually reviewed material remains development/calibration only. The final test set is neither selected nor opened in Phase 1.",
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
        [project_dir / "tools/build_proposition_ledger_phase1.py"]
        + list((project_dir / "proposition_ledger_research").rglob("*"))
        + ([project_dir / "tests/test_proposition_ledger_phase1.py"] if (project_dir / "tests/test_proposition_ledger_phase1.py").exists() else [])
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
    ledger_schema = _read_json(ledger_schema_path)
    experiment_schema = _read_json(experiment_schema_path)
    try:
        import jsonschema
    except ImportError as exc:  # pragma: no cover
        raise Phase1Error("jsonschema is required for Phase 1 validation") from exc
    meta_errors: list[str] = []
    validator_class = getattr(jsonschema, "Draft202012Validator", jsonschema.Draft7Validator)
    for name, schema in (("ledger", ledger_schema), ("experiment", experiment_schema)):
        try:
            effective_schema = schema if hasattr(jsonschema, "Draft202012Validator") else _draft7_compatible_schema(schema)
            validator_class.check_schema(effective_schema)
        except Exception as exc:  # jsonschema raises several schema subclasses
            meta_errors.append(f"{name}:{type(exc).__name__}:{exc}")
    fixture_validation = validate_synthetic_fixtures(project_dir)
    protocol_errors = _jsonschema_errors(protocol, experiment_schema)
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "ledger_schema_sha256": sha256_file(ledger_schema_path),
        "experiment_schema_sha256": sha256_file(experiment_schema_path),
        "meta_schema_errors": meta_errors,
        "protocol_schema_errors": protocol_errors,
        "synthetic_fixtures": fixture_validation,
        "passed": not meta_errors
        and not protocol_errors
        and fixture_validation["fixture_count"] == 12
        and fixture_validation["valid_fixture_count"] == 12
        and fixture_validation["all_invalid_examples_detected"],
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
        "source-inventory.json",
        "source-inventory.md",
        "source-overlap.json",
        "source-overlap.md",
        "conversation-feasibility-index.jsonl",
        "corpus-feasibility.json",
        "corpus-feasibility.md",
        "prior-exposure-registry.jsonl",
        "prior-exposure-summary.json",
        "calibration-pack/calibration-records.jsonl",
        "calibration-pack/calibration-index.json",
        "calibration-pack/manifest.json",
        "experiment-protocol-draft.json",
        "experiment-protocol-draft.md",
        "schema-validation.json",
        "phase1-report.md",
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
    calibration_rows = _read_jsonl(private_output / "calibration-pack/calibration-records.jsonl") if not missing else []
    row_hash_errors = [
        *_validate_row_hashes(feasibility_rows, "feasibility"),
        *_validate_row_hashes(exposure_rows, "exposure"),
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
                    "turn_order_unambiguous",
                    "complete_prefix_through_targets",
                )
            )
            or len(row.get("secondary_quality_limitations", [])) != 1
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
            ("experiment_schema_sha256", "proposition_ledger_research/schema/proposition-ledger-experiment-v1.schema.json"),
        ):
            if schema_validation.get(field) != sha256_file(project_dir / relative):
                schema_hash_errors.append(field)
    source_manifest_binding_errors: list[str] = []
    if not missing and expected_source_manifest_sha256 is not None:
        run_manifest = _read_json(private_output / "run-manifest.json")
        protocol = _read_json(private_output / "experiment-protocol-draft.json")
        if run_manifest.get("source_manifest_sha256") != expected_source_manifest_sha256:
            source_manifest_binding_errors.append("run_manifest")
        if protocol.get("corpus_policy", {}).get("source_manifest_sha256") != expected_source_manifest_sha256:
            source_manifest_binding_errors.append("experiment_protocol")
        exposure_hash = sha256_file(private_output / "prior-exposure-registry.jsonl")
        if protocol.get("corpus_policy", {}).get("exposure_registry_sha256") != exposure_hash:
            source_manifest_binding_errors.append("exposure_registry")
    target_prefix_count_errors: list[str] = []
    if not missing:
        feasibility_summary = _read_json(private_output / "corpus-feasibility.json")
        published_count = sum(
            len(row.get("published_reply_target_turn_ids", []))
            for row in feasibility_rows
            if row.get("reconstruction_grade") == "A"
        )
        terminal_count = sum(
            len(row.get("terminal_no_reply_target_turn_ids", []))
            for row in feasibility_rows
            if row.get("reconstruction_grade") == "A"
        )
        expected_counts = feasibility_summary.get("target_prefix_counts", {})
        if published_count != expected_counts.get("published_reply_target_prefixes"):
            target_prefix_count_errors.append("published_reply_target_prefixes")
        if terminal_count != expected_counts.get("terminal_no_reply_target_prefixes"):
            target_prefix_count_errors.append("terminal_no_reply_target_prefixes")
        if published_count + terminal_count != expected_counts.get("total_usable_target_prefixes"):
            target_prefix_count_errors.append("total_usable_target_prefixes")
        for row in feasibility_rows:
            if set(row.get("published_reply_target_turn_ids", [])) & set(row.get("terminal_no_reply_target_turn_ids", [])):
                target_prefix_count_errors.append(f"overlap:{row.get('conversation_key')}")
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
        "source_manifest_binding_errors": source_manifest_binding_errors,
        "target_prefix_count_errors": target_prefix_count_errors,
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
                source_manifest_binding_errors,
                target_prefix_count_errors,
                schema_validation.get("passed") is not True,
            )
        ),
    }


def _phase1_report(
    inventory: Mapping[str, Any],
    overlap: Mapping[str, Any],
    feasibility: Mapping[str, Any],
    exposure: Mapping[str, Any],
    protocol_sha256: str,
    ledger_schema_sha256: str,
    calibration_manifest: Mapping[str, Any],
    schema_validation: Mapping[str, Any],
    privacy: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> list[str]:
    grades = feasibility["grade_counts"]
    targets = feasibility["target_prefix_counts"]
    q = manifest.get("qud_summary", {})
    disposition = "ready_for_phase2_with_corpus_limitations"
    lines = [
        "# Proposition-ledger Phase 1 evidence report",
        "",
        "## Disposition",
        "",
        f"**{disposition}**",
        "",
        "Phase 1 establishes corpus and protocol feasibility only. It does not test whether a proposition ledger is effective and does not recommend production integration.",
        "",
        "## Historical evidence",
        "",
        f"The inventory contains {inventory['source_count']} exact source artefacts. The strongest pre-boundary conversation base is the frozen 44-conversation benchmark; prospective-v4 is the strongest boundary/post-boundary graph source. Historical 266/293-row replay unions are isolated real target/pipeline reconstructions, not complete independent conversations, and no model replicate is counted as a conversation.",
        "",
        "The prospective canonical-post source corroborates 185 benchmark turns. Three quote-tweet roots have a resolved quote-versus-reply parent semantic discrepancy; the Phase 1 index records the correction explicitly rather than merging quoted context as a reply parent.",
        "",
        "The multi-turn audit contributes 39 exposed development conversations, 87 per-reply labels, and 104 user-proposition extraction records. It is development evidence rather than an authoritative proposition gold set.",
        "",
        "## Corpus feasibility",
        "",
        f"Independent canonical-union conversations: **{feasibility['canonical_union_conversation_count']}**. Grade A: **{grades['A']}**; Grade B: **{grades['B']}**; Grade C: **{grades['C']}**.",
        "",
        f"Structurally usable Grade-A target prefixes: **{targets['total_usable_target_prefixes']}** ({targets['published_reply_target_prefixes']} confirmed published-reply targets and {targets['terminal_no_reply_target_prefixes']} quiescent terminal no-reply targets).",
        "",
        f"Directly exposed conversations: **{feasibility['directly_exposed_conversation_count']}**. Structurally mined-only: **{feasibility['structurally_mined_only_conversation_count']}**. Potential unexposed candidates, without selecting a final held-out set: **{feasibility['potential_unexposed_candidate_count']}**.",
        "",
        "Author-grouped splitting is feasible within the benchmark and prospective families without raw identity, but their pseudonym domains are not cross-family comparable.",
        "",
        "## Prior QUD work",
        "",
        f"The located QUD run is `{q.get('run_path')}` at worktree commit `{q.get('worktree_head')}`. Its frozen candidate identity is `{q.get('candidate_pool_sha256')}` and paid set identity is `{q.get('paid_case_set_sha256')}`.",
        "",
        f"Verified pilot counts: {q.get('candidate_pool_count')} candidates, {q.get('paid_case_count')} paid cases, {q.get('issue_found')} issue-found, {q.get('no_stable_issue')} no-stable-issue, {q.get('schema_or_provider_failure')} schema/provider failure, {q.get('rejected_target_case_count')} rejected-target cases, broad flags {q.get('transcript_broad_flags')}/{q.get('ledger_broad_flags')}, narrow triggers {q.get('transcript_narrow_triggers')}/{q.get('ledger_narrow_triggers')}, and repairs {q.get('repair_successes')}/{q.get('repair_failures')} success/failure.",
        "",
        "Reusable elements are immutable branch/target binding, exact transcript hashes and span validation, future-turn exclusion, sibling-context non-authority, deterministic request/cache accounting, strict response validation, blinding, and explicit failure categories. The old one-issue/signature design is not reused.",
        "",
        "Failed assumptions included forcing rhetoric into a polar fact, collapsing compound accusations, losing a live counterfactual, inferring false rejected targets, diagnosing premise-neutral replies as substitutions, and rejecting direct compound decomposition. The v1 schema covers each through explicit proposition kinds/groups, issue lifecycles, participant commitments, answer-target repair, relation provenance, obligations, abstention, and incremental deltas.",
        "",
        "## Protocol and calibration",
        "",
        f"Ledger schema: `proposition-ledger-v1.0.0`, SHA-256 `{ledger_schema_sha256}`. Four-arm protocol draft SHA-256 `{protocol_sha256}`.",
        "",
        f"The private transcript-only calibration pack contains {calibration_manifest['conversation_count']} already exposed Grade-A conversations and {calibration_manifest['target_prefix_count']} target prefixes; pack identity `{calibration_manifest['pack_content_sha256']}`. Synthetic fixture count: {schema_validation['synthetic_fixtures']['fixture_count']}.",
        "",
        "Phase 2 must freeze the split, model profiles, prompts, schemas, budgets, randomisation, rubric, thresholds, and blinding sequence before any paid call. It should then score ledger construction, continuity reasoning, and reply composition separately across transcript-only, equal-budget summary, machine-ledger, and smaller human-corrected-ledger arms.",
        "",
        "## Remaining uncertainty",
        "",
        "- No authoritative real-conversation gold proposition annotations exist.",
        "- Three benchmark quote-parent fields required explicit later-source reconciliation; three native benchmark conversations and eight prospective conversations remain Grade C.",
        "- Historical replay target corpora do not contain complete downstream conversations.",
        "- Cross-family author identity cannot be established from the incompatible pseudonym schemes.",
        "- Prior QUD scored-sheet filenames requested for discovery are absent; retained score CSVs are blank, so later recurrence metadata is the conservative human-review exposure authority.",
        "- The final held-out set was not selected or opened.",
        "",
        "## Integrity boundary",
        "",
        f"Schema validation passed: **{schema_validation['passed']}**. Privacy validation passed: **{privacy['passed']}**. No provider/model or X call, production write, runtime import, service change, merge, deployment, generated experimental reply, or real-conversation machine ledger occurred.",
    ]
    return lines


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


def _build_outputs(args: argparse.Namespace, manifest: Mapping[str, Any]) -> dict[str, Any]:
    project_dir = args.project_dir.resolve()
    private_output = args.private_output.resolve()
    _ensure_private_directory(private_output)
    key_path = private_output / "private-author-key"
    if not key_path.is_file() or key_path.is_symlink() or key_path.stat().st_size != 32 or stat.S_IMODE(key_path.stat().st_mode) != 0o600:
        raise Phase1Error("private output must already contain a mode-0600 32-byte private-author-key")
    inventory, before_hashes = _inventory_sources(manifest)
    overlap = _build_overlap(manifest)
    feasibility_rows, feasibility_summary, exposure_rows, exposure_summary, turns_by_conversation = _build_feasibility_and_exposure(manifest)
    calibration_records, calibration_manifest, calibration_index = _build_calibration_pack(
        manifest, feasibility_rows, turns_by_conversation
    )
    source_manifest_hash = sha256_file(args.source_manifest.resolve())
    exposure_registry_payload = _jsonl_payload(exposure_rows)
    exposure_registry_hash = sha256_bytes(exposure_registry_payload)
    protocol = _build_protocol(source_manifest_hash, exposure_registry_hash)
    schema_validation = _schema_validation(project_dir, protocol)
    if not schema_validation["passed"]:
        raise Phase1Error("schema or synthetic-fixture validation failed")
    _write_json(private_output / "source-inventory.json", inventory)
    _write_markdown(private_output / "source-inventory.md", _inventory_markdown(inventory))
    _write_json(private_output / "source-overlap.json", overlap)
    _write_markdown(private_output / "source-overlap.md", _overlap_markdown(overlap))
    _write_jsonl(private_output / "conversation-feasibility-index.jsonl", feasibility_rows)
    _write_json(private_output / "corpus-feasibility.json", feasibility_summary)
    _write_markdown(private_output / "corpus-feasibility.md", _feasibility_markdown(feasibility_summary))
    _write_private(private_output / "prior-exposure-registry.jsonl", exposure_registry_payload)
    _write_json(private_output / "prior-exposure-summary.json", exposure_summary)
    _write_jsonl(private_output / "calibration-pack/calibration-records.jsonl", calibration_records)
    _write_json(private_output / "calibration-pack/calibration-index.json", calibration_index)
    _write_json(private_output / "calibration-pack/manifest.json", calibration_manifest)
    _write_json(private_output / "experiment-protocol-draft.json", protocol)
    _write_markdown(private_output / "experiment-protocol-draft.md", _protocol_markdown(protocol))
    _write_json(private_output / "schema-validation.json", schema_validation)
    privacy = _privacy_validation(project_dir, private_output, manifest)
    if not privacy["passed"]:
        raise Phase1Error("privacy validation failed")
    protocol_sha256 = sha256_file(private_output / "experiment-protocol-draft.json")
    report_lines = _phase1_report(
        inventory,
        overlap,
        feasibility_summary,
        exposure_summary,
        protocol_sha256,
        schema_validation["ledger_schema_sha256"],
        calibration_manifest,
        schema_validation,
        privacy,
        manifest,
    )
    _write_markdown(private_output / "phase1-report.md", report_lines)
    run_manifest = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "run_kind": "proposition-ledger-phase1-evidence-only",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "private_output": str(private_output),
        "verified_origin_master_sha": manifest.get("verified_origin_master_sha"),
        "prospective_batch_resolved_once": manifest.get("prospective_batch_resolved_once"),
        "prospective_batch_manifest_sha256": manifest.get("prospective_batch_manifest_sha256"),
        "source_manifest_path": str(args.source_manifest.resolve()),
        "source_manifest_sha256": source_manifest_hash,
        "cutoff": args.cutoff,
        "network_calls": 0,
        "provider_calls": 0,
        "x_calls": 0,
        "model_generated_real_ledgers": 0,
        "model_generated_experimental_replies": 0,
        "production_writes": 0,
    }
    _write_json(private_output / "run-manifest.json", run_manifest)
    _write_sha256s(private_output)
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
        "exposure_counts": exposure_summary["conversation_status_counts"],
        "protocol_sha256": protocol_sha256,
        "ledger_schema_sha256": schema_validation["ledger_schema_sha256"],
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
    feasibility_rows, feasibility_summary, exposure_rows, exposure_summary, turns_by_conversation = _build_feasibility_and_exposure(manifest)
    calibration_records, calibration_manifest, calibration_index = _build_calibration_pack(
        manifest,
        feasibility_rows,
        turns_by_conversation,
    )
    exposure_payload = _jsonl_payload(exposure_rows)
    protocol = _build_protocol(
        sha256_file(args.source_manifest.resolve()),
        sha256_bytes(exposure_payload),
    )
    schema_validation = _schema_validation(project_dir, protocol)
    expected_json = {
        "source-inventory.json": inventory,
        "source-overlap.json": overlap,
        "corpus-feasibility.json": feasibility_summary,
        "prior-exposure-summary.json": exposure_summary,
        "calibration-pack/calibration-index.json": calibration_index,
        "calibration-pack/manifest.json": calibration_manifest,
        "experiment-protocol-draft.json": protocol,
        "schema-validation.json": schema_validation,
    }
    expected_jsonl = {
        "conversation-feasibility-index.jsonl": feasibility_rows,
        "prior-exposure-registry.jsonl": exposure_rows,
        "calibration-pack/calibration-records.jsonl": calibration_records,
    }
    errors: list[str] = []
    for relative, expected in expected_json.items():
        if canonical_json_bytes(_read_json(private_output / relative)) != canonical_json_bytes(expected):
            errors.append(relative)
    for relative, expected in expected_jsonl.items():
        if _jsonl_payload(_read_jsonl(private_output / relative)) != _jsonl_payload(expected):
            errors.append(relative)
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
