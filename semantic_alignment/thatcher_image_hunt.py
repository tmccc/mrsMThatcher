"""Conservative, isolated Margaret Thatcher archive-image discovery research."""

from __future__ import annotations

import argparse
import hashlib
import html
import ipaddress
import json
import mimetypes
import os
import re
import shutil
import socket
import statistics
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import unquote, urljoin, urlparse

import imagehash
import numpy as np
import requests
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
from jsonschema import Draft7Validator
from PIL import Image, ImageFile, UnidentifiedImageError

from .bakeoff import PRICES
from .hybrid_reply_retrieval import DEFAULT_MODEL_DIR, LocalE5Embedder, MODEL_ID as E5_MODEL_ID
from .io import atomic_write_json, atomic_write_text, read_json, sha256_file
from .quote_research_gemini import MODEL, SEARCH_QUERY_PRICE, extract_grounding

ImageFile.LOAD_TRUNCATED_IMAGES = False
Image.MAX_IMAGE_PIXELS = 120_000_000
LANCZOS = getattr(getattr(Image, "Resampling", Image), "LANCZOS")

SCHEMA_VERSION = 1
RUN_ID = "thatcher_image_hunt_001"
DISCOVERY_SCHEMA_VERSION = 4
MAX_DISCOVERY_CALLS = 6
MAX_TRIAGE_CALLS = 6
MAX_LOGICAL_CALLS = 12
TRIAGE_BATCH_SIZE = 25
TARGET_REVIEWABLE = 140
MAX_DOWNLOADED_CANDIDATES = 240
MAX_COMMONS_CATEGORIES = 80
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
MIN_PREFERRED_LONG_EDGE = 700
MIN_RETAINED_LONG_EDGE = 350
COMBINED_COST_LIMIT_USD = 20.0
DEVELOPER_COST_LIMIT_USD = 12.0
VERTEX_COST_LIMIT_USD = 12.0
MAX_OUTPUT_TOKENS_DISCOVERY = 8192
MAX_OUTPUT_TOKENS_TRIAGE = 24576
THINKING_BUDGET = 256
TEMPERATURE = 0.2

REVIEW_DECISIONS = {
    "keep", "reject", "maybe", "keep_as_replacement",
    "reject_visual_duplicate", "reject_editorially_redundant",
    "reject_poor_quality", "reject_rights_concern", "reject_attribution_unverified",
}
PRODUCTION_FORBIDDEN_NAMES = {
    "bot_state.json", "lines_used.json", "images_used.json", "mrsMThatcher.local.json",
    "confirmed_post_receipt.json", "confirmed_meme_receipt.json",
    "confirmed_reply_receipt.json", "confirmed_historical_context_reply_receipt.json",
}

SOURCE_PERSON_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Margaret Thatcher", (r"\bMargaret Thatcher\b", r"\bPrime Minister Thatcher\b", r"\bpremier Thatcher\b")),
    ("Ronald Reagan", (r"\bRonald Reagan\b", r"\bPresident Reagan\b", r"\bThatcher and Reagan\b")),
    ("George H. W. Bush", (r"\bGeorge H\.?\s*W\.?\s*Bush\b",)),
    ("Carl Albert", (r"\bCarl Albert\b",)),
    ("Robert Muldoon", (r"\bRobert Muldoon\b",)),
    ("Ruud Lubbers", (r"\bRuud Lubbers\b",)),
    ("Jimmy Carter", (r"\bJimmy Carter\b", r"\bPresident Carter\b")),
    ("Rosalynn Carter", (r"\bRosalynn Carter\b",)),
    ("Jacques Delors", (r"\bJacques Delors\b",)),
    ("Ciriaco De Mita", (r"\bCiriaco De Mita\b",)),
    ("Brian Mulroney", (r"\bBrian Mulroney\b",)),
    ("François Mitterrand", (r"\bFran[cç]ois Mitterrand\b",)),
    ("Helmut Kohl", (r"\bHelmut Kohl\b",)),
    ("Noboru Takeshita", (r"\bNoboru Takeshita\b",)),
    ("Robert J. Dole", (r"\bRobert J\.? Dole\b", r"\bDole, Robert J\.?\b", r"\bBob Dole\b")),
    ("Mikhail Gorbachev", (r"\bMikhail Gorbachev\b",)),
    ("Raisa Gorbacheva", (r"\bRaisa Gorbacheva\b",)),
    ("Denis Thatcher", (r"\bDenis Thatcher\b", r"\bDenis and Margaret Thatcher\b")),
    ("Gijs van Aardenne", (r"\bG\.M\.V\. van Aardenne\b", r"\bVan Aardenne\b", r"\bAardenne, G\.M\.V\. van\b")),
)


class ArchiveRateLimitExhausted(RuntimeError):
    """Raised when bounded archive rate-limit retries are exhausted."""
    def __init__(self, retry_after: float):
        """Initialise the archive rate limit exhausted."""
        super().__init__("archive image endpoint remained rate limited after one retry")
        self.retry_after = retry_after


DISCOVERY_RECORD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "source_page_url": {"type": "string"},
        "publisher": {"type": "string"},
        "page_title": {"type": "string"},
        "archive_or_collection": {"type": "string"},
        "event_or_period": {"type": "string"},
        "approximate_date": {"type": "string"},
        "people_or_context_named_by_source": {
            "type": "array", "items": {"type": "string"}, "maxItems": 12,
        },
        "caption_or_catalogue_text": {"type": "string"},
        "rights_or_licence_hint": {"type": "string"},
        "why_this_source_fills_a_gap": {"type": "string"},
        "search_queries_used": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
    },
    "required": [
        "source_page_url", "publisher", "page_title", "archive_or_collection",
        "event_or_period", "approximate_date", "people_or_context_named_by_source",
        "caption_or_catalogue_text", "rights_or_licence_hint",
        "why_this_source_fills_a_gap", "search_queries_used",
    ],
}
DISCOVERY_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "records": {"type": "array", "items": DISCOVERY_RECORD_SCHEMA, "maxItems": 20},
    },
    "required": ["records"],
}


def _string_array(maximum: int = 12) -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}, "maxItems": maximum}


VISUAL_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "description": {"type": "string"},
        "scene_summary": {"type": "string"},
        "scene_types": _string_array(8),
        "visible_elements": _string_array(16),
        "setting": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "location_type": {"type": "string", "enum": ["indoor", "outdoor", "mixed", "unknown"]},
                "details": _string_array(10),
            },
            "required": ["location_type", "details"],
        },
        "composition": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "shot_type": {"type": "string", "enum": [
                    "close_up", "head_and_shoulders", "half_body", "full_body", "group",
                    "crowd", "wide_scene", "document_or_artwork", "other",
                ]},
                "orientation": {"type": "string", "enum": ["landscape", "portrait", "square", "unknown"]},
                "subject_prominence": {"type": "integer", "minimum": 0, "maximum": 100},
                "background_complexity": {"type": "integer", "minimum": 0, "maximum": 100},
            },
            "required": ["shot_type", "orientation", "subject_prominence", "background_complexity"],
        },
        "people": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "count_category": {"type": "string", "enum": ["none", "one", "two", "small_group", "crowd", "unclear"]},
                "primary_subject_activities": _string_array(8),
                "primary_subject_moods": _string_array(7),
                "formality": {"type": "string", "enum": ["very_formal", "formal", "semi_formal", "informal", "mixed", "unknown"]},
            },
            "required": ["count_category", "primary_subject_activities", "primary_subject_moods", "formality"],
        },
        "visible_text": {
            "type": "object", "additionalProperties": False,
            "properties": {"present": {"type": "boolean"}, "summary": {"type": "string"}},
            "required": ["present", "summary"],
        },
        "themes": _string_array(10),
        "tone": _string_array(8),
        "visual_energy": {"type": "string", "enum": ["low", "medium", "high"]},
        "visible_symbols": _string_array(10),
        "quality": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "overall": {"type": "integer", "minimum": 0, "maximum": 100},
                "subject_clarity": {"type": "integer", "minimum": 0, "maximum": 100},
                "composition": {"type": "integer", "minimum": 0, "maximum": 100},
                "technical_quality": {"type": "integer", "minimum": 0, "maximum": 100},
                "crop_suitability_for_x": {"type": "integer", "minimum": 0, "maximum": 100},
                "damaged": {"type": "boolean"},
                "heavily_watermarked": {"type": "boolean"},
                "too_small": {"type": "boolean"},
            },
            "required": [
                "overall", "subject_clarity", "composition", "technical_quality",
                "crop_suitability_for_x", "damaged", "heavily_watermarked", "too_small",
            ],
        },
        "pairing": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "best_for_topics": _string_array(10),
                "best_for_tones": _string_array(8),
                "weak_for_topics": _string_array(8),
                "general_reusability": {"type": "integer", "minimum": 0, "maximum": 100},
                "semantic_specificity": {"type": "integer", "minimum": 0, "maximum": 100},
                "matching_summary": {"type": "string"},
            },
            "required": [
                "best_for_topics", "best_for_tones", "weak_for_topics",
                "general_reusability", "semantic_specificity", "matching_summary",
            ],
        },
    },
    "required": [
        "description", "scene_summary", "scene_types", "visible_elements", "setting",
        "composition", "people", "visible_text", "themes", "tone", "visual_energy",
        "visible_symbols", "quality", "pairing",
    ],
}
TRIAGE_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "records": {
            "type": "array", "maxItems": TRIAGE_BATCH_SIZE,
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "candidate_id": {"type": "string"},
                    "visual_analysis": VISUAL_ANALYSIS_SCHEMA,
                    "editorial_value": {
                        "type": "object", "additionalProperties": False,
                        "properties": {
                            "quality": {"type": "integer", "minimum": 0, "maximum": 100},
                            "distinctiveness": {"type": "integer", "minimum": 0, "maximum": 100},
                            "coverage_gap_value": {"type": "integer", "minimum": 0, "maximum": 100},
                            "recommended_priority": {"type": "string", "enum": ["high", "medium", "low", "reject"]},
                            "reasons": _string_array(8),
                        },
                        "required": ["quality", "distinctiveness", "coverage_gap_value", "recommended_priority", "reasons"],
                    },
                },
                "required": ["candidate_id", "visual_analysis", "editorial_value"],
            },
        },
    },
    "required": ["records"],
}


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_bytes(value: Any) -> bytes:
    """Return the canonical bytes."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_value(value: Any) -> str:
    """Return the SHA-256 value."""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    """Append jsonl."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_jsonl_if_exists(path: Path) -> list[dict[str, Any]]:
    """Read jsonl if exists."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def assert_isolated_path(research_dir: Path, target: Path) -> None:
    """Assert isolated path."""
    root = research_dir.resolve()
    resolved = target.resolve(strict=False)
    if resolved != root and root not in resolved.parents:
        raise RuntimeError(f"research write escaped isolated directory: {target}")
    if target.name in PRODUCTION_FORBIDDEN_NAMES or "images" == target.name and target.parent == root.parent.parent:
        raise RuntimeError(f"production write forbidden: {target}")


def load_baseline(path: Path) -> dict[str, Any]:
    """Load baseline."""
    baseline = read_json(path)
    if not isinstance(baseline, dict) or baseline.get("schema_version") != 3:
        raise RuntimeError("image baseline must use schema version 3")
    items = baseline.get("items")
    current = baseline.get("current_hashes")
    path_index = baseline.get("path_index")
    if not isinstance(items, dict) or not isinstance(current, list) or not isinstance(path_index, dict):
        raise RuntimeError("image baseline has invalid structure")
    if len(current) != 69 or len(set(current)) != 69:
        raise RuntimeError("image baseline must contain 69 unique current hashes")
    if set(current) != set(items) or len(set(path_index.values())) != 69:
        raise RuntimeError("image baseline hashes and path index do not reconcile")
    for digest in current:
        row = items[digest]
        if row.get("image_hash") != digest or not isinstance(row.get("analysis"), dict):
            raise RuntimeError(f"invalid baseline item {digest}")
    return baseline


def _count(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values if str(value).strip()).items(), key=lambda item: (-item[1], item[0])))


def build_coverage_profile(baseline: dict[str, Any]) -> dict[str, Any]:
    """Build coverage profile."""
    rows = [baseline["items"][digest]["analysis"] for digest in sorted(baseline["current_hashes"])]
    flattened: dict[str, list[str]] = defaultdict(list)
    qualities: list[int] = []
    specificity: list[str] = []
    for row in rows:
        flattened["scene_types"].extend(row.get("scene_types") or [])
        flattened["location_type"].append(str((row.get("setting") or {}).get("location_type") or "unknown"))
        flattened["orientation"].append(str((row.get("composition") or {}).get("orientation") or "unknown"))
        flattened["shot_type"].append(str((row.get("composition") or {}).get("shot_type") or "other"))
        flattened["visual_energy"].append(str(row.get("visual_energy") or "unknown"))
        flattened["tone"].extend(row.get("tone") or [])
        people = row.get("people") or {}
        flattened["activities"].extend(people.get("primary_subject_activities") or [])
        flattened["moods"].extend(people.get("primary_subject_moods") or [])
        flattened["people_count"].append(str(people.get("count_category") or "unclear"))
        flattened["visible_symbols"].extend((row.get("historical_context") or {}).get("visible_symbols") or [])
        flattened["pairing_topics"].extend((row.get("pairing") or {}).get("best_for_topics") or [])
        qualities.append(int((row.get("quality") or {}).get("overall") or 0))
        specificity.append(str((row.get("historical_context") or {}).get("specificity") or "general"))
        season = row.get("seasonality") or {}
        flattened["seasonality"].append(str(season.get("strength") or "none"))
    counts = {key: _count(value) for key, value in sorted(flattened.items())}
    formal_scenes = sum(counts["scene_types"].get(key, 0) for key in ("formal_portrait", "official_portrait"))
    priorities = [
        "early career, Finchley campaigning and Education Secretary years",
        "election canvassing, crowds, rallies and active outdoor campaigning",
        "factories, shops, farms, schools and industrial visits",
        "conversations with workers and members of the public",
        "Cabinet, parliamentary, desk-work and behind-the-scenes working scenes",
        "international diplomacy with source-identified Reagan, Gorbachev and Cold War context",
        "Falklands, defence, European Council and Bruges-era settings",
        "warm, humorous, laughing, informal and later-life book-event scenes",
        "group photographs and high visual-energy compositions",
    ]
    profile = {
        "schema_version": SCHEMA_VERSION,
        "baseline_schema_version": 3,
        "baseline_image_count": len(rows),
        "baseline_hash": sha256_value(baseline),
        "counts": counts,
        "quality": {
            "minimum": min(qualities), "maximum": max(qualities),
            "median": statistics.median(qualities),
            "mean": round(statistics.mean(qualities), 2),
        },
        "historical_specificity": _count(specificity),
        "observed_concentrations": {
            "formal_portrait_scene_assignments": formal_scenes,
            "indoor_images": counts["location_type"].get("indoor", 0),
            "one_person_images": counts["people_count"].get("one", 0),
            "low_energy_images": counts["visual_energy"].get("low", 0),
            "serious_tone_assignments": counts["tone"].get("serious", 0),
            "authoritative_tone_assignments": counts["tone"].get("authoritative", 0),
        },
        "search_priorities": priorities,
        "generated_at": utc_now(),
    }
    return profile


BRIEF_GROUPS = (
    ("early-career-domestic", "early legal and political career, Finchley constituency work, 1950s-1970s campaigns, canvassing, Education Secretary school and university visits"),
    ("workers-industry-public", "factories, workshops, shops, farms, industrial visits, conversations with workers, families and members of the public, active outdoor scenes"),
    ("government-working-scenes", "Cabinet, parliamentary, dispatch-box, desk work, press conferences, policy meetings, election headquarters and behind-the-scenes working scenes"),
    ("cold-war-diplomacy", "Reagan, Gorbachev, NATO, Cold War diplomacy, Washington, Moscow and other source-identified international meetings"),
    ("defence-europe-rallies", "Falklands and defence settings, European Council and Bruges-era events, patriotic scenes, crowds, rallies and forceful public speaking"),
    ("informal-later-life", "warm or laughing expressions, informal moments, group photographs, later-life speeches, memoir and book events, high-energy or visually distinctive scenes"),
)


def build_discovery_briefs(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Build discovery briefs."""
    baseline_hash = str(profile["baseline_hash"])
    result = []
    for index, (brief_id, focus) in enumerate(BRIEF_GROUPS, 1):
        core = {
            "brief_id": brief_id,
            "order": index,
            "focus": focus,
            "baseline_hash": baseline_hash,
            "preferred_publishers": [
                "Margaret Thatcher Foundation", "Wikimedia Commons", "UK Parliament",
                "UK Government or National Archives", "US presidential libraries", "NARA",
                "NATO or institutional archives", "universities", "Flickr Commons institutions",
            ],
            "avoid_publishers": ["Getty", "Alamy", "Pinterest", "wallpaper sites", "unattributed blogs"],
        }
        result.append({**core, "brief_hash": sha256_value(core)})
    return result


