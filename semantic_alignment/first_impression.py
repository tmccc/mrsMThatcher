"""Build and validate first-impression and pairwise image assessments."""

from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .bakeoff import FREE_TRADE_KEY, PRICES, PROVIDER_MODELS, estimate_tokens
from .io import atomic_write_json
from .bakeoff import ProviderClient

SCHEMA_VERSION = 1
IMAGE_PROMPT_VERSION = "image-first-impression-v1"
QUOTE_INTENT_VERSION = "quote-visual-intent-v1-derived"
CRITIC_PROMPT_VERSION = "first-impression-alignment-v1"
PAIRWISE_PROMPT_VERSION = "first-impression-pairwise-v1"
EVEREST_QUOTE_HASH = "230b8d71f541acfc6a18d0a29f508eddf33b90d0f25beb736ba83ac1fb1cfc1a"
EVEREST_IMAGE = "tg_fbaf32a54650f629731270135c319348343a6c08189e71f22ccb74b1a1d89521.png"
TONES = {"inspirational", "patriotic", "optimistic", "triumphant", "reflective", "warning",
         "confrontational", "defensive", "mournful", "ominous", "satirical", "angry", "calm", "resolute"}
ENERGY = {"low", "medium", "high"}
SEVERITY = {"low", "medium", "high"}
FIT = {"exceptional", "strong", "acceptable_indirect", "weak_competed", "poor"}

STRING = {"type": "string", "minLength": 1, "maxLength": 1200}
STRINGS = {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 300}, "maxItems": 16}
SCORE = {"type": "integer", "minimum": 0, "maximum": 100}
ATTENTION = {"type": "object", "additionalProperties": False, "properties": {
    "rank": {"type": "integer", "minimum": 1, "maximum": 12}, "element": STRING,
    "attention_strength": {"type": "number", "minimum": 0, "maximum": 1}},
    "required": ["rank", "element", "attention_strength"]}
COMPETITION = {"type": "object", "additionalProperties": False, "properties": {
    "element": STRING, "competes_with": STRING, "severity": {"type": "string", "enum": sorted(SEVERITY)}},
    "required": ["element", "competes_with", "severity"]}

IMAGE_FIRST_OUTPUT_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "first_impression_message": STRING, "dominant_visual_subject": STRING, "first_object_noticed": STRING,
    "largest_visual_element": STRING, "highest_contrast_element": STRING, "most_recognisable_symbol": STRING,
    "dominant_symbols": STRINGS, "attention_hierarchy": {"type": "array", "items": ATTENTION, "minItems": 1, "maxItems": 8},
    "visual_competition": {"type": "array", "items": COMPETITION, "maxItems": 8},
    "concrete_visual_evidence": STRINGS, "face_prominence": SCORE, "text_like_element_prominence": SCORE,
    "background_dominance": SCORE, "focal_clarity_score": SCORE, "dominant_subject_strength": SCORE,
    "distracting_symbol_score": SCORE, "visual_competition_score": SCORE, "visual_clutter_score": SCORE,
    "primary_tone": {"type": "string", "enum": sorted(TONES)},
    "secondary_tones": {"type": "array", "items": {"type": "string", "enum": sorted(TONES)}, "maxItems": 6},
    "emotional_energy": {"type": "string", "enum": sorted(ENERGY)},
    **{f"{tone}_tone": SCORE for tone in ("patriotic", "inspirational", "warning", "defensive", "optimistic")},
    "dominant_visual_message_confidence": {"type": "number", "minimum": 0, "maximum": 1},
    "ambiguity": {"type": "string", "enum": sorted(SEVERITY)}},
    "required": ["first_impression_message", "dominant_visual_subject", "first_object_noticed",
        "largest_visual_element", "highest_contrast_element", "most_recognisable_symbol", "dominant_symbols",
        "attention_hierarchy", "visual_competition", "concrete_visual_evidence", "face_prominence",
        "text_like_element_prominence", "background_dominance", "focal_clarity_score", "dominant_subject_strength",
        "distracting_symbol_score", "visual_competition_score", "visual_clutter_score", "primary_tone",
        "secondary_tones", "emotional_energy", "patriotic_tone", "inspirational_tone", "warning_tone",
        "defensive_tone", "optimistic_tone", "dominant_visual_message_confidence", "ambiguity"]}

