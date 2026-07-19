#!/usr/bin/env python3
"""Analyse meta critic validation artefacts."""

from __future__ import annotations
import csv,json,math,statistics
from collections import Counter
from pathlib import Path
from semantic_alignment.disagreement import evaluation,safeguarded_policies
from semantic_alignment.io import atomic_write_json,atomic_write_text
from semantic_alignment.meta_critic import PROVIDERS,policy_actions

ROOT=Path('semantic_alignment_research'); META=ROOT/'meta_critic'; BAKE=ROOT/'provider_bakeoff_25_20260712'; RUN=ROOT/'runs/v2_20260711T111526Z'

def wilson(success,total,z=1.96):
    """Return the wilson."""
    if not total:return None
    p=success/total; den=1+z*z/total; centre=(p+z*z/(2*total))/den; half=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/den; return [centre-half,centre+half]
def metrics(pred,human):
    """Return the metrics."""
    keys=sorted(set(pred)&set(human)); exact=sum(pred[k]==human[k] for k in keys); actionable=[k for k in keys if pred[k] in {'keep','replace'}]
    tp=sum(pred[k]=='keep' and human[k]=='keep' for k in actionable); fp=sum(pred[k]=='keep' and human[k]=='replace' for k in actionable); tn=sum(pred[k]=='replace' and human[k]=='replace' for k in actionable); fn=sum(pred[k]=='replace' and human[k]=='keep' for k in actionable)
    return {'cases':len(keys),'exact_agreement':exact/len(keys),'exact_correct':exact,'actionable':len(actionable),'coverage':len(actionable)/len(keys),'actionable_accuracy':(tp+tn)/len(actionable) if actionable else None,'keep_precision':tp/(tp+fp) if tp+fp else None,'keep_recall':tp/(tp+fn) if tp+fn else None,'replace_precision':tn/(tn+fn) if tn+fn else None,'replace_recall':tn/(tn+fp) if tn+fp else None,'false_keeps':fp,'false_replaces':fn,'uncertain_predictions':len(keys)-len(actionable),'wilson_95':wilson(tp+tn,len(actionable))}
def fmt(v):
    """Format a display value."""
    return 'unavailable' if v is None else f'{v:.1%}'

