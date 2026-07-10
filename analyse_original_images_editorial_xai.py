#!/usr/bin/env python3
"""
Analyse original MrsMThatcher images with a richer editorial prompt.

This is intended as an EXPERIMENTAL analyser for the non-generated/original
image corpus. It does not overwrite the existing production analysis unless you
explicitly choose to use its output later.

Key idea:
- do NOT just describe what is literally visible;
- do analyse what kinds of Thatcher quotations the image is editorially suited to.

Example:
    python3 analyse_original_images_editorial_xai.py \
      --input-dir images \
      --output original_image_editorial_analysis_experiment_v1.json \
      --limit 12 \
      --shuffle

Targeted example:
    python3 analyse_original_images_editorial_xai.py \
      --input-dir images \
      --output original_image_editorial_analysis_experiment_v1.json \
      --basenames t70.jpg t12.jpg t43.jpg
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

API_BASE = "https://api.x.ai/v1"
RESPONSES_URL = f"{API_BASE}/responses"
DEFAULT_MODEL = "grok-4.5"
DEFAULT_OUTPUT = "original_image_editorial_analysis_experiment_v1.json"
PROMPT_VERSION = "editorial_originals_v1"
LOG = logging.getLogger("original-editorial-analysis")

ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "image_summary": {"type": "string", "maxLength": 500},
        "scene_description": {"type": "string", "maxLength": 1400},
        "thatcher_present": {"type": "boolean"},
        "thatcher_prominence": {
            "type": "string",
            "enum": ["none", "minor", "shared", "dominant"],
        },
        "setting": {"type": "string", "maxLength": 200},
        "visible_elements": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 16,
        },
        "moods": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 12,
        },
        "rhetorical_energy": {
            "type": "string",
            "enum": ["very_low", "low", "medium", "high", "very_high"],
        },
        "editorial_functions": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 14,
        },
        "abstract_quote_affinities": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 18,
        },
        "best_quote_types": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 14,
        },
        "avoid_quote_types": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 12,
        },
        "literal_topics": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 12,
        },
        "match_strengths": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 12,
        },
        "match_risks": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 12,
        },
        "dimension_scores": {
            "type": "object",
            "properties": {
                "leadership": {"type": "number", "minimum": 0, "maximum": 10},
                "conviction": {"type": "number", "minimum": 0, "maximum": 10},
                "authority": {"type": "number", "minimum": 0, "maximum": 10},
                "defiance": {"type": "number", "minimum": 0, "maximum": 10},
                "warning": {"type": "number", "minimum": 0, "maximum": 10},
                "optimism": {"type": "number", "minimum": 0, "maximum": 10},
                "patriotism": {"type": "number", "minimum": 0, "maximum": 10},
                "statesmanship": {"type": "number", "minimum": 0, "maximum": 10},
                "economic_seriousness": {"type": "number", "minimum": 0, "maximum": 10},
                "human_warmth": {"type": "number", "minimum": 0, "maximum": 10},
                "ceremony_formality": {"type": "number", "minimum": 0, "maximum": 10},
                "historical_iconicity": {"type": "number", "minimum": 0, "maximum": 10},
            },
            "required": [
                "leadership",
                "conviction",
                "authority",
                "defiance",
                "warning",
                "optimism",
                "patriotism",
                "statesmanship",
                "economic_seriousness",
                "human_warmth",
                "ceremony_formality",
                "historical_iconicity",
            ],
            "additionalProperties": False,
        },
        "overall_editorial_utility": {
            "type": "number",
            "minimum": 0,
            "maximum": 10,
        },
        "reasoning": {"type": "string", "maxLength": 1800},
    },
    "required": [
        "image_summary",
        "scene_description",
        "thatcher_present",
        "thatcher_prominence",
        "setting",
        "visible_elements",
        "moods",
        "rhetorical_energy",
        "editorial_functions",
        "abstract_quote_affinities",
        "best_quote_types",
        "avoid_quote_types",
        "literal_topics",
        "match_strengths",
        "match_risks",
        "dimension_scores",
        "overall_editorial_utility",
        "reasoning",
    ],
    "additionalProperties": False,
}

ANALYSIS_PROMPT = """
You are analysing one original historical/political image for a Margaret Thatcher quotation account.

Your task is NOT merely to describe what is literally in the photograph.

Your main goal is to judge the image as an EDITORIAL COMPANION to Thatcher quotations.

Important rules:

1. Go beyond literal depiction.
   - A picture of Thatcher speaking at a microphone may work very well for quotes about freedom, conviction,
     warning, leadership, responsibility, national direction, or political struggle even if those concepts
     are not visibly depicted as objects.
   - Do not reduce the analysis to surface nouns only.

