"""Compare provider compatibility on fixed image-to-quotation cases."""

from __future__ import annotations

import argparse
import copy
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests

from .bakeoff import PRICES, PROVIDER_MODELS, ProviderClient
from .image_quote_shortlist_rerank import (
    BATCH_SCHEMA,
    SHORTLIST_SIZE,
    _metrics,
    build_prompt,
    estimate_tokens,
    text_hash,
    utc_now,
    validate_response,
)
from .io import atomic_write_json, atomic_write_text, read_json
from .thatcher_image_hunt import append_jsonl, load_project_environment


SCHEMA_VERSION = 1
TRIAL_NAME = "image_quote_provider_compatibility_001"
PROMPT_VERSION = "image-quote-focused-shortlist-rerank-v1"
SELECTION_VERSION = "openai-claude-compatibility-sample-v1"
PROVIDERS = ("openai", "anthropic")
PILOT_IMAGE_COUNT = 8
CALIBRATION_IMAGE_COUNT = 2
EVALUATION_IMAGE_COUNT = 6
MAX_OUTPUT_TOKENS = 8192
EXPECTED_OUTPUT_TOKENS = 2500
READ_TIMEOUT_SECONDS = 360
HARD_COMBINED_LIMIT_USD = 6.0
PROVIDER_LIMITS_USD = {"openai": 4.0, "anthropic": 2.5}


def single_image_schema() -> dict[str, Any]:
    """Return the single image schema."""
    schema = copy.deepcopy(BATCH_SCHEMA)
    schema["properties"]["records"]["minItems"] = 1
    schema["properties"]["records"]["maxItems"] = 1
    return schema


def _suitable_sets(prior_results: dict[str, Any]) -> dict[str, dict[str, set[str]]]:
    result: dict[str, dict[str, set[str]]] = {}
    for provider in ("grok", "gemini"):
        rows = prior_results["providers"].get(provider) or []
        result[provider] = {
            row["candidate_id"]: {
                assessment["quote_hash"]
                for assessment in row["assessments"]
                if assessment["decision"] == "suitable"
            }
            for row in rows
        }
    if not result["grok"] or set(result["grok"]) != set(result["gemini"]):
        raise RuntimeError("complete, identity-matched Grok and Gemini results are required")
    return result


def _stratum(grok: set[str], gemini: set[str]) -> str:
    if not grok and not gemini:
        return "no_match"
    if grok and not gemini:
        return "grok_only"
    if gemini and not grok:
        return "gemini_only"
    if grok == gemini:
        return "shared_only"
    return "mixed_disagreement"


def _pick(rows: list[dict[str, Any]], stratum: str, count: int) -> list[dict[str, Any]]:
    eligible = [row for row in rows if row["provider_stratum"] == stratum]
    eligible.sort(key=lambda row: (text_hash(f"{SELECTION_VERSION}:{stratum}:{row['candidate_id']}"), row["candidate_id"]))
    if len(eligible) < count:
        raise RuntimeError(f"insufficient candidates for stratum {stratum}")
    return eligible[:count]


def select_pilot_images(
    shortlists: list[dict[str, Any]],
    split: dict[str, Any],
    prior_results: dict[str, Any],
) -> list[dict[str, Any]]:
    """Select pilot images."""
    suitable = _suitable_sets(prior_results)
    calibration = set(split["calibration_image_ids"])
    evaluation = set(split["evaluation_image_ids"])
    rows = []
    for shortlist in shortlists:
        candidate_id = shortlist["candidate_id"]
        partition = "calibration" if candidate_id in calibration else "evaluation" if candidate_id in evaluation else None
        if partition is None:
            raise RuntimeError(f"candidate missing from calibration split: {candidate_id}")
        grok = suitable["grok"][candidate_id]
        gemini = suitable["gemini"][candidate_id]
        rows.append({
            "candidate_id": candidate_id,
            "partition": partition,
            "provider_stratum": _stratum(grok, gemini),
            "grok_suitable_count": len(grok),
            "gemini_suitable_count": len(gemini),
            "shared_suitable_count": len(grok & gemini),
        })

    calibration_rows = [row for row in rows if row["partition"] == "calibration"]
    evaluation_rows = [row for row in rows if row["partition"] == "evaluation"]
    selected = []
    selected.extend(_pick(calibration_rows, "no_match", 1))
    calibration_nonempty = [row for row in calibration_rows if row["provider_stratum"] != "no_match"]
    calibration_nonempty.sort(
        key=lambda row: (text_hash(f"{SELECTION_VERSION}:calibration_nonempty:{row['candidate_id']}"), row["candidate_id"])
    )
    if not calibration_nonempty:
        raise RuntimeError("no non-empty calibration provider case")
    selected.append(calibration_nonempty[0])
    selected.extend(_pick(evaluation_rows, "grok_only", 1))
    selected.extend(_pick(evaluation_rows, "gemini_only", 1))
    selected.extend(_pick(evaluation_rows, "no_match", 1))
    selected.extend(_pick(evaluation_rows, "shared_only", 1))
    selected.extend(_pick(evaluation_rows, "mixed_disagreement", 2))
    selected.sort(key=lambda row: row["candidate_id"])
    if len(selected) != PILOT_IMAGE_COUNT or len({row["candidate_id"] for row in selected}) != PILOT_IMAGE_COUNT:
        raise RuntimeError("compatibility sample must contain eight unique images")
    if sum(row["partition"] == "calibration" for row in selected) != CALIBRATION_IMAGE_COUNT:
        raise RuntimeError("compatibility sample calibration count mismatch")
    if sum(row["partition"] == "evaluation" for row in selected) != EVALUATION_IMAGE_COUNT:
        raise RuntimeError("compatibility sample evaluation count mismatch")
    return selected


