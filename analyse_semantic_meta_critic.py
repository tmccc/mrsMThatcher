#!/usr/bin/env python3
"""Analyse semantic meta critic artefacts."""

from __future__ import annotations
import argparse,json,statistics
from collections import Counter
from pathlib import Path
from semantic_alignment.bakeoff import FREE_TRADE_KEY
from semantic_alignment.io import atomic_write_json,atomic_write_text
from semantic_alignment.meta_critic import PROVIDERS,analyse_case,policy_summary,priority,validate_weights
from semantic_alignment.disagreement import disagreement,evaluation,safeguarded_policies

def main(argv=None):
    """Run the command-line entry point."""
    ap=argparse.ArgumentParser(); ap.add_argument('--bakeoff-dir',type=Path,default=Path('semantic_alignment_research/provider_bakeoff_25_20260712')); ap.add_argument('--output-dir',type=Path,default=Path('semantic_alignment_research/meta_critic')); ap.add_argument('--weights',type=Path); args=ap.parse_args(argv)
    b=args.bakeoff_dir.resolve(); out=args.output_dir.resolve(); out.mkdir(parents=True,exist_ok=True); cases=json.load(open(b/'cases.json'))['items']; results={p:json.load(open(b/f'{p}_results.json'))['items'] for p in PROVIDERS}
    weight_data=json.load(open(args.weights)) if args.weights else None
    try: weights,source,sample=validate_weights(weight_data)
    except ValueError as exc: raise SystemExit(str(exc))
    atomic_write_json(out/'provider_weights.json',{'schema_version':1,'weights':weights,'source':source,'sample_size':sample,'minimum_human_cases':20,'cap':1.5})
    items=[]; disagreements=[]; queue=[]
    for case in cases:
        rows={p:results[p][case['case_id']] for p in PROVIDERS}; meta=analyse_case(case,rows,weights=weights); d=disagreement(case['case_id'],rows); meta.update({'disagreement_score':d['disagreement_score'],'disagreement_band':d['disagreement_band']}); items.append(meta); disagreements.append(d); band,reasons,value=priority(meta,free_trade=(case['quote_hash'],case['image_basename'])==FREE_TRADE_KEY)
        if meta['operational_consensus']=='two_two_split': band='urgent'; reasons.append('two_to_two_operational_split')
        elif d['disagreement_band']=='extreme': band='urgent'; reasons.append('extreme_disagreement')
        elif d['disagreement_band']=='high' and not meta['requires_human_review']: band='high'; reasons.append('high_disagreement_automatic_recommendation')
        elif d['components']['internal_inconsistency']>=50: band='high'; reasons.append('high_internal_inconsistency')
        queue.append({'case_id':case['case_id'],'priority':band,'review_priority':band,'review_reasons':sorted(set(reasons)),'disagreement_band':d['disagreement_band'],'disagreement_score':d['disagreement_score'],'disagreement_reasons':d['explanation'],'recommended_action':meta['recommended_action'],'confidence':meta['confidence'],'automatic':not meta['requires_human_review'],'provider_decisions':{p:rows[p]['keep_or_replace'] for p in PROVIDERS},'estimated_review_value':value})
    rank={'urgent':0,'high':1,'medium':2,'low':3,'control':4}; queue.sort(key=lambda x:(rank[x['priority']],-x['disagreement_score'],-x['estimated_review_value'],x['case_id']))
    human_path=out/'human_validation.json'; human_doc=json.load(open(human_path)) if human_path.exists() else {'schema_version':1,'analysis_kind':'meta_critic_human_validation','items':{}}
    if not human_path.exists(): atomic_write_json(human_path,human_doc)
    human={k:v.get('human_action') for k,v in human_doc.get('items',{}).items()}; policies=policy_summary(items,human)
    extra={p:{x['case_id']:safeguarded_policies(x,next(d for d in disagreements if d['case_id']==x['case_id']))[p] for x in items} for p in 'FGH'}
    for p,preds in extra.items(): policies[p]={**evaluation(preds,human),'review_burden':sum(v=='defer' for v in preds.values())}
    atomic_write_json(out/'meta_results.json',{'schema_version':2,'providers':list(PROVIDERS),'items':items}); atomic_write_json(out/'disagreement_results.json',{'schema_version':1,'formula':{'operational':.35,'taxonomy':.25,'score_dispersion':.15,'rationale':.15,'internal_inconsistency':.10},'bands':{'low':'0-19','moderate':'20-44','high':'45-69','extreme':'70-100'},'items':disagreements}); atomic_write_json(out/'review_queue.json',{'schema_version':2,'items':queue}); atomic_write_json(out/'policy_comparison.json',{'schema_version':2,'human_labels_available':len(human),'policies':policies})
    confidence=Counter(x['confidence'] for x in items); actions=Counter(x['recommended_action'] for x in items); levels=Counter(x['operational_consensus'] for x in items); free=next(x for x in items if x['case_id']=='55f5cd6d633c143f5170')
    lines=['# Deterministic semantic meta-critic report','','## Summary','',f'- Cases: {len(items)}',f'- Recommended actions: {dict(actions)}',f'- Confidence: {dict(confidence)}',f'- Operational consensus: {dict(levels)}',f'- Human review required: {sum(x["requires_human_review"] for x in items)}',f'- Queue priorities: {dict(Counter(x["priority"] for x in queue))}','','## Policy coverage','', '| policy | resolved | coverage | review burden | human accuracy |','|---|---:|---:|---:|---|']
    for p,row in policies.items():
        accuracy='unavailable' if row.get('accuracy') is None else f"{row['accuracy']:.0%}"
        lines.append(f'| {p} | {row["resolved"]} | {row["coverage"]:.0%} | {row["review_burden"]} | {accuracy} |')
    lines += ['','Human accuracy is unavailable: existing A/B preferences are not direct four-provider keep/replace labels.','','## Free-trade case','',f'- Relationship votes: {free["primary_relationship_votes"]}',f'- Operational votes: {free["operational_votes"]}',f'- Scores: {free["score_summary"]}',f'- Recommendation: {free["recommended_action"]}',f'- Confidence: {free["confidence"]}',f'- Human review: {free["requires_human_review"]}',f'- Reasons: {free["review_reasons"]}','','## Limitations','','Consensus is not ground truth. Equal provider weights are used until at least 20 compatible human-labelled cases exist. This is offline research and has no production effect.']
    bands=Counter(x['disagreement_band'] for x in disagreements); free_d=next(x for x in disagreements if x['case_id']==free['case_id']); report=['# Disagreement index report','',f'- Bands: {dict(bands)}',f'- Mean score: {statistics.mean(x["disagreement_score"] for x in disagreements):.1f}',f'- High/extreme automatic recommendations: {sum(x["disagreement_band"] in {"high","extreme"} and not m["requires_human_review"] for x,m in zip(disagreements,items))}','','## Free-trade case','',f'- Operational votes: {free["operational_votes"]}',f'- Taxonomy votes: {free["primary_relationship_votes"]}',f'- Disagreement: {free_d["disagreement_score"]} ({free_d["disagreement_band"]})',f'- Components: {free_d["components"]}',f'- Confidence/recommendation: {free["confidence"]} / {free["recommended_action"]}',f'- Prioritised: {next(x for x in queue if x["case_id"]==free["case_id"])["priority"]}',f'- Explanation: {free_d["explanation"]}','','Confidence describes recommendation evidence; disagreement describes cross-provider review risk. Either may be high independently.']; atomic_write_text(out/'disagreement_report.md','\n'.join(report)+'\n')
    atomic_write_text(out/'meta_critic_report.md','\n'.join(lines)+'\n'); print(json.dumps({'cases':len(items),'actions':dict(actions),'review_required':sum(x['requires_human_review'] for x in items),'disagreement_bands':dict(bands),'policy_coverage':{p:r['coverage'] for p,r in policies.items()}},indent=2))
if __name__=='__main__': raise SystemExit(main())
