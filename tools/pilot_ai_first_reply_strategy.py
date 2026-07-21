#!/usr/bin/env python3
"""Run a bounded, non-posting xAI pilot of the AI-first reply pipeline.

The runner imports the production reply pipeline but never imports the bot
or any X client. It can contact only the configured xAI API host, writes only
inside its output directory, records authoritative provider costs, and never
retries an ambiguous request.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from reply_evidence import EvidenceRepository
from reply_strategy import (
    STRATEGY_VERSION,
    _reviewer_prompts,
    reviewer_schema,
    run_reply_pipeline,
    split_reply_sentences,
    validate_proposer,
    validate_reviewer,
)


SCHEMA_VERSION = 2
PILOT_VERSION = "ai-first-provider-pilot-v13"
FIXTURE_CURRENT_DATE = "2026-07-20"
USD_TICKS_PER_DOLLAR = 10_000_000_000
XAI_HOST = "api.x.ai"
DEFAULT_XAI_BASE = "https://api.x.ai/v1"
REASONING_OUTPUT_MULTIPLIER = 16
REVIEW_CHALLENGE_IDS = {
    "burnham-unrelated-wall",
    "east-west-direction-reversed",
    "unusual-allegation-verb",
    "unicode-fabricated-quotation",
    "wrong-actor",
    "wrong-relationship",
    "wrong-date",
    "wrong-quantity",
}


class PilotError(RuntimeError):
    """The isolated pilot cannot continue safely."""


class CostLimitReached(PilotError):
    """The next request would exceed the confirmed pilot ceiling."""


class RateLimitReached(PilotError):
    """The bounded rate-limit retry allowance was exhausted."""


class ServerErrorReached(PilotError):
    """The bounded provider-server-error retry allowance was exhausted."""


class DefiniteHTTPError(PilotError):
    """The provider returned a definite non-retryable HTTP error response."""


def utc_now() -> str:
    """Return a UTC timestamp suitable for durable records."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    """Return the SHA-256 of bytes."""
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    """Return a file SHA-256 without changing the file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    """Write deterministic JSON atomically and durably."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def atomic_text(path: Path, value: str) -> None:
    """Write text atomically and durably."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def read_json(path: Path) -> Any:
    """Read a JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))


def validate_xai_base(value: str) -> str:
    """Allow only xAI's HTTPS API host."""
    base = value.rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or parsed.hostname != XAI_HOST or parsed.path != "/v1":
        raise PilotError("pilot endpoint must be exactly https://api.x.ai/v1")
    if parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment:
        raise PilotError("pilot endpoint contains unsupported URL components")
    return base