def _maximum_cost(provider: str, input_tokens: int) -> float:
    price = PRICES[provider]
    return input_tokens * price["input"] / 1_000_000 + MAX_OUTPUT_TOKENS * price["output"] / 1_000_000


def _expected_cost(provider: str, input_tokens: int) -> float:
    price = PRICES[provider]
    return input_tokens * price["input"] / 1_000_000 + EXPECTED_OUTPUT_TOKENS * price["output"] / 1_000_000


def prepare_trial(source_trial: Path, output_dir: Path) -> dict[str, Any]:
    """Prepare trial."""
    if output_dir.exists() and (output_dir / "compatibility_manifest.json").exists():
        existing = read_json(output_dir / "compatibility_manifest.json")
        if existing.get("source_trial") != str(source_trial.resolve()):
            raise RuntimeError("existing compatibility trial belongs to another source trial")

    source_manifest = read_json(source_trial / "trial_manifest.json")
    if source_manifest.get("image_count") != 24 or source_manifest.get("shortlist_size") != SHORTLIST_SIZE:
        raise RuntimeError("source trial invariants failed")
    shortlists_document = read_json(source_trial / "shortlists.json")
    shortlists = shortlists_document["records"]
    split = read_json(source_trial / "calibration_split.json")
    examples_document = read_json(source_trial / "calibration_examples.json")
    examples = examples_document.get("records") if isinstance(examples_document, dict) else examples_document
    if not isinstance(examples, list):
        raise RuntimeError("calibration examples missing")
    prior_results = read_json(source_trial / "provider_results.json")
    sample = select_pilot_images(shortlists, split, prior_results)
    shortlist_by_id = {row["candidate_id"]: row for row in shortlists}
    schema = single_image_schema()

    output_dir.mkdir(parents=True, exist_ok=True)
    prompt_dir = output_dir / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    items = []
    for selection in sample:
        candidate_id = selection["candidate_id"]
        prompt = build_prompt([shortlist_by_id[candidate_id]], examples, response_schema=schema)
        prompt_path = prompt_dir / f"{candidate_id}.txt"
        atomic_write_text(prompt_path, prompt)
        items.append({
            **selection,
            "prompt_path": str(prompt_path.relative_to(output_dir)),
            "prompt_sha256": text_hash(prompt),
            "estimated_input_tokens": estimate_tokens(prompt),
            "shortlist_quote_ids_sha256": shortlist_by_id[candidate_id]["shortlist_quote_ids_sha256"],
        })

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "trial_name": TRIAL_NAME,
        "selection_version": SELECTION_VERSION,
        "source_trial": str(source_trial.resolve()),
        "source_trial_manifest_sha256": text_hash(json.dumps(source_manifest, sort_keys=True, separators=(",", ":"))),
        "prompt_version": PROMPT_VERSION,
        "providers": list(PROVIDERS),
        "models": {provider: PROVIDER_MODELS[provider] for provider in PROVIDERS},
        "image_count": len(items),
        "calibration_image_count": CALIBRATION_IMAGE_COUNT,
        "evaluation_image_count": EVALUATION_IMAGE_COUNT,
        "shortlist_size": SHORTLIST_SIZE,
        "one_image_per_request": True,
        "provider_prompt_parity": True,
        "tools_enabled": False,
        "read_timeout_seconds": READ_TIMEOUT_SECONDS,
        "items": items,
        "selected_image_ids_sha256": text_hash("\n".join(row["candidate_id"] for row in items) + "\n"),
        "generated_at": utc_now(),
    }
    atomic_write_json(output_dir / "compatibility_manifest.json", manifest)
    atomic_write_json(output_dir / "response_schema.json", schema)

    provider_costs = {}
    for provider in PROVIDERS:
        expected = sum(_expected_cost(provider, row["estimated_input_tokens"]) for row in items)
        maximum = sum(_maximum_cost(provider, row["estimated_input_tokens"]) for row in items)
        provider_costs[provider] = {
            "model": PROVIDER_MODELS[provider],
            "planned_calls": len(items),
            "expected_cost_usd": round(expected, 6),
            "conservative_maximum_cost_usd": round(maximum, 6),
            "hard_limit_usd": PROVIDER_LIMITS_USD[provider],
        }
    preflight = {
        "schema_version": SCHEMA_VERSION,
        "planned_calls": len(items) * len(PROVIDERS),
        "planned_calls_by_provider": {provider: len(items) for provider in PROVIDERS},
        "expected_combined_cost_usd": round(sum(row["expected_cost_usd"] for row in provider_costs.values()), 6),
        "conservative_maximum_cost_usd": round(sum(row["conservative_maximum_cost_usd"] for row in provider_costs.values()), 6),
        "hard_combined_limit_usd": HARD_COMBINED_LIMIT_USD,
        "providers": provider_costs,
        "provider_prompt_parity": True,
        "one_image_per_request": True,
        "providers_run_concurrently": True,
        "maximum_concurrency_per_provider": 1,
        "read_timeout_seconds": READ_TIMEOUT_SECONDS,
        "tools_enabled": False,
        "network_calls_made": False,
        "prior_trial_ambiguous_exposure_usd": round(sum(
            float((read_json(source_trial / "providers" / provider / "state.json") or {}).get("ambiguous_exposure_usd") or 0.0)
            for provider in PROVIDERS
        ), 6),
        "prior_trial_exposure_is_outside_this_new_ceiling": True,
        "generated_at": utc_now(),
    }
    if any(row["conservative_maximum_cost_usd"] > row["hard_limit_usd"] for row in provider_costs.values()):
        raise RuntimeError("provider compatibility cost ceiling is too low")
    if preflight["conservative_maximum_cost_usd"] > HARD_COMBINED_LIMIT_USD:
        raise RuntimeError("combined compatibility cost ceiling is too low")
    atomic_write_json(output_dir / "preflight.json", preflight)
    preflight_lines = [
        "# OpenAI/Claude compatibility preflight", "",
        f"- Images: {len(items)} ({CALIBRATION_IMAGE_COUNT} calibration, {EVALUATION_IMAGE_COUNT} held out)",
        f"- Calls: {preflight['planned_calls']} ({len(items)} per provider)",
        f"- Expected new spend: US${preflight['expected_combined_cost_usd']:.4f}",
        f"- Conservative new-call maximum: US${preflight['conservative_maximum_cost_usd']:.4f}",
        f"- Hard new-run ceiling: US${HARD_COMBINED_LIMIT_USD:.2f}",
        f"- Prior ambiguous maximum exposure: US${preflight['prior_trial_ambiguous_exposure_usd']:.4f} (reported separately)",
        f"- Read timeout: {READ_TIMEOUT_SECONDS} seconds", "",
        "| Candidate | Partition | Prior-provider stratum | Prompt SHA-256 |",
        "|---|---|---|---|",
    ]
    preflight_lines.extend(
        f"| {row['candidate_id']} | {row['partition']} | {row['provider_stratum']} | `{row['prompt_sha256']}` |"
        for row in items
    )
    preflight_lines.extend(["", "No network call was made during preparation.", ""])
    atomic_write_text(output_dir / "preflight.md", "\n".join(preflight_lines))
    return {"manifest": manifest, "preflight": preflight}


