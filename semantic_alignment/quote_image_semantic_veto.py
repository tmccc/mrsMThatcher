"""Compile and evaluate the read-only quotation-image veto shadow manifest."""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import statistics
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from .io import atomic_write_json, atomic_write_text, sha256_file


SCHEMA_VERSION = 1
POLICY_VERSION = "material-veto-v2-postrun-corrected-shadow-v1"
ATTRIBUTION_CLEANED_V3_POLICY_VERSION = (
    "affirmative-material-contradiction-rules-v3-runtime-eligible-610"
)
ATTRIBUTION_ELIGIBILITY_RULE_VERSION = "canonical-principal-speaker-v2-reject-misattributed"
DEFAULT_MANIFEST = (
    "semantic_alignment_research/quote_image_semantic_veto_001/shadow/"
    "material_veto_v2_shadow_manifest.json"
)
SOURCE_DIR_NAME = "relation_aware_semantic_veto_002"
BASE_SOURCE_DIR_NAME = "relation_aware_semantic_veto_001"
RUNTIME_DIR_NAME = "quote_image_semantic_veto_runtime"
EXPECTED = {
    "quote_count": 626,
    "image_count": 91,
    "pair_count": 5862,
    "allow_count": 5453,
    "veto_count": 409,
    "current_winner_count": 626,
    "current_winner_allow_count": 583,
    "current_winner_veto_count": 43,
    "quotes_with_allowed": 598,
    "quotes_without_allowed": 28,
    "critical_count": 3,
    "critical_veto_count": 3,
    "positive_count": 5,
    "positive_retained": 5,
    "unknown_count": 0,
    "correction_veto_count": 8,
    "relationship_correction_count": 5,
}
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
LOG_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*? - (?P<msg>.*)$"
)
QUOTE_RE = re.compile(r"Selected quote line_no=(\d+) quote_hash=([0-9a-f]{64})\b")
IMAGE_RE = re.compile(
    r"REGULAR_IMAGE_SELECTED source=(original|generated|other) basename=([^\s]+) "
    r"score=([^\s]+)"
)
CANDIDATE_RE = re.compile(r"Image match candidate basename=([^\s]+) score=([^\s]+)\b")
POST_RE = re.compile(r"EVENT (\{.*\})$")


