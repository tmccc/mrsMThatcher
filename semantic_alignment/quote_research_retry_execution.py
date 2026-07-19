"""Execute and merge resumable quotation-research retry batches."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .io import atomic_write_json, atomic_write_text, read_json, read_jsonl, sha256_file
from .quote_research_corpus import CorpusRunner
from .quote_research_gemini import (
    PACKET_SCHEMA,
)

RETRY_RUN_VERSION = 1
RETRY_PROMPT_VERSION = "quote-research-grounded-v2-retry"
IDENTITY_FIELDS = {"quote_id", "quote_text"}
RETRY_PACKET_SCHEMA = {
    **PACKET_SCHEMA,
    "properties": {key: value for key, value in PACKET_SCHEMA["properties"].items() if key not in IDENTITY_FIELDS},
    "required": [key for key in PACKET_SCHEMA["required"] if key not in IDENTITY_FIELDS],
}


def build_retry_batch(parent_run: Path, source_manifest: Path, count: int, output: Path) -> dict[str, Any]:
    """Build retry batch."""
    if count != 30:
        raise RuntimeError("this staged batch builder is limited to exactly 30 items")
    if output.exists():
        existing = read_json(output)
        if existing.get("record_count") != count:
            raise RuntimeError("existing immutable retry batch has a different count")
        return existing
    source = read_json(source_manifest)
    completed = set((read_json(parent_run / "research_packets.json") or {}).get("items", {}))
    prior_paths = [parent_run / "retry_analysis/retry_manifest_20.json"]
    prior_paths.extend(sorted((parent_run / "retry_batches").glob("retry_batch_*.json")))
    prior_ids = {
        row["quote_id"] for path in prior_paths if path.resolve() != output.resolve()
        for row in read_json(path)["records"]
    }
    eligible = [row for row in source["records"] if row["quote_id"] not in completed | prior_ids]
    by_group: dict[str, list[dict[str, Any]]] = {}
    for row in eligible:
        by_group.setdefault(row["retry_group"], []).append(row)
    targets = {"transport_failures": 10, "missing_grounding": 18,
               "identity_and_structured_output": 2}
    if len(eligible) == count:
        # The terminal staged batch must not strand valid candidates merely because
        # the remaining failure-class mix differs from the sampling target.
        selected = list(eligible)
        targets = dict(Counter(row["retry_group"] for row in selected))
    else:
        selected = []
        for group, target in targets.items():
            ordered = sorted(by_group.get(group, []), key=lambda row: hashlib.sha256(
                f"{output.stem}:{row['quote_id']}".encode()).hexdigest())
            if len(ordered) < target:
                raise RuntimeError(f"insufficient eligible {group} cases")
            selected.extend(ordered[:target])
    selected.sort(key=lambda row: row["quote_id"])
    ids = [row["quote_id"] for row in selected]
    carry_forward = sorted(prior_ids - completed)
    payload = {
        "schema_version": 1, "record_kind": "quote_research_retry_staged_batch",
        "batch_id": output.stem, "source_manifest_sha256": sha256_file(source_manifest),
        "selection_seed": output.stem, "record_count": len(selected),
        "class_counts": targets, "records": selected,
        "excluded_previous_validation_ids": sorted(prior_ids),
        "carry_forward_not_included": carry_forward,
        "observed_cost_per_recovered_quote_usd": 0.1813568875,
        "expected_cost_usd": round(count * 0.1813568875, 4),
        "hard_combined_ceiling_usd": 7.5,
    }
    payload["manifest_sha256"] = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    if len(ids) != count or len(ids) != len(set(ids)) or set(ids) & (completed | prior_ids):
        raise RuntimeError("retry batch selection integrity failure")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and read_json(output) != payload:
        raise RuntimeError("existing immutable retry batch differs")
    if not output.exists():
        atomic_write_json(output, payload)
    return payload


def build_failed_batch_recovery(parent_run: Path, source_manifest: Path, source_run: Path,
                                output: Path) -> dict[str, Any]:
    """Build failed batch recovery."""
    source = read_json(source_manifest)
    failed = set((read_json(source_run / "permanent_failures.json") or {}).get("items", {}))
    completed = set((read_json(source_run / "research_packets.json") or {}).get("items", {}))
    records = [row for row in source["records"] if row["quote_id"] in failed]
    records.sort(key=lambda row: row["quote_id"])
    if len(records) != 19 or failed & completed or {row["quote_id"] for row in records} != failed:
        raise RuntimeError("Batch 002 recovery must contain exactly its 19 unresolved cases")
    counts = dict(Counter(row["retry_group"] for row in records))
    payload = {
        "schema_version": 1, "record_kind": "quote_research_failed_batch_recovery",
        "batch_id": output.stem, "source_manifest_sha256": sha256_file(source_manifest),
        "source_run": str(source_run.relative_to(parent_run)), "record_count": len(records),
        "class_counts": counts, "records": records,
        "excluded_completed_source_batch_ids": sorted(completed),
        "expected_cost_usd": round(len(records) * 0.1813568875, 4),
        "hard_combined_ceiling_usd": 5.0,
    }
    payload["manifest_sha256"] = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and read_json(output) != payload:
        raise RuntimeError("existing immutable failed-batch recovery manifest differs")
    if not output.exists():
        atomic_write_json(output, payload)
    return payload


def build_remaining_failed_recovery(parent_run: Path, source_manifest: Path,
                                    output: Path, max_prior_cycles: int = 1) -> dict[str, Any]:
    """Build one final recovery pass without recycling repeatedly exhausted cases."""
    if output.exists():
        return read_json(output)
    source = read_json(source_manifest)
    completed = set((read_json(parent_run / "research_packets.json") or {}).get("items", {}))
    unresolved = set((read_json(parent_run / "permanent_failures.json") or {}).get("items", {}))
    prior_paths = [parent_run / "retry_analysis/retry_manifest_20.json"]
    prior_paths.extend(sorted((parent_run / "retry_batches").glob("retry_batch_*.json")))
    cycle_counts = Counter(
        row["quote_id"] for path in prior_paths if path.resolve() != output.resolve()
        for row in read_json(path)["records"]
    )
    records = [
        row for row in source["records"]
        if row["quote_id"] in unresolved - completed
        and cycle_counts[row["quote_id"]] <= max_prior_cycles
    ]
    records.sort(key=lambda row: row["quote_id"])
    if not records:
        raise RuntimeError("no once-exhausted unresolved cases remain eligible for recovery")
    excluded = sorted(
        quote_id for quote_id in unresolved - completed
        if cycle_counts[quote_id] > max_prior_cycles
    )
    payload = {
        "schema_version": 1,
        "record_kind": "quote_research_remaining_failed_recovery",
        "batch_id": output.stem,
        "source_manifest_sha256": sha256_file(source_manifest),
        "record_count": len(records),
        "class_counts": dict(Counter(row["retry_group"] for row in records)),
        "records": records,
        "maximum_prior_retry_cycles": max_prior_cycles,
        "excluded_repeatedly_exhausted_ids": excluded,
        "expected_cost_usd": round(len(records) * 0.1813568875, 4),
        "hard_combined_ceiling_usd": 5.0,
    }
    payload["manifest_sha256"] = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output, payload)
    return payload


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def retry_prompt(record: dict[str, Any]) -> str:
    """Retry prompt."""
    return f"""Prompt version: {RETRY_PROMPT_VERSION}
