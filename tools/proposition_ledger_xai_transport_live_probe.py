#!/usr/bin/env python3
"""Run the bounded Phase 2C synthetic xAI evidence-transport smoke test.

Importing this module is inert.  ``--prepare`` and ``--verify`` are offline;
the official SDK transport is imported and constructed only by ``--run``.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import importlib.metadata
import json
import os
import stat
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from tools import proposition_ledger_evidence_transport as evidence  # noqa: E402
from tools import proposition_ledger_semantic_delta as semantic  # noqa: E402
from tools import proposition_ledger_xai_provider_preflight as preflight  # noqa: E402


PHASE2B_DIR = PROJECT_DIR / "proposition_ledger_research/phase2b"
PHASE2C_DIR = PROJECT_DIR / "proposition_ledger_research/phase2c"
CANONICAL_SCHEMA_PATH = (
    PROJECT_DIR
    / "proposition_ledger_research/schema/"
    "proposition-ledger-semantic-delta-v1.schema.json"
)
TRANSPORT_SCHEMA_PATH = (
    PROJECT_DIR
    / "proposition_ledger_research/schema/"
    "proposition-ledger-xai-transport-delta-v2.schema.json"
)
PERSISTED_LEDGER_SCHEMA_PATH = semantic.DEFAULT_LEDGER_SCHEMA_PATH
PHASE2B_PROMPT_PATH = PHASE2B_DIR / "incremental-ledger-system-prompt-v2.txt"
TRANSPORT_MANIFEST_PATH = PHASE2B_DIR / "transport-contract-manifest.json"
CASES_PATH = PHASE2C_DIR / "synthetic-cases.json"
ADDENDUM_PATH = PHASE2C_DIR / "synthetic-probe-addendum.txt"

PINNED_PYTHON = Path(
    "/disks/disk1/research/private-runs/"
    "proposition-ledger-phase1.3-xai-provider-preflight-20260901T013552Z/"
    "venv-a/bin/python"
)
PINNED_XAI_SDK_VERSION = "1.19.0"
PROTOCOL_VERSION = "proposition-ledger-phase2c-transport-live-probe-v1"
CALL_LOG_VERSION = "proposition-ledger-phase2c-call-log-v1"
VALIDATION_VERSION = "proposition-ledger-phase2c-validation-v1"
SUMMARY_VERSION = "proposition-ledger-phase2c-result-summary-v1"
MAX_OUTPUT_TOKENS = 4096
CLIENT_TIMEOUT_SECONDS = 300.0
PROVIDER_CALL_BUDGET = 6
ERROR_LIMIT = 512
NO_RETRY_CHANNEL_OPTIONS: tuple[tuple[str, Any], ...] = (
    ("grpc.enable_retries", 0),
    ("grpc.service_config", "{}"),
)

SEMANTIC_COLLECTIONS: tuple[str, ...] = (
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
)

CALL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "order": 1,
        "case_id": "format-chain",
        "turn_index": 0,
        "model": "grok-4.3",
        "dependency_order": None,
    },
    {
        "order": 2,
        "case_id": "format-chain",
        "turn_index": 0,
        "model": "grok-4.6",
        "dependency_order": None,
    },
    {
        "order": 3,
        "case_id": "format-chain",
        "turn_index": 1,
        "model": "grok-4.6",
        "dependency_order": 2,
    },
    {
        "order": 4,
        "case_id": "format-chain",
        "turn_index": 1,
        "model": "grok-4.3",
        "dependency_order": 1,
    },
    {
        "order": 5,
        "case_id": "overlap",
        "turn_index": 0,
        "model": "grok-4.3",
        "dependency_order": None,
    },
    {
        "order": 6,
        "case_id": "overlap",
        "turn_index": 0,
        "model": "grok-4.6",
        "dependency_order": None,
    },
)

TERMINAL_STATES = frozenset({"completed", "failed", "blocked"})
VALID_STATES = TERMINAL_STATES | {"planned", "attempted"}
ALLOWED_CALL_FILES = frozenset(
    {
        "raw-response.txt",
        "parsed-transport.json",
        "resolved-canonical-delta.json",
        "materialised-ledger.json",
        "validation.json",
        "usage.json",
    }
)


class ProbeError(RuntimeError):
    """A fail-closed local or live probe error."""


class StrictJSONError(ProbeError):
    """The response was not one strict UTF-8 JSON value."""


@dataclass(frozen=True)
class ProviderObservation:
    """The bounded provider response retained by the probe."""

    raw_text: str
    returned_model_id: str | None
    finish_reason: str | None
    usage: Mapping[str, Any]


def utc_now() -> str:
    """Return a whole-second RFC 3339 UTC timestamp."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Return the shared compact canonical JSON encoding."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def pretty_json_bytes(value: Any) -> bytes:
    """Return stable, human-readable private JSON bytes."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    """Return the lowercase SHA-256 digest for exact bytes."""

    return hashlib.sha256(value).hexdigest()


def _bounded(errors: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for error in errors:
        if len(result) >= 32:
            break
        result.append(str(error).replace("\n", " ")[:ERROR_LIMIT])
    return result


def _duplicate_member_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJSONError(f"duplicate JSON member: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise StrictJSONError(f"non-finite JSON number: {value}")


def strict_json_loads(source: bytes) -> Any:
    """Parse one UTF-8 JSON value, rejecting BOMs, duplicates and non-finites."""

    if source.startswith(b"\xef\xbb\xbf"):
        raise StrictJSONError("UTF-8 BOM is not permitted")
    try:
        text = source.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=_duplicate_member_hook,
            parse_constant=_reject_nonfinite,
        )
    except StrictJSONError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StrictJSONError(f"{type(exc).__name__}:{exc}") from exc


def _read_regular_bytes(path: str | Path, label: str) -> bytes:
    candidate = Path(path)
    try:
        metadata = candidate.lstat()
    except FileNotFoundError as exc:
        raise ProbeError(f"{label} is absent") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ProbeError(f"{label} is not a regular non-symlink file")
    return candidate.read_bytes()


def _load_json(path: str | Path, label: str) -> Any:
    return strict_json_loads(_read_regular_bytes(path, label))


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_private_bytes(path: Path, value: bytes) -> None:
    """Atomically create or replace one mode-0600 regular file."""

    if os.path.lexists(path):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ProbeError(f"unsafe output target: {path.name}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def _write_private_json(path: Path, value: Any) -> None:
    _write_private_bytes(path, pretty_json_bytes(value))


def _create_private_output(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ProbeError("private output path must be absolute")
    if os.path.lexists(candidate):
        raise ProbeError("private output path already exists")
    parent = candidate.parent
    if not parent.is_dir() or parent.is_symlink():
        raise ProbeError("private output parent must be a real directory")
    candidate.mkdir(mode=0o700)
    candidate.chmod(0o700)
    _fsync_directory(parent)
    return candidate


def _require_private_output(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ProbeError("private output path must be absolute")
    try:
        metadata = candidate.lstat()
    except FileNotFoundError as exc:
        raise ProbeError("private output directory is absent") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ProbeError("private output is not a real directory")
    return candidate


def _ensure_private_directory(path: Path) -> None:
    if os.path.lexists(path):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ProbeError(f"unsafe private directory: {path.name}")
    else:
        path.mkdir(mode=0o700)
    path.chmod(0o700)


def verify_pinned_environment() -> dict[str, Any]:
    """Require the established interpreter and xai-sdk pin."""

    if Path(sys.executable).resolve() != PINNED_PYTHON.resolve():
        raise ProbeError("current interpreter is not the established pinned Python")
    try:
        version = importlib.metadata.version("xai-sdk")
    except importlib.metadata.PackageNotFoundError as exc:
        raise ProbeError("xai-sdk is absent from the pinned environment") from exc
    if version != PINNED_XAI_SDK_VERSION:
        raise ProbeError("xai-sdk version differs from 1.19.0")
    return {
        "python_executable": str(Path(sys.executable).resolve()),
        "xai_sdk_version": version,
    }


def _harden_live_environment() -> None:
    """Disable SDK telemetry and remove unrelated credentials in this process."""

    credential_markers = (
        "API_KEY",
        "AUTHORIZATION",
        "BEARER",
        "COOKIE",
        "CREDENTIAL",
        "PASSWORD",
        "SECRET",
        "TOKEN",
    )
    unrelated = {
        name
        for name in os.environ
        if name != "XAI_API_KEY"
        and any(marker in name.upper() for marker in credential_markers)
    }
    unrelated.update(
        name
        for name in preflight.PROVIDER_KEY_ENV_NAMES
        if name != "XAI_API_KEY"
    )
    for name in unrelated:
        os.environ.pop(name, None)
    os.environ["XAI_SDK_DISABLE_SENSITIVE_TELEMETRY_ATTRIBUTES"] = "1"
    os.environ["XAI_SDK_DISABLE_TRACING"] = "1"


@contextmanager
def _exclusive_execution_lock(output_dir: Path) -> Iterable[None]:
    """Hold a non-blocking lock on the stable private-run directory inode."""

    flags = os.O_RDONLY
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(output_dir, flags)
    except OSError as exc:
        raise ProbeError("could not open private output for locking") from exc
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ProbeError("private output lock target is not a directory")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProbeError("another probe process holds the private-run lock") from exc
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _expected_cases() -> dict[str, Any]:
    contributor = {
        "participant_id": "participant-contributor",
        "role": "contributor",
        "author_key": "synthetic-author-participant-contributor",
        "identity_confidence": 1.0,
    }
    account = {
        "participant_id": "participant-account",
        "role": "account",
        "author_key": "synthetic-author-participant-account",
        "identity_confidence": 1.0,
    }
    formatting = "Café 🧭 status is “ready  now”.\r\nCode:\tA-1."
    repeated = "First report: the gate is open. Final report: the gate is open."
    return {
        "format_version": "proposition-ledger-phase2c-synthetic-cases-v1",
        "synthetic": True,
        "cases": [
            {
                "case_id": "format-chain",
                "conversation_key": "synthetic-format-chain",
                "turns": [
                    {
                        "turn_index": 0,
                        "turn_id": "synthetic-format-turn-0",
                        "parent_turn_id": None,
                        "participant": contributor,
                        "exact_text": formatting,
                        "provider_instruction": (
                            "Represent the explicit status statement. Cite the "
                            "complete current turn exactly as evidence."
                        ),
                        "hidden_local_expectation": {
                            "exact_text": formatting,
                            "occurrence_index": 0,
                        },
                    },
                    {
                        "turn_index": 1,
                        "turn_id": "synthetic-format-turn-1",
                        "parent_turn_id": "synthetic-format-turn-0",
                        "participant": account,
                        "exact_text": repeated,
                        "provider_instruction": (
                            "Represent the final report. Cite the exact phrase \"the "
                            "gate is open\" at its second literal occurrence."
                        ),
                        "hidden_local_expectation": {
                            "exact_text": "the gate is open",
                            "occurrence_index": 1,
                        },
                    },
                ],
            },
            {
                "case_id": "overlap",
                "conversation_key": "synthetic-overlap",
                "turns": [
                    {
                        "turn_index": 0,
                        "turn_id": "synthetic-overlap-turn-0",
                        "parent_turn_id": None,
                        "participant": contributor,
                        "exact_text": "Token: aaaa.",
                        "provider_instruction": (
                            "Represent the explicit fact about the token and cite the "
                            "middle overlapping occurrence of \"aa\" inside \"aaaa\"."
                        ),
                        "hidden_local_expectation": {
                            "exact_text": "aa",
                            "occurrence_index": 1,
                        },
                    }
                ],
            },
        ],
    }


def validate_cases(value: Any) -> dict[str, Any]:
    """Require exactly the three requested invented turns and hidden checks."""

    expected = _expected_cases()
    if value != expected:
        raise ProbeError("synthetic cases differ from the frozen Phase 2C cases")
    cases = {item["case_id"]: item for item in value["cases"]}
    formatting = cases["format-chain"]["turns"][0]["exact_text"]
    if len(formatting) != 42 or "\r\n" not in formatting or "\t" not in formatting:
        raise ProbeError("Unicode/CRLF/tab formatting case is not exact")
    if "ready  now" not in formatting:
        raise ProbeError("two-space formatting case is not exact")
    repeated_turn = cases["format-chain"]["turns"][1]
    if evidence.find_overlapping_occurrences(
        repeated_turn["exact_text"], "the gate is open"
    ) != ((14, 30), (46, 62)):
        raise ProbeError("repeated phrase positions differ")
    overlap_turn = cases["overlap"]["turns"][0]
    if evidence.find_overlapping_occurrences(overlap_turn["exact_text"], "aa") != (
        (7, 9),
        (8, 10),
        (9, 11),
    ):
        raise ProbeError("overlapping phrase positions differ")
    return cases


def _tracked_input_hashes() -> dict[str, str]:
    paths = {
        "canonical_schema": CANONICAL_SCHEMA_PATH,
        "transport_schema": TRANSPORT_SCHEMA_PATH,
        "phase2b_system_prompt": PHASE2B_PROMPT_PATH,
        "transport_contract_manifest": TRANSPORT_MANIFEST_PATH,
        "synthetic_cases": CASES_PATH,
        "synthetic_probe_addendum": ADDENDUM_PATH,
        "probe_tool": Path(__file__),
    }
    return {
        name: sha256_bytes(_read_regular_bytes(path, name))
        for name, path in sorted(paths.items())
    }


def validate_tracked_inputs() -> dict[str, Any]:
    """Load only the bounded tracked inputs and reconcile the Phase 2B manifest."""

    canonical_raw = _read_regular_bytes(CANONICAL_SCHEMA_PATH, "canonical schema")
    transport_raw = _read_regular_bytes(TRANSPORT_SCHEMA_PATH, "transport schema")
    canonical = strict_json_loads(canonical_raw)
    transport = strict_json_loads(transport_raw)
    manifest = _load_json(TRANSPORT_MANIFEST_PATH, "transport contract manifest")
    cases_value = _load_json(CASES_PATH, "synthetic cases")
    if not all(isinstance(item, dict) for item in (canonical, transport, manifest)):
        raise ProbeError("tracked schema or manifest root is not an object")

    provider_schema, transformations = preflight.transform_provider_schema(transport)
    derived_manifest = evidence.build_response_contract_manifest(
        transport_schema=transport,
        xai_provider_schema=provider_schema,
    )
    if manifest != derived_manifest:
        raise ProbeError("Phase 2B contract manifest does not match its inputs")
    if sha256_bytes(canonical_raw) != manifest["canonical_semantic_schema_sha256"]:
        raise ProbeError("canonical semantic schema hash mismatch")
    if sha256_bytes(transport_raw) != manifest["transport_schema_sha256"]:
        raise ProbeError("transport schema hash mismatch")
    if preflight.value_sha256(provider_schema) != manifest["xai_provider_schema_sha256"]:
        raise ProbeError("xAI provider schema hash mismatch")
    if len(transformations) != 19:
        raise ProbeError("established xAI provider transformation count differs")

    cases = validate_cases(cases_value)
    try:
        base_prompt = _read_regular_bytes(
            PHASE2B_PROMPT_PATH, "Phase 2B system prompt"
        ).decode("utf-8", errors="strict")
        addendum = _read_regular_bytes(ADDENDUM_PATH, "probe addendum").decode(
            "utf-8", errors="strict"
        )
    except UnicodeDecodeError as exc:
        raise ProbeError("prompt input is not strict UTF-8") from exc
    required_addendum_fragments = (
        "wholly synthetic",
        "explicit semantic content",
        "At least one evidence-bearing semantic record",
        "Abstention",
        "no_stable_issue",
        "copied exactly",
        "Overlapping matches count",
        "turn IDs",
        "character offsets",
        "only the structured response",
    )
    if any(fragment not in addendum for fragment in required_addendum_fragments):
        raise ProbeError("probe addendum is missing a required bounded instruction")
    system_prompt = base_prompt.rstrip("\n") + "\n\n" + addendum.strip("\n") + "\n"
    return {
        "canonical_schema": canonical,
        "transport_schema": transport,
        "provider_schema": provider_schema,
        "persisted_ledger_schema": _load_json(
            PERSISTED_LEDGER_SCHEMA_PATH, "persisted ledger schema"
        ),
        "manifest": manifest,
        "cases": cases,
        "system_prompt": system_prompt,
        "protocol_hash": sha256_bytes(system_prompt.encode("utf-8")),
        "input_hashes": _tracked_input_hashes(),
    }


def _case_and_turn(
    tracked: Mapping[str, Any], spec: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    case = tracked["cases"][spec["case_id"]]
    turn = case["turns"][spec["turn_index"]]
    return case, turn


def materialisation_turn(
    case: Mapping[str, Any], turn: Mapping[str, Any]
) -> dict[str, Any]:
    """Build the trusted current-turn shape consumed by the materialiser."""

    return {
        "conversation_key": case["conversation_key"],
        "turn_id": turn["turn_id"],
        "turn_index": turn["turn_index"],
        "parent_turn_id": turn["parent_turn_id"],
        "post_id": turn["turn_id"],
        "speaker_id": turn["participant"]["participant_id"],
        "text": turn["exact_text"],
    }


def genesis_context(
    case: Mapping[str, Any], turn: Mapping[str, Any]
) -> dict[str, Any]:
    """Build the bounded wholly synthetic genesis context."""

    return {
        "conversation_key": case["conversation_key"],
        "current_participant": copy.deepcopy(turn["participant"]),
        "root_post_id": turn["turn_id"],
        "source_completeness": {
            "reconstruction_grade": "A",
            "exact_text_complete": True,
            "parent_graph_complete": True,
            "chronology_complete": True,
            "account_publication_confirmed": True,
            "complete_prefix_through_turn": True,
            "limitations": ["Wholly synthetic Phase 2C transport smoke test."],
        },
    }


def build_turn_payload(
    tracked: Mapping[str, Any],
    case: Mapping[str, Any],
    turn: Mapping[str, Any],
    prior_ledger: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build one message payload, excluding every hidden expectation field."""

    payload = evidence.build_phase2b_user_payload(
        protocol_version=PROTOCOL_VERSION,
        protocol_hash=str(tracked["protocol_hash"]),
        conversation_key=str(case["conversation_key"]),
        current_turn_id=str(turn["turn_id"]),
        turn_index=int(turn["turn_index"]),
        parent_turn_id=turn["parent_turn_id"],
        current_turn_text=str(turn["exact_text"]),
        speaker_descriptor=turn["participant"],
        prior_ledger=prior_ledger,
        response_contract_manifest=tracked["manifest"],
    )
    payload["synthetic_probe_instruction"] = turn["provider_instruction"]
    encoded = canonical_json_bytes(payload)
    if b"hidden_local_expectation" in encoded:
        raise ProbeError("hidden expectation leaked into the provider payload")
    return payload


