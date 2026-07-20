#!/usr/bin/env python3
"""Produce a network-free evaluation of the AI-first reply architecture."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from reply_evidence import EvidenceRepository
from reply_strategy import MODES, STRATEGY_VERSION, deterministic_reply_error


USAGE_TICKS_RE = re.compile(r"cost_in_usd_ticks['\"]?\s*:\s*(\d+)")
TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
CANDIDATE_RE = re.compile(r"\b(?:mention|quote tweet|hot post reply)\s+(?:id=)?(\d{3,})", re.IGNORECASE)
USD_TICKS_PER_DOLLAR = 10_000_000_000


def sha256_file(path: Path) -> str:
    """Return a file SHA-256 without modifying the source."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    """Write deterministic JSON through an atomic same-directory replacement."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def atomic_text(path: Path, value: str) -> None:
    """Write text through an atomic same-directory replacement."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def stable_read(path: Path, attempts: int = 5) -> bytes:
    """Read a possibly mutable local file only when its metadata remains stable."""
    for _attempt in range(attempts):
        before = path.stat()
        content = path.read_bytes()
        after = path.stat()
        if (
            before.st_size == after.st_size == len(content)
            and before.st_mtime_ns == after.st_mtime_ns
            and before.st_ino == after.st_ino
        ):
            return content
        time.sleep(0.02)
    raise RuntimeError(f"could not obtain a stable read of {path}")


def percentile(values: list[float], quantile: float) -> float | None:
    """Return a nearest-rank percentile for a non-empty sample."""
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * quantile))))
    return ordered[index]


def scan_logs(paths: list[Path]) -> dict[str, Any]:
    """Collect bounded metadata and usage figures without retaining user text."""
    candidates: set[str] = set()
    usage_ticks: list[int] = []
    timestamps: list[str] = []
    lines_scanned = 0
    for path in paths:
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                lines_scanned += 1
                timestamp = TIMESTAMP_RE.match(line)
                if timestamp:
                    timestamps.append(timestamp.group(1))
                candidate = CANDIDATE_RE.search(line)
                if candidate:
                    candidates.add(candidate.group(1))
                usage = USAGE_TICKS_RE.search(line)
                if usage:
                    usage_ticks.append(int(usage.group(1)))
    return {
        "log_paths": [str(path) for path in paths if path.is_file()],
        "lines_scanned": lines_scanned,
        "coverage_start": min(timestamps) if timestamps else None,
        "coverage_end": max(timestamps) if timestamps else None,
        "unique_recent_candidate_count": len(candidates),
        "candidate_id_hashes": sorted(
            hashlib.sha256(candidate.encode("ascii")).hexdigest() for candidate in candidates
        ),
        "usage_call_count": len(usage_ticks),
        "usage_ticks_median": statistics.median(usage_ticks) if usage_ticks else None,
        "usage_ticks_mean": statistics.fmean(usage_ticks) if usage_ticks else None,
    }


def evaluate(project_dir: Path) -> dict[str, Any]:
    """Evaluate fixtures, saved history and local evidence retrieval without a model."""
    adversarial_path = project_dir / "tests/fixtures/ai_first_reply_adversarial_cases.json"
    valid_path = project_dir / "tests/fixtures/ai_first_reply_valid_cases.json"
    state_path = project_dir / "bot_state.json"
    research_dir = project_dir / "semantic_alignment_research/quote_research_full_001"
    adversarial = json.loads(adversarial_path.read_text(encoding="utf-8"))
    valid = json.loads(valid_path.read_text(encoding="utf-8"))
    state_bytes = stable_read(state_path)
    state = json.loads(state_bytes.decode("utf-8"))
    history = state.get("reply_strategy_history", [])
    if not isinstance(history, list):
        raise ValueError("reply_strategy_history must be a list")

    repository = EvidenceRepository(
        research_dir,
        factual_evidence_path=project_dir / "reply_factual_evidence.json",
    )
    old_mode_counts = Counter(
        str(item.get("mode") or "unavailable")
        for item in history
        if isinstance(item, dict)
    )
    historical_checks = []
    for item in history:
        if not isinstance(item, dict):
            continue
        reply = str(item.get("reply_text") or "")
        if not reply:
            continue
        proposal = {
            "proposed_reply": reply,
            "exact_thatcher_wording_used": False,
            "exact_thatcher_wording": "",
        }
        reason = deterministic_reply_error(
            proposal,
            repository,
            recent_replies=[],
            maximum_reply_length=270,
            maximum_sentences=2,
        )
        historical_checks.append({
            "target_id_hash": hashlib.sha256(str(item.get("target_id") or "").encode("utf-8")).hexdigest(),
            "reply_hash": hashlib.sha256(reply.encode("utf-8")).hexdigest(),
            "old_mode": str(item.get("mode") or "unavailable"),
            "deterministic_envelope": "pass" if reason is None else "reject",
            "reason": reason,
        })

    retrieval_queries = [
        "Berlin Wall movement East Germany West Germany",
        "inflation government economic policy",
        "Gorbachev Soviet relations diplomacy",
        "trade unions employment law",
        "European Community sovereignty",
    ]
    retrieval_latencies: list[float] = []
    retrieval_counts: list[int] = []
    for query in retrieval_queries:
        started = time.perf_counter()
        passages = repository.candidate_passages(query, maximum_packets=6, maximum_passages=24)
        retrieval_latencies.append((time.perf_counter() - started) * 1000)
        retrieval_counts.append(len(passages))

    log_paths = sorted(project_dir.glob("mrsMThatcher.log*"))
    logs = scan_logs(log_paths)
    median_ticks = logs["usage_ticks_median"]
    estimated_cost = {
        "basis": (
            "retained historical xAI cost_in_usd_ticks; "
            f"{USD_TICKS_PER_DOLLAR:,} ticks treated as US$1"
        ),
        "nonfactual_calls": 2,
        "factual_calls": 3,
        "maximum_revision_calls": 6,
        "median_historical_call_usd": (float(median_ticks) / USD_TICKS_PER_DOLLAR) if median_ticks is not None else None,
        "estimated_nonfactual_reply_usd": (2 * float(median_ticks) / USD_TICKS_PER_DOLLAR) if median_ticks is not None else None,
        "estimated_factual_reply_usd": (3 * float(median_ticks) / USD_TICKS_PER_DOLLAR) if median_ticks is not None else None,
        "estimated_maximum_revision_reply_usd": (6 * float(median_ticks) / USD_TICKS_PER_DOLLAR) if median_ticks is not None else None,
        "caveat": "The new prompts and structured outputs were not billed offline; these are historical-call extrapolations, not quotations.",
    }

    return {
        "schema_version": 1,
        "strategy_version": STRATEGY_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "network_calls": 0,
        "model_calls": 0,
        "source_hashes": {
            str(path.relative_to(project_dir)): (
                hashlib.sha256(state_bytes).hexdigest()
                if path == state_path
                else sha256_file(path)
            )
            for path in (adversarial_path, valid_path, state_path)
        },
        "adversarial_fixture": {
            "case_count": len(adversarial),
            "expected_reject_count": sum(item.get("expected") == "reject" for item in adversarial),
            "case_ids": [str(item.get("case_id")) for item in adversarial],
            "execution": "covered by scripted proposer/reviewer pipeline regressions",
        },
        "valid_fixture": {
            "case_count": len(valid),
            "modes": dict(sorted(Counter(str(item.get("mode")) for item in valid).items())),
            "all_modes_valid": all(item.get("mode") in MODES for item in valid),
            "case_ids": [str(item.get("case_id")) for item in valid],
        },
        "saved_historical_replies": {
            "history_count": len(history),
            "old_mode_counts": dict(sorted(old_mode_counts.items())),
            "replies_with_text": len(historical_checks),
            "deterministic_envelope_pass_count": sum(item["deterministic_envelope"] == "pass" for item in historical_checks),
            "deterministic_envelope_reject_count": sum(item["deterministic_envelope"] == "reject" for item in historical_checks),
            "records": historical_checks,
            "semantic_replay_status": "not_run_without_an_offline_model_or_remote_call",
        },
        "recent_structured_logs": logs,
        "local_retrieval": {
            "query_count": len(retrieval_queries),
            "candidate_counts": retrieval_counts,
            "latency_ms_p50": statistics.median(retrieval_latencies),
            "latency_ms_p95": percentile(retrieval_latencies, 0.95),
            "latency_ms_max": max(retrieval_latencies),
        },
        "model_latency": {
            "measured_offline": False,
            "configured_timeout_seconds_per_call": 60,
            "maximum_calls": 6,
            "absolute_timeout_envelope_seconds": 360,
        },
        "estimated_api_cost": estimated_cost,
        "quality_assessment_scope": (
            "Deterministic and scripted safety behaviour was exercised offline. Natural-language quality on recent live candidates "
            "requires a separately authorised non-posting model pilot; no model was contacted by this evaluation."
        ),
    }


def render(result: dict[str, Any]) -> str:
    """Render the compact human-readable evaluation."""
    adversarial = result["adversarial_fixture"]
    valid = result["valid_fixture"]
    history = result["saved_historical_replies"]
    retrieval = result["local_retrieval"]
    cost = result["estimated_api_cost"]
    lines = [
        "# AI-first Reply Strategy Offline Evaluation",
        "",
        f"Strategy: `{result['strategy_version']}`",
        f"Network/model calls: **{result['network_calls']} / {result['model_calls']}**",
        "",
        "## Fixtures",
        "",
        f"- Adversarial cases: {adversarial['case_count']} ({adversarial['expected_reject_count']} expected rejects)",
        f"- Valid cases: {valid['case_count']} across {len(valid['modes'])} modes",
        f"- All valid fixture modes recognised: {valid['all_modes_valid']}",
        "",
        "## Saved History",
        "",
        f"- V1 history records: {history['history_count']}",
        f"- Reply texts within the current deterministic envelope: {history['deterministic_envelope_pass_count']}",
        f"- Reply texts rejected by the current deterministic envelope: {history['deterministic_envelope_reject_count']}",
        f"- Semantic replay: `{history['semantic_replay_status']}`",
        "",
        "## Local Evidence",
        "",
        f"- Retrieval queries: {retrieval['query_count']}",
        f"- Latency p50/p95/max: {retrieval['latency_ms_p50']:.3f} / {retrieval['latency_ms_p95']:.3f} / {retrieval['latency_ms_max']:.3f} ms",
        "",
        "## Cost Estimate",
        "",
        f"- Estimated non-factual reply: {cost['estimated_nonfactual_reply_usd']!r} USD",
        f"- Estimated factual reply: {cost['estimated_factual_reply_usd']!r} USD",
        f"- Six-call revision envelope: {cost['estimated_maximum_revision_reply_usd']!r} USD",
        f"- Caveat: {cost['caveat']}",
        "",
        "## Scope",
        "",
        result["quality_assessment_scope"],
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the offline evaluation CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    project_dir = args.project_dir.resolve()
    output_dir = args.output_dir.resolve()
    result = evaluate(project_dir)
    atomic_json(output_dir / "offline_evaluation.json", result)
    report_path = output_dir / "offline_evaluation.md"
    atomic_text(report_path, render(result))
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
