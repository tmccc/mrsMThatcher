from __future__ import annotations

import base64
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .first_impression import EVEREST_QUOTE_HASH
from .bakeoff import FREE_TRADE_KEY
from .io import atomic_write_json, sha256_file

SCHEMA_VERSION = 1
PROMPT_VERSION = "generation-brief-v1"
STYLES = ("mechanism_first", "first_impression_first", "balanced_editorial")
MAX_CASES = 20
MAX_IMAGES = 60
MAX_ATTEMPTS = 2
HARD_CEILING_USD = 20.0
MODEL = "gpt-image-1.5"
QUALITY = "low"
SIZE = "1024x1024"
OBSERVED_COST_PER_IMAGE_USD = 0.013


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, list):
        return "; ".join(_text(item) for item in value if _text(item))
    return ""


def build_brief(quote: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any]:
    claims = quote.get("claims") or []
    mechanisms = [_text(row.get("mechanism")) for row in claims if isinstance(row, dict)]
    consequences = [_text(row.get("claimed_consequence")) for row in claims if isinstance(row, dict)]
    principles = [_text(row.get("broader_principle")) for row in claims if isinstance(row, dict)]
    mechanism = next((x for x in mechanisms if x), _text(quote.get("specific_concepts")))
    consequence = next((x for x in consequences if x), _text(quote.get("dominant_message")))
    principle = next((x for x in principles if x), _text(quote.get("primary_themes")))
    subjects = list(intent.get("desired_primary_visual_subjects") or quote.get("desired_visual_evidence") or [])
    avoid = list(dict.fromkeys([*(intent.get("undesired_dominant_messages") or []), *(quote.get("not_about") or [])]))
    if not mechanism:
        mechanism = _text(quote.get("core_claim"))
    return {
        "schema_version": SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "quote_hash": quote["quote_hash"],
        "quote_text": quote["quote_text"],
        "core_claim": _text(quote.get("core_claim")),
        "mechanism": mechanism,
        "claimed_consequence": consequence,
        "broader_principle": principle,
        "desired_first_impression": _text(intent.get("desired_first_impression")),
        "desired_primary_subject": subjects[0] if subjects else _text(quote.get("desired_visual_evidence")),
        "desired_tone": list(intent.get("desired_tone") or []),
        "attention_hierarchy": subjects[:4],
        "must_include": subjects[:4],
        "must_avoid": avoid[:8],
        "forbidden_dominant_messages": avoid[:8],
        "visual_style": "editorial photorealism, clear human-scale scene, restrained period detail",
        "timeline_readability": "high",
        "provenance": "deterministic projection of cached quote and visual-intent fingerprints",
    }


def prompt_for_style(brief: dict[str, Any], style: str) -> str:
    if style not in STYLES:
        raise ValueError(f"unknown prompt style: {style}")
    common = {
        "mechanism": brief["mechanism"],
        "consequence": brief["claimed_consequence"],
        "principle": brief["broader_principle"],
        "first_impression": brief["desired_first_impression"],
        "primary_subject": brief["desired_primary_subject"],
        "tone": brief["desired_tone"],
        "attention_hierarchy": brief["attention_hierarchy"],
        "must_include": brief["must_include"],
        "must_avoid": brief["must_avoid"],
        "forbidden_dominant_messages": brief["forbidden_dominant_messages"],
    }
    emphases = {
        "mechanism_first": "Make the concrete causal mechanism the visible action. Show consequence and principle only through that action.",
        "first_impression_first": "Design the first second: the primary subject and desired message must dominate at thumbnail size before any detail is inspected.",
        "balanced_editorial": "Balance causal accuracy, immediate readability, emotional tone and editorial visual power without becoming literal or diagrammatic.",
    }
    return (
        f"Prompt version: {PROMPT_VERSION}; style: {style}.\n"
        "Create one 1024x1024 editorial image for a quotation shown separately in a social-media post. "
        "Do not render quotation text, captions, logos, watermarks, diagrams or split-screen comparisons. "
        "Use a single coherent scene with a legible focal subject and strong composition at timeline size.\n"
        f"PRIMARY INSTRUCTION: {emphases[style]}\n"
        f"GENERATION BRIEF: {json.dumps(common, ensure_ascii=False, sort_keys=True)}\n"
        "The forbidden dominant messages must not become the focal subject, colour emphasis or most recognisable symbol. "
        "Avoid generic ideological conflict when the quotation concerns a more specific mechanism or human consequence."
    )


