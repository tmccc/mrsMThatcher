"""Build, merge, and score blind human calibration datasets."""

from __future__ import annotations

import hashlib
import math
import statistics
from collections import Counter, defaultdict
from typing import Any

RELATIONSHIPS = (
    "direct_illustration", "illustrates_mechanism",
    "illustrates_claimed_consequence", "illustrates_broader_principle",
    "related_ideological_substitution", "secondary_theme_match", "ambiguous",
    "unrelated", "contradictory",
)
BANDS = ("very_low", "low", "moderate", "high", "very_high")
IMAGE_DECISIONS = (
    "keep_current_image", "prefer_different_generated_image", "unsure",
)
REVIEW_SCHEMA_VERSION = 3
SCORES = (
    "relevance", "directness", "mechanism_alignment", "consequence_alignment",
    "principle_alignment", "specificity", "editorial_power", "overall_suitability",
)
SCORE = {"type": "number", "minimum": 0, "maximum": 100}
CALIBRATION_CRITIC_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "primary_relationship": {"type": "string", "enum": list(RELATIONSHIPS)},
        "secondary_relationship": {"type": "string", "enum": ["none", *RELATIONSHIPS]},
        **{name: SCORE for name in SCORES},
        "mechanism_matches": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
        "consequence_matches": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
        "principle_matches": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
        "extraneous_arguments": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
        "missing_primary_claims": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
        "explanation": {"type": "string", "minLength": 1, "maxLength": 1200},
        "preferred_visual_direction": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["primary_relationship", "secondary_relationship", *SCORES,
                 "mechanism_matches", "consequence_matches", "principle_matches",
                 "extraneous_arguments", "missing_primary_claims", "explanation",
                 "preferred_visual_direction", "confidence"],
}


def validate_calibration_critic(value: Any) -> dict[str, Any]:
    """Validate calibration critic."""
    if not isinstance(value, dict): raise ValueError("critic result must be an object")
    if value.get("primary_relationship") not in RELATIONSHIPS: raise ValueError("invalid primary relationship")
    if value.get("secondary_relationship") not in ("none", *RELATIONSHIPS): raise ValueError("invalid secondary relationship")
    for name in SCORES:
        score=value.get(name)
        if isinstance(score,bool) or not isinstance(score,(int,float)) or not 0 <= float(score) <= 100: raise ValueError(f"invalid {name}")
    for name in ("mechanism_matches","consequence_matches","principle_matches","extraneous_arguments","missing_primary_claims","preferred_visual_direction"):
        if not isinstance(value.get(name),list) or any(type(x) is not str for x in value[name]): raise ValueError(f"invalid {name}")
    if type(value.get("explanation")) is not str or not value["explanation"].strip(): raise ValueError("invalid explanation")
    confidence=value.get("confidence")
    if isinstance(confidence,bool) or not isinstance(confidence,(int,float)) or not 0 <= float(confidence) <= 1: raise ValueError("invalid confidence")
    return dict(value)

MECHANISM_TERMS = {
    "through", "by ", "because", "competition", "trade", "payments", "tax",
    "control", "choice", "law", "spending", "ownership", "defence", "union",
}
CONSEQUENCE_TERMS = {
    "growth", "prosper", "benefit", "price", "freedom", "liberty", "decline",
    "harm", "unemployment", "inflation", "poverty", "wealth", "strength",
    "destroy", "fail", "success", "secure", "consumer",
}
PRINCIPLE_TERMS = {
    "capital", "socialis", "communis", "freedom", "liberty", "democracy",
    "sovereign", "nation", "individual", "government", "market", "moral",
}


def _matching(values: list[str], terms: set[str]) -> list[str]:
    return [value for value in values if any(term in value.lower() for term in terms)]


