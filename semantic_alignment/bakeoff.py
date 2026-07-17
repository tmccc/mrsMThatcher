from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

import requests

from .calibration import RELATIONSHIPS, decompose_image, decompose_quote, spearman
from .io import atomic_write_json, read_json
from .prompts import CRITIC_ALLOWED_IMAGE, CRITIC_ALLOWED_QUOTE

BAKEOFF_SCHEMA_VERSION = 1
BAKEOFF_PROMPT_VERSION = "provider-neutral-picture-editor-v1"
FREE_TRADE_KEY = ("1ae9443573e42259af54c30a0ec90a6a8746e640b09e53ae1c28a4c0a2d0ed6b", "tg_661b01c39a8d223df51cd0365e79ffe7e3c4f86ac81fa0af114ce95be49cb831.png")
PROVIDER_MODELS = {"grok": "grok-4.5", "openai": "gpt-5.6-sol", "anthropic": "claude-sonnet-4-6", "gemini": "gemini-3.1-pro-preview"}
PRICES = {
    "grok": {"input": 2.0, "cached_input": 0.5, "output": 6.0},
    "openai": {"input": 5.0, "cached_input": 0.5, "output": 30.0},
    "anthropic": {"input": 3.0, "cached_input": 0.30, "output": 15.0},
    "gemini": {"input": 2.0, "cached_input": 0.20, "output": 12.0},
}
PROVIDER_CEILINGS = {"grok": 0.75, "openai": 1.75, "anthropic": 1.50, "gemini": 1.50}
COMBINED_CEILING = 3.0
MAX_OUTPUT_TOKENS = 1600
SCORE_FIELDS = ("relevance_score", "directness_score", "mechanism_alignment_score",
                "consequence_alignment_score", "principle_alignment_score",
                "specificity_score", "editorial_power_score", "overall_suitability_score")
STRING_LIST = {"type": "array", "items": {"type": "string", "maxLength": 300}, "maxItems": 16}
BAKEOFF_OUTPUT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "primary_relationship": {"type": "string", "enum": list(RELATIONSHIPS)},
        "secondary_relationship": {"anyOf": [{"type": "string", "enum": list(RELATIONSHIPS)}, {"type": "null"}]},
        **{field: {"type": "number", "minimum": 0, "maximum": 100} for field in SCORE_FIELDS},
        "keep_or_replace": {"type": "string", "enum": ["keep", "replace", "unsure"]},
        "quote_mechanism": STRING_LIST, "quote_claimed_consequences": STRING_LIST,
        "quote_broader_principles": STRING_LIST, "image_depicted_subject": STRING_LIST,
        "image_implied_mechanism": STRING_LIST, "image_depicted_consequences": STRING_LIST,
        "image_ideological_framing": STRING_LIST, "matched_elements": STRING_LIST,
        "unillustrated_primary_elements": STRING_LIST, "extraneous_image_arguments": STRING_LIST,
        "explanation": {"type": "string", "minLength": 1, "maxLength": 1600},
        "stronger_visual_direction": STRING_LIST,
    },
    "required": ["primary_relationship", "secondary_relationship", *SCORE_FIELDS,
                 "keep_or_replace", "quote_mechanism", "quote_claimed_consequences",
                 "quote_broader_principles", "image_depicted_subject",
                 "image_implied_mechanism", "image_depicted_consequences",
                 "image_ideological_framing", "matched_elements",
                 "unillustrated_primary_elements", "extraneous_image_arguments",
                 "explanation", "stronger_visual_direction"],
}


def _without_schema_keywords(schema:dict[str,Any], unsupported:set[str]) -> dict[str,Any]:
    def clean(value):
        if isinstance(value,dict): return {k:clean(v) for k,v in value.items() if k not in unsupported}
        if isinstance(value,list): return [clean(v) for v in value]
        return value
    return clean(schema)


def openai_output_schema(schema:dict[str,Any]=BAKEOFF_OUTPUT_SCHEMA) -> dict[str,Any]:
    # OpenAI's strict structured-output subset rejects uniqueItems. Duplicate
    # list members remain prohibited by provider-neutral local validation.
    return _without_schema_keywords(schema,{"uniqueItems"})


def anthropic_output_schema(schema:dict[str,Any]=BAKEOFF_OUTPUT_SCHEMA) -> dict[str,Any]:
    # Anthropic structured outputs reject numeric ranges and array minimums
    # above one; local strict validation retains those provider-neutral rules.
    return _without_schema_keywords(schema,{"minimum","maximum","minItems","maxItems","maxLength","uniqueItems"})