def load_cases(
    project_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Load fixed ordinary, reviewer-challenge and forced-revision cases."""
    end_to_end_path = project_dir / "tests/fixtures/ai_first_reply_provider_pilot_cases.json"
    adversarial_path = project_dir / "tests/fixtures/ai_first_reply_adversarial_cases.json"
    revision_path = project_dir / "tests/fixtures/ai_first_reply_provider_revision_cases.json"
    end_to_end = read_json(end_to_end_path)
    adversarial = [row for row in read_json(adversarial_path) if row["case_id"] in REVIEW_CHALLENGE_IDS]
    revision = read_json(revision_path)
    if not isinstance(end_to_end, list) or len(end_to_end) != 10:
        raise PilotError("provider pilot fixture must contain exactly ten end-to-end cases")
    if len(adversarial) != len(REVIEW_CHALLENGE_IDS):
        raise PilotError("provider pilot reviewer challenge set is incomplete")
    if not isinstance(revision, list) or len(revision) != 2:
        raise PilotError("provider pilot must contain exactly two forced-revision cases")
    ids = [str(row.get("case_id") or "") for row in end_to_end]
    revision_ids = [str(row.get("case_id") or "") for row in revision]
    if (
        len(ids + revision_ids) != len(set(ids + revision_ids))
        or any(not value for value in ids + revision_ids)
    ):
        raise PilotError("provider pilot case IDs must be non-empty and unique")
    for row in revision:
        validate_proposer(
            row.get("initial_proposer"),
            maximum_reply_length=270,
            maximum_claims=6,
        )
    return end_to_end, adversarial, revision


def strategy_config(model: str, corpus_path: Path) -> dict[str, Any]:
    """Return the reviewed production strategy limits for the pilot."""
    return {
        "enabled": True,
        "strategy_version": STRATEGY_VERSION,
        "proposer_model": model,
        "reviewer_model": model,
        "evidence_model": model,
        "research_corpus_path": str(corpus_path),
        "maximum_model_calls": 6,
        "proposer_timeout_seconds": 60,
        "evidence_timeout_seconds": 60,
        "reviewer_timeout_seconds": 60,
        "proposer_max_output_tokens": 900,
        "evidence_max_output_tokens": 1800,
        "reviewer_max_output_tokens": 900,
        "maximum_revisions": 1,
        "maximum_invalid_response_retries": 1,
        "maximum_claims": 6,
        "maximum_evidence_packets_per_claim": 6,
        "maximum_evidence_passages_per_claim": 24,
        "maximum_reply_sentences": 2,
        "fail_closed": True,
    }


def fetch_model_metadata(
    *, api_key: str, base_url: str, model: str, get: Callable[..., Any] = requests.get
) -> dict[str, Any]:
    """Fetch and retain only the selected authenticated xAI model metadata."""
    response = get(
        f"{base_url}/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    response.raise_for_status()
    document = response.json()
    rows = document.get("data") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        raise PilotError("xAI model metadata response is invalid")
    selected = next((row for row in rows if isinstance(row, dict) and row.get("id") == model), None)
    if selected is None:
        raise PilotError(f"configured model is unavailable: {model}")
    required = (
        "prompt_text_token_price",
        "cached_prompt_text_token_price",
        "completion_text_token_price",
    )
    if any(type(selected.get(key)) is not int or selected[key] <= 0 for key in required):
        raise PilotError("xAI model metadata lacks authoritative positive token prices")
    return {
        "model": model,
        "retrieved_at": utc_now(),
        "usd_ticks_per_dollar": USD_TICKS_PER_DOLLAR,
        **{key: selected[key] for key in required},
    }


class PilotLedger:
    """Persist billed operations and conservative ambiguous exposure."""

    def __init__(
        self,
        path: Path,
        *,
        model: str,
        hard_limit_usd: float,
        run_version: str = PILOT_VERSION,
    ) -> None:
        """Open or create the identity-bound cost ledger."""
        self.path = path
        if path.exists():
            self.data = read_json(path)
            if (
                self.data.get("model") != model
                or self.data.get("hard_limit_usd") != hard_limit_usd
                or self.data.get("pilot_version") != run_version
            ):
                raise PilotError("existing pilot ledger model, cost limit or run version differs")
        else:
            self.data = {
                "schema_version": SCHEMA_VERSION,
                "pilot_version": run_version,
                "model": model,
                "hard_limit_usd": hard_limit_usd,
                "usd_ticks_per_dollar": USD_TICKS_PER_DOLLAR,
                "status": "resumable",
                "blocked": False,
                "operations": [],
                "created_at": utc_now(),
            }
            self._save()
        if self.data.get("blocked"):
            raise PilotError(f"pilot cost ledger is blocked: {self.data.get('blocked_reason')}")

    def _save(self) -> None:
        known = sum(
            int(row.get("cost_in_usd_ticks") or 0)
            for row in self.data["operations"]
            if row.get("status") == "completed"
        )
        ambiguous = sum(
            int(row.get("maximum_possible_cost_ticks") or 0)
            for row in self.data["operations"]
            if row.get("status") == "ambiguous"
        )
        self.data.update({
            "known_cost_in_usd_ticks": known,
            "known_cost_usd": known / USD_TICKS_PER_DOLLAR,
            "ambiguous_exposure_in_usd_ticks": ambiguous,
            "ambiguous_exposure_usd": ambiguous / USD_TICKS_PER_DOLLAR,
            "combined_exposure_usd": (known + ambiguous) / USD_TICKS_PER_DOLLAR,
            "updated_at": utc_now(),
        })
        atomic_json(self.path, self.data)

    def operation(self, logical_call_id: str, request_hash: str) -> dict[str, Any] | None:
        """Return one identity-checked prior operation regardless of status."""
        rows = [row for row in self.data["operations"] if row.get("logical_call_id") == logical_call_id]
        if not rows:
            return None
        if len(rows) != 1 or rows[0].get("request_hash") != request_hash:
            raise PilotError(f"logical call identity changed on resume: {logical_call_id}")
        return rows[0]

    def completed(self, logical_call_id: str, request_hash: str) -> dict[str, Any] | None:
        """Return one completed operation and reject incomplete prior transmission."""
        row = self.operation(logical_call_id, request_hash)
        if row is None:
            return None
        if row.get("status") != "completed":
            raise PilotError(f"logical call has an incomplete or ambiguous prior attempt: {logical_call_id}")
        return row

    def prepare(
        self,
        *,
        logical_call_id: str,
        case_id: str,
        stage: str,
        request_hash: str,
        prompt_hash: str,
        maximum_possible_cost_ticks: int,
    ) -> dict[str, Any]:
        """Reserve one logical request under the hard cost ceiling."""
        limit_ticks = int(self.data["hard_limit_usd"] * USD_TICKS_PER_DOLLAR)
        current_ticks = int(round(float(self.data.get("combined_exposure_usd", 0)) * USD_TICKS_PER_DOLLAR))
        if current_ticks + maximum_possible_cost_ticks > limit_ticks:
            raise CostLimitReached(
                f"next request {logical_call_id} could exceed the ${self.data['hard_limit_usd']:.2f} ceiling"
            )
        row = {
            "logical_call_id": logical_call_id,
            "case_id": case_id,
            "stage": stage,
            "model": self.data["model"],
            "attempt_number": 1,
            "request_hash": request_hash,
            "prompt_hash": prompt_hash,
            "response_hash": None,
            "request_id": None,
            "input_tokens": None,
            "completion_tokens": None,
            "reasoning_tokens": None,
            "cost_in_usd_ticks": None,
            "cost_usd": None,
            "maximum_possible_cost_ticks": maximum_possible_cost_ticks,
            "maximum_possible_cost_usd": maximum_possible_cost_ticks / USD_TICKS_PER_DOLLAR,
            "latency_seconds": None,
            "status": "prepared",
            "prepared_at": utc_now(),
        }
        self.data["operations"].append(row)
        self._save()
        return row

    def sending(self, row: dict[str, Any]) -> None:
        """Durably mark a prepared operation as transmitted."""
        row.update({"status": "sending", "sending_at": utc_now()})
        self._save()

    def rate_limited(
        self,
        row: dict[str, Any],
        *,
        retry_after_seconds: float,
        response_hash: str,
    ) -> None:
        """Record a definite provider rate-limit refusal without blocking resume."""
        events = row.setdefault("rate_limit_events", [])
        events.append({
            "attempt_number": row["attempt_number"],
            "retry_after_seconds": retry_after_seconds,
            "response_hash": response_hash,
            "timestamp": utc_now(),
        })
        row.update({
            "status": "rate_limited",
            "last_rate_limited_at": utc_now(),
        })
        self._save()

    def retry_rate_limited(self, row: dict[str, Any]) -> None:
        """Prepare the same logical request for another post-429 attempt."""
        if row.get("status") != "rate_limited":
            raise PilotError("only a rate-limited operation can be retried")
        row.update({
            "status": "prepared",
            "attempt_number": int(row.get("attempt_number") or 1) + 1,
            "prepared_at": utc_now(),
        })
        self._save()

    def server_error(
        self,
        row: dict[str, Any],
        *,
        status_code: int,
        retry_after_seconds: float,
        response_hash: str,
    ) -> None:
        """Record a definite 5xx response without treating billing as ambiguous."""
        events = row.setdefault("server_error_events", [])
        events.append({
            "attempt_number": row["attempt_number"],
            "status_code": status_code,
            "retry_after_seconds": retry_after_seconds,
            "response_hash": response_hash,
            "timestamp": utc_now(),
        })
        row.update({
            "status": "server_error",
            "last_server_error_at": utc_now(),
        })
        self._save()

    def retry_server_error(self, row: dict[str, Any]) -> None:
        """Prepare the same logical request for another post-5xx attempt."""
        if row.get("status") != "server_error":
            raise PilotError("only a server-error operation can be retried")
        row.update({
            "status": "prepared",
            "attempt_number": int(row.get("attempt_number") or 1) + 1,
            "prepared_at": utc_now(),
        })
        self._save()

    def definite_http_error(
        self,
        row: dict[str, Any],
        *,
        status_code: int,
        response_hash: str,
    ) -> None:
        """Record a definite non-retryable HTTP rejection."""
        row.update({
            "status": "http_error",
            "http_status_code": status_code,
            "http_response_hash": response_hash,
            "http_error_at": utc_now(),
        })
        self._save()

    def complete(
        self,
        row: dict[str, Any],
        *,
        raw: dict[str, Any],
        latency_seconds: float,
    ) -> None:
        """Record an authoritative provider response and its billed usage."""
        usage = raw.get("usage")
        if not isinstance(usage, dict) or type(usage.get("cost_in_usd_ticks")) is not int:
            self.ambiguous(row, PilotError("provider response lacks authoritative cost metadata"))
            raise PilotError("provider response lacks authoritative cost metadata")
        prompt_details = usage.get("prompt_tokens_details") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        content = raw.get("choices", [{}])[0].get("message", {}).get("content")
        row.update({
            "status": "completed",
            "response_hash": sha256_bytes(json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")),
            "request_id": str(raw.get("id") or ""),
            "input_tokens": int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
            "cached_tokens": int(prompt_details.get("cached_tokens") or usage.get("cached_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
            "reasoning_tokens": int(completion_details.get("reasoning_tokens") or usage.get("reasoning_tokens") or 0),
            "cost_in_usd_ticks": usage["cost_in_usd_ticks"],
            "cost_usd": usage["cost_in_usd_ticks"] / USD_TICKS_PER_DOLLAR,
            "latency_seconds": latency_seconds,
            "completed_at": utc_now(),
        })
        self._save()

    def ambiguous(self, row: dict[str, Any], error: BaseException) -> None:
        """Block the run after an operation with uncertain billing outcome."""
        row.update({
            "status": "ambiguous",
            "error": f"{type(error).__name__}: {error}",
            "ambiguous_at": utc_now(),
        })
        self.data.update({
            "blocked": True,
            "status": "blocked_ambiguous_cost",
            "blocked_reason": f"ambiguous billed operation {row['logical_call_id']}",
        })
        self._save()


class PilotTransport:
    """Production-compatible xAI transport with isolated caching and cost guards."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model_metadata: dict[str, Any],
        ledger: PilotLedger,
        response_dir: Path,
        post: Callable[..., Any] = requests.post,
        sleep: Callable[[float], None] = time.sleep,
        maximum_rate_limit_retries: int = 0,
        maximum_server_error_retries: int = 0,
    ) -> None:
        """Initialise an allowlisted provider transport over one durable ledger."""
        self.api_key = api_key
        self.base_url = validate_xai_base(base_url)
        self.model_metadata = model_metadata
        self.ledger = ledger
        self.response_dir = response_dir
        self.post = post
        self.sleep = sleep
        if type(maximum_rate_limit_retries) is not int or not 0 <= maximum_rate_limit_retries <= 8:
            raise PilotError("maximum rate-limit retries must be an integer from zero to eight")
        if type(maximum_server_error_retries) is not int or not 0 <= maximum_server_error_retries <= 4:
            raise PilotError("maximum server-error retries must be an integer from zero to four")
        self.maximum_rate_limit_retries = maximum_rate_limit_retries
        self.maximum_server_error_retries = maximum_server_error_retries
        self.case_id = ""
        self.sequence = 0

    def set_case(self, case_id: str) -> None:
        """Set the current logical case and reset its call sequence."""
        self.case_id = case_id
        self.sequence = 0

    def __call__(
        self,
        *,
        stage: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
        timeout_seconds: int,
        max_output_tokens: int,
        media_context: dict[str, Any] | None,
    ) -> object:
        if not self.case_id:
            raise PilotError("pilot transport case identity is unset")
        if media_context:
            raise PilotError("provider pilot does not transmit media")
        if model != self.model_metadata["model"]:
            raise PilotError("pipeline model differs from authenticated pilot model")
        self.sequence += 1
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "max_tokens": max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": f"ai_reply_{stage}",
                    "strict": True,
                    "schema": response_schema,
                },
            },
        }
        request_bytes = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        request_hash = sha256_bytes(request_bytes)
        prompt_hash = sha256_bytes((system_prompt + "\n" + user_prompt).encode("utf-8"))
        logical_call_id = f"{self.case_id}:{self.sequence}:{stage}"
        cache_path = self.response_dir / f"{sha256_bytes(logical_call_id.encode('utf-8'))}.json"
        prior = self.ledger.operation(logical_call_id, request_hash)
        if prior is not None and prior.get("status") == "completed":
            if not cache_path.exists():
                raise PilotError(f"completed logical call lacks response cache: {logical_call_id}")
            cached = read_json(cache_path)
            if cached.get("request_hash") != request_hash:
                raise PilotError(f"cached request differs for {logical_call_id}")
            return cached["raw"]["choices"][0]["message"]["content"]

        if prior is not None and cache_path.exists():
            cached = read_json(cache_path)
            if (
                cached.get("logical_call_id") != logical_call_id
                or cached.get("request_hash") != request_hash
                or not isinstance(cached.get("raw"), dict)
            ):
                raise PilotError(f"recoverable response cache differs for {logical_call_id}")
            self.ledger.complete(
                prior,
                raw=cached["raw"],
                latency_seconds=float(cached.get("latency_seconds") or 0),
            )
            return cached["raw"]["choices"][0]["message"]["content"]

        if prior is not None and prior.get("status") == "sending":
            error = PilotError(f"logical call transmission outcome is ambiguous: {logical_call_id}")
            self.ledger.ambiguous(prior, error)
            raise error
        if prior is not None and prior.get("status") == "rate_limited":
            if self.maximum_rate_limit_retries == 0:
                raise RateLimitReached(f"logical call remains rate limited: {logical_call_id}")
            events = prior.get("rate_limit_events") or []
            retry_after = float(events[-1].get("retry_after_seconds") or 1) if events else 1.0
            self.sleep(min(60.0, max(0.0, retry_after)))
            self.ledger.retry_rate_limited(prior)
        if prior is not None and prior.get("status") == "server_error":
            if self.maximum_server_error_retries == 0:
                raise ServerErrorReached(f"logical call remains on provider server error: {logical_call_id}")
            events = prior.get("server_error_events") or []
            retry_after = float(events[-1].get("retry_after_seconds") or 1) if events else 1.0
            self.sleep(min(60.0, max(0.0, retry_after)))
            self.ledger.retry_server_error(prior)
        if prior is not None and prior.get("status") != "prepared":
            raise PilotError(f"logical call has unsupported prior status: {logical_call_id}")

        input_token_upper_bound = max(1, len(request_bytes))
        output_token_upper_bound = max_output_tokens * REASONING_OUTPUT_MULTIPLIER
        maximum_ticks = (
            input_token_upper_bound * self.model_metadata["prompt_text_token_price"]
            + output_token_upper_bound * self.model_metadata["completion_text_token_price"]
        )
        row = prior or self.ledger.prepare(
            logical_call_id=logical_call_id,
            case_id=self.case_id,
            stage=stage,
            request_hash=request_hash,
            prompt_hash=prompt_hash,
            maximum_possible_cost_ticks=maximum_ticks,
        )
        rate_limits_this_invocation = 0
        server_errors_this_invocation = 0
        while True:
            self.ledger.sending(row)
            started = time.monotonic()
            try:
                response = self.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=timeout_seconds,
                )
                latency = time.monotonic() - started
                raw = response.json()
                response_hash = sha256_bytes(
                    json.dumps(raw, ensure_ascii=False, sort_keys=True).encode("utf-8")
                )
                if response.status_code == 429:
                    headers = getattr(response, "headers", {}) or {}
                    retry_header = headers.get("Retry-After") if hasattr(headers, "get") else None
                    try:
                        retry_after = float(retry_header)
                    except (TypeError, ValueError):
                        retry_after = min(60.0, 5.0 * (2 ** rate_limits_this_invocation))
                    retry_after = min(60.0, max(0.0, retry_after))
                    self.ledger.rate_limited(
                        row,
                        retry_after_seconds=retry_after,
                        response_hash=response_hash,
                    )
                    rate_limits_this_invocation += 1
                    if rate_limits_this_invocation > self.maximum_rate_limit_retries:
                        raise RateLimitReached(
                            f"xAI remained rate limited after {rate_limits_this_invocation} response(s)"
                        )
                    self.sleep(retry_after)
                    self.ledger.retry_rate_limited(row)
                    continue
                if 500 <= response.status_code <= 599:
                    headers = getattr(response, "headers", {}) or {}
                    retry_header = headers.get("Retry-After") if hasattr(headers, "get") else None
                    try:
                        retry_after = float(retry_header)
                    except (TypeError, ValueError):
                        retry_after = min(60.0, 5.0 * (2 ** server_errors_this_invocation))
                    retry_after = min(60.0, max(0.0, retry_after))
                    self.ledger.server_error(
                        row,
                        status_code=response.status_code,
                        retry_after_seconds=retry_after,
                        response_hash=response_hash,
                    )
                    server_errors_this_invocation += 1
                    if server_errors_this_invocation > self.maximum_server_error_retries:
                        raise ServerErrorReached(
                            "xAI continued returning server errors after "
                            f"{server_errors_this_invocation} response(s)"
                        )
                    self.sleep(retry_after)
                    self.ledger.retry_server_error(row)
                    continue
                if response.status_code >= 400:
                    self.ledger.definite_http_error(
                        row,
                        status_code=response.status_code,
                        response_hash=response_hash,
                    )
                    raise DefiniteHTTPError(f"xAI returned HTTP {response.status_code}")
                if not isinstance(raw, dict):
                    raise PilotError("xAI response must be an object")
                atomic_json(cache_path, {
                    "logical_call_id": logical_call_id,
                    "request_hash": request_hash,
                    "received_at": utc_now(),
                    "latency_seconds": latency,
                    "raw": raw,
                })
                self.ledger.complete(row, raw=raw, latency_seconds=latency)
                content = raw.get("choices", [{}])[0].get("message", {}).get("content")
                if not isinstance(content, (str, dict)):
                    raise PilotError("xAI response lacks structured content")
                return content
            except (DefiniteHTTPError, RateLimitReached, ServerErrorReached):
                raise
            except BaseException as exc:
                if row.get("status") != "completed":
                    self.ledger.ambiguous(row, exc)
                raise


