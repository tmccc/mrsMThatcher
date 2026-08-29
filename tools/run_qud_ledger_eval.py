#!/usr/bin/env python3
"""Run a private, text-only QUD-ledger shadow evaluation.

The runner imports only provider-independent reply code and existing research
harness helpers.  It has no X client or posting path.  Preparation is offline;
provider traffic requires the explicit ``--execute-live-models`` flag.
"""

from __future__ import annotations

import argparse
import copy
import csv
import io
import json
import os
import re
import secrets
import statistics
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from reply_evidence import EvidenceRepository
from reply_strategy import deterministic_reply_error, validate_reply_context
import tested_reply_pipeline as tested_pipeline
from tools import extract_prospective_conversations as prospective_extractor
from tools import run_grok_46_tested_pipeline_eval as base_harness
from tools import run_writer_model_eval as writer_harness


RUN_VERSION = "qud-ledger-shadow-eval-v1"
SCHEMA_VERSION = 1
MAX_CANDIDATE_CASES = 80
MAX_COMPARISON_CASES = 60
MAX_PATH_TURNS = 20
MAX_REPLY_LENGTH = 270
GLOBAL_COST_CEILING_USD = 20.0

PRODUCTION_CHECKOUT = Path("/disks/disk1/etc/mrsMThatcher")
PROSPECTIVE_ROOT = Path(
    "/disks/disk1/research/mrsMThatcher-prospective-conversations-v4"
)
DEFAULT_OUTPUT_ROOT = Path(
    "/disks/disk1/research/qud-ledger-shadow-eval-20260829"
)
DEFAULT_ENV_FILE = PRODUCTION_CHECKOUT / "mrsMThatcher.env"
PRIOR_CASE_FILES = (
    Path("/disks/disk1/research/grok-46-tested-pipeline-eval-20260828/cases.jsonl"),
    Path("/disks/disk1/research/writer-model-eval-20260828/writer_cases.jsonl"),
)

SOURCE_MASTER_SHA = "1351760ef50a17149f94c7001687ec4b114c6234"
RESEARCH_SOURCE_COMMITS = (
    (
        "c79489d367f1d69d50ec58d6f07d53d536da8102",
        "Add Grok 4.6 tested-pipeline evaluation harness",
    ),
    (
        "5d3ba9e2b0d11fc457e162a094da79ad81b2539d",
        "Add tested-pipeline writer model evaluation",
    ),
)

XAI_HOST = base_harness.XAI_HOST
OPENAI_HOST = base_harness.OPENAI_HOST
XAI_BASE_URL = base_harness.XAI_BASE_URL
OPENAI_BASE_URL = base_harness.OPENAI_BASE_URL
NETWORK_ALLOWLIST = frozenset({XAI_HOST, OPENAI_HOST})

PRODUCTION_XAI_MODEL = "grok-4.3"
PRODUCTION_XAI_EFFORT = "low"
PRODUCTION_OPENAI_MODEL = "gpt-5.6-sol"
PRODUCTION_OPENAI_EFFORT = "medium"
ISSUE_MODEL = "grok-4.6"
ISSUE_EFFORT = "low"
CRITIC_MODEL = "grok-4.6"
CRITIC_EFFORT = "low"
REPAIR_MODEL = "gpt-5.6-sol"
REPAIR_EFFORT = "medium"

ARMS: dict[str, dict[str, str]] = {
    "A": {
        "name": "exact_current_baseline",
        "writer_issue_state": "none",
        "post_draft_critic": "none",
    },
    "B": {
        "name": "transcript_only_post_draft_critic",
        "writer_issue_state": "none",
        "post_draft_critic": "transcript_only",
    },
    "C": {
        "name": "issue_state_supplied_to_writer",
        "writer_issue_state": "usable_only",
        "post_draft_critic": "none",
    },
    "D": {
        "name": "issue_state_writer_plus_ledger_gate",
        "writer_issue_state": "usable_only",
        "post_draft_critic": "ledger_backed",
    },
}
BLIND_LABELS = ("W", "X", "Y", "Z")

WRITER_STAGES = frozenset(
    {
        "writer_v3_initial",
        "bounded_claim_cleanup",
        "exact_duplicate_repair",
        "direct_answer_repair",
    }
)
ROUTING_AND_REVIEW_PREFIXES = (
    "candidate_backed_engagement",
    "reply_necessity_",
    "focused_group_review",
    "allegation_review_",
    "authentication_review_",
)
PROTECTED_PRODUCTION_PATHS = frozenset(
    {
        "tested_reply_pipeline.py",
        "reply_strategy.py",
        "reply_evidence.py",
        "mrsMThatcher2.py",
        "tools/extract_prospective_conversations.py",
        "mrsMThatcher.local.json",
        "bot_state.json",
        "mrsMThatcher.control.json",
    }
)

PRIORITY_REASONS = (
    "requested_liberty_conversation_match",
    "explicit_correction_cue",
    "post_clarification_continuation",
    "same_author_path_continuation",
    "multiple_account_replies_on_path",
    "third_or_later_substantive_path_turn",
    "branch_contamination_control",
)

ISSUE_STATE_PROMPT = """You extract one compact Questions-Under-Discussion issue hypothesis from an exact principal-author branch.
Raw turn text is authoritative. The returned structure is only a fallible interpretive hypothesis linked to raw turns; it is not factual or semantic ground truth.
Do not decide whether any political proposition is true. Do not infer a contributor's private beliefs. Do not treat silence as concession. Do not import a sibling contributor's issue.
Distinguish the question under discussion from the contributor's stance. Distinguish a user replacing their own issue from a user rejecting the account's attempted answer.
The wording “I am not asking about X” rejects X as the answer target and is not withdrawal of a proposition owned by the user.
Extract at most one live issue. Keep only up to three answer targets that an exact contributor turn explicitly rejected. Use no_stable_issue or unclear rather than forcing an interpretation.
Use only supplied exact turn IDs. Keep every string short. Do not produce symbolic or modal-logic formulae. Do not add rationale. Return only the required JSON."""

ANSWERHOOD_CRITIC_PROMPT = """Judge whether a proposed public reply answers the live issue in the supplied exact principal-author transcript.
Judge answerhood, not whether the political view is correct. Do not judge whether a reply was necessary. Do not judge tone, humour, or factual grounding.
A reply can contradict the contributor and still directly answer the live issue. A reply can be compatible and relevant yet still substitute a nearby issue.
An additional point is elaboration_after_answer only when the live issue was answered first. Asking for clarification is not automatically aligned when the issue is already clear.
An explicit user repair must be grounded in an exact user turn. Use only supplied turn IDs as evidence. Use unclear rather than inventing a precise relation.
Keep answer and proposition strings short. Do not add rationale. Return only the required JSON."""

ISSUE_STATE_WRITER_INSTRUCTION = """The supplied issue_state is a fallible aid. Raw context remains authoritative. Use the issue state only to keep the reply on the exact live issue and avoid reviving an answer target explicitly rejected by the contributor. If it conflicts with the raw context or is unclear, ignore it. Answer the live issue before adding a related elaboration. Never mention the issue state."""

ALIGNMENT_REPAIR_PROMPT = """Perform one bounded alignment repair of the proposed public reply.
Answer the live issue in the first sentence. Disagreement is allowed. Do not answer a merely nearby proposition instead. Do not revive an answer target explicitly rejected by the contributor. Related elaboration is allowed only after the live issue is answered.
Use only trusted_facts for checkable factual claims. Introduce no new date, attribution, quotation, motive, prevalence, or historical claim.
Preserve natural British English and the account's existing voice. Return one or two short sentences, no more than 270 characters, with no emoji. Do not mention the critic, issue state, repair, or experiment.
Use cannot_compose_safely when a grounded repair is not possible. Return only the required JSON."""


class EvaluationError(RuntimeError):
    """The isolated evaluation cannot continue safely."""


CostLimitReached = base_harness.CostLimitReached
AmbiguousRequestError = base_harness.AmbiguousRequestError
ProviderError = base_harness.ProviderError
DefiniteProviderError = base_harness.DefiniteProviderError
TransientProviderError = base_harness.TransientProviderError
ModelAvailabilityError = base_harness.ModelAvailabilityError

atomic_bytes = base_harness.atomic_bytes
atomic_json = base_harness.atomic_json
atomic_jsonl = base_harness.atomic_jsonl
atomic_text = base_harness.atomic_text
canonical_json_bytes = base_harness.canonical_json_bytes
read_json = base_harness.read_json
read_jsonl = base_harness.read_jsonl
sha256_bytes = base_harness.sha256_bytes
sha256_file = base_harness.sha256_file
sha256_value = base_harness.sha256_value
strict_json_loads = base_harness.strict_json_loads
utc_now = base_harness.utc_now


def _strict(properties: Mapping[str, Any]) -> dict[str, Any]:
    """Return a strict JSON object schema requiring every property."""
    return {
        "type": "object",
        "properties": copy.deepcopy(dict(properties)),
        "required": list(properties),
        "additionalProperties": False,
    }


def _bounded_string(maximum: int, *, allow_empty: bool = False) -> dict[str, Any]:
    """Return a bounded single-line-ish string schema."""
    return {
        "type": "string",
        "minLength": 0 if allow_empty else 1,
        "maxLength": maximum,
        "pattern": r"^[^\u0000-\u001f\u007f-\u009f]*$",
    }


def _turn_ids(*, maximum: int = 6) -> dict[str, Any]:
    """Return the bounded exact-turn-ID array schema."""
    return {
        "type": "array",
        "items": _bounded_string(128),
        "maxItems": maximum,
        "uniqueItems": True,
    }


USER_STANCE_SCHEMA = _strict(
    {
        "status": {
            "type": "string",
            "enum": ["asserted", "challenged", "questioned", "unclear"],
        },
        "canonical_text": _bounded_string(500),
        "source_turn_ids": _turn_ids(),
    }
)

SIGNATURE_SCHEMA = _strict(
    {
        "subject": _bounded_string(240),
        "relation": {
            "type": "string",
            "enum": [
                "requires",
                "sufficient_for",
                "causes",
                "enables",
                "prevents",
                "compares_with",
                "defines",
                "attributes",
                "other",
                "unclear",
            ],
        },
        "object": _bounded_string(240),
        "polarity": {
            "type": "string",
            "enum": ["affirmed", "denied", "questioned", "unclear"],
        },
        "modality": {
            "type": "string",
            "enum": [
                "necessity",
                "sufficiency",
                "possibility",
                "actuality",
                "normative",
                "none",
                "unclear",
            ],
        },
        "scope": {
            "type": "string",
            "enum": ["universal", "qualified", "particular", "unclear"],
        },
        "kind": {
            "type": "string",
            "enum": [
                "factual",
                "normative",
                "conceptual",
                "counterfactual",
                "interpretive",
                "mixed",
                "unclear",
            ],
        },
    }
)

LIVE_ISSUE_SCHEMA = _strict(
    {
        "issue_id": {"type": "string", "const": "I1"},
        "question_under_discussion": _bounded_string(500),
        "raised_by_turn_ids": _turn_ids(),
        "user_stance": USER_STANCE_SCHEMA,
        "signature": SIGNATURE_SCHEMA,
        "status": {
            "type": "string",
            "enum": ["open", "answered", "replaced", "unclear"],
        },
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
        },
    }
)

REJECTED_TARGET_SCHEMA = _strict(
    {
        "account_turn_ids": _turn_ids(),
        "answered_question": _bounded_string(500),
        "rejected_by_turn_ids": _turn_ids(),
        "repair_type": {
            "type": "string",
            "enum": [
                "not_the_question",
                "wrong_actor",
                "wrong_relation",
                "wrong_scope",
                "wrong_polarity",
                "wrong_time_or_counterfactual",
                "other",
            ],
        },
        "status": {"type": "string", "const": "deprecated_as_answer_target"},
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
        },
    }
)

ISSUE_STATE_SCHEMA = _strict(
    {
        "status": {
            "type": "string",
            "enum": ["issue_found", "no_stable_issue", "unclear"],
        },
        "live_issue": {"anyOf": [LIVE_ISSUE_SCHEMA, {"type": "null"}]},
        "rejected_answer_targets": {
            "type": "array",
            "items": REJECTED_TARGET_SCHEMA,
            "maxItems": 3,
        },
        "source_turn_ids": _turn_ids(),
    }
)

ANSWERHOOD_SCHEMA = _strict(
    {
        "candidate_answered_question": _bounded_string(500, allow_empty=True),
        "candidate_proposition": _bounded_string(500, allow_empty=True),
        "addresses_live_issue": {
            "type": "string",
            "enum": [
                "direct",
                "partial",
                "elaboration_after_answer",
                "substitute",
                "unrelated",
                "unclear",
            ],
        },
        "logical_relation_to_user_stance": {
            "type": "string",
            "enum": [
                "entails",
                "strengthens",
                "weakens",
                "contradicts",
                "compatible",
                "unknown",
            ],
        },
        "explicit_repair_present": {"type": "boolean"},
        "revives_rejected_answer_target": {"type": "boolean"},
        "alignment_differences": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "subject_changed",
                    "relation_changed",
                    "object_changed",
                    "actor_changed",
                    "polarity_changed",
                    "modality_changed",
                    "scope_changed",
                    "time_or_counterfactual_changed",
                    "live_issue_not_answered",
                    "none",
                    "unclear",
                ],
            },
            "maxItems": 10,
            "uniqueItems": True,
        },
        "evidence_turn_ids": _turn_ids(),
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
        },
    }
)


def git_revision(project_dir: Path, revision: str) -> str:
    """Resolve one Git revision without modifying repository state."""
    return base_harness.git_revision(project_dir, revision)


