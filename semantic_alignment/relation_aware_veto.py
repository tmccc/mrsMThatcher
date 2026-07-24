"""Build semantic contracts and adjudicate relation-aware image vetoes."""

from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import math
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from google import genai
from google.genai import types

from .bakeoff import PRICES, PROVIDER_MODELS
from .discovered_image_preparation import _load_production_scorer, production_hashes
from .image_quote_eligibility import load_completed_corpus
from .io import atomic_write_json, atomic_write_text, read_json, sha256_file
from .thatcher_image_hunt import append_jsonl, load_project_environment, parse_json_response


MODEL = "gemini-3.1-pro-preview"
if PROVIDER_MODELS.get("gemini") != MODEL:
    raise RuntimeError("configured Gemini model differs from relation-veto pinned model")

SCHEMA_VERSION = 1
CONTRACT_SCHEMA_VERSION = "relation-aware-quote-contract-v1"
PAIR_SCHEMA_VERSION = "relation-aware-pair-judgement-v4"
PROMPT_VERSION = "relation-aware-semantic-veto-2026-07-16-v1"
PAIR_PROMPT_VERSION = "relation-aware-semantic-veto-pairs-2026-07-16-v3"
PRICING_VERSION = "gemini-3.1-pro-preview-public-pricing-2026-07-16-v1"
SDK_API_VERSION_DEVELOPER = "v1beta"
SDK_API_VERSION_VERTEX = "v1"
THINKING_LEVEL = "HIGH"
TEMPERATURE = 0.0
CONTRACT_MAX_OUTPUT_TOKENS = 4992
PAIR_MAX_OUTPUT_TOKENS = 5792
EXPECTED_CONTRACT_OUTPUT_TOKENS = 1200
EXPECTED_PAIR_OUTPUT_TOKENS = 2800
EXPECTED_QUOTES = 627
EXPECTED_UNRESOLVED = 5
EXPECTED_BASELINE_IMAGES = 69
EXPECTED_DISCOVERED_IMAGES = 22
EXPECTED_IMAGES = EXPECTED_BASELINE_IMAGES + EXPECTED_DISCOVERED_IMAGES
PILOT_QUOTE_COUNT = 20
PILOT_IMAGE_COUNT = 8
SELECTOR_CANDIDATE_COUNT = 25
MAX_PAIR_CANDIDATES_PER_IMAGE = 35
PAIR_RECORDS_PER_REQUEST = 5
PRODUCTION_TOP_K = 5
PRODUCTION_BATCH_MAX_ITEMS = 190
V2_PRODUCTION_TOP_K = 8
V2_CONTRACT_VERSION = "relation-aware-material-contradiction-contract-v2"
V2_PAIR_PROMPT_VERSION = "relation-aware-material-contradiction-pairs-2026-07-16-v1"
V2_SELECTION_VERSION = "production-oriented-top8-current69-expanded91-v2"
V2_REUSE_VERSION = "relation-aware-v1-to-v2-reuse-policy-v1"
PLANNED_EXECUTION_CEILING_USD = 50.0
REPAIR_RESERVE_USD = 10.0
HARD_COMBINED_CEILING_USD = 60.0
V2_HARD_COMBINED_CEILING_USD = 100.0
V2_PLANNED_EXECUTION_CEILING_USD = 90.0
BATCH_PRICE_FACTOR = 0.5
INTERACTIVE_PRICE_FACTOR = 1.0

CLAIM_TYPES = {
    "abstract_principle", "relationship", "relationship_transformation", "concrete_action",
    "named_event", "named_entity", "causal_claim", "comparison", "warning",
    "personal_reflection", "other",
}
RELATIONSHIPS = {"none", "ally", "friend", "opponent", "enemy", "adversary", "unknown"}
TRANSITIONS = {
    "none", "enemy_to_friend", "opponent_to_partner", "conflict_to_peace",
    "decline_to_recovery", "freedom_to_coercion", "coercion_to_freedom", "other",
}
STRICT_RELATIONSHIP_TRANSITIONS = {"enemy_to_friend", "opponent_to_partner"}
POLARITIES = {"positive", "negative", "mixed", "neutral"}
SPECIFICITIES = {"general", "period_specific", "event_specific", "person_specific"}
VISUAL_STRATEGIES = {"literal", "historical_scene", "documentary", "symbolic", "neutral_portrait"}
CONFIDENCES = {"high", "medium", "low"}
PAIR_DECISIONS = {"allow", "veto", "uncertain"}
RELATIONSHIP_SUPPORT = {"yes", "no", "unknown", "not_required"}
CONTRADICTION_TYPES = {
    "none", "ally_adversary_confusion", "wrong_relationship", "missing_relationship_evidence",
    "missing_transition", "wrong_named_entity", "wrong_event", "wrong_period", "wrong_action",
    "wrong_direction", "wrong_before_after_state", "portrait_substitution", "generic_theme_only",
    "misleading_dominant_message", "insufficient_actor_count", "insufficient_evidence", "other",
}
SOURCE_FIELDS = {
    "quote_text", "verified_text", "verification_status", "research_confidence",
    "historical_context", "immediate_subject", "intended_argument", "literal_meaning",
    "broader_principle", "mechanism", "claimed_consequence", "entities", "editorial_guidance",
    "source_event", "date",
}

STRING = {"type": "string"}
STRING_LIST = {"type": "array", "items": STRING, "maxItems": 16}
CONTRACT_FIELDS = (
    "quote_id", "quote_text", "verified_text", "verification_status", "research_confidence",
    "dominant_proposition", "claim_type", "actor_count_minimum", "required_actor_roles",
    "named_entities_required", "initial_relationship", "final_relationship", "required_transition",
    "required_action", "required_direction", "required_before_state", "required_after_state",
    "polarity", "historical_specificity", "visual_strategies_allowed", "neutral_portrait_allowed",
    "multi_person_image_risk", "relationship_evidence_required", "hard_conflicts",
    "misleading_implications_to_avoid", "source_fields_used", "confidence",
)
CONTRACT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "quote_id": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "quote_text": STRING, "verified_text": STRING, "verification_status": STRING,
        "research_confidence": {"type": "string", "enum": sorted(CONFIDENCES)},
        "dominant_proposition": STRING,
        "claim_type": {"type": "string", "enum": sorted(CLAIM_TYPES)},
        "actor_count_minimum": {"type": "integer", "minimum": 0, "maximum": 12},
        "required_actor_roles": STRING_LIST, "named_entities_required": STRING_LIST,
        "initial_relationship": {"type": "string", "enum": sorted(RELATIONSHIPS)},
        "final_relationship": {"type": "string", "enum": sorted(RELATIONSHIPS)},
        "required_transition": {"type": "string", "enum": sorted(TRANSITIONS)},
        "required_action": STRING, "required_direction": STRING,
        "required_before_state": STRING, "required_after_state": STRING,
        "polarity": {"type": "string", "enum": sorted(POLARITIES)},
        "historical_specificity": {"type": "string", "enum": sorted(SPECIFICITIES)},
        "visual_strategies_allowed": {
            "type": "array", "items": {"type": "string", "enum": sorted(VISUAL_STRATEGIES)},
            "minItems": 1, "maxItems": len(VISUAL_STRATEGIES),
        },
        "neutral_portrait_allowed": {"type": "boolean"},
        "multi_person_image_risk": {"type": "string", "enum": ["low", "medium", "high"]},
        "relationship_evidence_required": {"type": "boolean"},
        "hard_conflicts": STRING_LIST, "misleading_implications_to_avoid": STRING_LIST,
        "source_fields_used": {
            "type": "array", "items": {"type": "string", "enum": sorted(SOURCE_FIELDS)},
            "minItems": 1, "maxItems": len(SOURCE_FIELDS),
        },
        "confidence": {"type": "string", "enum": sorted(CONFIDENCES)},
    },
    "required": list(CONTRACT_FIELDS),
}

PAIR_FIELDS = (
    "pair_id", "quote_id", "image_id", "decision", "confidence", "materially_misleading",
    "relationship_supported", "contradiction_types", "dominant_visual_message",
    "reason",
)


def _pair_record_schema() -> dict[str, Any]:
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "pair_id": STRING,
            "quote_id": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "image_id": STRING,
            "decision": {"type": "string", "enum": sorted(PAIR_DECISIONS)},
            "confidence": {"type": "string", "enum": sorted(CONFIDENCES)},
            "materially_misleading": {"type": "boolean"},
            "relationship_supported": {"type": "string", "enum": sorted(RELATIONSHIP_SUPPORT)},
            "contradiction_types": {
                "type": "array", "items": {"type": "string", "enum": sorted(CONTRADICTION_TYPES)},
                "minItems": 1, "maxItems": 8,
            },
            "dominant_visual_message": STRING,
            "reason": STRING,
        },
        "required": list(PAIR_FIELDS),
    }


def pair_response_schema(count: int) -> dict[str, Any]:
    """Return the pair response schema."""
    item = _pair_record_schema()
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            # Gemini rejects larger exact-cardinality arrays as an overly complex
            # response schema. Exact count and immutable IDs are enforced locally
            # by validate_pair_response before a result can be accepted.
            "records": {"type": "array", "items": item},
        },
        "required": ["records"],
    }


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_bytes(value: Any) -> bytes:
    """Return the canonical bytes."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def value_hash(value: Any) -> str:
    """Return the value hash."""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def text_hash(value: str) -> str:
    """Return whether text hash."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def estimate_tokens(value: str) -> int:
    """Estimate tokens."""
    return math.ceil(len(value.encode("utf-8")) / 3)


def _clean(value: Any, maximum: int = 1200) -> str:
    result = " ".join(html.unescape(str(value or "")).split())
    return result if len(result) <= maximum else result[: maximum - 1].rstrip() + "…"


def _clean_list(value: Any, maximum_items: int = 12, maximum_text: int = 240) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value[:maximum_items] if (text := _clean(item, maximum_text))]


def compact_contract_input(packet: dict[str, Any]) -> dict[str, Any]:
    """Return the compact contract input."""
    editorial = packet["editorial_guidance"]
    return {
        "quote_id": packet["quote_id"], "quote_text": _clean(packet["quote_text"], 1800),
        "verified_text": _clean(packet["verified_text"], 1200),
        "verification_status": packet["verification_status"],
        "research_confidence": packet["research_confidence"],
        "date": _clean(packet["date"], 100), "source_event": _clean(packet["source_event"], 400),
        "historical_context": _clean(packet["historical_context"], 900),
        "immediate_subject": _clean(packet["immediate_subject"], 450),
        "intended_argument": _clean(packet["intended_argument"], 650),
        "literal_meaning": _clean(packet["literal_meaning"], 500),
        "broader_principle": _clean(packet["broader_principle"], 500),
        "mechanism": _clean(packet["mechanism"], 500),
        "claimed_consequence": _clean(packet["claimed_consequence"], 500),
        "entities": _clean_list(packet["entities"]),
        "editorial_guidance": {
            "desired_first_impression": _clean(editorial["desired_first_impression"], 400),
            "historical_requirements": _clean_list(editorial["historical_requirements"], 8),
            "must_be_visually_dominant": _clean_list(editorial["must_be_visually_dominant"], 8),
            "must_not_dominate": _clean_list(editorial["must_not_dominate"], 8),
            "common_visual_mistakes": _clean_list(editorial["common_visual_mistakes"], 8),
        },
    }


def contract_prompt(packet: dict[str, Any]) -> str:
    """Return the contract prompt."""
    source = compact_contract_input(packet)
    return f"""Prompt version: {PROMPT_VERSION}
Create a strict semantic contract for one Margaret Thatcher quotation using only the supplied canonical research packet. Return JSON only.

The contract is a safety veto, not a broad thematic matcher. A missed usable image is acceptable; a misleading image is not.

Rules:
- Preserve quote_id, quote_text, verified_text, verification_status and research_confidence exactly.
- dominant_proposition states the quotation's actual claim, not its broad topic.
- relationship and relationship_transformation claims require at least two actors and source-grounded relationship evidence.
- For an enemy-to-friend claim, set initial_relationship=enemy or adversary, final_relationship=friend or ally, required_transition=enemy_to_friend or opponent_to_partner, and relationship_evidence_required=true.
- A photograph of established allies meeting is a hard conflict for an enemy-to-friend transformation even though both concern diplomacy.
- State concrete direction, before/after states and mechanism where they are material.
- Named entities are required only when substituting another person, country or event would misstate the quotation.
- neutral_portrait_allowed must be false when a portrait would erase a required action, relationship, transition, named event or causal mechanism.
- hard_conflicts and misleading implications must be concrete and auditable.
- Use unknown rather than inventing a relationship.
- source_fields_used may name only fields actually used from the packet.
- Do not use outside knowledge and do not mention human labels.

JSON Schema:
{json.dumps(CONTRACT_SCHEMA, sort_keys=True, separators=(',', ':'))}

CANONICAL PACKET:
{json.dumps(source, sort_keys=True, separators=(',', ':'), ensure_ascii=False)}
"""


def validate_contract(value: Any, packet: dict[str, Any]) -> dict[str, Any]:
    """Validate contract."""
    if not isinstance(value, dict) or set(value) != set(CONTRACT_FIELDS):
        raise ValueError("semantic contract fields mismatch")
    for field in ("quote_id", "quote_text", "verified_text", "verification_status", "research_confidence"):
        if value[field] != packet[field]:
            raise ValueError(f"semantic contract changed immutable {field}")
    if value["claim_type"] not in CLAIM_TYPES:
        raise ValueError("invalid claim type")
    if value["initial_relationship"] not in RELATIONSHIPS or value["final_relationship"] not in RELATIONSHIPS:
        raise ValueError("invalid relationship")
    if value["required_transition"] not in TRANSITIONS or value["polarity"] not in POLARITIES:
        raise ValueError("invalid transition or polarity")
    if value["historical_specificity"] not in SPECIFICITIES or value["confidence"] not in CONFIDENCES:
        raise ValueError("invalid specificity or confidence")
    if value["multi_person_image_risk"] not in {"low", "medium", "high"}:
        raise ValueError("invalid multi-person risk")
    if type(value["actor_count_minimum"]) is not int or not 0 <= value["actor_count_minimum"] <= 12:
        raise ValueError("invalid actor minimum")
    for field in (
        "dominant_proposition", "required_action", "required_direction", "required_before_state",
        "required_after_state",
    ):
        if not isinstance(value[field], str):
            raise ValueError(f"{field} must be text")
    if not value["dominant_proposition"].strip():
        raise ValueError("dominant proposition is empty")
    for field in (
        "required_actor_roles", "named_entities_required", "hard_conflicts",
        "misleading_implications_to_avoid", "source_fields_used", "visual_strategies_allowed",
    ):
        if not isinstance(value[field], list) or any(not isinstance(item, str) or not item.strip() for item in value[field]):
            raise ValueError(f"invalid contract list {field}")
    if not value["visual_strategies_allowed"] or not set(value["visual_strategies_allowed"]).issubset(VISUAL_STRATEGIES):
        raise ValueError("invalid visual strategies")
    if not value["source_fields_used"] or not set(value["source_fields_used"]).issubset(SOURCE_FIELDS):
        raise ValueError("invalid source fields")
    if type(value["neutral_portrait_allowed"]) is not bool or type(value["relationship_evidence_required"]) is not bool:
        raise ValueError("contract booleans invalid")
    relationship_claim = value["claim_type"] in {"relationship", "relationship_transformation"}
    if relationship_claim and (not value["relationship_evidence_required"] or value["actor_count_minimum"] < 2):
        raise ValueError("relationship contract lacks evidence/actor requirement")
    if value["claim_type"] == "relationship_transformation" and value["required_transition"] == "none":
        raise ValueError("relationship transformation lacks required transition")
    if (
        value["required_transition"] in STRICT_RELATIONSHIP_TRANSITIONS
        and not value["relationship_evidence_required"]
    ):
        raise ValueError("transition lacks relationship evidence requirement")
    return copy.deepcopy(value)


def _people_count_minimum(category: Any) -> int | None:
    text = str(category or "").casefold()
    if text in {"one", "single", "1", "one person"}:
        return 1
    if text in {"two", "2", "two people"}:
        return 2
    if any(word in text for word in ("group", "crowd", "many", "multiple", "three")):
        return 3
    return None


def _relationship_sentences(packets: Sequence[dict[str, Any]], person: str) -> list[dict[str, Any]]:
    """Return the relationship sentences."""
    person_key = " ".join(re.findall(r"[a-z0-9]+", person.casefold()))

    def mentions_person(text: str) -> bool:
        return person_key in " ".join(re.findall(r"[a-z0-9]+", text.casefold()))

    def mentions_primary_subject(text: str) -> bool:
        normal = " ".join(re.findall(r"[a-z0-9]+", text.casefold()))
        without_person = normal.replace(person_key, " ")
        return "margaret thatcher" in without_person or "thatcher" in without_person.split()

    cooperative_re = re.compile(
        r"\b(all(?:y|ies|iance)|friend(?:ship)?|partner(?:ship)?|"
        r"collaborat\w*|cooperat\w*|rapprochement)\b", re.I,
    )
    hostile_re = re.compile(r"\b(opponent|enemy|adversar\w*|hostil\w*)\b", re.I)
    rows = []
    for packet in packets:
        fields = {
            key: str(packet.get(key) or "") for key in (
                "historical_context", "immediate_subject", "intended_argument", "literal_meaning",
                "broader_principle", "mechanism", "claimed_consequence",
            )
        }
        for field, text in fields.items():
            sentences = re.split(r"(?<=[.!?])\s+", text)
            for index, sentence in enumerate(sentences):
                if not mentions_person(sentence):
                    continue
                start = max(0, index - 1)
                end = min(len(sentences), index + 2)
                window = " ".join(sentences[start:end])
                same_sentence_pair = mentions_primary_subject(sentence)
                pair_reference = bool(re.search(
                    r"\b(?:the two (?:leaders|figures)|they|both)\b", window, re.I,
                ))
                direct_cooperation = cooperative_re.search(window) and (
                    same_sentence_pair or (mentions_primary_subject(window) and pair_reference)
                )
                direct_hostility = bool(same_sentence_pair and re.search(
                    r"\b(?:was|were|became|remained|regarded as|treated as)\b.{0,80}"
                    r"\b(?:opponent|enemy|adversary|hostile)\b",
                    sentence, re.I,
                ))
                if direct_cooperation or direct_hostility:
                    rows.append({
                        "quote_id": packet["quote_id"], "field": field,
                        "text": _clean(window, 700),
                    })
        # A packet may establish a transition across separate bounded fields:
        # the context identifies the counterparty while the intended argument
        # states the adversary-to-cooperation mechanism. Both anchors are
        # required; a surname match beside an unrelated enemy is insufficient.
        context = fields["historical_context"]
        semantics = " ".join(
            fields[key] for key in ("intended_argument", "broader_principle", "claimed_consequence")
        )
        if (
            not any(row["quote_id"] == packet["quote_id"] for row in rows)
            and mentions_person(context)
            and mentions_primary_subject(context)
            and hostile_re.search(semantics)
            and cooperative_re.search(semantics)
        ):
            rows.append({
                "quote_id": packet["quote_id"],
                "field": "bounded_relationship_semantics",
                "text": _clean(f"{context} {semantics}", 1800),
            })
    return rows


def rebuild_source_grounded_relationship_assertions(
    images: Sequence[dict[str, Any]], packets: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Recompute discovered-image relationships from bounded pair evidence."""
    records = []
    changed = []
    for source in images:
        image = copy.deepcopy(source)
        if image.get("corpus") != "original":
            assertions = []
            for person in image.get("named_people") or []:
                if person == "Margaret Thatcher":
                    continue
                evidence = _relationship_sentences(packets, str(person))
                assertions.append({
                    "participants": ["Margaret Thatcher", str(person)],
                    "relationship": _relationship_kind(evidence),
                    "evidence": evidence[:6],
                    "evidence_source": "completed_canonical_research_packets",
                })
            if assertions != image.get("relationship_assertions", []):
                changed.append({
                    "image_id": image["image_id"],
                    "before": image.get("relationship_assertions", []),
                    "after": assertions,
                })
            image["relationship_assertions"] = assertions
        records.append(image)
    audit = {
        "schema_version": 1,
        "policy_version": "bounded-explicit-pair-relationship-evidence-v2",
        "changed_image_count": len(changed),
        "changes": changed,
        "generated_at": utc_now(),
    }
    return records, audit


def _relationship_kind(evidence: Sequence[dict[str, Any]]) -> str:
    """Return the relationship kind."""
    texts = [row["text"].casefold() for row in evidence]
    for text in texts:
        hostile = bool(re.search(r"\b(enemy|adversar\w*|hostil\w*|opponent)\b", text))
        cooperative = bool(re.search(r"\b(friend|partner|cooperat\w*|rapprochement)\b", text))
        if hostile and cooperative:
            return "adversary_to_partner"
    text = " ".join(texts)
    if re.search(r"\b(all(?:y|ies|iance)|collaborat\w*)\b", text):
        return "ally"
    if "friend" in text:
        return "friend"
    if "opponent" in text:
        return "opponent"
    if re.search(r"\b(enemy|adversar\w*|hostil\w*)\b", text):
        return "adversary"
    return "unknown"


def _compact_visual(analysis: dict[str, Any]) -> dict[str, Any]:
    people = analysis.get("people") if isinstance(analysis.get("people"), dict) else {}
    historical = analysis.get("historical_context") if isinstance(analysis.get("historical_context"), dict) else {}
    return {
        "description": _clean(analysis.get("description"), 700),
        "scene_summary": _clean(analysis.get("scene_summary"), 500),
        "scene_types": _clean_list(analysis.get("scene_types"), 10, 100),
        "setting": analysis.get("setting") if isinstance(analysis.get("setting"), dict) else {},
        "shot_type": (analysis.get("composition") or {}).get("shot_type"),
        "subject_prominence": (analysis.get("composition") or {}).get("subject_prominence"),
        "people_count_category": people.get("count_category"),
        "people_count_minimum": _people_count_minimum(people.get("count_category")),
        "activities": _clean_list(people.get("primary_subject_activities"), 10, 120),
        "moods": _clean_list(people.get("primary_subject_moods"), 10, 120),
        "themes": _clean_list(analysis.get("themes"), 12, 120),
        "tone": _clean_list(analysis.get("tone"), 10, 120),
        "visual_energy": analysis.get("visual_energy"),
        "visible_elements": _clean_list(analysis.get("visible_elements"), 14, 140),
        "visible_symbols": _clean_list(historical.get("visible_symbols"), 10, 140),
        "historical_specificity": historical.get("specificity"),
        "event_hint": _clean(historical.get("event_or_context_hint"), 400),
    }


def load_image_corpus(
    project_dir: Path, work_dir: Path, packets: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load image corpus."""
    baseline_path = project_dir / "image_analysis.json"
    baseline = read_json(baseline_path)
    if baseline.get("schema_version") != 3 or len(baseline.get("items") or {}) != EXPECTED_BASELINE_IMAGES:
        raise RuntimeError("baseline must contain exactly 69 schema-v3 images")
    if len(baseline.get("path_index") or {}) != EXPECTED_BASELINE_IMAGES:
        raise RuntimeError("baseline path index must contain exactly 69 images")
    discovered_path = work_dir / "canonical_image_analysis.json"
    discovered = read_json(discovered_path)
    production = read_json(work_dir / "production_ready_manifest.json")
    source_manifest = read_json(work_dir / "source_and_attribution_manifest.json")
    identities = read_json(work_dir / "source_grounded_identity_metadata.json")
    production_rows = production.get("records") or []
    if len(production_rows) != EXPECTED_DISCOVERED_IMAGES:
        raise RuntimeError("production-ready discovery manifest must contain exactly 22 images")
    if len(discovered.get("items") or {}) != EXPECTED_DISCOVERED_IMAGES:
        raise RuntimeError("canonical discovered analysis must contain exactly 22 images")
    allowed_ids = {row["candidate_id"] for row in production_rows}
    source_by_id = {
        row["candidate_id"]: row for row in source_manifest.get("records") or []
        if row.get("candidate_id") in allowed_ids
    }
    identity_by_id = {
        row["candidate_id"]: row for row in identities.get("records") or []
        if row.get("candidate_id") in allowed_ids
    }
    if set(source_by_id) != allowed_ids or set(identity_by_id) != allowed_ids:
        raise RuntimeError("source-grounded discovered identity/provenance coverage is incomplete")
    rows = []
    for filename, digest in sorted(baseline["path_index"].items()):
        if filename.startswith("._") or not re.fullmatch(r"t\d+\.(?:jpe?g|png|webp)", filename, re.I):
            raise RuntimeError(f"unexpected original image filename: {filename}")
        path = project_dir / "images" / filename
        if not path.is_file() or sha256_file(path) != digest:
            raise RuntimeError(f"original image content mismatch: {filename}")
        item = baseline["items"][digest]
        rows.append({
            "image_id": f"original:{filename}", "image_sha256": digest, "corpus": "original",
            "filename": filename, "local_path": str(path.resolve()),
            "source_page_url": None, "source_caption": None, "source_event": None,
            "source_date": None, "identity_basis": "unknown", "identity_confidence": "low",
            "named_people": [], "source_evidence": [], "relationship_assertions": [],
            "visual": _compact_visual(item["analysis"]), "analysis": item["analysis"],
        })
    for source in sorted(production_rows, key=lambda row: row["candidate_id"]):
        candidate_id = source["candidate_id"]
        digest = source["original_sha256"]
        if source.get("rights_status") not in {"public_domain", "clear_reuse", "attribution_required"}:
            raise RuntimeError(f"rights-ineligible discovery entered image corpus: {candidate_id}")
        item = discovered["items"].get(digest)
        identity = identity_by_id[candidate_id]
        attribution = source_by_id[candidate_id]
        if not isinstance(item, dict) or item.get("image_hash") != digest:
            raise RuntimeError(f"missing discovered analysis: {candidate_id}")
        path = work_dir.parent / "exported_kept" / source["export_relative_path"]
        if not path.is_file() or sha256_file(path) != digest:
            raise RuntimeError(f"discovered image content mismatch: {candidate_id}")
        named_people = sorted(set(identity.get("named_people") or []))
        relationship_assertions = []
        for person in named_people:
            if person == "Margaret Thatcher":
                continue
            evidence = _relationship_sentences(packets, person)
            relationship_assertions.append({
                "participants": ["Margaret Thatcher", person],
                "relationship": _relationship_kind(evidence),
                "evidence": evidence[:6],
                "evidence_source": "completed_canonical_research_packets",
            })
        rows.append({
            "image_id": f"discovered:{candidate_id}", "candidate_id": candidate_id,
            "image_sha256": digest, "corpus": "discovered_production_ready",
            "filename": path.name, "local_path": str(path.resolve()),
            "source_page_url": attribution.get("source_page_url"),
            "source_caption": _clean(attribution.get("caption"), 900),
            "source_event": _clean(identity.get("source_event_summary"), 700),
            "source_date": _clean(identity.get("approximate_date"), 120),
            "identity_basis": identity.get("identity_basis"),
            "identity_confidence": identity.get("identity_confidence"),
            "named_people": named_people,
            "source_evidence": _clean_list(identity.get("source_evidence_fields"), 16, 400),
            "relationship_assertions": relationship_assertions,
            "rights_status": source.get("rights_status"),
            "visual": _compact_visual(item["analysis"]), "analysis": item["analysis"],
        })
    if len(rows) != EXPECTED_IMAGES or len({row["image_sha256"] for row in rows}) != EXPECTED_IMAGES:
        raise RuntimeError("image corpus must contain 91 unique images (69 + 22)")
    metadata = {
        "baseline_count": EXPECTED_BASELINE_IMAGES, "discovered_count": EXPECTED_DISCOVERED_IMAGES,
        "total_count": len(rows), "baseline_analysis_sha256": sha256_file(baseline_path),
        "discovered_analysis_sha256": sha256_file(discovered_path),
        "production_ready_manifest_sha256": sha256_file(work_dir / "production_ready_manifest.json"),
        "source_manifest_sha256": sha256_file(work_dir / "source_and_attribution_manifest.json"),
        "image_id_set_sha256": text_hash("\n".join(row["image_id"] for row in rows) + "\n"),
        "image_content_set_sha256": text_hash("\n".join(sorted(row["image_sha256"] for row in rows)) + "\n"),
    }
    return rows, metadata


def _entity_matches_image(entity: str, image: dict[str, Any]) -> bool:
    corpus = " ".join(
        [image.get("source_caption") or "", image.get("source_event") or ""]
        + list(image.get("named_people") or [])
    ).casefold()
    entity_key = entity.casefold()
    if entity_key in corpus:
        return True
    surname = entity_key.split()[-1]
    return len(surname) >= 5 and re.search(rf"\b{re.escape(surname)}\b", corpus) is not None


_ENTITY_TEXT_ALIASES = {
    "united kingdom": ("britain", "british", "uk"),
    "united states": ("america", "american", "usa", "u.s."),
    "soviet union": ("soviet", "ussr", "russia", "russian"),
    "conservative party": ("conservative", "tory"),
    "european economic community": ("eec", "common market"),
    "european community": ("european community", "ec"),
}


def _entity_is_explicit_in_quote(entity: str, quote_text: str, verified_text: str) -> bool:
    """Return true only when the quotation itself identifies the entity.

    Research context, source occasion and packet entity lists deliberately do not
    participate. They explain a quotation but do not make an entity visually
    compulsory.
    """
    text = html.unescape(f"{quote_text} {verified_text}").casefold()
    key = " ".join(str(entity or "").casefold().split())
    if not key:
        return False
    candidates = {key, *_ENTITY_TEXT_ALIASES.get(key, ())}
    words = key.split()
    if len(words) >= 2 and len(words[-1]) >= 5:
        candidates.add(words[-1])
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(candidate)}(?![a-z0-9])", text)
        for candidate in candidates if candidate
    )


