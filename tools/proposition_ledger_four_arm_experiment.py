#!/usr/bin/env python3
"""Case-focused runner for the frozen supplemental four-arm challenge.

Validate, prepare and verify are offline.  Live execution is guarded by the
frozen human/Git/budget policy and accepts only a reviewed, injected adapter;
this module never imports a provider SDK in an offline mode.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools import proposition_ledger_xai_transport_live_probe as private_io


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_FREEZE = (
    PROJECT_DIR / "proposition_ledger_research/phase2_experiment/experiment-freeze-v4.json"
)
CASE_SLUG = "market-planning-live-20260901"
ALIASES = ("evaluation-01", "evaluation-02", "evaluation-03")
ARMS = ("A", "B", "C", "D")
STATUS_LABELS = {
    "supplemental",
    "post_specification",
    "live_challenge",
    "excluded_from_prespecified_counts",
}
VERSIONS = {
    "canonical_semantic_delta": "proposition-ledger-semantic-delta-v1.1.2",
    "materialiser": "proposition-ledger-semantic-delta-materialiser-v2.0.2",
    "xai_transport": "proposition-ledger-xai-transport-delta-v2.0.2",
    "persisted_ledger": "proposition-ledger-v1.0.1",
}
XAI_PROVIDER_SCHEMA_SHA256 = "b9be2d529245f6e8ebba8a9262223324efba304622399629e9b34e0215af7d87"
PRIOR_LEDGER_SENTINEL = "FILL_WITH_PREVIOUS_MATERIALISED_LEDGER_ID"
HUMAN_SELECTOR_VERSION = "proposition-ledger-human-selector-delta-v1.1.0"
PREPARED_HUMAN_DIR = "human-ledger-pack"
HUMAN_REFERENCE_CHAIN_FILE = "reference-chain.json"
HUMAN_REFERENCE_PACK_FILE = "reference-pack.json"
HUMAN_DETERMINISTIC_DELTA_FIELDS = {
    "schema_version", "canonical_schema_version", "conversation_key",
    "target_turn_id", "as_of_turn_index", "prior_ledger_reference",
}
PRIOR_SEMANTIC_OBJECT_FIELDS = (
    "propositions", "issue_states", "participant_commitments",
    "conversational_obligations", "proposition_groups", "answer_targets",
)
HUMAN_HELP_FIELD_ALIASES = {
    "new_proposition": "new_propositions",
    "proposition_update": "proposition_updates",
    "new_proposition_group": "new_proposition_groups",
    "proposition_group_update": "proposition_group_updates",
    "new_issue_state": "new_issue_states",
    "issue_state_update": "issue_state_updates",
    "commitment_change": "commitment_changes",
    "obligation_change": "obligation_changes",
    "new_relation": "new_relations",
    "answer_target": "answer_target_changes",
    "rejected_answer_target": "rejected_answer_target_changes",
    "repair_record": "repair_records",
    "resolved_item": "resolved_items",
    "abstention": "abstentions",
    "warning": "warnings",
}
HUMAN_LOCAL_REF_SPECS = (
    ("new_propositions", "new-proposition", None),
    ("new_proposition_groups", "new-proposition-group", None),
    ("new_issue_states", "new-issue", None),
    ("commitment_changes", "new-commitment", "add"),
    ("obligation_changes", "new-obligation", "add"),
    ("new_relations", "new-relation", None),
    ("answer_target_changes", "new-answer-target", "add"),
    ("rejected_answer_target_changes", "new-rejected-answer-target", "add"),
    ("repair_records", "new-repair", "add"),
    ("warnings", "new-warning", None),
)
HUMAN_LOCAL_REF_FORMATS = {
    **{
        field: f"{prefix}-N in collection order"
        + (f" for operation={operation}" if operation is not None else "")
        for field, prefix, operation in HUMAN_LOCAL_REF_SPECS
    },
    "issue_live_alternatives": "new-alternative-N in current-turn traversal order",
}
HUMAN_VISIBLE_FORBIDDEN = re.compile(
    r"(?i)(?:\bxai\b|\bgrok\b|\bopenai\b|\bprovider\b|\bmodel\b|"
    r"\barm(?:[-_ ]?[a-d])\b|\bdiagnos(?:is|tic)\b|\bp[1-7]\b)"
)
CASE_FILES = {
    "source-manifest.json",
    "relationship-graph.json",
    "chronology.json",
    "evaluation-points.json",
    "observed-production-contexts.json",
    "chronology-aware-contexts.json",
    "replay-manifest.json",
    "transcript.json",
    "human-arm-d-pack",
    "post-annotation-adjudication.json",
    "SHA256SUMS",
}
ALLOWED_CASE_EXTRAS = {
    "source-records", "arm-inputs", "post-adjudication", "README.md", "validate.sh"
}
MODEL_ACTORS = ("model", "codex", "assistant", "agent", "gpt", "grok", "claude")
ARM_D_GATE = {
    "arm_d_reference_type": "single_human_reference",
    "consensus_gold": False,
    "machine_authorship": "forbidden",
    "machine_output_revealed_before_lock": False,
    "pre_reveal_reference_lock": "required",
    "human_annotator_count": 1,
    "deterministic_materialisation_required": True,
    "deterministic_selector_resolution_required": True,
    "provenance_receipt_required": True,
    "transcript_first": True,
}
HUMAN_REFERENCE_WORKFLOW = {
    "reference_chain_artifact": "single-human-reference-chain-v2",
    "provenance_artifact": "single-human-provenance-v2",
    "reference_pack_artifact": "single-human-transcript-first-chain-pack-v2",
    "one_incremental_chain": True,
    "snapshot_derivation": "frozen_representation_prefixes",
    "offline_turn_annotation_required": True,
    "future_turn_display_forbidden": True,
}
HUMAN_AWARENESS_FIELDS = (
    "knew_working_hypothesis",
    "previously_seen_published_replies",
    "was_investigator",
)
FORBIDDEN_BLIND_KEYS = {
    "suspected_failure_class",
    "suspected_failure",
    "failure_class",
    "failure_labels",
    "working_diagnosis",
    "diagnosis",
    "production_outcome",
    "published_reply",
    "historical_account_reply",
    "arm_identity",
    "provider_or_model_identity",
}


class FourArmError(RuntimeError):
    """A fail-closed case, freeze, provenance or execution-gate error."""

    pass


def _json(path: Path, label: str) -> dict[str, Any]:
    value = private_io.strict_json_loads(private_io._read_regular_bytes(path, label))
    if not isinstance(value, dict):
        raise FourArmError(f"{label} is not an object")
    return value


def _sha(source: bytes) -> str:
    return hashlib.sha256(source).hexdigest()


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise FourArmError(f"{label} is not a timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FourArmError(f"{label} is not RFC3339") from exc
    if result.tzinfo is None:
        raise FourArmError(f"{label} lacks a timezone")
    return result.astimezone(timezone.utc)


def _private_directory(path: str | Path, label: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise FourArmError(f"{label} path must be absolute")
    try:
        metadata = candidate.lstat()
    except FileNotFoundError as exc:
        raise FourArmError(f"{label} is absent") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise FourArmError(f"{label} is not a real directory")
    return candidate


def _audit_private(root: Path) -> int:
    if stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise FourArmError(f"{root.name} mode is not 0700")
    count = 0
    for path in root.rglob("*"):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise FourArmError("private tree contains a symlink")
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISDIR(metadata.st_mode):
            if mode != 0o700:
                raise FourArmError(f"private directory mode is not 0700: {path.name}")
        elif stat.S_ISREG(metadata.st_mode):
            count += 1
            if mode != 0o600:
                raise FourArmError(f"private file mode is not 0600: {path.name}")
        else:
            raise FourArmError("private tree contains a special file")
    return count


def _checksum_lines(source: bytes) -> dict[str, str]:
    try:
        lines = source.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError as exc:
        raise FourArmError("checksum manifest is not UTF-8") from exc
    result: dict[str, str] = {}
    for line in lines:
        if len(line) < 67 or line[64:66] != "  ":
            raise FourArmError("invalid checksum line")
        digest, relative = line[:64], line[66:]
        if (
            not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not relative
            or relative.startswith("/")
            or ".." in Path(relative).parts
            or relative in result
        ):
            raise FourArmError("unsafe or duplicate checksum entry")
        result[relative] = digest
    return result


def _verify_case_sums(case: Path) -> tuple[str, int]:
    source = private_io._read_regular_bytes(case / "SHA256SUMS", "case SHA256SUMS")
    declared = _checksum_lines(source)
    actual = {
        path.relative_to(case).as_posix()
        for path in case.rglob("*")
        if path.is_file() and not path.is_symlink() and path.name != "SHA256SUMS"
    }
    if set(declared) != actual:
        raise FourArmError("case checksum inventory differs")
    for relative, digest in declared.items():
        if _sha(private_io._read_regular_bytes(case / relative, relative)) != digest:
            raise FourArmError(f"case checksum mismatch: {relative}")
    return _sha(source), len(declared)


def _tracked(record: Any, label: str, version: str | None = None) -> None:
    if not isinstance(record, Mapping) or (version and record.get("version") != version):
        raise FourArmError(f"{label} tracked reference differs")
    value = record.get("path")
    if not isinstance(value, str):
        raise FourArmError(f"{label} path is absent")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise FourArmError(f"{label} path is unsafe")
    path = PROJECT_DIR / relative
    raw = private_io._read_regular_bytes(path, label)
    if record.get("sha256") != _sha(raw):
        raise FourArmError(f"{label} tracked hash differs")


def _arm_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        item if isinstance(item, str) else str(item.get("arm_id"))
        for item in value
        if isinstance(item, (str, Mapping))
    )


def validate_freeze(path: str | Path = DEFAULT_FREEZE) -> dict[str, Any]:
    """Validate the tracked, hash-bound experiment freeze without provider access."""

    freeze = _json(Path(path), "experiment freeze")
    required = {
        "freeze_version", "protocol_status", "source_commit", "branch", "contracts",
        "profiles", "arms", "accepted_evaluation_aliases", "observed_context_policy",
        "leakage_policy", "arm_d_gate", "provider_policy", "private_case_freeze_sha256",
        "original_results_untouched", "source_completeness", "source_completeness_sha256",
        "protocol_amendment", "supersedes_freeze", "human_reference_workflow",
    }
    if not required <= set(freeze):
        raise FourArmError("experiment freeze is incomplete")
    if (
        freeze["freeze_version"] != "four-arm-supplemental-freeze-v4"
        or freeze["protocol_status"] != "frozen_post_specification_amendment"
    ):
        raise FourArmError("experiment protocol is not frozen")
    if not re.fullmatch(r"[0-9a-f]{40}", str(freeze["source_commit"])):
        raise FourArmError("frozen source commit is invalid")
    if freeze["accepted_evaluation_aliases"] != list(ALIASES) or _arm_ids(freeze["arms"]) != ARMS:
        raise FourArmError("frozen evaluation aliases or A-D arms differ")
    if freeze["original_results_untouched"] is not True:
        raise FourArmError("original results are not frozen untouched")
    completeness = freeze["source_completeness"]
    expected_completeness = {
        "reconstruction_grade": "B", "exact_text_complete": True,
        "parent_graph_complete": False, "chronology_complete": True,
        "account_publication_confirmed": True, "complete_prefix_through_turn": True,
    }
    if (
        not isinstance(completeness, Mapping)
        or any(completeness.get(key) != value for key, value in expected_completeness.items())
        or not isinstance(completeness.get("limitations"), list)
        or len(completeness["limitations"]) < 2
        or not all(isinstance(item, str) and item for item in completeness["limitations"])
        or not all(term in " ".join(completeness["limitations"]).lower() for term in ("parent", "referenced"))
        or freeze["source_completeness_sha256"] != _sha(private_io.canonical_json_bytes(completeness))
    ):
        raise FourArmError("frozen source-completeness record differs")
    contracts = freeze["contracts"]
    if not isinstance(contracts, Mapping):
        raise FourArmError("contract freeze is absent")
    for name, version in VERSIONS.items():
        _tracked(contracts.get(name), name, version)
    for name in ("evidence_transport", "ledger_prompt"):
        _tracked(contracts.get(name), name)
    _tracked(freeze["protocol_amendment"], "single-human protocol amendment")
    _tracked(
        freeze["supersedes_freeze"], "superseded experiment freeze",
        "four-arm-supplemental-freeze-v3",
    )
    workflow = freeze["human_reference_workflow"]
    if not isinstance(workflow, Mapping) or dict(workflow) != HUMAN_REFERENCE_WORKFLOW:
        raise FourArmError("single-chain human reference workflow differs")
    profiles = freeze["profiles"]
    if not isinstance(profiles, Mapping) or set(profiles) != {
        "arm_b_summary", "arm_c_ledger", "downstream"
    }:
        raise FourArmError("frozen profile set differs")
    common = {
        "provider", "model", "reasoning_effort", "max_output_tokens", "prompt_path",
        "prompt_sha256", "response_schema_path", "response_schema_sha256",
    }
    for name, profile in profiles.items():
        if not isinstance(profile, Mapping) or not common <= set(profile):
            raise FourArmError(f"{name} profile is incomplete")
        if any(profile[key] in (None, "", "pending") for key in common):
            raise FourArmError(f"{name} profile is not frozen")
        if not isinstance(profile["max_output_tokens"], int) or isinstance(
            profile["max_output_tokens"], bool
        ) or profile["max_output_tokens"] <= 0:
            raise FourArmError(f"{name} output budget is invalid")
        _tracked({"path": profile["prompt_path"], "sha256": profile["prompt_sha256"]}, f"{name} prompt")
        _tracked(
            {"path": profile["response_schema_path"], "sha256": profile["response_schema_sha256"]},
            f"{name} response schema",
        )
        expected = {
            "arm_b_summary": ("xai", "grok-4.6", "low", 8192),
            "arm_c_ledger": ("xai", "grok-4.6", "low", 8192),
            "downstream": ("openai", "gpt-5.6-sol", "medium", 900),
        }[name]
        observed_profile = (
            str(profile["provider"]).lower(), profile["model"],
            profile["reasoning_effort"], profile["max_output_tokens"],
        )
        if observed_profile != expected or profile.get("tools") != []:
            raise FourArmError(f"{name} selected model, budget or tool policy differs")
    downstream = profiles["downstream"]
    for field in ("timeout_seconds", "temperature", "store", "stream", "retries"):
        if field not in downstream or downstream[field] is None:
            raise FourArmError(f"downstream {field} is not frozen")
    if (
        downstream["timeout_seconds"] != 180 or downstream["temperature"] != 1
        or downstream["store"] is not False or downstream["stream"] is not False
        or downstream["retries"] != 0
        or downstream.get("postvalidator") != "downstream-reply-contract-v1"
    ):
        raise FourArmError("downstream storage, streaming or retries differ")
    if profiles["arm_c_ledger"].get("provider_response_schema_sha256") != XAI_PROVIDER_SCHEMA_SHA256:
        raise FourArmError("Arm C transformed provider-schema hash differs")
    observed = json.dumps(freeze["observed_context_policy"], sort_keys=True).lower()
    leakage = json.dumps(freeze["leakage_policy"], sort_keys=True).lower()
    if "not_retained" not in observed or not any(x in observed for x in ("blocked", "nonrunnable")):
        raise FourArmError("observed context policy does not block missing exact payloads")
    if not all(x in leakage for x in ("future", "diagnosis", "later_model")):
        raise FourArmError("leakage policy is incomplete")
    policy = freeze["provider_policy"]
    if not isinstance(policy, Mapping) or policy.get("at_most_once") is not True:
        raise FourArmError("provider policy is not at-most-once")
    if any(policy.get(key) != 0 for key in ("retries", "repair_calls", "fallback_calls", "preflight_calls")):
        raise FourArmError("provider retry/repair/fallback/preflight policy differs")
    if not isinstance(policy.get("live_execution_enabled"), bool) or not isinstance(
        policy.get("live_adapter_status"), str
    ) or not isinstance(policy.get("live_verification_performed"), bool):
        raise FourArmError("live adapter gate is not explicit")
    gate = freeze["arm_d_gate"]
    expected_gate_keys = {*ARM_D_GATE, "awareness_fields_required", "status"}
    if (
        not isinstance(gate, Mapping)
        or set(gate) != expected_gate_keys
        or any(gate.get(key) != value for key, value in ARM_D_GATE.items())
        or gate.get("status") != "pending_single_human_reference"
        or gate.get("awareness_fields_required") != [
            *HUMAN_AWARENESS_FIELDS, "independent_of_machine_ledger"
        ]
    ):
        raise FourArmError("Arm D single-human reference gate differs")
    if not re.fullmatch(r"[0-9a-f]{64}", str(freeze["private_case_freeze_sha256"])):
        raise FourArmError("private case freeze hash is invalid")
    return freeze


def _case_object(case: Path, name: str) -> dict[str, Any]:
    value = _json(case / name, name)
    if value.get("case_slug") != CASE_SLUG:
        raise FourArmError(f"{name} case slug differs")
    return value


def _turn_index(transcript: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    turns = transcript.get("turns")
    required = {
        "turn_id", "turn_index", "post_id", "speaker_id", "role", "created_at_utc",
        "parent_turn_id", "referenced_tweets", "text", "text_sha256",
        "relevant_evaluation_aliases", "scope",
    }
    if not isinstance(turns, list) or not turns:
        raise FourArmError("transcript turns are absent")
    result: dict[str, dict[str, Any]] = {}
    indexes: set[int] = set()
    for value in turns:
        if not isinstance(value, Mapping) or not required <= set(value):
            raise FourArmError("transcript turn is incomplete")
        turn = dict(value)
        turn_id, index = turn["turn_id"], turn["turn_index"]
        if not isinstance(turn_id, str) or turn_id in result or not isinstance(index, int) or isinstance(index, bool) or index in indexes:
            raise FourArmError("transcript turn identity is invalid")
        if not isinstance(turn["text"], str) or _sha(turn["text"].encode()) != turn["text_sha256"]:
            raise FourArmError("transcript exact-text hash differs")
        if turn["scope"] not in {"core", "operational_only"}:
            raise FourArmError("transcript scope is invalid")
        if not isinstance(turn["referenced_tweets"], list) or any(
            not isinstance(reference, Mapping)
            or not isinstance(reference.get("id"), str)
            or not isinstance(reference.get("type"), str)
            for reference in turn["referenced_tweets"]
        ):
            raise FourArmError("transcript referenced-tweet evidence is invalid")
        if not isinstance(turn["relevant_evaluation_aliases"], list) or any(
            alias not in ALIASES for alias in turn["relevant_evaluation_aliases"]
        ):
            raise FourArmError("turn relevance aliases differ")
        _utc(turn["created_at_utc"], f"{turn_id} creation")
        result[turn_id] = turn
        indexes.add(index)
    if sorted(indexes) != list(range(len(result))):
        raise FourArmError("transcript turn indices are not nonnegative and contiguous")
    for turn in result.values():
        parent = turn["parent_turn_id"]
        if parent is not None and (
            parent not in result or result[parent]["turn_index"] >= turn["turn_index"]
        ):
            raise FourArmError("transcript parent binding is invalid")
    return result


def _model_turn(turn: Mapping[str, Any]) -> dict[str, Any]:
    for field in ("turn_id", "speaker_id", "parent_turn_id"):
        value = turn[field]
        if value is not None and re.fullmatch(r"[0-9]{15,22}", str(value)):
            raise FourArmError("provider-bound context contains a raw source identity")
    return {key: turn[key] for key in (
        "turn_id", "turn_index", "speaker_id", "role", "created_at_utc",
        "parent_turn_id", "text",
    )}


def _contexts(value: Mapping[str, Any], label: str) -> dict[str, dict[str, Any]]:
    rows = value.get("contexts")
    if not isinstance(rows, list) or len(rows) != 3:
        raise FourArmError(f"{label} must have exactly three contexts")
    result = {str(row.get("evaluation_alias")): dict(row) for row in rows if isinstance(row, Mapping)}
    if set(result) != set(ALIASES):
        raise FourArmError(f"{label} aliases differ")
    return result


def _context_payload(context: Mapping[str, Any], transcript: Mapping[str, Any], turns: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    ids = context.get("turn_ids")
    if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)):
        raise FourArmError("chronology context turn list is invalid")
    try:
        selected = [turns[str(turn_id)] for turn_id in ids]
    except KeyError as exc:
        raise FourArmError("context references an unknown turn") from exc
    return {
        "conversation_key": transcript.get("conversation_key"),
        "reply_target_turn_id": context.get("reply_target_turn_id"),
        "ledger_as_of_turn_id": context.get("ledger_as_of_turn_id"),
        "turns": [_model_turn(turn) for turn in selected],
    }


def _context_common(
    context: Mapping[str, Any], alias: str, variant: str, no_future_status: str
) -> None:
    required = {
        "replay_input_id", "evaluation_alias", "context_variant", "reply_target_turn_id",
        "ledger_as_of_turn_id", "context_cutoff_utc", "exact_production_payload_status",
        "exact_production_payload", "turn_ids", "required_relevant_turn_ids", "excluded_turn_ids",
        "no_future_validation", "canonical_input_sha256", "replay_eligibility",
    }
    if not required <= set(context) or context["evaluation_alias"] != alias or context["context_variant"] != variant:
        raise FourArmError("context field, alias or variant differs")
    if (
        not isinstance(context["no_future_validation"], Mapping)
        or context["no_future_validation"].get("status") != no_future_status
    ):
        raise FourArmError("context no-future status differs")
    _utc(context["context_cutoff_utc"], f"{alias} cutoff")


def _reconstructed_edges(turns: Mapping[str, Mapping[str, Any]]) -> set[tuple[str, str, str]]:
    """Reconstruct retained direct-parent and referenced-tweet edges."""

    post_to_turn = {str(turn["post_id"]): turn_id for turn_id, turn in turns.items()}
    result: set[tuple[str, str, str]] = set()
    for turn_id, turn in turns.items():
        if turn["parent_turn_id"] is not None:
            result.add((turn_id, str(turn["parent_turn_id"]), "direct_parent"))
        for reference in turn["referenced_tweets"]:
            target = post_to_turn.get(str(reference["id"]))
            if target is not None:
                result.add((turn_id, target, f"referenced_tweet:{reference['type']}"))
    return result


def _graph_and_equality(source: Mapping[str, Any], graph: Mapping[str, Any], turns: Mapping[str, Mapping[str, Any]]) -> None:
    equality = source.get("equality_side_effect")
    if not isinstance(equality, Mapping) or equality != {
        "scope": "operational_only", "included_in_core": False, "included_in_semantic_ledger": False
    }:
        raise FourArmError("equality-in-liberty side effect is not separate")
    equality_id, core = graph.get("equality_turn_id"), set(map(str, graph.get("core_turn_ids", [])))
    nodes, edges = graph.get("nodes"), graph.get("edges")
    if not isinstance(equality_id, str) or not isinstance(nodes, list) or not isinstance(edges, list):
        raise FourArmError("relationship graph partition is absent")
    node = next((item for item in nodes if isinstance(item, Mapping) and item.get("turn_id") == equality_id), None)
    if not node or node.get("scope") != "operational_only" or equality_id in core:
        raise FourArmError("equality graph node entered the core")
    if equality_id not in turns or turns[equality_id]["scope"] != "operational_only":
        raise FourArmError("equality transcript turn is not operational-only")
    frozen_edges = {
        (
            str(edge.get("source_turn_id")), str(edge.get("target_turn_id")),
            str(edge.get("relationship_type")),
        )
        for edge in edges if isinstance(edge, Mapping)
    }
    if not _reconstructed_edges(turns) <= frozen_edges:
        raise FourArmError("frozen graph omits direct-parent or referenced-tweet evidence")
    for edge in edges:
        endpoints = {str(edge.get("source_turn_id")), str(edge.get("target_turn_id"))} if isinstance(edge, Mapping) else set()
        if equality_id in endpoints and endpoints & core:
            raise FourArmError("equality graph node is linked into core")


def _walk_keys(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        return set(map(str, value)) | set().union(*(_walk_keys(child) for child in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_walk_keys(child) for child in value), set())
    return set()


def _blind_pack(case: Path, post: Mapping[str, Any]) -> None:
    files = list((case / "human-arm-d-pack").rglob("*.json"))
    if not files or post.get("diagnosis_visible_to_models") is not False:
        raise FourArmError("blind human pack or diagnosis seal is absent")
    for path in files:
        value = private_io.strict_json_loads(private_io._read_regular_bytes(path, "human pack"))
        if _walk_keys(value) & FORBIDDEN_BLIND_KEYS:
            raise FourArmError("human pack exposes diagnosis, failure or condition labels")


def _derive_replays(
    transcript: Mapping[str, Any], evaluations: list[Any], observed: Mapping[str, Mapping[str, Any]],
    chronology: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    turns = _turn_index(transcript)
    evals = {str(row.get("evaluation_alias")): row for row in evaluations if isinstance(row, Mapping)}
    if set(evals) != set(ALIASES):
        raise FourArmError("evaluation aliases differ")
    replays, snapshots, context_hashes = [], {}, set()
    for alias in ALIASES:
        evaluation, actual, diagnostic = evals[alias], observed[alias], chronology[alias]
        _context_common(
            actual, alias, "observed_production", "not_verifiable_exact_payload_missing"
        )
        _context_common(diagnostic, alias, "chronology_aware", "passed")
        if not evaluation.get("accepted"):
            raise FourArmError("evaluation point is not accepted")
        if actual["exact_production_payload_status"] != "not_retained" or actual["exact_production_payload"] is not None or actual["canonical_input_sha256"] is not None or actual["replay_eligibility"] != "blocked_exact_payload_not_retained":
            raise FourArmError("observed production payload is incorrectly replayable")
        ancestry_ids = actual["turn_ids"]
        if not isinstance(ancestry_ids, list) or len(ancestry_ids) != len(set(ancestry_ids)):
            raise FourArmError("observed retained ancestry proxy is invalid")
        actual_cutoff = _utc(actual["context_cutoff_utc"], "observed cutoff")
        try:
            ancestry_turns = [turns[str(turn_id)] for turn_id in ancestry_ids]
        except KeyError as exc:
            raise FourArmError("observed ancestry proxy references an unknown turn") from exc
        if any(
            _utc(turn["created_at_utc"], "ancestry creation") > actual_cutoff
            for turn in ancestry_turns
        ) or set(map(str, ancestry_ids)) & set(map(str, actual["excluded_turn_ids"])):
            raise FourArmError("observed ancestry proxy crosses its retained cutoff")
        if diagnostic["exact_production_payload"] is not None or diagnostic["replay_eligibility"] != "runnable":
            raise FourArmError("chronology replay masquerades as production or is blocked")
        if evaluation.get("observed_context_id") != actual["replay_input_id"] or evaluation.get("chronology_aware_context_id") != diagnostic["replay_input_id"]:
            raise FourArmError("evaluation context binding differs")
        if actual["reply_target_turn_id"] != evaluation.get("reply_target_turn_id"):
            raise FourArmError("observed context target differs from accepted evaluation")
        for field in ("reply_target_turn_id", "ledger_as_of_turn_id", "context_cutoff_utc"):
            if evaluation.get(field) != diagnostic.get(field):
                raise FourArmError(f"evaluation {field} differs from context")
        payload = _context_payload(diagnostic, transcript, turns)
        context_hash = _sha(private_io.canonical_json_bytes(payload))
        if diagnostic["canonical_input_sha256"] != context_hash:
            raise FourArmError("chronology input hash differs")
        context_hashes.add(context_hash)
        ids, cutoff = tuple(map(str, diagnostic["turn_ids"])), _utc(diagnostic["context_cutoff_utc"], "cutoff")
        if str(diagnostic["reply_target_turn_id"]) not in ids:
            raise FourArmError("reply target is absent from chronology replay")
        indexes = [int(turns[turn_id]["turn_index"]) for turn_id in ids]
        if indexes != sorted(indexes) or len(indexes) != len(set(indexes)):
            raise FourArmError("chronology replay turn order differs")
        if set(ids) & set(map(str, diagnostic["excluded_turn_ids"])):
            raise FourArmError("chronology replay includes an excluded turn")
        for turn_id in ids:
            if _utc(turns[turn_id]["created_at_utc"], "turn creation") > cutoff or turns[turn_id]["scope"] != "core":
                raise FourArmError("chronology replay contains future or operational-only content")
        required = {
            turn_id for turn_id, turn in turns.items()
            if alias in turn["relevant_evaluation_aliases"] and turn["scope"] == "core"
            and _utc(turn["created_at_utc"], "turn creation") <= cutoff
        }
        if required != set(map(str, diagnostic["required_relevant_turn_ids"])) or not required <= set(ids):
            raise FourArmError("chronology replay omits a required pre-generation contribution")
        boundary = str(diagnostic["ledger_as_of_turn_id"])
        if boundary not in ids or boundary != ids[-1]:
            raise FourArmError("ledger snapshot boundary is not the final included turn")
        prefix = ids[: ids.index(boundary) + 1]
        snapshot_payload = {
            "conversation_key": transcript["conversation_key"],
            "turns": [_model_turn(turns[turn_id]) for turn_id in prefix],
        }
        snapshot_hash = _sha(private_io.canonical_json_bytes(snapshot_payload))
        snapshots.setdefault(snapshot_hash, {
            "snapshot_sha256": snapshot_hash, "turn_ids": list(prefix),
            "evaluation_aliases": [], "payload": snapshot_payload,
        })["evaluation_aliases"].append(alias)
        replays.append({
            "evaluation_alias": alias, "replay_input_id": diagnostic["replay_input_id"],
            "canonical_input_sha256": context_hash, "canonical_input": payload,
            "representation_snapshot_sha256": snapshot_hash,
        })
    if len(context_hashes) != 3 or len(snapshots) != 2:
        raise FourArmError("expected three target inputs and two representation snapshots")
    ordered = sorted(snapshots.values(), key=lambda row: (len(row["turn_ids"]), row["snapshot_sha256"]))
    if ordered[1]["turn_ids"][: len(ordered[0]["turn_ids"])] != ordered[0]["turn_ids"]:
        raise FourArmError("Arm C snapshots are not one incremental prefix chain")
    for number, snapshot in enumerate(ordered, 1):
        snapshot["snapshot_id"] = f"snapshot-{number:02d}"
        snapshot["evaluation_aliases"].sort()
    ids = {row["snapshot_sha256"]: row["snapshot_id"] for row in ordered}
    for replay in replays:
        replay["representation_snapshot_id"] = ids[replay["representation_snapshot_sha256"]]
        replay["arm_inputs"] = {
            "A": {"transcript_sha256": replay["canonical_input_sha256"], "representation": "none"},
            "B": {"transcript_sha256": replay["canonical_input_sha256"], "ordinary_summary_snapshot": replay["representation_snapshot_id"]},
            "C": {"transcript_sha256": replay["canonical_input_sha256"], "machine_ledger_snapshot": replay["representation_snapshot_id"]},
            "D": {"transcript_sha256": replay["canonical_input_sha256"], "human_ledger_snapshot": replay["representation_snapshot_id"]},
        }
        if tuple(replay["arm_inputs"]) != ARMS or any(_walk_keys(value) & FORBIDDEN_BLIND_KEYS for value in replay["arm_inputs"].values()):
            raise FourArmError("A-D input isolation differs")
    return {
        "plan_version": "market-planning-four-arm-replays-v1", "case_slug": CASE_SLUG,
        "accepted_evaluation_aliases": list(ALIASES), "runnable_replays": replays,
        "representation_snapshots": ordered, "unique_runnable_replay_inputs": 3,
        "unique_representation_snapshots": 2, "unique_downstream_arm_evaluations": 12,
    }


def validate_case(case: str | Path, *, experiment_freeze: str | Path = DEFAULT_FREEZE) -> dict[str, Any]:
    """Validate the immutable private case and derive its deduplicated replay plan."""

    root = _private_directory(case, "private case")
    private_count = _audit_private(root)
    root_names = {path.name for path in root.iterdir()}
    if not CASE_FILES <= root_names or root_names - CASE_FILES - ALLOWED_CASE_EXTRAS:
        raise FourArmError("private case root inventory differs")
    case_hash, checked = _verify_case_sums(root)
    freeze = validate_freeze(experiment_freeze)
    if freeze["private_case_freeze_sha256"] != case_hash:
        raise FourArmError("experiment freeze does not bind the private case")
    records = {name: _case_object(root, name) for name in CASE_FILES if name.endswith(".json")}
    source, replay_manifest = records["source-manifest.json"], records["replay-manifest.json"]
    for record in (source, replay_manifest):
        if set(record.get("status_labels", [])) != STATUS_LABELS:
            raise FourArmError("supplemental status labels differ")
    if source.get("original_prespecified_results_untouched") is not True:
        raise FourArmError("source manifest changes original results")
    if replay_manifest.get("accepted_evaluation_aliases") != list(ALIASES) or any(
        replay_manifest.get(field) != expected for field, expected in {
            "unique_chronology_replay_input_count": 3,
            "unique_representation_snapshot_count": 2,
            "prospective_downstream_arm_evaluation_count": 12,
        }.items()
    ):
        raise FourArmError("replay manifest counts or aliases differ")
    if replay_manifest.get("equality_operational_side_effect_only") is not True or replay_manifest.get("original_prespecified_results_untouched") is not True:
        raise FourArmError("replay manifest changes scope or original results")
    transcript, turns = records["transcript.json"], _turn_index(records["transcript.json"])
    _graph_and_equality(source, records["relationship-graph.json"], turns)
    events = records["chronology.json"].get("events")
    if not isinstance(events, list) or not events:
        raise FourArmError("chronology events are absent")
    for event in events:
        if not isinstance(event, Mapping) or not isinstance(event.get("event_type"), str):
            raise FourArmError("chronology event is incomplete")
        _utc(event.get("at_utc"), "chronology event")
    if not {
        "post_creation", "candidate_consideration", "model_generation", "publication"
    } <= {str(event["event_type"]) for event in events}:
        raise FourArmError("chronology collapses creation, consideration, generation or publication")
    evaluations = records["evaluation-points.json"].get("evaluation_points")
    if not isinstance(evaluations, list) or len(evaluations) != 3:
        raise FourArmError("exactly three evaluation points are required")
    for evaluation in evaluations:
        if not isinstance(evaluation, Mapping) or not isinstance(evaluation.get("generation"), Mapping):
            raise FourArmError("evaluation timing record is absent")
        for field in ("context_build_at_utc", "pipeline_approved_at_utc", "generated_text_observed_at_utc", "publication_at_utc"):
            value = evaluation["generation"].get(field)
            if value is not None:
                _utc(value, f"evaluation {field}")
    observed = _contexts(records["observed-production-contexts.json"], "observed contexts")
    chronology = _contexts(records["chronology-aware-contexts.json"], "chronology contexts")
    plan = _derive_replays(transcript, evaluations, observed, chronology)
    expected_bindings = [{
        "evaluation_alias": alias,
        "observed_context_id": observed[alias]["replay_input_id"],
        "chronology_aware_context_id": chronology[alias]["replay_input_id"],
    } for alias in ALIASES]
    if replay_manifest.get("bindings") != expected_bindings:
        raise FourArmError("replay bindings differ")
    _blind_pack(root, records["post-annotation-adjudication.json"])
    return {
        "status": "passed", "provider_calls": 0, "case_slug": CASE_SLUG,
        "accepted_evaluation_aliases": list(ALIASES), "unique_runnable_replay_inputs": 3,
        "unique_representation_snapshots": 2, "unique_arm_evaluations": 12,
        "private_case_freeze_sha256": case_hash, "checked_files": checked,
        "private_files": private_count, "replay_plan": plan,
    }


def _pack_turns(snapshot: Mapping[str, Any], transcript: Mapping[str, Any]) -> list[dict[str, Any]]:
    turns = _turn_index(transcript)
    result = []
    for index, turn_id in enumerate(snapshot["turn_ids"]):
        source = turns[turn_id]
        result.append({
            "conversation_key": transcript["conversation_key"], "turn_id": turn_id,
            "turn_index": index, "post_id": f"local-post:{turn_id}",
            "speaker_id": source["speaker_id"], "parent_turn_id": source["parent_turn_id"],
            "text": source["text"], "participant": {
                "participant_id": source["speaker_id"], "role": source["role"],
                "author_key": f"private:{source['speaker_id']}", "identity_confidence": 1.0,
            },
        })
    return result


def _prepared_human_readme(output: Path) -> bytes:
    return (
        "# Single-human incremental reference workflow\n\n"
        "Edit `provenance.json` honestly. Build `reference-chain.json` one turn at a time "
        "with the offline command below; do not edit immutable files under "
        "`frozen-guidance/`. Prior knowledge of the hypothesis, prior exposure to historical "
        "published replies, and investigator status are recorded rather than treated as "
        "disqualifying. Semantic judgements must be independent of the experiment's machine "
        "ledger.\n\n"
        "```sh\n"
        "python3 -m tools.proposition_ledger_four_arm_experiment "
        f"--annotate-human-reference --output {output}\n"
        "```\n\n"
        "The command shows exactly one current turn and the semantic objects materialised "
        "from already approved turns. Enter one JSON object containing every authorised "
        "semantic field, using empty arrays where the human judges that no item applies. "
        "Do not enter `local_ref`; the runner numbers local objects from collection order. "
        "Evidence uses exact current-turn text. Omit `occurrence_index` when that text occurs "
        "once; when it repeats, enter the intended zero-based occurrence. For "
        "contract-derived shapes and enums, run `--help-field new_propositions` (or, for "
        "example, `new_relations`, `commitment_changes`, or `issue_state_updates`). The "
        "runner derives metadata, character offsets, permanent IDs, hashes and both frozen "
        "snapshot ledgers.\n\n"
        "After all turns and `provenance.json` are complete, validate, materialise and "
        "pre-reveal hash-lock the reference with this one command:\n\n"
        "```sh\n"
        "python3 -m tools.proposition_ledger_four_arm_experiment --verify "
        f"--lock-human-reference --output {output}\n"
        "```\n\n"
        "Do not open any experiment-generated machine output before the lock succeeds.\n"
    ).encode("utf-8")


def _audit_prepared_human_pack(root: Path) -> None:
    """Fail closed if a distributed path or content reveals experimental identities."""

    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if HUMAN_VISIBLE_FORBIDDEN.search(relative):
            raise FourArmError(f"human annotation pack path leaks hidden information: {relative}")
        if path.is_file():
            source = private_io._read_regular_bytes(path, f"human annotation pack {relative}")
            try:
                text = source.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise FourArmError("human annotation pack contains a non-text file") from exc
            if HUMAN_VISIBLE_FORBIDDEN.search(text):
                raise FourArmError(f"human annotation pack content leaks hidden information: {relative}")


def _human_input_schema(binding: Mapping[str, Any]) -> dict[str, Any]:
    _tracked(binding, "human selector contract", VERSIONS["xai_transport"])
    return _json(PROJECT_DIR / str(binding["path"]), "human selector contract")


def _human_editable_fields(schema: Mapping[str, Any]) -> tuple[str, ...]:
    properties, required = schema.get("properties"), schema.get("required")
    if not isinstance(properties, Mapping) or not isinstance(required, list):
        raise FourArmError("human selector contract root is malformed")
    editable = tuple(name for name in required if name not in HUMAN_DETERMINISTIC_DELTA_FIELDS)
    if (
        not editable
        or set(properties) != set(required)
        or set(editable) != set(properties) - HUMAN_DETERMINISTIC_DELTA_FIELDS
    ):
        raise FourArmError("human selector contract does not expose one complete required surface")
    return editable


def _write_human_packs(
    output: Path, plan: Mapping[str, Any], transcript: Mapping[str, Any], source_pack: Path,
    source_completeness: Mapping[str, Any], contract_bindings: Mapping[str, Any],
) -> list[Path]:
    root = output / PREPARED_HUMAN_DIR
    private_io._ensure_private_directory(root)
    immutable: list[Path] = []
    guidance_root = root / "frozen-guidance"
    private_io._ensure_private_directory(guidance_root)
    selector_schema = _human_input_schema(contract_bindings["xai_transport"])
    editable_fields = _human_editable_fields(selector_schema)
    guide = guidance_root / "annotation-guide.json"
    _json(source_pack / "annotation-guide.json", "frozen annotation guide")
    private_io._write_private_json(guide, {
        "guide_version": "single-human-incremental-guide-v2",
        "reference_only": True,
        "human_judgement_fields": list(editable_fields),
        "evidence_selector_fields": ["exact_text", "occurrence_index"],
        "occurrence_index_rule": (
            "Omit only for uniquely occurring exact_text; choose the zero-based index "
            "when the text repeats."
        ),
        "deterministic_fields_are_derived": True,
        "derived_local_ref_formats": HUMAN_LOCAL_REF_FORMATS,
        "incremental_rule": "Approve exactly the next turn; frozen snapshots are derived prefixes.",
        "help_command": (
            "python3 -m tools.proposition_ledger_four_arm_experiment "
            "--help-field FIELD"
        ),
    })
    reference_notice = guidance_root / "REFERENCE-ONLY.txt"
    private_io._write_private_bytes(
        reference_notice,
        b"Immutable reference material only. Complete only the files named in ../README.md.\n",
    )
    readme = root / "README.md"
    private_io._write_private_bytes(readme, _prepared_human_readme(output))
    immutable.extend((guide, reference_notice, readme))
    snapshots = list(plan["representation_snapshots"])
    longest = max(snapshots, key=lambda item: len(item["turn_ids"]))
    if any(longest["turn_ids"][: len(item["turn_ids"])] != item["turn_ids"] for item in snapshots):
        raise FourArmError("human reference snapshots are not one incremental prefix chain")
    snapshot_rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        source_template = _json(
            source_pack / snapshot["snapshot_id"] / "blank-semantic-delta-chain.json",
            "frozen semantic-delta template",
        ).get("chain")
        if not isinstance(source_template, list) or [
            item.get("turn_id") for item in source_template if isinstance(item, Mapping)
        ] != snapshot["turn_ids"] or any(
            not isinstance(item, Mapping) or not isinstance(item.get("semantic_delta_template"), Mapping)
            for item in source_template
        ):
            raise FourArmError("frozen human semantic-delta template differs from snapshot")
        prefix_transcript = {
            "conversation_key": transcript["conversation_key"],
            "turns": _pack_turns(snapshot, transcript),
        }
        snapshot_rows.append({
            "snapshot_id": snapshot["snapshot_id"],
            "terminal_turn_id": snapshot["turn_ids"][-1],
            "prefix_length": len(snapshot["turn_ids"]),
            "transcript_prefix_sha256": _sha(private_io.canonical_json_bytes(prefix_transcript)),
            "evaluation_aliases": snapshot["evaluation_aliases"],
        })
    transcript_value = {
        "conversation_key": transcript["conversation_key"],
        "turns": _pack_turns(longest, transcript),
    }
    pack = {
        "pack_version": HUMAN_REFERENCE_WORKFLOW["reference_pack_artifact"],
        "case_slug": CASE_SLUG, "status": "pending_human_reference",
        "reference_type": "single_human_reference", "consensus_gold": False,
        "transcript_sha256": _sha(private_io.canonical_json_bytes(transcript_value)),
        "transcript": transcript_value, "snapshot_bindings": snapshot_rows,
        "annotation_contract": {
            "human_selector_version": HUMAN_SELECTOR_VERSION,
            "selector_contract_sha256": contract_bindings["xai_transport"]["sha256"],
            "canonical_target_schema_version": VERSIONS["canonical_semantic_delta"],
            "canonical_schema": copy.deepcopy(contract_bindings["canonical_semantic_delta"]),
            "persisted_ledger_schema": copy.deepcopy(contract_bindings["persisted_ledger"]),
            "evidence_selector_contract_version": "exact-text-occurrence-index-v2.0.2",
            "evidence_selector_fields": ["exact_text", "occurrence_index"],
            "editable_semantic_fields": list(editable_fields),
            "resolution": "frozen deterministic evidence resolution before canonical materialisation",
        },
        "source_completeness": copy.deepcopy(dict(source_completeness)),
        "prior_reference_resolution": {
            "sentinel": PRIOR_LEDGER_SENTINEL,
            "rule": "Resolve the exact non-genesis sentinel to the immediately prior materialised ledger.",
            "submitted_artifact_mutated": False,
        },
    }
    pack_path = guidance_root / HUMAN_REFERENCE_PACK_FILE
    private_io._write_private_json(pack_path, pack)
    immutable.append(pack_path)
    pack_hash = _sha(private_io._read_regular_bytes(pack_path, "human reference pack"))
    private_io._write_private_json(root / HUMAN_REFERENCE_CHAIN_FILE, {
        "artifact_version": HUMAN_REFERENCE_WORKFLOW["reference_chain_artifact"],
        "reference_pack_sha256": pack_hash,
        "status": "pending_human_reference",
        "semantic_judgements_authored_by_human": None,
        "semantic_delta_chain": [],
        "completed_at_utc": None,
        "reference_type": "single_human_reference", "consensus_gold": False,
        "machine_authorship": "forbidden",
    })
    private_io._write_private_json(root / "provenance.json", {
        "artifact_version": HUMAN_REFERENCE_WORKFLOW["provenance_artifact"],
        "reference_pack_sha256": pack_hash,
        "status": "pending_human_provenance", "actor_type": None,
        "annotator_id": None,
        "knew_working_hypothesis": None,
        "previously_seen_published_replies": None,
        "was_investigator": None,
        "independent_of_machine_ledger": None,
        "machine_authorship": "forbidden",
        "machine_output_revealed_before_lock": None,
        "completed_at_utc": None,
    })
    index_path = root / "index.json"
    private_io._write_private_json(index_path, {
        "pack_index_version": "single-human-reference-chain-index-v2", "case_slug": CASE_SLUG,
        "status": "pending_human_reference", "reference_type": "single_human_reference",
        "consensus_gold": False, "reference_pack_sha256": pack_hash,
        "reference_chain_file": HUMAN_REFERENCE_CHAIN_FILE,
        "snapshot_bindings": snapshot_rows,
    })
    _audit_prepared_human_pack(root)
    return [*immutable, index_path]


def _provider_schema(profile_name: str, profile: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    canonical = _json(PROJECT_DIR / profile["response_schema_path"], "response schema")
    if profile_name != "arm_c_ledger":
        return canonical, _sha(private_io.canonical_json_bytes(canonical))
    from tools import proposition_ledger_xai_provider_preflight as established

    first, first_ledger = established.transform_provider_schema(canonical)
    second, second_ledger = established.transform_provider_schema(canonical)
    if private_io.canonical_json_bytes((first, first_ledger)) != private_io.canonical_json_bytes((second, second_ledger)):
        raise FourArmError("xAI provider-schema transformation is not deterministic")
    allowed = {
        "identity", "insert_explicit_additional_properties_true",
        "remove_redundant_outer_anchors_for_xai_full_string_pattern",
    }
    if any(row.get("transformation_kind") not in allowed for row in first_ledger):
        raise FourArmError("xAI provider-schema transformation kind differs")
    return first, _sha(private_io.canonical_json_bytes(first))


def _call_plan(replays: Mapping[str, Any], freeze: Mapping[str, Any], freeze_hash: str) -> dict[str, Any]:
    snapshots, rows = replays["representation_snapshots"], []
    longest = max(snapshots, key=lambda row: len(row["turn_ids"]))
    profiles = freeze["profiles"]
    postvalidation = {
        "arm_c_incremental_delta": [
            "strict_json", "frozen_response_schema", "exact_text_occurrence_resolution",
            "semantic_delta_materialisation", "persisted_ledger_validation",
        ],
        "arm_b_ordinary_summary": ["strict_json", "frozen_response_schema", "equal_budget_validation"],
        "downstream_arm_evaluation": [
            "strict_json", "frozen_response_schema",
            {
                "validator": "downstream-reply-contract-v1",
                "status_reply": "reply.strip() must be nonempty and len(reply) <= 270",
                "status_cannot_compose_safely": "reply must equal the empty string",
                "retry_on_failure": False,
            },
        ],
    }
    def add(operation: str, identity: str, dependencies: list[str]) -> str:
        profile_name = {
            "arm_c_incremental_delta": "arm_c_ledger", "arm_b_ordinary_summary": "arm_b_summary",
            "downstream_arm_evaluation": "downstream",
        }[operation]
        profile = profiles[profile_name]
        _, provider_schema_hash = _provider_schema(profile_name, profile)
        call_id = f"call-{len(rows) + 1:03d}-{identity}"
        rows.append({
            "sequence": len(rows) + 1, "call_id": call_id, "operation": operation,
            "identity": identity, "dependency_call_ids": dependencies, "profile": profile_name,
            "prompt_sha256": profile["prompt_sha256"],
            "response_schema_sha256": profile["response_schema_sha256"],
            "provider_response_schema_sha256": provider_schema_hash,
            "postvalidation": postvalidation[operation],
            "postvalidator_id": (
                profile.get("postvalidator")
                if operation == "downstream_arm_evaluation"
                else postvalidation[operation][-1]
            ),
            "request_derivation_sha256": _sha(private_io.canonical_json_bytes({
                "freeze_sha256": freeze_hash, "operation": operation, "identity": identity,
                "dependencies": dependencies, "prompt_sha256": profile["prompt_sha256"],
                "response_schema_sha256": profile["response_schema_sha256"],
                "provider_response_schema_sha256": provider_schema_hash,
                "postvalidation": postvalidation[operation],
            })),
        })
        return call_id
    ledger, previous = {}, []
    for turn_id in longest["turn_ids"]:
        call_id = add("arm_c_incremental_delta", f"ledger-{turn_id}", previous[-1:])
        ledger[turn_id], previous = call_id, [*previous, call_id]
    summary = {}
    for snapshot in snapshots:
        summary[snapshot["snapshot_id"]] = add(
            "arm_b_ordinary_summary", f"summary-{snapshot['snapshot_id']}",
            [],
        )
    for replay in replays["runnable_replays"]:
        snapshot = next(row for row in snapshots if row["snapshot_id"] == replay["representation_snapshot_id"])
        for arm in ARMS:
            dependencies = {
                "A": [], "B": [summary[snapshot["snapshot_id"]]],
                "C": [ledger[snapshot["turn_ids"][-1]]],
                "D": [f"human-reference:{snapshot['snapshot_id']}"],
            }[arm]
            add("downstream_arm_evaluation", f"{replay['evaluation_alias']}-arm-{arm}", dependencies)
    if len(rows) != len(longest["turn_ids"]) + 2 + 12:
        raise FourArmError("provider budget omits B/C upstream calls")
    return {
        "call_plan_version": "four-arm-at-most-once-v1", "freeze_sha256": freeze_hash,
        "unique_arm_evaluations": 12, "incremental_arm_c_call_count": len(longest["turn_ids"]),
        "arm_b_summary_call_count": 2, "planned_provider_call_count": len(rows),
        "automatic_retry_count": 0, "repair_call_count": 0, "fallback_call_count": 0,
        "entries": rows,
    }


def _call_ledger(plan: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(plan))
    result["provider_call_count"] = 0
    result["entries"] = [{
        **copy.deepcopy(row), "state": "planned", "attempt_number": 0, "provider_call_count": 0,
        "request_sha256": None, "started_at_utc": None, "completed_at_utc": None,
        "state_history": [{"state": "planned", "at_utc": None, "reason": "frozen_plan"}],
    } for row in plan["entries"]]
    return result


def _immutable_sums(output: Path, paths: Sequence[Path]) -> None:
    lines = []
    for path in sorted(set(paths)):
        relative = path.relative_to(output).as_posix()
        lines.append(f"{_sha(private_io._read_regular_bytes(path, relative))}  {relative}\n")
    private_io._write_private_bytes(output / "immutable-SHA256SUMS", "".join(lines).encode())


def _verify_immutable(output: Path) -> int:
    declared = _checksum_lines(private_io._read_regular_bytes(output / "immutable-SHA256SUMS", "immutable sums"))
    for relative, digest in declared.items():
        if _sha(private_io._read_regular_bytes(output / relative, relative)) != digest:
            raise FourArmError(f"immutable prepared file changed: {relative}")
    return len(declared)


def prepare_run(case: str | Path, output: str | Path, *, experiment_freeze: str | Path = DEFAULT_FREEZE) -> dict[str, Any]:
    """Create one private zero-call plan and blank transcript-first human packs."""

    validation = validate_case(case, experiment_freeze=experiment_freeze)
    output_path = Path(output)
    if not output_path.is_absolute() or os.path.lexists(output_path):
        raise FourArmError("private output must be a new absolute path")
    try:
        output_path.relative_to(PROJECT_DIR)
    except ValueError:
        pass
    else:
        raise FourArmError("private output must stay outside Git")
    if not output_path.parent.is_dir() or output_path.parent.is_symlink():
        raise FourArmError("private output parent is unsafe")
    output_path.mkdir(mode=0o700)
    freeze_raw = private_io._read_regular_bytes(Path(experiment_freeze), "experiment freeze")
    freeze, freeze_hash = validate_freeze(experiment_freeze), _sha(freeze_raw)
    immutable = []
    for name, value in (
        ("experiment-freeze.json", freeze_raw),
        ("case-binding.json", private_io.pretty_json_bytes({
            "binding_version": "supplemental-case-binding-v1", "case_slug": CASE_SLUG,
            "case_directory": str(Path(case)), "case_sha256sums_sha256": validation["private_case_freeze_sha256"],
            "experiment_freeze_sha256": freeze_hash,
        })),
        ("replay-plan.json", private_io.pretty_json_bytes(validation["replay_plan"])),
    ):
        path = output_path / name
        private_io._write_private_bytes(path, value)
        immutable.append(path)
    transcript = _case_object(Path(case), "transcript.json")
    immutable.extend(
        _write_human_packs(
            output_path, validation["replay_plan"], transcript,
            Path(case) / "human-arm-d-pack",
            freeze["source_completeness"], freeze["contracts"],
        )
    )
    plan = _call_plan(validation["replay_plan"], freeze, freeze_hash)
    plan_path = output_path / "call-plan.json"
    private_io._write_private_json(plan_path, plan)
    immutable.append(plan_path)
    private_io._write_private_json(output_path / "call-ledger.json", _call_ledger(plan))
    result = {
        "status": "prepared_pending_human_reference", "provider_calls": 0,
        "case_slug": CASE_SLUG, "accepted_evaluation_aliases": list(ALIASES),
        "unique_runnable_replay_inputs": 3, "unique_representation_snapshots": 2,
        "unique_arm_evaluations": 12, "planned_provider_calls": plan["planned_provider_call_count"],
        "arm_d_status": "pending_human_reference",
    }
    result_path = output_path / "prepare-result.json"
    private_io._write_private_json(result_path, result)
    immutable.append(result_path)
    _immutable_sums(output_path, immutable)
    _audit_private(output_path)
    return result


def _validate_ledger(plan: Mapping[str, Any], ledger: Mapping[str, Any]) -> None:
    if ledger.get("planned_provider_call_count") != len(plan["entries"]) or ledger.get("automatic_retry_count") != 0 or ledger.get("repair_call_count") != 0 or ledger.get("fallback_call_count") != 0:
        raise FourArmError("durable call ledger counts differ")
    rows = ledger.get("entries")
    if not isinstance(rows, list) or len(rows) != len(plan["entries"]):
        raise FourArmError("durable call ledger entries differ")
    attempted = 0
    for frozen, row in zip(plan["entries"], rows):
        if not isinstance(row, Mapping) or any(row.get(key) != value for key, value in frozen.items()):
            raise FourArmError("call identity changed")
        state, count = row.get("state"), row.get("provider_call_count")
        if state not in {"planned", "sending", "response_received", "completed", "failed_provider", "failed_validation", "blocked_dependency", "reused_exact_request", "uncertain_after_send", "not_attempted_due_to_global_failure"} or count not in {0, 1} or row.get("attempt_number") != count:
            raise FourArmError("call state or at-most-once count differs")
        history = row.get("state_history")
        if not isinstance(history, list) or not history or history[0].get("state") != "planned" or history[-1].get("state") != state:
            raise FourArmError("call state history differs")
        if count and "sending" not in {item.get("state") for item in history}:
            raise FourArmError("attempted call lacks durable sending state")
        sent_states = {
            "sending", "response_received", "completed", "failed_provider",
            "failed_validation", "uncertain_after_send",
        }
        unsent_states = {
            "planned", "blocked_dependency", "reused_exact_request",
            "not_attempted_due_to_global_failure",
        }
        if (state in sent_states and count != 1) or (state in unsent_states and count != 0):
            raise FourArmError("call state and recorded provider count disagree")
        attempted += count
    if ledger.get("provider_call_count") != attempted or attempted > len(rows):
        raise FourArmError("provider-call total differs")


def _materialised_ledgers(
    pack: Mapping[str, Any], chain: Any, *, transport_binding: Mapping[str, Any] | None = None,
    require_complete: bool = True,
) -> list[dict[str, Any]]:
    turns = pack.get("transcript", {}).get("turns", [])
    if (
        not isinstance(turns, list)
        or not isinstance(chain, list)
        or len(chain) > len(turns)
        or (require_complete and len(chain) != len(turns))
    ):
        raise FourArmError(
            "/semantic_delta_chain: chain does not cover the required incremental prefix"
        )
    contract = pack.get("annotation_contract")
    if (
        not isinstance(contract, Mapping)
        or contract.get("human_selector_version") != HUMAN_SELECTOR_VERSION
        or contract.get("canonical_target_schema_version") != VERSIONS["canonical_semantic_delta"]
        or contract.get("evidence_selector_contract_version") != "exact-text-occurrence-index-v2.0.2"
        or contract.get("evidence_selector_fields") != ["exact_text", "occurrence_index"]
    ):
        raise FourArmError("human evidence-resolution contract differs")
    if transport_binding is None:
        transport_binding = validate_freeze(DEFAULT_FREEZE)["contracts"]["xai_transport"]
    if contract.get("selector_contract_sha256") != transport_binding.get("sha256"):
        raise FourArmError("human selector contract hash differs")
    canonical_binding = contract.get("canonical_schema")
    ledger_binding = contract.get("persisted_ledger_schema")
    _tracked(transport_binding, "internal evidence transport schema", VERSIONS["xai_transport"])
    _tracked(canonical_binding, "human canonical schema", VERSIONS["canonical_semantic_delta"])
    _tracked(ledger_binding, "human persisted ledger schema", VERSIONS["persisted_ledger"])
    transport_schema = _json(
        PROJECT_DIR / transport_binding["path"], "internal evidence transport schema"
    )
    canonical_schema = _json(PROJECT_DIR / canonical_binding["path"], "human canonical schema")
    ledger_schema = _json(PROJECT_DIR / ledger_binding["path"], "human persisted ledger schema")
    from tools import proposition_ledger_evidence_transport as evidence
    from tools import proposition_ledger_semantic_delta as semantic
    prior = None
    results: list[dict[str, Any]] = []
    for index, (turn, delta) in enumerate(zip(turns, chain)):
        pointer = f"/semantic_delta_chain/{index}"
        if not isinstance(delta, Mapping):
            raise FourArmError(f"{pointer}: human selector delta must be an object")
        delta = copy.deepcopy(delta)
        for field, expected in (
            ("schema_version", HUMAN_SELECTOR_VERSION),
            ("canonical_schema_version", VERSIONS["canonical_semantic_delta"]),
        ):
            if delta.get(field) != expected:
                raise FourArmError(f"{pointer}/{field}: human selector identity differs")
        expected_metadata = {
            "conversation_key": pack["transcript"]["conversation_key"],
            "target_turn_id": turn["turn_id"], "as_of_turn_index": index,
            "prior_ledger_reference": None if index == 0 else {
                "ledger_id": PRIOR_LEDGER_SENTINEL, "as_of_turn_index": index - 1,
            },
        }
        for field, expected in expected_metadata.items():
            if delta.get(field) != expected:
                raise FourArmError(
                    f"{pointer}/{field}: deterministic current-turn binding differs"
                )
        delta["schema_version"] = VERSIONS["xai_transport"]
        if prior is not None and delta.get("prior_ledger_reference") == {
            "ledger_id": PRIOR_LEDGER_SENTINEL, "as_of_turn_index": index - 1,
        }:
            delta["prior_ledger_reference"] = {
                "ledger_id": prior["ledger_id"],
                "as_of_turn_index": prior["as_of_turn_index"],
            }
        genesis = None if index else {
            "conversation_key": pack["transcript"]["conversation_key"],
            "current_participant": turn["participant"], "root_post_id": turn["post_id"],
            "source_completeness": copy.deepcopy(pack["source_completeness"]),
        }
        resolution = evidence.resolve_transport_delta(
            delta, current_turn_id=turn["turn_id"], current_turn_text=turn["text"],
            transport_schema=transport_schema, canonical_schema=canonical_schema,
        )
        if not resolution.succeeded or resolution.canonical_delta is None:
            details = " | ".join(str(error) for error in resolution.errors[:4])
            raise FourArmError(
                f"{pointer} turn {turn['turn_id']}: human evidence resolution failed: "
                f"{resolution.status}: {details}; rule=transport schema and exact evidence"
            )
        result = semantic.materialise_semantic_delta(
            prior, turn, resolution.canonical_delta,
            current_participant=turn["participant"], genesis_context=genesis,
            semantic_schema=canonical_schema, ledger_schema=ledger_schema,
        )
        if result.status != "ok" or result.ledger is None:
            details = " | ".join(str(error) for error in result.errors[:4])
            raise FourArmError(
                f"{pointer} turn {turn['turn_id']}: human semantic consistency failed: "
                f"{result.status}: {details}; rule=canonical cross-record consistency"
            )
        prior = result.ledger
        results.append(prior)
    return results


def _materialised_ledger(
    pack: Mapping[str, Any], chain: Any, *, transport_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ledgers = _materialised_ledgers(pack, chain, transport_binding=transport_binding)
    if not ledgers:
        raise FourArmError("human semantic chain did not produce a ledger")
    return ledgers[-1]


def _materialise(
    pack: Mapping[str, Any], chain: Any, *, transport_binding: Mapping[str, Any] | None = None,
) -> str:
    return str(_materialised_ledger(
        pack, chain, transport_binding=transport_binding,
    )["ledger_sha256"])


def _human_identity(value: Any) -> str:
    identity = value.strip().casefold() if isinstance(value, str) else ""
    if not identity or any(marker in identity for marker in MODEL_ACTORS):
        raise FourArmError("Arm D reference lacks genuine human authorship")
    return identity


def _human_submission(output: Path) -> dict[str, Any]:
    human_root = output / PREPARED_HUMAN_DIR
    freeze = validate_freeze(output / "experiment-freeze.json")
    transport_binding = freeze["contracts"]["xai_transport"]
    index = _json(human_root / "index.json", "human reference index")
    expected_rows = _json(output / "replay-plan.json", "replay plan").get(
        "representation_snapshots"
    )
    if (
        index.get("pack_index_version") != "single-human-reference-chain-index-v2"
        or index.get("status") != "pending_human_reference"
        or index.get("reference_type") != "single_human_reference"
        or index.get("consensus_gold") is not False
        or not isinstance(expected_rows, list) or len(expected_rows) != 2
        or index.get("reference_chain_file") != HUMAN_REFERENCE_CHAIN_FILE
    ):
        raise FourArmError("human reference index differs from the frozen incremental chain")
    pack_path = human_root / "frozen-guidance" / HUMAN_REFERENCE_PACK_FILE
    pack = _json(pack_path, "human reference pack")
    pack_hash = _sha(private_io._read_regular_bytes(pack_path, "human reference pack"))
    transcript = pack.get("transcript")
    if not isinstance(transcript, Mapping) or not isinstance(transcript.get("turns"), list):
        raise FourArmError("human reference pack transcript is malformed")
    transcript_turns = transcript["turns"]
    longest = max(expected_rows, key=lambda row: len(row["turn_ids"]))
    expected_bindings = []
    for row in expected_rows:
        prefix = {"conversation_key": transcript.get("conversation_key"), "turns": [
            turn for turn in transcript_turns[: len(row["turn_ids"])]
        ]}
        expected_bindings.append({
            "snapshot_id": row["snapshot_id"], "terminal_turn_id": row["turn_ids"][-1],
            "prefix_length": len(row["turn_ids"]),
            "transcript_prefix_sha256": _sha(private_io.canonical_json_bytes(prefix)),
            "evaluation_aliases": row["evaluation_aliases"],
        })
    if (
        pack.get("pack_version") != HUMAN_REFERENCE_WORKFLOW["reference_pack_artifact"]
        or pack.get("status") != "pending_human_reference"
        or pack.get("reference_type") != "single_human_reference"
        or pack.get("consensus_gold") is not False
        or [turn.get("turn_id") for turn in transcript_turns] != longest["turn_ids"]
        or pack.get("transcript_sha256") != _sha(private_io.canonical_json_bytes(transcript))
        or pack.get("snapshot_bindings") != expected_bindings
        or index.get("snapshot_bindings") != expected_bindings
        or index.get("reference_pack_sha256") != pack_hash
    ):
        raise FourArmError("human reference pack differs from the frozen incremental chain")
    selector_schema = _human_input_schema(transport_binding)
    if pack.get("annotation_contract", {}).get("editable_semantic_fields") != list(
        _human_editable_fields(selector_schema)
    ):
        raise FourArmError("human reference editable surface differs from the frozen contract")
    provenance = _json(human_root / "provenance.json", "human provenance")
    reference_path = human_root / HUMAN_REFERENCE_CHAIN_FILE
    reference = _json(reference_path, "human reference chain")
    chain = reference.get("semantic_delta_chain")
    if not isinstance(chain, list) or len(chain) > len(longest["turn_ids"]):
        raise FourArmError("human reference chain length is invalid")
    expected_status = (
        "pending_human_reference" if not chain else
        "completed_human_reference" if len(chain) == len(longest["turn_ids"]) else
        "in_progress_human_reference"
    )
    if (
        reference.get("artifact_version") != HUMAN_REFERENCE_WORKFLOW["reference_chain_artifact"]
        or reference.get("reference_pack_sha256") != pack_hash
        or reference.get("status") != expected_status
        or reference.get("reference_type") != "single_human_reference"
        or reference.get("consensus_gold") is not False
        or reference.get("machine_authorship") != "forbidden"
        or (bool(chain) and reference.get("semantic_judgements_authored_by_human") is not True)
        or (not chain and reference.get("semantic_judgements_authored_by_human") is not None)
        or (
            not isinstance(reference.get("completed_at_utc"), str)
            if expected_status == "completed_human_reference"
            else reference.get("completed_at_utc") is not None
        )
    ):
        raise FourArmError("human reference chain binding, status or classification differs")
    ledgers = _materialised_ledgers(
        pack, chain, transport_binding=transport_binding, require_complete=False,
    )
    provenance_status = provenance.get("status")
    if (
        provenance.get("artifact_version") != HUMAN_REFERENCE_WORKFLOW["provenance_artifact"]
        or provenance.get("reference_pack_sha256") != pack_hash
        or provenance.get("machine_authorship") != "forbidden"
    ):
        raise FourArmError("human provenance classification differs")
    if provenance_status not in {"pending_human_provenance", "completed_human_provenance"}:
        raise FourArmError("human provenance status is invalid")
    if provenance_status == "pending_human_provenance" and provenance.get("actor_type") not in (None, "human"):
        raise FourArmError("pending Arm D provenance names a model/non-human actor")
    identity = None
    if provenance_status == "completed_human_provenance":
        identity = _human_identity(provenance.get("annotator_id"))
        if (
            provenance.get("actor_type") != "human"
            or provenance.get("machine_authorship") != "forbidden"
            or provenance.get("independent_of_machine_ledger") is not True
            or provenance.get("machine_output_revealed_before_lock") is not False
            or any(type(provenance.get(field)) is not bool for field in HUMAN_AWARENESS_FIELDS)
        ):
            raise FourArmError("Arm D human provenance is invalid or conceals awareness")
    if provenance_status != "completed_human_provenance" or expected_status != "completed_human_reference":
        return {
            "status": expected_status, "pack": pack, "reference": reference,
            "ledgers": ledgers,
        }
    completion_times = [
        _utc(provenance.get("completed_at_utc"), "provenance completion"),
        _utc(reference.get("completed_at_utc"), "reference completion"),
    ]
    records = [{
        "snapshot_id": binding["snapshot_id"], "reference_pack_sha256": pack_hash,
        "transcript_prefix_sha256": binding["transcript_prefix_sha256"],
        "ledger": ledgers[binding["prefix_length"] - 1],
    } for binding in expected_bindings]
    return {
        "status": "completed_unlocked", "records": records,
        "provenance": provenance, "annotator_identity": identity,
        "provenance_sha256": _sha(private_io._read_regular_bytes(
            human_root / "provenance.json", "human provenance"
        )),
        "reference_sha256": _sha(private_io._read_regular_bytes(
            reference_path, "human reference chain"
        )),
        "pack_sha256": pack_hash, "latest_completion": max(completion_times),
    }


def _materialised_reference_artifact(submission: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "artifact_version": "single-human-materialised-reference-v1",
        "reference_type": "single_human_reference", "consensus_gold": False,
        "provenance_file_sha256": submission["provenance_sha256"],
        "reference_chain_sha256": submission["reference_sha256"],
        "snapshot_ledgers": [{
            "snapshot_id": record["snapshot_id"],
            "reference_pack_sha256": record["reference_pack_sha256"],
            "transcript_prefix_sha256": record["transcript_prefix_sha256"],
            "ledger": record["ledger"],
        } for record in submission["records"]],
    }


def _schema_ref(schema: Mapping[str, Any], reference: str) -> Mapping[str, Any]:
    if not reference.startswith("#/"):
        raise FourArmError("human help contract contains an external schema reference")
    value: Any = schema
    for raw in reference[2:].split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, Mapping) or part not in value:
            raise FourArmError("human help contract contains an unresolved schema reference")
        value = value[part]
    if not isinstance(value, Mapping):
        raise FourArmError("human help schema reference is not an object")
    return value


def _schema_outline(
    schema: Mapping[str, Any], node: Mapping[str, Any], *, active_refs: tuple[str, ...] = (),
) -> dict[str, Any]:
    if "$ref" in node:
        reference = node["$ref"]
        if not isinstance(reference, str) or reference in active_refs:
            return {"contract_ref": reference, "recursive": True}
        resolved = _schema_outline(
            schema, _schema_ref(schema, reference), active_refs=(*active_refs, reference),
        )
        return {"contract_ref": reference, **resolved}
    result = {
        key: copy.deepcopy(node[key]) for key in (
            "type", "description", "const", "enum", "minimum", "maximum", "minLength",
            "maxLength", "pattern", "minItems", "maxItems", "minProperties", "maxProperties", "uniqueItems",
            "additionalProperties",
        ) if key in node
    }
    required = node.get("required")
    if isinstance(required, list):
        result["required"] = copy.deepcopy(required)
    properties = node.get("properties")
    if isinstance(properties, Mapping):
        result["properties"] = {
            str(name): _schema_outline(schema, child, active_refs=active_refs)
            for name, child in properties.items() if isinstance(child, Mapping)
        }
    items = node.get("items")
    if isinstance(items, Mapping):
        result["items"] = _schema_outline(schema, items, active_refs=active_refs)
    for keyword in ("oneOf", "anyOf", "allOf"):
        alternatives = node.get(keyword)
        if isinstance(alternatives, list):
            result[keyword] = [
                _schema_outline(schema, child, active_refs=active_refs)
                for child in alternatives if isinstance(child, Mapping)
            ]
    for keyword in ("if", "then", "else", "not"):
        condition = node.get(keyword)
        if isinstance(condition, Mapping):
            result[keyword] = _schema_outline(schema, condition, active_refs=active_refs)
    return result


def _humanise_schema_outline(value: Any) -> Any:
    """Remove only fields that the runner derives mechanically from the contract shape."""

    if isinstance(value, list):
        return [_humanise_schema_outline(item) for item in value]
    if not isinstance(value, Mapping):
        return copy.deepcopy(value)
    result = {key: _humanise_schema_outline(child) for key, child in value.items()}
    properties = result.get("properties")
    if isinstance(properties, dict) and "local_ref" in properties:
        properties.pop("local_ref")
        if isinstance(result.get("required"), list):
            result["required"] = [name for name in result["required"] if name != "local_ref"]
        result["local_ref"] = "derived from collection order; do not enter"
    if isinstance(properties, dict) and {"exact_text", "occurrence_index"} <= set(properties):
        if isinstance(result.get("required"), list):
            result["required"] = [
                name for name in result["required"] if name != "occurrence_index"
            ]
        properties["occurrence_index"]["human_input_rule"] = (
            "May be omitted only when exact_text occurs once; required to disambiguate repetitions."
        )
    return result


def human_field_help(
    field: str, *, experiment_freeze: str | Path = DEFAULT_FREEZE,
) -> dict[str, Any]:
    """Return contract-derived field shape and enums without loading a case or provider."""

    freeze = validate_freeze(experiment_freeze)
    schema = _human_input_schema(freeze["contracts"]["xai_transport"])
    fields = _human_editable_fields(schema)
    if field == "all":
        return {
            "status": "contract_help", "provider_calls": 0,
            "human_selector_version": HUMAN_SELECTOR_VERSION,
            "contract_sha256": freeze["contracts"]["xai_transport"]["sha256"],
            "editable_fields": list(fields),
        }
    requested = field
    field = HUMAN_HELP_FIELD_ALIASES.get(field, field)
    if field not in fields:
        raise FourArmError(
            f"unknown or deterministic human field: {requested}; choose one of {', '.join(fields)}"
        )
    return {
        "status": "contract_help", "provider_calls": 0,
        "human_selector_version": HUMAN_SELECTOR_VERSION,
        "contract_sha256": freeze["contracts"]["xai_transport"]["sha256"],
        "requested_field": requested,
        "field": field,
        "shape": _humanise_schema_outline(
            _schema_outline(schema, schema["properties"][field])
        ),
        "mechanical_derivations": [
            "local_ref values are assigned from collection order",
            "occurrence_index is derived only for a uniquely occurring exact_text",
        ],
        "local_ref_formats": HUMAN_LOCAL_REF_FORMATS,
    }


def _inject_local_refs(value: dict[str, Any]) -> None:
    if "local_ref" in _walk_keys(value):
        raise FourArmError(
            "/human_semantic_fields: local_ref is deterministic; the human must not enter it"
        )
    for field, prefix, operation in HUMAN_LOCAL_REF_SPECS:
        rows = value.get(field)
        if not isinstance(rows, list):
            continue
        ordinal = 0
        for row in rows:
            if not isinstance(row, dict) or (operation is not None and row.get("operation") != operation):
                continue
            ordinal += 1
            row["local_ref"] = f"{prefix}-{ordinal}"
    alternative_lists = [
        issue.get("live_alternatives")
        for issue in value.get("new_issue_states", []) if isinstance(issue, dict)
    ]
    alternative_lists.extend(
        update.get("changes", {}).get("live_alternatives")
        for update in value.get("issue_state_updates", []) if isinstance(update, dict)
    )
    alternative_ordinal = 0
    for alternatives in alternative_lists:
        if not isinstance(alternatives, list):
            continue
        for alternative in alternatives:
            if isinstance(alternative, dict):
                alternative_ordinal += 1
                alternative["local_ref"] = f"new-alternative-{alternative_ordinal}"


def _derive_occurrence_indexes(value: Any, exact_text: str, pointer: str = "") -> None:
    if isinstance(value, list):
        for index, child in enumerate(value):
            _derive_occurrence_indexes(child, exact_text, f"{pointer}/{index}")
        return
    if not isinstance(value, dict):
        return
    if "exact_text" in value and "occurrence_index" not in value:
        needle = value["exact_text"]
        if isinstance(needle, str) and needle:
            starts, start = [], 0
            while True:
                start = exact_text.find(needle, start)
                if start < 0:
                    break
                starts.append(start)
                start += 1
            if len(starts) == 1:
                value["occurrence_index"] = 0
            elif len(starts) > 1:
                raise FourArmError(
                    f"{pointer or '/'}: repeated exact_text requires human occurrence_index"
                )
    for key, child in list(value.items()):
        _derive_occurrence_indexes(child, exact_text, f"{pointer}/{key}")


def _human_delta(
    pack: Mapping[str, Any], index: int, judgements: Any,
) -> dict[str, Any]:
    fields = pack.get("annotation_contract", {}).get("editable_semantic_fields")
    turns = pack.get("transcript", {}).get("turns")
    if not isinstance(fields, list) or not isinstance(turns, list) or index >= len(turns):
        raise FourArmError("human annotation pack cannot identify the next turn")
    if not isinstance(judgements, Mapping):
        raise FourArmError(
            f"/semantic_delta_chain/{index}: human annotation input must be one JSON object"
        )
    missing, extra = set(fields) - set(judgements), set(judgements) - set(fields)
    if missing or extra:
        detail = []
        if missing:
            detail.append(f"missing: {', '.join(sorted(missing))}")
        if extra:
            detail.append(f"not human-editable: {', '.join(sorted(extra))}")
        raise FourArmError(
            f"/semantic_delta_chain/{index}: human annotation fields differ ("
            + "; ".join(detail) + ")"
        )
    turn = turns[index]
    semantic_values = copy.deepcopy(dict(judgements))
    _inject_local_refs(semantic_values)
    _derive_occurrence_indexes(semantic_values, turn["text"])
    return {
        "schema_version": HUMAN_SELECTOR_VERSION,
        "canonical_schema_version": VERSIONS["canonical_semantic_delta"],
        "conversation_key": pack["transcript"]["conversation_key"],
        "target_turn_id": turn["turn_id"], "as_of_turn_index": index,
        "prior_ledger_reference": None if index == 0 else {
            "ledger_id": PRIOR_LEDGER_SENTINEL, "as_of_turn_index": index - 1,
        },
        **semantic_values,
    }


def _pre_reveal_gate(prepared: Mapping[str, Any]) -> None:
    if prepared["ledger"].get("provider_call_count") != 0 or any(
        row.get("state") != "planned" for row in prepared["ledger"].get("entries", [])
    ):
        raise FourArmError("human reference must be completed before any experiment machine output")
    if any(os.path.lexists(prepared["root"] / name) for name in (
        "requests", "responses", "validations", "products",
    )):
        raise FourArmError("human reference must be completed before any experiment machine output")


def annotate_human_reference(
    output: str | Path, *, turn_id: str | None = None,
    input_fn: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Interactively validate and append exactly one human-authored current-turn delta."""

    prompt = input if input_fn is None else input_fn
    prepared = _prepared(output)
    _pre_reveal_gate(prepared)
    human_root = prepared["root"] / PREPARED_HUMAN_DIR
    if any(os.path.lexists(human_root / name) for name in (
        "materialised-reference.json", "reference-lock.json", "LOCKED-SHA256SUMS",
    )):
        raise FourArmError("the human reference is already locked")
    submission = _human_submission(prepared["root"])
    reference, pack = submission["reference"], submission["pack"]
    chain = copy.deepcopy(reference["semantic_delta_chain"])
    turns = pack["transcript"]["turns"]
    index = len(chain)
    if index >= len(turns):
        raise FourArmError("the human reference chain is already complete")
    turn = turns[index]
    if turn_id is not None and turn_id != turn["turn_id"]:
        raise FourArmError("--turn-id must identify the next uncompleted turn")
    prior = submission["ledgers"][-1] if submission["ledgers"] else None
    display = {
        "current_turn_id": turn["turn_id"], "speaker": turn["speaker_id"],
        "exact_text": turn["text"],
        "prior_materialised_semantic_objects": None if prior is None else {
            field: copy.deepcopy(prior[field]) for field in PRIOR_SEMANTIC_OBJECT_FIELDS
        },
    }
    print(json.dumps(display, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
    try:
        source = prompt(
            "Enter every human semantic field as one JSON object "
            "(use --help-field separately for contract help): "
        )
    except EOFError as exc:
        raise FourArmError("human annotation input ended before a JSON object") from exc
    judgements = private_io.strict_json_loads(source.encode("utf-8"))
    delta = _human_delta(pack, index, judgements)
    proposed = [*chain, delta]
    _materialised_ledgers(
        pack, proposed, transport_binding=prepared["freeze"]["contracts"]["xai_transport"],
        require_complete=False,
    )
    print(json.dumps(delta, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
    try:
        approval = prompt("Type APPROVE to append this exact delta: ")
    except EOFError as exc:
        raise FourArmError("human annotation input ended before literal approval") from exc
    if approval != "APPROVE":
        raise FourArmError("human annotation was not literally approved; reference unchanged")
    completed = len(proposed) == len(turns)
    updated = {
        **reference, "semantic_delta_chain": proposed,
        "semantic_judgements_authored_by_human": True,
        "status": "completed_human_reference" if completed else "in_progress_human_reference",
        "completed_at_utc": _now() if completed else None,
    }
    private_io._write_private_json(human_root / HUMAN_REFERENCE_CHAIN_FILE, updated)
    return {
        "status": updated["status"], "provider_calls": 0,
        "approved_turn_id": turn["turn_id"], "approved_turn_index": index,
        "completed_turn_count": len(proposed), "total_turn_count": len(turns),
    }


def _locked_sums(human_root: Path) -> bytes:
    return "".join(
        f"{_sha(private_io._read_regular_bytes(path, 'human lock input'))}  "
        f"{path.relative_to(human_root).as_posix()}\n"
        for path in sorted(human_root.rglob("*"))
        if path.is_file() and path.name != "LOCKED-SHA256SUMS"
    ).encode("utf-8")


def lock_human_reference(output: str | Path) -> dict[str, Any]:
    """Validate, materialise and seal one human reference without provider access."""

    prepared = _prepared(output)
    _pre_reveal_gate(prepared)
    human_root = prepared["root"] / PREPARED_HUMAN_DIR
    generated = [
        human_root / "materialised-reference.json",
        human_root / "reference-lock.json",
        human_root / "LOCKED-SHA256SUMS",
    ]
    if any(os.path.lexists(path) for path in generated):
        raise FourArmError("human reference lock artifacts already exist")
    submission = _human_submission(prepared["root"])
    if submission["status"] != "completed_unlocked":
        raise FourArmError("Arm D remains at the genuine human single-reference gate")
    materialised = _materialised_reference_artifact(submission)
    materialised_raw = private_io.pretty_json_bytes(materialised)
    materialised_hash = _sha(materialised_raw)
    locked_at = _now()
    if submission["latest_completion"] > _utc(locked_at, "reference lock"):
        raise FourArmError("human reference completion is later than its lock")
    lock = {
        "artifact_version": "single-human-reference-lock-v2", "status": "locked",
        **ARM_D_GATE,
        "provenance_file_sha256": submission["provenance_sha256"],
        "reference_chain_sha256": submission["reference_sha256"],
        "reference_pack_sha256": submission["pack_sha256"],
        "materialised_reference_sha256": materialised_hash,
        "locked_at_utc": locked_at, "external_call_count_at_lock": 0,
    }
    private_io._write_private_bytes(generated[0], materialised_raw)
    private_io._write_private_json(generated[1], lock)
    seal_source = _locked_sums(human_root)
    private_io._write_private_bytes(generated[2], seal_source)
    verified = _human_gate(prepared["root"], require_locked=True)
    return {**verified, "provider_calls": 0, "materialised_reference_sha256": materialised_hash}


def _human_gate(output: Path, *, require_locked: bool) -> dict[str, Any]:
    human_root = output / PREPARED_HUMAN_DIR
    submission = _human_submission(output)
    generated = [
        human_root / "materialised-reference.json",
        human_root / "reference-lock.json",
        human_root / "LOCKED-SHA256SUMS",
    ]
    present = [os.path.lexists(path) for path in generated]
    if not any(present):
        if require_locked:
            raise FourArmError("Arm D remains at the genuine human single-reference gate")
        return {"status": submission["status"], "seal_sha256": None}
    if not all(present) or submission["status"] != "completed_unlocked":
        raise FourArmError("Arm D reference lock is partial or precedes completed human input")
    materialised_raw = private_io._read_regular_bytes(generated[0], "materialised human reference")
    expected_raw = private_io.pretty_json_bytes(_materialised_reference_artifact(submission))
    if materialised_raw != expected_raw:
        raise FourArmError("materialised human reference differs from deterministic output")
    lock = _json(generated[1], "human reference lock")
    if (
        lock.get("status") != "locked"
        or lock.get("artifact_version") != "single-human-reference-lock-v2"
        or any(lock.get(key) != value for key, value in ARM_D_GATE.items())
        or lock.get("provenance_file_sha256") != submission["provenance_sha256"]
        or lock.get("reference_chain_sha256") != submission["reference_sha256"]
        or lock.get("reference_pack_sha256") != submission["pack_sha256"]
        or lock.get("materialised_reference_sha256") != _sha(materialised_raw)
        or lock.get("external_call_count_at_lock") != 0
    ):
        raise FourArmError("single-human reference lock is incomplete or post-reveal")
    if submission["latest_completion"] > _utc(lock.get("locked_at_utc"), "reference lock"):
        raise FourArmError("human reference completion is later than its lock")
    seal_source = private_io._read_regular_bytes(generated[2], "human reference seal")
    declared = _checksum_lines(seal_source)
    actual = {
        path.relative_to(human_root).as_posix()
        for path in human_root.rglob("*")
        if path.is_file() and path.name != "LOCKED-SHA256SUMS"
    }
    if set(declared) != actual or any(
        _sha(private_io._read_regular_bytes(human_root / relative, relative)) != digest
        for relative, digest in declared.items()
    ):
        raise FourArmError("post-human immutable lock receipt differs")
    return {"status": "locked", "seal_sha256": _sha(seal_source)}


def _prepared(output: str | Path) -> dict[str, Any]:
    root = _private_directory(output, "private run")
    _audit_private(root)
    checked = _verify_immutable(root)
    binding = _json(root / "case-binding.json", "case binding")
    freeze_raw = private_io._read_regular_bytes(root / "experiment-freeze.json", "prepared freeze")
    if _sha(freeze_raw) != binding.get("experiment_freeze_sha256"):
        raise FourArmError("prepared freeze changed")
    freeze = validate_freeze(root / "experiment-freeze.json")
    case = _private_directory(binding.get("case_directory"), "bound case")
    case_hash, _ = _verify_case_sums(case)
    if case_hash != binding.get("case_sha256sums_sha256"):
        raise FourArmError("bound private case changed")
    plan, ledger = _json(root / "call-plan.json", "call plan"), _json(root / "call-ledger.json", "call ledger")
    _validate_ledger(plan, ledger)
    return {"root": root, "case": case, "freeze": freeze, "binding": binding, "plan": plan, "ledger": ledger, "checked": checked}


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=PROJECT_DIR, check=False, capture_output=True, text=True)
    if result.returncode:
        raise FourArmError(f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _live_git_gate(freeze: Mapping[str, Any]) -> None:
    if _git("status", "--porcelain") or _git("branch", "--show-current") != freeze["branch"]:
        raise FourArmError("live worktree is dirty or on the wrong branch")
    head, source = _git("rev-parse", "HEAD"), freeze["source_commit"]
    if _git("merge-base", head, source) != source or _git("rev-parse", f"origin/{freeze['branch']}") != head:
        raise FourArmError("live HEAD is not the pushed descendant of the frozen source")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@contextmanager
def _execution_lock(root: Path):
    lock = root / "execution.lock"
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "r+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FourArmError("another live execution holds the private run lock") from exc
        yield


def _transition(root: Path, call_id: str, state: str, **updates: Any) -> dict[str, Any]:
    ledger = _json(root / "call-ledger.json", "call ledger")
    row = next((item for item in ledger["entries"] if item["call_id"] == call_id), None)
    if row is None:
        raise FourArmError("call transition references an unknown call")
    allowed = {
        "planned": {"sending", "blocked_dependency", "reused_exact_request"},
        "sending": {"response_received", "failed_provider", "uncertain_after_send"},
        "response_received": {"completed", "failed_validation"},
    }
    if state not in allowed.get(str(row.get("state")), set()):
        raise FourArmError(f"illegal call transition: {row.get('state')}->{state}")
    row.update(copy.deepcopy(updates))
    row["state"] = state
    row["state_history"].append({
        "state": state, "at_utc": updates.get("started_at_utc")
        or updates.get("completed_at_utc") or _now(), "reason": updates.get("reason"),
    })
    ledger["provider_call_count"] = sum(
        int(item.get("provider_call_count", 0)) for item in ledger["entries"]
    )
    if ledger["provider_call_count"] > ledger["planned_provider_call_count"]:
        raise FourArmError("provider-call budget exceeded")
    private_io._write_private_json(root / "call-ledger.json", ledger)
    return row


def _human_ledgers(root: Path) -> dict[str, dict[str, Any]]:
    human_root = root / PREPARED_HUMAN_DIR
    artifact = _json(human_root / "materialised-reference.json", "materialised human reference")
    rows = artifact.get("snapshot_ledgers")
    if not isinstance(rows, list) or len(rows) != 2:
        raise FourArmError("materialised human reference does not cover two snapshots")
    return {str(row["snapshot_id"]): copy.deepcopy(row["ledger"]) for row in rows}


def _provider_safe(value: Any, key: str = "") -> None:
    if key in {"source_post_id", "author_id", "principal_author_id"}:
        raise FourArmError("provider request contains a raw source-identity field")
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            _provider_safe(child, str(child_key))
    elif isinstance(value, list):
        for child in value:
            _provider_safe(child, key)
    elif key.endswith("_id") and isinstance(value, str) and re.fullmatch(r"[0-9]{15,22}", value):
        raise FourArmError("provider request contains a raw source identity")


def _runtime(prepared: Mapping[str, Any]) -> dict[str, Any]:
    replay = _json(prepared["root"] / "replay-plan.json", "replay plan")
    transcript = _case_object(prepared["case"], "transcript.json")
    longest = max(replay["representation_snapshots"], key=lambda row: len(row["turn_ids"]))
    pack_transcript = {
        "conversation_key": transcript["conversation_key"],
        "turns": _pack_turns(longest, transcript),
    }
    contracts = prepared["freeze"]["contracts"]
    return {
        "replay": replay,
        "replays": {row["evaluation_alias"]: row for row in replay["runnable_replays"]},
        "snapshots": {row["snapshot_id"]: row for row in replay["representation_snapshots"]},
        "pack_transcript": pack_transcript,
        "turns": {row["turn_id"]: row for row in pack_transcript["turns"]},
        "transport_schema": _json(PROJECT_DIR / contracts["xai_transport"]["path"], "transport schema"),
        "canonical_schema": _json(PROJECT_DIR / contracts["canonical_semantic_delta"]["path"], "semantic schema"),
        "ledger_schema": _json(PROJECT_DIR / contracts["persisted_ledger"]["path"], "ledger schema"),
        "human": _human_ledgers(prepared["root"]),
    }


def _profile_request(
    entry: Mapping[str, Any], prepared: Mapping[str, Any], runtime: Mapping[str, Any],
    products: Mapping[str, Any], prior_c: Mapping[str, Any] | None,
) -> dict[str, Any]:
    profile = prepared["freeze"]["profiles"][entry["profile"]]
    operation, identity = entry["operation"], str(entry["identity"])
    if operation == "arm_b_ordinary_summary":
        snapshot_id = identity.removeprefix("summary-")
        supplied = {"transcript": runtime["snapshots"][snapshot_id]["payload"]}
    elif operation == "arm_c_incremental_delta":
        from tools import proposition_ledger_evidence_transport as evidence

        turn = runtime["turns"][identity.removeprefix("ledger-")]
        provider_schema, provider_schema_hash = _provider_schema(entry["profile"], profile)
        if provider_schema_hash != entry["provider_response_schema_sha256"] or provider_schema_hash != XAI_PROVIDER_SCHEMA_SHA256:
            raise FourArmError("Arm C provider schema differs before send")
        contract = evidence.build_response_contract_manifest(
            transport_schema=runtime["transport_schema"], xai_provider_schema=provider_schema
        )
        supplied = evidence.build_phase2b_user_payload(
            protocol_version=prepared["freeze"]["freeze_version"],
            protocol_hash=prepared["binding"]["experiment_freeze_sha256"],
            conversation_key=runtime["pack_transcript"]["conversation_key"],
            current_turn_id=turn["turn_id"], turn_index=turn["turn_index"],
            parent_turn_id=turn["parent_turn_id"], current_turn_text=turn["text"],
            speaker_descriptor=turn["participant"], prior_ledger=prior_c,
            response_contract_manifest=contract,
        )
    else:
        match = re.fullmatch(r"(evaluation-0[1-3])-arm-([A-D])", identity)
        if match is None:
            raise FourArmError("downstream call identity is invalid")
        alias, arm = match.groups()
        replay = runtime["replays"][alias]
        snapshot_id = replay["representation_snapshot_id"]
        dependency = entry["dependency_call_ids"][0] if entry["dependency_call_ids"] else None
        supplement = None
        if arm in {"B", "C"}:
            supplement = products[dependency]
        elif arm == "D":
            supplement = runtime["human"][snapshot_id]
        supplied = {
            "conversation": replay["canonical_input"],
            "supplementary_context": supplement,
        }
    request_profile = {
        key: copy.deepcopy(profile[key]) for key in (
            "provider", "model", "reasoning_effort", "max_output_tokens", "tools"
        )
    }
    for key in ("timeout_seconds", "temperature", "store", "stream", "retries"):
        if key in profile:
            request_profile[key] = profile[key]
    provider_schema, provider_schema_hash = _provider_schema(entry["profile"], profile)
    if provider_schema_hash != entry["provider_response_schema_sha256"]:
        raise FourArmError("provider response schema hash differs before send")
    request = {
        "request_version": "frozen-four-arm-provider-request-v1",
        "operation": operation, "profile": request_profile,
        "system_prompt": private_io._read_regular_bytes(
            PROJECT_DIR / profile["prompt_path"], "frozen prompt"
        ).decode("utf-8"),
        "user_payload": private_io.canonical_json_bytes(supplied).decode("utf-8"),
        "response_schema": provider_schema,
    }
    _provider_safe(request)
    return request


def _response_bytes(observation: Any) -> bytes:
    raw = getattr(observation, "raw_text", None)
    if raw is None:
        raw = observation
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, str):
        return raw.encode("utf-8")
    if isinstance(raw, Mapping):
        return private_io.canonical_json_bytes(raw)
    raise FourArmError("provider observation does not contain JSON text")


def _postvalidate(
    entry: Mapping[str, Any], raw: bytes, prepared: Mapping[str, Any],
    runtime: Mapping[str, Any], prior_c: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], Any]:
    from tools import proposition_ledger_evidence_transport as evidence
    from tools import proposition_ledger_semantic_delta as semantic
    from tools import build_proposition_ledger_phase1 as ledger_validation

    parsed = private_io.strict_json_loads(raw)
    profile = prepared["freeze"]["profiles"][entry["profile"]]
    schema = _json(PROJECT_DIR / profile["response_schema_path"], "response schema")
    errors = tuple(ledger_validation._jsonschema_errors(parsed, schema))
    if errors:
        raise FourArmError("frozen response schema failed: " + "; ".join(errors[:4]))
    validation: dict[str, Any] = {"status": "passed", "schema_errors": []}
    if entry["operation"] == "arm_b_ordinary_summary":
        return validation, {"summary": parsed["summary"]}
    if entry["operation"] == "downstream_arm_evaluation":
        status, reply = parsed["status"], parsed["reply"]
        if (status == "reply" and (not reply.strip() or len(reply) > 270)) or (
            status == "cannot_compose_safely" and reply != ""
        ):
            raise FourArmError("downstream-reply-contract-v1 failed")
        validation["postvalidator"] = "downstream-reply-contract-v1"
        return validation, dict(parsed)
    turn = runtime["turns"][str(entry["identity"]).removeprefix("ledger-")]
    resolved = evidence.resolve_transport_delta(
        parsed, current_turn_id=turn["turn_id"], current_turn_text=turn["text"],
        transport_schema=runtime["transport_schema"], canonical_schema=runtime["canonical_schema"],
    )
    if not resolved.succeeded or resolved.canonical_delta is None:
        raise FourArmError(f"evidence transport failed: {resolved.status}")
    genesis = None if prior_c is not None else {
        "conversation_key": runtime["pack_transcript"]["conversation_key"],
        "current_participant": turn["participant"], "root_post_id": turn["post_id"],
        "source_completeness": copy.deepcopy(prepared["freeze"]["source_completeness"]),
    }
    materialised = semantic.materialise_semantic_delta(
        prior_c, turn, resolved.canonical_delta,
        current_participant=turn["participant"], genesis_context=genesis,
        semantic_schema=runtime["canonical_schema"], ledger_schema=runtime["ledger_schema"],
    )
    if not materialised.succeeded or materialised.ledger is None:
        raise FourArmError(f"semantic materialisation failed: {materialised.status}")
    validation.update({
        "evidence_resolution_status": resolved.status,
        "ledger_sha256": materialised.ledger["ledger_sha256"],
    })
    return validation, materialised.ledger


def _adapter_sample(adapter: Any, request: Mapping[str, Any]) -> Any:
    if callable(adapter):
        return adapter(copy.deepcopy(dict(request)))
    sample = getattr(adapter, "sample", None)
    if callable(sample):
        return sample(copy.deepcopy(dict(request)))
    raise FourArmError("reviewed provider adapter has no sample boundary")


def _xai_child() -> None:
    """One pinned-SDK structured-output call, reached only by the gated adapter."""

    request = private_io.strict_json_loads(sys.stdin.buffer.read())
    if not isinstance(request, Mapping) or request.get("profile", {}).get("provider", "").lower() != "xai":
        raise FourArmError("xAI child request is invalid")
    private_io._harden_live_environment()
    from xai_sdk import Client
    from xai_sdk.chat import system, user
    from xai_sdk.proto import chat_pb2

    response_format = chat_pb2.ResponseFormat(
        format_type=chat_pb2.FORMAT_TYPE_JSON_SCHEMA,
        schema=private_io.canonical_json_bytes(request["response_schema"]).decode("utf-8"),
    )
    client = Client(
        api_key=os.environ["XAI_API_KEY"], timeout=private_io.CLIENT_TIMEOUT_SECONDS,
        channel_options=list(private_io.NO_RETRY_CHANNEL_OPTIONS),
    )
    try:
        chat = client.chat.create(
            model=request["profile"]["model"],
            messages=[system(request["system_prompt"]), user(request["user_payload"])],
            max_tokens=request["profile"]["max_output_tokens"],
            reasoning_effort=request["profile"]["reasoning_effort"], tools=[],
            parallel_tool_calls=False, response_format=response_format,
            search_parameters=None, store_messages=False,
        )
        response = chat.sample()
        if not isinstance(response.content, str):
            raise FourArmError("xAI response content is not text")
        sys.stdout.write(response.content)
    finally:
        client.close()


class ReviewedProviderAdapter:
    """Minimal frozen xAI/OpenAI boundary; construction follows every live gate."""

    def __init__(self) -> None:
        """Require both credentials and construct only the no-retry OpenAI client."""

        if not os.environ.get("XAI_API_KEY") or not os.environ.get("OPENAI_API_KEY"):
            raise FourArmError("both frozen-provider credentials are required")
        if not private_io.PINNED_PYTHON.is_file():
            raise FourArmError("established pinned xAI interpreter is absent")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise FourArmError("installed OpenAI SDK is unavailable") from exc
        self._openai = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"], timeout=180, max_retries=0
        )

    def sample(self, request: Mapping[str, Any]) -> str:
        """Make exactly one structured-output call using the frozen provider profile."""

        provider = str(request["profile"]["provider"]).lower()
        if provider == "xai":
            code = "from tools.proposition_ledger_four_arm_experiment import _xai_child; _xai_child()"
            result = subprocess.run(
                [str(private_io.PINNED_PYTHON), "-c", code], cwd=PROJECT_DIR,
                input=private_io.canonical_json_bytes(request), capture_output=True, check=False,
            )
            if result.returncode or not result.stdout:
                raise FourArmError(f"pinned xAI adapter failed with exit {result.returncode}")
            return result.stdout.decode("utf-8", errors="strict")
        if provider != "openai":
            raise FourArmError("frozen provider is unsupported")
        profile = request["profile"]
        response = self._openai.responses.create(
            model=profile["model"], instructions=request["system_prompt"],
            input=request["user_payload"], reasoning={"effort": profile["reasoning_effort"]},
            max_output_tokens=profile["max_output_tokens"], tools=[], tool_choice="none",
            parallel_tool_calls=False, temperature=profile["temperature"],
            store=profile["store"], stream=profile["stream"],
            text={"format": {
                "type": "json_schema", "name": "four_arm_downstream_reply",
                "strict": True, "schema": request["response_schema"],
            }},
        )
        if not isinstance(response.output_text, str):
            raise FourArmError("OpenAI response content is not text")
        return response.output_text

    def close(self) -> None:
        """Close the retained OpenAI client after the bounded plan finishes."""

        self._openai.close()


def _execute_plan(prepared: Mapping[str, Any], adapter: Any) -> dict[str, Any]:
    root, runtime = prepared["root"], _runtime(prepared)
    for name in ("requests", "responses", "validations", "products"):
        private_io._ensure_private_directory(root / name)
    products: dict[str, Any] = {}
    request_owners: dict[str, str] = {}
    prior_c: dict[str, Any] | None = None
    successful = {"completed", "reused_exact_request"}
    for frozen in prepared["plan"]["entries"]:
        call_id = str(frozen["call_id"])
        current = _json(root / "call-ledger.json", "call ledger")
        state_by_id = {row["call_id"]: row["state"] for row in current["entries"]}
        dependencies = [
            item for item in frozen["dependency_call_ids"]
            if not item.startswith("human-reference:")
        ]
        if any(state_by_id.get(item) not in successful for item in dependencies):
            _transition(root, call_id, "blocked_dependency", provider_call_count=0,
                        attempt_number=0, completed_at_utc=_now(), reason="dependency_not_validated")
            continue
        request = _profile_request(frozen, prepared, runtime, products, prior_c)
        request_raw, request_hash = private_io.canonical_json_bytes(request), _sha(private_io.canonical_json_bytes(request))
        request_path = root / "requests" / f"{call_id}.json"
        if request_path.exists():
            raise FourArmError("provider request artifact already exists")
        private_io._write_private_bytes(request_path, request_raw)
        if request_hash in request_owners:
            owner = request_owners[request_hash]
            products[call_id] = products[owner]
            _transition(
                root, call_id, "reused_exact_request", request_sha256=request_hash,
                request_path=request_path.relative_to(root).as_posix(), reused_from_call_id=owner,
                provider_call_count=0, attempt_number=0, completed_at_utc=_now(), reason="byte_identical_request",
            )
            if frozen["operation"] == "arm_c_incremental_delta":
                prior_c = products[owner]
            continue
        started = _now()
        _transition(
            root, call_id, "sending", request_sha256=request_hash,
            request_path=request_path.relative_to(root).as_posix(), provider_call_count=1,
            attempt_number=1, started_at_utc=started, reason="durable_before_provider_invocation",
        )
        try:
            observation = _adapter_sample(adapter, request)
        except Exception as exc:
            _transition(root, call_id, "failed_provider", completed_at_utc=_now(),
                        failure_class=type(exc).__name__, reason="provider_exception_no_retry")
            continue
        raw = _response_bytes(observation)
        try:
            response_characters: int | None = len(raw.decode("utf-8", errors="strict"))
        except UnicodeDecodeError:
            response_characters = None
        response_path = root / "responses" / f"{call_id}.raw"
        private_io._write_private_bytes(response_path, raw)
        _transition(
            root, call_id, "response_received", response_sha256=_sha(raw),
            response_path=response_path.relative_to(root).as_posix(), reason="response_durably_recorded",
            response_utf8_bytes=len(raw), response_characters=response_characters,
        )
        try:
            validation, product = _postvalidate(frozen, raw, prepared, runtime, prior_c)
        except Exception as exc:
            validation = {"status": "failed", "error_class": type(exc).__name__, "error": str(exc)}
            validation_path = root / "validations" / f"{call_id}.json"
            private_io._write_private_json(validation_path, validation)
            _transition(
                root, call_id, "failed_validation", validation_path=validation_path.relative_to(root).as_posix(),
                validation_sha256=_sha(private_io._read_regular_bytes(validation_path, "validation")),
                completed_at_utc=_now(), reason="postvalidation_failed_no_retry",
            )
            continue
        validation_path, product_path = (
            root / "validations" / f"{call_id}.json", root / "products" / f"{call_id}.json"
        )
        private_io._write_private_json(validation_path, validation)
        private_io._write_private_json(product_path, product)
        products[call_id], request_owners[request_hash] = product, call_id
        if frozen["operation"] == "arm_c_incremental_delta":
            prior_c = product
        _transition(
            root, call_id, "completed", validation_path=validation_path.relative_to(root).as_posix(),
            validation_sha256=_sha(private_io._read_regular_bytes(validation_path, "validation")),
            product_path=product_path.relative_to(root).as_posix(),
            product_sha256=_sha(private_io._read_regular_bytes(product_path, "product")),
            completed_at_utc=_now(), reason="strict_postvalidation_passed",
        )
    ledger = _json(root / "call-ledger.json", "call ledger")
    states = {state: sum(row["state"] == state for row in ledger["entries"]) for state in {
        row["state"] for row in ledger["entries"]
    }}
    result = {
        "status": "completed" if states.get("completed", 0) + states.get("reused_exact_request", 0) == len(ledger["entries"]) else "completed_with_fail_closed_outcomes",
        "provider_calls": ledger["provider_call_count"], "planned_provider_calls": ledger["planned_provider_call_count"],
        "unique_arm_evaluations": 12, "states": states,
        "downstream_completed": sum(
            row["operation"] == "downstream_arm_evaluation" and row["state"] in successful
            for row in ledger["entries"]
        ),
    }
    private_io._write_private_json(root / "run-result.json", result)
    return result


def run_experiment(
    output: str | Path, *, confirm_arm_evaluations: int | None,
    confirm_provider_call_budget: int | None, provider_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Run the exact at-most-once plan through an injected reviewed adapter."""

    root = _private_directory(output, "private run")
    with _execution_lock(root):
        prepared = _prepared(root)
        plan, ledger = prepared["plan"], prepared["ledger"]
        if confirm_arm_evaluations != 12 or confirm_provider_call_budget != plan["planned_provider_call_count"]:
            raise FourArmError("live arm/provider budget acknowledgements are not exact")
        if ledger["provider_call_count"] or {row["state"] for row in ledger["entries"]} != {"planned"}:
            raise FourArmError("provider plan is not pristine; no call may be repeated")
        _human_gate(root, require_locked=True)
        _live_git_gate(prepared["freeze"])
        policy = prepared["freeze"]["provider_policy"]
        if (
            policy["live_execution_enabled"] is not True
            or policy["live_adapter_status"] != "implemented_offline_reviewed_not_live_verified"
            or policy["live_verification_performed"] is not False
        ):
            raise FourArmError("frozen provider policy keeps the reviewed live adapter gate closed")
        adapter = (provider_factory or ReviewedProviderAdapter)()
        try:
            return _execute_plan(prepared, adapter)
        finally:
            close = getattr(adapter, "close", None)
            if callable(close):
                close()


def _verify_dynamic_artifacts(prepared: Mapping[str, Any]) -> int:
    root, checked = prepared["root"], 0
    rows = {row["call_id"]: row for row in prepared["ledger"]["entries"]}
    for call_id, row in rows.items():
        if row["state"] == "planned" or row["state"] == "blocked_dependency":
            continue
        request = root / "requests" / f"{call_id}.json"
        if row.get("request_path") != request.relative_to(root).as_posix() or row.get("request_sha256") != _sha(
            private_io._read_regular_bytes(request, "provider request")
        ):
            raise FourArmError("provider request artifact binding differs")
        checked += 1
        if row["state"] == "reused_exact_request":
            owner = rows.get(row.get("reused_from_call_id"))
            if owner is None or owner["state"] != "completed" or owner.get("request_sha256") != row["request_sha256"]:
                raise FourArmError("exact-request reuse binding differs")
            continue
        if row["state"] == "failed_provider" or row["state"] == "sending":
            continue
        response = root / "responses" / f"{call_id}.raw"
        response_raw = private_io._read_regular_bytes(response, "provider response")
        try:
            characters: int | None = len(response_raw.decode("utf-8", errors="strict"))
        except UnicodeDecodeError:
            characters = None
        if (
            row.get("response_path") != response.relative_to(root).as_posix()
            or row.get("response_sha256") != _sha(response_raw)
            or row.get("response_utf8_bytes") != len(response_raw)
            or row.get("response_characters") != characters
        ):
            raise FourArmError("provider response artifact binding differs")
        checked += 1
        if row["state"] == "response_received":
            continue
        validation = root / "validations" / f"{call_id}.json"
        if row.get("validation_path") != validation.relative_to(root).as_posix() or row.get("validation_sha256") != _sha(
            private_io._read_regular_bytes(validation, "response validation")
        ):
            raise FourArmError("response validation artifact binding differs")
        checked += 1
        if row["state"] == "completed":
            product = root / "products" / f"{call_id}.json"
            if row.get("product_path") != product.relative_to(root).as_posix() or row.get("product_sha256") != _sha(
                private_io._read_regular_bytes(product, "validated product")
            ):
                raise FourArmError("validated product artifact binding differs")
            checked += 1
    return checked


def verify_run(output: str | Path) -> dict[str, Any]:
    """Regenerate and verify prepared artifacts without provider capability."""

    prepared = _prepared(output)
    validation = validate_case(prepared["case"], experiment_freeze=prepared["root"] / "experiment-freeze.json")
    expected = _call_plan(validation["replay_plan"], prepared["freeze"], prepared["binding"]["experiment_freeze_sha256"])
    if private_io.pretty_json_bytes(expected) != private_io._read_regular_bytes(prepared["root"] / "call-plan.json", "call plan"):
        raise FourArmError("call plan does not regenerate byte-identically")
    recorded = int(prepared["ledger"]["provider_call_count"])
    pristine = recorded == 0 and all(
        row["state"] == "planned" for row in prepared["ledger"]["entries"]
    )
    return {
        "status": "passed_prepared" if pristine else "passed",
        "provider_calls": recorded, "verification_provider_calls": 0,
        "recorded_provider_calls": recorded, "call_ledger_unchanged": pristine,
        "accepted_evaluation_aliases": list(ALIASES),
        "unique_arm_evaluations": 12, "planned_provider_calls": prepared["plan"]["planned_provider_call_count"],
        "arm_d_status": _human_gate(prepared["root"], require_locked=False)["status"],
        "immutable_files_checked": prepared["checked"],
        "dynamic_artifacts_checked": _verify_dynamic_artifacts(prepared),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Frozen supplemental proposition-ledger four-arm case")
    modes = parser.add_mutually_exclusive_group(required=True)
    for flag in ("validate-case", "prepare-run", "run", "verify"):
        modes.add_argument(f"--{flag}", action="store_true")
    modes.add_argument(
        "--annotate-human-reference", action="store_true",
        help="offline: show and append exactly the next human-annotated turn",
    )
    modes.add_argument(
        "--help-field", metavar="FIELD",
        help=("offline contract help; e.g. new_proposition, issue_state_update, "
              "answer_target, or all"),
    )
    parser.add_argument("--case", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--experiment-freeze", type=Path, default=DEFAULT_FREEZE)
    parser.add_argument("--confirm-arm-evaluations", type=int)
    parser.add_argument("--confirm-provider-call-budget", type=int)
    parser.add_argument("--turn-id", help="optional next-turn guard for human annotation")
    parser.add_argument(
        "--lock-human-reference", action="store_true",
        help="with --verify, validate, materialise and pre-reveal lock the human reference",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch exactly one validate, prepare, gated-run or verify operation."""

    args = _parser().parse_args(argv)
    try:
        if args.help_field is not None:
            if (
                args.case is not None or args.output is not None or args.lock_human_reference
                or args.turn_id is not None or args.confirm_arm_evaluations is not None
                or args.confirm_provider_call_budget is not None
            ):
                raise FourArmError("--help-field accepts only --experiment-freeze")
            result = human_field_help(
                args.help_field, experiment_freeze=args.experiment_freeze,
            )
        elif args.annotate_human_reference:
            if (
                args.case is not None or args.output is None or args.lock_human_reference
                or args.confirm_arm_evaluations is not None
                or args.confirm_provider_call_budget is not None
            ):
                raise FourArmError(
                    "--annotate-human-reference requires --output and optional --turn-id"
                )
            result = annotate_human_reference(args.output, turn_id=args.turn_id)
        elif args.validate_case:
            if (
                args.case is None or args.output is not None or args.lock_human_reference
                or args.turn_id is not None
            ):
                raise FourArmError("--validate-case requires only --case")
            result = validate_case(args.case, experiment_freeze=args.experiment_freeze)
            result.pop("replay_plan")
        elif args.prepare_run:
            if (
                args.case is None or args.output is None or args.lock_human_reference
                or args.turn_id is not None
            ):
                raise FourArmError("--prepare-run requires --case and --output")
            result = prepare_run(args.case, args.output, experiment_freeze=args.experiment_freeze)
        elif args.run:
            if (
                args.case is not None or args.output is None or args.lock_human_reference
                or args.turn_id is not None
            ):
                raise FourArmError("--run requires only --output")
            result = run_experiment(
                args.output, confirm_arm_evaluations=args.confirm_arm_evaluations,
                confirm_provider_call_budget=args.confirm_provider_call_budget,
            )
        else:
            if (
                args.case is not None or args.output is None
                or args.confirm_arm_evaluations is not None
                or args.confirm_provider_call_budget is not None
                or args.turn_id is not None
            ):
                raise FourArmError("--verify accepts only --output")
            result = (
                lock_human_reference(args.output)
                if args.lock_human_reference
                else verify_run(args.output)
            )
    except (FourArmError, private_io.ProbeError) as exc:
        print(f"four_arm_experiment_error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