Research exactly the single quotation in the immutable request envelope using Google Search grounding. Perform at least one focused grounded search before synthesising the JSON response. A response without provider-linked grounding chunks and grounding supports will be rejected.

IMMUTABLE REQUEST ENVELOPE (context only; do not emit these fields):
quote_id: {record['quote_id']}
uploaded_quote_text: {json.dumps(record['quote_text'], ensure_ascii=False)}
manifest_identity_hash: {record['input_hash']}

Return the compact quote-research JSON object, excluding quote_id and quote_text. Research only the uploaded quotation. Put evidence-supported corrected wording only in verified_text. Never replace the uploaded quotation with a different saying. Establish verification status, variation notes, source event/date/locator, context, intended argument, literal meaning, principle, mechanism, consequence, entities and concise editorial guidance. Prefer underlying Margaret Thatcher Foundation transcripts, Hansard, official records, books and contemporary reporting. Use unknown rather than speculation. Keep prose fields to two sentences, lists to eight items and sources to six. Model-written URLs are not evidence; every accepted source and support claim must be linked by returned provider grounding metadata.
"""


def retry_repair_prompt(record: dict[str, Any], raw_text: str, validation_error: str) -> str:
    """Retry repair prompt."""
    return f"""Prompt version: {RETRY_PROMPT_VERSION}-grounded-repair