def validate_bakeoff_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(BAKEOFF_OUTPUT_SCHEMA["required"]):
        raise ValueError("bake-off result fields do not match schema")
    if value["primary_relationship"] not in RELATIONSHIPS: raise ValueError("invalid primary relationship")
    if value["secondary_relationship"] is not None and value["secondary_relationship"] not in RELATIONSHIPS: raise ValueError("invalid secondary relationship")
    for field in SCORE_FIELDS:
        score=value[field]
        if isinstance(score,bool) or not isinstance(score,(int,float)) or not math.isfinite(float(score)) or not 0 <= float(score) <= 100: raise ValueError(f"invalid {field}")
    if value["keep_or_replace"] not in {"keep","replace","unsure"}: raise ValueError("invalid keep_or_replace")
    for field in ("quote_mechanism","quote_claimed_consequences","quote_broader_principles","image_depicted_subject","image_implied_mechanism","image_depicted_consequences","image_ideological_framing","matched_elements","unillustrated_primary_elements","extraneous_image_arguments","stronger_visual_direction"):
        if not isinstance(value[field],list) or any(type(item) is not str for item in value[field]): raise ValueError(f"invalid {field}")
    if type(value["explanation"]) is not str or not value["explanation"].strip(): raise ValueError("invalid explanation")
    return dict(value)


def common_prompt(quote: dict[str, Any], image: dict[str, Any]) -> str:
    # Deliberately no local decomposition, prior critic, human label or selection metadata.
    clean_quote={key:quote[key] for key in sorted(CRITIC_ALLOWED_QUOTE) if key in quote}
    clean_image={key:image[key] for key in sorted(CRITIC_ALLOWED_IMAGE) if key in image}
    payload={"quote_fingerprint":clean_quote,"image_fingerprint":clean_image}
    return f"""Prompt version: {BAKEOFF_PROMPT_VERSION}
Act as an experienced newspaper picture editor. Compare the two independent semantic
fingerprints below. Judge the pairing as a published post with the quotation alongside
the image. Do not infer a generation prompt, origin relationship, production status,
historical score, engagement, expected answer, or another critic's judgement.

Distinguish the quotation's mechanism, claimed consequences and broader principles
from the image's depicted subject, implied mechanism, depicted consequences and
ideological framing. A picture may be relevant because it illustrates a claimed
consequence even when it does not depict the causal mechanism. Separately identify
ideological substitution, extraneous arguments and missing primary elements.

Choose primary and optional secondary relationships only from: {', '.join(RELATIONSHIPS)}.
Score each requested dimension independently from 0 to 100. Overall suitability is an
editorial decision, not an arithmetic average. Choose keep, replace or unsure without
assuming that an alternative image is guaranteed to be better. Be concise and return
only the requested structured JSON. No tools are available.

FINGERPRINTS:
{json.dumps(payload,sort_keys=True,separators=(',',':'))}"""


def _tokens(text: str) -> set[str]:
    return {word for word in ''.join(ch.lower() if ch.isalnum() else ' ' for ch in text).split() if len(word)>4}


