#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from semantic_alignment.io import atomic_write_json, read_json
from semantic_alignment.scene_grammar_planning import build_scene_plan, schema_document

ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "semantic_alignment_research/scene_grammar_planning_001"
SOURCE = ROOT / "semantic_alignment_research/generation_prompt_pilot_001"


def build(output: Path) -> dict:
    manifest = read_json(SOURCE / "validation_manifest.json")
    briefs = read_json(SOURCE / "generation_briefs.json")["items"]
    failures = read_json(ROOT / "semantic_alignment_research/scene_grammar_pilot_001/failure_analysis.json")
    failure_by_case = {row["case_id"]: row for row in failures["items"]}
    items = [
        build_scene_plan(case, briefs[case["quote_hash"]], failure_by_case.get(case["case_id"]))
        for case in manifest["items"]
    ]
    if len(items) != 20 or len({row["case_id"] for row in items}) != 20:
        raise RuntimeError("planning set must contain exactly 20 unique cases")
    output.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "analysis_kind": "scene_grammar_planning_set",
        "source_manifest_hash": manifest["manifest_hash"],
        "items": items,
        "set_hash": hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest(),
    }
    atomic_write_json(output / "scene_specs.json", payload)
    atomic_write_json(output / "scene_grammar_schema.json", schema_document())
    if not (output / "scene_review.json").exists():
        atomic_write_json(output / "scene_review.json", {"schema_version": 1, "analysis_kind": "scene_grammar_human_review", "items": {}})
    report = "\n".join([
        "# Scene Grammar Planning 001", "",
        "- Executable planning set: yes",
        "- Deterministic scene specifications: 20",
        "- Source cases: Generation Prompt Pilot 001",
        "- Inputs: cached quote semantics, cached visual-intent briefs, Tony's prior notes and offline failure analysis",
        "- Everest correction: tallest Everest summit, climber and Union Flag; communist/map/Cold War imagery forbidden",
        "- Free-trade correction: voluntary exchange and real goods/payment; generic ideological symbolism forbidden",
        "- Prompt compilation: not performed",
        "- Image generation: not performed",
        "- Provider calls: none", "",
        "## Review command", "",
        "```bash",
        "python3 tools/scene_grammar_planning_review.py \\",
        "  --run-dir semantic_alignment_research/scene_grammar_planning_001 \\",
        "  --host 127.0.0.1 --port 8774",
        "```", "",
        "The reviewer records approve, revise or reject plus optional notes using atomic persistence. It binds only to loopback and supports previous/next navigation, progress, revision and resume.", "",
        "No production file or behaviour was changed. Nothing was staged, committed, pushed or deployed.",
    ])
    (output / "scene_grammar_planning_report.md").write_text(report + "\n")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = build(args.output_dir.resolve())
    print(json.dumps({"cases": len(payload["items"]), "set_hash": payload["set_hash"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