class InjectedInitialProposerTransport:
    """Inject one deterministic bad draft, then use the real provider transport."""

    def __init__(self, delegate: PilotTransport, initial_proposer: dict[str, Any]) -> None:
        """Wrap the provider transport with one deterministic initial proposal."""
        self.delegate = delegate
        self.initial_proposer = json.loads(json.dumps(initial_proposer))
        self.injected = False

    def __call__(self, **kwargs: Any) -> object:
        """Return the fixture only for the first proposer stage."""
        if kwargs.get("stage") == "proposer" and not self.injected:
            self.injected = True
            return json.loads(json.dumps(self.initial_proposer))
        return self.delegate(**kwargs)


def context_for_case(case: dict[str, Any]) -> dict[str, Any]:
    """Build an isolated production-schema context for a pilot case."""
    case_id = str(case["case_id"])
    return {
        "target_id": f"pilot-{case_id}",
        "thread_id": f"pilot-thread-{case_id}",
        "lane": "mention",
        "incoming_contribution": str(case["contribution"]),
        "quoted_post": None,
        "parent_thread": [],
        "clarification_request": case.get("clarification_request"),
        "current_date": FIXTURE_CURRENT_DATE,
    }


def grade_end_to_end(case: dict[str, Any], result: Any) -> tuple[bool, list[str]]:
    """Grade deterministic expectations without pretending to assess prose taste."""
    outcome = "approved" if result.reply is not None else "no_reply"
    failures: list[str] = []
    invalid_stages = [
        str(row.get("stage") or "unknown")
        for row in result.audit
        if row.get("status") == "invalid"
    ]
    if invalid_stages:
        failures.append(
            "invalid structured output at stage(s): " + ", ".join(invalid_stages)
        )
    if outcome not in case["expected_outcomes"]:
        failures.append(f"unexpected outcome {outcome}")
    reply = str(result.reply or "")
    metadata = getattr(result.reply, "pipeline_metadata", {}) if result.reply is not None else {}
    if outcome == "approved" and case["expected_modes"] and metadata.get("mode") not in case["expected_modes"]:
        failures.append(f"unexpected mode {metadata.get('mode')}")
    folded = reply.casefold()
    for alternatives in case.get("required_term_groups", []):
        if not any(str(term).casefold() in folded for term in alternatives):
            failures.append(f"missing required concept group {alternatives}")
    for phrase in case.get("forbidden_phrases", []):
        if str(phrase).casefold() in folded:
            failures.append(f"forbidden phrase repeated: {phrase}")
    return not failures, failures


