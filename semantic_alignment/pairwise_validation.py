"""Validate blind pairwise judgements and provider calibration results."""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .first_impression import EVEREST_QUOTE_HASH
from .io import atomic_write_json

SCHEMA_VERSION = 1
DEFAULT_SEED = 20260712
CHOICES = {"A", "B", "neither", "unsure"}
STRENGTHS = {"slight", "moderate", "strong"}
REASONS = {
    "better_semantic_fit", "better_first_impression", "better_tone",
    "better_visual_impact", "less_distracting", "acceptable_symbolism",
    "neither_is_good_enough", "other",
}
PAIRWISE_PROMPT_VERSION = "pairwise-editorial-validation-v1"
PAIRWISE_CHOICES = {"A", "B", "neither", "unsure"}
PAIRWISE_AXES = {"A", "B", "equal", "unclear"}
PAIRWISE_RISKS = {"wrong_dominant_message", "tone_mismatch", "too_generic", "too_literal",
                  "ideological_substitution", "weak_visual_impact", "distracting_symbol",
                  "acceptable_symbolism", "no_good_candidate"}
PAIRWISE_SCHEMA = {"type":"object","additionalProperties":False,"properties":{
    "preferred_candidate":{"type":"string","enum":sorted(PAIRWISE_CHOICES)},
    "preference_strength":{"type":"string","enum":sorted(STRENGTHS)},
    **{key:{"type":"string","enum":sorted(PAIRWISE_AXES)} for key in
       ("better_semantic_fit","better_first_impression","better_tone_match","better_visual_impact","less_distracting")},
    "would_publish_preferred":{"type":"boolean"},
    "reason":{"type":"string","minLength":1,"maxLength":1200},
    "risk_flags":{"type":"array","items":{"type":"string","enum":sorted(PAIRWISE_RISKS)},"maxItems":9,"uniqueItems":True}},
    "required":["preferred_candidate","preference_strength","better_semantic_fit","better_first_impression",
                "better_tone_match","better_visual_impact","less_distracting","would_publish_preferred","reason","risk_flags"]}
FREE_TRADE_QUOTE_HASH = "1ae9443573e42259af54c30a0ec90a6a8746e640b09e53ae1c28a4c0a2d0ed6b"


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def pair_id(quote_hash: str, current: str, challenger: str) -> str:
    """Return the pair ID."""
    return hashlib.sha256(f"pairwise-v1:{quote_hash}:{current}:{challenger}".encode()).hexdigest()[:20]


def validate_review(value: Any, *, complete: bool = True) -> dict[str, Any]:
    """Validate review."""
    if not isinstance(value, dict):
        raise ValueError("review must be an object")
    allowed = {"case_id", "preferred_candidate", "preference_strength", "reasons", "notes", "updated_at"}
    if set(value) - allowed:
        raise ValueError("unknown review field")
    if not isinstance(value.get("case_id"), str) or not value["case_id"]:
        raise ValueError("case_id is required")
    choice = value.get("preferred_candidate")
    strength = value.get("preference_strength")
    if complete and (choice not in CHOICES or strength not in STRENGTHS):
        raise ValueError("completed review requires a valid choice and strength")
    if choice is not None and choice not in CHOICES:
        raise ValueError("invalid preferred_candidate")
    if strength is not None and strength not in STRENGTHS:
        raise ValueError("invalid preference_strength")
    reasons = value.get("reasons", [])
    if not isinstance(reasons, list) or any(type(x) is not str or x not in REASONS for x in reasons):
        raise ValueError("invalid reasons")
    if not isinstance(value.get("notes", ""), str):
        raise ValueError("notes must be text")
    return dict(value)


def validate_pairwise_judgement(value: Any) -> dict[str, Any]:
    """Validate pairwise judgement."""
    required = set(PAIRWISE_SCHEMA["required"])
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("pairwise judgement fields do not match schema")
    if value["preferred_candidate"] not in PAIRWISE_CHOICES or value["preference_strength"] not in STRENGTHS:
        raise ValueError("invalid pairwise choice")
    for key in ("better_semantic_fit","better_first_impression","better_tone_match","better_visual_impact","less_distracting"):
        if value[key] not in PAIRWISE_AXES: raise ValueError(f"invalid axis: {key}")
    if type(value["would_publish_preferred"]) is not bool: raise ValueError("would_publish_preferred must be boolean")
    if not isinstance(value["reason"], str) or not value["reason"].strip() or len(value["reason"]) > 1200:
        raise ValueError("invalid reason")
    flags=value["risk_flags"]
    if not isinstance(flags,list) or len(flags)!=len(set(flags)) or any(type(x) is not str or x not in PAIRWISE_RISKS for x in flags):
        raise ValueError("invalid risk flags")
    return dict(value)


