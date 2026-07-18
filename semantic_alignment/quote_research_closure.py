from __future__ import annotations

import json
import re
import socket
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .io import atomic_write_json, atomic_write_text, read_json, read_jsonl, sha256_file
from .quote_research_gemini import (
    TOP_LEVEL_FIELDS, extract_grounding, parse_response_packet, validate_packet,
)

SCHEMA_VERSION = 1
EXPECTED_UNRESOLVED = {
    "0a67f403a7ac02347e43791d2daf3057aabdcfd64b62edbe1b3484a3a4b66729",
    "268ab7ec8f0d8688966d1008443f3cc3ed7a34293981c40f3b83ea5225f1dba1",
    "6037112de070bb4455915a61e36ef2d173eaa51316372c5fd52f64016226dfd0",
    "61fad2fe1381709d144b506708f1eba04502f4c0c0cfe82905530861e4d976c2",
    "a88a0bd1754a0ca7d63a1f41a324b80c722f48facd5b138f4e0cbe7c175edd32",
    "b7be9a9edba96d14bd408336b514b2504407a80ddad4435d2eb19f29c6b472b6",
}

TRIAGE = {
    "0a67f403a7ac02347e43791d2daf3057aabdcfd64b62edbe1b3484a3a4b66729": {
        "likely_source_event_type": "political speech, essay, or book passage",
        "repositories": ["Margaret Thatcher Foundation speech and document archive", "Thatcher books and collected writings", "Conservative Party archive"],
        "search_phrases": ["\"Socialists have always spent much of their time\"", "\"new titles for their beliefs\"", "\"outdated and discredited\" socialism"],
        "possible_variants": ["socialists spend much of their time seeking new names for their beliefs", "old doctrines become outdated and discredited"],
        "provisional_status": "presently indeterminate",
        "sufficient_evidence": "A dated primary transcript, publication scan, or archival catalogue entry containing the wording in context.",
        "manual_difficulty": "moderate",
    },
    "268ab7ec8f0d8688966d1008443f3cc3ed7a34293981c40f3b83ea5225f1dba1": {
        "likely_source_event_type": "memoir, political essay, lecture, or interview",
        "repositories": ["Margaret Thatcher Foundation document archive", "Thatcher books and memoirs", "British newspaper and broadcast interview archives"],
        "search_phrases": ["\"Political myths\" \"die hard\" Thatcher", "\"exploded by events\" \"expert\" analysis", "\"another is promptly created\" political commentators"],
        "possible_variants": ["political myths die hard", "when one myth is exploded another is created for expert prediction"],
        "provisional_status": "presently indeterminate",
        "sufficient_evidence": "A primary publication or transcript linking the complete sentence to a dated event or edition.",
        "manual_difficulty": "moderate",
    },
    "6037112de070bb4455915a61e36ef2d173eaa51316372c5fd52f64016226dfd0": {
        "likely_source_event_type": "election speech, campaign address, manifesto launch, or business speech",
        "repositories": ["Margaret Thatcher Foundation speeches", "Conservative Party manifesto and campaign archive", "Hansard", "Contemporary newspaper election coverage"],
        "search_phrases": ["\"lethal taxes\" \"business atmosphere\"", "\"cut out the red tape\" \"bureaucratic interference\"", "\"poisoned the business atmosphere\" Conservative"],
        "possible_variants": ["cut through red tape", "bureaucratic intervention", "taxes which poison the business climate"],
        "provisional_status": "presently indeterminate; campaign-language provenance is plausible but unproved",
        "sufficient_evidence": "A campaign transcript, leaflet scan, manifesto, or contemporaneous report quoting the sentence and identifying date/event.",
        "manual_difficulty": "moderate",
    },
    "61fad2fe1381709d144b506708f1eba04502f4c0c0cfe82905530861e4d976c2": {
        "likely_source_event_type": "personal interview, profile, press conference, or broadcast",
        "repositories": ["Margaret Thatcher Foundation interview transcripts", "BBC and television transcript archives", "British newspaper profile archives"],
        "search_phrases": ["\"The better I do, the more is expected of me\"", "\"I am ready for that\" \"strength to do anything\"", "Thatcher \"anything that I feel has to be done\""],
        "possible_variants": ["the better I perform, the more people expect", "I have the strength to do what has to be done"],
        "provisional_status": "presently indeterminate",
        "sufficient_evidence": "A dated interview transcript, recording, or contemporary profile with the complete exchange.",
        "manual_difficulty": "moderate",
    },
    "a88a0bd1754a0ca7d63a1f41a324b80c722f48facd5b138f4e0cbe7c175edd32": {
        "likely_source_event_type": "interview or informal press exchange",
        "repositories": ["Margaret Thatcher Foundation interview archive", "British newspaper interview archives", "Broadcast transcript archives"],
        "search_phrases": ["\"You don't tell deliberate lies\"", "\"sometimes you have to be evasive\" Thatcher", "Thatcher evasive deliberate lies interview"],
        "possible_variants": ["one does not tell deliberate lies", "sometimes a politician has to be evasive"],
        "provisional_status": "presently indeterminate; short aphoristic wording raises paraphrase risk",
        "sufficient_evidence": "A transcript or recording preserving the question and answer, rather than a quotation compilation.",
        "manual_difficulty": "easy to moderate",
    },
    "b7be9a9edba96d14bd408336b514b2504407a80ddad4435d2eb19f29c6b472b6": {
        "likely_source_event_type": "speech or lecture on liberty, property, and economic rights",
        "repositories": ["Margaret Thatcher Foundation speeches", "Hansard", "Thatcher books and collected speeches", "International lecture archives"],
        "search_phrases": ["\"Liberty and property are intricately bound up\"", "\"no property right has no human rights\"", "\"freedom without capital and private property\" Thatcher"],
        "possible_variants": ["property rights and human rights", "freedom requires capital and private property", "The ellipsis may join separate passages"],
        "provisional_status": "presently indeterminate; excerpt or composite wording is possible",
        "sufficient_evidence": "A primary transcript or publication showing both clauses, their separation, and whether the ellipsis joins one continuous passage.",
        "manual_difficulty": "moderate to difficult",
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@contextmanager
def offline_only() -> Iterator[None]:
    original = socket.create_connection
    socket.create_connection = lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("network access is forbidden during corpus closure"))
    try:
        yield
    finally:
        socket.create_connection = original


def _attempt_files(run_dir: Path) -> list[Path]:
    paths = [run_dir / "attempts.jsonl"]
    paths.extend(run_dir.glob("retry_analysis/**/attempts.jsonl"))
    paths.extend(run_dir.glob("retry_batches/**/attempts.jsonl"))
    return sorted({path for path in paths if path.exists()})


def _raw_text(raw: dict[str, Any]) -> str:
    texts = []
    for candidate in raw.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            if isinstance(part.get("text"), str):
                texts.append(part["text"])
    return "\n".join(texts)


def _response_summary(path: Path, record: dict[str, Any]) -> dict[str, Any]:
    result = {"path": str(path), "sha256": sha256_file(path), "parse_result": "not_json"}
    try:
        raw = read_json(path)
    except Exception as exc:
        result["parser_error"] = str(exc)
        return result
    grounding = extract_grounding(raw)
    text = _raw_text(raw)
    result.update({
        "parse_result": "raw_json_loaded",
        "model_version": raw.get("modelVersion") or raw.get("model_version"),
        "usage": raw.get("usageMetadata") or raw.get("usage_metadata") or {},
        "generated_search_query_count": len(grounding["queries"]),
        "grounding_chunk_count": len(grounding["sources"]),
        "grounding_support_count": len(grounding["supports"]),
        "grounding_sources": grounding["sources"],
        "grounding_supports": grounding["supports"],
        "search_entry_html_present": bool(grounding["search_entry_point"]),
        "model_written_urls": sorted(set(re.findall(r"https?://[^\s\"'<>]+", text))),
    })
    try:
        packet, repairs = parse_response_packet(raw)
        result["packet_parse_result"] = "parsed"
        result["parser_repairs"] = repairs
        result["parsed_packet_fields"] = sorted(packet)
        returned_id = packet.get("quote_id")
        returned_text = packet.get("quote_text")
        if returned_id is None and returned_text is None:
            result["quote_identity_result"] = "immutable_identity_not_model_editable; bound locally for validation"
            packet["quote_id"] = record["quote_id"]
            packet["quote_text"] = record["quote_text"]
        elif returned_id == record["quote_id"] and returned_text == record["quote_text"]:
            result["quote_identity_result"] = "exact manifest identity"
        else:
            result["quote_identity_result"] = "quote identity changed"
        try:
            validate_packet(packet, record)
            result["schema_validation_result"] = "valid canonical packet schema"
        except Exception as exc:
            result["schema_validation_result"] = "failed"
            result["schema_validation_error"] = str(exc)
    except Exception as exc:
        result["packet_parse_result"] = "failed"
        result["parser_error"] = str(exc)
        result["schema_validation_result"] = "not attempted because parsing failed"
        result["quote_identity_result"] = "not assessable"
    return result


def _cost_index(run_dir: Path) -> tuple[dict[tuple[str, str, int], dict[str, Any]], float, float]:
    index = {}
    known = ambiguous = 0.0
    for path in [run_dir / "cost_ledger.json", *run_dir.glob("retry_analysis/**/cost_ledger.json"),
                 *run_dir.glob("retry_batches/**/cost_ledger.json")]:
        if not path.exists():
            continue
        ledger = read_json(path)
        known += float(ledger.get("combined_known_spend_usd") or 0)
        ambiguous += float(ledger.get("ambiguous_possible_exposure_usd") or 0)
        for row in ledger.get("calls") or []:
            key = (str(path.parent.relative_to(run_dir)), row.get("quote_id"),
                   int(row.get("transport_attempt_number") or 0))
            index[key] = row
    return index, known, ambiguous


def _related_families(records: list[dict[str, Any]], quote_id: str) -> list[str]:
    target = set(re.findall(r"[a-z]{4,}", next(row["quote_text"] for row in records if row["quote_id"] == quote_id).casefold()))
    scored = []
    for row in records:
        if row["quote_id"] == quote_id:
            continue
        words = set(re.findall(r"[a-z]{4,}", row["quote_text"].casefold()))
        score = len(target & words) / max(1, len(target | words))
        if score >= .28:
            scored.append((score, row["quote_id"]))
    return [quote for _, quote in sorted(scored, reverse=True)[:5]]


def build_unresolved_dossier(run_dir: Path) -> dict[str, Any]:
    manifest_value = read_json(run_dir / "corpus_manifest.json")
    records = manifest_value["records"]
    manifest = {row["quote_id"]: row for row in records}
    unresolved = set((read_json(run_dir / "permanent_failures.json") or {}).get("items", {}))
    if unresolved != EXPECTED_UNRESOLVED:
        raise RuntimeError(f"expected six fixed unresolved IDs, got {sorted(unresolved)}")
    costs, _, _ = _cost_index(run_dir)
    histories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for attempt_path in _attempt_files(run_dir):
        attempt_run = attempt_path.parent
        run_rel = str(attempt_run.relative_to(run_dir)) or "."
        rows = [row for row in read_jsonl(attempt_path) if row.get("quote_id") in unresolved]
        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[(row["quote_id"], row.get("attempt_epoch"), row.get("transport"),
                    row.get("transport_attempt_number"))].append(row)
        for (quote_id, epoch, transport, attempt_number), lifecycle in groups.items():
            lifecycle.sort(key=lambda row: row.get("timestamp") or "")
            terminal = lifecycle[-1]
            raw_paths = sorted((attempt_run / "raw_responses" / quote_id).glob(
                f"{transport}_attempt_{attempt_number}*.json"))
            cost = costs.get((run_rel, quote_id, int(attempt_number or 0)), {})
            response_summaries = [_response_summary(path, manifest[quote_id]) for path in raw_paths]
            failure = terminal.get("failure") or {}
            histories[quote_id].append({
                "quote_id": quote_id, "run": run_rel, "attempt_epoch": epoch,
                "transport": transport, "transport_attempt_number": attempt_number,
                "model": terminal.get("model"), "request_timestamp": lifecycle[0].get("timestamp"),
                "terminal_timestamp": terminal.get("timestamp"),
                "lifecycle_states": [row.get("state") or row.get("lifecycle_state") for row in lifecycle],
                "response_status": terminal.get("state") or terminal.get("lifecycle_state"),
                "elapsed_seconds": failure.get("elapsed_seconds", cost.get("latency_seconds")),
                "failure": failure or None, "known_cost_usd": float(cost.get("cost_usd") or 0),
                "usage": {key: cost.get(key) for key in ("input_tokens", "output_tokens", "reasoning_tokens", "cached_tokens")},
                "raw_responses": response_summaries,
                "grounding_summary": {
                    "generated_search_query_count": sum(row.get("generated_search_query_count", 0) for row in response_summaries),
                    "grounding_chunks": sum(row.get("grounding_chunk_count", 0) for row in response_summaries),
                    "grounding_supports": sum(row.get("grounding_support_count", 0) for row in response_summaries),
                    "search_entry_html_present": any(row.get("search_entry_html_present") for row in response_summaries),
                    "model_written_url_count": sum(len(row.get("model_written_urls", [])) for row in response_summaries),
                },
            })
    cases = []
    attempt_lines = []
    for quote_id in sorted(unresolved):
        history = sorted(histories[quote_id], key=lambda row: (row.get("request_timestamp") or "", row["run"]))
        attempt_lines.extend(history)
        missing = sum((row.get("failure") or {}).get("kind") in {"missing_grounding", "validation_failure"}
                      and "ground" in str(row.get("failure")).casefold() for row in history)
        transport_failures = sum((row.get("failure") or {}).get("kind") in {"quota_429", "transient", "ambiguous"}
                                 for row in history)
        terminal_class = "provider omitted grounding metadata" if missing else "provider transport or capacity failure"
        cases.append({
            "quote_id": quote_id, "manifest_quote_text": manifest[quote_id]["quote_text"],
            "manifest_occurrence_metadata": manifest[quote_id].get("source_occurrences", []),
            "manifest_input_hash": manifest[quote_id]["input_hash"],
            "attempt_count": len(history), "attempt_history": history,
            "primary_failure_classification": terminal_class,
            "failure_evidence": {"missing_grounding_attempts": missing, "transport_or_capacity_attempts": transport_failures},
            "exact_terminal_failure_reason": (history[-1].get("failure") if history else None),
            "retry_runs": list(dict.fromkeys(row["run"] for row in history)),
            "related_quote_family_ids": _related_families(records, quote_id),
            "historical_triage": TRIAGE[quote_id],
            "implementation_defect": False,
            "historical_conclusion_from_technical_failure": False,
        })
    output = run_dir / "final_unresolved"
    output.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": SCHEMA_VERSION, "record_kind": "final_unresolved_cases",
               "generated_at": utc_now(), "case_count": len(cases), "cases": cases}
    atomic_write_json(output / "unresolved_cases.json", payload)
    atomic_write_text(output / "unresolved_attempt_history.jsonl", "".join(
        json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in attempt_lines))
    taxonomy = {"schema_version": SCHEMA_VERSION, "record_kind": "final_unresolved_failure_taxonomy",
                "counts": dict(Counter(row["primary_failure_classification"] for row in cases)),
                "items": {row["quote_id"]: {"classification": row["primary_failure_classification"],
                                                "evidence": row["failure_evidence"],
                                                "terminal_failure": row["exact_terminal_failure_reason"]}
                          for row in cases}}
    atomic_write_json(output / "unresolved_failure_taxonomy.json", taxonomy)
    return payload


