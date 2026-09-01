#!/usr/bin/env python3
"""Bounded paired incremental-ledger development pilot (Phase 2A).

The module is deliberately inert on import.  Provider code is imported only by
``--execute-live-pilot`` after the frozen budget, credential, Git, manifest,
and durable-call-ledger guards have passed.  Every other mode is offline.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import importlib
import itertools
import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from tools import proposition_ledger_semantic_delta as semantic  # noqa: E402


class _LazyModule:
    """Delay network-capable standard-library imports until an active mode needs them."""

    def __init__(self, module_name: str) -> None:
        self._module_name = module_name
        self._module: Any = None

    def __getattr__(self, name: str) -> Any:
        if self._module is None:
            self._module = importlib.import_module(self._module_name)
        return getattr(self._module, name)


live_probe = _LazyModule("tools.proposition_ledger_xai_live_probe")
preflight = _LazyModule("tools.proposition_ledger_xai_provider_preflight")


PHASE2A_DIR = PROJECT_DIR / "proposition_ledger_research/phase2a"
PROTOCOL_MD_PATH = PHASE2A_DIR / "development-pilot-protocol.md"
PROTOCOL_JSON_PATH = PHASE2A_DIR / "development-pilot-protocol.json"
SYSTEM_PROMPT_PATH = PHASE2A_DIR / "incremental-ledger-system-prompt.txt"
REVIEW_RUBRIC_PATH = PHASE2A_DIR / "human-review-rubric.json"
TRACKED_FREEZE_PATH = PHASE2A_DIR / "protocol-freeze.json"
CANONICAL_SCHEMA_PATH = (
    PROJECT_DIR
    / "proposition_ledger_research/schema/proposition-ledger-semantic-delta-v1.schema.json"
)
PERSISTED_LEDGER_SCHEMA_PATH = semantic.DEFAULT_LEDGER_SCHEMA_PATH

CORRECTED_CORPUS_RUN = Path(
    "/disks/disk1/research/private-runs/"
    "proposition-ledger-phase1.2-author-binding-20260831T205024Z"
)
ORIGINAL_PHASE1_RUN = Path(
    "/disks/disk1/research/private-runs/"
    "proposition-ledger-phase1-20260831T145852Z"
)
CALIBRATION_DIR = CORRECTED_CORPUS_RUN / "calibration-pack"
CALIBRATION_MANIFEST_PATH = CALIBRATION_DIR / "manifest.json"
CALIBRATION_INDEX_PATH = CALIBRATION_DIR / "calibration-index.json"
CALIBRATION_RECORDS_PATH = CALIBRATION_DIR / "calibration-records.jsonl"
TARGET_PREFIX_METADATA_PATH = (
    CORRECTED_CORPUS_RUN / "target-prefix-feasibility-index.jsonl"
)
FROZEN_SOURCE_MANIFEST_PATH = ORIGINAL_PHASE1_RUN / "frozen-source-manifest.json"
PROSPECTIVE_BATCH_DIR = Path(
    "/disks/disk1/research/mrsMThatcher-prospective-conversations-v4/"
    "batches/20260831T140305Z-1e5ce2945da2"
)
PROSPECTIVE_BATCH_MANIFEST_PATH = PROSPECTIVE_BATCH_DIR / "manifest.json"
CORRECTED_PHASE14_RUN = Path(
    "/disks/disk1/research/private-runs/"
    "proposition-ledger-phase1.4-xai-live-probe-correction-20260901T074052Z"
)

SOURCE_COMMIT = "79429508e31beedec9a3d6470c67f854b60dbf3a"
EXPECTED_CANONICAL_SCHEMA_SHA256 = (
    "eea15c28f5019cea405bfdd65b924ee18c76dc428f16a1fe502592b5cb953d8a"
)
EXPECTED_PROVIDER_SCHEMA_SHA256 = (
    "37423dc87da3a253ee6c3dcc826c764268d094b16ba83bd1d4a9b1e00948bc21"
)
EXPECTED_SOURCE_MANIFEST_SHA256 = (
    "b42ce8f0ead2961f04e81e041c51455a41c64a7830854a7ec080e593ae7289ec"
)
EXPECTED_PROSPECTIVE_MANIFEST_SHA256 = (
    "a28add4ce6b019e56c5bd9ec63bed217c4d5405c0bd3bd48c5ee814e506a339f"
)
FROZEN_CUTOFF = "2026-08-31T14:03:05Z"
PROTOCOL_VERSION = "proposition-ledger-phase2a-development-pilot-v1.0.0"
SELECTION_ALGORITHM_VERSION = "phase2a-development-selection-v2"
SELECTION_POLICY_VERSION = "phase2a-development-selection-v2"
DEVELOPMENT_STABILITY_POLICY_VERSION = (
    "frozen-open-prefix-development-exception-v1"
)
HELD_OUT_SEAL_POLICY_VERSION = (
    "substantive-content-and-experimental-exposure-seal-v2"
)
OPEN_PREFIX_EXCEPTION_REASON = (
    "sole_prelabelled_stressor_case_without_stable_substitute"
)
MAX_OPEN_PREFIX_EXCEPTIONS = 1
REQUEST_CONTRACT_REVISION = "phase1.4-no-tools-omit-tool-choice-v2"
MAX_OUTPUT_TOKENS = 4096
CLIENT_TIMEOUT_SECONDS = 300.0
MAX_PROVIDER_CALL_BUDGET = 64
MIN_UNIQUE_TURNS = 24
MAX_UNIQUE_TURNS = 32
SELECTED_CONVERSATION_COUNT = 8
APPLICATION_RETRIES = 0
STORE_MESSAGES = False
ERROR_LIMIT = 512

PROFILES: tuple[dict[str, Any], ...] = (
    {
        "profile_id": "xai-grok-4.3-low-ledger-v1",
        "provider": "xAI",
        "model": "grok-4.3",
        "reasoning_effort": "low",
    },
    {
        "profile_id": "xai-grok-4.6-low-ledger-v1",
        "provider": "xAI",
        "model": "grok-4.6",
        "reasoning_effort": "low",
    },
)
PROFILE_BY_ID = {item["profile_id"]: item for item in PROFILES}
PROFILE_BY_MODEL = {item["model"]: item for item in PROFILES}

DIRECT_EXPOSURE_CATEGORIES = frozenset(
    {
        "development_labelled",
        "prior_human_review",
        "calibration",
        "prior_model_experiment",
        "report_excerpt",
        "current_manual_incident_review",
    }
)
SEALED_STATUSES = frozenset({"genuinely_unexposed", "structurally_mined_only"})
TERMINAL_CALL_STATES = frozenset(
    {
        "provider_error_received",
        "strict_validation_failed",
        "materialisation_failed",
        "validated_and_materialised",
        "validated_with_diagnostic_flags",
        "uncertain_after_send",
        "blocked_by_prior_turn_failure",
        "not_attempted_due_to_global_failure",
    }
)
NONREPEATABLE_CALL_STATES = TERMINAL_CALL_STATES | {"sending", "response_received"}
VALID_CALL_STATES = NONREPEATABLE_CALL_STATES | {"planned"}
CHAIN_FAILURE_STATES = frozenset(
    {"provider_error_received", "strict_validation_failed", "materialisation_failed"}
)
GLOBAL_PROVIDER_FAILURES = frozenset(
    {
        "authentication_failure",
        "authorisation_failure",
        "billing_failure",
        "rate_limit_or_account_quota_failure",
        "transport_failure_before_send",
        "global_transport_failure",
        "timeout_after_possible_transmission",
        "unclassified_failure_after_send",
    }
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

STRESSORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "existence_availability_vs_security_durability_independence",
        ("existence versus security", "liberty existence", "availability versus"),
    ),
    (
        "valid_distinction_or_nearby_proposition",
        (
            "valid distinction",
            "different proposition",
            "rejected substitute",
            "enabling architecture",
            "premise distinction",
            "normative distinction",
        ),
    ),
    (
        "live_counterfactual_or_conditional",
        ("counterfactual", "conditional"),
    ),
    (
        "compound_allegation",
        ("compound allegation",),
    ),
    (
        "explicit_correction_or_qualification",
        ("explicit correction", "corrected relation", "healthy correction"),
    ),
    (
        "clarification_then_apparent_answer",
        ("account clarification", "answer to account clarification"),
    ),
    (
        "rhetorical_expressive_no_stable_issue",
        ("rhetorical expressive", "no stable proposition", "social exchange"),
    ),
    (
        "healthy_control",
        (
            "healthy sustained debate",
            "partial concession",
            "multilingual",
            "sustained debate",
            "direct engagement",
        ),
    ),
)
STRESSOR_IDS = tuple(item[0] for item in STRESSORS)


class PilotError(RuntimeError):
    """A bounded Phase 2A guard or validation failure."""


class SelectionBoundaryError(PilotError):
    """A record failed closed before any transcript field was accessed."""


class ProviderObservation:
    """The bounded visible observation saved for one provider response."""

    def __init__(
        self,
        raw_text: str,
        returned_model_id: str | None,
        provider_response_id: str | None,
        finish_reason: str | None,
        usage: Mapping[str, Any],
    ) -> None:
        """Store only visible response content and bounded provider metadata."""

        self.raw_text = raw_text
        self.returned_model_id = returned_model_id
        self.provider_response_id = provider_response_id
        self.finish_reason = finish_reason
        self.usage = usage


def utc_now() -> str:
    """Return a whole-second RFC 3339 UTC timestamp."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Return the repository's deterministic compact JSON encoding."""

    return preflight.canonical_json_bytes(value)


def pretty_json_bytes(value: Any) -> bytes:
    """Return the repository's deterministic private-file JSON encoding."""

    return preflight.pretty_json_bytes(value)


def sha256_bytes(value: bytes) -> str:
    """Return a lowercase SHA-256 digest for non-credential bytes."""

    return hashlib.sha256(value).hexdigest()


def value_sha256(value: Any) -> str:
    """Hash a JSON-compatible value using canonical bytes."""

    return sha256_bytes(canonical_json_bytes(value))


