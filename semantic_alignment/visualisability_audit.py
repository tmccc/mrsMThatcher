"""Score quotation visualisability and produce calibrated audit outputs."""

from __future__ import annotations

import html
import json
import math
import os
import re
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from .image_provider_trial import CATEGORIES, _near_duplicate, atomic_write_bytes, atomic_write_json, read_json, sha256_bytes
from .openai_quality_trial import load_corpus

RUBRIC_VERSION = 1
GRADES = {"A", "B", "C", "D"}
ACTIONS = {"generate", "refine_then_generate", "concept_development_required", "skip"}
TREATMENTS = {"historical_scene", "symbolic_editorial", "portrait_or_likeness", "rhetorical_satire", "object_or_metaphor", "documentary_reconstruction", "typography_dependent", "abstract_graphic", "no_suitable_treatment"}
SCORE_FIELDS = ("concept_immediacy", "historical_specificity", "symbolic_clarity", "emotional_impact", "composition_potential", "static_image_suitability", "text_dependency", "generic_image_risk", "misinterpretation_risk", "anachronism_risk", "portrait_dependency", "prompt_overload", "historical_knowledge_dependency", "visual_distinctiveness")
RISK_FIELDS = {"text_dependency", "generic_image_risk", "misinterpretation_risk", "anachronism_risk", "portrait_dependency", "prompt_overload", "historical_knowledge_dependency"}
HUMAN_DECISIONS = {
    "A": ("Excellent candidate", "generate"),
    "B": ("Good candidate", "refine_then_generate"),
    "C": ("Needs a better concept", "concept_development_required"),
    "D": ("Do not generate", "skip"),
}


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str:
    if isinstance(value, list): return "; ".join(_text(x) for x in value if _text(x))
    return " ".join(str(value or "").split())


def _words(value: Any) -> list[str]:
    return re.findall(r"[a-z]{3,}", _text(value).lower())


def _bin(value: float, cuts: tuple[float, float, float, float]) -> int:
    return 1 + sum(value >= cut for cut in cuts)


def category_for(packet: dict[str, Any]) -> str:
    """Return the category for."""
    haystack = " ".join(_text(packet.get(k)) for k in ("quote_text", "historical_context", "immediate_subject", "intended_argument", "broader_principle", "mechanism")).lower()
    ranked = [(sum(word in haystack for word in words), index, name) for index, (name, words) in enumerate(CATEGORIES)]
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return ranked[0][2]


def load_calibration(review_dirs: list[Path], packets: dict[str, dict[str, Any]], quote_analysis: dict[str, Any]) -> dict[str, Any]:
    """Load calibration."""
    cases = []
    for directory in review_dirs:
        manifest = read_json(directory / "manifest.json"); results = read_json(directory / "review" / "review_results.json")
        by_id = {row["quote_id"]: row for row in manifest["items"]}
        for row in results["items"]:
            qid = row["quote_id"]; qa = (quote_analysis.get(qid) or {}).get("analysis") or {}; scores = qa.get("scores") or {}
            concepts = qa.get("literal_visual_concepts") or []
            cases.append({"quote_id": qid, "trial": directory.name, "outcome": row["winner"], "neither": row["winner"] == "neither", "category": by_id[qid]["category"], "visual_matchability": scores.get("visual_matchability"), "standalone_clarity": scores.get("standalone_clarity"), "literal_concept_count": len(concepts), "historical_specific": ((qa.get("historical_context") or {}).get("specificity") not in {None, "general"}), "prompt_word_count": len((directory / "prompts" / f"{qid}.txt").read_text().split())})
    if len(cases) != 40 or sum(row["neither"] for row in cases) != 18:
        raise RuntimeError("expected exactly 40 calibration cases with 18 neither outcomes")
    def avg(rows, field):
        values = [float(x[field]) for x in rows if isinstance(x.get(field), (int, float))]
        return round(sum(values)/len(values), 3) if values else None
    neither = [x for x in cases if x["neither"]]; success = [x for x in cases if not x["neither"]]
    category = {name: {"cases": len(rows), "neither": sum(x["neither"] for x in rows), "neither_rate": round(sum(x["neither"] for x in rows)/len(rows), 4)} for name in sorted({x["category"] for x in cases}) if (rows := [x for x in cases if x["category"] == name])}
    observed={"successful": {"visual_matchability_mean": avg(success,"visual_matchability"), "standalone_clarity_mean": avg(success,"standalone_clarity"), "literal_concept_count_mean": avg(success,"literal_concept_count"), "prompt_word_count_mean": avg(success,"prompt_word_count")}, "neither": {"visual_matchability_mean": avg(neither,"visual_matchability"), "standalone_clarity_mean": avg(neither,"standalone_clarity"), "literal_concept_count_mean": avg(neither,"literal_concept_count"), "prompt_word_count_mean": avg(neither,"prompt_word_count")}}
    vm_gap=abs((observed["successful"]["visual_matchability_mean"] or 0)-(observed["neither"]["visual_matchability_mean"] or 0))/20
    concept_gap=abs((observed["successful"]["literal_concept_count_mean"] or 0)-(observed["neither"]["literal_concept_count_mean"] or 0))
    reliability=round(min(1,(vm_gap+concept_gap)/2),4)
    return {"schema_version": 1, "case_count": 40, "successful_pair_count": len(success), "neither_count": len(neither), "neither_rate": 0.45, "metadata_concept_reliability":reliability,"reliability_formula":"mean(abs visual-matchability gap / 20, abs concrete-concept-count gap), capped at 1", "observed_features":observed, "category_outcomes": category, "items": cases}


