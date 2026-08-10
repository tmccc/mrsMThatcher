#!/usr/bin/env python3
"""Build a deterministic review shortlist from a reply reconstruction corpus.

This tool deliberately performs only conservative, evidence-linked normalisation and
lexical shortlisting.  Historical outcomes are baseline evidence, not quality labels,
and the resulting shortlist is not a final adjudicated evaluation set.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Sequence


SCHEMA_VERSION = 1
TOOL_VERSION = "reply-evaluation-pool-v1"
NORMALISATION_VERSION = "reply-candidate-normalisation-v1"
SOURCE_SCHEMA_VERSION = 2
SOURCE_TOOL_VERSION = "reply-history-reconstruction-v2"
SOURCE_EXTRACTOR_COMMIT = "bdb6a5b18468bbda02f2c908f8a7699601ee5d18"
SNOWFLAKE_EPOCH_MS = 1288834974657
SUPPORTED_LANES = {"mention", "quote-tweet", "hot-post"}
ELIGIBLE_OUTCOMES = {
    "posted",
    "editorial_no_reply",
    "deterministic_rejection",
}
OUTPUT_FILES = [
    "run_manifest.json",
    "input_verification.json",
    "normalised_candidates.jsonl",
    "normalisation_actions.jsonl",
    "excluded_candidates.jsonl",
    "evaluation_eligible_candidates.jsonl",
    "input_text_clusters.jsonl",
    "shortlist_pairs.jsonl",
    "shortlist_cases.jsonl",
    "shortlist_review.md",
    "manual_selection_template.csv",
    "normalisation_report.md",
    "SHA256SUMS",
]
CHECKSUMMED_SOURCE_FILES = (
    "run_manifest.json",
    "conversational_candidates.jsonl",
    "reply_quality_inventory.json",
)
EXACT_CORPUS_HASHES = {
    "run_manifest.json": "60e540a73a52fc962c26209fa5e8847eff28d56be7292f039675020db59b443b",
    "conversational_candidates.jsonl": "2c8c81c15e53137734951fdf6d40914a3fbe737734533a1fc57002e6f4f6e238",
    "reply_quality_inventory.json": "a922c682940eed0bdcf63d1e6dbae5e43efb21c3ac11fd8a0828ad6b6b17fa52",
}
EXACT_GATE = {
    "input_candidates": 589,
    "excluded_invalid_identities": 3,
    "normalised_candidates": 586,
    "outcome": {
        "posted": 98,
        "editorial_no_reply": 139,
        "deterministic_rejection": 40,
        "operational_failure": 1,
        "unresolved": 308,
    },
    "status": {"complete": 278, "partial": 308, "ambiguous": 0},
    "outcome_changes": 6,
}
GENERIC_REASON = "no_usable_reply_generated"
GENERIC_EVENT_KINDS = {
    "legacy_editorial_no_reply",
    "mention_grok_skip",
    "hot_post_reply_grok_skip",
}
GENERIC_SKIP_EVENT_KINDS = {"candidate_skipped", "legacy_candidate_skipped"}
REPETITION_REASONS = {"exact_duplicate_reply", "near_duplicate_reply"}
ATTEMPT_SCOPED_FIELDS = {
    "mode",
    "tone",
    "reviewer_verdict",
    "model_call_count",
    "revision_count",
    "no_reply_reason",
    "deterministic_rejection_reason",
}
ATTEMPT_METADATA_FIELDS = ATTEMPT_SCOPED_FIELDS - {"no_reply_reason", "deterministic_rejection_reason"}
STRATA = (
    "formulaic_substantive_posted",
    "civil_challenge_or_disagreement",
    "genuine_social_courtesy",
    "factual_or_historical_question",
    "safe_wit_opportunity",
    "justified_safety_no_reply",
)
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by",
    "for", "from", "had", "has", "have", "he", "her", "hers", "him",
    "his", "i", "in", "is", "it", "its", "me", "my", "of", "on",
    "or", "our", "ours", "she", "so", "that", "the", "their", "them",
    "they", "this", "to", "us", "was", "we", "were", "will", "with",
    "you", "your", "yours",
}


class PoolBuildError(RuntimeError):
    """A safe, user-facing refusal."""


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def pretty_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_iso_utc(value: str, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise PoolBuildError(f"{label} must be an ISO-8601 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise PoolBuildError(f"{label} is not a valid ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo != timezone.utc:
        raise PoolBuildError(f"{label} must be UTC")
    return parsed


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def validate_paths(corpus: Path, output: Path, worktree: Path) -> tuple[Path, Path, Path]:
    corpus = corpus.resolve(strict=True)
    output = output.resolve(strict=False)
    worktree = worktree.resolve(strict=True)
    if not corpus.is_dir():
        raise PoolBuildError(f"corpus is not a directory: {corpus}")
    if is_within(output, corpus):
        raise PoolBuildError("output path must not be inside the source corpus")
    if is_within(output, worktree):
        raise PoolBuildError("output path must not be inside the Git worktree")
    if output.exists() and not output.is_dir():
        raise PoolBuildError("output path exists and is not a directory")
    if output.exists() and any(output.iterdir()):
        raise PoolBuildError("output directory must be new or empty")
    return corpus, output, worktree


def parse_sha256sums(data: bytes) -> dict[str, str]:
    """Parse GNU-style unescaped SHA256SUMS entries without resolving any path."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PoolBuildError("SHA256SUMS is not valid UTF-8") from exc
    result: dict[str, str] = {}
    line_re = re.compile(r"^([0-9a-fA-F]{64}) ([ *])(.+)$")
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line:
            continue
        match = line_re.fullmatch(line)
        if not match:
            raise PoolBuildError(f"malformed SHA256SUMS entry on line {line_number}")
        digest, _marker, raw_name = match.groups()
        path = Path(raw_name)
        if path.is_absolute() or raw_name in {".", ".."} or ".." in path.parts:
            raise PoolBuildError(f"unsafe checksum path on line {line_number}: {raw_name!r}")
        if (
            raw_name != path.as_posix()
            or raw_name.startswith("./")
            or "//" in raw_name
            or "\\" in raw_name
            or any(ord(character) < 32 for character in raw_name)
        ):
            raise PoolBuildError(f"malformed checksum path on line {line_number}: {raw_name!r}")
        if raw_name in result:
            raise PoolBuildError(f"duplicate checksum path: {raw_name}")
        result[raw_name] = digest.lower()
    return result


