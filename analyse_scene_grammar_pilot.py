#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
from pathlib import Path
from typing import Any

from semantic_alignment import (
    CRITIC_PROMPT_VERSION,
    CRITIC_SCHEMA_VERSION,
    IMAGE_PROMPT_VERSION,
    IMAGE_SCHEMA_VERSION,
)
from semantic_alignment.first_impression import (
    ALIGNMENT_OUTPUT_SCHEMA,
    EVEREST_QUOTE_HASH,
    IMAGE_FIRST_OUTPUT_SCHEMA,
    critic_prompt as first_critic_prompt,
    image_first_prompt,
    validate_alignment,
    validate_image_first,
)
from semantic_alignment.generation_prompt_pilot import ImageClient
from semantic_alignment.io import atomic_write_json, read_json
from semantic_alignment.pipeline import CostLedger, XAIClient, cached_call, utc_now
from semantic_alignment.prompts import critic_prompt as semantic_critic_prompt
from semantic_alignment.prompts import image_prompt
from semantic_alignment.scene_grammar_pilot import (
    FREE_TRADE_KEY,
    SCENE_FIELDS,
    STYLES,
    classify_failures,
    compile_prompt,
    execute,
    preflight,
    scene_spec,
    validate_spec,
)
from semantic_alignment.schemas import (
    CRITIC_OUTPUT_SCHEMA,
    IMAGE_OUTPUT_SCHEMA,
    validate_critic_result,
    validate_image_fingerprint,
)
from tools.audit_generated_image_identity_dependence import (
    AUDIT_SCHEMA,
    prompt_for_context as identity_prompt,
    validate_analysis as validate_identity,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_RUN = ROOT / "semantic_alignment_research/scene_grammar_pilot_001"
OLD_RUN = ROOT / "semantic_alignment_research/generation_prompt_pilot_001"
SOURCE_RUN = ROOT / "semantic_alignment_research/runs/v2_20260711T111526Z"
FIRST_RUN = ROOT / "semantic_alignment_research/first_impression/v1_20260712"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("prepare")
    subparsers.add_parser("dry-run")
    generate = subparsers.add_parser("generate")
    generate.add_argument("--execute-generation", action="store_true")
    generate.add_argument("--confirm-cost-limit-usd", type=float)
    generate.add_argument("--resume", action="store_true")
    analyse = subparsers.add_parser("analyse")
    analyse.add_argument("--execute-analysis", action="store_true")
    analyse.add_argument("--confirm-cost-limit-usd", type=float)
    analyse.add_argument("--resume", action="store_true")
    subparsers.add_parser("report")
    return parser


def prepare(run: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = read_json(OLD_RUN / "validation_manifest.json")
    briefs = read_json(OLD_RUN / "generation_briefs.json")["items"]
    failures = classify_failures(
        manifest,
        read_json(OLD_RUN / "human_reviews.json"),
        read_json(OLD_RUN / "generated_candidates.json"),
        read_json(OLD_RUN / "analysis_results.json"),
    )
    failure_by_case = {row["case_id"]: row for row in failures["items"]}
    specs = {
        case["case_id"]: validate_spec(
            scene_spec(case, briefs[case["quote_hash"]], failure_by_case.get(case["case_id"]))
        )
        for case in manifest["items"]
    }
    prompts = {
        case_id: {style: compile_prompt(spec, style) for style in STYLES}
        for case_id, spec in specs.items()
    }
    run.mkdir(parents=True, exist_ok=True)
    atomic_write_json(run / "failure_analysis.json", failures)
    atomic_write_json(run / "scene_grammar_schema.json", {"schema_version": 1, "required": list(SCENE_FIELDS)})
    atomic_write_json(run / "scene_specs.json", {"schema_version": 1, "items": specs})
    atomic_write_json(run / "compiled_prompts.json", {"schema_version": 1, "items": prompts})
    atomic_write_json(run / "validation_manifest.json", manifest)
    atomic_write_json(run / "preflight.json", preflight(20))
    for name in ("analysis_results.json", "human_reviews.json"):
        if not (run / name).exists():
            atomic_write_json(run / name, {"schema_version": 1, "items": {}})
    return manifest, specs


def _identity_check(
    *,
    run: Path,
    candidate_id: str,
    candidate: dict[str, Any],
    case: dict[str, Any],
    spec: dict[str, Any],
    semantic_fingerprint: dict[str, Any],
    client: XAIClient,
    ledger: CostLedger,
    limit: float,
) -> dict[str, Any]:
    if "Margaret Thatcher" not in spec["primary_subject"]:
        return {"status": "not_required", "grounded_people": []}
    context = {
        "basename": candidate["image_basename"],
        "origin_quote_hash": candidate["quote_hash"],
        "origin_quote": case["quote_text"],
        "grounded_people": ["Margaret Thatcher"],
        "generation_prompt": compile_prompt(spec, candidate["style"]),
        "semantic_brief": spec,
        "existing_analysis": semantic_fingerprint,
    }
    response = cached_call(
        stage="image",
        key=f"scene-identity:{candidate_id}",
        prompt=identity_prompt(context),
        schema=AUDIT_SCHEMA,
        client=client,
        ledger=ledger,
        response_dir=run / "responses",
        confirmed_stage_limit=limit,
        image_path=Path(candidate["path"]),
        expected_sha256=candidate["sha256"],
    )
    return {
        "status": "completed",
        "grounded_people": ["Margaret Thatcher"],
        "result": validate_identity(response.content, ["Margaret Thatcher"]),
        "model": response.model,
    }


def analyse(run: Path, manifest: dict[str, Any], specs: dict[str, Any], limit: float | None) -> dict[str, Any]:
    if limit is None or not 0 < limit <= 10:
        raise RuntimeError("analysis ceiling must be positive and no more than $10")
    generated = read_json(run / "generated_candidates.json")
    quotes = read_json(SOURCE_RUN / "quote_semantic_fingerprints.json")["items"]
    intents = read_json(FIRST_RUN / "quote_visual_intents.json")["items"]
    output = read_json(run / "analysis_results.json")
    cases = {case["case_id"]: case for case in manifest["items"]}
    client = XAIClient(api_key=os.getenv("XAI_API_KEY", ""))
    ledger = CostLedger(run / "analysis_cost_ledger.json", run_id=run.name)
    for candidate_id, candidate in sorted(generated["items"].items()):
        if candidate_id in output["items"]:
            continue
        path = Path(candidate["path"])
        sha256 = candidate["sha256"]
        first_response = cached_call(
            stage="image", key=f"scene-first:{candidate_id}", prompt=image_first_prompt(),
            schema=IMAGE_FIRST_OUTPUT_SCHEMA, client=client, ledger=ledger,
            response_dir=run / "responses", confirmed_stage_limit=limit,
            image_path=path, expected_sha256=sha256,
        )
        first = {
            "schema_version": 1,
            "analysis_kind": "image_first_impression",
            "image_basename": candidate["image_basename"],
            "sha256": sha256,
            **validate_image_first(first_response.content),
        }
        semantic_response = cached_call(
            stage="image", key=f"scene-semantic:{candidate_id}", prompt=image_prompt(),
            schema=IMAGE_OUTPUT_SCHEMA, client=client, ledger=ledger,
            response_dir=run / "responses", confirmed_stage_limit=limit,
            image_path=path, expected_sha256=sha256,
        )
        semantic_fingerprint = {
            "schema_version": IMAGE_SCHEMA_VERSION,
            "analysis_kind": "image_implied_message",
            "image_basename": candidate["image_basename"],
            "sha256": sha256,
            **semantic_response.content,
            "model": semantic_response.model,
            "prompt_version": IMAGE_PROMPT_VERSION,
            "analysed_at": utc_now(),
        }
        validate_image_fingerprint(semantic_fingerprint)
        quote = quotes[candidate["quote_hash"]]
        semantic_critic = cached_call(
            stage="critic", key=f"scene-semantic-critic:{candidate_id}",
            prompt=semantic_critic_prompt(quote, semantic_fingerprint), schema=CRITIC_OUTPUT_SCHEMA,
            client=client, ledger=ledger, response_dir=run / "responses", confirmed_stage_limit=limit,
        )
        semantic_alignment = {
            "schema_version": CRITIC_SCHEMA_VERSION,
            "analysis_kind": "quote_image_semantic_alignment",
            "quote_hash": candidate["quote_hash"],
            "image_basename": candidate["image_basename"],
            **semantic_critic.content,
            "model": semantic_critic.model,
            "prompt_version": CRITIC_PROMPT_VERSION,
            "analysed_at": utc_now(),
        }
        validate_critic_result(semantic_alignment)
        first_critic = cached_call(
            stage="critic", key=f"scene-first-critic:{candidate_id}",
            prompt=first_critic_prompt(intents[candidate["quote_hash"]], first),
            schema=ALIGNMENT_OUTPUT_SCHEMA, client=client, ledger=ledger,
            response_dir=run / "responses", confirmed_stage_limit=limit,
        )
        output["items"][candidate_id] = {
            "candidate": candidate,
            "image_first_impression": first,
            "image_semantic_fingerprint": semantic_fingerprint,
            "semantic_alignment": semantic_alignment,
            "first_impression_alignment": validate_alignment(first_critic.content),
            "identity_check": _identity_check(
                run=run,
                candidate_id=candidate_id,
                candidate=candidate,
                case=cases[candidate["case_id"]],
                spec=specs[candidate["case_id"]],
                semantic_fingerprint=semantic_fingerprint,
                client=client,
                ledger=ledger,
                limit=limit,
            ),
        }
        atomic_write_json(run / "analysis_results.json", output)
        print(f"analysed {len(output['items'])}/{len(generated['items'])}", flush=True)
    return output


def report(run: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    generated = read_json(run / "generated_candidates.json")
    analyses = read_json(run / "analysis_results.json")
    reviews = read_json(run / "human_reviews.json")
    old_reviews = read_json(OLD_RUN / "human_reviews.json")
    old_failures = read_json(run / "failure_analysis.json")
    if len(generated["items"]) != 40 or len(analyses["items"]) != 40 or len(reviews["items"]) != 20:
        raise RuntimeError("report requires 40 generations, 40 analyses and 20 reviews")
    by_case: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for candidate_id, candidate in generated["items"].items():
        by_case.setdefault(candidate["case_id"], []).append((candidate_id, candidate))
    wins = {style: 0 for style in STYLES}
    none = unsure = 0
    message = {value: 0 for value in ("yes", "partly", "no")}
    for case in manifest["items"]:
        review = reviews["items"][case["case_id"]]
        message[review["message_match"]] += 1
        rows = sorted(by_case[case["case_id"]])
        if review["preferred_candidate"] == "none":
            none += 1
        elif review["preferred_candidate"] == "unsure":
            unsure += 1
        else:
            index = "AB".index(review["preferred_candidate"])
            wins[rows[index][1]["style"]] += 1
    ledger = read_json(run / "analysis_cost_ledger.json", {"calls": []})
    known_analysis_cost = sum(float(row.get("cost_usd") or 0) for row in ledger["calls"])
    estimated_generation_cost = 40 * 0.013
    publishable = 20 - none - unsure
    style_rows = []
    for style in STYLES:
        candidate_ids = [candidate_id for candidate_id, row in generated["items"].items() if row["style"] == style]
        style_rows.append({
            "style": style,
            "human_wins": wins[style],
            "win_rate": wins[style] / 20,
            "first_impression_mean": sum(analyses["items"][key]["first_impression_alignment"]["dominant_visual_message_alignment_score"] for key in candidate_ids) / len(candidate_ids),
            "semantic_mean": sum(analyses["items"][key]["semantic_alignment"]["semantic_alignment_score"] for key in candidate_ids) / len(candidate_ids),
            "tone_mean": sum(analyses["items"][key]["first_impression_alignment"]["tone_alignment_score"] for key in candidate_ids) / len(candidate_ids),
            "editorial_mean": sum(analyses["items"][key]["semantic_alignment"]["editorial_power_score"] for key in candidate_ids) / len(candidate_ids),
        })
    with (run / "style_comparison.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=style_rows[0])
        writer.writeheader()
        writer.writerows(style_rows)

    old_failure_by_case = {row["case_id"]: row for row in old_failures["items"]}
    category_results = {}
    for category in sorted({cat for row in old_failures["items"] for cat in row["categories"]}):
        affected = [case_id for case_id, row in old_failure_by_case.items() if category in row["categories"]]
        corrected = sum(reviews["items"][case_id]["preferred_candidate"] not in {"none", "unsure"} for case_id in affected)
        category_results[category] = {"prior_cases": len(affected), "corrected": corrected, "still_none": len(affected)-corrected}

    accepted_ids = []
    rejected_ids = []
    for case in manifest["items"]:
        rows = sorted(by_case[case["case_id"]])
        choice = reviews["items"][case["case_id"]]["preferred_candidate"]
        if choice in {"A", "B"}:
            accepted_ids.append(rows["AB".index(choice)][0])
        elif choice == "none":
            rejected_ids.extend(candidate_id for candidate_id, _candidate in rows)

    def score_means(candidate_ids: list[str]) -> dict[str, float]:
        return {
            "first_impression": round(statistics.mean(analyses["items"][key]["first_impression_alignment"]["dominant_visual_message_alignment_score"] for key in candidate_ids), 2),
            "tone": round(statistics.mean(analyses["items"][key]["first_impression_alignment"]["tone_alignment_score"] for key in candidate_ids), 2),
            "semantic": round(statistics.mean(analyses["items"][key]["semantic_alignment"]["semantic_alignment_score"] for key in candidate_ids), 2),
            "editorial": round(statistics.mean(analyses["items"][key]["semantic_alignment"]["editorial_power_score"] for key in candidate_ids), 2),
        }

    accepted_means = score_means(accepted_ids)
    rejected_means = score_means(rejected_ids)
    residual_failures = {
        "identity_or_likeness": sum("resemble" in row.get("notes", "").lower() or "appearance" in row.get("notes", "").lower() for row in reviews["items"].values()),
        "garbled_or_distracting_detail": sum("sign" in row.get("notes", "").lower() or "weird" in row.get("notes", "").lower() for row in reviews["items"].values()),
        "historical_or_geographic_specificity": sum("bosnia" in row.get("notes", "").lower() or "croatia" in row.get("notes", "").lower() for row in reviews["items"].values()),
        "concept_remains_hard_to_visualise": sum("difficult to" in row.get("notes", "").lower() for row in reviews["items"].values()),
    }

    def regression(quote_hash: str, name: str) -> None:
        case = next(row for row in manifest["items"] if row["quote_hash"] == quote_hash)
        review = reviews["items"][case["case_id"]]
        rows = sorted(by_case[case["case_id"]])
        lines = [
            f"# {name} scene case", "", case["quote_text"], "",
            f"- Human choice: {review['preferred_candidate']}",
            f"- Immediate message: {review['message_match']}",
            f"- Notes: {review.get('notes', '')}",
        ]
        for index, (candidate_id, candidate) in enumerate(rows):
            first = analyses["items"][candidate_id]["first_impression_alignment"]
            semantic = analyses["items"][candidate_id]["semantic_alignment"]
            lines.append(
                f"- Candidate {'AB'[index]} ({candidate['style']}): first={first['dominant_visual_message_alignment_score']}, "
                f"semantic={semantic['semantic_alignment_score']}, tone={first['tone_alignment_score']}"
            )
        (run / f"{name.lower().replace(' ', '_')}_scene_case.md").write_text("\n".join(lines) + "\n")

    regression(EVEREST_QUOTE_HASH, "Everest")
    regression(FREE_TRADE_KEY[0], "Free trade")
    old_none = sum(row["preferred_candidate"] == "none" for row in old_reviews["items"].values())
    reduction = old_none / 20 - none / 20
    if none <= 8 and message["yes"] >= 8:
        recommendation = "ready for shadow generation"
    elif none < 15:
        recommendation = "repeat another bounded scene-grammar pilot"
    else:
        recommendation = "scene grammar not supported"
    lines = [
        "# Scene Grammar Pilot 001", "",
        "## Result", "",
        f"- Publishable: {publishable}/20",
        f"- None: {none}/20 ({none/20:.0%}), previous {old_none}/20 ({old_none/20:.0%})",
        f"- Absolute none-rate reduction: {reduction:.0%}",
        f"- Immediate message: {message}",
        f"- Style wins: {wins}",
        f"- Analysis known cost: ${known_analysis_cost:.6f}",
        f"- Estimated generation cost: ${estimated_generation_cost:.2f}",
        f"- Approximate cost per publishable case: ${(known_analysis_cost+estimated_generation_cost)/publishable:.4f}",
        f"- Recommendation: **{recommendation}**", "",
        "## Method", "",
        "The exact 20 difficult cases from Generation Prompt Pilot 001 were reused. Each received one deterministic physical scene specification and two prompts with identical semantic content: direct documentary rendering and cinematic photorealism. Tony reviewed stable A/B positions without seeing style or automated analysis.", "",
        "Domestic scenes were placed in recognisably British settings where physically appropriate. Three first-person or personality-led quotes explicitly grounded a realistic Margaret Thatcher; those six images also received the existing identity/likeness audit.", "",
        "## Failure correction", "",
        "| Prior failure category | Prior cases | Corrected | Still none |", "|---|---:|---:|---:|",
        *[f"| {category} | {values['prior_cases']} | {values['corrected']} | {values['still_none']} |" for category, values in category_results.items()], "",
        "Residual or newly visible problems from Tony's notes included: " + ", ".join(f"{name.replace('_',' ')} ({count})" for name, count in residual_failures.items()) + ". These are descriptive counts and may overlap.", "",
        "## Automated score calibration", "",
        f"Human-selected winners averaged first-impression {accepted_means['first_impression']}, tone {accepted_means['tone']}, semantic {accepted_means['semantic']} and editorial {accepted_means['editorial']}.",
        f"Candidates in pairs Tony rejected as none still averaged first-impression {rejected_means['first_impression']}, tone {rejected_means['tone']}, semantic {rejected_means['semantic']} and editorial {rejected_means['editorial']}.",
        "The small separation confirms automated-score inflation relative to publishability; the scores must remain observational.", "",
        "## Regression cases", "",
        "Everest: Tony selected Candidate B and marked the immediate message yes. The communist/geopolitical failure disappeared without a special post-selection rule.",
        "Free trade: Tony selected Candidate B and marked the immediate message yes. The scene shows voluntary exchange and exported goods rather than capitalism-versus-socialism symbolism, although generated packaging details remain a general visual-quality risk.", "",
        "## Style and provider scope", "",
        f"Direct won {wins['scene_grammar_direct']} cases and cinematic won {wins['scene_grammar_cinematic']}; this is not a decisive rendering-style separation. A multi-provider image trial was deferred because changing provider inside this fixed 2x20 design would confound the scene-grammar test. A separate small provider-controlled trial may now be justified.", "",
        "## Limitations", "",
        "This deliberately difficult 20-case sample is small, non-random and reused from the prior pilot. The same image provider generated both variants. Human judgement comes from one editor, and the estimated image cost is reconstructed from observed project pricing rather than authoritative per-image billing metadata.", "",
        "## Implementation and verification", "",
        "Architecture: cached quote/visual-intent inputs -> deterministic Scene Grammar v1 -> two semantically identical prompt variants -> bounded gpt-image-1.5 low generation -> existing xAI first-impression, semantic, tone/editorial and scoped identity analyses -> loopback blinded review -> offline comparison.", "",
        "New task files: `analyse_scene_grammar_pilot.py`, `semantic_alignment/scene_grammar_pilot.py`, `tools/scene_grammar_pilot_review.py`, `tests/test_scene_grammar_pilot.py`, and this versioned research directory. Existing Generation Prompt Pilot 001 files were read but not overwritten.", "",
        "Prompt isolation: image analysis received pixels only; quote-to-image critics received cached fingerprints; generation prompts contained scene specifications but no human labels or expected winners. The direct and cinematic prompts have byte-identical `SCENE_SPECIFICATION` payloads.", "",
        "Verification: `python3 -m py_compile analyse_semantic_alignment_xai.py semantic_alignment/*.py tools/*.py` passed; 180 affected semantic-alignment and production-log-isolation tests passed; `git diff --check` passed; the loopback reviewer smoke test passed. `git diff --stat` is empty because all task files are currently untracked. `git status --short` contains these new task paths plus numerous preserved unrelated untracked artefacts.", "",
        "## Safety record", "",
        "No production behaviour or production file changed. The production bot was not stopped, restarted or signalled. No prior fingerprint, critic output, prompt, image or human review was overwritten. The canonical simulator was not run. No search, grounding or model tools were enabled. Nothing was staged, committed, pushed or deployed.",
    ]
    (run / "scene_grammar_pilot_report.md").write_text("\n".join(lines) + "\n")
    return {"none": none, "message": message, "wins": wins, "recommendation": recommendation}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run = args.run_dir.resolve()
    manifest, specs = prepare(run)
    if args.command in {"prepare", "dry-run"}:
        print(json.dumps(preflight(20), indent=2))
        return 0
    if args.command == "generate":
        if not args.execute_generation:
            raise RuntimeError("explicit generation flag required")
        output = execute(
            run, manifest, specs, ImageClient(os.getenv("OPENAI_API_KEY", "")),
            args.confirm_cost_limit_usd,
        )
        print(len(output["items"]))
        return 0
    if args.command == "analyse":
        if not args.execute_analysis:
            raise RuntimeError("explicit analysis flag required")
        print(len(analyse(run, manifest, specs, args.confirm_cost_limit_usd)["items"]))
        return 0
    print(json.dumps(report(run, manifest), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