2. Focus on quote-pairing usefulness.
   Ask:
   - What kinds of Thatcher quotations would this image suit best?
   - What abstract ideas or rhetorical tones does it naturally support?
   - What kinds of quotes would feel mismatched or awkward with it?

3. Prefer editorial/rhetorical interpretation over over-literal tagging.
   Good examples:
   - assertion_of_principle
   - warning
   - resolve
   - leadership
   - persuasion
   - statesmanship
   - confrontation
   - national_direction
   - duty
   - conviction
   - public_service
   - authority
   - freedom
   - responsibility

4. Be concise but meaningful.
   Use short phrases rather than long paragraphs in list fields.

5. Do not invent specific historical facts unless they are obvious from the image itself.
   If uncertain, stay general.

6. Score the image as a reusable editorial asset for quotes, not as a work of art.

Interpretation guidance:
- "editorial_functions" means what the image DOES rhetorically in a post.
- "abstract_quote_affinities" means the abstract ideas the image naturally supports.
- "best_quote_types" means the kinds of Thatcher quote themes that would pair well.
- "avoid_quote_types" means themes likely to feel mismatched.
- "literal_topics" should still capture major visible subject matter, but must not dominate the analysis.
- "match_strengths" should explain why this image is useful.
- "match_risks" should explain what kinds of false or weak matches could occur.

