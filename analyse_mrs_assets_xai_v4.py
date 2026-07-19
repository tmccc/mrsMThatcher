#!/usr/bin/env python3
"""
One-off/resumable xAI metadata analysis for the MrsMThatcher quote archive.

Analyses:
  * every non-empty quote in mrsMThatcher.txt
  * every image file directly under images/

Writes two JSON databases designed for later use by mrsMThatcher2.py:
  * quote_analysis.json
  * image_analysis.json

Key properties:
  * one xAI call per unique quote/image content hash
  * strict JSON-schema structured outputs
  * resumable and idempotent
  * atomic checkpoint writes after every item
  * retries only transient failures
  * stable SHA-256 identities survive quote reordering and image renaming
  * duplicate quote text / duplicate image bytes are analysed only once

The script deliberately does not modify the live bot or its state.
"""

from __future__ import annotations

import argparse
import base64
import copy
import fcntl
import hashlib
import json
import logging
import os
import random
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests


DEFAULT_BASE_DIR = Path("/disks/disk1/etc/mrsMThatcher")
DEFAULT_QUOTE_FILE = DEFAULT_BASE_DIR / "mrsMThatcher.txt"
DEFAULT_IMAGE_DIR = DEFAULT_BASE_DIR / "images"
DEFAULT_QUOTE_OUTPUT = DEFAULT_BASE_DIR / "quote_analysis.json"
DEFAULT_IMAGE_OUTPUT = DEFAULT_BASE_DIR / "image_analysis.json"
DEFAULT_LOG_FILE = DEFAULT_BASE_DIR / "mrs_asset_analysis.log"
DEFAULT_LOCK_FILE = DEFAULT_BASE_DIR / ".mrs_asset_analysis.lock"

DEFAULT_XAI_BASE_URL = "https://api.x.ai/v1"
DEFAULT_MODEL = os.getenv("XAI_MODEL", "grok-4.3")

QUOTE_PROMPT_VERSION = "quote-analysis-2026-07-05-v4"
IMAGE_PROMPT_VERSION = "image-analysis-2026-07-05-v4"
QUOTE_DB_SCHEMA_VERSION = 2
IMAGE_DB_SCHEMA_VERSION = 3

DIRECT_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
CONVERTIBLE_IMAGE_EXTENSIONS = {
    ".webp",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
}
MAX_DIRECT_IMAGE_BYTES = 20 * 1024 * 1024
TARGET_PREPARED_IMAGE_BYTES = 18 * 1024 * 1024

TOPIC_VALUES = [
    "economy",
    "free_enterprise",
    "taxation",
    "inflation",
    "trade",
    "unions",
    "socialism",
    "communism",
    "state_power",
    "individual_liberty",
    "democracy",
    "law_and_order",
    "defence",
    "foreign_policy",
    "europe",
    "national_identity",
    "patriotism",
    "leadership",
    "government",
    "parliament",
    "monarchy",
    "family",
    "faith",
    "morality",
    "education",
    "welfare",
    "work",
    "responsibility",
    "opportunity",
    "women",
    "environment",
    "history",
    "humour",
    "personal_reflection",
    "other",
]

TONE_VALUES = [
    "combative",
    "authoritative",
    "reflective",
    "optimistic",
    "patriotic",
    "humorous",
    "sarcastic",
    "compassionate",
    "solemn",
    "celebratory",
    "warning",
    "defiant",
    "pragmatic",
    "inspirational",
    "personal",
    "warm",
    "serious",
]

SCENE_VALUES = [
    "podium_speech",
    "parliament",
    "formal_portrait",
    "informal_portrait",
    "crowd",
    "outdoor_public_event",
    "diplomatic_event",
    "military_or_defence",
    "industrial_or_workplace",
    "family_or_social",
    "religious_or_ceremonial",
    "patriotic_flags",
    "campaigning",
    "office_or_working",
    "international_travel",
    "historical_archive",
    "generic_statesmanlike",
    "press_or_media",
    "other",
]

MOOD_VALUES = [
    "serious",
    "smiling",
    "forceful",
    "reflective",
    "warm",
    "determined",
    "relaxed",
    "solemn",
    "celebratory",
    "neutral",
    "concerned",
    "animated",
]

OCCASION_VALUES = [
    "christmas",
    "new_year",
    "easter",
    "remembrance_day",
    "armistice_day",
    "conference_season",
    "election",
    "other",
]

SEASON_VALUES = ["spring", "summer", "autumn", "winter"]


class AnalysisError(RuntimeError):
    """Base exception for image-analysis failures."""
    pass


class PermanentAnalysisError(AnalysisError):
    """Raised when retrying an analysis cannot succeed."""
    pass


class TransientAnalysisError(AnalysisError):
    """Raised when an analysis may succeed after retrying."""
    pass


@dataclass(frozen=True)
class PreparedImage:
    """Represent prepared image data."""
    data: bytes
    mime_type: str
    preparation: str


@dataclass(frozen=True)
class ApiResult:
    """Represent API result data."""
    content: dict[str, Any]
    usage: dict[str, Any]
    response_id: str | None