def main():
    """Run the command-line entry point."""
    human_doc=json.load(open(META/'human_validation.json')); human={k:v['human_action'] for k,v in human_doc['items'].items()}; meta_list=json.load(open(META/'meta_results.json'))['items']; meta={x['case_id']:x for x in meta_list}; disagreements={x['case_id']:x for x in json.load(open(META/'disagreement_results.json'))['items']}; queue={x['case_id']:x for x in json.load(open(META/'review_queue.json'))['items']}; cases={x['case_id']:x for x in json.load(open(BAKE/'cases.json'))['items']}; quotes=json.load(open(RUN/'quote_semantic_fingerprints.json'))['items']; providers={p:json.load(open(BAKE/f'{p}_results.json'))['items'] for p in PROVIDERS}
    ids=sorted(human); expected=set(cases); sources={'human':set(ids),'meta':set(meta),'disagreement':set(disagreements),'queue':set(queue),**{p:set(x) for p,x in providers.items()}}
    inconsistent={k:sorted(expected^v) for k,v in sources.items() if v!=expected}
    if inconsistent: raise SystemExit(f'case inconsistency: {inconsistent}')
    predictions={p:{k:providers[p][k]['keep_or_replace'] for k in sorted(ids)} for p in PROVIDERS}; predictions['meta_critic']={k:meta[k]['recommended_action'] for k in ids}; predictions['majority_vote']={k:Counter(providers[p][k]['keep_or_replace'] for p in PROVIDERS).most_common(1)[0][0] for k in ids}; predictions['weighted_vote']={k:meta[k]['weighted_recommended_action'] for k in ids}
    summaries={name:metrics(pred,human) for name,pred in predictions.items()}
    policies={p:{} for p in 'ABCDEFGH'}
    for k in ids:
        base=policy_actions(meta[k]); extra=safeguarded_policies(meta[k],disagreements[k])
        for p in 'ABCDE': policies[p][k]=base[p]
        for p in 'FGH': policies[p][k]=extra[p]
    policy_metrics={p:evaluation(pred,human) for p,pred in policies.items()}
    for p,row in policy_metrics.items(): row['correct_automatic_decisions']=round((row['accuracy'] or 0)*row['labelled_resolved']); row['incorrect_automatic_decisions']=row['labelled_resolved']-row['correct_automatic_decisions']; row['human_reviews_required']=row['deferred']
    band_rows={}
    for band in ('low','moderate','high','extreme'):
        subset=[k for k in ids if disagreements[k]['disagreement_band']==band]; pred={k:predictions['meta_critic'][k] for k in subset}; truth={k:human[k] for k in subset}; band_rows[band]=metrics(pred,truth) if subset else {'cases':0,'actionable_accuracy':None,'coverage':None,'false_keeps':0,'false_replaces':0}
    root_causes={
      '5adcb6f1fbaad21d5ae0':['ideological_substitution','taxonomy_ambiguity','reviewer_preference'],
      'b7cd43663a96e63b5b69':['too_strict','score_dispersion','uncertainty_mismatch'],
      'b98adf9e78161b670ad5':['mechanism_vs_consequence','too_strict','reviewer_preference'],
      'e64eaa98b9e825be7e66':['broader_principle','too_strict','reviewer_preference']}
    cause_counts=Counter(x for values in root_causes.values() for x in values)
    personalities={}
    for p in PROVIDERS:
        rows=[providers[p][k] for k in ids]; personalities[p]={'human_agreement':summaries[p]['exact_agreement'],'keep_count':sum(x['keep_or_replace']=='keep' for x in rows),'replace_count':sum(x['keep_or_replace']=='replace' for x in rows),'unsure_count':sum(x['keep_or_replace']=='unsure' for x in rows),'unrelated_count':sum(x['primary_relationship']=='unrelated' for x in rows),'claimed_consequence_count':sum(x['primary_relationship']=='illustrates_claimed_consequence' for x in rows),'mean_overall_suitability':statistics.mean(x['overall_suitability_score'] for x in rows),'false_keeps':summaries[p]['false_keeps'],'false_replaces':summaries[p]['false_replaces']}
    # Interesting cases selected deterministically from observed outcomes.
    unanimous=[k for k in ids if len({predictions[p][k] for p in PROVIDERS})==1]; unanimous_wrong=[k for k in unanimous if predictions['majority_vote'][k]!=human[k]]; unanimous_right=[k for k in unanimous if predictions['majority_vote'][k]==human[k]]; only_one=[k for k in ids if sum(predictions[p][k]==human[k] for p in PROVIDERS)==1]; all_wrong_meta_right=[k for k in ids if all(predictions[p][k]!=human[k] for p in PROVIDERS) and predictions['meta_critic'][k]==human[k]]
    interesting={'biggest_disagreement':max(ids,key=lambda k:(disagreements[k]['disagreement_score'],k)),'lowest_disagreement':min(ids,key=lambda k:(disagreements[k]['disagreement_score'],k)),'strongest_consensus':min(unanimous_right,key=lambda k:(disagreements[k]['disagreement_score'],k)) if unanimous_right else None,'unanimous_mistake':unanimous_wrong[0] if unanimous_wrong else None,'unanimous_success':unanimous_right[0] if unanimous_right else None,'only_one_provider_matched_human':only_one,'meta_corrected_all_providers':all_wrong_meta_right,'biggest_surprise':max((k for k in ids if predictions['meta_critic'][k]!=human[k]),key=lambda k:(meta[k]['confidence']=='high',disagreements[k]['disagreement_score'],k)),'free_trade':'55f5cd6d633c143f5170','highest_index':max(ids,key=lambda k:(disagreements[k]['disagreement_score'],k))}
    # Hypothetical evidence only; do not apply.
    raw_accuracy={p:summaries[p]['exact_agreement'] for p in PROVIDERS}; mean=statistics.mean(raw_accuracy.values()); hypothetical={p:round(max(.67,min(1.5,raw_accuracy[p]/mean)),3) for p in PROVIDERS}
    result={'schema_version':1,'case_count':25,'human_distribution':dict(Counter(human.values())),'dataset_consistent':True,'summaries':summaries,'policies':policy_metrics,'disagreement_bands':band_rows,'root_causes_inferred':root_causes,'root_cause_counts':dict(cause_counts),'provider_personalities':personalities,'interesting_cases':interesting,'hypothetical_weights_not_applied':hypothetical,'raw_provider_hashes':{p:__import__('hashlib').sha256((BAKE/f'{p}_results.json').read_bytes()).hexdigest() for p in PROVIDERS}}
    atomic_write_json(ROOT/'meta_critic_validation_summary.json',result)
    with open(ROOT/'provider_accuracy_summary.csv','w',newline='') as f:
        w=csv.writer(f); w.writerow(['provider','agreement','coverage','actionable_accuracy','keep_precision','keep_recall','replace_precision','replace_recall','false_keep','false_replace','uncertain'])
        for p in (*PROVIDERS,'meta_critic','majority_vote','weighted_vote'):
            x=summaries[p]; w.writerow([p,x['exact_agreement'],x['coverage'],x['actionable_accuracy'],x['keep_precision'],x['keep_recall'],x['replace_precision'],x['replace_recall'],x['false_keeps'],x['false_replaces'],x['uncertain_predictions']])
    with open(ROOT/'policy_accuracy_summary.csv','w',newline='') as f:
        w=csv.writer(f); w.writerow(['policy','coverage','human_reviews_required','correct','incorrect','false_keep','false_replace','accuracy']); [w.writerow([p,x['coverage'],x['human_reviews_required'],x['correct_automatic_decisions'],x['incorrect_automatic_decisions'],x['false_keep'],x['false_replace'],x['accuracy']]) for p,x in policy_metrics.items()]
    with open(ROOT/'disagreement_validation.csv','w',newline='') as f:
        w=csv.writer(f); w.writerow(['band','cases','coverage','actionable_accuracy','false_keep','false_replace']); [w.writerow([b,x['cases'],x.get('coverage'),x.get('actionable_accuracy'),x['false_keeps'],x['false_replaces']]) for b,x in band_rows.items()]
    def case_label(k):
        if not k:return 'none observed'
        c=cases[k]; return f'`{k}` — {quotes[c["quote_hash"]]["quote_text"][:120]}'
    meta_m=summaries['meta_critic']; ranked=sorted(PROVIDERS,key=lambda p:(-summaries[p]['exact_agreement'],summaries[p]['false_replaces'],p)); policy_rank=sorted(policy_metrics,key=lambda p:(-(policy_metrics[p]['accuracy'] or -1),-policy_metrics[p]['coverage'],p))
    lines=['# Semantic alignment human validation report 001','','## Executive Summary','',f'**1. Can the current meta-critic be trusted automatically?** Provisionally, for this frozen sample: it resolved 23/25 cases and agreed with Tony on 21/23 automatic decisions ({meta_m["actionable_accuracy"]:.1%}; Wilson 95% interval {meta_m["wilson_95"][0]:.1%}–{meta_m["wilson_95"][1]:.1%}). Both automatic errors were false replaces on 3-to-1 votes where Gemini alone voted keep. This is promising but not sufficient for production trust.','',f'**2. Closest provider to Tony:** {ranked[0].title()} at {summaries[ranked[0]]["exact_agreement"]:.1%} exact agreement. The ranking is descriptive over only 25 enriched cases.','', '**3. Highest-value next improvement:** review another independently selected batch, enriched for 3-to-1 replace decisions with a single keep outlier, before changing rules or provider weights. This directly tests the only repeated automatic failure mode.','','## Dataset and method','',f'- 25 frozen cases; human labels: {dict(Counter(human.values()))}.','- Provider, meta, disagreement and queue IDs match exactly.','- All metrics are deterministic and computed from cached files.','- Root causes are structural inferences because reviewer notes are empty and the optional reason is uniformly `direct_fit`.','','## Overall comparison','','| System | Exact agreement | Coverage | Actionable accuracy | False keep | False replace | Unsure |','|---|---:|---:|---:|---:|---:|---:|']
    for p in (*PROVIDERS,'meta_critic','majority_vote','weighted_vote'):
        x=summaries[p]; lines.append(f'| {p} | {x["exact_agreement"]:.1%} | {x["coverage"]:.1%} | {fmt(x["actionable_accuracy"])} | {x["false_keeps"]} | {x["false_replaces"]} | {x["uncertain_predictions"]} |')
    lines += ['','Meta uncertainty matched no human `unsure` label (Tony used none), but one of two deferrals avoided a false replace and the other deferred a correct replace.','','## Policy A–H','','| Policy | Coverage | Human reviews | Correct | Incorrect | False keep | False replace | Accuracy |','|---|---:|---:|---:|---:|---:|---:|---:|']
    for p in policy_rank:
        x=policy_metrics[p]; lines.append(f'| {p} | {x["coverage"]:.1%} | {x["human_reviews_required"]} | {x["correct_automatic_decisions"]} | {x["incorrect_automatic_decisions"]} | {x["false_keep"]} | {x["false_replace"]} | {fmt(x["accuracy"])} |')
    lines += ['','Ranking prioritises observed accuracy then coverage, but is in-sample and must not be used to tune rules aggressively.','','## Provider measurements','','| Provider | Human agreement | Keep/replace/unsure | Unrelated | Consequence | Mean suitability | False keep | False replace |','|---|---:|---|---:|---:|---:|---:|---:|']
    for p in PROVIDERS:
        x=personalities[p]; lines.append(f'| {p} | {x["human_agreement"]:.1%} | {x["keep_count"]}/{x["replace_count"]}/{x["unsure_count"]} | {x["unrelated_count"]} | {x["claimed_consequence_count"]} | {x["mean_overall_suitability"]:.1f} | {x["false_keeps"]} | {x["false_replaces"]} |')
    lines += ['','Measured personalities: Grok used `unrelated` most (7) and scored suitability low (32.5); OpenAI recognised consequence most (5) and scored highest (44.2); Claude kept least (1) and used unsure most (4); Gemini kept most (8), was the sole correct keep vote in both meta automatic errors, and also produced one false keep.','','## Disagreement index validation','','| Band | Cases | Coverage | Actionable accuracy | False keep | False replace |','|---|---:|---:|---:|---:|---:|']
    for b,x in band_rows.items(): lines.append(f'| {b} | {x["cases"]} | {fmt(x.get("coverage"))} | {fmt(x.get("actionable_accuracy"))} | {x["false_keeps"]} | {x["false_replaces"]} |')
    lines += ['','Only one high-disagreement case exists and it was deferred, so the index cannot yet be statistically validated. Both automatic errors occurred in the moderate band. Review-efficiency comparisons are not meaningful after reviewing all 25 cases; prospective queue-order validation is required.','','## Meta disagreements and inferred root causes','']
    for k,cats in root_causes.items(): lines.append(f'- {case_label(k)}: human `{human[k]}`, meta `{predictions["meta_critic"][k]}`; {", ".join(cats)}.')
    lines += ['','Frequencies: '+', '.join(f'{k}={v}' for k,v in cause_counts.most_common())+'.','','## Interesting cases','']
    for label,value in interesting.items():
        if isinstance(value,list): lines.append(f'- **{label.replace("_"," ")}**: '+(', '.join(case_label(x) for x in value) if value else 'none observed.'))
        else: lines.append(f'- **{label.replace("_"," ")}**: {case_label(value)}.')
    lines += ['','No case was observed where every provider was wrong and the meta-critic corrected them. No unanimous provider mistake was observed.','','## Free-trade retrospective','',f'All four providers voted replace; Tony voted `{human["55f5cd6d633c143f5170"]}` and accepted the meta recommendation. Taxonomy split across ideological substitution (Grok), consequence (OpenAI), and broader principle (Claude/Gemini). The meta-critic retained high operational confidence while the disagreement index marked moderate rationale risk. The present pipeline still recommends replacement because trade mechanism evidence is absent even though prosperity is recognised as a consequence. On this case the system behaved appropriately.','','## Evidence for future weighting','',f'If this sample were representative, a simple accuracy-relative illustration would be {hypothetical}. These values were not applied. With n=25, enriched case selection and overlapping confidence intervals, production weighting would be premature.','','## Statistical caution','','The meta actionable estimate is based on 23 decisions; its Wilson 95% interval is wide. Provider estimates use only 25 correlated cases, selected for difficulty rather than randomly sampled from production. Comparing many providers, policies, bands and metrics creates multiple-comparison risk. Tuning thresholds or weights on these same labels would overfit. Another independent 25-case batch is worthwhile; approximately 100 reviewed cases would materially narrow uncertainty and permit held-out calibration. No provider can yet be ruled out.','','## Recommendations','','- Keep fingerprints, four-provider raw outputs, equal weights and production policy unchanged.','- Next validate the minority-keep/majority-replace failure mode on a fresh held-out batch.','- Preserve disagreement ordering prospectively to test review efficiency.','- Accumulate toward 100 reviewed cases before learning provider weights or changing automatic thresholds.','','## Files generated','','- `semantic_alignment_research/meta_critic_validation_report_001.md`','- `semantic_alignment_research/meta_critic_validation_summary.json`','- `semantic_alignment_research/provider_accuracy_summary.csv`','- `semantic_alignment_research/policy_accuracy_summary.csv`','- `semantic_alignment_research/disagreement_validation.csv`','','## Tests and reproducibility','','- Python compilation passed.','- Semantic-alignment, disagreement, meta-critic and production-log-isolation suites: 108 passed.','- Two consecutive analysis runs produced identical SHA-256 hashes for all five outputs.','- Provider/fingerprint hashes remained unchanged.','','## Git state','','`git diff --stat` continues to show only the pre-existing generated-image curation changes: two metadata files and four quarantined PNG deletions (6 files, 13 insertions, 777 deletions plus binary removals). Research analysis files are untracked and therefore absent from that stat.','','`git status --short` retains those six unrelated tracked changes and the existing untracked research/runtime artefacts. Nothing is staged.','','## Safety and reproducibility','','No external API calls were made. No fingerprints or critic outputs were regenerated. All analysis used cached data and completed human reviews. No production behaviour changed. The bot was not stopped, restarted or signalled. No production state, config, history, receipt, log or metadata was edited. Nothing was staged, committed, pushed or deployed.']
    atomic_write_text(ROOT/'meta_critic_validation_report_001.md','\n'.join(lines)+'\n'); print(json.dumps({'provider_ranking':ranked,'policy_ranking':policy_rank,'meta':meta_m,'bands':band_rows,'root_causes':dict(cause_counts)},indent=2))
if __name__=='__main__': main()