def select_cases(old_results: list[dict[str, Any]], quotes: dict[str, Any], images: dict[str, Any], *, limit: int = 25) -> dict[str, Any]:
    by_key={(row["quote_hash"],row["image_basename"]):row for row in old_results}
    selected=[]; used=set()
    def take(candidates,count,reason):
        for row in candidates:
            if len(selected)>=limit: return
            key=(row["quote_hash"],row["image_basename"])
            if key not in used and len([x for x in selected if x["selection_reason"]==reason])<count:
                used.add(key); selected.append({"case_id":hashlib.sha256(f"{key[0]}:{key[1]}".encode()).hexdigest()[:20],"quote_hash":key[0],"image_basename":key[1],"selection_reason":reason})
    free=by_key.get(FREE_TRADE_KEY)
    if not free: raise ValueError("free-trade case missing")
    take([free],1,"forced_free_trade_consequence_case")
    unrelated=sorted([r for r in old_results if r["claim_relationship"]=="unrelated"],key=lambda r:(-r["editorial_power_score"],r["quote_hash"],r["image_basename"]))
    take(unrelated,8,"old_grok_unrelated")
    consequence=[]
    for row in old_results:
        q=decompose_quote(quotes[row["quote_hash"]]); i=decompose_image(images[row["image_basename"]])
        overlap=_tokens(' '.join(q["claimed_consequences"]))&_tokens(' '.join(i["depicted_consequence"]))
        if overlap: consequence.append((len(overlap),row))
    consequence_order=[row for _,row in sorted(consequence,key=lambda x:(-x[0],x[1]["quote_hash"]))]
    consequence_order += sorted([r for r in old_results if r["claim_relationship"]=="related_but_not_equivalent"],key=lambda r:(-r["semantic_alignment_score"],r["quote_hash"]))
    take(consequence_order,4,"likely_consequence_only")
    ideological=sorted([r for r in old_results if r["claim_relationship"]=="generic_ideological_substitution"],key=lambda r:(-r["editorial_power_score"],r["quote_hash"]))
    take(ideological,4,"old_grok_ideological_substitution")
    strong=sorted(old_results,key=lambda r:(-r["semantic_alignment_score"],-r["directness_score"],r["quote_hash"]))
    take(strong,4,"highest_old_alignment")
    ambiguous=sorted([r for r in old_results if images[r["image_basename"]].get("ambiguity") in {"medium","high"}],key=lambda r:(abs(r["semantic_alignment_score"]-40),r["quote_hash"]))
    take(ambiguous,2,"image_fingerprint_ambiguous")
    poor=sorted([r for r in old_results if r["claim_relationship"]=="contradiction"],key=lambda r:(r["semantic_alignment_score"],r["quote_hash"]))+sorted(old_results,key=lambda r:(r["semantic_alignment_score"],r["quote_hash"]))
    take(poor,2,"contradictory_or_poor")
    if len(selected)<limit: take(strong,limit-len(selected),"deterministic_fill")
    if len(selected)!=limit: raise ValueError(f"could select only {len(selected)} cases")
    return {"schema_version":1,"analysis_kind":"provider_critic_bakeoff_cases","case_count":limit,"selection_before_provider_results":True,"items":selected}


def estimate_tokens(prompt: str) -> int:
    return math.ceil(len(prompt.encode("utf-8"))/3)  # Conservative without provider tokenizer.


def preflight(cases: list[dict[str,Any]], quotes: dict[str,Any], images: dict[str,Any]) -> dict[str,Any]:
    prompts=[common_prompt(quotes[x["quote_hash"]],images[x["image_basename"]]) for x in cases]
    input_tokens=sum(estimate_tokens(prompt) for prompt in prompts)
    rows={}
    for provider in ("grok","openai","anthropic","gemini"):
        price=PRICES[provider]; expected_output=900*len(prompts); max_output=MAX_OUTPUT_TOKENS*len(prompts)
        expected=input_tokens*price["input"]/1e6+expected_output*price["output"]/1e6
        maximum=input_tokens*price["input"]/1e6+max_output*price["output"]/1e6
        rows[provider]={"model":PROVIDER_MODELS[provider],"calls":len(prompts),"estimated_input_tokens":input_tokens,"estimated_output_tokens":expected_output,"maximum_output_tokens":max_output,"expected_cost_usd":expected,"conservative_maximum_cost_usd":maximum,"hard_ceiling_usd":PROVIDER_CEILINGS[provider],"reasoning_effort":"low","tools_enabled":False}
    return {"schema_version":1,"prompt_version":BAKEOFF_PROMPT_VERSION,"providers":rows,"combined_expected_cost_usd":sum(x["expected_cost_usd"] for x in rows.values()),"combined_conservative_maximum_cost_usd":sum(x["conservative_maximum_cost_usd"] for x in rows.values()),"combined_hard_ceiling_usd":COMBINED_CEILING}


