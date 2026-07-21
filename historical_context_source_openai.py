#!/usr/bin/env python3
"""Run a cost-bounded OpenAI web-search pilot for unresolved source evidence."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)

from historical_context_formatter import atomic_write_json, load_and_validate_corpus
from historical_context_source_gemini import (
    HARD_LIMIT_USD,
    recognised_source_authority,
    residual_queue,
    verify_grounding_source,
    verify_source,
)
from historical_context_source_openai_manifest import (
    OPENAI_RESEARCH_FILENAME,
    OPENAI_RESEARCH_POLICY_VERSION,
    OPENAI_RESEARCH_SCHEMA_VERSION,
    validate_openai_research_manifest,
)


MODEL = "gpt-5.4-mini-2026-03-17"
API_VERSION = "v1/responses"
PROMPT_VERSION = "historical-context-openai-forced-web-search-v1"
MAX_OUTPUT_TOKENS = 800
MAX_TOOL_CALLS = 1
MAX_BILLABLE_INPUT_TOKENS_GUARD = 64_000
MAX_REPORTED_SEARCH_CALLS_GUARD = 4
INPUT_USD_PER_MILLION = 0.75
OUTPUT_USD_PER_MILLION = 4.50
WEB_SEARCH_USD_PER_CALL = 0.01
# The documented charge is $0.01. Reserve three times that amount so a pricing
# or accounting discrepancy cannot threaten the project-wide hard ceiling.
WEB_SEARCH_COST_GUARD_USD = 0.03


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _estimated_tokens(value: str) -> int:
    return math.ceil(len(value.encode()) / 3)


def research_prompt(packet: dict[str, Any]) -> str:
    """Build a narrow source-finding prompt whose prose is never trusted."""
    return f"""Prompt version: {PROMPT_VERSION}
Use web search for this one historical quotation attributed to Margaret Thatcher.

Quotation: {json.dumps(packet['quote_text'], ensure_ascii=False)}
Verified wording in the research packet: {json.dumps(packet['verified_text'], ensure_ascii=False)}
Current wording classification: {packet['verification_status']}