def grade_forced_revision(case: dict[str, Any], result: Any) -> tuple[bool, list[str]]:
    """Require the complete reviewer-to-revision-to-fresh-review path."""
    passed, failures = grade_end_to_end(case, result)
    audit = list(result.audit)
    if result.revision_count != 1:
        failures.append(f"expected exactly one revision, observed {result.revision_count}")
    first_reviews = [
        row for row in audit
        if row.get("stage") == "reviewer" and row.get("status") == "completed"
    ]
    if len(first_reviews) != 1 or first_reviews[0].get("verdict") != "revise":
        failures.append("first fresh reviewer did not request the required revision")
    required_stages = {"revision_proposer", "revision_reviewer"}
    completed_stages = {
        str(row.get("stage"))
        for row in audit
        if row.get("status") == "completed"
    }
    missing = sorted(required_stages - completed_stages)
    if missing:
        failures.append("missing completed revision stage(s): " + ", ".join(missing))
    return passed and not failures, failures


def reviewer_proposer(case: dict[str, Any]) -> dict[str, Any]:
    """Represent an intentionally unsafe draft as untrusted proposer output."""
    contribution = str(case["contribution"])
    question = "?" in contribution
    proposed_reply = str(case["bad_reply"])
    first_sentence = split_reply_sentences(proposed_reply)
    return {
        "mode": "direct_factual_answer" if question else "opinion_or_principle",
        "interpretation": "Adversarial reviewer challenge",
        "proposed_reply": proposed_reply,
        "direct_factual_question_present": question,
        "requested_answer_type": "other" if question else "none",
        "direct_answer_text": first_sentence[0] if question and first_sentence else "",
        "factual_claims": [],
        "exact_thatcher_wording_used": False,
        "exact_thatcher_wording": "",
        "tone": "neutral",
        "confidence": "high",
        "no_reply_reason": "",
    }


