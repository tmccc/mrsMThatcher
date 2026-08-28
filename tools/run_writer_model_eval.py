#!/usr/bin/env python3
"""Run the private tested-pipeline writer-model comparison.

The experiment replays every routing and independent-review decision from the
completed arm A of the earlier Grok evaluation.  It changes only calls which
write or rewrite public reply text, while candidate-dependent factual audits
continue to use the production Grok 4.3 low identity.  Preparation is entirely
offline; provider traffic requires ``--execute-live-models``.
"""

from __future__ import annotations

import argparse
import copy
import csv
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools import run_grok_46_tested_pipeline_eval as previous_tool


RUN_VERSION = "writer-model-eval-v1"
SCHEMA_VERSION = 1
USD_TICKS_PER_DOLLAR = previous_tool.USD_TICKS_PER_DOLLAR
MAX_REPLY_LENGTH = previous_tool.MAX_REPLY_LENGTH
GLOBAL_COST_CEILING_USD = 15.0

PRODUCTION_CHECKOUT = Path("/disks/disk1/etc/mrsMThatcher")
PREVIOUS_OUTPUT_ROOT = Path(
    "/disks/disk1/research/grok-46-tested-pipeline-eval-20260828"
)
DEFAULT_OUTPUT_ROOT = Path(
    "/disks/disk1/research/writer-model-eval-20260828"
)
DEFAULT_ENV_FILE = PRODUCTION_CHECKOUT / "mrsMThatcher.env"

EXPECTED_PREVIOUS_SOURCE_SHA = "3ed89715fe8b160c3423e08ffb6533303408d0b5"
EXPECTED_PREVIOUS_CASE_SET_SHA256 = (
    "d9810a2094815efaa074b94446e1b02a50683003773a04178708a8ffd3408c85"
)

XAI_HOST = previous_tool.XAI_HOST
OPENAI_HOST = previous_tool.OPENAI_HOST
XAI_BASE_URL = previous_tool.XAI_BASE_URL
OPENAI_BASE_URL = previous_tool.OPENAI_BASE_URL
NETWORK_ALLOWLIST = frozenset({XAI_HOST, OPENAI_HOST})

LOGICAL_XAI_MODEL = "grok-4.3"
LOGICAL_XAI_EFFORT = "low"
LOGICAL_OPENAI_MODEL = "gpt-5.6-sol"
LOGICAL_OPENAI_EFFORT = "medium"

ARMS: dict[str, dict[str, Any]] = {
    "A": {
        "effective_provider": "OpenAI",
        "effective_model": "gpt-5.6-sol",
        "effective_reasoning_effort": "medium",
        "cached_baseline": True,
    },
    "B": {
        "effective_provider": "OpenAI",
        "effective_model": "gpt-5.6-terra",
        "effective_reasoning_effort": "medium",
        "cached_baseline": False,
    },
    "C": {
        "effective_provider": "OpenAI",
        "effective_model": "gpt-5.6-luna",
        "effective_reasoning_effort": "medium",
        "cached_baseline": False,
    },
    "D": {
        "effective_provider": "xAI",
        "effective_model": "grok-4.6",
        "effective_reasoning_effort": "low",
        "cached_baseline": False,
    },
    "E": {
        "effective_provider": "xAI",
        "effective_model": "grok-4.3",
        "effective_reasoning_effort": "low",
        "cached_baseline": False,
    },
}
NON_BASELINE_ARMS = tuple(arm for arm in ARMS if arm != "A")
BLIND_LABELS = ("V", "W", "X", "Y", "Z")

WRITER_STAGES = frozenset(
    {
        "writer_v3_initial",
        "bounded_claim_cleanup",
        "exact_duplicate_repair",
        "direct_answer_repair",
    }
)
CLAIM_AUDIT_STAGES = frozenset(
    {
        "narrow_claim_audit",
        "cleanup_claim_audit",
        "diversity_claim_audit",
        "direct_answer_repair_claim_audit",
    }
)

# USD rates per million tokens, converted to the durable ten-billionth-dollar
# unit used by the earlier harness.
OPENAI_PRICING_USD_PER_MILLION: dict[str, dict[str, float]] = {
    "gpt-5.6-sol": {"input": 4.0, "cached_input": 0.4, "output": 20.0},
    "gpt-5.6-terra": {"input": 2.0, "cached_input": 0.2, "output": 12.0},
    "gpt-5.6-luna": {"input": 0.2, "cached_input": 0.02, "output": 1.2},
}
OPENAI_PRICING_TICKS_PER_TOKEN: dict[str, dict[str, int]] = {
    model: {
        name: int(round(rate * USD_TICKS_PER_DOLLAR / 1_000_000))
        for name, rate in rates.items()
    }
    for model, rates in OPENAI_PRICING_USD_PER_MILLION.items()
}

EvaluationError = previous_tool.EvaluationError
CostLimitReached = previous_tool.CostLimitReached
AmbiguousRequestError = previous_tool.AmbiguousRequestError
ProviderError = previous_tool.ProviderError
DefiniteProviderError = previous_tool.DefiniteProviderError
TransientProviderError = previous_tool.TransientProviderError
ModelAvailabilityError = previous_tool.ModelAvailabilityError

atomic_bytes = previous_tool.atomic_bytes
atomic_json = previous_tool.atomic_json
atomic_jsonl = previous_tool.atomic_jsonl
atomic_text = previous_tool.atomic_text
canonical_json_bytes = previous_tool.canonical_json_bytes
private_path = previous_tool.private_path
read_json = previous_tool.read_json
read_jsonl = previous_tool.read_jsonl
sha256_bytes = previous_tool.sha256_bytes
sha256_file = previous_tool.sha256_file
sha256_value = previous_tool.sha256_value
strict_json_loads = previous_tool.strict_json_loads
utc_now = previous_tool.utc_now


def validate_provider_url(url: str, *, expected_host: str | None = None) -> str:
    """Allow only the two exact HTTPS provider API destinations used here."""
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
        OPENAI_HOST: {"/v1/models", "/v1/chat/completions"},
    }
    if parsed.path not in permitted_paths[parsed.hostname]:
        raise EvaluationError("provider URL path is not permitted")
    return url


def ensure_private_output_dir(path: Path, *, project_dir: Path) -> Path:
    """Create only the fixed private research output tree, never production."""
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


def _require_private_member(path: Path, *, directory: bool) -> None:
    """Require a non-symlink private file or directory with the exact mode."""
    info = path.lstat()
    expected_mode = 0o700 if directory else 0o600
    correct_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not correct_type or stat.S_ISLNK(info.st_mode):
        raise EvaluationError(f"private experiment member has an unsafe type: {path}")
    if stat.S_IMODE(info.st_mode) != expected_mode:
        raise EvaluationError(
            f"private experiment member must use mode {expected_mode:o}: {path}"
        )


def production_config() -> dict[str, Any]:
    """Return and verify the unchanged production tested-pipeline identities."""
    config = previous_tool.production_config()
    expected = {
        "xai_model": LOGICAL_XAI_MODEL,
        "xai_reasoning_effort": LOGICAL_XAI_EFFORT,
        "openai_model": LOGICAL_OPENAI_MODEL,
        "openai_reasoning_effort": LOGICAL_OPENAI_EFFORT,
    }
    if any(config.get(key) != value for key, value in expected.items()):
        raise EvaluationError("production provider identity changed")
    return config


def stable_nonbaseline_arm_order(frozen_case_hash: str, case_id: str) -> list[str]:
    """Shuffle B-E independently and reproducibly for one frozen case."""
    seed = int(
        sha256_bytes((frozen_case_hash + "\0" + str(case_id)).encode("utf-8")),
        16,
    )
    arms = list(NON_BASELINE_ARMS)
    random.Random(seed).shuffle(arms)
    return arms


def create_or_load_arm_key(path: Path) -> dict[str, str]:
    """Create once or validate the private A-E to V-Z mapping."""
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


@dataclass(frozen=True)
class PreviousExperiment:
    """Validated immutable view of the earlier experiment and response cache."""

    root: Path
    manifest: dict[str, Any]
    cases: tuple[dict[str, Any], ...]
    arm_a_results: dict[str, dict[str, Any]]
    ledger_operations: dict[str, dict[str, Any]]
    response_dir: Path
    file_hashes: dict[str, str]


def validate_previous_experiment(
    root: Path = PREVIOUS_OUTPUT_ROOT,
    *,
    enforce_fixed_root: bool = True,
) -> PreviousExperiment:
    """Validate every required previous artifact and completed response cache."""
    resolved = root.expanduser().resolve()
    if enforce_fixed_root and resolved != PREVIOUS_OUTPUT_ROOT.resolve():
        raise EvaluationError("the previous experiment path is not the frozen private output")
    _require_private_member(resolved, directory=True)
    names = ("manifest.json", "cases.jsonl", "arm_results.jsonl", "request_ledger.json")
    for name in names:
        _require_private_member(resolved / name, directory=False)
    response_dir = resolved / "responses"
    _require_private_member(response_dir, directory=True)

    manifest = read_json(resolved / "manifest.json")
    if not isinstance(manifest, dict):
        raise EvaluationError("previous manifest is not an object")
    if manifest.get("source_git_sha") != EXPECTED_PREVIOUS_SOURCE_SHA:
        raise EvaluationError("previous source Git SHA differs from the frozen identity")
    if manifest.get("case_set_sha256") != EXPECTED_PREVIOUS_CASE_SET_SHA256:
        raise EvaluationError("previous manifest case-set SHA-256 differs")
    if manifest.get("logical_production_config") != production_config():
        raise EvaluationError("previous frozen production configuration differs")

    cases = read_jsonl(resolved / "cases.jsonl")
    if sha256_value(cases) != EXPECTED_PREVIOUS_CASE_SET_SHA256:
        raise EvaluationError("previous cases.jsonl does not reproduce the frozen case hash")
    case_ids = [str(case.get("case_id") or "") for case in cases]
    if not all(case_ids) or len(case_ids) != len(set(case_ids)):
        raise EvaluationError("previous cases contain missing or duplicate identities")

    all_results = read_jsonl(resolved / "arm_results.jsonl")
    arm_a_rows = [row for row in all_results if row.get("arm") == "A"]
    if len(arm_a_rows) != len(cases):
        raise EvaluationError("previous experiment lacks one completed arm-A row per case")
    arm_a_results: dict[str, dict[str, Any]] = {}
    for row in arm_a_rows:
        case_id = str(row.get("case_id") or "")
        if (
            not case_id
            or case_id in arm_a_results
            or row.get("execution_status") != "completed"
        ):
            raise EvaluationError("previous arm A is not uniquely and fully completed")
        arm_a_results[case_id] = row
    if set(arm_a_results) != set(case_ids):
        raise EvaluationError("previous arm-A result identities do not match cases")

    ledger = read_json(resolved / "request_ledger.json")
    if (
        not isinstance(ledger, dict)
        or ledger.get("case_set_sha256") != EXPECTED_PREVIOUS_CASE_SET_SHA256
        or ledger.get("blocked") is not False
        or not isinstance(ledger.get("operations"), list)
    ):
        raise EvaluationError("previous request ledger identity or completion state differs")
    operations: dict[str, dict[str, Any]] = {}
    for row in ledger["operations"]:
        request_hash = str(row.get("request_hash") or "") if isinstance(row, dict) else ""
        if (
            not re.fullmatch(r"[0-9a-f]{64}", request_hash)
            or request_hash in operations
            or row.get("status") != "completed"
        ):
            raise EvaluationError("previous request ledger is not uniquely completed")
        operations[request_hash] = row

    response_files = {
        path.stem: path for path in response_dir.iterdir() if path.name.endswith(".json")
    }
    if set(response_files) != set(operations):
        raise EvaluationError("previous response cache and request ledger identities differ")
    for request_hash, path in response_files.items():
        _require_private_member(path, directory=False)
        cached = read_json(path)
        if (
            not isinstance(cached, dict)
            or cached.get("request_hash") != request_hash
            or not isinstance(cached.get("raw"), dict)
            or sha256_value(cached["raw"]) != operations[request_hash].get("response_hash")
        ):
            raise EvaluationError("previous cached provider response failed identity validation")

    for case_id, row in arm_a_results.items():
        requests_for_case = row.get("requests")
        if not isinstance(requests_for_case, list):
            raise EvaluationError(f"previous arm-A requests are invalid for {case_id}")
        for event in requests_for_case:
            request_hash = str(event.get("request_hash") or "") if isinstance(event, dict) else ""
            if request_hash not in operations:
                raise EvaluationError(f"previous arm-A response is unavailable for {case_id}")

    file_hashes = {
        name: sha256_file(resolved / name) for name in names
    }
    file_hashes["responses_identity_sha256"] = sha256_value(
        [
            {
                "request_hash": request_hash,
                "response_hash": operations[request_hash]["response_hash"],
            }
            for request_hash in sorted(operations)
        ]
    )
    return PreviousExperiment(
        root=resolved,
        manifest=manifest,
        cases=tuple(cases),
        arm_a_results=arm_a_results,
        ledger_operations=operations,
        response_dir=response_dir,
        file_hashes=file_hashes,
    )


