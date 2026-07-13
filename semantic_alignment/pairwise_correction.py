from __future__ import annotations

import hashlib,json,re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .pairwise_validation import assert_report_rendered

MIN_EDITORIAL_QUALITY=70
MIN_PRODUCTION_SCORE=40.0
MIN_TOPIC_SCORE=25.0

def load_trace_records(root:Path)->list[dict[str,Any]]:
    rows=[]
    for path in sorted(root.glob('runs/run_*/selections.jsonl')):
        with path.open(encoding='utf-8') as handle:
            for line in handle:
                row=json.loads(line)
                if row.get('candidate_detail'):rows.append({**row,'trace_file':str(path)})
    return rows

def trace_index(rows:list[dict[str,Any]])->dict[tuple[str,str],list[dict[str,Any]]]:
    result=defaultdict(list)
    for row in rows:
        result[(row.get('quote_hash'),row.get('production_image') or row.get('winner'))].append(row)
    return result

def eligible_trace_runner_up(trace:dict[str,Any],current:str,*,active:set[str],first_impressions:set[str],editorial:dict[str,Any],hashes:dict[str,str])->tuple[dict[str,Any]|None,str]:
    current_hash=hashes.get(current)
    selector_eligible=[item for item in trace.get('candidate_detail',[]) if item.get('identity_shadow_score','eligible') is not None]
    if not selector_eligible or selector_eligible[0].get('basename')!=current:
        return None,'trace does not rank the current winner first among selector-eligible candidates'
    if len(selector_eligible)<2:return None,'trace has no selector-eligible runner-up'
    item=selector_eligible[1];name=item.get('basename');quality=((editorial.get(name,{}).get('analysis') or {}).get('quality') or {}).get('overall',0)
    topics=float((item.get('components') or {}).get('topics') or 0);score=float(item.get('production_score') or 0)
    if item.get('source')!='generated':return None,'exact runner-up is not a generated candidate'
    if name not in active:return None,'exact runner-up is not active'
    if name not in first_impressions:return None,'exact runner-up lacks a cached first-impression fingerprint'
    if current_hash and hashes.get(name)==current_hash:return None,'exact runner-up duplicates the current image hash'
    if quality<MIN_EDITORIAL_QUALITY:return None,'exact runner-up is below the editorial-quality floor'
    if score<MIN_PRODUCTION_SCORE:return None,'exact runner-up is below the production-score floor'
    if topics<MIN_TOPIC_SCORE:return None,'exact runner-up is below the topic-score floor'
    return {'image_basename':name,'source':'real_production_runner_up','production_score':score,'topic_score':topics,'editorial_quality':quality,'trace_file':trace['trace_file'],'post_index':trace.get('post_index')},'highest-scoring eligible generated runner-up in exact quote/current-winner trace'

def validate_pilot_readiness(items:list[dict[str,Any]],blind:dict[str,Any],*,minimum:int=20)->dict[str,Any]:
    allowed={'exact_production_runner_up','exact_simulator_runner_up','cached_identical_state_runner_up','genuinely_eligible_soft_light_alternative'}
    issues=[]
    for row in items:
        private=blind.get(row.get('case_id'),{})
        if row.get('provenance') not in allowed:issues.append({'case_id':row.get('case_id'),'issue':'invalid_provenance'})
        if not private.get('eligibility_verified'):issues.append({'case_id':row.get('case_id'),'issue':'eligibility_not_verified'})
        if row.get('candidate_a')==row.get('candidate_b'):issues.append({'case_id':row.get('case_id'),'issue':'duplicate_pair'})
    credible=len(items)-len({x['case_id'] for x in issues})
    return {'ready':credible>=minimum and not issues,'credible_cases':credible,'minimum_required':minimum,'issues':issues,
            'status':'ready' if credible>=minimum and not issues else 'challenger construction still inadequate'}

def reconstruct_pairs(manifest:list[dict[str,Any]],blind:dict[str,Any],traces:dict[tuple[str,str],list[dict[str,Any]]],*,active:set[str],first_impressions:set[str],editorial:dict[str,Any],hashes:dict[str,str])->list[dict[str,Any]]:
    rows=[]
    for case in manifest:
        cid=case['case_id'];private=blind[cid];current=private['current_winner'];matches=traces.get((case['quote_hash'],current),[])
        chosen=None;reason='no exact selector trace for quote and current winner';trace_count=len(matches)
        for trace in matches:
            candidate,why=eligible_trace_runner_up(trace,current,active=active,first_impressions=first_impressions,editorial=editorial,hashes=hashes)
            if candidate and (chosen is None or candidate['production_score']>chosen['production_score']):chosen=candidate;reason=why
        rows.append({'case_id':cid,'quote_hash':case['quote_hash'],'current_winner':current,'old_challenger':private['challenger'],'old_source':'lexical_overlap','new_challenger':chosen['image_basename'] if chosen else None,'new_source':chosen['source'] if chosen else 'none','credible':bool(chosen),'change_reason':reason,'eligibility_verified':bool(chosen),'matching_trace_count':trace_count,'selector_evidence':chosen})
    return rows

def build_independent_pilot(traces:list[dict[str,Any]],original_pairs:set[tuple[str,str,str]],*,active:set[str],first_impressions:set[str],editorial:dict[str,Any],hashes:dict[str,str],quote_intents:set[str],limit:int=25)->dict[str,Any]:
    items=[];used=set()
    for trace in sorted(traces,key=lambda x:(x.get('quote_hash',''),x.get('post_index',0),x['trace_file'])):
        qhash=trace.get('quote_hash');current=trace.get('production_image') or trace.get('winner')
        if qhash not in quote_intents or current not in active or current not in first_impressions:continue
        challenger,_=eligible_trace_runner_up(trace,current,active=active,first_impressions=first_impressions,editorial=editorial,hashes=hashes)
        if not challenger:continue
        key=(qhash,current,challenger['image_basename'])
        if key in original_pairs or key in used:continue
        used.add(key);case_id=hashlib.sha256((':'.join(key)+':improved-pilot-v1').encode()).hexdigest()[:20]
        swap=int(case_id,16)%2==1;a,b=(challenger['image_basename'],current) if swap else (current,challenger['image_basename'])
        items.append({'case_id':case_id,'quote_hash':qhash,'candidate_a':a,'candidate_b':b,'input_hash':hashlib.sha256(':'.join((qhash,a,b)).encode()).hexdigest(),'source':'exact_selector_trace','selection_reason':'independent_exact_trace_with_quality_floors'})
        if len(items)>=limit:break
    return {'schema_version':1,'analysis_kind':'improved_pairwise_pilot_manifest','case_count':len(items),'items':items,'not_executed':True,'quality_floors':{'editorial_quality':MIN_EDITORIAL_QUALITY,'production_score':MIN_PRODUCTION_SCORE,'topic_component':MIN_TOPIC_SCORE}}

def validate_reconciliation(rows:list[dict[str,Any]])->None:
    if not rows or any(row.get('computed_value')!=row.get('rendered_value') or row.get('match') is not True for row in rows):raise ValueError('report reconciliation failed')

def validate_rendered_report(text:str)->None:
    assert_report_rendered(text)
    suspicious=[r"metrics\[",r"costs\[",r"\$\{",r"\{model_",r"\{exact\}"]
    found=[p for p in suspicious if re.search(p,text)]
    if found:raise ValueError(f'suspicious report template content: {found}')