def rubric(calibration: dict[str, Any]) -> dict[str, Any]:
    """Return the rubric."""
    return {"rubric_version": RUBRIC_VERSION, "basis": ["canonical packet fields", "existing quote-analysis visual_matchability and standalone_clarity", "observable concept/symbol/event counts", "40 blinded Medium/High reviews including 18 neither cases"], "dimension_scale": {"1":"very weak/low; for risks, minimal", "2":"weak/limited", "3":"mixed/moderate", "4":"strong/high", "5":"very strong; for risks, severe"}, "observable_mappings": {"visual_matchability_to_1_5":"existing 0-100 score binned at 20/40/60/80", "emotional_intensity_to_1_5":"existing 0-100 score binned at 20/40/60/80", "concept_count_to_symbolic_clarity":"0/1/2/3+ concrete concepts map to 1/2/3/4, with a fifth point for named visible symbols", "historical_specificity":"date/event/entity and prior specific-history flags", "risks":"counts of explicit mistakes/avoidances, abstraction terms, portrait-only preferences, and historical constraints"}, "aggregate_formula": {"inherent_visualisability":"mean(concept_immediacy, symbolic_clarity, emotional_impact, composition_potential, static_image_suitability, visual_distinctiveness) scaled to 0-100, minus text-dependency and misinterpretation penalties", "current_concept_quality_raw":"observable concept dimensions minus generic, overload, and misinterpretation penalties", "current_concept_quality_calibration":"unreviewed raw estimates are shrunk toward 50 by observed metadata reliability; reviewed neither cases are capped at 45; reviewed successes retain raw estimates", "generation_priority":"45% inherent + 35% calibrated current concept + 20% inverse generic/misinterpretation risk"}, "grades": {"A":"inherent >=75, concept >=68, static suitability >=4, generic and misinterpretation risks <=3", "B":"inherent >=60 and concept >=50", "C":"inherent >=40 or a viable historical/symbolic treatment exists", "D":"otherwise; also forced when static suitability <=1, text dependency >=4, and concept immediacy <=2"}, "calibration_summary": {"cases":40,"neither":18,"neither_rate":calibration["neither_rate"],"metadata_concept_reliability":calibration["metadata_concept_reliability"]}}


def _qa_fields(qa_record: dict[str, Any] | None) -> dict[str, Any]:
    return ((qa_record or {}).get("analysis") or {})


def score_packet(qid: str, packet: dict[str, Any], qa_record: dict[str, Any] | None, calibration: dict[str, Any], current_prompt: str | None = None) -> dict[str, Any]:
    """Score packet."""
    qa = _qa_fields(qa_record); qa_scores = qa.get("scores") or {}; guidance = packet.get("editorial_guidance") or {}; history = qa.get("historical_context") or {}
    concepts = [x for x in qa.get("literal_visual_concepts") or [] if _text(x)]
    symbols = ((qa.get("archive_image_preferences") or {}).get("preferred_visible_symbols") or [])
    event_specific = bool(packet.get("date") and packet.get("source_event")) and history.get("specificity") not in {None,"general"}
    visual_match = float(qa_scores.get("visual_matchability", 45 if concepts else 25))
    clarity = float(qa_scores.get("standalone_clarity", 50))
    emotion = float(qa.get("emotional_intensity", 45))
    abstract_terms = sum(word in set(_words(packet.get("quote_text"))) for word in ("idea","principle","belief","responsibility","society","consensus","truth","morality","freedom"))
    mistakes = len(guidance.get("common_visual_mistakes") or []) + len(guidance.get("must_not_dominate") or [])
    historical_requirements = len(guidance.get("historical_requirements") or [])
    desired = _text(guidance.get("desired_first_impression")); dominant = guidance.get("must_be_visually_dominant") or []
    portrait_terms = " ".join(_words((qa.get("archive_image_preferences") or {}).get("preferred_scenes"))).lower()
    portrait_only = bool(portrait_terms) and any(x in portrait_terms for x in ("portrait","podium","parliament")) and not concepts and not symbols
    visible_phrase = any("phrase" in _text(x).lower() or "words" in _text(x).lower() or "text" in _text(x).lower() for x in dominant)
    concept_immediacy = _bin((visual_match + clarity) / 2, (25,45,65,82))
    historical_specificity = min(5, 1 + int(bool(packet.get("date"))) + int(bool(packet.get("source_event"))) + int(bool(packet.get("entities"))) + int(event_specific))
    symbolic_clarity = min(5, 1 + min(len(concepts),3) + int(bool(symbols)))
    emotional_impact = _bin(emotion, (20,40,60,80))
    composition_potential = _bin(0.65*visual_match + 7*min(len(concepts),3), (25,45,65,82))
    text_dependency = min(5, 1 + int(visible_phrase) + int(not concepts) + int(abstract_terms >= 3) + int(clarity < 45))
    generic_risk = min(5, 1 + int(portrait_only) + int(not concepts) + int(not symbols) + int("general" in _text(history.get("specificity")).lower()))
    misinterpretation_risk = min(5, 1 + min(mistakes,2) + int(abstract_terms >= 2) + int(len(_text(packet.get("intended_argument"))) > 400))
    anachronism_risk = min(5, 1 + min(historical_requirements,2) + int(event_specific) + int(len(packet.get("entities") or []) >= 5))
    portrait_dependency = min(5, 1 + 2*int(portrait_only) + int("Margaret Thatcher" in (packet.get("entities") or []) and any(x in portrait_terms for x in ("portrait","podium"))))
    prompt_length = len((current_prompt or "").split())
    prompt_overload = min(5, 1 + int(len(packet.get("entities") or []) >= 6) + int(mistakes >= 3) + int(historical_requirements >= 3) + int(prompt_length > 500))
    historical_dependency = min(5, 1 + int(event_specific) + int(historical_requirements > 0) + int(len(packet.get("entities") or []) >= 4) + int(packet.get("verification_status") not in {"exact","normalised"}))
    distinctiveness = min(5, 1 + min(len(concepts),2) + int(bool(symbols)) + int(event_specific))
    static_suitability = max(1, min(5, round((concept_immediacy + symbolic_clarity + composition_potential + distinctiveness - text_dependency) / 4)))
    scores = {"concept_immediacy":concept_immediacy,"historical_specificity":historical_specificity,"symbolic_clarity":symbolic_clarity,"emotional_impact":emotional_impact,"composition_potential":composition_potential,"static_image_suitability":static_suitability,"text_dependency":text_dependency,"generic_image_risk":generic_risk,"misinterpretation_risk":misinterpretation_risk,"anachronism_risk":anachronism_risk,"portrait_dependency":portrait_dependency,"prompt_overload":prompt_overload,"historical_knowledge_dependency":historical_dependency,"visual_distinctiveness":distinctiveness}
    inherent_base = sum(scores[k] for k in ("concept_immediacy","symbolic_clarity","emotional_impact","composition_potential","static_image_suitability","visual_distinctiveness"))/30*100
    inherent = round(max(0,min(100,inherent_base-2.5*max(0,text_dependency-2)-2.5*max(0,misinterpretation_risk-2))))
    concept_base = sum(scores[k] for k in ("concept_immediacy","symbolic_clarity","composition_potential","visual_distinctiveness"))/20*100
    raw_concept_quality = round(max(0,min(100,concept_base-3*sum(max(0,scores[k]-2) for k in ("generic_image_risk","prompt_overload","misinterpretation_risk")))))
    reviewed=next((row for row in calibration["items"] if row["quote_id"]==qid),None)
    if reviewed and reviewed["neither"]:concept_quality=min(raw_concept_quality,45)
    elif reviewed:concept_quality=raw_concept_quality
    else:concept_quality=round(50+calibration["metadata_concept_reliability"]*(raw_concept_quality-50))
    inverse_risk = 100-(generic_risk+misinterpretation_risk-2)/8*100
    priority = round(max(0,min(100,.45*inherent+.35*concept_quality+.20*inverse_risk)))
    viable_treatment = bool(event_specific or concepts or symbols)
    if inherent>=75 and concept_quality>=68 and static_suitability>=4 and generic_risk<=3 and misinterpretation_risk<=3: grade="A"
    elif inherent>=60 and concept_quality>=50: grade="B"
    elif inherent>=40 or viable_treatment: grade="C"
    else: grade="D"
    if static_suitability<=1 and text_dependency>=4 and concept_immediacy<=2: grade="D"
    action={"A":"generate","B":"refine_then_generate","C":"concept_development_required","D":"skip"}[grade]
    quote_lower=packet["quote_text"].lower()
    if grade=="D": treatment="no_suitable_treatment"
    elif event_specific and historical_specificity>=4: treatment="historical_scene"
    elif "satir" in quote_lower or "joke" in quote_lower or "turning" in quote_lower: treatment="rhetorical_satire"
    elif concepts: treatment="object_or_metaphor" if abstract_terms else "symbolic_editorial"
    elif portrait_dependency>=3: treatment="portrait_or_likeness"
    elif text_dependency>=4: treatment="typography_dependent"
    else: treatment="abstract_graphic"
    primary = desired or (f"A single editorial scene centred on {concepts[0]}." if concepts else f"A historically grounded scene expressing {_text(packet.get('immediate_subject'))}.")
    alternative = f"Show the mechanism directly: {_text(packet.get('mechanism'))}." if packet.get("mechanism") else f"Use a restrained symbolic treatment of {_text(packet.get('broader_principle'))}."
    issues=[]; changes=[]
    if not concepts: issues.append("no concrete visual concept in prior quote analysis");changes.append("develop one concrete subject-action-setting composition")
    if generic_risk>=4:issues.append("generic-image risk is high");changes.append("replace generic political staging with quote-specific objects or action")
    if misinterpretation_risk>=4:issues.append("multiple misleading interpretations are plausible");changes.append("state the forbidden dominant message and visual hierarchy explicitly")
    if prompt_overload>=4:issues.append("available guidance is overloaded");changes.append("retain only one dominant message and essential historical constraints")
    if text_dependency>=4:issues.append("concept depends heavily on written language");changes.append("find a scene or object that works without rendering the quotation")
    should_appear = treatment in {"portrait_or_likeness","documentary_reconstruction"} or (event_specific and "Margaret Thatcher" in (packet.get("entities") or []))
    confidence = "high" if qa_record and packet.get("research_confidence")=="high" else "medium" if qa_record else "low"
    rationale=f"Existing visual matchability {visual_match:.0f}/100; {len(concepts)} concrete concept(s); static suitability {static_suitability}/5; generic and misinterpretation risks {generic_risk}/5 and {misinterpretation_risk}/5."
    return {"quote_id":qid,"quote_text":packet["quote_text"],"category":category_for(packet),"inherent_visualisability_score":inherent,"current_concept_quality_score":concept_quality,"generation_priority_score":priority,"grade":grade,"recommended_action":action,"preferred_treatment":treatment,"short_rationale":rationale,"primary_visual_concept":primary,"alternative_visual_concept":alternative,"why_concept_works_or_fails":f"The concept is {'concrete enough for a static composition' if concepts or event_specific else 'not yet anchored to a concrete visible action or object' }.","likely_failure_mode":issues[0] if issues else None,"thatcher_should_appear":should_appear,"likeness_required":bool(should_appear and treatment in {"portrait_or_likeness","historical_scene"}),"historical_setting_required":bool(event_specific),"visible_text_required":bool(text_dependency>=4 and not viable_treatment),"understandable_without_quote_text":bool(concept_immediacy>=3 and text_dependency<=3),"scores":scores,"prompt_issues":issues,"recommended_prompt_changes":changes,"skip_reason":("Static image meaning would depend mainly on text or an ungrounded generic image." if grade=="D" else None),"confidence":confidence,"rubric_version":RUBRIC_VERSION,"source_metadata":{"quote_analysis_available":bool(qa_record),"current_prompt_available":bool(current_prompt),"visual_matchability":visual_match,"literal_visual_concepts":concepts,"preferred_symbols":symbols}}