def select_writer_cases(previous: PreviousExperiment) -> list[dict[str, Any]]:
    """Select every frozen case whose completed arm A reached the initial writer."""
    selected: list[dict[str, Any]] = []
    for case in sorted(previous.cases, key=lambda row: int(row["selection_index"])):
        result = previous.arm_a_results[str(case["case_id"])]
        reached_writer = any(
            isinstance(event, dict) and event.get("stage") == "writer_v3_initial"
            for event in result.get("requests") or []
        )
        if not reached_writer:
            continue
        row = copy.deepcopy(case)
        row.pop("arm_execution_order", None)
        row["writer_arm_execution_order"] = ["A", *stable_nonbaseline_arm_order(
            EXPECTED_PREVIOUS_CASE_SET_SHA256, str(case["case_id"])
        )]
        row["previous_arm_a_result_identity_sha256"] = sha256_value(
            {
                "final_status": result.get("final_status"),
                "final_reason": result.get("final_reason"),
                "final_public_reply": result.get("final_public_reply"),
                "request_hashes": [
                    event.get("request_hash")
                    for event in result.get("requests") or []
                    if isinstance(event, dict)
                ],
            }
        )
        selected.append(row)
    if not selected:
        raise EvaluationError("no completed previous arm-A case reached writer_v3_initial")
    return selected