def now_iso() -> str:
    """Return the now iso."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    """Return the SHA-256 bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """Return the SHA-256 text."""
    return sha256_bytes(text.encode("utf-8"))


def normalise_quote_text(text: str) -> str:
    """Normalise quote text."""
    return re.sub(r"\s+", " ", text.strip())


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    """Read JSON."""
    if not path.exists():
        return copy.deepcopy(default)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisError(f"Could not read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AnalysisError(f"Expected JSON object in {path}")
    return value


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    """Write a JSON document atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def configure_logging(log_file: Path | None, verbose: bool) -> None:
    """Configure logging."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        handlers=handlers,
        force=True,
    )


def acquire_lock(path: Path) -> Any:
    """Return the acquire lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise AnalysisError(f"Another asset-analysis process already holds {path}") from exc
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid={os.getpid()} started={now_iso()}\n")
    handle.flush()
    return handle


def enum_schema(values: list[str]) -> dict[str, Any]:
    """Return the enum schema."""
    return {"type": "string", "enum": values}


def string_array_schema(*, max_items: int = 12) -> dict[str, Any]:
    """Return the string array schema."""
    return {
        "type": "array",
        "items": {"type": "string", "maxLength": 120},
        "maxItems": max_items,
    }


def enum_array_schema(values: list[str], *, max_items: int = 10) -> dict[str, Any]:
    """Return the enum array schema."""
    return {
        "type": "array",
        "items": enum_schema(values),
        "maxItems": max_items,
    }


SEASON_WINDOW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "start_mm_dd": {
            "type": "string",
            "pattern": "(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])",
        },
        "end_mm_dd": {
            "type": "string",
            "pattern": "(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])",
        },
        "reason": {"type": "string", "maxLength": 240},
    },
    "required": ["start_mm_dd", "end_mm_dd", "reason"],
    "additionalProperties": False,
}

QUOTE_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "maxLength": 360},
        "primary_topics": enum_array_schema(TOPIC_VALUES, max_items=5),
        "secondary_topics": enum_array_schema(TOPIC_VALUES, max_items=8),
        "specific_keywords": string_array_schema(max_items=14),
        "tone": enum_array_schema(TONE_VALUES, max_items=6),
        "emotional_intensity": {"type": "integer", "minimum": 0, "maximum": 100},
        "visual_energy": {"type": "string", "enum": ["low", "medium", "high"]},
        "literal_visual_concepts": string_array_schema(max_items=12),
        "archive_image_preferences": {
            "type": "object",
            "properties": {
                "preferred_scenes": enum_array_schema(SCENE_VALUES, max_items=8),
                "preferred_subject_moods": enum_array_schema(MOOD_VALUES, max_items=7),
                "preferred_activities": string_array_schema(max_items=10),
                "preferred_visible_symbols": string_array_schema(max_items=10),
                "visual_affinities": string_array_schema(max_items=10),
                "weak_visual_mismatches": string_array_schema(max_items=10),
                "strong_visual_mismatches": string_array_schema(max_items=8),
                "matching_summary": {"type": "string", "maxLength": 520},
            },
            "required": [
                "preferred_scenes",
                "preferred_subject_moods",
                "preferred_activities",
                "preferred_visible_symbols",
                "visual_affinities",
                "weak_visual_mismatches",
                "strong_visual_mismatches",
                "matching_summary",
            ],
            "additionalProperties": False,
        },
        "seasonality": {
            "type": "object",
            "properties": {
                "relevance": {
                    "type": "string",
                    "enum": ["none", "soft", "strong", "date_specific"],
                },
                "seasons": enum_array_schema(SEASON_VALUES, max_items=4),
                "occasions": enum_array_schema(OCCASION_VALUES, max_items=5),
                "preferred_windows": {
                    "type": "array",
                    "items": SEASON_WINDOW_SCHEMA,
                    "maxItems": 6,
                },
                "hard_exclude_outside_windows": {"type": "boolean"},
                "explanation": {"type": "string", "maxLength": 420},
            },
            "required": [
                "relevance",
                "seasons",
                "occasions",
                "preferred_windows",
                "hard_exclude_outside_windows",
                "explanation",
            ],
            "additionalProperties": False,
        },
        "historical_context": {
            "type": "object",
            "properties": {
                "specificity": {
                    "type": "string",
                    "enum": ["general", "specific_period", "specific_event", "specific_person"],
                },
                "referenced_people": string_array_schema(max_items=8),
                "referenced_places": string_array_schema(max_items=8),
                "referenced_events": string_array_schema(max_items=8),
                "needs_historical_image_match": {"type": "boolean"},
                "explanation": {"type": "string", "maxLength": 420},
            },
            "required": [
                "specificity",
                "referenced_people",
                "referenced_places",
                "referenced_events",
                "needs_historical_image_match",
                "explanation",
            ],
            "additionalProperties": False,
        },
        "scores": {
            "type": "object",
            "properties": {
                "standalone_clarity": {"type": "integer", "minimum": 0, "maximum": 100},
                "visual_matchability": {"type": "integer", "minimum": 0, "maximum": 100},
                "general_post_suitability": {"type": "integer", "minimum": 0, "maximum": 100},
            },
            "required": ["standalone_clarity", "visual_matchability", "general_post_suitability"],
            "additionalProperties": False,
        },
    },
    "required": [
        "summary",
        "primary_topics",
        "secondary_topics",
        "specific_keywords",
        "tone",
        "emotional_intensity",
        "visual_energy",
        "literal_visual_concepts",
        "archive_image_preferences",
        "seasonality",
        "historical_context",
        "scores",
    ],
    "additionalProperties": False,
}

IMAGE_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "description": {"type": "string", "maxLength": 520},
        "scene_summary": {"type": "string", "maxLength": 360},
        "scene_types": enum_array_schema(SCENE_VALUES, max_items=8),
        "visible_elements": string_array_schema(max_items=16),
        "setting": {
            "type": "object",
            "properties": {
                "location_type": {
                    "type": "string",
                    "enum": ["indoor", "outdoor", "mixed", "unknown"],
                },
                "details": string_array_schema(max_items=10),
            },
            "required": ["location_type", "details"],
            "additionalProperties": False,
        },
        "composition": {
            "type": "object",
            "properties": {
                "shot_type": {
                    "type": "string",
                    "enum": [
                        "close_up",
                        "head_and_shoulders",
                        "half_body",
                        "full_body",
                        "group",
                        "crowd",
                        "wide_scene",
                        "document_or_artwork",
                        "other",
                    ],
                },
                "orientation": {
                    "type": "string",
                    "enum": ["landscape", "portrait", "square", "unknown"],
                },
                "subject_prominence": {"type": "integer", "minimum": 0, "maximum": 100},
                "background_complexity": {"type": "integer", "minimum": 0, "maximum": 100},
            },
            "required": ["shot_type", "orientation", "subject_prominence", "background_complexity"],
            "additionalProperties": False,
        },
        "people": {
            "type": "object",
            "properties": {
                "count_category": {
                    "type": "string",
                    "enum": ["none", "one", "two", "small_group", "crowd", "unclear"],
                },
                "primary_subject_activities": string_array_schema(max_items=8),
                "primary_subject_moods": enum_array_schema(MOOD_VALUES, max_items=7),
                "formality": {
                    "type": "string",
                    "enum": ["very_formal", "formal", "semi_formal", "informal", "mixed", "unknown"],
                },
            },
            "required": [
                "count_category",
                "primary_subject_activities",
                "primary_subject_moods",
                "formality",
            ],
            "additionalProperties": False,
        },
        "visible_text": {
            "type": "object",
            "properties": {
                "present": {"type": "boolean"},
                "summary": {"type": ["string", "null"], "maxLength": 320},
            },
            "required": ["present", "summary"],
            "additionalProperties": False,
        },
        "themes": enum_array_schema(TOPIC_VALUES, max_items=10),
        "tone": enum_array_schema(TONE_VALUES, max_items=8),
        "visual_energy": {"type": "string", "enum": ["low", "medium", "high"]},
        "seasonality": {
            "type": "object",
            "properties": {
                "visible_season": {
                    "type": "string",
                    "enum": ["none", "spring", "summer", "autumn", "winter", "ambiguous"],
                },
                "strength": {
                    "type": "string",
                    "enum": ["none", "weak", "moderate", "strong"],
                },
                "occasions": enum_array_schema(OCCASION_VALUES, max_items=5),
                "cues": string_array_schema(max_items=10),
                "avoid_outside_season_or_occasion": {"type": "boolean"},
                "explanation": {"type": "string", "maxLength": 420},
            },
            "required": [
                "visible_season",
                "strength",
                "occasions",
                "cues",
                "avoid_outside_season_or_occasion",
                "explanation",
            ],
            "additionalProperties": False,
        },
        "historical_context": {
            "type": "object",
            "properties": {
                "specificity": {
                    "type": "string",
                    "enum": ["general", "suggestive", "specific_event"],
                },
                "event_or_context_hint": {"type": ["string", "null"], "maxLength": 260},
                "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
                "visible_symbols": string_array_schema(max_items=10),
            },
            "required": ["specificity", "event_or_context_hint", "confidence", "visible_symbols"],
            "additionalProperties": False,
        },
        "quality": {
            "type": "object",
            "properties": {
                "overall": {"type": "integer", "minimum": 0, "maximum": 100},
                "subject_clarity": {"type": "integer", "minimum": 0, "maximum": 100},
                "composition": {"type": "integer", "minimum": 0, "maximum": 100},
                "technical_quality": {"type": "integer", "minimum": 0, "maximum": 100},
                "crop_suitability_for_x": {"type": "integer", "minimum": 0, "maximum": 100},
            },
            "required": [
                "overall",
                "subject_clarity",
                "composition",
                "technical_quality",
                "crop_suitability_for_x",
            ],
            "additionalProperties": False,
        },
        "pairing": {
            "type": "object",
            "properties": {
                "best_for_topics": enum_array_schema(TOPIC_VALUES, max_items=10),
                "best_for_tones": enum_array_schema(TONE_VALUES, max_items=8),
                "weak_for_topics": enum_array_schema(TOPIC_VALUES, max_items=8),
                "general_reusability": {"type": "integer", "minimum": 0, "maximum": 100},
                "semantic_specificity": {"type": "integer", "minimum": 0, "maximum": 100},
                "matching_summary": {"type": "string", "maxLength": 500},
            },
            "required": [
                "best_for_topics",
                "best_for_tones",
                "weak_for_topics",
                "general_reusability",
                "semantic_specificity",
                "matching_summary",
            ],
            "additionalProperties": False,
        },
    },
    "required": [
        "description",
        "scene_summary",
        "scene_types",
        "visible_elements",
        "setting",
        "composition",
        "people",
        "visible_text",
        "themes",
        "tone",
        "visual_energy",
        "seasonality",
        "historical_context",
        "quality",
        "pairing",
    ],
    "additionalProperties": False,
}


QUOTE_SYSTEM_PROMPT = """
You are creating durable metadata for an automated Margaret Thatcher quotation account on X.
Analyse only the supplied quote text. The metadata will later be used to:
1. keep strongly seasonal or occasion-specific quotes close to the right calendar period;
2. pre-rank a fixed archive of existing political photographs for semantic and tonal suitability;
3. allow a later AI comparison of the chosen quote against a reduced shortlist of actual archive images.

