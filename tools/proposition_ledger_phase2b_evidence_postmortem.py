#!/usr/bin/env python3
"""Offline Phase 2B post-mortem for the frozen Phase 2A provider responses.

The module is inert on import and has no provider, HTTP, gRPC, X, or production
imports.  Its command-line build is deliberately bound to the already exposed
Phase 2A private run.  It verifies that run's complete ``SHA256SUMS`` manifest
before reading any response artefact, diagnoses exact-text/coordinate failures,
and runs explicitly labelled counterfactual materialisations without modifying
the source run.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import stat
import sys
import tempfile
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from statistics import median
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
AUTHORIZED_SOURCE_RUN = Path(
    "/disks/disk1/research/private-runs/"
    "proposition-ledger-phase2a-development-pilot-resumed-20260901T112911Z"
)
EXPECTED_SOURCE_SUMS_SHA256 = (
    "49db1009e2352f1837237887819813b3460ee4bea040f68aff38091c5cd8e5c0"
)
EXPECTED_ATTEMPTED_RESPONSES = 21
EXPECTED_PARSED_RESPONSES = 20
EXPECTED_MATERIALISED_RESPONSES = 7
EXPECTED_CANONICAL_SCHEMA_SHA256 = (
    "eea15c28f5019cea405bfdd65b924ee18c76dc428f16a1fe502592b5cb953d8a"
)
CANONICAL_SCHEMA_VERSION = "proposition-ledger-semantic-delta-v1.1.0"
POSTMORTEM_VERSION = "proposition-ledger-phase2b-evidence-postmortem-v1"
RECOVERY_METHOD = "posthoc_unique_exact_text_resolution"
RECOVERY_LABEL = "diagnostic_counterfactual_only"
PHASE2A_RESULT = (
    "phase2a_development_pilot_completed_with_profile_attrition_review_pending"
)

CANONICAL_SCHEMA_PATH = (
    PROJECT_DIR
    / "proposition_ledger_research/schema/"
    "proposition-ledger-semantic-delta-v1.schema.json"
)
PERSISTED_SCHEMA_PATH = (
    PROJECT_DIR
    / "proposition_ledger_research/schema/proposition-ledger-v1.schema.json"
)
PHASE2A_PROMPT_PATH = (
    PROJECT_DIR
    / "proposition_ledger_research/phase2a/incremental-ledger-system-prompt.txt"
)
PHASE2B_PROMPT_PATH = (
    PROJECT_DIR
    / "proposition_ledger_research/phase2b/"
    "incremental-ledger-system-prompt-v2.txt"
)
TRANSPORT_MANIFEST_PATH = (
    PROJECT_DIR
    / "proposition_ledger_research/phase2b/transport-contract-manifest.json"
)
TRANSPORT_SCHEMA_PATH = (
    PROJECT_DIR
    / "proposition_ledger_research/schema/"
    "proposition-ledger-xai-transport-delta-v2.schema.json"
)
TRANSPORT_MODULE_PATH = PROJECT_DIR / "tools/proposition_ledger_evidence_transport.py"

PROFILE_MODELS = {
    "xai-grok-4.3-low-ledger-v1": "grok-4.3",
    "xai-grok-4.6-low-ledger-v1": "grok-4.6",
}
VALIDATION_STAGES = (
    "strict_json",
    "provider_schema",
    "intended_canonical",
    "binding",
    "evidence_span",
    "semantic_reference",
    "materialisation",
    "persisted_ledger",
)
EVIDENCE_FAILURE_TAXONOMY = (
    "wrong_current_turn_reference",
    "start_or_end_not_integer",
    "boolean_used_as_integer",
    "negative_start",
    "non_positive_length",
    "end_out_of_bounds",
    "exact_text_not_present",
    "exact_text_present_once_coordinates_wrong",
    "exact_text_present_multiple_times",
    "duplicate_resolved_span",
    "codepoint_end_inclusive_signature",
    "utf16_code_unit_signature",
    "utf8_byte_offset_signature",
    "whitespace_or_punctuation_difference",
    "normalisation_difference",
    "several_plausible_coordinate_conventions",
    "no_recognised_coordinate_signature",
    "not_assessable_due_to_strict_json_failure",
)
COORDINATE_SIGNATURES = (
    "codepoint_end_inclusive_signature",
    "utf16_code_unit_signature",
    "utf8_byte_offset_signature",
    "several_plausible_coordinate_conventions",
    "no_recognised_coordinate_signature",
)
RECOVERABILITY_CLASSES = (
    "uniquely_recoverable_from_exact_text",
    "recoverable_only_with_occurrence_disambiguation",
    "not_recoverable_exact_text_absent",
    "not_recoverable_unparsed",
    "not_recoverable_other",
)
STRICT_JSON_FAILURE_CATEGORIES = (
    "invalid_utf8",
    "duplicate_member",
    "non_finite_number",
    "code_fence_or_surrounding_prose",
    "multiple_top_level_values",
    "truncated_json",
    "syntactically_invalid_json",
    "empty_response",
    "other_strict_json_failure",
)
FORBIDDEN_PROVIDER_PERSISTENCE_FIELDS = frozenset(
    {
        "ledger_id",
        "ledger_sha256",
        "participants",
        "previous_ledger_sha256",
        "root_post_id",
        "state_patch",
        "state_transitions",
        "turn_refs",
    }
)
SEMANTIC_CHANGE_ARRAYS = (
    "answer_target_changes",
    "commitment_changes",
    "issue_state_updates",
    "new_issue_states",
    "new_proposition_groups",
    "new_propositions",
    "new_relations",
    "obligation_changes",
    "proposition_group_updates",
    "proposition_updates",
    "rejected_answer_target_changes",
    "repair_records",
    "resolved_items",
    "warnings",
)
TERMINAL_ISSUE_STATUSES = frozenset(
    {"answered", "superseded", "abandoned", "expired", "no_stable_issue"}
)
SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_ERROR_COUNT = 32
MAX_ERROR_LENGTH = 512

ROOT_ARTIFACT_NAMES = (
    "source-integrity.json",
    "phase2a-response-index.json",
    "evidence-failure-postmortem.json",
    "evidence-failure-postmortem.md",
    "strict-json-failure-diagnostic.json",
    "materialised-no-stable-issue-audit.json",
    "request-payload-size-comparison.json",
)
PREFLIGHT_ARTIFACT_NAMES = (
    "local-sdk-grok-4.3.json",
    "local-sdk-grok-4.6.json",
    "transport-schema-equivalence-audit.json",
    "transport-schema-transformation-ledger.json",
    "transport-schema.json",
    "xai-provider-transport-schema.json",
)
FINAL_ARTIFACT_NAMES = (
    "phase2b-report.md",
    "result-summary.json",
    "validation.json",
)
CHECKSUM_FILE_NAME = "SHA256SUMS"
TRANSPORT_SCHEMA_VERSION = "proposition-ledger-xai-transport-delta-v2.0.0"
TRANSPORT_SCHEMA_FILE_SHA256 = (
    "1fad0addc29f4b09ef567c2b8d08ae86d33427919cfebf8b8ec4236badef2236"
)
XAI_PROVIDER_TRANSPORT_SCHEMA_SHA256 = (
    "3f280218c11bd5d08ab81fcecf4c65a0ca9d1719f6b5456060600aceab1d5bb7"
)
READY_DISPOSITION = "phase2b_evidence_transport_ready_for_synthetic_live_probe"
RESIDUAL_DISPOSITION = (
    "phase2b_evidence_transport_ready_with_residual_exact_text_fidelity_risk"
)


class PostmortemError(RuntimeError):
    """A bounded offline integrity, parsing, or deterministic replay failure."""


class _DuplicateMemberError(ValueError):
    """Internal marker for a duplicate JSON object member."""


class _NonFiniteNumberError(ValueError):
    """Internal marker for a non-finite JSON number."""


def canonical_json_bytes(value: Any) -> bytes:
    """Return the repository's compact deterministic UTF-8 JSON encoding."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def pretty_json_bytes(value: Any) -> bytes:
    """Return stable human-readable UTF-8 JSON with one final newline."""

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


def sha256_bytes(value: bytes) -> str:
    """Return a lowercase SHA-256 digest for bytes."""

    return hashlib.sha256(value).hexdigest()


def value_sha256(value: Any) -> str:
    """Hash a value using deterministic compact JSON."""

    return sha256_bytes(canonical_json_bytes(value))


def diagnostic_counterfactual_metadata() -> dict[str, Any]:
    """Return immutable labels preventing recovery from rewriting Phase 2A."""

    return {
        "counterfactual_label": RECOVERY_LABEL,
        "diagnostic_method": RECOVERY_METHOD,
        "phase2a_experimental_outcome_revised": False,
    }


def research_guardrails() -> dict[str, Any]:
    """Return the fixed non-inference and no-call post-mortem guardrails."""

    return {
        "model_profile_selected": False,
        "model_winner_inferred": False,
        "phase2a_experimental_outcome_revised": False,
        "provider_calls": 0,
    }


def _bounded(values: Iterable[Any]) -> list[str]:
    return [
        str(value).replace("\n", " ")[:MAX_ERROR_LENGTH]
        for value in list(values)[:MAX_ERROR_COUNT]
    ]