class ProviderLedger:
    def __init__(self,path:Path,provider:str):
        self.path=path; self.provider=provider; self.data=read_json(path,None) or {"schema_version":1,"provider":provider,"model":PROVIDER_MODELS[provider],"status":"resumable","blocked":False,"calls":[],"attempts":[],"ambiguous_requests":[],"total_cost_usd":0}
        if self.data.get("blocked"): raise RuntimeError(f"{provider} ledger blocked: {self.data.get('blocked_reason')}")
        if not path.exists(): atomic_write_json(path,self.data)
    def cost(self): return sum(float(row["cost_usd"]) for row in self.data["calls"])
    def guard(self,prompt:str,limit:float):
        maximum=estimate_tokens(prompt)*PRICES[self.provider]["input"]/1e6+MAX_OUTPUT_TOKENS*PRICES[self.provider]["output"]/1e6
        if self.cost()+maximum>min(limit,PROVIDER_CEILINGS[self.provider]): raise RuntimeError(f"next {self.provider} call could exceed provider ceiling")
    def begin(self,case:dict[str,Any],prompt:str):
        attempts=self.data.setdefault("attempts",[]); row={"run_id":"provider_bakeoff_25_20260712","provider":self.provider,"case_id":case["case_id"],"quote_hash":case["quote_hash"],"image_basename":case["image_basename"],"request_sequence":len(attempts)+1,"attempt_number":1,"model":PROVIDER_MODELS[self.provider],"prompt_version":BAKEOFF_PROMPT_VERSION,"schema_version":BAKEOFF_SCHEMA_VERSION,"request_timestamp":time.time(),"input_hash":hashlib.sha256(prompt.encode()).hexdigest(),"lifecycle_state":"prepared"}; attempts.append(row); atomic_write_json(self.path,self.data); return row
    def block(self,item_key:str,error:BaseException,request_id:str|None=None,response_received:bool=False):
        self.data["ambiguous_requests"].append({"item_key":item_key,"timestamp":time.time(),"error":f"{type(error).__name__}: {error}","request_id":request_id,"response_received":response_received}); self.data.update({"blocked":True,"status":"blocked_ambiguous_cost","blocked_reason":"ambiguous request outcome"}); atomic_write_json(self.path,self.data)
    def confirmed_failure(self,item_key:str,error:BaseException):
        response=getattr(error,"response",None); self.data.setdefault("confirmed_failures",[]).append({"item_key":item_key,"timestamp":time.time(),"error":f"{type(error).__name__}: {error}","request_id":response.headers.get("request-id") if response is not None else None,"http_status":response.status_code if response is not None else None}); atomic_write_json(self.path,self.data)
    def record(self,row:dict[str,Any]):
        if any(x["case_id"]==row["case_id"] for x in self.data["calls"]): return
        self.data["calls"].append(row); self.data["total_cost_usd"]=self.cost(); self.data["updated_at"]=time.time(); atomic_write_json(self.path,self.data)


