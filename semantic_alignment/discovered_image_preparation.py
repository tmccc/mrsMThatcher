"""Offline preparation of reviewed archive-image candidates for possible integration."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import logging
import os
import re
import shutil
import statistics
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import unquote, urlparse

from jsonschema import Draft7Validator

import analyse_mrs_assets_xai_v4 as canonical
from .io import atomic_write_json, atomic_write_text, read_json, sha256_file
from .thatcher_image_hunt import (
    _baseline_image_path,
    _hash_distance,
    _image_fingerprints,
    classify_image_similarity,
    export_kept,
    load_project_environment,
    source_named_people_from_evidence,
)


ANALYSIS_COST_CEILING_USD = 1.0
CONSERVATIVE_CALL_COST_USD = 0.05
USD_TICKS_PER_DOLLAR = 10_000_000_000
SOURCE_GROUNDED_PROMPT_VERSION = "source-grounded-image-analysis-2026-07-16-v1"
PAIRING_DECISIONS = {
    "approve_pairing", "reject_pairing", "image_unsuitable",
    "metadata_needs_correction", "prefer_existing_image", "skip",
}
MAYBE_DECISIONS = {"promote_keep", "reject", "remain_maybe"}
PRODUCTION_READY_RIGHTS = {"public_domain", "clear_reuse", "attribution_required"}
PRODUCTION_HASH_PATHS = (
    "image_analysis.json",
    "generated_image_analysis.json",
    "mrsMThatcher2.py",
    "images",
    "generated_review_approved_images",
)

SOURCE_GROUNDED_SYSTEM_PROMPT = canonical.IMAGE_SYSTEM_PROMPT.replace(
    "Do not identify people by name.",
    "Do not identify people from facial appearance. Names explicitly supplied in the authoritative "
    "source record may and should be used; do not add any other identity.",
) + """

The user message contains an immutable SOURCE RECORD collected from an archive page. Treat its named
people, event, date and place as provenance, not as visual inference. Integrate supported identities
and event context into description, scene_summary, historical_context and pairing guidance where
editorially relevant. Do not reduce a source-identified Reagan, Bush, Carter, Gorbachev, summit or
other diplomatic scene to generic people or generic foreign policy. Distinguish allies, adversaries,
family members, hosts, meetings, ceremonies and specific events when the source record does.