def validate_provider_url(url: str, *, expected_host: str | None = None) -> str:
    """Allow only exact synchronous model and model-list provider endpoints."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in NETWORK_ALLOWLIST
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.query
        or parsed.fragment
    ):
        raise EvaluationError("network destination is outside the provider allowlist")
    if expected_host is not None and parsed.hostname != expected_host:
        raise EvaluationError(f"provider request must use {expected_host}")
    permitted = {
        XAI_HOST: {"/v1/models", "/v1/chat/completions"},
        OPENAI_HOST: {
            "/v1/models",
            f"/v1/models/{PRODUCTION_OPENAI_MODEL}",
            "/v1/chat/completions",
        },
    }
    if parsed.path not in permitted[parsed.hostname]:
        raise EvaluationError("provider URL path is not permitted")
    return url


def ensure_private_output_dir(path: Path, *, project_dir: Path) -> Path:
    """Create only the fixed private experiment tree, never a runtime path."""
    resolved = path.expanduser().resolve()
    project = project_dir.expanduser().resolve()
    production = PRODUCTION_CHECKOUT.resolve()
    allowed = DEFAULT_OUTPUT_ROOT.resolve()
    if resolved == production or production in resolved.parents:
        raise EvaluationError("experiment output must never be inside production")
    if resolved == project or project in resolved.parents:
        raise EvaluationError("experiment output must never be inside the worktree")
    if resolved != allowed and allowed not in resolved.parents:
        raise EvaluationError(f"experiment output must be beneath {allowed}")
    os.makedirs(resolved, mode=0o700, exist_ok=True)
    if resolved.is_symlink() or not resolved.is_dir():
        raise EvaluationError("private output path must be a real directory")
    os.chmod(resolved, 0o700)
    return resolved


def private_path(root: Path, relative: str) -> Path:
    """Resolve a private output member without permitting tree escape."""
    return base_harness.private_path(root, relative)


def assert_production_sources_clean(project_dir: Path) -> None:
    """Reject any tracked or untracked change to protected production paths."""
    process = subprocess.run(
        ["git", "status", "--porcelain=v1", "--", *sorted(PROTECTED_PRODUCTION_PATHS)],
        cwd=project_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    if process.stdout.strip():
        raise EvaluationError("a protected production path has been changed")
    systemd = subprocess.run(
        ["git", "status", "--porcelain=v1", "--", "*.service", "*.timer"],
        cwd=project_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    if systemd.stdout.strip():
        raise EvaluationError("a systemd unit has been changed")


def production_config() -> dict[str, Any]:
    """Return and verify the exact enabled frozen production configuration."""
    config = base_harness.production_config()
    expected = {
        "xai_model": PRODUCTION_XAI_MODEL,
        "xai_reasoning_effort": PRODUCTION_XAI_EFFORT,
        "openai_model": PRODUCTION_OPENAI_MODEL,
        "openai_reasoning_effort": PRODUCTION_OPENAI_EFFORT,
    }
    differences = {
        key: {"expected": value, "observed": config.get(key)}
        for key, value in expected.items()
        if config.get(key) != value
    }
    if differences:
        raise EvaluationError(
            "frozen production architecture changed: "
            + json.dumps(differences, sort_keys=True)
        )
    errors = tested_pipeline.validate_strategy_config(config)
    if errors:
        raise EvaluationError("production configuration is invalid: " + "; ".join(errors))
    return config


def _parse_object(value: object, *, label: str) -> dict[str, Any]:
    """Parse one strict structured-output object."""
    parsed = strict_json_loads(value) if isinstance(value, (str, bytes)) else value
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be a JSON object")
    return copy.deepcopy(parsed)


def _exact_keys(value: Mapping[str, Any], expected: Iterable[str], *, label: str) -> None:
    """Require an exact object-key set."""
    expected_set = set(expected)
    if set(value) != expected_set:
        raise ValueError(
            f"{label} fields mismatch missing={sorted(expected_set - set(value))} "
            f"extra={sorted(set(value) - expected_set)}"
        )


def _validate_string(value: object, *, label: str, maximum: int, allow_empty: bool = False) -> str:
    """Validate one bounded model-returned string."""
    if type(value) is not str or len(value) > maximum or (not allow_empty and not value):
        raise ValueError(f"{label} is invalid")
    if any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value):
        raise ValueError(f"{label} contains control characters")
    return value


def _validate_ids(
    value: object,
    *,
    label: str,
    valid_turn_ids: set[str],
    maximum: int = 6,
) -> list[str]:
    """Validate one bounded unique list of supplied exact turn IDs."""
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{label} is invalid")
    ids = [
        _validate_string(item, label=f"{label}[]", maximum=128)
        for item in value
    ]
    if len(ids) != len(set(ids)) or any(item not in valid_turn_ids for item in ids):
        raise ValueError(f"{label} contains duplicate or unknown turn IDs")
    return ids


def validate_issue_state(value: object, *, valid_turn_ids: set[str]) -> dict[str, Any]:
    """Validate issue-state shape, bounds, enums, and exact source IDs."""
    item = _parse_object(value, label="issue state")
    _exact_keys(
        item,
        {"status", "live_issue", "rejected_answer_targets", "source_turn_ids"},
        label="issue state",
    )
    status = item.get("status")
    if status not in {"issue_found", "no_stable_issue", "unclear"}:
        raise ValueError("issue state status is invalid")
    live = item.get("live_issue")
    if status == "issue_found":
        if not isinstance(live, dict):
            raise ValueError("issue_found requires live_issue")
        _exact_keys(live, LIVE_ISSUE_SCHEMA["properties"], label="live issue")
        if live.get("issue_id") != "I1":
            raise ValueError("live issue ID is invalid")
        _validate_string(
            live.get("question_under_discussion"),
            label="question_under_discussion",
            maximum=500,
        )
        _validate_ids(
            live.get("raised_by_turn_ids"),
            label="raised_by_turn_ids",
            valid_turn_ids=valid_turn_ids,
        )
        stance = live.get("user_stance")
        if not isinstance(stance, dict):
            raise ValueError("user_stance is invalid")
        _exact_keys(stance, USER_STANCE_SCHEMA["properties"], label="user_stance")
        if stance.get("status") not in {"asserted", "challenged", "questioned", "unclear"}:
            raise ValueError("user stance status is invalid")
        _validate_string(
            stance.get("canonical_text"), label="user stance text", maximum=500
        )
        _validate_ids(
            stance.get("source_turn_ids"),
            label="user stance source_turn_ids",
            valid_turn_ids=valid_turn_ids,
        )
        signature = live.get("signature")
        if not isinstance(signature, dict):
            raise ValueError("signature is invalid")
        _exact_keys(signature, SIGNATURE_SCHEMA["properties"], label="signature")
        _validate_string(signature.get("subject"), label="signature subject", maximum=240)
        _validate_string(signature.get("object"), label="signature object", maximum=240)
        for field, allowed in {
            "relation": {
                "requires", "sufficient_for", "causes", "enables", "prevents",
                "compares_with", "defines", "attributes", "other", "unclear",
            },
            "polarity": {"affirmed", "denied", "questioned", "unclear"},
            "modality": {
                "necessity", "sufficiency", "possibility", "actuality",
                "normative", "none", "unclear",
            },
            "scope": {"universal", "qualified", "particular", "unclear"},
            "kind": {
                "factual", "normative", "conceptual", "counterfactual",
                "interpretive", "mixed", "unclear",
            },
        }.items():
            if signature.get(field) not in allowed:
                raise ValueError(f"signature {field} is invalid")
        if live.get("status") not in {"open", "answered", "replaced", "unclear"}:
            raise ValueError("live issue status is invalid")
        if live.get("confidence") not in {"high", "medium", "low"}:
            raise ValueError("live issue confidence is invalid")
    elif live is not None:
        raise ValueError("non-issue status must use null live_issue")

    rejected = item.get("rejected_answer_targets")
    if not isinstance(rejected, list) or len(rejected) > 3:
        raise ValueError("rejected_answer_targets is invalid")
    for index, target in enumerate(rejected):
        if not isinstance(target, dict):
            raise ValueError("rejected answer target must be an object")
        _exact_keys(target, REJECTED_TARGET_SCHEMA["properties"], label=f"rejected target {index}")
        _validate_ids(
            target.get("account_turn_ids"),
            label="account_turn_ids",
            valid_turn_ids=valid_turn_ids,
        )
        _validate_string(
            target.get("answered_question"), label="answered_question", maximum=500
        )
        _validate_ids(
            target.get("rejected_by_turn_ids"),
            label="rejected_by_turn_ids",
            valid_turn_ids=valid_turn_ids,
        )
        if target.get("repair_type") not in {
            "not_the_question", "wrong_actor", "wrong_relation", "wrong_scope",
            "wrong_polarity", "wrong_time_or_counterfactual", "other",
        }:
            raise ValueError("rejected answer repair_type is invalid")
        if target.get("status") != "deprecated_as_answer_target":
            raise ValueError("rejected answer target status is invalid")
        if target.get("confidence") not in {"high", "medium", "low"}:
            raise ValueError("rejected answer target confidence is invalid")
    _validate_ids(
        item.get("source_turn_ids"),
        label="source_turn_ids",
        valid_turn_ids=valid_turn_ids,
    )
    return item


def issue_state_usable(value: object) -> bool:
    """Return whether a validated issue state may influence a writer."""
    return bool(
        isinstance(value, dict)
        and value.get("status") == "issue_found"
        and isinstance(value.get("live_issue"), dict)
        and value["live_issue"].get("confidence") in {"high", "medium"}
    )


def validate_answerhood_critic(
    value: object, *, valid_turn_ids: set[str]
) -> dict[str, Any]:
    """Validate answerhood separately from logical stance relation."""
    item = _parse_object(value, label="answerhood critic")
    _exact_keys(item, ANSWERHOOD_SCHEMA["properties"], label="answerhood critic")
    _validate_string(
        item.get("candidate_answered_question"),
        label="candidate_answered_question",
        maximum=500,
        allow_empty=True,
    )
    _validate_string(
        item.get("candidate_proposition"),
        label="candidate_proposition",
        maximum=500,
        allow_empty=True,
    )
    if item.get("addresses_live_issue") not in {
        "direct", "partial", "elaboration_after_answer", "substitute", "unrelated", "unclear",
    }:
        raise ValueError("addresses_live_issue is invalid")
    if item.get("logical_relation_to_user_stance") not in {
        "entails", "strengthens", "weakens", "contradicts", "compatible", "unknown",
    }:
        raise ValueError("logical relation is invalid")
    if type(item.get("explicit_repair_present")) is not bool:
        raise ValueError("explicit_repair_present must be boolean")
    if type(item.get("revives_rejected_answer_target")) is not bool:
        raise ValueError("revives_rejected_answer_target must be boolean")
    differences = item.get("alignment_differences")
    allowed_differences = set(ANSWERHOOD_SCHEMA["properties"]["alignment_differences"]["items"]["enum"])
    if (
        not isinstance(differences, list)
        or len(differences) > 10
        or len(differences) != len(set(differences))
        or any(value not in allowed_differences for value in differences)
        or ("none" in differences and len(differences) != 1)
    ):
        raise ValueError("alignment_differences is invalid")
    _validate_ids(
        item.get("evidence_turn_ids"),
        label="evidence_turn_ids",
        valid_turn_ids=valid_turn_ids,
    )
    if item.get("confidence") not in {"high", "medium", "low"}:
        raise ValueError("critic confidence is invalid")
    return item


def broad_substitution_flag(critic: Mapping[str, Any]) -> bool:
    """Return the descriptive high-confidence broad substitution flag."""
    return bool(
        critic.get("confidence") == "high"
        and critic.get("addresses_live_issue") in {"substitute", "unrelated"}
    )


def narrow_gate_trigger(
    critic: Mapping[str, Any], *, valid_turn_ids: set[str]
) -> bool:
    """Return the sole rule that may authorise an alignment repair."""
    evidence = critic.get("evidence_turn_ids")
    evidence_valid = bool(
        isinstance(evidence, list)
        and all(type(value) is str and value in valid_turn_ids for value in evidence)
    )
    return bool(
        critic.get("confidence") == "high"
        and critic.get("addresses_live_issue") in {"substitute", "unrelated"}
        and critic.get("explicit_repair_present") is True
        and critic.get("revives_rejected_answer_target") is True
        and evidence_valid
    )


def issue_transcript_hash(turns: Sequence[Mapping[str, Any]]) -> str:
    """Hash the raw authoritative transcript exactly as stored."""
    return sha256_value(list(turns))


def _skip(
    source_kind: str, identity: object, reason_code: str, detail: str
) -> dict[str, str]:
    """Return a concise private unusable-case record."""
    return {
        "source_kind": source_kind,
        "identity": str(identity or "unavailable")[:256],
        "reason_code": reason_code,
        "detail": detail[:500],
    }


def _turn_has_images(turn: Mapping[str, Any]) -> bool:
    """Return whether retained metadata reports a material image premise."""
    summary = turn.get("reply_visual_context_summary")
    return bool(
        isinstance(summary, dict)
        and type(summary.get("native_photo_count_max")) is int
        and summary["native_photo_count_max"] > 0
    )


def _exact_issue_turn(
    turn: Mapping[str, Any], *, principal: str, sequence: int
) -> dict[str, Any]:
    """Return one exact raw-text issue-transcript turn with source identity."""
    return {
        "turn_id": str(turn["post_id"]),
        "parent_turn_id": (
            str(turn.get("parent_post_id")) if turn.get("parent_post_id") else None
        ),
        "author_role": str(turn["author_role"]),
        "principal_contributor": bool(
            turn.get("author_role") == "user"
            and str(turn.get("author_key") or "") == principal
        ),
        "text": str(turn["text"]),
        "sequence": sequence,
    }


def _surface_trigger_reasons(
    issue_transcript: Sequence[Mapping[str, Any]], retained: Iterable[str] = ()
) -> list[str]:
    """Return ordered descriptive priority signals without quality labels."""
    reasons = set(str(value) for value in retained)
    user_turns = [turn for turn in issue_transcript if turn.get("author_role") == "user"]
    account_turns = [turn for turn in issue_transcript if turn.get("author_role") == "account"]
    user_text = "\n".join(str(turn.get("text") or "") for turn in user_turns)
    if prospective_extractor.correction_cues(user_text):
        reasons.add("explicit_correction_cue")
    if len(user_turns) >= 2:
        reasons.add("same_author_path_continuation")
    if len(account_turns) >= 2:
        reasons.add("multiple_account_replies_on_path")
    substantive_count = sum(
        prospective_extractor.substantive_result(turn.get("text")).substantive
        for turn in issue_transcript
    )
    if substantive_count >= 3:
        reasons.add("third_or_later_substantive_path_turn")
    folded = " ".join(user_text.casefold().replace("‑", "-").split())
    whole = " ".join(
        str(turn.get("text") or "").casefold().replace("‑", "-")
        for turn in issue_transcript
    )
    if (
        "liberty" in whole
        and "economic" in whole
        and ("non-economic" in whole or "non economic" in whole)
        and "security" in whole
        and "independen" in whole
        and ("not asking" in folded or "not about" in folded)
    ):
        reasons.add("requested_liberty_conversation_match")
    return [reason for reason in PRIORITY_REASONS if reason in reasons]


def _priority_band(reasons: Sequence[str]) -> int:
    """Return the first deterministic candidate-priority band."""
    return min(
        (PRIORITY_REASONS.index(reason) for reason in reasons if reason in PRIORITY_REASONS),
        default=len(PRIORITY_REASONS),
    )


def _non_authoritative_refs(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Retain sibling and hand-off provenance without live-issue authority."""
    result: list[dict[str, Any]] = []
    for field, kind in (
        ("sibling_context_refs", "sibling"),
        ("handoff_context_refs", "handoff"),
    ):
        values = row.get(field)
        if not isinstance(values, list):
            continue
        for value in values[:32]:
            if not isinstance(value, dict):
                continue
            result.append(
                {
                    "context_kind": kind,
                    "authoritative_for_live_issue": False,
                    "source": copy.deepcopy(value),
                }
            )
    return result