def write_coverage_outputs(baseline_path: Path, research_dir: Path) -> dict[str, Any]:
    """Write coverage outputs."""
    assert_isolated_path(research_dir, research_dir / "coverage_profile.json")
    baseline = load_baseline(baseline_path)
    profile = build_coverage_profile(baseline)
    briefs = build_discovery_briefs(profile)
    research_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(research_dir / "coverage_profile.json", profile)
    atomic_write_json(research_dir / "discovery_briefs.json", {
        "schema_version": SCHEMA_VERSION, "brief_count": len(briefs), "briefs": briefs,
    })
    lines = [
        "# Existing image coverage profile", "",
        f"- Baseline: schema v3, {profile['baseline_image_count']} unique images",
        f"- Baseline hash: `{profile['baseline_hash']}`", "",
        "## Concentrations", "",
    ]
    lines.extend(f"- {key.replace('_', ' ')}: {value}" for key, value in profile["observed_concentrations"].items())
    lines.extend(["", "## Discovery priorities", ""])
    lines.extend(f"{index}. {value}" for index, value in enumerate(profile["search_priorities"], 1))
    atomic_write_text(research_dir / "coverage_profile.md", "\n".join(lines) + "\n")
    return profile


def _single_json_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object found")
    depth = 0
    quoted = escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    raise ValueError("truncated JSON object")


def _repair_single_missing_outer_brace(text: str) -> str:
    stack: list[str] = []
    quoted = escaped = False
    pairs = {"}": "{", "]": "["}
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack or stack.pop() != pairs[char]:
                raise ValueError("mismatched JSON delimiters")
    if quoted or stack != ["{"] or not text.lstrip().startswith("{"):
        raise ValueError("truncated JSON is not missing only the outer object brace")
    return text + "}"


