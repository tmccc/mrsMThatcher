from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .io import atomic_write_json, atomic_write_text, read_json, read_jsonl, sha256_file
from .quote_research_gemini import (
    MODEL, PROMPT_VERSION, bind_packet_to_grounding, extract_grounding,
    parse_response_packet, validate_packet,
)

RECOVERY_SCHEMA_VERSION = 1
FINAL_ATTEMPT_STATES = {
    "validation_failure", "transient_failure", "cancelled_before_send", "confirmed_failure",
}
TAXONOMY_CATEGORIES = (
    "missing_grounded_source", "vertex_grounding_metadata_extraction_incompatibility",
    "malformed_or_truncated_json", "schema_validation_error", "quote_identity_changed",
    "timeout_or_transport_failure", "no_response_received", "cancelled_before_send",
    "genuine_historical_verification_failure", "other",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()


def _normalised_uploaded_text(value: str) -> str:
    text = " ".join(value.split()).strip()
    pairs = (("\"", "\""), ("'", "'"), ("\u201c", "\u201d"), ("\u2018", "\u2019"))
    if len(text) >= 2 and any(text.startswith(left) and text.endswith(right) for left, right in pairs):
        text = text[1:-1].strip()
    return " ".join(text.split())


def enforce_quote_identity(packet: dict[str, Any], record: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Bind immutable envelope fields without replacing substantive researched wording."""
    if packet.get("quote_id") != record["quote_id"]:
        raise ValueError("quote_id changed")
    supplied = packet.get("quote_text")
    if not isinstance(supplied, str):
        raise ValueError("quote_text missing")
    repairs: list[str] = []
    if supplied != record["quote_text"]:
        if _normalised_uploaded_text(supplied) != _normalised_uploaded_text(record["quote_text"]):
            raise ValueError("quote identity changed")
        repairs.append("restored_manifest_quote_text_after_outer_quote_or_whitespace_change")
    result = dict(packet)
    result["quote_id"] = record["quote_id"]
    result["quote_text"] = record["quote_text"]
    return result, repairs


def _raw_paths(run_dir: Path, quote_id: str) -> list[Path]:
    folder = run_dir / "raw_responses" / quote_id
    if not folder.exists():
        return []
    return sorted(path for path in folder.glob("*.json") if not path.name.endswith("_extracted.json"))


def _attempt_number(path: Path) -> int:
    try:
        return int(path.stem.rsplit("_attempt_", 1)[1].split("_", 1)[0])
    except (IndexError, ValueError):
        return 0


def _transport(path: Path) -> str:
    return "developer_api" if path.name.startswith("developer_api") else "vertex_ai"


def _response_text(raw: dict[str, Any]) -> str:
    return "".join(str(part.get("text") or "")
                   for candidate in raw.get("candidates") or []
                   for part in (candidate.get("content") or {}).get("parts") or [])


def _salvage_packet_before_sources(raw: dict[str, Any]) -> dict[str, Any]:
    """Discard only a malformed final model source array; metadata rebuilds it later."""
    text = _response_text(raw).strip()
    matches = list(re.finditer(r'"sources"\s*:', text))
    if not matches:
        raise ValueError("no final sources field available for bounded salvage")
    prefix = text[:matches[-1].start()]
    candidate = prefix + '"sources": []}'
    value = json.loads(candidate)
    if not isinstance(value, dict):
        raise ValueError("salvaged packet is not an object")
    return value


def inspect_raw_response(path: Path, record: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path), "transport": _transport(path), "attempt_number": _attempt_number(path),
        "raw_sha256": sha256_file(path), "packet_parsed": False, "valid_packet": False,
        "grounding_source_count": 0, "linked_grounding_source_count": 0, "repairs": [],
    }
    try:
        raw = read_json(path)
    except (json.JSONDecodeError, OSError) as exc:
        result["error"] = f"raw_response_malformed: {type(exc).__name__}: {exc}"
        return result
    candidates = raw.get("candidates") if isinstance(raw, dict) else None
    candidate = candidates[0] if isinstance(candidates, list) and candidates else {}
    metadata = (candidate.get("groundingMetadata") or candidate.get("grounding_metadata") or {}) \
        if isinstance(candidate, dict) else {}
    result["response_layout"] = (
        "developer_camel_case" if "groundingMetadata" in candidate else
        "vertex_snake_case" if "grounding_metadata" in candidate else "no_grounding_metadata"
    )
    result["grounding_metadata_fields"] = sorted(metadata)
    grounding = extract_grounding(raw)
    result["grounding_source_count"] = len(grounding["sources"])
    result["linked_grounding_source_count"] = sum(bool(row.get("supports")) for row in grounding["sources"])
    result["grounding_query_count"] = len(grounding["queries"])
    result["grounding"] = grounding
    try:
        try:
            packet, parse_repairs = parse_response_packet(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            packet = _salvage_packet_before_sources(raw)
            parse_repairs = ["discarded_malformed_final_model_sources_rebuilt_from_grounding_metadata"]
        result["packet_parsed"] = True
        packet, identity_repairs = enforce_quote_identity(packet, record)
        bound = bind_packet_to_grounding(packet, grounding)
        validated = validate_packet(bound, record)
        result["valid_packet"] = True
        result["packet"] = validated
        result["repairs"] = parse_repairs + identity_repairs
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _latest_failure_state(rows: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    final = [row for row in rows if row.get("state") in FINAL_ATTEMPT_STATES]
    if not final:
        return "unknown", {}
    return str(final[-1]["state"]), dict(final[-1].get("failure") or {"message": final[-1].get("error")})


def _classify_failure(state: str, failure: dict[str, Any], raw: list[dict[str, Any]]) -> str:
    message = str(failure.get("message") or "").casefold()
    if state == "cancelled_before_send":
        return "cancelled_before_send"
    if state == "confirmed_failure" or state == "transient_failure":
        if any(term in message for term in ("timeout", "429", "resource_exhausted", "503", "500", "cancelled")):
            return "timeout_or_transport_failure"
        return "no_response_received" if not raw else "other"
    if "quote identity changed" in message or "quote_id changed" in message:
        return "quote_identity_changed"
    if "grounded source" in message:
        return "missing_grounded_source"
    if any(term in message for term in ("jsondecodeerror", "unterminated", "truncated json")):
        return "malformed_or_truncated_json"
    if "nonetype" in message and not any(item.get("packet_parsed") for item in raw):
        return "no_response_received"
    if "schema" in message or state == "validation_failure":
        return "schema_validation_error"
    return "other"


def _transport_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    layouts = Counter(row.get("response_layout", "unknown") for row in rows)
    metadata_fields = Counter(field for row in rows for field in row.get("grounding_metadata_fields", []))
    return {
        "responses": len(rows), "layouts": dict(sorted(layouts.items())),
        "metadata_fields": dict(sorted(metadata_fields.items())),
        "with_parsed_packet": sum(row.get("packet_parsed", False) for row in rows),
        "with_grounding_chunks": sum(row.get("grounding_source_count", 0) > 0 for row in rows),
        "with_linked_grounding": sum(row.get("linked_grounding_source_count", 0) > 0 for row in rows),
        "valid_under_recovery_parser": sum(row.get("valid_packet", False) for row in rows),
    }


def audit_failures(run_dir: Path) -> dict[str, Any]:
    manifest = read_json(run_dir / "corpus_manifest.json")
    records = {row["quote_id"]: row for row in manifest["records"]}
    completed = (read_json(run_dir / "research_packets.json") or {}).get("items", {})
    failures = (read_json(run_dir / "permanent_failures.json") or {}).get("items", {})
    previously_recovered = {quote_id for quote_id, packet in completed.items()
                            if isinstance(packet, dict) and packet.get("offline_recovery")}
    original_failure_ids = set(failures) | previously_recovered
    original_completed = set(completed) - previously_recovered
    prior_taxonomy = ((read_json(run_dir / "offline_recovery" / "failure_taxonomy.json", {}) or {})
                      .get("items", {}))
    attempts = read_jsonl(run_dir / "attempts.jsonl")
    attempts_by_quote: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in attempts:
        attempts_by_quote[str(row.get("quote_id"))].append(row)
    if original_completed & original_failure_ids or original_completed | original_failure_ids != set(records):
        raise RuntimeError("run terminal sets do not exactly match immutable manifest")

    taxonomy: dict[str, Any] = {}
    recovered: dict[str, Any] = {}
    all_failed_raw: list[dict[str, Any]] = []
    for quote_id in sorted(original_failure_ids):
        record = records[quote_id]
        state, failure = _latest_failure_state(attempts_by_quote.get(quote_id, []))
        if quote_id in prior_taxonomy:
            state = str(prior_taxonomy[quote_id].get("latest_state") or state)
            failure = dict(prior_taxonomy[quote_id].get("failure") or failure)
        raw_rows = [inspect_raw_response(path, record) for path in _raw_paths(run_dir, quote_id)]
        all_failed_raw.extend(raw_rows)
        valid = [row for row in raw_rows if row.get("valid_packet")]
        chosen = valid[-1] if valid else None
        original_category = _classify_failure(state, failure, raw_rows)
        # The observed Vertex snake_case layout was already understood. Recovery may use an
        # earlier grounded attempt, but that is not evidence of a transport-layout defect.
        category = original_category
        any_content = any(row.get("packet_parsed") for row in raw_rows)
        any_linked = any(row.get("linked_grounding_source_count", 0) for row in raw_rows)
        parser_schema_only = bool(not chosen and any_linked and any_content)
        lacks_usable_response = not any_content
        needs_paid_retry = bool(not chosen and not parser_schema_only and category != "genuine_historical_verification_failure")
        entry = {
            "quote_id": quote_id, "quote_hash": record["quote_hash"],
            "latest_state": state, "failure": failure, "category": category,
            "original_recorded_category": original_category,
            "raw_response_count": len(raw_rows), "has_parseable_content": any_content,
            "has_metadata_linked_grounding": any_linked, "recoverable_offline": bool(chosen),
            "parser_or_schema_work_only": parser_schema_only,
            "lacks_usable_response": lacks_usable_response, "paid_retry_candidate": needs_paid_retry,
            "raw_responses": [{key: value for key, value in row.items() if key not in {"packet", "grounding"}}
                              for row in raw_rows],
        }
        taxonomy[quote_id] = entry
        if chosen:
            packet = dict(chosen["packet"])
            packet.update({
                "transport": chosen["transport"], "model": MODEL, "prompt_version": PROMPT_VERSION,
                "grounding_source_count": len(packet["sources"]),
                "completed_at": str((attempts_by_quote.get(quote_id) or [{}])[-1].get("timestamp") or
                                    prior_taxonomy.get(quote_id, {}).get("recorded_at") or "unknown"),
                "offline_recovery": {
                    "schema_version": RECOVERY_SCHEMA_VERSION,
                    "raw_response_path": str(Path(chosen["path"]).relative_to(run_dir)),
                    "raw_response_sha256": chosen["raw_sha256"], "repairs": chosen["repairs"],
                    "original_failure_category": category,
                },
            })
            recovered[quote_id] = {
                "packet": packet, "grounding": chosen["grounding"],
                "source_raw_response": str(Path(chosen["path"]).relative_to(run_dir)),
                "source_raw_sha256": chosen["raw_sha256"], "repairs": chosen["repairs"],
            }

    developer_success_raw = []
    vertex_success_raw = []
    for quote_id, packet in completed.items():
        rows = [inspect_raw_response(path, records[quote_id]) for path in _raw_paths(run_dir, quote_id)]
        target = developer_success_raw if packet.get("transport") == "developer_api" else vertex_success_raw
        target.extend(row for row in rows if row["transport"] == packet.get("transport"))
    comparison = {
        "developer_success": _transport_summary(developer_success_raw),
        "vertex_success": _transport_summary(vertex_success_raw),
        "vertex_failed": _transport_summary([row for row in all_failed_raw if row["transport"] == "vertex_ai"]),
    }
    counts = Counter(entry["category"] for entry in taxonomy.values())
    taxonomy_counts = {category: counts.get(category, 0) for category in TAXONOMY_CATEGORIES}
    latest_states = Counter(entry["latest_state"] for entry in taxonomy.values())
    summary = {
        "schema_version": RECOVERY_SCHEMA_VERSION, "run_id": run_dir.name,
        "manifest_sha256": manifest["manifest_sha256"], "completed_before_recovery": len(original_completed),
        "non_completed_audited": len(taxonomy), "taxonomy_counts": taxonomy_counts,
        "latest_state_counts": dict(sorted(latest_states.items())), "recoverable_offline": len(recovered),
        "parser_or_schema_work_only": sum(row["parser_or_schema_work_only"] for row in taxonomy.values()),
        "lacking_usable_response": sum(row["lacks_usable_response"] for row in taxonomy.values()),
        "paid_retry_candidates": sum(row["paid_retry_candidate"] for row in taxonomy.values()),
        "genuine_historical_failures": counts.get("genuine_historical_verification_failure", 0),
        "source_coverage_recoverable": (sum(bool(row["packet"]["sources"]) for row in recovered.values()) /
                                        len(recovered) if recovered else 0.0),
    }
    return {"summary": summary, "taxonomy": taxonomy, "recovered": recovered, "comparison": comparison}


def _comparison_markdown(audit: dict[str, Any]) -> str:
    lines = ["# Transport Structure Comparison", "",
             "The comparison uses preserved provider response dumps only; no network access was used.", "",
             "| Cohort | Responses | Parsed packet | Grounding chunks | Linked grounding | Locally valid |",
             "|---|---:|---:|---:|---:|---:|"]
    for label, data in audit["comparison"].items():
        lines.append(f"| {label.replace('_', ' ').title()} | {data['responses']} | "
                     f"{data['with_parsed_packet']} | {data['with_grounding_chunks']} | "
                     f"{data['with_linked_grounding']} | {data['valid_under_recovery_parser']} |")
    lines.extend(["", "Developer responses use camelCase `groundingMetadata`; Vertex SDK dumps use snake_case "
                  "`grounding_metadata`. Both layouts are supported. Failed Vertex responses split into responses "
                  "with normal linked chunks and responses containing only search queries/search-entry metadata. "
                  "A search-entry widget or URL in generated prose is not accepted as grounded evidence.", ""])
    for label, data in audit["comparison"].items():
        lines.append(f"## {label.replace('_', ' ').title()}")
        lines.append("")
        lines.append(f"Layouts: `{json.dumps(data['layouts'], sort_keys=True)}`")
        lines.append("")
        lines.append(f"Observed grounding fields: `{', '.join(data['metadata_fields']) or 'none'}`")
        lines.append("")
    return "\n".join(lines)


def _report_markdown(audit: dict[str, Any], applied: bool) -> str:
    summary = audit["summary"]
    lines = ["# Offline Quote Research Recovery", "",
             f"- Run: `{summary['run_id']}`", f"- Original completed packets: {summary['completed_before_recovery']}",
             f"- Original non-completed records audited: {summary['non_completed_audited']}",
             f"- Recoverable from preserved raw evidence: {summary['recoverable_offline']}",
             f"- Still requiring parser/schema work only: {summary['parser_or_schema_work_only']}",
             f"- Lacking usable response content: {summary['lacking_usable_response']}",
             f"- Paid retry candidates: {summary['paid_retry_candidates']}",
             f"- Genuine confirmed historical failures: {summary['genuine_historical_failures']}",
             f"- Source coverage among recovered packets: {summary['source_coverage_recoverable']:.1%}",
             f"- Applied to main packet collection: {applied}", "", "## Failure Taxonomy", ""]
    for category, count in summary["taxonomy_counts"].items():
        lines.append(f"- {category}: {count}")
    lines.extend(["", "Recovery accepts citations only where preserved provider grounding chunks are linked by "
                  "grounding-support metadata. Model-written URLs and search-entry HTML alone are insufficient. "
                  "Raw responses and the original attempt ledger remain immutable.", ""])
    return "\n".join(lines)


def write_audit_outputs(run_dir: Path, audit: dict[str, Any], applied: bool = False) -> Path:
    output = run_dir / "offline_recovery"
    output.mkdir(parents=True, exist_ok=True)
    taxonomy_payload = {"schema_version": RECOVERY_SCHEMA_VERSION,
                        "summary": audit["summary"], "items": audit["taxonomy"]}
    recovered_payload = {"schema_version": RECOVERY_SCHEMA_VERSION,
                         "items": {key: row["packet"] for key, row in audit["recovered"].items()}}
    recoverable = {"schema_version": RECOVERY_SCHEMA_VERSION,
                   "items": {key: {name: value for name, value in row.items() if name != "packet"}
                             for key, row in audit["recovered"].items()}}
    unresolved = {key: row for key, row in audit["taxonomy"].items() if not row["recoverable_offline"]}
    paid = {key: row for key, row in unresolved.items() if row["paid_retry_candidate"]}
    for name, value in (
        ("failure_taxonomy.json", taxonomy_payload), ("recoverable_from_raw.json", recoverable),
        ("recovered_packets.json", recovered_payload),
        ("still_unresolved.json", {"schema_version": RECOVERY_SCHEMA_VERSION, "items": unresolved}),
        ("paid_retry_candidates.json", {"schema_version": RECOVERY_SCHEMA_VERSION, "items": paid}),
    ):
        atomic_write_json(output / name, value)
    atomic_write_text(output / "transport_structure_comparison.md", _comparison_markdown(audit))
    atomic_write_text(output / "offline_recovery_report.md", _report_markdown(audit, applied))
    return output


def _append_recovery_attempt(path: Path, row: dict[str, Any]) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    atomic_write_text(path, existing + json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def apply_recovery(run_dir: Path, audit: dict[str, Any]) -> dict[str, Any]:
    output = write_audit_outputs(run_dir, audit, applied=False)
    recovered = audit["recovered"]
    if not recovered:
        write_audit_outputs(run_dir, audit, applied=True)
        return {"applied": 0, "idempotent": True}
    packet_path = run_dir / "research_packets.json"
    permanent_path = run_dir / "permanent_failures.json"
    grounding_path = run_dir / "grounding_sources.json"
    state_path = run_dir / "run_state.json"
    status_path = run_dir / "status.json"
    attempts_path = run_dir / "attempts.jsonl"
    raw_root = run_dir / "raw_responses"
    protected_before = {"attempts.jsonl": sha256_file(attempts_path),
                        "raw_responses": _canonical_hash(sorted(
                            (str(path.relative_to(raw_root)), sha256_file(path)) for path in raw_root.rglob("*") if path.is_file()))}
    packets = read_json(packet_path)
    already = set(recovered) & set(packets.get("items", {}))
    to_apply = sorted(set(recovered) - already)
    if not to_apply:
        write_audit_outputs(run_dir, audit, applied=True)
        return {"applied": 0, "idempotent": True, "already_present": len(already)}
    permanent = read_json(permanent_path)
    grounding_db = read_json(grounding_path)
    state = read_json(state_path)
    status = read_json(status_path)
    originals = {path.name: path.read_text(encoding="utf-8") for path in
                 (packet_path, permanent_path, grounding_path, state_path, status_path)}
    before_hashes = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in originals.items()}
    backup = output / "backups" / before_hashes[packet_path.name][:16]
    backup.mkdir(parents=True, exist_ok=True)
    for name, text in originals.items():
        target = backup / name
        if not target.exists():
            atomic_write_text(target, text)
    for quote_id in to_apply:
        row = recovered[quote_id]
        packets["items"][quote_id] = row["packet"]
        grounding_db["items"][quote_id] = {"transport": row["packet"]["transport"], **row["grounding"]}
        permanent["items"].pop(quote_id, None)
        permanent.setdefault("offline_recovery_audit", []).append({
            "quote_id": quote_id, "recovered_at": _utc_now(),
            "source_raw_response": row["source_raw_response"],
            "source_raw_sha256": row["source_raw_sha256"],
        })
        if quote_id in state.get("items", {}):
            state["items"][quote_id].update({"status": "valid", "last_error": None,
                                             "offline_recovered": True, "updated_at": _utc_now()})
    state["updated_at"] = _utc_now()
    valid = len(packets["items"])
    failed = len(permanent["items"])
    status.update({"valid_packets": valid, "permanent_failures": failed, "pending": 0, "active": 0,
                   "remaining_nonterminal": 0, "updated_at": _utc_now()})
    status.setdefault("developer", {})["completed"] = sum(
        row.get("transport") == "developer_api" for row in packets["items"].values())
    status.setdefault("vertex", {})["completed"] = sum(
        row.get("transport") == "vertex_ai" for row in packets["items"].values())
    status.setdefault("resume_plan", {}).update({"completed_will_be_skipped": valid,
                                                  "permanent_failures_will_be_skipped": failed,
                                                  "unfinished_quotes": 0})
    for path, value in ((packet_path, packets), (permanent_path, permanent),
                        (grounding_path, grounding_db), (state_path, state), (status_path, status)):
        atomic_write_json(path, value)
    after_hashes = {path.name: sha256_file(path) for path in
                    (packet_path, permanent_path, grounding_path, state_path, status_path)}
    protected_after = {"attempts.jsonl": sha256_file(attempts_path),
                       "raw_responses": _canonical_hash(sorted(
                           (str(path.relative_to(raw_root)), sha256_file(path)) for path in raw_root.rglob("*") if path.is_file()))}
    if protected_before != protected_after:
        raise RuntimeError("offline recovery modified immutable raw responses or attempt history")
    record = {"schema_version": RECOVERY_SCHEMA_VERSION, "record_kind": "offline_recovery_apply",
              "applied_at": _utc_now(), "quote_ids": to_apply, "applied_count": len(to_apply),
              "backup_dir": str(backup.relative_to(run_dir)), "before_hashes": before_hashes,
              "after_hashes": after_hashes, "immutable_hashes": protected_after}
    _append_recovery_attempt(output / "recovery_attempts.jsonl", record)
    write_audit_outputs(run_dir, audit, applied=True)
    return {"applied": len(to_apply), "idempotent": False, "backup_dir": str(backup),
            "before_hashes": before_hashes, "after_hashes": after_hashes}