def reviewer_detected_failure(case: dict[str, Any], review: dict[str, Any]) -> bool:
    """Return whether a reviewer safely refused the known-bad draft."""
    if review["verdict"] == "approve":
        return False
    failure = case["failure"]
    if failure == "topically_irrelevant":
        return review["topically_relevant"] is False
    if failure == "unsupported_allegation":
        return review["endorses_unsupported_allegation"] or review["contains_unsupported_factual_claims"]
    if failure == "fabricated_quotation":
        return review["quotation_attribution_correct"] is False
    if failure in {"wrong_actor", "wrong_relationship"}:
        return review["actor_action_relationship_correct"] is False or review["contains_unsupported_factual_claims"]
    if failure == "wrong_direction":
        return review["direction_polarity_correct"] is False or review["contains_unsupported_factual_claims"]
    if failure in {"wrong_date", "wrong_quantity"}:
        return review["dates_quantities_correct"] is False or review["contains_unsupported_factual_claims"]
    return True


def bounded_validate_response(
    request: Callable[[], object],
    validator: Callable[[object], Any],
    *,
    maximum_invalid_retries: int,
) -> tuple[Any, int]:
    """Validate a provider response with the same one-retry production policy."""
    for attempt in range(maximum_invalid_retries + 1):
        raw = request()
        try:
            return validator(raw), attempt
        except (json.JSONDecodeError, TypeError, ValueError):
            if attempt >= maximum_invalid_retries:
                raise
    raise AssertionError("bounded response validation loop did not return")


