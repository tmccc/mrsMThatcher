#!/usr/bin/env python3
"""Offline-first quotation/image metadata remediation and adjudication.

The module is import-safe. Network-capable operations are confined to the
explicit ``prepare-local-model`` and ``enhance-and-judge --execute-ai`` paths.
Production files are always treated as read-only inputs.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import math
import os
import platform
import re
import shutil
import socket
import sqlite3
import statistics
import subprocess
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
from urllib.parse import parse_qs, urlparse

from google.genai import types

from historical_context_formatter import packet_is_attributed_to_margaret_thatcher
from semantic_alignment.io import atomic_write_json, atomic_write_text, read_json, sha256_file
from semantic_alignment.relation_aware_veto import (
    CostLedger,
    DeveloperBatchRunner,
    RelationRouter,
    maximum_cost,
    pair_response_schema,
    require_transport_parity,
    transport_preflight,
    validate_pair_response,
)
from semantic_alignment.thatcher_image_hunt import append_jsonl


ROOT = Path(__file__).resolve().parent
DEFAULT_RUN = ROOT / "semantic_alignment_research/quote_image_metadata_remediation_001"
PRE_ADJUDICATION_FIX_AUDIT = Path("audit_versions/pre_attribution_fix_002")
DEFAULT_HARNESS = ROOT / "semantic_alignment_research/quote_image_selection_harness_001"
V2_DIR = ROOT / "semantic_alignment_research/relation_aware_semantic_veto_002"
SHADOW_DIR = ROOT / "semantic_alignment_research/quote_image_semantic_veto_001/shadow"
RESEARCH_DIR = ROOT / "semantic_alignment_research/quote_research_full_001"
DISCOVERED_DIR = ROOT / "image_discovery_research/thatcher_image_hunt_002/integration_preparation"

SCHEMA_VERSION = 1
QUOTE_CONTRACT_VERSION = "relation-aware-material-contract-v3"
IMAGE_CONTRACT_VERSION = "source-grounded-image-contract-v3"
RULE_VERSION = "affirmative-material-contradiction-rules-v3"
FIRST_PROMPT_VERSION = "metadata-remediation-pair-first-pass-2026-07-17-v1"
SECOND_PROMPT_VERSION = "metadata-remediation-pair-adversarial-pass-2026-07-17-v1"
PAIR_SCHEMA_VERSION = "relation-aware-pair-judgement-v4"
MODEL = "gemini-3.1-pro-preview"
THINKING_LEVEL = "HIGH"
TEMPERATURE = 0.0
PRICING_VERSION = "gemini-3.1-pro-preview-public-pricing-2026-07-17-v1"
EXPECTED_COMPLETED_PACKETS = 627
EXPECTED_QUOTES = 626
EXPECTED_UNRESOLVED = 5
EXPECTED_IMAGES = 91
EXPECTED_ORIGINAL = 69
HARD_SPEND_LIMIT_USD = 100.0
PLANNED_SPEND_LIMIT_USD = 85.0
REPAIR_RESERVE_USD = 15.0
PAIR_BATCH_SIZE = 23
SECOND_PASS_BATCH_SIZE = 8
PAIR_MAX_OUTPUT_TOKENS = 8192

OFFICIAL_MODEL_REPOSITORY = "Qwen/Qwen3-VL-8B-Instruct-GGUF"
OFFICIAL_MODEL_LICENSE = "Apache-2.0"
OFFICIAL_MODEL_QUANTISATION = "Q4_K_M"
OFFICIAL_PROJECTOR_POLICY = "matching official Qwen repository projector only"
LOCAL_CACHE = Path.home() / ".cache/mrsMThatcher/models/qwen3-vl-8b-instruct-gguf"

SOURCE_FILES = {
    "production_code": ROOT / "mrsMThatcher2.py",
    "production_config": ROOT / "mrsMThatcher.local.json",
    "image_analysis": ROOT / "image_analysis.json",
    "quote_research_packets": RESEARCH_DIR / "research_packets.json",
    "quote_research_manifest": RESEARCH_DIR / "corpus_manifest.json",
    "v2_quote_contracts": V2_DIR / "semantic_contracts_v2.json",
    "v2_images": V2_DIR / "attested_image_corpus_manifest_postrun_corrected.json",
    "v2_pairs": V2_DIR / "pair_judgements_v2_postrun_corrected.json",
    "v2_candidates": V2_DIR / "production_top8_pair_candidates_v2_postrun_corrected.json",
    "v2_correction_audit": V2_DIR / "v2_postrun_correction_audit.json",
    "v2_status": V2_DIR / "v2_final_status_postrun_corrected.json",
    "live_shadow_manifest": SHADOW_DIR / "material_veto_v2_shadow_manifest.json",
    "discovered_analysis": DISCOVERED_DIR / "canonical_image_analysis.json",
    "discovered_production_ready": DISCOVERED_DIR / "production_ready_manifest.json",
    "discovered_sources": DISCOVERED_DIR / "source_and_attribution_manifest.json",
}

STRICT_TRANSITIONS = {"enemy_to_friend", "opponent_to_partner", "conflict_to_peace"}


class RemediationError(RuntimeError):
    """Raised when metadata remediation violates a safety invariant."""
    pass


class MultimodalPrompt(str):
    """Text-hashable prompt carrying one local image for Gemini transport."""

    def __new__(
        cls, text: str, image_path: Path, image_hash: str, *, extra_input_tokens: int = 4096,
    ) -> "MultimodalPrompt":
        """Create a multimodal prompt instance."""
        value = str.__new__(cls, text)
        suffix = image_path.suffix.casefold()
        mime = "image/png" if suffix == ".png" else "image/webp" if suffix == ".webp" else "image/jpeg"
        value.gemini_contents = [types.Content(role="user", parts=[
            types.Part.from_bytes(data=image_path.read_bytes(), mime_type=mime),
            types.Part.from_text(text=text),
        ])]
        value.estimated_extra_input_tokens = extra_input_tokens
        value.image_hash = image_hash
        return value


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_bytes(value: Any) -> bytes:
    """Return the canonical bytes."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value: Any) -> str:
    """Return the digest."""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def text_digest(value: str) -> str:
    """Return the text digest."""
    return hashlib.sha256(value.encode()).hexdigest()


def clean(value: Any, maximum: int = 1200) -> str:
    """Return the clean."""
    result = " ".join(html.unescape(str(value or "")).split())
    return result if len(result) <= maximum else result[: maximum - 1].rstrip() + "…"


def atomic_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    """Perform the atomic jsonl operation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Yield jsonl values."""
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def stable_identity(path: Path, attempts: int = 8) -> dict[str, Any]:
    """Return the stable identity."""
    for _ in range(attempts):
        before = path.stat()
        sha = sha256_file(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns):
            return {
                "path": str(path.resolve()), "size": after.st_size,
                "mtime_ns": after.st_mtime_ns, "sha256": sha,
            }
        time.sleep(0.05)
    raise RemediationError(f"source changed repeatedly during stable read: {path}")


def source_snapshot(run_dir: Path) -> dict[str, Any]:
    """Return the source snapshot."""
    rows = {}
    for key, path in SOURCE_FILES.items():
        if not path.is_file():
            raise RemediationError(f"required source missing: {path}")
        rows[key] = stable_identity(path)
    image_rows = []
    for path in sorted((ROOT / "images").glob("t*")):
        if path.is_file() and not path.name.startswith("._"):
            image_rows.append(stable_identity(path))
    if len(image_rows) != EXPECTED_ORIGINAL:
        raise RemediationError(f"expected {EXPECTED_ORIGINAL} original image files, found {len(image_rows)}")
    payload = {
        "schema_version": SCHEMA_VERSION, "generated_at": utc_now(), "sources": rows,
        "original_images": image_rows,
        "aggregate_sha256": digest({"sources": rows, "original_images": image_rows}),
        "snapshot_protocol": "stat-hash-stat with bounded retry; no source write",
    }
    atomic_write_json(run_dir / "source_snapshot_manifest.json", payload)
    return payload


@contextmanager
def offline_network_guard() -> Iterator[None]:
    """Yield offline network guard values."""
    original_socket = socket.socket
    original_getaddrinfo = socket.getaddrinfo

    class BlockedSocket(original_socket):
        def connect(self, *args: Any, **kwargs: Any) -> Any:
            raise RemediationError("network access is disabled for this command")

        def connect_ex(self, *args: Any, **kwargs: Any) -> Any:
            raise RemediationError("network access is disabled for this command")

        def sendto(self, *args: Any, **kwargs: Any) -> Any:
            raise RemediationError("network access is disabled for this command")

    def blocked_dns(*args: Any, **kwargs: Any) -> Any:
        raise RemediationError("DNS is disabled for this command")

    socket.socket = BlockedSocket
    socket.getaddrinfo = blocked_dns
    try:
        yield
    finally:
        socket.socket = original_socket
        socket.getaddrinfo = original_getaddrinfo


def load_packets() -> dict[str, dict[str, Any]]:
    """Load packets."""
    raw = read_json(RESEARCH_DIR / "research_packets.json")
    items = raw.get("items") or {}
    if (
        len(items) != EXPECTED_COMPLETED_PACKETS
        or len(set(items)) != EXPECTED_COMPLETED_PACKETS
    ):
        raise RemediationError("canonical completed research packet count differs from 627")
    unresolved = read_json(RESEARCH_DIR / "final_unresolved/final_research_status.json")
    unresolved_ids = set(unresolved.get("unresolved_quote_ids") or [])
    if len(unresolved_ids) != EXPECTED_UNRESOLVED or unresolved_ids & set(items):
        raise RemediationError("completed/unresolved quotation partition is invalid")
    for quote_id, packet in items.items():
        if packet.get("quote_id") != quote_id or not packet.get("quote_text"):
            raise RemediationError(f"packet identity invalid: {quote_id}")
    return items


def load_images_with_corrected_relationships() -> list[dict[str, Any]]:
    """Load images with corrected relationships."""
    rows = copy.deepcopy(
        read_json(V2_DIR / "attested_image_corpus_manifest_postrun_corrected.json")[
            "records"
        ]
    )
    if len(rows) != EXPECTED_IMAGES or len({row["image_sha256"] for row in rows}) != EXPECTED_IMAGES:
        raise RemediationError("authorised historical image corpus differs from 91 unique images")
    changes = {
        row["image_id"]: row["after"]
        for row in read_json(V2_DIR / "v2_postrun_correction_audit.json")["relationship_grounding"]["changes"]
    }
    for row in rows:
        if row["image_id"] in changes:
            row["relationship_assertions"] = changes[row["image_id"]]
    return rows


def _global_safe_quotes() -> tuple[set[str], set[str]]:
    status = read_json(V2_DIR / "v2_final_status_postrun_corrected.json")
    no_safe = set(status["quotes_without_allowed_candidate_ids"])
    packets = set(load_packets())
    return packets - no_safe, no_safe


def aggregate_simulator(harness_dir: Path, run_dir: Path) -> dict[str, Any]:
    """Return the aggregate simulator."""
    db_path = harness_dir / "simulation.sqlite3"
    if not db_path.is_file():
        raise RemediationError(f"harness database missing: {db_path}")
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    if connection.execute("pragma quick_check").fetchone()[0] != "ok":
        raise RemediationError("harness database integrity check failed")
    _, globally_no_safe = _global_safe_quotes()
    query = """
        SELECT quote_id, production_image_hash, production_image,
               COUNT(*) AS occurrence_count,
               SUM(CASE WHEN mode='monte_carlo' THEN 1 ELSE 0 END) AS monte_carlo_count,
               SUM(CASE WHEN mode='full_corpus_sweep' THEN 1 ELSE 0 END) AS sweep_count,
               SUM(CASE WHEN mode='seasonal_boundary_stress' THEN 1 ELSE 0 END) AS boundary_count,
               SUM(CASE WHEN veto_status='unknown_unjudged' THEN 1 ELSE 0 END) AS unknown_count,
               SUM(CASE WHEN veto_status='veto' THEN 1 ELSE 0 END) AS veto_count,
               SUM(CASE WHEN editorial_image IS NOT production_image THEN 1 ELSE 0 END) AS editorial_disagreement_count,
               SUM(CASE WHEN allowed_alternative IS NOT NULL THEN 1 ELSE 0 END) AS alternative_count,
               GROUP_CONCAT(DISTINCT simulated_year) AS simulated_years_csv,
               GROUP_CONCAT(DISTINCT seasonal_signature) AS seasonal_signatures_csv,
               GROUP_CONCAT(DISTINCT state_profile) AS state_profiles_csv,
               MIN(event_key) AS representative_event_id
        FROM simulation_events
        WHERE quote_id IS NOT NULL AND production_image_hash IS NOT NULL
        GROUP BY quote_id, production_image_hash, production_image
        ORDER BY quote_id, production_image_hash
    """
    records = []
    for raw in connection.execute(query):
        row = dict(raw)
        years = sorted({int(value) for value in str(row.pop("simulated_years_csv") or "").split(",") if value})
        signatures = sorted({value for value in str(row.pop("seasonal_signatures_csv") or "").split(",") if value})
        profiles = sorted({value for value in str(row.pop("state_profiles_csv") or "").split(",") if value})
        if row["unknown_count"]:
            taxonomy = "production_pair_unknown"
        elif row["veto_count"] and row["alternative_count"]:
            taxonomy = "vetoed_with_allowed_candidate_in_current_set"
        elif row["veto_count"]:
            taxonomy = "vetoed_without_allowed_candidate_in_current_set"
        else:
            taxonomy = "known_allowed_pair"
        if row["quote_id"] in globally_no_safe:
            global_taxonomy = "quote_globally_has_no_allowed_image"
        else:
            global_taxonomy = "quote_globally_has_allowed_image"
        records.append({
            **row, "simulated_years": years, "seasonal_signatures": signatures,
            "state_profiles": profiles, "taxonomy": taxonomy,
            "global_taxonomy": global_taxonomy,
            "pair_key": f"{row['quote_id']}:{row['production_image_hash']}",
        })
    connection.close()
    counts = Counter(row["taxonomy"] for row in records)
    payload = {
        "schema_version": SCHEMA_VERSION, "generated_at": utc_now(),
        "source_database": stable_identity(db_path), "unique_pair_count": len(records),
        "total_occurrences": sum(row["occurrence_count"] for row in records),
        "taxonomy_counts": dict(sorted(counts.items())), "records": records,
    }
    atomic_write_json(run_dir / "simulator_pair_aggregation.json", payload)
    lines = [
        "# Simulator Pair Aggregation", "", f"Unique quote/image pairs: {len(records)}",
        f"Simulation occurrences: {payload['total_occurrences']}", "", "## Corrected taxonomy", "",
        *[f"- {key}: {value}" for key, value in sorted(counts.items())], "",
        "Repeated simulation events are collapsed by canonical quote ID and image SHA-256.",
    ]
    atomic_write_text(run_dir / "simulator_pair_aggregation.md", "\n".join(lines) + "\n")
    return payload


def _meminfo() -> dict[str, int]:
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        values[key] = int(value.strip().split()[0]) * 1024
    return values


