#!/usr/bin/env python3
"""Run a repeated, bounded qualification of AI-first principle replies."""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from reply_evidence import EvidenceRepository
from tools import evaluate_ai_first_reply_quote_matrix as matrix
from tools import pilot_ai_first_reply_strategy as provider


PROFILE = "repeated-principle-world-claim-qualification-v7"
SOURCE_RUN = (
    PROJECT_ROOT
    / "semantic_alignment_research/ai_first_reply_strategy_001"
    / "provider_pilot_repair_20260721_postfix_v1"
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "semantic_alignment_research/ai_first_reply_strategy_001"
    / "provider_pilot_principle_qualification_20260721_v4"
)
MAXIMUM_HARD_LIMIT_USD = 2.5
EXPECTED_CASE_COST_USD = 0.018
REPEATED_SCENARIOS = {"principle_agreement", "principle_challenge"}
CONTROL_QUOTAS = {
    "direct_meaning_question": 5,
    "wrong_speaker_question": 5,
    "unsupported_allegation": 5,
    "quoted_context_distraction": 5,
}


class QualificationError(RuntimeError):
    """The qualification cannot continue without violating an invariant."""


def repeated_case(case: dict[str, Any], repeat: int) -> dict[str, Any]:
    """Return a semantically identical case with a distinct logical identity."""
    row = {
        **case,
        "case_id": f"{case['case_id']}-qualification-repeat-{repeat}",
        "sample_role": "principle_repeatability_qualification",
        "qualification_repeat": repeat,
    }
    row["fixture_hash"] = matrix.value_hash({
        key: value for key, value in row.items() if key != "fixture_hash"
    })
    return row


