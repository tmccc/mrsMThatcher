#!/usr/bin/env python3
"""Audit generated images for identity-dependent editorial meaning.

This is an offline experiment. It never imports or writes production bot state,
configuration, image analysis, histories, or receipts.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import math
import mimetypes
import os
import random
import re
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE_DIR = ROOT / "generated_review_approved_images"
DEFAULT_ORIGIN_ROOT = ROOT / "openai_generated_quote_images" / "items"
DEFAULT_ANALYSIS = ROOT / "generated_image_analysis.json"
DEFAULT_OUTPUT = ROOT / "generated_image_identity_dependence_audit.json"
DEFAULT_REPORT = ROOT / "generated_image_identity_dependence_audit_report.md"
DEFAULT_REVIEW_DIR = ROOT / "review" / "generated_identity_dependence"
DEFAULT_LOG = ROOT / "mrsMThatcher.log"
DEFAULT_FACE_MANIFEST = DEFAULT_IMAGE_DIR / "grok_face_correction_manifest_v2.json"
DEFAULT_PRE_FACE_BACKUP = DEFAULT_IMAGE_DIR / "pre_face_correction_backup_20260709_033026"
DEFAULT_MODEL = os.getenv("XAI_MODEL", "grok-4.3")
DEFAULT_API_BASE = os.getenv("XAI_API_BASE_URL", "https://api.x.ai/v1")
SCHEMA_VERSION = 1
ANALYSIS_KIND = "generated_image_identity_dependence_audit"
PROMPT_VERSION = "identity-dependence-2026-07-10-v2"
GENERATED_RE = re.compile(r"^tg_([0-9a-f]{64})\.png$")

IDENTITY_DEPENDENCE = {"none", "low", "medium", "high", "essential"}
PERSON_PROMINENCE = {"none", "minor", "supporting", "dominant"}
KNOWLEDGE_LEVEL = {"none", "low", "medium", "high"}
RISK_LEVEL = {"none", "low", "medium", "high"}
REUSE_SAFETY = {"safe", "mostly_safe", "contextual", "risky", "origin_quote_only"}
POLICIES = {"unrestricted", "small_penalty", "strong_penalty", "origin_quote_only"}
PERSON_SOURCES = {"origin_metadata", "generation_prompt", "existing_analysis", "none", "unclear"}

AUDIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "contains_specific_intended_person": {"type": "boolean"},
        "intended_person_source": {"type": "string", "enum": sorted(PERSON_SOURCES)},
        "intended_person_label": {"type": ["string", "null"]},
        "person_prominence": {"type": "string", "enum": sorted(PERSON_PROMINENCE)},
        "identity_dependence": {"type": "string", "enum": sorted(IDENTITY_DEPENDENCE)},
        "recognisability_to_typical_viewer": {"type": "number", "minimum": 0, "maximum": 10},
        "recognisability_to_politically_interested_viewer": {"type": "number", "minimum": 0, "maximum": 10},
        "specialist_knowledge_required": {"type": "string", "enum": sorted(KNOWLEDGE_LEVEL)},
        "likeness_ambiguity": {"type": "string", "enum": sorted(RISK_LEVEL)},
        "misidentification_risk": {"type": "string", "enum": sorted(RISK_LEVEL)},
        "meaning_without_identity": {"type": "string", "minLength": 1, "maxLength": 500},
        "meaning_retention_without_identity": {"type": "number", "minimum": 0, "maximum": 10},
        "origin_quote_suitability": {"type": "number", "minimum": 0, "maximum": 10},
        "cross_quote_reuse_safety": {"type": "string", "enum": sorted(REUSE_SAFETY)},
        "recommended_cross_quote_policy": {"type": "string", "enum": sorted(POLICIES)},
        "recommended_penalty_strength": {"type": "number", "minimum": 0, "maximum": 10},
        "identity_failure_reason": {"type": "string", "maxLength": 500},
        "cross_quote_reasoning": {"type": "string", "minLength": 1, "maxLength": 700},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "contains_specific_intended_person", "intended_person_source", "intended_person_label",
        "person_prominence", "identity_dependence", "recognisability_to_typical_viewer",
        "recognisability_to_politically_interested_viewer", "specialist_knowledge_required",
        "likeness_ambiguity", "misidentification_risk", "meaning_without_identity",
        "meaning_retention_without_identity", "origin_quote_suitability", "cross_quote_reuse_safety",
        "recommended_cross_quote_policy", "recommended_penalty_strength", "identity_failure_reason",
        "cross_quote_reasoning", "confidence",
    ],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generated_origin_hash(basename: str) -> str:
    match = GENERATED_RE.fullmatch(basename)
    if not match:
        raise ValueError(f"Invalid generated image basename: {basename}")
    return match.group(1)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, allow_nan=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def finite_number(value: Any, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError(f"{name} must be finite in {low}..{high}")
    return number


def grounded_person_label_is_valid(label: str, grounded_people: Iterable[str]) -> bool:
    grounded = {str(value).strip().casefold() for value in grounded_people if str(value).strip()}
    if label.strip().casefold() in grounded:
        return True
    parts = re.split(r"\s*(?:;|,|/|\band\b)\s*", label, flags=re.I)
    cleaned = {
        re.sub(r"\s*\([^)]*\)\s*$", "", part).strip().casefold()
        for part in parts
        if part.strip()
    }
    return bool(cleaned) and cleaned <= grounded


def validate_analysis(analysis: Any, grounded_people: Iterable[str] = ()) -> dict[str, Any]:
    if not isinstance(analysis, dict):
        raise ValueError("analysis must be an object")
    required = set(AUDIT_SCHEMA["required"])
    actual = set(analysis)
    if actual != required:
        raise ValueError(f"analysis schema mismatch missing={sorted(required-actual)} unexpected={sorted(actual-required)}")
    enum_fields = {
        "intended_person_source": PERSON_SOURCES,
        "person_prominence": PERSON_PROMINENCE,
        "identity_dependence": IDENTITY_DEPENDENCE,
        "specialist_knowledge_required": KNOWLEDGE_LEVEL,
        "likeness_ambiguity": RISK_LEVEL,
        "misidentification_risk": RISK_LEVEL,
        "cross_quote_reuse_safety": REUSE_SAFETY,
        "recommended_cross_quote_policy": POLICIES,
    }
    for key, allowed in enum_fields.items():
        if analysis[key] not in allowed:
            raise ValueError(f"invalid {key}: {analysis[key]!r}")
    if type(analysis["contains_specific_intended_person"]) is not bool:
        raise ValueError("contains_specific_intended_person must be boolean")
    for key in ("recognisability_to_typical_viewer", "recognisability_to_politically_interested_viewer", "meaning_retention_without_identity", "origin_quote_suitability", "recommended_penalty_strength"):
        finite_number(analysis[key], key, 0, 10)
    finite_number(analysis["confidence"], "confidence", 0, 1)
    for key in ("meaning_without_identity", "identity_failure_reason", "cross_quote_reasoning"):
        if not isinstance(analysis[key], str) or (key != "identity_failure_reason" and not analysis[key].strip()):
            raise ValueError(f"{key} must be text")
    label = analysis["intended_person_label"]
    grounded = {str(value).strip().casefold() for value in grounded_people if str(value).strip()}
    if analysis["contains_specific_intended_person"]:
        if not isinstance(label, str) or not label.strip():
            raise ValueError("specific intended person requires intended_person_label")
        if grounded and not grounded_person_label_is_valid(label, grounded_people):
            raise ValueError(f"intended person {label!r} is not grounded in supplied origin metadata")
        if not grounded:
            raise ValueError("specific intended identity cannot be invented without grounded origin metadata")
    elif label is not None:
        raise ValueError("intended_person_label must be null when no specific intended person is established")
    return analysis


def analysis_records_by_basename(path: Path) -> dict[str, dict[str, Any]]:
    payload = load_json(path)
    if payload.get("analysis_kind") != "images" or not isinstance(payload.get("items"), dict):
        raise ValueError(f"Unsupported generated analysis: {path}")
    result: dict[str, dict[str, Any]] = {}
    for image_hash, record in payload["items"].items():
        if not isinstance(record, dict):
            continue
        for basename in record.get("paths") or []:
            if isinstance(basename, str):
                result[Path(basename).name] = {"image_hash": image_hash, **record}
    return result


def grounded_people_from_origin(quote_analysis: dict[str, Any], face_audit_item: dict[str, Any] | None = None) -> list[str]:
    historical = quote_analysis.get("historical_context") if isinstance(quote_analysis.get("historical_context"), dict) else {}
    people = [str(value).strip() for value in historical.get("referenced_people") or [] if str(value).strip()]
    assessment = face_audit_item.get("assessment") if isinstance(face_audit_item, dict) and isinstance(face_audit_item.get("assessment"), dict) else {}
    if assessment.get("thatcher_present") is True:
        people.append("Margaret Thatcher")
    unique: list[str] = []
    seen: set[str] = set()
    for person in people:
        key = person.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(person)
    return unique


def build_context(
    image_path: Path,
    origin_root: Path,
    existing_record: dict[str, Any],
    face_audit_item: dict[str, Any] | None = None,
) -> dict[str, Any]:
    origin_hash = generated_origin_hash(image_path.name)
    item_dir = origin_root / origin_hash
    if not item_dir.is_dir():
        raise ValueError(f"Missing origin metadata directory for {image_path.name}: {item_dir}")
    quote = read_text(item_dir / "quote.txt")
    prompt = read_text(item_dir / "generation_prompt.txt")
    quote_analysis = load_json(item_dir / "quote_analysis.json")
    manifest = load_json(item_dir / "manifest.json")
    if manifest.get("quote_hash") != origin_hash:
        raise ValueError(f"Origin manifest hash mismatch for {image_path.name}")
    grounded_people = grounded_people_from_origin(quote_analysis, face_audit_item)
    return {
        "basename": image_path.name,
        "image_path": str(image_path),
        "image_sha256": sha256_file(image_path),
        "origin_quote_hash": origin_hash,
        "origin_quote": quote,
        "generation_prompt": prompt,
        "semantic_brief": quote_analysis,
        "manifest": manifest,
        "existing_analysis": existing_record.get("analysis") or {},
        "existing_analysis_hash": existing_record.get("image_hash"),
        "grounded_people": grounded_people,
    }


def face_correction_provenance(
    path: Path,
    current_hash: str,
    analysed_hash: str,
    manifest_items: dict[str, Any],
    pre_face_backup: Path | None,
) -> dict[str, Any] | None:
    item = manifest_items.get(path.name)
    if not isinstance(item, dict) or pre_face_backup is None:
        return None
    backup = pre_face_backup / path.name
    if not backup.is_file() or sha256_file(backup) != analysed_hash:
        return None
    candidates = []
    output_path = item.get("output_path")
    if isinstance(output_path, str):
        candidates.append(Path(output_path))
    for subdir in ("grok_face_corrected", "grok_face_rejected_candidates"):
        candidates.append(path.parent / subdir / path.name)
    matching = next((candidate for candidate in candidates if candidate.is_file() and sha256_file(candidate) == current_hash), None)
    if matching is None:
        return None
    return {
        "status": item.get("status"),
        "pre_correction_sha256": analysed_hash,
        "current_sha256": current_hash,
        "matching_correction_artifact": str(matching),
        "manifest": str(DEFAULT_FACE_MANIFEST),
    }


def validate_inputs(
    image_dir: Path,
    origin_root: Path,
    analysis_path: Path,
    basenames: list[str] | None = None,
    face_manifest: Path | None = None,
    pre_face_backup: Path | None = None,
) -> list[dict[str, Any]]:
    records = analysis_records_by_basename(analysis_path)
    manifest_items: dict[str, Any] = {}
    if face_manifest and face_manifest.is_file():
        manifest_payload = load_json(face_manifest)
        if not isinstance(manifest_payload.get("items"), dict):
            raise ValueError(f"Malformed face-correction manifest: {face_manifest}")
        manifest_items = manifest_payload["items"]
    paths = sorted(image_dir.glob("tg_*.png"))
    if basenames:
        requested = set(basenames)
        for name in requested:
            generated_origin_hash(name)
        paths = [path for path in paths if path.name in requested]
        missing = requested - {path.name for path in paths}
        if missing:
            raise ValueError(f"Requested generated image(s) missing: {sorted(missing)}")
    contexts = []
    for path in paths:
        generated_origin_hash(path.name)
        record = records.get(path.name)
        if not record:
            raise ValueError(f"Missing generated_image_analysis.json record for {path.name}")
        context = build_context(path, origin_root, record, manifest_items.get(path.name))
        if context["existing_analysis_hash"] != context["image_sha256"]:
            provenance = face_correction_provenance(
                path,
                context["image_sha256"],
                str(context["existing_analysis_hash"] or ""),
                manifest_items,
                pre_face_backup,
            )
            if provenance is None:
                raise ValueError(f"Stale generated analysis hash without face-correction provenance for {path.name}")
            context["face_correction_provenance"] = provenance
        contexts.append(context)
    if not contexts:
        raise ValueError("No generated images selected for audit")
    return contexts


def data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def prompt_for_context(context: dict[str, Any]) -> str:
    supplied = {
        "basename": context["basename"],
        "origin_quote_hash": context["origin_quote_hash"],
        "origin_quote": context["origin_quote"],
        "grounded_intended_people": context["grounded_people"],
        "generation_prompt": context["generation_prompt"],
        "semantic_image_brief": context["semantic_brief"],
        "existing_visual_analysis": context["existing_analysis"],
    }
    return (
        "Audit this generated editorial image for identity dependence and safe cross-quote reuse.\n\n"
        "The supplied metadata is authoritative about generation intent. Do not guess a named person not present "
        "in grounded_intended_people. Determine whether the image actually centres one of those intended people, "
        "how recognisable that depiction is, and how much editorial meaning survives without recognition. A person "
        "being present does not itself make an image identity-dependent. Generic leaders, speakers, workers, families "
        "and crowds may remain reusable when their visible action and setting carry the meaning. Conversely, an "
        "ambiguous pseudo-portrait of a named dissident or historical figure may be unsafe across unrelated quotes.\n\n"
        "CALIBRATION RULES:\n"
        "- Do not classify an image as high/essential or origin_quote_only merely because it depicts a named person.\n"
        "- A recognisable Margaret Thatcher portrait, speech, working scene or leadership image can be unrestricted "
        "or mostly safe when its visible rhetoric (leadership, resolve, work, debate, patriotism, warmth) survives "
        "without knowing the exact originating quote. Thatcher's identity itself is normal account context, not an "
        "automatic cross-quote defect.\n"
        "- Reserve origin_quote_only for images whose scene makes a specific factual/historical claim, whose central "
        "meaning substantially disappears without identifying the person, or whose reuse would create likely "
        "misattribution/anachronism.\n"
        "- If meaning_retention_without_identity is 7 or higher and the scene does not create a specific factual "
        "misattribution, prefer safe/mostly_safe/contextual over origin_quote_only.\n"
        "- Use small_penalty or strong_penalty for intermediate risks. The policy distribution should reflect actual "
        "visual differences, not simply whether grounded_intended_people is non-empty.\n\n"
        "Scores must assess the rendered image, while intended identity must remain grounded in supplied metadata. "
        "If no grounded identity exists, contains_specific_intended_person must be false and intended_person_label null.\n\n"
        "INPUT CONTEXT:\n" + json.dumps(supplied, ensure_ascii=False, sort_keys=True)
    )


def response_text(payload: dict[str, Any]) -> str:
    for item in payload.get("output") or []:
        if isinstance(item, dict) and item.get("type") == "message":
            for content in item.get("content") or []:
                if isinstance(content, dict) and content.get("type") == "output_text" and isinstance(content.get("text"), str):
                    return content["text"]
    raise ValueError("No output_text in xAI response")


def call_xai(session: requests.Session, context: dict[str, Any], model: str, api_base: str, timeout: float, retries: int) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = {
        "model": model,
        "input": [{"role": "user", "content": [
            {"type": "input_image", "image_url": data_uri(Path(context["image_path"])), "detail": "high"},
            {"type": "input_text", "text": prompt_for_context(context)},
        ]}],
        "text": {"format": {"type": "json_schema", "name": "generated_identity_dependence", "schema": AUDIT_SCHEMA, "strict": True}},
        "store": False,
    }
    url = f"{api_base.rstrip('/')}/responses"
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = session.post(url, json=payload, timeout=timeout)
            if 200 <= response.status_code < 300:
                raw = response.json()
                parsed = json.loads(response_text(raw))
                validate_analysis(parsed, context["grounded_people"])
                return parsed, raw
            if response.status_code not in {408, 429} and not 500 <= response.status_code < 600:
                raise RuntimeError(f"Permanent xAI HTTP {response.status_code}: {response.text[:1200]}")
            raise requests.RequestException(f"Transient xAI HTTP {response.status_code}: {response.text[:1200]}")
        except (requests.RequestException, ValueError, KeyError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt >= retries or str(exc).startswith("Permanent xAI") or isinstance(exc, (ValueError, KeyError, json.JSONDecodeError)):
                break
            delay = min(60.0, 2 ** attempt + random.random())
            logging.warning("Transient xAI failure for %s attempt=%d/%d; retrying in %.1fs: %s", context["basename"], attempt + 1, retries + 1, delay, exc)
            time.sleep(delay)
    raise RuntimeError(f"xAI audit failed for {context['basename']}: {last_error}") from last_error


def empty_output(model: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "analysis_kind": ANALYSIS_KIND,
        "prompt_version": PROMPT_VERSION,
        "analysis_model": model,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "items": {},
        "failures": {},
        "runs": [],
    }


def load_output(path: Path, model: str) -> dict[str, Any]:
    if not path.exists():
        return empty_output(model)
    payload = load_json(path)
    if payload.get("schema_version") != SCHEMA_VERSION or payload.get("analysis_kind") != ANALYSIS_KIND or not isinstance(payload.get("items"), dict):
        raise ValueError(f"Unsupported audit output: {path}")
    payload.setdefault("failures", {})
    payload.setdefault("runs", [])
    return payload


def item_is_current(item: Any, context: dict[str, Any], model: str) -> bool:
    return bool(
        isinstance(item, dict)
        and item.get("image_sha256") == context["image_sha256"]
        and item.get("origin_quote_hash") == context["origin_quote_hash"]
        and item.get("analysis_model") == model
        and item.get("prompt_version") == PROMPT_VERSION
        and isinstance(item.get("analysis"), dict)
    )


def policy_fixed_penalty(policy: str) -> float | None:
    return {"unrestricted": 0.0, "small_penalty": 6.0, "strong_penalty": 18.0, "origin_quote_only": None}[policy]


def continuous_identity_penalty(analysis: dict[str, Any], maximum: float = 24.0) -> float:
    dependence = {"none": 0.0, "low": 0.2, "medium": 0.5, "high": 0.8, "essential": 1.0}[analysis["identity_dependence"]]
    recognisability = finite_number(analysis["recognisability_to_typical_viewer"], "recognisability", 0, 10) / 10
    retention = finite_number(analysis["meaning_retention_without_identity"], "retention", 0, 10) / 10
    return round(maximum * dependence * (1 - recognisability) * (1 - retention), 4)


def adjusted_cross_quote_score(score: float, analysis: dict[str, Any], policy_name: str) -> float | None:
    if policy_name == "continuous":
        return score - continuous_identity_penalty(analysis)
    penalty = policy_fixed_penalty(analysis["recommended_cross_quote_policy"])
    return None if penalty is None else score - penalty


def parse_recent_candidate_groups(log_path: Path) -> list[dict[str, Any]]:
    selected_re = re.compile(r"^(?P<time>\S+ \S+).*Selected matched image basename=(?P<name>\S+).* score=(?P<score>-?[0-9.]+)")
    candidate_re = re.compile(r"Image match candidate basename=(?P<name>\S+) score=(?P<score>-?[0-9.]+)")
    post_re = re.compile(r"Posting quote/image\. line_no=(?P<line>\d+) quote_hash=(?P<quote>[0-9a-f]{64})")
    groups: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            selected = selected_re.search(line)
            if selected:
                if current:
                    groups.append(current)
                current = {"time": selected.group("time"), "actual_winner": selected.group("name"), "actual_score": float(selected.group("score")), "candidates": {selected.group("name"): float(selected.group("score"))}}
                continue
            if current:
                candidate = candidate_re.search(line)
                if candidate:
                    current["candidates"][candidate.group("name")] = float(candidate.group("score"))
                post = post_re.search(line)
                if post:
                    current["line_no"] = int(post.group("line"))
                    current["quote_hash"] = post.group("quote")
                    groups.append(current)
                    current = None
    if current:
        groups.append(current)
    return [group for group in groups if str(group.get("actual_winner", "")).startswith("tg_") and group.get("quote_hash")]


def simulate_policies(groups: list[dict[str, Any]], audit_items: dict[str, Any]) -> dict[str, Any]:
    results = []
    for group in groups:
        rows = []
        for basename, score in group["candidates"].items():
            audit = (audit_items.get(basename) or {}).get("analysis")
            origin_match = basename.startswith("tg_") and generated_origin_hash(basename) == group["quote_hash"]
            row = {"basename": basename, "baseline_score": score, "source": "generated" if basename.startswith("tg_") else "original", "origin_match": origin_match}
            for policy in ("fixed", "continuous"):
                if row["source"] == "original" or origin_match:
                    row[policy] = score
                elif audit:
                    row[policy] = adjusted_cross_quote_score(score, audit, policy)
                else:
                    row[policy] = score
            rows.append(row)
        result = {key: value for key, value in group.items() if key != "candidates"}
        result["candidate_count_recoverable"] = len(rows)
        result["best_original_recoverable"] = next((row["basename"] for row in sorted(rows, key=lambda item: item["baseline_score"], reverse=True) if row["source"] == "original"), None)
        for policy in ("fixed", "continuous"):
            eligible = [row for row in rows if row[policy] is not None]
            winner = max(eligible, key=lambda row: (row[policy], row["basename"])) if eligible else None
            result[f"{policy}_winner"] = winner["basename"] if winner else None
            result[f"{policy}_winner_score"] = winner[policy] if winner else None
            result[f"{policy}_changed"] = bool(winner and winner["basename"] != group["actual_winner"])
        results.append(result)
    diagnostic = next((item for item in results if item["actual_winner"].startswith("tg_faf99f")), None)
    return {
        "scope": "recoverable logged top-candidate sets; not exact historical availability replay",
        "observations": len(results),
        "fixed_changes": sum(bool(item["fixed_changed"]) for item in results),
        "continuous_changes": sum(bool(item["continuous_changed"]) for item in results),
        "diagnostic": diagnostic,
        "results": results,
    }


def distribution(items: dict[str, Any], field: str) -> dict[str, int]:
    return dict(sorted(Counter((item.get("analysis") or {}).get(field) for item in items.values()).items(), key=lambda pair: str(pair[0])))


def create_contact_sheets(items: dict[str, Any], image_dir: Path, output_dir: Path) -> list[str]:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        logging.warning("Pillow unavailable; contact sheets skipped")
        return []
    output_dir.mkdir(parents=True, exist_ok=True)
    scored = list(items.items())
    groups = {
        "highest_identity_dependence": sorted(scored, key=lambda pair: ({"none": 0, "low": 1, "medium": 2, "high": 3, "essential": 4}[pair[1]["analysis"]["identity_dependence"]], -pair[1]["analysis"]["meaning_retention_without_identity"]), reverse=True)[:12],
        "lowest_recognisability_identity_dependent": sorted((pair for pair in scored if pair[1]["analysis"]["identity_dependence"] in {"medium", "high", "essential"}), key=lambda pair: pair[1]["analysis"]["recognisability_to_typical_viewer"])[:12],
        "origin_quote_only": [pair for pair in scored if pair[1]["analysis"]["recommended_cross_quote_policy"] == "origin_quote_only"][:12],
        "safe_person_comparison": [pair for pair in scored if pair[1]["analysis"]["contains_specific_intended_person"] and pair[1]["analysis"]["recommended_cross_quote_policy"] in {"unrestricted", "small_penalty"}][:12],
    }
    written = []
    font = ImageFont.load_default()
    for group_name, group in groups.items():
        if not group:
            continue
        tile_w, tile_h = 300, 360
        columns = 3
        rows = math.ceil(len(group) / columns)
        sheet = Image.new("RGB", (tile_w * columns, tile_h * rows), "white")
        draw = ImageDraw.Draw(sheet)
        for index, (basename, item) in enumerate(group):
            x, y = (index % columns) * tile_w, (index // columns) * tile_h
            with Image.open(image_dir / basename) as source:
                image = source.convert("RGB")
                image.thumbnail((tile_w - 16, 280))
            sheet.paste(image, (x + (tile_w - image.width) // 2, y + 4))
            analysis = item["analysis"]
            label = [basename[:34], f"dependence={analysis['identity_dependence']} recog={analysis['recognisability_to_typical_viewer']}", f"policy={analysis['recommended_cross_quote_policy']}"]
            draw.multiline_text((x + 8, y + 290), "\n".join(label), fill="black", font=font, spacing=3)
        path = output_dir / f"{group_name}.jpg"
        sheet.save(path, quality=90)
        written.append(str(path))
    return written


def report_markdown(output: dict[str, Any], diagnostic_quote: str, diagnostic_cross_quote: str, diagnostic_score: float) -> str:
    items = output["items"]
    analyses = [item["analysis"] for item in items.values()]
    specific = sum(bool(item["contains_specific_intended_person"]) for item in analyses)
    risk_order = {"none": 0, "low": 1, "medium": 2, "high": 3, "essential": 4}
    highest = sorted(items.items(), key=lambda pair: (risk_order[pair[1]["analysis"]["identity_dependence"]], -pair[1]["analysis"]["recognisability_to_typical_viewer"], -pair[1]["analysis"]["meaning_retention_without_identity"]), reverse=True)[:20]
    safe_people = [(name, item) for name, item in items.items() if item["analysis"]["contains_specific_intended_person"] and item["analysis"]["cross_quote_reuse_safety"] in {"safe", "mostly_safe"}]
    origin_only = [(name, item) for name, item in items.items() if item["analysis"]["recommended_cross_quote_policy"] == "origin_quote_only"]
    diagnostic_name = "tg_faf99f3030693b0a55f0116194551792f3c4ea51261d7310eca7fb4d33b667d5.png"
    diagnostic = items.get(diagnostic_name)
    lines = ["# Generated Image Identity-Dependence Audit", "", f"Generated: `{output.get('updated_at')}`", "", "## Executive verdict", ""]
    policy_counts = distribution(items, "recommended_cross_quote_policy")
    risky_count = sum(policy_counts.get(key, 0) for key in ("strong_penalty", "origin_quote_only"))
    lines.append(f"The audit completed {len(items)} generated images. {specific} contain a grounded specific intended person; {risky_count} are recommended for strong cross-quote restriction. The audit supports a narrow metadata-driven cross-quote policy experiment rather than a global generated-image penalty.")
    lines += ["", "## Input validation", "", f"- Approved generated images: `{output.get('input_count')}`", f"- Successfully audited: `{len(items)}`", f"- Failed: `{len(output.get('failures') or {})}`", "- Every basename, origin directory, manifest hash, current image SHA-256 and existing generated-analysis hash was validated.", "", "## Existing metadata reviewed", "", "The audit used the origin quote, quote analysis, generation prompt, manifest and existing visual analysis. Existing metadata grounds intended people but does not judge likeness, identity dependence, meaning retention or cross-quote safety.", "", "## Whether new xAI analysis was necessary", "", "Yes. Local metadata establishes intent, but only a vision judgement can assess whether the rendered person is recognisable and whether the image still communicates its meaning without that recognition.", "", "## Corpus distributions", ""]
    lines.append(f"Specific intended people: **{specific} / {len(items)}**")
    for title, field in (("Identity dependence", "identity_dependence"), ("Cross-quote reuse safety", "cross_quote_reuse_safety"), ("Recommended policy", "recommended_cross_quote_policy")):
        lines += ["", f"### {title}", "", "| value | count |", "|---|---:|"]
        for value, count in distribution(items, field).items():
            lines.append(f"| {value} | {count} |")
    typical = [item["recognisability_to_typical_viewer"] for item in analyses]
    retention = [item["meaning_retention_without_identity"] for item in analyses]
    lines += ["", "### Numeric distributions", "", "| measure | mean | minimum | maximum |", "|---|---:|---:|---:|", f"| Typical-viewer recognisability | {sum(typical)/len(typical):.2f} | {min(typical):.1f} | {max(typical):.1f} |", f"| Meaning retention without identity | {sum(retention)/len(retention):.2f} | {min(retention):.1f} | {max(retention):.1f} |"]
    def table(title: str, rows: list[tuple[str, Any]]) -> None:
        lines.extend(["", f"## {title}", "", "| image | identity dependence | recognisability | meaning retention | reuse safety | policy |", "|---|---|---:|---:|---|---|"])
        for name, item in rows:
            a = item["analysis"]
            lines.append(f"| `{name}` | {a['identity_dependence']} | {a['recognisability_to_typical_viewer']:.1f} | {a['meaning_retention_without_identity']:.1f} | {a['cross_quote_reuse_safety']} | {a['recommended_cross_quote_policy']} |")
    table("Highest-risk images", highest)
    table("Person-containing images that remain safe for reuse", safe_people[:20])
    table("Origin-quote-only recommendations", origin_only[:20])
    lines += ["", "## Diagnostic case study", "", f"Image: `{diagnostic_name}`", "", f"Origin quote: {diagnostic_quote}", "", f"Recent cross-quote: {diagnostic_cross_quote}", "", f"Logged production score: `{diagnostic_score}`"]
    if diagnostic:
        a = diagnostic["analysis"]
        lines += ["", f"- Intended person: `{a['intended_person_label']}` (source `{a['intended_person_source']}`)", f"- Identity dependence: `{a['identity_dependence']}`", f"- Typical-viewer recognisability: `{a['recognisability_to_typical_viewer']}` / 10", f"- Meaning without identity: {a['meaning_without_identity']}", f"- Meaning retention without identity: `{a['meaning_retention_without_identity']}` / 10", f"- Reuse safety: `{a['cross_quote_reuse_safety']}`", f"- Recommended policy: `{a['recommended_cross_quote_policy']}`", f"- Reason: {a['cross_quote_reasoning']}"]
    experiment = output.get("offline_policy_experiment") or {}
    lines += ["", "## Offline production-scoring experiment", "", f"Scope: {experiment.get('scope', 'not run')}", "", f"- Generated selections with recoverable logged candidate sets: `{experiment.get('observations', 0)}`", f"- Fixed-policy winner changes: `{experiment.get('fixed_changes', 0)}`", f"- Continuous-policy winner changes: `{experiment.get('continuous_changes', 0)}`", "- These are logged top-candidate comparisons, not exact historical replay of image-cycle availability."]
    if experiment.get("diagnostic"):
        lines += ["", "Diagnostic simulated result:", "", "```json", json.dumps(experiment["diagnostic"], indent=2, ensure_ascii=False), "```"]
    lines += ["", "## Recommended next production experiment", "", "Keep production unchanged while reviewing the highest-risk contact sheets. If the classifications hold up, test the hybrid fixed policy offline and then in shadow logs: unrestricted unchanged, small/strong penalties only for cross-quote reuse, and origin_quote_only excluded only outside its origin quote. Do not apply a blanket person penalty.", "", "## Contact sheets", ""]
    for path in output.get("contact_sheets") or []:
        lines.append(f"- `{path}`")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-dir", type=Path, default=DEFAULT_IMAGE_DIR)
    parser.add_argument("--origin-root", type=Path, default=DEFAULT_ORIGIN_ROOT)
    parser.add_argument("--generated-analysis", type=Path, default=DEFAULT_ANALYSIS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--review-dir", type=Path, default=DEFAULT_REVIEW_DIR)
    parser.add_argument("--log-file", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--face-correction-manifest", type=Path, default=DEFAULT_FACE_MANIFEST)
    parser.add_argument("--pre-face-backup", type=Path, default=DEFAULT_PRE_FACE_BACKUP)
    parser.add_argument("--basenames", nargs="*", default=[])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--sleep", type=float, default=0.5)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    contexts = validate_inputs(
        args.image_dir.resolve(),
        args.origin_root.resolve(),
        args.generated_analysis.resolve(),
        args.basenames or None,
        args.face_correction_manifest.resolve(),
        args.pre_face_backup.resolve(),
    )
    if args.limit > 0:
        contexts = contexts[: args.limit]
    output = load_output(args.output, args.model)
    output["input_count"] = len(list(args.image_dir.glob("tg_*.png")))
    pending = [context for context in contexts if args.overwrite or not item_is_current(output["items"].get(context["basename"]), context, args.model)]
    logging.info("Validated %d selected generated image(s); pending=%d completed=%d", len(contexts), len(pending), len(output["items"]))
    if args.dry_run:
        return 0
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        raise SystemExit("XAI_API_KEY is not set")
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "MrsMThatcher-IdentityAudit/1.0"})
    started = utc_now()
    succeeded = failed = 0
    for index, context in enumerate(pending, 1):
        logging.info("Auditing %d/%d %s grounded_people=%s", index, len(pending), context["basename"], context["grounded_people"])
        try:
            analysis, raw = call_xai(session, context, args.model, args.api_base, args.timeout, args.max_retries)
            output["items"][context["basename"]] = {
                "basename": context["basename"], "image_sha256": context["image_sha256"],
                "origin_quote_hash": context["origin_quote_hash"], "origin_quote": context["origin_quote"],
                "grounded_people": context["grounded_people"], "analysis_model": args.model,
                "prompt_version": PROMPT_VERSION, "analysed_at": utc_now(), "analysis": analysis,
                "response_id": raw.get("id"), "usage": raw.get("usage") if isinstance(raw.get("usage"), dict) else {},
            }
            output["failures"].pop(context["basename"], None)
            succeeded += 1
        except Exception as exc:
            logging.exception("Audit failed for %s", context["basename"])
            output["failures"][context["basename"]] = {"failed_at": utc_now(), "error": str(exc)}
            failed += 1
        output["updated_at"] = utc_now()
        atomic_write_json(args.output, output)
        if args.sleep > 0:
            time.sleep(args.sleep)
    groups = parse_recent_candidate_groups(args.log_file) if args.log_file.is_file() else []
    output["offline_policy_experiment"] = simulate_policies(groups, output["items"])
    output["contact_sheets"] = create_contact_sheets(output["items"], args.image_dir, args.review_dir)
    output["runs"].append({"started_at": started, "finished_at": utc_now(), "selected": len(contexts), "pending": len(pending), "succeeded": succeeded, "failed": failed, "model": args.model})
    output["updated_at"] = utc_now()
    atomic_write_json(args.output, output)
    diagnostic = "tg_faf99f3030693b0a55f0116194551792f3c4ea51261d7310eca7fb4d33b667d5.png"
    diagnostic_item = output["items"].get(diagnostic) or {}
    args.report.write_text(report_markdown(output, diagnostic_item.get("origin_quote", ""), "I place a profound belief - indeed a fervent faith - in the virtues of self reliance and personal independence. On these is founded the whole case for the free society.", 90.41531202375181), encoding="utf-8")
    logging.info("Audit complete items=%d failures=%d output=%s report=%s", len(output["items"]), len(output["failures"]), args.output, args.report)
    return 0 if not output["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
