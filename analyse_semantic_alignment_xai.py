#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from semantic_alignment.io import atomic_write_json, atomic_write_text
from semantic_alignment.reporting import write_execution_report
from semantic_alignment.pipeline import (
    CRITIC_PROMPT_VERSION, IMAGE_PROMPT_VERSION, QUOTE_PROMPT_VERSION,
    DEFAULT_MODEL, MODEL_PRICES_TICKS, STAGE_CEILINGS, TOTAL_CEILING,
    CostLedger, XAIClient, build_validation_cases, canonical_inventory, critic_pairs,
    estimate_cost, generated_image_inventory, load_database, make_shortlists,
    pending_images, pending_quotes, quote_inventory, require_execution_approval,
    run_critic, run_image_analysis, run_quote_analysis, run_replay,
)

DEFAULT_SESSION = "simulation_runs/counterfactual_evidence_20x250_20260710"


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Offline semantic-alignment research; xAI is disabled unless explicitly authorised.")
    ap.add_argument("--project-dir", type=Path, default=Path(__file__).resolve().parent)
    ap.add_argument("--research-dir", type=Path, default=Path("semantic_alignment_research"))
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("quote-fingerprints", "image-fingerprints", "critic"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--dry-run", action="store_true")
        cmd.add_argument("--execute-xai", action="store_true")
        cmd.add_argument("--confirm-cost-limit-usd", type=float)
        cmd.add_argument("--max-items", type=int)
        cmd.add_argument("--model", default=DEFAULT_MODEL)
        cmd.add_argument("--resume", action="store_true", help="Resume is always hash-aware; accepted for explicitness.")
        cmd.add_argument("--run-id")
    sub.choices["quote-fingerprints"].add_argument("--new-run", action="store_true")
    sub.choices["quote-fingerprints"].add_argument("--only-quote-hash")
    sub.choices["image-fingerprints"].add_argument("--only-image")
    sub.choices["image-fingerprints"].add_argument("--quarantined", action="store_true")
    sub.choices["critic"].add_argument("--validation-only", action="store_true")
    sub.choices["critic"].add_argument("--all-shortlists", action="store_true")
    validation = sub.add_parser("validation-set"); validation.add_argument("--limit", type=int, default=150); validation.add_argument("--run-id", required=True)
    replay = sub.add_parser("replay")
    replay.add_argument("--candidate-cache", type=Path)
    report = sub.add_parser("report"); report.add_argument("--run-id", required=True)
    status = sub.add_parser("run-status"); status.add_argument("--run-id", required=True)
    sub.add_parser("dry-run")
    return ap


def paths(args: argparse.Namespace) -> tuple[Path, Path]:
    project = args.project_dir.expanduser().resolve()
    research = args.research_dir.expanduser()
    if not research.is_absolute(): research = project / research
    research.mkdir(parents=True, exist_ok=True)
    return project, research


def load_all(research: Path):
    quote = load_database(research / "quote_semantic_fingerprints.json", "quote_semantic_fingerprint_database", QUOTE_PROMPT_VERSION)
    image = load_database(research / "image_implied_messages_generated.json", "image_implied_message_database", IMAGE_PROMPT_VERSION)
    critic = load_database(research / "semantic_alignment_critic.json", "semantic_alignment_critic_database", CRITIC_PROMPT_VERSION)
    validation = json.loads((research / "manual_validation_cases.json").read_text()) if (research / "manual_validation_cases.json").exists() else {"items": []}
    return quote, image, critic, validation


def new_run(research: Path) -> tuple[str, Path]:
    run_id = "v2_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run = research / "runs" / run_id
    run.mkdir(parents=True, exist_ok=False)
    atomic_write_json(run / "run_manifest.json", {"schema_version": 2, "run_id": run_id,
        "status": "resumable", "created_at": datetime.now(timezone.utc).isoformat(),
        "model": DEFAULT_MODEL, "quote_prompt_version": QUOTE_PROMPT_VERSION,
        "image_prompt_version": IMAGE_PROMPT_VERSION, "critic_prompt_version": CRITIC_PROMPT_VERSION})
    CostLedger(run / "cost_ledger.json", run_id=run_id)
    return run_id, run


def selected_run(research: Path, args: argparse.Namespace) -> tuple[str, Path]:
    if getattr(args, "new_run", False):
        if getattr(args, "run_id", None): raise RuntimeError("--new-run and --run-id are mutually exclusive")
        run_id, run = new_run(research); print(f"new_run_id={run_id}")
        return run_id, run
    run_id = getattr(args, "run_id", None)
    if not run_id: raise RuntimeError("--run-id is required unless quote-fingerprints uses --new-run")
    run = research / "runs" / run_id
    if not (run / "run_manifest.json").exists(): raise RuntimeError(f"Unknown run id: {run_id}")
    return run_id, run


def run_status(run: Path) -> dict:
    manifest = json.loads((run / "run_manifest.json").read_text())
    ledger = json.loads((run / "cost_ledger.json").read_text()) if (run / "cost_ledger.json").exists() else {}
    state = "blocked_ambiguous_cost" if ledger.get("blocked") else manifest.get("status", "resumable")
    return {"run_id": manifest["run_id"], "status": state, "resumable": state == "resumable",
            "known_cost_usd": ledger.get("total_cost_usd", 0), "blocked_reason": ledger.get("blocked_reason")}


def verify_pricing(research: Path, model: str) -> dict:
    path = research / "pricing" / "models_response.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    selected = next((item for item in data.get("data", []) if item.get("id") == model), None)
    if model != DEFAULT_MODEL or selected is None:
        raise RuntimeError(f"Expected authenticated model metadata for {DEFAULT_MODEL}; got {model}")
    expected = {
        "prompt_text_token_price": MODEL_PRICES_TICKS["input"],
        "prompt_image_token_price": MODEL_PRICES_TICKS["image_input"],
        "cached_prompt_text_token_price": MODEL_PRICES_TICKS["cached_input"],
        "completion_text_token_price": MODEL_PRICES_TICKS["output"],
    }
    mismatches = {key: (selected.get(key), value) for key, value in expected.items() if selected.get(key) != value}
    if mismatches:
        raise RuntimeError(f"Authenticated model pricing differs from approved rates: {mismatches}")
    result = {"verified": True, "model": model, "model_metadata": {key: selected[key] for key in ("id", *expected)}, "usd_ticks_per_dollar": 10_000_000_000, "input_usd_per_million": 2.0, "output_usd_per_million": 6.0, "search_tools_enabled": False, "reasoning_effort": "low", "max_completion_tokens": 1000}
    atomic_write_json(research / "pricing" / "pricing_verification.json", result)
    return result


def preflight(project: Path, research: Path) -> dict:
    quote_db, image_db, critic_db, validation = load_all(research)
    quotes = len(pending_quotes(quote_inventory(project), quote_db))
    images = len(pending_images(generated_image_inventory(project), image_db))
    pairs = critic_pairs([], validation)
    critic = sum(f"{q}:{i}" not in critic_db["items"] for q, i in pairs)
    estimate = estimate_cost(quotes=quotes, images=images, critic=critic or 150)
    for row in estimate["stages"]:
        if row["estimated_stage_cost_usd"] > STAGE_CEILINGS[row["stage"]]:
            raise RuntimeError(f"Preflight {row['stage']} estimate exceeds stage ceiling")
    if estimate["cumulative_estimated_cost_usd"] > TOTAL_CEILING:
        raise RuntimeError("Preflight estimate exceeds total ceiling")
    atomic_write_json(research / "preflight_estimate.json", estimate)
    return estimate


def dry_summary(project: Path, research: Path) -> dict:
    quote_db, image_db, critic_db, validation = load_all(research)
    quotes, images = quote_inventory(project), generated_image_inventory(project)
    shortlists = make_shortlists(quote_db, image_db, validation) if quote_db["items"] and image_db["items"] else []
    pairs = critic_pairs(shortlists, validation)
    session = project / DEFAULT_SESSION
    projected_shortlists = len(quotes) * 15
    projected_critic = max(len(pairs), projected_shortlists + len(validation.get("items", [])))
    return {
        "quotes_total": len(quotes), "quotes_needing_analysis": len(pending_quotes(quotes, quote_db)),
        "active_generated_images": len(images), "images_needing_analysis": len(pending_images(images, image_db)),
        "cached_quote_fingerprints": len(quote_db["items"]), "cached_image_fingerprints": len(image_db["items"]),
        "shortlisted_pairs_materialised": len(shortlists), "projected_shortlisted_pairs": projected_shortlists,
        "critic_pairs_needing_analysis_materialised": sum(f"{q}:{i}" not in critic_db["items"] for q, i in pairs),
        "projected_critic_calls_conservative": projected_critic,
        "cost": estimate_cost(quotes=len(pending_quotes(quotes, quote_db)), images=len(pending_images(images, image_db)), critic=projected_critic),
        "canonical": canonical_inventory(session),
    }


def main(argv=None) -> int:
    args = parser().parse_args(argv); project, research = paths(args)
    if args.command == "run-status":
        active = research / "runs" / args.run_id
        archived = research / "archive" / args.run_id / "archive_manifest.json"
        if active.exists(): result = run_status(active)
        elif archived.exists(): result = json.loads(archived.read_text())
        else: raise RuntimeError(f"Unknown run id: {args.run_id}")
        print(json.dumps(result, indent=2)); return 0
    if args.command == "dry-run":
        quote_db, image_db, critic_db, validation = load_all(research)
        summary = dry_summary(project, research)
        atomic_write_json(research / "dry_run_summary.json", summary)
        print(json.dumps(summary, indent=2)); return 0
    run_id, run = selected_run(research, args)
    quote_db, image_db, critic_db, validation = load_all(run)
    if args.command == "report":
        write_execution_report(run)
        manifest = json.loads((run / "run_manifest.json").read_text()); manifest["status"] = "complete"; manifest["completed_at"] = datetime.now(timezone.utc).isoformat(); atomic_write_json(run / "run_manifest.json", manifest)
        print(json.dumps(run_status(run), indent=2)); return 0
    if args.command == "validation-set":
        result = build_validation_cases(project, project / DEFAULT_SESSION, limit=args.limit)
        active = {row["image_basename"] for row in generated_image_inventory(project)}
        result["items"] = [row for row in result["items"] if row.get("image_basename") in active]
        if len(result["items"]) != args.limit: raise RuntimeError(f"Only {len(result['items'])} active unique validation pairs available")
        atomic_write_json(run / "manual_validation_cases.json", result)
        print(f"validation_cases={len(result['items'])}"); return 0
    if args.command == "quote-fingerprints":
        pending = pending_quotes(quote_inventory(project), quote_db); estimate = estimate_cost(quotes=len(pending[:args.max_items]))
        if args.only_quote_hash: pending = [row for row in pending if row["quote_hash"] == args.only_quote_hash]; estimate = estimate_cost(quotes=len(pending[:args.max_items]))
        print(json.dumps(estimate, indent=2))
        if args.dry_run or not args.execute_xai: return 0
        verify_pricing(research, args.model); preflight(project, run)
        require_execution_approval(estimate, execute_xai=args.execute_xai, cost_limit=args.confirm_cost_limit_usd)
        client = XAIClient(api_key=os.getenv("XAI_API_KEY", ""), model=args.model)
        run_quote_analysis(pending, quote_db, run / "quote_semantic_fingerprints.json", client, ledger=CostLedger(run / "cost_ledger.json"), confirmed_stage_limit=args.confirm_cost_limit_usd, max_items=args.max_items); return 0
    if args.command == "image-fingerprints":
        image_output = run / ("image_implied_messages_quarantined.json" if args.quarantined else "image_implied_messages_generated.json")
        if args.quarantined: image_db = load_database(image_output, "image_implied_message_database", IMAGE_PROMPT_VERSION)
        pending = pending_images(generated_image_inventory(project, quarantined=args.quarantined), image_db)
        if args.only_image: pending = [row for row in pending if row["image_basename"] == args.only_image]
        estimate = estimate_cost(images=len(pending[:args.max_items]))
        print(json.dumps(estimate, indent=2))
        if args.dry_run or not args.execute_xai: return 0
        verify_pricing(research, args.model); preflight(project, run)
        require_execution_approval(estimate, execute_xai=args.execute_xai, cost_limit=args.confirm_cost_limit_usd)
        client = XAIClient(api_key=os.getenv("XAI_API_KEY", ""), model=args.model)
        run_image_analysis(pending, image_db, image_output, client, ledger=CostLedger(run / "cost_ledger.json"), confirmed_stage_limit=args.confirm_cost_limit_usd, max_items=args.max_items); return 0
    if args.command == "critic":
        shortlists = make_shortlists(quote_db, image_db, validation); pairs = critic_pairs(shortlists, validation)
        validation_pairs = critic_pairs([], validation)
        if not args.all_shortlists:
            pairs = validation_pairs
        pending = [pair for pair in pairs if f"{pair[0]}:{pair[1]}" not in critic_db["items"]]
        estimate = estimate_cost(critic=len(pending[:args.max_items])); print(json.dumps(estimate, indent=2))
        if args.dry_run or not args.execute_xai: return 0
        if not args.validation_only and not args.all_shortlists:
            raise RuntimeError("Real critic execution requires --validation-only or explicit --all-shortlists")
        verify_pricing(research, args.model); preflight(project, run)
        require_execution_approval(estimate, execute_xai=args.execute_xai, cost_limit=args.confirm_cost_limit_usd)
        client = XAIClient(api_key=os.getenv("XAI_API_KEY", ""), model=args.model)
        run_critic(pending, quote_db, image_db, critic_db, run / "semantic_alignment_critic.json", client, ledger=CostLedger(run / "cost_ledger.json"), confirmed_stage_limit=args.confirm_cost_limit_usd, max_items=args.max_items); return 0
    if args.command == "replay":
        if args.candidate_cache is None:
            print(json.dumps(canonical_inventory(project / DEFAULT_SESSION), indent=2)); return 0
        path = args.candidate_cache if args.candidate_cache.is_absolute() else project / args.candidate_cache
        run_replay(path, critic_db, research / "semantic_alignment_replay.json"); return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
