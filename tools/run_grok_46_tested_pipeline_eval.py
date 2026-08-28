#!/usr/bin/env python3
"""Run the private, text-only Grok 4.6 tested-pipeline comparison.

The runner imports only the provider-independent tested reply pipeline and its
local evidence repository.  It has no X client or posting path.  Without the
explicit live flag it only validates and freezes the experiment's deterministic
case set inside the private research output directory.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import io
import json
import math
import os
import random
import re
import secrets
import shlex
import stat
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from reply_evidence import EvidenceRepository
from reply_strategy import validate_reply_context
from tested_reply_pipeline import (
    default_config,
    run_reply_pipeline,
    stage_telemetry,
    validate_strategy_config,
)


RUN_VERSION = "grok-46-tested-pipeline-eval-v1"
SCHEMA_VERSION = 1
USD_TICKS_PER_DOLLAR = 10_000_000_000
MAX_CASES = 150
MAX_REPLY_LENGTH = 270
REASONING_OUTPUT_MULTIPLIER = 16

PRODUCTION_CHECKOUT = Path("/disks/disk1/etc/mrsMThatcher")
PROSPECTIVE_ROOT = Path(
    "/disks/disk1/research/mrsMThatcher-prospective-conversations-v4"
)
DEFAULT_OUTPUT_ROOT = Path(
    "/disks/disk1/research/grok-46-tested-pipeline-eval-20260828"
)
DEFAULT_ENV_FILE = PRODUCTION_CHECKOUT / "mrsMThatcher.env"

XAI_HOST = "api.x.ai"
OPENAI_HOST = "api.openai.com"
XAI_BASE_URL = "https://api.x.ai/v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"
NETWORK_ALLOWLIST = frozenset({XAI_HOST, OPENAI_HOST})

LOGICAL_XAI_MODEL = "grok-4.3"
LOGICAL_XAI_EFFORT = "low"
LOGICAL_OPENAI_MODEL = "gpt-5.6-sol"
LOGICAL_OPENAI_EFFORT = "medium"

# Official GPT-5.6 Sol standard-priority text prices observed on 2026-08-28.
# Cache writes are 1.25 times uncached input.  These rates are estimates when
# the provider response does not contain an authoritative cost field.
OPENAI_INPUT_TICKS_PER_TOKEN = 40_000
OPENAI_CACHED_INPUT_TICKS_PER_TOKEN = 4_000
OPENAI_CACHE_WRITE_TICKS_PER_TOKEN = 50_000
OPENAI_OUTPUT_TICKS_PER_TOKEN = 200_000
OPENAI_PRICING_SOURCE = "https://developers.openai.com/api/docs/models/gpt-5.6-sol"

ARMS: dict[str, dict[str, str]] = {
    "A": {"effective_xai_model": "grok-4.3", "effective_xai_effort": "low"},
    "B": {"effective_xai_model": "grok-4.3", "effective_xai_effort": "medium"},
    "C": {"effective_xai_model": "grok-4.6", "effective_xai_effort": "low"},
    "D": {"effective_xai_model": "grok-4.6", "effective_xai_effort": "medium"},
}
BLIND_LABELS = ("W", "X", "Y", "Z")
PIPELINE_LANES = {
    "mention": "mention",
    "hot-post reply": "hot_post_reply",
    "quote-tweet reply": "quote_tweet",
}
CRITICAL_RECONSTRUCTION_WARNINGS = {
    "ambiguous_parentage",
    "missing_parent_post",
    "root_not_reached_by_parent_path",
}


class EvaluationError(RuntimeError):
    """The isolated evaluation cannot continue safely."""


class CostLimitReached(EvaluationError):
    """The next provider request could exceed the global cost ceiling."""


class AmbiguousRequestError(EvaluationError):
    """A transmitted provider request has no safely recoverable outcome."""


class ProviderError(EvaluationError):
    """A provider returned an unusable or terminal response."""


class DefiniteProviderError(ProviderError):
    """A provider definitely rejected a request without an ambiguous outcome."""


class TransientProviderError(ProviderError):
    """The bounded definite 429 or 5xx retry allowance was exhausted."""


class ModelAvailabilityError(EvaluationError):
    """A required effective xAI model is unavailable to the authenticated key."""


def utc_now() -> str:
    """Return a second-resolution UTC timestamp."""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def canonical_json_bytes(value: Any, *, newline: bool = False) -> bytes:
    """Return deterministic UTF-8 JSON bytes."""
    suffix = b"\n" if newline else b""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + suffix


def sha256_bytes(value: bytes) -> str:
    """Return the SHA-256 digest of bytes."""
    return hashlib.sha256(value).hexdigest()


def sha256_value(value: Any) -> str:
    """Return the canonical SHA-256 digest of a JSON-compatible value."""
    return sha256_bytes(canonical_json_bytes(value))


def sha256_file(path: Path) -> str:
    """Return a file SHA-256 without changing the file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def strict_json_loads(value: str | bytes) -> Any:
    """Parse JSON while rejecting duplicate object keys."""

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key!r}")
            result[key] = item
        return result

    return json.loads(value, object_pairs_hook=reject_duplicates)


def read_json(path: Path) -> Any:
    """Read one strict JSON document."""
    return strict_json_loads(path.read_bytes())


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read strict, newline-terminated JSON object rows."""
    rows: list[dict[str, Any]] = []
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        raise EvaluationError(f"JSONL file has an unterminated final row: {path}")
    for line_number, line in enumerate(data.splitlines(), start=1):
        if not line.strip():
            raise EvaluationError(f"JSONL file contains a blank row: {path}:{line_number}")
        value = strict_json_loads(line)
        if not isinstance(value, dict):
            raise EvaluationError(f"JSONL row is not an object: {path}:{line_number}")
        rows.append(value)
    return rows


def _fsync_directory(path: Path) -> None:
    """Synchronise one directory entry set after an atomic replacement."""
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_bytes(path: Path, value: bytes) -> None:
    """Write one private file atomically with mode 0600."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def atomic_json(path: Path, value: Any) -> None:
    """Write deterministic private JSON atomically."""
    atomic_bytes(
        path,
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        + b"\n",
    )


def atomic_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    """Write deterministic private JSON object rows atomically."""
    atomic_bytes(path, b"".join(canonical_json_bytes(dict(row), newline=True) for row in rows))


def atomic_text(path: Path, value: str) -> None:
    """Write private UTF-8 text atomically."""
    atomic_bytes(path, value.encode("utf-8"))


def ensure_private_output_dir(path: Path, *, project_dir: Path) -> Path:
    """Validate and create the sole permitted private experiment output tree."""
    resolved = path.expanduser().resolve()
    project = project_dir.expanduser().resolve()
    production = PRODUCTION_CHECKOUT.resolve()
    allowed = DEFAULT_OUTPUT_ROOT.resolve()
    if resolved == production or production in resolved.parents:
        raise EvaluationError("experiment output must never be inside the production checkout")
    if resolved == project or project in resolved.parents:
        raise EvaluationError("experiment output must never be inside the evaluation worktree")
    if resolved != allowed and allowed not in resolved.parents:
        raise EvaluationError(
            f"experiment output must be beneath the fixed private root {allowed}"
        )
    os.makedirs(resolved, mode=0o700, exist_ok=True)
    if resolved.is_symlink() or not resolved.is_dir():
        raise EvaluationError("private output path must be a real directory")
    os.chmod(resolved, 0o700)
    return resolved


def private_path(root: Path, relative: str) -> Path:
    """Resolve one relative output path without permitting tree escape."""
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise EvaluationError("private output file name is unsafe")
    resolved_root = root.resolve()
    resolved = (resolved_root / candidate).resolve()
    if resolved_root not in resolved.parents:
        raise EvaluationError("private output file escapes the experiment directory")
    return resolved


def production_config() -> dict[str, Any]:
    """Return the exact enabled frozen production tested-pipeline configuration."""
    config = default_config()
    config["enabled"] = True
    errors = validate_strategy_config(config)
    if errors:
        raise EvaluationError("frozen tested-pipeline config is invalid: " + "; ".join(errors))
    expected = {
        "xai_model": LOGICAL_XAI_MODEL,
        "xai_reasoning_effort": LOGICAL_XAI_EFFORT,
        "openai_model": LOGICAL_OPENAI_MODEL,
        "openai_reasoning_effort": LOGICAL_OPENAI_EFFORT,
    }
    if any(config.get(key) != value for key, value in expected.items()):
        raise EvaluationError("frozen tested-pipeline provider identity changed")
    return config


def validate_provider_url(url: str, *, expected_host: str | None = None) -> str:
    """Allow only exact HTTPS API destinations on the two provider hosts."""
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
    permitted_paths = {
        XAI_HOST: {"/v1/models", "/v1/chat/completions"},
        OPENAI_HOST: {"/v1/models", f"/v1/models/{LOGICAL_OPENAI_MODEL}", "/v1/chat/completions"},
    }
    if parsed.path not in permitted_paths[parsed.hostname]:
        raise EvaluationError("provider URL path is not permitted")
    return url


def effective_provider_identity(
    provider: str,
    logical_model: str,
    logical_effort: str,
    arm: str,
) -> tuple[str, str]:
    """Apply an arm only to the effective xAI model and reasoning effort."""
    if arm not in ARMS:
        raise EvaluationError(f"unknown experiment arm: {arm}")
    if provider == "xAI":
        if (logical_model, logical_effort) != (
            LOGICAL_XAI_MODEL,
            LOGICAL_XAI_EFFORT,
        ):
            raise EvaluationError("pipeline changed the logical production xAI identity")
        definition = ARMS[arm]
        return (
            definition["effective_xai_model"],
            definition["effective_xai_effort"],
        )
    if provider == "OpenAI":
        if (logical_model, logical_effort) != (
            LOGICAL_OPENAI_MODEL,
            LOGICAL_OPENAI_EFFORT,
        ):
            raise EvaluationError("pipeline changed the production OpenAI identity")
        return logical_model, logical_effort
    raise EvaluationError(f"unsupported tested-pipeline provider: {provider}")


def stable_arm_order(frozen_corpus_hash: str, case_id: str) -> list[str]:
    """Return a reproducible independently shuffled arm order for one case."""
    seed = int(
        sha256_bytes(
            (frozen_corpus_hash + "\0" + str(case_id)).encode("utf-8")
        ),
        16,
    )
    result = list(ARMS)
    random.Random(seed).shuffle(result)
    return result


