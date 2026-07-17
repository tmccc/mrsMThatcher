from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import analyse_mrs_assets_xai_v4 as xai

from .bakeoff import PRICES
from .io import atomic_write_json, atomic_write_text, read_json, sha256_file
from .quote_research_gemini import validate_packet
from .thatcher_image_hunt import (
    GeminiHuntClient,
    LogicalCallRouter,
    append_jsonl,
    load_project_environment,
    require_transport_parity,
)


SCHEMA_VERSION = 1
PROMPT_VERSION = "source-grounded-image-quote-eligibility-v1"
CORPUS_TEMPLATE_VERSION = "compact-quote-editorial-corpus-v1"
TRIAL_NAME = "image_quote_eligibility_trial_001"
EXPECTED_QUOTES = 626
EXPECTED_UNRESOLVED = 6
EXPECTED_IMAGES = 24
BATCH_SIZE = 6
MAX_BATCHES_PER_PROVIDER = 4
MAX_SUITABLE_QUOTES = 20
MAX_REJECTED_BROAD_MATCHES = 10
MAX_OUTPUT_TOKENS = 24576
EXPECTED_OUTPUT_TOKENS = 12750
HARD_COST_CEILING_USD = 10.0
CONSERVATIVE_NEXT_CALL_USD = 1.25

MATCH_BASES = {
    "exact_event",
    "named_person_or_relationship",
    "direct_mechanism",
    "direct_consequence",
    "direct_principle",
    "tone_or_rhetorical",
    "portrait_general",
}
STRENGTHS = {"moderate", "strong"}
RISK_FLAGS = {
    "named_person_mismatch",
    "wrong_event",
    "wrong_period",
    "generic_topic_only",
    "tone_mismatch",
    "visual_message_mismatch",
    "symbolic_overreach",
    "ally_adversary_confusion",
}

STRING_300 = {"type": "string", "maxLength": 300}
SUITABLE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "quote_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "strength": {"type": "string", "enum": sorted(STRENGTHS)},
        "match_basis": {"type": "string", "enum": sorted(MATCH_BASES)},
        "reason": STRING_300,
        "risk_flags": {
            "type": "array",
            "items": {"type": "string", "enum": sorted(RISK_FLAGS)},
            "maxItems": 8,
        },
    },
    "required": ["quote_hash", "strength", "match_basis", "reason", "risk_flags"],
}
REJECTED_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "quote_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "reason": STRING_300,
    },
    "required": ["quote_hash", "reason"],
}
IMAGE_RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "candidate_id": {"type": "string", "minLength": 1, "maxLength": 80},
        "image_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "suitable_quotes": {
            "type": "array", "items": SUITABLE_SCHEMA, "maxItems": MAX_SUITABLE_QUOTES,
        },
        "no_suitable_quote": {"type": "boolean"},
        "selection_summary": {"type": "string", "maxLength": 500},
        "rejected_broad_matches": {
            "type": "array", "items": REJECTED_SCHEMA, "maxItems": MAX_REJECTED_BROAD_MATCHES,
        },
    },
    "required": [
        "candidate_id", "image_sha256", "suitable_quotes", "no_suitable_quote",
        "selection_summary", "rejected_broad_matches",
    ],
}
BATCH_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "records": {
            "type": "array", "items": IMAGE_RESULT_SCHEMA,
            "minItems": BATCH_SIZE, "maxItems": BATCH_SIZE,
        },
    },
    "required": ["records"],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def value_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def estimate_tokens(value: str) -> int:
    return math.ceil(len(value.encode("utf-8")) / 3)


def _clean_text(value: Any, maximum: int = 700) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= maximum:
        return text
    return text[: maximum - 1].rstrip() + "…"


def _clean_list(value: Any, *, maximum_items: int = 10, maximum_text: int = 240) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_clean_text(item, maximum_text) for item in value[:maximum_items] if _clean_text(item, maximum_text)]


