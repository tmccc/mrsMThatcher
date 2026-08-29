#!/usr/bin/env python3
"""Run a private detector-only rejected-answer recurrence evaluation.

The runner has no posting path and imports no X client.  Corpus preparation is
offline.  Provider traffic is possible only with ``--execute-live-models`` and
is restricted to the two established synchronous model endpoints.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import re
import secrets
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
from urllib.parse import urlsplit

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools import run_grok_46_tested_pipeline_eval as base_harness
from tools import run_qud_ledger_eval as qud_harness
from tools import run_writer_model_eval as writer_harness


RUN_VERSION = "rejected-answer-recurrence-eval-v1"
SCHEMA_VERSION = 1
GLOBAL_COST_CEILING_USD = 10.0
MAX_CASES = 30
MAX_PATH_TURNS = 24
ACCOUNT_ID = "961002152582885377"

PRODUCTION_CHECKOUT = Path("/disks/disk1/etc/mrsMThatcher")
PREVIOUS_EXPERIMENT_ROOT = Path(
    "/disks/disk1/research/qud-ledger-shadow-eval-20260829"
)
PROSPECTIVE_ROOT = Path(
    "/disks/disk1/research/mrsMThatcher-prospective-conversations-v4"
)
GROK_EVAL_ROOT = Path(
    "/disks/disk1/research/grok-46-tested-pipeline-eval-20260828"
)
WRITER_EVAL_ROOT = Path(
    "/disks/disk1/research/writer-model-eval-20260828"
)
DEFAULT_OUTPUT_ROOT = Path(
    "/disks/disk1/research/rejected-answer-recurrence-eval-20260829"
)
DEFAULT_ENV_FILE = PRODUCTION_CHECKOUT / "mrsMThatcher.env"
DEFAULT_HISTORY_ROOT = DEFAULT_OUTPUT_ROOT / "history_reconstruction"

XAI_HOST = base_harness.XAI_HOST
OPENAI_HOST = base_harness.OPENAI_HOST
XAI_BASE_URL = base_harness.XAI_BASE_URL
OPENAI_BASE_URL = base_harness.OPENAI_BASE_URL
NETWORK_ALLOWLIST = frozenset({XAI_HOST, OPENAI_HOST})

EXTRACTION_MODEL = "grok-4.6"
EXTRACTION_EFFORT = "low"
DETECTOR_MODEL = "gpt-5.6-sol"
DETECTOR_EFFORT = "medium"
CONDITIONS = ("transcript_only", "repair_record_backed")
BLIND_DETECTOR_LABELS = ("P", "Q")

SNAPSHOT_ROOT = Path("/disks/disk1/.zfs/snapshot")
SNAPSHOT_NAME_RE = re.compile(r"^zfs-auto-snap_daily-.*$")
RECONSTRUCTION_TOOL_COMMIT = "bdb6a5b18468bbda02f2c908f8a7699601ee5d18"
RECONSTRUCTION_TOOL_GIT_PATH = "tools/reconstruct_reply_history.py"

PROTECTED_PRODUCTION_PATHS = frozenset(
    {
        "tested_reply_pipeline.py",
        "reply_strategy.py",
        "reply_evidence.py",
        "mrsMThatcher2.py",
        "tools/extract_prospective_conversations.py",
        "tools/run_qud_ledger_eval.py",
        "mrsMThatcher.local.json",
        "bot_state.json",
        "mrsMThatcher.control.json",
    }
)


class EvaluationError(RuntimeError):
    """The isolated recurrence evaluation cannot continue safely."""


CostLimitReached = base_harness.CostLimitReached
AmbiguousRequestError = base_harness.AmbiguousRequestError
ProviderError = base_harness.ProviderError
DefiniteProviderError = base_harness.DefiniteProviderError
TransientProviderError = base_harness.TransientProviderError
ModelAvailabilityError = base_harness.ModelAvailabilityError

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
    return {
        "type": "object",
        "properties": copy.deepcopy(dict(properties)),
        "required": list(properties),
        "additionalProperties": False,
    }


def _bounded_string(maximum: int, *, allow_empty: bool = False) -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 0 if allow_empty else 1,
        "maxLength": maximum,
        "pattern": r"^[^\u0000-\u001f\u007f-\u009f]*$",
    }


def _turn_id_array(maximum: int = 3) -> dict[str, Any]:
    return {
        "type": "array",
        "items": _bounded_string(128),
        "maxItems": maximum,
        "uniqueItems": True,
    }


RESTORED_ISSUE_SCHEMA = _strict(
    {
        "text": _bounded_string(600, allow_empty=True),
        "source_turn_ids": _turn_id_array(),
        "source_quotes": {
            "type": "array",
            "items": _bounded_string(500),
            "maxItems": 3,
        },
    }
)

REPAIR_RECORD_SCHEMA = _strict(
    {
        "status": {
            "type": "string",
            "enum": ["explicit_repair_found", "no_explicit_repair", "unclear"],
        },
        "repair_turn_id": _bounded_string(128, allow_empty=True),
        "repair_evidence_quote": _bounded_string(500, allow_empty=True),
        "rejected_account_turn_id": _bounded_string(128, allow_empty=True),
        "rejected_answer_quote": _bounded_string(500, allow_empty=True),
        "restored_issue": RESTORED_ISSUE_SCHEMA,
        "repair_type": {
            "type": "string",
            "enum": [
                "wrong_question",
                "wrong_proposition",
                "wrong_actor",
                "wrong_relation",
                "wrong_scope",
                "wrong_polarity",
                "wrong_time_or_counterfactual",
                "other",
                "not_applicable",
            ],
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    }
)

DETECTOR_SCHEMA = _strict(
    {
        "status": {
            "type": "string",
            "enum": [
                "recurrence",
                "no_recurrence",
                "no_explicit_repair",
                "unclear",
            ],
        },
        "repair_turn_id": _bounded_string(128, allow_empty=True),
        "rejected_account_turn_id": _bounded_string(128, allow_empty=True),
        "addresses_restored_issue": {
            "type": "string",
            "enum": ["direct", "partial", "not_addressed", "unclear"],
        },
        "treatment_of_rejected_answer": {
            "type": "string",
            "enum": [
                "avoids",
                "references_to_distinguish",
                "substantially_repeats_as_answer",
                "unclear",
            ],
        },
        "candidate_first_focus": {
            "type": "string",
            "enum": ["restored_issue", "rejected_answer", "other", "unclear"],
        },
        "candidate_evidence_quote": _bounded_string(500, allow_empty=True),
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    }
)

EXTRACTION_PROMPT = """Extract only an explicit contributor rejection of a previous account answer as the answer target.
Raw turn text is authoritative. Do not build a general issue ledger, topic graph, belief store, or formal logic record. Do not decide whether either political proposition is true and do not infer private beliefs.
Ordinary disagreement is not an explicit wrong-answer repair. “I am not asking about X” rejects X as the account's answer target; it is not the contributor withdrawing a proposition they owned. A contributor genuinely replacing the old question is not a repair of the old question. “I don't care. I’m keeping my benefits” is not, by itself, an assertion that the account answered a different question.
Identify the exact earlier account answer rejected, and the exact issue the contributor restores, using literal quotes and only supplied turn IDs. Use unclear rather than manufacture a precise record. Return no rationale and only the required JSON."""

DETECTOR_PROMPT = """Judge only recurrence after an explicit wrong-answer repair. Do not perform a general relevance or quality review.
Do not decide whether the candidate is polite, witty, politically agreeable, or factually correct. Do not flag a reply merely because it is generic or incomplete. Do not flag a premise-neutral answer merely because it declines to accept an unsupported allegation.
Direct disagreement can still address the restored issue. Mentioning the rejected answer to distinguish it from the restored issue is not recurrence. A related point after directly answering the restored issue is not recurrence.
A reply is recurrence only when it again uses the rejected answer as the answer and leaves the restored issue unaddressed. Use exact supplied IDs and quote exact candidate text as evidence. Use unclear instead of forcing a verdict. Return no rationale and only the required JSON."""


PREFILTER_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        (
            r"\b(?:(?:that|this)\s+(?:is|was|isn't|wasn't)|that['’]s)"
            r"\s+not\s+what\s+i\s+(?:asked|meant)\b"
        ),
        r"\bi\s+(?:did\s+not|didn't|am\s+not|i'm\s+not)\s+ask(?:ing)?\s+(?:about|whether|if|for)\b",
        r"\byou\s+(?:answered|are\s+answering)\s+(?:a\s+)?different\s+(?:question|point|proposition)\b",
        r"\bthat\s+(?:does\s+not|doesn't|did\s+not|didn't)\s+answer\s+(?:my|the)\s+question\b",
        r"\byou\s+(?:have\s+not|haven't|did\s+not|didn't)\s+answered\s+(?:my|the)\s+question\b",
        r"\bmy\s+(?:question|point)\s+(?:is|was)\b.{0,180}\bnot\b",
        r"\bi\s+(?:said|asked|meant)\b.{0,180}\bnot\b",
        (
            r"\bplease\s+don['’]t\s+hedge\b.{0,420}"
            r"\b(?:did\s+not|didn't)\s+say\b.{0,220}\bbut\b"
        ),
    )
)
USER_REPLACEMENT_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:forget|drop|leave|abandon)(?:ing)?\s+(?:my|the|that)\s+(?:old|previous|former)?\s*question\b",
        r"\bi\s+(?:withdraw|abandon)\s+(?:my|the)\s+(?:old|previous|former)?\s*(?:question|point)\b",
    )
)


def explicit_repair_prefilter(text: object) -> bool:
    """Select only high-precision metaconversational wrong-answer cues."""
    if not isinstance(text, str):
        return False
    collapsed = " ".join(text.split())
    if any(pattern.search(collapsed) for pattern in USER_REPLACEMENT_PATTERNS):
        return False
    return any(pattern.search(collapsed) for pattern in PREFILTER_PATTERNS)


def validate_provider_url(url: str, *, expected_host: str | None = None) -> str:
    """Allow only exact synchronous model and availability endpoints."""
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
        OPENAI_HOST: {f"/v1/models/{DETECTOR_MODEL}", "/v1/chat/completions"},
    }
    if parsed.path not in permitted[parsed.hostname]:
        raise EvaluationError("provider URL path is not permitted")
    return url


def ensure_private_output_dir(path: Path, *, project_dir: Path) -> Path:
    """Create only the fixed private tree and protect production and QUD data."""
    resolved = path.expanduser().resolve()
    project = project_dir.expanduser().resolve()
    production = PRODUCTION_CHECKOUT.resolve()
    previous = PREVIOUS_EXPERIMENT_ROOT.resolve()
    allowed = DEFAULT_OUTPUT_ROOT.resolve()
    protected = (production, project, previous)
    if any(resolved == root or root in resolved.parents for root in protected):
        raise EvaluationError("experiment output is inside a protected source tree")
    if resolved != allowed and allowed not in resolved.parents:
        raise EvaluationError(f"experiment output must be beneath {allowed}")
    os.makedirs(resolved, mode=0o700, exist_ok=True)
    if resolved.is_symlink() or not resolved.is_dir():
        raise EvaluationError("private output path must be a real directory")
    os.chmod(resolved, 0o700)
    return resolved


def private_path(root: Path, relative: str) -> Path:
    """Resolve a private member without allowing traversal or a symlink escape."""
    return base_harness.private_path(root, relative)


def assert_production_sources_clean(project_dir: Path) -> None:
    """Refuse to run after a protected production or systemd source change."""
    process = subprocess.run(
        ["git", "status", "--porcelain=v1", "--", *sorted(PROTECTED_PRODUCTION_PATHS)],
        cwd=project_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    if process.stdout.strip():
        raise EvaluationError("a protected production or prior-experiment module changed")
    units = subprocess.run(
        ["git", "status", "--porcelain=v1", "--", "*.service", "*.timer"],
        cwd=project_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    if units.stdout.strip():
        raise EvaluationError("a systemd unit changed")


def enumerate_daily_snapshot_roots(snapshot_root: Path = SNAPSHOT_ROOT) -> list[Path]:
    """Return the newest eight explicitly named retained daily snapshot roots."""
    entries = sorted(
        (
            entry
            for entry in snapshot_root.iterdir()
            if entry.is_dir() and SNAPSHOT_NAME_RE.fullmatch(entry.name)
        ),
        key=lambda entry: entry.name,
    )
    selected = entries[-8:]
    if len(selected) != 8:
        raise EvaluationError("fewer than eight retained daily snapshots are available")
    projects = [entry / "etc" / "mrsMThatcher" for entry in selected]
    if any(not project.is_dir() for project in projects):
        raise EvaluationError("a selected snapshot project root is unavailable")
    return projects


def reconstruction_tool_blob(project_dir: Path) -> bytes:
    """Read the retained reviewed history tool from its exact Git revision."""
    process = subprocess.run(
        [
            "git",
            "show",
            f"{RECONSTRUCTION_TOOL_COMMIT}:{RECONSTRUCTION_TOOL_GIT_PATH}",
        ],
        cwd=project_dir,
        check=True,
        capture_output=True,
    )
    if not process.stdout.startswith(b"#!/usr/bin/env python3\n"):
        raise EvaluationError("retained reconstruction tool blob is invalid")
    return process.stdout


def _parse_object(value: object, *, label: str) -> dict[str, Any]:
    parsed = strict_json_loads(value) if isinstance(value, (str, bytes)) else value
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be a JSON object")
    return copy.deepcopy(parsed)


def _exact_keys(value: Mapping[str, Any], expected: Iterable[str], *, label: str) -> None:
    expected_set = set(expected)
    if set(value) != expected_set:
        raise ValueError(
            f"{label} fields mismatch missing={sorted(expected_set - set(value))} "
            f"extra={sorted(set(value) - expected_set)}"
        )


def _bounded_model_string(
    value: object, *, label: str, maximum: int, allow_empty: bool = False
) -> str:
    if type(value) is not str or len(value) > maximum or (not allow_empty and not value):
        raise ValueError(f"{label} is invalid")
    if any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value):
        raise ValueError(f"{label} contains control characters")
    return value


def _normalised_turns(transcript: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(transcript):
        turn = dict(raw)
        turn_id = str(turn.get("turn_id") or "")
        text = turn.get("text")
        role = turn.get("author_role")
        if not turn_id or turn_id in seen:
            raise EvaluationError("transcript turn IDs must be non-empty and unique")
        if role not in {"account", "principal_contributor"}:
            raise EvaluationError("transcript contains a sibling or unsupported role")
        if not isinstance(text, str) or not text.strip():
            raise EvaluationError("transcript has missing visible text")
        parent = turn.get("parent_turn_id")
        if index == 0:
            if parent not in {None, ""}:
                raise EvaluationError("partial path reconstruction precedes first turn")
        elif parent != turns[-1]["turn_id"]:
            raise EvaluationError("transcript parent order is ambiguous or non-linear")
        seen.add(turn_id)
        turns.append(
            {
                "turn_id": turn_id,
                "post_id": str(turn.get("post_id") or turn_id),
                "parent_turn_id": str(parent) if parent else None,
                "author_role": role,
                "author_key": str(turn.get("author_key") or ""),
                "text": text,
                "created_at": str(turn.get("created_at") or ""),
            }
        )
    if not turns or len(turns) > MAX_PATH_TURNS:
        raise EvaluationError("transcript is empty or exceeds the 24-turn bound")
    return turns


def transcript_sha256(transcript: Sequence[Mapping[str, Any]]) -> str:
    """Hash the exact canonical detector transcript."""
    return sha256_value(_normalised_turns(transcript))


def _turn_map(transcript: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {turn["turn_id"]: turn for turn in _normalised_turns(transcript)}


def validate_repair_record(
    value: object,
    *,
    transcript: Sequence[Mapping[str, Any]],
    principal_contributor_key: str,
) -> dict[str, Any]:
    """Validate strict extraction fields, chronology, roles, and literal quotes."""
    parsed = _parse_object(value, label="repair record")
    expected = (
        "status",
        "repair_turn_id",
        "repair_evidence_quote",
        "rejected_account_turn_id",
        "rejected_answer_quote",
        "restored_issue",
        "repair_type",
        "confidence",
    )
    _exact_keys(parsed, expected, label="repair record")
    if parsed["status"] not in {
        "explicit_repair_found",
        "no_explicit_repair",
        "unclear",
    }:
        raise ValueError("repair record status is invalid")
    if parsed["confidence"] not in {"high", "medium", "low"}:
        raise ValueError("repair record confidence is invalid")
    turns = _normalised_turns(transcript)
    by_id = {turn["turn_id"]: turn for turn in turns}
    order = {turn["turn_id"]: index for index, turn in enumerate(turns)}
    restored = parsed.get("restored_issue")
    if not isinstance(restored, dict):
        raise ValueError("restored issue is not an object")
    _exact_keys(
        restored,
        ("text", "source_turn_ids", "source_quotes"),
        label="restored issue",
    )
    _bounded_model_string(restored["text"], label="restored issue text", maximum=600, allow_empty=True)
    source_ids = restored["source_turn_ids"]
    source_quotes = restored["source_quotes"]
    if (
        not isinstance(source_ids, list)
        or not isinstance(source_quotes, list)
        or len(source_ids) != len(source_quotes)
        or len(source_ids) > 3
        or len(source_ids) != len(set(source_ids))
    ):
        raise ValueError("restored issue sources are invalid")
    if parsed["status"] != "explicit_repair_found":
        empty_fields = (
            parsed["repair_turn_id"],
            parsed["repair_evidence_quote"],
            parsed["rejected_account_turn_id"],
            parsed["rejected_answer_quote"],
            restored["text"],
        )
        if any(empty_fields) or source_ids or source_quotes:
            raise ValueError("non-repair record must have empty evidence fields")
        if parsed["repair_type"] != "not_applicable":
            raise ValueError("non-repair record must use not_applicable")
        return parsed
    allowed_types = {
        "wrong_question",
        "wrong_proposition",
        "wrong_actor",
        "wrong_relation",
        "wrong_scope",
        "wrong_polarity",
        "wrong_time_or_counterfactual",
        "other",
    }
    if parsed["repair_type"] not in allowed_types:
        raise ValueError("explicit repair type is invalid")
    repair_id = _bounded_model_string(
        parsed["repair_turn_id"], label="repair turn ID", maximum=128
    )
    rejected_id = _bounded_model_string(
        parsed["rejected_account_turn_id"],
        label="rejected account turn ID",
        maximum=128,
    )
    if repair_id not in by_id or rejected_id not in by_id:
        raise ValueError("repair evidence cites a turn outside the transcript")
    repair_turn = by_id[repair_id]
    rejected_turn = by_id[rejected_id]
    if (
        repair_turn["author_role"] != "principal_contributor"
        or repair_turn["author_key"] != principal_contributor_key
    ):
        raise ValueError("repair turn is not authored by the principal contributor")
    if rejected_turn["author_role"] != "account":
        raise ValueError("rejected turn is not authored by the account")
    if order[rejected_id] >= order[repair_id]:
        raise ValueError("rejected account turn must precede the repair")
    repair_quote = _bounded_model_string(
        parsed["repair_evidence_quote"], label="repair quote", maximum=500
    )
    rejected_quote = _bounded_model_string(
        parsed["rejected_answer_quote"], label="rejected answer quote", maximum=500
    )
    if repair_quote not in repair_turn["text"]:
        raise ValueError("repair evidence quote is not a literal substring")
    if rejected_quote not in rejected_turn["text"]:
        raise ValueError("rejected answer quote is not a literal substring")
    _bounded_model_string(restored["text"], label="restored issue text", maximum=600)
    if not source_ids:
        raise ValueError("explicit repair must cite a restored-issue source turn")
    for source_id, source_quote in zip(source_ids, source_quotes):
        if source_id not in by_id or order[source_id] > order[repair_id]:
            raise ValueError("restored issue cites a sibling or future turn")
        if by_id[source_id]["author_role"] != "principal_contributor":
            raise ValueError("restored issue source is not from the principal contributor")
        if by_id[source_id]["author_key"] != principal_contributor_key:
            raise ValueError("restored issue source has a mixed contributor")
        quote = _bounded_model_string(
            source_quote, label="restored issue quote", maximum=500
        )
        if quote not in by_id[source_id]["text"]:
            raise ValueError("restored issue quote is not a literal substring")
    if repair_id != turns[-1]["turn_id"]:
        raise ValueError("repair evidence must identify the final contributor turn")
    return parsed


def repair_record_usable(record: Mapping[str, Any] | None) -> bool:
    """Return whether a validated repair can enable the backed detector."""
    return bool(
        record
        and record.get("status") == "explicit_repair_found"
        and record.get("confidence") == "high"
        and record.get("validation", {}).get("valid", True) is True
    )


def validate_detector_result(
    value: object,
    *,
    transcript: Sequence[Mapping[str, Any]],
    candidate_reply: str,
    usable_repair_record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate strict detector fields and exact candidate/turn evidence."""
    parsed = _parse_object(value, label="detector result")
    expected = (
        "status",
        "repair_turn_id",
        "rejected_account_turn_id",
        "addresses_restored_issue",
        "treatment_of_rejected_answer",
        "candidate_first_focus",
        "candidate_evidence_quote",
        "confidence",
    )
    _exact_keys(parsed, expected, label="detector result")
    enums = {
        "status": {"recurrence", "no_recurrence", "no_explicit_repair", "unclear"},
        "addresses_restored_issue": {"direct", "partial", "not_addressed", "unclear"},
        "treatment_of_rejected_answer": {
            "avoids",
            "references_to_distinguish",
            "substantially_repeats_as_answer",
            "unclear",
        },
        "candidate_first_focus": {
            "restored_issue",
            "rejected_answer",
            "other",
            "unclear",
        },
        "confidence": {"high", "medium", "low"},
    }
    for field, allowed in enums.items():
        if parsed[field] not in allowed:
            raise ValueError(f"detector {field} is invalid")
    turns = _turn_map(transcript)
    repair_id = _bounded_model_string(
        parsed["repair_turn_id"], label="detector repair ID", maximum=128, allow_empty=True
    )
    rejected_id = _bounded_model_string(
        parsed["rejected_account_turn_id"],
        label="detector rejected ID",
        maximum=128,
        allow_empty=True,
    )
    quote = _bounded_model_string(
        parsed["candidate_evidence_quote"],
        label="candidate evidence quote",
        maximum=500,
        allow_empty=True,
    )
    if parsed["status"] == "no_explicit_repair":
        if repair_id or rejected_id or quote:
            raise ValueError("no-explicit-repair detector result must have empty evidence")
        return parsed
    if repair_id not in turns or rejected_id not in turns:
        raise ValueError("detector cites a turn outside the transcript")
    ordered = list(turns)
    if (
        turns[repair_id]["author_role"] != "principal_contributor"
        or turns[rejected_id]["author_role"] != "account"
        or ordered.index(rejected_id) >= ordered.index(repair_id)
    ):
        raise ValueError("detector repair/rejected roles or order are invalid")
    if quote and quote not in candidate_reply:
        raise ValueError("candidate evidence is not a literal substring")
    if parsed["status"] == "recurrence" and not quote:
        raise ValueError("a recurrence verdict requires literal candidate evidence")
    if usable_repair_record is not None:
        if not repair_record_usable(usable_repair_record):
            raise ValueError("record-backed detector lacks a usable repair record")
        if (
            repair_id != usable_repair_record.get("repair_turn_id")
            or rejected_id != usable_repair_record.get("rejected_account_turn_id")
        ):
            raise ValueError("record-backed detector IDs differ from the repair record")
    return parsed


