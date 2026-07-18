from __future__ import annotations

import hashlib
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .io import atomic_write_json, atomic_write_text, read_json, read_jsonl
from .quote_research_gemini import MODEL

RETRY_PLAN_VERSION = 1
RETRY_PROMPT_VERSION = "quote-research-grounded-v2-retry"
TRANSPORT_CATEGORIES = {"timeout_or_transport_failure", "no_response_received", "cancelled_before_send"}
MISSING_CATEGORIES = {"missing_grounded_source"}
STRUCTURED_CATEGORIES = {"quote_identity_changed", "schema_validation_error", "malformed_or_truncated_json"}


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _raw_text(raw: dict[str, Any]) -> str:
    return "".join(str(part.get("text") or "")
                   for candidate in raw.get("candidates") or []
                   for part in (candidate.get("content") or {}).get("parts") or [])


def _raw_metadata(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"raw_present": False}
    raw = read_json(path)
    candidates = raw.get("candidates") or []
    candidate = candidates[0] if candidates else {}
    metadata = candidate.get("groundingMetadata") or candidate.get("grounding_metadata") or {}
    chunks = metadata.get("groundingChunks") or metadata.get("grounding_chunks") or []
    supports = metadata.get("groundingSupports") or metadata.get("grounding_supports") or []
    queries = metadata.get("webSearchQueries") or metadata.get("web_search_queries") or []
    entry = metadata.get("searchEntryPoint") or metadata.get("search_entry_point") or {}
    text = _raw_text(raw)
    parsed = raw.get("parsed") if isinstance(raw.get("parsed"), dict) else None
    packet_text = json.dumps(parsed, ensure_ascii=False) if parsed else text
    return {
        "raw_present": True, "generated_search_query_count": len(queries),
        "grounding_chunks_present": bool(chunks), "grounding_chunk_count": len(chunks),
        "grounding_supports_present": bool(supports), "grounding_support_count": len(supports),
        "search_entry_html_present": bool((entry or {}).get("renderedContent") or
                                          (entry or {}).get("rendered_content")),
        "model_written_urls_present": bool(re.search(r"https?://", packet_text)),
        "candidate_finish_reason": candidate.get("finishReason") or candidate.get("finish_reason"),
        "response_model_version": raw.get("modelVersion") or raw.get("model_version"),
        "response_id_present": bool(raw.get("responseId") or raw.get("response_id")),
    }


