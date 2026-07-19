#!/usr/bin/env python3
"""Analyse meta critic validation 250 artefacts."""

from __future__ import annotations
import argparse,csv,json,math
from collections import Counter
from pathlib import Path
from semantic_alignment.disagreement import safeguarded_policies
from semantic_alignment.io import atomic_write_json,atomic_write_text
from semantic_alignment.meta_critic import policy_actions

RUN=Path('semantic_alignment_research/provider_bakeoff_250_20260712_v1')
OUT=Path('semantic_alignment_research/meta_critic_validation_250')
PROVIDERS=('grok','openai','anthropic','gemini')

def wilson(k,n,z=1.96):
    """Return the wilson."""
    if not n:return None
    p=k/n;d=1+z*z/n;c=(p+z*z/(2*n))/d;h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return [max(0,c-h),min(1,c+h)]

def metrics(pred,human):
    """Return the metrics."""
    ids=sorted(set(pred)&set(human)); actionable=[k for k in ids if pred[k] in {'keep','replace'}]
    tp=sum(pred[k]=='keep' and human[k]=='keep' for k in actionable);fp=sum(pred[k]=='keep' and human[k]=='replace' for k in actionable)
    tn=sum(pred[k]=='replace' and human[k]=='replace' for k in actionable);fn=sum(pred[k]=='replace' and human[k]=='keep' for k in actionable);correct=tp+tn
    return {'available':len(ids),'actionable':len(actionable),'coverage':len(actionable)/len(ids) if ids else None,
            'accuracy':correct/len(actionable) if actionable else None,'correct':correct,'false_keep':fp,'false_replace':fn,
            'keep_precision':tp/(tp+fp) if tp+fp else None,'keep_recall':tp/(tp+fn) if tp+fn else None,
            'replace_precision':tn/(tn+fn) if tn+fn else None,'replace_recall':tn/(tn+fp) if tn+fp else None,
            'uncertain':len(ids)-len(actionable),'wilson_95':wilson(correct,len(actionable))}

def majority(votes):
    """Return the majority."""
    c=Counter(votes); value,count=c.most_common(1)[0]
    return value if count>len(votes)/2 else 'unsure'

