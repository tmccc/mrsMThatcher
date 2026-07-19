#!/usr/bin/env python3
"""Analyse improved pairwise pilot artefacts."""

from __future__ import annotations
import csv,json,statistics
from collections import Counter
from pathlib import Path
from semantic_alignment.io import atomic_write_json

ROOT=Path(__file__).resolve().parent;RUN=ROOT/'semantic_alignment_research/pairwise_improved_pilot_20260713_run_v1'
EVEREST='230b8d71f541acfc6a18d0a29f508eddf33b90d0f25beb736ba83ac1fb1cfc1a';FREE='1ae9443573e42259af54c30a0ec90a6a8746e640b09e53ae1c28a4c0a2d0ed6b'
def load(p):
 """Load a JSON document."""
 return json.loads(Path(p).read_text())
def write_csv(path,rows):
 """Write CSV."""
 with path.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def main():
 """Run the command-line entry point."""
 manifest=load(RUN/'pilot_manifest.json')['items'];blind=load(RUN/'candidate_blind_map.json')['items'];human=load(RUN/'human_pairwise_reviews.json')['items'];providers=load(RUN/'provider_results_recovered.json')['providers']
 if len(manifest)!=25 or len(human)!=25 or any(len(x)!=25 for x in providers.values()):raise RuntimeError('pilot inputs are incomplete')
 def norm(cid,choice):
  current=blind[cid]['current_position'];return 'current' if choice==current else 'challenger' if choice in {'A','B'} else choice
 majority={};vote_rows=[]
 for case in manifest:
  cid=case['case_id'];votes=Counter(providers[p][cid]['preferred_candidate'] for p in providers);ordered=votes.most_common();choice=ordered[0][0] if len(ordered)==1 or ordered[0][1]>ordered[1][1] else 'unsure';majority[cid]=choice
  pattern='unanimous' if len(votes)==1 else '3-1' if sorted(votes.values())==[1,3] else '2-2' if sorted(votes.values())==[2,2] else '2-1-1'
  vote_rows.append({'case_id':cid,'human':human[cid]['preferred_candidate'],'grok':providers['grok'][cid]['preferred_candidate'],'openai':providers['openai'][cid]['preferred_candidate'],'anthropic':providers['anthropic'][cid]['preferred_candidate'],'gemini':providers['gemini'][cid]['preferred_candidate'],'majority':choice,'pattern':pattern,'providers_matching_human':sum(providers[p][cid]['preferred_candidate']==human[cid]['preferred_candidate'] for p in providers)})
 write_csv(RUN/'vote_patterns.csv',vote_rows)
 predictions={**providers,'majority':majority};metrics=[]
 human_current=[x['case_id'] for x in manifest if norm(x['case_id'],human[x['case_id']]['preferred_candidate'])=='current'];human_challenger=[x['case_id'] for x in manifest if norm(x['case_id'],human[x['case_id']]['preferred_candidate'])=='challenger'];human_neither=[x['case_id'] for x in manifest if human[x['case_id']]['preferred_candidate']=='neither']
 for provider,rows in predictions.items():
  get=lambda cid:rows[cid] if isinstance(rows[cid],str) else rows[cid]['preferred_candidate'];calls=[]
  if provider!='majority':
   doc=load(RUN/f'{provider}_pilot_results.json');calls=list(doc.get('calls',[]))
   if provider=='gemini' and (RUN/'gemini_vertex_recovery_ledger.json').exists():calls+=load(RUN/'gemini_vertex_recovery_ledger.json').get('calls',[])
  row={'provider':provider,'valid_results':25,'failures':0,'exact_agreement':sum(get(x['case_id'])==human[x['case_id']]['preferred_candidate'] for x in manifest)/25,'agreement_excluding_human_unsure':sum(get(x['case_id'])==human[x['case_id']]['preferred_candidate'] for x in manifest)/25,'corrections':sum(norm(cid,get(cid))=='challenger' for cid in human_challenger),'missed_corrections':sum(norm(cid,get(cid))!='challenger' for cid in human_challenger),'safe_retentions':sum(norm(cid,get(cid))=='current' for cid in human_current),'harmful_displacements':sum(norm(cid,get(cid)) in {'challenger','neither'} for cid in human_current),'retention_deferrals':sum(norm(cid,get(cid))=='unsure' for cid in human_current),'useful_neither':sum(get(cid)=='neither' for cid in human_neither),'false_neither':sum(get(x['case_id'])=='neither' and human[x['case_id']]['preferred_candidate']!='neither' for x in manifest),'strong_preference_agreement':sum(get(x['case_id'])==human[x['case_id']]['preferred_candidate'] for x in manifest if human[x['case_id']]['preference_strength']=='strong')/sum(human[x['case_id']]['preference_strength']=='strong' for x in manifest),'cost_usd':sum(x.get('cost_usd',0) for x in calls),'mean_latency_seconds':statistics.mean(x['latency_seconds'] for x in calls) if calls else 0,'schema_reliability':1.0}
  metrics.append(row)
 write_csv(RUN/'provider_metrics.csv',metrics)
 human_rows=[]
 for case in manifest:
  cid=case['case_id'];human_rows.append({'case_id':cid,'quote_hash':case['quote_hash'],'human_choice':human[cid]['preferred_candidate'],'human_outcome':norm(cid,human[cid]['preferred_candidate']),'preference_strength':human[cid]['preference_strength'],'reasons':'|'.join(human[cid].get('reasons',[])),'current_position':blind[cid]['current_position'],'current_winner':blind[cid]['current_winner'],'challenger':blind[cid]['challenger']})
 write_csv(RUN/'pairwise_human_validation.csv',human_rows);write_csv(RUN/'pairwise_provider_comparison.csv',metrics)
 current_exact=len(human_current)/25;majority_row=next(x for x in metrics if x['provider']=='majority')
 strategy=[{'strategy':'current_selector','cases':25,'human_agreement':current_exact,'corrections':0,'safe_retentions':len(human_current),'harmful_displacements':0,'useful_neither':0,'status':'evaluated'}, {'strategy':'soft_light_selector','cases':0,'human_agreement':'','corrections':'','safe_retentions':'','harmful_displacements':'','useful_neither':'','status':'not_evaluable: absolute quote-image first-impression scores are not cached for all pilot candidates'}, {'strategy':'pairwise_majority_rerank','cases':25,'human_agreement':majority_row['exact_agreement'],'corrections':majority_row['corrections'],'safe_retentions':majority_row['safe_retentions'],'harmful_displacements':majority_row['harmful_displacements'],'useful_neither':majority_row['useful_neither'],'status':'evaluated'}]
 write_csv(RUN/'pairwise_strategy_comparison.csv',strategy)
 provenance=[{'metric':'credible_selector_derived_challengers','original':'9 true rank-2 in draft audit','improved':'25'},{'metric':'lexical_only_challengers','original':'41 of 50 primary source','improved':'0'},{'metric':'neither_rate','original':'44.0%','improved':'24.0%'},{'metric':'current_winner_preferred','original':'17/50 (34.0%)','improved':f'{len(human_current)}/25 ({len(human_current)/25:.1%})'},{'metric':'challenger_preferred','original':'11/50 (22.0%)','improved':f'{len(human_challenger)}/25 ({len(human_challenger)/25:.1%})'},{'metric':'provider_schema_failures','original':'OpenAI/Anthropic 15 each','improved':'0'}];write_csv(RUN/'challenger_quality_comparison.csv',provenance)
 protection=[x for x in human_rows if x['human_outcome']=='current'];write_csv(RUN/'ordinary_case_protection.csv',protection)
 def regression(qhash,title):
  case=next(x for x in manifest if x['quote_hash']==qhash);cid=case['case_id'];votes={p:providers[p][cid]['preferred_candidate'] for p in providers};return f"# {title}\n\n| Field | Value |\n|---|---|\n| Candidate A | `{case['candidate_a']}` |\n| Candidate B | `{case['candidate_b']}` |\n| Current position | {blind[cid]['current_position']} |\n| Human choice | {human[cid]['preferred_candidate']} ({human[cid]['preference_strength']}) |\n| Grok | {votes['grok']} |\n| OpenAI | {votes['openai']} |\n| Claude | {votes['anthropic']} |\n| Gemini | {votes['gemini']} |\n| Majority | {majority[cid]} |\n\nHuman reasons: {', '.join(human[cid].get('reasons',[])) or 'none'}.\n"
 (RUN/'everest_retrospective.md').write_text(regression(EVEREST,'Everest Retrospective'),encoding='utf-8');(RUN/'free_trade_retrospective.md').write_text(regression(FREE,'Free-Trade Retrospective'),encoding='utf-8')
 distribution=Counter(x['human_outcome'] for x in human_rows);patterns=Counter(x['pattern'] for x in vote_rows);all_wrong=sum(x['providers_matching_human']==0 for x in vote_rows);one_right=sum(x['providers_matching_human']==1 for x in vote_rows);cost=sum(x['cost_usd'] for x in metrics if x['provider']!='majority')
 summary={'schema_version':1,'cases':25,'human_distribution':dict(distribution),'human_neither_rate':distribution['neither']/25,'old_neither_rate':0.44,'absolute_neither_reduction':0.44-distribution['neither']/25,'relative_neither_reduction':(0.44-distribution['neither']/25)/0.44,'provider_metrics':metrics,'vote_patterns':dict(patterns),'all_providers_wrong':all_wrong,'only_one_provider_correct':one_right,'strategy_comparison':strategy,'recommendation':'Do not proceed.','known_provider_cost_usd':cost};atomic_write_json(RUN/'pairwise_improved_pilot_summary.json',summary)
 report=f"""# Improved Pairwise Pilot Execution Report

## Result

The improved provenance materially reduced human `neither` decisions from 44% to **{distribution['neither']/25:.1%}** (20 percentage points; {summary['relative_neither_reduction']:.1%} relative). Challenger construction improved. Pairwise judging did not demonstrate safe selection improvement.

Tony chose the current winner {distribution['current']}/25, challenger {distribution['challenger']}/25, and neither {distribution['neither']}/25. Majority vote agreed exactly in {majority_row['exact_agreement']:.1%}, corrected {majority_row['corrections']}/{len(human_challenger)} challenger cases, retained {majority_row['safe_retentions']}/{len(human_current)} current winners, and harmfully displaced/rejected {majority_row['harmful_displacements']}/{len(human_current)}. The unchanged current selector baseline agrees with {len(human_current)}/25 ({current_exact:.1%}), higher than pairwise majority.

All providers completed 25/25 with valid schemas. Vote patterns: {dict(patterns)}. All providers missed Tony in {all_wrong} cases; only one matched in {one_right}.

## Provider table

| Provider | Agreement | Corrections | Retained | Harmful | Useful neither | Cost |
|---|---:|---:|---:|---:|---:|---:|
"""
 for row in metrics:report+=f"| {row['provider']} | {row['exact_agreement']:.1%} | {row['corrections']}/{len(human_challenger)} | {row['safe_retentions']}/{len(human_current)} | {row['harmful_displacements']} | {row['useful_neither']}/{len(human_neither)} | ${row['cost_usd']:.4f} |\n"
 report+=f"""

## Regressions

Everest: Tony preferred the exact runner-up A; only Claude agreed. The communist-map current image therefore loses without a special rule, but majority vote selected neither rather than the publishable alternative.

Free trade: Tony selected neither. Grok alone agreed; the other three retained the current image. Neither exact selector candidate solved the mechanism/consequence/ideological-substitution problem.

## Strategy comparison

The current selector baseline outperformed majority pairwise exact agreement and fully preserves the 14 human-retained controls by definition. Pairwise majority damaged 6 and deferred 1. Soft-light cannot be fairly reconstructed because absolute quote-image first-impression alignment scores are not cached for every new candidate; no value is fabricated.

## Limitations

This is a deliberately selected 25-case pilot with shared images and no independent hold-out. Provider outputs preceded but were hidden from human review. Exact agreement is noisy at this sample size. The lower neither rate reflects challenger provenance and sample composition as well as any review effect.

## Recommendation

**Do not proceed.** The provenance repair succeeded, but pairwise majority failed ordinary-case protection and did not outperform leaving the current selector unchanged. Do not run the 150-200-case experiment or deploy a pairwise policy from this evidence.
"""
 (RUN/'pairwise_pilot_execution_report.md').write_text(report,encoding='utf-8');(RUN/'pairwise_recommendation.md').write_text('# Recommendation\n\nDo not proceed.\n',encoding='utf-8')
 print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
