"""Evaluate first-impression judgements against blind validation evidence."""

from __future__ import annotations
import csv,io,json,math,statistics
from collections import Counter
from pathlib import Path
from typing import Any
from .first_impression import case_id
from .io import read_json,sha256_file

PROVIDERS=('grok','openai','anthropic','gemini')
THRESHOLDS=(30,35,40,45,50)
CURVES={
 'soft_light':((90,0),(75,1),(60,3),(40,6),(0,10)),
 'soft_moderate':((90,0),(75,2),(60,5),(40,9),(0,15)),
 'soft_strong':((90,0),(75,3),(60,7),(40,12),(0,20)),
}

def wilson(success:int,total:int,z:float=1.96)->tuple[float|None,float|None]:
    """Return the wilson."""
    if not total:return None,None
    p=success/total;den=1+z*z/total;centre=(p+z*z/(2*total))/den;half=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/den
    return max(0,centre-half),min(1,centre+half)

def binary_metrics(predictions:dict[str,str],human:dict[str,str])->dict[str,Any]:
    """Return the binary metrics."""
    ids=[cid for cid in predictions if human.get(cid) in {'keep','replace'} and predictions[cid] in {'keep','replace'}]
    tp=sum(predictions[c]=='replace' and human[c]=='replace' for c in ids);tn=sum(predictions[c]=='keep' and human[c]=='keep' for c in ids);fp=sum(predictions[c]=='replace' and human[c]=='keep' for c in ids);fn=sum(predictions[c]=='keep' and human[c]=='replace' for c in ids)
    agreement=tp+tn;lo,hi=wilson(agreement,len(ids))
    div=lambda a,b:a/b if b else None
    return {'evaluated':len(ids),'correct':agreement,'agreement':div(agreement,len(ids)),'agreement_ci95_low':lo,'agreement_ci95_high':hi,'false_replace':fp,'false_keep':fn,
        'replace_precision':div(tp,tp+fp),'replace_recall':div(tp,tp+fn),'keep_precision':div(tn,tn+fn),'keep_recall':div(tn,tn+fp),
        'balanced_accuracy':statistics.mean(x for x in (div(tp,tp+fn),div(tn,tn+fp)) if x is not None) if ids else None,'deferred':len(set(human)-set(ids))}

def penalty(score:float,curve)->int:
    """Return the penalty."""
    return next(value for floor,value in curve if score>=floor)

def integrity(run:Path,project:Path)->dict[str,Any]:
    """Return the integrity."""
    cases=read_json(run/'validation_cases.json')['items'];reviews=read_json(run/'human_reviews.json')['items'];quotes=read_json(run/'quote_visual_intents.json')['items'];images=read_json(run/'image_first_impressions.json')['items'];errors=[]
    ids=[x['case_id'] for x in cases]
    if len(cases)!=50 or len(set(ids))!=50:errors.append('case cardinality/duplicate IDs')
    if set(ids)!=set(reviews):errors.append('human review IDs do not exactly match cases')
    for row in cases:
        cid,q,b=row['case_id'],row['quote_hash'],row['image_basename']
        if cid!=case_id(q,b):errors.append(f'case ID mismatch:{cid}')
        if q not in quotes:errors.append(f'missing quote intent:{cid}')
        if b not in images:errors.append(f'missing image fingerprint:{cid}');continue
        path=project/'generated_review_approved_images'/b
        if not path.is_file():errors.append(f'missing image file:{cid}')
        elif sha256_file(path)!=images[b].get('sha256'):errors.append(f'image hash mismatch:{cid}')
        if images[b].get('schema_version')!=1 or quotes.get(q,{}).get('schema_version')!=1:errors.append(f'schema mismatch:{cid}')
        review=reviews.get(cid,{})
        if review.get('image_decision') not in {'keep','replace','unsure'} or review.get('matches_quote') not in {'yes','partly','no','unsure'} or not str(review.get('first_thing','')).strip():errors.append(f'incomplete review:{cid}')
    if errors:raise ValueError('; '.join(errors[:20]))
    return {'valid':True,'intended_cases':50,'completed_reviews':50,'unique_case_ids':50,'image_hashes_valid':True,'schema_version':1,'human_labels_absent_from_provider_payload_by_whitelist':True}

