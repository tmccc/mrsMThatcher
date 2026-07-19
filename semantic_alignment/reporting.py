"""Summarise semantic-alignment execution ledgers and write reports."""

from __future__ import annotations

import csv
import io
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from .io import atomic_write_json, atomic_write_text, read_json

FREE_TRADE_HASH = "1ae9443573e42259af54c30a0ec90a6a8746e640b09e53ae1c28a4c0a2d0ed6b"
FREE_TRADE_IMAGE = "tg_661b01c39a8d223df51cd0365e79ffe7e3c4f86ac81fa0af114ce95be49cb831.png"


def stage_usage(ledger: dict[str, Any], stage: str) -> dict[str, Any]:
    """Return the stage usage."""
    rows = [row for row in ledger.get("calls", []) if row.get("stage") == stage]
    costs = [float(row.get("cost_usd") or 0) for row in rows]
    return {
        "calls": len(rows), "input_tokens": sum(int(row.get("input_tokens") or 0) for row in rows),
        "cached_tokens": sum(int(row.get("cached_tokens") or 0) for row in rows),
        "reasoning_tokens": sum(int(row.get("reasoning_tokens") or 0) for row in rows),
        "completion_tokens": sum(int(row.get("completion_tokens") or 0) for row in rows),
        "cost_usd": sum(costs), "mean_call_cost_usd": statistics.mean(costs) if costs else 0,
        "median_call_cost_usd": statistics.median(costs) if costs else 0,
        "maximum_call_cost_usd": max(costs, default=0),
        "retry_calls": sum(int(row.get("attempt") or 1) > 1 for row in rows),
        "retry_cost_usd": sum(float(row.get("cost_usd") or 0) for row in rows if int(row.get("attempt") or 1) > 1),
    }


def score_band(score: float) -> str:
    """Score band."""
    if score >= 90: return "90-100 exceptional"
    if score >= 75: return "75-89 strong"
    if score >= 60: return "60-74 indirect"
    if score >= 40: return "40-59 weak"
    return "0-39 mismatch"


def build_execution_summary(research: Path) -> dict[str, Any]:
    """Build execution summary."""
    quote_db = read_json(research / "quote_semantic_fingerprints.json", {}) or {}
    image_db = read_json(research / "image_implied_messages_generated.json", {}) or {}
    critic_db = read_json(research / "semantic_alignment_critic.json", {}) or {}
    validation = read_json(research / "manual_validation_cases.json", {}) or {}
    ledger = read_json(research / "cost_ledger.json", {}) or {}
    critics = list((critic_db.get("items") or {}).values())
    expected = {(row.get("quote_hash"), row.get("image_basename")): row for row in validation.get("items", [])}
    expected_map = {"related_but_indirect": "related_but_not_equivalent", "direct_match": "direct_equivalence"}
    compared = []
    for row in critics:
        manual = expected.get((row.get("quote_hash"), row.get("image_basename")))
        if manual and manual.get("expected_category"):
            normalised = expected_map.get(manual["expected_category"], manual["expected_category"])
            compared.append({"expected": manual["expected_category"], "normalised_expected": normalised, "actual": row.get("claim_relationship"), "agree": normalised == row.get("claim_relationship"), "quote_hash": row["quote_hash"], "image_basename": row["image_basename"]})
    scores = [float(row["semantic_alignment_score"]) for row in critics]
    ordered = sorted(critics, key=lambda row: (-float(row["semantic_alignment_score"]), row["quote_hash"], row["image_basename"]))
    free_key = f"{FREE_TRADE_HASH}:{FREE_TRADE_IMAGE}"
    free_critic = (critic_db.get("items") or {}).get(free_key)
    free_quote = (quote_db.get("items") or {}).get(FREE_TRADE_HASH)
    free_image = (image_db.get("items") or {}).get(FREE_TRADE_IMAGE)
    return {
        "schema_version": 2, "analysis_kind": "semantic_alignment_validation_execution_summary",
        "stages": {
            "quote": {**stage_usage(ledger, "quote"), "successes": len(quote_db.get("items", {})), "failures": len(quote_db.get("failures", {}))},
            "image": {**stage_usage(ledger, "image"), "successes": len(image_db.get("items", {})), "failures": len(image_db.get("failures", {}))},
            "critic": {**stage_usage(ledger, "critic"), "successes": len(critic_db.get("items", {})), "failures": len(critic_db.get("failures", {}))},
        },
        "exact_cumulative_cost_usd": float(ledger.get("total_cost_usd") or 0),
        "score_distribution": dict(sorted(Counter(score_band(score) for score in scores).items())),
        "relationship_distribution": dict(sorted(Counter(row.get("claim_relationship") for row in critics).items())),
        "mismatch_distribution": dict(sorted(Counter(row.get("mismatch_type") for row in critics).items())),
        "score_mean": statistics.mean(scores) if scores else None,
        "score_median": statistics.median(scores) if scores else None,
        "human_comparisons": compared,
        "human_agreement_rate": sum(row["agree"] for row in compared) / len(compared) if compared else None,
        "false_positives": [row for row in compared if row["normalised_expected"] != "direct_equivalence" and row["actual"] == "direct_equivalence"],
        "false_negatives": [row for row in compared if row["normalised_expected"] == "direct_equivalence" and row["actual"] != "direct_equivalence"],
        "uncertain_cases": [row for row in critics if float(row.get("confidence") or 0) < 0.7],
        "top_direct_matches": [row for row in ordered if row.get("claim_relationship") in {"direct_equivalence", "strong_support"}][:10],
        "top_related_but_indirect": [row for row in ordered if row.get("claim_relationship") in {"related_but_not_equivalent", "partial_support", "secondary_theme_only"}][:10],
        "top_semantic_mismatches": sorted(critics, key=lambda row: (float(row["semantic_alignment_score"]), row["quote_hash"], row["image_basename"]))[:10],
        "free_trade_case": {"quote": free_quote, "image": free_image, "critic": free_critic, "human_expected_category": (expected.get((FREE_TRADE_HASH, FREE_TRADE_IMAGE)) or {}).get("expected_category")},
    }