ALIGNMENT_OUTPUT_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "dominant_visual_message_alignment_score": SCORE, "tone_alignment_score": SCORE,
    "subject_alignment_score": SCORE, "salience_interference_score": SCORE,
    "first_second_fit": {"type": "string", "enum": sorted(FIT)}, "dominant_mismatch": STRING,
    "quote_message_visually_present": {"type": "boolean"}, "quote_message_visually_dominant": {"type": "boolean"},
    "competing_message": STRING, "competing_message_strength": {"type": "string", "enum": ["none", "secondary", "strong", "dominant"]},
    "editorial_risk": {"type": "string", "enum": sorted(SEVERITY)}, "explanation": STRING,
    "stronger_visual_direction": STRINGS}, "required": ["dominant_visual_message_alignment_score",
    "tone_alignment_score", "subject_alignment_score", "salience_interference_score", "first_second_fit",
    "dominant_mismatch", "quote_message_visually_present", "quote_message_visually_dominant", "competing_message",
    "competing_message_strength", "editorial_risk", "explanation", "stronger_visual_direction"]}

PAIRWISE_OUTPUT_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "preferred_candidate": {"type": "string", "enum": ["A", "B", "neither", "unsure"]},
    "preference_strength": {"type": "string", "enum": ["weak", "moderate", "strong"]},
    "better_first_impression": {"type": "string", "enum": ["A", "B", "tie", "neither"]},
    "better_tone_match": {"type": "string", "enum": ["A", "B", "tie", "neither"]},
    "less_distracting": {"type": "string", "enum": ["A", "B", "tie"]},
    "better_semantic_fit": {"type": "string", "enum": ["A", "B", "tie", "unknown"]},
    "overall_reason": STRING, "would_replace_current_winner": {"type": "boolean"}},
    "required": ["preferred_candidate", "preference_strength", "better_first_impression", "better_tone_match",
        "less_distracting", "better_semantic_fit", "overall_reason", "would_replace_current_winner"]}