def unresolved_template_tokens(text: str) -> list[str]:
    """Return the unresolved template tokens."""
    import re
    patterns=(r"\$\{[^}\n]+\}",r"(?<!\$)\{(?:metrics|costs|exact|model_|len\()[^}\n]+\}",r"(?<!\$)\{[A-Za-z_][A-Za-z0-9_]*(?:\[[^}\n]+\])?\}")
    return sorted(set(match.group(0) for pattern in patterns for match in re.finditer(pattern,text)))


def assert_report_rendered(text: str) -> None:
    """Assert report rendered."""
    tokens=unresolved_template_tokens(text)
    if tokens: raise ValueError(f"unresolved report template tokens: {tokens}")


def pairwise_model_prompt(intent: dict[str, Any], quote: dict[str, Any], a: dict[str, Any], b: dict[str, Any]) -> str:
    """Return the pairwise model prompt."""
    allowed_intent={k:intent.get(k) for k in ("desired_first_impression","desired_primary_visual_subjects","desired_tone","undesired_dominant_messages")}
    allowed_quote={k:quote.get(k) for k in ("dominant_message","core_claim","claims","primary_themes","secondary_themes","specific_concepts","not_about")}
    def clean(row):
        first=row["first_impression"]; semantic=row["semantic"]; editorial=row["editorial"]
        return {"semantic":{k:semantic.get(k) for k in ("dominant_message","core_implied_claim","implied_claims","primary_themes","secondary_messages","specific_concepts","visual_evidence","emotional_tone")},
                "first_impression":{k:first.get(k) for k in ("first_impression_message","dominant_visual_subject","first_object_noticed","dominant_symbols","visual_competition","focal_clarity_score","dominant_subject_strength","distracting_symbol_score","visual_competition_score","primary_tone","secondary_tones")},
                "editorial_power":((editorial.get("analysis") or {}).get("quality") or {}).get("overall")}
    payload={"quote_visual_intent":allowed_intent,"quote_semantics":allowed_quote,"candidate_A":clean(a),"candidate_B":clean(b)}
    return f"""Prompt version: {PAIRWISE_PROMPT_VERSION}
Act as an experienced newspaper picture editor. Choose which anonymous image would
make the stronger published post when displayed alongside the quotation represented
by the independent fingerprints below. Credit useful symbolism and valid indirect
illustration; do not require literal depiction. Penalise a wrong dominant message,
tone mismatch, distraction, generic substitution, or weak visual impact. Select
neither when neither candidate is publishable, and unsure only when evidence is
genuinely insufficient. A and B are arbitrary positions. You are not given and must
not infer production status, selector rank, generation history, prior model results,
human labels, or an expected answer. No tools, search, grounding, URLs, raw images or
external context are available. Return only the requested JSON.
INPUT:{_canonical(payload)}"""


def calibration_metrics(results: dict[str, dict[str, Any]], reviews: dict[str, Any], blind: dict[str, Any], case_ids: list[str]) -> dict[str, Any]:
    """Return the calibration metrics."""
    summaries={}
    for provider,doc in sorted(results.items()):
        rows=[]
        for cid in case_ids:
            model=(doc.get("items") or {}).get(cid); human=reviews.get(cid)
            if model and human: rows.append((cid,model,human,blind[cid]))
        exact=sum(m["preferred_candidate"]==h["preferred_candidate"] for _,m,h,_ in rows)
        usable=[x for x in rows if x[1]["preferred_candidate"]!="unsure" and x[2]["preferred_candidate"]!="unsure"]
        harmful=sum(m["preferred_candidate"]!=h["preferred_candidate"] and b["prior_human_label"]=="keep" for _,m,h,b in rows)
        corrected=sum(m["preferred_candidate"]==h["preferred_candidate"] and b["prior_human_label"]=="replace" for _,m,h,b in rows)
        neither=sum(m["preferred_candidate"]==h["preferred_candidate"]=="neither" for _,m,h,_ in rows)
        summaries[provider]={"evaluated":len(rows),"exact_agreement":exact/len(rows) if rows else None,
            "agreement_excluding_unsure":sum(m["preferred_candidate"]==h["preferred_candidate"] for _,m,h,_ in usable)/len(usable) if usable else None,
            "harmful_displacements":harmful,"corrections":corrected,"neither_agreement":neither,
            "schema_failures":len(doc.get("failures",{})),"cost_usd":sum(x.get("cost_usd",0) for x in doc.get("calls",[])),
            "mean_latency_seconds":sum(x.get("latency_seconds",0) for x in doc.get("calls",[]))/max(1,len(doc.get("calls",[])))}
    return {"schema_version":1,"analysis_kind":"pairwise_provider_calibration","providers":summaries}