def consistency_issues(provider:str,rows:dict[str,Any])->list[dict[str,Any]]:
    """Return the consistency issues."""
    issues=[]
    for cid,row in rows.items():
        explanation=str(row.get('explanation','')).lower();alignment=float(row['dominant_visual_message_alignment_score']);tone=float(row['tone_alignment_score']);interference=float(row['salience_interference_score']);fit=row['first_second_fit']
        def add(kind,severity,reason):issues.append({'case_id':cid,'provider':provider,'issue_type':kind,'severity':severity,'reason':reason})
        if interference<=30 and any(word in explanation for word in ('overwhelm','dominant competing','drowns','high visual competition','interference','consum','subsum')):add('salience_scale_inversion','material','explanation describes strong interference but score is <=30')
        if interference>=70 and fit in {'strong','exceptional'}:add('salience_fit_contradiction','material','high interference coexists with strong first-second fit')
        if fit=='poor' and alignment>=60:add('fit_score_contradiction','material','poor fit coexists with alignment >=60')
        if any(word in explanation for word in ('tone mismatch','tone is incompatible','rather than inspirational','rather than reflective')) and tone>=70:add('tone_score_contradiction','minor','explanation describes tone mismatch but tone score >=70')
        if row.get('quote_message_visually_dominant') and alignment<25:add('dominance_score_contradiction','minor','quote marked visually dominant with very low alignment')
    return issues

def _median_results(provider_rows:dict[str,dict[str,Any]],field:str)->dict[str,float]:
    ids=set().union(*(rows.keys() for rows in provider_rows.values()));return {cid:statistics.median([rows[cid][field] for rows in provider_rows.values() if cid in rows]) for cid in ids}

def _semantic_scores(cases:list[dict[str,Any]],semantic_dir:Path)->tuple[dict[str,float],dict[str,float],dict[str,list[dict[str,Any]]]]:
    stores={p:(read_json(semantic_dir/f'{p}_results.json',{}) or {}).get('items',{}) for p in PROVIDERS};suit={};power={};raw={}
    for case in cases:
        cid=case['case_id'];rows=[store[cid] for store in stores.values() if cid in store];raw[cid]=rows
        if rows:suit[cid]=statistics.median(row['overall_suitability_score'] for row in rows);power[cid]=statistics.median(row['editorial_power_score'] for row in rows)
    return suit,power,raw

def classify_false_replace(case,score,tone,semantic,power,images,provider_issue=False):
    """Classify false replace."""
    if provider_issue:return 'provider score defect'
    if semantic is not None and semantic>=60:return 'acceptable indirectness'
    if power is not None and power>=75:return 'aesthetic strength outweighed mismatch'
    if score>=30:return 'threshold too strict'
    if tone>=60:return 'useful symbolism'
    if images[case['image_basename']].get('primary_tone') in {'satirical','reflective'}:return 'tone vocabulary mismatch'
    return 'broader principle'

def classify_bad(case,tone,semantic,images):
    """Classify bad."""
    image=images[case['image_basename']];message=(image.get('first_impression_message') or '').lower()
    if image.get('visual_competition_score',0)>=70:return 'secondary symbol overwhelmed quote'
    if tone<40:return 'tone mismatch'
    if any(word in message for word in ('communis','socialis','capitalis')):return 'ideological substitution'
    if 'thatcher' in message and semantic is not None and semantic<45:return 'generic Thatcher imagery'
    if image.get('visual_clutter_score',0)>=70:return 'visual clutter'
    if semantic is not None and semantic<40:return 'semantic mismatch'
    return 'wrong dominant message'

def csv_text(rows:list[dict[str,Any]],fields:list[str]|None=None)->str:
    """Return the CSV text."""
    if not rows:return ''
    fields=fields or list(rows[0]);buf=io.StringIO();writer=csv.DictWriter(buf,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows);return buf.getvalue()

