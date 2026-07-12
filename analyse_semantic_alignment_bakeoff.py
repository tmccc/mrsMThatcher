#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import statistics
from pathlib import Path

from semantic_alignment.bakeoff import (COMBINED_CEILING, FREE_TRADE_KEY,
    PROVIDER_CEILINGS, PROVIDER_MODELS, ProviderClient, compare_n_results,
    compare_results, create_blinding, create_four_way_blinding, create_three_way_blinding, preflight,
    run_provider, select_cases)
from semantic_alignment.io import atomic_write_json, atomic_write_text, read_json

DEFAULT_SOURCE=Path("semantic_alignment_research/runs/v2_20260711T111526Z")
DEFAULT_OUTPUT=Path("semantic_alignment_research/provider_bakeoff_25_20260712")


def parser():
    ap=argparse.ArgumentParser(description="Blinded provider critic bake-off; external calls require explicit provider flags.")
    ap.add_argument("--source-run",type=Path,default=DEFAULT_SOURCE); ap.add_argument("--output-dir",type=Path,default=DEFAULT_OUTPUT)
    sub=ap.add_subparsers(dest="command",required=True)
    sub.add_parser("prepare"); sub.add_parser("dry-run"); sub.add_parser("compare"); sub.add_parser("compare-three"); sub.add_parser("compare-four"); sub.add_parser("status")
    run=sub.add_parser("run-provider"); run.add_argument("--provider",choices=("grok","openai","anthropic","gemini"),required=True); run.add_argument("--execute-grok",action="store_true"); run.add_argument("--execute-openai",action="store_true"); run.add_argument("--execute-claude",action="store_true"); run.add_argument("--execute-gemini",action="store_true"); run.add_argument("--confirm-provider-cost-limit-usd",type=float); run.add_argument("--confirm-total-cost-limit-usd",type=float)
    return ap


def load_source(source):
    read=lambda name:json.loads((source/name).read_text())
    return read("quote_semantic_fingerprints.json")["items"],read("image_implied_messages_generated.json")["items"],read("semantic_alignment_critic.json")["items"]


def verify_pricing(output):
    openai=read_json(output/"pricing/openai_models.json",{}); grok=read_json(output/"pricing/grok_models.json",{})
    openai_ids={row.get("id") for row in openai.get("data",[])}; grok_rows={row.get("id"):row for row in grok.get("data",[])}
    if PROVIDER_MODELS["openai"] not in openai_ids: raise RuntimeError("selected OpenAI model absent from authenticated model list")
    grow=grok_rows.get(PROVIDER_MODELS["grok"])
    if not grow or (grow.get("prompt_text_token_price"),grow.get("cached_prompt_text_token_price"),grow.get("completion_text_token_price"))!=(20000,5000,60000): raise RuntimeError("Grok authenticated pricing differs from approved rates")
    result={"schema_version":1,"verified":True,"openai":{"model":"gpt-5.6-sol","authenticated_model_list":True,"input_usd_per_million":5,"cached_input_usd_per_million":.5,"output_usd_per_million":30,"source":"https://developers.openai.com/api/docs/models/gpt-5.6-sol","responses_api":True,"structured_outputs":True},"grok":{"model":"grok-4.5","authenticated_model_list":True,"input_usd_per_million":2,"cached_input_usd_per_million":.5,"output_usd_per_million":6,"metadata_prices_ticks":[20000,5000,60000],"ticks_per_usd":10000000000},"tools_enabled":False}
    atomic_write_json(output/"pricing/pricing_verification.json",result); return result


def verify_anthropic(output):
    data=read_json(output/"pricing/anthropic_models.json",{}); row=next((x for x in data.get("data",[]) if x.get("id")==PROVIDER_MODELS["anthropic"]),None)
    if not row: raise RuntimeError("Claude Sonnet 4.6 absent from authenticated Anthropic model list")
    if not ((row.get("capabilities") or {}).get("structured_outputs") or {}).get("supported"): raise RuntimeError("selected Claude model lacks structured outputs")
    result={"schema_version":1,"verified":True,"model":row,"prices_usd_per_million":{"input":3.0,"cached_input":0.30,"output":15.0},"source":"https://platform.claude.com/docs/en/about-claude/pricing","tools_enabled":False}; atomic_write_json(output/"pricing/anthropic_pricing_verification.json",result); return result


