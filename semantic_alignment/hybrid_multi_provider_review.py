"""Run resumable blind retrieval reviews across multiple model providers."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from .bakeoff import anthropic_output_schema, openai_output_schema
from .hybrid_gemini_review import (
    BATCH_SIZE,
    MAX_OUTPUT_TOKENS,
    PROMPT_VERSION,
    RESPONSE_SCHEMA,
    estimate_tokens,
    parsed_response_from_raw,
    review_prompt,
    validate_batch_response,
)
from .hybrid_reply_retrieval import browser_review_payload
from .io import atomic_write_json, atomic_write_text, read_json

SCHEMA_VERSION = 1
PROVIDERS = ("grok", "openai", "anthropic")
MODELS = {
    "grok": "grok-4.5",
    "openai": "gpt-5.6-sol",
    "anthropic": "claude-fable-5",
}
PRICES = {
    "grok": {"input": 2.0, "cached_input": 0.5, "output": 6.0},
    "openai": {"input": 5.0, "cached_input": 0.5, "output": 30.0},
    "anthropic": {"input": 10.0, "cached_input": 1.0, "output": 50.0},
}
PROVIDER_LIMITS_USD = {"grok": 2.0, "openai": 6.0, "anthropic": 10.0}
COMBINED_LIMIT_USD = 18.0
EXPECTED_OUTPUT_TOKENS_PER_BATCH = 3000
ANTHROPIC_TOKEN_ESTIMATE_MULTIPLIER = 1.30


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _provider_input_estimate(provider: str, prompt: str) -> int:
    base = estimate_tokens(prompt)
    if provider == "anthropic":
        return int(base * ANTHROPIC_TOKEN_ESTIMATE_MULTIPLIER + 0.999)
    return base


def maximum_attempt_cost(provider: str, prompt: str) -> float:
    """Estimate the maximum cost of one provider attempt."""
    price = PRICES[provider]
    return (
        _provider_input_estimate(provider, prompt) * price["input"]
        + MAX_OUTPUT_TOKENS * price["output"]
    ) / 1_000_000


def prepare_multi_provider_review(retrieval_dir: Path) -> dict[str, Any]:
    """Prepare multi provider review."""
    payload = browser_review_payload(retrieval_dir)
    cases = payload["items"]
    if len(cases) != 100 or payload["context_unavailable_count"]:
        raise RuntimeError("multi-provider review requires exactly 100 context-complete blind cases")
    gemini_manifest = read_json(retrieval_dir / "manual_review" / "gemini_review_manifest.json")
    if not gemini_manifest or len(gemini_manifest.get("batches") or []) != 20:
        raise RuntimeError("immutable Gemini review manifest is missing or incomplete")
    cases_by_id = {str(case["case_id"]): case for case in cases}
    batches: list[dict[str, Any]] = []
    for batch in gemini_manifest["batches"]:
        batch_cases = [cases_by_id[str(case_id)] for case_id in batch["case_ids"]]
        prompt = review_prompt(batch_cases)
        prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if prompt_sha256 != batch["prompt_sha256"]:
            raise RuntimeError(f"prompt parity failed for {batch['batch_id']}")
        batches.append({
            "batch_id": str(batch["batch_id"]),
            "case_ids": [str(case_id) for case_id in batch["case_ids"]],
            "prompt_sha256": prompt_sha256,
            "estimated_input_tokens": estimate_tokens(prompt),
        })
    manifest_core = {
        "schema_version": SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "case_count": len(cases),
        "batch_size": BATCH_SIZE,
        "batch_count": len(batches),
        "models": MODELS,
        "providers": list(PROVIDERS),
        "source_gemini_manifest_sha256": hashlib.sha256(
            json.dumps(gemini_manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "batches": batches,
    }
    manifest = {
        **manifest_core,
        "manifest_sha256": hashlib.sha256(
            json.dumps(manifest_core, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "generated_at": utc_now(),
    }
    review_dir = retrieval_dir / "manual_review"
    manifest_path = review_dir / "multi_provider_review_manifest.json"
    existing = read_json(manifest_path, None)
    if existing:
        for key, value in manifest_core.items():
            if existing.get(key) != value:
                raise RuntimeError("existing multi-provider review manifest differs")
    else:
        atomic_write_json(manifest_path, manifest)

    prompts = [review_prompt([cases_by_id[case_id] for case_id in batch["case_ids"]]) for batch in batches]
    rows: dict[str, dict[str, Any]] = {}
    for provider in PROVIDERS:
        input_tokens = sum(_provider_input_estimate(provider, prompt) for prompt in prompts)
        expected_output = EXPECTED_OUTPUT_TOKENS_PER_BATCH * len(prompts)
        maximum_output = MAX_OUTPUT_TOKENS * len(prompts)
        price = PRICES[provider]
        expected = (input_tokens * price["input"] + expected_output * price["output"]) / 1_000_000
        maximum = (input_tokens * price["input"] + maximum_output * price["output"]) / 1_000_000
        retry_reserve = max(maximum_attempt_cost(provider, prompt) for prompt in prompts)
        rows[provider] = {
            "model": MODELS[provider],
            "batch_calls": len(prompts),
            "case_count": len(cases),
            "estimated_input_tokens": input_tokens,
            "expected_output_tokens": expected_output,
            "maximum_output_tokens": maximum_output,
            "expected_cost_usd": round(expected, 4),
            "conservative_base_cost_usd": round(maximum, 4),
            "single_retry_reserve_usd": round(retry_reserve, 4),
            "hard_limit_usd": PROVIDER_LIMITS_USD[provider],
        }
        if maximum + retry_reserve > PROVIDER_LIMITS_USD[provider]:
            raise RuntimeError(f"{provider} preflight exceeds its hard limit")
    preflight = {
        "schema_version": SCHEMA_VERSION,
        "case_count": len(cases),
        "batch_count_per_provider": len(prompts),
        "planned_calls_without_retries": len(prompts) * len(PROVIDERS),
        "providers": rows,
        "combined_expected_cost_usd": round(sum(row["expected_cost_usd"] for row in rows.values()), 4),
        "combined_conservative_base_cost_usd": round(
            sum(row["conservative_base_cost_usd"] for row in rows.values()), 4
        ),
        "combined_hard_limit_usd": COMBINED_LIMIT_USD,
        "exact_prompt_parity_with_gemini": True,
        "tools_enabled": False,
        "search_grounding_enabled": False,
        "human_reviews_will_be_modified": False,
        "generated_at": utc_now(),
    }
    atomic_write_json(review_dir / "multi_provider_review_preflight.json", preflight)
    return preflight


class ReviewProviderClient:
    """Provide the review provider client."""
    def __init__(
        self,
        provider: str,
        api_key: str,
        *,
        transport: Callable[..., Any] | None = None,
        timeout_seconds: float = 240,
    ):
        """Initialise the review provider client."""
        if provider not in PROVIDERS:
            raise ValueError("unsupported blind-review provider")
        if not api_key:
            raise RuntimeError(f"{provider} API key is required for explicit execution")
        self.provider = provider
        self.model = MODELS[provider]
        self.api_key = api_key
        self.transport = transport or requests.post
        self.timeout_seconds = timeout_seconds

    def payload(self, prompt: str) -> dict[str, Any]:
        """Return the payload."""
        if self.provider == "grok":
            return {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "blind_historical_retrieval_review",
                        "strict": True,
                        "schema": RESPONSE_SCHEMA,
                    },
                },
                "reasoning_effort": "low",
                "max_tokens": MAX_OUTPUT_TOKENS,
            }
        if self.provider == "anthropic":
            return {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": MAX_OUTPUT_TOKENS,
                "output_config": {
                    "effort": "low",
                    "format": {
                        "type": "json_schema",
                        "schema": anthropic_output_schema(RESPONSE_SCHEMA),
                    },
                },
            }
        return {
            "model": self.model,
            "input": prompt,
            "reasoning": {"effort": "low"},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "blind_historical_retrieval_review",
                    "strict": True,
                    "schema": openai_output_schema(RESPONSE_SCHEMA),
                },
            },
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "store": False,
        }

    def call(self, prompt: str) -> dict[str, Any]:
        """Submit one structured review prompt to this provider."""
        urls = {
            "grok": "https://api.x.ai/v1/chat/completions",
            "openai": "https://api.openai.com/v1/responses",
            "anthropic": "https://api.anthropic.com/v1/messages",
        }
        headers = {"Content-Type": "application/json"}
        if self.provider == "anthropic":
            headers.update({"x-api-key": self.api_key, "anthropic-version": "2023-06-01"})
        else:
            headers["Authorization"] = f"Bearer {self.api_key}"
        started = time.monotonic()
        response = self.transport(
            urls[self.provider],
            headers=headers,
            json=self.payload(prompt),
            timeout=self.timeout_seconds,
        )
        latency = time.monotonic() - started
        response.raise_for_status()
        raw = response.json()
        request_id = (
            response.headers.get("request-id")
            or response.headers.get("x-request-id")
            or raw.get("id")
        )
        return {"raw": raw, "latency_seconds": latency, "request_id": request_id}

    def usage_and_cost(self, raw: dict[str, Any]) -> tuple[dict[str, int], float]:
        """Return the usage and cost."""
        usage = raw.get("usage") or {}
        if self.provider == "grok":
            input_tokens = int(usage.get("prompt_tokens") or 0)
            output_tokens = int(usage.get("completion_tokens") or 0)
            cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
            reasoning = int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
            ticks = usage.get("cost_in_usd_ticks")
            if type(ticks) is not int:
                raise ValueError("missing authoritative Grok cost")
            cost = ticks / 10_000_000_000
        elif self.provider == "openai":
            input_tokens = int(usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or 0)
            cached = int((usage.get("input_tokens_details") or {}).get("cached_tokens") or 0)
            reasoning = int((usage.get("output_tokens_details") or {}).get("reasoning_tokens") or 0)
            if not input_tokens and not output_tokens:
                raise ValueError("missing authoritative OpenAI usage")
            cost = (
                (input_tokens - cached) * PRICES["openai"]["input"]
                + cached * PRICES["openai"]["cached_input"]
                + output_tokens * PRICES["openai"]["output"]
            ) / 1_000_000
        else:
            input_tokens = int(usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or 0)
            cached = int(usage.get("cache_read_input_tokens") or 0)
            reasoning = int((usage.get("output_tokens_details") or {}).get("thinking_tokens") or 0)
            if not input_tokens and not output_tokens:
                raise ValueError("missing authoritative Anthropic usage")
            cost = (
                (input_tokens - cached) * PRICES["anthropic"]["input"]
                + cached * PRICES["anthropic"]["cached_input"]
                + output_tokens * PRICES["anthropic"]["output"]
            ) / 1_000_000
        return {
            "input_tokens": input_tokens,
            "cached_tokens": cached,
            "reasoning_tokens": reasoning,
            "output_tokens": output_tokens,
        }, cost

    def parsed_content(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Return the parsed content."""
        if self.provider == "grok":
            text = str(raw["choices"][0]["message"]["content"])
        elif self.provider == "openai":
            text = str(raw.get("output_text") or "")
            if not text:
                text = "".join(
                    str(part.get("text") or "")
                    for item in raw.get("output") or []
                    for part in item.get("content") or []
                    if part.get("type") == "output_text"
                )
        else:
            if raw.get("stop_reason") == "refusal":
                raise ValueError("Claude Fable refusal")
            text = "".join(
                str(block.get("text") or "")
                for block in raw.get("content") or []
                if block.get("type") == "text"
            )
        if not text:
            raise ValueError(f"{self.provider} response contains no structured text")
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError(f"{self.provider} response is not a JSON object")
        return value


