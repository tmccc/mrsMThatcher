#!/usr/bin/env python3
"""Analyse pairwise validation artefacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from semantic_alignment.pairwise_validation import DEFAULT_SEED, build_manifest, select_calibration, write_run

ROOT = Path(__file__).resolve().parent
FIRST_RUN = ROOT / "semantic_alignment_research/first_impression/v1_20260712"
OUTPUT = ROOT / "semantic_alignment_research/pairwise_validation_001"


def load(path: Path):
    """Load a JSON document."""
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    """Run the command-line entry point."""
    parser = argparse.ArgumentParser(description="Build the offline pairwise editorial validation run")
    parser.add_argument("--run-dir", type=Path, default=OUTPUT)
    parser.add_argument("--cases", type=int, default=50)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--build-manifest", action="store_true")
    args = parser.parse_args()
    if not args.build_manifest:
        parser.error("--build-manifest is required")
    if args.cases != 50:
        parser.error("this frozen study requires exactly 50 cases")

    validation = load(FIRST_RUN / "validation_cases.json")["items"]
    human = load(FIRST_RUN / "human_reviews.json")["items"]
    intents = load(FIRST_RUN / "quote_visual_intents.json")["items"]
    images = load(FIRST_RUN / "image_first_impressions.json")["items"]
    editorial_doc = load(ROOT / "generated_image_analysis.json")
    editorial_by_name = {}
    paths = {}
    for name in editorial_doc["file_metadata"]:
        item_hash = editorial_doc["path_index"][name]
        editorial_by_name[name] = editorial_doc["items"][item_hash]
        paths[name] = ROOT / "generated_review_approved_images" / name
    identity = load(ROOT / "generated_image_identity_dependence_audit.json")["items"]
    previous = list(load(FIRST_RUN / "pairwise_rankings.json")["items"].values())
    manifest, blind = build_manifest(validation, human, intents, images, editorial_by_name, identity, paths, previous, seed=args.seed)
    calibration = select_calibration(manifest, blind)
    write_run(args.run_dir.resolve(), manifest, blind, calibration)
    counts = {label: sum(x["prior_human_label"] == label for x in blind["items"].values()) for label in ("keep", "replace", "unsure")}
    print(json.dumps({"run_dir": str(args.run_dir.resolve()), "cases": 50, "calibration_cases": 15,
                      "prior_control_distribution": counts, "paid_calls_made": 0}, indent=2))


if __name__ == "__main__":
    main()