def parse_retained_date(value: object) -> str | None:
    """Return the UTC calendar date from one retained observation timestamp."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).date().isoformat()


def _skip(
    source: str,
    identity: object,
    reason_code: str,
    detail: str,
) -> dict[str, str]:
    """Return a bounded machine-readable corpus skip record without conversation text."""
    return {
        "source": source,
        "identity": str(identity or "unavailable")[:256],
        "reason_code": reason_code,
        "detail": detail[:500],
    }


def _turn_has_native_photos(turn: Mapping[str, Any]) -> bool:
    """Return whether retained metadata reports native photos on a turn."""
    summary = turn.get("reply_visual_context_summary")
    return (
        isinstance(summary, dict)
        and type(summary.get("native_photo_count_max")) is int
        and summary["native_photo_count_max"] > 0
    )


def _clarification_from_path(
    path: Sequence[Mapping[str, Any]],
    incoming_index: int,
    principal_author_key: str,
) -> dict[str, str] | None:
    """Recover only an exact user-question/account-clarification/user-correction chain."""
    if incoming_index < 2:
        return None
    original = path[incoming_index - 2]
    clarification = path[incoming_index - 1]
    correction = path[incoming_index]
    if (
        original.get("author_role") != "user"
        or str(original.get("author_key") or "") != principal_author_key
        or clarification.get("author_role") != "account"
        or clarification.get("account_turn_asked_for_clarification") is not True
        or str(clarification.get("parent_post_id") or "")
        != str(original.get("post_id") or "")
        or str(correction.get("parent_post_id") or "")
        != str(clarification.get("post_id") or "")
    ):
        return None
    original_text = original.get("text")
    correction_text = correction.get("text")
    if (
        not isinstance(original_text, str)
        or "?" not in original_text
        or not original_text.strip()
        or not isinstance(correction_text, str)
        or not correction_text.strip()
    ):
        return None
    return {
        "original_question": original_text,
        "correction": correction_text,
    }


def prospective_case_from_row(
    row: object,
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    """Build one exact text-only production-shaped case or a concise skip record."""
    source = "prospective"
    if not isinstance(row, dict):
        return None, _skip(source, "unavailable", "row_not_object", "candidate row is not an object")
    identity = row.get("candidate_key") or row.get("branch_key") or "unavailable"
    required_strings = ("candidate_key", "principal_author_key", "root_post_id")
    missing = [key for key in required_strings if not str(row.get(key) or "").strip()]
    if missing:
        return None, _skip(
            source,
            identity,
            "missing_authoritative_identity",
            "missing " + ",".join(missing),
        )
    warnings = row.get("warnings")
    if not isinstance(warnings, list):
        return None, _skip(source, identity, "warnings_invalid", "warnings must be a list")
    critical = sorted(CRITICAL_RECONSTRUCTION_WARNINGS & {str(value) for value in warnings})
    if critical:
        return None, _skip(
            source,
            identity,
            "incomplete_parentage",
            "critical warnings: " + ",".join(critical),
        )
    path = row.get("path_turns")
    if not isinstance(path, list) or not path or not all(isinstance(turn, dict) for turn in path):
        return None, _skip(source, identity, "path_invalid", "path_turns is incomplete")
    for index in range(1, len(path)):
        if str(path[index].get("parent_post_id") or "") != str(
            path[index - 1].get("post_id") or ""
        ):
            return None, _skip(
                source,
                identity,
                "non_contiguous_parentage",
                f"path edge {index - 1}->{index} is not authoritative",
            )
    principal = str(row["principal_author_key"])
    user_indices = [
        index
        for index, turn in enumerate(path)
        if turn.get("author_role") == "user"
        and str(turn.get("author_key") or "") == principal
    ]
    if not user_indices:
        return None, _skip(source, identity, "incoming_turn_missing", "no principal user turn is retained")
    incoming_index = user_indices[-1]
    incoming = path[incoming_index]
    target_id = str(incoming.get("post_id") or "")
    incoming_text = incoming.get("text")
    if not target_id or not isinstance(incoming_text, str) or not incoming_text.strip():
        return None, _skip(
            source,
            identity,
            "incoming_turn_incomplete",
            "final principal user turn lacks exact ID or text",
        )
    if len(incoming_text) > 10_000:
        return None, _skip(source, identity, "incoming_text_too_long", "incoming text exceeds context bound")
    retained_visuals = row.get("reply_visual_context_summaries")
    if not isinstance(retained_visuals, list):
        return None, _skip(source, identity, "visual_metadata_invalid", "visual summaries must be a list")
    if retained_visuals or any(_turn_has_native_photos(turn) for turn in path[: incoming_index + 1]):
        return None, _skip(
            source,
            identity,
            "image_dependent",
            "retained native-photo context is excluded from the text-only experiment",
        )
    retained_lane = str(incoming.get("lane") or "")
    lane = PIPELINE_LANES.get(retained_lane)
    if lane is None:
        return None, _skip(
            source,
            identity,
            "lane_unavailable",
            f"retained lane is not a tested-pipeline lane: {retained_lane!r}",
        )
    current_date = parse_retained_date(
        incoming.get("first_observed_at") or incoming.get("last_observed_at")
    )
    if current_date is None:
        return None, _skip(
            source,
            identity,
            "observation_date_unavailable",
            "incoming turn has no retained timezone-aware observation date",
        )
    visible_preceding: list[dict[str, str]] = []
    for turn in path[:incoming_index]:
        post_id = str(turn.get("post_id") or "")
        role = turn.get("author_role")
        text = turn.get("text")
        if not post_id or role not in {"account", "user", "unknown"}:
            return None, _skip(
                source,
                identity,
                "preceding_turn_identity_incomplete",
                "a preceding path turn lacks exact ID or role",
            )
        if text is None:
            return None, _skip(
                source,
                identity,
                "preceding_turn_text_unavailable",
                "a preceding path turn has unavailable visible text",
            )
        if not isinstance(text, str) or len(text) > 2_000:
            return None, _skip(
                source,
                identity,
                "preceding_turn_text_invalid",
                "a preceding path turn violates the tested context text bound",
            )
        visible_preceding.append({"post_id": post_id, "author_role": role, "text": text})
    parents = visible_preceding[-3:]
    clarification = _clarification_from_path(path, incoming_index, principal)
    context = {
        "target_id": target_id,
        "thread_id": str(row["root_post_id"]),
        "lane": lane,
        "incoming_contribution": incoming_text,
        "quoted_post": None,
        "parent_thread": parents,
        "clarification_request": clarification,
        "current_date": current_date,
    }
    try:
        clean_context = validate_reply_context(context)
    except (TypeError, ValueError) as exc:
        return None, _skip(
            source,
            identity,
            "reply_context_invalid",
            f"{type(exc).__name__}: {exc}",
        )
    retained_recent = [
        str(turn["text"])
        for turn in path[:incoming_index]
        if turn.get("author_role") == "account"
        and isinstance(turn.get("text"), str)
        and str(turn["text"]).strip()
    ][-20:]
    return {
        "case_id": f"prospective:{row['candidate_key']}",
        "source": "prospective",
        "source_identity": str(row["candidate_key"]),
        "canonical_target_identity": f"x-post:{target_id}",
        "context": clean_context,
        "recent_replies": retained_recent,
        "recent_reply_history": {
            "available": bool(retained_recent),
            "basis": (
                "exact_retained_visible_account_turns_on_path"
                if retained_recent
                else "exact_recent_reply_history_unavailable"
            ),
        },
        "media_context": None,
        "retained_metadata": {
            "candidate_key": str(row["candidate_key"]),
            "branch_key": str(row.get("branch_key") or ""),
            "root_post_id": str(row["root_post_id"]),
            "retained_lane": retained_lane,
            "observation_date_basis": (
                "first_observed_at"
                if incoming.get("first_observed_at")
                else "last_observed_at"
            ),
            "clarification_recovered": clarification is not None,
            "quoted_post_retained": False,
        },
        "expected_public_outcome": None,
    }, None


def _fixture_context(case: Mapping[str, Any], *, prefix: str) -> dict[str, Any]:
    """Reuse the tracked provider pilot's production-schema fixture context shape."""
    case_id = str(case["case_id"])
    return validate_reply_context(
        {
            "target_id": f"pilot-{prefix}{case_id}",
            "thread_id": f"pilot-thread-{prefix}{case_id}",
            "lane": "mention",
            "incoming_contribution": str(case["contribution"]),
            "quoted_post": None,
            "parent_thread": [],
            "clarification_request": copy.deepcopy(case.get("clarification_request")),
            "current_date": "2026-07-20",
        }
    )


def tested_pipeline_regression_challenges() -> list[dict[str, Any]]:
    """Return exact public-outcome contexts retained in tracked pipeline regressions."""
    base_parent = {
        "post_id": "90",
        "author_role": "account",
        "text": "Freedom requires responsibility.",
    }
    definitions = [
        {
            "case_id": "tested-already-answered",
            "incoming": "Did Margaret Thatcher really say this?",
            "parents": [
                {
                    "post_id": "tested-prior-question",
                    "author_role": "user",
                    "text": "Did Margaret Thatcher really say this?",
                },
                {
                    "post_id": "tested-prior-answer",
                    "author_role": "account",
                    "text": "The local transcript records those exact words.",
                },
            ],
            "expected": "no_reply",
            "category": "already_answered_question",
            "source_locator": "tests/test_tested_reply_pipeline.py:52,1693",
        },
        {
            "case_id": "tested-group-hostility",
            "incoming": "Those people can never belong in our community.",
            "parents": [base_parent],
            "expected": "no_reply",
            "category": "group_hostility",
            "source_locator": "tests/test_tested_reply_pipeline.py:2299",
        },
        {
            "case_id": "tested-civil-criticism",
            "incoming": "A civil criticism of the account's interpretation.",
            "parents": [base_parent],
            "expected": "approved",
            "category": "civil_criticism",
            "source_locator": "tests/test_tested_reply_pipeline.py:1762",
        },
    ]
    result: list[dict[str, Any]] = []
    for definition in definitions:
        case_id = str(definition["case_id"])
        context = validate_reply_context(
            {
                "target_id": f"tested-regression-{case_id}",
                "thread_id": f"tested-regression-thread-{case_id}",
                "lane": "mention",
                "incoming_contribution": str(definition["incoming"]),
                "quoted_post": None,
                "parent_thread": copy.deepcopy(definition["parents"]),
                "clarification_request": None,
                "current_date": "2026-08-16",
            }
        )
        result.append(
            {
                "case_id": f"challenge:{case_id}",
                "source": "challenge:tested_pipeline_regression",
                "source_identity": case_id,
                "canonical_target_identity": f"tested-regression:{case_id}",
                "context": context,
                "recent_replies": [],
                "recent_reply_history": {
                    "available": False,
                    "basis": "exact_recent_reply_history_unavailable",
                },
                "media_context": None,
                "retained_metadata": {
                    "coverage_categories": [definition["category"]],
                    "source_locator": definition["source_locator"],
                },
                "expected_public_outcome": {
                    "kind": "fixed_status",
                    "allowed_statuses": [definition["expected"]],
                },
            }
        )
    return result