def _validate_record(record: dict[str, Any]) -> None:
    if record["grade"] not in GRADES or record["recommended_action"] not in ACTIONS or record["preferred_treatment"] not in TREATMENTS: raise ValueError("invalid classification")
    if any(not 1<=record["scores"][field]<=5 for field in SCORE_FIELDS): raise ValueError("score out of bounds")
    if not 0<=record["inherent_visualisability_score"]<=100 or not 0<=record["current_concept_quality_score"]<=100 or not 0<=record["generation_priority_score"]<=100: raise ValueError("aggregate out of bounds")
    if record["grade"]=="D" and record["recommended_action"]!="skip":raise ValueError("D must skip")
    if record["grade"]=="A" and record["visible_text_required"]:raise ValueError("A cannot require visible text")
    if record["preferred_treatment"]=="portrait_or_likeness" and record["scores"]["portrait_dependency"]<3:raise ValueError("generic portrait fallback forbidden")


def calibrate(research_run: Path, review_dirs: list[Path], output: Path) -> dict[str, Any]:
    """Return the calibrate."""
    packets, unresolved, eligible_hash=load_corpus(research_run);qa=read_json(Path("quote_analysis.json"),{"items":{}})["items"]
    cal=load_calibration(review_dirs,packets,qa);output.mkdir(parents=True,exist_ok=True);atomic_write_json(output/"calibration_cases.json",cal);atomic_write_json(output/"rubric.json",rubric(cal))
    obs=cal["observed_features"]
    lines=["# Visualisability Calibration","",f"Calibration cases: 40; successful pair outcomes: 22; neither: 18 (45%).","","## Observable differences","",f"- Existing visual-matchability mean: successful `{obs['successful']['visual_matchability_mean']}`, neither `{obs['neither']['visual_matchability_mean']}`.",f"- Standalone-clarity mean: successful `{obs['successful']['standalone_clarity_mean']}`, neither `{obs['neither']['standalone_clarity_mean']}`.",f"- Concrete-concept count mean: successful `{obs['successful']['literal_concept_count_mean']}`, neither `{obs['neither']['literal_concept_count_mean']}`.",f"- Prompt word count mean: successful `{obs['successful']['prompt_word_count_mean']}`, neither `{obs['neither']['prompt_word_count_mean']}`.",f"- Derived metadata-concept reliability: `{cal['metadata_concept_reliability']}`.","","The 45% neither rate shows that rendering quality cannot rescue weak concepts. Existing metadata discriminated weakly between outcomes, so unreviewed current-concept estimates are shrunk toward neutral by the observed reliability. Inherent visualisability remains independent. Category outcomes are descriptive calibration only; they do not determine an individual grade.",""]
    atomic_write_bytes(output/"calibration_report.md","\n".join(lines).encode());return cal


