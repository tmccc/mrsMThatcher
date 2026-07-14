#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
from pathlib import Path

from semantic_alignment.gemini_fallback import verify_adc_access
from semantic_alignment.io import atomic_write_json, atomic_write_text, read_json
from semantic_alignment.quote_research_gemini import (
    COMBINED_LIMIT, DEVELOPER_LIMIT, MAX_QUOTES, VERTEX_LIMIT,
    DeveloperResearchClient, ResearchWorker, VertexResearchClient,
    build_pilot, preflight, report,
)
from semantic_alignment.quote_research_corpus import (
    DEFAULT_COMBINED_LIMIT, DEFAULT_DEVELOPER_LIMIT, DEFAULT_VERTEX_LIMIT,
    CorpusRunner, build_corpus_manifest, corpus_preflight,
    install_signal_handlers, restore_signal_handlers, verify_corpus_manifest,
)

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT / "thatcher_quote_research_project"
DEFAULT_RUN = ROOT / "semantic_alignment_research/quote_research_gemini_pilot_002_20260713"
DEFAULT_CORPUS_RUN = ROOT / "semantic_alignment_research/quote_research_full_001"


def add_execution_flags(command: argparse.ArgumentParser) -> None:
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--execute", action="store_true")
    command.add_argument("--enable-vertex-fallback", action="store_true")
    command.add_argument("--confirm-developer-limit-usd", type=float)
    command.add_argument("--confirm-vertex-limit-usd", type=float)
    command.add_argument("--confirm-combined-limit-usd", type=float)
    command.add_argument("--developer-concurrency", type=int, default=2)
    command.add_argument("--vertex-concurrency", type=int, default=2)
    command.add_argument("--connect-timeout", type=float, default=20)
    command.add_argument("--read-timeout", type=float, default=300)
    command.add_argument("--max-items", type=int)
    command.add_argument("--only-quote-id")
    command.add_argument("--no-write", action="store_true")
    command.add_argument("--reset-permanent-failure")
    command.add_argument("--resume", action="store_true")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-pilot")
    build.add_argument("--count", type=int, default=MAX_QUOTES)
    build.add_argument("--exclude-batch", type=Path, default=PROJECT / "pilot_batch_001.json")
    build.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    corpus = sub.add_parser("build-corpus")
    corpus.add_argument("--manifest", type=Path, default=PROJECT / "quote_manifest.json")
    corpus.add_argument("--run-dir", type=Path, default=DEFAULT_CORPUS_RUN)
    recovery = sub.add_parser("prepare-pilot-recovery")
    recovery.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    status = sub.add_parser("status")
    status.add_argument("--run-dir", type=Path, required=True)
    status.add_argument("--json-status", action="store_true")
    run = sub.add_parser("run")
    run.add_argument("--run-dir", type=Path, required=True)
    add_execution_flags(run)
    retry = sub.add_parser("retry-failures")
    retry.add_argument("--run-dir", type=Path, required=True)
    retry.add_argument("--only-status", choices=("transient_failure", "validation_failure", "interrupted"), required=True)
    add_execution_flags(retry)
    return root


def build_command(args) -> dict:
    run = args.run_dir.resolve()
    manifest = read_json(PROJECT / "quote_manifest.json")
    excluded = read_json(args.exclude_batch.resolve())
    payload = build_pilot(manifest, excluded, args.count)
    run.mkdir(parents=True, exist_ok=True)
    path = run / "pilot_manifest.json"
    if path.exists() and read_json(path) != payload:
        raise RuntimeError("existing immutable pilot manifest differs")
    if not path.exists():
        atomic_write_json(path, payload)
    pf = preflight(payload["records"])
    atomic_write_json(run / "preflight.json", pf)
    report(run, payload)
    print(json.dumps({"run_dir": str(run), "records": payload["record_count"], "manifest_sha256": payload["manifest_sha256"]}, indent=2))
    return payload


