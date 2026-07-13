#!/usr/bin/env python3
from __future__ import annotations

import csv,json,statistics
from collections import Counter
from pathlib import Path

from semantic_alignment.io import atomic_write_json
from semantic_alignment.pairwise_validation import calibration_metrics

ROOT=Path(__file__).resolve().parent;RUN=ROOT/'semantic_alignment_research/pairwise_validation_001';SOURCE=ROOT/'semantic_alignment_research/first_impression/v1_20260712'
def load(p):return json.loads(Path(p).read_text())
def write_csv(path,rows,fields):
    with path.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)

def main():
    manifest=load(RUN/'pairwise_manifest.json')['items'];blind=load(RUN/'candidate_blind_map.json')['items'];reviews=load(RUN/'human_pairwise_reviews.json')['items'];calids=load(RUN/'calibration_cases.json')['case_ids'];intents=load(SOURCE/'quote_visual_intents.json')['items']
    providers={}
    for p in ('grok','openai','anthropic','gemini'):
        path=RUN/f'{p}_pairwise_calibration_results.json';providers[p]=load(path) if path.exists() else {'items':{},'failures':{},'calls':[]}
    metrics=calibration_metrics(providers,reviews,blind,calids);atomic_write_json(RUN/'provider_calibration_results.json',metrics)
    decision=load(RUN/'provider_selection_decision.json')
    remaining=load(RUN/'grok_remaining_pairwise_results.json') if (RUN/'grok_remaining_pairwise_results.json').exists() else {'items':{},'failures':{},'calls':[]}
    calibration_grok=providers['grok'];model_results={**calibration_grok.get('items',{}),**remaining.get('items',{})}
    rows=[]
    for case in manifest:
        cid=case['case_id'];b=blind[cid];h=reviews[cid];current=b['current_position'];challenger='B' if current=='A' else 'A';choice=h['preferred_candidate']
        outcome='current_winner' if choice==current else 'challenger' if choice==challenger else choice
        rows.append({'case_id':cid,'quote_hash':case['quote_hash'],'candidate_a':case['candidate_a'],'candidate_b':case['candidate_b'],'human_choice':choice,'human_outcome':outcome,'preference_strength':h['preference_strength'],'prior_current_label':b['prior_human_label'],'selection_reason':case['selection_reason'],'current_position':current})
    fields=list(rows[0]);write_csv(RUN/'pairwise_validation.csv',rows,fields)
    counts=Counter(x['human_outcome'] for x in rows);strength=Counter(x['preference_strength'] for x in rows);prior=Counter(x['prior_current_label'] for x in rows)
    corrections=sum(x['prior_current_label']=='replace' and x['human_outcome']=='challenger' for x in rows);bad_neither=sum(x['prior_current_label']=='replace' and x['human_outcome']=='neither' for x in rows)
    retention=sum(x['prior_current_label']=='keep' and x['human_outcome']=='current_winner' for x in rows);harm=sum(x['prior_current_label']=='keep' and x['human_outcome']=='challenger' for x in rows);keep_neither=sum(x['prior_current_label']=='keep' and x['human_outcome']=='neither' for x in rows)
    exact=sum(model_results.get(x['case_id'],{}).get('preferred_candidate')==reviews[x['case_id']]['preferred_candidate'] for x in manifest)
    model_bad_challenger=sum(x['prior_current_label']=='replace' and x['human_outcome']=='challenger' and model_results[x['case_id']]['preferred_candidate']==x['human_choice'] for x in rows)
    model_bad_neither=sum(x['prior_current_label']=='replace' and x['human_outcome']=='neither' and model_results[x['case_id']]['preferred_candidate']=='neither' for x in rows)
    def normal(choice,cid):
        current=blind[cid]['current_position'];return 'current_winner' if choice==current else 'challenger' if choice in {'A','B'} else choice
    strong_current=[x for x in rows if x['prior_current_label']=='keep' and x['human_outcome']=='current_winner']
    model_strong_retained=sum(normal(model_results[x['case_id']]['preferred_candidate'],x['case_id'])=='current_winner' for x in strong_current)
    model_strong_damaged=len(strong_current)-model_strong_retained
    methods=[
      {'method':'A_current_winner','cases':50,'human_agreement':counts['current_winner']/50,'corrections':0,'harmful_displacements':0,'deferrals':0,'neither_cases':counts['neither'],'status':'evaluated'},
      {'method':'B_soft_light','cases':0,'human_agreement':'','corrections':'','harmful_displacements':'','deferrals':50,'neither_cases':'','status':'not reconstructable: challenger is not always the soft-light winner'},
      {'method':'C_pairwise_cached_fingerprint_challenger','cases':50,'human_agreement':exact/50,'corrections':model_bad_challenger+model_bad_neither,'harmful_displacements':model_strong_damaged,'deferrals':0,'neither_cases':sum(x.get('preferred_candidate')=='neither' for x in model_results.values()),'status':'evaluated with Grok selected by calibration'},
      {'method':'D_pairwise_soft_light','cases':0,'human_agreement':'','corrections':'','harmful_displacements':'','deferrals':50,'neither_cases':'','status':'not reconstructable: challenger is not always the soft-light winner'},
      {'method':'E_pairwise_high_risk','cases':0,'human_agreement':'','corrections':'','harmful_displacements':'','deferrals':50,'neither_cases':'','status':'not separately evaluated'},
      {'method':'F_pairwise_close_scores','cases':0,'human_agreement':'','corrections':'','harmful_displacements':'','deferrals':50,'neither_cases':'','status':'not separately evaluated'}]
    write_csv(RUN/'method_comparison.csv',methods,list(methods[0]))
    protection=[x|{'classification':'harmful_displacement_candidate' if x['human_outcome']=='challenger' else 'current_retained' if x['human_outcome']=='current_winner' else 'neither_publishable'} for x in rows if x['prior_current_label']=='keep']
    write_csv(RUN/'ordinary_case_protection.csv',protection,list(protection[0]))
    def retrospective(qhash,title):
        case=next(x for x in rows if x['quote_hash']==qhash);quote=intents[qhash]['quote_text'];model=model_results[case['case_id']];return f"# {title}\n\n**Quote:** {quote}\n\n| Field | Value |\n|---|---|\n| Candidate A | `{case['candidate_a']}` |\n| Candidate B | `{case['candidate_b']}` |\n| Human preference | {case['human_choice']} ({case['preference_strength']}) |\n| Blinded outcome | {case['human_outcome']} |\n| Prior current-image judgement | {case['prior_current_label']} |\n| Grok preference | {model['preferred_candidate']} ({model['preference_strength']}) |\n| Would publish | {model['would_publish_preferred']} |\n\n**Model reason:** {model['reason']}\n\nThe pair was judged without revealing the current winner.\n"
    everest=next(x['quote_hash'] for x in manifest if x['selection_reason']=='forced_everest_regression');free=next(x['quote_hash'] for x in manifest if x['selection_reason']=='forced_free_trade_regression')
    (RUN/'everest_pairwise_retrospective.md').write_text(retrospective(everest,'Everest Pairwise Retrospective'),encoding='utf-8');(RUN/'free_trade_pairwise_retrospective.md').write_text(retrospective(free,'Free-Trade Pairwise Retrospective'),encoding='utf-8')
    costs={p:metrics['providers'][p]['cost_usd'] for p in metrics['providers']};costs['grok_remaining']=sum(x.get('cost_usd',0) for x in remaining.get('calls',[]))
    summary={'schema_version':1,'analysis_kind':'pairwise_validation_summary','human_reviews':50,'human_outcomes':dict(counts),'preference_strength':dict(strength),'prior_labels':dict(prior),'bad_winner_corrections':corrections,'bad_winner_neither':bad_neither,'strong_control_retention':retention,'strong_control_harmful_displacement_candidates':harm,'strong_control_neither':keep_neither,'provider_calibration':metrics,'provider_selection':decision,'full_grok_validation':{'evaluated':len(model_results),'exact_agreement':exact/50,'bad_challenger_choices_matched':model_bad_challenger,'bad_neither_choices_matched':model_bad_neither,'strong_current_controls':len(strong_current),'strong_controls_retained':model_strong_retained,'strong_controls_damaged':model_strong_damaged},'cost_usd':costs,'recommendation':'pairwise ranking not supported: promising correction signal, but candidate construction produced too many neither choices and ordinary-control damage was too high'}
    atomic_write_json(RUN/'pairwise_validation_summary.json',summary)
    report=f"""# Pairwise Editorial Validation Report 001

## Executive conclusion

Pairwise ranking is **not yet supported for a larger experiment**. Human review completed all 50 blinded pairs, but **{counts['neither']} of 50** pairs selected neither image, showing that deterministic challenger construction did not reliably produce a publishable alternative. Grok achieved **{exact/50:.1%}** full-set agreement, but damaged **{model_strong_damaged} of {len(strong_current)}** strong controls that Tony retained.

## Manifest and human review

- 50 deterministic cases; Everest and free trade included.
- Current-winner position independently randomised and sealed.
- Human outcomes: current winner {counts['current_winner']}, challenger {counts['challenger']}, neither {counts['neither']}, unsure {counts['unsure']}.
- Preference strength: slight {strength['slight']}, moderate {strength['moderate']}, strong {strength['strong']}.
- Previously rejected winners: {prior['replace']}; challenger preferred {corrections}; neither acceptable {bad_neither}.
- Previously approved controls: {prior['keep']}; current retained {retention}; challenger preferred {harm}; neither acceptable {keep_neither}.

## Provider calibration

| Provider | Valid / 15 | Exact agreement on valid results | Failures | Known cost |
|---|---:|---:|---:|---:|
"""
    for p,row in metrics['providers'].items():
        agreement = "" if row['exact_agreement'] is None else f"{row['exact_agreement']:.1%}"
        report+=f"| {p} | {row['evaluated']} | {agreement} | {row['schema_failures']} | ${row['cost_usd']:.4f} |\n"
    report+="""

OpenAI and Anthropic returned repeated HTTP 400 responses to the structured schema. Grok completed 15/15 at {metrics['providers']['grok']['exact_agreement']:.1%} agreement. Gemini completed 13/15 at {metrics['providers']['gemini']['exact_agreement']:.1%}; Developer API quota failures invoked Vertex and two cases remained failed. Attempt histories are preserved and no case exceeded two Developer attempts.

## Safety decision

The predefined calibration gate selected Grok. Grok then completed the remaining 35 cases, producing 50/50 model judgements at a combined Grok cost of ${costs['grok']+costs['grok_remaining']:.4f}. Existing five earlier pairwise outputs were not overwritten.

## Method comparison

The evaluated pairwise method matched Tony in {exact}/50 cases. For previously rejected winners it matched {model_bad_challenger}/8 challenger choices and {model_bad_neither}/20 neither choices. Among {len(strong_current)} strong controls where Tony retained the current winner, it retained {model_strong_retained} and displaced or rejected {model_strong_damaged}. Methods requiring a known soft-light winner or selector score margin remain unavailable because those candidate traces were not present.

## Statistical limitations

The 50 cases reuse a deliberately enriched prior review set; candidates share images; challenger construction used lexical overlap over cached fingerprints; 44% neither choices reveal strong construction bias; the 15 calibration cases informed provider selection; and there is no independent hold-out. OpenAI/Anthropic compatibility failures also prevent a fair four-provider ranking.

## Recommendation

Do not proceed to 150-200 cases yet. Pairwise judgement shows useful correction ability and fixes Everest without a special rule, but ordinary-control damage and the 44% neither rate fail the stated safety criteria. First build challengers from real selector traces or cached per-quote scores, repair provider structured-output compatibility, and validate on an independent bounded set.
"""
    (RUN/'pairwise_validation_report_001.md').write_text(report,encoding='utf-8')
    print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