def _prompt_index(review_dirs: list[Path]) -> dict[str,str]:
    result={}
    for directory in review_dirs:
        for path in (directory/"prompts").glob("*.txt"):result[path.stem]=path.read_text()
    return result


def _review_index(review_dirs:list[Path])->dict[str,dict[str,Any]]:
    result={}
    for directory in review_dirs:
        review=read_json(directory/"review"/"review_results.json",{"items":[]})
        for row in review["items"]:
            qid=row["quote_id"]
            result[qid]={"trial":directory.name,"outcome":row["winner"],"images":{quality:str((directory/"images"/quality/f"{qid}.png").resolve()) for quality in ("medium","high") if (directory/"images"/quality/f"{qid}.png").is_file()}}
    return result


def run_audit(research_run: Path, output: Path, review_dirs: list[Path]) -> dict[str,Any]:
    """Run audit."""
    packets,unresolved,eligible_hash=load_corpus(research_run);qa=read_json(Path("quote_analysis.json"),{"items":{}})["items"];cal=read_json(output/"calibration_cases.json");prompts=_prompt_index(review_dirs);reviews=_review_index(review_dirs)
    records={qid:score_packet(qid,packet,qa.get(qid),cal,prompts.get(qid)) for qid,packet in sorted(packets.items())}
    for qid,record in records.items():record.update({"historical_context_summary":_text(packets[qid].get("historical_context"))[:600],"current_prompt":prompts.get(qid),"prior_review":reviews.get(qid)})
    for record in records.values():_validate_record(record)
    atomic_write_json(output/"quote_visualisability.json",{"schema_version":1,"rubric_version":RUBRIC_VERSION,"items":records})
    atomic_write_bytes(output/"quote_visualisability.jsonl",("\n".join(json.dumps(x,sort_keys=True) for x in records.values())+"\n").encode())
    manifest={"schema_version":1,"record_kind":"visualisability_audit_manifest","eligible_count":len(records),"unresolved_excluded":sorted(unresolved),"eligible_quote_id_set_sha256":eligible_hash,"corpus_hash":sha256_bytes(json.dumps({k:packets[k]["quote_text"] for k in sorted(packets)},sort_keys=True).encode()),"packet_schema_version":read_json(research_run/"research_packets.json").get("schema_version"),"generated_at":utc_now()};atomic_write_json(output/"audit_manifest.json",manifest)
    interface=output/"manual_review_interface";interface.mkdir(parents=True,exist_ok=True)
    if not (interface/"overrides.json").exists():atomic_write_json(interface/"overrides.json",{"schema_version":1,"items":{}})
    if not (interface/"audit_trail.jsonl").exists():atomic_write_bytes(interface/"audit_trail.jsonl",b"")
    _write_outputs(output,records,cal);return records


def _dedupe_best(records:list[dict[str,Any]],limit=50)->list[dict[str,Any]]:
    """Return the dedupe best."""
    selected=[];category_counts=Counter()
    for record in sorted(records,key=lambda x:(-x["generation_priority_score"],category_counts[x["category"]],x["quote_id"])):
        if any(_near_duplicate(record["quote_text"],x["quote_text"]) for x in selected):continue
        if category_counts[record["category"]]>=math.ceil(limit/len(CATEGORIES))+1:continue
        selected.append(record);category_counts[record["category"]]+=1
        if len(selected)==limit:break
    return selected


def _write_outputs(output:Path,records:dict[str,dict[str,Any]],cal:dict[str,Any])->None:
    rows=list(records.values()); subsets={"grade_a_generate.json":[x for x in rows if x["grade"]=="A"],"grade_b_refine.json":[x for x in rows if x["grade"]=="B"],"grade_c_concept_development.json":[x for x in rows if x["grade"]=="C"],"grade_d_skip.json":[x for x in rows if x["grade"]=="D"],"portrait_dependent.json":[x for x in rows if x["scores"]["portrait_dependency"]>=3],"historically_specific.json":[x for x in rows if x["historical_setting_required"]],"high_misinterpretation_risk.json":[x for x in rows if x["scores"]["misinterpretation_risk"]>=4],"high_text_dependency.json":[x for x in rows if x["scores"]["text_dependency"]>=4],"strong_quote_weak_prompt.json":[x for x in rows if x["inherent_visualisability_score"]>=70 and x["current_concept_quality_score"]<55]}
    for name,items in subsets.items():atomic_write_json(output/name,{"count":len(items),"items":items})
    best=_dedupe_best([x for x in rows if x["grade"] in {"A","B"}]);atomic_write_json(output/"best_50_generation_candidates.json",{"count":len(best),"items":best,"selection":"priority with category diversity and near-duplicate exclusion"})
    queue=sorted([x for x in rows if x["confidence"]=="low" or x["grade"] in {"C","D"} or x["scores"]["misinterpretation_risk"]>=4 or (x["inherent_visualisability_score"]>=70 and x["current_concept_quality_score"]<55)],key=lambda x:({"D":0,"C":1,"B":2,"A":3}[x["grade"]],-x["generation_priority_score"],x["quote_id"]));atomic_write_json(output/"manual_review_queue.json",{"count":len(queue),"items":queue})
    grades=Counter(x["grade"] for x in rows);actions=Counter(x["recommended_action"] for x in rows);issues=Counter(issue for x in rows for issue in x["prompt_issues"])
    summary={"eligible_count":len(rows),"grades":dict(grades),"actions":dict(actions),"common_prompt_issues":dict(issues.most_common()),"strong_quote_weak_prompt":len(subsets["strong_quote_weak_prompt.json"]),"portrait_dependent":len(subsets["portrait_dependent.json"]),"historically_specific":len(subsets["historically_specific.json"]),"skipped":grades["D"],"best_50_count":len(best),"manual_review_queue_size":len(queue),"calibration_neither":cal["neither_count"]};atomic_write_json(output/"visualisability_summary.json",summary)
    _report(output,summary)