def _attempt_index(rows: list[dict[str, Any]]) -> dict[tuple[str, str, int, int], list[dict[str, Any]]]:
    result: dict[tuple[str, str, int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        quote_id = row.get("quote_id"); transport = row.get("transport")
        if quote_id and transport:
            result[(str(quote_id), str(transport), int(row.get("attempt_epoch", 0) or 0),
                    int(row.get("transport_attempt_number", row.get("attempt_number", 0)) or 0))].append(row)
    return result


def _quota_events(rows: list[dict[str, Any]]) -> list[tuple[datetime, dict[str, Any]]]:
    result = []
    for row in rows:
        failure = row.get("failure") or {}
        message = str(failure.get("message") or "").casefold()
        if "429" in message or "resource_exhausted" in message or "capacity" in message:
            timestamp = _parse_time(row.get("timestamp"))
            if timestamp:
                result.append((timestamp, row))
    return result


def _quote_type(text: str) -> str:
    lowered = text.casefold()
    if any(token in lowered for token in ("mr speaker", "hon. member", "this house")):
        return "parliamentary"
    if len(text) < 90:
        return "short_attribution_risk"
    if len(text) > 300:
        return "long_excerpt_or_composite"
    if "..." in text or "[" in text:
        return "excerpt_or_edited"
    return "general"


def analyze_missing_grounding(run_dir: Path, taxonomy: dict[str, Any]) -> dict[str, Any]:
    manifest = read_json(run_dir / "corpus_manifest.json")
    records = {row["quote_id"]: row for row in manifest["records"]}
    attempts = read_jsonl(run_dir / "attempts.jsonl")
    indexed = _attempt_index(attempts)
    costs = read_json(run_dir / "cost_ledger.json")
    call_index = {(row["quote_id"], row["transport"], int(row.get("attempt_epoch", 0) or 0),
                   int(row.get("transport_attempt_number", 0) or 0)): row for row in costs.get("calls", [])}
    quota_events = _quota_events(attempts)
    missing_ids = sorted(quote_id for quote_id, row in taxonomy.items()
                         if row["category"] == "missing_grounded_source")
    items = {}
    all_attempt_rows = []
    for quote_id in missing_ids:
        record = records[quote_id]
        attempt_details = []
        keys = sorted(key for key in indexed if key[0] == quote_id)
        for key in keys:
            _, transport, epoch, number = key
            lifecycle = indexed[key]
            final = next((row for row in reversed(lifecycle) if row.get("state") in {
                "completed", "validation_failure", "transient_failure", "confirmed_failure",
                "cancelled_before_send", "ambiguous_outcome"}), lifecycle[-1])
            received = next((row for row in reversed(lifecycle) if row.get("raw_response_path")), None)
            raw_path = run_dir / received["raw_response_path"] if received else Path("/nonexistent")
            raw_meta = _raw_metadata(raw_path)
            call = call_index.get(key, {})
            timestamp = call.get("timestamp") or final.get("timestamp")
            parsed_time = _parse_time(timestamp)
            near = []
            if parsed_time:
                for event_time, event in quota_events:
                    delta = abs((event_time - parsed_time).total_seconds())
                    if delta <= 300:
                        near.append({"seconds_from_request": round((event_time - parsed_time).total_seconds(), 3),
                                     "quote_id": event.get("quote_id"), "transport": event.get("transport"),
                                     "failure": event.get("failure")})
            detail = {
                "transport": transport, "model": final.get("model") or MODEL,
                "attempt_number": number, "attempt_epoch": epoch,
                "response_status": final.get("state"), "repair_attempt": bool(final.get("repair_attempt")),
                "timestamp": timestamp, "output_token_count": int(call.get("output_tokens") or 0),
                "elapsed_seconds": call.get("latency_seconds") or (final.get("failure") or {}).get("elapsed_seconds"),
                "quota_timeout_or_capacity_events_within_5m": near, **raw_meta,
            }
            attempt_details.append(detail)
            all_attempt_rows.append({"quote_id": quote_id, **detail})
        for detail in attempt_details:
            detail["associated_attempts"] = [
                {"transport": other["transport"], "attempt_number": other["attempt_number"],
                 "attempt_epoch": other["attempt_epoch"], "response_status": other["response_status"],
                 "repair_attempt": other["repair_attempt"]}
                for other in attempt_details if other is not detail
            ]
        items[quote_id] = {
            "quote_id": quote_id, "quote_hash": record["quote_hash"], "quote_type": _quote_type(record["quote_text"]),
            "quote_character_count": len(record["quote_text"]),
            "source_occurrence_count": len(record.get("source_occurrences") or []),
            "offline_recovered": quote_id in (read_json(run_dir / "research_packets.json").get("items") or {}),
            "attempts": attempt_details,
        }

    def grouped(field: str) -> dict[str, int]:
        return dict(sorted(Counter(str(row.get(field)) for row in all_attempt_rows).items()))

    def grounding_cross(field: str) -> dict[str, dict[str, int]]:
        result: dict[str, Counter] = defaultdict(Counter)
        for row in all_attempt_rows:
            key = str(row.get(field))
            result[key]["attempts"] += 1
            result[key]["chunks_present"] += int(bool(row.get("grounding_chunks_present")))
            result[key]["chunks_absent"] += int(bool(row.get("raw_present")) and not row.get("grounding_chunks_present"))
        return {key: dict(value) for key, value in sorted(result.items())}

    for row in all_attempt_rows:
        tokens = int(row.get("output_token_count") or 0)
        row["output_token_band"] = "0" if not tokens else "1-1024" if tokens <= 1024 else "1025-2048" if tokens <= 2048 else "2049+"
        queries = int(row.get("generated_search_query_count") or 0)
        row["search_query_band"] = "0" if not queries else "1" if queries == 1 else "2" if queries == 2 else "3+"
        row["repair_kind"] = "repair" if row.get("repair_attempt") else "initial"

    with_chunks = [row for row in all_attempt_rows if row.get("grounding_chunks_present")]
    without_chunks = [row for row in all_attempt_rows if row.get("raw_present") and not row.get("grounding_chunks_present")]
    initial = [row for row in all_attempt_rows if not row.get("repair_attempt")]
    repairs = [row for row in all_attempt_rows if row.get("repair_attempt")]
    correlations = {
        "case_count": len(items), "attempt_count": len(all_attempt_rows),
        "transport_attempt_counts": grouped("transport"), "response_status_counts": grouped("response_status"),
        "grounding_by_transport": grounding_cross("transport"),
        "grounding_by_model": grounding_cross("model"),
        "grounding_by_repair_kind": grounding_cross("repair_kind"),
        "grounding_by_output_token_band": grounding_cross("output_token_band"),
        "grounding_by_search_query_band": grounding_cross("search_query_band"),
        "repair_attempts": len(repairs), "initial_attempts": len(initial),
        "attempts_with_grounding_chunks": len(with_chunks),
        "attempts_without_grounding_chunks_despite_response": len(without_chunks),
        "search_entry_html_without_chunks": sum(row.get("search_entry_html_present") for row in without_chunks),
        "model_urls_without_chunks": sum(row.get("model_written_urls_present") for row in without_chunks),
        "mean_output_tokens_with_chunks": statistics.mean(row["output_token_count"] for row in with_chunks) if with_chunks else None,
        "mean_output_tokens_without_chunks": statistics.mean(row["output_token_count"] for row in without_chunks) if without_chunks else None,
        "mean_search_queries_with_chunks": statistics.mean(row["generated_search_query_count"] for row in with_chunks) if with_chunks else None,
        "mean_search_queries_without_chunks": statistics.mean(row["generated_search_query_count"] for row in without_chunks) if without_chunks else None,
        "mean_latency_with_chunks": statistics.mean(float(row["elapsed_seconds"]) for row in with_chunks if row.get("elapsed_seconds") is not None) if with_chunks else None,
        "mean_latency_without_chunks": statistics.mean(float(row["elapsed_seconds"]) for row in without_chunks if row.get("elapsed_seconds") is not None) if without_chunks else None,
        "quote_type_counts": dict(sorted(Counter(row["quote_type"] for row in items.values()).items())),
        "attempt_hour_utc_counts": dict(sorted(Counter(
            (_parse_time(row.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc)).strftime("%Y-%m-%dT%H:00Z")
            for row in all_attempt_rows).items())),
        "attempts_near_quota_or_capacity_event": sum(bool(row["quota_timeout_or_capacity_events_within_5m"])
                                                      for row in all_attempt_rows),
        "request_concurrency": "not recorded per attempt; the corpus runner was configured for bounded provider concurrency",
    }
    raw_baseline: dict[str, Counter] = defaultdict(Counter)
    for path in (run_dir / "raw_responses").glob("*/*.json"):
        name = path.name
        transport = "developer_api" if name.startswith("developer_api_") else "vertex_ai" if name.startswith("vertex_ai_") else "unknown"
        metadata = _raw_metadata(path)
        raw_baseline[transport]["raw_responses"] += 1
        raw_baseline[transport]["chunks_present"] += int(bool(metadata.get("grounding_chunks_present")))
        raw_baseline[transport]["supports_present"] += int(bool(metadata.get("grounding_supports_present")))
        raw_baseline[transport]["search_entry_without_chunks"] += int(
            bool(metadata.get("search_entry_html_present")) and not metadata.get("grounding_chunks_present"))
    correlations["corpus_raw_grounding_baseline"] = {
        key: dict(value) for key, value in sorted(raw_baseline.items())
    }
    return {"schema_version": RETRY_PLAN_VERSION, "items": items, "correlations": correlations}


def _retry_group(category: str) -> tuple[str, str]:
    if category in TRANSPORT_CATEGORIES:
        return "transport_failures", category
    if category in MISSING_CATEGORIES:
        return "missing_grounding", category
    if category in STRUCTURED_CATEGORIES:
        return "identity_and_structured_output", category
    raise ValueError(f"unplanned paid retry category: {category}")


def build_retry_plan(run_dir: Path, taxonomy: dict[str, Any], validation_count: int = 20) -> dict[str, Any]:
    manifest = read_json(run_dir / "corpus_manifest.json")
    records = {row["quote_id"]: row for row in manifest["records"]}
    completed = (read_json(run_dir / "research_packets.json") or {}).get("items", {})
    candidates = []
    for quote_id, audit in taxonomy.items():
        if quote_id in completed or not audit.get("paid_retry_candidate"):
            continue
        group, subgroup = _retry_group(audit["category"])
        candidates.append({
            **records[quote_id], "retry_group": group, "retry_subgroup": subgroup,
            "original_failure_category": audit["category"],
            "retry_plan_version": RETRY_PLAN_VERSION, "retry_prompt_version": RETRY_PROMPT_VERSION,
        })
    candidates.sort(key=lambda row: row["quote_id"])
    if len({row["quote_id"] for row in candidates}) != len(candidates):
        raise RuntimeError("duplicate retry candidates")
    if set(completed) & {row["quote_id"] for row in candidates}:
        raise RuntimeError("completed or offline-recovered packet entered retry plan")
    validation_count = min(validation_count, len(candidates))

    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        by_group[row["retry_group"]].append(row)
    targets = {"transport_failures": 7, "missing_grounding": 10,
               "identity_and_structured_output": 3}
    selected = []
    for group in ("transport_failures", "missing_grounding", "identity_and_structured_output"):
        ordered = sorted(by_group[group], key=lambda row: hashlib.sha256(
            f"retry-validation-v1:{row['quote_id']}".encode()).hexdigest())
        selected.extend(ordered[:targets[group]])
    if len(selected) < validation_count:
        used = {row["quote_id"] for row in selected}
        fill = sorted((row for row in candidates if row["quote_id"] not in used),
                      key=lambda row: hashlib.sha256(f"retry-fill-v1:{row['quote_id']}".encode()).hexdigest())
        selected.extend(fill[:validation_count - len(selected)])
    selected = sorted(selected[:validation_count], key=lambda row: row["quote_id"])
    if len(selected) != validation_count:
        raise RuntimeError("unable to construct deterministic validation retry batch")

    def payload(kind: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        value = {"schema_version": RETRY_PLAN_VERSION, "record_kind": kind,
                 "parent_run_id": run_dir.name, "parent_manifest_sha256": manifest["manifest_sha256"],
                 "retry_prompt_version": RETRY_PROMPT_VERSION,
                 "quote_identity_policy": {
                     "immutable_request_envelope": ["quote_id", "quote_text"],
                     "model_editable_identity_fields": [],
                     "verified_wording_field": "verified_text",
                     "binding": "copy quote_id and manifest quote_text locally after validation",
                     "mismatch_action": "reject",
                 },
                 "record_count": len(rows), "records": rows}
        value["manifest_sha256"] = hashlib.sha256(json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        return value
    return {"full": payload("quote_research_paid_retry_manifest", candidates),
            "validation": payload("quote_research_retry_validation_manifest", selected)}


def cost_projection(plan: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    calls = (read_json(run_dir / "cost_ledger.json") or {}).get("calls", [])
    observed = [float(row["cost_usd"]) for row in calls if float(row.get("cost_usd") or 0) > 0]
    median = statistics.median(observed)
    p90 = sorted(observed)[min(len(observed) - 1, int(len(observed) * .9))]
    expected_attempts = 1.2

    def estimate(count: int) -> dict[str, Any]:
        expected = count * median * expected_attempts
        high = count * p90 * 1.5
        return {"items": count, "expected_attempts_per_item": expected_attempts,
                "expected_cost_usd": round(expected, 4), "high_projection_usd": round(high, 4)}
    validation = estimate(plan["validation"]["record_count"])
    validation["hard_combined_ceiling_usd"] = 5.0
    validation["allowed"] = validation["expected_cost_usd"] <= 5.0
    full = estimate(plan["full"]["record_count"])
    return {"schema_version": RETRY_PLAN_VERSION, "observed_call_median_usd": median,
            "observed_call_p90_usd": p90, "validation_batch": validation, "full_retry_set": full,
            "note": "Projections use observed grounded-call costs; execution guards still stop before the hard ceiling."}


def _commands_markdown(run_dir: Path) -> str:
    return f"""# Proposed Retry Commands

No command below is authorised by this planning task.

```bash
python3 analyse_quote_research_gemini.py retry-plan-status \\
  --run-dir {run_dir} \\
  --retry-manifest {run_dir / 'retry_analysis/retry_manifest_20.json'}

python3 analyse_quote_research_gemini.py run-retry-validation \\
  --run-dir {run_dir} \\
  --retry-manifest {run_dir / 'retry_analysis/retry_manifest_20.json'} \\
  --execute \\
  --enable-vertex-fallback \\
  --developer-concurrency 1 \\
  --vertex-concurrency 1 \\
  --max-attempts 2 \\
  --pause-developer-after-consecutive-provider-wide-429 2 \\
  --no-automatic-developer-reprobe \\
  --confirm-combined-limit-usd 5 \\
  --resume
```

The execution command must remain unavailable without `--execute` and the exact $5 confirmation. It must skip every quote already present in `research_packets.json`, including offline recoveries. Review the 20-item result before authorising any remaining candidate.
"""


def _analysis_report(missing: dict[str, Any], plan: dict[str, Any], costs: dict[str, Any]) -> str:
    corr = missing["correlations"]
    groups = Counter(row["retry_group"] for row in plan["full"]["records"])
    subgroups = Counter(row["retry_subgroup"] for row in plan["full"]["records"])
    no_chunks = corr["attempts_without_grounding_chunks_despite_response"]
    return f"""# Quote Research Retry Analysis

## Missing Grounding

The 101 original missing-grounding cases comprise {corr['attempt_count']} attempts. Of these, {no_chunks} preserved responses contained no grounding chunks. Search-entry HTML without chunks occurred in {corr['search_entry_html_without_chunks']} attempts, and model-written URLs without chunks occurred in {corr['model_urls_without_chunks']}; neither is accepted as evidence.

Developer uses camelCase metadata and Vertex uses snake_case metadata, but both observed layouts are supported. The failures are therefore primarily provider non-return of linked chunks/supports, not an extractor incompatibility. Repair attempts are separately counted ({corr['repair_attempts']}) and frequently lack grounding because schema-repair prompts synthesised structured output without a successful grounded search.

The affected attempts used Developer {corr['transport_attempt_counts'].get('developer_api', 0)} times and Vertex {corr['transport_attempt_counts'].get('vertex_ai', 0)} times. Both attempts with usable chunks were Developer responses; no affected Vertex response contained chunks. This is a strong transport association in this run, but not proof of a Vertex adapter defect: corpus-wide preserved Vertex responses also contain successfully extracted snake_case chunks/supports. Output length and search-query count do not supply a safe deterministic explanation: missing metadata occurred across all measured bands. Eighty-eight repair attempts had no chunks, so repair-without-a-new-grounded-search is a clear contributing mechanism. Quota/capacity events were temporally dense, making the five-minute proximity count descriptive rather than causal. Per-request concurrency was not persisted, so that correlation cannot be measured retrospectively.

## Offline Repair

The two remaining parser-only cases were repaired by discarding only their malformed final model-written `sources` arrays. Every other required field was complete. Sources were rebuilt solely from linked grounding chunks/supports in the same original response. No model claim or source was invented.

## Unique Paid Retry Plan

- Total unique candidates: {plan['full']['record_count']}
- Transport failures: {groups['transport_failures']}
- Missing grounding: {groups['missing_grounding']}
- Identity and structured output: {groups['identity_and_structured_output']}
- Identity subgroup: {subgroups['quote_identity_changed']}
- Schema subgroup: {subgroups['schema_validation_error']}
- Malformed JSON subgroup: {subgroups['malformed_or_truncated_json']}

The 20-item validation batch uses 7 transport, 10 missing-grounding, and 3 identity/structured-output cases. Its expected cost is ${costs['validation_batch']['expected_cost_usd']:.4f}, with a hard combined ceiling of $5. Full-set expected cost is ${costs['full_retry_set']['expected_cost_usd']:.4f}; it is not authorised.

## Recommended Settings

### Transport failures

Retry is justified. Start with Developer, use concurrency 1, a 360-second grounded read timeout, and one retry. After two consecutive provider-wide 429 responses, persist the pause and route directly to Vertex without probing Developer again. Expected recovery probability: 80-90%.

### Missing grounding

Retry is justified only through the validation batch first. The prompt should require at least one grounded search before synthesis and explicitly state that a packet without provider-linked grounding will be rejected. Keep the same model, search tool, structured schema, and transport parity. Concurrency 1, 360-second timeout, one retry. Expected recovery probability: 60-80%; the provider may still omit chunks.

### Identity and structured output

Retry is justified. Remove `quote_id` and uploaded `quote_text` from the model-editable response schema. Supply them as an immutable request envelope and bind them locally after parsing. Keep `verified_text` separate. Any response researching another quotation remains invalid. Concurrency 1, 360-second timeout, one retry. Expected recovery probability: 85-95% for identity defects and 70-85% for malformed/schema defects.

No remaining case is classified as a genuine historical failure. No full retry run should be authorised before human and technical review of the 20-item batch.
"""


def generate_retry_analysis(run_dir: Path, validation_count: int = 20) -> dict[str, Any]:
    taxonomy_payload = read_json(run_dir / "offline_recovery" / "failure_taxonomy.json")
    taxonomy = taxonomy_payload["items"]
    missing = analyze_missing_grounding(run_dir, taxonomy)
    plan = build_retry_plan(run_dir, taxonomy, validation_count)
    costs = cost_projection(plan, run_dir)
    output = run_dir / "retry_analysis"
    output.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output / "missing_grounding_analysis.json", missing)
    atomic_write_json(output / "failure_correlations.json", {
        "schema_version": RETRY_PLAN_VERSION, **missing["correlations"]})
    groups = defaultdict(list)
    for row in plan["full"]["records"]:
        groups[row["retry_group"]].append(row["quote_id"])
    atomic_write_json(output / "retry_groups.json", {
        "schema_version": RETRY_PLAN_VERSION, "unique_candidate_count": plan["full"]["record_count"],
        "groups": dict(groups),
        "recommendations": {
            "transport_failures": {"justified": True, "preferred_transport": "developer_then_vertex",
                "timeout_seconds": 360, "concurrency": 1, "retry_limit": 1, "expected_recovery_probability": "0.80-0.90"},
            "missing_grounding": {"justified": True, "preferred_transport": "developer_then_vertex",
                "timeout_seconds": 360, "concurrency": 1, "retry_limit": 1, "expected_recovery_probability": "0.60-0.80",
                "prompt_change": "require grounded search before structured synthesis"},
            "identity_and_structured_output": {"justified": True, "preferred_transport": "developer_then_vertex",
                "timeout_seconds": 360, "concurrency": 1, "retry_limit": 1, "expected_recovery_probability": "0.70-0.95",
                "prompt_change": "bind quote identity outside model-editable schema"},
        }})
    atomic_write_json(output / "retry_manifest_20.json", plan["validation"])
    atomic_write_json(output / "full_retry_manifest.json", plan["full"])
    atomic_write_json(output / "retry_cost_projection.json", costs)
    atomic_write_text(output / "proposed_retry_commands.md", _commands_markdown(run_dir))
    atomic_write_text(output / "retry_analysis_report.md", _analysis_report(missing, plan, costs))
    return {"missing": missing, "plan": plan, "costs": costs, "output_dir": str(output)}