_NON_PERSON_ENTITY_MARKERS = {
    "armed", "bbc", "britain", "british", "brussels", "christmas", "communism",
    "communist", "concorde", "conservative", "democracy", "empire", "europe",
    "european", "fascism", "federalism", "forces", "front", "government", "gulag",
    "health", "islam", "jerusalem", "labour", "left", "marxism", "nato", "nazism",
    "party", "prison", "socialism", "socialists", "state", "street", "thames", "trade",
    "union", "unions", "united", "wall", "west", "world",
}


def _entity_looks_like_person(entity: str) -> bool:
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", str(entity or ""))
    if len(words) < 2 or len(words) > 5:
        return False
    if any(word.casefold() in _NON_PERSON_ENTITY_MARKERS for word in words):
        return False
    return sum(word[:1].isupper() for word in words) >= 2


def attest_original_collection_identity(
    images: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply the curator-known identity of the original Thatcher collection.

    This is collection provenance, not facial identification. It attests only
    Margaret Thatcher as the principal subject; other people remain unknown
    unless a source caption or archive record already establishes them.
    """
    records = []
    attested = []
    for source in images:
        image = copy.deepcopy(source)
        if image.get("corpus") == "original":
            image["identity_basis"] = "curated_collection_attestation"
            image["identity_confidence"] = "high"
            image["named_people"] = sorted({
                *[str(value) for value in image.get("named_people") or []],
                "Margaret Thatcher",
            })
            evidence = list(image.get("source_evidence") or [])
            evidence.append(
                "Curator attestation: this project collection consists of Margaret Thatcher photographs; "
                "Margaret Thatcher is the principal subject. No other participant is inferred."
            )
            image["source_evidence"] = evidence
            attested.append({
                "image_id": image["image_id"],
                "image_sha256": image["image_sha256"],
                "attested_person": "Margaret Thatcher",
                "scope": "principal_subject_only",
                "other_participants": "unknown_unless_separately_source_grounded",
            })
        records.append(image)
    if len(attested) != EXPECTED_BASELINE_IMAGES:
        raise RuntimeError("collection attestation requires exactly 69 original images")
    manifest = {
        "schema_version": 1,
        "attestation_version": "original-thatcher-collection-curator-attestation-v1",
        "basis": "authoritative project collection provenance",
        "facial_identification_used": False,
        "attested_image_count": len(attested),
        "records": sorted(attested, key=lambda row: row["image_id"]),
        "record_set_sha256": value_hash(sorted(attested, key=lambda row: row["image_id"])),
    }
    return records, manifest


def normalise_contract_for_material_veto(
    contract: dict[str, Any], packet: dict[str, Any],
) -> dict[str, Any]:
    """Narrow a v1 contract to facts whose visual contradiction is material.

    Source occasion, contextual entities and source date remain available as
    advisory context but cease to be visual requirements unless the quotation
    itself explicitly identifies them.
    """
    if contract["quote_id"] != packet["quote_id"] or contract["quote_text"] != packet["quote_text"]:
        raise ValueError("contract and canonical packet identity differ")
    value = copy.deepcopy(contract)
    original_required_entities = list(contract.get("named_entities_required") or [])
    visually_required = sorted({
        entity for entity in original_required_entities
        if _entity_looks_like_person(entity)
        if _entity_is_explicit_in_quote(entity, packet["quote_text"], packet.get("verified_text") or "")
    })
    strict_relationship = contract["claim_type"] in {"relationship", "relationship_transformation"}
    strict_transition = contract.get("required_transition") in STRICT_RELATIONSHIP_TRANSITIONS
    relationship_required = strict_relationship or strict_transition

    value["named_entities_required"] = visually_required
    value["relationship_evidence_required"] = relationship_required
    if not relationship_required:
        value["initial_relationship"] = "none"
        value["final_relationship"] = "none"
        value["required_transition"] = "none"
        value["actor_count_minimum"] = min(int(value.get("actor_count_minimum") or 0), 1)
        value["required_actor_roles"] = []
    else:
        value["actor_count_minimum"] = max(2, int(value.get("actor_count_minimum") or 0))

    explicit_year_or_period = bool(re.search(
        r"\b(?:18|19|20)\d{2}\b|\b(?:war|falklands|cold war|winter of discontent)\b",
        packet["quote_text"].casefold(),
    ))
    if visually_required and contract["claim_type"] == "named_entity":
        value["historical_specificity"] = "person_specific"
    elif explicit_year_or_period and contract["claim_type"] == "named_event":
        value["historical_specificity"] = "event_specific"
    elif explicit_year_or_period:
        value["historical_specificity"] = "period_specific"
    else:
        value["historical_specificity"] = "general"

    value["neutral_portrait_allowed"] = not (
        relationship_required
        or (contract["claim_type"] in {"named_entity", "named_event"} and bool(visually_required))
    )
    hard_conflicts = []
    if relationship_required:
        hard_conflicts.extend([
            "Substituting an established ally for a required adversary",
            "Claiming a relationship transformation not established by source provenance",
        ])
    if visually_required:
        hard_conflicts.append(
            "Substituting a different identifiable person, country, organisation or event for an entity named in the quotation"
        )
    value["hard_conflicts"] = hard_conflicts
    value["misleading_implications_to_avoid"] = [
        "Do not turn source occasion, source date or merely contextual entities into compulsory visual content.",
        "Do not reject a neutral, symbolic or non-literal illustration unless it communicates a materially false story.",
    ]
    value["contract_version"] = V2_CONTRACT_VERSION
    value["v1_contract_sha256"] = value_hash(contract)
    value["mentioned_entities"] = list(packet.get("entities") or [])
    value["visually_required_entities"] = visually_required
    value["source_event_advisory_only"] = True
    value["source_date_advisory_only"] = True
    value["period_is_hard_constraint"] = explicit_year_or_period
    value["normalisation_changes"] = {
        "removed_context_only_entity_count": len(set(original_required_entities) - set(visually_required)),
        "relationship_requirement_removed": bool(contract.get("relationship_evidence_required")) and not relationship_required,
        "source_specificity_relaxed": value["historical_specificity"] != contract["historical_specificity"],
        "neutral_portrait_relaxed": value["neutral_portrait_allowed"] and not contract["neutral_portrait_allowed"],
    }
    return value


def deterministic_material_contradictions(
    contract: dict[str, Any], image: dict[str, Any],
) -> list[dict[str, str]]:
    """Hard-block only source-grounded material contradictions."""
    reasons: list[dict[str, str]] = []
    relationships = [row.get("relationship", "unknown") for row in image.get("relationship_assertions") or []]
    if contract["relationship_evidence_required"]:
        if len(image.get("named_people") or []) < 2 or not relationships or all(value == "unknown" for value in relationships):
            reasons.append({
                "rule": "missing_relationship_evidence",
                "detail": "The quotation materially depends on a relationship, but image provenance does not establish it.",
            })
        hostile_required = contract["initial_relationship"] in {"enemy", "adversary", "opponent"}
        if hostile_required and any(value in {"ally", "friend"} for value in relationships):
            reasons.append({
                "rule": "ally_adversary_confusion",
                "detail": "Established allies cannot depict the quotation's required adversarial relationship.",
            })
        if contract["required_transition"] in STRICT_RELATIONSHIP_TRANSITIONS and not any(
            value == "adversary_to_partner" for value in relationships
        ):
            reasons.append({
                "rule": "missing_transition",
                "detail": "Image provenance does not establish the required adversary-to-partner transformation.",
            })
    required_people = contract.get("visually_required_entities") or []
    named_other_people = [
        person for person in image.get("named_people") or []
        if str(person).casefold() != "margaret thatcher"
    ]
    substituted_people = [
        person for person in required_people
        if named_other_people and not _entity_matches_image(person, image)
    ]
    if substituted_people:
        reasons.append({
            "rule": "wrong_named_entity",
            "detail": "The image establishes a different participant while the quotation names: "
            + ", ".join(substituted_people),
        })
    explicit_counterparties = [
        entity for entity in contract.get("mentioned_entities") or []
        if str(entity).casefold() != "margaret thatcher"
        and _entity_is_explicit_in_quote(
            str(entity), contract.get("quote_text") or "", contract.get("verified_text") or "",
        )
    ]
    negotiation_required = bool(re.search(
        r"\b(?:negotiat\w*|concession\w*|reciprocat\w*|deal(?:ing)?|bargain\w*)\b",
        str(contract.get("required_action") or ""), re.I,
    ))
    if (
        explicit_counterparties
        and negotiation_required
        and named_other_people
        and any(value in {"ally", "friend"} for value in relationships)
        and not any(_entity_matches_image(entity, image) for entity in explicit_counterparties)
    ):
        reasons.append({
            "rule": "wrong_geopolitical_counterparty",
            "detail": (
                "The image depicts an established ally in place of the quotation's explicitly named "
                "negotiating counterparty: " + ", ".join(explicit_counterparties)
            ),
        })
    people_minimum = image["visual"].get("people_count_minimum")
    if contract["relationship_evidence_required"] and people_minimum == 1:
        reasons.append({
            "rule": "insufficient_actor_count",
            "detail": "A single-person image cannot depict the quotation's required relationship.",
        })
    return sorted(reasons, key=lambda row: (row["rule"], row["detail"]))


def deterministic_contradictions(contract: dict[str, Any], image: dict[str, Any]) -> list[dict[str, str]]:
    """Return the deterministic contradictions."""
    reasons: list[dict[str, str]] = []
    relationship_required = bool(contract["relationship_evidence_required"])
    relationships = [row.get("relationship", "unknown") for row in image.get("relationship_assertions") or []]
    if relationship_required:
        if len(image.get("named_people") or []) < 2 or not relationships or all(value == "unknown" for value in relationships):
            reasons.append({
                "rule": "missing_relationship_evidence",
                "detail": "The quotation requires a known relationship, but source-grounded image provenance does not establish one.",
            })
        hostile_required = contract["initial_relationship"] in {"enemy", "adversary", "opponent"}
        if hostile_required and any(value in {"ally", "friend"} for value in relationships):
            reasons.append({
                "rule": "ally_adversary_confusion",
                "detail": "The quotation requires an adversarial starting relationship, but local canonical evidence identifies established allies/friends.",
            })
        if contract["required_transition"] in {"enemy_to_friend", "opponent_to_partner", "conflict_to_peace"}:
            if not any(value == "adversary_to_partner" for value in relationships):
                reasons.append({
                    "rule": "missing_transition",
                    "detail": "No source-grounded adversary-to-partner transition is established for the depicted participants.",
                })
    required_entities = contract.get("named_entities_required") or []
    if required_entities and not any(_entity_matches_image(entity, image) for entity in required_entities):
        reasons.append({
            "rule": "wrong_named_entity",
            "detail": "None of the contract's required named entities is established by image provenance.",
        })
    people_minimum = image["visual"].get("people_count_minimum")
    if contract["actor_count_minimum"] >= 2 and people_minimum == 1:
        reasons.append({
            "rule": "insufficient_actor_count",
            "detail": "The image is a single-person scene but the proposition requires multiple actors.",
        })
    shot = str(image["visual"].get("shot_type") or "").casefold()
    portrait = people_minimum == 1 and any(word in shot for word in ("portrait", "head", "close"))
    if portrait and not contract["neutral_portrait_allowed"] and contract["claim_type"] in {
        "relationship", "relationship_transformation", "concrete_action", "named_event", "causal_claim",
    }:
        reasons.append({
            "rule": "portrait_substitution",
            "detail": "A neutral portrait would erase the contract's required relationship, action, event or mechanism.",
        })
    return sorted(reasons, key=lambda row: (row["rule"], row["detail"]))


def pair_prompt(
    image: dict[str, Any],
    pairs: Sequence[dict[str, Any]],
) -> str:
    """Return the pair prompt."""
    image_input = {key: image.get(key) for key in (
        "image_id", "image_sha256", "corpus", "source_caption", "source_event", "source_date",
        "identity_basis", "identity_confidence", "named_people", "source_evidence",
        "relationship_assertions", "visual",
    )}
    pair_inputs = [{
        "pair_id": row["pair_id"], "quote_id": row["quote_id"],
        "contract": row["contract"], "deterministic_contradictions": row["deterministic_contradictions"],
    } for row in pairs]
    schema = pair_response_schema(len(pairs))
    return f"""Prompt version: {PAIR_PROMPT_VERSION}
Act as an adversarial editorial safety judge. Assess every supplied quotation/image pair. Return JSON only.

Safety principle: a missed usable image is acceptable; a materially false, contradictory or misleading image is not.

Rules:
- Judge the quotation's dominant proposition and required relationship/mechanism, not shared broad themes.
- Source-grounded image identities and relationships are authoritative. Do not identify anyone from appearance and do not add identities.
- Unknown relationship evidence cannot support a relationship-dependent quotation.
- A meeting between established allies cannot illustrate turning an enemy into a friend. Diplomacy topic overlap does not rescue that contradiction.
- A single still can represent a documented relationship or transition only when source/canonical evidence supports the depicted participants and relationship.
- Treat every deterministic contradiction as a veto unless the input is internally malformed; explain rather than override it.
- Search for the strongest plausible case in favour, then the strongest contradiction or misleading implication.
- Keep each explanatory field concise: normally one sentence and never an essay.
- allow only when the image responsibly conveys the actual proposition. uncertain is conservative and will be vetoed downstream.
- Do not use outside retrieval, human labels or visual facial identification.
- Return exactly one top-level records array containing one object per PAIR CONTRACT.

JSON Schema:
{json.dumps(schema, sort_keys=True, separators=(',', ':'))}

IMAGE CONTRACT:
{json.dumps(image_input, sort_keys=True, separators=(',', ':'), ensure_ascii=False)}

PAIR CONTRACTS:
{json.dumps(pair_inputs, sort_keys=True, separators=(',', ':'), ensure_ascii=False)}
"""


def material_pair_prompt(
    image: dict[str, Any], pairs: Sequence[dict[str, Any]],
) -> str:
    """Build the v2 judgement prompt with a material-contradiction boundary."""
    image_input = {key: image.get(key) for key in (
        "image_id", "image_sha256", "corpus", "source_caption", "source_event", "source_date",
        "identity_basis", "identity_confidence", "named_people", "source_evidence",
        "relationship_assertions", "visual",
    )}
    pair_inputs = [{
        "pair_id": row["pair_id"], "quote_id": row["quote_id"],
        "contract": row["contract"], "deterministic_contradictions": row["deterministic_contradictions"],
    } for row in pairs]
    return f"""Prompt version: {V2_PAIR_PROMPT_VERSION}
Act as a conservative editorial safety veto. Assess every quotation/image pair and return JSON only.

The only question is whether publishing the image with the quotation would communicate a materially false,
contradictory or misleading story. This is not a demand for literal historical illustration.

Rules:
- Veto wrong relationships, relationship transformations, named people/entities, political sides, actions,
  events, polarity or before/after direction when the contradiction is material to the quotation.
- Established allies cannot depict enemies becoming friends. Broad diplomacy overlap cannot rescue this.
- Source-grounded image identity and relationships are authoritative. Never identify a person from appearance.
- Unknown relationship or identity cannot satisfy a quotation that explicitly requires that relationship or entity.
- Source occasion, packet date and contextual entities are advisory only unless the contract explicitly marks
  them as visually required. An earlier portrait may illustrate a later general quotation.
- A neutral portrait, symbolic image or non-literal scene is allowed when it does not tell a false story.
- Do not veto merely because an image fails to show the full mechanism, original speech occasion, every actor,
  or every institution discussed in the research context.
- Treat deterministic contradictions as controlling hard vetoes.
- uncertain remains ineligible downstream. Keep reasons concise and concrete.
- Do not use outside retrieval, tools, human labels or facial identification.

JSON Schema:
{json.dumps(pair_response_schema(len(pairs)), sort_keys=True, separators=(',', ':'))}

IMAGE CONTRACT:
{json.dumps(image_input, sort_keys=True, separators=(',', ':'), ensure_ascii=False)}

PAIR CONTRACTS:
{json.dumps(pair_inputs, sort_keys=True, separators=(',', ':'), ensure_ascii=False)}
"""


def validate_pair_response(
    value: Any, image: dict[str, Any], pairs: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate pair response."""
    if not isinstance(value, dict) or set(value) != {"records"} or not isinstance(value["records"], list):
        raise ValueError("pair response must contain only records")
    expected = {row["pair_id"]: row for row in pairs}
    if len(value["records"]) != len(expected):
        raise ValueError("pair response count mismatch")
    output = []
    seen = set()
    for record in value["records"]:
        if not isinstance(record, dict) or set(record) != set(PAIR_FIELDS):
            raise ValueError("pair response fields mismatch")
        pair_id = str(record["pair_id"])
        if pair_id not in expected or pair_id in seen:
            raise ValueError("unknown or duplicate pair ID")
        source = expected[pair_id]
        if record["quote_id"] != source["quote_id"] or record["image_id"] != image["image_id"]:
            raise ValueError("pair response changed immutable identity")
        if record["decision"] not in PAIR_DECISIONS or record["confidence"] not in CONFIDENCES:
            raise ValueError("pair decision enum invalid")
        if type(record["materially_misleading"]) is not bool:
            raise ValueError("materially_misleading must be boolean")
        if record["relationship_supported"] not in RELATIONSHIP_SUPPORT:
            raise ValueError("invalid relationship support")
        contradictions = record["contradiction_types"]
        if not isinstance(contradictions, list) or not contradictions or not set(contradictions).issubset(CONTRADICTION_TYPES):
            raise ValueError("invalid contradiction types")
        for field in ("dominant_visual_message", "reason"):
            if not isinstance(record[field], str) or not record[field].strip():
                raise ValueError(f"empty pair judgement field: {field}")
        contract = source["contract"]
        if contract["relationship_evidence_required"] and record["relationship_supported"] in {"unknown", "no"} and record["decision"] == "allow":
            raise ValueError("relationship-dependent pair allowed without relationship evidence")
        if source["deterministic_contradictions"] and record["decision"] == "allow":
            raise ValueError("model attempted to override deterministic contradiction")
        if "none" in contradictions and len(set(contradictions)) != 1:
            raise ValueError("none cannot coexist with contradiction types")
        if record["decision"] == "allow" and (
            record["materially_misleading"] or set(contradictions) != {"none"}
        ):
            raise ValueError("allow decision contains contradictory safety metadata")
        final_veto = bool(source["deterministic_contradictions"]) or record["decision"] != "allow"
        output.append({
            **copy.deepcopy(record),
            "contradiction_types": sorted(set(contradictions)),
            "deterministic_contradictions": source["deterministic_contradictions"],
            "final_decision": "veto" if final_veto else "allow",
            "final_reason": (
                "deterministic_contradiction" if source["deterministic_contradictions"]
                else "model_veto_or_uncertainty" if record["decision"] != "allow"
                else "model_allow_no_deterministic_conflict"
            ),
        })
        seen.add(pair_id)
    return sorted(output, key=lambda row: row["pair_id"])


def _load_frozen_split(project_dir: Path) -> dict[str, Any]:
    path = project_dir / "semantic_alignment_research/image_quote_shortlist_rerank_001/calibration_split.json"
    value = read_json(path)
    if not isinstance(value, dict) or len(value.get("calibration_pairs") or []) != 23 or len(value.get("evaluation_pairs") or []) != 76:
        raise RuntimeError("frozen pairing evidence must contain 23 calibration and 76 evaluation pairs")
    return value


def select_pilot(
    packets: Sequence[dict[str, Any]], images: Sequence[dict[str, Any]], split: dict[str, Any],
) -> dict[str, Any]:
    """Select pilot."""
    packet_ids = {row["quote_id"] for row in packets}
    image_by_candidate = {
        row.get("candidate_id"): row for row in images if row.get("candidate_id")
    }
    mandatory_images = [
        "00f4566964e0c88838ad", "0b15f11312a31033907d", "0fe7ed6333faf654b062",
        "168392f289b5df25f16b", "62fe0c8d105bf2ad7c95", "5202e374b764482c4cda",
    ]
    evaluation = split["evaluation_pairs"]
    counts = Counter(row["candidate_id"] for row in evaluation if row["candidate_id"] in image_by_candidate)
    selected_images = list(mandatory_images)
    for candidate_id, _count in sorted(counts.items(), key=lambda row: (-row[1], row[0])):
        if candidate_id not in selected_images:
            selected_images.append(candidate_id)
        if len(selected_images) == PILOT_IMAGE_COUNT:
            break
    if len(selected_images) != PILOT_IMAGE_COUNT or any(item not in image_by_candidate for item in selected_images):
        raise RuntimeError("cannot construct deterministic eight-image veto pilot")
    mandatory_quotes = [
        "e0770fc3d6619db33fb692301a29fbf15f0e8e2306bd568064e557a108f69ec3",
        "d778f363342e4b00d83cbd40464b23f565c4e6fa58f12966771532b64963c1ce",
    ]
    selected_quotes = list(mandatory_quotes)
    pair_counts = Counter(
        row["quote_hash"] for row in evaluation if row["candidate_id"] in selected_images
    )
    for quote_id, _count in sorted(pair_counts.items(), key=lambda row: (-row[1], row[0])):
        if quote_id in packet_ids and quote_id not in selected_quotes:
            selected_quotes.append(quote_id)
        if len(selected_quotes) == PILOT_QUOTE_COUNT:
            break
    if len(selected_quotes) < PILOT_QUOTE_COUNT:
        for quote_id in sorted(packet_ids):
            if quote_id not in selected_quotes:
                selected_quotes.append(quote_id)
            if len(selected_quotes) == PILOT_QUOTE_COUNT:
                break
    labelled = [
        row for row in evaluation
        if row["candidate_id"] in selected_images and row["quote_hash"] in selected_quotes
    ]
    if len(labelled) < 20:
        raise RuntimeError("veto pilot must contain at least 20 frozen held-out judgements")
    value = {
        "schema_version": SCHEMA_VERSION,
        "selection_version": "relation-veto-pilot-frozen-evidence-v1",
        "image_ids": [image_by_candidate[item]["image_id"] for item in selected_images],
        "candidate_ids": selected_images,
        "quote_ids": selected_quotes,
        "held_out_label_count": len(labelled),
        "held_out_positive_count": sum(bool(row["human_positive"]) for row in labelled),
        "held_out_negative_count": sum(not bool(row["human_positive"]) for row in labelled),
        "selection_uses_labels_only_for_stratification_and_evaluation": True,
        "human_labels_in_model_prompt": False,
        "image_id_set_sha256": text_hash("\n".join(selected_images) + "\n"),
        "quote_id_set_sha256": text_hash("\n".join(selected_quotes) + "\n"),
    }
    return value


def prepare(
    project_dir: Path, research_run: Path, work_dir: Path, output_dir: Path,
) -> dict[str, Any]:
    """Prepare validated relation-aware veto corpora and run manifests."""
    output_dir.mkdir(parents=True, exist_ok=True)
    packets, corpus_meta = load_completed_corpus(research_run)
    if len(packets) != EXPECTED_QUOTES or corpus_meta["unresolved_count"] != EXPECTED_UNRESOLVED:
        raise RuntimeError("quote corpus does not reconcile to 627 completed and five unresolved")
    images, image_meta = load_image_corpus(project_dir, work_dir, packets)
    split = _load_frozen_split(project_dir)
    pilot = select_pilot(packets, images, split)
    production_before = production_hashes(project_dir)
    compact_packets = [compact_contract_input(row) for row in packets]
    quote_manifest = {
        "schema_version": SCHEMA_VERSION, "records": compact_packets,
        "metadata": corpus_meta, "records_sha256": value_hash(compact_packets),
    }
    image_manifest = {
        "schema_version": SCHEMA_VERSION,
        "records": [{key: value for key, value in row.items() if key != "analysis"} for row in images],
        "metadata": image_meta,
    }
    scoring_analysis = {
        "schema_version": 3,
        "items": {
            row["image_sha256"]: {
                "image_hash": row["image_sha256"],
                "paths": [row["image_id"]],
                "analysis": row["analysis"],
            }
            for row in images
        },
        "path_index": {row["image_id"]: row["image_sha256"] for row in images},
    }
    atomic_write_json(output_dir / "quote_corpus_manifest.json", quote_manifest)
    atomic_write_json(output_dir / "image_corpus_manifest.json", image_manifest)
    atomic_write_json(output_dir / "image_analysis_for_scoring.json", scoring_analysis)
    atomic_write_json(output_dir / "pilot_manifest.json", pilot)
    atomic_write_json(output_dir / "semantic_contract_schema.json", CONTRACT_SCHEMA)
    atomic_write_json(output_dir / "pair_judgement_schema_example.json", pair_response_schema(PILOT_QUOTE_COUNT))
    atomic_write_json(output_dir / "production_hashes_before.json", production_before)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_name": output_dir.name,
        "model": MODEL, "sdk_version": getattr(genai, "__version__", "unknown"),
        "developer_api_version": SDK_API_VERSION_DEVELOPER,
        "vertex_api_version": SDK_API_VERSION_VERTEX,
        "thinking_level": THINKING_LEVEL, "temperature": TEMPERATURE,
        "contract_prompt_version": PROMPT_VERSION, "pair_prompt_version": PAIR_PROMPT_VERSION,
        "contract_schema_version": CONTRACT_SCHEMA_VERSION,
        "pair_schema_version": PAIR_SCHEMA_VERSION, "pricing_version": PRICING_VERSION,
        "standard_price_per_million": PRICES["gemini"], "batch_price_factor": BATCH_PRICE_FACTOR,
        "quote_count": len(packets), "image_count": len(images),
        "baseline_image_count": EXPECTED_BASELINE_IMAGES,
        "discovered_image_count": EXPECTED_DISCOVERED_IMAGES,
        "unresolved_quote_ids": corpus_meta["unresolved_quote_ids"],
        "tools_enabled": False, "google_search_enabled": False,
        "url_context_enabled": False, "image_generation_enabled": False,
        "contract_schema_sha256": value_hash(CONTRACT_SCHEMA),
        "pair_schema_sha256": value_hash(pair_response_schema(PILOT_QUOTE_COUNT)),
        "production_hash_aggregate_before": production_before["aggregate_sha256"],
        "generated_at": utc_now(),
    }
    atomic_write_json(output_dir / "run_manifest.json", manifest)
    return {"manifest": manifest, "pilot": pilot, "quote_metadata": corpus_meta, "image_metadata": image_meta}


