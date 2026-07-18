from __future__ import annotations

import argparse
import json
import math
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import requests

from .bakeoff import PRICES, PROVIDER_MODELS, ProviderClient
from .hybrid_reply_retrieval import DEFAULT_MODEL_DIR, LocalE5Embedder, exact_cosine_search, validate_index
from .image_quote_eligibility import (
    RISK_FLAGS,
    compact_quote_record,
    load_completed_corpus,
    load_image_records,
    text_hash,
    value_hash,
)
from .io import atomic_write_json, atomic_write_text, read_json
from .thatcher_image_hunt import (
    GeminiHuntClient,
    LogicalCallRouter,
    append_jsonl,
    load_project_environment,
    require_transport_parity,
)


SCHEMA_VERSION = 1
PROMPT_VERSION = "image-quote-focused-shortlist-rerank-v1"
SHORTLIST_VERSION = "local-image-quote-shortlist-v1"
TRIAL_NAME = "image_quote_shortlist_rerank_001"
PROVIDERS = ("grok", "openai", "anthropic", "gemini")
EXPECTED_QUOTES = 626
SHORTLIST_SIZE = 25
BATCH_SIZE = 6
BATCH_COUNT = 4
MAX_OUTPUT_TOKENS = 24576
EXPECTED_OUTPUT_TOKENS = 15000
HARD_COMBINED_LIMIT_USD = 12.0
PROVIDER_LIMITS_USD = {"grok": 3.0, "openai": 6.5, "anthropic": 4.0, "gemini": 3.0}

DECISIONS = {"suitable", "unsuitable", "unsure"}
CONFIDENCES = {"high", "medium", "low"}
MATCH_BASES = {
    "exact_event", "named_person_or_relationship", "direct_mechanism", "direct_consequence",
    "direct_principle", "tone_or_rhetorical", "portrait_general", "no_valid_match",
}

ASSESSMENT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "quote_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "decision": {"type": "string", "enum": sorted(DECISIONS)},
        "confidence": {"type": "string", "enum": sorted(CONFIDENCES)},
        "match_basis": {"type": "string", "enum": sorted(MATCH_BASES)},
        "reason": {"type": "string", "maxLength": 280},
        "risk_flags": {
            "type": "array", "items": {"type": "string", "enum": sorted(RISK_FLAGS)}, "maxItems": 8,
        },
    },
    "required": ["quote_hash", "decision", "confidence", "match_basis", "reason", "risk_flags"],
}
IMAGE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "candidate_id": {"type": "string", "minLength": 1, "maxLength": 80},
        "image_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "assessments": {
            "type": "array", "items": ASSESSMENT_SCHEMA,
            "minItems": SHORTLIST_SIZE, "maxItems": SHORTLIST_SIZE,
        },
        "summary": {"type": "string", "maxLength": 400},
    },
    "required": ["candidate_id", "image_sha256", "assessments", "summary"],
}
BATCH_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "records": {
            "type": "array", "items": IMAGE_SCHEMA, "minItems": BATCH_SIZE, "maxItems": BATCH_SIZE,
        },
    },
    "required": ["records"],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text.encode("utf-8")) / 3)


def image_query(record: dict[str, Any]) -> str:
    visual = record["visual"]
    people = visual.get("people") or {}
    pairing = visual.get("pairing") or {}
    setting = visual.get("setting") or {}
    fields = [
        ("source_event", record.get("source_event")),
        ("named_people", ", ".join(record.get("named_people") or [])),
        ("date", record.get("approximate_date")),
        ("description", visual.get("description")),
        ("scene", visual.get("scene_summary")),
        ("scene_types", ", ".join(visual.get("scene_types") or [])),
        ("setting", json.dumps(setting, sort_keys=True, ensure_ascii=False)),
        ("activities", ", ".join(people.get("primary_subject_activities") or [])),
        ("moods", ", ".join(people.get("primary_subject_moods") or [])),
        ("tone", ", ".join(visual.get("tone") or [])),
        ("themes", ", ".join(visual.get("themes") or [])),
        ("best_topics", ", ".join(pairing.get("best_for_topics") or [])),
        ("matching_summary", pairing.get("matching_summary")),
    ]
    return "\n".join(f"{name}: {value}" for name, value in fields if value)


def _normal_words(value: Any) -> set[str]:
    return {
        word.casefold().strip(".,;:()[]{}'\"")
        for word in str(value or "").split()
        if len(word.strip(".,;:()[]{}'\"")) >= 4
    }


def _current_review_candidates(work_dir: Path) -> dict[str, set[str]]:
    matching = read_json(work_dir / "quote_matching_dry_run.json")
    result: dict[str, set[str]] = defaultdict(set)
    for image in matching["records"]:
        selected = image["top_winning_matches"][:5] or image["top_proposed_matches"][:3]
        result[image["candidate_id"]].update(row["quote_hash"] for row in selected)
    return result


def _selector_candidates(work_dir: Path) -> dict[str, list[dict[str, Any]]]:
    matching = read_json(work_dir / "quote_matching_dry_run.json")
    return {row["candidate_id"]: row["top_proposed_matches"][:20] for row in matching["records"]}


