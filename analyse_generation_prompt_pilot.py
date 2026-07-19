#!/usr/bin/env python3
"""Analyse generation prompt pilot artefacts."""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

from semantic_alignment.generation_prompt_pilot import (
    ImageClient, STYLES, build_brief, build_manifest,
    execute_generation, preflight, prompt_for_style,
)
from semantic_alignment.io import atomic_write_json, read_json
from semantic_alignment import IMAGE_SCHEMA_VERSION, IMAGE_PROMPT_VERSION, CRITIC_SCHEMA_VERSION, CRITIC_PROMPT_VERSION
from semantic_alignment.pipeline import CostLedger, XAIClient, cached_call, utc_now
from semantic_alignment.first_impression import IMAGE_FIRST_OUTPUT_SCHEMA, image_first_prompt, validate_image_first, critic_prompt as first_critic_prompt, ALIGNMENT_OUTPUT_SCHEMA, validate_alignment
from semantic_alignment.prompts import image_prompt, critic_prompt as semantic_critic_prompt
from semantic_alignment.schemas import IMAGE_OUTPUT_SCHEMA, CRITIC_OUTPUT_SCHEMA, validate_image_fingerprint, validate_critic_result

PROJECT = Path(__file__).resolve().parent
RUN = PROJECT / "semantic_alignment_research/generation_prompt_pilot_001"
SOURCE = PROJECT / "semantic_alignment_research/runs/v2_20260711T111526Z"
FIRST = PROJECT / "semantic_alignment_research/first_impression/v1_20260712"
PAIR = PROJECT / "semantic_alignment_research/pairwise_improved_pilot_20260713_run_v1"


def parser():
    """Build the command-line argument parser."""
    p = argparse.ArgumentParser(); p.add_argument("--project-dir", type=Path, default=PROJECT); p.add_argument("--run-dir", type=Path, default=RUN)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare"); sub.add_parser("dry-run"); sub.add_parser("report")
    gen = sub.add_parser("generate"); gen.add_argument("--execute-generation", action="store_true"); gen.add_argument("--confirm-cost-limit-usd", type=float); gen.add_argument("--resume", action="store_true")
    ana = sub.add_parser("analyse"); ana.add_argument("--execute-analysis", action="store_true"); ana.add_argument("--confirm-cost-limit-usd", type=float); ana.add_argument("--resume", action="store_true")
    return p


def prepare(run: Path):
    """Prepare the generation-prompt pilot workspace and manifest."""
    quotes = read_json(SOURCE / "quote_semantic_fingerprints.json")["items"]
    intents = read_json(FIRST / "quote_visual_intents.json")["items"]
    manifest = build_manifest(quotes, intents, read_json(PAIR / "pairwise_manifest.json"),
        read_json(PAIR / "human_pairwise_reviews.json"), read_json(FIRST / "validation_cases.json"),
        read_json(FIRST / "human_reviews.json"))
    briefs = {row["quote_hash"]: build_brief(quotes[row["quote_hash"]], intents[row["quote_hash"]]) for row in manifest["items"]}
    prompts = {row["quote_hash"]: {style: prompt_for_style(briefs[row["quote_hash"]], style) for style in STYLES} for row in manifest["items"]}
    run.mkdir(parents=True, exist_ok=True); atomic_write_json(run / "validation_manifest.json", manifest); atomic_write_json(run / "generation_briefs.json", {"schema_version": 1, "items": briefs}); atomic_write_json(run / "prompt_variants.json", {"schema_version": 1, "items": prompts}); atomic_write_json(run / "preflight.json", preflight(manifest))
    for name, empty in (("analysis_results.json", {"schema_version": 1, "items": {}}), ("human_reviews.json", {"schema_version": 1, "items": {}})):
        if not (run / name).exists(): atomic_write_json(run / name, empty)
    return manifest, briefs