Never infer a name from the pixels. Never add a person, event, place or date absent from SOURCE RECORD.
The output must still conform exactly to the canonical image-analysis schema.
""".strip()


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def effective_production_ready_records(work_dir: Path) -> list[dict[str, Any]]:
    """Return original production-ready keeps plus second-pass promoted maybes."""
    records = list(read_json(work_dir / "production_ready_manifest.json")["records"])
    maybe_records = {
        row["candidate_id"]: row for row in read_json(work_dir / "maybe_manifest.json").get("records") or []
    }
    maybe_reviews = read_json(work_dir / "maybe_second_pass_reviews.json").get("reviews") or {}
    for candidate_id in sorted(maybe_records):
        review = maybe_reviews.get(candidate_id) or {}
        if review.get("decision") != "promote_keep":
            continue
        row = json.loads(json.dumps(maybe_records[candidate_id]))
        if row.get("rights_status") not in PRODUCTION_READY_RIGHTS:
            raise RuntimeError(f"promoted maybe is not production-ready by rights: {candidate_id}")
        row["second_pass_promotion"] = review
        records.append(row)
    records.sort(key=lambda item: item["candidate_id"])
    if len({row["candidate_id"] for row in records}) != len(records):
        raise RuntimeError("effective production-ready manifest contains duplicate candidate IDs")
    atomic_write_json(work_dir / "effective_production_ready_manifest.json", {
        "schema_version": 1,
        "records": records,
        "original_production_ready_count": len(read_json(work_dir / "production_ready_manifest.json")["records"]),
        "promoted_maybe_count": sum("second_pass_promotion" in row for row in records),
    })
    return records


def _source_evidence_fields(record: dict[str, Any]) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    for key in (
        "page_title", "caption", "alt_text", "identity_evidence", "approximate_date",
        "archive_or_collection", "credit", "publisher",
    ):
        value = str(record.get(key) or "").strip()
        if value:
            fields.append((key, value))
    named_people = record.get("source_named_people") or []
    if named_people:
        fields.append(("source_named_people", json.dumps(named_people, ensure_ascii=False)))
    return fields


def _source_named_people(source_text: str, record: dict[str, Any]) -> list[str]:
    names = source_named_people_from_evidence(
        source_text,
        str(record.get("archive_or_collection") or ""),
        str(record.get("approximate_date") or ""),
        supplied_values=record.get("source_named_people") or [],
    )
    # This archive record says "President Bush" and is dated 1990 in the George Bush
    # Presidential Library collection. The source evidence identifies George H. W. Bush.
    if (
        "George H. W. Bush" not in names
        and re.search(r"\bPresident Bush\b", source_text, flags=re.IGNORECASE)
        and "1990" in str(record.get("approximate_date") or "")
        and "George Bush Presidential Library" in source_text
    ):
        names.append("George H. W. Bush")
    return sorted(set(names))


def _source_event_summary(record: dict[str, Any]) -> str:
    caption = str(record.get("caption") or "").strip()
    if caption and not re.fullmatch(r"Depicted people:\s*.+", caption, flags=re.IGNORECASE):
        value = caption
    else:
        value = str(record.get("page_title") or record.get("alt_text") or caption).strip()
    value = re.sub(r"^File:", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+-\s+Wikimedia Commons\s*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\.(?:jpe?g|png|webp)\s*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip()
    description_match = re.search(
        r"\bBeschrijving\s*:\s*(.+?)(?:\s+Datum\s*:|\s+Locatie\s*:|$)",
        value,
        flags=re.IGNORECASE,
    )
    if description_match:
        value = description_match.group(1).strip()
    value = re.split(r"\s*\((?:left to right|from left to right)[^)]*\)", value, maxsplit=1, flags=re.IGNORECASE)[0]
    value = re.sub(r"\s+-\s+DPLA\s+-\s+[0-9a-f]+\s*$", "", value, flags=re.IGNORECASE)
    if value.startswith(("“", '"')):
        closing = value.find("”.")
        if closing > 0:
            value = value[1:closing]
    return value[:2000]


def build_source_grounded_metadata(research_dir: Path, work_dir: Path) -> dict[str, Any]:
    """Build source grounded metadata."""
    records = effective_production_ready_records(work_dir)
    full_manifest = read_json(research_dir / "candidate_manifest.json")
    full_by_id = {row["candidate_id"]: row for row in full_manifest.get("records") or []}
    values: list[dict[str, Any]] = []
    for exported in records:
        candidate_id = exported["candidate_id"]
        if candidate_id not in full_by_id:
            raise RuntimeError(f"candidate missing from source manifest: {candidate_id}")
        source = full_by_id[candidate_id]
        fields = _source_evidence_fields(source)
        evidence_text = "\n".join(f"{key}: {value}" for key, value in fields)
        names = _source_named_people(evidence_text, source)
        if "Margaret Thatcher" not in names:
            raise RuntimeError(f"source evidence does not identify Margaret Thatcher: {candidate_id}")
        source_hash = hashlib.sha256(evidence_text.encode("utf-8")).hexdigest()
        values.append({
            "candidate_id": candidate_id,
            "image_sha256": exported["original_sha256"],
            "named_people": names,
            "source_event_summary": _source_event_summary(source),
            "approximate_date": str(source.get("approximate_date") or "").strip(),
            "identity_basis": source.get("identity_basis") or exported.get("identity_basis"),
            "identity_confidence": source.get("identity_confidence") or exported.get("identity_confidence"),
            "source_page_url": source.get("source_page_url") or exported.get("source_page_url"),
            "source_evidence_fields": [key for key, _value in fields],
            "source_evidence_text": evidence_text,
            "source_text_sha256": source_hash,
            "visual_model_identity_inference_allowed": False,
        })
    value = {
        "schema_version": 1,
        "candidate_count": len(values),
        "records": values,
        "generated_at": utc_now(),
    }
    atomic_write_json(work_dir / "source_grounded_identity_metadata.json", value)
    return value


def source_grounded_user_prompt(context: dict[str, Any]) -> str:
    """Return the source grounded user prompt."""
    bounded = {
        key: context.get(key)
        for key in (
            "candidate_id", "named_people", "source_event_summary", "approximate_date",
            "identity_basis", "identity_confidence", "source_page_url", "source_evidence_text",
        )
    }
    return (
        "Analyse the attached image using the canonical schema and this authoritative archive "
        "SOURCE RECORD. Use supplied identities as source facts, not facial recognition. Do not "
        "identify any additional person from facial appearance. Preserve the distinction between "
        "the specific event/relationship and generic diplomatic imagery.\n\nSOURCE RECORD:\n"
        + json.dumps(bounded, ensure_ascii=False, sort_keys=True, indent=2)
    )


def _truncate_bounded_prose(value: Any, limit: int) -> Any:
    if not isinstance(value, str) or len(value) <= limit:
        return value
    prefix = value[: max(0, limit - 3)].rstrip()
    sentence_end = max(prefix.rfind(". "), prefix.rfind("; "))
    if sentence_end >= limit // 2:
        return prefix[: sentence_end + 1]
    word_end = prefix.rfind(" ")
    if word_end >= limit // 2:
        prefix = prefix[:word_end]
    return prefix.rstrip(" ,.;:") + "..."


def normalise_source_grounded_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    """Repair harmless provider over-length prose without changing structured meaning."""
    value = json.loads(json.dumps(analysis))
    value["description"] = _truncate_bounded_prose(value.get("description"), 520)
    value["scene_summary"] = _truncate_bounded_prose(value.get("scene_summary"), 360)
    historical = value.get("historical_context")
    if isinstance(historical, dict):
        historical["event_or_context_hint"] = _truncate_bounded_prose(
            historical.get("event_or_context_hint"), 260,
        )
    pairing = value.get("pairing")
    if isinstance(pairing, dict):
        pairing["matching_summary"] = _truncate_bounded_prose(pairing.get("matching_summary"), 500)
    seasonality = value.get("seasonality")
    if isinstance(seasonality, dict):
        seasonality["explanation"] = _truncate_bounded_prose(seasonality.get("explanation"), 420)
    return value


def apply_source_identity_overlay(analysis: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Apply source identity overlay."""
    corrected = json.loads(json.dumps(analysis))
    names = [str(value) for value in context.get("named_people") or [] if str(value).strip()]
    if not names:
        return corrected
    event = str(context.get("source_event_summary") or context.get("event_or_period") or "").strip()
    date = str(context.get("approximate_date") or "").strip()
    identity_line = ", ".join(names)
    hint_parts = [identity_line]
    if event and identity_line.casefold() not in event.casefold():
        hint_parts.append(event)
    elif event:
        hint_parts = [event]
    if date and date not in " ".join(hint_parts):
        hint_parts.append(date)
    hint = ". ".join(part.rstrip(". ") for part in hint_parts if part).strip()
    if len(hint) > 260:
        names_prefix = identity_line[:200].rstrip(" ,")
        remaining = max(0, 260 - len(names_prefix) - len(". ") - len("..."))
        suffix = event[:remaining].rstrip(" ,.;")
        hint = (names_prefix + ((". " + suffix) if suffix else "") + "...")[:260]
    historical = corrected.setdefault("historical_context", {})
    confidence = {"high": 95, "medium": 80, "low": 60}.get(
        str(context.get("identity_confidence") or "").lower(), 60,
    )
    historical["event_or_context_hint"] = hint
    historical["confidence"] = max(int(historical.get("confidence") or 0), confidence)
    historical["specificity"] = "specific_event" if len(names) > 1 or bool(date) else "suggestive"

    description = str(corrected.get("description") or "").strip()
    if not all(name.casefold() in description.casefold() for name in names):
        prefix = f"Source-identified people: {identity_line}."
        corrected["description"] = (prefix + (" " + description if description else ""))[:520].rstrip()
    matching = corrected.setdefault("pairing", {}).get("matching_summary") or ""
    context_sentence = f"Source context: {hint}."
    if not all(name.casefold() in str(matching).casefold() for name in names):
        corrected["pairing"]["matching_summary"] = (
            context_sentence + (" " + str(matching).strip() if matching else "")
        )[:500].rstrip()
    return corrected


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    """Append jsonl."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)


def _safe_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        return []
    return sorted(
        item for item in path.rglob("*")
        if item.is_file() and item.name != ".DS_Store" and not item.name.startswith("._")
    )


def production_hashes(project_dir: Path) -> dict[str, Any]:
    """Return the production hashes."""
    records: list[dict[str, Any]] = []
    for relative in PRODUCTION_HASH_PATHS:
        root = project_dir / relative
        for path in _safe_files(root):
            records.append({
                "path": str(path.relative_to(project_dir)),
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            })
    payload = json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "schema_version": 1,
        "records": records,
        "aggregate_sha256": hashlib.sha256(payload).hexdigest(),
    }


def prepare_export(project_dir: Path, research_dir: Path, work_dir: Path) -> dict[str, Any]:
    """Prepare export."""
    work_dir.mkdir(parents=True, exist_ok=True)
    before_path = work_dir / "production_hashes_before.json"
    current_hashes = production_hashes(project_dir)
    if before_path.exists():
        before = read_json(before_path)
        if before.get("aggregate_sha256") != current_hashes["aggregate_sha256"]:
            raise RuntimeError("production image/code corpus changed since integration preparation began")
    else:
        atomic_write_json(before_path, current_hashes)
    export_dir = research_dir / "exported_kept"
    export_result = export_kept(research_dir, export_dir)
    exported = read_json(export_dir / "manifests/export_manifest.json")
    groups = {
        group: [row for row in exported["records"] if row["export_group"] == group]
        for group in ("production_ready", "rights_pending", "maybe")
    }
    if (len(groups["production_ready"]), len(groups["rights_pending"]), len(groups["maybe"])) != (22, 4, 2):
        raise RuntimeError("reviewed export does not reconcile to 22 production-ready, 4 rights-pending, 2 maybe")
    for name, group in (
        ("production_ready_manifest.json", "production_ready"),
        ("rights_pending_manifest.json", "rights_pending"),
        ("maybe_manifest.json", "maybe"),
    ):
        atomic_write_json(work_dir / name, {"schema_version": 1, "records": groups[group]})
    atomic_write_json(
        work_dir / "source_and_attribution_manifest.json",
        read_json(export_dir / "manifests/source_and_attribution_manifest.json"),
    )
    if not (work_dir / "human_pairing_review.json").exists():
        atomic_write_json(work_dir / "human_pairing_review.json", {"schema_version": 1, "reviews": {}, "updated_at": None})
    if not (work_dir / "human_pairing_review_audit.jsonl").exists():
        atomic_write_text(work_dir / "human_pairing_review_audit.jsonl", "")
    if not (work_dir / "maybe_second_pass_reviews.json").exists():
        atomic_write_json(work_dir / "maybe_second_pass_reviews.json", {"schema_version": 1, "reviews": {}, "updated_at": None})
    if not (work_dir / "maybe_second_pass_review_audit.jsonl").exists():
        atomic_write_text(work_dir / "maybe_second_pass_review_audit.jsonl", "")
    return {"export": export_result, "groups": {key: len(value) for key, value in groups.items()}}


def _candidate_path(research_dir: Path, row: dict[str, Any]) -> Path:
    return research_dir / "exported_kept" / row["export_relative_path"]


def _comparison_records(project_dir: Path, baseline: dict[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for filename in sorted(baseline["path_index"]):
        path = _baseline_image_path(project_dir / "image_analysis.json", filename)
        values.append({"collection": "original", "filename": filename, "path": path, "fingerprints": _image_fingerprints(path)})
    generated_analysis_path = project_dir / "generated_image_analysis.json"
    if generated_analysis_path.exists():
        generated = read_json(generated_analysis_path)
        for filename in sorted(generated.get("path_index") or {}):
            path = project_dir / "generated_review_approved_images" / filename
            if path.is_file():
                values.append({"collection": "generated", "filename": filename, "path": path, "fingerprints": _image_fingerprints(path)})
    return values


def duplicate_audit(project_dir: Path, research_dir: Path, work_dir: Path) -> dict[str, Any]:
    """Return the duplicate audit."""
    records = effective_production_ready_records(work_dir)
    baseline = read_json(project_dir / "image_analysis.json")
    comparisons = _comparison_records(project_dir, baseline)
    prior_candidates: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for row in sorted(records, key=lambda item: item["candidate_id"]):
        path = _candidate_path(research_dir, row)
        fingerprints = _image_fingerprints(path)
        matches: list[dict[str, Any]] = []
        for other in comparisons:
            similarity = classify_image_similarity(fingerprints, other["fingerprints"])
            if similarity == "visually_distinct":
                continue
            matches.append({
                "collection": other["collection"], "filename": other["filename"],
                "similarity": similarity,
                "phash_distance": _hash_distance(fingerprints["phash"], other["fingerprints"]["phash"]),
                "dhash_distance": _hash_distance(fingerprints["dhash"], other["fingerprints"]["dhash"]),
                "candidate_dimensions": [fingerprints["width"], fingerprints["height"]],
                "existing_dimensions": [other["fingerprints"]["width"], other["fingerprints"]["height"]],
            })
        for other in prior_candidates:
            similarity = classify_image_similarity(fingerprints, other["fingerprints"])
            if similarity != "visually_distinct":
                matches.append({
                    "collection": "approved_candidates", "candidate_id": other["candidate_id"],
                    "similarity": similarity,
                    "phash_distance": _hash_distance(fingerprints["phash"], other["fingerprints"]["phash"]),
                    "dhash_distance": _hash_distance(fingerprints["dhash"], other["fingerprints"]["dhash"]),
                    "candidate_dimensions": [fingerprints["width"], fingerprints["height"]],
                    "existing_dimensions": [other["fingerprints"]["width"], other["fingerprints"]["height"]],
                })
        classification = "visually_distinct"
        reason = "No exact, perceptual, crop-like or alternate-scan match passed the local similarity gates."
        if matches:
            severity = {"exact_duplicate": 0, "near_duplicate": 1, "probable_crop": 2, "alternate_scan": 3}
            best = min(matches, key=lambda item: severity.get(item["similarity"], 99))
            candidate_edge = max(best["candidate_dimensions"])
            existing_edge = max(best["existing_dimensions"])
            if candidate_edge >= existing_edge * 1.25 and best["collection"] != "approved_candidates":
                classification = "higher_quality_replacement"
                reason = f"Perceptually related to {best.get('filename')} with at least 25% more long-edge resolution."
            elif best["similarity"] == "near_duplicate":
                classification = "near_duplicate"
                reason = f"Close perceptual match to {best.get('filename') or best.get('candidate_id')}."
            elif best["similarity"] == "alternate_scan":
                classification = "alternate_scan"
                reason = f"Likely alternate scan of {best.get('filename') or best.get('candidate_id')}."
            else:
                classification = "manual_review_required"
                reason = f"Possible crop or exact duplicate of {best.get('filename') or best.get('candidate_id')}; retain for audit."
        results.append({
            "candidate_id": row["candidate_id"], "original_sha256": row["original_sha256"],
            "classification": classification, "reason": reason,
            "fingerprints": fingerprints, "matches": matches,
        })
        prior_candidates.append({"candidate_id": row["candidate_id"], "fingerprints": fingerprints})
    summary = dict(sorted(Counter(row["classification"] for row in results).items()))
    value = {"schema_version": 1, "candidate_count": len(results), "classification_counts": summary, "records": results}
    atomic_write_json(work_dir / "duplicate_audit.json", value)
    lines = ["# Final Duplicate Audit", "", f"Audited {len(results)} production-ready candidates against 69 original analyses, locally available original files, generated images and each other.", "", "| Candidate | Classification | Explanation |", "|---|---|---|"]
    lines.extend(f"| `{row['candidate_id']}` | {row['classification']} | {row['reason']} |" for row in results)
    atomic_write_text(work_dir / "duplicate_audit.md", "\n".join(lines) + "\n")
    return value


def filename_plan(project_dir: Path, work_dir: Path) -> dict[str, Any]:
    """Return the filename plan."""
    records = effective_production_ready_records(work_dir)
    existing_names = {path.name.casefold() for path in (project_dir / "images").iterdir() if path.is_file()}
    baseline_names = {name.casefold() for name in read_json(project_dir / "image_analysis.json")["path_index"]}
    occupied = existing_names | baseline_names
    old_plan_path = work_dir / "filename_plan.json"
    old_plan = read_json(old_plan_path).get("records") if old_plan_path.exists() else []
    old_by_id = {row["candidate_id"]: row for row in old_plan or []}
    old_names = {
        str(row.get("proposed_name") or "").casefold()
        for row in old_by_id.values() if row.get("proposed_name")
    }
    numbers = [
        int(match.group(1)) for name in occupied | old_names
        if (match := re.fullmatch(r"t(\d+)\.[a-z0-9]+", name))
    ]
    next_number = max(numbers, default=0) + 1
    plans: list[dict[str, Any]] = []
    planned_names: set[str] = set()
    for row in sorted(records, key=lambda item: item["candidate_id"]):
        suffix = Path(row["original_filename"]).suffix.lower()
        old = old_by_id.get(row["candidate_id"])
        if old:
            proposed = str(old["proposed_name"])
        else:
            while True:
                proposed = f"t{next_number:02d}{suffix}"
                next_number += 1
                if proposed.casefold() not in occupied | planned_names:
                    break
        if proposed.casefold() in occupied or proposed.casefold() in planned_names:
            raise RuntimeError(f"filename plan collision: {proposed}")
        planned_names.add(proposed.casefold())
        plans.append({
            "candidate_id": row["candidate_id"], "source_export_path": row["export_relative_path"],
            "old_name": row["original_filename"], "proposed_name": proposed,
            "sha256": row["original_sha256"], "applied": False,
            "reverse_operation": {"from": proposed, "to": row["original_filename"]},
        })
    value = {
        "schema_version": 1,
        "next_available_number": next_number,
        "collision_checked_names": len(occupied | planned_names),
        "records": plans,
    }
    atomic_write_json(work_dir / "filename_plan.json", value)
    return value


def planned_filename_bounds(plan: dict[str, Any]) -> list[str]:
    """Return the planned filename bounds."""
    records = plan.get("records") or []
    if not records:
        return []

    def key(row: dict[str, Any]) -> tuple[int, str]:
        name = str(row.get("proposed_name") or "")
        match = re.fullmatch(r"t(\d+)\.[a-z0-9]+", name, flags=re.IGNORECASE)
        return (int(match.group(1)) if match else -1, name.casefold())

    ordered = sorted(records, key=key)
    return [ordered[0]["proposed_name"], ordered[-1]["proposed_name"]]


def analysis_preflight(project_dir: Path, work_dir: Path) -> dict[str, Any]:
    """Return the analysis preflight."""
    baseline = read_json(project_dir / "image_analysis.json")
    costs = []
    for item in baseline["items"].values():
        ticks = (item.get("usage") or {}).get("cost_in_usd_ticks")
        if type(ticks) is int:
            costs.append(ticks / USD_TICKS_PER_DOLLAR)
    count = len(read_json(work_dir / "production_ready_manifest.json")["records"])
    value = {
        "schema_version": 1, "network_calls_made": False,
        "candidate_count": count, "model": baseline["model"],
        "prompt_version": canonical.IMAGE_PROMPT_VERSION,
        "image_schema_version": canonical.IMAGE_DB_SCHEMA_VERSION,
        "identity_instruction_verified": "Do not identify people by name" in canonical.IMAGE_SYSTEM_PROMPT,
        "expected_cost_usd": round(count * statistics.mean(costs), 4),
        "observed_baseline_maximum_projection_usd": round(count * max(costs), 4),
        "hard_cost_ceiling_usd": ANALYSIS_COST_CEILING_USD,
        "required_exact_confirmation_usd": ANALYSIS_COST_CEILING_USD,
        "pending_count": count,
    }
    output = work_dir / "canonical_image_analysis.json"
    if output.exists():
        current = read_json(output)
        value["pending_count"] = sum(row["original_sha256"] not in current.get("items", {}) for row in read_json(work_dir / "production_ready_manifest.json")["records"])
    atomic_write_json(work_dir / "analysis_preflight.json", value)
    return value


def _fresh_analysis_db(project_dir: Path, work_dir: Path, model: str) -> dict[str, Any]:
    return {
        "schema_version": 3, "analysis_kind": "images",
        "prompt_version": canonical.IMAGE_PROMPT_VERSION, "model": model,
        "source": {"image_dir": str(work_dir.parent / "exported_kept/production_ready"), "isolated_research": True},
        "created_at": utc_now(), "updated_at": utc_now(), "path_index": {},
        "current_hashes": [], "file_metadata": {}, "items": {}, "failures": {},
    }


def analyse_candidates(project_dir: Path, research_dir: Path, work_dir: Path, *, execute: bool, confirmed_cost: float | None) -> dict[str, Any]:
    """Analyse candidates."""
    preflight = analysis_preflight(project_dir, work_dir)
    if not execute:
        return {"status": "dry_run", **preflight}
    if confirmed_cost != ANALYSIS_COST_CEILING_USD:
        raise RuntimeError(f"analysis requires exact --confirm-max-cost-usd {ANALYSIS_COST_CEILING_USD:g}")
    if not preflight["identity_instruction_verified"]:
        raise RuntimeError("canonical image prompt lacks the no-facial-identification instruction")
    load_project_environment(project_dir / "mrsMThatcher.env")
    api_key = os.getenv("XAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("XAI_API_KEY is not configured")
    records = read_json(work_dir / "production_ready_manifest.json")["records"]
    output = work_dir / "canonical_image_analysis.json"
    model = str(read_json(project_dir / "image_analysis.json")["model"])
    db = read_json(output) if output.exists() else _fresh_analysis_db(project_dir, work_dir, model)
    if db.get("model") != model or db.get("prompt_version") != canonical.IMAGE_PROMPT_VERSION:
        raise RuntimeError("existing isolated analysis cache uses a different model or prompt")
    known_ticks = sum(
        int((item.get("usage") or {}).get("cost_in_usd_ticks") or 0)
        for item in db.get("items", {}).values()
    )
    completed_now = 0
    for row in sorted(records, key=lambda item: item["candidate_id"]):
        image_hash = row["original_sha256"]
        existing = db["items"].get(image_hash)
        if canonical.item_is_current(existing, model, canonical.IMAGE_PROMPT_VERSION):
            continue
        if known_ticks / USD_TICKS_PER_DOLLAR + CONSERVATIVE_CALL_COST_USD > ANALYSIS_COST_CEILING_USD:
            raise RuntimeError("conservative next-call allowance would exceed the analysis cost ceiling")
        path = _candidate_path(research_dir, row)
        attempt = {"candidate_id": row["candidate_id"], "image_hash": image_hash, "model": model, "started_at": utc_now()}
        try:
            prepared = canonical.prepare_image(path)
            result = canonical.call_xai_structured(
                api_key=api_key,
                base_url=os.getenv("XAI_API_BASE_URL", canonical.DEFAULT_XAI_BASE_URL),
                model=model,
                messages=canonical.make_image_messages(prepared, "high"),
                schema_name="mrs_image_analysis",
                schema=canonical.IMAGE_ANALYSIS_SCHEMA,
                timeout_seconds=180,
                max_tokens=2200,
                max_retries=1,
            )
            errors = sorted(Draft7Validator(canonical.IMAGE_ANALYSIS_SCHEMA).iter_errors(result.content), key=lambda error: list(error.path))
            if errors:
                raise RuntimeError("schema validation failed: " + "; ".join(error.message for error in errors[:5]))
            ticks = result.usage.get("cost_in_usd_ticks")
            if type(ticks) is not int or ticks < 0:
                raise RuntimeError("successful xAI response lacks authoritative cost_in_usd_ticks")
            known_ticks += ticks
            db["items"][image_hash] = {
                "image_hash": image_hash, "paths": [row["export_relative_path"]],
                "source_size_bytes": path.stat().st_size, "source_suffix": path.suffix.lower(),
                "api_mime_type": prepared.mime_type, "preparation": prepared.preparation,
                "analysis_model": model, "prompt_version": canonical.IMAGE_PROMPT_VERSION,
                "analysed_at": utc_now(), "response_id": result.response_id,
                "usage": result.usage, "analysis": result.content,
                "identity_provenance": {
                    "basis": row["identity_basis"], "evidence": row["identity_evidence"],
                    "confidence": row["identity_confidence"], "model_was_not_asked_to_identify_person": True,
                },
            }
            db["failures"].pop(image_hash, None)
            attempt.update({"status": "completed", "response_id": result.response_id, "usage": result.usage})
            completed_now += 1
        except Exception as exc:
            db["failures"][image_hash] = {
                "candidate_id": row["candidate_id"], "error_type": type(exc).__name__,
                "error": str(exc)[:4000], "last_attempt_at": utc_now(),
            }
            attempt.update({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)[:4000]})
        finally:
            attempt["completed_at"] = utc_now()
            append_jsonl(work_dir / "canonical_analysis_attempts.jsonl", attempt)
            db["path_index"] = {
                item["paths"][0]: digest for digest, item in sorted(db["items"].items())
            }
            db["current_hashes"] = sorted(db["items"])
            db["updated_at"] = utc_now()
            atomic_write_json(output, db)
            atomic_write_json(work_dir / "analysis_cost_ledger.json", {
                "schema_version": 1, "known_cost_in_usd_ticks": known_ticks,
                "known_cost_usd": known_ticks / USD_TICKS_PER_DOLLAR,
                "hard_ceiling_usd": ANALYSIS_COST_CEILING_USD,
                "completed_count": len(db["items"]), "failure_count": len(db["failures"]),
            })
    return {
        "status": "completed" if len(db["items"]) == len(records) else "incomplete",
        "completed_now": completed_now, "completed_total": len(db["items"]),
        "failures": len(db["failures"]), "known_cost_usd": known_ticks / USD_TICKS_PER_DOLLAR,
    }


def source_grounded_analysis_preflight(project_dir: Path, research_dir: Path, work_dir: Path) -> dict[str, Any]:
    """Return the source grounded analysis preflight."""
    source_metadata = build_source_grounded_metadata(research_dir, work_dir)
    records = effective_production_ready_records(work_dir)
    baseline = read_json(project_dir / "image_analysis.json")
    costs = []
    for item in baseline["items"].values():
        ticks = (item.get("usage") or {}).get("cost_in_usd_ticks")
        if type(ticks) is int:
            costs.append(ticks / USD_TICKS_PER_DOLLAR)
    output = work_dir / "source_grounded_image_analysis.json"
    current = read_json(output) if output.exists() else {"items": {}}
    contexts = {row["image_sha256"]: row for row in source_metadata["records"]}
    pending = 0
    for row in records:
        existing = (current.get("items") or {}).get(row["original_sha256"])
        context = contexts[row["original_sha256"]]
        if not (
            isinstance(existing, dict)
            and existing.get("prompt_version") == SOURCE_GROUNDED_PROMPT_VERSION
            and existing.get("source_text_sha256") == context["source_text_sha256"]
            and existing.get("analysis_model") == baseline["model"]
        ):
            pending += 1
    prior_ticks = 0
    prior_ledger = work_dir / "analysis_cost_ledger.json"
    if prior_ledger.exists():
        prior_ticks = int(read_json(prior_ledger).get("known_cost_in_usd_ticks") or 0)
    billing_state = _source_grounded_billing_state(work_dir, current)
    grounded_ticks = sum(
        int(row.get("cost_in_usd_ticks") or 0)
        for row in billing_state["responses"].values()
    )
    unaccounted_response_count = _unaccounted_source_response_count(work_dir)
    ambiguous_exposure = unaccounted_response_count * CONSERVATIVE_CALL_COST_USD
    value = {
        "schema_version": 1,
        "network_calls_made": False,
        "candidate_count": len(records),
        "pending_count": pending,
        "model": baseline["model"],
        "prompt_version": SOURCE_GROUNDED_PROMPT_VERSION,
        "image_schema_version": canonical.IMAGE_DB_SCHEMA_VERSION,
        "source_identity_record_count": len(source_metadata["records"]),
        "expected_incremental_cost_usd": round(pending * statistics.mean(costs), 4),
        "observed_baseline_maximum_projection_usd": round(pending * max(costs), 4),
        "prior_pixel_only_analysis_cost_usd": prior_ticks / USD_TICKS_PER_DOLLAR,
        "source_grounded_cost_so_far_usd": grounded_ticks / USD_TICKS_PER_DOLLAR,
        "combined_known_cost_usd": (prior_ticks + grounded_ticks) / USD_TICKS_PER_DOLLAR,
        "unaccounted_provider_response_count": unaccounted_response_count,
        "ambiguous_possible_exposure_usd": ambiguous_exposure,
        "combined_known_plus_ambiguous_usd": (
            prior_ticks + grounded_ticks
        ) / USD_TICKS_PER_DOLLAR + ambiguous_exposure,
        "hard_combined_cost_ceiling_usd": ANALYSIS_COST_CEILING_USD,
        "required_exact_confirmation_usd": ANALYSIS_COST_CEILING_USD,
        "identity_source_only": True,
    }
    atomic_write_json(work_dir / "source_grounded_analysis_preflight.json", value)
    return value


def _source_grounded_messages(prepared: Any, context: dict[str, Any]) -> list[dict[str, Any]]:
    messages = canonical.make_image_messages(prepared, "high")
    messages[0]["content"] = SOURCE_GROUNDED_SYSTEM_PROMPT
    messages[1]["content"][-1]["text"] = source_grounded_user_prompt(context)
    return messages


def _source_grounded_billing_state(work_dir: Path, db: dict[str, Any]) -> dict[str, Any]:
    path = work_dir / "source_grounded_billed_responses.json"
    state = read_json(path) if path.exists() else {"schema_version": 1, "responses": {}}
    responses = state.setdefault("responses", {})
    for item in db.get("items", {}).values():
        response_id = item.get("response_id")
        ticks = (item.get("usage") or {}).get("cost_in_usd_ticks")
        if response_id and type(ticks) is int and response_id not in responses:
            responses[response_id] = {
                "candidate_id": (item.get("source_grounded_context") or {}).get("candidate_id"),
                "cost_in_usd_ticks": ticks,
                "status": "completed_before_billing_ledger",
            }
    atomic_write_json(path, state)
    return state


def _unaccounted_source_response_count(work_dir: Path) -> int:
    path = work_dir / "source_grounded_analysis_attempts.jsonl"
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            row.get("status") == "failed"
            and "schema validation failed" in str(row.get("error") or "")
            and not row.get("usage")
        ):
            count += 1
    return count


def _write_source_grounded_cost_ledger(work_dir: Path, db: dict[str, Any]) -> dict[str, Any]:
    prior_ticks = 0
    prior_ledger = work_dir / "analysis_cost_ledger.json"
    if prior_ledger.exists():
        prior_ticks = int(read_json(prior_ledger).get("known_cost_in_usd_ticks") or 0)
    billing_state = _source_grounded_billing_state(work_dir, db)
    grounded_ticks = sum(
        int(row.get("cost_in_usd_ticks") or 0)
        for row in billing_state["responses"].values()
    )
    unaccounted_response_count = _unaccounted_source_response_count(work_dir)
    ambiguous_exposure_ticks = int(
        round(unaccounted_response_count * CONSERVATIVE_CALL_COST_USD * USD_TICKS_PER_DOLLAR)
    )
    value = {
        "schema_version": 1,
        "prior_pixel_only_cost_in_usd_ticks": prior_ticks,
        "source_grounded_cost_in_usd_ticks": grounded_ticks,
        "combined_known_cost_in_usd_ticks": prior_ticks + grounded_ticks,
        "unaccounted_provider_response_count": unaccounted_response_count,
        "ambiguous_possible_exposure_in_usd_ticks": ambiguous_exposure_ticks,
        "ambiguous_possible_exposure_usd": ambiguous_exposure_ticks / USD_TICKS_PER_DOLLAR,
        "source_grounded_cost_usd": grounded_ticks / USD_TICKS_PER_DOLLAR,
        "combined_known_cost_usd": (prior_ticks + grounded_ticks) / USD_TICKS_PER_DOLLAR,
        "combined_known_plus_ambiguous_usd": (
            prior_ticks + grounded_ticks + ambiguous_exposure_ticks
        ) / USD_TICKS_PER_DOLLAR,
        "hard_combined_ceiling_usd": ANALYSIS_COST_CEILING_USD,
        "completed_count": len(db.get("items") or {}),
        "failure_count": len(db.get("failures") or {}),
    }
    atomic_write_json(work_dir / "source_grounded_analysis_cost_ledger.json", value)
    return value


def recover_source_grounded_raw(project_dir: Path, research_dir: Path, work_dir: Path) -> dict[str, Any]:
    """Recover source grounded raw."""
    output = work_dir / "source_grounded_image_analysis.json"
    if not output.exists():
        return {"recovered_count": 0, "remaining_failures": 0}
    db = read_json(output)
    if not db.get("failures"):
        _write_source_grounded_cost_ledger(work_dir, db)
        return {"recovered_count": 0, "remaining_failures": 0}
    metadata = build_source_grounded_metadata(research_dir, work_dir)
    contexts = {row["image_sha256"]: row for row in metadata["records"]}
    records = {row["original_sha256"]: row for row in effective_production_ready_records(work_dir)}
    raw_dir = work_dir / "source_grounded_raw_responses"
    raw_records = []
    if raw_dir.exists():
        for path in sorted(raw_dir.glob("*.json")):
            try:
                raw_records.append((path, read_json(path)))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
    recovered = 0
    for image_hash in sorted(list(db.get("failures") or {})):
        context = contexts.get(image_hash)
        row = records.get(image_hash)
        if not context or not row:
            continue
        candidates = [
            (path, raw) for path, raw in raw_records
            if raw.get("image_hash") == image_hash
            and raw.get("source_text_sha256") == context["source_text_sha256"]
        ]
        if not candidates:
            continue
        path, raw = max(candidates, key=lambda item: str(item[1].get("received_at") or ""))
        content = raw.get("content")
        if not isinstance(content, dict):
            continue
        analysis = normalise_source_grounded_analysis(
            apply_source_identity_overlay(normalise_source_grounded_analysis(content), context),
        )
        errors = sorted(
            Draft7Validator(canonical.IMAGE_ANALYSIS_SCHEMA).iter_errors(analysis),
            key=lambda error: list(error.path),
        )
        if errors:
            continue
        identity_text = " ".join((
            str(analysis.get("description") or ""),
            str((analysis.get("historical_context") or {}).get("event_or_context_hint") or ""),
            str((analysis.get("pairing") or {}).get("matching_summary") or ""),
        )).casefold()
        if any(name.casefold() not in identity_text for name in context["named_people"]):
            continue
        image_path = _candidate_path(research_dir, row)
        prepared = canonical.prepare_image(image_path)
        db.setdefault("items", {})[image_hash] = {
            "image_hash": image_hash,
            "paths": [row["export_relative_path"]],
            "source_size_bytes": image_path.stat().st_size,
            "source_suffix": image_path.suffix.lower(),
            "api_mime_type": prepared.mime_type,
            "preparation": prepared.preparation,
            "analysis_model": db["model"],
            "prompt_version": SOURCE_GROUNDED_PROMPT_VERSION,
            "source_text_sha256": context["source_text_sha256"],
            "analysed_at": raw.get("received_at") or utc_now(),
            "response_id": raw.get("response_id"),
            "usage": raw.get("usage") or {},
            "analysis": analysis,
            "source_grounded_context": context,
            "identity_provenance": {
                "basis": context["identity_basis"],
                "evidence": context["source_evidence_text"],
                "confidence": context["identity_confidence"],
                "named_people": context["named_people"],
                "model_was_not_asked_to_identify_person": True,
                "identities_bound_from_source_record": True,
            },
            "offline_recovery": {
                "raw_response_file": str(path.relative_to(work_dir)),
                "recovered_at": utc_now(),
                "normalisation": "bounded_prose_truncation_and_source_identity_overlay",
            },
        }
        db["failures"].pop(image_hash, None)
        recovered += 1
        append_jsonl(work_dir / "source_grounded_analysis_attempts.jsonl", {
            "candidate_id": row["candidate_id"],
            "image_hash": image_hash,
            "response_id": raw.get("response_id"),
            "status": "recovered_from_saved_response",
            "raw_response_file": str(path.relative_to(work_dir)),
            "completed_at": utc_now(),
        })
    db["path_index"] = {item["paths"][0]: digest for digest, item in sorted(db.get("items", {}).items())}
    db["current_hashes"] = sorted(db.get("items", {}))
    db["updated_at"] = utc_now()
    atomic_write_json(output, db)
    _write_source_grounded_cost_ledger(work_dir, db)
    return {"recovered_count": recovered, "remaining_failures": len(db.get("failures") or {})}


def refresh_source_grounded_overlays(research_dir: Path, work_dir: Path) -> dict[str, Any]:
    """Return the refresh source grounded overlays."""
    output = work_dir / "source_grounded_image_analysis.json"
    if not output.exists():
        return {"updated_count": 0, "reason": "source-grounded analysis does not exist"}
    metadata = build_source_grounded_metadata(research_dir, work_dir)
    contexts = {row["image_sha256"]: row for row in metadata["records"]}
    db = read_json(output)
    updated = 0
    for image_hash, item in sorted((db.get("items") or {}).items()):
        new_context = contexts.get(image_hash)
        if not new_context:
            continue
        analysis = json.loads(json.dumps(item["analysis"]))
        old_hint = str((analysis.get("historical_context") or {}).get("event_or_context_hint") or "")
        pairing = analysis.get("pairing") if isinstance(analysis.get("pairing"), dict) else {}
        matching = str(pairing.get("matching_summary") or "")
        old_prefix = f"Source context: {old_hint}."
        if matching.startswith(old_prefix):
            pairing["matching_summary"] = matching[len(old_prefix):].lstrip()
        corrected = normalise_source_grounded_analysis(
            apply_source_identity_overlay(normalise_source_grounded_analysis(analysis), new_context),
        )
        item["analysis"] = corrected
        item["source_grounded_context"] = new_context
        item["source_text_sha256"] = new_context["source_text_sha256"]
        item["source_overlay_refreshed_at"] = utc_now()
        updated += 1
    db["updated_at"] = utc_now()
    atomic_write_json(output, db)
    return {"updated_count": updated, "candidate_count": len(contexts)}


def audit_source_grounded_metadata(research_dir: Path, work_dir: Path) -> dict[str, Any]:
    """Audit source grounded metadata."""
    metadata = build_source_grounded_metadata(research_dir, work_dir)
    db = read_json(work_dir / "source_grounded_image_analysis.json")
    _write_source_grounded_cost_ledger(work_dir, db)
    missing_items: list[str] = []
    missing_bindings: list[dict[str, Any]] = []
    schema_failures: list[dict[str, Any]] = []
    named_reference_count = 0
    person_counts: Counter[str] = Counter()
    for context in metadata["records"]:
        image_hash = context["image_sha256"]
        item = (db.get("items") or {}).get(image_hash)
        if not item:
            missing_items.append(context["candidate_id"])
            continue
        analysis = item.get("analysis") or {}
        errors = sorted(
            Draft7Validator(canonical.IMAGE_ANALYSIS_SCHEMA).iter_errors(analysis),
            key=lambda error: list(error.path),
        )
        if errors:
            schema_failures.append({
                "candidate_id": context["candidate_id"],
                "errors": [error.message for error in errors[:10]],
            })
        text = " ".join((
            str(analysis.get("description") or ""),
            str(analysis.get("scene_summary") or ""),
            str((analysis.get("historical_context") or {}).get("event_or_context_hint") or ""),
            str((analysis.get("pairing") or {}).get("matching_summary") or ""),
        )).casefold()
        named_reference_count += len(context["named_people"])
        person_counts.update(context["named_people"])
        missing = [name for name in context["named_people"] if name.casefold() not in text]
        if missing:
            missing_bindings.append({"candidate_id": context["candidate_id"], "missing_names": missing})
    passed = not missing_items and not missing_bindings and not schema_failures and not db.get("failures")
    value = {
        "schema_version": 1,
        "passed": passed,
        "candidate_count": len(metadata["records"]),
        "completed_analysis_count": len(db.get("items") or {}),
        "named_person_reference_count": named_reference_count,
        "named_person_counts": dict(sorted(person_counts.items())),
        "candidates_with_multiple_named_people": sum(
            len(row["named_people"]) > 1 for row in metadata["records"]
        ),
        "missing_analysis_items": missing_items,
        "missing_identity_bindings": missing_bindings,
        "schema_failures": schema_failures,
        "analysis_failure_count": len(db.get("failures") or {}),
        "identity_method": "authoritative source caption, archive metadata, or source page context; never facial inference",
        "generated_at": utc_now(),
    }
    atomic_write_json(work_dir / "source_grounded_metadata_audit.json", value)
    lines = [
        "# Source-Grounded Metadata Audit", "",
        f"- Result: {'PASS' if passed else 'FAIL'}",
        f"- Candidates: {value['candidate_count']}",
        f"- Completed schema-v3 analyses: {value['completed_analysis_count']}",
        f"- Named-person references bound: {named_reference_count}",
        f"- Multi-person images: {value['candidates_with_multiple_named_people']}",
        f"- Missing identity bindings: {len(missing_bindings)}",
        f"- Schema failures: {len(schema_failures)}", "",
        "Identities were taken from authoritative source captions, archive metadata or source-page "
        "context. The visual model was prohibited from inferring identity from facial appearance.",
    ]
    lines.extend(["", "## Source-identified people", "", "| Person | Images |", "|---|---:|"])
    lines.extend(f"| {name} | {count} |" for name, count in sorted(person_counts.items()))
    atomic_write_text(work_dir / "source_grounded_metadata_audit.md", "\n".join(lines) + "\n")
    status = {
        "schema_version": 2,
        "automated_source_grounded_validation": "passed" if passed else "failed",
        "human_metadata_validation": "not_performed",
        "absence_of_pairing_correction_flags_is_metadata_approval": False,
        "source_identity_method": value["identity_method"],
        "candidate_count": value["candidate_count"],
        "safe_for_offline_selector_dry_run": passed,
        "production_integration_applied": False,
        "generated_at": utc_now(),
    }
    atomic_write_json(work_dir / "metadata_review_status.json", status)
    return value


def analyse_source_grounded_candidates(
    project_dir: Path,
    research_dir: Path,
    work_dir: Path,
    *,
    execute: bool,
    confirmed_cost: float | None,
) -> dict[str, Any]:
    """Analyse source grounded candidates."""
    preflight = source_grounded_analysis_preflight(project_dir, research_dir, work_dir)
    if not execute:
        return {"status": "dry_run", **preflight}
    if confirmed_cost != ANALYSIS_COST_CEILING_USD:
        raise RuntimeError(f"source-grounded analysis requires exact --confirm-max-cost-usd {ANALYSIS_COST_CEILING_USD:g}")
    load_project_environment(project_dir / "mrsMThatcher.env")
    api_key = os.getenv("XAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("XAI_API_KEY is not configured")
    offline_recovery = recover_source_grounded_raw(project_dir, research_dir, work_dir)
    records = effective_production_ready_records(work_dir)
    metadata = build_source_grounded_metadata(research_dir, work_dir)
    context_by_hash = {row["image_sha256"]: row for row in metadata["records"]}
    model = str(read_json(project_dir / "image_analysis.json")["model"])
    output = work_dir / "source_grounded_image_analysis.json"
    if output.exists():
        db = read_json(output)
    else:
        db = _fresh_analysis_db(project_dir, work_dir, model)
        db["prompt_version"] = SOURCE_GROUNDED_PROMPT_VERSION
        db["analysis_kind"] = "source_grounded_images"
    if db.get("model") != model or db.get("prompt_version") != SOURCE_GROUNDED_PROMPT_VERSION:
        raise RuntimeError("existing source-grounded cache uses a different model or prompt")
    prior_ticks = 0
    if (work_dir / "analysis_cost_ledger.json").exists():
        prior_ticks = int(read_json(work_dir / "analysis_cost_ledger.json").get("known_cost_in_usd_ticks") or 0)
    billing_state = _source_grounded_billing_state(work_dir, db)
    grounded_ticks = sum(
        int(row.get("cost_in_usd_ticks") or 0)
        for row in billing_state["responses"].values()
    )
    unaccounted_response_count = _unaccounted_source_response_count(work_dir)
    ambiguous_exposure_ticks = int(
        round(unaccounted_response_count * CONSERVATIVE_CALL_COST_USD * USD_TICKS_PER_DOLLAR)
    )
    completed_now = 0
    for row in sorted(records, key=lambda item: item["candidate_id"]):
        image_hash = row["original_sha256"]
        context = context_by_hash[image_hash]
        existing = db["items"].get(image_hash)
        if (
            isinstance(existing, dict)
            and existing.get("analysis_model") == model
            and existing.get("prompt_version") == SOURCE_GROUNDED_PROMPT_VERSION
            and existing.get("source_text_sha256") == context["source_text_sha256"]
        ):
            continue
        combined_exposure = (prior_ticks + grounded_ticks + ambiguous_exposure_ticks) / USD_TICKS_PER_DOLLAR
        if combined_exposure + CONSERVATIVE_CALL_COST_USD > ANALYSIS_COST_CEILING_USD:
            raise RuntimeError("conservative next-call allowance would exceed combined analysis cost ceiling")
        path = _candidate_path(research_dir, row)
        attempt = {
            "candidate_id": row["candidate_id"], "image_hash": image_hash,
            "source_text_sha256": context["source_text_sha256"], "model": model,
            "prompt_version": SOURCE_GROUNDED_PROMPT_VERSION, "started_at": utc_now(),
        }
        result = None
        try:
            prepared = canonical.prepare_image(path)
            result = canonical.call_xai_structured(
                api_key=api_key,
                base_url=os.getenv("XAI_API_BASE_URL", canonical.DEFAULT_XAI_BASE_URL),
                model=model,
                messages=_source_grounded_messages(prepared, context),
                schema_name="mrs_source_grounded_image_analysis",
                schema=canonical.IMAGE_ANALYSIS_SCHEMA,
                timeout_seconds=180,
                max_tokens=2400,
                max_retries=1,
            )
            ticks = result.usage.get("cost_in_usd_ticks")
            if type(ticks) is not int or ticks < 0:
                raise RuntimeError("successful xAI response lacks authoritative cost_in_usd_ticks")
            response_id = str(result.response_id or "").strip()
            if not response_id:
                raise RuntimeError("successful xAI response lacks response ID")
            raw_record = {
                "schema_version": 1,
                "candidate_id": row["candidate_id"],
                "image_hash": image_hash,
                "source_text_sha256": context["source_text_sha256"],
                "response_id": response_id,
                "usage": result.usage,
                "content": result.content,
                "received_at": utc_now(),
            }
            atomic_write_json(work_dir / "source_grounded_raw_responses" / f"{response_id}.json", raw_record)
            if response_id not in billing_state["responses"]:
                billing_state["responses"][response_id] = {
                    "candidate_id": row["candidate_id"],
                    "cost_in_usd_ticks": ticks,
                    "status": "provider_response_received",
                }
                atomic_write_json(work_dir / "source_grounded_billed_responses.json", billing_state)
                grounded_ticks += ticks
            attempt.update({"response_id": response_id, "usage": result.usage})
            analysis = apply_source_identity_overlay(
                normalise_source_grounded_analysis(result.content), context,
            )
            analysis = normalise_source_grounded_analysis(analysis)
            errors = sorted(
                Draft7Validator(canonical.IMAGE_ANALYSIS_SCHEMA).iter_errors(analysis),
                key=lambda error: list(error.path),
            )
            if errors:
                raise RuntimeError("schema validation failed: " + "; ".join(error.message for error in errors[:5]))
            text = " ".join((
                str(analysis.get("description") or ""),
                str((analysis.get("historical_context") or {}).get("event_or_context_hint") or ""),
                str((analysis.get("pairing") or {}).get("matching_summary") or ""),
            )).casefold()
            missing_names = [name for name in context["named_people"] if name.casefold() not in text]
            if missing_names:
                raise RuntimeError("source identities missing from corrected analysis: " + ", ".join(missing_names))
            db["items"][image_hash] = {
                "image_hash": image_hash,
                "paths": [row["export_relative_path"]],
                "source_size_bytes": path.stat().st_size,
                "source_suffix": path.suffix.lower(),
                "api_mime_type": prepared.mime_type,
                "preparation": prepared.preparation,
                "analysis_model": model,
                "prompt_version": SOURCE_GROUNDED_PROMPT_VERSION,
                "source_text_sha256": context["source_text_sha256"],
                "analysed_at": utc_now(),
                "response_id": result.response_id,
                "usage": result.usage,
                "analysis": analysis,
                "source_grounded_context": context,
                "identity_provenance": {
                    "basis": context["identity_basis"],
                    "evidence": context["source_evidence_text"],
                    "confidence": context["identity_confidence"],
                    "named_people": context["named_people"],
                    "model_was_not_asked_to_identify_person": True,
                    "identities_bound_from_source_record": True,
                },
            }
            db["failures"].pop(image_hash, None)
            attempt.update({"status": "completed", "response_id": result.response_id, "usage": result.usage})
            completed_now += 1
        except Exception as exc:
            db["failures"][image_hash] = {
                "candidate_id": row["candidate_id"], "error_type": type(exc).__name__,
                "error": str(exc)[:4000], "last_attempt_at": utc_now(),
            }
            attempt.update({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)[:4000]})
        finally:
            attempt["completed_at"] = utc_now()
            append_jsonl(work_dir / "source_grounded_analysis_attempts.jsonl", attempt)
            db["path_index"] = {item["paths"][0]: digest for digest, item in sorted(db["items"].items())}
            db["current_hashes"] = sorted(db["items"])
            db["updated_at"] = utc_now()
            atomic_write_json(output, db)
            atomic_write_json(work_dir / "source_grounded_analysis_cost_ledger.json", {
                "schema_version": 1,
                "prior_pixel_only_cost_in_usd_ticks": prior_ticks,
                "source_grounded_cost_in_usd_ticks": grounded_ticks,
                "combined_known_cost_in_usd_ticks": prior_ticks + grounded_ticks,
                "unaccounted_provider_response_count": unaccounted_response_count,
                "ambiguous_possible_exposure_in_usd_ticks": ambiguous_exposure_ticks,
                "ambiguous_possible_exposure_usd": ambiguous_exposure_ticks / USD_TICKS_PER_DOLLAR,
                "source_grounded_cost_usd": grounded_ticks / USD_TICKS_PER_DOLLAR,
                "combined_known_cost_usd": (prior_ticks + grounded_ticks) / USD_TICKS_PER_DOLLAR,
                "combined_known_plus_ambiguous_usd": (
                    prior_ticks + grounded_ticks + ambiguous_exposure_ticks
                ) / USD_TICKS_PER_DOLLAR,
                "hard_combined_ceiling_usd": ANALYSIS_COST_CEILING_USD,
                "completed_count": len(db["items"]),
                "failure_count": len(db["failures"]),
            })
    return {
        "status": "completed" if len(db["items"]) == len(records) else "incomplete",
        "completed_now": completed_now,
        "completed_total": len(db["items"]),
        "failures": len(db["failures"]),
        "source_grounded_cost_usd": grounded_ticks / USD_TICKS_PER_DOLLAR,
        "combined_known_cost_usd": (prior_ticks + grounded_ticks) / USD_TICKS_PER_DOLLAR,
        "ambiguous_possible_exposure_usd": ambiguous_exposure_ticks / USD_TICKS_PER_DOLLAR,
        "offline_recovered_now": offline_recovery["recovered_count"],
    }


def _combined_image_db(project_dir: Path, work_dir: Path) -> tuple[dict[str, Any], dict[str, str]]:
    baseline = read_json(project_dir / "image_analysis.json")
    source_grounded = work_dir / "source_grounded_image_analysis.json"
    new = read_json(source_grounded if source_grounded.exists() else work_dir / "canonical_image_analysis.json")
    plan = read_json(work_dir / "filename_plan.json")["records"]
    proposed = {row["candidate_id"]: row["proposed_name"] for row in plan}
    by_hash = {row["sha256"]: row for row in plan}
    merged = json.loads(json.dumps(baseline))
    for digest, item in new["items"].items():
        merged["items"][digest] = item
        name = by_hash[digest]["proposed_name"]
        merged["path_index"][name] = digest
    merged["current_hashes"] = sorted(merged["items"])
    return merged, proposed


def _load_production_scorer() -> Any:
    """Import the production module without allowing import-time diagnostics into CLI JSON."""
    previous_disable_level = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        return importlib.import_module("mrsMThatcher2")
    finally:
        logging.disable(previous_disable_level)


def quote_matching_dry_run(project_dir: Path, work_dir: Path) -> dict[str, Any]:
    """Return the quote matching dry run."""
    source_grounded_active = (work_dir / "source_grounded_image_analysis.json").exists()
    prior_matching = None
    prior_pair_ids: set[str] = set()
    matching_path = work_dir / "quote_matching_dry_run.json"
    if source_grounded_active:
        audit = audit_source_grounded_metadata(work_dir.parent, work_dir)
        if not audit["passed"]:
            raise RuntimeError("source-grounded metadata audit failed; refusing selector dry run")
    if source_grounded_active and matching_path.exists():
        backup_json = work_dir / "quote_matching_dry_run_pre_source_grounded.json"
        backup_md = work_dir / "quote_matching_dry_run_pre_source_grounded.md"
        backup_reviews = work_dir / "human_pairing_review_pre_source_grounded.json"
        if not backup_json.exists():
            shutil.copy2(matching_path, backup_json)
        if (work_dir / "quote_matching_dry_run.md").exists() and not backup_md.exists():
            shutil.copy2(work_dir / "quote_matching_dry_run.md", backup_md)
        if (work_dir / "human_pairing_review.json").exists() and not backup_reviews.exists():
            shutil.copy2(work_dir / "human_pairing_review.json", backup_reviews)
        prior_matching = read_json(backup_json)
        prior_pair_ids = {row["pair_id"] for row in _review_pairs_from_matching(prior_matching)}
    bot = _load_production_scorer()
    merged, proposed = _combined_image_db(project_dir, work_dir)
    baseline = read_json(project_dir / "image_analysis.json")
    quotes = read_json(project_dir / "quote_analysis.json")
    source_grounded = work_dir / "source_grounded_image_analysis.json"
    new = read_json(source_grounded if source_grounded.exists() else work_dir / "canonical_image_analysis.json")
    manifest = effective_production_ready_records(work_dir)
    hash_by_candidate = {row["candidate_id"]: row["original_sha256"] for row in manifest}
    idf = bot.build_image_topic_idf(merged)
    today = datetime.now().strftime("%m-%d")
    baseline_competitors: dict[str, tuple[float | None, str | None, dict[str, float]]] = {}
    for quote_hash, quote_item in sorted(quotes["items"].items()):
        competitors = []
        for filename, existing_hash in baseline["path_index"].items():
            existing_analysis = baseline["items"][existing_hash]["analysis"]
            existing_score, existing_components, existing_eligible = bot.score_image_for_quote(
                quote_item["analysis"], existing_analysis, idf,
            )
            if existing_eligible and not bot.image_is_out_of_season(existing_analysis, today):
                competitors.append((existing_score, filename, existing_components))
        baseline_competitors[quote_hash] = (
            max(competitors, key=lambda item: (item[0], item[1]))
            if competitors else (None, None, {})
        )
    broad_dominance_threshold = max(10, min(50, len(quotes["items"]) // 10))
    results: list[dict[str, Any]] = []
    suspicious: list[dict[str, Any]] = []
    for candidate_id in sorted(proposed):
        digest = hash_by_candidate[candidate_id]
        analysis = new["items"][digest]["analysis"]
        quote_rows: list[dict[str, Any]] = []
        for quote_hash, quote_item in sorted(quotes["items"].items()):
            quote_analysis = quote_item["analysis"]
            score, components, eligible = bot.score_image_for_quote(quote_analysis, analysis, idf)
            seasonal_excluded = bot.image_is_out_of_season(analysis, today)
            competitor = baseline_competitors[quote_hash]
            would_win = bool(eligible and not seasonal_excluded and (competitor[0] is None or score > competitor[0]))
            useful_components = sum(float(components.get(key, 0)) for key in ("topics", "tone_mood", "scene_activity_symbols", "historical", "visual_energy"))
            suspicious_pairing = would_win and useful_components <= 2
            row = {
                "quote_hash": quote_hash, "quote_text": quote_item["text"],
                "score": round(score, 6), "components": components, "eligible": eligible,
                "seasonally_excluded_now": seasonal_excluded,
                "nearest_competing_existing_image": competitor[1],
                "nearest_competing_score": round(competitor[0], 6) if competitor[0] is not None else None,
                "nearest_competing_components": competitor[2],
                "would_win_when_all_originals_available": would_win,
                "score_margin": round(score - competitor[0], 6) if competitor[0] is not None else None,
                "suspicious_pairing": suspicious_pairing,
            }
            if eligible:
                quote_rows.append(row)
            if suspicious_pairing:
                suspicious.append({"candidate_id": candidate_id, **row})
        quote_rows.sort(key=lambda item: (-item["score"], item["quote_hash"]))
        wins = [row for row in quote_rows if row["would_win_when_all_originals_available"]]
        broad = len(wins) > broad_dominance_threshold
        results.append({
            "candidate_id": candidate_id, "proposed_name": proposed[candidate_id],
            "eligible_quote_count": len(quote_rows), "eligible_quote_ids": [row["quote_hash"] for row in quote_rows],
            "competitive_win_count": len(wins), "top_proposed_matches": quote_rows[:20],
            "top_winning_matches": wins[:10], "never_competitive": not wins,
            "dominates_too_many_unrelated_quotes": broad,
            "materially_improves_coverage": len(wins) >= 5 and not broad,
            "seasonality": analysis.get("seasonality") or {},
            "rights_status": next(row["rights_status"] for row in manifest if row["candidate_id"] == candidate_id),
        })
    value = {
        "schema_version": 1, "selector": "mrsMThatcher2.score_image_for_quote",
        "analysis_source": (
            "source_grounded_image_analysis.json" if source_grounded_active
            else "canonical_image_analysis.json"
        ),
        "state_mutated": False, "quote_count": len(quotes["items"]),
        "candidate_count": len(results), "records": results,
        "broad_dominance_win_threshold": broad_dominance_threshold,
        "never_competitive_count": sum(row["never_competitive"] for row in results),
        "broad_dominance_count": sum(row["dominates_too_many_unrelated_quotes"] for row in results),
        "material_coverage_improvement_count": sum(row["materially_improves_coverage"] for row in results),
        "suspicious_pairings": suspicious,
        "interpretation": "Offline, state-free comparison with every original image available; actual production winners remain cycle/history dependent.",
    }
    atomic_write_json(work_dir / "quote_matching_dry_run.json", value)
    lines = ["# Quote Matching Dry Run", "", value["interpretation"], "", "| Candidate | Eligible quotes | Top-score wins | Coverage improvement | Broad dominance |", "|---|---:|---:|---|---|"]
    lines.extend(
        f"| `{row['candidate_id']}` | {row['eligible_quote_count']} | {row['competitive_win_count']} | {'yes' if row['materially_improves_coverage'] else 'no'} | {'yes' if row['dominates_too_many_unrelated_quotes'] else 'no'} |"
        for row in results
    )
    atomic_write_text(work_dir / "quote_matching_dry_run.md", "\n".join(lines) + "\n")
    if source_grounded_active:
        current_pair_ids = {row["pair_id"] for row in _review_pairs_from_matching(value)}
        review_ids = set((read_json(work_dir / "human_pairing_review.json").get("reviews") or {}))
        prior_pairs = {
            row["pair_id"]: row for row in _review_pairs_from_matching(prior_matching or {"records": []})
        }
        current_pairs = {
            row["pair_id"]: row for row in _review_pairs_from_matching(value)
        }
        changed_score_ids = sorted(
            pair_id for pair_id in prior_pair_ids & current_pair_ids
            if (
                prior_pairs[pair_id].get("score") != current_pairs[pair_id].get("score")
                or prior_pairs[pair_id].get("components") != current_pairs[pair_id].get("components")
            )
        )
        migration = {
            "schema_version": 1,
            "prior_pair_count": len(prior_pair_ids),
            "current_pair_count": len(current_pair_ids),
            "unchanged_pair_count": len(prior_pair_ids & current_pair_ids),
            "removed_pair_count": len(prior_pair_ids - current_pair_ids),
            "new_pair_count": len(current_pair_ids - prior_pair_ids),
            "preserved_review_count": len(current_pair_ids & review_ids),
            "historical_review_count": len(review_ids),
            "unchanged_pair_ids_with_recalculated_scores": changed_score_ids,
            "new_unreviewed_pair_ids": sorted(current_pair_ids - review_ids),
            "removed_reviewed_pair_ids": sorted((prior_pair_ids - current_pair_ids) & review_ids),
            "reason": "Source-grounded named-person and event metadata replaced generic pixel-only metadata.",
            "generated_at": utc_now(),
        }
        atomic_write_json(work_dir / "pairing_review_source_metadata_migration.json", migration)
    return value


def _review_pairs_from_matching(matching: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in matching["records"]:
        selected = candidate["top_winning_matches"][:5] or candidate["top_proposed_matches"][:3]
        for match in selected:
            rows.append({"pair_id": f"{candidate['candidate_id']}:{match['quote_hash']}", "candidate_id": candidate["candidate_id"], **match})
    return rows


def _review_pairs(work_dir: Path) -> list[dict[str, Any]]:
    return _review_pairs_from_matching(read_json(work_dir / "quote_matching_dry_run.json"))


def review_source_records(work_dir: Path) -> dict[str, dict[str, Any]]:
    """Return every reviewable candidate, including second-pass promotions."""
    return {row["candidate_id"]: row for row in effective_production_ready_records(work_dir)}


def pairing_review_state(research_dir: Path, work_dir: Path) -> dict[str, Any]:
    """Return the pairing review state."""
    source = {row["candidate_id"]: row for row in effective_production_ready_records(work_dir)}
    source_grounded = work_dir / "source_grounded_image_analysis.json"
    analysis = read_json(source_grounded if source_grounded.exists() else work_dir / "canonical_image_analysis.json")
    plan = {row["candidate_id"]: row for row in read_json(work_dir / "filename_plan.json")["records"]}
    review = read_json(work_dir / "human_pairing_review.json")
    records = []
    for pair in _review_pairs(work_dir):
        candidate = source[pair["candidate_id"]]
        records.append({
            **pair, "source": {key: candidate.get(key) for key in ("publisher", "caption", "rights_status", "required_attribution_wording")},
            "analysis_summary": analysis["items"][candidate["original_sha256"]]["analysis"]["scene_summary"],
            "new_image_url": f"/new/{pair['candidate_id']}",
            "existing_image_url": f"/existing/{pair['nearest_competing_existing_image']}" if pair["nearest_competing_existing_image"] else None,
            "proposed_name": plan[pair["candidate_id"]]["proposed_name"],
        })
    reviews = review.get("reviews") or {}
    current_ids = {row["pair_id"] for row in records}
    return {
        "records": records,
        "reviews": reviews,
        "reviewed_count": len(current_ids.intersection(reviews)),
        "unreviewed_count": len(current_ids - set(reviews)),
    }


def save_pairing_review(work_dir: Path, pair_id: str, decision: str, note: str) -> dict[str, Any]:
    """Save pairing review."""
    if decision not in PAIRING_DECISIONS:
        raise ValueError("invalid pairing-review decision")
    valid_ids = {row["pair_id"] for row in _review_pairs(work_dir)}
    if pair_id not in valid_ids:
        raise ValueError("unknown pairing")
    path = work_dir / "human_pairing_review.json"
    state = read_json(path)
    previous = (state.get("reviews") or {}).get(pair_id)
    clean_note = str(note).strip()[:4000]
    if previous and previous.get("decision") == decision and previous.get("note") == clean_note:
        return state
    revision = int((previous or {}).get("revision") or 0) + 1
    current = {"pair_id": pair_id, "decision": decision, "note": clean_note, "revision": revision, "reviewed_at": utc_now()}
    state.setdefault("reviews", {})[pair_id] = current
    state["updated_at"] = utc_now()
    atomic_write_json(path, state)
    append_jsonl(work_dir / "human_pairing_review_audit.jsonl", {"pair_id": pair_id, "previous": previous, "new": current, "timestamp": utc_now()})
    return state


def save_maybe_review(work_dir: Path, candidate_id: str, decision: str, note: str) -> dict[str, Any]:
    """Save maybe review."""
    if decision not in MAYBE_DECISIONS:
        raise ValueError("invalid maybe-review decision")
    valid_ids = {row["candidate_id"] for row in read_json(work_dir / "maybe_manifest.json")["records"]}
    if candidate_id not in valid_ids:
        raise ValueError("unknown maybe candidate")
    path = work_dir / "maybe_second_pass_reviews.json"
    state = read_json(path)
    previous = (state.get("reviews") or {}).get(candidate_id)
    clean_note = str(note).strip()[:4000]
    if previous and previous.get("decision") == decision and previous.get("note") == clean_note:
        return state
    revision = int((previous or {}).get("revision") or 0) + 1
    current = {"candidate_id": candidate_id, "decision": decision, "note": clean_note, "revision": revision, "reviewed_at": utc_now()}
    state.setdefault("reviews", {})[candidate_id] = current
    state["updated_at"] = utc_now()
    atomic_write_json(path, state)
    append_jsonl(work_dir / "maybe_second_pass_review_audit.jsonl", {"candidate_id": candidate_id, "previous": previous, "new": current, "timestamp": utc_now()})
    return state


def compile_review_results(work_dir: Path) -> dict[str, Any]:
    """Compile pairing decisions without treating them as metadata approval."""
    pairs = _review_pairs(work_dir)
    pair_by_id = {row["pair_id"]: row for row in pairs}
    review_state = read_json(work_dir / "human_pairing_review.json")
    reviews = review_state.get("reviews") or {}
    expected_ids = set(pair_by_id)
    reviewed_ids = expected_ids.intersection(reviews)
    decision_counts = Counter(
        reviews[pair_id].get("decision") or "unavailable"
        for pair_id in sorted(reviewed_ids)
    )
    approved: list[dict[str, Any]] = []
    candidate_ids = sorted({row["candidate_id"] for row in pairs})
    metadata_statuses = {candidate_id: "not_human_validated" for candidate_id in candidate_ids}
    for pair_id in sorted(reviewed_ids):
        decision = reviews[pair_id].get("decision")
        if decision == "metadata_needs_correction":
            metadata_statuses[pair_by_id[pair_id]["candidate_id"]] = "correction_requested"
        if decision == "approve_pairing":
            pair = pair_by_id[pair_id]
            approved.append({
                "pair_id": pair_id,
                "candidate_id": pair["candidate_id"],
                "quote_id": pair.get("quote_hash"),
                "quote_text": pair.get("quote_text"),
                "new_image_score": pair.get("score"),
                "existing_image": pair.get("nearest_competing_existing_image"),
                "existing_image_score": pair.get("nearest_competing_score"),
                "review_note": reviews[pair_id].get("note") or "",
            })

    maybe_manifest = read_json(work_dir / "maybe_manifest.json")
    maybe_ids = {row["candidate_id"] for row in maybe_manifest.get("records") or []}
    maybe_state = read_json(work_dir / "maybe_second_pass_reviews.json")
    maybe_reviews = maybe_state.get("reviews") or {}
    reviewed_maybe_ids = maybe_ids.intersection(maybe_reviews)
    maybe_counts = Counter(
        maybe_reviews[candidate_id].get("decision") or "unavailable"
        for candidate_id in sorted(reviewed_maybe_ids)
    )
    promoted_maybe_ids = sorted(
        candidate_id for candidate_id in reviewed_maybe_ids
        if maybe_reviews[candidate_id].get("decision") == "promote_keep"
    )
    notes = [
        {
            "pair_id": pair_id,
            "candidate_id": pair_by_id[pair_id]["candidate_id"],
            "quote_id": pair_by_id[pair_id].get("quote_hash"),
            "decision": reviews[pair_id].get("decision"),
            "note": str(reviews[pair_id].get("note") or "").strip(),
        }
        for pair_id in sorted(reviewed_ids)
        if str(reviews[pair_id].get("note") or "").strip()
    ]
    default_metadata_validation = {
        "status": "not_performed",
        "reason": (
            "The pairing reviewer records suitability of individual quote/image pairings. "
            "It has no independent metadata-approval decision, so an absent correction flag "
            "must not be interpreted as approval of generated metadata."
        ),
        "absence_of_correction_flags_is_approval": False,
        "correction_requested_count": sum(
            status == "correction_requested" for status in metadata_statuses.values()
        ),
        "candidate_statuses": metadata_statuses,
    }
    status_path = work_dir / "metadata_review_status.json"
    if status_path.exists():
        existing_status = read_json(status_path)
        metadata_validation = (
            existing_status
            if existing_status.get("automated_source_grounded_validation") in {"passed", "failed"}
            else default_metadata_validation
        )
    else:
        metadata_validation = default_metadata_validation
    value = {
        "schema_version": 1,
        "pairing_count": len(expected_ids),
        "pairing_reviewed_count": len(reviewed_ids),
        "pairing_unreviewed_count": len(expected_ids - reviewed_ids),
        "pairing_review_complete": reviewed_ids == expected_ids,
        "decision_counts": dict(sorted(decision_counts.items())),
        "approved_pairing_count": len(approved),
        "approved_pairings": approved,
        "review_notes": notes,
        "maybe_count": len(maybe_ids),
        "maybe_reviewed_count": len(reviewed_maybe_ids),
        "maybe_review_complete": reviewed_maybe_ids == maybe_ids,
        "maybe_decision_counts": dict(sorted(maybe_counts.items())),
        "promoted_maybe_candidate_ids": promoted_maybe_ids,
        "metadata_validation": metadata_validation,
        "generated_at": utc_now(),
    }
    source_grounded_passed = metadata_validation.get("automated_source_grounded_validation") == "passed"
    lines = [
        "# Pairing Review Results", "",
        f"- Pairings reviewed: {value['pairing_reviewed_count']}/{value['pairing_count']}",
        f"- Approved pairings: {value['approved_pairing_count']}",
        f"- Preferred existing image: {decision_counts.get('prefer_existing_image', 0)}",
        f"- Maybe images promoted: {len(promoted_maybe_ids)}/{len(maybe_ids)}", "",
        "## Metadata status", "",
        (
            "Source-grounded identity and event metadata passed the automated audit; "
            "pairing review remains a separate judgement."
            if source_grounded_passed else
            "Generated visual metadata was **not human-validated** by this review."
        ),
        "The absence of a `metadata_needs_correction` decision is not metadata approval.", "",
        "## Decisions", "",
    ]
    lines.extend(f"- `{key}`: {count}" for key, count in sorted(decision_counts.items()))
    lines.extend(["", "## Approved pairings", "", "| Candidate | Quote ID | New score | Existing image |", "|---|---|---:|---|"])
    lines.extend(
        f"| `{row['candidate_id']}` | `{row['quote_id']}` | {row['new_image_score']:.2f} | `{row['existing_image'] or 'none'}` |"
        for row in approved
    )
    if notes:
        lines.extend(["", "## Reviewer notes", ""])
        lines.extend(
            f"- `{row['pair_id']}` ({row['decision']}): {row['note']}"
            for row in notes
        )
    atomic_write_json(work_dir / "pairing_review_results.json", value)
    if metadata_validation is default_metadata_validation:
        atomic_write_json(status_path, metadata_validation)
    atomic_write_text(work_dir / "pairing_review_report.md", "\n".join(lines) + "\n")
    return value


PAIRING_HTML = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Pairing review</title><style>
body{margin:0;font:16px system-ui;background:#f4f4f1;color:#171717}header{position:sticky;top:0;z-index:4;background:#fff;border-bottom:1px solid #ccc;padding:10px 16px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}header label{display:flex;gap:6px;align-items:center}main{max-width:1180px;margin:auto;padding:16px}.notice{background:#fff8d8;border:1px solid #d6bd57;padding:9px 12px;margin-bottom:12px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.panel{background:#fff;border:1px solid #ccc;border-radius:6px;padding:14px}.images{display:grid;grid-template-columns:1fr 1fr;gap:10px}.images img{width:100%;max-height:55vh;object-fit:contain;background:#ddd}.quote{font:22px Georgia,serif;line-height:1.35;white-space:pre-wrap}.buttons{display:flex;flex-wrap:wrap;gap:8px}.buttons button{min-height:48px;padding:9px 13px}.selected{outline:3px solid #1261a0;background:#e8f3fb}textarea{width:100%;min-height:80px;box-sizing:border-box}button{min-height:40px}.complete{font-size:22px;font-weight:700;padding:30px;text-align:center}@media(max-width:800px){.grid{grid-template-columns:1fr}.images{grid-template-columns:1fr}.images img{max-height:42vh}main{padding:9px}.quote{font-size:19px}header{position:static}}</style></head><body><header><strong>Image/quote pairing review</strong><span id="progress"></span><label><input id="onlyUnreviewed" type="checkbox" checked>Show unreviewed only</label><button id="prev">Previous</button><button id="next">Next</button><button id="nextUnreviewed">Next unreviewed</button><a href="/maybe">Maybe-image review</a></header><main><div class="notice">A decision saves immediately. With “Show unreviewed only” selected, the decided pair disappears and the next outstanding pair is shown.</div><div id="complete" class="panel complete" hidden>All current pairings have been reviewed.</div><div id="reviewGrid" class="grid"><section class="panel"><div class="images"><div><h3>New image</h3><img id="newImage" alt="New candidate image"></div><div><h3>Existing competitor</h3><img id="existing" alt="Existing comparison image"></div></div><p id="source"></p><p id="summary"></p></section><section class="panel"><div class="quote" id="quote"></div><p id="scores"></p><div class="buttons" id="buttons"></div><label>Optional note<textarea id="note"></textarea></label></section></div></main><script>
const options=[['approve_pairing','Approve pairing'],['reject_pairing','Reject pairing'],['image_unsuitable','Image unsuitable'],['metadata_needs_correction','Metadata needs correction'],['prefer_existing_image','Prefer existing image'],['skip','Skip']];const el=id=>document.getElementById(id);const newImage=el('newImage'),existing=el('existing'),quote=el('quote'),source=el('source'),summary=el('summary'),scores=el('scores'),note=el('note'),buttons=el('buttons'),progress=el('progress'),onlyUnreviewed=el('onlyUnreviewed'),complete=el('complete'),reviewGrid=el('reviewGrid');let data,idx=0,busy=false;function reviewed(r){return Boolean(data.reviews[r.pair_id])}function rows(){return onlyUnreviewed.checked?data.records.filter(r=>!reviewed(r)):data.records}function recount(){const current=new Set(data.records.map(r=>r.pair_id));data.reviewed_count=Object.keys(data.reviews).filter(id=>current.has(id)).length;data.unreviewed_count=data.records.length-data.reviewed_count}function draw(){const list=rows();recount();progress.textContent=`${data.reviewed_count}/${data.records.length} reviewed · ${data.unreviewed_count} remaining`;if(!list.length){complete.hidden=false;reviewGrid.hidden=true;return}complete.hidden=true;reviewGrid.hidden=false;idx=Math.max(0,Math.min(idx,list.length-1));const r=list[idx],v=data.reviews[r.pair_id]||{};progress.textContent+=` · showing ${idx+1}/${list.length}`;newImage.src=r.new_image_url;if(r.existing_image_url){existing.src=r.existing_image_url;existing.style.display='block'}else existing.style.display='none';quote.textContent=r.quote_text;source.textContent=`${r.source.publisher} · ${r.source.rights_status} · ${r.source.caption||''}`;summary.textContent=r.analysis_summary;scores.textContent=`New ${r.score.toFixed(2)} vs existing ${r.nearest_competing_score?.toFixed(2)??'n/a'} · ${JSON.stringify(r.components)}`;note.value=v.note||'';buttons.innerHTML='';for(const [value,label] of options){const b=document.createElement('button');b.textContent=label;b.className=v.decision===value?'selected':'';b.onclick=()=>save(value);buttons.appendChild(b)}}async function save(decision){if(busy)return;const list=rows(),current=list[idx];if(!current)return;busy=true;try{const response=await fetch('/api/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pair_id:current.pair_id,decision,note:note.value})});if(!response.ok)throw Error(await response.text());data.reviews=(await response.json()).reviews;draw()}finally{busy=false}}el('prev').onclick=()=>{idx=Math.max(0,idx-1);draw()};el('next').onclick=()=>{idx=Math.min(rows().length-1,idx+1);draw()};el('nextUnreviewed').onclick=()=>{onlyUnreviewed.checked=true;idx=0;draw()};onlyUnreviewed.onchange=()=>{idx=0;draw()};document.addEventListener('keydown',event=>{if(event.target===note)return;if(event.key==='ArrowLeft')el('prev').click();if(event.key==='ArrowRight')el('next').click()});fetch('/api/state').then(response=>response.json()).then(value=>{data=value;onlyUnreviewed.checked=data.unreviewed_count>0;draw()});</script></body></html>"""