The eventual image is NOT arbitrary stock art and will NOT be generated to fit the quote. It will be
chosen from a fixed archive of political photographs used by the account, consisting mainly of formal
and informal portraits, speeches, parliamentary or official settings, public events, campaigning,
diplomatic occasions, travel, crowds, patriotic scenes, and other historical/political photographs.

Separate two ideas carefully:
- literal_visual_concepts: what the words literally evoke, even if no such archive photograph exists;
- archive_image_preferences: realistic guidance for choosing the best available political photograph
  from that fixed archive.

Do not recommend an impossible literal scene as the main archive preference merely because the quote
uses a metaphor. Translate metaphors into realistic archive-photo qualities such as mood, setting,
activity, symbolism, formality, and visual energy.

Be conservative and grounded. Assign a topic only when it is directly expressed or materially implied
by this specific quotation. Do not add themes merely because they are commonly associated with
Margaret Thatcher, conservatism, or the account. Do not infer a broad political value from a generic
word when the quotation itself does not make that value part of its meaning. Empty arrays are valid.
As a calibration guide, most quotes should have about 1-3 primary topics and 0-3 secondary topics;
use more only when the quotation genuinely contains several distinct substantive ideas. Secondary
topics must still be meaningfully present, not merely associated by background knowledge.

Do not invent historical context, people, events, places, dates, or seasonality that are not supported
by the text.