Search the exact wording in quotation marks with Margaret Thatcher's name. If that
does not locate it, search the verified wording and one distinctive phrase. Prefer
primary transcripts, archives, Thatcher-authored books with precise locators, or
reputable contemporary reporting. Do not cite quotation aggregators, social media,
Wikipedia, search-result pages or invented URLs. Briefly state whether a credible
page was found. The calling program will ignore your conclusion and independently
fetch and verify every cited page.
"""


def maximum_call_cost(prompt: str) -> float:
    """Return the conservative cost reservation for one bounded request."""
    return (
        max(_estimated_tokens(prompt), MAX_BILLABLE_INPUT_TOKENS_GUARD)
        * INPUT_USD_PER_MILLION / 1_000_000
        + MAX_OUTPUT_TOKENS * OUTPUT_USD_PER_MILLION / 1_000_000
        + MAX_REPORTED_SEARCH_CALLS_GUARD * WEB_SEARCH_COST_GUARD_USD
    )


def actual_call_cost(input_tokens: int, output_tokens: int, search_calls: int) -> float:
    """Calculate known cost from authoritative response usage."""
    return (
        input_tokens * INPUT_USD_PER_MILLION / 1_000_000
        + output_tokens * OUTPUT_USD_PER_MILLION / 1_000_000
        + search_calls * WEB_SEARCH_USD_PER_CALL
    )


def build_preflight(
    packets: dict[str, dict[str, Any]],
    quote_ids: list[str],
    *,
    prior_known_spend_usd: float,
    prior_ambiguous_exposure_usd: float = 0.0,
) -> dict[str, Any]:
    """Describe exact pilot scope and worst-case exposure before any request."""
    maxima = [maximum_call_cost(research_prompt(packets[q])) for q in quote_ids]
    return {
        "schema_version": 1,
        "provider": "openai",
        "model": MODEL,
        "sdk_version": importlib.metadata.version("openai"),
        "api_version": API_VERSION,
        "prompt_version": PROMPT_VERSION,
        "pricing_version": "openai-pricing-2026-07-21",
        "tool": "web_search_preview",
        "tool_choice": "forced_web_search_preview",
        "maximum_tool_calls_per_request": MAX_TOOL_CALLS,
        "maximum_reported_search_calls_cost_guard": MAX_REPORTED_SEARCH_CALLS_GUARD,
        "maximum_billable_input_tokens_guard": MAX_BILLABLE_INPUT_TOKENS_GUARD,
        "maximum_output_tokens_per_request": MAX_OUTPUT_TOKENS,
        "queue_quote_ids": quote_ids,
        "queue_sha256": _sha256(("\n".join(quote_ids) + "\n").encode()),
        "prior_known_spend_usd": prior_known_spend_usd,
        "prior_ambiguous_exposure_usd": prior_ambiguous_exposure_usd,
        "maximum_single_call_exposure_usd": round(max(maxima, default=0.0), 8),
        "maximum_new_exposure_usd": round(sum(maxima), 8),
        "maximum_combined_exposure_usd": round(
            prior_known_spend_usd + prior_ambiguous_exposure_usd + sum(maxima), 8
        ),
        "hard_limit_usd": HARD_LIMIT_USD,
        "within_hard_limit": (
            prior_known_spend_usd + prior_ambiguous_exposure_usd + sum(maxima)
            <= HARD_LIMIT_USD
        ),
    }


def resumable_research_queue(
    audit: dict[str, Any], completed_or_failed_quote_ids: set[str]
) -> list[str]:
    """Retain prior work when attached sources shrink the current residual."""
    scope = set(audit["summary"]["no_reliable_source_quote_ids"])
    scope.update(completed_or_failed_quote_ids)
    unknown = scope - set(audit["items"])
    if unknown:
        raise RuntimeError("saved OpenAI source results contain unknown quote IDs")
    queue_audit = {
        **audit,
        "summary": {
            **audit["summary"],
            "no_reliable_source_quote_ids": sorted(scope),
        },
    }
    return residual_queue(queue_audit)


def extract_cited_sources(raw: dict[str, Any]) -> tuple[list[dict[str, str]], int]:
    """Extract cited URLs and count native web-search calls from a response."""
    found: list[dict[str, str]] = []
    search_calls = 0

    def walk(value: Any) -> None:
        nonlocal search_calls
        if isinstance(value, dict):
            if value.get("type") == "web_search_call":
                search_calls += 1
            if value.get("type") == "url_citation" and value.get("url"):
                found.append({
                    "url": str(value["url"]),
                    "title": str(value.get("title") or ""),
                })
            if value.get("type") in {"url", "web_search_result"} and value.get("url"):
                found.append({
                    "url": str(value["url"]),
                    "title": str(value.get("title") or ""),
                })
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(raw.get("output", []))
    unique: dict[str, dict[str, str]] = {}
    for source in found:
        unique.setdefault(source["url"], source)
    return list(unique.values()), search_calls


class OpenAIWebSearchClient:
    """Call the pinned Responses API model with mandatory bounded web search."""

    def __init__(self, api_key: str):
        """Configure a non-retrying Responses API client."""
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is unavailable")
        self.client = OpenAI(api_key=api_key, timeout=180.0, max_retries=0)

    def call(self, prompt: str) -> dict[str, Any]:
        """Run one mandatory web-search request and return saved response data."""
        started = time.monotonic()
        response = self.client.responses.create(
            model=MODEL,
            input=prompt,
            tools=[{
                "type": "web_search_preview",
                "search_context_size": "high",
                "user_location": {
                    "type": "approximate",
                    "country": "GB",
                    "timezone": "Europe/London",
                },
            }],
            tool_choice={"type": "web_search_preview"},
            max_tool_calls=MAX_TOOL_CALLS,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            reasoning={"effort": "low"},
            include=["web_search_call.action.sources"],
            store=False,
        )
        raw = response.model_dump(mode="json", exclude_none=True)
        sources, search_calls = extract_cited_sources(raw)
        usage = raw.get("usage") or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        if input_tokens < 1 or output_tokens < 1:
            raise RuntimeError("OpenAI response lacks authoritative token usage")
        return {
            "raw": raw,
            "sources": sources,
            "search_calls": search_calls,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "known_cost_usd": actual_call_cost(
                input_tokens, output_tokens, search_calls
            ),
            "request_id": str(raw.get("id") or ""),
            "latency_seconds": time.monotonic() - started,
        }


def revalidate_saved_results(
    run_dir: Path, packets: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Rebuild accepted evidence from saved raw responses without another call."""
    path = run_dir / "research_results.json"
    results = json.loads(path.read_text(encoding="utf-8"))
    for quote_id, item in results.get("items", {}).items():
        raw_path = run_dir / "raw_responses" / f"{quote_id}.json"
        if not raw_path.exists() and item.get("carried_forward_from_run"):
            raw_path = (
                run_dir / "raw_responses" / "carried"
                / f"{quote_id}_{item['carried_forward_from_run']}.json"
            )
        if not raw_path.exists():
            raise RuntimeError(f"saved OpenAI raw response is missing: {quote_id}")
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        response_hash = _sha256(_canonical_json(raw))
        if response_hash != item.get("response_hash"):
            raise RuntimeError(f"saved OpenAI response hash differs: {quote_id}")
        sources, search_calls = extract_cited_sources(raw)
        accepted: list[dict[str, Any]] = []
        prior_revalidation_rejections = [
            row for row in item.get("rejected_sources", [])
            if str(row.get("reason") or "").startswith(
                "accepted_source_revalidation:"
            )
        ]
        approximate_candidates = [
            row for row in item.get("rejected_sources", [])
            if row.get("reason") == "quotation_wording_not_found"
            and recognised_source_authority(
                str(row.get("source", {}).get("url") or "")
            ) is not None
        ]
        rejected = [
            row for row in item.get("rejected_sources", [])
            if row not in prior_revalidation_rejections
            and row not in approximate_candidates
        ]
        seen: set[str] = set()
        sources_to_revalidate = list(item.get("validated_sources", [])) + [
            row["source"] for row in prior_revalidation_rejections
        ]
        for source in sources_to_revalidate:
            verified, reason = verify_source(source, packets[quote_id])
            if verified is None:
                rejected.append({
                    "source": source,
                    "reason": f"accepted_source_revalidation:{reason}",
                })
                continue
            if verified["source_id"] in seen:
                continue
            verified.update({
                "research_provider": "openai",
                "research_model": MODEL,
                "research_prompt_version": PROMPT_VERSION,
            })
            accepted.append(verified)
            seen.add(verified["source_id"])
        if accepted:
            rejected.extend(approximate_candidates)
        else:
            for index, row in enumerate(approximate_candidates):
                source = row["source"]
                verified, reason = verify_grounding_source(
                    source, packets[quote_id]
                )
                if verified is None:
                    rejected.append({
                        "source": source,
                        "reason": f"approximate_revalidation:{reason}",
                    })
                    continue
                verified.update({
                    "research_provider": "openai",
                    "research_model": MODEL,
                    "research_prompt_version": PROMPT_VERSION,
                })
                accepted.append(verified)
                seen.add(verified["source_id"])
                rejected.extend(approximate_candidates[index + 1:])
                break
        item.update({
            "search_call_count": search_calls,
            "cited_source_count": len(sources),
            "validated_sources": accepted,
            "rejected_sources": rejected,
            "final_outcome": (
                "reliable_sources_found"
                if accepted
                else "no_locally_verified_source_from_search"
            ),
            "deterministically_revalidated_at": _utc_now(),
        })
    atomic_write_json(path, results)
    return results