def parse_json_response(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Parse JSON response."""
    repairs: list[str] = []
    parsed = raw.get("parsed")
    if isinstance(parsed, dict):
        return dict(parsed), repairs
    text = "".join(
        str(part.get("text") or "")
        for candidate in raw.get("candidates") or []
        for part in (candidate.get("content") or {}).get("parts") or []
        if isinstance(part, dict)
    ).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, count=1, flags=re.I)
        text = re.sub(r"\s*```$", "", text, count=1)
        repairs.append("stripped_markdown_fence")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        try:
            isolated = _single_json_object(text)
        except ValueError:
            text = _repair_single_missing_outer_brace(text)
            repairs.append("appended_single_missing_outer_object_brace")
            isolated = _single_json_object(text)
        if isolated != text:
            repairs.append("isolated_single_json_object")
        repaired = re.sub(r",\s*([}\]])", r"\1", isolated)
        if repaired != isolated:
            repairs.append("removed_trailing_comma")
        value = json.loads(repaired)
    if not isinstance(value, dict):
        raise ValueError("Gemini response must contain one JSON object")
    return value, repairs


def _usage_and_cost(raw: dict[str, Any], grounding: dict[str, Any]) -> tuple[dict[str, int], dict[str, float]]:
    meta = raw.get("usageMetadata") or raw.get("usage_metadata") or {}
    usage = {
        "input_tokens": int(meta.get("promptTokenCount") or meta.get("prompt_token_count") or 0),
        "cached_tokens": int(meta.get("cachedContentTokenCount") or meta.get("cached_content_token_count") or 0),
        "output_tokens": int(meta.get("candidatesTokenCount") or meta.get("candidates_token_count") or 0)
        + int(meta.get("thoughtsTokenCount") or meta.get("thoughts_token_count") or 0),
    }
    price = PRICES["gemini"]
    token_cost = (
        (usage["input_tokens"] - usage["cached_tokens"]) * price["input"]
        + usage["cached_tokens"] * price["cached_input"]
        + usage["output_tokens"] * price["output"]
    ) / 1_000_000
    search_cost = len(grounding.get("queries") or []) * SEARCH_QUERY_PRICE
    return usage, {
        "token_cost_usd": round(token_cost, 8),
        "search_cost_usd_conservative": round(search_cost, 8),
        "cost_usd": round(token_cost + search_cost, 8),
    }


def _error_details(exc: BaseException) -> dict[str, Any]:
    response = getattr(exc, "response", None)
    code = getattr(exc, "code", None) or getattr(response, "status_code", None)
    try:
        code = int(code) if code is not None else None
    except (TypeError, ValueError):
        code = None
    retry_after = None
    headers = getattr(response, "headers", {}) or {}
    try:
        retry_after = min(60.0, max(0.0, float(headers.get("Retry-After"))))
    except (TypeError, ValueError):
        pass
    return {
        "http_status": code,
        "retry_after": retry_after,
        "error_type": type(exc).__name__,
        "error": str(exc)[:2000],
        "error_body": str(getattr(response, "text", "") or "")[:8000] or None,
        "is_429": code == 429,
        "is_transient": isinstance(exc, (requests.Timeout, requests.ConnectionError))
        or code == 408 or (code is not None and 500 <= code <= 599),
    }


class GeminiHuntClient:
    """One Gemini transport with an identical semantic envelope on both backends."""

    def __init__(
        self,
        transport: str,
        *,
        api_key: str | None = None,
        project: str | None = None,
        location: str = "global",
        client: Any | None = None,
        timeout_seconds: float = 300,
    ):
        """Initialise the gemini hunt client."""
        if transport not in {"developer_api", "vertex"}:
            raise ValueError("unsupported Gemini transport")
        self.transport = transport
        self.model = MODEL
        if client is not None:
            self.client = client
        elif transport == "developer_api":
            if not api_key:
                raise RuntimeError("Gemini Developer API key is required for execution")
            self.api_key = api_key
            self.client = genai.Client(vertexai=False, api_key=api_key)
        else:
            if not project:
                raise RuntimeError("GOOGLE_CLOUD_PROJECT is required for Vertex fallback")
            self.client = genai.Client(vertexai=True, project=project, location=location)
        self.timeout_seconds = timeout_seconds

    def settings_signature(self, phase: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Return the settings signature."""
        return {
            "model": self.model,
            "phase": phase,
            "response_mime_type": None if phase == "discovery" else "application/json",
            "response_schema": None,
            "max_output_tokens": MAX_OUTPUT_TOKENS_DISCOVERY if phase == "discovery" else MAX_OUTPUT_TOKENS_TRIAGE,
            "thinking_budget": THINKING_BUDGET,
            "temperature": TEMPERATURE,
            "google_search": phase == "discovery",
        }

    def config(self, phase: str, schema: dict[str, Any]) -> types.GenerateContentConfig:
        """Return the config."""
        return types.GenerateContentConfig(
            response_mime_type=None if phase == "discovery" else "application/json",
            response_json_schema=None,
            max_output_tokens=MAX_OUTPUT_TOKENS_DISCOVERY if phase == "discovery" else MAX_OUTPUT_TOKENS_TRIAGE,
            thinking_config=types.ThinkingConfig(thinking_budget=THINKING_BUDGET),
            temperature=TEMPERATURE,
            tools=[types.Tool(google_search=types.GoogleSearch())] if phase == "discovery" else None,
            http_options=types.HttpOptions(timeout=int(self.timeout_seconds * 1000)),
        )

    def call(
        self,
        *,
        phase: str,
        prompt: str,
        schema: dict[str, Any],
        images: Sequence[tuple[str, bytes, str]] = (),
    ) -> dict[str, Any]:
        """Submit one grounded or multimodal image-hunt request."""
        if self.transport == "developer_api" and phase == "discovery" and not images:
            return self._call_developer_grounded_rest(prompt, schema)
        parts: list[types.Part] = [types.Part(text=prompt)]
        for label, payload, mime_type in images:
            parts.append(types.Part(text=f"Candidate label: {label}"))
            parts.append(types.Part.from_bytes(data=payload, mime_type=mime_type))
        contents = [types.Content(role="user", parts=parts)]
        started = time.monotonic()
        response = self.client.models.generate_content(
            model=self.model, contents=contents, config=self.config(phase, schema),
        )
        elapsed = time.monotonic() - started
        raw = response.model_dump(mode="json", exclude_none=True)
        grounding = extract_grounding(raw) if phase == "discovery" else {
            "queries": [], "sources": [], "supports": [], "search_entry_point": None,
        }
        try:
            parsed, repairs = parse_json_response(raw)
            parse_error = None
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            parsed, repairs = None, []
            parse_error = f"{type(exc).__name__}: {exc}"
        usage, cost = _usage_and_cost(raw, grounding)
        return {
            "raw": raw,
            "parsed": parsed, "parse_error": parse_error,
            "normalisation": repairs,
            "grounding": grounding,
            "usage": usage,
            **cost,
            "latency_seconds": elapsed,
            "request_id": str(getattr(response, "response_id", "") or "") or None,
            "model_version": str(getattr(response, "model_version", "") or "") or None,
        }

    def developer_grounded_payload(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Return the developer grounded payload."""
        del schema  # Grounded search currently rejects transport-enforced JSON Schema.
        return {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "tools": [{"googleSearch": {}}],
            "generationConfig": {
                "maxOutputTokens": MAX_OUTPUT_TOKENS_DISCOVERY,
                "thinkingConfig": {"thinkingBudget": THINKING_BUDGET},
                "temperature": TEMPERATURE,
            },
        }

    def _call_developer_grounded_rest(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
            json=self.developer_grounded_payload(prompt, schema),
            timeout=(20, self.timeout_seconds),
        )
        response.raise_for_status()
        elapsed = time.monotonic() - started
        raw = response.json()
        grounding = extract_grounding(raw)
        try:
            parsed, repairs = parse_json_response(raw)
            parse_error = None
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            parsed, repairs = None, []
            parse_error = f"{type(exc).__name__}: {exc}"
        usage, cost = _usage_and_cost(raw, grounding)
        return {
            "raw": raw, "parsed": parsed, "parse_error": parse_error, "normalisation": repairs,
            "grounding": grounding, "usage": usage, **cost,
            "latency_seconds": elapsed,
            "request_id": response.headers.get("x-request-id") or response.headers.get("request-id"),
            "model_version": raw.get("modelVersion"),
        }


def require_transport_parity(
    developer: GeminiHuntClient, vertex: GeminiHuntClient, phase: str, schema: dict[str, Any],
) -> None:
    """Require transport parity."""
    if developer.settings_signature(phase, schema) != vertex.settings_signature(phase, schema):
        raise RuntimeError(f"Gemini {phase} transport parity failed")


class LogicalCallRouter:
    """Durable sequential Developer-to-Vertex routing for bounded research calls."""

    def __init__(
        self,
        research_dir: Path,
        developer: GeminiHuntClient,
        vertex: GeminiHuntClient | None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        combined_limit: float = COMBINED_COST_LIMIT_USD,
        developer_limit: float = DEVELOPER_COST_LIMIT_USD,
        vertex_limit: float = VERTEX_COST_LIMIT_USD,
    ):
        """Initialise the logical call router."""
        self.research_dir = research_dir
        self.run_id = research_dir.name
        self.developer = developer
        self.vertex = vertex
        self.sleep = sleep
        self.limits = {
            "combined": combined_limit, "developer_api": developer_limit, "vertex": vertex_limit,
        }
        self.state_path = research_dir / "provider_route_state.json"
        existing = read_json(self.state_path) if self.state_path.exists() else {}
        self.state = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "developer_unavailable": bool(existing.get("developer_unavailable")),
            "developer_unavailable_reason": existing.get("developer_unavailable_reason"),
            "developer_unavailable_at": existing.get("developer_unavailable_at"),
            "consecutive_developer_429": int(existing.get("consecutive_developer_429") or 0),
            "direct_to_vertex_count": int(existing.get("direct_to_vertex_count") or 0),
            "known_spend_usd": dict(existing.get("known_spend_usd") or {"developer_api": 0.0, "vertex": 0.0}),
            "completed_logical_calls": dict(existing.get("completed_logical_calls") or {}),
            "ambiguous_logical_calls": dict(existing.get("ambiguous_logical_calls") or {}),
            "updated_at": utc_now(),
        }
        for key in ("developer_api", "vertex"):
            self.state["known_spend_usd"].setdefault(key, 0.0)
        self._save_state()

    def _save_state(self) -> None:
        self.state["updated_at"] = utc_now()
        atomic_write_json(self.state_path, self.state)

    def _logical_call_phases(self) -> dict[str, str]:
        values = {
            str(call_id): str(row.get("phase"))
            for call_id, row in self.state["completed_logical_calls"].items()
        }
        for row in read_jsonl_if_exists(self.research_dir / "provider_attempts.jsonl"):
            if row.get("logical_call_id") and row.get("phase"):
                values.setdefault(str(row["logical_call_id"]), str(row["phase"]))
        return values

    def _check_call_limit(self, phase: str, logical_call_id: str) -> None:
        calls = self._logical_call_phases()
        if logical_call_id in calls:
            return
        counts = Counter(calls.values())
        if len(calls) >= MAX_LOGICAL_CALLS:
            raise RuntimeError("maximum total logical AI calls reached")
        limit = MAX_DISCOVERY_CALLS if phase == "discovery" else MAX_TRIAGE_CALLS
        if counts[phase] >= limit:
            raise RuntimeError(f"maximum {phase} logical AI calls reached")

    def _check_cost(self, transport: str) -> None:
        spend = self.state["known_spend_usd"]
        conservative_next_call = 0.75
        if float(spend[transport]) + conservative_next_call > self.limits[transport]:
            raise RuntimeError(f"{transport} cost ceiling reached")
        if sum(float(value) for value in spend.values()) + conservative_next_call > self.limits["combined"]:
            raise RuntimeError("combined Gemini cost ceiling reached")

    def _persist_attempt(self, row: dict[str, Any]) -> None:
        append_jsonl(self.research_dir / "provider_attempts.jsonl", row)

    def _record_success(
        self, logical_call_id: str, phase: str, prompt_hash: str, image_hashes: list[str],
        transport: str, result: dict[str, Any], attempt: int,
    ) -> dict[str, Any]:
        raw_dir = self.research_dir / ("grounded_discovery/raw" if phase == "discovery" else "triage_batches/raw")
        normal_dir = self.research_dir / ("grounded_discovery/normalised" if phase == "discovery" else "triage_batches/normalised")
        raw_dir.mkdir(parents=True, exist_ok=True)
        normal_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / f"{logical_call_id}.json"
        normal_path = normal_dir / f"{logical_call_id}.json"
        atomic_write_json(raw_path, result["raw"])
        normalised = {
            "logical_call_id": logical_call_id,
            "phase": phase,
            "provider": transport,
            "model": self.developer.model,
            "prompt_hash": prompt_hash,
            "image_hashes": image_hashes,
            "parsed": result["parsed"],
            "parse_error": result.get("parse_error"),
            "grounding": result["grounding"],
            "normalisation": result["normalisation"],
            "usage": result["usage"],
            "cost_usd": result["cost_usd"],
            "latency_seconds": result["latency_seconds"],
            "request_id": result["request_id"],
            "completed_at": utc_now(),
        }
        atomic_write_json(normal_path, normalised)
        self.state["known_spend_usd"][transport] = round(
            float(self.state["known_spend_usd"][transport]) + float(result["cost_usd"]), 8,
        )
        self.state["completed_logical_calls"][logical_call_id] = {
            "phase": phase, "provider": transport, "prompt_hash": prompt_hash,
            "image_hashes": image_hashes, "normalised_path": str(normal_path.relative_to(self.research_dir)),
            "completed_at": normalised["completed_at"], "cost_usd": result["cost_usd"],
        }
        self._save_state()
        append_jsonl(self.research_dir / "logical_calls.jsonl", self.state["completed_logical_calls"][logical_call_id] | {
            "logical_call_id": logical_call_id, "status": "completed",
        })
        return normalised

    def run(
        self,
        *,
        logical_call_id: str,
        phase: str,
        prompt: str,
        schema: dict[str, Any],
        images: Sequence[tuple[str, bytes, str]] = (),
    ) -> dict[str, Any]:
        """Run one logical image-hunt call with persisted provider routing."""
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        image_hashes = [hashlib.sha256(payload).hexdigest() for _label, payload, _mime in images]
        cached = self.state["completed_logical_calls"].get(logical_call_id)
        if cached:
            if cached.get("prompt_hash") != prompt_hash or cached.get("image_hashes") != image_hashes:
                raise RuntimeError(f"logical call cache identity mismatch: {logical_call_id}")
            return read_json(self.research_dir / cached["normalised_path"])
        if logical_call_id in self.state["ambiguous_logical_calls"]:
            raise RuntimeError(
                f"logical call {logical_call_id} has an ambiguous interrupted outcome; automatic replay refused"
            )
        self._check_call_limit(phase, logical_call_id)
        if self.vertex:
            require_transport_parity(self.developer, self.vertex, phase, schema)
        prior_attempts = [
            row for row in read_jsonl_if_exists(self.research_dir / "provider_attempts.jsonl")
            if row.get("logical_call_id") == logical_call_id
        ]
        prior_transport_attempts = Counter(str(row.get("provider")) for row in prior_attempts)
        eligible_failures = Counter(
            str(row.get("provider")) for row in prior_attempts
            if row.get("is_429") or row.get("is_transient")
        )
        transports = ["vertex"] if self.state["developer_unavailable"] else ["developer_api"]
        if transports == ["vertex"]:
            self.state["direct_to_vertex_count"] += 1
            self._save_state()
        transport_attempts = {"developer_api": 0, "vertex": 0}
        while transports:
            transport = transports.pop(0)
            client = self.developer if transport == "developer_api" else self.vertex
            if client is None:
                raise RuntimeError("Vertex fallback required but unavailable")
            if eligible_failures[transport] >= 2:
                raise RuntimeError(f"{transport} bounded retry allowance already exhausted for {logical_call_id}")
            max_attempts = 2 if transport in {"developer_api", "vertex"} else 1
            while transport_attempts[transport] < max_attempts:
                self._check_cost(transport)
                transport_attempts[transport] += 1
                attempt_number = len(prior_attempts) + sum(transport_attempts.values())
                transport_attempt_number = prior_transport_attempts[transport] + transport_attempts[transport]
                started_at = utc_now()
                started = time.monotonic()
                try:
                    result = client.call(phase=phase, prompt=prompt, schema=schema, images=images)
                except (KeyboardInterrupt, SystemExit) as exc:
                    row = {
                        "run_id": self.run_id, "logical_call_id": logical_call_id, "phase": phase,
                        "provider": transport, "model": client.model, "attempt": attempt_number,
                        "transport_attempt": transport_attempt_number, "started_at": started_at,
                        "completed_at": utc_now(), "elapsed_seconds": round(time.monotonic() - started, 3),
                        "http_status": None, "retry_after": None, "prompt_hash": prompt_hash,
                        "response_hash": None, "usage": {}, "known_cost_usd": None,
                        "status": "ambiguous_interruption", "error_type": type(exc).__name__,
                        "error": "operator interruption while request outcome was unknown",
                    }
                    self._persist_attempt(row)
                    self.state["ambiguous_logical_calls"][logical_call_id] = row
                    self._save_state()
                    raise
                except Exception as exc:
                    details = _error_details(exc)
                    row = {
                        "run_id": self.run_id, "logical_call_id": logical_call_id, "phase": phase,
                        "provider": transport, "model": client.model, "attempt": attempt_number,
                        "transport_attempt": transport_attempt_number, "started_at": started_at,
                        "completed_at": utc_now(), "elapsed_seconds": round(time.monotonic() - started, 3),
                        "prompt_hash": prompt_hash, "response_hash": None, "usage": {},
                        "known_cost_usd": None, "status": "429" if details["is_429"] else "failed", **details,
                    }
                    self._persist_attempt(row)
                    if details["is_429"] or details["is_transient"]:
                        eligible_failures[transport] += 1
                    if transport == "developer_api":
                        if details["is_429"]:
                            self.state["consecutive_developer_429"] += 1
                            self._save_state()
                            if self.state["consecutive_developer_429"] >= 2:
                                self.state.update({
                                    "developer_unavailable": True,
                                    "developer_unavailable_reason": "two_consecutive_http_429",
                                    "developer_unavailable_at": utc_now(),
                                })
                                self._save_state()
                                if self.vertex is None:
                                    raise RuntimeError("Developer exhausted and Vertex unavailable") from exc
                                transports.insert(0, "vertex")
                                break
                            if eligible_failures[transport] >= 2:
                                raise RuntimeError(
                                    f"Developer bounded retry allowance exhausted for {logical_call_id}"
                                ) from exc
                            self.sleep(details["retry_after"] if details["retry_after"] is not None else 2.0)
                            continue
                        self.state["consecutive_developer_429"] = 0
                        self._save_state()
                        if details["is_transient"] and eligible_failures[transport] < 2 and transport_attempts[transport] < 2:
                            self.sleep(2.0)
                            continue
                        raise
                    if (details["is_429"] or details["is_transient"]) and eligible_failures[transport] < 2 and transport_attempts[transport] < 2:
                        self.sleep(details["retry_after"] if details["retry_after"] is not None else 2.0)
                        continue
                    raise RuntimeError(f"Vertex fallback stopped after {details['http_status'] or details['error_type']}") from exc
                self.state["consecutive_developer_429"] = 0 if transport == "developer_api" else self.state["consecutive_developer_429"]
                response_hash = sha256_value(result["raw"])
                self._persist_attempt({
                    "run_id": self.run_id, "logical_call_id": logical_call_id, "phase": phase,
                    "provider": transport, "model": client.model, "attempt": attempt_number,
                    "transport_attempt": transport_attempt_number, "started_at": started_at,
                    "completed_at": utc_now(), "http_status": 200, "retry_after": None,
                    "prompt_hash": prompt_hash, "response_hash": response_hash,
                    "usage": result["usage"], "known_cost_usd": result["cost_usd"], "status": "completed",
                })
                return self._record_success(
                    logical_call_id, phase, prompt_hash, image_hashes, transport, result, attempt_number,
                )
        raise RuntimeError(f"logical call did not complete: {logical_call_id}")


def discovery_prompt(brief: dict[str, Any]) -> str:
    """Return the discovery prompt."""
    return f"""You are finding reputable archive SOURCE PAGES containing photographs of Margaret Thatcher.

Research focus: {brief['focus']}

You must execute Google Search before answering. Use several focused queries for the requested focus. If you cannot execute a grounded search, answer only: NO GROUNDED RESULTS.

Find archive catalogue pages, institutional gallery pages, transcript/event pages with an attributed photograph, or Wikimedia Commons file pages. Prefer the Margaret Thatcher Foundation, Wikimedia Commons, UK Parliament, UK government and national archives, presidential libraries, NARA, NATO, universities, and institutional Flickr Commons collections. Do not prioritise Getty, Alamy, paid press agencies, Pinterest, wallpaper sites, social reposts, or unattributed blogs.

Summarise up to 20 distinct source pages likely to yield usable photographs in concise prose. Cite every page through Google Search grounding. Discuss source pages, not invented direct image URLs. Do not identify anyone from facial appearance. Do not infer reuse permission merely because an image is online. The application will construct structured records solely from provider grounding metadata, so unsupported prose and model-written URLs will be ignored."""


def records_from_provider_grounding(grounding: dict[str, Any], brief: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the records from provider grounding."""
    records: list[dict[str, Any]] = []
    queries = [str(value) for value in grounding.get("queries") or []][:6]
    for source in grounding.get("sources") or []:
        supports = [str(value).strip() for value in source.get("supports") or [] if str(value).strip()]
        if not source.get("url") or not supports:
            continue
        title = str(source.get("title") or "Grounded archive source").strip()
        records.append({
            "source_page_url": str(source["url"]),
            "publisher": urlparse(str(source["url"])).hostname or "provider-grounded source",
            "page_title": title,
            "archive_or_collection": "",
            "event_or_period": str(brief["focus"]),
            "approximate_date": "",
            "people_or_context_named_by_source": ["Margaret Thatcher"] if re.search(r"\bThatcher\b", title + " " + " ".join(supports), re.I) else [],
            "caption_or_catalogue_text": " ".join(supports)[:3000],
            "rights_or_licence_hint": "",
            "why_this_source_fills_a_gap": str(brief["focus"]),
            "search_queries_used": queries,
        })
        if len(records) >= 20:
            break
    return records


def triage_prompt(candidate_ids: Sequence[str], gap_priorities: Sequence[str]) -> str:
    """Return the triage prompt."""
    labels = ", ".join(candidate_ids)
    gaps = "; ".join(gap_priorities)
    transport_schema = json.dumps(TRIAGE_RESPONSE_SCHEMA, sort_keys=True, separators=(",", ":"))
    return f"""Assess the attached archive-image candidates for visual and editorial usefulness.

Candidate labels, in attachment order: {labels}
Baseline gaps: {gaps}

Return only one JSON object matching the following schema, with exactly one record for every candidate label:
{transport_schema}

Describe people generically as 'the woman', 'the speaker', 'the primary subject', or 'other people'. Do not identify any person from facial appearance. Source-page evidence established attribution separately and is not part of this visual task. Assess only visible scene, activity, mood, composition, technical quality, X crop suitability, damage, watermarking, distinctiveness and coverage-gap value. Do not reward an image simply because it is historical. Use concise factual descriptions and reject unusably poor or heavily watermarked candidates."""


def _normalise_url(url: str) -> str:
    parsed = urlparse(str(url).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"unsupported URL: {url!r}")
    path = re.sub(r"/{2,}", "/", unquote(parsed.path or "/"))
    query = parsed.query.rstrip("&")
    return parsed._replace(scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower(), path=path, query=query, fragment="").geturl()


def validate_public_url(url: str, *, resolve_dns: bool = True) -> str:
    """Validate public URL."""
    normalised = _normalise_url(url)
    hostname = urlparse(normalised).hostname or ""
    if hostname.casefold() in {"localhost", "localhost.localdomain"}:
        raise ValueError("local URL rejected")
    try:
        address = ipaddress.ip_address(hostname.strip("[]"))
        if not address.is_global:
            raise ValueError("non-public URL rejected")
    except ValueError as exc:
        if "non-public" in str(exc):
            raise
        if resolve_dns:
            for answer in socket.getaddrinfo(hostname, None):
                resolved = ipaddress.ip_address(answer[4][0])
                if not resolved.is_global:
                    raise ValueError("URL resolves to a non-public address")
    return normalised


def _url_equivalent(left: str, right: str) -> bool:
    try:
        lval, rval = urlparse(_normalise_url(left)), urlparse(_normalise_url(right))
    except ValueError:
        return False
    return lval.hostname == rval.hostname and lval.path.rstrip("/") == rval.path.rstrip("/") and lval.query == rval.query


def resolve_grounded_sources(
    grounding: dict[str, Any], session: requests.Session, *, timeout: float = 30,
    resolution_cache: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Resolve grounded sources."""
    resolution_cache = resolution_cache or {}
    resolved: list[dict[str, Any]] = []
    for source in grounding.get("sources") or []:
        if not source.get("supports") or not source.get("url"):
            continue
        row = dict(source)
        row["original_url"] = str(source["url"])
        row["resolved_url"] = None
        row["resolution_error"] = None
        if resolution_cache.get(row["original_url"]):
            row["resolved_url"] = resolution_cache[row["original_url"]]
            row["resolution_from_cache"] = True
            resolved.append(row)
            continue
        try:
            url = validate_public_url(row["original_url"])
            response = session.get(url, timeout=timeout, allow_redirects=True, stream=True)
            response.raise_for_status()
            row["resolved_url"] = validate_public_url(response.url)
            response.close()
        except Exception as exc:
            row["resolution_error"] = f"{type(exc).__name__}: {exc}"[:1000]
        resolved.append(row)
    return resolved


def validate_grounded_discovery_records(
    parsed: dict[str, Any], grounding: dict[str, Any], resolved_sources: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate grounded discovery records."""
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    records = parsed.get("records") if isinstance(parsed, dict) else None
    if not isinstance(records, list):
        raise ValueError("discovery response records must be a list")
    for record in records:
        reason = None
        normalisations: list[str] = []
        if isinstance(record, dict):
            record = dict(record)
            for field in ("people_or_context_named_by_source", "search_queries_used"):
                if isinstance(record.get(field), str) and record[field].strip():
                    record[field] = [record[field].strip()]
                    normalisations.append(f"{field}:string_to_singleton_list")
        errors = sorted(Draft7Validator(DISCOVERY_RECORD_SCHEMA).iter_errors(record), key=lambda error: list(error.path))
        if errors:
            reason = "schema_mismatch"
        else:
            try:
                page_url = validate_public_url(str(record["source_page_url"]), resolve_dns=False)
            except ValueError:
                page_url, reason = "", "invalid_source_page_url"
            linked = None
            if not reason:
                for source in resolved_sources:
                    if _url_equivalent(page_url, str(source.get("original_url") or "")) or _url_equivalent(
                        page_url, str(source.get("resolved_url") or ""),
                    ):
                        linked = source
                        break
                if linked is None:
                    reason = "source_url_not_linked_by_provider_grounding"
            if not reason and not str(record["page_title"]).strip():
                reason = "missing_page_title"
            if not reason and not str(record["why_this_source_fills_a_gap"]).strip():
                reason = "missing_gap_rationale"
        if reason:
            rejected.append({"record": record, "reason": reason})
            continue
        accepted.append({
            **record,
            "source_page_url": page_url,
            "grounding_source_url": linked["original_url"],
            "grounding_resolved_url": linked.get("resolved_url"),
            "grounding_supports": linked.get("supports") or [],
            "local_normalisations": normalisations,
        })
    return accepted, rejected


def _iter_jsonld_images(value: Any, inherited_text: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, list):
        for item in value:
            yield from _iter_jsonld_images(item, inherited_text)
    elif isinstance(value, dict):
        text = " ".join(str(value.get(key) or "") for key in ("name", "caption", "description", "headline")) or inherited_text
        for key in ("contentUrl", "thumbnailUrl"):
            if isinstance(value.get(key), str):
                yield value[key], text
        image = value.get("image")
        if isinstance(image, str):
            yield image, text
        elif image is not None:
            yield from _iter_jsonld_images(image, text)
        for key, item in value.items():
            if key != "image" and isinstance(item, (list, dict)):
                yield from _iter_jsonld_images(item, text)


def extract_image_references(page_url: str, page_html: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Extract image references."""
    soup = BeautifulSoup(page_html, "lxml")
    title = html.unescape((soup.title.string if soup.title and soup.title.string else "").strip())
    description_tag = soup.find("meta", attrs={"name": re.compile("description", re.I)})
    page_description = str(description_tag.get("content") or "").strip() if description_tag else ""
    page_text = " ".join(soup.stripped_strings)
    rights_matches = re.findall(
        r".{0,120}(?:public domain|creative commons|CC[- ]BY|copyright|all rights reserved|open government licence|reuse permitted).{0,180}",
        page_text, flags=re.I,
    )
    references: dict[str, dict[str, Any]] = {}

    def add(raw_url: str, *, origin: str, alt: str = "", caption: str = "", metadata: str = "") -> None:
        if not raw_url or raw_url.startswith(("data:", "javascript:")):
            return
        try:
            url = validate_public_url(urljoin(page_url, html.unescape(raw_url)), resolve_dns=False)
        except ValueError:
            return
        if urlparse(url).path.casefold().endswith((".svg", ".ico")):
            return
        row = references.setdefault(url, {
            "direct_image_url": url, "origins": [], "alt_text": "", "caption": "", "metadata_text": "",
        })
        if origin not in row["origins"]:
            row["origins"].append(origin)
        for key, value in (("alt_text", alt), ("caption", caption), ("metadata_text", metadata)):
            if value and len(value) > len(row[key]):
                row[key] = html.unescape(re.sub(r"\s+", " ", value).strip())[:4000]

    for tag in soup.find_all("img"):
        alt = str(tag.get("alt") or "")
        figure = tag.find_parent("figure")
        caption_tag = figure.find("figcaption") if figure else None
        caption = " ".join(caption_tag.stripped_strings) if caption_tag else ""
        parent_text = " ".join(tag.parent.stripped_strings)[:1000] if tag.parent else ""
        for attribute in ("src", "data-src", "data-original", "data-lazy-src"):
            add(str(tag.get(attribute) or ""), origin=f"img:{attribute}", alt=alt, caption=caption, metadata=parent_text)
        for attribute in ("srcset", "data-srcset"):
            choices = []
            for item in str(tag.get(attribute) or "").split(","):
                fields = item.strip().split()
                if fields:
                    weight = int(re.sub(r"\D", "", fields[1])) if len(fields) > 1 and re.sub(r"\D", "", fields[1]) else 0
                    choices.append((weight, fields[0]))
            if choices:
                add(max(choices)[1], origin=f"img:{attribute}", alt=alt, caption=caption, metadata=parent_text)
        parent_link = tag.find_parent("a")
        if parent_link:
            add(str(parent_link.get("href") or ""), origin="linked_original", alt=alt, caption=caption, metadata=parent_text)
    for property_name in ("og:image", "twitter:image"):
        for tag in soup.find_all("meta", attrs={"property": property_name}) + soup.find_all("meta", attrs={"name": property_name}):
            add(str(tag.get("content") or ""), origin=property_name, metadata=page_description)
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            value = json.loads(script.string or script.get_text())
        except (TypeError, json.JSONDecodeError):
            continue
        for url, metadata in _iter_jsonld_images(value):
            add(url, origin="json_ld", metadata=metadata)
    for link in soup.find_all("a", href=True):
        href = str(link.get("href") or "")
        if re.search(r"\.(?:jpe?g|png|webp)(?:\?|$)", href, re.I) or any(term in href.casefold() for term in ("download", "original", "fullsize")):
            add(href, origin="page_link", metadata=" ".join(link.stripped_strings))
    return {
        "title": title,
        "description": page_description,
        "mentions_margaret_thatcher": bool(re.search(r"\bMargaret\s+Thatcher\b", page_text, re.I)),
        "page_text_excerpt": page_text[:12000],
        "rights_excerpt": " | ".join(rights_matches[:8])[:3000],
    }, list(references.values())


def source_named_people_from_evidence(
    *evidence_values: str, supplied_values: Sequence[str] = (),
) -> list[str]:
    """Extract only explicitly source-named people; never inspect image pixels."""
    source_text = "\n".join(str(value or "") for value in evidence_values)
    supplied_text = "\n".join(str(value or "") for value in supplied_values)
    combined = source_text + "\n" + supplied_text
    names = {
        name for name, patterns in SOURCE_PERSON_ALIASES
        if any(re.search(pattern, combined, flags=re.IGNORECASE) for pattern in patterns)
    }
    if (
        "George H. W. Bush" not in names
        and re.search(r"\bPresident Bush\b", combined, flags=re.IGNORECASE)
        and (
            "George Bush Presidential Library" in combined
            or re.search(r"\b(?:1989|1990|1991|1992|1993)\b", combined)
        )
    ):
        names.add("George H. W. Bush")
    return sorted(names)


def source_identity_evidence(reference: dict[str, Any], page: dict[str, Any], discovery: dict[str, Any]) -> dict[str, Any]:
    """Return the source identity evidence."""
    metadata = " ".join(str(reference.get(key) or "") for key in ("alt_text", "metadata_text"))
    candidates = [("source_caption", str(reference.get("caption") or ""), "high")]
    candidates.append(("archive_record" if "json_ld" in (reference.get("origins") or []) else "source_metadata", metadata, "high"))
    candidates.append(("page_context", " ".join(str(page.get(key) or "") for key in ("title", "description")), "medium"))
    for basis, evidence, confidence in candidates:
        if re.search(r"\b(?:Margaret\s+)?Thatcher\b", evidence, re.I):
            return {
                "identity_basis": basis,
                "identity_evidence": re.sub(r"\s+", " ", evidence).strip()[:1200],
                "identity_confidence": confidence,
                "source_named_people": source_named_people_from_evidence(
                    evidence,
                    str(discovery.get("page_title") or ""),
                    str(discovery.get("archive_or_collection") or ""),
                    str(discovery.get("approximate_date") or ""),
                    supplied_values=discovery.get("people_or_context_named_by_source") or [],
                ),
            }
    return {
        "identity_basis": "page_context", "identity_evidence": "",
        "identity_confidence": "low", "source_named_people": [],
    }


def classify_rights(*values: str, host: str = "") -> tuple[str, str]:
    """Classify rights."""
    evidence = " ".join(value for value in values if value).strip()
    folded = evidence.casefold()
    if any(term in folded for term in ("do not reproduce", "all rights reserved", "rights managed")):
        return "editorial_or_licensed_only", evidence[:1500]
    if any(term in folded for term in ("public domain", "cc0")):
        return "public_domain", evidence[:1500]
    if "creative commons" in folded or re.search(r"\bcc[- ]by\b", folded):
        return "attribution_required", evidence[:1500]
    if any(term in folded for term in ("free to reuse", "open government licence", "reuse permitted")):
        return "clear_reuse", evidence[:1500]
    if any(term in host.casefold() for term in ("getty", "alamy")):
        return "do_not_use", evidence[:1500]
    if "copyright" in folded or "licensed" in folded:
        return "editorial_or_licensed_only", evidence[:1500]
    return "rights_unclear", evidence[:1500]


def publisher_name_for_url(url: str, fallback: str = "") -> str:
    """Return the publisher name for URL."""
    host = (urlparse(url).hostname or "").casefold()
    known = (
        ("commons.wikimedia.org", "Wikimedia Commons"),
        ("margaretthatcher.org", "Margaret Thatcher Foundation"),
        ("reaganlibrary.gov", "Ronald Reagan Presidential Library"),
        ("nationalarchives.gov.uk", "UK National Archives"),
        ("archives.gov", "US National Archives"),
        ("parliament.uk", "UK Parliament"),
        ("nato.int", "NATO"),
    )
    for suffix, label in known:
        if host == suffix or host.endswith(f".{suffix}"):
            return label
    if fallback and "vertexaisearch.cloud.google.com" not in fallback.casefold():
        return fallback
    return host or fallback


def _plain_metadata(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("value") or ""
    text = str(value or "")
    if "<" not in text and ">" not in text:
        return html.unescape(re.sub(r"\s+", " ", text)).strip()
    return html.unescape(" ".join(BeautifulSoup(text, "lxml").stripped_strings)).strip()


def commons_image_candidate(
    page: dict[str, Any], *, grounding_parent_url: str, require_title_attribution: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return the commons image candidate."""
    title = str(page.get("title") or "")
    image_info = (page.get("imageinfo") or [{}])[0]
    mime = str(image_info.get("mime") or "").casefold()
    if mime not in {"image/jpeg", "image/png", "image/webp"}:
        return None
    metadata = image_info.get("extmetadata") or {}
    description = _plain_metadata(metadata.get("ImageDescription"))
    object_name = _plain_metadata(metadata.get("ObjectName"))
    named_title = f"{title} {object_name}"
    if require_title_attribution and not re.search(r"\b(?:Margaret\s+)?Thatcher\b", named_title, re.I):
        return None
    if any(term in named_title.casefold() for term in ("funeral", "grave", "condolence", "begräbnis")):
        return None
    identity_evidence = re.sub(r"\s+", " ", " ".join((title, object_name, description))).strip()
    if not re.search(r"\bMargaret\s+Thatcher\b", identity_evidence, re.I):
        return None
    source_url = str(image_info.get("descriptionurl") or "")
    direct_url = str(image_info.get("thumburl") or image_info.get("url") or "")
    size = int(image_info.get("size") or 0)
    width = int(image_info.get("width") or 0)
    height = int(image_info.get("height") or 0)
    if not image_info.get("thumburl") and (size > MAX_DOWNLOAD_BYTES or max(width, height) > 4000):
        return None
    if not source_url or not direct_url:
        return None
    licence = _plain_metadata(metadata.get("LicenseShortName")) or _plain_metadata(metadata.get("UsageTerms"))
    licence_url = _plain_metadata(metadata.get("LicenseUrl"))
    credit = _plain_metadata(metadata.get("Credit")) or _plain_metadata(metadata.get("Artist"))
    rights_status, rights_evidence = classify_rights(licence, description, host="commons.wikimedia.org")
    candidate_id = hashlib.sha256(f"{source_url}\n{direct_url}".encode("utf-8")).hexdigest()[:20]
    date = _plain_metadata(metadata.get("DateTimeOriginal"))
    page_record = {
        "source_page_url": source_url,
        "grounding_parent_source_page_url": grounding_parent_url,
        "publisher": "Wikimedia Commons",
        "page_title": title.removeprefix("File:"),
        "page_title_observed": object_name or title.removeprefix("File:"),
        "archive_or_collection": "Wikimedia Commons",
        "approximate_date": date,
        "caption_or_catalogue_text": description,
        "rights_or_licence_hint": licence,
        "retrieved_at": utc_now(),
        "content_type": "application/json; MediaWiki API",
        "http_status": 200,
        "image_reference_count": 1,
        "local_normalisations": ["structured_commons_archive_metadata"],
    }
    candidate = {
        "candidate_id": candidate_id,
        "source_page_url": source_url,
        "grounding_parent_source_page_url": grounding_parent_url,
        "direct_image_url": direct_url,
        "publisher": "Wikimedia Commons",
        "page_title": title.removeprefix("File:"),
        "archive_or_collection": "Wikimedia Commons",
        "event_or_period": date,
        "approximate_date": date,
        "caption": description or object_name,
        "alt_text": object_name,
        "credit": credit,
        "licence_text": rights_evidence or licence,
        "grounded_rights_hint": licence,
        "rights_url": licence_url,
        "rights_status": rights_status,
        "identity_basis": "archive_record",
        "identity_evidence": identity_evidence[:1200],
        "identity_confidence": "high",
        "source_named_people": source_named_people_from_evidence(identity_evidence),
        "reference_origins": ["wikimedia_commons_api"],
        "retrieved_at": utc_now(),
    }
    return page_record, candidate


def _commons_api_json(
    session: requests.Session, params: dict[str, Any], *, timeout: float = 45, use_post: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rate_limits: list[dict[str, Any]] = []
    for attempt in (1, 2):
        request = session.post if use_post else session.get
        keyword = "data" if use_post else "params"
        response = request("https://commons.wikimedia.org/w/api.php", **{keyword: params}, timeout=timeout)
        if response.status_code == 429:
            retry_after = min(float(response.headers.get("Retry-After") or 2), 60.0)
            rate_limits.append({"attempt": attempt, "retry_after": retry_after, "timestamp": utc_now()})
            if attempt == 1:
                time.sleep(retry_after)
                continue
        response.raise_for_status()
        return response.json(), rate_limits
    raise RuntimeError("Wikimedia Commons API remained rate limited after one retry")


def expand_grounded_commons_categories(
    research_dir: Path,
    source_pages: list[dict[str, Any]],
    candidates_by_id: dict[str, dict[str, Any]],
    session: requests.Session,
) -> dict[str, Any]:
    """Return the expand grounded commons categories."""
    roots: list[tuple[str, str]] = []
    for row in source_pages:
        url = str(row.get("source_page_url") or "")
        parsed = urlparse(url)
        if parsed.hostname != "commons.wikimedia.org" or not unquote(parsed.path).startswith("/wiki/Category:Margaret_Thatcher"):
            continue
        title = unquote(parsed.path.removeprefix("/wiki/")).replace("_", " ")
        roots.append((title, url))
    roots = sorted(set(roots))
    state_path = research_dir / "commons_expansion_state.json"
    state = read_json(state_path) if state_path.exists() else {
        "schema_version": 1, "completed_categories": [], "queued_categories": [],
        "api_request_count": 0, "rate_limits": [], "download_failures": [],
    }
    completed = set(state.get("completed_categories") or [])
    archive_rate_limit_cycles = sum(
        "ArchiveRateLimitExhausted" in str(row.get("error") or "")
        for row in state.get("download_failures") or []
    )
    archive_recovery_exhausted = archive_rate_limit_cycles >= 2
    if archive_recovery_exhausted:
        state["archive_recovery_exhausted"] = True
        state["archive_recovery_exhausted_reason"] = "two_bounded_rate_limit_cycles"
        state["archive_cooldown_until_epoch"] = None
        atomic_write_json(state_path, state)
    cooldown_until = float(state.get("archive_cooldown_until_epoch") or 0)
    if not archive_recovery_exhausted and cooldown_until > time.time():
        return {
            "categories_processed_now": 0, "candidate_count": len(candidates_by_id),
            "cooldown_active": True, "cooldown_until_epoch": cooldown_until,
        }
    queue: list[tuple[str, str, int]] = [
        (str(row["category"]), str(row["grounding_parent_url"]), int(row["depth"]))
        for row in state.get("queued_categories") or []
    ]
    if not queue:
        queue = [(title, url, 0) for title, url in roots if title not in completed]
    excluded = ("funeral", "grave", "monument", "in art", "coat of arms", "things named", "protests against")
    page_urls = {str(row.get("source_page_url") or "") for row in source_pages}
    downloaded_source_urls = {
        str(row.get("source_page_url") or "") for row in candidates_by_id.values()
        if row.get("local_path") and (research_dir / str(row["local_path"])).is_file()
    }
    source_audit_path = research_dir / "source_pages.json"
    unsupported_records = (
        read_json(source_audit_path).get("rejected_unsupported_records", [])
        if source_audit_path.exists() else []
    )
    processed_now = 0

    def checkpoint() -> None:
        atomic_write_text(
            research_dir / "raw_candidates.jsonl",
            "".join(
                json.dumps(candidates_by_id[key], sort_keys=True, ensure_ascii=False) + "\n"
                for key in sorted(candidates_by_id)
            ),
        )
        atomic_write_json(research_dir / "source_pages.json", {
            "schema_version": SCHEMA_VERSION, "pages": source_pages,
            "rejected_unsupported_records": unsupported_records,
        })
        state["candidate_count"] = len(candidates_by_id)
        state["updated_at"] = utc_now()
        atomic_write_json(state_path, state)

    def ingest_metadata_pages(
        pages: Sequence[dict[str, Any]], grounding_parent: str, *, require_title_attribution: bool = False,
    ) -> bool:
        for page in pages:
            if len(candidates_by_id) >= MAX_DOWNLOADED_CANDIDATES:
                break
            converted = commons_image_candidate(
                page, grounding_parent_url=grounding_parent,
                require_title_attribution=require_title_attribution,
            )
            if converted is None:
                continue
            page_record, candidate = converted
            if candidate["source_page_url"] in downloaded_source_urls:
                continue
            if page_record["source_page_url"] not in page_urls:
                source_pages.append(page_record)
                page_urls.add(page_record["source_page_url"])
            candidate_id = candidate["candidate_id"]
            existing = candidates_by_id.get(candidate_id)
            if existing and existing.get("local_path") and (research_dir / existing["local_path"]).is_file():
                continue
            try:
                recovered = recover_downloaded_candidate(candidate, research_dir)
                candidates_by_id[candidate_id] = recovered or download_candidate(candidate, research_dir, session)
                downloaded_source_urls.add(candidate["source_page_url"])
            except ArchiveRateLimitExhausted as exc:
                state.setdefault("download_failures", []).append({
                    "candidate_id": candidate_id, "source_page_url": candidate.get("source_page_url"),
                    "error": f"{type(exc).__name__}: {exc}", "timestamp": utc_now(),
                })
                state["archive_cooldown_until_epoch"] = time.time() + max(exc.retry_after, 900.0)
                state["archive_cooldown_reason"] = "two_consecutive_image_download_429s"
                checkpoint()
                return False
            except Exception as exc:
                state.setdefault("download_failures", []).append({
                    "candidate_id": candidate_id, "source_page_url": candidate.get("source_page_url"),
                    "error": f"{type(exc).__name__}: {exc}"[:1000], "timestamp": utc_now(),
                })
            finally:
                time.sleep(1.5)
        checkpoint()
        return True

    while queue and len(completed) < MAX_COMMONS_CATEGORIES and len(candidates_by_id) < MAX_DOWNLOADED_CANDIDATES:
        category, grounding_parent, depth = queue.pop(0)
        if category in completed:
            continue
        data, rate_limits = _commons_api_json(session, {
            "action": "query", "format": "json", "formatversion": "2", "list": "categorymembers",
            "cmtitle": category, "cmlimit": "500", "cmtype": "file|subcat",
        })
        state["api_request_count"] = int(state.get("api_request_count") or 0) + 1
        state.setdefault("rate_limits", []).extend(rate_limits)
        members = data.get("query", {}).get("categorymembers", [])
        file_titles = sorted(str(row["title"]) for row in members if int(row.get("ns", -1)) == 6)
        if depth < 3:
            for row in members:
                if int(row.get("ns", -1)) != 14:
                    continue
                subcategory = str(row.get("title") or "")
                folded = subcategory.casefold()
                if "margaret thatcher" not in folded or any(term in folded for term in excluded):
                    continue
                if subcategory not in completed and all(subcategory != queued[0] for queued in queue):
                    queue.append((subcategory, grounding_parent, depth + 1))
        for start in range(0, len(file_titles), 50):
            if len(candidates_by_id) >= MAX_DOWNLOADED_CANDIDATES:
                break
            metadata, rate_limits = _commons_api_json(session, {
                "action": "query", "format": "json", "formatversion": "2", "prop": "imageinfo",
                "titles": "|".join(file_titles[start:start + 50]),
                "iiprop": "url|size|mime|extmetadata", "iiurlwidth": "960",
                "iiextmetadatalanguage": "en",
                "iiextmetadatafilter": "ImageDescription|ObjectName|Credit|Artist|LicenseShortName|UsageTerms|LicenseUrl|DateTimeOriginal",
            }, use_post=True)
            state["api_request_count"] = int(state.get("api_request_count") or 0) + 1
            state.setdefault("rate_limits", []).extend(rate_limits)
            if not ingest_metadata_pages(metadata.get("query", {}).get("pages", []), grounding_parent):
                return {
                    "categories_processed_now": processed_now, "candidate_count": len(candidates_by_id),
                    "cooldown_active": True,
                    "cooldown_until_epoch": state["archive_cooldown_until_epoch"],
                }
        completed.add(category)
        processed_now += 1
        state["completed_categories"] = sorted(completed)
        state["queued_categories"] = [
            {"category": item[0], "grounding_parent_url": item[1], "depth": item[2]} for item in queue
        ]
        state["candidate_count"] = len(candidates_by_id)
        state["updated_at"] = utc_now()
        atomic_write_json(state_path, state)
        time.sleep(0.15)
    search_cycles = int(state.get("archive_search_cycles") or (1 if state.get("archive_search_completed") else 0))
    prior_rate_limited_downloads = sum(
        "429" in str(row.get("error") or "") for row in state.get("download_failures") or []
    )
    should_search = not state.get("archive_search_completed") or (
        search_cycles < 2 and prior_rate_limited_downloads > 0
    )
    if archive_recovery_exhausted:
        should_search = False
    if roots and len(candidates_by_id) < MAX_DOWNLOADED_CANDIDATES and should_search:
        search_titles_path = research_dir / "commons_archive_search_titles.json"
        if search_titles_path.exists():
            search_titles = list(read_json(search_titles_path).get("titles") or [])
        else:
            search, rate_limits = _commons_api_json(session, {
                "action": "query", "format": "json", "formatversion": "2", "list": "search",
                "srsearch": '"Margaret Thatcher" filetype:bitmap', "srnamespace": "6", "srlimit": "500",
            })
            state["api_request_count"] = int(state.get("api_request_count") or 0) + 1
            state.setdefault("rate_limits", []).extend(rate_limits)
            search_titles = sorted(str(row["title"]) for row in search.get("query", {}).get("search", []))
            atomic_write_json(search_titles_path, {"schema_version": 1, "titles": search_titles})
        grounding_parent = roots[0][1]
        search_offset = int(state.get("archive_search_next_offset") or 0)
        for start in range(search_offset, len(search_titles), 50):
            if len(candidates_by_id) >= MAX_DOWNLOADED_CANDIDATES:
                break
            metadata, rate_limits = _commons_api_json(session, {
                "action": "query", "format": "json", "formatversion": "2", "prop": "imageinfo",
                "titles": "|".join(search_titles[start:start + 50]),
                "iiprop": "url|size|mime|extmetadata", "iiurlwidth": "960",
                "iiextmetadatalanguage": "en",
                "iiextmetadatafilter": "ImageDescription|ObjectName|Credit|Artist|LicenseShortName|UsageTerms|LicenseUrl|DateTimeOriginal",
            }, use_post=True)
            state["api_request_count"] = int(state.get("api_request_count") or 0) + 1
            state.setdefault("rate_limits", []).extend(rate_limits)
            if not ingest_metadata_pages(
                metadata.get("query", {}).get("pages", []), grounding_parent,
                require_title_attribution=True,
            ):
                state["archive_search_next_offset"] = start
                checkpoint()
                return {
                    "categories_processed_now": processed_now, "candidate_count": len(candidates_by_id),
                    "cooldown_active": True,
                    "cooldown_until_epoch": state["archive_cooldown_until_epoch"],
                }
            state["archive_search_next_offset"] = start + 50
            checkpoint()
        state["archive_search_completed"] = True
        state["archive_search_cycles"] = search_cycles + 1
        state["archive_search_result_count"] = len(search_titles)
        state["candidate_count"] = len(candidates_by_id)
        state["updated_at"] = utc_now()
        atomic_write_json(state_path, state)
    state["completed_categories"] = sorted(completed)
    state["queued_categories"] = [
        {"category": item[0], "grounding_parent_url": item[1], "depth": item[2]} for item in queue
    ]
    state["candidate_count"] = len(candidates_by_id)
    state["updated_at"] = utc_now()
    atomic_write_json(state_path, state)
    return {
        "categories_processed_now": processed_now, "candidate_count": len(candidates_by_id),
        "cooldown_active": False, "cooldown_until_epoch": None,
    }


def fetch_source_page(
    record: dict[str, Any], session: requests.Session, *, timeout: float = 30,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fetch source page."""
    url = validate_public_url(record["source_page_url"])
    response = session.get(url, timeout=timeout, allow_redirects=True)
    response.raise_for_status()
    final_url = validate_public_url(response.url)
    content_type = str(response.headers.get("Content-Type") or "")
    if "html" not in content_type.casefold() and not response.text.lstrip().startswith(("<!DOCTYPE", "<html", "<HTML")):
        raise ValueError("source page is not HTML")
    if len(response.content) > 5 * 1024 * 1024:
        raise ValueError("source page exceeds 5 MiB")
    page, references = extract_image_references(final_url, response.text)
    page_record = {
        **record, "requested_url": url, "source_page_url": final_url,
        "http_status": response.status_code, "content_type": content_type,
        "retrieved_at": utc_now(), "page_title_observed": page["title"],
        "image_reference_count": len(references),
    }
    candidates: list[dict[str, Any]] = []
    for reference in references:
        identity = source_identity_evidence(reference, page, record)
        rights_status, rights_evidence = classify_rights(
            str(reference.get("caption") or ""), str(reference.get("metadata_text") or ""),
            page["description"], page["rights_excerpt"], host=urlparse(final_url).hostname or "",
        )
        candidate_id = hashlib.sha256(
            f"{final_url}\n{reference['direct_image_url']}".encode("utf-8")
        ).hexdigest()[:20]
        candidates.append({
            "candidate_id": candidate_id,
            "source_page_url": final_url,
            "direct_image_url": reference["direct_image_url"],
            "publisher": publisher_name_for_url(final_url, str(record.get("publisher") or "")),
            "page_title": page["title"] or record.get("page_title") or "",
            "archive_or_collection": record.get("archive_or_collection") or "",
            "event_or_period": record.get("event_or_period") or "",
            "approximate_date": record.get("approximate_date") or "",
            "caption": reference.get("caption") or "",
            "alt_text": reference.get("alt_text") or "",
            "credit": "",
            "licence_text": rights_evidence,
            "grounded_rights_hint": record.get("rights_or_licence_hint") or "",
            "rights_url": "",
            "rights_status": rights_status,
            **identity,
            "reference_origins": reference.get("origins") or [],
            "retrieved_at": utc_now(),
        })
    confidence_order = {"high": 0, "medium": 1, "low": 2}
    candidates.sort(key=lambda row: (
        confidence_order.get(row["identity_confidence"], 3),
        0 if "linked_original" in row["reference_origins"] else 1,
        row["candidate_id"],
    ))
    return page_record, candidates[:60]


def download_candidate(
    candidate: dict[str, Any], research_dir: Path, session: requests.Session, *, timeout: float = 45,
) -> dict[str, Any]:
    """Download candidate."""
    if candidate.get("identity_confidence") == "low" or not candidate.get("identity_evidence"):
        raise ValueError("candidate attribution is not source-verified")
    url = validate_public_url(candidate["direct_image_url"])
    response = None
    for attempt in (1, 2):
        response = session.get(url, timeout=timeout, stream=True, allow_redirects=True)
        if response.status_code == 429 and attempt == 1:
            retry_after = min(float(response.headers.get("Retry-After") or 5), 60.0)
            response.close()
            time.sleep(retry_after)
            continue
        if response.status_code == 429:
            retry_after = min(float(response.headers.get("Retry-After") or 5), 60.0)
            response.close()
            raise ArchiveRateLimitExhausted(retry_after)
        response.raise_for_status()
        break
    assert response is not None
    final_url = validate_public_url(response.url)
    content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].casefold()
    payload = bytearray()
    for chunk in response.iter_content(1024 * 128):
        payload.extend(chunk)
        if len(payload) > MAX_DOWNLOAD_BYTES:
            raise ValueError("image exceeds download-size limit")
    if payload[:100].lstrip().startswith((b"<", b"<!")):
        raise ValueError("HTML response masquerades as an image")
    try:
        with Image.open(__import__("io").BytesIO(payload)) as image:
            image.verify()
        with Image.open(__import__("io").BytesIO(payload)) as image:
            image.load()
            image_format = str(image.format or "").upper()
            width, height = image.size
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("downloaded payload is not a decodable image") from exc
    if image_format not in {"JPEG", "PNG", "WEBP"}:
        raise ValueError(f"unsupported decoded image format: {image_format}")
    if max(width, height) < MIN_RETAINED_LONG_EDGE:
        raise ValueError("only a tiny thumbnail was available")
    suffix = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}[image_format]
    output = research_dir / "downloaded" / f"{candidate['candidate_id']}{suffix}"
    assert_isolated_path(research_dir, output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    temporary.write_bytes(bytes(payload))
    os.replace(temporary, output)
    return {
        **candidate,
        "direct_image_url": final_url,
        "local_path": str(output.relative_to(research_dir)),
        "http_content_type": content_type,
        "width": width, "height": height,
        "source_file_size": len(payload),
        "image_format": image_format,
        "image_sha256": hashlib.sha256(payload).hexdigest(),
        "below_preferred_resolution": max(width, height) < MIN_PREFERRED_LONG_EDGE,
        "downloaded_at": utc_now(),
    }


def recover_downloaded_candidate(candidate: dict[str, Any], research_dir: Path) -> dict[str, Any] | None:
    """Recover downloaded candidate."""
    matches = sorted((research_dir / "downloaded").glob(f"{candidate['candidate_id']}.*"))
    for path in matches:
        try:
            with Image.open(path) as image:
                image.load()
                image_format = str(image.format or "").upper()
                width, height = image.size
        except (UnidentifiedImageError, OSError):
            continue
        if image_format not in {"JPEG", "PNG", "WEBP"}:
            continue
        payload_hash = sha256_file(path)
        return {
            **candidate,
            "local_path": str(path.relative_to(research_dir)),
            "http_content_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "width": width, "height": height, "source_file_size": path.stat().st_size,
            "image_format": image_format, "image_sha256": payload_hash,
            "below_preferred_resolution": max(width, height) < MIN_PREFERRED_LONG_EDGE,
            "downloaded_at": utc_now(), "recovered_from_existing_file": True,
        }
    return None


def harvest_saved_discovery(
    research_dir: Path, *, session: requests.Session | None = None,
) -> dict[str, Any]:
    """Return the harvest saved discovery."""
    session = session or requests.Session()
    session.headers.update({"User-Agent": "mrsMThatcher-image-research/1.0 (archive research)"})
    normal_dir = research_dir / "grounded_discovery" / "normalised"
    normalised = [read_json(path) for path in sorted(normal_dir.glob("discovery-*.json"))]
    existing_pages_file = research_dir / "source_pages.json"
    existing_pages = read_json(existing_pages_file).get("pages", []) if existing_pages_file.exists() else []
    source_pages: list[dict[str, Any]] = list(existing_pages)
    fetched_urls = {str(row.get("source_page_url")) for row in source_pages}
    resolution_cache = {
        str(row["grounding_source_url"]): str(row["grounding_resolved_url"])
        for row in source_pages
        if row.get("grounding_source_url") and row.get("grounding_resolved_url")
    }
    rejected_records: list[dict[str, Any]] = []
    candidates_by_id: dict[str, dict[str, Any]] = {
        row["candidate_id"]: row for row in read_jsonl_if_exists(research_dir / "raw_candidates.jsonl")
    }
    for row in candidates_by_id.values():
        row["publisher"] = publisher_name_for_url(
            str(row.get("source_page_url") or ""), str(row.get("publisher") or ""),
        )
    failures: list[dict[str, Any]] = []
    for call in normalised:
        if not isinstance(call.get("parsed"), dict) or not isinstance(call["parsed"].get("records"), list):
            rejected_records.append({
                "logical_call_id": call.get("logical_call_id"),
                "record": None, "reason": "provider_response_unusable",
                "parse_error": call.get("parse_error"),
            })
            continue
        resolved = resolve_grounded_sources(
            call.get("grounding") or {}, session, resolution_cache=resolution_cache,
        )
        accepted, rejected = validate_grounded_discovery_records(
            call.get("parsed") or {}, call.get("grounding") or {}, resolved,
        )
        rejected_records.extend({"logical_call_id": call.get("logical_call_id"), **row} for row in rejected)
        for record in accepted:
            if record["source_page_url"] in fetched_urls or record.get("grounding_resolved_url") in fetched_urls:
                continue
            try:
                page, references = fetch_source_page(record, session)
                source_pages.append(page)
                fetched_urls.add(page["source_page_url"])
            except Exception as exc:
                failures.append({
                    "stage": "source_page", "source_page_url": record.get("source_page_url"),
                    "error": f"{type(exc).__name__}: {exc}", "timestamp": utc_now(),
                })
                continue
            for reference in references:
                if len(candidates_by_id) >= MAX_DOWNLOADED_CANDIDATES:
                    break
                candidate_id = reference["candidate_id"]
                existing = candidates_by_id.get(candidate_id)
                if existing and existing.get("local_path") and (research_dir / existing["local_path"]).is_file():
                    continue
                try:
                    downloaded = download_candidate(reference, research_dir, session)
                    candidates_by_id[candidate_id] = downloaded
                except Exception as exc:
                    failures.append({
                        "stage": "image_download", "candidate_id": candidate_id,
                        "source_page_url": reference.get("source_page_url"),
                        "direct_image_url": reference.get("direct_image_url"),
                        "error": f"{type(exc).__name__}: {exc}", "timestamp": utc_now(),
                    })
    commons = expand_grounded_commons_categories(research_dir, source_pages, candidates_by_id, session)
    commons_state_path = research_dir / "commons_expansion_state.json"
    if commons_state_path.exists():
        failures.extend({"stage": "commons_image_download", **row} for row in read_json(commons_state_path).get("download_failures", []))
    candidates = [candidates_by_id[key] for key in sorted(candidates_by_id)]
    atomic_write_json(research_dir / "source_pages.json", {
        "schema_version": SCHEMA_VERSION, "pages": source_pages,
        "rejected_unsupported_records": rejected_records,
    })
    atomic_write_text(
        research_dir / "raw_candidates.jsonl",
        "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in candidates),
    )
    atomic_write_text(
        research_dir / "download_failures.jsonl",
        "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in failures),
    )
    return {
        "grounded_calls": len(normalised), "accepted_source_pages": len(source_pages),
        "unsupported_source_records": len(rejected_records), "downloaded_candidates": len(candidates),
        "failures": len(failures), "commons_categories_processed_now": commons["categories_processed_now"],
        "commons_cooldown_active": bool(commons.get("cooldown_active")),
        "commons_cooldown_until_epoch": commons.get("cooldown_until_epoch"),
    }


def _image_fingerprints(path: Path) -> dict[str, Any]:
    with Image.open(path) as source:
        image = source.convert("RGB")
        return {
            "sha256": sha256_file(path),
            "phash": _local_phash(image),
            "dhash": _local_dhash(image),
            "width": image.width,
            "height": image.height,
            "aspect_ratio": round(image.width / max(image.height, 1), 6),
            "greyscale": image.convert("L").getextrema()[0] == image.convert("L").getextrema()[1],
        }


def _local_phash(image: Image.Image, *, hash_size: int = 8, highfreq_factor: int = 4) -> str:
    """Compute pHash without SciPy, whose system build may not match project NumPy."""
    size = hash_size * highfreq_factor
    pixels = np.asarray(image.convert("L").resize((size, size), LANCZOS), dtype=np.float64)
    positions = np.arange(size, dtype=np.float64)
    frequencies = np.arange(hash_size, dtype=np.float64)[:, None]
    basis = np.cos((np.pi / size) * (positions + 0.5) * frequencies)
    basis[0] *= 1 / np.sqrt(2)
    basis *= np.sqrt(2 / size)
    coefficients = basis @ pixels @ basis.T
    median = np.median(coefficients.flatten()[1:])
    return str(imagehash.ImageHash(coefficients > median))


def _local_dhash(image: Image.Image, *, hash_size: int = 8) -> str:
    pixels = np.asarray(
        image.convert("L").resize((hash_size + 1, hash_size), LANCZOS), dtype=np.int16,
    )
    return str(imagehash.ImageHash(pixels[:, 1:] > pixels[:, :-1]))


def _hash_distance(left: str, right: str) -> int:
    return imagehash.hex_to_hash(left) - imagehash.hex_to_hash(right)


def classify_image_similarity(candidate: dict[str, Any], other: dict[str, Any]) -> str:
    """Classify image similarity."""
    if candidate["sha256"] == other["sha256"]:
        return "exact_duplicate"
    phash = _hash_distance(candidate["phash"], other["phash"])
    dhash = _hash_distance(candidate["dhash"], other["dhash"])
    aspect_delta = abs(float(candidate["aspect_ratio"]) - float(other["aspect_ratio"]))
    if phash <= 4 and dhash <= 5 and aspect_delta <= 0.08:
        return "near_duplicate"
    if phash <= 7 and aspect_delta > 0.08:
        return "probable_crop"
    if phash <= 8 and dhash <= 10:
        return "alternate_scan"
    return "visually_distinct"


def _baseline_image_path(baseline_path: Path, filename: str) -> Path:
    for path in (baseline_path.parent / "images" / filename, baseline_path.parent / filename):
        if path.is_file():
            return path
    raise FileNotFoundError(f"baseline image unavailable: {filename}")


def build_dedupe_manifest(baseline_path: Path, research_dir: Path) -> dict[str, Any]:
    """Build dedupe manifest."""
    baseline = load_baseline(baseline_path)
    existing: list[dict[str, Any]] = []
    for filename, digest in sorted(baseline["path_index"].items()):
        fingerprints = _image_fingerprints(_baseline_image_path(baseline_path, filename))
        if fingerprints["sha256"] != digest:
            raise RuntimeError(f"baseline image hash changed: {filename}")
        existing.append({"filename": filename, **fingerprints})
    candidates = read_jsonl_if_exists(research_dir / "raw_candidates.jsonl")
    rows: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    class_counts: Counter[str] = Counter()
    for candidate in sorted(candidates, key=lambda row: row["candidate_id"]):
        local_path = research_dir / candidate["local_path"]
        fingerprints = _image_fingerprints(local_path)
        comparisons = []
        for other in existing:
            duplicate_class = classify_image_similarity(fingerprints, other)
            if duplicate_class != "visually_distinct":
                comparisons.append({
                    "existing_filename": other["filename"], "class": duplicate_class,
                    "phash_distance": _hash_distance(fingerprints["phash"], other["phash"]),
                    "dhash_distance": _hash_distance(fingerprints["dhash"], other["dhash"]),
                    "existing_dimensions": [other["width"], other["height"]],
                })
        candidate_matches = []
        for prior in accepted:
            duplicate_class = classify_image_similarity(fingerprints, prior["fingerprints"])
            if duplicate_class != "visually_distinct":
                candidate_matches.append({"candidate_id": prior["candidate_id"], "class": duplicate_class})
        best_class = "visually_distinct"
        hierarchy = {"exact_duplicate": 0, "near_duplicate": 1, "probable_crop": 2, "alternate_scan": 3}
        all_matches = [row["class"] for row in comparisons + candidate_matches]
        if all_matches:
            best_class = min(all_matches, key=lambda value: hierarchy[value])
        possible_replacement = False
        if comparisons and best_class in {"near_duplicate", "probable_crop", "alternate_scan"}:
            largest_existing = max(item["existing_dimensions"][0] * item["existing_dimensions"][1] for item in comparisons)
            possible_replacement = fingerprints["width"] * fingerprints["height"] >= largest_existing * 1.5
            if possible_replacement:
                best_class = "possible_higher_quality_replacement"
        retained = best_class in {"visually_distinct", "possible_higher_quality_replacement"}
        row = {
            "candidate_id": candidate["candidate_id"], "local_path": candidate["local_path"],
            "fingerprints": fingerprints, "duplicate_class": best_class,
            "existing_matches": comparisons, "candidate_matches": candidate_matches,
            "possible_replacement": possible_replacement, "retained_for_triage": retained,
        }
        rows.append(row)
        class_counts[best_class] += 1
        if retained:
            accepted.append(row)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "baseline_hash": sha256_value(baseline),
        "candidate_count": len(rows), "retained_count": len(accepted),
        "class_counts": dict(sorted(class_counts.items())), "candidates": rows,
        "generated_at": utc_now(),
    }
    atomic_write_json(research_dir / "dedupe_manifest.json", manifest)
    return manifest


def create_thumbnail(path: Path, output: Path, *, maximum: int = 768) -> tuple[bytes, str]:
    """Create thumbnail."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(path) as source:
        image = source.convert("RGB")
        image.thumbnail((maximum, maximum), LANCZOS)
        temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
        image.save(temporary, "JPEG", quality=88, optimize=True)
        os.replace(temporary, output)
    return output.read_bytes(), "image/jpeg"


def validate_triage_response(parsed: dict[str, Any], expected_ids: Sequence[str]) -> list[dict[str, Any]]:
    """Validate triage response."""
    rows = parsed.get("records") if isinstance(parsed, dict) else None
    if not isinstance(rows, list):
        raise ValueError("triage records must be a list")
    schema_errors = sorted(Draft7Validator(TRIAGE_RESPONSE_SCHEMA).iter_errors(parsed), key=lambda error: list(error.path))
    if schema_errors:
        raise ValueError(f"triage schema validation failed: {schema_errors[0].message}")
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"candidate_id", "visual_analysis", "editorial_value"}:
            raise ValueError("triage record schema mismatch")
        candidate_id = str(row["candidate_id"])
        if candidate_id in by_id:
            raise ValueError(f"duplicate triage candidate: {candidate_id}")
        by_id[candidate_id] = row
    if set(by_id) != set(expected_ids):
        raise ValueError("triage result IDs differ from supplied candidates")
    return [by_id[candidate_id] for candidate_id in expected_ids]


def recover_saved_triage_response(research_dir: Path, normalised_path: Path) -> dict[str, Any]:
    """Recover saved triage response."""
    value = read_json(normalised_path)
    if isinstance(value.get("parsed"), dict):
        return value
    raw_path = research_dir / "triage_batches" / "raw" / normalised_path.name
    if not raw_path.exists():
        return value
    try:
        parsed, repairs = parse_json_response(read_json(raw_path))
    except (ValueError, json.JSONDecodeError):
        return value
    value["parsed"] = parsed
    value["original_parse_error"] = value.get("parse_error")
    value["parse_error"] = None
    value["normalisation"] = list(value.get("normalisation") or []) + [
        f"offline_raw_recovery:{repair}" for repair in repairs
    ]
    atomic_write_json(normalised_path, value)
    return value


def remaining_logical_call_capacity(phases: dict[str, str], phase: str) -> int:
    """Return the remaining logical call capacity."""
    phase_limit = MAX_DISCOVERY_CALLS if phase == "discovery" else MAX_TRIAGE_CALLS
    return max(0, min(phase_limit - Counter(phases.values())[phase], MAX_LOGICAL_CALLS - len(phases)))


def run_triage_batches(
    research_dir: Path, router: LogicalCallRouter, *, maximum_calls: int | None = None,
) -> dict[str, Any]:
    """Run triage batches."""
    candidates = {row["candidate_id"]: row for row in read_jsonl_if_exists(research_dir / "raw_candidates.jsonl")}
    dedupe = read_json(research_dir / "dedupe_manifest.json")
    retained_ids = [row["candidate_id"] for row in dedupe["candidates"] if row["retained_for_triage"]]
    existing_calls = {
        key for key, value in router.state["completed_logical_calls"].items() if value.get("phase") == "triage"
    }
    phases = router._logical_call_phases()
    allowed_calls = remaining_logical_call_capacity(phases, "triage")
    if maximum_calls is not None:
        allowed_calls = min(allowed_calls, maximum_calls)
    calls_made = 0
    analysed: dict[str, dict[str, Any]] = {}
    normal_dir = research_dir / "triage_batches" / "normalised"
    for path in sorted(normal_dir.glob("triage-*.json")):
        value = recover_saved_triage_response(research_dir, path)
        for row in value.get("parsed", {}).get("records", []):
            if isinstance(row, dict) and row.get("candidate_id"):
                analysed[row["candidate_id"]] = row
    pending = [candidate_id for candidate_id in retained_ids if candidate_id not in analysed]
    pending = pending[:max(0, TARGET_REVIEWABLE - len(analysed))]
    profile = read_json(research_dir / "coverage_profile.json")
    for start in range(0, len(pending), TRIAGE_BATCH_SIZE):
        if calls_made >= allowed_calls:
            break
        batch_ids = pending[start:start + TRIAGE_BATCH_SIZE]
        if not batch_ids:
            break
        logical_call_id = f"triage-{len(existing_calls) + calls_made + 1:02d}"
        images = []
        for candidate_id in batch_ids:
            source = research_dir / candidates[candidate_id]["local_path"]
            thumbnail = research_dir / "triage_batches" / "thumbnails" / f"{candidate_id}.jpg"
            payload, mime_type = create_thumbnail(source, thumbnail)
            images.append((candidate_id, payload, mime_type))
        result = router.run(
            logical_call_id=logical_call_id, phase="triage",
            prompt=triage_prompt(batch_ids, profile["search_priorities"]),
            schema=TRIAGE_RESPONSE_SCHEMA, images=images,
        )
        rows = validate_triage_response(result["parsed"], batch_ids)
        analysed.update({row["candidate_id"]: row for row in rows})
        calls_made += 1
    return {"retained": len(retained_ids), "analysed": len(analysed), "calls_made": calls_made}


def _analysis_comparison_text(analysis: dict[str, Any]) -> str:
    setting = analysis.get("setting") or {}
    people = analysis.get("people") or {}
    pairing = analysis.get("pairing") or {}
    historical = analysis.get("historical_context") or {}
    fields = [
        ("description", analysis.get("description")),
        ("scene summary", analysis.get("scene_summary")),
        ("scene types", analysis.get("scene_types")),
        ("setting", [setting.get("location_type"), *(setting.get("details") or [])]),
        ("activities", people.get("primary_subject_activities")),
        ("moods", people.get("primary_subject_moods")),
        ("tone", analysis.get("tone")),
        ("visual energy", analysis.get("visual_energy")),
        ("visible elements", analysis.get("visible_elements")),
        ("visible symbols", analysis.get("visible_symbols") or historical.get("visible_symbols")),
        ("topics", pairing.get("best_for_topics")),
    ]
    values = []
    for label, value in fields:
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value if item)
        if value:
            values.append(f"{label}: {value}")
    return "\n".join(values)


def _candidate_novel_attributes(analysis: dict[str, Any], profile: dict[str, Any]) -> tuple[list[str], list[str]]:
    counts = profile["counts"]
    people = analysis.get("people") or {}
    setting = analysis.get("setting") or {}
    values = {
        "scene_types": analysis.get("scene_types") or [],
        "location_type": [setting.get("location_type")],
        "visual_energy": [analysis.get("visual_energy")],
        "activities": people.get("primary_subject_activities") or [],
        "moods": people.get("primary_subject_moods") or [],
        "people_count": [people.get("count_category")],
        "tone": analysis.get("tone") or [],
    }
    novel = []
    gaps = []
    for dimension, items in values.items():
        for item in items:
            if item and int(counts.get(dimension, {}).get(str(item), 0)) <= 2:
                novel.append(f"{dimension}:{item}")
    if setting.get("location_type") == "outdoor":
        gaps.append("active or outdoor setting")
    if people.get("count_category") in {"small_group", "crowd"}:
        gaps.append("group or crowd composition")
    if analysis.get("visual_energy") == "high":
        gaps.append("high visual energy")
    if any(value in {"warm", "humorous", "laughing", "joyful"} for value in (analysis.get("tone") or []) + (people.get("primary_subject_moods") or [])):
        gaps.append("warm or humorous expression")
    if any(term in " ".join(analysis.get("scene_types") or []).casefold() for term in ("factory", "farm", "school", "campaign", "rally", "working")):
        gaps.append("underrepresented activity or institution")
    return sorted(set(novel)), sorted(set(gaps))


def build_candidate_manifest(
    baseline_path: Path, research_dir: Path, *, embedder: Any | None = None,
) -> dict[str, Any]:
    """Build candidate manifest."""
    baseline = load_baseline(baseline_path)
    profile = read_json(research_dir / "coverage_profile.json")
    raw = {row["candidate_id"]: row for row in read_jsonl_if_exists(research_dir / "raw_candidates.jsonl")}
    dedupe = read_json(research_dir / "dedupe_manifest.json")
    dedupe_rows = {row["candidate_id"]: row for row in dedupe["candidates"]}
    triage: dict[str, dict[str, Any]] = {}
    for path in sorted((research_dir / "triage_batches" / "normalised").glob("triage-*.json")):
        value = read_json(path)
        for row in value.get("parsed", {}).get("records", []):
            triage[str(row["candidate_id"])] = row
    baseline_ids = sorted(baseline["current_hashes"])
    baseline_texts = [_analysis_comparison_text(baseline["items"][digest]["analysis"]) for digest in baseline_ids]
    retained_ids = {
        candidate_id for candidate_id, row in dedupe_rows.items()
        if row.get("retained_for_triage")
    }
    # A candidate can be triaged during the pilot and subsequently become a
    # duplicate when later discovery calls find a better copy.  Final review
    # output must reflect the final dedupe decision, not the earlier snapshot.
    triaged_ids = sorted(set(raw) & set(triage) & retained_ids)
    candidate_texts = [_analysis_comparison_text(triage[candidate_id]["visual_analysis"]) for candidate_id in triaged_ids]
    if triaged_ids:
        embedder = embedder or LocalE5Embedder(DEFAULT_MODEL_DIR)
        baseline_vectors = embedder.encode(baseline_texts, batch_size=16)
        candidate_vectors = embedder.encode(candidate_texts, batch_size=16)
        similarities = candidate_vectors @ baseline_vectors.T
    else:
        similarities = np.empty((0, len(baseline_ids)), dtype=np.float32)
    filename_by_hash = {
        digest: sorted(baseline["items"][digest].get("paths") or [digest])[0] for digest in baseline_ids
    }
    records: list[dict[str, Any]] = []
    for index, candidate_id in enumerate(triaged_ids):
        scores = similarities[index]
        nearest_indices = sorted(range(len(baseline_ids)), key=lambda idx: (-float(scores[idx]), baseline_ids[idx]))[:3]
        novel, gaps = _candidate_novel_attributes(triage[candidate_id]["visual_analysis"], profile)
        top_similarity = float(scores[nearest_indices[0]]) if nearest_indices else 0.0
        redundancy = "high" if top_similarity >= 0.9 else "medium" if top_similarity >= 0.8 else "low"
        nearest = []
        duplicate_by_file = {
            row["existing_filename"]: row["class"] for row in dedupe_rows[candidate_id]["existing_matches"]
        }
        for nearest_index in nearest_indices:
            digest = baseline_ids[nearest_index]
            filename = filename_by_hash[digest]
            nearest.append({
                "filename": filename,
                "semantic_similarity": round(float(scores[nearest_index]), 6),
                "visual_duplicate_class": duplicate_by_file.get(filename, "visually_distinct"),
                "comparison_summary": f"Semantic comparison with {filename}; visual duplicate class {duplicate_by_file.get(filename, 'visually_distinct')}.",
            })
        records.append({
            **raw[candidate_id],
            "dedupe": dedupe_rows[candidate_id],
            **triage[candidate_id],
            "semantic_novelty": {
                "nearest_existing_images": nearest,
                "novel_attributes": novel,
                "coverage_gaps_filled": gaps,
                "editorial_redundancy": redundancy,
                "possible_replacement": bool(dedupe_rows[candidate_id]["possible_replacement"]),
                "embedding_model": E5_MODEL_ID,
            },
            "review_status": "undecided",
        })
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "baseline_hash": sha256_value(baseline),
        "candidate_count": len(records),
        "rights_status_counts": dict(sorted(Counter(row["rights_status"] for row in records).items())),
        "embedding_model": E5_MODEL_ID,
        "records": records,
        "generated_at": utc_now(),
    }
    atomic_write_json(research_dir / "candidate_manifest.json", manifest)
    if not (research_dir / "review_state.json").exists():
        atomic_write_json(research_dir / "review_state.json", {"schema_version": 1, "reviews": {}, "updated_at": utc_now()})
    refresh_review_manifests(research_dir)
    return manifest


REVIEW_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Thatcher image research review</title>
<style>
:root{font-family:system-ui,sans-serif;color:#181818;background:#f4f4f1}*{box-sizing:border-box}body{margin:0}
header{position:sticky;top:0;z-index:3;background:#fff;border-bottom:1px solid #ccc;padding:10px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
main{max-width:1280px;margin:auto;padding:16px}.grid{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(300px,.8fr);gap:16px}
.panel{background:#fff;border:1px solid #ccc;border-radius:6px;padding:14px}.candidate{width:100%;max-height:68vh;object-fit:contain;background:#111}
.comparison{display:grid;grid-template-columns:1fr 1fr;gap:10px}.comparison img{width:100%;max-height:260px;object-fit:contain;background:#222}
h1{font-size:20px;margin:0}h2{font-size:17px;margin:0 0 8px}h3{font-size:15px;margin:16px 0 5px}.muted{color:#666}.bad{color:#9c1515}.good{color:#176326}
.buttons{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:12px 0}.buttons button{min-height:48px;padding:8px;border:1px solid #888;background:#fff;border-radius:5px;font-weight:650}
.buttons button.selected{background:#1f5b3a;color:white;border-color:#1f5b3a}.nav{display:flex;gap:8px}.nav button,select{min-height:42px;padding:7px 12px}
textarea{width:100%;min-height:70px}dl{display:grid;grid-template-columns:140px 1fr;gap:5px 10px;margin:0}dt{font-weight:650}dd{margin:0;overflow-wrap:anywhere}
a{color:#1557a5;overflow-wrap:anywhere}@media(max-width:800px){.grid{grid-template-columns:1fr}.candidate{max-height:55vh}.buttons{grid-template-columns:1fr 1fr}main{padding:10px}}
</style></head><body><header><h1>Image candidate review</h1><span id="progress"></span><select id="filter"><option value="all">All</option><option value="undecided">Undecided</option><option value="keep">Keep</option><option value="maybe">Maybe</option><option value="reject">Rejected</option><option value="replacement">Replacement</option><option value="rights_unclear">Rights unclear</option></select><div class="nav"><button id="prev">Previous</button><button id="next">Next</button></div></header>
<main><div class="grid"><section class="panel"><img id="candidate" class="candidate" alt="Locally stored candidate image"><h2 id="title"></h2><p id="caption"></p><a id="source" target="_blank" rel="noreferrer">Open source page</a><dl id="facts"></dl></section>
<section class="panel"><h2>Editorial assessment</h2><p id="summary"></p><h3>Coverage gaps</h3><p id="gaps"></p><h3>Nearest existing image</h3><div class="comparison"><img id="newSmall" alt="Candidate"><img id="existing" alt="Nearest existing collection image"></div><p id="nearest"></p><div class="buttons" id="buttons"></div><label>Note<textarea id="note"></textarea></label></section></div></main>
<script>
const decisions=[['keep','Keep'],['reject','Reject'],['maybe','Maybe'],['keep_as_replacement','Keep as replacement'],['reject_visual_duplicate','Reject — visual duplicate'],['reject_editorially_redundant','Reject — editorially redundant'],['reject_poor_quality','Reject — poor quality'],['reject_rights_concern','Reject — rights concern'],['reject_attribution_unverified','Reject — attribution unverified']];
let payload=null,list=[],index=0,busy=false;const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function filtered(){const f=document.querySelector('#filter').value;return payload.records.filter(r=>{const d=(payload.reviews[r.candidate_id]||{}).decision||'undecided';if(f==='all')return true;if(f==='undecided')return d==='undecided';if(f==='reject')return d.startsWith('reject');if(f==='replacement')return d==='keep_as_replacement';if(f==='rights_unclear')return r.rights_status==='rights_unclear';return d===f})}
function draw(){list=filtered();if(!list.length){document.querySelector('main').innerHTML='<p>No candidates in this filter.</p>';return}index=Math.max(0,Math.min(index,list.length-1));const r=list[index],v=r.visual_analysis||{},e=r.editorial_value||{},n=r.semantic_novelty||{},review=payload.reviews[r.candidate_id]||{};document.querySelector('#progress').textContent=`${index+1} of ${list.length} · ${payload.reviewed_count}/${payload.records.length} reviewed`;document.querySelector('#candidate').src='/candidate/'+r.candidate_id;document.querySelector('#newSmall').src='/candidate/'+r.candidate_id;document.querySelector('#title').textContent=r.page_title||r.publisher;document.querySelector('#caption').textContent=r.caption||r.alt_text||'No image-specific caption recorded.';const a=document.querySelector('#source');a.href=r.source_page_url;a.textContent=r.publisher+' source page';document.querySelector('#summary').textContent=v.scene_summary||v.description||'No visual summary.';document.querySelector('#gaps').textContent=(n.coverage_gaps_filled||[]).join(', ')||'No specific baseline gap identified.';const near=(n.nearest_existing_images||[])[0];const ex=document.querySelector('#existing');if(near){ex.src='/existing/'+encodeURIComponent(near.filename);ex.style.display='block';document.querySelector('#nearest').textContent=`${near.filename} · similarity ${near.semantic_similarity} · ${n.editorial_redundancy} editorial redundancy`;}else{ex.style.display='none';document.querySelector('#nearest').textContent='No comparison available.'}document.querySelector('#facts').innerHTML=`<dt>Dimensions</dt><dd>${r.width} × ${r.height}; ${r.source_file_size} bytes</dd><dt>Event/period</dt><dd>${esc(r.event_or_period)} ${esc(r.approximate_date)}</dd><dt>Identity basis</dt><dd>${esc(r.identity_basis)} (${esc(r.identity_confidence)}): ${esc(r.identity_evidence)}</dd><dt>Rights</dt><dd>${esc(r.rights_status)}: ${esc(r.licence_text)}</dd><dt>Quality</dt><dd>${esc(e.quality)}; priority ${esc(e.recommended_priority)}</dd>`;document.querySelector('#note').value=review.note||'';document.querySelector('#buttons').innerHTML='';for(const [value,label] of decisions){const b=document.createElement('button');b.textContent=label;b.className=review.decision===value?'selected':'';b.onclick=()=>save(value);document.querySelector('#buttons').appendChild(b)}}
async function save(decision){if(busy)return;busy=true;try{const r=list[index],response=await fetch('/api/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:r.candidate_id,decision,note:document.querySelector('#note').value})});if(!response.ok)throw new Error(await response.text());const state=await response.json();payload.reviews=state.reviews;payload.reviewed_count=state.reviewed_count;draw()}finally{busy=false}}
document.querySelector('#prev').onclick=()=>{index=Math.max(0,index-1);draw()};document.querySelector('#next').onclick=()=>{index=Math.min(list.length-1,index+1);draw()};document.querySelector('#filter').onchange=()=>{index=0;draw()};document.addEventListener('keydown',e=>{if(e.target.tagName==='TEXTAREA')return;if(e.key==='ArrowLeft')document.querySelector('#prev').click();if(e.key==='ArrowRight')document.querySelector('#next').click();if(e.key.toLowerCase()==='k')save('keep');if(e.key.toLowerCase()==='r')save('reject');if(e.key.toLowerCase()==='m')save('maybe');if(e.key.toLowerCase()==='d')save('reject_visual_duplicate');if(e.key.toLowerCase()==='p')save('keep_as_replacement')});
fetch('/api/state').then(r=>r.json()).then(v=>{payload=v;draw()});
</script></body></html>"""


def save_review(research_dir: Path, candidate_id: str, decision: str, note: str = "") -> dict[str, Any]:
    """Save review."""
    if decision not in REVIEW_DECISIONS:
        raise ValueError("invalid review decision")
    manifest = read_json(research_dir / "candidate_manifest.json")
    if candidate_id not in {row["candidate_id"] for row in manifest["records"]}:
        raise ValueError("unknown candidate")
    path = research_dir / "review_state.json"
    state = read_json(path) if path.exists() else {"schema_version": 1, "reviews": {}}
    reviews = state.setdefault("reviews", {})
    previous = reviews.get(candidate_id)
    clean_note = str(note).strip()[:4000]
    if previous and previous.get("decision") == decision and previous.get("note", "") == clean_note:
        return state
    if path.exists():
        atomic_write_json(research_dir / "review_state.backup.json", state)
    revision = int((previous or {}).get("revision") or 0) + 1
    current = {
        "candidate_id": candidate_id, "decision": decision, "note": clean_note,
        "revision": revision, "reviewed_at": utc_now(),
    }
    reviews[candidate_id] = current
    state["updated_at"] = utc_now()
    atomic_write_json(path, state)
    append_jsonl(research_dir / "review_audit.jsonl", {
        "candidate_id": candidate_id, "previous": previous, "new": current,
        "revision": revision, "timestamp": utc_now(),
    })
    refresh_review_manifests(research_dir, state)
    return state


def refresh_review_manifests(research_dir: Path, state: dict[str, Any] | None = None) -> None:
    """Perform the refresh review manifests operation."""
    state = state or (read_json(research_dir / "review_state.json") if (research_dir / "review_state.json").exists() else {"reviews": {}})
    reviews = state.get("reviews") or {}
    kept = [row for row in reviews.values() if row.get("decision") in {"keep", "keep_as_replacement"}]
    rejected = [row for row in reviews.values() if str(row.get("decision") or "").startswith("reject")]
    atomic_write_json(research_dir / "kept_manifest.json", {"schema_version": 1, "records": sorted(kept, key=lambda row: row["candidate_id"])})
    atomic_write_json(research_dir / "rejected_manifest.json", {"schema_version": 1, "records": sorted(rejected, key=lambda row: row["candidate_id"])})


def review_api_state(research_dir: Path) -> dict[str, Any]:
    """Return the review API state."""
    manifest = read_json(research_dir / "candidate_manifest.json")
    state = read_json(research_dir / "review_state.json") if (research_dir / "review_state.json").exists() else {"reviews": {}}
    reviews = state.get("reviews") or {}
    return {
        "records": manifest["records"], "reviews": reviews,
        "reviewed_count": sum(1 for row in reviews.values() if row.get("decision") in REVIEW_DECISIONS),
    }


def make_review_handler(research_dir: Path, baseline_path: Path) -> type[BaseHTTPRequestHandler]:
    """Create review handler."""
    manifest = read_json(research_dir / "candidate_manifest.json")
    candidates = {row["candidate_id"]: row for row in manifest["records"]}
    baseline = load_baseline(baseline_path)
    filenames = set(baseline["path_index"])

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            path = unquote(urlparse(self.path).path)
            if path == "/":
                self._send(200, REVIEW_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/api/state":
                self._send(200, canonical_bytes(review_api_state(research_dir)), "application/json")
                return
            if path.startswith("/candidate/"):
                candidate_id = path.rsplit("/", 1)[-1]
                row = candidates.get(candidate_id)
                if row:
                    file_path = research_dir / row["local_path"]
                    if file_path.is_file() and research_dir.resolve() in file_path.resolve().parents:
                        self._send(200, file_path.read_bytes(), mimetypes.guess_type(file_path.name)[0] or "application/octet-stream")
                        return
            if path.startswith("/existing/"):
                filename = path.rsplit("/", 1)[-1]
                if filename in filenames:
                    file_path = _baseline_image_path(baseline_path, filename)
                    self._send(200, file_path.read_bytes(), mimetypes.guess_type(file_path.name)[0] or "application/octet-stream")
                    return
            self._send(404, b"not found", "text/plain")

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/api/review":
                self._send(404, b"not found", "text/plain")
                return
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 16_384)
                value = json.loads(self.rfile.read(length))
                state = save_review(
                    research_dir, str(value.get("candidate_id") or ""),
                    str(value.get("decision") or ""), str(value.get("note") or ""),
                )
                response = {
                    "reviews": state["reviews"],
                    "reviewed_count": len(state["reviews"]),
                }
                self._send(200, canonical_bytes(response), "application/json")
            except Exception as exc:
                self._send(400, str(exc).encode("utf-8"), "text/plain; charset=utf-8")

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    return Handler


def serve_review(research_dir: Path, baseline_path: Path, host: str, port: int) -> None:
    """Serve review."""
    server = ThreadingHTTPServer((host, port), make_review_handler(research_dir, baseline_path))
    print(f"Review server: http://{host}:{port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()


def export_kept(research_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Export kept."""
    assert_isolated_path(research_dir, output_dir)
    manifest = read_json(research_dir / "candidate_manifest.json")
    state = read_json(research_dir / "review_state.json")
    records = {row["candidate_id"]: row for row in manifest["records"]}
    reviews = state.get("reviews") or {}
    selected_ids = sorted(
        candidate_id for candidate_id, review in reviews.items()
        if candidate_id in records and review.get("decision") in {"keep", "keep_as_replacement", "maybe"}
    )
    audit_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line_number, audit in enumerate(read_jsonl_if_exists(research_dir / "review_audit.jsonl"), start=1):
        candidate_id = str(audit.get("candidate_id") or "")
        if candidate_id:
            audit_by_id[candidate_id].append({
                "line_number": line_number,
                "revision": audit.get("revision"),
                "timestamp": audit.get("timestamp"),
            })

    def export_group(candidate_id: str) -> str:
        decision = reviews[candidate_id]["decision"]
        if decision == "maybe":
            return "maybe"
        rights = records[candidate_id].get("rights_status")
        if rights in {"public_domain", "clear_reuse", "attribution_required"}:
            return "production_ready"
        return "rights_pending"

    def attribution_wording(row: dict[str, Any]) -> str:
        if row.get("rights_status") != "attribution_required":
            return ""
        licence_text = re.sub(r"\s+", " ", str(row.get("licence_text") or "")).strip()
        match = re.search(r"\b(CC(?:0| BY(?:-SA|-NC|-ND|-NC-SA|-NC-ND)?)[ -]?\d(?:\.\d)?)\b", licence_text, re.I)
        licence = match.group(1).upper().replace("CC0 1.0", "CC0 1.0") if match else "the recorded licence"
        credit = re.sub(r"\s+", " ", str(row.get("credit") or row.get("publisher") or "source archive")).strip()
        rights_url = str(row.get("rights_url") or "").strip()
        suffix = f" ({rights_url})" if rights_url else ""
        return f"{credit}. Licensed under {licence}{suffix}."

    export_records: list[dict[str, Any]] = []
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        for group in ("production_ready", "rights_pending", "maybe", "manifests"):
            (temporary / group).mkdir(parents=True)
        for candidate_id in selected_ids:
            row = records[candidate_id]
            source = research_dir / row["local_path"]
            if not source.is_file():
                raise FileNotFoundError(f"selected candidate image is missing: {source}")
            filename = f"candidate-{candidate_id}{source.suffix.lower()}"
            group = export_group(candidate_id)
            destination = temporary / group / filename
            shutil.copy2(source, destination)
            digest = sha256_file(destination)
            expected_digest = str(row.get("image_sha256") or digest)
            if digest != expected_digest:
                raise RuntimeError(f"candidate image hash changed for {candidate_id}")
            audit_rows = audit_by_id.get(candidate_id) or []
            export_records.append({
                "candidate_id": candidate_id,
                "export_group": group,
                "export_relative_path": f"{group}/{filename}",
                "source_page_url": row.get("source_page_url") or "",
                "direct_image_url": row.get("direct_image_url") or "",
                "publisher": row.get("publisher") or "",
                "archive_or_collection": row.get("archive_or_collection") or "",
                "caption": row.get("caption") or "",
                "identity_basis": row.get("identity_basis") or "",
                "identity_evidence": row.get("identity_evidence") or "",
                "identity_confidence": row.get("identity_confidence") or "",
                "source_named_people": row.get("source_named_people") or [],
                "photographer_or_credit": row.get("credit") or "",
                "licence_text": row.get("licence_text") or "",
                "licence_url": row.get("rights_url") or "",
                "rights_status": row.get("rights_status") or "rights_unclear",
                "required_attribution_wording": attribution_wording(row),
                "original_filename": source.name,
                "original_relative_path": row["local_path"],
                "original_sha256": digest,
                "width": row.get("width"),
                "height": row.get("height"),
                "source_file_size": row.get("source_file_size"),
                "review_decision": reviews[candidate_id]["decision"],
                "review": reviews[candidate_id],
                "review_audit_reference": {
                    "path": "review_audit.jsonl",
                    "entries": audit_rows,
                    "latest_revision": reviews[candidate_id].get("revision"),
                },
                "gemini_triage": row.get("visual_analysis") or {},
                "semantic_novelty": row.get("semantic_novelty") or {},
            })
        export_records.sort(key=lambda row: row["candidate_id"])
        groups = {
            group: [row for row in export_records if row["export_group"] == group]
            for group in ("production_ready", "rights_pending", "maybe")
        }
        atomic_write_json(temporary / "manifests/export_manifest.json", {
            "schema_version": 2,
            "authoritative_review_state_sha256": sha256_file(research_dir / "review_state.json"),
            "authoritative_review_audit_sha256": sha256_file(research_dir / "review_audit.jsonl"),
            "candidate_manifest_sha256": sha256_file(research_dir / "candidate_manifest.json"),
            "records": export_records,
        })
        for group, rows in groups.items():
            atomic_write_json(temporary / f"manifests/{group}_manifest.json", {
                "schema_version": 2, "records": rows,
            })
        atomic_write_json(temporary / "manifests/source_and_attribution_manifest.json", {
            "schema_version": 1,
            "records": [{
                key: row[key] for key in (
                    "candidate_id", "export_group", "source_page_url", "direct_image_url", "publisher",
                    "archive_or_collection", "caption", "identity_basis", "identity_evidence",
                    "identity_confidence", "source_named_people", "photographer_or_credit", "licence_text", "licence_url",
                    "rights_status", "required_attribution_wording", "original_sha256", "review_decision",
                    "review_audit_reference",
                )
            } for row in export_records],
        })

        def tree_digest(path: Path) -> str:
            digest = hashlib.sha256()
            for file_path in sorted(item for item in path.rglob("*") if item.is_file()):
                digest.update(str(file_path.relative_to(path)).encode("utf-8"))
                digest.update(b"\0")
                digest.update(file_path.read_bytes())
            return digest.hexdigest()

        reused = output_dir.exists() and tree_digest(output_dir) == tree_digest(temporary)
        if reused:
            shutil.rmtree(temporary)
        else:
            if output_dir.exists():
                archived = output_dir.with_name(f"{output_dir.name}.backup-{int(time.time())}")
                os.replace(output_dir, archived)
            os.replace(temporary, output_dir)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {
        "exported_count": len(selected_ids),
        "production_ready_count": len(groups["production_ready"]),
        "rights_pending_count": len(groups["rights_pending"]),
        "maybe_count": len(groups["maybe"]),
        "reused": reused,
        "output_dir": str(output_dir),
    }


def load_project_environment(path: Path) -> None:
    """Load simple shell-style assignments without displaying or replacing environment secrets."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def api_preflight(research_dir: Path) -> dict[str, Any]:
    """Return the API preflight."""
    developer_configured = bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    location = os.getenv("GOOGLE_CLOUD_LOCATION") or "global"
    adc_path = Path.home() / ".config/gcloud/application_default_credentials.json"
    value = {
        "schema_version": SCHEMA_VERSION,
        "run_id": research_dir.name,
        "model": MODEL,
        "provider_order": ["gemini_developer_api", "vertex_ai_gemini"],
        "developer_configured": developer_configured,
        "vertex_project_configured": bool(project),
        "vertex_location": location,
        "adc_readable": adc_path.is_file() and os.access(adc_path, os.R_OK),
        "same_model_family_required": True,
        "logical_call_limits": {
            "grounded_discovery": MAX_DISCOVERY_CALLS,
            "multimodal_triage": MAX_TRIAGE_CALLS,
            "total": MAX_LOGICAL_CALLS,
        },
        "planned_pilot": {"grounded_discovery_calls": 2, "multimodal_triage_calls": 1},
        "planned_full_maximum": {"grounded_discovery_calls": 6, "multimodal_triage_calls": 6},
        "estimated_cost_usd": {"minimum": 0.75, "expected": 2.5, "maximum": COMBINED_COST_LIMIT_USD},
        "hard_cost_limits_usd": {
            "developer_api": DEVELOPER_COST_LIMIT_USD,
            "vertex": VERTEX_COST_LIMIT_USD,
            "combined": COMBINED_COST_LIMIT_USD,
        },
        "output_directory": str(research_dir),
        "production_image_directory_used": False,
        "generated_at": utc_now(),
    }
    atomic_write_json(research_dir / "api_preflight.json", value)
    return value


def create_clients() -> tuple[GeminiHuntClient, GeminiHuntClient]:
    """Create clients."""
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    location = os.getenv("GOOGLE_CLOUD_LOCATION") or "global"
    developer = GeminiHuntClient("developer_api", api_key=api_key)
    vertex = GeminiHuntClient("vertex", project=project, location=location)
    require_transport_parity(developer, vertex, "discovery", DISCOVERY_RESPONSE_SCHEMA)
    require_transport_parity(developer, vertex, "triage", TRIAGE_RESPONSE_SCHEMA)
    return developer, vertex


def run_discovery_calls(
    research_dir: Path, router: LogicalCallRouter, *, maximum_briefs: int,
) -> dict[str, Any]:
    """Run discovery calls."""
    briefs = read_json(research_dir / "discovery_briefs.json")["briefs"]
    completed_before = set(router.state["completed_logical_calls"])
    made = 0
    records_by_call: dict[str, int] = {}
    for brief in briefs[:maximum_briefs]:
        suffix = f"-{int(brief['order']):02d}"
        prior_valid = None
        for call_id, row in router.state["completed_logical_calls"].items():
            if row.get("phase") != "discovery" or not call_id.endswith(suffix):
                continue
            value = read_json(research_dir / row["normalised_path"])
            if isinstance(value.get("parsed"), dict) and isinstance(value["parsed"].get("records"), list):
                prior_valid = (call_id, value)
                break
        if prior_valid:
            records_by_call[prior_valid[0]] = len(prior_valid[1]["parsed"]["records"])
            continue
        if len(router._logical_call_phases()) >= MAX_LOGICAL_CALLS or Counter(router._logical_call_phases().values())["discovery"] >= MAX_DISCOVERY_CALLS:
            break
        logical_call_id = f"discovery-v{DISCOVERY_SCHEMA_VERSION}-{int(brief['order']):02d}"
        result = router.run(
            logical_call_id=logical_call_id,
            phase="discovery",
            prompt=discovery_prompt(brief),
            schema=DISCOVERY_RESPONSE_SCHEMA,
        )
        provider_records = records_from_provider_grounding(result.get("grounding") or {}, brief)
        if provider_records:
            call_state = router.state["completed_logical_calls"][logical_call_id]
            normalised_path = research_dir / call_state["normalised_path"]
            persisted = read_json(normalised_path)
            persisted["model_output_parse_error"] = persisted.get("parse_error")
            persisted["parsed"] = {"records": provider_records}
            persisted["parse_error"] = None
            persisted["normalisation"] = list(persisted.get("normalisation") or []) + [
                "constructed_records_from_provider_grounding_metadata",
            ]
            atomic_write_json(normalised_path, persisted)
            result = persisted
        parsed = result.get("parsed") if isinstance(result.get("parsed"), dict) else {}
        records_by_call[logical_call_id] = len(parsed.get("records") or [])
        if logical_call_id not in completed_before:
            made += 1
    return {"calls_made": made, "record_counts": records_by_call}


def pilot_gate(harvest: dict[str, Any], dedupe: dict[str, Any]) -> dict[str, Any]:
    """Return the pilot gate."""
    checks = {
        "at_least_15_attributed_downloads": harvest["downloaded_candidates"] >= 15,
        "at_least_10_visually_distinct": dedupe["retained_count"] >= 10,
        "source_extraction_operational": harvest["accepted_source_pages"] > 0,
        "production_isolation": True,
    }
    return {"passed": all(checks.values()), "checks": checks}


def build_final_report(research_dir: Path, baseline_path: Path) -> dict[str, Any]:
    """Build final report."""
    profile = read_json(research_dir / "coverage_profile.json")
    route = read_json(research_dir / "provider_route_state.json") if (research_dir / "provider_route_state.json").exists() else {}
    pages = read_json(research_dir / "source_pages.json") if (research_dir / "source_pages.json").exists() else {"pages": []}
    candidates = read_jsonl_if_exists(research_dir / "raw_candidates.jsonl")
    dedupe = read_json(research_dir / "dedupe_manifest.json") if (research_dir / "dedupe_manifest.json").exists() else {"class_counts": {}, "retained_count": 0}
    manifest = read_json(research_dir / "candidate_manifest.json") if (research_dir / "candidate_manifest.json").exists() else {"records": [], "rights_status_counts": {}}
    review_state = read_json(research_dir / "review_state.json") if (research_dir / "review_state.json").exists() else {"reviews": {}}
    gate = read_json(research_dir / "pilot_gate.json") if (research_dir / "pilot_gate.json").exists() else {"passed": False}
    commons = read_json(research_dir / "commons_expansion_state.json") if (research_dir / "commons_expansion_state.json").exists() else {}
    attempts = read_jsonl_if_exists(research_dir / "provider_attempts.jsonl")
    logical_call_ids = {str(row.get("logical_call_id")) for row in attempts if row.get("logical_call_id")}
    ambiguous_provider_responses = [
        row for row in attempts
        if row.get("error_type") == "ValueError" and "Gemini response must contain" in str(row.get("error"))
    ]
    rejected_sources = pages.get("rejected_unsupported_records") or []
    manifest_records = manifest.get("records") or []
    manifest_by_id = {str(row.get("candidate_id") or ""): row for row in manifest_records}
    reviews = {
        str(candidate_id): row for candidate_id, row in (review_state.get("reviews") or {}).items()
        if str(candidate_id) in manifest_by_id and row.get("decision") in REVIEW_DECISIONS
    }
    decision_counts = dict(sorted(Counter(str(row["decision"]) for row in reviews.values()).items()))
    kept_ids = {
        candidate_id for candidate_id, row in reviews.items()
        if row.get("decision") in {"keep", "keep_as_replacement"}
    }
    kept_rights_status_counts = dict(sorted(Counter(
        str(manifest_by_id[candidate_id].get("rights_status") or "unavailable")
        for candidate_id in kept_ids
    ).items()))
    production_ready_rights = {"public_domain", "clear_reuse", "attribution_required"}
    review_summary = {
        "reviewed_count": len(reviews),
        "unreviewed_count": max(0, len(manifest_records) - len(reviews)),
        "complete": bool(manifest_records) and len(reviews) == len(manifest_records),
        "decision_counts": decision_counts,
        "outcome_counts": {
            "keep": sum(
                count for decision, count in decision_counts.items()
                if decision in {"keep", "keep_as_replacement"}
            ),
            "maybe": decision_counts.get("maybe", 0),
            "reject": sum(count for decision, count in decision_counts.items() if decision.startswith("reject")),
        },
        "note_count": sum(bool(str(row.get("note") or "").strip()) for row in reviews.values()),
        "revision_count": sum(int(row.get("revision") or 0) for row in reviews.values()),
        "kept_rights_status_counts": kept_rights_status_counts,
        "production_ready_kept_count": sum(
            count for status, count in kept_rights_status_counts.items() if status in production_ready_rights
        ),
    }
    value = {
        "baseline_image_count": profile["baseline_image_count"],
        "major_gaps": profile["search_priorities"],
        "gemini_model": MODEL,
        "logical_call_count": len(logical_call_ids),
        "completed_logical_call_count": len(route.get("completed_logical_calls") or {}),
        "developer_attempt_count": sum(row.get("provider") == "developer_api" for row in attempts),
        "vertex_attempt_count": sum(row.get("provider") == "vertex" for row in attempts),
        "failover_occurred": bool(route.get("developer_unavailable")),
        "http_429_attempts": [row for row in attempts if row.get("http_status") == 429],
        "known_spend_usd": route.get("known_spend_usd") or {},
        "ambiguous_unrecorded_response_count": len(ambiguous_provider_responses),
        "estimated_ambiguous_spend_usd": round(len(ambiguous_provider_responses) * 0.03, 2),
        "conservative_ambiguous_exposure_usd": round(len(ambiguous_provider_responses) * 0.75, 2),
        "grounded_source_pages": len(pages.get("pages") or []),
        "unsupported_model_source_records": sum(row.get("reason") == "source_url_not_linked_by_provider_grounding" for row in rejected_sources),
        "downloaded_candidates": len(candidates),
        "dedupe_class_counts": dedupe.get("class_counts") or {},
        "possible_replacements": int((dedupe.get("class_counts") or {}).get("possible_higher_quality_replacement", 0)),
        "triaged_reviewable_candidates": len(manifest.get("records") or []),
        "target_reviewable_candidates": TARGET_REVIEWABLE,
        "target_met": len(manifest.get("records") or []) >= TARGET_REVIEWABLE,
        "rights_status_counts": manifest.get("rights_status_counts") or {},
        "triage_priority_counts": dict(sorted(Counter(
            str((row.get("editorial_value") or {}).get("recommended_priority") or "unavailable")
            for row in manifest.get("records") or []
        ).items())),
        "commons_archive_api_requests": int(commons.get("api_request_count") or 0),
        "commons_archive_api_429_events": len(commons.get("rate_limits") or []),
        "commons_image_429_failures": sum(
            "429" in str(row.get("error") or "") for row in commons.get("download_failures") or []
        ),
        "commons_bounded_rate_limit_cycles": sum(
            "ArchiveRateLimitExhausted" in str(row.get("error") or "")
            for row in commons.get("download_failures") or []
        ),
        "commons_recovery_exhausted": bool(commons.get("archive_recovery_exhausted")),
        "review_url": "http://127.0.0.1:8768",
        "review": review_summary,
        "generated_at": utc_now(),
    }
    lines = [
        f"# {research_dir.name.replace('_', ' ').title()}", "",
        f"- Existing baseline: {value['baseline_image_count']} images (schema v3)",
        f"- Gemini model: `{MODEL}`",
        f"- Attempted logical calls: {value['logical_call_count']}",
        f"- Provider-completed logical calls: {value['completed_logical_call_count']}",
        f"- Developer attempts: {value['developer_attempt_count']}",
        f"- Vertex attempts: {value['vertex_attempt_count']}",
        f"- Developer-to-Vertex failover: {'yes' if value['failover_occurred'] else 'no'}",
        f"- Grounded source pages harvested: {value['grounded_source_pages']}",
        f"- Model source records rejected for absent provider grounding: {value['unsupported_model_source_records']}",
        f"- Images downloaded: {value['downloaded_candidates']}",
        f"- Reviewable triaged candidates: {value['triaged_reviewable_candidates']}",
        f"- Approximate review target: {value['target_reviewable_candidates']} ({'met' if value['target_met'] else 'not reached'})",
        f"- Possible higher-quality replacements: {value['possible_replacements']}",
        f"- Known spend by transport: `{json.dumps(value['known_spend_usd'], sort_keys=True)}`", "",
        f"- Responses lost before the parser checkpoint defect was fixed: {value['ambiguous_unrecorded_response_count']} (estimated spend US${value['estimated_ambiguous_spend_usd']:.2f}; conservative possible exposure up to US${value['conservative_ambiguous_exposure_usd']:.2f})", "",
        "## Coverage priorities", "",
    ]
    lines.extend(f"- {gap}" for gap in value["major_gaps"])
    lines.extend(["", "## Duplicate classes", ""])
    lines.extend(f"- {key}: {count}" for key, count in sorted(value["dedupe_class_counts"].items()))
    lines.extend(["", "## Rights status", ""])
    lines.extend(f"- {key}: {count}" for key, count in sorted(value["rights_status_counts"].items()))
    lines.extend(["", "## Archive harvesting", "",
        f"- Commons API requests: {value['commons_archive_api_requests']}",
        f"- Commons API 429 events: {value['commons_archive_api_429_events']}",
        f"- Commons image-download 429 failures: {value['commons_image_429_failures']}",
        f"- Bounded image rate-limit cycles exhausted: {value['commons_bounded_rate_limit_cycles']}",
        f"- Further automatic archive recovery disabled: {'yes' if value['commons_recovery_exhausted'] else 'no'}",
        "", "## Automated triage priority", "",
    ])
    lines.extend(f"- {key}: {count}" for key, count in sorted(value["triage_priority_counts"].items()))
    if not value["target_met"]:
        lines.extend([
            "", "## Stop condition", "",
            f"Target not reached: {value['triaged_reviewable_candidates']} reviewable candidates were obtained, "
            f"{value['target_reviewable_candidates'] - value['triaged_reviewable_candidates']} below the approximate target.",
            "The run stopped at the 12-logical-call ceiling after two bounded Commons image-rate-limit recovery cycles. "
            "No weak, unattributed or untriaged candidates were added as padding.",
    ])
    if gate.get("passed"):
        if value["review"]["complete"]:
            review_intro = (
                f"The pilot gate passed and all {value['triaged_reviewable_candidates']} locally stored "
                "candidates have been reviewed. To revise a decision, run:"
            )
            closing = (
                "No candidate was added to the production image collection. Human review is complete; "
                "an explicit isolated export remains required."
            )
        else:
            review_intro = (
                f"The pilot gate passed and {value['triaged_reviewable_candidates']} locally stored candidates "
                "are available for human review. Run:"
            )
            closing = (
                "No candidate was added to the production image collection. Human review and an explicit "
                "isolated export remain required."
            )
    else:
        review_intro = (
            "The review interface is implemented and tested, but the current manifest is empty because "
            "the pilot gate failed. Once a future separately authorised grounded discovery run yields "
            "candidates, run:"
        )
        closing = (
            "The pilot gate failed. No candidate was added to the production image collection."
        )
    lines.extend([
        "", "## Review", "",
        f"- Human review complete: {'yes' if value['review']['complete'] else 'no'}",
        f"- Reviewed: {value['review']['reviewed_count']} of {value['triaged_reviewable_candidates']}",
        f"- Keep: {value['review']['outcome_counts']['keep']}",
        f"- Maybe: {value['review']['outcome_counts']['maybe']}",
        f"- Reject: {value['review']['outcome_counts']['reject']}",
        f"- Kept candidates with source-evidenced production-ready rights: {value['review']['production_ready_kept_count']}",
        f"- Kept rights statuses: `{json.dumps(value['review']['kept_rights_status_counts'], sort_keys=True)}`", "",
        review_intro, "",
        "```bash",
        f"python3 thatcher_image_hunt.py serve-review --research-dir image_discovery_research/{research_dir.name} --host 127.0.0.1 --port 8768",
        "```", "",
        "Export only kept candidates to the isolated research export directory:", "",
        "```bash",
        f"python3 thatcher_image_hunt.py export-kept --research-dir image_discovery_research/{research_dir.name} --output-dir image_discovery_research/{research_dir.name}/exported_kept",
        "```", "",
        closing,
    ])
    atomic_write_json(research_dir / "run_summary.json", value)
    atomic_write_text(research_dir / "final_report.md", "\n".join(lines) + "\n")
    return value


def run_pipeline(
    baseline_path: Path, research_dir: Path, *, execute: bool, confirmed_cost: float | None,
) -> dict[str, Any]:
    """Run pipeline."""
    write_coverage_outputs(baseline_path, research_dir)
    preflight = api_preflight(research_dir)
    if not execute:
        return {"status": "preflight_only", "preflight": preflight}
    if confirmed_cost != COMBINED_COST_LIMIT_USD:
        raise RuntimeError(f"execution requires exact --confirm-max-cost-usd {COMBINED_COST_LIMIT_USD:g}")
    if not preflight["developer_configured"] or not preflight["vertex_project_configured"] or not preflight["adc_readable"]:
        raise RuntimeError("Gemini Developer and Vertex fallback preflight must both pass")
    developer, vertex = create_clients()
    router = LogicalCallRouter(research_dir, developer, vertex)
    run_discovery_calls(research_dir, router, maximum_briefs=2)
    harvest = harvest_saved_discovery(research_dir)
    dedupe = build_dedupe_manifest(baseline_path, research_dir)
    if harvest.get("commons_cooldown_active"):
        summary = build_final_report(research_dir, baseline_path)
        return {
            "status": "archive_cooldown", "cooldown_until_epoch": harvest.get("commons_cooldown_until_epoch"),
            "candidate_count": dedupe["retained_count"], "summary": summary,
        }
    gate = pilot_gate(harvest, dedupe)
    atomic_write_json(research_dir / "pilot_gate.json", gate | {"harvest": harvest, "dedupe": {
        "retained_count": dedupe["retained_count"], "class_counts": dedupe["class_counts"],
    }})
    if not gate["passed"]:
        if not (research_dir / "candidate_manifest.json").exists():
            atomic_write_json(research_dir / "candidate_manifest.json", {
                "schema_version": SCHEMA_VERSION, "baseline_hash": sha256_value(load_baseline(baseline_path)),
                "candidate_count": 0, "rights_status_counts": {}, "embedding_model": E5_MODEL_ID,
                "records": [], "generated_at": utc_now(),
            })
        if not (research_dir / "review_state.json").exists():
            atomic_write_json(research_dir / "review_state.json", {"schema_version": 1, "reviews": {}, "updated_at": utc_now()})
        refresh_review_manifests(research_dir)
        build_final_report(research_dir, baseline_path)
        return {"status": "pilot_gate_failed", "gate": gate}
    run_triage_batches(research_dir, router, maximum_calls=1)
    no_yield_briefs = 0
    retained_before = dedupe["retained_count"]
    for maximum_briefs in range(3, MAX_DISCOVERY_CALLS + 1):
        run_discovery_calls(research_dir, router, maximum_briefs=maximum_briefs)
        harvest = harvest_saved_discovery(research_dir)
        dedupe = build_dedupe_manifest(baseline_path, research_dir)
        if dedupe["retained_count"] > retained_before:
            no_yield_briefs = 0
        else:
            no_yield_briefs += 1
        retained_before = dedupe["retained_count"]
        if retained_before >= TARGET_REVIEWABLE or no_yield_briefs >= 2:
            break
    run_triage_batches(research_dir, router, maximum_calls=MAX_TRIAGE_CALLS - 1)
    manifest = build_candidate_manifest(baseline_path, research_dir)
    summary = build_final_report(research_dir, baseline_path)
    return {"status": "completed", "candidate_count": manifest["candidate_count"], "summary": summary}


def status(research_dir: Path) -> dict[str, Any]:
    """Return the status."""
    result: dict[str, Any] = {"research_dir": str(research_dir)}
    for name in ("coverage_profile.json", "api_preflight.json", "provider_route_state.json", "pilot_gate.json", "dedupe_manifest.json", "candidate_manifest.json", "review_state.json"):
        path = research_dir / name
        result[name.removesuffix(".json")] = read_json(path) if path.exists() else None
    if result.get("candidate_manifest"):
        result["candidate_manifest"] = {
            "candidate_count": result["candidate_manifest"]["candidate_count"],
            "rights_status_counts": result["candidate_manifest"]["rights_status_counts"],
        }
    return result


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("profile", "preflight", "run", "status", "serve-review", "export-kept"))
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--research-dir", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--confirm-max-cost-usd", type=float)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--output-dir", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line entry point."""
    args = build_parser().parse_args(argv)
    project_dir = args.project_dir.resolve()
    baseline_path = (args.baseline or project_dir / "image_analysis.json").resolve()
    research_dir = (args.research_dir or project_dir / "image_discovery_research" / RUN_ID).resolve()
    load_project_environment(project_dir / "mrsMThatcher.env")
    if args.command == "profile":
        print(json.dumps(write_coverage_outputs(baseline_path, research_dir), indent=2, sort_keys=True))
    elif args.command == "preflight":
        write_coverage_outputs(baseline_path, research_dir)
        print(json.dumps(api_preflight(research_dir), indent=2, sort_keys=True))
    elif args.command == "run":
        print(json.dumps(run_pipeline(
            baseline_path, research_dir, execute=args.execute,
            confirmed_cost=args.confirm_max_cost_usd,
        ), indent=2, sort_keys=True))
    elif args.command == "status":
        print(json.dumps(status(research_dir), indent=2, sort_keys=True))
    elif args.command == "serve-review":
        serve_review(research_dir, baseline_path, args.host, args.port)
    elif args.command == "export-kept":
        output_dir = (args.output_dir or research_dir / "exported_kept").resolve()
        print(json.dumps(export_kept(research_dir, output_dir), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