def prospective_candidate_from_row(
    row: object, *, source_identity: str, review_pack_identity: Mapping[str, Any]
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    """Build one strict exact-path candidate from a prospective review row."""
    source_kind = "prospective"
    if not isinstance(row, dict):
        return None, _skip(source_kind, None, "row_not_object", "row is not an object")
    identity = row.get("candidate_key") or row.get("branch_key") or "unavailable"
    branch_key = str(row.get("branch_key") or "")
    principal = str(row.get("principal_author_key") or "")
    if not branch_key or not principal or not str(row.get("candidate_key") or ""):
        return None, _skip(
            source_kind, identity, "missing_authoritative_identity",
            "branch, candidate, or principal identity is unavailable",
        )
    warnings = row.get("warnings")
    if not isinstance(warnings, list):
        return None, _skip(source_kind, identity, "warnings_invalid", "warnings must be a list")
    warning_values = {str(value) for value in warnings}
    if warning_values & {
        "ambiguous_parentage", "missing_parent_post", "root_not_reached_by_parent_path"
    }:
        return None, _skip(
            source_kind, identity, "ambiguous_parentage",
            "path has ambiguous or incomplete authoritative parentage",
        )
    raw_path = row.get("path_turns")
    if not isinstance(raw_path, list) or not raw_path or not all(isinstance(turn, dict) for turn in raw_path):
        return None, _skip(source_kind, identity, "partial_path_reconstruction", "path_turns is invalid")
    principal_indices = [
        index
        for index, turn in enumerate(raw_path)
        if turn.get("author_role") == "user"
        and str(turn.get("author_key") or "") == principal
    ]
    if not principal_indices:
        return None, _skip(source_kind, identity, "target_missing", "principal target turn is absent")
    target_index = principal_indices[-1]
    path = raw_path[: target_index + 1]
    if len(path) > MAX_PATH_TURNS:
        return None, _skip(source_kind, identity, "path_over_20_turns", "issue path exceeds 20 turns")
    for index, turn in enumerate(path):
        role = turn.get("author_role")
        if role not in {"account", "user"}:
            return None, _skip(source_kind, identity, "conflicting_parent_identity", "path contains an unknown role")
        if role == "user" and str(turn.get("author_key") or "") != principal:
            return None, _skip(source_kind, identity, "mixed_principal_authors", "path contains another contributor")
        if not str(turn.get("post_id") or ""):
            return None, _skip(source_kind, identity, "missing_required_text", "turn ID is unavailable")
        text = turn.get("text")
        if type(text) is not str or not text.strip():
            return None, _skip(source_kind, identity, "missing_required_text", "turn text is unavailable")
        if index and str(turn.get("parent_post_id") or "") != str(path[index - 1].get("post_id") or ""):
            return None, _skip(source_kind, identity, "ambiguous_parentage", "path is not a contiguous parent chain")
    if any(_turn_has_images(turn) for turn in path) or row.get("reply_visual_context_summaries"):
        return None, _skip(source_kind, identity, "image_dependent", "path has unavailable visual premises")
    substantive_users = [
        turn
        for turn in path
        if turn.get("author_role") == "user"
        and prospective_extractor.substantive_result(turn.get("text")).substantive
    ]
    if len(substantive_users) < 2:
        return None, _skip(source_kind, identity, "insufficient_principal_turns", "fewer than two substantive contributor turns")
    if not any(turn.get("author_role") == "account" for turn in path[:-1]):
        return None, _skip(source_kind, identity, "account_context_missing", "no account turn precedes target")

    production_case, production_skip = base_harness.prospective_case_from_row(row)
    if production_case is None:
        assert production_skip is not None
        return None, _skip(
            source_kind,
            identity,
            "invalid_production_shaped_reply_context",
            str(production_skip.get("reason_code") or "invalid production context"),
        )
    target_turn_id = str(path[-1]["post_id"])
    if production_case["context"]["target_id"] != target_turn_id:
        return None, _skip(source_kind, identity, "target_mismatch", "production target differs from issue target")
    issue_transcript = [
        _exact_issue_turn(turn, principal=principal, sequence=index)
        for index, turn in enumerate(path, start=1)
    ]
    retained_reasons = row.get("review_reason_codes")
    reasons = _surface_trigger_reasons(
        issue_transcript,
        retained_reasons if isinstance(retained_reasons, list) else (),
    )
    provenance_refs = _non_authoritative_refs(row)
    if provenance_refs and not any(reason in reasons for reason in ("explicit_correction_cue", "post_clarification_continuation", "same_author_path_continuation", "multiple_account_replies_on_path", "third_or_later_substantive_path_turn", "requested_liberty_conversation_match")):
        reasons.append("branch_contamination_control")
    context = validate_reply_context(production_case["context"])
    return {
        "case_id": f"qud:{branch_key}:{target_turn_id}",
        "source_kind": source_kind,
        "source_occurrences": [
            {
                "source_kind": source_kind,
                "source_identity": source_identity,
                "candidate_identity": str(row["candidate_key"]),
            }
        ],
        "review_pack_identity": copy.deepcopy(dict(review_pack_identity)),
        "branch_key": branch_key,
        "target_turn_id": target_turn_id,
        "principal_contributor_key": principal,
        "trigger_reason_codes": reasons,
        "priority_band": _priority_band(reasons),
        "issue_transcript": issue_transcript,
        "issue_transcript_sha256": issue_transcript_hash(issue_transcript),
        "production_context": context,
        "production_context_sha256": sha256_value(context),
        "recent_replies": copy.deepcopy(production_case["recent_replies"]),
        "recent_replies_sha256": sha256_value(production_case["recent_replies"]),
        "media_context": None,
        "target_identity": copy.deepcopy(production_case["canonical_target_identity"]),
        "non_authoritative_context_refs": provenance_refs,
        "historical_labels": [],
    }, None


def prior_candidate_from_row(
    row: object, *, source_file: Path, source_file_sha256: str
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    """Recover only demonstrably complete prior frozen production contexts."""
    source_kind = "prior_evaluation"
    if not isinstance(row, dict):
        return None, _skip(source_kind, None, "row_not_object", "prior row is not an object")
    identity = row.get("case_id") or "unavailable"
    if row.get("source") != "prospective":
        return None, _skip(source_kind, identity, "non_historical_fixture", "row is not a retained prospective case")
    metadata = row.get("retained_metadata")
    context = row.get("context")
    if not isinstance(metadata, dict) or not isinstance(context, dict):
        return None, _skip(source_kind, identity, "missing_authoritative_identity", "metadata or context is absent")
    branch_key = str(metadata.get("branch_key") or "")
    target_id = str(context.get("target_id") or "")
    parents = context.get("parent_thread")
    if not branch_key or not target_id or not isinstance(parents, list):
        return None, _skip(source_kind, identity, "missing_authoritative_identity", "branch, target, or ordered parents are absent")
    if context.get("quoted_post") is not None or row.get("media_context") is not None:
        return None, _skip(source_kind, identity, "image_or_quote_dependent", "prior context is not a plain parent path")
    if not parents or str(context.get("thread_id") or "") != str(parents[0].get("post_id") or ""):
        return None, _skip(source_kind, identity, "partial_path_reconstruction", "frozen production context omits the path root")
    if len(parents) + 1 > MAX_PATH_TURNS:
        return None, _skip(source_kind, identity, "path_over_20_turns", "prior path exceeds 20 turns")
    if any(
        not isinstance(turn, dict)
        or set(turn) != {"post_id", "author_role", "text"}
        or turn.get("author_role") not in {"account", "user"}
        or not str(turn.get("post_id") or "")
        or type(turn.get("text")) is not str
        or not str(turn.get("text") or "").strip()
        for turn in parents
    ):
        return None, _skip(source_kind, identity, "missing_required_text", "prior ordered turn is incomplete")
    account_texts = [str(turn["text"]) for turn in parents if turn["author_role"] == "account"]
    if list(row.get("recent_replies") or []) != account_texts:
        return None, _skip(source_kind, identity, "partial_path_reconstruction", "retained recent replies prove omitted account turns")
    incoming = context.get("incoming_contribution")
    if type(incoming) is not str or not incoming.strip():
        return None, _skip(source_kind, identity, "missing_required_text", "target text is absent")
    if not account_texts:
        return None, _skip(source_kind, identity, "account_context_missing", "no account turn precedes target")
    user_count = sum(turn["author_role"] == "user" for turn in parents) + 1
    if user_count < 2:
        return None, _skip(source_kind, identity, "insufficient_principal_turns", "fewer than two contributor turns are retained")
    try:
        clean_context = validate_reply_context(context)
    except (TypeError, ValueError) as exc:
        return None, _skip(source_kind, identity, "invalid_production_shaped_reply_context", str(exc))
    principal = "prior-principal:" + sha256_value(
        {"source": source_file_sha256, "branch_key": branch_key}
    )[:32]
    raw_turns: list[dict[str, Any]] = []
    for index, turn in enumerate(parents, start=1):
        raw_turns.append(
            {
                "turn_id": str(turn["post_id"]),
                "parent_turn_id": str(parents[index - 2]["post_id"]) if index > 1 else None,
                "author_role": str(turn["author_role"]),
                "principal_contributor": turn["author_role"] == "user",
                "text": str(turn["text"]),
                "sequence": index,
            }
        )
    raw_turns.append(
        {
            "turn_id": target_id,
            "parent_turn_id": str(parents[-1]["post_id"]),
            "author_role": "user",
            "principal_contributor": True,
            "text": incoming,
            "sequence": len(raw_turns) + 1,
        }
    )
    if sum(
        turn["principal_contributor"]
        and prospective_extractor.substantive_result(turn["text"]).substantive
        for turn in raw_turns
    ) < 2:
        return None, _skip(source_kind, identity, "insufficient_principal_turns", "prior contributor turns are not substantive")
    reasons = _surface_trigger_reasons(raw_turns)
    if clean_context.get("clarification_request") is not None:
        reasons = list(dict.fromkeys([*reasons, "post_clarification_continuation"]))
        reasons.sort(key=lambda value: PRIORITY_REASONS.index(value))
    labels: list[dict[str, Any]] = []
    explicit = row.get("proposition_substitution_label")
    if isinstance(explicit, dict) and explicit.get("provenance"):
        labels.append(copy.deepcopy(explicit))
    return {
        "case_id": f"qud:{branch_key}:{target_id}",
        "source_kind": source_kind,
        "source_occurrences": [
            {
                "source_kind": source_kind,
                "source_identity": f"{source_file}:{source_file_sha256}",
                "candidate_identity": str(identity),
            }
        ],
        "review_pack_identity": None,
        "branch_key": branch_key,
        "target_turn_id": target_id,
        "principal_contributor_key": principal,
        "trigger_reason_codes": reasons,
        "priority_band": _priority_band(reasons),
        "issue_transcript": raw_turns,
        "issue_transcript_sha256": issue_transcript_hash(raw_turns),
        "production_context": clean_context,
        "production_context_sha256": sha256_value(clean_context),
        "recent_replies": copy.deepcopy(account_texts),
        "recent_replies_sha256": sha256_value(account_texts),
        "media_context": None,
        "target_identity": copy.deepcopy(row.get("canonical_target_identity") or f"x-post:{target_id}"),
        "non_authoritative_context_refs": [],
        "historical_labels": labels,
    }, None


def _merge_duplicate_candidate(existing: dict[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    """Merge provenance only when exact authoritative case inputs agree."""
    invariant_fields = (
        "branch_key", "target_turn_id", "issue_transcript_sha256",
        "production_context_sha256", "recent_replies_sha256",
    )
    if any(existing.get(field) != incoming.get(field) for field in invariant_fields):
        raise EvaluationError("duplicate branch/target has conflicting exact inputs")
    merged = copy.deepcopy(existing)
    occurrences = [
        *merged.get("source_occurrences", []),
        *copy.deepcopy(incoming.get("source_occurrences", [])),
    ]
    merged["source_occurrences"] = sorted(
        {canonical_json_bytes(value): value for value in occurrences}.values(),
        key=canonical_json_bytes,
    )
    merged["trigger_reason_codes"] = [
        reason
        for reason in PRIORITY_REASONS
        if reason in set(merged.get("trigger_reason_codes", []))
        | set(incoming.get("trigger_reason_codes", []))
    ]
    merged["priority_band"] = _priority_band(merged["trigger_reason_codes"])
    merged["historical_labels"] = [
        *merged.get("historical_labels", []),
        *copy.deepcopy(incoming.get("historical_labels", [])),
    ]
    if existing.get("source_kind") != "prospective" and incoming.get("source_kind") == "prospective":
        for field in ("source_kind", "review_pack_identity", "principal_contributor_key", "non_authoritative_context_refs"):
            merged[field] = copy.deepcopy(incoming.get(field))
    return merged


def select_candidate_pool(
    candidates: Sequence[dict[str, Any]], *, frozen_source_identity: str,
    cap: int = MAX_CANDIDATE_CASES,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Deduplicate, prioritise, hash-sort, cap, and freeze candidate inputs."""
    if type(cap) is not int or not 1 <= cap <= MAX_CANDIDATE_CASES:
        raise EvaluationError("candidate cap must be 1..80")
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    skips: list[dict[str, str]] = []
    for candidate in candidates:
        key = (str(candidate["branch_key"]), str(candidate["target_turn_id"]))
        if key in by_key:
            try:
                by_key[key] = _merge_duplicate_candidate(by_key[key], candidate)
            except EvaluationError as exc:
                skips.append(_skip(candidate["source_kind"], candidate["case_id"], "conflicting_duplicate", str(exc)))
            continue
        by_key[key] = copy.deepcopy(candidate)
    ordered = sorted(
        by_key.values(),
        key=lambda value: (
            int(value.get("priority_band", len(PRIORITY_REASONS))),
            sha256_bytes(
                (
                    frozen_source_identity
                    + "\0"
                    + str(value["branch_key"])
                    + "\0"
                    + str(value["target_turn_id"])
                ).encode("utf-8")
            ),
        ),
    )
    selected: list[dict[str, Any]] = []
    control_count = 0
    for candidate in ordered:
        is_control = candidate.get("priority_band") == PRIORITY_REASONS.index("branch_contamination_control")
        if is_control and control_count >= 10:
            skips.append(_skip(candidate["source_kind"], candidate["case_id"], "bounded_control_cap", "at most ten branch-contamination controls"))
            continue
        if len(selected) >= cap:
            skips.append(_skip(candidate["source_kind"], candidate["case_id"], "candidate_pool_cap", f"fixed cap {cap}"))
            continue
        frozen = copy.deepcopy(candidate)
        frozen["selection_index"] = len(selected)
        frozen["candidate_identity_sha256"] = sha256_value(
            {
                "branch_key": frozen["branch_key"],
                "target_turn_id": frozen["target_turn_id"],
                "issue_transcript_sha256": frozen["issue_transcript_sha256"],
                "production_context_sha256": frozen["production_context_sha256"],
            }
        )
        selected.append(frozen)
        control_count += is_control
    return selected, skips


def load_review_pack(review_pack: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Reuse exhaustive immutable review-pack loading from the prior harness."""
    return base_harness.load_review_pack(review_pack)


def build_candidate_pool(
    *, review_pack: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, str]], dict[str, Any]]:
    """Build the complete immutable candidate pool without provider access."""
    pack_identity, prospective_rows = load_review_pack(review_pack)
    candidates: list[dict[str, Any]] = []
    skips: list[dict[str, str]] = []
    for row in prospective_rows:
        candidate, skip = prospective_candidate_from_row(
            row,
            source_identity=str(pack_identity["pack_content_sha256"]),
            review_pack_identity=pack_identity,
        )
        if candidate is not None:
            candidates.append(candidate)
        elif skip is not None:
            skips.append(skip)

    prior_inventory: list[dict[str, Any]] = []
    for path in PRIOR_CASE_FILES:
        if not path.is_file():
            raise EvaluationError(f"required prior frozen case file is absent: {path}")
        digest = sha256_file(path)
        rows = read_jsonl(path)
        prior_inventory.append({"path": str(path), "sha256": digest, "row_count": len(rows)})
        for row in rows:
            candidate, skip = prior_candidate_from_row(
                row, source_file=path, source_file_sha256=digest
            )
            if candidate is not None:
                candidates.append(candidate)
            elif skip is not None:
                skips.append(skip)
    frozen_source_identity = sha256_value(
        {"review_pack": pack_identity, "prior_inputs": prior_inventory}
    )
    selected, selection_skips = select_candidate_pool(
        candidates, frozen_source_identity=frozen_source_identity
    )
    skips.extend(selection_skips)
    inventory = {
        "prospective_source_rows": len(prospective_rows),
        "prior_source_rows": sum(item["row_count"] for item in prior_inventory),
        "valid_candidate_occurrences": len(candidates),
        "deduplicated_valid_candidates": len(
            {(item["branch_key"], item["target_turn_id"]) for item in candidates}
        ),
        "selected_candidate_pool": len(selected),
        "unusable_or_unselected": len(skips),
        "prior_inputs": prior_inventory,
        "frozen_source_identity_sha256": frozen_source_identity,
    }
    return pack_identity, selected, skips, inventory


class QudRequestLedger(writer_harness.WriterRequestLedger):
    """Identity-bound request ledger with the experiment's US$20 ceiling."""

    def __init__(
        self,
        path: Path,
        *,
        case_set_sha256: str,
        hard_limit_usd: float = GLOBAL_COST_CEILING_USD,
    ) -> None:
        """Open or create the QUD experiment ledger."""
        self.path = path
        self.limit_ticks = int(
            round(hard_limit_usd * base_harness.USD_TICKS_PER_DOLLAR)
        )
        maximum = int(
            GLOBAL_COST_CEILING_USD * base_harness.USD_TICKS_PER_DOLLAR
        )
        if self.limit_ticks <= 0 or self.limit_ticks > maximum:
            raise EvaluationError("global provider-cost ceiling must be in (0, 20.00]")
        if path.exists():
            value = read_json(path)
            if not isinstance(value, dict):
                raise EvaluationError("request ledger is not an object")
            self.data = value
            if (
                self.data.get("schema_version") != SCHEMA_VERSION
                or self.data.get("run_version") != RUN_VERSION
                or self.data.get("case_set_sha256") != case_set_sha256
                or self.data.get("hard_limit_ticks") != self.limit_ticks
                or not isinstance(self.data.get("operations"), list)
            ):
                raise EvaluationError("request ledger identity or ceiling changed")
        else:
            self.data = {
                "schema_version": SCHEMA_VERSION,
                "run_version": RUN_VERSION,
                "case_set_sha256": case_set_sha256,
                "hard_limit_ticks": self.limit_ticks,
                "hard_limit_usd": (
                    self.limit_ticks / base_harness.USD_TICKS_PER_DOLLAR
                ),
                "blocked": False,
                "blocked_reason": None,
                "operations": [],
                "created_at": utc_now(),
            }
            self._save()
        self._validate_unique_hashes()


class CanonicalProviderClient:
    """Thin reuse layer over the existing cached synchronous research transport."""

    def __init__(
        self,
        *,
        arm: str,
        case_id: str,
        ledger: QudRequestLedger | None,
        response_dir: Path | None,
        api_keys: Mapping[str, str] | None,
        xai_model_metadata: Mapping[str, Mapping[str, Any]] | None,
        post: Callable[..., Any] = requests.post,
        sleep: Callable[[float], None] = time.sleep,
        live_request: Callable[..., tuple[object, dict[str, Any]]] | None = None,
    ) -> None:
        """Bind one arm/case consumer to the shared canonical cache."""
        if arm not in ARMS:
            raise EvaluationError(f"unknown arm: {arm}")
        self.arm = arm
        self.case_id = str(case_id)
        self.call_events: list[dict[str, Any]] = []
        self._delegate = writer_harness.WriterPipelineTransport(
            arm=arm,
            case_id=self.case_id,
            prior_cache=object(),  # unused by the inherited live/cache helper
            registry=writer_harness.WriterInputRegistry(),
            ledger=ledger,
            response_dir=response_dir,
            api_keys=api_keys,
            xai_model_metadata=xai_model_metadata,
            post=post,
            sleep=sleep,
            live_request=live_request,
        )

    def request(
        self,
        *,
        provider: str,
        stage: str,
        model: str,
        reasoning_effort: str,
        system_prompt: str,
        payload: Mapping[str, Any],
        response_schema: Mapping[str, Any],
        timeout_seconds: int,
        max_output_tokens: int,
    ) -> tuple[object, dict[str, Any]]:
        """Serve one fully identified request from cache or one bounded send."""
        allowed = {
            "xAI": {
                (PRODUCTION_XAI_MODEL, PRODUCTION_XAI_EFFORT),
                (ISSUE_MODEL, ISSUE_EFFORT),
                (CRITIC_MODEL, CRITIC_EFFORT),
            },
            "OpenAI": {(PRODUCTION_OPENAI_MODEL, PRODUCTION_OPENAI_EFFORT)},
        }
        if provider not in allowed or (model, reasoning_effort) not in allowed[provider]:
            raise EvaluationError(
                f"unapproved provider identity: {provider}/{model}/{reasoning_effort}"
            )
        if not isinstance(payload, Mapping) or not isinstance(response_schema, Mapping):
            raise EvaluationError("structured request payload and schema must be objects")
        self._delegate.sequence += 1
        content, event = self._delegate._send_live(
            logical_provider=provider,
            stage=stage,
            logical_model=model,
            logical_reasoning_effort=reasoning_effort,
            effective_provider=provider,
            effective_model=model,
            effective_reasoning_effort=reasoning_effort,
            system_prompt=system_prompt,
            payload=payload,
            response_schema=response_schema,
            timeout_seconds=timeout_seconds,
            max_output_tokens=max_output_tokens,
        )
        event["consumer_arm"] = self.arm
        event["tools_enabled"] = False
        self.call_events.append(copy.deepcopy(event))
        return content, event


def _production_identity(provider: str) -> tuple[str, str]:
    """Return the frozen model and effort for one production provider."""
    if provider == "xAI":
        return PRODUCTION_XAI_MODEL, PRODUCTION_XAI_EFFORT
    if provider == "OpenAI":
        return PRODUCTION_OPENAI_MODEL, PRODUCTION_OPENAI_EFFORT
    raise EvaluationError(f"unsupported production provider: {provider}")


class PipelineTransport:
    """Preserve production calls and optionally inject issue state at writer stages."""

    def __init__(
        self,
        *,
        client: CanonicalProviderClient,
        inject_issue_state: bool = False,
        issue_state: Mapping[str, Any] | None = None,
    ) -> None:
        """Initialise one production-pipeline transport view."""
        if inject_issue_state and not issue_state_usable(issue_state):
            raise EvaluationError("only a usable issue state may reach a writer")
        self.client = client
        self.inject_issue_state = inject_issue_state
        self.issue_state = copy.deepcopy(dict(issue_state or {}))
        self.call_events: list[dict[str, Any]] = []
        self.writer_responses: list[dict[str, Any]] = []

    def __call__(
        self,
        *,
        provider: str,
        stage: str,
        model: str,
        system_prompt: str,
        payload: dict[str, Any],
        response_schema: dict[str, Any],
        timeout_seconds: int,
        max_output_tokens: int,
        reasoning_effort: str,
    ) -> object:
        """Forward an exact production request across the declared isolation boundary."""
        expected_model, expected_effort = _production_identity(provider)
        if (model, reasoning_effort) != (expected_model, expected_effort):
            raise EvaluationError("production pipeline provider identity changed")
        original_identity = base_harness.canonical_request_identity(
            provider=provider,
            model=model,
            reasoning_effort=reasoning_effort,
            system_prompt=system_prompt,
            user_payload=payload,
            response_schema=response_schema,
            maximum_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
        )
        effective_prompt = system_prompt
        effective_payload = copy.deepcopy(payload)
        injected = self.inject_issue_state and stage in WRITER_STAGES
        if injected:
            if "issue_state" in effective_payload:
                raise EvaluationError("production writer payload unexpectedly has issue_state")
            effective_prompt = system_prompt + "\n\n" + ISSUE_STATE_WRITER_INSTRUCTION
            effective_payload["issue_state"] = copy.deepcopy(self.issue_state)
        content, event = self.client.request(
            provider=provider,
            stage=stage,
            model=model,
            reasoning_effort=reasoning_effort,
            system_prompt=effective_prompt,
            payload=effective_payload,
            response_schema=response_schema,
            timeout_seconds=timeout_seconds,
            max_output_tokens=max_output_tokens,
        )
        event.update(
            {
                "issue_state_injected": injected,
                "canonical_unmodified_production_request_sha256": sha256_value(
                    original_identity
                ),
                "effective_system_prompt_sha256": sha256_bytes(
                    effective_prompt.encode("utf-8")
                ),
                "effective_user_payload_sha256": sha256_value(effective_payload),
            }
        )
        self.call_events.append(event)
        if stage in WRITER_STAGES:
            self.writer_responses.append(
                {"stage": stage, "response": copy.deepcopy(content)}
            )
        return content


def _structured_research_call(
    *,
    client: CanonicalProviderClient,
    provider: str,
    stage: str,
    model: str,
    reasoning_effort: str,
    system_prompt: str,
    payload: Mapping[str, Any],
    response_schema: Mapping[str, Any],
    max_output_tokens: int,
    timeout_seconds: int = 180,
) -> tuple[object, dict[str, Any]]:
    """Make one strict, tool-free research request through the shared cache."""
    return client.request(
        provider=provider,
        stage=stage,
        model=model,
        reasoning_effort=reasoning_effort,
        system_prompt=system_prompt,
        payload=payload,
        response_schema=response_schema,
        timeout_seconds=timeout_seconds,
        max_output_tokens=max_output_tokens,
    )


def _event_totals(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate bounded request usage, cost, cache, and latency fields."""
    return {
        "provider_call_events": len(events),
        "network_provider_calls": sum(
            event.get("network_request") is True for event in events
        ),
        "cache_hits": sum(event.get("cache_hit") is True for event in events),
        "input_tokens": sum(int(event.get("input_tokens") or 0) for event in events),
        "cached_input_tokens": sum(
            int(event.get("cached_input_tokens") or 0) for event in events
        ),
        "output_tokens": sum(int(event.get("output_tokens") or 0) for event in events),
        "reasoning_tokens": sum(
            int(event.get("reasoning_tokens") or 0) for event in events
        ),
        "provider_reported_billed_cost_usd": sum(
            float(event.get("provider_reported_cost_usd") or 0) for event in events
        ),
        "estimated_openai_cost_usd": sum(
            float(event.get("estimated_cost_usd") or 0) for event in events
        ),
        "provider_latency_seconds": sum(
            float(event.get("provider_latency_seconds") or 0) for event in events
        ),
    }


def _first_writer_candidate(transport: PipelineTransport) -> str | None:
    """Extract the initial writer draft solely for private diagnostics."""
    for row in transport.writer_responses:
        if row.get("stage") != "writer_v3_initial":
            continue
        try:
            value = tested_pipeline._validate_writer(row.get("response"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return value["reply"] if value["status"] == "reply" else None
    return None


@dataclass(frozen=True)
class EffectivePipelineRun:
    """Research representation of the production wrapper's final public outcome."""

    pipeline_stage_status: str
    pipeline_stage_reason: str
    effective_status: str
    effective_reason: str
    initial_public_candidate: str | None
    final_public_candidate: str | None
    baseline_reached_writer: bool
    model_call_count: int
    revision_count: int
    audit: tuple[dict[str, Any], ...]
    direct_answer_repair: dict[str, Any]


def run_current_public_pipeline(
    *,
    case: Mapping[str, Any],
    config: Mapping[str, Any],
    repository: EvidenceRepository,
    transport: PipelineTransport,
) -> EffectivePipelineRun:
    """Replay the pipeline plus its sole production direct-answer handling."""
    result = tested_pipeline.run_reply_pipeline(
        context=copy.deepcopy(case["production_context"]),
        config=copy.deepcopy(dict(config)),
        repository=repository,
        transport=transport,
        maximum_reply_length=MAX_REPLY_LENGTH,
        recent_replies=copy.deepcopy(case["recent_replies"]),
        media_context=None,
    )
    effective_reply = result.reply
    effective_status = str(result.status)
    effective_reason = str(result.reason)
    effective_audit = tuple(copy.deepcopy(result.audit))
    model_calls = int(result.model_call_count)
    revisions = int(result.revision_count)
    repair_record: dict[str, Any] = {
        "attempted": False,
        "outcome": "not_applicable",
        "repaired_draft": None,
        "original_local_rejection_reason": None,
    }
    context = case["production_context"]
    if (
        result.reply is not None
        and context.get("clarification_request") is not None
        and result.reply.draft_record.get("mode") != "direct_factual_answer"
    ):
        repair = tested_pipeline.repair_approved_direct_answer(
            approved_reply=result.reply,
            context=copy.deepcopy(context),
            config=copy.deepcopy(dict(config)),
            repository=repository,
            transport=transport,
            maximum_reply_length=MAX_REPLY_LENGTH,
            recent_replies=copy.deepcopy(case["recent_replies"]),
            media_context=None,
        )
        repair_record = {
            "attempted": repair.attempted,
            "outcome": repair.outcome,
            "repaired_draft": repair.repaired_draft,
            "original_local_rejection_reason": (
                "clarification_not_direct_factual_answer"
            ),
        }
        effective_audit = (*effective_audit, *copy.deepcopy(repair.audit))
        model_calls += int(repair.additional_model_calls)
        if repair.reply is None:
            effective_reply = None
            effective_status = "local_rejection"
            effective_reason = str(repair.reason)
        else:
            effective_reply = repair.reply
            effective_status = "approved_for_publication"
            effective_reason = str(repair.reason)
            metadata = repair.reply.pipeline_metadata
            model_calls = int(metadata.get("model_call_count") or model_calls)
            revisions = int(metadata.get("revision_count") or revisions + 1)
    elif result.reply is not None:
        effective_status = "approved_for_publication"
        effective_reason = "local_validation_passed"
    return EffectivePipelineRun(
        pipeline_stage_status=str(result.status),
        pipeline_stage_reason=str(result.reason),
        effective_status=effective_status,
        effective_reason=effective_reason,
        initial_public_candidate=_first_writer_candidate(transport),
        final_public_candidate=(
            str(effective_reply) if effective_reply is not None else None
        ),
        baseline_reached_writer=any(
            event.get("stage") == "writer_v3_initial"
            for event in transport.call_events
        ),
        model_call_count=model_calls,
        revision_count=revisions,
        audit=effective_audit,
        direct_answer_repair=repair_record,
    )


def _routing_request_hashes(events: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Return ordered canonical hashes for shared routing/reviewer calls."""
    return [
        {
            "stage": str(event.get("stage") or ""),
            "request_hash": str(event.get("request_hash") or ""),
        }
        for event in events
        if any(
            str(event.get("stage") or "").startswith(prefix)
            for prefix in ROUTING_AND_REVIEW_PREFIXES
        )
    ]


def _route_result_identity(telemetry: Mapping[str, Any]) -> dict[str, Any]:
    """Return the routing-only telemetry fields that A and C must share."""
    keys = (
        "deterministic_suppressed",
        "deterministic_reason",
        "xai_gate_decision",
        "reply_necessity_outcome",
        "group_hostility_candidate",
        "group_hostility_outcome",
        "allegation_conspiracy_candidate",
        "allegation_conspiracy_outcome",
        "attribution_route",
        "attribution_reply_requirement",
        "authentication_outcome",
        "reply_requirement",
        "route_source",
    )
    return {key: copy.deepcopy(telemetry.get(key)) for key in keys}


def pipeline_arm_result(
    *,
    case: Mapping[str, Any],
    arm: str,
    run: EffectivePipelineRun,
    transport: PipelineTransport,
    trusted_facts: Sequence[Mapping[str, Any]],
    wall_latency_seconds: float,
) -> dict[str, Any]:
    """Build one complete private A or C result row."""
    events = copy.deepcopy(transport.call_events)
    telemetry = tested_pipeline.stage_telemetry(run.audit)
    route_identity = _route_result_identity(telemetry)
    final_hash = (
        sha256_bytes(run.final_public_candidate.encode("utf-8"))
        if run.final_public_candidate is not None
        else None
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case["case_id"],
        "source_kind": case["source_kind"],
        "arm": arm,
        "execution_status": "completed",
        "issue_transcript_sha256": case["issue_transcript_sha256"],
        "production_context_sha256": case["production_context_sha256"],
        "trusted_facts_sha256": sha256_value(list(trusted_facts)),
        "recent_replies_sha256": case["recent_replies_sha256"],
        "baseline_routing_outcome": route_identity,
        "routing_result_sha256": sha256_value(route_identity),
        "routing_request_hashes": _routing_request_hashes(events),
        "baseline_reached_writer": run.baseline_reached_writer,
        "pipeline_stage_status": run.pipeline_stage_status,
        "pipeline_stage_reason": run.pipeline_stage_reason,
        "final_status": run.effective_status,
        "final_reason": run.effective_reason,
        "initial_public_candidate": run.initial_public_candidate,
        "final_public_candidate": run.final_public_candidate,
        "starting_candidate_sha256": final_hash,
        "final_public_candidate_sha256": final_hash,
        "critic_type": None,
        "critic_result": None,
        "broad_substitution_flag": False,
        "narrow_gate_trigger": False,
        "repair_attempts": 0,
        "repair_outcome": None,
        "direct_answer_repair": copy.deepcopy(run.direct_answer_repair),
        "deterministic_validation_results": [
            copy.deepcopy(row)
            for row in run.audit
            if str(row.get("stage") or "").endswith("validation")
            or "duplicate" in str(row.get("stage") or "")
        ],
        "claim_audit_and_cleanup_results": [
            copy.deepcopy(row)
            for row in run.audit
            if "claim" in str(row.get("stage") or "")
        ],
        "schema_failures": [
            str(row.get("stage") or "unknown")
            for row in run.audit
            if row.get("schema_valid") is False
        ],
        "provider_failures": [
            str(event.get("provider_error"))
            for event in events
            if event.get("provider_error")
        ],
        "ambiguous_call_failures": [
            str(event.get("provider_error"))
            for event in events
            if "AmbiguousRequestError" in str(event.get("provider_error") or "")
        ],
        "model_call_count": run.model_call_count,
        "revision_count": run.revision_count,
        "requests": events,
        "usage_and_cost": _event_totals(events),
        "arm_latency_seconds": round(wall_latency_seconds, 6),
        "completed_at": utc_now(),
    }


def arm_error_result(
    *,
    case: Mapping[str, Any],
    arm: str,
    error: BaseException,
    events: Sequence[Mapping[str, Any]],
    wall_latency_seconds: float,
    incomplete: bool,
) -> dict[str, Any]:
    """Retain a failed or safely blocked arm without inventing an outcome."""
    copied_events = copy.deepcopy(list(events))
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case["case_id"],
        "source_kind": case["source_kind"],
        "arm": arm,
        "execution_status": "incomplete" if incomplete else "error",
        "issue_transcript_sha256": case["issue_transcript_sha256"],
        "production_context_sha256": case["production_context_sha256"],
        "trusted_facts_sha256": None,
        "recent_replies_sha256": case["recent_replies_sha256"],
        "baseline_routing_outcome": None,
        "routing_result_sha256": None,
        "routing_request_hashes": _routing_request_hashes(copied_events),
        "baseline_reached_writer": any(
            event.get("stage") == "writer_v3_initial" for event in copied_events
        ),
        "pipeline_stage_status": "incomplete" if incomplete else "error",
        "pipeline_stage_reason": f"{type(error).__name__}: {error}",
        "final_status": "incomplete" if incomplete else "error",
        "final_reason": f"{type(error).__name__}: {error}",
        "initial_public_candidate": None,
        "final_public_candidate": None,
        "starting_candidate_sha256": None,
        "final_public_candidate_sha256": None,
        "critic_type": None,
        "critic_result": None,
        "broad_substitution_flag": False,
        "narrow_gate_trigger": False,
        "repair_attempts": 0,
        "repair_outcome": None,
        "direct_answer_repair": None,
        "deterministic_validation_results": [],
        "claim_audit_and_cleanup_results": [],
        "schema_failures": [],
        "provider_failures": [f"{type(error).__name__}: {error}"],
        "ambiguous_call_failures": (
            [f"{type(error).__name__}: {error}"]
            if isinstance(error, AmbiguousRequestError)
            else []
        ),
        "model_call_count": len(copied_events),
        "revision_count": 0,
        "requests": copied_events,
        "usage_and_cost": _event_totals(copied_events),
        "arm_latency_seconds": round(wall_latency_seconds, 6),
        "completed_at": utc_now(),
    }


def extract_issue_state(
    *, case: Mapping[str, Any], client: CanonicalProviderClient
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Extract and validate one issue state with its raw-transcript binding."""
    started = time.monotonic()
    valid_ids = {
        str(turn["turn_id"]) for turn in case["issue_transcript"]
    }
    content, event = _structured_research_call(
        client=client,
        provider="xAI",
        stage="issue_state_extraction",
        model=ISSUE_MODEL,
        reasoning_effort=ISSUE_EFFORT,
        system_prompt=ISSUE_STATE_PROMPT,
        payload={
            "issue_transcript": copy.deepcopy(case["issue_transcript"]),
            "target_turn_id": case["target_turn_id"],
        },
        response_schema=ISSUE_STATE_SCHEMA,
        max_output_tokens=1_800,
    )
    state = validate_issue_state(content, valid_turn_ids=valid_ids)
    record = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case["case_id"],
        "issue_transcript_sha256": case["issue_transcript_sha256"],
        "target_turn_id": case["target_turn_id"],
        "result": state,
        "status": state["status"],
        "confidence": (
            state["live_issue"]["confidence"]
            if isinstance(state.get("live_issue"), dict)
            else None
        ),
        "source_turn_ids": copy.deepcopy(state["source_turn_ids"]),
        "rejected_answer_targets": copy.deepcopy(
            state["rejected_answer_targets"]
        ),
        "usable_for_writer": issue_state_usable(state),
        "schema_failure": None,
        "provider_failure": None,
        "requests": [copy.deepcopy(event)],
        "usage_and_cost": _event_totals([event]),
        "latency_seconds": round(time.monotonic() - started, 6),
        "completed_at": utc_now(),
    }
    return state, record


def issue_state_error_record(
    *,
    case: Mapping[str, Any],
    error: BaseException,
    events: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Retain one unusable extraction failure without synthesising a state."""
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case["case_id"],
        "issue_transcript_sha256": case["issue_transcript_sha256"],
        "target_turn_id": case["target_turn_id"],
        "result": None,
        "status": "schema_or_provider_failure",
        "confidence": None,
        "source_turn_ids": [],
        "rejected_answer_targets": [],
        "usable_for_writer": False,
        "schema_failure": (
            f"{type(error).__name__}: {error}"
            if isinstance(error, (ValueError, json.JSONDecodeError))
            else None
        ),
        "provider_failure": (
            f"{type(error).__name__}: {error}"
            if not isinstance(error, (ValueError, json.JSONDecodeError))
            else None
        ),
        "requests": copy.deepcopy(list(events)),
        "usage_and_cost": _event_totals(events),
        "latency_seconds": None,
        "completed_at": utc_now(),
    }


def run_answerhood_critic(
    *,
    case: Mapping[str, Any],
    candidate: str,
    critic_type: str,
    issue_state: Mapping[str, Any] | None,
    client: CanonicalProviderClient,
    stage_suffix: str = "initial",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the same critic with transcript-only or ledger-backed evidence."""
    if critic_type not in {"transcript_only", "ledger_backed"}:
        raise EvaluationError("critic type is invalid")
    if critic_type == "ledger_backed" and not issue_state_usable(issue_state):
        raise EvaluationError("ledger-backed critic requires a usable issue state")
    payload: dict[str, Any] = {
        "issue_transcript": copy.deepcopy(case["issue_transcript"]),
        "target_turn_id": case["target_turn_id"],
        "proposed_final_reply": candidate,
    }
    if critic_type == "ledger_backed":
        payload["issue_state"] = copy.deepcopy(dict(issue_state or {}))
    content, event = _structured_research_call(
        client=client,
        provider="xAI",
        stage=f"answerhood_{critic_type}_{stage_suffix}",
        model=CRITIC_MODEL,
        reasoning_effort=CRITIC_EFFORT,
        system_prompt=ANSWERHOOD_CRITIC_PROMPT,
        payload=payload,
        response_schema=ANSWERHOOD_SCHEMA,
        max_output_tokens=1_000,
    )
    valid_ids = {
        str(turn["turn_id"]) for turn in case["issue_transcript"]
    }
    critic = validate_answerhood_critic(content, valid_turn_ids=valid_ids)
    return critic, event


def _repair_deterministic_error(
    candidate: str,
    *,
    repository: EvidenceRepository,
    recent_replies: Sequence[str],
) -> str | None:
    """Apply the existing public validator with the repair's two-sentence limit."""
    proposal = {
        "proposed_reply": candidate,
        "exact_thatcher_wording_used": False,
        "exact_thatcher_wording": "",
    }
    return deterministic_reply_error(
        proposal,
        repository,
        recent_replies=list(recent_replies),
        maximum_reply_length=MAX_REPLY_LENGTH,
        maximum_sentences=2,
    )


def _call_alignment_claim_audit(
    *,
    case: Mapping[str, Any],
    candidate: str,
    reply_requirement: str | None,
    trusted_facts: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    client: CanonicalProviderClient,
    stage: str,
) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
    """Run the unchanged production risk detector and applicable narrow audit."""
    risk = tested_pipeline.detect_claim_risk(candidate, reply_requirement)
    if not risk["risky"]:
        return "pass", None, {"risk": risk, "provider_call": False}
    content, event = _structured_research_call(
        client=client,
        provider="xAI",
        stage=stage,
        model=PRODUCTION_XAI_MODEL,
        reasoning_effort=PRODUCTION_XAI_EFFORT,
        system_prompt=tested_pipeline.CLAIM_AUDIT_PROMPT,
        payload={
            "context": copy.deepcopy(case["production_context"]),
            "trusted_facts": copy.deepcopy(list(trusted_facts)),
            "media_context": [],
            "reply_requirement": reply_requirement,
            "candidate_reply": {
                "label": "untrusted proposed output",
                "text": candidate,
            },
            "risk_categories": copy.deepcopy(risk["categories"]),
            "matched_text": copy.deepcopy(risk["matched_text"]),
        },
        response_schema=tested_pipeline.CLAIM_AUDIT_SCHEMA,
        max_output_tokens=int(config["claim_audit_max_output_tokens"]),
        timeout_seconds=int(config["timeout_seconds"]),
    )
    outcome = tested_pipeline._validate_enum(
        content, tested_pipeline.CLAIM_AUDIT_SCHEMA, "alignment repair claim audit"
    )
    return outcome, event, {"risk": risk, "provider_call": True, "outcome": outcome}


def perform_alignment_repair(
    *,
    arm: str,
    case: Mapping[str, Any],
    original_candidate: str,
    critic: Mapping[str, Any],
    critic_type: str,
    issue_state: Mapping[str, Any] | None,
    trusted_facts: Sequence[Mapping[str, Any]],
    reply_requirement: str | None,
    config: Mapping[str, Any],
    repository: EvidenceRepository,
    client: CanonicalProviderClient,
) -> dict[str, Any]:
    """Attempt exactly one alignment rewrite and fail closed on any unsafe result."""
    if arm not in {"B", "D"}:
        raise EvaluationError("only gated arms may perform alignment repair")
    valid_ids = {
        str(turn["turn_id"]) for turn in case["issue_transcript"]
    }
    if not narrow_gate_trigger(critic, valid_turn_ids=valid_ids):
        raise EvaluationError("alignment repair lacks the exact narrow trigger")
    events: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    payload: dict[str, Any] = {
        "issue_transcript": copy.deepcopy(case["issue_transcript"]),
        "production_context": copy.deepcopy(case["production_context"]),
        "trusted_facts": copy.deepcopy(list(trusted_facts)),
        "recent_replies": copy.deepcopy(case["recent_replies"]),
        "original_candidate": original_candidate,
        "critic_certificate": copy.deepcopy(dict(critic)),
    }
    if arm == "D":
        if not issue_state_usable(issue_state):
            raise EvaluationError("arm D repair requires a usable issue state")
        payload["issue_state"] = copy.deepcopy(dict(issue_state or {}))
    try:
        content, event = _structured_research_call(
            client=client,
            provider="OpenAI",
            stage=f"alignment_repair_{arm}",
            model=REPAIR_MODEL,
            reasoning_effort=REPAIR_EFFORT,
            system_prompt=ALIGNMENT_REPAIR_PROMPT,
            payload=payload,
            response_schema=tested_pipeline.WRITER_SCHEMA,
            max_output_tokens=int(config["writer_max_output_tokens"]),
            timeout_seconds=int(config["timeout_seconds"]),
        )
        events.append(event)
        writer = tested_pipeline._validate_writer(content)
        if writer["status"] != "reply":
            raise ValueError("alignment writer returned cannot_compose_safely")
        candidate = writer["reply"]
        rejection = _repair_deterministic_error(
            candidate,
            repository=repository,
            recent_replies=case["recent_replies"],
        )
        checks.append({"stage": "alignment_repair_validation", "rejection": rejection})
        if rejection:
            raise ValueError(f"deterministic rejection: {rejection}")
        outcome, audit_event, claim_record = _call_alignment_claim_audit(
            case=case,
            candidate=candidate,
            reply_requirement=reply_requirement,
            trusted_facts=trusted_facts,
            config=config,
            client=client,
            stage=f"alignment_repair_claim_audit_{arm}",
        )
        checks.append({"stage": "alignment_repair_claim_audit", **claim_record})
        if audit_event is not None:
            events.append(audit_event)
        cleanup_record: dict[str, Any] | None = None
        if outcome in {"rewrite_claim_free", "rewrite_supported_factual"}:
            cleanup_payload: dict[str, Any] = {
                "context": copy.deepcopy(case["production_context"]),
                "recent_replies": copy.deepcopy(case["recent_replies"]),
                "trusted_facts": copy.deepcopy(list(trusted_facts)),
                "media_context": [],
                "reply_requirement": reply_requirement,
                "audit_outcome": outcome,
                "candidate_reply_untrusted": candidate,
            }
            cleanup_prompt = tested_pipeline.CLAIM_CLEANUP_PROMPT
            if arm == "D":
                cleanup_prompt += "\n\n" + ISSUE_STATE_WRITER_INSTRUCTION
                cleanup_payload["issue_state"] = copy.deepcopy(dict(issue_state or {}))
            cleanup_content, cleanup_event = _structured_research_call(
                client=client,
                provider="OpenAI",
                stage=f"alignment_repair_claim_cleanup_{arm}",
                model=PRODUCTION_OPENAI_MODEL,
                reasoning_effort=PRODUCTION_OPENAI_EFFORT,
                system_prompt=cleanup_prompt,
                payload=cleanup_payload,
                response_schema=tested_pipeline.WRITER_SCHEMA,
                max_output_tokens=int(config["cleanup_max_output_tokens"]),
                timeout_seconds=int(config["timeout_seconds"]),
            )
            events.append(cleanup_event)
            cleanup = tested_pipeline._validate_writer(cleanup_content)
            if cleanup["status"] != "reply":
                raise ValueError("claim cleanup returned cannot_compose_safely")
            candidate = cleanup["reply"]
            rejection = _repair_deterministic_error(
                candidate,
                repository=repository,
                recent_replies=case["recent_replies"],
            )
            cleanup_record = {
                "attempted": True,
                "candidate": candidate,
                "deterministic_rejection": rejection,
            }
            checks.append({"stage": "alignment_cleanup_validation", "rejection": rejection})
            if rejection:
                raise ValueError(f"cleanup deterministic rejection: {rejection}")
            outcome, reaudit_event, reaudit_record = _call_alignment_claim_audit(
                case=case,
                candidate=candidate,
                reply_requirement=reply_requirement,
                trusted_facts=trusted_facts,
                config=config,
                client=client,
                stage=f"alignment_repair_cleanup_claim_audit_{arm}",
            )
            checks.append({"stage": "alignment_cleanup_claim_audit", **reaudit_record})
            if reaudit_event is not None:
                events.append(reaudit_event)
        if outcome != "pass":
            raise ValueError(f"factual grounding did not pass: {outcome}")
        post_critic, post_event = run_answerhood_critic(
            case=case,
            candidate=candidate,
            critic_type=critic_type,
            issue_state=issue_state,
            client=client,
            stage_suffix="post_repair",
        )
        events.append(post_event)
        revival_remains = narrow_gate_trigger(post_critic, valid_turn_ids=valid_ids)
        checks.append(
            {
                "stage": "post_repair_answerhood",
                "narrow_trigger_remains": revival_remains,
            }
        )
        if revival_remains:
            raise ValueError("repair remains a high-confidence rejected-target revival")
        return {
            "success": True,
            "outcome": "alignment_repair_succeeded",
            "final_public_candidate": candidate,
            "repair_attempts": 1,
            "claim_cleanup": cleanup_record,
            "post_repair_critic": post_critic,
            "checks": checks,
            "requests": events,
            "failure": None,
        }
    except (CostLimitReached, AmbiguousRequestError):
        raise
    except BaseException as exc:
        return {
            "success": False,
            "outcome": "alignment_repair_failed",
            "final_public_candidate": None,
            "repair_attempts": 1,
            "claim_cleanup": None,
            "post_repair_critic": None,
            "checks": checks,
            "requests": events,
            "failure": f"{type(exc).__name__}: {exc}",
        }


def critic_arm_result(
    *,
    case: Mapping[str, Any],
    arm: str,
    starting_row: Mapping[str, Any],
    critic_type: str,
    critic: Mapping[str, Any],
    critic_event: Mapping[str, Any],
    repair: Mapping[str, Any] | None,
    wall_latency_seconds: float,
) -> dict[str, Any]:
    """Build a B or D result while preserving the exact starting candidate."""
    starting = starting_row.get("final_public_candidate")
    if not isinstance(starting, str) or not starting:
        raise EvaluationError("critic arm has no valid starting public candidate")
    valid_ids = {str(turn["turn_id"]) for turn in case["issue_transcript"]}
    broad = broad_substitution_flag(critic)
    narrow = narrow_gate_trigger(critic, valid_turn_ids=valid_ids)
    events = [copy.deepcopy(dict(critic_event))]
    final = starting
    final_reason = "critic_recorded_no_intervention"
    repair_attempts = 0
    repair_outcome = None
    checks: list[dict[str, Any]] = []
    if repair is not None:
        if not narrow:
            raise EvaluationError("repair exists without the exact narrow trigger")
        events.extend(copy.deepcopy(repair.get("requests") or []))
        final = repair.get("final_public_candidate")
        final_reason = str(repair.get("outcome") or "alignment_repair_failed")
        repair_attempts = int(repair.get("repair_attempts") or 0)
        repair_outcome = str(repair.get("outcome") or "")
        checks = copy.deepcopy(repair.get("checks") or [])
    if repair_attempts > 1:
        raise EvaluationError("more than one alignment rewrite was attempted")
    final_hash = sha256_bytes(final.encode("utf-8")) if isinstance(final, str) else None
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case["case_id"],
        "source_kind": case["source_kind"],
        "arm": arm,
        "execution_status": "completed",
        "issue_transcript_sha256": case["issue_transcript_sha256"],
        "production_context_sha256": case["production_context_sha256"],
        "trusted_facts_sha256": starting_row.get("trusted_facts_sha256"),
        "recent_replies_sha256": case["recent_replies_sha256"],
        "baseline_routing_outcome": copy.deepcopy(starting_row.get("baseline_routing_outcome")),
        "routing_result_sha256": starting_row.get("routing_result_sha256"),
        "routing_request_hashes": copy.deepcopy(starting_row.get("routing_request_hashes") or []),
        "baseline_reached_writer": True,
        "pipeline_stage_status": starting_row.get("pipeline_stage_status"),
        "pipeline_stage_reason": starting_row.get("pipeline_stage_reason"),
        "final_status": (
            "approved_for_publication" if isinstance(final, str) else "no_reply"
        ),
        "final_reason": final_reason,
        "initial_public_candidate": starting,
        "final_public_candidate": final,
        "starting_candidate_sha256": sha256_bytes(starting.encode("utf-8")),
        "final_public_candidate_sha256": final_hash,
        "critic_type": critic_type,
        "critic_result": copy.deepcopy(dict(critic)),
        "broad_substitution_flag": broad,
        "narrow_gate_trigger": narrow,
        "repair_attempts": repair_attempts,
        "repair_outcome": repair_outcome,
        "repair_details": copy.deepcopy(dict(repair or {})),
        "direct_answer_repair": copy.deepcopy(starting_row.get("direct_answer_repair")),
        "deterministic_validation_results": checks,
        "claim_audit_and_cleanup_results": [
            row for row in checks if "claim" in str(row.get("stage") or "")
        ],
        "schema_failures": [],
        "provider_failures": [
            str(event.get("provider_error"))
            for event in events
            if event.get("provider_error")
        ],
        "ambiguous_call_failures": [
            str(event.get("provider_error"))
            for event in events
            if "AmbiguousRequestError" in str(event.get("provider_error") or "")
        ],
        "model_call_count": len(events),
        "revision_count": repair_attempts,
        "requests": events,
        "usage_and_cost": _event_totals(events),
        "arm_latency_seconds": round(wall_latency_seconds, 6),
        "completed_at": utc_now(),
    }


def reuse_arm_result(
    *, case: Mapping[str, Any], arm: str, source: Mapping[str, Any]
) -> dict[str, Any]:
    """Reuse an exact public outcome when unusable issue state forbids resampling."""
    row = copy.deepcopy(dict(source))
    row["arm"] = arm
    row["reused_exact_outcome_from_arm"] = source["arm"]
    row["requests"] = []
    row["usage_and_cost"] = _event_totals([])
    row["model_call_count"] = 0
    row["arm_latency_seconds"] = 0.0
    row["completed_at"] = utc_now()
    if row.get("final_public_candidate") != source.get("final_public_candidate"):
        raise EvaluationError("reused arm outcome changed")
    return row


def assert_arm_invariants(
    *,
    arm_a: Mapping[str, Any],
    arm_b: Mapping[str, Any],
    arm_c: Mapping[str, Any],
    arm_d: Mapping[str, Any],
) -> None:
    """Assert the declared input, routing, and pre-gate candidate equalities."""
    input_fields = (
        "issue_transcript_sha256",
        "production_context_sha256",
        "trusted_facts_sha256",
        "recent_replies_sha256",
    )
    for field in input_fields:
        if len({row.get(field) for row in (arm_a, arm_b, arm_c, arm_d)}) != 1:
            raise EvaluationError(f"arm input hash differs: {field}")
    if arm_a.get("routing_result_sha256") != arm_c.get("routing_result_sha256"):
        raise EvaluationError("Arm A and C routing results differ")
    if arm_a.get("routing_request_hashes") != arm_c.get("routing_request_hashes"):
        raise EvaluationError("Arm A and C routing/reviewer requests differ")
    a_candidate = arm_a.get("final_public_candidate")
    c_candidate = arm_c.get("final_public_candidate")
    if arm_b.get("starting_candidate_sha256") != (
        sha256_bytes(a_candidate.encode("utf-8")) if isinstance(a_candidate, str) else None
    ):
        raise EvaluationError("Arm A and B do not share the exact baseline candidate")
    if arm_d.get("starting_candidate_sha256") != (
        sha256_bytes(c_candidate.encode("utf-8")) if isinstance(c_candidate, str) else None
    ):
        raise EvaluationError("Arm C and D do not share the exact issue-aware candidate")


def fetch_openai_model_availability(
    *, api_key: str, get: Callable[..., Any] = requests.get
) -> dict[str, Any]:
    """Verify GPT-5.6 Sol through the authenticated OpenAI model endpoint."""
    if not api_key:
        raise EvaluationError("OPENAI_API_KEY is required for live execution")
    url = f"{OPENAI_BASE_URL}/models/{PRODUCTION_OPENAI_MODEL}"
    validate_provider_url(url, expected_host=OPENAI_HOST)
    try:
        response = get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
            allow_redirects=False,
        )
    except BaseException as exc:
        raise ModelAvailabilityError(
            f"OpenAI model endpoint failed: {type(exc).__name__}: {exc}"
        ) from exc
    status = int(getattr(response, "status_code", 0) or 0)
    if status != 200:
        raise ModelAvailabilityError(
            f"OpenAI model endpoint returned HTTP {status}"
        )
    try:
        document = response.json()
    except BaseException as exc:
        raise ModelAvailabilityError("OpenAI model endpoint returned invalid JSON") from exc
    if not isinstance(document, dict) or document.get("id") != PRODUCTION_OPENAI_MODEL:
        raise ModelAvailabilityError("authenticated GPT-5.6 Sol availability failed")
    return {"id": PRODUCTION_OPENAI_MODEL, "retrieved_at": utc_now()}


def verify_model_availability(
    *,
    api_keys: Mapping[str, str],
    get: Callable[..., Any] = requests.get,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Verify all three fixed model IDs before an inference request."""
    if not api_keys.get("xAI") or not api_keys.get("OpenAI"):
        raise EvaluationError("both provider API keys are required for live execution")
    xai = base_harness.fetch_xai_model_metadata(
        api_key=api_keys["xAI"], get=get
    )
    if set(xai) != {PRODUCTION_XAI_MODEL, ISSUE_MODEL}:
        raise ModelAvailabilityError("authenticated xAI model set is incomplete")
    openai = fetch_openai_model_availability(
        api_key=api_keys["OpenAI"], get=get
    )
    return {
        "checked_at": utc_now(),
        "endpoint_call_count": 2,
        "xAI": sorted(xai),
        "OpenAI": [openai["id"]],
        "all_required_available": True,
    }, xai


def load_api_keys(env_file: Path | None = None) -> dict[str, str]:
    """Reuse the existing local provider-credential convention."""
    return base_harness.load_api_keys(env_file)


def load_arm_results(path: Path) -> list[dict[str, Any]]:
    """Load unique resumable case/arm result rows."""
    if not path.exists():
        return []
    rows = read_jsonl(path)
    identities = [(str(row.get("case_id")), str(row.get("arm"))) for row in rows]
    if len(identities) != len(set(identities)):
        raise EvaluationError("arm result identities are duplicated")
    if any(arm not in ARMS for _case_id, arm in identities):
        raise EvaluationError("arm results contain an unknown arm")
    return rows


def load_issue_state_records(path: Path) -> list[dict[str, Any]]:
    """Load unique resumable issue-state records."""
    if not path.exists():
        return []
    rows = read_jsonl(path)
    identities = [str(row.get("case_id") or "") for row in rows]
    if not all(identities) or len(identities) != len(set(identities)):
        raise EvaluationError("issue-state record identities are invalid")
    return rows


def _result_sort_key(
    row: Mapping[str, Any], cases: Sequence[Mapping[str, Any]]
) -> tuple[int, int]:
    """Return stable candidate and arm ordering for durable results."""
    case_order = {str(case["case_id"]): index for index, case in enumerate(cases)}
    return case_order[str(row["case_id"])], list(ARMS).index(str(row["arm"]))


def _persist_results(
    path: Path,
    rows: list[dict[str, Any]],
    cases: Sequence[Mapping[str, Any]],
) -> None:
    """Sort and atomically persist resumable arm results."""
    rows.sort(key=lambda row: _result_sort_key(row, cases))
    atomic_jsonl(path, rows)


def _paid_case_selection(
    cases: Sequence[dict[str, Any]], results: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Select up to 60 ordered baselines that reached a valid public writer outcome."""
    arm_a = {
        str(row["case_id"]): row
        for row in results
        if row.get("arm") == "A" and row.get("execution_status") == "completed"
    }
    return [
        case
        for case in cases
        if (
            case["case_id"] in arm_a
            and arm_a[case["case_id"]].get("baseline_reached_writer") is True
            and isinstance(arm_a[case["case_id"]].get("final_public_candidate"), str)
            and arm_a[case["case_id"]].get("final_public_candidate")
        )
    ][:MAX_COMPARISON_CASES]


def _set_paid_case_set(
    *, output_dir: Path, cases: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Freeze or validate the post-baseline paid comparison selection."""
    path = private_path(output_dir, "manifest.json")
    manifest = read_json(path)
    if not isinstance(manifest, dict):
        raise EvaluationError("manifest is invalid")
    ids = [str(case["case_id"]) for case in cases]
    digest = sha256_value(ids)
    prior = manifest.get("paid_comparison")
    value = {
        "case_ids": ids,
        "case_count": len(ids),
        "case_set_sha256": digest,
        "maximum_cases": MAX_COMPARISON_CASES,
    }
    if prior is not None and prior != value:
        raise EvaluationError("paid comparison case set changed on resume")
    manifest["paid_comparison"] = value
    manifest["updated_at"] = utc_now()
    atomic_json(path, manifest)
    return manifest


def _applied_research_commits(project_dir: Path) -> list[dict[str, str]]:
    """Resolve the two cherry-picked research commits by exact subject."""
    process = subprocess.run(
        [
            "git",
            "log",
            "--format=%H%x00%s",
            "origin/master..HEAD",
        ],
        cwd=project_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    by_subject: dict[str, str] = {}
    for line in process.stdout.splitlines():
        if "\0" not in line:
            continue
        commit, subject = line.split("\0", 1)
        by_subject.setdefault(subject, commit)
    result: list[dict[str, str]] = []
    for source_sha, subject in RESEARCH_SOURCE_COMMITS:
        applied = by_subject.get(subject)
        if not applied or not re.fullmatch(r"[0-9a-f]{40}", applied):
            raise EvaluationError(f"required cherry-picked research commit is absent: {subject}")
        result.append(
            {
                "source_commit_sha": source_sha,
                "applied_commit_sha": applied,
                "subject": subject,
            }
        )
    return result


def create_or_load_arm_key(path: Path) -> dict[str, str]:
    """Create once or validate the sole private A-D to W-Z mapping."""
    if path.exists():
        value = read_json(path)
        if (
            not isinstance(value, dict)
            or set(value) != set(ARMS)
            or set(value.values()) != set(BLIND_LABELS)
            or not all(type(label) is str for label in value.values())
        ):
            raise EvaluationError("private blind arm key is invalid")
        return dict(value)
    labels = list(BLIND_LABELS)
    secrets.SystemRandom().shuffle(labels)
    value = dict(zip(ARMS, labels))
    atomic_json(path, value)
    return value


def _immutable_manifest_fields(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return fields that cannot change during safe resumption."""
    mutable = {
        "created_at",
        "updated_at",
        "paid_comparison",
        "provider_model_availability",
        "execution",
    }
    return {
        key: copy.deepcopy(value)
        for key, value in manifest.items()
        if key not in mutable
    }


def prepare_experiment(
    *, project_dir: Path, output_dir: Path, review_pack: Path
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, str]]]:
    """Freeze and hash every candidate before any provider request."""
    assert_production_sources_clean(project_dir)
    config = production_config()
    actual_master = git_revision(project_dir, "origin/master")
    pack_identity, cases, skips, inventory = build_candidate_pool(
        review_pack=review_pack
    )
    candidate_pool_sha256 = sha256_value(cases)
    evidence_identity = base_harness.repository_identity(project_dir, config)
    counts = {
        **inventory,
        "prospective_candidate_occurrences": sum(
            occurrence.get("source_kind") == "prospective"
            for case in cases
            for occurrence in case.get("source_occurrences") or []
        ),
        "prior_evaluation_candidate_occurrences": sum(
            occurrence.get("source_kind") == "prior_evaluation"
            for case in cases
            for occurrence in case.get("source_occurrences") or []
        ),
        "selected_prospective_cases": sum(
            case.get("source_kind") == "prospective" for case in cases
        ),
        "selected_prior_evaluation_cases": sum(
            case.get("source_kind") == "prior_evaluation" for case in cases
        ),
        "skip_reason_counts": dict(
            sorted(Counter(row["reason_code"] for row in skips).items())
        ),
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_version": RUN_VERSION,
        "created_at": utc_now(),
        "source_master_sha": actual_master,
        "prompt_written_master_sha": SOURCE_MASTER_SHA,
        "cherry_picked_research_commits": _applied_research_commits(project_dir),
        "project_dir": str(project_dir),
        "output_dir": str(output_dir),
        "frozen_review_pack": copy.deepcopy(pack_identity),
        "frozen_review_pack_manifest_sha256": pack_identity["manifest_sha256"],
        "candidate_pool_sha256": candidate_pool_sha256,
        "candidate_counts": counts,
        "paid_comparison": None,
        "tool_sha256": sha256_file(Path(__file__).resolve()),
        "issue_state_prompt_sha256": sha256_bytes(ISSUE_STATE_PROMPT.encode("utf-8")),
        "issue_state_schema_sha256": sha256_value(ISSUE_STATE_SCHEMA),
        "critic_prompt_sha256": sha256_bytes(ANSWERHOOD_CRITIC_PROMPT.encode("utf-8")),
        "critic_schema_sha256": sha256_value(ANSWERHOOD_SCHEMA),
        "repair_prompt_sha256": sha256_bytes(ALIGNMENT_REPAIR_PROMPT.encode("utf-8")),
        "writer_injection_sha256": sha256_bytes(
            ISSUE_STATE_WRITER_INSTRUCTION.encode("utf-8")
        ),
        "arm_definitions": copy.deepcopy(ARMS),
        "effective_provider_models_and_efforts": {
            "ordinary_xAI": {
                "model": PRODUCTION_XAI_MODEL,
                "reasoning_effort": PRODUCTION_XAI_EFFORT,
            },
            "ordinary_OpenAI": {
                "model": PRODUCTION_OPENAI_MODEL,
                "reasoning_effort": PRODUCTION_OPENAI_EFFORT,
            },
            "issue_state": {"model": ISSUE_MODEL, "reasoning_effort": ISSUE_EFFORT},
            "answerhood_critics": {
                "model": CRITIC_MODEL,
                "reasoning_effort": CRITIC_EFFORT,
            },
            "alignment_repair": {
                "model": REPAIR_MODEL,
                "reasoning_effort": REPAIR_EFFORT,
            },
        },
        "logical_production_config": copy.deepcopy(config),
        "evidence_corpus": evidence_identity,
        "network_allowlist": sorted(NETWORK_ALLOWLIST),
        "provider_tools_enabled": False,
        "posting_enabled": False,
        "x_client_imported": False,
        "text_only": True,
        "maximum_candidate_cases": MAX_CANDIDATE_CASES,
        "maximum_paid_comparison_cases": MAX_COMPARISON_CASES,
        "global_cost_ceiling_usd": GLOBAL_COST_CEILING_USD,
        "provider_model_availability": {
            "checked": False,
            "all_required_available": None,
        },
        "execution": {
            "mode": "prepared_only",
            "blocker": None,
            "cost": None,
            "request_count": 0,
        },
    }
    manifest_path = private_path(output_dir, "manifest.json")
    if manifest_path.exists():
        existing = read_json(manifest_path)
        if not isinstance(existing, dict):
            raise EvaluationError("existing manifest is invalid")
        comparable = copy.deepcopy(manifest)
        for field in (
            "created_at",
            "updated_at",
            "paid_comparison",
            "provider_model_availability",
            "execution",
        ):
            comparable[field] = existing.get(field)
        if _immutable_manifest_fields(existing) != _immutable_manifest_fields(comparable):
            raise EvaluationError("immutable experiment inputs changed on resume")
        manifest = existing
    else:
        atomic_json(manifest_path, manifest)

    cases_path = private_path(output_dir, "cases.jsonl")
    case_bytes = b"".join(
        canonical_json_bytes(case, newline=True) for case in cases
    )
    if cases_path.exists() and cases_path.read_bytes() != case_bytes:
        raise EvaluationError("frozen candidate pool changed on resume")
    if not cases_path.exists():
        atomic_bytes(cases_path, case_bytes)
    unusable_path = private_path(output_dir, "unusable_cases.jsonl")
    skip_bytes = b"".join(
        canonical_json_bytes(row, newline=True) for row in skips
    )
    if unusable_path.exists() and unusable_path.read_bytes() != skip_bytes:
        raise EvaluationError("unusable-case records changed on resume")
    if not unusable_path.exists():
        atomic_bytes(unusable_path, skip_bytes)
    for name in ("issue_states.jsonl", "arm_results.jsonl"):
        path = private_path(output_dir, name)
        if not path.exists():
            atomic_bytes(path, b"")
    response_dir = private_path(output_dir, "responses")
    os.makedirs(response_dir, mode=0o700, exist_ok=True)
    os.chmod(response_dir, 0o700)
    create_or_load_arm_key(private_path(output_dir, "arm_key.private.json"))
    QudRequestLedger(
        private_path(output_dir, "request_ledger.json"),
        case_set_sha256=candidate_pool_sha256,
        hard_limit_usd=GLOBAL_COST_CEILING_USD,
    )
    return manifest, cases, skips


def update_manifest_execution(
    output_dir: Path,
    *,
    mode: str,
    blocker: str | None,
    ledger: QudRequestLedger,
    availability: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Update only mutable execution, cost, and availability metadata."""
    path = private_path(output_dir, "manifest.json")
    manifest = read_json(path)
    if not isinstance(manifest, dict):
        raise EvaluationError("manifest is invalid")
    operations = [
        row for row in ledger.data.get("operations", []) if isinstance(row, dict)
    ]
    if availability is not None:
        manifest["provider_model_availability"] = copy.deepcopy(dict(availability))
    manifest["execution"] = {
        "mode": mode,
        "blocker": blocker,
        "cost": ledger.cost_summary(),
        "request_count": len(operations),
        "completed_request_count": sum(
            row.get("status") == "completed" for row in operations
        ),
        "ambiguous_request_count": sum(
            row.get("status") == "ambiguous" for row in operations
        ),
        "definite_provider_failure_count": sum(
            row.get("status") in {"http_error", "rate_limited", "server_error"}
            for row in operations
        ),
        "cache_consumer_count": sum(
            max(0, len(row.get("consumers") or []) - 1) for row in operations
        ),
    }
    manifest["updated_at"] = utc_now()
    atomic_json(path, manifest)
    return manifest


def critic_failure_arm_result(
    *,
    case: Mapping[str, Any],
    arm: str,
    starting_row: Mapping[str, Any],
    critic_type: str,
    error: BaseException,
    events: Sequence[Mapping[str, Any]],
    wall_latency_seconds: float,
) -> dict[str, Any]:
    """Keep the starting public result when a non-triggering critic is invalid."""
    row = reuse_arm_result(case=case, arm=arm, source=starting_row)
    row.update(
        {
            "critic_type": critic_type,
            "critic_result": None,
            "final_reason": "critic_unavailable_no_intervention",
            "broad_substitution_flag": False,
            "narrow_gate_trigger": False,
            "requests": copy.deepcopy(list(events)),
            "usage_and_cost": _event_totals(events),
            "arm_latency_seconds": round(wall_latency_seconds, 6),
            "schema_failures": (
                [f"{type(error).__name__}: {error}"]
                if isinstance(error, (ValueError, json.JSONDecodeError))
                else []
            ),
            "provider_failures": (
                [f"{type(error).__name__}: {error}"]
                if not isinstance(error, (ValueError, json.JSONDecodeError))
                else []
            ),
        }
    )
    return row


def _trusted_facts_for_case(
    *,
    case: Mapping[str, Any],
    repository: EvidenceRepository,
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Build the exact deterministic trusted-facts packet for one case."""
    return tested_pipeline.build_trusted_facts(
        copy.deepcopy(case["production_context"]), repository, dict(config)
    )


def execute_experiment(
    *,
    execute_live_models: bool,
    project_dir: Path,
    output_dir: Path,
    manifest: Mapping[str, Any],
    cases: Sequence[dict[str, Any]],
    api_keys: Mapping[str, str],
    hard_limit_usd: float = GLOBAL_COST_CEILING_USD,
    get: Callable[..., Any] = requests.get,
    post: Callable[..., Any] = requests.post,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    QudRequestLedger,
    str | None,
    dict[str, Any] | None,
]:
    """Run baseline first, freeze paid cases, then execute B/C/D per case."""
    if not execute_live_models:
        raise EvaluationError("--execute-live-models is required before provider work")
    assert_production_sources_clean(project_dir)
    config = production_config()
    if git_revision(project_dir, "origin/master") != manifest.get("source_master_sha"):
        raise EvaluationError("origin/master changed after candidate-pool preparation")
    availability, xai_metadata = verify_model_availability(
        api_keys=api_keys, get=get
    )
    ledger = QudRequestLedger(
        private_path(output_dir, "request_ledger.json"),
        case_set_sha256=str(manifest["candidate_pool_sha256"]),
        hard_limit_usd=hard_limit_usd,
    )
    repository = EvidenceRepository(
        project_dir / str(config["research_corpus_path"]),
        factual_evidence_path=project_dir / "reply_factual_evidence.json",
    )
    response_dir = private_path(output_dir, "responses")
    results_path = private_path(output_dir, "arm_results.jsonl")
    issue_path = private_path(output_dir, "issue_states.jsonl")
    results = load_arm_results(results_path)
    result_map = {
        (str(row["case_id"]), str(row["arm"])): row for row in results
    }
    issue_rows = load_issue_state_records(issue_path)
    issue_map = {str(row["case_id"]): row for row in issue_rows}
    blocker: str | None = None

    for case in cases:
        identity = (str(case["case_id"]), "A")
        if identity in result_map:
            continue
        client = CanonicalProviderClient(
            arm="A",
            case_id=str(case["case_id"]),
            ledger=ledger,
            response_dir=response_dir,
            api_keys=api_keys,
            xai_model_metadata=xai_metadata,
            post=post,
            sleep=sleep,
        )
        transport = PipelineTransport(client=client)
        started = time.monotonic()
        try:
            trusted_facts = _trusted_facts_for_case(
                case=case, repository=repository, config=config
            )
            run = run_current_public_pipeline(
                case=case,
                config=config,
                repository=repository,
                transport=transport,
            )
            row = pipeline_arm_result(
                case=case,
                arm="A",
                run=run,
                transport=transport,
                trusted_facts=trusted_facts,
                wall_latency_seconds=time.monotonic() - started,
            )
        except (CostLimitReached, AmbiguousRequestError) as exc:
            blocker = f"{type(exc).__name__}: {exc}"
            row = arm_error_result(
                case=case,
                arm="A",
                error=exc,
                events=transport.call_events,
                wall_latency_seconds=time.monotonic() - started,
                incomplete=True,
            )
        except (DefiniteProviderError, TransientProviderError) as exc:
            blocker = f"{type(exc).__name__}: {exc}"
            row = arm_error_result(
                case=case,
                arm="A",
                error=exc,
                events=transport.call_events,
                wall_latency_seconds=time.monotonic() - started,
                incomplete=False,
            )
        except BaseException as exc:
            row = arm_error_result(
                case=case,
                arm="A",
                error=exc,
                events=transport.call_events,
                wall_latency_seconds=time.monotonic() - started,
                incomplete=False,
            )
        results.append(row)
        result_map[identity] = row
        _persist_results(results_path, results, cases)
        if blocker:
            return results, issue_rows, ledger, blocker, availability

    paid_cases = _paid_case_selection(cases, results)
    manifest = _set_paid_case_set(output_dir=output_dir, cases=paid_cases)
    for case in paid_cases:
        case_id = str(case["case_id"])
        arm_a = result_map[(case_id, "A")]
        trusted_facts = _trusted_facts_for_case(
            case=case, repository=repository, config=config
        )
        if sha256_value(trusted_facts) != arm_a.get("trusted_facts_sha256"):
            raise EvaluationError("trusted facts changed after baseline")

        issue_record = issue_map.get(case_id)
        if issue_record is None:
            issue_client = CanonicalProviderClient(
                arm="B",
                case_id=f"{case_id}:shared_issue",
                ledger=ledger,
                response_dir=response_dir,
                api_keys=api_keys,
                xai_model_metadata=xai_metadata,
                post=post,
                sleep=sleep,
            )
            try:
                _state, issue_record = extract_issue_state(
                    case=case, client=issue_client
                )
            except (CostLimitReached, AmbiguousRequestError) as exc:
                blocker = f"{type(exc).__name__}: {exc}"
                issue_record = issue_state_error_record(
                    case=case, error=exc, events=issue_client.call_events
                )
            except BaseException as exc:
                issue_record = issue_state_error_record(
                    case=case, error=exc, events=issue_client.call_events
                )
            issue_rows.append(issue_record)
            issue_rows.sort(
                key=lambda row: next(
                    index
                    for index, selected in enumerate(paid_cases)
                    if selected["case_id"] == row["case_id"]
                )
            )
            atomic_jsonl(issue_path, issue_rows)
            issue_map[case_id] = issue_record
            if blocker:
                return results, issue_rows, ledger, blocker, availability
        issue_state = issue_record.get("result")
        usable = issue_state_usable(issue_state)

        if (case_id, "B") not in result_map:
            client_b = CanonicalProviderClient(
                arm="B",
                case_id=case_id,
                ledger=ledger,
                response_dir=response_dir,
                api_keys=api_keys,
                xai_model_metadata=xai_metadata,
                post=post,
                sleep=sleep,
            )
            started = time.monotonic()
            try:
                critic, critic_event = run_answerhood_critic(
                    case=case,
                    candidate=str(arm_a["final_public_candidate"]),
                    critic_type="transcript_only",
                    issue_state=None,
                    client=client_b,
                )
                repair = None
                if narrow_gate_trigger(
                    critic,
                    valid_turn_ids={
                        str(turn["turn_id"]) for turn in case["issue_transcript"]
                    },
                ):
                    repair = perform_alignment_repair(
                        arm="B",
                        case=case,
                        original_candidate=str(arm_a["final_public_candidate"]),
                        critic=critic,
                        critic_type="transcript_only",
                        issue_state=None,
                        trusted_facts=trusted_facts,
                        reply_requirement=(arm_a.get("baseline_routing_outcome") or {}).get("reply_requirement"),
                        config=config,
                        repository=repository,
                        client=client_b,
                    )
                row_b = critic_arm_result(
                    case=case,
                    arm="B",
                    starting_row=arm_a,
                    critic_type="transcript_only",
                    critic=critic,
                    critic_event=critic_event,
                    repair=repair,
                    wall_latency_seconds=time.monotonic() - started,
                )
            except (CostLimitReached, AmbiguousRequestError) as exc:
                blocker = f"{type(exc).__name__}: {exc}"
                row_b = arm_error_result(
                    case=case,
                    arm="B",
                    error=exc,
                    events=client_b.call_events,
                    wall_latency_seconds=time.monotonic() - started,
                    incomplete=True,
                )
            except BaseException as exc:
                row_b = critic_failure_arm_result(
                    case=case,
                    arm="B",
                    starting_row=arm_a,
                    critic_type="transcript_only",
                    error=exc,
                    events=client_b.call_events,
                    wall_latency_seconds=time.monotonic() - started,
                )
            results.append(row_b)
            result_map[(case_id, "B")] = row_b
            _persist_results(results_path, results, cases)
            if blocker:
                return results, issue_rows, ledger, blocker, availability

        if (case_id, "C") not in result_map:
            if not usable:
                row_c = reuse_arm_result(case=case, arm="C", source=arm_a)
            else:
                client_c = CanonicalProviderClient(
                    arm="C",
                    case_id=case_id,
                    ledger=ledger,
                    response_dir=response_dir,
                    api_keys=api_keys,
                    xai_model_metadata=xai_metadata,
                    post=post,
                    sleep=sleep,
                )
                transport_c = PipelineTransport(
                    client=client_c,
                    inject_issue_state=True,
                    issue_state=issue_state,
                )
                started = time.monotonic()
                try:
                    run_c = run_current_public_pipeline(
                        case=case,
                        config=config,
                        repository=repository,
                        transport=transport_c,
                    )
                    row_c = pipeline_arm_result(
                        case=case,
                        arm="C",
                        run=run_c,
                        transport=transport_c,
                        trusted_facts=trusted_facts,
                        wall_latency_seconds=time.monotonic() - started,
                    )
                    if (
                        row_c["routing_result_sha256"] != arm_a["routing_result_sha256"]
                        or row_c["routing_request_hashes"] != arm_a["routing_request_hashes"]
                    ):
                        raise EvaluationError("Arm A and C routing diverged")
                except (CostLimitReached, AmbiguousRequestError) as exc:
                    blocker = f"{type(exc).__name__}: {exc}"
                    row_c = arm_error_result(
                        case=case,
                        arm="C",
                        error=exc,
                        events=transport_c.call_events,
                        wall_latency_seconds=time.monotonic() - started,
                        incomplete=True,
                    )
                except BaseException as exc:
                    row_c = arm_error_result(
                        case=case,
                        arm="C",
                        error=exc,
                        events=transport_c.call_events,
                        wall_latency_seconds=time.monotonic() - started,
                        incomplete=False,
                    )
            results.append(row_c)
            result_map[(case_id, "C")] = row_c
            _persist_results(results_path, results, cases)
            if blocker:
                return results, issue_rows, ledger, blocker, availability
        arm_c = result_map[(case_id, "C")]

        if (case_id, "D") not in result_map:
            if not usable:
                row_d = reuse_arm_result(case=case, arm="D", source=arm_c)
            elif not isinstance(arm_c.get("final_public_candidate"), str):
                row_d = reuse_arm_result(case=case, arm="D", source=arm_c)
            else:
                client_d = CanonicalProviderClient(
                    arm="D",
                    case_id=case_id,
                    ledger=ledger,
                    response_dir=response_dir,
                    api_keys=api_keys,
                    xai_model_metadata=xai_metadata,
                    post=post,
                    sleep=sleep,
                )
                started = time.monotonic()
                try:
                    critic_d, critic_event_d = run_answerhood_critic(
                        case=case,
                        candidate=str(arm_c["final_public_candidate"]),
                        critic_type="ledger_backed",
                        issue_state=issue_state,
                        client=client_d,
                    )
                    repair_d = None
                    if narrow_gate_trigger(
                        critic_d,
                        valid_turn_ids={
                            str(turn["turn_id"]) for turn in case["issue_transcript"]
                        },
                    ):
                        repair_d = perform_alignment_repair(
                            arm="D",
                            case=case,
                            original_candidate=str(arm_c["final_public_candidate"]),
                            critic=critic_d,
                            critic_type="ledger_backed",
                            issue_state=issue_state,
                            trusted_facts=trusted_facts,
                            reply_requirement=(arm_c.get("baseline_routing_outcome") or {}).get("reply_requirement"),
                            config=config,
                            repository=repository,
                            client=client_d,
                        )
                    row_d = critic_arm_result(
                        case=case,
                        arm="D",
                        starting_row=arm_c,
                        critic_type="ledger_backed",
                        critic=critic_d,
                        critic_event=critic_event_d,
                        repair=repair_d,
                        wall_latency_seconds=time.monotonic() - started,
                    )
                except (CostLimitReached, AmbiguousRequestError) as exc:
                    blocker = f"{type(exc).__name__}: {exc}"
                    row_d = arm_error_result(
                        case=case,
                        arm="D",
                        error=exc,
                        events=client_d.call_events,
                        wall_latency_seconds=time.monotonic() - started,
                        incomplete=True,
                    )
                except BaseException as exc:
                    row_d = critic_failure_arm_result(
                        case=case,
                        arm="D",
                        starting_row=arm_c,
                        critic_type="ledger_backed",
                        error=exc,
                        events=client_d.call_events,
                        wall_latency_seconds=time.monotonic() - started,
                    )
            results.append(row_d)
            result_map[(case_id, "D")] = row_d
            _persist_results(results_path, results, cases)
            if blocker:
                return results, issue_rows, ledger, blocker, availability

        assert_arm_invariants(
            arm_a=arm_a,
            arm_b=result_map[(case_id, "B")],
            arm_c=arm_c,
            arm_d=result_map[(case_id, "D")],
        )
    return results, issue_rows, ledger, blocker, availability


def percentile(values: Sequence[float], proportion: float) -> float | None:
    """Return a nearest-rank percentile for a non-empty sample."""
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) * proportion) + 0.999999) - 1))
    return round(ordered[index], 6)