def candidate_id(quote_hash: str, style: str) -> str:
    return hashlib.sha256(f"{quote_hash}:{style}:{PROMPT_VERSION}".encode()).hexdigest()[:20]


def build_manifest(quotes: dict[str, Any], intents: dict[str, Any], pair_manifest: dict[str, Any],
                   pair_reviews: dict[str, Any], first_cases: dict[str, Any], first_reviews: dict[str, Any]) -> dict[str, Any]:
    pair_by_id = {row["case_id"]: row for row in pair_manifest.get("items", [])}
    selected: dict[str, str] = {
        EVEREST_QUOTE_HASH: "forced_everest_regression",
        FREE_TRADE_KEY[0]: "forced_free_trade_regression",
    }
    for case_id, review in sorted(pair_reviews.get("items", {}).items()):
        row = pair_by_id.get(case_id)
        if row and review.get("preferred_candidate") == "neither":
            selected.setdefault(row["quote_hash"], "pairwise_neither_existing_candidates")
    first_by_id = {row["case_id"]: row for row in first_cases.get("items", [])}
    for case_id, review in sorted(first_reviews.get("items", {}).items()):
        row = first_by_id.get(case_id)
        if row and review.get("image_decision") == "replace":
            selected.setdefault(row["quote_hash"], "human_replace_wrong_message_or_tone")
    for case_id, review in sorted(first_reviews.get("items", {}).items()):
        row = first_by_id.get(case_id)
        if row and review.get("image_decision") == "keep":
            selected.setdefault(row["quote_hash"], "strong_control")
    items = []
    for quote_hash, reason in list(selected.items()):
        if len(items) >= MAX_CASES:
            break
        if quote_hash not in quotes or quote_hash not in intents:
            continue
        items.append({"case_id": hashlib.sha256(f"generation:{quote_hash}".encode()).hexdigest()[:20],
                      "quote_hash": quote_hash, "quote_text": quotes[quote_hash]["quote_text"],
                      "inclusion_reason": reason})
    if len(items) != MAX_CASES or not {EVEREST_QUOTE_HASH, FREE_TRADE_KEY[0]} <= {x["quote_hash"] for x in items}:
        raise RuntimeError("unable to construct exact 20-case validation set with regressions")
    return {"schema_version": SCHEMA_VERSION, "analysis_kind": "generation_prompt_validation_manifest",
            "prompt_version": PROMPT_VERSION, "items": items,
            "manifest_hash": hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()}


def preflight(manifest: dict[str, Any]) -> dict[str, Any]:
    calls = len(manifest["items"]) * len(STYLES)
    if len(manifest["items"]) > MAX_CASES or calls > MAX_IMAGES:
        raise RuntimeError("pilot exceeds case or image maximum")
    expected = calls * OBSERVED_COST_PER_IMAGE_USD
    retry_reserve = calls * OBSERVED_COST_PER_IMAGE_USD
    # Observed completed first-impression study: $2.536823 text critics and
    # $0.291034 vision for 50 images. Use the observed per-image rates and a
    # full 100% reserve for the bounded downstream analysis stage.
    analysis_expected = calls * ((2.536823 + 0.291034) / 50)
    analysis_retry_reserve = analysis_expected
    maximum = expected + retry_reserve + analysis_expected + analysis_retry_reserve
    return {"schema_version": 1, "model": MODEL, "quality": QUALITY, "size": SIZE,
            "cases": len(manifest["items"]), "generation_calls": calls,
            "maximum_attempts_per_candidate": MAX_ATTEMPTS,
            "expected_generation_cost_usd": round(expected, 4), "generation_retry_reserve_usd": round(retry_reserve, 4),
            "expected_analysis_cost_usd": round(analysis_expected, 4), "analysis_retry_reserve_usd": round(analysis_retry_reserve, 4),
            "expected_cost_usd": round(expected + analysis_expected, 4), "conservative_retry_cost_usd": round(retry_reserve + analysis_retry_reserve, 4),
            "conservative_maximum_cost_usd": round(maximum, 4), "hard_ceiling_usd": HARD_CEILING_USD,
            "pricing_basis": "observed project cost per gpt-image-1.5 low 1024x1024 request",
            "paid_execution_allowed": maximum <= HARD_CEILING_USD}


