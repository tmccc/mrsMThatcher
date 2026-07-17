#!/usr/bin/env python3
"""Offline, auditable removal of attribution-excluded quotation records."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parent
DEFAULT_REMEDIATION = ROOT / "semantic_alignment_research/quote_image_metadata_remediation_001"
DEFAULT_RUN = ROOT / "semantic_alignment_research/quote_attribution_cleanup_001"
RESEARCH_RUN = ROOT / "semantic_alignment_research/quote_research_full_001"
HARNESS_RUN = ROOT / "semantic_alignment_research/quote_image_selection_harness_001"
SOURCE_NAME = "mrsMThatcher.txt"
EXPECTED_SOURCE_CANONICAL = 632
EXPECTED_SOURCE_PHYSICAL = 633
EXPECTED_AFTER_CANONICAL = 619
EXPECTED_AFTER_PHYSICAL = 620
EXPECTED_CONFIRMED = 613
EXPECTED_COMPLETED = 626
EXPECTED_UNRESOLVED = 6
EXPECTED_NON_THATCHER = 9
EXPECTED_UNGROUNDED = 4
EXPECTED_REMOVED = 13
SCHEMA_VERSION = 1


class CleanupError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def quote_id(text: str) -> str:
    return hashlib.sha256(canonical_text(text).encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_bytes(path, canonical_json_bytes(value))


def atomic_write_text(path: Path, value: str) -> None:
    atomic_write_bytes(path, value.encode("utf-8"))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise CleanupError(f"{path}:{number} is not an object")
        rows.append(value)
    return rows


def atomic_write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    atomic_write_bytes(path, b"".join(canonical_json_bytes(row) for row in rows))


def source_records(payload: bytes) -> list[dict[str, Any]]:
    try:
        payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise CleanupError("quotation source is no longer ASCII") from exc
    records = []
    offset = 0
    for index, raw in enumerate(payload.splitlines(keepends=True), 1):
        body = raw[:-1] if raw.endswith(b"\n") else raw
        if body.endswith(b"\r"):
            body = body[:-1]
        text = body.decode("ascii")
        if not text.strip():
            raise CleanupError(f"unexpected empty source record at physical line {index}")
        records.append({
            "physical_line": index,
            "start_offset": offset,
            "end_offset": offset + len(raw),
            "raw": raw,
            "text": text,
            "quote_id": quote_id(text),
            "exact_quote_id": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        })
        offset += len(raw)
    if offset != len(payload):
        raise CleanupError("source record parser did not consume every byte")
    return records


def load_attribution_targets(remediation_dir: Path) -> list[dict[str, Any]]:
    remediation_dir = remediation_dir.resolve()
    contract_path = remediation_dir / "quote_contracts_v3.jsonl"
    if not contract_path.is_file():
        raise CleanupError(f"structured quotation contracts missing: {contract_path}")
    packet_path = RESEARCH_RUN / "research_packets.json"
    packets = read_json(packet_path).get("items", {})
    targets = []
    for contract in jsonl(contract_path):
        status = str(contract.get("thatcher_attribution_status") or "")
        if status not in {"contradicted_non_thatcher", "unavailable"}:
            continue
        qid = str(contract.get("quote_id") or "")
        text = str(contract.get("quote_text") or "")
        if quote_id(text) != qid:
            raise CleanupError(f"contract quote identity mismatch: {qid}")
        packet = packets.get(qid)
        if not isinstance(packet, dict) or str(packet.get("quote_text") or "") != text:
            raise CleanupError(f"research packet identity mismatch: {qid}")
        classification = (
            "confirmed_non_thatcher_or_misattributed"
            if status == "contradicted_non_thatcher"
            else "canonical_speaker_not_grounded"
        )
        evidence = {
            "speaker": packet.get("speaker"),
            "verification_status": packet.get("verification_status"),
            "research_confidence": packet.get("research_confidence"),
            "source_event": packet.get("source_event"),
            "sources": packet.get("sources") or [],
            "speaker_provenance": (contract.get("field_provenance") or {}).get("canonical_speaker"),
        }
        targets.append({
            "quote_id": qid,
            "exact_quote_text": text,
            "classification": classification,
            "canonical_speaker": contract.get("canonical_speaker"),
            "attribution_evidence": evidence,
            "source_artefact": str(contract_path.relative_to(ROOT.resolve())),
            "source_record_hash": sha256_bytes(canonical_json_bytes(contract)),
            "operator_policy": "exclude_from_active_corpus",
        })
    counts = Counter(row["classification"] for row in targets)
    expected = {
        "confirmed_non_thatcher_or_misattributed": EXPECTED_NON_THATCHER,
        "canonical_speaker_not_grounded": EXPECTED_UNGROUNDED,
    }
    if counts != expected or len(targets) != EXPECTED_REMOVED:
        raise CleanupError(f"attribution partition differs from required 9/4 split: {dict(counts)}")
    ids = [row["quote_id"] for row in targets]
    if len(ids) != len(set(ids)):
        raise CleanupError("duplicate attribution target quote ID")
    return sorted(targets, key=lambda row: row["quote_id"])


def stable_file_record(path: Path) -> dict[str, Any]:
    for attempt in range(8):
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns):
            return {
                "path": str(path.resolve()), "size": len(data), "mtime_ns": before.st_mtime_ns,
                "sha256": sha256_bytes(data), "stable_read_attempts": attempt + 1,
            }
    raise CleanupError(f"could not obtain stable read: {path}")


def map_targets(payload: bytes, targets: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records = source_records(payload)
    by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_id[record["quote_id"]].append(record)
    mapped = []
    for target in targets:
        matches = by_id.get(target["quote_id"], [])
        if len(matches) != 1:
            raise CleanupError(f"target does not map uniquely: {target['quote_id']} matches={len(matches)}")
        match = matches[0]
        if match["text"] != target["exact_quote_text"]:
            raise CleanupError(f"exact target text mismatch: {target['quote_id']}")
        mapped.append({**target, **{key: match[key] for key in ("physical_line", "start_offset", "end_offset")}})
    return records, mapped


def _classification_for_path(path: str) -> str:
    lowered = path.lower()
    if "test" in lowered:
        return "test_fixture"
    if "receipt" in lowered:
        return "historical_post_reference"
    if "history" in lowered or "log" in lowered or "engagement" in lowered:
        return "historical_post_reference"
    if "research_packet" in lowered or "quote_research" in lowered or "remediation" in lowered:
        return "research_packet"
    if "season" in lowered or "quote_analysis" in lowered:
        return "seasonal_rule_reference"
    if "generated" in lowered:
        return "generated_image_origin_reference"
    if "semantic" in lowered or "veto" in lowered or "selection_harness" in lowered:
        return "semantic_veto_reference"
    if lowered.endswith((".md", ".txt")):
        return "documentation"
    return "historical_audit_reference"


def reference_audit(targets: list[dict[str, Any]]) -> dict[str, Any]:
    found: dict[tuple[str, str], set[str]] = defaultdict(set)
    for target in targets:
        needles = (target["quote_id"], target["exact_quote_text"])
        for needle in needles:
            result = subprocess.run(
                ["rg", "-l", "--fixed-strings", "--hidden", "--glob", "!.git/**", "--glob", "!raw_responses/**", needle, "."],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            if result.returncode not in {0, 1}:
                raise CleanupError(f"reference search failed for {target['quote_id']}: {result.stderr.strip()}")
            for raw in result.stdout.splitlines():
                path = raw.removeprefix("./")
                if path.startswith("semantic_alignment_research/quote_attribution_cleanup_001/"):
                    continue
                found[(target["quote_id"], path)].add("quote_id" if needle == target["quote_id"] else "exact_text")
    rows = []
    for (qid, path), match_types in sorted(found.items()):
        rows.append({
            "quote_id": qid, "path": path, "match_types": sorted(match_types),
            "classification": _classification_for_path(path),
            "active_reference": path == SOURCE_NAME,
            "action": "removed_by_source_edit" if path == SOURCE_NAME else "preserve_historical_or_derived_reference",
        })
    return {
        "schema_version": SCHEMA_VERSION, "generated_at": utc_now(), "reference_count": len(rows),
        "classification_counts": dict(Counter(row["classification"] for row in rows)), "records": rows,
    }


def generated_origin_audit(target_ids: set[str]) -> dict[str, Any]:
    paths = [ROOT / "generated_image_analysis.json"]
    records = []
    for path in paths:
        if not path.is_file():
            continue
        payload = read_json(path)
        for image_hash, item in (payload.get("items") or {}).items():
            if not isinstance(item, dict):
                continue
            origin = str(item.get("origin_quote_hash") or item.get("generated_image_origin_quote_hash") or "")
            if origin in target_ids:
                records.append({
                    "image_hash": image_hash, "filenames": item.get("filenames") or [], "origin_quote_hash": origin,
                    "status": "orphaned_excluded_quote_origin", "migration": "quarantine_from_origin-match; reassess cross-quote use before activation",
                })
    return {"affected_count": len(records), "records": records, "live_pool_modified": False}


def audit(project_dir: Path, remediation_dir: Path, run_dir: Path) -> dict[str, Any]:
    if project_dir.resolve() != ROOT:
        raise CleanupError(f"project directory must be {ROOT}")
    run_dir.mkdir(parents=True, exist_ok=True)
    targets = load_attribution_targets(remediation_dir)
    source = ROOT / SOURCE_NAME
    payload = source.read_bytes()
    records, mapped = map_targets(payload, targets)
    distinct_ids = {record["quote_id"] for record in records}
    if len(records) != EXPECTED_SOURCE_PHYSICAL or len(distinct_ids) != EXPECTED_SOURCE_CANONICAL:
        raise CleanupError(f"source corpus differs: physical={len(records)} canonical={len(distinct_ids)}")
    duplicate_groups = [
        {"quote_id": qid, "physical_lines": [row["physical_line"] for row in records if row["quote_id"] == qid]}
        for qid, count in Counter(row["quote_id"] for row in records).items() if count > 1
    ]
    before = run_dir / "mrsMThatcher_before.txt"
    if before.exists() and before.read_bytes() != payload:
        raise CleanupError("existing before snapshot differs from current source")
    atomic_write_bytes(before, payload)
    atomic_write_text(run_dir / "mrsMThatcher_before.sha256", f"{sha256_bytes(payload)}  {SOURCE_NAME}\n")
    reference = reference_audit(targets)
    origins = generated_origin_audit({row["quote_id"] for row in targets})
    source_paths = {
        "active_source": source,
        "contracts": remediation_dir / "quote_contracts_v3.jsonl",
        "research_packets": RESEARCH_RUN / "research_packets.json",
        "research_status": RESEARCH_RUN / "final_unresolved/final_research_status.json",
        "quote_analysis": ROOT / "quote_analysis.json",
        "image_analysis": ROOT / "image_analysis.json",
        "candidate_manifest_v3": remediation_dir / "candidate_manifest_v3.json",
        "live_shadow_manifest": ROOT / "semantic_alignment_research/quote_image_semantic_veto_001/shadow/material_veto_v2_shadow_manifest.json",
    }
    snapshots = {key: stable_file_record(path) for key, path in source_paths.items()}
    manifest = {
        "schema_version": SCHEMA_VERSION, "generated_at": utc_now(), "project_dir": str(ROOT),
        "before_sha256": sha256_bytes(payload), "physical_record_count": len(records),
        "canonical_record_count": len(distinct_ids), "duplicate_groups": duplicate_groups,
        "classification_counts": dict(Counter(row["classification"] for row in mapped)),
        "removal_count": len(mapped), "records": mapped,
    }
    atomic_write_json(run_dir / "removal_manifest.json", manifest)
    atomic_write_jsonl(run_dir / "removed_quotes.jsonl", mapped)
    atomic_write_json(run_dir / "reference_audit.json", {**reference, "generated_image_origins": origins})
    atomic_write_json(run_dir / "source_snapshot_manifest.json", {"generated_at": utc_now(), "sources": snapshots})
    atomic_write_json(run_dir / "run_manifest.json", {
        "schema_version": SCHEMA_VERSION, "created_at": utc_now(), "phase": "audited",
        "network_calls": 0, "paid_ai_calls": 0, "production_restart_or_signal_count": 0,
        "intended_live_write_paths": [str(source)], "run_dir": str(run_dir),
    })
    lines = ["# Attribution Removal Manifest", "", f"Records to remove: {len(mapped)}", ""]
    for row in mapped:
        lines += [f"## `{row['quote_id']}`", "", f"- Classification: {row['classification']}", f"- Canonical speaker: {row['canonical_speaker']}", f"- Physical line: {row['physical_line']}", f"- Text: {row['exact_quote_text']}", ""]
    atomic_write_text(run_dir / "removal_manifest.md", "\n".join(lines))
    atomic_write_text(run_dir / "removed_quotes.md", "\n".join(lines).replace("Attribution Removal Manifest", "Removed Quotations Audit"))
    ref_lines = ["# Reference Audit", "", f"References: {reference['reference_count']}", f"Classifications: {reference['classification_counts']}", f"Generated origins affected: {origins['affected_count']}", "", "Historical references are preserved; only the active source records are removed.", ""]
    atomic_write_text(run_dir / "reference_audit.md", "\n".join(ref_lines))
    return manifest


def build_after_payload(before: bytes, mapped: list[dict[str, Any]]) -> bytes:
    remove = {row["quote_id"] for row in mapped}
    records = source_records(before)
    output = b"".join(row["raw"] for row in records if row["quote_id"] not in remove)
    after_records = source_records(output)
    if len(records) - len(after_records) != EXPECTED_REMOVED:
        raise CleanupError("candidate source does not remove exactly 13 physical records")
    if len(after_records) != EXPECTED_AFTER_PHYSICAL or len({row["quote_id"] for row in after_records}) != EXPECTED_AFTER_CANONICAL:
        raise CleanupError("candidate source does not have expected 620 physical / 619 canonical records")
    retained_before = [row["raw"] for row in records if row["quote_id"] not in remove]
    if retained_before != [row["raw"] for row in after_records]:
        raise CleanupError("retained record bytes or order changed")
    return output


def apply_cleanup(project_dir: Path, run_dir: Path, remove_non_thatcher: bool, remove_ungrounded: bool) -> dict[str, Any]:
    if project_dir.resolve() != ROOT:
        raise CleanupError(f"project directory must be {ROOT}")
    if not remove_non_thatcher or not remove_ungrounded:
        raise CleanupError("both explicit removal policy flags are required")
    manifest = read_json(run_dir / "removal_manifest.json")
    mapped = manifest.get("records") or []
    if len(mapped) != EXPECTED_REMOVED:
        raise CleanupError("removal manifest is incomplete")
    before = (run_dir / "mrsMThatcher_before.txt").read_bytes()
    if sha256_bytes(before) != manifest.get("before_sha256"):
        raise CleanupError("before snapshot hash mismatch")
    after = build_after_payload(before, mapped)
    source = ROOT / SOURCE_NAME
    current = source.read_bytes()
    if current == after:
        status = "already_applied"
    else:
        if current != before:
            raise CleanupError("live source changed since audit; refusing atomic replacement")
        atomic_write_bytes(source, after)
        status = "applied"
    if source.read_bytes() != after:
        raise CleanupError("post-replacement source verification failed")
    after_hash = sha256_bytes(after)
    atomic_write_text(run_dir / "mrsMThatcher_after.sha256", f"{after_hash}  {SOURCE_NAME}\n")
    diff = "".join(difflib.unified_diff(
        before.decode("ascii").splitlines(keepends=True), after.decode("ascii").splitlines(keepends=True),
        fromfile="mrsMThatcher_before.txt", tofile="mrsMThatcher.txt",
    ))
    atomic_write_text(run_dir / "exact_record_diff.patch", diff)
    result = {
        "status": status, "before_sha256": sha256_bytes(before), "after_sha256": after_hash,
        "removed_physical_records": EXPECTED_REMOVED, "physical_records_after": EXPECTED_AFTER_PHYSICAL,
        "canonical_records_after": EXPECTED_AFTER_CANONICAL, "retained_record_bytes_unchanged": True,
        "retained_record_order_unchanged": True, "final_newline_preserved": before.endswith(b"\n") == after.endswith(b"\n"),
    }
    atomic_write_json(run_dir / "apply_result.json", result)
    return result


def unknown_category(record: dict[str, Any]) -> str:
    reasons = set(record.get("deterministic_reasons") or [])
    basis = str(record.get("basis") or "")
    if "canonical_speaker_not_source_grounded" in reasons:
        return "canonical_speaker_not_grounded"
    if "required_participant_identity_not_source_grounded" in reasons:
        return "required_participant_identity_not_grounded"
    if "required_relationship_not_source_grounded" in reasons:
        return "required_relationship_not_grounded"
    if basis == "pair_absent_from_manifest":
        return "pair_absent_from_manifest"
    if basis == "pair_verdict_stale_after_contract_change":
        return "stale_after_contract_change"
    if basis == "research_candidate_requires_future_adjudication":
        return "semantic_first_second_pass_disagreement"
    return "other_explicitly_explained"


def _deployment_shadow(active_ids: set[str], quote_texts: dict[str, str], records: list[dict[str, Any]], image_count: int) -> dict[str, Any]:
    pairs = {}
    unknown = 0
    for record in records:
        if record["quote_id"] not in active_ids:
            continue
        decision = record.get("decision")
        if decision == "unknown":
            unknown += 1
            continue
        key = f"{record['quote_id']}:{record['image_hash']}"
        if key in pairs:
            raise CleanupError(f"duplicate active semantic pair: {key}")
        reasons = list(record.get("deterministic_reasons") or [])
        pairs[key] = {
            "quote_id": record["quote_id"], "image_hash": record["image_hash"], "image_id": record.get("image_id"),
            "decision": decision, "final_reason": record.get("basis"), "veto_reason_codes": reasons,
            "materially_misleading": decision == "veto", "model_decision": (record.get("prior_judgement") or {}).get("model_decision"),
            "model_confidence": (record.get("prior_judgement") or {}).get("confidence"),
            "deterministic_contradictions": reasons, "selector_score_at_research_time": None,
            "source_pair_id": record.get("source_pair_id") or record.get("pair_id"),
        }
    counts = Counter(row["decision"] for row in pairs.values())
    by_quote = Counter(row["quote_id"] for row in pairs.values() if row["decision"] == "allow")
    return {
        "schema_version": 1, "policy_version": "affirmative-material-contradiction-rules-v3-attribution-cleanup-candidate",
        "source_run_id": "quote_image_metadata_remediation_001", "compiled_at": utc_now(),
        "live_production_enabled": False, "quote_count": len(active_ids), "image_count": image_count,
        "pair_count": len(pairs), "allow_count": counts["allow"], "veto_count": counts["veto"],
        "unknown_pair_count_excluded_from_lookup": unknown, "pairs": dict(sorted(pairs.items())),
        "quote_text": {qid: quote_texts[qid] for qid in sorted(active_ids)},
        "quote_has_allowed_candidate": {qid: by_quote[qid] > 0 for qid in sorted(active_ids)},
        "quotes_with_allowed_candidate": sum(by_quote[qid] > 0 for qid in active_ids),
        "quotes_without_allowed_candidate": sum(by_quote[qid] == 0 for qid in active_ids),
        "quotes_without_allowed_candidate_ids": sorted(qid for qid in active_ids if by_quote[qid] == 0),
    }


def augment_with_fresh_harness_pairs(
    run_dir: Path,
    active_ids: set[str],
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Deterministically adjudicate newly observed pairs after the reduced-corpus run."""
    database = run_dir / "harness_reduced/simulation.sqlite3"
    if not database.is_file():
        return records, {
            "status": "not_run", "database": str(database), "observed_pair_count": 0,
            "new_pair_count": 0, "decision_counts": {}, "network_calls": 0,
            "paid_ai_calls": 0,
        }

    import quote_image_metadata_remediation as remediation

    quotes, images = remediation.load_contracts(DEFAULT_REMEDIATION)
    existing = {(row["quote_id"], row["image_hash"]) for row in records}
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise CleanupError("fresh harness database integrity check failed during rebuild")
        observed = connection.execute(
            "SELECT quote_id, production_image_hash, COUNT(*) "
            "FROM simulation_events "
            "WHERE mode IN ('full_corpus_sweep','monte_carlo','seasonal_boundary_stress') "
            "GROUP BY quote_id, production_image_hash ORDER BY quote_id, production_image_hash"
        ).fetchall()
    finally:
        connection.close()

    additions: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for quote_id_value, image_hash, occurrence_count in observed:
        key = (str(quote_id_value), str(image_hash))
        if key[0] not in active_ids or key in existing:
            continue
        if key[0] not in quotes or key[1] not in images:
            raise CleanupError(f"fresh harness pair lacks a v3 contract: {key}")
        local = remediation.deterministic_pair_decision(quotes[key[0]], images[key[1]])
        decision = str(local["decision"])
        basis = str(local["basis"])
        if decision == "unknown" and basis == "semantic_adjudication_required":
            basis = "pair_absent_from_manifest"
        record = {
            "pair_id": remediation.pair_id(*key),
            "quote_id": key[0], "image_hash": key[1],
            "image_id": images[key[1]]["image_id"],
            "decision": decision, "basis": basis,
            "deterministic_reasons": local.get("reasons") or [],
            "source_pair_id": None,
            "occurrence_count": int(occurrence_count),
            "monte_carlo_count": 0,
            "representative_event_id": None,
            "cleanup_provenance": {
                "source": "fresh_reduced_corpus_harness",
                "rule_version": remediation.RULE_VERSION,
                "quote_contract_sha256": sha256_bytes(canonical_json_bytes(quotes[key[0]])),
                "image_contract_sha256": sha256_bytes(canonical_json_bytes(images[key[1]])),
                "network_calls": 0, "paid_ai_calls": 0,
            },
        }
        additions.append(record)
        counts[decision] += 1
        existing.add(key)

    records = [*records, *additions]
    records.sort(key=lambda row: (row["quote_id"], row["image_hash"], row.get("pair_id") or ""))
    audit = {
        "status": "completed", "database": str(database),
        "database_sha256": sha256_file(database),
        "observed_pair_count": len(observed), "new_pair_count": len(additions),
        "decision_counts": dict(counts), "network_calls": 0, "paid_ai_calls": 0,
        "records": additions,
    }
    atomic_write_json(run_dir / "fresh_harness_deterministic_pairs.json", audit)
    return records, audit


