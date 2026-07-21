#!/usr/bin/env python3
"""Prepare and run a bounded provider pilot for the repaired reply strategy."""

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


RUN_VERSION = matrix.RUN_VERSION
PILOT_PROFILE = "nonretryable-safety-conflicts-and-world-claim-checklist-v8"
MAXIMUM_HARD_LIMIT_USD = 5.0
EXPECTED_CASE_COST_USD = {
    "grok-4.3": 0.018,
    "grok-4.5": 0.035,
}
PRIOR_RUN = (
    PROJECT_ROOT
    / "semantic_alignment_research/ai_first_reply_strategy_001"
    / "provider_pilot_corpus_matrix_20260721_v5"
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "semantic_alignment_research/ai_first_reply_strategy_001"
    / "provider_pilot_repair_20260721_v4"
)
SCENARIO_QUOTAS = {
    "direct_context_question": 20,
    "direct_meaning_question": 20,
    "quotation_verification": 20,
    "wrong_speaker_question": 20,
    "principle_agreement": 15,
    "principle_challenge": 15,
    "unsupported_allegation": 5,
    "quoted_context_distraction": 5,
}


class RepairPilotError(RuntimeError):
    """The repair pilot cannot continue without violating an invariant."""


def select_sample(
    fixtures: list[dict[str, Any]],
    prior_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Select prior failures first, then deterministic high-risk controls."""
    fixtures_by_id = {row["case_id"]: row for row in fixtures}
    prior_by_scenario: dict[str, list[dict[str, Any]]] = {}
    for result in prior_results:
        prior_by_scenario.setdefault(str(result["scenario_id"]), []).append(result)

    sample: list[dict[str, Any]] = []
    selected: set[str] = set()
    for scenario_id, quota in SCENARIO_QUOTAS.items():
        prior = prior_by_scenario.get(scenario_id, [])
        ranked_prior = sorted(
            prior,
            key=lambda row: (
                bool(row.get("passed")),
                -int(row.get("risk_score") or 0),
                matrix.stable_tie(fixtures_by_id[row["case_id"]]),
            ),
        )
        candidates = [
            fixtures_by_id[row["case_id"]]
            for row in ranked_prior
            if row["case_id"] in fixtures_by_id
        ]
        candidates.extend(sorted(
            (
                row for row in fixtures
                if row["scenario_id"] == scenario_id and row["case_id"] not in {
                    candidate["case_id"] for candidate in candidates
                }
            ),
            key=lambda row: (-matrix.risk_score(row), matrix.stable_tie(row)),
        ))
        chosen = 0
        for case in candidates:
            if case["case_id"] in selected:
                continue
            prior_result = next(
                (row for row in prior if row["case_id"] == case["case_id"]),
                None,
            )
            sample.append({
                **case,
                "sample_role": (
                    "prior_failure_regression"
                    if prior_result is not None and not prior_result.get("passed")
                    else "preserved_behaviour_control"
                ),
                "risk_score": matrix.risk_score(case),
                "prior_outcome": prior_result.get("outcome") if prior_result else None,
                "prior_passed": prior_result.get("passed") if prior_result else None,
            })
            selected.add(case["case_id"])
            chosen += 1
            if chosen == quota:
                break
        if chosen != quota:
            raise RepairPilotError(f"could not select {quota} cases for {scenario_id}")
    return sample


def prepare(
    *,
    project_dir: Path,
    output_dir: Path,
    model: str,
    hard_limit_usd: float,
) -> dict[str, Any]:
    """Create immutable offline inputs and a conservative cost preflight."""
    if not 0 < hard_limit_usd <= MAXIMUM_HARD_LIMIT_USD:
        raise RepairPilotError("hard limit must be greater than zero and no more than US$5")
    expected_case_cost = EXPECTED_CASE_COST_USD.get(model)
    if expected_case_cost is None:
        raise RepairPilotError(f"no reviewed cost preflight is available for model {model}")
    corpus = project_dir / "semantic_alignment_research/quote_research_full_001"
    repository = EvidenceRepository(
        corpus,
        factual_evidence_path=project_dir / "reply_factual_evidence.json",
    )
    fixtures = matrix.build_matrix(repository)
    fixture_validation_sample = matrix.build_paid_sample(fixtures, targeted_per_scenario=1)
    base_validation = matrix.validate_matrix(
        repository,
        fixtures,
        fixture_validation_sample,
        targeted_per_scenario=1,
    )
    if not base_validation["passed"]:
        raise RepairPilotError("base fixture validation failed")
    prior_results = matrix.collect_results(PRIOR_RUN)
    sample = select_sample(fixtures, prior_results)
    errors: list[str] = []
    expected_count = sum(SCENARIO_QUOTAS.values())
    if len(sample) != expected_count:
        errors.append(f"sample count {len(sample)} != {expected_count}")
    if len({row["case_id"] for row in sample}) != len(sample):
        errors.append("sample case IDs are not unique")
    if Counter(row["scenario_id"] for row in sample) != Counter(SCENARIO_QUOTAS):
        errors.append("sample scenario quotas differ")
    wrong_resolutions = []
    for case in sample:
        resolved = repository.resolve_context_quotation(matrix.fixture_context(case))
        if resolved is None or resolved["quote_id"] != case["quote_id"]:
            wrong_resolutions.append(case["case_id"])
    if wrong_resolutions:
        errors.append(f"quotation resolution mismatch: {wrong_resolutions[:5]}")

    output_dir.mkdir(parents=True, exist_ok=True)
    fixtures_text = matrix.jsonl_text(fixtures)
    sample_text = matrix.jsonl_text(sample)
    fixtures_sha = provider.sha256_bytes(fixtures_text.encode("utf-8"))
    sample_sha = provider.sha256_bytes(sample_text.encode("utf-8"))
    validation = {
        **base_validation,
        "passed": not errors,
        "errors": errors,
        "pilot_profile": PILOT_PROFILE,
        "paid_sample_count": len(sample),
        "paid_sample_scenario_counts": dict(sorted(Counter(
            row["scenario_id"] for row in sample
        ).items())),
        "prior_failure_case_count": sum(
            row["sample_role"] == "prior_failure_regression" for row in sample
        ),
        "correct_quote_resolution_count": len(sample) - len(wrong_resolutions),
        "fixtures_sha256": fixtures_sha,
        "paid_sample_sha256": sample_sha,
    }
    if errors:
        raise RepairPilotError("repair pilot validation failed: " + "; ".join(errors))

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
        PRIOR_RUN / "evaluation_results.json",
    ]
    manifest = {
        "schema_version": matrix.SCHEMA_VERSION,
        "run_version": RUN_VERSION,
        "pilot_profile": PILOT_PROFILE,
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
        "prior_run": str(PRIOR_RUN),
        "source_hashes": {
            str(path.relative_to(project_dir)): provider.sha256_file(path)
            for path in source_paths
        },
    }
    manifest_path = output_dir / "run_manifest.json"
    if manifest_path.exists():
        existing = provider.read_json(manifest_path)
        if {k: v for k, v in existing.items() if k != "created_at"} != {
            k: v for k, v in manifest.items() if k != "created_at"
        }:
            raise RepairPilotError("prepared run differs from immutable existing manifest")
    else:
        provider.atomic_text(output_dir / "fixtures.jsonl", fixtures_text)
        provider.atomic_text(output_dir / "paid_sample.jsonl", sample_text)
        provider.atomic_json(manifest_path, manifest)
    provider.atomic_json(output_dir / "offline_validation.json", validation)
    provider.atomic_json(output_dir / "cost_preflight.json", {
        "schema_version": matrix.SCHEMA_VERSION,
        "pilot_profile": PILOT_PROFILE,
        "model": model,
        "planned_case_count": len(sample),
        "expected_case_cost_usd": expected_case_cost,
        "expected_cost_usd": round(len(sample) * expected_case_cost, 6),
        "hard_limit_usd": hard_limit_usd,
        "estimate_basis": (
            "Conservative model-specific projection from the completed Grok 4.3 token profile; "
            "the authenticated provider prices are fetched again before inference."
        ),
        "guard": "Every request is reserved before transmission; execution stops before the hard limit.",
    })
    return validation


def main(argv: list[str] | None = None) -> int:
    """Run one explicit preparation, paid execution or report action."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run", "report"))
    parser.add_argument("--project-dir", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model", default=os.getenv("XAI_MODEL", "grok-4.3"))
    parser.add_argument("--confirm-cost-limit-usd", type=float, required=True)
    parser.add_argument("--execute-xai", action="store_true")
    args = parser.parse_args(argv)
    project_dir = args.project_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if output_dir == project_dir or project_dir not in output_dir.parents:
        raise RepairPilotError("output directory must be isolated beneath the project")
    if args.command == "prepare":
        result = prepare(
            project_dir=project_dir,
            output_dir=output_dir,
            model=args.model,
            hard_limit_usd=args.confirm_cost_limit_usd,
        )
        print(matrix.canonical_json({
            "passed": result["passed"],
            "paid_sample_count": result["paid_sample_count"],
            "prior_failure_case_count": result["prior_failure_case_count"],
            "output_dir": str(output_dir),
        }))
        return 0
    if args.command == "run":
        if not args.execute_xai:
            raise RepairPilotError("--execute-xai is required for paid execution")
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