def build_request(
    tracked: Mapping[str, Any],
    spec: Mapping[str, Any],
    prior_ledger: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build and validate one credential-free provider request representation."""

    case, turn = _case_and_turn(tracked, spec)
    payload = build_turn_payload(tracked, case, turn, prior_ledger)
    request = evidence.build_request_representation(
        model=str(spec["model"]),
        user_payload=payload,
        system_prompt=str(tracked["system_prompt"]),
        xai_provider_schema=tracked["provider_schema"],
    )
    request["client_timeout_seconds"] = CLIENT_TIMEOUT_SECONDS
    request["no_retry_channel_options"] = [
        list(item) for item in NO_RETRY_CHANNEL_OPTIONS
    ]
    _validate_request_contract(request, tracked)
    return request


def _validate_request_contract(
    request: Mapping[str, Any], tracked: Mapping[str, Any]
) -> None:
    messages = request.get("messages")
    if not isinstance(messages, list) or len(messages) != 2:
        raise ProbeError("request must contain exactly two conversational messages")
    schema_encodings = tuple(
        canonical_json_bytes(tracked[name])
        for name in ("canonical_schema", "transport_schema", "provider_schema")
    )
    for message in messages:
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, str):
            raise ProbeError("request message content is not text")
        content_bytes = content.encode("utf-8")
        if any(schema in content_bytes for schema in schema_encodings):
            raise ProbeError("a complete schema appeared in conversational messages")
    response_format = request.get("response_format")
    if not isinstance(response_format, Mapping) or response_format.get(
        "schema"
    ) != tracked["provider_schema"]:
        raise ProbeError("full provider schema is absent from response_format")
    required = {
        "max_tokens": MAX_OUTPUT_TOKENS,
        "reasoning_effort": "low",
        "tools": [],
        "parallel_tool_calls": False,
        "search_parameters": None,
        "store_messages": False,
        "streaming": False,
        "code_execution": False,
        "fallback_model": None,
        "application_retry_count": 0,
        "sdk_grpc_retries": False,
        "tool_choice_parameter_sent": False,
    }
    if any(request.get(field) != expected for field, expected in required.items()):
        raise ProbeError("request transport controls differ from the Phase 2B contract")
    if "tool_choice" in request:
        raise ProbeError("tool_choice must be omitted")


def build_expected_transport_delta(
    case: Mapping[str, Any],
    turn: Mapping[str, Any],
    prior_ledger: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build a local-only valid witness used to compile dependent templates."""

    expectation = turn["hidden_local_expectation"]
    proposition = {
        "local_ref": "new-proposition-1",
        "canonical_text": str(expectation["exact_text"]),
        "speaker_or_attributor": {
            "kind": "speaker",
            "participant_id": turn["participant"]["participant_id"],
            "attributed_participant_id": None,
        },
        "exact_evidence_spans": [
            {
                "exact_text": expectation["exact_text"],
                "occurrence_index": expectation["occurrence_index"],
            }
        ],
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
            "surface_marker": None,
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
    return {
        "schema_version": evidence.TRANSPORT_SCHEMA_VERSION,
        "canonical_schema_version": evidence.CANONICAL_SCHEMA_VERSION,
        "conversation_key": case["conversation_key"],
        "target_turn_id": turn["turn_id"],
        "as_of_turn_index": turn["turn_index"],
        "prior_ledger_reference": (
            None
            if prior_ledger is None
            else {
                "ledger_id": prior_ledger["ledger_id"],
                "as_of_turn_index": prior_ledger["as_of_turn_index"],
            }
        ),
        "new_propositions": [proposition],
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


def construct_six_local_requests(
    tracked: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Construct six envelopes, using local witness ledgers for turn-1 templates."""

    requests: list[dict[str, Any]] = []
    bootstrap_priors: dict[str, dict[str, Any]] = {}
    for spec in CALL_SPECS:
        dependency = spec["dependency_order"]
        prior = bootstrap_priors.get(str(spec["model"])) if dependency else None
        if dependency and prior is None:
            raise ProbeError("dependent local request lacks its bootstrap predecessor")
        requests.append(build_request(tracked, spec, prior))
        if spec["case_id"] == "format-chain" and spec["turn_index"] == 0:
            case, turn = _case_and_turn(tracked, spec)
            witness = build_expected_transport_delta(case, turn, None)
            processed = process_response_bytes(
                canonical_json_bytes(witness),
                case=case,
                turn=turn,
                prior_ledger=None,
                tracked=tracked,
            )
            if processed["validation"]["overall_validation_status"] != "passed":
                raise ProbeError("local turn-0 witness did not fully validate")
            bootstrap_priors[str(spec["model"])] = processed["ledger"]
    if len(requests) != PROVIDER_CALL_BUDGET:
        raise ProbeError("local construction did not produce exactly six requests")
    return requests, bootstrap_priors


def compile_local_sdk_requests(
    requests: Sequence[Mapping[str, Any]], tracked: Mapping[str, Any]
) -> dict[str, Any]:
    """Compile all requests through the pinned SDK under network denial."""

    guard = preflight.NetworkDenialGuard()
    compiled = 0
    with guard:
        try:
            import grpc

            guard.patch_grpc(grpc)
            from xai_sdk.chat import BaseChat, system, user
            from xai_sdk.proto import chat_pb2
            from xai_sdk.sync.chat import Client as ChatClient
        except ImportError as exc:
            raise ProbeError("pinned xai-sdk could not be imported locally") from exc
        for representation in requests:
            channel = preflight._RegistrationOnlyChannel()
            client = ChatClient(channel)
            response_format = chat_pb2.ResponseFormat(
                format_type=chat_pb2.FORMAT_TYPE_JSON_SCHEMA,
                schema=canonical_json_bytes(tracked["provider_schema"]).decode("utf-8"),
            )
            chat = client.create(
                model=representation["model"],
                messages=[
                    system(representation["messages"][0]["content"]),
                    user(representation["messages"][1]["content"]),
                ],
                max_tokens=MAX_OUTPUT_TOKENS,
                reasoning_effort="low",
                tools=[],
                parallel_tool_calls=False,
                response_format=response_format,
                search_parameters=None,
                store_messages=False,
            )
            request = BaseChat._make_request(chat, 1)
            if channel.rpc_invocation_count:
                raise ProbeError("local SDK request construction invoked transport")
            if request.tools or request.HasField("tool_choice") or request.HasField(
                "search_parameters"
            ):
                raise ProbeError("local SDK request enabled a forbidden facility")
            emitted = strict_json_loads(request.response_format.schema.encode("utf-8"))
            if emitted != tracked["provider_schema"]:
                raise ProbeError("SDK changed the response_format schema")
            compiled += 1
    return {
        "requests_constructed": compiled,
        "provider_calls_made": 0,
        "transport_rpc_invocations": 0,
        "full_schema_in_response_format": compiled == PROVIDER_CALL_BUDGET,
        "full_schema_in_messages": False,
    }


def _validation_template(raw: bytes | None, server_status: str) -> dict[str, Any]:
    stages = (
        "strict_json",
        "provider_transport_schema",
        "evidence_resolution",
        "canonical_semantic_schema",
        "semantic_reference",
        "deterministic_materialisation",
        "persisted_ledger",
    )
    result: dict[str, Any] = {
        "validation_version": VALIDATION_VERSION,
        "validation_sequence": list(stages) + ["hidden_case_expectation"],
        "server_acceptance_status": server_status,
        "structural_validity_status": "not_run",
        "hidden_expectation_status": "not_run",
        "overall_validation_status": "not_run",
        "raw_response_sha256": None if raw is None else sha256_bytes(raw),
        "resolution_summary": None,
        "materialiser_status": None,
        "deterministic_materialisation_equal": None,
        "hidden_checks": {},
    }
    for stage in stages:
        result[stage] = {"status": "not_run", "errors": []}
    return result


def _fail_structural(
    validation: dict[str, Any], stage: str, errors: Iterable[Any]
) -> None:
    validation[stage] = {"status": "failed", "errors": _bounded(errors)}
    validation["structural_validity_status"] = "failed"
    validation["overall_validation_status"] = "failed"


def _iter_evidence_objects(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        spans = value.get("exact_evidence_spans")
        if isinstance(spans, list):
            for span in spans:
                if isinstance(span, Mapping):
                    yield span
        for key, child in value.items():
            if key != "exact_evidence_spans":
                yield from _iter_evidence_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_evidence_objects(child)


def _contains_value(value: Any, expected: Any) -> bool:
    if value == expected:
        return True
    if isinstance(value, Mapping):
        return any(_contains_value(child, expected) for child in value.values())
    if isinstance(value, list):
        return any(_contains_value(child, expected) for child in value)
    return False


def hidden_expectation_check(
    parsed: Mapping[str, Any],
    canonical: Mapping[str, Any],
    resolution_manifest: Mapping[str, Any],
    turn: Mapping[str, Any],
) -> dict[str, Any]:
    """Check the private selector, offset, record, and extraction expectations."""

    expectation = turn["hidden_local_expectation"]
    expected_text = expectation["exact_text"]
    expected_index = expectation["occurrence_index"]
    transport_spans = list(_iter_evidence_objects(parsed))
    canonical_spans = list(_iter_evidence_objects(canonical))
    matches = evidence.find_overlapping_occurrences(turn["exact_text"], expected_text)
    expected_start, expected_end = matches[expected_index]
    selector_rows = resolution_manifest.get("selectors", [])
    expected_transport_spans = [
        span
        for span in transport_spans
        if span.get("exact_text") == expected_text
        and span.get("occurrence_index") == expected_index
    ]
    expected_canonical_spans = [
        span
        for span in canonical_spans
        if span.get("exact_text") == expected_text
        and span.get("start_char") == expected_start
        and span.get("end_char") == expected_end
        and span.get("turn_id") == turn["turn_id"]
    ]
    expected_selector_rows = [
        row
        for row in selector_rows
        if row.get("exact_text_sha256")
        == sha256_bytes(str(expected_text).encode("utf-8"))
        and row.get("selected_occurrence_index") == expected_index
        and row.get("selected_start") == expected_start
        and row.get("selected_end") == expected_end
    ]
    semantic_record_count = sum(
        len(parsed.get(field, []))
        for field in SEMANTIC_COLLECTIONS
        if isinstance(parsed.get(field), list)
    )
    evidence_record_count = sum(
        1
        for field in SEMANTIC_COLLECTIONS
        for record in parsed.get(field, [])
        if isinstance(record, Mapping)
        and isinstance(record.get("exact_evidence_spans"), list)
        and record["exact_evidence_spans"]
    )
    checks = {
        "expected_exact_text_used": bool(expected_transport_spans),
        "expected_occurrence_index_used": bool(expected_transport_spans),
        "expected_match_count_observed": len(matches) > expected_index
        and bool(expected_selector_rows)
        and all(
            row.get("occurrence_count") == len(matches)
            for row in expected_selector_rows
        ),
        "expected_offsets_resolved": bool(expected_canonical_spans),
        "resolved_offsets_select_exact_substring": bool(canonical_spans)
        and all(
            turn["exact_text"][span["start_char"] : span["end_char"]]
            == span.get("exact_text")
            for span in canonical_spans
        ),
        "semantic_record_produced": semantic_record_count >= 1,
        "evidence_bearing_semantic_record_produced": evidence_record_count >= 1,
        "extraction_complete": parsed.get("extraction_status") == "complete",
        "not_abstained": parsed.get("abstentions") == [],
        "no_stable_issue_absent": not _contains_value(parsed, "no_stable_issue"),
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "semantic_record_count": semantic_record_count,
        "evidence_bearing_semantic_record_count": evidence_record_count,
        "expected_occurrence_count": len(matches),
        "selected_occurrence_index": expected_index,
        "selected_start": expected_start,
        "selected_end": expected_end,
    }


def _explicit_persisted_errors(
    ledger: Mapping[str, Any],
    prior_ledger: Mapping[str, Any] | None,
    case: Mapping[str, Any],
    turn: Mapping[str, Any],
    ledger_schema: Mapping[str, Any],
) -> list[str]:
    current_turn = materialisation_turn(case, turn)
    if prior_ledger is None:
        errors = semantic.phase1.validate_ledger(
            ledger,
            {
                "conversation_key": case["conversation_key"],
                "turns": [current_turn],
            },
            ledger_schema,
            _immediate_previous=None,
            _validate_history=False,
        )
    else:
        errors = semantic.phase1.validate_ledger_incremental(
            ledger, prior_ledger, current_turn, ledger_schema
        )
    result = list(errors)
    if ledger.get("ledger_sha256") != semantic.phase1.ledger_sha256(ledger):
        result.append("ledger_self_hash_mismatch")
    return sorted(set(result))


def process_response_bytes(
    raw: bytes,
    *,
    case: Mapping[str, Any],
    turn: Mapping[str, Any],
    prior_ledger: Mapping[str, Any] | None,
    tracked: Mapping[str, Any],
) -> dict[str, Any]:
    """Run the complete deterministic Phase 2B-to-persisted validation chain."""

    validation = _validation_template(raw, "accepted")
    try:
        parsed = strict_json_loads(raw)
    except StrictJSONError as exc:
        _fail_structural(validation, "strict_json", [exc])
        return {"parsed": None, "canonical": None, "ledger": None, "validation": validation}
    validation["strict_json"] = {"status": "passed", "errors": []}

    provider_errors = preflight.intended_validation_errors(
        tracked["provider_schema"], parsed, pattern_mode="xai_full_string"
    )
    if provider_errors:
        _fail_structural(validation, "provider_transport_schema", provider_errors)
        return {"parsed": parsed, "canonical": None, "ledger": None, "validation": validation}
    validation["provider_transport_schema"] = {"status": "passed", "errors": []}

    resolution = evidence.resolve_transport_delta(
        parsed,
        current_turn_id=str(turn["turn_id"]),
        current_turn_text=str(turn["exact_text"]),
        transport_schema=tracked["transport_schema"],
        canonical_schema=tracked["canonical_schema"],
    )
    if not resolution.succeeded or resolution.canonical_delta is None:
        _fail_structural(
            validation,
            "evidence_resolution",
            [resolution.status, *resolution.errors],
        )
        return {"parsed": parsed, "canonical": None, "ledger": None, "validation": validation}
    canonical = resolution.canonical_delta
    manifest = resolution.resolution_manifest or {}
    validation["evidence_resolution"] = {"status": "passed", "errors": []}
    validation["resolution_summary"] = {
        "resolver_version": manifest.get("resolver_version"),
        "selector_count": manifest.get("selector_count"),
        "resolution_manifest_sha256": manifest.get("resolution_manifest_sha256"),
        "selectors": copy.deepcopy(manifest.get("selectors", [])),
    }

    canonical_errors = preflight.intended_validation_errors(
        tracked["canonical_schema"],
        canonical,
        pattern_mode="canonical_outer_anchors",
    )
    if canonical_errors:
        _fail_structural(
            validation, "canonical_semantic_schema", canonical_errors
        )
        return {"parsed": parsed, "canonical": canonical, "ledger": None, "validation": validation}
    validation["canonical_semantic_schema"] = {"status": "passed", "errors": []}

    current_turn = materialisation_turn(case, turn)
    context = genesis_context(case, turn) if prior_ledger is None else None
    first = semantic.materialise_semantic_delta(
        prior_ledger,
        current_turn,
        canonical,
        current_participant=turn["participant"],
        genesis_context=context,
        semantic_schema=tracked["canonical_schema"],
        ledger_schema=tracked["persisted_ledger_schema"],
    )
    validation["materialiser_status"] = first.status
    if first.status == "semantic_reference_invalid":
        _fail_structural(validation, "semantic_reference", first.errors)
        return {"parsed": parsed, "canonical": canonical, "ledger": None, "validation": validation}
    validation["semantic_reference"] = {"status": "passed", "errors": []}
    if not first.succeeded or first.ledger is None:
        _fail_structural(
            validation,
            "deterministic_materialisation",
            [first.status, *first.errors],
        )
        if first.status == "persisted_ledger_validation_failure":
            validation["persisted_ledger"] = {
                "status": "failed",
                "errors": _bounded(first.errors),
            }
        return {"parsed": parsed, "canonical": canonical, "ledger": None, "validation": validation}

    second = semantic.materialise_semantic_delta(
        copy.deepcopy(prior_ledger),
        copy.deepcopy(current_turn),
        copy.deepcopy(canonical),
        current_participant=copy.deepcopy(turn["participant"]),
        genesis_context=copy.deepcopy(context),
        semantic_schema=tracked["canonical_schema"],
        ledger_schema=tracked["persisted_ledger_schema"],
    )
    deterministic = (
        second.succeeded
        and second.ledger is not None
        and canonical_json_bytes(first.ledger) == canonical_json_bytes(second.ledger)
        and first.local_id_map == second.local_id_map
    )
    validation["deterministic_materialisation_equal"] = deterministic
    if not deterministic:
        errors = ["repeated_materialisation_differed"]
        if not second.succeeded:
            errors.extend([second.status, *second.errors])
        _fail_structural(validation, "deterministic_materialisation", errors)
        return {"parsed": parsed, "canonical": canonical, "ledger": None, "validation": validation}
    validation["deterministic_materialisation"] = {"status": "passed", "errors": []}

    persisted_errors = _explicit_persisted_errors(
        first.ledger,
        prior_ledger,
        case,
        turn,
        tracked["persisted_ledger_schema"],
    )
    if persisted_errors:
        _fail_structural(validation, "persisted_ledger", persisted_errors)
        return {"parsed": parsed, "canonical": canonical, "ledger": None, "validation": validation}
    validation["persisted_ledger"] = {"status": "passed", "errors": []}
    validation["structural_validity_status"] = "passed"

    hidden = hidden_expectation_check(parsed, canonical, manifest, turn)
    validation["hidden_checks"] = hidden
    validation["hidden_expectation_status"] = hidden["status"]
    validation["overall_validation_status"] = (
        "passed" if hidden["status"] == "passed" else "failed"
    )
    return {
        "parsed": parsed,
        "canonical": canonical,
        "ledger": first.ledger,
        "validation": validation,
    }


def _call_identity(spec: Mapping[str, Any], tracked: Mapping[str, Any]) -> str:
    case, turn = _case_and_turn(tracked, spec)
    material = {
        "order": spec["order"],
        "case_id": spec["case_id"],
        "conversation_key": case["conversation_key"],
        "turn_id": turn["turn_id"],
        "turn_index": turn["turn_index"],
        "model": spec["model"],
        "dependency_order": spec["dependency_order"],
        "input_hashes": tracked["input_hashes"],
    }
    return sha256_bytes(canonical_json_bytes(material))


def make_call_log(tracked: Mapping[str, Any]) -> dict[str, Any]:
    """Create the fixed ordered six-entry durable call plan."""

    entries: list[dict[str, Any]] = []
    for spec in CALL_SPECS:
        case, turn = _case_and_turn(tracked, spec)
        entries.append(
            {
                **copy.deepcopy(spec),
                "conversation_key": case["conversation_key"],
                "turn_id": turn["turn_id"],
                "call_identity": _call_identity(spec, tracked),
                "state": "planned",
                "attempt_number": 0,
                "provider_call_count": 0,
                "attempted_at_utc": None,
                "finished_at_utc": None,
            }
        )
    return {
        "call_log_version": CALL_LOG_VERSION,
        "prepared_at_utc": utc_now(),
        "planned_call_count": PROVIDER_CALL_BUDGET,
        "attempted_call_count": 0,
        "provider_call_count": 0,
        "retry_call_count": 0,
        "repair_call_count": 0,
        "fallback_call_count": 0,
        "input_hashes": copy.deepcopy(tracked["input_hashes"]),
        "entries": entries,
    }


def _validate_call_log(log: Any, tracked: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(log, dict) or log.get("call_log_version") != CALL_LOG_VERSION:
        raise ProbeError("call log version is invalid")
    entries = log.get("entries")
    if not isinstance(entries, list) or len(entries) != PROVIDER_CALL_BUDGET:
        raise ProbeError("call log must contain exactly six entries")
    if log.get("planned_call_count") != PROVIDER_CALL_BUDGET:
        raise ProbeError("call log planned count differs")
    if log.get("input_hashes") != tracked["input_hashes"]:
        raise ProbeError("tracked inputs changed after prepare")
    if any(log.get(field) != 0 for field in ("retry_call_count", "repair_call_count", "fallback_call_count")):
        raise ProbeError("call log contains a retry, repair, or fallback count")
    attempted = 0
    for spec, entry in zip(CALL_SPECS, entries):
        if not isinstance(entry, dict):
            raise ProbeError("call log entry is not an object")
        for field in ("order", "case_id", "turn_index", "model", "dependency_order"):
            if entry.get(field) != spec[field]:
                raise ProbeError("call log order or identity differs")
        if entry.get("call_identity") != _call_identity(spec, tracked):
            raise ProbeError("call identity differs")
        state = entry.get("state")
        if state not in VALID_STATES:
            raise ProbeError("call log state is invalid")
        count = entry.get("provider_call_count")
        attempt_number = entry.get("attempt_number")
        if state in {"attempted", "completed", "failed"}:
            if count != 1 or attempt_number != 1:
                raise ProbeError("attempted call count is invalid")
            attempted += 1
        elif count != 0 or attempt_number != 0:
            raise ProbeError("unattempted call has a provider count")
    if log.get("attempted_call_count") != attempted or log.get(
        "provider_call_count"
    ) != attempted:
        raise ProbeError("call log aggregate attempted count differs")
    return log


def _load_call_log(output_dir: Path, tracked: Mapping[str, Any]) -> dict[str, Any]:
    return _validate_call_log(_load_json(output_dir / "call-log.json", "call log"), tracked)


def _write_call_log(output_dir: Path, log: Mapping[str, Any]) -> None:
    _write_private_json(output_dir / "call-log.json", log)


def _mark_attempted(output_dir: Path, log: dict[str, Any], index: int) -> None:
    entry = log["entries"][index]
    if entry["state"] != "planned":
        raise ProbeError("only a planned call may be attempted")
    entry["state"] = "attempted"
    entry["attempt_number"] = 1
    entry["provider_call_count"] = 1
    entry["attempted_at_utc"] = utc_now()
    log["attempted_call_count"] += 1
    log["provider_call_count"] += 1
    _write_call_log(output_dir, log)


def _finish_attempt(output_dir: Path, log: dict[str, Any], index: int, state: str) -> None:
    if state not in {"completed", "failed"}:
        raise ProbeError("attempted call terminal state is invalid")
    entry = log["entries"][index]
    if entry["state"] != "attempted":
        raise ProbeError("only an attempted call may finish")
    entry["state"] = state
    entry["finished_at_utc"] = utc_now()
    _write_call_log(output_dir, log)


def _mark_blocked(
    output_dir: Path, log: dict[str, Any], index: int, dependency_order: int
) -> None:
    entry = log["entries"][index]
    if entry["state"] != "planned":
        raise ProbeError("only a planned call may be blocked")
    entry["state"] = "blocked"
    entry["finished_at_utc"] = utc_now()
    entry["blocked_by_order"] = dependency_order
    _write_call_log(output_dir, log)


def _call_directory(output_dir: Path, entry: Mapping[str, Any]) -> Path:
    safe_model = str(entry["model"])
    return output_dir / (
        f"call-{int(entry['order']):02d}-{entry['case_id']}-"
        f"turn-{entry['turn_index']}-{safe_model}"
    )


def _persist_processed(call_dir: Path, raw: bytes, processed: Mapping[str, Any]) -> None:
    _ensure_private_directory(call_dir)
    _write_private_bytes(call_dir / "raw-response.txt", raw)
    if processed.get("parsed") is not None:
        _write_private_json(call_dir / "parsed-transport.json", processed["parsed"])
    if processed.get("canonical") is not None:
        _write_private_json(
            call_dir / "resolved-canonical-delta.json", processed["canonical"]
        )
    if processed.get("ledger") is not None:
        _write_private_json(call_dir / "materialised-ledger.json", processed["ledger"])
    _write_private_json(call_dir / "validation.json", processed["validation"])


def _provider_failure_validation(error_type: str) -> dict[str, Any]:
    validation = _validation_template(None, "failed")
    validation["provider_error_type"] = error_type[:128]
    validation["structural_validity_status"] = "not_run_no_response"
    validation["hidden_expectation_status"] = "not_run_no_response"
    validation["overall_validation_status"] = "failed"
    return validation


def _blocked_validation(dependency_order: int) -> dict[str, Any]:
    validation = _validation_template(None, "not_attempted")
    validation["blocked_by_order"] = dependency_order
    validation["structural_validity_status"] = "not_run_blocked"
    validation["hidden_expectation_status"] = "not_run_blocked"
    validation["overall_validation_status"] = "blocked"
    return validation


def _usage_record(
    *,
    observation: ProviderObservation | None,
    latency_seconds: float | None,
) -> dict[str, Any]:
    return {
        "provider_response_received": observation is not None,
        "returned_model_id": (
            None if observation is None else observation.returned_model_id
        ),
        "finish_reason": None if observation is None else observation.finish_reason,
        "latency_seconds": latency_seconds,
        "usage": {} if observation is None else copy.deepcopy(dict(observation.usage)),
    }


class XaiTransport:
    """The sole official-SDK live transport, instantiated only in run mode."""

    def __init__(self) -> None:
        """Construct the pinned client using only the current environment key."""

        if "XAI_API_KEY" not in os.environ:
            raise ProbeError("XAI_API_KEY is absent")
        _harden_live_environment()
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
            api_key=os.environ["XAI_API_KEY"],
            timeout=CLIENT_TIMEOUT_SECONDS,
            channel_options=list(NO_RETRY_CHANNEL_OPTIONS),
        )

    def sample(
        self,
        entry: Mapping[str, Any],
        request: Mapping[str, Any],
        _context: Mapping[str, Any],
    ) -> ProviderObservation:
        """Make exactly one non-streaming structured-output sample call."""

        response_format = self._chat_pb2.ResponseFormat(
            format_type=self._chat_pb2.FORMAT_TYPE_JSON_SCHEMA,
            schema=canonical_json_bytes(request["response_format"]["schema"]).decode(
                "utf-8"
            ),
        )
        chat = self._client.chat.create(
            model=entry["model"],
            messages=[
                self._system(request["messages"][0]["content"]),
                self._user(request["messages"][1]["content"]),
            ],
            max_tokens=MAX_OUTPUT_TOKENS,
            reasoning_effort="low",
            tools=[],
            parallel_tool_calls=False,
            response_format=response_format,
            search_parameters=None,
            store_messages=False,
        )
        response = chat.sample()
        if not isinstance(response.content, str):
            raise ProbeError("provider response content was not text")
        usage = self._message_to_dict(
            response.usage, preserving_proto_field_name=True
        )
        return ProviderObservation(
            raw_text=response.content,
            returned_model_id=response.proto.model or None,
            finish_reason=response.finish_reason or None,
            usage=usage,
        )

    def close(self) -> None:
        """Close the transient official SDK client."""

        self._client.close()


def prepare_run(
    output: str | Path,
    *,
    environment_check: Callable[[], Mapping[str, Any]] = verify_pinned_environment,
    local_compiler: Callable[
        [Sequence[Mapping[str, Any]], Mapping[str, Any]], Mapping[str, Any]
    ] = compile_local_sdk_requests,
) -> dict[str, Any]:
    """Prepare one immutable six-entry private plan with zero provider calls."""

    environment_check()
    tracked = validate_tracked_inputs()
    requests, _bootstrap = construct_six_local_requests(tracked)
    compilation = local_compiler(requests, tracked)
    if compilation.get("requests_constructed") != PROVIDER_CALL_BUDGET:
        raise ProbeError("prepare did not construct all six SDK requests")
    if compilation.get("provider_calls_made") != 0 or compilation.get(
        "transport_rpc_invocations"
    ) != 0:
        raise ProbeError("prepare unexpectedly invoked provider transport")
    output_dir = _create_private_output(output)
    log = make_call_log(tracked)
    _write_call_log(output_dir, log)
    audit_private_run(output_dir, prepared=True)
    return {
        "status": "prepared",
        "output": str(output_dir),
        "planned_calls": PROVIDER_CALL_BUDGET,
        "provider_calls": 0,
        "requests_constructed": compilation["requests_constructed"],
        "schema_absent_from_messages": True,
        "schema_present_in_response_format": True,
    }


def _load_prior_for_call(
    output_dir: Path,
    log: Mapping[str, Any],
    entry: Mapping[str, Any],
    tracked: Mapping[str, Any],
) -> dict[str, Any] | None:
    dependency = entry.get("dependency_order")
    if dependency is None:
        return None
    dependency_entry = log["entries"][int(dependency) - 1]
    if dependency_entry["state"] != "completed":
        return None
    prior_path = _call_directory(output_dir, dependency_entry) / "materialised-ledger.json"
    prior = _load_json(prior_path, "same-model prior materialised ledger")
    if not isinstance(prior, dict):
        raise ProbeError("same-model prior ledger is not an object")
    if dependency_entry["model"] != entry["model"]:
        raise ProbeError("dependent call points to another model")
    case, turn = _case_and_turn(tracked, entry)
    if (
        prior.get("conversation_key") != case["conversation_key"]
        or prior.get("target_turn_id") != turn["parent_turn_id"]
        or prior.get("as_of_turn_index") != int(turn["turn_index"]) - 1
        or prior.get("ledger_sha256") != semantic.phase1.ledger_sha256(prior)
    ):
        raise ProbeError("same-model prior ledger binding is invalid")
    schema_errors = semantic.phase1._jsonschema_errors(
        prior, tracked["persisted_ledger_schema"]
    )
    if schema_errors:
        raise ProbeError("same-model prior ledger fails its schema")
    return prior


def run_probe(
    output: str | Path,
    *,
    confirm_calls: int | None,
    transport: Any | None = None,
    environment_check: Callable[[], Mapping[str, Any]] = verify_pinned_environment,
) -> dict[str, Any]:
    """Lock the private run and execute each planned call at most once."""

    if confirm_calls != PROVIDER_CALL_BUDGET:
        raise ProbeError("--run requires --confirm-calls 6")
    output_dir = _require_private_output(output)
    with _exclusive_execution_lock(output_dir):
        return _run_probe_locked(
            output_dir,
            confirm_calls=confirm_calls,
            transport=transport,
            environment_check=environment_check,
        )


def _run_probe_locked(
    output: str | Path,
    *,
    confirm_calls: int | None,
    transport: Any | None = None,
    environment_check: Callable[[], Mapping[str, Any]] = verify_pinned_environment,
) -> dict[str, Any]:
    """Execute each still-planned entry once, with no retries or fallbacks."""

    if confirm_calls != PROVIDER_CALL_BUDGET:
        raise ProbeError("--run requires --confirm-calls 6")
    environment_check()
    tracked = validate_tracked_inputs()
    output_dir = _require_private_output(output)
    if os.path.lexists(output_dir / "SHA256SUMS"):
        raise ProbeError("finalised runs cannot be executed again")
    log = _load_call_log(output_dir, tracked)
    if not any(entry["state"] == "planned" for entry in log["entries"]):
        raise ProbeError("call log has no planned entry")
    if all(entry["state"] == "planned" for entry in log["entries"]):
        audit_private_run(output_dir, prepared=True)

    owned_transport = transport is None
    active_transport = XaiTransport() if transport is None else transport
    try:
        for index, entry in enumerate(log["entries"]):
            if entry["state"] != "planned":
                continue
            dependency = entry.get("dependency_order")
            if dependency is not None and log["entries"][dependency - 1][
                "state"
            ] != "completed":
                _mark_blocked(output_dir, log, index, int(dependency))
                call_dir = _call_directory(output_dir, entry)
                _ensure_private_directory(call_dir)
                _write_private_json(
                    call_dir / "validation.json", _blocked_validation(int(dependency))
                )
                _write_private_json(
                    call_dir / "usage.json",
                    _usage_record(observation=None, latency_seconds=None),
                )
                continue

            prior = _load_prior_for_call(output_dir, log, entry, tracked)
            request = build_request(tracked, entry, prior)
            case, turn = _case_and_turn(tracked, entry)
            call_dir = _call_directory(output_dir, entry)
            _ensure_private_directory(call_dir)
            _mark_attempted(output_dir, log, index)
            began = time.monotonic()
            try:
                observation = active_transport.sample(
                    copy.deepcopy(entry),
                    request,
                    {
                        "case": copy.deepcopy(case),
                        "turn": copy.deepcopy(turn),
                        "prior_ledger": copy.deepcopy(prior),
                    },
                )
            except Exception as exc:
                latency = round(time.monotonic() - began, 6)
                validation = _provider_failure_validation(type(exc).__name__)
                _write_private_json(call_dir / "validation.json", validation)
                _write_private_json(
                    call_dir / "usage.json",
                    _usage_record(observation=None, latency_seconds=latency),
                )
                _finish_attempt(output_dir, log, index, "failed")
                continue

            latency = round(time.monotonic() - began, 6)
            try:
                raw = observation.raw_text.encode("utf-8", errors="strict")
                # Preserve the returned bytes before any local validator runs.
                _write_private_bytes(call_dir / "raw-response.txt", raw)
                processed = process_response_bytes(
                    raw,
                    case=case,
                    turn=turn,
                    prior_ledger=prior,
                    tracked=tracked,
                )
                _persist_processed(call_dir, raw, processed)
                _write_private_json(
                    call_dir / "usage.json",
                    _usage_record(observation=observation, latency_seconds=latency),
                )
                state = (
                    "completed"
                    if processed["validation"]["overall_validation_status"] == "passed"
                    and processed.get("ledger") is not None
                    else "failed"
                )
            except Exception as exc:
                validation = _provider_failure_validation(
                    f"local_validation_{type(exc).__name__}"
                )
                validation["server_acceptance_status"] = "accepted"
                validation["structural_validity_status"] = "failed"
                _write_private_json(call_dir / "validation.json", validation)
                _write_private_json(
                    call_dir / "usage.json",
                    _usage_record(observation=observation, latency_seconds=latency),
                )
                state = "failed"
            _finish_attempt(output_dir, log, index, state)
    finally:
        if owned_transport:
            try:
                active_transport.close()
            except Exception:
                pass

    log = _load_call_log(output_dir, tracked)
    summary = build_result_summary(output_dir, log, tracked)
    _write_private_json(output_dir / "result-summary.json", summary)
    write_checksums(output_dir)
    audit_private_run(output_dir, prepared=False)
    return summary


def _numeric_token(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def build_result_summary(
    output_dir: Path, log: Mapping[str, Any], tracked: Mapping[str, Any]
) -> dict[str, Any]:
    """Derive the aggregate and six bounded per-call result records."""

    calls: list[dict[str, Any]] = []
    token_fields = (
        "prompt_tokens",
        "prompt_text_tokens",
        "cached_prompt_tokens",
        "cached_prompt_text_tokens",
        "reasoning_tokens",
        "completion_tokens",
        "total_tokens",
    )
    token_totals = {field: 0 for field in token_fields}
    latency_total = 0.0
    latency_count = 0
    for entry in log["entries"]:
        call_dir = _call_directory(output_dir, entry)
        validation = _load_json(call_dir / "validation.json", "call validation")
        usage_record = _load_json(call_dir / "usage.json", "call usage")
        usage = usage_record.get("usage", {})
        if not isinstance(validation, dict) or not isinstance(usage_record, dict):
            raise ProbeError("call result artifact is not an object")
        if isinstance(usage, Mapping):
            for field in token_fields:
                token_totals[field] += _numeric_token(usage.get(field))
        latency = usage_record.get("latency_seconds")
        if isinstance(latency, (int, float)) and not isinstance(latency, bool):
            latency_total += float(latency)
            latency_count += 1
        calls.append(
            {
                "order": entry["order"],
                "case_id": entry["case_id"],
                "turn_id": entry["turn_id"],
                "turn_index": entry["turn_index"],
                "model": entry["model"],
                "state": entry["state"],
                "provider_call_count": entry["provider_call_count"],
                "server_acceptance_status": validation.get(
                    "server_acceptance_status"
                ),
                "structural_validity_status": validation.get(
                    "structural_validity_status"
                ),
                "hidden_expectation_status": validation.get(
                    "hidden_expectation_status"
                ),
                "transport_resolution_status": validation.get(
                    "evidence_resolution", {}
                ).get("status"),
                "canonical_validation_status": validation.get(
                    "canonical_semantic_schema", {}
                ).get("status"),
                "materialisation_status": validation.get(
                    "deterministic_materialisation", {}
                ).get("status"),
                "persisted_ledger_status": validation.get(
                    "persisted_ledger", {}
                ).get("status"),
                "latency_seconds": latency,
                "usage": copy.deepcopy(usage),
            }
        )

    by_order = {item["order"]: item for item in calls}
    chain_by_model = {
        "grok-4.3": all(by_order[order]["state"] == "completed" for order in (1, 4)),
        "grok-4.6": all(by_order[order]["state"] == "completed" for order in (2, 3)),
    }
    exact_formatting = {
        model: by_order[order]["hidden_expectation_status"] == "passed"
        for model, order in (("grok-4.3", 1), ("grok-4.6", 2))
    }
    repeated = {
        model: by_order[order]["hidden_expectation_status"] == "passed"
        for model, order in (("grok-4.3", 4), ("grok-4.6", 3))
    }
    overlap = {
        model: by_order[order]["hidden_expectation_status"] == "passed"
        for model, order in (("grok-4.3", 5), ("grok-4.6", 6))
    }
    return {
        "summary_version": SUMMARY_VERSION,
        "planned_call_count": PROVIDER_CALL_BUDGET,
        "attempted_call_count": log["attempted_call_count"],
        "provider_call_count": log["provider_call_count"],
        "completed_call_count": sum(item["state"] == "completed" for item in calls),
        "failed_call_count": sum(item["state"] == "failed" for item in calls),
        "blocked_call_count": sum(item["state"] == "blocked" for item in calls),
        "retry_call_count": 0,
        "repair_call_count": 0,
        "fallback_call_count": 0,
        "server_acceptance_success_count": sum(
            item["server_acceptance_status"] == "accepted" for item in calls
        ),
        "transport_success_count": sum(
            item["transport_resolution_status"] == "passed" for item in calls
        ),
        "canonical_success_count": sum(
            item["canonical_validation_status"] == "passed" for item in calls
        ),
        "materialisation_success_count": sum(
            item["materialisation_status"] == "passed" for item in calls
        ),
        "persisted_ledger_success_count": sum(
            item["persisted_ledger_status"] == "passed" for item in calls
        ),
        "hidden_expectation_success_count": sum(
            item["hidden_expectation_status"] == "passed" for item in calls
        ),
        "two_turn_chain_completed_by_model": chain_by_model,
        "exact_formatting_passed_by_model": exact_formatting,
        "repeated_text_index_1_passed_by_model": repeated,
        "overlapping_index_1_passed_by_model": overlap,
        "token_totals": token_totals,
        "latency": {
            "count": latency_count,
            "total_seconds": round(latency_total, 6),
        },
        "calls": calls,
        "input_hashes": copy.deepcopy(tracked["input_hashes"]),
        "real_conversation_records_read": 0,
        "held_out_records_read": 0,
        "model_winner_selected": False,
        "phase2a_pilot_rerun": False,
        "production_touched": False,
        "merged": False,
        "deployed": False,
    }


def write_checksums(output_dir: Path) -> None:
    """Write a sorted checksum manifest covering every other generated file."""

    lines: list[str] = []
    for path in sorted(output_dir.rglob("*")):
        if path == output_dir / "SHA256SUMS" or path.is_dir():
            continue
        if path.is_symlink() or not path.is_file():
            raise ProbeError("private output contains an unsafe checksum target")
        relative = path.relative_to(output_dir).as_posix()
        lines.append(f"{sha256_bytes(path.read_bytes())}  {relative}\n")
    _write_private_bytes(output_dir / "SHA256SUMS", "".join(lines).encode("utf-8"))


def verify_checksums(output_dir: Path) -> dict[str, Any]:
    """Require exact checksum inventory and content matches."""

    source = _read_regular_bytes(output_dir / "SHA256SUMS", "SHA256SUMS")
    try:
        text = source.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ProbeError("SHA256SUMS is not UTF-8") from exc
    declared: dict[str, str] = {}
    for line in text.splitlines():
        if len(line) < 67 or line[64:66] != "  ":
            raise ProbeError("invalid SHA256SUMS line")
        digest, relative = line[:64], line[66:]
        if (
            len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not relative
            or relative.startswith("/")
            or ".." in Path(relative).parts
            or relative in declared
            or relative == "SHA256SUMS"
        ):
            raise ProbeError("unsafe or duplicate SHA256SUMS entry")
        declared[relative] = digest
    actual_paths = {
        path.relative_to(output_dir).as_posix()
        for path in output_dir.rglob("*")
        if path.is_file() and not path.is_symlink() and path.name != "SHA256SUMS"
    }
    if set(declared) != actual_paths:
        raise ProbeError("SHA256SUMS inventory differs from private files")
    for relative, digest in declared.items():
        path = output_dir / relative
        if path.is_symlink() or not path.is_file():
            raise ProbeError("checksum target is unsafe")
        if sha256_bytes(path.read_bytes()) != digest:
            raise ProbeError(f"checksum mismatch: {relative}")
    return {"status": "passed", "checked_files": len(declared)}


def audit_private_run(output_dir: Path, *, prepared: bool) -> dict[str, Any]:
    """Require private modes, no symlinks, and only the prescribed inventory."""

    if stat.S_IMODE(output_dir.stat().st_mode) != 0o700:
        raise ProbeError("private output directory mode is not 0700")
    root_files: set[str] = set()
    root_dirs: set[str] = set()
    for path in output_dir.iterdir():
        if path.is_symlink():
            raise ProbeError("private output contains a symlink")
        if path.is_file():
            root_files.add(path.name)
        elif path.is_dir():
            root_dirs.add(path.name)
        else:
            raise ProbeError("private output contains a special file")
    if prepared:
        if root_files != {"call-log.json"} or root_dirs:
            raise ProbeError("prepared private inventory differs")
    else:
        expected_dirs = {
            _call_directory(output_dir, entry).name
            for entry in _load_json(output_dir / "call-log.json", "call log")[
                "entries"
            ]
        }
        if root_files != {"call-log.json", "result-summary.json", "SHA256SUMS"}:
            raise ProbeError("final private root inventory differs")
        if root_dirs != expected_dirs:
            raise ProbeError("final private call-directory inventory differs")
    for path in output_dir.rglob("*"):
        if path.is_symlink():
            raise ProbeError("private output contains a nested symlink")
        mode = stat.S_IMODE(path.stat().st_mode)
        if path.is_dir():
            if mode != 0o700:
                raise ProbeError("private subdirectory mode is not 0700")
            names = {child.name for child in path.iterdir()}
            if not names <= ALLOWED_CALL_FILES:
                raise ProbeError("call directory contains an unapproved file")
        elif not path.is_file() or mode != 0o600:
            raise ProbeError("private generated file mode is not 0600")
    return {"status": "passed"}


def _compare_optional_json(path: Path, expected: Any, label: str) -> None:
    if expected is None:
        if os.path.lexists(path):
            raise ProbeError(f"unexpected {label}")
        return
    if _read_regular_bytes(path, label) != pretty_json_bytes(expected):
        raise ProbeError(f"saved {label} differs from deterministic reprocessing")


def verify_run(
    output: str | Path,
    *,
    environment_check: Callable[[], Mapping[str, Any]] = verify_pinned_environment,
) -> dict[str, Any]:
    """Lock the private run and verify it without any provider capability."""

    output_dir = _require_private_output(output)
    with _exclusive_execution_lock(output_dir):
        return _verify_run_locked(
            output_dir,
            environment_check=environment_check,
        )


def _verify_run_locked(
    output: str | Path,
    *,
    environment_check: Callable[[], Mapping[str, Any]] = verify_pinned_environment,
) -> dict[str, Any]:
    """Reprocess saved raw bytes without credentials, clients, or provider calls."""

    environment_check()
    tracked = validate_tracked_inputs()
    output_dir = _require_private_output(output)
    log_before = _read_regular_bytes(output_dir / "call-log.json", "call log")
    log = _load_call_log(output_dir, tracked)
    if all(entry["state"] == "planned" for entry in log["entries"]):
        if os.path.lexists(output_dir / "SHA256SUMS") or os.path.lexists(
            output_dir / "result-summary.json"
        ):
            raise ProbeError("prepared run unexpectedly contains final artifacts")
        audit_private_run(output_dir, prepared=True)
        return {
            "status": "passed_prepared",
            "provider_calls_made": 0,
            "saved_responses_reprocessed": 0,
            "api_key_required": False,
        }

    checksum = verify_checksums(output_dir)
    audit_private_run(output_dir, prepared=False)
    priors: dict[str, dict[str, Any]] = {}
    reprocessed = 0
    for entry in log["entries"]:
        call_dir = _call_directory(output_dir, entry)
        state = entry["state"]
        dependency = entry.get("dependency_order")
        prior = priors.get(str(entry["model"])) if dependency is not None else None
        if dependency is not None:
            dependency_entry = log["entries"][int(dependency) - 1]
            if dependency_entry["state"] == "completed" and prior is None:
                raise ProbeError("verify lacks the completed same-model predecessor")
        raw_path = call_dir / "raw-response.txt"
        if raw_path.exists():
            raw = _read_regular_bytes(raw_path, "raw response")
            case, turn = _case_and_turn(tracked, entry)
            processed = process_response_bytes(
                raw,
                case=case,
                turn=turn,
                prior_ledger=prior,
                tracked=tracked,
            )
            _compare_optional_json(
                call_dir / "parsed-transport.json", processed["parsed"], "parsed transport"
            )
            _compare_optional_json(
                call_dir / "resolved-canonical-delta.json",
                processed["canonical"],
                "resolved canonical delta",
            )
            _compare_optional_json(
                call_dir / "materialised-ledger.json",
                processed["ledger"],
                "materialised ledger",
            )
            _compare_optional_json(
                call_dir / "validation.json", processed["validation"], "validation"
            )
            expected_state = (
                "completed"
                if processed["validation"]["overall_validation_status"] == "passed"
                and processed["ledger"] is not None
                else "failed"
            )
            if state != expected_state:
                raise ProbeError("call state differs from deterministic validation")
            if state == "completed" and entry["case_id"] == "format-chain" and entry[
                "turn_index"
            ] == 0:
                priors[str(entry["model"])] = processed["ledger"]
            reprocessed += 1
        elif state == "blocked":
            if dependency is None or log["entries"][int(dependency) - 1][
                "state"
            ] == "completed":
                raise ProbeError("blocked call does not have a failed dependency")
            expected_validation = _blocked_validation(int(dependency))
            _compare_optional_json(
                call_dir / "validation.json", expected_validation, "blocked validation"
            )
            for name in (
                "parsed-transport.json",
                "resolved-canonical-delta.json",
                "materialised-ledger.json",
            ):
                if os.path.lexists(call_dir / name):
                    raise ProbeError("blocked call contains a response-derived artifact")
        elif state == "failed":
            validation = _load_json(call_dir / "validation.json", "provider failure validation")
            if not isinstance(validation, dict) or validation.get(
                "server_acceptance_status"
            ) != "failed":
                raise ProbeError("raw-less failed call is not a provider failure")
        else:
            raise ProbeError("finalized run contains a planned or attempted entry")

        usage = _load_json(call_dir / "usage.json", "usage")
        if not isinstance(usage, dict) or set(usage) != {
            "provider_response_received",
            "returned_model_id",
            "finish_reason",
            "latency_seconds",
            "usage",
        }:
            raise ProbeError("usage artifact shape differs")

    expected_summary = build_result_summary(output_dir, log, tracked)
    if _read_regular_bytes(
        output_dir / "result-summary.json", "result summary"
    ) != pretty_json_bytes(expected_summary):
        raise ProbeError("result summary differs from deterministic evidence")
    if _read_regular_bytes(output_dir / "call-log.json", "call log") != log_before:
        raise ProbeError("verify changed the call log")
    final_checksum = verify_checksums(output_dir)
    return {
        "status": "passed",
        "provider_calls_made": 0,
        "saved_responses_reprocessed": reprocessed,
        "api_key_required": False,
        "call_log_unchanged": True,
        "checksum_status": final_checksum["status"],
        "checked_files": checksum["checked_files"],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare, run, or verify the six-call Phase 2C transport probe."
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--prepare", action="store_true")
    modes.add_argument("--run", action="store_true")
    modes.add_argument("--verify", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confirm-calls", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch exactly one prepare, run, or verify mode."""

    args = _build_parser().parse_args(argv)
    try:
        if args.prepare:
            if args.confirm_calls is not None:
                raise ProbeError("--prepare does not accept --confirm-calls")
            result = prepare_run(args.output)
        elif args.run:
            result = run_probe(args.output, confirm_calls=args.confirm_calls)
        else:
            if args.confirm_calls is not None:
                raise ProbeError("--verify does not accept --confirm-calls")
            result = verify_run(args.output)
    except ProbeError as exc:
        print(f"phase2c_probe_error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