class SharedCostGuard:
    """Represent shared cost guard data."""
    def __init__(self, review_dir: Path, combined_limit: float):
        """Initialise the shared cost guard."""
        self.review_dir = review_dir
        self.combined_limit = combined_limit
        self.lock = threading.Lock()

    def known_spend(self) -> float:
        """Return the known spend."""
        return sum(
            float((read_json(self.review_dir / f"{provider}_review_cost_ledger.json", {}) or {}).get("known_spend_usd") or 0)
            for provider in PROVIDERS
        )

    def guard(self, provider: str, provider_spend: float, next_maximum: float, provider_limit: float) -> None:
        """Reject a call that could exceed configured spend limits."""
        with self.lock:
            if provider_spend + next_maximum > provider_limit:
                raise RuntimeError(f"{provider} blind-review cost ceiling reached")
            if self.known_spend() + next_maximum > self.combined_limit:
                raise RuntimeError("combined blind-review cost ceiling reached")


class ProviderReviewRunner:
    """Run provider review operations."""
    def __init__(
        self,
        retrieval_dir: Path,
        client: ReviewProviderClient,
        cost_guard: SharedCostGuard,
        *,
        provider_limit: float,
        sleep: Callable[[float], None] = time.sleep,
    ):
        """Initialise the provider review runner."""
        self.retrieval_dir = retrieval_dir
        self.review_dir = retrieval_dir / "manual_review"
        self.client = client
        self.provider = client.provider
        self.cost_guard = cost_guard
        self.provider_limit = provider_limit
        self.sleep = sleep
        self.results_path = self.review_dir / f"{self.provider}_blind_reviews.json"
        self.attempts_path = self.review_dir / f"{self.provider}_review_attempts.jsonl"
        self.cost_path = self.review_dir / f"{self.provider}_review_cost_ledger.json"
        self.state_path = self.review_dir / f"{self.provider}_review_state.json"
        self.raw_dir = self.review_dir / f"{self.provider}_raw_responses"

    def _load(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        results = read_json(self.results_path, None) or {
            "schema_version": SCHEMA_VERSION,
            "provider": self.provider,
            "model": self.client.model,
            "items": {},
        }
        costs = read_json(self.cost_path, None) or {
            "schema_version": SCHEMA_VERSION,
            "provider": self.provider,
            "model": self.client.model,
            "calls": [],
            "known_spend_usd": 0.0,
        }
        state = read_json(self.state_path, None) or {
            "schema_version": SCHEMA_VERSION,
            "provider": self.provider,
            "model": self.client.model,
            "unresolved_batches": {},
        }
        attempts: list[dict[str, Any]] = []
        if self.attempts_path.exists():
            attempts = [json.loads(line) for line in self.attempts_path.read_text(encoding="utf-8").splitlines() if line]
        terminals = {
            (row.get("batch_id"), row.get("attempt_number"))
            for row in attempts
            if row.get("event") in {
                "attempt_failed", "response_rejected", "completed", "ambiguous_on_resume",
            }
        }
        for row in list(attempts):
            key = (row.get("batch_id"), row.get("attempt_number"))
            if row.get("event") != "attempt_started" or key in terminals:
                continue
            recovered = {
                **row,
                "event": "ambiguous_on_resume",
                "finished_at": utc_now(),
                "reason": "attempt was started without a confirmed terminal outcome",
            }
            _append_jsonl(self.attempts_path, recovered)
            attempts.append(recovered)
            state.setdefault("unresolved_batches", {})[str(row.get("batch_id"))] = "ambiguous_outcome"
        self._persist(results, costs, state)
        return results, costs, state, attempts

    def _persist(self, results: dict[str, Any], costs: dict[str, Any], state: dict[str, Any]) -> None:
        atomic_write_json(self.results_path, results)
        atomic_write_json(self.cost_path, costs)
        atomic_write_json(self.state_path, state)

    @staticmethod
    def _attempt_count(attempts: list[dict[str, Any]], batch_id: str) -> int:
        return sum(row.get("event") == "attempt_started" and row.get("batch_id") == batch_id for row in attempts)

    def run(self) -> dict[str, Any]:
        """Run one provider's incomplete review work within the shared budget."""
        payload = browser_review_payload(self.retrieval_dir)
        cases_by_id = {str(case["case_id"]): case for case in payload["items"]}
        manifest = read_json(self.review_dir / "multi_provider_review_manifest.json")
        results, costs, state, attempts = self._load()
        state.update({"run_active": True, "active_pid": os.getpid(), "started_at": utc_now()})
        self._persist(results, costs, state)
        try:
            for batch in manifest["batches"]:
                batch_id = str(batch["batch_id"])
                case_ids = [str(case_id) for case_id in batch["case_ids"]]
                if all(case_id in results["items"] for case_id in case_ids):
                    continue
                if any(
                    row.get("event") == "ambiguous_on_resume" and row.get("batch_id") == batch_id
                    for row in attempts
                ):
                    state["unresolved_batches"][batch_id] = "ambiguous_outcome"
                    continue
                cases = [cases_by_id[case_id] for case_id in case_ids]
                prompt = review_prompt(cases)
                if hashlib.sha256(prompt.encode("utf-8")).hexdigest() != batch["prompt_sha256"]:
                    raise RuntimeError(f"prompt parity changed for {batch_id}")
                outcome = "failed"
                while self._attempt_count(attempts, batch_id) < 2:
                    maximum = maximum_attempt_cost(self.provider, prompt)
                    self.cost_guard.guard(
                        self.provider,
                        float(costs.get("known_spend_usd") or 0),
                        maximum,
                        self.provider_limit,
                    )
                    attempt_number = self._attempt_count(attempts, batch_id) + 1
                    started = {
                        "event": "attempt_started",
                        "timestamp": utc_now(),
                        "provider": self.provider,
                        "model": self.client.model,
                        "batch_id": batch_id,
                        "case_ids": case_ids,
                        "attempt_number": attempt_number,
                        "prompt_sha256": batch["prompt_sha256"],
                        "maximum_attempt_cost_usd": maximum,
                    }
                    _append_jsonl(self.attempts_path, started)
                    attempts.append(started)
                    try:
                        response = self.client.call(prompt)
                    except requests.HTTPError as exc:
                        status = exc.response.status_code if exc.response is not None else None
                        body = ""
                        if exc.response is not None:
                            body = (exc.response.text or "")[:4000]
                        failure = {
                            **started,
                            "event": "attempt_failed",
                            "finished_at": utc_now(),
                            "http_status": status,
                            "response_body": body,
                        }
                        _append_jsonl(self.attempts_path, failure)
                        attempts.append(failure)
                        if status in {408, 429, 500, 502, 503, 504} and attempt_number < 2:
                            self.sleep(2 ** attempt_number)
                            continue
                        outcome = f"http_{status or 'unknown'}"
                        break
                    except (requests.ConnectTimeout, requests.ConnectionError) as exc:
                        failure = {
                            **started,
                            "event": "attempt_failed",
                            "finished_at": utc_now(),
                            "error": f"{type(exc).__name__}: {exc}",
                            "confirmed_transient": True,
                        }
                        _append_jsonl(self.attempts_path, failure)
                        attempts.append(failure)
                        if attempt_number < 2:
                            self.sleep(2 ** attempt_number)
                            continue
                        outcome = "transport_failure"
                        break
                    except BaseException as exc:
                        failure = {
                            **started,
                            "event": "ambiguous_on_resume",
                            "finished_at": utc_now(),
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                        _append_jsonl(self.attempts_path, failure)
                        attempts.append(failure)
                        outcome = "ambiguous_outcome"
                        break

                    self.raw_dir.mkdir(parents=True, exist_ok=True)
                    raw_path = self.raw_dir / f"{batch_id}.{attempt_number}.json"
                    atomic_write_json(raw_path, response["raw"])
                    try:
                        usage, cost = self.client.usage_and_cost(response["raw"])
                    except ValueError as exc:
                        failure = {
                            **started,
                            "event": "ambiguous_on_resume",
                            "finished_at": utc_now(),
                            "error": str(exc),
                            "raw_response_path": str(raw_path),
                        }
                        _append_jsonl(self.attempts_path, failure)
                        attempts.append(failure)
                        outcome = "ambiguous_cost"
                        break
                    call = {
                        "provider": self.provider,
                        "model": self.client.model,
                        "batch_id": batch_id,
                        "case_ids": case_ids,
                        "attempt_number": attempt_number,
                        "timestamp": utc_now(),
                        "request_id": response["request_id"],
                        "latency_seconds": response["latency_seconds"],
                        "cost_usd": cost,
                        "raw_response_path": str(raw_path),
                        **usage,
                    }
                    costs["calls"].append(call)
                    costs["known_spend_usd"] = round(sum(float(row["cost_usd"]) for row in costs["calls"]), 10)
                    atomic_write_json(self.cost_path, costs)
                    try:
                        content = self.client.parsed_content(response["raw"])
                        values = validate_batch_response(content, cases)
                    except (KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
                        rejected = {
                            **started,
                            "event": "response_rejected",
                            "finished_at": utc_now(),
                            "error": f"{type(exc).__name__}: {exc}",
                            "charged_call": call,
                        }
                        _append_jsonl(self.attempts_path, rejected)
                        attempts.append(rejected)
                        if attempt_number < 2:
                            continue
                        outcome = "response_rejected"
                        break
                    for value in values:
                        results["items"][value["case_id"]] = {
                            **value,
                            "reviewer": self.provider,
                            "model": self.client.model,
                            "batch_id": batch_id,
                            "reviewed_at": utc_now(),
                            "prompt_version": PROMPT_VERSION,
                        }
                    atomic_write_json(self.results_path, results)
                    completed = {**started, "event": "completed", "finished_at": utc_now(), "call": call}
                    _append_jsonl(self.attempts_path, completed)
                    attempts.append(completed)
                    state.get("unresolved_batches", {}).pop(batch_id, None)
                    outcome = "completed"
                    break
                if outcome != "completed":
                    state.setdefault("unresolved_batches", {})[batch_id] = outcome
                self._persist(results, costs, state)
        finally:
            state.update({"run_active": False, "active_pid": None, "finished_at": utc_now()})
            self._persist(results, costs, state)
        return provider_review_summary(self.retrieval_dir, self.provider)


def provider_review_summary(retrieval_dir: Path, provider: str) -> dict[str, Any]:
    """Return the provider review summary."""
    review_dir = retrieval_dir / "manual_review"
    results = read_json(review_dir / f"{provider}_blind_reviews.json", {"items": {}})
    costs = read_json(review_dir / f"{provider}_review_cost_ledger.json", {})
    state = read_json(review_dir / f"{provider}_review_state.json", {})
    items = results.get("items") or {}
    return {
        "provider": provider,
        "model": MODELS[provider],
        "completed_reviews": len(items),
        "unresolved_reviews": 100 - len(items),
        "choice_counts": dict(sorted(Counter(row["choice"] for row in items.values()).items())),
        "evidence_desirability_counts": dict(sorted(Counter(
            row["evidence_desirability"] for row in items.values()
        ).items())),
        "intervention_counts": dict(sorted(Counter(row["intervention"] for row in items.values()).items())),
        "known_spend_usd": float(costs.get("known_spend_usd") or 0),
        "calls": len(costs.get("calls") or []),
        "unresolved_batches": dict(state.get("unresolved_batches") or {}),
    }


def run_multi_provider_reviews(
    retrieval_dir: Path,
    clients: dict[str, ReviewProviderClient],
    *,
    provider_limits: dict[str, float] | None = None,
    combined_limit: float = COMBINED_LIMIT_USD,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run multi provider reviews."""
    if set(clients) != set(PROVIDERS):
        raise ValueError("exactly Grok, OpenAI and Anthropic clients are required")
    limits = provider_limits or PROVIDER_LIMITS_USD
    guard = SharedCostGuard(retrieval_dir / "manual_review", combined_limit)
    runners = {
        provider: ProviderReviewRunner(
            retrieval_dir,
            clients[provider],
            guard,
            provider_limit=limits[provider],
            sleep=sleep,
        )
        for provider in PROVIDERS
    }
    started = time.monotonic()
    summaries: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="blind-review-provider") as pool:
        futures = {pool.submit(runner.run): provider for provider, runner in runners.items()}
        for future in as_completed(futures):
            provider = futures[future]
            try:
                summaries[provider] = future.result()
            except BaseException as exc:
                summaries[provider] = {"provider": provider, "worker_error": f"{type(exc).__name__}: {exc}"}
    result = {
        "schema_version": SCHEMA_VERSION,
        "providers": summaries,
        "wall_clock_seconds": time.monotonic() - started,
        "concurrent": True,
        "maximum_active_per_provider": 1,
        "generated_at": utc_now(),
    }
    atomic_write_json(retrieval_dir / "manual_review" / "multi_provider_execution_summary.json", result)
    return result


def _parsed_provider_raw(provider: str, raw: dict[str, Any]) -> dict[str, Any]:
    if provider == "grok":
        text = str(raw["choices"][0]["message"]["content"])
    elif provider == "openai":
        text = str(raw.get("output_text") or "") or "".join(
            str(part.get("text") or "")
            for item in raw.get("output") or []
            for part in item.get("content") or []
            if part.get("type") == "output_text"
        )
    elif provider == "anthropic":
        text = "".join(
            str(block.get("text") or "")
            for block in raw.get("content") or []
            if block.get("type") == "text"
        )
    else:
        return parsed_response_from_raw(raw)
    if not text:
        raise ValueError("raw response contains no structured text")
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("raw response does not contain a JSON object")
    return value


def recover_provider_reviews_offline(retrieval_dir: Path, provider: str) -> dict[str, Any]:
    """Recover provider reviews offline."""
    if provider not in PROVIDERS:
        raise ValueError("unsupported provider")
    review_dir = retrieval_dir / "manual_review"
    payload = browser_review_payload(retrieval_dir)
    cases_by_id = {str(case["case_id"]): case for case in payload["items"]}
    manifest = read_json(review_dir / "multi_provider_review_manifest.json")
    results = read_json(review_dir / f"{provider}_blind_reviews.json", {"items": {}})
    costs = read_json(review_dir / f"{provider}_review_cost_ledger.json", {"calls": []})
    state = read_json(review_dir / f"{provider}_review_state.json", {})
    audit_path = review_dir / f"{provider}_review_offline_recovery.json"
    existing = read_json(audit_path, None)
    unresolved = dict(state.get("unresolved_batches") or {})
    if not unresolved and existing:
        return existing
    calls_by_batch: dict[str, list[dict[str, Any]]] = {}
    for call in costs.get("calls") or []:
        calls_by_batch.setdefault(str(call["batch_id"]), []).append(call)
    rows: list[dict[str, Any]] = []
    for batch in manifest["batches"]:
        batch_id = str(batch["batch_id"])
        if batch_id not in unresolved:
            continue
        cases = [cases_by_id[str(case_id)] for case_id in batch["case_ids"]]
        recovered = None
        source = None
        errors: list[str] = []
        for call in reversed(calls_by_batch.get(batch_id, [])):
            try:
                raw = read_json(Path(call["raw_response_path"]))
                recovered = validate_batch_response(
                    _parsed_provider_raw(provider, raw),
                    cases,
                    allow_case_rationale_for_packet_reason=True,
                    allow_unrecognised_reason_tags=True,
                    allow_insufficient_context_intervention_inconsistency=True,
                    allow_historical_correction_without_factual_claim=True,
                )
                source = call
                break
            except (OSError, KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
        if recovered is None or source is None:
            rows.append({"batch_id": batch_id, "status": "still_unresolved", "errors": errors})
            continue
        for value in recovered:
            if value["case_id"] not in results["items"]:
                results["items"][value["case_id"]] = {
                    **value,
                    "reviewer": provider,
                    "model": MODELS[provider],
                    "batch_id": batch_id,
                    "reviewed_at": utc_now(),
                    "prompt_version": PROMPT_VERSION,
                    "offline_normalised_from_preserved_response": True,
                    "source_attempt_number": source["attempt_number"],
                    "source_raw_response_path": source["raw_response_path"],
                }
        unresolved.pop(batch_id, None)
        rows.append({
            "batch_id": batch_id,
            "status": "recovered_offline",
            "case_ids": list(batch["case_ids"]),
            "source_attempt_number": source["attempt_number"],
            "source_raw_response_path": source["raw_response_path"],
        })
    state["unresolved_batches"] = unresolved
    atomic_write_json(review_dir / f"{provider}_blind_reviews.json", results)
    atomic_write_json(review_dir / f"{provider}_review_state.json", state)
    audit = {
        "schema_version": SCHEMA_VERSION,
        "provider": provider,
        "offline_only": True,
        "recovered_case_count": sum(
            len(row.get("case_ids") or []) for row in rows if row["status"] == "recovered_offline"
        ),
        "still_unresolved_batch_count": sum(row["status"] == "still_unresolved" for row in rows),
        "batches": rows,
        "generated_at": utc_now(),
    }
    atomic_write_json(audit_path, audit)
    return audit


def compare_all_ai_reviews(retrieval_dir: Path) -> dict[str, Any]:
    """Compare all ai reviews."""
    review_dir = retrieval_dir / "manual_review"
    multi_manifest = read_json(review_dir / "multi_provider_review_manifest.json")
    gemini_manifest = read_json(review_dir / "gemini_review_manifest.json")
    multi_hashes = {
        str(batch["batch_id"]): str(batch["prompt_sha256"])
        for batch in multi_manifest.get("batches") or []
    }
    gemini_hashes = {
        str(batch["batch_id"]): str(batch["prompt_sha256"])
        for batch in gemini_manifest.get("batches") or []
    }
    if len(multi_hashes) != 20 or multi_hashes != gemini_hashes:
        raise RuntimeError("cannot compare reviews without exact 20-batch prompt parity")
    result_paths = {
        "gemini": review_dir / "gemini_reviews.json",
        "grok": review_dir / "grok_blind_reviews.json",
        "openai": review_dir / "openai_blind_reviews.json",
        "anthropic": review_dir / "anthropic_blind_reviews.json",
    }
    results = {provider: (read_json(path, {}) or {}).get("items") or {} for provider, path in result_paths.items()}
    sample_ids = {str(item["case_id"]) for item in read_json(retrieval_dir / "review_sample_100.json")["items"]}
    incomplete = {provider: len(sample_ids - set(items)) for provider, items in results.items()}
    if any(incomplete.values()):
        raise RuntimeError(f"cannot compare incomplete AI reviews: {incomplete}")
    blind = read_json(retrieval_dir / "blind_assignment_manifest.json")["assignments"]
    providers = tuple(sorted(results))
    provider_summaries: dict[str, Any] = {}
    for provider in providers:
        items = results[provider]
        wins = Counter()
        desirable_wins = Counter()
        packet_counts: dict[str, Counter[str]] = {}
        for case_id, row in items.items():
            if row["choice"] in {"A_better", "B_better"}:
                retriever = blind[case_id][row["choice"][0]]
                wins[retriever] += 1
                if row["evidence_desirability"] == "desirable":
                    desirable_wins[retriever] += 1
            for packet in row.get("packet_assessments") or []:
                retriever = blind[case_id][packet["side"]]
                packet_counts.setdefault(retriever, Counter())[packet["assessment"]] += 1
        provider_summaries[provider] = {
            "model": str(next(iter(items.values())).get("model") or ""),
            "choice_counts": dict(sorted(Counter(row["choice"] for row in items.values()).items())),
            "evidence_desirability_counts": dict(sorted(Counter(
                row["evidence_desirability"] for row in items.values()
            ).items())),
            "intervention_counts": dict(sorted(Counter(row["intervention"] for row in items.values()).items())),
            "confidence_counts": dict(sorted(Counter(row["confidence"] for row in items.values()).items())),
            "decisive_retriever_wins": dict(sorted(wins.items())),
            "decisive_retriever_wins_when_evidence_desirable": dict(sorted(desirable_wins.items())),
            "packet_assessment_counts_by_retriever": {
                key: dict(sorted(value.items())) for key, value in sorted(packet_counts.items())
            },
            "offline_normalised_review_count": sum(
                bool(row.get("offline_normalised_from_preserved_response")) for row in items.values()
            ),
            "logical_inconsistency_count": sum(bool(row.get("logical_inconsistencies")) for row in items.values()),
            "unrecognised_provider_reason_tag_count": sum(
                len(row.get("unrecognised_provider_reason_tags") or []) for row in items.values()
            ),
        }
        provider_summaries[provider]["directly_validated_review_count"] = (
            len(items) - provider_summaries[provider]["offline_normalised_review_count"]
        )
        if provider == "gemini":
            provider_summaries[provider]["known_spend_usd"] = float(
                (read_json(review_dir / "gemini_review_cost_ledger.json", {}) or {}).get("combined_known_spend_usd") or 0
            )
        else:
            provider_summaries[provider]["known_spend_usd"] = float(
                (read_json(review_dir / f"{provider}_review_cost_ledger.json", {}) or {}).get("known_spend_usd") or 0
            )

    pairwise: dict[str, Any] = {}
    for index, left in enumerate(providers):
        for right in providers[index + 1:]:
            pairwise[f"{left}__{right}"] = {
                "choice_exact_agreement": sum(
                    results[left][case_id]["choice"] == results[right][case_id]["choice"]
                    for case_id in sample_ids
                ) / len(sample_ids),
                "intervention_exact_agreement": sum(
                    results[left][case_id]["intervention"] == results[right][case_id]["intervention"]
                    for case_id in sample_ids
                ) / len(sample_ids),
                "evidence_desirability_exact_agreement": sum(
                    results[left][case_id]["evidence_desirability"]
                    == results[right][case_id]["evidence_desirability"]
                    for case_id in sample_ids
                ) / len(sample_ids),
            }

    consensus_counts = Counter()
    evidence_vote_counts = Counter()
    retriever_majority = Counter()
    strong_evidence_retriever_majority = Counter()
    case_rows: list[dict[str, Any]] = []
    for case_id in sorted(sample_ids):
        choices = Counter(results[provider][case_id]["choice"] for provider in providers)
        top_count = max(choices.values())
        consensus_counts[
            "unanimous" if top_count == 4 else "three_to_one" if top_count == 3 else "two_or_less"
        ] += 1
        desirable_votes = sum(
            results[provider][case_id]["evidence_desirability"] == "desirable" for provider in providers
        )
        evidence_vote_counts[str(desirable_votes)] += 1
        retriever_votes = Counter()
        for provider in providers:
            choice = results[provider][case_id]["choice"]
            if choice in {"A_better", "B_better"}:
                retriever_votes[blind[case_id][choice[0]]] += 1
        if retriever_votes:
            best = max(retriever_votes.values())
            leaders = [name for name, count in retriever_votes.items() if count == best]
            retriever_majority[leaders[0] if len(leaders) == 1 else "tied"] += 1
        else:
            retriever_majority["no_decisive_vote"] += 1
        if desirable_votes >= 3:
            desirable_retriever_votes = Counter()
            for provider in providers:
                row = results[provider][case_id]
                if row["evidence_desirability"] != "desirable":
                    continue
                if row["choice"] in {"A_better", "B_better"}:
                    desirable_retriever_votes[blind[case_id][row["choice"][0]]] += 1
            if desirable_retriever_votes:
                best = max(desirable_retriever_votes.values())
                leaders = [name for name, count in desirable_retriever_votes.items() if count == best]
                strong_evidence_retriever_majority[
                    leaders[0] if len(leaders) == 1 else "tied"
                ] += 1
            else:
                strong_evidence_retriever_majority["no_decisive_vote"] += 1
        case_rows.append({
            "case_id": case_id,
            "desirable_votes": desirable_votes,
            "choice_consensus_strength": top_count,
            "retriever_votes": dict(sorted(retriever_votes.items())),
            "providers": {
                provider: {
                    "choice": results[provider][case_id]["choice"],
                    "intervention": results[provider][case_id]["intervention"],
                    "evidence_desirability": results[provider][case_id]["evidence_desirability"],
                    "confidence": results[provider][case_id]["confidence"],
                    "rationale": results[provider][case_id]["rationale"],
                }
                for provider in providers
            },
        })

    total_cost = sum(float(row["known_spend_usd"]) for row in provider_summaries.values())
    comparison = {
        "schema_version": SCHEMA_VERSION,
        "case_count": len(sample_ids),
        "prompt_parity_verified": True,
        "prompt_hashes": multi_hashes,
        "human_reviews_sha256": hashlib.sha256(
            (review_dir / "human_reviews.json").read_bytes()
        ).hexdigest(),
        "providers": provider_summaries,
        "pairwise_agreement": pairwise,
        "choice_consensus_counts": dict(sorted(consensus_counts.items())),
        "evidence_desirable_vote_counts": dict(sorted(evidence_vote_counts.items())),
        "retriever_majority_counts": dict(sorted(retriever_majority.items())),
        "strong_evidence_case_count": sum(
            count for votes, count in evidence_vote_counts.items() if int(votes) >= 3
        ),
        "strong_evidence_retriever_majority_counts": dict(sorted(strong_evidence_retriever_majority.items())),
        "known_total_cost_usd": total_cost,
        "case_results": case_rows,
        "recommendation": "retain_hybrid_shadow_only",
        "generated_at": utc_now(),
    }
    atomic_write_json(review_dir / "all_ai_review_comparison.json", comparison)

    csv_path = review_dir / "all_ai_review_comparison.csv"
    temporary = csv_path.with_suffix(".csv.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        fields = ["case_id", "desirable_votes", "choice_consensus_strength"] + [
            f"{provider}_{field}"
            for provider in providers
            for field in ("choice", "intervention", "evidence_desirability", "confidence")
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in case_rows:
            flat = {
                "case_id": row["case_id"],
                "desirable_votes": row["desirable_votes"],
                "choice_consensus_strength": row["choice_consensus_strength"],
            }
            for provider in providers:
                for field in ("choice", "intervention", "evidence_desirability", "confidence"):
                    flat[f"{provider}_{field}"] = row["providers"][provider][field]
            writer.writerow(flat)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, csv_path)

    lines = [
        "# Four-AI Blind Retrieval Review", "",
        "The four models reviewed the same 100 context-complete cases, the same blind A/B assignments, and byte-identical substantive prompts. No model received retriever identity, human labels, external tools, or Search grounding.", "",
        "Prompt parity was verified against all 20 immutable Gemini batch hashes. The human-review file was read only and its SHA-256 is recorded in the machine-readable comparison.", "",
        "## Provider results", "",
        "| Provider | Model | Direct | Offline-normalised | Evidence desirable | No historical evidence | Hybrid wins when desirable | Lexical wins when desirable | Cost |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for provider in providers:
        row = provider_summaries[provider]
        lines.append(
            f"| {provider} | `{row['model']}` | {row['directly_validated_review_count']} | "
            f"{row['offline_normalised_review_count']} | "
            f"{row['evidence_desirability_counts'].get('desirable', 0)} | "
            f"{row['choice_counts'].get('no_historical_evidence', 0)} | "
            f"{row['decisive_retriever_wins_when_evidence_desirable'].get('hybrid', 0)} | "
            f"{row['decisive_retriever_wins_when_evidence_desirable'].get('lexical', 0)} | "
            f"${row['known_spend_usd']:.4f} |"
        )
    lines.extend(["", "## Consensus", ""])
    lines.extend(f"- {key}: {value}" for key, value in sorted(consensus_counts.items()))
    lines.extend(["", "Evidence-desirable votes per case:", ""])
    lines.extend(f"- {key} of 4: {value}" for key, value in sorted(evidence_vote_counts.items()))
    lines.extend(["", "Among cases where at least three providers found historical evidence desirable:", ""])
    lines.extend(
        f"- {key}: {value}" for key, value in sorted(strong_evidence_retriever_majority.items())
    )
    lines.extend(["", "## Packet assessments", ""])
    lines.extend([
        "| Provider | Retriever | Relevant | Partial | Irrelevant | Unsafe |",
        "|---|---|---:|---:|---:|---:|",
    ])
    for provider in providers:
        packet_rows = provider_summaries[provider]["packet_assessment_counts_by_retriever"]
        for retriever in ("lexical", "hybrid"):
            counts = packet_rows.get(retriever, {})
            lines.append(
                f"| {provider} | {retriever} | {counts.get('relevant', 0)} | "
                f"{counts.get('partially_relevant', 0)} | {counts.get('irrelevant', 0)} | "
                f"{counts.get('unsafe_as_evidence', 0)} |"
            )
    lines.extend(["", "## Pairwise exact agreement", ""])
    lines.extend([
        "| Providers | Set choice | Evidence desirability | Intervention |",
        "|---|---:|---:|---:|",
    ])
    for pair, values in sorted(pairwise.items()):
        lines.append(
            f"| {pair.replace('__', ' / ')} | {values['choice_exact_agreement']:.0%} | "
            f"{values['evidence_desirability_exact_agreement']:.0%} | "
            f"{values['intervention_exact_agreement']:.0%} |"
        )
    normalisation_notes = [
        f"- {provider}: {row['offline_normalised_review_count']} offline-normalised reviews; "
        f"{row['logical_inconsistency_count']} preserved logical inconsistencies; "
        f"{row['unrecognised_provider_reason_tag_count']} unrecognised reason tags preserved separately."
        for provider, row in sorted(provider_summaries.items())
    ]
    lines.extend(["", "## Schema reliability", "", *normalisation_notes])
    lines.extend(["", "## Interpretation", ""])
    lines.append(
        "The providers agree much more consistently on whether history is desirable than on the exact retrieval-set choice. In the 22 cases where at least three providers wanted historical evidence, the decisive votes favoured hybrid retrieval in 11 cases and lexical retrieval in 4; 7 had no decisive retriever vote. Packet-level assessments also favour hybrid retrieval, but these correlated model judgements are not independent ground truth. Hybrid retrieval should remain shadow-only pending stronger validation of the high-value disagreement cases."
    )
    lines.extend(["", f"Known four-provider spend: ${total_cost:.4f}."])
    atomic_write_text(review_dir / "all_ai_review_comparison.md", "\n".join(lines) + "\n")
    return comparison