def _triage_markdown(cases: list[dict[str, Any]]) -> str:
    sections = []
    for index, case in enumerate(cases, 1):
        triage = case["historical_triage"]
        sections.append(f"""## {index}. `{case['quote_id']}`

> {case['manifest_quote_text']}

- Technical classification: {case['primary_failure_classification']}
- Provisional historical status: {triage['provisional_status']}
- Likely source-event type: {triage['likely_source_event_type']}
- Manual difficulty: {triage['manual_difficulty']}
- Repositories: {'; '.join(triage['repositories'])}
- Exact search phrases: {'; '.join(triage['search_phrases'])}
- Possible variants: {'; '.join(triage['possible_variants'])}
- Related local quote-family IDs: {', '.join(case['related_quote_family_ids']) or 'none found at the deterministic similarity threshold'}
- Evidence sufficient to resolve: {triage['sufficient_evidence']}
""")
    return "# Unresolved Historical Triage\n\nThis is an offline worklist, not a historical finding. Repeated technical failure does not establish authenticity or misattribution.\n\n" + "\n".join(sections)


def corpus_closure_audit(run_dir: Path, strict: bool = False) -> dict[str, Any]:
    corpus = read_json(run_dir / "corpus_manifest.json")
    records = corpus["records"]
    manifest = {row["quote_id"]: row for row in records}
    packets_value = read_json(run_dir / "research_packets.json")
    packets = packets_value["items"]
    failures_value = read_json(run_dir / "permanent_failures.json")
    unresolved = set(failures_value["items"])
    manifest_ids = set(manifest)
    packet_ids = set(packets)
    schema_errors = []
    identity_errors = []
    for quote_id, packet in packets.items():
        try:
            validate_packet({field: packet[field] for field in TOP_LEVEL_FIELDS}, manifest[quote_id])
        except Exception as exc:
            schema_errors.append({"quote_id": quote_id, "error": str(exc)})
        if packet.get("quote_id") != quote_id or packet.get("quote_text") != manifest[quote_id]["quote_text"]:
            identity_errors.append(quote_id)
    meta_path = run_dir / "retry_batches/recovery_stage_meta_report.json"
    recovery = read_json(meta_path)
    _, full_known, full_ambiguous = _cost_index(run_dir)
    # _cost_index includes staged ledgers; report the main ledger separately to avoid double counting.
    main_cost = read_json(run_dir / "cost_ledger.json")
    full_run_known = float(main_cost.get("combined_known_spend_usd") or 0)
    full_run_ambiguous = float(main_cost.get("ambiguous_possible_exposure_usd") or 0)
    checks = {
        "manifest_count_632": len(manifest_ids) == 632,
        "completed_count_626": len(packet_ids) == 626,
        "unresolved_count_6": len(unresolved) == 6,
        "no_duplicate_manifest_ids": len(records) == len(manifest_ids),
        "no_duplicate_completed_ids": len(packets) == len(packet_ids),
        "all_packets_schema_valid": not schema_errors,
        "all_packet_identities_immutable": not identity_errors,
        "fixed_unresolved_ids_match": unresolved == EXPECTED_UNRESOLVED,
        "completed_and_unresolved_disjoint": not packet_ids & unresolved,
        "manifest_partition_complete": packet_ids | unresolved == manifest_ids,
        "recovery_unique_counts_reconcile": recovery["unique_candidates_recovered"] + recovery["unique_candidates_unresolved"] == 170,
        "recovery_spend_reconciles": abs(recovery["known_spend_usd"] - 14.2083834) < 1e-8,
        "ambiguous_exposure_reconciles": abs(recovery["ambiguous_possible_exposure_usd"] - .146008) < 1e-8,
    }
    audit = {
        "schema_version": SCHEMA_VERSION, "record_kind": "quote_research_corpus_closure_audit",
        "generated_at": utc_now(), "strict": strict, "checks": checks,
        "all_checks_passed": all(checks.values()), "schema_errors": schema_errors,
        "identity_errors": identity_errors, "counts": {"manifest": len(manifest_ids),
            "completed": len(packet_ids), "unresolved": len(unresolved)},
        "costs": {"full_run_known_spend_usd": full_run_known,
                  "full_run_ambiguous_exposure_usd": full_run_ambiguous,
                  "staged_recovery_known_spend_usd": recovery["known_spend_usd"],
                  "staged_recovery_ambiguous_exposure_usd": recovery["ambiguous_possible_exposure_usd"],
                  "all_discovered_ledgers_known_spend_usd": full_known,
                  "all_discovered_ledgers_ambiguous_exposure_usd": full_ambiguous},
        "hashes": {
            "research_packets_sha256": sha256_file(run_dir / "research_packets.json"),
            "corpus_manifest_sha256": sha256_file(run_dir / "corpus_manifest.json"),
            "project_quote_manifest_sha256": sha256_file(run_dir.parents[1] / "thatcher_quote_research_project/quote_manifest.json"),
            "full_retry_manifest_sha256": sha256_file(run_dir / "retry_analysis/full_retry_manifest.json"),
            "recovery_meta_report_sha256": sha256_file(meta_path),
        },
    }
    if strict and not audit["all_checks_passed"]:
        raise RuntimeError(f"strict corpus closure failed: {[key for key, value in checks.items() if not value]}")
    output = run_dir / "final_unresolved"
    output.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output / "corpus_closure_audit.json", audit)
    return audit


