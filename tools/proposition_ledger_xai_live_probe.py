#!/usr/bin/env python3
"""Bounded synthetic xAI live-schema probe for proposition-ledger Phase 1.4.

Importing this module performs no provider import and no network operation.
The only live path is ``--execute-live-probe`` and it requires an exact budget
acknowledgement.  All other paths are local and synthetic.
"""

from __future__ import annotations

import argparse
import copy
import ctypes
import errno
import fcntl
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from tools import proposition_ledger_semantic_delta as semantic  # noqa: E402
from tools import proposition_ledger_xai_provider_preflight as preflight  # noqa: E402


PHASE14_DIR = PROJECT_DIR / "proposition_ledger_research/phase1_4"
CASE_PATH = PHASE14_DIR / "synthetic-live-probe-case.json"
SYSTEM_PROMPT_PATH = PHASE14_DIR / "synthetic-live-probe-system-prompt.txt"
CANONICAL_SCHEMA_PATH = preflight.CANONICAL_SCHEMA_PATH
PERSISTED_LEDGER_SCHEMA_PATH = semantic.DEFAULT_LEDGER_SCHEMA_PATH
PHASE13_PRIVATE_RUN = Path(
    "/disks/disk1/research/private-runs/"
    "proposition-ledger-phase1.3-xai-regex-correction-20260901T025148Z"
)
PHASE13_RUN_MANIFEST_PATH = PHASE13_PRIVATE_RUN / "run-manifest.json"
PHASE13_LOCK_PATH = PHASE13_PRIVATE_RUN / "sdk-environment-lock.json"
PRIOR_PHASE14_PRIVATE_RUN = Path(
    "/disks/disk1/research/private-runs/"
    "proposition-ledger-phase1.4-xai-live-probe-20260901T070907Z"
)
DIAGNOSIS_PRIVATE_RUN = Path(
    "/disks/disk1/research/private-runs/"
    "proposition-ledger-phase1.4-xai-invalid-argument-diagnosis-20260901T072000Z"
)

SOURCE_COMMIT = "eb59bcc545920b107f17d9a4b3dffa7a31301fa0"
SOURCE_PARENT = "777b40f67793d6140fa3f923186cf737c89042ad"
SOURCE_SUBJECT = "Correct xAI regex compatibility analysis"
CORRECTION_BASE_COMMIT = "39bb70b6f7974623ce13d2fcbf53331e5acbdffc"
ALLOWED_PREPARE_HEADS = frozenset((SOURCE_COMMIT, CORRECTION_BASE_COMMIT))
REQUEST_CONTRACT_REVISION = "phase1.4-no-tools-omit-tool-choice-v2"
PRIOR_PHASE14_PROVIDER_CALL_COUNT = 2
DIAGNOSTIC_PROVIDER_CALL_COUNT = 5
EXPECTED_PROVIDER_SCHEMA_SHA256 = (
    "37423dc87da3a253ee6c3dcc826c764268d094b16ba83bd1d4a9b1e00948bc21"
)
MAX_OUTPUT_TOKENS = 4096
CLIENT_TIMEOUT_SECONDS = 300.0
PROVIDER_CALL_BUDGET = 2
ERROR_MESSAGE_LIMIT = 512
STORE_MESSAGES = False

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

UNRELATED_CREDENTIAL_VARIABLES = tuple(
    name for name in preflight.PROVIDER_KEY_ENV_NAMES if name != "XAI_API_KEY"
)
NO_RETRY_CHANNEL_OPTIONS: tuple[tuple[str, Any], ...] = (
    ("grpc.enable_retries", 0),
    ("grpc.service_config", "{}"),
)
GRPC_STATUS_CODES = frozenset(
    {
        "ABORTED",
        "ALREADY_EXISTS",
        "CANCELLED",
        "DATA_LOSS",
        "DEADLINE_EXCEEDED",
        "FAILED_PRECONDITION",
        "INTERNAL",
        "INVALID_ARGUMENT",
        "NOT_FOUND",
        "OK",
        "OUT_OF_RANGE",
        "PERMISSION_DENIED",
        "RESOURCE_EXHAUSTED",
        "UNAUTHENTICATED",
        "UNAVAILABLE",
        "UNIMPLEMENTED",
        "UNKNOWN",
    }
)
PROVIDER_ERROR_CATEGORIES = frozenset(
    {
        "authentication_failure",
        "authorisation_failure",
        "billing_failure",
        "definite_provider_error",
        "global_transport_failure",
        "model_profile_rejection",
        "rate_limit_or_account_quota_failure",
        "server_schema_rejection",
        "timeout_after_possible_transmission",
        "transport_failure_before_send",
        "unclassified_failure_after_send",
    }
)
REQUIRED_OPERATOR_CHECK_IDS = frozenset(
    {
        "git_cached_diff_check",
        "git_diff_check_post_call",
        "git_diff_check_pre_call",
        "pinned_pytest",
        "prepare_mode",
        "privacy_scan",
        "py_compile",
        "python_documentation_check",
        "sha256sum_check",
        "synthetic_fixtures_check",
        "system_pytest",
        "verify_only",
    }
)