def run_command(args) -> int:
    run = args.run_dir.resolve()
    if (run / "corpus_manifest.json").exists():
        return corpus_run_command(args)
    manifest = read_json(run / "pilot_manifest.json")
    pf = preflight(manifest["records"])
    atomic_write_json(run / "preflight.json", pf)
    if args.dry_run:
        print(json.dumps(pf, indent=2))
        return 0
    if not args.execute:
        raise RuntimeError("live execution requires --execute")
    if not args.enable_vertex_fallback:
        raise RuntimeError("this pilot requires --enable-vertex-fallback")
    if (args.confirm_developer_limit_usd, args.confirm_vertex_limit_usd, args.confirm_combined_limit_usd) != (DEVELOPER_LIMIT, VERTEX_LIMIT, COMBINED_LIMIT):
        raise RuntimeError("exact $3 Developer, $3 Vertex and $5 combined confirmations required")
    if not pf["allowed"]:
        raise RuntimeError("preflight cost gate failed")
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    developer = DeveloperResearchClient(key or "")
    vertex_env = verify_adc_access(dict(os.environ))
    vertex = VertexResearchClient(project=vertex_env["project"], location=vertex_env["location"])
    worker = ResearchWorker(run, manifest["records"], developer, vertex,
                            args.confirm_developer_limit_usd, args.confirm_vertex_limit_usd,
                            args.confirm_combined_limit_usd)
    status = worker.run()
    result = report(run, manifest)
    print(json.dumps({"status": status, "report": result}, indent=2))
    return 0


def build_corpus_command(args) -> dict:
    run = args.run_dir.resolve()
    payload = build_corpus_manifest(read_json(args.manifest.resolve()))
    run.mkdir(parents=True, exist_ok=True)
    path = run / "corpus_manifest.json"
    if path.exists() and read_json(path) != payload:
        raise RuntimeError("existing immutable corpus manifest differs")
    if not path.exists():
        atomic_write_json(path, payload)
    pf = corpus_preflight(payload["records"])
    atomic_write_json(run / "preflight.json", pf)
    print(json.dumps({"run_dir": str(run), "records": payload["record_count"],
                      "source_occurrences": payload["source_occurrence_count"],
                      "manifest_sha256": payload["manifest_sha256"], "preflight": pf}, indent=2))
    return payload


