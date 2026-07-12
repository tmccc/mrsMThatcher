from __future__ import annotations
import re,statistics
from collections import Counter
from itertools import combinations
from typing import Any
from .bakeoff import SCORE_FIELDS

ADJACENT={
 ('direct_illustration','illustrates_mechanism'):.2,
 ('illustrates_mechanism','illustrates_claimed_consequence'):.45,
 ('illustrates_claimed_consequence','illustrates_broader_principle'):.3,
 ('illustrates_claimed_consequence','related_ideological_substitution'):.45,
 ('illustrates_broader_principle','related_ideological_substitution'):.35,
 ('related_ideological_substitution','secondary_theme_match'):.4,
 ('illustrates_broader_principle','secondary_theme_match'):.4,
 ('unrelated','contradictory'):.65,
 ('ambiguous','secondary_theme_match'):.5,
}
RATIONALE_FIELDS=('quote_mechanism','quote_claimed_consequences','quote_broader_principles','image_depicted_consequences','image_ideological_framing','matched_elements','unillustrated_primary_elements','extraneous_image_arguments')

def relationship_distance(a:str,b:str)->float:
    if a==b:return 0
    key=tuple(sorted((a,b))); lookup={tuple(sorted(k)):v for k,v in ADJACENT.items()}
    if key in lookup:return lookup[key]
    if 'contradictory' in key:return 1.0
    if 'unrelated' in key:return .9
    if 'ambiguous' in key:return .7
    return .65

def _tokens(values):
    text=' '.join(values if isinstance(values,list) else [str(values)])
    return {x for x in re.findall(r'[a-z0-9]+',text.lower()) if len(x)>3}

def _iqr(values):
    q=statistics.quantiles(values,n=4,method='inclusive'); return q[2]-q[0]

def disagreement(case_id:str,rows:dict[str,dict[str,Any]]):
    pairs=list(combinations(rows.items(),2)); operations=Counter(x['keep_or_replace'] for x in rows.values()); counts=sorted(operations.values(),reverse=True)
    operational=0 if counts==[4] else 25 if counts==[3,1] else 55 if counts==[2,1,1] else 80 if counts==[2,2] else 100
    taxonomy_values=[]; ordering_overlap=0
    for (_,a),(_,b) in pairs:
        d=relationship_distance(a['primary_relationship'],b['primary_relationship']); sa={a['primary_relationship'],a.get('secondary_relationship')}-{None}; sb={b['primary_relationship'],b.get('secondary_relationship')}-{None}
        if sa&sb:
            d*=.6; ordering_overlap+=1
        taxonomy_values.append(d)
    taxonomy=100*statistics.mean(taxonomy_values)
    field_dispersion={}; outliers=[]
    for field in SCORE_FIELDS:
        vals={p:float(r[field]) for p,r in rows.items()}; med=statistics.median(vals.values()); iqr=_iqr(list(vals.values())); mad=statistics.median(abs(v-med) for v in vals.values()); field_dispersion[field]={'median':med,'iqr':iqr,'mad':mad}
        outliers += [{'provider':p,'field':field,'distance_from_median':abs(v-med)} for p,v in vals.items() if abs(v-med)>30]
    score=min(100,statistics.mean(x['iqr'] for x in field_dispersion.values())*2)
    rationale_parts=[]
    for (_,a),(_,b) in pairs:
        scores=[]
        for field in RATIONALE_FIELDS:
            x,y=_tokens(a.get(field,[])),_tokens(b.get(field,[])); scores.append(1 if not x and not y else len(x&y)/len(x|y) if x|y else 1)
        rationale_parts.append(statistics.mean(scores))
    rationale=100*(1-statistics.mean(rationale_parts))
    inconsistencies=[]
    for provider,row in rows.items():
        rels={row['primary_relationship'],row.get('secondary_relationship')}
        if row['primary_relationship']=='unrelated' and row['relevance_score']>=65: inconsistencies.append(f'{provider}:unrelated_high_relevance')
        if row['mechanism_alignment_score']>=65 and 'illustrates_mechanism' not in rels and 'direct_illustration' not in rels: inconsistencies.append(f'{provider}:mechanism_not_classified')
        if row['consequence_alignment_score']>=65 and 'illustrates_claimed_consequence' not in rels: inconsistencies.append(f'{provider}:consequence_not_classified')
        if row['keep_or_replace']=='keep' and row['overall_suitability_score']<=30: inconsistencies.append(f'{provider}:keep_low_suitability')
        if row['keep_or_replace']=='replace' and row['overall_suitability_score']>=75 and not row.get('unillustrated_primary_elements') and not row.get('extraneous_image_arguments'): inconsistencies.append(f'{provider}:replace_high_suitability_no_defect')
    internal=min(100,len(inconsistencies)*25)
    total=round(.35*operational+.25*taxonomy+.15*score+.15*rationale+.10*internal)
    band='low' if total<20 else 'moderate' if total<45 else 'high' if total<70 else 'extreme'
    explanations=[]
    explanations.append('All providers agree operationally.' if operational==0 else f'Operational votes are split {dict(operations)}.')
    if taxonomy>=60: explanations.append('Primary editorial interpretations are substantially incompatible.')
    elif taxonomy>=30: explanations.append('Relationship labels differ but retain some conceptual overlap.')
    else: explanations.append('Relationship differences are mostly adjacent or ordering-only.')
    if score>=50: explanations.append('Scores show broad calibration spread.')
    elif score>=25: explanations.append('Scores show moderate spread.')
    if rationale>=60: explanations.append('Structured rationales have limited token overlap.')
    if inconsistencies: explanations.append(f'{len(inconsistencies)} provider-internal consistency flag(s).')
    return {'case_id':case_id,'disagreement_score':total,'disagreement_band':band,'components':{'operational':round(operational,2),'taxonomy':round(taxonomy,2),'primary_secondary_overlap_pairs':ordering_overlap,'score_dispersion':round(score,2),'rationale':round(rationale,2),'internal_inconsistency':round(internal,2)},'score_fields':field_dispersion,'score_outliers':outliers,'internal_inconsistencies':inconsistencies,'explanation':explanations}

def safeguarded_policies(meta:dict[str,Any],d:dict[str,Any]):
    votes=Counter(meta['operational_votes']); top=votes.most_common(1)[0]; majority=top[0] if top[1]>=3 and top[0] in {'keep','replace'} else 'defer'; unanimous=top[0] if top[1]==4 and top[0] in {'keep','replace'} else 'defer'
    return {'F':majority if d['disagreement_band'] in {'low','moderate'} else 'defer','G':unanimous if d['components']['internal_inconsistency']<50 else 'defer','H':majority if d['disagreement_band'] not in {'high','extreme'} else 'defer'}

def evaluation(predictions:dict[str,str],human:dict[str,str]):
    resolved={k:v for k,v in predictions.items() if v in {'keep','replace'}}; labelled={k:v for k,v in resolved.items() if human.get(k) in {'keep','replace'}}; reviewed={k:v for k,v in human.items() if v in {'keep','replace'}}
    errors=sum(v!=human[k] for k,v in labelled.items())
    return {'coverage':len(resolved)/len(predictions) if predictions else 0,'resolved':len(resolved),'deferred':len(predictions)-len(resolved),'labelled_resolved':len(labelled),'accuracy':(len(labelled)-errors)/len(labelled) if labelled else None,'false_keep':sum(v=='keep' and human[k]=='replace' for k,v in labelled.items()),'false_replace':sum(v=='replace' and human[k]=='keep' for k,v in labelled.items()),'review_efficiency':errors/len(reviewed) if reviewed else None}