def _strict_fields(value: Any, schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(schema["required"]):
        raise ValueError("result fields do not match schema")
    for key, spec in schema["properties"].items():
        item = value[key]
        if spec.get("type") == "integer" and (type(item) is not int or not 0 <= item <= 100):
            raise ValueError(f"invalid score: {key}")
        if "enum" in spec and item not in spec["enum"]:
            raise ValueError(f"invalid enum: {key}")
    return dict(value)


def validate_image_first(value: Any) -> dict[str, Any]:
    """Validate image first."""
    row = _strict_fields(value, IMAGE_FIRST_OUTPUT_SCHEMA)
    if row["primary_tone"] not in TONES or any(tone not in TONES for tone in row["secondary_tones"]):
        raise ValueError("invalid tone")
    if not row["attention_hierarchy"] or sorted(x["rank"] for x in row["attention_hierarchy"]) != list(range(1, len(row["attention_hierarchy"]) + 1)):
        raise ValueError("attention ranks must be contiguous")
    for item in row["attention_hierarchy"]:
        if isinstance(item["attention_strength"], bool) or not isinstance(item["attention_strength"], (int, float)) or not 0 <= item["attention_strength"] <= 1:
            raise ValueError("invalid attention strength")
    return row


def validate_alignment(value: Any) -> dict[str, Any]:
    """Validate alignment."""
    return _strict_fields(value, ALIGNMENT_OUTPUT_SCHEMA)
def validate_pairwise(value: Any) -> dict[str, Any]:
    """Validate pairwise."""
    return _strict_fields(value, PAIRWISE_OUTPUT_SCHEMA)


def image_first_prompt() -> str:
    """Return the image first prompt."""
    return f"""Prompt version: {IMAGE_PROMPT_VERSION}
View the supplied image as a social-media user scrolling quickly, before seeing any
quotation or caption. From pixels alone, state the single strongest message perceived
in the first second and which concrete element attracts attention first. Distinguish
dominant from secondary content. Identify largest/highest-contrast elements, symbols,
face/text/background prominence, visual competition, clutter and focal clarity.
Judge tone independently using only the controlled vocabulary in the schema. Scores
are bounded model judgements, not eye-tracking measurements. Do not infer a quotation,
origin quote, filename meaning, generation prompt, intended brief, expected answer or
existing analysis. Mention a named person only if visibly recognisable. No tools are
available. Return only the requested JSON."""


def derive_quote_intent(quote: dict[str, Any], legacy: dict[str, Any] | None = None) -> dict[str, Any]:
    """Derive quote intent."""
    legacy_analysis = (legacy or {}).get("analysis") or {}
    tones = [tone for tone in legacy_analysis.get("tone", []) if tone in TONES]
    undesired = list(dict.fromkeys([*quote.get("not_about", []), *legacy_analysis.get("archive_image_preferences", {}).get("strong_visual_mismatches", [])]))
    return {"schema_version": SCHEMA_VERSION, "analysis_kind": "quote_visual_intent",
        "quote_hash": quote["quote_hash"], "quote_text": quote["quote_text"],
        "desired_first_impression": legacy_analysis.get("summary") or quote["dominant_message"],
        "desired_primary_visual_subjects": list(quote.get("desired_visual_evidence", [])),
        "desired_tone": tones, "undesired_dominant_messages": undesired,
        "source_quote_fingerprint_prompt_version": quote.get("prompt_version"),
        "source_legacy_analysis_prompt_version": (legacy or {}).get("prompt_version"),
        "prompt_version": QUOTE_INTENT_VERSION, "derived_at": datetime.now(timezone.utc).isoformat(),
        "provenance": "deterministic_projection_of_existing_ai_fields"}


def critic_prompt(intent: dict[str, Any], image: dict[str, Any]) -> str:
    """Return the critic prompt."""
    allowed_intent = {key: intent[key] for key in ("schema_version", "analysis_kind", "quote_hash",
        "desired_first_impression", "desired_primary_visual_subjects", "desired_tone", "undesired_dominant_messages")}
    allowed_image = {key: image[key] for key in ("schema_version", "analysis_kind", "image_basename", "sha256",
        "first_impression_message", "dominant_visual_subject", "first_object_noticed", "dominant_symbols",
        "attention_hierarchy", "visual_competition", "concrete_visual_evidence", "focal_clarity_score",
        "dominant_subject_strength", "distracting_symbol_score", "visual_competition_score", "primary_tone",
        "secondary_tones", "emotional_energy", "patriotic_tone", "inspirational_tone", "warning_tone",
        "defensive_tone", "optimistic_tone", "ambiguity")}
    return f"""Prompt version: {CRITIC_PROMPT_VERSION}
Act as a picture editor judging the first second of a published quote/image pairing.
Compare only the independent visual-intent and first-impression fingerprints below.
Do not infer raw pixels, generation history, origin relationship, production status,
prior critics, human labels or expected answers. Separately judge dominant visual
message, tone, subject and salience interference. A semantic relationship can exist
while the dominant visual message is editorially wrong. Score bands: 90-100 exceptional;
75-89 strong; 60-74 acceptable but indirect; 40-59 weak or competed; 0-39 dominant
visual mismatch. No tools are available. Return only requested JSON.
QUOTE_VISUAL_INTENT:{json.dumps(allowed_intent, sort_keys=True, separators=(',', ':'))}
IMAGE_FIRST_IMPRESSION:{json.dumps(allowed_image, sort_keys=True, separators=(',', ':'))}"""


def pairwise_prompt(intent: dict[str, Any], a: dict[str, Any], b: dict[str, Any], semantic_a: dict[str, Any] | None = None, semantic_b: dict[str, Any] | None = None) -> str:
    """Return the pairwise prompt."""
    def clean_image(row): return {key: row[key] for key in ("image_basename", "first_impression_message", "dominant_visual_subject", "first_object_noticed", "dominant_symbols", "primary_tone", "secondary_tones", "focal_clarity_score", "distracting_symbol_score", "visual_competition_score")}
    payload = {"quote_visual_intent": {key: intent[key] for key in ("quote_hash", "desired_first_impression", "desired_primary_visual_subjects", "desired_tone", "undesired_dominant_messages")},
        "candidate_A": clean_image(a), "candidate_B": clean_image(b),
        "semantic_alignment_A": semantic_a or {"status": "unavailable"}, "semantic_alignment_B": semantic_b or {"status": "unavailable"}}
    return f"""Prompt version: {PAIRWISE_PROMPT_VERSION}
Choose which anonymous candidate makes the stronger published post alongside the
quotation intent. Compare first impression, tone, distraction and semantic fit. Do
not infer production identity, winner status, history, human labels or generation
prompts. A and B are arbitrary labels. No tools are available. Return only JSON.
INPUT:{json.dumps(payload, sort_keys=True, separators=(',', ':'))}"""


def case_id(quote_hash: str, image: str) -> str:
    """Return a stable case identifier."""
    return hashlib.sha256(f"{quote_hash}:{image}".encode()).hexdigest()[:20]


def build_validation_cases(cases: list[dict[str, Any]], human: dict[str, Any], disagreements: dict[str, Any], active_images: set[str], *, limit: int = 50) -> dict[str, Any]:
    """Return whether build validation cases."""
    by_id = {row["case_id"]: row for row in cases if row["image_basename"] in active_images}
    dis = {row["case_id"]: row for row in disagreements}
    selected: list[dict[str, Any]] = []; used = set()
    def add(q, image, reason, source="existing_250"):
        key = (q, image)
        if key in used or image not in active_images or len(selected) >= limit: return
        used.add(key); selected.append({"case_id": case_id(q, image), "quote_hash": q,
            "image_basename": image, "inclusion_reason": reason, "source_subset": source})
    add(EVEREST_QUOTE_HASH, EVEREST_IMAGE, "forced_everest_first_impression_failure", "new_case_study")
    add(*FREE_TRADE_KEY, "forced_free_trade_indirect_case", "fixed_regression")
    labelled = []
    for cid, label in human.items():
        if cid in by_id:
            labelled.append((label.get("human_action", "unsure"), -(dis.get(cid, {}).get("disagreement_score") or 0), cid, by_id[cid]))
    for action, _score, cid, row in sorted(labelled):
        target = {"keep": 15, "replace": 25, "unsure": 4}.get(action, 0)
        if sum(x["inclusion_reason"] == f"human_{action}" for x in selected) < target:
            add(row["quote_hash"], row["image_basename"], f"human_{action}")
    for row in sorted(by_id.values(), key=lambda x: (-(dis.get(x["case_id"], {}).get("disagreement_score") or 0), x["case_id"])):
        add(row["quote_hash"], row["image_basename"], "high_disagreement_fill")
    if len(selected) != limit: raise ValueError(f"could select only {len(selected)} validation cases")
    return {"schema_version": 1, "analysis_kind": "first_impression_validation_cases",
        "case_count": limit, "created_at": datetime.now(timezone.utc).isoformat(), "items": selected}


def pending_images(cases: list[dict[str, Any]], inventory: dict[str, dict[str, Any]], db: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the pending images."""
    result=[]
    for basename in sorted({row["image_basename"] for row in cases}):
        source=inventory[basename];cached=db.get("items",{}).get(basename)
        if not isinstance(cached,dict) or cached.get("sha256")!=source["sha256"]:
            result.append(source)
    return result


def preflight(cases: list[dict[str, Any]], intents: dict[str, Any], images: dict[str, Any], *, vision_calls: int) -> dict[str, Any]:
    """Build the deterministic execution preflight."""
    prompts=[critic_prompt(intents[row["quote_hash"]],images[row["image_basename"]]) for row in cases if row["image_basename"] in images]
    tokens=sum(estimate_tokens(p) for p in prompts) if len(prompts)==len(cases) else 2500*len(cases)
    calls=len(cases);ceilings={"grok":3.0,"openai":5.0,"anthropic":3.0,"gemini":3.0};providers={}
    for provider in ceilings:
        expected=tokens*PRICES[provider]["input"]/1e6+calls*700*PRICES[provider]["output"]/1e6
        maximum=tokens*PRICES[provider]["input"]/1e6+calls*1200*PRICES[provider]["output"]/1e6
        providers[provider]={"model":PROVIDER_MODELS[provider],"calls":calls,"estimated_input_tokens":tokens,
            "estimated_output_tokens":calls*700,"expected_cost_usd":expected,"conservative_maximum_cost_usd":maximum,
            "hard_ceiling_usd":ceilings[provider],"tools_enabled":False}
    vision_cost=vision_calls*((500+4100)*2+1000*6)/1e6
    return {"schema_version":1,"validation_cases":len(cases),"vision_calls":vision_calls,
        "vision_expected_cost_usd":vision_cost,"providers":providers,
        "combined_expected_cost_usd":vision_cost+sum(x["expected_cost_usd"] for x in providers.values()),
        "combined_hard_ceiling_usd":14.0}


def strategy_comparison(cases: list[dict[str, Any]], alignments: dict[str, Any], human: dict[str, Any]) -> dict[str, Any]:
    """Return the strategy comparison."""
    rows={name:{"winner_changes":0,"resolved":0,"human_agreement":0,"false_keeps":0,"false_replaces":0} for name in "ABCDE"}
    for case in cases:
        cid=case["case_id"];result=alignments.get(cid);label=human.get(cid,{}).get("human_action")
        if not result:continue
        base="keep";score=result["dominant_visual_message_alignment_score"];tone=result["tone_alignment_score"]
        actions={"A":base,"B":"replace" if score<40 else base,"C":"replace" if score<50 and result["salience_interference_score"]>70 else base,"D":"replace" if tone<40 else base,"E":"defer"}
        for name,action in actions.items():
            rows[name]["resolved"]+=action in {"keep","replace"};rows[name]["winner_changes"]+=action=="replace"
            if label in {"keep","replace"} and action in {"keep","replace"}:
                rows[name]["human_agreement"]+=action==label;rows[name]["false_keeps"]+=action=="keep" and label=="replace";rows[name]["false_replaces"]+=action=="replace" and label=="keep"
    return {"schema_version":1,"observational_only":True,"strategies":rows,
        "limitations":["Validation-set counterfactual only; generated share, exhaustion and diversity require candidate traces not available here."]}


class StructuredProviderAdapter:
    """Represent structured provider adapter data."""
    def __init__(self, client: ProviderClient, schema: dict[str, Any], schema_name: str, max_output_tokens: int = 1200):
        """Initialise the structured provider adapter."""
        self.client=client;self.model=client.model;self.schema=schema;self.schema_name=schema_name;self.max_output_tokens=max_output_tokens
    def payload(self,prompt):
        """Return the payload."""
        return self.client.payload(prompt,schema=self.schema,schema_name=self.schema_name,max_output_tokens=self.max_output_tokens)
    def call(self,prompt):
        """Submit one prompt through the structured provider adapter."""
        return self.client.call(prompt,schema=self.schema,schema_name=self.schema_name,max_output_tokens=self.max_output_tokens)


class AlignmentWorker:
    """Run alignment operations."""
    def __init__(self,provider:str,cases:list[dict[str,Any]],intents:dict[str,Any],images:dict[str,Any],run_dir:Path,client:StructuredProviderAdapter,limit:float,sleep=time.sleep):
        """Initialise the alignment worker."""
        self.provider=provider;self.cases=cases;self.intents=intents;self.images=images;self.run_dir=run_dir;self.client=client;self.limit=limit;self.sleep=sleep
        self.results_path=run_dir/f'{provider}_first_impression_results.json';self.ledger_path=run_dir/f'{provider}_first_impression_ledger.json'
    def run(self):
        """Run the assigned first-impression analysis jobs."""
        results=json.loads(self.results_path.read_text()) if self.results_path.exists() else {"schema_version":1,"provider":self.provider,"model":self.client.model,"prompt_version":CRITIC_PROMPT_VERSION,"items":{},"failures":{}}
        ledger=json.loads(self.ledger_path.read_text()) if self.ledger_path.exists() else {"schema_version":1,"provider":self.provider,"attempts":[],"calls":[],"ambiguous":[],"known_cost_usd":0.0}
        for case in self.cases:
            cid=case['case_id']
            if cid in results['items'] or any(x['case_id']==cid and x['state']=='ambiguous' for x in ledger['attempts']):continue
            prompt=critic_prompt(self.intents[case['quote_hash']],self.images[case['image_basename']]);prior=[x for x in ledger['attempts'] if x['case_id']==cid]
            while len(prior)<2 and cid not in results['items']:
                maximum=estimate_tokens(prompt)*PRICES[self.provider]['input']/1e6+1200*PRICES[self.provider]['output']/1e6
                if ledger['known_cost_usd']+maximum>self.limit:raise RuntimeError(f'{self.provider} first-impression ceiling reached')
                attempt={"case_id":cid,"attempt_number":len(prior)+1,"state":"prepared","input_hash":hashlib.sha256(prompt.encode()).hexdigest(),"timestamp":time.time()};ledger['attempts'].append(attempt);atomic_write_json(self.ledger_path,ledger);attempt['state']='sending';atomic_write_json(self.ledger_path,ledger)
                try:response=self.client.call(prompt)
                except requests.HTTPError as exc:
                    attempt['state']='confirmed_failure';attempt['http_status']=exc.response.status_code if exc.response is not None else None;atomic_write_json(self.ledger_path,ledger);prior=[x for x in ledger['attempts'] if x['case_id']==cid]
                    if len(prior)<2:self.sleep(2);continue
                    break
                except BaseException as exc:
                    attempt['state']='ambiguous';attempt['error']=f'{type(exc).__name__}: {exc}';ledger['ambiguous'].append({"case_id":cid,"attempt_number":attempt['attempt_number']});atomic_write_json(self.ledger_path,ledger);break
                call={"case_id":cid,"attempt_number":attempt['attempt_number'],"request_id":response.get('request_id'),"cost_usd":response['cost_usd'],"latency_seconds":response['latency_seconds'],**response['usage']};ledger['calls'].append(call);ledger['known_cost_usd']+=response['cost_usd']
                try:value=validate_alignment(response['content'])
                except ValueError as exc:
                    attempt['state']='confirmed_failure';attempt['schema_error']=str(exc);atomic_write_json(self.ledger_path,ledger);prior=[x for x in ledger['attempts'] if x['case_id']==cid];continue
                attempt['state']='completed';results['items'][cid]={"schema_version":1,"analysis_kind":"first_impression_alignment","case_id":cid,"quote_hash":case['quote_hash'],"image_basename":case['image_basename'],**value,"provider":self.provider,"model":self.client.model,"prompt_version":CRITIC_PROMPT_VERSION};atomic_write_json(self.results_path,results);atomic_write_json(self.ledger_path,ledger);break
            if cid not in results['items']:results['failures'][cid]={"attempts":len([x for x in ledger['attempts'] if x['case_id']==cid])};atomic_write_json(self.results_path,results)
        return {"provider":self.provider,"completed":len(results['items']),"failed":len(results['failures']),"cost_usd":ledger['known_cost_usd']}


def run_alignment_workers(workers:list[AlignmentWorker])->dict[str,Any]:
    """Run alignment workers."""
    from concurrent.futures import ThreadPoolExecutor,as_completed
    summaries={}
    with ThreadPoolExecutor(max_workers=len(workers),thread_name_prefix='first_impression') as pool:
        futures={pool.submit(worker.run):worker.provider for worker in workers}
        for future in as_completed(futures):
            provider=futures[future]
            try:summaries[provider]=future.result()
            except BaseException as exc:summaries[provider]={"provider":provider,"worker_error":f'{type(exc).__name__}: {exc}'}
    return summaries


def pairwise_cases(cases:list[dict[str,Any]],*,limit:int=12)->list[dict[str,Any]]:
    """Return the pairwise cases."""
    grouped=defaultdict(list)
    for case in cases:grouped[case['quote_hash']].append(case)
    rows=[]
    for quote_hash,items in sorted(grouped.items()):
        if len(items)<2:continue
        ordered=sorted(items,key=lambda x:x['case_id'])[:2]
        rows.append({"pair_id":hashlib.sha256(f"{quote_hash}:{ordered[0]['image_basename']}:{ordered[1]['image_basename']}".encode()).hexdigest()[:20],"quote_hash":quote_hash,"candidate_a":ordered[0]['image_basename'],"candidate_b":ordered[1]['image_basename']})
        if len(rows)>=limit:break
    return rows