Return only the requested JSON.
""".strip()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyse original images for editorial quote-pairing usefulness using xAI vision."
    )
    parser.add_argument(
        "--input-dir",
        default="images",
        help="Directory containing original images (default: images)",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"Output JSON file (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--glob",
        default="*",
        help="Filename glob within input-dir (default: *)",
    )
    parser.add_argument(
        "--basenames",
        nargs="*",
        default=[],
        help="Optional explicit basenames to analyse",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum number of images to analyse (0 = no limit)",
    )
    parser.add_argument(
        "--shuffle",
        action="store_true",
        help="Shuffle candidate image order before limiting",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=12345,
        help="Random seed used with --shuffle",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"xAI model name (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="HTTP timeout in seconds (default: 120)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=6,
        help="Max request retries (default: 6)",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=1.5,
        help="Sleep after successful API call in seconds (default: 1.5)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Reanalyse items already present in the output file",
    )
    parser.add_argument(
        "--store-raw-response",
        action="store_true",
        help="Store the raw parsed API payload per image",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()


def make_session() -> requests.Session:
    api_key = os.environ.get("XAI_API_KEY")
    if not api_key:
        raise SystemExit("XAI_API_KEY is not set in the environment.")
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "MrsMThatcher-OriginalEditorialAnalysis/1.0",
    })
    return session


def response_text(payload: dict[str, Any]) -> str:
    for item in payload.get("output", []):
        if isinstance(item, dict) and item.get("type") == "message":
            for content in item.get("content", []):
                if (
                    isinstance(content, dict)
                    and content.get("type") == "output_text"
                    and isinstance(content.get("text"), str)
                ):
                    return content["text"]
    raise ValueError("No output_text found in xAI Responses API payload")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def data_uri(path: Path) -> str:
    suffix = path.suffix.lower()
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suffix)
    if mime is None:
        raise ValueError(f"Unsupported image type for {path}")
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def load_output(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": 1,
            "analysis_kind": "original_editorial_experiment",
            "prompt_version": PROMPT_VERSION,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "items": {},
            "run_history": [],
        }

    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if payload.get("schema_version") != 1 or not isinstance(payload.get("items"), dict):
        raise SystemExit(f"Unsupported or malformed output file: {path}")

    payload.setdefault("analysis_kind", "original_editorial_experiment")
    payload.setdefault("prompt_version", PROMPT_VERSION)
    payload.setdefault("run_history", [])
    payload.setdefault("updated_at", utc_now())
    return payload


def post_json_with_retries(
    session: requests.Session,
    url: str,
    payload: dict[str, Any],
    *,
    timeout: float,
    max_retries: int,
    sleep_seconds: float,
) -> dict[str, Any]:
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            response = session.post(url, json=payload, timeout=timeout)
            if response.status_code < 400:
                result = response.json()
                if not isinstance(result, dict):
                    raise ValueError("xAI response was not a JSON object")
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
                return result

            body = response.text[:2000]
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                raise RuntimeError(f"Transient HTTP {response.status_code}: {body}")
            raise RuntimeError(f"Non-retryable HTTP {response.status_code}: {body}")

        except (requests.Timeout, requests.ConnectionError, RuntimeError, ValueError) as exc:
            last_error = exc
            transient = isinstance(exc, (requests.Timeout, requests.ConnectionError)) or "Transient HTTP" in str(exc)
            if not transient or attempt >= max_retries:
                break
            delay = min(60.0, (2 ** (attempt - 1)) + random.uniform(0.0, 1.0))
            LOG.warning(
                "API attempt %d/%d failed transiently: %s; sleeping %.1fs",
                attempt,
                max_retries,
                exc,
                delay,
            )
            time.sleep(delay)

    raise RuntimeError(f"xAI request failed after retries: {last_error}") from last_error


def structured_vision_call(
    session: requests.Session,
    *,
    model: str,
    image_path: Path,
    timeout: float,
    max_retries: int,
    sleep_seconds: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = {
        "model": model,
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_image", "image_url": data_uri(image_path), "detail": "high"},
                {"type": "input_text", "text": ANALYSIS_PROMPT},
            ],
        }],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "original_editorial_analysis",
                "schema": ANALYSIS_SCHEMA,
                "strict": True,
            }
        },
        "store": False,
    }

    raw = post_json_with_retries(
        session,
        RESPONSES_URL,
        payload,
        timeout=timeout,
        max_retries=max_retries,
        sleep_seconds=sleep_seconds,
    )
    parsed = json.loads(response_text(raw))
    if not isinstance(parsed, dict):
        raise ValueError("Structured analysis was not a JSON object")
    return parsed, raw


def discover_images(args: argparse.Namespace) -> list[Path]:
    input_dir = Path(args.input_dir).expanduser().resolve()
    if not input_dir.is_dir():
        raise SystemExit(f"Input directory does not exist: {input_dir}")

    candidates = []
    allowed_suffixes = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

    if args.basenames:
        for name in args.basenames:
            path = input_dir / name
            if not path.is_file():
                raise SystemExit(f"Requested basename not found: {path}")
            if path.suffix.lower() not in allowed_suffixes:
                raise SystemExit(f"Unsupported file type: {path}")
            candidates.append(path)
    else:
        for path in sorted(input_dir.glob(args.glob)):
            if not path.is_file():
                continue
            if path.suffix.lower() not in allowed_suffixes:
                continue
            if path.name.startswith("._"):
                continue
            if path.name.startswith("tg_"):
                # This script is intended for original images.
                continue
            candidates.append(path)

    if args.shuffle:
        rng = random.Random(args.seed)
        rng.shuffle(candidates)

    if args.limit > 0:
        candidates = candidates[:args.limit]

    return candidates


def main() -> int:
    args = parse_args()
    configure_logging(args.verbose)

    output_path = Path(args.output).expanduser().resolve()
    output_payload = load_output(output_path)
    session = make_session()

    output_payload["updated_at"] = utc_now()
    output_payload["prompt_version"] = PROMPT_VERSION
    output_payload["analysis_kind"] = "original_editorial_experiment"
    output_payload["model"] = args.model
    output_payload["input_dir"] = str(Path(args.input_dir).expanduser().resolve())

    candidates = discover_images(args)
    LOG.info("Candidate images discovered: %d", len(candidates))

    processed = 0
    skipped_existing = 0
    failures = 0

    for index, path in enumerate(candidates, start=1):
        basename = path.name
        if not args.overwrite and basename in output_payload["items"]:
            skipped_existing += 1
            LOG.info("[%d/%d] Skipping existing %s", index, len(candidates), basename)
            continue

        LOG.info("[%d/%d] Analysing %s", index, len(candidates), basename)

        try:
            analysis, raw = structured_vision_call(
                session,
                model=args.model,
                image_path=path,
                timeout=args.timeout,
                max_retries=args.max_retries,
                sleep_seconds=args.sleep,
            )

            record = {
                "basename": basename,
                "path": str(path),
                "sha256": sha256_file(path),
                "analysed_at": utc_now(),
                "analysis": analysis,
            }
            if args.store_raw_response:
                record["raw_response"] = raw

            output_payload["items"][basename] = record
            output_payload["updated_at"] = utc_now()
            atomic_write_json(output_path, output_payload)

            processed += 1
            LOG.info(
                "Saved %s | utility=%.2f | affinities=%s",
                basename,
                float(analysis.get("overall_editorial_utility", 0.0)),
                ", ".join(analysis.get("abstract_quote_affinities", [])[:6]),
            )

        except Exception as exc:  # noqa: BLE001
            failures += 1
            LOG.exception("Failed to analyse %s: %s", basename, exc)

    output_payload["run_history"].append({
        "finished_at": utc_now(),
        "processed": processed,
        "skipped_existing": skipped_existing,
        "failures": failures,
        "candidate_count": len(candidates),
        "model": args.model,
        "prompt_version": PROMPT_VERSION,
    })
    output_payload["updated_at"] = utc_now()
    atomic_write_json(output_path, output_payload)

    LOG.info("Done.")
    LOG.info("Processed: %d", processed)
    LOG.info("Skipped existing: %d", skipped_existing)
    LOG.info("Failures: %d", failures)
    LOG.info("Output: %s", output_path)

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