def decompose_quote(item: dict[str, Any]) -> dict[str, Any]:
    """Return the decompose quote."""
    claims = [row["claim"] for row in item["claims"]]
    concepts = item.get("specific_concepts", [])
    mechanisms = _matching(concepts, MECHANISM_TERMS)
    consequences = _matching(concepts, CONSEQUENCE_TERMS)
    if not mechanisms:
        mechanisms = _matching(claims, {"through", "by ", "because", "leads", "produces"})
    if not consequences:
        consequences = _matching(claims, CONSEQUENCE_TERMS)
    consequences = [value for value in consequences if value not in mechanisms]
    principles = _matching(item.get("primary_themes", []) + item.get("secondary_themes", []), PRINCIPLE_TERMS)
    principles += [value for value in concepts if value not in principles and any(term in value.lower() for term in {"freedom", "liberty", "open market"})]
    return {
        "schema_version": 1, "analysis_kind": "local_quote_editorial_decomposition",
        "quote_hash": item["quote_hash"], "core_mechanism": mechanisms,
        "claimed_consequences": consequences,
        "broader_principles": principles,
        "source_claims": claims,
        "provenance": "deterministic_keyword_derivation_from_v2_fingerprint",
    }


def decompose_image(item: dict[str, Any]) -> dict[str, Any]:
    """Return the decompose image."""
    claims = [row["claim"] for row in item["implied_claims"]]
    mechanisms = _matching(claims, MECHANISM_TERMS | {"agent", "leads", "brings", "produces"})
    consequences = _matching(claims, CONSEQUENCE_TERMS)
    principles = _matching(item.get("primary_themes", []) + item.get("secondary_messages", []), PRINCIPLE_TERMS)
    return {
        "schema_version": 1, "analysis_kind": "local_image_editorial_decomposition",
        "image_basename": item["image_basename"],
        "depicted_subject": item.get("visual_evidence", [])[:6],
        "implied_mechanism": mechanisms, "depicted_consequence": consequences,
        "broader_ideological_framing": principles,
        "unclassified_claims": [claim for claim in claims if claim not in mechanisms and claim not in consequences],
        "provenance": "deterministic_keyword_derivation_from_v2_fingerprint",
    }


def case_id(quote_hash: str, image_basename: str) -> str:
    """Return a stable case identifier."""
    return hashlib.sha256(f"{quote_hash}:{image_basename}".encode()).hexdigest()[:20]