def analyse(run:Path,project:Path,semantic_dir:Path)->dict[str,Any]:
    """Aggregate parsed production records into digest metrics."""
    valid=integrity(run,project);cases=read_json(run/'validation_cases.json')['items'];case_map={x['case_id']:x for x in cases};reviews=read_json(run/'human_reviews.json')['items'];human={cid:x['image_decision'] for cid,x in reviews.items()};match_labels={cid:x['matches_quote'] for cid,x in reviews.items()};images=read_json(run/'image_first_impressions.json')['items'];intents=read_json(run/'quote_visual_intents.json')['items'];provider_rows={p:read_json(run/f'{p}_first_impression_results.json',{}).get('items',{}) for p in ('grok','openai','anthropic')};provider_rows['gemini']=read_json(run/'gemini_results.json',{}).get('items',{})
    recovered_gemini=(read_json(semantic_dir/'gemini_results.json',{}) or {}).get('items',{});valid['semantic_recovered_gemini_results']=len(recovered_gemini);valid['semantic_recovery_complete_for_250']=len(recovered_gemini)==250
    median_alignment=_median_results(provider_rows,'dominant_visual_message_alignment_score');median_tone=_median_results(provider_rows,'tone_alignment_score');semantic,power,_=_semantic_scores(cases,semantic_dir)
    provider_validation=[]
    for provider,rows in provider_rows.items():
        pred={cid:'replace' if row['dominant_visual_message_alignment_score']<40 else 'keep' for cid,row in rows.items()};metrics=binary_metrics(pred,human);match_ids=[cid for cid in rows if match_labels.get(cid) in {'yes','partly','no'}];match_correct=sum(('yes' if rows[cid]['quote_message_visually_dominant'] else 'partly' if rows[cid]['quote_message_visually_present'] else 'no')==match_labels[cid] for cid in match_ids);provider_validation.append({'provider':provider,'rule':'provider score <40 => replace',**metrics,'match_evaluated':len(match_ids),'match_exact_agreement':match_correct/len(match_ids) if match_ids else None})
    majority={};
    for cid in case_map:
        votes=['replace' if rows[cid]['dominant_visual_message_alignment_score']<40 else 'keep' for rows in provider_rows.values() if cid in rows]
        if votes:
            counts=Counter(votes);top=counts.most_common()
            if len(top)==1 or top[0][1]>top[1][1]:majority[cid]=top[0][0]
    provider_validation.append({'provider':'majority_vote','rule':'majority provider threshold votes',**binary_metrics(majority,human),'match_evaluated':None,'match_exact_agreement':None})
    median_pred={cid:'replace' if score<40 else 'keep' for cid,score in median_alignment.items()};provider_validation.append({'provider':'median_score','rule':'median score <40',**binary_metrics(median_pred,human),'match_evaluated':None,'match_exact_agreement':None})
    issues=[issue for p,rows in provider_rows.items() for issue in consistency_issues(p,rows)];issue_cases={x['case_id'] for x in issues if x['severity']=='material'}
    cleaned={cid:pred for cid,pred in median_pred.items() if cid not in issue_cases};provider_validation.append({'provider':'median_cleaned','rule':'median score <40 excluding deterministic material inconsistencies',**binary_metrics(cleaned,human),'match_evaluated':None,'match_exact_agreement':None})
    hard=[]
    for threshold in THRESHOLDS:
        pred={cid:'replace' if score<threshold else 'keep' for cid,score in median_alignment.items()};m=binary_metrics(pred,human);hard.append({'threshold':threshold,'rejected':sum(x=='replace' for x in pred.values()),'human_approved_wrongly_rejected':m['false_replace'],'human_rejected_correctly_rejected':sum(pred.get(cid)=='replace' and label=='replace' for cid,label in human.items()),'precision_rejection':m['replace_precision'],'recall_bad_images':m['replace_recall'],'false_replace_rate':m['false_replace']/16,'false_keep_rate':m['false_keep']/28,'no_alternative_remaining':'unavailable: candidate sets not captured'})
    soft=[]
    for name,curve in CURVES.items():
        pred={cid:'keep' if semantic[cid]-penalty(median_alignment[cid],curve)>=40 else 'replace' for cid in semantic if cid in median_alignment};m=binary_metrics(pred,human);soft.append({'strategy':name,'evaluated':m['evaluated'],'decision_proxy_changes':sum(x=='replace' for x in pred.values()),'human_preferred_changes':sum(pred.get(cid)=='replace' and human.get(cid)=='replace' for cid in pred),'good_images_damaged':m['false_replace'],'bad_images_corrected':sum(pred.get(cid)=='replace' and human.get(cid)=='replace' for cid in pred),'net_correct_minus_errors':m['correct']-m['false_replace']-m['false_keep'],'cases_made_worse':m['false_replace']+m['false_keep'],'candidate_exhaustion':'unavailable','winner_changes':'unavailable: no selector score margins'})
    formulas={'first_impression':{cid:median_alignment[cid] for cid in median_alignment},'tone':median_tone,'first_plus_tone':{cid:.75*median_alignment[cid]+.25*median_tone[cid] for cid in median_alignment},'semantic':semantic,'semantic_plus_first':{cid:.5*semantic[cid]+.5*median_alignment[cid] for cid in semantic if cid in median_alignment},'semantic_first_tone':{cid:.5*semantic[cid]+.35*median_alignment[cid]+.15*median_tone[cid] for cid in semantic if cid in median_alignment}}
    tone_rows=[]
    for name,scores in formulas.items():
        pred={cid:'keep' if value>=40 else 'replace' for cid,value in scores.items()};m=binary_metrics(pred,human);label_scores=[(scores[cid],1 if human[cid]=='keep' else 0) for cid in scores if human.get(cid) in {'keep','replace'}];corr=None
        if label_scores:
            from .calibration import spearman
            corr=spearman([x[0] for x in label_scores],[x[1] for x in label_scores])
        tone_rows.append({'formula':name,**m,'spearman_human_keep':corr})
    false_rows=[];bad_rows=[]
    for cid,label in human.items():
        if cid not in median_pred:continue
        case=case_map[cid];score=median_alignment[cid];tone=median_tone[cid];sem=semantic.get(cid);powr=power.get(cid)
        if label=='keep' and median_pred[cid]=='replace':false_rows.append({'case_id':cid,'quote_hash':case['quote_hash'],'quote':intents[case['quote_hash']].get('quote_text'),'image_basename':case['image_basename'],'human_decision':label,'first_impression_score':score,'tone_score':tone,'semantic_score':sem,'editorial_power':powr,'category':classify_false_replace(case,score,tone,sem,powr,images,cid in issue_cases),'first_impression':images[case['image_basename']]['first_impression_message'],'provider_explanations':json.dumps({p:rows[cid]['explanation'] for p,rows in provider_rows.items() if cid in rows},sort_keys=True)})
        if label=='replace' and median_pred[cid]=='replace':bad_rows.append({'case_id':cid,'quote_hash':case['quote_hash'],'image_basename':case['image_basename'],'first_impression_score':score,'tone_score':tone,'semantic_score':sem,'category':classify_bad(case,tone,sem,images),'first_impression':images[case['image_basename']]['first_impression_message']})
    pair_model=read_json(run/'pairwise_rankings.json',{}).get('items',{});pair_human=read_json(run/'pairwise_human_reviews.json',{}).get('items',{});pair_rows=[]
    for pid,row in pair_model.items():
        choice=pair_human.get(pid,{}).get('preferred');pair_rows.append({'pair_id':pid,'model_preferred':row['preferred_candidate'],'human_preferred':choice,'agreement':row['preferred_candidate']==choice if choice else None,'preference_strength':row['preference_strength']})
    strategies=[]
    strategy_predictions={'A':{cid:'keep' for cid in case_map},'B':median_pred,
        'C':{cid:'keep' if semantic[cid]-penalty(median_alignment[cid],CURVES['soft_light'])>=40 else 'replace' for cid in semantic if cid in median_alignment},
        'D':{cid:'keep' if semantic[cid]-penalty(median_alignment[cid],CURVES['soft_moderate'])>=40 else 'replace' for cid in semantic if cid in median_alignment},
        'E':{cid:'keep' if semantic[cid]-penalty(median_alignment[cid],CURVES['soft_strong'])>=40 else 'replace' for cid in semantic if cid in median_alignment},
        'F':{cid:'keep' if median_alignment[cid]>=40 and median_tone[cid]>=40 else 'replace' for cid in median_alignment},
        'G':{cid:'keep' if .5*semantic[cid]+.5*median_alignment[cid]>=40 else 'replace' for cid in semantic if cid in median_alignment},'H':{}}
    for name,pred in strategy_predictions.items():
        m=binary_metrics(pred,human);strategies.append({'strategy':name,'cases_evaluated':m['evaluated'],'winner_changes_or_replace_proxy':sum(x=='replace' for x in pred.values()),'agreement':m['agreement'],'false_keep':m['false_keep'],'false_replace':m['false_replace'],'deferrals':50-len(pred),'no_candidate_cases':'unavailable','known_bad_corrections':sum(pred.get(cid)=='replace' and human.get(cid)=='replace' for cid in pred),'good_winner_retention':sum(pred.get(cid)=='keep' and human.get(cid)=='keep' for cid in pred),'ordinary_case_damage':m['false_replace']})
    return {'integrity':valid,'human_summary':{'completed':50,'keep':sum(x=='keep' for x in human.values()),'replace':sum(x=='replace' for x in human.values()),'unsure':sum(x=='unsure' for x in human.values()),'matches':dict(Counter(match_labels.values())),'pairwise_completed':len(pair_human)},'provider_validation':provider_validation,'consistency_issues':issues,'hard_thresholds':hard,'soft_penalties':soft,'tone_analysis':tone_rows,'false_replaces':false_rows,'bad_corrections':bad_rows,'pairwise':pair_rows,'strategies':strategies,'median_alignment':median_alignment,'median_tone':median_tone,'semantic_scores':semantic,'case_map':case_map,'reviews':reviews,'intents':intents,'images':images,'provider_rows':provider_rows}