def _absolute_lexical(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return Path(os.path.abspath(candidate))


def _require_real_directory(path: str | Path, label: str) -> Path:
    candidate = _absolute_lexical(path)
    try:
        metadata = candidate.lstat()
    except FileNotFoundError as exc:
        raise PostmortemError(f"{label} is missing: {candidate}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise PostmortemError(f"{label} is not a real directory: {candidate}")
    return candidate


def _read_regular_bytes(path: str | Path, label: str) -> bytes:
    candidate = _absolute_lexical(path)
    _require_real_directory(candidate.parent, f"{label} parent")
    try:
        before = candidate.lstat()
    except FileNotFoundError as exc:
        raise PostmortemError(f"{label} is missing: {candidate}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise PostmortemError(f"{label} is not a regular non-symlink file")
    descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise PostmortemError(f"{label} changed while opening")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _load_json(path: str | Path, label: str) -> Any:
    raw = _read_regular_bytes(path, label)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PostmortemError(f"invalid JSON for {label}: {exc}") from exc


def _private_directory(path: str | Path, *, create: bool) -> Path:
    candidate = _absolute_lexical(path)
    if create and not candidate.exists():
        parent = _require_real_directory(candidate.parent, "private-output parent")
        os.mkdir(candidate, 0o700, dir_fd=None)
        del parent
    result = _require_real_directory(candidate, "private-output directory")
    mode = stat.S_IMODE(result.stat().st_mode)
    if mode != 0o700:
        raise PostmortemError(
            f"private-output directory mode must be 0700, found {mode:04o}: {result}"
        )
    return result


def _ensure_private_subdir(path: Path, *, create: bool) -> Path:
    if create and not path.exists():
        parent = _require_real_directory(path.parent, "private subdirectory parent")
        os.mkdir(path, 0o700, dir_fd=None)
        del parent
    result = _require_real_directory(path, "private subdirectory")
    mode = stat.S_IMODE(result.stat().st_mode)
    if mode != 0o700:
        raise PostmortemError(
            f"private subdirectory mode must be 0700, found {mode:04o}: {result}"
        )
    return result


def _atomic_write_private(path: Path, content: bytes) -> None:
    parent = _require_real_directory(path.parent, "private artifact parent")
    if os.path.lexists(path):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise PostmortemError(f"refusing unsafe output target: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(content):
            offset += os.write(descriptor, content[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if os.path.lexists(temporary):
            temporary.unlink()


def _safe_component(value: Any, label: str) -> str:
    text = str(value)
    if not SAFE_COMPONENT_RE.fullmatch(text) or text in {".", ".."}:
        raise PostmortemError(f"unsafe {label}: {text!r}")
    return text


def _parse_sha256sums(raw: bytes) -> list[tuple[str, str]]:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PostmortemError("Phase 2A SHA256SUMS is not UTF-8") from exc
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64})  ([^\x00\r\n]+)", line)
        if match is None:
            raise PostmortemError(
                f"invalid Phase 2A SHA256SUMS line {line_number}"
            )
        digest, relative_text = match.groups()
        relative = Path(relative_text)
        if (
            relative.is_absolute()
            or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise PostmortemError(
                f"unsafe Phase 2A SHA256SUMS path at line {line_number}"
            )
        normalised = relative.as_posix()
        if normalised in seen:
            raise PostmortemError(f"duplicate Phase 2A SHA256SUMS path: {normalised}")
        seen.add(normalised)
        rows.append((normalised, digest))
    if not rows:
        raise PostmortemError("Phase 2A SHA256SUMS is empty")
    return rows


def _source_inventory(source: Path) -> tuple[list[str], list[str]]:
    """Inventory every entry without following links or accepting special files."""

    files: list[str] = []
    directories: list[str] = []
    pending = [source]
    while pending:
        current = pending.pop()
        relative_parent = current.relative_to(source)
        with os.scandir(current) as iterator:
            entries = sorted(iterator, key=lambda item: item.name)
        for entry in entries:
            relative = (relative_parent / entry.name).as_posix()
            if relative.startswith("./"):
                relative = relative[2:]
            if entry.is_symlink():
                raise PostmortemError(
                    f"symlink is forbidden in Phase 2A source run: {relative}"
                )
            if entry.is_dir(follow_symlinks=False):
                directories.append(relative)
                pending.append(Path(entry.path))
            elif entry.is_file(follow_symlinks=False):
                files.append(relative)
            else:
                raise PostmortemError(
                    f"special entry is forbidden in Phase 2A source run: {relative}"
                )
    return sorted(files), sorted(directories)


def verify_source_run(source_run: str | Path = AUTHORIZED_SOURCE_RUN) -> dict[str, Any]:
    """Verify the authorized source manifest and every listed file before use."""

    source = _require_real_directory(source_run, "Phase 2A source run")
    if source != AUTHORIZED_SOURCE_RUN:
        raise PostmortemError(
            "post-mortem source must be the authorized completed Phase 2A run"
        )
    actual_files, actual_directories = _source_inventory(source)
    sums_raw = _read_regular_bytes(source / "SHA256SUMS", "Phase 2A SHA256SUMS")
    sums_sha = sha256_bytes(sums_raw)
    if sums_sha != EXPECTED_SOURCE_SUMS_SHA256:
        raise PostmortemError(
            "Phase 2A SHA256SUMS hash mismatch: "
            f"expected {EXPECTED_SOURCE_SUMS_SHA256}, found {sums_sha}"
        )
    rows = _parse_sha256sums(sums_raw)
    expected_files = sorted(["SHA256SUMS", *(relative for relative, _ in rows)])
    if actual_files != expected_files:
        missing = sorted(set(expected_files).difference(actual_files))
        extra = sorted(set(actual_files).difference(expected_files))
        raise PostmortemError(
            f"Phase 2A source file inventory mismatch: missing={missing}, extra={extra}"
        )
    checked: list[dict[str, str]] = []
    for relative, expected in rows:
        actual = sha256_bytes(
            _read_regular_bytes(source / relative, f"Phase 2A artifact {relative}")
        )
        if actual != expected:
            raise PostmortemError(
                f"Phase 2A checksum mismatch for {relative}: "
                f"expected {expected}, found {actual}"
            )
        checked.append({"path": relative, "sha256": actual})
    complete_tree = [
        {"entry_type": "directory", "path": relative}
        for relative in actual_directories
    ] + [
        {
            "entry_type": "file",
            "path": "SHA256SUMS",
            "sha256": sums_sha,
        },
        *(
            {
                "entry_type": "file",
                "path": item["path"],
                "sha256": item["sha256"],
            }
            for item in checked
        ),
    ]
    complete_tree.sort(key=lambda item: (item["path"], item["entry_type"]))
    return {
        "artifact_evidence": "deterministic checksum verification",
        "all_listed_checksums_passed": True,
        "listed_file_count": len(checked),
        "manifest_entries_sha256": value_sha256(checked),
        "sha256sums_file_sha256": sums_sha,
        "complete_tree_digest": value_sha256(complete_tree),
        "complete_tree_directory_count": len(actual_directories),
        "complete_tree_entry_count": len(complete_tree),
        "complete_tree_regular_file_count": len(actual_files),
        "complete_tree_matches_declared_inventory": True,
        "source_run_authorized": True,
    }


def find_overlapping_occurrences(text: str, exact_text: str) -> list[tuple[int, int]]:
    """Return all exact, potentially overlapping Python-string matches."""

    if not isinstance(text, str) or not isinstance(exact_text, str) or not exact_text:
        return []
    result: list[tuple[int, int]] = []
    cursor = 0
    while cursor <= len(text) - len(exact_text):
        start = text.find(exact_text, cursor)
        if start < 0:
            break
        result.append((start, start + len(exact_text)))
        cursor = start + 1
    return result


def _json_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def iter_evidence_spans(
    value: Any, pointer: str = ""
) -> Iterable[tuple[str, Mapping[str, Any]]]:
    """Yield canonical evidence arrays recursively with stable JSON pointers."""

    if isinstance(value, Mapping):
        spans = value.get("exact_evidence_spans")
        if isinstance(spans, list):
            base = f"{pointer}/{_json_pointer_token('exact_evidence_spans')}"
            for index, span in enumerate(spans):
                if isinstance(span, Mapping):
                    yield f"{base}/{index}", span
        for key, child in value.items():
            if key != "exact_evidence_spans":
                yield from iter_evidence_spans(
                    child, f"{pointer}/{_json_pointer_token(str(key))}"
                )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_evidence_spans(child, f"{pointer}/{index}")


def _unit_boundary_map(text: str, encoding: str) -> dict[int, int]:
    result = {0: 0}
    units = 0
    for index, character in enumerate(text, 1):
        if encoding == "utf-16-le":
            units += len(character.encode(encoding)) // 2
        else:
            units += len(character.encode(encoding))
        result[units] = index
    return result


def _coordinate_slice(
    text: str, start: Any, end: Any
) -> str | None:
    if type(start) is not int or type(end) is not int:  # bool is not accepted
        return None
    if start < 0 or end <= start or end > len(text):
        return None
    return text[start:end]


def _convention_slice(
    text: str, start: Any, end: Any, *, encoding: str
) -> tuple[int, int, str] | None:
    if type(start) is not int or type(end) is not int:
        return None
    boundaries = _unit_boundary_map(text, encoding)
    if start not in boundaries or end not in boundaries:
        return None
    codepoint_start, codepoint_end = boundaries[start], boundaries[end]
    if codepoint_end <= codepoint_start:
        return None
    return codepoint_start, codepoint_end, text[codepoint_start:codepoint_end]


def _content_characters(value: str) -> tuple[str, ...]:
    return tuple(
        character
        for character in value
        if not character.isspace()
        and not unicodedata.category(character).startswith("P")
    )


def _ordered_categories(categories: Iterable[str]) -> list[str]:
    unique = set(categories)
    unknown = unique.difference(EVIDENCE_FAILURE_TAXONOMY)
    if unknown:
        raise PostmortemError(f"unknown evidence taxonomy categories: {sorted(unknown)}")
    return [item for item in EVIDENCE_FAILURE_TAXONOMY if item in unique]


def diagnose_evidence_span(
    span: Mapping[str, Any],
    *,
    current_turn_id: str,
    current_turn_text: str,
    pointer: str = "",
) -> dict[str, Any]:
    """Classify one old canonical span without changing or fuzzing its text."""

    supplied_turn = span.get("turn_id")
    start = span.get("start_char")
    end = span.get("end_char")
    exact_text_value = span.get("exact_text")
    exact_text = exact_text_value if isinstance(exact_text_value, str) else None
    matches = (
        find_overlapping_occurrences(current_turn_text, exact_text)
        if isinstance(exact_text, str) and exact_text
        else []
    )
    categories: list[str] = []
    if supplied_turn != current_turn_id:
        categories.append("wrong_current_turn_reference")
    if isinstance(start, bool) or isinstance(end, bool):
        categories.extend(("start_or_end_not_integer", "boolean_used_as_integer"))
    elif type(start) is not int or type(end) is not int:
        categories.append("start_or_end_not_integer")
    if type(start) is int and start < 0:
        categories.append("negative_start")
    if type(start) is int and type(end) is int and end <= start:
        categories.append("non_positive_length")
    if type(end) is int and end > len(current_turn_text):
        categories.append("end_out_of_bounds")

    canonical_slice = _coordinate_slice(current_turn_text, start, end)
    coordinates_exact = (
        isinstance(exact_text, str) and canonical_slice == exact_text
    )
    if isinstance(exact_text, str) and exact_text:
        if not matches:
            categories.append("exact_text_not_present")
        elif len(matches) == 1 and not coordinates_exact:
            categories.append("exact_text_present_once_coordinates_wrong")
        elif len(matches) > 1:
            categories.append("exact_text_present_multiple_times")
    else:
        categories.append("exact_text_not_present")

    recognised: list[str] = []
    if isinstance(exact_text, str) and exact_text and not coordinates_exact:
        if (
            type(start) is int
            and type(end) is int
            and start >= 0
            and end >= start
            and end < len(current_turn_text)
            and current_turn_text[start : end + 1] == exact_text
        ):
            recognised.append("codepoint_end_inclusive_signature")
        utf16 = _convention_slice(
            current_turn_text, start, end, encoding="utf-16-le"
        )
        if utf16 is not None and utf16[2] == exact_text:
            recognised.append("utf16_code_unit_signature")
        utf8 = _convention_slice(current_turn_text, start, end, encoding="utf-8")
        if utf8 is not None and utf8[2] == exact_text:
            recognised.append("utf8_byte_offset_signature")
        categories.extend(recognised)
        if len(recognised) > 1:
            categories.append("several_plausible_coordinate_conventions")
        elif matches and type(start) is int and type(end) is int and not recognised:
            categories.append("no_recognised_coordinate_signature")

        if canonical_slice is not None and canonical_slice != exact_text:
            if _content_characters(canonical_slice) == _content_characters(
                exact_text
            ):
                categories.append("whitespace_or_punctuation_difference")
            if (
                unicodedata.normalize("NFD", canonical_slice)
                == unicodedata.normalize("NFD", exact_text)
            ):
                categories.append("normalisation_difference")

    exact_bytes = exact_text.encode("utf-8") if isinstance(exact_text, str) else b""
    supplied = {
        "end_char": end,
        "exact_text_length": len(exact_text) if isinstance(exact_text, str) else None,
        "exact_text_sha256": sha256_bytes(exact_bytes),
        "start_char": start,
        "turn_id_matches_current": supplied_turn == current_turn_id,
    }
    return {
        "selector_json_pointer": pointer or "",
        "supplied": supplied,
        "literal_occurrence_analysis": {
            "match_count": len(matches),
            "matches": [
                {
                    "end": match_end,
                    "occurrence_index": index,
                    "start": match_start,
                }
                for index, (match_start, match_end) in enumerate(matches)
            ],
            "unique": len(matches) == 1,
        },
        "original_canonical_span_valid": (
            supplied_turn == current_turn_id and coordinates_exact
        ),
        "diagnostic_categories": _ordered_categories(categories),
        "coordinate_signatures": [
            item for item in COORDINATE_SIGNATURES if item in categories
        ],
    }


def analyse_parsed_evidence(
    parsed: Mapping[str, Any] | None,
    *,
    current_turn_id: str,
    current_turn_text: str,
) -> dict[str, Any]:
    """Diagnose all parsed evidence and derive the bounded recovery class."""

    if parsed is None:
        return {
            "evidence_span_count": 0,
            "evidence_spans": [],
            "response_diagnostic_categories": [
                "not_assessable_due_to_strict_json_failure"
            ],
            "diagnostic_recovery_status": "not_recoverable_unparsed",
        }
    diagnostics = [
        diagnose_evidence_span(
            span,
            current_turn_id=current_turn_id,
            current_turn_text=current_turn_text,
            pointer=pointer,
        )
        for pointer, span in iter_evidence_spans(parsed)
    ]
    resolved_groups: dict[tuple[str, int, int, str], list[int]] = {}
    spans = list(iter_evidence_spans(parsed))
    for index, ((_, span), diagnostic) in enumerate(zip(spans, diagnostics)):
        matches = diagnostic["literal_occurrence_analysis"]["matches"]
        exact_text = span.get("exact_text")
        if len(matches) == 1 and isinstance(exact_text, str):
            parent_array_pointer = diagnostic["selector_json_pointer"].rsplit(
                "/", 1
            )[0]
            key = (
                parent_array_pointer,
                matches[0]["start"],
                matches[0]["end"],
                exact_text,
            )
            resolved_groups.setdefault(key, []).append(index)
    for indexes in resolved_groups.values():
        if len(indexes) > 1:
            for index in indexes:
                categories = diagnostics[index]["diagnostic_categories"]
                diagnostics[index]["diagnostic_categories"] = _ordered_categories(
                    [*categories, "duplicate_resolved_span"]
                )

    if any(
        diagnostic["literal_occurrence_analysis"]["match_count"] == 0
        for diagnostic in diagnostics
    ):
        recovery = "not_recoverable_exact_text_absent"
    elif any(
        diagnostic["literal_occurrence_analysis"]["match_count"] > 1
        for diagnostic in diagnostics
    ):
        recovery = "recoverable_only_with_occurrence_disambiguation"
    elif all(
        diagnostic["literal_occurrence_analysis"]["match_count"] == 1
        for diagnostic in diagnostics
    ):
        recovery = "uniquely_recoverable_from_exact_text"
    else:
        recovery = "not_recoverable_other"
    return {
        "evidence_span_count": len(diagnostics),
        "evidence_spans": diagnostics,
        "response_diagnostic_categories": [],
        "diagnostic_recovery_status": recovery,
    }


def _replace_unique_evidence(
    value: Any, current_turn_id: str, current_turn_text: str
) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, child in value.items():
            if key == "exact_evidence_spans":
                if not isinstance(child, list):
                    raise PostmortemError("expected exact_evidence_spans array")
                replacements: list[dict[str, Any]] = []
                for span in child:
                    if not isinstance(span, Mapping):
                        raise PostmortemError("expected evidence span object")
                    exact_text = span.get("exact_text")
                    if not isinstance(exact_text, str) or not exact_text:
                        raise PostmortemError("unique recovery requires non-empty text")
                    matches = find_overlapping_occurrences(current_turn_text, exact_text)
                    if len(matches) != 1:
                        raise PostmortemError("unique recovery invariant failed")
                    start, end = matches[0]
                    replacements.append(
                        {
                            "end_char": end,
                            "exact_text": exact_text,
                            "start_char": start,
                            "turn_id": current_turn_id,
                        }
                    )
                result[key] = replacements
            else:
                result[key] = _replace_unique_evidence(
                    child, current_turn_id, current_turn_text
                )
        return result
    if isinstance(value, list):
        return [
            _replace_unique_evidence(child, current_turn_id, current_turn_text)
            for child in value
        ]
    return copy.deepcopy(value)


def _mask_evidence(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: (
                {"evidence_array_length": len(child)}
                if key == "exact_evidence_spans" and isinstance(child, list)
                else _mask_evidence(child)
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_mask_evidence(child) for child in value]
    return copy.deepcopy(value)


def build_diagnostic_canonical_delta(
    parsed: Mapping[str, Any],
    *,
    current_turn_id: str,
    current_turn_text: str,
) -> dict[str, Any]:
    """Replace only uniquely located evidence fields in a deep copy."""

    analysis = analyse_parsed_evidence(
        parsed,
        current_turn_id=current_turn_id,
        current_turn_text=current_turn_text,
    )
    if analysis["diagnostic_recovery_status"] != (
        "uniquely_recoverable_from_exact_text"
    ):
        raise PostmortemError("response is not uniquely recoverable")
    recovered = _replace_unique_evidence(parsed, current_turn_id, current_turn_text)
    if _mask_evidence(recovered) != _mask_evidence(parsed):
        raise PostmortemError("non-evidence semantic fields changed during recovery")
    return recovered


def _duplicate_member_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateMemberError(key)
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise _NonFiniteNumberError(value)


def _reject_overflow_numbers(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise _NonFiniteNumberError("overflow")
    if isinstance(value, Mapping):
        for child in value.values():
            _reject_overflow_numbers(child)
    elif isinstance(value, list):
        for child in value:
            _reject_overflow_numbers(child)


def _truncation_structure(text: str) -> list[str]:
    stack: list[str] = []
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            stack.append(character)
        elif character in "]}":
            if stack and (stack[-1], character) in {("[", "]"), ("{", "}")}:
                stack.pop()
            else:
                return []
    reasons: list[str] = []
    if in_string:
        reasons.append("unterminated_string")
    if stack:
        reasons.append("unclosed_container")
    return reasons


def _looks_like_json_value_start(text: str) -> bool:
    return bool(text) and (
        text[0] in '{["-0123456789' or text.startswith(("true", "false", "null"))
    )


def strict_json_diagnostic(
    raw: bytes, *, finish_reason: str | None = None
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Strictly parse one object or return one deterministic failure category."""

    base: dict[str, Any] = {
        "appears_truncated": False,
        "parser_error": None,
        "strict_json_failure_category": None,
        "strict_json_status": "failed",
        "structural_truncation_evidence": [],
    }
    if not raw or not raw.strip():
        base["strict_json_failure_category"] = "empty_response"
        base["parser_error"] = "empty or whitespace-only response"
        return None, base
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        base["strict_json_failure_category"] = "invalid_utf8"
        base["parser_error"] = f"UnicodeDecodeError:{exc.reason}"
        return None, base
    try:
        value = json.loads(
            text,
            object_pairs_hook=_duplicate_member_hook,
            parse_constant=_reject_nonfinite,
        )
        _reject_overflow_numbers(value)
    except _DuplicateMemberError as exc:
        base["strict_json_failure_category"] = "duplicate_member"
        base["parser_error"] = f"duplicate JSON member: {exc}"
        return None, base
    except _NonFiniteNumberError as exc:
        base["strict_json_failure_category"] = "non_finite_number"
        base["parser_error"] = f"non-finite JSON number: {exc}"
        return None, base
    except json.JSONDecodeError as exc:
        stripped = text.strip()
        structural = _truncation_structure(text)
        finish_truncation = str(finish_reason or "").upper() in {
            "LENGTH",
            "MAX_LEN",
            "MAX_TOKENS",
            "REASON_MAX_LEN",
            "REASON_MAX_TOKENS",
        }
        base["structural_truncation_evidence"] = structural
        base["appears_truncated"] = bool(structural or finish_truncation)
        base["parser_error"] = (
            f"JSONDecodeError:{exc.msg}:line={exc.lineno}:column={exc.colno}"
        )
        suffix = text[exc.pos :].lstrip() if exc.pos <= len(text) else ""
        if stripped.startswith("```") or stripped.endswith("```"):
            category = "code_fence_or_surrounding_prose"
        elif exc.msg == "Extra data" and _looks_like_json_value_start(suffix):
            category = "multiple_top_level_values"
        elif exc.msg == "Extra data":
            category = "code_fence_or_surrounding_prose"
        elif base["appears_truncated"]:
            category = "truncated_json"
        elif not stripped.startswith("{"):
            category = "code_fence_or_surrounding_prose"
        else:
            category = "syntactically_invalid_json"
        base["strict_json_failure_category"] = category
        return None, base
    except (TypeError, ValueError) as exc:
        base["strict_json_failure_category"] = "other_strict_json_failure"
        base["parser_error"] = f"{type(exc).__name__}:{exc}"
        return None, base
    if not isinstance(value, dict):
        base["strict_json_failure_category"] = "other_strict_json_failure"
        base["parser_error"] = "top-level JSON value is not an object"
        return None, base
    return value, {
        **base,
        "strict_json_status": "passed",
    }


def _original_validation_outcome(validation: Mapping[str, Any]) -> tuple[str, str]:
    reached = "none"
    for stage in VALIDATION_STAGES:
        status = validation.get(f"{stage}_status")
        if isinstance(status, str) and not status.startswith("not_run"):
            reached = stage
        if status == "failed":
            return reached, f"{stage}_failed"
    if all(validation.get(f"{stage}_status") == "passed" for stage in VALIDATION_STAGES):
        return "persisted_ledger", "validated_and_materialised"
    return reached, "other_terminal_validation_state"


def _provider_persistence_errors(value: Any, pointer: str = "") -> list[str]:
    errors: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_pointer = f"{pointer}/{_json_pointer_token(str(key))}"
            if key in FORBIDDEN_PROVIDER_PERSISTENCE_FIELDS and not (
                child_pointer == "/prior_ledger_reference/ledger_id"
            ):
                errors.append(f"provider_persistence_field:{child_pointer}")
            errors.extend(_provider_persistence_errors(child, child_pointer))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(_provider_persistence_errors(child, f"{pointer}/{index}"))
    return errors


def _binding_errors(
    parsed: Mapping[str, Any],
    *,
    conversation_key: str,
    turn: Mapping[str, Any],
    prior_ledger: Mapping[str, Any] | None,
) -> list[str]:
    expected_prior = (
        None
        if prior_ledger is None
        else {
            "as_of_turn_index": prior_ledger.get("as_of_turn_index"),
            "ledger_id": prior_ledger.get("ledger_id"),
        }
    )
    expected = {
        "as_of_turn_index": turn["turn_index"],
        "conversation_key": conversation_key,
        "prior_ledger_reference": expected_prior,
        "target_turn_id": turn["turn_id"],
    }
    errors = [
        f"{field}_binding_mismatch"
        for field, expected_value in expected.items()
        if parsed.get(field) != expected_value
    ]
    errors.extend(_provider_persistence_errors(parsed))
    return _bounded(errors)


def _materialisation_turn(
    conversation_key: str, turn: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "conversation_key": conversation_key,
        "parent_turn_id": turn["parent_turn_id"],
        "post_id": turn["turn_id"],
        "speaker_id": turn["speaker"]["participant_id"],
        "text": turn["text"],
        "turn_id": turn["turn_id"],
        "turn_index": turn["turn_index"],
    }


def _genesis_context(
    conversation_key: str, turn: Mapping[str, Any], reconstruction_grade: str
) -> dict[str, Any]:
    return {
        "conversation_key": conversation_key,
        "current_participant": copy.deepcopy(turn["speaker"]),
        "root_post_id": turn["turn_id"],
        "source_completeness": {
            "account_publication_confirmed": True,
            "chronology_complete": True,
            "complete_prefix_through_turn": True,
            "exact_text_complete": True,
            "limitations": [
                "Historical replies after the selected target and production outcomes are withheld."
            ],
            "parent_graph_complete": True,
            "reconstruction_grade": reconstruction_grade,
        },
    }


def _diagnostic_materialisation(
    recovered: Mapping[str, Any],
    *,
    conversation_key: str,
    turn: Mapping[str, Any],
    transcript_turns: Sequence[Mapping[str, Any]],
    reconstruction_grade: str,
    prior_ledger: Mapping[str, Any] | None,
    canonical_schema: Mapping[str, Any],
    persisted_schema: Mapping[str, Any],
) -> dict[str, Any]:
    # These repository-local imports have no provider or transport dependency.
    from tools import proposition_ledger_semantic_delta as semantic
    from tools import proposition_ledger_xai_provider_preflight as preflight

    canonical_errors = preflight.intended_validation_errors(
        canonical_schema,
        recovered,
        pattern_mode="canonical_outer_anchors",
    )
    stages: dict[str, Any] = {
        "binding_errors": [],
        "binding_status": "not_run",
        "canonical_delta_validation_errors": _bounded(canonical_errors),
        "canonical_delta_validation_status": (
            "passed" if not canonical_errors else "failed"
        ),
        "materialisation_errors": [],
        "materialiser_result_status": "not_run",
        "materialisation_status": "not_run",
        "persisted_ledger_errors": [],
        "persisted_ledger_status": "not_run",
        "semantic_reference_status": "not_run",
    }
    if canonical_errors:
        return {"ledger": None, "local_id_map": None, "stages": stages}
    binding_errors = _binding_errors(
        recovered,
        conversation_key=conversation_key,
        turn=turn,
        prior_ledger=prior_ledger,
    )
    stages["binding_errors"] = binding_errors
    stages["binding_status"] = "passed" if not binding_errors else "failed"
    if binding_errors:
        return {"ledger": None, "local_id_map": None, "stages": stages}

    current = _materialisation_turn(conversation_key, turn)
    result = semantic.materialise_semantic_delta(
        prior_ledger,
        current,
        recovered,
        current_participant=turn["speaker"],
        genesis_context=(
            _genesis_context(conversation_key, turn, reconstruction_grade)
            if prior_ledger is None
            else None
        ),
        semantic_schema=canonical_schema,
        ledger_schema=persisted_schema,
    )
    stages["materialiser_result_status"] = result.status
    stages["materialisation_errors"] = _bounded(result.errors)
    if result.status == "semantic_reference_invalid":
        stages["semantic_reference_status"] = "failed"
        stages["materialisation_status"] = "not_run_due_to_reference_failure"
        return {"ledger": None, "local_id_map": None, "stages": stages}
    stages["semantic_reference_status"] = "passed"
    if not result.succeeded or result.ledger is None:
        stages["materialisation_status"] = "failed"
        if result.status == "persisted_ledger_validation_failure":
            stages["persisted_ledger_status"] = "failed"
        return {"ledger": None, "local_id_map": None, "stages": stages}
    stages["materialisation_status"] = "passed"
    prefix = [
        _materialisation_turn(conversation_key, item)
        for item in transcript_turns
        if int(item["turn_index"]) <= int(turn["turn_index"])
    ]
    persisted_errors = list(
        semantic.phase1.validate_ledger(
            result.ledger,
            {"conversation_key": conversation_key, "turns": prefix},
            persisted_schema,
            _immediate_previous=prior_ledger,
            _validate_history=False,
        )
    )
    if result.ledger.get("ledger_sha256") != semantic.phase1.ledger_sha256(
        result.ledger
    ):
        persisted_errors.append("ledger_self_hash_mismatch")
    stages["persisted_ledger_errors"] = _bounded(persisted_errors)
    stages["persisted_ledger_status"] = (
        "passed" if not persisted_errors else "failed"
    )
    return {
        "ledger": copy.deepcopy(result.ledger),
        "local_id_map": copy.deepcopy(result.local_id_map),
        "stages": stages,
    }


def _call_directory(source: Path, entry: Mapping[str, Any]) -> Path:
    conversation = _safe_component(entry["pilot_conversation_id"], "conversation ID")
    model = _safe_component(entry["model"], "model")
    turn_index = entry.get("turn_index")
    if type(turn_index) is not int or turn_index < 0:
        raise PostmortemError("invalid attempted-call turn index")
    return source / "conversations" / conversation / model / f"turn-{turn_index:03d}"


def _transcript(source: Path, conversation_id: str) -> dict[str, Any]:
    conversation = _safe_component(conversation_id, "conversation ID")
    value = _load_json(
        source / "conversations" / conversation / "transcript.json",
        f"selected transcript {conversation}",
    )
    if not isinstance(value, dict) or not isinstance(value.get("turns"), list):
        raise PostmortemError(f"invalid selected transcript: {conversation}")
    return value


def _turn_for_entry(
    transcript: Mapping[str, Any], entry: Mapping[str, Any]
) -> dict[str, Any]:
    matching = [
        item
        for item in transcript["turns"]
        if isinstance(item, Mapping)
        and item.get("turn_index") == entry.get("turn_index")
        and item.get("turn_id") == entry.get("pilot_turn_id")
    ]
    if len(matching) != 1:
        raise PostmortemError("attempted call does not bind to exactly one selected turn")
    turn = copy.deepcopy(dict(matching[0]))
    if not isinstance(turn.get("text"), str) or not isinstance(turn.get("speaker"), Mapping):
        raise PostmortemError("selected current turn is incomplete")
    return turn


def _prior_ledger(
    source: Path, entry: Mapping[str, Any]
) -> dict[str, Any] | None:
    turn_index = int(entry["turn_index"])
    if turn_index == 0:
        return None
    directory = _call_directory(source, {**entry, "turn_index": turn_index - 1})
    value = _load_json(directory / "materialised-ledger.json", "prior materialised ledger")
    if not isinstance(value, dict):
        raise PostmortemError("prior materialised ledger is not an object")
    return value


def _old_payload(
    entry: Mapping[str, Any],
    turn: Mapping[str, Any],
    prior_ledger: Mapping[str, Any] | None,
    *,
    protocol_version: str,
    protocol_hash: str,
    provider_schema: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "exact_current_visible_text": turn["text"],
        "pilot_local_conversation_key": entry["pilot_conversation_id"],
        "pilot_local_current_turn_id": turn["turn_id"],
        "pilot_local_parent_turn_id": turn["parent_turn_id"],
        "protocol_freeze_sha256": protocol_hash,
        "protocol_version": protocol_version,
        "provider_response_schema": copy.deepcopy(provider_schema),
        "trusted_current_speaker_participant_descriptor": copy.deepcopy(
            turn["speaker"]
        ),
        "turn_index": turn["turn_index"],
        "validated_prior_persisted_ledger": copy.deepcopy(prior_ledger),
    }


def _phase2a_request(
    model: str,
    payload: Mapping[str, Any],
    system_prompt: str,
    provider_schema: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "application_retry_count": 0,
        "client_timeout_seconds": 300.0,
        "code_execution": False,
        "fallback_model": None,
        "max_tokens": 4096,
        "messages": [
            {"content": system_prompt, "role": "system"},
            {
                "content": canonical_json_bytes(payload).decode("utf-8"),
                "role": "user",
            },
        ],
        "model": model,
        "parallel_tool_calls": False,
        "reasoning_effort": "low",
        "request_contract_revision": "phase1.4-no-tools-omit-tool-choice-v2",
        "response_format": {
            "format_type": "json_schema",
            "schema": copy.deepcopy(provider_schema),
        },
        "sampling_parameters_set": [],
        "sdk_grpc_retries": False,
        "search_parameters": None,
        "store_messages": False,
        "streaming": False,
        "tool_choice_parameter_sent": False,
        "tools": [],
    }


def _load_transport_module() -> Any:
    if not TRANSPORT_MODULE_PATH.is_file():
        raise PostmortemError("Phase 2B transport module is missing")
    from tools import proposition_ledger_evidence_transport

    return proposition_ledger_evidence_transport


def _new_payload_and_request(
    module: Any,
    *,
    entry: Mapping[str, Any],
    turn: Mapping[str, Any],
    prior_ledger: Mapping[str, Any] | None,
    protocol_version: str,
    protocol_hash: str,
    response_manifest: Mapping[str, Any],
    system_prompt: str,
    xai_provider_schema: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    builder = getattr(module, "build_phase2b_user_payload", None)
    request_builder = getattr(module, "build_request_representation", None)
    if not callable(builder) or not callable(request_builder):
        raise PostmortemError(
            "transport module lacks Phase 2B payload/request builder API"
        )
    payload = builder(
        protocol_version=protocol_version,
        protocol_hash=protocol_hash,
        conversation_key=entry["pilot_conversation_id"],
        current_turn_id=turn["turn_id"],
        turn_index=turn["turn_index"],
        parent_turn_id=turn["parent_turn_id"],
        current_turn_text=turn["text"],
        speaker_descriptor=turn["speaker"],
        prior_ledger=prior_ledger,
        response_contract_manifest=response_manifest,
    )
    request = request_builder(
        model=entry["model"],
        user_payload=payload,
        system_prompt=system_prompt,
        xai_provider_schema=xai_provider_schema,
    )
    if not isinstance(payload, dict) or not isinstance(request, dict):
        raise PostmortemError("transport payload/request builder returned invalid data")
    return payload, request


def _numeric_summary(values: Sequence[int | float]) -> dict[str, Any]:
    if not values:
        return {
            "maximum": None,
            "median": None,
            "minimum": None,
            "range": [None, None],
            "total": 0,
        }
    contains_float = any(isinstance(value, float) for value in values)
    total: int | float = sum(values)
    middle: int | float = median(values)
    if contains_float:
        total = round(float(total), 6)
        middle = round(float(middle), 6)
    return {
        "maximum": max(values),
        "median": middle,
        "minimum": min(values),
        "range": [min(values), max(values)],
        "total": total,
    }


def _count_complete_value(value: Any, target: Mapping[str, Any]) -> int:
    if isinstance(value, Mapping):
        if dict(value) == dict(target):
            return 1
        return sum(_count_complete_value(child, target) for child in value.values())
    if isinstance(value, list):
        return sum(_count_complete_value(child, target) for child in value)
    return 0


def _payload_size_comparison(
    *,
    source: Path,
    entries: Sequence[Mapping[str, Any]],
    transcripts: Mapping[str, Mapping[str, Any]],
    call_ledger: Mapping[str, Any],
    phase2a_provider_schema: Mapping[str, Any],
) -> dict[str, Any]:
    module = _load_transport_module()
    transport_schema = _load_json(
        TRANSPORT_SCHEMA_PATH, "tracked Phase 2B transport schema"
    )
    if not isinstance(transport_schema, dict):
        raise PostmortemError("Phase 2B transport schema is not an object")
    from tools import proposition_ledger_xai_provider_preflight as preflight

    xai_provider_schema, _ = preflight.transform_provider_schema(transport_schema)
    response_manifest_builder = getattr(
        module, "build_response_contract_manifest", None
    )
    if not callable(response_manifest_builder):
        raise PostmortemError("transport module lacks response-manifest builder")
    response_manifest = response_manifest_builder(
        transport_schema=transport_schema,
        xai_provider_schema=xai_provider_schema,
    )
    if TRANSPORT_MANIFEST_PATH.exists():
        tracked_manifest = _load_json(
            TRANSPORT_MANIFEST_PATH, "tracked Phase 2B transport contract manifest"
        )
        if tracked_manifest != response_manifest:
            raise PostmortemError(
                "tracked Phase 2B transport contract manifest is not deterministic"
            )
    old_prompt = _read_regular_bytes(PHASE2A_PROMPT_PATH, "Phase 2A prompt").decode(
        "utf-8"
    )
    new_prompt = _read_regular_bytes(PHASE2B_PROMPT_PATH, "Phase 2B prompt").decode(
        "utf-8"
    )
    protocol_version = str(
        _load_json(source / "protocol-freeze.json", "Phase 2A protocol freeze")[
            "protocol_version"
        ]
    )
    protocol_hash = str(call_ledger["protocol_freeze_sha256"])
    rows: list[dict[str, Any]] = []
    for entry in entries:
        transcript = transcripts[str(entry["pilot_conversation_id"])]
        turn = _turn_for_entry(transcript, entry)
        prior = _prior_ledger(source, entry)
        old_payload = _old_payload(
            entry,
            turn,
            prior,
            protocol_version=protocol_version,
            protocol_hash=protocol_hash,
            provider_schema=phase2a_provider_schema,
        )
        old_bytes = canonical_json_bytes(old_payload)
        if sha256_bytes(old_bytes) != entry.get("actual_payload_sha256"):
            raise PostmortemError("reconstructed Phase 2A payload hash mismatch")
        old_without_schema = copy.deepcopy(old_payload)
        del old_without_schema["provider_response_schema"]
        embedded_schema_field_bytes = len(old_bytes) - len(
            canonical_json_bytes(old_without_schema)
        )
        new_payload, new_request = _new_payload_and_request(
            module,
            entry=entry,
            turn=turn,
            prior_ledger=prior,
            protocol_version=protocol_version,
            protocol_hash=protocol_hash,
            response_manifest=response_manifest,
            system_prompt=new_prompt,
            xai_provider_schema=xai_provider_schema,
        )
        new_bytes = canonical_json_bytes(new_payload)
        user_schema_occurrences = _count_complete_value(
            new_payload, xai_provider_schema
        )
        request_schema_occurrences = _count_complete_value(
            new_request, xai_provider_schema
        )
        if user_schema_occurrences != 0 or request_schema_occurrences != 1:
            raise PostmortemError(
                "corrected request schema-placement invariant failed"
            )
        reduction = len(old_bytes) - len(new_bytes)
        percentage = round((reduction / len(old_bytes)) * 100, 6)
        old_request = _phase2a_request(
            str(entry["model"]),
            old_payload,
            old_prompt,
            phase2a_provider_schema,
        )
        rows.append(
            {
                "absolute_byte_reduction": reduction,
                "complete_provider_schema_occurrences_in_request": (
                    request_schema_occurrences
                ),
                "complete_provider_schema_occurrences_in_user_payload": (
                    user_schema_occurrences
                ),
                "new_complete_request_bytes": len(canonical_json_bytes(new_request)),
                "new_user_payload_bytes": len(new_bytes),
                "old_complete_request_bytes": len(canonical_json_bytes(old_request)),
                "old_complete_request_reproducible": True,
                "old_user_payload_bytes": len(old_bytes),
                "percentage_byte_reduction": percentage,
                "pilot_conversation_id": entry["pilot_conversation_id"],
                "pilot_turn_id": entry["pilot_turn_id"],
                "profile_id": entry["profile_id"],
                "schema_bytes_removed_from_conversational_message": (
                    embedded_schema_field_bytes
                ),
                "turn_index": entry["turn_index"],
            }
        )
    metrics = {
        "absolute_byte_reduction": _numeric_summary(
            [item["absolute_byte_reduction"] for item in rows]
        ),
        "new_complete_request_bytes": _numeric_summary(
            [item["new_complete_request_bytes"] for item in rows]
        ),
        "new_user_payload_bytes": _numeric_summary(
            [item["new_user_payload_bytes"] for item in rows]
        ),
        "old_complete_request_bytes": _numeric_summary(
            [item["old_complete_request_bytes"] for item in rows]
        ),
        "old_user_payload_bytes": _numeric_summary(
            [item["old_user_payload_bytes"] for item in rows]
        ),
        "percentage_byte_reduction": _numeric_summary(
            [item["percentage_byte_reduction"] for item in rows]
        ),
        "schema_bytes_removed_from_conversational_message": _numeric_summary(
            [
                item["schema_bytes_removed_from_conversational_message"]
                for item in rows
            ]
        ),
    }
    metrics["overall_user_payload_percentage_byte_reduction"] = round(
        (
            metrics["absolute_byte_reduction"]["total"]
            / metrics["old_user_payload_bytes"]["total"]
        )
        * 100,
        6,
    )
    return {
        "artifact_evidence": "deterministic offline reconstruction",
        "counterfactual_provider_token_count": None,
        "counterfactual_token_or_monetary_saving_claimed": False,
        "payload_count": len(rows),
        "per_call": rows,
        "complete_schema_present_once_in_new_request": all(
            item["complete_provider_schema_occurrences_in_request"] == 1
            for item in rows
        ),
        "schema_absent_from_new_user_message": all(
            item["complete_provider_schema_occurrences_in_user_payload"] == 0
            for item in rows
        ),
        "summary": metrics,
    }


def _audit_materialised_ledgers(
    materialised_calls: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for call in materialised_calls:
        parsed = call["parsed"]
        ledger = call["original_materialised_ledger"]
        issues = ledger.get("issue_states", [])
        no_stable_count = sum(
            isinstance(item, Mapping) and item.get("status") == "no_stable_issue"
            for item in issues
        )
        live_count = sum(
            isinstance(item, Mapping)
            and item.get("status") not in TERMINAL_ISSUE_STATUSES
            for item in issues
        )
        semantic_change_count = sum(
            len(parsed.get(field, []))
            if isinstance(parsed.get(field), list)
            else 0
            for field in SEMANTIC_CHANGE_ARRAYS
        )
        rows.append(
            {
                "abstention_codes": sorted(
                    str(item) for item in parsed.get("abstentions", [])
                ),
                "answer_target_count": len(ledger.get("answer_targets", [])),
                "commitment_count": len(ledger.get("participant_commitments", [])),
                "extraction_status": parsed.get("extraction_status"),
                "issue_count": len(issues),
                "live_issue_count": live_count,
                "no_stable_issue_count": no_stable_count,
                "no_stable_issue_output": (
                    no_stable_count > 0
                    or "no_stable_issue"
                    in {str(item) for item in parsed.get("abstentions", [])}
                ),
                "obligation_count": len(
                    ledger.get("conversational_obligations", [])
                ),
                "pilot_conversation_id": call["pilot_conversation_id"],
                "pilot_turn_id": call["pilot_turn_id"],
                "profile_id": call["profile_id"],
                "proposition_count": len(ledger.get("propositions", [])),
                "proposition_update_count": len(
                    parsed.get("proposition_updates", [])
                ),
                "relation_count": len(ledger.get("proposition_relations", [])),
                "semantic_change_record_count": semantic_change_count,
                "turn_index": call["turn_index"],
                "warning_count": len(ledger.get("warnings", [])),
            }
        )
    no_stable = [item for item in rows if item["no_stable_issue_output"]]
    aggregate = {
        "abstained_extraction_with_no_semantic_additions": sum(
            item["extraction_status"] == "abstained"
            and item["semantic_change_record_count"] == 0
            for item in rows
        ),
        "complete_extraction_with_no_semantic_additions": sum(
            item["extraction_status"] == "complete"
            and item["semantic_change_record_count"] == 0
            for item in rows
        ),
        "no_stable_issue_with_one_or_more_commitments": sum(
            item["commitment_count"] >= 1 for item in no_stable
        ),
        "no_stable_issue_with_one_or_more_propositions": sum(
            item["proposition_count"] >= 1 for item in no_stable
        ),
        "no_stable_issue_with_zero_commitments": sum(
            item["commitment_count"] == 0 for item in no_stable
        ),
        "no_stable_issue_with_zero_propositions": sum(
            item["proposition_count"] == 0 for item in no_stable
        ),
    }
    systematic_abstained_without_additions = bool(no_stable) and all(
        item["extraction_status"] == "abstained"
        and item["semantic_change_record_count"] == 0
        for item in no_stable
    )
    further_investigation = bool(
        systematic_abstained_without_additions
        or aggregate["no_stable_issue_with_one_or_more_commitments"]
        or aggregate["no_stable_issue_with_one_or_more_propositions"]
    )
    return {
        "artifact_evidence": "mechanical counts only",
        "audit_scope": "seven original Phase 2A materialised ledgers",
        "materialised_ledger_count": len(rows),
        "per_ledger": rows,
        "aggregate": aggregate,
        "mechanical_evidence_supports_further_no_stable_issue_investigation": (
            further_investigation
        ),
        "human_semantic_correctness_judgement_performed": False,
        "no_stable_issue_prompt_revision_made": False,
        "further_investigation_trigger": (
            "systematic_no_stable_issue_abstention_without_semantic_additions"
            if systematic_abstained_without_additions
            else (
                "no_stable_issue_coexists_with_proposition_or_commitment"
                if further_investigation
                else None
            )
        ),
        "semantic_change_record_fields": list(SEMANTIC_CHANGE_ARRAYS),
    }


def _render_postmortem_markdown(aggregate: Mapping[str, Any]) -> bytes:
    classifications = aggregate["response_recoverability_counts"]
    categories = aggregate["evidence_failure_category_counts"]
    signatures = aggregate["coordinate_signature_counts"]
    lines = [
        "# Phase 2B evidence-boundary post-mortem",
        "",
        "This is a deterministic, offline diagnostic over the 21 already saved "
        "Phase 2A responses. It makes no provider calls and does not revise the "
        "Phase 2A experimental outcome.",
        "",
        f"- Responses classified: {aggregate['response_count']}",
        f"- Parsed responses: {aggregate['parsed_response_count']}",
        f"- Evidence spans examined: {aggregate['evidence_span_count']}",
        f"- Original materialised responses: {aggregate['original_materialised_count']}",
        "- Provider calls: 0",
        "- Model winner inferred: no",
        "",
        "## Exact-text recoverability",
        "",
    ]
    lines.extend(
        f"- `{name}`: {classifications.get(name, 0)}"
        for name in RECOVERABILITY_CLASSES
    )
    lines.extend(["", "## Evidence diagnostic categories", ""])
    lines.extend(
        f"- `{name}`: {categories.get(name, 0)}"
        for name in EVIDENCE_FAILURE_TAXONOMY
    )
    lines.extend(["", "## Coordinate signatures", ""])
    lines.extend(
        f"- `{name}`: {signatures.get(name, 0)}"
        for name in COORDINATE_SIGNATURES
    )
    lines.extend(
        ["", "## Diagnostic materialisation by profile", ""]
    )
    for profile, findings in aggregate.get(
        "diagnostic_materialisation_by_profile", {}
    ).items():
        lines.extend(
            [
                f"- `{profile}` canonical validation passed: "
                f"{findings['canonical_validation_passed']}",
                f"- `{profile}` diagnostic materialisation passed: "
                f"{findings['diagnostic_materialisation_passed']}",
                f"- `{profile}` materialiser result statuses: "
                f"{json.dumps(findings['materialiser_result_status_counts'], sort_keys=True)}",
                f"- `{profile}` failure reason categories: "
                f"{json.dumps(findings['failure_reason_category_counts'], sort_keys=True)}",
            ]
        )
    lines.extend(
        [
            "",
            "Every diagnostic materialisation is labelled "
            f"`{RECOVERY_LABEL}` and uses `{RECOVERY_METHOD}`. These are post-hoc "
            "counterfactuals, not corrected provider observations.",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def _taxonomy_definitions() -> dict[str, str]:
    return {
        "boolean_used_as_integer": (
            "start_char or end_char is a Python bool; bool is never accepted as int"
        ),
        "codepoint_end_inclusive_signature": (
            "valid code-point start and inclusive end slice equals exact_text"
        ),
        "duplicate_resolved_span": (
            "two sibling spans in the same canonical evidence array have the "
            "same sole literal resolution"
        ),
        "end_out_of_bounds": "integer end_char exceeds len(current_turn_text)",
        "exact_text_not_present": (
            "non-empty exact_text has zero literal overlapping Python-string matches"
        ),
        "exact_text_present_multiple_times": (
            "exact_text has more than one literal overlapping match"
        ),
        "exact_text_present_once_coordinates_wrong": (
            "exact_text has one literal match but supplied code-point slice differs"
        ),
        "negative_start": "integer start_char is less than zero",
        "no_recognised_coordinate_signature": (
            "literal text exists and supplied integer coordinates mismatch, but no "
            "bounded alternate coordinate convention produces exact_text"
        ),
        "non_positive_length": "integer end_char is not greater than start_char",
        "normalisation_difference": (
            "supplied in-bounds slice differs literally but has equal NFD solely as "
            "a diagnostic signature; it is never used for recovery"
        ),
        "not_assessable_due_to_strict_json_failure": (
            "saved raw bytes fail strict JSON and therefore expose no parsed "
            "evidence selector for span-level diagnosis"
        ),
        "several_plausible_coordinate_conventions": (
            "two or more bounded alternate coordinate signatures hold"
        ),
        "start_or_end_not_integer": (
            "start_char or end_char has type other than Python int"
        ),
        "utf16_code_unit_signature": (
            "both supplied boundaries map to UTF-16 code-unit boundaries and the "
            "resulting exact code-point slice equals exact_text"
        ),
        "utf8_byte_offset_signature": (
            "both supplied boundaries map to UTF-8 byte boundaries and the resulting "
            "exact code-point slice equals exact_text"
        ),
        "whitespace_or_punctuation_difference": (
            "supplied in-bounds slice differs literally while its sequence of "
            "non-whitespace, non-punctuation code points equals exact_text's; this "
            "signature is never used for recovery"
        ),
        "wrong_current_turn_reference": (
            "supplied turn_id is not the exact trusted current turn ID"
        ),
    }


def _materialisation_failure_reason(error: str) -> str:
    if error.startswith("schema:"):
        return "persisted_ledger_schema_validation_failure"
    if error.startswith("resolved_state_missing_item:"):
        return "resolved_state_missing_item"
    if error.startswith("semantic_reference") or "reference" in error:
        return "semantic_reference_failure"
    if error.startswith("semantic_transition") or "transition" in error:
        return "semantic_transition_failure"
    if error.startswith("ledger_self_hash"):
        return "persisted_ledger_hash_failure"
    return "other_bounded_materialisation_failure"


def _analyse_source(source_run: str | Path) -> dict[str, Any]:
    source_integrity_before = verify_source_run(source_run)
    source = AUTHORIZED_SOURCE_RUN
    call_ledger = _load_json(source / "call-ledger.json", "Phase 2A call ledger")
    if not isinstance(call_ledger, dict) or not isinstance(
        call_ledger.get("entries"), list
    ):
        raise PostmortemError("invalid Phase 2A call ledger")
    entries = sorted(
        [
            copy.deepcopy(dict(item))
            for item in call_ledger["entries"]
            if isinstance(item, Mapping) and item.get("provider_call_count") == 1
        ],
        key=lambda item: int(item["sequence"]),
    )
    if len(entries) != EXPECTED_ATTEMPTED_RESPONSES:
        raise PostmortemError(
            "attempted Phase 2A response count mismatch: "
            f"expected {EXPECTED_ATTEMPTED_RESPONSES}, found {len(entries)}"
        )
    phase2a_provider_schema = _load_json(
        source / "provider-schema.json", "Phase 2A provider schema"
    )
    canonical_schema_raw = _read_regular_bytes(
        CANONICAL_SCHEMA_PATH, "canonical semantic-delta schema"
    )
    if sha256_bytes(canonical_schema_raw) != EXPECTED_CANONICAL_SCHEMA_SHA256:
        raise PostmortemError("canonical semantic-delta schema hash mismatch")
    canonical_schema = json.loads(canonical_schema_raw.decode("utf-8"))
    persisted_schema = _load_json(PERSISTED_SCHEMA_PATH, "persisted ledger schema")
    if not isinstance(phase2a_provider_schema, dict) or not isinstance(
        canonical_schema, dict
    ) or not isinstance(persisted_schema, dict):
        raise PostmortemError("required schema is not an object")

    transcripts: dict[str, dict[str, Any]] = {}
    per_calls: list[dict[str, Any]] = []
    recoveries: list[dict[str, Any]] = []
    materialised_calls: list[dict[str, Any]] = []
    strict_failures: list[dict[str, Any]] = []
    for entry in entries:
        conversation_id = str(entry["pilot_conversation_id"])
        transcript = transcripts.setdefault(
            conversation_id, _transcript(source, conversation_id)
        )
        turn = _turn_for_entry(transcript, entry)
        directory = _call_directory(source, entry)
        raw = _read_regular_bytes(directory / "response.raw.txt", "raw response")
        raw_sha = sha256_bytes(raw)
        if raw_sha != entry.get("raw_response_sha256"):
            raise PostmortemError("call-ledger raw-response hash mismatch")
        usage = _load_json(directory / "usage.json", "response usage metadata")
        validation = _load_json(
            directory / "validation.json", "original response validation"
        )
        if not isinstance(usage, dict) or not isinstance(validation, dict):
            raise PostmortemError("invalid saved response metadata")
        parsed, strict = strict_json_diagnostic(
            raw, finish_reason=usage.get("finish_reason")
        )
        saved_parsed_path = directory / "response.parsed.json"
        if parsed is not None:
            if not saved_parsed_path.exists():
                raise PostmortemError("parsed response sidecar is missing")
            saved_parsed = _load_json(saved_parsed_path, "saved parsed response")
            if saved_parsed != parsed:
                raise PostmortemError("saved parsed response differs from strict parse")
        elif saved_parsed_path.exists():
            raise PostmortemError("strict failure unexpectedly has parsed sidecar")
        if strict["strict_json_status"] != validation.get("strict_json_status"):
            raise PostmortemError("recomputed strict-JSON status differs from original")
        evidence = analyse_parsed_evidence(
            parsed,
            current_turn_id=str(turn["turn_id"]),
            current_turn_text=str(turn["text"]),
        )
        reached, terminal = _original_validation_outcome(validation)
        per_call: dict[str, Any] = {
            "counterfactual_materialisation_status": "not_run",
            "counterfactual_materialiser_result_status": "not_run",
            "diagnostic_recovery_status": evidence["diagnostic_recovery_status"],
            "evidence_span_diagnostic_details": evidence["evidence_spans"],
            "finish_reason": usage.get("finish_reason"),
            "original_evidence_span_count": evidence["evidence_span_count"],
            "original_terminal_validation_category": terminal,
            "original_validation_stage_reached": reached,
            "parsed_response_status": "parsed" if parsed is not None else "unparsed",
            "pilot_conversation_id": conversation_id,
            "pilot_turn_id": entry["pilot_turn_id"],
            "profile_id": entry["profile_id"],
            "raw_response_sha256": raw_sha,
            "response_diagnostic_categories": evidence[
                "response_diagnostic_categories"
            ],
            "strict_json_status": strict["strict_json_status"],
            "turn_index": entry["turn_index"],
            "usage_metadata": copy.deepcopy(usage.get("usage", {})),
            "visible_completion_codepoint_length": (
                len(raw.decode("utf-8"))
                if strict["strict_json_failure_category"] != "invalid_utf8"
                else None
            ),
            "visible_completion_raw_byte_length": len(raw),
        }
        if parsed is None:
            strict_record = {
                "appears_truncated": strict["appears_truncated"],
                "completion_token_count": usage.get("usage", {}).get(
                    "completion_tokens"
                ),
                "exact_strict_parser_error_category": strict[
                    "strict_json_failure_category"
                ],
                "finish_reason": usage.get("finish_reason"),
                "parser_error": strict["parser_error"],
                "pilot_conversation_id": conversation_id,
                "pilot_turn_id": entry["pilot_turn_id"],
                "raw_byte_length": len(raw),
                "raw_response_sha256": raw_sha,
                "returned_model": usage.get("returned_model_id"),
                "structural_truncation_evidence": strict[
                    "structural_truncation_evidence"
                ],
            }
            strict_failures.append(strict_record)
        elif evidence["diagnostic_recovery_status"] == (
            "uniquely_recoverable_from_exact_text"
        ):
            recovered = build_diagnostic_canonical_delta(
                parsed,
                current_turn_id=str(turn["turn_id"]),
                current_turn_text=str(turn["text"]),
            )
            prior = _prior_ledger(source, entry)
            replay = _diagnostic_materialisation(
                recovered,
                conversation_key=conversation_id,
                turn=turn,
                transcript_turns=transcript["turns"],
                reconstruction_grade=str(transcript["reconstruction_grade"]),
                prior_ledger=prior,
                canonical_schema=canonical_schema,
                persisted_schema=persisted_schema,
            )
            counterfactual_status = (
                "passed"
                if replay["stages"]["persisted_ledger_status"] == "passed"
                else "failed"
            )
            per_call["counterfactual_materialisation_status"] = (
                counterfactual_status
            )
            per_call["counterfactual_materialiser_result_status"] = replay[
                "stages"
            ]["materialiser_result_status"]
            original_materialised_path = directory / "materialised-ledger.json"
            original_materialised = (
                _load_json(original_materialised_path, "original materialised ledger")
                if original_materialised_path.exists()
                else None
            )
            recovery = {
                "canonical_delta": recovered,
                "canonical_delta_sha256": value_sha256(recovered),
                "counterfactual_materialisation_status": counterfactual_status,
                **diagnostic_counterfactual_metadata(),
                "local_id_map": replay["local_id_map"],
                "materialised_ledger": replay["ledger"],
                "materialised_ledger_matches_original": (
                    original_materialised == replay["ledger"]
                    if original_materialised is not None
                    else None
                ),
                "non_evidence_semantic_fields_preserved": (
                    _mask_evidence(parsed) == _mask_evidence(recovered)
                ),
                "pilot_conversation_id": conversation_id,
                "pilot_turn_id": entry["pilot_turn_id"],
                "profile_id": entry["profile_id"],
                "resolution_manifest": [
                    {
                        "exact_text_length": item[0]["supplied"][
                            "exact_text_length"
                        ],
                        "exact_text_sha256": item[0]["supplied"][
                            "exact_text_sha256"
                        ],
                        "occurrence_count": item[0][
                            "literal_occurrence_analysis"
                        ]["match_count"],
                        "resolved_span_sha256": value_sha256(item[1]),
                        "selected_end": item[0]["literal_occurrence_analysis"][
                            "matches"
                        ][0]["end"],
                        "selected_occurrence_index": 0,
                        "selected_start": item[0][
                            "literal_occurrence_analysis"
                        ]["matches"][0]["start"],
                        "selector_json_pointer": item[0][
                            "selector_json_pointer"
                        ],
                    }
                    for item in zip(
                        evidence["evidence_spans"],
                        (span for _, span in iter_evidence_spans(recovered)),
                        strict=True,
                    )
                ],
                "stages": replay["stages"],
                "turn_index": entry["turn_index"],
            }
            recoveries.append(recovery)
        if terminal == "validated_and_materialised":
            original_ledger = _load_json(
                directory / "materialised-ledger.json",
                "original materialised ledger",
            )
            materialised_calls.append(
                {
                    "original_materialised_ledger": original_ledger,
                    "parsed": parsed,
                    "pilot_conversation_id": conversation_id,
                    "pilot_turn_id": entry["pilot_turn_id"],
                    "profile_id": entry["profile_id"],
                    "turn_index": entry["turn_index"],
                }
            )
        per_calls.append(per_call)

    if sum(item["parsed_response_status"] == "parsed" for item in per_calls) != (
        EXPECTED_PARSED_RESPONSES
    ):
        raise PostmortemError("parsed Phase 2A response count mismatch")
    if len(materialised_calls) != EXPECTED_MATERIALISED_RESPONSES:
        raise PostmortemError("materialised Phase 2A response count mismatch")
    if len(strict_failures) != 1:
        raise PostmortemError("expected exactly one strict-JSON failure")

    category_counts = Counter(
        category
        for call in per_calls
        for span in call["evidence_span_diagnostic_details"]
        for category in span["diagnostic_categories"]
    )
    response_category_counts = Counter(
        category
        for call in per_calls
        for category in call["response_diagnostic_categories"]
    )
    category_counts.update(response_category_counts)
    signature_counts = Counter(
        signature
        for call in per_calls
        for span in call["evidence_span_diagnostic_details"]
        for signature in span["coordinate_signatures"]
    )
    recovery_counts = Counter(
        str(item["diagnostic_recovery_status"]) for item in per_calls
    )
    profile_recovery: dict[str, dict[str, int]] = {}
    profile_materialisation: dict[str, dict[str, int]] = {}
    for profile in sorted(PROFILE_MODELS):
        profile_recovery[profile] = dict(
            sorted(
                Counter(
                    str(item["diagnostic_recovery_status"])
                    for item in per_calls
                    if item["profile_id"] == profile
                ).items()
            )
        )
        profile_records = [
            item for item in recoveries if item["profile_id"] == profile
        ]
        materialiser_status_counts = Counter(
            str(item["stages"]["materialiser_result_status"])
            for item in profile_records
        )
        failure_reasons = Counter(
            reason
            for item in profile_records
            if item["counterfactual_materialisation_status"] != "passed"
            for reason in {
                _materialisation_failure_reason(str(error))
                for error in item["stages"]["materialisation_errors"]
            }
        )
        terminal_stage_counts = Counter(
            (
                "canonical_delta_validation_failed"
                if item["stages"]["canonical_delta_validation_status"] == "failed"
                else "binding_validation_failed"
                if item["stages"]["binding_status"] == "failed"
                else "semantic_reference_validation_failed"
                if item["stages"]["semantic_reference_status"] == "failed"
                else "persisted_ledger_validation_failed"
                if item["stages"]["persisted_ledger_status"] == "failed"
                else "materialisation_failed"
                if item["stages"]["materialisation_status"] != "passed"
                else "passed"
            )
            for item in profile_records
        )
        profile_materialisation[profile] = {
            "canonical_validation_passed": sum(
                item["stages"]["canonical_delta_validation_status"] == "passed"
                for item in profile_records
            ),
            "diagnostic_materialisation_passed": sum(
                item["counterfactual_materialisation_status"] == "passed"
                for item in profile_records
            ),
            "failure_reason_category_counts": dict(sorted(failure_reasons.items())),
            "materialiser_result_status_counts": dict(
                sorted(materialiser_status_counts.items())
            ),
            "persisted_ledger_validation_passed": sum(
                item["stages"]["persisted_ledger_status"] == "passed"
                for item in profile_records
            ),
            "terminal_stage_counts": dict(sorted(terminal_stage_counts.items())),
        }
    aggregate = {
        "artifact_evidence": "deterministic offline reprocessing",
        "canonical_schema_sha256": EXPECTED_CANONICAL_SCHEMA_SHA256,
        "canonical_schema_version": CANONICAL_SCHEMA_VERSION,
        "coordinate_signature_counts": {
            name: signature_counts.get(name, 0) for name in COORDINATE_SIGNATURES
        },
        "diagnostic_materialisation_by_profile": profile_materialisation,
        "evidence_failure_category_counts": {
            name: category_counts.get(name, 0) for name in EVIDENCE_FAILURE_TAXONOMY
        },
        "evidence_span_count": sum(
            int(item["original_evidence_span_count"]) for item in per_calls
        ),
        "original_materialised_count": len(materialised_calls),
        "parsed_response_count": sum(
            item["parsed_response_status"] == "parsed" for item in per_calls
        ),
        "phase2a_result": PHASE2A_RESULT,
        "postmortem_version": POSTMORTEM_VERSION,
        **research_guardrails(),
        "response_count": len(per_calls),
        "response_recoverability_by_profile": profile_recovery,
        "response_recoverability_counts": {
            name: recovery_counts.get(name, 0) for name in RECOVERABILITY_CLASSES
        },
        "strict_json_failure_count": len(strict_failures),
        "taxonomy_definitions": _taxonomy_definitions(),
    }
    audit = _audit_materialised_ledgers(materialised_calls)
    payload_sizes = _payload_size_comparison(
        source=source,
        entries=entries,
        transcripts=transcripts,
        call_ledger=call_ledger,
        phase2a_provider_schema=phase2a_provider_schema,
    )
    source_integrity_after = verify_source_run(source)
    if source_integrity_after != source_integrity_before:
        raise PostmortemError("Phase 2A source-run integrity changed during analysis")
    source_integrity = {
        **source_integrity_before,
        "source_run_unchanged_after_analysis": True,
    }
    index = {
        "artifact_evidence": "saved Phase 2A sidecars plus deterministic diagnosis",
        "attempted_response_count": len(per_calls),
        "entries": per_calls,
        "postmortem_version": POSTMORTEM_VERSION,
        "provider_calls": 0,
    }
    return {
        "aggregate": aggregate,
        "artifacts": {
            "evidence-failure-postmortem.json": pretty_json_bytes(aggregate),
            "evidence-failure-postmortem.md": _render_postmortem_markdown(
                aggregate
            ),
            "materialised-no-stable-issue-audit.json": pretty_json_bytes(audit),
            "phase2a-response-index.json": pretty_json_bytes(index),
            "request-payload-size-comparison.json": pretty_json_bytes(payload_sizes),
            "source-integrity.json": pretty_json_bytes(source_integrity),
            "strict-json-failure-diagnostic.json": pretty_json_bytes(
                {
                    "artifact_evidence": "strict parsing of saved raw bytes",
                    "diagnostics": strict_failures,
                    "provider_calls": 0,
                }
            ),
        },
        "per_calls": per_calls,
        "recoveries": recoveries,
    }


def build_postmortem_artifacts(
    source_run: str | Path = AUTHORIZED_SOURCE_RUN,
) -> dict[str, Any]:
    """Reprocess all saved responses twice and return byte-stable artefacts."""

    first = _analyse_source(source_run)
    second = _analyse_source(source_run)
    if (
        first["aggregate"] != second["aggregate"]
        or first["per_calls"] != second["per_calls"]
        or first["recoveries"] != second["recoveries"]
        or _expected_file_map(first) != _expected_file_map(second)
    ):
        raise PostmortemError("saved responses did not reprocess byte-identically")
    first["aggregate"]["saved_responses_reprocessed_twice_byte_identical"] = True
    first["artifacts"]["evidence-failure-postmortem.json"] = pretty_json_bytes(
        first["aggregate"]
    )
    first["artifacts"]["evidence-failure-postmortem.md"] = (
        _render_postmortem_markdown(first["aggregate"])
    )
    return first


def _expected_file_map(result: Mapping[str, Any]) -> dict[Path, bytes]:
    files = {Path(name): content for name, content in result["artifacts"].items()}
    for sequence, call in enumerate(result["per_calls"], 1):
        files[Path("per-call-diagnostics") / f"call-{sequence:03d}.json"] = (
            pretty_json_bytes(call)
        )
    for sequence, recovery in enumerate(result["recoveries"], 1):
        files[Path("diagnostic-recovery") / f"recovery-{sequence:03d}.json"] = (
            pretty_json_bytes(recovery)
        )
    return files


def write_postmortem(
    result: Mapping[str, Any], private_output: str | Path
) -> dict[str, Any]:
    """Atomically write the deterministic post-mortem-owned private artefacts."""

    output = _private_directory(private_output, create=True)
    _ensure_private_subdir(output / "per-call-diagnostics", create=True)
    _ensure_private_subdir(output / "diagnostic-recovery", create=True)
    files = _expected_file_map(result)
    for relative, content in sorted(files.items(), key=lambda item: item[0].as_posix()):
        _atomic_write_private(output / relative, content)
    return {
        "artifact_count": len(files),
        "evidence_span_count": result["aggregate"]["evidence_span_count"],
        "provider_calls": 0,
        "response_count": result["aggregate"]["response_count"],
        "status": "phase2b_postmortem_built_offline",
    }


def verify_postmortem(
    result: Mapping[str, Any], private_output: str | Path
) -> dict[str, Any]:
    """Byte-compare a recomputation without writing any private artefact."""

    output = _private_directory(private_output, create=False)
    _ensure_private_subdir(output / "per-call-diagnostics", create=False)
    _ensure_private_subdir(output / "diagnostic-recovery", create=False)
    files = _expected_file_map(result)
    for relative, expected in sorted(files.items(), key=lambda item: item[0].as_posix()):
        actual = _read_regular_bytes(output / relative, f"Phase 2B artifact {relative}")
        if actual != expected:
            raise PostmortemError(f"verify-only byte mismatch: {relative}")
        if stat.S_IMODE((output / relative).stat().st_mode) != 0o600:
            raise PostmortemError(f"private artifact mode is not 0600: {relative}")
    for subdir, expected_count in (
        ("per-call-diagnostics", len(result["per_calls"])),
        ("diagnostic-recovery", len(result["recoveries"])),
    ):
        actual_files: list[str] = []
        for item in (output / subdir).iterdir():
            metadata = item.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise PostmortemError(
                    f"unexpected non-regular entry in {subdir}: {item.name}"
                )
            actual_files.append(item.name)
        actual_files.sort()
        if len(actual_files) != expected_count:
            raise PostmortemError(
                f"unexpected file count in {subdir}: "
                f"expected {expected_count}, found {len(actual_files)}"
            )
    return {
        "artifact_count": len(files),
        "byte_equality_verified": True,
        "provider_calls": 0,
        "source_run_unchanged": True,
        "status": "phase2b_postmortem_verify_only_passed",
        "writes_performed": 0,
    }


def _load_private_json(output: Path, name: str) -> dict[str, Any]:
    """Load one regular mode-0600 JSON object from the Phase 2B run."""

    path = output / name
    value = _load_json(path, f"Phase 2B private artifact {name}")
    if not isinstance(value, dict):
        raise PostmortemError(f"private JSON root is not an object: {name}")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise PostmortemError(f"private JSON mode is not 0600: {name}")
    return value


def _validation_facts(
    *,
    focused_passed: int,
    regression_passed: int,
    pinned_passed: int,
    documentation_modules: int,
    independent_rebuild_passed: bool,
    static_checks_passed: bool,
) -> dict[str, Any]:
    """Build deterministic facts for the exact completed validation commands."""

    for label, value in {
        "focused_passed": focused_passed,
        "regression_passed": regression_passed,
        "pinned_passed": pinned_passed,
        "documentation_modules": documentation_modules,
    }.items():
        if type(value) is not int or value <= 0:
            raise PostmortemError(f"invalid final validation count: {label}")
    return {
        "test_commands": [
            {
                "command": (
                    "MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "
                    "python3 -m pytest -q "
                    "tests/test_proposition_ledger_evidence_transport.py "
                    "tests/test_proposition_ledger_phase2b_evidence_postmortem.py"
                ),
                "failed": 0,
                "passed": focused_passed,
                "skipped": 0,
                "xfail": 0,
            },
            {
                "command": (
                    "MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "
                    "python3 -m pytest -q "
                    "tests/test_proposition_ledger_xai_development_pilot.py "
                    "tests/test_proposition_ledger_xai_live_probe.py "
                    "tests/test_proposition_ledger_xai_provider_preflight.py "
                    "tests/test_proposition_ledger_provider_schema.py "
                    "tests/test_proposition_ledger_semantic_delta.py "
                    "tests/test_proposition_ledger_phase1.py "
                    "tests/test_proposition_ledger_author_groups.py "
                    "tests/test_proposition_ledger_evidence_transport.py "
                    "tests/test_proposition_ledger_phase2b_evidence_postmortem.py"
                ),
                "failed": 0,
                "passed": regression_passed,
                "skipped": 0,
                "xfail": 0,
            },
            {
                "command": (
                    "pinned CPython 3.10.12 with provider credential variables "
                    "removed and Phase 1.3 network denial active: "
                    "MRS_TEST_MODE=1 MRS_XAI_PINNED_SDK_TEST=1 "
                    "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "
                    "python -m pytest -q "
                    "tests/test_proposition_ledger_evidence_transport.py "
                    "tests/test_proposition_ledger_xai_provider_preflight.py"
                ),
                "failed": 0,
                "passed": pinned_passed,
                "skipped": 0,
                "xfail": 0,
            },
        ],
        "documentation_modules_checked": documentation_modules,
        "independent_rebuild_byte_identical": independent_rebuild_passed,
        "static_checks": {
            "git_diff_check": static_checks_passed,
            "phase1_synthetic_fixture_check": static_checks_passed,
            "python_documentation_check": static_checks_passed,
            "required_py_compile": static_checks_passed,
            "transport_schema_check": static_checks_passed,
        },
    }


def _expected_complete_inventory() -> set[str]:
    """Return the exact permitted complete Phase 2B private-run inventory."""

    root = {
        *ROOT_ARTIFACT_NAMES,
        *PREFLIGHT_ARTIFACT_NAMES,
        *FINAL_ARTIFACT_NAMES,
        CHECKSUM_FILE_NAME,
    }
    root.update(f"per-call-diagnostics/call-{index:03d}.json" for index in range(1, 22))
    root.update(
        f"diagnostic-recovery/recovery-{index:03d}.json"
        for index in range(1, 21)
    )
    return root


def _private_inventory(output: Path) -> tuple[list[str], int]:
    """Audit private modes, regular files, directories, and symlink absence."""

    files: list[str] = []
    directory_count = 0
    for path in [output, *sorted(output.rglob("*"))]:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise PostmortemError("symlink found in Phase 2B private run")
        if stat.S_ISDIR(metadata.st_mode):
            directory_count += 1
            if stat.S_IMODE(metadata.st_mode) != 0o700:
                raise PostmortemError(f"private directory mode is not 0700: {path}")
        elif stat.S_ISREG(metadata.st_mode):
            if stat.S_IMODE(metadata.st_mode) != 0o600:
                raise PostmortemError(f"private file mode is not 0600: {path}")
            files.append(path.relative_to(output).as_posix())
        else:
            raise PostmortemError("non-regular object found in Phase 2B private run")
    return sorted(files), directory_count


def _audit_component_inventory(output: Path) -> None:
    """Require the exact safe directory shape before reading component files."""

    files, directory_count = _private_inventory(output)
    if directory_count != 3:
        raise PostmortemError(
            f"private directory count differs: expected 3, found {directory_count}"
        )
    expected_components = (
        _expected_complete_inventory()
        - set(FINAL_ARTIFACT_NAMES)
        - {CHECKSUM_FILE_NAME}
    )
    present = set(files)
    missing = sorted(expected_components.difference(present))
    unexpected = sorted(present.difference(_expected_complete_inventory()))
    if missing or unexpected:
        raise PostmortemError(
            "private component inventory mismatch: "
            f"missing={missing} unexpected={unexpected}"
        )


def _privacy_scan(output: Path) -> dict[str, Any]:
    """Scan the fixed component-artifact set for privacy and credential markers."""

    production_paths = 0
    credential_values = 0
    aggregate_identity_leaks = 0
    report_names = {
        "evidence-failure-postmortem.md",
        "phase2b-report.md",
        "result-summary.json",
        "validation.json",
    }
    files_scanned = 0
    component_names = sorted(
        _expected_complete_inventory()
        - set(FINAL_ARTIFACT_NAMES)
        - {CHECKSUM_FILE_NAME}
    )
    for name in component_names:
        path = output / name
        source = _read_regular_bytes(path, f"privacy scan {name}")
        files_scanned += 1
        production_paths += source.count(b"/disks/disk1/etc/")
        credential_values += len(
            re.findall(
                rb"(?i)(?:authorization|api[_-]?key|password|secret)"
                rb"[\"'\s]*[:=][\"'\s]*(?!false\b|null\b)[A-Za-z0-9/+_.=-]{12,}",
                source,
            )
        )
        if path.name in report_names:
            aggregate_identity_leaks += len(
                re.findall(rb"dev-(?:conversation|turn)-[0-9]+", source)
            )
    if production_paths or credential_values or aggregate_identity_leaks:
        raise PostmortemError("Phase 2B privacy or credential scan failed")
    return {
        "aggregate_report_pilot_identity_leak_count": 0,
        "api_key_value_read_hashed_measured_or_serialised": False,
        "credential_value_marker_count": 0,
        "files_scanned": files_scanned,
        "production_path_leak_count": 0,
        "status": "passed",
    }


def _scan_rendered_aggregate_artifacts(rendered: Mapping[str, bytes]) -> None:
    """Reject bounded sensitive markers in prospective aggregate artifacts."""

    for name, source in rendered.items():
        if source.count(b"/disks/disk1/etc/"):
            raise PostmortemError(f"production path leaked into {name}")
        if re.search(rb"dev-(?:conversation|turn)-[0-9]+", source):
            raise PostmortemError(f"pilot-local identity leaked into {name}")
        if re.search(
            rb"(?i)(?:authorization|api[_-]?key|password|secret)"
            rb"[\"'\s]*[:=][\"'\s]*(?!false\b|null\b)[A-Za-z0-9/+_.=-]{12,}",
            source,
        ):
            raise PostmortemError(f"credential-like value leaked into {name}")


def _build_integrated_result(
    output: Path, validation_facts: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate component artifacts and derive the Phase 2B disposition."""

    _audit_component_inventory(output)
    postmortem = _load_private_json(output, "evidence-failure-postmortem.json")
    source = _load_private_json(output, "source-integrity.json")
    strict = _load_private_json(output, "strict-json-failure-diagnostic.json")
    no_stable = _load_private_json(output, "materialised-no-stable-issue-audit.json")
    payload = _load_private_json(output, "request-payload-size-comparison.json")
    equivalence = _load_private_json(output, "transport-schema-equivalence-audit.json")
    transformation = _load_private_json(
        output, "transport-schema-transformation-ledger.json"
    )
    private_transport_bytes = _read_regular_bytes(
        output / "transport-schema.json", "transport-schema.json"
    )
    private_provider_schema = _load_private_json(
        output, "xai-provider-transport-schema.json"
    )
    sdk43 = _load_private_json(output, "local-sdk-grok-4.3.json")
    sdk46 = _load_private_json(output, "local-sdk-grok-4.6.json")
    strict_rows = strict.get("diagnostics")
    if not isinstance(strict_rows, list) or len(strict_rows) != 1:
        raise PostmortemError("strict failure diagnostic count is not one")
    strict_row = strict_rows[0]
    recovery = postmortem.get("response_recoverability_counts", {})
    sdk_records = (sdk43, sdk46)
    expected_sdk_versions = {
        "grpcio": "1.83.1",
        "jsonschema": "4.26.0",
        "protobuf": "6.33.6",
        "pydantic": "2.13.5",
        "xai-sdk": "1.19.0",
    }
    sdk_ok = all(
        item.get("provider_calls") == 0
        and item.get("rpc_invocation_count") == 0
        and item.get("inference_requests") == 0
        and item.get("model_list_requests") == 0
        and item.get("application_retry_count") == 0
        and item.get("fallback_model") is None
        and item.get("request_construction_status")
        == "succeeded_without_transport"
        and item.get("server_acceptance_status") == "not_tested"
        and item.get("python_version") == "3.10.12"
        and item.get("sdk_versions") == expected_sdk_versions
        and item.get("reasoning_effort") == "low"
        and item.get("maximum_output_tokens") == 4096
        and item.get("store_messages") is False
        and item.get("streaming") is False
        and item.get("tool_count") == 0
        and item.get("tool_choice_present") is False
        and item.get("tool_choice_parameter_sent") is False
        and item.get("search_parameters_present") is False
        and item.get("web_search_enabled") is False
        and item.get("x_search_enabled") is False
        and item.get("code_execution_enabled") is False
        and item.get("schema_absent_from_user_message") is True
        and item.get("schema_complete_occurrences_in_messages") == 0
        and item.get("schema_complete_occurrences_in_serialized_request") == 1
        and item.get("schema_present_once_in_response_format") is True
        and item.get("provider_schema_sha256")
        == XAI_PROVIDER_TRANSPORT_SCHEMA_SHA256
        and item.get("sdk_emitted_schema_equal") is True
        and item.get("sdk_emitted_schema_sha256")
        == XAI_PROVIDER_TRANSPORT_SCHEMA_SHA256
        and item.get("sdk_schema_conversion_status") == "raw_schema_preserved"
        and item.get("requests_differ_only_by_model") is True
        and item.get("network_denial_audit", {}).get(
            "guard_active_during_provider_import_and_request_construction"
        )
        is True
        and item.get("network_denial_audit", {}).get("provider_key_values_read")
        is False
        and item.get("network_denial_audit", {}).get("transport_rpc_invocations")
        == 0
        for item in sdk_records
    )
    sdk_ok = sdk_ok and [
        (item.get("model"), item.get("profile_id")) for item in sdk_records
    ] == [
        ("grok-4.3", "xai-grok-4.3-low-ledger-v2"),
        ("grok-4.6", "xai-grok-4.6-low-ledger-v2"),
    ]
    sdk_ok = sdk_ok and all(
        item.get("request_contract_revision")
        == "phase2b-exact-evidence-selector-v1"
        and item.get("message_count") == 2
        and item.get("live_probe_authorised") is False
        and item.get("development_rerun_authorised") is False
        and type(item.get("serialized_request_byte_length")) is int
        and item["serialized_request_byte_length"] > 0
        and isinstance(item.get("serialized_request_sha256"), str)
        and SHA256_RE.fullmatch(item["serialized_request_sha256"]) is not None
        and item.get("network_denial_audit", {}).get(
            "deliberate_connection_attempts_blocked"
        )
        is True
        and item.get("network_denial_audit", {}).get("provider_calls") == 0
        and all(
            type(count) is int and count >= 1
            for count in item.get("network_denial_audit", {})
            .get("attempts_observed", {})
            .values()
        )
        and set(
            item.get("network_denial_audit", {})
            .get("attempts_observed", {})
            .keys()
        )
        == {"dns", "grpc", "socket"}
        for item in sdk_records
    )
    sdk_ok = sdk_ok and (
        sdk43.get("serialized_request_byte_length")
        == sdk46.get("serialized_request_byte_length")
        and sdk43.get("serialized_request_sha256")
        != sdk46.get("serialized_request_sha256")
    )
    provider_schema_value_sha256 = sha256_bytes(
        canonical_json_bytes(private_provider_schema)
    )
    schema_audit = equivalence.get("transport_schema_equivalence", {})
    provider_audit = equivalence.get("xai_provider_schema_audit", {})
    canonical_to_transport = transformation.get("canonical_to_transport", [])
    transport_to_provider = transformation.get("transport_to_xai_provider", [])
    schema_ok = (
        sha256_bytes(private_transport_bytes) == TRANSPORT_SCHEMA_FILE_SHA256
        and private_transport_bytes == TRANSPORT_SCHEMA_PATH.read_bytes()
        and provider_schema_value_sha256 == XAI_PROVIDER_TRANSPORT_SCHEMA_SHA256
        and transformation.get("transport_schema_file_sha256")
        == TRANSPORT_SCHEMA_FILE_SHA256
        and transformation.get("xai_provider_schema_value_sha256")
        == XAI_PROVIDER_TRANSPORT_SCHEMA_SHA256
        and schema_audit.get("status") == "passed"
        and schema_audit.get("exact_deterministic_derivation") is True
        and schema_audit.get("canonical_structure_restored_after_bounded_inverse")
        is True
        and schema_audit.get("permitted_transformation_count") == 7
        and provider_audit.get("status") == "passed"
        and provider_audit.get("deterministic") is True
        and provider_audit.get("provider_keyword_rejected_count") == 0
        and provider_audit.get("additional_properties_default_expansion_count")
        == 5
        and provider_audit.get("proved_outer_anchor_removal_count") == 14
        and provider_audit.get("transformation_count") == 19
        and isinstance(canonical_to_transport, list)
        and len(canonical_to_transport) == 7
        and isinstance(transport_to_provider, list)
        and len(transport_to_provider) == 19
    )
    transport_module = _load_transport_module()
    transport_source_sha256 = sha256_bytes(TRANSPORT_MODULE_PATH.read_bytes())
    core_ok = (
        source.get("all_listed_checksums_passed") is True
        and source.get("complete_tree_matches_declared_inventory") is True
        and source.get("source_run_unchanged_after_analysis") is True
        and postmortem.get("response_count") == 21
        and postmortem.get("parsed_response_count") == 20
        and postmortem.get("evidence_span_count") == 112
        and postmortem.get("saved_responses_reprocessed_twice_byte_identical") is True
        and payload.get("payload_count") == 21
        and payload.get("schema_absent_from_new_user_message") is True
        and schema_ok
        and sdk_ok
        and all(validation_facts.get("static_checks", {}).values())
        and validation_facts.get("independent_rebuild_byte_identical") is True
    )
    if not core_ok:
        raise PostmortemError("Phase 2B readiness invariant failed")
    absent = int(recovery.get("not_recoverable_exact_text_absent", 0))
    disposition = RESIDUAL_DISPOSITION if absent else READY_DISPOSITION
    privacy = _privacy_scan(output)
    payload_summary = payload["summary"]
    result = {
        "artifact_evidence": "deterministic Phase 2B integration",
        "canonical_schema": {
            "sha256": EXPECTED_CANONICAL_SCHEMA_SHA256,
            "version": CANONICAL_SCHEMA_VERSION,
        },
        "diagnostic_materialisation_by_profile": postmortem[
            "diagnostic_materialisation_by_profile"
        ],
        "evidence_failure_category_counts": postmortem[
            "evidence_failure_category_counts"
        ],
        "coordinate_signature_counts": postmortem[
            "coordinate_signature_counts"
        ],
        "evidence_span_count": postmortem["evidence_span_count"],
        "evidence_resolver": {
            "source_sha256": transport_source_sha256,
            "version": transport_module.EVIDENCE_RESOLVER_VERSION,
        },
        "mechanical_no_stable_issue_audit": no_stable["aggregate"],
        "mechanical_no_stable_issue_further_investigation_supported": no_stable[
            "mechanical_evidence_supports_further_no_stable_issue_investigation"
        ],
        "payload_byte_comparison": payload_summary,
        "payload_schema_duplication_removal": {
            "complete_schema_present_once_in_response_format": payload[
                "complete_schema_present_once_in_new_request"
            ],
            "complete_schema_present_in_user_message": False,
            "schema_bytes_removed_total": payload_summary[
                "schema_bytes_removed_from_conversational_message"
            ]["total"],
        },
        "phase2a_result_unchanged": PHASE2A_RESULT,
        "phase2b_disposition": disposition,
        "provider_calls": 0,
        "response_count": postmortem["response_count"],
        "response_recoverability_by_profile": postmortem[
            "response_recoverability_by_profile"
        ],
        "response_recoverability_counts": recovery,
        "strict_json_failure": {
            "appears_truncated": strict_row["appears_truncated"],
            "category": strict_row["exact_strict_parser_error_category"],
            "completion_token_count": strict_row["completion_token_count"],
            "finish_reason": strict_row["finish_reason"],
            "raw_byte_length": strict_row["raw_byte_length"],
            "returned_model": strict_row["returned_model"],
        },
        "source_integrity": {
            "complete_tree_digest": source["complete_tree_digest"],
            "sha256sums_file_sha256": source["sha256sums_file_sha256"],
            "sha256sum_check_passed": source["all_listed_checksums_passed"],
            "source_run_unchanged": source["source_run_unchanged_after_analysis"],
        },
        "transport_schema": {
            "sha256": TRANSPORT_SCHEMA_FILE_SHA256,
            "version": TRANSPORT_SCHEMA_VERSION,
            "xai_provider_schema_hash_basis": "canonical_json_value_sha256",
            "xai_provider_schema_sha256": XAI_PROVIDER_TRANSPORT_SCHEMA_SHA256,
        },
        "transport_transformations": {
            "canonical_to_transport_count": len(canonical_to_transport),
            "canonical_to_transport_kinds": sorted(
                item["transformation_kind"] for item in canonical_to_transport
            ),
            "provider_additional_properties_expansions": provider_audit[
                "additional_properties_default_expansion_count"
            ],
            "provider_outer_anchor_removals_proved": provider_audit[
                "proved_outer_anchor_removal_count"
            ],
            "transport_to_provider_count": len(transport_to_provider),
        },
        "local_sdk": {
            "models": [item["model"] for item in sdk_records],
            "provider_schema_preserved": True,
            "requests_differ_only_by_model": True,
            "server_acceptance_status": "not_tested",
        },
        "controls": {
            "development_rerun_authorised": False,
            "deployment_performed": False,
            "downstream_four_arm_experiment_authorised": False,
            "held_out_use_authorised": False,
            "human_scoring_conducted": False,
            "ledger_effectiveness_established": False,
            "live_probe_authorised": False,
            "merge_performed": False,
            "model_profile_selected": False,
            "model_winner_inferred": False,
            "phase2a_artifacts_modified": False,
            "phase2a_experimental_outcome_revised": False,
            "production_or_services_modified": False,
            "production_integration_authorised": False,
            "provider_server_acceptance_established": False,
            "sealed_clean_prefixes_remained_sealed": True,
        },
    }
    validation = {
        "artifact_evidence": "deterministic local validation",
        "checksum_verification_status": "passed",
        "complete_private_inventory_expected_file_count": len(
            _expected_complete_inventory()
        ),
        "held_out_or_unexposed_content_read": False,
        "no_api_key_read": True,
        "phase2a_source_tree_digest": source["complete_tree_digest"],
        "privacy": privacy,
        "provider_calls": 0,
        "sealed_clean_prefixes_remained_sealed": True,
        "status": "passed",
        "verify_only": {
            "provider_calls": 0,
            "requires_api_key": False,
            "source_or_git_mutations": 0,
            "writes_performed": 0,
        },
        **copy.deepcopy(dict(validation_facts)),
    }
    return result, validation


def _render_phase2b_report(
    result: Mapping[str, Any], validation: Mapping[str, Any]
) -> bytes:
    """Render the private aggregate Phase 2B report without case identities."""

    recovery = result["response_recoverability_counts"]
    diagnostic = result["diagnostic_materialisation_by_profile"]
    payload = result["payload_byte_comparison"]
    no_stable = result["mechanical_no_stable_issue_audit"]
    lines = [
        "# Proposition-ledger Phase 2B evidence transport",
        "",
        f"Disposition: `{result['phase2b_disposition']}`",
        "",
        "This offline post-mortem and local transport implementation made zero "
        "provider calls and does not revise the immutable Phase 2A result.",
        "",
        "## Evidence boundary",
        "",
        f"- Responses: 21; evidence spans: {result['evidence_span_count']}.",
        "- Unique exact-text recoveries: "
        f"{recovery['uniquely_recoverable_from_exact_text']}; repeated-text "
        f"ambiguities: {recovery['recoverable_only_with_occurrence_disambiguation']}; "
        f"absent text: {recovery['not_recoverable_exact_text_absent']}.",
        "- Strict failure: "
        f"`{result['strict_json_failure']['category']}`, finish reason "
        f"`{result['strict_json_failure']['finish_reason']}`, returned model "
        f"`{result['strict_json_failure']['returned_model']}`.",
        "- Coordinate signatures: "
        f"`{json.dumps(result['coordinate_signature_counts'], sort_keys=True)}`.",
        "- Diagnostic persisted materialisation: Grok 4.3 "
        f"{diagnostic['xai-grok-4.3-low-ledger-v1']['persisted_ledger_validation_passed']}/13; "
        "Grok 4.6 "
        f"{diagnostic['xai-grok-4.6-low-ledger-v1']['persisted_ledger_validation_passed']}/7.",
        "- These are diagnostic counterfactuals, not corrected provider observations.",
        "",
        "## Mechanical no_stable_issue audit",
        "",
        f"- Zero propositions: {no_stable['no_stable_issue_with_zero_propositions']}; "
        f"one or more: {no_stable['no_stable_issue_with_one_or_more_propositions']}.",
        f"- Zero commitments: {no_stable['no_stable_issue_with_zero_commitments']}; "
        f"one or more: {no_stable['no_stable_issue_with_one_or_more_commitments']}.",
        f"- Abstained/no additions: {no_stable['abstained_extraction_with_no_semantic_additions']}.",
        "- No semantic prompt revision or human correctness judgment was made.",
        "",
        "## Transport and validation",
        "",
        f"- Canonical schema: `{result['canonical_schema']['version']}` / "
        f"`{result['canonical_schema']['sha256']}`.",
        f"- Transport schema: `{result['transport_schema']['version']}` / "
        f"`{result['transport_schema']['sha256']}`.",
        "- xAI provider transport schema: "
        f"`{result['transport_schema']['xai_provider_schema_sha256']}`.",
        f"- Evidence resolver: `{result['evidence_resolver']['version']}` / "
        f"`{result['evidence_resolver']['source_sha256']}`.",
        "- Canonical-to-transport ledger entries: "
        f"{result['transport_transformations']['canonical_to_transport_count']}; "
        "provider transformations: "
        f"{result['transport_transformations']['transport_to_provider_count']} "
        "(five explicit additionalProperties defaults and fourteen proved "
        "outer-anchor removals).",
        f"- User payload bytes: {payload['old_user_payload_bytes']['total']} -> "
        f"{payload['new_user_payload_bytes']['total']} (reduction "
        f"{payload['absolute_byte_reduction']['total']}, "
        f"{payload['overall_user_payload_percentage_byte_reduction']}%).",
        "- Schema bytes removed from conversational messages: "
        f"{result['payload_schema_duplication_removal']['schema_bytes_removed_total']}.",
        "- Both pinned local SDK requests serialised, differed only by model, "
        "omitted tool_choice, and kept the schema out of the user message.",
        f"- Independent rebuild byte-identical: "
        f"{str(validation['independent_rebuild_byte_identical']).lower()}.",
        "- Tests: "
        + "; ".join(
            f"{item['passed']} passed, {item['failed']} failed, "
            f"{item['skipped']} skipped"
            for item in validation["test_commands"]
        )
        + ".",
        "- Verify-only, permissions, privacy, and checksum validation passed.",
        "",
        "No profile winner or effectiveness result was established. No held-out "
        "content was read, both clean prefixes remained sealed, and no live probe, "
        "development rerun, downstream experiment, production change, merge, or "
        "deployment occurred.",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def _checksum_bytes(output: Path) -> bytes:
    """Build the exact sorted checksum inventory excluding SHA256SUMS itself."""

    rows: list[str] = []
    for relative in sorted(_expected_complete_inventory() - {CHECKSUM_FILE_NAME}):
        path = output / relative
        rows.append(f"{sha256_bytes(_read_regular_bytes(path, relative))}  {relative}")
    return ("\n".join(rows) + "\n").encode("utf-8")


def finalize_private_run(
    private_output: str | Path,
    validation_facts: Mapping[str, Any],
    *,
    verify_only: bool,
) -> dict[str, Any]:
    """Write or verify the integrated report, disposition, and full checksums."""

    output = _private_directory(private_output, create=False)
    result, validation = _build_integrated_result(output, validation_facts)
    rendered = {
        "phase2b-report.md": _render_phase2b_report(result, validation),
        "result-summary.json": pretty_json_bytes(result),
        "validation.json": pretty_json_bytes(validation),
    }
    _scan_rendered_aggregate_artifacts(rendered)
    if verify_only:
        for name, expected in sorted(rendered.items()):
            if _read_regular_bytes(output / name, name) != expected:
                raise PostmortemError(f"integrated verify-only mismatch: {name}")
        expected_sums = _checksum_bytes(output)
        if _read_regular_bytes(output / CHECKSUM_FILE_NAME, CHECKSUM_FILE_NAME) != expected_sums:
            raise PostmortemError("SHA256SUMS differs from deterministic inventory")
    else:
        for name, content in sorted(rendered.items()):
            _atomic_write_private(output / name, content)
        _atomic_write_private(output / CHECKSUM_FILE_NAME, _checksum_bytes(output))
    files, directory_count = _private_inventory(output)
    expected = _expected_complete_inventory()
    if set(files) != expected or directory_count != 3:
        missing = sorted(expected.difference(files))
        unexpected = sorted(set(files).difference(expected))
        raise PostmortemError(
            "complete private inventory mismatch: "
            f"missing={missing} unexpected={unexpected} "
            f"directories={directory_count}"
        )
    declared = _parse_sha256sums(
        _read_regular_bytes(output / CHECKSUM_FILE_NAME, CHECKSUM_FILE_NAME)
    )
    if {name for name, _digest in declared} != expected - {CHECKSUM_FILE_NAME}:
        raise PostmortemError("SHA256SUMS path inventory mismatch")
    for relative, digest in declared:
        if sha256_bytes(_read_regular_bytes(output / relative, relative)) != digest:
            raise PostmortemError(f"checksum mismatch: {relative}")
    return {
        "checksum_entry_count": len(declared),
        "directory_count": directory_count,
        "file_count": len(files),
        "phase2b_disposition": result["phase2b_disposition"],
        "provider_calls": 0,
        "status": "phase2b_private_run_verified" if verify_only else "phase2b_private_run_finalized",
        "writes_performed": 0 if verify_only else len(rendered) + 1,
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the bounded offline command-line parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-run",
        type=Path,
        default=AUTHORIZED_SOURCE_RUN,
        help="authorized completed Phase 2A private run",
    )
    parser.add_argument(
        "--private-output",
        type=Path,
        required=True,
        help="mode-0700 Phase 2B private output directory",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="recompute and byte-compare without writing",
    )
    parser.add_argument(
        "--finalize-private",
        action="store_true",
        help="also write or verify integrated result/report/checksum artifacts",
    )
    parser.add_argument("--focused-passed", type=int)
    parser.add_argument("--regression-passed", type=int)
    parser.add_argument("--pinned-passed", type=int)
    parser.add_argument("--documentation-modules", type=int)
    parser.add_argument("--independent-rebuild-passed", action="store_true")
    parser.add_argument("--static-checks-passed", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the offline build or its write-free verify-only pass."""

    arguments = build_parser().parse_args(argv)
    try:
        result = build_postmortem_artifacts(arguments.source_run)
        summary = (
            verify_postmortem(result, arguments.private_output)
            if arguments.verify_only
            else write_postmortem(result, arguments.private_output)
        )
        if arguments.finalize_private:
            count_values = (
                arguments.focused_passed,
                arguments.regression_passed,
                arguments.pinned_passed,
                arguments.documentation_modules,
            )
            if any(value is None for value in count_values):
                raise PostmortemError(
                    "finalization requires all exact validation counts"
                )
            facts = _validation_facts(
                focused_passed=arguments.focused_passed,
                regression_passed=arguments.regression_passed,
                pinned_passed=arguments.pinned_passed,
                documentation_modules=arguments.documentation_modules,
                independent_rebuild_passed=arguments.independent_rebuild_passed,
                static_checks_passed=arguments.static_checks_passed,
            )
            summary = {
                **summary,
                "finalization": finalize_private_run(
                    arguments.private_output,
                    facts,
                    verify_only=arguments.verify_only,
                ),
            }
    except PostmortemError as exc:
        print(f"phase2b evidence post-mortem failed: {exc}", file=os.sys.stderr)
        return 1
    print(canonical_json_bytes(summary).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