class ShadowManifestError(ValueError):
    """Raised when a semantic-veto shadow manifest is invalid."""
    pass


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> bytes:
    """Return the canonical JSON."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sha256_value(value: Any) -> str:
    """Return the SHA-256 value."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def classify_veto_category(
    shadow_status: str,
    *,
    alternative_available: bool,
    quote_has_no_allowed_candidate_globally: bool,
) -> str | None:
    """Classify veto category."""
    if shadow_status != "veto":
        return None
    if alternative_available:
        return "selection_error_candidate_available"
    if quote_has_no_allowed_candidate_globally:
        return "coverage_gap_no_safe_image"
    return None


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ShadowManifestError(f"required file missing: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ShadowManifestError(f"cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ShadowManifestError(f"JSON root is not an object: {path}")
    return value


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise ShadowManifestError(message)


def locate_corrected_sources(run_dir: Path) -> tuple[Path, Path]:
    """Return the locate corrected sources."""
    run_dir = run_dir.resolve()
    candidates = [run_dir, run_dir.parent / SOURCE_DIR_NAME]
    source = next(
        (path for path in candidates if (path / "v2_final_status_postrun_corrected.json").is_file()),
        None,
    )
    if source is None:
        raise ShadowManifestError(
            "authoritative corrected v2 source directory not found; uncorrected artefacts are not accepted"
        )
    base_candidates = [source.parent / BASE_SOURCE_DIR_NAME, run_dir.parent / BASE_SOURCE_DIR_NAME]
    base = next((path for path in base_candidates if (path / "quote_corpus_manifest.json").is_file()), None)
    if base is None:
        raise ShadowManifestError("base quotation/image corpus manifests not found")
    return source, base


def source_paths(run_dir: Path) -> dict[str, Path]:
    """Return the source paths."""
    source, base = locate_corrected_sources(run_dir)
    return {
        "corrected_final_status": source / "v2_final_status_postrun_corrected.json",
        "corrected_evaluation": source / "v2_final_evaluation_postrun_corrected.json",
        "correction_audit": source / "v2_postrun_correction_audit.json",
        "pair_decisions": source / "pair_judgements_v2_postrun_corrected.json",
        "candidate_pair_manifest": source / "production_top8_pair_candidates_v2_postrun_corrected.json",
        "quote_contracts": source / "semantic_contracts_v2.json",
        "image_contracts": source / "attested_image_corpus_manifest_postrun_corrected.json",
        "quotation_corpus": base / "quote_corpus_manifest.json",
        "image_corpus": base / "image_corpus_manifest.json",
        "run_manifest": base / "run_manifest.json",
    }


def _validate_authoritative_sources(paths: dict[str, Path]) -> dict[str, dict[str, Any]]:
    values = {name: _read_object(path) for name, path in paths.items()}
    status = values["corrected_final_status"]
    evaluation = values["corrected_evaluation"]
    audit = values["correction_audit"]
    decisions = values["pair_decisions"]
    candidates = values["candidate_pair_manifest"]
    contracts = values["quote_contracts"]
    images = values["image_contracts"]
    quotes = values["quotation_corpus"]
    image_corpus = values["image_corpus"]
    run = values["run_manifest"]

    _expect(status.get("evaluation_passed") is True, "corrected evaluation did not pass")
    _expect(status.get("live_production_enabled") is False, "corrected source was marked live")
    _expect(status.get("offline_correction_only") is True, "correction is not marked offline-only")
    _expect(status.get("paid_api_calls_made") is False, "correction reports paid API calls")
    _expect(evaluation.get("passed") is True, "corrected evaluation passed flag is false")
    exact_status = {
        "candidate_pair_count": EXPECTED["pair_count"],
        "judged_pair_count": EXPECTED["pair_count"],
        "allow_count": EXPECTED["allow_count"],
        "veto_count": EXPECTED["veto_count"],
        "current_production_winner_allow_count": EXPECTED["current_winner_allow_count"],
        "current_production_winner_veto_count": EXPECTED["current_winner_veto_count"],
        "quotes_without_allowed_candidate_count": EXPECTED["quotes_without_allowed"],
        "new_or_changed_deterministic_veto_count": EXPECTED["correction_veto_count"],
        "relationship_metadata_changed_image_count": EXPECTED["relationship_correction_count"],
    }
    for key, expected in exact_status.items():
        _expect(status.get(key) == expected, f"corrected status {key}={status.get(key)!r}, expected {expected}")

    metrics = status.get("metrics") if isinstance(status.get("metrics"), dict) else {}
    expected_metrics = {
        "current_winner_judged_count": EXPECTED["current_winner_count"],
        "quotes_with_allowed_candidate": EXPECTED["quotes_with_allowed"],
        "critical_ally_case_count": EXPECTED["critical_count"],
        "critical_ally_veto_count": EXPECTED["critical_veto_count"],
        "positive_count": EXPECTED["positive_count"],
        "positive_retained_count": EXPECTED["positive_retained"],
        "unknown_pair_count": EXPECTED["unknown_count"],
    }
    for key, expected in expected_metrics.items():
        _expect(metrics.get(key) == expected, f"corrected metric {key}={metrics.get(key)!r}, expected {expected}")

    correction_rows = audit.get("new_or_changed_deterministic_vetoes")
    _expect(isinstance(correction_rows, list) and len(correction_rows) == 8, "correction audit does not retain eight veto changes")
    _expect(len(decisions.get("records", {})) == EXPECTED["pair_count"], "corrected decision dataset is incomplete")
    _expect(decisions.get("failures") in ({}, []), "corrected decision dataset contains failures")
    _expect(candidates.get("quote_count") == EXPECTED["quote_count"], "candidate quote count mismatch")
    _expect(candidates.get("union_pair_count") == EXPECTED["pair_count"], "candidate pair count mismatch")
    _expect(len(candidates.get("records", [])) == EXPECTED["quote_count"], "candidate records are incomplete")
    _expect(len(contracts.get("records", {})) == EXPECTED["quote_count"], "quote contracts are incomplete")
    _expect(len(images.get("records", [])) == EXPECTED["image_count"], "corrected image contracts are incomplete")
    _expect(len(quotes.get("records", [])) == EXPECTED["quote_count"], "quotation corpus is incomplete")
    _expect(quotes.get("metadata", {}).get("unresolved_count") == 6, "unresolved quotation count is not six")
    _expect(len(image_corpus.get("records", [])) == EXPECTED["image_count"], "image corpus is incomplete")
    _expect(run.get("quote_count") == EXPECTED["quote_count"], "run quote count mismatch")
    _expect(run.get("image_count") == EXPECTED["image_count"], "run image count mismatch")
    _expect(len(run.get("unresolved_quote_ids", [])) == 6, "run unresolved quote count mismatch")
    return values


def _reason_codes(judgement: dict[str, Any]) -> list[str]:
    codes = {
        str(item.get("rule"))
        for item in judgement.get("deterministic_contradictions", [])
        if isinstance(item, dict) and item.get("rule")
    }
    codes.update(
        str(value)
        for value in judgement.get("contradiction_types", [])
        if value and value != "none"
    )
    if judgement.get("final_decision") == "veto" and not codes:
        codes.add("model_material_veto")
    return sorted(codes)


def compile_shadow_manifest(run_dir: Path, *, strict: bool = True) -> tuple[dict[str, Any], dict[str, Any], Path]:
    """Compile corrected pair decisions into a deterministic shadow lookup."""
    paths = source_paths(run_dir)
    values = _validate_authoritative_sources(paths)
    decisions = values["pair_decisions"]["records"]
    candidate_groups = values["candidate_pair_manifest"]["records"]
    quote_contracts = values["quote_contracts"]["records"]
    image_rows = values["image_contracts"]["records"]
    quote_rows = values["quotation_corpus"]["records"]
    unresolved = set(values["quotation_corpus"]["metadata"]["unresolved_quote_ids"])

    quote_ids = {str(row["quote_id"]) for row in quote_rows}
    image_by_id = {str(row["image_id"]): row for row in image_rows}
    image_hashes = {str(row["image_sha256"]).lower() for row in image_rows}
    _expect(len(quote_ids) == EXPECTED["quote_count"], "duplicate quotation IDs")
    _expect(not (quote_ids & unresolved), "unresolved quotation entered completed source corpus")
    _expect(len(image_by_id) == EXPECTED["image_count"], "duplicate image IDs")
    _expect(len(image_hashes) == EXPECTED["image_count"], "duplicate image hashes")
    _expect(set(quote_contracts) == quote_ids, "quote contract/corpus identity mismatch")

    candidate_by_pair: dict[str, dict[str, Any]] = {}
    for group in candidate_groups:
        quote_id = str(group.get("quote_id") or "")
        _expect(quote_id in quote_ids, f"candidate references unknown quote {quote_id}")
        for pair in group.get("pairs", []):
            pair_id = str(pair.get("pair_id") or "")
            _expect(HEX64.fullmatch(pair_id) is not None, f"invalid pair ID {pair_id!r}")
            _expect(pair_id not in candidate_by_pair, f"duplicate pair ID {pair_id}")
            _expect(str(pair.get("quote_id")) == quote_id, f"pair {pair_id} quote identity mismatch")
            candidate_by_pair[pair_id] = pair
    _expect(len(candidate_by_pair) == EXPECTED["pair_count"], "candidate pair union is incomplete")
    _expect(set(candidate_by_pair) == set(decisions), "candidate/decision pair sets differ")

    pair_index: dict[str, dict[str, Any]] = {}
    decision_counts: Counter[str] = Counter()
    quote_allowed: Counter[str] = Counter()
    current_winners: dict[str, str] = {}
    for pair_id in sorted(candidate_by_pair):
        pair = candidate_by_pair[pair_id]
        judgement = decisions[pair_id]
        quote_id = str(pair["quote_id"])
        image_id = str(pair["image_id"])
        _expect(quote_id in quote_ids and quote_id not in unresolved, f"pair {pair_id} has ineligible quote")
        _expect(image_id in image_by_id, f"pair {pair_id} has unauthorised image {image_id}")
        _expect(str(judgement.get("pair_id")) == pair_id, f"judgement pair identity mismatch {pair_id}")
        _expect(str(judgement.get("quote_id")) == quote_id, f"judgement quote identity mismatch {pair_id}")
        _expect(str(judgement.get("image_id")) == image_id, f"judgement image identity mismatch {pair_id}")
        decision = str(judgement.get("final_decision") or "")
        _expect(decision in {"allow", "veto"}, f"unknown decision for pair {pair_id}")
        image_hash = str(image_by_id[image_id]["image_sha256"]).lower()
        _expect(HEX64.fullmatch(image_hash) is not None, f"invalid image hash for {image_id}")
        key = f"{quote_id}:{image_hash}"
        _expect(key not in pair_index, f"contradictory duplicate quote/image pair {key}")
        reasons = _reason_codes(judgement)
        _expect(decision != "veto" or bool(reasons), f"veto pair {pair_id} has no reason")
        pair_index[key] = {
            "quote_id": quote_id,
            "image_hash": image_hash,
            "image_id": image_id,
            "decision": decision,
            "final_reason": str(judgement.get("final_reason") or judgement.get("reason") or ""),
            "veto_reason_codes": reasons,
            "materially_misleading": bool(judgement.get("materially_misleading")),
            "model_decision": str(judgement.get("decision") or ""),
            "model_confidence": str(judgement.get("confidence") or ""),
            "deterministic_contradictions": list(judgement.get("deterministic_contradictions") or []),
            "selector_score_at_research_time": pair.get("selector_score"),
            "source_pair_id": pair_id,
        }
        decision_counts[decision] += 1
        if decision == "allow":
            quote_allowed[quote_id] += 1
        ranks = pair.get("ranks") if isinstance(pair.get("ranks"), dict) else {}
        if ranks.get("current_69_top5") == 1:
            _expect(quote_id not in current_winners, f"multiple current winners for quote {quote_id}")
            current_winners[quote_id] = pair_id

    _expect(decision_counts == {"allow": EXPECTED["allow_count"], "veto": EXPECTED["veto_count"]}, "decision counts differ from corrected status")
    _expect(len(current_winners) == EXPECTED["current_winner_count"], "not all current production winners are represented")
    current_counts = Counter(decisions[pair_id]["final_decision"] for pair_id in current_winners.values())
    _expect(
        current_counts == {"allow": EXPECTED["current_winner_allow_count"], "veto": EXPECTED["current_winner_veto_count"]},
        "current production winner decision counts mismatch",
    )
    with_allowed = {quote_id for quote_id in quote_ids if quote_allowed[quote_id]}
    _expect(len(with_allowed) == EXPECTED["quotes_with_allowed"], "allowed-candidate quotation count mismatch")

    aliases = values["candidate_pair_manifest"].get("quote_analysis_aliases") or {}
    runtime_quote_aliases = {str(value): str(key) for key, value in aliases.items()}
    _expect(all(target in quote_ids for target in runtime_quote_aliases.values()), "invalid quote alias target")
    quote_text = {str(row["quote_id"]): str(row.get("quote_text") or "") for row in quote_rows}
    relative_root = run_dir.resolve().parents[1] if len(run_dir.resolve().parents) > 1 else run_dir.resolve().parent
    source_files: dict[str, dict[str, str]] = {}
    for name, path in paths.items():
        try:
            display = str(path.resolve().relative_to(relative_root))
        except ValueError:
            display = str(path.resolve())
        source_files[name] = {"path": display, "sha256": sha256_file(path)}

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "source_run_id": "relation_aware_semantic_veto_002:postrun_corrected",
        "source_file_hashes": source_files,
        # Pin the manifest timestamp to the immutable correction audit so a
        # repeated compile is byte-for-byte deterministic.
        "compiled_at": str(values["correction_audit"].get("generated_at") or ""),
        "quote_count": EXPECTED["quote_count"],
        "image_count": EXPECTED["image_count"],
        "pair_count": EXPECTED["pair_count"],
        "allow_count": EXPECTED["allow_count"],
        "veto_count": EXPECTED["veto_count"],
        "current_production_winner_count": EXPECTED["current_winner_count"],
        "current_production_winner_allow_count": EXPECTED["current_winner_allow_count"],
        "current_production_winner_veto_count": EXPECTED["current_winner_veto_count"],
        "quotes_with_allowed_candidate": EXPECTED["quotes_with_allowed"],
        "quotes_without_allowed_candidate": EXPECTED["quotes_without_allowed"],
        "quotes_without_allowed_candidate_ids": sorted(quote_ids - with_allowed),
        "critical_ally_enemy_veto_count": EXPECTED["critical_veto_count"],
        "critical_ally_enemy_case_count": EXPECTED["critical_count"],
        "eligible_positive_retained_count": EXPECTED["positive_retained"],
        "eligible_positive_count": EXPECTED["positive_count"],
        "unknown_pair_count": 0,
        "live_production_enabled": False,
        "paid_api_calls_in_correction": False,
        "corrected_deterministic_veto_change_count": 8,
        "relationship_metadata_correction_count": 5,
        "runtime_quote_aliases": dict(sorted(runtime_quote_aliases.items())),
        "quote_text": dict(sorted(quote_text.items())),
        "image_hashes": sorted(image_hashes),
        "quote_has_allowed_candidate": {quote_id: quote_id in with_allowed for quote_id in sorted(quote_ids)},
        "pairs": pair_index,
    }
    output = run_dir / "shadow" / "material_veto_v2_shadow_manifest.json"
    audit = validate_compiled_manifest(manifest, strict=strict)
    return manifest, audit, output