def select_review_cases(critics: list[dict[str, Any]], *, limit: int = 100,
                        free_trade_key: tuple[str, str] | None = None) -> list[dict[str, Any]]:
    """Select review cases."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in critics:
        groups[row["claim_relationship"]].append(row)
    for rows in groups.values():
        rows.sort(key=lambda row: (-float(row["editorial_power_score"]), float(row["semantic_alignment_score"]), row["quote_hash"], row["image_basename"]))
    selected: list[dict[str, Any]] = []
    # Round-robin gives rare relationships representation before filling common groups.
    while len(selected) < limit and any(groups.values()):
        for relationship in sorted(groups):
            if groups[relationship] and len(selected) < limit:
                selected.append(groups[relationship].pop(0))
    if free_trade_key:
        free = next((row for row in critics if (row["quote_hash"], row["image_basename"]) == free_trade_key), None)
        if free:
            selected = [row for row in selected if row is not free]
            selected.insert(0, free)
    return selected


def build_review_dataset(critics: list[dict[str, Any]], quote_items: dict[str, Any],
                         image_items: dict[str, Any], *, limit: int = 100,
                         free_trade_key: tuple[str, str] | None = None) -> dict[str, Any]:
    """Build review dataset."""
    selected = select_review_cases(critics, limit=limit, free_trade_key=free_trade_key)
    items = []
    for index, row in enumerate(selected):
        qhash, basename = row["quote_hash"], row["image_basename"]
        split = "calibration" if index < 25 else "holdout" if index < 50 else "reserve"
        items.append({
            "case_id": case_id(qhash, basename), "split": split,
            "quote_hash": qhash, "image_basename": basename,
            "old_relationship": row["claim_relationship"],
            "old_alignment": row["semantic_alignment_score"],
            "old_directness": row["directness_score"],
            "quote_decomposition": decompose_quote(quote_items[qhash]),
            "image_decomposition": decompose_image(image_items[basename]),
            "human_label": None,
        })
    return {"schema_version": REVIEW_SCHEMA_VERSION, "analysis_kind": "semantic_alignment_human_calibration",
            "relationships": list(RELATIONSHIPS), "bands": list(BANDS), "items": items}


def validate_human_label(label: Any) -> dict[str, Any]:
    """Validate human label."""
    if not isinstance(label, dict): raise ValueError("human label must be an object")
    if label.get("primary_relationship") not in RELATIONSHIPS: raise ValueError("invalid primary relationship")
    secondary = label.get("secondary_relationship")
    if secondary is not None and secondary not in RELATIONSHIPS: raise ValueError("invalid secondary relationship")
    for key in ("relevance", "directness"):
        if label.get(key) not in BANDS: raise ValueError(f"invalid {key} band")
    for key in ("appropriateness_rating", "publish_likelihood_rating"):
        value = label.get(key)
        if type(value) is not int or not 1 <= value <= 5:
            raise ValueError(f"{key} must be an integer from 1 to 5")
    decision = label.get("image_decision")
    if type(decision) is not str or decision not in IMAGE_DECISIONS:
        raise ValueError("image_decision must be an exact recognised value")
    for key in ("acceptable_for_posting", "preferred_over_current"):
        if key in label and type(label.get(key)) is not bool: raise ValueError(f"{key} must be boolean")
    if type(label.get("notes", "")) is not str: raise ValueError("notes must be a string")
    return dict(label)


def review_is_complete(row: dict[str, Any]) -> bool:
    """Return whether review is complete."""
    try:
        validate_human_label(row.get("human_label"))
        return True
    except ValueError:
        return False


def migrate_review_dataset(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate review dataset."""
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("invalid review dataset")
    migrated = dict(data); migrated["schema_version"] = REVIEW_SCHEMA_VERSION
    migrated["items"] = []
    for original in data["items"]:
        row = dict(original); label = row.get("human_label")
        if isinstance(label, dict):
            row["human_label"] = dict(label)  # Missing ratings remain missing.
        row["review_complete"] = review_is_complete(row)
        migrated["items"].append(row)
    return migrated