Treat preferred_windows as practical social-media posting windows, not merely the literal dates of an
event. A date-specific event can have a surrounding period during which a post still feels timely.
For example, an explicitly Christmas quotation can reasonably be timely from around mid-December,
not only on 24-25 December. Choose the operational window in which posting would feel natural to a
reader. A quote can also be usable near Christmas without being Christmas-specific: mark that as soft
seasonality, not strong. Use hard_exclude_outside_windows only when posting outside all preferred
windows would clearly look wrong or absurd. An explicitly occasion-specific quotation should normally
use hard_exclude_outside_windows=true when posting it outside the practical occasion window would
plainly look mistimed. For example, a quotation explicitly about Christmas should normally be hard-
excluded outside its Christmas posting window. Do not use hard exclusion for merely soft seasonal
relevance or for a general quotation that simply happens to suit an occasion.

Strong visual mismatches should normally be empty. Add one only when the visual combination would
actively contradict, undermine, or make nonsense of the quotation. An image that is merely irrelevant,
suboptimal, too literal, too generic, or a poor tonal fit belongs in weak_visual_mismatches instead.
Do not turn ordinary lack of fit into a strong mismatch.

Use concise lower-case keywords and the controlled enum values exactly where the schema provides them.
""".strip()

QUOTE_USER_TEMPLATE = """
Analyse this single quotation for future seasonal scheduling and image matching.

QUOTE:
<quote>
{quote}
</quote>
""".strip()

IMAGE_SYSTEM_PROMPT = """
You are creating durable visual metadata for a fixed archive of photographs used with political
quotations on X. Analyse only what is visibly supported by the image. The metadata will later be used to:
1. prevent seasonally inappropriate image choices;
2. pre-rank images for a chosen quotation by topic, tone, mood, setting, and visual energy;
3. allow a later AI comparison of the chosen quote against a reduced shortlist.

Do not identify people by name. Do not infer private or sensitive attributes. Do not invent an exact
event, date, location, or historical context from a generic photograph. If an event/context is only a
possibility, use low confidence and a cautious hint.

Keep visible image content separate from pairing suitability:
- themes means topics materially depicted or signalled by the visible setting, activity, objects,
  symbols, text, or interaction. It may be empty. A generic formal portrait does not automatically
  depict leadership, government, patriotism, or any other political topic merely because the subject
  appears to be a politician.
- pairing.best_for_topics means quotation topics for which this image would make a good accompaniment,
  even when that topic is not literally visible. This can therefore be broader than themes, but it
  should still be selective and grounded in the image's mood, activity, formality, setting, and symbols.