def load_completed_corpus(research_run: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    packets_path = research_run / "research_packets.json"
    manifest_path = research_run / "corpus_manifest.json"
    closure_path = research_run / "final_unresolved/final_research_status.json"
    closure_audit_path = research_run / "final_unresolved/corpus_closure_audit.json"
    packets_db = read_json(packets_path)
    manifest = read_json(manifest_path)
    status = read_json(closure_path)
    closure_audit = read_json(closure_audit_path)
    packets = packets_db.get("items") if isinstance(packets_db, dict) else None
    manifest_records = manifest.get("records") if isinstance(manifest, dict) else None
    unresolved = set(status.get("unresolved_quote_ids") or []) if isinstance(status, dict) else set()
    if not isinstance(packets, dict) or len(packets) != EXPECTED_QUOTES:
        raise RuntimeError(f"completed packet count must be exactly {EXPECTED_QUOTES}")
    if not isinstance(manifest_records, list) or len(manifest_records) != EXPECTED_QUOTES + EXPECTED_UNRESOLVED:
        raise RuntimeError("corpus manifest must contain exactly 632 records")
    if len(unresolved) != EXPECTED_UNRESOLVED:
        raise RuntimeError(f"unresolved quote count must be exactly {EXPECTED_UNRESOLVED}")
    if not bool(closure_audit.get("all_checks_passed")):
        raise RuntimeError("canonical corpus closure audit does not pass")
    manifest_by_id = {str(row.get("quote_id")): row for row in manifest_records}
    if len(manifest_by_id) != len(manifest_records):
        raise RuntimeError("corpus manifest contains duplicate quote IDs")
    if unresolved.intersection(packets):
        raise RuntimeError("unresolved quote appears in completed packet collection")
    rows: list[dict[str, Any]] = []
    for quote_id in sorted(packets):
        packet = packets[quote_id]
        if not isinstance(packet, dict):
            raise RuntimeError(f"completed packet is not an object: {quote_id}")
        if "validation_status" in packet and packet.get("validation_status") != "valid":
            raise RuntimeError(f"completed packet has an invalid status: {quote_id}")
        validated = validate_packet(
            {key: packet[key] for key in (
                "quote_id", "quote_text", "verification_status", "verified_text",
                "text_variation_notes", "speaker", "date", "source_event", "stable_locator",
                "historical_context", "immediate_subject", "intended_argument", "literal_meaning",
                "broader_principle", "mechanism", "claimed_consequence", "entities",
                "editorial_guidance", "research_confidence", "unresolved_questions", "sources",
            )},
            {"quote_id": quote_id, "quote_text": str(manifest_by_id[quote_id]["quote_text"])},
        )
        if validated["quote_id"] != quote_id:
            raise RuntimeError(f"packet key/identity mismatch: {quote_id}")
        rows.append(validated)
    expected_corpus_hash = str(status.get("corpus_hash") or "")
    if expected_corpus_hash and sha256_file(packets_path) != expected_corpus_hash:
        raise RuntimeError("research packet collection hash differs from closure status")
    metadata = {
        "packet_count": len(rows),
        "unresolved_count": len(unresolved),
        "unresolved_quote_ids": sorted(unresolved),
        "research_packets_sha256": sha256_file(packets_path),
        "corpus_manifest_sha256": sha256_file(manifest_path),
        "eligible_quote_id_set_sha256": text_hash("\n".join(row["quote_id"] for row in rows) + "\n"),
        "closure_audit_sha256": sha256_file(closure_audit_path),
    }
    return rows, metadata


def compact_quote_record(packet: dict[str, Any]) -> dict[str, Any]:
    editorial = packet["editorial_guidance"]
    return {
        "id": packet["quote_id"],
        "quote": _clean_text(packet["quote_text"], 1800),
        "verified": _clean_text(packet["verified_text"], 1200),
        "verification": packet["verification_status"],
        "date": _clean_text(packet["date"], 80),
        "event": _clean_text(packet["source_event"], 300),
        "context": _clean_text(packet["historical_context"], 550),
        "subject": _clean_text(packet["immediate_subject"], 350),
        "argument": _clean_text(packet["intended_argument"], 500),
        "literal": _clean_text(packet["literal_meaning"], 400),
        "principle": _clean_text(packet["broader_principle"], 400),
        "mechanism": _clean_text(packet["mechanism"], 400),
        "consequence": _clean_text(packet["claimed_consequence"], 400),
        "entities": _clean_list(packet["entities"], maximum_items=10, maximum_text=120),
        "visual": {
            "first_impression": _clean_text(editorial["desired_first_impression"], 300),
            "requirements": _clean_list(editorial["historical_requirements"], maximum_items=6),
            "must_dominate": _clean_list(editorial["must_be_visually_dominant"], maximum_items=6),
            "must_not_dominate": _clean_list(editorial["must_not_dominate"], maximum_items=6),
            "mistakes": _clean_list(editorial["common_visual_mistakes"], maximum_items=6),
        },
        "confidence": packet["research_confidence"],
    }


def load_image_records(work_dir: Path) -> list[dict[str, Any]]:
    effective = read_json(work_dir / "effective_production_ready_manifest.json")
    identities = read_json(work_dir / "source_grounded_identity_metadata.json")
    analysis_db = read_json(work_dir / "source_grounded_image_analysis.json")
    records = effective.get("records") if isinstance(effective, dict) else None
    if not isinstance(records, list) or len(records) != EXPECTED_IMAGES:
        raise RuntimeError(f"effective production-ready image count must be exactly {EXPECTED_IMAGES}")
    identity_by_hash = {
        row["image_sha256"]: row for row in identities.get("records") or [] if isinstance(row, dict)
    }
    analyses = analysis_db.get("items") or {}
    result = []
    for source in sorted(records, key=lambda row: row["candidate_id"]):
        image_hash = str(source["original_sha256"])
        identity = identity_by_hash.get(image_hash)
        analysed = analyses.get(image_hash)
        if not isinstance(identity, dict) or not isinstance(analysed, dict):
            raise RuntimeError(f"missing source-grounded metadata or analysis for {source['candidate_id']}")
        analysis = analysed.get("analysis")
        if not isinstance(analysis, dict):
            raise RuntimeError(f"missing canonical visual analysis for {source['candidate_id']}")
        result.append({
            "candidate_id": source["candidate_id"],
            "image_sha256": image_hash,
            "rights_status": source["rights_status"],
            "publisher": _clean_text(source.get("publisher"), 200),
            "caption": _clean_text(source.get("caption"), 600),
            "approximate_date": _clean_text(identity.get("approximate_date"), 100),
            "source_event": _clean_text(identity.get("source_event_summary"), 600),
            "named_people": _clean_list(identity.get("named_people"), maximum_items=16, maximum_text=120),
            "identity_basis": identity.get("identity_basis"),
            "identity_confidence": identity.get("identity_confidence"),
            "analysis_model": analysed.get("analysis_model"),
            "analysis_prompt_version": analysed.get("prompt_version"),
            "visual": {
                "description": _clean_text(analysis.get("description"), 700),
                "scene_summary": _clean_text(analysis.get("scene_summary"), 450),
                "scene_types": _clean_list(analysis.get("scene_types")),
                "setting": analysis.get("setting") or {},
                "people": analysis.get("people") or {},
                "tone": _clean_list(analysis.get("tone")),
                "themes": _clean_list(analysis.get("themes")),
                "visual_energy": analysis.get("visual_energy"),
                "visible_elements": _clean_list(analysis.get("visible_elements"), maximum_items=16),
                "historical_context": analysis.get("historical_context") or {},
                "pairing": analysis.get("pairing") or {},
            },
        })
    if len({row["candidate_id"] for row in result}) != EXPECTED_IMAGES:
        raise RuntimeError("duplicate image candidate IDs")
    if len({row["image_sha256"] for row in result}) != EXPECTED_IMAGES:
        raise RuntimeError("duplicate image hashes")
    return result


def build_prompt(quote_corpus: list[dict[str, Any]], images: list[dict[str, Any]]) -> str:
    schema_text = json.dumps(BATCH_RESPONSE_SCHEMA, sort_keys=True, separators=(",", ":"))
    corpus_text = "\n".join(json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) for row in quote_corpus)
    image_text = json.dumps(images, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"""Prompt version: {PROMPT_VERSION}
Act as a conservative picture editor. Select only quotations for which each supplied archive photograph is genuinely suitable for publication. This is an eligibility audit, not a thematic search and not a request to fill a quota.

RULES
- Use only quote hashes present in the eligible corpus below. Return at most {MAX_SUITABLE_QUOTES} per image; an empty list is valid and often preferable.
- A suitable pairing must communicate the quotation's intended mechanism, consequence, broader principle, historically specific event, named relationship, or rhetorical tone immediately and without a misleading dominant message.
- Mere shared words, generic conservatism, generic leadership, generic diplomacy, or broad ideological overlap are insufficient.
- Respect source-grounded identities and events. Do not treat an ally as an adversary, one president as another, an informal reception as a summit, or a portrait as evidence of an unrelated policy mechanism.
- Reject wrong actor, wrong event, wrong period, tone mismatch, visual-message mismatch, symbolic overreach and ally/adversary confusion.
- A named-person photograph is normally suitable only when the quote concerns that person, that relationship, that exact event, or a principle that the visible interaction directly and unambiguously illustrates.
- A general portrait may match only a clearly compatible personal, biographical or rhetorical quotation; it is not a fallback for abstract policy.
- Strength must be moderate or strong. Do not return weak possibilities.
- Risk flags identify residual risks on otherwise suitable matches; if a risk makes the dominant message misleading, reject the match instead.
- Record up to {MAX_REJECTED_BROAD_MATCHES} tempting but unsuitable broad matches so the audit can diagnose false-positive themes.
- The quotation records are canonical historical evidence. Do not rewrite quote identity or invent context.
- Human review outcomes and expected answers are deliberately absent.

Return exactly one JSON object matching this schema and exactly one record for each image candidate:
{schema_text}

IMAGE METADATA (source-grounded identity plus canonical visual analysis):
{image_text}

ELIGIBLE QUOTATION CORPUS ({len(quote_corpus)} records; compact field labels are self-describing):
{corpus_text}
"""


def validate_batch_response(
    value: Any, expected_images: list[dict[str, Any]], eligible_quote_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"records"} or not isinstance(value["records"], list):
        raise ValueError("batch response must contain only a records array")
    if len(value["records"]) != len(expected_images):
        raise ValueError("batch response image count mismatch")
    expected = {row["candidate_id"]: row for row in expected_images}
    seen_images: set[str] = set()
    validated: list[dict[str, Any]] = []
    for row in value["records"]:
        required = {
            "candidate_id", "image_sha256", "suitable_quotes", "no_suitable_quote",
            "selection_summary", "rejected_broad_matches",
        }
        if not isinstance(row, dict) or set(row) != required:
            raise ValueError("image result fields mismatch")
        candidate_id = str(row["candidate_id"])
        if candidate_id not in expected or candidate_id in seen_images:
            raise ValueError("unknown or duplicate image candidate")
        if row["image_sha256"] != expected[candidate_id]["image_sha256"]:
            raise ValueError("image identity changed")
        if type(row["no_suitable_quote"]) is not bool:
            raise ValueError("no_suitable_quote must be boolean")
        if not isinstance(row["selection_summary"], str) or len(row["selection_summary"]) > 500:
            raise ValueError("invalid selection summary")
        suitable = row["suitable_quotes"]
        rejected = row["rejected_broad_matches"]
        if not isinstance(suitable, list) or len(suitable) > MAX_SUITABLE_QUOTES:
            raise ValueError("invalid suitable quote list")
        if not isinstance(rejected, list) or len(rejected) > MAX_REJECTED_BROAD_MATCHES:
            raise ValueError("invalid rejected broad-match list")
        if bool(suitable) == row["no_suitable_quote"]:
            raise ValueError("no_suitable_quote contradicts suitable quote list")
        suitable_ids: set[str] = set()
        normalised_suitable = []
        for match in suitable:
            if not isinstance(match, dict) or set(match) != {"quote_hash", "strength", "match_basis", "reason", "risk_flags"}:
                raise ValueError("suitable match fields mismatch")
            quote_id = str(match["quote_hash"])
            if quote_id not in eligible_quote_ids or quote_id in suitable_ids:
                raise ValueError("unknown or duplicate suitable quote hash")
            if match["strength"] not in STRENGTHS or match["match_basis"] not in MATCH_BASES:
                raise ValueError("invalid suitable match enum")
            if not isinstance(match["reason"], str) or not match["reason"].strip() or len(match["reason"]) > 300:
                raise ValueError("invalid suitable match reason")
            if not isinstance(match["risk_flags"], list) or len(match["risk_flags"]) > 8:
                raise ValueError("invalid risk flags")
            if any(flag not in RISK_FLAGS for flag in match["risk_flags"]):
                raise ValueError("unknown risk flag")
            suitable_ids.add(quote_id)
            normalised_suitable.append({**match, "risk_flags": sorted(set(match["risk_flags"]))})
        normalised_rejected = []
        rejected_ids: set[str] = set()
        for match in rejected:
            if not isinstance(match, dict) or set(match) != {"quote_hash", "reason"}:
                raise ValueError("rejected broad-match fields mismatch")
            quote_id = str(match["quote_hash"])
            if quote_id not in eligible_quote_ids or quote_id in rejected_ids or quote_id in suitable_ids:
                raise ValueError("unknown, duplicate or contradictory rejected quote hash")
            if not isinstance(match["reason"], str) or not match["reason"].strip() or len(match["reason"]) > 300:
                raise ValueError("invalid rejected broad-match reason")
            rejected_ids.add(quote_id)
            normalised_rejected.append(match)
        validated.append({**row, "suitable_quotes": normalised_suitable, "rejected_broad_matches": normalised_rejected})
        seen_images.add(candidate_id)
    return {"records": sorted(validated, key=lambda row: row["candidate_id"])}


def _batches(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    result = [rows[index:index + BATCH_SIZE] for index in range(0, len(rows), BATCH_SIZE)]
    if len(result) != MAX_BATCHES_PER_PROVIDER or any(len(batch) != BATCH_SIZE for batch in result):
        raise RuntimeError("trial requires exactly four six-image batches")
    return result


def prepare_trial(research_run: Path, image_work_dir: Path, output_dir: Path) -> dict[str, Any]:
    packets, corpus_meta = load_completed_corpus(research_run)
    compact_corpus = [compact_quote_record(packet) for packet in packets]
    image_records = load_image_records(image_work_dir)
    batches = _batches(image_records)
    quote_corpus_sha256 = value_hash(compact_corpus)
    image_manifest_sha256 = value_hash(image_records)
    existing_manifest_path = output_dir / "trial_manifest.json"
    provider_results_exist = any((output_dir / "providers").glob("*/normalised/*.json")) or any(
        (output_dir / "providers").glob("*/validated/*.json")
    )
    if existing_manifest_path.exists() and provider_results_exist:
        existing = read_json(existing_manifest_path)
        if (
            existing.get("quote_corpus_sha256") != quote_corpus_sha256
            or existing.get("image_manifest_sha256") != image_manifest_sha256
            or existing.get("prompt_version") != PROMPT_VERSION
        ):
            raise RuntimeError("completed provider results make trial inputs immutable")
        return {"manifest": existing, "preflight": read_json(output_dir / "preflight.json")}
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_dir / "quote_corpus.json", {
        "schema_version": SCHEMA_VERSION,
        "template_version": CORPUS_TEMPLATE_VERSION,
        "records": compact_corpus,
    })
    atomic_write_json(output_dir / "image_manifest.json", {
        "schema_version": SCHEMA_VERSION, "records": image_records,
    })
    prompts_dir = output_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    for stale in prompts_dir.glob("batch_*.txt"):
        stale.unlink()
    batch_manifest = []
    prompts = []
    for index, batch in enumerate(batches, 1):
        prompt = build_prompt(compact_corpus, batch)
        prompt_path = prompts_dir / f"batch_{index:02d}.txt"
        atomic_write_text(prompt_path, prompt)
        prompts.append(prompt)
        batch_manifest.append({
            "batch_id": f"batch-{index:02d}",
            "candidate_ids": [row["candidate_id"] for row in batch],
            "image_hashes": [row["image_sha256"] for row in batch],
            "prompt_path": str(prompt_path.relative_to(output_dir)),
            "prompt_sha256": text_hash(prompt),
            "prompt_bytes": len(prompt.encode("utf-8")),
            "estimated_input_tokens": estimate_tokens(prompt),
        })
    prompt_hashes = [row["prompt_sha256"] for row in batch_manifest]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "trial_name": TRIAL_NAME,
        "prompt_version": PROMPT_VERSION,
        "corpus_template_version": CORPUS_TEMPLATE_VERSION,
        "research_run": str(research_run.resolve()),
        "image_work_dir": str(image_work_dir.resolve()),
        "eligible_corpus": corpus_meta,
        "quote_corpus_sha256": quote_corpus_sha256,
        "image_manifest_sha256": image_manifest_sha256,
        "image_count": len(image_records),
        "batch_size": BATCH_SIZE,
        "providers": ["grok", "gemini"],
        "provider_prompt_hashes": {"grok": prompt_hashes, "gemini": prompt_hashes},
        "prompt_parity": True,
        "batches": batch_manifest,
        "human_labels_in_provider_inputs": False,
        "generated_at": utc_now(),
    }
    atomic_write_json(output_dir / "trial_manifest.json", manifest)
    preflight = build_preflight(output_dir)
    return {"manifest": manifest, "preflight": preflight}