def run_pilot(
    *,
    project_dir: Path,
    output_dir: Path,
    model: str,
    base_url: str,
    api_key: str,
    hard_limit_usd: float,
    post: Callable[..., Any] = requests.post,
    get: Callable[..., Any] = requests.get,
) -> dict[str, Any]:
    """Execute the bounded end-to-end and independent-reviewer pilot."""
    if not api_key:
        raise PilotError("XAI_API_KEY is required with --execute-xai")
    base_url = validate_xai_base(base_url)
    end_to_end, adversarial, forced_revisions = load_cases(project_dir)
    corpus = project_dir / "semantic_alignment_research/quote_research_full_001"
    repository = EvidenceRepository(
        corpus,
        factual_evidence_path=project_dir / "reply_factual_evidence.json",
    )
    config = strategy_config(model, corpus)
    metadata = fetch_model_metadata(api_key=api_key, base_url=base_url, model=model, get=get)
    atomic_json(output_dir / "pricing/model_metadata.json", metadata)
    ledger = PilotLedger(output_dir / "cost_ledger.json", model=model, hard_limit_usd=hard_limit_usd)
    transport = PilotTransport(
        api_key=api_key,
        base_url=base_url,
        model_metadata=metadata,
        ledger=ledger,
        response_dir=output_dir / "raw_responses",
        post=post,
    )
    results: list[dict[str, Any]] = []
    started = time.monotonic()
    for case in end_to_end:
        transport.set_case(f"e2e-{case['case_id']}")
        case_started = time.monotonic()
        try:
            pipeline = run_reply_pipeline(
                context=context_for_case(case),
                config=config,
                repository=repository,
                transport=transport,
                maximum_reply_length=270,
                recent_replies=[],
                media_context=None,
            )
            passed, failures = grade_end_to_end(case, pipeline)
            results.append({
                "case_id": case["case_id"],
                "case_type": "end_to_end",
                "source_set": case["source_set"],
                "source_locator": case.get("source_locator"),
                "contribution": case["contribution"],
                "status": pipeline.status,
                "reason": pipeline.reason,
                "reply": str(pipeline.reply or ""),
                "mode": getattr(pipeline.reply, "pipeline_metadata", {}).get("mode") if pipeline.reply else "no_reply",
                "reviewer_verdict": getattr(pipeline.reply, "pipeline_metadata", {}).get("reviewer_verdict") if pipeline.reply else "not_approved",
                "model_call_count": pipeline.model_call_count,
                "revision_count": pipeline.revision_count,
                "audit": list(pipeline.audit),
                "passed": passed,
                "failures": failures,
                "latency_seconds": round(time.monotonic() - case_started, 3),
            })
        except CostLimitReached:
            raise
        except BaseException as exc:
            results.append({
                "case_id": case["case_id"],
                "case_type": "end_to_end",
                "source_set": case["source_set"],
                "status": "error",
                "passed": False,
                "failures": [f"{type(exc).__name__}: {exc}"],
                "latency_seconds": round(time.monotonic() - case_started, 3),
            })
            break

    for case in forced_revisions:
        transport.set_case(f"revision-{case['case_id']}")
        case_started = time.monotonic()
        provider_calls_before = len(ledger.data["operations"])
        injected_transport = InjectedInitialProposerTransport(
            transport,
            case["initial_proposer"],
        )
        try:
            pipeline = run_reply_pipeline(
                context=context_for_case(case),
                config=config,
                repository=repository,
                transport=injected_transport,
                maximum_reply_length=270,
                recent_replies=[],
                media_context=None,
            )
            passed, failures = grade_forced_revision(case, pipeline)
            results.append({
                "case_id": case["case_id"],
                "case_type": "forced_revision_end_to_end",
                "source_set": case["source_set"],
                "contribution": case["contribution"],
                "status": pipeline.status,
                "reason": pipeline.reason,
                "reply": str(pipeline.reply or ""),
                "mode": getattr(pipeline.reply, "pipeline_metadata", {}).get("mode") if pipeline.reply else "no_reply",
                "reviewer_verdict": getattr(pipeline.reply, "pipeline_metadata", {}).get("reviewer_verdict") if pipeline.reply else "not_approved",
                "model_call_count": pipeline.model_call_count,
                "provider_call_count": len(ledger.data["operations"]) - provider_calls_before,
                "revision_count": pipeline.revision_count,
                "initial_proposer_injected": injected_transport.injected,
                "audit": list(pipeline.audit),
                "passed": passed,
                "failures": failures,
                "latency_seconds": round(time.monotonic() - case_started, 3),
            })
        except CostLimitReached:
            raise
        except BaseException as exc:
            results.append({
                "case_id": case["case_id"],
                "case_type": "forced_revision_end_to_end",
                "source_set": case["source_set"],
                "status": "error",
                "passed": False,
                "failures": [f"{type(exc).__name__}: {exc}"],
                "latency_seconds": round(time.monotonic() - case_started, 3),
            })
            break

    for case in adversarial:
        transport.set_case(f"review-{case['case_id']}")
        case_started = time.monotonic()
        context = context_for_case(case)
        proposer = reviewer_proposer(case)
        system_prompt, user_prompt = _reviewer_prompts(context, proposer, [], None)
        try:
            review, invalid_response_retries = bounded_validate_response(
                lambda: transport(
                    stage="pilot_adversarial_reviewer",
                    model=model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_schema=reviewer_schema(config["maximum_claims"]),
                    timeout_seconds=config["reviewer_timeout_seconds"],
                    max_output_tokens=config["reviewer_max_output_tokens"],
                    media_context=None,
                ),
                lambda raw: validate_reviewer(
                    raw,
                    maximum_claims=config["maximum_claims"],
                    proposed_reply=proposer["proposed_reply"],
                ),
                maximum_invalid_retries=config["maximum_invalid_response_retries"],
            )
            passed = reviewer_detected_failure(case, review)
            results.append({
                "case_id": case["case_id"],
                "case_type": "adversarial_reviewer",
                "source_set": "adversarial_regression",
                "contribution": case["contribution"],
                "bad_reply": case["bad_reply"],
                "expected_failure": case["failure"],
                "reviewer_verdict": review["verdict"],
                "reviewer_summary": review["summary"],
                "reviewer_reasons": review["reasons"],
                "invalid_response_retries": invalid_response_retries,
                "passed": passed,
                "failures": [] if passed else ["reviewer did not safely identify the adversarial defect"],
                "latency_seconds": round(time.monotonic() - case_started, 3),
            })
        except CostLimitReached:
            raise
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            results.append({
                "case_id": case["case_id"],
                "case_type": "adversarial_reviewer",
                "source_set": "adversarial_regression",
                "status": "error",
                "passed": False,
                "failures": [f"{type(exc).__name__}: {exc}"],
                "latency_seconds": round(time.monotonic() - case_started, 3),
            })
            continue
        except BaseException as exc:
            results.append({
                "case_id": case["case_id"],
                "case_type": "adversarial_reviewer",
                "source_set": "adversarial_regression",
                "status": "error",
                "passed": False,
                "failures": [f"{type(exc).__name__}: {exc}"],
                "latency_seconds": round(time.monotonic() - case_started, 3),
            })
            break

    latencies = [float(row["latency_seconds"]) for row in results]
    call_rows = [row for row in ledger.data["operations"] if row.get("status") == "completed"]
    call_latencies = [float(row["latency_seconds"]) for row in call_rows]
    forced_revision_rows = [
        row for row in results if row.get("case_type") == "forced_revision_end_to_end"
    ]
    summary = {
        "schema_version": SCHEMA_VERSION,
        "pilot_version": PILOT_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "model": model,
        "endpoint": base_url,
        "tools_enabled": False,
        "search_enabled": False,
        "posting_enabled": False,
        "media_transmitted": False,
        "temperature": 0,
        "hard_limit_usd": hard_limit_usd,
        "started_at": ledger.data["created_at"],
        "completed_at": utc_now(),
        "wall_seconds": round(time.monotonic() - started, 3),
        "case_count": len(results),
        "case_pass_count": sum(bool(row.get("passed")) for row in results),
        "case_failure_count": sum(not bool(row.get("passed")) for row in results),
        "case_type_counts": dict(Counter(row["case_type"] for row in results)),
        "forced_revision_case_count": len(forced_revision_rows),
        "forced_revision_pass_count": sum(
            bool(row.get("passed")) for row in forced_revision_rows
        ),
        "model_call_count": len(call_rows),
        "model_call_stage_counts": dict(Counter(str(row["stage"]) for row in call_rows)),
        "known_cost_usd": ledger.data["known_cost_usd"],
        "ambiguous_exposure_usd": ledger.data["ambiguous_exposure_usd"],
        "combined_exposure_usd": ledger.data["combined_exposure_usd"],
        "input_tokens": sum(int(row.get("input_tokens") or 0) for row in call_rows),
        "cached_tokens": sum(int(row.get("cached_tokens") or 0) for row in call_rows),
        "completion_tokens": sum(int(row.get("completion_tokens") or 0) for row in call_rows),
        "reasoning_tokens": sum(int(row.get("reasoning_tokens") or 0) for row in call_rows),
        "call_latency_p50_seconds": statistics.median(call_latencies) if call_latencies else None,
        "call_latency_p95_seconds": percentile(call_latencies, 0.95),
        "call_latency_max_seconds": max(call_latencies) if call_latencies else None,
        "case_latency_p50_seconds": statistics.median(latencies) if latencies else None,
        "results": results,
    }
    atomic_json(output_dir / "pilot_results.json", summary)
    atomic_text(output_dir / "pilot_report.md", render_report(summary, metadata))
    return summary