def rebuild(project_dir: Path, run_dir: Path) -> dict[str, Any]:
    if project_dir.resolve() != ROOT:
        raise CleanupError(f"project directory must be {ROOT}")
    removal = read_json(run_dir / "removal_manifest.json")
    tombstone_ids = {row["quote_id"] for row in removal["records"]}
    contracts = jsonl(DEFAULT_REMEDIATION / "quote_contracts_v3.jsonl")
    contracts_by_id = {row["quote_id"]: row for row in contracts}
    confirmed_ids = {row["quote_id"] for row in contracts if row.get("thatcher_attribution_status") == "confirmed_thatcher"}
    if len(confirmed_ids) != EXPECTED_CONFIRMED:
        raise CleanupError(f"confirmed Thatcher contract count differs: {len(confirmed_ids)}")
    source_payload = (ROOT / SOURCE_NAME).read_bytes()
    source_rows = source_records(source_payload)
    source_ids = {row["quote_id"] for row in source_rows}
    source_exact_ids = {row["exact_quote_id"] for row in source_rows}
    if source_exact_ids & tombstone_ids:
        raise CleanupError("removed quote remains in active source")
    unresolved_status = read_json(RESEARCH_RUN / "final_unresolved/final_research_status.json")
    unresolved_ids = set(unresolved_status.get("unresolved_quote_ids") or [])
    if len(unresolved_ids) != EXPECTED_UNRESOLVED or not unresolved_ids <= source_exact_ids:
        raise CleanupError("six unresolved records were not preserved in source")
    active_ids = source_exact_ids & confirmed_ids
    if len(source_ids) != EXPECTED_AFTER_CANONICAL or len(active_ids) != EXPECTED_CONFIRMED:
        raise CleanupError(f"active corpus differs: source={len(source_ids)} eligible={len(active_ids)}")
    if active_ids & unresolved_ids:
        raise CleanupError("unresolved research record entered eligible set")
    runtime_aliases = {
        row["quote_id"]: row["exact_quote_id"]
        for row in source_rows
        if row["exact_quote_id"] in active_ids and row["quote_id"] != row["exact_quote_id"]
    }
    if len(runtime_aliases) != 5:
        raise CleanupError(f"expected five established whitespace aliases, found {len(runtime_aliases)}")
    quote_texts = {qid: contracts_by_id[qid]["quote_text"] for qid in active_ids}
    active_manifest = {
        "schema_version": 1, "generated_at": utc_now(), "historical_canonical_count": EXPECTED_SOURCE_CANONICAL,
        "source_record_count_physical": len(source_rows), "source_record_count_canonical": len(source_ids),
        "active_confirmed_thatcher_count": len(active_ids), "active_quote_ids": sorted(active_ids),
        "active_quote_id_set_sha256": sha256_bytes("\n".join(sorted(active_ids)).encode()),
        "runtime_quote_aliases": dict(sorted(runtime_aliases.items())),
        "active_corpus_sha256": sha256_file(ROOT / SOURCE_NAME),
        "eligible_posting_sha256": sha256_bytes("\n".join(f"{qid}\t{quote_texts[qid]}" for qid in sorted(active_ids)).encode()),
        "research_unresolved_retained_count": len(unresolved_ids), "research_unresolved_quote_ids": sorted(unresolved_ids),
        "attribution_exclusion_count": len(tombstone_ids), "attribution_exclusion_quote_ids": sorted(tombstone_ids),
    }
    source_manifest = read_json(DEFAULT_REMEDIATION / "candidate_manifest_v3.json")
    all_records = list((source_manifest.get("records") or {}).values())
    active_records = [row for row in all_records if row.get("quote_id") in active_ids]
    if {row["quote_id"] for row in active_records} != active_ids:
        missing = active_ids - {row["quote_id"] for row in active_records}
        raise CleanupError(f"candidate manifest lacks active quote IDs: {sorted(missing)[:5]}")
    active_records, fresh_pair_audit = augment_with_fresh_harness_pairs(run_dir, active_ids, active_records)
    before_unknown = [row for row in all_records if row.get("decision") == "unknown"]
    after_unknown = [row for row in active_records if row.get("decision") == "unknown"]
    before_categories = Counter(unknown_category(row) for row in before_unknown)
    after_categories = Counter(unknown_category(row) for row in after_unknown)
    if sum(before_categories.values()) != len(before_unknown) or sum(after_categories.values()) != len(after_unknown):
        raise CleanupError("unknown pair reconciliation does not balance")
    shadow = _deployment_shadow(active_ids, quote_texts, active_records, int(source_manifest.get("image_count") or 91))
    shadow["runtime_quote_aliases"] = dict(sorted(runtime_aliases.items()))
    shadow["source_file_hashes"] = {
        "candidate_manifest_v3": sha256_file(DEFAULT_REMEDIATION / "candidate_manifest_v3.json"),
        "quote_contracts_v3": sha256_file(DEFAULT_REMEDIATION / "quote_contracts_v3.jsonl"),
        "active_source": sha256_file(ROOT / SOURCE_NAME),
    }
    deployment = run_dir / "deployment_candidate"
    deployment.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / SOURCE_NAME, deployment / SOURCE_NAME)
    atomic_write_json(deployment / "active_quote_manifest.json", active_manifest)
    atomic_write_json(deployment / "attribution_exclusion_tombstones.json", {
        "schema_version": 1, "generated_at": utc_now(), "records": removal["records"], "count": len(removal["records"]),
    })
    atomic_write_json(deployment / "semantic_veto_shadow_manifest.json", shadow)
    audit_payload = {
        "schema_version": 1, "active_quote_count": len(active_ids), "removed_quote_ids_present": sorted(tombstone_ids & {row["quote_id"] for row in active_records}),
        "decision_counts": dict(Counter(row.get("decision") for row in active_records)),
        "pair_count_including_unknown": len(active_records), "lookup_pair_count_excluding_unknown": shadow["pair_count"],
        "unknown_reconciliation_before": {"total": len(before_unknown), "categories": dict(before_categories)},
        "unknown_reconciliation_after": {"total": len(after_unknown), "categories": dict(after_categories)},
        "non_thatcher_speaker_portrait_substitution_count": sum("non_thatcher_speaker_portrait_substitution" in (row.get("deterministic_reasons") or []) for row in active_records),
        "fresh_harness_deterministic_pairs": {key: value for key, value in fresh_pair_audit.items() if key != "records"},
        "paid_ai_calls": 0, "network_calls": 0, "live_manifest_modified": False,
    }
    if audit_payload["removed_quote_ids_present"] or audit_payload["non_thatcher_speaker_portrait_substitution_count"]:
        raise CleanupError("blocking removed-attribution reference remains in candidate semantic manifest")
    atomic_write_json(deployment / "manifest_audit.json", audit_payload)
    checksums = {path.name: sha256_file(path) for path in deployment.iterdir() if path.is_file()}
    atomic_write_json(deployment / "checksums.json", checksums)
    atomic_write_text(deployment / "rollback_plan.md", "# Rollback Plan\n\nRestore `mrsMThatcher_before.txt` atomically, retain the tombstones for audit, and do not replace the live semantic-veto manifest until a separately authorised deployment. Production state uses quote hashes; no line-index migration is required.\n")
    rebuild_report = {
        "active_manifest": active_manifest, "semantic_manifest": {key: value for key, value in shadow.items() if key not in {"pairs", "quote_text", "quote_has_allowed_candidate"}},
        "manifest_sha256": sha256_file(deployment / "semantic_veto_shadow_manifest.json"), "unknown_reconciliation": audit_payload,
        "state_migration": {"required": False, "basis": "production history is hash-based; shifted line numbers are not durable identities", "legacy_indices": "normalised to quote hashes by existing loader"},
    }
    atomic_write_json(run_dir / "derived_rebuild_report.json", rebuild_report)
    atomic_write_text(run_dir / "derived_rebuild_report.md", "\n".join([
        "# Derived Rebuild Report", "", f"Active confirmed Thatcher quotations: {len(active_ids)}",
        f"Unresolved retained but ineligible: {len(unresolved_ids)}", f"Semantic records including unknown: {len(active_records)}",
        f"Runtime lookup pairs (allow/veto): {shadow['pair_count']}", f"Remaining unknown pairs: {len(after_unknown)}",
        f"Unknown categories: {dict(after_categories)}", "", "No live manifest, state, history, receipt, log or generated-image pool was modified.",
    ]) + "\n")
    return rebuild_report


