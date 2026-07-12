#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path

from semantic_alignment.bakeoff import PRICES, ProviderClient, common_prompt, estimate_tokens
from semantic_alignment.io import atomic_write_json, atomic_write_text, read_json
from semantic_alignment.large_bakeoff import Worker, verify_manifest

SOURCE_RUN=Path('semantic_alignment_research/provider_bakeoff_250_20260712_v1')
FINGERPRINT_RUN=Path('semantic_alignment_research/runs/v2_20260711T111526Z')
OLD_RUN=Path('semantic_alignment_research/provider_bakeoff_25_20260712')
RECOVERY=Path('semantic_alignment_research/provider_bakeoff_250_20260712_v1/gemini_recovery_23_v1')
CEILING=1.50

class RecoveryBudget:
    def __init__(self,run_dir:Path):self.run_dir=run_dir
    def guard(self,provider,provider_spend,next_max):
        if provider!='gemini' or provider_spend+next_max>CEILING:
            raise RuntimeError('Gemini recovery cost ceiling reached')

class PacedClient:
    def __init__(self,client,seconds=12):self.client=client;self.model=client.model;self.seconds=seconds;self.last=None
    def call(self,prompt):
        if self.last is not None:
            time.sleep(max(0,self.seconds-(time.monotonic()-self.last)))
        self.last=time.monotonic()
        return self.client.call(prompt)

def inputs():
    q=json.load(open(FINGERPRINT_RUN/'quote_semantic_fingerprints.json'))['items']
    i=json.load(open(FINGERPRINT_RUN/'image_implied_messages_generated.json'))['items']
    old=json.load(open(OLD_RUN/'cases.json'))['items']
    manifest=json.load(open(SOURCE_RUN/'cases.json'))
    verify_manifest(manifest,q,i,old)
    exhausted=set((json.load(open(SOURCE_RUN/'gemini_ledger.json')).get('exhausted') or {}))
    cases=[x for x in manifest['items'] if x['case_id'] in exhausted]
    if len(cases)!=23:raise RuntimeError(f'expected 23 exhausted cases, found {len(cases)}')
    return q,i,manifest,cases

def preflight(cases,q,i):
    input_tokens=sum(estimate_tokens(common_prompt(q[x['quote_hash']],i[x['image_basename']])) for x in cases)
    expected_output=900*len(cases); maximum_output=1800*len(cases)
    expected=input_tokens*PRICES['gemini']['input']/1e6+expected_output*PRICES['gemini']['output']/1e6
    maximum=input_tokens*PRICES['gemini']['input']/1e6+maximum_output*PRICES['gemini']['output']/1e6
    return {'cases':len(cases),'model':'gemini-3.1-pro-preview','estimated_input_tokens':input_tokens,
            'estimated_output_tokens':expected_output,'expected_cost_usd':expected,
            'conservative_base_cost_usd':maximum,'maximum_two_attempt_exposure_usd':maximum*2,
            'ceiling_usd':CEILING,'pacing_seconds':12,'max_attempts_per_case':2,'tools_enabled':False}

def merge(cases):
    original=json.load(open(SOURCE_RUN/'gemini_results.json')); recovered=read_json(RECOVERY/'gemini_results.json',{}) or {'items':{}}
    merged=json.loads(json.dumps(original)); merged['items'].update(recovered.get('items',{}))
    merged['recovery_provenance']={'original_results':str(SOURCE_RUN/'gemini_results.json'),'recovery_results':str(RECOVERY/'gemini_results.json'),'recovered_case_ids':sorted(recovered.get('items',{}))}
    atomic_write_json(RECOVERY/'gemini_results_merged_derived.json',merged)
    return merged

def report(cases,pf,summary,merged):
    ledger=read_json(RECOVERY/'gemini_ledger.json',{}) or {}; recovered=read_json(RECOVERY/'gemini_results.json',{}) or {'items':{}}
    failures=ledger.get('confirmed_failures',[])
    lines=['# Gemini exhausted-case recovery','',f'- Frozen cases targeted: {len(cases)}',f'- Recovered: {len(recovered.get("items",{}))}',f'- Still exhausted: {len(ledger.get("exhausted",{}))}',f'- Attempts: {len(ledger.get("attempts",[]))}',f'- Model: `{pf["model"]}`',f'- Known recovery cost: ${summary.get("known_cost_usd",0):.6f}',f'- Ambiguous exposure: ${summary.get("uncertain_possible_exposure_usd",0):.6f}',f'- Derived merged Gemini coverage: {len(merged["items"])}/250','','## Failures','']
    if failures:
        for x in failures:lines.append(f'- `{x.get("case_id")}` attempt {x.get("attempt_number")}: HTTP {x.get("http_status",x.get("status"))}; provider status `{x.get("provider_status")}`; code `{x.get("provider_code")}`; message `{x.get("message")}`')
    else:lines.append('- None.')
    lines += ['','The original Gemini result and ledger files were not modified. No other provider was called. No tools, search or grounding were enabled.']
    atomic_write_text(RECOVERY/'recovery_report.md','\n'.join(lines)+'\n')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=('dry-run','execute','report'));parser.add_argument('--confirm-cost-limit-usd',type=float);args=parser.parse_args()
    q,i,manifest,cases=inputs();RECOVERY.mkdir(parents=True,exist_ok=True)
    atomic_write_json(RECOVERY/'cases.json',{'schema_version':1,'source_manifest':str(SOURCE_RUN/'cases.json'),'case_count':23,'items':cases})
    pf=preflight(cases,q,i);atomic_write_json(RECOVERY/'preflight.json',pf)
    if pf['maximum_two_attempt_exposure_usd']>CEILING:raise SystemExit('preflight exceeds recovery ceiling')
    if args.command=='dry-run':print(json.dumps(pf,indent=2));return
    if args.command=='execute':
        if args.confirm_cost_limit_usd!=CEILING:raise SystemExit('exact $1.50 confirmation required')
        key=os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY')
        client=PacedClient(ProviderClient('gemini',key),12)
        summary=Worker('gemini',cases,q,i,RECOVERY,client,RecoveryBudget(RECOVERY)).run()
        merged=merge(cases);report(cases,pf,summary,merged);print(json.dumps(summary,indent=2));return
    summary=read_json(RECOVERY/'gemini_worker_summary.json',{}) or {};merged=merge(cases);report(cases,pf,summary,merged)

if __name__=='__main__':main()