def validate_compiled_manifest(value: dict[str, Any], *, strict: bool = True) -> dict[str, Any]:
    """Validate shadow-manifest identity, counts, uniqueness, and provenance."""
    _expect(value.get("schema_version") == SCHEMA_VERSION, "unsupported shadow manifest schema")
    policy_version = str(value.get("policy_version") or "")
    _expect(
        policy_version in {POLICY_VERSION, ATTRIBUTION_CLEANED_V3_POLICY_VERSION},
        "unsupported or stale shadow policy",
    )
    if policy_version == ATTRIBUTION_CLEANED_V3_POLICY_VERSION:
        return _validate_attribution_cleaned_v3_manifest(value, strict=strict)
    exact = {
        "quote_count": 626,
        "image_count": 91,
        "pair_count": 5862,
        "allow_count": 5453,
        "veto_count": 409,
        "current_production_winner_count": 626,
        "current_production_winner_allow_count": 583,
        "current_production_winner_veto_count": 43,
        "quotes_with_allowed_candidate": 598,
        "quotes_without_allowed_candidate": 28,
        "critical_ally_enemy_veto_count": 3,
        "critical_ally_enemy_case_count": 3,
        "eligible_positive_retained_count": 5,
        "eligible_positive_count": 5,
        "unknown_pair_count": 0,
        "corrected_deterministic_veto_change_count": 8,
        "relationship_metadata_correction_count": 5,
    }
    for key, expected in exact.items():
        _expect(value.get(key) == expected, f"manifest {key} mismatch")
    _expect(value.get("live_production_enabled") is False, "manifest incorrectly marks live enforcement")
    _expect(value.get("paid_api_calls_in_correction") is False, "manifest correction was not zero-cost")
    pairs = value.get("pairs")
    _expect(isinstance(pairs, dict) and len(pairs) == 5862, "manifest pair index is incomplete")
    counts: Counter[str] = Counter()
    pair_ids: set[str] = set()
    quote_ids: set[str] = set()
    image_hashes = set(value.get("image_hashes") or [])
    _expect(len(image_hashes) == 91, "manifest authorised image set mismatch")
    for key, row in pairs.items():
        _expect(isinstance(row, dict), f"manifest pair {key} is not an object")
        quote_id = str(row.get("quote_id") or "")
        image_hash = str(row.get("image_hash") or "")
        _expect(key == f"{quote_id}:{image_hash}", f"manifest key mismatch {key}")
        _expect(HEX64.fullmatch(quote_id) is not None, f"invalid quote ID {quote_id}")
        _expect(image_hash in image_hashes, f"unauthorised image hash {image_hash}")
        decision = str(row.get("decision") or "")
        _expect(decision in {"allow", "veto"}, f"unknown pair verdict {decision!r}")
        _expect(decision != "veto" or bool(row.get("veto_reason_codes")), f"veto {key} lacks reason")
        pair_id = str(row.get("source_pair_id") or "")
        _expect(HEX64.fullmatch(pair_id) is not None and pair_id not in pair_ids, f"duplicate/invalid source pair ID {pair_id}")
        pair_ids.add(pair_id)
        quote_ids.add(quote_id)
        counts[decision] += 1
    _expect(counts == {"allow": 5453, "veto": 409}, "compiled manifest decision totals mismatch")
    _expect(len(quote_ids) == 626, "compiled manifest quotation coverage mismatch")
    flags = value.get("quote_has_allowed_candidate")
    _expect(isinstance(flags, dict) and len(flags) == 626, "global quote safety flags are incomplete")
    _expect(sum(flag is True for flag in flags.values()) == 598, "global quote safety flag totals mismatch")
    return {
        "valid": True,
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "quote_count": len(quote_ids),
        "image_count": len(image_hashes),
        "pair_count": len(pairs),
        "allow_count": counts["allow"],
        "veto_count": counts["veto"],
        "source_pair_ids_unique": len(pair_ids) == len(pairs),
        "strict": bool(strict),
    }