def load_challenge_cases(
    project_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Load only tracked fixture cases with directly recoverable public constraints."""
    pilot_path = project_dir / "tests/fixtures/ai_first_reply_provider_pilot_cases.json"
    adversarial_path = project_dir / "tests/fixtures/ai_first_reply_adversarial_cases.json"
    pilot = read_json(pilot_path)
    adversarial = read_json(adversarial_path)
    if not isinstance(pilot, list) or not isinstance(adversarial, list):
        raise EvaluationError("tracked challenge fixtures must be arrays")
    cases: list[dict[str, Any]] = []
    skips: list[dict[str, str]] = []
    for row in pilot:
        identity = row.get("case_id") if isinstance(row, dict) else "unavailable"
        try:
            if (
                not isinstance(row, dict)
                or not str(row.get("case_id") or "")
                or not isinstance(row.get("expected_outcomes"), list)
                or not row["expected_outcomes"]
            ):
                raise ValueError("fixture lacks an explicit expected public outcome")
            context = _fixture_context(row, prefix="")
        except (KeyError, TypeError, ValueError) as exc:
            skips.append(
                _skip("challenge:provider_pilot", identity, "challenge_invalid", str(exc))
            )
            continue
        fixture_identity = sha256_value(
            {
                "incoming_contribution": context["incoming_contribution"],
                "clarification_request": context["clarification_request"],
            }
        )
        cases.append(
            {
                "case_id": f"challenge:{row['case_id']}",
                "source": "challenge:provider_pilot",
                "source_identity": str(row["case_id"]),
                "canonical_target_identity": f"fixture-content:{fixture_identity}",
                "context": context,
                "recent_replies": [],
                "recent_reply_history": {
                    "available": False,
                    "basis": "exact_recent_reply_history_unavailable",
                },
                "media_context": None,
                "retained_metadata": {"source_locator": row.get("source_locator")},
                "expected_public_outcome": {
                    "kind": "provider_pilot",
                    "allowed_statuses": [str(value) for value in row["expected_outcomes"]],
                    "required_term_groups": copy.deepcopy(row.get("required_term_groups") or []),
                    "forbidden_phrases": [str(value) for value in row.get("forbidden_phrases") or []],
                },
            }
        )
    for row in adversarial:
        identity = row.get("case_id") if isinstance(row, dict) else "unavailable"
        try:
            if (
                not isinstance(row, dict)
                or not str(row.get("case_id") or "")
                or row.get("expected") != "reject"
                or not isinstance(row.get("bad_reply"), str)
                or not row["bad_reply"].strip()
            ):
                raise ValueError("fixture lacks an explicit rejected public reply")
            context = _fixture_context(row, prefix="adversarial-")
        except (KeyError, TypeError, ValueError) as exc:
            skips.append(
                _skip("challenge:adversarial", identity, "challenge_invalid", str(exc))
            )
            continue
        fixture_identity = sha256_value(
            {
                "incoming_contribution": context["incoming_contribution"],
                "clarification_request": context["clarification_request"],
            }
        )
        cases.append(
            {
                "case_id": f"challenge:adversarial:{row['case_id']}",
                "source": "challenge:adversarial",
                "source_identity": str(row["case_id"]),
                "canonical_target_identity": f"fixture-content:{fixture_identity}",
                "context": context,
                "recent_replies": [],
                "recent_reply_history": {
                    "available": False,
                    "basis": "exact_recent_reply_history_unavailable",
                },
                "media_context": None,
                "retained_metadata": {"failure_category": str(row.get("failure") or "")},
                "expected_public_outcome": {
                    "kind": "reject_known_bad_reply",
                    "known_bad_reply": str(row["bad_reply"]),
                    "failure_category": str(row.get("failure") or ""),
                },
            }
        )
    cases.extend(tested_pipeline_regression_challenges())
    return cases, skips


def load_review_pack(review_pack: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Verify and load one immutable review pack beneath the prospective root."""
    pack = review_pack.expanduser().resolve()
    review_root = (PROSPECTIVE_ROOT / "review-packs").resolve()
    if review_root not in pack.parents or pack == review_root:
        raise EvaluationError("review pack must be beneath the fixed prospective review-pack root")
    if pack.is_symlink() or not pack.is_dir():
        raise EvaluationError("review pack must be a real directory")
    manifest_path = pack / "manifest.json"
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise EvaluationError("review-pack manifest is not an object")
    hashes = manifest.get("output_file_hashes")
    if not isinstance(hashes, dict):
        raise EvaluationError("review-pack manifest lacks output hashes")
    for name in ("conversations.jsonl", "review-candidates.jsonl", "review-pack.md"):
        path = pack / name
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise EvaluationError(f"review-pack member is not a regular file: {name}")
        observed = sha256_file(path)
        if hashes.get(name) != observed:
            raise EvaluationError(f"review-pack member hash mismatch: {name}")
    if manifest.get("include_open") is not True:
        raise EvaluationError("review pack must include open conversations")
    pack_hash = manifest.get("pack_content_sha256")
    if not isinstance(pack_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", pack_hash):
        raise EvaluationError("review-pack content hash is invalid")
    return {
        "path": str(pack),
        "name": str(manifest.get("pack_name") or pack.name),
        "manifest_sha256": sha256_file(manifest_path),
        "pack_content_sha256": pack_hash,
        "source_batch_id": manifest.get("source_batch_id"),
        "source_snapshot_sha256": manifest.get("source_snapshot_sha256"),
        "creation_timestamp": manifest.get("creation_timestamp"),
        "since": manifest.get("since"),
        "until": manifest.get("until"),
        "include_open": manifest.get("include_open"),
        "review_candidate_count": manifest.get("review_candidate_count"),
    }, read_jsonl(pack / "review-candidates.jsonl")


def repository_identity(project_dir: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    """Hash the exact local evidence inputs used by the frozen pipeline."""
    corpus = project_dir / str(config["research_corpus_path"])
    paths = [
        corpus / "corpus_manifest.json",
        corpus / "research_packets.json",
        corpus / "final_unresolved/final_research_status.json",
        project_dir / "reply_factual_evidence.json",
    ]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise EvaluationError("local evidence input is missing: " + ", ".join(missing))
    return {
        "logical_research_corpus_path": str(config["research_corpus_path"]),
        "files": {
            str(path.relative_to(project_dir)): sha256_file(path) for path in paths
        },
    }


def canonical_case_input(
    case: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    evidence_identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Return every non-provider input supplied to one pipeline case."""
    return {
        "context": copy.deepcopy(case["context"]),
        "recent_replies": copy.deepcopy(case["recent_replies"]),
        "media_context": copy.deepcopy(case["media_context"]),
        "maximum_reply_length": MAX_REPLY_LENGTH,
        "logical_production_config": copy.deepcopy(dict(config)),
        "evidence_corpus": copy.deepcopy(dict(evidence_identity)),
    }


def select_cases(
    *,
    challenge_cases: Sequence[dict[str, Any]],
    prospective_cases: Sequence[dict[str, Any]],
    frozen_corpus_hash: str,
    config: Mapping[str, Any],
    evidence_identity: Mapping[str, Any],
    cap: int = MAX_CASES,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Deterministically deduplicate, prioritise challenges, and cap the corpus."""
    if type(cap) is not int or cap <= 0 or cap > MAX_CASES:
        raise EvaluationError(f"case cap must be an integer no greater than {MAX_CASES}")
    skips: list[dict[str, str]] = []
    selected_challenges: list[dict[str, Any]] = []
    seen: set[str] = set()
    for case in sorted(challenge_cases, key=lambda value: str(value["case_id"])):
        identity = str(case["canonical_target_identity"])
        if identity in seen:
            skips.append(
                _skip(case["source"], case["case_id"], "duplicate_identity", identity)
            )
            continue
        seen.add(identity)
        selected_challenges.append(copy.deepcopy(case))
    if len(selected_challenges) > cap:
        raise EvaluationError("valid challenge cases exceed the fixed paid-corpus cap")
    ordered_prospective = sorted(
        prospective_cases,
        key=lambda value: sha256_bytes(
            (
                frozen_corpus_hash
                + "\0"
                + str(value["canonical_target_identity"])
            ).encode("utf-8")
        ),
    )
    selected_prospective: list[dict[str, Any]] = []
    remaining = cap - len(selected_challenges)
    for case in ordered_prospective:
        identity = str(case["canonical_target_identity"])
        if identity in seen:
            skips.append(_skip(case["source"], case["case_id"], "duplicate_identity", identity))
            continue
        seen.add(identity)
        if len(selected_prospective) >= remaining:
            skips.append(_skip(case["source"], case["case_id"], "corpus_cap", f"fixed cap {cap}"))
            continue
        selected_prospective.append(copy.deepcopy(case))
    selected = selected_challenges + selected_prospective
    for index, case in enumerate(selected):
        case["selection_index"] = index
        case["arm_execution_order"] = stable_arm_order(
            frozen_corpus_hash, str(case["case_id"])
        )
        case_input = canonical_case_input(
            case, config=config, evidence_identity=evidence_identity
        )
        case["canonical_case_input_sha256"] = sha256_value(case_input)
    return selected, skips


def prepare_case_set(
    *,
    project_dir: Path,
    review_pack: Path,
    config: Mapping[str, Any],
    evidence_identity: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, str]]]:
    """Validate the frozen pack and build the complete deterministic paid corpus."""
    pack_identity, rows = load_review_pack(review_pack)
    prospective: list[dict[str, Any]] = []
    skips: list[dict[str, str]] = []
    for row in rows:
        case, reason = prospective_case_from_row(row)
        if case is not None:
            prospective.append(case)
        elif reason is not None:
            skips.append(reason)
    challenges, challenge_skips = load_challenge_cases(project_dir)
    skips.extend(challenge_skips)
    selected, selection_skips = select_cases(
        challenge_cases=challenges,
        prospective_cases=prospective,
        frozen_corpus_hash=str(pack_identity["pack_content_sha256"]),
        config=config,
        evidence_identity=evidence_identity,
    )
    skips.extend(selection_skips)
    return pack_identity, selected, skips


def _usage_from_response(raw: Mapping[str, Any]) -> dict[str, int]:
    """Return the common reported token fields from a provider response."""
    usage = raw.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    prompt_details = usage.get("prompt_tokens_details")
    if not isinstance(prompt_details, dict):
        prompt_details = usage.get("input_tokens_details")
    if not isinstance(prompt_details, dict):
        prompt_details = {}
    completion_details = usage.get("completion_tokens_details")
    if not isinstance(completion_details, dict):
        completion_details = usage.get("output_tokens_details")
    if not isinstance(completion_details, dict):
        completion_details = {}
    return {
        "input_tokens": int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
        "cached_input_tokens": int(
            prompt_details.get("cached_tokens") or usage.get("cached_tokens") or 0
        ),
        "cache_write_tokens": int(prompt_details.get("cache_write_tokens") or 0),
        "output_tokens": int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        ),
        "reasoning_tokens": int(
            completion_details.get("reasoning_tokens")
            or usage.get("reasoning_tokens")
            or 0
        ),
    }


def estimate_openai_cost_ticks(
    raw: Mapping[str, Any], *, fallback_ticks: int
) -> tuple[int, str]:
    """Estimate one OpenAI call from reported usage or its reserved upper bound."""
    usage = raw.get("usage")
    if not isinstance(usage, dict):
        return fallback_ticks, "reserved_upper_bound_no_usage"
    tokens = _usage_from_response(raw)
    input_tokens = tokens["input_tokens"]
    cached_tokens = min(input_tokens, tokens["cached_input_tokens"])
    cache_write_tokens = min(
        max(0, input_tokens - cached_tokens), tokens["cache_write_tokens"]
    )
    ordinary_tokens = max(0, input_tokens - cached_tokens - cache_write_tokens)
    estimate = (
        ordinary_tokens * OPENAI_INPUT_TICKS_PER_TOKEN
        + cached_tokens * OPENAI_CACHED_INPUT_TICKS_PER_TOKEN
        + cache_write_tokens * OPENAI_CACHE_WRITE_TICKS_PER_TOKEN
        + tokens["output_tokens"] * OPENAI_OUTPUT_TICKS_PER_TOKEN
    )
    return estimate, "usage_times_published_standard_rates"


class RequestLedger:
    """Persist request identities, cost exposure, retries, and ambiguous outcomes."""

    def __init__(
        self,
        path: Path,
        *,
        case_set_sha256: str,
        hard_limit_usd: float = 35.0,
    ) -> None:
        """Open or create an identity-bound private request ledger."""
        self.path = path
        self.limit_ticks = int(round(hard_limit_usd * USD_TICKS_PER_DOLLAR))
        if self.limit_ticks <= 0 or self.limit_ticks > 35 * USD_TICKS_PER_DOLLAR:
            raise EvaluationError("global provider-cost ceiling must be in (0, 35.00]")
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
                raise EvaluationError("request ledger identity or cost ceiling changed")
        else:
            self.data = {
                "schema_version": SCHEMA_VERSION,
                "run_version": RUN_VERSION,
                "case_set_sha256": case_set_sha256,
                "hard_limit_ticks": self.limit_ticks,
                "hard_limit_usd": self.limit_ticks / USD_TICKS_PER_DOLLAR,
                "blocked": False,
                "blocked_reason": None,
                "operations": [],
                "created_at": utc_now(),
            }
            self._save()
        self._validate_unique_hashes()

    def _validate_unique_hashes(self) -> None:
        """Reject duplicate or malformed durable request identities."""
        hashes = [
            str(row.get("request_hash") or "")
            for row in self.data["operations"]
            if isinstance(row, dict)
        ]
        if (
            len(hashes) != len(self.data["operations"])
            or any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes)
            or len(hashes) != len(set(hashes))
        ):
            raise EvaluationError("request ledger contains invalid or duplicate identities")

    def _save(self) -> None:
        """Refresh aggregate exposure and durably save the ledger."""
        summary = self.cost_summary()
        self.data.update(summary)
        self.data["updated_at"] = utc_now()
        atomic_json(self.path, self.data)

    def operation(self, request_hash: str) -> dict[str, Any] | None:
        """Return one prior operation for a canonical request hash."""
        matches = [
            row
            for row in self.data["operations"]
            if isinstance(row, dict) and row.get("request_hash") == request_hash
        ]
        if len(matches) > 1:
            raise EvaluationError("request ledger identity is duplicated")
        return matches[0] if matches else None

    def add_consumer(self, row: dict[str, Any], consumer_id: str) -> None:
        """Record one arm/case consumer without changing request identity."""
        consumers = row.setdefault("consumers", [])
        if consumer_id not in consumers:
            consumers.append(consumer_id)
            consumers.sort()
            self._save()

    def _operation_exposure_ticks(self, row: Mapping[str, Any]) -> int:
        """Return conservative current cost exposure for one operation."""
        status = row.get("status")
        if status == "completed":
            provider_ticks = row.get("provider_reported_cost_in_usd_ticks")
            if type(provider_ticks) is int and provider_ticks >= 0:
                return provider_ticks
            return int(row.get("estimated_cost_in_usd_ticks") or 0)
        if status in {"prepared", "sending", "rate_limited", "server_error", "ambiguous"}:
            return int(row.get("maximum_possible_cost_ticks") or 0)
        return 0

    def reserve(
        self,
        *,
        request_hash: str,
        provider: str,
        stage: str,
        logical_model: str,
        logical_effort: str,
        effective_model: str,
        effective_effort: str,
        maximum_possible_cost_ticks: int,
        consumer_id: str,
    ) -> dict[str, Any]:
        """Reserve a new canonical request beneath the global cost ceiling."""
        prior = self.operation(request_hash)
        if prior is not None:
            immutable = {
                "provider": provider,
                "logical_model": logical_model,
                "logical_reasoning_effort": logical_effort,
                "effective_model": effective_model,
                "effective_reasoning_effort": effective_effort,
                "maximum_possible_cost_ticks": maximum_possible_cost_ticks,
            }
            if any(prior.get(key) != value for key, value in immutable.items()):
                raise EvaluationError("canonical request metadata changed on resume")
            self.add_consumer(prior, consumer_id)
            return prior
        if self.data.get("blocked"):
            raise AmbiguousRequestError(str(self.data.get("blocked_reason") or "ledger blocked"))
        if type(maximum_possible_cost_ticks) is not int or maximum_possible_cost_ticks <= 0:
            raise EvaluationError("request cost reservation must be a positive integer")
        exposure = sum(
            self._operation_exposure_ticks(row)
            for row in self.data["operations"]
            if isinstance(row, dict)
        )
        if exposure + maximum_possible_cost_ticks > self.limit_ticks:
            raise CostLimitReached(
                "next request could exceed the global US$"
                f"{self.limit_ticks / USD_TICKS_PER_DOLLAR:.2f} ceiling"
            )
        row = {
            "request_hash": request_hash,
            "provider": provider,
            "stage": stage,
            "logical_model": logical_model,
            "logical_reasoning_effort": logical_effort,
            "effective_model": effective_model,
            "effective_reasoning_effort": effective_effort,
            "maximum_possible_cost_ticks": maximum_possible_cost_ticks,
            "maximum_possible_cost_usd": (
                maximum_possible_cost_ticks / USD_TICKS_PER_DOLLAR
            ),
            "provider_reported_cost_in_usd_ticks": None,
            "provider_reported_cost_usd": None,
            "estimated_cost_in_usd_ticks": None,
            "estimated_cost_usd": None,
            "estimate_basis": None,
            "input_tokens": None,
            "cached_input_tokens": None,
            "cache_write_tokens": None,
            "output_tokens": None,
            "reasoning_tokens": None,
            "request_id": None,
            "response_hash": None,
            "response_model": None,
            "provider_latency_seconds": None,
            "attempt_number": 0,
            "attempt_events": [],
            "consumers": [consumer_id],
            "status": "prepared",
            "prepared_at": utc_now(),
        }
        self.data["operations"].append(row)
        self._save()
        return row

    def sending(self, row: dict[str, Any]) -> None:
        """Durably mark one additional transmission attempt before sending it."""
        row["attempt_number"] = int(row.get("attempt_number") or 0) + 1
        row["status"] = "sending"
        row["sending_at"] = utc_now()
        self._save()

    def definite_retryable_response(
        self,
        row: dict[str, Any],
        *,
        status_code: int,
        response_hash: str,
        latency_seconds: float,
    ) -> None:
        """Record a definite 429 or 5xx response that may be retried once."""
        status = "rate_limited" if status_code == 429 else "server_error"
        row["status"] = status
        row.setdefault("attempt_events", []).append(
            {
                "attempt_number": row["attempt_number"],
                "status": status,
                "http_status_code": status_code,
                "response_hash": response_hash,
                "latency_seconds": latency_seconds,
                "recorded_at": utc_now(),
            }
        )
        self._save()

    def definite_http_error(
        self,
        row: dict[str, Any],
        *,
        status_code: int,
        response_hash: str,
        latency_seconds: float,
    ) -> None:
        """Record a definite non-retryable HTTP response."""
        row.update(
            {
                "status": "http_error",
                "http_status_code": status_code,
                "http_response_hash": response_hash,
                "provider_latency_seconds": latency_seconds,
                "http_error_at": utc_now(),
            }
        )
        row.setdefault("attempt_events", []).append(
            {
                "attempt_number": row["attempt_number"],
                "status": "http_error",
                "http_status_code": status_code,
                "response_hash": response_hash,
                "latency_seconds": latency_seconds,
                "recorded_at": utc_now(),
            }
        )
        self._save()

    def complete(
        self,
        row: dict[str, Any],
        *,
        raw: Mapping[str, Any],
        latency_seconds: float,
    ) -> None:
        """Record a completed response, reported usage, and authoritative or estimated cost."""
        usage = raw.get("usage")
        provider_ticks = (
            usage.get("cost_in_usd_ticks") if isinstance(usage, dict) else None
        )
        if type(provider_ticks) is not int or provider_ticks < 0:
            provider_ticks = None
        if row["provider"] == "xAI" and provider_ticks is None:
            self.ambiguous(
                row,
                EvaluationError("completed xAI response lacks provider-reported cost_in_usd_ticks"),
            )
            raise EvaluationError(
                "completed xAI response lacks provider-reported cost_in_usd_ticks"
            )
        if provider_ticks is None:
            estimated_ticks, estimate_basis = estimate_openai_cost_ticks(
                raw,
                fallback_ticks=int(row["maximum_possible_cost_ticks"]),
            )
        else:
            estimated_ticks, estimate_basis = None, None
        tokens = _usage_from_response(raw)
        response_hash = sha256_value(raw)
        prior_latencies = sum(
            float(event.get("latency_seconds") or 0)
            for event in row.get("attempt_events") or []
            if isinstance(event, dict)
        )
        row.update(
            {
                "status": "completed",
                "provider_reported_cost_in_usd_ticks": provider_ticks,
                "provider_reported_cost_usd": (
                    provider_ticks / USD_TICKS_PER_DOLLAR
                    if provider_ticks is not None
                    else None
                ),
                "estimated_cost_in_usd_ticks": estimated_ticks,
                "estimated_cost_usd": (
                    estimated_ticks / USD_TICKS_PER_DOLLAR
                    if estimated_ticks is not None
                    else None
                ),
                "estimate_basis": estimate_basis,
                **tokens,
                "request_id": str(raw.get("id") or ""),
                "response_hash": response_hash,
                "response_model": str(raw.get("model") or ""),
                "provider_latency_seconds": prior_latencies + latency_seconds,
                "completed_at": utc_now(),
            }
        )
        row.setdefault("attempt_events", []).append(
            {
                "attempt_number": row["attempt_number"],
                "status": "completed",
                "latency_seconds": latency_seconds,
                "response_hash": response_hash,
                "recorded_at": utc_now(),
            }
        )
        self._save()

    def ambiguous(self, row: dict[str, Any], error: BaseException) -> None:
        """Block future transmissions after an outcome that cannot be recovered safely."""
        row.update(
            {
                "status": "ambiguous",
                "error": f"{type(error).__name__}: {error}",
                "ambiguous_at": utc_now(),
            }
        )
        self.data["blocked"] = True
        self.data["blocked_reason"] = (
            "ambiguous transmitted request " + str(row.get("request_hash") or "")
        )
        self._save()

    def cost_summary(self) -> dict[str, Any]:
        """Return clearly separated billed, estimated, and ambiguous exposure."""
        operations = [row for row in self.data.get("operations", []) if isinstance(row, dict)]
        billed = sum(
            int(row.get("provider_reported_cost_in_usd_ticks") or 0)
            for row in operations
            if row.get("status") == "completed"
        )
        estimated = sum(
            int(row.get("estimated_cost_in_usd_ticks") or 0)
            for row in operations
            if row.get("status") == "completed"
        )
        ambiguous = sum(
            int(row.get("maximum_possible_cost_ticks") or 0)
            for row in operations
            if row.get("status") == "ambiguous"
        )
        exposure = sum(self._operation_exposure_ticks(row) for row in operations)
        return {
            "total_billed_cost_in_usd_ticks": billed,
            "total_billed_cost_usd": billed / USD_TICKS_PER_DOLLAR,
            "total_estimated_cost_in_usd_ticks": estimated,
            "total_estimated_cost_usd": estimated / USD_TICKS_PER_DOLLAR,
            "ambiguous_exposure_in_usd_ticks": ambiguous,
            "ambiguous_exposure_usd": ambiguous / USD_TICKS_PER_DOLLAR,
            "current_conservative_exposure_in_usd_ticks": exposure,
            "current_conservative_exposure_usd": exposure / USD_TICKS_PER_DOLLAR,
        }