Do not assign political themes merely because the subject appears to be a famous politician. Ground
visible themes in the actual image. Empty arrays are valid and preferable to generic topic inflation.

Set visual_energy from the image itself:
- low: static, quiet, restrained, contemplative, or visually subdued;
- medium: ordinary public activity, engaged interaction, or moderate movement/emphasis;
- high: forceful gesture, strong action, intense crowd energy, dramatic movement, or unusually dynamic
  composition.

Strong seasonality requires visible evidence such as Christmas decorations, snow, clearly autumnal
foliage, or another unmistakable cue. Clothing alone usually supports only weak or moderate
seasonality.

The pairing field weak_for_topics is advisory and should be sparse. Leave it empty unless the image is
notably poorer for a topic than a generic archive photograph would be. Do not enumerate every topic
that is simply absent from the scene, and do not use it as a catalogue of all non-matching subjects.
Reserve true hard exclusion for seasonal or genuinely contradictory cases captured elsewhere.

Judge suitability for pairing, not political truth. Use the controlled enum values exactly where the
schema provides them.
""".strip()

IMAGE_USER_PROMPT = """
Analyse this single archive image for future seasonal filtering and quote-to-image matching.
Describe the visible scene, mood, composition, visual energy, seasonality, quality, and the kinds of
quotation themes and tones it would best support. Keep visible themes distinct from pairing suitability.
Use weak_for_topics only for notably poor fits, and leave it empty when there are no such topics.
""".strip()


def fresh_quote_db(model: str, quote_file: Path) -> dict[str, Any]:
    """Return the fresh quote database."""
    return {
        "schema_version": QUOTE_DB_SCHEMA_VERSION,
        "analysis_kind": "quotes",
        "prompt_version": QUOTE_PROMPT_VERSION,
        "model": model,
        "source": {"quote_file": str(quote_file)},
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "line_index": {},
        "current_hashes": [],
        "items": {},
        "failures": {},
    }


def fresh_image_db(model: str, image_dir: Path) -> dict[str, Any]:
    """Return the fresh image database."""
    return {
        "schema_version": IMAGE_DB_SCHEMA_VERSION,
        "analysis_kind": "images",
        "prompt_version": IMAGE_PROMPT_VERSION,
        "model": model,
        "source": {"image_dir": str(image_dir)},
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "path_index": {},
        "current_hashes": [],
        "items": {},
        "failures": {},
    }


def ensure_db_shape(db: dict[str, Any], fresh: dict[str, Any]) -> dict[str, Any]:
    """Ensure database shape."""
    result = copy.deepcopy(fresh)
    result.update(db)
    for key in ("items", "failures"):
        if not isinstance(result.get(key), dict):
            result[key] = {}
    return result


def item_is_current(item: Any, model: str, prompt_version: str) -> bool:
    """Return whether item is current."""
    return (
        isinstance(item, dict)
        and item.get("analysis_model") == model
        and item.get("prompt_version") == prompt_version
        and isinstance(item.get("analysis"), dict)
    )


def response_retry_delay(response: requests.Response | None, attempt_index: int) -> float:
    """Return the response retry delay."""
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
        reset = response.headers.get("x-ratelimit-reset")
        if reset:
            try:
                return max(0.0, float(reset) - time.time() + 1.0)
            except ValueError:
                pass
    return min(120.0, 2.0 * (2 ** attempt_index)) + random.uniform(0.0, 1.0)


def call_xai_structured(
    *,
    api_key: str,
    base_url: str,
    model: str,
    messages: list[dict[str, Any]],
    schema_name: str,
    schema: dict[str, Any],
    timeout_seconds: int,
    max_tokens: int,
    max_retries: int,
) -> ApiResult:
    """Return the call xAI structured."""
    payload = {
        "model": model,
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "schema": schema,
                "strict": True,
            },
        },
        "max_tokens": max_tokens,
    }

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        response: requests.Response | None = None
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=timeout_seconds)
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= max_retries:
                raise TransientAnalysisError(f"xAI request failed: {exc}") from exc
            delay = response_retry_delay(None, attempt)
            logging.warning("xAI transport failure attempt=%d/%d; retrying in %.1fs: %s", attempt + 1, max_retries + 1, delay, exc)
            time.sleep(delay)
            continue

        if 200 <= response.status_code < 300:
            try:
                data = response.json()
                raw_content = data["choices"][0]["message"]["content"]
                content = json.loads(raw_content)
            except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                raise PermanentAnalysisError(
                    f"Could not parse successful xAI structured response: {response.text[:1200]}"
                ) from exc
            if not isinstance(content, dict):
                raise PermanentAnalysisError("xAI structured response was not a JSON object")
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            response_id = str(data.get("id")) if data.get("id") is not None else None
            return ApiResult(content=content, usage=usage, response_id=response_id)

        body = response.text[:2000]
        status = response.status_code
        if status == 429 or status == 408 or 500 <= status <= 599:
            last_error = TransientAnalysisError(f"xAI HTTP {status}: {body}")
            if attempt >= max_retries:
                raise last_error
            delay = response_retry_delay(response, attempt)
            logging.warning("xAI transient HTTP %d attempt=%d/%d; retrying in %.1fs", status, attempt + 1, max_retries + 1, delay)
            time.sleep(delay)
            continue

        raise PermanentAnalysisError(f"xAI HTTP {status}: {body}")

    raise TransientAnalysisError(str(last_error or "xAI request failed"))


def load_quotes(quote_file: Path) -> tuple[dict[str, str], dict[str, list[int]]]:
    """Load quotes."""
    try:
        lines = quote_file.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise AnalysisError(f"Could not read quote file {quote_file}: {exc}") from exc

    line_index: dict[str, str] = {}
    hash_lines: dict[str, list[int]] = {}
    unique_quotes: dict[str, str] = {}

    for line_number, raw in enumerate(lines, start=1):
        quote = normalise_quote_text(raw)
        if not quote:
            continue
        quote_hash = sha256_text(quote)
        line_index[str(line_number)] = quote_hash
        hash_lines.setdefault(quote_hash, []).append(line_number)
        unique_quotes.setdefault(quote_hash, quote)

    return unique_quotes, hash_lines


def list_image_files(image_dir: Path, recursive: bool) -> list[Path]:
    """List image files."""
    if not image_dir.is_dir():
        raise AnalysisError(f"Image directory does not exist: {image_dir}")
    iterator: Iterable[Path] = image_dir.rglob("*") if recursive else image_dir.iterdir()
    files = []
    for path in iterator:
        if not path.is_file():
            continue
        if path.name == ".DS_Store" or path.name.startswith("._"):
            continue
        suffix = path.suffix.lower()
        if suffix in DIRECT_IMAGE_EXTENSIONS or suffix in CONVERTIBLE_IMAGE_EXTENSIONS:
            files.append(path)
        else:
            logging.warning("Skipping unsupported non-image/unknown file: %s", path)
    return sorted(files, key=lambda p: str(p).lower())


def prepare_image(path: Path) -> PreparedImage:
    """Prepare image."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise AnalysisError(f"Could not read image {path}: {exc}") from exc

    suffix = path.suffix.lower()
    if suffix in DIRECT_IMAGE_EXTENSIONS and len(data) <= MAX_DIRECT_IMAGE_BYTES:
        mime = "image/png" if suffix == ".png" else "image/jpeg"
        return PreparedImage(data=data, mime_type=mime, preparation="original")

    try:
        from PIL import Image  # type: ignore
    except ImportError as exc:
        reason = "unsupported format" if suffix not in DIRECT_IMAGE_EXTENSIONS else "image exceeds xAI 20MiB input limit"
        raise AnalysisError(
            f"{path}: {reason}; install Pillow (`python3 -m pip install Pillow`) so the analyser can convert it"
        ) from exc

    try:
        with Image.open(path) as source:
            if getattr(source, "is_animated", False):
                source.seek(0)
            image = source.convert("RGB")
            image.thumbnail((4096, 4096))
            qualities = [92, 88, 82, 75, 68, 60, 50]
            prepared = b""
            for quality in qualities:
                with tempfile.SpooledTemporaryFile(max_size=TARGET_PREPARED_IMAGE_BYTES + 1) as handle:
                    image.save(handle, format="JPEG", quality=quality, optimize=True)
                    handle.seek(0)
                    prepared = handle.read()
                if len(prepared) <= TARGET_PREPARED_IMAGE_BYTES:
                    break
            if len(prepared) > MAX_DIRECT_IMAGE_BYTES:
                raise AnalysisError(f"Could not reduce {path} below the xAI 20MiB image limit")
            return PreparedImage(data=prepared, mime_type="image/jpeg", preparation="converted_to_jpeg")
    except AnalysisError:
        raise
    except Exception as exc:
        raise AnalysisError(f"Could not prepare image {path}: {exc}") from exc


