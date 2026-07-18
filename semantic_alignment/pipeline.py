from __future__ import annotations

import base64
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from . import (CRITIC_PROMPT_VERSION, IMAGE_PROMPT_VERSION, QUOTE_PROMPT_VERSION,
               CRITIC_SCHEMA_VERSION, IMAGE_SCHEMA_VERSION, QUOTE_SCHEMA_VERSION)
from .io import atomic_write_json, read_json, read_jsonl, sha256_file, unique_dicts
from .prompts import critic_prompt, image_prompt, quote_prompt
from .replay import replay_candidate_cache
from .schemas import (
    CRITIC_OUTPUT_SCHEMA, IMAGE_OUTPUT_SCHEMA, QUOTE_OUTPUT_SCHEMA,
    validate_critic_result, validate_image_fingerprint, validate_quote_fingerprint,
)
from .shortlist import build_shortlist

DEFAULT_MODEL = "grok-4.5"
USD_TICKS_PER_DOLLAR = 10_000_000_000
INPUT_USD_PER_MILLION = 2.0
OUTPUT_USD_PER_MILLION = 6.0
MODEL_PRICES_TICKS = {"input": 20_000, "cached_input": 5_000, "image_input": 20_000, "output": 60_000}
STAGE_CEILINGS = {"quote": 6.0, "image": 3.0, "critic": 3.0}
TOTAL_CEILING = 12.0
TOKEN_ASSUMPTIONS = {
    "quote": {"input": 900, "image_input": 0, "output": 650, "reasoning_output_allowance": 250, "max_output": 1000},
    "image": {"input": 400, "image_input": 4100, "output": 750, "reasoning_output_allowance": 250, "max_output": 1000},
    "critic": {"input": 1500, "image_input": 0, "output": 700, "reasoning_output_allowance": 300, "max_output": 1000},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def database(kind: str, prompt_version: str) -> dict[str, Any]:
    return {"schema_version": 2, "analysis_kind": kind, "prompt_version": prompt_version, "created_at": utc_now(), "updated_at": utc_now(), "items": {}, "failures": {}}


def load_database(path: Path, kind: str, prompt_version: str) -> dict[str, Any]:
    data = read_json(path, None)
    if data is None:
        return database(kind, prompt_version)
    if not isinstance(data, dict) or data.get("schema_version") != 2 or data.get("analysis_kind") != kind or data.get("prompt_version") != prompt_version or not isinstance(data.get("items"), dict):
        raise ValueError(f"Invalid {kind} database: {path}")
    return data


def quote_inventory(project_dir: Path) -> list[dict[str, Any]]:
    data = read_json(project_dir / "quote_analysis.json", {})
    rows = []
    for quote_hash, item in sorted((data.get("items") or {}).items()):
        text = item.get("text") or (item.get("analysis") or {}).get("text")
        if isinstance(text, str) and text.strip():
            rows.append({"quote_hash": quote_hash, "quote_text": text, "existing_analysis": item.get("analysis") or {}})
    return rows


def generated_image_inventory(project_dir: Path, *, quarantined: bool = False) -> list[dict[str, Any]]:
    if not quarantined:
        paths = sorted((project_dir / "generated_review_approved_images").glob("tg_*.png"))
    else:
        paths = sorted((project_dir / "generated_image_quarantine" / "transactions").glob("*/images/tg_*.png"))
    seen, rows = set(), []
    for path in paths:
        if path.name in seen:
            continue
        seen.add(path.name)
        rows.append({"image_basename": path.name, "path": path, "sha256": sha256_file(path), "pool": "quarantined" if quarantined else "active"})
    return rows


def pending_quotes(inventory: list[dict[str, Any]], db: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for row in inventory:
        item = (db.get("items") or {}).get(row["quote_hash"])
        try:
            validate_quote_fingerprint(item)
            if item["quote_text"] != row["quote_text"]:
                raise ValueError("quote text changed")
        except (ValueError, TypeError):
            result.append(row)
    return result


def pending_images(inventory: list[dict[str, Any]], db: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for row in inventory:
        item = (db.get("items") or {}).get(row["image_basename"])
        try:
            validate_image_fingerprint(item)
            if item["sha256"] != row["sha256"]:
                raise ValueError("image hash changed")
        except (ValueError, TypeError):
            result.append(row)
    return result


def estimate_stage(stage: str, calls: int, *, retry_allowance: int = 0) -> dict[str, Any]:
    assumptions = TOKEN_ASSUMPTIONS[stage]
    billed_calls = calls + retry_allowance
    input_tokens = billed_calls * assumptions["input"]
    image_tokens = billed_calls * assumptions["image_input"]
    output_tokens = billed_calls * assumptions["output"]
    cost = ((input_tokens + image_tokens) * INPUT_USD_PER_MILLION + output_tokens * OUTPUT_USD_PER_MILLION) / 1_000_000
    return {
        "stage": stage, "model": DEFAULT_MODEL, "remaining_calls": calls,
        "retry_allowance_calls": retry_allowance, "estimated_input_tokens": input_tokens,
        "image_input_token_allowance": image_tokens, "estimated_output_tokens": output_tokens,
        "reasoning_output_allowance_per_call": assumptions["reasoning_output_allowance"],
        "max_completion_tokens_per_call": assumptions["max_output"],
        "input_usd_per_million": INPUT_USD_PER_MILLION,
        "output_usd_per_million": OUTPUT_USD_PER_MILLION,
        "estimated_stage_cost_usd": round(cost, 6), "hard_ceiling_usd": STAGE_CEILINGS[stage],
    }


def estimate_cost(*, quotes: int = 0, images: int = 0, critic: int = 0,
                  include_retry_allowance: bool = True) -> dict[str, Any]:
    def retries(calls: int) -> int:
        return math.ceil(calls * 0.02) if include_retry_allowance and calls else 0
    stages = [estimate_stage("quote", quotes, retry_allowance=retries(quotes)),
              estimate_stage("image", images, retry_allowance=retries(images)),
              estimate_stage("critic", critic, retry_allowance=retries(critic))]
    cumulative = sum(row["estimated_stage_cost_usd"] for row in stages)
    return {
        "model": DEFAULT_MODEL, "pricing_source": "authenticated /v1/models plus xAI cost-tracking documentation",
        "usd_ticks_per_dollar": USD_TICKS_PER_DOLLAR, "stages": stages,
        "cumulative_estimated_cost_usd": round(cumulative, 6), "hard_total_ceiling_usd": TOTAL_CEILING,
    }


def usage_record(usage: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(usage, dict) or type(usage.get("cost_in_usd_ticks")) is not int:
        raise MissingAuthoritativeCost("usage.cost_in_usd_ticks is absent")
    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    ticks = usage["cost_in_usd_ticks"]
    return {
        "cost_in_usd_ticks": ticks, "cost_usd": ticks / USD_TICKS_PER_DOLLAR,
        "input_tokens": int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
        "cached_tokens": int(prompt_details.get("cached_tokens") or usage.get("cached_tokens") or 0),
        "reasoning_tokens": int(completion_details.get("reasoning_tokens") or usage.get("reasoning_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }


class MissingAuthoritativeCost(RuntimeError):
    pass


class CostLimitReached(RuntimeError):
    pass


class CostLedger:
    def __init__(self, path: Path, *, create: bool = True, run_id: str | None = None) -> None:
        self.path = path
        existing = read_json(path, None)
        if existing is None and not create:
            raise FileNotFoundError(path)
        self.data = existing or {"schema_version": 2, "run_id": run_id, "status": "resumable", "model": DEFAULT_MODEL, "usd_ticks_per_dollar": USD_TICKS_PER_DOLLAR, "calls": [], "ambiguous_requests": [], "blocked": False, "created_at": utc_now()}
        if existing is None:
            atomic_write_json(self.path, self.data)
        if self.data.get("blocked") or self.data.get("status") in {"blocked_ambiguous_cost", "archived"}:
            raise MissingAuthoritativeCost(f"Cost ledger is blocked: {self.data.get('blocked_reason')}")

    def ticks(self, stage: str | None = None) -> int:
        return sum(int(row["cost_in_usd_ticks"]) for row in self.data["calls"] if stage is None or row["stage"] == stage)

    def guard_next(self, stage: str, *, confirmed_stage_limit: float) -> None:
        maximum = TOKEN_ASSUMPTIONS[stage]
        max_ticks = (maximum["input"] + maximum["image_input"]) * MODEL_PRICES_TICKS["input"] + maximum["max_output"] * MODEL_PRICES_TICKS["output"]
        stage_limit = min(STAGE_CEILINGS[stage], confirmed_stage_limit)
        if self.ticks(stage) + max_ticks > int(stage_limit * USD_TICKS_PER_DOLLAR):
            raise CostLimitReached(f"Next {stage} call could exceed stage ceiling ${stage_limit:.2f}")
        if self.ticks() + max_ticks > int(TOTAL_CEILING * USD_TICKS_PER_DOLLAR):
            raise CostLimitReached(f"Next {stage} call could exceed total ceiling ${TOTAL_CEILING:.2f}")

    def record(self, *, call_id: str, stage: str, item_key: str, usage: dict[str, Any], model: str, attempt: int = 1) -> dict[str, Any]:
        prior = next((row for row in self.data["calls"] if row["call_id"] == call_id), None)
        if prior:
            return prior
        try:
            parsed = usage_record(usage)
        except MissingAuthoritativeCost as exc:
            self.block_ambiguous(stage=stage, item_key=item_key, model=model,
                                 error=exc, response_received=True)
            raise
        row = {"call_id": call_id, "stage": stage, "item_key": item_key, "model": model, "attempt": attempt, "timestamp": utc_now(), **parsed}
        self.data["calls"].append(row)
        self.data.update({"updated_at": utc_now(), "total_cost_in_usd_ticks": self.ticks(), "total_cost_usd": self.ticks() / USD_TICKS_PER_DOLLAR})
        atomic_write_json(self.path, self.data)
        return row

    def block_ambiguous(self, *, stage: str, item_key: str, model: str,
                        error: BaseException, request_id: str | None = None,
                        response_received: bool = False) -> None:
        event = {"stage": stage, "item_key": item_key, "model": model,
                 "timestamp": utc_now(), "request_id": request_id,
                 "known_token_information": None,
                 "transport_failure": f"{type(error).__name__}: {error}",
                 "server_response_received": response_received}
        self.data.setdefault("ambiguous_requests", []).append(event)
        self.data.update({"blocked": True, "status": "blocked_ambiguous_cost",
                          "blocked_reason": "Inference outcome lacks authoritative cost metadata",
                          "blocked_item_key": item_key, "updated_at": utc_now()})
        atomic_write_json(self.path, self.data)


@dataclass
class XAIResult:
    content: dict[str, Any]
    usage: dict[str, Any]
    model: str
    raw: dict[str, Any]


class XAIClient:
    def __init__(self, *, api_key: str, model: str = DEFAULT_MODEL, transport: Callable[..., Any] | None = None) -> None:
        if not api_key:
            raise RuntimeError("XAI_API_KEY is required only with --execute-xai")
        self.api_key, self.model = api_key, model
        self.transport = transport or requests.post

    def structured(self, prompt: str, *, stage: str, schema: dict[str, Any], image_path: Path | None = None, expected_sha256: str | None = None) -> XAIResult:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if image_path is not None:
            image_bytes = image_path.read_bytes()
            actual = hashlib.sha256(image_bytes).hexdigest()
            if expected_sha256 and actual != expected_sha256:
                raise ValueError(f"Image changed before request: {image_path}")
            encoded = base64.b64encode(image_bytes).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}})
        response = self.transport(
            "https://api.x.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model, "messages": [{"role": "user", "content": content}],
                "response_format": {"type": "json_schema", "json_schema": {"name": f"semantic_alignment_{stage}", "strict": True, "schema": schema}},
                "reasoning_effort": "low", "max_tokens": TOKEN_ASSUMPTIONS[stage]["max_output"],
            },
            timeout=120,
        )
        response.raise_for_status()
        raw = response.json()
        return XAIResult(content=json.loads(raw["choices"][0]["message"]["content"]), usage=raw.get("usage") or {}, model=str(raw.get("model") or self.model), raw=raw)


def require_execution_approval(estimate: dict[str, Any], *, execute_xai: bool, cost_limit: float | None) -> None:
    if not execute_xai:
        raise RuntimeError("External analysis requires explicit --execute-xai")
    if cost_limit is None:
        raise RuntimeError("--confirm-cost-limit-usd is required with --execute-xai")
    estimated = estimate.get("estimated_stage_cost_usd", estimate.get("cumulative_estimated_cost_usd", 0))
    if estimated > cost_limit:
        raise RuntimeError(f"Estimated cost ${estimated:.2f} exceeds confirmed limit ${cost_limit:.2f}")


def cached_call(*, stage: str, key: str, prompt: str, schema: dict[str, Any], client: XAIClient, ledger: CostLedger, response_dir: Path, confirmed_stage_limit: float, image_path: Path | None = None, expected_sha256: str | None = None) -> XAIResult:
    cache = response_dir / stage / f"{hashlib.sha256(key.encode()).hexdigest()}.json"
    if cache.exists():
        raw = read_json(cache)
        result = XAIResult(content=raw["content"], usage=raw["usage"], model=raw["model"], raw=raw.get("raw") or {})
    else:
        ledger.guard_next(stage, confirmed_stage_limit=confirmed_stage_limit)
        try:
            result = client.structured(prompt, stage=stage, schema=schema, image_path=image_path, expected_sha256=expected_sha256)
        except BaseException as exc:
            ledger.block_ambiguous(stage=stage, item_key=key, model=client.model, error=exc)
            raise
        atomic_write_json(cache, {"item_key": key, "stage": stage, "content": result.content, "usage": result.usage, "model": result.model, "raw": result.raw, "received_at": utc_now()})
    ledger.record(call_id=f"{stage}:{key}", stage=stage, item_key=key, usage=result.usage, model=result.model)
    return result


def run_quote_analysis(rows: list[dict[str, Any]], db: dict[str, Any], output: Path, client: XAIClient, *, ledger: CostLedger | None = None, confirmed_stage_limit: float = 6.0, max_items: int | None = None, checkpoint_every: int = 5) -> int:
    ledger = ledger or CostLedger(output.parent / "cost_ledger.json")
    completed = 0; consecutive_schema_failures = 0
    for row in rows[:max_items]:
        key = row["quote_hash"]
        try:
            result = cached_call(stage="quote", key=key, prompt=quote_prompt(row["quote_text"]), schema=QUOTE_OUTPUT_SCHEMA, client=client, ledger=ledger, response_dir=output.parent / "runs" / "responses", confirmed_stage_limit=confirmed_stage_limit)
            item = {"schema_version": QUOTE_SCHEMA_VERSION, "analysis_kind": "quote_semantic_fingerprint", "quote_hash": key, "quote_text": row["quote_text"], **result.content, "model": result.model, "prompt_version": QUOTE_PROMPT_VERSION, "analysed_at": utc_now(), "provenance": {"source": "xai_structured_analysis", "independent_input": "quote_text_only"}}
            db["items"][key] = validate_quote_fingerprint(item)
            db["failures"].pop(key, None)
            completed += 1; consecutive_schema_failures = 0
        except (MissingAuthoritativeCost, CostLimitReached):
            db["updated_at"] = utc_now(); atomic_write_json(output, db); raise
        except Exception as exc:
            db["failures"][key] = {"error": str(exc), "retryable": isinstance(exc, requests.RequestException), "failed_at": utc_now()}
            consecutive_schema_failures = consecutive_schema_failures + 1 if isinstance(exc, ValueError) else 0
            if consecutive_schema_failures >= 3:
                db["updated_at"] = utc_now(); atomic_write_json(output, db); raise RuntimeError("Stopping after three consecutive quote schema/content failures") from exc
        if completed % checkpoint_every == 0:
            db["updated_at"] = utc_now(); atomic_write_json(output, db)
    db["updated_at"] = utc_now(); atomic_write_json(output, db)
    return completed


def run_image_analysis(rows: list[dict[str, Any]], db: dict[str, Any], output: Path, client: XAIClient, *, ledger: CostLedger | None = None, confirmed_stage_limit: float = 3.0, max_items: int | None = None, checkpoint_every: int = 5) -> int:
    ledger = ledger or CostLedger(output.parent / "cost_ledger.json")
    completed = 0; consecutive_schema_failures = 0
    for row in rows[:max_items]:
        key = row["image_basename"]
        try:
            result = cached_call(stage="image", key=key, prompt=image_prompt(), schema=IMAGE_OUTPUT_SCHEMA, client=client, ledger=ledger, response_dir=output.parent / "runs" / "responses", confirmed_stage_limit=confirmed_stage_limit, image_path=row["path"], expected_sha256=row["sha256"])
            if sha256_file(row["path"]) != row["sha256"]:
                raise ValueError(f"Image changed after request: {row['path']}")
            item = {"schema_version": IMAGE_SCHEMA_VERSION, "analysis_kind": "image_implied_message", "image_basename": key, "sha256": row["sha256"], **result.content, "model": result.model, "prompt_version": IMAGE_PROMPT_VERSION, "analysed_at": utc_now(), "provenance": {"source": "xai_vision_structured_analysis", "independent_input": "image_pixels_only"}}
            db["items"][key] = validate_image_fingerprint(item)
            db["failures"].pop(key, None)
            completed += 1; consecutive_schema_failures = 0
        except (MissingAuthoritativeCost, CostLimitReached):
            db["updated_at"] = utc_now(); atomic_write_json(output, db); atomic_write_json(output.parent / "image_analysis_failures.json", db["failures"]); raise
        except Exception as exc:
            db["failures"][key] = {"error": str(exc), "retryable": isinstance(exc, requests.RequestException), "failed_at": utc_now()}
            consecutive_schema_failures = consecutive_schema_failures + 1 if isinstance(exc, ValueError) else 0
            if consecutive_schema_failures >= 3:
                db["updated_at"] = utc_now(); atomic_write_json(output, db); atomic_write_json(output.parent / "image_analysis_failures.json", db["failures"]); raise RuntimeError("Stopping after three consecutive image schema/content failures") from exc
        if completed % checkpoint_every == 0:
            db["updated_at"] = utc_now(); atomic_write_json(output, db); atomic_write_json(output.parent / "image_analysis_failures.json", db["failures"])
    db["updated_at"] = utc_now(); atomic_write_json(output, db); atomic_write_json(output.parent / "image_analysis_failures.json", db["failures"])
    return completed


def canonical_winner_cases(session_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted((session_dir / "counterfactual" / "runs").glob("run_*/branch_comparison.jsonl")):
        for item in read_jsonl(path):
            for branch, winner in sorted((item.get("winners") or {}).items()):
                if not str(winner).startswith("tg_"):
                    continue
                rows.append({"quote_hash": item["quote_hash"], "quote_text": item.get("quote_text", ""), "image_basename": winner, "forced_reason": f"canonical_{branch}_winner", "run_id": item["run_id"], "post_index": item["post_index"]})
    return unique_dicts(rows, ("quote_hash", "image_basename"))


def build_validation_cases(project_dir: Path, session_dir: Path, *, limit: int = 150) -> dict[str, Any]:
    cases = canonical_winner_cases(session_dir)
    quote_data = quote_inventory(project_dir)
    free_trade = [row for row in quote_data if "every consumer has benefited" in row["quote_text"].lower()]
    for row in free_trade:
        cases.insert(0, {"quote_hash": row["quote_hash"], "quote_text": row["quote_text"], "image_basename": "tg_661b01c39a8d223df51cd0365e79ffe7e3c4f86ac81fa0af114ce95be49cb831.png", "forced_reason": "recent_production_free_trade_pairing", "expected_category": "related_but_indirect", "reviewer_notes": "Recent production pairing: assess whether generic capitalism/socialism imagery directly illustrates free trade."})
    selected = cases[:limit]
    return {"schema_version": 1, "analysis_kind": "semantic_alignment_manual_validation_cases", "created_at": utc_now(), "items": selected}


def canonical_inventory(session_dir: Path) -> dict[str, Any]:
    comparisons = list((session_dir / "counterfactual" / "runs").glob("run_*/branch_comparison.jsonl"))
    indices = sum(len(read_jsonl(path)) for path in comparisons)
    return {"runs": len(comparisons), "post_indices": indices, "branch_winner_records": indices * 3, "candidate_sets_available": False, "limitation": "canonical selection records have empty candidate_detail arrays; a fourth evolving branch cannot be reconstructed from winners alone"}


def make_shortlists(quote_db: dict[str, Any], image_db: dict[str, Any], validation: dict[str, Any], *, limit: int = 15) -> list[dict[str, Any]]:
    forced_by_quote: dict[str, dict[str, str]] = {}
    for case in validation.get("items", []):
        if case.get("image_basename"):
            forced_by_quote.setdefault(case["quote_hash"], {})[case["image_basename"]] = case.get("forced_reason", "validation_case")
    rows = []
    images = list(image_db.get("items", {}).values())
    for quote_hash, quote in sorted(quote_db.get("items", {}).items()):
        rows.extend(build_shortlist(quote, images, limit=limit, forced=forced_by_quote.get(quote_hash)))
    return rows


def critic_pairs(shortlists: list[dict[str, Any]], validation: dict[str, Any]) -> list[tuple[str, str]]:
    pairs = [(row["quote_hash"], row["image_basename"]) for row in shortlists]
    pairs.extend((row.get("quote_hash", ""), row.get("image_basename", "")) for row in validation.get("items", []))
    return sorted({pair for pair in pairs if pair[0] and pair[1]})


def run_critic(pairs: list[tuple[str, str]], quote_db: dict[str, Any], image_db: dict[str, Any], critic_db: dict[str, Any], output: Path, client: XAIClient, *, ledger: CostLedger | None = None, confirmed_stage_limit: float = 3.0, max_items: int | None = None) -> int:
    ledger = ledger or CostLedger(output.parent / "cost_ledger.json")
    completed = 0; consecutive_schema_failures = 0
    for quote_hash, basename in pairs[:max_items]:
        key = f"{quote_hash}:{basename}"
        existing = critic_db["items"].get(key)
        try:
            if existing:
                validate_critic_result(existing); continue
            quote, image = quote_db["items"][quote_hash], image_db["items"][basename]
            result = cached_call(stage="critic", key=key, prompt=critic_prompt(quote, image), schema=CRITIC_OUTPUT_SCHEMA, client=client, ledger=ledger, response_dir=output.parent / "runs" / "responses", confirmed_stage_limit=confirmed_stage_limit)
            item = {"schema_version": CRITIC_SCHEMA_VERSION, "analysis_kind": "quote_image_semantic_alignment", "quote_hash": quote_hash, "image_basename": basename, **result.content, "model": result.model, "prompt_version": CRITIC_PROMPT_VERSION, "analysed_at": utc_now(), "provenance": {"source": "xai_structured_critic", "independent_input": "validated_fingerprints_only"}}
            critic_db["items"][key] = validate_critic_result(item); critic_db["failures"].pop(key, None); completed += 1; consecutive_schema_failures = 0
        except (MissingAuthoritativeCost, CostLimitReached):
            critic_db["updated_at"] = utc_now(); atomic_write_json(output, critic_db); raise
        except Exception as exc:
            critic_db["failures"][key] = {"error": str(exc), "retryable": isinstance(exc, requests.RequestException), "failed_at": utc_now()}
            consecutive_schema_failures = consecutive_schema_failures + 1 if isinstance(exc, ValueError) else 0
            if consecutive_schema_failures >= 3:
                critic_db["updated_at"] = utc_now(); atomic_write_json(output, critic_db); raise RuntimeError("Stopping after three consecutive critic schema/content failures") from exc
        if completed % 5 == 0:
            critic_db["updated_at"] = utc_now(); atomic_write_json(output, critic_db)
    critic_db["updated_at"] = utc_now(); atomic_write_json(output, critic_db)
    return completed


def run_replay(candidate_cache: Path, critic_db: dict[str, Any], output: Path) -> dict[str, Any]:
    events = read_json(candidate_cache, [])
    if not isinstance(events, list):
        raise ValueError("candidate cache must contain a JSON list")
    critic = {}
    for item in critic_db.get("items", {}).values():
        validated = validate_critic_result(item)
        critic[(validated["quote_hash"], validated["image_basename"])] = validated
    result = replay_candidate_cache(events, critic)
    atomic_write_json(output, result)
    return result