def provider_selection(metrics: dict[str, Any], *, minimum_agreement: float = 0.55) -> dict[str, Any]:
    """Return the provider selection."""
    rows=metrics["providers"]
    eligible=[(v.get("exact_agreement") or 0,-v.get("harmful_displacements",0),k) for k,v in rows.items()
              if v.get("evaluated")==15 and not v.get("schema_failures") and (v.get("exact_agreement") or 0)>=minimum_agreement]
    if not eligible:
        return {"schema_version":1,"decision":"do_not_proceed","reason":"no provider met the predefined calibration adequacy gate","minimum_exact_agreement":minimum_agreement}
    eligible.sort(reverse=True); best=eligible[0]
    if len(eligible)>1 and eligible[1][:2]==best[:2]:
        return {"schema_version":1,"decision":"use_two_provider_consensus","providers":[best[2],eligible[1][2]],"reason":"top providers tied on agreement and harmful displacement"}
    return {"schema_version":1,"decision":"use_one_provider","providers":[best[2]],"reason":"highest adequate exact agreement with harmful displacement as tie-breaker"}


def review_progress(case_ids: list[str], reviews: dict[str, Any]) -> dict[str, int]:
    """Return the review progress."""
    complete = 0
    for cid in case_ids:
        try:
            validate_review(reviews.get(cid), complete=True)
            complete += 1
        except (TypeError, ValueError):
            pass
    return {"total": len(case_ids), "complete": complete, "incomplete": len(case_ids) - complete}


def save_review(path: Path, case_ids: set[str], review: dict[str, Any]) -> None:
    """Save review."""
    row = validate_review(review)
    if row["case_id"] not in case_ids:
        raise ValueError("unknown case_id")
    document = {"schema_version": SCHEMA_VERSION, "analysis_kind": "pairwise_human_reviews", "items": {}}
    if path.exists():
        document = json.loads(path.read_text(encoding="utf-8"))
    previous = document.setdefault("items", {}).get(row["case_id"], {})
    row["case_id"] = previous.get("case_id", row["case_id"])
    row["updated_at"] = datetime.now(timezone.utc).isoformat()
    document["items"][row["case_id"]] = row
    atomic_write_json(path, document)


def _tokens(value: Any) -> set[str]:
    text = _canonical(value).lower()
    return {word for word in "".join(ch if ch.isalnum() else " " for ch in text).split() if len(word) > 3}


def _candidate_score(intent: dict[str, Any], image: dict[str, Any], editorial: dict[str, Any]) -> float:
    """Return the candidate score."""
    desired = _tokens({
        "message": intent.get("desired_first_impression"),
        "subjects": intent.get("desired_primary_visual_subjects", []),
        "tones": intent.get("desired_tone", []),
    })
    observed = _tokens({
        "message": image.get("first_impression_message"),
        "subject": image.get("dominant_visual_subject"),
        "symbols": image.get("dominant_symbols", []),
        "tones": [image.get("primary_tone"), *image.get("secondary_tones", [])],
    })
    overlap = len(desired & observed) / max(1, len(desired | observed))
    quality = ((editorial.get("analysis") or {}).get("quality") or {}).get("overall", 50) / 100
    tone = 1.0 if set(intent.get("desired_tone", [])) & {image.get("primary_tone"), *image.get("secondary_tones", [])} else 0.0
    return round(70 * overlap + 20 * quality + 10 * tone, 6)