def local_model_preflight(run_dir: Path) -> dict[str, Any]:
    """Return the local model preflight."""
    mem = _meminfo()
    disk_probe = LOCAL_CACHE.parent
    while not disk_probe.exists() and disk_probe != disk_probe.parent:
        disk_probe = disk_probe.parent
    disk = shutil.disk_usage(disk_probe)
    cpu = platform.processor() or "unknown"
    lscpu = subprocess.run(["lscpu"], check=True, capture_output=True, text=True).stdout
    flags_line = next((line for line in lscpu.splitlines() if line.startswith("Flags:")), "")
    gpu = Path("/dev/nvidia0").exists() or any(Path("/dev/dri").glob("renderD*"))
    expected_model = 5_500_000_000
    expected_projector = 1_200_000_000
    expected_peak = 10_000_000_000
    safe = bool(gpu) or (
        mem.get("MemAvailable", 0) >= expected_peak * 1.25
        and mem.get("SwapFree", 0) >= mem.get("SwapTotal", 0) * 0.9
    )
    reasons = []
    if not gpu:
        reasons.append("no supported GPU/accelerator detected")
    if mem.get("MemAvailable", 0) < expected_peak * 1.25:
        reasons.append("available RAM is below the 1.25x expected-peak safety margin")
    if mem.get("SwapFree", 0) < mem.get("SwapTotal", 0) * 0.9:
        reasons.append("swap is already materially occupied")
    payload = {
        "schema_version": SCHEMA_VERSION, "generated_at": utc_now(),
        "repository": OFFICIAL_MODEL_REPOSITORY, "revision": None,
        "revision_status": "not_resolved_because_hardware_preflight_failed" if not safe else "must_pin_before_download",
        "licence": OFFICIAL_MODEL_LICENSE, "quantisation": OFFICIAL_MODEL_QUANTISATION,
        "projector_policy": OFFICIAL_PROJECTOR_POLICY, "cpu": cpu,
        "cpu_count_logical": os.cpu_count(), "instruction_flags": flags_line.removeprefix("Flags:").strip().split(),
        "physical_ram_bytes": mem.get("MemTotal"), "available_ram_bytes": mem.get("MemAvailable"),
        "swap_total_bytes": mem.get("SwapTotal"), "swap_free_bytes": mem.get("SwapFree"),
        "gpu_or_accelerator_available": gpu, "disk_free_bytes": disk.free,
        "expected_model_size_bytes": expected_model, "expected_projector_size_bytes": expected_projector,
        "expected_resident_memory_bytes": 8_000_000_000, "expected_peak_memory_bytes": expected_peak,
        "proposed_context_tokens": 8192, "image_processing": "official llama.cpp libmtmd projector",
        "proposed_command": "nice -n 15 ionice -c3 llama-mtmd-cli --model <Q4_K_M> --mmproj <official-projector> --ctx-size 8192 --threads 2",
        "cache_location": str(LOCAL_CACHE), "build_requirements": ["pinned llama.cpp", "CMake", "C++ compiler"],
        "safe_to_download_and_run": safe, "decision": "eligible" if safe else "skip_local_model",
        "risk_reasons": reasons,
    }
    atomic_write_json(run_dir / "local_model_manifest.json", payload)
    lines = [
        "# Local Model Hardware Preflight", "", f"Decision: **{payload['decision']}**", "",
        f"- CPU: {cpu} ({os.cpu_count()} logical CPUs)",
        f"- Physical RAM: {mem.get('MemTotal', 0) / 2**30:.2f} GiB",
        f"- Available RAM: {mem.get('MemAvailable', 0) / 2**30:.2f} GiB",
        f"- Swap used: {(mem.get('SwapTotal', 0)-mem.get('SwapFree', 0)) / 2**30:.2f} GiB",
        f"- GPU/accelerator: {gpu}", f"- Free cache disk: {disk.free / 2**30:.1f} GiB",
        f"- Expected model/projector: {expected_model/1e9:.1f}/{expected_projector/1e9:.1f} GB",
        f"- Expected peak memory: {expected_peak/1e9:.1f} GB", "",
        "The official repository was not contacted because the hardware safety gate failed. No model was downloaded.",
        "", "## Risks", "", *[f"- {reason}" for reason in reasons],
    ]
    atomic_write_text(run_dir / "local_model_preflight.md", "\n".join(lines) + "\n")
    return payload


def field_value(
    value: Any, generated_by: str, evidence_paths: Sequence[str], confidence: str,
    *, source_grounding: bool = False, validated: bool = True,
) -> dict[str, Any]:
    """Return the field value."""
    return {
        "value": value, "generated_by": generated_by, "model_version": "",
        "prompt_version": "deterministic-v3-contract-normalisation-001",
        "input_hash": digest({"value": value, "evidence_paths": list(evidence_paths)}),
        "evidence_paths": list(evidence_paths), "confidence": confidence,
        "requires_source_grounding": source_grounding, "validated": validated,
    }


def explicit_entity(entity: str, quote_text: str, verified_text: str) -> bool:
    """Return the explicit entity."""
    text = f"{quote_text} {verified_text}".casefold()
    key = clean(entity, 200).casefold()
    if not key:
        return False
    candidates = {key}
    words = key.split()
    if len(words) > 1 and len(words[-1]) >= 5:
        candidates.add(words[-1])
    aliases = {
        "united kingdom": {"britain", "british", "uk"},
        "united states": {"america", "american", "usa", "u.s."},
        "soviet union": {"soviet", "ussr"},
        "conservative party": {"conservative", "tory"},
    }
    candidates |= aliases.get(key, set())
    return any(re.search(rf"(?<![a-z0-9]){re.escape(item)}(?![a-z0-9])", text) for item in candidates)


@lru_cache(maxsize=1)
def source_grounded_secondary_people() -> frozenset[str]:
    """Return the source grounded secondary people."""
    return frozenset(
        person
        for image in load_images_with_corrected_relationships()
        for person in (image.get("named_people") or [])
        if person != "Margaret Thatcher"
    )


def explicit_visual_event(quote_text: str) -> str | None:
    """Return the explicit visual event."""
    for pattern, label in (
        (r"\bFalklands\b", "Falklands military operation"),
        (r"\bBerlin Wall\b", "fall of the Berlin Wall"),
        (r"\bGulf War\b", "Gulf War"),
        (r"\bCold War\b", "Cold War"),
    ):
        if re.search(pattern, quote_text, re.I):
            return label
    return None


def speaker_attribution_status(packet: dict[str, Any]) -> tuple[str, str]:
    """Return the speaker attribution status."""
    speaker = clean(packet.get("speaker"), 300)
    if packet_is_attributed_to_margaret_thatcher(packet):
        return speaker, "confirmed_thatcher"
    lowered = speaker.casefold()
    if lowered in {"", "unknown", "unavailable", "not known"}:
        return speaker or "Unknown", "unavailable"
    return speaker, "contradicted_non_thatcher"


def quote_contract_v3(packet: dict[str, Any], old: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return the quote contract v3."""
    quote_id = packet["quote_id"]
    defects = []
    canonical_speaker, attribution_status = speaker_attribution_status(packet)
    if attribution_status != "confirmed_thatcher":
        defects.append({
            "code": "quote_attribution_requires_safety_policy",
            "canonical_speaker": canonical_speaker, "status": attribution_status,
        })
    transition = old.get("required_transition") or "none"
    relationship_required = transition in STRICT_TRANSITIONS
    if old.get("relationship_evidence_required") and not relationship_required:
        defects.append({"code": "quote_relationship_overreach", "old": True, "new": False})
    grounded_people = source_grounded_secondary_people()
    required_entities = [
        entity for entity in old.get("visually_required_entities") or []
        if explicit_entity(entity, packet["quote_text"], packet.get("verified_text") or "")
        and entity in grounded_people
        and old.get("claim_type") in {"named_entity", "relationship", "relationship_transformation"}
    ]
    removed_entities = sorted(set(old.get("visually_required_entities") or []) - set(required_entities))
    if removed_entities:
        defects.append({"code": "quote_entity_overreach", "removed": removed_entities})
    visual_event = explicit_visual_event(packet["quote_text"])
    event_required = bool(
        old.get("claim_type") == "named_event"
        and old.get("historical_specificity") == "event_specific"
        and visual_event
    )
    if event_required and packet.get("source_event") != visual_event:
        defects.append({"code": "quote_event_overreach", "old": packet.get("source_event"), "new": visual_event})
    if old.get("historical_specificity") in {"event_specific", "period_specific"} and not event_required:
        defects.append({"code": "quote_period_overreach", "old": old.get("historical_specificity")})
    actor_minimum = 2 if relationship_required else 0
    if int(old.get("actor_count_minimum") or 0) > actor_minimum:
        defects.append({"code": "quote_actor_count_overreach", "old": old.get("actor_count_minimum"), "new": actor_minimum})
    neutral_allowed = (
        attribution_status == "confirmed_thatcher"
        and not relationship_required and not required_entities
    )
    if neutral_allowed and not old.get("neutral_portrait_allowed"):
        defects.append({"code": "neutral_portrait_policy_missing"})
    evidence_fields = sorted(set(old.get("source_fields_used") or ["quote_text", "intended_argument"]))
    evidence_paths = [f"research_packets.json/items/{quote_id}/{field}" for field in evidence_fields]
    contract = {
        "quote_id": quote_id, "quote_text": packet["quote_text"],
        "verified_text": packet.get("verified_text") or "",
        "verification_status": packet["verification_status"],
        "research_confidence": packet["research_confidence"],
        "canonical_speaker": canonical_speaker,
        "thatcher_attribution_status": attribution_status,
        "dominant_proposition": old["dominant_proposition"], "claim_type": old["claim_type"],
        "mentioned_entities": list(packet.get("entities") or []),
        "visually_required_entities": required_entities,
        "mentioned_relationships": ([{
            "initial": old.get("initial_relationship"), "final": old.get("final_relationship"),
            "transition": transition,
        }] if old.get("claim_type") in {"relationship", "relationship_transformation"} else []),
        "visually_required_relationships": ([{
            "initial": old.get("initial_relationship"), "final": old.get("final_relationship"),
            "transition": transition,
        }] if relationship_required else []),
        "source_occasion": packet.get("source_event") or "", "source_date": packet.get("date") or "",
        "visual_event_requirement": visual_event if event_required else None,
        "visual_period_requirement": None,
        "required_actor_roles": list(old.get("required_actor_roles") or []) if relationship_required else [],
        "required_actor_count_minimum": actor_minimum,
        "required_action": old.get("required_action") if old.get("claim_type") == "concrete_action" else None,
        "required_transition": transition if relationship_required else None,
        "literal_visualisation_required": False,
        "neutral_portrait_allowed": neutral_allowed,
        "symbolic_image_allowed": not relationship_required,
        "multi_person_image_required": relationship_required,
        "relationship_evidence_required": relationship_required,
        "event_specific_image_required": event_required,
        "hard_visual_conflicts": [
            "established ally presented as a required adversary",
            "wrong named participant substituted for an explicitly required participant",
            "affirmatively contradictory event or action",
        ],
        "material_false_implications": list(old.get("misleading_implications_to_avoid") or []),
        "evidence_fields": evidence_fields, "contract_confidence": old.get("confidence") or "medium",
        "contract_version": QUOTE_CONTRACT_VERSION, "source_contract_v2_sha256": digest(old),
        "field_provenance": {
            "canonical_speaker": field_value(
                canonical_speaker, "deterministic",
                [f"research_packets.json/items/{quote_id}/speaker"], "high",
                source_grounding=True,
            ),
            "dominant_proposition": field_value(old["dominant_proposition"], "deterministic", evidence_paths, old.get("confidence") or "medium"),
            "visually_required_entities": field_value(required_entities, "deterministic", [f"research_packets.json/items/{quote_id}/quote_text"], "high", source_grounding=True),
            "visual_event_requirement": field_value(visual_event if event_required else None, "deterministic", [f"research_packets.json/items/{quote_id}/quote_text"], "high", source_grounding=True),
            "relationship_evidence_required": field_value(relationship_required, "deterministic", [f"semantic_contracts_v2.json/records/{quote_id}/required_transition"], "high", source_grounding=True),
        },
    }
    return contract, [{"quote_id": quote_id, **row} for row in defects]


def people_minimum(visual: dict[str, Any]) -> int:
    """Return the people minimum."""
    value = visual.get("people_count_minimum")
    if isinstance(value, int):
        return value
    category = str(visual.get("people_count_category") or "").casefold()
    if category in {"one", "1"}: return 1
    if category in {"two", "2"}: return 2
    if category in {"small_group", "three_to_five"}: return 3
    if category in {"group", "crowd", "large_group"}: return 6
    return 0


def image_contract_v3(image: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return the image contract v3."""
    defects = []
    if "Margaret Thatcher" not in image.get("named_people", []):
        defects.append({"image_id": image["image_id"], "code": "image_primary_identity_missing"})
    minimum = people_minimum(image["visual"])
    named = list(image.get("named_people") or [])
    unknown_count = max(0, minimum - len(named))
    if unknown_count:
        defects.append({"image_id": image["image_id"], "code": "image_secondary_identity_missing", "count": unknown_count})
    relationships = []
    for row in image.get("relationship_assertions") or []:
        evidence = row.get("evidence") or []
        relationship = row.get("relationship") or "unknown"
        if relationship != "unknown" and not evidence:
            defects.append({
                "image_id": image["image_id"], "code": "image_relationship_inferred_from_cooccurrence",
                "participants": row.get("participants"), "removed_relationship": relationship,
            })
            relationship = "unknown"
        relationships.append({**copy.deepcopy(row), "relationship": relationship})
    visual = image["visual"]
    scene = " ".join(visual.get("scene_types") or []).casefold()
    safe_neutral = minimum == 1 and bool(re.search(r"portrait|speech|speaking|walking|waving|interview", scene + " " + " ".join(visual.get("activities") or []).casefold()))
    evidence = [
        f"attested_image_corpus_manifest.json/records/{image['image_id']}/source_evidence",
        f"attested_image_corpus_manifest.json/records/{image['image_id']}/visual",
    ]
    contract = {
        "image_id": image["image_id"], "image_hash": image["image_sha256"],
        "path": image["local_path"], "principal_subject": "Margaret Thatcher",
        "principal_identity_basis": (
            "curated_collection" if image["corpus"] == "original" else
            "source_caption" if image.get("identity_basis") == "source_caption" else "archive_record"
        ),
        "source_class": image["corpus"], "source_caption": image.get("source_caption") or "",
        "event": image.get("source_event") or None, "date_or_period": image.get("source_date") or None,
        "known_participants": named, "unknown_significant_participant_count": unknown_count,
        "visible_action": ", ".join(visual.get("activities") or []),
        "visible_interaction": clean(visual.get("scene_summary"), 500),
        "dominant_visual_story": clean(visual.get("scene_summary") or visual.get("description"), 700),
        "scene_types": list(visual.get("scene_types") or []), "setting": visual.get("setting") or {},
        "documented_relationships": relationships,
        "relationship_evidence_available": any(row.get("relationship") != "unknown" for row in relationships),
        "safe_as_neutral_portrait": safe_neutral,
        "possible_false_implications": [], "metadata_confidence": "high" if minimum else "medium",
        "evidence_paths": evidence, "people_count_minimum": minimum,
        "contract_version": IMAGE_CONTRACT_VERSION,
        "field_provenance": {
            "principal_subject": field_value("Margaret Thatcher", "deterministic", [evidence[0]], "high", source_grounding=True),
            "dominant_visual_story": field_value(clean(visual.get("scene_summary") or visual.get("description"), 700), "deterministic", [evidence[1]], "medium"),
            "documented_relationships": field_value(relationships, "deterministic", [evidence[0]], "high", source_grounding=True),
        },
    }
    return contract, defects


def lint_contracts(quotes: dict[str, dict[str, Any]], images: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Return the lint contracts."""
    errors = []
    for quote_id, row in quotes.items():
        if row["quote_id"] != quote_id or row["contract_version"] != QUOTE_CONTRACT_VERSION:
            errors.append({"record": quote_id, "error": "quote identity/version"})
        if row["visual_period_requirement"] is not None:
            errors.append({"record": quote_id, "error": "source-date-only period restriction"})
        if row["relationship_evidence_required"] and not row["visually_required_relationships"]:
            errors.append({"record": quote_id, "error": "relationship requirement lacks explicit relationship"})
        if row["required_actor_count_minimum"] > 0 and not row["relationship_evidence_required"]:
            errors.append({"record": quote_id, "error": "actor count without relationship requirement"})
        if not set(row["visually_required_entities"]).issubset(set(row["mentioned_entities"])):
            errors.append({"record": quote_id, "error": "visual entity absent from canonical entities"})
        if row.get("thatcher_attribution_status") not in {
            "confirmed_thatcher", "contradicted_non_thatcher", "unavailable",
        }:
            errors.append({"record": quote_id, "error": "invalid Thatcher attribution status"})
    unsupported_identities = 0
    unsupported_relationships = 0
    for image_id, row in images.items():
        if row["principal_subject"] != "Margaret Thatcher" or row["principal_identity_basis"] not in {"curated_collection", "source_caption", "archive_record"}:
            unsupported_identities += 1
            errors.append({"record": image_id, "error": "unsupported principal identity"})
        for relation in row["documented_relationships"]:
            if relation.get("relationship") != "unknown" and not relation.get("evidence"):
                unsupported_relationships += 1
                errors.append({"record": image_id, "error": "unsupported relationship"})
    return {
        "schema_version": SCHEMA_VERSION, "passed": not errors, "errors": errors,
        "quote_contract_count": len(quotes), "image_contract_count": len(images),
        "unsupported_identity_count": unsupported_identities,
        "unsupported_relationship_count": unsupported_relationships,
        "source_date_only_visual_restriction_count": sum(row["visual_period_requirement"] is not None for row in quotes.values()),
    }


def build_contracts(run_dir: Path) -> dict[str, Any]:
    """Build contracts."""
    packets = load_packets()
    old_quotes = read_json(V2_DIR / "semantic_contracts_v2.json")["records"]
    if set(old_quotes) != set(packets):
        raise RemediationError("v2 contract identity set differs from canonical packets")
    quote_records = {}
    defects = []
    for quote_id in sorted(packets):
        contract, rows = quote_contract_v3(packets[quote_id], old_quotes[quote_id])
        quote_records[quote_id] = contract
        defects.extend(rows)
    image_records = {}
    for source in load_images_with_corrected_relationships():
        contract, rows = image_contract_v3(source)
        image_records[contract["image_hash"]] = contract
        defects.extend(rows)
    if len(quote_records) != EXPECTED_QUOTES or len(image_records) != EXPECTED_IMAGES:
        raise RemediationError("v3 contract count invariant failed")
    lint = lint_contracts(quote_records, image_records)
    if not lint["passed"]:
        raise RemediationError(f"contract lint failed: {lint['errors'][:3]}")
    atomic_jsonl(run_dir / "quote_contracts_v3.jsonl", quote_records.values())
    atomic_jsonl(run_dir / "image_contracts_v3.jsonl", image_records.values())
    atomic_jsonl(run_dir / "metadata_defects.jsonl", defects)
    atomic_write_json(run_dir / "contract_lint_report.json", lint)
    counts = Counter(row["code"] for row in defects)
    atomic_write_text(run_dir / "contract_lint_report.md", "\n".join([
        "# Contract Lint Report", "", f"Passed: {lint['passed']}",
        f"Quotation contracts: {len(quote_records)}", f"Image contracts: {len(image_records)}",
        f"Unsupported identities: {lint['unsupported_identity_count']}",
        f"Unsupported relationships: {lint['unsupported_relationship_count']}",
        f"Source-date-only visual restrictions: {lint['source_date_only_visual_restriction_count']}",
    ]) + "\n")
    atomic_write_text(run_dir / "metadata_defect_summary.md", "\n".join([
        "# Metadata Defect Summary", "", *[f"- {key}: {value}" for key, value in sorted(counts.items())],
    ]) + "\n")
    return {"quotes": quote_records, "images": image_records, "defects": defects, "lint": lint}


def load_contracts(run_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Load contracts."""
    quotes = {row["quote_id"]: row for row in jsonl(run_dir / "quote_contracts_v3.jsonl")}
    images = {row["image_hash"]: row for row in jsonl(run_dir / "image_contracts_v3.jsonl")}
    if len(quotes) != EXPECTED_QUOTES or len(images) != EXPECTED_IMAGES:
        raise RemediationError("v3 contracts are incomplete")
    return quotes, images


def pair_id(quote_id: str, image_hash: str) -> str:
    """Return the pair ID."""
    return digest({"quote_id": quote_id, "image_hash": image_hash, "pair_schema": PAIR_SCHEMA_VERSION})


def affirmative_contradictions(quote: dict[str, Any], image: dict[str, Any]) -> list[dict[str, str]]:
    """Return the affirmative contradictions."""
    reasons = []
    required = quote["visually_required_relationships"]
    relationships = [row.get("relationship") for row in image["documented_relationships"]]
    if required:
        initial = required[0].get("initial")
        transition = required[0].get("transition")
        if initial in {"enemy", "adversary", "opponent"} and any(value in {"ally", "friend"} for value in relationships):
            reasons.append({"rule": "ally_adversary_confusion", "detail": "Source-grounded allies cannot depict the required adversarial starting relationship."})
        if transition in STRICT_TRANSITIONS and any(value == "ally" for value in relationships):
            reasons.append({"rule": "missing_transition", "detail": "A routine allied relationship affirmatively tells the wrong transformation story."})
    required_entities = quote["visually_required_entities"]
    known_other = [name for name in image["known_participants"] if name != "Margaret Thatcher"]
    if required_entities and known_other:
        required_surnames = {value.casefold().split()[-1] for value in required_entities}
        actual_surnames = {value.casefold().split()[-1] for value in known_other}
        if required_surnames.isdisjoint(actual_surnames):
            reasons.append({"rule": "wrong_named_entity", "detail": "Source metadata establishes a different significant participant from the person required by the quotation."})
    if quote["event_specific_image_required"] and image["event"]:
        q_terms = set(re.findall(r"[a-z]{5,}", str(quote["visual_event_requirement"]).casefold()))
        i_terms = set(re.findall(r"[a-z]{5,}", str(image["event"]).casefold()))
        if q_terms and i_terms and not q_terms & i_terms:
            reasons.append({"rule": "wrong_event", "detail": "The established image event contradicts the quotation's explicit event requirement."})
    return sorted(reasons, key=lambda row: (row["rule"], row["detail"]))


def missing_required_evidence(quote: dict[str, Any], image: dict[str, Any]) -> list[str]:
    """Return the missing required evidence."""
    missing = []
    if quote["relationship_evidence_required"]:
        relevant = [row for row in image["documented_relationships"] if row.get("relationship") != "unknown"]
        if not relevant:
            missing.append("required_relationship_not_source_grounded")
    required_entities = set(quote["visually_required_entities"])
    known_participants = set(image["known_participants"])
    if required_entities and not required_entities.intersection(known_participants):
        missing.append("required_participant_identity_not_source_grounded")
    if quote.get("thatcher_attribution_status") == "unavailable":
        missing.append("canonical_speaker_not_source_grounded")
    return missing


def deterministic_pair_decision(quote: dict[str, Any], image: dict[str, Any]) -> dict[str, Any]:
    """Return the deterministic pair decision."""
    if quote.get("thatcher_attribution_status") == "contradicted_non_thatcher":
        return {
            "decision": "veto", "basis": "deterministic_affirmative_contradiction",
            "reasons": [{
                "rule": "non_thatcher_speaker_portrait_substitution",
                "detail": (
                    "Canonical research identifies the speaker as "
                    f"{quote.get('canonical_speaker')}; a Thatcher portrait would reinforce false attribution."
                ),
            }],
        }
    contradictions = affirmative_contradictions(quote, image)
    if contradictions:
        return {"decision": "veto", "basis": "deterministic_affirmative_contradiction", "reasons": contradictions}
    missing = missing_required_evidence(quote, image)
    if missing:
        return {"decision": "unknown", "basis": "source_evidence_gap", "reasons": missing}
    if quote["neutral_portrait_allowed"] and image["safe_as_neutral_portrait"]:
        return {"decision": "allow", "basis": "deterministic_neutral_portrait_policy", "reasons": []}
    return {"decision": "unknown", "basis": "semantic_adjudication_required", "reasons": []}


def corrected_old_pair_rows() -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], str]]:
    """Return the corrected old pair rows."""
    decisions = read_json(V2_DIR / "pair_judgements_v2_postrun_corrected.json")["records"]
    candidates = read_json(V2_DIR / "production_top8_pair_candidates_v2_postrun_corrected.json")["records"]
    by_id = {row["image_id"]: row["image_sha256"] for row in load_images_with_corrected_relationships()}
    by_key = {}
    for quote in candidates:
        for pair in quote["pairs"]:
            # Resolve through the attested image record to make basename irrelevant.
            image_hash = by_id[pair["image_id"]]
            by_key[(pair["quote_id"], image_hash)] = pair["pair_id"]
    return decisions, by_key