def verify_source(corpus: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, str], dict[str, Any]]:
    allowed = set(CHECKSUMMED_SOURCE_FILES) | {"SHA256SUMS"}
    for name in allowed:
        path = corpus / name
        if not path.is_file():
            raise PoolBuildError(f"required corpus input is missing: {name}")

    sums_bytes = (corpus / "SHA256SUMS").read_bytes()
    recorded = parse_sha256sums(sums_bytes)
    actual: dict[str, str] = {}
    for name in CHECKSUMMED_SOURCE_FILES:
        if name not in recorded:
            raise PoolBuildError(f"SHA256SUMS has no entry for required input: {name}")
        actual[name] = sha256_file(corpus / name)
        if actual[name] != recorded[name]:
            raise PoolBuildError(
                f"checksum mismatch for {name}: recorded {recorded[name]}, actual {actual[name]}"
            )

    try:
        manifest = json.loads((corpus / "run_manifest.json").read_bytes())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PoolBuildError("run_manifest.json is not valid JSON") from exc
    required_manifest = {
        "schema_version": SOURCE_SCHEMA_VERSION,
        "tool_version": SOURCE_TOOL_VERSION,
        "extractor_git_commit": SOURCE_EXTRACTOR_COMMIT,
        "extractor_git_commit_confidence": "exact",
        "live_project_included": False,
    }
    for field, expected in required_manifest.items():
        if manifest.get(field) != expected:
            raise PoolBuildError(
                f"source manifest {field} mismatch: expected {expected!r}, got {manifest.get(field)!r}"
            )
    if not isinstance(manifest.get("selected_snapshots"), list) or len(manifest["selected_snapshots"]) != 32:
        raise PoolBuildError("source manifest must contain exactly 32 selected_snapshots")

    try:
        inventory = json.loads((corpus / "reply_quality_inventory.json").read_bytes())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PoolBuildError("reply_quality_inventory.json is not valid JSON") from exc
    if not isinstance(inventory, dict):
        raise PoolBuildError("reply_quality_inventory.json must contain a JSON object")

    verification = {
        "verified_files": [
            {"path": name, "recorded_sha256": recorded[name], "actual_sha256": actual[name], "verified": True}
            for name in CHECKSUMMED_SOURCE_FILES
        ],
        "checksum_policy": "Only files read by this tool were hashed; large record files were neither opened nor hashed.",
        "manifest_requirements": {
            **required_manifest,
            "selected_snapshots_count": 32,
        },
        "sha256sums_entry_count": len(recorded),
    }
    return manifest, inventory, actual | {"SHA256SUMS": sha256_bytes(sums_bytes)}, verification


def source_lines(path: Path) -> list[tuple[bytes, dict[str, Any]]]:
    result: list[tuple[bytes, dict[str, Any]]] = []
    with path.open("rb") as handle:
        for line_number, with_ending in enumerate(handle, 1):
            if with_ending.endswith(b"\r\n"):
                exact = with_ending[:-2]
            elif with_ending.endswith((b"\n", b"\r")):
                exact = with_ending[:-1]
            else:
                exact = with_ending
            if not exact:
                raise PoolBuildError(f"blank candidate JSONL line at {line_number}")
            try:
                row = json.loads(exact)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise PoolBuildError(f"invalid candidate JSON on line {line_number}") from exc
            if not isinstance(row, dict):
                raise PoolBuildError(f"candidate line {line_number} is not a JSON object")
            result.append((exact, row))
    return result


def decode_target_snowflake(row: dict[str, Any], tolerance_hours: float) -> dict[str, Any]:
    raw_target = row.get("target_id")
    diagnostic: dict[str, Any] = {
        "target_id": raw_target,
        "first_timestamp": row.get("first_timestamp"),
        "snowflake_tolerance_hours": tolerance_hours,
        "target_id_decimal": False,
        "decoded_target_timestamp": None,
        "absolute_time_delta_hours": None,
        "valid_for_candidate_time": False,
    }
    target_text = str(raw_target) if not isinstance(raw_target, bool) else ""
    if not re.fullmatch(r"[0-9]+", target_text):
        return diagnostic
    diagnostic["target_id_decimal"] = True
    try:
        target_int = int(target_text)
        decoded_ms = (target_int >> 22) + SNOWFLAKE_EPOCH_MS
        decoded = datetime.fromtimestamp(decoded_ms / 1000, tz=timezone.utc)
        first = parse_iso_utc(row.get("first_timestamp"), "candidate first_timestamp")
    except (OverflowError, OSError, ValueError, PoolBuildError):
        return diagnostic
    delta = abs((decoded - first).total_seconds()) / 3600
    diagnostic["decoded_target_timestamp"] = decoded.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    diagnostic["absolute_time_delta_hours"] = round(delta, 9)
    diagnostic["valid_for_candidate_time"] = delta <= tolerance_hours
    return diagnostic


def event_is_placed(event: dict[str, Any]) -> bool:
    return event.get("chronology_status") == "placed"


def event_is_generic_wrapper(event: dict[str, Any]) -> bool:
    if event.get("is_generic_wrapper") is True:
        return True
    kind = event.get("event_kind")
    reason = event.get("reason")
    if kind in GENERIC_EVENT_KINDS:
        return True
    return kind in GENERIC_SKIP_EVENT_KINDS and reason == GENERIC_REASON


def event_is_specific(event: dict[str, Any]) -> bool:
    return (
        event_is_placed(event)
        and bool(event.get("is_specific"))
        and not event_is_generic_wrapper(event)
        and event.get("reason") != GENERIC_REASON
    )


def event_sort_key(event: dict[str, Any]) -> tuple[datetime, int, str]:
    try:
        timestamp = parse_iso_utc(event.get("timestamp"), "terminal event timestamp")
    except PoolBuildError:
        timestamp = datetime.min.replace(tzinfo=timezone.utc)
    order = event.get("order_index") if isinstance(event.get("order_index"), int) else -1
    return timestamp, order, str(event.get("evidence_id") or "")


def event_domains(event: dict[str, Any]) -> set[tuple[str, str]]:
    domains: set[tuple[str, str]] = set()
    for location in event.get("evidence_locations") or []:
        if (
            isinstance(location, dict)
            and isinstance(location.get("ordering_domain"), str)
            and isinstance(location.get("source_identity"), str)
        ):
            domains.add((location["ordering_domain"], location["source_identity"]))
    return domains


def wrapper_is_close(specific: dict[str, Any], wrapper: dict[str, Any]) -> bool:
    if wrapper.get("is_generic_wrapper") is True:
        return True
    if not (event_domains(specific) & event_domains(wrapper)):
        return False
    try:
        earlier = parse_iso_utc(specific.get("timestamp"), "specific event timestamp")
        later = parse_iso_utc(wrapper.get("timestamp"), "wrapper event timestamp")
    except PoolBuildError:
        return False
    delta = (later - earlier).total_seconds()
    return 0 <= delta <= 5


def action_record(
    candidate_id: str,
    rule: str,
    field: str,
    old_value: Any,
    new_value: Any,
    evidence_ids: Iterable[str | None],
    explanation: str,
) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "rule": rule,
        "field": field,
        "old_value": old_value,
        "new_value": new_value,
        "evidence_ids": [item for item in evidence_ids if item],
        "explanation": explanation,
    }


