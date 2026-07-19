#!/usr/bin/env python3
"""Calibrate semantic alignment."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from semantic_alignment.calibration import (CALIBRATION_CRITIC_SCHEMA,
    build_review_dataset, failure_summary, labelled_review_statistics,
    merge_review_data, migrate_review_dataset, review_is_complete,
    review_progress, threshold_analysis, validate_calibration_critic,
    validate_human_label)
from semantic_alignment.calibration_prompt import calibration_critic_prompt
from semantic_alignment.io import atomic_write_json, atomic_write_text
from semantic_alignment.pipeline import CostLedger, XAIClient, cached_call

FREE_KEY=("1ae9443573e42259af54c30a0ec90a6a8746e640b09e53ae1c28a4c0a2d0ed6b","tg_661b01c39a8d223df51cd0365e79ffe7e3c4f86ac81fa0af114ce95be49cb831.png")
OBSERVED_CRITIC_MEAN_USD=0.00904056


def load_run(run: Path):
    """Load run."""
    read=lambda name: json.loads((run/name).read_text())
    return read('quote_semantic_fingerprints.json'),read('image_implied_messages_generated.json'),read('semantic_alignment_critic.json')


def parser():
    """Build the command-line argument parser."""
    ap=argparse.ArgumentParser(description='Offline human calibration; paid calls require complete human labels.')
    ap.add_argument('--run-dir',type=Path,required=True); sub=ap.add_subparsers(dest='command',required=True)
    sub.add_parser('build-review')
    sub.add_parser('status')
    sub.add_parser('report')
    sub.add_parser('threshold-analysis')
    prompt=sub.add_parser('render-prompt'); prompt.add_argument('--case-id',required=True)
    for split in ('calibration','holdout'):
        cmd=sub.add_parser(f'run-{split}'); cmd.add_argument('--execute-xai',action='store_true'); cmd.add_argument('--confirm-cost-limit-usd',type=float); cmd.add_argument('--model',default='grok-4.5')
    return ap


def require_labels(dataset: dict, split: str):
    """Require labels."""
    rows=[x for x in dataset['items'] if x['split']==split]
    if len(rows)!=25: raise RuntimeError(f'{split} split must contain exactly 25 cases')
    missing=[]
    for row in rows:
        try: validate_human_label(row.get('human_label'))
        except ValueError: missing.append(row['case_id'])
    if missing: raise RuntimeError(f'{split} has {len(missing)} unlabelled/invalid human cases; no paid execution permitted')
    return rows


def main(argv=None):
    """Run the command-line entry point."""
    args=parser().parse_args(argv); run=args.run_dir.resolve(); calibration=run/'calibration'; calibration.mkdir(exist_ok=True)
    qdb,idb,cdb=load_run(run); dataset_path=calibration/'human_review_cases.json'
    if args.command=='build-review':
        fresh=build_review_dataset(list(cdb['items'].values()),qdb['items'],idb['items'],free_trade_key=FREE_KEY)
        existing=json.loads(dataset_path.read_text()) if dataset_path.exists() else None
        dataset=merge_review_data(fresh,existing)
        atomic_write_json(dataset_path,dataset); atomic_write_json(calibration/'critic_failure_summary.json',failure_summary(list(cdb['items'].values())))
        print('review_cases=100 calibration=25 holdout=25 reserve=50'); return 0
    dataset=migrate_review_dataset(json.loads(dataset_path.read_text()))
    model={'items':{}}
    for name in ('critic_calibration.json','critic_holdout.json'):
        path=calibration/name
        if path.exists(): model['items'].update((json.loads(path.read_text()).get('items') or {}))
    if args.command=='status':
        progress=review_progress(dataset); splits={s:sum(x['split']==s and review_is_complete(x) for x in dataset['items']) for s in ('calibration','holdout','reserve')}
        estimate=50*OBSERVED_CRITIC_MEAN_USD*1.10
        print(json.dumps({'progress':progress,'complete_by_split':splits,'estimated_50_call_cost_usd':round(estimate,6),'hard_stop_usd':1.5},indent=2)); return 0
    if args.command in {'report','threshold-analysis'}:
        stats=labelled_review_statistics(dataset,model)
        thresholds={"publish_likelihood":threshold_analysis(dataset,model,target="publish_likelihood"),"image_decision":threshold_analysis(dataset,model,target="image_decision")}
        atomic_write_json(calibration/'human_review_analysis.json',stats); atomic_write_json(calibration/'threshold_analysis.json',thresholds)
        if args.command=='report':
            lines=['# Human editorial calibration analysis','',f"Complete reviews: {stats['progress']['complete']}/{stats['progress']['total']}",f"Status: {'preliminary' if stats['preliminary'] else 'reviewed sample'}",'',f"Appropriateness distribution: `{stats['appropriateness_distribution']}`",f"Publish-likelihood distribution: `{stats['publish_likelihood_distribution']}`",f"Image-decision distribution: `{stats['image_decision_distribution']}`",f"Correlations: `{stats['correlations']}`",'',f"Decision by relationship: `{stats['decision_by_primary_relationship']}`",f"Decision by appropriateness: `{stats['decision_by_appropriateness_rating']}`",f"Decision by publish likelihood: `{stats['decision_by_publish_likelihood_rating']}`",f"Decision by critic overall band: `{stats['decision_by_critic_overall_band']}`",'',f"By relationship: `{stats['by_relationship']}`",'',f"By old critic score band: `{stats['by_old_critic_score_band']}`",'',f"High critic / prefer different: `{stats['high_critic_prefer_different']}`",f"Low critic / keep current: `{stats['low_critic_keep_current']}`",f"Appropriateness >=4 / prefer different: `{stats['high_appropriateness_prefer_different']}`",f"Publish likelihood <=2 / keep current: `{stats['low_publish_keep_current']}`",f"Unsure cases: `{stats['unsure_cases']}`",'',f"Threshold analyses: `{thresholds}`"]
            atomic_write_text(calibration/'human_review_analysis.md','\n'.join(lines)+'\n')
        print(json.dumps(thresholds if args.command=='threshold-analysis' else stats,indent=2)); return 0
    row=next(x for x in dataset['items'] if x['case_id']==getattr(args,'case_id',None)) if args.command=='render-prompt' else None
    if row:
        print(calibration_critic_prompt(qdb['items'][row['quote_hash']],idb['items'][row['image_basename']],row['quote_decomposition'],row['image_decomposition'])); return 0
    split=args.command.removeprefix('run-'); rows=require_labels(dataset,split)
    estimate=len(rows)*OBSERVED_CRITIC_MEAN_USD*1.10
    print(json.dumps({'split':split,'calls':len(rows),'estimated_cost_usd':round(estimate,6),'limit':args.confirm_cost_limit_usd},indent=2))
    if not args.execute_xai: return 0
    if args.confirm_cost_limit_usd is None or estimate > min(args.confirm_cost_limit_usd,1.5): raise RuntimeError('estimated cost exceeds confirmed calibration limit')
    output=calibration/f'critic_{split}.json'; db=json.loads(output.read_text()) if output.exists() else {'schema_version':1,'analysis_kind':'picture_editor_calibration_results','items':{},'failures':{}}
    ledger=CostLedger(calibration/'cost_ledger.json',run_id=f"{run.name}-calibration")
    client=XAIClient(api_key=os.getenv('XAI_API_KEY',''),model=args.model)
    for case in rows:
        key=case['case_id']
        if key in db['items']: continue
        result=cached_call(stage='critic',key=f"{split}:{key}",prompt=calibration_critic_prompt(qdb['items'][case['quote_hash']],idb['items'][case['image_basename']],case['quote_decomposition'],case['image_decomposition']),schema=CALIBRATION_CRITIC_SCHEMA,client=client,ledger=ledger,response_dir=calibration/'responses',confirmed_stage_limit=min(args.confirm_cost_limit_usd,1.5))
        db['items'][key]={'case_id':key,'split':split,**validate_calibration_critic(result.content),'model':result.model}
        atomic_write_json(output,db)
    return 0

if __name__=='__main__': raise SystemExit(main())