MAYBE_HTML = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Maybe review</title><style>body{font:17px system-ui;max-width:900px;margin:auto;padding:16px}img{display:block;max-width:100%;max-height:65vh;margin:auto}button{min-height:48px;margin:8px;padding:10px}.selected{outline:3px solid #1261a0}textarea{width:100%;min-height:80px}@media(max-width:800px){body{padding:9px}}</style></head><body><a href="/">Pairing review</a><h1>Maybe second pass</h1><p id="progress"></p><img id="image"><h2 id="title"></h2><p id="caption"></p><div id="buttons"></div><textarea id="note"></textarea><div><button id="prev">Previous</button><button id="next">Next</button></div><script>const opts=[['promote_keep','Promote to keep'],['reject','Reject'],['remain_maybe','Remain maybe']];let data,idx=0,busy=false;function draw(){const r=data.records[idx],v=data.reviews[r.candidate_id]||{};progress.textContent=`${idx+1} of ${data.records.length}`;image.src='/maybe-image/'+r.candidate_id;title.textContent=r.publisher;caption.textContent=r.caption;note.value=v.note||'';buttons.innerHTML='';for(const [x,l] of opts){const b=document.createElement('button');b.textContent=l;b.className=v.decision===x?'selected':'';b.onclick=()=>save(x);buttons.appendChild(b)}}async function save(decision){if(busy)return;busy=true;try{const r=await fetch('/api/maybe-review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:data.records[idx].candidate_id,decision,note:note.value})});if(!r.ok)throw Error(await r.text());data.reviews=(await r.json()).reviews;draw()}finally{busy=false}}prev.onclick=()=>{idx=Math.max(0,idx-1);draw()};next.onclick=()=>{idx=Math.min(data.records.length-1,idx+1);draw()};fetch('/api/maybe-state').then(r=>r.json()).then(x=>{data=x;draw()});</script></body></html>"""


def make_review_handler(project_dir: Path, research_dir: Path, work_dir: Path) -> type[BaseHTTPRequestHandler]:
    """Create review handler."""
    source = review_source_records(work_dir)
    maybe = {row["candidate_id"]: row for row in read_json(work_dir / "maybe_manifest.json")["records"]}

    class Handler(BaseHTTPRequestHandler):
        def send(self, status: int, payload: bytes, content_type: str) -> None:
            self.send_response(status); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(payload))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            path = unquote(urlparse(self.path).path)
            if path == "/": return self.send(200, PAIRING_HTML.encode(), "text/html; charset=utf-8")
            if path == "/maybe": return self.send(200, MAYBE_HTML.encode(), "text/html; charset=utf-8")
            if path == "/api/state": return self.send(200, json.dumps(pairing_review_state(research_dir, work_dir)).encode(), "application/json")
            if path == "/api/maybe-state":
                reviews = read_json(work_dir / "maybe_second_pass_reviews.json").get("reviews") or {}
                return self.send(200, json.dumps({"records": list(maybe.values()), "reviews": reviews}).encode(), "application/json")
            if path.startswith("/new/"):
                row = source.get(path.rsplit("/", 1)[-1]); file_path = _candidate_path(research_dir, row) if row else None
                if file_path and file_path.is_file(): return self.send(200, file_path.read_bytes(), "image/" + ("png" if file_path.suffix.lower() == ".png" else "jpeg"))
            if path.startswith("/maybe-image/"):
                row = maybe.get(path.rsplit("/", 1)[-1]); file_path = _candidate_path(research_dir, row) if row else None
                if file_path and file_path.is_file(): return self.send(200, file_path.read_bytes(), "image/" + ("png" if file_path.suffix.lower() == ".png" else "jpeg"))
            if path.startswith("/existing/"):
                filename = path.rsplit("/", 1)[-1]
                if filename in read_json(project_dir / "image_analysis.json")["path_index"]:
                    file_path = project_dir / "images" / filename
                    if file_path.is_file(): return self.send(200, file_path.read_bytes(), "image/jpeg")
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:  # noqa: N802
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 16384)
                value = json.loads(self.rfile.read(length))
                if urlparse(self.path).path == "/api/review": state = save_pairing_review(work_dir, str(value.get("pair_id") or ""), str(value.get("decision") or ""), str(value.get("note") or ""))
                elif urlparse(self.path).path == "/api/maybe-review": state = save_maybe_review(work_dir, str(value.get("candidate_id") or ""), str(value.get("decision") or ""), str(value.get("note") or ""))
                else: return self.send(404, b"not found", "text/plain")
                self.send(200, json.dumps(state).encode(), "application/json")
            except Exception as exc:
                self.send(400, str(exc).encode(), "text/plain; charset=utf-8")

        def log_message(self, _format: str, *_args: Any) -> None: return

    return Handler