def make_quote_messages(quote: str) -> list[dict[str, Any]]:
    """Create quote messages."""
    return [
        {"role": "system", "content": QUOTE_SYSTEM_PROMPT},
        {"role": "user", "content": QUOTE_USER_TEMPLATE.format(quote=quote)},
    ]


def make_image_messages(prepared: PreparedImage, detail: str) -> list[dict[str, Any]]:
    """Create image messages."""
    encoded = base64.b64encode(prepared.data).decode("ascii")
    data_url = f"data:{prepared.mime_type};base64,{encoded}"
    return [
        {"role": "system", "content": IMAGE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": data_url, "detail": detail},
                },
                {"type": "text", "text": IMAGE_USER_PROMPT},
            ],
        },
    ]


def record_failure(db: dict[str, Any], item_hash: str, *, source: str, error: Exception) -> None:
    """Record failure."""
    old = db.setdefault("failures", {}).get(item_hash, {})
    attempts = int(old.get("attempts", 0)) + 1 if isinstance(old, dict) else 1
    db["failures"][item_hash] = {
        "source": source,
        "attempts": attempts,
        "last_attempt_at": now_iso(),
        "error_type": type(error).__name__,
        "error": str(error)[:4000],
    }


def process_quotes(args: argparse.Namespace, api_key: str) -> None:
    """Process quotes."""
    quote_file = args.quote_file
    output = args.quote_output
    unique_quotes, hash_lines = load_quotes(quote_file)
    line_index = {
        str(line_number): quote_hash
        for quote_hash, line_numbers in hash_lines.items()
        for line_number in line_numbers
    }

    fresh = fresh_quote_db(args.model, quote_file)
    db = ensure_db_shape(read_json(output, fresh), fresh)
    db["schema_version"] = QUOTE_DB_SCHEMA_VERSION
    db["analysis_kind"] = "quotes"
    db["prompt_version"] = QUOTE_PROMPT_VERSION
    db["model"] = args.model
    db["source"] = {
        "quote_file": str(quote_file),
        "source_sha256": sha256_bytes(quote_file.read_bytes()),
        "line_count": len(quote_file.read_text(encoding="utf-8").splitlines()),
        "non_empty_quote_count": sum(len(v) for v in hash_lines.values()),
        "unique_quote_count": len(unique_quotes),
    }
    db["line_index"] = dict(sorted(line_index.items(), key=lambda x: int(x[0])))
    db["current_hashes"] = sorted(unique_quotes.keys())
    db["updated_at"] = now_iso()
    atomic_write_json(output, db)

    pending = []
    for quote_hash, quote in unique_quotes.items():
        existing = db["items"].get(quote_hash)
        if not args.force and item_is_current(existing, args.model, QUOTE_PROMPT_VERSION):
            continue
        pending.append((quote_hash, quote, hash_lines[quote_hash]))

    if args.quote_limit is not None:
        pending = pending[: args.quote_limit]

    logging.info(
        "Quotes: %d non-empty lines, %d unique, %d pending, output=%s",
        sum(len(v) for v in hash_lines.values()),
        len(unique_quotes),
        len(pending),
        output,
    )

    for index, (quote_hash, quote, line_numbers) in enumerate(pending, start=1):
        label = f"quote {index}/{len(pending)} hash={quote_hash[:12]} lines={line_numbers}"
        if args.dry_run:
            logging.info("DRY RUN %s", label)
            continue
        logging.info("Analysing %s", label)
        try:
            result = call_xai_structured(
                api_key=api_key,
                base_url=args.xai_base_url,
                model=args.model,
                messages=make_quote_messages(quote),
                schema_name="mrs_quote_analysis",
                schema=QUOTE_ANALYSIS_SCHEMA,
                timeout_seconds=args.timeout,
                max_tokens=args.quote_max_tokens,
                max_retries=args.max_retries,
            )
            db["items"][quote_hash] = {
                "quote_hash": quote_hash,
                "text": quote,
                "line_numbers": line_numbers,
                "analysis_model": args.model,
                "prompt_version": QUOTE_PROMPT_VERSION,
                "analysed_at": now_iso(),
                "response_id": result.response_id,
                "usage": result.usage,
                "analysis": result.content,
            }
            db["failures"].pop(quote_hash, None)
            logging.info("Completed %s usage=%s", label, result.usage)
        except Exception as exc:
            logging.exception("Failed %s", label)
            record_failure(db, quote_hash, source=f"lines={line_numbers}", error=exc)
        db["updated_at"] = now_iso()
        atomic_write_json(output, db)
        if args.sleep_seconds > 0 and index < len(pending):
            time.sleep(args.sleep_seconds)