def provider_request_body(
    *,
    provider: str,
    stage: str,
    model: str,
    reasoning_effort: str,
    system_prompt: str,
    payload: Mapping[str, Any],
    response_schema: Mapping[str, Any],
    max_output_tokens: int,
) -> dict[str, Any]:
    """Reproduce the production tested-pipeline Chat Completions request format."""
    schema_name = "mrs_tested_" + re.sub(
        r"[^a-z0-9_]+", "_", stage.lower()
    )[:48]
    request: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            },
        ],
        "reasoning_effort": reasoning_effort,
        "temperature": 1,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": copy.deepcopy(dict(response_schema)),
            },
        },
    }
    if provider == "xAI":
        request["max_tokens"] = max_output_tokens
    elif provider == "OpenAI":
        request["max_completion_tokens"] = max_output_tokens
        request["store"] = False
        request["prompt_cache_options"] = {"mode": "explicit"}
    else:
        raise EvaluationError(f"unsupported tested-pipeline provider: {provider}")
    return request


def canonical_request_identity(
    *,
    provider: str,
    model: str,
    reasoning_effort: str,
    system_prompt: str,
    user_payload: Mapping[str, Any],
    response_schema: Mapping[str, Any],
    maximum_output_tokens: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Return the complete canonical provider-request cache identity."""
    return {
        "provider": provider,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "system_prompt": system_prompt,
        "user_payload": copy.deepcopy(dict(user_payload)),
        "response_schema": copy.deepcopy(dict(response_schema)),
        "maximum_output_tokens": maximum_output_tokens,
        "timeout_seconds": timeout_seconds,
    }


def maximum_request_cost_ticks(
    *,
    provider: str,
    request_body: Mapping[str, Any],
    max_output_tokens: int,
    effective_model: str,
    xai_model_metadata: Mapping[str, Mapping[str, Any]],
) -> int:
    """Return a conservative byte/token upper-bound reservation for one request."""
    input_upper_bound = max(1, len(canonical_json_bytes(request_body)))
    if provider == "OpenAI":
        input_rate = OPENAI_CACHE_WRITE_TICKS_PER_TOKEN
        output_rate = OPENAI_OUTPUT_TICKS_PER_TOKEN
        if input_upper_bound > 272_000:
            input_rate *= 2
            output_rate = int(output_rate * 1.5)
        return input_upper_bound * input_rate + max_output_tokens * output_rate
    metadata = xai_model_metadata.get(effective_model)
    if not isinstance(metadata, Mapping):
        raise ModelAvailabilityError(
            f"no authenticated xAI pricing metadata for {effective_model}"
        )
    input_rate = metadata.get("prompt_text_token_price")
    output_rate = metadata.get("completion_text_token_price")
    if type(input_rate) is not int or input_rate <= 0 or type(output_rate) is not int or output_rate <= 0:
        raise ModelAvailabilityError(
            f"authenticated xAI pricing metadata is incomplete for {effective_model}"
        )
    return (
        input_upper_bound * input_rate
        + max_output_tokens * REASONING_OUTPUT_MULTIPLIER * output_rate
    )


def _response_payload_bytes(response: Any) -> bytes:
    """Return stable response bytes for definite HTTP-event hashing."""
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content
    return str(getattr(response, "text", "")).encode("utf-8", errors="replace")


def _extract_provider_content(raw: Mapping[str, Any]) -> object:
    """Extract structured Chat Completions content without schema interpretation."""
    try:
        content = raw["choices"][0]["message"]["content"]  # type: ignore[index]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("provider response shape is invalid") from exc
    if not isinstance(content, (str, dict)):
        raise ProviderError("provider response content is not structured JSON")
    return copy.deepcopy(content)


class ResearchTransport:
    """Arm-aware, allowlisted, durable provider transport for the tested pipeline."""

    def __init__(
        self,
        *,
        arm: str,
        case_id: str,
        api_keys: Mapping[str, str],
        xai_model_metadata: Mapping[str, Mapping[str, Any]],
        ledger: RequestLedger,
        response_dir: Path,
        post: Callable[..., Any] = requests.post,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Initialise one arm/case view over the shared canonical request cache."""
        if arm not in ARMS:
            raise EvaluationError(f"unknown experiment arm: {arm}")
        self.arm = arm
        self.case_id = str(case_id)
        self.api_keys = dict(api_keys)
        self.xai_model_metadata = xai_model_metadata
        self.ledger = ledger
        self.response_dir = response_dir
        self.post = post
        self.sleep = sleep
        self.sequence = 0
        self.call_events: list[dict[str, Any]] = []
        os.makedirs(response_dir, mode=0o700, exist_ok=True)
        os.chmod(response_dir, 0o700)

    def _cache_path(self, request_hash: str) -> Path:
        """Return the private raw-response cache path for one request hash."""
        return self.response_dir / f"{request_hash}.json"

    def _read_cache(self, path: Path, request_hash: str) -> dict[str, Any]:
        """Read and identity-check one raw response cache record."""
        cached = read_json(path)
        if (
            not isinstance(cached, dict)
            or cached.get("request_hash") != request_hash
            or not isinstance(cached.get("raw"), dict)
        ):
            raise EvaluationError("raw response cache identity is invalid")
        return cached

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
        """Serve one pipeline request from cache or one bounded live transmission."""
        started = time.monotonic()
        self.sequence += 1
        effective_model, effective_effort = effective_provider_identity(
            provider, model, reasoning_effort, self.arm
        )
        request_body = provider_request_body(
            provider=provider,
            stage=stage,
            model=effective_model,
            reasoning_effort=effective_effort,
            system_prompt=system_prompt,
            payload=payload,
            response_schema=response_schema,
            max_output_tokens=max_output_tokens,
        )
        wrapped_schema = request_body["response_format"]["json_schema"]
        identity = canonical_request_identity(
            provider=provider,
            model=effective_model,
            reasoning_effort=effective_effort,
            system_prompt=system_prompt,
            user_payload=payload,
            response_schema=wrapped_schema,
            maximum_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
        )
        request_hash = sha256_value(identity)
        consumer_id = f"{self.case_id}:{self.arm}:{self.sequence}:{stage}"
        event: dict[str, Any] = {
            "request_hash": request_hash,
            "provider": provider,
            "stage": stage,
            "logical_model": model,
            "logical_reasoning_effort": reasoning_effort,
            "effective_model": effective_model,
            "effective_reasoning_effort": effective_effort,
            "cache_hit": False,
            "provider_error": None,
        }
        self.call_events.append(event)
        cache_path = self._cache_path(request_hash)
        prior = self.ledger.operation(request_hash)
        try:
            if prior is not None:
                self.ledger.add_consumer(prior, consumer_id)
                if cache_path.exists():
                    cached = self._read_cache(cache_path, request_hash)
                    if prior.get("status") != "completed":
                        self.ledger.complete(
                            prior,
                            raw=cached["raw"],
                            latency_seconds=float(cached.get("latency_seconds") or 0),
                        )
                    event["cache_hit"] = True
                    event.update(self._event_usage(prior))
                    return _extract_provider_content(cached["raw"])
                if prior.get("status") == "sending":
                    error = AmbiguousRequestError(
                        f"transmission outcome is ambiguous for request {request_hash}"
                    )
                    self.ledger.ambiguous(prior, error)
                    raise error
                if prior.get("status") == "ambiguous":
                    raise AmbiguousRequestError(
                        f"request {request_hash} is durably marked ambiguous"
                    )
                if prior.get("status") == "http_error":
                    raise DefiniteProviderError(
                        f"request previously received HTTP {prior.get('http_status_code')}"
                    )
                if prior.get("status") in {"rate_limited", "server_error"}:
                    if int(prior.get("attempt_number") or 0) >= 2:
                        raise TransientProviderError(
                            f"request exhausted its bounded {prior['status']} retry"
                        )
                    self.sleep(1.0)
                elif prior.get("status") != "prepared":
                    raise EvaluationError(
                        f"unsupported prior request status: {prior.get('status')!r}"
                    )
            elif cache_path.exists():
                raise EvaluationError("orphan response cache exists without a request ledger row")
            maximum_ticks = maximum_request_cost_ticks(
                provider=provider,
                request_body=request_body,
                max_output_tokens=max_output_tokens,
                effective_model=effective_model,
                xai_model_metadata=self.xai_model_metadata,
            )
            row = self.ledger.reserve(
                request_hash=request_hash,
                provider=provider,
                stage=stage,
                logical_model=model,
                logical_effort=reasoning_effort,
                effective_model=effective_model,
                effective_effort=effective_effort,
                maximum_possible_cost_ticks=maximum_ticks,
                consumer_id=consumer_id,
            )
            url = (
                f"{XAI_BASE_URL}/chat/completions"
                if provider == "xAI"
                else f"{OPENAI_BASE_URL}/chat/completions"
            )
            expected_host = XAI_HOST if provider == "xAI" else OPENAI_HOST
            validate_provider_url(url, expected_host=expected_host)
            api_key = self.api_keys.get(provider, "")
            if not api_key:
                raise EvaluationError(f"{provider} API key is unavailable")
            while True:
                self.ledger.sending(row)
                attempt_started = time.monotonic()
                try:
                    response = self.post(
                        url,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json=request_body,
                        timeout=timeout_seconds,
                        allow_redirects=False,
                    )
                except BaseException as exc:
                    error = AmbiguousRequestError(
                        f"{provider} request raised before a definite response: {type(exc).__name__}"
                    )
                    self.ledger.ambiguous(row, error)
                    raise error from exc
                latency = time.monotonic() - attempt_started
                status_code = int(getattr(response, "status_code", 0) or 0)
                response_hash = sha256_bytes(_response_payload_bytes(response))
                if status_code == 429 or 500 <= status_code <= 599:
                    self.ledger.definite_retryable_response(
                        row,
                        status_code=status_code,
                        response_hash=response_hash,
                        latency_seconds=latency,
                    )
                    if int(row["attempt_number"]) >= 2:
                        raise TransientProviderError(
                            f"{provider} returned HTTP {status_code} twice"
                        )
                    self.sleep(1.0)
                    continue
                if status_code < 200 or status_code >= 300:
                    self.ledger.definite_http_error(
                        row,
                        status_code=status_code,
                        response_hash=response_hash,
                        latency_seconds=latency,
                    )
                    raise DefiniteProviderError(
                        f"{provider} returned definite HTTP {status_code}"
                    )
                try:
                    raw = response.json()
                    if not isinstance(raw, dict):
                        raise ValueError("response JSON is not an object")
                except BaseException as exc:
                    error = AmbiguousRequestError(
                        f"{provider} returned an unusable successful response"
                    )
                    self.ledger.ambiguous(row, error)
                    raise error from exc
                try:
                    atomic_json(
                        cache_path,
                        {
                            "request_hash": request_hash,
                            "received_at": utc_now(),
                            "latency_seconds": latency,
                            "raw": raw,
                        },
                    )
                    self.ledger.complete(row, raw=raw, latency_seconds=latency)
                except BaseException:
                    if not cache_path.exists() and row.get("status") != "ambiguous":
                        self.ledger.ambiguous(
                            row,
                            AmbiguousRequestError(
                                "provider response was received but could not be cached"
                            ),
                        )
                    raise
                event.update(self._event_usage(row))
                return _extract_provider_content(raw)
        except BaseException as exc:
            event["provider_error"] = f"{type(exc).__name__}: {exc}"
            prior_or_current = self.ledger.operation(request_hash)
            if prior_or_current is not None:
                event.update(self._event_usage(prior_or_current))
            raise
        finally:
            event["wall_latency_seconds"] = round(time.monotonic() - started, 6)

    @staticmethod
    def _event_usage(row: Mapping[str, Any]) -> dict[str, Any]:
        """Return one non-payload request summary for an arm result."""
        return {
            "request_status": row.get("status"),
            "input_tokens": row.get("input_tokens"),
            "cached_input_tokens": row.get("cached_input_tokens"),
            "cache_write_tokens": row.get("cache_write_tokens"),
            "output_tokens": row.get("output_tokens"),
            "reasoning_tokens": row.get("reasoning_tokens"),
            "provider_reported_cost_usd": row.get("provider_reported_cost_usd"),
            "estimated_cost_usd": row.get("estimated_cost_usd"),
            "provider_latency_seconds": row.get("provider_latency_seconds"),
            "response_model": row.get("response_model"),
        }


def fetch_xai_model_metadata(
    *,
    api_key: str,
    get: Callable[..., Any] = requests.get,
) -> dict[str, dict[str, Any]]:
    """Verify both experimental xAI models through the authenticated models endpoint."""
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
    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code != 200:
        raise ModelAvailabilityError(
            f"xAI models endpoint returned HTTP {status_code}"
        )
    try:
        document = response.json()
    except BaseException as exc:
        raise ModelAvailabilityError("xAI models endpoint returned invalid JSON") from exc
    rows = document.get("data") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        raise ModelAvailabilityError("xAI models endpoint response lacks a data array")
    required_models = {"grok-4.3", "grok-4.6"}
    selected = {
        str(row.get("id")): row
        for row in rows
        if isinstance(row, dict) and row.get("id") in required_models
    }
    missing = sorted(required_models - set(selected))
    if missing:
        observed_requested = sorted(required_models & set(selected))
        raise ModelAvailabilityError(
            "required xAI model availability check failed; missing="
            + ",".join(missing)
            + "; observed_requested="
            + (",".join(observed_requested) or "none")
        )
    required_prices = (
        "prompt_text_token_price",
        "cached_prompt_text_token_price",
        "completion_text_token_price",
    )
    result: dict[str, dict[str, Any]] = {}
    for model in sorted(required_models):
        row = selected[model]
        invalid = [
            key
            for key in required_prices
            if type(row.get(key)) is not int or row[key] <= 0
        ]
        if invalid:
            raise ModelAvailabilityError(
                f"xAI model {model} lacks positive pricing metadata: {','.join(invalid)}"
            )
        result[model] = {
            "id": model,
            **{key: row[key] for key in required_prices},
            "retrieved_at": utc_now(),
            "usd_ticks_per_dollar": USD_TICKS_PER_DOLLAR,
        }
    return result


def git_revision(project_dir: Path, revision: str) -> str:
    """Resolve one Git revision without changing repository state."""
    process = subprocess.run(
        ["git", "rev-parse", revision],
        cwd=project_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    value = process.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise EvaluationError(f"Git revision {revision!r} is not a full SHA-1")
    return value


def create_or_load_arm_key(path: Path) -> dict[str, str]:
    """Create once or validate the sole private A/B/C/D-to-W/X/Y/Z mapping."""
    if path.exists():
        value = read_json(path)
        if (
            not isinstance(value, dict)
            or set(value) != set(ARMS)
            or set(value.values()) != set(BLIND_LABELS)
            or not all(isinstance(item, str) for item in value.values())
        ):
            raise EvaluationError("private blind arm key is invalid")
        return dict(value)
    labels = list(BLIND_LABELS)
    secrets.SystemRandom().shuffle(labels)
    value = dict(zip(ARMS, labels))
    atomic_json(path, value)
    return value


def _immutable_manifest_fields(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return the fields that must remain exact across safe resume."""
    excluded = {
        "created_at",
        "updated_at",
        "execution",
        "provider_models_actually_observed",
    }
    return {key: copy.deepcopy(value) for key, value in manifest.items() if key not in excluded}


def prepare_experiment(
    *,
    project_dir: Path,
    output_dir: Path,
    review_pack: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, str]]]:
    """Prepare and durably bind the entire corpus before any model call."""
    config = production_config()
    evidence_identity = repository_identity(project_dir, config)
    pack_identity, cases, skips = prepare_case_set(
        project_dir=project_dir,
        review_pack=review_pack,
        config=config,
        evidence_identity=evidence_identity,
    )
    case_set_sha256 = sha256_value(cases)
    tool_path = Path(__file__).resolve()
    fixture_paths = [
        project_dir / "tests/fixtures/ai_first_reply_provider_pilot_cases.json",
        project_dir / "tests/fixtures/ai_first_reply_adversarial_cases.json",
        project_dir / "tests/test_tested_reply_pipeline.py",
    ]
    counts = {
        "prospective": sum(case["source"] == "prospective" for case in cases),
        "challenge": sum(str(case["source"]).startswith("challenge:") for case in cases),
        "skipped": len(skips),
        "total": len(cases),
        "skipped_reason_counts": dict(
            sorted(Counter(row["reason_code"] for row in skips).items())
        ),
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_version": RUN_VERSION,
        "created_at": utc_now(),
        "source_git_sha": git_revision(project_dir, "HEAD"),
        "source_origin_master_sha": git_revision(project_dir, "origin/master"),
        "project_dir": str(project_dir),
        "output_dir": str(output_dir),
        "frozen_review_pack": pack_identity,
        "case_set_sha256": case_set_sha256,
        "case_counts": counts,
        "tool_sha256": sha256_file(tool_path),
        "fixture_hashes": {
            str(path.relative_to(project_dir)): sha256_file(path)
            for path in fixture_paths
        },
        "evidence_corpus": evidence_identity,
        "logical_production_config": config,
        "arm_definitions": copy.deepcopy(ARMS),
        "logical_xai_identity": {
            "model": LOGICAL_XAI_MODEL,
            "reasoning_effort": LOGICAL_XAI_EFFORT,
        },
        "openai_identity": {
            "model": LOGICAL_OPENAI_MODEL,
            "reasoning_effort": LOGICAL_OPENAI_EFFORT,
        },
        "network_allowlist": sorted(NETWORK_ALLOWLIST),
        "posting_enabled": False,
        "x_client_imported": False,
        "media_transmitted": False,
        "text_only": True,
        "maximum_paid_cases": MAX_CASES,
        "global_cost_ceiling_usd": 35.0,
        "openai_estimate_pricing": {
            "source": OPENAI_PRICING_SOURCE,
            "observed_date": "2026-08-28",
            "input_usd_per_million_tokens": 4.0,
            "cached_input_usd_per_million_tokens": 0.4,
            "cache_write_usd_per_million_tokens": 5.0,
            "output_usd_per_million_tokens": 20.0,
            "authoritative": False,
        },
        "provider_models_actually_observed": {
            "xAI_models_endpoint": [],
            "xAI_response_models": [],
            "OpenAI_response_models": [],
        },
        "execution": {
            "mode": "prepared_only",
            "blocker": None,
            "cost": None,
        },
    }
    manifest_path = private_path(output_dir, "manifest.json")
    if manifest_path.exists():
        existing = read_json(manifest_path)
        if not isinstance(existing, dict):
            raise EvaluationError("existing experiment manifest is invalid")
        comparable = copy.deepcopy(manifest)
        comparable["created_at"] = existing.get("created_at")
        comparable["updated_at"] = existing.get("updated_at")
        comparable["execution"] = existing.get("execution")
        comparable["provider_models_actually_observed"] = existing.get(
            "provider_models_actually_observed"
        )
        if _immutable_manifest_fields(existing) != _immutable_manifest_fields(comparable):
            raise EvaluationError("immutable experiment inputs changed on resume")
        manifest = existing
    else:
        atomic_json(manifest_path, manifest)
    cases_path = private_path(output_dir, "cases.jsonl")
    expected_cases_bytes = b"".join(
        canonical_json_bytes(case, newline=True) for case in cases
    )
    if cases_path.exists() and cases_path.read_bytes() != expected_cases_bytes:
        raise EvaluationError("prepared case set differs from existing private cases.jsonl")
    if not cases_path.exists():
        atomic_bytes(cases_path, expected_cases_bytes)
    skips_path = private_path(output_dir, "skipped.jsonl")
    expected_skips_bytes = b"".join(
        canonical_json_bytes(row, newline=True) for row in skips
    )
    if skips_path.exists() and skips_path.read_bytes() != expected_skips_bytes:
        raise EvaluationError("prepared skip set differs from existing private skipped.jsonl")
    if not skips_path.exists():
        atomic_bytes(skips_path, expected_skips_bytes)
    results_path = private_path(output_dir, "arm_results.jsonl")
    if not results_path.exists():
        atomic_bytes(results_path, b"")
    create_or_load_arm_key(private_path(output_dir, "arm_key.private.json"))
    return manifest, cases, skips


def update_manifest_execution(
    output_dir: Path,
    *,
    mode: str,
    blocker: str | None,
    ledger: RequestLedger | None,
    xai_metadata: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Update only operational manifest fields after preparation."""
    path = private_path(output_dir, "manifest.json")
    manifest = read_json(path)
    if not isinstance(manifest, dict):
        raise EvaluationError("experiment manifest is invalid")
    operations = ledger.data["operations"] if ledger is not None else []
    observed = {
        "xAI_models_endpoint": sorted(xai_metadata or {}),
        "xAI_response_models": sorted(
            {
                str(row.get("response_model") or row.get("effective_model") or "")
                for row in operations
                if isinstance(row, dict)
                and row.get("provider") == "xAI"
                and row.get("status") == "completed"
                and str(row.get("response_model") or row.get("effective_model") or "")
            }
        ),
        "OpenAI_response_models": sorted(
            {
                str(row.get("response_model") or row.get("effective_model") or "")
                for row in operations
                if isinstance(row, dict)
                and row.get("provider") == "OpenAI"
                and row.get("status") == "completed"
                and str(row.get("response_model") or row.get("effective_model") or "")
            }
        ),
    }
    manifest["provider_models_actually_observed"] = observed
    manifest["execution"] = {
        "mode": mode,
        "blocker": blocker,
        "cost": ledger.cost_summary() if ledger is not None else None,
        "request_count": len(operations),
        "completed_request_count": sum(
            isinstance(row, dict) and row.get("status") == "completed"
            for row in operations
        ),
        "cache_consumer_count": sum(
            max(0, len(row.get("consumers") or []) - 1)
            for row in operations
            if isinstance(row, dict)
        ),
    }
    manifest["updated_at"] = utc_now()
    atomic_json(path, manifest)
    return manifest


def load_api_keys(env_file: Path | None = None) -> dict[str, str]:
    """Load only provider keys from the environment or the existing private env file."""
    values = {
        "XAI_API_KEY": os.getenv("XAI_API_KEY", ""),
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", ""),
    }
    if all(values.values()) or env_file is None or not env_file.exists():
        return {"xAI": values["XAI_API_KEY"], "OpenAI": values["OPENAI_API_KEY"]}
    info = env_file.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise EvaluationError("provider environment file must be a regular non-symlink file")
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        match = re.fullmatch(r"(XAI_API_KEY|OPENAI_API_KEY)=(.*)", line)
        if match is None or values[match.group(1)]:
            continue
        try:
            parsed = shlex.split(match.group(2), comments=True, posix=True)
        except ValueError as exc:
            raise EvaluationError("provider environment file contains an invalid key value") from exc
        if len(parsed) != 1:
            raise EvaluationError("provider environment key value is not a single shell word")
        values[match.group(1)] = parsed[0]
    return {"xAI": values["XAI_API_KEY"], "OpenAI": values["OPENAI_API_KEY"]}


def load_arm_results(path: Path) -> list[dict[str, Any]]:
    """Load and identity-check resumable arm results."""
    if not path.exists():
        return []
    rows = read_jsonl(path)
    identities = [(str(row.get("case_id")), str(row.get("arm"))) for row in rows]
    if len(identities) != len(set(identities)):
        raise EvaluationError("arm result identities are duplicated")
    if any(arm not in ARMS for _case, arm in identities):
        raise EvaluationError("arm results contain an unknown arm")
    return rows


def _normalise_public_text(value: object) -> str:
    """Return compact case-folded text for explicit fixture constraints."""
    return " ".join(str(value or "").casefold().split())


def grade_challenge_result(
    case: Mapping[str, Any], *, final_status: str, final_reply: str | None
) -> dict[str, Any] | None:
    """Apply only explicit tracked-fixture constraints, never a model quality judgement."""
    expectation = case.get("expected_public_outcome")
    if not isinstance(expectation, dict):
        return None
    failures: list[str] = []
    kind = expectation.get("kind")
    outcome = "approved" if final_reply else "no_reply"
    if kind == "provider_pilot":
        allowed = expectation.get("allowed_statuses")
        if not isinstance(allowed, list) or outcome not in allowed:
            failures.append(f"public outcome {outcome} is outside the tracked allowed set")
        folded = _normalise_public_text(final_reply)
        for alternatives in expectation.get("required_term_groups") or []:
            if not isinstance(alternatives, list) or not any(
                _normalise_public_text(term) in folded for term in alternatives
            ):
                failures.append("required tracked concept group is absent")
        for phrase in expectation.get("forbidden_phrases") or []:
            if _normalise_public_text(phrase) in folded:
                failures.append("tracked forbidden phrase is present")
    elif kind == "fixed_status":
        allowed = expectation.get("allowed_statuses")
        if not isinstance(allowed, list) or outcome not in allowed:
            failures.append(f"public outcome {outcome} is outside the tracked allowed set")
    elif kind == "reject_known_bad_reply":
        bad = _normalise_public_text(expectation.get("known_bad_reply"))
        actual = _normalise_public_text(final_reply)
        if bad and actual and bad in actual:
            failures.append("known-bad tracked reply was reproduced")
    else:
        return None
    if final_status == "error":
        failures.append("arm execution failed")
    return {
        "status": "pass" if not failures else "fail",
        "failures": failures,
        "basis": "explicit_tracked_fixture_constraint",
    }


def _sum_reported(events: Sequence[Mapping[str, Any]], key: str) -> int:
    """Sum integer usage fields across attributed request events."""
    return sum(
        int(event.get(key) or 0)
        for event in events
        if type(event.get(key)) is int
    )


def arm_result_from_pipeline(
    *,
    case: Mapping[str, Any],
    arm: str,
    execution_order_index: int,
    result: Any,
    transport: ResearchTransport,
    wall_latency_seconds: float,
) -> dict[str, Any]:
    """Retain the complete requested pipeline and provider observability for one arm."""
    audit = list(result.audit)
    telemetry = stage_telemetry(result.audit)
    final_reply = str(result.reply) if result.reply is not None else None
    events = copy.deepcopy(transport.call_events)
    grade = grade_challenge_result(
        case,
        final_status=str(result.status),
        final_reply=final_reply,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case["case_id"],
        "source": case["source"],
        "canonical_case_input_sha256": case["canonical_case_input_sha256"],
        "arm": arm,
        "arm_execution_order": copy.deepcopy(case["arm_execution_order"]),
        "arm_execution_order_index": execution_order_index,
        "logical_xai_model": LOGICAL_XAI_MODEL,
        "logical_xai_reasoning_effort": LOGICAL_XAI_EFFORT,
        "effective_xai_model": ARMS[arm]["effective_xai_model"],
        "effective_xai_reasoning_effort": ARMS[arm]["effective_xai_effort"],
        "logical_openai_model": LOGICAL_OPENAI_MODEL,
        "logical_openai_reasoning_effort": LOGICAL_OPENAI_EFFORT,
        "effective_openai_model": LOGICAL_OPENAI_MODEL,
        "effective_openai_reasoning_effort": LOGICAL_OPENAI_EFFORT,
        "execution_status": "completed",
        "final_status": str(result.status),
        "final_reason": str(result.reason),
        "final_public_reply": final_reply,
        "audit": audit,
        "xai_gate_decision": telemetry.get("xai_gate_decision"),
        "focused_xai_review_outcomes": [
            {"stage": row.get("stage"), "outcome": row.get("outcome")}
            for row in audit
            if row.get("stage") == "group_hostility_outcome"
        ],
        "xai_claim_audit_outcomes": copy.deepcopy(
            telemetry.get("claim_audit_outcomes") or []
        ),
        "stage_telemetry": telemetry,
        "model_call_count": int(result.model_call_count),
        "request_count": len(events),
        "cache_hits": sum(event.get("cache_hit") is True for event in events),
        "schema_failures": [
            str(row.get("stage") or "unknown")
            for row in audit
            if row.get("schema_valid") is False
        ],
        "provider_errors": [
            str(event["provider_error"])
            for event in events
            if event.get("provider_error")
        ],
        "tokens": {
            "input": _sum_reported(events, "input_tokens"),
            "cached_input": _sum_reported(events, "cached_input_tokens"),
            "cache_write": _sum_reported(events, "cache_write_tokens"),
            "output": _sum_reported(events, "output_tokens"),
            "reasoning": _sum_reported(events, "reasoning_tokens"),
        },
        "incremental_provider_reported_cost_usd": sum(
            float(event.get("provider_reported_cost_usd") or 0)
            for event in events
            if event.get("cache_hit") is not True
        ),
        "incremental_estimated_cost_usd": sum(
            float(event.get("estimated_cost_usd") or 0)
            for event in events
            if event.get("cache_hit") is not True
        ),
        "attributed_provider_reported_cost_usd": sum(
            float(event.get("provider_reported_cost_usd") or 0) for event in events
        ),
        "attributed_estimated_cost_usd": sum(
            float(event.get("estimated_cost_usd") or 0) for event in events
        ),
        "request_provider_latency_seconds": sum(
            float(event.get("provider_latency_seconds") or 0) for event in events
        ),
        "arm_wall_latency_seconds": round(wall_latency_seconds, 6),
        "requests": events,
        "automatic_fixture_grade": grade,
        "completed_at": utc_now(),
    }


def arm_error_result(
    *,
    case: Mapping[str, Any],
    arm: str,
    execution_order_index: int,
    transport: ResearchTransport,
    error: BaseException,
    wall_latency_seconds: float,
    incomplete: bool,
) -> dict[str, Any]:
    """Retain a non-retried error or cost-bound partial arm without inventing an outcome."""
    events = copy.deepcopy(transport.call_events)
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case["case_id"],
        "source": case["source"],
        "canonical_case_input_sha256": case["canonical_case_input_sha256"],
        "arm": arm,
        "arm_execution_order": copy.deepcopy(case["arm_execution_order"]),
        "arm_execution_order_index": execution_order_index,
        "logical_xai_model": LOGICAL_XAI_MODEL,
        "logical_xai_reasoning_effort": LOGICAL_XAI_EFFORT,
        "effective_xai_model": ARMS[arm]["effective_xai_model"],
        "effective_xai_reasoning_effort": ARMS[arm]["effective_xai_effort"],
        "logical_openai_model": LOGICAL_OPENAI_MODEL,
        "logical_openai_reasoning_effort": LOGICAL_OPENAI_EFFORT,
        "effective_openai_model": LOGICAL_OPENAI_MODEL,
        "effective_openai_reasoning_effort": LOGICAL_OPENAI_EFFORT,
        "execution_status": "incomplete" if incomplete else "error",
        "final_status": "incomplete" if incomplete else "error",
        "final_reason": f"{type(error).__name__}: {error}",
        "final_public_reply": None,
        "audit": [],
        "xai_gate_decision": None,
        "focused_xai_review_outcomes": [],
        "xai_claim_audit_outcomes": [],
        "stage_telemetry": {},
        "model_call_count": len(events),
        "request_count": len(events),
        "cache_hits": sum(event.get("cache_hit") is True for event in events),
        "schema_failures": [],
        "provider_errors": [
            str(event["provider_error"])
            for event in events
            if event.get("provider_error")
        ],
        "tokens": {
            "input": _sum_reported(events, "input_tokens"),
            "cached_input": _sum_reported(events, "cached_input_tokens"),
            "cache_write": _sum_reported(events, "cache_write_tokens"),
            "output": _sum_reported(events, "output_tokens"),
            "reasoning": _sum_reported(events, "reasoning_tokens"),
        },
        "incremental_provider_reported_cost_usd": sum(
            float(event.get("provider_reported_cost_usd") or 0)
            for event in events
            if event.get("cache_hit") is not True
        ),
        "incremental_estimated_cost_usd": sum(
            float(event.get("estimated_cost_usd") or 0)
            for event in events
            if event.get("cache_hit") is not True
        ),
        "attributed_provider_reported_cost_usd": sum(
            float(event.get("provider_reported_cost_usd") or 0) for event in events
        ),
        "attributed_estimated_cost_usd": sum(
            float(event.get("estimated_cost_usd") or 0) for event in events
        ),
        "request_provider_latency_seconds": sum(
            float(event.get("provider_latency_seconds") or 0) for event in events
        ),
        "arm_wall_latency_seconds": round(wall_latency_seconds, 6),
        "requests": events,
        "automatic_fixture_grade": None,
        "completed_at": utc_now(),
    }


def assert_identical_case_inputs(
    case: Mapping[str, Any], results: Sequence[Mapping[str, Any]]
) -> None:
    """Assert that every retained arm for a case has the one canonical input hash."""
    observed = {
        str(row.get("canonical_case_input_sha256") or "")
        for row in results
        if row.get("case_id") == case.get("case_id")
    }
    if observed and observed != {case["canonical_case_input_sha256"]}:
        raise EvaluationError(f"case input changed across arms: {case['case_id']}")


def execute_experiment(
    *,
    project_dir: Path,
    output_dir: Path,
    manifest: Mapping[str, Any],
    cases: Sequence[dict[str, Any]],
    api_keys: Mapping[str, str],
    hard_limit_usd: float = 35.0,
    get: Callable[..., Any] = requests.get,
    post: Callable[..., Any] = requests.post,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[dict[str, Any]], RequestLedger, str | None, dict[str, dict[str, Any]]]:
    """Verify models, then execute resumable arms until completion or a safe blocker."""
    if not api_keys.get("xAI") or not api_keys.get("OpenAI"):
        missing = [
            name
            for name, provider in (("XAI_API_KEY", "xAI"), ("OPENAI_API_KEY", "OpenAI"))
            if not api_keys.get(provider)
        ]
        raise EvaluationError("live execution lacks " + ", ".join(missing))
    xai_metadata = fetch_xai_model_metadata(api_key=api_keys["xAI"], get=get)
    ledger = RequestLedger(
        private_path(output_dir, "request_ledger.json"),
        case_set_sha256=str(manifest["case_set_sha256"]),
        hard_limit_usd=hard_limit_usd,
    )
    config = production_config()
    corpus_path = project_dir / str(config["research_corpus_path"])
    repository = EvidenceRepository(
        corpus_path,
        factual_evidence_path=project_dir / "reply_factual_evidence.json",
    )
    results_path = private_path(output_dir, "arm_results.jsonl")
    results = load_arm_results(results_path)
    by_identity = {(row["case_id"], row["arm"]): row for row in results}
    blocker: str | None = None
    stop = False
    for case in cases:
        assert_identical_case_inputs(case, results)
        for order_index, arm in enumerate(case["arm_execution_order"]):
            identity = (case["case_id"], arm)
            if identity in by_identity:
                continue
            recomputed = sha256_value(
                canonical_case_input(
                    case,
                    config=config,
                    evidence_identity=manifest["evidence_corpus"],
                )
            )
            if recomputed != case["canonical_case_input_sha256"]:
                raise EvaluationError(f"case input changed before arm execution: {case['case_id']}")
            transport = ResearchTransport(
                arm=arm,
                case_id=str(case["case_id"]),
                api_keys=api_keys,
                xai_model_metadata=xai_metadata,
                ledger=ledger,
                response_dir=private_path(output_dir, "responses"),
                post=post,
                sleep=sleep,
            )
            arm_started = time.monotonic()
            try:
                pipeline_result = run_reply_pipeline(
                    context=copy.deepcopy(case["context"]),
                    config=copy.deepcopy(config),
                    repository=repository,
                    transport=transport,
                    maximum_reply_length=MAX_REPLY_LENGTH,
                    recent_replies=copy.deepcopy(case["recent_replies"]),
                    media_context=None,
                )
                arm_result = arm_result_from_pipeline(
                    case=case,
                    arm=arm,
                    execution_order_index=order_index,
                    result=pipeline_result,
                    transport=transport,
                    wall_latency_seconds=time.monotonic() - arm_started,
                )
            except (CostLimitReached, AmbiguousRequestError) as exc:
                blocker = f"{type(exc).__name__}: {exc}"
                arm_result = arm_error_result(
                    case=case,
                    arm=arm,
                    execution_order_index=order_index,
                    transport=transport,
                    error=exc,
                    wall_latency_seconds=time.monotonic() - arm_started,
                    incomplete=True,
                )
                stop = True
            except (DefiniteProviderError, TransientProviderError) as exc:
                blocker = f"{type(exc).__name__}: {exc}"
                arm_result = arm_error_result(
                    case=case,
                    arm=arm,
                    execution_order_index=order_index,
                    transport=transport,
                    error=exc,
                    wall_latency_seconds=time.monotonic() - arm_started,
                    incomplete=False,
                )
                stop = True
            except BaseException as exc:
                arm_result = arm_error_result(
                    case=case,
                    arm=arm,
                    execution_order_index=order_index,
                    transport=transport,
                    error=exc,
                    wall_latency_seconds=time.monotonic() - arm_started,
                    incomplete=False,
                )
            results.append(arm_result)
            by_identity[identity] = arm_result
            assert_identical_case_inputs(case, results)
            results.sort(
                key=lambda row: (
                    next(
                        int(item["selection_index"])
                        for item in cases
                        if item["case_id"] == row["case_id"]
                    ),
                    int(row["arm_execution_order_index"]),
                )
            )
            atomic_jsonl(results_path, results)
            if stop:
                break
        if stop:
            break
    return results, ledger, blocker, xai_metadata


def percentile(values: Sequence[float], proportion: float) -> float | None:
    """Return a nearest-rank percentile for a non-empty numeric sample."""
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = min(
        len(ordered) - 1,
        max(0, int(math.ceil(proportion * len(ordered))) - 1),
    )
    return ordered[index]


def _latency_summary(values: Sequence[float]) -> dict[str, float | None]:
    """Return compact p50, p95, and maximum latency statistics."""
    return {
        "p50_seconds": statistics.median(values) if values else None,
        "p95_seconds": percentile(values, 0.95),
        "max_seconds": max(values) if values else None,
    }


def _result_outcome(row: Mapping[str, Any] | None) -> str:
    """Return a compact operational public outcome for comparison."""
    if row is None:
        return "not_run"
    if row.get("execution_status") != "completed":
        return str(row.get("execution_status") or "error")
    return "reply" if row.get("final_public_reply") else "no_reply"


def build_comparison(
    *,
    manifest: Mapping[str, Any],
    cases: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    ledger: RequestLedger | None,
    blocker: str | None,
    human_scoring: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the operational comparison without assigning unlabelled quality verdicts."""
    result_map = {
        (str(row["case_id"]), str(row["arm"])): row for row in results
    }
    completed_arms = [
        row for row in results if row.get("execution_status") == "completed"
    ]
    complete_case_ids = [
        str(case["case_id"])
        for case in cases
        if all(
            result_map.get((str(case["case_id"]), arm), {}).get("execution_status")
            == "completed"
            for arm in ARMS
        )
    ]
    per_arm: dict[str, Any] = {}
    for arm, definition in ARMS.items():
        rows = [
            row
            for row in results
            if row.get("arm") == arm and row.get("execution_status") == "completed"
        ]
        wall_latencies = [float(row["arm_wall_latency_seconds"]) for row in rows]
        provider_latencies = [
            float(row["request_provider_latency_seconds"]) for row in rows
        ]
        grades = [
            row["automatic_fixture_grade"]
            for row in rows
            if isinstance(row.get("automatic_fixture_grade"), dict)
        ]
        xai_requests = [
            event
            for row in rows
            for event in row.get("requests") or []
            if isinstance(event, dict) and event.get("provider") == "xAI"
        ]
        openai_requests = [
            event
            for row in rows
            for event in row.get("requests") or []
            if isinstance(event, dict) and event.get("provider") == "OpenAI"
        ]
        per_arm[arm] = {
            **copy.deepcopy(definition),
            "logical_xai_model": LOGICAL_XAI_MODEL,
            "logical_xai_reasoning_effort": LOGICAL_XAI_EFFORT,
            "completed_arm_count": len(rows),
            "reply_count": sum(bool(row.get("final_public_reply")) for row in rows),
            "no_reply_count": sum(not bool(row.get("final_public_reply")) for row in rows),
            "xai_gate_counts": dict(
                sorted(Counter(str(row.get("xai_gate_decision") or "not_reached") for row in rows).items())
            ),
            "claim_audit_outcome_counts": dict(
                sorted(
                    Counter(
                        str(item.get("outcome") or "unknown")
                        for row in rows
                        for item in row.get("xai_claim_audit_outcomes") or []
                        if isinstance(item, dict)
                    ).items()
                )
            ),
            "model_call_count": sum(int(row.get("model_call_count") or 0) for row in rows),
            "request_count": sum(int(row.get("request_count") or 0) for row in rows),
            "cache_hit_count": sum(int(row.get("cache_hits") or 0) for row in rows),
            "xai_cache_hit_count": sum(event.get("cache_hit") is True for event in xai_requests),
            "openai_cache_hit_count": sum(
                event.get("cache_hit") is True for event in openai_requests
            ),
            "schema_failure_count": sum(len(row.get("schema_failures") or []) for row in rows),
            "provider_error_count": sum(len(row.get("provider_errors") or []) for row in rows),
            "tokens": {
                key: sum(int(row.get("tokens", {}).get(key) or 0) for row in rows)
                for key in ("input", "cached_input", "cache_write", "output", "reasoning")
            },
            "incremental_billed_cost_usd": sum(
                float(row.get("incremental_provider_reported_cost_usd") or 0)
                for row in rows
            ),
            "incremental_estimated_cost_usd": sum(
                float(row.get("incremental_estimated_cost_usd") or 0) for row in rows
            ),
            "cache_neutral_attributed_billed_cost_usd": sum(
                float(row.get("attributed_provider_reported_cost_usd") or 0)
                for row in rows
            ),
            "cache_neutral_attributed_estimated_cost_usd": sum(
                float(row.get("attributed_estimated_cost_usd") or 0) for row in rows
            ),
            "arm_wall_latency": _latency_summary(wall_latencies),
            "cache_neutral_provider_latency": _latency_summary(provider_latencies),
            "labelled_fixture_pass_count": sum(grade.get("status") == "pass" for grade in grades),
            "labelled_fixture_fail_count": sum(grade.get("status") == "fail" for grade in grades),
        }
    pairwise: dict[str, Any] = {}
    arms = list(ARMS)
    for left_index, left in enumerate(arms):
        for right in arms[left_index + 1 :]:
            common = [
                case_id
                for case_id in complete_case_ids
                if (case_id, left) in result_map and (case_id, right) in result_map
            ]
            pairwise[f"{left}_vs_{right}"] = {
                "paired_case_count": len(common),
                "same_public_reply_or_silence_count": sum(
                    (
                        _result_outcome(result_map[(case_id, left)]),
                        result_map[(case_id, left)].get("final_public_reply"),
                    )
                    == (
                        _result_outcome(result_map[(case_id, right)]),
                        result_map[(case_id, right)].get("final_public_reply"),
                    )
                    for case_id in common
                ),
                "different_reply_silence_decision_count": sum(
                    _result_outcome(result_map[(case_id, left)])
                    != _result_outcome(result_map[(case_id, right)])
                    for case_id in common
                ),
                "different_xai_gate_decision_count": sum(
                    result_map[(case_id, left)].get("xai_gate_decision")
                    != result_map[(case_id, right)].get("xai_gate_decision")
                    for case_id in common
                ),
                "different_claim_audit_trace_count": sum(
                    result_map[(case_id, left)].get("xai_claim_audit_outcomes")
                    != result_map[(case_id, right)].get("xai_claim_audit_outcomes")
                    for case_id in common
                ),
            }
    if ledger is not None:
        cost = ledger.cost_summary()
        operation_rows = [
            row for row in ledger.data["operations"] if isinstance(row, dict)
        ]
    else:
        cost = {
            "total_billed_cost_usd": 0.0,
            "total_estimated_cost_usd": 0.0,
            "ambiguous_exposure_usd": 0.0,
            "current_conservative_exposure_usd": 0.0,
        }
        operation_rows = []
    call_latencies = [
        float(row.get("provider_latency_seconds") or 0)
        for row in operation_rows
        if row.get("status") == "completed"
    ]
    comparison = {
        "schema_version": SCHEMA_VERSION,
        "run_version": RUN_VERSION,
        "generated_at": utc_now(),
        "source_git_sha": manifest["source_git_sha"],
        "case_set_sha256": manifest["case_set_sha256"],
        "selected_case_counts": copy.deepcopy(manifest["case_counts"]),
        "completed_case_count": len(complete_case_ids),
        "completed_arm_count": len(completed_arms),
        "result_row_count": len(results),
        "global_cache_hit_count": sum(int(row.get("cache_hits") or 0) for row in results),
        "global_cache_hit_counts_by_provider": {
            provider: sum(
                event.get("cache_hit") is True
                for row in results
                for event in row.get("requests") or []
                if isinstance(event, dict) and event.get("provider") == provider
            )
            for provider in ("xAI", "OpenAI")
        },
        "provider_request_count": len(operation_rows),
        "provider_completed_request_count": sum(
            row.get("status") == "completed" for row in operation_rows
        ),
        "cost": cost,
        "provider_request_latency": _latency_summary(call_latencies),
        "arms": per_arm,
        "paired_operational_differences": pairwise,
        "execution_blocker": blocker,
        "human_scoring": copy.deepcopy(human_scoring),
        "quality_conclusion": (
            "No winning model is declared before completed blind human scoring. "
            "Unlabelled prospective conversations have no automatic quality verdict."
            if human_scoring is None
            else "Blind human scores are reported descriptively; no deployment recommendation is made."
        ),
    }
    return comparison


def render_comparison_markdown(comparison: Mapping[str, Any]) -> str:
    """Render the private operational report without a pre-scoring winner."""
    counts = comparison["selected_case_counts"]
    cost = comparison["cost"]
    latency = comparison["provider_request_latency"]
    lines = [
        "# Grok 4.6 Tested-Pipeline Evaluation",
        "",
        f"Source Git SHA: `{comparison['source_git_sha']}`",
        f"Case-set SHA-256: `{comparison['case_set_sha256']}`",
        "",
        "## Corpus and execution",
        "",
        f"- Prospective cases: {counts['prospective']}",
        f"- Challenge cases: {counts['challenge']}",
        f"- Skipped rows/cases: {counts['skipped']}",
        f"- Total selected cases: {counts['total']}",
        f"- Complete four-arm cases: {comparison['completed_case_count']}",
        f"- Completed arms: {comparison['completed_arm_count']}",
        f"- Canonical provider requests: {comparison['provider_request_count']}",
        f"- Cache hits: {comparison['global_cache_hit_count']}",
        f"- Execution blocker: `{comparison['execution_blocker'] or 'none'}`",
        "",
        "## Cost",
        "",
        f"- Provider-reported billed cost: US${float(cost.get('total_billed_cost_usd') or 0):.6f}",
        f"- Estimated cost where no provider cost was reported: US${float(cost.get('total_estimated_cost_usd') or 0):.6f}",
        f"- Ambiguous maximum exposure: US${float(cost.get('ambiguous_exposure_usd') or 0):.6f}",
        f"- Conservative current exposure: US${float(cost.get('current_conservative_exposure_usd') or 0):.6f}",
        "",
        "The billed and estimated figures are deliberately separate; the OpenAI estimate is not provider-authoritative.",
        "",
        "## Provider latency",
        "",
        f"- Request p50: {latency['p50_seconds']!r} seconds",
        f"- Request p95: {latency['p95_seconds']!r} seconds",
        f"- Request maximum: {latency['max_seconds']!r} seconds",
        "",
        "## Arms",
        "",
        "| Arm | Effective xAI model | Effort | Completed | Replies | No replies | Cache hits | Labelled pass | Labelled fail | Incremental billed cost (USD) | Incremental estimated cost (USD) | Arm wall p50 (s) |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in ARMS:
        row = comparison["arms"][arm]
        lines.append(
            f"| {arm} | {row['effective_xai_model']} | {row['effective_xai_effort']} "
            f"| {row['completed_arm_count']} | {row['reply_count']} | {row['no_reply_count']} "
            f"| {row['cache_hit_count']} | {row['labelled_fixture_pass_count']} "
            f"| {row['labelled_fixture_fail_count']} "
            f"| {float(row['incremental_billed_cost_usd']):.6f} "
            f"| {float(row['incremental_estimated_cost_usd']):.6f} "
            f"| {row['arm_wall_latency']['p50_seconds']!r} |"
        )
    lines.extend(
        [
            "",
            "GPT-5.6 Sol remained the unchanged independent reviewer and final writer in every completed arm.",
            "",
            "## Interpretation boundary",
            "",
            str(comparison["quality_conclusion"]),
            "",
        ]
    )
    human = comparison.get("human_scoring")
    if isinstance(human, dict):
        lines.extend(["## Blind human scoring", ""])
        lines.append(f"Completed scored cases: {human.get('scored_case_count', 0)}")
        lines.append("")
        lines.append("| Arm | Acceptable | Minor | Unacceptable | Pairwise wins | Losses | Ties | False replies | False silences |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for arm in ARMS:
            row = human["arms"][arm]
            lines.append(
                f"| {arm} | {row['acceptable']} | {row['minor']} | {row['unacceptable']} "
                f"| {row['wins']} | {row['losses']} | {row['ties']} "
                f"| {row['false_replies']} | {row['false_silences']} |"
            )
        lines.append("")
    return "\n".join(lines)


def _visible_context(case: Mapping[str, Any]) -> dict[str, Any]:
    """Return only the context a blind reviewer should see."""
    context = case["context"]
    return {
        "quoted_post": copy.deepcopy(context.get("quoted_post")),
        "parent_thread": copy.deepcopy(context.get("parent_thread") or []),
        "incoming_contribution": str(context.get("incoming_contribution") or ""),
    }


def _blind_outcome(result: Mapping[str, Any] | None) -> str:
    """Render a public result without route, provider, model, cost, or latency data."""
    if result is None:
        return "[NOT RUN]"
    if result.get("execution_status") != "completed":
        return "[OUTCOME UNAVAILABLE]"
    reply = result.get("final_public_reply")
    if isinstance(reply, str) and reply:
        return reply
    return "[NO REPLY]"


def build_blind_rows(
    *,
    cases: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    arm_key: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Build blinded W/X/Y/Z rows without serialising the private mapping."""
    result_map = {
        (str(row["case_id"]), str(row["arm"])): row for row in results
    }
    label_to_arm = {label: arm for arm, label in arm_key.items()}
    return [
        {
            "case_id": case["case_id"],
            "visible_input_context": _visible_context(case),
            "outcomes": {
                label: _blind_outcome(
                    result_map.get((str(case["case_id"]), label_to_arm[label]))
                )
                for label in BLIND_LABELS
            },
        }
        for case in cases
    ]


def _markdown_visible_context(value: Mapping[str, Any]) -> list[str]:
    """Render exact visible context as indented private-review lines."""
    lines: list[str] = []
    quoted = value.get("quoted_post")
    if isinstance(quoted, dict):
        lines.append(f"- Quoted account post: {quoted.get('text', '')}")
    for parent in value.get("parent_thread") or []:
        if isinstance(parent, dict):
            lines.append(
                f"- Prior {parent.get('author_role', 'unknown')}: {parent.get('text', '')}"
            )
    lines.append(f"- Incoming user: {value.get('incoming_contribution', '')}")
    return lines


def render_blind_review(rows: Sequence[Mapping[str, Any]]) -> str:
    """Render the model- and route-blind human review document."""
    lines = [
        "# Blind Reply Review",
        "",
        "Rate each W/X/Y/Z public outcome independently. Silence is shown as `[NO REPLY]`.",
        "",
    ]
    for row in rows:
        lines.extend([f"## {row['case_id']}", "", "Visible input context:", ""])
        lines.extend(_markdown_visible_context(row["visible_input_context"]))
        lines.append("")
        for label in BLIND_LABELS:
            lines.extend(
                [
                    f"### {label} public outcome",
                    "",
                    str(row["outcomes"][label]),
                    "",
                ]
            )
    return "\n".join(lines)


def write_blind_scores_template(path: Path, cases: Sequence[Mapping[str, Any]]) -> None:
    """Create once a one-row-per-case manual scoring CSV template."""
    if path.exists():
        return
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "case_id",
            "W_rating",
            "X_rating",
            "Y_rating",
            "Z_rating",
            "preferred_outcome",
            "notes",
        ],
        lineterminator="\n",
    )
    writer.writeheader()
    for case in cases:
        writer.writerow(
            {
                "case_id": case["case_id"],
                "W_rating": "",
                "X_rating": "",
                "Y_rating": "",
                "Z_rating": "",
                "preferred_outcome": "",
                "notes": "",
            }
        )
    atomic_text(path, buffer.getvalue())


def generate_reports(
    *,
    output_dir: Path,
    manifest: Mapping[str, Any],
    cases: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    ledger: RequestLedger | None,
    blocker: str | None,
    human_scoring: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write operational and blinded outputs from retained deterministic data."""
    comparison = build_comparison(
        manifest=manifest,
        cases=cases,
        results=results,
        ledger=ledger,
        blocker=blocker,
        human_scoring=human_scoring,
    )
    atomic_json(private_path(output_dir, "comparison.json"), comparison)
    atomic_text(
        private_path(output_dir, "comparison.md"),
        render_comparison_markdown(comparison),
    )
    arm_key = create_or_load_arm_key(
        private_path(output_dir, "arm_key.private.json")
    )
    blind_rows = build_blind_rows(cases=cases, results=results, arm_key=arm_key)
    atomic_jsonl(private_path(output_dir, "blind_review.jsonl"), blind_rows)
    atomic_text(
        private_path(output_dir, "blind_review.md"), render_blind_review(blind_rows)
    )
    write_blind_scores_template(
        private_path(output_dir, "blind_scores.csv"), cases
    )
    return comparison


def load_completed_scores(
    *,
    scores_path: Path,
    cases: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    arm_key: Mapping[str, str],
) -> dict[str, Any]:
    """Decode a completed blind CSV into paired arm totals without another model."""
    allowed_ratings = {"acceptable": 2, "minor": 1, "unacceptable": 0}
    with scores_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        expected_fields = {
            "case_id",
            "W_rating",
            "X_rating",
            "Y_rating",
            "Z_rating",
            "preferred_outcome",
            "notes",
        }
        if reader.fieldnames is None or set(reader.fieldnames) != expected_fields:
            raise EvaluationError("blind score CSV columns changed")
        rows = list(reader)
    case_ids = [str(case["case_id"]) for case in cases]
    if [str(row.get("case_id") or "") for row in rows] != case_ids:
        raise EvaluationError("blind score CSV cases or ordering changed")
    label_to_arm = {label: arm for arm, label in arm_key.items()}
    result_map = {
        (str(row["case_id"]), str(row["arm"])): row for row in results
    }
    arm_rows: dict[str, dict[str, int]] = {
        arm: {
            "acceptable": 0,
            "minor": 0,
            "unacceptable": 0,
            "wins": 0,
            "losses": 0,
            "ties": 0,
            "preferred": 0,
            "false_replies": 0,
            "false_silences": 0,
        }
        for arm in ARMS
    }
    for case, score_row in zip(cases, rows):
        ratings: dict[str, str] = {}
        for label in BLIND_LABELS:
            value = str(score_row.get(f"{label}_rating") or "").strip().casefold()
            if value not in allowed_ratings:
                raise EvaluationError(
                    f"blind score for {case['case_id']} {label} must be acceptable, minor, or unacceptable"
                )
            arm = label_to_arm[label]
            ratings[arm] = value
            arm_rows[arm][value] += 1
        preferred = str(score_row.get("preferred_outcome") or "").strip().upper()
        if preferred not in {*BLIND_LABELS, "TIE"}:
            raise EvaluationError(
                f"preferred outcome for {case['case_id']} must be W, X, Y, Z, or tie"
            )
        if preferred in BLIND_LABELS:
            arm_rows[label_to_arm[preferred]]["preferred"] += 1
        arm_names = list(ARMS)
        for left_index, left in enumerate(arm_names):
            for right in arm_names[left_index + 1 :]:
                left_value = allowed_ratings[ratings[left]]
                right_value = allowed_ratings[ratings[right]]
                if left_value > right_value:
                    arm_rows[left]["wins"] += 1
                    arm_rows[right]["losses"] += 1
                elif right_value > left_value:
                    arm_rows[right]["wins"] += 1
                    arm_rows[left]["losses"] += 1
                else:
                    arm_rows[left]["ties"] += 1
                    arm_rows[right]["ties"] += 1
        expectation = case.get("expected_public_outcome")
        if (
            not isinstance(expectation, dict)
            or expectation.get("kind") not in {"provider_pilot", "fixed_status"}
        ):
            continue
        allowed = expectation.get("allowed_statuses")
        if not isinstance(allowed, list) or len(set(allowed)) != 1:
            continue
        expected = str(allowed[0])
        for arm in ARMS:
            result = result_map.get((str(case["case_id"]), arm))
            if result is None or result.get("execution_status") != "completed":
                continue
            replied = bool(result.get("final_public_reply"))
            if expected == "no_reply" and replied:
                arm_rows[arm]["false_replies"] += 1
            elif expected == "approved" and not replied:
                arm_rows[arm]["false_silences"] += 1
    return {
        "completed": True,
        "scored_case_count": len(cases),
        "rating_scale": ["acceptable", "minor", "unacceptable"],
        "arms": arm_rows,
        "cost_and_latency_source": "comparison arm aggregates",
        "generated_at": utc_now(),
    }


def report_completed_scores(output_dir: Path) -> dict[str, Any]:
    """Consume completed blind scores and refresh only the operational comparison."""
    manifest = read_json(private_path(output_dir, "manifest.json"))
    if not isinstance(manifest, dict):
        raise EvaluationError("experiment manifest is invalid")
    cases = read_jsonl(private_path(output_dir, "cases.jsonl"))
    results = load_arm_results(private_path(output_dir, "arm_results.jsonl"))
    arm_key = create_or_load_arm_key(private_path(output_dir, "arm_key.private.json"))
    scoring = load_completed_scores(
        scores_path=private_path(output_dir, "blind_scores.csv"),
        cases=cases,
        results=results,
        arm_key=arm_key,
    )
    ledger_path = private_path(output_dir, "request_ledger.json")
    ledger = (
        RequestLedger(
            ledger_path,
            case_set_sha256=str(manifest["case_set_sha256"]),
            hard_limit_usd=float(manifest.get("global_cost_ceiling_usd") or 35.0),
        )
        if ledger_path.exists()
        else None
    )
    blocker = (
        manifest.get("execution", {}).get("blocker")
        if isinstance(manifest.get("execution"), dict)
        else None
    )
    return generate_reports(
        output_dir=output_dir,
        manifest=manifest,
        cases=cases,
        results=results,
        ledger=ledger,
        blocker=str(blocker) if blocker else None,
        human_scoring=scoring,
    )


def _build_parser() -> argparse.ArgumentParser:
    """Return the narrow preparation, live-execution, and score-report parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--review-pack", type=Path)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--execute-live-models", action="store_true")
    parser.add_argument("--report-scores", action="store_true")
    parser.add_argument("--cost-ceiling-usd", type=float, default=35.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Prepare only by default; transmit provider requests only with the live flag."""
    os.umask(0o077)
    args = _build_parser().parse_args(argv)
    project_dir = args.project_dir.expanduser().resolve()
    if project_dir != PROJECT_ROOT.resolve():
        raise EvaluationError("project directory must be this isolated evaluation worktree")
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
                    "comparison": str(private_path(output_dir, "comparison.md")),
                },
                sort_keys=True,
            )
        )
        return 0
    if args.review_pack is None:
        raise EvaluationError("--review-pack is required for corpus preparation")
    if not 0 < args.cost_ceiling_usd <= 35.0:
        raise EvaluationError("--cost-ceiling-usd must be in (0, 35.00]")
    manifest, cases, _skips = prepare_experiment(
        project_dir=project_dir,
        output_dir=output_dir,
        review_pack=args.review_pack,
    )
    results = load_arm_results(private_path(output_dir, "arm_results.jsonl"))
    if not args.execute_live_models:
        manifest = update_manifest_execution(
            output_dir,
            mode="prepared_only",
            blocker=None,
            ledger=None,
        )
        comparison = generate_reports(
            output_dir=output_dir,
            manifest=manifest,
            cases=cases,
            results=results,
            ledger=None,
            blocker=None,
        )
        print(
            json.dumps(
                {
                    "mode": "prepared_only",
                    "cases": comparison["selected_case_counts"],
                    "case_set_sha256": comparison["case_set_sha256"],
                    "network_requests": 0,
                },
                sort_keys=True,
            )
        )
        return 0
    keys = load_api_keys(args.env_file)
    ledger: RequestLedger | None = None
    xai_metadata: dict[str, dict[str, Any]] | None = None
    blocker: str | None = None
    try:
        results, ledger, blocker, xai_metadata = execute_experiment(
            project_dir=project_dir,
            output_dir=output_dir,
            manifest=manifest,
            cases=cases,
            api_keys=keys,
            hard_limit_usd=args.cost_ceiling_usd,
        )
    except (EvaluationError, OSError, ValueError) as exc:
        blocker = f"{type(exc).__name__}: {exc}"
        ledger_path = private_path(output_dir, "request_ledger.json")
        if ledger_path.exists():
            ledger = RequestLedger(
                ledger_path,
                case_set_sha256=str(manifest["case_set_sha256"]),
                hard_limit_usd=args.cost_ceiling_usd,
            )
        results = load_arm_results(private_path(output_dir, "arm_results.jsonl"))
    manifest = update_manifest_execution(
        output_dir,
        mode="live_models",
        blocker=blocker,
        ledger=ledger,
        xai_metadata=xai_metadata,
    )
    comparison = generate_reports(
        output_dir=output_dir,
        manifest=manifest,
        cases=cases,
        results=results,
        ledger=ledger,
        blocker=blocker,
    )
    print(
        json.dumps(
            {
                "mode": "live_models",
                "completed_cases": comparison["completed_case_count"],
                "completed_arms": comparison["completed_arm_count"],
                "billed_cost_usd": comparison["cost"]["total_billed_cost_usd"],
                "estimated_cost_usd": comparison["cost"]["total_estimated_cost_usd"],
                "blocker": blocker,
                "comparison": str(private_path(output_dir, "comparison.md")),
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