def build_manifest(
    validation_cases: list[dict[str, Any]],
    human_reviews: dict[str, Any],
    intents: dict[str, Any],
    images: dict[str, Any],
    editorial: dict[str, Any],
    identity: dict[str, Any],
    image_paths: dict[str, Path],
    previous_pairs: list[dict[str, Any]],
    *, seed: int = DEFAULT_SEED,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build manifest."""
    if len(validation_cases) != 50:
        raise ValueError("pairwise validation requires exactly 50 source cases")
    previous_by_quote = {row["quote_hash"]: row for row in previous_pairs}
    eligible: list[str] = []
    for name in sorted(images):
        policy = ((identity.get(name) or {}).get("analysis") or {}).get("recommended_cross_quote_policy")
        path = image_paths.get(name)
        if path and path.is_file() and name in editorial and policy != "origin_quote_only":
            eligible.append(name)
    if len(eligible) < 2:
        raise ValueError("insufficient eligible fingerprinted images")

    logical: list[dict[str, Any]] = []
    for source in sorted(validation_cases, key=lambda row: row["case_id"]):
        qhash = source["quote_hash"]
        current = source["image_basename"]
        if current not in images or current not in image_paths or not image_paths[current].is_file():
            raise ValueError(f"current candidate unavailable: {current}")
        previous = previous_by_quote.get(qhash)
        forced = None
        if previous:
            options = [previous.get("candidate_a"), previous.get("candidate_b")]
            forced = next((name for name in options if name != current and name in eligible), None)
        ranked = sorted(
            ((-_candidate_score(intents[qhash], images[name], editorial[name]), name) for name in eligible if name != current),
            key=lambda item: (item[0], item[1]),
        )
        challenger = forced or (ranked[0][1] if ranked else None)
        no_challenger = challenger is None
        if no_challenger:
            challenger = current
        prior = human_reviews.get(source["case_id"], {}).get("image_decision", "unsure")
        score = -next((s for s, name in ranked if name == challenger), 0.0)
        reason = "previous_model_pair" if forced else "deterministic_existing_fingerprint_challenger"
        if qhash == EVEREST_QUOTE_HASH:
            reason = "forced_everest_regression"
        elif qhash == FREE_TRADE_QUOTE_HASH:
            reason = "forced_free_trade_regression"
        logical.append({
            "case_id": pair_id(qhash, current, challenger), "source_case_id": source["case_id"],
            "quote_hash": qhash, "current_winner": current, "challenger": challenger,
            "current_human_label": prior, "selection_reason": reason,
            "challenger_local_score": score, "no_credible_challenger": no_challenger,
        })

    rng = random.Random(seed)
    items: list[dict[str, Any]] = []
    blind: dict[str, Any] = {}
    for row in logical:
        swapped = bool(rng.getrandbits(1))
        a, b = (row["challenger"], row["current_winner"]) if swapped else (row["current_winner"], row["challenger"])
        public = {
            "case_id": row["case_id"], "quote_hash": row["quote_hash"],
            "candidate_a": a, "candidate_b": b,
            "selection_reason": row["selection_reason"], "no_credible_challenger": row["no_credible_challenger"],
        }
        public["input_hash"] = hashlib.sha256(_canonical({
            "quote": intents[row["quote_hash"]], "A": images[a], "B": images[b]
        }).encode()).hexdigest()
        items.append(public)
        blind[row["case_id"]] = {
            "current_winner": row["current_winner"], "challenger": row["challenger"],
            "current_position": "B" if swapped else "A", "source_case_id": row["source_case_id"],
            "prior_human_label": row["current_human_label"],
            "candidate_a_source": "alternative" if swapped else "current_winner",
            "candidate_b_source": "current_winner" if swapped else "runner_up",
        }
    if len(items) != 50 or len({row["case_id"] for row in items}) != 50:
        raise ValueError("manifest case IDs are not unique")
    qhashes = {row["quote_hash"] for row in items}
    if EVEREST_QUOTE_HASH not in qhashes or FREE_TRADE_QUOTE_HASH not in qhashes:
        raise ValueError("required regression case absent")
    counts = Counter(value["prior_human_label"] for value in blind.values())
    if counts["keep"] < 10 or counts["replace"] < 10:
        raise ValueError("manifest lacks required keep/replace controls")
    manifest = {
        "schema_version": SCHEMA_VERSION, "analysis_kind": "pairwise_validation_manifest",
        "seed": seed, "case_count": 50, "items": items,
        "integrity": {"active_only": True, "fingerprints_reused": True, "required_regressions": True},
    }
    blind_map = {"schema_version": SCHEMA_VERSION, "analysis_kind": "pairwise_candidate_blind_map", "items": blind}
    return manifest, blind_map


def select_calibration(manifest: dict[str, Any], blind_map: dict[str, Any]) -> dict[str, Any]:
    """Select calibration."""
    rows = manifest["items"]
    forced = [row for row in rows if row["quote_hash"] in {EVEREST_QUOTE_HASH, FREE_TRADE_QUOTE_HASH}]
    used = {row["case_id"] for row in forced}
    groups = {"replace": 5, "keep": 5, "unsure": 3}
    chosen = list(forced)
    for label, target in groups.items():
        pool = [row for row in rows if row["case_id"] not in used and blind_map["items"][row["case_id"]]["prior_human_label"] == label]
        for row in pool[:target]:
            chosen.append(row); used.add(row["case_id"])
    for row in rows:
        if len(chosen) >= 15:
            break
        if row["case_id"] not in used:
            chosen.append(row); used.add(row["case_id"])
    if len(chosen) != 15:
        raise ValueError("could not select 15 calibration cases")
    return {"schema_version": 1, "analysis_kind": "pairwise_calibration_cases", "case_count": 15,
            "case_ids": [row["case_id"] for row in chosen]}


def write_run(run_dir: Path, manifest: dict[str, Any], blind_map: dict[str, Any], calibration: dict[str, Any]) -> None:
    """Write run."""
    run_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(run_dir / "pairwise_manifest.json", manifest)
    atomic_write_json(run_dir / "candidate_blind_map.json", blind_map)
    atomic_write_json(run_dir / "calibration_cases.json", calibration)
    reviews = run_dir / "human_pairwise_reviews.json"
    if not reviews.exists():
        atomic_write_json(reviews, {"schema_version": 1, "analysis_kind": "pairwise_human_reviews", "items": {}})