def canonical_writer_input(
    *,
    stage: str,
    system_prompt: str,
    payload: Mapping[str, Any],
    response_schema: Mapping[str, Any],
    maximum_output_tokens: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Return a writer input identity before provider, model, and effort are added."""
    return {
        "stage": stage,
        "system_prompt": system_prompt,
        "user_payload": copy.deepcopy(dict(payload)),
        "response_schema": copy.deepcopy(dict(response_schema)),
        "maximum_output_tokens": maximum_output_tokens,
        "timeout_seconds": timeout_seconds,
    }


def canonical_writer_input_sha256(**kwargs: Any) -> str:
    """Hash one provider-neutral writer input."""
    return sha256_value(canonical_writer_input(**kwargs))


class WriterInputRegistry:
    """Assert that every arm receives the same initial writer input per case."""

    def __init__(self, expected: Mapping[str, str] | None = None) -> None:
        """Initialise optional immutable expected hashes for safe resumption."""
        self.initial_hashes = dict(expected or {})
        self.observed: list[dict[str, str]] = []

    def record(
        self,
        *,
        case_id: str,
        arm: str,
        stage: str,
        system_prompt: str,
        payload: Mapping[str, Any],
        response_schema: Mapping[str, Any],
        maximum_output_tokens: int,
        timeout_seconds: int,
    ) -> str:
        """Record a writer call and enforce the initial payload invariant."""
        value = canonical_writer_input_sha256(
            stage=stage,
            system_prompt=system_prompt,
            payload=payload,
            response_schema=response_schema,
            maximum_output_tokens=maximum_output_tokens,
            timeout_seconds=timeout_seconds,
        )
        if stage == "writer_v3_initial":
            prior = self.initial_hashes.setdefault(str(case_id), value)
            if prior != value:
                raise EvaluationError(
                    f"canonical initial writer input changed across arms: {case_id}"
                )
        self.observed.append(
            {
                "case_id": str(case_id),
                "arm": arm,
                "stage": stage,
                "canonical_writer_input_sha256": value,
            }
        )
        return value

    def initial_hash(self, case_id: str) -> str:
        """Return the already observed initial-writer hash for one case."""
        try:
            return self.initial_hashes[str(case_id)]
        except KeyError as exc:
            raise EvaluationError(f"initial writer was not reached for {case_id}") from exc


def _validate_logical_identity(
    provider: str, model: str, reasoning_effort: str
) -> None:
    """Reject any tested-pipeline call whose logical production identity moved."""
    expected = {
        "xAI": (LOGICAL_XAI_MODEL, LOGICAL_XAI_EFFORT),
        "OpenAI": (LOGICAL_OPENAI_MODEL, LOGICAL_OPENAI_EFFORT),
    }
    if provider not in expected or (model, reasoning_effort) != expected[provider]:
        raise EvaluationError("tested-pipeline logical provider identity changed")


def effective_call_identity(
    *,
    arm: str,
    stage: str,
    logical_provider: str,
    logical_model: str,
    logical_reasoning_effort: str,
) -> dict[str, Any]:
    """Return the frozen or writer-substituted identity for one pipeline call."""
    if arm not in ARMS:
        raise EvaluationError(f"unknown writer arm: {arm}")
    _validate_logical_identity(
        logical_provider, logical_model, logical_reasoning_effort
    )
    if stage in WRITER_STAGES:
        if logical_provider != "OpenAI":
            raise EvaluationError("writer-family call no longer uses logical OpenAI")
        definition = ARMS[arm]
        return {
            "effective_provider": definition["effective_provider"],
            "effective_model": definition["effective_model"],
            "effective_reasoning_effort": definition[
                "effective_reasoning_effort"
            ],
            "frozen_from_previous_arm_a": arm == "A",
        }
    if stage in CLAIM_AUDIT_STAGES:
        if logical_provider != "xAI":
            raise EvaluationError("claim-audit call no longer uses logical xAI")
        return {
            "effective_provider": "xAI",
            "effective_model": LOGICAL_XAI_MODEL,
            "effective_reasoning_effort": LOGICAL_XAI_EFFORT,
            "frozen_from_previous_arm_a": arm == "A",
        }
    return {
        "effective_provider": logical_provider,
        "effective_model": logical_model,
        "effective_reasoning_effort": logical_reasoning_effort,
        "frozen_from_previous_arm_a": True,
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
    """Reuse the tested-pipeline provider-specific request formatter."""
    return previous_tool.provider_request_body(
        provider=provider,
        stage=stage,
        model=model,
        reasoning_effort=reasoning_effort,
        system_prompt=system_prompt,
        payload=payload,
        response_schema=response_schema,
        max_output_tokens=max_output_tokens,
    )


def candidate_request_identity(
    *,
    provider: str,
    stage: str,
    model: str,
    reasoning_effort: str,
    system_prompt: str,
    payload: Mapping[str, Any],
    response_schema: Mapping[str, Any],
    maximum_output_tokens: int,
    timeout_seconds: int,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Build a provider request and its complete canonical cache identity."""
    body = provider_request_body(
        provider=provider,
        stage=stage,
        model=model,
        reasoning_effort=reasoning_effort,
        system_prompt=system_prompt,
        payload=payload,
        response_schema=response_schema,
        max_output_tokens=maximum_output_tokens,
    )
    wrapped_schema = body["response_format"]["json_schema"]
    identity = previous_tool.canonical_request_identity(
        provider=provider,
        model=model,
        reasoning_effort=reasoning_effort,
        system_prompt=system_prompt,
        user_payload=payload,
        response_schema=wrapped_schema,
        maximum_output_tokens=maximum_output_tokens,
        timeout_seconds=timeout_seconds,
    )
    return body, identity, sha256_value(identity)


def estimate_openai_cost_ticks(
    raw: Mapping[str, Any], *, model: str, fallback_ticks: int
) -> tuple[int, str]:
    """Estimate an OpenAI call from usage and the specified standard rates."""
    rates = OPENAI_PRICING_TICKS_PER_TOKEN.get(model)
    if rates is None:
        raise EvaluationError(f"no approved OpenAI pricing rates for {model}")
    if not isinstance(raw.get("usage"), dict):
        return fallback_ticks, "reserved_upper_bound_no_usage"
    tokens = previous_tool._usage_from_response(raw)
    input_tokens = tokens["input_tokens"]
    cached_tokens = min(input_tokens, tokens["cached_input_tokens"])
    ordinary_tokens = max(0, input_tokens - cached_tokens)
    estimate = (
        ordinary_tokens * rates["input"]
        + cached_tokens * rates["cached_input"]
        + tokens["output_tokens"] * rates["output"]
    )
    return estimate, "reported_usage_times_frozen_standard_rates"


def maximum_request_cost_ticks(
    *,
    provider: str,
    request_body: Mapping[str, Any],
    max_output_tokens: int,
    effective_model: str,
    xai_model_metadata: Mapping[str, Mapping[str, Any]],
) -> int:
    """Reserve a conservative upper bound for a candidate request."""
    if provider == "xAI":
        return previous_tool.maximum_request_cost_ticks(
            provider=provider,
            request_body=request_body,
            max_output_tokens=max_output_tokens,
            effective_model=effective_model,
            xai_model_metadata=xai_model_metadata,
        )
    rates = OPENAI_PRICING_TICKS_PER_TOKEN.get(effective_model)
    if rates is None:
        raise EvaluationError(f"no approved OpenAI pricing rates for {effective_model}")
    input_upper_bound = max(1, len(canonical_json_bytes(request_body)))
    return (
        input_upper_bound * rates["input"]
        + max_output_tokens * rates["output"]
    )


class WriterRequestLedger(previous_tool.RequestLedger):
    """Writer-experiment ledger with a fixed US$15 ceiling and model rates."""

    def __init__(
        self,
        path: Path,
        *,
        case_set_sha256: str,
        hard_limit_usd: float = GLOBAL_COST_CEILING_USD,
    ) -> None:
        """Open or create a writer-experiment request ledger."""
        self.path = path
        self.limit_ticks = int(round(hard_limit_usd * USD_TICKS_PER_DOLLAR))
        if (
            self.limit_ticks <= 0
            or self.limit_ticks > int(GLOBAL_COST_CEILING_USD * USD_TICKS_PER_DOLLAR)
        ):
            raise EvaluationError("global provider-cost ceiling must be in (0, 15.00]")
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

    def bind_provider_identities(
        self,
        row: dict[str, Any],
        *,
        logical_provider: str,
        effective_provider: str,
    ) -> None:
        """Persist separate logical and effective provider identities."""
        expected = {
            "logical_provider": logical_provider,
            "effective_provider": effective_provider,
        }
        observed = {key: row.get(key) for key in expected}
        if all(value is None for value in observed.values()):
            row.update(expected)
            self._save()
        elif observed != expected:
            raise EvaluationError("canonical request provider identities changed on resume")

    def complete(
        self,
        row: dict[str, Any],
        *,
        raw: Mapping[str, Any],
        latency_seconds: float,
    ) -> None:
        """Record reported xAI cost or a model-specific OpenAI estimate."""
        usage = raw.get("usage")
        provider_ticks = (
            usage.get("cost_in_usd_ticks") if isinstance(usage, dict) else None
        )
        if type(provider_ticks) is not int or provider_ticks < 0:
            provider_ticks = None
        if row["provider"] == "xAI" and provider_ticks is None:
            self.ambiguous(
                row,
                EvaluationError(
                    "completed xAI response lacks provider-reported cost_in_usd_ticks"
                ),
            )
            raise EvaluationError(
                "completed xAI response lacks provider-reported cost_in_usd_ticks"
            )
        if provider_ticks is None:
            estimated_ticks, estimate_basis = estimate_openai_cost_ticks(
                raw,
                model=str(row["effective_model"]),
                fallback_ticks=int(row["maximum_possible_cost_ticks"]),
            )
        else:
            estimated_ticks, estimate_basis = None, None
        tokens = previous_tool._usage_from_response(raw)
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


class PriorResponseCache:
    """Read-only complete canonical cache over the previous experiment."""

    def __init__(self, previous: PreviousExperiment) -> None:
        """Bind the validated previous output without creating files."""
        self.previous = previous

    def _call_identity(self, **kwargs: Any) -> tuple[dict[str, Any], dict[str, Any], str]:
        """Return the previous production provider identity for a call."""
        return candidate_request_identity(**kwargs)

    def contains_call(self, **kwargs: Any) -> bool:
        """Return whether the complete canonical call is cached previously."""
        _body, _identity, request_hash = self._call_identity(**kwargs)
        return request_hash in self.previous.ledger_operations

    def replay(
        self,
        *,
        case_id: str,
        logical_provider: str,
        stage: str,
        logical_model: str,
        logical_reasoning_effort: str,
        system_prompt: str,
        payload: Mapping[str, Any],
        response_schema: Mapping[str, Any],
        maximum_output_tokens: int,
        timeout_seconds: int,
        require_case_membership: bool,
        cache_source: str = "previous_arm_a",
    ) -> tuple[object, dict[str, Any]]:
        """Replay one exact cached response with no ledger or network mutation."""
        _validate_logical_identity(
            logical_provider, logical_model, logical_reasoning_effort
        )
        _body, _identity, request_hash = self._call_identity(
            provider=logical_provider,
            stage=stage,
            model=logical_model,
            reasoning_effort=logical_reasoning_effort,
            system_prompt=system_prompt,
            payload=payload,
            response_schema=response_schema,
            maximum_output_tokens=maximum_output_tokens,
            timeout_seconds=timeout_seconds,
        )
        operation = self.previous.ledger_operations.get(request_hash)
        if operation is None:
            raise EvaluationError(
                f"required previous arm-A cache entry is unavailable: {case_id}:{stage}"
            )
        if (
            operation.get("status") != "completed"
            or operation.get("provider") != logical_provider
            or operation.get("stage") != stage
            or operation.get("logical_model") != logical_model
            or operation.get("logical_reasoning_effort") != logical_reasoning_effort
        ):
            raise EvaluationError("previous cached request metadata differs")
        if require_case_membership:
            result = self.previous.arm_a_results.get(str(case_id))
            matching = [
                event
                for event in (result or {}).get("requests") or []
                if isinstance(event, dict)
                and event.get("request_hash") == request_hash
                and event.get("stage") == stage
            ]
            if not matching:
                raise EvaluationError(
                    f"frozen call is not part of the case's previous arm-A route: {case_id}:{stage}"
                )
        cached = read_json(self.previous.response_dir / f"{request_hash}.json")
        raw = cached["raw"]
        event = {
            "request_hash": request_hash,
            "stage": stage,
            "logical_provider": logical_provider,
            "logical_model": logical_model,
            "logical_reasoning_effort": logical_reasoning_effort,
            "effective_provider": logical_provider,
            "effective_model": logical_model,
            "effective_reasoning_effort": logical_reasoning_effort,
            "provider": logical_provider,
            "cache_hit": True,
            "cache_source": cache_source,
            "network_request": False,
            "provider_error": None,
            "request_status": "completed",
            "input_tokens": operation.get("input_tokens"),
            "cached_input_tokens": operation.get("cached_input_tokens"),
            "cache_write_tokens": operation.get("cache_write_tokens"),
            "output_tokens": operation.get("output_tokens"),
            "reasoning_tokens": operation.get("reasoning_tokens"),
            "provider_reported_cost_usd": operation.get(
                "provider_reported_cost_usd"
            ),
            "estimated_cost_usd": operation.get("estimated_cost_usd"),
            "provider_latency_seconds": operation.get("provider_latency_seconds"),
            "response_model": operation.get("response_model"),
        }
        return previous_tool._extract_provider_content(raw), event


def _event_usage(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return the common non-payload request fields for a result event."""
    return previous_tool.ResearchTransport._event_usage(row)


def _response_payload_bytes(response: Any) -> bytes:
    """Reuse stable response bytes for definite HTTP-event hashing."""
    return previous_tool._response_payload_bytes(response)


class WriterPipelineTransport:
    """Serve frozen calls from arm A and only variable calls from live cache/API."""

    def __init__(
        self,
        *,
        arm: str,
        case_id: str,
        prior_cache: PriorResponseCache,
        registry: WriterInputRegistry,
        ledger: WriterRequestLedger | None = None,
        response_dir: Path | None = None,
        api_keys: Mapping[str, str] | None = None,
        xai_model_metadata: Mapping[str, Mapping[str, Any]] | None = None,
        post: Callable[..., Any] = requests.post,
        sleep: Callable[[float], None] = time.sleep,
        live_request: Callable[..., tuple[object, dict[str, Any]]] | None = None,
    ) -> None:
        """Initialise one arm/case view over frozen and candidate caches."""
        if arm not in ARMS:
            raise EvaluationError(f"unknown writer arm: {arm}")
        self.arm = arm
        self.case_id = str(case_id)
        self.prior_cache = prior_cache
        self.registry = registry
        self.ledger = ledger
        self.response_dir = response_dir
        self.api_keys = dict(api_keys or {})
        self.xai_model_metadata = dict(xai_model_metadata or {})
        self.post = post
        self.sleep = sleep
        self.live_request = live_request
        self.sequence = 0
        self.call_events: list[dict[str, Any]] = []
        self.writer_responses: list[dict[str, Any]] = []
        if response_dir is not None:
            os.makedirs(response_dir, mode=0o700, exist_ok=True)
            os.chmod(response_dir, 0o700)

    def _cache_path(self, request_hash: str) -> Path:
        """Return one private candidate-response cache path."""
        if self.response_dir is None:
            raise EvaluationError("candidate response directory is unavailable")
        return self.response_dir / f"{request_hash}.json"

    @staticmethod
    def _read_cache(path: Path, request_hash: str) -> dict[str, Any]:
        """Read and identity-check one candidate response record."""
        cached = read_json(path)
        if (
            not isinstance(cached, dict)
            or cached.get("request_hash") != request_hash
            or not isinstance(cached.get("raw"), dict)
        ):
            raise EvaluationError("candidate raw-response cache identity is invalid")
        return cached

    def _send_live(
        self,
        *,
        logical_provider: str,
        stage: str,
        logical_model: str,
        logical_reasoning_effort: str,
        effective_provider: str,
        effective_model: str,
        effective_reasoning_effort: str,
        system_prompt: str,
        payload: Mapping[str, Any],
        response_schema: Mapping[str, Any],
        timeout_seconds: int,
        max_output_tokens: int,
    ) -> tuple[object, dict[str, Any]]:
        """Serve one variable call from its canonical cache or one bounded send."""
        if self.live_request is not None:
            return self.live_request(
                logical_provider=logical_provider,
                stage=stage,
                logical_model=logical_model,
                logical_reasoning_effort=logical_reasoning_effort,
                effective_provider=effective_provider,
                effective_model=effective_model,
                effective_reasoning_effort=effective_reasoning_effort,
                system_prompt=system_prompt,
                payload=copy.deepcopy(dict(payload)),
                response_schema=copy.deepcopy(dict(response_schema)),
                timeout_seconds=timeout_seconds,
                max_output_tokens=max_output_tokens,
            )
        if self.ledger is None:
            raise EvaluationError("live candidate call has no request ledger")
        body, _identity, request_hash = candidate_request_identity(
            provider=effective_provider,
            stage=stage,
            model=effective_model,
            reasoning_effort=effective_reasoning_effort,
            system_prompt=system_prompt,
            payload=payload,
            response_schema=response_schema,
            maximum_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
        )
        event: dict[str, Any] = {
            "request_hash": request_hash,
            "stage": stage,
            "logical_provider": logical_provider,
            "logical_model": logical_model,
            "logical_reasoning_effort": logical_reasoning_effort,
            "effective_provider": effective_provider,
            "effective_model": effective_model,
            "effective_reasoning_effort": effective_reasoning_effort,
            "provider": effective_provider,
            "cache_hit": False,
            "cache_source": None,
            "network_request": False,
            "provider_error": None,
        }
        consumer_id = f"{self.case_id}:{self.arm}:{self.sequence}:{stage}"
        cache_path = self._cache_path(request_hash)
        prior = self.ledger.operation(request_hash)
        try:
            if prior is not None:
                self.ledger.add_consumer(prior, consumer_id)
                self.ledger.bind_provider_identities(
                    prior,
                    logical_provider=logical_provider,
                    effective_provider=effective_provider,
                )
                if cache_path.exists():
                    cached = self._read_cache(cache_path, request_hash)
                    if prior.get("status") != "completed":
                        self.ledger.complete(
                            prior,
                            raw=cached["raw"],
                            latency_seconds=float(cached.get("latency_seconds") or 0),
                        )
                    event["cache_hit"] = True
                    event["cache_source"] = "current_response_cache"
                    event.update(_event_usage(prior))
                    return previous_tool._extract_provider_content(cached["raw"]), event
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
                raise EvaluationError(
                    "orphan candidate response cache exists without a ledger row"
                )

            maximum_ticks = maximum_request_cost_ticks(
                provider=effective_provider,
                request_body=body,
                max_output_tokens=max_output_tokens,
                effective_model=effective_model,
                xai_model_metadata=self.xai_model_metadata,
            )
            row = self.ledger.reserve(
                request_hash=request_hash,
                provider=effective_provider,
                stage=stage,
                logical_model=logical_model,
                logical_effort=logical_reasoning_effort,
                effective_model=effective_model,
                effective_effort=effective_reasoning_effort,
                maximum_possible_cost_ticks=maximum_ticks,
                consumer_id=consumer_id,
            )
            self.ledger.bind_provider_identities(
                row,
                logical_provider=logical_provider,
                effective_provider=effective_provider,
            )
            url = (
                f"{XAI_BASE_URL}/chat/completions"
                if effective_provider == "xAI"
                else f"{OPENAI_BASE_URL}/chat/completions"
            )
            expected_host = XAI_HOST if effective_provider == "xAI" else OPENAI_HOST
            validate_provider_url(url, expected_host=expected_host)
            api_key = self.api_keys.get(effective_provider, "")
            if not api_key:
                raise EvaluationError(f"{effective_provider} API key is unavailable")
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
                        json=body,
                        timeout=timeout_seconds,
                        allow_redirects=False,
                    )
                except BaseException as exc:
                    error = AmbiguousRequestError(
                        f"{effective_provider} request raised before a definite response: "
                        f"{type(exc).__name__}"
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
                            f"{effective_provider} returned HTTP {status_code} twice"
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
                        f"{effective_provider} returned definite HTTP {status_code}"
                    )
                try:
                    raw = response.json()
                    if not isinstance(raw, dict):
                        raise ValueError("response JSON is not an object")
                except BaseException as exc:
                    error = AmbiguousRequestError(
                        f"{effective_provider} returned an unusable successful response"
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
                event["network_request"] = True
                event.update(_event_usage(row))
                return previous_tool._extract_provider_content(raw), event
        except BaseException as exc:
            event["provider_error"] = f"{type(exc).__name__}: {exc}"
            row = self.ledger.operation(request_hash)
            if row is not None:
                event.update(_event_usage(row))
            raise

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
        """Route a real pipeline call across the exact isolation boundary."""
        started = time.monotonic()
        self.sequence += 1
        identity = effective_call_identity(
            arm=self.arm,
            stage=stage,
            logical_provider=provider,
            logical_model=model,
            logical_reasoning_effort=reasoning_effort,
        )
        writer_input_hash: str | None = None
        if stage in WRITER_STAGES:
            writer_input_hash = self.registry.record(
                case_id=self.case_id,
                arm=self.arm,
                stage=stage,
                system_prompt=system_prompt,
                payload=payload,
                response_schema=response_schema,
                maximum_output_tokens=max_output_tokens,
                timeout_seconds=timeout_seconds,
            )
        replay = self.arm == "A" or stage not in WRITER_STAGES | CLAIM_AUDIT_STAGES
        exact_prior_audit = False
        if not replay and stage in CLAIM_AUDIT_STAGES:
            exact_prior_audit = self.prior_cache.contains_call(
                provider=provider,
                stage=stage,
                model=model,
                reasoning_effort=reasoning_effort,
                system_prompt=system_prompt,
                payload=payload,
                response_schema=response_schema,
                maximum_output_tokens=max_output_tokens,
                timeout_seconds=timeout_seconds,
            )
        try:
            if replay or exact_prior_audit:
                content, event = self.prior_cache.replay(
                    case_id=self.case_id,
                    logical_provider=provider,
                    stage=stage,
                    logical_model=model,
                    logical_reasoning_effort=reasoning_effort,
                    system_prompt=system_prompt,
                    payload=payload,
                    response_schema=response_schema,
                    maximum_output_tokens=max_output_tokens,
                    timeout_seconds=timeout_seconds,
                    require_case_membership=replay,
                    cache_source=(
                        "previous_exact_candidate_audit"
                        if exact_prior_audit
                        else "previous_arm_a"
                    ),
                )
            else:
                content, event = self._send_live(
                    logical_provider=provider,
                    stage=stage,
                    logical_model=model,
                    logical_reasoning_effort=reasoning_effort,
                    effective_provider=str(identity["effective_provider"]),
                    effective_model=str(identity["effective_model"]),
                    effective_reasoning_effort=str(
                        identity["effective_reasoning_effort"]
                    ),
                    system_prompt=system_prompt,
                    payload=payload,
                    response_schema=response_schema,
                    timeout_seconds=timeout_seconds,
                    max_output_tokens=max_output_tokens,
                )
            event["experiment_variable_call"] = stage in WRITER_STAGES | CLAIM_AUDIT_STAGES
            if writer_input_hash is not None:
                event["canonical_writer_input_sha256"] = writer_input_hash
            if stage in WRITER_STAGES:
                self.writer_responses.append(
                    {"stage": stage, "response": copy.deepcopy(content)}
                )
            return content
        finally:
            if "event" in locals():
                event["wall_latency_seconds"] = round(
                    time.monotonic() - started, 6
                )
                self.call_events.append(event)


def fetch_openai_model_availability(
    *,
    api_key: str,
    get: Callable[..., Any] = requests.get,
) -> dict[str, dict[str, Any]]:
    """Verify authenticated availability of all three OpenAI writer models."""
    if not api_key:
        raise EvaluationError("OPENAI_API_KEY is required for live execution")
    url = f"{OPENAI_BASE_URL}/models"
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
            f"OpenAI models endpoint failed: {type(exc).__name__}: {exc}"
        ) from exc
    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code != 200:
        raise ModelAvailabilityError(
            f"OpenAI models endpoint returned HTTP {status_code}"
        )
    try:
        document = response.json()
    except BaseException as exc:
        raise ModelAvailabilityError(
            "OpenAI models endpoint returned invalid JSON"
        ) from exc
    rows = document.get("data") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        raise ModelAvailabilityError("OpenAI models endpoint response lacks a data array")
    required = set(OPENAI_PRICING_USD_PER_MILLION)
    observed = {
        str(row.get("id")): row
        for row in rows
        if isinstance(row, dict) and row.get("id") in required
    }
    missing = sorted(required - set(observed))
    if missing:
        raise ModelAvailabilityError(
            "required OpenAI model availability check failed; missing="
            + ",".join(missing)
        )
    return {
        model: {"id": model, "retrieved_at": utc_now()}
        for model in sorted(required)
    }


def fetch_xai_model_metadata(
    *,
    api_key: str,
    get: Callable[..., Any] = requests.get,
) -> dict[str, dict[str, Any]]:
    """Reuse authenticated xAI availability and pricing validation."""
    return previous_tool.fetch_xai_model_metadata(api_key=api_key, get=get)


def load_api_keys(env_file: Path | None = None) -> dict[str, str]:
    """Reuse the existing environment-file convention without exposing keys."""
    return previous_tool.load_api_keys(env_file)


def _writer_response_value(value: object) -> dict[str, Any] | None:
    """Parse one retained structured writer response without altering it."""
    parsed = value
    if isinstance(value, str):
        try:
            parsed = strict_json_loads(value)
        except (ValueError, json.JSONDecodeError):
            return None
    return copy.deepcopy(parsed) if isinstance(parsed, dict) else None


def _initial_writer_draft(transport: WriterPipelineTransport) -> str | None:
    """Return the exact initial draft text, if the structured writer supplied one."""
    for row in transport.writer_responses:
        if row.get("stage") != "writer_v3_initial":
            continue
        parsed = _writer_response_value(row.get("response"))
        if isinstance(parsed, dict) and parsed.get("status") == "reply":
            reply = parsed.get("reply")
            return str(reply) if isinstance(reply, str) else None
        return None
    return None


def _sum_tokens(events: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Sum reported token fields across request events."""
    return {
        name: sum(
            int(event.get(field) or 0)
            for event in events
            if type(event.get(field)) is int
        )
        for name, field in {
            "input": "input_tokens",
            "cached_input": "cached_input_tokens",
            "cache_write": "cache_write_tokens",
            "output": "output_tokens",
            "reasoning": "reasoning_tokens",
        }.items()
    }


def _event_cost(events: Sequence[Mapping[str, Any]], key: str) -> float:
    """Sum one cost field from request events."""
    return sum(float(event.get(key) or 0) for event in events)


def result_from_pipeline(
    *,
    case: Mapping[str, Any],
    arm: str,
    execution_order_index: int,
    result: Any,
    transport: WriterPipelineTransport,
    wall_latency_seconds: float,
) -> dict[str, Any]:
    """Retain initial draft, final public outcome, audits, usage, and identities."""
    audit = list(result.audit)
    telemetry = previous_tool.stage_telemetry(result.audit)
    final_reply = str(result.reply) if result.reply is not None else None
    events = copy.deepcopy(transport.call_events)
    initial_events = [event for event in events if event.get("stage") == "writer_v3_initial"]
    writer_events = [event for event in events if event.get("stage") in WRITER_STAGES]
    variable_events = [
        event for event in events if event.get("experiment_variable_call") is True
    ]
    new_events = [event for event in events if event.get("network_request") is True]
    grade = previous_tool.grade_challenge_result(
        case,
        final_status=str(result.status),
        final_reply=final_reply,
    )
    definition = ARMS[arm]
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case["case_id"],
        "source": case["source"],
        "arm": arm,
        "arm_execution_order": copy.deepcopy(case["writer_arm_execution_order"]),
        "arm_execution_order_index": execution_order_index,
        "canonical_writer_input_sha256": transport.registry.initial_hash(
            str(case["case_id"])
        ),
        "logical_writer_provider": "OpenAI",
        "logical_writer_model": LOGICAL_OPENAI_MODEL,
        "logical_writer_reasoning_effort": LOGICAL_OPENAI_EFFORT,
        "effective_writer_provider": definition["effective_provider"],
        "effective_writer_model": definition["effective_model"],
        "effective_writer_reasoning_effort": definition[
            "effective_reasoning_effort"
        ],
        "baseline_replayed_from_previous_cache": arm == "A",
        "execution_status": "completed",
        "final_status": str(result.status),
        "final_reason": str(result.reason),
        "initial_writer_draft": _initial_writer_draft(transport),
        "writer_responses": copy.deepcopy(transport.writer_responses),
        "final_public_reply": final_reply,
        "audit": audit,
        "stage_telemetry": telemetry,
        "xai_claim_audit_outcomes": copy.deepcopy(
            telemetry.get("claim_audit_outcomes") or []
        ),
        "model_call_count": int(result.model_call_count),
        "revision_count": int(getattr(result, "revision_count", 0) or 0),
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
        "tokens": _sum_tokens(events),
        "initial_draft_tokens": _sum_tokens(initial_events),
        "final_output_path_tokens": _sum_tokens(writer_events),
        "incremental_provider_reported_cost_usd": _event_cost(
            new_events, "provider_reported_cost_usd"
        ),
        "incremental_estimated_cost_usd": _event_cost(
            new_events, "estimated_cost_usd"
        ),
        "variable_path_attributed_provider_reported_cost_usd": _event_cost(
            variable_events, "provider_reported_cost_usd"
        ),
        "variable_path_attributed_estimated_cost_usd": _event_cost(
            variable_events, "estimated_cost_usd"
        ),
        "writer_provider_latency_seconds": sum(
            float(event.get("provider_latency_seconds") or 0)
            for event in writer_events
        ),
        "variable_path_provider_latency_seconds": sum(
            float(event.get("provider_latency_seconds") or 0)
            for event in variable_events
        ),
        "arm_wall_latency_seconds": round(wall_latency_seconds, 6),
        "requests": events,
        "automatic_fixture_grade": grade,
        "completed_at": utc_now(),
    }


def error_result(
    *,
    case: Mapping[str, Any],
    arm: str,
    execution_order_index: int,
    transport: WriterPipelineTransport,
    error: BaseException,
    wall_latency_seconds: float,
    incomplete: bool,
) -> dict[str, Any]:
    """Retain a non-retried provider or cost-bound incomplete arm."""
    events = copy.deepcopy(transport.call_events)
    definition = ARMS[arm]
    initial_hash = transport.registry.initial_hashes.get(str(case["case_id"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case["case_id"],
        "source": case["source"],
        "arm": arm,
        "arm_execution_order": copy.deepcopy(case["writer_arm_execution_order"]),
        "arm_execution_order_index": execution_order_index,
        "canonical_writer_input_sha256": initial_hash,
        "logical_writer_provider": "OpenAI",
        "logical_writer_model": LOGICAL_OPENAI_MODEL,
        "logical_writer_reasoning_effort": LOGICAL_OPENAI_EFFORT,
        "effective_writer_provider": definition["effective_provider"],
        "effective_writer_model": definition["effective_model"],
        "effective_writer_reasoning_effort": definition[
            "effective_reasoning_effort"
        ],
        "baseline_replayed_from_previous_cache": False,
        "execution_status": "incomplete" if incomplete else "error",
        "final_status": "incomplete" if incomplete else "error",
        "final_reason": f"{type(error).__name__}: {error}",
        "initial_writer_draft": _initial_writer_draft(transport),
        "writer_responses": copy.deepcopy(transport.writer_responses),
        "final_public_reply": None,
        "audit": [],
        "stage_telemetry": {},
        "xai_claim_audit_outcomes": [],
        "model_call_count": len(events),
        "revision_count": 0,
        "request_count": len(events),
        "cache_hits": sum(event.get("cache_hit") is True for event in events),
        "schema_failures": [],
        "provider_errors": [
            str(event["provider_error"])
            for event in events
            if event.get("provider_error")
        ],
        "tokens": _sum_tokens(events),
        "initial_draft_tokens": _sum_tokens(
            [event for event in events if event.get("stage") == "writer_v3_initial"]
        ),
        "final_output_path_tokens": _sum_tokens(
            [event for event in events if event.get("stage") in WRITER_STAGES]
        ),
        "incremental_provider_reported_cost_usd": _event_cost(
            [event for event in events if event.get("network_request") is True],
            "provider_reported_cost_usd",
        ),
        "incremental_estimated_cost_usd": _event_cost(
            [event for event in events if event.get("network_request") is True],
            "estimated_cost_usd",
        ),
        "variable_path_attributed_provider_reported_cost_usd": _event_cost(
            [event for event in events if event.get("experiment_variable_call") is True],
            "provider_reported_cost_usd",
        ),
        "variable_path_attributed_estimated_cost_usd": _event_cost(
            [event for event in events if event.get("experiment_variable_call") is True],
            "estimated_cost_usd",
        ),
        "writer_provider_latency_seconds": 0.0,
        "variable_path_provider_latency_seconds": 0.0,
        "arm_wall_latency_seconds": round(wall_latency_seconds, 6),
        "requests": events,
        "automatic_fixture_grade": None,
        "completed_at": utc_now(),
    }


def assert_baseline_replay_exact(
    *, result: Any, transport: WriterPipelineTransport, stored: Mapping[str, Any]
) -> None:
    """Stop unless replay reproduces status, reason, reply, and call sequence."""
    observed = {
        "final_status": str(result.status),
        "final_reason": str(result.reason),
        "final_public_reply": str(result.reply) if result.reply is not None else None,
    }
    expected = {
        "final_status": stored.get("final_status"),
        "final_reason": stored.get("final_reason"),
        "final_public_reply": stored.get("final_public_reply"),
    }
    if observed != expected:
        raise EvaluationError("offline arm-A replay changed the stored public outcome")
    observed_calls = [
        (event.get("stage"), event.get("request_hash"))
        for event in transport.call_events
    ]
    expected_calls = [
        (event.get("stage"), event.get("request_hash"))
        for event in stored.get("requests") or []
        if isinstance(event, dict)
    ]
    if observed_calls != expected_calls:
        raise EvaluationError("offline arm-A replay changed the stored call path")
    if any(event.get("cache_source") != "previous_arm_a" for event in transport.call_events):
        raise EvaluationError("offline arm A was not served solely from the previous cache")


def _validate_pipeline_source_identity(project_dir: Path) -> None:
    """Require current routing/pipeline sources to match the previous source commit."""
    paths = ("tested_reply_pipeline.py", "reply_strategy.py", "reply_evidence.py")
    process = subprocess.run(
        [
            "git",
            "diff",
            "--quiet",
            EXPECTED_PREVIOUS_SOURCE_SHA,
            "origin/master",
            "--",
            *paths,
        ],
        cwd=project_dir,
        check=False,
    )
    if process.returncode != 0:
        raise EvaluationError(
            "current production reply-pipeline sources differ from the frozen experiment"
        )


def _git_revision(project_dir: Path, revision: str) -> str:
    """Resolve one Git revision without modifying repository state."""
    return previous_tool.git_revision(project_dir, revision)


def replay_baseline_cases(
    *,
    project_dir: Path,
    cases: Sequence[dict[str, Any]],
    previous: PreviousExperiment,
    registry: WriterInputRegistry,
) -> list[dict[str, Any]]:
    """Run the real pipeline for arm A entirely from the immutable old cache."""
    config = production_config()
    corpus_path = project_dir / str(config["research_corpus_path"])
    repository = previous_tool.EvidenceRepository(
        corpus_path,
        factual_evidence_path=project_dir / "reply_factual_evidence.json",
    )
    prior_cache = PriorResponseCache(previous)
    rows: list[dict[str, Any]] = []
    for case in cases:
        transport = WriterPipelineTransport(
            arm="A",
            case_id=str(case["case_id"]),
            prior_cache=prior_cache,
            registry=registry,
        )
        started = time.monotonic()
        result = previous_tool.run_reply_pipeline(
            context=copy.deepcopy(case["context"]),
            config=copy.deepcopy(config),
            repository=repository,
            transport=transport,
            maximum_reply_length=MAX_REPLY_LENGTH,
            recent_replies=copy.deepcopy(case["recent_replies"]),
            media_context=None,
        )
        assert_baseline_replay_exact(
            result=result,
            transport=transport,
            stored=previous.arm_a_results[str(case["case_id"])],
        )
        rows.append(
            result_from_pipeline(
                case=case,
                arm="A",
                execution_order_index=0,
                result=result,
                transport=transport,
                wall_latency_seconds=time.monotonic() - started,
            )
        )
    return rows


def load_writer_results(path: Path) -> list[dict[str, Any]]:
    """Load unique, resumable writer-arm result rows."""
    if not path.exists():
        return []
    rows = read_jsonl(path)
    identities = [(str(row.get("case_id")), str(row.get("arm"))) for row in rows]
    if len(identities) != len(set(identities)):
        raise EvaluationError("writer result identities are duplicated")
    if any(arm not in ARMS for _case, arm in identities):
        raise EvaluationError("writer results contain an unknown arm")
    return rows


def _baseline_semantic_identity(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return stable arm-A fields that must match every offline replay."""
    return {
        "case_id": row.get("case_id"),
        "arm": row.get("arm"),
        "canonical_writer_input_sha256": row.get(
            "canonical_writer_input_sha256"
        ),
        "final_status": row.get("final_status"),
        "final_reason": row.get("final_reason"),
        "initial_writer_draft": row.get("initial_writer_draft"),
        "final_public_reply": row.get("final_public_reply"),
        "calls": [
            (event.get("stage"), event.get("request_hash"))
            for event in row.get("requests") or []
            if isinstance(event, dict)
        ],
    }


def _immutable_manifest_fields(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return manifest fields that cannot change during safe resumption."""
    excluded = {
        "created_at",
        "updated_at",
        "execution",
        "authenticated_model_availability",
    }
    return {
        key: copy.deepcopy(value)
        for key, value in manifest.items()
        if key not in excluded
    }


def prepare_experiment(
    *,
    project_dir: Path,
    output_dir: Path,
    previous_root: Path = PREVIOUS_OUTPUT_ROOT,
) -> tuple[dict[str, Any], list[dict[str, Any]], PreviousExperiment]:
    """Validate old artifacts, select cases, and exactly replay arm A offline."""
    previous = validate_previous_experiment(previous_root)
    _validate_pipeline_source_identity(project_dir)
    config = production_config()
    evidence_identity = previous_tool.repository_identity(project_dir, config)
    if evidence_identity != previous.manifest.get("evidence_corpus"):
        raise EvaluationError("current local evidence differs from previous arm A")
    cases = select_writer_cases(previous)
    registry = WriterInputRegistry()
    baseline_rows = replay_baseline_cases(
        project_dir=project_dir,
        cases=cases,
        previous=previous,
        registry=registry,
    )
    for case in cases:
        case["canonical_writer_input_sha256"] = registry.initial_hash(
            str(case["case_id"])
        )
    writer_case_set_sha256 = sha256_value(cases)
    source_master_sha = _git_revision(project_dir, "origin/master")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_version": RUN_VERSION,
        "created_at": utc_now(),
        "source_git_sha": source_master_sha,
        "source_origin_master_sha": source_master_sha,
        "project_dir": str(project_dir),
        "output_dir": str(output_dir),
        "previous_output_root": str(previous.root),
        "previous_source_git_sha": EXPECTED_PREVIOUS_SOURCE_SHA,
        "previous_case_set_sha256": EXPECTED_PREVIOUS_CASE_SET_SHA256,
        "previous_artifact_hashes": copy.deepcopy(previous.file_hashes),
        "writer_case_set_sha256": writer_case_set_sha256,
        "selected_case_count": len(cases),
        "logical_production_config": config,
        "evidence_corpus": evidence_identity,
        "arm_definitions": copy.deepcopy(ARMS),
        "writer_stages": sorted(WRITER_STAGES),
        "claim_audit_stages": sorted(CLAIM_AUDIT_STAGES),
        "canonical_initial_writer_inputs_identical_across_arms": True,
        "tool_sha256": sha256_file(Path(__file__).resolve()),
        "imported_harness_sha256": sha256_file(
            project_dir / "tools/run_grok_46_tested_pipeline_eval.py"
        ),
        "network_allowlist": sorted(NETWORK_ALLOWLIST),
        "posting_enabled": False,
        "x_client_imported": False,
        "global_cost_ceiling_usd": GLOBAL_COST_CEILING_USD,
        "openai_estimate_pricing": {
            model: {
                **rates,
                "units": "USD per million tokens",
                "authoritative": False,
            }
            for model, rates in OPENAI_PRICING_USD_PER_MILLION.items()
        },
        "authenticated_model_availability": {"OpenAI": [], "xAI": []},
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
            raise EvaluationError("existing writer manifest is invalid")
        comparable = copy.deepcopy(manifest)
        comparable["created_at"] = existing.get("created_at")
        comparable["updated_at"] = existing.get("updated_at")
        comparable["execution"] = existing.get("execution")
        comparable["authenticated_model_availability"] = existing.get(
            "authenticated_model_availability"
        )
        if _immutable_manifest_fields(existing) != _immutable_manifest_fields(comparable):
            raise EvaluationError("immutable writer experiment inputs changed on resume")
        manifest = existing
    else:
        atomic_json(manifest_path, manifest)

    cases_path = private_path(output_dir, "writer_cases.jsonl")
    expected_case_bytes = b"".join(
        canonical_json_bytes(case, newline=True) for case in cases
    )
    if cases_path.exists() and cases_path.read_bytes() != expected_case_bytes:
        raise EvaluationError("frozen writer case set changed on resume")
    if not cases_path.exists():
        atomic_bytes(cases_path, expected_case_bytes)

    results_path = private_path(output_dir, "writer_results.jsonl")
    existing_results = load_writer_results(results_path)
    selected_ids = {str(case["case_id"]) for case in cases}
    if any(str(row.get("case_id")) not in selected_ids for row in existing_results):
        raise EvaluationError("writer results contain a case outside the frozen selection")
    existing_map = {
        (str(row["case_id"]), str(row["arm"])): row for row in existing_results
    }
    for baseline in baseline_rows:
        identity = (str(baseline["case_id"]), "A")
        prior = existing_map.get(identity)
        if prior is not None:
            if _baseline_semantic_identity(prior) != _baseline_semantic_identity(baseline):
                raise EvaluationError("stored arm-A baseline differs from offline replay")
        else:
            existing_results.append(baseline)
            existing_map[identity] = baseline
    order = {str(case["case_id"]): index for index, case in enumerate(cases)}
    existing_results.sort(
        key=lambda row: (
            order[str(row["case_id"])],
            int(row.get("arm_execution_order_index") or 0),
        )
    )
    atomic_jsonl(results_path, existing_results)
    create_or_load_arm_key(private_path(output_dir, "arm_key.private.json"))
    WriterRequestLedger(
        private_path(output_dir, "request_ledger.json"),
        case_set_sha256=writer_case_set_sha256,
        hard_limit_usd=GLOBAL_COST_CEILING_USD,
    )
    return manifest, cases, previous


def _result_sort_key(
    row: Mapping[str, Any], cases: Sequence[Mapping[str, Any]]
) -> tuple[int, int]:
    """Return stable case and per-case execution ordering for results."""
    order = {str(case["case_id"]): index for index, case in enumerate(cases)}
    return order[str(row["case_id"])], int(row.get("arm_execution_order_index") or 0)


def execute_experiment(
    *,
    execute_live_models: bool,
    project_dir: Path | None = None,
    output_dir: Path | None = None,
    manifest: Mapping[str, Any] | None = None,
    cases: Sequence[dict[str, Any]] = (),
    previous: PreviousExperiment | None = None,
    api_keys: Mapping[str, str] | None = None,
    hard_limit_usd: float = GLOBAL_COST_CEILING_USD,
    get: Callable[..., Any] = requests.get,
    post: Callable[..., Any] = requests.post,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[
    list[dict[str, Any]],
    WriterRequestLedger,
    str | None,
    dict[str, dict[str, dict[str, Any]]],
]:
    """Verify models and execute only non-baseline arms with safe resumption."""
    if not execute_live_models:
        raise EvaluationError("--execute-live-models is required before any provider call")
    if project_dir is None or output_dir is None or manifest is None or previous is None:
        raise EvaluationError("live execution inputs are incomplete")
    keys = dict(api_keys or {})
    missing = [
        name
        for name, provider in (("XAI_API_KEY", "xAI"), ("OPENAI_API_KEY", "OpenAI"))
        if not keys.get(provider)
    ]
    if missing:
        raise EvaluationError("live execution lacks " + ", ".join(missing))
    if not 0 < hard_limit_usd <= GLOBAL_COST_CEILING_USD:
        raise EvaluationError("--cost-ceiling-usd must be in (0, 15.00]")

    openai_metadata = fetch_openai_model_availability(
        api_key=keys["OpenAI"], get=get
    )
    xai_metadata = fetch_xai_model_metadata(api_key=keys["xAI"], get=get)
    availability = {"OpenAI": openai_metadata, "xAI": xai_metadata}
    ledger = WriterRequestLedger(
        private_path(output_dir, "request_ledger.json"),
        case_set_sha256=str(manifest["writer_case_set_sha256"]),
        hard_limit_usd=hard_limit_usd,
    )
    config = production_config()
    repository = previous_tool.EvidenceRepository(
        project_dir / str(config["research_corpus_path"]),
        factual_evidence_path=project_dir / "reply_factual_evidence.json",
    )
    prior_cache = PriorResponseCache(previous)
    results_path = private_path(output_dir, "writer_results.jsonl")
    results = load_writer_results(results_path)
    result_map = {(str(row["case_id"]), str(row["arm"])): row for row in results}
    registry = WriterInputRegistry(
        {
            str(case["case_id"]): str(case["canonical_writer_input_sha256"])
            for case in cases
        }
    )
    blocker: str | None = None
    stop = False
    for case in cases:
        for execution_index, arm in enumerate(case["writer_arm_execution_order"]):
            if arm == "A" or (str(case["case_id"]), arm) in result_map:
                continue
            transport = WriterPipelineTransport(
                arm=arm,
                case_id=str(case["case_id"]),
                prior_cache=prior_cache,
                registry=registry,
                ledger=ledger,
                response_dir=private_path(output_dir, "responses"),
                api_keys=keys,
                xai_model_metadata=xai_metadata,
                post=post,
                sleep=sleep,
            )
            started = time.monotonic()
            try:
                pipeline_result = previous_tool.run_reply_pipeline(
                    context=copy.deepcopy(case["context"]),
                    config=copy.deepcopy(config),
                    repository=repository,
                    transport=transport,
                    maximum_reply_length=MAX_REPLY_LENGTH,
                    recent_replies=copy.deepcopy(case["recent_replies"]),
                    media_context=None,
                )
                row = result_from_pipeline(
                    case=case,
                    arm=arm,
                    execution_order_index=execution_index,
                    result=pipeline_result,
                    transport=transport,
                    wall_latency_seconds=time.monotonic() - started,
                )
            except (CostLimitReached, AmbiguousRequestError) as exc:
                blocker = f"{type(exc).__name__}: {exc}"
                row = error_result(
                    case=case,
                    arm=arm,
                    execution_order_index=execution_index,
                    transport=transport,
                    error=exc,
                    wall_latency_seconds=time.monotonic() - started,
                    incomplete=True,
                )
                stop = True
            except (DefiniteProviderError, TransientProviderError) as exc:
                blocker = f"{type(exc).__name__}: {exc}"
                row = error_result(
                    case=case,
                    arm=arm,
                    execution_order_index=execution_index,
                    transport=transport,
                    error=exc,
                    wall_latency_seconds=time.monotonic() - started,
                    incomplete=False,
                )
                stop = True
            except EvaluationError:
                raise
            except BaseException as exc:
                row = error_result(
                    case=case,
                    arm=arm,
                    execution_order_index=execution_index,
                    transport=transport,
                    error=exc,
                    wall_latency_seconds=time.monotonic() - started,
                    incomplete=False,
                )
            results.append(row)
            result_map[(str(case["case_id"]), arm)] = row
            results.sort(key=lambda item: _result_sort_key(item, cases))
            atomic_jsonl(results_path, results)
            if stop:
                break
        if stop:
            break
    return results, ledger, blocker, availability


def update_manifest_execution(
    output_dir: Path,
    *,
    mode: str,
    blocker: str | None,
    ledger: WriterRequestLedger,
    availability: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Update only mutable execution and authenticated-availability fields."""
    path = private_path(output_dir, "manifest.json")
    manifest = read_json(path)
    if not isinstance(manifest, dict):
        raise EvaluationError("writer experiment manifest is invalid")
    operations = [
        row for row in ledger.data.get("operations", []) if isinstance(row, dict)
    ]
    if availability is not None:
        manifest["authenticated_model_availability"] = {
            provider: sorted(models)
            for provider, models in availability.items()
        }
    manifest["execution"] = {
        "mode": mode,
        "blocker": blocker,
        "cost": ledger.cost_summary(),
        "canonical_candidate_request_count": len(operations),
        "completed_candidate_request_count": sum(
            row.get("status") == "completed" for row in operations
        ),
        "transmission_attempt_count": sum(
            int(row.get("attempt_number") or 0) for row in operations
        ),
        "current_cache_consumer_count": sum(
            max(0, len(row.get("consumers") or []) - 1) for row in operations
        ),
    }
    manifest["updated_at"] = utc_now()
    atomic_json(path, manifest)
    return manifest


def _percentile(values: Sequence[float], proportion: float) -> float | None:
    """Return the nearest-rank percentile for numeric values."""
    return previous_tool.percentile(values, proportion)


def _latency_summary(values: Sequence[float]) -> dict[str, float | None]:
    """Return median, p95, and maximum latency."""
    return {
        "median_seconds": statistics.median(values) if values else None,
        "p95_seconds": _percentile(values, 0.95),
        "maximum_seconds": max(values) if values else None,
    }


def _deterministic_rejection(row: Mapping[str, Any]) -> bool:
    """Return whether existing deterministic validation rejected this arm."""
    reason = str(row.get("final_reason") or "")
    prefixes = (
        "writer_local_rejection:",
        "cleanup_local_rejection:",
        "final_validation:",
        "deterministic_rejection:",
    )
    if reason.startswith(prefixes):
        return True
    return any(
        isinstance(item, dict)
        and "deterministic_validation" in str(item.get("stage") or "")
        and bool(item.get("rejection"))
        for item in row.get("audit") or []
    )


def _cannot_compose(row: Mapping[str, Any]) -> bool:
    """Return whether a writer stage explicitly could not compose safely."""
    return "cannot_compose_safely" in str(row.get("final_reason") or "") or any(
        isinstance(item, dict)
        and item.get("stage") == "direct_answer_repair_outcome"
        and item.get("outcome") == "writer_cannot_compose_safely"
        for item in row.get("audit") or []
    )


def _claim_audit_nonpass(row: Mapping[str, Any]) -> bool:
    """Return whether any candidate-specific claim audit did not pass."""
    outcomes = [
        str(item.get("outcome") or "")
        for item in row.get("xai_claim_audit_outcomes") or []
        if isinstance(item, dict)
    ]
    return any(outcome and outcome != "pass" for outcome in outcomes)


def _sum_token_dicts(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, int]:
    """Sum one nested token dictionary across result rows."""
    names = ("input", "cached_input", "cache_write", "output", "reasoning")
    return {
        name: sum(int(row.get(key, {}).get(name) or 0) for row in rows)
        for name in names
    }


def build_comparison(
    *,
    manifest: Mapping[str, Any],
    cases: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    ledger: WriterRequestLedger,
    blocker: str | None,
    human_scoring: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build operational aggregates without selecting a quality winner."""
    result_map = {
        (str(row["case_id"]), str(row["arm"])): row for row in results
    }
    complete_cases = [
        str(case["case_id"])
        for case in cases
        if all(
            result_map.get((str(case["case_id"]), arm), {}).get("execution_status")
            == "completed"
            for arm in ARMS
        )
    ]
    arms: dict[str, Any] = {}
    for arm, definition in ARMS.items():
        rows = [
            row
            for row in results
            if row.get("arm") == arm and row.get("execution_status") == "completed"
        ]
        writer_latencies = [
            float(event.get("provider_latency_seconds") or 0)
            for row in rows
            for event in row.get("requests") or []
            if isinstance(event, dict) and event.get("stage") in WRITER_STAGES
        ]
        grades = [
            row["automatic_fixture_grade"]
            for row in rows
            if isinstance(row.get("automatic_fixture_grade"), dict)
        ]
        arms[arm] = {
            **copy.deepcopy(definition),
            "completed_case_count": len(rows),
            "schema_failure_count": sum(len(row.get("schema_failures") or []) for row in rows),
            "provider_failure_count": sum(len(row.get("provider_errors") or []) for row in rows),
            "deterministic_rejection_count": sum(_deterministic_rejection(row) for row in rows),
            "cannot_compose_safely_count": sum(_cannot_compose(row) for row in rows),
            "claim_cleanup_attempt_count": sum(
                any(
                    isinstance(event, dict) and event.get("stage") == "bounded_claim_cleanup"
                    for event in row.get("requests") or []
                )
                for row in rows
            ),
            "duplicate_repair_attempt_count": sum(
                any(
                    isinstance(event, dict) and event.get("stage") == "exact_duplicate_repair"
                    for event in row.get("requests") or []
                )
                for row in rows
            ),
            "duplicate_repair_success_count": sum(
                any(
                    isinstance(item, dict)
                    and item.get("stage") == "exact_duplicate_repair_outcome"
                    and item.get("outcome") == "repaired"
                    for item in row.get("audit") or []
                )
                for row in rows
            ),
            "final_reply_count": sum(bool(row.get("final_public_reply")) for row in rows),
            "final_no_reply_count": sum(not bool(row.get("final_public_reply")) for row in rows),
            "initial_draft_tokens": _sum_token_dicts(rows, "initial_draft_tokens"),
            "final_output_path_tokens": _sum_token_dicts(rows, "final_output_path_tokens"),
            "incremental_billed_cost_usd": sum(
                float(row.get("incremental_provider_reported_cost_usd") or 0)
                for row in rows
            ),
            "incremental_estimated_cost_usd": sum(
                float(row.get("incremental_estimated_cost_usd") or 0)
                for row in rows
            ),
            "variable_path_attributed_billed_cost_usd": sum(
                float(row.get("variable_path_attributed_provider_reported_cost_usd") or 0)
                for row in rows
            ),
            "variable_path_attributed_estimated_cost_usd": sum(
                float(row.get("variable_path_attributed_estimated_cost_usd") or 0)
                for row in rows
            ),
            "writer_request_latency": _latency_summary(writer_latencies),
            "arm_wall_latency": _latency_summary(
                [float(row.get("arm_wall_latency_seconds") or 0) for row in rows]
            ),
            "tracked_fixture_pass_count": sum(
                grade.get("status") == "pass" for grade in grades
            ),
            "tracked_fixture_fail_count": sum(
                grade.get("status") == "fail" for grade in grades
            ),
        }
    operations = [
        row for row in ledger.data.get("operations", []) if isinstance(row, dict)
    ]
    request_events = [
        event
        for row in results
        for event in row.get("requests") or []
        if isinstance(event, dict)
    ]
    comparison = {
        "schema_version": SCHEMA_VERSION,
        "run_version": RUN_VERSION,
        "generated_at": utc_now(),
        "source_master_sha": manifest["source_origin_master_sha"],
        "previous_case_set_sha256": manifest["previous_case_set_sha256"],
        "writer_case_set_sha256": manifest["writer_case_set_sha256"],
        "selected_case_count": len(cases),
        "completed_case_count": len(complete_cases),
        "completed_arm_count": sum(
            row.get("execution_status") == "completed" for row in results
        ),
        "schema_failure_count": sum(
            len(row.get("schema_failures") or []) for row in results
        ),
        "provider_failure_count": sum(
            len(row.get("provider_errors") or []) for row in results
        ),
        "deterministic_rejection_count": sum(
            _deterministic_rejection(row)
            for row in results
            if row.get("execution_status") == "completed"
        ),
        "cannot_compose_safely_count": sum(
            _cannot_compose(row)
            for row in results
            if row.get("execution_status") == "completed"
        ),
        "provider_request_event_count": len(request_events),
        "cache_hit_count": sum(event.get("cache_hit") is True for event in request_events),
        "canonical_live_request_count": len(operations),
        "provider_transmission_attempt_count": sum(
            int(row.get("attempt_number") or 0) for row in operations
        ),
        "cost": ledger.cost_summary(),
        "live_provider_latency": _latency_summary(
            [
                float(row.get("provider_latency_seconds") or 0)
                for row in operations
                if row.get("status") == "completed"
            ]
        ),
        "arms": arms,
        "execution_blocker": blocker,
        "human_scoring": copy.deepcopy(human_scoring),
        "quality_conclusion": (
            "No quality winner is declared before completed blind human scoring."
            if human_scoring is None
            else "Completed blind human scores are reported descriptively without a deployment recommendation."
        ),
    }
    return comparison


def render_comparison_markdown(comparison: Mapping[str, Any]) -> str:
    """Render the private operational report without a pre-scoring winner."""
    cost = comparison["cost"]
    latency = comparison["live_provider_latency"]
    lines = [
        "# Tested-Pipeline Writer Model Evaluation",
        "",
        f"Source master SHA: `{comparison['source_master_sha']}`",
        f"Previous case-set SHA-256: `{comparison['previous_case_set_sha256']}`",
        f"Writer case-set SHA-256: `{comparison['writer_case_set_sha256']}`",
        "",
        "## Execution",
        "",
        f"- Selected cases: {comparison['selected_case_count']}",
        f"- Completed five-arm cases: {comparison['completed_case_count']}",
        f"- Completed arm results: {comparison['completed_arm_count']}",
        f"- Pipeline provider-call events: {comparison['provider_request_event_count']}",
        f"- Cache hits: {comparison['cache_hit_count']}",
        f"- Canonical live requests: {comparison['canonical_live_request_count']}",
        f"- Provider transmissions: {comparison['provider_transmission_attempt_count']}",
        f"- Schema failures: {comparison['schema_failure_count']}",
        f"- Provider failures: {comparison['provider_failure_count']}",
        f"- Deterministic rejections: {comparison['deterministic_rejection_count']}",
        f"- `cannot_compose_safely`: {comparison['cannot_compose_safely_count']}",
        f"- Execution blocker: `{comparison['execution_blocker'] or 'none'}`",
        "",
        "## Cost and latency",
        "",
        f"- Provider-reported billed cost: US${float(cost.get('total_billed_cost_usd') or 0):.6f}",
        f"- Estimated OpenAI cost: US${float(cost.get('total_estimated_cost_usd') or 0):.6f}",
        f"- Ambiguous maximum exposure: US${float(cost.get('ambiguous_exposure_usd') or 0):.6f}",
        f"- Live request median latency: {latency['median_seconds']!r} seconds",
        f"- Live request p95 latency: {latency['p95_seconds']!r} seconds",
        "",
        "Billed and estimated costs are kept separate. Cached arm-A costs are historical attributions and are not included in the new-execution totals above.",
        "",
        "## Arm operations",
        "",
        "| Arm | Effective writer | Completed | Reply | No reply | Schema fail | Provider fail | Deterministic reject | Cannot compose | Claim cleanup | Duplicate repair | Initial input tokens | Initial output tokens | Final-path output tokens | Fixture pass | Fixture fail | Writer median (s) | Writer p95 (s) |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in ARMS:
        row = comparison["arms"][arm]
        writer = (
            f"{row['effective_provider']} / {row['effective_model']} / "
            f"{row['effective_reasoning_effort']}"
        )
        lines.append(
            f"| {arm} | {writer} | {row['completed_case_count']} "
            f"| {row['final_reply_count']} | {row['final_no_reply_count']} "
            f"| {row['schema_failure_count']} | {row['provider_failure_count']} "
            f"| {row['deterministic_rejection_count']} | {row['cannot_compose_safely_count']} "
            f"| {row['claim_cleanup_attempt_count']} | {row['duplicate_repair_attempt_count']} "
            f"| {row['initial_draft_tokens']['input']} | {row['initial_draft_tokens']['output']} "
            f"| {row['final_output_path_tokens']['output']} "
            f"| {row['tracked_fixture_pass_count']} | {row['tracked_fixture_fail_count']} "
            f"| {row['writer_request_latency']['median_seconds']!r} "
            f"| {row['writer_request_latency']['p95_seconds']!r} |"
        )
    lines.extend(["", "## Interpretation boundary", "", str(comparison["quality_conclusion"]), ""])
    scoring = comparison.get("human_scoring")
    if isinstance(scoring, dict):
        lines.extend(
            [
                "## Completed blind scoring",
                "",
                f"Scored cases: {scoring['scored_case_count']}",
                "",
                "| Arm | Acceptable | Minor | Unacceptable | Preferred | Pairwise wins | Losses | Ties | Added unacceptable vs Sol | Billed cost / acceptable | Estimated cost / acceptable | Guard regressions | Claim-audit regressions |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for arm in ARMS:
            row = scoring["arms"][arm]
            lines.append(
                f"| {arm} | {row['acceptable']} | {row['minor']} | {row['unacceptable']} "
                f"| {row['preferred']} | {row['pairwise_wins']} | {row['pairwise_losses']} "
                f"| {row['pairwise_ties']} | {row['added_unacceptable_relative_to_sol']} "
                f"| {row['billed_cost_per_acceptable_final_outcome_usd']!r} "
                f"| {row['estimated_cost_per_acceptable_final_outcome_usd']!r} "
                f"| {row['guard_regressions']} | {row['claim_audit_regressions']} |"
            )
        lines.extend(["", "Pairwise detail:", ""])
        for name, values in scoring["pairwise"].items():
            lines.append(
                f"- {name}: wins={values['left_wins']}, losses={values['left_losses']}, ties={values['ties']}"
            )
        lines.append("")
    return "\n".join(lines)


def _visible_context(case: Mapping[str, Any]) -> dict[str, Any]:
    """Return only visible conversation context for blind review."""
    return previous_tool._visible_context(case)


def _blind_outcome(result: Mapping[str, Any] | None) -> str:
    """Render silence or any unavailable public outcome uniformly."""
    if result is None or result.get("execution_status") != "completed":
        return "[NO REPLY]"
    reply = result.get("final_public_reply")
    return reply if isinstance(reply, str) and reply else "[NO REPLY]"


def build_blind_rows(
    *,
    cases: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    arm_key: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Build V-Z outcomes without serialising the private mapping."""
    result_map = {
        (str(row["case_id"]), str(row["arm"])): row for row in results
    }
    label_to_arm = {label: arm for arm, label in arm_key.items()}
    return [
        {
            "case_id": case["case_id"],
            "visible_conversation_context": _visible_context(case),
            "outcomes": {
                label: _blind_outcome(
                    result_map.get((str(case["case_id"]), label_to_arm[label]))
                )
                for label in BLIND_LABELS
            },
        }
        for case in cases
    ]


def _render_visible_context(value: Mapping[str, Any]) -> list[str]:
    """Render exact visible context as concise review lines."""
    return previous_tool._markdown_visible_context(value)


def render_blind_review(rows: Sequence[Mapping[str, Any]]) -> str:
    """Render only scoring instructions, context, and V-Z public outcomes."""
    lines = [
        "# Blind Reply Review",
        "",
        "Rate each public outcome as `acceptable`, `minor`, or `unacceptable`, then record one preferred outcome (or `tie`) in the CSV.",
        "",
        "Treat an outcome as unacceptable if it answers a nearby proposition instead of the contributor's actual point; gets the actor, action, direction, polarity, date, or quantity wrong; confirms an unsupported attribution, translation, or allegation; invents a factual claim; fails to answer a direct supported question; asks an unnecessary clarification when the visible point is already clear; uses socially inappropriate wording; is conspicuously generic, canned, or unnatural; or stays silent when the frozen route clearly required a reply.",
        "",
        "Silence or inability to produce a publishable reply is shown as `[NO REPLY]`.",
        "",
    ]
    for row in rows:
        lines.extend([f"## {row['case_id']}", "", "Visible conversation context:", ""])
        lines.extend(_render_visible_context(row["visible_conversation_context"]))
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


def write_blind_scores_template(
    path: Path, cases: Sequence[Mapping[str, Any]]
) -> None:
    """Create once the exact five-outcome manual scoring CSV."""
    if path.exists():
        return
    buffer = io.StringIO(newline="")
    fields = [
        "case_id",
        "V_rating",
        "W_rating",
        "X_rating",
        "Y_rating",
        "Z_rating",
        "preferred_outcome",
        "notes",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for case in cases:
        writer.writerow({field: str(case["case_id"]) if field == "case_id" else "" for field in fields})
    atomic_text(path, buffer.getvalue())


def _validate_blind_outputs(
    *,
    rows: Sequence[Mapping[str, Any]],
    markdown: str,
    arm_key: Mapping[str, str],
) -> None:
    """Fail closed if a blind artifact reveals experiment identities or its key."""
    serialised = canonical_json_bytes(list(rows)).decode("utf-8") + "\n" + markdown
    forbidden = [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "grok-4.6",
        "grok-4.3",
        "reasoning_effort",
        "cost_in_usd",
        "effective_provider",
        "logical_provider",
        "guard failure",
        "arm_key",
    ]
    if any(value.casefold() in serialised.casefold() for value in forbidden):
        raise EvaluationError("blind review contains a forbidden experiment detail")
    for arm, label in arm_key.items():
        if f'"{arm}":"{label}"' in serialised.replace(" ", ""):
            raise EvaluationError("blind review contains the private arm mapping")


def generate_reports(
    *,
    output_dir: Path,
    manifest: Mapping[str, Any],
    cases: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    ledger: WriterRequestLedger,
    blocker: str | None,
    human_scoring: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write private operational and strictly blinded outputs."""
    comparison = build_comparison(
        manifest=manifest,
        cases=cases,
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
    arm_key = create_or_load_arm_key(
        private_path(output_dir, "arm_key.private.json")
    )
    blind_rows = build_blind_rows(cases=cases, results=results, arm_key=arm_key)
    blind_markdown = render_blind_review(blind_rows)
    _validate_blind_outputs(
        rows=blind_rows, markdown=blind_markdown, arm_key=arm_key
    )
    atomic_jsonl(private_path(output_dir, "blind_review.jsonl"), blind_rows)
    atomic_text(private_path(output_dir, "blind_review.md"), blind_markdown)
    write_blind_scores_template(
        private_path(output_dir, "blind_scores.csv"), cases
    )
    return comparison


def _guard_regression(candidate: Mapping[str, Any], baseline: Mapping[str, Any]) -> bool:
    """Return whether a candidate adds an existing deterministic guard failure."""
    candidate_guard = (
        _deterministic_rejection(candidate)
        or _cannot_compose(candidate)
        or bool(candidate.get("schema_failures"))
        or bool(candidate.get("provider_errors"))
    )
    baseline_guard = (
        _deterministic_rejection(baseline)
        or _cannot_compose(baseline)
        or bool(baseline.get("schema_failures"))
        or bool(baseline.get("provider_errors"))
    )
    return candidate_guard and not baseline_guard


def load_completed_scores(
    *,
    scores_path: Path,
    cases: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    arm_key: Mapping[str, str],
    comparison: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Decode completed five-outcome human scores without a model judge."""
    allowed = {"acceptable": 2, "minor": 1, "unacceptable": 0}
    with scores_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        expected_fields = {
            "case_id",
            "V_rating",
            "W_rating",
            "X_rating",
            "Y_rating",
            "Z_rating",
            "preferred_outcome",
            "notes",
        }
        if reader.fieldnames is None or set(reader.fieldnames) != expected_fields:
            raise EvaluationError("blind score CSV columns changed")
        score_rows = list(reader)
    case_ids = [str(case["case_id"]) for case in cases]
    if [str(row.get("case_id") or "") for row in score_rows] != case_ids:
        raise EvaluationError("blind score CSV cases or ordering changed")
    label_to_arm = {label: arm for arm, label in arm_key.items()}
    result_map = {
        (str(row["case_id"]), str(row["arm"])): row for row in results
    }
    if any(
        result_map.get((case_id, arm), {}).get("execution_status") != "completed"
        for case_id in case_ids
        for arm in ARMS
    ):
        raise EvaluationError("blind scores require every five-arm case to be completed")
    arm_rows: dict[str, dict[str, Any]] = {
        arm: {
            "acceptable": 0,
            "minor": 0,
            "unacceptable": 0,
            "preferred": 0,
            "pairwise_wins": 0,
            "pairwise_losses": 0,
            "pairwise_ties": 0,
            "added_unacceptable_relative_to_sol": 0,
            "guard_regressions": 0,
            "claim_audit_regressions": 0,
        }
        for arm in ARMS
    }
    ratings_by_case: dict[str, dict[str, str]] = {}
    preferred_ties = 0
    for case, score in zip(cases, score_rows):
        case_id = str(case["case_id"])
        ratings: dict[str, str] = {}
        for label in BLIND_LABELS:
            value = str(score.get(f"{label}_rating") or "").strip().casefold()
            if value not in allowed:
                raise EvaluationError(
                    f"blind score for {case_id} {label} must be acceptable, minor, or unacceptable"
                )
            arm = label_to_arm[label]
            ratings[arm] = value
            arm_rows[arm][value] += 1
        ratings_by_case[case_id] = ratings
        preferred = str(score.get("preferred_outcome") or "").strip().upper()
        if preferred == "TIE":
            preferred_ties += 1
        elif preferred in BLIND_LABELS:
            arm_rows[label_to_arm[preferred]]["preferred"] += 1
        else:
            raise EvaluationError(
                f"preferred outcome for {case_id} must be V, W, X, Y, Z, or tie"
            )

    pairwise: dict[str, dict[str, int | str]] = {}
    arm_names = list(ARMS)
    for left_index, left in enumerate(arm_names):
        for right in arm_names[left_index + 1 :]:
            left_wins = right_wins = ties = 0
            for case_id in case_ids:
                left_score = allowed[ratings_by_case[case_id][left]]
                right_score = allowed[ratings_by_case[case_id][right]]
                if left_score > right_score:
                    left_wins += 1
                elif right_score > left_score:
                    right_wins += 1
                else:
                    ties += 1
            arm_rows[left]["pairwise_wins"] += left_wins
            arm_rows[left]["pairwise_losses"] += right_wins
            arm_rows[left]["pairwise_ties"] += ties
            arm_rows[right]["pairwise_wins"] += right_wins
            arm_rows[right]["pairwise_losses"] += left_wins
            arm_rows[right]["pairwise_ties"] += ties
            pairwise[f"{left}_vs_{right}"] = {
                "left": left,
                "right": right,
                "left_wins": left_wins,
                "left_losses": right_wins,
                "ties": ties,
            }

    for case_id in case_ids:
        baseline_rating = ratings_by_case[case_id]["A"]
        baseline = result_map[(case_id, "A")]
        for arm in NON_BASELINE_ARMS:
            if (
                ratings_by_case[case_id][arm] == "unacceptable"
                and baseline_rating != "unacceptable"
            ):
                arm_rows[arm]["added_unacceptable_relative_to_sol"] += 1
            candidate = result_map[(case_id, arm)]
            arm_rows[arm]["guard_regressions"] += _guard_regression(
                candidate, baseline
            )
            arm_rows[arm]["claim_audit_regressions"] += (
                _claim_audit_nonpass(candidate) and not _claim_audit_nonpass(baseline)
            )

    comparison_arms = comparison.get("arms", {}) if isinstance(comparison, Mapping) else {}
    for arm in ARMS:
        acceptable = int(arm_rows[arm]["acceptable"])
        operational = comparison_arms.get(arm, {}) if isinstance(comparison_arms, Mapping) else {}
        billed = float(operational.get("variable_path_attributed_billed_cost_usd") or 0)
        estimated = float(
            operational.get("variable_path_attributed_estimated_cost_usd") or 0
        )
        arm_rows[arm]["billed_cost_per_acceptable_final_outcome_usd"] = (
            billed / acceptable if acceptable else None
        )
        arm_rows[arm]["estimated_cost_per_acceptable_final_outcome_usd"] = (
            estimated / acceptable if acceptable else None
        )
        arm_rows[arm]["latency"] = copy.deepcopy(
            operational.get("writer_request_latency")
        )
    return {
        "completed": True,
        "scored_case_count": len(cases),
        "rating_scale": ["acceptable", "minor", "unacceptable"],
        "arms": arm_rows,
        "pairwise": pairwise,
        "preferred_outcome_totals": {
            **{arm: arm_rows[arm]["preferred"] for arm in ARMS},
            "tie": preferred_ties,
        },
        "generated_at": utc_now(),
        "judge": "blind_human_only",
    }


def report_completed_scores(output_dir: Path) -> dict[str, Any]:
    """Decode the completed blind CSV and refresh the private report only."""
    manifest = read_json(private_path(output_dir, "manifest.json"))
    if not isinstance(manifest, dict):
        raise EvaluationError("writer experiment manifest is invalid")
    cases = read_jsonl(private_path(output_dir, "writer_cases.jsonl"))
    results = load_writer_results(private_path(output_dir, "writer_results.jsonl"))
    arm_key = create_or_load_arm_key(
        private_path(output_dir, "arm_key.private.json")
    )
    ledger = WriterRequestLedger(
        private_path(output_dir, "request_ledger.json"),
        case_set_sha256=str(manifest["writer_case_set_sha256"]),
        hard_limit_usd=float(manifest["global_cost_ceiling_usd"]),
    )
    blocker = (
        manifest.get("execution", {}).get("blocker")
        if isinstance(manifest.get("execution"), dict)
        else None
    )
    operational = build_comparison(
        manifest=manifest,
        cases=cases,
        results=results,
        ledger=ledger,
        blocker=str(blocker) if blocker else None,
    )
    scoring = load_completed_scores(
        scores_path=private_path(output_dir, "blind_scores.csv"),
        cases=cases,
        results=results,
        arm_key=arm_key,
        comparison=operational,
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
    """Return the narrow preparation, live-execution, and scoring parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--previous-output", type=Path, default=PREVIOUS_OUTPUT_ROOT)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--execute-live-models", action="store_true")
    parser.add_argument("--report-scores", action="store_true")
    parser.add_argument(
        "--cost-ceiling-usd", type=float, default=GLOBAL_COST_CEILING_USD
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Prepare offline by default; transmit only with the explicit live flag."""
    os.umask(0o077)
    args = _build_parser().parse_args(argv)
    project_dir = args.project_dir.expanduser().resolve()
    if project_dir != PROJECT_ROOT.resolve():
        raise EvaluationError("project directory must be this isolated evaluation worktree")
    if args.previous_output.expanduser().resolve() != PREVIOUS_OUTPUT_ROOT.resolve():
        raise EvaluationError("--previous-output must be the exact frozen experiment")
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
                    "comparison": str(
                        private_path(output_dir, "comparison.private.md")
                    ),
                },
                sort_keys=True,
            )
        )
        return 0
    if not 0 < args.cost_ceiling_usd <= GLOBAL_COST_CEILING_USD:
        raise EvaluationError("--cost-ceiling-usd must be in (0, 15.00]")
    manifest, cases, previous = prepare_experiment(
        project_dir=project_dir,
        output_dir=output_dir,
        previous_root=args.previous_output,
    )
    ledger = WriterRequestLedger(
        private_path(output_dir, "request_ledger.json"),
        case_set_sha256=str(manifest["writer_case_set_sha256"]),
        hard_limit_usd=args.cost_ceiling_usd,
    )
    if not args.execute_live_models:
        manifest = update_manifest_execution(
            output_dir,
            mode="prepared_only",
            blocker=None,
            ledger=ledger,
        )
        results = load_writer_results(
            private_path(output_dir, "writer_results.jsonl")
        )
        comparison = generate_reports(
            output_dir=output_dir,
            manifest=manifest,
            cases=cases,
            results=results,
            ledger=ledger,
            blocker=None,
        )
        print(
            json.dumps(
                {
                    "mode": "prepared_only",
                    "selected_cases": comparison["selected_case_count"],
                    "completed_cases": comparison["completed_case_count"],
                    "network_requests": 0,
                    "writer_case_set_sha256": comparison[
                        "writer_case_set_sha256"
                    ],
                },
                sort_keys=True,
            )
        )
        return 0

    keys = load_api_keys(args.env_file)
    blocker: str | None = None
    availability: dict[str, dict[str, dict[str, Any]]] | None = None
    try:
        results, ledger, blocker, availability = execute_experiment(
            execute_live_models=True,
            project_dir=project_dir,
            output_dir=output_dir,
            manifest=manifest,
            cases=cases,
            previous=previous,
            api_keys=keys,
            hard_limit_usd=args.cost_ceiling_usd,
        )
    except (EvaluationError, OSError, ValueError) as exc:
        blocker = f"{type(exc).__name__}: {exc}"
        results = load_writer_results(
            private_path(output_dir, "writer_results.jsonl")
        )
        ledger = WriterRequestLedger(
            private_path(output_dir, "request_ledger.json"),
            case_set_sha256=str(manifest["writer_case_set_sha256"]),
            hard_limit_usd=args.cost_ceiling_usd,
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
        results=results,
        ledger=ledger,
        blocker=blocker,
    )
    print(
        json.dumps(
            {
                "mode": "live_models",
                "selected_cases": comparison["selected_case_count"],
                "completed_cases": comparison["completed_case_count"],
                "provider_request_events": comparison[
                    "provider_request_event_count"
                ],
                "cache_hits": comparison["cache_hit_count"],
                "billed_cost_usd": comparison["cost"][
                    "total_billed_cost_usd"
                ],
                "estimated_cost_usd": comparison["cost"][
                    "total_estimated_cost_usd"
                ],
                "blocker": blocker,
                "comparison": str(
                    private_path(output_dir, "comparison.private.md")
                ),
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