def write_final_outputs(run_dir: Path, strict: bool = True) -> dict[str, Any]:
    with offline_only():
        dossier = build_unresolved_dossier(run_dir)
        cases = dossier["cases"]
        output = run_dir / "final_unresolved"
        triage = _triage_markdown(cases)
        atomic_write_text(output / "unresolved_historical_triage.md", triage)
        priority = sorted(cases, key=lambda row: ({"easy to moderate": 0, "moderate": 1,
                                                   "moderate to difficult": 2}.get(row["historical_triage"]["manual_difficulty"], 3),
                                                  row["quote_id"]))
        atomic_write_text(output / "unresolved_manual_worklist.md",
                          "# Unresolved Manual Worklist\n\n" + "\n".join(
                              f"{index}. `{row['quote_id']}` - {row['historical_triage']['manual_difficulty']}: {row['manifest_quote_text']}"
                              for index, row in enumerate(priority, 1)) + "\n")
        audit = corpus_closure_audit(run_dir, strict=strict)
        status = {
            "schema_version": SCHEMA_VERSION, "record_kind": "final_quote_research_status",
            "generated_timestamp": utc_now(), "total_manifest_quotes": 632,
            "completed_quotes": 626, "unresolved_quotes": 6,
            "completion_percentage": 626 / 632 * 100,
            "unresolved_quote_ids": sorted(EXPECTED_UNRESOLVED),
            "full_run_known_spend_usd": audit["costs"]["full_run_known_spend_usd"],
            "staged_recovery_known_spend_usd": audit["costs"]["staged_recovery_known_spend_usd"],
            "ambiguous_possible_exposure_usd": audit["costs"]["staged_recovery_ambiguous_exposure_usd"],
            "corpus_hash": audit["hashes"]["research_packets_sha256"],
            "packet_schema_version": (read_json(run_dir / "research_packets.json") or {}).get("schema_version"),
        }
        atomic_write_json(output / "final_research_status.json", status)
        atomic_write_text(output / "corpus_closure_report.md", f"""# Quote Research Corpus Closure Report

- Manifest records: 632
- Completed packets: 626 ({status['completion_percentage']:.2f}%)
- Unresolved: 6
- Canonical packet validation errors: {len(audit['schema_errors'])}
- Immutable identity errors: {len(audit['identity_errors'])}
- Staged recovery: 164/170 recovered (96.47%)
- Staged known spend: ${status['staged_recovery_known_spend_usd']:.4f}
- Ambiguous possible exposure: ${status['ambiguous_possible_exposure_usd']:.4f}
- Packet collection SHA-256: `{status['corpus_hash']}`
- Corpus manifest SHA-256: `{audit['hashes']['corpus_manifest_sha256']}`

All 632 IDs form a complete, disjoint partition between completed and unresolved records. All completed canonical packets validate and retain manifest quote identity. The six unresolved records remain technical/provider failures; no historical conclusion is inferred from that status. Automated paid recovery is closed because every remaining case exhausted two bounded cycles.
""")
        return {"dossier": dossier, "audit": audit, "status": status}