def _absolute(path: str | Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _real_directory(path: str | Path, label: str) -> Path:
    candidate = _absolute(path)
    try:
        metadata = candidate.lstat()
    except FileNotFoundError as exc:
        raise PilotError(f"{label} is missing: {candidate}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise PilotError(f"{label} is not a real directory")
    if candidate.resolve(strict=True) != candidate:
        raise PilotError(f"{label} has a symlink path component")
    return candidate


def _private_directory(path: str | Path, *, create: bool = False) -> Path:
    candidate = _absolute(path)
    if create:
        if os.path.lexists(candidate):
            existing = _real_directory(candidate, "private run")
            if stat.S_IMODE(existing.stat().st_mode) != 0o700:
                raise PilotError("existing private run mode is not 0700")
            if any(existing.iterdir()):
                raise PilotError(f"existing private run is not empty: {candidate}")
        else:
            _real_directory(candidate.parent, "private run parent")
            candidate.mkdir(mode=0o700)
            candidate.chmod(0o700)
    result = _real_directory(candidate, "private run")
    if stat.S_IMODE(result.stat().st_mode) != 0o700:
        raise PilotError("private run mode is not 0700")
    return result


def _ensure_private_subdir(path: Path) -> Path:
    if os.path.lexists(path):
        result = _real_directory(path, "private subdirectory")
    else:
        _real_directory(path.parent, "private subdirectory parent")
        path.mkdir(mode=0o700)
        path.chmod(0o700)
        result = _real_directory(path, "private subdirectory")
    if stat.S_IMODE(result.stat().st_mode) != 0o700:
        raise PilotError(f"private subdirectory mode is not 0700: {path}")
    return result


def _read_bytes(path: str | Path, label: str) -> bytes:
    candidate = _absolute(path)
    _real_directory(candidate.parent, f"{label} parent")
    try:
        before = candidate.lstat()
    except FileNotFoundError as exc:
        raise PilotError(f"{label} is missing: {candidate}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise PilotError(f"{label} is not a regular non-symlink file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(candidate, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise PilotError(f"{label} changed while opening")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def _atomic_write(path: str | Path, content: bytes) -> None:
    candidate = _absolute(path)
    parent = _real_directory(candidate.parent, "private output parent")
    if os.path.lexists(candidate):
        metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise PilotError(f"unsafe private output target: {candidate}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{candidate.name}.", suffix=".tmp", dir=parent
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
        os.replace(temporary, candidate)
        candidate.chmod(0o600)
        directory_descriptor = os.open(
            parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if os.path.lexists(temporary):
            temporary.unlink()


def _write_json(path: str | Path, value: Any) -> None:
    _atomic_write(path, pretty_json_bytes(value))


def _load_json(path: str | Path, label: str) -> dict[str, Any]:
    try:
        result = live_probe.strict_json_loads(_read_bytes(path, label))
    except live_probe.StrictJSONError as exc:
        raise PilotError(f"invalid strict JSON for {label}: {exc}") from exc
    return result


def _load_json_array(path: str | Path, label: str) -> list[Any]:
    raw = _read_bytes(path, label)
    try:
        text = raw.decode("utf-8", errors="strict")
        result = json.loads(
            text,
            object_pairs_hook=live_probe._duplicate_member_hook,
            parse_constant=live_probe._reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, live_probe.StrictJSONError) as exc:
        raise PilotError(f"invalid strict JSON for {label}") from exc
    if not isinstance(result, list):
        raise PilotError(f"{label} is not an array")
    return result


def _iter_jsonl_bytes(raw: bytes, label: str) -> Iterable[dict[str, Any]]:
    """Parse strict JSONL from one immutable byte buffer."""

    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            yield live_probe.strict_json_loads(line)
        except live_probe.StrictJSONError as exc:
            raise PilotError(f"invalid {label} line {line_number}: {exc}") from exc


def _iter_jsonl(path: str | Path, label: str) -> Iterable[dict[str, Any]]:
    raw = _read_bytes(path, label)
    yield from _iter_jsonl_bytes(raw, label)


def _load_schemas() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    canonical_raw = _read_bytes(CANONICAL_SCHEMA_PATH, "canonical schema")
    if sha256_bytes(canonical_raw) != EXPECTED_CANONICAL_SCHEMA_SHA256:
        raise PilotError("canonical schema SHA-256 mismatch")
    canonical = live_probe.strict_json_loads(canonical_raw)
    provider, transformations = preflight.transform_provider_schema(canonical)
    if value_sha256(provider) != EXPECTED_PROVIDER_SCHEMA_SHA256:
        raise PilotError("provider schema SHA-256 mismatch")
    counts = Counter(item.get("transformation_kind") for item in transformations)
    if counts != {
        "insert_explicit_additional_properties_true": 5,
        "remove_redundant_outer_anchors_for_xai_full_string_pattern": 14,
    } or len(transformations) != 19:
        raise PilotError("provider transformation ledger mismatch")
    persisted = _load_json(PERSISTED_LEDGER_SCHEMA_PATH, "persisted ledger schema")
    return canonical, provider, persisted


def _stressor_tags(raw_tags: Sequence[Any]) -> tuple[str, ...]:
    labels: list[str] = []
    for item in raw_tags:
        if isinstance(item, Mapping):
            item = item.get("stressor")
        if isinstance(item, str):
            labels.append(item.strip().lower())
    found = []
    for stressor_id, phrases in STRESSORS:
        if any(phrase in label for label in labels for phrase in phrases):
            found.append(stressor_id)
    return tuple(found)


def _protected_metadata_row(record: Mapping[str, Any]) -> bool:
    return bool(
        record.get("preliminary_within_family_held_out_eligibility") is True
        or record.get("preliminary_held_out_eligibility") is True
        or any(
            record.get(field) in {"genuinely_unexposed", "structurally_mined_only"}
            for field in (
                "effective_exposure_status",
                "conversation_exposure_status",
                "target_exposure_status",
            )
        )
    )


def _direct_metadata_row(record: Mapping[str, Any]) -> bool:
    categories = {
        str(value)
        for value in record.get("author_group_exposure_categories", [])
        if isinstance(value, str)
    }
    return (
        not _protected_metadata_row(record)
        and record.get("preliminary_within_family_held_out_eligibility") is False
        and record.get("preliminary_held_out_eligibility") in {None, False}
        and isinstance(record.get("target_sequence_class"), str)
        and bool(record.get("target_sequence_class"))
        and all(
            record.get(field) == "exposed"
            for field in (
                "effective_exposure_status",
                "conversation_exposure_status",
                "target_exposure_status",
            )
        )
        and bool(categories & DIRECT_EXPOSURE_CATEGORIES)
    )


def _sidecar_projection(record: Mapping[str, Any]) -> dict[str, Any]:
    """Project the minimum administrative fields; never include outcome data."""

    result = {
        "artifact_evidence": "frozen input",
        "source_metadata_row_sha256": record["row_sha256"],
        "conversation_key": record["conversation_key"],
        "target_turn_id": record["target_turn_id"],
        "target_sequence_class": record["target_sequence_class"],
        "source_family": record["source_family"],
        "prefix_turn_count": record["prefix_turn_count"],
        "reconstruction_grade": record["reconstruction_grade"],
        "complete_target_ancestry": record["complete_target_ancestry"],
        "stability_status": record["stability_status"],
        "activity_status_at_frozen_cutoff": record[
            "activity_status_at_frozen_cutoff"
        ],
        "effective_exposure_status": record["effective_exposure_status"],
        "conversation_exposure_status": record["conversation_exposure_status"],
        "target_exposure_status": record["target_exposure_status"],
        "exposure_categories": sorted(
            {
                str(value)
                for value in record.get("author_group_exposure_categories", [])
                if isinstance(value, str)
            }
        ),
        "preliminary_within_family_held_out_eligibility": record[
            "preliminary_within_family_held_out_eligibility"
        ],
        "preliminary_held_out_eligibility": record.get(
            "preliminary_held_out_eligibility", False
        ),
        "target_author_identity_status": record["target_author_identity_status"],
        "target_author_conflict_count": record.get(
            "review_candidate_principal_conflict_count", 0
        ),
        "within_family_author_group_key": record.get(
            "within_family_author_group_key"
        ),
        "administrative_protected_metadata_scan_only": False,
    }
    result["sidecar_row_sha256"] = value_sha256(result)
    return result


def _contains_substantive_metadata_field(value: Any) -> bool:
    """Detect transcript or quoted-content fields without rendering their values."""

    forbidden = {
        "text",
        "transcript",
        "transcript_prefix",
        "transcript_text",
        "quoted_text",
        "quote_text",
        "visible_text",
        "exact_text",
        "raw_text",
        "current_visible_text",
        "conversation_text",
    }
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalised = str(key).strip().lower()
            if (
                normalised in forbidden
                or "transcript" in normalised
                or normalised.startswith("quoted_")
                or normalised.endswith("_quoted_text")
            ):
                return True
            if _contains_substantive_metadata_field(child):
                return True
    elif isinstance(value, list):
        return any(_contains_substantive_metadata_field(item) for item in value)
    return False


def _write_metadata_boundary_evidence(
    output_dir: Path | None,
    sidecar: Sequence[Mapping[str, Any]],
    audit: Mapping[str, Any],
) -> None:
    """Durably record the metadata boundary before any transcript dereference."""

    if output_dir is None:
        return
    sidecar_bytes = b"".join(
        canonical_json_bytes(item) + b"\n" for item in sidecar
    )
    _atomic_write(output_dir / "exposed-development-candidate-index.jsonl", sidecar_bytes)
    _atomic_write(
        output_dir / "held-out-content-seal-audit.json", pretty_json_bytes(audit)
    )


def _record_protected_content_access_attempt(
    output_dir: Path, access_class: str
) -> None:
    """Durably mark a forbidden substantive-access event, then fail closed."""

    allowed_classes = {
        "protected_transcript_open_attempt",
        "protected_source_mapping_open_attempt",
        "protected_provider_payload_attempt",
        "protected_human_review_attempt",
        "unexpected_substantive_metadata",
    }
    if access_class not in allowed_classes:
        raise PilotError("unknown protected-content access event class")
    path = output_dir / "held-out-content-seal-audit.json"
    audit = _load_json(path, "held-out content seal audit")
    audit["substantive_seal_breached"] = True
    audit["substantive_access_guard_event"] = access_class
    audit["protected_transcript_text_bytes_read"] = "unknown_not_measured"
    if access_class == "protected_transcript_open_attempt":
        audit["protected_transcript_records_opened"] = max(
            1, int(audit.get("protected_transcript_records_opened", 0))
        )
    elif access_class == "protected_source_mapping_open_attempt":
        audit["protected_source_mapping_records_opened"] = max(
            1, int(audit.get("protected_source_mapping_records_opened", 0))
        )
    elif access_class == "protected_provider_payload_attempt":
        audit["protected_provider_payload_count"] = max(
            1, int(audit.get("protected_provider_payload_count", 0))
        )
    elif access_class == "protected_human_review_attempt":
        audit["protected_human_review_count"] = max(
            1, int(audit.get("protected_human_review_count", 0))
        )
    _write_json(path, audit)
    raise PilotError("protected substantive content access blocked and recorded")


def build_exposed_metadata_sidecar(
    output_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Scan administrative metadata and emit only directly exposed rows.

    Protected rows are counted in aggregate, never copied, rendered, mapped,
    dereferenced, or returned.  The source contains no transcript text.
    """

    raw = _read_bytes(TARGET_PREFIX_METADATA_PATH, "target-prefix administrative metadata")
    rows = list(_iter_jsonl_bytes(raw, "target-prefix metadata"))
    substantive_rows = sum(_contains_substantive_metadata_field(row) for row in rows)
    if substantive_rows:
        breached_audit = {
            "artifact_evidence": "deterministic derivation",
            "policy_version": HELD_OUT_SEAL_POLICY_VERSION,
            "source_metadata_file_sha256": sha256_bytes(raw),
            "source_metadata_rows_scanned": len(rows),
            "protected_metadata_rows_scanned_for_administration": 0,
            "protected_metadata_rows_rendered_to_operator": 0,
            "protected_rows_written_to_development_sidecar": 0,
            "protected_transcript_records_opened": substantive_rows,
            "protected_transcript_text_bytes_read": "unknown_not_measured",
            "protected_source_mapping_records_opened": 0,
            "protected_provider_payload_count": 0,
            "protected_model_output_count": 0,
            "protected_human_review_count": 0,
            "directly_exposed_rows_written_to_sidecar": 0,
            "structurally_mined_only_rows_excluded": 0,
            "genuinely_unexposed_rows_excluded": 0,
            "preliminarily_held_out_rows_excluded": 0,
            "unexpected_substantive_metadata_rows": substantive_rows,
            "substantive_seal_breached": True,
            "prior_protected_metadata_administrative_scan": True,
            "prior_scan_report": (
                "mrsMThatcher-proposition-ledger-phase2a-early-stop-report-"
                "20260901T094004Z.md"
            ),
            "prior_protected_metadata_rows_known_minimum": 2,
            "prior_genuinely_unexposed_metadata_row_count": "unknown",
            "prior_protected_transcript_text_bytes_read": 0,
            "prior_protected_provider_payload_count": 0,
            "prior_protected_model_output_count": 0,
            "prior_substantive_seal_breached": False,
        }
        _write_metadata_boundary_evidence(output_dir, [], breached_audit)
        raise PilotError(
            "administrative metadata unexpectedly contains substantive content fields"
        )
    protected_count = 0
    structural_count = 0
    unexposed_count = 0
    held_out_count = 0
    exposed: list[dict[str, Any]] = []
    for row in rows:
        declared = row.get("row_sha256")
        hash_input = copy.deepcopy(row)
        hash_input.pop("row_sha256", None)
        if value_sha256(hash_input) != declared:
            raise PilotError("administrative metadata row SHA-256 mismatch")
        held_out = bool(
            row.get("preliminary_within_family_held_out_eligibility") is True
            or row.get("preliminary_held_out_eligibility") is True
        )
        effective = row.get("effective_exposure_status")
        held_out_count += int(held_out)
        structural_count += int(
            any(
                row.get(field) == "structurally_mined_only"
                for field in (
                    "effective_exposure_status",
                    "conversation_exposure_status",
                    "target_exposure_status",
                )
            )
        )
        unexposed_count += int(
            any(
                row.get(field) == "genuinely_unexposed"
                for field in (
                    "effective_exposure_status",
                    "conversation_exposure_status",
                    "target_exposure_status",
                )
            )
        )
        if _protected_metadata_row(row):
            protected_count += 1
            continue
        if _direct_metadata_row(row):
            exposed.append(_sidecar_projection(row))
    if not exposed:
        raise PilotError("exposed development metadata sidecar is empty")
    audit = {
        "artifact_evidence": "deterministic derivation",
        "policy_version": HELD_OUT_SEAL_POLICY_VERSION,
        "source_metadata_file_sha256": sha256_bytes(raw),
        "source_metadata_rows_scanned": len(rows),
        "protected_metadata_rows_scanned_for_administration": protected_count,
        "protected_metadata_rows_rendered_to_operator": 0,
        "protected_rows_written_to_development_sidecar": 0,
        "protected_transcript_records_opened": 0,
        "protected_transcript_text_bytes_read": 0,
        "protected_source_mapping_records_opened": 0,
        "protected_provider_payload_count": 0,
        "protected_model_output_count": 0,
        "protected_human_review_count": 0,
        "directly_exposed_rows_written_to_sidecar": len(exposed),
        "structurally_mined_only_rows_excluded": structural_count,
        "genuinely_unexposed_rows_excluded": unexposed_count,
        "preliminarily_held_out_rows_excluded": held_out_count,
        "substantive_seal_breached": False,
        "prior_protected_metadata_administrative_scan": True,
        "prior_scan_report": (
            "mrsMThatcher-proposition-ledger-phase2a-early-stop-report-"
            "20260901T094004Z.md"
        ),
        "prior_protected_metadata_rows_known_minimum": 2,
        "prior_genuinely_unexposed_metadata_row_count": "unknown",
        "prior_protected_transcript_text_bytes_read": 0,
        "prior_protected_provider_payload_count": 0,
        "prior_protected_model_output_count": 0,
        "prior_substantive_seal_breached": False,
    }
    result = sorted(
        exposed,
        key=lambda item: (str(item["conversation_key"]), str(item["target_turn_id"])),
    )
    _write_metadata_boundary_evidence(output_dir, result, audit)
    return result, audit


def open_prefix_exception_binding(record: Mapping[str, Any]) -> str:
    """Hash only administrative fields that authorise the single exception."""

    return value_sha256(
        {
            "policy_version": DEVELOPMENT_STABILITY_POLICY_VERSION,
            "calibration_case_id": record.get("calibration_case_id"),
            "source_metadata_row_sha256": record.get("source_metadata_row_sha256"),
            "record_sha256": record.get("record_sha256"),
            "target_turn_id": record.get("target_turn_id"),
            "prefix_turn_count": record.get("prefix_turn_count"),
            "stability_status": record.get("stability_status"),
            "effective_exposure_status": record.get("effective_exposure_status"),
            "reason": record.get("open_prefix_exception_reason"),
            "frozen_cutoff": record.get("frozen_cutoff"),
        }
    )


def _validate_stability_before_text(record: Mapping[str, Any]) -> None:
    stability = record.get("stability_status")
    if stability in {
        "frozen_historical",
        "quiescent_at_frozen_cutoff",
    }:
        if record.get("frozen_open_prefix_exception") is True:
            raise SelectionBoundaryError("stable record cannot claim open-prefix exception")
        return
    if stability != "open_at_frozen_cutoff":
        raise SelectionBoundaryError("stability status absent or ineligible")
    checks = {
        "policy": record.get("development_stability_policy_version")
        == DEVELOPMENT_STABILITY_POLICY_VERSION,
        "case": bool(record.get("calibration_case_id"))
        and record.get("calibration_case_id")
        == record.get("authorised_open_prefix_exception_case_id"),
        "exception": record.get("frozen_open_prefix_exception") is True,
        "reason": record.get("open_prefix_exception_reason")
        == OPEN_PREFIX_EXCEPTION_REASON,
        "immutability": record.get("selected_prefix_immutability_status")
        == "frozen_at_cutoff",
        "post_target": record.get("post_target_extension_withheld") is True,
        "post_cutoff": record.get("post_cutoff_extension_withheld") is True,
        "cutoff": record.get("frozen_cutoff") == FROZEN_CUTOFF,
        "direct": record.get("effective_exposure_status") == "exposed",
        "grade": record.get("source_grade") == "A"
        or record.get("reconstruction_grade") == "A",
        "ancestry": record.get("complete_target_ancestry") is True,
        "author": record.get("target_author_identity_status") == "available",
        "conflict": record.get("target_author_conflict_count", 0) == 0,
        "compound": "compound_allegation" in record.get("stressor_tags", ()),
        "clarification": "clarification_then_apparent_answer"
        in record.get("stressor_tags", ()),
        "no_substitute": record.get("stable_direct_compound_substitute_count") == 0,
    }
    declared_binding = record.get("open_prefix_exception_binding_sha256")
    checks["binding"] = isinstance(declared_binding, str) and declared_binding == (
        open_prefix_exception_binding(record)
    )
    if not all(checks.values()):
        failed = ",".join(sorted(key for key, passed in checks.items() if not passed))
        raise SelectionBoundaryError(f"frozen open-prefix exception invalid:{failed}")


def enforce_selection_boundary(record: Mapping[str, Any]) -> dict[str, Any]:
    """Validate exposure and Grade A metadata before touching transcript text.

    The function intentionally reads ``transcript_prefix`` only after every
    held-out/unexposed/structural-only guard.  Tests use sentinel mappings to
    prove that ordering.  Calibration-pack records may use the pack-level
    frozen assertions represented by ``calibration_container_*`` fields.
    """

    held_out = record.get("preliminary_within_family_held_out_eligibility")
    if held_out is not False:
        raise SelectionBoundaryError(
            "preliminary held-out eligibility is true, absent, or ambiguous"
        )
    if record.get("preliminary_held_out_eligibility") is True:
        raise SelectionBoundaryError("preliminarily held-out record rejected")
    for field in (
        "effective_exposure_status",
        "conversation_exposure_status",
        "target_exposure_status",
    ):
        if record.get(field) in SEALED_STATUSES:
            status = (
                "genuinely unexposed"
                if record.get(field) == "genuinely_unexposed"
                else "structurally-mined-only"
            )
            raise SelectionBoundaryError(f"{status} record rejected: {field}")
    allowed_direct_statuses = {"exposed", "directly_exposed"}
    if any(
        record.get(field) not in allowed_direct_statuses
        for field in (
            "effective_exposure_status",
            "conversation_exposure_status",
            "target_exposure_status",
        )
    ):
        raise SelectionBoundaryError(
            "direct exposure status is absent, inconsistent, or ambiguous"
        )

    raw_categories = record.get("exposure_categories")
    if raw_categories is None:
        raw_categories = record.get("exposure_statuses")
    categories = {
        str(value)
        for value in (raw_categories or [])
        if isinstance(value, str) and value
    }
    calibration_asserted = (
        record.get("calibration_container_all_previously_exposed") is True
        and bool(record.get("prior_exposure_reasons"))
    )
    if calibration_asserted and raw_categories is None:
        categories.add("calibration")
    if not categories or not (categories & DIRECT_EXPOSURE_CATEGORIES):
        if categories == {"structurally_mined_only"}:
            raise SelectionBoundaryError("structurally-mined-only record rejected")
        raise SelectionBoundaryError("direct exposure evidence absent or ambiguous")
    if categories <= {"structurally_mined_only"}:
        raise SelectionBoundaryError("structurally-mined-only record rejected")
    if record.get("source_grade") != "A" and record.get("reconstruction_grade") != "A":
        raise SelectionBoundaryError("non-Grade-A record rejected")
    if record.get("calibration_container_all_grade_a") is False:
        raise SelectionBoundaryError("calibration container Grade-A assertion failed")
    if record.get("complete_target_ancestry") is not True:
        raise SelectionBoundaryError("complete exact target ancestry is not confirmed")
    if record.get("target_author_identity_status") != "available":
        raise SelectionBoundaryError("target author identity is unavailable")
    if int(record.get("target_author_conflict_count", 0) or 0) != 0:
        raise SelectionBoundaryError("target author identity is conflicting")

    _validate_stability_before_text(record)

    prefix = record.get("transcript_prefix")  # first transcript access
    if not isinstance(prefix, list) or not prefix:
        raise SelectionBoundaryError("complete transcript prefix absent")
    turns = copy.deepcopy(prefix)
    cutoff = datetime.fromisoformat(FROZEN_CUTOFF.replace("Z", "+00:00"))
    prior_timestamp: datetime | None = None
    for expected_index, turn in enumerate(turns):
        if not isinstance(turn, Mapping):
            raise SelectionBoundaryError("transcript turn is not an object")
        if turn.get("turn_index") != expected_index:
            raise SelectionBoundaryError("turn indexes are not exact and consecutive")
        if not isinstance(turn.get("turn_id"), str) or not turn.get("turn_id"):
            raise SelectionBoundaryError("turn identity absent")
        if not isinstance(turn.get("text"), str):
            raise SelectionBoundaryError("turn exact text absent")
        if turn.get("publication_status") not in {"observed", "confirmed", "published"}:
            raise SelectionBoundaryError(
                "turn publication identity is not observed, confirmed, or published"
            )
        timestamp = turn.get("timestamp")
        if not isinstance(timestamp, str) or not timestamp:
            raise SelectionBoundaryError("turn timestamp is absent")
        try:
            parsed_timestamp = datetime.fromisoformat(
                timestamp.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise SelectionBoundaryError("turn timestamp is invalid") from exc
        if parsed_timestamp.tzinfo is None:
            raise SelectionBoundaryError("turn timestamp lacks a UTC offset")
        parsed_timestamp = parsed_timestamp.astimezone(timezone.utc)
        if parsed_timestamp > cutoff:
            raise SelectionBoundaryError(
                "selected prefix contains a post-cutoff turn"
            )
        if prior_timestamp is not None and parsed_timestamp < prior_timestamp:
            raise SelectionBoundaryError("turn chronology is not exact")
        prior_timestamp = parsed_timestamp
        if (
            turn.get("author_role") != "account"
            and (not isinstance(turn.get("author_key"), str) or not turn.get("author_key"))
        ):
            raise SelectionBoundaryError("turn author identity absent")
        parent = turn.get("parent_turn_id")
        if expected_index == 0:
            if parent is not None:
                raise SelectionBoundaryError("selected prefix does not begin at genesis")
        else:
            if parent != turns[expected_index - 1]["turn_id"]:
                raise SelectionBoundaryError("ancestor link is not exact")
    if record.get("target_turn_id") != turns[-1].get("turn_id"):
        raise SelectionBoundaryError("target is not deepest prefix turn")
    return {
        "record": copy.deepcopy(dict(record)),
        "turns": turns,
        "exposure_categories": tuple(sorted(categories)),
    }


def _candidate_from_group(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = sorted(records, key=lambda item: str(item.get("calibration_case_id", "")))
    validated = [enforce_selection_boundary(item) for item in ordered]
    deepest = max(
        validated,
        key=lambda item: (
            len(item["turns"]),
            str(item["record"].get("calibration_case_id", "")),
        ),
    )
    record = deepest["record"]
    all_raw_tags = [
        tag
        for item in validated
        for tag in item["record"].get("intended_schema_stressors", [])
    ]
    target_author = str(deepest["turns"][-1]["author_key"])
    source_provenance = record.get("source_provenance", [])
    source_family = (
        "prospective-v4"
        if any(isinstance(item, Mapping) for item in source_provenance)
        else "benchmark"
    )
    direct_strength = len(record.get("prior_exposure_reasons", []))
    return {
        "calibration_case_id": str(record["calibration_case_id"]),
        "conversation_key": str(record["conversation_key"]),
        "turns": deepest["turns"],
        "turn_count": len(deepest["turns"]),
        "stressor_tags": _stressor_tags(all_raw_tags),
        "raw_stressor_tags": sorted(
            {
                str(tag.get("stressor") if isinstance(tag, Mapping) else tag)
                for tag in all_raw_tags
            }
        ),
        "source_family": source_family,
        "contributor_group_source_key": target_author,
        "direct_exposure_strength": direct_strength,
        "exposure_categories": deepest["exposure_categories"],
        "prior_exposure_reasons": copy.deepcopy(record.get("prior_exposure_reasons", [])),
        "record_sha256": record.get("record_sha256"),
        "source_record": record,
        "stability_status": record.get("stability_status"),
        "frozen_open_prefix_exception": bool(
            record.get("frozen_open_prefix_exception")
        ),
        "open_prefix_exception_reason": record.get("open_prefix_exception_reason"),
    }


def select_development_cases(
    records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Select exactly eight deepest independent exposed conversations."""

    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in sorted(
        records,
        key=lambda item: (
            str(item.get("conversation_key", "")),
            str(item.get("calibration_case_id", "")),
        ),
    ):
        # Boundary validation is intentionally before grouping by transcript data.
        enforce_selection_boundary(record)
        grouped[str(record.get("conversation_key", ""))].append(record)
    candidates = [_candidate_from_group(items) for _, items in sorted(grouped.items())]
    return _choose_candidate_combination(candidates)


def _choose_candidate_combination(
    candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if len(candidates) < SELECTED_CONVERSATION_COUNT:
        raise PilotError(
            f"only {len(candidates)} suitable independent conversations are available"
        )
    glc_available = any(
        item["source_family"] == "prospective-v4"
        and "compound_allegation" in item["stressor_tags"]
        for item in candidates
    )
    available_group_count = len(
        {item["contributor_group_source_key"] for item in candidates}
    )
    required_groups = min(6, available_group_count)

    feasible: list[tuple[tuple[Any, ...], tuple[dict[str, Any], ...]]] = []
    for combination in itertools.combinations(candidates, SELECTED_CONVERSATION_COUNT):
        total_turns = sum(item["turn_count"] for item in combination)
        if not MIN_UNIQUE_TURNS <= total_turns <= MAX_UNIQUE_TURNS:
            continue
        covered = {tag for item in combination for tag in item["stressor_tags"]}
        if not set(STRESSOR_IDS) <= covered:
            continue
        groups = Counter(item["contributor_group_source_key"] for item in combination)
        if groups and max(groups.values()) > 2:
            continue
        if len(groups) < required_groups:
            continue
        if glc_available and not any(
            item["source_family"] == "prospective-v4"
            and "compound_allegation" in item["stressor_tags"]
            for item in combination
        ):
            continue
        per_stressor = []
        for stressor in STRESSOR_IDS:
            matching = [item for item in combination if stressor in item["stressor_tags"]]
            best = max(
                matching,
                key=lambda item: (
                    item["direct_exposure_strength"],
                    item["turn_count"],
                    item["calibration_case_id"],
                ),
            )
            per_stressor.extend(
                (best["direct_exposure_strength"], best["turn_count"])
            )
        case_ids = tuple(sorted(item["calibration_case_id"] for item in combination))
        # Negative lexical bytes make the last tie rule choose the earliest IDs.
        lexical_tiebreak = tuple(-byte for byte in "\0".join(case_ids).encode("utf-8"))
        score = (
            tuple(per_stressor),
            len(groups),
            len({item["source_family"] for item in combination}),
            total_turns,
            lexical_tiebreak,
        )
        feasible.append((score, combination))
    if not feasible:
        raise PilotError(
            "eight suitable conversations cannot be selected within the 24-32-turn cap"
        )
    chosen = max(feasible, key=lambda item: item[0])[1]
    result = [
        copy.deepcopy(dict(item))
        for item in sorted(chosen, key=lambda x: x["calibration_case_id"])
    ]
    if sum(bool(item.get("frozen_open_prefix_exception")) for item in result) > (
        MAX_OPEN_PREFIX_EXCEPTIONS
    ):
        raise PilotError("more than one frozen-open-prefix exception was selected")
    return result


def select_development_case_metadata(
    calibration_index: Sequence[Mapping[str, Any]],
    exposed_sidecar: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Select cases from exposed administrative metadata before transcript load."""

    sidecar_by_target = {
        (str(item["conversation_key"]), str(item["target_turn_id"])): item
        for item in exposed_sidecar
    }
    grouped: dict[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = defaultdict(list)
    for item in calibration_index:
        key = (str(item.get("conversation_key")), str(item.get("target_turn_id")))
        sidecar = sidecar_by_target.get(key)
        if sidecar is None:
            continue
        if item.get("source_grade") != "A" or sidecar.get("reconstruction_grade") != "A":
            continue
        if (
            sidecar.get("complete_target_ancestry") is not True
            or sidecar.get("target_author_identity_status") != "available"
            or int(sidecar.get("target_author_conflict_count", 0) or 0) != 0
        ):
            continue
        grouped[key[0]].append((item, sidecar))
    metadata_candidates: list[dict[str, Any]] = []
    for conversation_key, records in sorted(grouped.items()):
        deepest_index, deepest_sidecar = max(
            records,
            key=lambda pair: (
                int(pair[0].get("prefix_turn_count", 0)),
                str(pair[0].get("calibration_case_id", "")),
            ),
        )
        raw_tags = [
            tag
            for index_item, _ in records
            for tag in index_item.get("intended_schema_stressors", [])
        ]
        stressors = _stressor_tags(raw_tags)
        metadata_candidates.append(
            {
                "calibration_case_id": str(deepest_index["calibration_case_id"]),
                "conversation_key": conversation_key,
                "turn_count": int(deepest_index["prefix_turn_count"]),
                "stressor_tags": stressors,
                "raw_stressor_tags": sorted({str(tag) for tag in raw_tags}),
                "deepest_raw_stressor_tags": sorted(
                    str(tag)
                    for tag in deepest_index.get("intended_schema_stressors", [])
                ),
                "source_family": deepest_sidecar["source_family"],
                "contributor_group_source_key": str(
                    deepest_sidecar.get("within_family_author_group_key")
                    or deepest_sidecar["sidecar_row_sha256"]
                ),
                "direct_exposure_strength": int(
                    deepest_index.get("prior_exposure_reason_count", 1)
                ),
                "exposure_categories": tuple(
                    deepest_sidecar.get("exposure_categories", [])
                ),
                "record_sha256": deepest_index["record_sha256"],
                "target_turn_id": deepest_index["target_turn_id"],
                "sidecar": copy.deepcopy(dict(deepest_sidecar)),
                "stability_status": deepest_sidecar["stability_status"],
                "frozen_open_prefix_exception": False,
            }
        )
    open_labelled = [
        item
        for item in metadata_candidates
        if item["stability_status"] == "open_at_frozen_cutoff"
        and "compound_allegation"
        in _stressor_tags(item["deepest_raw_stressor_tags"])
        and "clarification_then_apparent_answer"
        in _stressor_tags(item["deepest_raw_stressor_tags"])
    ]
    stable_compound = [
        item
        for item in metadata_candidates
        if item["stability_status"]
        in {"frozen_historical", "quiescent_at_frozen_cutoff"}
        and "compound_allegation" in item["stressor_tags"]
    ]
    if len(open_labelled) != 1 or stable_compound:
        raise PilotError(
            "frozen-open-prefix exception is not the sole prelabelled stressor case "
            "without a stable directly exposed substitute"
        )
    exception = open_labelled[0]
    exception["frozen_open_prefix_exception"] = True
    exception["stable_direct_compound_substitute_count"] = 0
    eligible = [
        item
        for item in metadata_candidates
        if item["stability_status"]
        in {"frozen_historical", "quiescent_at_frozen_cutoff"}
        or item is exception
    ]
    return _choose_candidate_combination(eligible)


def _selected_calibration_records(
    calibration_index: Sequence[Mapping[str, Any]],
    selected_metadata: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Parse transcript JSON only for the already selected exposed records."""

    wanted = {str(item["calibration_case_id"]) for item in selected_metadata}
    ordered_ids = [str(item["calibration_case_id"]) for item in calibration_index]
    raw = _read_bytes(CALIBRATION_RECORDS_PATH, "exposed calibration transcript records")
    declared_hash = _load_json(CALIBRATION_MANIFEST_PATH, "calibration manifest").get(
        "records_sha256"
    )
    if sha256_bytes(raw) != declared_hash:
        raise PilotError("calibration records SHA-256 mismatch")
    lines = [line for line in raw.splitlines() if line.strip()]
    if len(lines) != len(ordered_ids):
        raise PilotError("calibration record/index line count mismatch")
    selected: dict[str, dict[str, Any]] = {}
    for expected_id, line in zip(ordered_ids, lines):
        if expected_id not in wanted:
            continue
        record = live_probe.strict_json_loads(line)
        if record.get("calibration_case_id") != expected_id:
            raise PilotError("selected calibration line ordering mismatch")
        selected[expected_id] = record
    if set(selected) != wanted:
        raise PilotError("selected calibration transcript record is absent")
    return selected


def materialise_selected_case_inputs(
    selected_metadata: Sequence[Mapping[str, Any]],
    calibration_index: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Join selected sidecar rows to exact exposed transcript prefixes."""

    selected_records = _selected_calibration_records(
        calibration_index, selected_metadata
    )
    result: list[dict[str, Any]] = []
    for metadata in selected_metadata:
        case_id = str(metadata["calibration_case_id"])
        record = copy.deepcopy(selected_records[case_id])
        sidecar = metadata["sidecar"]
        record_for_hash = copy.deepcopy(record)
        declared_record_hash = record_for_hash.pop("record_sha256", None)
        if value_sha256(record_for_hash) != declared_record_hash:
            raise PilotError("selected calibration record SHA-256 mismatch")
        if declared_record_hash != metadata["record_sha256"]:
            raise PilotError("selected calibration index record binding mismatch")
        augmented = copy.deepcopy(record)
        augmented.update(
            {
                "calibration_container_all_grade_a": True,
                "calibration_container_all_previously_exposed": True,
                "exposure_categories": list(sidecar["exposure_categories"]),
                "preliminary_within_family_held_out_eligibility": False,
                "effective_exposure_status": sidecar["effective_exposure_status"],
                "conversation_exposure_status": sidecar[
                    "conversation_exposure_status"
                ],
                "target_exposure_status": sidecar["target_exposure_status"],
                "reconstruction_grade": sidecar["reconstruction_grade"],
                "complete_target_ancestry": sidecar["complete_target_ancestry"],
                "stability_status": sidecar["stability_status"],
                "target_author_identity_status": sidecar[
                    "target_author_identity_status"
                ],
                "target_author_conflict_count": sidecar[
                    "target_author_conflict_count"
                ],
                "source_metadata_row_sha256": sidecar[
                    "source_metadata_row_sha256"
                ],
                "prefix_turn_count": sidecar["prefix_turn_count"],
                "stressor_tags": list(metadata["stressor_tags"]),
                "development_stability_policy_version": (
                    DEVELOPMENT_STABILITY_POLICY_VERSION
                ),
                "frozen_cutoff": FROZEN_CUTOFF,
                "frozen_open_prefix_exception": bool(
                    metadata["frozen_open_prefix_exception"]
                ),
            }
        )
        if metadata["frozen_open_prefix_exception"]:
            augmented.update(
                {
                    "authorised_open_prefix_exception_case_id": case_id,
                    "open_prefix_exception_reason": OPEN_PREFIX_EXCEPTION_REASON,
                    "selected_prefix_immutability_status": "frozen_at_cutoff",
                    "post_target_extension_withheld": True,
                    "post_cutoff_extension_withheld": True,
                    "stable_direct_compound_substitute_count": 0,
                }
            )
            augmented["open_prefix_exception_binding_sha256"] = (
                open_prefix_exception_binding(augmented)
            )
        validated = enforce_selection_boundary(augmented)
        if len(validated["turns"]) != int(sidecar["prefix_turn_count"]):
            raise PilotError("selected exact prefix length differs from sidecar")
        if any(
            not isinstance(turn.get("timestamp"), str)
            or turn["timestamp"] > FROZEN_CUTOFF
            for turn in validated["turns"]
        ):
            raise PilotError("selected prefix contains a post-cutoff turn")
        candidate = copy.deepcopy(dict(metadata))
        candidate.update(
            {
                "turns": validated["turns"],
                "turn_count": len(validated["turns"]),
                "source_record": augmented,
                "prior_exposure_reasons": copy.deepcopy(
                    record.get("prior_exposure_reasons", [])
                ),
            }
        )
        result.append(candidate)
    return result


def _localise_cases(
    selected: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    source_map: dict[str, Any] = {
        "artifact_evidence": "frozen input",
        "mapping_version": "phase2a-private-source-id-map-v1",
        "cases": [],
    }
    group_keys = sorted({str(item["contributor_group_source_key"]) for item in selected})
    group_pseudonyms = {
        key: f"contributor-group-{index:03d}"
        for index, key in enumerate(group_keys, start=1)
    }
    for case_number, selected_case in enumerate(selected, start=1):
        conversation_id = f"dev-conversation-{case_number:03d}"
        author_order: list[str] = []
        for turn in selected_case["turns"]:
            author = str(turn["author_key"])
            if author not in author_order and turn.get("author_role") != "account":
                author_order.append(author)
        contributor_ids = {
            author: f"participant-contributor-{index:03d}"
            for index, author in enumerate(author_order, start=1)
        }
        local_turns: list[dict[str, Any]] = []
        turn_id_map: dict[str, str] = {}
        for turn_number, turn in enumerate(selected_case["turns"]):
            local_id = f"dev-turn-{case_number:03d}-{turn_number:03d}"
            turn_id_map[str(turn["turn_id"])] = local_id
            role = "account" if turn.get("author_role") == "account" else "contributor"
            participant = (
                "participant-account"
                if role == "account"
                else contributor_ids[str(turn["author_key"])]
            )
            parent_source = turn.get("parent_turn_id")
            local_turns.append(
                {
                    "turn_id": local_id,
                    "turn_index": turn_number,
                    "parent_turn_id": (
                        turn_id_map[str(parent_source)] if parent_source is not None else None
                    ),
                    "text": turn["text"],
                    "speaker": {
                        "participant_id": participant,
                        "role": role,
                        "author_key": f"pilot-author-{participant}",
                        "identity_confidence": 1.0,
                    },
                    "publication_status": turn["publication_status"],
                }
            )
        row: dict[str, Any] = {
            "artifact_evidence": "frozen input",
            "development_case_id": f"dev-case-{case_number:03d}",
            "pilot_conversation_id": conversation_id,
            "exposure_evidence": {
                "categories": list(selected_case["exposure_categories"]),
                "direct_reason_count": len(selected_case["prior_exposure_reasons"]),
                "calibration_pack_previously_exposed": True,
            },
            "reconstruction_grade": "A",
            "stability_status": selected_case["stability_status"],
            "conversation_stability_status": selected_case["stability_status"],
            "selected_prefix_immutability_status": "frozen_at_cutoff",
            "frozen_open_prefix_exception": bool(
                selected_case.get("frozen_open_prefix_exception")
            ),
            "development_stability_exception": bool(
                selected_case.get("frozen_open_prefix_exception")
            ),
            "development_stability_policy_version": (
                DEVELOPMENT_STABILITY_POLICY_VERSION
            ),
            "development_stability_exception_policy": (
                DEVELOPMENT_STABILITY_POLICY_VERSION
            ),
            "open_prefix_exception_reason": (
                OPEN_PREFIX_EXCEPTION_REASON
                if selected_case.get("frozen_open_prefix_exception")
                else None
            ),
            "development_stability_exception_reason": (
                OPEN_PREFIX_EXCEPTION_REASON
                if selected_case.get("frozen_open_prefix_exception")
                else None
            ),
            "post_target_extension_withheld": True,
            "post_cutoff_extension_withheld": True,
            "stressor_tags": list(selected_case["stressor_tags"]),
            "source_family": selected_case["source_family"],
            "contributor_group_pseudonym": group_pseudonyms[
                str(selected_case["contributor_group_source_key"])
            ],
            "selected_target_turn": local_turns[-1]["turn_id"],
            "ordered_ancestor_turn_identities": [item["turn_id"] for item in local_turns],
            "exact_private_transcript_prefix": local_turns,
            "withheld_later_turn_count": None,
            "withheld_later_turn_count_status": "unknown_not_dereferenced",
            "historical_reply_after_target_withheld": True,
            "withheld_production_outcome_status": "withheld",
            "unique_turn_count": len(local_turns),
            "source_record_sha256": selected_case["record_sha256"],
            "source_metadata_row_sha256": (
                selected_case.get("sidecar", {}).get("source_metadata_row_sha256")
                if isinstance(selected_case.get("sidecar"), Mapping)
                else selected_case.get("source_record", {}).get(
                    "source_metadata_row_sha256"
                )
            ),
        }
        row["row_sha256"] = value_sha256(row)
        cases.append(row)
        source_map["cases"].append(
            {
                "development_case_id": row["development_case_id"],
                "source_calibration_case_id": selected_case["calibration_case_id"],
                "source_conversation_key": selected_case["conversation_key"],
                "source_target_post_id": selected_case["source_record"].get("target_post_id"),
                "source_target_turn_id": selected_case["source_record"].get("target_turn_id"),
                "source_turn_mappings": [
                    {
                        "pilot_turn_id": local_turn["turn_id"],
                        "source_turn_id": source_turn["turn_id"],
                        "source_post_id": source_turn.get("post_id"),
                        "source_author_key": source_turn["author_key"],
                    }
                    for local_turn, source_turn in zip(local_turns, selected_case["turns"])
                ],
                "source_contributor_group_key": selected_case[
                    "contributor_group_source_key"
                ],
            }
        )
    return cases, source_map


def _validate_frozen_calibration_sources() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read only the exposed-only calibration container and public manifests."""

    source_raw = _read_bytes(FROZEN_SOURCE_MANIFEST_PATH, "frozen source manifest")
    prospective_raw = _read_bytes(
        PROSPECTIVE_BATCH_MANIFEST_PATH, "frozen prospective batch manifest"
    )
    if sha256_bytes(source_raw) != EXPECTED_SOURCE_MANIFEST_SHA256:
        raise PilotError("frozen source-manifest SHA-256 mismatch")
    if sha256_bytes(prospective_raw) != EXPECTED_PROSPECTIVE_MANIFEST_SHA256:
        raise PilotError("frozen prospective batch-manifest SHA-256 mismatch")
    source_manifest = live_probe.strict_json_loads(source_raw)
    if source_manifest.get("cutoff") != FROZEN_CUTOFF:
        raise PilotError("frozen source cutoff mismatch")
    if (
        source_manifest.get("prospective_batch_manifest_sha256")
        != EXPECTED_PROSPECTIVE_MANIFEST_SHA256
    ):
        raise PilotError("source manifest prospective binding mismatch")

    calibration_manifest = _load_json(
        CALIBRATION_MANIFEST_PATH, "calibration manifest"
    )
    required_assertions = {
        "all_cases_grade_a": True,
        "all_cases_previously_exposed": True,
        "transcript_only": True,
        "contains_generated_ledgers": False,
        "contains_generated_replies": False,
        "conversation_count": 16,
        "target_prefix_count": 35,
    }
    if any(calibration_manifest.get(key) != value for key, value in required_assertions.items()):
        raise PilotError("calibration container assertions mismatch")
    index_raw = _read_bytes(CALIBRATION_INDEX_PATH, "calibration index")
    records_raw = _read_bytes(CALIBRATION_RECORDS_PATH, "calibration records")
    index = _load_json_array(CALIBRATION_INDEX_PATH, "calibration index")
    if value_sha256(index) != calibration_manifest.get("index_sha256"):
        raise PilotError("calibration index SHA-256 mismatch")
    if sha256_bytes(records_raw) != calibration_manifest.get("records_sha256"):
        raise PilotError("calibration records SHA-256 mismatch")
    records = list(_iter_jsonl(CALIBRATION_RECORDS_PATH, "calibration records"))
    if len(index) != 35 or len(records) != 35:
        raise PilotError("calibration target count mismatch")

    frozen_selection = {
        (str(item["conversation_key"]), str(target["calibration_case_id"])): target
        for item in source_manifest.get("calibration_selection", [])
        for target in item.get("targets", [])
    }
    index_keys = {
        (str(item.get("conversation_key")), str(item.get("calibration_case_id")))
        for item in index
    }
    record_keys = {
        (str(item.get("conversation_key")), str(item.get("calibration_case_id")))
        for item in records
    }
    if not frozen_selection or set(frozen_selection) != index_keys or index_keys != record_keys:
        raise PilotError("calibration selection does not match frozen source manifest")
    index_by_key = {
        (str(item["conversation_key"]), str(item["calibration_case_id"])): item
        for item in index
    }
    prepared_records: list[dict[str, Any]] = []
    for record in records:
        key = (str(record["conversation_key"]), str(record["calibration_case_id"]))
        index_item = index_by_key[key]
        frozen_item = frozen_selection[key]
        if (
            record.get("target_turn_id") != index_item.get("target_turn_id")
            or record.get("target_turn_id") != frozen_item.get("target_turn_id")
            or len(record.get("transcript_prefix", []))
            != index_item.get("prefix_turn_count")
            or record.get("source_grade") != index_item.get("source_grade")
            or record.get("record_sha256") != index_item.get("record_sha256")
        ):
            raise PilotError(f"calibration record/index mismatch: {key[1]}")
        record_for_hash = copy.deepcopy(record)
        declared_record_hash = record_for_hash.pop("record_sha256", None)
        if value_sha256(record_for_hash) != declared_record_hash:
            raise PilotError(f"calibration row SHA-256 mismatch: {key[1]}")
        augmented = copy.deepcopy(record)
        augmented.update(
            {
                "calibration_container_all_grade_a": True,
                "calibration_container_all_previously_exposed": True,
                "exposure_categories": ["calibration"],
                "preliminary_within_family_held_out_eligibility": False,
                "effective_exposure_status": "directly_exposed",
                "conversation_exposure_status": "directly_exposed",
                "target_exposure_status": "directly_exposed",
            }
        )
        prepared_records.append(augmented)
    return prepared_records, {
        "artifact_evidence": "frozen input",
        "frozen_source_manifest_sha256": EXPECTED_SOURCE_MANIFEST_SHA256,
        "prospective_batch_manifest_sha256": EXPECTED_PROSPECTIVE_MANIFEST_SHA256,
        "calibration_manifest_sha256": sha256_bytes(
            _read_bytes(CALIBRATION_MANIFEST_PATH, "calibration manifest")
        ),
        "calibration_index_semantic_sha256": value_sha256(index),
        "calibration_index_file_sha256": sha256_bytes(index_raw),
        "calibration_records_sha256": sha256_bytes(records_raw),
        "cutoff": FROZEN_CUTOFF,
        "authorised_text_container": "exposed Grade-A calibration pack",
        "unexposed_or_held_out_source_files_opened": 0,
    }


_ORIGINAL_FROZEN_SOURCE_LOADER = _validate_frozen_calibration_sources


def _prepare_selection_v2(
    metadata_output_dir: Path | None = None,
) -> tuple[
    list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], dict[str, Any]
]:
    """Validate public manifests, scan metadata, select, then load transcripts."""

    source_raw = _read_bytes(FROZEN_SOURCE_MANIFEST_PATH, "frozen source manifest")
    prospective_raw = _read_bytes(
        PROSPECTIVE_BATCH_MANIFEST_PATH, "frozen prospective batch manifest"
    )
    if sha256_bytes(source_raw) != EXPECTED_SOURCE_MANIFEST_SHA256:
        raise PilotError("frozen source-manifest SHA-256 mismatch")
    if sha256_bytes(prospective_raw) != EXPECTED_PROSPECTIVE_MANIFEST_SHA256:
        raise PilotError("frozen prospective batch-manifest SHA-256 mismatch")
    source_manifest = live_probe.strict_json_loads(source_raw)
    if source_manifest.get("cutoff") != FROZEN_CUTOFF:
        raise PilotError("frozen source cutoff mismatch")
    calibration_manifest_raw = _read_bytes(
        CALIBRATION_MANIFEST_PATH, "calibration manifest"
    )
    calibration_manifest = live_probe.strict_json_loads(calibration_manifest_raw)
    if calibration_manifest.get("all_cases_grade_a") is not True or calibration_manifest.get(
        "all_cases_previously_exposed"
    ) is not True:
        raise PilotError("calibration pack is not frozen exposed Grade A")
    index_raw = _read_bytes(CALIBRATION_INDEX_PATH, "calibration index")
    calibration_index = _load_json_array(CALIBRATION_INDEX_PATH, "calibration index")
    if value_sha256(calibration_index) != calibration_manifest.get("index_sha256"):
        raise PilotError("calibration index semantic SHA-256 mismatch")
    frozen = {
        (str(group["conversation_key"]), str(target["calibration_case_id"])): target
        for group in source_manifest.get("calibration_selection", [])
        for target in group.get("targets", [])
    }
    indexed = {
        (str(item["conversation_key"]), str(item["calibration_case_id"])): item
        for item in calibration_index
    }
    if not frozen or set(frozen) != set(indexed):
        raise PilotError("calibration index differs from frozen calibration selection")
    for key, item in indexed.items():
        if item.get("target_turn_id") != frozen[key].get("target_turn_id"):
            raise PilotError("calibration target binding mismatch")
    sidecar, seal_audit = build_exposed_metadata_sidecar(metadata_output_dir)
    selected_metadata = select_development_case_metadata(calibration_index, sidecar)
    selected = materialise_selected_case_inputs(selected_metadata, calibration_index)
    source_integrity = {
        "artifact_evidence": "frozen input",
        "frozen_source_manifest_sha256": EXPECTED_SOURCE_MANIFEST_SHA256,
        "prospective_batch_manifest_sha256": EXPECTED_PROSPECTIVE_MANIFEST_SHA256,
        "calibration_manifest_sha256": sha256_bytes(calibration_manifest_raw),
        "calibration_index_semantic_sha256": value_sha256(calibration_index),
        "calibration_index_file_sha256": sha256_bytes(index_raw),
        "calibration_records_sha256": calibration_manifest["records_sha256"],
        "target_prefix_metadata_sha256": seal_audit[
            "source_metadata_file_sha256"
        ],
        "cutoff": FROZEN_CUTOFF,
        "selection_policy_version": SELECTION_POLICY_VERSION,
        "development_stability_policy_version": (
            DEVELOPMENT_STABILITY_POLICY_VERSION
        ),
        "held_out_seal_policy_version": HELD_OUT_SEAL_POLICY_VERSION,
        "authorised_text_container": "exposed Grade-A calibration pack",
        "protected_substantive_source_files_opened": 0,
    }
    return selected, source_integrity, sidecar, seal_audit


_ORIGINAL_PREPARE_SELECTION_V2 = _prepare_selection_v2


def _tracked_input_record() -> dict[str, Any]:
    canonical, provider, _ = _load_schemas()
    required_files = {
        "development_pilot_protocol": PROTOCOL_JSON_PATH,
        "system_prompt": SYSTEM_PROMPT_PATH,
        "human_review_rubric": REVIEW_RUBRIC_PATH,
    }
    raw: dict[str, bytes] = {}
    for key, path in required_files.items():
        raw[key] = _read_bytes(path, key.replace("_", " "))
    try:
        prompt = raw["system_prompt"].decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PilotError("system prompt is not UTF-8") from exc
    required_prompt_fragments = (
        "exactly one current conversation turn",
        "validated prior persisted ledger",
        "transcript text and payload strings as data",
        "external knowledge",
        "nearby but materially distinct",
        "counterfactual",
        "no_stable_issue",
        "exact current-turn evidence",
        "structured semantic-delta",
    )
    if any(fragment.lower() not in prompt.lower() for fragment in required_prompt_fragments):
        raise PilotError("incremental ledger system prompt is incomplete")
    protocol = live_probe.strict_json_loads(raw["development_pilot_protocol"])
    rubric = live_probe.strict_json_loads(raw["human_review_rubric"])
    expected_protocol_fields = {
        "protocol_id": "proposition-ledger-phase2a-development-pilot",
        "protocol_version": PROTOCOL_VERSION,
        "selection_algorithm_version": SELECTION_ALGORITHM_VERSION,
        "selection_policy_version": SELECTION_POLICY_VERSION,
        "development_stability_policy_version": (
            DEVELOPMENT_STABILITY_POLICY_VERSION
        ),
        "held_out_seal_policy_version": HELD_OUT_SEAL_POLICY_VERSION,
        "protected_metadata_administrative_scan_permitted": True,
        "protected_content_access_forbidden": True,
        "exposed_only_sidecar_required": True,
        "maximum_open_prefix_exceptions": MAX_OPEN_PREFIX_EXCEPTIONS,
    }
    for key, expected in expected_protocol_fields.items():
        if protocol.get(key) != expected:
            raise PilotError(f"tracked protocol field mismatch: {key}")
    if protocol.get("provider_profiles") != list(PROFILES):
        raise PilotError("tracked protocol provider profiles mismatch")
    provider_request = protocol.get("provider_request")
    if not isinstance(provider_request, Mapping) or any(
        provider_request.get(key) != expected
        for key, expected in {
            "request_contract_revision": REQUEST_CONTRACT_REVISION,
            "maximum_visible_output_tokens": MAX_OUTPUT_TOKENS,
            "client_timeout_seconds": int(CLIENT_TIMEOUT_SECONDS),
            "application_retries": 0,
            "sdk_grpc_retries": False,
            "tools": [],
            "tool_choice": "omitted",
            "store_messages": False,
            "streaming": False,
        }.items()
    ):
        raise PilotError("tracked protocol provider request mismatch")
    return {
        "protocol": protocol,
        "system_prompt": prompt,
        "rubric": rubric,
        "canonical_schema": canonical,
        "provider_schema": provider,
        "hashes": {
            "canonical_schema_sha256": EXPECTED_CANONICAL_SCHEMA_SHA256,
            "provider_schema_sha256": value_sha256(provider),
            "system_prompt_sha256": sha256_bytes(raw["system_prompt"]),
            "review_rubric_sha256": sha256_bytes(raw["human_review_rubric"]),
            "protocol_sha256": sha256_bytes(raw["development_pilot_protocol"]),
        },
    }


def _git_output(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=PROJECT_DIR,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise PilotError(f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def _source_commit_for_freeze() -> str:
    # Protocol source means the approved Phase 1.4 base, not the later freeze commit.
    merge_base = _git_output("merge-base", "HEAD", SOURCE_COMMIT)
    if merge_base != SOURCE_COMMIT:
        raise PilotError("worktree is not descended from the approved source commit")
    return SOURCE_COMMIT


def _protocol_seed_hash(
    manifest_sha256: str, tracked: Mapping[str, Any]
) -> str:
    return value_sha256(
        {
            "case_manifest_sha256": manifest_sha256,
            "protocol_sha256": tracked["hashes"]["protocol_sha256"],
            "system_prompt_sha256": tracked["hashes"]["system_prompt_sha256"],
            "selection_algorithm_version": SELECTION_ALGORITHM_VERSION,
            "label": "phase2a-model-order-and-review-blinding",
        }
    )


def build_protocol_freeze(
    manifest_sha256: str,
    selected_turn_count: int,
    tracked: Mapping[str, Any],
    *,
    exposed_sidecar_sha256: str | None = None,
    held_out_seal_audit_sha256: str | None = None,
    target_prefix_metadata_sha256: str | None = None,
    selected_open_prefix_exception_count: int = 0,
) -> dict[str, Any]:
    """Build the immutable private protocol-freeze record."""

    planned = selected_turn_count * len(PROFILES)
    if not 48 <= planned <= MAX_PROVIDER_CALL_BUDGET:
        raise PilotError("planned provider-call count is outside 48-64")
    seed_hash = _protocol_seed_hash(manifest_sha256, tracked)
    return {
        "artifact_evidence": "frozen input",
        "protocol_freeze_version": "phase2a-protocol-freeze-v1",
        "protocol_version": PROTOCOL_VERSION,
        "source_commit": _source_commit_for_freeze(),
        "canonical_schema_sha256": EXPECTED_CANONICAL_SCHEMA_SHA256,
        "provider_schema_sha256": EXPECTED_PROVIDER_SCHEMA_SHA256,
        "system_prompt_sha256": tracked["hashes"]["system_prompt_sha256"],
        "review_rubric_sha256": tracked["hashes"]["review_rubric_sha256"],
        "development_protocol_sha256": tracked["hashes"]["protocol_sha256"],
        "provider_profiles": copy.deepcopy(list(PROFILES)),
        "sdk_environment": {
            "python": "CPython 3.10.12",
            "xai-sdk": "1.19.0",
            "pydantic": "2.13.5",
            "protobuf": "6.33.6",
            "grpcio": "1.83.1",
            "jsonschema": "4.26.0",
        },
        "request_envelope_revision": REQUEST_CONTRACT_REVISION,
        "selection_algorithm_version": SELECTION_ALGORITHM_VERSION,
        "selection_policy_version": SELECTION_POLICY_VERSION,
        "development_stability_policy_version": (
            DEVELOPMENT_STABILITY_POLICY_VERSION
        ),
        "held_out_seal_policy_version": HELD_OUT_SEAL_POLICY_VERSION,
        "protected_administrative_metadata_scan_permitted": True,
        "protected_metadata_administrative_scan_permitted": True,
        "protected_substantive_content_forbidden": True,
        "protected_content_access_forbidden": True,
        "exposed_development_sidecar_required": True,
        "exposed_only_sidecar_required": True,
        "exposed_development_sidecar_sha256": exposed_sidecar_sha256,
        "held_out_content_seal_audit_sha256": held_out_seal_audit_sha256,
        "target_prefix_metadata_sha256": target_prefix_metadata_sha256,
        "maximum_open_prefix_exception_count": MAX_OPEN_PREFIX_EXCEPTIONS,
        "maximum_open_prefix_exceptions": MAX_OPEN_PREFIX_EXCEPTIONS,
        "selected_open_prefix_exception_count": selected_open_prefix_exception_count,
        "private_case_manifest_sha256": manifest_sha256,
        "selected_conversation_count": SELECTED_CONVERSATION_COUNT,
        "selected_unique_turn_count": selected_turn_count,
        "exact_planned_provider_call_count": planned,
        "maximum_provider_call_budget": MAX_PROVIDER_CALL_BUDGET,
        "model_order_randomisation_seed_hash": seed_hash,
        "model_order_counterbalancing_rule": (
            "sha256-parity-bit-of-protocol-hash-and-pilot-conversation-key"
        ),
        "maximum_visible_output_tokens": MAX_OUTPUT_TOKENS,
        "client_timeout_seconds": CLIENT_TIMEOUT_SECONDS,
        "retry_policy": {
            "application_retries": 0,
            "sdk_grpc_retries": 0,
            "fallback_calls": 0,
            "repair_calls": 0,
        },
        "validation_sequence": [
            "strict_json",
            "provider_facing_schema",
            "intended_canonical",
            "binding",
            "evidence",
            "semantic_reference",
            "deterministic_materialisation",
            "persisted_ledger",
        ],
        "diagnostic_definitions": [item[0] for item in STRESSORS],
        "declared_non_goals": [
            "held-out evaluation",
            "downstream four-arm experiment",
            "ledger effectiveness claim",
            "model profile selection",
            "production integration",
            "prompt revision",
        ],
    }


def wrap_tracked_protocol_freeze(
    execution_core: Mapping[str, Any],
    call_plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind the non-circular execution core to the exact immutable call plan."""

    core = copy.deepcopy(dict(execution_core))
    wrapper = copy.deepcopy(core)
    wrapper.update(
        {
            "tracked_wrapper_version": "phase2a-protocol-freeze-wrapper-v2",
            "execution_freeze_core_sha256": value_sha256(core),
            "call_plan_sha256": sha256_bytes(pretty_json_bytes(call_plan)),
        }
    )
    return wrapper


def _execution_freeze_core(wrapper: Mapping[str, Any]) -> dict[str, Any]:
    """Recover and verify the execution identity beneath the tracked wrapper."""

    core = copy.deepcopy(dict(wrapper))
    declared = core.pop("execution_freeze_core_sha256", None)
    core.pop("tracked_wrapper_version", None)
    core.pop("call_plan_sha256", None)
    if declared != value_sha256(core):
        raise PilotError("execution protocol-freeze core SHA-256 mismatch")
    return core


def tracked_protocol_freeze_from_private(output: str | Path) -> dict[str, Any]:
    """Return the exact safe tracked freeze wrapper generated by prepare mode."""

    output_dir = _private_directory(output)
    wrapper = _load_json(output_dir / "protocol-freeze.json", "private protocol freeze")
    _execution_freeze_core(wrapper)
    return wrapper


def _profile_order(protocol_hash: str, conversation_id: str) -> tuple[dict[str, Any], ...]:
    bit = int(
        sha256_bytes(f"{protocol_hash}:{conversation_id}".encode("utf-8"))[-1], 16
    ) & 1
    return PROFILES if bit == 0 else tuple(reversed(PROFILES))


def _prior_binding_marker(profile_id: str, turn_index: int) -> str:
    return "genesis" if turn_index == 0 else f"prior-output:{profile_id}:turn-{turn_index - 1}"


def build_call_plan(
    cases: Sequence[Mapping[str, Any]],
    freeze: Mapping[str, Any],
) -> dict[str, Any]:
    """Build every planned conversation-turn-profile call entry."""

    freeze_hash = value_sha256(freeze)
    entries: list[dict[str, Any]] = []
    sequence = 0
    for case in cases:
        conversation_id = str(case["pilot_conversation_id"])
        for profile in _profile_order(freeze_hash, conversation_id):
            for turn in case["exact_private_transcript_prefix"]:
                sequence += 1
                planned_payload_material = {
                    "protocol_version": PROTOCOL_VERSION,
                    "protocol_freeze_sha256": freeze_hash,
                    "pilot_conversation_key": conversation_id,
                    "current_turn_id": turn["turn_id"],
                    "turn_index": turn["turn_index"],
                    "parent_turn_id": turn["parent_turn_id"],
                    "current_visible_text_sha256": sha256_bytes(
                        turn["text"].encode("utf-8")
                    ),
                    "current_speaker": turn["speaker"],
                    "prior_ledger_binding": _prior_binding_marker(
                        profile["profile_id"], turn["turn_index"]
                    ),
                    "provider_schema_sha256": EXPECTED_PROVIDER_SCHEMA_SHA256,
                }
                planned_payload_hash = value_sha256(planned_payload_material)
                identity_material = {
                    "protocol_freeze_sha256": freeze_hash,
                    "case_manifest_sha256": freeze["private_case_manifest_sha256"],
                    "pilot_conversation_id": conversation_id,
                    "pilot_turn_id": turn["turn_id"],
                    "profile_id": profile["profile_id"],
                    "current_turn_payload_hash": planned_payload_hash,
                    "prior_ledger_hash_or_genesis_marker": planned_payload_material[
                        "prior_ledger_binding"
                    ],
                    "provider_schema_sha256": EXPECTED_PROVIDER_SCHEMA_SHA256,
                    "system_prompt_sha256": freeze["system_prompt_sha256"],
                }
                entries.append(
                    {
                        "sequence": sequence,
                        "development_case_id": case["development_case_id"],
                        "pilot_conversation_id": conversation_id,
                        "pilot_turn_id": turn["turn_id"],
                        "turn_index": turn["turn_index"],
                        "profile_id": profile["profile_id"],
                        "model": profile["model"],
                        "planned_payload_sha256": planned_payload_hash,
                        "planned_call_identity": value_sha256(identity_material),
                        "call_identity": None,
                        "actual_payload_sha256": None,
                        "prior_ledger_sha256": (
                            None if turn["turn_index"] else "genesis"
                        ),
                        "state": "planned",
                        "attempt_number": 0,
                        "provider_call_count": 0,
                        "started_at_utc": None,
                        "completed_at_utc": None,
                        "state_history": [
                            {"state": "planned", "at_utc": None, "reason": "frozen_plan"}
                        ],
                    }
                )
    planned_count = sum(len(case["exact_private_transcript_prefix"]) for case in cases) * 2
    if len(entries) != planned_count or planned_count != freeze[
        "exact_planned_provider_call_count"
    ]:
        raise PilotError("call-plan count does not reconcile")
    return {
        "artifact_evidence": "frozen input",
        "call_ledger_version": "phase2a-durable-call-ledger-v1",
        "protocol_freeze_sha256": freeze_hash,
        "case_manifest_sha256": freeze["private_case_manifest_sha256"],
        "planned_provider_call_count": planned_count,
        "maximum_provider_call_budget": MAX_PROVIDER_CALL_BUDGET,
        "provider_call_count": 0,
        "automatic_retry_count": 0,
        "fallback_call_count": 0,
        "repair_call_count": 0,
        "entries": entries,
    }


def _build_case_manifest(cases: Sequence[Mapping[str, Any]], source_integrity: Mapping[str, Any]) -> dict[str, Any]:
    unique_turns = sum(int(case["unique_turn_count"]) for case in cases)
    manifest: dict[str, Any] = {
        "artifact_evidence": "frozen input",
        "manifest_version": "phase2a-development-case-manifest-v1",
        "frozen_source_hashes": {
            "source_manifest_sha256": source_integrity[
                "frozen_source_manifest_sha256"
            ],
            "prospective_batch_manifest_sha256": source_integrity[
                "prospective_batch_manifest_sha256"
            ],
            "calibration_manifest_sha256": source_integrity[
                "calibration_manifest_sha256"
            ],
            "calibration_index_semantic_sha256": source_integrity[
                "calibration_index_semantic_sha256"
            ],
            "calibration_index_file_sha256": source_integrity[
                "calibration_index_file_sha256"
            ],
            "calibration_records_sha256": source_integrity[
                "calibration_records_sha256"
            ],
        },
        "selection_algorithm_version": SELECTION_ALGORITHM_VERSION,
        "selection_policy_version": SELECTION_POLICY_VERSION,
        "development_stability_policy_version": (
            DEVELOPMENT_STABILITY_POLICY_VERSION
        ),
        "held_out_seal_policy_version": HELD_OUT_SEAL_POLICY_VERSION,
        "target_prefix_metadata_sha256": source_integrity.get(
            "target_prefix_metadata_sha256"
        ),
        "selected_conversation_count": len(cases),
        "selected_unique_turn_count": unique_turns,
        "development_case_ids": [case["development_case_id"] for case in cases],
        "cases": copy.deepcopy(list(cases)),
    }
    manifest["manifest_content_sha256"] = value_sha256(manifest)
    return manifest


def prepare_run(output: str | Path) -> dict[str, Any]:
    """Prepare and freeze the private development corpus without provider calls."""

    if not os.environ.get("XAI_API_KEY"):
        return {
            "status": "phase2a_not_run_missing_xai_api_key",
            "provider_calls": 0,
        }
    tracked = _tracked_input_record()
    output_dir = _private_directory(output, create=True)
    if _validate_frozen_calibration_sources is not _ORIGINAL_FROZEN_SOURCE_LOADER:
        # Synthetic tests may inject wholly synthetic exposed records without
        # weakening the real metadata-first v2 path.
        injected_records, source_integrity = _validate_frozen_calibration_sources()
        selected = select_development_cases(injected_records)
        exposed_sidecar = []
        seal_audit = {
            "artifact_evidence": "deterministic derivation",
            "policy_version": HELD_OUT_SEAL_POLICY_VERSION,
            "source_metadata_rows_scanned": 0,
            "protected_metadata_rows_scanned_for_administration": 0,
            "protected_metadata_rows_rendered_to_operator": 0,
            "protected_rows_written_to_development_sidecar": 0,
            "protected_transcript_records_opened": 0,
            "protected_transcript_text_bytes_read": 0,
            "protected_source_mapping_records_opened": 0,
            "protected_provider_payload_count": 0,
            "protected_model_output_count": 0,
            "protected_human_review_count": 0,
            "directly_exposed_rows_written_to_sidecar": len(injected_records),
            "structurally_mined_only_rows_excluded": 0,
            "genuinely_unexposed_rows_excluded": 0,
            "preliminarily_held_out_rows_excluded": 0,
            "substantive_seal_breached": False,
            "prior_protected_metadata_administrative_scan": True,
            "prior_scan_report": (
                "mrsMThatcher-proposition-ledger-phase2a-early-stop-report-"
                "20260901T094004Z.md"
            ),
            "prior_protected_metadata_rows_known_minimum": 2,
            "prior_genuinely_unexposed_metadata_row_count": "unknown",
            "prior_protected_transcript_text_bytes_read": 0,
            "prior_protected_provider_payload_count": 0,
            "prior_protected_model_output_count": 0,
            "prior_substantive_seal_breached": False,
        }
    else:
        if _prepare_selection_v2 is _ORIGINAL_PREPARE_SELECTION_V2:
            selected, source_integrity, exposed_sidecar, seal_audit = (
                _prepare_selection_v2(output_dir)
            )
        else:
            # Synthetic test injection remains argument-free and provider-free.
            selected, source_integrity, exposed_sidecar, seal_audit = (
                _prepare_selection_v2()
            )
    cases, source_map = _localise_cases(selected)
    case_manifest = _build_case_manifest(cases, source_integrity)
    case_manifest_bytes = pretty_json_bytes(case_manifest)
    case_manifest_sha = sha256_bytes(case_manifest_bytes)
    unique_turns = case_manifest["selected_unique_turn_count"]
    if len(cases) != SELECTED_CONVERSATION_COUNT:
        raise PilotError("selected conversation count is not eight")
    if not MIN_UNIQUE_TURNS <= unique_turns <= MAX_UNIQUE_TURNS:
        raise PilotError("selected unique-turn count is outside 24-32")
    sidecar_content = b"".join(
        canonical_json_bytes(item) + b"\n" for item in exposed_sidecar
    )
    seal_audit_bytes = pretty_json_bytes(seal_audit)
    execution_freeze = build_protocol_freeze(
        case_manifest_sha,
        unique_turns,
        tracked,
        exposed_sidecar_sha256=sha256_bytes(sidecar_content),
        held_out_seal_audit_sha256=sha256_bytes(seal_audit_bytes),
        target_prefix_metadata_sha256=source_integrity.get(
            "target_prefix_metadata_sha256"
        ),
        selected_open_prefix_exception_count=sum(
            bool(case.get("frozen_open_prefix_exception")) for case in cases
        ),
    )
    plan = build_call_plan(cases, execution_freeze)
    freeze = wrap_tracked_protocol_freeze(execution_freeze, plan)

    conversations_dir = _ensure_private_subdir(output_dir / "conversations")
    for case in cases:
        case_dir = _ensure_private_subdir(
            conversations_dir / str(case["pilot_conversation_id"])
        )
        _write_json(
            case_dir / "transcript.json",
            {
                "artifact_evidence": "frozen input",
                "development_case_id": case["development_case_id"],
                "pilot_conversation_id": case["pilot_conversation_id"],
                "reconstruction_grade": "A",
                "turns": case["exact_private_transcript_prefix"],
                "historical_reply_after_target_withheld": True,
                "production_outcome_withheld": True,
            },
        )
        for profile in PROFILES:
            profile_dir = _ensure_private_subdir(case_dir / profile["model"])
            for turn in case["exact_private_transcript_prefix"]:
                _ensure_private_subdir(profile_dir / f"turn-{int(turn['turn_index']):03d}")

    _write_json(
        output_dir / "run-manifest.json",
        {
            "artifact_evidence": "frozen input",
            "run_version": PROTOCOL_VERSION,
            "status": "prepared_no_provider_calls",
            "created_at_utc": utc_now(),
            "source_commit": SOURCE_COMMIT,
            "provider_calls": 0,
            "private_case_manifest_sha256": case_manifest_sha,
            "execution_freeze_core_sha256": freeze[
                "execution_freeze_core_sha256"
            ],
            "tracked_protocol_freeze_sha256": value_sha256(freeze),
        },
    )
    _write_json(output_dir / "protocol-freeze.json", freeze)
    _write_json(output_dir / "source-integrity.json", source_integrity)
    _atomic_write(output_dir / "exposed-development-candidate-index.jsonl", sidecar_content)
    _atomic_write(output_dir / "held-out-content-seal-audit.json", seal_audit_bytes)
    _write_json(
        output_dir / "exposure-exclusion-audit.json",
        {
            "artifact_evidence": "deterministic derivation",
            "selection_boundary": "exposed administrative sidecar before transcript dereference",
            "held_out_seal_policy_version": HELD_OUT_SEAL_POLICY_VERSION,
            "protected_administrative_metadata_rows_scanned": seal_audit[
                "protected_metadata_rows_scanned_for_administration"
            ],
            "protected_substantive_rows_read": 0,
            "genuinely_unexposed_transcript_rows_read": 0,
            "structurally_mined_only_cases_selected": 0,
            "protected_transcript_text_bytes_read": 0,
            "selected_directly_exposed_grade_a_cases": len(cases),
            "selected_open_prefix_exception_count": sum(
                case.get("stability_status") == "open_at_frozen_cutoff" for case in cases
            ),
            "sealed_clean_prefix_aggregate_count": 2,
            "sealed_clean_prefixes_remained_unopened": True,
            "substantive_seal_breached": False,
        },
    )
    _atomic_write(output_dir / "development-case-manifest.json", case_manifest_bytes)
    _write_json(output_dir / "private-source-id-map.json", source_map)
    _write_json(
        output_dir / "prompt-manifest.json",
        {
            "artifact_evidence": "frozen input",
            **tracked["hashes"],
            "request_envelope_revision": REQUEST_CONTRACT_REVISION,
            "maximum_visible_output_tokens": MAX_OUTPUT_TOKENS,
            "client_timeout_seconds": CLIENT_TIMEOUT_SECONDS,
            "tools": [],
            "tool_choice_parameter_sent": False,
            "store_messages": False,
            "streaming": False,
            "search": False,
            "code_execution": False,
            "application_retries": 0,
            "sdk_grpc_retries": 0,
        },
    )
    _write_json(output_dir / "provider-schema.json", tracked["provider_schema"])
    _write_json(output_dir / "call-plan.json", plan)
    _write_json(output_dir / "call-ledger.json", copy.deepcopy(plan))
    _write_json(
        output_dir / "phase1.4-carry-forward-diagnostic.json",
        {
            "artifact_evidence": "frozen input",
            "diagnostic_id": "phase1_4_grok_4_3_carry_forward_diagnostic",
            "status": "not_inspected_until_protocol_freeze_commit_is_verified",
            "exact_failed_invariant": None,
            "recurrence_result": "phase1_4_failure_pattern_not_assessable",
        },
    )
    return {
        "status": "phase2a_prepared_no_provider_calls",
        "private_output": str(output_dir),
        "selected_conversations": len(cases),
        "selected_unique_turns": unique_turns,
        "selected_contributor_groups": len(
            {case["contributor_group_pseudonym"] for case in cases}
        ),
        "planned_provider_calls": plan["planned_provider_call_count"],
        "provider_calls": 0,
        "private_case_manifest_sha256": case_manifest_sha,
        "execution_freeze_core_sha256": freeze[
            "execution_freeze_core_sha256"
        ],
        "tracked_protocol_freeze_sha256": value_sha256(freeze),
        "tracked_protocol_freeze": freeze,
    }


def build_turn_payload(
    case: Mapping[str, Any],
    turn: Mapping[str, Any],
    freeze_or_prior_ledger: Mapping[str, Any] | None,
    prior_ledger: Mapping[str, Any] | None | object = Ellipsis,
    *,
    protocol_freeze_sha256: str | None = None,
    provider_schema: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the model-neutral current-turn payload with no future information."""

    if prior_ledger is Ellipsis:
        effective_prior = freeze_or_prior_ledger
    else:
        freeze = freeze_or_prior_ledger
        effective_prior = prior_ledger
        if protocol_freeze_sha256 is None:
            protocol_freeze_sha256 = value_sha256(freeze)
    if protocol_freeze_sha256 is None:
        raise PilotError("protocol-freeze SHA-256 is required for turn payload")
    if provider_schema is None:
        _, provider_schema, _ = _load_schemas()

    return {
        "protocol_version": PROTOCOL_VERSION,
        "protocol_freeze_sha256": protocol_freeze_sha256,
        "pilot_local_conversation_key": case["pilot_conversation_id"],
        "pilot_local_current_turn_id": turn["turn_id"],
        "turn_index": turn["turn_index"],
        "pilot_local_parent_turn_id": turn["parent_turn_id"],
        "exact_current_visible_text": turn["text"],
        "trusted_current_speaker_participant_descriptor": copy.deepcopy(
            turn["speaker"]
        ),
        "validated_prior_persisted_ledger": copy.deepcopy(effective_prior),
        "provider_response_schema": copy.deepcopy(provider_schema),
    }


def build_request_representation(
    profile: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    system_prompt: str,
    provider_schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the exact credential-free request contract used by the SDK."""

    return {
        "model": profile["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": canonical_json_bytes(payload).decode("utf-8"),
            },
        ],
        "max_tokens": MAX_OUTPUT_TOKENS,
        "reasoning_effort": "low",
        "tools": [],
        "parallel_tool_calls": False,
        "response_format": {
            "format_type": "json_schema",
            "schema": copy.deepcopy(provider_schema),
        },
        "search_parameters": None,
        "store_messages": STORE_MESSAGES,
        "streaming": False,
        "code_execution": False,
        "sampling_parameters_set": [],
        "fallback_model": None,
        "application_retry_count": APPLICATION_RETRIES,
        "sdk_grpc_retries": False,
        "client_timeout_seconds": CLIENT_TIMEOUT_SECONDS,
        "request_contract_revision": REQUEST_CONTRACT_REVISION,
        "tool_choice_parameter_sent": False,
    }


def request_representation(
    profile: Mapping[str, Any],
    payload: Mapping[str, Any],
    tracked: Mapping[str, Any],
) -> dict[str, Any]:
    """Compatibility wrapper for synthetic tests and local request inspection."""

    system_prompt = tracked.get("system_prompt")
    provider_schema = tracked.get("provider_schema")
    if not isinstance(system_prompt, str) or not isinstance(provider_schema, Mapping):
        raise PilotError("tracked request inputs are incomplete")
    return build_request_representation(
        profile,
        payload,
        system_prompt=system_prompt,
        provider_schema=provider_schema,
    )


def _request_metadata_record(
    *,
    entry: Mapping[str, Any],
    case: Mapping[str, Any],
    turn: Mapping[str, Any],
    profile: Mapping[str, Any],
    call_identity: str,
    payload_hash: str,
    prior_hash: str,
    request_repr: Mapping[str, Any],
    system_prompt_sha256: str,
) -> dict[str, Any]:
    """Build the exact deterministic metadata saved beside one request."""

    return {
        "artifact_evidence": "deterministic derivation",
        "call_identity": call_identity,
        "planned_call_identity": entry["planned_call_identity"],
        "profile_id": profile["profile_id"],
        "model": profile["model"],
        "pilot_conversation_id": case["pilot_conversation_id"],
        "pilot_turn_id": turn["turn_id"],
        "turn_index": turn["turn_index"],
        "current_turn_payload_sha256": payload_hash,
        "prior_ledger_sha256": prior_hash,
        "request_representation": {
            key: value
            for key, value in request_repr.items()
            if key not in {"messages", "response_format"}
        },
        "provider_schema_sha256": EXPECTED_PROVIDER_SCHEMA_SHA256,
        "system_prompt_sha256": system_prompt_sha256,
        "attempt_number": 1,
    }


def _materialisation_turn(
    case: Mapping[str, Any], turn: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "conversation_key": case["pilot_conversation_id"],
        "turn_id": turn["turn_id"],
        "turn_index": turn["turn_index"],
        "parent_turn_id": turn["parent_turn_id"],
        "post_id": turn["turn_id"],
        "speaker_id": turn["speaker"]["participant_id"],
        "text": turn["text"],
    }


def _validation_record(raw: bytes) -> dict[str, Any]:
    stages = (
        "strict_json",
        "provider_schema",
        "intended_canonical",
        "binding",
        "evidence_span",
        "semantic_reference",
        "materialisation",
        "persisted_ledger",
    )
    result: dict[str, Any] = {
        "artifact_evidence": "deterministic derivation",
        "raw_response_sha256": sha256_bytes(raw),
        "validation_sequence": list(stages),
        "provider_controls_persistence": False,
        "ordinary_python_jsonschema_is_authority": False,
        "ordinary_python_jsonschema_status": "not_run",
        "ordinary_python_jsonschema_errors": [],
    }
    for stage in stages:
        result[f"{stage}_status"] = "not_run"
        result[f"{stage}_errors"] = []
    return result


def _bounded(errors: Iterable[Any]) -> list[str]:
    return [str(error).replace("\n", " ")[:ERROR_LIMIT] for error in list(errors)[:32]]


def _provider_persistence_errors(value: Any, location: str = "$") -> list[str]:
    errors: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if key in FORBIDDEN_PROVIDER_PERSISTENCE_FIELDS and not (
                child_location == "$.prior_ledger_reference.ledger_id"
            ):
                errors.append(f"provider_persistence_field:{child_location}")
            errors.extend(_provider_persistence_errors(child, child_location))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(_provider_persistence_errors(child, f"{location}[{index}]"))
    return errors


def validate_response_bindings(
    parsed: Mapping[str, Any],
    case: Mapping[str, Any],
    turn: Mapping[str, Any],
    prior_ledger: Mapping[str, Any] | None,
) -> list[str]:
    """Validate conversation, turn, index, predecessor, and persistence bindings."""

    expected_prior = (
        None
        if prior_ledger is None
        else {
            "ledger_id": prior_ledger.get("ledger_id"),
            "as_of_turn_index": prior_ledger.get("as_of_turn_index"),
        }
    )
    expected = {
        "conversation_key": case["pilot_conversation_id"],
        "target_turn_id": turn["turn_id"],
        "as_of_turn_index": turn["turn_index"],
        "prior_ledger_reference": expected_prior,
    }
    errors = [
        f"{field}_binding_mismatch"
        for field, value in expected.items()
        if parsed.get(field) != value
    ]
    errors.extend(_provider_persistence_errors(parsed))
    return _bounded(errors)


def validate_current_turn_evidence(
    parsed: Mapping[str, Any], turn: Mapping[str, Any]
) -> list[str]:
    """Require every evidence span to be an exact current-turn Unicode slice."""

    text = str(turn["text"])
    current_turn_id = str(turn["turn_id"])
    errors: list[str] = []
    for location, span in live_probe._iter_evidence_spans(parsed):
        if span.get("turn_id") != current_turn_id:
            errors.append(f"non_current_evidence_turn:{location}")
            continue
        start, end = span.get("start_char"), span.get("end_char")
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
    return _bounded(errors)


def process_response_bytes(
    raw: bytes,
    *,
    case: Mapping[str, Any],
    turn: Mapping[str, Any],
    prior_ledger: Mapping[str, Any] | None,
    canonical_schema: Mapping[str, Any],
    provider_schema: Mapping[str, Any],
    persisted_ledger_schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply the frozen eight-stage validator and incremental materialiser."""

    validation = _validation_record(raw)
    try:
        parsed = live_probe.strict_json_loads(raw)
    except live_probe.StrictJSONError as exc:
        validation["strict_json_status"] = "failed"
        validation["strict_json_errors"] = _bounded([exc])
        return {"parsed": None, "ledger": None, "validation": validation}
    validation["strict_json_status"] = "passed"

    provider_errors = preflight.intended_validation_errors(
        provider_schema, parsed, pattern_mode="xai_full_string"
    )
    validation["provider_schema_errors"] = _bounded(provider_errors)
    validation["provider_schema_status"] = "passed" if not provider_errors else "failed"
    if provider_errors:
        return {"parsed": parsed, "ledger": None, "validation": validation}

    canonical_errors = preflight.intended_validation_errors(
        canonical_schema, parsed, pattern_mode="canonical_outer_anchors"
    )
    ordinary_errors = preflight.validation_errors(canonical_schema, parsed)
    validation["intended_canonical_errors"] = _bounded(canonical_errors)
    validation["intended_canonical_status"] = (
        "passed" if not canonical_errors else "failed"
    )
    validation["ordinary_python_jsonschema_errors"] = _bounded(ordinary_errors)
    validation["ordinary_python_jsonschema_status"] = (
        "passed" if not ordinary_errors else "failed"
    )
    if canonical_errors:
        return {"parsed": parsed, "ledger": None, "validation": validation}

    binding_errors = validate_response_bindings(parsed, case, turn, prior_ledger)
    validation["binding_errors"] = binding_errors
    validation["binding_status"] = "passed" if not binding_errors else "failed"
    if binding_errors:
        return {"parsed": parsed, "ledger": None, "validation": validation}

    evidence_errors = validate_current_turn_evidence(parsed, turn)
    validation["evidence_span_errors"] = evidence_errors
    validation["evidence_span_status"] = "passed" if not evidence_errors else "failed"
    if evidence_errors:
        return {"parsed": parsed, "ledger": None, "validation": validation}

    current_turn = _materialisation_turn(case, turn)
    genesis_context = None
    if prior_ledger is None:
        genesis_context = {
            "conversation_key": case["pilot_conversation_id"],
            "current_participant": copy.deepcopy(turn["speaker"]),
            "root_post_id": current_turn["post_id"],
            "source_completeness": {
                "reconstruction_grade": "A",
                "exact_text_complete": True,
                "parent_graph_complete": True,
                "chronology_complete": True,
                "account_publication_confirmed": True,
                "complete_prefix_through_turn": True,
                "limitations": [
                    "Historical replies after the selected target and production outcomes are withheld."
                ],
            },
        }
    materialised = semantic.materialise_semantic_delta(
        prior_ledger,
        current_turn,
        parsed,
        current_participant=turn["speaker"],
        genesis_context=genesis_context,
        semantic_schema=canonical_schema,
        ledger_schema=persisted_ledger_schema,
    )
    validation["materialisation_errors"] = _bounded(materialised.errors)
    if materialised.status == "semantic_reference_invalid":
        validation["semantic_reference_status"] = "failed"
        validation["materialisation_status"] = "not_run_due_to_reference_failure"
        return {"parsed": parsed, "ledger": None, "validation": validation}
    if materialised.status == "semantic_evidence_invalid":
        validation["semantic_reference_status"] = "passed"
        validation["evidence_span_status"] = "failed"
        validation["materialisation_status"] = "not_run_due_to_evidence_failure"
        return {"parsed": parsed, "ledger": None, "validation": validation}
    validation["semantic_reference_status"] = "passed"
    if not materialised.succeeded or materialised.ledger is None:
        validation["materialisation_status"] = "failed"
        if materialised.status == "persisted_ledger_validation_failure":
            validation["persisted_ledger_status"] = "failed"
        return {"parsed": parsed, "ledger": None, "validation": validation}
    ledger = copy.deepcopy(materialised.ledger)
    validation["materialisation_status"] = "passed"

    prefix_turns = [
        _materialisation_turn(case, item)
        for item in case["exact_private_transcript_prefix"][: int(turn["turn_index"]) + 1]
    ]
    persisted_errors = semantic.phase1.validate_ledger(
        ledger,
        {"conversation_key": case["pilot_conversation_id"], "turns": prefix_turns},
        persisted_ledger_schema,
        _immediate_previous=prior_ledger,
        _validate_history=False,
    )
    if ledger.get("ledger_sha256") != semantic.phase1.ledger_sha256(ledger):
        persisted_errors = list(persisted_errors) + ["ledger_self_hash_mismatch"]
    validation["persisted_ledger_errors"] = _bounded(persisted_errors)
    validation["persisted_ledger_status"] = (
        "passed" if not persisted_errors else "failed"
    )
    return {
        "parsed": parsed,
        "ledger": ledger,
        "local_id_map": copy.deepcopy(materialised.local_id_map),
        "validation": validation,
    }


def _duplicate_count(values: Sequence[Any]) -> int:
    seen: set[bytes] = set()
    duplicates = 0
    for value in values:
        material = canonical_json_bytes(value)
        if material in seen:
            duplicates += 1
        seen.add(material)
    return duplicates


def semantic_diagnostics(
    parsed: Mapping[str, Any],
    ledger: Mapping[str, Any],
    prior_ledger: Mapping[str, Any] | None,
    turn: Mapping[str, Any],
    *,
    provider_payload_size: int,
) -> dict[str, Any]:
    """Calculate structural observations without asserting semantic correctness."""

    prior = prior_ledger or {}
    collections = {
        "proposition": ("new_propositions", "proposition_updates"),
        "proposition_group": ("new_proposition_groups", "proposition_group_updates"),
        "issue": ("new_issue_states", "issue_state_updates"),
        "commitment": ("commitment_changes",),
        "obligation": ("obligation_changes",),
        "relation": ("new_relations",),
        "answer_target": ("answer_target_changes",),
        "rejected_answer_target": ("rejected_answer_target_changes",),
        "repair": ("repair_records",),
    }
    change_counts = {
        name: {
            field: len(parsed.get(field, [])) if isinstance(parsed.get(field), list) else 0
            for field in fields
        }
        for name, fields in collections.items()
    }
    issues = ledger.get("issue_states", [])
    obligations = ledger.get("conversational_obligations", [])
    terminal_issue = {"answered", "superseded", "abandoned", "expired", "no_stable_issue"}
    terminal_obligation = {"satisfied", "waived", "expired", "superseded"}
    live_issues = sum(item.get("status") not in terminal_issue for item in issues)
    closed_issues = len(issues) - live_issues
    open_obligations = sum(item.get("status") not in terminal_obligation for item in obligations)
    spans = list(live_probe._iter_evidence_spans(parsed))
    covered: set[int] = set()
    for _, span in spans:
        start, end = span.get("start_char"), span.get("end_char")
        if isinstance(start, int) and not isinstance(start, bool) and isinstance(end, int):
            covered.update(range(max(0, start), min(len(turn["text"]), end)))
    canonical_size = len(canonical_json_bytes(ledger))
    prior_size = len(canonical_json_bytes(prior_ledger)) if prior_ledger else 0
    relations = list(ledger.get("proposition_relations", []))
    relation_types = Counter(str(item.get("relation_type")) for item in relations)
    relation_addition_types = Counter(
        str(item.get("relation_type"))
        for item in parsed.get("new_relations", [])
        if isinstance(item, Mapping)
    )
    propositions = list(ledger.get("propositions", []))
    new_propositions = list(parsed.get("new_propositions", []))
    all_semantic_items = [
        item
        for field in (
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
            "abstentions",
            "warnings",
        )
        for item in (parsed.get(field, []) if isinstance(parsed.get(field), list) else [])
    ]
    duplicate_count = _duplicate_count(all_semantic_items)
    no_stable = any(item.get("status") == "no_stable_issue" for item in issues) or any(
        "no_stable_issue" in str(item) for item in parsed.get("abstentions", [])
    )
    attribution_separated = any(
        isinstance(item.get("speaker_or_attributor"), Mapping)
        and item["speaker_or_attributor"].get("kind") != "speaker"
        and item.get("commitment_status") != "speaker_committed"
        for item in propositions
    )
    structural = {
        "live_proposition_retained": any(
            item.get("status", item.get("lifecycle_status", "live")) == "live"
            for item in propositions
        ),
        "nearby_proposition_represented_separately": (
            relation_types["distinguishes"] > 0
            or relation_types["substitutes_for"] > 0
            or len(new_propositions) >= 2
        ),
        "compound_components_separately_represented": bool(
            ledger.get("proposition_groups")
        )
        and len(propositions) >= 2,
        "counterfactual_remains_live": any(
            item.get("status") not in terminal_issue
            and any(
                marker in str(item).lower()
                for marker in ("counterfactual", " if ", "would ", "could ")
            )
            for item in issues
        ),
        "correction_relation_represented": relation_types["corrects"] > 0,
        "rejected_answer_target_represented": bool(
            ledger.get("rejected_answer_targets")
        ),
        "open_question_or_obligation_retained": live_issues > 0 or open_obligations > 0,
        "no_stable_issue_used": no_stable,
        "attribution_separated_from_speaker_commitment": attribution_separated,
    }
    warnings = len(parsed.get("warnings", []))
    unsupported = int(parsed.get("unsupported_inferences_rejected", 0) or 0)
    diagnostic_flags = []
    if warnings:
        diagnostic_flags.append("provider_warning_present")
    if duplicate_count:
        diagnostic_flags.append("duplicate_semantic_object")
    if unsupported:
        diagnostic_flags.append("unsupported_inference_rejection_present")
    return {
        "artifact_evidence": "deterministic derivation",
        "change_counts": change_counts,
        "live_issue_count": live_issues,
        "closed_issue_count": closed_issues,
        "open_obligation_count": open_obligations,
        "relation_additions_by_type": dict(sorted(relation_addition_types.items())),
        "abstention_count": len(parsed.get("abstentions", [])),
        "warning_count": warnings,
        "unsupported_inference_rejections": unsupported,
        "ledger_canonical_byte_size": canonical_size,
        "provider_payload_byte_size": provider_payload_size,
        "state_growth_delta_bytes": canonical_size - prior_size,
        "evidence_span_count": len(spans),
        "evidence_coverage_character_count": len(covered),
        "evidence_coverage_fraction": (
            round(len(covered) / len(turn["text"]), 6) if turn["text"] else 0.0
        ),
        "unresolved_item_count": len(ledger.get("unresolved_items", [])),
        "resolved_item_count": len(ledger.get("resolved_items", [])),
        "lifecycle_transitions": copy.deepcopy(
            ledger.get("state_transitions", [])[-1:]
        ),
        "duplicate_semantic_objects": duplicate_count,
        "current_turn_semantic_density": (
            round(len(all_semantic_items) / max(1, len(turn["text"])), 6)
        ),
        "structural_signals": structural,
        "diagnostic_flags": diagnostic_flags,
    }


def process_response_twice(**kwargs: Any) -> dict[str, Any]:
    """Process identical response bytes twice and require byte determinism."""

    first = process_response_bytes(**kwargs)
    second = process_response_bytes(**kwargs)
    if canonical_json_bytes(first) != canonical_json_bytes(second):
        raise PilotError("saved response did not reprocess deterministically")
    return first


def call_state_from_validation(
    validation: Mapping[str, Any], diagnostics: Mapping[str, Any] | None
) -> str:
    """Map exact validation outcomes to one durable terminal call state."""

    for stage in (
        "strict_json",
        "provider_schema",
        "intended_canonical",
        "binding",
        "evidence_span",
        "semantic_reference",
    ):
        if validation.get(f"{stage}_status") == "failed":
            return "strict_validation_failed"
    if validation.get("materialisation_status") != "passed" or validation.get(
        "persisted_ledger_status"
    ) != "passed":
        return "materialisation_failed"
    if diagnostics and diagnostics.get("diagnostic_flags"):
        return "validated_with_diagnostic_flags"
    return "validated_and_materialised"


def _verify_case_manifest_hashes(manifest: Mapping[str, Any]) -> None:
    """Recompute every private case-row hash and the manifest content hash."""

    manifest_material = copy.deepcopy(dict(manifest))
    declared_manifest_hash = manifest_material.pop("manifest_content_sha256", None)
    if declared_manifest_hash != value_sha256(manifest_material):
        raise PilotError("private case-manifest content SHA-256 mismatch")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or len(cases) != SELECTED_CONVERSATION_COUNT:
        raise PilotError("private case-manifest does not contain exactly eight rows")
    seen_ids: set[str] = set()
    unique_turns = 0
    for row in cases:
        if not isinstance(row, Mapping):
            raise PilotError("private case-manifest row is not an object")
        row_material = copy.deepcopy(dict(row))
        declared_row_hash = row_material.pop("row_sha256", None)
        if declared_row_hash != value_sha256(row_material):
            raise PilotError("private case-manifest row SHA-256 mismatch")
        case_id = row.get("development_case_id")
        if not isinstance(case_id, str) or not case_id or case_id in seen_ids:
            raise PilotError("private case-manifest case identity is invalid")
        seen_ids.add(case_id)
        turns = row.get("exact_private_transcript_prefix")
        if not isinstance(turns, list) or len(turns) != row.get("unique_turn_count"):
            raise PilotError("private case-manifest row turn count mismatch")
        unique_turns += len(turns)
    if manifest.get("development_case_ids") != [
        row["development_case_id"] for row in cases
    ]:
        raise PilotError("private case-manifest case order binding mismatch")
    if manifest.get("selected_unique_turn_count") != unique_turns:
        raise PilotError("private case-manifest aggregate turn count mismatch")


def _verify_exposed_sidecar_rows(
    path: Path, seal_audit: Mapping[str, Any], *, raw: bytes | None = None
) -> None:
    """Validate every emitted sidecar row without emitting opaque identifiers."""

    seen: set[tuple[str, str]] = set()
    count = 0
    sidecar_raw = raw if raw is not None else _read_bytes(path, "exposed development sidecar")
    for row in _iter_jsonl_bytes(sidecar_raw, "exposed development sidecar"):
        material = copy.deepcopy(row)
        declared = material.pop("sidecar_row_sha256", None)
        if declared != value_sha256(material):
            raise PilotError("exposed development sidecar row SHA-256 mismatch")
        key = (str(row.get("conversation_key", "")), str(row.get("target_turn_id", "")))
        if not all(key) or key in seen:
            raise PilotError("exposed development sidecar key is absent or duplicated")
        seen.add(key)
        categories = {
            str(value)
            for value in row.get("exposure_categories", [])
            if isinstance(value, str)
        }
        if (
            _protected_metadata_row(row)
            or row.get("preliminary_within_family_held_out_eligibility") is not False
            or row.get("preliminary_held_out_eligibility") not in {None, False}
            or not all(
                row.get(field) == "exposed"
                for field in (
                    "effective_exposure_status",
                    "conversation_exposure_status",
                    "target_exposure_status",
                )
            )
            or not categories.intersection(DIRECT_EXPOSURE_CATEGORIES)
            or categories == {"structurally_mined_only"}
            or not isinstance(row.get("target_sequence_class"), str)
            or not row.get("target_sequence_class")
        ):
            raise PilotError("exposed development sidecar contains an ineligible row")
        count += 1
    if count != seal_audit.get("directly_exposed_rows_written_to_sidecar"):
        raise PilotError("exposed development sidecar row count differs from seal audit")


def _load_prepared(output: str | Path) -> dict[str, Any]:
    output_dir = _private_directory(output)
    freeze = _load_json(output_dir / "protocol-freeze.json", "private protocol freeze")
    execution_core = _execution_freeze_core(freeze)
    expected_freeze_policy = {
        "selection_policy_version": SELECTION_POLICY_VERSION,
        "development_stability_policy_version": DEVELOPMENT_STABILITY_POLICY_VERSION,
        "held_out_seal_policy_version": HELD_OUT_SEAL_POLICY_VERSION,
        "protected_metadata_administrative_scan_permitted": True,
        "protected_content_access_forbidden": True,
        "exposed_only_sidecar_required": True,
        "maximum_open_prefix_exceptions": MAX_OPEN_PREFIX_EXCEPTIONS,
    }
    for key, expected in expected_freeze_policy.items():
        if freeze.get(key) != expected:
            raise PilotError(f"private protocol freeze policy mismatch: {key}")
    saved_provider_schema = _load_json(
        output_dir / "provider-schema.json", "saved provider schema"
    )
    if value_sha256(saved_provider_schema) != EXPECTED_PROVIDER_SCHEMA_SHA256:
        raise PilotError("saved provider schema SHA-256 mismatch")
    prompt_manifest = _load_json(
        output_dir / "prompt-manifest.json", "private prompt manifest"
    )
    expected_prompt_manifest = {
        "canonical_schema_sha256": EXPECTED_CANONICAL_SCHEMA_SHA256,
        "provider_schema_sha256": EXPECTED_PROVIDER_SCHEMA_SHA256,
        "system_prompt_sha256": freeze.get("system_prompt_sha256"),
        "review_rubric_sha256": freeze.get("review_rubric_sha256"),
        "protocol_sha256": freeze.get("development_protocol_sha256"),
        "request_envelope_revision": REQUEST_CONTRACT_REVISION,
        "maximum_visible_output_tokens": MAX_OUTPUT_TOKENS,
        "client_timeout_seconds": CLIENT_TIMEOUT_SECONDS,
        "tools": [],
        "tool_choice_parameter_sent": False,
        "store_messages": False,
        "streaming": False,
        "search": False,
        "code_execution": False,
        "application_retries": 0,
        "sdk_grpc_retries": 0,
    }
    for key, expected in expected_prompt_manifest.items():
        if prompt_manifest.get(key) != expected:
            raise PilotError(f"private prompt manifest mismatch: {key}")
    source_integrity = _load_json(
        output_dir / "source-integrity.json", "private source integrity"
    )
    if (
        source_integrity.get("frozen_source_manifest_sha256")
        != EXPECTED_SOURCE_MANIFEST_SHA256
        or source_integrity.get("prospective_batch_manifest_sha256")
        != EXPECTED_PROSPECTIVE_MANIFEST_SHA256
        or source_integrity.get("target_prefix_metadata_sha256")
        != freeze.get("target_prefix_metadata_sha256")
        or source_integrity.get("cutoff") != FROZEN_CUTOFF
        or source_integrity.get("selection_policy_version")
        != SELECTION_POLICY_VERSION
        or source_integrity.get("development_stability_policy_version")
        != DEVELOPMENT_STABILITY_POLICY_VERSION
        or source_integrity.get("held_out_seal_policy_version")
        != HELD_OUT_SEAL_POLICY_VERSION
    ):
        raise PilotError("private source-integrity binding mismatch")

    # Validate the administrative seal and exposed-only sidecar before parsing
    # the transcript-bearing private case manifest.
    seal_path = output_dir / "held-out-content-seal-audit.json"
    seal_raw = _read_bytes(seal_path, "held-out content seal audit")
    if freeze.get("held_out_content_seal_audit_sha256") != sha256_bytes(seal_raw):
        raise PilotError("frozen held-out-seal audit SHA-256 mismatch")
    seal = live_probe.strict_json_loads(seal_raw)
    if seal.get("policy_version") != HELD_OUT_SEAL_POLICY_VERSION:
        raise PilotError("held-out content seal policy mismatch")
    required_zero_seal_fields = (
        "protected_metadata_rows_rendered_to_operator",
        "protected_rows_written_to_development_sidecar",
        "protected_transcript_records_opened",
        "protected_transcript_text_bytes_read",
        "protected_source_mapping_records_opened",
        "protected_provider_payload_count",
        "protected_model_output_count",
        "protected_human_review_count",
    )
    if (
        any(seal.get(key) != 0 for key in required_zero_seal_fields)
        or seal.get("substantive_seal_breached") is not False
        or seal.get("prior_protected_metadata_administrative_scan") is not True
        or seal.get("prior_protected_transcript_text_bytes_read") != 0
        or seal.get("prior_protected_provider_payload_count") != 0
        or seal.get("prior_protected_model_output_count") != 0
        or seal.get("prior_substantive_seal_breached") is not False
    ):
        raise PilotError("held-out content seal audit is not clean")
    sidecar_path = output_dir / "exposed-development-candidate-index.jsonl"
    sidecar_raw = _read_bytes(sidecar_path, "exposed development sidecar")
    if freeze.get("exposed_development_sidecar_sha256") != sha256_bytes(sidecar_raw):
        raise PilotError("frozen exposed-sidecar SHA-256 mismatch")
    _verify_exposed_sidecar_rows(sidecar_path, seal, raw=sidecar_raw)
    if freeze.get("target_prefix_metadata_sha256") != seal.get(
        "source_metadata_file_sha256"
    ):
        raise PilotError("target metadata source hash differs from seal audit")

    exclusion = _load_json(
        output_dir / "exposure-exclusion-audit.json", "exposure exclusion audit"
    )
    if any(
        exclusion.get(key) != 0
        for key in (
            "protected_substantive_rows_read",
            "genuinely_unexposed_transcript_rows_read",
            "structurally_mined_only_cases_selected",
            "protected_transcript_text_bytes_read",
        )
    ):
        raise PilotError("held-out/exposure exclusion audit is not zero")

    manifest_path = output_dir / "development-case-manifest.json"
    manifest_raw = _read_bytes(manifest_path, "private case manifest")
    manifest = live_probe.strict_json_loads(manifest_raw)
    _verify_case_manifest_hashes(manifest)
    expected_manifest_source_hashes = {
        "source_manifest_sha256": source_integrity.get(
            "frozen_source_manifest_sha256"
        ),
        "prospective_batch_manifest_sha256": source_integrity.get(
            "prospective_batch_manifest_sha256"
        ),
        "calibration_manifest_sha256": source_integrity.get(
            "calibration_manifest_sha256"
        ),
        "calibration_index_semantic_sha256": source_integrity.get(
            "calibration_index_semantic_sha256"
        ),
        "calibration_index_file_sha256": source_integrity.get(
            "calibration_index_file_sha256"
        ),
        "calibration_records_sha256": source_integrity.get(
            "calibration_records_sha256"
        ),
    }
    if manifest.get("frozen_source_hashes") != expected_manifest_source_hashes:
        raise PilotError("private case-manifest source hash binding mismatch")
    if sha256_bytes(manifest_raw) != freeze.get("private_case_manifest_sha256"):
        raise PilotError("private case-manifest SHA-256 differs from freeze")
    plan = _load_json(output_dir / "call-plan.json", "call plan")
    ledger = _load_json(output_dir / "call-ledger.json", "call ledger")
    if freeze.get("call_plan_sha256") != sha256_bytes(
        _read_bytes(output_dir / "call-plan.json", "call plan")
    ):
        raise PilotError("frozen call-plan SHA-256 mismatch")
    if manifest.get("selected_conversation_count") != SELECTED_CONVERSATION_COUNT:
        raise PilotError("prepared conversation count is not eight")
    unique_turns = manifest.get("selected_unique_turn_count")
    if (
        isinstance(unique_turns, bool)
        or not isinstance(unique_turns, int)
        or not MIN_UNIQUE_TURNS <= unique_turns <= MAX_UNIQUE_TURNS
    ):
        raise PilotError("prepared unique-turn count is outside 24-32")
    if freeze.get("exact_planned_provider_call_count") != unique_turns * 2:
        raise PilotError("frozen call count is not twice the unique-turn count")
    if plan.get("planned_provider_call_count") != unique_turns * 2:
        raise PilotError("call-plan count mismatch")
    immutable_plan = copy.deepcopy(plan)
    immutable_ledger = copy.deepcopy(ledger)
    for entry in immutable_ledger.get("entries", []):
        entry["state"] = "planned"
        entry["attempt_number"] = 0
        entry["provider_call_count"] = 0
        entry["started_at_utc"] = None
        entry["completed_at_utc"] = None
        entry["state_history"] = [
            {"state": "planned", "at_utc": None, "reason": "frozen_plan"}
        ]
        entry["call_identity"] = None
        entry["actual_payload_sha256"] = None
        entry["prior_ledger_sha256"] = None if entry.get("turn_index") else "genesis"
        for transient in (
            "provider_error_class",
            "provider_error_code",
            "sanitised_bounded_message",
            "raw_response_sha256",
            "validation_completed_at_utc",
            "validation_disposition",
            "stop_reason",
            "recovery_reason",
        ):
            entry.pop(transient, None)
    immutable_ledger["provider_call_count"] = 0
    if canonical_json_bytes(immutable_plan) != canonical_json_bytes(immutable_ledger):
        raise PilotError("durable call ledger does not derive from immutable plan")
    exception_count = sum(
        bool(case.get("development_stability_exception"))
        for case in manifest.get("cases", [])
    )
    if exception_count != freeze.get("selected_open_prefix_exception_count"):
        raise PilotError("frozen open-prefix exception count mismatch")
    return {
        "output_dir": output_dir,
        "manifest": manifest,
        "manifest_sha256": sha256_bytes(manifest_raw),
        "freeze": freeze,
        "freeze_sha256": freeze["execution_freeze_core_sha256"],
        "execution_freeze_core": execution_core,
        "plan": plan,
        "ledger": ledger,
        "exclusion": exclusion,
    }


def _verify_pushed_clean_freeze(prepared: Mapping[str, Any]) -> str:
    if _git_output("status", "--porcelain"):
        raise PilotError("tracked worktree or index is dirty")
    branch = _git_output("branch", "--show-current")
    expected_branch = "research/proposition-ledger-phase2a-development-pilot"
    if branch != expected_branch:
        raise PilotError(f"unexpected Phase 2A branch: {branch}")
    head = _git_output("rev-parse", "HEAD")
    remote = _git_output("rev-parse", f"origin/{expected_branch}")
    if head != remote:
        raise PilotError("local protocol-freeze commit is not equal to remote")
    subject = _git_output("show", "-s", "--format=%s", "HEAD")
    if subject != "Freeze proposition ledger development pilot":
        raise PilotError("HEAD is not the protocol-freeze commit")
    parent = _git_output("rev-parse", "HEAD^")
    if parent != SOURCE_COMMIT:
        raise PilotError(
            "protocol-freeze commit first parent is not the approved source commit"
        )
    if _git_output("merge-base", head, SOURCE_COMMIT) != SOURCE_COMMIT:
        raise PilotError("protocol-freeze commit is not descended from approved source")
    tracked = _tracked_input_record()
    private_freeze = prepared["freeze"]
    expected_fields = {
        "source_commit": SOURCE_COMMIT,
        "canonical_schema_sha256": EXPECTED_CANONICAL_SCHEMA_SHA256,
        "provider_schema_sha256": EXPECTED_PROVIDER_SCHEMA_SHA256,
        "system_prompt_sha256": tracked["hashes"]["system_prompt_sha256"],
        "review_rubric_sha256": tracked["hashes"]["review_rubric_sha256"],
        "development_protocol_sha256": tracked["hashes"]["protocol_sha256"],
        "private_case_manifest_sha256": prepared["manifest_sha256"],
        "selected_conversation_count": SELECTED_CONVERSATION_COUNT,
        "selected_unique_turn_count": prepared["manifest"][
            "selected_unique_turn_count"
        ],
        "exact_planned_provider_call_count": prepared["plan"][
            "planned_provider_call_count"
        ],
        "maximum_provider_call_budget": MAX_PROVIDER_CALL_BUDGET,
        "request_envelope_revision": REQUEST_CONTRACT_REVISION,
        "selection_algorithm_version": SELECTION_ALGORITHM_VERSION,
        "selection_policy_version": SELECTION_POLICY_VERSION,
        "development_stability_policy_version": (
            DEVELOPMENT_STABILITY_POLICY_VERSION
        ),
        "held_out_seal_policy_version": HELD_OUT_SEAL_POLICY_VERSION,
        "protected_metadata_administrative_scan_permitted": True,
        "protected_content_access_forbidden": True,
        "exposed_only_sidecar_required": True,
        "maximum_open_prefix_exceptions": MAX_OPEN_PREFIX_EXCEPTIONS,
        "model_order_counterbalancing_rule": (
            "sha256-parity-bit-of-protocol-hash-and-pilot-conversation-key"
        ),
        "selected_open_prefix_exception_count": sum(
            bool(case.get("development_stability_exception"))
            for case in prepared["manifest"].get("cases", [])
        ),
    }
    for key, value in expected_fields.items():
        if private_freeze.get(key) != value:
            raise PilotError(f"private protocol freeze mismatch: {key}")
    tracked_freeze = _load_json(TRACKED_FREEZE_PATH, "tracked protocol freeze")
    for key, value in expected_fields.items():
        if tracked_freeze.get(key) != value:
            raise PilotError(f"tracked protocol freeze mismatch: {key}")
    if tracked_freeze.get("provider_profiles") != list(PROFILES):
        raise PilotError("tracked provider profiles mismatch")
    if canonical_json_bytes(tracked_freeze) != canonical_json_bytes(private_freeze):
        raise PilotError("tracked and private protocol-freeze wrappers differ")
    _execution_freeze_core(tracked_freeze)
    return head


def _record_phase14_carry_forward(output_dir: Path) -> dict[str, Any]:
    """Inspect only the saved validation and parsed artefacts after Git freeze."""

    validation_path = CORRECTED_PHASE14_RUN / "grok-4.3/validation.json"
    parsed_path = CORRECTED_PHASE14_RUN / "grok-4.3/response.parsed.json"
    validation_raw = _read_bytes(validation_path, "Phase 1.4 Grok 4.3 validation")
    parsed_raw = _read_bytes(parsed_path, "Phase 1.4 Grok 4.3 parsed response")
    validation = live_probe.strict_json_loads(validation_raw)
    # Parse solely to prove this is the same persisted parsed artefact; do not inspect raw.
    parsed = live_probe.strict_json_loads(parsed_raw)
    smoke = validation.get("semantic_smoke")
    if not isinstance(smoke, Mapping) or smoke.get("status") != "failed":
        raise PilotError("Phase 1.4 Grok 4.3 failed semantic smoke is absent")
    checks = smoke.get("checks")
    if not isinstance(checks, Mapping):
        raise PilotError("Phase 1.4 semantic-smoke checks are absent")
    failed = sorted(str(key) for key, passed in checks.items() if passed is not True)
    if not failed:
        raise PilotError("Phase 1.4 semantic-smoke failed invariant is absent")
    record = {
        "artifact_evidence": "frozen input",
        "diagnostic_id": "phase1_4_grok_4_3_carry_forward_diagnostic",
        "status": "recorded_after_phase2a_provider_execution_from_frozen_artefacts",
        "exact_failed_invariants": failed,
        "phase1_4_validation_sha256": sha256_bytes(validation_raw),
        "phase1_4_parsed_response_sha256": sha256_bytes(parsed_raw),
        "phase1_4_parsed_schema_version": parsed.get("schema_version"),
        "recurrence_result": "phase1_4_failure_pattern_not_assessable",
    }
    _write_json(output_dir / "phase1.4-carry-forward-diagnostic.json", record)
    return record


def _ledger_index(ledger: Mapping[str, Any]) -> dict[tuple[str, int, str], int]:
    return {
        (
            str(entry.get("pilot_conversation_id")),
            int(entry.get("turn_index", -1)),
            str(entry.get("profile_id")),
        ): index
        for index, entry in enumerate(ledger.get("entries", []))
    }


def _transition_call(
    output_dir: Path,
    index: int,
    new_state: str,
    **updates: Any,
) -> dict[str, Any]:
    ledger = _load_json(output_dir / "call-ledger.json", "call ledger")
    if new_state not in VALID_CALL_STATES - {"planned"}:
        raise PilotError(f"unknown call state: {new_state}")
    entry = ledger["entries"][index]
    old_state = entry.get("state")
    allowed: dict[str, set[str]] = {
        "planned": {
            "sending",
            "blocked_by_prior_turn_failure",
            "not_attempted_due_to_global_failure",
        },
        "sending": {
            "response_received",
            "provider_error_received",
            "uncertain_after_send",
        },
        "response_received": {
            "strict_validation_failed",
            "materialisation_failed",
            "validated_and_materialised",
            "validated_with_diagnostic_flags",
        },
    }
    if new_state not in allowed.get(str(old_state), set()):
        raise PilotError(f"illegal call transition: {old_state}->{new_state}")
    entry["state"] = new_state
    entry.update(copy.deepcopy(updates))
    history = entry.setdefault("state_history", [])
    history.append(
        {
            "state": new_state,
            "at_utc": updates.get("started_at_utc")
            or updates.get("completed_at_utc")
            or utc_now(),
            "reason": updates.get("stop_reason")
            or updates.get("validation_disposition"),
        }
    )
    ledger["provider_call_count"] = sum(
        int(item.get("provider_call_count", 0)) for item in ledger["entries"]
    )
    if ledger["provider_call_count"] > ledger["planned_provider_call_count"]:
        raise PilotError("provider-call count exceeded frozen plan")
    if ledger["provider_call_count"] > MAX_PROVIDER_CALL_BUDGET:
        raise PilotError("provider-call count exceeded 64")
    _write_json(output_dir / "call-ledger.json", ledger)
    return ledger


def _case_by_id(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(case["development_case_id"]): copy.deepcopy(dict(case))
        for case in manifest.get("cases", [])
    }


def _turn_by_id(case: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(turn["turn_id"]): copy.deepcopy(dict(turn))
        for turn in case["exact_private_transcript_prefix"]
    }


def _turn_output_dir(output_dir: Path, entry: Mapping[str, Any]) -> Path:
    return (
        output_dir
        / "conversations"
        / str(entry["pilot_conversation_id"])
        / str(entry["model"])
        / f"turn-{int(entry['turn_index']):03d}"
    )


def _load_prior_ledger(
    output_dir: Path,
    case: Mapping[str, Any],
    profile: Mapping[str, Any],
    turn_index: int,
) -> dict[str, Any] | None:
    if turn_index == 0:
        return None
    path = (
        output_dir
        / "conversations"
        / str(case["pilot_conversation_id"])
        / str(profile["model"])
        / f"turn-{turn_index - 1:03d}"
        / "materialised-ledger.json"
    )
    return _load_json(path, "prior materialised ledger")


def _validate_prior_ledger_before_request(
    prior_ledger: Mapping[str, Any] | None,
    case: Mapping[str, Any],
    turn_index: int,
    persisted_ledger_schema: Mapping[str, Any],
) -> None:
    """Revalidate the exact predecessor schema, binding, index, and self-hash."""

    if turn_index == 0:
        if prior_ledger is not None:
            raise PilotError("genesis request unexpectedly has a prior ledger")
        return
    if prior_ledger is None:
        raise PilotError("non-genesis request lacks a validated prior ledger")
    if semantic.phase1._jsonschema_errors(prior_ledger, persisted_ledger_schema):
        raise PilotError("prior persisted ledger schema validation failed before send")
    if prior_ledger.get("ledger_sha256") != semantic.phase1.ledger_sha256(
        prior_ledger
    ):
        raise PilotError("prior persisted ledger self-hash failed before send")
    if prior_ledger.get("conversation_key") != case.get("pilot_conversation_id"):
        raise PilotError("prior persisted ledger conversation binding failed before send")
    if prior_ledger.get("as_of_turn_index") != turn_index - 1:
        raise PilotError("prior persisted ledger turn binding failed before send")


class XaiPilotTransport:
    """The sole live official-SDK boundary, imported only in live mode."""

    def __init__(self) -> None:
        """Construct the pinned official client with retries disabled."""

        key = os.environ.get("XAI_API_KEY")
        if not key:
            raise PilotError("phase2a_not_run_missing_xai_api_key")
        try:
            from google.protobuf.json_format import MessageToDict
            from xai_sdk import Client
            from xai_sdk.chat import system, user
            from xai_sdk.proto import chat_pb2
        except ImportError as exc:
            raise PilotError("pinned official xai-sdk import failed") from exc
        self._message_to_dict = MessageToDict
        self._system = system
        self._user = user
        self._chat_pb2 = chat_pb2
        self._client = Client(
            api_key=key,
            timeout=CLIENT_TIMEOUT_SECONDS,
            channel_options=list(live_probe.NO_RETRY_CHANNEL_OPTIONS),
        )

    def sample(
        self, profile: Mapping[str, Any], request_context: Mapping[str, Any]
    ) -> ProviderObservation:
        """Make exactly one non-streaming structured-output provider request."""

        schema = request_context["provider_schema"]
        response_format = self._chat_pb2.ResponseFormat(
            format_type=self._chat_pb2.FORMAT_TYPE_JSON_SCHEMA,
            schema=canonical_json_bytes(schema).decode("utf-8"),
        )
        # tool_choice is intentionally omitted when tools is empty.
        chat = self._client.chat.create(
            model=profile["model"],
            messages=[
                self._system(request_context["system_prompt"]),
                self._user(request_context["user_payload"]),
            ],
            max_tokens=MAX_OUTPUT_TOKENS,
            reasoning_effort="low",
            tools=[],
            parallel_tool_calls=False,
            response_format=response_format,
            search_parameters=None,
            store_messages=False,
        )
        response = chat.sample()  # the sole provider invocation
        if not isinstance(response.content, str):
            raise PilotError("provider response content was not text")
        usage = self._message_to_dict(
            response.usage, preserving_proto_field_name=True
        )
        return ProviderObservation(
            raw_text=response.content,
            returned_model_id=response.proto.model or None,
            provider_response_id=response.id or None,
            finish_reason=response.finish_reason or None,
            usage=usage,
        )

    def close(self) -> None:
        """Close transient SDK channels without persisting provider state."""

        self._client.close()


def _persist_received_observation(
    turn_dir: Path,
    observation: ProviderObservation,
    request_metadata: Mapping[str, Any],
    *,
    latency_seconds: float,
) -> bytes:
    """Durably preserve the provider observation before processing it."""

    raw = observation.raw_text.encode("utf-8", errors="strict")
    # Raw visible bytes are the irreplaceable observation. Write them first so
    # a parser, validator, diagnostic, or materialiser defect cannot strand a
    # non-repeatable response_received call without its response evidence.
    _atomic_write(turn_dir / "response.raw.txt", raw)
    _write_json(turn_dir / "request-metadata.json", request_metadata)
    _write_json(
        turn_dir / "usage.json",
        {
            "artifact_evidence": "provider observation",
            "usage": dict(observation.usage),
            "returned_model_id": observation.returned_model_id,
            "provider_response_id": observation.provider_response_id,
            "finish_reason": observation.finish_reason,
            "latency_seconds": latency_seconds,
        },
    )
    return raw


def _persist_processed_response(
    turn_dir: Path,
    processed: Mapping[str, Any],
    diagnostics: Mapping[str, Any] | None,
) -> None:
    """Persist deterministic derivations of an already saved observation."""

    if processed.get("parsed") is not None:
        _write_json(turn_dir / "response.parsed.json", processed["parsed"])
    _write_json(turn_dir / "validation.json", processed["validation"])
    if diagnostics is not None:
        _write_json(turn_dir / "semantic-diagnostics.json", diagnostics)
    if processed.get("ledger") is not None:
        _write_json(turn_dir / "materialised-ledger.json", processed["ledger"])


def _persist_provider_error(
    turn_dir: Path,
    request_metadata: Mapping[str, Any],
    classification: live_probe.ErrorClassification,
    *,
    latency_seconds: float,
) -> None:
    _write_json(turn_dir / "request-metadata.json", request_metadata)
    _write_json(
        turn_dir / "validation.json",
        {
            "artifact_evidence": "provider observation",
            "status": "provider_error_received",
            "provider_error_class": classification.category,
            "provider_error_code": classification.code,
            "sanitised_bounded_message": classification.message,
            "latency_seconds": latency_seconds,
        },
    )


def _mark_chain_blocked(
    output_dir: Path,
    ledger: Mapping[str, Any],
    failed_entry: Mapping[str, Any],
) -> dict[str, Any]:
    result = copy.deepcopy(dict(ledger))
    for index, entry in enumerate(result["entries"]):
        if (
            entry["state"] == "planned"
            and entry["pilot_conversation_id"] == failed_entry["pilot_conversation_id"]
            and entry["profile_id"] == failed_entry["profile_id"]
            and int(entry["turn_index"]) > int(failed_entry["turn_index"])
        ):
            result = _transition_call(
                output_dir,
                index,
                "blocked_by_prior_turn_failure",
                completed_at_utc=utc_now(),
                stop_reason=f"prior_turn_{failed_entry['state']}",
            )
    return result


def _mark_global_stop(
    output_dir: Path, ledger: Mapping[str, Any], reason: str
) -> dict[str, Any]:
    result = copy.deepcopy(dict(ledger))
    for index, entry in enumerate(result["entries"]):
        if entry["state"] == "planned":
            result = _transition_call(
                output_dir,
                index,
                "not_attempted_due_to_global_failure",
                completed_at_utc=utc_now(),
                stop_reason=reason,
            )
    return result


@contextmanager
def _exclusive_execution_lock(output_dir: Path) -> Iterable[None]:
    """Hold a non-blocking process lock on the stable private-run directory."""

    before = output_dir.lstat()
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open(output_dir, flags)
    acquired = False
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or stat.S_IMODE(opened.st_mode) != 0o700
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise PilotError("private run changed while acquiring execution lock")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError as exc:
            raise PilotError(
                "another live-pilot execution holds the private-run lock"
            ) from exc
        after = output_dir.lstat()
        if (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino):
            raise PilotError("private run changed while acquiring execution lock")
        yield
    finally:
        try:
            if acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def execute_live_pilot(
    output: str | Path,
    *,
    confirm_provider_call_budget: int | None,
    transport_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Execute each frozen call at most once with independent model chains."""

    if not os.environ.get("XAI_API_KEY"):
        return {
            "status": "phase2a_not_run_missing_xai_api_key",
            "provider_calls": 0,
        }
    output_dir = _private_directory(output)
    with _exclusive_execution_lock(output_dir):
        return _execute_live_pilot_locked(
            output_dir,
            confirm_provider_call_budget=confirm_provider_call_budget,
            transport_factory=transport_factory,
        )


def _execute_live_pilot_locked(
    output: str | Path,
    *,
    confirm_provider_call_budget: int | None,
    transport_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Execute the immutable call plan while holding the per-run process lock."""

    prepared = _load_prepared(output)
    exact_budget = prepared["freeze"]["exact_planned_provider_call_count"]
    if confirm_provider_call_budget != exact_budget:
        raise PilotError(
            f"--confirm-provider-call-budget must be exactly {exact_budget}"
        )
    if exact_budget > MAX_PROVIDER_CALL_BUDGET:
        raise PilotError("frozen provider-call budget exceeds 64")
    ledger = prepared["ledger"]
    _verify_state_history(ledger)
    states = [entry.get("state") for entry in ledger["entries"]]
    if any(state == "sending" for state in states):
        for index, state in enumerate(states):
            if state == "sending":
                ledger = _transition_call(
                    prepared["output_dir"],
                    index,
                    "uncertain_after_send",
                    completed_at_utc=utc_now(),
                    stop_reason="prior_process_ended_after_send",
                )
        _mark_global_stop(prepared["output_dir"], ledger, "uncertain_prior_call")
        raise PilotError("call ledger contains an uncertain sent call; no calls repeated")
    if any(state == "uncertain_after_send" for state in states):
        raise PilotError("call ledger already contains an uncertain call")
    if any(state == "response_received" for state in states):
        _mark_global_stop(
            prepared["output_dir"], ledger, "sent_response_received_but_unclosed"
        )
        raise PilotError(
            "call ledger contains a sent-but-unclosed response_received call"
        )
    if any(state not in VALID_CALL_STATES for state in states):
        raise PilotError("call ledger contains an unknown state")
    if all(state != "planned" for state in states):
        raise PilotError("refusing to repeat a completed provider call plan")

    protocol_commit = _verify_pushed_clean_freeze(prepared)
    live_probe._remove_unrelated_credentials()
    live_probe.verify_pinned_environment()
    tracked = _tracked_input_record()
    canonical, provider_schema, persisted_schema = _load_schemas()
    cases = _case_by_id(prepared["manifest"])
    factory = transport_factory or XaiPilotTransport
    try:
        transport = factory()
    except Exception as exc:
        failed = _mark_global_stop(
            prepared["output_dir"],
            _load_json(prepared["output_dir"] / "call-ledger.json", "call ledger"),
            f"local_transport_initialisation_failure:{type(exc).__name__}",
        )
        carry = _record_phase14_carry_forward(prepared["output_dir"])
        return _finalize_execution(prepared, failed, protocol_commit, carry)

    global_stop: str | None = None
    try:
        for plan_index in range(len(ledger["entries"])):
            ledger = _load_json(
                prepared["output_dir"] / "call-ledger.json", "call ledger"
            )
            entry = ledger["entries"][plan_index]
            if entry["state"] != "planned":
                continue
            if global_stop is not None:
                ledger = _mark_global_stop(prepared["output_dir"], ledger, global_stop)
                break
            case = cases[str(entry["development_case_id"])]
            turn = _turn_by_id(case)[str(entry["pilot_turn_id"])]
            profile = PROFILE_BY_ID[str(entry["profile_id"])]
            prior = _load_prior_ledger(
                prepared["output_dir"], case, profile, int(turn["turn_index"])
            )
            _validate_prior_ledger_before_request(
                prior,
                case,
                int(turn["turn_index"]),
                persisted_schema,
            )
            payload = build_turn_payload(
                case,
                turn,
                prior,
                protocol_freeze_sha256=prepared["freeze_sha256"],
                provider_schema=provider_schema,
            )
            payload_bytes = canonical_json_bytes(payload)
            payload_hash = sha256_bytes(payload_bytes)
            prior_hash = prior.get("ledger_sha256") if prior is not None else "genesis"
            call_identity = value_sha256(
                {
                    "protocol_freeze_sha256": prepared["freeze_sha256"],
                    "case_manifest_sha256": prepared["manifest_sha256"],
                    "pilot_conversation_id": case["pilot_conversation_id"],
                    "pilot_turn_id": turn["turn_id"],
                    "profile_id": profile["profile_id"],
                    "current_turn_payload_hash": payload_hash,
                    "prior_ledger_hash_or_genesis_marker": prior_hash,
                    "provider_schema_sha256": EXPECTED_PROVIDER_SCHEMA_SHA256,
                    "system_prompt_sha256": tracked["hashes"]["system_prompt_sha256"],
                }
            )
            started = utc_now()
            ledger = _transition_call(
                prepared["output_dir"],
                plan_index,
                "sending",
                started_at_utc=started,
                attempt_number=1,
                provider_call_count=1,
                actual_payload_sha256=payload_hash,
                prior_ledger_sha256=prior_hash,
                call_identity=call_identity,
            )
            request_repr = build_request_representation(
                profile,
                payload,
                system_prompt=tracked["system_prompt"],
                provider_schema=provider_schema,
            )
            metadata = _request_metadata_record(
                entry=entry,
                case=case,
                turn=turn,
                profile=profile,
                call_identity=call_identity,
                payload_hash=payload_hash,
                prior_hash=prior_hash,
                request_repr=request_repr,
                system_prompt_sha256=tracked["hashes"]["system_prompt_sha256"],
            )
            request_context = {
                "system_prompt": tracked["system_prompt"],
                "user_payload": payload_bytes.decode("utf-8"),
                "provider_schema": provider_schema,
                "request_representation": request_repr,
            }
            began = time.monotonic()
            try:
                observed = transport.sample(profile, request_context)
                if isinstance(observed, live_probe.LiveObservation):
                    observation = ProviderObservation(
                        raw_text=observed.raw_text,
                        returned_model_id=observed.returned_model_id,
                        provider_response_id=observed.provider_response_id,
                        finish_reason=observed.finish_reason,
                        usage=observed.usage,
                    )
                elif isinstance(observed, ProviderObservation):
                    observation = observed
                elif isinstance(observed, Mapping):
                    observation = ProviderObservation(**observed)
                else:
                    raise PilotError("fake/live transport returned unsupported observation")
            except BaseException as exc:
                latency = round(time.monotonic() - began, 6)
                classification = live_probe.classify_provider_error(exc)
                completed = utc_now()
                _persist_provider_error(
                    _turn_output_dir(prepared["output_dir"], entry),
                    metadata,
                    classification,
                    latency_seconds=latency,
                )
                target_state = (
                    "uncertain_after_send"
                    if classification.call_state == "uncertain_after_send"
                    else "provider_error_received"
                )
                ledger = _transition_call(
                    prepared["output_dir"],
                    plan_index,
                    target_state,
                    completed_at_utc=completed,
                    provider_error_class=classification.category,
                    provider_error_code=classification.code,
                    sanitised_bounded_message=classification.message,
                )
                failed_entry = ledger["entries"][plan_index]
                if classification.category in GLOBAL_PROVIDER_FAILURES:
                    global_stop = classification.category
                else:
                    ledger = _mark_chain_blocked(
                        prepared["output_dir"], ledger, failed_entry
                    )
                continue

            latency = round(time.monotonic() - began, 6)
            turn_dir = _turn_output_dir(prepared["output_dir"], entry)
            raw = _persist_received_observation(
                turn_dir,
                observation,
                metadata,
                latency_seconds=latency,
            )
            completed = utc_now()
            ledger = _transition_call(
                prepared["output_dir"],
                plan_index,
                "response_received",
                completed_at_utc=completed,
                raw_response_sha256=sha256_bytes(raw),
            )
            try:
                processed = process_response_twice(
                    raw=raw,
                    case=case,
                    turn=turn,
                    prior_ledger=prior,
                    canonical_schema=canonical,
                    provider_schema=provider_schema,
                    persisted_ledger_schema=persisted_schema,
                )
                diagnostics = None
                if processed.get("ledger") is not None:
                    diagnostics = semantic_diagnostics(
                        processed["parsed"],
                        processed["ledger"],
                        prior,
                        turn,
                        provider_payload_size=len(payload_bytes),
                    )
                target_state = call_state_from_validation(
                    processed["validation"], diagnostics
                )
                _persist_processed_response(
                    turn_dir,
                    processed,
                    diagnostics,
                )
                ledger = _transition_call(
                    prepared["output_dir"],
                    plan_index,
                    target_state,
                    validation_completed_at_utc=utc_now(),
                    validation_disposition=target_state,
                )
                if target_state in CHAIN_FAILURE_STATES:
                    ledger = _mark_chain_blocked(
                        prepared["output_dir"],
                        ledger,
                        ledger["entries"][plan_index],
                    )
            except Exception as exc:
                ledger = _transition_call(
                    prepared["output_dir"],
                    plan_index,
                    "materialisation_failed",
                    validation_completed_at_utc=utc_now(),
                    validation_disposition="post_call_implementation_defect",
                    sanitised_bounded_message=f"{type(exc).__name__}"[:ERROR_LIMIT],
                )
                global_stop = "post_call_implementation_defect"
    finally:
        try:
            transport.close()
        except Exception:
            pass
    ledger = _load_json(prepared["output_dir"] / "call-ledger.json", "call ledger")
    if global_stop:
        ledger = _mark_global_stop(prepared["output_dir"], ledger, global_stop)
    carry = _record_phase14_carry_forward(prepared["output_dir"])
    return _finalize_execution(prepared, ledger, protocol_commit, carry)


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    """Return a deterministic nearest-rank percentile for a non-empty sample."""

    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return round(ordered[rank - 1], 6)


def _turn_artifact_path(
    output_dir: Path, entry: Mapping[str, Any], name: str
) -> Path:
    """Resolve one pilot-local per-turn artefact path."""

    return _turn_output_dir(output_dir, entry) / name


def _usage_numbers(record: Mapping[str, Any]) -> dict[str, Any]:
    """Extract only official visible usage counters, preserving raw cost ticks."""

    usage = record.get("usage", record)
    if not isinstance(usage, Mapping):
        usage = {}
    numeric_fields = (
        "prompt_tokens",
        "prompt_text_tokens",
        "cached_prompt_tokens",
        "cached_prompt_text_tokens",
        "reasoning_tokens",
        "completion_tokens",
        "total_tokens",
    )
    result: dict[str, Any] = {}
    for field in numeric_fields:
        value = usage.get(field, 0)
        result[field] = (
            int(value)
            if isinstance(value, (int, float, str)) and str(value).isdigit()
            else 0
        )
    result["cost_in_usd_ticks"] = usage.get("cost_in_usd_ticks")
    return result


def derive_profile_metrics(
    call_ledger: Mapping[str, Any], output_dir: str | Path | None = None
) -> dict[str, Any]:
    """Reconcile per-profile operational metrics to the durable call ledger."""

    base = _absolute(output_dir) if output_dir is not None else None
    result: dict[str, Any] = {
        "artifact_evidence": "deterministic derivation",
        "profiles": {},
    }
    successful_states = {
        "validated_and_materialised",
        "validated_with_diagnostic_flags",
    }
    for profile in PROFILES:
        entries = [
            item
            for item in call_ledger.get("entries", [])
            if item.get("profile_id") == profile["profile_id"]
        ]
        attempted = [item for item in entries if int(item.get("provider_call_count", 0))]
        definite = [
            item
            for item in attempted
            if item.get("state") != "uncertain_after_send"
        ]
        validations: list[dict[str, Any]] = []
        diagnostics: list[dict[str, Any]] = []
        usages: list[dict[str, Any]] = []
        if base is not None:
            for entry in definite:
                validation_path = _turn_artifact_path(base, entry, "validation.json")
                if validation_path.exists():
                    validations.append(_load_json(validation_path, "turn validation"))
                diagnostic_path = _turn_artifact_path(
                    base, entry, "semantic-diagnostics.json"
                )
                if diagnostic_path.exists():
                    diagnostics.append(_load_json(diagnostic_path, "turn diagnostics"))
                usage_path = _turn_artifact_path(base, entry, "usage.json")
                if usage_path.exists():
                    usages.append(_load_json(usage_path, "turn usage"))

        def stage_count(stage: str) -> int:
            return sum(item.get(f"{stage}_status") == "passed" for item in validations)

        chain_groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for entry in entries:
            chain_groups[str(entry["pilot_conversation_id"])].append(entry)
        complete_chains = sum(
            all(item.get("state") in successful_states for item in chain)
            for chain in chain_groups.values()
        )
        sizes = [float(item["ledger_canonical_byte_size"]) for item in diagnostics]
        growth = [float(item["state_growth_delta_bytes"]) for item in diagnostics]
        latencies = [
            float(item["latency_seconds"])
            for item in validations
            if item.get("status") == "provider_error_received"
            and isinstance(item.get("latency_seconds"), (int, float))
        ]
        usage_totals = Counter()
        raw_costs: list[str] = []
        for usage_record in usages:
            if isinstance(usage_record.get("latency_seconds"), (int, float)):
                latencies.append(float(usage_record["latency_seconds"]))
            numbers = _usage_numbers(usage_record)
            for field in (
                "prompt_tokens",
                "cached_prompt_tokens",
                "cached_prompt_text_tokens",
                "reasoning_tokens",
                "completion_tokens",
                "total_tokens",
            ):
                usage_totals[field] += int(numbers[field])
            if numbers["cost_in_usd_ticks"] is not None:
                raw_costs.append(str(numbers["cost_in_usd_ticks"]))
        cost_total = None
        if raw_costs and all(value.isdigit() for value in raw_costs):
            cost_total = str(sum(int(value) for value in raw_costs))
        denominator = len(definite)
        profile_result = {
            "profile_id": profile["profile_id"],
            "model": profile["model"],
            "planned_calls": len(entries),
            "attempted_calls": len(attempted),
            "definite_responses": len(definite),
            "provider_errors": sum(
                item.get("state") == "provider_error_received" for item in entries
            ),
            "uncertain_calls": sum(
                item.get("state") == "uncertain_after_send" for item in entries
            ),
            "strict_json_success_count": stage_count("strict_json"),
            "provider_schema_success_count": stage_count("provider_schema"),
            "intended_canonical_success_count": stage_count("intended_canonical"),
            "evidence_span_success_count": stage_count("evidence_span"),
            "semantic_reference_success_count": stage_count("semantic_reference"),
            "materialisation_success_count": stage_count("materialisation"),
            "persisted_ledger_success_count": stage_count("persisted_ledger"),
            "complete_conversation_chain_count": complete_chains,
            "blocked_downstream_turn_count": sum(
                item.get("state") == "blocked_by_prior_turn_failure" for item in entries
            ),
            "diagnostic_flag_count": sum(
                len(item.get("diagnostic_flags", [])) for item in diagnostics
            ),
            "maximum_ledger_size_bytes": int(max(sizes)) if sizes else None,
            "median_ledger_size_bytes": median(sizes) if sizes else None,
            "average_state_growth_bytes_per_turn": (
                round(sum(growth) / len(growth), 6) if growth else None
            ),
            "prompt_tokens": usage_totals["prompt_tokens"],
            "cached_prompt_tokens": (
                usage_totals["cached_prompt_tokens"]
                or usage_totals["cached_prompt_text_tokens"]
            ),
            "reasoning_tokens": usage_totals["reasoning_tokens"],
            "completion_tokens": usage_totals["completion_tokens"],
            "total_tokens": usage_totals["total_tokens"],
            "raw_provider_cost_field": {
                "field": "cost_in_usd_ticks",
                "values": raw_costs,
                "raw_integer_total": cost_total,
                "currency_conversion_performed": False,
            },
            "latency_seconds": {
                "median": median(latencies) if latencies else None,
                "p95": _percentile(latencies, 0.95),
                "maximum": max(latencies) if latencies else None,
            },
        }
        for stage in (
            "strict_json",
            "provider_schema",
            "intended_canonical",
            "evidence_span",
            "semantic_reference",
            "materialisation",
            "persisted_ledger",
        ):
            count = profile_result[f"{stage}_success_count"]
            profile_result[f"{stage}_success_rate"] = (
                round(count / denominator, 6) if denominator else None
            )
        result["profiles"][profile["profile_id"]] = profile_result
    return result


def derive_paired_comparison(
    call_ledger: Mapping[str, Any],
    profile_metrics: Mapping[str, Any] | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Derive paired completion and structural disagreements by conversation."""

    successful = {"validated_and_materialised", "validated_with_diagnostic_flags"}
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for entry in call_ledger.get("entries", []):
        grouped[(str(entry["pilot_conversation_id"]), str(entry["profile_id"]))].append(
            entry
        )
    conversations = sorted({key[0] for key in grouped})
    counts = Counter()
    structural_disagreements = 0
    ledger_size_differences: list[int] = []
    base = _absolute(output_dir) if output_dir is not None else None
    per_conversation = []
    for conversation in conversations:
        chains = {
            profile["profile_id"]: sorted(
                grouped.get((conversation, profile["profile_id"]), []),
                key=lambda item: int(item["turn_index"]),
            )
            for profile in PROFILES
        }
        complete = {
            profile_id: bool(chain)
            and all(item.get("state") in successful for item in chain)
            for profile_id, chain in chains.items()
        }
        reached = {
            profile_id: bool(chain)
            and chain[-1].get("state") in successful
            for profile_id, chain in chains.items()
        }
        a, b = (profile["profile_id"] for profile in PROFILES)
        if complete[a] and complete[b]:
            counts["both_profiles_completed_conversation"] += 1
        elif complete[a]:
            counts["only_grok_4_3_completed"] += 1
        elif complete[b]:
            counts["only_grok_4_6_completed"] += 1
        else:
            counts["neither_completed"] += 1
        if reached[a] and reached[b]:
            counts["both_reached_deepest_target"] += 1
        elif reached[a] or reached[b]:
            counts["one_reached_deepest_target"] += 1
        if base is not None and reached[a] and reached[b]:
            final_a = chains[a][-1]
            final_b = chains[b][-1]
            da = _load_json(
                _turn_artifact_path(base, final_a, "semantic-diagnostics.json"),
                "final diagnostics A",
            )
            db = _load_json(
                _turn_artifact_path(base, final_b, "semantic-diagnostics.json"),
                "final diagnostics B",
            )
            if da.get("structural_signals") != db.get("structural_signals"):
                structural_disagreements += 1
            ledger_size_differences.append(
                int(db["ledger_canonical_byte_size"])
                - int(da["ledger_canonical_byte_size"])
            )
        per_conversation.append(
            {
                "pilot_conversation_id": conversation,
                "complete": complete,
                "reached_deepest_target": reached,
            }
        )
    metrics = profile_metrics or derive_profile_metrics(call_ledger, output_dir)
    metric_profiles = metrics.get("profiles", {})
    a_metrics = metric_profiles.get(PROFILES[0]["profile_id"], {})
    b_metrics = metric_profiles.get(PROFILES[1]["profile_id"], {})
    paired_counts = {
        key: counts[key]
        for key in (
            "both_profiles_completed_conversation",
            "only_grok_4_3_completed",
            "only_grok_4_6_completed",
            "neither_completed",
            "both_reached_deepest_target",
            "one_reached_deepest_target",
        )
    }
    return {
        "artifact_evidence": "deterministic derivation",
        **paired_counts,
        "structural_diagnostic_disagreement_count": structural_disagreements,
        "ledger_size_differences_grok_4_6_minus_grok_4_3_bytes": (
            ledger_size_differences
        ),
        "token_difference_grok_4_6_minus_grok_4_3": int(
            b_metrics.get("total_tokens", 0)
        )
        - int(a_metrics.get("total_tokens", 0)),
        "latency_median_difference_grok_4_6_minus_grok_4_3_seconds": (
            None
            if a_metrics.get("latency_seconds", {}).get("median") is None
            or b_metrics.get("latency_seconds", {}).get("median") is None
            else round(
                float(b_metrics["latency_seconds"]["median"])
                - float(a_metrics["latency_seconds"]["median"]),
                6,
            )
        ),
        "conversation_results": per_conversation,
        "semantic_winner_selected": False,
    }


def derive_phase2a_disposition(call_ledger: Mapping[str, Any]) -> str:
    """Derive the bounded top-level disposition from durable evidence only."""

    entries = list(call_ledger.get("entries", []))
    attempted = sum(int(item.get("provider_call_count", 0)) for item in entries)
    states = {str(item.get("state")) for item in entries}
    if "uncertain_after_send" in states:
        return "phase2a_development_pilot_uncertain_after_send"
    if attempted == 0:
        return "phase2a_development_pilot_blocked_before_calls"
    if "not_attempted_due_to_global_failure" in states:
        return "phase2a_development_pilot_inconclusive_operational_failure"
    success = {"validated_and_materialised", "validated_with_diagnostic_flags"}
    by_profile = {
        profile["profile_id"]: [
            item
            for item in entries
            if item.get("profile_id") == profile["profile_id"]
        ]
        for profile in PROFILES
    }
    if all(
        all(item.get("state") in success for item in profile_entries)
        for profile_entries in by_profile.values()
    ):
        return "phase2a_development_pilot_completed_review_pending"
    return "phase2a_development_pilot_completed_with_profile_attrition_review_pending"


def _sample_planning_record(
    call_ledger: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build development-only paired scenarios without estimating an effect."""

    completion = None
    reviewable_count = None
    complete_paired_count = None
    if call_ledger is not None:
        success = {"validated_and_materialised", "validated_with_diagnostic_flags"}
        grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
        for item in call_ledger.get("entries", []):
            grouped[
                (str(item.get("pilot_conversation_id")), str(item.get("profile_id")))
            ].append(item)
        conversations = sorted({key[0] for key in grouped})
        reviewable_count = 0
        complete_paired_count = 0
        for conversation in conversations:
            chains = [
                sorted(
                    grouped.get((conversation, profile["profile_id"]), []),
                    key=lambda item: int(item.get("turn_index", -1)),
                )
                for profile in PROFILES
            ]
            if any(chain and chain[-1].get("state") in success for chain in chains):
                reviewable_count += 1
            if all(chain and all(item.get("state") in success for item in chain) for chain in chains):
                complete_paired_count += 1
        completion = (
            round(complete_paired_count / SELECTED_CONVERSATION_COUNT, 6)
            if conversations
            else None
        )
    scenarios = []
    for absolute_difference in (0.10, 0.15, 0.20):
        rows = []
        for discordant_fraction in (0.20, 0.30, 0.40):
            approximate = math.ceil(
                ((1.959964 + 0.841621) ** 2 * discordant_fraction)
                / (absolute_difference**2)
            )
            rows.append(
                {
                    "assumed_discordant_pair_fraction": discordant_fraction,
                    "approximate_complete_paired_conversations": approximate,
                    "cluster_sensitivity_at_1_25": math.ceil(approximate * 1.25),
                    "operational_inflation_using_pilot_completion": (
                        math.ceil(approximate / completion)
                        if completion not in {None, 0}
                        else None
                    ),
                }
            )
        scenarios.append(
            {
                "plausible_absolute_difference": absolute_difference,
                "scenario_rows": rows,
            }
        )
    return {
        "artifact_evidence": "deterministic derivation",
        "planning_version": "phase2a-development-sample-planning-v1",
        "primary_independent_unit": "conversation",
        "clustering_sensitivity_unit": "contributor group",
        "method": "normal-approximation McNemar planning scenarios; not an effect estimate",
        "pilot_operational_completion_fraction": completion,
        "pilot_reviewable_conversation_count": reviewable_count,
        "pilot_reviewable_conversation_fraction": (
            round(reviewable_count / SELECTED_CONVERSATION_COUNT, 6)
            if reviewable_count is not None
            else None
        ),
        "pilot_complete_paired_conversation_count": complete_paired_count,
        "pilot_selected_conversation_denominator": (
            SELECTED_CONVERSATION_COUNT if call_ledger is not None else None
        ),
        "scenarios": scenarios,
        "sealed_clean_prefix_aggregate_count": 2,
        "sealed_cases_used_in_calculation": 0,
        "limitations": [
            "Eight development conversations cannot establish effectiveness.",
            "The two sealed clean prefixes are not a usable held-out evaluation.",
            "No held-out split is selected.",
            "Prospective collection must continue before a credible held-out result.",
            "Human review of this pilot must precede profile selection or prompt revision.",
        ],
        "ledger_effectiveness_established": False,
        "model_profile_selected": False,
    }


def _sample_planning_markdown(record: Mapping[str, Any]) -> str:
    """Render the bounded scenario table without case-level information."""

    lines = [
        "# Phase 2A development-only sample planning",
        "",
        "Conversation is the primary independent unit; contributor group is a clustering sensitivity unit.",
        "These are scenarios, not a pilot effect estimate.",
        "",
        "| Absolute difference | Discordant fraction | Complete pairs | 1.25 cluster sensitivity | Operationally inflated |",
        "|---:|---:|---:|---:|---:|",
    ]
    for scenario in record["scenarios"]:
        for row in scenario["scenario_rows"]:
            lines.append(
                "| {0:.0%} | {1:.0%} | {2} | {3} | {4} |".format(
                    scenario["plausible_absolute_difference"],
                    row["assumed_discordant_pair_fraction"],
                    row["approximate_complete_paired_conversations"],
                    row["cluster_sensitivity_at_1_25"],
                    row["operational_inflation_using_pilot_completion"]
                    if row["operational_inflation_using_pilot_completion"] is not None
                    else "not estimable",
                )
            )
    lines.extend(["", *[f"- {item}" for item in record["limitations"]], ""])
    return "\n".join(lines)


def build_sample_planning(output: str | Path) -> dict[str, Any]:
    """Write the deterministic development-only planning artefacts offline."""

    output_dir = _private_directory(output)
    ledger = _load_json(output_dir / "call-ledger.json", "call ledger")
    record = _sample_planning_record(ledger)
    _write_json(output_dir / "sample-planning.json", record)
    _atomic_write(
        output_dir / "sample-planning.md",
        _sample_planning_markdown(record).encode("utf-8"),
    )
    return record


def _ledger_projection(ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Project a final ledger concisely for blinded exploratory review."""

    def project(collection: str, fields: Sequence[str]) -> list[dict[str, Any]]:
        return [
            {field: copy.deepcopy(item.get(field)) for field in fields if field in item}
            for item in ledger.get(collection, [])
            if isinstance(item, Mapping)
        ]

    return {
        "as_of_turn_index": ledger.get("as_of_turn_index"),
        "propositions": project(
            "propositions",
            (
                "proposition_id",
                "canonical_text",
                "proposition_kind",
                "polarity",
                "modality",
                "speaker_or_attributor",
                "commitment_status",
                "status",
            ),
        ),
        "proposition_groups": project(
            "proposition_groups",
            ("proposition_group_id", "group_type", "member_proposition_ids"),
        ),
        "issues": project(
            "issue_states", ("issue_id", "question", "status", "target_proposition_ids")
        ),
        "commitments": project(
            "participant_commitments",
            ("commitment_id", "participant_id", "proposition_id", "status"),
        ),
        "obligations": project(
            "conversational_obligations",
            ("obligation_id", "owed_by_participant", "owed_to_participant", "status"),
        ),
        "relations": project(
            "proposition_relations",
            ("relation_id", "relation_type", "source_proposition_id", "target_proposition_id"),
        ),
        "answer_targets": project(
            "answer_targets", ("answer_target_id", "status", "target_proposition_ids")
        ),
        "rejected_answer_targets": project(
            "rejected_answer_targets",
            ("rejected_answer_target_id", "status", "target_proposition_ids"),
        ),
        "repair_records": project(
            "repair_records", ("repair_id", "repair_type", "status")
        ),
        "unresolved_item_count": len(ledger.get("unresolved_items", [])),
        "resolved_item_count": len(ledger.get("resolved_items", [])),
        "warning_count": len(ledger.get("warnings", [])),
        "extraction_status": copy.deepcopy(ledger.get("extraction_status")),
    }


def _review_score_form(rubric: Mapping[str, Any]) -> dict[str, Any]:
    """Create an empty score form from the tracked rubric."""

    criteria = rubric.get("criteria")
    if isinstance(criteria, Mapping):
        names = list(criteria)
    elif isinstance(criteria, list):
        names = [
            str(item.get("criterion_id") or item.get("id") or item)
            if isinstance(item, Mapping)
            else str(item)
            for item in criteria
        ]
    else:
        names = [
            "proposition_completeness",
            "invented_or_unsupported_propositions",
            "speaker_and_attribution_accuracy",
            "polarity_and_modality",
            "participant_commitments",
            "compound_decomposition",
            "issue_state_accuracy",
            "open_question_accuracy",
            "conversational_obligations",
            "corrections_and_concessions",
            "answer_target_accuracy",
            "rejected_target_accuracy",
            "live_proposition_retention",
            "proposition_substitution",
            "ignored_distinctions",
            "incremental_consistency",
            "overall_preferred_ledger",
            "fatal_defect",
            "reviewer_confidence",
            "notes",
        ]
    return {
        "artifact_evidence": "human-review template",
        "scores": {name: None for name in names},
        "review_status": "unscored",
    }


def _tree_hash(root: Path, *, exclude: frozenset[str] = frozenset()) -> str:
    """Hash a directory from relative names and file hashes, excluding mappings."""

    records = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise PilotError("symlink found in private output tree")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            if relative in exclude:
                continue
            records.append(
                {"path": relative, "sha256": sha256_bytes(_read_bytes(path, relative))}
            )
    return value_sha256(records)


def _verify_blinded_review_pack(review_dir: Path) -> None:
    """Reject model, operational, audit-label, or unblinding data in blind files."""

    forbidden_exact_values = {
        "xai",
        *(str(profile["profile_id"]).lower() for profile in PROFILES),
        *(str(profile["model"]).lower() for profile in PROFILES),
    }
    forbidden_keys = {
        "profile_id",
        "model",
        "provider",
        "provider_usage",
        "usage",
        "cost",
        "cost_in_usd_ticks",
        "latency",
        "latency_seconds",
        "call_order",
        "profile_order",
        "stressor",
        "stressor_tags",
        "prior_audit_label",
        "prior_audit_labels",
        "unblinding",
        "unblinding_mapping",
        "historical_production_outcome",
    }

    def inspect(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                normalised = str(key).lower()
                if (
                    normalised in forbidden_keys
                    or normalised.startswith("provider_usage")
                    or normalised.startswith("stressor_")
                    or normalised.startswith("prior_audit")
                    or normalised.startswith("call_order")
                    or normalised.startswith("unblinding")
                    or normalised.startswith("latency_")
                    or normalised.startswith("cost_")
                ):
                    raise PilotError("blinded review pack contains forbidden metadata")
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)
        elif isinstance(value, str) and value.lower() in forbidden_exact_values:
            raise PilotError("blinded review pack contains a model/provider identity")

    for path in sorted(review_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(review_dir).as_posix().lower()
        if relative == "unblinding.json":
            continue
        if path.name.lower() == "unblinding.json":
            raise PilotError("unexpected nested unblinding file in review pack")
        if any(value in relative for value in forbidden_exact_values):
            raise PilotError("blinded review pack filename discloses model identity")
        raw = _read_bytes(path, "blinded review artefact")
        try:
            value = live_probe.strict_json_loads(raw)
        except live_probe.StrictJSONError as exc:
            raise PilotError("blinded review artefact is not strict JSON") from exc
        inspect(value)


def build_review_pack(output: str | Path) -> dict[str, Any]:
    """Build an unscored, deterministically blinded review pack offline."""

    prepared = _load_prepared(output)
    output_dir = prepared["output_dir"]
    ledger = _load_json(output_dir / "call-ledger.json", "call ledger")
    review_dir = _ensure_private_subdir(output_dir / "human-review-pack")
    tracked = _tracked_input_record()
    successful = {"validated_and_materialised", "validated_with_diagnostic_flags"}
    entries_by_case_profile: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for entry in ledger["entries"]:
        entries_by_case_profile[
            (str(entry["development_case_id"]), str(entry["profile_id"]))
        ].append(entry)
    index_cases = []
    unblinding: dict[str, Any] = {
        "artifact_evidence": "deterministic derivation",
        "mapping_version": "phase2a-review-unblinding-v1",
        "cases": {},
    }
    for case in prepared["manifest"]["cases"]:
        available: list[tuple[dict[str, Any], Mapping[str, Any], Mapping[str, Any]]] = []
        for profile in PROFILES:
            chain = sorted(
                entries_by_case_profile.get(
                    (case["development_case_id"], profile["profile_id"]), []
                ),
                key=lambda item: int(item["turn_index"]),
            )
            if chain and chain[-1].get("state") in successful:
                final_ledger = _load_json(
                    _turn_artifact_path(output_dir, chain[-1], "materialised-ledger.json"),
                    "final review ledger",
                )
                available.append((profile, chain[-1], final_ledger))
        if not available:
            continue
        review_case_id = str(case["development_case_id"])
        parity = int(
            sha256_bytes(
                f"{prepared['freeze_sha256']}:{case['pilot_conversation_id']}:review".encode(
                    "utf-8"
                )
            )[-1],
            16,
        ) & 1
        if parity:
            available.reverse()
        labels = [f"Ledger {chr(65 + index)}" for index in range(len(available))]
        outputs = []
        mapping = {}
        for label, (profile, final_entry, final_ledger) in zip(labels, available):
            chain = sorted(
                entries_by_case_profile[
                    (case["development_case_id"], profile["profile_id"])
                ],
                key=lambda item: int(item["turn_index"]),
            )
            changes = []
            for entry in chain:
                diagnostic_path = _turn_artifact_path(
                    output_dir, entry, "semantic-diagnostics.json"
                )
                if diagnostic_path.exists():
                    diagnostic = _load_json(diagnostic_path, "review turn diagnostic")
                    changes.append(
                        {
                            "turn_index": entry["turn_index"],
                            "validation_status": entry["state"],
                            "change_counts": diagnostic["change_counts"],
                            "live_issue_count": diagnostic["live_issue_count"],
                            "unresolved_item_count": diagnostic[
                                "unresolved_item_count"
                            ],
                            "resolved_item_count": diagnostic["resolved_item_count"],
                        }
                    )
            outputs.append(
                {
                    "label": label,
                    "validation_status": final_entry["state"],
                    "final_ledger_projection": _ledger_projection(final_ledger),
                    "turn_by_turn_ledger_change_summaries": changes,
                }
            )
            mapping[label] = {
                "profile_id": profile["profile_id"],
                "model": profile["model"],
            }
        case_dir = _ensure_private_subdir(review_dir / review_case_id)
        _write_json(
            case_dir / "review-case.json",
            {
                "artifact_evidence": "human-review template",
                "review_case_id": review_case_id,
                "reconstruction_grade": case["reconstruction_grade"],
                "exact_transcript_prefix": case["exact_private_transcript_prefix"],
                "turn_boundaries": [
                    {
                        "turn_index": turn["turn_index"],
                        "turn_id": turn["turn_id"],
                        "speaker": turn["speaker"]["participant_id"],
                    }
                    for turn in case["exact_private_transcript_prefix"]
                ],
                "ledger_outputs": outputs,
                "historical_reply_after_target_withheld": True,
                "production_outcome_withheld": True,
            },
        )
        _write_json(case_dir / "scoring-form.json", _review_score_form(tracked["rubric"]))
        index_cases.append(
            {
                "review_case_id": review_case_id,
                "available_ledger_labels": labels,
                "scoring_status": "unscored",
            }
        )
        unblinding["cases"][review_case_id] = mapping
    _write_json(
        review_dir / "index.json",
        {
            "artifact_evidence": "human-review template",
            "pack_version": "phase2a-blinded-review-pack-v1",
            "case_count": len(index_cases),
            "cases": index_cases,
            "blinded": True,
            "scored": False,
            "not_arm_d_gold_annotation": True,
        },
    )
    _write_json(review_dir / "unblinding.json", unblinding)
    _verify_blinded_review_pack(review_dir)
    pack_hash = _tree_hash(review_dir, exclude=frozenset({"unblinding.json"}))
    result = {
        "status": "blinded_review_pack_built_unscored",
        "case_count": len(index_cases),
        "blinded_review_pack_sha256": pack_hash,
        "unblinding_sha256": sha256_bytes(
            _read_bytes(review_dir / "unblinding.json", "unblinding mapping")
        ),
        "provider_calls": 0,
    }
    build_sample_planning(output_dir)
    _refresh_summary_after_review(output_dir, result)
    _write_checksums(output_dir)
    return result


def _aggregate_structural_diagnostics(
    output_dir: Path, ledger: Mapping[str, Any]
) -> dict[str, Any]:
    """Aggregate structural signal counts without semantic correctness claims."""

    result: dict[str, Any] = {}
    for profile in PROFILES:
        counts = Counter()
        evaluated = 0
        for entry in ledger.get("entries", []):
            if entry.get("profile_id") != profile["profile_id"]:
                continue
            path = _turn_artifact_path(output_dir, entry, "semantic-diagnostics.json")
            if not path.exists():
                continue
            diagnostic = _load_json(path, "structural diagnostic")
            evaluated += 1
            for signal, present in diagnostic.get("structural_signals", {}).items():
                counts[signal] += int(present is True)
        result[profile["profile_id"]] = {
            "turns_evaluated": evaluated,
            "signal_observation_counts": dict(sorted(counts.items())),
            "semantic_correctness_claimed": False,
        }
    return result


def _update_carry_forward_recurrence(
    output_dir: Path,
    carry: Mapping[str, Any],
    ledger: Mapping[str, Any],
) -> dict[str, Any]:
    """Classify recurrence only where the synthetic invariant is assessable."""

    failed = set(carry.get("exact_failed_invariants", []))
    assessable = {
        "materialised_successfully",
        "extraction_complete",
        "no_unsupported_inference_or_warning",
        "exact_current_turn_evidence",
    }
    if not failed or not failed <= assessable:
        recurrence = "phase1_4_failure_pattern_not_assessable"
    else:
        observed_again = False
        assessed = 0
        for entry in ledger.get("entries", []):
            if entry.get("profile_id") != PROFILES[0]["profile_id"]:
                continue
            path = _turn_artifact_path(output_dir, entry, "validation.json")
            if not path.exists():
                continue
            validation = _load_json(path, "carry-forward validation")
            assessed += 1
            if "materialised_successfully" in failed and validation.get(
                "materialisation_status"
            ) != "passed":
                observed_again = True
            if "exact_current_turn_evidence" in failed and validation.get(
                "evidence_span_status"
            ) != "passed":
                observed_again = True
            parsed_path = _turn_artifact_path(output_dir, entry, "response.parsed.json")
            diagnostic_path = _turn_artifact_path(
                output_dir, entry, "semantic-diagnostics.json"
            )
            if parsed_path.exists():
                parsed = _load_json(parsed_path, "carry-forward parsed response")
                if "extraction_complete" in failed and parsed.get(
                    "extraction_status"
                ) != "complete":
                    observed_again = True
            if diagnostic_path.exists():
                diagnostic = _load_json(diagnostic_path, "carry-forward diagnostic")
                if "no_unsupported_inference_or_warning" in failed and (
                    diagnostic.get("warning_count", 0)
                    or diagnostic.get("unsupported_inference_rejections", 0)
                ):
                    observed_again = True
        if not assessed:
            recurrence = "phase1_4_failure_pattern_not_assessable"
        elif observed_again:
            recurrence = "phase1_4_failure_pattern_observed_again"
        else:
            recurrence = "phase1_4_failure_pattern_not_observed"
    result = copy.deepcopy(dict(carry))
    result["recurrence_result"] = recurrence
    result["recurrence_is_profile_selection_rule"] = False
    _write_json(output_dir / "phase1.4-carry-forward-diagnostic.json", result)
    return result


def _private_report(summary: Mapping[str, Any]) -> str:
    """Render a private aggregate report without case content or unblinding."""

    lines = [
        "# Proposition-ledger Phase 2A development pilot",
        "",
        f"Disposition: `{summary['phase2a_disposition']}`",
        f"Planned provider calls: {summary['planned_provider_calls']}",
        f"Attempted provider calls: {summary['attempted_provider_calls']}",
        "Retries, fallback calls, and repair calls: 0.",
        "",
        "This is an already-exposed development construction pilot. It is not held-out evaluation and does not establish ledger effectiveness.",
        "No model profile is selected and no downstream four-arm experiment is authorised.",
        "",
    ]
    for profile_id, metrics in summary.get("profile_metrics", {}).get(
        "profiles", {}
    ).items():
        lines.extend(
            [
                f"## {profile_id}",
                "",
                f"Attempted: {metrics['attempted_calls']}; definite responses: {metrics['definite_responses']}; complete chains: {metrics['complete_conversation_chain_count']}.",
                f"Strict JSON: {metrics['strict_json_success_count']}; canonical: {metrics['intended_canonical_success_count']}; materialised: {metrics['materialisation_success_count']}.",
                "",
            ]
        )
    return "\n".join(lines)


def _finalize_execution(
    prepared: Mapping[str, Any],
    ledger: Mapping[str, Any],
    protocol_commit: str,
    carry: Mapping[str, Any],
) -> dict[str, Any]:
    """Materialise aggregate operational results after the one live pass."""

    output_dir = prepared["output_dir"]
    metrics = derive_profile_metrics(ledger, output_dir)
    paired = derive_paired_comparison(ledger, metrics, output_dir)
    structural = _aggregate_structural_diagnostics(output_dir, ledger)
    carry_result = _update_carry_forward_recurrence(
        output_dir, carry, ledger
    )
    disposition = derive_phase2a_disposition(ledger)
    summary = {
        "artifact_evidence": "deterministic derivation",
        "phase2a_disposition": disposition,
        "source_commit": SOURCE_COMMIT,
        "protocol_freeze_commit": protocol_commit,
        "protocol_freeze_sha256": prepared["freeze_sha256"],
        "private_case_manifest_sha256": prepared["manifest_sha256"],
        "selected_conversation_count": prepared["manifest"][
            "selected_conversation_count"
        ],
        "selected_unique_turn_count": prepared["manifest"][
            "selected_unique_turn_count"
        ],
        "planned_provider_calls": prepared["plan"]["planned_provider_call_count"],
        "attempted_provider_calls": int(ledger.get("provider_call_count", 0)),
        "automatic_retries": 0,
        "fallback_calls": 0,
        "repair_calls": 0,
        "profile_metrics": metrics,
        "paired_operational_comparison": paired,
        "aggregate_structural_diagnostics": structural,
        "phase1_4_carry_forward": carry_result,
        "ledger_effectiveness_established": False,
        "model_profile_selected": False,
        "downstream_four_arm_pilot_authorised": False,
        "held_out_evaluation_authorised": False,
        "production_integration_authorised": False,
        "prompt_revised": False,
        "review_status": "not_yet_built",
    }
    _write_json(output_dir / "profile-metrics.json", metrics)
    _write_json(output_dir / "paired-operational-comparison.json", paired)
    build_sample_planning(output_dir)
    _write_json(output_dir / "result-summary.json", summary)
    _write_json(
        output_dir / "validation.json",
        {
            "artifact_evidence": "deterministic derivation",
            "status": "post_execution_offline_verification_pending",
            "provider_calls": ledger.get("provider_call_count", 0),
        },
    )
    _atomic_write(
        output_dir / "phase2a-report.md", _private_report(summary).encode("utf-8")
    )
    run_manifest = _load_json(output_dir / "run-manifest.json", "run manifest")
    run_manifest.update(
        {
            "status": disposition,
            "protocol_freeze_commit": protocol_commit,
            "attempted_provider_calls": ledger.get("provider_call_count", 0),
            "completed_at_utc": utc_now(),
        }
    )
    _write_json(output_dir / "run-manifest.json", run_manifest)
    return summary


def _refresh_summary_after_review(
    output_dir: Path, review_result: Mapping[str, Any]
) -> None:
    """Attach aggregate blinded-pack hashes without recording unblinding data."""

    summary_path = output_dir / "result-summary.json"
    if summary_path.exists():
        summary = _load_json(summary_path, "result summary")
    else:
        ledger = _load_json(output_dir / "call-ledger.json", "call ledger")
        summary = {
            "artifact_evidence": "deterministic derivation",
            "phase2a_disposition": derive_phase2a_disposition(ledger),
            "ledger_effectiveness_established": False,
            "model_profile_selected": False,
            "downstream_four_arm_pilot_authorised": False,
            "held_out_evaluation_authorised": False,
            "production_integration_authorised": False,
        }
    summary.update(
        {
            "review_status": "blinded_unscored_review_pack_built",
            "blinded_review_pack_case_count": review_result["case_count"],
            "blinded_review_pack_sha256": review_result[
                "blinded_review_pack_sha256"
            ],
        }
    )
    _write_json(summary_path, summary)


def _write_checksums(output_dir: Path) -> None:
    """Atomically write a complete SHA256SUMS inventory of private files."""

    lines = []
    for path in sorted(output_dir.rglob("*")):
        if path.is_symlink():
            raise PilotError("symlink found while writing checksums")
        if not path.is_file() or path.name == "SHA256SUMS":
            continue
        relative = path.relative_to(output_dir).as_posix()
        lines.append(f"{sha256_bytes(_read_bytes(path, relative))}  {relative}\n")
    _atomic_write(output_dir / "SHA256SUMS", "".join(lines).encode("utf-8"))


def verify_checksums(output_dir: str | Path) -> dict[str, Any]:
    """Verify every declared checksum and the exact non-symlink inventory."""

    root = _private_directory(output_dir)
    checksum_path = root / "SHA256SUMS"
    raw = _read_bytes(checksum_path, "SHA256SUMS")
    declared: dict[str, str] = {}
    for line in raw.decode("utf-8", errors="strict").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([^\n]+)", line)
        if not match or match.group(2) in declared:
            raise PilotError("invalid SHA256SUMS record")
        relative = match.group(2)
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise PilotError("unsafe SHA256SUMS path")
        declared[relative] = match.group(1)
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    }
    if set(declared) != actual:
        raise PilotError("SHA256SUMS inventory mismatch")
    for relative, expected in declared.items():
        if sha256_bytes(_read_bytes(root / relative, relative)) != expected:
            raise PilotError(f"SHA-256 mismatch: {relative}")
    return {"status": "passed", "verified_file_count": len(declared)}


def _compare_saved_json(path: Path, expected: Any, label: str) -> None:
    """Require a generated JSON artefact to match deterministic bytes exactly."""

    if not path.exists():
        raise PilotError(f"missing saved {label}")
    if _read_bytes(path, label) != pretty_json_bytes(expected):
        raise PilotError(f"saved {label} differs from deterministic regeneration")


def _audit_private_permissions(output_dir: Path) -> dict[str, Any]:
    """Require mode 0700 directories, mode 0600 files, and no symlinks."""

    directories = 0
    files = 0
    for path in [output_dir, *sorted(output_dir.rglob("*"))]:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise PilotError("symlink found in private run")
        if stat.S_ISDIR(metadata.st_mode):
            directories += 1
            if stat.S_IMODE(metadata.st_mode) != 0o700:
                raise PilotError(f"private directory mode is not 0700: {path.name}")
        elif stat.S_ISREG(metadata.st_mode):
            files += 1
            if stat.S_IMODE(metadata.st_mode) != 0o600:
                raise PilotError(f"private file mode is not 0600: {path.name}")
        else:
            raise PilotError("non-regular object found in private run")
    return {"status": "passed", "directory_count": directories, "file_count": files}


def _privacy_audit(output_dir: Path) -> dict[str, Any]:
    """Audit provider/tracked outputs without reading or fingerprinting credentials."""

    mapping = _load_json(output_dir / "private-source-id-map.json", "source ID map")
    source_identifiers: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key.startswith("source_") and isinstance(child, str) and child:
                    source_identifiers.add(child)
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(mapping)
    forbidden_files_checked = 0
    source_id_leaks = 0
    production_path_leaks = 0
    credential_serialisation_markers = 0
    exempt_prefixes = (
        "private-source-id-map.json",
        "exposed-development-candidate-index.jsonl",
    )
    # Exact visible transcript text may naturally contain public URLs/IDs; source
    # identity scanning therefore targets provider/request and aggregate artefacts.
    identity_scan_names = {
        "request-metadata.json",
        "prompt-manifest.json",
        "result-summary.json",
        "profile-metrics.json",
        "paired-operational-comparison.json",
        "phase2a-report.md",
    }
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.name == "SHA256SUMS":
            continue
        relative = path.relative_to(output_dir).as_posix()
        if relative.startswith(exempt_prefixes):
            continue
        raw = _read_bytes(path, f"privacy scan {relative}")
        forbidden_files_checked += 1
        if b"/disks/disk1/etc/" in raw or b".zfs/snapshot" in raw:
            production_path_leaks += 1
        if re.search(
            rb'(?i)(authorization|bearer|api[_-]?key|credential|password|secret)["\s]*:["\s]+[^\s",}]+',
            raw,
        ):
            credential_serialisation_markers += 1
        if path.name in identity_scan_names:
            text = raw.decode("utf-8", errors="ignore")
            source_id_leaks += sum(identifier in text for identifier in source_identifiers)
    tracked_files = [
        PROTOCOL_MD_PATH,
        PROTOCOL_JSON_PATH,
        SYSTEM_PROMPT_PATH,
        REVIEW_RUBRIC_PATH,
    ]
    if TRACKED_FREEZE_PATH.exists():
        tracked_files.append(TRACKED_FREEZE_PATH)
    for path in tracked_files:
        raw = _read_bytes(path, f"tracked privacy scan {path.name}")
        if re.search(rb"conversation-[0-9a-f]{16,64}", raw) or re.search(
            rb"\b[0-9]{15,22}\b", raw
        ):
            source_id_leaks += 1
        if b"/disks/" in raw:
            production_path_leaks += 1
    if source_id_leaks or production_path_leaks or credential_serialisation_markers:
        raise PilotError("privacy or credential scan failed")
    return {
        "status": "passed",
        "files_scanned": forbidden_files_checked,
        "source_identity_leak_count": 0,
        "production_path_leak_count": 0,
        "credential_serialisation_marker_count": 0,
        "api_key_value_read_hashed_measured_or_serialised": False,
        "protected_substantive_content_leak_count": 0,
    }


def _verify_state_history(call_ledger: Mapping[str, Any]) -> None:
    """Validate durable state transitions, attempt counts, and no retries."""

    allowed = {
        "planned": {
            "sending",
            "blocked_by_prior_turn_failure",
            "not_attempted_due_to_global_failure",
        },
        "sending": {
            "response_received",
            "provider_error_received",
            "uncertain_after_send",
        },
        "response_received": {
            "strict_validation_failed",
            "materialisation_failed",
            "validated_and_materialised",
            "validated_with_diagnostic_flags",
        },
    }
    attempts = 0
    for entry in call_ledger.get("entries", []):
        history = entry.get("state_history", [])
        states = [item.get("state") for item in history]
        if not states or states[0] != "planned" or states[-1] != entry.get("state"):
            raise PilotError("call state history endpoint mismatch")
        for before, after in zip(states, states[1:]):
            if after not in allowed.get(str(before), set()):
                raise PilotError("illegal durable call state history")
        attempt = int(entry.get("attempt_number", 0))
        count = int(entry.get("provider_call_count", 0))
        if attempt not in {0, 1} or count not in {0, 1} or attempt != count:
            raise PilotError("call attempt/retry count mismatch")
        attempts += count
        if count and "sending" not in states:
            raise PilotError("attempted call lacks durable sending state")
    if attempts != call_ledger.get("provider_call_count"):
        raise PilotError("call-ledger provider-call count mismatch")
    if attempts > call_ledger.get("planned_provider_call_count", MAX_PROVIDER_CALL_BUDGET):
        raise PilotError("attempt count exceeds frozen plan")
    if call_ledger.get("automatic_retry_count") != 0:
        raise PilotError("automatic retry count is not zero")


def verify_only(output: str | Path) -> dict[str, Any]:
    """Reprocess all saved bytes and verify determinism, seals, privacy, and hashes."""

    prepared = _load_prepared(output)
    output_dir = prepared["output_dir"]
    preexisting_checksum_result = None
    if (output_dir / "SHA256SUMS").exists():
        # Verify prior evidence before writing any derived verification record;
        # never bless tampering by regenerating the inventory first.
        preexisting_checksum_result = verify_checksums(output_dir)
    ledger = _load_json(output_dir / "call-ledger.json", "call ledger")
    _verify_state_history(ledger)
    tracked = _tracked_input_record()
    for key, expected in {
        "canonical_schema_sha256": EXPECTED_CANONICAL_SCHEMA_SHA256,
        "provider_schema_sha256": EXPECTED_PROVIDER_SCHEMA_SHA256,
        "system_prompt_sha256": tracked["hashes"]["system_prompt_sha256"],
        "review_rubric_sha256": tracked["hashes"]["review_rubric_sha256"],
        "development_protocol_sha256": tracked["hashes"]["protocol_sha256"],
    }.items():
        if prepared["freeze"].get(key) != expected:
            raise PilotError(f"frozen tracked-input hash mismatch: {key}")
    canonical, provider_schema, persisted_schema = _load_schemas()
    cases = _case_by_id(prepared["manifest"])
    reprocessed = 0
    successful_states = {
        "validated_and_materialised",
        "validated_with_diagnostic_flags",
    }
    chain_prior: dict[tuple[str, str], dict[str, Any] | None] = {}
    for entry in ledger["entries"]:
        state = entry["state"]
        chain_key = (str(entry["development_case_id"]), str(entry["profile_id"]))
        if int(entry["turn_index"]) == 0:
            chain_prior[chain_key] = None
        if state not in (
            successful_states
            | {
                "strict_validation_failed",
                "materialisation_failed",
                "provider_error_received",
            }
        ):
            continue
        case = cases[str(entry["development_case_id"])]
        turn = _turn_by_id(case)[str(entry["pilot_turn_id"])]
        prior = chain_prior.get(chain_key)
        payload = build_turn_payload(
            case,
            turn,
            prior,
            protocol_freeze_sha256=prepared["freeze_sha256"],
            provider_schema=provider_schema,
        )
        payload_hash = sha256_bytes(canonical_json_bytes(payload))
        prior_hash = prior.get("ledger_sha256") if prior is not None else "genesis"
        expected_identity = value_sha256(
            {
                "protocol_freeze_sha256": prepared["freeze_sha256"],
                "case_manifest_sha256": prepared["manifest_sha256"],
                "pilot_conversation_id": case["pilot_conversation_id"],
                "pilot_turn_id": turn["turn_id"],
                "profile_id": entry["profile_id"],
                "current_turn_payload_hash": payload_hash,
                "prior_ledger_hash_or_genesis_marker": prior_hash,
                "provider_schema_sha256": EXPECTED_PROVIDER_SCHEMA_SHA256,
                "system_prompt_sha256": tracked["hashes"]["system_prompt_sha256"],
            }
        )
        if (
            entry.get("actual_payload_sha256") != payload_hash
            or entry.get("prior_ledger_sha256") != prior_hash
            or entry.get("call_identity") != expected_identity
        ):
            raise PilotError("durable call identity or payload binding mismatch")
        profile = PROFILE_BY_ID[str(entry["profile_id"])]
        request_repr = build_request_representation(
            profile,
            payload,
            system_prompt=tracked["system_prompt"],
            provider_schema=provider_schema,
        )
        expected_request_metadata = _request_metadata_record(
            entry=entry,
            case=case,
            turn=turn,
            profile=profile,
            call_identity=expected_identity,
            payload_hash=payload_hash,
            prior_hash=prior_hash,
            request_repr=request_repr,
            system_prompt_sha256=tracked["hashes"]["system_prompt_sha256"],
        )
        _compare_saved_json(
            _turn_artifact_path(output_dir, entry, "request-metadata.json"),
            expected_request_metadata,
            "request metadata",
        )
        if state == "provider_error_received":
            provider_error = _load_json(
                _turn_artifact_path(output_dir, entry, "validation.json"),
                "provider error validation",
            )
            if any(
                provider_error.get(key) != expected
                for key, expected in {
                    "artifact_evidence": "provider observation",
                    "status": "provider_error_received",
                    "provider_error_class": entry.get("provider_error_class"),
                    "provider_error_code": entry.get("provider_error_code"),
                    "sanitised_bounded_message": entry.get(
                        "sanitised_bounded_message"
                    ),
                }.items()
            ) or not isinstance(provider_error.get("latency_seconds"), (int, float)):
                raise PilotError("saved provider error differs from durable call ledger")
            continue
        raw = _read_bytes(
            _turn_artifact_path(output_dir, entry, "response.raw.txt"),
            "saved raw provider response",
        )
        if entry.get("raw_response_sha256") != sha256_bytes(raw):
            raise PilotError("saved raw response SHA-256 differs from call ledger")
        processed = process_response_twice(
            raw=raw,
            case=case,
            turn=turn,
            prior_ledger=prior,
            canonical_schema=canonical,
            provider_schema=provider_schema,
            persisted_ledger_schema=persisted_schema,
        )
        _compare_saved_json(
            _turn_artifact_path(output_dir, entry, "validation.json"),
            processed["validation"],
            "validation",
        )
        parsed_path = _turn_artifact_path(output_dir, entry, "response.parsed.json")
        if processed.get("parsed") is None:
            if parsed_path.exists():
                raise PilotError("unexpected parsed response after strict failure")
        else:
            _compare_saved_json(parsed_path, processed["parsed"], "parsed response")
        if processed.get("ledger") is not None:
            ledger_path = _turn_artifact_path(
                output_dir, entry, "materialised-ledger.json"
            )
            _compare_saved_json(ledger_path, processed["ledger"], "materialised ledger")
            diagnostic = semantic_diagnostics(
                processed["parsed"],
                processed["ledger"],
                prior,
                turn,
                provider_payload_size=len(canonical_json_bytes(payload)),
            )
            _compare_saved_json(
                _turn_artifact_path(output_dir, entry, "semantic-diagnostics.json"),
                diagnostic,
                "semantic diagnostics",
            )
            chain_prior[chain_key] = processed["ledger"]
        reprocessed += 1
    metrics = derive_profile_metrics(ledger, output_dir)
    paired = derive_paired_comparison(ledger, metrics, output_dir)
    if (output_dir / "profile-metrics.json").exists():
        _compare_saved_json(output_dir / "profile-metrics.json", metrics, "profile metrics")
    if (output_dir / "paired-operational-comparison.json").exists():
        _compare_saved_json(
            output_dir / "paired-operational-comparison.json",
            paired,
            "paired operational comparison",
        )
    planning_path = output_dir / "sample-planning.json"
    if planning_path.exists():
        expected_planning = _sample_planning_record(ledger)
        _compare_saved_json(planning_path, expected_planning, "sample planning")
        if _read_bytes(output_dir / "sample-planning.md", "sample planning markdown") != (
            _sample_planning_markdown(expected_planning).encode("utf-8")
        ):
            raise PilotError("sample-planning Markdown differs from deterministic regeneration")
    seal = _load_json(
        output_dir / "held-out-content-seal-audit.json", "held-out seal audit"
    )
    if seal.get("substantive_seal_breached") is not False:
        raise PilotError("held-out substantive seal was breached")
    permissions = _audit_private_permissions(output_dir)
    privacy = _privacy_audit(output_dir)
    review_count = 0
    review_hash = None
    review_dir = output_dir / "human-review-pack"
    if review_dir.exists():
        _verify_blinded_review_pack(review_dir)
        review_index = _load_json(review_dir / "index.json", "review index")
        review_count = int(review_index["case_count"])
        review_hash = _tree_hash(
            review_dir, exclude=frozenset({"unblinding.json"})
        )
        for case_record in review_index.get("cases", []):
            form = _load_json(
                review_dir
                / str(case_record["review_case_id"])
                / "scoring-form.json",
                "review scoring form",
            )
            if form.get("review_status") != "unscored" or any(
                value is not None for value in form.get("scores", {}).values()
            ):
                raise PilotError("human review form is not empty")
    validation = {
        "artifact_evidence": "deterministic derivation",
        "status": "passed",
        "saved_responses_reprocessed": reprocessed,
        "byte_identical_derived_artefacts": True,
        "call_counts_and_state_transitions_verified": True,
        "held_out_content_seal_verified": True,
        "privacy": privacy,
        "permissions": permissions,
        "review_pack_case_count": review_count,
        "blinded_review_pack_sha256": review_hash,
        "provider_calls": 0,
        "preexisting_checksums_verified_before_writes": (
            preexisting_checksum_result is not None
        ),
    }
    _write_json(output_dir / "validation.json", validation)
    if (output_dir / "result-summary.json").exists():
        summary = _load_json(output_dir / "result-summary.json", "result summary")
        summary["offline_verification_status"] = "passed"
        summary["saved_responses_reprocessed"] = reprocessed
        _write_json(output_dir / "result-summary.json", summary)
    _write_checksums(output_dir)
    checksums = verify_checksums(output_dir)
    return {
        "status": "phase2a_verify_only_passed",
        "saved_responses_reprocessed": reprocessed,
        "checksums": checksums,
        "provider_calls": 0,
    }


def _build_parser() -> argparse.ArgumentParser:
    """Build the four-mode CLI without importing or contacting a provider."""

    parser = argparse.ArgumentParser(
        description="Bounded xAI proposition-ledger Phase 2A development pilot"
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--prepare", action="store_true")
    modes.add_argument("--execute-live-pilot", action="store_true")
    modes.add_argument("--build-review-pack", action="store_true")
    modes.add_argument("--verify-only", action="store_true")
    parser.add_argument("--private-output", required=True)
    parser.add_argument("--confirm-provider-call-budget", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch one bounded CLI mode and print only aggregate safe JSON."""

    args = _build_parser().parse_args(argv)
    try:
        if args.prepare:
            result = prepare_run(args.private_output)
        elif args.execute_live_pilot:
            result = execute_live_pilot(
                args.private_output,
                confirm_provider_call_budget=args.confirm_provider_call_budget,
            )
        elif args.build_review_pack:
            if args.confirm_provider_call_budget is not None:
                raise PilotError("call-budget acknowledgement is live-mode only")
            result = build_review_pack(args.private_output)
        else:
            if args.confirm_provider_call_budget is not None:
                raise PilotError("call-budget acknowledgement is live-mode only")
            result = verify_only(args.private_output)
    except PilotError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(canonical_json_bytes(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