class ImageClient:
    def __init__(self, api_key: str, transport=None):
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY required only for explicit generation")
        self.api_key = api_key
        self.transport = transport or requests.post

    def generate(self, prompt: str) -> tuple[bytes, dict[str, Any]]:
        response = self.transport("https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"model": MODEL, "prompt": prompt, "quality": QUALITY, "size": SIZE, "n": 1, "output_format": "png"},
            timeout=(30, 600))
        if response.status_code >= 400:
            raise requests.HTTPError(f"HTTP {response.status_code}: {response.text[:1000]}", response=response)
        raw = response.json(); encoded = raw["data"][0]["b64_json"]
        return base64.b64decode(encoded, validate=True), {"id": raw.get("id"), "usage": raw.get("usage", {})}


def execute_generation(run: Path, manifest: dict[str, Any], briefs: dict[str, Any], *, client: ImageClient,
                       confirmed_limit: float, sleep=time.sleep) -> dict[str, Any]:
    pf = preflight(manifest)
    if confirmed_limit != HARD_CEILING_USD or not pf["paid_execution_allowed"]:
        raise RuntimeError("exact $20 generation ceiling confirmation required")
    db = json.loads((run / "generated_candidates.json").read_text()) if (run / "generated_candidates.json").exists() else {"schema_version": 1, "items": {}, "attempts": []}
    for case in manifest["items"]:
        for style in STYLES:
            cid = candidate_id(case["quote_hash"], style)
            if cid in db["items"]:
                continue
            prompt = prompt_for_style(briefs[case["quote_hash"]], style)
            for attempt in range(1, MAX_ATTEMPTS + 1):
                record = {"candidate_id": cid, "case_id": case["case_id"], "quote_hash": case["quote_hash"],
                          "style": style, "attempt": attempt, "prepared_at": utc_now(),
                          "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest(), "state": "sending"}
                db["attempts"].append(record); atomic_write_json(run / "generated_candidates.json", db)
                try:
                    image, metadata = client.generate(prompt)
                    image_path = run / "images" / f"{cid}.png"; image_path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = image_path.with_suffix(".tmp"); tmp.write_bytes(image); tmp.replace(image_path)
                    record["state"] = "completed"; record["completed_at"] = utc_now()
                    db["items"][cid] = {"candidate_id": cid, "case_id": case["case_id"], "quote_hash": case["quote_hash"],
                        "style": style, "image_basename": image_path.name, "path": str(image_path), "sha256": sha256_file(image_path),
                        "model": MODEL, "quality": QUALITY, "size": SIZE, "prompt_version": PROMPT_VERSION,
                        "response_metadata": metadata, "generated_at": utc_now()}
                    atomic_write_json(run / "generated_candidates.json", db); break
                except requests.RequestException as exc:
                    record["state"] = "confirmed_failure"; record["error"] = str(exc); atomic_write_json(run / "generated_candidates.json", db)
                    if attempt == MAX_ATTEMPTS:
                        break
                    sleep(2 ** attempt)
    return db
