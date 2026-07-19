"""Aggregate provider critics into weighted disagreement and policy signals."""

from __future__ import annotations

import statistics
from collections import Counter
from typing import Any

PROVIDERS=("grok","openai","anthropic","gemini")
DEFAULT_THRESHOLDS={"wide_iqr":25,"score_outlier_distance":30,"high_suitability":65,"low_suitability":35,"high_alignment":65}


def validate_weights(data:dict[str,Any]|None, *, minimum_cases:int=20, cap:float=1.5):
    """Validate weights."""
    if not data: return {p:1.0 for p in PROVIDERS},"equal_default",0
    sample=data.get("sample_size",0)
    if type(sample) is not int or sample<minimum_cases: raise ValueError("insufficient human-reviewed cases for provider weights")
    weights=data.get("weights")
    if not isinstance(weights,dict) or set(weights)!=set(PROVIDERS): raise ValueError("invalid provider weights")
    clean={p:float(weights[p]) for p in PROVIDERS}
    if any(not 1/cap<=v<=cap for v in clean.values()): raise ValueError("provider weight exceeds cap")
    return clean,str(data.get("source","human_preferences")),sample


def _consensus(counter:Counter, provider_count:int):
    values=sorted(counter.values(),reverse=True)
    if values==[provider_count]: return "unanimous"
    if provider_count==4 and values==[3,1]: return "three_of_four"
    if provider_count==4 and values==[2,2]: return "two_two_split"
    if len(counter)==provider_count: return "complete_disagreement"
    return "plurality_only"


def analyse_case(case:dict[str,Any], rows:dict[str,dict[str,Any]], *, weights:dict[str,float]|None=None, thresholds:dict[str,float]|None=None):
    """Analyse case."""
    thresholds={**DEFAULT_THRESHOLDS,**(thresholds or {})}; weights=weights or {p:1.0 for p in rows}; n=len(rows)
    relationships=Counter(row["primary_relationship"] for row in rows.values()); operations=Counter(row["keep_or_replace"] for row in rows.values())
    med={f:statistics.median(float(row[f]) for row in rows.values()) for f in ("relevance_score","directness_score","overall_suitability_score")}
    overall=[float(row["overall_suitability_score"]) for row in rows.values()]; q=statistics.quantiles(overall,n=4,method="inclusive") if len(overall)>1 else [overall[0]]*3; iqr=q[2]-q[0]
    operational_level=_consensus(operations,n); relationship_level=_consensus(relationships,n); reasons=[]; outliers=[]
    for provider,row in rows.items():
        if operations[row["keep_or_replace"]]==1: outliers.append({"provider":provider,"field":"keep_or_replace","value":row["keep_or_replace"]})
        distance=abs(float(row["overall_suitability_score"])-med["overall_suitability_score"])
        if distance>thresholds["score_outlier_distance"]: reasons.append(f"score_outlier:{provider}")
        if row["primary_relationship"]=="unrelated" and row["directness_score"]>=thresholds["high_alignment"]: reasons.append(f"high_directness_unrelated:{provider}")
        if row["consequence_alignment_score"]>=thresholds["high_alignment"] and row["primary_relationship"]!="illustrates_claimed_consequence" and row.get("secondary_relationship")!="illustrates_claimed_consequence": reasons.append(f"unrecognised_consequence:{provider}")
    if iqr>thresholds["wide_iqr"]: reasons.append("wide_score_spread")
    majority=operations.most_common(); action="unsure"; confidence="low"; review=True
    if majority[0][1]==4 and majority[0][0] in {"keep","replace"}: action=majority[0][0]; confidence="high"; review=False
    elif majority[0][1]==3 and majority[0][0] in {"keep","replace"}: action=majority[0][0]; confidence="moderate"; review=False
    if operational_level in {"two_two_split","complete_disagreement","plurality_only"} or "wide_score_spread" in reasons: action="unsure"; confidence="low"; review=True
    if majority[0][0]=="replace" and med["overall_suitability_score"]>=thresholds["high_suitability"]: reasons.append("high_median_but_majority_replace"); review=True; confidence="low"
    if majority[0][0]=="keep" and med["overall_suitability_score"]<=thresholds["low_suitability"]: reasons.append("low_median_but_majority_keep"); review=True; confidence="low"
    weighted=Counter()
    for provider,row in rows.items(): weighted[row["keep_or_replace"]]+=weights.get(provider,1.0)
    weighted_action=weighted.most_common(1)[0][0] if len(weighted)==1 or weighted.most_common(2)[0][1]>weighted.most_common(2)[1][1] else "unsure"
    primary=relationships.most_common(); dominant=primary[0][0] if len(primary)==1 or primary[0][1]>primary[1][1] else None
    secondary=primary[1][0] if len(primary)>1 and primary[1][1]>=2 else None
    explanation=f"Operational votes: {dict(operations)}. Relationship votes: {dict(relationships)}."
    return {"case_id":case["case_id"],"provider_count":n,"primary_relationship_votes":dict(relationships),"operational_votes":dict(operations),"score_summary":{"relevance_median":med["relevance_score"],"directness_median":med["directness_score"],"overall_suitability_median":med["overall_suitability_score"],"overall_suitability_iqr":iqr},"relationship_consensus":relationship_level,"operational_consensus":operational_level,"numerical_consensus":"wide" if iqr>thresholds["wide_iqr"] else "compact","dominant_interpretation":dominant,"secondary_interpretation":secondary,"single_provider_outliers":outliers,"confidence":confidence,"recommended_action":action,"weighted_recommended_action":weighted_action,"requires_human_review":review,"review_reasons":sorted(set(reasons)),"explanation":explanation}