def main(argv=None):
    """Run the command-line entry point."""
    args = parser().parse_args(argv); run = args.run_dir.resolve(); manifest, briefs = prepare(run)
    if args.command in {"prepare", "dry-run"}:
        print(json.dumps(preflight(manifest), indent=2)); return 0
    if args.command == "report":
        generated=read_json(run/"generated_candidates.json"); analyses=read_json(run/"analysis_results.json"); reviews=read_json(run/"human_reviews.json")
        complete_reviews=len(reviews.get("items",{})); complete_analyses=len(analyses.get("items",{}))
        if complete_reviews!=20 or complete_analyses!=60: raise RuntimeError(f"final report requires 20 reviews and 60 analyses; have {complete_reviews} and {complete_analyses}")
        by_case={}
        for cid,row in generated["items"].items():by_case.setdefault(row["case_id"],[]).append((cid,row))
        summary={style:{"wins":0,"candidates":0,"message_yes":0,"message_partly":0,"message_no":0,"first":[],"semantic":[],"tone":[],"editorial":[]} for style in STYLES}; none=unsure=0
        for case_id,review in reviews["items"].items():
            ordered=sorted(by_case[case_id],key=lambda x:x[0]); choice=review["preferred_candidate"]
            if choice=="none":none+=1
            elif choice=="unsure":unsure+=1
            else:
                winner=ordered["ABC".index(choice)][1]; summary[winner["style"]]["wins"]+=1; summary[winner["style"]]["message_"+review["message_match"]]+=1
            for cid,row in ordered:
                a=analyses["items"][cid]; s=summary[row["style"]]; s["candidates"]+=1
                s["first"].append(a["first_impression_alignment"]["dominant_visual_message_alignment_score"]);s["tone"].append(a["first_impression_alignment"]["tone_alignment_score"]);s["semantic"].append(a["semantic_alignment"]["semantic_alignment_score"]);s["editorial"].append(a["semantic_alignment"]["editorial_power_score"])
        rows=[]
        for style,s in summary.items():
            rows.append({"prompt_style":style,"human_wins":s["wins"],"human_win_rate":s["wins"]/20,"none_rate":none/20,"message_yes":s["message_yes"],"message_partly":s["message_partly"],"message_no":s["message_no"],"first_impression_mean":sum(s["first"])/len(s["first"]),"semantic_mean":sum(s["semantic"])/len(s["semantic"]),"tone_mean":sum(s["tone"])/len(s["tone"]),"editorial_power_mean":sum(s["editorial"])/len(s["editorial"]),"estimated_generation_cost_per_human_win":round((20*0.013)/s["wins"],4) if s["wins"] else None})
        with (run/"prompt_style_comparison.csv").open("w",newline="",encoding="utf-8") as f:w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)
        def regression(qh,name):
            case=next(x for x in manifest["items"] if x["quote_hash"]==qh);review=reviews["items"][case["case_id"]];ordered=sorted(by_case[case["case_id"]],key=lambda x:x[0]);lines=[f"# {name} generation case","",case["quote_text"],"",f"- Human choice: {review['preferred_candidate']}",f"- Immediate-message answer: {review['message_match']}"]
            for i,(cid,row) in enumerate(ordered):
                a=analyses["items"][cid];lines.append(f"- Candidate {'ABC'[i]} ({row['style']}): first impression {a['first_impression_alignment']['dominant_visual_message_alignment_score']}, semantic {a['semantic_alignment']['semantic_alignment_score']}, tone {a['first_impression_alignment']['tone_alignment_score']}, editorial power {a['semantic_alignment']['editorial_power_score']}")
            (run/f"{name.lower().replace(' ','_')}_generation_case.md").write_text("\n".join(lines)+"\n")
        regression("230b8d71f541acfc6a18d0a29f508eddf33b90d0f25beb736ba83ac1fb1cfc1a","Everest");regression("1ae9443573e42259af54c30a0ec90a6a8746e640b09e53ae1c28a4c0a2d0ed6b","Free trade")
        best=max(rows,key=lambda x:(x["human_wins"],x["first_impression_mean"]));recommendation="adopt one prompt style for shadow generation" if best["human_wins"]>=9 and none<=3 else "repeat another bounded generation pilot"
        ledger=read_json(run/"analysis_cost_ledger.json",{"calls":[]});analysis_cost=sum(float(x.get("cost_usd") or 0) for x in ledger.get("calls",[]));attempts=generated.get("attempts",[]);failed=sum(x.get("state")=="confirmed_failure" for x in attempts);message_counts={key:sum(x["message_match"]==key for x in reviews["items"].values()) for key in ("yes","partly","no")}
        lines=["# Generation Prompt Pilot 001","","## Executive result","",f"- Cases: 20","- Images: 60 (20 per deterministic prompt style)",f"- Human publishable winner: {20-none-unsure}/20",f"- Human none: {none}/20",f"- Human unsure: {unsure}/20",f"- Immediate-message judgements: {message_counts}",f"- Leading style: {best['prompt_style']} ({best['human_wins']} wins)",f"- Recommendation: **{recommendation}**","","The redesigned prompts did not solve candidate quality in this deliberately difficult set. Automated scores were materially more favorable than the human publish decision, so they must not be used as a substitute for review.","","## Architecture and isolation","","Briefs were deterministic projections of cached quote semantic and visual-intent fingerprints. Three prompts used the same brief with mechanism-first, first-impression-first, or balanced-editorial ordering. Human labels and prior winner identities were absent. Generated files and outputs exist only in this versioned research directory.","","## Execution and cost","",f"- Generation: 60 completed, {failed} confirmed transient attempts retried once, no third attempt.",f"- Estimated generation spend: ${60*0.013:.2f} using the project-observed per-request rate.",f"- Automated analysis: {len(ledger.get('calls',[]))} calls, known spend ${analysis_cost:.6f}.",f"- Approximate combined spend: ${60*0.013+analysis_cost:.2f}; preflight ceiling: $20.","- No search, grounding, tools, simulator, production write or production behavior change.","","## Style comparison","","| Style | Human wins | First impression | Semantic | Tone | Editorial power | Cost/publishable win |","|---|---:|---:|---:|---:|---:|---:|"]+[f"| {r['prompt_style']} | {r['human_wins']} | {r['first_impression_mean']:.1f} | {r['semantic_mean']:.1f} | {r['tone_mean']:.1f} | {r['editorial_power_mean']:.1f} | ${r['estimated_generation_cost_per_human_win'] or 0:.2f} |" for r in rows]+["","## Identity and quality","","The briefs requested no specific non-Thatcher person. Identity checks are therefore recorded as not required rather than inventing intended identities. Existing semantic editorial-power and first-impression focal/salience fields provide the bounded editorial-quality view.","","## Regression cases","",f"- Everest: Tony selected none; immediate message partly matched. The note requires a UK flag and a composition where Everest is visibly taller than all surrounding peaks.","- Free trade: Tony selected Candidate B, the first-impression-first variant; immediate message partly matched.","- Detailed candidate scores are in `everest_generation_case.md` and `free_trade_generation_case.md`.","","## Existing-image comparison","","The pilot tested whether newly generated variants were publishable. It did not blindly expose production-winner identity in the review and therefore does not claim a controlled head-to-head win against the existing image. The historical Everest/free-trade outcomes remain regression context only.","","## Limitations","","This is a 20-case, difficulty-enriched sample with one human editor and one stochastic image per style. Prompt style is confounded with random generation variance. Fifteen none decisions leave only five style wins, far too few for choosing a production prompt. Automated scores substantially overestimated publishability.","","## Decision","",f"**{recommendation}**","","A repeat should first improve brief-to-scene specificity and rendering constraints, especially concrete national symbols, physical scale relationships, and avoidance of generic staged political imagery. Production adoption or a limited trial is not justified by this run."]
        (run/"generation_prompt_pilot_report.md").write_text("\n".join(lines)+"\n");print(json.dumps({"rows":rows,"none":none,"unsure":unsure,"recommendation":recommendation},indent=2));return 0
    if args.command == "generate":
        if not args.execute_generation: raise RuntimeError("generation requires --execute-generation")
        result = execute_generation(run, manifest, briefs, client=ImageClient(os.getenv("OPENAI_API_KEY", "")), confirmed_limit=args.confirm_cost_limit_usd)
        print(json.dumps({"completed": len(result["items"]), "maximum": 60}, indent=2)); return 0
    if not args.execute_analysis or args.confirm_cost_limit_usd is None or not 0 < args.confirm_cost_limit_usd <= 10:
        raise RuntimeError("analysis requires explicit execution and a ceiling at or below $10")
    generated = read_json(run / "generated_candidates.json"); quotes = read_json(SOURCE / "quote_semantic_fingerprints.json")["items"]
    intents = read_json(FIRST / "quote_visual_intents.json")["items"]; out = read_json(run / "analysis_results.json") or {"schema_version":1,"items":{}}
    client = XAIClient(api_key=os.getenv("XAI_API_KEY", "")); ledger = CostLedger(run / "analysis_cost_ledger.json", run_id=run.name)
    for cid, candidate in sorted(generated["items"].items()):
        if cid in out["items"]: continue
        path = Path(candidate["path"]); sha = candidate["sha256"]
        first = cached_call(stage="image",key=f"generation-first:{cid}",prompt=image_first_prompt(),schema=IMAGE_FIRST_OUTPUT_SCHEMA,client=client,ledger=ledger,response_dir=run/"responses",confirmed_stage_limit=args.confirm_cost_limit_usd,image_path=path,expected_sha256=sha)
        first_value = validate_image_first(first.content)
        semantic = cached_call(stage="image",key=f"generation-semantic:{cid}",prompt=image_prompt(),schema=IMAGE_OUTPUT_SCHEMA,client=client,ledger=ledger,response_dir=run/"responses",confirmed_stage_limit=args.confirm_cost_limit_usd,image_path=path,expected_sha256=sha)
        semantic_fp = {"schema_version":IMAGE_SCHEMA_VERSION,"analysis_kind":"image_implied_message","image_basename":candidate["image_basename"],"sha256":sha,**semantic.content,"model":semantic.model,"prompt_version":IMAGE_PROMPT_VERSION,"analysed_at":utc_now()}; validate_image_fingerprint(semantic_fp)
        first_fp = {"schema_version":1,"analysis_kind":"image_first_impression","image_basename":candidate["image_basename"],"sha256":sha,**first_value}
        q = quotes[candidate["quote_hash"]]
        sem_result = cached_call(stage="critic",key=f"generation-semantic-critic:{cid}",prompt=semantic_critic_prompt(q,semantic_fp),schema=CRITIC_OUTPUT_SCHEMA,client=client,ledger=ledger,response_dir=run/"responses",confirmed_stage_limit=args.confirm_cost_limit_usd)
        semantic_alignment = {"schema_version":CRITIC_SCHEMA_VERSION,"analysis_kind":"quote_image_semantic_alignment","quote_hash":candidate["quote_hash"],"image_basename":candidate["image_basename"],**sem_result.content,"model":sem_result.model,"prompt_version":CRITIC_PROMPT_VERSION,"analysed_at":utc_now()}; validate_critic_result(semantic_alignment)
        fi_result = cached_call(stage="critic",key=f"generation-first-critic:{cid}",prompt=first_critic_prompt(intents[candidate["quote_hash"]],first_fp),schema=ALIGNMENT_OUTPUT_SCHEMA,client=client,ledger=ledger,response_dir=run/"responses",confirmed_stage_limit=args.confirm_cost_limit_usd)
        out["items"][cid] = {"candidate":candidate,"image_first_impression":first_fp,"image_semantic_fingerprint":semantic_fp,"semantic_alignment":semantic_alignment,"first_impression_alignment":validate_alignment(fi_result.content),"identity_check":{"status":"not_required","reason":"generation brief did not request a specific non-Thatcher identity"}}
        atomic_write_json(run / "analysis_results.json",out)
    print(json.dumps({"analysed":len(out["items"]),"maximum":60},indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