def process_images(args: argparse.Namespace, api_key: str) -> None:
    """Process images."""
    image_dir = args.image_dir
    output = args.image_output
    files = list_image_files(image_dir, args.recursive_images)

    hash_paths: dict[str, list[str]] = {}
    hash_files: dict[str, Path] = {}
    file_metadata: dict[str, dict[str, Any]] = {}
    path_index: dict[str, str] = {}

    for path in files:
        data = path.read_bytes()
        image_hash = sha256_bytes(data)
        relative = str(path.relative_to(image_dir))
        path_index[relative] = image_hash
        hash_paths.setdefault(image_hash, []).append(relative)
        hash_files.setdefault(image_hash, path)
        stat = path.stat()
        file_metadata[relative] = {
            "size_bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "suffix": path.suffix.lower(),
        }

    fresh = fresh_image_db(args.model, image_dir)
    db = ensure_db_shape(read_json(output, fresh), fresh)
    db["schema_version"] = IMAGE_DB_SCHEMA_VERSION
    db["analysis_kind"] = "images"
    db["prompt_version"] = IMAGE_PROMPT_VERSION
    db["model"] = args.model
    db["source"] = {
        "image_dir": str(image_dir),
        "file_count": len(files),
        "unique_content_count": len(hash_files),
        "recursive": bool(args.recursive_images),
    }
    db["path_index"] = dict(sorted(path_index.items()))
    db["current_hashes"] = sorted(hash_files.keys())
    db["file_metadata"] = dict(sorted(file_metadata.items()))
    db["updated_at"] = now_iso()
    atomic_write_json(output, db)

    pending = []
    for image_hash, path in hash_files.items():
        existing = db["items"].get(image_hash)
        if not args.force and item_is_current(existing, args.model, IMAGE_PROMPT_VERSION):
            continue
        pending.append((image_hash, path, hash_paths[image_hash]))

    if args.image_limit is not None:
        pending = pending[: args.image_limit]

    logging.info(
        "Images: %d files, %d unique contents, %d pending, output=%s",
        len(files),
        len(hash_files),
        len(pending),
        output,
    )

    for index, (image_hash, path, relative_paths) in enumerate(pending, start=1):
        label = f"image {index}/{len(pending)} hash={image_hash[:12]} paths={relative_paths}"
        if args.dry_run:
            logging.info("DRY RUN %s", label)
            continue
        logging.info("Analysing %s", label)
        try:
            prepared = prepare_image(path)
            result = call_xai_structured(
                api_key=api_key,
                base_url=args.xai_base_url,
                model=args.model,
                messages=make_image_messages(prepared, args.image_detail),
                schema_name="mrs_image_analysis",
                schema=IMAGE_ANALYSIS_SCHEMA,
                timeout_seconds=args.timeout,
                max_tokens=args.image_max_tokens,
                max_retries=args.max_retries,
            )
            db["items"][image_hash] = {
                "image_hash": image_hash,
                "paths": relative_paths,
                "source_size_bytes": path.stat().st_size,
                "source_suffix": path.suffix.lower(),
                "api_mime_type": prepared.mime_type,
                "preparation": prepared.preparation,
                "analysis_model": args.model,
                "prompt_version": IMAGE_PROMPT_VERSION,
                "analysed_at": now_iso(),
                "response_id": result.response_id,
                "usage": result.usage,
                "analysis": result.content,
            }
            db["failures"].pop(image_hash, None)
            logging.info("Completed %s usage=%s", label, result.usage)
        except Exception as exc:
            logging.exception("Failed %s", label)
            record_failure(db, image_hash, source=",".join(relative_paths), error=exc)
        db["updated_at"] = now_iso()
        atomic_write_json(output, db)
        if args.sleep_seconds > 0 and index < len(pending):
            time.sleep(args.sleep_seconds)