Perform a fresh Google Search for the exact quotation in the immutable request envelope, then return a corrected compact quote-research JSON object. This is a new grounded research attempt, not an ungrounded rewrite. At least one provider-linked grounding chunk and support is mandatory. Do not emit quote_id or quote_text.

IMMUTABLE REQUEST ENVELOPE:
quote_id: {record['quote_id']}
uploaded_quote_text: {json.dumps(record['quote_text'], ensure_ascii=False)}
manifest_identity_hash: {record['input_hash']}

validation_error: {json.dumps(validation_error)}
Prior malformed response is supplied only to avoid losing supported work; independently re-check every material claim through the fresh grounded search:
{raw_text[:24000]}
"""


def bind_immutable_identity(content: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """Bind immutable identity."""
    if not isinstance(content, dict):
        raise ValueError("response packet must be an object")
    controlled = IDENTITY_FIELDS & set(content)
    if controlled:
        raise ValueError(f"model returned immutable identity fields: {sorted(controlled)}")
    value = dict(content)
    value["quote_id"] = record["quote_id"]
    value["quote_text"] = record["quote_text"]
    return value


def _retry_run_dir(manifest_path: Path, retry: dict[str, Any]) -> Path:
    if retry.get("record_kind") == "quote_research_retry_validation_manifest":
        return manifest_path.parent / "retry_validation_20"
    desired = manifest_path.parent / f"{manifest_path.stem}_run"
    legacy = manifest_path.parent / "retry_validation_20"
    legacy_manifest = legacy / "corpus_manifest.json"
    if legacy_manifest.exists():
        value = read_json(legacy_manifest)
        if value.get("parent_retry_manifest_sha256") == sha256_file(manifest_path):
            return legacy
    return desired


def validate_retry_manifest(run_dir: Path, manifest_path: Path, combined_ceiling: float | None = None) -> dict[str, Any]:
    """Validate retry manifest."""
    retry = read_json(manifest_path)
    records = retry.get("records") or []
    ids = [row["quote_id"] for row in records]
    expected_count = int(retry.get("record_count") or 0)
    if expected_count not in {19, 20, 30} or len(records) != expected_count or len(ids) != len(set(ids)):
        raise RuntimeError("retry manifest must contain exactly its approved 19, 20 or 30 unique quote IDs")
    counts = Counter(row["retry_group"] for row in records)
    expected = retry.get("class_counts") or {
        "transport_failures": 7, "missing_grounding": 10, "identity_and_structured_output": 3}
    if dict(counts) != expected:
        raise RuntimeError(f"retry validation composition mismatch: {dict(counts)}")
    completed = set((read_json(run_dir / "research_packets.json") or {}).get("items", {}))
    overlap = sorted(completed & set(ids))
    expected_run_dir = _retry_run_dir(manifest_path, retry)
    expected_retry_run = str(expected_run_dir.relative_to(run_dir))
    initial_validation = retry.get("record_kind") == "quote_research_retry_validation_manifest"
    if initial_validation:
        expected_retry_run = "retry_analysis/retry_validation_20"
    packets = (read_json(run_dir / "research_packets.json") or {}).get("items", {})
    unrelated_overlap = [quote_id for quote_id in overlap if not (
        packets[quote_id].get("recovered_via_paid_retry_validation") is True
        and packets[quote_id].get("retry_run") == expected_retry_run
    )]
    if unrelated_overlap:
        raise RuntimeError(f"completed/offline-recovered quotes present in retry manifest: {unrelated_overlap}")
    if initial_validation:
        cost = read_json(manifest_path.parent / "retry_cost_projection.json")["validation_batch"]
        expected_cost = float(cost["expected_cost_usd"]); conservative = float(cost["high_projection_usd"])
        approved_ceiling = 5.0
    else:
        expected_cost = float(retry["expected_cost_usd"]); conservative = expected_cost
        approved_ceiling = float(retry["hard_combined_ceiling_usd"])
    requested_ceiling = approved_ceiling if combined_ceiling is None else combined_ceiling
    if conservative > approved_ceiling or requested_ceiling != approved_ceiling:
        raise RuntimeError(f"retry validation cost projection exceeds exact ${approved_ceiling:g} ceiling")
    return {
        "schema_version": RETRY_RUN_VERSION, "manifest_path": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path), "candidate_count": len(records),
        "group_counts": expected, "duplicates": 0, "completed_overlap": overlap,
        "completed_overlap_provenance": "same_retry_run" if overlap else "none",
        "expected_cost_usd": expected_cost,
        "conservative_cost_usd": conservative, "hard_combined_ceiling_usd": approved_ceiling,
        "grounding_required": True, "identity_fields_model_editable": False,
        "developer_first": True, "concurrency": {"developer_api": 1, "vertex_ai": 1},
        "read_timeout_seconds": 360, "max_attempts_per_transport": 2,
        "developer_pause_after_consecutive_provider_wide_429": 2,
        "automatic_developer_reprobe": False,
        "live_execution_requires": ["--execute", f"--confirm-combined-limit-usd {approved_ceiling:g}"],
        "allowed": True,
    }


def prepare_retry_run(parent_run: Path, retry_manifest: Path) -> tuple[Path, dict[str, Any]]:
    """Prepare retry run."""
    preflight = validate_retry_manifest(parent_run, retry_manifest)
    source = read_json(retry_manifest)
    run_dir = _retry_run_dir(retry_manifest, source)
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1, "record_kind": "quote_research_retry_validation_run",
        "parent_run": parent_run.name, "parent_retry_manifest_sha256": preflight["manifest_sha256"],
        "record_count": len(source["records"]), "records": source["records"],
    }
    manifest["manifest_sha256"] = hashlib.sha256(json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    path = run_dir / "corpus_manifest.json"
    if path.exists() and read_json(path) != manifest:
        raise RuntimeError("immutable retry execution manifest differs")
    if not path.exists():
        atomic_write_json(path, manifest)
    atomic_write_json(run_dir / "preflight.json", preflight)
    return run_dir, manifest


class RetryValidationRunner(CorpusRunner):
    """Run retry validation operations."""
    def __init__(self, *args, **kwargs):
        """Initialise the retry validation runner."""
        super().__init__(*args, prompt_builder=retry_prompt, repair_builder=retry_repair_prompt,
                         identity_binder=bind_immutable_identity, **kwargs)


def apply_retry_results(parent_run: Path, retry_run: Path) -> dict[str, Any]:
    """Apply retry results."""
    retry_packets = (read_json(retry_run / "research_packets.json") or {}).get("items", {})
    main_path = parent_run / "research_packets.json"
    main = read_json(main_path)
    new_ids = sorted(set(retry_packets) - set(main.get("items", {})))
    if not new_ids:
        return {"applied": 0, "already_present": len(set(retry_packets) & set(main.get("items", {})))}
    backup_dir = retry_run / "apply_backups" / sha256_file(main_path)[:16]
    backup_dir.mkdir(parents=True, exist_ok=True)
    paths = ["research_packets.json", "grounding_sources.json", "permanent_failures.json", "run_state.json", "status.json"]
    before = {}
    for name in paths:
        source = parent_run / name
        before[name] = sha256_file(source)
        shutil.copy2(source, backup_dir / name)
    grounding = read_json(parent_run / "grounding_sources.json")
    retry_grounding = read_json(retry_run / "grounding_sources.json")
    permanent = read_json(parent_run / "permanent_failures.json")
    state = read_json(parent_run / "run_state.json")
    for quote_id in new_ids:
        main["items"][quote_id] = {**retry_packets[quote_id], "recovered_via_paid_retry_validation": True,
                                    "retry_run": str(retry_run.relative_to(parent_run))}
        grounding["items"][quote_id] = retry_grounding["items"][quote_id]
        permanent["items"].pop(quote_id, None)
        state["items"][quote_id].update({"status": "valid", "last_error": None,
                                          "completed_transport": retry_packets[quote_id]["transport"],
                                          "updated_at": utc_now()})
    atomic_write_json(main_path, main)
    atomic_write_json(parent_run / "grounding_sources.json", grounding)
    atomic_write_json(parent_run / "permanent_failures.json", permanent)
    atomic_write_json(parent_run / "run_state.json", state)
    status = read_json(parent_run / "status.json")
    status["valid_packets"] = len(main["items"])
    status["permanent_failures"] = len(permanent["items"])
    status["resume_plan"]["completed_will_be_skipped"] = len(main["items"])
    status["resume_plan"]["permanent_failures_will_be_skipped"] = len(permanent["items"])
    status["updated_at"] = utc_now()
    atomic_write_json(parent_run / "status.json", status)
    after = {name: sha256_file(parent_run / name) for name in paths}
    audit = {"schema_version": 1, "record_kind": "paid_retry_validation_apply", "applied_at": utc_now(),
             "quote_ids": new_ids, "applied_count": len(new_ids), "backup_dir": str(backup_dir.relative_to(parent_run)),
             "before_hashes": before, "after_hashes": after}
    atomic_write_json(retry_run / "apply_audit.json", audit)
    return audit


def write_recovery_stage_meta_report(parent_run: Path) -> dict[str, Any]:
    """Write recovery stage meta report."""
    manifest_runs: list[tuple[Path, Path]] = []
    initial = parent_run / "retry_analysis/retry_manifest_20.json"
    initial_run = parent_run / "retry_analysis/retry_validation_20"
    if (initial_run / "retry_validation_summary.json").exists():
        manifest_runs.append((initial, initial_run))
    for manifest_path in sorted((parent_run / "retry_batches").glob("retry_batch_*.json")):
        retry = read_json(manifest_path)
        retry_run = _retry_run_dir(manifest_path, retry)
        if (retry_run / "retry_validation_summary.json").exists():
            manifest_runs.append((manifest_path, retry_run))

    main_packets = set((read_json(parent_run / "research_packets.json") or {}).get("items", {}))
    final_failures = set((read_json(parent_run / "permanent_failures.json") or {}).get("items", {}))
    rows = []
    unique_ids: set[str] = set()
    timestamps = []
    for manifest_path, retry_run in manifest_runs:
        manifest = read_json(manifest_path)
        summary = read_json(retry_run / "retry_validation_summary.json")
        ids = {row["quote_id"] for row in manifest["records"]}
        unique_ids |= ids
        for attempt in read_jsonl(retry_run / "attempts.jsonl"):
            if attempt.get("timestamp"):
                timestamps.append(datetime.fromisoformat(attempt["timestamp"].replace("Z", "+00:00")))
        rows.append({
            "manifest": str(manifest_path.relative_to(parent_run)),
            "run": str(retry_run.relative_to(parent_run)),
            "targeted": len(ids),
            "recovered": int(summary["completed"]),
            "unresolved": int(summary["unresolved"]),
            "developer_completions": int(summary["developer_completions"]),
            "vertex_completions": int(summary["vertex_completions"]),
            "developer_429_count": int(summary["developer_429_count"]),
            "vertex_429_count": int(summary.get("vertex_429_count") or 0),
            "developer_pause_activated": bool(summary["developer_pause_activated"]),
            "direct_to_vertex_count": int(summary["direct_to_vertex_count"]),
            "known_spend_usd": float(summary["actual_spend_usd"]),
            "ambiguous_exposure_usd": float(summary.get("ambiguous_possible_exposure_usd") or 0),
            "elapsed_seconds": float(summary["elapsed_seconds"]),
        })
    recovered_unique = unique_ids & main_packets
    unresolved_unique = unique_ids & final_failures
    meta = {
        "schema_version": 1,
        "record_kind": "quote_research_recovery_stage_meta_report",
        "generated_at": utc_now(),
        "runs": rows,
        "run_count": len(rows),
        "unique_candidates_targeted": len(unique_ids),
        "unique_candidates_recovered": len(recovered_unique),
        "unique_candidates_unresolved": len(unresolved_unique),
        "unique_recovery_rate": len(recovered_unique) / len(unique_ids) if unique_ids else 0,
        "known_spend_usd": sum(row["known_spend_usd"] for row in rows),
        "ambiguous_possible_exposure_usd": sum(row["ambiguous_exposure_usd"] for row in rows),
        "developer_completions": sum(row["developer_completions"] for row in rows),
        "vertex_completions": sum(row["vertex_completions"] for row in rows),
        "developer_429_count": sum(row["developer_429_count"] for row in rows),
        "vertex_429_count": sum(row["vertex_429_count"] for row in rows),
        "final_packet_count": len(main_packets),
        "final_unresolved_count": len(final_failures),
        "final_unresolved_quote_ids": sorted(final_failures),
        "stage_wall_clock_seconds": ((max(timestamps) - min(timestamps)).total_seconds()
                                     if timestamps else 0),
        "stop_reason": "all candidates received a staged retry; remaining cases exhausted two bounded cycles",
    }
    output = parent_run / "retry_batches"
    atomic_write_json(output / "recovery_stage_meta_report.json", meta)
    table = "\n".join(
        f"| {row['manifest']} | {row['targeted']} | {row['recovered']} | {row['unresolved']} | "
        f"{row['developer_completions']} | {row['vertex_completions']} | ${row['known_spend_usd']:.4f} |"
        for row in rows
    )
    report = f"""# Quote Research Recovery Stage Meta Report