def narrow_recurrence_trigger(
    result: Mapping[str, Any], *, evidence_validation_passed: bool = True
) -> bool:
    """Apply the sole high-precision detector trigger without changing a reply."""
    return bool(
        evidence_validation_passed
        and result.get("status") == "recurrence"
        and result.get("confidence") == "high"
        and result.get("addresses_restored_issue") == "not_addressed"
        and result.get("treatment_of_rejected_answer")
        == "substantially_repeats_as_answer"
        and result.get("candidate_first_focus") == "rejected_answer"
    )


def _skip(source_kind: str, identity: str, reason: str) -> dict[str, str]:
    return {
        "source_kind": source_kind,
        "source_identity": identity,
        "reason": reason,
    }


def _source_text(turn: Mapping[str, Any]) -> str:
    value = turn.get("text") or turn.get("public_text")
    return value if isinstance(value, str) else ""


def _material_image_dependency(turn: Mapping[str, Any]) -> bool:
    summary = turn.get("reply_visual_context_summary")
    photo_count = (
        int(summary.get("native_photo_count_max") or 0)
        if isinstance(summary, Mapping)
        else 0
    )
    attachments = turn.get("attachments")
    has_attachment = bool(attachments) or photo_count > 0
    if not has_attachment:
        return False
    if turn.get("author_role") == "account" and _source_text(turn).strip():
        return False
    visible = turn.get("visible_media_text")
    return not isinstance(visible, str) or not visible.strip()


def _prospective_path(
    turns: Sequence[Mapping[str, Any]], repair_post_id: str
) -> tuple[list[Mapping[str, Any]] | None, str | None]:
    by_post = {
        str(turn.get("post_id")): turn
        for turn in turns
        if isinstance(turn, Mapping) and turn.get("post_id")
    }
    path: list[Mapping[str, Any]] = []
    current = repair_post_id
    seen: set[str] = set()
    while current:
        if current in seen:
            return None, "ambiguous_parentage"
        seen.add(current)
        turn = by_post.get(current)
        if turn is None:
            return None, "partial_path_reconstruction"
        path.append(turn)
        parent = turn.get("parent_post_id")
        status = turn.get("parent_observation_status")
        if parent in (None, ""):
            if status not in {None, "confirmed_none"}:
                return None, "ambiguous_parentage"
            break
        current = str(parent)
    path.reverse()
    return path, None