ROOT_COLLECTIONS = (
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
SMOKE_EMPTY_COLLECTIONS = tuple(
    name for name in ROOT_COLLECTIONS if name != "new_propositions"
)
PROVIDER_FORBIDDEN_PERSISTENCE_FIELDS = {
    "ledger_id",
    "ledger_sha256",
    "participant_id",
    "participants",
    "previous_ledger_sha256",
    "proposition_id",
    "source_path",
    "state_patch",
}
CALL_TERMINAL_OR_NONREPEATABLE_STATES = {
    "sending",
    "uncertain_after_send",
    "response_received",
    "provider_error_received",
    "fully_validated",
    "terminal_validation_failure",
}
ALLOWED_STATE_TRANSITIONS = {
    "planned": {"sending", "not_attempted_due_to_global_failure"},
    "sending": {
        "response_received",
        "provider_error_received",
        "uncertain_after_send",
    },
    "response_received": {"fully_validated", "terminal_validation_failure"},
    "provider_error_received": set(),
    "uncertain_after_send": set(),
    "fully_validated": set(),
    "terminal_validation_failure": set(),
    "not_attempted_due_to_global_failure": set(),
}


class ProbeError(RuntimeError):
    """A bounded, credential-free Phase 1.4 failure."""


class StrictJSONError(ProbeError):
    """Strict raw-response parsing failed."""


@dataclass(frozen=True)
class LiveObservation:
    """The deliberately bounded observation retained from one SDK response."""

    raw_text: str
    returned_model_id: str | None
    provider_response_id: str | None
    finish_reason: str | None
    usage: Mapping[str, Any]


@dataclass(frozen=True)
class ErrorClassification:
    """Credential-free classification used for durable stop decisions."""

    category: str
    code: str | None
    message: str
    call_state: str
    profile_disposition: str
    server_schema_acceptance_status: str
    request_definitely_sent: bool
    stops_following_calls: bool


def utc_now() -> str:
    """Return an RFC 3339 UTC timestamp with whole-second precision."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Return the shared deterministic compact JSON representation."""

    return preflight.canonical_json_bytes(value)


def pretty_json_bytes(value: Any) -> bytes:
    """Return the shared deterministic private-artifact JSON representation."""

    return preflight.pretty_json_bytes(value)


def sha256_bytes(value: bytes) -> str:
    """Return a lowercase SHA-256 digest for non-credential bytes."""

    return hashlib.sha256(value).hexdigest()


def _absolute_lexical(path: str | Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _require_real_directory(path: str | Path, label: str) -> Path:
    candidate = _absolute_lexical(path)
    try:
        metadata = candidate.lstat()
    except FileNotFoundError as exc:
        raise ProbeError(f"{label} is missing: {candidate}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ProbeError(f"{label} is not a real directory")
    if candidate.resolve(strict=True) != candidate:
        raise ProbeError(f"{label} has a symlink path component")
    return candidate


def _create_private_directory(path: str | Path, label: str) -> Path:
    candidate = _absolute_lexical(path)
    if os.path.lexists(candidate):
        raise ProbeError(f"{label} already exists: {candidate}")
    _require_real_directory(candidate.parent, f"{label} parent")
    candidate.mkdir(mode=0o700)
    candidate.chmod(0o700)
    return _require_real_directory(candidate, label)


def _create_private_subdirectory(path: Path) -> None:
    _require_real_directory(path.parent, "private subdirectory parent")
    if os.path.lexists(path):
        raise ProbeError(f"private subdirectory already exists: {path}")
    path.mkdir(mode=0o700)
    path.chmod(0o700)


def _read_regular_bytes(path: str | Path, label: str) -> bytes:
    candidate = _absolute_lexical(path)
    _require_real_directory(candidate.parent, f"{label} parent")
    try:
        before = candidate.lstat()
    except FileNotFoundError as exc:
        raise ProbeError(f"{label} is missing") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ProbeError(f"{label} is not a regular non-symlink file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(candidate, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise ProbeError(f"{label} changed while opening")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _atomic_write_private_bytes(path: str | Path, content: bytes) -> None:
    candidate = _absolute_lexical(path)
    parent = _require_real_directory(candidate.parent, "private output parent")
    if os.path.lexists(candidate):
        metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ProbeError(f"refusing unsafe output target: {candidate}")
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
        directory_descriptor = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if os.path.lexists(temporary):
            temporary.unlink()


def _atomic_write_private_json(path: str | Path, value: Any) -> None:
    _atomic_write_private_bytes(path, pretty_json_bytes(value))


def _load_strict_json_file(path: str | Path, label: str) -> Any:
    raw = _read_regular_bytes(path, label)
    try:
        return strict_json_loads(raw)
    except StrictJSONError as exc:
        raise ProbeError(f"invalid strict JSON for {label}: {exc}") from exc


def _duplicate_member_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJSONError(f"duplicate JSON member: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise StrictJSONError(f"non-finite JSON number: {value}")


def strict_json_loads(source: bytes) -> dict[str, Any]:
    """Parse exactly one UTF-8 JSON object without repairs or extraction."""

    try:
        text = source.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise StrictJSONError("invalid UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_duplicate_member_hook,
            parse_constant=_reject_nonfinite,
        )
    except StrictJSONError:
        raise
    except json.JSONDecodeError as exc:
        raise StrictJSONError(f"invalid JSON: {exc.msg}") from exc

    def reject_overflow_numbers(item: Any) -> None:
        if isinstance(item, float) and not math.isfinite(item):
            raise StrictJSONError("non-finite JSON number")
        if isinstance(item, Mapping):
            for child in item.values():
                reject_overflow_numbers(child)
        elif isinstance(item, list):
            for child in item:
                reject_overflow_numbers(child)

    reject_overflow_numbers(value)
    if not isinstance(value, dict):
        raise StrictJSONError("top-level JSON value is not an object")
    return value


def build_user_payload(case: Mapping[str, Any]) -> str:
    """Build the exact deterministic user payload from the tracked case."""

    return canonical_json_bytes(case).decode("utf-8")


def build_message_array(system_prompt: str, user_payload: str) -> list[dict[str, str]]:
    """Build the frozen two-message array in provider order."""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_payload},
    ]


def _expected_case_values() -> dict[str, Any]:
    return {
        "conversation_key": "synthetic:phase1.4-live-schema-probe",
        "target_turn_id": "synthetic-turn-0",
        "turn_index": 0,
        "parent_turn_id": None,
        "visible_text": "The lamp is on.",
        "participant_reference": "participant-contributor-1",
        "speaker_role": "contributor",
    }


def validate_synthetic_case(case: Mapping[str, Any]) -> None:
    """Fail closed unless the tracked case is exactly the frozen synthetic case."""

    expected = _expected_case_values()
    checks = {
        "artifact_status": case.get("artifact_status") == "planned",
        "synthetic": case.get("synthetic") is True,
        "case_id": case.get("case_id") == expected["conversation_key"],
        "conversation_key": case.get("conversation_key")
        == expected["conversation_key"],
        "target_turn_id": case.get("target_turn_id")
        == expected["target_turn_id"],
        "turn_index": case.get("turn_index") == expected["turn_index"],
        "parent_turn_id": case.get("parent_turn_id") is None,
        "post_id": case.get("post_id") is None,
        "visible_text": case.get("visible_text") == expected["visible_text"],
        "offset_unit": case.get("evidence_offset_unit") == "Unicode code points",
    }
    speaker = case.get("speaker")
    checks["speaker"] = isinstance(speaker, Mapping) and dict(speaker) == {
        "participant_reference": expected["participant_reference"],
        "role": expected["speaker_role"],
    }
    text = case.get("visible_text")
    if not isinstance(text, str):
        raise ProbeError("synthetic case visible text is not a string")
    computed_span = {
        "turn_id": expected["target_turn_id"],
        "start_char": 0,
        "end_char": len(text),
        "exact_text": text[0 : len(text)],
    }
    checks["programmatic_evidence_span"] = (
        case.get("exact_evidence_span") == computed_span
    )
    participant = case.get("trusted_current_participant")
    checks["trusted_participant"] = isinstance(participant, Mapping) and dict(
        participant
    ) == {
        "author_key": "synthetic-author-participant-contributor-1",
        "identity_confidence": 1.0,
        "participant_id": expected["participant_reference"],
        "role": expected["speaker_role"],
    }
    instruction = case.get("semantic_instruction")
    required_empty = set(SMOKE_EMPTY_COLLECTIONS)
    checks["semantic_instruction"] = (
        isinstance(instruction, Mapping)
        and instruction.get("unsupported_inferences_rejected") == 0
        and set(instruction.get("required_empty_collections", []))
        == required_empty
        and len(instruction.get("requirements", [])) >= 10
    )
    if not all(checks.values()):
        failures = sorted(name for name, passed in checks.items() if not passed)
        raise ProbeError("synthetic case mismatch: " + ",".join(failures))


def build_materialisation_inputs(
    case: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Derive trusted turn, participant, and genesis inputs from the case."""

    participant = copy.deepcopy(dict(case["trusted_current_participant"]))
    current_turn = {
        "conversation_key": case["conversation_key"],
        "turn_id": case["target_turn_id"],
        "turn_index": case["turn_index"],
        "parent_turn_id": case["parent_turn_id"],
        "post_id": case["post_id"],
        "speaker_id": case["speaker"]["participant_reference"],
        "text": case["visible_text"],
    }
    context_record = case["synthetic_genesis_context"]
    genesis_context = {
        "conversation_key": case["conversation_key"],
        "current_participant": copy.deepcopy(participant),
        "root_post_id": context_record["root_post_id"],
        "source_completeness": copy.deepcopy(context_record["source_completeness"]),
    }
    return current_turn, participant, genesis_context


def _load_and_transform_schemas() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    canonical_raw = _read_regular_bytes(CANONICAL_SCHEMA_PATH, "canonical schema")
    if sha256_bytes(canonical_raw) != preflight.EXPECTED_CANONICAL_SHA256:
        raise ProbeError("canonical schema SHA-256 mismatch")
    canonical = strict_json_loads(canonical_raw)
    if (
        canonical.get("properties", {}).get("schema_version", {}).get("const")
        != preflight.EXPECTED_CANONICAL_VERSION
    ):
        raise ProbeError("canonical schema version mismatch")
    provider, transformations = preflight.transform_provider_schema(canonical)
    provider_hash = preflight.value_sha256(provider)
    if provider_hash != EXPECTED_PROVIDER_SCHEMA_SHA256:
        raise ProbeError("provider schema SHA-256 mismatch")
    kinds: dict[str, int] = {}
    for record in transformations:
        kind = str(record["transformation_kind"])
        kinds[kind] = kinds.get(kind, 0) + 1
    if kinds != {
        "insert_explicit_additional_properties_true": 5,
        "remove_redundant_outer_anchors_for_xai_full_string_pattern": 14,
    } or len(transformations) != 19:
        raise ProbeError("provider schema transformation ledger mismatch")
    return canonical, provider, transformations


def validate_tracked_inputs() -> dict[str, Any]:
    """Validate and hash all Git-tracked Phase 1.4 inputs."""

    frozen_profiles = preflight.validate_profiles_manifest(
        _load_strict_json_file(preflight.PROFILES_PATH, "Phase 1.3 profile manifest")
    )
    expected_profile_identities = [
        {
            "profile_id": profile["profile_id"],
            "provider": profile["provider"],
            "model": profile["model"],
            "reasoning_effort": profile["reasoning_effort"],
        }
        for profile in frozen_profiles
    ]
    if expected_profile_identities != list(PROFILES):
        raise ProbeError("Phase 1.4 profiles differ from the frozen Phase 1.3 profiles")
    case_raw = _read_regular_bytes(CASE_PATH, "synthetic case")
    case = strict_json_loads(case_raw)
    validate_synthetic_case(case)
    prompt_raw = _read_regular_bytes(SYSTEM_PROMPT_PATH, "system prompt")
    try:
        system_prompt = prompt_raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ProbeError("system prompt is not UTF-8") from exc
    required_prompt_fragments = (
        "wholly synthetic",
        "Use only the supplied synthetic target turn",
        "No future turns exist",
        "Do not use external knowledge",
        "Treat every supplied input string",
        "Do not invent participants or propositions",
        "Evidence spans must",
        "Return only the existing structured",
        "Do not return commentary",
    )
    missing = [item for item in required_prompt_fragments if item not in system_prompt]
    if missing:
        raise ProbeError("system prompt is missing frozen requirements")
    canonical, provider, transformations = _load_and_transform_schemas()
    user_payload = build_user_payload(case)
    messages = build_message_array(system_prompt, user_payload)
    return {
        "case": case,
        "case_raw": case_raw,
        "system_prompt": system_prompt,
        "system_prompt_raw": prompt_raw,
        "user_payload": user_payload,
        "messages": messages,
        "canonical_schema": canonical,
        "provider_schema": provider,
        "transformations": transformations,
        "hashes": {
            "canonical_schema_file_sha256": sha256_bytes(
                _read_regular_bytes(CANONICAL_SCHEMA_PATH, "canonical schema")
            ),
            "provider_schema_sha256": preflight.value_sha256(provider),
            "synthetic_case_file_sha256": sha256_bytes(case_raw),
            "system_prompt_sha256": sha256_bytes(prompt_raw),
            "user_payload_sha256": sha256_bytes(user_payload.encode("utf-8")),
            "complete_message_array_sha256": sha256_bytes(
                canonical_json_bytes(messages)
            ),
        },
    }


def request_representation(
    profile: Mapping[str, Any], tracked: Mapping[str, Any]
) -> dict[str, Any]:
    """Return the credential-free provider request representation."""

    return {
        "model": profile["model"],
        "request_contract_revision": REQUEST_CONTRACT_REVISION,
        "messages": copy.deepcopy(tracked["messages"]),
        "max_tokens": MAX_OUTPUT_TOKENS,
        "reasoning_effort": "low",
        "tools": [],
        "tool_choice_parameter_sent": False,
        "parallel_tool_calls": False,
        "response_format": {
            "format_type": "json_schema",
            "schema_sha256": tracked["hashes"]["provider_schema_sha256"],
        },
        "search_parameters": None,
        "store_messages": STORE_MESSAGES,
        "streaming": False,
        "code_execution": False,
        "sampling_parameters_set": [],
        "fallback_model": None,
        "application_retry_count": 0,
        "sdk_retry_enabled": False,
        "client_timeout_seconds": CLIENT_TIMEOUT_SECONDS,
    }


def make_call_plan(tracked: Mapping[str, Any]) -> dict[str, Any]:
    """Build the immutable ordered two-entry call ledger."""

    entries: list[dict[str, Any]] = []
    for order, profile in enumerate(PROFILES, start=1):
        identity_material = {
            "profile_id": profile["profile_id"],
            "model": profile["model"],
            "provider_schema_sha256": tracked["hashes"][
                "provider_schema_sha256"
            ],
            "system_prompt_sha256": tracked["hashes"]["system_prompt_sha256"],
            "user_payload_sha256": tracked["hashes"]["user_payload_sha256"],
            "request_contract_revision": REQUEST_CONTRACT_REVISION,
        }
        entries.append(
            {
                "order": order,
                "profile_id": profile["profile_id"],
                "provider": "xAI",
                "model": profile["model"],
                "request_contract_revision": REQUEST_CONTRACT_REVISION,
                "call_identity": sha256_bytes(canonical_json_bytes(identity_material)),
                "state": "planned",
                "attempt_number": 0,
                "provider_call_count": 0,
                "started_at_utc": None,
                "completed_at_utc": None,
            }
        )
    return {
        "artifact_evidence": "derived",
        "ledger_format": "proposition-ledger-phase1.4-call-ledger-v1",
        "provider_call_budget": PROVIDER_CALL_BUDGET,
        "provider_call_count": 0,
        "entries": entries,
    }


def _verify_request_equivalence(
    representations: Sequence[Mapping[str, Any]],
) -> None:
    if len(representations) != 2:
        raise ProbeError("exactly two request representations are required")
    normalized: list[dict[str, Any]] = []
    for representation in representations:
        item = copy.deepcopy(dict(representation))
        item.pop("model", None)
        normalized.append(item)
    if canonical_json_bytes(normalized[0]) != canonical_json_bytes(normalized[1]):
        raise ProbeError("local requests differ by more than model ID")


def verify_pinned_environment() -> dict[str, Any]:
    """Verify the selected interpreter, exact lock, wheelhouse, and SDK sources."""

    run_manifest = _load_strict_json_file(
        PHASE13_RUN_MANIFEST_PATH, "Phase 1.3 run manifest"
    )
    selected_python = run_manifest.get("python_executable_path")
    if not isinstance(selected_python, str) or not selected_python:
        raise ProbeError("Phase 1.3 selected Python path is absent")
    if _absolute_lexical(sys.executable) != _absolute_lexical(selected_python):
        raise ProbeError("current interpreter is not the selected Phase 1.3 interpreter")
    lock_raw = _read_regular_bytes(PHASE13_LOCK_PATH, "Phase 1.3 environment lock")
    lock = strict_json_loads(lock_raw)
    record = preflight._environment_record(lock, PHASE13_PRIVATE_RUN)
    if record.get("lock_matches_environment") is not True:
        failed = sorted(
            key for key, passed in record.get("checks", {}).items() if not passed
        )
        raise ProbeError("pinned environment mismatch: " + ",".join(failed))
    environment: dict[str, str] = {}
    for name in os.environ:
        if name not in preflight.PROVIDER_KEY_ENV_NAMES:
            environment[name] = os.environ[name]
    completed = subprocess.run(
        [sys.executable, "-m", "pip", "check"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if completed.returncode != 0:
        raise ProbeError("pip check failed in the pinned environment")
    source_checks = record.get("installed_source_checks", [])
    if not source_checks or not all(item.get("matched") for item in source_checks):
        raise ProbeError("installed xai-sdk source hash verification failed")
    return {
        "artifact_evidence": "observed",
        "selected_python": selected_python,
        "python_version": record["python"]["version"],
        "lock_file_sha256": sha256_bytes(lock_raw),
        "lock_semantic_sha256": preflight.value_sha256(lock),
        "pip_check_status": "passed",
        "locked_versions_matched": record["checks"]["locked_versions_matched"],
        "wheelhouse_exact_and_hash_verified": record["checks"][
            "wheelhouse_exact_and_hash_verified"
        ],
        "installed_source_files_matched": record["checks"][
            "installed_source_files_matched"
        ],
        "installed_source_tree_matched": record["checks"][
            "installed_source_tree_matched"
        ],
        "installed_source_tree_sha256": record[
            "installed_source_tree_sha256"
        ],
        "installed_source_file_count": record[
            "installed_source_tree_file_count"
        ],
        "package_versions": {
            name: record["installed_locked_versions"][name]
            for name in ("grpcio", "jsonschema", "protobuf", "pydantic", "xai-sdk")
        },
    }


def compile_local_requests(tracked: Mapping[str, Any]) -> dict[str, Any]:
    """Construct both exact SDK requests under denial without transport."""

    guard = preflight.NetworkDenialGuard()
    serialized: list[Any] = []
    records: list[dict[str, Any]] = []
    with guard:
        try:
            import grpc

            guard.patch_grpc(grpc)
            from xai_sdk.chat import BaseChat, system, user
            from xai_sdk.proto import chat_pb2
            from xai_sdk.sync.chat import Client as ChatClient
        except ImportError as exc:
            raise ProbeError("pinned xai-sdk could not be imported locally") from exc
        response_format = chat_pb2.ResponseFormat(
            format_type=chat_pb2.FORMAT_TYPE_JSON_SCHEMA,
            schema=canonical_json_bytes(tracked["provider_schema"]).decode("utf-8"),
        )
        for profile in PROFILES:
            channel = preflight._RegistrationOnlyChannel()
            client = ChatClient(channel)
            chat = client.create(
                model=profile["model"],
                messages=[
                    system(tracked["system_prompt"]),
                    user(tracked["user_payload"]),
                ],
                max_tokens=MAX_OUTPUT_TOKENS,
                reasoning_effort="low",
                tools=[],
                parallel_tool_calls=False,
                response_format=response_format,
                search_parameters=None,
                store_messages=STORE_MESSAGES,
            )
            request = BaseChat._make_request(chat, 1)
            if channel.rpc_invocation_count:
                raise ProbeError("local SDK construction invoked a transport")
            forbidden_set = [
                name
                for name in (
                    "temperature",
                    "top_p",
                    "seed",
                    "frequency_penalty",
                    "presence_penalty",
                )
                if request.HasField(name)
            ]
            if forbidden_set:
                raise ProbeError("unfrozen sampling fields were set locally")
            if (
                request.HasField("search_parameters")
                or request.tools
                or request.HasField("tool_choice")
            ):
                raise ProbeError("tools, tool choice, or search appeared in local request")
            emitted = strict_json_loads(request.response_format.schema.encode("utf-8"))
            if preflight.value_sha256(emitted) != EXPECTED_PROVIDER_SCHEMA_SHA256:
                raise ProbeError("SDK changed the provider schema")
            serialized.append(request)
            records.append(
                {
                    "profile_id": profile["profile_id"],
                    "model": request.model,
                    "request_representation": request_representation(profile, tracked),
                    "serialized_request_sha256": sha256_bytes(
                        request.SerializeToString(deterministic=True)
                    ),
                    "request_construction_status": "passed_without_transport",
                    "tool_choice_field_present": request.HasField("tool_choice"),
                    "rpc_invocation_count": 0,
                }
            )
    first = copy.deepcopy(serialized[0])
    second = copy.deepcopy(serialized[1])
    first.model = ""
    second.model = ""
    equal_without_model = first.SerializeToString(
        deterministic=True
    ) == second.SerializeToString(deterministic=True)
    _verify_request_equivalence(
        [record["request_representation"] for record in records]
    )
    if not equal_without_model:
        raise ProbeError("SDK protobuf requests differ by more than model ID")
    return {
        "artifact_evidence": "derived",
        "profiles": records,
        "requests_differ_only_by_model_id": True,
        "message_arrays_byte_identical": True,
        "provider_calls_made": 0,
        "transport_rpc_invocations": 0,
    }


def _bounded_errors(errors: Sequence[Any]) -> list[str]:
    return [str(error)[:ERROR_MESSAGE_LIMIT] for error in errors[:32]]


def validate_bindings(
    parsed: Mapping[str, Any], case: Mapping[str, Any]
) -> list[str]:
    """Validate the exact synthetic conversation/turn/genesis bindings."""

    expected = {
        "conversation_key": case["conversation_key"],
        "target_turn_id": case["target_turn_id"],
        "as_of_turn_index": case["turn_index"],
        "prior_ledger_reference": None,
    }
    errors = [
        f"{name}_binding_mismatch"
        for name, value in expected.items()
        if parsed.get(name) != value
    ]
    participant = case["speaker"]["participant_reference"]
    participant_fields = {
        "addressed_participant",
        "participant_id",
        "attributed_participant_id",
        "asserted_or_analysed_by",
        "initiating_speaker",
        "owed_by_participant",
        "owed_to_participant",
    }
    forbidden_path_markers = (
        "/disks/",
        "mrsMThatcher2.py",
        "file://",
        "production/",
    )

    def walk(value: Any, location: str = "$") -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                child_location = f"{location}.{key}"
                if key in participant_fields and child not in {None, participant}:
                    errors.append(f"unknown_participant_reference:{child_location}")
                walk(child, child_location)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{location}[{index}]")
        elif isinstance(value, str) and any(
            marker in value for marker in forbidden_path_markers
        ):
            errors.append(f"unexpected_production_identity:{location}")

    walk(parsed)
    return _bounded_errors(errors)


def _iter_evidence_spans(
    value: Any, location: str = "$"
) -> Sequence[tuple[str, Mapping[str, Any]]]:
    found: list[tuple[str, Mapping[str, Any]]] = []
    if isinstance(value, Mapping):
        spans = value.get("exact_evidence_spans")
        if isinstance(spans, list):
            for index, span in enumerate(spans):
                if isinstance(span, Mapping):
                    found.append((f"{location}.exact_evidence_spans[{index}]", span))
        for key, child in value.items():
            if key != "exact_evidence_spans":
                found.extend(_iter_evidence_spans(child, f"{location}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_iter_evidence_spans(child, f"{location}[{index}]"))
    return found


def validate_evidence_spans(
    parsed: Mapping[str, Any], case: Mapping[str, Any]
) -> list[str]:
    """Require every cited span to be an exact current-turn Unicode slice."""

    expected_turn = str(case["target_turn_id"])
    text = str(case["visible_text"])
    errors: list[str] = []
    for location, span in _iter_evidence_spans(parsed):
        if span.get("turn_id") != expected_turn:
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
    return _bounded_errors(errors)


def semantic_smoke_invariants(
    parsed: Mapping[str, Any],
    case: Mapping[str, Any],
    *,
    materialised: bool,
) -> dict[str, Any]:
    """Evaluate the deliberately narrow, non-comparative lamp-case invariants."""

    propositions = parsed.get("new_propositions")
    one = propositions[0] if isinstance(propositions, list) and len(propositions) == 1 else None
    expected_span = case["exact_evidence_span"]
    participant = case["speaker"]["participant_reference"]
    derivation = one.get("derivation") if isinstance(one, Mapping) else None
    temporal_scope = one.get("temporal_scope") if isinstance(one, Mapping) else None
    checks = {
        "exactly_one_new_proposition": one is not None,
        "faithful_visible_statement": isinstance(one, Mapping)
        and one.get("canonical_text") == case["visible_text"],
        "exact_current_turn_evidence": isinstance(one, Mapping)
        and one.get("exact_evidence_spans") == [expected_span],
        "direct_span_derivation": isinstance(one, Mapping)
        and isinstance(derivation, Mapping)
        and derivation.get("kind") == "direct_span"
        and derivation.get("source_proposition_refs") == [],
        "positive_descriptive_assertion": isinstance(one, Mapping)
        and one.get("speech_act") == "assertion"
        and one.get("proposition_kind") == "descriptive"
        and one.get("polarity") == "positive",
        "no_unsupported_modality_quantification_or_date": isinstance(one, Mapping)
        and one.get("modality") == {"type": "none", "strength": "none"}
        and one.get("quantification") == {"type": "none", "surface_marker": None}
        and isinstance(temporal_scope, Mapping)
        and temporal_scope.get("type") == "present"
        and temporal_scope.get("start") is None
        and temporal_scope.get("end") is None
        and temporal_scope.get("surface_marker") in {None, "is"},
        "current_speaker_committed": isinstance(one, Mapping)
        and one.get("speaker_or_attributor")
        == {
            "kind": "speaker",
            "participant_id": participant,
            "attributed_participant_id": None,
        }
        and one.get("commitment_status") == "speaker_committed",
        "no_unsupported_collections": all(
            parsed.get(name) == [] for name in SMOKE_EMPTY_COLLECTIONS
        ),
        "no_unsupported_inference_or_warning": parsed.get(
            "unsupported_inferences_rejected"
        )
        == 0
        and parsed.get("warnings") == [],
        "extraction_complete": parsed.get("extraction_status") == "complete",
        "materialised_successfully": materialised,
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "comparative_score": None,
        "winner_selected": False,
    }


def _record_semantic_smoke(
    validation: dict[str, Any],
    parsed: Mapping[str, Any],
    case: Mapping[str, Any],
    *,
    materialised: bool,
) -> None:
    """Record the non-comparative smoke result after mandatory prior stages."""

    smoke = semantic_smoke_invariants(parsed, case, materialised=materialised)
    validation["semantic_smoke"] = smoke
    validation["semantic_smoke_status"] = smoke["status"]


def _initial_validation_record(raw: bytes) -> dict[str, Any]:
    return {
        "artifact_evidence": "derived",
        "raw_response_sha256": sha256_bytes(raw),
        "strict_json_status": "not_run",
        "structured_output_status": "not_run",
        "provider_schema_validation_status": "not_run",
        "provider_schema_errors": [],
        "canonical_validation_status": "not_run",
        "canonical_validation_errors": [],
        "ordinary_python_jsonschema_status": "not_run",
        "ordinary_python_jsonschema_errors": [],
        "ordinary_python_jsonschema_is_authority": False,
        "binding_validation_status": "not_run",
        "binding_errors": [],
        "evidence_span_validation_status": "not_run",
        "evidence_span_errors": [],
        "semantic_reference_validation_status": "not_run",
        "materialisation_status": "not_run",
        "materialisation_errors": [],
        "persisted_ledger_validation_status": "not_run",
        "persisted_ledger_validation_errors": [],
        "semantic_smoke_status": "not_run",
        "semantic_smoke": None,
        "provider_controls_persistent_fields": False,
    }


def process_response_bytes(
    raw: bytes,
    *,
    case: Mapping[str, Any],
    canonical_schema: Mapping[str, Any],
    provider_schema: Mapping[str, Any],
    persisted_ledger_schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Run the mandatory local chain once against immutable response bytes."""

    validation = _initial_validation_record(raw)
    parsed: dict[str, Any] | None = None
    ledger: dict[str, Any] | None = None
    local_id_map: dict[str, str] | None = None
    try:
        parsed = strict_json_loads(raw)
    except StrictJSONError as exc:
        validation["strict_json_status"] = "failed"
        validation["strict_json_errors"] = [str(exc)[:ERROR_MESSAGE_LIMIT]]
        return {"parsed": None, "ledger": None, "validation": validation}
    validation["strict_json_status"] = "passed"
    validation["strict_json_errors"] = []

    forbidden_fields: list[str] = []

    def inspect_provider_fields(value: Any, location: str = "$") -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                child_location = f"{location}.{key}"
                if key in PROVIDER_FORBIDDEN_PERSISTENCE_FIELDS and key not in {
                    "participant_id"
                }:
                    forbidden_fields.append(child_location)
                inspect_provider_fields(child, child_location)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                inspect_provider_fields(child, f"{location}[{index}]")

    inspect_provider_fields(parsed)
    validation["provider_forbidden_persistence_fields_present"] = forbidden_fields
    validation["provider_controls_persistent_fields"] = False

    provider_errors = preflight.intended_validation_errors(
        provider_schema, parsed, pattern_mode="xai_full_string"
    )
    validation["provider_schema_errors"] = _bounded_errors(provider_errors)
    validation["provider_schema_validation_status"] = (
        "passed" if not provider_errors else "failed"
    )
    validation["structured_output_status"] = (
        "valid" if not provider_errors else "invalid"
    )

    canonical_errors = preflight.intended_validation_errors(
        canonical_schema, parsed, pattern_mode="canonical_outer_anchors"
    )
    ordinary_errors = preflight.validation_errors(canonical_schema, parsed)
    validation["canonical_validation_errors"] = _bounded_errors(canonical_errors)
    validation["canonical_validation_status"] = (
        "passed" if not canonical_errors else "failed"
    )
    validation["ordinary_python_jsonschema_errors"] = _bounded_errors(
        ordinary_errors
    )
    validation["ordinary_python_jsonschema_status"] = (
        "passed" if not ordinary_errors else "failed"
    )
    if canonical_errors:
        _record_semantic_smoke(validation, parsed, case, materialised=False)
        return {"parsed": parsed, "ledger": None, "validation": validation}

    binding_errors = [
        f"provider_controlled_persistence_field:{location}"
        for location in forbidden_fields
    ] + validate_bindings(parsed, case)
    binding_errors = _bounded_errors(binding_errors)
    validation["binding_errors"] = binding_errors
    validation["binding_validation_status"] = (
        "passed" if not binding_errors else "failed"
    )
    if binding_errors:
        _record_semantic_smoke(validation, parsed, case, materialised=False)
        return {"parsed": parsed, "ledger": None, "validation": validation}

    evidence_errors = validate_evidence_spans(parsed, case)
    validation["evidence_span_errors"] = evidence_errors
    validation["evidence_span_validation_status"] = (
        "passed" if not evidence_errors else "failed"
    )
    if evidence_errors:
        _record_semantic_smoke(validation, parsed, case, materialised=False)
        return {"parsed": parsed, "ledger": None, "validation": validation}

    current_turn, participant, genesis_context = build_materialisation_inputs(case)
    result = semantic.materialise_semantic_delta(
        None,
        current_turn,
        parsed,
        current_participant=participant,
        genesis_context=genesis_context,
        semantic_schema=canonical_schema,
        ledger_schema=persisted_ledger_schema,
    )
    validation["materialisation_errors"] = _bounded_errors(result.errors)
    if result.status == "semantic_reference_invalid":
        validation["semantic_reference_validation_status"] = "failed"
        validation["materialisation_status"] = "not_run_due_to_reference_failure"
        _record_semantic_smoke(validation, parsed, case, materialised=False)
        return {"parsed": parsed, "ledger": None, "validation": validation}
    validation["semantic_reference_validation_status"] = "passed"
    if result.status != semantic.SUCCESS_STATUS or result.ledger is None:
        if result.status == "semantic_evidence_invalid":
            validation["evidence_span_validation_status"] = "failed"
        validation["materialisation_status"] = "failed"
        if result.status == "persisted_ledger_validation_failure":
            validation["persisted_ledger_validation_status"] = "failed"
        _record_semantic_smoke(validation, parsed, case, materialised=False)
        return {"parsed": parsed, "ledger": None, "validation": validation}

    ledger = copy.deepcopy(result.ledger)
    local_id_map = copy.deepcopy(result.local_id_map)
    validation["materialisation_status"] = "passed"
    persisted_errors = semantic.phase1.validate_ledger(
        ledger,
        {"conversation_key": case["conversation_key"], "turns": [current_turn]},
        persisted_ledger_schema,
        _immediate_previous=None,
        _validate_history=False,
    )
    if ledger.get("ledger_sha256") != semantic.phase1.ledger_sha256(ledger):
        persisted_errors = list(persisted_errors) + ["ledger_self_hash_mismatch"]
    validation["persisted_ledger_validation_errors"] = _bounded_errors(
        persisted_errors
    )
    validation["persisted_ledger_validation_status"] = (
        "passed" if not persisted_errors else "failed"
    )
    if persisted_errors:
        _record_semantic_smoke(validation, parsed, case, materialised=True)
        return {
            "parsed": parsed,
            "ledger": ledger,
            "local_id_map": local_id_map,
            "validation": validation,
        }

    _record_semantic_smoke(validation, parsed, case, materialised=True)
    return {
        "parsed": parsed,
        "ledger": ledger,
        "local_id_map": local_id_map,
        "validation": validation,
    }


def derive_profile_disposition(validation: Mapping[str, Any]) -> str:
    """Derive a per-profile disposition without conflating server acceptance."""

    if validation.get("strict_json_status") != "passed":
        return "accepted_but_strict_json_invalid"
    if validation.get("provider_schema_validation_status") != "passed":
        return "accepted_but_provider_schema_validation_failed"
    if validation.get("canonical_validation_status") != "passed":
        return "accepted_but_canonical_validation_failed"
    if validation.get("binding_validation_status") != "passed":
        return "accepted_but_binding_validation_failed"
    if validation.get("evidence_span_validation_status") != "passed":
        return "accepted_but_evidence_span_validation_failed"
    if validation.get("semantic_reference_validation_status") != "passed":
        return "accepted_but_semantic_reference_validation_failed"
    if validation.get("materialisation_status") != "passed":
        return "accepted_but_materialisation_failed"
    if validation.get("persisted_ledger_validation_status") != "passed":
        return "accepted_but_persisted_ledger_validation_failed"
    if validation.get("semantic_smoke_status") != "passed":
        return "accepted_but_semantic_smoke_failed"
    return "accepted_and_fully_validated"


def _processed_bytes(result: Mapping[str, Any]) -> bytes:
    return canonical_json_bytes(
        {
            "parsed": result.get("parsed"),
            "ledger": result.get("ledger"),
            "local_id_map": result.get("local_id_map"),
            "validation": result.get("validation"),
        }
    )


def process_response_twice(
    raw: bytes,
    *,
    case: Mapping[str, Any],
    canonical_schema: Mapping[str, Any],
    provider_schema: Mapping[str, Any],
    persisted_ledger_schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Require byte-identical derived results from the same saved bytes."""

    kwargs = {
        "case": case,
        "canonical_schema": canonical_schema,
        "provider_schema": provider_schema,
        "persisted_ledger_schema": persisted_ledger_schema,
    }
    first = process_response_bytes(raw, **kwargs)
    second = process_response_bytes(raw, **kwargs)
    first_bytes = _processed_bytes(first)
    second_bytes = _processed_bytes(second)
    if first_bytes != second_bytes:
        raise ProbeError("saved response reprocessing is not deterministic")
    result = copy.deepcopy(first)
    result["validation"]["deterministic_reprocessing"] = {
        "status": "passed",
        "first_derived_sha256": sha256_bytes(first_bytes),
        "second_derived_sha256": sha256_bytes(second_bytes),
        "byte_identical": True,
        "additional_provider_calls": 0,
    }
    return result


def _git_output(*arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=PROJECT_DIR,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ProbeError("Git metadata query failed")
    return completed.stdout.strip()


def _write_checksums(output_dir: Path) -> None:
    lines: list[str] = []
    for path in sorted(output_dir.rglob("*")):
        if path == output_dir / "SHA256SUMS":
            continue
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise ProbeError(f"private run contains symlink: {path}")
        if stat.S_ISREG(metadata.st_mode):
            relative = path.relative_to(output_dir).as_posix()
            lines.append(f"{sha256_bytes(_read_regular_bytes(path, relative))}  {relative}\n")
    _atomic_write_private_bytes(
        output_dir / "SHA256SUMS", "".join(lines).encode("utf-8")
    )


def _recovery_unlisted_paths(ledger: Mapping[str, Any]) -> frozenset[str]:
    """Return only known post-prepare artifacts for interrupted execution."""

    names: set[str] = set()
    response_names = {
        "response-metadata.json",
        "response.raw.txt",
        "response.parsed.json",
        "validation.json",
        "materialised-ledger.json",
        "usage.json",
    }
    for entry in ledger["entries"]:
        state = entry["state"]
        model = entry["model"]
        if state == "provider_error_received":
            names.add(f"{model}/response-metadata.json")
        elif state not in {"planned", "not_attempted_due_to_global_failure"}:
            names.update(f"{model}/{name}" for name in response_names)
    names.update(
        {
            "phase1.4-report.md",
            "result-summary.json",
            "validation.json",
        }
    )
    return frozenset(names)


def _checksum_declares_finalized_run(output_dir: Path) -> bool:
    """Return whether the checksum manifest commits all final root artifacts."""

    manifest = _read_regular_bytes(output_dir / "SHA256SUMS", "SHA256SUMS")
    try:
        lines = manifest.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ProbeError("SHA256SUMS is not UTF-8") from exc
    listed: set[str] = set()
    for line in lines:
        match = re.fullmatch(r"[0-9a-f]{64}  ([^\r\n]+)", line)
        if match is None:
            raise ProbeError("invalid SHA256SUMS line")
        listed.add(match.group(1))
    return {
        "phase1.4-report.md",
        "result-summary.json",
        "validation.json",
    }.issubset(listed)


def verify_checksums(
    output_dir: Path,
    *,
    allowed_mismatches: frozenset[str] = frozenset(),
    allowed_unlisted: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Verify private-run hashes and reject unlisted files outside recovery."""

    manifest = _read_regular_bytes(output_dir / "SHA256SUMS", "SHA256SUMS")
    try:
        lines = manifest.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ProbeError("SHA256SUMS is not UTF-8") from exc
    checked = 0
    tolerated: list[str] = []
    listed: set[str] = set()
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([^\r\n]+)", line)
        if match is None:
            raise ProbeError("invalid SHA256SUMS line")
        relative = Path(match.group(2))
        if relative.is_absolute() or ".." in relative.parts:
            raise ProbeError("unsafe SHA256SUMS path")
        relative_name = relative.as_posix()
        if relative_name in listed:
            raise ProbeError("duplicate SHA256SUMS path")
        listed.add(relative_name)
        target = output_dir / relative
        if sha256_bytes(_read_regular_bytes(target, str(relative))) != match.group(1):
            if relative_name not in allowed_mismatches:
                raise ProbeError(f"checksum mismatch: {relative}")
            tolerated.append(relative_name)
        checked += 1
    actual_files = {
        path.relative_to(output_dir).as_posix()
        for path in output_dir.rglob("*")
        if path.is_file() and not path.is_symlink() and path.name != "SHA256SUMS"
    }
    unexpected = sorted(actual_files - listed)
    forbidden_unlisted = sorted(set(unexpected) - set(allowed_unlisted))
    if forbidden_unlisted:
        raise ProbeError(
            "unlisted private-run files: " + ",".join(forbidden_unlisted)
        )
    return {
        "status": "passed_with_expected_recovery_changes"
        if tolerated or unexpected
        else "passed",
        "files_checked": checked,
        "tolerated_mismatches": tolerated,
        "tolerated_unlisted_files": unexpected,
    }


def audit_private_run_permissions(output_dir: Path) -> dict[str, Any]:
    """Require mode 0700 directories, 0600 files, and no symlinks."""

    directories = 0
    files = 0
    for path in [output_dir, *sorted(output_dir.rglob("*"))]:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise ProbeError(f"private run contains symlink: {path}")
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISDIR(metadata.st_mode):
            directories += 1
            if mode != 0o700:
                raise ProbeError(f"private directory mode is not 0700: {path}")
        elif stat.S_ISREG(metadata.st_mode):
            files += 1
            if mode != 0o600:
                raise ProbeError(f"private file mode is not 0600: {path}")
        else:
            raise ProbeError(f"unsupported private-run entry: {path}")
    return {"status": "passed", "directories": directories, "files": files}


def audit_private_run_inventory(output_dir: Path) -> dict[str, Any]:
    """Reject every private-run path outside the bounded artifact vocabulary."""

    root_files = {
        "SHA256SUMS",
        "call-ledger.json",
        "live-probe-manifest.json",
        "operator-record.json",
        "phase1.4-report.md",
        "prompt-manifest.json",
        "provider-schema.json",
        "result-summary.json",
        "run-manifest.json",
        "validation.json",
    }
    model_files = {
        "materialised-ledger.json",
        "request-metadata.json",
        "response-metadata.json",
        "response.parsed.json",
        "response.raw.txt",
        "usage.json",
        "validation.json",
    }
    expected_directories = {profile["model"] for profile in PROFILES}
    files = 0
    for path in sorted(output_dir.rglob("*")):
        relative = path.relative_to(output_dir)
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise ProbeError(f"private run contains symlink: {relative}")
        if stat.S_ISDIR(metadata.st_mode):
            if len(relative.parts) != 1 or relative.name not in expected_directories:
                raise ProbeError(f"unexpected private-run directory: {relative}")
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise ProbeError(f"unsupported private-run entry: {relative}")
        if len(relative.parts) == 1:
            allowed = relative.name in root_files
        elif len(relative.parts) == 2 and relative.parts[0] in expected_directories:
            allowed = relative.parts[1] in model_files
        else:
            allowed = False
        if not allowed:
            raise ProbeError(f"unexpected private-run file: {relative}")
        files += 1
    return {"status": "passed", "files": files}


def prepare_run(output: str | Path) -> dict[str, Any]:
    """Create a zero-call immutable plan after all local checks pass."""

    tracked = validate_tracked_inputs()
    environment = verify_pinned_environment()
    local_compilation = compile_local_requests(tracked)
    representations = [
        request_representation(profile, tracked) for profile in PROFILES
    ]
    _verify_request_equivalence(representations)
    call_plan = make_call_plan(tracked)
    prepare_head = _git_output("rev-parse", "HEAD")
    if prepare_head not in ALLOWED_PREPARE_HEADS:
        raise ProbeError("Git HEAD is not approved for Phase 1.4 preparation")
    output_dir = _create_private_directory(output, "private run")
    try:
        for profile in PROFILES:
            _create_private_subdirectory(output_dir / profile["model"])
        run_manifest = {
            "artifact_evidence": "derived",
            "run_format": "proposition-ledger-phase1.4-live-schema-probe-v1",
            "prepared_at_utc": utc_now(),
            "private_run_path": str(output_dir),
            "git_source_commit": SOURCE_COMMIT,
            "git_source_parent": SOURCE_PARENT,
            "git_source_subject": SOURCE_SUBJECT,
            "git_head_at_prepare": prepare_head,
            "live_probe_tool_sha256_at_prepare": sha256_bytes(
                _read_regular_bytes(Path(__file__), "live probe tool")
            ),
            "git_branch": _git_output("branch", "--show-current"),
            "origin_master_at_setup": _git_output("rev-parse", "origin/master"),
            "request_contract_revision": REQUEST_CONTRACT_REVISION,
            "correction_base_commit": CORRECTION_BASE_COMMIT,
            "correction_reason": "omit_tool_choice_when_tools_are_empty",
            "prior_phase1_4_private_run": str(PRIOR_PHASE14_PRIVATE_RUN),
            "prior_phase1_4_provider_call_count": PRIOR_PHASE14_PROVIDER_CALL_COUNT,
            "diagnosis_private_run": str(DIAGNOSIS_PRIVATE_RUN),
            "diagnostic_provider_call_count": DIAGNOSTIC_PROVIDER_CALL_COUNT,
            "synthetic_only": True,
            "provider_call_budget": PROVIDER_CALL_BUDGET,
            "provider_call_count": 0,
            "real_conversation_records_read": 0,
            "held_out_records_read": 0,
            "production_files_read": 0,
            "sealed_clean_prefixes_read": 0,
            "model_list_calls": 0,
            "openai_calls": 0,
            "x_calls": 0,
            "search_calls": 0,
            "tool_calls": 0,
            "development_pilot_authorised": False,
            "real_corpus_use_authorised": False,
            "held_out_use_authorised": False,
            "production_integration_authorised": False,
        }
        prompt_manifest = {
            "artifact_evidence": "derived",
            **tracked["hashes"],
            "message_count": 2,
            "message_arrays_byte_identical": True,
            "user_payload_construction": "canonical_compact_json_of_tracked_case",
            "system_prompt_final_line_feed_preserved": tracked[
                "system_prompt"
            ].endswith("\n"),
        }
        artifact_inventory = {
            "run-manifest.json": "derived",
            "live-probe-manifest.json": "derived",
            "provider-schema.json": "derived",
            "prompt-manifest.json": "derived",
            "call-ledger.json": "derived",
            "operator-record.json": "derived",
            "result-summary.json": "derived",
            "validation.json": "derived",
            "phase1.4-report.md": "derived",
            "grok-4.3/request-metadata.json": "planned",
            "grok-4.3/response-metadata.json": "observed",
            "grok-4.3/response.raw.txt": "observed",
            "grok-4.3/response.parsed.json": "derived",
            "grok-4.3/validation.json": "derived",
            "grok-4.3/materialised-ledger.json": "derived",
            "grok-4.3/usage.json": "observed",
            "grok-4.6/request-metadata.json": "planned",
            "grok-4.6/response-metadata.json": "observed",
            "grok-4.6/response.raw.txt": "observed",
            "grok-4.6/response.parsed.json": "derived",
            "grok-4.6/validation.json": "derived",
            "grok-4.6/materialised-ledger.json": "derived",
            "grok-4.6/usage.json": "observed",
            "SHA256SUMS": "derived",
        }
        live_manifest = {
            "artifact_evidence": "derived",
            "manifest_format": "proposition-ledger-phase1.4-live-probe-manifest-v1",
            "profiles": list(PROFILES),
            "maximum_output_tokens": MAX_OUTPUT_TOKENS,
            "store_messages": STORE_MESSAGES,
            "request_contract_revision": REQUEST_CONTRACT_REVISION,
            "tool_choice_transport_policy": "omitted_when_tools_empty",
            "client_timeout_seconds": CLIENT_TIMEOUT_SECONDS,
            "no_retry_channel_options": [list(item) for item in NO_RETRY_CHANNEL_OPTIONS],
            "sdk_retry_behavior_inspected": True,
            "sdk_default_retry_behavior": (
                "grpc retries enabled for UNAVAILABLE with maxAttempts=5"
            ),
            "one_attempt_enforcement": (
                "grpc.enable_retries=0 plus empty service config and no application retry"
            ),
            "environment_validation": environment,
            "local_request_compilation": local_compilation,
            "provider_schema_sha256": tracked["hashes"][
                "provider_schema_sha256"
            ],
            "artifact_inventory": artifact_inventory,
            "authorisation": {
                "development_pilot_authorised": False,
                "real_corpus_use_authorised": False,
                "held_out_use_authorised": False,
                "production_integration_authorised": False,
            },
        }
        _atomic_write_private_json(output_dir / "run-manifest.json", run_manifest)
        _atomic_write_private_json(
            output_dir / "live-probe-manifest.json", live_manifest
        )
        _atomic_write_private_json(
            output_dir / "provider-schema.json", tracked["provider_schema"]
        )
        _atomic_write_private_json(
            output_dir / "prompt-manifest.json", prompt_manifest
        )
        _atomic_write_private_json(output_dir / "call-ledger.json", call_plan)
        for profile, representation in zip(PROFILES, representations):
            request_metadata = {
                "artifact_evidence": "planned",
                "profile_id": profile["profile_id"],
                "provider": "xAI",
                "request": representation,
                "provider_schema_sha256": tracked["hashes"][
                    "provider_schema_sha256"
                ],
                "system_prompt_sha256": tracked["hashes"][
                    "system_prompt_sha256"
                ],
                "user_payload_sha256": tracked["hashes"]["user_payload_sha256"],
                "complete_message_array_sha256": tracked["hashes"][
                    "complete_message_array_sha256"
                ],
                "credentials_included": False,
                "request_headers_preserved": False,
            }
            _atomic_write_private_json(
                output_dir / profile["model"] / "request-metadata.json",
                request_metadata,
            )
        _write_checksums(output_dir)
        verify_checksums(output_dir)
        audit_private_run_permissions(output_dir)
        audit_private_run_inventory(output_dir)
    except Exception:
        # Preserve partial evidence for diagnosis; never recursively remove a run.
        raise
    return {
        "status": "prepared",
        "private_run": str(output_dir),
        "provider_call_count": 0,
        "provider_schema_sha256": tracked["hashes"]["provider_schema_sha256"],
        "prompt_hashes": {
            key: tracked["hashes"][key]
            for key in (
                "synthetic_case_file_sha256",
                "system_prompt_sha256",
                "user_payload_sha256",
                "complete_message_array_sha256",
            )
        },
    }


def _sanitize_error_message(message: str) -> str:
    """Discard arbitrary error text rather than attempting deny-list redaction."""

    del message
    return "provider error detail withheld by credential boundary"


def _provider_error_code(exc: BaseException) -> str | None:
    candidate = getattr(exc, "code", None)
    try:
        value = candidate() if callable(candidate) else candidate
    except Exception:
        return None
    if value is None:
        return None
    name = getattr(value, "name", None)
    normalized = str(name if name is not None else value).removeprefix(
        "StatusCode."
    ).upper()
    if normalized not in GRPC_STATUS_CODES:
        return "UNKNOWN"
    return normalized


def _provider_error_details(exc: BaseException) -> str:
    """Return transient classification text that is never persisted or reported."""

    candidate = getattr(exc, "details", None)
    try:
        value = candidate() if callable(candidate) else candidate
    except Exception:
        value = None
    if not isinstance(value, str) or not value:
        value = f"{type(exc).__name__}: provider call failed"
    return value[:4096]


def classify_provider_error(exc: BaseException) -> ErrorClassification:
    """Classify one no-retry SDK error without retaining traceback or metadata."""

    code = _provider_error_code(exc)
    provider_detail = _provider_error_details(exc)
    lowered = provider_detail.lower()

    def message_for(category: str) -> str:
        return _sanitize_error_message(
            f"provider error classified as {category}; code={code or 'unavailable'}"
        )

    schema_markers = ("json schema", "schema", "response_format", "structured output")
    model_profile_markers = (
        "unknown model",
        "model not found",
        "model is not available",
        "model is unavailable",
        "model unavailable",
        "unsupported model",
        "access to model",
        "permission for model",
        "reasoning_effort",
        "reasoning effort",
    )
    has_schema_marker = any(marker in lowered for marker in schema_markers)
    has_model_profile_marker = any(
        marker in lowered for marker in model_profile_markers
    )
    has_billing_marker = any(
        marker in lowered
        for marker in (
            "billing",
            "credit balance",
            "insufficient credit",
            "payment method",
            "payment required",
        )
    )
    has_authentication_marker = any(
        marker in lowered
        for marker in (
            "authentication failed",
            "invalid api key",
            "invalid credential",
            "unauthenticated",
        )
    )
    has_pre_send_transport_marker = any(
        marker in lowered
        for marker in (
            "connection refused",
            "dns",
            "failed to connect",
            "name resolution",
        )
    )
    if code in {"DEADLINE_EXCEEDED", "CANCELLED"} or "timed out" in lowered:
        return ErrorClassification(
            "timeout_after_possible_transmission",
            code,
            message_for("timeout_after_possible_transmission"),
            "uncertain_after_send",
            "uncertain_after_send",
            "not_determined",
            False,
            True,
        )
    if code == "UNAUTHENTICATED" or has_authentication_marker:
        return ErrorClassification(
            "authentication_failure",
            code,
            message_for("authentication_failure"),
            "provider_error_received",
            "authentication_or_authorisation_failed",
            "not_determined",
            True,
            True,
        )
    if has_billing_marker:
        return ErrorClassification(
            "billing_failure",
            code,
            message_for("billing_failure"),
            "provider_error_received",
            "authentication_or_authorisation_failed",
            "not_determined",
            True,
            True,
        )
    if code == "PERMISSION_DENIED" and has_model_profile_marker and not any(
        marker in lowered for marker in ("account", "bill", "quota")
    ):
        return ErrorClassification(
            "model_profile_rejection",
            code,
            message_for("model_profile_rejection"),
            "provider_error_received",
            "server_rejected_profile",
            "not_determined",
            True,
            False,
        )
    if code == "PERMISSION_DENIED":
        category = "billing_failure" if "bill" in lowered else "authorisation_failure"
        return ErrorClassification(
            category,
            code,
            message_for(category),
            "provider_error_received",
            "authentication_or_authorisation_failed",
            "not_determined",
            True,
            True,
        )
    if code == "RESOURCE_EXHAUSTED" or any(
        marker in lowered for marker in ("rate limit", "quota", "billing limit")
    ):
        return ErrorClassification(
            "rate_limit_or_account_quota_failure",
            code,
            message_for("rate_limit_or_account_quota_failure"),
            "provider_error_received",
            "rate_limited_or_quota_blocked",
            "not_determined",
            True,
            True,
        )
    if has_pre_send_transport_marker:
        return ErrorClassification(
            "transport_failure_before_send",
            code,
            message_for("transport_failure_before_send"),
            "provider_error_received",
            "transport_failure_before_send",
            "not_determined",
            False,
            True,
        )
    if code == "UNAVAILABLE":
        return ErrorClassification(
            "global_transport_failure",
            code,
            message_for("global_transport_failure"),
            "provider_error_received",
            "global_transport_failure",
            "not_determined",
            False,
            True,
        )
    if code in {"INVALID_ARGUMENT", "FAILED_PRECONDITION"} and has_schema_marker:
        return ErrorClassification(
            "server_schema_rejection",
            code,
            message_for("server_schema_rejection"),
            "provider_error_received",
            "server_rejected_schema",
            "rejected",
            True,
            False,
        )
    if code in {
        "INVALID_ARGUMENT",
        "FAILED_PRECONDITION",
        "NOT_FOUND",
        "UNIMPLEMENTED",
    } and has_model_profile_marker:
        return ErrorClassification(
            "model_profile_rejection",
            code,
            message_for("model_profile_rejection"),
            "provider_error_received",
            "server_rejected_profile",
            "not_determined",
            True,
            False,
        )
    if code is not None:
        return ErrorClassification(
            "definite_provider_error",
            code,
            message_for("definite_provider_error"),
            "provider_error_received",
            "definite_provider_error",
            "not_determined",
            True,
            False,
        )
    return ErrorClassification(
        "unclassified_failure_after_send",
        None,
        message_for("unclassified_failure_after_send"),
        "uncertain_after_send",
        "uncertain_after_send",
        "not_determined",
        False,
        True,
    )


class XaiLiveTransport:
    """The sole official-SDK live transport; constructed only in live mode."""

    def __init__(self) -> None:
        """Create the pinned client with a bounded timeout and retries disabled."""

        api_key = os.environ.get("XAI_API_KEY")
        if not api_key:
            raise ProbeError("live_probe_not_run_missing_xai_api_key")
        try:
            from google.protobuf.json_format import MessageToDict
            from xai_sdk import Client
            from xai_sdk.chat import system, user
            from xai_sdk.proto import chat_pb2
        except ImportError as exc:
            raise ProbeError("pinned official xai-sdk import failed") from exc
        self._message_to_dict = MessageToDict
        self._system = system
        self._user = user
        self._chat_pb2 = chat_pb2
        self._client = Client(
            api_key=api_key,
            timeout=CLIENT_TIMEOUT_SECONDS,
            channel_options=list(NO_RETRY_CHANNEL_OPTIONS),
        )

    def sample(
        self, profile: Mapping[str, Any], tracked: Mapping[str, Any]
    ) -> LiveObservation:
        """Make exactly one official non-streaming sample call."""

        response_format = self._chat_pb2.ResponseFormat(
            format_type=self._chat_pb2.FORMAT_TYPE_JSON_SCHEMA,
            schema=canonical_json_bytes(tracked["provider_schema"]).decode("utf-8"),
        )
        chat = self._client.chat.create(
            model=profile["model"],
            messages=[
                self._system(tracked["system_prompt"]),
                self._user(tracked["user_payload"]),
            ],
            max_tokens=MAX_OUTPUT_TOKENS,
            reasoning_effort="low",
            tools=[],
            parallel_tool_calls=False,
            response_format=response_format,
            search_parameters=None,
            store_messages=STORE_MESSAGES,
        )
        # This is the only provider invocation in the implementation.
        response = chat.sample()
        raw_text = response.content
        if not isinstance(raw_text, str):
            raise ProbeError("provider response content was not text")
        usage = self._message_to_dict(
            response.usage, preserving_proto_field_name=True
        )
        return LiveObservation(
            raw_text=raw_text,
            returned_model_id=(response.proto.model or None),
            provider_response_id=(response.id or None),
            finish_reason=(response.finish_reason or None),
            usage=usage,
        )

    def close(self) -> None:
        """Close transient SDK channels without preserving the client."""

        self._client.close()


def _create_real_transport() -> XaiLiveTransport:
    return XaiLiveTransport()


def _credential_present() -> bool:
    return bool(os.environ.get("XAI_API_KEY"))


def _remove_unrelated_credentials() -> None:
    credential_name_markers = (
        "API_KEY",
        "AUTHORIZATION",
        "BEARER",
        "COOKIE",
        "CREDENTIAL",
        "PASSWORD",
        "SECRET",
        "TOKEN",
    )
    names_to_remove = set(UNRELATED_CREDENTIAL_VARIABLES)
    names_to_remove.update(
        name
        for name in os.environ
        if name != "XAI_API_KEY"
        and any(marker in name.upper() for marker in credential_name_markers)
    )
    for name in names_to_remove:
        os.environ.pop(name, None)
    os.environ["XAI_SDK_DISABLE_SENSITIVE_TELEMETRY_ATTRIBUTES"] = "1"
    os.environ["XAI_SDK_DISABLE_TRACING"] = "1"


@contextmanager
def _exclusive_execution_lock(output_dir: Path) -> Any:
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
            raise ProbeError("private run changed while acquiring execution lock")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError as exc:
            raise ProbeError(
                "another live-probe execution holds the private-run lock"
            ) from exc
        after = output_dir.lstat()
        if (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino):
            raise ProbeError("private run changed while acquiring execution lock")
        yield
    finally:
        try:
            if acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _load_call_ledger(output_dir: Path) -> dict[str, Any]:
    ledger = _load_strict_json_file(output_dir / "call-ledger.json", "call ledger")
    if not isinstance(ledger, dict):
        raise ProbeError("call ledger is not an object")
    entries = ledger.get("entries")
    if not isinstance(entries, list) or len(entries) != 2:
        raise ProbeError("call ledger does not contain exactly two entries")
    allowed_states = set(ALLOWED_STATE_TRANSITIONS)
    for index, (entry, profile) in enumerate(zip(entries, PROFILES), start=1):
        if not isinstance(entry, dict):
            raise ProbeError("call ledger entry is not an object")
        if (
            entry.get("order") != index
            or entry.get("profile_id") != profile["profile_id"]
            or entry.get("provider") != "xAI"
            or entry.get("model") != profile["model"]
        ):
            raise ProbeError("call ledger order or profile identity differs from plan")
        state = entry.get("state")
        attempt = entry.get("attempt_number")
        count = entry.get("provider_call_count")
        if state not in allowed_states:
            raise ProbeError("call ledger contains an unknown state")
        if isinstance(attempt, bool) or attempt not in {0, 1}:
            raise ProbeError("call ledger attempt number is invalid")
        if isinstance(count, bool) or count not in {0, 1}:
            raise ProbeError("call ledger entry count is invalid")
        if state in {"planned", "not_attempted_due_to_global_failure"}:
            if attempt != 0 or count != 0:
                raise ProbeError("unattempted call ledger entry has a call count")
        elif attempt != 1 or count != 1 or not entry.get("started_at_utc"):
            raise ProbeError("attempted call ledger entry lacks durable send evidence")
    counts = sum(entry["provider_call_count"] for entry in entries)
    if counts != ledger.get("provider_call_count") or counts > PROVIDER_CALL_BUDGET:
        raise ProbeError("call ledger count is invalid")
    definite_first_states = {
        "response_received",
        "provider_error_received",
        "fully_validated",
        "terminal_validation_failure",
    }
    if (
        entries[1]["provider_call_count"]
        and entries[0]["state"] not in definite_first_states
    ):
        raise ProbeError("second call lacks a definite first-call result")
    return ledger


def _transition_call(
    output_dir: Path,
    index: int,
    new_state: str,
    **evidence: Any,
) -> dict[str, Any]:
    ledger = _load_call_ledger(output_dir)
    entry = ledger["entries"][index]
    old_state = str(entry["state"])
    if new_state not in ALLOWED_STATE_TRANSITIONS.get(old_state, set()):
        raise ProbeError(f"forbidden call-ledger transition: {old_state}->{new_state}")
    entry["state"] = new_state
    entry.update(copy.deepcopy(evidence))
    if new_state == "sending":
        if entry.get("attempt_number") not in {0, None}:
            raise ProbeError("provider call already has an attempt number")
        entry["attempt_number"] = 1
        entry["provider_call_count"] = 1
        entry["started_at_utc"] = evidence.get("started_at_utc", utc_now())
    ledger["provider_call_count"] = sum(
        int(item.get("provider_call_count", 0)) for item in ledger["entries"]
    )
    if ledger["provider_call_count"] > PROVIDER_CALL_BUDGET:
        raise ProbeError("provider call budget would be exceeded")
    _atomic_write_private_json(output_dir / "call-ledger.json", ledger)
    return ledger


def _blank_profile_result(profile: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "artifact_evidence": "derived",
        "profile_id": profile["profile_id"],
        "provider": "xAI",
        "model": profile["model"],
        "request_attempted": False,
        "request_definitely_sent": False,
        "provider_response_received": False,
        "server_schema_acceptance_status": "not_determined",
        "structured_output_status": "not_run",
        "strict_json_status": "not_run",
        "provider_schema_validation_status": "not_run",
        "canonical_validation_status": "not_run",
        "ordinary_python_jsonschema_status": "not_run",
        "binding_validation_status": "not_run",
        "evidence_span_validation_status": "not_run",
        "semantic_reference_validation_status": "not_run",
        "materialisation_status": "not_run",
        "persisted_ledger_validation_status": "not_run",
        "semantic_smoke_status": "not_run",
        "provider_error_class": None,
        "provider_error_code": None,
        "call_state": "planned",
        "provider_call_count": 0,
        "profile_disposition": "not_attempted",
        "returned_model_id": None,
        "provider_response_id": None,
        "finish_reason": None,
        "usage": None,
        "latency_seconds": None,
        "received_at_utc": None,
        "raw_response_sha256": None,
    }


def _profile_result_from_validation(
    profile: Mapping[str, Any],
    observation: LiveObservation,
    validation: Mapping[str, Any],
    *,
    latency_seconds: float,
    received_at_utc: str,
) -> dict[str, Any]:
    result = _blank_profile_result(profile)
    disposition = derive_profile_disposition(validation)
    result.update(
        {
            "request_attempted": True,
            "request_definitely_sent": True,
            "provider_response_received": True,
            "server_schema_acceptance_status": "accepted",
            "structured_output_status": validation["structured_output_status"],
            "strict_json_status": validation["strict_json_status"],
            "provider_schema_validation_status": validation[
                "provider_schema_validation_status"
            ],
            "canonical_validation_status": validation[
                "canonical_validation_status"
            ],
            "ordinary_python_jsonschema_status": validation[
                "ordinary_python_jsonschema_status"
            ],
            "binding_validation_status": validation["binding_validation_status"],
            "evidence_span_validation_status": validation[
                "evidence_span_validation_status"
            ],
            "semantic_reference_validation_status": validation[
                "semantic_reference_validation_status"
            ],
            "materialisation_status": validation["materialisation_status"],
            "persisted_ledger_validation_status": validation[
                "persisted_ledger_validation_status"
            ],
            "semantic_smoke_status": validation["semantic_smoke_status"],
            "call_state": (
                "fully_validated"
                if disposition == "accepted_and_fully_validated"
                else "terminal_validation_failure"
            ),
            "provider_call_count": 1,
            "profile_disposition": disposition,
            "returned_model_id": observation.returned_model_id,
            "provider_response_id": observation.provider_response_id,
            "finish_reason": observation.finish_reason,
            "usage": copy.deepcopy(dict(observation.usage)),
            "latency_seconds": latency_seconds,
            "received_at_utc": received_at_utc,
            "raw_response_sha256": validation["raw_response_sha256"],
        }
    )
    return result


def _profile_result_from_error(
    profile: Mapping[str, Any],
    classification: ErrorClassification,
    *,
    latency_seconds: float,
    completed_at_utc: str,
) -> dict[str, Any]:
    result = _blank_profile_result(profile)
    result.update(
        {
            "request_attempted": True,
            "request_definitely_sent": classification.request_definitely_sent,
            "server_schema_acceptance_status": classification.server_schema_acceptance_status,
            "provider_error_class": classification.category,
            "provider_error_code": classification.code,
            "call_state": classification.call_state,
            "provider_call_count": 1,
            "profile_disposition": classification.profile_disposition,
            "latency_seconds": latency_seconds,
            "received_at_utc": completed_at_utc,
        }
    )
    return result


def derive_phase1_4_disposition(profile_results: Sequence[Mapping[str, Any]]) -> str:
    """Derive the top-level result only from per-profile evidence."""

    if any(item.get("call_state") == "uncertain_after_send" for item in profile_results):
        return "phase1_4_uncertain_after_send"
    if any(
        item.get("server_schema_acceptance_status") == "rejected"
        for item in profile_results
    ):
        return "phase1_4_live_schema_rejected_one_or_both_profiles"
    accepted = [
        item
        for item in profile_results
        if item.get("server_schema_acceptance_status") == "accepted"
    ]
    if len(accepted) == 2:
        if all(
            item.get("profile_disposition") == "accepted_and_fully_validated"
            for item in accepted
        ):
            return "phase1_4_live_schema_acceptance_confirmed_both_profiles"
        return "phase1_4_live_schema_acceptance_confirmed_with_validation_failure"
    if len(accepted) == 1:
        return "phase1_4_live_schema_acceptance_confirmed_one_profile"
    return "phase1_4_inconclusive_operational_failure"


def _persist_processed_response(model_dir: Path, processed: Mapping[str, Any]) -> None:
    if processed.get("parsed") is not None:
        _atomic_write_private_json(
            model_dir / "response.parsed.json", processed["parsed"]
        )
    _atomic_write_private_json(model_dir / "validation.json", processed["validation"])
    if processed.get("ledger") is not None:
        _atomic_write_private_json(
            model_dir / "materialised-ledger.json", processed["ledger"]
        )


def _write_response_observation(
    model_dir: Path,
    profile: Mapping[str, Any],
    observation: LiveObservation,
    raw: bytes,
    *,
    latency_seconds: float,
    received_at_utc: str,
) -> None:
    _atomic_write_private_bytes(model_dir / "response.raw.txt", raw)
    _atomic_write_private_json(
        model_dir / "usage.json",
        {"artifact_evidence": "observed", "usage": dict(observation.usage)},
    )
    _atomic_write_private_json(
        model_dir / "response-metadata.json",
        {
            "artifact_evidence": "observed",
            "profile_id": profile["profile_id"],
            "requested_model_id": profile["model"],
            "returned_model_id": observation.returned_model_id,
            "provider_response_id": observation.provider_response_id,
            "finish_reason": observation.finish_reason,
            "latency_seconds": latency_seconds,
            "local_receive_timestamp_utc": received_at_utc,
            "raw_response_sha256": sha256_bytes(raw),
            "raw_response_byte_length": len(raw),
            "request_headers_preserved": False,
            "authentication_metadata_preserved": False,
            "hidden_reasoning_preserved": False,
            "artifact_inventory": {
                "response.raw.txt": "observed",
                "response.parsed.json": "derived",
                "validation.json": "derived",
                "materialised-ledger.json": "derived",
                "usage.json": "observed",
            },
        },
    )


def _write_error_observation(
    model_dir: Path,
    profile: Mapping[str, Any],
    classification: ErrorClassification,
    *,
    latency_seconds: float,
    completed_at_utc: str,
) -> None:
    _atomic_write_private_json(
        model_dir / "response-metadata.json",
        {
            "artifact_evidence": "observed",
            "profile_id": profile["profile_id"],
            "requested_model_id": profile["model"],
            "provider_response_received": False,
            "provider_error_class": classification.category,
            "provider_error_code": classification.code,
            "sanitised_bounded_message": classification.message,
            "local_error_timestamp_utc": completed_at_utc,
            "latency_seconds": latency_seconds,
            "request_headers_preserved": False,
            "authentication_metadata_preserved": False,
            "stack_frame_locals_preserved": False,
        },
    )


def _not_attempted_result(
    profile: Mapping[str, Any], reason: str
) -> dict[str, Any]:
    result = _blank_profile_result(profile)
    result.update(
        {
            "call_state": "not_attempted_due_to_global_failure",
            "profile_disposition": "not_attempted_due_to_global_failure",
            "provider_error_class": reason,
        }
    )
    return result


def _operator_record(output_dir: Path) -> dict[str, Any]:
    path = output_dir / "operator-record.json"
    if not os.path.lexists(path):
        return {}
    value = _load_strict_json_file(path, "operator record")
    if not isinstance(value, dict):
        raise ProbeError("operator record is not an object")
    return value


def _render_report(
    output_dir: Path,
    summary: Mapping[str, Any],
    verification: Mapping[str, Any],
) -> bytes:
    run = _load_strict_json_file(output_dir / "run-manifest.json", "run manifest")
    prompt = _load_strict_json_file(
        output_dir / "prompt-manifest.json", "prompt manifest"
    )
    live = _load_strict_json_file(
        output_dir / "live-probe-manifest.json", "live probe manifest"
    )
    operator = _operator_record(output_dir)
    final_commit = operator.get("final_commit", "pending_until_final_commit")
    final_parent = operator.get("final_parent", run["git_head_at_prepare"])
    test_records = operator.get("test_records", [])
    lines = [
        "# Proposition ledger Phase 1.4 xAI live-schema probe report",
        "",
        "Artifact evidence: derived from the frozen synthetic inputs, durable call ledger, observed provider metadata, and saved response bytes.",
        "",
        "## Disposition",
        "",
        f"- Overall: `{summary['phase1_4_disposition']}`",
        f"- Correction-run provider-call budget: `{summary['provider_call_budget']}`",
        f"- Correction-run provider-call count: `{summary['provider_call_count']}`",
        f"- Prior malformed-envelope run calls: `{run['prior_phase1_4_provider_call_count']}`",
        f"- Diagnosis calls: `{run['diagnostic_provider_call_count']}`",
        f"- Cumulative xAI inference calls through this correction: `{run['prior_phase1_4_provider_call_count'] + run['diagnostic_provider_call_count'] + summary['provider_call_count']}`",
        "- Automatic retries across all three runs: `0`",
        f"- Verify-only: `{verification.get('status', 'not_yet_run')}`",
        f"- Deterministic saved-byte reprocessing: `{verification.get('deterministic_reprocessing_status', 'not_yet_run')}`",
        f"- Checksum verification: `{verification.get('checksum_status', 'not_yet_run')}`",
        "",
        "## Git and paths",
        "",
        f"- Source commit: `{run['git_source_commit']}`",
        f"- Source parent: `{run['git_source_parent']}`",
        f"- Git HEAD at preparation (correction base): `{run['git_head_at_prepare']}`",
        f"- Corrected live-probe tool SHA-256 at preparation: `{run.get('live_probe_tool_sha256_at_prepare', 'legacy_not_recorded')}`",
        f"- Request-contract revision: `{run['request_contract_revision']}`",
        f"- Correction reason: `{run['correction_reason']}`",
        f"- `origin/master` recorded at setup: `{run['origin_master_at_setup']}`",
        f"- Final commit: `{final_commit}`",
        f"- Final first parent: `{final_parent}`",
        f"- Branch: `{run['git_branch']}`",
        f"- Worktree: `{PROJECT_DIR}`",
        f"- Private run: `{output_dir}`",
        "",
        "## Frozen hashes",
        "",
        f"- Canonical schema version: `{preflight.EXPECTED_CANONICAL_VERSION}`",
        f"- Canonical schema SHA-256: `{prompt['canonical_schema_file_sha256']}`",
        f"- Provider schema version: `{preflight.EXPECTED_CANONICAL_VERSION}`",
        f"- Provider schema SHA-256: `{prompt['provider_schema_sha256']}`",
        f"- Synthetic case SHA-256: `{prompt['synthetic_case_file_sha256']}`",
        f"- System prompt SHA-256: `{prompt['system_prompt_sha256']}`",
        f"- User payload SHA-256: `{prompt['user_payload_sha256']}`",
        f"- Complete message-array SHA-256: `{prompt['complete_message_array_sha256']}`",
        f"- Tool-choice transport policy: `{live.get('tool_choice_transport_policy', 'legacy_explicit_none')}`",
        "",
        "## Per-profile observations",
        "",
    ]
    for result in summary["profiles"]:
        lines.extend(
            [
                f"### {result['profile_id']}",
                "",
                f"- Request/call state: `{result['call_state']}`; attempted `{str(result['request_attempted']).lower()}`; count `{result['provider_call_count']}`",
                f"- Server-schema acceptance: `{result['server_schema_acceptance_status']}`",
                f"- Strict JSON: `{result['strict_json_status']}`",
                f"- Provider schema: `{result['provider_schema_validation_status']}`",
                f"- Intended canonical schema: `{result['canonical_validation_status']}`",
                f"- Binding: `{result['binding_validation_status']}`",
                f"- Evidence spans: `{result['evidence_span_validation_status']}`",
                f"- Semantic references: `{result['semantic_reference_validation_status']}`",
                f"- Materialisation: `{result['materialisation_status']}`",
                f"- Persisted-ledger validation: `{result['persisted_ledger_validation_status']}`",
                f"- Semantic smoke: `{result['semantic_smoke_status']}` (not a comparative score)",
                f"- Profile disposition: `{result['profile_disposition']}`",
                f"- Returned model: `{result.get('returned_model_id')}`",
                f"- Finish reason: `{result.get('finish_reason')}`",
                f"- Usage: `{json.dumps(result.get('usage'), sort_keys=True)}`",
                f"- Latency seconds: `{result.get('latency_seconds')}`",
                f"- Raw response SHA-256: `{result.get('raw_response_sha256')}`",
                f"- Sanitised provider error class/code: `{result.get('provider_error_class')}` / `{result.get('provider_error_code')}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Environment and one-attempt enforcement",
            "",
            "- Pinned CPython and all locked packages passed exact-environment verification and `pip check` before live execution.",
            "- Installed `xai-sdk==1.19.0` source-file and source-tree hashes matched the Phase 1.3 environment record.",
            "- SDK retry behaviour was inspected. Its default retry service config was replaced with `grpc.enable_retries=0` and an empty service config; application retries were zero.",
            "- No fallback, streaming, tools, search, code execution, or unfrozen sampling field was enabled.",
            "",
            "## Test records",
            "",
        ]
    )
    if test_records:
        for record in test_records:
            lines.append(
                f"- `{record.get('command')}` — {record.get('result')} "
                f"(passed={record.get('passed', 0)}, failed={record.get('failed', 0)}, "
                f"skipped={record.get('skipped', 0)})"
            )
    else:
        lines.append("- Pending final operator test record.")
    lines.extend(
        [
            "",
            "## Safety confirmations",
            "",
            f"- Provider retries: `{summary['provider_retries']}`.",
            f"- Model-list calls: `{summary['model_list_calls']}`; OpenAI calls: `{summary['openai_calls']}`; X calls: `{summary['x_calls']}`.",
            f"- Real conversations read: `{summary['real_conversation_records_read']}`; held-out records read: `{summary['held_out_records_read']}`; production files read: `{summary['production_files_read']}`.",
            f"- Sealed clean prefixes read: `{summary['sealed_clean_prefixes_read']}`; both remained sealed.",
            "- No power analysis, model comparison, development pilot, corpus experiment, service action, merge, deployment, real ledger, real summary, or reply decision occurred.",
            "- Production and all services were untouched.",
            "- Development-pilot, real-corpus, held-out, and production-integration authorisation all remain `false`.",
            "",
            "This result tests only live server schema acceptance and mandatory local boundaries. It does not establish proposition-ledger effectiveness, and a single synthetic failure would not show that the ledger concept is unsound. A later pilot requires a separately frozen protocol.",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def _finalize_run(
    output_dir: Path,
    profile_results: Sequence[Mapping[str, Any]],
    *,
    verification_status: str,
    verified_responses: int,
) -> dict[str, Any]:
    ledger = _load_call_ledger(output_dir)
    if len(profile_results) != len(PROFILES):
        raise ProbeError("final result does not contain exactly two profiles")
    for profile, entry, result in zip(PROFILES, ledger["entries"], profile_results):
        expected_result_state = (
            "uncertain_after_send" if entry["state"] == "sending" else entry["state"]
        )
        if (
            result.get("profile_id") != profile["profile_id"]
            or result.get("model") != profile["model"]
            or result.get("provider_call_count") != entry["provider_call_count"]
            or result.get("call_state") != expected_result_state
        ):
            raise ProbeError("final profile result differs from the call ledger")
    if sum(item["provider_call_count"] for item in profile_results) != ledger[
        "provider_call_count"
    ]:
        raise ProbeError("final profile call counts differ from call ledger")
    disposition = derive_phase1_4_disposition(profile_results)
    response_count = sum(
        item.get("provider_response_received") is True for item in profile_results
    )
    if response_count == 0:
        deterministic_status = "not_applicable_no_saved_response"
    elif verified_responses == response_count:
        deterministic_status = "passed"
    else:
        deterministic_status = "incomplete"
    summary = {
        "artifact_evidence": "derived",
        "summary_format": "proposition-ledger-phase1.4-result-summary-v1",
        "phase1_4_disposition": disposition,
        "provider_call_budget": PROVIDER_CALL_BUDGET,
        "provider_call_count": ledger["provider_call_count"],
        "profiles": copy.deepcopy(list(profile_results)),
        "provider_retries": 0,
        "model_list_calls": 0,
        "openai_calls": 0,
        "x_calls": 0,
        "search_calls": 0,
        "tool_calls": 0,
        "real_conversation_records_read": 0,
        "held_out_records_read": 0,
        "production_files_read": 0,
        "sealed_clean_prefixes_read": 0,
        "model_comparison_performed": False,
        "winner_selected": False,
        "power_analysis_performed": False,
        "development_pilot_run": False,
        "merged": False,
        "deployed": False,
        "development_pilot_authorised": False,
        "real_corpus_use_authorised": False,
        "held_out_use_authorised": False,
        "production_integration_authorised": False,
        "verify_only_status": verification_status,
        "deterministic_response_reprocessing_status": deterministic_status,
    }
    verification = {
        "artifact_evidence": "derived",
        "status": verification_status,
        "checksum_status": "passed",
        "call_ledger_unchanged": verification_status == "passed",
        "provider_call_count_unchanged": verification_status == "passed",
        "saved_responses_reprocessed": verified_responses,
        "deterministic_reprocessing_status": deterministic_status,
        "provider_calls_during_verification": 0,
        "network_transport_instantiated": False,
        "verified_at_utc": utc_now() if verification_status == "passed" else None,
    }
    _atomic_write_private_json(output_dir / "result-summary.json", summary)
    _atomic_write_private_json(output_dir / "validation.json", verification)
    _atomic_write_private_bytes(
        output_dir / "phase1.4-report.md",
        _render_report(output_dir, summary, verification),
    )
    run_manifest = _load_strict_json_file(
        output_dir / "run-manifest.json", "run manifest"
    )
    run_manifest["provider_call_count"] = ledger["provider_call_count"]
    run_manifest["phase1_4_disposition"] = disposition
    run_manifest["completed_at_utc"] = utc_now()
    _atomic_write_private_json(output_dir / "run-manifest.json", run_manifest)
    _write_checksums(output_dir)
    verify_checksums(output_dir)
    audit_private_run_permissions(output_dir)
    audit_private_run_inventory(output_dir)
    return summary


def _validate_prepared_run(
    output_dir: Path,
    tracked: Mapping[str, Any],
    *,
    allow_mutable_call_ledger: bool = False,
) -> dict[str, Any]:
    recovery_ledger = (
        _load_call_ledger(output_dir) if allow_mutable_call_ledger else None
    )
    checksum = verify_checksums(
        output_dir,
        allowed_mismatches=(
            frozenset({"call-ledger.json", "run-manifest.json"})
            if allow_mutable_call_ledger
            else frozenset()
        ),
        allowed_unlisted=(
            _recovery_unlisted_paths(recovery_ledger)
            if recovery_ledger is not None
            else frozenset()
        ),
    )
    stored_schema = _load_strict_json_file(
        output_dir / "provider-schema.json", "stored provider schema"
    )
    if canonical_json_bytes(stored_schema) != canonical_json_bytes(
        tracked["provider_schema"]
    ):
        raise ProbeError("stored provider schema differs from regenerated schema")
    prompt = _load_strict_json_file(
        output_dir / "prompt-manifest.json", "prompt manifest"
    )
    for name, digest in tracked["hashes"].items():
        if prompt.get(name) != digest:
            raise ProbeError(f"stored prompt hash mismatch: {name}")
    expected_plan = make_call_plan(tracked)
    ledger = _load_call_ledger(output_dir)
    if recovery_ledger is not None and canonical_json_bytes(
        ledger
    ) != canonical_json_bytes(recovery_ledger):
        raise ProbeError("call ledger changed during prepared-run validation")
    for actual, expected in zip(ledger["entries"], expected_plan["entries"]):
        for name in (
            "order",
            "profile_id",
            "provider",
            "model",
            "request_contract_revision",
            "call_identity",
        ):
            if actual.get(name) != expected.get(name):
                raise ProbeError(f"call ledger plan mismatch: {name}")
    run_manifest = _load_strict_json_file(
        output_dir / "run-manifest.json", "run manifest"
    )
    expected_run_fields = {
        "git_source_commit": SOURCE_COMMIT,
        "git_source_parent": SOURCE_PARENT,
        "git_source_subject": SOURCE_SUBJECT,
        "request_contract_revision": REQUEST_CONTRACT_REVISION,
        "correction_base_commit": CORRECTION_BASE_COMMIT,
        "correction_reason": "omit_tool_choice_when_tools_are_empty",
        "prior_phase1_4_private_run": str(PRIOR_PHASE14_PRIVATE_RUN),
        "prior_phase1_4_provider_call_count": PRIOR_PHASE14_PROVIDER_CALL_COUNT,
        "diagnosis_private_run": str(DIAGNOSIS_PRIVATE_RUN),
        "diagnostic_provider_call_count": DIAGNOSTIC_PROVIDER_CALL_COUNT,
        "git_branch": "research/proposition-ledger-phase1.4-xai-live-probe",
        "private_run_path": str(output_dir),
        "provider_call_budget": PROVIDER_CALL_BUDGET,
        "synthetic_only": True,
        "development_pilot_authorised": False,
        "real_corpus_use_authorised": False,
        "held_out_use_authorised": False,
        "production_integration_authorised": False,
    }
    if (
        not isinstance(run_manifest, dict)
        or run_manifest.get("git_head_at_prepare") not in ALLOWED_PREPARE_HEADS
        or any(
            run_manifest.get(name) != value
            for name, value in expected_run_fields.items()
        )
    ):
        raise ProbeError("stored run manifest differs from the frozen run identity")
    stored_tool_hash = run_manifest.get("live_probe_tool_sha256_at_prepare")
    current_tool_hash = sha256_bytes(
        _read_regular_bytes(Path(__file__), "live probe tool")
    )
    if stored_tool_hash not in {None, current_tool_hash} or (
        run_manifest.get("git_head_at_prepare") == CORRECTION_BASE_COMMIT
        and stored_tool_hash != current_tool_hash
    ):
        raise ProbeError("stored live-probe tool hash differs")
    if run_manifest.get("provider_call_count") not in {
        0,
        ledger["provider_call_count"],
    }:
        raise ProbeError("stored run-manifest call count is inconsistent")
    live_manifest = _load_strict_json_file(
        output_dir / "live-probe-manifest.json", "live probe manifest"
    )
    expected_live_fields = {
        "profiles": list(PROFILES),
        "maximum_output_tokens": MAX_OUTPUT_TOKENS,
        "store_messages": STORE_MESSAGES,
        "request_contract_revision": REQUEST_CONTRACT_REVISION,
        "client_timeout_seconds": CLIENT_TIMEOUT_SECONDS,
        "no_retry_channel_options": [list(item) for item in NO_RETRY_CHANNEL_OPTIONS],
        "sdk_retry_behavior_inspected": True,
        "provider_schema_sha256": tracked["hashes"]["provider_schema_sha256"],
    }
    if not isinstance(live_manifest, dict) or any(
        live_manifest.get(name) != value
        for name, value in expected_live_fields.items()
    ):
        raise ProbeError("stored live-probe manifest differs from frozen settings")
    tool_choice_policy = live_manifest.get("tool_choice_transport_policy")
    if tool_choice_policy not in {None, "omitted_when_tools_empty"} or (
        run_manifest.get("git_head_at_prepare") == CORRECTION_BASE_COMMIT
        and tool_choice_policy != "omitted_when_tools_empty"
    ):
        raise ProbeError("stored tool-choice transport policy differs")
    if live_manifest.get("authorisation") != {
        "development_pilot_authorised": False,
        "real_corpus_use_authorised": False,
        "held_out_use_authorised": False,
        "production_integration_authorised": False,
    }:
        raise ProbeError("stored live-probe authorisation boundary differs")
    for profile in PROFILES:
        metadata = _load_strict_json_file(
            output_dir / profile["model"] / "request-metadata.json",
            f"{profile['model']} request metadata",
        )
        if (
            metadata.get("artifact_evidence") != "planned"
            or metadata.get("profile_id") != profile["profile_id"]
            or metadata.get("provider") != "xAI"
            or metadata.get("request") != request_representation(profile, tracked)
            or metadata.get("provider_schema_sha256")
            != tracked["hashes"]["provider_schema_sha256"]
            or metadata.get("system_prompt_sha256")
            != tracked["hashes"]["system_prompt_sha256"]
            or metadata.get("user_payload_sha256")
            != tracked["hashes"]["user_payload_sha256"]
            or metadata.get("complete_message_array_sha256")
            != tracked["hashes"]["complete_message_array_sha256"]
            or metadata.get("credentials_included") is not False
            or metadata.get("request_headers_preserved") is not False
        ):
            raise ProbeError(f"{profile['model']} request metadata mismatch")
    audit_private_run_permissions(output_dir)
    audit_private_run_inventory(output_dir)
    return {"checksum": checksum, "ledger": ledger}


def execute_live_probe(
    output: str | Path,
    *,
    confirm_provider_call_budget: int | None,
    transport_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Attempt each frozen profile at most once, in order, with durable evidence."""

    if confirm_provider_call_budget != PROVIDER_CALL_BUDGET:
        raise ProbeError("--confirm-provider-call-budget must be exactly 2")
    if not _credential_present():
        return {
            "status": "live_probe_not_run_missing_xai_api_key",
            "phase1_4_disposition": "phase1_4_not_run_missing_xai_api_key",
            "provider_call_count": 0,
        }
    _remove_unrelated_credentials()
    output_dir = _require_real_directory(output, "private run")
    with _exclusive_execution_lock(output_dir):
        return _execute_live_probe_locked(output_dir, transport_factory)


def _execute_live_probe_locked(
    output_dir: Path,
    transport_factory: Callable[[], Any] | None,
) -> dict[str, Any]:
    """Execute the call plan while the caller holds the run-directory lock."""

    tracked = validate_tracked_inputs()
    verify_pinned_environment()
    compile_local_requests(tracked)
    ledger = _load_call_ledger(output_dir)
    states = [entry["state"] for entry in ledger["entries"]]
    checksum_declares_finalized = _checksum_declares_finalized_run(output_dir)
    prepared = _validate_prepared_run(
        output_dir,
        tracked,
        allow_mutable_call_ledger=(
            any(state != "planned" for state in states)
            and not checksum_declares_finalized
        ),
    )
    ledger = prepared["ledger"]
    persisted_schema = _load_strict_json_file(
        PERSISTED_LEDGER_SCHEMA_PATH, "persisted ledger schema"
    )
    if "sending" in states:
        sending_index = states.index("sending")
        _transition_call(
            output_dir,
            sending_index,
            "uncertain_after_send",
            completed_at_utc=utc_now(),
            recovery_reason="prior process ended without definite response evidence",
        )
        for index in range(sending_index + 1, len(PROFILES)):
            if ledger["entries"][index]["state"] == "planned":
                _transition_call(
                    output_dir,
                    index,
                    "not_attempted_due_to_global_failure",
                    completed_at_utc=utc_now(),
                    stop_reason="uncertain_prior_call",
                )
        recovered_ledger = _load_call_ledger(output_dir)
        persisted_schema = _load_strict_json_file(
            PERSISTED_LEDGER_SCHEMA_PATH, "persisted ledger schema"
        )
        results, reprocessed = _reconstruct_results_after_interruption(
            output_dir,
            recovered_ledger,
            tracked,
            persisted_schema,
            persist_missing_derived=True,
        )
        return _finalize_run(
            output_dir,
            results,
            verification_status="not_yet_run",
            verified_responses=reprocessed,
        )

    if checksum_declares_finalized and any(
        state != "planned" for state in states
    ):
        raise ProbeError("refusing to repeat a finalized provider call plan")

    if "response_received" in states:
        recovered, _ = _reconstruct_results_after_interruption(
            output_dir,
            ledger,
            tracked,
            persisted_schema,
            persist_missing_derived=True,
        )
        for index, state in enumerate(states):
            if state == "response_received":
                disposition = recovered[index]["profile_disposition"]
                target_state = (
                    "fully_validated"
                    if disposition == "accepted_and_fully_validated"
                    else "terminal_validation_failure"
                )
                _transition_call(
                    output_dir,
                    index,
                    target_state,
                    validation_disposition=disposition,
                    validation_completed_at_utc=utc_now(),
                )
        ledger = _load_call_ledger(output_dir)
        states = [entry["state"] for entry in ledger["entries"]]

    global_failure_categories = {
        "authentication_failure",
        "authorisation_failure",
        "billing_failure",
        "rate_limit_or_account_quota_failure",
        "transport_failure_before_send",
        "global_transport_failure",
    }
    stop_after_index: int | None = None
    stop_reason: str | None = None
    for index, entry in enumerate(ledger["entries"]):
        if entry["state"] == "not_attempted_due_to_global_failure":
            stop_after_index = index
            stop_reason = str(entry.get("stop_reason") or "global_failure")
            break
        if entry["state"] == "uncertain_after_send":
            stop_after_index = index
            stop_reason = "uncertain_prior_call"
            break
        if (
            entry["state"] == "provider_error_received"
            and entry.get("provider_error_class") in global_failure_categories
        ):
            stop_after_index = index
            stop_reason = str(entry["provider_error_class"])
            break
        if (
            entry["state"] == "terminal_validation_failure"
            and entry.get("validation_disposition")
            == "local_validation_harness_failure"
        ):
            stop_after_index = index
            stop_reason = "local_validation_harness_failure"
            break
    if stop_after_index is not None:
        for index in range(stop_after_index + 1, len(PROFILES)):
            if ledger["entries"][index]["state"] == "planned":
                _transition_call(
                    output_dir,
                    index,
                    "not_attempted_due_to_global_failure",
                    completed_at_utc=utc_now(),
                    stop_reason=stop_reason,
                )
        ledger = _load_call_ledger(output_dir)
        results, reprocessed = _reconstruct_results_after_interruption(
            output_dir,
            ledger,
            tracked,
            persisted_schema,
            persist_missing_derived=True,
        )
        return _finalize_run(
            output_dir,
            results,
            verification_status="not_yet_run",
            verified_responses=reprocessed,
        )

    pending_indices = [
        index
        for index, entry in enumerate(ledger["entries"])
        if entry["state"] == "planned"
    ]
    if not pending_indices:
        results, reprocessed = _reconstruct_results_after_interruption(
            output_dir,
            ledger,
            tracked,
            persisted_schema,
            persist_missing_derived=True,
        )
        return _finalize_run(
            output_dir,
            results,
            verification_status="not_yet_run",
            verified_responses=reprocessed,
        )
    if pending_indices not in ([0, 1], [1]):
        raise ProbeError("planned call ordering is inconsistent with durable evidence")

    profile_results: list[dict[str, Any]] = []
    successful_reprocess_count = 0
    if pending_indices == [1]:
        prior_results, successful_reprocess_count = (
            _reconstruct_results_after_interruption(
                output_dir,
                ledger,
                tracked,
                persisted_schema,
                persist_missing_derived=True,
            )
        )
        profile_results.append(prior_results[0])

    factory = transport_factory or _create_real_transport
    try:
        transport = factory()
    except Exception:
        reason = "local_transport_initialization_failure"
        for index in pending_indices:
            _transition_call(
                output_dir,
                index,
                "not_attempted_due_to_global_failure",
                completed_at_utc=utc_now(),
                stop_reason=reason,
            )
        failed_ledger = _load_call_ledger(output_dir)
        results, reprocessed = _reconstruct_results_after_interruption(
            output_dir,
            failed_ledger,
            tracked,
            persisted_schema,
            persist_missing_derived=True,
        )
        return _finalize_run(
            output_dir,
            results,
            verification_status="not_yet_run",
            verified_responses=reprocessed,
        )

    stop_reason: str | None = None
    try:
        for index in pending_indices:
            profile = PROFILES[index]
            if stop_reason is not None:
                _transition_call(
                    output_dir,
                    index,
                    "not_attempted_due_to_global_failure",
                    completed_at_utc=utc_now(),
                    stop_reason=stop_reason,
                )
                profile_results.append(_not_attempted_result(profile, stop_reason))
                continue
            started_at = utc_now()
            _transition_call(
                output_dir, index, "sending", started_at_utc=started_at
            )
            began = time.monotonic()
            try:
                observation = transport.sample(profile, tracked)
            except BaseException as exc:  # includes interruption after possible send
                latency = round(time.monotonic() - began, 6)
                completed_at = utc_now()
                classification = classify_provider_error(exc)
                _write_error_observation(
                    output_dir / profile["model"],
                    profile,
                    classification,
                    latency_seconds=latency,
                    completed_at_utc=completed_at,
                )
                _transition_call(
                    output_dir,
                    index,
                    classification.call_state,
                    completed_at_utc=completed_at,
                    provider_error_class=classification.category,
                    provider_error_code=classification.code,
                    sanitised_bounded_message=classification.message,
                )
                profile_results.append(
                    _profile_result_from_error(
                        profile,
                        classification,
                        latency_seconds=latency,
                        completed_at_utc=completed_at,
                    )
                )
                if classification.stops_following_calls:
                    stop_reason = classification.category
                continue

            latency = round(time.monotonic() - began, 6)
            received_at = utc_now()
            raw = observation.raw_text.encode("utf-8", errors="strict")
            model_dir = output_dir / profile["model"]
            _write_response_observation(
                model_dir,
                profile,
                observation,
                raw,
                latency_seconds=latency,
                received_at_utc=received_at,
            )
            _transition_call(
                output_dir,
                index,
                "response_received",
                completed_at_utc=received_at,
                raw_response_sha256=sha256_bytes(raw),
                provider_response_id=observation.provider_response_id,
                returned_model_id=observation.returned_model_id,
                finish_reason=observation.finish_reason,
            )
            try:
                processed = process_response_twice(
                    raw,
                    case=tracked["case"],
                    canonical_schema=tracked["canonical_schema"],
                    provider_schema=tracked["provider_schema"],
                    persisted_ledger_schema=persisted_schema,
                )
                _persist_processed_response(model_dir, processed)
                profile_result = _profile_result_from_validation(
                    profile,
                    observation,
                    processed["validation"],
                    latency_seconds=latency,
                    received_at_utc=received_at,
                )
                _transition_call(
                    output_dir,
                    index,
                    profile_result["call_state"],
                    validation_disposition=profile_result["profile_disposition"],
                    validation_completed_at_utc=utc_now(),
                )
                profile_results.append(profile_result)
                successful_reprocess_count += 1
            except Exception as exc:
                safe = _sanitize_error_message(
                    f"local_validation_harness_failure:{type(exc).__name__}"
                )
                _transition_call(
                    output_dir,
                    index,
                    "terminal_validation_failure",
                    validation_disposition="local_validation_harness_failure",
                    validation_completed_at_utc=utc_now(),
                    sanitised_bounded_message=safe,
                )
                item = _blank_profile_result(profile)
                item.update(
                    {
                        "request_attempted": True,
                        "request_definitely_sent": True,
                        "provider_response_received": True,
                        "server_schema_acceptance_status": "accepted",
                        "call_state": "terminal_validation_failure",
                        "provider_call_count": 1,
                        "profile_disposition": "accepted_but_local_validation_harness_failed",
                        "provider_error_class": "local_validation_harness_failure",
                        "returned_model_id": observation.returned_model_id,
                        "provider_response_id": observation.provider_response_id,
                        "finish_reason": observation.finish_reason,
                        "usage": dict(observation.usage),
                        "latency_seconds": latency,
                        "received_at_utc": received_at,
                        "raw_response_sha256": sha256_bytes(raw),
                    }
                )
                profile_results.append(item)
                stop_reason = "local_validation_harness_failure"
    finally:
        try:
            transport.close()
        except Exception:
            pass

    summary = _finalize_run(
        output_dir,
        profile_results,
        verification_status="not_yet_run",
        verified_responses=successful_reprocess_count,
    )
    return summary


def _compare_saved_json(path: Path, expected: Any, label: str) -> None:
    saved = _load_strict_json_file(path, label)
    if canonical_json_bytes(saved) != canonical_json_bytes(expected):
        raise ProbeError(f"saved derived artifact mismatch: {label}")


def _safe_nonnegative_number(value: Any, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise ProbeError(f"{label} is not a finite non-negative number")
    return float(value)


def _load_response_observation(
    model_dir: Path,
    profile: Mapping[str, Any],
    entry: Mapping[str, Any],
) -> tuple[bytes, Mapping[str, Any], Mapping[str, Any]]:
    """Load and cross-check one complete durable provider response."""

    raw = _read_regular_bytes(model_dir / "response.raw.txt", "saved response")
    metadata = _load_strict_json_file(
        model_dir / "response-metadata.json",
        f"{profile['model']} response metadata",
    )
    usage_record = _load_strict_json_file(
        model_dir / "usage.json", f"{profile['model']} usage"
    )
    expected_metadata_fields = {
        "artifact_evidence",
        "artifact_inventory",
        "authentication_metadata_preserved",
        "finish_reason",
        "hidden_reasoning_preserved",
        "latency_seconds",
        "local_receive_timestamp_utc",
        "profile_id",
        "provider_response_id",
        "raw_response_byte_length",
        "raw_response_sha256",
        "request_headers_preserved",
        "requested_model_id",
        "returned_model_id",
    }
    if not isinstance(metadata, dict) or set(metadata) != expected_metadata_fields:
        raise ProbeError(f"{profile['model']} response metadata fields differ")
    if not isinstance(usage_record, dict) or set(usage_record) != {
        "artifact_evidence",
        "usage",
    }:
        raise ProbeError(f"{profile['model']} usage fields differ")
    expected_inventory = {
        "response.raw.txt": "observed",
        "response.parsed.json": "derived",
        "validation.json": "derived",
        "materialised-ledger.json": "derived",
        "usage.json": "observed",
    }
    bounded_identifiers = (
        (metadata.get("returned_model_id"), 128),
        (metadata.get("provider_response_id"), 256),
        (metadata.get("finish_reason"), 64),
    )
    if (
        metadata.get("artifact_evidence") != "observed"
        or usage_record.get("artifact_evidence") != "observed"
        or metadata.get("profile_id") != profile["profile_id"]
        or metadata.get("requested_model_id") != profile["model"]
        or metadata.get("raw_response_sha256") != sha256_bytes(raw)
        or metadata.get("raw_response_byte_length") != len(raw)
        or metadata.get("request_headers_preserved") is not False
        or metadata.get("authentication_metadata_preserved") is not False
        or metadata.get("hidden_reasoning_preserved") is not False
        or metadata.get("artifact_inventory") != expected_inventory
        or not isinstance(metadata.get("local_receive_timestamp_utc"), str)
        or not metadata.get("local_receive_timestamp_utc")
        or not isinstance(usage_record.get("usage"), dict)
        or any(
            value is not None
            and (
                not isinstance(value, str)
                or len(value) > limit
                or re.fullmatch(r"[A-Za-z0-9._:/-]+", value) is None
            )
            for value, limit in bounded_identifiers
        )
    ):
        raise ProbeError(f"{profile['model']} response metadata is inconsistent")
    _safe_nonnegative_number(
        metadata.get("latency_seconds"), f"{profile['model']} latency"
    )
    if entry["state"] not in {"sending", "uncertain_after_send"}:
        comparisons = {
            "raw_response_sha256": metadata["raw_response_sha256"],
            "provider_response_id": metadata["provider_response_id"],
            "returned_model_id": metadata["returned_model_id"],
            "finish_reason": metadata["finish_reason"],
            "completed_at_utc": metadata["local_receive_timestamp_utc"],
        }
        if any(entry.get(name) != value for name, value in comparisons.items()):
            raise ProbeError(
                f"{profile['model']} response metadata differs from call ledger"
            )
    return raw, metadata, usage_record["usage"]


def _load_error_observation(
    model_dir: Path,
    profile: Mapping[str, Any],
    entry: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Load and cross-check one bounded provider-error observation."""

    metadata = _load_strict_json_file(
        model_dir / "response-metadata.json",
        f"{profile['model']} provider error metadata",
    )
    expected_fields = {
        "artifact_evidence",
        "authentication_metadata_preserved",
        "latency_seconds",
        "local_error_timestamp_utc",
        "profile_id",
        "provider_error_class",
        "provider_error_code",
        "provider_response_received",
        "request_headers_preserved",
        "requested_model_id",
        "sanitised_bounded_message",
        "stack_frame_locals_preserved",
    }
    if not isinstance(metadata, dict) or set(metadata) != expected_fields:
        raise ProbeError(f"{profile['model']} provider error metadata fields differ")
    error_code = metadata.get("provider_error_code")
    if (
        metadata.get("artifact_evidence") != "observed"
        or metadata.get("profile_id") != profile["profile_id"]
        or metadata.get("requested_model_id") != profile["model"]
        or metadata.get("provider_response_received") is not False
        or metadata.get("request_headers_preserved") is not False
        or metadata.get("authentication_metadata_preserved") is not False
        or metadata.get("stack_frame_locals_preserved") is not False
        or metadata.get("sanitised_bounded_message")
        != "provider error detail withheld by credential boundary"
        or metadata.get("provider_error_class") not in PROVIDER_ERROR_CATEGORIES
        or (error_code is not None and error_code not in GRPC_STATUS_CODES)
        or not isinstance(metadata.get("local_error_timestamp_utc"), str)
        or not metadata.get("local_error_timestamp_utc")
    ):
        raise ProbeError(f"{profile['model']} provider error evidence is inconsistent")
    if entry["state"] == "provider_error_received" or entry.get(
        "provider_error_class"
    ) is not None:
        comparisons = {
            "provider_error_class": metadata["provider_error_class"],
            "provider_error_code": metadata["provider_error_code"],
            "sanitised_bounded_message": metadata["sanitised_bounded_message"],
            "completed_at_utc": metadata["local_error_timestamp_utc"],
        }
        if any(entry.get(name) != value for name, value in comparisons.items()):
            raise ProbeError(
                f"{profile['model']} provider error metadata differs from call ledger"
            )
    _safe_nonnegative_number(
        metadata.get("latency_seconds"), f"{profile['model']} error latency"
    )
    return metadata


def _verify_or_persist_processed(
    model_dir: Path,
    processed: Mapping[str, Any],
    *,
    persist_missing: bool,
) -> None:
    """Compare saved derived artifacts, creating only interrupted missing ones."""

    artifacts = (
        ("validation.json", processed["validation"], True),
        ("response.parsed.json", processed.get("parsed"), False),
        ("materialised-ledger.json", processed.get("ledger"), False),
    )
    for name, expected, always_required in artifacts:
        path = model_dir / name
        present = os.path.lexists(path)
        should_exist = always_required or expected is not None
        if present and not should_exist:
            raise ProbeError(f"unexpected saved derived artifact: {name}")
        if present:
            _compare_saved_json(path, expected, name)
        elif should_exist and persist_missing:
            _atomic_write_private_json(path, expected)
        elif should_exist:
            raise ProbeError(f"missing saved derived artifact: {name}")


def _profile_result_from_saved_error(
    profile: Mapping[str, Any],
    entry: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    residual_uncommitted = (
        entry["state"] in {"sending", "uncertain_after_send"}
        and entry.get("provider_error_class") is None
    )
    category = (
        "unclassified_failure_after_send"
        if residual_uncommitted
        else str(metadata["provider_error_class"])
    )
    dispositions = {
        "server_schema_rejection": "server_rejected_schema",
        "model_profile_rejection": "server_rejected_profile",
        "authentication_failure": "authentication_or_authorisation_failed",
        "authorisation_failure": "authentication_or_authorisation_failed",
        "billing_failure": "authentication_or_authorisation_failed",
        "rate_limit_or_account_quota_failure": "rate_limited_or_quota_blocked",
        "transport_failure_before_send": "transport_failure_before_send",
        "global_transport_failure": "global_transport_failure",
        "timeout_after_possible_transmission": "uncertain_after_send",
        "unclassified_failure_after_send": "uncertain_after_send",
        "definite_provider_error": "definite_provider_error",
    }
    result = _blank_profile_result(profile)
    result.update(
        {
            "request_attempted": True,
            "request_definitely_sent": category
            not in {
                "transport_failure_before_send",
                "global_transport_failure",
                "timeout_after_possible_transmission",
                "unclassified_failure_after_send",
            },
            "server_schema_acceptance_status": (
                "rejected" if category == "server_schema_rejection" else "not_determined"
            ),
            "provider_error_class": category,
            "provider_error_code": metadata.get("provider_error_code"),
            "call_state": (
                "uncertain_after_send"
                if entry["state"] in {"sending", "uncertain_after_send"}
                else entry["state"]
            ),
            "provider_call_count": entry["provider_call_count"],
            "profile_disposition": dispositions.get(
                category, "definite_provider_error"
            ),
            "latency_seconds": metadata["latency_seconds"],
            "received_at_utc": metadata["local_error_timestamp_utc"],
        }
    )
    return result


def _reconstruct_results_after_interruption(
    output_dir: Path,
    ledger: Mapping[str, Any],
    tracked: Mapping[str, Any],
    persisted_schema: Mapping[str, Any],
    *,
    persist_missing_derived: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    """Derive every profile result from ledger-bound durable evidence."""

    results: list[dict[str, Any]] = []
    reprocessed = 0
    for index, profile in enumerate(PROFILES):
        entry = ledger["entries"][index]
        state = entry["state"]
        model_dir = output_dir / profile["model"]
        response_paths = [
            model_dir / "response.raw.txt",
            model_dir / "response-metadata.json",
            model_dir / "usage.json",
        ]
        derived_paths = [
            model_dir / "response.parsed.json",
            model_dir / "validation.json",
            model_dir / "materialised-ledger.json",
        ]
        response_presence = [os.path.lexists(path) for path in response_paths]
        derived_presence = [os.path.lexists(path) for path in derived_paths]
        metadata_is_error = False
        if response_presence[1]:
            possible_metadata = _load_strict_json_file(
                response_paths[1], f"{profile['model']} response metadata"
            )
            metadata_is_error = (
                isinstance(possible_metadata, Mapping)
                and possible_metadata.get("provider_response_received") is False
            )
        has_complete_response = all(response_presence) and not metadata_is_error
        if has_complete_response:
            raw, metadata, usage = _load_response_observation(
                model_dir, profile, entry
            )
            processed = process_response_twice(
                raw,
                case=tracked["case"],
                canonical_schema=tracked["canonical_schema"],
                provider_schema=tracked["provider_schema"],
                persisted_ledger_schema=persisted_schema,
            )
            _verify_or_persist_processed(
                model_dir,
                processed,
                persist_missing=persist_missing_derived,
            )
            observation = LiveObservation(
                raw_text=raw.decode("utf-8", errors="strict"),
                returned_model_id=metadata.get("returned_model_id"),
                provider_response_id=metadata.get("provider_response_id"),
                finish_reason=metadata.get("finish_reason"),
                usage=usage,
            )
            result = _profile_result_from_validation(
                profile,
                observation,
                processed["validation"],
                latency_seconds=float(metadata["latency_seconds"]),
                received_at_utc=str(metadata["local_receive_timestamp_utc"]),
            )
            if state in {"fully_validated", "terminal_validation_failure"}:
                ledger_disposition = entry.get("validation_disposition")
                if ledger_disposition == "local_validation_harness_failure":
                    result.update(
                        {
                            "call_state": "terminal_validation_failure",
                            "profile_disposition": (
                                "accepted_but_local_validation_harness_failed"
                            ),
                            "provider_error_class": (
                                "local_validation_harness_failure"
                            ),
                        }
                    )
                elif result["call_state"] != state:
                    raise ProbeError(
                        f"{profile['model']} validation result differs from call state"
                    )
                elif ledger_disposition != result["profile_disposition"]:
                    raise ProbeError(
                        f"{profile['model']} validation disposition differs from ledger"
                    )
            elif state == "response_received":
                result["call_state"] = "response_received"
            elif state in {"sending", "uncertain_after_send"}:
                result["call_state"] = "uncertain_after_send"
                result["profile_disposition"] = "uncertain_after_send"
                result["provider_error_class"] = (
                    "response_evidence_without_terminal_call_ledger_state"
                )
            else:
                raise ProbeError(
                    f"{profile['model']} response artifacts conflict with call state"
                )
            results.append(result)
            reprocessed += 1
            continue
        if any(response_presence) and not metadata_is_error:
            if state not in {"sending", "uncertain_after_send"}:
                raise ProbeError(
                    f"{profile['model']} has incomplete response observation"
                )
            if any(derived_presence):
                raise ProbeError(
                    f"{profile['model']} incomplete response has derived artifacts"
                )
            result = _blank_profile_result(profile)
            result.update(
                {
                    "request_attempted": True,
                    "call_state": "uncertain_after_send",
                    "provider_call_count": entry["provider_call_count"],
                    "profile_disposition": "uncertain_after_send",
                    "provider_error_class": "incomplete_residual_response_observation",
                }
            )
            results.append(result)
            continue
        if metadata_is_error:
            if response_presence[0] or response_presence[2] or any(derived_presence):
                raise ProbeError(f"{profile['model']} error has response artifacts")
            if state not in {"provider_error_received", "uncertain_after_send"}:
                raise ProbeError(
                    f"{profile['model']} error metadata conflicts with call state"
                )
            metadata = _load_error_observation(model_dir, profile, entry)
            results.append(_profile_result_from_saved_error(profile, entry, metadata))
            continue
        if state in {"provider_error_received", "response_received"}:
            raise ProbeError(f"{profile['model']} durable observation is missing")
        if state in {
            "fully_validated",
            "terminal_validation_failure",
        }:
            raise ProbeError(f"{profile['model']} terminal evidence is missing")
        if state in {"sending", "uncertain_after_send"}:
            if any(derived_presence):
                raise ProbeError(
                    f"{profile['model']} has derived artifacts without a response"
                )
            result = _blank_profile_result(profile)
            result.update(
                {
                    "request_attempted": True,
                    "call_state": "uncertain_after_send",
                    "provider_call_count": entry["provider_call_count"],
                    "profile_disposition": "uncertain_after_send",
                    "provider_error_class": "interrupted_without_definite_result",
                }
            )
            results.append(result)
            continue
        if state == "not_attempted_due_to_global_failure":
            if any(derived_presence):
                raise ProbeError(
                    f"{profile['model']} unattempted call has derived artifacts"
                )
            results.append(
                _not_attempted_result(
                    profile, str(entry.get("stop_reason") or "global_failure")
                )
            )
            continue
        if any(derived_presence):
            raise ProbeError(f"{profile['model']} planned call has derived artifacts")
        results.append(_blank_profile_result(profile))
    return results, reprocessed


def _validate_summary_against_evidence(
    summary: Mapping[str, Any],
    results: Sequence[Mapping[str, Any]],
    ledger: Mapping[str, Any],
) -> None:
    """Reject summary, safety, or call-count drift from durable evidence."""

    if not isinstance(summary, dict) or summary.get("profiles") != list(results):
        raise ProbeError("stored profile summary differs from durable evidence")
    if summary.get("provider_call_budget") != PROVIDER_CALL_BUDGET:
        raise ProbeError("stored summary call budget differs")
    if summary.get("provider_call_count") != ledger["provider_call_count"]:
        raise ProbeError("stored summary call count differs from call ledger")
    if summary.get("phase1_4_disposition") != derive_phase1_4_disposition(results):
        raise ProbeError("stored Phase 1.4 disposition differs from evidence")
    required_zeroes = (
        "provider_retries",
        "model_list_calls",
        "openai_calls",
        "x_calls",
        "search_calls",
        "tool_calls",
        "real_conversation_records_read",
        "held_out_records_read",
        "production_files_read",
        "sealed_clean_prefixes_read",
    )
    required_false = (
        "model_comparison_performed",
        "winner_selected",
        "power_analysis_performed",
        "development_pilot_run",
        "merged",
        "deployed",
        "development_pilot_authorised",
        "real_corpus_use_authorised",
        "held_out_use_authorised",
        "production_integration_authorised",
    )
    if any(summary.get(name) != 0 for name in required_zeroes) or any(
        summary.get(name) is not False for name in required_false
    ):
        raise ProbeError("stored summary safety counters or authorisations differ")
    if sum(item["provider_call_count"] for item in results) != ledger[
        "provider_call_count"
    ]:
        raise ProbeError("profile result call counts differ from call ledger")


def verify_only(output: str | Path) -> dict[str, Any]:
    """Reprocess saved bytes twice without credentials or provider transport."""

    output_dir = _require_real_directory(output, "private run")
    with _exclusive_execution_lock(output_dir):
        return _verify_only_locked(output_dir)


def _verify_only_locked(output_dir: Path) -> dict[str, Any]:
    """Verify a private run while excluding a concurrent live execution."""

    ledger_before = _read_regular_bytes(output_dir / "call-ledger.json", "call ledger")
    ledger = _load_call_ledger(output_dir)
    nonplanned = any(entry["state"] != "planned" for entry in ledger["entries"])
    summary_path = output_dir / "result-summary.json"
    summary_exists = os.path.lexists(summary_path)
    checksum_declares_finalized = _checksum_declares_finalized_run(output_dir)
    recovery_mode = nonplanned and not checksum_declares_finalized
    checksum_before = verify_checksums(
        output_dir,
        allowed_mismatches=(
            frozenset({"call-ledger.json", "run-manifest.json"})
            if recovery_mode
            else frozenset()
        ),
        allowed_unlisted=(
            _recovery_unlisted_paths(ledger) if recovery_mode else frozenset()
        ),
    )
    call_count_before = ledger["provider_call_count"]
    tracked = validate_tracked_inputs()
    _validate_prepared_run(
        output_dir, tracked, allow_mutable_call_ledger=recovery_mode
    )
    if not summary_exists and not nonplanned:
        ledger_after = _read_regular_bytes(output_dir / "call-ledger.json", "call ledger")
        if ledger_after != ledger_before:
            raise ProbeError("verify-only changed the call ledger")
        audit_private_run_permissions(output_dir)
        audit_private_run_inventory(output_dir)
        return {
            "status": "passed",
            "checksum_status": checksum_before["status"],
            "provider_call_count": call_count_before,
            "saved_responses_reprocessed": 0,
            "provider_calls_made": 0,
            "network_transport_instantiated": False,
            "call_ledger_unchanged": True,
        }
    persisted_schema = _load_strict_json_file(
        PERSISTED_LEDGER_SCHEMA_PATH, "persisted ledger schema"
    )
    verified_results, reprocessed = _reconstruct_results_after_interruption(
        output_dir,
        ledger,
        tracked,
        persisted_schema,
        persist_missing_derived=not checksum_declares_finalized,
    )
    if recovery_mode and any(
        entry["state"] == "planned" for entry in ledger["entries"]
    ):
        ledger_after = _read_regular_bytes(
            output_dir / "call-ledger.json", "call ledger"
        )
        if ledger_after != ledger_before:
            raise ProbeError("verify-only changed the incomplete call ledger")
        audit_private_run_permissions(output_dir)
        audit_private_run_inventory(output_dir)
        return {
            "status": "passed_incomplete_call_plan",
            "phase1_4_disposition": derive_phase1_4_disposition(
                verified_results
            ),
            "checksum_status": checksum_before["status"],
            "provider_call_count": call_count_before,
            "saved_responses_reprocessed": reprocessed,
            "provider_calls_made": 0,
            "network_transport_instantiated": False,
            "call_ledger_unchanged": True,
        }
    if checksum_declares_finalized:
        if not summary_exists:
            raise ProbeError("final checksum manifest lacks result summary file")
        stored_summary = _load_strict_json_file(summary_path, "result summary")
        _validate_summary_against_evidence(
            stored_summary, verified_results, ledger
        )
        stored_run = _load_strict_json_file(
            output_dir / "run-manifest.json", "run manifest"
        )
        if (
            stored_run.get("provider_call_count") != ledger["provider_call_count"]
            or stored_run.get("phase1_4_disposition")
            != derive_phase1_4_disposition(verified_results)
        ):
            raise ProbeError("stored run-manifest result differs from evidence")
    ledger_after_processing = _read_regular_bytes(
        output_dir / "call-ledger.json", "call ledger"
    )
    if ledger_after_processing != ledger_before:
        raise ProbeError("verify-only changed the call ledger")
    summary = _finalize_run(
        output_dir,
        verified_results,
        verification_status="passed",
        verified_responses=reprocessed,
    )
    ledger_after = _read_regular_bytes(output_dir / "call-ledger.json", "call ledger")
    if ledger_after != ledger_before:
        raise ProbeError("verify-only changed the call ledger during finalisation")
    if _load_call_ledger(output_dir)["provider_call_count"] != call_count_before:
        raise ProbeError("verify-only changed the provider call count")
    final_checksum = verify_checksums(output_dir)
    return {
        "status": "passed",
        "phase1_4_disposition": summary["phase1_4_disposition"],
        "checksum_status": final_checksum["status"],
        "provider_call_count": call_count_before,
        "saved_responses_reprocessed": reprocessed,
        "deterministic_reprocessing_status": (
            "passed" if reprocessed else "not_applicable_no_saved_response"
        ),
        "provider_calls_made": 0,
        "network_transport_instantiated": False,
        "call_ledger_unchanged": True,
    }


def publish_report(output: str | Path, destination: str | Path) -> dict[str, Any]:
    """Atomically publish the redacted private report to an existing Dropbox."""

    output_dir = _require_real_directory(output, "private run")
    with _exclusive_execution_lock(output_dir):
        verify_checksums(output_dir)
        audit_private_run_permissions(output_dir)
        audit_private_run_inventory(output_dir)
        operator = _operator_record(output_dir)
        tests = operator.get("test_records")
        final_commit = operator.get("final_commit")
        run = _load_strict_json_file(
            output_dir / "run-manifest.json", "run manifest"
        )
        prepared_head = run.get("git_head_at_prepare")
        final_parent = _git_output("rev-parse", "HEAD^")
        if (
            operator.get("artifact_evidence") != "derived"
            or operator.get("record_format")
            != "proposition-ledger-phase1.4-operator-record-v1"
            or not isinstance(final_commit, str)
            or re.fullmatch(r"[0-9a-f]{40}", final_commit) is None
            or final_commit != _git_output("rev-parse", "HEAD")
            or prepared_head not in ALLOWED_PREPARE_HEADS
            or operator.get("final_parent") != prepared_head
            or final_parent != prepared_head
        ):
            raise ProbeError("report publication requires verified final Git identities")
        valid_test_records = isinstance(tests, list) and bool(tests)
        observed_check_ids: set[str] = set()
        if valid_test_records:
            for record in tests:
                if not isinstance(record, Mapping):
                    valid_test_records = False
                    break
                check_id = record.get("check_id")
                totals = [
                    record.get("passed"),
                    record.get("failed"),
                    record.get("skipped"),
                ]
                if (
                    not isinstance(check_id, str)
                    or not isinstance(record.get("command"), str)
                    or not record.get("command")
                    or record.get("result") != "passed"
                    or any(
                        isinstance(value, bool)
                        or not isinstance(value, int)
                        or value < 0
                        for value in totals
                    )
                    or record.get("failed") != 0
                ):
                    valid_test_records = False
                    break
                observed_check_ids.add(check_id)
        if (
            not valid_test_records
            or not REQUIRED_OPERATOR_CHECK_IDS.issubset(observed_check_ids)
        ):
            raise ProbeError("report publication requires passing final test records")
        summary = _load_strict_json_file(
            output_dir / "result-summary.json", "result summary"
        )
        verification = _load_strict_json_file(
            output_dir / "validation.json", "verification result"
        )
        if (
            summary.get("verify_only_status") != "passed"
            or verification.get("status") != "passed"
            or verification.get("checksum_status") != "passed"
        ):
            raise ProbeError("report publication requires successful verify-only")
        source = _read_regular_bytes(
            output_dir / "phase1.4-report.md", "private report"
        )
        if b"pending_until_final_commit" in source or b"Pending final operator" in source:
            raise ProbeError("private report still contains pending final fields")
    destination_path = _absolute_lexical(destination)
    expected_parent = _absolute_lexical(Path.home() / "Dropbox")
    if destination_path.parent != expected_parent:
        raise ProbeError("Dropbox report destination is outside the exact Dropbox root")
    parent = _require_real_directory(expected_parent, "Dropbox directory")
    if not os.access(parent, os.W_OK):
        raise ProbeError("Dropbox directory is not writable")
    if os.path.lexists(destination_path):
        raise ProbeError("Dropbox report already exists")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination_path.name}.", suffix=".tmp", dir=parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(source):
            offset += os.write(descriptor, source[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        rename_noreplace = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
        if rename_noreplace is None:
            raise ProbeError("atomic no-overwrite rename is unavailable")
        rename_noreplace.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename_noreplace.restype = ctypes.c_int
        if (
            rename_noreplace(
                -100,
                os.fsencode(temporary),
                -100,
                os.fsencode(destination_path),
                1,
            )
            != 0
        ):
            error_number = ctypes.get_errno()
            if error_number == errno.EEXIST:
                raise ProbeError("Dropbox report already exists")
            raise ProbeError(
                f"atomic Dropbox publication failed with errno {error_number}"
            )
        destination_path.chmod(0o600)
        directory_descriptor = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if os.path.lexists(temporary):
            temporary.unlink()
    return {
        "status": "published",
        "destination": str(destination_path),
        "sha256": sha256_bytes(source),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run or verify the bounded synthetic Phase 1.4 xAI schema probe."
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--prepare", action="store_true")
    modes.add_argument("--execute-live-probe", action="store_true")
    modes.add_argument("--verify-only", action="store_true")
    modes.add_argument("--publish-report", action="store_true")
    parser.add_argument("--private-output", type=Path, required=True)
    parser.add_argument("--confirm-provider-call-budget", type=int)
    parser.add_argument("--dropbox-output", type=Path)
    return parser


def _cli_safe_result(value: Any) -> Any:
    """Remove fields restricted to private storage from command output."""

    if isinstance(value, Mapping):
        return {
            key: _cli_safe_result(child)
            for key, child in value.items()
            if key not in {"provider_response_id", "sanitised_bounded_message"}
        }
    if isinstance(value, list):
        return [_cli_safe_result(child) for child in value]
    return value


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch one explicitly selected local or live probe mode."""

    args = _build_parser().parse_args(argv)
    try:
        if args.prepare:
            if args.confirm_provider_call_budget is not None:
                raise ProbeError("prepare mode does not accept a call acknowledgement")
            result = prepare_run(args.private_output)
        elif args.execute_live_probe:
            result = execute_live_probe(
                args.private_output,
                confirm_provider_call_budget=args.confirm_provider_call_budget,
            )
            if result.get("status") == "live_probe_not_run_missing_xai_api_key":
                print("live_probe_not_run_missing_xai_api_key")
                return 4
        elif args.verify_only:
            if args.confirm_provider_call_budget is not None:
                raise ProbeError("verify-only mode does not accept a call acknowledgement")
            result = verify_only(args.private_output)
        else:
            if args.dropbox_output is None:
                raise ProbeError("publish-report requires --dropbox-output")
            result = publish_report(args.private_output, args.dropbox_output)
    except ProbeError as exc:
        print(f"phase1_4_error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(_cli_safe_result(result), allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