def _usage(raw: dict[str, Any]) -> dict[str, int]:
    meta = raw.get("usageMetadata") or raw.get("usage_metadata") or {}
    return {
        "input_tokens": int(meta.get("promptTokenCount") or meta.get("prompt_token_count") or 0),
        "cached_tokens": int(meta.get("cachedContentTokenCount") or meta.get("cached_content_token_count") or 0),
        "output_tokens": int(meta.get("candidatesTokenCount") or meta.get("candidates_token_count") or 0),
        "thinking_tokens": int(meta.get("thoughtsTokenCount") or meta.get("thoughts_token_count") or 0),
    }


def calculate_cost(usage: dict[str, int], *, batch: bool) -> float:
    """Calculate cost."""
    factor = BATCH_PRICE_FACTOR if batch else INTERACTIVE_PRICE_FACTOR
    uncached = max(0, usage["input_tokens"] - usage["cached_tokens"])
    output = usage["output_tokens"] + usage["thinking_tokens"]
    return round(factor * (
        uncached * PRICES["gemini"]["input"]
        + usage["cached_tokens"] * PRICES["gemini"]["cached_input"]
        + output * PRICES["gemini"]["output"]
    ) / 1_000_000, 10)


def maximum_cost(prompt: str, max_output_tokens: int, *, batch: bool) -> float:
    """Return the maximum cost."""
    factor = BATCH_PRICE_FACTOR if batch else INTERACTIVE_PRICE_FACTOR
    extra_input_tokens = int(getattr(prompt, "estimated_extra_input_tokens", 0) or 0)
    return round(factor * (
        (estimate_tokens(prompt) + extra_input_tokens) * PRICES["gemini"]["input"]
        + max_output_tokens * PRICES["gemini"]["output"]
    ) / 1_000_000, 10)


def _estimate_contract_cost(packets: Sequence[dict[str, Any]], *, batch: bool, maximum: bool) -> float:
    prompts = [contract_prompt(row) for row in packets]
    output = CONTRACT_MAX_OUTPUT_TOKENS if maximum else EXPECTED_CONTRACT_OUTPUT_TOKENS
    factor = BATCH_PRICE_FACTOR if batch else INTERACTIVE_PRICE_FACTOR
    return round(factor * (
        sum(estimate_tokens(prompt) for prompt in prompts) * PRICES["gemini"]["input"]
        + len(prompts) * output * PRICES["gemini"]["output"]
    ) / 1_000_000, 6)


def build_preflight(output_dir: Path) -> dict[str, Any]:
    """Build preflight."""
    quote_manifest = read_json(output_dir / "quote_corpus_manifest.json")
    image_manifest = read_json(output_dir / "image_corpus_manifest.json")
    pilot = read_json(output_dir / "pilot_manifest.json")
    packet_by_id = {row["quote_id"]: row for row in quote_manifest["records"]}
    pilot_packets = [packet_by_id[quote_id] for quote_id in pilot["quote_ids"]]
    pilot_contract_expected = _estimate_contract_cost(pilot_packets, batch=False, maximum=False)
    pilot_contract_max = _estimate_contract_cost(pilot_packets, batch=False, maximum=True)
    # Pilot pair prompts cannot be rendered until contracts exist; bound them conservatively.
    pilot_pair_calls = PILOT_IMAGE_COUNT * math.ceil(PILOT_QUOTE_COUNT / PAIR_RECORDS_PER_REQUEST)
    pilot_pair_expected = round(pilot_pair_calls * (
        5_000 * PRICES["gemini"]["input"] + EXPECTED_PAIR_OUTPUT_TOKENS * PRICES["gemini"]["output"]
    ) / 1_000_000, 6)
    pilot_pair_max = round(pilot_pair_calls * (
        6_500 * PRICES["gemini"]["input"] + PAIR_MAX_OUTPUT_TOKENS * PRICES["gemini"]["output"]
    ) / 1_000_000, 6)
    remaining_packets = [
        row for row in quote_manifest["records"] if row["quote_id"] not in set(pilot["quote_ids"])
    ]
    full_contract_expected = _estimate_contract_cost(remaining_packets, batch=True, maximum=False)
    full_contract_max = _estimate_contract_cost(remaining_packets, batch=True, maximum=True)
    maximum_pair_items = EXPECTED_IMAGES * math.ceil(MAX_PAIR_CANDIDATES_PER_IMAGE / PAIR_RECORDS_PER_REQUEST)
    full_pair_expected = round(maximum_pair_items * (
        5_000 * PRICES["gemini"]["input"] + EXPECTED_PAIR_OUTPUT_TOKENS * PRICES["gemini"]["output"]
    ) * BATCH_PRICE_FACTOR / 1_000_000, 6)
    full_pair_max = round(maximum_pair_items * (
        6_500 * PRICES["gemini"]["input"] + PAIR_MAX_OUTPUT_TOKENS * PRICES["gemini"]["output"]
    ) * BATCH_PRICE_FACTOR / 1_000_000, 6)
    expected = pilot_contract_expected + pilot_pair_expected + full_contract_expected + full_pair_expected
    guarded = pilot_contract_max + pilot_pair_max + full_contract_max + full_pair_max
    value = {
        "schema_version": SCHEMA_VERSION, "model": MODEL,
        "quote_count": len(quote_manifest["records"]), "image_count": len(image_manifest["records"]),
        "pilot": {
            "contract_calls": PILOT_QUOTE_COUNT, "pair_calls": pilot_pair_calls,
            "expected_cost_usd": round(pilot_contract_expected + pilot_pair_expected, 6),
            "conservative_maximum_cost_usd": round(pilot_contract_max + pilot_pair_max, 6),
        },
        "full_batch": {
            "contract_items": EXPECTED_QUOTES, "pair_items_maximum": maximum_pair_items,
            "expected_cost_usd": round(full_contract_expected + full_pair_expected, 6),
            "conservative_maximum_cost_usd": round(full_contract_max + full_pair_max, 6),
            "batch_discount": "50% of standard equivalent model cost",
        },
        "expected_combined_cost_usd": round(expected, 6),
        "conservative_planned_maximum_usd": round(guarded, 6),
        "planned_execution_ceiling_usd": PLANNED_EXECUTION_CEILING_USD,
        "protected_repair_reserve_usd": REPAIR_RESERVE_USD,
        "absolute_hard_stop_usd": HARD_COMBINED_CEILING_USD,
        "within_planned_ceiling": guarded <= PLANNED_EXECUTION_CEILING_USD,
        "no_request_may_cross_hard_stop": True,
        "no_tools_search_or_grounding": True,
        "generated_at": utc_now(),
    }
    if not value["within_planned_ceiling"]:
        raise RuntimeError("conservative planned execution exceeds US$50 ceiling")
    atomic_write_json(output_dir / "cost_preflight.json", value)
    lines = [
        "# Relation-aware semantic veto cost preflight", "",
        f"- Model: `{MODEL}`", f"- Quotes: {EXPECTED_QUOTES}", f"- Images: {EXPECTED_IMAGES}",
        f"- Expected combined spend: US${expected:.4f}",
        f"- Conservative planned maximum: US${guarded:.4f}",
        f"- Planned ceiling: US${PLANNED_EXECUTION_CEILING_USD:.2f}",
        f"- Protected repair reserve: US${REPAIR_RESERVE_USD:.2f}",
        f"- Absolute hard stop: US${HARD_COMBINED_CEILING_USD:.2f}", "",
        "The full run uses Gemini Batch API pricing at 50% of standard cost. No model call was made by this preflight.",
    ]
    atomic_write_text(output_dir / "cost_preflight.md", "\n".join(lines) + "\n")
    return value