class ProviderClient:
    def __init__(self,provider:str,api_key:str,transport:Callable[...,Any]|None=None,*,timeout_seconds:float=180):
        if provider not in {"grok","openai","anthropic","gemini"}: raise ValueError("unknown provider")
        if not api_key: raise RuntimeError(f"{provider} API key required only for explicit execution")
        self.provider=provider; self.model=PROVIDER_MODELS[provider]; self.transport=transport or requests.post; self.api_key=api_key; self.timeout_seconds=float(timeout_seconds)
    def payload(self,prompt:str,*,schema:dict[str,Any]=BAKEOFF_OUTPUT_SCHEMA,schema_name:str="provider_neutral_picture_editor",max_output_tokens:int=MAX_OUTPUT_TOKENS):
        if self.provider=="grok":
            return {"model":self.model,"messages":[{"role":"user","content":prompt}],"response_format":{"type":"json_schema","json_schema":{"name":schema_name,"strict":True,"schema":schema}},"reasoning_effort":"low","max_tokens":max_output_tokens}
        if self.provider=="anthropic":
            return {"model":self.model,"messages":[{"role":"user","content":prompt}],"max_tokens":max_output_tokens,"output_config":{"effort":"low","format":{"type":"json_schema","schema":anthropic_output_schema(schema)}}}
        if self.provider=="gemini":
            return {"contents":[{"role":"user","parts":[{"text":prompt}]}],"generationConfig":{"responseMimeType":"application/json","responseJsonSchema":schema,"maxOutputTokens":max_output_tokens,"thinkingConfig":{"thinkingBudget":512}}}
        return {"model":self.model,"input":prompt,"reasoning":{"effort":"low"},"text":{"format":{"type":"json_schema","name":schema_name,"strict":True,"schema":openai_output_schema(schema)}},"max_output_tokens":max_output_tokens,"store":False}
    def call(self,prompt:str,*,schema:dict[str,Any]=BAKEOFF_OUTPUT_SCHEMA,schema_name:str="provider_neutral_picture_editor",max_output_tokens:int=MAX_OUTPUT_TOKENS):
        urls={"grok":"https://api.x.ai/v1/chat/completions","openai":"https://api.openai.com/v1/responses","anthropic":"https://api.anthropic.com/v1/messages","gemini":f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"}
        headers={"Content-Type":"application/json"}
        if self.provider=="anthropic": headers.update({"x-api-key":self.api_key,"anthropic-version":"2023-06-01"})
        elif self.provider=="gemini": headers["x-goog-api-key"]=self.api_key
        else: headers["Authorization"]=f"Bearer {self.api_key}"
        started=time.monotonic(); response=self.transport(urls[self.provider],headers=headers,json=self.payload(prompt,schema=schema,schema_name=schema_name,max_output_tokens=max_output_tokens),timeout=self.timeout_seconds); latency=time.monotonic()-started; response.raise_for_status(); raw=response.json(); request_id=response.headers.get("request-id") or response.headers.get("x-request-id") or raw.get("id")
        if self.provider=="grok":
            content=json.loads(raw["choices"][0]["message"]["content"]); usage=raw.get("usage") or {}; ticks=usage.get("cost_in_usd_ticks")
            if type(ticks) is not int: raise ValueError("missing authoritative Grok cost")
            cost=ticks/10_000_000_000; input_tokens=int(usage.get("prompt_tokens") or 0); output_tokens=int(usage.get("completion_tokens") or 0); cached=int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0); reasoning=int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
        elif self.provider=="openai":
            usage=raw.get("usage") or {}; input_tokens=int(usage.get("input_tokens") or 0); output_tokens=int(usage.get("output_tokens") or 0); cached=int((usage.get("input_tokens_details") or {}).get("cached_tokens") or 0); reasoning=int((usage.get("output_tokens_details") or {}).get("reasoning_tokens") or 0)
            if not usage or not input_tokens and not output_tokens: raise ValueError("missing authoritative OpenAI usage")
            cost=(input_tokens-cached)*PRICES["openai"]["input"]/1e6+cached*PRICES["openai"]["cached_input"]/1e6+output_tokens*PRICES["openai"]["output"]/1e6
            text=raw.get("output_text")
            if not text:
                text=''.join(part.get("text","") for item in raw.get("output",[]) for part in item.get("content",[]) if part.get("type")=="output_text")
            content=json.loads(text)
        elif self.provider=="anthropic":
            usage=raw.get("usage") or {}; input_tokens=int(usage.get("input_tokens") or 0); output_tokens=int(usage.get("output_tokens") or 0); cached=int(usage.get("cache_read_input_tokens") or 0); reasoning=int((usage.get("output_tokens_details") or {}).get("thinking_tokens") or 0)
            if not usage or not input_tokens and not output_tokens: raise ValueError("missing authoritative Anthropic usage")
            cost=(input_tokens-cached)*PRICES["anthropic"]["input"]/1e6+cached*PRICES["anthropic"]["cached_input"]/1e6+output_tokens*PRICES["anthropic"]["output"]/1e6
            blocks=raw.get("content") or []; text=''.join(block.get("text","") for block in blocks if block.get("type")=="text"); content=json.loads(text)
        else:
            usage=raw.get("usageMetadata") or {}; input_tokens=int(usage.get("promptTokenCount") or 0); completion=int(usage.get("candidatesTokenCount") or 0); reasoning=int(usage.get("thoughtsTokenCount") or 0); output_tokens=completion+reasoning; cached=int(usage.get("cachedContentTokenCount") or 0)
            if not usage or not input_tokens and not output_tokens: raise ValueError("missing authoritative Gemini usage")
            cost=(input_tokens-cached)*PRICES["gemini"]["input"]/1e6+cached*PRICES["gemini"]["cached_input"]/1e6+output_tokens*PRICES["gemini"]["output"]/1e6
            text=''.join(part.get("text","") for candidate in raw.get("candidates",[]) for part in (candidate.get("content") or {}).get("parts",[])); content=json.loads(text)
        return {"content":content,"usage":{"input_tokens":input_tokens,"cached_tokens":cached,"reasoning_tokens":reasoning,"output_tokens":output_tokens},"cost_usd":cost,"latency_seconds":latency,"request_id":request_id,"raw":raw}