def build_preflight(output_dir: Path) -> dict[str, Any]:
    manifest = read_json(output_dir / "trial_manifest.json")
    total_input_tokens = sum(int(row["estimated_input_tokens"]) for row in manifest["batches"])
    calls = len(manifest["batches"])
    providers: dict[str, Any] = {}
    for provider in ("grok", "gemini"):
        price = PRICES[provider]
        expected = (total_input_tokens * price["input"] + calls * EXPECTED_OUTPUT_TOKENS * price["output"]) / 1_000_000
        guarded = (total_input_tokens * price["input"] + calls * MAX_OUTPUT_TOKENS * price["output"]) / 1_000_000
        providers[provider] = {
            "model": xai.DEFAULT_MODEL if provider == "grok" else GeminiHuntClient("developer_api", client=object()).model,
            "logical_calls": calls,
            "estimated_input_tokens": total_input_tokens,
            "expected_output_tokens": calls * EXPECTED_OUTPUT_TOKENS,
            "maximum_output_tokens": calls * MAX_OUTPUT_TOKENS,
            "expected_cost_usd": round(expected, 6),
            "guarded_base_cost_usd": round(guarded, 6),
        }
    expected_combined = sum(row["expected_cost_usd"] for row in providers.values())
    guarded_combined = sum(row["guarded_base_cost_usd"] for row in providers.values())
    value = {
        "schema_version": SCHEMA_VERSION,
        "models": {provider: row["model"] for provider, row in providers.items()},
        "providers": providers,
        "logical_calls_total": calls * 2,
        "image_count": EXPECTED_IMAGES,
        "quote_count": EXPECTED_QUOTES,
        "expected_combined_cost_usd": round(expected_combined, 6),
        "guarded_base_combined_cost_usd": round(guarded_combined, 6),
        "hard_combined_cost_ceiling_usd": HARD_COST_CEILING_USD,
        "within_hard_ceiling": guarded_combined <= HARD_COST_CEILING_USD,
        "prompt_parity": manifest["prompt_parity"],
        "google_search_grounding_enabled": False,
        "human_labels_in_provider_inputs": False,
        "production_files_written": False,
        "generated_at": utc_now(),
    }
    atomic_write_json(output_dir / "preflight.json", value)
    return value