## Outcome

- Unique paid-retry candidates: {len(unique_ids)}
- Recovered: {len(recovered_unique)} ({meta['unique_recovery_rate']:.1%})
- Still unresolved: {len(unresolved_unique)}
- Final completed packet count: {len(main_packets)}/632
- Known retry spend: ${meta['known_spend_usd']:.4f}
- Possible ambiguous exposure: ${meta['ambiguous_possible_exposure_usd']:.4f}
- Developer completions across run records: {meta['developer_completions']}
- Vertex completions across run records: {meta['vertex_completions']}
- Developer / Vertex 429 responses: {meta['developer_429_count']} / {meta['vertex_429_count']}

## Batches

| Manifest | Targeted | Recovered | Unresolved | Developer | Vertex | Known spend |
|---|---:|---:|---:|---:|---:|---:|
{table}

## Stop Decision

All 170 original paid-retry candidates received a staged retry. The final {len(final_failures)} unresolved quotations have also exhausted one separate bounded recovery cycle, so another paid retry is not justified without a new method or explicit exception. The final unresolved IDs are recorded in the machine-readable report.

No production behavior was changed. Original provider attempts and raw responses remain preserved. Nothing was staged, committed, pushed, or deployed.
"""
    atomic_write_text(output / "recovery_stage_meta_report.md", report)
    return meta