def seed_completed_results(
    destination: Path,
    source_runs: list[Path],
    packets: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Carry completed immutable responses into a resumable aggregate run."""
    destination.mkdir(parents=True, exist_ok=True)
    results_path = destination / "research_results.json"
    aggregate = (
        json.loads(results_path.read_text(encoding="utf-8"))
        if results_path.exists()
        else {"schema_version": 1, "items": {}, "failures": {}}
    )
    for source_run in source_runs:
        source_results = revalidate_saved_results(source_run, packets)
        for quote_id, item in source_results.get("items", {}).items():
            if item.get("quote_text") != packets[quote_id]["quote_text"]:
                raise RuntimeError(f"carried OpenAI result changed identity: {quote_id}")
            carried = json.loads(json.dumps(item))
            carried["carried_forward_from_run"] = source_run.name
            previous = aggregate["items"].get(quote_id)
            if previous and previous.get("response_hash") != carried.get("response_hash"):
                carried["superseded_carried_response_hash"] = previous.get("response_hash")
            aggregate["items"][quote_id] = carried
            source_raw = source_run / "raw_responses" / f"{quote_id}.json"
            carried_raw = (
                destination / "raw_responses" / "carried"
                / f"{quote_id}_{source_run.name}.json"
            )
            carried_raw.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_raw, carried_raw)
    atomic_write_json(results_path, aggregate)
    return aggregate


def build_research_manifest(
    run_dir: Path,
    packets: dict[str, dict[str, Any]],
    queue_quote_ids: list[str],
) -> dict[str, Any]:
    """Compile a dependency-free evidence manifest from a completed run."""
    results_path = run_dir / "research_results.json"
    ledger_path = run_dir / "api_cost_ledger.json"
    results = revalidate_saved_results(run_dir, packets)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    if any(row.get("status") == "sending" for row in ledger.get("attempts", [])):
        raise RuntimeError("OpenAI source run has an unresolved sending attempt")
    manifest = {
        "schema_version": OPENAI_RESEARCH_SCHEMA_VERSION,
        "policy_version": OPENAI_RESEARCH_POLICY_VERSION,
        "provider": "openai",
        "model": MODEL,
        "sdk_version": importlib.metadata.version("openai"),
        "api_version": API_VERSION,
        "prompt_version": PROMPT_VERSION,
        "source_run_id": run_dir.name,
        "source_results_sha256": _sha256(results_path.read_bytes()),
        "source_ledger_sha256": _sha256(ledger_path.read_bytes()),
        "queue_quote_ids": queue_quote_ids,
        "queue_count": len(queue_quote_ids),
        "completed_quote_count": len(results.get("items", {})),
        "failure_count": len(results.get("failures", {})),
        "attempt_count": len(ledger.get("attempts", [])),
        "prior_known_spend_usd": float(ledger["prior_known_spend_usd"]),
        "incremental_known_spend_usd": round(
            float(ledger["known_spend_usd"])
            - float(ledger["prior_known_spend_usd"]),
            10,
        ),
        "cumulative_known_spend_usd": float(ledger["known_spend_usd"]),
        "cumulative_ambiguous_exposure_usd": float(
            ledger["ambiguous_exposure_usd"]
        ),
        "items": results.get("items", {}),
        "failures": results.get("failures", {}),
    }
    validate_openai_research_manifest(manifest, packets)
    return manifest


class PilotRunner:
    """Execute an atomic, resumable and strictly budgeted OpenAI pilot."""

    def __init__(
        self,
        run_dir: Path,
        packets: dict[str, dict[str, Any]],
        quote_ids: list[str],
        client: OpenAIWebSearchClient,
        *,
        prior_known_spend_usd: float,
        prior_ambiguous_exposure_usd: float = 0.0,
    ):
        """Configure a resumable, sequential pilot with cumulative cost state."""
        self.run_dir = run_dir
        self.packets = packets
        self.quote_ids = quote_ids
        self.client = client
        self.prior_known_spend = prior_known_spend_usd
        self.prior_ambiguous_exposure = prior_ambiguous_exposure_usd
        self.ledger_path = run_dir / "api_cost_ledger.json"
        self.results_path = run_dir / "research_results.json"

    def _load(self) -> tuple[dict[str, Any], dict[str, Any]]:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        ledger = (
            json.loads(self.ledger_path.read_text())
            if self.ledger_path.exists()
            else {
                "schema_version": 1,
                "hard_limit_usd": HARD_LIMIT_USD,
                "prior_known_spend_usd": self.prior_known_spend,
                "prior_ambiguous_exposure_usd": self.prior_ambiguous_exposure,
                "known_spend_usd": self.prior_known_spend,
                "ambiguous_exposure_usd": self.prior_ambiguous_exposure,
                "attempts": [],
            }
        )
        results = (
            json.loads(self.results_path.read_text())
            if self.results_path.exists()
            else {"schema_version": 1, "items": {}, "failures": {}}
        )
        if not math.isclose(
            float(ledger.get("prior_known_spend_usd", -1)),
            self.prior_known_spend,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise RuntimeError("saved OpenAI ledger prior-spend baseline differs")
        if not math.isclose(
            float(ledger.get("prior_ambiguous_exposure_usd", -1)),
            self.prior_ambiguous_exposure,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise RuntimeError("saved OpenAI ledger prior ambiguous exposure differs")
        if any(row.get("status") == "sending" for row in ledger.get("attempts", [])):
            raise RuntimeError(
                "saved OpenAI ledger contains an unresolved sending attempt; "
                "refusing to repeat a potentially billed request"
            )
        atomic_write_json(self.ledger_path, ledger)
        atomic_write_json(self.results_path, results)
        return ledger, results

    def _persist_prepared(
        self, ledger: dict[str, Any], quote_id: str, prompt: str, maximum: float
    ) -> dict[str, Any]:
        exposure = float(ledger["known_spend_usd"]) + float(
            ledger["ambiguous_exposure_usd"]
        )
        if exposure + maximum > HARD_LIMIT_USD:
            raise RuntimeError("combined historical-context research hard limit reached")
        attempt = {
            "logical_call_id": f"openai-source-research:{quote_id}",
            "quote_id": quote_id,
            "provider": "openai",
            "model": MODEL,
            "prompt_version": PROMPT_VERSION,
            "prompt_hash": _sha256(prompt.encode()),
            "maximum_possible_cost_usd": maximum,
            "status": "sending",
            "prepared_at": _utc_now(),
        }
        ledger["attempts"].append(attempt)
        atomic_write_json(self.ledger_path, ledger)
        return attempt

    def _recalculate_known_spend(self, ledger: dict[str, Any]) -> None:
        """Include every response with authoritative usage in known spend."""
        ledger["known_spend_usd"] = self.prior_known_spend + sum(
            float(row.get("known_cost_usd") or 0)
            for row in ledger["attempts"]
            if row.get("status") in {"completed", "completed_unusable"}
        )

    def run(self, *, maximum_new_calls: int | None = None) -> dict[str, Any]:
        """Process incomplete pilot items without repeating completed requests."""
        ledger, results = self._load()
        new_calls = 0
        for quote_id in self.quote_ids:
            if quote_id in results["items"] or quote_id in results["failures"]:
                continue
            if maximum_new_calls is not None and new_calls >= maximum_new_calls:
                break
            packet = self.packets[quote_id]
            prompt = research_prompt(packet)
            maximum = maximum_call_cost(prompt)
            attempt = self._persist_prepared(ledger, quote_id, prompt, maximum)
            new_calls += 1
            try:
                response = self.client.call(prompt)
            except (RateLimitError, APIConnectionError, APITimeoutError) as exc:
                attempt["status"] = (
                    "429" if isinstance(exc, RateLimitError) else "ambiguous"
                )
                attempt["failure"] = type(exc).__name__
                if attempt["status"] == "ambiguous":
                    attempt["ambiguous_cost_exposure_usd"] = maximum
                    ledger["ambiguous_exposure_usd"] = float(
                        ledger["ambiguous_exposure_usd"]
                    ) + maximum
                atomic_write_json(self.ledger_path, ledger)
                results["failures"][quote_id] = {
                    "status": attempt["status"], "recorded_at": _utc_now()
                }
                atomic_write_json(self.results_path, results)
                break
            except APIStatusError as exc:
                ambiguous = int(exc.status_code) >= 500
                attempt["status"] = "ambiguous" if ambiguous else "http_error"
                attempt["failure"] = f"HTTP {exc.status_code}"
                if ambiguous:
                    attempt["ambiguous_cost_exposure_usd"] = maximum
                    ledger["ambiguous_exposure_usd"] = float(
                        ledger["ambiguous_exposure_usd"]
                    ) + maximum
                atomic_write_json(self.ledger_path, ledger)
                results["failures"][quote_id] = {
                    "status": attempt["status"], "http_status": exc.status_code,
                    "recorded_at": _utc_now(),
                }
                atomic_write_json(self.results_path, results)
                if ambiguous:
                    break
                continue
            except Exception as exc:
                attempt["status"] = "failed_before_usable_response"
                attempt["failure"] = f"{type(exc).__name__}: {exc}"
                atomic_write_json(self.ledger_path, ledger)
                results["failures"][quote_id] = {
                    "status": "failed", "failure": type(exc).__name__,
                    "recorded_at": _utc_now(),
                }
                atomic_write_json(self.results_path, results)
                continue
            raw_path = self.run_dir / "raw_responses" / f"{quote_id}.json"
            atomic_write_json(raw_path, response["raw"])
            if not 1 <= response["search_calls"] <= MAX_REPORTED_SEARCH_CALLS_GUARD:
                attempt.update({
                    "status": "completed_unusable",
                    "response_hash": _sha256(_canonical_json(response["raw"])),
                    "request_id": response["request_id"],
                    "input_tokens": response["input_tokens"],
                    "output_tokens": response["output_tokens"],
                    "search_call_count": response["search_calls"],
                    "known_cost_usd": response["known_cost_usd"],
                    "latency_seconds": response["latency_seconds"],
                    "failure": "forced_search_call_count_outside_guard",
                    "completed_at": _utc_now(),
                })
                self._recalculate_known_spend(ledger)
                atomic_write_json(self.ledger_path, ledger)
                results["failures"][quote_id] = {
                    "status": "completed_unusable",
                    "failure": "forced_search_call_count_outside_guard",
                    "recorded_at": _utc_now(),
                }
                atomic_write_json(self.results_path, results)
                continue
            accepted = []
            rejected = []
            for source in response["sources"]:
                verified, reason = verify_grounding_source(source, packet)
                if verified is None:
                    rejected.append({"source": source, "reason": reason})
                elif not any(
                    row["source_id"] == verified["source_id"] for row in accepted
                ):
                    verified.update({
                        "research_provider": "openai",
                        "research_model": MODEL,
                        "research_prompt_version": PROMPT_VERSION,
                    })
                    accepted.append(verified)
            attempt.update({
                "status": "completed",
                "response_hash": _sha256(_canonical_json(response["raw"])),
                "request_id": response["request_id"],
                "input_tokens": response["input_tokens"],
                "output_tokens": response["output_tokens"],
                "search_call_count": response["search_calls"],
                "known_cost_usd": response["known_cost_usd"],
                "latency_seconds": response["latency_seconds"],
                "completed_at": _utc_now(),
            })
            self._recalculate_known_spend(ledger)
            atomic_write_json(self.ledger_path, ledger)
            results["items"][quote_id] = {
                "quote_id": quote_id,
                "quote_text": packet["quote_text"],
                "model": MODEL,
                "prompt_version": PROMPT_VERSION,
                "response_hash": attempt["response_hash"],
                "search_call_count": response["search_calls"],
                "cited_source_count": len(response["sources"]),
                "validated_sources": accepted,
                "rejected_sources": rejected,
                "final_outcome": (
                    "reliable_sources_found"
                    if accepted
                    else "no_locally_verified_source_from_search"
                ),
                "completed_at": _utc_now(),
            }
            atomic_write_json(self.results_path, results)
        return {"ledger": ledger, "results": results}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line options for pilot execution and manifest rebuilding."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-dir", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--prior-known-spend-usd", type=float, required=True)
    parser.add_argument("--prior-ambiguous-exposure-usd", type=float, default=0.0)
    parser.add_argument("--maximum-new-calls", type=int, default=5)
    parser.add_argument("--seed-run-dir", type=Path, action="append", default=[])
    parser.add_argument("--build-manifest-only", action="store_true")
    parser.add_argument("--manifest-output", type=Path)
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run preflight, paid execution or offline manifest compilation."""
    args = parse_args(argv)
    packets, _ = load_and_validate_corpus(
        args.research_dir,
        require_source_role_audit=False,
        load_source_role_audit=False,
    )
    audit = json.loads(args.audit.read_text(encoding="utf-8"))
    if args.seed_run_dir:
        seed_completed_results(args.run_dir, args.seed_run_dir, packets)
    existing = (
        json.loads((args.run_dir / "research_results.json").read_text())
        if (args.run_dir / "research_results.json").exists()
        else {"items": {}, "failures": {}}
    )
    queue = resumable_research_queue(
        audit,
        set(existing.get("items", {})) | set(existing.get("failures", {})),
    )
    pending = [
        quote_id for quote_id in queue
        if quote_id not in existing.get("items", {})
        and quote_id not in existing.get("failures", {})
    ][: args.maximum_new_calls]
    preflight = build_preflight(
        packets, pending,
        prior_known_spend_usd=args.prior_known_spend_usd,
        prior_ambiguous_exposure_usd=args.prior_ambiguous_exposure_usd,
    )
    args.run_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.run_dir / "cost_preflight.json", preflight)
    if not preflight["within_hard_limit"]:
        raise RuntimeError("OpenAI pilot cannot fit within the combined hard limit")
    if args.build_manifest_only:
        output = args.manifest_output or args.research_dir / OPENAI_RESEARCH_FILENAME
        manifest = build_research_manifest(args.run_dir, packets, queue)
        atomic_write_json(output, manifest)
        print(json.dumps({
            "manifest": str(output),
            "completed_quote_count": manifest["completed_quote_count"],
            "failure_count": manifest["failure_count"],
            "source_count": sum(
                len(item["validated_sources"])
                for item in manifest["items"].values()
            ),
            "sha256": _sha256(output.read_bytes()),
        }, indent=2, sort_keys=True))
        return 0
    if not args.execute:
        print(json.dumps(preflight, indent=2, sort_keys=True))
        return 0
    runner = PilotRunner(
        args.run_dir,
        packets,
        queue,
        OpenAIWebSearchClient(os.environ.get("OPENAI_API_KEY", "")),
        prior_known_spend_usd=args.prior_known_spend_usd,
        prior_ambiguous_exposure_usd=args.prior_ambiguous_exposure_usd,
    )
    outcome = runner.run(maximum_new_calls=args.maximum_new_calls)
    print(json.dumps({
        "known_spend_usd": outcome["ledger"]["known_spend_usd"],
        "ambiguous_exposure_usd": outcome["ledger"]["ambiguous_exposure_usd"],
        "completed": len(outcome["results"]["items"]),
        "failures": len(outcome["results"]["failures"]),
        "sources_found": sum(
            bool(item["validated_sources"])
            for item in outcome["results"]["items"].values()
        ),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