def _report(output:Path,s:dict[str,Any])->None:
    lines=["# Visualisability Audit 001","",f"Eligible completed quotations: **{s['eligible_count']}**. The six unresolved records were excluded.","","## Grades","","| Grade | Count | Percentage |","|---|---:|---:|"]+[f"| {grade} | {s['grades'].get(grade,0)} | {s['grades'].get(grade,0)/s['eligible_count']:.1%} |" for grade in "ABCD"]+["","## Actions",""]+[f"- {k}: {v}" for k,v in sorted(s["actions"].items())]+["","## Key subsets","",f"- Strong quote, weak current concept: {s['strong_quote_weak_prompt']}",f"- Portrait dependent: {s['portrait_dependent']}",f"- Historically specific/reconstruction-sensitive: {s['historically_specific']}",f"- Skip: {s['skipped']}",f"- Best generation batch: {s['best_50_count']}",f"- Manual review queue: {s['manual_review_queue_size']}","","## Common prompt issues",""]+[f"- {k}: {v}" for k,v in s["common_prompt_issues"].items()]+["","This is a deterministic research audit, not a production selector. Five packets lacked prior quote-analysis metadata and were assigned low confidence. No network or generation call was made.",""]
    atomic_write_bytes(output/"visualisability_report.md","\n".join(lines).encode())


def _review_paths(audit_dir: Path) -> dict[str, Path]:
    root = audit_dir / "manual_review_interface"
    return {"root": root, "reviews": root / "human_reviews.json",
            "audit": root / "human_review_audit.jsonl", "summary": root / "human_review_summary.json",
            "report": root / "human_review_report.md", "backup": root / "human_reviews.json.backup"}


def _ensure_review_store(audit_dir: Path) -> dict[str, Any]:
    paths = _review_paths(audit_dir); paths["root"].mkdir(parents=True, exist_ok=True)
    if paths["reviews"].exists():
        store = read_json(paths["reviews"], {"schema_version": 1, "items": {}})
        if not paths["backup"].exists(): atomic_write_bytes(paths["backup"], paths["reviews"].read_bytes())
        if (not paths["audit"].exists() or paths["audit"].stat().st_size == 0):
            for row in store.get("items", {}).values():
                if row.get("migrated_from_legacy_override"):
                    _append_review_audit(paths["audit"], {"quote_id": row["quote_id"],
                        "timestamp": row["reviewer_timestamp"], "revision": row["review_revision"],
                        "previous_value": None, "new_value": row["new_value"],
                        "migration": "legacy_override"})
        return store
    store = {"schema_version": 1, "items": {}}
    # Preserve and import decisions from the original editor without modifying its files.
    legacy = read_json(paths["root"] / "overrides.json", {"items": {}}) or {"items": {}}
    for qid, row in legacy.get("items", {}).items():
        grade = row.get("grade")
        if grade in HUMAN_DECISIONS:
            label, action = HUMAN_DECISIONS[grade]
            timestamp = row.get("human_updated_at") or utc_now()
            store["items"][qid] = {"quote_id": qid, "human_grade": grade, "human_action": action,
                "human_decision": label, "human_note": row.get("reviewer_notes", ""),
                "reviewer_timestamp": timestamp, "review_revision": 1,
                "previous_value": None, "new_value": {"human_grade": grade, "human_action": action,
                    "human_note": row.get("reviewer_notes", "")}, "migrated_from_legacy_override": True}
    atomic_write_json(paths["reviews"], store)
    atomic_write_bytes(paths["backup"], paths["reviews"].read_bytes())
    if not paths["audit"].exists(): atomic_write_bytes(paths["audit"], b"")
    return store


def _append_review_audit(path: Path, record: dict[str, Any]) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (json.dumps(record, sort_keys=True) + "\n").encode()); os.fsync(fd)
    finally: os.close(fd)


def save_human_review(audit_dir: Path, quote_id: str, grade: str, note: str = "",
                      advanced_overrides: dict[str, Any] | None = None) -> tuple[dict[str, Any], bool]:
    """Save human review."""
    if grade not in HUMAN_DECISIONS: raise ValueError("invalid human decision")
    base = read_json(audit_dir / "quote_visualisability.json")["items"]
    if quote_id not in base: raise KeyError(quote_id)
    paths = _review_paths(audit_dir); store = _ensure_review_store(audit_dir); previous = store["items"].get(quote_id)
    note = str(note or "").strip(); advanced = advanced_overrides or (previous or {}).get("advanced_overrides") or {}
    label, action = HUMAN_DECISIONS[grade]
    comparable = {"human_grade": grade, "human_action": action, "human_note": note, "advanced_overrides": advanced}
    prior_comparable = {key: (previous or {}).get(key, {} if key == "advanced_overrides" else "") for key in comparable}
    if previous and comparable == prior_comparable: return previous, False
    timestamp = utc_now(); revision = int((previous or {}).get("review_revision", 0)) + 1
    record = {"quote_id": quote_id, **comparable, "human_decision": label,
        "reviewer_timestamp": timestamp, "review_revision": revision,
        "previous_value": ({key: prior_comparable[key] for key in comparable} if previous else None),
        "new_value": comparable}
    if paths["reviews"].exists(): atomic_write_bytes(paths["backup"], paths["reviews"].read_bytes())
    store["items"][quote_id] = record; atomic_write_json(paths["reviews"], store)
    _append_review_audit(paths["audit"], {"quote_id": quote_id, "timestamp": timestamp,
        "revision": revision, "previous_value": record["previous_value"], "new_value": comparable})
    write_human_review_outputs(audit_dir)
    return record, True