def _validate_attribution_cleaned_v3_manifest(
    value: dict[str, Any], *, strict: bool
) -> dict[str, Any]:
    exact = {
        "quote_count": 610,
        "image_count": 91,
        "pair_count": 22_066,
        "allow_count": 21_938,
        "veto_count": 128,
        "quotes_with_allowed_candidate": 609,
        "quotes_without_allowed_candidate": 1,
        "unknown_pair_count_excluded_from_lookup": 167,
    }
    for key, expected in exact.items():
        _expect(value.get(key) == expected, f"v3 manifest {key} mismatch")
    _expect(value.get("live_production_enabled") is False, "manifest incorrectly marks live enforcement")

    pairs = value.get("pairs")
    _expect(isinstance(pairs, dict) and len(pairs) == exact["pair_count"], "v3 pair index is incomplete")
    counts: Counter[str] = Counter()
    pair_ids: set[str] = set()
    quote_ids: set[str] = set()
    image_hashes: set[str] = set()
    for key, row in pairs.items():
        _expect(isinstance(row, dict), f"v3 manifest pair {key} is not an object")
        quote_id = str(row.get("quote_id") or "")
        image_hash = str(row.get("image_hash") or "")
        _expect(key == f"{quote_id}:{image_hash}", f"v3 manifest key mismatch {key}")
        _expect(HEX64.fullmatch(quote_id) is not None, f"invalid v3 quote ID {quote_id}")
        _expect(HEX64.fullmatch(image_hash) is not None, f"invalid v3 image hash {image_hash}")
        decision = str(row.get("decision") or "")
        _expect(decision in {"allow", "veto"}, f"unknown v3 pair verdict {decision!r}")
        _expect(decision != "veto" or bool(row.get("veto_reason_codes")), f"v3 veto {key} lacks reason")
        pair_id = str(row.get("source_pair_id") or "")
        _expect(HEX64.fullmatch(pair_id) is not None and pair_id not in pair_ids, f"duplicate/invalid v3 source pair ID {pair_id}")
        pair_ids.add(pair_id)
        quote_ids.add(quote_id)
        image_hashes.add(image_hash)
        counts[decision] += 1

    _expect(counts == {"allow": 21_938, "veto": 128}, "v3 manifest decision totals mismatch")
    _expect(len(quote_ids) == 610, "v3 manifest quotation coverage mismatch")
    _expect(len(image_hashes) == 91, "v3 manifest authorised image set mismatch")
    flags = value.get("quote_has_allowed_candidate")
    _expect(isinstance(flags, dict) and set(flags) == quote_ids, "v3 global quote safety flags are incomplete")
    _expect(sum(flag is True for flag in flags.values()) == 609, "v3 global quote safety flag totals mismatch")
    _expect(sum(flag is False for flag in flags.values()) == 1, "v3 no-safe-image flag total mismatch")
    aliases = value.get("runtime_quote_aliases") or {}
    _expect(isinstance(aliases, dict) and len(aliases) == 5, "v3 runtime quote aliases mismatch")
    runtime_quote_ids = value.get("runtime_eligible_quote_ids")
    _expect(
        isinstance(runtime_quote_ids, list)
        and len(runtime_quote_ids) == 610
        and len(set(runtime_quote_ids)) == 610
        and all(HEX64.fullmatch(str(quote_id)) is not None for quote_id in runtime_quote_ids),
        "v3 runtime eligibility IDs are invalid",
    )
    resolved_runtime_ids = {
        str(aliases.get(str(quote_id)) or quote_id)
        for quote_id in runtime_quote_ids
    }
    _expect(resolved_runtime_ids == quote_ids, "v3 runtime eligibility set differs from pair coverage")
    _expect(
        value.get("attribution_rule_version") == ATTRIBUTION_ELIGIBILITY_RULE_VERSION,
        "v3 attribution rule version mismatch",
    )
    source_hashes = value.get("source_file_hashes")
    _expect(isinstance(source_hashes, dict) and source_hashes, "v3 source hashes are missing")
    for name, source in source_hashes.items():
        _expect(
            isinstance(source, dict)
            and isinstance(source.get("path"), str)
            and HEX64.fullmatch(str(source.get("sha256") or "")) is not None,
            f"v3 source hash record is invalid: {name}",
        )
    _expect(
        {"runtime_eligible_quote_manifest", "completed_quote_research", "attribution_predicate"}
        <= set(source_hashes),
        "v3 eligibility freshness sources are incomplete",
    )
    return {
        "valid": True,
        "schema_version": SCHEMA_VERSION,
        "policy_version": ATTRIBUTION_CLEANED_V3_POLICY_VERSION,
        "quote_count": len(quote_ids),
        "image_count": len(image_hashes),
        "pair_count": len(pairs),
        "allow_count": counts["allow"],
        "veto_count": counts["veto"],
        "source_pair_ids_unique": len(pair_ids) == len(pairs),
        "strict": bool(strict),
    }


def write_compiled_manifest(run_dir: Path, *, strict: bool = True) -> dict[str, Any]:
    """Write a validated shadow manifest atomically."""
    manifest, audit, output = compile_shadow_manifest(run_dir, strict=strict)
    atomic_write_json(output, manifest)
    manifest_hash = sha256_file(output)
    audit.update({
        "generated_at": utc_now(),
        "manifest_path": str(output),
        "manifest_sha256": manifest_hash,
        "source_file_hashes": manifest["source_file_hashes"],
        "blocking_errors": [],
    })
    atomic_write_json(output.parent / "shadow_manifest_audit.json", audit)
    atomic_write_text(
        output.parent / "shadow_manifest_audit.md",
        "# Material-veto v2 shadow manifest audit\n\n"
        f"- Result: **PASS**\n- Manifest SHA-256: `{manifest_hash}`\n"
        f"- Quotations: {audit['quote_count']}\n- Images: {audit['image_count']}\n"
        f"- Pairs: {audit['pair_count']} ({audit['allow_count']} allow, {audit['veto_count']} veto)\n"
        "- Authoritative source: corrected post-run v2 artefacts only\n"
        "- Active enforcement: disabled\n- Additional provider calls: none\n",
    )
    return audit


def _deep_size(value: Any, seen: set[int] | None = None) -> int:
    seen = seen or set()
    identity = id(value)
    if identity in seen:
        return 0
    seen.add(identity)
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(_deep_size(key, seen) + _deep_size(item, seen) for key, item in value.items())
    elif isinstance(value, (list, tuple, set, frozenset)):
        size += sum(_deep_size(item, seen) for item in value)
    return size