def repetition_override(row: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    history = row.get("terminal_attempt_history") or []
    placed = [event for event in history if isinstance(event, dict) and event_is_placed(event)]
    repetition = [
        event
        for event in placed
        if event.get("outcome") == "deterministic_rejection"
        and event.get("reason") in REPETITION_REASONS
        and event_is_specific(event)
    ]
    if row.get("outcome") != "editorial_no_reply" or not repetition:
        return row.get("outcome"), None
    specific = max(repetition, key=event_sort_key)
    later = [event for event in placed if event_sort_key(event) > event_sort_key(specific)]
    if any(event.get("outcome") == "posted" for event in later):
        return row.get("outcome"), None
    for event in later:
        if event.get("outcome") == "operational_failure":
            continue
        if not event_is_generic_wrapper(event) or not wrapper_is_close(specific, event):
            return row.get("outcome"), None
    return "deterministic_rejection", specific


def latest_specific_event(row: dict[str, Any], outcome: str) -> dict[str, Any] | None:
    events = [
        event
        for event in (row.get("terminal_attempt_history") or [])
        if isinstance(event, dict) and event.get("outcome") == outcome and event_is_specific(event)
    ]
    return max(events, key=event_sort_key) if events else None


def resolve_conflict(
    row: dict[str, Any], conflict: dict[str, Any], outcome: str
) -> tuple[bool, Any, list[str], str]:
    values = conflict.get("values")
    evidence_ids = conflict.get("evidence_ids")
    if not isinstance(values, list) or not isinstance(evidence_ids, list) or len(values) != len(evidence_ids):
        return False, None, [], "malformed value/evidence arrays were not inferred"
    final_id = row.get("final_outcome_evidence_id")
    direct_indexes = [index for index, evidence_id in enumerate(evidence_ids) if evidence_id == final_id]
    if len(direct_indexes) == 1:
        index = direct_indexes[0]
        return True, values[index], [evidence_ids[index]], "value linked directly to final_outcome_evidence_id"
    terminal = latest_specific_event(row, outcome)
    if terminal:
        terminal_id = terminal.get("evidence_id")
        terminal_indexes = [index for index, evidence_id in enumerate(evidence_ids) if evidence_id == terminal_id]
        if len(terminal_indexes) == 1:
            index = terminal_indexes[0]
            return True, values[index], [evidence_ids[index]], "value linked to latest placed specific final-attempt event"
        if conflict.get("field") in {"no_reply_reason", "deterministic_rejection_reason"}:
            reason = terminal.get("reason")
            matching = [index for index, value in enumerate(values) if value == reason]
            if len(matching) == 1:
                index = matching[0]
                return True, values[index], [evidence_ids[index], terminal_id], "reason matches latest placed specific final-attempt event"
    return False, None, [], "no unique final-attempt value could be selected"


def supported_terminal_reason(row: dict[str, Any], outcome: str) -> tuple[Any, list[str]]:
    event = latest_specific_event(row, outcome)
    if event:
        return event.get("reason"), [event.get("evidence_id")]
    if outcome == "editorial_no_reply" and row.get("no_reply_reason"):
        # Some early legacy rows contain only the terminal generator disposition.
        return row.get("no_reply_reason"), [row.get("final_outcome_evidence_id")]
    if outcome == "deterministic_rejection" and row.get("deterministic_rejection_reason"):
        return row.get("deterministic_rejection_reason"), [row.get("final_outcome_evidence_id")]
    return None, []


def normalise_candidate(
    original: dict[str, Any], source_hash: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    row = deepcopy(original)
    candidate_id = str(row.get("candidate_id") or "")
    actions: list[dict[str, Any]] = []
    row["source_row_sha256"] = source_hash
    row["original_outcome"] = original.get("outcome")
    row["original_reconstruction_status"] = original.get("reconstruction_status")
    row["original_no_reply_reason"] = original.get("no_reply_reason")
    row["original_deterministic_rejection_reason"] = original.get("deterministic_rejection_reason")
    row["original_raw_reasons"] = deepcopy(original.get("raw_reasons") or [])
    for field in sorted(ATTEMPT_METADATA_FIELDS):
        row[f"original_{field}"] = deepcopy(original.get(field))

    outcome, repetition_event = repetition_override(row)
    if repetition_event:
        actions.append(
            action_record(
                candidate_id,
                "specific_repetition_rejection_outranks_generic_wrappers",
                "outcome",
                original.get("outcome"),
                outcome,
                [repetition_event.get("evidence_id")]
                + [event.get("evidence_id") for event in row.get("terminal_attempt_history") or [] if event_is_generic_wrapper(event)],
                "A placed exact/near-duplicate rejection is followed only by supported close generic wrappers.",
            )
        )
    row["normalised_outcome"] = outcome

    remaining: list[dict[str, Any]] = []
    conflict_by_field: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for conflict in original.get("conflict_evidence") or []:
        if isinstance(conflict, dict):
            conflict_by_field[str(conflict.get("field"))].append(conflict)
        else:
            remaining.append(deepcopy(conflict))

    selected_attempt_values: dict[str, Any] = {}
    for field, conflicts in conflict_by_field.items():
        if field not in ATTEMPT_SCOPED_FIELDS or len(conflicts) != 1:
            remaining.extend(deepcopy(conflicts))
            continue
        conflict = conflicts[0]
        resolved, value, evidence_ids, explanation = resolve_conflict(row, conflict, outcome)
        if not resolved:
            old_value = row.get(field)
            row[field] = None
            remaining.append(deepcopy(conflict))
            actions.append(
                action_record(
                    candidate_id,
                    "final_attempt_metadata_unresolved",
                    field,
                    old_value,
                    None,
                    conflict.get("evidence_ids") if isinstance(conflict.get("evidence_ids"), list) else [],
                    explanation + "; the attempt-scoped field remains null and ambiguous.",
                )
            )
            continue
        old_value = original.get(field)
        selected_attempt_values[field] = value
        row[field] = value
        rule = "generic_no_reply_reason_is_not_conflict" if field == "no_reply_reason" else "final_attempt_metadata_follows_evidence"
        actions.append(
            action_record(
                candidate_id,
                rule,
                field,
                old_value,
                value,
                evidence_ids,
                explanation,
            )
        )

    normalised_no_reply = selected_attempt_values.get("no_reply_reason", original.get("no_reply_reason"))
    normalised_rejection = selected_attempt_values.get(
        "deterministic_rejection_reason", original.get("deterministic_rejection_reason")
    )
    if outcome == "editorial_no_reply":
        selected_reason, evidence_ids = supported_terminal_reason(row, outcome)
        if selected_reason is not None and selected_reason != normalised_no_reply:
            actions.append(
                action_record(
                    candidate_id,
                    "latest_specific_no_reply_reason",
                    "no_reply_reason",
                    normalised_no_reply,
                    selected_reason,
                    evidence_ids,
                    "Selected the latest placed, non-generic, specific terminal reason for the final editorial attempt.",
                )
            )
        normalised_no_reply = selected_reason
        normalised_rejection = None
    elif outcome == "deterministic_rejection":
        selected_reason, evidence_ids = supported_terminal_reason(row, outcome)
        if repetition_event:
            selected_reason = repetition_event.get("reason")
            evidence_ids = [repetition_event.get("evidence_id")]
        if selected_reason is not None and selected_reason != normalised_rejection:
            actions.append(
                action_record(
                    candidate_id,
                    "specific_deterministic_rejection_reason",
                    "deterministic_rejection_reason",
                    normalised_rejection,
                    selected_reason,
                    evidence_ids,
                    "Selected the placed specific deterministic rejection reason for the final outcome.",
                )
            )
        normalised_rejection = selected_reason
        normalised_no_reply = None
    else:
        normalised_no_reply = None
        normalised_rejection = None

    row["normalised_no_reply_reason"] = normalised_no_reply
    row["normalised_deterministic_rejection_reason"] = normalised_rejection
    failure_reason, _failure_evidence = supported_terminal_reason(row, "operational_failure")
    row["normalised_operational_failure_reason"] = failure_reason if outcome == "operational_failure" else None
    for field in sorted(ATTEMPT_METADATA_FIELDS):
        row[f"normalised_{field}"] = deepcopy(row.get(field))
    row["remaining_conflict_evidence"] = remaining

    status = "partial"
    has_identity = bool(row.get("incoming_text")) and row.get("lane") in SUPPORTED_LANES
    terminal_resolved = outcome in {"posted", "editorial_no_reply", "deterministic_rejection", "operational_failure"}
    if remaining:
        status = "ambiguous"
    elif has_identity and terminal_resolved:
        if outcome == "posted" and row.get("actual_reply_text") and row.get("reply_post_id"):
            status = "complete"
        elif outcome == "editorial_no_reply" and normalised_no_reply:
            status = "complete"
        elif outcome == "deterministic_rejection" and normalised_rejection:
            status = "complete"
        elif outcome == "operational_failure" and row["normalised_operational_failure_reason"]:
            status = "complete"
    row["normalised_reconstruction_status"] = status
    eligible = (
        status == "complete"
        and outcome in ELIGIBLE_OUTCOMES
        and bool(row.get("incoming_text"))
        and not remaining
        and (outcome != "posted" or bool(row.get("actual_reply_text")))
    )
    row["evaluation_eligibility"] = {
        "eligible": eligible,
        "basis": "complete supported conversational terminal candidate with no unresolved conflict" if eligible else "excluded, incomplete, operational, unresolved, or ambiguous",
    }
    row["normalisation_actions"] = actions
    row["suggested_strata"] = []
    return row, actions


def lexical_tokens(text: Any) -> list[str]:
    if not isinstance(text, str):
        return []
    return re.findall(r"[^\W_]+", text.casefold(), flags=re.UNICODE)


def lexical_normalise(text: Any) -> str:
    return " ".join(lexical_tokens(text))


class UnionFind:
    def __init__(self, items: Sequence[str]) -> None:
        self.parent = {item: item for item in items}

    def find(self, item: str) -> str:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return
        first, second = sorted((left_root, right_root))
        self.parent[second] = first


def cluster_inputs(rows: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    ids = sorted(str(row["candidate_id"]) for row in rows)
    row_by_id = {str(row["candidate_id"]): row for row in rows}
    norms = {candidate_id: lexical_normalise(row_by_id[candidate_id].get("incoming_text")) for candidate_id in ids}
    union = UnionFind(ids)
    edges: list[tuple[str, str, float, str]] = []
    exact_seen: dict[str, str] = {}
    for candidate_id in ids:
        norm = norms[candidate_id]
        if norm in exact_seen:
            union.union(candidate_id, exact_seen[norm])
            edges.append((exact_seen[norm], candidate_id, 1.0, "exact_normalised"))
        else:
            exact_seen[norm] = candidate_id
    for left_index, left in enumerate(ids):
        for right in ids[left_index + 1 :]:
            if norms[left] == norms[right]:
                continue
            ratio = SequenceMatcher(None, norms[left], norms[right], autojunk=False).ratio()
            if ratio >= 0.92:
                union.union(left, right)
                edges.append((left, right, ratio, "near_duplicate_sequence_matcher_gte_0.92"))
    components: dict[str, list[str]] = defaultdict(list)
    for candidate_id in ids:
        components[union.find(candidate_id)].append(candidate_id)
    cluster_rows: list[dict[str, Any]] = []
    mapping: dict[str, str] = {}
    for members in sorted((sorted(value) for value in components.values()), key=lambda value: value[0]):
        cluster_id = "input-cluster-" + sha256_bytes("\n".join(members).encode("utf-8"))[:20]
        member_set = set(members)
        component_edges = [
            {"left_candidate_id": left, "right_candidate_id": right, "ratio": round(ratio, 6), "basis": basis}
            for left, right, ratio, basis in edges
            if left in member_set and right in member_set
        ]
        for candidate_id in members:
            mapping[candidate_id] = cluster_id
        cluster_rows.append(
            {
                "cluster_id": cluster_id,
                "candidate_ids": members,
                "size": len(members),
                "lexical_normalisations": sorted({norms[item] for item in members}),
                "edges": component_edges,
                "method": "connected components of exact lowercase alphanumeric token matches and SequenceMatcher ratio >= 0.92; lexical clustering is not semantic equivalence",
            }
        )
    return cluster_rows, mapping


def inventory_formulaic_ids(inventory: dict[str, Any]) -> tuple[set[str], dict[str, list[str]]]:
    ids: set[str] = set()
    basis: dict[str, list[str]] = defaultdict(list)
    collections: list[tuple[str, Any]] = [
        ("candidate_formulaic_clusters", inventory.get("candidate_formulaic_clusters")),
        ("exact_duplicate_groups", inventory.get("exact_duplicate_groups")),
        ("normalised_duplicate_groups", inventory.get("normalised_duplicate_groups")),
    ]
    near = inventory.get("near_duplicate_groups")
    if isinstance(near, dict):
        for threshold, groups in near.items():
            collections.append((f"near_duplicate_groups[{threshold}]", groups))
    for source, groups in collections:
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("candidate_ids"), list):
                continue
            for candidate_id in group["candidate_ids"]:
                if isinstance(candidate_id, str):
                    ids.add(candidate_id)
                    basis[candidate_id].append(source)
    return ids, {key: sorted(set(value)) for key, value in basis.items()}


def contains_phrase(text: str, phrases: Iterable[str]) -> list[str]:
    padded = f" {lexical_normalise(text)} "
    return sorted(phrase for phrase in phrases if f" {phrase} " in padded)


def safety_category(reason: Any) -> str | None:
    value = lexical_normalise(reason)
    categories = (
        ("repetition", ("exact duplicate", "near duplicate", "repetition", "repeated")),
        ("spam", ("spam", "not worth replying")),
        ("abuse_or_harassment", ("abuse", "abusive", "harass", "insult")),
        ("obvious_bait", ("bait", "provocation", "troll")),
        ("dangerous_amplification", ("dangerous", "amplification", "extreme proposal")),
        ("serious_unsupported_allegation", ("unsupported allegation", "unverified claim", "unverifiable claim", "unverified claims")),
        ("conspiracy_claim", ("conspiracy",)),
        ("incoherent_material", ("incoherent", "gibberish", "nonsense")),
        ("wholly_unrelated_material", ("wholly unrelated", "unrelated", "off topic")),
    )
    for category, phrases in categories:
        if any(phrase in value for phrase in phrases):
            return category
    return None


def selected_reason(row: dict[str, Any]) -> Any:
    if row.get("normalised_outcome") == "deterministic_rejection":
        return row.get("normalised_deterministic_rejection_reason")
    if row.get("normalised_outcome") == "editorial_no_reply":
        return row.get("normalised_no_reply_reason")
    return None


def make_match(stratum: str, score: int, hits: list[str], misses: list[str], components: dict[str, int]) -> dict[str, Any]:
    return {
        "stratum": stratum,
        "score": score,
        "rule_hits": hits,
        "rule_misses": misses,
        "rank_basis": {
            "score_components": components,
            "ordering": "descending score, dynamic diversity preferences, then candidate_id ascending",
            "stochastic_components": False,
        },
    }


def score_candidate(
    row: dict[str, Any], formulaic_ids: set[str], formulaic_basis: dict[str, list[str]]
) -> list[dict[str, Any]]:
    if not row.get("evaluation_eligibility", {}).get("eligible"):
        return []
    candidate_id = str(row["candidate_id"])
    incoming = str(row.get("incoming_text") or "")
    reply = str(row.get("actual_reply_text") or "")
    incoming_norm = lexical_normalise(incoming)
    reply_norm = lexical_normalise(reply)
    tokens = lexical_tokens(incoming)
    content_tokens = [token for token in tokens if token not in STOP_WORDS]
    outcome = row.get("normalised_outcome")
    mode = str(row.get("mode") or "")
    reason = selected_reason(row)
    unsafe_category = safety_category(reason)
    matches: list[dict[str, Any]] = []

    social_phrases = {
        "thank", "thanks", "grateful", "appreciate", "welcome", "hello", "hi",
        "love", "lovely", "wonderful", "brilliant", "great", "good luck", "sorry",
        "sympathy", "support", "agree", "quite right", "well done", "best wishes",
    }
    social_hits = contains_phrase(incoming, social_phrases)
    argument_markers = {
        "because", "therefore", "however", "but", "if", "when", "should", "must",
        "consensus", "trust", "service", "cycle", "pioneer", "irony", "whereas", "although",
    }
    argument_hits = sorted(set(tokens) & argument_markers)
    question = "?" in incoming
    comparison = bool(re.search(r"\b(?:more|less|better|worse|than|like|unlike|compared|versus|vs)\b", incoming_norm))
    contrast_punctuation = any(mark in incoming for mark in ("->", "→", "—", ";", ":"))
    substantive_flags = {
        "at_least_14_non_stop_tokens": len(content_tokens) >= 14,
        "at_least_100_characters": len(incoming) >= 100,
        "argument_contrast_or_analogy_marker": bool(argument_hits),
        "question_or_explicit_comparison": question or comparison,
        "arrow_or_contrast_punctuation": contrast_punctuation,
    }
    merely_social = bool(social_hits) and len(content_tokens) < 14 and len(incoming) < 100 and not argument_hits and not question and not comparison

    stock_phrases = {
        "well noted", "is noted", "is welcome", "is well made", "thank you for the observation",
        "much appreciated", "appreciated", "kind words", "a fair observation", "principles endure",
    }
    stock_hits = contains_phrase(reply, stock_phrases)
    formulaic = candidate_id in formulaic_ids
    if outcome == "posted" and incoming and reply and (formulaic or stock_hits) and any(substantive_flags.values()) and not merely_social:
        components = {
            "inventory_formulaic": 6 if formulaic else 0,
            "stock_acknowledgement": 4 if stock_hits else 0,
            "substantive_signals": sum(substantive_flags.values()),
        }
        hits = ([f"reply_inventory:{source}" for source in formulaic_basis.get(candidate_id, [])]
                + [f"stock_acknowledgement:{phrase}" for phrase in stock_hits]
                + [name for name, present in substantive_flags.items() if present])
        misses = [name for name, present in substantive_flags.items() if not present]
        matches.append(make_match(STRATA[0], sum(components.values()), hits, misses, components))

    challenge_terms = {
        "disagree", "wrong", "however", "but", "although", "yet", "why", "how", "should", "must",
        "recommend", "consider", "challenge", "criticism", "criticise", "criticize", "fair", "justice",
        "moral", "policy", "government", "political", "freedom", "responsibility", "rights", "tax",
    }
    challenge_hits = sorted(set(tokens) & challenge_terms)
    civil = not bool(re.search(r"\b(?:idiot|moron|stupid|hate|kill|die|scum|traitor)\b", incoming_norm))
    challenge_signal = question or bool(challenge_hits) or comparison
    solely_unsafe = unsafe_category in {"abuse_or_harassment", "obvious_bait", "dangerous_amplification"} and not (question or comparison)
    if challenge_signal and civil and not solely_unsafe:
        components = {"civil": 2, "question": 2 if question else 0, "challenge_terms": min(5, len(challenge_hits)), "comparison": 1 if comparison else 0}
        hits = ["civil_lexical_screen"] + (["question_mark"] if question else []) + [f"challenge_term:{term}" for term in challenge_hits] + (["explicit_comparison"] if comparison else [])
        misses = [] if outcome in {"posted", "editorial_no_reply", "deterministic_rejection"} else ["eligible_historical_outcome"]
        matches.append(make_match(STRATA[1], sum(components.values()), hits, misses, components))

    substantive_social = question or comparison or bool(argument_hits) or len(content_tokens) >= 14 or len(incoming) >= 100
    if social_hits and not substantive_social:
        components = {"social_phrases": min(5, len(social_hits) * 2), "short_contribution": 3 if len(incoming) <= 100 else 0, "historical_outcome_mix_aid": 1}
        hits = [f"social_content:{phrase}" for phrase in social_hits]
        if len(incoming) <= 100:
            hits.append("relatively_short_contribution")
        matches.append(make_match(STRATA[2], sum(components.values()), hits, ["substantive_question_analogy_or_policy_argument"], components))

    direct_question = bool(re.search(r"(?:^|\s)(?:who|what|when|where|which|how many|how much|did|does|do|was|were|is|are|can you tell|could you explain)\b", incoming_norm))
    factual_terms = {
        "who", "what", "when", "where", "which", "year", "date", "history", "historical", "number",
        "many", "much", "did", "does", "was", "were", "record", "fact", "evidence", "source", "quote",
    }
    factual_hits = sorted(set(tokens) & factual_terms)
    motive_value = bool(re.search(r"\b(?:why|should|opinion|believe|feel|better|worse|right|wrong|moral)\b", incoming_norm))
    factual_construction = question or direct_question
    empirically_answerable = bool(factual_hits) and (not motive_value or len(factual_hits) >= 2)
    if factual_construction and empirically_answerable:
        components = {
            "direct_factual_answer_mode": 10 if mode == "direct_factual_answer" else 0,
            "question_mark": 3 if question else 0,
            "direct_question_construction": 2 if direct_question else 0,
            "factual_lexical_cues": min(5, len(factual_hits)),
            "motive_or_value_penalty": -2 if motive_value else 0,
        }
        hits = (["historical_mode:direct_factual_answer"] if mode == "direct_factual_answer" else []) + (["question_mark"] if question else []) + (["direct_question_construction"] if direct_question else []) + [f"factual_cue:{term}" for term in factual_hits]
        misses = ["motive_or_value_cues_absent"] if motive_value else []
        matches.append(make_match(STRATA[3], sum(components.values()), hits, misses, components))

    exclusion_terms = {
        "grief", "grieving", "bereaved", "suicide", "distress", "assault", "murder", "rape", "abuse",
        "accusation", "accusations", "accuse", "accused", "allegation", "allegations", "fraud", "criminal",
    }
    serious = bool(set(tokens) & exclusion_terms) or unsafe_category in {"abuse_or_harassment", "dangerous_amplification", "serious_unsupported_allegation"}
    wit_terms = {"irony", "ironic", "paradox", "cycle", "like", "unlike", "but", "yet", "whereas", "joke", "funny", "humour", "humor", "wry", "absurd"}
    wit_hits = sorted(set(tokens) & wit_terms)
    punchy = len(incoming) <= 100 and bool(re.search(r"[!;:—]|\b(?:never|always)\b", incoming))
    rich_formulaic = formulaic and (len(incoming) >= 100 or len(content_tokens) >= 14)
    mode_wit = mode in {"wry_reply", "light_humour"}
    if civil and not serious and (wit_hits or contrast_punctuation or punchy or rich_formulaic or mode_wit):
        components = {
            "historical_wit_mode": 6 if mode_wit else 0,
            "wit_lexical_signals": min(5, len(wit_hits) * 2),
            "contrast_or_incongruity_punctuation": 2 if contrast_punctuation else 0,
            "compact_punchy_claim": 2 if punchy else 0,
            "rich_input_with_formulaic_reply": 4 if rich_formulaic else 0,
        }
        hits = ([f"historical_mode:{mode}"] if mode_wit else []) + [f"wit_lexical_evidence:{term}" for term in wit_hits]
        if contrast_punctuation:
            hits.append("contrast_or_incongruity_punctuation")
        if punchy:
            hits.append("compact_punchy_claim")
        if rich_formulaic:
            hits.append("formulaic_reply_to_lexically_rich_contribution")
        matches.append(make_match(STRATA[4], sum(components.values()), hits, ["grief_distress_serious_accusation_or_abuse"], components))

    if outcome in {"editorial_no_reply", "deterministic_rejection"} and unsafe_category:
        components = {"specific_supported_reason": 6, "reason_diversity_category": 3, "deterministic_rejection": 1 if outcome == "deterministic_rejection" else 0}
        hits = [f"specific_reason_category:{unsafe_category}", f"historical_outcome:{outcome}"]
        matches.append(make_match(STRATA[5], sum(components.values()), hits, [], components))
    return matches


def select_shortlist(
    rows: Sequence[dict[str, Any]], cluster_by_candidate: dict[str, str], per_stratum: int
) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for stratum in STRATA:
        candidates = []
        for row in rows:
            match = next((item for item in row.get("suggested_strata") or [] if item["stratum"] == stratum), None)
            if match:
                candidates.append((row, match))
        selected: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
        used_clusters: set[str] = set()
        author_counts: Counter[str] = Counter()
        lane_counts: Counter[str] = Counter()
        era_counts: Counter[str] = Counter()
        outcome_counts: Counter[str] = Counter()
        reason_counts: Counter[str] = Counter()
        remaining = list(candidates)
        while remaining and len(selected) < per_stratum:
            options = []
            for row, match in remaining:
                candidate_id = str(row["candidate_id"])
                cluster = cluster_by_candidate[candidate_id]
                author = str(row.get("author_id")) if row.get("author_id") is not None else None
                if cluster in used_clusters or (author is not None and author_counts[author] >= 2):
                    continue
                lane = str(row.get("lane") or "")
                era = str(row.get("prompt_era_id") or "")
                outcome = str(row.get("normalised_outcome") or "")
                reason = safety_category(selected_reason(row)) or ""
                bonuses = {
                    "new_lane": 3 if lane_counts[lane] == 0 else 0,
                    "new_prompt_era": 2 if era_counts[era] == 0 else 0,
                    "new_historical_outcome": 2 if outcome_counts[outcome] == 0 else 0,
                    "new_safety_reason_category": 4 if stratum == STRATA[5] and reason_counts[reason] == 0 else 0,
                }
                effective = int(match["score"]) + sum(bonuses.values())
                options.append(((-effective, -int(match["score"]), candidate_id), row, match, bonuses))
            if not options:
                break
            _key, row, match, bonuses = min(options, key=lambda item: item[0])
            candidate_id = str(row["candidate_id"])
            cluster = cluster_by_candidate[candidate_id]
            author = str(row.get("author_id")) if row.get("author_id") is not None else None
            lane = str(row.get("lane") or "")
            era = str(row.get("prompt_era_id") or "")
            outcome = str(row.get("normalised_outcome") or "")
            reason = safety_category(selected_reason(row)) or ""
            selection_basis = {
                "base_score": match["score"],
                "diversity_bonuses": bonuses,
                "effective_selection_score": int(match["score"]) + sum(bonuses.values()),
                "input_cluster_limit": "at most one",
                "author_limit": "at most two when author_id is available",
                "final_tie_breaker": candidate_id,
            }
            selected.append((row, match, selection_basis))
            used_clusters.add(cluster)
            if author is not None:
                author_counts[author] += 1
            lane_counts[lane] += 1
            era_counts[era] += 1
            outcome_counts[outcome] += 1
            reason_counts[reason] += 1
            remaining = [(candidate, item) for candidate, item in remaining if candidate is not row]
        for rank, (row, match, selection_basis) in enumerate(selected, 1):
            pairs.append(
                {
                    "stratum": stratum,
                    "provisional_rank": rank,
                    "candidate_id": row["candidate_id"],
                    "input_cluster_id": cluster_by_candidate[str(row["candidate_id"])],
                    "author_id": row.get("author_id"),
                    "lane": row.get("lane"),
                    "prompt_era_id": row.get("prompt_era_id"),
                    "historical_outcome": row.get("normalised_outcome"),
                    "incoming_text": row.get("incoming_text"),
                    "historical_reply_text": row.get("actual_reply_text"),
                    "historical_no_reply_reason": selected_reason(row),
                    "score": match["score"],
                    "rule_hits": match["rule_hits"],
                    "rule_misses": match["rule_misses"],
                    "rank_basis": match["rank_basis"],
                    "selection_basis": selection_basis,
                }
            )
    return pairs


def output_cases(pairs: Sequence[dict[str, Any]], rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pair in pairs:
        by_id[str(pair["candidate_id"])].append(pair)
    rows_by_id = {str(row["candidate_id"]): row for row in rows}
    result = []
    for candidate_id in sorted(by_id):
        row = deepcopy(rows_by_id[candidate_id])
        row["shortlist_memberships"] = [
            {"stratum": pair["stratum"], "provisional_rank": pair["provisional_rank"], "score": pair["score"]}
            for pair in sorted(by_id[candidate_id], key=lambda item: (item["stratum"], item["provisional_rank"]))
        ]
        result.append(row)
    return result


def output_git_provenance(tool_path: Path, worktree: Path) -> dict[str, Any]:
    commit = None
    confidence = "unavailable"
    try:
        rev = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        commit = rev.stdout.strip()
        status_result = subprocess.run(
            ["git", "-C", str(worktree), "status", "--porcelain", "--untracked-files=all", "--", str(tool_path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        confidence = "exact" if not status_result.stdout.strip() else "worktree_modified"
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "running_tool_source_sha256": sha256_file(tool_path),
        "running_tool_git_commit": commit,
        "running_tool_git_commit_confidence": confidence,
    }


def create_output_directory(output: Path) -> None:
    if output.exists():
        if any(output.iterdir()):
            raise PoolBuildError("output directory became non-empty before creation")
        output.chmod(0o700)
    else:
        output.mkdir(mode=0o700, parents=False)
    if stat.S_IMODE(output.stat().st_mode) != 0o700:
        raise PoolBuildError("could not establish output directory mode 0700")


def exclusive_write(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise PoolBuildError(f"output file does not have mode 0600: {path.name}")


def jsonl_bytes(rows: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(canonical_json(row) for row in rows)


def csv_bytes(pairs: Sequence[dict[str, Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    fields = [
        "stratum", "provisional_rank", "candidate_id", "lane", "prompt_era_id",
        "historical_outcome", "selected", "final_stratum", "reviewer_note",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for pair in pairs:
        writer.writerow({
            "stratum": pair["stratum"],
            "provisional_rank": pair["provisional_rank"],
            "candidate_id": pair["candidate_id"],
            "lane": pair["lane"],
            "prompt_era_id": pair["prompt_era_id"],
            "historical_outcome": pair["historical_outcome"],
            "selected": "",
            "final_stratum": "",
            "reviewer_note": "",
        })
    return buffer.getvalue().encode("utf-8")


def indent_private_text(value: Any) -> str:
    text = str(value or "")
    return "\n".join("    " + line for line in text.splitlines()) or "    (none)"


def shortlist_review(pairs: Sequence[dict[str, Any]], counts: dict[str, int]) -> bytes:
    lines = [
        "# Provisional reply evaluation shortlist",
        "",
        "**Historical outcome is evidence, not a desired answer.**",
        "",
        "**Shortlist stratum is provisional.**",
        "",
        "**No model-generated comparison has yet occurred.**",
        "",
        "Input clusters use exact lowercase alphanumeric token matches and SequenceMatcher connected components at 0.92. This is lexical clustering, not semantic equivalence.",
        "",
    ]
    for stratum in STRATA:
        lines.extend([f"## {stratum}", "", f"Provisional cases: {counts.get(stratum, 0)}", ""])
        for pair in [item for item in pairs if item["stratum"] == stratum]:
            lines.extend([
                f"### {pair['provisional_rank']}. {pair['candidate_id']}",
                "",
                f"- Lane: {pair.get('lane')}",
                f"- Prompt era: {pair.get('prompt_era_id')}",
                f"- Historical outcome: {pair.get('historical_outcome')}",
                f"- Score: {pair.get('score')}",
                f"- Rule hits: {', '.join(pair.get('rule_hits') or [])}",
                "- Incoming contribution:",
                "",
                indent_private_text(pair.get("incoming_text")),
                "",
                "- Historical reply (if posted):",
                "",
                indent_private_text(pair.get("historical_reply_text")),
                "",
            ])
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def coverage(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "lanes": dict(sorted(Counter(str(row.get("lane")) for row in rows).items())),
        "prompt_eras": dict(sorted(Counter(str(row.get("prompt_era_id")) for row in rows).items())),
        "historical_outcomes": dict(sorted(Counter(str(row.get("normalised_outcome")) for row in rows).items())),
    }


def report_markdown(
    before_outcomes: Counter[str], before_status: Counter[str], after_outcomes: Counter[str],
    after_status: Counter[str], excluded: Sequence[dict[str, Any]], actions: Sequence[dict[str, Any]],
    eligible_count: int, shortlist_counts: dict[str, int], overlap_rows: dict[str, list[str]],
    shortlist_coverage: dict[str, Any], failures: Sequence[str],
) -> bytes:
    outcome_changes = [action for action in actions if action["field"] == "outcome"]
    resolved_fields = [action for action in actions if action["rule"] in {"generic_no_reply_reason_is_not_conflict", "final_attempt_metadata_follows_evidence"}]
    outcome_names = sorted(
        set(before_outcomes) | set(after_outcomes) | {"posted", "editorial_no_reply", "deterministic_rejection", "operational_failure", "unresolved"}
    )
    status_names = sorted(set(before_status) | set(after_status) | {"complete", "partial", "ambiguous"})
    before_outcome_counts = {name: before_outcomes.get(name, 0) for name in outcome_names}
    after_outcome_counts = {name: after_outcomes.get(name, 0) for name in outcome_names}
    before_status_counts = {name: before_status.get(name, 0) for name in status_names}
    after_status_counts = {name: after_status.get(name, 0) for name in status_names}
    excluded_ambiguities = sum(row.get("reconstruction_status") == "ambiguous" for row in excluded)
    remaining = int(after_status.get("ambiguous", 0))
    resolved_ambiguities = int(before_status.get("ambiguous", 0)) - excluded_ambiguities - remaining
    lines = [
        "# Reply evaluation pool normalisation report", "",
        "This is a deterministic review shortlist, not a final or frozen evaluation set.", "",
        "## Counts", "",
        f"- Input candidates: {sum(before_outcomes.values())}",
        f"- Normalised candidates: {sum(after_outcomes.values())}",
        f"- Outcomes before: `{json.dumps(before_outcome_counts, sort_keys=True)}`",
        f"- Outcomes after: `{json.dumps(after_outcome_counts, sort_keys=True)}`",
        f"- Status before: `{json.dumps(before_status_counts, sort_keys=True)}`",
        f"- Status after: `{json.dumps(after_status_counts, sort_keys=True)}`",
        f"- Excluded identities: {len(excluded)}",
        f"- Resolved ambiguous candidates: {resolved_ambiguities}",
        f"- Remaining ambiguous candidates: {remaining}",
        f"- Eligible candidates: {eligible_count}", "",
        "## Exclusions", "",
    ]
    if excluded:
        lines.extend(f"- {row.get('candidate_id')}: {row.get('excluded_reason')}" for row in excluded)
    else:
        lines.append("- None")
    lines.extend(["", "## Outcome changes", ""])
    if outcome_changes:
        lines.extend(f"- {action['candidate_id']}: {action['old_value']} -> {action['new_value']} ({action['rule']})" for action in outcome_changes)
    else:
        lines.append("- None")
    lines.extend(["", "## Resolved conflict fields", ""])
    if resolved_fields:
        lines.extend(f"- {action['candidate_id']}: {action['field']} ({action['rule']})" for action in resolved_fields)
    else:
        lines.append("- None")
    lines.extend(["", "## Remaining conflicts", "", f"- Ambiguous candidates: {remaining}", "", "## Shortlist", ""])
    lines.extend(f"- {stratum}: {shortlist_counts.get(stratum, 0)}" for stratum in STRATA)
    lines.extend(["", f"- Cross-stratum candidate overlap count: {len(overlap_rows)}", ""])
    if overlap_rows:
        lines.extend(f"- {candidate_id}: {', '.join(strata)}" for candidate_id, strata in sorted(overlap_rows.items()))
    lines.extend(["", "## Prompt-era and lane coverage", "", f"- Lanes: `{json.dumps(shortlist_coverage['lanes'], sort_keys=True)}`", f"- Prompt eras: `{json.dumps(shortlist_coverage['prompt_eras'], sort_keys=True)}`", "", "## Gate diagnostics", ""])
    lines.extend([f"- {failure}" for failure in failures] or ["- All applicable gates passed."])
    lines.extend([
        "", "## Limitations", "",
        "- Suggested strata are lexical review aids, not semantic or quality judgements.",
        "- Lexical clustering can join surface-similar inputs and miss paraphrases.",
        "- Historical outcomes describe baseline behaviour and are not desired answers.",
        "- Safety and wit strata require human review before any evaluation set is frozen.",
    ])
    return ("\n".join(lines) + "\n").encode("utf-8")


def exact_gate_failures(
    exact_corpus: bool, input_count: int, excluded_count: int, normalised_count: int,
    outcomes: Counter[str], statuses: Counter[str], actions: Sequence[dict[str, Any]],
) -> list[str]:
    if not exact_corpus:
        return []
    observed = {
        "input_candidates": input_count,
        "excluded_invalid_identities": excluded_count,
        "normalised_candidates": normalised_count,
        "outcome_changes": sum(action["field"] == "outcome" for action in actions),
    }
    failures = []
    for field in ("input_candidates", "excluded_invalid_identities", "normalised_candidates", "outcome_changes"):
        if observed[field] != EXACT_GATE[field]:
            failures.append(f"exact corpus {field}: expected {EXACT_GATE[field]}, observed {observed[field]}")
    for name, expected in EXACT_GATE["outcome"].items():
        if outcomes.get(name, 0) != expected:
            failures.append(f"exact corpus outcome {name}: expected {expected}, observed {outcomes.get(name, 0)}")
    for name, expected in EXACT_GATE["status"].items():
        if statuses.get(name, 0) != expected:
            failures.append(f"exact corpus status {name}: expected {expected}, observed {statuses.get(name, 0)}")
    return failures


def build(args: argparse.Namespace) -> tuple[Path, dict[str, Any], list[str]]:
    tool_path = Path(__file__).resolve()
    worktree = tool_path.parents[1]
    corpus, output, worktree = validate_paths(Path(args.corpus), Path(args.output), worktree)
    parse_iso_utc(args.created_at, "--created-at")
    if args.shortlist_per_stratum < 1:
        raise PoolBuildError("--shortlist-per-stratum must be at least 1")
    if args.snowflake_tolerance_hours < 0:
        raise PoolBuildError("--snowflake-tolerance-hours must not be negative")
    source_manifest, inventory, source_hashes, input_verification = verify_source(corpus)
    lines = source_lines(corpus / "conversational_candidates.jsonl")
    before_outcomes = Counter(str(row.get("outcome")) for _line, row in lines)
    before_status = Counter(str(row.get("reconstruction_status")) for _line, row in lines)

    excluded: list[dict[str, Any]] = []
    normalised: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    for exact_line, original in lines:
        row_hash = sha256_bytes(exact_line)
        diagnostic = decode_target_snowflake(original, args.snowflake_tolerance_hours)
        if not diagnostic["valid_for_candidate_time"]:
            excluded_row = deepcopy(original)
            excluded_row["source_row_sha256"] = row_hash
            excluded_row["excluded_reason"] = "invalid_target_snowflake_for_candidate_time"
            excluded_row["snowflake_diagnostic"] = diagnostic
            excluded.append(excluded_row)
            continue
        row, row_actions = normalise_candidate(original, row_hash)
        normalised.append(row)
        actions.extend(row_actions)

    after_outcomes = Counter(str(row.get("normalised_outcome")) for row in normalised)
    after_status = Counter(str(row.get("normalised_reconstruction_status")) for row in normalised)
    eligible = [row for row in normalised if row["evaluation_eligibility"]["eligible"]]
    cluster_rows, cluster_mapping = cluster_inputs(eligible)
    formulaic_ids, formulaic_basis = inventory_formulaic_ids(inventory)
    for row in normalised:
        row["suggested_strata"] = score_candidate(row, formulaic_ids, formulaic_basis)
    pairs = select_shortlist(eligible, cluster_mapping, args.shortlist_per_stratum)
    cases = output_cases(pairs, normalised)
    shortlist_counts = {stratum: sum(pair["stratum"] == stratum for pair in pairs) for stratum in STRATA}
    membership: dict[str, list[str]] = defaultdict(list)
    for pair in pairs:
        membership[str(pair["candidate_id"])].append(str(pair["stratum"]))
    overlap_rows = {candidate_id: sorted(strata) for candidate_id, strata in membership.items() if len(strata) > 1}
    shortlisted_rows = [next(row for row in normalised if row["candidate_id"] == candidate_id) for candidate_id in sorted(membership)]
    shortlist_coverage = coverage(shortlisted_rows)
    exact_corpus = all(source_hashes.get(name) == digest for name, digest in EXACT_CORPUS_HASHES.items())
    failures = exact_gate_failures(
        exact_corpus, len(lines), len(excluded), len(normalised), after_outcomes, after_status, actions
    )
    for stratum in STRATA:
        if shortlist_counts[stratum] < 8:
            failures.append(f"shortlist {stratum}: requires at least 8, observed {shortlist_counts[stratum]}")
    if exact_corpus:
        target_ids = sorted(str(row.get("target_id")) for row in excluded)
        if target_ids != ["100", "910", "910"]:
            failures.append(f"exact corpus excluded target IDs differ: observed {target_ids!r}")

    action_counts = Counter(action["rule"] for action in actions)
    unique_count = len(membership)
    overlap_count = len(overlap_rows)
    outcome_names = sorted(
        set(before_outcomes) | set(after_outcomes) | {"posted", "editorial_no_reply", "deterministic_rejection", "operational_failure", "unresolved"}
    )
    status_names = sorted(set(before_status) | set(after_status) | {"complete", "partial", "ambiguous"})
    outcome_counts_before = {name: before_outcomes.get(name, 0) for name in outcome_names}
    outcome_counts_after = {name: after_outcomes.get(name, 0) for name in outcome_names}
    status_counts_before = {name: before_status.get(name, 0) for name in status_names}
    status_counts_after = {name: after_status.get(name, 0) for name in status_names}
    provenance = output_git_provenance(tool_path, worktree)
    input_verification.update({
        "source_corpus": str(corpus),
        "source_manifest_sha256": source_hashes["run_manifest.json"],
        "source_candidate_file_sha256": source_hashes["conversational_candidates.jsonl"],
        "source_inventory_sha256": source_hashes["reply_quality_inventory.json"],
        "large_record_files_opened": False,
    })
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "normalisation_version": NORMALISATION_VERSION,
        "arguments": {
            "corpus": str(Path(args.corpus)),
            "output": str(Path(args.output)),
            "created_at": args.created_at,
            "shortlist_per_stratum": args.shortlist_per_stratum,
            "snowflake_tolerance_hours": args.snowflake_tolerance_hours,
        },
        "source_corpus_path": str(corpus),
        "source_manifest_sha256": source_hashes["run_manifest.json"],
        "source_candidate_file_sha256": source_hashes["conversational_candidates.jsonl"],
        "source_inventory_sha256": source_hashes["reply_quality_inventory.json"],
        "source_extractor_commit": source_manifest["extractor_git_commit"],
        "counts": {
            "input_candidates": len(lines),
            "excluded_candidates": len(excluded),
            "normalised_candidates": len(normalised),
            "evaluation_eligible_candidates": len(eligible),
            "unique_shortlisted_candidates": unique_count,
            "cross_stratum_overlap_candidates": overlap_count,
        },
        "outcome_counts_before": outcome_counts_before,
        "outcome_counts_after": outcome_counts_after,
        "status_counts_before": status_counts_before,
        "status_counts_after": status_counts_after,
        "normalisation_action_counts_by_rule": dict(sorted(action_counts.items())),
        "shortlist_counts_by_stratum": shortlist_counts,
        "shortlist_coverage": shortlist_coverage,
        "created_at": args.created_at,
        "output_inventory": OUTPUT_FILES,
        "suitable_for_freezing": not failures,
        "diagnostic_failures": failures,
        "diagnostic_candidate_ids": {
            "excluded": [row.get("candidate_id") for row in excluded],
            "outcome_changes": sorted({action["candidate_id"] for action in actions if action["field"] == "outcome"}),
            "remaining_ambiguities": [
                row.get("candidate_id") for row in normalised if row.get("normalised_reconstruction_status") == "ambiguous"
            ],
        },
        "diagnostic_action_summaries": [
            {"candidate_id": action["candidate_id"], "rule": action["rule"], "field": action["field"]}
            for action in actions
        ],
        **provenance,
    }

    payloads = {
        "run_manifest.json": pretty_json(manifest),
        "input_verification.json": pretty_json(input_verification),
        "normalised_candidates.jsonl": jsonl_bytes(normalised),
        "normalisation_actions.jsonl": jsonl_bytes(actions),
        "excluded_candidates.jsonl": jsonl_bytes(excluded),
        "evaluation_eligible_candidates.jsonl": jsonl_bytes(eligible),
        "input_text_clusters.jsonl": jsonl_bytes(cluster_rows),
        "shortlist_pairs.jsonl": jsonl_bytes(pairs),
        "shortlist_cases.jsonl": jsonl_bytes(cases),
        "shortlist_review.md": shortlist_review(pairs, shortlist_counts),
        "manual_selection_template.csv": csv_bytes(pairs),
        "normalisation_report.md": report_markdown(
            before_outcomes, before_status, after_outcomes, after_status, excluded, actions,
            len(eligible), shortlist_counts, overlap_rows, shortlist_coverage, failures,
        ),
    }
    old_umask = os.umask(0o077)
    try:
        create_output_directory(output)
        for name in OUTPUT_FILES:
            if name == "SHA256SUMS":
                continue
            exclusive_write(output / name, payloads[name])
        checksum_lines = [f"{sha256_bytes(payloads[name])}  {name}\n" for name in OUTPUT_FILES if name != "SHA256SUMS"]
        exclusive_write(output / "SHA256SUMS", "".join(checksum_lines).encode("ascii"))
    finally:
        os.umask(old_umask)
    return output, manifest, failures


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--corpus", required=True, help="immutable reconstruction corpus directory")
    result.add_argument("--output", required=True, help="new or empty private output directory")
    result.add_argument("--created-at", required=True, help="fixed ISO-8601 UTC creation timestamp")
    result.add_argument("--shortlist-per-stratum", type=int, default=12)
    result.add_argument("--snowflake-tolerance-hours", type=float, default=24)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        output, manifest, failures = build(args)
    except (PoolBuildError, FileExistsError, PermissionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    counts = manifest["counts"]
    print(
        f"wrote provisional shortlist to {output}: "
        f"input={counts['input_candidates']} excluded={counts['excluded_candidates']} "
        f"normalised={counts['normalised_candidates']} eligible={counts['evaluation_eligible_candidates']}"
    )
    print("shortlist counts: " + ", ".join(f"{key}={value}" for key, value in manifest["shortlist_counts_by_stratum"].items()))
    if failures:
        print("run is unsuitable for freezing:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        diagnostics = manifest["diagnostic_candidate_ids"]
        print("candidate ID diagnostics: " + json.dumps(diagnostics, sort_keys=True), file=sys.stderr)
        print(
            "normalisation action diagnostics: "
            + json.dumps(manifest["diagnostic_action_summaries"], sort_keys=True),
            file=sys.stderr,
        )
        return 2
    print("all normalisation and shortlist gates passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