def priority(meta:dict[str,Any], *, free_trade:bool=False):
    """Return the priority."""
    reasons=meta["review_reasons"][:]
    if len(meta["primary_relationship_votes"])>=3: reasons.append("sharp_rationale_conflict")
    if free_trade: reasons.append("free_trade_case")
    if meta["operational_consensus"]=="two_two_split": band="urgent"; reasons.insert(0,"two_to_two_operational_split")
    elif meta["relationship_consensus"]=="complete_disagreement": band="urgent"; reasons.insert(0,"complete_taxonomy_disagreement")
    elif any("consequence" in x for x in reasons) or len(meta["primary_relationship_votes"])>=3: band="high"
    elif meta["requires_human_review"]: band="high"
    elif free_trade: band="medium"
    elif meta["confidence"]=="high": band="control"; reasons.append("unanimous_control")
    else: band="low"
    value={"urgent":5,"high":4,"medium":3,"low":2,"control":1}[band]
    return band,sorted(set(reasons)),value


def policy_actions(meta:dict[str,Any]):
    """Return the policy actions."""
    votes=Counter(meta["operational_votes"]); top=votes.most_common(1)[0]
    unanimous=top[1]==4 and top[0] in {"keep","replace"}; majority=top[1]>=3 and top[0] in {"keep","replace"}
    return {"A":top[0] if unanimous else "defer","B":top[0] if majority else "defer","C":meta["recommended_action"] if not meta["requires_human_review"] else "defer","D":meta["weighted_recommended_action"] if meta["weighted_recommended_action"] in {"keep","replace"} else "defer","E":top[0] if majority and not any("consequence" in x for x in meta["review_reasons"]) else "defer"}


def policy_summary(items:list[dict[str,Any]], human:dict[str,str]|None=None):
    """Return the policy summary."""
    human=human or {}; out={}
    for policy in "ABCDE":
        actions={x["case_id"]:policy_actions(x)[policy] for x in items}; resolved={k:v for k,v in actions.items() if v!="defer"}; labelled={k:v for k,v in resolved.items() if k in human}
        correct=sum(v==human[k] for k,v in labelled.items()); out[policy]={"resolved":len(resolved),"coverage":len(resolved)/len(items) if items else 0,"human_labelled_resolved":len(labelled),"accuracy":correct/len(labelled) if labelled else None,"false_keep":sum(v=="keep" and human[k]=="replace" for k,v in labelled.items()),"false_replace":sum(v=="replace" and human[k]=="keep" for k,v in labelled.items()),"review_burden":len(items)-len(resolved)}
    return out