def percentile(values: list[float], proportion: float) -> float | None:
    """Return a nearest-rank percentile."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, math.ceil(len(ordered) * proportion) - 1))]


def render_report(summary: dict[str, Any], metadata: dict[str, Any]) -> str:
    """Render a concise pilot report."""
    lines = [
        "# AI-first Reply Strategy Provider Pilot",
        "",
        "## Outcome",
        "",
        f"- Cases: {summary['case_pass_count']}/{summary['case_count']} passed",
        (
            "- Forced end-to-end revision cases: "
            f"{summary['forced_revision_pass_count']}/{summary['forced_revision_case_count']} passed"
        ),
        f"- Structured model calls: {summary['model_call_count']}",
        f"- Known provider cost: US${summary['known_cost_usd']:.6f}",
        f"- Ambiguous exposure: US${summary['ambiguous_exposure_usd']:.6f}",
        f"- Hard ceiling: US${summary['hard_limit_usd']:.2f}",
        f"- Model: `{summary['model']}`",
        f"- Endpoint: `{summary['endpoint']}`",
        "- X posting, media upload, search and tools: disabled",
        "",
        "## Provider Configuration",
        "",
        "- Separate fresh proposer and reviewer requests were used.",
        "- Temperature: 0",
        "- Strict typed JSON Schema response format: enabled",
        "- Retries after ambiguous transmission: disabled",
        f"- Input price: {metadata['prompt_text_token_price']} ticks/token",
        f"- Output price: {metadata['completion_text_token_price']} ticks/token",
        "",
        "## Performance",
        "",
        f"- Input tokens: {summary['input_tokens']}",
        f"- Cached tokens: {summary['cached_tokens']}",
        f"- Completion tokens: {summary['completion_tokens']}",
        f"- Reasoning tokens: {summary['reasoning_tokens']}",
        f"- Call latency p50: {summary['call_latency_p50_seconds']:.3f}s" if summary["call_latency_p50_seconds"] is not None else "- Call latency p50: n/a",
        f"- Call latency p95: {summary['call_latency_p95_seconds']:.3f}s" if summary["call_latency_p95_seconds"] is not None else "- Call latency p95: n/a",
        f"- Call latency maximum: {summary['call_latency_max_seconds']:.3f}s" if summary["call_latency_max_seconds"] is not None else "- Call latency maximum: n/a",
        "",
        "## Cases",
        "",
        "| Case | Type | Result | Mode/verdict | Calls | Reply or finding |",
        "|---|---|---|---|---:|---|",
    ]
    for row in summary["results"]:
        result = "PASS" if row.get("passed") else "FAIL"
        mode = row.get("mode") or row.get("reviewer_verdict") or row.get("status") or ""
        calls = row.get("model_call_count", 1 if row["case_type"] == "adversarial_reviewer" else 0)
        detail = row.get("reply") or row.get("reviewer_summary") or "; ".join(row.get("failures", [])) or row.get("reason", "")
        detail = str(detail).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| `{row['case_id']}` | {row['case_type']} | {result} | {mode} | {calls} | {detail} |")
    lines.extend([
        "",
        "## Verdict",
        "",
        "The pilot validates provider/schema compatibility only when every case passes and ambiguous exposure is zero.",
        "It does not authorise production activation; source-default activation remains disabled.",
        "",
        "PILOT PASSED" if summary["case_failure_count"] == 0 and summary["ambiguous_exposure_usd"] == 0 else "PILOT FAILED",
        "",
    ])
    return "\n".join(lines)


def write_manifest(project_dir: Path, output_dir: Path, model: str, hard_limit_usd: float) -> None:
    """Record immutable pilot inputs before a billed operation."""
    input_paths = [
        Path(__file__).resolve(),
        project_dir / "reply_strategy.py",
        project_dir / "reply_evidence.py",
        project_dir / "historical_context_formatter.py",
        project_dir / "semantic_alignment/quote_research_schema.py",
        project_dir / "reply_factual_evidence.json",
        project_dir / "tests/fixtures/ai_first_reply_provider_pilot_cases.json",
        project_dir / "tests/fixtures/ai_first_reply_provider_revision_cases.json",
        project_dir / "tests/fixtures/ai_first_reply_adversarial_cases.json",
        project_dir / "semantic_alignment_research/quote_research_full_001/corpus_manifest.json",
        project_dir / "semantic_alignment_research/quote_research_full_001/research_packets.json",
        project_dir / "semantic_alignment_research/quote_research_full_001/final_unresolved/final_research_status.json",
    ]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "pilot_version": PILOT_VERSION,
        "created_at": utc_now(),
        "project_dir": str(project_dir),
        "output_dir": str(output_dir),
        "model": model,
        "hard_limit_usd": hard_limit_usd,
        "fixture_current_date": FIXTURE_CURRENT_DATE,
        "network_allowlist": [XAI_HOST],
        "posting_enabled": False,
        "source_hashes": {str(path.relative_to(project_dir)): sha256_file(path) for path in input_paths},
    }
    manifest_path = output_dir / "run_manifest.json"
    if manifest_path.exists():
        existing = read_json(manifest_path)
        comparable = {key: value for key, value in manifest.items() if key != "created_at"}
        existing_comparable = {key: value for key, value in existing.items() if key != "created_at"}
        if existing_comparable != comparable:
            raise PilotError("pilot source inputs or immutable run configuration changed on resume")
        return
    atomic_json(manifest_path, manifest)


def main(argv: list[str] | None = None) -> int:
    """Run the explicit, cost-confirmed provider pilot."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, default=PROJECT_ROOT)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "semantic_alignment_research/ai_first_reply_strategy_001/provider_pilot_20260720_v12",
    )
    parser.add_argument("--model", default=os.getenv("XAI_MODEL", "grok-4.3"))
    parser.add_argument("--xai-base-url", default=os.getenv("XAI_API_BASE_URL", DEFAULT_XAI_BASE))
    parser.add_argument("--execute-xai", action="store_true")
    parser.add_argument("--confirm-cost-limit-usd", type=float)
    args = parser.parse_args(argv)
    project_dir = args.project_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if not args.execute_xai:
        raise PilotError("--execute-xai is required for the provider pilot")
    if args.confirm_cost_limit_usd is None or not 0 < args.confirm_cost_limit_usd <= 3.0:
        raise PilotError("--confirm-cost-limit-usd must be greater than zero and no more than 3.00")
    if output_dir == project_dir or project_dir not in output_dir.parents:
        raise PilotError("pilot output must be an isolated directory under the project")
    output_dir.mkdir(parents=True, exist_ok=True)
    write_manifest(project_dir, output_dir, args.model, args.confirm_cost_limit_usd)
    summary = run_pilot(
        project_dir=project_dir,
        output_dir=output_dir,
        model=args.model,
        base_url=args.xai_base_url,
        api_key=os.getenv("XAI_API_KEY", ""),
        hard_limit_usd=args.confirm_cost_limit_usd,
    )
    print(json.dumps({
        "cases": summary["case_count"],
        "passed": summary["case_pass_count"],
        "calls": summary["model_call_count"],
        "known_cost_usd": summary["known_cost_usd"],
        "report": str(output_dir / "pilot_report.md"),
    }, sort_keys=True))
    return 0 if summary["case_failure_count"] == 0 and summary["ambiguous_exposure_usd"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