def positive_int(value: str) -> int:
    """Return the positive int."""
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return parsed


def non_negative_float(value: str) -> float:
    """Return the non negative float."""
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return parsed


def parse_args() -> argparse.Namespace:
    """Parse args."""
    parser = argparse.ArgumentParser(
        description="Analyse MrsMThatcher quotes and regular-post images with xAI and store structured JSON metadata."
    )
    parser.add_argument("--quote-file", type=Path, default=DEFAULT_QUOTE_FILE)
    parser.add_argument("--image-dir", type=Path, default=DEFAULT_IMAGE_DIR)
    parser.add_argument("--quote-output", type=Path, default=DEFAULT_QUOTE_OUTPUT)
    parser.add_argument("--image-output", type=Path, default=DEFAULT_IMAGE_OUTPUT)
    parser.add_argument("--log-file", type=Path, default=DEFAULT_LOG_FILE)
    parser.add_argument("--lock-file", type=Path, default=DEFAULT_LOCK_FILE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--xai-base-url", default=os.getenv("XAI_API_BASE_URL", DEFAULT_XAI_BASE_URL))
    parser.add_argument("--timeout", type=positive_int, default=180)
    parser.add_argument("--max-retries", type=int, default=6)
    parser.add_argument("--sleep-seconds", type=non_negative_float, default=1.0)
    parser.add_argument("--quote-max-tokens", type=positive_int, default=1800)
    parser.add_argument("--image-max-tokens", type=positive_int, default=2200)
    parser.add_argument("--image-detail", choices=["low", "auto", "high"], default="high")
    parser.add_argument("--quote-limit", type=positive_int)
    parser.add_argument("--image-limit", type=positive_int)
    parser.add_argument("--quotes-only", action="store_true")
    parser.add_argument("--images-only", action="store_true")
    parser.add_argument("--recursive-images", action="store_true")
    parser.add_argument("--force", action="store_true", help="Reanalyse even current items")
    parser.add_argument("--dry-run", action="store_true", help="Scan and show pending work without calling xAI")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.quotes_only and args.images_only:
        parser.error("--quotes-only and --images-only are mutually exclusive")
    if args.max_retries < 0:
        parser.error("--max-retries must be >= 0")
    return args


def main() -> int:
    """Run the command-line entry point."""
    args = parse_args()
    configure_logging(args.log_file, args.verbose)

    api_key = os.getenv("XAI_API_KEY", "")
    if not args.dry_run and not api_key:
        logging.error("XAI_API_KEY is not set")
        return 2

    lock_handle = None
    try:
        lock_handle = acquire_lock(args.lock_file)
        logging.info("Acquired analysis lock %s", args.lock_file)
        logging.info("Model=%s xAI_base=%s dry_run=%s", args.model, args.xai_base_url, args.dry_run)

        if not args.images_only:
            process_quotes(args, api_key)
        if not args.quotes_only:
            process_images(args, api_key)

        logging.info("Analysis run complete")
        return 0
    except AnalysisError as exc:
        logging.error("Analysis failed: %s", exc)
        return 1
    except KeyboardInterrupt:
        logging.warning("Interrupted; completed items are already checkpointed")
        return 130
    finally:
        if lock_handle is not None:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            lock_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