def run_provider(provider:str,cases:list[dict[str,Any]],quotes:dict[str,Any],images:dict[str,Any],output_dir:Path,client:ProviderClient,confirmed_limit:float):
    output=output_dir/f"{provider}_results.json"; db=read_json(output,None) or {"schema_version":1,"provider":provider,"model":client.model,"prompt_version":BAKEOFF_PROMPT_VERSION,"items":{},"failures":{}}
    ledger=ProviderLedger(output_dir/f"{provider}_cost_ledger.json",provider)
    for case in cases:
        key=case["case_id"]
        if key in db["items"]: continue
        prompt=common_prompt(quotes[case["quote_hash"]],images[case["image_basename"]]); ledger.guard(prompt,confirmed_limit); attempt=ledger.begin(case,prompt)
        try: result=client.call(prompt)
        except requests.HTTPError as exc:
            if exc.response is not None and 400 <= exc.response.status_code < 500: ledger.confirmed_failure(key,exc)
            else: ledger.block(key,exc)
            raise
        except BaseException as exc: ledger.block(key,exc); raise
        call={"case_id":key,"request_id":result["request_id"],"timestamp":time.time(),"model":client.model,"latency_seconds":result["latency_seconds"],"cost_usd":result["cost_usd"],"input_hash":attempt["input_hash"],**result["usage"],"retry_count":0}; ledger.record(call)
        try: validated=validate_bakeoff_result(result["content"])
        except ValueError as exc: db["failures"][key]={"error":str(exc),"charged_call":call}; atomic_write_json(output,db); raise
        db["items"][key]={"case_id":key,"quote_hash":case["quote_hash"],"image_basename":case["image_basename"],**validated,"model":client.model}; atomic_write_json(output,db)
    return db


def create_blinding(output_dir:Path):
    path=output_dir/"sealed_provider_mapping.json"
    if path.exists(): return read_json(path)
    providers=["grok","openai"]; secrets.SystemRandom().shuffle(providers); mapping={"Critic A":providers[0],"Critic B":providers[1]}; atomic_write_json(path,mapping); os.chmod(path,0o600); return mapping


def create_three_way_blinding(output_dir:Path):
    path=output_dir/"sealed_provider_mapping_three_way.json"
    if path.exists(): return read_json(path)
    providers=["grok","openai","anthropic"]; secrets.SystemRandom().shuffle(providers)
    mapping={label:provider for label,provider in zip(("Critic A","Critic B","Critic C"),providers)}
    atomic_write_json(path,mapping); os.chmod(path,0o600); return mapping


def create_four_way_blinding(output_dir:Path):
    path=output_dir/"sealed_provider_mapping_four_way.json"
    if path.exists(): return read_json(path)
    providers=["grok","openai","anthropic","gemini"]; secrets.SystemRandom().shuffle(providers)
    mapping={label:provider for label,provider in zip(("Critic A","Critic B","Critic C","Critic D"),providers)}
    atomic_write_json(path,mapping); os.chmod(path,0o600); return mapping


def confusion(left:list[str],right:list[str]):
    labels=sorted(set(left)|set(right)); matrix={a:{b:0 for b in labels} for a in labels}
    for a,b in zip(left,right): matrix[a][b]+=1
    return matrix


def cohens_kappa(left:list[str],right:list[str]):
    if not left or len(left)!=len(right): return None
    observed=sum(a==b for a,b in zip(left,right))/len(left); lc,rc=Counter(left),Counter(right); expected=sum(lc[x]*rc[x] for x in set(lc)|set(rc))/(len(left)**2)
    return (observed-expected)/(1-expected) if expected<1 else 1.0