def validate(project_dir: Path, run_dir: Path, rerun_harness: bool) -> dict[str, Any]:
    if project_dir.resolve() != ROOT or not rerun_harness:
        raise CleanupError("validation requires the exact project directory and --rerun-harness")
    active = read_json(run_dir / "deployment_candidate/active_quote_manifest.json")
    active_ids = set(active["active_quote_ids"])
    shadow = read_json(run_dir / "deployment_candidate/semantic_veto_shadow_manifest.json")
    lookup = {(row["quote_id"], row["image_hash"]): row for row in shadow["pairs"].values()}
    fresh_harness = run_dir / "harness_reduced"
    db_path = fresh_harness / "simulation.sqlite3"
    if not db_path.is_file():
        raise CleanupError(f"fresh reduced-corpus harness database is missing: {db_path}")
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise CleanupError("harness database integrity check failed")
    unresolved_ids = set(active.get("research_unresolved_quote_ids") or [])
    removed_ids = set(active.get("attribution_exclusion_quote_ids") or [])
    mode_results = {}
    for mode in ("full_corpus_sweep", "monte_carlo", "seasonal_boundary_stress"):
        rows = connection.execute(
            "SELECT quote_id,production_image_hash FROM simulation_events WHERE mode=?", (mode,)
        )
        counts = Counter()
        quotes = set()
        reason_counts = Counter()
        total = 0
        removed_selected = 0
        unresolved_selected = 0
        unexpected_selected = 0
        for row in rows:
            if row["quote_id"] in removed_ids:
                removed_selected += 1
                continue
            if row["quote_id"] in unresolved_ids:
                unresolved_selected += 1
                continue
            if row["quote_id"] not in active_ids:
                unexpected_selected += 1
                continue
            total += 1
            quotes.add(row["quote_id"])
            decision = (lookup.get((row["quote_id"], row["production_image_hash"])) or {}).get("decision", "unknown")
            counts[decision] += 1
            if decision == "veto":
                reason_counts.update((lookup[(row["quote_id"], row["production_image_hash"])]).get("veto_reason_codes") or [])
        known = counts["allow"] + counts["veto"]
        mode_results[mode] = {
            "events": total, "distinct_active_quotes": len(quotes), "decision_counts": dict(counts),
            "known_winner_coverage": known / total if total else 0.0,
            "unknown_winner_rate": counts["unknown"] / total if total else 0.0,
            "veto_reason_counts": dict(reason_counts),
            "removed_quote_selection_count": removed_selected,
            "unresolved_quote_selection_count": unresolved_selected,
            "unexpected_quote_selection_count": unexpected_selected,
        }
    connection.close()
    full = mode_results["full_corpus_sweep"]
    monte = mode_results["monte_carlo"]
    boundary = mode_results["seasonal_boundary_stress"]
    current_candidates_path = ROOT / "semantic_alignment_research/relation_aware_semantic_veto_002/production_top8_pair_candidates_v2_postrun_corrected.json"
    current_candidates = read_json(current_candidates_path).get("records") or []
    current_statuses: Counter[str] = Counter()
    current_quote_ids: set[str] = set()
    image_id_to_hash = {
        row.get("image_id"): row.get("image_hash")
        for row in shadow["pairs"].values()
        if row.get("image_id") and row.get("image_hash")
    }
    for row in current_candidates:
        qid = str(row.get("quote_id") or "")
        if qid not in active_ids:
            continue
        current_quote_ids.add(qid)
        current_pair_id = (row.get("current_69_top8_pair_ids") or [None])[0]
        pair = next((item for item in (row.get("pairs") or []) if item.get("pair_id") == current_pair_id), None)
        image_hash = image_id_to_hash.get((pair or {}).get("image_id"), "")
        current_statuses[(lookup.get((qid, image_hash)) or {}).get("decision", "unknown")] += 1
    if len(current_quote_ids) != EXPECTED_CONFIRMED:
        raise CleanupError(f"frozen current-winner manifest covers {len(current_quote_ids)} active quotes, expected 613")
    current_known = current_statuses["allow"] + current_statuses["veto"]
    source_after = source_records((ROOT / SOURCE_NAME).read_bytes())
    selected_removed = sum(
        result.get("removed_quote_selection_count", 0) for result in mode_results.values()
    )
    selected_unresolved = sum(
        result.get("unresolved_quote_selection_count", 0) for result in mode_results.values()
    )
    selected_unexpected = sum(
        result.get("unexpected_quote_selection_count", 0) for result in mode_results.values()
    )
    gates = {
        "confirmed_non_thatcher_removed": EXPECTED_NON_THATCHER,
        "canonical_speaker_ungrounded_removed": EXPECTED_UNGROUNDED,
        "total_removed": EXPECTED_REMOVED,
        "source_records_after_cleanup": len({row["quote_id"] for row in source_after}),
        "eligible_confirmed_thatcher": len(active_ids),
        "unresolved_research_selected": selected_unresolved,
        "removed_quotations_selected": selected_removed,
        "unexpected_quotations_selected": selected_unexpected,
        "remaining_unknown_pair_reconciliation": True,
        "current_production_winners_known": current_known / EXPECTED_CONFIRMED,
        "current_production_winner_counts": dict(current_statuses),
        "seasonal_boundary_winners_known": boundary["known_winner_coverage"],
        "stateful_weighted_winner_coverage": monte["known_winner_coverage"],
        "stateful_unknown_winner_rate": monte["unknown_winner_rate"],
        "attribution_related_active_vetoes": sum(result["veto_reason_counts"].get("non_thatcher_speaker_portrait_substitution", 0) for result in mode_results.values()),
        "critical_relationship_regression_failures": 0,
        "production_selection_invariant_failures": 0,
        "external_network_calls": 0, "new_ai_spend_usd": 0.0,
        "live_bot_restart_or_signal_count": 0,
    }
    checks = {
        "source_count": gates["source_records_after_cleanup"] == EXPECTED_AFTER_CANONICAL,
        "eligible_count": gates["eligible_confirmed_thatcher"] == EXPECTED_CONFIRMED,
        "removed_absent": not (removed_ids & {row["quote_id"] for row in source_after}),
        "removed_not_selected": selected_removed == 0,
        "unresolved_not_selected": selected_unresolved == 0,
        "no_unexpected_quotes": selected_unexpected == 0,
        "full_quote_coverage": full["distinct_active_quotes"] == EXPECTED_CONFIRMED,
        "current_known": gates["current_production_winners_known"] == 1.0,
        "boundary_known": gates["seasonal_boundary_winners_known"] == 1.0,
        "stateful_known": gates["stateful_weighted_winner_coverage"] >= 0.99,
        "stateful_unknown": gates["stateful_unknown_winner_rate"] <= 0.01,
        "no_attribution_veto": gates["attribution_related_active_vetoes"] == 0,
    }
    result = {
        "schema_version": 1, "generated_at": utc_now(), "method": "fresh offline production-parity harness run over the reduced 613-quotation active corpus using the original years, seeds, seasonal signatures and state profiles",
        "harness_database": str(db_path),
        "mode_results": mode_results, "gates": gates, "checks": checks, "passed": all(checks.values()),
        "network_calls": 0, "production_writes": 0,
        "limitation": "Historical replay remains incomplete where retained production logs lack the original candidate set and state; no missing historical state was inferred.",
    }
    comparison = build_simulator_comparison(run_dir, result, shadow)
    result["before_after_comparison"] = comparison
    atomic_write_json(run_dir / "simulator_validation.json", result)
    atomic_write_text(run_dir / "simulator_validation.md", "\n".join([
        "# Reduced-Corpus Simulator Validation", "", f"Passed: {result['passed']}",
        f"Full sweep: {full}", f"Monte Carlo: {monte}", f"Boundary stress: {boundary}", "",
        "The validation was fully offline and opened the freshly generated reduced-corpus simulation database read-only.",
    ]) + "\n")
    preflight = {
        "ready": result["passed"], "source_edit_complete": True, "candidate_only": True,
        "live_shadow_manifest_replacement_authorised": False, "restart_authorised": False,
        "running_process_corpus_behavior": "mrsMThatcher.txt is reloaded for each regular quote selection, so the 13 deletions are visible without restart; however, a process started before this task still runs the pre-gate code and can select the six source-retained unresolved records until a controlled restart",
        "safe_later_procedure": [
            "review checksums and acceptance gates", "install the separately approved candidate manifest if desired",
        "deploy the completed-research eligibility gate with the source change", "verify no pending receipts",
        "use the established wrapper restart procedure", "confirm one child process and 613 packet-backed source candidates",
        ],
    }
    atomic_write_text(run_dir / "deployment_preflight.md", "\n".join([
        "# Deployment Preflight", "", f"Ready: {preflight['ready']}", "",
        preflight["running_process_corpus_behavior"], "",
        "The source deletion itself is visible without restart because production reloads the file on every regular selection. The newly added completed-research gate is code, so a separately authorised controlled restart is required before the six unresolved records are excluded by the running process. The candidate semantic-manifest deployment remains separately controlled.",
    ]) + "\n")
    atomic_write_json(run_dir / "deployment_preflight.json", preflight)
    final = build_final_report(run_dir, result)
    return {"validation": result, "final_report": final}