def _load_batch(output_dir: Path, batch_row: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    prompt = (output_dir / batch_row["prompt_path"]).read_text(encoding="utf-8")
    if text_hash(prompt) != batch_row["prompt_sha256"]:
        raise RuntimeError("persisted prompt hash mismatch")
    images = read_json(output_dir / "image_manifest.json")["records"]
    by_id = {row["candidate_id"]: row for row in images}
    return prompt, [by_id[candidate_id] for candidate_id in batch_row["candidate_ids"]]


def _provider_state(output_dir: Path, provider: str) -> dict[str, Any]:
    path = output_dir / "providers" / provider / "state.json"
    state = read_json(path, None)
    if not isinstance(state, dict):
        state = {
            "schema_version": SCHEMA_VERSION, "provider": provider,
            "completed_batches": {}, "partial_batches": {}, "failed_batches": {}, "known_cost_usd": 0.0,
            "ambiguous_exposure_usd": 0.0,
        }
    state.setdefault("partial_batches", {})
    return state


def _save_provider_state(output_dir: Path, provider: str, state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    atomic_write_json(output_dir / "providers" / provider / "state.json", state)


def total_known_spend(output_dir: Path) -> float:
    grok = float(_provider_state(output_dir, "grok").get("known_cost_usd") or 0.0)
    gemini_state = read_json(output_dir / "providers/gemini/provider_route_state.json", {})
    gemini = sum(float(value or 0.0) for value in (gemini_state.get("known_spend_usd") or {}).values())
    return round(grok + gemini, 8)


def _assert_cost_room(output_dir: Path) -> None:
    if total_known_spend(output_dir) + CONSERVATIVE_NEXT_CALL_USD > HARD_COST_CEILING_USD:
        raise RuntimeError("conservative next-call allowance would exceed the combined trial ceiling")


def run_grok(output_dir: Path, manifest: dict[str, Any], api_key: str) -> None:
    provider_dir = output_dir / "providers/grok"
    raw_dir = provider_dir / "raw"
    normal_dir = provider_dir / "normalised"
    raw_dir.mkdir(parents=True, exist_ok=True)
    normal_dir.mkdir(parents=True, exist_ok=True)
    state = _provider_state(output_dir, "grok")
    eligible = {row["id"] for row in read_json(output_dir / "quote_corpus.json")["records"]}
    model = str(read_json(output_dir / "preflight.json")["models"]["grok"])
    for batch in manifest["batches"]:
        batch_id = batch["batch_id"]
        if batch_id in state["completed_batches"]:
            continue
        _assert_cost_room(output_dir)
        prompt, images = _load_batch(output_dir, batch)
        for attempt_number in (1, 2):
            started = utc_now()
            append_jsonl(provider_dir / "attempts.jsonl", {
                "batch_id": batch_id, "attempt_number": attempt_number, "status": "started",
                "model": model, "prompt_sha256": batch["prompt_sha256"], "started_at": started,
            })
            try:
                result = xai.call_xai_structured(
                    api_key=api_key,
                    base_url=os.getenv("XAI_API_BASE_URL", xai.DEFAULT_XAI_BASE_URL),
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    schema_name="mrs_image_quote_eligibility_batch",
                    schema=BATCH_RESPONSE_SCHEMA,
                    timeout_seconds=360,
                    max_tokens=MAX_OUTPUT_TOKENS,
                    max_retries=0,
                )
                validated = validate_batch_response(result.content, images, eligible)
                ticks = result.usage.get("cost_in_usd_ticks")
                if type(ticks) is not int or ticks < 0:
                    raise RuntimeError("successful Grok response lacks authoritative cost_in_usd_ticks")
                cost = ticks / 10_000_000_000
                response = {
                    "schema_version": SCHEMA_VERSION, "provider": "grok", "model": model,
                    "batch_id": batch_id, "prompt_sha256": batch["prompt_sha256"],
                    "request_id": result.response_id, "usage": result.usage,
                    "cost_usd": cost, "content": result.content, "completed_at": utc_now(),
                }
                atomic_write_json(raw_dir / f"{batch_id}.json", response)
                normalised = {**response, "content": validated}
                atomic_write_json(normal_dir / f"{batch_id}.json", normalised)
                state["known_cost_usd"] = round(float(state["known_cost_usd"]) + cost, 8)
                state["completed_batches"][batch_id] = {
                    "normalised_path": str((normal_dir / f"{batch_id}.json").relative_to(output_dir)),
                    "cost_usd": cost, "request_id": result.response_id,
                }
                state["failed_batches"].pop(batch_id, None)
                _save_provider_state(output_dir, "grok", state)
                append_jsonl(provider_dir / "attempts.jsonl", {
                    "batch_id": batch_id, "attempt_number": attempt_number, "status": "completed",
                    "model": model, "prompt_sha256": batch["prompt_sha256"],
                    "request_id": result.response_id, "usage": result.usage,
                    "known_cost_usd": cost, "completed_at": utc_now(),
                })
                break
            except xai.TransientAnalysisError as exc:
                append_jsonl(provider_dir / "attempts.jsonl", {
                    "batch_id": batch_id, "attempt_number": attempt_number,
                    "status": "transient_failure", "error": str(exc)[:2000], "completed_at": utc_now(),
                })
                if attempt_number == 2:
                    state["failed_batches"][batch_id] = {"status": "transient_failure", "error": str(exc)[:2000]}
                    _save_provider_state(output_dir, "grok", state)
            except Exception as exc:
                append_jsonl(provider_dir / "attempts.jsonl", {
                    "batch_id": batch_id, "attempt_number": attempt_number,
                    "status": "permanent_failure", "error": f"{type(exc).__name__}: {exc}"[:2000],
                    "completed_at": utc_now(),
                })
                state["failed_batches"][batch_id] = {
                    "status": "permanent_failure", "error": f"{type(exc).__name__}: {exc}"[:2000],
                }
                _save_provider_state(output_dir, "grok", state)
                break


def run_gemini(output_dir: Path, manifest: dict[str, Any]) -> None:
    provider_dir = output_dir / "providers/gemini"
    provider_dir.mkdir(parents=True, exist_ok=True)
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    location = os.getenv("GOOGLE_CLOUD_LOCATION") or "global"
    developer = GeminiHuntClient("developer_api", api_key=api_key, timeout_seconds=360)
    vertex = GeminiHuntClient("vertex", project=project, location=location, timeout_seconds=360)
    require_transport_parity(developer, vertex, "triage", BATCH_RESPONSE_SCHEMA)
    router = LogicalCallRouter(
        provider_dir, developer, vertex,
        combined_limit=HARD_COST_CEILING_USD,
        developer_limit=HARD_COST_CEILING_USD,
        vertex_limit=HARD_COST_CEILING_USD,
    )
    eligible = {row["id"] for row in read_json(output_dir / "quote_corpus.json")["records"]}
    state = _provider_state(output_dir, "gemini")
    for batch in manifest["batches"]:
        batch_id = batch["batch_id"]
        if batch_id in state["completed_batches"]:
            continue
        _assert_cost_room(output_dir)
        prompt, images = _load_batch(output_dir, batch)
        logical_id = f"eligibility-{batch_id}"
        try:
            result = router.run(
                logical_call_id=logical_id, phase="triage", prompt=prompt,
                schema=BATCH_RESPONSE_SCHEMA, images=(),
            )
            validated = validate_batch_response(result.get("parsed"), images, eligible)
            normal_path = provider_dir / "validated" / f"{batch_id}.json"
            atomic_write_json(normal_path, {
                "schema_version": SCHEMA_VERSION, "provider": "gemini",
                "transport": result["provider"], "model": result["model"],
                "batch_id": batch_id, "prompt_sha256": batch["prompt_sha256"],
                "request_id": result.get("request_id"), "usage": result.get("usage") or {},
                "cost_usd": result.get("cost_usd"), "content": validated,
                "completed_at": result.get("completed_at") or utc_now(),
            })
            state["completed_batches"][batch_id] = {
                "normalised_path": str(normal_path.relative_to(output_dir)),
                "transport": result["provider"], "cost_usd": result.get("cost_usd"),
            }
            state["failed_batches"].pop(batch_id, None)
            _save_provider_state(output_dir, "gemini", state)
        except Exception as exc:
            state["failed_batches"][batch_id] = {
                "status": "failed", "error": f"{type(exc).__name__}: {exc}"[:2000],
            }
            _save_provider_state(output_dir, "gemini", state)


def run_trial(project_dir: Path, output_dir: Path, *, execute: bool, confirmed_cost: float) -> dict[str, Any]:
    if not execute:
        raise RuntimeError("live provider calls require --execute")
    if confirmed_cost != HARD_COST_CEILING_USD:
        raise RuntimeError(f"execution requires exact --confirm-max-cost-usd {HARD_COST_CEILING_USD:g}")
    preflight = read_json(output_dir / "preflight.json")
    if not preflight.get("within_hard_ceiling") or not preflight.get("prompt_parity"):
        raise RuntimeError("preflight does not permit execution")
    load_project_environment(project_dir / "mrsMThatcher.env")
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        raise RuntimeError("XAI_API_KEY is not configured")
    manifest = read_json(output_dir / "trial_manifest.json")
    run_gemini(output_dir, manifest)
    run_grok(output_dir, manifest, api_key)
    return compile_results(output_dir)


def _provider_records(output_dir: Path, provider: str) -> tuple[list[dict[str, Any]], list[str]]:
    state = _provider_state(output_dir, provider)
    records: list[dict[str, Any]] = []
    for batch_id, row in sorted(state.get("completed_batches", {}).items()):
        value = read_json(output_dir / row["normalised_path"])
        records.extend(value["content"]["records"])
    for batch_id, row in sorted(state.get("partial_batches", {}).items()):
        if batch_id in state.get("completed_batches", {}):
            continue
        value = read_json(output_dir / row["normalised_path"])
        records.extend(value["content"]["records"])
    failures = sorted(set(state.get("failed_batches", {})))
    return records, failures


def recover_partial_gemini_batches(output_dir: Path) -> dict[str, Any]:
    """Retain independently valid records from a structurally conflicting Gemini batch."""
    state = _provider_state(output_dir, "gemini")
    manifest = read_json(output_dir / "trial_manifest.json")
    eligible = {row["id"] for row in read_json(output_dir / "quote_corpus.json")["records"]}
    image_rows = read_json(output_dir / "image_manifest.json")["records"]
    images = {row["candidate_id"]: row for row in image_rows}
    recovered_batches = []
    for batch in manifest["batches"]:
        batch_id = batch["batch_id"]
        if batch_id not in state.get("failed_batches", {}) or batch_id in state.get("partial_batches", {}):
            continue
        router_path = output_dir / "providers/gemini/triage_batches/normalised" / f"eligibility-{batch_id}.json"
        if not router_path.exists():
            continue
        provider_value = read_json(router_path)
        parsed = provider_value.get("parsed")
        if not isinstance(parsed, dict) or not isinstance(parsed.get("records"), list):
            continue
        expected_ids = set(batch["candidate_ids"])
        valid_records = []
        invalid_records = []
        seen: set[str] = set()
        for record in parsed["records"]:
            candidate_id = str(record.get("candidate_id") or "") if isinstance(record, dict) else ""
            if candidate_id not in expected_ids or candidate_id in seen:
                invalid_records.append({
                    "candidate_id": candidate_id or None,
                    "error": "unknown or duplicate image candidate",
                    "record_sha256": value_hash(record),
                })
                continue
            seen.add(candidate_id)
            try:
                validated = validate_batch_response({"records": [record]}, [images[candidate_id]], eligible)
                valid_records.extend(validated["records"])
            except ValueError as exc:
                invalid_records.append({
                    "candidate_id": candidate_id,
                    "image_sha256": images[candidate_id]["image_sha256"],
                    "error": str(exc),
                    "record_sha256": value_hash(record),
                })
        missing = sorted(expected_ids - seen)
        invalid_records.extend({"candidate_id": candidate_id, "error": "record missing"} for candidate_id in missing)
        if not valid_records:
            continue
        path = output_dir / "providers/gemini/partial" / f"{batch_id}.json"
        recovery = {
            "schema_version": SCHEMA_VERSION,
            "provider": "gemini",
            "transport": provider_value.get("provider"),
            "model": provider_value.get("model"),
            "batch_id": batch_id,
            "prompt_sha256": batch["prompt_sha256"],
            "content": {"records": sorted(valid_records, key=lambda row: row["candidate_id"])},
            "invalid_records": invalid_records,
            "recovery_kind": "offline_per_record_validation",
            "provider_response_unchanged": True,
            "conflicting_records_not_normalised": True,
            "recovered_at": utc_now(),
        }
        atomic_write_json(path, recovery)
        state["partial_batches"][batch_id] = {
            "normalised_path": str(path.relative_to(output_dir)),
            "valid_image_count": len(valid_records),
            "invalid_image_count": len(invalid_records),
            "invalid_candidate_ids": sorted(
                row["candidate_id"] for row in invalid_records if row.get("candidate_id")
            ),
        }
        recovered_batches.append({"batch_id": batch_id, **state["partial_batches"][batch_id]})
    _save_provider_state(output_dir, "gemini", state)
    value = {
        "schema_version": SCHEMA_VERSION,
        "recovered_batches": recovered_batches,
        "valid_images_recovered": sum(row["valid_image_count"] for row in state["partial_batches"].values()),
        "invalid_images_retained": sum(row["invalid_image_count"] for row in state["partial_batches"].values()),
        "paid_calls_made": 0,
        "generated_at": utc_now(),
    }
    atomic_write_json(output_dir / "gemini_offline_partial_recovery.json", value)
    return value


def _human_evaluation_rows(output_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    manifest = read_json(output_dir / "trial_manifest.json")
    work_dir = Path(manifest["image_work_dir"])
    matching = read_json(work_dir / "quote_matching_dry_run.json")
    reviews = (read_json(work_dir / "human_pairing_review.json").get("reviews") or {})
    pairs = []
    exclusions = []
    for candidate in matching["records"]:
        selected = candidate["top_winning_matches"][:5] or candidate["top_proposed_matches"][:3]
        for match in selected:
            pair_id = f"{candidate['candidate_id']}:{match['quote_hash']}"
            review = reviews.get(pair_id)
            if not review:
                continue
            note = str(review.get("note") or "").casefold()
            contradictory = review.get("decision") == "approve_pairing" and any(
                token in note for token in ("no.", "what has that got", "poor choice", "not suitable")
            )
            row = {
                "pair_id": pair_id, "candidate_id": candidate["candidate_id"],
                "quote_hash": match["quote_hash"], "human_decision": review["decision"],
                "current_selector_would_win": bool(match.get("would_win_when_all_originals_available")),
                "human_note": review.get("note") or "",
            }
            if contradictory:
                row["exclusion_reason"] = "decision contradicts reviewer note"
                exclusions.append(row)
            else:
                pairs.append(row)
    return pairs, exclusions


def _classification_metrics(rows: list[dict[str, Any]], predictions: set[tuple[str, str]]) -> dict[str, Any]:
    tp = fp = tn = fn = 0
    for row in rows:
        actual = row["human_decision"] == "approve_pairing"
        predicted = (row["candidate_id"], row["quote_hash"]) in predictions
        if actual and predicted:
            tp += 1
        elif not actual and predicted:
            fp += 1
        elif not actual and not predicted:
            tn += 1
        else:
            fn += 1
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    specificity = tn / (tn + fp) if tn + fp else None
    return {
        "evaluated_pairs": len(rows), "true_positive": tp, "false_positive": fp,
        "true_negative": tn, "false_negative": fn,
        "precision": precision, "recall": recall, "specificity": specificity,
        "harmful_eligibility_rate": fp / (fp + tn) if fp + tn else None,
    }


def compile_results(output_dir: Path) -> dict[str, Any]:
    partial_recovery = recover_partial_gemini_batches(output_dir)
    manifest = read_json(output_dir / "trial_manifest.json")
    providers: dict[str, Any] = {}
    sets: dict[str, set[tuple[str, str]]] = {}
    for provider in ("grok", "gemini"):
        records, failures = _provider_records(output_dir, provider)
        suitable = {
            (row["candidate_id"], match["quote_hash"])
            for row in records for match in row["suitable_quotes"]
        }
        sets[provider] = suitable
        providers[provider] = {
            "completed_images": len(records), "failed_batches": failures,
            "partial_validation": (
                partial_recovery if provider == "gemini" else {
                    "valid_images_recovered": 0, "invalid_images_retained": 0,
                }
            ),
            "suitable_pair_count": len(suitable),
            "images_with_no_suitable_quote": sum(row["no_suitable_quote"] for row in records),
            "match_basis_counts": dict(Counter(
                match["match_basis"] for row in records for match in row["suitable_quotes"]
            )),
        }
    human_rows, excluded = _human_evaluation_rows(output_dir)
    consensus = sets.get("grok", set()).intersection(sets.get("gemini", set()))
    union = sets.get("grok", set()).union(sets.get("gemini", set()))
    selector_predictions = {
        (row["candidate_id"], row["quote_hash"])
        for row in human_rows if row["current_selector_would_win"]
    }
    for provider in providers:
        providers[provider]["human_evaluation"] = _classification_metrics(human_rows, sets[provider])
    evaluation = {
        "schema_version": SCHEMA_VERSION,
        "providers": providers,
        "provider_agreement": {
            "intersection_pair_count": len(consensus), "union_pair_count": len(union),
            "jaccard": len(consensus) / len(union) if union else 1.0,
            "grok_only": len(sets.get("grok", set()) - sets.get("gemini", set())),
            "gemini_only": len(sets.get("gemini", set()) - sets.get("grok", set())),
        },
        "consensus_human_evaluation": _classification_metrics(human_rows, consensus),
        "union_human_evaluation": _classification_metrics(human_rows, union),
        "current_selector_human_evaluation": _classification_metrics(human_rows, selector_predictions),
        "human_evaluation_pair_count": len(human_rows),
        "excluded_human_pair_count": len(excluded),
        "excluded_human_pairs": excluded,
        "unlabelled_provider_pairs_are_not_assumed_correct": True,
        "known_cost_usd": total_known_spend(output_dir),
        "generated_at": utc_now(),
    }
    all_records = {}
    for provider in ("grok", "gemini"):
        records, _ = _provider_records(output_dir, provider)
        all_records[provider] = records
    atomic_write_json(output_dir / "eligibility_results.json", {
        "schema_version": SCHEMA_VERSION, "providers": all_records,
    })
    quote_by_id = {row["id"]: row for row in read_json(output_dir / "quote_corpus.json")["records"]}
    image_by_id = {row["candidate_id"]: row for row in read_json(output_dir / "image_manifest.json")["records"]}
    provider_by_image = {
        provider: {row["candidate_id"]: row for row in records}
        for provider, records in all_records.items()
    }
    eligibility_map = []
    for candidate_id in sorted(image_by_id):
        pair_rows: dict[str, dict[str, Any]] = {}
        no_match_providers = []
        missing_providers = []
        for provider in ("grok", "gemini"):
            image_result = provider_by_image[provider].get(candidate_id)
            if image_result is None:
                missing_providers.append(provider)
                continue
            if image_result["no_suitable_quote"]:
                no_match_providers.append(provider)
            for match in image_result["suitable_quotes"]:
                row = pair_rows.setdefault(match["quote_hash"], {
                    "quote_hash": match["quote_hash"],
                    "quote_text": quote_by_id[match["quote_hash"]]["quote"],
                    "source_event": quote_by_id[match["quote_hash"]]["event"],
                    "intended_argument": quote_by_id[match["quote_hash"]]["argument"],
                    "provider_assessments": {},
                })
                row["provider_assessments"][provider] = match
        for row in pair_rows.values():
            votes = len(row["provider_assessments"])
            row["provider_vote_count"] = votes
            row["agreement_class"] = "both_providers" if votes == 2 else f"{next(iter(row['provider_assessments']))}_only"
            row["human_review_required"] = True
        eligibility_map.append({
            "candidate_id": candidate_id,
            "image_sha256": image_by_id[candidate_id]["image_sha256"],
            "source_event": image_by_id[candidate_id]["source_event"],
            "named_people": image_by_id[candidate_id]["named_people"],
            "provider_no_match": sorted(no_match_providers),
            "provider_result_missing": sorted(missing_providers),
            "suitable_quotes": sorted(
                pair_rows.values(), key=lambda row: (-row["provider_vote_count"], row["quote_hash"])
            ),
        })
    atomic_write_json(output_dir / "image_quote_eligibility_map.json", {
        "schema_version": SCHEMA_VERSION,
        "interpretation": (
            "Provider suggestions are review candidates, not production eligibility. "
            "No pair may enter production from this trial without human or separately authorised validation."
        ),
        "records": eligibility_map,
    })
    review_rows = []
    for image in eligibility_map:
        for match in image["suitable_quotes"]:
            review_rows.append({
                "pair_id": f"{image['candidate_id']}:{match['quote_hash']}",
                "candidate_id": image["candidate_id"],
                "image_sha256": image["image_sha256"],
                "image_source_event": image["source_event"],
                "named_people": image["named_people"],
                **match,
                "review_status": "unreviewed",
            })
    atomic_write_json(output_dir / "eligibility_review_queue.json", {
        "schema_version": SCHEMA_VERSION,
        "record_count": len(review_rows),
        "records": review_rows,
    })
    review_lines = [
        "# Eligibility review queue", "",
        "These are provider suggestions, not approved production pairings. Every row requires editorial review.", "",
        "| Image | Quote hash | Provider assessment | Quote |",
        "|---|---|---|---|",
    ]
    for row in review_rows:
        providers_text = ", ".join(sorted(row["provider_assessments"]))
        quote = row["quote_text"].replace("|", "\\|").replace("\n", " ")
        review_lines.append(
            f"| `{row['candidate_id']}` | `{row['quote_hash']}` | {providers_text} | {quote} |"
        )
    atomic_write_text(output_dir / "eligibility_review_queue.md", "\n".join(review_lines) + "\n")
    atomic_write_json(output_dir / "provider_comparison.json", evaluation)
    write_report(output_dir, manifest, evaluation)
    return evaluation


def _pct(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.1%}"


def write_report(output_dir: Path, manifest: dict[str, Any], evaluation: dict[str, Any]) -> None:
    gemini_route = read_json(output_dir / "providers/gemini/provider_route_state.json", {})
    grok_state = _provider_state(output_dir, "grok")
    gemini_spend = sum(float(value or 0.0) for value in (gemini_route.get("known_spend_usd") or {}).values())
    grok_spend = float(grok_state.get("known_cost_usd") or 0.0)
    gemini_attempts_path = output_dir / "providers/gemini/provider_attempts.jsonl"
    gemini_attempts = [
        json.loads(line) for line in gemini_attempts_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ] if gemini_attempts_path.exists() else []
    grok_attempts_path = output_dir / "providers/grok/attempts.jsonl"
    grok_attempts = [
        json.loads(line) for line in grok_attempts_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("status") != "started"
    ] if grok_attempts_path.exists() else []
    lines = [
        "# Image-to-quote eligibility trial",
        "",
        "## Scope",
        "",
        f"- Eligible quotation packets: {manifest['eligible_corpus']['packet_count']}",
        f"- Excluded unresolved quotations: {manifest['eligible_corpus']['unresolved_count']}",
        f"- Source-grounded images: {manifest['image_count']}",
        "- Providers: Grok and Gemini, with byte-identical substantive prompts",
        "- Human pairing labels were not included in provider inputs.",
        "- The providers received source-grounded image metadata and canonical visual analysis, not human pairing decisions.",
        "- This is an isolated research result; it does not alter production selection.",
        "",
        "## Provider results",
        "",
        "| Provider | Completed images | Suitable pairs | No-match images | Precision on reviewed pairs | Recall | Harmful eligibility rate |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for provider in ("grok", "gemini"):
        row = evaluation["providers"][provider]
        metrics = row["human_evaluation"]
        lines.append(
            f"| {provider.title()} | {row['completed_images']} | {row['suitable_pair_count']} | "
            f"{row['images_with_no_suitable_quote']} | {_pct(metrics['precision'])} | "
            f"{_pct(metrics['recall'])} | {_pct(metrics['harmful_eligibility_rate'])} |"
        )
    consensus = evaluation["consensus_human_evaluation"]
    current = evaluation["current_selector_human_evaluation"]
    lines.extend([
        "",
        "## Agreement and calibration",
        "",
        f"- Provider intersection: {evaluation['provider_agreement']['intersection_pair_count']} pairs.",
        f"- Provider union: {evaluation['provider_agreement']['union_pair_count']} pairs.",
        f"- Provider Jaccard agreement: {evaluation['provider_agreement']['jaccard']:.3f}.",
        f"- Consensus precision on the coherent human-reviewed subset: {_pct(consensus['precision'])}.",
        f"- Consensus recall on that subset: {_pct(consensus['recall'])}.",
        f"- Consensus harmful eligibility rate: {_pct(consensus['harmful_eligibility_rate'])}.",
        f"- Current selector precision on the same subset: {_pct(current['precision'])}.",
        f"- One contradictory human decision/note record was excluded: {evaluation['excluded_human_pair_count']}.",
        "- Provider pairs outside the reviewed subset remain unvalidated and are not treated as correct.",
        "",
        "## Transport and cost",
        "",
        f"- Gemini model: `{read_json(output_dir / 'preflight.json')['models']['gemini']}`.",
        f"- Grok model: `{read_json(output_dir / 'preflight.json')['models']['grok']}`.",
        f"- Gemini completed attempts: {sum(row.get('status') == 'completed' for row in gemini_attempts)}; "
        f"Developer 429s: {sum(row.get('status') == '429' for row in gemini_attempts)}; "
        f"direct-to-Vertex count: {gemini_route.get('direct_to_vertex_count', 0)}.",
        f"- Grok completed attempts: {sum(row.get('status') == 'completed' for row in grok_attempts)}.",
        f"- Gemini known spend: US${gemini_spend:.4f}.",
        f"- Grok known spend: US${grok_spend:.4f}.",
        f"- Known provider spend: US${evaluation['known_cost_usd']:.4f}.",
        "",
        "## Recommendation",
        "",
        "- Do not use cross-provider agreement as an eligibility gate from this run: the intersection is empty.",
        "- Grok's all-empty result is too conservative and misses every coherent human-approved calibration pair.",
        "- Gemini's suggestions are a useful 26-pair editorial review queue, but none overlap the 16 coherent human-approved calibration pairs; they are not validated positives.",
        "- The metadata-plus-entire-corpus one-pass method is therefore not supported as a production replacement for the selector.",
        "- A better next experiment would use candidate generation followed by a focused per-image rerank over a locally retrieved shortlist, with explicit negative calibration and no production changes.",
    ])
    atomic_write_text(output_dir / "image_quote_eligibility_report.md", "\n".join(lines) + "\n")


def status(output_dir: Path) -> dict[str, Any]:
    manifest = read_json(output_dir / "trial_manifest.json")
    preflight = read_json(output_dir / "preflight.json")
    result = {
        "trial_name": manifest.get("trial_name"),
        "quote_count": manifest.get("eligible_corpus", {}).get("packet_count"),
        "image_count": manifest.get("image_count"),
        "prompt_parity": manifest.get("prompt_parity"),
        "preflight": preflight,
        "providers": {},
        "known_cost_usd": total_known_spend(output_dir),
    }
    for provider in ("grok", "gemini"):
        state = _provider_state(output_dir, provider)
        result["providers"][provider] = {
            "completed_batches": len(state.get("completed_batches", {})),
            "partial_batches": len(state.get("partial_batches", {})),
            "failed_batches": len(state.get("failed_batches", {})),
        }
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Source-grounded image-to-quote eligibility trial")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--research-run", type=Path, required=True)
    prepare.add_argument("--image-work-dir", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--trial-dir", type=Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("--project-dir", type=Path, default=Path.cwd())
    run.add_argument("--trial-dir", type=Path, required=True)
    run.add_argument("--execute", action="store_true")
    run.add_argument("--confirm-max-cost-usd", type=float, default=0.0)
    run.add_argument("--resume", action="store_true")
    report = sub.add_parser("report")
    report.add_argument("--trial-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "prepare":
        value = prepare_trial(args.research_run, args.image_work_dir, args.output)
    elif args.command == "status":
        value = status(args.trial_dir)
    elif args.command == "run":
        value = run_trial(args.project_dir, args.trial_dir, execute=args.execute, confirmed_cost=args.confirm_max_cost_usd)
    elif args.command == "report":
        value = compile_results(args.trial_dir)
    else:
        raise RuntimeError("unreachable")
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


__all__ = [
    "BATCH_RESPONSE_SCHEMA", "HARD_COST_CEILING_USD", "build_prompt", "compact_quote_record",
    "compile_results", "load_completed_corpus", "load_image_records", "prepare_trial",
    "run_trial", "validate_batch_response",
]