def serve_review(project_dir: Path, research_dir: Path, work_dir: Path, host: str, port: int) -> None:
    """Serve review."""
    server = ThreadingHTTPServer((host, port), make_review_handler(project_dir, research_dir, work_dir))
    print(f"Pairing review: http://{host}:{port}")
    print(f"Maybe second pass: http://{host}:{port}/maybe")
    try: server.serve_forever()
    finally: server.server_close()


def verify_production_unchanged(project_dir: Path, work_dir: Path) -> dict[str, Any]:
    """Verify production unchanged."""
    before = read_json(work_dir / "production_hashes_before.json")
    after = production_hashes(project_dir)
    value = {"schema_version": 1, "unchanged": before["aggregate_sha256"] == after["aggregate_sha256"], "before": before, "after": after, "verified_at": utc_now()}
    atomic_write_json(work_dir / "production_hash_verification.json", value)
    if not value["unchanged"]:
        raise RuntimeError("production image/code hashes changed during isolated preparation")
    return value


def final_report(project_dir: Path, research_dir: Path, work_dir: Path) -> dict[str, Any]:
    """Return the final report."""
    verification = verify_production_unchanged(project_dir, work_dir)
    production = effective_production_ready_records(work_dir)
    original_production_count = len(read_json(work_dir / "production_ready_manifest.json")["records"])
    pending = read_json(work_dir / "rights_pending_manifest.json")["records"]
    maybe = read_json(work_dir / "maybe_manifest.json")["records"]
    duplicate = read_json(work_dir / "duplicate_audit.json")
    source_grounded_path = work_dir / "source_grounded_image_analysis.json"
    source_grounded_active = source_grounded_path.exists()
    analysis = read_json(
        source_grounded_path if source_grounded_active else work_dir / "canonical_image_analysis.json"
    )
    ledger = read_json(
        work_dir / "source_grounded_analysis_cost_ledger.json"
        if source_grounded_active else work_dir / "analysis_cost_ledger.json"
    )
    plan = read_json(work_dir / "filename_plan.json")
    matching = read_json(work_dir / "quote_matching_dry_run.json")
    review_results = compile_review_results(work_dir)
    metadata_status = review_results["metadata_validation"]
    plan_by_id = {row["candidate_id"]: row for row in plan["records"]}
    improved_ids = [row["candidate_id"] for row in matching["records"] if row["materially_improves_coverage"]]
    noncompetitive_ids = [row["candidate_id"] for row in matching["records"] if row["never_competitive"]]
    broad_ids = [row["candidate_id"] for row in matching["records"] if row["dominates_too_many_unrelated_quotes"]]
    value = {
        "schema_version": 2, "production_ready_count": len(production),
        "original_production_ready_count": original_production_count,
        "promoted_maybe_count": len(production) - original_production_count,
        "rights_pending_count": len(pending), "maybe_count": len(maybe),
        "duplicate_classifications": duplicate["classification_counts"],
        "analysis_model": analysis["model"], "analysis_prompt_version": analysis["prompt_version"],
        "analysis_source": matching.get("analysis_source") or (
            "source_grounded_image_analysis.json" if source_grounded_active
            else "canonical_image_analysis.json"
        ),
        "analysis_count": len(analysis["items"]),
        "analysis_cost_usd": (
            ledger["combined_known_cost_usd"] if source_grounded_active else ledger["known_cost_usd"]
        ),
        "source_grounded_incremental_cost_usd": ledger.get("source_grounded_cost_usd"),
        "ambiguous_possible_exposure_usd": ledger.get("ambiguous_possible_exposure_usd", 0),
        "filename_range": planned_filename_bounds(plan),
        "material_coverage_improvement_count": matching["material_coverage_improvement_count"],
        "material_coverage_improvement_candidate_ids": improved_ids,
        "never_competitive_count": matching["never_competitive_count"],
        "never_competitive_candidate_ids": noncompetitive_ids,
        "broad_dominance_count": matching["broad_dominance_count"],
        "broad_dominance_candidate_ids": broad_ids,
        "suspicious_pairing_count": len(matching["suspicious_pairings"]),
        "production_files_unchanged": verification["unchanged"],
        "pairing_review_count": len(_review_pairs(work_dir)), "review_url": "http://127.0.0.1:8769",
        "pairing_reviewed_count": review_results["pairing_reviewed_count"],
        "pairing_review_complete": review_results["pairing_review_complete"],
        "pairing_decision_counts": review_results["decision_counts"],
        "approved_pairing_count": review_results["approved_pairing_count"],
        "promoted_maybe_candidate_ids": review_results["promoted_maybe_candidate_ids"],
        "metadata_validation_status": metadata_status.get(
            "automated_source_grounded_validation", metadata_status.get("status", "unavailable")
        ),
        "generated_at": utc_now(),
    }
    lines = [
        "# Discovered Image Integration Preparation", "",
        f"- Production-ready: {value['production_ready_count']} "
        f"({value['original_production_ready_count']} original keeps + "
        f"{value['promoted_maybe_count']} promoted maybes)",
        f"- Rights pending: {value['rights_pending_count']}",
        f"- Maybe second pass: {value['maybe_count']}",
        f"- Canonical analyses: {value['analysis_count']} with `{value['analysis_model']}` / `{value['analysis_prompt_version']}`",
        f"- Analysis source: `{value['analysis_source']}`",
        f"- Combined known analysis cost: US${value['analysis_cost_usd']:.4f}",
        f"- Ambiguous possible exposure: US${value['ambiguous_possible_exposure_usd']:.4f}",
        f"- Planned filenames: `{value['filename_range'][0]}` through `{value['filename_range'][1]}` (not applied)",
        f"- Material coverage improvements: {value['material_coverage_improvement_count']}",
        f"- Never competitive: {value['never_competitive_count']}",
        f"- Broad-dominance flags: {value['broad_dominance_count']}",
        f"- Suspicious pairing flags: {value['suspicious_pairing_count']}",
        f"- Pairing review: {value['pairing_reviewed_count']}/{value['pairing_review_count']} complete",
        f"- Approved pairings: {value['approved_pairing_count']}",
        f"- Preferred existing image: {value['pairing_decision_counts'].get('prefer_existing_image', 0)}",
        f"- Maybe images promoted to keep: {len(value['promoted_maybe_candidate_ids'])}",
        f"- Automated source-grounded metadata validation: {value['metadata_validation_status']}",
        f"- Production image/code files unchanged: {'yes' if value['production_files_unchanged'] else 'no'}", "",
        "## Duplicate classifications", "",
    ]
    lines.extend(f"- {key}: {count}" for key, count in sorted(value["duplicate_classifications"].items()))
    lines.extend(["", "## Production-ready images", "", "| Candidate | Proposed name | Rights | Attribution |", "|---|---|---|---|"])
    lines.extend(
        f"| `{row['candidate_id']}` | `{plan_by_id[row['candidate_id']]['proposed_name']}` | {row['rights_status']} | {row['required_attribution_wording'] or 'None required by recorded rights status'} |"
        for row in production
    )
    lines.extend(["", "## Segregated images", "", "### Rights pending", ""])
    lines.extend(f"- `{row['candidate_id']}`: {row['publisher']} ({row['rights_status']})" for row in pending)
    lines.extend(["", "### Maybe second pass", ""])
    lines.extend(f"- `{row['candidate_id']}`: {row['publisher']} ({row['rights_status']})" for row in maybe)
    lines.extend([
        "", "## Matching observations", "",
        "- Material coverage improvements: " + (", ".join(f"`{item}`" for item in improved_ids) or "none"),
        "- Never competitive in the all-originals-available comparison: " + (", ".join(f"`{item}`" for item in noncompetitive_ids) or "none"),
        "- Broad-dominance audit cases: " + (", ".join(f"`{item}`" for item in broad_ids) or "none"),
        "- Suspicious low-evidence pairings: " + (str(len(matching["suspicious_pairings"]))),
        "- Proposed higher-quality replacements: " + str(duplicate["classification_counts"].get("higher_quality_replacement", 0)),
    ])
    lines.extend([
        "", "## Pairing review", "",
        f"Reviewed {value['pairing_reviewed_count']} of {value['pairing_review_count']} current proposed pairings.",
        f"Approved pairings: {value['approved_pairing_count']}.",
        f"Existing image preferred: {value['pairing_decision_counts'].get('prefer_existing_image', 0)}.",
        "The two Maybe images were promoted to keep in the second-pass review.", "",
        "Named people and event context were rebuilt from source captions, archive metadata and "
        "source-page evidence. The source-grounded audit status is recorded above; no facial "
        "identity inference was used. Pairing decisions remain separate from metadata validation.", "",
        "Detailed results are in `pairing_review_results.json` and `pairing_review_report.md`.", "",
        "```bash",
        f"python3 prepare_discovered_images.py serve-pairing-review --work-dir {work_dir} --host 0.0.0.0 --port 8769",
        "```", "",
        "No file was copied into `images/`; no production analysis, state, receipt, history or configuration was changed.",
    ])
    atomic_write_json(work_dir / "preparation_summary.json", value)
    atomic_write_text(work_dir / "final_preparation_report.md", "\n".join(lines) + "\n")
    return value


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=(
        "prepare", "analysis-preflight", "analyse", "build-source-metadata",
        "source-grounded-preflight", "analyse-source-grounded", "refresh-source-overlays",
        "audit-source-metadata", "match",
        "serve-pairing-review", "status", "report",
    ))
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    parser.add_argument("--research-dir", type=Path, default=Path("image_discovery_research/thatcher_image_hunt_002"))
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-max-cost-usd", type=float)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8769)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line entry point."""
    args = build_parser().parse_args(argv)
    project_dir = args.project_dir.resolve()
    research_dir = (args.research_dir if args.research_dir.is_absolute() else project_dir / args.research_dir).resolve()
    work_dir = (args.work_dir or research_dir / "integration_preparation").resolve()
    if research_dir not in work_dir.parents:
        raise RuntimeError("work directory must remain beneath the isolated research run")
    if args.command == "prepare":
        result = prepare_export(project_dir, research_dir, work_dir)
        result["duplicate_audit"] = duplicate_audit(project_dir, research_dir, work_dir)["classification_counts"]
        result["filename_plan"] = filename_plan(project_dir, work_dir)
    elif args.command == "analysis-preflight": result = analysis_preflight(project_dir, work_dir)
    elif args.command == "analyse": result = analyse_candidates(project_dir, research_dir, work_dir, execute=args.execute, confirmed_cost=args.confirm_max_cost_usd)
    elif args.command == "build-source-metadata": result = build_source_grounded_metadata(research_dir, work_dir)
    elif args.command == "source-grounded-preflight": result = source_grounded_analysis_preflight(project_dir, research_dir, work_dir)
    elif args.command == "analyse-source-grounded":
        result = analyse_source_grounded_candidates(
            project_dir, research_dir, work_dir,
            execute=args.execute, confirmed_cost=args.confirm_max_cost_usd,
        )
    elif args.command == "refresh-source-overlays": result = refresh_source_grounded_overlays(research_dir, work_dir)
    elif args.command == "audit-source-metadata": result = audit_source_grounded_metadata(research_dir, work_dir)
    elif args.command == "match":
        value = quote_matching_dry_run(project_dir, work_dir)
        result = {key: value[key] for key in (
            "schema_version", "selector", "state_mutated", "quote_count", "candidate_count",
            "never_competitive_count", "broad_dominance_count",
            "material_coverage_improvement_count", "broad_dominance_win_threshold",
        )}
    elif args.command == "serve-pairing-review": serve_review(project_dir, research_dir, work_dir, args.host, args.port); return 0
    elif args.command == "report": result = final_report(project_dir, research_dir, work_dir)
    else:
        result = {"work_dir": str(work_dir), "files": sorted(path.name for path in work_dir.iterdir()) if work_dir.exists() else []}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