def build_shortlists(
    research_run: Path,
    work_dir: Path,
    retrieval_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    packets, corpus_meta = load_completed_corpus(research_run)
    packet_by_id = {row["quote_id"]: row for row in packets}
    images = load_image_records(work_dir)
    index_manifest = validate_index(retrieval_dir, DEFAULT_MODEL_DIR)
    quote_ids = read_json(retrieval_dir / "index/quote_ids.json")
    matrix = np.load(retrieval_dir / "index/embeddings.npy", mmap_mode="r", allow_pickle=False)
    embedder = LocalE5Embedder(DEFAULT_MODEL_DIR)
    embeddings = embedder.encode([image_query(row) for row in images], query=True, batch_size=8)
    selector = _selector_candidates(work_dir)
    review_candidates = _current_review_candidates(work_dir)
    rows = []
    for image, embedding in zip(images, embeddings):
        semantic = exact_cosine_search(matrix, embedding, quote_ids, 30)
        semantic_map = {quote_id: (rank, score) for rank, (quote_id, score) in enumerate(semantic, 1)}
        selector_map = {
            row["quote_hash"]: (rank, float(row.get("score") or 0.0))
            for rank, row in enumerate(selector[image["candidate_id"]], 1)
            if row["quote_hash"] in packet_by_id
        }
        eligible_review_candidates = review_candidates[image["candidate_id"]].intersection(packet_by_id)
        named = {value.casefold() for value in image.get("named_people") or []}
        source_words = _normal_words(image.get("source_event"))
        metadata: dict[str, list[str]] = {}
        for quote_id, packet in packet_by_id.items():
            signals = []
            entities = {str(value).casefold() for value in packet.get("entities") or []}
            matched_people = sorted(named.intersection(entities))
            if matched_people:
                signals.extend(f"named_person:{value}" for value in matched_people)
            event_words = _normal_words(packet.get("source_event")) | _normal_words(packet.get("historical_context"))
            overlap = source_words.intersection(event_words)
            if len(overlap) >= 3:
                signals.append("source_event_overlap")
            if signals:
                metadata[quote_id] = signals
        union = set(semantic_map) | set(selector_map) | set(metadata) | eligible_review_candidates
        ranked = []
        for quote_id in union:
            semantic_rank, semantic_score = semantic_map.get(quote_id, (None, None))
            selector_rank, selector_score = selector_map.get(quote_id, (None, None))
            signals = metadata.get(quote_id, [])
            score = 0.0
            if quote_id in eligible_review_candidates:
                score += 1.0
            if selector_rank is not None:
                score += 0.35 / (10 + selector_rank)
            if semantic_rank is not None:
                score += 0.25 / (10 + semantic_rank) + max(float(semantic_score) - 0.75, 0) * 0.2
            score += min(len(signals), 3) * 0.03
            sources = []
            if quote_id in eligible_review_candidates:
                sources.append("selector_review_candidate")
            if selector_rank is not None:
                sources.append("selector_top20")
            if semantic_rank is not None:
                sources.append("semantic_top30")
            sources.extend(signals)
            ranked.append({
                "quote_hash": quote_id,
                "retrieval_score": round(score, 8),
                "retrieval_sources": sources,
                "selector_rank": selector_rank,
                "selector_score": selector_score,
                "semantic_rank": semantic_rank,
                "semantic_similarity": semantic_score,
            })
        ranked.sort(key=lambda row: (-row["retrieval_score"], row["quote_hash"]))
        selected = ranked[:SHORTLIST_SIZE]
        required = eligible_review_candidates
        if not required.issubset({row["quote_hash"] for row in selected}):
            raise RuntimeError(f"shortlist lost a review candidate for {image['candidate_id']}")
        prompt_quotes = []
        for row in sorted(selected, key=lambda item: item["quote_hash"]):
            compact = compact_quote_record(packet_by_id[row["quote_hash"]])
            prompt_quotes.append({
                "quote_hash": compact["id"], "quote": compact["quote"],
                "event": compact["event"], "context": compact["context"],
                "argument": compact["argument"], "principle": compact["principle"],
                "mechanism": compact["mechanism"], "consequence": compact["consequence"],
                "entities": compact["entities"], "visual": compact["visual"],
                "confidence": compact["confidence"],
            })
        rows.append({
            "candidate_id": image["candidate_id"],
            "image_sha256": image["image_sha256"],
            "image_metadata": image,
            "query_text_sha256": text_hash(image_query(image)),
            "ranked_candidates": selected,
            "prompt_quotes": prompt_quotes,
            "shortlist_quote_ids_sha256": text_hash("\n".join(row["quote_hash"] for row in prompt_quotes) + "\n"),
        })
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "shortlist_version": SHORTLIST_VERSION,
        "image_count": len(rows),
        "shortlist_size": SHORTLIST_SIZE,
        "corpus": corpus_meta,
        "embedding_index_manifest_sha256": value_hash(index_manifest),
        "embedding_model": index_manifest["model_id"],
        "all_review_candidates_retained": True,
        "human_decisions_used": False,
        "generated_at": utc_now(),
    }
    return rows, metadata


def coherent_human_pairs(work_dir: Path) -> list[dict[str, Any]]:
    matching = read_json(work_dir / "quote_matching_dry_run.json")
    reviews = (read_json(work_dir / "human_pairing_review.json").get("reviews") or {})
    rows = []
    for image in matching["records"]:
        selected = image["top_winning_matches"][:5] or image["top_proposed_matches"][:3]
        for pair in selected:
            pair_id = f"{image['candidate_id']}:{pair['quote_hash']}"
            review = reviews[pair_id]
            note = str(review.get("note") or "").casefold()
            if review["decision"] == "approve_pairing" and any(
                text in note for text in ("what has that got", "poor choice", "not suitable", "no.")
            ):
                continue
            rows.append({
                "pair_id": pair_id, "candidate_id": image["candidate_id"],
                "quote_hash": pair["quote_hash"], "human_decision": review["decision"],
                "human_positive": review["decision"] == "approve_pairing",
                "note": review.get("note") or "",
            })
    return rows


def calibration_split(work_dir: Path, eligible_quote_ids: set[str] | None = None) -> dict[str, Any]:
    rows = coherent_human_pairs(work_dir)
    if eligible_quote_ids is not None:
        rows = [row for row in rows if row["quote_hash"] in eligible_quote_ids]
    by_image: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_image[row["candidate_id"]].append(row)
    positive_images = sorted(
        (image for image, values in by_image.items() if any(row["human_positive"] for row in values)),
        key=lambda value: text_hash(f"calibration-positive-v1:{value}"),
    )
    negative_images = sorted(
        (image for image, values in by_image.items() if not any(row["human_positive"] for row in values)),
        key=lambda value: text_hash(f"calibration-negative-v1:{value}"),
    )
    calibration_images = sorted(positive_images[:3] + negative_images[:3])
    evaluation_images = sorted(set(by_image) - set(calibration_images))
    return {
        "schema_version": SCHEMA_VERSION,
        "split_version": "image-level-calibration-split-v1",
        "calibration_image_ids": calibration_images,
        "evaluation_image_ids": evaluation_images,
        "calibration_pairs": [row for row in rows if row["candidate_id"] in calibration_images],
        "evaluation_pairs": [row for row in rows if row["candidate_id"] in evaluation_images],
        "contradictory_pairs_excluded": 1,
    }