def compare_results(cases:list[dict[str,Any]],grok:dict[str,Any],openai:dict[str,Any]):
    pairs=[(case,grok["items"][case["case_id"]],openai["items"][case["case_id"]]) for case in cases if case["case_id"] in grok["items"] and case["case_id"] in openai["items"]]
    gp=[g["primary_relationship"] for _,g,_ in pairs]; op=[o["primary_relationship"] for _,_,o in pairs]
    scores={}
    for field in SCORE_FIELDS:
        gv=[float(g[field]) for _,g,_ in pairs]; ov=[float(o[field]) for _,_,o in pairs]
        scores[field]={"grok_mean":statistics.mean(gv),"grok_median":statistics.median(gv),"grok_stddev":statistics.pstdev(gv),"openai_mean":statistics.mean(ov),"openai_median":statistics.median(ov),"openai_stddev":statistics.pstdev(ov),"mean_absolute_difference":statistics.mean(abs(a-b) for a,b in zip(gv,ov)),"spearman":spearman(gv,ov),"largest_disagreements":sorted([{"case_id":case["case_id"],"grok":g[field],"openai":o[field],"absolute_difference":abs(g[field]-o[field])} for case,g,o in pairs],key=lambda x:(-x["absolute_difference"],x["case_id"]))[:5]}
    consequence=[]
    for case,g,o in pairs:
        if g["primary_relationship"]=="unrelated" and o["primary_relationship"]=="illustrates_claimed_consequence": consequence.append({"case_id":case["case_id"],"pattern":"grok_unrelated_openai_consequence"})
        for provider,row in (("grok",g),("openai",o)):
            if row["consequence_alignment_score"]>=50 and row["mechanism_alignment_score"]<40: consequence.append({"case_id":case["case_id"],"pattern":f"{provider}_outcome_without_mechanism"})
    explanation={}
    for provider,index in (("grok",1),("openai",2)):
        rows=[pair[index] for pair in pairs]; texts=[row["explanation"] for row in rows]
        explanation[provider]={"mean_characters":statistics.mean(map(len,texts)),"unique_explanations":len(set(texts)),"schema_complete":len(rows),"classification_score_contradictions":sum((row["primary_relationship"]=="unrelated" and row["relevance_score"]>=60) or (row["primary_relationship"] in {"direct_illustration","illustrates_mechanism"} and row["directness_score"]<30) for row in rows),"missing_quote_mechanism":sum(not row["quote_mechanism"] for row in rows),"missing_consequences":sum(not row["quote_claimed_consequences"] for row in rows)}
    return {"schema_version":1,"paired_cases":len(pairs),"primary_exact_agreement":sum(a==b for a,b in zip(gp,op))/len(pairs) if pairs else None,"primary_or_secondary_overlap":sum(bool({g["primary_relationship"],g["secondary_relationship"]}-{None} & ({o["primary_relationship"],o["secondary_relationship"]}-{None})) for _,g,o in pairs)/len(pairs) if pairs else None,"cohens_kappa":cohens_kappa(gp,op),"confusion_matrix":confusion(gp,op),"category_counts":{"grok":dict(Counter(gp)),"openai":dict(Counter(op))},"unrelated_counts":{"grok":gp.count("unrelated"),"openai":op.count("unrelated")},"consequence_counts":{"grok":gp.count("illustrates_claimed_consequence"),"openai":op.count("illustrates_claimed_consequence")},"ideological_substitution_counts":{"grok":gp.count("related_ideological_substitution"),"openai":op.count("related_ideological_substitution")},"keep_replace":{"agreement":sum(g["keep_or_replace"]==o["keep_or_replace"] for _,g,o in pairs)/len(pairs) if pairs else None,"grok":dict(Counter(g["keep_or_replace"] for _,g,_ in pairs)),"openai":dict(Counter(o["keep_or_replace"] for _,_,o in pairs))},"scores":scores,"consequence_recognition_events":consequence,"explanation_checks":explanation,"provisional_recommendation":"Insufficient human evidence"}