def main(argv=None):
    """Run the command-line entry point."""
    global RUN,OUT
    parser=argparse.ArgumentParser();parser.add_argument('--run-dir',type=Path,default=RUN);parser.add_argument('--output-dir',type=Path,default=OUT);args=parser.parse_args(argv)
    RUN=args.run_dir;OUT=args.output_dir
    human_doc=json.load(open(RUN/'human_validation.json'));human={k:v['human_action'] for k,v in human_doc['items'].items()}
    if len(human)!=250:raise SystemExit(f'expected 250 human reviews, found {len(human)}')
    cases={x['case_id']:x for x in json.load(open(RUN/'cases.json'))['items']};results={p:json.load(open(RUN/f'{p}_results.json'))['items'] for p in PROVIDERS}
    meta={x['case_id']:x for x in json.load(open(RUN/'meta_results.json'))['items']};dis={x['case_id']:x for x in json.load(open(RUN/'disagreement_results.json'))['items']}
    predictions={p:{k:x['keep_or_replace'] for k,x in results[p].items()} for p in PROVIDERS}
    predictions['majority_vote']={k:majority([results[p][k]['keep_or_replace'] for p in PROVIDERS if k in results[p]]) for k in cases}
    predictions['meta_critic']={k:x['recommended_action'] for k,x in meta.items()}
    summaries={name:metrics(pred,human) for name,pred in predictions.items()}
    policies={p:{} for p in 'ABCDEFGH'}
    for k,m in meta.items():
        base=policy_actions(m);extra=safeguarded_policies(m,dis[k])
        for p in 'ABCDE':policies[p][k]=base[p]
        for p in 'FGH':policies[p][k]=extra[p]
    policy_metrics={p:metrics(x,human) for p,x in policies.items()}
    bands={}
    for band in ('low','moderate','high','extreme'):
        ids=[k for k,x in dis.items() if x['disagreement_band']==band]
        bands[band]=metrics({k:predictions['meta_critic'][k] for k in ids},{k:human[k] for k in ids})
    complete=set(meta); incomplete=set(cases)-complete
    gemini_only=[]
    for k in complete:
        votes={p:results[p][k]['keep_or_replace'] for p in PROVIDERS}
        if votes['gemini']=='keep' and list(votes.values()).count('replace')==3:gemini_only.append(k)
    gemini_only_metrics=metrics({k:'keep' for k in gemini_only},human)
    meta_errors=[{'case_id':k,'meta':predictions['meta_critic'][k],'human':human[k],'confidence':meta[k]['confidence'],'disagreement_band':dis[k]['disagreement_band'],'votes':{p:results[p][k]['keep_or_replace'] for p in PROVIDERS}} for k in sorted(complete) if predictions['meta_critic'][k] in {'keep','replace'} and predictions['meta_critic'][k]!=human[k]]
    result={'schema_version':1,'human_reviews':250,'human_distribution':dict(Counter(human.values())),'four_provider_complete':len(complete),'incomplete_cases':len(incomplete),'summaries':summaries,'policies':policy_metrics,'disagreement_bands':bands,'gemini_only_keep':{'case_ids':sorted(gemini_only),**gemini_only_metrics},'meta_errors':meta_errors,'meta_acceptable':dict(Counter(x['meta_acceptable'] for x in human_doc['items'].values()))}
    OUT.mkdir(parents=True,exist_ok=True);atomic_write_json(OUT/'validation_summary.json',result)
    with open(OUT/'system_accuracy.csv','w',newline='') as f:
        w=csv.writer(f);w.writerow(['system','available','coverage','accuracy','correct','false_keep','false_replace','uncertain']);[w.writerow([n,x['available'],x['coverage'],x['accuracy'],x['correct'],x['false_keep'],x['false_replace'],x['uncertain']]) for n,x in summaries.items()]
    with open(OUT/'policy_accuracy.csv','w',newline='') as f:
        w=csv.writer(f);w.writerow(['policy','available','coverage','accuracy','correct','false_keep','false_replace','uncertain']);[w.writerow([n,x['available'],x['coverage'],x['accuracy'],x['correct'],x['false_keep'],x['false_replace'],x['uncertain']]) for n,x in policy_metrics.items()]
    lines=['# 250-case human validation','','## Executive summary','',f'Tony reviewed all 250 cases: {dict(Counter(human.values()))}. Four-provider and meta-critic validation covers {len(complete)} cases; the {len(incomplete)} cases missing Gemini remain separate.','',f'The meta-critic resolved {summaries["meta_critic"]["actionable"]}/{len(complete)} complete cases and achieved {summaries["meta_critic"]["accuracy"]:.1%} actionable accuracy ({summaries["meta_critic"]["correct"]} correct, {summaries["meta_critic"]["false_keep"]} false keeps, {summaries["meta_critic"]["false_replace"]} false replaces). Its 95% Wilson interval is {summaries["meta_critic"]["wilson_95"][0]:.1%}–{summaries["meta_critic"]["wilson_95"][1]:.1%}.','',f'Gemini was the lone keeper in {len(gemini_only)} cases; Tony kept {gemini_only_metrics["correct"]} and replaced {gemini_only_metrics["false_keep"]}. This directly measures whether Gemini rescued useful images.','','## Systems','','| System | Available | Coverage | Accuracy | Correct | False keep | False replace | Unsure/defer |','|---|---:|---:|---:|---:|---:|---:|---:|']
    for n,x in summaries.items():lines.append(f'| {n} | {x["available"]} | {x["coverage"]:.1%} | {x["accuracy"]:.1%} | {x["correct"]} | {x["false_keep"]} | {x["false_replace"]} | {x["uncertain"]} |')
    lines += ['','## Policies A-H','','| Policy | Coverage | Accuracy | Correct | False keep | False replace | Deferred |','|---|---:|---:|---:|---:|---:|---:|']
    for n,x in sorted(policy_metrics.items()):lines.append(f'| {n} | {x["coverage"]:.1%} | {x["accuracy"]:.1%} | {x["correct"]} | {x["false_keep"]} | {x["false_replace"]} | {x["uncertain"]} |')
    lines += ['','## Disagreement bands','','| Band | Cases | Coverage | Accuracy | False keep | False replace |','|---|---:|---:|---:|---:|---:|']
    for n,x in bands.items():
        accuracy='unavailable' if x['accuracy'] is None else f'{x["accuracy"]:.1%}'
        coverage='unavailable' if x['coverage'] is None else f'{x["coverage"]:.1%}'
        lines.append(f'| {n} | {x["available"]} | {coverage} | {accuracy} | {x["false_keep"]} | {x["false_replace"]} |')
    lines += ['','## Interpretation','',f'- Gemini-only keeps: {gemini_only_metrics["correct"]}/{len(gemini_only)} agreed with Tony; {gemini_only_metrics["false_keep"]} were false keeps.',f'- Meta acceptability entered directly by Tony: {result["meta_acceptable"]}.','- Provider metrics use each provider’s available denominator; Gemini uses 227, the others 250.','- Majority vote uses all available providers and returns unsure on ties.','- No weights or rules were changed.','','## Outputs','','- `validation_summary.json`','- `system_accuracy.csv`','- `policy_accuracy.csv`','- `validation_report.md`','','All calculations are offline and deterministic. No provider calls were made and no raw results were modified.']
    atomic_write_text(OUT/'validation_report.md','\n'.join(lines)+'\n')
    print(json.dumps({'summaries':summaries,'gemini_only_keep':result['gemini_only_keep'],'meta_errors':len(meta_errors)},indent=2))

if __name__=='__main__':main()