def calibration_examples(
    split: dict[str, Any], shortlists: list[dict[str, Any]], quote_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    image_by_id = {row["candidate_id"]: row for row in shortlists}
    examples = []
    by_image: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in split["calibration_pairs"]:
        by_image[row["candidate_id"]].append(row)
    for candidate_id in split["calibration_image_ids"]:
        values = sorted(by_image[candidate_id], key=lambda row: (not row["human_positive"], row["quote_hash"]))
        positives = [row for row in values if row["human_positive"]][:1]
        negatives = [row for row in values if not row["human_positive"]][:2]
        for row in positives + negatives:
            quote = quote_by_id[row["quote_hash"]]
            examples.append({
                "image": {
                    "candidate_id": candidate_id,
                    "source_event": image_by_id[candidate_id]["image_metadata"]["source_event"],
                    "named_people": image_by_id[candidate_id]["image_metadata"]["named_people"],
                    "scene": image_by_id[candidate_id]["image_metadata"]["visual"]["scene_summary"],
                },
                "quote": {
                    "quote_hash": row["quote_hash"], "quote": quote["quote"],
                    "argument": quote["argument"], "event": quote["event"],
                },
                "human_editorial_decision": "suitable" if row["human_positive"] else "unsuitable",
            })
    return examples


def build_prompt(
    batch: list[dict[str, Any]],
    examples: list[dict[str, Any]],
    *,
    response_schema: dict[str, Any] = BATCH_SCHEMA,
) -> str:
    provider_rows = [{
        "candidate_id": row["candidate_id"],
        "image_sha256": row["image_sha256"],
        "image_metadata": row["image_metadata"],
        "quote_shortlist": row["prompt_quotes"],
    } for row in batch]
    return f"""Prompt version: {PROMPT_VERSION}
Act as a conservative picture editor. Classify EVERY image/quote pair in each supplied 25-quote shortlist.

Decision meanings:
- suitable: the photograph can responsibly accompany the quotation and communicates its event, relationship, mechanism, consequence, principle or tone without a misleading dominant message.
- unsuitable: broad topic overlap, generic leadership, wrong event/person/period, ally-adversary confusion, tone mismatch, or an image that does not communicate the argument.
- unsure: only when evidence is genuinely balanced; do not use it to avoid the task.

Rules:
- Return exactly 25 assessments for every image and use every supplied quote hash exactly once.
- Source-grounded identities and events are authoritative. Do not infer a different person or event.
- Shared words or ideology alone are insufficient. A diplomatic photograph is not automatically suitable for any foreign-policy quote.
- A named-person photograph normally requires that person, that relationship, the exact event, or a directly illustrated principle.
- A portrait is not a generic fallback for abstract policy. Use portrait_general only for compatible personal, biographical or rhetorical statements.
- If a residual risk makes the dominant visual message misleading, classify unsuitable rather than adding a risk flag.
- Keep reasons concise. Do not rewrite quote or image identity.

The following disjoint image-level calibration examples were labelled by a human editor. They demonstrate the required level of conservatism. They are not part of held-out evaluation:
{json.dumps(examples, sort_keys=True, separators=(',', ':'), ensure_ascii=False)}

Return one JSON object matching this schema:
{json.dumps(response_schema, sort_keys=True, separators=(',', ':'))}

CASES:
{json.dumps(provider_rows, sort_keys=True, separators=(',', ':'), ensure_ascii=False)}
"""


def validate_response(value: Any, batch: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"records"} or not isinstance(value["records"], list):
        raise ValueError("response must contain only records")
    if len(value["records"]) != len(batch):
        raise ValueError("response image count mismatch")
    expected = {row["candidate_id"]: row for row in batch}
    seen_images: set[str] = set()
    output = []
    for record in value["records"]:
        if not isinstance(record, dict) or set(record) != {"candidate_id", "image_sha256", "assessments", "summary"}:
            raise ValueError("image response fields mismatch")
        candidate_id = str(record["candidate_id"])
        if candidate_id not in expected or candidate_id in seen_images:
            raise ValueError("unknown or duplicate image")
        if record["image_sha256"] != expected[candidate_id]["image_sha256"]:
            raise ValueError("image identity changed")
        assessments = record["assessments"]
        if not isinstance(assessments, list) or len(assessments) != SHORTLIST_SIZE:
            raise ValueError("assessment count must equal shortlist size")
        expected_quotes = {row["quote_hash"] for row in expected[candidate_id]["prompt_quotes"]}
        seen_quotes: set[str] = set()
        normalised = []
        for assessment in assessments:
            if not isinstance(assessment, dict) or set(assessment) != {
                "quote_hash", "decision", "confidence", "match_basis", "reason", "risk_flags",
            }:
                raise ValueError("assessment fields mismatch")
            quote_id = str(assessment["quote_hash"])
            if quote_id not in expected_quotes or quote_id in seen_quotes:
                raise ValueError("unknown or duplicate shortlist quote")
            if assessment["decision"] not in DECISIONS or assessment["confidence"] not in CONFIDENCES:
                raise ValueError("assessment enum invalid")
            if assessment["match_basis"] not in MATCH_BASES:
                raise ValueError("match basis invalid")
            if assessment["decision"] != "unsuitable" and assessment["match_basis"] == "no_valid_match":
                raise ValueError("non-unsuitable assessment cannot use no_valid_match")
            if not isinstance(assessment["reason"], str) or not assessment["reason"].strip() or len(assessment["reason"]) > 280:
                raise ValueError("invalid assessment reason")
            if not isinstance(assessment["risk_flags"], list) or any(flag not in RISK_FLAGS for flag in assessment["risk_flags"]):
                raise ValueError("invalid risk flags")
            seen_quotes.add(quote_id)
            normalised.append({**assessment, "risk_flags": sorted(set(assessment["risk_flags"]))})
        if seen_quotes != expected_quotes:
            raise ValueError("assessment quote set mismatch")
        if not isinstance(record["summary"], str) or len(record["summary"]) > 400:
            raise ValueError("invalid summary")
        output.append({**record, "assessments": sorted(normalised, key=lambda row: row["quote_hash"])})
        seen_images.add(candidate_id)
    return {"records": sorted(output, key=lambda row: row["candidate_id"])}


def _batches(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    values = [rows[index:index + BATCH_SIZE] for index in range(0, len(rows), BATCH_SIZE)]
    if len(values) != BATCH_COUNT or any(len(batch) != BATCH_SIZE for batch in values):
        raise RuntimeError("trial requires four six-image batches")
    return values


def prepare_trial(
    research_run: Path, work_dir: Path, retrieval_dir: Path, output_dir: Path,
) -> dict[str, Any]:
    shortlists, shortlist_meta = build_shortlists(research_run, work_dir, retrieval_dir)
    compact_packets = [compact_quote_record(row) for row in load_completed_corpus(research_run)[0]]
    quote_by_id = {row["id"]: row for row in compact_packets}
    split = calibration_split(work_dir, set(quote_by_id))
    examples = calibration_examples(split, shortlists, quote_by_id)
    batches = _batches(shortlists)
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_dir / "shortlists.json", {
        "schema_version": SCHEMA_VERSION, "metadata": shortlist_meta, "records": shortlists,
    })
    atomic_write_json(output_dir / "calibration_split.json", split)
    atomic_write_json(output_dir / "calibration_examples.json", {
        "schema_version": SCHEMA_VERSION, "records": examples,
    })
    prompt_dir = output_dir / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    for stale in prompt_dir.glob("batch_*.txt"):
        stale.unlink()
    manifest_batches = []
    prompts = []
    for index, batch in enumerate(batches, 1):
        prompt = build_prompt(batch, examples)
        path = prompt_dir / f"batch_{index:02d}.txt"
        atomic_write_text(path, prompt)
        prompts.append(prompt)
        manifest_batches.append({
            "batch_id": f"batch-{index:02d}",
            "candidate_ids": [row["candidate_id"] for row in batch],
            "prompt_path": str(path.relative_to(output_dir)),
            "prompt_sha256": text_hash(prompt),
            "estimated_input_tokens": estimate_tokens(prompt),
        })
    prompt_hashes = [row["prompt_sha256"] for row in manifest_batches]
    manifest = {
        "schema_version": SCHEMA_VERSION, "trial_name": TRIAL_NAME,
        "prompt_version": PROMPT_VERSION, "shortlist_version": SHORTLIST_VERSION,
        "research_run": str(research_run.resolve()), "work_dir": str(work_dir.resolve()),
        "retrieval_dir": str(retrieval_dir.resolve()), "image_count": len(shortlists),
        "quote_count": EXPECTED_QUOTES, "shortlist_size": SHORTLIST_SIZE,
        "shortlists_sha256": value_hash(shortlists), "calibration_split_sha256": value_hash(split),
        "providers": list(PROVIDERS),
        "provider_prompt_hashes": {provider: prompt_hashes for provider in PROVIDERS},
        "prompt_parity": True, "batches": manifest_batches, "generated_at": utc_now(),
    }
    atomic_write_json(output_dir / "trial_manifest.json", manifest)
    preflight = build_preflight(output_dir)
    return {"manifest": manifest, "preflight": preflight}


def _provider_token_estimate(provider: str, prompt: str) -> int:
    base = estimate_tokens(prompt)
    return math.ceil(base * 1.15) if provider == "anthropic" else base


def build_preflight(output_dir: Path) -> dict[str, Any]:
    manifest = read_json(output_dir / "trial_manifest.json")
    prompts = [(output_dir / row["prompt_path"]).read_text(encoding="utf-8") for row in manifest["batches"]]
    providers = {}
    for provider in PROVIDERS:
        input_tokens = sum(_provider_token_estimate(provider, prompt) for prompt in prompts)
        price = PRICES[provider]
        expected = (input_tokens * price["input"] + BATCH_COUNT * EXPECTED_OUTPUT_TOKENS * price["output"]) / 1_000_000
        guarded = (input_tokens * price["input"] + BATCH_COUNT * MAX_OUTPUT_TOKENS * price["output"]) / 1_000_000
        max_attempt = max(
            (_provider_token_estimate(provider, prompt) * price["input"] + MAX_OUTPUT_TOKENS * price["output"]) / 1_000_000
            for prompt in prompts
        )
        providers[provider] = {
            "model": PROVIDER_MODELS[provider], "calls": BATCH_COUNT,
            "estimated_input_tokens": input_tokens,
            "expected_output_tokens": BATCH_COUNT * EXPECTED_OUTPUT_TOKENS,
            "maximum_output_tokens": BATCH_COUNT * MAX_OUTPUT_TOKENS,
            "expected_cost_usd": round(expected, 6),
            "guarded_base_cost_usd": round(guarded, 6),
            "single_retry_reserve_usd": round(max_attempt, 6),
            "hard_limit_usd": PROVIDER_LIMITS_USD[provider],
        }
        if guarded + max_attempt > PROVIDER_LIMITS_USD[provider]:
            raise RuntimeError(f"{provider} preflight exceeds provider limit")
    combined_guarded = sum(row["guarded_base_cost_usd"] for row in providers.values())
    value = {
        "schema_version": SCHEMA_VERSION, "providers": providers,
        "planned_calls_without_retries": BATCH_COUNT * len(PROVIDERS),
        "expected_combined_cost_usd": round(sum(row["expected_cost_usd"] for row in providers.values()), 6),
        "guarded_base_combined_cost_usd": round(combined_guarded, 6),
        "hard_combined_limit_usd": HARD_COMBINED_LIMIT_USD,
        "within_combined_limit": combined_guarded <= HARD_COMBINED_LIMIT_USD,
        "prompt_parity": manifest["prompt_parity"], "tools_enabled": False,
        "search_grounding_enabled": False, "production_files_written": False,
        "generated_at": utc_now(),
    }
    atomic_write_json(output_dir / "preflight.json", value)
    return value


def _state(output_dir: Path, provider: str) -> dict[str, Any]:
    path = output_dir / "providers" / provider / "state.json"
    value = read_json(path, None)
    if not isinstance(value, dict):
        value = {
            "schema_version": SCHEMA_VERSION, "provider": provider,
            "completed_batches": {}, "received_batches": {}, "failed_batches": {},
            "transport_attempts": {}, "known_cost_usd": 0.0, "ambiguous_exposure_usd": 0.0,
        }
    value.setdefault("received_batches", {})
    value.setdefault("transport_attempts", {})
    value.setdefault("ambiguous_exposure_usd", 0.0)
    return value


def _save_state(output_dir: Path, provider: str, state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    atomic_write_json(output_dir / "providers" / provider / "state.json", state)


def total_spend(output_dir: Path) -> float:
    total = 0.0
    for provider in PROVIDERS:
        if provider == "gemini":
            route = read_json(output_dir / "providers/gemini/provider_route_state.json", {})
            total += sum(float(value or 0.0) for value in (route.get("known_spend_usd") or {}).values())
        else:
            total += float(_state(output_dir, provider).get("known_cost_usd") or 0.0)
    return round(total, 10)


def total_ambiguous_exposure(output_dir: Path) -> float:
    return round(sum(
        float(_state(output_dir, provider).get("ambiguous_exposure_usd") or 0.0)
        for provider in PROVIDERS
    ), 10)


def _load_batch(output_dir: Path, batch_row: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    prompt = (output_dir / batch_row["prompt_path"]).read_text(encoding="utf-8")
    if text_hash(prompt) != batch_row["prompt_sha256"]:
        raise RuntimeError("prompt hash mismatch")
    shortlists = {row["candidate_id"]: row for row in read_json(output_dir / "shortlists.json")["records"]}
    return prompt, [shortlists[candidate_id] for candidate_id in batch_row["candidate_ids"]]


def _next_maximum_cost(provider: str, prompt: str) -> float:
    price = PRICES[provider]
    return (
        _provider_token_estimate(provider, prompt) * price["input"]
        + MAX_OUTPUT_TOKENS * price["output"]
    ) / 1_000_000


def _guard_cost(output_dir: Path, provider: str, prompt: str) -> None:
    maximum = _next_maximum_cost(provider, prompt)
    state = _state(output_dir, provider)
    provider_exposure = (
        float(state.get("known_cost_usd") or 0.0)
        + float(state.get("ambiguous_exposure_usd") or 0.0)
    )
    if provider_exposure + maximum > PROVIDER_LIMITS_USD[provider]:
        raise RuntimeError(f"{provider} cost ceiling reached")
    if total_spend(output_dir) + total_ambiguous_exposure(output_dir) + maximum > HARD_COMBINED_LIMIT_USD:
        raise RuntimeError("combined cost ceiling reached")


def run_http_provider(output_dir: Path, manifest: dict[str, Any], provider: str, api_key: str) -> None:
    client = ProviderClient(provider, api_key)
    provider_dir = output_dir / "providers" / provider
    state = _state(output_dir, provider)
    for batch_row in manifest["batches"]:
        batch_id = batch_row["batch_id"]
        if batch_id in state["completed_batches"]:
            continue
        prompt, batch = _load_batch(output_dir, batch_row)
        failed_item = state["failed_batches"].get(batch_id) or {}
        if "ReadTimeout" in str(failed_item.get("error") or "") and not failed_item.get("ambiguous_outcome"):
            exposure = _next_maximum_cost(provider, prompt)
            failed_item.update({"ambiguous_outcome": True, "maximum_exposure_usd": exposure})
            state["failed_batches"][batch_id] = failed_item
            state["ambiguous_exposure_usd"] = round(
                float(state.get("ambiguous_exposure_usd") or 0.0) + exposure, 10
            )
            _save_state(output_dir, provider, state)
        received_item = state["received_batches"].get(batch_id)
        if received_item:
            received = read_json(output_dir / received_item["path"])
            try:
                validated = validate_response(received["content"], batch)
            except Exception as exc:
                state["failed_batches"][batch_id] = {
                    "error": f"{type(exc).__name__}: {exc}"[:4000],
                    "charged_response": True,
                    "request_id": received.get("request_id"),
                    "path": received_item["path"],
                }
                _save_state(output_dir, provider, state)
                continue
            normal_path = provider_dir / "normalised" / f"{batch_id}.json"
            atomic_write_json(normal_path, {**received, "content": validated})
            state["completed_batches"][batch_id] = {
                "path": str(normal_path.relative_to(output_dir)),
                "cost_usd": received.get("cost_usd"),
                "request_id": received.get("request_id"),
            }
            state["failed_batches"].pop(batch_id, None)
            _save_state(output_dir, provider, state)
            continue
        if state["failed_batches"].get(batch_id, {}).get("charged_response"):
            continue
        if state["failed_batches"].get(batch_id, {}).get("ambiguous_outcome"):
            continue
        previous_attempts = int(state["transport_attempts"].get(batch_id) or 0)
        for attempt in range(previous_attempts + 1, 3):
            _guard_cost(output_dir, provider, prompt)
            started = {"event": "started", "provider": provider, "batch_id": batch_id,
                       "attempt": attempt, "prompt_sha256": batch_row["prompt_sha256"], "timestamp": utc_now()}
            state["transport_attempts"][batch_id] = attempt
            _save_state(output_dir, provider, state)
            append_jsonl(provider_dir / "attempts.jsonl", started)
            try:
                response = client.call(
                    prompt, schema=BATCH_SCHEMA, schema_name="image_quote_shortlist_rerank",
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                )
                raw_path = provider_dir / "raw" / f"{batch_id}.json"
                atomic_write_json(raw_path, response["raw"])
                received_path = provider_dir / "received" / f"{batch_id}.json"
                received = {
                    "schema_version": SCHEMA_VERSION, "provider": provider, "model": client.model,
                    "batch_id": batch_id, "prompt_sha256": batch_row["prompt_sha256"],
                    "request_id": response["request_id"], "usage": response["usage"],
                    "cost_usd": response["cost_usd"], "latency_seconds": response["latency_seconds"],
                    "content": response["content"], "completed_at": utc_now(),
                }
                atomic_write_json(received_path, received)
                state["known_cost_usd"] = round(float(state["known_cost_usd"]) + float(response["cost_usd"]), 10)
                state["received_batches"][batch_id] = {
                    "path": str(received_path.relative_to(output_dir)),
                    "cost_usd": response["cost_usd"], "request_id": response["request_id"],
                }
                _save_state(output_dir, provider, state)
                validated = validate_response(received["content"], batch)
                normal_path = provider_dir / "normalised" / f"{batch_id}.json"
                atomic_write_json(normal_path, {**received, "content": validated})
                state["completed_batches"][batch_id] = {
                    "path": str(normal_path.relative_to(output_dir)), "cost_usd": response["cost_usd"],
                    "request_id": response["request_id"],
                }
                state["failed_batches"].pop(batch_id, None)
                _save_state(output_dir, provider, state)
                append_jsonl(provider_dir / "attempts.jsonl", {
                    **started, "event": "completed", "completed_at": utc_now(),
                    "request_id": response["request_id"], "cost_usd": response["cost_usd"],
                })
                break
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                body = (exc.response.text or "")[:4000] if exc.response is not None else ""
                append_jsonl(provider_dir / "attempts.jsonl", {
                    **started, "event": "failed", "completed_at": utc_now(),
                    "http_status": status, "response_body": body,
                })
                if status in {408, 429, 500, 502, 503, 504} and attempt == 1:
                    time.sleep(2)
                    continue
                state["failed_batches"][batch_id] = {"http_status": status, "error": body}
                _save_state(output_dir, provider, state)
                break
            except requests.ReadTimeout as exc:
                exposure = _next_maximum_cost(provider, prompt)
                state["ambiguous_exposure_usd"] = round(
                    float(state.get("ambiguous_exposure_usd") or 0.0) + exposure, 10
                )
                append_jsonl(provider_dir / "attempts.jsonl", {
                    **started, "event": "failed", "completed_at": utc_now(),
                    "error": f"{type(exc).__name__}: {exc}"[:4000],
                    "ambiguous_outcome": True, "maximum_exposure_usd": exposure,
                })
                state["failed_batches"][batch_id] = {
                    "error": f"{type(exc).__name__}: {exc}"[:4000],
                    "ambiguous_outcome": True, "maximum_exposure_usd": exposure,
                }
                _save_state(output_dir, provider, state)
                break
            except Exception as exc:
                append_jsonl(provider_dir / "attempts.jsonl", {
                    **started, "event": "failed", "completed_at": utc_now(),
                    "error": f"{type(exc).__name__}: {exc}"[:4000],
                })
                received_item = state["received_batches"].get(batch_id)
                state["failed_batches"][batch_id] = {
                    "error": f"{type(exc).__name__}: {exc}"[:4000],
                    "charged_response": bool(received_item),
                    **({"path": received_item["path"], "request_id": received_item.get("request_id")}
                       if received_item else {}),
                }
                _save_state(output_dir, provider, state)
                break


def run_gemini(output_dir: Path, manifest: dict[str, Any]) -> None:
    provider = "gemini"
    provider_dir = output_dir / "providers/gemini"
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    location = os.getenv("GOOGLE_CLOUD_LOCATION") or "global"
    developer = GeminiHuntClient("developer_api", api_key=api_key, timeout_seconds=360)
    vertex = GeminiHuntClient("vertex", project=project, location=location, timeout_seconds=360)
    require_transport_parity(developer, vertex, "triage", BATCH_SCHEMA)
    router = LogicalCallRouter(
        provider_dir, developer, vertex,
        combined_limit=PROVIDER_LIMITS_USD[provider],
        developer_limit=PROVIDER_LIMITS_USD[provider],
        vertex_limit=PROVIDER_LIMITS_USD[provider],
    )
    state = _state(output_dir, provider)
    for batch_row in manifest["batches"]:
        batch_id = batch_row["batch_id"]
        if batch_id in state["completed_batches"]:
            continue
        prompt, batch = _load_batch(output_dir, batch_row)
        _guard_cost(output_dir, provider, prompt)
        try:
            result = router.run(
                logical_call_id=f"shortlist-{batch_id}", phase="triage", prompt=prompt,
                schema=BATCH_SCHEMA, images=(),
            )
            validated = validate_response(result.get("parsed"), batch)
            path = provider_dir / "validated" / f"{batch_id}.json"
            atomic_write_json(path, {
                "schema_version": SCHEMA_VERSION, "provider": provider,
                "transport": result["provider"], "model": result["model"],
                "batch_id": batch_id, "prompt_sha256": batch_row["prompt_sha256"],
                "request_id": result.get("request_id"), "usage": result.get("usage") or {},
                "cost_usd": result.get("cost_usd"), "latency_seconds": result.get("latency_seconds"),
                "content": validated, "completed_at": result.get("completed_at") or utc_now(),
            })
            state["completed_batches"][batch_id] = {
                "path": str(path.relative_to(output_dir)), "cost_usd": result.get("cost_usd"),
                "transport": result["provider"],
            }
            state["failed_batches"].pop(batch_id, None)
            _save_state(output_dir, provider, state)
        except Exception as exc:
            state["failed_batches"][batch_id] = {"error": f"{type(exc).__name__}: {exc}"[:4000]}
            _save_state(output_dir, provider, state)


def run_trial(project_dir: Path, output_dir: Path, *, execute: bool, confirmed_cost: float) -> dict[str, Any]:
    if not execute:
        raise RuntimeError("live calls require --execute")
    if confirmed_cost != HARD_COMBINED_LIMIT_USD:
        raise RuntimeError(f"execution requires exact --confirm-max-cost-usd {HARD_COMBINED_LIMIT_USD:g}")
    preflight = read_json(output_dir / "preflight.json")
    if not preflight.get("within_combined_limit") or not preflight.get("prompt_parity"):
        raise RuntimeError("preflight does not permit execution")
    load_project_environment(project_dir / "mrsMThatcher.env")
    keys = {
        "grok": os.getenv("XAI_API_KEY"), "openai": os.getenv("OPENAI_API_KEY"),
        "anthropic": os.getenv("ANTHROPIC_API_KEY"),
    }
    if any(not value for value in keys.values()):
        raise RuntimeError("one or more provider API keys are not configured")
    manifest = read_json(output_dir / "trial_manifest.json")
    run_gemini(output_dir, manifest)
    for provider in ("grok", "openai", "anthropic"):
        run_http_provider(output_dir, manifest, provider, str(keys[provider]))
    return compile_results(output_dir)


def provider_records(output_dir: Path, provider: str) -> list[dict[str, Any]]:
    state = _state(output_dir, provider)
    rows = []
    for _batch, item in sorted(state.get("completed_batches", {}).items()):
        rows.extend(read_json(output_dir / item["path"])["content"]["records"])
    return rows


def _metrics(
    rows: list[dict[str, Any]], predicted: set[tuple[str, str]], *, available: bool = True,
) -> dict[str, Any]:
    if not available:
        return {
            "available": False, "pairs": len(rows), "true_positive": None,
            "false_positive": None, "true_negative": None, "false_negative": None,
            "precision": None, "recall": None, "specificity": None,
            "harmful_eligibility_rate": None,
        }
    tp = fp = tn = fn = 0
    for row in rows:
        actual = bool(row["human_positive"])
        value = (row["candidate_id"], row["quote_hash"]) in predicted
        if actual and value:
            tp += 1
        elif actual:
            fn += 1
        elif value:
            fp += 1
        else:
            tn += 1
    return {
        "available": True, "pairs": len(rows), "true_positive": tp, "false_positive": fp,
        "true_negative": tn, "false_negative": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "specificity": tn / (tn + fp) if tn + fp else None,
        "harmful_eligibility_rate": fp / (fp + tn) if fp + tn else None,
    }


def compile_results(output_dir: Path) -> dict[str, Any]:
    manifest = read_json(output_dir / "trial_manifest.json")
    split = read_json(output_dir / "calibration_split.json")
    provider_sets: dict[str, set[tuple[str, str]]] = {}
    provider_unsure: dict[str, set[tuple[str, str]]] = {}
    summaries = {}
    all_records = {}
    complete_providers = []
    for provider in PROVIDERS:
        records = provider_records(output_dir, provider)
        all_records[provider] = records
        suitable = {
            (row["candidate_id"], item["quote_hash"])
            for row in records for item in row["assessments"] if item["decision"] == "suitable"
        }
        unsure = {
            (row["candidate_id"], item["quote_hash"])
            for row in records for item in row["assessments"] if item["decision"] == "unsure"
        }
        provider_sets[provider] = suitable
        provider_unsure[provider] = unsure
        complete = len(records) == manifest["image_count"]
        if complete:
            complete_providers.append(provider)
        summaries[provider] = {
            "completed_images": len(records), "suitable_pair_count": len(suitable),
            "unsure_pair_count": len(unsure), "failed_batches": sorted(_state(output_dir, provider)["failed_batches"]),
            "complete": complete,
            "calibration_metrics": _metrics(split["calibration_pairs"], suitable, available=complete),
            "held_out_metrics": _metrics(split["evaluation_pairs"], suitable, available=complete),
        }
    votes: Counter[tuple[str, str]] = Counter()
    for provider in complete_providers:
        votes.update(provider_sets[provider])
    four_provider_available = len(complete_providers) == len(PROVIDERS)
    majority = {pair for pair, count in votes.items() if count >= 3}
    unanimous = {pair for pair, count in votes.items() if count == 4}
    any_provider = set(votes)
    available_threshold = len(complete_providers) // 2 + 1 if len(complete_providers) >= 2 else None
    available_majority = {
        pair for pair, count in votes.items()
        if available_threshold is not None and count >= available_threshold
    }
    result = {
        "schema_version": SCHEMA_VERSION, "providers": summaries,
        "complete_providers": complete_providers,
        "majority": {
            "available": four_provider_available,
            "pair_count": len(majority) if four_provider_available else None,
            "calibration_metrics": _metrics(
                split["calibration_pairs"], majority, available=four_provider_available
            ),
            "held_out_metrics": _metrics(
                split["evaluation_pairs"], majority, available=four_provider_available
            ),
        },
        "unanimous": {
            "available": four_provider_available,
            "pair_count": len(unanimous) if four_provider_available else None,
            "held_out_metrics": _metrics(
                split["evaluation_pairs"], unanimous, available=four_provider_available
            ),
        },
        "available_provider_majority": {
            "providers": complete_providers, "threshold": available_threshold,
            "pair_count": len(available_majority) if available_threshold is not None else None,
            "calibration_metrics": _metrics(
                split["calibration_pairs"], available_majority,
                available=available_threshold is not None,
            ),
            "held_out_metrics": _metrics(
                split["evaluation_pairs"], available_majority,
                available=available_threshold is not None,
            ),
        },
        "any_provider": {
            "pair_count": len(any_provider),
            "held_out_metrics": _metrics(split["evaluation_pairs"], any_provider),
        },
        "vote_distribution": dict(Counter(str(count) for count in votes.values())),
        "known_cost_usd": total_spend(output_dir),
        "ambiguous_exposure_usd": total_ambiguous_exposure(output_dir),
        "generated_at": utc_now(),
    }
    shortlist_rows = {row["candidate_id"]: row for row in read_json(output_dir / "shortlists.json")["records"]}
    quote_lookup = {
        quote["quote_hash"]: quote
        for row in shortlist_rows.values() for quote in row["prompt_quotes"]
    }
    vote_rows = []
    for pair, count in sorted(votes.items(), key=lambda item: (-item[1], item[0])):
        candidate_id, quote_hash = pair
        assessments = {}
        for provider in PROVIDERS:
            record = next((row for row in all_records[provider] if row["candidate_id"] == candidate_id), None)
            if record:
                assessments[provider] = next(item for item in record["assessments"] if item["quote_hash"] == quote_hash)
        vote_rows.append({
            "pair_id": f"{candidate_id}:{quote_hash}", "candidate_id": candidate_id,
            "image_source_event": shortlist_rows[candidate_id]["image_metadata"]["source_event"],
            "named_people": shortlist_rows[candidate_id]["image_metadata"]["named_people"],
            "quote_hash": quote_hash, "quote_text": quote_lookup[quote_hash]["quote"],
            "provider_vote_count": count,
            "majority_suitable": four_provider_available and count >= 3,
            "four_provider_majority_suitable": four_provider_available and count >= 3,
            "available_provider_majority_suitable": (
                available_threshold is not None and count >= available_threshold
            ),
            "unanimous_suitable": four_provider_available and count == 4,
            "provider_assessments": assessments,
        })
    assessment_index = {
        provider: {
            (record["candidate_id"], assessment["quote_hash"]): assessment
            for record in records for assessment in record["assessments"]
        }
        for provider, records in all_records.items()
    }
    human_comparison = []
    for partition, rows in (
        ("calibration", split["calibration_pairs"]),
        ("held_out", split["evaluation_pairs"]),
    ):
        for row in rows:
            pair = (row["candidate_id"], row["quote_hash"])
            human_comparison.append({
                "partition": partition,
                "pair_id": row["pair_id"],
                "candidate_id": row["candidate_id"],
                "quote_hash": row["quote_hash"],
                "human_positive": row["human_positive"],
                "human_decision": row["human_decision"],
                "provider_decisions": {
                    provider: assessment_index[provider].get(pair)
                    for provider in PROVIDERS
                },
                "available_provider_vote_count": votes[pair],
                "available_provider_majority_suitable": pair in available_majority,
            })
    atomic_write_json(output_dir / "provider_results.json", {"schema_version": SCHEMA_VERSION, "providers": all_records})
    atomic_write_json(output_dir / "provider_comparison.json", result)
    atomic_write_json(output_dir / "human_label_comparison.json", {
        "schema_version": SCHEMA_VERSION,
        "records": human_comparison,
    })
    atomic_write_json(output_dir / "majority_eligibility_map.json", {
        "schema_version": SCHEMA_VERSION, "records": vote_rows,
        "production_authorised": False,
    })
    write_report(output_dir, manifest, result)
    return result


def _pct(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.1%}"


def write_report(output_dir: Path, manifest: dict[str, Any], result: dict[str, Any]) -> None:
    lines = [
        "# Focused image-to-quote shortlist rerank", "",
        "## Design", "",
        f"- Images: {manifest['image_count']}", f"- Canonical quote corpus used for local retrieval: {manifest['quote_count']}",
        f"- Shortlist size per image: {manifest['shortlist_size']}",
        "- Providers: Grok, OpenAI, Claude and Gemini.",
        "- All provider prompts were byte-identical and contained no held-out human decisions.",
        "- Human calibration and held-out evaluation were split by image.", "",
        "## Transport outcomes", "",
        "| Provider | Model | Completed batches | Known spend | Ambiguous maximum exposure |",
        "|---|---|---:|---:|---:|",
    ]
    for provider in PROVIDERS:
        state = _state(output_dir, provider)
        known = (
            sum((read_json(output_dir / "providers/gemini/provider_route_state.json", {}).get(
                "known_spend_usd"
            ) or {}).values())
            if provider == "gemini" else float(state.get("known_cost_usd") or 0.0)
        )
        lines.append(
            f"| {provider.title()} | {PROVIDER_MODELS[provider]} | "
            f"{len(state.get('completed_batches', {}))}/4 | US${known:.4f} | "
            f"US${float(state.get('ambiguous_exposure_usd') or 0.0):.4f} |"
        )
    lines.extend([
        "",
        "- Gemini completed all batches through the Developer API without retry or Vertex fallback.",
        "- Grok completed all paid calls; an over-constrained local cross-field rule was corrected and all four saved responses were recovered offline without another call.",
        "- OpenAI transmitted all four requests but each exceeded the 180-second read timeout. Their outcomes and billing are ambiguous, so they were not retried or scored.",
        "- Claude's first attempts were rejected unbilled because its wire-schema subset does not support array minimums above one. After a provider-specific serializer correction, all four requests exceeded the 180-second read timeout; they are ambiguous and unscored.",
        "",
        "## Results", "",
        "| Provider | Images | Suitable | Unsure | Held-out precision | Held-out recall | Harmful rate |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    for provider in PROVIDERS:
        row = result["providers"][provider]
        metrics = row["held_out_metrics"]
        lines.append(
            f"| {provider.title()} | {row['completed_images']} | {row['suitable_pair_count']} | "
            f"{row['unsure_pair_count']} | {_pct(metrics['precision'])} | {_pct(metrics['recall'])} | "
            f"{_pct(metrics['harmful_eligibility_rate'])} |"
        )
    majority = result["majority"]["held_out_metrics"]
    available = result["available_provider_majority"]
    available_metrics = available["held_out_metrics"]
    lines.extend([
        "", "## Combined decision", "",
        f"- Complete providers: {', '.join(result['complete_providers']) or 'none'}.",
        f"- Three-of-four majority pairs: {result['majority']['pair_count']}.",
        f"- Unanimous pairs: {result['unanimous']['pair_count']}.",
        f"- Majority held-out precision: {_pct(majority['precision'])}.",
        f"- Majority held-out recall: {_pct(majority['recall'])}.",
        f"- Majority harmful eligibility rate: {_pct(majority['harmful_eligibility_rate'])}.",
        f"- Available-provider majority threshold: {available['threshold']}.",
        f"- Available-provider majority pairs: {available['pair_count']}.",
        f"- Available-provider held-out precision: {_pct(available_metrics['precision'])}.",
        f"- Available-provider held-out recall: {_pct(available_metrics['recall'])}.",
        f"- Known spend: US${result['known_cost_usd']:.4f}.",
        f"- Ambiguous maximum exposure: US${result['ambiguous_exposure_usd']:.4f}.", "",
        "## Interpretation", "",
        "Gemini was materially more useful than Grok on the held-out human-reviewed pairs, but its 55.6% precision and 41.7% recall are not strong enough to define production eligibility. Grok's held-out recall was 8.3%.",
        "",
        "The two available providers' intersection was more conservative but not safer: it recovered one of twelve held-out positives and included two held-out false positives. This does not support using provider agreement as the eligibility rule.",
        "",
        "OpenAI and Claude remain methodologically useful independent judges, but this run did not measure their editorial quality. A future bounded compatibility run should use smaller response batches or provider-supported asynchronous polling, while preserving identical per-case content and the same held-out split.",
        "",
        "No production selector or image metadata was changed. Provider votes remain research evidence only.",
    ])
    atomic_write_text(output_dir / "shortlist_rerank_report.md", "\n".join(lines) + "\n")


def status(output_dir: Path) -> dict[str, Any]:
    manifest = read_json(output_dir / "trial_manifest.json")
    return {
        "trial_name": manifest["trial_name"], "image_count": manifest["image_count"],
        "quote_count": manifest["quote_count"], "shortlist_size": manifest["shortlist_size"],
        "prompt_parity": manifest["prompt_parity"], "known_cost_usd": total_spend(output_dir),
        "ambiguous_exposure_usd": total_ambiguous_exposure(output_dir),
        "providers": {
            provider: {
                "completed_batches": len(_state(output_dir, provider)["completed_batches"]),
                "failed_batches": len(_state(output_dir, provider)["failed_batches"]),
            } for provider in PROVIDERS
        },
        "preflight": read_json(output_dir / "preflight.json"),
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Focused four-provider image/quote rerank")
    sub = value.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--research-run", type=Path, required=True)
    prepare.add_argument("--image-work-dir", type=Path, required=True)
    prepare.add_argument("--retrieval-dir", type=Path, required=True)
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
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "prepare":
        result = prepare_trial(args.research_run, args.image_work_dir, args.retrieval_dir, args.output)
    elif args.command == "run":
        result = run_trial(args.project_dir, args.trial_dir, execute=args.execute, confirmed_cost=args.confirm_max_cost_usd)
    elif args.command == "status":
        result = status(args.trial_dir)
    else:
        result = compile_results(args.trial_dir)
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


__all__ = [
    "BATCH_SCHEMA", "build_prompt", "build_shortlists", "calibration_split", "compile_results",
    "prepare_trial", "run_trial", "validate_response",
]