def prepare_pilot_recovery(args) -> dict:
    run = args.run_dir.resolve()
    parent = read_json(run / "pilot_manifest.json")
    records = []
    for source in parent["records"]:
        row = dict(source)
        row["input_hash"] = hashlib.sha256(json.dumps({
            "quote_id": row["quote_id"], "quote_hash": row["quote_hash"],
            "quote_text": row["quote_text"], "source_occurrences": row["source_occurrences"],
            "prompt_version": "quote-research-grounded-v1", "schema_version": 1,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        records.append(row)
    payload = {"schema_version": 1, "record_kind": "linked_pilot_recovery_manifest",
               "parent_manifest_sha256": parent["manifest_sha256"],
               "record_count": len(records),
               "source_occurrence_count": sum(len(row["source_occurrences"]) for row in records),
               "records": records}
    payload["manifest_sha256"] = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    path = run / "corpus_manifest.json"
    if path.exists() and read_json(path) != payload:
        raise RuntimeError("existing linked recovery manifest differs")
    if not path.exists():
        atomic_write_json(path, payload)
    print(json.dumps({"run_dir": str(run), "records": len(records),
                      "already_valid": len((read_json(run/'research_packets.json', {}) or {}).get('items', {})),
                      "manifest_sha256": payload["manifest_sha256"]}, indent=2))
    return payload


def read_status(run: Path) -> dict:
    status_path = run / "status.json"
    if status_path.exists():
        payload = read_json(status_path)
        terminal = int(payload.get("valid_packets") or 0) + int(payload.get("permanent_failures") or 0)
        if terminal:
            payload["projected_final_spend_usd"] = (float(payload.get("combined_known_spend_usd") or 0) /
                                                       terminal * int(payload["total_quotes"]))
        return payload
    manifest = read_json(run / "corpus_manifest.json")
    packets = (read_json(run / "research_packets.json", {}) or {}).get("items", {})
    permanent = (read_json(run / "permanent_failures.json", {}) or {}).get("items", {})
    costs = read_json(run / "cost_ledger.json", {}) or {}
    transport = read_json(run / "transport_status.json", {}) or {}
    valid = len(packets); failed = len(permanent); pending = manifest["record_count"]-valid-failed
    developer_completed = sum(row.get("transport") == "developer_api" for row in packets.values())
    vertex_completed = sum(row.get("transport") == "vertex_ai" for row in packets.values())
    known = float(costs.get("combined_known_spend_usd") or 0)
    return {"schema_version": 1, "run_id": run.name, "total_quotes": manifest["record_count"],
            "valid_packets": valid, "pending": pending, "active": 0,
            "transient_failures": 0, "validation_failures": 0, "permanent_failures": failed,
            "remaining_nonterminal": pending,
            "developer": {"completed": developer_completed, "known_spend_usd": float(costs.get("developer_known_spend_usd") or 0)},
            "vertex": {"completed": vertex_completed, "known_spend_usd": float(costs.get("vertex_known_spend_usd") or 0)},
            "combined_known_spend_usd": known,
            "ambiguous_possible_exposure_usd": float(costs.get("ambiguous_possible_exposure_usd") or 0),
            "developer_paused": bool(transport.get("developer_paused")),
            "developer_pause_reason": transport.get("pause_reason"),
            "direct_to_vertex_count": int(transport.get("direct_to_vertex_count") or 0),
            "average_cost_per_valid_quote": known/valid if valid else None,
            "projected_final_spend_usd": known/(valid+failed)*manifest["record_count"] if valid+failed else None,
            "eta_seconds": None, "resume_plan": {"completed_will_be_skipped": valid,
                "permanent_failures_will_be_skipped": failed,
                "unfinished_quotes": pending,
                "next_transport": "vertex_ai" if transport.get("developer_paused") else "developer_api",
                "developer_will_be_probed": not bool(transport.get("developer_paused"))}}


def status_command(args) -> int:
    payload = read_status(args.run_dir.resolve())
    if args.json_status:
        print(json.dumps(payload, indent=2))
        return 0
    print(f"Quote research run: {payload['run_id']}")
    print(f"  total={payload['total_quotes']} valid={payload['valid_packets']} pending={payload['pending']} active={payload['active']}")
    print(f"  transient={payload['transient_failures']} validation={payload['validation_failures']} permanent={payload['permanent_failures']}")
    print(f"  Developer completed={payload['developer']['completed']} spend=${payload['developer']['known_spend_usd']:.4f}")
    print(f"  Vertex completed={payload['vertex']['completed']} spend=${payload['vertex']['known_spend_usd']:.4f}")
    print(f"  Developer paused={payload['developer_paused']} direct-to-Vertex={payload['direct_to_vertex_count']}")
    print(f"  combined spend=${payload['combined_known_spend_usd']:.4f} projected={payload['projected_final_spend_usd']}")
    print(f"  ETA seconds={payload['eta_seconds']}")
    print("  resume plan=" + json.dumps(payload["resume_plan"], sort_keys=True))
    return 0


def write_full_report(run: Path, status: dict) -> None:
    packets = read_json(run / "research_packets.json", {"items": {}})
    costs = read_json(run / "cost_ledger.json", {"calls": []})
    sources = [source for packet in packets["items"].values() for source in packet.get("sources", [])]
    calls = costs.get("calls", [])
    actual = float(status["combined_known_spend_usd"])
    preflight_data = read_json(run / "preflight.json", {})
    lines = ["# Full quote-research execution report", "",
        f"- Manifest: `{run / 'corpus_manifest.json'}`", f"- Total quotes: {status['total_quotes']}",
        f"- Valid packets: {status['valid_packets']}", f"- Permanent failures: {status['permanent_failures']}",
        f"- Remaining nonterminal: {status['remaining_nonterminal']}",
        f"- Developer completions: {status['developer']['completed']}",
        f"- Vertex completions: {status['vertex']['completed']}",
        f"- Developer paused: {status['developer_paused']} ({status.get('developer_pause_reason')})",
        f"- Direct-to-Vertex: {status['direct_to_vertex_count']}",
        f"- Developer spend: ${status['developer']['known_spend_usd']:.6f}",
        f"- Vertex spend: ${status['vertex']['known_spend_usd']:.6f}",
        f"- Combined conservatively reconstructed spend: ${actual:.6f}",
        f"- Ambiguous possible exposure: ${status['ambiguous_possible_exposure_usd']:.6f}",
        f"- Average cost per valid quote: ${status['average_cost_per_valid_quote']:.6f}" if status['average_cost_per_valid_quote'] else "- Average cost per valid quote: unavailable",
        f"- Preflight expected cost: ${float(preflight_data.get('projected_expected_cost_usd') or 0):.6f}",
        f"- Grounded sources retained: {len(sources)}",
        f"- Source coverage among valid packets: {sum(bool(p.get('sources')) for p in packets['items'].values())/max(len(packets['items']),1):.1%}",
        f"- Authoritative usage records: {len(calls)}",
        f"- Mean latency: {statistics.mean(float(c['latency_seconds']) for c in calls):.2f}s" if calls else "- Mean latency: unavailable",
        "", "Completed packets and permanent failures are terminal and skipped on resume. Raw responses, extracted responses, grounding metadata, attempts, usage, and costs remain linked by quote ID and transport attempt."]
    atomic_write_text(run / "quote_research_full_report.md", "\n".join(lines) + "\n")


def corpus_run_command(args) -> int:
    run = args.run_dir.resolve()
    manifest = read_json(run / "corpus_manifest.json")
    verify_corpus_manifest(manifest)
    existing = read_json(run / "research_packets.json", {"items": {}})
    permanent = read_json(run / "permanent_failures.json", {"items": {}})
    terminal_ids = set(existing.get("items", {})) | set(permanent.get("items", {}))
    remaining_records = [row for row in manifest["records"] if row["quote_id"] not in terminal_ids]
    pf = corpus_preflight(manifest["records"], remaining_records=remaining_records)
    prior_costs = read_json(run / "cost_ledger.json", {}) or {}
    pf.update({
        "remaining_developer_ceiling_usd": DEFAULT_DEVELOPER_LIMIT-float(prior_costs.get("developer_known_spend_usd") or 0),
        "remaining_vertex_ceiling_usd": DEFAULT_VERTEX_LIMIT-float(prior_costs.get("vertex_known_spend_usd") or 0),
        "remaining_combined_ceiling_usd": DEFAULT_COMBINED_LIMIT-float(prior_costs.get("combined_known_spend_usd") or 0),
        "developer_concurrency": args.developer_concurrency,
        "vertex_concurrency": args.vertex_concurrency,
        "connect_timeout_seconds": args.connect_timeout,
        "read_timeout_seconds": args.read_timeout,
    })
    if not args.no_write:
        atomic_write_json(run / "preflight.json", pf)
    if args.dry_run or args.no_write:
        print(json.dumps({"preflight": pf, "status": read_status(run)}, indent=2))
        return 0
    if args.reset_permanent_failure and not args.execute:
        runner = CorpusRunner(run, manifest, None, None,
                              args.confirm_developer_limit_usd or DEFAULT_DEVELOPER_LIMIT,
                              args.confirm_vertex_limit_usd or DEFAULT_VERTEX_LIMIT,
                              args.confirm_combined_limit_usd or DEFAULT_COMBINED_LIMIT)
        runner.reset_permanent(args.reset_permanent_failure)
        print(json.dumps(runner.status_payload(), indent=2))
        return 0
    if not args.execute:
        raise RuntimeError("live corpus execution requires --execute")
    if not args.enable_vertex_fallback:
        raise RuntimeError("full corpus execution requires --enable-vertex-fallback")
    confirmations = (args.confirm_developer_limit_usd, args.confirm_vertex_limit_usd, args.confirm_combined_limit_usd)
    if any(value is None or value <= 0 for value in confirmations):
        raise RuntimeError("explicit positive Developer, Vertex and combined cost confirmations are required")
    if (confirmations[0] > DEFAULT_DEVELOPER_LIMIT or confirmations[1] > DEFAULT_VERTEX_LIMIT or
            confirmations[2] > DEFAULT_COMBINED_LIMIT):
        raise RuntimeError(
            "confirmed ceilings exceed the "
            f"${DEFAULT_DEVELOPER_LIMIT:g}/${DEFAULT_VERTEX_LIMIT:g}/${DEFAULT_COMBINED_LIMIT:g} safety maxima"
        )
    if float(pf["projected_expected_cost_usd"]) > confirmations[2]:
        raise RuntimeError("remaining expected cost exceeds the confirmed combined ceiling")
    if not pf["allowed"]:
        raise RuntimeError("expected preflight cost exceeds the combined ceiling")
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    developer = DeveloperResearchClient(key or "", connect_timeout=args.connect_timeout, read_timeout=args.read_timeout)
    vertex_env = verify_adc_access(dict(os.environ))
    vertex = VertexResearchClient(project=vertex_env["project"], location=vertex_env["location"], read_timeout=args.read_timeout)
    runner = CorpusRunner(run, manifest, developer, vertex, *confirmations,
                          developer_concurrency=args.developer_concurrency,
                          vertex_concurrency=args.vertex_concurrency)
    if args.reset_permanent_failure:
        runner.reset_permanent(args.reset_permanent_failure)
    previous = install_signal_handlers(runner)
    try:
        status = runner.run(max_items=args.max_items, only_quote_id=args.only_quote_id,
                            statuses={args.only_status} if getattr(args, "only_status", None) else None)
    finally:
        restore_signal_handlers(previous)
    write_full_report(run, status)
    print(json.dumps(status, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "build-pilot":
        build_command(args)
        return 0
    if args.command == "build-corpus":
        build_corpus_command(args)
        return 0
    if args.command == "prepare-pilot-recovery":
        prepare_pilot_recovery(args)
        return 0
    if args.command == "status":
        return status_command(args)
    return run_command(args)


if __name__ == "__main__":
    raise SystemExit(main())
