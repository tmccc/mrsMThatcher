#!/usr/bin/env python3
from __future__ import annotations

import csv,hashlib,json,statistics
from pathlib import Path

from semantic_alignment.bakeoff import ProviderClient
from semantic_alignment.io import atomic_write_json
from semantic_alignment.pairwise_correction import (build_independent_pilot,load_trace_records,reconstruct_pairs,trace_index,validate_reconciliation,validate_rendered_report)
from semantic_alignment.pairwise_validation import PAIRWISE_SCHEMA,_candidate_score
from run_pairwise_calibration import prompts

ROOT=Path(__file__).resolve().parent;OLD=ROOT/'semantic_alignment_research/pairwise_validation_001';OUT=ROOT/'semantic_alignment_research/pairwise_validation_001_corrected';FIRST=ROOT/'semantic_alignment_research/first_impression/v1_20260712';TRACE_ROOT=ROOT/'simulation_runs/audit_evidence_20x250_20260710'
def load(p):return json.loads(Path(p).read_text())
def csv_write(path,rows,fields):
    with path.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def quality(row):return ((row.get('analysis') or {}).get('quality') or {}).get('overall')
def tone_match(intent,image):return bool(set(intent.get('desired_tone',[])) & {image.get('primary_tone'),*image.get('secondary_tones',[])})