def verify_gemini(output):
    data=read_json(output/"pricing/gemini_models_retry.json",{}) or read_json(output/"pricing/gemini_models.json",{}); row=next((x for x in data.get("models",[]) if x.get("name")=="models/"+PROVIDER_MODELS["gemini"]),None)
    if not row or "generateContent" not in row.get("supportedGenerationMethods",[]): raise RuntimeError("Gemini 3.1 Pro Preview unavailable")
    result={"schema_version":1,"verified":True,"model":row,"stable":False,"preview_required_because_stable_2_5_unavailable_to_new_users":True,"structured_outputs":True,"prices_usd_per_million":{"input":2.0,"cached_input":0.20,"output_including_thinking":12.0},"source":"https://ai.google.dev/gemini-api/docs/pricing","tools_enabled":False}; atomic_write_json(output/"pricing/gemini_pricing_verification.json",result); return result


def ensure_prepared(source,output):
    output.mkdir(parents=True,exist_ok=True); quotes,images,old=load_source(source); cases_path=output/"cases.json"
    if not cases_path.exists(): atomic_write_json(cases_path,select_cases(list(old.values()),quotes,images))
    create_blinding(output); return quotes,images,old,read_json(cases_path)["items"]


def provider_usage(output,provider):
    ledger=read_json(output/f"{provider}_cost_ledger.json",{}) or {}; rows=ledger.get("calls",[])
    return {"calls":len(rows),"input_tokens":sum(x.get("input_tokens",0) for x in rows),"cached_tokens":sum(x.get("cached_tokens",0) for x in rows),"reasoning_tokens":sum(x.get("reasoning_tokens",0) for x in rows),"output_tokens":sum(x.get("output_tokens",0) for x in rows),"cost_usd":sum(x.get("cost_usd",0) for x in rows),"latency_mean":statistics.mean([x["latency_seconds"] for x in rows]) if rows else None,"latency_median":statistics.median([x["latency_seconds"] for x in rows]) if rows else None,"latency_total":sum(x.get("latency_seconds",0) for x in rows),"blocked":ledger.get("blocked",False),"ambiguous_requests":len(ledger.get("ambiguous_requests",[])),"retries":sum(x.get("retry_count",0) for x in rows)}


def write_report(output,comparison,cases,quotes,images):
    usage={p:provider_usage(output,p) for p in ("grok","openai")}; free=next(x for x in cases if (x["quote_hash"],x["image_basename"])==FREE_TRADE_KEY); grok=read_json(output/"grok_results.json")["items"][free["case_id"]]; openai=read_json(output/"openai_results.json")["items"][free["case_id"]]
    mapping=read_json(output/"sealed_provider_mapping.json"); provider_to_critic={provider:critic for critic,provider in mapping.items()}; results={"grok":grok,"openai":openai}
    def blinded(value):
        if not isinstance(value,dict): return value
        return {provider_to_critic.get(key,key):blinded(item) for key,item in value.items()}
    lines=["# Blinded 25-case critic provider bake-off","","> Provider identities remain sealed for human review. Comparative statistics and case judgements use Critic A/B labels.","","## Models and pricing","",f"- Models used (mapping sealed): `{PROVIDER_MODELS['grok']}` and `{PROVIDER_MODELS['openai']}`.","- Unit prices: Grok $2 input / $0.50 cached / $6 output; OpenAI $5 input / $0.50 cached / $30 output per million tokens.","- Both: low reasoning, 1,600 output-token cap, strict schema, no tools.","","## Usage by model (not linked to Critic A/B)",""]
    for p,row in usage.items(): lines.append(f"- {PROVIDER_MODELS[p]}: calls={row['calls']}, input={row['input_tokens']}, cached={row['cached_tokens']}, reasoning={row['reasoning_tokens']}, output={row['output_tokens']}, cost=${row['cost_usd']:.6f}, mean latency={row['latency_mean']:.3f}s, retries={row['retries']}, ambiguous={row['ambiguous_requests']}")
    lines += ["","## Agreement","",f"- Exact primary relationship: {comparison['primary_exact_agreement']:.1%}",f"- Primary-or-secondary overlap: {comparison['primary_or_secondary_overlap']:.1%}",f"- Cohen's kappa: {comparison['cohens_kappa']}",f"- Unrelated: {blinded(comparison['unrelated_counts'])}",f"- Claimed consequence: {blinded(comparison['consequence_counts'])}",f"- Ideological substitution: {blinded(comparison['ideological_substitution_counts'])}",f"- Keep/replace: {blinded(comparison['keep_replace'])}","","## Free-trade case",""]
    for critic in ("Critic A","Critic B"):
        row=results[mapping[critic]]
        lines += [f"### {critic}",f"- Relationships: {row['primary_relationship']} / {row['secondary_relationship']}",f"- Relevance/directness: {row['relevance_score']} / {row['directness_score']}",f"- Mechanism/consequence/principle: {row['mechanism_alignment_score']} / {row['consequence_alignment_score']} / {row['principle_alignment_score']}",f"- Decision: {row['keep_or_replace']}",f"- Explanation: {row['explanation']}",f"- Stronger direction: {row['stronger_visual_direction']}",""]
    lines += ["## Provisional recommendation","",comparison["provisional_recommendation"],"","Human preference review is required before preferring a provider. Higher average scores alone are not treated as better calibration.","","## Blinded review", "", "Run the loopback review command documented in the execution report. Provider mapping is stored separately with mode 0600."]
    atomic_write_text(output/"bakeoff_report.md","\n".join(lines)+"\n"); atomic_write_json(output/"bakeoff_execution_summary.json",{"usage":usage,"comparison":comparison,"free_trade":{"case":free,"grok":grok,"openai":openai}})