def build_pair_candidates(
    project_dir: Path,
    output_dir: Path,
    contracts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build the bounded selector-relevant pair set without reading production state."""
    quote_manifest = read_json(output_dir / "quote_corpus_manifest.json")
    image_manifest = read_json(output_dir / "image_corpus_manifest.json")
    image_db = read_json(output_dir / "image_analysis_for_scoring.json")
    quote_db = read_json(project_dir / "quote_analysis.json")
    packet_ids = {row["quote_id"] for row in quote_manifest["records"]}
    if set(contracts) != packet_ids:
        raise RuntimeError("pair candidate construction requires all 627 valid contracts")
    quote_items, quote_analysis_aliases = _resolve_quote_analysis_items(
        quote_manifest["records"], quote_db,
    )
    bot = _load_production_scorer()
    idf = bot.build_image_topic_idf(image_db)
    frozen = _load_frozen_split(project_dir)
    frozen_by_candidate: dict[str, set[str]] = defaultdict(set)
    for row in frozen["calibration_pairs"] + frozen["evaluation_pairs"]:
        frozen_by_candidate[str(row["candidate_id"])].add(str(row["quote_hash"]))
    analysis_by_id = {
        image_id: image_db["items"][digest]["analysis"]
        for image_id, digest in image_db["path_index"].items()
    }
    rows = []
    for image in image_manifest["records"]:
        image_id = image["image_id"]
        scored = []
        for quote_id in sorted(packet_ids):
            score, components, eligible = bot.score_image_for_quote(
                quote_items[quote_id]["analysis"], analysis_by_id[image_id], idf,
            )
            if eligible:
                scored.append({
                    "quote_id": quote_id,
                    "selector_score": round(float(score), 8),
                    "selector_components": components,
                })
        scored.sort(key=lambda row: (-row["selector_score"], row["quote_id"]))
        selected: dict[str, dict[str, Any]] = {
            row["quote_id"]: {**row, "selection_reasons": ["selector_top_25"]}
            for row in scored[:SELECTOR_CANDIDATE_COUNT]
        }
        candidate_id = image.get("candidate_id")
        for quote_id in sorted(frozen_by_candidate.get(str(candidate_id), set())):
            if quote_id not in packet_ids:
                continue
            score_row = next((row for row in scored if row["quote_id"] == quote_id), None)
            if score_row is None:
                score_row = {"quote_id": quote_id, "selector_score": None, "selector_components": {}}
            selected.setdefault(quote_id, {**score_row, "selection_reasons": []})
            selected[quote_id]["selection_reasons"].append("frozen_human_evaluation")
        for quote_id, contract in sorted(contracts.items()):
            if not contract["named_entities_required"]:
                continue
            if any(_entity_matches_image(entity, image) for entity in contract["named_entities_required"]):
                score_row = next((row for row in scored if row["quote_id"] == quote_id), None)
                if score_row is None:
                    score_row = {"quote_id": quote_id, "selector_score": None, "selector_components": {}}
                selected.setdefault(quote_id, {**score_row, "selection_reasons": []})
                selected[quote_id]["selection_reasons"].append("source_grounded_entity_match")
        ordered = sorted(
            selected.values(),
            key=lambda row: (
                "frozen_human_evaluation" not in row["selection_reasons"],
                "source_grounded_entity_match" not in row["selection_reasons"],
                -(row["selector_score"] if row["selector_score"] is not None else -1e9),
                row["quote_id"],
            ),
        )[:MAX_PAIR_CANDIDATES_PER_IMAGE]
        pair_rows = []
        for row in sorted(ordered, key=lambda item: item["quote_id"]):
            contract = contracts[row["quote_id"]]
            pair_rows.append({
                "pair_id": text_hash(f"{image_id}\n{row['quote_id']}"),
                "image_id": image_id,
                "quote_id": row["quote_id"],
                "selector_score": row["selector_score"],
                "selector_components": row["selector_components"],
                "selection_reasons": sorted(set(row["selection_reasons"])),
                "contract": contract,
                "deterministic_contradictions": deterministic_contradictions(contract, image),
            })
        rows.append({"image_id": image_id, "pairs": pair_rows})
    value = {
        "schema_version": SCHEMA_VERSION,
        "selector": "mrsMThatcher2.score_image_for_quote",
        "selector_candidate_count": SELECTOR_CANDIDATE_COUNT,
        "maximum_pairs_per_image": MAX_PAIR_CANDIDATES_PER_IMAGE,
        "image_count": len(rows),
        "pair_count": sum(len(row["pairs"]) for row in rows),
        "quote_analysis_aliases": quote_analysis_aliases,
        "records": rows,
        "production_state_read": False,
        "production_state_written": False,
        "generated_at": utc_now(),
    }
    atomic_write_json(output_dir / "pair_candidates.json", value)
    return value


def select_production_top_k(
    scored: Sequence[dict[str, Any]], current_image_ids: set[str], *, top_k: int = PRODUCTION_TOP_K,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Select production top k."""
    ordered = sorted(scored, key=lambda row: (-row["selector_score"], row["image_id"]))
    expanded = ordered[:top_k]
    current = [row for row in ordered if row["image_id"] in current_image_ids][:top_k]
    by_image: dict[str, dict[str, Any]] = {}
    for lane, rows in (("current_69_top5", current), ("expanded_91_top5", expanded)):
        for rank, row in enumerate(rows, 1):
            saved = by_image.setdefault(row["image_id"], {**row, "selection_reasons": [], "ranks": {}})
            saved["selection_reasons"].append(lane)
            saved["ranks"][lane] = rank
    union = sorted(by_image.values(), key=lambda row: row["image_id"])
    return current, expanded, union


def build_production_top5_candidates(
    project_dir: Path,
    output_dir: Path,
    contracts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build production top5 candidates."""
    quote_manifest = read_json(output_dir / "quote_corpus_manifest.json")
    image_manifest = read_json(output_dir / "image_corpus_manifest.json")
    image_db = read_json(output_dir / "image_analysis_for_scoring.json")
    quote_db = read_json(project_dir / "quote_analysis.json")
    packet_ids = {row["quote_id"] for row in quote_manifest["records"]}
    if set(contracts) != packet_ids or len(packet_ids) != EXPECTED_QUOTES:
        raise RuntimeError("production-oriented matrix requires all 627 valid contracts")
    quote_items, aliases = _resolve_quote_analysis_items(quote_manifest["records"], quote_db)
    image_rows = image_manifest["records"]
    current_image_ids = {
        row["image_id"] for row in image_rows if row.get("corpus") == "original"
    }
    if len(current_image_ids) != EXPECTED_BASELINE_IMAGES:
        raise RuntimeError("production-oriented matrix requires exactly 69 current images")
    analysis_by_id = {
        image_id: image_db["items"][digest]["analysis"]
        for image_id, digest in image_db["path_index"].items()
    }
    bot = _load_production_scorer()
    idf = bot.build_image_topic_idf(image_db)
    records = []
    union_ids: set[str] = set()
    expanded_ids: set[str] = set()
    current_ids: set[str] = set()
    for quote_id in sorted(packet_ids):
        scored = []
        for image in image_rows:
            score, components, eligible = bot.score_image_for_quote(
                quote_items[quote_id]["analysis"], analysis_by_id[image["image_id"]], idf,
            )
            if eligible:
                scored.append({
                    "image_id": image["image_id"],
                    "selector_score": round(float(score), 8),
                    "selector_components": components,
                })
        current, expanded, union = select_production_top_k(scored, current_image_ids)
        if len(current) != PRODUCTION_TOP_K or len(expanded) != PRODUCTION_TOP_K:
            raise RuntimeError(f"quote {quote_id} does not have five eligible images in both corpora")
        pair_rows = []
        for row in union:
            pair_id = text_hash(f"{row['image_id']}\n{quote_id}")
            union_ids.add(pair_id)
            pair_rows.append({
                "pair_id": pair_id,
                "image_id": row["image_id"],
                "quote_id": quote_id,
                "selector_score": row["selector_score"],
                "selector_components": row["selector_components"],
                "selection_reasons": sorted(set(row["selection_reasons"])),
                "ranks": row["ranks"],
                "contract": contracts[quote_id],
                "deterministic_contradictions": deterministic_contradictions(
                    contracts[quote_id], next(item for item in image_rows if item["image_id"] == row["image_id"]),
                ),
            })
        current_pair_ids = [text_hash(f"{row['image_id']}\n{quote_id}") for row in current]
        expanded_pair_ids = [text_hash(f"{row['image_id']}\n{quote_id}") for row in expanded]
        current_ids.update(current_pair_ids)
        expanded_ids.update(expanded_pair_ids)
        records.append({
            "quote_id": quote_id,
            "current_69_top5_pair_ids": current_pair_ids,
            "expanded_91_top5_pair_ids": expanded_pair_ids,
            "pairs": pair_rows,
        })
    value = {
        "schema_version": SCHEMA_VERSION,
        "selection_version": "production-oriented-top5-current69-expanded91-v1",
        "selector": "mrsMThatcher2.score_image_for_quote",
        "quote_count": len(records),
        "current_image_count": len(current_image_ids),
        "expanded_image_count": len(image_rows),
        "top_k": PRODUCTION_TOP_K,
        "current_pair_count": len(current_ids),
        "expanded_pair_count": len(expanded_ids),
        "union_pair_count": len(union_ids),
        "current_only_pair_count": len(current_ids - expanded_ids),
        "expanded_only_pair_count": len(expanded_ids - current_ids),
        "pair_id_set_sha256": text_hash("\n".join(sorted(union_ids)) + "\n"),
        "quote_analysis_aliases": aliases,
        "records": records,
        "generated_at": utc_now(),
    }
    atomic_write_json(output_dir / "production_top5_pair_candidates.json", value)
    return value


def build_material_veto_revision(
    project_dir: Path, source_dir: Path, output_dir: Path,
) -> dict[str, Any]:
    """Prepare the corrected v2 contract and full production-choice matrix offline."""
    output_dir.mkdir(parents=True, exist_ok=True)
    quote_manifest = read_json(source_dir / "quote_corpus_manifest.json")
    image_manifest = read_json(source_dir / "image_corpus_manifest.json")
    image_db = read_json(source_dir / "image_analysis_for_scoring.json")
    v1_contract_db = read_json(source_dir / "semantic_contracts.json")
    v1_pair_db = read_json(source_dir / "pair_judgements.json")
    packets = quote_manifest.get("records") or []
    if len(packets) != EXPECTED_QUOTES or len({row["quote_id"] for row in packets}) != EXPECTED_QUOTES:
        raise RuntimeError("v2 revision requires exactly 627 unique canonical packets")
    if len(image_manifest.get("records") or []) != EXPECTED_IMAGES:
        raise RuntimeError("v2 revision requires the immutable 69+22 image corpus")
    if set(v1_contract_db.get("records") or {}) != {row["quote_id"] for row in packets}:
        raise RuntimeError("v2 revision requires all 627 v1 contracts")

    images, attestation = attest_original_collection_identity(image_manifest["records"])
    images, relationship_audit = rebuild_source_grounded_relationship_assertions(images, packets)
    atomic_write_json(output_dir / "original_collection_identity_attestation.json", {
        **attestation, "generated_at": utc_now(),
    })
    atomic_write_json(output_dir / "attested_image_corpus_manifest.json", {
        "schema_version": 1,
        "source_image_manifest_sha256": sha256_file(source_dir / "image_corpus_manifest.json"),
        "records": images,
        "generated_at": utc_now(),
    })
    atomic_write_json(output_dir / "relationship_grounding_audit.json", relationship_audit)

    packet_by_id = {row["quote_id"]: row for row in packets}
    v2_contracts = {
        quote_id: normalise_contract_for_material_veto(contract, packet_by_id[quote_id])
        for quote_id, contract in sorted(v1_contract_db["records"].items())
    }
    contract_metrics = {
        "contract_count": len(v2_contracts),
        "v1_named_entity_requirement_count": sum(
            bool(row.get("named_entities_required")) for row in v1_contract_db["records"].values()
        ),
        "v2_visually_required_entity_count": sum(
            bool(row.get("visually_required_entities")) for row in v2_contracts.values()
        ),
        "context_only_entity_requirements_removed": sum(
            int(row["normalisation_changes"]["removed_context_only_entity_count"])
            for row in v2_contracts.values()
        ),
        "relationship_requirements_removed": sum(
            row["normalisation_changes"]["relationship_requirement_removed"]
            for row in v2_contracts.values()
        ),
        "source_specificity_relaxed": sum(
            row["normalisation_changes"]["source_specificity_relaxed"]
            for row in v2_contracts.values()
        ),
        "neutral_portrait_relaxed": sum(
            row["normalisation_changes"]["neutral_portrait_relaxed"]
            for row in v2_contracts.values()
        ),
    }
    atomic_write_json(output_dir / "semantic_contracts_v2.json", {
        "schema_version": 1,
        "contract_version": V2_CONTRACT_VERSION,
        "source_contract_file_sha256": sha256_file(source_dir / "semantic_contracts.json"),
        "metrics": contract_metrics,
        "records": v2_contracts,
        "generated_at": utc_now(),
    })

    quote_items, aliases = _resolve_quote_analysis_items(packets, read_json(project_dir / "quote_analysis.json"))
    analysis_by_id = {
        image_id: image_db["items"][digest]["analysis"]
        for image_id, digest in image_db["path_index"].items()
    }
    image_by_id = {row["image_id"]: row for row in images}
    current_image_ids = {
        row["image_id"] for row in images if row.get("corpus") == "original"
    }
    candidate_by_source_id = {
        row.get("candidate_id"): row["image_id"] for row in images if row.get("candidate_id")
    }
    split = _load_frozen_split(project_dir)
    frozen_by_quote: dict[str, set[str]] = defaultdict(set)
    for label in split["calibration_pairs"] + split["evaluation_pairs"]:
        image_id = candidate_by_source_id.get(str(label["candidate_id"]))
        if image_id and str(label["quote_hash"]) in packet_by_id:
            frozen_by_quote[str(label["quote_hash"])].add(image_id)

    bot = _load_production_scorer()
    idf = bot.build_image_topic_idf(image_db)
    candidate_records = []
    pair_by_id: dict[str, dict[str, Any]] = {}
    current_pair_ids: set[str] = set()
    expanded_pair_ids: set[str] = set()
    for quote_id in sorted(packet_by_id):
        scored = []
        for image in images:
            score, components, eligible = bot.score_image_for_quote(
                quote_items[quote_id]["analysis"], analysis_by_id[image["image_id"]], idf,
            )
            if eligible:
                scored.append({
                    "image_id": image["image_id"],
                    "selector_score": round(float(score), 8),
                    "selector_components": components,
                })
        current, expanded, union = select_production_top_k(
            scored, current_image_ids, top_k=V2_PRODUCTION_TOP_K,
        )
        if len(current) != V2_PRODUCTION_TOP_K or len(expanded) != V2_PRODUCTION_TOP_K:
            raise RuntimeError(f"quote {quote_id} lacks eight eligible candidates")
        by_image = {row["image_id"]: row for row in union}
        scored_by_image = {row["image_id"]: row for row in scored}
        for image_id in sorted(frozen_by_quote.get(quote_id, set())):
            if image_id not in by_image:
                score = scored_by_image.get(image_id, {
                    "image_id": image_id, "selector_score": None, "selector_components": {},
                })
                by_image[image_id] = {
                    **score, "selection_reasons": ["frozen_human_evaluation"], "ranks": {},
                }
            else:
                by_image[image_id]["selection_reasons"] = sorted({
                    *by_image[image_id]["selection_reasons"], "frozen_human_evaluation",
                })
        pairs = []
        for image_id, row in sorted(by_image.items()):
            pair_id = text_hash(f"{image_id}\n{quote_id}")
            pair = {
                "pair_id": pair_id,
                "image_id": image_id,
                "quote_id": quote_id,
                "selector_score": row["selector_score"],
                "selector_components": row["selector_components"],
                "selection_reasons": sorted(set(row["selection_reasons"])),
                "ranks": row["ranks"],
                "contract": v2_contracts[quote_id],
                "deterministic_contradictions": deterministic_material_contradictions(
                    v2_contracts[quote_id], image_by_id[image_id],
                ),
            }
            pair["v2_input_sha256"] = value_hash({
                "prompt_version": V2_PAIR_PROMPT_VERSION,
                "contract": pair["contract"],
                "image": {key: image_by_id[image_id].get(key) for key in (
                    "image_id", "image_sha256", "identity_basis", "identity_confidence",
                    "named_people", "source_caption", "source_event", "source_date",
                    "relationship_assertions", "visual",
                )},
                "deterministic_contradictions": pair["deterministic_contradictions"],
            })
            pairs.append(pair)
            pair_by_id[pair_id] = pair
        quote_current_ids = [text_hash(f"{row['image_id']}\n{quote_id}") for row in current]
        quote_expanded_ids = [text_hash(f"{row['image_id']}\n{quote_id}") for row in expanded]
        current_pair_ids.update(quote_current_ids)
        expanded_pair_ids.update(quote_expanded_ids)
        candidate_records.append({
            "quote_id": quote_id,
            "current_69_top8_pair_ids": quote_current_ids,
            "expanded_91_top8_pair_ids": quote_expanded_ids,
            "pairs": pairs,
        })

    candidate_value = {
        "schema_version": 2,
        "selection_version": V2_SELECTION_VERSION,
        "quote_count": len(candidate_records),
        "top_k": V2_PRODUCTION_TOP_K,
        "current_pair_count": len(current_pair_ids),
        "expanded_pair_count": len(expanded_pair_ids),
        "union_pair_count": len(pair_by_id),
        "current_winner_count": len(candidate_records),
        "quote_analysis_aliases": aliases,
        "pair_id_set_sha256": text_hash("\n".join(sorted(pair_by_id)) + "\n"),
        "records": candidate_records,
        "generated_at": utc_now(),
    }
    atomic_write_json(output_dir / "production_top8_pair_candidates_v2.json", candidate_value)

    reused: dict[str, dict[str, Any]] = {}
    reuse_counts = Counter()
    unresolved = []
    for pair_id, pair in sorted(pair_by_id.items()):
        old = (v1_pair_db.get("records") or {}).get(pair_id)
        if pair["deterministic_contradictions"]:
            judgement = local_deterministic_pair_veto(
                f"v2-local-{pair_id[:20]}", image_by_id[pair["image_id"]], pair,
            )["response"][0]
            judgement["v2_reuse_basis"] = "v2_material_deterministic_contradiction"
            reused[pair_id] = judgement
            reuse_counts["v2_material_deterministic_veto"] += 1
        elif old and old.get("final_decision") == "allow":
            judgement = copy.deepcopy(old)
            judgement["deterministic_contradictions"] = []
            judgement["v2_reuse_basis"] = "v1_allow_under_stricter_boundary"
            judgement["v2_input_sha256"] = pair["v2_input_sha256"]
            reused[pair_id] = judgement
            reuse_counts["v1_allow"] += 1
        else:
            unresolved.append({
                "pair_id": pair_id,
                "quote_id": pair["quote_id"],
                "image_id": pair["image_id"],
                "v2_input_sha256": pair["v2_input_sha256"],
                "prior_v1_status": old.get("final_decision") if old else "unjudged",
                "prior_v1_contradiction_types": old.get("contradiction_types") if old else [],
            })
    atomic_write_json(output_dir / "v2_reuse_manifest.json", {
        "schema_version": 1,
        "reuse_policy_version": V2_REUSE_VERSION,
        "policy": {
            "v1_allow": "reusable because v2 narrows rather than adds veto conditions",
            "v1_veto": "not reusable unless independently established by a v2 deterministic material contradiction",
            "unknown": "ineligible until judged",
        },
        "counts": dict(sorted(reuse_counts.items())),
        "reused_judgement_count": len(reused),
        "unresolved_judgement_count": len(unresolved),
        "records": reused,
        "generated_at": utc_now(),
    })
    atomic_write_json(output_dir / "v2_missing_judgement_manifest.json", {
        "schema_version": 1,
        "pair_prompt_version": V2_PAIR_PROMPT_VERSION,
        "record_count": len(unresolved),
        "records": unresolved,
        "generated_at": utc_now(),
    })

    evaluation = evaluate_material_veto_revision(
        project_dir, candidate_value, reused, candidate_by_source_id, image_by_id,
    )
    atomic_write_json(output_dir / "v2_offline_evaluation.json", evaluation)
    ledger = read_json(source_dir / "cost_ledger.json")
    observed = float(read_json(source_dir / "production_top5_preflight.json", {}).get(
        "observed_cost_per_pair_usd", 0.005057301,
    ))
    expected = len(unresolved) * observed
    remaining = V2_HARD_COMBINED_CEILING_USD - float(ledger["known_spend_usd"]) - float(ledger["ambiguous_exposure_usd"])
    preflight = {
        "schema_version": 1,
        "paid_calls_made": False,
        "missing_pair_count": len(unresolved),
        "observed_v1_cost_per_pair_usd": observed,
        "expected_incremental_cost_usd": round(expected, 6),
        "expected_with_20_percent_contingency_usd": round(expected * 1.2, 6),
        "known_spend_before_usd": ledger["known_spend_usd"],
        "ambiguous_exposure_before_usd": ledger["ambiguous_exposure_usd"],
        "remaining_under_authorised_hard_ceiling_usd": round(remaining, 6),
        "authorised_hard_ceiling_usd": V2_HARD_COMBINED_CEILING_USD,
        "fits_authorised_hard_ceiling": expected * 1.2 <= remaining + 1e-9,
        "further_execution_authorised": True,
        "generated_at": utc_now(),
    }
    atomic_write_json(output_dir / "v2_cost_preflight.json", preflight)
    report_lines = [
        "# Relation-Aware Material Veto v2 Offline Revision", "",
        "No provider call was made. The v1 run remains immutable and live production remains unchanged.", "",
        "## Corrected boundary", "",
        f"- Original images curator-attested as Margaret Thatcher: {attestation['attested_image_count']}",
        f"- V1 contracts with named visual requirements: {contract_metrics['v1_named_entity_requirement_count']}",
        f"- V2 contracts with quotation-explicit visual entities: {contract_metrics['v2_visually_required_entity_count']}",
        f"- Context-only entity requirements removed: {contract_metrics['context_only_entity_requirements_removed']}",
        f"- Source specificity constraints relaxed: {contract_metrics['source_specificity_relaxed']}", "",
        "## Coverage and reuse", "",
        f"- Quotations represented: {candidate_value['quote_count']}/627",
        f"- Current production winners represented: {candidate_value['current_winner_count']}/627",
        f"- Unique top-eight/frozen pairs: {candidate_value['union_pair_count']}",
        f"- Judgements reusable without a new model call: {len(reused)}",
        f"- Pairs still requiring v2 judgement: {len(unresolved)}", "",
        "## Offline gates", "",
        *[f"- {name}: {'PASS' if passed else 'FAIL'}" for name, passed in evaluation["gates"].items()], "",
        "## Cost preflight", "",
        f"- Expected incremental cost: US${preflight['expected_incremental_cost_usd']:.4f}",
        f"- Expected with contingency: US${preflight['expected_with_20_percent_contingency_usd']:.4f}",
        f"- Remaining under authorised US$100 ceiling: US${preflight['remaining_under_authorised_hard_ceiling_usd']:.4f}",
        f"- Fits authorised hard ceiling: {preflight['fits_authorised_hard_ceiling']}", "",
        "The corrected v2 remains blocked until all unknown candidate pairs are judged and every strict gate passes.",
    ]
    atomic_write_text(output_dir / "relation_aware_material_veto_v2_report.md", "\n".join(report_lines) + "\n")
    return {
        "contracts": contract_metrics,
        "candidates": {key: candidate_value[key] for key in (
            "quote_count", "top_k", "current_pair_count", "expanded_pair_count", "union_pair_count",
        )},
        "reuse": {"reused": len(reused), "unresolved": len(unresolved), **dict(reuse_counts)},
        "evaluation": evaluation,
        "preflight": preflight,
    }


def evaluate_material_veto_revision(
    project_dir: Path,
    candidates: dict[str, Any],
    judgements: dict[str, dict[str, Any]],
    image_id_by_candidate: dict[str, str],
    image_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Apply the original safety and retention criteria without relabelling them."""
    pair_by_id = {
        pair["pair_id"]: pair
        for quote in candidates["records"] for pair in quote["pairs"]
    }
    split = _load_frozen_split(project_dir)
    eligible_labels, excluded = partition_frozen_evaluation_labels(
        split["evaluation_pairs"], image_id_by_candidate,
    )
    records = []
    for label in eligible_labels:
        image_id = image_id_by_candidate[label["candidate_id"]]
        pair_id = text_hash(f"{image_id}\n{label['quote_hash']}")
        judgement = judgements.get(pair_id)
        withdrawn_positive = _is_operator_withdrawn_positive(
            label, (image_by_id or {}).get(image_id),
        )
        records.append({
            "pair_id": pair_id,
            "candidate_id": label["candidate_id"],
            "quote_id": label["quote_hash"],
            "human_positive": bool(label["human_positive"]),
            "positive_calibration_eligible": bool(label["human_positive"]) and not withdrawn_positive,
            "operator_withdrawn_positive": withdrawn_positive,
            "operator_override_reason": (
                "Operator explicitly withdrew accepted Thatcher/Reagan pairings from positive calibration."
                if withdrawn_positive else None
            ),
            "human_decision": label["human_decision"],
            "human_safety_rejection": _is_explicit_safety_rejection(label),
            "system_decision": judgement.get("final_decision") if judgement else "unknown",
        })
    raw_positives = [row for row in records if row["human_positive"]]
    withdrawn_positives = [row for row in records if row["operator_withdrawn_positive"]]
    positives = [row for row in records if row["positive_calibration_eligible"]]
    retained = [row for row in positives if row["system_decision"] == "allow"]
    safety = [row for row in records if row["human_safety_rejection"]]
    safety_false_accepts = [row for row in safety if row["system_decision"] == "allow"]
    safety_vetoed = [row for row in safety if row["system_decision"] == "veto"]
    critical_quote = "e0770fc3d6619db33fb692301a29fbf15f0e8e2306bd568064e557a108f69ec3"
    ally_candidates = {
        "00f4566964e0c88838ad", "0b15f11312a31033907d", "0fe7ed6333faf654b062",
        "149051134f38e43abb40", "168392f289b5df25f16b", "5398b106f38f66e60fa9",
    }
    critical = [
        row for row in records
        if row["quote_id"] == critical_quote and row["candidate_id"] in ally_candidates
    ]
    current_winners = [row["current_69_top8_pair_ids"][0] for row in candidates["records"]]
    current_winner_judged = sum(pair_id in judgements for pair_id in current_winners)
    allowed_quote_count = sum(
        any(judgements.get(pair["pair_id"], {}).get("final_decision") == "allow" for pair in quote["pairs"])
        for quote in candidates["records"]
    )
    all_pairs_judged = len(judgements) == len(pair_by_id) and set(judgements) == set(pair_by_id)
    retention_rate = _ratio(len(retained), len(positives))
    allowed_quote_rate = _ratio(allowed_quote_count, EXPECTED_QUOTES)
    gates = {
        "zero_severe_false_accepts": not safety_false_accepts,
        "all_explicit_safety_rejections_vetoed": _all_safety_rejections_vetoed(safety),
        "all_critical_ally_enemy_cases_vetoed": bool(critical) and all(
            row["system_decision"] == "veto" for row in critical
        ),
        "at_least_95_percent_human_approved_pairs_retained": (
            retention_rate is not None and retention_rate >= 0.95
        ),
        "all_626_current_production_winners_judged": current_winner_judged == EXPECTED_QUOTES,
        "all_candidate_pairs_judged": all_pairs_judged,
        "at_least_95_percent_quotes_have_allowed_candidate": (
            allowed_quote_rate is not None and allowed_quote_rate >= 0.95
        ),
        "unknown_pairs_remain_ineligible": all(
            row.get("final_decision") in {"allow", "veto"} for row in judgements.values()
        ),
    }
    return {
        "schema_version": 2,
        "evaluation_policy": "original-retention-and-coverage-gates-restored-v1",
        "passed": all(gates.values()),
        "gates": gates,
        "metrics": {
            "candidate_pair_count": len(pair_by_id),
            "judged_pair_count": len(judgements),
            "unknown_pair_count": len(pair_by_id) - len(judgements),
            "raw_positive_count": len(raw_positives),
            "operator_withdrawn_positive_count": len(withdrawn_positives),
            "positive_count": len(positives),
            "positive_retained_count": len(retained),
            "positive_retention_rate": retention_rate,
            "safety_rejection_count": len(safety),
            "safety_rejection_veto_count": len(safety_vetoed),
            "safety_false_accept_count": len(safety_false_accepts),
            "critical_ally_case_count": len(critical),
            "critical_ally_veto_count": sum(row["system_decision"] == "veto" for row in critical),
            "current_winner_judged_count": current_winner_judged,
            "quotes_with_allowed_candidate": allowed_quote_count,
            "quotes_with_allowed_candidate_rate": allowed_quote_rate,
            "excluded_frozen_pair_count": len(excluded),
        },
        "records": records,
        "generated_at": utc_now(),
    }


def build_v2_batch_manifest(output_dir: Path) -> dict[str, Any]:
    """Build v2 batch manifest."""
    path = output_dir / "v2_batch_manifest.json"
    candidates = read_json(output_dir / "production_top8_pair_candidates_v2.json")
    reuse = read_json(output_dir / "v2_reuse_manifest.json")
    all_pairs = {
        pair["pair_id"]: pair
        for quote in candidates["records"] for pair in quote["pairs"]
    }
    source_hash = candidates["pair_id_set_sha256"]
    if path.exists():
        saved = read_json(path)
        if saved.get("source_pair_id_set_sha256") != source_hash:
            raise RuntimeError("saved v2 batch manifest does not match the candidate matrix")
        return saved
    unresolved_ids = sorted(set(all_pairs) - set(reuse["records"]))
    by_image: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pair_id in unresolved_ids:
        by_image[all_pairs[pair_id]["image_id"]].append(all_pairs[pair_id])
    items = []
    for image_id in sorted(by_image):
        pairs = sorted(by_image[image_id], key=lambda row: (row["quote_id"], row["pair_id"]))
        for offset in range(0, len(pairs), PAIR_RECORDS_PER_REQUEST):
            chunk = pairs[offset:offset + PAIR_RECORDS_PER_REQUEST]
            pair_ids = [row["pair_id"] for row in chunk]
            logical_id = "v2-material-pairs-" + text_hash(
                image_id + "\n" + "\n".join(pair_ids)
            )[:32]
            items.append({
                "logical_id": logical_id,
                "image_id": image_id,
                "pair_ids": pair_ids,
                "pair_input_hashes": [row["v2_input_sha256"] for row in chunk],
            })
    batches = []
    for number, offset in enumerate(range(0, len(items), PRODUCTION_BATCH_MAX_ITEMS), 1):
        block = items[offset:offset + PRODUCTION_BATCH_MAX_ITEMS]
        batches.append({
            "batch_id": f"material-veto-v2-pair-judgements-001-{number:02d}",
            "logical_ids": [row["logical_id"] for row in block],
            "item_count": len(block),
        })
    value = {
        "schema_version": 1,
        "manifest_version": "material-veto-v2-fixed-batches-v1",
        "source_pair_id_set_sha256": source_hash,
        "candidate_pair_count": len(all_pairs),
        "reused_pair_count": len(reuse["records"]),
        "missing_pair_count": len(unresolved_ids),
        "missing_pair_id_set_sha256": text_hash("\n".join(unresolved_ids) + "\n"),
        "batch_item_count": len(items),
        "provider_batch_count": len(batches),
        "maximum_items_per_provider_batch": PRODUCTION_BATCH_MAX_ITEMS,
        "items": items,
        "provider_batches": batches,
        "created_at": utc_now(),
    }
    atomic_write_json(path, value)
    return value


def _v2_runtime_items(output_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    candidates = read_json(output_dir / "production_top8_pair_candidates_v2.json")
    manifest = build_v2_batch_manifest(output_dir)
    images = {
        row["image_id"]: row
        for row in read_json(output_dir / "attested_image_corpus_manifest.json")["records"]
    }
    pair_by_id = {
        pair["pair_id"]: pair
        for quote in candidates["records"] for pair in quote["pairs"]
    }
    runtime = {}
    for saved in manifest["items"]:
        image = images[saved["image_id"]]
        pairs = [pair_by_id[pair_id] for pair_id in saved["pair_ids"]]
        if [row["v2_input_sha256"] for row in pairs] != saved["pair_input_hashes"]:
            raise RuntimeError(f"v2 item input hash mismatch: {saved['logical_id']}")
        logical_id = saved["logical_id"]
        runtime[logical_id] = {
            "logical_id": logical_id,
            "image": image,
            "prompt": material_pair_prompt(image, pairs),
            "schema": pair_response_schema(len(pairs)),
            "max_output_tokens": PAIR_MAX_OUTPUT_TOKENS,
            "expected_pair_ids": saved["pair_ids"],
            "pairs": pairs,
            "recovery_splits": [{
                "logical_id": f"{logical_id}-split-{number:02d}",
                "prompt": material_pair_prompt(image, [pair]),
                "schema": pair_response_schema(1),
                "max_output_tokens": PAIR_MAX_OUTPUT_TOKENS,
                "pair": pair,
                "validator": (
                    lambda value, image=image, pair=pair:
                    validate_pair_response(value, image, [pair])
                ),
            } for number, pair in enumerate(pairs, 1)],
        }
    return runtime, pair_by_id


def prepare_v2_execution(source_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Prepare v2 execution."""
    ledger = initialise_v2_cost_ledger(source_dir, output_dir)
    manifest = build_v2_batch_manifest(output_dir)
    runtime, _pairs = _v2_runtime_items(output_dir)
    batch_maxima = []
    for batch in manifest["provider_batches"]:
        maximum = sum(
            maximum_cost(runtime[logical_id]["prompt"], PAIR_MAX_OUTPUT_TOKENS, batch=True)
            for logical_id in batch["logical_ids"]
        )
        batch_maxima.append(round(maximum, 6))
    preflight = read_json(output_dir / "v2_cost_preflight.json")
    projected = (
        float(ledger.value["known_spend_usd"])
        + float(ledger.value["ambiguous_exposure_usd"])
        + float(preflight["expected_with_20_percent_contingency_usd"])
    )
    value = {
        "schema_version": 1,
        "model": MODEL,
        "pair_prompt_version": V2_PAIR_PROMPT_VERSION,
        "pair_schema_version": PAIR_SCHEMA_VERSION,
        "candidate_pair_count": manifest["candidate_pair_count"],
        "reused_pair_count": manifest["reused_pair_count"],
        "missing_pair_count": manifest["missing_pair_count"],
        "batch_item_count": manifest["batch_item_count"],
        "provider_batch_count": manifest["provider_batch_count"],
        "provider_batch_maximum_reservations_usd": batch_maxima,
        "expected_incremental_cost_usd": preflight["expected_incremental_cost_usd"],
        "expected_with_20_percent_contingency_usd": preflight["expected_with_20_percent_contingency_usd"],
        "known_spend_before_usd": ledger.value["known_spend_usd"],
        "ambiguous_exposure_before_usd": ledger.value["ambiguous_exposure_usd"],
        "hard_combined_ceiling_usd": ledger.value["hard_combined_ceiling_usd"],
        "projected_combined_exposure_usd": round(projected, 6),
        "within_authorised_ceiling": projected <= V2_HARD_COMBINED_CEILING_USD + 1e-9,
        "no_provider_call_made": True,
        "generated_at": utc_now(),
    }
    if not value["within_authorised_ceiling"]:
        raise RuntimeError("corrected v2 execution does not fit the authorised US$100 ceiling")
    atomic_write_json(output_dir / "v2_execution_preflight.json", value)
    atomic_write_text(output_dir / "v2_execution_preflight.md", "\n".join([
        "# Material Veto v2 Execution Preflight", "",
        f"- Model: `{MODEL}`",
        f"- Candidate pairs: {value['candidate_pair_count']}",
        f"- Reused judgements: {value['reused_pair_count']}",
        f"- New judgements: {value['missing_pair_count']}",
        f"- Batch items: {value['batch_item_count']}",
        f"- Sequential provider batches: {value['provider_batch_count']}",
        f"- Expected incremental cost: US${float(value['expected_incremental_cost_usd']):.4f}",
        f"- Expected with contingency: US${float(value['expected_with_20_percent_contingency_usd']):.4f}",
        f"- Projected combined exposure: US${value['projected_combined_exposure_usd']:.4f}",
        f"- Hard combined ceiling: US${float(value['hard_combined_ceiling_usd']):.2f}",
        "", "No provider call was made by this preflight.",
    ]) + "\n")
    return value


def run_material_veto_v2(
    project_dir: Path,
    source_dir: Path,
    output_dir: Path,
    *,
    execute: bool,
    confirmed_limit: float,
    poll_seconds: float = 30.0,
) -> dict[str, Any]:
    """Run material veto v2."""
    if not execute or confirmed_limit != V2_HARD_COMBINED_CEILING_USD:
        raise RuntimeError("v2 execution requires --execute and exact --confirm-combined-limit-usd 100")
    preflight = prepare_v2_execution(source_dir, output_dir)
    if not preflight["within_authorised_ceiling"]:
        raise RuntimeError("v2 execution preflight did not pass")
    developer, vertex, _transport = transport_preflight(
        project_dir, output_dir, inspect_availability=True,
    )
    ledger = initialise_v2_cost_ledger(source_dir, output_dir)
    router = RelationRouter(output_dir, developer, vertex, ledger)
    batch_runner = DeveloperBatchRunner(
        output_dir, router, ledger, poll_seconds=poll_seconds,
    )
    manifest = build_v2_batch_manifest(output_dir)
    runtime, pair_by_id = _v2_runtime_items(output_dir)
    item_by_id = runtime
    reuse = read_json(output_dir / "v2_reuse_manifest.json")
    pair_db_path = output_dir / "pair_judgements_v2.json"
    pair_db = read_json(pair_db_path, {}) or {
        "schema_version": 2,
        "pair_prompt_version": V2_PAIR_PROMPT_VERSION,
        "records": copy.deepcopy(reuse["records"]),
        "failures": {},
        "created_at": utc_now(),
    }
    if not set(reuse["records"]).issubset(pair_db.get("records") or {}):
        raise RuntimeError("v2 pair database lost a reusable judgement")
    atomic_write_json(pair_db_path, pair_db)
    for provider_batch in manifest["provider_batches"]:
        block = [item_by_id[logical_id] for logical_id in provider_batch["logical_ids"]]
        validators = {
            item["logical_id"]: (
                lambda value, image=item["image"], pairs=item["pairs"]:
                validate_pair_response(value, image, pairs)
            )
            for item in block
        }
        results = batch_runner.run(
            batch_id=provider_batch["batch_id"], items=block, validators=validators,
        )
        for item in block:
            for judgement in results[item["logical_id"]]["response"]:
                pair_db["records"][judgement["pair_id"]] = judgement
        pair_db["updated_at"] = utc_now()
        atomic_write_json(pair_db_path, pair_db)
    missing = set(pair_by_id) - set(pair_db["records"])
    extras = set(pair_db["records"]) - set(pair_by_id)
    if missing or extras:
        raise RuntimeError(f"v2 pair database does not reconcile: missing={len(missing)} extras={len(extras)}")
    candidates = read_json(output_dir / "production_top8_pair_candidates_v2.json")
    image_by_id = {
        row["image_id"]: row
        for row in read_json(output_dir / "attested_image_corpus_manifest.json")["records"]
    }
    image_id_by_candidate = {
        row.get("candidate_id"): row["image_id"]
        for row in image_by_id.values()
        if row.get("candidate_id")
    }
    evaluation = evaluate_material_veto_revision(
        project_dir, candidates, pair_db["records"], image_id_by_candidate, image_by_id,
    )
    atomic_write_json(output_dir / "v2_final_evaluation.json", evaluation)
    final_ledger = read_json(output_dir / "cost_ledger.json")
    source_ledger = read_json(source_dir / "cost_ledger.json")
    incremental = float(final_ledger["known_spend_usd"]) - float(source_ledger["known_spend_usd"])
    before = read_json(source_dir / "production_hashes_before.json")
    now = production_hashes(project_dir)
    unchanged = before["aggregate_sha256"] == now["aggregate_sha256"]
    if not unchanged:
        raise RuntimeError("production files changed during material-veto v2 research")
    status = {
        "schema_version": 2,
        "candidate_pair_count": len(pair_by_id),
        "judged_pair_count": len(pair_db["records"]),
        "evaluation_passed": evaluation["passed"],
        "gates": evaluation["gates"],
        "metrics": evaluation["metrics"],
        "incremental_known_spend_usd": round(incremental, 6),
        "total_known_spend_usd": final_ledger["known_spend_usd"],
        "ambiguous_exposure_usd": final_ledger["ambiguous_exposure_usd"],
        "production_files_unchanged": unchanged,
        "live_production_enabled": False,
        "generated_at": utc_now(),
    }
    atomic_write_json(output_dir / "v2_final_status.json", status)
    atomic_write_text(output_dir / "material_veto_v2_final_report.md", "\n".join([
        "# Relation-Aware Material Veto v2 Final Report", "",
        f"- Candidate pairs judged: {status['judged_pair_count']}/{status['candidate_pair_count']}",
        f"- Incremental known spend: US${status['incremental_known_spend_usd']:.4f}",
        f"- Total known spend: US${float(status['total_known_spend_usd']):.4f}",
        f"- Evaluation passed: {status['evaluation_passed']}",
        f"- Production files unchanged: {unchanged}", "",
        "## Gates", "",
        *[f"- {name}: {'PASS' if passed else 'FAIL'}" for name, passed in evaluation["gates"].items()], "",
        "## Metrics", "",
        *[f"- {name}: {value}" for name, value in evaluation["metrics"].items()], "",
        "The v2 veto remains offline. No production selection behaviour was activated.",
    ]) + "\n")
    return status


def finalise_material_veto_v2_offline(
    project_dir: Path, source_dir: Path, output_dir: Path,
) -> dict[str, Any]:
    """Apply audited local corrections without altering paid response artefacts."""
    candidates = read_json(output_dir / "production_top8_pair_candidates_v2.json")
    paid_judgements = read_json(output_dir / "pair_judgements_v2.json")
    attested = read_json(output_dir / "attested_image_corpus_manifest.json")
    packets = read_json(source_dir / "quote_corpus_manifest.json").get("records") or []
    if len(packets) != EXPECTED_QUOTES:
        raise RuntimeError("offline v2 finalisation requires all 627 canonical packets")
    corrected_images, relationship_audit = rebuild_source_grounded_relationship_assertions(
        attested["records"], packets,
    )
    semantically_changed_relationship_images = {
        row["image_id"] for row in relationship_audit["changes"]
        if [
            (tuple(value.get("participants") or []), value.get("relationship"))
            for value in row["before"]
        ] != [
            (tuple(value.get("participants") or []), value.get("relationship"))
            for value in row["after"]
        ]
    }
    image_by_id = {row["image_id"]: row for row in corrected_images}
    image_id_by_candidate = {
        row["candidate_id"]: row["image_id"] for row in corrected_images if row.get("candidate_id")
    }
    corrected_candidates = copy.deepcopy(candidates)
    pair_by_id: dict[str, dict[str, Any]] = {}
    changed_pair_inputs = set()
    for quote in corrected_candidates["records"]:
        for pair in quote["pairs"]:
            image = image_by_id[pair["image_id"]]
            before = pair.get("deterministic_contradictions") or []
            after = deterministic_material_contradictions(pair["contract"], image)
            if before != after or pair["image_id"] in {
                row["image_id"] for row in relationship_audit["changes"]
            }:
                changed_pair_inputs.add(pair["pair_id"])
            pair["deterministic_contradictions"] = after
            pair["postrun_corrected_input_sha256"] = value_hash({
                "prompt_version": V2_PAIR_PROMPT_VERSION,
                "contract": pair["contract"],
                "image": {key: image.get(key) for key in (
                    "image_id", "image_sha256", "identity_basis", "identity_confidence",
                    "named_people", "source_caption", "source_event", "source_date",
                    "relationship_assertions", "visual",
                )},
                "deterministic_contradictions": after,
            })
            pair_by_id[pair["pair_id"]] = pair
    if set(pair_by_id) != set(paid_judgements.get("records") or {}):
        raise RuntimeError("paid pair judgements do not cover the corrected candidate matrix")

    corrected_judgements = {}
    new_deterministic_vetoes = []
    preserved_after_metadata_change = []
    for pair_id, pair in sorted(pair_by_id.items()):
        image = image_by_id[pair["image_id"]]
        old = paid_judgements["records"][pair_id]
        if pair["deterministic_contradictions"]:
            judgement = local_deterministic_pair_veto(
                f"v2-postrun-local-{pair_id[:20]}", image, pair,
            )["response"][0]
            judgement["postrun_correction_basis"] = "corrected_deterministic_material_contradiction"
            if old.get("final_decision") != "veto" or (
                old.get("deterministic_contradictions") or []
            ) != pair["deterministic_contradictions"]:
                new_deterministic_vetoes.append({
                    "pair_id": pair_id,
                    "old_final_decision": old.get("final_decision"),
                    "old_deterministic_contradictions": old.get("deterministic_contradictions") or [],
                    "new_deterministic_contradictions": pair["deterministic_contradictions"],
                })
        else:
            judgement = copy.deepcopy(old)
            judgement["deterministic_contradictions"] = []
            if pair_id in changed_pair_inputs:
                if (
                    pair["image_id"] in semantically_changed_relationship_images
                    and pair["contract"].get("relationship_evidence_required")
                ):
                    raise RuntimeError(
                        "changed relationship evidence was reused for a relationship-dependent allow"
                    )
                judgement["postrun_correction_basis"] = (
                    "paid_judgement_preserved_relationship_metadata_immaterial_to_contract"
                )
                preserved_after_metadata_change.append(pair_id)
        judgement["postrun_corrected_input_sha256"] = pair["postrun_corrected_input_sha256"]
        corrected_judgements[pair_id] = judgement

    evaluation = evaluate_material_veto_revision(
        project_dir, corrected_candidates, corrected_judgements,
        image_id_by_candidate, image_by_id,
    )
    production_before = read_json(source_dir / "production_hashes_before.json")
    production_now = production_hashes(project_dir)
    production_unchanged = (
        production_before["aggregate_sha256"] == production_now["aggregate_sha256"]
    )
    if not production_unchanged:
        raise RuntimeError("production files changed during offline v2 finalisation")
    decision_counts = Counter(
        row.get("final_decision") for row in corrected_judgements.values()
    )
    current_winner_ids = [
        row["current_69_top8_pair_ids"][0] for row in corrected_candidates["records"]
    ]
    current_winner_veto_count = sum(
        corrected_judgements[pair_id]["final_decision"] == "veto"
        for pair_id in current_winner_ids
    )
    no_allowed_quote_ids = [
        quote["quote_id"] for quote in corrected_candidates["records"]
        if not any(
            corrected_judgements[pair["pair_id"]]["final_decision"] == "allow"
            for pair in quote["pairs"]
        )
    ]
    ledger = read_json(output_dir / "cost_ledger.json")
    status = {
        "schema_version": 1,
        "offline_correction_only": True,
        "paid_api_calls_made": False,
        "candidate_pair_count": len(pair_by_id),
        "judged_pair_count": len(corrected_judgements),
        "evaluation_passed": evaluation["passed"],
        "gates": evaluation["gates"],
        "metrics": evaluation["metrics"],
        "allow_count": decision_counts["allow"],
        "veto_count": decision_counts["veto"],
        "current_production_winner_allow_count": EXPECTED_QUOTES - current_winner_veto_count,
        "current_production_winner_veto_count": current_winner_veto_count,
        "quotes_without_allowed_candidate_count": len(no_allowed_quote_ids),
        "quotes_without_allowed_candidate_ids": no_allowed_quote_ids,
        "relationship_metadata_changed_image_count": relationship_audit["changed_image_count"],
        "changed_pair_input_count": len(changed_pair_inputs),
        "new_or_changed_deterministic_veto_count": len(new_deterministic_vetoes),
        "preserved_paid_judgement_after_immaterial_metadata_change_count": len(
            preserved_after_metadata_change
        ),
        "known_spend_usd": ledger["known_spend_usd"],
        "ambiguous_exposure_usd": ledger["ambiguous_exposure_usd"],
        "production_files_unchanged": production_unchanged,
        "live_production_enabled": False,
        "generated_at": utc_now(),
    }
    correction_audit = {
        "schema_version": 1,
        "paid_response_files_modified": False,
        "original_pair_judgement_sha256": sha256_file(output_dir / "pair_judgements_v2.json"),
        "original_candidate_manifest_sha256": sha256_file(
            output_dir / "production_top8_pair_candidates_v2.json"
        ),
        "relationship_grounding": relationship_audit,
        "changed_pair_input_count": len(changed_pair_inputs),
        "new_or_changed_deterministic_vetoes": new_deterministic_vetoes,
        "preserved_paid_judgement_pair_ids": preserved_after_metadata_change,
        "operator_positive_override": {
            "rule": "accepted pair is excluded from positive calibration when source-grounded image metadata names Ronald Reagan",
            "basis": "operator explicitly withdrew accepted Thatcher/Reagan pairings",
            "count": evaluation["metrics"]["operator_withdrawn_positive_count"],
        },
        "generic_reject_safety_policy": (
            "A generic reject without a contradiction note is not severe safety evidence."
        ),
        "generated_at": utc_now(),
    }
    atomic_write_json(output_dir / "attested_image_corpus_manifest_postrun_corrected.json", {
        **attested,
        "records": corrected_images,
        "relationship_policy_version": relationship_audit["policy_version"],
        "generated_at": utc_now(),
    })
    atomic_write_json(
        output_dir / "production_top8_pair_candidates_v2_postrun_corrected.json",
        corrected_candidates,
    )
    atomic_write_json(output_dir / "pair_judgements_v2_postrun_corrected.json", {
        **paid_judgements,
        "records": corrected_judgements,
        "postrun_offline_correction": True,
        "updated_at": utc_now(),
    })
    atomic_write_json(output_dir / "v2_postrun_correction_audit.json", correction_audit)
    atomic_write_json(output_dir / "v2_final_evaluation_postrun_corrected.json", evaluation)
    atomic_write_json(output_dir / "v2_final_status_postrun_corrected.json", status)
    atomic_write_json(output_dir / "v2_authoritative_evaluation_pointer.json", {
        "schema_version": 1,
        "authoritative_evaluation": "v2_final_evaluation_postrun_corrected.json",
        "authoritative_status": "v2_final_status_postrun_corrected.json",
        "original_paid_run_evaluation": "v2_final_evaluation.json",
        "original_paid_run_status": "v2_final_status.json",
        "correction_audit": "v2_postrun_correction_audit.json",
        "paid_response_files_modified": False,
        "live_production_enabled": False,
        "generated_at": utc_now(),
    })
    atomic_write_text(output_dir / "material_veto_v2_postrun_corrected_report.md", "\n".join([
        "# Relation-Aware Material Veto v2 Post-Run Correction", "",
        "This report applies deterministic offline corrections only. Paid raw and normalised responses remain unchanged.", "",
        f"- Evaluation passed: {status['evaluation_passed']}",
        f"- Pairs judged: {status['judged_pair_count']}/{status['candidate_pair_count']}",
        f"- Relationship metadata corrected for images: {status['relationship_metadata_changed_image_count']}",
        f"- New or changed deterministic vetoes: {status['new_or_changed_deterministic_veto_count']}",
        f"- Raw human approvals: {evaluation['metrics']['raw_positive_count']}",
        f"- Operator-withdrawn Reagan approvals: {evaluation['metrics']['operator_withdrawn_positive_count']}",
        f"- Eligible positive retention: {evaluation['metrics']['positive_retained_count']}/{evaluation['metrics']['positive_count']}",
        f"- Explicit safety vetoes: {evaluation['metrics']['safety_rejection_veto_count']}/{evaluation['metrics']['safety_rejection_count']}",
        f"- Quotes with an allowed candidate: {evaluation['metrics']['quotes_with_allowed_candidate']}/{EXPECTED_QUOTES}",
        f"- Quotes with no allowed candidate: {status['quotes_without_allowed_candidate_count']}",
        f"- Current production winners allowed: {status['current_production_winner_allow_count']}/{EXPECTED_QUOTES}",
        f"- Final pair decisions: {status['allow_count']} allow, {status['veto_count']} veto",
        f"- Known cumulative spend: US${float(status['known_spend_usd']):.4f}",
        f"- Production files unchanged: {production_unchanged}", "",
        "## Gates", "",
        *[f"- {name}: {'PASS' if passed else 'FAIL'}" for name, passed in evaluation["gates"].items()], "",
        "Passing these bounded gates supports a future shadow integration; it does not activate or prove the veto as a production control.",
        "The veto remains offline and is not enabled in production.",
    ]) + "\n")
    return status


def _resolve_quote_analysis_items(
    packets: Sequence[dict[str, Any]],
    quote_db: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Resolve legacy hash drift only through an exact immutable-text match."""
    source_items = quote_db.get("items") or {}
    by_text: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for source_id, item in source_items.items():
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            by_text[item["text"]].append((str(source_id), item))
    resolved: dict[str, dict[str, Any]] = {}
    aliases: dict[str, str] = {}
    for packet in packets:
        quote_id = str(packet["quote_id"])
        if quote_id in source_items:
            resolved[quote_id] = source_items[quote_id]
            continue
        matches = by_text.get(str(packet["quote_text"])) or []
        if not matches:
            raise RuntimeError(f"production quote analysis lacks canonical quote {quote_id}")
        analysis_hashes = {value_hash(item.get("analysis")) for _source_id, item in matches}
        if len(analysis_hashes) != 1:
            raise RuntimeError(f"conflicting exact-text quote analyses for canonical quote {quote_id}")
        source_id, item = min(matches, key=lambda row: row[0])
        resolved[quote_id] = item
        aliases[quote_id] = source_id
    return resolved, aliases


def pilot_pairs(output_dir: Path, contracts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the pilot pairs."""
    pilot = read_json(output_dir / "pilot_manifest.json")
    images = {
        row["image_id"]: row for row in read_json(output_dir / "image_corpus_manifest.json")["records"]
    }
    quote_ids = set(pilot["quote_ids"])
    rows = []
    for image_id in pilot["image_ids"]:
        pairs = []
        for quote_id in sorted(quote_ids):
            contract = contracts[quote_id]
            pairs.append({
                "pair_id": text_hash(f"{image_id}\n{quote_id}"),
                "image_id": image_id,
                "quote_id": quote_id,
                "contract": contract,
                "deterministic_contradictions": deterministic_contradictions(contract, images[image_id]),
            })
        rows.append({"image": images[image_id], "pairs": pairs})
    return rows


def pair_chunks(pairs: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Return the pair chunks."""
    return [list(pairs[offset:offset + PAIR_RECORDS_PER_REQUEST]) for offset in range(0, len(pairs), PAIR_RECORDS_PER_REQUEST)]


def _error_details(exc: BaseException) -> dict[str, Any]:
    response = getattr(exc, "response", None)
    status = getattr(exc, "code", None) or getattr(response, "status_code", None)
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    retry_after = None
    headers = getattr(response, "headers", {}) or {}
    try:
        retry_after = min(60.0, max(0.0, float(headers.get("Retry-After"))))
    except (TypeError, ValueError):
        pass
    return {
        "http_status": status,
        "retry_after": retry_after,
        "error_type": type(exc).__name__,
        "error": str(exc)[:2000],
        "is_429": status == 429,
        "ambiguous_outcome": status is None,
    }


def _response_truncated(result: dict[str, Any]) -> bool:
    for candidate in (result.get("raw") or {}).get("candidates") or []:
        reason = str(candidate.get("finishReason") or candidate.get("finish_reason") or "")
        if reason.upper().endswith("MAX_TOKENS"):
            return True
    return False


class CostLedger:
    """Persist and manage cost records."""
    def __init__(self, output_dir: Path):
        """Initialise the cost ledger."""
        self.path = output_dir / "cost_ledger.json"
        existing = read_json(self.path, {})
        self.value = existing or {
            "schema_version": SCHEMA_VERSION,
            "pricing_version": PRICING_VERSION,
            "known_spend_usd": 0.0,
            "known_spend_by_transport": {"developer_api": 0.0, "vertex_ai": 0.0},
            "known_spend_by_class": {"planned": 0.0, "repair": 0.0},
            "ambiguous_exposure_usd": 0.0,
            "pending_reservations": {},
            "planned_execution_ceiling_usd": PLANNED_EXECUTION_CEILING_USD,
            "repair_reserve_usd": REPAIR_RESERVE_USD,
            "hard_combined_ceiling_usd": HARD_COMBINED_CEILING_USD,
            "operations": [],
        }
        self._save()

    def _save(self) -> None:
        self.value["updated_at"] = utc_now()
        atomic_write_json(self.path, self.value)

    def reserve(self, operation_id: str, maximum_usd: float, transport: str, *, repair: bool) -> None:
        """Perform the reserve operation."""
        if operation_id in self.value["pending_reservations"]:
            raise RuntimeError(f"cost reservation already pending: {operation_id}")
        pending = sum(float(row["maximum_usd"]) for row in self.value["pending_reservations"].values())
        projected = (
            float(self.value["known_spend_usd"])
            + float(self.value["ambiguous_exposure_usd"])
            + pending
            + maximum_usd
        )
        hard_ceiling = float(self.value.get("hard_combined_ceiling_usd", HARD_COMBINED_CEILING_USD))
        planned_ceiling = float(self.value.get("planned_execution_ceiling_usd", PLANNED_EXECUTION_CEILING_USD))
        repair_ceiling = float(self.value.get("repair_reserve_usd", REPAIR_RESERVE_USD))
        if projected > hard_ceiling + 1e-9:
            raise RuntimeError(f"absolute US${hard_ceiling:g} cost ceiling would be exceeded")
        if not repair:
            planned = float(self.value["known_spend_by_class"].get("planned", 0)) + pending + maximum_usd
            if planned > planned_ceiling + 1e-9:
                raise RuntimeError(f"planned US${planned_ceiling:g} execution ceiling would be exceeded")
        else:
            repair_spend = float(self.value["known_spend_by_class"].get("repair", 0)) + maximum_usd
            if repair_spend > repair_ceiling + 1e-9:
                raise RuntimeError(f"protected US${repair_ceiling:g} repair reserve would be exceeded")
        self.value["pending_reservations"][operation_id] = {
            "maximum_usd": round(maximum_usd, 10), "transport": transport,
            "class": "repair" if repair else "planned", "reserved_at": utc_now(),
        }
        self.value["operations"].append({
            "operation_id": operation_id, "event": "reserved", "maximum_usd": round(maximum_usd, 10),
            "transport": transport, "class": "repair" if repair else "planned", "timestamp": utc_now(),
        })
        self._save()
    def release(self, operation_id: str, *, reason: str) -> None:
        """Perform the release operation."""
        row = self.value["pending_reservations"].pop(operation_id, None)
        if row:
            self.value["operations"].append({
                "operation_id": operation_id, "event": "released", "reason": reason,
                "maximum_usd": row["maximum_usd"], "timestamp": utc_now(),
            })
            self._save()

    def complete(self, operation_id: str, actual_usd: float) -> None:
        """Perform the complete operation."""
        row = self.value["pending_reservations"].pop(operation_id)
        transport = row["transport"]
        spending_class = row["class"]
        self.value["known_spend_usd"] = round(float(self.value["known_spend_usd"]) + actual_usd, 10)
        self.value["known_spend_by_transport"][transport] = round(
            float(self.value["known_spend_by_transport"].get(transport, 0)) + actual_usd, 10,
        )
        self.value["known_spend_by_class"][spending_class] = round(
            float(self.value["known_spend_by_class"].get(spending_class, 0)) + actual_usd, 10,
        )
        self.value["operations"].append({
            "operation_id": operation_id, "event": "completed", "actual_usd": round(actual_usd, 10),
            "transport": transport, "class": spending_class, "timestamp": utc_now(),
        })
        self._save()

    def completed_cost(self, operation_id: str) -> float | None:
        """Return the completed cost."""
        for row in reversed(self.value["operations"]):
            if row.get("operation_id") == operation_id and row.get("event") == "completed":
                return float(row["actual_usd"])
        return None

    def mark_ambiguous(self, operation_id: str, *, reason: str) -> None:
        """Mark ambiguous."""
        row = self.value["pending_reservations"].pop(operation_id)
        self.value["ambiguous_exposure_usd"] = round(
            float(self.value["ambiguous_exposure_usd"]) + float(row["maximum_usd"]), 10,
        )
        self.value["operations"].append({
            "operation_id": operation_id, "event": "ambiguous", "reason": reason,
            "maximum_usd": row["maximum_usd"], "transport": row["transport"], "timestamp": utc_now(),
        })
        self._save()


def initialise_v2_cost_ledger(source_dir: Path, output_dir: Path) -> CostLedger:
    """Seed v2 with the complete v1 spend history and the authorised US$40 increase."""
    source_path = source_dir / "cost_ledger.json"
    target_path = output_dir / "cost_ledger.json"
    source = read_json(source_path)
    if source.get("pending_reservations"):
        raise RuntimeError("v1 cost ledger has pending reservations")
    source_hash = sha256_file(source_path)
    if target_path.exists():
        existing = read_json(target_path)
        authorisation = existing.get("v2_budget_authorisation") or {}
        if authorisation.get("source_v1_ledger_sha256") != source_hash:
            raise RuntimeError("v2 cost ledger is not linked to the current immutable v1 ledger")
        if float(existing.get("hard_combined_ceiling_usd") or 0) != V2_HARD_COMBINED_CEILING_USD:
            raise RuntimeError("v2 hard ceiling differs from the authorised US$100")
        return CostLedger(output_dir)
    value = copy.deepcopy(source)
    value["planned_execution_ceiling_usd"] = V2_PLANNED_EXECUTION_CEILING_USD
    value["repair_reserve_usd"] = REPAIR_RESERVE_USD
    value["hard_combined_ceiling_usd"] = V2_HARD_COMBINED_CEILING_USD
    value["pending_reservations"] = {}
    value["v2_budget_authorisation"] = {
        "source_v1_ledger_sha256": source_hash,
        "previous_hard_ceiling_usd": HARD_COMBINED_CEILING_USD,
        "increase_usd": 40.0,
        "new_hard_ceiling_usd": V2_HARD_COMBINED_CEILING_USD,
        "authorised_at": utc_now(),
    }
    value.setdefault("operations", []).append({
        "event": "project_budget_increased",
        "increase_usd": 40.0,
        "previous_hard_ceiling_usd": HARD_COMBINED_CEILING_USD,
        "new_hard_ceiling_usd": V2_HARD_COMBINED_CEILING_USD,
        "timestamp": utc_now(),
    })
    atomic_write_json(target_path, value)
    return CostLedger(output_dir)


class GeminiRelationClient:
    """Provide the gemini relation client."""
    def __init__(
        self,
        transport: str,
        *,
        api_key: str | None = None,
        project: str | None = None,
        location: str = "global",
        client: Any | None = None,
        timeout_seconds: float = 600,
    ):
        """Initialise the gemini relation client."""
        if transport not in {"developer_api", "vertex_ai"}:
            raise ValueError("invalid Gemini transport")
        self.transport = transport
        if client is not None:
            self.client = client
        elif transport == "developer_api":
            if not api_key:
                raise RuntimeError("Gemini Developer API key is required")
            self.client = genai.Client(vertexai=False, api_key=api_key)
        else:
            if not project:
                raise RuntimeError("GOOGLE_CLOUD_PROJECT is required for Vertex")
            self.client = genai.Client(vertexai=True, project=project, location=location)
        self.timeout_seconds = timeout_seconds

    def config(self, schema: dict[str, Any], max_output_tokens: int, *, batch: bool = False) -> types.GenerateContentConfig:
        """Return the config."""
        typed_schema = None
        json_schema = schema
        if batch:
            typed_schema = types.Schema.from_json_schema(
                json_schema=types.JSONSchema.model_validate(schema),
                api_option="VERTEX_AI" if self.transport == "vertex_ai" else "GEMINI_API",
                raise_error_on_unsupported_field=True,
            )
            json_schema = None
        return types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=typed_schema,
            response_json_schema=json_schema,
            max_output_tokens=max_output_tokens,
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.HIGH),
            temperature=TEMPERATURE,
            http_options=None if batch else types.HttpOptions(timeout=int(self.timeout_seconds * 1000)),
        )

    def settings_signature(self, schema: dict[str, Any], max_output_tokens: int) -> dict[str, Any]:
        """Return the settings signature."""
        config = self.config(schema, max_output_tokens)
        return {
            "model": MODEL,
            "response_mime_type": config.response_mime_type,
            "response_json_schema": config.response_json_schema,
            "max_output_tokens": config.max_output_tokens,
            "thinking_level": str(config.thinking_config.thinking_level),
            "temperature": config.temperature,
            "tools": config.tools,
        }

    def model_available(self) -> dict[str, Any]:
        """Return whether model available."""
        model = self.client.models.get(model=MODEL)
        name = str(getattr(model, "name", "") or "")
        if MODEL not in name:
            raise RuntimeError(f"exact pinned model unavailable on {self.transport}: {name}")
        return {"transport": self.transport, "requested_model": MODEL, "returned_name": name}

    def call(self, prompt: str, schema: dict[str, Any], max_output_tokens: int) -> dict[str, Any]:
        """Submit one typed relation-aware prompt to Gemini."""
        started = time.monotonic()
        response = self.client.models.generate_content(
            model=MODEL,
            contents=getattr(prompt, "gemini_contents", prompt),
            config=self.config(schema, max_output_tokens),
        )
        elapsed = time.monotonic() - started
        raw = response.model_dump(mode="json", exclude_none=True)
        parsed = response.parsed if isinstance(response.parsed, dict) else None
        repairs: list[str] = []
        parse_error = None
        if parsed is None:
            try:
                parsed, repairs = parse_json_response(raw)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                parse_error = f"{type(exc).__name__}: {exc}"
        usage = _usage(raw)
        return {
            "raw": raw, "parsed": parsed, "parse_error": parse_error,
            "normalisation": repairs, "usage": usage,
            "cost_usd": calculate_cost(usage, batch=False), "latency_seconds": elapsed,
            "request_id": str(getattr(response, "response_id", "") or "") or None,
            "model_version": str(getattr(response, "model_version", "") or "") or None,
        }


def require_transport_parity(
    developer: GeminiRelationClient,
    vertex: GeminiRelationClient,
    schema: dict[str, Any],
    max_output_tokens: int,
) -> None:
    """Require transport parity."""
    if developer.settings_signature(schema, max_output_tokens) != vertex.settings_signature(schema, max_output_tokens):
        raise RuntimeError("Developer and Vertex semantic settings differ")


class RelationRouter:
    """Sequential, resumable Developer-to-Vertex routing for interactive requests."""

    def __init__(
        self,
        output_dir: Path,
        developer: GeminiRelationClient,
        vertex: GeminiRelationClient | None,
        ledger: CostLedger,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ):
        """Initialise the relation router."""
        self.output_dir = output_dir
        self.developer = developer
        self.vertex = vertex
        self.ledger = ledger
        self.sleep = sleep
        self.state_path = output_dir / "provider_route_state.json"
        self.state = read_json(self.state_path, {}) or {
            "schema_version": SCHEMA_VERSION,
            "developer_paused": False,
            "pause_reason": None,
            "pause_timestamp": None,
            "consecutive_developer_429s": 0,
            "developer_attempts": 0,
            "vertex_attempts": 0,
            "direct_to_vertex_count": 0,
        }
        self._save_state()

    def _save_state(self) -> None:
        self.state["updated_at"] = utc_now()
        atomic_write_json(self.state_path, self.state)

    def _attempt(
        self,
        *,
        client: GeminiRelationClient,
        logical_id: str,
        prompt: str,
        schema: dict[str, Any],
        max_output_tokens: int,
        transport_attempt: int,
        repair: bool,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        transport = client.transport
        raw_dir = self.output_dir / "raw_responses"
        lifecycle_attempt = len(list(raw_dir.glob(f"{logical_id}--{transport}--*.json"))) + 1
        attempt_id = f"{logical_id}:{transport}:{lifecycle_attempt}"
        maximum = maximum_cost(prompt, max_output_tokens, batch=False)
        self.ledger.reserve(attempt_id, maximum, transport, repair=repair)
        started = utc_now()
        append_jsonl(self.output_dir / "attempts.jsonl", {
            "logical_call_id": logical_id, "attempt_id": attempt_id,
            "provider": transport, "model": MODEL, "attempt": lifecycle_attempt,
            "transport_attempt_in_invocation": transport_attempt,
            "prompt_hash": text_hash(prompt), "status": "sending", "started_at": started,
            "maximum_reserved_cost_usd": maximum, "repair": repair,
        })
        try:
            result = client.call(prompt, schema, max_output_tokens)
        except BaseException as exc:
            details = _error_details(exc)
            if details["ambiguous_outcome"]:
                self.ledger.mark_ambiguous(attempt_id, reason=details["error"])
            else:
                self.ledger.release(attempt_id, reason=f"http_{details['http_status']}")
            append_jsonl(self.output_dir / "attempts.jsonl", {
                "logical_call_id": logical_id, "attempt_id": attempt_id,
                "provider": transport, "model": MODEL, "attempt": lifecycle_attempt,
                "transport_attempt_in_invocation": transport_attempt,
                "prompt_hash": text_hash(prompt), "status": "failed",
                "started_at": started, "completed_at": utc_now(), **details,
            })
            return None, details
        self.ledger.complete(attempt_id, float(result["cost_usd"]))
        raw_path = raw_dir / f"{logical_id}--{transport}--{lifecycle_attempt}.json"
        atomic_write_json(raw_path, {
            "logical_call_id": logical_id, "transport": transport, "model": MODEL,
            "prompt_hash": text_hash(prompt), "response": result["raw"],
            "usage": result["usage"], "cost_usd": result["cost_usd"],
            "request_id": result["request_id"], "model_version": result["model_version"],
            "received_at": utc_now(),
        })
        append_jsonl(self.output_dir / "attempts.jsonl", {
            "logical_call_id": logical_id, "attempt_id": attempt_id,
            "provider": transport, "model": MODEL, "attempt": lifecycle_attempt,
            "transport_attempt_in_invocation": transport_attempt,
            "prompt_hash": text_hash(prompt), "response_hash": value_hash(result["raw"]),
            "status": "completed", "http_status": 200, "started_at": started,
            "completed_at": utc_now(), "usage": result["usage"],
            "known_cost_usd": result["cost_usd"], "request_id": result["request_id"],
            "raw_response_file": str(raw_path.relative_to(self.output_dir)), "repair": repair,
        })
        return result, None

    def call(
        self,
        *,
        logical_id: str,
        prompt: str,
        schema: dict[str, Any],
        max_output_tokens: int,
        validator: Callable[[Any], Any],
        allow_repair: bool = True,
        force_transport: str | None = None,
        initial_attempt_is_repair: bool = False,
    ) -> dict[str, Any]:
        """Route one logical request with persisted retry and failover state."""
        result_path = self.output_dir / "normalised_responses" / f"{logical_id}.json"
        existing = read_json(result_path, None)
        if isinstance(existing, dict) and existing.get("status") == "completed":
            return existing
        prompt_hash = text_hash(prompt)
        raw_pattern = f"{logical_id}--*--*.json"
        for raw_path in sorted(
            (self.output_dir / "raw_responses").glob(raw_pattern),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        ):
            saved = read_json(raw_path, None)
            if not isinstance(saved, dict) or saved.get("prompt_hash") != prompt_hash:
                continue
            try:
                parsed, repairs = parse_json_response(saved.get("response") or {})
                validated = validator(parsed)
            except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                continue
            value = {
                "schema_version": SCHEMA_VERSION,
                "logical_call_id": logical_id,
                "status": "completed",
                "transport": saved.get("transport"),
                "prompt_hash": prompt_hash,
                "response": validated,
                "normalisation": [*repairs, "recovered_from_saved_response"],
                "usage": saved.get("usage") or {},
                "cost_usd": float(saved.get("cost_usd") or 0.0),
                "request_id": saved.get("request_id"),
                "model_version": saved.get("model_version") or saved.get("model"),
                "completed_at": saved.get("received_at") or utc_now(),
                "recovered_from_saved_response": str(raw_path.relative_to(self.output_dir)),
            }
            atomic_write_json(result_path, value)
            append_jsonl(self.output_dir / "attempts.jsonl", {
                "logical_call_id": logical_id,
                "provider": saved.get("transport"),
                "model": MODEL,
                "prompt_hash": prompt_hash,
                "response_hash": value_hash(saved.get("response") or {}),
                "status": "recovered_from_saved_response",
                "raw_response_file": str(raw_path.relative_to(self.output_dir)),
                "completed_at": utc_now(),
            })
            return value
        if force_transport not in {None, "developer_api", "vertex_ai"}:
            raise ValueError("invalid forced transport")
        if force_transport == "vertex_ai" or (force_transport is None and self.state["developer_paused"]):
            if not self.vertex:
                raise RuntimeError("Vertex is required but unavailable")
            clients = [self.vertex, self.vertex]
            self.state["direct_to_vertex_count"] += 1
            self._save_state()
        elif force_transport == "developer_api":
            clients = [self.developer, self.developer]
        else:
            clients = [self.developer, self.developer]
        last_details = None
        response = None
        used_transport = None
        transport_attempts: dict[str, int] = defaultdict(int)
        index = 0
        while index < len(clients):
            client = clients[index]
            transport_attempts[client.transport] += 1
            if client.transport == "developer_api":
                self.state["developer_attempts"] += 1
            else:
                self.state["vertex_attempts"] += 1
            self._save_state()
            response, details = self._attempt(
                client=client, logical_id=logical_id, prompt=prompt, schema=schema,
                max_output_tokens=max_output_tokens,
                transport_attempt=transport_attempts[client.transport], repair=initial_attempt_is_repair,
            )
            if response is not None:
                used_transport = client.transport
                if client.transport == "developer_api":
                    self.state["consecutive_developer_429s"] = 0
                    self._save_state()
                break
            last_details = details
            if details and details["ambiguous_outcome"]:
                raise RuntimeError(f"ambiguous {client.transport} outcome blocks automatic retry: {logical_id}")
            if not details or not details["is_429"]:
                if client.transport == "developer_api":
                    self.state["consecutive_developer_429s"] = 0
                    self._save_state()
                raise RuntimeError(f"ineligible transport failure for {logical_id}: {details}")
            if client.transport == "developer_api":
                self.state["consecutive_developer_429s"] += 1
                self._save_state()
                if self.state["consecutive_developer_429s"] == 1:
                    self.sleep(details["retry_after"] if details["retry_after"] is not None else 2.0)
                    index += 1
                    continue
                self.state.update({
                    "developer_paused": True,
                    "pause_reason": "two_consecutive_confirmed_developer_429s",
                    "pause_timestamp": utc_now(),
                })
                self._save_state()
                if not self.vertex:
                    raise RuntimeError("Developer paused after two 429s and Vertex is unavailable")
                clients = [self.vertex, self.vertex]
                transport_attempts.clear()
                index = 0
                continue
            if transport_attempts["vertex_ai"] == 1:
                self.sleep(details["retry_after"] if details["retry_after"] is not None else 2.0)
                index += 1
                continue
            raise RuntimeError("Vertex returned two confirmed 429s; run stopped resumably")
        if response is None or used_transport is None:
            raise RuntimeError(f"no response for {logical_id}: {last_details}")
        try:
            validated = validator(response.get("parsed"))
            validation_error = None
        except (TypeError, ValueError, KeyError) as exc:
            validated = None
            validation_error = f"{type(exc).__name__}: {exc}"
        if validated is None and _response_truncated(response):
            raise RuntimeError(
                f"response hit MAX_TOKENS for {logical_id}; same-size repair suppressed"
            )
        if validated is None and allow_repair:
            repair_prompt = prompt + (
                "\n\nREPAIR INSTRUCTION: The saved response failed deterministic local JSON/schema validation. "
                "Re-evaluate the original input and return one complete object conforming exactly to the supplied schema."
            )
            repair_id = f"{logical_id}-repair"
            client = self.developer if used_transport == "developer_api" else self.vertex
            if client is None:
                raise RuntimeError("repair transport unavailable")
            repair_response, repair_details = self._attempt(
                client=client, logical_id=repair_id, prompt=repair_prompt, schema=schema,
                max_output_tokens=max_output_tokens, transport_attempt=1, repair=True,
            )
            if repair_response is None:
                raise RuntimeError(f"repair failed without transport failover: {repair_details}")
            try:
                validated = validator(repair_response.get("parsed"))
            except (TypeError, ValueError, KeyError) as exc:
                raise RuntimeError(f"repair response remains invalid: {exc}") from exc
            response = repair_response
            used_transport = client.transport
        elif validated is None:
            raise RuntimeError(f"saved response is invalid and repair is disabled: {validation_error}")
        value = {
            "schema_version": SCHEMA_VERSION, "logical_call_id": logical_id,
            "status": "completed", "transport": used_transport,
            "prompt_hash": text_hash(prompt), "response": validated,
            "normalisation": response.get("normalisation") or [],
            "usage": response["usage"], "cost_usd": response["cost_usd"],
            "request_id": response["request_id"], "model_version": response["model_version"],
            "completed_at": utc_now(),
        }
        atomic_write_json(result_path, value)
        return value


class DeveloperBatchRunner:
    """Durable Developer Batch execution with interactive Vertex fallback."""

    TERMINAL_SUCCESS = {"JOB_STATE_SUCCEEDED", "JOB_STATE_PARTIALLY_SUCCEEDED"}
    TERMINAL_FAILURE = {"JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"}

    def __init__(
        self,
        output_dir: Path,
        router: RelationRouter,
        ledger: CostLedger,
        *,
        sleep: Callable[[float], None] = time.sleep,
        poll_seconds: float = 30.0,
    ):
        """Initialise the developer batch runner."""
        self.output_dir = output_dir
        self.router = router
        self.ledger = ledger
        self.sleep = sleep
        self.poll_seconds = poll_seconds
        self.path = output_dir / "batch_jobs.json"
        self.jobs = read_json(self.path, {}) or {"schema_version": SCHEMA_VERSION, "jobs": {}}
        self._save()

    def _save(self) -> None:
        self.jobs["updated_at"] = utc_now()
        atomic_write_json(self.path, self.jobs)

    @staticmethod
    def _state(job: Any) -> str:
        state = getattr(job, "state", None)
        return str(getattr(state, "value", state) or "")

    def _interactive_fallback(
        self,
        batch_id: str,
        items: Sequence[dict[str, Any]],
        validators: dict[str, Callable[[Any], Any]],
    ) -> dict[str, Any]:
        results = {}
        for item in items:
            logical_id = item["logical_id"]
            results[logical_id] = self.router.call(
                logical_id=logical_id,
                prompt=item["prompt"],
                schema=item["schema"],
                max_output_tokens=item["max_output_tokens"],
                validator=validators[logical_id],
                force_transport="vertex_ai" if self.router.state["developer_paused"] else None,
            )
        self.jobs["jobs"][batch_id] = {
            "status": "completed_via_interactive_fallback",
            "logical_ids": sorted(results), "completed_at": utc_now(),
        }
        self._save()
        return results

    def run(
        self,
        *,
        batch_id: str,
        items: Sequence[dict[str, Any]],
        validators: dict[str, Callable[[Any], Any]],
    ) -> dict[str, Any]:
        """Run one resumable batch and validate every logical item."""
        existing_results = {}
        pending = []
        for item in items:
            path = self.output_dir / "normalised_responses" / f"{item['logical_id']}.json"
            value = read_json(path, None)
            if isinstance(value, dict) and value.get("status") == "completed":
                existing_results[item["logical_id"]] = value
            else:
                pending.append(item)
        if not pending:
            state = self.jobs["jobs"].get(batch_id)
            if state and state.get("completed_item_count") != len(existing_results):
                state["completed_item_count"] = len(existing_results)
                self._save()
            return existing_results
        state = self.jobs["jobs"].get(batch_id)
        if state and state.get("status") == "prepared_without_provider_job_id":
            raise RuntimeError(f"batch {batch_id} has an ambiguous submission state")
        if self.router.state["developer_paused"]:
            return {**existing_results, **self._interactive_fallback(batch_id, pending, validators)}
        operation_id = f"batch:{batch_id}"
        maximum = sum(
            maximum_cost(item["prompt"], item["max_output_tokens"], batch=True)
            for item in pending
        )
        if not state:
            self.ledger.reserve(operation_id, maximum, "developer_api", repair=False)
            self.jobs["jobs"][batch_id] = {
                "status": "prepared_without_provider_job_id",
                "logical_ids": [item["logical_id"] for item in pending],
                "item_count": len(pending), "maximum_reserved_cost_usd": round(maximum, 10),
                "prepared_at": utc_now(),
            }
            self._save()
            requests_list = [
                types.InlinedRequest(
                    model=MODEL,
                    contents=getattr(item["prompt"], "gemini_contents", item["prompt"]),
                    metadata={"logical_call_id": item["logical_id"]},
                    config=self.router.developer.config(
                        item["schema"], item["max_output_tokens"], batch=True,
                    ),
                )
                for item in pending
            ]
            create_attempt = 0
            while True:
                create_attempt += 1
                append_jsonl(self.output_dir / "attempts.jsonl", {
                    "logical_call_id": batch_id, "attempt_id": f"{operation_id}:submission:{create_attempt}",
                    "provider": "developer_api", "model": MODEL, "attempt": create_attempt,
                    "status": "batch_submission_sending", "item_count": len(pending),
                    "started_at": utc_now(),
                })
                try:
                    job = self.router.developer.client.batches.create(
                        model=MODEL,
                        src=requests_list,
                        config=types.CreateBatchJobConfig(
                            display_name=batch_id,
                            http_options=types.HttpOptions(timeout=120_000),
                        ),
                    )
                except BaseException as exc:
                    details = _error_details(exc)
                    append_jsonl(self.output_dir / "attempts.jsonl", {
                        "logical_call_id": batch_id,
                        "attempt_id": f"{operation_id}:submission:{create_attempt}",
                        "provider": "developer_api", "model": MODEL, "attempt": create_attempt,
                        "status": "batch_submission_failed", "completed_at": utc_now(), **details,
                    })
                    if details["ambiguous_outcome"]:
                        self.ledger.mark_ambiguous(operation_id, reason=details["error"])
                        raise RuntimeError("ambiguous batch submission blocks resubmission") from exc
                    if not details["is_429"]:
                        self.ledger.release(operation_id, reason=f"batch_http_{details['http_status']}")
                        raise RuntimeError("non-429 batch failure is ineligible for Vertex failover") from exc
                    self.router.state["consecutive_developer_429s"] += 1
                    self.router._save_state()
                    if create_attempt == 1:
                        self.sleep(details["retry_after"] if details["retry_after"] is not None else 2.0)
                        continue
                    self.ledger.release(operation_id, reason="two_batch_submission_429s")
                    self.router.state.update({
                        "developer_paused": True,
                        "pause_reason": "two_consecutive_confirmed_developer_batch_429s",
                        "pause_timestamp": utc_now(),
                    })
                    self.router._save_state()
                    self.jobs["jobs"].pop(batch_id, None)
                    self._save()
                    return {**existing_results, **self._interactive_fallback(batch_id, pending, validators)}
                self.router.state["consecutive_developer_429s"] = 0
                self.router._save_state()
                job_name = str(getattr(job, "name", "") or "")
                if not job_name:
                    self.ledger.mark_ambiguous(operation_id, reason="batch creation returned no job ID")
                    raise RuntimeError("batch submission returned no durable job ID")
                self.jobs["jobs"][batch_id].update({
                    "status": "submitted", "provider_job_id": job_name,
                    "submitted_at": utc_now(), "submission_attempts": create_attempt,
                })
                self._save()
                state = self.jobs["jobs"][batch_id]
                break
        job_name = state.get("provider_job_id")
        if not job_name:
            raise RuntimeError(f"batch {batch_id} has no provider job ID")
        while True:
            job = self.router.developer.client.batches.get(
                name=job_name,
                config=types.GetBatchJobConfig(http_options=types.HttpOptions(timeout=120_000)),
            )
            job_state = self._state(job)
            state["provider_state"] = job_state
            state["last_polled_at"] = utc_now()
            self._save()
            if job_state in self.TERMINAL_SUCCESS:
                break
            if job_state in self.TERMINAL_FAILURE:
                self.ledger.mark_ambiguous(operation_id, reason=f"terminal batch state {job_state}")
                raise RuntimeError(f"batch {batch_id} ended in {job_state}")
            self.sleep(self.poll_seconds)
        responses = list(getattr(getattr(job, "dest", None), "inlined_responses", None) or [])
        by_id = {
            str((getattr(row, "metadata", None) or {}).get("logical_call_id") or ""): row
            for row in responses
        }
        actual_cost = 0.0
        batch_results: dict[str, Any] = {}
        repair_items = []
        for item in pending:
            logical_id = item["logical_id"]
            row = by_id.get(logical_id)
            if row is None or getattr(row, "error", None) is not None or getattr(row, "response", None) is None:
                error = getattr(row, "error", None) if row is not None else None
                error_code = int(getattr(error, "code", 0) or 0)
                actual_cost += maximum_cost(item["prompt"], item["max_output_tokens"], batch=True)
                append_jsonl(self.output_dir / "attempts.jsonl", {
                    "logical_call_id": logical_id, "provider": "developer_api", "model": MODEL,
                    "status": "batch_item_failed", "http_status": error_code or None,
                    "error": str(getattr(error, "message", error) or "missing batch response")[:2000],
                    "completed_at": utc_now(),
                })
                if error_code == 429:
                    repair_items.append((item, False, False))
                    continue
                raise RuntimeError(f"non-429 batch item failure: {logical_id}")
            response = row.response
            raw = response.model_dump(mode="json", exclude_none=True)
            usage = _usage(raw)
            item_cost = calculate_cost(usage, batch=True)
            actual_cost += item_cost
            raw_path = (
                self.output_dir / "raw_responses"
                / f"{logical_id}--{batch_id}--developer_batch.json"
            )
            atomic_write_json(raw_path, {
                "logical_call_id": logical_id, "transport": "developer_api_batch", "model": MODEL,
                "batch_id": batch_id, "provider_job_id": job_name,
                "prompt_hash": text_hash(item["prompt"]), "response": raw,
                "usage": usage, "cost_usd": item_cost, "received_at": utc_now(),
            })
            parsed = response.parsed if isinstance(response.parsed, dict) else None
            if parsed is None:
                try:
                    parsed, _repairs = parse_json_response(raw)
                except (TypeError, ValueError, json.JSONDecodeError):
                    parsed = None
            truncated = _response_truncated({"raw": raw})
            try:
                validated = validators[logical_id](parsed)
            except (TypeError, ValueError, KeyError):
                repair_items.append((item, True, truncated))
                continue
            value = {
                "schema_version": SCHEMA_VERSION, "logical_call_id": logical_id,
                "status": "completed", "transport": "developer_api_batch",
                "prompt_hash": text_hash(item["prompt"]), "response": validated,
                "usage": usage, "cost_usd": item_cost, "provider_job_id": job_name,
                "completed_at": utc_now(),
            }
            atomic_write_json(self.output_dir / "normalised_responses" / f"{logical_id}.json", value)
            batch_results[logical_id] = value
        recorded_batch_cost = self.ledger.completed_cost(operation_id)
        if recorded_batch_cost is None:
            self.ledger.complete(operation_id, actual_cost)
            recorded_batch_cost = actual_cost
        for item, malformed, truncated in repair_items:
            logical_id = item["logical_id"]
            if truncated and item.get("recovery_splits"):
                split_results = []
                for split in item["recovery_splits"]:
                    if split["pair"].get("deterministic_contradictions"):
                        result = local_deterministic_pair_veto(
                            split["logical_id"], item["image"], split["pair"],
                        )
                        atomic_write_json(
                            self.output_dir / "normalised_responses" / f"{split['logical_id']}.json",
                            result,
                        )
                        append_jsonl(self.output_dir / "attempts.jsonl", {
                            "logical_call_id": split["logical_id"],
                            "provider": "local_deterministic_rules",
                            "status": "completed_without_provider_call",
                            "reason": "deterministic contradiction controls fail-closed recovery",
                            "completed_at": utc_now(),
                        })
                    else:
                        result = self.router.call(
                            logical_id=split["logical_id"],
                            prompt=split["prompt"],
                            schema=split["schema"],
                            max_output_tokens=split["max_output_tokens"],
                            validator=split["validator"],
                            force_transport=None,
                            initial_attempt_is_repair=True,
                            allow_repair=False,
                        )
                    split_results.append(result)
                merged = merge_split_pair_results(
                    split_results, item["expected_pair_ids"],
                )
                usage = {
                    key: sum(int((result.get("usage") or {}).get(key, 0) or 0) for result in split_results)
                    for key in ("input_tokens", "cached_tokens", "output_tokens", "thinking_tokens")
                }
                result = {
                    "schema_version": SCHEMA_VERSION,
                    "logical_call_id": logical_id,
                    "status": "completed",
                    "transport": "interactive_split_recovery",
                    "prompt_hash": text_hash(item["prompt"]),
                    "response": merged,
                    "normalisation": ["split_after_batch_max_tokens"],
                    "usage": usage,
                    "cost_usd": round(sum(float(row.get("cost_usd") or 0) for row in split_results), 10),
                    "split_logical_call_ids": [row["logical_call_id"] for row in split_results],
                    "completed_at": utc_now(),
                }
                atomic_write_json(
                    self.output_dir / "normalised_responses" / f"{logical_id}.json",
                    result,
                )
                batch_results[logical_id] = result
                continue
            repair_id = logical_id if not malformed else f"{logical_id}-batch-repair"
            result = self.router.call(
                logical_id=repair_id,
                prompt=item["prompt"] + ("\n\nReturn one complete schema-valid JSON object." if malformed else ""),
                schema=item["schema"], max_output_tokens=item["max_output_tokens"],
                validator=validators[logical_id],
                force_transport=None,
                initial_attempt_is_repair=malformed,
                allow_repair=False,
            )
            if repair_id != logical_id:
                result = {**result, "logical_call_id": logical_id, "recovered_by": repair_id}
                atomic_write_json(self.output_dir / "normalised_responses" / f"{logical_id}.json", result)
            batch_results[logical_id] = result
        state.update({
            "status": "completed", "provider_state": self._state(job),
            "completed_at": utc_now(), "known_cost_usd": round(recorded_batch_cost, 10),
            "completed_item_count": len(existing_results) + len(batch_results),
        })
        self._save()
        return {**existing_results, **batch_results}


def _clients(project_dir: Path, output_dir: Path) -> tuple[GeminiRelationClient, GeminiRelationClient | None]:
    load_project_environment(project_dir / "mrsMThatcher.env")
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    location = os.getenv("GOOGLE_CLOUD_LOCATION") or "global"
    developer = GeminiRelationClient("developer_api", api_key=api_key)
    vertex = None
    vertex_error = None
    if project:
        try:
            vertex = GeminiRelationClient("vertex_ai", project=project, location=location)
        except Exception as exc:
            vertex_error = f"{type(exc).__name__}: {exc}"
    atomic_write_json(output_dir / "transport_configuration.json", {
        "schema_version": SCHEMA_VERSION,
        "developer_configured": bool(api_key),
        "vertex_project_configured": bool(project),
        "vertex_location": location,
        "vertex_client_constructed": vertex is not None,
        "vertex_error": vertex_error,
        "credentials_recorded": False,
        "generated_at": utc_now(),
    })
    return developer, vertex


def transport_preflight(
    project_dir: Path,
    output_dir: Path,
    *,
    inspect_availability: bool,
) -> tuple[GeminiRelationClient, GeminiRelationClient | None, dict[str, Any]]:
    """Return the transport preflight."""
    developer, vertex = _clients(project_dir, output_dir)
    results: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "model": MODEL,
        "availability_checked": inspect_availability,
        "developer_api": {"configured": True, "available": None},
        "vertex_ai": {"configured": vertex is not None, "available": None},
        "settings_parity": None,
        "no_generation_request_made": True,
        "generated_at": utc_now(),
    }
    available = 0
    if inspect_availability:
        for name, client in (("developer_api", developer), ("vertex_ai", vertex)):
            if client is None:
                continue
            try:
                results[name].update(client.model_available())
                results[name]["available"] = True
                available += 1
            except Exception as exc:
                results[name].update({
                    "available": False,
                    "error": f"{type(exc).__name__}: {exc}"[:2000],
                })
        if not available:
            atomic_write_json(output_dir / "transport_preflight.json", results)
            raise RuntimeError(f"{MODEL} is unavailable through both configured transports")
    if vertex is not None:
        require_transport_parity(developer, vertex, CONTRACT_SCHEMA, CONTRACT_MAX_OUTPUT_TOKENS)
        results["settings_parity"] = True
    else:
        results["settings_parity"] = "vertex_unavailable"
    atomic_write_json(output_dir / "transport_preflight.json", results)
    return developer, vertex, results


def _contract_db(output_dir: Path) -> dict[str, Any]:
    return read_json(output_dir / "semantic_contracts.json", {
        "schema_version": SCHEMA_VERSION,
        "contract_schema_version": CONTRACT_SCHEMA_VERSION,
        "records": {}, "failures": {},
    })


def run_pilot(
    project_dir: Path,
    output_dir: Path,
    *,
    execute: bool,
    confirmed_limit: float,
) -> dict[str, Any]:
    """Run pilot."""
    if not execute or confirmed_limit != HARD_COMBINED_CEILING_USD:
        raise RuntimeError("pilot requires --execute and exact --confirm-combined-limit-usd 60")
    developer, vertex, _preflight = transport_preflight(
        project_dir, output_dir, inspect_availability=True,
    )
    ledger = CostLedger(output_dir)
    router = RelationRouter(output_dir, developer, vertex, ledger)
    quote_manifest = read_json(output_dir / "quote_corpus_manifest.json")
    packet_by_id = {row["quote_id"]: row for row in quote_manifest["records"]}
    pilot = read_json(output_dir / "pilot_manifest.json")
    contract_db = _contract_db(output_dir)
    for quote_id in pilot["quote_ids"]:
        if quote_id in contract_db["records"]:
            continue
        packet = packet_by_id[quote_id]
        logical_id = f"pilot-contract-{quote_id}"
        result = router.call(
            logical_id=logical_id,
            prompt=contract_prompt(packet),
            schema=CONTRACT_SCHEMA,
            max_output_tokens=CONTRACT_MAX_OUTPUT_TOKENS,
            validator=lambda value, packet=packet: validate_contract(value, packet),
        )
        contract_db["records"][quote_id] = result["response"]
        contract_db["updated_at"] = utc_now()
        atomic_write_json(output_dir / "semantic_contracts.json", contract_db)
    pairs_by_image = pilot_pairs(output_dir, contract_db["records"])
    pair_db = read_json(output_dir / "pair_judgements.json", {
        "schema_version": SCHEMA_VERSION, "pair_schema_version": PAIR_SCHEMA_VERSION,
        "records": {}, "failures": {},
    })
    for row in pairs_by_image:
        image = row["image"]
        for chunk_number, pairs in enumerate(pair_chunks(row["pairs"]), 1):
            logical_id = f"pilot-pairs-{text_hash(image['image_id'])[:20]}-{chunk_number:02d}"
            if all(pair["pair_id"] in pair_db["records"] for pair in pairs):
                continue
            result = router.call(
                logical_id=logical_id,
                prompt=pair_prompt(image, pairs),
                schema=pair_response_schema(len(pairs)),
                max_output_tokens=PAIR_MAX_OUTPUT_TOKENS,
                validator=lambda value, image=image, pairs=pairs: validate_pair_response(value, image, pairs),
            )
            for judgement in result["response"]:
                pair_db["records"][judgement["pair_id"]] = judgement
            pair_db["updated_at"] = utc_now()
            atomic_write_json(output_dir / "pair_judgements.json", pair_db)
    evaluation = evaluate_frozen_evidence(project_dir, output_dir, scope="pilot")
    return evaluation


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _positive_retention_gate(retained_count: int, positive_count: int) -> bool:
    """Return the positive retention gate."""
    rate = _ratio(retained_count, positive_count)
    return rate is not None and rate >= 0.95


def _all_safety_rejections_vetoed(records: Sequence[dict[str, Any]]) -> bool:
    """Return the all safety rejections vetoed."""
    return bool(records) and all(row.get("system_decision") == "veto" for row in records)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def conservative_ambiguous_pair_vetoes(
    image: dict[str, Any], pairs: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the conservative ambiguous pair vetoes."""
    value = {"records": [{
        "pair_id": row["pair_id"],
        "quote_id": row["quote_id"],
        "image_id": image["image_id"],
        "decision": "uncertain",
        "confidence": "low",
        "materially_misleading": False,
        "relationship_supported": (
            "unknown" if row["contract"]["relationship_evidence_required"] else "not_required"
        ),
        "contradiction_types": ["insufficient_evidence"],
        "dominant_visual_message": "No validated provider judgement is available.",
        "reason": "A prior transmitted request had an ambiguous outcome; automatic resubmission is blocked.",
    } for row in pairs]}
    return validate_pair_response(value, image, pairs)


def local_deterministic_pair_veto(
    logical_id: str, image: dict[str, Any], pair: dict[str, Any],
) -> dict[str, Any]:
    """Return the local deterministic pair veto."""
    contradictions = pair.get("deterministic_contradictions") or []
    if not contradictions:
        raise ValueError("local deterministic recovery requires a deterministic contradiction")
    contradiction_types = sorted({
        rule if rule in CONTRADICTION_TYPES else "other"
        for rule in (str(row.get("rule") or "other") for row in contradictions)
    })
    value = {"records": [{
        "pair_id": pair["pair_id"],
        "quote_id": pair["quote_id"],
        "image_id": image["image_id"],
        "decision": "veto",
        "confidence": "high",
        "materially_misleading": True,
        "relationship_supported": (
            "unknown" if pair["contract"]["relationship_evidence_required"] else "not_required"
        ),
        "contradiction_types": contradiction_types,
        "dominant_visual_message": str(
            (image.get("visual") or {}).get("scene_summary")
            or (image.get("visual") or {}).get("description")
            or "The source-grounded image scene."
        ),
        "reason": " ".join(str(row.get("detail") or "") for row in contradictions).strip(),
    }]}
    return {
        "schema_version": SCHEMA_VERSION,
        "logical_call_id": logical_id,
        "status": "completed",
        "transport": "local_deterministic_recovery",
        "prompt_hash": None,
        "response": validate_pair_response(value, image, [pair]),
        "normalisation": ["deterministic_contradiction_veto_after_invalid_or_truncated_batch_item"],
        "usage": {"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "thinking_tokens": 0},
        "cost_usd": 0.0,
        "completed_at": utc_now(),
    }


def merge_split_pair_results(
    split_results: Sequence[dict[str, Any]], expected_pair_ids: Sequence[str],
) -> list[dict[str, Any]]:
    """Merge split pair results."""
    records = [
        record
        for result in split_results
        for record in (result.get("response") or [])
    ]
    actual_ids = [str(record.get("pair_id") or "") for record in records]
    if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != set(expected_pair_ids):
        raise RuntimeError("split pair recovery did not return the exact expected pair IDs")
    return sorted(records, key=lambda row: row["pair_id"])


def build_production_top5_batch_manifest(
    output_dir: Path,
    candidates: dict[str, Any],
    pair_db: dict[str, Any],
) -> dict[str, Any]:
    """Build production top5 batch manifest."""
    path = output_dir / "production_top5_batch_manifest.json"
    if path.exists():
        saved = read_json(path)
        if saved.get("source_pair_id_set_sha256") != candidates.get("pair_id_set_sha256"):
            raise RuntimeError("saved production top-five batch manifest does not match candidates")
        return saved
    by_image: dict[str, list[dict[str, Any]]] = defaultdict(list)
    reused = 0
    for quote_row in candidates["records"]:
        for pair in quote_row["pairs"]:
            if pair["pair_id"] in pair_db["records"]:
                reused += 1
            else:
                by_image[pair["image_id"]].append(pair)
    items = []
    for image_id in sorted(by_image):
        for pairs in pair_chunks(sorted(by_image[image_id], key=lambda row: row["pair_id"])):
            pair_ids = [row["pair_id"] for row in pairs]
            logical_id = (
                f"production-pairs-{text_hash(image_id)[:16]}-"
                f"{text_hash(chr(10).join(pair_ids))[:16]}"
            )
            items.append({
                "logical_id": logical_id,
                "image_id": image_id,
                "pair_ids": pair_ids,
                "pairs": pairs,
            })
    logical_ids = [row["logical_id"] for row in items]
    if len(logical_ids) != len(set(logical_ids)):
        raise RuntimeError("production top-five batch logical IDs are not unique")
    missing_pair_ids = {
        pair_id for row in items for pair_id in row["pair_ids"]
    }
    value = {
        "schema_version": SCHEMA_VERSION,
        "manifest_version": "production-top5-missing-pairs-v1",
        "source_pair_id_set_sha256": candidates["pair_id_set_sha256"],
        "union_pair_count": candidates["union_pair_count"],
        "reused_pair_count": reused,
        "missing_pair_count": len(missing_pair_ids),
        "batch_item_count": len(items),
        "maximum_items_per_provider_batch": PRODUCTION_BATCH_MAX_ITEMS,
        "missing_pair_id_set_sha256": text_hash("\n".join(sorted(missing_pair_ids)) + "\n"),
        "items": items,
        "created_at": utc_now(),
    }
    atomic_write_json(path, value)
    return value


def build_production_top5_preflight(
    output_dir: Path,
    candidates: dict[str, Any],
    batch_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Build production top5 preflight."""
    preflight_path = output_dir / "production_top5_preflight.json"
    if preflight_path.exists():
        saved = read_json(preflight_path)
        if (
            saved.get("candidate_pair_count") != candidates.get("union_pair_count")
            or saved.get("missing_pair_count") != batch_manifest.get("missing_pair_count")
        ):
            raise RuntimeError("saved production top-five preflight does not match the immutable batch manifest")
        return saved
    image_by_id = {
        row["image_id"]: row
        for row in read_json(output_dir / "image_corpus_manifest.json")["records"]
    }
    item_costs = []
    for item in batch_manifest["items"]:
        prompt = pair_prompt(image_by_id[item["image_id"]], item["pairs"])
        item_costs.append(maximum_cost(prompt, PAIR_MAX_OUTPUT_TOKENS, batch=True))
    ledger = read_json(output_dir / "cost_ledger.json")
    observed_pair_batch_cost = next((
        float(row.get("actual_usd") or 0)
        for row in ledger.get("operations") or []
        if row.get("event") == "completed"
        and row.get("operation_id") == "batch:full-pair-judgements-003-typed-response-schema"
    ), 0.0)
    observed_pair_count = 2485
    observed_cost_per_pair = observed_pair_batch_cost / observed_pair_count
    expected_incremental = observed_cost_per_pair * batch_manifest["missing_pair_count"]
    expected_with_repair_contingency = expected_incremental * 1.2
    known = float(ledger.get("known_spend_usd") or 0)
    ambiguous = float(ledger.get("ambiguous_exposure_usd") or 0)
    block_maxima = [
        round(sum(item_costs[start:start + PRODUCTION_BATCH_MAX_ITEMS]), 10)
        for start in range(0, len(item_costs), PRODUCTION_BATCH_MAX_ITEMS)
    ]
    projected_total = known + ambiguous + expected_with_repair_contingency
    value = {
        "schema_version": SCHEMA_VERSION,
        "candidate_pair_count": candidates["union_pair_count"],
        "reused_pair_count": batch_manifest["reused_pair_count"],
        "missing_pair_count": batch_manifest["missing_pair_count"],
        "batch_item_count": batch_manifest["batch_item_count"],
        "provider_batch_count": len(block_maxima),
        "provider_batch_maximum_reservations_usd": block_maxima,
        "conservative_all_items_maximum_usd": round(sum(item_costs), 10),
        "observed_cost_per_pair_usd": round(observed_cost_per_pair, 10),
        "expected_incremental_cost_usd": round(expected_incremental, 6),
        "expected_with_20_percent_repair_contingency_usd": round(expected_with_repair_contingency, 6),
        "known_spend_before_usd": known,
        "ambiguous_exposure_before_usd": ambiguous,
        "projected_combined_exposure_usd": round(projected_total, 6),
        "hard_combined_ceiling_usd": HARD_COMBINED_CEILING_USD,
        "within_expected_ceiling": projected_total <= HARD_COMBINED_CEILING_USD,
        "sequential_reservations_required": True,
        "no_provider_call_made": True,
        "generated_at": utc_now(),
    }
    if not value["within_expected_ceiling"]:
        raise RuntimeError("production top-five expected cost exceeds cumulative US$60 ceiling")
    atomic_write_json(preflight_path, value)
    atomic_write_text(output_dir / "production_top5_preflight.md", "\n".join([
        "# Production-oriented top-five veto preflight", "",
        f"- Unique current/expanded pairs: {value['candidate_pair_count']}",
        f"- Reused judgements: {value['reused_pair_count']}",
        f"- Missing judgements: {value['missing_pair_count']}",
        f"- Sequential provider batches: {value['provider_batch_count']}",
        f"- Expected incremental cost: US${value['expected_incremental_cost_usd']:.4f}",
        f"- Expected with repair contingency: US${value['expected_with_20_percent_repair_contingency_usd']:.4f}",
        f"- Projected combined exposure: US${value['projected_combined_exposure_usd']:.4f}",
        f"- Hard combined ceiling: US${HARD_COMBINED_CEILING_USD:.2f}", "",
        "Provider batches are sequential. The durable ledger checks every reservation and call against the cumulative hard ceiling.",
    ]) + "\n")
    return value


def _simulate_lane(
    pair_ids: Sequence[str], pair_by_id: dict[str, dict[str, Any]], judgements: dict[str, Any],
) -> dict[str, Any]:
    """Return the simulate lane."""
    ordered = [pair_by_id[pair_id] for pair_id in pair_ids]
    winner = ordered[0]
    allowed = [row for row in ordered if judgements[row["pair_id"]]["final_decision"] == "allow"]
    selected = allowed[0] if allowed else None
    return {
        "selector_winner_pair_id": winner["pair_id"],
        "selector_winner_image_id": winner["image_id"],
        "selector_winner_score": winner["selector_score"],
        "selector_winner_vetoed": judgements[winner["pair_id"]]["final_decision"] == "veto",
        "selected_pair_id": selected["pair_id"] if selected else None,
        "selected_image_id": selected["image_id"] if selected else None,
        "selected_score": selected["selector_score"] if selected else None,
        "allowed_candidate_count": len(allowed),
        "fallback_used": bool(selected and selected["pair_id"] != winner["pair_id"]),
        "no_allowed_image": selected is None,
    }


def simulate_production_top5(output_dir: Path) -> dict[str, Any]:
    """Return the simulate production top5."""
    candidates = read_json(output_dir / "production_top5_pair_candidates.json")
    judgements = read_json(output_dir / "pair_judgements.json")["records"]
    image_manifest = read_json(output_dir / "image_corpus_manifest.json")
    corpus_by_image = {row["image_id"]: row.get("corpus") for row in image_manifest["records"]}
    records = []
    for quote_row in candidates["records"]:
        pair_by_id = {row["pair_id"]: row for row in quote_row["pairs"]}
        required = set(quote_row["current_69_top5_pair_ids"] + quote_row["expanded_91_top5_pair_ids"])
        if not required.issubset(judgements):
            raise RuntimeError(f"production simulation missing judgements for {quote_row['quote_id']}")
        current = _simulate_lane(
            quote_row["current_69_top5_pair_ids"], pair_by_id, judgements,
        )
        expanded = _simulate_lane(
            quote_row["expanded_91_top5_pair_ids"], pair_by_id, judgements,
        )
        records.append({
            "quote_id": quote_row["quote_id"],
            "current_69": current,
            "expanded_91": expanded,
            "expanded_selected_discovered": bool(
                expanded["selected_image_id"]
                and corpus_by_image[expanded["selected_image_id"]] == "discovered_production_ready"
            ),
            "selected_image_changed": current["selected_image_id"] != expanded["selected_image_id"],
        })
    def lane_metrics(name: str) -> dict[str, Any]:
        rows = [row[name] for row in records]
        return {
            "quote_count": len(rows),
            "selector_winner_veto_count": sum(row["selector_winner_vetoed"] for row in rows),
            "selector_winner_allow_count": sum(not row["selector_winner_vetoed"] for row in rows),
            "fallback_used_count": sum(row["fallback_used"] for row in rows),
            "no_allowed_image_count": sum(row["no_allowed_image"] for row in rows),
            "at_least_one_allowed_count": sum(not row["no_allowed_image"] for row in rows),
        }
    metrics = {
        "current_69": lane_metrics("current_69"),
        "expanded_91": lane_metrics("expanded_91"),
        "selected_image_changed_count": sum(row["selected_image_changed"] for row in records),
        "expanded_selected_discovered_count": sum(row["expanded_selected_discovered"] for row in records),
    }
    critical_quote = "e0770fc3d6619db33fb692301a29fbf15f0e8e2306bd568064e557a108f69ec3"
    value = {
        "schema_version": SCHEMA_VERSION,
        "simulation_version": "production-top5-current69-expanded91-v1",
        "observational_offline_only": True,
        "metrics": metrics,
        "critical_enemy_to_friend_case": next(row for row in records if row["quote_id"] == critical_quote),
        "records": records,
        "generated_at": utc_now(),
    }
    atomic_write_json(output_dir / "production_selector_simulation.json", value)
    atomic_write_text(output_dir / "production_selector_simulation.md", "\n".join([
        "# Production-Oriented Veto Simulation", "",
        "This is an offline observational simulation. It does not change the selector or production state.", "",
        "| Corpus | Winner vetoed | Fallback used | At least one allowed | No allowed image |",
        "|---|---:|---:|---:|---:|",
        f"| Current 69 | {metrics['current_69']['selector_winner_veto_count']} | {metrics['current_69']['fallback_used_count']} | {metrics['current_69']['at_least_one_allowed_count']} | {metrics['current_69']['no_allowed_image_count']} |",
        f"| Expanded 91 | {metrics['expanded_91']['selector_winner_veto_count']} | {metrics['expanded_91']['fallback_used_count']} | {metrics['expanded_91']['at_least_one_allowed_count']} | {metrics['expanded_91']['no_allowed_image_count']} |", "",
        f"Expanded selector chose a discovered image after veto for {metrics['expanded_selected_discovered_count']} quotations.",
        "No production activation occurred.",
    ]) + "\n")
    return value


_SAFETY_REJECTION_NOTE_MARKERS = (
    "inappropriate",
    "allie",
    "enemy",
    "misleading",
    "contradict",
    "false",
    "wrong relationship",
)


def _is_explicit_safety_rejection(label: dict[str, Any]) -> bool:
    """Separate evidence of harm from a mere preference for another image."""
    note = str(label.get("note") or "").casefold()
    return any(marker in note for marker in _SAFETY_REJECTION_NOTE_MARKERS)


def _is_operator_withdrawn_positive(
    label: dict[str, Any], image: dict[str, Any] | None,
) -> bool:
    """Apply the operator's explicit withdrawal of accepted Reagan pair labels."""
    return bool(label.get("human_positive")) and "Ronald Reagan" in set(
        (image or {}).get("named_people") or []
    )


def partition_frozen_evaluation_labels(
    labels: Sequence[dict[str, Any]],
    image_id_by_candidate: dict[str, str],
    *,
    allowed_candidate_ids: set[str] | None = None,
    allowed_quote_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate in-scope frozen evidence from labels for excluded images.

    Frozen reviews may include research candidates that are intentionally absent
    from the authorised 69+22 image corpus. Those labels remain auditable, but
    cannot be required pair judgements for this run.
    """
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for label in labels:
        candidate_id = str(label["candidate_id"])
        quote_id = str(label["quote_hash"])
        if allowed_candidate_ids is not None and candidate_id not in allowed_candidate_ids:
            continue
        if allowed_quote_ids is not None and quote_id not in allowed_quote_ids:
            continue
        if candidate_id not in image_id_by_candidate:
            excluded.append({
                "candidate_id": candidate_id,
                "quote_id": quote_id,
                "reason": "image_not_in_authorised_corpus",
            })
            continue
        eligible.append(label)
    return eligible, excluded


def evaluate_frozen_evidence(project_dir: Path, output_dir: Path, *, scope: str) -> dict[str, Any]:
    """Evaluate frozen evidence."""
    if scope not in {"pilot", "full"}:
        raise ValueError("evaluation scope must be pilot or full")
    split = _load_frozen_split(project_dir)
    pilot = read_json(output_dir / "pilot_manifest.json")
    pair_db = read_json(output_dir / "pair_judgements.json")
    image_manifest = read_json(output_dir / "image_corpus_manifest.json")
    image_id_by_candidate = {
        row.get("candidate_id"): row["image_id"]
        for row in image_manifest["records"] if row.get("candidate_id")
    }
    eligible_labels, excluded_labels = partition_frozen_evaluation_labels(
        split["evaluation_pairs"],
        image_id_by_candidate,
        allowed_candidate_ids=set(pilot["candidate_ids"]) if scope == "pilot" else None,
        allowed_quote_ids=set(pilot["quote_ids"]) if scope == "pilot" else None,
    )
    records = []
    missing_pair_ids = []
    for label in eligible_labels:
        image_id = image_id_by_candidate[label["candidate_id"]]
        pair_id = text_hash(f"{image_id}\n{label['quote_hash']}")
        judgement = pair_db["records"].get(pair_id)
        if not judgement:
            missing_pair_ids.append(pair_id)
            continue
        records.append({
            "pair_id": pair_id, "candidate_id": label["candidate_id"],
            "quote_id": label["quote_hash"], "human_positive": bool(label["human_positive"]),
            "human_decision": label["human_decision"], "human_note": label.get("note") or "",
            "human_safety_rejection": _is_explicit_safety_rejection(label),
            "system_decision": judgement["final_decision"],
            "deterministic_contradictions": judgement["deterministic_contradictions"],
            "model_decision": judgement["decision"],
        })
    positives = [row for row in records if row["human_positive"]]
    negatives = [row for row in records if not row["human_positive"]]
    nonpreferred = [
        row for row in records if row["human_decision"] == "prefer_existing_image"
    ]
    safety_rejections = [row for row in records if row["human_safety_rejection"]]
    safety_false_accepts = [
        row for row in safety_rejections if row["system_decision"] == "allow"
    ]
    nonpreferred_allows = [
        row for row in nonpreferred if row["system_decision"] == "allow"
    ]
    retained = [row for row in positives if row["system_decision"] == "allow"]
    critical_quote = "e0770fc3d6619db33fb692301a29fbf15f0e8e2306bd568064e557a108f69ec3"
    ally_candidates = {
        "00f4566964e0c88838ad", "0b15f11312a31033907d", "0fe7ed6333faf654b062",
        "149051134f38e43abb40", "168392f289b5df25f16b", "5398b106f38f66e60fa9",
    }
    critical = [
        row for row in records
        if row["quote_id"] == critical_quote and row["candidate_id"] in ally_candidates
    ]
    deterministic_overrides = [
        row for row in records
        if row["deterministic_contradictions"] and row["system_decision"] != "veto"
    ]
    metrics = {
        "frozen_evaluation_pair_count": len(split["evaluation_pairs"]),
        "eligible_frozen_pair_count": len(eligible_labels),
        "excluded_frozen_pair_count": len(excluded_labels),
        "evaluated_eligible_pair_count": len(records),
        "missing_eligible_pair_count": len(missing_pair_ids),
        "labelled_pair_count": len(records),
        "positive_count": len(positives), "negative_count": len(negatives),
        "positive_retained_count": len(retained),
        "positive_retention_rate": _ratio(len(retained), len(positives)),
        "nonpreferred_pair_count": len(nonpreferred),
        "nonpreferred_allow_count": len(nonpreferred_allows),
        "nonpreferred_allow_rate": _ratio(len(nonpreferred_allows), len(nonpreferred)),
        "safety_rejection_count": len(safety_rejections),
        "safety_rejection_veto_count": len(safety_rejections) - len(safety_false_accepts),
        "safety_rejection_veto_rate": _ratio(
            len(safety_rejections) - len(safety_false_accepts), len(safety_rejections)
        ),
        "safety_false_accept_count": len(safety_false_accepts),
        "critical_ally_case_count": len(critical),
        "critical_ally_veto_count": sum(row["system_decision"] == "veto" for row in critical),
        "deterministic_override_count": len(deterministic_overrides),
    }
    gates = {
        "at_least_20_held_out_pairs": len(records) >= 20,
        "all_eligible_frozen_pairs_evaluated": (
            len(records) == len(eligible_labels) and not missing_pair_ids
        ),
        "critical_ally_cases_present": len(critical) >= 2,
        "critical_ally_cases_all_vetoed": bool(critical) and all(
            row["system_decision"] == "veto" for row in critical
        ),
        "explicit_safety_rejections_present": bool(safety_rejections),
        "all_explicit_safety_rejections_vetoed": not safety_false_accepts,
        "at_least_95_percent_human_approved_pairs_retained": _positive_retention_gate(
            len(retained), len(positives),
        ),
        "no_deterministic_contradiction_override": not deterministic_overrides,
    }
    value = {
        "schema_version": SCHEMA_VERSION, "scope": scope,
        "frozen_evaluation_only": True, "human_labels_sent_to_model": False,
        "metrics": metrics, "gates": gates, "passed": all(gates.values()),
        "safety_false_accepts": safety_false_accepts,
        "nonpreferred_allows": nonpreferred_allows,
        "critical_ally_cases": critical,
        "excluded_frozen_evidence": excluded_labels,
        "missing_eligible_pair_ids": missing_pair_ids,
        "records": records, "generated_at": utc_now(),
    }
    atomic_write_json(output_dir / f"{scope}_evaluation.json", value)
    return value


def run_full(
    project_dir: Path,
    output_dir: Path,
    *,
    execute: bool,
    confirmed_limit: float,
    poll_seconds: float = 30.0,
) -> dict[str, Any]:
    """Run full."""
    if not execute or confirmed_limit != HARD_COMBINED_CEILING_USD:
        raise RuntimeError("full run requires --execute and exact --confirm-combined-limit-usd 60")
    pilot_evaluation = read_json(output_dir / "pilot_evaluation.json")
    if not pilot_evaluation or not pilot_evaluation.get("passed"):
        raise RuntimeError("strict pilot evaluation gates did not pass; full run is blocked")
    developer, vertex, _preflight = transport_preflight(
        project_dir, output_dir, inspect_availability=True,
    )
    ledger = CostLedger(output_dir)
    router = RelationRouter(output_dir, developer, vertex, ledger)
    batch = DeveloperBatchRunner(
        output_dir, router, ledger, poll_seconds=poll_seconds,
    )
    quote_manifest = read_json(output_dir / "quote_corpus_manifest.json")
    packet_by_id = {row["quote_id"]: row for row in quote_manifest["records"]}
    contract_db = _contract_db(output_dir)
    contract_items = []
    contract_validators: dict[str, Callable[[Any], Any]] = {}
    for quote_id in sorted(packet_by_id):
        if quote_id in contract_db["records"]:
            continue
        packet = packet_by_id[quote_id]
        logical_id = f"contract-{quote_id}"
        contract_items.append({
            "logical_id": logical_id,
            "prompt": contract_prompt(packet),
            "schema": CONTRACT_SCHEMA,
            "max_output_tokens": CONTRACT_MAX_OUTPUT_TOKENS,
        })
        contract_validators[logical_id] = (
            lambda value, packet=packet: validate_contract(value, packet)
        )
    if contract_items:
        results = batch.run(
            batch_id="full-semantic-contracts-001",
            items=contract_items,
            validators=contract_validators,
        )
        for item in contract_items:
            quote_id = item["logical_id"].removeprefix("contract-")
            contract_db["records"][quote_id] = results[item["logical_id"]]["response"]
            contract_db["updated_at"] = utc_now()
            atomic_write_json(output_dir / "semantic_contracts.json", contract_db)
    if set(contract_db["records"]) != set(packet_by_id):
        raise RuntimeError("full contract set is incomplete after batch execution")
    candidates = build_pair_candidates(project_dir, output_dir, contract_db["records"])
    image_by_id = {
        row["image_id"]: row for row in read_json(output_dir / "image_corpus_manifest.json")["records"]
    }
    pair_db = read_json(output_dir / "pair_judgements.json", {
        "schema_version": SCHEMA_VERSION, "pair_schema_version": PAIR_SCHEMA_VERSION,
        "records": {}, "failures": {},
    })
    pair_db["pair_schema_version"] = PAIR_SCHEMA_VERSION
    ambiguous_pair_logical_ids = {
        str(row.get("logical_call_id") or "").removesuffix("-batch-repair")
        for row in _read_jsonl(output_dir / "attempts.jsonl")
        if row.get("ambiguous_outcome") is True
        and str(row.get("logical_call_id") or "").startswith("pairs-")
        and str(row.get("logical_call_id") or "").endswith("-batch-repair")
    }
    pair_items = []
    pair_validators: dict[str, Callable[[Any], Any]] = {}
    for image_row in candidates["records"]:
        image_id = image_row["image_id"]
        image = image_by_id[image_id]
        for chunk_number, pairs in enumerate(pair_chunks(image_row["pairs"]), 1):
            if all(row["pair_id"] in pair_db["records"] for row in pairs):
                continue
            logical_id = f"pairs-{text_hash(image_id)[:24]}-{chunk_number:02d}"
            if logical_id in ambiguous_pair_logical_ids:
                recovered = conservative_ambiguous_pair_vetoes(image, pairs)
                for judgement in recovered:
                    pair_db["records"][judgement["pair_id"]] = judgement
                pair_db["updated_at"] = utc_now()
                atomic_write_json(output_dir / "pair_judgements.json", pair_db)
                atomic_write_json(output_dir / "ambiguous_pair_vetoes.json", {
                    "schema_version": SCHEMA_VERSION,
                    "policy": "ambiguous provider outcome is never resubmitted automatically; affected pairs are conservatively vetoed",
                    "logical_call_ids": sorted(ambiguous_pair_logical_ids),
                    "records": recovered,
                    "generated_at": utc_now(),
                })
                continue
            pair_items.append({
                "logical_id": logical_id,
                "image": image,
                "prompt": pair_prompt(image, pairs),
                "schema": pair_response_schema(len(pairs)),
                "max_output_tokens": PAIR_MAX_OUTPUT_TOKENS,
                "expected_pair_ids": [row["pair_id"] for row in pairs],
                "recovery_splits": [{
                    "logical_id": f"{logical_id}-split-{split_number:02d}",
                    "prompt": pair_prompt(image, [pair]),
                    "schema": pair_response_schema(1),
                    "max_output_tokens": PAIR_MAX_OUTPUT_TOKENS,
                    "pair": pair,
                    "validator": (
                        lambda value, image=image, pair=pair:
                        validate_pair_response(value, image, [pair])
                    ),
                } for split_number, pair in enumerate(pairs, 1)],
            })
            pair_validators[logical_id] = (
                lambda value, image=image, pairs=pairs: validate_pair_response(value, image, pairs)
            )
    if pair_items:
        results = batch.run(
            batch_id="full-pair-judgements-003-typed-response-schema",
            items=pair_items,
            validators=pair_validators,
        )
        for item in pair_items:
            for judgement in results[item["logical_id"]]["response"]:
                pair_db["records"][judgement["pair_id"]] = judgement
            pair_db["updated_at"] = utc_now()
            atomic_write_json(output_dir / "pair_judgements.json", pair_db)
    expected_pair_ids = {
        pair["pair_id"] for image in candidates["records"] for pair in image["pairs"]
    }
    if not expected_pair_ids.issubset(pair_db["records"]):
        raise RuntimeError("full pair judgement set is incomplete")
    evaluation = evaluate_frozen_evidence(project_dir, output_dir, scope="full")
    finalise(output_dir, project_dir, evaluation)
    return evaluation


def prepare_production_top5(project_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Prepare production top5."""
    full = read_json(output_dir / "full_evaluation.json")
    if not full.get("passed"):
        raise RuntimeError("completed relation-aware safety evaluation is required")
    contracts = _contract_db(output_dir)["records"]
    candidates = build_production_top5_candidates(project_dir, output_dir, contracts)
    pair_db = read_json(output_dir / "pair_judgements.json", {"records": {}})
    batch_manifest = build_production_top5_batch_manifest(output_dir, candidates, pair_db)
    preflight = build_production_top5_preflight(output_dir, candidates, batch_manifest)
    return {
        "candidates": {
            key: candidates[key] for key in (
                "quote_count", "current_pair_count", "expanded_pair_count",
                "union_pair_count", "current_only_pair_count", "expanded_only_pair_count",
            )
        },
        "batch": {
            key: batch_manifest[key] for key in (
                "reused_pair_count", "missing_pair_count", "batch_item_count",
            )
        },
        "preflight": preflight,
    }


def run_production_top5(
    project_dir: Path,
    output_dir: Path,
    *,
    execute: bool,
    confirmed_limit: float,
    poll_seconds: float = 30.0,
) -> dict[str, Any]:
    """Run production top5."""
    raise RuntimeError(
        "retired v1 production-top5 execution: the v1 contract boundary failed positive-retention "
        "and corpus-coverage requirements; prepare the material-veto v2 revision instead"
    )
def finalise(output_dir: Path, project_dir: Path, evaluation: dict[str, Any]) -> dict[str, Any]:
    """Return the finalise."""
    contracts = _contract_db(output_dir)
    pair_db = read_json(output_dir / "pair_judgements.json")
    candidates = read_json(output_dir / "pair_candidates.json")
    candidate_by_pair = {
        row["pair_id"]: row
        for image in candidates["records"] for row in image["pairs"]
    }
    decisions = []
    for pair_id in sorted(candidate_by_pair):
        source = candidate_by_pair[pair_id]
        judgement = pair_db["records"][pair_id]
        decisions.append({
            "pair_id": pair_id, "quote_id": source["quote_id"], "image_id": source["image_id"],
            "selector_score": source["selector_score"],
            "selector_components": source["selector_components"],
            "selection_reasons": source["selection_reasons"],
            "deterministic_contradictions": source["deterministic_contradictions"],
            "model_decision": judgement["decision"],
            "model_confidence": judgement["confidence"],
            "materially_misleading": judgement["materially_misleading"],
            "final_decision": judgement["final_decision"],
            "final_reason": judgement["final_reason"],
            "reason": judgement["reason"],
        })
    veto_count = sum(row["final_decision"] == "veto" for row in decisions)
    deterministic_count = sum(bool(row["deterministic_contradictions"]) for row in decisions)
    value = {
        "schema_version": SCHEMA_VERSION,
        "policy": "hard deterministic contradiction OR model veto/uncertain => veto",
        "safety_principle": "A missed usable image is acceptable; a misleading image is not.",
        "quote_contract_count": len(contracts["records"]),
        "image_count": candidates["image_count"], "pair_count": len(decisions),
        "veto_count": veto_count, "allow_count": len(decisions) - veto_count,
        "deterministic_veto_count": deterministic_count,
        "records": decisions, "generated_at": utc_now(),
    }
    atomic_write_json(output_dir / "veto_decisions.json", value)
    production_after = production_hashes(project_dir)
    before = read_json(output_dir / "production_hashes_before.json")
    unchanged = production_after["aggregate_sha256"] == before["aggregate_sha256"]
    atomic_write_json(output_dir / "production_isolation_audit.json", {
        "schema_version": SCHEMA_VERSION,
        "before": before, "after": production_after, "unchanged": unchanged,
        "live_veto_enabled": False, "production_files_written": False,
        "generated_at": utc_now(),
    })
    if not unchanged:
        raise RuntimeError("production image/selector files changed during isolated veto run")
    eligible = bool(evaluation.get("passed"))
    shadow = {
        "schema_version": SCHEMA_VERSION,
        "eligible_for_future_shadow_integration": eligible,
        "enabled_in_live_production": False,
        "reason": (
            "strict frozen evaluation gates passed; operator review is still required before shadow activation"
            if eligible else "strict frozen evaluation gates failed"
        ),
        "decision_file_sha256": sha256_file(output_dir / "veto_decisions.json"),
        "generated_at": utc_now(),
    }
    atomic_write_json(output_dir / "shadow_integration_status.json", shadow)
    report = build_report(output_dir)
    return {"decisions": value, "isolation": unchanged, "shadow": shadow, "report": report}


def build_report(output_dir: Path) -> dict[str, Any]:
    """Build report."""
    manifest = read_json(output_dir / "run_manifest.json")
    preflight = read_json(output_dir / "cost_preflight.json")
    pilot = read_json(output_dir / "pilot_evaluation.json", {})
    full = read_json(output_dir / "full_evaluation.json", {})
    ledger = read_json(output_dir / "cost_ledger.json", {})
    routes = read_json(output_dir / "provider_route_state.json", {})
    decisions = read_json(output_dir / "veto_decisions.json", {})
    isolation = read_json(output_dir / "production_isolation_audit.json", {})
    jobs = read_json(output_dir / "batch_jobs.json", {}).get("jobs") or {}
    completed_operation_costs = {
        str(row.get("operation_id")): float(row.get("actual_usd") or 0)
        for row in ledger.get("operations") or []
        if row.get("event") == "completed"
    }
    batch_incidents = {
        "diagnostic-fixed-pair-envelope-001": "native_json_schema_transport_returned_schema_skeletons",
        "full-pair-judgements-001": "native_json_schema_transport_returned_schema_skeletons",
    }
    normalised = []
    for path in sorted((output_dir / "normalised_responses").glob("*.json")):
        value = read_json(path, {})
        if value.get("status") == "completed":
            normalised.append(value)
    provenance = {
        "schema_version": SCHEMA_VERSION,
        "initial_run_manifest_sha256": sha256_file(output_dir / "run_manifest.json"),
        "prepared_versions": {
            "contract_prompt_version": manifest.get("contract_prompt_version"),
            "contract_schema_version": manifest.get("contract_schema_version"),
            "pair_prompt_version": manifest.get("pair_prompt_version"),
            "pair_schema_version": manifest.get("pair_schema_version"),
        },
        "final_code_versions": {
            "contract_prompt_version": PROMPT_VERSION,
            "contract_schema_version": CONTRACT_SCHEMA_VERSION,
            "pair_prompt_version": PAIR_PROMPT_VERSION,
            "pair_schema_version": PAIR_SCHEMA_VERSION,
        },
        "model": MODEL,
        "sdk_version": getattr(genai, "__version__", manifest.get("sdk_version")),
        "developer_api_version": SDK_API_VERSION_DEVELOPER,
        "vertex_api_version": SDK_API_VERSION_VERTEX,
        "thinking_level": THINKING_LEVEL,
        "temperature": TEMPERATURE,
        "pricing_version": PRICING_VERSION,
        "completed_normalised_response_count": len(normalised),
        "completed_response_transport_counts": dict(sorted(Counter(
            str(row.get("transport") or "unavailable") for row in normalised
        ).items())),
        "completed_response_prompt_hash_count": len({
            str(row.get("prompt_hash")) for row in normalised if row.get("prompt_hash")
        }),
        "batch_jobs": [{
            "batch_id": batch_id,
            "provider_job_id": row.get("provider_job_id"),
            "item_count": row.get("item_count"),
            "recorded_status": row.get("status"),
            "provider_state": row.get("provider_state"),
            "effective_status": (
                "provider_succeeded_schema_transport_incompatible"
                if batch_id in batch_incidents
                else "provider_succeeded_local_processing_incomplete"
                if row.get("status") != "completed"
                and row.get("provider_state") in DeveloperBatchRunner.TERMINAL_SUCCESS
                else row.get("status")
            ),
            "incident": batch_incidents.get(batch_id),
            "known_cost_usd": (
                row.get("known_cost_usd")
                if row.get("known_cost_usd") is not None
                else completed_operation_costs.get(f"batch:{batch_id}")
            ),
        } for batch_id, row in sorted(jobs.items())],
        "note": (
            "The prepared manifest records the initial schema versions. Final code versions differ "
            "where the Gemini Batch native JSON-schema transport had to be replaced by the SDK's "
            "typed response-schema transport. Per-request prompt hashes and raw responses are retained."
        ),
        "generated_at": utc_now(),
    }
    atomic_write_json(output_dir / "execution_provenance.json", provenance)
    status = {
        "schema_version": SCHEMA_VERSION,
        "model": MODEL,
        "pilot_passed": pilot.get("passed"),
        "full_passed": full.get("passed"),
        "contract_count": decisions.get("quote_contract_count", 0),
        "image_count": decisions.get("image_count", manifest["image_count"]),
        "pair_count": decisions.get("pair_count", 0),
        "veto_count": decisions.get("veto_count", 0),
        "allow_count": decisions.get("allow_count", 0),
        "known_spend_usd": ledger.get("known_spend_usd", 0),
        "ambiguous_exposure_usd": ledger.get("ambiguous_exposure_usd", 0),
        "developer_paused": routes.get("developer_paused", False),
        "developer_attempts": routes.get("developer_attempts", 0),
        "vertex_attempts": routes.get("vertex_attempts", 0),
        "direct_to_vertex_count": routes.get("direct_to_vertex_count", 0),
        "production_files_unchanged": isolation.get("unchanged"),
        "live_production_enabled": False,
        "generated_at": utc_now(),
    }
    atomic_write_json(output_dir / "final_status.json", status)
    pilot_metrics = pilot.get("metrics") or {}
    full_metrics = full.get("metrics") or {}
    lines = [
        "# Relation-Aware Semantic Veto Report", "",
        "## Outcome", "",
        f"- Model: `{MODEL}`",
        f"- Canonical quotation contracts: {status['contract_count']} of {EXPECTED_QUOTES}",
        f"- Historical photographs: {status['image_count']} ({EXPECTED_BASELINE_IMAGES} existing + {EXPECTED_DISCOVERED_IMAGES} discovered)",
        f"- Selector-relevant pairs judged: {status['pair_count']}",
        f"- Final vetoes: {status['veto_count']}",
        f"- Final allows: {status['allow_count']}",
        f"- Known spend: US${float(status['known_spend_usd']):.4f}",
        f"- Ambiguous exposure: US${float(status['ambiguous_exposure_usd']):.4f}",
        "- Live production activation: no", "",
        "## Architecture", "",
        "1. Canonical research packets are converted into immutable semantic contracts.",
        "2. Source-grounded identities and relationships drive deterministic hard-conflict rules.",
        "3. Gemini acts as an adversarial pair judge; `uncertain` is a veto, not an allow.",
        "4. Broad topic scores cannot override a deterministic relationship contradiction.", "",
        "## Frozen Evaluation", "",
        f"- Pilot passed: {pilot.get('passed')}",
        f"- Pilot explicit-safety veto rate: {pilot_metrics.get('safety_rejection_veto_rate')}",
        f"- Pilot positive retention: {pilot_metrics.get('positive_retention_rate')}",
        f"- Full passed: {full.get('passed')}",
        f"- Full explicit-safety veto rate: {full_metrics.get('safety_rejection_veto_rate')}",
        f"- Full positive retention: {full_metrics.get('positive_retention_rate')}",
        "- Human-approved pair retention is a mandatory promotion gate and must be at least 95%.",
        f"- Eligible frozen pairs evaluated: {full_metrics.get('evaluated_eligible_pair_count')}/{full_metrics.get('eligible_frozen_pair_count')}",
        f"- Frozen labels excluded with unauthorised images: {full_metrics.get('excluded_frozen_pair_count')}",
        f"- Full safety false accepts: {full_metrics.get('safety_false_accept_count')}",
        f"- Full nonpreferred-image allows (not a safety error): {full_metrics.get('nonpreferred_allow_count')}",
        f"- Critical established-ally cases vetoed: {full_metrics.get('critical_ally_veto_count')}/{full_metrics.get('critical_ally_case_count')}", "",
        "## Transport and Cost", "",
        f"- Developer attempts: {status['developer_attempts']}",
        f"- Vertex attempts: {status['vertex_attempts']}",
        f"- Developer run-wide pause: {status['developer_paused']}",
        f"- Direct-to-Vertex requests: {status['direct_to_vertex_count']}",
        f"- Preflight expected cost: US${float(preflight['expected_combined_cost_usd']):.4f}",
        f"- Preflight conservative planned maximum: US${float(preflight['conservative_planned_maximum_usd']):.4f}",
        f"- Final pair prompt/schema: `{PAIR_PROMPT_VERSION}` / `{PAIR_SCHEMA_VERSION}`",
        f"- SDK/API versions: `{provenance['sdk_version']}` / Developer `{SDK_API_VERSION_DEVELOPER}` / Vertex `{SDK_API_VERSION_VERTEX}`",
        "- Native JSON-schema Batch transport produced unusable schema skeletons in the first full pair batch; the typed SDK response schema corrected it without changing model, prompt semantics, or safety policy.",
        "- Batch schema-transport corrections and per-request hashes are recorded in `execution_provenance.json`.", "",
        "## Safety", "",
        "The run used no search, URL context, tools, code execution, image generation, or facial identification.",
        f"Production image and selector hashes remained unchanged: {status['production_files_unchanged']}.",
        "The veto was not enabled in live production.", "",
        "## Limitation", "",
        "The full matrix is bounded to current-selector candidates, frozen evaluated pairs, and source-grounded entity matches. "
        "An unseen arbitrary pair must be judged before it can be treated as allowed.",
    ]
    atomic_write_text(output_dir / "relation_aware_semantic_veto_report.md", "\n".join(lines) + "\n")
    return status


def status(output_dir: Path) -> dict[str, Any]:
    """Return the status."""
    result = {
        "prepared": (output_dir / "run_manifest.json").exists(),
        "pilot_complete": (output_dir / "pilot_evaluation.json").exists(),
        "full_complete": (output_dir / "full_evaluation.json").exists(),
        "finalised": (output_dir / "final_status.json").exists(),
        "contracts": len((_contract_db(output_dir).get("records") or {})) if output_dir.exists() else 0,
        "pair_judgements": len((read_json(output_dir / "pair_judgements.json", {}).get("records") or {})),
        "cost": read_json(output_dir / "cost_ledger.json", {}),
        "route": read_json(output_dir / "provider_route_state.json", {}),
    }
    return result


def _paths(args: argparse.Namespace) -> tuple[Path, Path, Path, Path]:
    project_dir = Path(args.project_dir).resolve()
    research_run = Path(args.research_run)
    if not research_run.is_absolute():
        research_run = project_dir / research_run
    work_dir = Path(args.image_work_dir)
    if not work_dir.is_absolute():
        work_dir = project_dir / work_dir
    output_dir = Path(args.output)
    if not output_dir.is_absolute():
        output_dir = project_dir / output_dir
    return project_dir, research_run, work_dir, output_dir


def _add_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-dir", default=str(Path.cwd()))
    parser.add_argument(
        "--research-run", default="semantic_alignment_research/quote_research_full_001",
    )
    parser.add_argument(
        "--image-work-dir",
        default="image_discovery_research/thatcher_image_hunt_002/integration_preparation",
    )
    parser.add_argument(
        "--output", default="semantic_alignment_research/relation_aware_semantic_veto_001",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(description="Offline relation-aware quotation/image veto research")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in (
        "prepare", "status", "report", "pilot", "full", "execute-all",
        "production-preflight", "production-top5", "revise-v2", "v2-preflight", "v2-full",
        "v2-finalise-offline",
    ):
        command = sub.add_parser(name)
        _add_paths(command)
        if name in {"revise-v2", "v2-preflight", "v2-full", "v2-finalise-offline"}:
            command.add_argument(
                "--source-v1-run",
                default="semantic_alignment_research/relation_aware_semantic_veto_001",
            )
        if name in {"pilot", "full", "execute-all", "production-top5", "v2-full"}:
            command.add_argument("--execute", action="store_true")
            command.add_argument("--confirm-combined-limit-usd", type=float)
        if name in {"full", "execute-all", "production-top5", "v2-full"}:
            command.add_argument("--poll-seconds", type=float, default=30.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line entry point."""
    args = build_parser().parse_args(argv)
    project_dir, research_run, work_dir, output_dir = _paths(args)
    if args.command == "prepare":
        value = prepare(project_dir, research_run, work_dir, output_dir)
        value["cost_preflight"] = build_preflight(output_dir)
    elif args.command == "status":
        value = status(output_dir)
    elif args.command == "report":
        value = build_report(output_dir)
    elif args.command == "pilot":
        value = run_pilot(
            project_dir, output_dir, execute=args.execute,
            confirmed_limit=args.confirm_combined_limit_usd,
        )
    elif args.command == "full":
        value = run_full(
            project_dir, output_dir, execute=args.execute,
            confirmed_limit=args.confirm_combined_limit_usd,
            poll_seconds=args.poll_seconds,
        )
    elif args.command == "production-preflight":
        value = prepare_production_top5(project_dir, output_dir)
    elif args.command == "production-top5":
        value = run_production_top5(
            project_dir, output_dir, execute=args.execute,
            confirmed_limit=args.confirm_combined_limit_usd,
            poll_seconds=args.poll_seconds,
        )
    elif args.command in {"revise-v2", "v2-preflight", "v2-full", "v2-finalise-offline"}:
        source_dir = Path(args.source_v1_run)
        if not source_dir.is_absolute():
            source_dir = project_dir / source_dir
        if source_dir.resolve() == output_dir.resolve():
            raise RuntimeError("v2 revision output must not overwrite the v1 run")
        source_dir = source_dir.resolve()
        if args.command == "revise-v2":
            value = build_material_veto_revision(project_dir, source_dir, output_dir)
        elif args.command == "v2-preflight":
            value = prepare_v2_execution(source_dir, output_dir)
        elif args.command == "v2-full":
            value = run_material_veto_v2(
                project_dir, source_dir, output_dir,
                execute=args.execute,
                confirmed_limit=args.confirm_combined_limit_usd,
                poll_seconds=args.poll_seconds,
            )
        else:
            value = finalise_material_veto_v2_offline(
                project_dir, source_dir, output_dir,
            )
    else:
        prepare(project_dir, research_run, work_dir, output_dir)
        build_preflight(output_dir)
        pilot = run_pilot(
            project_dir, output_dir, execute=args.execute,
            confirmed_limit=args.confirm_combined_limit_usd,
        )
        if not pilot["passed"]:
            raise RuntimeError("pilot failed strict gates; full run not started")
        value = run_full(
            project_dir, output_dir, execute=args.execute,
            confirmed_limit=args.confirm_combined_limit_usd,
            poll_seconds=args.poll_seconds,
        )
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