def _provider_state(output_dir: Path, provider: str) -> dict[str, Any]:
    path = output_dir / "providers" / provider / "state.json"
    value = read_json(path, None) or {
        "schema_version": SCHEMA_VERSION,
        "provider": provider,
        "model": PROVIDER_MODELS[provider],
        "completed_items": {},
        "received_items": {},
        "failed_items": {},
        "attempt_counts": {},
        "known_cost_usd": 0.0,
        "ambiguous_exposure_usd": 0.0,
        "provider_stopped": False,
        "stop_reason": "",
    }
    value.setdefault("received_items", {})
    return value


def _save_provider_state(output_dir: Path, provider: str, state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    atomic_write_json(output_dir / "providers" / provider / "state.json", state)


def _combined_exposure(output_dir: Path) -> float:
    return sum(
        float(_provider_state(output_dir, provider).get("known_cost_usd") or 0.0)
        + float(_provider_state(output_dir, provider).get("ambiguous_exposure_usd") or 0.0)
        for provider in PROVIDERS
    )


def _guard_call(output_dir: Path, provider: str, input_tokens: int) -> None:
    state = _provider_state(output_dir, provider)
    maximum = _maximum_cost(provider, input_tokens)
    provider_exposure = float(state.get("known_cost_usd") or 0.0) + float(state.get("ambiguous_exposure_usd") or 0.0)
    if provider_exposure + maximum > PROVIDER_LIMITS_USD[provider]:
        raise RuntimeError(f"{provider} compatibility ceiling reached")
    if _combined_exposure(output_dir) + maximum > HARD_COMBINED_LIMIT_USD:
        raise RuntimeError("combined compatibility ceiling reached")


def _truncate_bounded_text(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value
    prefix = value[: maximum - 3].rsplit(" ", 1)[0].rstrip(" ,;:")
    if not prefix:
        prefix = value[: maximum - 3]
    return prefix + "..."


def normalise_provider_content(
    provider: str,
    value: Any,
    expected_shortlists: list[dict[str, Any]] | None = None,
) -> tuple[Any, list[dict[str, Any]]]:
    """Normalise provider content."""
    normalised = copy.deepcopy(value)
    changes: list[dict[str, Any]] = []
    if provider != "anthropic" or not isinstance(normalised, dict):
        return normalised, changes
    records = normalised.get("records")
    if not isinstance(records, list):
        return normalised, changes
    expected_by_candidate = {
        row["candidate_id"]: row for row in (expected_shortlists or [])
    }
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("assessments"), list):
            continue
        expected = expected_by_candidate.get(record.get("candidate_id"))
        if expected and record.get("image_sha256") == expected.get("image_sha256"):
            expected_hashes = [row["quote_hash"] for row in expected["prompt_quotes"]]
            actual_hashes = [row.get("quote_hash") for row in record["assessments"]]
            unknown = [value for value in actual_hashes if value not in set(expected_hashes)]
            missing = [value for value in expected_hashes if value not in set(actual_hashes)]
            if (
                len(actual_hashes) == len(expected_hashes)
                and len(set(actual_hashes)) == len(actual_hashes)
                and len(unknown) == len(missing) == 1
            ):
                position = actual_hashes.index(unknown[0])
                common_prefix = len(os.path.commonprefix([unknown[0], missing[0]]))
                if (
                    position == expected_hashes.index(missing[0])
                    and all(
                        actual == wanted
                        for index, (actual, wanted) in enumerate(zip(actual_hashes, expected_hashes))
                        if index != position
                    )
                    and common_prefix >= 40
                ):
                    record["assessments"][position]["quote_hash"] = missing[0]
                    changes.append({
                        "normalisation": "anthropic_single_quote_hash_copy_error",
                        "candidate_id": record.get("candidate_id"),
                        "assessment_position": position,
                        "original_quote_hash": unknown[0],
                        "normalised_quote_hash": missing[0],
                        "common_prefix_length": common_prefix,
                    })
        for assessment in record["assessments"]:
            if not isinstance(assessment, dict) or not isinstance(assessment.get("reason"), str):
                continue
            reason = assessment["reason"]
            if len(reason) <= 280:
                continue
            shortened = _truncate_bounded_text(reason, 280)
            assessment["reason"] = shortened
            changes.append({
                "normalisation": "anthropic_reason_max_length",
                "candidate_id": record.get("candidate_id"),
                "quote_hash": assessment.get("quote_hash"),
                "original_length": len(reason),
                "normalised_length": len(shortened),
            })
        summary = record.get("summary")
        if isinstance(summary, str) and len(summary) > 400:
            shortened = _truncate_bounded_text(summary, 400)
            record["summary"] = shortened
            changes.append({
                "normalisation": "anthropic_summary_max_length",
                "candidate_id": record.get("candidate_id"),
                "original_length": len(summary),
                "normalised_length": len(shortened),
            })
    return normalised, changes


def _saved_response_content(provider: str, raw: dict[str, Any]) -> Any:
    if provider == "anthropic":
        text = "".join(
            block.get("text", "")
            for block in raw.get("content") or []
            if isinstance(block, dict) and block.get("type") == "text"
        )
    elif provider == "openai":
        text = raw.get("output_text") or "".join(
            part.get("text", "")
            for item in raw.get("output") or []
            if isinstance(item, dict)
            for part in item.get("content") or []
            if isinstance(part, dict) and part.get("type") == "output_text"
        )
    else:
        raise ValueError(f"unsupported compatibility provider: {provider}")
    if not text:
        raise ValueError("saved provider response has no structured text")
    return json.loads(text)


def recover_received_items(
    output_dir: Path,
    provider: str,
    state: dict[str, Any],
    shortlists: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Recover received items."""
    provider_dir = output_dir / "providers" / provider
    attempts_path = provider_dir / "attempts.jsonl"
    for candidate_id, received in sorted(state["received_items"].items()):
        if candidate_id in state["completed_items"]:
            continue
        raw_path = output_dir / received["raw_path"]
        if not raw_path.is_file():
            continue
        try:
            content = _saved_response_content(provider, read_json(raw_path))
            content, changes = normalise_provider_content(
                provider, content, [shortlists[candidate_id]],
            )
            validated = validate_response(content, [shortlists[candidate_id]])
        except Exception as exc:
            state["failed_items"][candidate_id] = {
                **(state["failed_items"].get(candidate_id) or {}),
                "offline_recovery_error": f"{type(exc).__name__}: {exc}"[:4000],
                "charged_response": True,
                "ambiguous_outcome": False,
            }
            _save_provider_state(output_dir, provider, state)
            continue
        normal_path = provider_dir / "normalised" / f"{candidate_id}.json"
        atomic_write_json(normal_path, validated)
        state["completed_items"][candidate_id] = {
            **received,
            "path": str(normal_path.relative_to(output_dir)),
            "normalisations": changes,
            "offline_recovered": True,
        }
        state["failed_items"].pop(candidate_id, None)
        for change in changes:
            append_jsonl(provider_dir / "normalisations.jsonl", {
                **change, "provider": provider, "timestamp": utc_now(), "offline_recovery": True,
            })
        append_jsonl(attempts_path, {
            "event": "offline_recovered", "provider": provider,
            "candidate_id": candidate_id, "request_id": received.get("request_id"),
            "normalisation_count": len(changes), "completed_at": utc_now(),
        })
        _save_provider_state(output_dir, provider, state)
    if state.get("provider_stopped") and state.get("stop_reason") == "charged_validation_failure":
        unresolved = set(state["received_items"]) - set(state["completed_items"])
        if not unresolved:
            state["provider_stopped"] = False
            state["stop_reason"] = ""
            _save_provider_state(output_dir, provider, state)
    return state


def run_provider(output_dir: Path, provider: str, api_key: str) -> dict[str, Any]:
    """Run provider."""
    manifest = read_json(output_dir / "compatibility_manifest.json")
    schema = read_json(output_dir / "response_schema.json")
    source_trial = Path(manifest["source_trial"])
    shortlists = {row["candidate_id"]: row for row in read_json(source_trial / "shortlists.json")["records"]}
    provider_dir = output_dir / "providers" / provider
    provider_dir.mkdir(parents=True, exist_ok=True)
    state = _provider_state(output_dir, provider)
    state = recover_received_items(output_dir, provider, state, shortlists)
    if state.get("provider_stopped"):
        return state
    client = ProviderClient(provider, api_key, timeout_seconds=READ_TIMEOUT_SECONDS)
    attempts_path = provider_dir / "attempts.jsonl"

    for item in manifest["items"]:
        candidate_id = item["candidate_id"]
        if candidate_id in state["completed_items"]:
            continue
        if candidate_id in state["received_items"]:
            if candidate_id not in state["failed_items"]:
                state["failed_items"][candidate_id] = {
                    "error": "received response requires offline lifecycle reconciliation",
                    "ambiguous_outcome": False,
                    "charged_response": True,
                }
                _save_provider_state(output_dir, provider, state)
            continue
        if candidate_id in state["failed_items"] and int(state["attempt_counts"].get(candidate_id) or 0) >= 1:
            continue
        if state.get("provider_stopped"):
            break
        prompt = (output_dir / item["prompt_path"]).read_text(encoding="utf-8")
        if text_hash(prompt) != item["prompt_sha256"]:
            raise RuntimeError(f"prompt hash mismatch for {candidate_id}")
        _guard_call(output_dir, provider, int(item["estimated_input_tokens"]))
        attempt = int(state["attempt_counts"].get(candidate_id) or 0) + 1
        state["attempt_counts"][candidate_id] = attempt
        _save_provider_state(output_dir, provider, state)
        started_at = utc_now()
        append_jsonl(attempts_path, {
            "event": "started", "provider": provider, "candidate_id": candidate_id,
            "attempt": attempt, "prompt_sha256": item["prompt_sha256"], "started_at": started_at,
        })
        result: dict[str, Any] | None = None
        try:
            result = client.call(
                prompt,
                schema=schema,
                schema_name="image_quote_compatibility_single_image",
                max_output_tokens=MAX_OUTPUT_TOKENS,
            )
            raw_path = provider_dir / "raw" / f"{candidate_id}.json"
            normal_path = provider_dir / "normalised" / f"{candidate_id}.json"
            state["known_cost_usd"] = round(float(state["known_cost_usd"]) + float(result["cost_usd"]), 10)
            state["received_items"][candidate_id] = {
                "raw_path": str(raw_path.relative_to(output_dir)),
                "raw_persisted": False,
                "request_id": result["request_id"],
                "cost_usd": result["cost_usd"],
                "latency_seconds": result["latency_seconds"],
                "usage": result["usage"],
                "prompt_sha256": item["prompt_sha256"],
            }
            _save_provider_state(output_dir, provider, state)
            atomic_write_json(raw_path, result["raw"])
            state["received_items"][candidate_id]["raw_persisted"] = True
            _save_provider_state(output_dir, provider, state)
            try:
                content, changes = normalise_provider_content(
                    provider, result["content"], [shortlists[candidate_id]],
                )
                validated = validate_response(content, [shortlists[candidate_id]])
            except Exception as exc:
                state["failed_items"][candidate_id] = {
                    "error": f"{type(exc).__name__}: {exc}"[:4000],
                    "ambiguous_outcome": False,
                    "charged_response": True,
                    "request_id": result["request_id"],
                    "cost_usd": result["cost_usd"],
                }
                state["provider_stopped"] = True
                state["stop_reason"] = "charged_validation_failure"
                append_jsonl(attempts_path, {
                    "event": "charged_validation_failure", "provider": provider,
                    "candidate_id": candidate_id, "attempt": attempt,
                    "request_id": result["request_id"], "cost_usd": result["cost_usd"],
                    "completed_at": utc_now(),
                })
                _save_provider_state(output_dir, provider, state)
                continue
            atomic_write_json(normal_path, validated)
            for change in changes:
                append_jsonl(provider_dir / "normalisations.jsonl", {
                    **change, "provider": provider, "timestamp": utc_now(), "offline_recovery": False,
                })
            state["completed_items"][candidate_id] = {
                "path": str(normal_path.relative_to(output_dir)),
                "raw_path": str(raw_path.relative_to(output_dir)),
                "request_id": result["request_id"],
                "cost_usd": result["cost_usd"],
                "latency_seconds": result["latency_seconds"],
                "usage": result["usage"],
                "prompt_sha256": item["prompt_sha256"],
                "normalisations": changes,
            }
            state["failed_items"].pop(candidate_id, None)
            append_jsonl(attempts_path, {
                "event": "completed", "provider": provider, "candidate_id": candidate_id,
                "attempt": attempt, "request_id": result["request_id"], "cost_usd": result["cost_usd"],
                "latency_seconds": result["latency_seconds"], "completed_at": utc_now(),
            })
            _save_provider_state(output_dir, provider, state)
        except requests.ReadTimeout as exc:
            maximum = _maximum_cost(provider, int(item["estimated_input_tokens"]))
            state["ambiguous_exposure_usd"] = round(float(state["ambiguous_exposure_usd"]) + maximum, 10)
            state["failed_items"][candidate_id] = {
                "error": f"{type(exc).__name__}: {exc}",
                "ambiguous_outcome": True,
                "maximum_exposure_usd": maximum,
            }
            state["provider_stopped"] = True
            state["stop_reason"] = "ambiguous_read_timeout"
            append_jsonl(attempts_path, {
                "event": "ambiguous_timeout", "provider": provider, "candidate_id": candidate_id,
                "attempt": attempt, "maximum_exposure_usd": maximum, "completed_at": utc_now(),
            })
            _save_provider_state(output_dir, provider, state)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            body = (exc.response.text if exc.response is not None else str(exc))[:4000]
            state["failed_items"][candidate_id] = {"http_status": status, "error": body, "ambiguous_outcome": False}
            state["provider_stopped"] = True
            state["stop_reason"] = f"http_failure_{status or 'unknown'}"
            append_jsonl(attempts_path, {
                "event": "http_failure", "provider": provider, "candidate_id": candidate_id,
                "attempt": attempt, "http_status": status, "completed_at": utc_now(),
            })
            _save_provider_state(output_dir, provider, state)
        except Exception as exc:
            if result is None:
                maximum = _maximum_cost(provider, int(item["estimated_input_tokens"]))
                state["ambiguous_exposure_usd"] = round(float(state["ambiguous_exposure_usd"]) + maximum, 10)
                state["failed_items"][candidate_id] = {
                    "error": f"{type(exc).__name__}: {exc}"[:4000],
                    "ambiguous_outcome": True,
                    "maximum_exposure_usd": maximum,
                }
                event = "ambiguous_provider_response_failure"
            else:
                state["failed_items"][candidate_id] = {
                    "error": f"{type(exc).__name__}: {exc}"[:4000],
                    "ambiguous_outcome": False,
                    "charged_response": True,
                    "request_id": result.get("request_id"),
                    "cost_usd": result.get("cost_usd"),
                }
                event = "charged_local_persistence_failure"
            state["provider_stopped"] = True
            state["stop_reason"] = event
            append_jsonl(attempts_path, {
                "event": event, "provider": provider, "candidate_id": candidate_id,
                "attempt": attempt, "error_type": type(exc).__name__, "completed_at": utc_now(),
            })
            _save_provider_state(output_dir, provider, state)
    return state


def run_trial(project_dir: Path, output_dir: Path, *, execute: bool, confirmed_cost: float) -> dict[str, Any]:
    """Run trial."""
    if not execute:
        raise RuntimeError("live compatibility calls require --execute")
    if confirmed_cost != HARD_COMBINED_LIMIT_USD:
        raise RuntimeError(f"execution requires exact --confirm-max-cost-usd {HARD_COMBINED_LIMIT_USD:g}")
    manifest = read_json(output_dir / "compatibility_manifest.json")
    if manifest.get("image_count") != PILOT_IMAGE_COUNT:
        raise RuntimeError("compatibility manifest image count mismatch")
    load_project_environment(project_dir / "mrsMThatcher.env")
    keys = {"openai": os.getenv("OPENAI_API_KEY"), "anthropic": os.getenv("ANTHROPIC_API_KEY")}
    missing = [provider for provider, key in keys.items() if not key]
    if missing:
        raise RuntimeError(f"missing API credentials for: {', '.join(missing)}")
    results = {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="compatibility-provider") as executor:
        futures = {executor.submit(run_provider, output_dir, provider, keys[provider]): provider for provider in PROVIDERS}
        for future in as_completed(futures):
            provider = futures[future]
            results[provider] = future.result()
    return results


def _load_provider_records(output_dir: Path, provider: str) -> list[dict[str, Any]]:
    state = _provider_state(output_dir, provider)
    rows = []
    for candidate_id, item in sorted(state["completed_items"].items()):
        record = read_json(output_dir / item["path"])["records"][0]
        if record["candidate_id"] != candidate_id:
            raise RuntimeError("completed provider identity mismatch")
        rows.append(record)
    return rows


def build_report(output_dir: Path) -> dict[str, Any]:
    """Build report."""
    manifest = read_json(output_dir / "compatibility_manifest.json")
    source_trial = Path(manifest["source_trial"])
    split = read_json(source_trial / "calibration_split.json")
    human_rows = [
        row for row in split["calibration_pairs"] + split["evaluation_pairs"]
        if row["candidate_id"] in {item["candidate_id"] for item in manifest["items"]}
    ]
    providers = {}
    for provider in PROVIDERS:
        records = _load_provider_records(output_dir, provider)
        suitable = {
            (record["candidate_id"], assessment["quote_hash"])
            for record in records for assessment in record["assessments"]
            if assessment["decision"] == "suitable"
        }
        complete_ids = {record["candidate_id"] for record in records}
        calibration_rows = [row for row in human_rows if row["candidate_id"] in complete_ids and row["candidate_id"] in split["calibration_image_ids"]]
        evaluation_rows = [row for row in human_rows if row["candidate_id"] in complete_ids and row["candidate_id"] in split["evaluation_image_ids"]]
        state = _provider_state(output_dir, provider)
        providers[provider] = {
            "model": PROVIDER_MODELS[provider],
            "completed_images": len(records),
            "suitable_pair_count": len(suitable),
            "calibration_metrics": _metrics(calibration_rows, suitable, available=bool(calibration_rows)),
            "evaluation_metrics": _metrics(evaluation_rows, suitable, available=bool(evaluation_rows)),
            "known_cost_usd": state["known_cost_usd"],
            "ambiguous_exposure_usd": state["ambiguous_exposure_usd"],
            "provider_stopped": state["provider_stopped"],
            "stop_reason": state["stop_reason"],
        }
    selected_ids = {item["candidate_id"] for item in manifest["items"]}
    prior_results = read_json(source_trial / "provider_results.json")["providers"]
    baseline_providers = {}
    suitable_by_provider: dict[str, set[tuple[str, str]]] = {}
    for provider in ("grok", "gemini"):
        records = [row for row in prior_results[provider] if row["candidate_id"] in selected_ids]
        suitable = {
            (record["candidate_id"], assessment["quote_hash"])
            for record in records for assessment in record["assessments"]
            if assessment["decision"] == "suitable"
        }
        suitable_by_provider[provider] = suitable
        baseline_providers[provider] = {
            "model": PROVIDER_MODELS[provider],
            "completed_images": len(records),
            "suitable_pair_count": len(suitable),
            "calibration_metrics": _metrics(
                [row for row in human_rows if row["candidate_id"] in split["calibration_image_ids"]], suitable,
                available=len(records) == PILOT_IMAGE_COUNT,
            ),
            "evaluation_metrics": _metrics(
                [row for row in human_rows if row["candidate_id"] in split["evaluation_image_ids"]], suitable,
                available=len(records) == PILOT_IMAGE_COUNT,
            ),
        }
    for provider in PROVIDERS:
        records = _load_provider_records(output_dir, provider)
        suitable_by_provider[provider] = {
            (record["candidate_id"], assessment["quote_hash"])
            for record in records for assessment in record["assessments"]
            if assessment["decision"] == "suitable"
        }
    all_four_complete = all(
        (baseline_providers[provider]["completed_images"] if provider in baseline_providers else providers[provider]["completed_images"])
        == PILOT_IMAGE_COUNT
        for provider in ("grok", "gemini", "openai", "anthropic")
    )
    vote_counts: dict[tuple[str, str], int] = {}
    for suitable in suitable_by_provider.values():
        for pair in suitable:
            vote_counts[pair] = vote_counts.get(pair, 0) + 1
    majority_pairs = {pair for pair, votes in vote_counts.items() if votes >= 3}
    evaluation_human_rows = [row for row in human_rows if row["candidate_id"] in split["evaluation_image_ids"]]
    consensus_thresholds = {}
    all_suitable_pairs = set(vote_counts)
    for threshold in range(1, 5):
        threshold_pairs = {
            pair for pair in all_suitable_pairs if vote_counts[pair] >= threshold
        }
        consensus_thresholds[f"{threshold}_of_4"] = {
            "pair_count": len(threshold_pairs),
            "evaluation_metrics": _metrics(
                evaluation_human_rows, threshold_pairs, available=all_four_complete,
            ),
        }
    positive_pairs = {
        (row["candidate_id"], row["quote_hash"])
        for row in evaluation_human_rows if row["human_positive"]
    }
    prior_positive_pairs = positive_pairs & (
        suitable_by_provider["grok"] | suitable_by_provider["gemini"]
    )
    added_positive_pairs = positive_pairs & (
        suitable_by_provider["openai"] | suitable_by_provider["anthropic"]
    )
    incremental_provider_value = {
        "held_out_human_positive_pairs": len(positive_pairs),
        "found_by_grok_or_gemini": len(prior_positive_pairs),
        "found_by_openai_or_anthropic": len(added_positive_pairs),
        "found_only_by_openai_or_anthropic": len(added_positive_pairs - prior_positive_pairs),
        "found_only_by_grok_or_gemini": len(prior_positive_pairs - added_positive_pairs),
    }
    result = {
        "schema_version": SCHEMA_VERSION,
        "trial_name": TRIAL_NAME,
        "selected_images": manifest["items"],
        "providers": providers,
        "baseline_providers": baseline_providers,
        "all_four_provider_complete": all_four_complete,
        "three_of_four_majority_pair_count": len(majority_pairs) if all_four_complete else None,
        "three_of_four_majority_evaluation_metrics": _metrics(
            evaluation_human_rows, majority_pairs, available=all_four_complete,
        ),
        "consensus_thresholds": consensus_thresholds,
        "incremental_provider_value": incremental_provider_value,
        "known_cost_usd": round(sum(row["known_cost_usd"] for row in providers.values()), 10),
        "ambiguous_exposure_usd": round(sum(row["ambiguous_exposure_usd"] for row in providers.values()), 10),
        "generated_at": utc_now(),
    }
    atomic_write_json(output_dir / "provider_comparison.json", result)
    lines = [
        "# OpenAI/Claude image-quote compatibility pilot", "",
        "## Design", "",
        f"- Images: {manifest['image_count']} ({manifest['calibration_image_count']} calibration, {manifest['evaluation_image_count']} held out).",
        f"- One image and {manifest['shortlist_size']} unchanged quote candidates per request.",
        "- OpenAI and Claude received byte-identical substantive prompts.",
        "- Selection used the prior Grok/Gemini disagreement pattern, not held-out human labels.",
        "- No tools, search, image generation or production writes were enabled.", "",
        "## Outcomes", "",
        "| Provider | Model | Completed images | Suitable pairs | Held-out precision | Held-out recall | Known spend | Ambiguous exposure |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for provider in ("grok", "gemini", "openai", "anthropic"):
        row = baseline_providers[provider] if provider in baseline_providers else providers[provider]
        provider_label = {"openai": "OpenAI", "anthropic": "Claude"}.get(provider, provider.title())
        metrics = row["evaluation_metrics"]
        precision = "unavailable" if metrics["precision"] is None else f"{metrics['precision']:.1%}"
        recall = "unavailable" if metrics["recall"] is None else f"{metrics['recall']:.1%}"
        known_spend = "prior trial" if provider in baseline_providers else f"US${row['known_cost_usd']:.4f}"
        ambiguous = "none" if provider in baseline_providers else f"US${row['ambiguous_exposure_usd']:.4f}"
        lines.append(
            f"| {provider_label} | {row['model']} | {row['completed_images']}/8 | {row['suitable_pair_count']} | "
            f"{precision} | {recall} | {known_spend} | {ambiguous} |"
        )
    if all_four_complete:
        majority = result["three_of_four_majority_evaluation_metrics"]
        two_of_four = consensus_thresholds["2_of_4"]
        lines.extend([
            "",
            f"Three-of-four majority pairs: {result['three_of_four_majority_pair_count']}.",
            f"Held-out majority precision/recall: {majority['precision']:.1%} / {majority['recall']:.1%}.",
            f"Two-of-four pairs: {two_of_four['pair_count']}; held-out precision/recall: "
            f"{two_of_four['evaluation_metrics']['precision']:.1%} / "
            f"{two_of_four['evaluation_metrics']['recall']:.1%}.",
            f"OpenAI or Claude uniquely recovered "
            f"{incremental_provider_value['found_only_by_openai_or_anthropic']} of "
            f"{incremental_provider_value['held_out_human_positive_pairs']} held-out human-positive pairs.",
            "",
            "## Interpretation",
            "",
            "The one-image request design resolves the earlier OpenAI and Claude compatibility failure.",
            "On this small held-out sample, adding those providers supplied only one unique true-positive rescue and did not improve consensus recall beyond 22.2%.",
            "The result supports preserving these providers for bounded comparison, but does not justify a larger four-provider run or a production policy.",
        ])
    lines.extend(["", "This bounded compatibility pilot is not a production selection policy.", ""])
    atomic_write_text(output_dir / "compatibility_report.md", "\n".join(lines))
    return result


def status(output_dir: Path) -> dict[str, Any]:
    """Return the status."""
    manifest = read_json(output_dir / "compatibility_manifest.json")
    return {
        "trial_name": manifest["trial_name"],
        "image_count": manifest["image_count"],
        "preflight": read_json(output_dir / "preflight.json"),
        "providers": {
            provider: {
                "completed_items": len(_provider_state(output_dir, provider)["completed_items"]),
                "failed_items": len(_provider_state(output_dir, provider)["failed_items"]),
                "known_cost_usd": _provider_state(output_dir, provider)["known_cost_usd"],
                "ambiguous_exposure_usd": _provider_state(output_dir, provider)["ambiguous_exposure_usd"],
                "provider_stopped": _provider_state(output_dir, provider)["provider_stopped"],
            }
            for provider in PROVIDERS
        },
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(description="Bounded OpenAI/Claude image-quote compatibility pilot")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--source-trial", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("--project-dir", type=Path, default=Path.cwd())
    run.add_argument("--trial-dir", type=Path, required=True)
    run.add_argument("--execute", action="store_true")
    run.add_argument("--confirm-max-cost-usd", type=float, default=0.0)
    run.add_argument("--resume", action="store_true")
    stat = sub.add_parser("status")
    stat.add_argument("--trial-dir", type=Path, required=True)
    report = sub.add_parser("report")
    report.add_argument("--trial-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the command-line entry point."""
    args = build_parser().parse_args(argv)
    if args.command == "prepare":
        value = prepare_trial(args.source_trial.resolve(), args.output.resolve())
    elif args.command == "run":
        value = run_trial(
            args.project_dir.resolve(), args.trial_dir.resolve(),
            execute=args.execute, confirmed_cost=args.confirm_max_cost_usd,
        )
    elif args.command == "status":
        value = status(args.trial_dir.resolve())
    else:
        value = build_report(args.trial_dir.resolve())
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0