def merge_review_data(fresh: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
    """Merge review data."""
    prior = {row["case_id"]: row for row in migrate_review_dataset(existing)["items"]} if existing else {}
    merged = migrate_review_dataset(fresh)
    for row in merged["items"]:
        old = prior.get(row["case_id"])
        if old and old.get("human_label") is not None:
            row["human_label"] = old["human_label"]
            row["review_complete"] = review_is_complete(row)
    return merged


def review_progress(data: dict[str, Any]) -> dict[str, int]:
    """Return the review progress."""
    complete = sum(review_is_complete(row) for row in data.get("items", []))
    return {"total": len(data.get("items", [])), "complete": complete,
            "incomplete": len(data.get("items", [])) - complete}


def _rank(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__); result=[0.0]*len(values); index=0
    while index < len(order):
        end=index+1
        while end < len(order) and values[order[end]] == values[order[index]]: end += 1
        rank=(index+1+end)/2
        for pos in order[index:end]: result[pos]=rank
        index=end
    return result


def spearman(values_a: list[float], values_b: list[float]) -> float | None:
    """Return the spearman."""
    if len(values_a) != len(values_b) or len(values_a) < 2: return None
    a,b=_rank(values_a),_rank(values_b); ma,mb=statistics.mean(a),statistics.mean(b)
    numerator=sum((x-ma)*(y-mb) for x,y in zip(a,b)); da=sum((x-ma)**2 for x in a); db=sum((y-mb)**2 for y in b)
    return numerator / math.sqrt(da*db) if da and db else None


def labelled_review_statistics(data: dict[str, Any], critic_results: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the labelled review statistics."""
    rows=[row for row in data.get("items",[]) if review_is_complete(row)]
    appropriateness=[row["human_label"]["appropriateness_rating"] for row in rows]
    publish=[row["human_label"]["publish_likelihood_rating"] for row in rows]
    decisions=[row["human_label"]["image_decision"] for row in rows]
    def distribution(values):
        counts=Counter(values); total=len(values)
        return {name:{"count":count,"percentage":count*100/total if total else 0} for name,count in sorted(counts.items())}
    def decision_cross_tab(key):
        groups=defaultdict(list)
        for row in rows: groups[str(key(row))].append(row["human_label"]["image_decision"])
        return {name:distribution(values) for name,values in sorted(groups.items())}
    def grouped(key):
        groups=defaultdict(list)
        for row in rows: groups[key(row)].append(row)
        return {name:{"count":len(group),"appropriateness_mean":statistics.mean(x["human_label"]["appropriateness_rating"] for x in group),"appropriateness_median":statistics.median(x["human_label"]["appropriateness_rating"] for x in group),"publish_mean":statistics.mean(x["human_label"]["publish_likelihood_rating"] for x in group),"publish_median":statistics.median(x["human_label"]["publish_likelihood_rating"] for x in group)} for name,group in sorted(groups.items())}
    model=(critic_results or {}).get("items",{})
    paired=[(row,model[row["case_id"]]) for row in rows if row["case_id"] in model]
    correlations={"critic_relevance_vs_human_appropriateness":spearman([x[1]["relevance"] for x in paired],[x[0]["human_label"]["appropriateness_rating"] for x in paired]),"critic_directness_vs_human_appropriateness":spearman([x[1]["directness"] for x in paired],[x[0]["human_label"]["appropriateness_rating"] for x in paired]),"critic_overall_suitability_vs_publish_likelihood":spearman([x[1]["overall_suitability"] for x in paired],[x[0]["human_label"]["publish_likelihood_rating"] for x in paired])}
    def score_band(row):
        score=float(row["old_alignment"])
        return "80-100" if score >= 80 else f"{int(score//20)*20:02d}-{int(score//20)*20+19:02d}"
    decision_by_critic=defaultdict(list)
    for row,result in paired:
        band="80-100" if result["overall_suitability"]>=80 else f"{int(result['overall_suitability']//20)*20:02d}-{int(result['overall_suitability']//20)*20+19:02d}"
        decision_by_critic[band].append(row["human_label"]["image_decision"])
    return {"preliminary":len(rows)<50,"progress":review_progress(data),"appropriateness_distribution":dict(sorted(Counter(appropriateness).items())),"publish_likelihood_distribution":dict(sorted(Counter(publish).items())),"image_decision_distribution":distribution(decisions),"decision_by_primary_relationship":decision_cross_tab(lambda row:row["human_label"]["primary_relationship"]),"decision_by_appropriateness_rating":decision_cross_tab(lambda row:row["human_label"]["appropriateness_rating"]),"decision_by_publish_likelihood_rating":decision_cross_tab(lambda row:row["human_label"]["publish_likelihood_rating"]),"decision_by_critic_overall_band":{name:distribution(values) for name,values in sorted(decision_by_critic.items())},"by_relationship":grouped(lambda row:row["human_label"]["primary_relationship"]),"by_old_critic_score_band":grouped(score_band),"correlations":correlations,"paired_critic_cases":len(paired),"high_critic_low_publish":[row["case_id"] for row,result in paired if result["overall_suitability"]>=70 and row["human_label"]["publish_likelihood_rating"]<=2],"low_critic_high_publish":[row["case_id"] for row,result in paired if result["overall_suitability"]<40 and row["human_label"]["publish_likelihood_rating"]>=4],"high_critic_prefer_different":[row["case_id"] for row,result in paired if result["overall_suitability"]>=70 and row["human_label"]["image_decision"]=="prefer_different_generated_image"],"low_critic_keep_current":[row["case_id"] for row,result in paired if result["overall_suitability"]<40 and row["human_label"]["image_decision"]=="keep_current_image"],"high_appropriateness_prefer_different":[row["case_id"] for row in rows if row["human_label"]["appropriateness_rating"]>=4 and row["human_label"]["image_decision"]=="prefer_different_generated_image"],"low_publish_keep_current":[row["case_id"] for row in rows if row["human_label"]["publish_likelihood_rating"]<=2 and row["human_label"]["image_decision"]=="keep_current_image"],"unsure_cases":[row["case_id"] for row in rows if row["human_label"]["image_decision"]=="unsure"]}


def threshold_analysis(data: dict[str, Any], critic_results: dict[str, Any], *, minimum_cases: int = 20, target: str = "publish_likelihood") -> dict[str, Any]:
    """Return the threshold analysis."""
    model=critic_results.get("items",{}); pairs=[]
    excluded=0
    for row in data.get("items",[]):
        if not review_is_complete(row) or row["case_id"] not in model: continue
        if target == "publish_likelihood":
            rating=row["human_label"]["publish_likelihood_rating"]
            if rating in {1,2,4,5}: pairs.append((float(model[row["case_id"]]["overall_suitability"]),rating>=4))
            else: excluded += 1
        elif target == "image_decision":
            decision=row["human_label"]["image_decision"]
            if decision == "unsure": excluded += 1
            else: pairs.append((float(model[row["case_id"]]["overall_suitability"]),decision=="keep_current_image"))
        else: raise ValueError("unknown threshold target")
    if len(pairs)<minimum_cases or len({label for _,label in pairs})<2:
        return {"available":False,"target":target,"reason":f"requires at least {minimum_cases} labelled critic pairs with positive and negative classes","cases":len(pairs),"excluded_indeterminate":excluded}
    rows=[]
    for threshold in range(0,101):
        tp=sum(score>=threshold and truth for score,truth in pairs); fp=sum(score>=threshold and not truth for score,truth in pairs); tn=sum(score<threshold and not truth for score,truth in pairs); fn=sum(score<threshold and truth for score,truth in pairs)
        precision=tp/(tp+fp) if tp+fp else None; recall=tp/(tp+fn) if tp+fn else 0; specificity=tn/(tn+fp) if tn+fp else 0; fpr=fp/(fp+tn) if fp+tn else 0; fnr=fn/(fn+tp) if fn+tp else 0
        rows.append({"threshold":threshold,"tp":tp,"fp":fp,"tn":tn,"fn":fn,"precision":precision,"recall":recall,"specificity":specificity,"false_positive_rate":fpr,"false_negative_rate":fnr})
    recommended=max(rows,key=lambda row:(row["recall"]-row["false_positive_rate"],row["precision"] or 0,-row["threshold"]))
    return {"available":True,"target":target,"cases":len(pairs),"excluded_indeterminate":excluded,"criterion":"maximise recall minus false-positive rate; then precision; then prefer the lower threshold","candidate_thresholds":rows,"recommended_threshold":recommended,"caveat":"Exploratory separation only; validate on the untouched hold-out before operational use."}


def failure_summary(critics: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the failure summary."""
    return {
        "count": len(critics),
        "relationships": dict(sorted(Counter(row["claim_relationship"] for row in critics).items())),
        "lowest_scoring": sorted(critics, key=lambda row: (row["semantic_alignment_score"], row["quote_hash"]))[:20],
        "contradictions": [row for row in critics if row["claim_relationship"] == "contradiction"],
        "high_power_low_alignment": sorted([row for row in critics if row["editorial_power_score"] >= 60 and row["semantic_alignment_score"] < 40], key=lambda row: (-row["editorial_power_score"], row["semantic_alignment_score"]))[:20],
    }