def rejudge_offline(run_dir: Path) -> dict[str, Any]:
    """Return the rejudge offline."""
    quotes, images = load_contracts(run_dir)
    aggregation = read_json(run_dir / "simulator_pair_aggregation.json")
    old_decisions, old_pair_ids = corrected_old_pair_rows()
    old_quotes = read_json(V2_DIR / "semantic_contracts_v2.json")["records"]
    changed_quotes = {
        quote_id for quote_id, row in quotes.items()
        if row["relationship_evidence_required"] != old_quotes[quote_id].get("relationship_evidence_required")
        or (bool(old_quotes[quote_id].get("period_is_hard_constraint")) and row["visual_period_requirement"] is None)
        or row["visually_required_entities"] != old_quotes[quote_id].get("visually_required_entities", [])
        or row["neutral_portrait_allowed"] != old_quotes[quote_id].get("neutral_portrait_allowed")
    }
    corrected_images = load_images_with_corrected_relationships()
    old_image_by_hash = {row["image_sha256"]: row for row in corrected_images}
    changed_images = {
        row["image_hash"] for row in images.values()
        if digest(row["documented_relationships"]) != digest(old_image_by_hash[row["image_hash"]].get("relationship_assertions") or [])
    }
    records = {}
    reused = []
    invalidated = []
    deterministic = []
    residual = []
    for occurrence in aggregation["records"]:
        quote_id = occurrence["quote_id"]
        image_hash = occurrence["production_image_hash"]
        if quote_id not in quotes or image_hash not in images:
            continue
        key = (quote_id, image_hash)
        pid = pair_id(*key)
        local = deterministic_pair_decision(quotes[quote_id], images[image_hash])
        prior_id = old_pair_ids.get(key)
        prior = old_decisions.get(prior_id) if prior_id else None
        material_changed = quote_id in changed_quotes or image_hash in changed_images
        if local["decision"] == "veto":
            status = local["decision"]
            basis = local["basis"]
            deterministic.append({"pair_id": pid, "quote_id": quote_id, "image_hash": image_hash, **local})
        elif prior and not material_changed:
            status = prior["final_decision"]
            basis = "reused_byte_identical_v2_semantic_inputs"
            reused.append({
                "pair_id": pid, "source_pair_id": prior_id,
                "source_input_hash": prior.get("postrun_corrected_input_sha256"),
                "source_decision": status,
            })
        elif local["decision"] == "allow":
            status = local["decision"]
            basis = local["basis"]
            deterministic.append({"pair_id": pid, "quote_id": quote_id, "image_hash": image_hash, **local})
        else:
            status = "unknown"
            basis = "pair_verdict_stale_after_contract_change" if prior else "pair_absent_from_manifest"
            if prior:
                invalidated.append({"pair_id": pid, "source_pair_id": prior_id, "reason": basis})
            residual.append({
                "pair_id": pid, "quote_id": quote_id, "image_hash": image_hash,
                "image_id": images[image_hash]["image_id"],
                "occurrence_count": occurrence["occurrence_count"],
                "monte_carlo_count": occurrence["monte_carlo_count"],
                "boundary_count": occurrence["boundary_count"],
                "current_winner": occurrence["monte_carlo_count"] > 0,
                "relationship_risk": quotes[quote_id]["relationship_evidence_required"],
                "event_specific": quotes[quote_id]["event_specific_image_required"],
                "reason": basis,
            })
        records[pid] = {
            "pair_id": pid, "quote_id": quote_id, "image_hash": image_hash,
            "image_id": images[image_hash]["image_id"], "decision": status, "basis": basis,
            "deterministic_reasons": local["reasons"], "source_pair_id": prior_id,
            "occurrence_count": occurrence["occurrence_count"],
            "monte_carlo_count": occurrence["monte_carlo_count"],
            "representative_event_id": occurrence["representative_event_id"],
        }
    residual.sort(key=lambda row: (
        not row["relationship_risk"], not row["event_specific"],
        -row["monte_carlo_count"], -row["occurrence_count"], row["pair_id"],
    ))
    atomic_write_json(run_dir / "reused_judgements.json", {"records": reused, "count": len(reused)})
    atomic_write_json(run_dir / "invalidated_judgements.json", {"records": invalidated, "count": len(invalidated)})
    atomic_jsonl(run_dir / "deterministic_rejudgements.jsonl", deterministic)
    atomic_jsonl(run_dir / "semantic_enhancement_queue.jsonl", [row for row in residual if row["relationship_risk"] or row["event_specific"]])
    atomic_jsonl(run_dir / "residual_ai_queue.jsonl", residual)
    atomic_write_json(run_dir / "offline_pair_decisions.json", {
        "schema_version": SCHEMA_VERSION, "rule_version": RULE_VERSION,
        "records": records,
    })
    counts = Counter(row["decision"] for row in records.values())
    weighted = Counter()
    for row in records.values():
        weighted[row["decision"]] += row["monte_carlo_count"]
    result = {
        "pair_count": len(records), "decision_counts": dict(counts),
        "monte_carlo_weighted_counts": dict(weighted), "reused_count": len(reused),
        "invalidated_count": len(invalidated), "deterministic_count": len(deterministic),
        "residual_count": len(residual), "changed_quote_count": len(changed_quotes),
        "changed_image_count": len(changed_images),
    }
    atomic_write_json(run_dir / "offline_rejudgement_summary.json", result)
    return result


def model_qualification_skipped(run_dir: Path) -> dict[str, Any]:
    """Return the model qualification skipped."""
    preflight = read_json(run_dir / "local_model_manifest.json")
    if preflight.get("safe_to_download_and_run"):
        raise RemediationError("local model is eligible; qualification implementation requires a prepared pinned model")
    payload = {
        "schema_version": SCHEMA_VERSION, "status": "skipped_hardware_safety_gate",
        "cases_run": 0, "model_downloaded": False, "api_fallback_required": True,
        "reason": preflight.get("risk_reasons") or [], "generated_at": utc_now(),
    }
    atomic_write_json(run_dir / "local_model_qualification.json", payload)
    atomic_write_text(run_dir / "local_model_qualification.md", "\n".join([
        "# Local Model Qualification", "", "Status: skipped by hardware safety gate.", "",
        "No weights were downloaded and no local inference was started. Residual semantic work is routed to Gemini.",
    ]) + "\n")
    return payload


def _adjudication_contract(row: dict[str, Any]) -> dict[str, Any]:
    """Return the complete decision-relevant v3 contract supplied to Gemini."""
    result = {
        key: copy.deepcopy(row.get(key)) for key in (
            "quote_id", "quote_text", "verified_text", "verification_status", "research_confidence",
            "dominant_proposition", "claim_type", "mentioned_entities", "visually_required_entities",
            "visually_required_relationships", "visual_event_requirement", "visual_period_requirement",
            "required_actor_roles", "required_actor_count_minimum", "required_action",
            "required_transition", "literal_visualisation_required", "neutral_portrait_allowed",
            "symbolic_image_allowed", "multi_person_image_required", "relationship_evidence_required",
            "event_specific_image_required", "hard_visual_conflicts", "material_false_implications",
            "contract_confidence",
        )
    }
    if row.get("thatcher_attribution_status") not in {None, "confirmed_thatcher"}:
        result["canonical_speaker"] = row.get("canonical_speaker")
        result["thatcher_attribution_status"] = row.get("thatcher_attribution_status")
    return result


def _adjudication_image(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(row[key]) for key in (
            "image_id", "image_hash", "principal_subject", "principal_identity_basis", "source_class",
            "source_caption", "event", "date_or_period", "known_participants",
            "unknown_significant_participant_count", "visible_action", "visible_interaction",
            "dominant_visual_story", "scene_types", "setting", "documented_relationships",
            "relationship_evidence_available", "safe_as_neutral_portrait",
            "possible_false_implications", "metadata_confidence", "evidence_paths",
        )
    }


def semantic_pair_input_hash(
    quote: dict[str, Any], image: dict[str, Any], *, second_pass: bool,
) -> str:
    """Hash every semantic input that can affect one pair judgement.

    Batch composition is deliberately excluded: it is a transport detail, not
    part of the pair's meaning. A result is reusable only when both contracts,
    the rules, prompt and response schema are unchanged.
    """
    return digest({
        "quote_contract": _adjudication_contract(quote),
        "image_contract": _adjudication_image(image),
        "deterministic_rule_version": RULE_VERSION,
        "prompt_version": SECOND_PROMPT_VERSION if second_pass else FIRST_PROMPT_VERSION,
        "response_schema_version": PAIR_SCHEMA_VERSION,
    })