def validate_shadow_config(config: Any) -> list[str]:
    """Validate that semantic veto configuration is disabled or shadow-only."""
    if not isinstance(config, dict):
        return ["quote_image_semantic_veto must be an object"]
    expected = {
        "enabled", "mode", "manifest_path", "fail_open",
        "record_best_allowed_alternative", "maximum_shadow_history",
    }
    if set(config) != expected:
        return ["quote_image_semantic_veto fields mismatch"]
    errors: list[str] = []
    if type(config.get("enabled")) is not bool:
        errors.append("quote_image_semantic_veto.enabled must be boolean")
    if config.get("mode") not in {"disabled", "shadow"}:
        errors.append("quote_image_semantic_veto.mode must be disabled or shadow")
    if config.get("enabled") and config.get("mode") != "shadow":
        errors.append("enabled quote_image_semantic_veto must use shadow mode")
    if not isinstance(config.get("manifest_path"), str) or not config["manifest_path"].strip():
        errors.append("quote_image_semantic_veto.manifest_path must be non-empty")
    if config.get("fail_open") is not True:
        errors.append("quote_image_semantic_veto.fail_open must remain true")
    if type(config.get("record_best_allowed_alternative")) is not bool:
        errors.append("quote_image_semantic_veto.record_best_allowed_alternative must be boolean")
    maximum = config.get("maximum_shadow_history")
    if type(maximum) is not int or not 100 <= maximum <= 100_000:
        errors.append("quote_image_semantic_veto.maximum_shadow_history must be 100..100000")
    return errors


class ShadowHistoryWriter:
    """Persist and manage shadow history records."""
    def __init__(self, runtime_dir: Path, maximum_records: int):
        """Initialise the shadow history writer."""
        self.runtime_dir = runtime_dir
        self.path = runtime_dir / "shadow_history.jsonl"
        self.status_path = runtime_dir / "shadow_status.json"
        self.maximum_records = int(maximum_records)
        self.lock = threading.Lock()
        runtime_dir.mkdir(parents=True, exist_ok=True)
        self.count = sum(1 for _ in self.path.open(encoding="utf-8")) if self.path.is_file() else 0

    def append(self, event: dict[str, Any]) -> None:
        """Append one bounded semantic-veto shadow event."""
        line = json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self.lock:
            if self.count >= self.maximum_records and self.path.exists():
                timestamp = int(time.time())
                archive = self.runtime_dir / f"shadow_history.{timestamp}.jsonl"
                suffix = 1
                while archive.exists():
                    archive = self.runtime_dir / f"shadow_history.{timestamp}.{suffix}.jsonl"
                    suffix += 1
                os.replace(self.path, archive)
                self.count = 0
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            self.count += 1
            summary = summarise_events(read_shadow_history(self.runtime_dir, self.maximum_records))
            summary.update({"schema_version": 1, "updated_at": utc_now(), "active_history_records": self.count})
            atomic_write_json(self.status_path, summary)


def read_shadow_history(runtime_dir: Path, maximum: int = 10_000) -> list[dict[str, Any]]:
    """Read shadow history."""
    path = runtime_dir / "shadow_history.jsonl"
    if maximum <= 0:
        return []
    archive_pattern = re.compile(r"shadow_history\.(\d+)(?:\.(\d+))?\.jsonl\Z")

    def archive_key(item: Path) -> tuple[int, int, str]:
        match = archive_pattern.fullmatch(item.name)
        if match:
            return int(match.group(1)), int(match.group(2) or 0), item.name
        return 0, 0, item.name

    paths = sorted(runtime_dir.glob("shadow_history.*.jsonl"), key=archive_key)
    if path.is_file():
        paths.append(path)
    rows: list[dict[str, Any]] = []
    for history_path in reversed(paths):
        current: list[dict[str, Any]] = []
        for line in history_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                current.append(value)
        rows = current + rows
        if len(rows) >= maximum:
            break
    return rows[-maximum:]