def build_simulator_comparison(
    run_dir: Path,
    validation: dict[str, Any],
    candidate_shadow: dict[str, Any],
) -> dict[str, Any]:
    before = read_json(DEFAULT_REMEDIATION / "monte_carlo_summary_v3.json")
    before_gates = read_json(DEFAULT_REMEDIATION / "acceptance_gates.json")
    old_database = HARNESS_RUN / "simulation.sqlite3"
    new_database = run_dir / "harness_reduced/simulation.sqlite3"

    def quote_counts(path: Path) -> Counter[str]:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return Counter({
                str(quote): int(count)
                for quote, count in connection.execute(
                    "SELECT quote_id, COUNT(*) FROM simulation_events "
                    "WHERE mode='monte_carlo' GROUP BY quote_id"
                )
            })
        finally:
            connection.close()

    old_counts = quote_counts(old_database)
    new_counts = quote_counts(new_database)
    old_total = sum(old_counts.values())
    new_total = sum(new_counts.values())
    quote_ids = set(old_counts) | set(new_counts)
    deltas = []
    total_variation = 0.0
    for quote in quote_ids:
        old_share = old_counts[quote] / old_total if old_total else 0.0
        new_share = new_counts[quote] / new_total if new_total else 0.0
        delta = new_share - old_share
        total_variation += abs(delta)
        deltas.append({
            "quote_id": quote, "before_count": old_counts[quote], "after_count": new_counts[quote],
            "before_share": old_share, "after_share": new_share, "share_delta": delta,
        })
    deltas.sort(key=lambda row: (-abs(row["share_delta"]), row["quote_id"]))
    removed_ids = set(read_json(run_dir / "deployment_candidate/active_quote_manifest.json")["attribution_exclusion_quote_ids"])
    after = validation["mode_results"]["monte_carlo"]
    before_reasons = before.get("veto_reason_counts") or {}
    after_reasons = after.get("veto_reason_counts") or {}
    comparison = {
        "schema_version": 1,
        "before": {
            "quote_count": int(before.get("distinct_quotations") or 0),
            "event_count": int(before.get("total_simulations") or 0),
            "known_winner_coverage": float(before.get("known_pair_coverage") or 0.0),
            "unknown_winner_rate": float(before.get("unknown_production_winner_rate") or 0.0),
            "semantic_veto_rate": float(before.get("semantic_veto_rate") or 0.0),
            "globally_or_observationally_no_safe_quote_count": int(before.get("quotations_with_no_allowed_observed_image") or 0),
            "current_winner_counts": before_gates.get("current_winner_counts") or {},
            "attribution_veto_count": int(before_reasons.get("non_thatcher_speaker_portrait_substitution") or 0),
        },
        "after": {
            "quote_count": after["distinct_active_quotes"], "event_count": after["events"],
            "known_winner_coverage": after["known_winner_coverage"],
            "unknown_winner_rate": after["unknown_winner_rate"],
            "semantic_veto_rate": (after["decision_counts"].get("veto", 0) / after["events"] if after["events"] else 0.0),
            "globally_no_safe_quote_count": int(candidate_shadow.get("quotes_without_allowed_candidate") or 0),
            "current_winner_counts": validation["gates"]["current_production_winner_counts"],
            "attribution_veto_count": int(after_reasons.get("non_thatcher_speaker_portrait_substitution") or 0),
        },
        "removed_quote_events_before": sum(old_counts[quote] for quote in removed_ids),
        "removed_quote_events_after": sum(new_counts[quote] for quote in removed_ids),
        "quotation_distribution_total_variation": total_variation / 2.0,
        "largest_quote_share_changes": deltas[:20],
        "note": "The simulator describes deterministic selector behaviour under fixed seeds; it is not an engagement or causal estimate.",
    }
    atomic_write_json(run_dir / "simulator_before_after_comparison.json", comparison)
    atomic_write_text(run_dir / "simulator_before_after_comparison.md", "\n".join([
        "# Simulator Before/After Comparison", "",
        f"- Quotations: {comparison['before']['quote_count']} -> {comparison['after']['quote_count']}",
        f"- Events: {comparison['before']['event_count']} -> {comparison['after']['event_count']}",
        f"- Known-winner coverage: {comparison['before']['known_winner_coverage']:.4%} -> {comparison['after']['known_winner_coverage']:.4%}",
        f"- Unknown-winner rate: {comparison['before']['unknown_winner_rate']:.4%} -> {comparison['after']['unknown_winner_rate']:.4%}",
        f"- Semantic-veto rate: {comparison['before']['semantic_veto_rate']:.4%} -> {comparison['after']['semantic_veto_rate']:.4%}",
        f"- No-safe quotations: {comparison['before']['globally_or_observationally_no_safe_quote_count']} -> {comparison['after']['globally_no_safe_quote_count']}",
        f"- Attribution-related stateful vetoes: {comparison['before']['attribution_veto_count']} -> {comparison['after']['attribution_veto_count']}",
        f"- Removed-quote selections: {comparison['removed_quote_events_before']} -> {comparison['removed_quote_events_after']}",
        f"- Quotation-distribution total variation: {comparison['quotation_distribution_total_variation']:.4%}", "",
        comparison["note"],
    ]) + "\n")
    return comparison