def compare_n_results(cases:list[dict[str,Any]], results:dict[str,dict[str,Any]]) -> dict[str,Any]:
    providers=sorted(results); ids=[case["case_id"] for case in cases if all(case["case_id"] in results[p]["items"] for p in providers)]
    summaries={}
    for provider in providers:
        rows=[results[provider]["items"][key] for key in ids]; primary=[row["primary_relationship"] for row in rows]
        summaries[provider]={"model":results[provider].get("model",PROVIDER_MODELS[provider]),"cases":len(rows),"primary_relationships":dict(Counter(primary)),"secondary_relationships":dict(Counter(str(row["secondary_relationship"]) for row in rows)),"scores":{field:{"mean":statistics.mean(float(row[field]) for row in rows),"median":statistics.median(float(row[field]) for row in rows)} for field in SCORE_FIELDS},"decisions":dict(Counter(row["keep_or_replace"] for row in rows)),"unrelated":primary.count("unrelated"),"claimed_consequence":primary.count("illustrates_claimed_consequence"),"schema_failures":len(results[provider].get("failures",{}))}
    pairwise={}
    for index,left in enumerate(providers):
        for right in providers[index+1:]:
            lr=[results[left]["items"][key] for key in ids]; rr=[results[right]["items"][key] for key in ids]; lp=[x["primary_relationship"] for x in lr]; rp=[x["primary_relationship"] for x in rr]
            score_rows={}
            for field in SCORE_FIELDS:
                lv=[float(x[field]) for x in lr]; rv=[float(x[field]) for x in rr]
                score_rows[field]={"spearman":spearman(lv,rv),"mean_absolute_difference":statistics.mean(abs(a-b) for a,b in zip(lv,rv)),"largest_disagreements":sorted(({"case_id":key,left:a,right:b,"absolute_difference":abs(a-b)} for key,a,b in zip(ids,lv,rv)),key=lambda x:(-x["absolute_difference"],x["case_id"]))[:5]}
            pairwise[f"{left}_vs_{right}"]={"exact_primary_agreement":sum(a==b for a,b in zip(lp,rp))/len(ids),"primary_or_secondary_overlap":sum(bool(({a["primary_relationship"],a["secondary_relationship"]}-{None}) & ({b["primary_relationship"],b["secondary_relationship"]}-{None})) for a,b in zip(lr,rr))/len(ids),"cohens_kappa":cohens_kappa(lp,rp),"confusion_matrix":confusion(lp,rp),"decision_agreement":sum(a["keep_or_replace"]==b["keep_or_replace"] for a,b in zip(lr,rr))/len(ids),"scores":score_rows,"consequence_disagreements":[key for key,a,b in zip(ids,lr,rr) if (a["primary_relationship"]=="illustrates_claimed_consequence") != (b["primary_relationship"]=="illustrates_claimed_consequence")],"unrelated_disagreements":[key for key,a,b in zip(ids,lr,rr) if (a["primary_relationship"]=="unrelated") != (b["primary_relationship"]=="unrelated")]}
    all_exact=[]; all_overlap=[]; all_decision=[]; two_one=[]; three_way=[]; unique_unrelated=[]; unique_consequence=[]; consensus=[]; three_one=[]; two_two=[]; complete=[]; decision_outliers=[]
    for key in ids:
        rows={p:results[p]["items"][key] for p in providers}; primary=[x["primary_relationship"] for x in rows.values()]; decisions=[x["keep_or_replace"] for x in rows.values()]; all_exact.append(len(set(primary))==1); all_decision.append(len(set(decisions))==1)
        common=set.intersection(*[({x["primary_relationship"],x["secondary_relationship"]}-{None}) for x in rows.values()]); all_overlap.append(bool(common)); counts=Counter(primary)
        if sorted(counts.values())==[1,2]: two_one.append({"case_id":key,"relationships":{p:rows[p]["primary_relationship"] for p in providers}})
        if len(counts)==3: three_way.append({"case_id":key,"relationships":{p:rows[p]["primary_relationship"] for p in providers}})
        if len(providers)==4:
            values=sorted(counts.values())
            if values==[1,3]: three_one.append(key)
            if values==[2,2]: two_two.append(key)
            if values==[1,1,1,1]: complete.append(key)
            dc=Counter(decisions)
            if sorted(dc.values())==[1,3]: decision_outliers.append({"case_id":key,"decisions":{p:rows[p]["keep_or_replace"] for p in providers}})
            mode=counts.most_common(); stable=len(mode)==1 or (len(mode)>1 and mode[0][1]>mode[1][1]); label="4/4 agreement" if values==[4] else "3/4 majority" if values==[1,3] else "2/2 split" if values==[2,2] else "no stable consensus"
            med={field:statistics.median(float(rows[p][field]) for p in providers) for field in ("relevance_score","directness_score","overall_suitability_score")}
            consensus.append({"case_id":key,"status":label,"modal_primary_relationship":mode[0][0] if stable else None,"modal_decision":Counter(decisions).most_common(1)[0][0],"median_scores":med,"provider_distance":{p:sum(abs(float(rows[p][f])-med[f]) for f in med)/len(med) for p in providers}})
        if primary.count("unrelated")==1: unique_unrelated.append({"case_id":key,"provider":providers[primary.index("unrelated")]})
        if primary.count("illustrates_claimed_consequence")==1: unique_consequence.append({"case_id":key,"provider":providers[primary.index("illustrates_claimed_consequence")]})
    return {"schema_version":1,"providers":providers,"cases":len(ids),"summaries":summaries,"pairwise":pairwise,"multi_provider":{"exact_primary_agreement":statistics.mean(all_exact),"primary_or_secondary_overlap":statistics.mean(all_overlap),"decision_agreement":statistics.mean(all_decision),"two_against_one":two_one,"three_way_disagreements":three_way,"three_against_one":three_one,"two_versus_two":two_two,"complete_disagreement":complete,"decision_outliers":decision_outliers,"unique_unrelated":unique_unrelated,"unique_claimed_consequence":unique_consequence,"consensus":consensus},"three_way":{"exact_primary_agreement":statistics.mean(all_exact),"primary_or_secondary_overlap":statistics.mean(all_overlap),"decision_agreement":statistics.mean(all_decision),"two_against_one":two_one,"three_way_disagreements":three_way,"unique_unrelated":unique_unrelated,"unique_claimed_consequence":unique_consequence},"provisional_recommendation":"Insufficient human evidence"}