@dataclass
class ShadowRuntime:
    """Represent shadow runtime data."""
    available: bool
    manifest_path: Path
    manifest_sha256: str = ""
    policy_version: str = ""
    pairs: dict[str, dict[str, Any]] | None = None
    quote_aliases: dict[str, str] | None = None
    quote_flags: dict[str, bool] | None = None
    quote_text: dict[str, str] | None = None
    reason: str = ""
    status: str = "manifest_unavailable"
    load_time_ms: float = 0.0
    memory_bytes: int = 0
    writer: ShadowHistoryWriter | None = None
    record_alternative: bool = True

    @classmethod
    def load(
        cls,
        project_dir: Path,
        config: dict[str, Any],
        *,
        verify_source_hashes: bool = True,
        enable_history: bool = True,
        expected_runtime_quote_ids: set[str] | None = None,
    ) -> "ShadowRuntime":
        """Load and validate a fail-open shadow runtime once at startup."""
        errors = validate_shadow_config(config)
        if errors:
            return cls(False, Path(str(config.get("manifest_path") or "")), reason="; ".join(errors))
        path = Path(config["manifest_path"])
        if not path.is_absolute():
            path = project_dir / path
        started = time.perf_counter()
        try:
            manifest = _read_object(path)
            validate_compiled_manifest(manifest)
            stale = []
            if verify_source_hashes:
                for item in manifest.get("source_file_hashes", {}).values():
                    source_path = Path(str(item.get("path") or ""))
                    if not source_path.is_absolute():
                        source_path = project_dir / source_path
                    if not source_path.is_file() or sha256_file(source_path) != item.get("sha256"):
                        stale.append(str(source_path))
            if stale:
                return cls(
                    False, path, manifest_sha256=sha256_file(path), policy_version=str(manifest.get("policy_version") or ""),
                    reason=f"source hash mismatch: {stale[0]}", status="manifest_stale",
                    load_time_ms=(time.perf_counter() - started) * 1000,
                )
            if expected_runtime_quote_ids is not None:
                flags = manifest.get("quote_has_allowed_candidate") or {}
                aliases = manifest.get("runtime_quote_aliases") or {}
                resolved_expected = {
                    str(aliases.get(str(quote_id)) or quote_id)
                    for quote_id in expected_runtime_quote_ids
                }
                if resolved_expected != set(flags):
                    extra = sorted(set(flags) - resolved_expected)
                    missing = sorted(resolved_expected - set(flags))
                    return cls(
                        False,
                        path,
                        manifest_sha256=sha256_file(path),
                        policy_version=str(manifest.get("policy_version") or ""),
                        reason=(
                            "runtime quotation eligibility mismatch: "
                            f"manifest_extra={extra[:1]} runtime_missing={missing[:1]}"
                        ),
                        status="manifest_stale",
                        load_time_ms=(time.perf_counter() - started) * 1000,
                    )
            runtime = cls(
                True,
                path,
                manifest_sha256=sha256_file(path),
                policy_version=str(manifest["policy_version"]),
                pairs=manifest["pairs"],
                quote_aliases=manifest.get("runtime_quote_aliases") or {},
                quote_flags=manifest["quote_has_allowed_candidate"],
                quote_text=manifest.get("quote_text") or {},
                status="allow",
                load_time_ms=(time.perf_counter() - started) * 1000,
                record_alternative=bool(config["record_best_allowed_alternative"]),
            )
            runtime.memory_bytes = _deep_size({
                "pairs": runtime.pairs,
                "aliases": runtime.quote_aliases,
                "flags": runtime.quote_flags,
            })
            if enable_history:
                runtime.writer = ShadowHistoryWriter(
                    project_dir / RUNTIME_DIR_NAME,
                    int(config["maximum_shadow_history"]),
                )
            return runtime
        except Exception as exc:
            return cls(
                False, path, reason=f"{type(exc).__name__}: {exc}", status="manifest_unavailable",
                load_time_ms=(time.perf_counter() - started) * 1000,
            )

    def canonical_quote_id(self, quote_hash: str) -> str:
        """Return the canonical quote ID."""
        quote_hash = str(quote_hash or "").lower()
        if self.quote_flags and quote_hash in self.quote_flags:
            return quote_hash
        return str((self.quote_aliases or {}).get(quote_hash) or quote_hash)

    def pair(self, quote_id: str, image_hash: str) -> dict[str, Any] | None:
        """Return the pair."""
        return (self.pairs or {}).get(f"{quote_id}:{str(image_hash or '').lower()}")

    def evaluate(
        self,
        *,
        quote_hash: str,
        selected: dict[str, Any],
        candidates: Sequence[dict[str, Any]],
        quote_preview: str = "",
        tie_break_state: object | None = None,
    ) -> dict[str, Any]:
        """Evaluate a completed production selection without changing it."""
        started = time.perf_counter_ns()
        quote_id = self.canonical_quote_id(quote_hash)
        source = str(selected.get("image_source") or "original")
        selected_hash = str(selected.get("image_hash") or "").lower()
        selected_score = float(selected.get("score") or 0.0)
        selected_pair: dict[str, Any] | None = None
        if not self.available:
            status = self.status
        elif source == "generated":
            status = "out_of_scope_generated"
        else:
            selected_pair = self.pair(quote_id, selected_hash)
            status = str(selected_pair.get("decision")) if selected_pair else "unknown_unjudged"

        classified: list[tuple[dict[str, Any], str, dict[str, Any] | None]] = []
        for candidate in candidates:
            candidate_source = str(candidate.get("image_source") or "original")
            if candidate_source == "generated":
                classified.append((candidate, "out_of_scope_generated", None))
                continue
            row = self.pair(quote_id, str(candidate.get("image_hash") or "")) if self.available else None
            classified.append((candidate, str(row.get("decision")) if row else "unknown_unjudged", row))
        allowed = [item for item in classified if item[1] == "allow"]
        alternative = None
        if status == "veto" and self.record_alternative and allowed:
            best_score = max(float(item[0].get("score") or 0.0) for item in allowed)
            tied = [item[0] for item in allowed if float(item[0].get("score") or 0.0) == best_score]
            if len(tied) == 1:
                alternative = tied[0]
            elif tie_break_state is not None:
                local_rng = random.Random()
                local_rng.setstate(tie_break_state)
                alternative = local_rng.choice(tied)
            else:
                alternative = min(tied, key=lambda item: str(item.get("basename") or ""))
        quote_flag = (self.quote_flags or {}).get(quote_id)
        veto_category = classify_veto_category(
            status,
            alternative_available=alternative is not None,
            quote_has_no_allowed_candidate_globally=quote_flag is False,
        )
        latency = (time.perf_counter_ns() - started) / 1_000_000
        event = {
            "event": "quote_image_semantic_veto_shadow",
            "timestamp": utc_now(),
            "quote_id": quote_id,
            "quote_hash": str(quote_hash or ""),
            "quote_preview": str(quote_preview or "")[:160],
            "selected_image_hash": selected_hash,
            "selected_image_basename": str(selected.get("basename") or ""),
            "selected_image_source": source,
            "selected_score": round(selected_score, 6),
            "shadow_status": status,
            "would_veto_production_winner": status == "veto",
            "veto_category": veto_category,
            "veto_reason_codes": list((selected_pair or {}).get("veto_reason_codes") or []),
            "veto_explanation": str((selected_pair or {}).get("final_reason") or self.reason or ""),
            "alternative_available": alternative is not None,
            "alternative_image_hash": str(alternative.get("image_hash")) if alternative else None,
            "alternative_image_basename": str(alternative.get("basename")) if alternative else None,
            "alternative_source": str(alternative.get("image_source") or "original") if alternative else None,
            "alternative_score": round(float(alternative.get("score") or 0.0), 6) if alternative else None,
            "score_delta_from_production_winner": round(float(alternative.get("score") or 0.0) - selected_score, 6) if alternative else None,
            "alternative_reason": (
                None if alternative else
                "quote_has_no_allowed_candidate_globally" if quote_flag is False else
                "no_allowed_alternative_in_current_candidate_set" if status == "veto" else None
            ),
            "eligible_candidate_count": len(candidates),
            "allowed_candidate_count": sum(item[1] == "allow" for item in classified),
            "vetoed_candidate_count": sum(item[1] == "veto" for item in classified),
            "unknown_candidate_count": sum(item[1] == "unknown_unjudged" for item in classified),
            "out_of_scope_candidate_count": sum(item[1] == "out_of_scope_generated" for item in classified),
            "quote_has_allowed_candidate_globally": quote_flag is True,
            "quote_has_no_allowed_candidate_globally": quote_flag is False,
            "manifest_policy_version": self.policy_version,
            "manifest_sha256": self.manifest_sha256,
            "lookup_latency_ms": round(latency, 6),
            "production_selection_changed": False,
        }
        if self.writer:
            self.writer.append(event)
        return event


def percentile(values: Sequence[float], fraction: float) -> float | None:
    """Return the percentile."""
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    low = math.floor(index)
    high = math.ceil(index)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def _manifest_event_key(row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("manifest_policy_version") or "unavailable"),
        str(row.get("manifest_sha256") or ""),
    )