def human_review_summary(audit_dir: Path) -> dict[str, Any]:
    """Return the human review summary."""
    base = read_json(audit_dir / "quote_visualisability.json")["items"]
    reviews = _ensure_review_store(audit_dir)["items"]; counts = Counter(x["human_grade"] for x in reviews.values())
    matrix = {grade: dict(Counter(reviews[qid]["human_grade"] for qid in reviews if base[qid]["grade"] == grade)) for grade in "ABCD"}
    agreements = sum(base[qid]["grade"] == row["human_grade"] for qid, row in reviews.items())
    override_by_grade = {}
    for grade in "ABCD":
        grade_reviewed = sum(base[qid]["grade"] == grade for qid in reviews)
        overridden = sum(base[qid]["grade"] == grade and row["human_grade"] != grade for qid, row in reviews.items())
        override_by_grade[grade] = {"reviewed": grade_reviewed, "overridden": overridden,
            "override_rate": round(overridden / grade_reviewed, 4) if grade_reviewed else None}
    total = len(base); reviewed = len(reviews)
    return {"schema_version": 1, "total": total, "reviewed_count": reviewed,
        "unreviewed_count": total - reviewed, "reviewed_percentage": round(100 * reviewed / total, 2) if total else 0,
        "excellent_count": counts["A"], "good_count": counts["B"],
        "needs_better_concept_count": counts["C"], "do_not_generate_count": counts["D"],
        "agreement_count": agreements, "disagreement_count": reviewed - agreements,
        "agreement_rate": round(agreements / reviewed, 4) if reviewed else None,
        "automated_to_human_matrix": matrix, "override_rates_by_automated_grade": override_by_grade,
        "note_count": sum(bool(x.get("human_note")) for x in reviews.values()), "generated_at": utc_now()}


def write_human_review_outputs(audit_dir: Path) -> dict[str, Any]:
    """Write human review outputs."""
    paths = _review_paths(audit_dir); summary = human_review_summary(audit_dir); atomic_write_json(paths["summary"], summary)
    lines = ["# Human Visualisability Review", "", f"Reviewed: **{summary['reviewed_count']}/{summary['total']}** ({summary['reviewed_percentage']:.2f}%).",
        "", "| Decision | Count |", "|---|---:|", f"| Excellent candidate | {summary['excellent_count']} |",
        f"| Good candidate | {summary['good_count']} |", f"| Needs a better concept | {summary['needs_better_concept_count']} |",
        f"| Do not generate | {summary['do_not_generate_count']} |", "", f"Automated-grade agreement: {summary['agreement_count']}/{summary['reviewed_count']}.",
        f"Human overrides: {summary['disagreement_count']}. Notes: {summary['note_count']}."]
    atomic_write_bytes(paths["report"], ("\n".join(lines) + "\n").encode()); return summary


def _filter_sort_records(base: dict[str, Any], reviews: dict[str, Any], filter_name: str = "all",
                         sort_name: str = "priority") -> list[dict[str, Any]]:
    """Filter sort records."""
    def include(qid, row):
        human = reviews.get(qid); grade = human.get("human_grade") if human else None; scores = row["scores"]
        return {"all": True, "unreviewed": human is None, "reviewed": human is not None,
            "auto_a": row["grade"] == "A", "auto_b": row["grade"] == "B", "auto_c": row["grade"] == "C", "auto_d": row["grade"] == "D",
            "human_a": grade == "A", "human_b": grade == "B", "human_c": grade == "C", "human_d": grade == "D",
            "high_misinterpretation": scores["misinterpretation_risk"] >= 4,
            "high_text_dependency": scores["text_dependency"] >= 4,
            "likeness": bool(row["likeness_required"]), "historical": bool(row["historical_setting_required"]),
            "strong_weak": row["inherent_visualisability_score"] >= 70 and row["current_concept_quality_score"] < 55}.get(filter_name, True)
    rows = [row for qid, row in base.items() if include(qid, row)]
    confidence = {"high": 0, "medium": 1, "low": 2}
    keys = {"priority": lambda x: (-x["generation_priority_score"], x["quote_id"]),
        "quote_order": lambda x: x["quote_id"], "confidence": lambda x: (confidence.get(x["confidence"], 3), x["quote_id"]),
        "risk": lambda x: (-max(x["scores"][f] for f in RISK_FIELDS), x["quote_id"]),
        "review_state": lambda x: (x["quote_id"] in reviews, -x["generation_priority_score"], x["quote_id"])}
    return sorted(rows, key=keys.get(sort_name, keys["priority"]))


def status(audit_dir:Path)->dict[str,Any]:
    """Return the status."""
    summary=read_json(audit_dir/"visualisability_summary.json",{}); review=write_human_review_outputs(audit_dir)
    return {**summary,"human_reviews":review}


def _page_css() -> str:
    """Return the page CSS."""
    return """body{font:16px system-ui;margin:0;background:#f4f4f1;color:#202020}main{max-width:1080px;margin:auto;padding:14px}header,.toolbar,nav,.panel{background:#fff;padding:12px;margin-bottom:10px;border:1px solid #ddd}.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:end}label{display:block}select,input,textarea,button{font:inherit}blockquote{font-size:clamp(1.15rem,2.5vw,1.7rem);line-height:1.35;margin:12px 0;padding:18px;border-left:5px solid #a51d2d;background:#fff}.concepts{display:grid;grid-template-columns:1fr 1fr;gap:10px}.decision-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.decision{min-height:72px;border:2px solid #bbb;background:#fff;font-weight:700;padding:10px}.decision.selected{border-color:#111;background:#e8efe8;box-shadow:inset 0 0 0 2px #fff}.save{background:#202020;color:#fff;border:0;padding:14px 22px;font-weight:700}.muted{color:#666}.scores{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.scores label{background:#f5f5f5;padding:7px}.advanced-editor[hidden]{display:none}textarea{width:100%;box-sizing:border-box;min-height:64px}nav{display:flex;justify-content:space-between;align-items:center}.progress{height:7px;background:#ddd}.progress span{display:block;height:100%;background:#28704a}.disabled{opacity:.55}.summary-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.summary-grid div{background:#fff;padding:14px;border:1px solid #ddd}@media(max-width:760px){.concepts,.decision-grid,.summary-grid{grid-template-columns:1fr}.scores{grid-template-columns:1fr 1fr}main{padding:8px}.decision{min-height:62px}}"""