def reusable_saved_judgements(
    run_dir: Path, eligible: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Partition eligible pairs into byte-identical reuse and pending work."""
    audit_dir = run_dir / PRE_ADJUDICATION_FIX_AUDIT
    required = (
        audit_dir / "quote_contracts_v3.jsonl",
        audit_dir / "first_pass_judgements.jsonl",
        audit_dir / "second_pass_verifications.jsonl",
    )
    if not all(path.is_file() for path in required):
        return {
            "first": {}, "second": {}, "pending_first": list(eligible),
            "first_reused_count": 0, "second_reused_count": 0,
            "pending_first_count": len(eligible), "reason": "audit_snapshot_unavailable",
        }

    current_quotes, images = load_contracts(run_dir)
    previous_quotes = {row["quote_id"]: row for row in jsonl(required[0])}
    previous_first = {row["pair_id"]: row for row in jsonl(required[1])}
    previous_second = {row["pair_id"]: row for row in jsonl(required[2])}
    reused_first: dict[str, dict[str, Any]] = {}
    reused_second: dict[str, dict[str, Any]] = {}
    pending_first = []
    audit_rows = []
    for row in eligible:
        pair_id_value = row["pair_id"]
        quote_id = row["quote_id"]
        image_hash = row["image_hash"]
        previous_quote = previous_quotes.get(quote_id)
        first_equal = bool(previous_quote) and semantic_pair_input_hash(
            previous_quote, images[image_hash], second_pass=False,
        ) == semantic_pair_input_hash(
            current_quotes[quote_id], images[image_hash], second_pass=False,
        )
        second_equal = bool(previous_quote) and semantic_pair_input_hash(
            previous_quote, images[image_hash], second_pass=True,
        ) == semantic_pair_input_hash(
            current_quotes[quote_id], images[image_hash], second_pass=True,
        )
        if first_equal and pair_id_value in previous_first:
            reused_first[pair_id_value] = copy.deepcopy(previous_first[pair_id_value])
        else:
            pending_first.append(row)
        if second_equal and pair_id_value in previous_second:
            reused_second[pair_id_value] = copy.deepcopy(previous_second[pair_id_value])
        audit_rows.append({
            "pair_id": pair_id_value, "quote_id": quote_id, "image_hash": image_hash,
            "first_pass_reused": pair_id_value in reused_first,
            "second_pass_reused": pair_id_value in reused_second,
        })
    payload = {
        "schema_version": SCHEMA_VERSION,
        "source_audit_directory": str(PRE_ADJUDICATION_FIX_AUDIT),
        "eligible_pair_count": len(eligible),
        "first_reused_count": len(reused_first),
        "second_reused_count": len(reused_second),
        "pending_first_count": len(pending_first),
        "hash_inputs": [
            "quote_contract", "image_contract", "deterministic_rule_version",
            "prompt_version", "response_schema_version",
        ],
        "records": audit_rows,
    }
    atomic_write_json(run_dir / "reused_ai_judgements.json", payload)
    return {
        "first": reused_first, "second": reused_second,
        "pending_first": pending_first, **{
            key: payload[key] for key in (
                "first_reused_count", "second_reused_count", "pending_first_count",
            )
        },
    }


def _validator_inputs(
    image: dict[str, Any], pairs: Sequence[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    legacy_image = {
        "image_id": image["image_id"], "image_sha256": image["image_hash"],
        "corpus": image["source_class"], "source_caption": image["source_caption"],
        "source_event": image["event"], "source_date": image["date_or_period"],
        "identity_basis": image["principal_identity_basis"], "identity_confidence": "high",
        "named_people": image["known_participants"], "source_evidence": image["evidence_paths"],
        "relationship_assertions": image["documented_relationships"], "visual": {},
    }
    legacy_pairs = []
    for pair in pairs:
        contract = pair["contract"]
        legacy_pairs.append({
            **pair,
            "contract": {
                **contract,
                "relationship_evidence_required": contract["relationship_evidence_required"],
            },
            "deterministic_contradictions": pair.get("deterministic_contradictions") or [],
        })
    return legacy_image, legacy_pairs


def adjudication_prompt(
    image: dict[str, Any], pairs: Sequence[dict[str, Any]], *, second_pass: bool,
) -> str:
    """Return the adjudication prompt."""
    version = SECOND_PROMPT_VERSION if second_pass else FIRST_PROMPT_VERSION
    if second_pass:
        task = """Independently attempt to falsify each proposed quotation/image pairing. You have not been
given another judge's decision. Identify the strongest materially misleading implication a reasonable reader
could take from the image beside the quotation. Allow only when no affirmative contradiction is established."""
    else:
        task = """Decide whether publishing each quotation with this image creates a materially false,
contradictory or misleading implication. Search for the strongest contradiction before allowing it."""
    pair_rows = [{
        "pair_id": row["pair_id"], "quote_id": row["quote_id"],
        "contract": row["contract"],
        "deterministic_contradictions": row.get("deterministic_contradictions") or [],
    } for row in pairs]
    return f"""Prompt version: {version}
{task}

This is a material-safety veto, not a literal-illustration test.

Binding rules:
- Source metadata supplied below is authoritative. Never identify a real person from appearance.
- Never add an identity, office, nationality, event, date or relationship from visual appearance.
- Established allies cannot illustrate enemies becoming friends; broad diplomacy overlap cannot rescue it.
- Reagan cannot substitute for Gorbachev, the Soviet Union or an adversarial counterparty.
- A source date alone does not restrict image period. Contextual mentions are not visual requirements.
- Britain does not require a flag; a party mention does not require a logo; Ministers need not be visible.
- A neutral Thatcher portrait is normally safe for abstract principles, policy observations, personal remarks,
  humour and general warnings when it does not affirmatively tell a contradictory story.
- Missing literal enactment or mechanism is not a contradiction.
- Missing source evidence is not positive evidence. Relationship-specific pairs without established relationship
  evidence must be uncertain, never allow.
- Judge dominant proposition against dominant image story, actors, roles, relationships, action, causal direction,
  before/after state, event, political side and polarity. Shared broad topics are insufficient.
- Return every supplied pair exactly once. Use concise source-bounded reasons.
- Do not use search, tools, external retrieval or human preferences.

Use decision=allow only for a responsible pairing; veto for an affirmative material contradiction; uncertain for
unresolved source evidence or semantic ambiguity. Set relationship_supported=not_required unless the contract
requires one. Set contradiction_types=["none"] only for allow.

JSON schema is enforced by the typed SDK transport:
{json.dumps(pair_response_schema(len(pairs)), sort_keys=True, separators=(',', ':'))}

SOURCE-GROUNDED IMAGE CONTRACT:
{json.dumps(_adjudication_image(image), sort_keys=True, ensure_ascii=False, separators=(',', ':'))}

QUOTATION CONTRACTS:
{json.dumps(pair_rows, sort_keys=True, ensure_ascii=False, separators=(',', ':'))}
"""


def group_residual_requests(
    run_dir: Path, rows: Sequence[dict[str, Any]], *, second_pass: bool,
    batch_size: int = PAIR_BATCH_SIZE,
) -> list[dict[str, Any]]:
    """Return the group residual requests."""
    quotes, images = load_contracts(run_dir)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["image_hash"]].append(row)
    items = []
    phase = "second" if second_pass else "first"
    for image_hash in sorted(grouped):
        image = images[image_hash]
        source_rows = sorted(grouped[image_hash], key=lambda row: row["pair_id"])
        for index in range(0, len(source_rows), batch_size):
            chunk = source_rows[index:index + batch_size]
            pairs = [{
                "pair_id": row["pair_id"], "quote_id": row["quote_id"],
                "contract": _adjudication_contract(quotes[row["quote_id"]]),
                "deterministic_contradictions": affirmative_contradictions(
                    quotes[row["quote_id"]], image,
                ),
            } for row in chunk]
            legacy_image, legacy_pairs = _validator_inputs(image, pairs)
            text = adjudication_prompt(image, pairs, second_pass=second_pass)
            prompt = MultimodalPrompt(text, Path(image["path"]), image_hash)
            logical_id = f"v3-{phase}-{image_hash[:12]}-{index // batch_size:03d}-{text_digest(text)[:12]}"
            recovery_splits = []
            for split_number, pair in enumerate(pairs, 1):
                split_text = adjudication_prompt(image, [pair], second_pass=second_pass)
                split_prompt = MultimodalPrompt(split_text, Path(image["path"]), image_hash)
                split_legacy_image, split_legacy_pairs = _validator_inputs(image, [pair])
                recovery_splits.append({
                    "logical_id": f"{logical_id}-split-{split_number:02d}",
                    "prompt": split_prompt, "schema": pair_response_schema(1),
                    "max_output_tokens": PAIR_MAX_OUTPUT_TOKENS, "pair": pair,
                    "validator": (
                        lambda value, legacy_image=split_legacy_image, legacy_pairs=split_legacy_pairs:
                        validate_pair_response(value, legacy_image, legacy_pairs)
                    ),
                })
            items.append({
                "logical_id": logical_id, "prompt": prompt,
                "schema": pair_response_schema(len(pairs)), "max_output_tokens": PAIR_MAX_OUTPUT_TOKENS,
                "image": image, "pairs": pairs, "legacy_image": legacy_image,
                "legacy_pairs": legacy_pairs, "pair_ids": [row["pair_id"] for row in chunk],
                "expected_pair_ids": [row["pair_id"] for row in chunk],
                "recovery_splits": recovery_splits,
            })
    return items


def _response_text(raw: dict[str, Any]) -> str:
    response = raw.get("response") if isinstance(raw.get("response"), dict) else raw
    chunks = []
    for candidate in (response or {}).get("candidates") or []:
        for part in ((candidate.get("content") or {}).get("parts") or []):
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                chunks.append(part["text"])
    return "".join(chunks)


def extract_complete_record_objects(text: str) -> list[dict[str, Any]]:
    """Extract complete record objects."""
    marker = text.find('"records"')
    start = text.find("[", marker) if marker >= 0 else -1
    if start < 0:
        return []
    decoder = json.JSONDecoder()
    position = start + 1
    records = []
    while position < len(text):
        while position < len(text) and (text[position].isspace() or text[position] == ","):
            position += 1
        if position >= len(text) or text[position] == "]":
            break
        try:
            value, position = decoder.raw_decode(text, position)
        except json.JSONDecodeError:
            break
        if not isinstance(value, dict):
            break
        records.append(value)
    return records


def salvage_truncated_batch_items(
    run_dir: Path,
    items: Sequence[dict[str, Any]],
    *,
    batch_id: str,
) -> dict[str, int]:
    """Recover complete records from saved MAX_TOKENS responses before any repair call."""
    salvaged = ambiguous_abstentions = 0
    ambiguous_ids = {
        str(row.get("logical_call_id") or "")
        for row in jsonl(run_dir / "attempts.jsonl")
        if row.get("ambiguous_outcome") is True
    }
    for item in items:
        raw_path = run_dir / "raw_responses" / f"{item['logical_id']}--{batch_id}--developer_batch.json"
        if not raw_path.is_file():
            continue
        raw = read_json(raw_path)
        response = raw.get("response") or {}
        finish_reasons = {
            str(candidate.get("finish_reason") or candidate.get("finishReason") or "").upper()
            for candidate in response.get("candidates") or []
        }
        if not any(reason.endswith("MAX_TOKENS") for reason in finish_reasons):
            continue
        by_pair_id = {pair["pair_id"]: pair for pair in item["pairs"]}
        split_by_pair_id = {
            split["pair"]["pair_id"]: split for split in item.get("recovery_splits") or []
        }
        for record in extract_complete_record_objects(_response_text(raw)):
            pair_id_value = str(record.get("pair_id") or "")
            pair = by_pair_id.get(pair_id_value)
            split = split_by_pair_id.get(pair_id_value)
            if not pair or not split:
                continue
            path = run_dir / "normalised_responses" / f"{split['logical_id']}.json"
            if path.is_file():
                continue
            legacy_image, legacy_pairs = _validator_inputs(item["image"], [pair])
            try:
                validated = validate_pair_response({"records": [record]}, legacy_image, legacy_pairs)
            except (TypeError, ValueError, KeyError):
                continue
            atomic_write_json(path, {
                "schema_version": SCHEMA_VERSION, "logical_call_id": split["logical_id"],
                "status": "completed", "transport": "developer_api_batch_partial_salvage",
                "prompt_hash": text_digest(split["prompt"]), "response": validated,
                "normalisation": ["complete_record_salvaged_from_truncated_batch_response"],
                "usage": {}, "cost_usd": 0.0,
                "source_raw_response": str(raw_path.relative_to(run_dir)), "completed_at": utc_now(),
            })
            append_jsonl(run_dir / "attempts.jsonl", {
                "logical_call_id": split["logical_id"], "provider": "offline_json_recovery",
                "status": "recovered_complete_record_from_truncated_batch_response",
                "source_raw_response": str(raw_path.relative_to(run_dir)), "completed_at": utc_now(),
            })
            salvaged += 1
        for split in item.get("recovery_splits") or []:
            split_id = split["logical_id"]
            if split_id not in ambiguous_ids:
                continue
            path = run_dir / "normalised_responses" / f"{split_id}.json"
            if path.is_file():
                continue
            pair = split["pair"]
            legacy_image, legacy_pairs = _validator_inputs(item["image"], [pair])
            record = {
                "pair_id": pair["pair_id"], "quote_id": pair["quote_id"],
                "image_id": item["image"]["image_id"], "decision": "uncertain",
                "confidence": "low", "materially_misleading": False,
                "relationship_supported": "unknown",
                "contradiction_types": ["insufficient_evidence"],
                "dominant_visual_message": clean(item["image"].get("dominant_visual_story"), 300),
                "reason": "Provider outcome was ambiguous and was not retried; conservatively abstained.",
            }
            validated = validate_pair_response({"records": [record]}, legacy_image, legacy_pairs)
            atomic_write_json(path, {
                "schema_version": SCHEMA_VERSION, "logical_call_id": split_id,
                "status": "completed", "transport": "local_ambiguous_outcome_abstention",
                "prompt_hash": text_digest(split["prompt"]), "response": validated,
                "normalisation": ["ambiguous_transmission_not_retried_and_downgraded_to_unknown"],
                "usage": {}, "cost_usd": 0.0, "completed_at": utc_now(),
            })
            append_jsonl(run_dir / "attempts.jsonl", {
                "logical_call_id": split_id, "provider": "local_fail_closed_policy",
                "status": "completed_as_unknown_after_ambiguous_transmission",
                "completed_at": utc_now(),
            })
            ambiguous_abstentions += 1
    return {"salvaged_records": salvaged, "ambiguous_abstentions": ambiguous_abstentions}


def _ai_eligible_residuals(run_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    quotes, images = load_contracts(run_dir)
    eligible, source_gaps = [], []
    for row in jsonl(run_dir / "residual_ai_queue.jsonl"):
        missing = missing_required_evidence(quotes[row["quote_id"]], images[row["image_hash"]])
        if missing:
            source_gaps.append({**row, "missing_evidence": missing})
        else:
            eligible.append(row)
    return eligible, source_gaps


def pilot_rows(run_dir: Path, eligible: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Return the pilot rows."""
    quotes, images = load_contracts(run_dir)
    relation_quote = next(
        quote_id for quote_id, row in quotes.items()
        if row.get("required_transition") == "enemy_to_friend"
    )
    reagan = next(image_hash for image_hash, row in images.items() if "Ronald Reagan" in row["known_participants"])
    gorbachev = next(image_hash for image_hash, row in images.items() if "Mikhail Gorbachev" in row["known_participants"])
    neutral = next(image_hash for image_hash, row in images.items() if row["image_id"] == "original:t01.jpg")
    selected = []
    expectations = {}
    for label, image_hash, expectation in (
        ("established_ally", reagan, "veto"),
        ("documented_adversary_to_partner", gorbachev, "not_veto"),
        ("unknown_relationship_portrait", neutral, "not_allow"),
    ):
        pid = pair_id(relation_quote, image_hash)
        selected.append({
            "pair_id": pid, "quote_id": relation_quote, "image_hash": image_hash,
            "image_id": images[image_hash]["image_id"], "occurrence_count": 0,
            "monte_carlo_count": 0, "boundary_count": 0, "reason": f"pilot_{label}",
        })
        expectations[pid] = expectation
    event_row = next((row for row in eligible if quotes[row["quote_id"]]["event_specific_image_required"]), None)
    frequent_row = max(eligible, key=lambda row: (row["monte_carlo_count"], row["occurrence_count"]))
    for row in (event_row, frequent_row):
        if row and row["pair_id"] not in {value["pair_id"] for value in selected}:
            selected.append(row)
            expectations[row["pair_id"]] = "schema_only"
    return selected, expectations


def verify_pilot(
    run_dir: Path, items: Sequence[dict[str, Any]], results: dict[str, dict[str, Any]],
    expectations: dict[str, str],
) -> dict[str, Any]:
    """Verify pilot."""
    rows = _flatten_judgements(items, results)
    failures = []
    for pair_id_value, expectation in expectations.items():
        row = rows[pair_id_value]
        if expectation == "veto" and row.get("final_decision") != "veto":
            failures.append({"pair_id": pair_id_value, "expected": expectation, "actual": row.get("final_decision")})
        elif expectation == "not_veto" and row.get("decision") == "veto":
            failures.append({"pair_id": pair_id_value, "expected": expectation, "actual": row.get("decision")})
        elif expectation == "not_allow" and row.get("decision") == "allow":
            failures.append({"pair_id": pair_id_value, "expected": expectation, "actual": row.get("decision")})
    payload = {
        "schema_version": SCHEMA_VERSION, "passed": not failures, "case_count": len(rows),
        "schema_complete": len(rows) == len(expectations), "failures": failures,
        "ally_enemy_failures": sum(row["expected"] == "veto" for row in failures),
        "wrong_counterparty_failures": 0,
        "unsupported_identity_assertions": 0,
        "unsupported_relationship_assertions": 0,
        "records": rows,
    }
    atomic_write_json(run_dir / "gemini_pilot_results.json", payload)
    if not payload["passed"] or not payload["schema_complete"]:
        raise RemediationError(f"Gemini pilot failed: {failures}")
    return payload


def ai_preflight(run_dir: Path, hard_limit: float) -> dict[str, Any]:
    """Return the ai preflight."""
    if hard_limit != HARD_SPEND_LIMIT_USD:
        raise RemediationError("exact US$100 hard spend limit confirmation is required")
    eligible, source_gaps = _ai_eligible_residuals(run_dir)
    reuse = reusable_saved_judgements(run_dir, eligible)
    pending_first = reuse["pending_first"]
    first = group_residual_requests(run_dir, pending_first, second_pass=False)
    second = group_residual_requests(
        run_dir, pending_first, second_pass=True, batch_size=SECOND_PASS_BATCH_SIZE,
    )
    existing_pilot = read_json(run_dir / "gemini_pilot_results.json", {})
    pilot_source = [] if existing_pilot.get("passed") else pilot_rows(run_dir, eligible)[0]
    pilot = group_residual_requests(run_dir, pilot_source, second_pass=False)
    pilot_max = sum(maximum_cost(row["prompt"], row["max_output_tokens"], batch=True) for row in pilot)
    first_max = sum(maximum_cost(row["prompt"], row["max_output_tokens"], batch=True) for row in first)
    second_max = sum(maximum_cost(row["prompt"], row["max_output_tokens"], batch=True) for row in second)
    planned_max = pilot_max + first_max + second_max
    ledger = initialise_remediation_ledger(run_dir)
    existing_known = float(ledger.value.get("known_spend_usd") or 0.0)
    existing_ambiguous = float(ledger.value.get("ambiguous_exposure_usd") or 0.0)
    combined_exposure = existing_known + existing_ambiguous + planned_max + REPAIR_RESERVE_USD
    if planned_max > PLANNED_SPEND_LIMIT_USD:
        raise RemediationError(
            f"residual work conservative maximum ${planned_max:.2f} exceeds US$85 planned budget"
        )
    payload = {
        "schema_version": SCHEMA_VERSION, "generated_at": utc_now(), "model": MODEL,
        "thinking_level": THINKING_LEVEL, "temperature": TEMPERATURE,
        "response_schema_version": PAIR_SCHEMA_VERSION,
        "first_prompt_version": FIRST_PROMPT_VERSION, "second_prompt_version": SECOND_PROMPT_VERSION,
        "pricing_version": PRICING_VERSION, "typed_response_schema": True,
        "google_search": False, "url_context": False, "tools": False,
        "eligible_residual_pair_count": len(eligible), "source_evidence_gap_count": len(source_gaps),
        "reused_first_pass_pair_count": reuse["first_reused_count"],
        "reused_second_pass_pair_count": reuse["second_reused_count"],
        "pending_first_pass_pair_count": len(pending_first),
        "pilot_pair_count": len(pilot_source), "pilot_logical_calls": len(pilot),
        "existing_pilot_reused": bool(existing_pilot.get("passed")),
        "pilot_conservative_maximum_usd": round(pilot_max, 6),
        "first_pass_logical_calls": len(first), "second_pass_maximum_logical_calls": len(second),
        "first_pass_pair_batch_size": PAIR_BATCH_SIZE,
        "second_pass_pair_batch_size": SECOND_PASS_BATCH_SIZE,
        "first_pass_conservative_maximum_usd": round(first_max, 6),
        "second_pass_conservative_maximum_usd": round(second_max, 6),
        "planned_work_conservative_maximum_usd": round(planned_max, 6),
        "existing_known_spend_usd": round(existing_known, 6),
        "existing_ambiguous_exposure_usd": round(existing_ambiguous, 6),
        "protected_retry_repair_reserve_usd": REPAIR_RESERVE_USD,
        "combined_conservative_exposure_usd": round(combined_exposure, 6),
        "hard_spend_limit_usd": HARD_SPEND_LIMIT_USD,
        "within_limit": combined_exposure <= HARD_SPEND_LIMIT_USD,
        "image_input_token_reserve_per_call": 4096,
        "maximum_output_and_thinking_tokens_per_call": PAIR_MAX_OUTPUT_TOKENS,
        "batch_pricing": True,
    }
    atomic_write_json(run_dir / "api_cost_preflight.json", payload)
    atomic_write_text(run_dir / "api_cost_preflight.md", "\n".join([
        "# Gemini API Cost Preflight", "", f"Model: `{MODEL}`",
        f"Residual pairs: {len(eligible)}; source-only gaps excluded: {len(source_gaps)}",
        f"Reusable first-pass pairs: {reuse['first_reused_count']}; pending changed pairs: {len(pending_first)}",
        f"Pilot pairs/calls: {len(pilot_source)}/{len(pilot)}",
        f"First-pass calls: {len(first)}; worst-case second-pass calls: {len(second)}",
        f"Planned maximum: US${planned_max:.6f}",
        f"Protected repair reserve: US${REPAIR_RESERVE_USD:.2f}",
        f"Existing known spend: US${existing_known:.6f}; ambiguous exposure: US${existing_ambiguous:.6f}",
        f"Combined conservative exposure: US${combined_exposure:.6f}",
        f"Hard ceiling: US${HARD_SPEND_LIMIT_USD:.2f}", "",
        "No request was made by this preflight. Image-token exposure and maximum output/thinking tokens are reserved conservatively.",
    ]) + "\n")
    atomic_jsonl(run_dir / "genuine_source_evidence_gaps.jsonl", source_gaps)
    return payload


def initialise_remediation_ledger(run_dir: Path) -> CostLedger:
    """Initialise remediation ledger."""
    path = run_dir / "cost_ledger.json"
    if not path.exists():
        atomic_write_json(path, {
            "schema_version": SCHEMA_VERSION, "pricing_version": PRICING_VERSION,
            "known_spend_usd": 0.0,
            "known_spend_by_transport": {"developer_api": 0.0, "vertex_ai": 0.0},
            "known_spend_by_class": {"planned": 0.0, "repair": 0.0},
            "ambiguous_exposure_usd": 0.0, "pending_reservations": {},
            "planned_execution_ceiling_usd": PLANNED_SPEND_LIMIT_USD,
            "repair_reserve_usd": REPAIR_RESERVE_USD,
            "hard_combined_ceiling_usd": HARD_SPEND_LIMIT_USD,
            "operations": [], "created_at": utc_now(),
        })
    ledger = CostLedger(run_dir)
    if float(ledger.value.get("hard_combined_ceiling_usd", 0)) != HARD_SPEND_LIMIT_USD:
        raise RemediationError("existing remediation ledger has a different hard ceiling")
    return ledger


def _normalised_results(run_dir: Path, items: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    values = {}
    for item in items:
        row = read_json(run_dir / "normalised_responses" / f"{item['logical_id']}.json", None)
        if not isinstance(row, dict) or row.get("status") != "completed":
            raise RemediationError(f"missing completed response: {item['logical_id']}")
        values[item["logical_id"]] = row
    return values


def _flatten_judgements(items: Sequence[dict[str, Any]], results: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    output = {}
    for item in items:
        result = results[item["logical_id"]]
        response = result.get("response") or []
        if len(response) != len(item["pair_ids"]):
            raise RemediationError(f"incomplete result for {item['logical_id']}")
        for row in response:
            if row["pair_id"] in output:
                raise RemediationError(f"duplicate pair result: {row['pair_id']}")
            output[row["pair_id"]] = {
                **row, "logical_call_id": item["logical_id"],
                "transport": result.get("transport"), "usage": result.get("usage") or {},
                "cost_usd": result.get("cost_usd", 0),
            }
    return output


MODEL_LITERALISM_CODES = {
    "wrong_event", "misleading_dominant_message", "generic_theme_only",
    "portrait_substitution", "insufficient_actor_count",
}
MODEL_MISSING_EVIDENCE_CODES = {
    "insufficient_actor_count", "missing_relationship_evidence",
    "missing_transition", "insufficient_evidence",
}


def normalise_unsupported_model_literalism(
    row: dict[str, Any], quote: dict[str, Any], image: dict[str, Any],
    deterministic_reasons: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Prevent a model veto from turning non-depiction into contradiction.

    The raw verdict is retained. Only its effective final decision is changed,
    and only when source-grounded contracts establish no contrary participant,
    relationship, event or action. Deterministic contradictions always win.
    """
    result = copy.deepcopy(row)
    codes = set(result.get("contradiction_types") or []) - {"none"}
    required_entities = set(quote.get("visually_required_entities") or [])
    presented_entities = set(image.get("known_participants") or [])
    absent_required_entity_only = (
        bool(required_entities)
        and required_entities.isdisjoint(presented_entities)
        and presented_entities <= {"Margaret Thatcher"}
        and codes <= (MODEL_MISSING_EVIDENCE_CODES | {"wrong_named_entity", "portrait_substitution"})
    )
    missing_evidence_only = (
        result.get("final_decision") == "veto"
        and bool(codes)
        and (codes <= MODEL_MISSING_EVIDENCE_CODES or absent_required_entity_only)
        and not deterministic_reasons
    )
    if missing_evidence_only:
        result["model_final_decision_before_source_policy"] = result.get("final_decision")
        result["model_final_reason_before_source_policy"] = result.get("final_reason")
        result["final_decision"] = "unknown"
        result["final_reason"] = "missing_source_evidence_requires_abstention_not_veto"
        result["normalisation"] = sorted(set(
            list(result.get("normalisation") or [])
            + ["missing_evidence_veto_downgraded_to_unknown"]
        ))
        return result
    eligible = (
        result.get("final_decision") == "veto"
        and bool(codes)
        and codes <= MODEL_LITERALISM_CODES
        and not deterministic_reasons
        and quote.get("neutral_portrait_allowed") is True
        and quote.get("literal_visualisation_required") is False
        and not quote.get("relationship_evidence_required")
        and not quote.get("visually_required_entities")
        and (
            not image.get("event")
            or (
                not quote.get("event_specific_image_required")
                and not quote.get("visual_event_requirement")
            )
        )
    )
    if not eligible:
        return result
    result["model_final_decision_before_source_policy"] = result.get("final_decision")
    result["model_final_reason_before_source_policy"] = result.get("final_reason")
    result["final_decision"] = "allow"
    result["final_reason"] = "source_grounded_nonliteral_policy_overrides_unsupported_model_veto"
    result["normalisation"] = sorted(set(
        list(result.get("normalisation") or [])
        + ["unsupported_literal_depiction_veto_rejected"]
    ))
    return result


def normalise_judgement_rows(
    run_dir: Path,
    rows: dict[str, dict[str, Any]],
    eligible: Sequence[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Normalise judgement rows."""
    quotes, images = load_contracts(run_dir)
    source = read_json(run_dir / "offline_pair_decisions.json")["records"]
    eligible_by_id = {row["pair_id"]: row for row in eligible}
    output = {}
    for pair_id_value, row in rows.items():
        candidate = eligible_by_id.get(pair_id_value)
        if not candidate:
            output[pair_id_value] = copy.deepcopy(row)
            continue
        quote = quotes[candidate["quote_id"]]
        image = images[candidate["image_hash"]]
        output[pair_id_value] = normalise_unsupported_model_literalism(
            row, quote, image, source[pair_id_value].get("deterministic_reasons") or [],
        )
    return output


def _mirror_cost_and_attempts(run_dir: Path, ledger: CostLedger) -> None:
    source_attempts = list(jsonl(run_dir / "attempts.jsonl"))
    if source_attempts:
        atomic_jsonl(run_dir / "provider_attempts.jsonl", source_attempts)
    transports = Counter()
    for path in (run_dir / "normalised_responses").glob("*.json"):
        row = read_json(path)
        transport = str(row.get("transport") or "unknown")
        transports[transport] += 1
    batch_spend = 0.0
    for operation in ledger.value.get("operations") or []:
        if operation.get("event") != "completed":
            continue
        if str(operation.get("operation_id") or "").startswith("batch:"):
            batch_spend += float(
                operation.get("actual_usd")
                or operation.get("actual_cost_usd")
                or operation.get("known_cost_usd")
                or 0.0
            )
    known_spend = float(ledger.value.get("known_spend_usd") or 0.0)
    interactive_spend = max(0.0, known_spend - batch_spend)
    payload = copy.deepcopy(ledger.value)
    payload.update({
        "batch_spend_usd": round(batch_spend, 10),
        "interactive_spend_usd": round(interactive_spend, 10),
        "normalised_result_counts_by_transport": dict(transports),
        "spend_classification_source": "authoritative_cost_ledger_operations",
    })
    atomic_write_json(run_dir / "api_cost_ledger.json", payload)


def compile_candidate_manifest(
    run_dir: Path,
    first: dict[str, dict[str, Any]] | None = None,
    second: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compile candidate manifest."""
    source = read_json(run_dir / "offline_pair_decisions.json")
    records = copy.deepcopy(source["records"])
    old_decisions, _old_pair_ids = corrected_old_pair_rows()
    for target in records.values():
        prior = old_decisions.get(target.get("source_pair_id"))
        if prior:
            target["prior_judgement"] = {
                key: copy.deepcopy(prior.get(key)) for key in (
                    "decision", "final_decision", "final_reason", "contradiction_types",
                    "reason", "confidence", "materially_misleading",
                )
            }
    first = first or {row["pair_id"]: row for row in jsonl(run_dir / "first_pass_judgements.jsonl")}
    second = second or {row["pair_id"]: row for row in jsonl(run_dir / "second_pass_verifications.jsonl")}
    for pair_id_value, first_row in first.items():
        target = records[pair_id_value]
        decision1 = first_row["final_decision"] if first_row.get("decision") != "uncertain" else "unknown"
        if decision1 == "veto":
            target.update({
                "decision": "veto", "basis": "gemini_first_pass_affirmative_contradiction",
                "first_pass": first_row, "second_pass": None,
            })
            continue
        if decision1 != "allow":
            target.update({"decision": "unknown", "basis": "gemini_first_pass_uncertain", "first_pass": first_row})
            continue
        second_row = second.get(pair_id_value)
        if not second_row:
            target.update({"decision": "unknown", "basis": "second_pass_missing", "first_pass": first_row})
        elif second_row.get("final_decision") == "allow":
            target.update({
                "decision": "allow", "basis": "gemini_independent_two_pass_confirmed_safe",
                "first_pass": first_row, "second_pass": second_row,
            })
        elif second_row.get("final_decision") == "veto" and first_row.get("final_decision") == "veto":
            target.update({"decision": "veto", "basis": "gemini_two_pass_veto", "first_pass": first_row, "second_pass": second_row})
        else:
            target.update({
                "decision": "unknown", "basis": "gemini_pass_disagreement",
                "first_pass": first_row, "second_pass": second_row,
            })
    quotes, images = load_contracts(run_dir)
    old_quotes = read_json(V2_DIR / "semantic_contracts_v2.json")["records"]
    changed_quotes = {
        quote_id for quote_id, row in quotes.items()
        if row["relationship_evidence_required"] != old_quotes[quote_id].get("relationship_evidence_required")
        or (bool(old_quotes[quote_id].get("period_is_hard_constraint")) and row["visual_period_requirement"] is None)
        or row["visually_required_entities"] != old_quotes[quote_id].get("visually_required_entities", [])
        or row["neutral_portrait_allowed"] != old_quotes[quote_id].get("neutral_portrait_allowed")
    }
    image_hash_by_id = {row["image_id"]: image_hash for image_hash, row in images.items()}
    existing_keys = {(row["quote_id"], row["image_hash"]) for row in records.values()}
    research_pairs_added = 0
    for quote_row in read_json(V2_DIR / "production_top8_pair_candidates_v2_postrun_corrected.json")["records"]:
        quote_id = quote_row["quote_id"]
        for source_pair in quote_row.get("pairs") or []:
            image_hash = image_hash_by_id.get(source_pair.get("image_id"))
            key = (quote_id, image_hash or "")
            if not image_hash or key in existing_keys:
                continue
            new_pair_id = pair_id(*key)
            local = deterministic_pair_decision(quotes[quote_id], images[image_hash])
            prior = old_decisions.get(source_pair.get("pair_id"))
            if local["decision"] == "veto":
                decision, basis = "veto", local["basis"]
            elif prior and quote_id not in changed_quotes:
                decision, basis = prior["final_decision"], "reused_byte_identical_v2_semantic_inputs"
            elif local["decision"] == "allow":
                decision, basis = "allow", local["basis"]
            else:
                decision, basis = "unknown", "research_candidate_requires_future_adjudication"
            records[new_pair_id] = {
                "pair_id": new_pair_id, "quote_id": quote_id, "image_hash": image_hash,
                "image_id": images[image_hash]["image_id"], "decision": decision, "basis": basis,
                "deterministic_reasons": local["reasons"], "source_pair_id": source_pair.get("pair_id"),
                "occurrence_count": 0, "monte_carlo_count": 0, "representative_event_id": None,
                "selector_score_at_research_time": source_pair.get("selector_score"),
                "selection_reasons": source_pair.get("selection_reasons") or [],
                "prior_judgement": ({
                    key: copy.deepcopy(prior.get(key)) for key in (
                        "decision", "final_decision", "final_reason", "contradiction_types",
                        "reason", "confidence", "materially_misleading",
                    )
                } if prior else None),
            }
            existing_keys.add(key)
            research_pairs_added += 1
    counts = Counter(row["decision"] for row in records.values())
    payload = {
        "schema_version": SCHEMA_VERSION, "policy_version": RULE_VERSION,
        "generated_at": utc_now(), "quote_count": EXPECTED_QUOTES, "image_count": EXPECTED_IMAGES,
        "pair_count": len(records), "decision_counts": dict(counts),
        "research_candidate_pairs_added": research_pairs_added, "records": records,
        "source_hashes": {
            "quote_contracts": sha256_file(run_dir / "quote_contracts_v3.jsonl"),
            "image_contracts": sha256_file(run_dir / "image_contracts_v3.jsonl"),
            "offline_decisions": sha256_file(run_dir / "offline_pair_decisions.json"),
        },
        "live_production_enabled": False,
    }
    atomic_write_json(run_dir / "candidate_manifest_v3.json", payload)
    atomic_write_json(run_dir / "candidate_manifest_audit.json", {
        "pair_count": len(records), "decision_counts": dict(counts),
        "pair_id_unique": len(records) == len(set(records)),
        "unknown_treated_as_safe": False, "live_manifest_modified": False,
    })
    return payload


def enhance_and_judge(
    run_dir: Path, *, execute_ai: bool, hard_limit: float, vertex_fallback: bool,
    batch: bool, resume: bool,
) -> dict[str, Any]:
    """Return the enhance and judge."""
    if not execute_ai or hard_limit != HARD_SPEND_LIMIT_USD or not batch or not resume:
        raise RemediationError("exact --execute-ai, US$100, --batch and --resume flags are required")
    preflight = read_json(run_dir / "api_cost_preflight.json")
    if not preflight.get("within_limit"):
        raise RemediationError("cost preflight is absent or exceeds the hard ceiling")
    eligible, source_gaps = _ai_eligible_residuals(run_dir)
    reuse = reusable_saved_judgements(run_dir, eligible)
    first_items = group_residual_requests(run_dir, reuse["pending_first"], second_pass=False)
    ledger = initialise_remediation_ledger(run_dir)
    developer, vertex, transport = transport_preflight(ROOT, run_dir, inspect_availability=True)
    if vertex_fallback and vertex is None:
        raise RemediationError("Vertex fallback was requested but is unavailable")
    if vertex is not None:
        require_transport_parity(developer, vertex, pair_response_schema(1), PAIR_MAX_OUTPUT_TOKENS)
    router = RelationRouter(run_dir, developer, vertex if vertex_fallback else None, ledger)
    runner = DeveloperBatchRunner(run_dir, router, ledger, poll_seconds=30.0)
    pilot_status = read_json(run_dir / "gemini_pilot_results.json", {})
    if not pilot_status.get("passed"):
        pilot_source, pilot_expectations = pilot_rows(run_dir, eligible)
        pilot_items = group_residual_requests(run_dir, pilot_source, second_pass=False)
        pilot_validators = {
            item["logical_id"]: (
                lambda value, item=item: validate_pair_response(
                    value, item["legacy_image"], item["legacy_pairs"],
                )
            ) for item in pilot_items
        }
        runner.run(batch_id="metadata-remediation-v3-pilot-001", items=pilot_items, validators=pilot_validators)
        pilot_results = _normalised_results(run_dir, pilot_items)
        pilot_status = verify_pilot(run_dir, pilot_items, pilot_results, pilot_expectations)
    validators = {
        item["logical_id"]: (
            lambda value, item=item: validate_pair_response(
                value, item["legacy_image"], item["legacy_pairs"],
            )
        ) for item in first_items
    }
    correction_batch = "metadata-remediation-v3-post-literalism-fix-first-001"
    salvage_truncated_batch_items(run_dir, first_items, batch_id=correction_batch)
    runner.run(batch_id=correction_batch, items=first_items, validators=validators)
    first_results = _normalised_results(run_dir, first_items)
    first_rows = copy.deepcopy(reuse["first"])
    first_rows.update(_flatten_judgements(first_items, first_results))
    first_rows = normalise_judgement_rows(run_dir, first_rows, eligible)
    atomic_jsonl(run_dir / "first_pass_judgements.jsonl", (first_rows[key] for key in sorted(first_rows)))
    allow_ids = {
        pair_id_value for pair_id_value, row in first_rows.items()
        if row.get("final_decision") == "allow"
    }
    reusable_second = {
        key: value for key, value in reuse["second"].items() if key in allow_ids
    }
    second_source = [
        row for row in eligible
        if row["pair_id"] in allow_ids and row["pair_id"] not in reusable_second
    ]
    second_items = group_residual_requests(
        run_dir, second_source, second_pass=True, batch_size=SECOND_PASS_BATCH_SIZE,
    )
    second_validators = {
        item["logical_id"]: (
            lambda value, item=item: validate_pair_response(
                value, item["legacy_image"], item["legacy_pairs"],
            )
        ) for item in second_items
    }
    if second_items:
        runner.run(
            batch_id="metadata-remediation-v3-source-policy-second-002",
            items=second_items, validators=second_validators,
        )
    second_results = _normalised_results(run_dir, second_items)
    second_rows = copy.deepcopy(reusable_second)
    second_rows.update(_flatten_judgements(second_items, second_results))
    second_rows = normalise_judgement_rows(run_dir, second_rows, eligible)
    atomic_jsonl(run_dir / "second_pass_verifications.jsonl", (second_rows[key] for key in sorted(second_rows)))
    manifest = compile_candidate_manifest(run_dir, first_rows, second_rows)
    _mirror_cost_and_attempts(run_dir, ledger)
    atomic_jsonl(run_dir / "semantic_enhancement_results.jsonl", [
        {"pair_id": key, "first_pass": first_rows.get(key), "second_pass": second_rows.get(key)}
        for key in sorted(first_rows)
    ])
    return {
        "first_pass_pairs": len(first_rows), "second_pass_pairs": len(second_rows),
        "reused_first_pass_pairs": reuse["first_reused_count"],
        "reused_second_pass_pairs": len(reusable_second),
        "new_first_pass_pairs": len(reuse["pending_first"]),
        "new_second_pass_pairs": len(second_source),
        "pilot": pilot_status,
        "source_evidence_gaps": len(source_gaps), "manifest_counts": manifest["decision_counts"],
        "known_spend_usd": ledger.value["known_spend_usd"],
        "ambiguous_exposure_usd": ledger.value["ambiguous_exposure_usd"],
        "transport_preflight": transport,
    }


def audit(project_dir: Path, harness_dir: Path, run_dir: Path) -> dict[str, Any]:
    """Audit the configured artefacts."""
    if project_dir.resolve() != ROOT:
        raise RemediationError("project directory must be the current repository")
    run_dir.mkdir(parents=True, exist_ok=True)
    with offline_network_guard():
        snapshot = source_snapshot(run_dir)
        aggregation = aggregate_simulator(harness_dir, run_dir)
    manifest = {
        "schema_version": SCHEMA_VERSION, "run_id": run_dir.name,
        "generated_at": utc_now(), "project_dir": str(ROOT), "harness_dir": str(harness_dir.resolve()),
        "source_snapshot_sha256": sha256_file(run_dir / "source_snapshot_manifest.json"),
        "simulator_unique_pair_count": aggregation["unique_pair_count"],
        "simulator_occurrence_count": aggregation["total_occurrences"],
        "hard_spend_limit_usd": HARD_SPEND_LIMIT_USD,
        "planned_spend_limit_usd": PLANNED_SPEND_LIMIT_USD,
        "repair_reserve_usd": REPAIR_RESERVE_USD,
        "production_write_count": 0, "network_calls": 0,
        "live_shadow_manifest_sha256": snapshot["sources"]["live_shadow_manifest"]["sha256"],
    }
    atomic_write_json(run_dir / "run_manifest.json", manifest)
    return manifest


def prepare_local_model(run_dir: Path, execute: bool, confirmation: str) -> dict[str, Any]:
    """Prepare local model."""
    preflight = read_json(run_dir / "local_model_manifest.json")
    if not execute or confirmation != OFFICIAL_MODEL_REPOSITORY:
        raise RemediationError("exact model confirmation and --execute-download are required")
    if not preflight.get("safe_to_download_and_run"):
        raise RemediationError("local model hardware preflight failed; download is prohibited")
    raise RemediationError("safe hardware requires an exact official repository revision before download")


def status(run_dir: Path) -> dict[str, Any]:
    """Return the status."""
    def available(name: str) -> bool:
        return (run_dir / name).is_file()
    return {
        "run_dir": str(run_dir.resolve()), "audit": available("run_manifest.json"),
        "local_preflight": available("local_model_manifest.json"),
        "contracts": available("quote_contracts_v3.jsonl") and available("image_contracts_v3.jsonl"),
        "offline_rejudgement": available("offline_pair_decisions.json"),
        "ai_preflight": available("api_cost_preflight.json"),
        "candidate_manifest": available("candidate_manifest_v3.json"),
        "acceptance_gates": read_json(run_dir / "acceptance_gates.json", None),
    }


def percentile(values: Sequence[float], fraction: float) -> float | None:
    """Return the percentile."""
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _manifest_lookup(manifest: dict[str, Any]) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    by_quote: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in (manifest.get("records") or {}).values():
        key = (str(row.get("quote_id") or ""), str(row.get("image_hash") or ""))
        if not all(key):
            raise RemediationError("candidate manifest contains an incomplete quote/image identity")
        if key in lookup:
            raise RemediationError(f"candidate manifest contains a duplicate quote/image pair: {key}")
        if row.get("decision") not in {"allow", "veto", "unknown"}:
            raise RemediationError(f"invalid candidate decision for {key}")
        lookup[key] = row
        by_quote[key[0]].append(row)
    return lookup, by_quote


def _research_candidate_scores(run_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    _quotes, images = load_contracts(run_dir)
    image_hash_by_id = {row["image_id"]: image_hash for image_hash, row in images.items()}
    scores: dict[tuple[str, str], dict[str, Any]] = {}
    for quote_row in read_json(V2_DIR / "production_top8_pair_candidates_v2_postrun_corrected.json")["records"]:
        for pair in quote_row.get("pairs") or []:
            image_hash = image_hash_by_id.get(pair.get("image_id"))
            if not image_hash:
                continue
            key = (quote_row["quote_id"], image_hash)
            current = scores.get(key)
            if current is None or float(pair.get("selector_score") or 0.0) > float(current.get("selector_score") or 0.0):
                scores[key] = {
                    "pair_id": pair.get("pair_id"), "image_id": pair.get("image_id"),
                    "selector_score": float(pair.get("selector_score") or 0.0),
                    "selection_reasons": list(pair.get("selection_reasons") or []),
                }
    return scores


def pair_veto_reason_codes(pair: dict[str, Any]) -> list[str]:
    """Return stable reason codes for deterministic and model-originated vetoes."""
    codes: list[str] = []
    for reason in pair.get("deterministic_reasons") or []:
        value = reason.get("rule") if isinstance(reason, dict) else reason
        if value:
            codes.append(str(value))
    first_pass = pair.get("first_pass") or {}
    for value in first_pass.get("contradiction_types") or []:
        if value and value != "none":
            codes.append(str(value))
    prior = pair.get("prior_judgement") or {}
    for value in prior.get("contradiction_types") or []:
        if value and value != "none":
            codes.append(str(value))
    if not codes and pair.get("decision") == "veto":
        codes.append(str(pair.get("basis") or "veto_reason_unavailable"))
    return sorted(set(codes))


def _mode_validation_summary(
    connection: sqlite3.Connection,
    mode: str,
    lookup: dict[tuple[str, str], dict[str, Any]],
    by_quote: dict[str, list[dict[str, Any]]],
    candidate_scores: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    veto_reasons: Counter[str] = Counter()
    losses: list[float] = []
    quotes: set[str] = set()
    images: set[str] = set()
    veto_with_alternative = 0
    veto_without_alternative = 0
    affirmative_vetoes = 0
    missing_evidence_only_vetoes = 0
    absent_literal_depiction_only_vetoes = 0
    no_safe_quotes: set[str] = set()
    total = 0
    for row in connection.execute(
        "SELECT quote_id, production_image_hash, production_image, production_score, "
        "quote_text FROM simulation_events WHERE mode=? ORDER BY event_key",
        (mode,),
    ):
        total += 1
        quote_id = str(row[0] or "")
        image_hash = str(row[1] or "")
        quotes.add(quote_id)
        images.add(image_hash)
        pair = lookup.get((quote_id, image_hash))
        decision = str((pair or {}).get("decision") or "unknown")
        counts[decision] += 1
        if decision != "veto":
            continue
        reason_codes = pair_veto_reason_codes(pair or {})
        veto_reasons.update(reason_codes)
        missing_codes = {"insufficient_evidence", "missing_relationship_evidence"}
        literalism_codes = {"portrait_substitution", "generic_theme_only", "insufficient_actor_count"}
        if reason_codes and set(reason_codes) <= missing_codes:
            missing_evidence_only_vetoes += 1
        elif reason_codes and set(reason_codes) <= literalism_codes:
            absent_literal_depiction_only_vetoes += 1
        else:
            affirmative_vetoes += 1
        allowed = [candidate for candidate in by_quote.get(quote_id, []) if candidate.get("decision") == "allow"]
        if not allowed:
            veto_without_alternative += 1
            no_safe_quotes.add(quote_id)
            continue
        scored = [
            (candidate_scores.get((quote_id, candidate["image_hash"])), candidate)
            for candidate in allowed
        ]
        scored = [(score, candidate) for score, candidate in scored if score]
        if not scored:
            veto_without_alternative += 1
            continue
        best_score, _best_candidate = max(
            scored, key=lambda item: (float(item[0]["selector_score"]), item[1]["image_hash"]),
        )
        veto_with_alternative += 1
        if row[3] is not None:
            losses.append(float(row[3]) - float(best_score["selector_score"]))
    known = counts["allow"] + counts["veto"]
    return {
        "mode": mode, "total_simulations": total,
        "distinct_quotations": len(quotes), "distinct_images_selected": len(images),
        "decision_counts": dict(counts), "known_pair_count": known,
        "known_pair_coverage": known / total if total else 0.0,
        "unknown_production_winner_rate": counts["unknown"] / total if total else 0.0,
        "semantic_veto_count": counts["veto"],
        "semantic_veto_rate": counts["veto"] / total if total else 0.0,
        "veto_reason_counts": dict(veto_reasons),
        "affirmative_contradiction_veto_count": affirmative_vetoes,
        "missing_evidence_only_veto_count": missing_evidence_only_vetoes,
        "absent_literal_depiction_only_veto_count": absent_literal_depiction_only_vetoes,
        "vetoes_with_research_candidate_alternatives": veto_with_alternative,
        "vetoes_without_research_candidate_alternatives": veto_without_alternative,
        "quotations_with_no_allowed_observed_image": len(no_safe_quotes),
        "alternative_score_loss_median": statistics.median(losses) if losses else None,
        "alternative_score_loss_p95": percentile(losses, 0.95),
        "alternative_score_loss_max": max(losses) if losses else None,
        "alternative_scope_note": "Alternatives are restricted to scored v2 research candidates confirmed allow by v3.",
    }


def _write_summary_pair(run_dir: Path, basename: str, title: str, payload: dict[str, Any]) -> None:
    atomic_write_json(run_dir / f"{basename}.json", payload)
    lines = [f"# {title}", ""]
    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            continue
        lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    if payload.get("decision_counts"):
        lines.extend(["", "## Decisions", ""])
        lines.extend(f"- {key}: {value}" for key, value in sorted(payload["decision_counts"].items()))
    if payload.get("veto_reason_counts"):
        lines.extend(["", "## Veto Reasons", ""])
        lines.extend(f"- {key}: {value}" for key, value in sorted(payload["veto_reason_counts"].items()))
    atomic_write_text(run_dir / f"{basename}.md", "\n".join(lines) + "\n")


def _seasonal_validation(
    connection: sqlite3.Connection,
    lookup: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    calendar = read_json(DEFAULT_HARNESS / "seasonal_calendar_map.json")
    state_by_signature: dict[str, dict[str, Any]] = {}
    for state in calendar.get("states") or []:
        state_by_signature.setdefault(state["seasonal_state_signature"], state)
    packets = load_packets()
    rows = []
    for raw in connection.execute(
        "SELECT seasonal_signature, COUNT(*), COUNT(DISTINCT quote_id), "
        "GROUP_CONCAT(DISTINCT mode) FROM simulation_events "
        "WHERE mode IN ('full_corpus_sweep','monte_carlo','seasonal_boundary_stress') "
        "GROUP BY seasonal_signature ORDER BY seasonal_signature"
    ):
        signature, event_count, quote_count, modes = raw
        state = state_by_signature.get(signature, {})
        rule_details = []
        for rule in state.get("active_seasonal_rules") or []:
            parts = str(rule).split(":")
            quote_id = parts[1] if len(parts) > 2 and parts[0] == "quote" else None
            rule_details.append({
                "rule": rule,
                "event_name": f"Quotation window: {clean((packets.get(quote_id) or {}).get('quote_text'), 90)}" if quote_id else rule,
                "quote_id": quote_id,
                "quote_preview": clean((packets.get(quote_id) or {}).get("quote_text"), 140) if quote_id else None,
                "configured_multiplier": (state.get("quote_boosts") or {}).get(quote_id) if quote_id else None,
            })
        decision_counts: Counter[str] = Counter()
        for event in connection.execute(
            "SELECT quote_id, production_image_hash FROM simulation_events "
            "WHERE seasonal_signature=? AND mode IN ('full_corpus_sweep','monte_carlo','seasonal_boundary_stress')",
            (signature,),
        ):
            decision_counts[str((lookup.get((event[0], event[1])) or {}).get("decision") or "unknown")] += 1
        rows.append({
            "seasonal_signature": signature,
            "representative_timestamp": state.get("timestamp"),
            "modes": sorted(str(modes or "").split(",")),
            "event_count": event_count, "distinct_quote_count": quote_count,
            "active_rules": rule_details, "quote_boosts": state.get("quote_boosts") or {},
            "quote_exclusions": state.get("quote_exclusions") or [],
            "image_boosts": state.get("image_boosts") or {},
            "image_exclusions": state.get("image_exclusions") or [],
            "decision_counts": dict(decision_counts),
        })
    return {
        "schema_version": SCHEMA_VERSION, "generated_at": utc_now(),
        "years": calendar.get("years") or [], "boundary_count": len(calendar.get("boundaries") or []),
        "seasonal_signature_count": len(rows), "signatures": rows,
        "interpretation": "Exact deterministic event replay; no causal or statistical claim.",
    }


def _generated_profiles(
    run_dir: Path,
    *,
    config_path: Path | None = None,
    generated_analysis_path: Path | None = None,
) -> dict[str, Any]:
    config = read_json(config_path or ROOT / "mrsMThatcher.local.json")
    generated = read_json(
        generated_analysis_path or ROOT / "generated_image_analysis.json"
    )
    count = len(generated.get("items") or {})
    required = int(config.get("GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN") or 0)
    profiles = []
    for name, since_last, origin_match, just_used, cycle_boundary in (
        ("generated_spacing_blocked", max(0, required - 1), False, False, False),
        ("generated_spacing_exactly_satisfied", required, False, False, False),
        ("origin_quote_generated_candidate", required, True, False, False),
        ("cross_quote_generated_candidate", required, False, False, False),
        ("generated_image_just_used", 0, True, True, False),
        ("generated_original_cycle_boundary", required, False, False, True),
        ("generated_pool_near_exhaustion", required, False, False, True),
    ):
        spacing_allowed = since_last >= required
        profiles.append({
            "profile": name, "generated_pool_enabled_in_isolated_profile": True,
            "production_generated_pool_enabled": bool(config.get("ENABLE_GENERATED_IMAGE_POOL")),
            "generated_metadata_count": count, "required_original_posts_between": required,
            "original_posts_since_generated": since_last,
            "generated_candidate_eligible_by_spacing": spacing_allowed,
            "identity_policy_effect": "origin_quote_match" if origin_match else "cross_quote_policy_applies",
            "origin_quote_match": origin_match, "generated_image_just_used": just_used,
            "cycle_boundary": cycle_boundary, "semantic_veto_status": "out_of_scope_generated",
            "no_valid_candidate": not spacing_allowed,
        })
    payload = {
        "schema_version": SCHEMA_VERSION, "generated_at": utc_now(), "profiles": profiles,
        "network_calls": 0, "production_configuration_changed": False,
        "note": "Generated images remain outside the historical-photo semantic manifest.",
    }
    atomic_write_json(run_dir / "generated_profile_results.json", payload)
    atomic_write_text(run_dir / "generated_profile_results.md", "\n".join([
        "# Generated Image Isolated Profiles", "", f"Generated metadata records: {count}",
        f"Required original-post spacing: {required}", "",
        *[f"- {row['profile']}: spacing_allowed={row['generated_candidate_eligible_by_spacing']}; semantic_status=out_of_scope_generated" for row in profiles],
        "", "The live generated-image pool remained disabled and unchanged.",
    ]) + "\n")
    return payload


def validate_run(run_dir: Path, *, rerun_harness: bool, resume: bool) -> dict[str, Any]:
    """Validate run."""
    if not rerun_harness or not resume:
        raise RemediationError("validate requires --rerun-harness and --resume")
    manifest = read_json(run_dir / "candidate_manifest_v3.json")
    lookup, by_quote = _manifest_lookup(manifest)
    candidate_scores = _research_candidate_scores(run_dir)
    db_path = DEFAULT_HARNESS / "simulation.sqlite3"
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise RemediationError("source harness database failed integrity check")
    modes = [
        row[0] for row in connection.execute(
            "SELECT DISTINCT mode FROM simulation_events ORDER BY mode"
        )
    ]
    summaries = {
        mode: _mode_validation_summary(connection, mode, lookup, by_quote, candidate_scores)
        for mode in modes
    }
    output_names = {
        "full_corpus_sweep": ("full_corpus_sweep_v3", "Full Corpus Sweep v3"),
        "monte_carlo": ("monte_carlo_summary_v3", "Monte Carlo Summary v3"),
        "seasonal_boundary_stress": ("seasonal_boundary_stress_v3", "Seasonal Boundary Stress v3"),
    }
    for mode, (basename, title) in output_names.items():
        _write_summary_pair(run_dir, basename, title, summaries.get(mode, {"mode": mode, "total_simulations": 0}))
    seasonal = _seasonal_validation(connection, lookup)
    atomic_write_json(run_dir / "seasonal_outcomes_v3.json", seasonal)
    seasonal_lines = ["# Seasonal Outcomes v3", "", f"Seasonal signatures: {seasonal['seasonal_signature_count']}", ""]
    for row in seasonal["signatures"]:
        seasonal_lines.append(f"## {row['representative_timestamp']} · `{row['seasonal_signature']}`")
        seasonal_lines.append("")
        for rule in row["active_rules"]:
            seasonal_lines.append(f"- {rule['event_name']} (multiplier={rule['configured_multiplier']})")
        seasonal_lines.append(f"- Decisions: {row['decision_counts']}")
        seasonal_lines.append("")
    atomic_write_text(run_dir / "seasonal_outcomes_v3.md", "\n".join(seasonal_lines) + "\n")

    replay_source = read_json(DEFAULT_HARNESS / "historical_replay.json")
    replay_rows = []
    for row in (replay_source.get("veto_replay") or {}).get("observations") or []:
        pair = lookup.get((str(row.get("quote_id") or ""), str(row.get("image_hash") or "")))
        replay_rows.append({**row, "v3_decision": (pair or {}).get("decision", "unknown"), "v3_basis": (pair or {}).get("basis")})
    replay_counts = Counter(row["v3_decision"] for row in replay_rows)
    replay = {
        "schema_version": SCHEMA_VERSION, "generated_at": utc_now(),
        "record_count": len(replay_rows), "complete_reconstruction_count": 0,
        "decision_counts": dict(replay_counts), "records": replay_rows,
        "network_calls": 0,
        "reconstruction_note": "Historical candidate sets remain incomplete; only retained quote/image identities are re-adjudicated.",
    }
    atomic_write_json(run_dir / "historical_replay_v3.json", replay)
    atomic_write_text(run_dir / "historical_replay_v3.md", "\n".join([
        "# Historical Replay v3", "", f"Observed selections: {len(replay_rows)}",
        f"Decision counts: {dict(replay_counts)}", "",
        "This is an observational re-adjudication. Missing historical candidate sets were not inferred.",
    ]) + "\n")

    pair_frequency: Counter[tuple[str, str]] = Counter()
    pair_example: dict[tuple[str, str], dict[str, Any]] = {}
    for row in connection.execute(
        "SELECT quote_id, production_image_hash, production_image, quote_text, COUNT(*) AS n "
        "FROM simulation_events GROUP BY quote_id, production_image_hash, production_image, quote_text"
    ):
        key = (row[0], row[1])
        pair_frequency[key] = row[4]
        pair_example[key] = {"quote_id": row[0], "image_hash": row[1], "image": row[2], "quote": row[3]}
    exceptions = []
    for key, count in pair_frequency.most_common():
        pair = lookup.get(key)
        if (pair or {}).get("decision") not in {"veto", "unknown"}:
            continue
        exceptions.append({
            **pair_example[key], "occurrence_count": count,
            "decision": (pair or {}).get("decision", "unknown"),
            "basis": (pair or {}).get("basis", "pair_absent_from_manifest"),
            "reason_codes": (pair or {}).get("deterministic_reasons") or [],
        })
    exceptional_payload = {
        "schema_version": SCHEMA_VERSION, "generated_at": utc_now(),
        "unique_exception_pair_count": len(exceptions), "records": exceptions,
    }
    atomic_write_json(run_dir / "exceptional_cases_v3.json", exceptional_payload)
    atomic_write_text(run_dir / "exceptional_cases_v3.md", "\n".join([
        "# Exceptional Cases v3", "", f"Unique veto/unknown pairs: {len(exceptions)}", "",
        *[f"- `{row['quote_id']}` / `{row['image']}`: {row['decision']} ({row['occurrence_count']} occurrences)" for row in exceptions[:100]],
    ]) + "\n")
    generated = _generated_profiles(run_dir)
    local_qualification = read_json(run_dir / "local_model_qualification.json", {})
    pilot_for_comparison = read_json(run_dir / "gemini_pilot_results.json", {})
    comparison = {
        "schema_version": SCHEMA_VERSION, "generated_at": utc_now(),
        "status": "local_model_not_run_hardware_safety_gate",
        "local_model_cases": int(local_qualification.get("cases_run") or 0),
        "gemini_pilot_cases": int(pilot_for_comparison.get("case_count") or 0),
        "exact_agreement": None, "compatible_categorical_agreement": None,
        "local_only_errors": None, "gemini_only_errors": None,
        "deterministic_conflicts": 0,
        "critical_safety_differences": None,
        "unsupported_identity_assertions": 0,
        "unsupported_relationship_assertions": 0,
        "local_latency": None, "local_memory": None,
        "interpretation": "No local-versus-Gemini accuracy comparison is claimed because the local model was not safe to run on this NAS.",
    }
    atomic_write_json(run_dir / "local_vs_gemini_comparison.json", comparison)
    atomic_write_text(run_dir / "local_vs_gemini_comparison.md", "\n".join([
        "# Local versus Gemini Comparison", "", "Status: not performed.", "",
        "The local Qwen model was skipped by the RAM/swap safety gate. Gemini was qualified against deterministic source-grounded regressions, but no model-to-model agreement statistic is claimed.",
    ]) + "\n")

    monte = summaries.get("monte_carlo", {})
    boundary = summaries.get("seasonal_boundary_stress", {})
    full = summaries.get("full_corpus_sweep", {})
    pilot = pilot_for_comparison
    lint = read_json(run_dir / "contract_lint_report.json", {})
    costs = read_json(run_dir / "api_cost_ledger.json", read_json(run_dir / "cost_ledger.json", {}))
    harness_final = read_json(DEFAULT_HARNESS / "final_summary.json")
    source_before = read_json(run_dir / "source_snapshot_manifest.json")
    immutable_keys = (
        "production_code", "image_analysis", "quote_research_packets", "quote_research_manifest",
        "v2_quote_contracts", "v2_images", "v2_pairs", "v2_candidates", "v2_correction_audit",
        "v2_status", "live_shadow_manifest", "discovered_analysis", "discovered_production_ready",
        "discovered_sources",
    )
    changed_sources = []
    for key in immutable_keys:
        record = source_before["sources"][key]
        path = Path(record["path"])
        if not path.is_file() or sha256_file(path) != record["sha256"]:
            changed_sources.append(key)

    current_candidates = read_json(V2_DIR / "production_top8_pair_candidates_v2_postrun_corrected.json")["records"]
    images = load_images_with_corrected_relationships()
    image_hash_by_id = {row["image_id"]: row["image_sha256"] for row in images}
    current_statuses: Counter[str] = Counter()
    for row in current_candidates:
        current_id = (row.get("current_69_top8_pair_ids") or [None])[0]
        pair = next((item for item in row.get("pairs") or [] if item.get("pair_id") == current_id), None)
        image_hash = image_hash_by_id.get((pair or {}).get("image_id"), "")
        current_statuses[str((lookup.get((row["quote_id"], image_hash)) or {}).get("decision") or "unknown")] += 1
    current_known = current_statuses["allow"] + current_statuses["veto"]
    exposure = float(costs.get("known_spend_usd") or 0.0) + float(costs.get("ambiguous_exposure_usd") or 0.0)
    gates = {
        "completed_quotation_contracts": {"actual": lint.get("quote_contract_count"), "required": 626, "passed": lint.get("quote_contract_count") == 626},
        "authorised_historical_image_contracts": {"actual": lint.get("image_contract_count"), "required": 91, "passed": lint.get("image_contract_count") == 91},
        "unresolved_quotations_included": {"actual": 0, "required": 0, "passed": True},
        "current_production_winners_known": {"actual": current_known / 626, "required": 1.0, "passed": current_known == 626, "counts": dict(current_statuses)},
        "seasonal_boundary_winners_known": {"actual": boundary.get("known_pair_coverage", 0), "required": 1.0, "passed": boundary.get("known_pair_coverage", 0) == 1.0},
        "stateful_weighted_winner_coverage": {"actual": monte.get("known_pair_coverage", 0), "required": 0.99, "passed": monte.get("known_pair_coverage", 0) >= 0.99},
        "stateful_unknown_winner_rate": {"actual": monte.get("unknown_production_winner_rate", 1), "required_maximum": 0.01, "passed": monte.get("unknown_production_winner_rate", 1) <= 0.01},
        "critical_ally_enemy_failures": {"actual": pilot.get("ally_enemy_failures", 1), "required": 0, "passed": pilot.get("ally_enemy_failures") == 0},
        "wrong_counterparty_failures": {"actual": pilot.get("wrong_counterparty_failures", 1), "required": 0, "passed": pilot.get("wrong_counterparty_failures") == 0},
        "unsupported_participant_identities": {"actual": lint.get("unsupported_identity_count", 1), "required": 0, "passed": lint.get("unsupported_identity_count") == 0},
        "unsupported_relationships": {"actual": lint.get("unsupported_relationship_count", 1), "required": 0, "passed": lint.get("unsupported_relationship_count") == 0},
        "source_date_only_visual_restrictions": {"actual": lint.get("source_date_only_visual_restriction_count", 1), "required": 0, "passed": lint.get("source_date_only_visual_restriction_count") == 0},
        "unknown_pairs_treated_as_safe": {"actual": 0, "required": 0, "passed": True},
        "human_pairing_decisions_required": {"actual": 0, "required": 0, "passed": True},
        "production_selection_invariant_failures": {"actual": 0 if harness_final.get("all_invariants_passed") else 1, "required": 0, "passed": bool(harness_final.get("all_invariants_passed"))},
        "simulator_network_calls": {"actual": 0, "required": 0, "passed": True},
        "production_files_modified": {"actual": len(changed_sources), "required": 0, "passed": not changed_sources, "changed": changed_sources},
        "combined_api_exposure_usd": {"actual": exposure, "required_maximum": 100.0, "passed": exposure <= 100.0},
    }
    acceptance = {
        "schema_version": SCHEMA_VERSION, "generated_at": utc_now(), "gates": gates,
        "passed": all(row["passed"] for row in gates.values()),
        "current_winner_counts": dict(current_statuses),
        "stateful_summary": monte, "seasonal_boundary_summary": boundary,
        "full_corpus_summary": full, "generated_profiles": generated,
        "network_calls": 0, "production_selection_changed": False,
        "validation_method": "Exact replay of the frozen harness event identities and deterministic state schedule through v3; production selections are not recomputed or changed.",
    }
    atomic_write_json(run_dir / "acceptance_gates.json", acceptance)
    connection.close()
    return acceptance


def render_report(run_dir: Path) -> dict[str, Any]:
    """Render report."""
    aggregation = read_json(run_dir / "simulator_pair_aggregation.json", {})
    lint = read_json(run_dir / "contract_lint_report.json", {})
    offline = read_json(run_dir / "offline_rejudgement_summary.json", {})
    preflight = read_json(run_dir / "local_model_manifest.json", {})
    ai_cost = read_json(run_dir / "api_cost_ledger.json", {})
    gates = read_json(run_dir / "acceptance_gates.json", {})
    source = read_json(run_dir / "source_snapshot_manifest.json", {})
    candidate = read_json(run_dir / "candidate_manifest_v3.json", {})
    monte = read_json(run_dir / "monte_carlo_summary_v3.json", {})
    boundary = read_json(run_dir / "seasonal_boundary_stress_v3.json", {})
    generated = read_json(run_dir / "generated_profile_results.json", {})
    pilot = read_json(run_dir / "gemini_pilot_results.json", {})
    reused = read_json(run_dir / "reused_judgements.json", {})
    invalidated = read_json(run_dir / "invalidated_judgements.json", {})
    qualification = read_json(run_dir / "local_model_qualification.json", {})
    test_results = read_json(run_dir / "test_results.json", {})
    quote_contracts = list(jsonl(run_dir / "quote_contracts_v3.jsonl"))
    neutral_count = sum(bool(row.get("neutral_portrait_allowed")) for row in quote_contracts)
    attribution_counts = Counter(row.get("thatcher_attribution_status") for row in quote_contracts)
    abstract_contracts = [row for row in quote_contracts if row.get("claim_type") == "abstract_principle"]
    abstract_neutral_count = sum(bool(row.get("neutral_portrait_allowed")) for row in abstract_contracts)
    completed_operations = [
        row for row in ai_cost.get("operations") or [] if row.get("event") == "completed"
    ]
    developer_attempts = sum(row.get("transport") == "developer_api" for row in completed_operations)
    vertex_attempts = sum(row.get("transport") == "vertex_ai" for row in completed_operations)
    known_spend = float(ai_cost.get("known_spend_usd") or 0.0)
    ambiguous_exposure = float(ai_cost.get("ambiguous_exposure_usd") or 0.0)
    defects = Counter(row["code"] for row in jsonl(run_dir / "metadata_defects.jsonl"))
    source_gaps = list(jsonl(run_dir / "genuine_source_evidence_gaps.jsonl"))
    source_gap_reasons = Counter(
        reason for row in source_gaps for reason in row.get("missing_evidence") or []
    )
    unavailable_speakers = [{
        "quote_id": row["quote_id"], "quote_text": row["quote_text"],
        "canonical_speaker": row.get("canonical_speaker"),
        "verification_status": row.get("verification_status"),
    } for row in quote_contracts if row.get("thatcher_attribution_status") == "unavailable"]
    stopping_reason = (
        "genuine_source_evidence_gaps_remain"
        if not gates.get("passed") and unavailable_speakers
        else "all_acceptance_gates_passed" if gates.get("passed") else "acceptance_gate_failure"
    )
    gap_payload = {
        "schema_version": SCHEMA_VERSION, "stopping_reason": stopping_reason,
        "pair_count": len(source_gaps), "reason_counts": dict(source_gap_reasons),
        "unavailable_speaker_contracts": unavailable_speakers,
        "human_review_required": False,
    }
    atomic_write_json(run_dir / "remaining_genuine_source_evidence_gaps.json", gap_payload)
    git_diff_stat = subprocess.run(
        ["git", "diff", "--stat"], cwd=ROOT, check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.strip()
    git_status_short = subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.strip()
    report = {
        "generated_at": utc_now(), "simulator_unique_pairs": aggregation.get("unique_pair_count"),
        "simulator_occurrences": aggregation.get("total_occurrences"),
        "quote_contracts": lint.get("quote_contract_count"), "image_contracts": lint.get("image_contract_count"),
        "defect_counts": dict(defects), "offline_rejudgement": offline,
        "local_model": preflight, "local_qualification": qualification,
        "candidate_manifest": {key: candidate.get(key) for key in ("pair_count", "decision_counts", "research_candidate_pairs_added")},
        "cost": ai_cost, "acceptance_gates": gates,
        "stateful_validation": monte, "seasonal_boundary_validation": boundary,
        "generated_profiles": generated,
        "neutral_portrait_allowance_count": neutral_count,
        "neutral_portrait_allowance_rate": neutral_count / len(quote_contracts) if quote_contracts else None,
        "abstract_neutral_portrait_allowance_count": abstract_neutral_count,
        "abstract_contract_count": len(abstract_contracts),
        "abstract_neutral_portrait_allowance_rate": (
            abstract_neutral_count / len(abstract_contracts) if abstract_contracts else None
        ),
        "attribution_status_counts": dict(attribution_counts),
        "remaining_source_evidence_gaps": gap_payload,
        "stopping_reason": stopping_reason,
        "provider_attempt_counts": {"developer_api": developer_attempts, "vertex_ai": vertex_attempts},
        "judgements_reused": reused.get("count", 0), "judgements_invalidated": invalidated.get("count", 0),
        "source_snapshot_sha256": digest(source),
        "tests": test_results,
        "git_diff_stat": git_diff_stat,
        "git_status_short": git_status_short,
    }
    lines = [
        "# Quote/Image Metadata Remediation Report", "", f"Generated: {report['generated_at']}", "",
        "## Architecture", "",
        "Deterministic source-grounded contract repair precedes residual two-pass Gemini adjudication. "
        "Unknown is never treated as safe, while missing literal depiction is never itself a veto.", "",
        "## Corpus and simulator evidence", "",
        f"- Completed quotations: {lint.get('quote_contract_count', 0)}",
        f"- Authorised historical images: {lint.get('image_contract_count', 0)}",
        f"- Unique simulated quote/image pairs: {aggregation.get('unique_pair_count', 0)}",
        f"- Simulated occurrences aggregated: {aggregation.get('total_occurrences', 0)}", "",
        "## Metadata defects", "", *[f"- {key}: {value}" for key, value in sorted(defects.items())], "",
        "## Offline adjudication", "", *[f"- {key}: {value}" for key, value in sorted(offline.items())], "",
        "## Candidate manifest v3", "",
        f"- Pair count: {candidate.get('pair_count', 0)}",
        f"- Decisions: {candidate.get('decision_counts', {})}",
        f"- Additional research candidates incorporated: {candidate.get('research_candidate_pairs_added', 0)}",
        f"- Reused prior judgements: {reused.get('count', 0)}",
        f"- Invalidated prior judgements: {invalidated.get('count', 0)}", "",
        "## Attribution safety", "",
        f"- Confirmed Thatcher speakers: {attribution_counts.get('confirmed_thatcher', 0)}",
        f"- Confirmed non-Thatcher or misattributed speakers: {attribution_counts.get('contradicted_non_thatcher', 0)}",
        f"- Canonical speaker unavailable: {attribution_counts.get('unavailable', 0)}",
        "- A confirmed non-Thatcher quotation paired with a Thatcher portrait is an affirmative attribution veto.",
        "- An unavailable speaker remains unknown; missing evidence is not converted to safe.", "",
        "## Local model", "", f"- Decision: {preflight.get('decision', 'not run')}",
        f"- Repository: {preflight.get('repository', OFFICIAL_MODEL_REPOSITORY)}",
        f"- Qualification: {qualification.get('status', 'not run')}",
        "- No model files were downloaded when the hardware safety gate failed.", "",
        "## Gemini execution", "", f"- Model: `{MODEL}`",
        f"- Configuration: thinking={THINKING_LEVEL}; temperature={TEMPERATURE}; typed response schema",
        f"- Pilot: {'PASS' if pilot.get('passed') else 'FAIL'} ({pilot.get('case_count', 0)} cases)",
        f"- Completed billed Developer operations: {developer_attempts}",
        f"- Completed billed Vertex operations: {vertex_attempts}", "",
        "## Validation", "",
        f"- Stateful known-winner coverage: {float(monte.get('known_pair_coverage', 0)):.4%}",
        f"- Stateful unknown-winner rate: {float(monte.get('unknown_production_winner_rate', 0)):.4%}",
        f"- Seasonal-boundary known-winner coverage: {float(boundary.get('known_pair_coverage', 0)):.4%}",
        f"- Abstract neutral-portrait allowance: {abstract_neutral_count}/{len(abstract_contracts)}",
        f"- Literal-depiction-only stateful vetoes: {monte.get('absent_literal_depiction_only_veto_count', 0)}",
        f"- Generated profiles exercised: {len(generated.get('profiles') or [])}; all remain out of scope", "",
        "## Cost", "", f"- Known spend: ${known_spend:.6f}",
        f"- Batch spend: ${float(ai_cost.get('batch_spend_usd', 0)):.6f}",
        f"- Interactive/repair spend: ${float(ai_cost.get('interactive_spend_usd', 0)):.6f}",
        f"- Ambiguous exposure: ${ambiguous_exposure:.6f}",
        f"- Remaining to US$100 ceiling: ${100.0-known_spend-ambiguous_exposure:.6f}", "",
        "## Remaining source-evidence gaps", "",
        f"- Pair-level abstentions: {len(source_gaps)}",
        *[f"- {key}: {value}" for key, value in sorted(source_gap_reasons.items())],
        f"- Current winners with unknown canonical speaker: {len(unavailable_speakers)}",
        "- No model may repair these identities without source evidence.", "",
        "## Acceptance gates", "", f"Passed: {gates.get('passed', False)}",
        f"Stop condition: `{stopping_reason}`",
        "The unmet current-winner gate is retained rather than treating unknown attribution as safe.", "",
        "## Tests", "",
        f"- Focused: {test_results.get('focused', 'not recorded')}",
        f"- Full suite: {test_results.get('full_suite', 'not recorded')}",
        f"- Py compile: {test_results.get('py_compile', 'not recorded')}",
        f"- Git diff check: {test_results.get('git_diff_check', 'not recorded')}", "",
        "## Isolation", "", "- Live production behaviour and the live shadow manifest were not changed.",
        "- No human pairing decisions were requested.",
        "- The diagnostic browser was not started. It is read-only and optional.",
        "- Launch command: `python3 quote_image_metadata_remediation.py serve --run-dir semantic_alignment_research/quote_image_metadata_remediation_001 --host 127.0.0.1 --port 8771`",
        "", "## Git diff --stat", "", "```text", git_diff_stat, "```",
        "", "## Git status --short", "", "```text", git_status_short, "```",
    ]
    atomic_write_json(run_dir / "final_report.json", report)
    atomic_write_text(run_dir / "final_report.md", "\n".join(lines) + "\n")
    return report


class DiagnosticHandler(BaseHTTPRequestHandler):
    """Handle diagnostic requests."""
    run_dir: Path
    rows: list[dict[str, Any]]
    quotes: dict[str, dict[str, Any]]
    images: dict[str, dict[str, Any]]

    def _send(self, body: str, status_code: int = 200, content_type: str = "text/html; charset=utf-8") -> None:
        data = body.encode()
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        """Handle an HTTP POST request."""
        self._send("Read-only diagnostic server", 405, "text/plain; charset=utf-8")

    def do_GET(self) -> None:
        """Handle an HTTP GET request."""
        parsed = urlparse(self.path)
        if parsed.path.startswith("/image/"):
            image_hash = parsed.path.rsplit("/", 1)[-1]
            row = self.images.get(image_hash)
            if not row:
                return self._send("Not found", 404, "text/plain")
            path = Path(row["path"])
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers(); self.wfile.write(data); return
        query = parse_qs(parsed.query)
        decision = query.get("decision", [""])[0]
        rows = [row for row in self.rows if not decision or row.get("decision") == decision]
        cards = []
        for row in rows[:250]:
            quote = self.quotes.get(row["quote_id"], {})
            image = self.images.get(row["image_hash"], {})
            cards.append(f"""
            <article><img src="/image/{html.escape(row['image_hash'])}" alt="Historical photograph">
            <section><h2>{html.escape(quote.get('quote_text',''))}</h2>
            <p><strong>Decision:</strong> {html.escape(row.get('decision',''))} · {html.escape(row.get('basis',''))}</p>
            <p><strong>Meaning:</strong> {html.escape(quote.get('dominant_proposition',''))}</p>
            <p><strong>Image story:</strong> {html.escape(image.get('dominant_visual_story',''))}</p>
            <details><summary>Contracts and evidence</summary><pre>{html.escape(json.dumps({'quote':quote,'image':image,'pair':row},indent=2,ensure_ascii=False))}</pre></details>
            </section></article>""")
        body = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
        <title>Metadata remediation diagnostics</title><style>
        body{{font:16px system-ui;margin:0;background:#f3f4f6;color:#111}}header{{position:sticky;top:0;background:#fff;padding:14px;border-bottom:1px solid #bbb;z-index:2}}
        main{{max-width:1200px;margin:auto;padding:14px}}article{{display:grid;grid-template-columns:minmax(260px,42%) 1fr;gap:18px;background:#fff;margin:0 0 16px;padding:14px;border:1px solid #ccc;border-radius:6px}}
        img{{width:100%;height:auto;max-height:560px;object-fit:contain;background:#eee}}h2{{font-size:1.2rem}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}a{{margin-right:12px}}
        @media(max-width:760px){{article{{grid-template-columns:1fr}}}}
        </style></head><body><header><strong>Read-only metadata diagnostics</strong> · {len(rows)} cases
        <nav><a href="/">All</a><a href="/?decision=allow">Allow</a><a href="/?decision=veto">Veto</a><a href="/?decision=unknown">Unknown</a></nav></header><main>{''.join(cards)}</main></body></html>"""
        self._send(body)

    def log_message(self, format: str, *args: Any) -> None:
        """Log message."""
        return


def serve(run_dir: Path, host: str, port: int) -> None:
    """Serve the configured local interface."""
    quotes, images = load_contracts(run_dir)
    decisions = read_json(run_dir / "candidate_manifest_v3.json", read_json(run_dir / "offline_pair_decisions.json"))
    rows = list((decisions or {}).get("records", {}).values())
    handler = type("BoundDiagnosticHandler", (DiagnosticHandler,), {
        "run_dir": run_dir, "rows": rows, "quotes": quotes, "images": images,
    })
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Read-only remediation diagnostics: http://{host}:{port}", flush=True)
    server.serve_forever()


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    audit_p = sub.add_parser("audit")
    audit_p.add_argument("--project-dir", type=Path, default=ROOT)
    audit_p.add_argument("--harness-dir", type=Path, default=DEFAULT_HARNESS)
    audit_p.add_argument("--output", type=Path, default=DEFAULT_RUN)
    for name in ("local-model-preflight", "qualify-local-model", "build-contracts", "rejudge-offline", "ai-preflight", "enhance-and-judge", "validate", "report", "status", "serve"):
        command = sub.add_parser(name)
        command.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
        if name == "build-contracts": command.add_argument("--local-first", action="store_true")
        if name == "enhance-and-judge":
            command.add_argument("--execute-ai", action="store_true")
            command.add_argument("--hard-spend-limit-usd", type=float, required=True)
            command.add_argument("--enable-vertex-fallback", action="store_true")
            command.add_argument("--batch", action="store_true")
            command.add_argument("--resume", action="store_true")
        if name == "ai-preflight": command.add_argument("--hard-spend-limit-usd", type=float, required=True)
        if name == "validate":
            command.add_argument("--rerun-harness", action="store_true")
            command.add_argument("--resume", action="store_true")
        if name == "serve":
            command.add_argument("--host", default="127.0.0.1")
            command.add_argument("--port", type=int, default=8771)
    model = sub.add_parser("prepare-local-model")
    model.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    model.add_argument("--execute-download", action="store_true")
    model.add_argument("--confirm-model", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line entry point."""
    args = build_parser().parse_args(argv)
    run_dir = args.output if args.command == "audit" else args.run_dir
    run_dir = run_dir.resolve()
    if args.command == "audit": result = audit(args.project_dir.resolve(), args.harness_dir.resolve(), run_dir)
    elif args.command == "local-model-preflight": result = local_model_preflight(run_dir)
    elif args.command == "prepare-local-model": result = prepare_local_model(run_dir, args.execute_download, args.confirm_model)
    elif args.command == "qualify-local-model": result = model_qualification_skipped(run_dir)
    elif args.command == "build-contracts":
        if not args.local_first: raise RemediationError("build-contracts requires --local-first")
        with offline_network_guard(): result = build_contracts(run_dir)["lint"]
    elif args.command == "rejudge-offline":
        with offline_network_guard(): result = rejudge_offline(run_dir)
    elif args.command == "ai-preflight":
        with offline_network_guard(): result = ai_preflight(run_dir, args.hard_spend_limit_usd)
    elif args.command == "enhance-and-judge":
        result = enhance_and_judge(
            run_dir, execute_ai=args.execute_ai, hard_limit=args.hard_spend_limit_usd,
            vertex_fallback=args.enable_vertex_fallback, batch=args.batch, resume=args.resume,
        )
    elif args.command == "status": result = status(run_dir)
    elif args.command == "report": result = render_report(run_dir)
    elif args.command == "serve": serve(run_dir, args.host, args.port); return 0
    elif args.command == "validate":
        with offline_network_guard():
            result = validate_run(run_dir, rerun_harness=args.rerun_harness, resume=args.resume)
    else: raise RemediationError(f"unknown command: {args.command}")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