def _summarise_event_subset(events: Sequence[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(str(row.get("shadow_status") or "unknown") for row in events)
    veto_rows = [row for row in events if row.get("shadow_status") == "veto"]
    latency = [float(row["lookup_latency_ms"]) for row in events if isinstance(row.get("lookup_latency_ms"), (int, float))]
    deltas = [float(row["score_delta_from_production_winner"]) for row in veto_rows if isinstance(row.get("score_delta_from_production_winner"), (int, float))]
    reasons = Counter(code for row in veto_rows for code in row.get("veto_reason_codes", []))
    categories = Counter(
        row.get("veto_category") or classify_veto_category(
            str(row.get("shadow_status") or ""),
            alternative_available=row.get("alternative_available") is True,
            quote_has_no_allowed_candidate_globally=(
                row.get("quote_has_no_allowed_candidate_globally") is True
            ),
        )
        for row in veto_rows
    )
    no_safe_quote_ids = {
        str(row.get("quote_id"))
        for row in events
        if row.get("quote_has_no_allowed_candidate_globally") is True and row.get("quote_id")
    }
    return {
        "events": len(events),
        "status_counts": dict(sorted(statuses.items())),
        "allowed": statuses["allow"],
        "vetoed": statuses["veto"],
        "unknown": statuses["unknown_unjudged"],
        "generated_out_of_scope": statuses["out_of_scope_generated"],
        "manifest_unavailable": statuses["manifest_unavailable"],
        "manifest_stale": statuses["manifest_stale"],
        "vetoed_with_alternative": sum(row.get("alternative_available") is True for row in veto_rows),
        "vetoed_without_alternative": sum(row.get("alternative_available") is not True for row in veto_rows),
        "selection_error_candidate_available": categories["selection_error_candidate_available"],
        "coverage_gap_no_safe_image": categories["coverage_gap_no_safe_image"],
        "quotes_with_no_globally_allowed_candidate": len(no_safe_quote_ids),
        "veto_reason_counts": dict(reasons.most_common()),
        "alternative_score_delta_median": statistics.median(deltas) if deltas else None,
        "alternative_score_delta_min": min(deltas) if deltas else None,
        "alternative_score_delta_max": max(deltas) if deltas else None,
        "lookup_latency_p50_ms": percentile(latency, 0.5),
        "lookup_latency_p95_ms": percentile(latency, 0.95),
        "lookup_latency_max_ms": max(latency) if latency else None,
        "manifest_policy_version": next((str(row.get("manifest_policy_version")) for row in reversed(events) if row.get("manifest_policy_version")), "unavailable"),
        "manifest_sha256": next((str(row.get("manifest_sha256")) for row in reversed(events) if row.get("manifest_sha256")), ""),
        "production_selection_change_failures": sum(row.get("production_selection_changed") is not False for row in events),
        "last_event": events[-1] if events else None,
    }


def summarise_events(
    events: Sequence[dict[str, Any]],
    *,
    current_manifest_sha256: str | None = None,
    current_policy_version: str | None = None,
) -> dict[str, Any]:
    """Summarise events."""
    all_events = list(events)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in all_events:
        grouped.setdefault(_manifest_event_key(row), []).append(row)
    if current_manifest_sha256 is not None:
        selected_key = next(
            (key for key in grouped if key[1] == current_manifest_sha256),
            (str(current_policy_version or "unavailable"), current_manifest_sha256),
        )
    elif all_events:
        selected_key = _manifest_event_key(all_events[-1])
    else:
        selected_key = (str(current_policy_version or "unavailable"), str(current_manifest_sha256 or ""))
    selected_events = grouped.get(selected_key, [])
    summary = _summarise_event_subset(selected_events)
    summary["manifest_policy_version"] = selected_key[0]
    summary["manifest_sha256"] = selected_key[1]
    summary.update({
        "history_events_all_manifests": len(all_events),
        "events_excluded_from_current_manifest_summary": len(all_events) - len(selected_events),
        "mixed_manifest_versions": len(grouped) > 1,
        "manifest_strata": [
            {
                "manifest_policy_version": key[0],
                "manifest_sha256": key[1],
                "events": len(rows),
                "status_counts": _summarise_event_subset(rows)["status_counts"],
            }
            for key, rows in grouped.items()
        ],
    })
    return summary


def _iter_log_records(project_dir: Path, since_days: int) -> list[tuple[datetime, str, str, int]]:
    cutoff = datetime.now() - timedelta(days=since_days)
    rows: list[tuple[datetime, str, str, int]] = []
    seen: set[tuple[str, str]] = set()
    for path in sorted(project_dir.glob("mrsMThatcher*.log*")):
        if "selftest" in path.name.lower():
            continue
        for ordinal, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines()):
            match = LOG_RE.match(line)
            if not match:
                continue
            ts = datetime.strptime(match.group("ts"), "%Y-%m-%d %H:%M:%S")
            if ts < cutoff:
                continue
            msg = match.group("msg")
            identity = (match.group("ts"), msg)
            if identity in seen:
                continue
            seen.add(identity)
            rows.append((ts, msg, path.name, ordinal))
    rows.sort(key=lambda row: (row[0], row[2], row[3]))
    return rows


def historical_replay(project_dir: Path, manifest_path: Path, *, since_days: int) -> dict[str, Any]:
    """Replay historical selections locally against the shadow manifest."""
    config = {
        "enabled": True,
        "mode": "shadow",
        "manifest_path": str(manifest_path),
        "fail_open": True,
        "record_best_allowed_alternative": False,
        "maximum_shadow_history": 10_000,
    }
    runtime = ShadowRuntime.load(project_dir, config, verify_source_hashes=True, enable_history=False)
    if not runtime.available:
        raise ShadowManifestError(runtime.reason)
    pending_quote: tuple[str, datetime] | None = None
    pending_image: tuple[str, str, float, datetime] | None = None
    pending_candidates: list[tuple[str, float]] = []
    observed: list[dict[str, Any]] = []
    path_hashes: dict[str, str] = {}
    for analysis_name in ("image_analysis.json", "generated_image_analysis.json"):
        try:
            path_index = _read_object(project_dir / analysis_name).get("path_index")
            if isinstance(path_index, dict):
                path_hashes.update({str(name): str(digest) for name, digest in path_index.items()})
        except Exception:
            continue
    for ts, msg, _, _ in _iter_log_records(project_dir, since_days):
        quote_match = QUOTE_RE.search(msg)
        if quote_match:
            pending_quote = (quote_match.group(2), ts)
            pending_image = None
            pending_candidates = []
            continue
        image_match = IMAGE_RE.search(msg)
        if image_match and pending_quote:
            try:
                score = float(image_match.group(3))
            except ValueError:
                score = 0.0
            pending_image = (image_match.group(1), image_match.group(2), score, ts)
            continue
        candidate_match = CANDIDATE_RE.search(msg)
        if candidate_match and pending_quote:
            try:
                pending_candidates.append((candidate_match.group(1), float(candidate_match.group(2))))
            except ValueError:
                pass
            continue
        post_match = POST_RE.search(msg)
        if not post_match or not pending_quote or not pending_image:
            continue
        try:
            event = json.loads(post_match.group(1))
        except json.JSONDecodeError:
            continue
        if event.get("event") != "main_post_posted" or event.get("lane") != "quote_image":
            continue
        quote_hash, quote_ts = pending_quote
        source, basename, score, image_ts = pending_image
        if (ts - quote_ts).total_seconds() > 900 or (ts - image_ts).total_seconds() > 900:
            pending_quote = pending_image = None
            continue
        image_hash = path_hashes.get(basename, "")
        if source == "generated":
            status = "out_of_scope_generated"
            row = None
        else:
            row = runtime.pair(runtime.canonical_quote_id(quote_hash), image_hash)
            status = str(row.get("decision")) if row else "unknown_unjudged"
        alternative = None
        if status == "veto":
            replay_candidates = [(basename, score), *pending_candidates]
            unique_candidates: dict[str, float] = {}
            for candidate_name, candidate_score in replay_candidates:
                unique_candidates[candidate_name] = max(candidate_score, unique_candidates.get(candidate_name, -math.inf))
            allowed_candidates = []
            for candidate_name, candidate_score in unique_candidates.items():
                if candidate_name.startswith("tg_"):
                    continue
                candidate_row = runtime.pair(runtime.canonical_quote_id(quote_hash), path_hashes.get(candidate_name, ""))
                if candidate_row and candidate_row.get("decision") == "allow":
                    allowed_candidates.append((candidate_name, candidate_score, path_hashes.get(candidate_name, "")))
            if allowed_candidates:
                alternative = sorted(allowed_candidates, key=lambda item: (-item[1], item[0]))[0]
        quote_has_no_allowed_candidate_globally = (
            (runtime.quote_flags or {}).get(runtime.canonical_quote_id(quote_hash)) is False
        )
        observed.append({
            "time": ts.isoformat(), "post_id": str(event.get("post_id") or ""),
            "quote_id": runtime.canonical_quote_id(quote_hash), "quote_hash": quote_hash,
            "image_basename": basename, "image_hash": image_hash, "image_source": source,
            "score": score, "shadow_status": status,
            "veto_category": classify_veto_category(
                status,
                alternative_available=alternative is not None,
                quote_has_no_allowed_candidate_globally=quote_has_no_allowed_candidate_globally,
            ),
            "veto_reason_codes": list((row or {}).get("veto_reason_codes") or []),
            "alternative_available": alternative is not None,
            "alternative_image_basename": alternative[0] if alternative else None,
            "alternative_image_hash": alternative[2] if alternative else None,
            "alternative_score": alternative[1] if alternative else None,
            "score_delta_from_production_winner": alternative[1] - score if alternative else None,
            "alternative_basis": "logged_top_candidates" if alternative else None,
            "quote_has_no_allowed_candidate_globally": quote_has_no_allowed_candidate_globally,
            "manifest_policy_version": runtime.policy_version,
            "manifest_sha256": runtime.manifest_sha256,
            "production_selection_changed": False,
        })
        pending_quote = pending_image = None
        pending_candidates = []
    summary = summarise_events(observed)
    summary.update({
        "schema_version": 1,
        "generated_at": utc_now(),
        "window_days": since_days,
        "total_selections": len(observed),
        "in_scope_original_selections": sum(row["image_source"] != "generated" for row in observed),
        "generated_out_of_scope_selections": sum(row["image_source"] == "generated" for row in observed),
        "observations": observed,
        "observational_only": True,
        "network_calls": 0,
    })
    return summary


def replay_markdown(value: dict[str, Any]) -> str:
    """Replay markdown."""
    lines = [
        "# Quote/image material-veto shadow replay",
        "",
        f"Window: trailing {value['window_days']} days",
        f"Selections: **{value['total_selections']}**",
        f"In-scope originals: **{value['in_scope_original_selections']}**",
        f"Generated out of scope: **{value['generated_out_of_scope_selections']}**",
        f"Allowed / vetoed / unknown: **{value['allowed']} / {value['vetoed']} / {value['unknown']}**",
        f"Veto reasons: `{json.dumps(value['veto_reason_counts'], sort_keys=True)}`",
        "",
        "This is a network-free observational replay. No production choice or state was changed.",
    ]
    vetoed = [row for row in value.get("observations", []) if row.get("shadow_status") == "veto"]
    if vetoed:
        lines.extend(["", "## Highest-impact observed vetoes", "", "| Quote ID | Production | Reasons | Allowed alternative | Score delta |", "|---|---|---|---|---:|"])
        ranked = sorted(
            vetoed,
            key=lambda row: abs(float(row.get("score_delta_from_production_winner") or 0.0)),
            reverse=True,
        )
        for row in ranked[:5]:
            reasons = ", ".join(row.get("veto_reason_codes") or []) or "unavailable"
            delta = row.get("score_delta_from_production_winner")
            lines.append(
                f"| `{row.get('quote_id', '')}` | `{row.get('image_basename', '')}` | {reasons} | "
                f"`{row.get('alternative_image_basename') or 'none'}` | "
                f"{f'{float(delta):.2f}' if delta is not None else 'unavailable'} |"
            )
    return "\n".join(lines) + "\n"


def shadow_preflight(project_dir: Path, manifest_path: Path) -> dict[str, Any]:
    """Validate a shadow manifest and its source hashes without network access."""
    config = {
        "enabled": True, "mode": "shadow", "manifest_path": str(manifest_path),
        "fail_open": True, "record_best_allowed_alternative": True, "maximum_shadow_history": 10_000,
    }
    runtime = ShadowRuntime.load(project_dir, config, verify_source_hashes=True, enable_history=False)
    result = {
        "valid": runtime.available,
        "status": runtime.status if not runtime.available else "ready",
        "reason": runtime.reason,
        "manifest_path": str(runtime.manifest_path),
        "manifest_sha256": runtime.manifest_sha256,
        "policy_version": runtime.policy_version,
        "load_time_ms": round(runtime.load_time_ms, 3),
        "memory_bytes": runtime.memory_bytes,
        "source_file_hashes": _read_object(runtime.manifest_path).get("source_file_hashes", {}),
        "network_calls": 0,
        "active_enforcement": False,
    }
    if not runtime.available:
        raise ShadowManifestError(runtime.reason)
    return result


def shadow_status(project_dir: Path) -> dict[str, Any]:
    """Summarise local semantic-veto shadow configuration and observations."""
    local_path = project_dir / "mrsMThatcher.local.json"
    try:
        local = _read_object(local_path)
    except Exception:
        local = {}
    config = local.get("quote_image_semantic_veto") if isinstance(local, dict) else None
    enabled = isinstance(config, dict) and config.get("enabled") is True and config.get("mode") == "shadow"
    history = read_shadow_history(project_dir / RUNTIME_DIR_NAME)
    manifest_path = str((config or {}).get("manifest_path") or DEFAULT_MANIFEST)
    manifest = Path(manifest_path)
    if not manifest.is_absolute():
        manifest = project_dir / manifest
    validity: dict[str, Any]
    try:
        value = _read_object(manifest)
        audit = validate_compiled_manifest(value)
        configured_manifest_sha256 = sha256_file(manifest)
        configured_policy_version = str(value.get("policy_version") or "unavailable")
        validity = {
            "valid": True,
            **audit,
            "sha256": configured_manifest_sha256,
            "source_file_hashes": value.get("source_file_hashes", {}),
        }
    except Exception as exc:
        configured_manifest_sha256 = None
        configured_policy_version = None
        validity = {"valid": False, "reason": f"{type(exc).__name__}: {exc}"}
    summary = summarise_events(
        history,
        current_manifest_sha256=configured_manifest_sha256,
        current_policy_version=configured_policy_version,
    )
    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "feature_enabled": enabled,
        "configured_mode": str((config or {}).get("mode") or "disabled"),
        "manifest_path": str(manifest),
        "manifest": validity,
        **summary,
        "observation_progress": {
            "toward_100": min(int(summary["events"]), 100),
            "toward_200": min(int(summary["events"]), 200),
        },
        "active_enforcement": False,
        "network_calls": 0,
    }