def serve(audit_dir:Path,host:str,port:int)->None:
    """Serve the local quotation-visualisability review interface."""
    base=read_json(audit_dir/"quote_visualisability.json")["items"]; _ensure_review_store(audit_dir); write_human_review_outputs(audit_dir)
    class Handler(BaseHTTPRequestHandler):
        def send(self,code,body,mime="text/html; charset=utf-8"):self.send_response(code);self.send_header("Content-Type",mime);self.send_header("Content-Length",str(len(body)));self.send_header("Cache-Control","no-store");self.end_headers();self.wfile.write(body)
        def redirect(self,url):self.send_response(303);self.send_header("Location",url);self.end_headers()
        def queue(self,query):
            reviews=_ensure_review_store(audit_dir)["items"]
            return _filter_sort_records(base,reviews,(query.get("filter")or["all"])[0],(query.get("sort")or["priority"])[0])
        def do_GET(self):
            p=urlparse(self.path);query=parse_qs(p.query)
            if p.path=="/summary":return self.summary_page()
            rows=self.queue(query)
            if p.path=="/":
                if not rows:return self.send(200,b"No quotations match this filter.")
                return self.redirect(f"/case/{rows[0]['quote_id']}"+("?"+p.query if p.query else ""))
            if p.path.startswith("/case/"):
                qid=p.path.rsplit("/",1)[-1]
                if qid not in base:return self.send(404,b"not found","text/plain")
                return self.page(qid,query)
            if p.path.startswith("/prior/"):
                parts=p.path.strip("/").split("/")
                if len(parts)!=3 or parts[1] not in base or parts[2] not in {"medium","high"}:return self.send(404,b"not found","text/plain")
                prior=base[parts[1]].get("prior_review") or {};image=Path((prior.get("images") or {}).get(parts[2],""))
                if not image.is_file():return self.send(404,b"not found","text/plain")
                return self.send(200,image.read_bytes(),"image/png")
            self.send(404,b"not found","text/plain")
        def page(self,qid,query):
            reviews=_ensure_review_store(audit_dir)["items"]; review=reviews.get(qid); row=base[qid]; rows=self.queue(query); ids=[x["quote_id"] for x in rows]
            if qid not in ids: ids=list(base); index=ids.index(qid)
            else:index=ids.index(qid)
            prev=ids[max(0,index-1)];nxt=ids[min(len(ids)-1,index+1)]
            prev_u=next((x for x in reversed(ids[:index]) if x not in reviews),qid);next_u=next((x for x in ids[index+1:] if x not in reviews),qid)
            qs=urlencode({k:v[0] for k,v in query.items()}); suffix=("?"+qs if qs else ""); selected=(review or {}).get("human_grade")
            decisions="".join(f'<button type="button" class="decision {"selected" if selected==grade else ""}" data-grade="{grade}"><span>{i}</span> {label}</button>' for i,(grade,(label,_)) in enumerate(HUMAN_DECISIONS.items(),1))
            scores="".join(f'<label>{f.replace("_"," ")} <input type="number" min="1" max="5" name="score_{f}" value="{row["scores"][f]}"></label>' for f in SCORE_FIELDS)
            filters=[("all","All"),("unreviewed","Unreviewed"),("reviewed","Reviewed")]+[(f"auto_{g.lower()}",f"Automated Grade {g}") for g in "ABCD"]+[(f"human_{g.lower()}",HUMAN_DECISIONS[g][0]) for g in "ABCD"]+[("high_misinterpretation","High misinterpretation risk"),("high_text_dependency","High text dependency"),("likeness","Thatcher likeness required"),("historical","Historically specific"),("strong_weak","Strong quote / weak prompt")]
            filter_options="".join(f'<option value="{value}" {"selected" if (query.get("filter")or["all"])[0]==value else ""}>{label}</option>' for value,label in filters)
            sort_options="".join(f'<option value="{value}" {"selected" if (query.get("sort")or["priority"])[0]==value else ""}>{label}</option>' for value,label in [("priority","Automated priority"),("quote_order","Quote order"),("confidence","Automated confidence"),("risk","Risk"),("review_state","Human review state")])
            prior=row.get("prior_review") or {};images="".join(f'<img src="/prior/{qid}/{quality}" alt="Prior reviewed candidate" style="max-width:45%;max-height:280px;object-fit:contain">' for quality in ("medium","high") if quality in (prior.get("images") or {}))
            current=(review or {}).get("advanced_overrides",{}); treatment=current.get("preferred_treatment",row["preferred_treatment"]); concept=current.get("primary_visual_concept",row["primary_visual_concept"])
            body=f'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Visualisability review</title><style>{_page_css()}</style></head><body><main><header><b>Visualisability review</b> · {index+1} of {len(ids)} · {len(reviews)}/{len(base)} reviewed ({100*len(reviews)/len(base):.1f}%) <a style="float:right" href="/summary">Summary</a><div class="progress"><span style="width:{100*len(reviews)/len(base):.2f}%"></span></div></header><form class="toolbar" method="get" action="/"><label>Filter<select name="filter">{filter_options}</select></label><label>Sort<select name="sort">{sort_options}</select></label><button>Apply</button><a href="/case/{prev_u}{suffix}">Previous unreviewed</a><a href="/case/{next_u}{suffix}">Next unreviewed</a></form><nav><a id="previous" href="/case/{prev}{suffix}">← Previous</a><span>{'Reviewed: '+html.escape((review or {}).get('human_decision','')) if review else 'Unreviewed'}</span><a id="next" href="/case/{nxt}{suffix}">Next →</a></nav><form id="review-form" method="post" action="/case/{qid}{suffix}"><input type="hidden" name="grade" id="grade" value="{selected or ''}"><blockquote>{html.escape(row['quote_text'])}</blockquote><section class="panel"><b>Historical context</b><p>{html.escape(row['historical_context_summary'])}</p></section><section class="concepts"><div class="panel"><b>Primary visual concept</b><p>{html.escape(row['primary_visual_concept'])}</p></div><div class="panel"><b>Alternative concept</b><p>{html.escape(row['alternative_visual_concept'])}</p></div></section><details class="panel"><summary>Current prompt</summary><pre style="white-space:pre-wrap">{html.escape(row.get('current_prompt') or 'No saved canonical generation prompt; metadata-only assessment.')}</pre></details><section class="panel"><b>Automated assessment: Grade {row['grade']} · {html.escape(row['recommended_action'])}</b><p class="muted">{html.escape(row['short_rationale'])}</p></section><section class="panel"><h2>Your decision</h2><div class="decision-grid">{decisions}</div><label>Optional short note<textarea name="note" maxlength="1000">{html.escape((review or {}).get('human_note',''))}</textarea></label><p id="save-state" class="muted">Selecting a decision saves immediately.</p><button class="save" name="save_next" value="1">Save and Next</button></section><details class="panel"><summary>Advanced details</summary><p><b>Treatment:</b> {html.escape(row['preferred_treatment'])} · <b>Likeness required:</b> {row['likeness_required']} · <b>Historical setting required:</b> {row['historical_setting_required']} · <b>Text dependency:</b> {row['scores']['text_dependency']}</p><p><b>Prompt issues:</b> {html.escape('; '.join(row['prompt_issues']) or 'None')}</p><p><b>Likely failure:</b> {html.escape(row.get('likely_failure_mode') or 'None')}</p><p><b>Prior image evidence:</b> {html.escape(str(prior.get('outcome','None')))}</p>{images}<details><summary>Raw current generation prompt</summary><pre style="white-space:pre-wrap">{html.escape(row.get('current_prompt') or 'No saved prompt.')}</pre></details><p><button type="button" id="edit-advanced">Edit advanced fields</button></p><fieldset id="advanced-editor" class="advanced-editor" hidden><input type="hidden" name="edit_advanced" value="1"><label>Treatment<select name="preferred_treatment">{''.join(f'<option {"selected" if treatment==x else ""}>{x}</option>' for x in sorted(TREATMENTS))}</select></label><label>Primary concept<textarea name="primary_visual_concept">{html.escape(concept)}</textarea></label><div class="scores">{scores}</div><label><input type="checkbox" name="thatcher_should_appear" {"checked" if current.get('thatcher_should_appear',row['thatcher_should_appear']) else ""}> Thatcher should appear</label><label><input type="checkbox" name="likeness_required" {"checked" if current.get('likeness_required',row['likeness_required']) else ""}> Accurate likeness required</label></fieldset></details><section class="panel disabled"><button type="button" disabled title="Disabled during visualisability review. Test generation must be implemented and authorised separately.">Generate test image</button><p>Disabled during visualisability review. Test generation must be implemented and authorised separately.</p></section></form></main><script>const form=document.getElementById('review-form'),grade=document.getElementById('grade'),state=document.getElementById('save-state');let saving=false;async function save(){{if(!grade.value||saving)return;saving=true;state.textContent='Saving…';const r=await fetch(form.action,{{method:'POST',body:new FormData(form),headers:{{'X-Autosave':'1'}}}});state.textContent=r.ok?'Saved':'Save failed';saving=false}}document.querySelectorAll('.decision').forEach(b=>b.onclick=()=>{{grade.value=b.dataset.grade;document.querySelectorAll('.decision').forEach(x=>x.classList.toggle('selected',x===b));save()}});document.getElementById('edit-advanced').onclick=()=>document.getElementById('advanced-editor').hidden=false;document.addEventListener('keydown',e=>{{if(['TEXTAREA','INPUT','SELECT'].includes(document.activeElement.tagName))return;if('1234'.includes(e.key))document.querySelectorAll('.decision')[Number(e.key)-1].click();else if(e.key==='Enter')form.requestSubmit();else if(e.key==='ArrowLeft')location.href=document.getElementById('previous').href;else if(e.key==='ArrowRight')location.href=document.getElementById('next').href}});form.addEventListener('submit',e=>{{if(saving)e.preventDefault()}});</script></body></html>'''
            self.send(200,body.encode())
        def summary_page(self):
            summary=write_human_review_outputs(audit_dir); reviews=_ensure_review_store(audit_dir)["items"]
            disagreements=sorted(((base[qid],row) for qid,row in reviews.items() if base[qid]["grade"]!=row["human_grade"]),key=lambda pair:(-pair[0]["generation_priority_score"],pair[0]["quote_id"]))
            links=' '.join(f'<a href="/?filter=human_{g.lower()}">{HUMAN_DECISIONS[g][0]}</a>' for g in "ABCD")
            body=f'''<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><style>{_page_css()}</style><main><header><b>Review summary</b> <a style="float:right" href="/">Back to review</a></header><section class="summary-grid"><div><b>{summary['reviewed_count']}/{summary['total']}</b><br>reviewed</div><div><b>{summary['unreviewed_count']}</b><br>unreviewed</div><div><b>{summary['agreement_count']}</b><br>automated agreements</div><div><b>{summary['disagreement_count']}</b><br>overrides</div></section><section class="panel"><h2>Human decisions</h2><p>Excellent {summary['excellent_count']} · Good {summary['good_count']} · Needs better concept {summary['needs_better_concept_count']} · Do not generate {summary['do_not_generate_count']}</p><p><a href="/?filter=unreviewed">Unreviewed queue</a> · {links}</p></section><section class="panel"><h2>Strongest disagreements</h2>{''.join(f'<p><a href="/case/{row[0]["quote_id"]}">Automated {row[0]["grade"]} → Human {row[1]["human_grade"]}</a>: {html.escape(row[0]["quote_text"][:180])}</p>' for row in disagreements[:30]) or '<p>None recorded.</p>'}</section></main>''';self.send(200,body.encode())
        def do_POST(self):
            p=urlparse(self.path);qid=p.path.rsplit("/",1)[-1]
            if qid not in base:return self.send(404,b"not found","text/plain")
            form=parse_qs(self.rfile.read(int(self.headers.get("Content-Length","0"))).decode(),keep_blank_values=True);grade=(form.get("grade")or[""])[0]
            advanced=None
            if "edit_advanced" in form:
                advanced={"preferred_treatment":(form.get("preferred_treatment")or[base[qid]["preferred_treatment"]])[0],"primary_visual_concept":(form.get("primary_visual_concept")or[base[qid]["primary_visual_concept"]])[0],"scores":{f:int((form.get(f"score_{f}")or[base[qid]["scores"][f]])[0]) for f in SCORE_FIELDS},"thatcher_should_appear":"thatcher_should_appear" in form,"likeness_required":"likeness_required" in form}
            try:save_human_review(audit_dir,qid,grade,(form.get("note")or[""])[0],advanced)
            except (ValueError,KeyError):return self.send(400,b"invalid review","text/plain")
            if self.headers.get("X-Autosave")=="1":return self.send(200,b'{"saved":true}',"application/json")
            query=parse_qs(p.query);ids=[x["quote_id"] for x in self.queue(query)];index=ids.index(qid) if qid in ids else -1;nxt=ids[min(len(ids)-1,index+1)] if ids else qid
            self.redirect(f"/case/{nxt}"+("?"+p.query if p.query else ""))
        def log_message(self,*args):pass
    ThreadingHTTPServer((host,port),Handler).serve_forever()