def write_execution_report(research: Path) -> dict[str, Any]:
    """Write execution report."""
    summary = build_execution_summary(research)
    atomic_write_json(research / "validation_execution_summary.json", summary)
    lines = ["# Semantic alignment v2 validation execution report", "", f"Exact cumulative billed cost: `${summary['exact_cumulative_cost_usd']:.6f}`", "", "## Stage usage", ""]
    for stage, row in summary["stages"].items():
        lines.append(f"- **{stage}**: calls={row['calls']}, successes={row['successes']}, failures={row['failures']}, input={row['input_tokens']}, cached={row['cached_tokens']}, reasoning={row['reasoning_tokens']}, completion={row['completion_tokens']}, cost=${row['cost_usd']:.6f}")
    lines += ["", "## Validation", "", f"Score distribution: `{summary['score_distribution']}`", f"Claim relationships: `{summary['relationship_distribution']}`", f"Human expected-category agreement: `{summary['human_agreement_rate']}`", f"False positives: `{len(summary['false_positives'])}`", f"False negatives: `{len(summary['false_negatives'])}`", f"Uncertain cases: `{len(summary['uncertain_cases'])}`", "", "## Free-trade case", ""]
    case = summary["free_trade_case"]
    if case["critic"]:
        lines.extend([f"- Quote core claim: {case['quote']['core_claim']}", f"- Quote primary claims: {[x['claim'] for x in case['quote']['claims'] if x['importance'] == 'primary']}", f"- Image core implied claim: {case['image']['core_implied_claim']}", f"- Image dominant implied claims: {[x['claim'] for x in case['image']['implied_claims'] if x['salience'] == 'dominant']}", f"- Matched claims: {case['critic']['matched_claims']}", f"- Unillustrated quote claims: {case['critic']['unillustrated_primary_claims']}", f"- Extraneous image claims: {case['critic']['image_claims_not_required_by_quote']}", f"- Claim relationship: {case['critic']['claim_relationship']}", f"- Alignment/directness: {case['critic']['semantic_alignment_score']} / {case['critic']['directness_score']}", f"- Category: {case['critic']['mismatch_type']}", f"- Explanation: {case['critic']['explanation']}", f"- Stronger direction: {case['critic']['stronger_visual_direction']}", f"- Human expected category: {case['human_expected_category']}"])
    else:
        lines.append("Pending.")
    atomic_write_text(research / "semantic_alignment_validation_execution_report.md", "\n".join(lines) + "\n")
    buffer = io.StringIO(); writer = csv.writer(buffer); writer.writerow(["quote_hash", "image_basename", "alignment", "directness", "editorial_power", "mismatch_type", "confidence"])
    critic_db = read_json(research / "semantic_alignment_critic.json", {}) or {}
    for row in sorted((critic_db.get("items") or {}).values(), key=lambda item: (item["quote_hash"], item["image_basename"])):
        writer.writerow([row["quote_hash"], row["image_basename"], row["semantic_alignment_score"], row["directness_score"], row["editorial_power_score"], row["mismatch_type"], row["confidence"]])
    atomic_write_text(research / "validation_critic_results.csv", buffer.getvalue())
    return summary