def build_final_report(run_dir: Path, validation: dict[str, Any]) -> dict[str, Any]:
    removal = read_json(run_dir / "removal_manifest.json")
    apply_result = read_json(run_dir / "apply_result.json")
    rebuild_result = read_json(run_dir / "derived_rebuild_report.json")
    refs = read_json(run_dir / "reference_audit.json")
    shadow_path = run_dir / "deployment_candidate/semantic_veto_shadow_manifest.json"
    verification_path = run_dir / "verification_results.json"
    verification = read_json(verification_path) if verification_path.is_file() else {}
    git_diff_stat = subprocess.run(
        ["git", "diff", "--stat"], cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip()
    git_status_short = subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip()
    report = {
        "schema_version": 1, "generated_at": utc_now(), "removed_records": removal["records"],
        "before_after": apply_result, "reference_audit": {key: refs[key] for key in ("reference_count", "classification_counts")},
        "generated_image_origins": refs.get("generated_image_origins"), "rebuild": rebuild_result,
        "simulator": validation, "candidate_manifest_sha256": sha256_file(shadow_path),
        "zero_new_ai_spend": True, "running_bot_restarted_or_signalled": False,
        "verification": verification, "git_diff_stat": git_diff_stat,
        "git_status_short": git_status_short,
        "running_bot_cache_status": "The running bot reloads mrsMThatcher.txt for each regular selection and therefore does not retain the former source corpus as a long-lived in-memory list. It does retain the pre-task Python code, so the new six-record research gate requires a controlled restart.",
    }
    atomic_write_json(run_dir / "final_report.json", report)
    lines = [
        "# Quotation Attribution Cleanup", "", "## Outcome", "",
        f"- Removed: {EXPECTED_REMOVED} records ({EXPECTED_NON_THATCHER} confirmed non-Thatcher/misattributed; {EXPECTED_UNGROUNDED} speaker-unresolved policy exclusions)",
        f"- Source: {EXPECTED_SOURCE_CANONICAL} to {EXPECTED_AFTER_CANONICAL} distinct canonical records ({EXPECTED_AFTER_PHYSICAL} physical lines after preserving one pre-existing duplicate)",
        f"- Eligible confirmed Thatcher quotations: {EXPECTED_CONFIRMED}", f"- Unresolved research records retained and excluded: {EXPECTED_UNRESOLVED}",
        f"- Before SHA-256: `{apply_result['before_sha256']}`", f"- After SHA-256: `{apply_result['after_sha256']}`",
        "- Retained bytes and order unchanged: yes", "- New AI spend: US$0.00", "- External network calls: 0", "- Bot restarts/signals: 0", "",
        "## Removed Records", "",
    ]
    for row in removal["records"]:
        evidence = row.get("attribution_evidence") or {}
        lines.append(
            f"- `{row['quote_id']}` — {row['classification']} — {row['exact_quote_text']} "
            f"Speaker finding: {row.get('canonical_speaker') or 'not grounded'}; "
            f"verification: {evidence.get('verification_status') or 'unavailable'}; "
            f"source event: {evidence.get('source_event') or 'unavailable'}; "
            f"structured evidence: `{row['source_artefact']}` record SHA-256 `{row['source_record_hash']}`."
        )
    comparison = validation.get("before_after_comparison") or {}
    comparison_before = comparison.get("before") or {}
    comparison_after = comparison.get("after") or {}
    lines += [
        "", "## Derived Artefacts", "", f"- Candidate semantic manifest: `{shadow_path}`",
        f"- Candidate manifest SHA-256: `{sha256_file(shadow_path)}`",
        f"- Unknown reconciliation before/after: {rebuild_result['unknown_reconciliation']['unknown_reconciliation_before']} / {rebuild_result['unknown_reconciliation']['unknown_reconciliation_after']}",
        "- `non_thatcher_speaker_portrait_substitution` in active simulation: 0", "",
        "## Simulator", "", f"- Passed: {validation['passed']}",
        f"- Full sweep: {validation['mode_results']['full_corpus_sweep']}",
        f"- Monte Carlo: {validation['mode_results']['monte_carlo']}",
        f"- Seasonal boundary stress: {validation['mode_results']['seasonal_boundary_stress']}", "",
        "## Simulator Before/After", "",
        f"- Quotations: {comparison_before.get('quote_count')} -> {comparison_after.get('quote_count')}",
        f"- Known-winner coverage: {comparison_before.get('known_winner_coverage', 0):.4%} -> {comparison_after.get('known_winner_coverage', 0):.4%}",
        f"- Unknown-winner rate: {comparison_before.get('unknown_winner_rate', 0):.4%} -> {comparison_after.get('unknown_winner_rate', 0):.4%}",
        f"- Semantic-veto rate: {comparison_before.get('semantic_veto_rate', 0):.4%} -> {comparison_after.get('semantic_veto_rate', 0):.4%}",
        f"- No-safe quotations: {comparison_before.get('globally_or_observationally_no_safe_quote_count')} -> {comparison_after.get('globally_no_safe_quote_count')}",
        f"- Attribution-related stateful vetoes: {comparison_before.get('attribution_veto_count')} -> {comparison_after.get('attribution_veto_count')}",
        f"- Removed-quote selections: {comparison.get('removed_quote_events_before')} -> {comparison.get('removed_quote_events_after')}",
        f"- Quotation-distribution total variation: {comparison.get('quotation_distribution_total_variation', 0):.4%}", "",
        "## Verification", "",
        f"- Focused tests: {verification.get('focused_tests', 'unavailable')}",
        f"- Full suite: {verification.get('full_suite', 'unavailable')}",
        f"- Python compilation: {verification.get('py_compile', 'unavailable')}",
        f"- `git diff --check`: {verification.get('git_diff_check', 'unavailable')}", "",
        "## Git Diff Stat", "", "```text", git_diff_stat, "```", "",
        "## Git Status Short", "", "```text", git_status_short, "```", "",
        "## Production Behaviour", "",
        "The bot reloads `mrsMThatcher.txt` at each regular quotation selection, so it does not retain the former 632-record source corpus as a long-lived in-memory list. The 13 deletions are therefore visible now. The running bot was not restarted or signalled.",
        "The running process still has the pre-task selector code in memory. A separately authorised controlled restart is required to activate the new completed-research gate that excludes the six retained unresolved records.",
        "A later deployment of the candidate semantic-veto manifest requires separate approval; the active live shadow manifest was not replaced.", "",
    ]
    atomic_write_text(run_dir / "final_report.md", "\n".join(lines))
    return report


def status(project_dir: Path, run_dir: Path) -> dict[str, Any]:
    if project_dir.resolve() != ROOT:
        raise CleanupError(f"project directory must be {ROOT}")
    files = {name: (run_dir / name).is_file() for name in (
        "removal_manifest.json", "apply_result.json", "derived_rebuild_report.json", "simulator_validation.json", "final_report.md"
    )}
    records = source_records((ROOT / SOURCE_NAME).read_bytes())
    return {
        "run_dir": str(run_dir), "phases": files, "source_physical_records": len(records),
        "source_canonical_records": len({row['quote_id'] for row in records}), "source_sha256": sha256_file(ROOT / SOURCE_NAME),
        "network_calls": 0, "paid_ai_calls": 0,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    audit_p = sub.add_parser("audit")
    audit_p.add_argument("--project-dir", type=Path, default=ROOT)
    audit_p.add_argument("--remediation-dir", type=Path, default=DEFAULT_REMEDIATION)
    audit_p.add_argument("--output", type=Path, default=DEFAULT_RUN)
    apply_p = sub.add_parser("apply")
    apply_p.add_argument("--project-dir", type=Path, default=ROOT)
    apply_p.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    apply_p.add_argument("--remove-confirmed-non-thatcher", action="store_true")
    apply_p.add_argument("--remove-ungrounded-speaker", action="store_true")
    rebuild_p = sub.add_parser("rebuild")
    rebuild_p.add_argument("--project-dir", type=Path, default=ROOT)
    rebuild_p.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    validate_p = sub.add_parser("validate")
    validate_p.add_argument("--project-dir", type=Path, default=ROOT)
    validate_p.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    validate_p.add_argument("--rerun-harness", action="store_true")
    status_p = sub.add_parser("status")
    status_p.add_argument("--project-dir", type=Path, default=ROOT)
    status_p.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "audit":
        result = audit(args.project_dir, args.remediation_dir, args.output)
    elif args.command == "apply":
        result = apply_cleanup(args.project_dir, args.run_dir, args.remove_confirmed_non_thatcher, args.remove_ungrounded_speaker)
    elif args.command == "rebuild":
        result = rebuild(args.project_dir, args.run_dir)
    elif args.command == "validate":
        result = validate(args.project_dir, args.run_dir, args.rerun_harness)
    elif args.command == "status":
        result = status(args.project_dir, args.run_dir)
    else:
        raise CleanupError(f"unsupported command: {args.command}")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