def _latency_summary(values: Sequence[float]) -> dict[str, float | None]:
    """Return compact median and p95 latency telemetry."""
    clean = [float(value) for value in values if float(value) >= 0]
    return {
        "median_seconds": round(statistics.median(clean), 6) if clean else None,
        "p95_seconds": percentile(clean, 0.95),
    }


def _paid_cases_from_manifest(
    manifest: Mapping[str, Any], cases: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return the immutable post-baseline paid case order."""
    paid = manifest.get("paid_comparison")
    if not isinstance(paid, dict):
        return []
    ids = paid.get("case_ids")
    if not isinstance(ids, list) or any(type(value) is not str for value in ids):
        raise EvaluationError("paid comparison manifest is invalid")
    by_id = {str(case["case_id"]): case for case in cases}
    if any(case_id not in by_id for case_id in ids):
        raise EvaluationError("paid comparison references an unknown case")
    selected = [by_id[case_id] for case_id in ids]
    if sha256_value(ids) != paid.get("case_set_sha256"):
        raise EvaluationError("paid comparison hash is invalid")
    return selected


def _all_request_events(
    issue_rows: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return all retained request-consumer events without model payloads."""
    return [
        copy.deepcopy(dict(event))
        for owner in [*issue_rows, *results]
        for event in owner.get("requests") or []
        if isinstance(event, dict)
    ]


def _operation_cost_by_provider(ledger: QudRequestLedger) -> dict[str, dict[str, float]]:
    """Separate authoritative xAI billing from estimated OpenAI cost."""
    result = {
        "xAI": {"billed_usd": 0.0, "estimated_usd": 0.0},
        "OpenAI": {"billed_usd": 0.0, "estimated_usd": 0.0},
    }
    for row in ledger.data.get("operations", []):
        if not isinstance(row, dict) or row.get("status") != "completed":
            continue
        provider = str(row.get("provider") or "")
        if provider not in result:
            continue
        result[provider]["billed_usd"] += float(
            row.get("provider_reported_cost_usd") or 0
        )
        result[provider]["estimated_usd"] += float(
            row.get("estimated_cost_usd") or 0
        )
    return result


def build_comparison(
    *,
    manifest: Mapping[str, Any],
    cases: Sequence[dict[str, Any]],
    issue_rows: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    ledger: QudRequestLedger,
    blocker: str | None,
    human_scoring: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the private operational report without choosing a winning arm."""
    paid_cases = _paid_cases_from_manifest(manifest, cases)
    paid_ids = {str(case["case_id"]) for case in paid_cases}
    result_map = {
        (str(row.get("case_id")), str(row.get("arm"))): row for row in results
    }
    arm_a_rows = [row for row in results if row.get("arm") == "A"]
    completed_a = [row for row in arm_a_rows if row.get("execution_status") == "completed"]
    issue_status_counts = Counter(str(row.get("status") or "failure") for row in issue_rows)
    confidence_counts = Counter(
        str(row.get("confidence"))
        for row in issue_rows
        if row.get("confidence") in {"high", "medium", "low"}
    )
    rejected_counts = {
        "cases_with_rejected_targets": sum(
            bool(row.get("rejected_answer_targets")) for row in issue_rows
        ),
        "total_rejected_targets": sum(
            len(row.get("rejected_answer_targets") or []) for row in issue_rows
        ),
    }
    per_arm: dict[str, dict[str, Any]] = {}
    for arm in ARMS:
        rows = [
            row
            for row in results
            if row.get("arm") == arm and str(row.get("case_id")) in paid_ids
        ]
        completed = [row for row in rows if row.get("execution_status") == "completed"]
        per_arm[arm] = {
            "completed": len(completed),
            "public_reply_count": sum(
                isinstance(row.get("final_public_candidate"), str)
                and bool(row.get("final_public_candidate"))
                for row in completed
            ),
            "no_reply_count": sum(
                not row.get("final_public_candidate") for row in completed
            ),
            "broad_substitution_flags": sum(
                row.get("broad_substitution_flag") is True for row in completed
            ),
            "narrow_gate_triggers": sum(
                row.get("narrow_gate_trigger") is True for row in completed
            ),
            "repair_successes": sum(
                row.get("repair_outcome") == "alignment_repair_succeeded"
                for row in completed
            ),
            "repair_failures": sum(
                row.get("repair_outcome") == "alignment_repair_failed"
                for row in completed
            ),
            "schema_failures": sum(len(row.get("schema_failures") or []) for row in rows),
            "provider_failures": sum(len(row.get("provider_failures") or []) for row in rows),
            "cache_hits": sum(
                int((row.get("usage_and_cost") or {}).get("cache_hits") or 0)
                for row in rows
            ),
            "tokens": {
                field: sum(
                    int((row.get("usage_and_cost") or {}).get(field) or 0)
                    for row in rows
                )
                for field in (
                    "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens"
                )
            },
            "arm_latency": _latency_summary(
                [float(row.get("arm_latency_seconds") or 0) for row in completed]
            ),
        }
    operations = [
        row for row in ledger.data.get("operations", []) if isinstance(row, dict)
    ]
    operation_latencies = [
        float(row.get("provider_latency_seconds") or 0)
        for row in operations
        if row.get("status") == "completed"
    ]
    events = _all_request_events(issue_rows, results)
    cost = ledger.cost_summary()
    provider_cost = _operation_cost_by_provider(ledger)
    automatic_prelabels = []
    for case in paid_cases:
        for label in case.get("historical_labels") or []:
            automatic_prelabels.append(
                {
                    "case_id": case["case_id"],
                    "label": copy.deepcopy(label),
                    "arm_results": {
                        arm: copy.deepcopy(
                            result_map.get((str(case["case_id"]), arm), {}).get(
                                "critic_result"
                            )
                        )
                        for arm in ("B", "D")
                    },
                }
            )
    comparison = {
        "schema_version": SCHEMA_VERSION,
        "run_version": RUN_VERSION,
        "generated_at": utc_now(),
        "source_master_sha": manifest["source_master_sha"],
        "candidate_pool_sha256": manifest["candidate_pool_sha256"],
        "paid_comparison_case_set_sha256": (
            (manifest.get("paid_comparison") or {}).get("case_set_sha256")
        ),
        "counts": {
            "candidate_pool": len(cases),
            "paid_comparison": len(paid_cases),
            "prospective_cases": manifest["candidate_counts"]["selected_prospective_cases"],
            "prior_evaluation_cases": manifest["candidate_counts"]["selected_prior_evaluation_cases"],
            "deduplicated_candidates": manifest["candidate_counts"]["deduplicated_valid_candidates"],
            "unusable_or_unselected": manifest["candidate_counts"]["unusable_or_unselected"],
            "skip_reason_counts": copy.deepcopy(
                manifest["candidate_counts"]["skip_reason_counts"]
            ),
            "baseline_completed": len(completed_a),
            "baseline_reply": sum(
                bool(row.get("final_public_candidate")) for row in completed_a
            ),
            "baseline_no_reply": sum(
                not row.get("final_public_candidate") for row in completed_a
            ),
        },
        "issue_state_status_counts": dict(sorted(issue_status_counts.items())),
        "issue_state_confidence_counts": {
            level: confidence_counts.get(level, 0) for level in ("high", "medium", "low")
        },
        "rejected_answer_targets": rejected_counts,
        "critic_flags": {
            "transcript_only_broad": sum(
                row.get("arm") == "B" and row.get("broad_substitution_flag") is True
                for row in results
            ),
            "ledger_backed_broad": sum(
                row.get("arm") == "D" and row.get("broad_substitution_flag") is True
                for row in results
            ),
            "transcript_only_narrow": sum(
                row.get("arm") == "B" and row.get("narrow_gate_trigger") is True
                for row in results
            ),
            "ledger_backed_narrow": sum(
                row.get("arm") == "D" and row.get("narrow_gate_trigger") is True
                for row in results
            ),
        },
        "repairs": {
            "successes": sum(
                row.get("repair_outcome") == "alignment_repair_succeeded"
                for row in results
            ),
            "failures": sum(
                row.get("repair_outcome") == "alignment_repair_failed"
                for row in results
            ),
        },
        "arms": per_arm,
        "provider": {
            "availability": copy.deepcopy(manifest.get("provider_model_availability")),
            "availability_endpoint_calls": int(
                (manifest.get("provider_model_availability") or {}).get(
                    "endpoint_call_count", 0
                )
            ),
            "canonical_inference_requests": len(operations),
            "completed_inference_requests": sum(
                row.get("status") == "completed" for row in operations
            ),
            "provider_call_events": len(events),
            "cache_hit_events": sum(event.get("cache_hit") is True for event in events),
            "schema_failures": sum(
                len(row.get("schema_failures") or []) for row in results
            ) + sum(bool(row.get("schema_failure")) for row in issue_rows),
            "provider_failures": sum(
                len(row.get("provider_failures") or []) for row in results
            ) + sum(bool(row.get("provider_failure")) for row in issue_rows),
            "ambiguous_call_failures": sum(
                row.get("status") == "ambiguous" for row in operations
            ),
            "token_usage": {
                field: sum(int(row.get(field) or 0) for row in operations)
                for field in (
                    "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens"
                )
            },
            "billed_xai_cost_usd": provider_cost["xAI"]["billed_usd"],
            "estimated_openai_cost_usd": provider_cost["OpenAI"]["estimated_usd"],
            "cost_summary": cost,
            "request_latency": _latency_summary(operation_latencies),
        },
        "automatic_prelabelled_results": automatic_prelabels,
        "execution_blocker": blocker,
        "human_scoring": copy.deepcopy(human_scoring),
        "quality_conclusion": (
            "No winning arm is declared before completed blind and diagnostic human scoring. "
            "No production activation or pipeline change is recommended by this research run."
        ),
    }
    return comparison


def render_comparison_markdown(comparison: Mapping[str, Any]) -> str:
    """Render the private operational report without a model-quality verdict."""
    counts = comparison["counts"]
    provider = comparison["provider"]
    latency = provider["request_latency"]
    issue_status = comparison["issue_state_status_counts"]
    issue_confidence = comparison["issue_state_confidence_counts"]
    critic = comparison["critic_flags"]
    repairs = comparison["repairs"]
    lines = [
        "# QUD Ledger Shadow Evaluation",
        "",
        f"Source master SHA: `{comparison['source_master_sha']}`",
        f"Candidate-pool SHA-256: `{comparison['candidate_pool_sha256']}`",
        f"Paid case-set SHA-256: `{comparison.get('paid_comparison_case_set_sha256')}`",
        "",
        "## Corpus and baseline",
        "",
        f"- Candidate pool: {counts['candidate_pool']}",
        f"- Prospective cases: {counts['prospective_cases']}",
        f"- Prior frozen-evaluation cases: {counts['prior_evaluation_cases']}",
        f"- Deduplicated valid candidates before cap: {counts['deduplicated_candidates']}",
        f"- Unusable or unselected rows: {counts['unusable_or_unselected']}",
        f"- Skip reasons: `{counts['skip_reason_counts']}`",
        f"- Baseline replies: {counts['baseline_reply']}",
        f"- Baseline no-replies: {counts['baseline_no_reply']}",
        f"- Paid four-arm cases: {counts['paid_comparison']}",
        "",
        "## Issue states and answerhood gates",
        "",
        f"- Status counts: `{issue_status}`",
        f"- Confidence counts: `{issue_confidence}`",
        f"- Cases with rejected targets: {comparison['rejected_answer_targets']['cases_with_rejected_targets']}",
        f"- Total rejected targets: {comparison['rejected_answer_targets']['total_rejected_targets']}",
        f"- Transcript-only broad flags: {critic['transcript_only_broad']}",
        f"- Ledger-backed broad flags: {critic['ledger_backed_broad']}",
        f"- Transcript-only narrow triggers: {critic['transcript_only_narrow']}",
        f"- Ledger-backed narrow triggers: {critic['ledger_backed_narrow']}",
        f"- Repair successes: {repairs['successes']}",
        f"- Repair failures: {repairs['failures']}",
        "",
        "## Public outcomes by arm",
        "",
        "| Arm | Completed | Replies | No replies | Broad flags | Narrow triggers | Repair success | Repair failure |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in ARMS:
        row = comparison["arms"][arm]
        lines.append(
            f"| {arm} | {row['completed']} | {row['public_reply_count']} | "
            f"{row['no_reply_count']} | {row['broad_substitution_flags']} | "
            f"{row['narrow_gate_triggers']} | {row['repair_successes']} | "
            f"{row['repair_failures']} |"
        )
    lines.extend(
        [
            "",
            "## Operations, cost, and latency",
            "",
            f"- Authenticated model-endpoint calls: {provider['availability_endpoint_calls']}",
            f"- Canonical inference requests: {provider['canonical_inference_requests']}",
            f"- Provider call events: {provider['provider_call_events']}",
            f"- Cache-hit events: {provider['cache_hit_events']}",
            f"- Schema failures: {provider['schema_failures']}",
            f"- Provider failures: {provider['provider_failures']}",
            f"- Ambiguous-call failures: {provider['ambiguous_call_failures']}",
            f"- Token usage: `{provider['token_usage']}`",
            f"- Provider-reported billed xAI cost: US${float(provider['billed_xai_cost_usd']):.6f}",
            f"- Estimated OpenAI cost: US${float(provider['estimated_openai_cost_usd']):.6f}",
            f"- Request median latency: {latency['median_seconds']!r} seconds",
            f"- Request p95 latency: {latency['p95_seconds']!r} seconds",
            f"- Execution blocker: `{comparison.get('execution_blocker') or 'none'}`",
            "",
            "The xAI billed figure and OpenAI estimate are deliberately separate. The OpenAI figure is not provider-authoritative.",
            "",
            "## Interpretation boundary",
            "",
            str(comparison["quality_conclusion"]),
            "",
        ]
    )
    human = comparison.get("human_scoring")
    if isinstance(human, dict):
        lines.extend(["## Completed human score report", ""])
        lines.append(
            f"Scored cases: {human.get('scored_case_count', 0)}. Results are descriptive paired counts only."
        )
        lines.extend(
            [
                "",
                "| Arm | Acceptable | Minor | Unacceptable | Substitution yes | Preferred | W/L/T vs A | New unacceptable | New substitutions | Repaired substitutions | False interventions | Failed-repair no reply | Billed / acceptable | Estimated / acceptable | Median / p95 latency |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        score_arms = human.get("arms") or {}
        for arm in ARMS:
            row = score_arms.get(arm) or {}
            latency = row.get("latency") or {}
            lines.append(
                f"| {arm} | {row.get('acceptable', 0)} | {row.get('minor', 0)} | "
                f"{row.get('unacceptable', 0)} | {row.get('substitution_yes', 0)} | "
                f"{row.get('preferred_outcomes', 0)} | "
                f"{row.get('wins_against_A', 0)}/{row.get('losses_against_A', 0)}/"
                f"{row.get('ties_against_A', 0)} | "
                f"{row.get('newly_introduced_unacceptable', 0)} | "
                f"{row.get('newly_introduced_substitutions', 0)} | "
                f"{row.get('repaired_substitutions', 0)} | "
                f"{row.get('false_interventions_on_sound_replies', 0)} | "
                f"{row.get('no_reply_from_failed_repair', 0)} | "
                f"{row.get('billed_cost_per_acceptable_outcome_usd')!r} | "
                f"{row.get('estimated_cost_per_acceptable_outcome_usd')!r} | "
                f"{latency.get('median_seconds')!r} / {latency.get('p95_seconds')!r} |"
            )
        lines.extend(
            [
                "",
                f"Preferred-outcome totals: `{human.get('preferred_outcome_totals') or {}}`",
                "",
            ]
        )
        diagnostic = human.get("diagnostic_scoring")
        if isinstance(diagnostic, dict):
            lines.extend(
                [
                    "Diagnostic extraction and critic scoring:",
                    "",
                    f"- Rated cases: {diagnostic.get('scored_case_count', 0)}",
                    f"- Counts: `{diagnostic.get('counts') or {}}`",
                    f"- Issue-state accuracy: {diagnostic.get('issue_state_accuracy')!r}",
                    f"- Transcript-only critic accuracy: {diagnostic.get('transcript_critic_accuracy')!r}",
                    f"- Ledger-backed critic accuracy: {diagnostic.get('ledger_critic_accuracy')!r}",
                    "",
                ]
            )
        lines.append(
            "No production activation is recommended by this experiment. A separate, tightly scoped production-design review would still be required even if an experimental arm performs better."
        )
        lines.append("")
    return "\n".join(lines)


def _blind_outcome(result: Mapping[str, Any] | None) -> str:
    """Render every non-public or silent result uniformly."""
    if result is None or result.get("execution_status") != "completed":
        return "[NO REPLY]"
    value = result.get("final_public_candidate")
    return value if isinstance(value, str) and value else "[NO REPLY]"


def build_blind_rows(
    *,
    cases: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    arm_key: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Build exact-branch W/X/Y/Z rows without serialising the arm key."""
    result_map = {
        (str(row["case_id"]), str(row["arm"])): row for row in results
    }
    label_to_arm = {label: arm for arm, label in arm_key.items()}
    return [
        {
            "case_id": case["case_id"],
            "exact_visible_principal_author_branch": copy.deepcopy(
                case["issue_transcript"]
            ),
            "public_outcomes": {
                label: _blind_outcome(
                    result_map.get((str(case["case_id"]), label_to_arm[label]))
                )
                for label in BLIND_LABELS
            },
        }
        for case in cases
    ]


def _render_issue_transcript(turns: Sequence[Mapping[str, Any]]) -> list[str]:
    """Render exact issue turns for a private human reviewer."""
    lines: list[str] = []
    for turn in turns:
        role = "Account" if turn.get("author_role") == "account" else "Contributor"
        lines.append(
            f"- {role} (`{turn.get('turn_id')}`): {turn.get('text', '')}"
        )
    return lines


def render_blind_review(rows: Sequence[Mapping[str, Any]]) -> str:
    """Render only exact context, public outcomes, and blind instructions."""
    lines = [
        "# Blind Public-Output Review",
        "",
        "Rate each W/X/Y/Z outcome as `acceptable`, `minor`, or `unacceptable`. Mark proposition substitution as `yes`, `no`, or `unclear`. Silence is `[NO REPLY]`.",
        "",
        "An outcome is defective when it answers a nearby proposition instead of the live issue; changes the actor, action, relation, object, polarity, modality, scope, time, or counterfactual; asks for clarification when the issue is already clear; revives an answer target explicitly rejected by the contributor; fails to answer a direct issue; invents a factual claim; or becomes socially inappropriate or unusable.",
        "",
        "Do not penalise a reply merely because it disagrees with the contributor. Do not penalise a genuine elaboration when the live issue was answered first.",
        "",
    ]
    for row in rows:
        lines.extend([f"## {row['case_id']}", "", "Exact visible branch:", ""])
        lines.extend(_render_issue_transcript(row["exact_visible_principal_author_branch"]))
        lines.append("")
        for label in BLIND_LABELS:
            lines.extend(
                [
                    f"### {label} public outcome",
                    "",
                    str(row["public_outcomes"][label]),
                    "",
                ]
            )
    return "\n".join(lines)


def _validate_blind_outputs(
    *, rows: Sequence[Mapping[str, Any]], markdown: str
) -> None:
    """Require the exact blind structure and forbid diagnostic metadata keys."""
    for row in rows:
        if set(row) != {
            "case_id", "exact_visible_principal_author_branch", "public_outcomes"
        }:
            raise EvaluationError("blind row fields changed")
        if set(row["public_outcomes"]) != set(BLIND_LABELS):
            raise EvaluationError("blind outcome labels changed")
    forbidden_headings = (
        "Arm A", "Arm B", "Arm C", "Arm D", "issue state:", "critic result:",
        "narrow trigger", "provider cost", "route reason", "arm key",
    )
    if any(value.casefold() in markdown.casefold() for value in forbidden_headings):
        raise EvaluationError("blind markdown leaks an experiment definition")


def write_blind_scores_template(
    path: Path, cases: Sequence[Mapping[str, Any]]
) -> None:
    """Create or safely populate the exact four-outcome blind score template."""
    fields = [
        "case_id",
        "W_rating", "W_substitution",
        "X_rating", "X_substitution",
        "Y_rating", "Y_substitution",
        "Z_rating", "Z_substitution",
        "preferred_outcome", "notes",
    ]
    expected_case_ids = [str(case["case_id"]) for case in cases]
    if path.exists():
        existing = _read_csv_exact(path, fields)
        existing_case_ids = [str(row.get("case_id") or "") for row in existing]
        if existing_case_ids == expected_case_ids:
            return
        if existing:
            raise EvaluationError("existing blind score template case set changed")
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for case in cases:
        writer.writerow(
            {field: str(case["case_id"]) if field == "case_id" else "" for field in fields}
        )
    atomic_text(path, buffer.getvalue())


def render_diagnostic_review(
    *,
    cases: Sequence[Mapping[str, Any]],
    issue_rows: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
) -> str:
    """Render unblinded diagnostics without exposing the W/X/Y/Z mapping."""
    issue_map = {str(row["case_id"]): row for row in issue_rows}
    result_map = {
        (str(row["case_id"]), str(row["arm"])): row for row in results
    }
    lines = [
        "# Diagnostic QUD Review",
        "",
        "Do not open this document until the blind public-output scores have been completed and saved.",
        "",
        "This document contains extraction and critic diagnostics. It does not contain the W/X/Y/Z arm mapping.",
        "",
    ]
    for case in cases:
        case_id = str(case["case_id"])
        issue = issue_map.get(case_id, {}).get("result")
        transcript_critic = result_map.get((case_id, "B"), {}).get("critic_result")
        ledger_critic = result_map.get((case_id, "D"), {}).get("critic_result")
        lines.extend([f"## {case_id}", "", "Exact transcript:", ""])
        lines.extend(_render_issue_transcript(case["issue_transcript"]))
        lines.extend(
            [
                "",
                "Extracted issue state:",
                "",
                "```json",
                json.dumps(issue, ensure_ascii=False, sort_keys=True, indent=2),
                "```",
                "",
                "Transcript-only critic:",
                "",
                "```json",
                json.dumps(transcript_critic, ensure_ascii=False, sort_keys=True, indent=2),
                "```",
                "",
                "Ledger-backed critic:",
                "",
                "```json",
                json.dumps(ledger_critic, ensure_ascii=False, sort_keys=True, indent=2),
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def write_diagnostic_scores_template(
    path: Path, cases: Sequence[Mapping[str, Any]]
) -> None:
    """Create or safely populate the exact diagnostic score template."""
    fields = [
        "case_id", "issue_state_rating", "user_stance_rating",
        "rejected_target_rating", "transcript_critic_rating",
        "ledger_critic_rating", "notes",
    ]
    expected_case_ids = [str(case["case_id"]) for case in cases]
    if path.exists():
        existing = _read_csv_exact(path, fields)
        existing_case_ids = [str(row.get("case_id") or "") for row in existing]
        if existing_case_ids == expected_case_ids:
            return
        if existing:
            raise EvaluationError("existing diagnostic score template case set changed")
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for case in cases:
        writer.writerow(
            {field: str(case["case_id"]) if field == "case_id" else "" for field in fields}
        )
    atomic_text(path, buffer.getvalue())


def generate_reports(
    *,
    output_dir: Path,
    manifest: Mapping[str, Any],
    cases: Sequence[dict[str, Any]],
    issue_rows: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    ledger: QudRequestLedger,
    blocker: str | None,
    human_scoring: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write operational, blind, and separate diagnostic artifacts."""
    comparison = build_comparison(
        manifest=manifest,
        cases=cases,
        issue_rows=issue_rows,
        results=results,
        ledger=ledger,
        blocker=blocker,
        human_scoring=human_scoring,
    )
    atomic_json(private_path(output_dir, "comparison.private.json"), comparison)
    atomic_text(
        private_path(output_dir, "comparison.private.md"),
        render_comparison_markdown(comparison),
    )
    paid_cases = _paid_cases_from_manifest(manifest, cases)
    arm_key = create_or_load_arm_key(private_path(output_dir, "arm_key.private.json"))
    blind_rows = build_blind_rows(
        cases=paid_cases, results=results, arm_key=arm_key
    )
    blind_markdown = render_blind_review(blind_rows)
    _validate_blind_outputs(rows=blind_rows, markdown=blind_markdown)
    atomic_jsonl(private_path(output_dir, "blind_review.jsonl"), blind_rows)
    atomic_text(private_path(output_dir, "blind_review.md"), blind_markdown)
    write_blind_scores_template(
        private_path(output_dir, "blind_scores.csv"), paid_cases
    )
    diagnostic = render_diagnostic_review(
        cases=paid_cases, issue_rows=issue_rows, results=results
    )
    atomic_text(private_path(output_dir, "diagnostic_review.md"), diagnostic)
    write_diagnostic_scores_template(
        private_path(output_dir, "diagnostic_scores.csv"), paid_cases
    )
    return comparison


def _read_csv_exact(path: Path, expected_fields: Sequence[str]) -> list[dict[str, str]]:
    """Read one exact-column UTF-8 CSV without modifying it."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(expected_fields):
            raise EvaluationError(f"score CSV columns changed: {path.name}")
        return [dict(row) for row in reader]


def load_completed_diagnostic_scores(
    *, path: Path, cases: Sequence[Mapping[str, Any]]
) -> dict[str, Any] | None:
    """Optionally validate and summarise a fully completed diagnostic CSV."""
    fields = [
        "case_id", "issue_state_rating", "user_stance_rating",
        "rejected_target_rating", "transcript_critic_rating",
        "ledger_critic_rating", "notes",
    ]
    rows = _read_csv_exact(path, fields)
    case_ids = [str(case["case_id"]) for case in cases]
    if [str(row.get("case_id") or "") for row in rows] != case_ids:
        raise EvaluationError("diagnostic score case order changed")
    scored_fields = fields[1:-1]
    if all(not str(row.get(field) or "").strip() for row in rows for field in scored_fields):
        return None
    if any(not str(row.get(field) or "").strip() for row in rows for field in scored_fields):
        raise EvaluationError("diagnostic scores are only partially completed")
    allowed = {
        "issue_state_rating": {"accurate", "partly_accurate", "wrong", "genuinely_unclear"},
        "user_stance_rating": {"accurate", "partly_accurate", "wrong", "not_applicable"},
        "rejected_target_rating": {"accurate", "missed", "false_positive", "not_applicable"},
        "transcript_critic_rating": {"correct", "incorrect", "genuinely_unclear"},
        "ledger_critic_rating": {"correct", "incorrect", "genuinely_unclear"},
    }
    counts: dict[str, dict[str, int]] = {}
    for field, values in allowed.items():
        observed = [str(row[field]).strip().casefold() for row in rows]
        if any(value not in values for value in observed):
            raise EvaluationError(f"invalid diagnostic score in {field}")
        counts[field] = dict(sorted(Counter(observed).items()))
    return {
        "completed": True,
        "scored_case_count": len(rows),
        "counts": counts,
        "issue_state_accuracy": (
            counts["issue_state_rating"].get("accurate", 0) / len(rows)
            if rows else None
        ),
        "transcript_critic_accuracy": (
            counts["transcript_critic_rating"].get("correct", 0) / len(rows)
            if rows else None
        ),
        "ledger_critic_accuracy": (
            counts["ledger_critic_rating"].get("correct", 0) / len(rows)
            if rows else None
        ),
    }


def load_completed_scores(
    *,
    scores_path: Path,
    diagnostic_path: Path,
    cases: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    issue_rows: Sequence[Mapping[str, Any]],
    arm_key: Mapping[str, str],
) -> dict[str, Any]:
    """Decode completed blind scores into paired descriptive arm counts."""
    fields = [
        "case_id",
        "W_rating", "W_substitution",
        "X_rating", "X_substitution",
        "Y_rating", "Y_substitution",
        "Z_rating", "Z_substitution",
        "preferred_outcome", "notes",
    ]
    rows = _read_csv_exact(scores_path, fields)
    case_ids = [str(case["case_id"]) for case in cases]
    if [str(row.get("case_id") or "") for row in rows] != case_ids:
        raise EvaluationError("blind score cases or ordering changed")
    label_to_arm = {label: arm for arm, label in arm_key.items()}
    ratings_allowed = {"acceptable": 2, "minor": 1, "unacceptable": 0}
    substitutions_allowed = {"yes", "no", "unclear"}
    ratings_by_case: dict[str, dict[str, str]] = {}
    substitutions_by_case: dict[str, dict[str, str]] = {}
    preferred = Counter()
    arm_rows: dict[str, dict[str, Any]] = {
        arm: {
            "acceptable": 0,
            "minor": 0,
            "unacceptable": 0,
            "substitution_yes": 0,
            "substitution_no": 0,
            "substitution_unclear": 0,
            "preferred_outcomes": 0,
            "wins_against_A": 0,
            "losses_against_A": 0,
            "ties_against_A": 0,
            "newly_introduced_unacceptable": 0,
            "newly_introduced_substitutions": 0,
            "repaired_substitutions": 0,
            "false_interventions_on_sound_replies": 0,
            "no_reply_from_failed_repair": 0,
        }
        for arm in ARMS
    }
    for score in rows:
        case_id = str(score["case_id"])
        ratings: dict[str, str] = {}
        substitutions: dict[str, str] = {}
        for label in BLIND_LABELS:
            rating = str(score[f"{label}_rating"]).strip().casefold()
            substitution = str(score[f"{label}_substitution"]).strip().casefold()
            if rating not in ratings_allowed:
                raise EvaluationError(f"invalid blind rating for {case_id} {label}")
            if substitution not in substitutions_allowed:
                raise EvaluationError(f"invalid substitution score for {case_id} {label}")
            arm = label_to_arm[label]
            ratings[arm] = rating
            substitutions[arm] = substitution
            arm_rows[arm][rating] += 1
            arm_rows[arm][f"substitution_{substitution}"] += 1
        ratings_by_case[case_id] = ratings
        substitutions_by_case[case_id] = substitutions
        choice = str(score["preferred_outcome"]).strip().upper()
        if choice == "TIE":
            preferred["tie"] += 1
        elif choice in BLIND_LABELS:
            preferred[label_to_arm[choice]] += 1
            arm_rows[label_to_arm[choice]]["preferred_outcomes"] += 1
        else:
            raise EvaluationError(
                f"preferred outcome for {case_id} must be W, X, Y, Z, or tie"
            )

    result_map = {
        (str(row["case_id"]), str(row["arm"])): row for row in results
    }
    for case_id in case_ids:
        baseline_rating = ratings_by_case[case_id]["A"]
        baseline_substitution = substitutions_by_case[case_id]["A"]
        for arm in ARMS:
            left = ratings_allowed[ratings_by_case[case_id][arm]]
            baseline = ratings_allowed[baseline_rating]
            if left > baseline:
                arm_rows[arm]["wins_against_A"] += 1
            elif left < baseline:
                arm_rows[arm]["losses_against_A"] += 1
            else:
                arm_rows[arm]["ties_against_A"] += 1
            if ratings_by_case[case_id][arm] == "unacceptable" and baseline_rating != "unacceptable":
                arm_rows[arm]["newly_introduced_unacceptable"] += 1
            if substitutions_by_case[case_id][arm] == "yes" and baseline_substitution != "yes":
                arm_rows[arm]["newly_introduced_substitutions"] += 1
            if baseline_substitution == "yes" and substitutions_by_case[case_id][arm] == "no":
                arm_rows[arm]["repaired_substitutions"] += 1
            result = result_map.get((case_id, arm), {})
            if result.get("repair_outcome") == "alignment_repair_failed" and not result.get("final_public_candidate"):
                arm_rows[arm]["no_reply_from_failed_repair"] += 1

        predecessor = {"B": "A", "C": "A", "D": "C"}
        for arm, prior_arm in predecessor.items():
            prior_sound = (
                ratings_by_case[case_id][prior_arm] == "acceptable"
                and substitutions_by_case[case_id][prior_arm] == "no"
            )
            changed = (
                result_map.get((case_id, arm), {}).get("final_public_candidate")
                != result_map.get((case_id, prior_arm), {}).get("final_public_candidate")
            )
            degraded = (
                ratings_allowed[ratings_by_case[case_id][arm]]
                < ratings_allowed[ratings_by_case[case_id][prior_arm]]
                or substitutions_by_case[case_id][arm] == "yes"
            )
            if prior_sound and changed and degraded:
                arm_rows[arm]["false_interventions_on_sound_replies"] += 1

    shared_issue_billed = sum(
        float((row.get("usage_and_cost") or {}).get("provider_reported_billed_cost_usd") or 0)
        for row in issue_rows
    )
    shared_issue_estimated = sum(
        float((row.get("usage_and_cost") or {}).get("estimated_openai_cost_usd") or 0)
        for row in issue_rows
    )
    for arm in ARMS:
        result_rows = [
            result_map[(case_id, arm)]
            for case_id in case_ids
            if (case_id, arm) in result_map
        ]
        billed = sum(
            float((row.get("usage_and_cost") or {}).get("provider_reported_billed_cost_usd") or 0)
            for row in result_rows
        )
        estimated = sum(
            float((row.get("usage_and_cost") or {}).get("estimated_openai_cost_usd") or 0)
            for row in result_rows
        )
        if arm in {"B", "C", "D"}:
            billed += shared_issue_billed / 3
            estimated += shared_issue_estimated / 3
        acceptable = int(arm_rows[arm]["acceptable"])
        arm_rows[arm]["billed_cost_per_acceptable_outcome_usd"] = (
            billed / acceptable if acceptable else None
        )
        arm_rows[arm]["estimated_cost_per_acceptable_outcome_usd"] = (
            estimated / acceptable if acceptable else None
        )
        arm_rows[arm]["latency"] = _latency_summary(
            [float(row.get("arm_latency_seconds") or 0) for row in result_rows]
        )
    diagnostic = load_completed_diagnostic_scores(path=diagnostic_path, cases=cases)
    return {
        "completed": True,
        "judge": "blind_human_only",
        "scored_case_count": len(cases),
        "arms": arm_rows,
        "preferred_outcome_totals": {
            **{arm: preferred.get(arm, 0) for arm in ARMS},
            "tie": preferred.get("tie", 0),
        },
        "diagnostic_scoring": diagnostic,
        "production_activation_recommendation": (
            "none; unsupported-fact, branch-contamination, false-intervention, and suppression criteria require explicit human review, and any favourable result would still require a separate tightly scoped production-design review"
        ),
        "significance_tests": "not_performed",
        "generated_at": utc_now(),
    }


def report_completed_scores(output_dir: Path) -> dict[str, Any]:
    """Decode human score files and refresh only private descriptive reports."""
    manifest = read_json(private_path(output_dir, "manifest.json"))
    if not isinstance(manifest, dict):
        raise EvaluationError("manifest is invalid")
    cases = read_jsonl(private_path(output_dir, "cases.jsonl"))
    paid_cases = _paid_cases_from_manifest(manifest, cases)
    results = load_arm_results(private_path(output_dir, "arm_results.jsonl"))
    issue_rows = load_issue_state_records(private_path(output_dir, "issue_states.jsonl"))
    arm_key = create_or_load_arm_key(private_path(output_dir, "arm_key.private.json"))
    scoring = load_completed_scores(
        scores_path=private_path(output_dir, "blind_scores.csv"),
        diagnostic_path=private_path(output_dir, "diagnostic_scores.csv"),
        cases=paid_cases,
        results=results,
        issue_rows=issue_rows,
        arm_key=arm_key,
    )
    ledger = QudRequestLedger(
        private_path(output_dir, "request_ledger.json"),
        case_set_sha256=str(manifest["candidate_pool_sha256"]),
        hard_limit_usd=float(manifest["global_cost_ceiling_usd"]),
    )
    blocker = (manifest.get("execution") or {}).get("blocker")
    return generate_reports(
        output_dir=output_dir,
        manifest=manifest,
        cases=cases,
        issue_rows=issue_rows,
        results=results,
        ledger=ledger,
        blocker=str(blocker) if blocker else None,
        human_scoring=scoring,
    )


def _build_parser() -> argparse.ArgumentParser:
    """Return the preparation, live execution, and score-report parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--review-pack", type=Path)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--execute-live-models", action="store_true")
    parser.add_argument("--report-scores", action="store_true")
    parser.add_argument(
        "--cost-ceiling-usd", type=float, default=GLOBAL_COST_CEILING_USD
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Prepare offline by default and require an explicit flag for provider calls."""
    os.umask(0o077)
    args = _build_parser().parse_args(argv)
    project_dir = args.project_dir.expanduser().resolve()
    if project_dir != PROJECT_ROOT.resolve():
        raise EvaluationError("project directory must be this isolated worktree")
    output_dir = ensure_private_output_dir(args.output_dir, project_dir=project_dir)
    if args.report_scores:
        if args.execute_live_models:
            raise EvaluationError("--report-scores cannot be combined with live execution")
        comparison = report_completed_scores(output_dir)
        print(
            json.dumps(
                {
                    "mode": "report_scores",
                    "scored_cases": comparison["human_scoring"]["scored_case_count"],
                    "comparison": str(private_path(output_dir, "comparison.private.md")),
                },
                sort_keys=True,
            )
        )
        return 0
    if args.review_pack is None:
        raise EvaluationError("--review-pack is required for preparation")
    if args.cost_ceiling_usd != GLOBAL_COST_CEILING_USD:
        raise EvaluationError("--cost-ceiling-usd must remain exactly 20.00")
    manifest, cases, _skips = prepare_experiment(
        project_dir=project_dir,
        output_dir=output_dir,
        review_pack=args.review_pack,
    )
    ledger = QudRequestLedger(
        private_path(output_dir, "request_ledger.json"),
        case_set_sha256=str(manifest["candidate_pool_sha256"]),
        hard_limit_usd=GLOBAL_COST_CEILING_USD,
    )
    results = load_arm_results(private_path(output_dir, "arm_results.jsonl"))
    issue_rows = load_issue_state_records(private_path(output_dir, "issue_states.jsonl"))
    if not args.execute_live_models:
        manifest = update_manifest_execution(
            output_dir,
            mode="prepared_only",
            blocker=None,
            ledger=ledger,
        )
        comparison = generate_reports(
            output_dir=output_dir,
            manifest=manifest,
            cases=cases,
            issue_rows=issue_rows,
            results=results,
            ledger=ledger,
            blocker=None,
        )
        print(
            json.dumps(
                {
                    "mode": "prepared_only",
                    "candidate_pool_count": comparison["counts"]["candidate_pool"],
                    "candidate_pool_sha256": manifest["candidate_pool_sha256"],
                    "network_requests": 0,
                },
                sort_keys=True,
            )
        )
        return 0
    api_keys = load_api_keys(args.env_file)
    blocker: str | None = None
    availability: dict[str, Any] | None = None
    try:
        results, issue_rows, ledger, blocker, availability = execute_experiment(
            execute_live_models=True,
            project_dir=project_dir,
            output_dir=output_dir,
            manifest=manifest,
            cases=cases,
            api_keys=api_keys,
            hard_limit_usd=GLOBAL_COST_CEILING_USD,
        )
    except (EvaluationError, OSError, ValueError) as exc:
        blocker = f"{type(exc).__name__}: {exc}"
        results = load_arm_results(private_path(output_dir, "arm_results.jsonl"))
        issue_rows = load_issue_state_records(private_path(output_dir, "issue_states.jsonl"))
        ledger = QudRequestLedger(
            private_path(output_dir, "request_ledger.json"),
            case_set_sha256=str(manifest["candidate_pool_sha256"]),
            hard_limit_usd=GLOBAL_COST_CEILING_USD,
        )
    manifest = update_manifest_execution(
        output_dir,
        mode="live_models",
        blocker=blocker,
        ledger=ledger,
        availability=availability,
    )
    comparison = generate_reports(
        output_dir=output_dir,
        manifest=manifest,
        cases=cases,
        issue_rows=issue_rows,
        results=results,
        ledger=ledger,
        blocker=blocker,
    )
    print(
        json.dumps(
            {
                "mode": "live_models",
                "candidate_pool": comparison["counts"]["candidate_pool"],
                "paid_comparison": comparison["counts"]["paid_comparison"],
                "canonical_requests": comparison["provider"]["canonical_inference_requests"],
                "cache_hits": comparison["provider"]["cache_hit_events"],
                "billed_xai_cost_usd": comparison["provider"]["billed_xai_cost_usd"],
                "estimated_openai_cost_usd": comparison["provider"]["estimated_openai_cost_usd"],
                "blocker": blocker,
                "comparison": str(private_path(output_dir, "comparison.private.md")),
            },
            sort_keys=True,
        )
    )
    return 1 if blocker else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (EvaluationError, OSError, ValueError) as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