def select_sample(source_sample: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select every principle case twice and fixed high-risk controls once."""
    selected: list[dict[str, Any]] = []
    for case in source_sample:
        if case["scenario_id"] in REPEATED_SCENARIOS:
            selected.extend(repeated_case(case, repeat) for repeat in (1, 2))
    for scenario_id, quota in CONTROL_QUOTAS.items():
        candidates = sorted(
            (case for case in source_sample if case["scenario_id"] == scenario_id),
            key=lambda case: (-matrix.risk_score(case), matrix.stable_tie(case)),
        )
        if len(candidates) < quota:
            raise QualificationError(f"insufficient controls for {scenario_id}")
        for case in candidates[:quota]:
            row = {
                **case,
                "case_id": f"{case['case_id']}-qualification-control",
                "sample_role": "preserved_safety_control",
                "qualification_repeat": 1,
            }
            row["fixture_hash"] = matrix.value_hash({
                key: value for key, value in row.items() if key != "fixture_hash"
            })
            selected.append(row)
    return sorted(selected, key=lambda row: (
        row["scenario_id"],
        row["quote_id"],
        row["qualification_repeat"],
    ))


def select_challenge_smoke(
    source_sample: list[dict[str, Any]],
    quote_limit: int,
) -> list[dict[str, Any]]:
    """Select a deterministic repeated subset of civil principle challenges."""
    if not 1 <= quote_limit <= 15:
        raise QualificationError("challenge smoke quote limit must be from 1 to 15")
    challenge_rows = [
        row for row in select_sample(source_sample)
        if row["scenario_id"] == "principle_challenge"
    ]
    ranked_quote_ids: list[str] = []
    for row in sorted(
        challenge_rows,
        key=lambda item: (-matrix.risk_score(item), matrix.stable_tie(item)),
    ):
        quote_id = str(row["quote_id"])
        if quote_id not in ranked_quote_ids:
            ranked_quote_ids.append(quote_id)
    selected_quote_ids = set(ranked_quote_ids[:quote_limit])
    selected = [
        row for row in challenge_rows
        if row["quote_id"] in selected_quote_ids
    ]
    if len(selected) != quote_limit * 2:
        raise QualificationError("challenge smoke sample is incomplete")
    return sorted(
        selected,
        key=lambda row: (row["quote_id"], row["qualification_repeat"]),
    )


def prepare(
    *,
    project_dir: Path,
    output_dir: Path,
    model: str,
    hard_limit_usd: float,
    challenge_smoke_quote_limit: int = 0,
) -> dict[str, Any]:
    """Freeze deterministic qualification inputs and cost controls."""
    if model != "grok-4.3":
        raise QualificationError("this qualification is reviewed only for grok-4.3")
    if not 0 < hard_limit_usd <= MAXIMUM_HARD_LIMIT_USD:
        raise QualificationError("hard limit must be greater than zero and no more than US$2.50")
    corpus = project_dir / "semantic_alignment_research/quote_research_full_001"
    repository = EvidenceRepository(
        corpus,
        factual_evidence_path=project_dir / "reply_factual_evidence.json",
    )
    fixtures = matrix.build_matrix(repository)
    source_sample = matrix.read_jsonl(SOURCE_RUN / "paid_sample.jsonl")
    if challenge_smoke_quote_limit:
        sample = select_challenge_smoke(source_sample, challenge_smoke_quote_limit)
        expected_counts = {"principle_challenge": challenge_smoke_quote_limit * 2}
        profile = f"{PROFILE}-challenge-smoke-{challenge_smoke_quote_limit}x2"
    else:
        sample = select_sample(source_sample)
        expected_counts = {
            "principle_agreement": 30,
            "principle_challenge": 30,
            **CONTROL_QUOTAS,
        }
        profile = PROFILE
    expected_count = sum(expected_counts.values())
    errors: list[str] = []
    if len(sample) != expected_count:
        errors.append(f"sample count {len(sample)} != {expected_count}")
    if len({row["case_id"] for row in sample}) != len(sample):
        errors.append("qualification case IDs are not unique")
    if Counter(row["scenario_id"] for row in sample) != Counter(expected_counts):
        errors.append("qualification scenario counts differ")
    wrong_resolutions = []
    for case in sample:
        resolved = repository.resolve_context_quotation(matrix.fixture_context(case))
        if resolved is None or resolved["quote_id"] != case["quote_id"]:
            wrong_resolutions.append(case["case_id"])
    if wrong_resolutions:
        errors.append(f"quotation resolution mismatch: {wrong_resolutions[:5]}")
    if errors:
        raise QualificationError("qualification validation failed: " + "; ".join(errors))

    output_dir.mkdir(parents=True, exist_ok=True)
    fixtures_text = matrix.jsonl_text(fixtures)
    sample_text = matrix.jsonl_text(sample)
    fixtures_sha = provider.sha256_bytes(fixtures_text.encode("utf-8"))
    sample_sha = provider.sha256_bytes(sample_text.encode("utf-8"))
    source_paths = [
        Path(__file__).resolve(),
        project_dir / "tools/evaluate_ai_first_reply_quote_matrix.py",
        project_dir / "tools/pilot_ai_first_reply_strategy.py",
        project_dir / "reply_strategy.py",
        project_dir / "reply_evidence.py",
        project_dir / "historical_context_formatter.py",
        project_dir / "semantic_alignment/quote_research_schema.py",
        project_dir / "reply_factual_evidence.json",
        corpus / "corpus_manifest.json",
        corpus / "research_packets.json",
        corpus / "final_unresolved/final_research_status.json",
        SOURCE_RUN / "run_manifest.json",
        SOURCE_RUN / "paid_sample.jsonl",
        SOURCE_RUN / "evaluation_results.json",
    ]
    manifest = {
        "schema_version": matrix.SCHEMA_VERSION,
        "run_version": matrix.RUN_VERSION,
        "pilot_profile": profile,
        "created_at": provider.utc_now(),
        "project_dir": str(project_dir),
        "output_dir": str(output_dir),
        "model": model,
        "endpoint": provider.DEFAULT_XAI_BASE,
        "network_allowlist": [provider.XAI_HOST],
        "posting_enabled": False,
        "media_transmitted": False,
        "hard_limit_usd": hard_limit_usd,
        "paid_sample_count": len(sample),
        "fixtures_sha256": fixtures_sha,
        "paid_sample_sha256": sample_sha,
        "source_run": str(SOURCE_RUN),
        "source_hashes": {
            str(path.relative_to(project_dir)): provider.sha256_file(path)
            for path in source_paths
        },
    }
    manifest_path = output_dir / "run_manifest.json"
    if manifest_path.exists():
        existing = provider.read_json(manifest_path)
        if {key: value for key, value in existing.items() if key != "created_at"} != {
            key: value for key, value in manifest.items() if key != "created_at"
        }:
            raise QualificationError("prepared run differs from immutable existing manifest")
    else:
        provider.atomic_text(output_dir / "fixtures.jsonl", fixtures_text)
        provider.atomic_text(output_dir / "paid_sample.jsonl", sample_text)
        provider.atomic_json(manifest_path, manifest)
    validation = {
        "schema_version": matrix.SCHEMA_VERSION,
        "run_version": matrix.RUN_VERSION,
        "pilot_profile": profile,
        "passed": True,
        "errors": [],
        "eligible_quote_count": len(repository.packets),
        "fixture_count": len(fixtures),
        "scenario_count": len(matrix.SCENARIOS),
        "paid_sample_count": len(sample),
        "paid_sample_scenario_counts": dict(sorted(Counter(
            row["scenario_id"] for row in sample
        ).items())),
        "correct_quote_resolution_count": len(sample),
        "fixtures_sha256": fixtures_sha,
        "paid_sample_sha256": sample_sha,
    }
    provider.atomic_json(output_dir / "offline_validation.json", validation)
    provider.atomic_json(output_dir / "cost_preflight.json", {
        "schema_version": matrix.SCHEMA_VERSION,
        "pilot_profile": profile,
        "model": model,
        "planned_case_count": len(sample),
        "expected_case_cost_usd": EXPECTED_CASE_COST_USD,
        "expected_cost_usd": round(len(sample) * EXPECTED_CASE_COST_USD, 6),
        "hard_limit_usd": hard_limit_usd,
        "guard": "Every request is reserved before transmission; ambiguous calls are never retried.",
    })
    return validation


def main(argv: list[str] | None = None) -> int:
    """Prepare, execute or report the isolated qualification."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run", "report"))
    parser.add_argument("--project-dir", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model", default="grok-4.3")
    parser.add_argument("--confirm-cost-limit-usd", type=float, required=True)
    parser.add_argument("--challenge-smoke-quote-limit", type=int, default=0)
    parser.add_argument("--execute-xai", action="store_true")
    args = parser.parse_args(argv)
    project_dir = args.project_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if output_dir == project_dir or project_dir not in output_dir.parents:
        raise QualificationError("output directory must be isolated beneath the project")
    if args.command == "prepare":
        if args.challenge_smoke_quote_limit and output_dir == DEFAULT_OUTPUT:
            raise QualificationError(
                "challenge smoke preparation requires an explicit isolated output directory"
            )
        result = prepare(
            project_dir=project_dir,
            output_dir=output_dir,
            model=args.model,
            hard_limit_usd=args.confirm_cost_limit_usd,
            challenge_smoke_quote_limit=args.challenge_smoke_quote_limit,
        )
        print(matrix.canonical_json({
            "passed": result["passed"],
            "paid_sample_count": result["paid_sample_count"],
            "output_dir": str(output_dir),
        }))
        return 0
    if args.command == "run":
        if not args.execute_xai:
            raise QualificationError("--execute-xai is required for paid execution")
        summary = matrix.execute(
            project_dir=project_dir,
            output_dir=output_dir,
            model=args.model,
            api_key=os.getenv("XAI_API_KEY", ""),
            hard_limit_usd=args.confirm_cost_limit_usd,
        )
    else:
        summary = matrix.build_report(project_dir=project_dir, output_dir=output_dir)
    print(matrix.canonical_json({
        "completed": summary["completed_case_count"],
        "planned": summary["effective_paid_sample_count"],
        "passed": summary["passed_case_count"],
        "failed": summary["failed_case_count"],
        "known_cost_usd": summary["known_cost_usd"],
        "report": str(output_dir / "evaluation_report.md"),
    }))
    return 0 if summary["completed_case_count"] == summary["effective_paid_sample_count"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