def main(argv=None):
    args=parser().parse_args(argv); source=args.source_run.resolve(); output=args.output_dir.resolve(); quotes,images,old,cases=ensure_prepared(source,output)
    if args.command=="prepare": print(f"cases={len(cases)}"); return 0
    estimate=preflight(cases,quotes,images); atomic_write_json(output/"preflight_four_provider.json",estimate)
    if args.command=="dry-run": verify_pricing(output); verify_anthropic(output); verify_gemini(output); print(json.dumps(estimate,indent=2)); return 0
    if args.command=="status": print(json.dumps({p:provider_usage(output,p) for p in ("grok","openai","anthropic","gemini")},indent=2)); return 0
    if args.command=="run-provider":
        verify_pricing(output); provider=args.provider
        if provider=="anthropic": verify_anthropic(output)
        if provider=="gemini": verify_gemini(output)
        enabled={"grok":args.execute_grok,"openai":args.execute_openai,"anthropic":args.execute_claude,"gemini":args.execute_gemini}[provider]
        if not enabled: raise RuntimeError(f"explicit execution flag required for {provider}")
        if args.confirm_total_cost_limit_usd is None or args.confirm_total_cost_limit_usd>COMBINED_CEILING: raise RuntimeError("confirmed total limit must be present and <= $3")
        limit=args.confirm_provider_cost_limit_usd
        if limit is None or limit>PROVIDER_CEILINGS[provider] or estimate["providers"][provider]["conservative_maximum_cost_usd"]>limit: raise RuntimeError("provider estimate exceeds confirmed/hard limit")
        env={"grok":"XAI_API_KEY","openai":"OPENAI_API_KEY","anthropic":"ANTHROPIC_API_KEY","gemini":"GEMINI_API_KEY"}[provider]; key=os.getenv(env,"") or (os.getenv("GOOGLE_API_KEY","") if provider=="gemini" else ""); client=ProviderClient(provider,key); run_provider(provider,cases,quotes,images,output,client,limit); return 0
    if args.command=="compare":
        grok=read_json(output/"grok_results.json"); openai=read_json(output/"openai_results.json"); comparison=compare_results(cases,grok,openai); atomic_write_json(output/"comparison.json",comparison); write_report(output,comparison,cases,quotes,images); print(json.dumps(comparison,indent=2)); return 0
    if args.command=="compare-three":
        results={p:read_json(output/f"{p}_results.json") for p in ("grok","openai","anthropic")}; comparison=compare_n_results(cases,results); atomic_write_json(output/"comparison_three_provider.json",comparison); create_three_way_blinding(output); print(json.dumps(comparison,indent=2)); return 0
    if args.command=="compare-four":
        results={p:read_json(output/f"{p}_results.json") for p in ("grok","openai","anthropic","gemini")}; comparison=compare_n_results(cases,results); atomic_write_json(output/"comparison_four_provider.json",comparison); create_four_way_blinding(output); print(json.dumps(comparison,indent=2)); return 0
    return 2

if __name__=="__main__": raise SystemExit(main())