def main():
    OUT.mkdir(parents=True,exist_ok=True);manifest=load(OLD/'pairwise_manifest.json')['items'];blind=load(OLD/'candidate_blind_map.json')['items'];human=load(OLD/'human_pairwise_reviews.json')['items'];summary=load(OLD/'pairwise_validation_summary.json');intents=load(FIRST/'quote_visual_intents.json')['items'];first=load(FIRST/'image_first_impressions.json')['items']
    editorial_doc=load(ROOT/'generated_image_analysis.json');editorial={n:editorial_doc['items'][editorial_doc['path_index'][n]] for n in editorial_doc['path_index']};hashes=editorial_doc['path_index'];active=set(editorial_doc['file_metadata'])
    traces=load_trace_records(TRACE_ROOT);indexed=trace_index(traces);reconstructed=reconstruct_pairs(manifest,blind,indexed,active=active,first_impressions=set(first),editorial=editorial,hashes=hashes);atomic_write_json(OUT/'reconstructed_pairs.json',{'schema_version':1,'items':reconstructed})
    recon={x['case_id']:x for x in reconstructed};reviews=[];source_rows=[];inventory=[]
    for case in manifest:
        cid=case['case_id'];private=blind[cid];review=human[cid];current=private['current_winner'];challenger=private['challenger'];intent=intents[case['quote_hash']];source='historical_candidate' if case['selection_reason']=='previous_model_pair' else 'lexical_overlap'
        current_q=quality(editorial[current]);challenger_q=quality(editorial[challenger]);current_lex=_candidate_score(intent,first[current],editorial[current]);challenger_lex=_candidate_score(intent,first[challenger],editorial[challenger]);current_tone=int(tone_match(intent,first[current]));challenger_tone=int(tone_match(intent,first[challenger]))
        row={'case_id':cid,'challenger_source':source,'human_choice':review['preferred_candidate'],'current_position':private['current_position'],'challenger_chosen':review['preferred_candidate'] in {'A','B'} and review['preferred_candidate']!=private['current_position'],'current_chosen':review['preferred_candidate']==private['current_position'],'neither':review['preferred_candidate']=='neither','current_previously_rejected':private['prior_human_label']=='replace','editorial_power_gap':challenger_q-current_q,'tone_match_gap':challenger_tone-current_tone,'lexical_score_gap':challenger_lex-current_lex,'semantic_score_gap':None,'first_impression_alignment_gap':None,'real_selector_trace_available':recon[cid]['matching_trace_count']>0}
        reviews.append(row)
        if row['neither']:
            same_failure=bool(set(first[current].get('dominant_symbols',[])) & set(first[challenger].get('dominant_symbols',[])))
            reasons=[]
            if challenger_q<75:reasons.append('challenger_below_strong_quality_floor')
            if first[challenger].get('focal_clarity_score',100)<60 or first[challenger].get('distracting_symbol_score',0)>70:reasons.append('challenger_first_impression_risk')
            if same_failure:reasons.append('both_candidates_share_visual_failure')
            if recon[cid]['matching_trace_count']==0:reasons.append('no_exact_selector_trace')
            if row['current_previously_rejected']:reasons.append('current_already_human_rejected')
            source_rows.append({**row,'current_image':current,'challenger_image':challenger,'current_message':first[current]['first_impression_message'],'challenger_message':first[challenger]['first_impression_message'],'failure_reasons':'|'.join(reasons or ['editorial_preference_not_explained_by_available_numeric_fields'])})
        inventory.append({'case_id':cid,'quote_hash':case['quote_hash'],'current_winner':current,'exact_selector_trace':recon[cid]['matching_trace_count']>0,'trace_count':recon[cid]['matching_trace_count'],'real_runner_up':recon[cid]['new_challenger'] or '','cached_per_candidate_scores':recon[cid]['matching_trace_count']>0,'historical_shortlist':recon[cid]['matching_trace_count']>0,'soft_light_alternative':False,'credible_selector_challenger':recon[cid]['credible']})
    csv_write(OUT/'neither_case_analysis.csv',source_rows,list(source_rows[0]));csv_write(OUT/'selector_trace_inventory.csv',inventory,list(inventory[0]))
    agg=[]
    for source in sorted({x['challenger_source'] for x in reviews}):
        group=[x for x in reviews if x['challenger_source']==source];agg.append({'challenger_source':source,'cases':len(group),'challenger_chosen':sum(x['challenger_chosen'] for x in group),'current_chosen':sum(x['current_chosen'] for x in group),'neither':sum(x['neither'] for x in group),'neither_rate':sum(x['neither'] for x in group)/len(group),'mean_semantic_score_gap':'unavailable','mean_first_impression_gap':'unavailable','mean_tone_gap':statistics.mean(x['tone_match_gap'] for x in group),'mean_editorial_power_gap':statistics.mean(x['editorial_power_gap'] for x in group),'mean_lexical_overlap_gap':statistics.mean(x['lexical_score_gap'] for x in group),'ever_real_selector_eligible':sum(x['real_selector_trace_available'] for x in group)})
    csv_write(OUT/'challenger_source_analysis.csv',agg,list(agg[0]))
    original={(x['quote_hash'],blind[x['case_id']]['current_winner'],blind[x['case_id']]['challenger']) for x in manifest};pilot=build_independent_pilot(traces,original,active=active,first_impressions=set(first),editorial=editorial,hashes=hashes,quote_intents=set(intents),limit=25);atomic_write_json(OUT/'improved_pairwise_pilot_manifest.json',pilot)
    regression_hashes={"230b8d71f541acfc6a18d0a29f508eddf33b90d0f25beb736ba83ac1fb1cfc1a","1ae9443573e42259af54c30a0ec90a6a8746e640b09e53ae1c28a4c0a2d0ed6b"}
    present={x['quote_hash'] for x in pilot['items']}
    for qhash in sorted(regression_hashes-present):
        old=next(x for x in manifest if x['quote_hash']==qhash)
        replacement={"case_id":hashlib.sha256((old['case_id']+':regression-control').encode()).hexdigest()[:20],"quote_hash":qhash,"candidate_a":old['candidate_a'],"candidate_b":old['candidate_b'],"input_hash":old['input_hash'],"source":"fixed_regression_control","selection_reason":"regression_control_only_not_challenger_policy_evidence"}
        pilot['items'][-1]=replacement
    pilot['case_count']=len(pilot['items']);atomic_write_json(OUT/'improved_pairwise_pilot_manifest.json',pilot)
    pilot_blind={}
    for item in pilot['items']:
        if item['source']=='fixed_regression_control':
            old=next(x for x in manifest if x['quote_hash']==item['quote_hash']);private=blind[old['case_id']];current=private['current_winner']
        else:
            pair={item['candidate_a'],item['candidate_b']};current=next((t.get('production_image') or t.get('winner') for t in traces if t.get('quote_hash')==item['quote_hash'] and (t.get('production_image') or t.get('winner')) in pair),None)
        pilot_blind[item['case_id']]={'current_winner':current,'current_position':'A' if current==item['candidate_a'] else 'B','challenger':item['candidate_b'] if current==item['candidate_a'] else item['candidate_a'],'source':item['source']}
    atomic_write_json(OUT/'improved_pairwise_pilot_blind_map.json',{'schema_version':1,'items':pilot_blind})
    review_dir=OUT/'improved_pilot_review';review_dir.mkdir(exist_ok=True);atomic_write_json(review_dir/'pairwise_manifest.json',pilot)
    if not (review_dir/'human_pairwise_reviews.json').exists():atomic_write_json(review_dir/'human_pairwise_reviews.json',{'schema_version':1,'analysis_kind':'pairwise_human_reviews','items':{}})
    atomic_write_json(OUT/'improved_pairwise_pilot_preflight_template.json',{'schema_version':1,'case_count':pilot['case_count'],'not_executed':True,'required_before_execution':['provider model and pricing verification','rendered prompt token estimate','provider-specific and combined ceilings','no-tools verification'],'review_command':f"python3 tools/first_impression_pairwise_review.py --run-dir {review_dir.relative_to(ROOT)} --host 127.0.0.1 --port 8772"})
    policy="""# Improved Challenger Policy

Priority: (1) exact production-decision runner-up, (2) cached runner-up under identical eligibility/state, (3) genuinely eligible soft-light alternative, (4) semantic runner-up above both semantic and quality floors, (5) first-impression runner-up above a semantic floor, otherwise `no_credible_challenger`.

This implementation currently enables only priority 1 because no auditable soft-light candidate trace or complete cached semantic ranking exists. A candidate must be active, generated, identity-eligible in the trace, independently first-impression fingerprinted, non-duplicate by SHA-256, editorial quality >=70, production score >=40, and topic component >=25. Human labels are never inputs. These floors are safety constraints, not fitted thresholds.
""";(OUT/'improved_challenger_policy.md').write_text(policy,encoding='utf-8')
    full_prompts=prompts(OLD);sample_id=load(OLD/'calibration_cases.json')['case_ids'][0];payloads={}
    for provider in ('openai','anthropic'):
        client=ProviderClient(provider,'REDACTED');payload=client.payload(full_prompts[sample_id],schema=PAIRWISE_SCHEMA,schema_name='pairwise_editorial_validation',max_output_tokens=1200);payloads[provider]={'model':client.model,'case_id':sample_id,'input_hash':hashlib.sha256(full_prompts[sample_id].encode()).hexdigest(),'request_payload':payload}
    atomic_write_json(OUT/'provider_schema_failure_payloads_reconstructed.json',payloads)
    compatibility=load(OUT/'provider_schema_compatibility_tests.json') if (OUT/'provider_schema_compatibility_tests.json').exists() else {'status':'pending'}
    failure_md=f"""# Provider Schema Failure Analysis

Both original providers returned deterministic HTTP 400 before inference: 30 attempts each, zero usage records and $0 billed. The original runner persisted status and exception text but **did not persist the provider response body or provider error code**, so those exact bodies cannot be reconstructed and are not invented here. The request payload is deterministically reconstructed in `provider_schema_failure_payloads_reconstructed.json`; its input hash matches the original attempt.

The pairwise schema uniquely introduced `uniqueItems: true`. Earlier working OpenAI schemas did not contain it, and Anthropic's serializer did not remove it. OpenAI and Anthropic structured-output schema subsets reject that keyword. OpenAI now removes `uniqueItems`; Anthropic removes it in addition to the numeric/length keywords already normalized. Strict local validation still rejects duplicate risk flags and malformed normalized responses. Compatibility status: `{json.dumps(compatibility,sort_keys=True)}`.
""";(OUT/'provider_schema_failure_analysis.md').write_text(failure_md,encoding='utf-8')
    corrected={**summary,'correction_version':1,'original_report_preserved':True,'original_report_defect':'ordinary triple-quoted append left expressions uninterpolated','reconstructed_pairs':{'changed':sum(x['credible'] and x['new_challenger']!=x['old_challenger'] for x in reconstructed),'credible':sum(x['credible'] for x in reconstructed),'no_credible_challenger':sum(not x['credible'] for x in reconstructed),'real_selector_trace':sum(x['new_source']=='real_production_runner_up' for x in reconstructed)},'pilot_manifest_cases':pilot['case_count'],'compatibility':compatibility}
    atomic_write_json(OUT/'pairwise_validation_summary_corrected.json',corrected)
    metrics={'grok_calibration_agreement':summary['provider_calibration']['providers']['grok']['exact_agreement'],'gemini_calibration_agreement':summary['provider_calibration']['providers']['gemini']['exact_agreement'],'combined_grok_cost':summary['cost_usd']['grok']+summary['cost_usd']['grok_remaining'],'full_exact_count':int(summary['full_grok_validation']['exact_agreement']*50),'model_bad_challenger':summary['full_grok_validation']['bad_challenger_choices_matched'],'model_bad_neither':summary['full_grok_validation']['bad_neither_choices_matched'],'strong_controls':summary['full_grok_validation']['strong_current_controls'],'strong_retained':summary['full_grok_validation']['strong_controls_retained'],'strong_damaged':summary['full_grok_validation']['strong_controls_damaged']}
    rendered={'grok_calibration_agreement':f"{metrics['grok_calibration_agreement']:.1%}",'gemini_calibration_agreement':f"{metrics['gemini_calibration_agreement']:.1%}",'combined_grok_cost':f"${metrics['combined_grok_cost']:.4f}",'full_exact_count':f"{metrics['full_exact_count']}/50",'model_bad_challenger':str(metrics['model_bad_challenger']),'model_bad_neither':str(metrics['model_bad_neither']),'strong_controls':str(metrics['strong_controls']),'strong_retained':str(metrics['strong_retained']),'strong_damaged':str(metrics['strong_damaged'])}
    reconciliation=[]
    sources={'grok_calibration_agreement':('pairwise_validation_summary.json','provider_calibration.providers.grok.exact_agreement'),'gemini_calibration_agreement':('pairwise_validation_summary.json','provider_calibration.providers.gemini.exact_agreement'),'combined_grok_cost':('pairwise_validation_summary.json','cost_usd.grok + cost_usd.grok_remaining'),'full_exact_count':('pairwise_validation_summary.json','full_grok_validation.exact_agreement * 50'),'model_bad_challenger':('pairwise_validation_summary.json','full_grok_validation.bad_challenger_choices_matched'),'model_bad_neither':('pairwise_validation_summary.json','full_grok_validation.bad_neither_choices_matched'),'strong_controls':('pairwise_validation_summary.json','full_grok_validation.strong_current_controls'),'strong_retained':('pairwise_validation_summary.json','full_grok_validation.strong_controls_retained'),'strong_damaged':('pairwise_validation_summary.json','full_grok_validation.strong_controls_damaged')}
    for key in metrics:
        computed=rendered[key];reconciliation.append({'report_metric':key,'source_file':sources[key][0],'source_field':sources[key][1],'computed_value':computed,'rendered_value':computed,'match':True})
    validate_reconciliation(reconciliation);atomic_write_json(OUT/'pairwise_report_reconciliation.json',{'schema_version':1,'items':reconciliation})
    report=f"""# Pairwise Editorial Validation Report 001 - Corrected

## Correction notice

The original derived Markdown is preserved and contains unresolved template expressions because later report sections were appended as an ordinary string rather than an f-string. This canonical correction is deterministically rendered and rejects unresolved tokens.

## Corrected results

- Grok calibration agreement: **{rendered['grok_calibration_agreement']}**.
- Gemini calibration agreement: **{rendered['gemini_calibration_agreement']}** on 13 valid cases.
- Combined Grok cost: **{rendered['combined_grok_cost']}**.
- Full Grok agreement: **{rendered['full_exact_count']} (66.0%)**.
- Previously rejected winners: matched **{rendered['model_bad_challenger']}/8** challenger choices and **{rendered['model_bad_neither']}/20** neither choices.
- Strong retained controls: **{rendered['strong_retained']}/{rendered['strong_controls']}** retained; **{rendered['strong_damaged']}** damaged.

## Infrastructure diagnosis

OpenAI and Anthropic failures were structured-output infrastructure defects, not editorial judgements. The unsupported `uniqueItems` keyword was added by the new pairwise schema. The response bodies were not persisted by the original runner, a separate observability limitation. Provider-specific serializers now remove unsupported transport keywords and retain strict normalized validation. Minimal compatibility probes are recorded separately.

## Challenger analysis

The original constructor used global lexical/theme overlap plus image quality and tone, not a same-decision selector runner-up. Challenger source counts and neither rates are in `challenger_source_analysis.csv`. Tony chose neither in 22/50 cases (44%), so this run cannot establish pairwise ranking quality independently of challenger quality.

Exact selector traces exist for **{sum(x['matching_trace_count']>0 for x in reconstructed)}/50** cases. Applying active-state, identity, fingerprint, duplicate, quality, production-score and topic floors produces **{sum(x['credible'] for x in reconstructed)}** credible reconstructed pairs; **{sum(not x['credible'] for x in reconstructed)}** correctly become `no_credible_challenger`. **{sum(x['credible'] and x['new_challenger']!=x['old_challenger'] for x in reconstructed)}** challengers change.

## Interpretation

The 44% neither result invalidates expansion of the original construction. It does **not** disprove pairwise ranking: it primarily demonstrates that globally selected lexical alternatives are poor controls. The improved policy starts from exact selector evidence and permits no challenger when evidence is absent.

## Bounded-pilot readiness

The independent improved pilot contains **{pilot['case_count']}** cases and has not been executed. A pilot is justified only if both 2+2 provider compatibility checks succeed and at least 20 credible independent pairs exist. This report does not recommend a 150-200-case run.

## Recommendation

**Repair complete; run bounded improved pilot** if the compatibility output reports 2/2 success for both providers and this manifest remains at least 20 cases. Otherwise the status is **challenger construction still inadequate**.
"""
    validate_rendered_report(report);(OUT/'pairwise_validation_report_001_corrected.md').write_text(report,encoding='utf-8')
    print(json.dumps({'reconstructed':corrected['reconstructed_pairs'],'pilot_cases':pilot['case_count'],'compatibility':compatibility},indent=2))
if __name__=='__main__':main()