def _normalise_source_path(
    path: Sequence[Mapping[str, Any]], *, principal_key: str
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, turn in enumerate(path):
        role = turn.get("author_role")
        author_key = str(turn.get("author_key") or "")
        if role == "user":
            if author_key != principal_key:
                raise EvaluationError("mixed principal contributors on path")
            normal_role = "principal_contributor"
        elif role == "account":
            normal_role = "account"
        else:
            raise EvaluationError("sibling contributor occurs on authoritative path")
        turn_id = str(turn.get("turn_id") or turn.get("post_id") or "")
        result.append(
            {
                "turn_id": turn_id,
                "post_id": str(turn.get("post_id") or turn_id),
                "parent_turn_id": result[-1]["turn_id"] if index else None,
                "author_role": normal_role,
                "author_key": author_key,
                "text": _source_text(turn),
                "created_at": str(turn.get("created_at") or ""),
            }
        )
    return _normalised_turns(result)


def prospective_cases_from_conversations(
    rows: Sequence[Mapping[str, Any]], *, source_path: Path, review_pack_identity: str
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Build exact published reply-after-repair cases from frozen conversations."""
    cases: list[dict[str, Any]] = []
    skips: list[dict[str, str]] = []
    for conversation in rows:
        conversation_key = str(conversation.get("conversation_key") or "")
        turns_raw = conversation.get("turns")
        if not isinstance(turns_raw, list):
            skips.append(_skip("prospective", conversation_key, "missing_turns"))
            continue
        by_post = {
            str(turn.get("post_id")): turn
            for turn in turns_raw
            if isinstance(turn, Mapping) and turn.get("post_id")
        }
        for candidate in turns_raw:
            if not isinstance(candidate, Mapping) or candidate.get("author_role") != "account":
                continue
            parent_id = str(candidate.get("parent_post_id") or "")
            repair = by_post.get(parent_id)
            if not repair or repair.get("author_role") != "user":
                continue
            if not explicit_repair_prefilter(_source_text(repair)):
                continue
            identity = f"{conversation_key}:{parent_id}:{candidate.get('post_id')}"
            if (
                candidate.get("publication_status") != "published"
                or not candidate.get("publication_evidence")
                or not _source_text(candidate).strip()
            ):
                skips.append(_skip("prospective", identity, "unproved_account_reply"))
                continue
            if candidate.get("reconstruction_confidence") not in {None, "high"}:
                skips.append(_skip("prospective", identity, "partial_reconstruction"))
                continue
            path, reason = _prospective_path(turns_raw, parent_id)
            if path is None:
                skips.append(_skip("prospective", identity, str(reason)))
                continue
            if len(path) > MAX_PATH_TURNS:
                skips.append(_skip("prospective", identity, "more_than_24_path_turns"))
                continue
            principal_key = str(repair.get("author_key") or "")
            if not principal_key:
                skips.append(_skip("prospective", identity, "missing_principal_identity"))
                continue
            if any(
                turn.get("account_graph_ambiguous_fields")
                or turn.get("account_graph_conflicts")
                or turn.get("account_content_conflicts")
                for turn in path
            ):
                skips.append(_skip("prospective", identity, "ambiguous_parentage"))
                continue
            if any(_material_image_dependency(turn) for turn in (*path, candidate)):
                skips.append(_skip("prospective", identity, "unavailable_visual_premise"))
                continue
            try:
                transcript = _normalise_source_path(path, principal_key=principal_key)
            except EvaluationError as exc:
                reason_text = (
                    "mixed_principal_contributors"
                    if "mixed" in str(exc)
                    else "sibling_branch_contamination"
                )
                skips.append(_skip("prospective", identity, reason_text))
                continue
            if transcript[-1]["post_id"] != parent_id:
                skips.append(_skip("prospective", identity, "future_turn_leakage"))
                continue
            prior_account = [turn for turn in transcript[:-1] if turn["author_role"] == "account"]
            if not prior_account:
                skips.append(_skip("prospective", identity, "missing_rejected_account_answer"))
                continue
            transcript_hash = sha256_value(transcript)
            candidate_text = _source_text(candidate)
            candidate_hash = sha256_bytes(candidate_text.encode("utf-8"))
            case_identity = sha256_value(
                {
                    "source": review_pack_identity,
                    "conversation": conversation_key,
                    "repair": parent_id,
                    "candidate": str(candidate.get("post_id")),
                }
            )
            cases.append(
                {
                    "case_id": f"recurrence:prospective:{case_identity[:24]}",
                    "source_kind": "prospective_explicit_repair_candidate",
                    "source_priority": 1,
                    "source_path": str(source_path),
                    "source_snapshot_identity": review_pack_identity,
                    "conversation_identity": str(conversation.get("conversation_id") or conversation_key),
                    "branch_identity": sha256_value([turn["post_id"] for turn in transcript]),
                    "principal_contributor_key": principal_key,
                    "repair_candidate_turn_id": transcript[-1]["turn_id"],
                    "candidate_account_reply_id": str(candidate.get("post_id") or ""),
                    "candidate_identity": str(candidate.get("post_id") or ""),
                    "transcript": transcript,
                    "candidate_reply": candidate_text,
                    "source_evidence": {
                        "publication_authority": candidate.get("publication_authority"),
                        "publication_evidence": copy.deepcopy(candidate.get("publication_evidence")),
                        "conversation_reconstruction_confidence": conversation.get(
                            "reconstruction_confidence"
                        ),
                    },
                    "reconstruction_confidence": "high",
                    "transcript_sha256": transcript_hash,
                    "candidate_sha256": candidate_hash,
                    "carried_forward_human_label": None,
                    "plausible_explicit_repair_candidate": True,
                    "liberty_case": bool(
                        re.search(
                            r"economic (?:liberty|freedom)|non-economic (?:liberty|freedom)",
                            " ".join(turn["text"] for turn in transcript),
                            re.IGNORECASE,
                        )
                    ),
                }
            )
    return cases, skips


def _embedded_posts(message: object) -> list[dict[str, Any]]:
    if not isinstance(message, str):
        return []
    prefixes = (
        "Parent chain: ",
        "Mentions returned: ",
        "Fetched tweet: ",
        "X response json: ",
    )
    prefix = next((item for item in prefixes if message.startswith(item)), None)
    if prefix is None:
        return []
    try:
        document = json.loads(message[len(prefix) :])
    except (json.JSONDecodeError, TypeError):
        return []
    if prefix == "X response json: " and isinstance(document, dict):
        document = document.get("data")
    candidates = document if isinstance(document, list) else [document]
    return [
        copy.deepcopy(item)
        for item in candidates
        if isinstance(item, dict)
        and item.get("id")
        and item.get("author_id")
        and isinstance(item.get("text"), str)
    ]


_HISTORY_CONTEXT_PARENT_RE = re.compile(
    r"^Built AI reply context for mention (?P<target>\d+)\. "
    r"chain_items=\d+ immediate_parent=(?P<parent>\d+) quoted=(?:True|False)$"
)


def _merge_history_post(
    posts: dict[str, dict[str, Any]], observed: Mapping[str, Any]
) -> None:
    post_id = str(observed.get("post_id") or "")
    if not post_id:
        return
    candidate = copy.deepcopy(dict(observed))
    candidate.setdefault("post_id", post_id)
    candidate.setdefault("turn_id", post_id)
    candidate.setdefault("author_id", "")
    candidate.setdefault("text", "")
    candidate.setdefault("parent_post_id", None)
    candidate.setdefault("parent_observed", False)
    candidate.setdefault("created_at", "")
    candidate.setdefault("conversation_id", "")
    candidate.setdefault("attachments", None)
    candidate.setdefault("evidence_record_ids", [])
    candidate.setdefault("conflict", False)
    prior = posts.get(post_id)
    if prior is None:
        posts[post_id] = candidate
        return
    for field in ("author_id", "text", "parent_post_id"):
        left, right = prior.get(field), candidate.get(field)
        if left not in {None, ""} and right not in {None, ""} and left != right:
            prior["conflict"] = True
        elif left in {None, ""} and right not in {None, ""}:
            prior[field] = right
    for field in ("created_at", "conversation_id", "attachments"):
        if prior.get(field) in (None, "") and candidate.get(field) not in (None, ""):
            prior[field] = copy.deepcopy(candidate[field])
    prior["parent_observed"] = bool(
        prior.get("parent_observed") or candidate.get("parent_observed")
    )
    prior["conflict"] = bool(prior.get("conflict") or candidate.get("conflict"))
    prior["evidence_record_ids"] = sorted(
        {
            str(item)
            for item in (
                list(prior.get("evidence_record_ids") or [])
                + list(candidate.get("evidence_record_ids") or [])
            )
            if item
        }
    )


def _history_post_observations(
    path: Path, candidate_rows: Sequence[Mapping[str, Any]] = ()
) -> dict[str, dict[str, Any]]:
    posts: dict[str, dict[str, Any]] = {}
    for row in candidate_rows:
        target_id = str(row.get("target_id") or "")
        incoming = row.get("incoming_text")
        author_id = str(row.get("author_id") or "")
        if target_id and author_id and isinstance(incoming, str) and incoming:
            _merge_history_post(
                posts,
                {
                    "post_id": target_id,
                    "author_id": author_id,
                    "text": incoming,
                    "parent_post_id": None,
                    "parent_observed": row.get("lane") == "quote-tweet",
                    "created_at": str(row.get("first_timestamp") or ""),
                    "conversation_id": str(row.get("thread_id") or ""),
                    "evidence_record_ids": list(row.get("source_record_ids") or []),
                },
            )
        if (
            row.get("outcome") == "posted"
            and row.get("reconstruction_status") == "complete"
            and isinstance(row.get("actual_reply_text"), str)
            and row.get("actual_reply_text")
            and row.get("reply_post_id")
            and row.get("final_outcome_evidence_id")
        ):
            _merge_history_post(
                posts,
                {
                    "post_id": str(row["reply_post_id"]),
                    "author_id": ACCOUNT_ID,
                    "text": str(row["actual_reply_text"]),
                    "parent_post_id": target_id,
                    "parent_observed": bool(target_id),
                    "created_at": str(row.get("terminal_timestamp") or ""),
                    "conversation_id": str(row.get("thread_id") or ""),
                    "evidence_record_ids": sorted(
                        {
                            str(item)
                            for item in (
                                list(row.get("source_record_ids") or [])
                                + [str(row["final_outcome_evidence_id"])]
                            )
                            if item
                        }
                    ),
                },
            )
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            message = row.get("message")
            parent_match = (
                _HISTORY_CONTEXT_PARENT_RE.fullmatch(message)
                if isinstance(message, str)
                else None
            )
            if parent_match:
                target_id = parent_match.group("target")
                _merge_history_post(
                    posts,
                    {
                        "post_id": target_id,
                        "parent_post_id": parent_match.group("parent"),
                        "parent_observed": True,
                        "evidence_record_ids": [str(row.get("record_id") or "")],
                    },
                )
            for post in _embedded_posts(row.get("message")):
                post_id = str(post["id"])
                parents = [
                    str(ref.get("id"))
                    for ref in post.get("referenced_tweets") or []
                    if isinstance(ref, Mapping)
                    and ref.get("type") == "replied_to"
                    and ref.get("id")
                ]
                parent = parents[0] if len(set(parents)) == 1 else None
                observed = {
                    "post_id": post_id,
                    "turn_id": post_id,
                    "author_id": str(post.get("author_id")),
                    "text": str(post.get("text")),
                    "parent_post_id": parent,
                    "parent_observed": not parents or parent is not None,
                    "created_at": str(post.get("created_at") or ""),
                    "conversation_id": str(post.get("conversation_id") or ""),
                    "attachments": copy.deepcopy(post.get("attachments")),
                    "evidence_record_ids": [str(row.get("record_id") or "")],
                    "conflict": False,
                }
                _merge_history_post(posts, observed)
    return posts


def _history_ancestor_path(
    posts: Mapping[str, Mapping[str, Any]], repair_id: str
) -> tuple[list[Mapping[str, Any]] | None, str | None]:
    path: list[Mapping[str, Any]] = []
    current = repair_id
    seen: set[str] = set()
    while current:
        if current in seen:
            return None, "ambiguous_parentage"
        seen.add(current)
        post = posts.get(current)
        if post is None:
            return None, "partial_path_reconstruction"
        if post.get("conflict") or post.get("parent_observed") is False:
            return None, "ambiguous_parentage"
        path.append(post)
        current = str(post.get("parent_post_id") or "")
    path.reverse()
    return path, None


def history_cases_from_reconstruction(
    history_root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], dict[str, Any]]:
    """Recover exact, parent-linked published replies from reconstruction evidence."""
    log_path = history_root / "unique_log_records.jsonl"
    candidate_path = history_root / "conversational_candidates.jsonl"
    manifest_path = history_root / "run_manifest.json"
    if not (log_path.is_file() and candidate_path.is_file() and manifest_path.is_file()):
        return [], [_skip("historical", str(history_root), "history_reconstruction_unavailable")], {}
    candidate_rows = read_jsonl(candidate_path)
    posts = _history_post_observations(log_path, candidate_rows)
    cases: list[dict[str, Any]] = []
    skips: list[dict[str, str]] = []
    for row in candidate_rows:
        incoming = row.get("incoming_text")
        if not explicit_repair_prefilter(incoming):
            continue
        identity = str(row.get("candidate_id") or row.get("target_id") or "unknown")
        if (
            row.get("outcome") != "posted"
            or row.get("reconstruction_status") != "complete"
            or not isinstance(row.get("actual_reply_text"), str)
            or not row.get("actual_reply_text")
            or not row.get("reply_post_id")
            or not row.get("final_outcome_evidence_id")
        ):
            skips.append(_skip("historical", identity, "unproved_account_reply"))
            continue
        repair_id = str(row.get("target_id") or "")
        post = posts.get(repair_id)
        if post is None or post.get("text") != incoming:
            skips.append(_skip("historical", identity, "missing_repair_turn_text"))
            continue
        path, reason = _history_ancestor_path(posts, repair_id)
        if path is None:
            skips.append(_skip("historical", identity, str(reason)))
            continue
        if len(path) > MAX_PATH_TURNS:
            skips.append(_skip("historical", identity, "more_than_24_path_turns"))
            continue
        principal = str(post.get("author_id") or "")
        if principal == ACCOUNT_ID or not principal:
            skips.append(_skip("historical", identity, "conflicting_parent_identity"))
            continue
        if any(
            str(turn.get("author_id")) not in {ACCOUNT_ID, principal} for turn in path
        ):
            skips.append(_skip("historical", identity, "mixed_principal_contributors"))
            continue
        if any(_material_image_dependency(turn) for turn in path):
            skips.append(_skip("historical", identity, "unavailable_visual_premise"))
            continue
        if not any(
            str(turn.get("author_id")) == ACCOUNT_ID for turn in path[:-1]
        ):
            skips.append(_skip("historical", identity, "missing_rejected_account_answer"))
            continue
        normalised: list[dict[str, Any]] = []
        for index, turn in enumerate(path):
            normalised.append(
                {
                    "turn_id": str(turn["post_id"]),
                    "post_id": str(turn["post_id"]),
                    "parent_turn_id": normalised[-1]["turn_id"] if index else None,
                    "author_role": (
                        "account"
                        if str(turn.get("author_id")) == ACCOUNT_ID
                        else "principal_contributor"
                    ),
                    "author_key": (
                        "account" if str(turn.get("author_id")) == ACCOUNT_ID else principal
                    ),
                    "text": str(turn.get("text") or ""),
                    "created_at": str(turn.get("created_at") or ""),
                }
            )
        try:
            transcript = _normalised_turns(normalised)
        except EvaluationError:
            skips.append(_skip("historical", identity, "invalid_exact_path"))
            continue
        candidate_text = str(row["actual_reply_text"])
        case_identity = sha256_value(
            {
                "history_manifest": sha256_file(manifest_path),
                "repair": repair_id,
                "candidate": str(row["reply_post_id"]),
            }
        )
        transcript_text = " ".join(turn["text"] for turn in transcript)
        cases.append(
            {
                "case_id": f"recurrence:historical:{case_identity[:24]}",
                "source_kind": "historical_explicit_repair_candidate",
                "source_priority": 0,
                "source_path": str(history_root),
                "source_snapshot_identity": "history-reconstruction-newest-eight-plus-live",
                "conversation_identity": str(post.get("conversation_id") or ""),
                "branch_identity": sha256_value([turn["post_id"] for turn in transcript]),
                "principal_contributor_key": principal,
                "repair_candidate_turn_id": transcript[-1]["turn_id"],
                "candidate_account_reply_id": str(row["reply_post_id"]),
                "candidate_identity": str(row["reply_post_id"]),
                "transcript": transcript,
                "candidate_reply": candidate_text,
                "source_evidence": {
                    "candidate_id": identity,
                    "final_outcome_evidence_id": row["final_outcome_evidence_id"],
                    "source_record_ids": copy.deepcopy(row.get("source_record_ids") or []),
                    "path_record_ids": sorted(
                        {
                            str(record_id)
                            for turn in path
                            for record_id in turn.get("evidence_record_ids") or []
                            if record_id
                        }
                    ),
                },
                "reconstruction_confidence": "high",
                "transcript_sha256": sha256_value(transcript),
                "candidate_sha256": sha256_bytes(candidate_text.encode("utf-8")),
                "carried_forward_human_label": None,
                "plausible_explicit_repair_candidate": True,
                "liberty_case": bool(
                    re.search(
                        r"economic (?:liberty|freedom)|non-economic (?:liberty|freedom)",
                        transcript_text,
                        re.IGNORECASE,
                    )
                ),
            }
        )
    manifest = read_json(manifest_path)
    return cases, skips, manifest if isinstance(manifest, dict) else {}


def load_qud_negative_controls(
    root: Path = PREVIOUS_EXPERIMENT_ROOT,
) -> list[dict[str, Any]]:
    """Carry forward exactly seven reviewed Arm A outcomes as private negatives."""
    manifest = read_json(root / "manifest.json")
    paid = manifest.get("paid_comparison") if isinstance(manifest, dict) else None
    paid_ids = paid.get("case_ids") if isinstance(paid, dict) else None
    if not isinstance(paid_ids, list) or len(paid_ids) != 7:
        raise EvaluationError("previous QUD paid case identity is unavailable")
    case_map = {str(row.get("case_id")): row for row in read_jsonl(root / "cases.jsonl")}
    arm_a = {
        str(row.get("case_id")): row
        for row in read_jsonl(root / "arm_results.jsonl")
        if row.get("arm") == "A"
    }
    controls: list[dict[str, Any]] = []
    for prior_id in paid_ids:
        prior = case_map.get(str(prior_id))
        result = arm_a.get(str(prior_id))
        if not prior or not result or not isinstance(result.get("final_public_candidate"), str):
            raise EvaluationError("a reviewed QUD negative control is incomplete")
        principal_key = str(prior.get("principal_contributor_key") or "")
        normalised: list[dict[str, Any]] = []
        for index, raw in enumerate(prior.get("issue_transcript") or []):
            role = raw.get("author_role")
            normalised.append(
                {
                    "turn_id": str(raw.get("turn_id") or ""),
                    "post_id": str(raw.get("turn_id") or ""),
                    "parent_turn_id": normalised[-1]["turn_id"] if index else None,
                    "author_role": (
                        "principal_contributor" if role == "user" else "account"
                    ),
                    "author_key": principal_key if role == "user" else "account",
                    "text": str(raw.get("text") or ""),
                    "created_at": "",
                }
            )
        transcript = _normalised_turns(normalised)
        candidate = result["final_public_candidate"]
        identity = sha256_value(
            {
                "prior_case_id": prior_id,
                "prior_input_hash": prior.get("candidate_identity_sha256"),
                "arm_a_hash": result.get("final_public_candidate_sha256"),
            }
        )
        controls.append(
            {
                "case_id": f"recurrence:qud-negative:{identity[:24]}",
                "source_kind": "qud_human_reviewed_negative_control",
                "source_priority": 3,
                "source_path": str(root),
                "source_snapshot_identity": str(
                    prior.get("review_pack_identity") or "qud-ledger-shadow-eval-20260829"
                ),
                "conversation_identity": str(prior.get("branch_key") or ""),
                "branch_identity": str(prior.get("branch_key") or ""),
                "principal_contributor_key": principal_key,
                "repair_candidate_turn_id": transcript[-1]["turn_id"],
                "candidate_account_reply_id": (
                    f"arm-a:{result.get('final_public_candidate_sha256')}"
                ),
                "candidate_identity": (
                    f"arm-a:{result.get('final_public_candidate_sha256')}"
                ),
                "transcript": transcript,
                "candidate_reply": candidate,
                "source_evidence": {
                    "previous_case_id": prior_id,
                    "previous_candidate_identity_sha256": prior.get(
                        "candidate_identity_sha256"
                    ),
                    "previous_issue_transcript_sha256": prior.get(
                        "issue_transcript_sha256"
                    ),
                    "previous_arm_a_candidate_sha256": result.get(
                        "final_public_candidate_sha256"
                    ),
                },
                "reconstruction_confidence": "retained_exact_input",
                "transcript_sha256": sha256_value(transcript),
                "candidate_sha256": sha256_bytes(candidate.encode("utf-8")),
                "carried_forward_human_label": {
                    "explicit_recurrence": "no",
                    "proposition_substitution": "no",
                    "label_source": "completed human review, 2026-08-29",
                },
                "plausible_explicit_repair_candidate": False,
                "liberty_case": False,
            }
        )
    return controls


def prior_research_fixture_skips() -> list[dict[str, str]]:
    """Record repair-like fixtures that lack an exact rejected answer and publication."""
    sources = (
        ("prior_grok_evaluation_fixture", GROK_EVAL_ROOT / "cases.jsonl"),
        ("prior_writer_evaluation_fixture", WRITER_EVAL_ROOT / "writer_cases.jsonl"),
    )
    skips: list[dict[str, str]] = []
    for source_kind, path in sources:
        if not path.is_file():
            continue
        for row in read_jsonl(path):
            context = row.get("context")
            incoming = (
                context.get("incoming_contribution")
                if isinstance(context, Mapping)
                else None
            )
            if explicit_repair_prefilter(incoming):
                skips.append(
                    _skip(
                        source_kind,
                        str(
                            row.get("case_id")
                            or row.get("canonical_target_identity")
                            or path
                        ),
                        "missing_rejected_account_answer_text",
                    )
                )
    return skips


def select_corpus(
    cases: Sequence[Mapping[str, Any]], *, maximum: int = MAX_CASES
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Deduplicate, sort deterministically by source priority and cap at 30."""
    if maximum < 1 or maximum > MAX_CASES:
        raise EvaluationError("corpus cap must be between one and 30")
    ordered = sorted(
        (copy.deepcopy(dict(case)) for case in cases),
        key=lambda case: (
            int(
                case["source_priority"]
                if case.get("source_priority") is not None
                else 99
            ),
            sha256_value(
                {
                    "source": case.get("source_snapshot_identity"),
                    "case": case.get("case_id"),
                    "transcript": case.get("transcript_sha256"),
                    "candidate": case.get("candidate_sha256"),
                }
            ),
        ),
    )
    selected: list[dict[str, Any]] = []
    skips: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for case in ordered:
        key = (str(case.get("transcript_sha256")), str(case.get("candidate_sha256")))
        if key in seen:
            skips.append(_skip(str(case.get("source_kind")), str(case.get("case_id")), "duplicate_exact_input"))
            continue
        seen.add(key)
        if len(selected) >= maximum:
            skips.append(_skip(str(case.get("source_kind")), str(case.get("case_id")), "corpus_cap"))
            continue
        case["selection_index"] = len(selected) + 1
        selected.append(case)
    return selected, skips


def provider_case_payload(case: Mapping[str, Any]) -> dict[str, Any]:
    """Return exact public inputs while excluding every human label and provenance."""
    return {
        "case_id": str(case["case_id"]),
        "principal_contributor_key": str(case["principal_contributor_key"]),
        "repair_candidate_turn_id": str(case["repair_candidate_turn_id"]),
        "transcript": copy.deepcopy(case["transcript"]),
        "candidate_reply": str(case["candidate_reply"]),
    }


class RecurrenceRequestLedger(writer_harness.WriterRequestLedger):
    """Identity-bound request ledger with the fixed US$10 ceiling."""

    def __init__(
        self,
        path: Path,
        *,
        case_set_sha256: str,
        hard_limit_usd: float = GLOBAL_COST_CEILING_USD,
    ) -> None:
        """Open or create the recurrence experiment ledger."""
        self.path = path
        self.limit_ticks = int(
            round(hard_limit_usd * base_harness.USD_TICKS_PER_DOLLAR)
        )
        maximum = int(
            GLOBAL_COST_CEILING_USD * base_harness.USD_TICKS_PER_DOLLAR
        )
        if self.limit_ticks <= 0 or self.limit_ticks > maximum:
            raise EvaluationError("global provider-cost ceiling must be in (0, 10.00]")
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


class ProviderClient:
    """Reuse the existing strict cached synchronous transport for three call types."""

    _CONSUMER_ARMS = {
        "extractor": "A",
        "transcript_only": "B",
        "repair_record_backed": "C",
    }

    def __init__(
        self,
        *,
        consumer: str,
        case_id: str,
        ledger: RecurrenceRequestLedger | None,
        response_dir: Path | None,
        api_keys: Mapping[str, str] | None,
        xai_model_metadata: Mapping[str, Mapping[str, Any]] | None,
        post: Callable[..., Any] = requests.post,
        sleep: Callable[[float], None] = time.sleep,
        live_request: Callable[..., tuple[object, dict[str, Any]]] | None = None,
    ) -> None:
        """Bind one logical consumer to the shared canonical request cache."""
        if consumer not in self._CONSUMER_ARMS:
            raise EvaluationError(f"unknown request consumer: {consumer}")
        self.consumer = consumer
        self.case_id = str(case_id)
        self.call_events: list[dict[str, Any]] = []
        self._delegate = writer_harness.WriterPipelineTransport(
            arm=self._CONSUMER_ARMS[consumer],
            case_id=self.case_id,
            prior_cache=object(),
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
        """Serve one complete canonical request from cache or one bounded send."""
        allowed = {
            "xAI": {(EXTRACTION_MODEL, EXTRACTION_EFFORT)},
            "OpenAI": {(DETECTOR_MODEL, DETECTOR_EFFORT)},
        }
        if provider not in allowed or (model, reasoning_effort) not in allowed[provider]:
            raise EvaluationError(
                f"unapproved provider identity: {provider}/{model}/{reasoning_effort}"
            )
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
        event["consumer"] = self.consumer
        event["tools_enabled"] = False
        self.call_events.append(copy.deepcopy(event))
        return content, event


def fetch_xai_model_availability(
    *, api_key: str, get: Callable[..., Any] = requests.get
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Verify only Grok 4.6 and retain its authenticated pricing metadata."""
    if not api_key:
        raise EvaluationError("XAI_API_KEY is required for live execution")
    url = f"{XAI_BASE_URL}/models"
    validate_provider_url(url, expected_host=XAI_HOST)
    try:
        response = get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
            allow_redirects=False,
        )
    except BaseException as exc:
        raise ModelAvailabilityError(
            f"xAI models endpoint failed: {type(exc).__name__}: {exc}"
        ) from exc
    if int(getattr(response, "status_code", 0) or 0) != 200:
        raise ModelAvailabilityError(
            f"xAI models endpoint returned HTTP {getattr(response, 'status_code', 0)}"
        )
    try:
        document = response.json()
    except BaseException as exc:
        raise ModelAvailabilityError("xAI models endpoint returned invalid JSON") from exc
    rows = document.get("data") if isinstance(document, dict) else None
    selected = next(
        (
            row
            for row in rows or []
            if isinstance(row, dict) and row.get("id") == EXTRACTION_MODEL
        ),
        None,
    )
    price_fields = (
        "prompt_text_token_price",
        "cached_prompt_text_token_price",
        "completion_text_token_price",
    )
    if selected is None or any(
        type(selected.get(field)) is not int or selected[field] <= 0
        for field in price_fields
    ):
        raise ModelAvailabilityError("authenticated Grok 4.6 availability failed")
    metadata = {
        EXTRACTION_MODEL: {
            "id": EXTRACTION_MODEL,
            **{field: selected[field] for field in price_fields},
            "retrieved_at": utc_now(),
            "usd_ticks_per_dollar": base_harness.USD_TICKS_PER_DOLLAR,
        }
    }
    return {"id": EXTRACTION_MODEL, "retrieved_at": utc_now()}, metadata


def verify_model_availability(
    *, api_keys: Mapping[str, str], get: Callable[..., Any] = requests.get
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Verify the two fixed research models before an inference request."""
    if not api_keys.get("xAI") or not api_keys.get("OpenAI"):
        raise EvaluationError("both provider API keys are required for live execution")
    xai, metadata = fetch_xai_model_availability(api_key=api_keys["xAI"], get=get)
    openai = qud_harness.fetch_openai_model_availability(
        api_key=api_keys["OpenAI"], get=get
    )
    return (
        {
            "checked_at": utc_now(),
            "endpoint_call_count": 2,
            "xAI": [xai["id"]],
            "OpenAI": [openai["id"]],
            "all_required_available": True,
        },
        metadata,
    )


def load_api_keys(env_file: Path | None = None) -> dict[str, str]:
    """Reuse the established local environment convention without exposing keys."""
    return base_harness.load_api_keys(env_file)


def extract_explicit_repair(
    *, case: Mapping[str, Any], client: ProviderClient
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Make and validate one constrained Grok repair-extraction request."""
    public = provider_case_payload(case)
    payload = {
        "principal_contributor_key": public["principal_contributor_key"],
        "repair_candidate_turn_id": public["repair_candidate_turn_id"],
        "transcript": public["transcript"],
    }
    content, event = client.request(
        provider="xAI",
        stage="explicit_repair_extraction",
        model=EXTRACTION_MODEL,
        reasoning_effort=EXTRACTION_EFFORT,
        system_prompt=EXTRACTION_PROMPT,
        payload=payload,
        response_schema=REPAIR_RECORD_SCHEMA,
        timeout_seconds=180,
        max_output_tokens=900,
    )
    parsed = validate_repair_record(
        content,
        transcript=case["transcript"],
        principal_contributor_key=str(case["principal_contributor_key"]),
    )
    return parsed, event


def run_detector(
    *,
    case: Mapping[str, Any],
    condition: str,
    client: ProviderClient,
    repair_record: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one transcript-only or usable-record-backed detector condition."""
    if condition not in CONDITIONS:
        raise EvaluationError(f"unknown detector condition: {condition}")
    if condition == "repair_record_backed" and not repair_record_usable(repair_record):
        raise EvaluationError("record-backed detector requires a usable repair record")
    public = provider_case_payload(case)
    payload = {
        "principal_contributor_key": public["principal_contributor_key"],
        "repair_candidate_turn_id": public["repair_candidate_turn_id"],
        "transcript": public["transcript"],
        "candidate_reply": public["candidate_reply"],
    }
    if condition == "repair_record_backed":
        payload["repair_record"] = copy.deepcopy(dict(repair_record or {}))
    content, event = client.request(
        provider="OpenAI",
        stage=f"rejected_answer_recurrence_{condition}",
        model=DETECTOR_MODEL,
        reasoning_effort=DETECTOR_EFFORT,
        system_prompt=DETECTOR_PROMPT,
        payload=payload,
        response_schema=DETECTOR_SCHEMA,
        timeout_seconds=180,
        max_output_tokens=700,
    )
    parsed = validate_detector_result(
        content,
        transcript=case["transcript"],
        candidate_reply=str(case["candidate_reply"]),
        usable_repair_record=(
            repair_record if condition == "repair_record_backed" else None
        ),
    )
    return parsed, event


def create_or_load_detector_key(path: Path) -> dict[str, str]:
    """Create one random global P/Q mapping and keep it in its sole private file."""
    if path.exists():
        value = read_json(path)
        if (
            not isinstance(value, dict)
            or set(value) != set(BLIND_DETECTOR_LABELS)
            or set(value.values()) != set(CONDITIONS)
        ):
            raise EvaluationError("detector key is invalid")
        return {str(key): str(item) for key, item in value.items()}
    order = list(CONDITIONS)
    if secrets.randbelow(2):
        order.reverse()
    value = dict(zip(BLIND_DETECTOR_LABELS, order))
    atomic_json(path, value)
    return value


def _load_unique_rows(path: Path, fields: Sequence[str]) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = read_jsonl(path)
    identities = [tuple(str(row.get(field)) for field in fields) for row in rows]
    if len(identities) != len(set(identities)):
        raise EvaluationError(f"duplicate resumable rows in {path.name}")
    return rows


def _persist_execution_rows(
    output_dir: Path,
    repair_rows: Sequence[Mapping[str, Any]],
    detector_rows: Sequence[Mapping[str, Any]],
) -> None:
    atomic_jsonl(
        private_path(output_dir, "repair_records.jsonl"),
        sorted(repair_rows, key=lambda row: str(row.get("case_id"))),
    )
    atomic_jsonl(
        private_path(output_dir, "detector_results.jsonl"),
        sorted(
            detector_rows,
            key=lambda row: (str(row.get("case_id")), str(row.get("condition"))),
        ),
    )


def execute_experiment(
    *,
    output_dir: Path,
    manifest: Mapping[str, Any],
    cases: Sequence[Mapping[str, Any]],
    api_keys: Mapping[str, str],
    post: Callable[..., Any] = requests.post,
    get: Callable[..., Any] = requests.get,
    sleep: Callable[[float], None] = time.sleep,
    live_request: Callable[..., tuple[object, dict[str, Any]]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], RecurrenceRequestLedger, dict[str, Any], str | None]:
    """Run extraction then both detector conditions with durable resumption."""
    plausible = [
        case for case in cases if case.get("plausible_explicit_repair_candidate") is True
    ]
    if not plausible:
        raise EvaluationError("negative-only efficacy trial is prohibited")
    availability, xai_metadata = verify_model_availability(api_keys=api_keys, get=get)
    ledger = RecurrenceRequestLedger(
        private_path(output_dir, "request_ledger.json"),
        case_set_sha256=str(manifest["case_set_sha256"]),
    )
    response_dir = private_path(output_dir, "responses")
    os.makedirs(response_dir, mode=0o700, exist_ok=True)
    os.chmod(response_dir, 0o700)
    repair_rows = _load_unique_rows(
        private_path(output_dir, "repair_records.jsonl"), ("case_id",)
    )
    detector_rows = _load_unique_rows(
        private_path(output_dir, "detector_results.jsonl"), ("case_id", "condition")
    )
    repairs = {str(row.get("case_id")): row for row in repair_rows}
    detectors = {
        (str(row.get("case_id")), str(row.get("condition"))): row
        for row in detector_rows
    }
    blocker: str | None = None
    for case in cases:
        case_id = str(case["case_id"])
        if case_id not in repairs:
            started = time.monotonic()
            client = ProviderClient(
                consumer="extractor",
                case_id=case_id,
                ledger=ledger,
                response_dir=response_dir,
                api_keys=api_keys,
                xai_model_metadata=xai_metadata,
                post=post,
                sleep=sleep,
                live_request=live_request,
            )
            row: dict[str, Any] = {
                "case_id": case_id,
                "status": "failed",
                "result": None,
                "validation": {"valid": False, "error": None},
                "usable": False,
                "request": None,
                "latency_seconds": None,
                "provider_failure": None,
                "schema_failure": None,
                "ambiguous_failure": None,
            }
            try:
                result, event = extract_explicit_repair(case=case, client=client)
                row.update(
                    {
                        "status": "completed",
                        "result": result,
                        "validation": {"valid": True, "error": None},
                        "usable": repair_record_usable(result),
                        "request": event,
                    }
                )
            except AmbiguousRequestError as exc:
                row["ambiguous_failure"] = f"{type(exc).__name__}: {exc}"
                blocker = row["ambiguous_failure"]
            except ProviderError as exc:
                row["provider_failure"] = f"{type(exc).__name__}: {exc}"
            except (ValueError, json.JSONDecodeError) as exc:
                row["schema_failure"] = f"{type(exc).__name__}: {exc}"
            except CostLimitReached as exc:
                blocker = f"{type(exc).__name__}: {exc}"
            row["latency_seconds"] = round(time.monotonic() - started, 6)
            repairs[case_id] = row
            repair_rows = list(repairs.values())
            _persist_execution_rows(output_dir, repair_rows, list(detectors.values()))
            if blocker:
                break
        repair_row = repairs[case_id]
        validated_record = (
            repair_row.get("result") if repair_row.get("usable") is True else None
        )
        for condition in CONDITIONS:
            key = (case_id, condition)
            if key in detectors:
                continue
            if condition == "repair_record_backed" and validated_record is None:
                detectors[key] = {
                    "case_id": case_id,
                    "condition": condition,
                    "status": "no_usable_repair_record",
                    "result": None,
                    "evidence_validation": {"valid": False, "error": "no_usable_repair_record"},
                    "narrow_trigger": False,
                    "request": None,
                    "latency_seconds": 0.0,
                    "provider_failure": None,
                    "schema_failure": None,
                    "ambiguous_failure": None,
                }
                _persist_execution_rows(output_dir, list(repairs.values()), list(detectors.values()))
                continue
            started = time.monotonic()
            client = ProviderClient(
                consumer=condition,
                case_id=case_id,
                ledger=ledger,
                response_dir=response_dir,
                api_keys=api_keys,
                xai_model_metadata=xai_metadata,
                post=post,
                sleep=sleep,
                live_request=live_request,
            )
            result_row: dict[str, Any] = {
                "case_id": case_id,
                "condition": condition,
                "status": "failed",
                "result": None,
                "evidence_validation": {"valid": False, "error": None},
                "narrow_trigger": False,
                "request": None,
                "latency_seconds": None,
                "provider_failure": None,
                "schema_failure": None,
                "ambiguous_failure": None,
            }
            try:
                result, event = run_detector(
                    case=case,
                    condition=condition,
                    client=client,
                    repair_record=validated_record,
                )
                result_row.update(
                    {
                        "status": "completed",
                        "result": result,
                        "evidence_validation": {"valid": True, "error": None},
                        "narrow_trigger": narrow_recurrence_trigger(result),
                        "request": event,
                    }
                )
            except AmbiguousRequestError as exc:
                result_row["ambiguous_failure"] = f"{type(exc).__name__}: {exc}"
                blocker = result_row["ambiguous_failure"]
            except ProviderError as exc:
                result_row["provider_failure"] = f"{type(exc).__name__}: {exc}"
            except (ValueError, json.JSONDecodeError) as exc:
                result_row["schema_failure"] = f"{type(exc).__name__}: {exc}"
            except CostLimitReached as exc:
                blocker = f"{type(exc).__name__}: {exc}"
            result_row["latency_seconds"] = round(time.monotonic() - started, 6)
            detectors[key] = result_row
            _persist_execution_rows(output_dir, list(repairs.values()), list(detectors.values()))
            if blocker:
                break
        if blocker:
            break
    return list(repairs.values()), list(detectors.values()), ledger, availability, blocker


def _review_pack_identity(review_pack: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest_path = review_pack / "manifest.json"
    conversations_path = review_pack / "conversations.jsonl"
    if not manifest_path.is_file() or not conversations_path.is_file():
        raise EvaluationError("frozen prospective review pack is incomplete")
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise EvaluationError("frozen prospective manifest is invalid")
    return manifest, read_jsonl(conversations_path)


def _git_head(project_dir: Path) -> str:
    process = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    return process.stdout.strip()


def _git_branch(project_dir: Path) -> str:
    process = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=project_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    return process.stdout.strip()


def _source_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "present": False, "sha256": None}
    return {"path": str(path), "present": True, "sha256": sha256_file(path)}


def prepare_experiment(
    *,
    project_dir: Path,
    output_dir: Path,
    review_pack: Path,
    history_root: Path,
    reconstruction_tool: Path,
    snapshot_projects: Sequence[Path] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, str]]]:
    """Freeze the exact corpus and useful provenance without provider traffic."""
    assert_production_sources_clean(project_dir)
    output_dir = ensure_private_output_dir(output_dir, project_dir=project_dir)
    review_pack = review_pack.resolve(strict=True)
    history_root = history_root.resolve(strict=False)
    reconstruction_tool = reconstruction_tool.resolve(strict=True)
    snapshots = list(snapshot_projects or enumerate_daily_snapshot_roots())
    if len(snapshots) != 8 or len({path.parents[1].name for path in snapshots}) != 8:
        raise EvaluationError("exactly eight explicit daily snapshot projects are required")
    pack_manifest, conversations = _review_pack_identity(review_pack)
    pack_name = str(pack_manifest.get("pack_name") or review_pack.name)
    prospective, prospective_skips = prospective_cases_from_conversations(
        conversations,
        source_path=review_pack,
        review_pack_identity=pack_name,
    )
    historical, historical_skips, history_manifest = history_cases_from_reconstruction(
        history_root
    )
    controls = load_qud_negative_controls()
    cases, selection_skips = select_corpus([*historical, *prospective, *controls])
    skips = [
        *historical_skips,
        *prospective_skips,
        *prior_research_fixture_skips(),
        *selection_skips,
    ]
    plausible_count = sum(
        case.get("plausible_explicit_repair_candidate") is True for case in cases
    )
    if sum(case.get("source_kind") == "qud_human_reviewed_negative_control" for case in cases) != 7:
        raise EvaluationError("all seven reviewed QUD controls must be retained")
    case_hash = sha256_value(cases)
    cases_path = private_path(output_dir, "cases.jsonl")
    manifest_path = private_path(output_dir, "manifest.json")
    if cases_path.exists():
        existing = read_jsonl(cases_path)
        if sha256_value(existing) != case_hash:
            raise EvaluationError("frozen corpus changed on resume")
    else:
        atomic_jsonl(cases_path, cases)
    source_manifest = PREVIOUS_EXPERIMENT_ROOT / "manifest.json"
    prior_pack_path = None
    prior_pack_hash = None
    if source_manifest.is_file():
        prior_manifest = read_json(source_manifest)
        if isinstance(prior_manifest, dict):
            frozen = prior_manifest.get("frozen_review_pack")
            if isinstance(frozen, Mapping):
                prior_pack_path = frozen.get("path")
                prior_pack_hash = frozen.get("manifest_sha256")
            elif isinstance(frozen, str):
                prior_pack_path = frozen
            prior_pack_hash = (
                prior_pack_hash
                or prior_manifest.get("frozen_review_pack_manifest_sha256")
            )
    liberty_cases = [case for case in cases if case.get("liberty_case") is True]
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_version": RUN_VERSION,
        "created_at": utc_now(),
        "source_branch": _git_branch(project_dir),
        "source_commit": _git_head(project_dir),
        "project_dir": str(project_dir),
        "output_dir": str(output_dir),
        "posting_enabled": False,
        "x_client_imported": False,
        "provider_tools_enabled": False,
        "text_only": True,
        "network_allowlist": sorted(NETWORK_ALLOWLIST),
        "reconstruction_tool": {
            "path": str(reconstruction_tool),
            "sha256": sha256_file(reconstruction_tool),
            "retained_git_commit": RECONSTRUCTION_TOOL_COMMIT,
            "retained_git_path": RECONSTRUCTION_TOOL_GIT_PATH,
        },
        "searched_roots": {
            "live_project": str(PRODUCTION_CHECKOUT),
            "snapshot_projects": [str(path.resolve()) for path in snapshots],
        },
        "source_review_packs": {
            "current": {
                "path": str(review_pack),
                "manifest_sha256": sha256_file(review_pack / "manifest.json"),
                "pack_content_sha256": pack_manifest.get("pack_content_sha256"),
            },
            "prior_qud": {
                "path": prior_pack_path,
                "manifest_sha256": prior_pack_hash,
            },
        },
        "research_sources": [
            _source_file(PREVIOUS_EXPERIMENT_ROOT / "manifest.json"),
            _source_file(PREVIOUS_EXPERIMENT_ROOT / "cases.jsonl"),
            _source_file(PREVIOUS_EXPERIMENT_ROOT / "arm_results.jsonl"),
            _source_file(GROK_EVAL_ROOT / "cases.jsonl"),
            _source_file(WRITER_EVAL_ROOT / "writer_cases.jsonl"),
        ],
        "history_reconstruction": {
            "path": str(history_root),
            "manifest_sha256": (
                sha256_file(history_root / "run_manifest.json")
                if (history_root / "run_manifest.json").is_file()
                else None
            ),
            "selected_snapshots": history_manifest.get("selected_snapshots"),
            "live_project_included": history_manifest.get("live_project_included"),
        },
        "liberty_case_found": bool(liberty_cases),
        "liberty_case_id": liberty_cases[0]["case_id"] if liberty_cases else None,
        "liberty_case_provenance": (
            {
                "source_kind": liberty_cases[0]["source_kind"],
                "source_path": liberty_cases[0]["source_path"],
            }
            if liberty_cases
            else None
        ),
        "case_set_sha256": case_hash,
        "case_counts": {
            "historical_candidates": sum(
                case["source_kind"] == "historical_explicit_repair_candidate"
                for case in cases
            ),
            "prospective_candidates": sum(
                case["source_kind"] == "prospective_explicit_repair_candidate"
                for case in cases
            ),
            "plausible_repair_candidates": plausible_count,
            "qud_negative_controls": 7,
            "skipped": len(skips),
            "total": len(cases),
        },
        "skip_reason_counts": dict(sorted(Counter(row["reason"] for row in skips).items())),
        "maximum_cases": MAX_CASES,
        "tool_sha256": sha256_file(Path(__file__)),
        "prompt_sha256": {
            "extraction": sha256_bytes(EXTRACTION_PROMPT.encode("utf-8")),
            "detector": sha256_bytes(DETECTOR_PROMPT.encode("utf-8")),
        },
        "schema_sha256": {
            "repair_record": sha256_value(REPAIR_RECORD_SCHEMA),
            "detector": sha256_value(DETECTOR_SCHEMA),
        },
        "models_and_efforts": {
            "repair_extraction": {
                "provider": "xAI",
                "model": EXTRACTION_MODEL,
                "reasoning_effort": EXTRACTION_EFFORT,
            },
            "transcript_only_detector": {
                "provider": "OpenAI",
                "model": DETECTOR_MODEL,
                "reasoning_effort": DETECTOR_EFFORT,
            },
            "repair_record_backed_detector": {
                "provider": "OpenAI",
                "model": DETECTOR_MODEL,
                "reasoning_effort": DETECTOR_EFFORT,
            },
        },
        "cost_ceiling_usd": GLOBAL_COST_CEILING_USD,
        "model_availability": None,
        "execution": {
            "mode": "prepared_only",
            "blocker": None,
            "updated_at": utc_now(),
        },
    }
    if manifest_path.exists():
        existing_manifest = read_json(manifest_path)
        immutable = (
            "run_version",
            "source_branch",
            "source_commit",
            "reconstruction_tool",
            "searched_roots",
            "source_review_packs",
            "case_set_sha256",
            "tool_sha256",
            "prompt_sha256",
            "schema_sha256",
            "models_and_efforts",
            "cost_ceiling_usd",
        )
        if not isinstance(existing_manifest, dict) or any(
            existing_manifest.get(field) != manifest.get(field) for field in immutable
        ):
            raise EvaluationError("immutable experiment manifest identity changed")
        manifest = existing_manifest
    else:
        atomic_json(manifest_path, manifest)
    create_or_load_detector_key(private_path(output_dir, "detector_key.private.json"))
    os.makedirs(private_path(output_dir, "responses"), mode=0o700, exist_ok=True)
    os.chmod(private_path(output_dir, "responses"), 0o700)
    return manifest, cases, skips


def update_manifest_execution(
    output_dir: Path,
    *,
    mode: str,
    blocker: str | None,
    availability: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Update only mutable execution facts in the single private manifest."""
    path = private_path(output_dir, "manifest.json")
    manifest = read_json(path)
    if not isinstance(manifest, dict):
        raise EvaluationError("private manifest is invalid")
    if availability is not None:
        manifest["model_availability"] = copy.deepcopy(dict(availability))
    manifest["execution"] = {
        "mode": mode,
        "blocker": blocker,
        "updated_at": utc_now(),
    }
    atomic_json(path, manifest)
    return manifest


def percentile(values: Sequence[float], proportion: float) -> float | None:
    """Return a nearest-rank-like interpolated percentile for a small corpus."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = proportion * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _operation_metrics(ledger: RecurrenceRequestLedger) -> dict[str, Any]:
    operations = [
        row for row in ledger.data.get("operations", []) if isinstance(row, dict)
    ]
    latencies = [
        float(row["provider_latency_seconds"])
        for row in operations
        if row.get("provider_latency_seconds") is not None
    ]
    summary = ledger.cost_summary()
    return {
        "canonical_requests": len(operations),
        "network_request_events": sum(
            bool(row.get("attempt_events")) for row in operations
        ),
        "provider_http_attempts": sum(
            len(row.get("attempt_events") or []) for row in operations
        ),
        "completed_provider_calls": sum(row.get("status") == "completed" for row in operations),
        "ambiguous_calls": sum(row.get("status") == "ambiguous" for row in operations),
        "provider_failures": sum(
            row.get("status") in {"http_error", "rate_limited", "server_error"}
            for row in operations
        ),
        "xai_billed_cost_usd": summary["total_billed_cost_usd"],
        "openai_estimated_cost_usd": summary["total_estimated_cost_usd"],
        "median_request_latency_seconds": statistics.median(latencies) if latencies else None,
        "p95_request_latency_seconds": percentile(latencies, 0.95),
        "input_tokens": sum(int(row.get("input_tokens") or 0) for row in operations),
        "output_tokens": sum(int(row.get("output_tokens") or 0) for row in operations),
        "reasoning_tokens": sum(int(row.get("reasoning_tokens") or 0) for row in operations),
    }


def build_comparison(
    *,
    manifest: Mapping[str, Any],
    cases: Sequence[Mapping[str, Any]],
    skips: Sequence[Mapping[str, Any]],
    repair_rows: Sequence[Mapping[str, Any]],
    detector_rows: Sequence[Mapping[str, Any]],
    ledger: RecurrenceRequestLedger,
    detector_key: Mapping[str, str],
    blocker: str | None,
) -> dict[str, Any]:
    """Build a factual operational report without judging detector quality."""
    repairs = {str(row.get("case_id")): row for row in repair_rows}
    detector_map = {
        (str(row.get("case_id")), str(row.get("condition"))): row
        for row in detector_rows
    }
    inverse = {condition: label for label, condition in detector_key.items()}
    confidence = Counter(
        str((row.get("result") or {}).get("confidence") or "not_completed")
        for row in repair_rows
    )
    statuses = Counter(
        str((row.get("result") or {}).get("status") or row.get("status") or "not_completed")
        for row in repair_rows
    )
    trigger_counts = {
        inverse[condition]: sum(
            row.get("condition") == condition and row.get("narrow_trigger") is True
            for row in detector_rows
        )
        for condition in CONDITIONS
    }
    event_rows = [
        row.get("request")
        for row in [*repair_rows, *detector_rows]
        if isinstance(row.get("request"), Mapping)
    ]
    provider = _operation_metrics(ledger)
    provider["cache_hit_events"] = sum(event.get("cache_hit") is True for event in event_rows)
    comparison = {
        "generated_at": utc_now(),
        "case_set_sha256": manifest["case_set_sha256"],
        "liberty_case_found": manifest["liberty_case_found"],
        "liberty_case_id": manifest["liberty_case_id"],
        "counts": {
            **copy.deepcopy(dict(manifest["case_counts"])),
            "repair_extractions_completed": sum(
                row.get("status") == "completed" for row in repair_rows
            ),
            "usable_repair_records": sum(row.get("usable") is True for row in repair_rows),
            "detectors_completed": sum(
                row.get("status") == "completed" for row in detector_rows
            ),
            "record_backed_not_run": sum(
                row.get("status") == "no_usable_repair_record" for row in detector_rows
            ),
        },
        "skip_reason_counts": dict(sorted(Counter(str(row.get("reason")) for row in skips).items())),
        "skipped_cases": copy.deepcopy(list(skips)),
        "repair_status_counts": dict(sorted(statuses.items())),
        "repair_confidence_counts": dict(sorted(confidence.items())),
        "narrow_trigger_counts": trigger_counts,
        "failures": {
            "schema": sum(bool(row.get("schema_failure")) for row in [*repair_rows, *detector_rows]),
            "provider": sum(bool(row.get("provider_failure")) for row in [*repair_rows, *detector_rows]),
            "ambiguous": sum(bool(row.get("ambiguous_failure")) for row in [*repair_rows, *detector_rows]),
            "evidence_validation": sum(
                isinstance(row.get("evidence_validation"), Mapping)
                and row["evidence_validation"].get("valid") is False
                and row.get("status") != "no_usable_repair_record"
                for row in detector_rows
            ),
        },
        "provider": provider,
        "blocker": blocker,
        "human_scoring": None,
        "quality_conclusion": (
            "No detector condition is declared successful before blind ground-truth "
            "and diagnostic scoring are complete."
        ),
    }
    # Touch maps here so malformed duplicate inputs fail during report generation.
    if len(repairs) != len(repair_rows) or len(detector_map) != len(detector_rows):
        raise EvaluationError("duplicate execution result identity")
    return comparison


def render_comparison_markdown(comparison: Mapping[str, Any]) -> str:
    """Render the private operational comparison without naming a winner."""
    counts = comparison["counts"]
    provider = comparison["provider"]
    failures = comparison["failures"]
    lines = [
        "# Rejected-answer recurrence detector — private operational report",
        "",
        "This is detector-only research evidence. It did not alter, rewrite, suppress, or publish any reply.",
        "No detector condition is recommended or declared successful before human blind and diagnostic scoring.",
        "",
        "## Corpus",
        "",
        f"- Total cases: {counts['total']}",
        f"- Plausible explicit-repair candidates: {counts['plausible_repair_candidates']}",
        f"- Historical candidates: {counts['historical_candidates']}",
        f"- Prospective candidates: {counts['prospective_candidates']}",
        f"- Prior QUD negative controls: {counts['qud_negative_controls']}",
        f"- Skipped candidates: {counts['skipped']}",
        f"- Liberty case found: {str(comparison['liberty_case_found']).lower()}",
        "",
        "## Execution",
        "",
        f"- Repair extractions completed: {counts['repair_extractions_completed']}",
        f"- Usable high-confidence repair records: {counts['usable_repair_records']}",
        f"- Detector calls completed: {counts['detectors_completed']}",
        f"- Record-backed condition not run without a usable record: {counts['record_backed_not_run']}",
        f"- Detector P narrow triggers: {comparison['narrow_trigger_counts']['P']}",
        f"- Detector Q narrow triggers: {comparison['narrow_trigger_counts']['Q']}",
        "",
        "## Provider and safety accounting",
        "",
        f"- Canonical requests: {provider['canonical_requests']}",
        f"- Network provider calls: {provider['network_request_events']}",
        f"- Cache-hit events: {provider['cache_hit_events']}",
        f"- Schema failures: {failures['schema']}",
        f"- Provider failures: {failures['provider']}",
        f"- Ambiguous calls: {failures['ambiguous']}",
        f"- Evidence-validation failures: {failures['evidence_validation']}",
        f"- xAI provider-reported billed cost: US${provider['xai_billed_cost_usd']:.6f}",
        f"- OpenAI estimated cost: US${provider['openai_estimated_cost_usd']:.6f}",
        f"- Median request latency: {provider['median_request_latency_seconds']}",
        f"- P95 request latency: {provider['p95_request_latency_seconds']}",
        "",
        f"Blocker: {comparison.get('blocker') or 'none'}",
        "",
    ]
    if comparison.get("skip_reason_counts"):
        lines.extend(["## Skip reasons", ""])
        lines.extend(
            f"- {reason}: {count}"
            for reason, count in comparison["skip_reason_counts"].items()
        )
        lines.append("")
    return "\n".join(lines)


def _blind_source_category(case: Mapping[str, Any]) -> str:
    categories = {
        "historical_explicit_repair_candidate": "retained historical branch",
        "prospective_explicit_repair_candidate": "frozen prospective branch",
        "qud_human_reviewed_negative_control": "prior QUD paid case",
    }
    return categories.get(str(case.get("source_kind")), "retained exact branch")


def _render_transcript(turns: Sequence[Mapping[str, Any]]) -> list[str]:
    lines: list[str] = []
    for turn in turns:
        role = (
            "Account"
            if turn.get("author_role") == "account"
            else "Principal contributor"
        )
        lines.extend(
            [
                f"**{role} — {turn.get('turn_id')}**",
                "",
                str(turn.get("text") or ""),
                "",
            ]
        )
    return lines


def render_blind_case_review(cases: Sequence[Mapping[str, Any]]) -> str:
    """Render only exact case inputs and safe source categories for human labels."""
    lines = [
        "# Blind recurrence ground-truth review",
        "",
        "Review the exact branch only up to the final contributor turn, then the separate candidate account reply.",
        "Judge explicit wrong-answer repair and recurrence, not political agreement, politeness, wit, or general reply quality.",
        "A direct disagreement may answer the restored issue. A distinction or later elaboration is not recurrence when the restored issue is answered first.",
        "",
    ]
    for case in cases:
        lines.extend(
            [
                f"## {case['case_id']}",
                "",
                f"Source category: {_blind_source_category(case)}",
                "",
                "### Exact branch through alleged repair",
                "",
                *_render_transcript(case["transcript"]),
                "### Candidate account reply",
                "",
                str(case["candidate_reply"]),
                "",
            ]
        )
    return "\n".join(lines)


BLIND_LABEL_FIELDS = (
    "case_id",
    "explicit_wrong_answer_repair",
    "rejected_answer_identifiable",
    "restored_issue_identifiable",
    "candidate_addresses_restored_issue_first",
    "candidate_repeats_rejected_answer_as_answer",
    "overall_recurrence",
    "notes",
)


def write_blind_case_labels(path: Path, cases: Sequence[Mapping[str, Any]]) -> None:
    """Write the label template once, prefilling only seven reviewed outcomes."""
    if path.exists():
        return
    rows: list[dict[str, str]] = []
    for case in cases:
        carried = case.get("carried_forward_human_label")
        is_control = isinstance(carried, Mapping)
        rows.append(
            {
                "case_id": str(case["case_id"]),
                "explicit_wrong_answer_repair": "",
                "rejected_answer_identifiable": "",
                "restored_issue_identifiable": "",
                "candidate_addresses_restored_issue_first": "",
                "candidate_repeats_rejected_answer_as_answer": "",
                "overall_recurrence": "no" if is_control else "",
                "notes": (
                    "carried forward from completed human review on 2026-08-29"
                    if is_control
                    else ""
                ),
            }
        )
    output = []
    import io

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=BLIND_LABEL_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    atomic_text(path, buffer.getvalue())


def render_diagnostic_review(
    *,
    cases: Sequence[Mapping[str, Any]],
    repair_rows: Sequence[Mapping[str, Any]],
    detector_rows: Sequence[Mapping[str, Any]],
    detector_key: Mapping[str, str],
) -> str:
    """Render exact extraction and blinded-condition diagnostics after the warning."""
    repairs = {str(row.get("case_id")): row for row in repair_rows}
    detectors = {
        (str(row.get("case_id")), str(row.get("condition"))): row
        for row in detector_rows
    }
    lines = [
        "# Diagnostic review",
        "",
        "**Do not open until blind_case_labels.csv has been completed and saved.**",
        "",
        "Detector P and Detector Q remain blinded here; their condition mapping is not shown.",
        "",
    ]
    for case in cases:
        case_id = str(case["case_id"])
        repair = repairs.get(case_id)
        lines.extend(
            [
                f"## {case_id}",
                "",
                "### Exact transcript",
                "",
                *_render_transcript(case["transcript"]),
                "### Candidate account reply",
                "",
                str(case["candidate_reply"]),
                "",
                "### Explicit-repair extraction",
                "",
                "```json",
                json.dumps(
                    repair.get("result") if repair else None,
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                ),
                "```",
                "",
                "Exact-substring validation: "
                + (
                    json.dumps(repair.get("validation"), sort_keys=True)
                    if repair
                    else "not run"
                ),
                "",
            ]
        )
        for label in BLIND_DETECTOR_LABELS:
            condition = detector_key[label]
            row = detectors.get((case_id, condition))
            lines.extend(
                [
                    f"### Detector {label}",
                    "",
                    "```json",
                    json.dumps(
                        row.get("result") if row else None,
                        ensure_ascii=True,
                        indent=2,
                        sort_keys=True,
                    ),
                    "```",
                    "",
                    f"Execution status: {row.get('status') if row else 'not_run'}",
                    "",
                    f"Narrow machine trigger: {str(bool(row and row.get('narrow_trigger'))).lower()}",
                    "",
                ]
            )
    return "\n".join(lines)


DIAGNOSTIC_SCORE_FIELDS = (
    "case_id",
    "repair_record_rating",
    "detector_P_rating",
    "detector_Q_rating",
    "notes",
)


def write_diagnostic_scores(path: Path, cases: Sequence[Mapping[str, Any]]) -> None:
    """Write the empty diagnostic score template without overwriting human work."""
    if path.exists():
        return
    import io

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=DIAGNOSTIC_SCORE_FIELDS, lineterminator="\n"
    )
    writer.writeheader()
    for case in cases:
        writer.writerow(
            {
                "case_id": case["case_id"],
                "repair_record_rating": "",
                "detector_P_rating": "",
                "detector_Q_rating": "",
                "notes": "",
            }
        )
    atomic_text(path, buffer.getvalue())


def _blind_review_forbidden_text(review: str) -> None:
    forbidden = (
        "grok-4.6",
        "gpt-5.6-sol",
        "repair_record",
        "extractor output",
        "detector output",
        "narrow_trigger",
        "cost_usd",
        "provider_latency",
        "transcript_only",
        "repair_record_backed",
    )
    lowered = review.lower()
    if any(item.lower() in lowered for item in forbidden):
        raise EvaluationError("blind case review leaks private diagnostics")


def generate_reports(
    *,
    output_dir: Path,
    manifest: Mapping[str, Any],
    cases: Sequence[Mapping[str, Any]],
    skips: Sequence[Mapping[str, Any]],
    repair_rows: Sequence[Mapping[str, Any]],
    detector_rows: Sequence[Mapping[str, Any]],
    ledger: RecurrenceRequestLedger,
    blocker: str | None,
) -> dict[str, Any]:
    """Generate the private operational, blind, and diagnostic output set."""
    key = create_or_load_detector_key(
        private_path(output_dir, "detector_key.private.json")
    )
    comparison = build_comparison(
        manifest=manifest,
        cases=cases,
        skips=skips,
        repair_rows=repair_rows,
        detector_rows=detector_rows,
        ledger=ledger,
        detector_key=key,
        blocker=blocker,
    )
    atomic_json(private_path(output_dir, "comparison.private.json"), comparison)
    atomic_text(
        private_path(output_dir, "comparison.private.md"),
        render_comparison_markdown(comparison),
    )
    blind = render_blind_case_review(cases)
    _blind_review_forbidden_text(blind)
    atomic_text(private_path(output_dir, "blind_case_review.md"), blind)
    write_blind_case_labels(
        private_path(output_dir, "blind_case_labels.csv"), cases
    )
    diagnostic = render_diagnostic_review(
        cases=cases,
        repair_rows=repair_rows,
        detector_rows=detector_rows,
        detector_key=key,
    )
    atomic_text(private_path(output_dir, "diagnostic_review.md"), diagnostic)
    write_diagnostic_scores(
        private_path(output_dir, "diagnostic_scores.csv"), cases
    )
    return comparison


def _read_csv_exact(path: Path, expected_fields: Sequence[str]) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(expected_fields):
            raise EvaluationError(f"{path.name} header is invalid")
        return [dict(row) for row in reader]


def load_completed_blind_labels(
    path: Path, *, expected_case_ids: set[str]
) -> list[dict[str, str]]:
    """Load complete ground truth while allowing optional control subfields."""
    if not path.is_file():
        raise EvaluationError("completed blind_case_labels.csv is required")
    rows = _read_csv_exact(path, BLIND_LABEL_FIELDS)
    if {row["case_id"] for row in rows} != expected_case_ids:
        raise EvaluationError("blind label case identity changed")
    allowed = {
        "explicit_wrong_answer_repair": {"yes", "no", "unclear"},
        "rejected_answer_identifiable": {"yes", "no", "not_applicable", "unclear"},
        "restored_issue_identifiable": {"yes", "no", "not_applicable", "unclear"},
        "candidate_addresses_restored_issue_first": {
            "yes",
            "partly",
            "no",
            "not_applicable",
            "unclear",
        },
        "candidate_repeats_rejected_answer_as_answer": {
            "yes",
            "no",
            "not_applicable",
            "unclear",
        },
        "overall_recurrence": {"yes", "no", "unclear"},
    }
    for row in rows:
        if row["overall_recurrence"] not in allowed["overall_recurrence"]:
            raise EvaluationError("blind recurrence labels are incomplete")
        is_carried = "carried forward" in row.get("notes", "").lower()
        for field, values in allowed.items():
            if field == "overall_recurrence":
                continue
            if row[field] == "" and is_carried:
                continue
            if row[field] not in values:
                raise EvaluationError(f"blind label field {field} is incomplete")
    return rows


def load_completed_diagnostic_scores(
    path: Path, *, expected_case_ids: set[str]
) -> list[dict[str, str]] | None:
    """Load optional completed diagnostic scores or return none for a blank file."""
    if not path.is_file():
        return None
    rows = _read_csv_exact(path, DIAGNOSTIC_SCORE_FIELDS)
    if {row["case_id"] for row in rows} != expected_case_ids:
        raise EvaluationError("diagnostic score case identity changed")
    if all(
        not row["repair_record_rating"]
        and not row["detector_P_rating"]
        and not row["detector_Q_rating"]
        for row in rows
    ):
        return None
    allowed_repair = {
        "accurate",
        "partly_accurate",
        "missed",
        "false_positive",
        "not_applicable",
        "genuinely_unclear",
    }
    allowed_detector = {"correct", "incorrect", "genuinely_unclear", "not_run"}
    if any(
        row["repair_record_rating"] not in allowed_repair
        or row["detector_P_rating"] not in allowed_detector
        or row["detector_Q_rating"] not in allowed_detector
        for row in rows
    ):
        raise EvaluationError("diagnostic scores are incomplete or invalid")
    return rows


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _condition_score(
    *,
    condition: str,
    labels: Mapping[str, Mapping[str, str]],
    detector_rows: Mapping[tuple[str, str], Mapping[str, Any]],
    repair_rows: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    true_positive_cases = [
        case_id for case_id, row in labels.items() if row["overall_recurrence"] == "yes"
    ]
    true_negative_cases = [
        case_id for case_id, row in labels.items() if row["overall_recurrence"] == "no"
    ]
    tp: list[str] = []
    fp: list[str] = []
    tn: list[str] = []
    fn: list[str] = []
    unclear = 0
    triggers = 0
    for case_id, label in labels.items():
        row = detector_rows.get((case_id, condition))
        triggered = bool(row and row.get("narrow_trigger") is True)
        triggers += triggered
        result_status = (row.get("result") or {}).get("status") if row else None
        if result_status == "unclear" or label["overall_recurrence"] == "unclear":
            unclear += 1
            continue
        truth = label["overall_recurrence"] == "yes"
        if condition == "repair_record_backed" and truth:
            repair = repair_rows.get(case_id)
            if not repair or repair.get("usable") is not True:
                triggered = False
        if truth and triggered:
            tp.append(case_id)
        elif truth:
            fn.append(case_id)
        elif triggered:
            fp.append(case_id)
        else:
            tn.append(case_id)
    return {
        "true_recurrence_positives": len(true_positive_cases),
        "true_recurrence_negatives": len(true_negative_cases),
        "true_positives": len(tp),
        "false_positives": len(fp),
        "true_negatives": len(tn),
        "false_negatives": len(fn),
        "unclear": unclear,
        "precision": _ratio(len(tp), len(tp) + len(fp)),
        "recall": _ratio(len(tp), len(tp) + len(fn)),
        "false_positive_rate": _ratio(len(fp), len(fp) + len(tn)),
        "narrow_trigger_count": triggers,
        "false_positive_case_ids": fp,
        "false_negative_case_ids": fn,
    }


def report_completed_scores(output_dir: Path) -> dict[str, Any]:
    """Decode P/Q privately and report paired extraction/detector performance."""
    manifest = read_json(private_path(output_dir, "manifest.json"))
    cases = read_jsonl(private_path(output_dir, "cases.jsonl"))
    case_ids = {str(case["case_id"]) for case in cases}
    labels_rows = load_completed_blind_labels(
        private_path(output_dir, "blind_case_labels.csv"),
        expected_case_ids=case_ids,
    )
    labels = {row["case_id"]: row for row in labels_rows}
    repair_list = _load_unique_rows(
        private_path(output_dir, "repair_records.jsonl"), ("case_id",)
    )
    detector_list = _load_unique_rows(
        private_path(output_dir, "detector_results.jsonl"), ("case_id", "condition")
    )
    repairs = {str(row["case_id"]): row for row in repair_list}
    detectors = {
        (str(row["case_id"]), str(row["condition"])): row for row in detector_list
    }
    key = create_or_load_detector_key(
        private_path(output_dir, "detector_key.private.json")
    )
    true_repairs = [
        case_id
        for case_id, row in labels.items()
        if row["explicit_wrong_answer_repair"] == "yes"
    ]
    usable = [case_id for case_id, row in repairs.items() if row.get("usable") is True]
    extraction_tp = [case_id for case_id in usable if case_id in true_repairs]
    extraction_fp = [
        case_id
        for case_id in usable
        if labels.get(case_id, {}).get("explicit_wrong_answer_repair") == "no"
    ]
    extraction_miss = [case_id for case_id in true_repairs if case_id not in usable]
    condition_scores = {
        condition: _condition_score(
            condition=condition,
            labels=labels,
            detector_rows=detectors,
            repair_rows=repairs,
        )
        for condition in CONDITIONS
    }
    disagreements = [
        case_id
        for case_id in sorted(case_ids)
        if bool(detectors.get((case_id, CONDITIONS[0]), {}).get("narrow_trigger"))
        != bool(detectors.get((case_id, CONDITIONS[1]), {}).get("narrow_trigger"))
    ]
    controls = {
        str(case["case_id"])
        for case in cases
        if case.get("source_kind") == "qud_human_reviewed_negative_control"
    }
    diagnostic = load_completed_diagnostic_scores(
        private_path(output_dir, "diagnostic_scores.csv"), expected_case_ids=case_ids
    )
    comparison = read_json(private_path(output_dir, "comparison.private.json"))
    scoring = {
        "reported_at": utc_now(),
        "repair_extraction": {
            "true_explicit_repairs": len(true_repairs),
            "extracted_usable_repairs": len(usable),
            "true_positives": len(extraction_tp),
            "false_positives": len(extraction_fp),
            "misses": len(extraction_miss),
            "precision": _ratio(len(extraction_tp), len(extraction_tp) + len(extraction_fp)),
            "recall": _ratio(len(extraction_tp), len(true_repairs)),
            "false_positive_case_ids": extraction_fp,
            "miss_case_ids": extraction_miss,
        },
        "detectors_by_condition": condition_scores,
        "detectors_by_blind_label": {
            label: condition_scores[condition] for label, condition in key.items()
        },
        "liberty_result": (
            {
                "case_id": manifest.get("liberty_case_id"),
                "human_label": labels.get(str(manifest.get("liberty_case_id"))),
                "detector_P_trigger": bool(
                    detectors.get((str(manifest.get("liberty_case_id")), key["P"]), {}).get(
                        "narrow_trigger"
                    )
                ),
                "detector_Q_trigger": bool(
                    detectors.get((str(manifest.get("liberty_case_id")), key["Q"]), {}).get(
                        "narrow_trigger"
                    )
                ),
            }
            if manifest.get("liberty_case_found")
            else {"found": False}
        ),
        "prior_negative_controls": {
            "case_count": len(controls),
            "detector_P_false_positive_case_ids": sorted(
                case_id
                for case_id in controls
                if detectors.get((case_id, key["P"]), {}).get("narrow_trigger") is True
            ),
            "detector_Q_false_positive_case_ids": sorted(
                case_id
                for case_id in controls
                if detectors.get((case_id, key["Q"]), {}).get("narrow_trigger") is True
            ),
        },
        "condition_disagreement_case_ids": disagreements,
        "diagnostic_scores": diagnostic,
        "operational_failures": comparison.get("failures"),
        "cost_and_latency": comparison.get("provider"),
    }
    positive_count = sum(row["overall_recurrence"] == "yes" for row in labels_rows)
    zero_control_fp = all(
        not scoring["prior_negative_controls"][field]
        for field in (
            "detector_P_false_positive_case_ids",
            "detector_Q_false_positive_case_ids",
        )
    )
    scoring["production_design_threshold"] = {
        "at_least_three_confirmed_positives": positive_count >= 3,
        "zero_negative_control_false_positives": zero_control_fp,
        "conclusion": (
            "At most, these scores can justify a separate tightly scoped production-design experiment; this task changes no production system."
            if positive_count >= 3 and zero_control_fp
            else "The prerequisites for recommending further production design work are not established."
        ),
    }
    comparison = copy.deepcopy(dict(comparison))
    comparison["human_scoring"] = scoring
    atomic_json(private_path(output_dir, "comparison.private.json"), comparison)
    markdown = render_comparison_markdown(comparison)
    markdown += "\n## Human score report\n\n```json\n"
    markdown += json.dumps(scoring, ensure_ascii=True, indent=2, sort_keys=True)
    markdown += "\n```\n"
    atomic_text(private_path(output_dir, "comparison.private.md"), markdown)
    return comparison


def require_live_execution(enabled: bool) -> None:
    """Reject every provider path unless the explicit live flag is present."""
    if enabled is not True:
        raise EvaluationError("provider traffic requires --execute-live-models")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--review-pack", type=Path)
    parser.add_argument("--history-root", type=Path, default=DEFAULT_HISTORY_ROOT)
    parser.add_argument("--reconstruction-tool", type=Path)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--execute-live-models", action="store_true")
    parser.add_argument("--report-scores", action="store_true")
    parser.add_argument(
        "--cost-ceiling-usd", type=float, default=GLOBAL_COST_CEILING_USD
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Prepare offline, conditionally execute, or report completed human scores."""
    os.umask(0o077)
    args = _build_parser().parse_args(argv)
    project_dir = args.project_dir.expanduser().resolve()
    if project_dir != PROJECT_ROOT.resolve():
        raise EvaluationError("project directory must be this isolated worktree")
    output_dir = ensure_private_output_dir(args.output_dir, project_dir=project_dir)
    if args.cost_ceiling_usd != GLOBAL_COST_CEILING_USD:
        raise EvaluationError("--cost-ceiling-usd must remain exactly 10.00")
    if args.report_scores:
        if args.execute_live_models:
            raise EvaluationError("--report-scores cannot be combined with live execution")
        comparison = report_completed_scores(output_dir)
        print(
            json.dumps(
                {
                    "mode": "report_scores",
                    "comparison": str(private_path(output_dir, "comparison.private.md")),
                    "scored_cases": comparison["counts"]["total"],
                },
                sort_keys=True,
            )
        )
        return 0
    if args.review_pack is None:
        raise EvaluationError("--review-pack is required for corpus preparation")
    reconstruction_tool = args.reconstruction_tool
    if reconstruction_tool is None:
        reconstruction_tool = output_dir / "history_tool" / RECONSTRUCTION_TOOL_GIT_PATH
    manifest, cases, skips = prepare_experiment(
        project_dir=project_dir,
        output_dir=output_dir,
        review_pack=args.review_pack,
        history_root=args.history_root,
        reconstruction_tool=reconstruction_tool,
    )
    ledger = RecurrenceRequestLedger(
        private_path(output_dir, "request_ledger.json"),
        case_set_sha256=str(manifest["case_set_sha256"]),
    )
    repair_rows = _load_unique_rows(
        private_path(output_dir, "repair_records.jsonl"), ("case_id",)
    )
    detector_rows = _load_unique_rows(
        private_path(output_dir, "detector_results.jsonl"), ("case_id", "condition")
    )
    plausible = int(manifest["case_counts"]["plausible_repair_candidates"])
    blocker: str | None = None
    availability: dict[str, Any] | None = None
    mode = "prepared_only"
    if args.execute_live_models and plausible:
        require_live_execution(True)
        mode = "live_models"
        api_keys = load_api_keys(args.env_file)
        try:
            repair_rows, detector_rows, ledger, availability, blocker = execute_experiment(
                output_dir=output_dir,
                manifest=manifest,
                cases=cases,
                api_keys=api_keys,
            )
        except (EvaluationError, OSError, ValueError) as exc:
            blocker = f"{type(exc).__name__}: {exc}"
            repair_rows = _load_unique_rows(
                private_path(output_dir, "repair_records.jsonl"), ("case_id",)
            )
            detector_rows = _load_unique_rows(
                private_path(output_dir, "detector_results.jsonl"),
                ("case_id", "condition"),
            )
            ledger = RecurrenceRequestLedger(
                private_path(output_dir, "request_ledger.json"),
                case_set_sha256=str(manifest["case_set_sha256"]),
            )
    elif args.execute_live_models:
        mode = "no_positive_corpus_no_live_calls"
        blocker = "retained history contains no plausible exact repair case with a proved account reply"
    manifest = update_manifest_execution(
        output_dir,
        mode=mode,
        blocker=blocker,
        availability=availability,
    )
    _persist_execution_rows(output_dir, repair_rows, detector_rows)
    comparison = generate_reports(
        output_dir=output_dir,
        manifest=manifest,
        cases=cases,
        skips=skips,
        repair_rows=repair_rows,
        detector_rows=detector_rows,
        ledger=ledger,
        blocker=blocker,
    )
    print(
        json.dumps(
            {
                "mode": mode,
                "total_cases": comparison["counts"]["total"],
                "plausible_repair_candidates": comparison["counts"][
                    "plausible_repair_candidates"
                ],
                "liberty_case_found": comparison["liberty_case_found"],
                "provider_calls": comparison["provider"]["network_request_events"],
                "cache_hits": comparison["provider"]["cache_hit_events"],
                "blocker": blocker,
                "comparison": str(private_path(output_dir, "comparison.private.md")),
            },
            sort_keys=True,
        )
    )
    return 1 if blocker and mode == "live_models" else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (EvaluationError, OSError, ValueError) as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
