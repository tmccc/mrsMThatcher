#!/usr/bin/env python3
"""Offline retrieval, validation, and audit helpers for accuracy-first replies."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from historical_context_formatter import load_and_validate_corpus

MODES = {
    "historical_correction", "historical_context", "researched_principle",
    "wry_reply", "playful_reply", "deadpan_reply", "warm_reply", "no_reply",
}
HUMOUR_TONES = {"dry", "wry", "playful", "deadpan", "warm", "none"}
CONFIDENCE_LEVELS = {"high": 3, "medium": 2, "low": 1, "none": 0}
HISTORICAL_MODES = {"historical_correction", "historical_context", "researched_principle"}
CANNED_PATTERNS = (
    "the lesson remains unlearned", "socialism promised", "history has a habit",
    "one system",
)
RETRIEVAL_FIELDS = (
    "quote_text", "verified_text", "source_event", "historical_context",
    "immediate_subject", "intended_argument", "literal_meaning", "broader_principle",
    "mechanism", "claimed_consequence",
)
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'-]{2,}")
STOPWORDS = {
    "and", "are", "but", "for", "from", "has", "have", "into", "its", "not",
    "that", "the", "their", "then", "there", "these", "they", "this", "was",
    "were", "what", "when", "where", "which", "who", "with", "would", "your",
}


DEFAULT_REPLY_STRATEGY = {
    "enabled": False,
    "accuracy_first": True,
    "research_corpus_enabled": True,
    "research_corpus_path": "semantic_alignment_research/quote_research_full_001",
    "completed_packets_only": True,
    "allow_historical_correction": True,
    "allow_historical_context": True,
    "allow_researched_principle": True,
    "allow_humour": True,
    "preferred_humour_tones": ["dry", "wry", "playful", "deadpan", "warm"],
    "maximum_retrieved_packets": 5,
    "minimum_grounded_confidence": "medium",
    "no_hashtags": True,
}


@dataclass(frozen=True)
class RetrievedEvidence:
    quote_id: str
    score: float
    verification_status: str
    summary: str
    packet: dict[str, Any]

    def prompt_record(self) -> dict[str, Any]:
        packet = self.packet
        return {
            "quote_id": self.quote_id,
            "verification_status": self.verification_status,
            "research_confidence": packet.get("research_confidence", "low"),
            "source_event": packet.get("source_event", ""),
            "date": packet.get("date", ""),
            "immediate_subject": packet.get("immediate_subject", ""),
            "intended_argument": packet.get("intended_argument", ""),
            "broader_principle": packet.get("broader_principle", ""),
            "mechanism": packet.get("mechanism", ""),
            "claimed_consequence": packet.get("claimed_consequence", ""),
            "verified_text": packet.get("verified_text", ""),
        }


class ReplyDecision(str):
    """A string-compatible reply carrying private strategy metadata."""

    strategy_metadata: dict[str, Any]

    def __new__(cls, value: str, strategy_metadata: dict[str, Any]):
        instance = str.__new__(cls, value)
        instance.strategy_metadata = strategy_metadata
        return instance


def validate_reply_strategy_config(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["reply_strategy must be an object"]
    if set(value) != set(DEFAULT_REPLY_STRATEGY):
        return ["reply_strategy fields mismatch"]
    errors: list[str] = []
    for key in (
        "enabled", "accuracy_first", "research_corpus_enabled", "completed_packets_only",
        "allow_historical_correction", "allow_historical_context",
        "allow_researched_principle", "allow_humour", "no_hashtags",
    ):
        if type(value.get(key)) is not bool:
            errors.append(f"reply_strategy.{key} must be boolean")
    if not isinstance(value.get("research_corpus_path"), str) or not value["research_corpus_path"].strip():
        errors.append("reply_strategy.research_corpus_path must be a non-empty string")
    maximum = value.get("maximum_retrieved_packets")
    if type(maximum) is not int or not 1 <= maximum <= 10:
        errors.append("reply_strategy.maximum_retrieved_packets must be an integer from 1 to 10")
    tones = value.get("preferred_humour_tones")
    if not isinstance(tones, list) or not tones or any(tone not in HUMOUR_TONES - {"none"} for tone in tones):
        errors.append("reply_strategy.preferred_humour_tones contains unsupported values")
    if value.get("minimum_grounded_confidence") not in {"medium", "high"}:
        errors.append("reply_strategy.minimum_grounded_confidence must be medium or high")
    if value.get("enabled"):
        if value.get("accuracy_first") is not True:
            errors.append("reply_strategy.accuracy_first must remain true when enabled")
        if value.get("completed_packets_only") is not True:
            errors.append("reply_strategy.completed_packets_only must remain true when enabled")
        if value.get("no_hashtags") is not True:
            errors.append("reply_strategy.no_hashtags must remain true when enabled")
    return errors


def allowed_modes_from_config(config: dict[str, Any]) -> set[str]:
    modes = {"no_reply"}
    if config.get("allow_historical_correction"):
        modes.add("historical_correction")
    if config.get("allow_historical_context"):
        modes.add("historical_context")
    if config.get("allow_researched_principle"):
        modes.add("researched_principle")
    if config.get("allow_humour"):
        modes.update({"wry_reply", "playful_reply", "deadpan_reply", "warm_reply"})
    return modes


def _tokens(value: Any) -> set[str]:
    return {token for token in TOKEN_RE.findall(str(value or "").lower()) if token not in STOPWORDS}


def _packet_text(packet: dict[str, Any]) -> str:
    values: list[str] = [str(packet.get(field) or "") for field in RETRIEVAL_FIELDS]
    for field in ("entities",):
        item = packet.get(field)
        if isinstance(item, list):
            values.extend(str(part) for part in item)
    guidance = packet.get("editorial_guidance")
    if isinstance(guidance, dict):
        values.append(str(guidance.get("desired_first_impression") or ""))
    return " ".join(values)


def retrieve_research_packets(
    incoming_text: str,
    research_dir: Path,
    *,
    maximum: int = 5,
) -> list[RetrievedEvidence]:
    packets, unresolved = load_and_validate_corpus(research_dir)
    if len(packets) != 626 or len(unresolved) != 6:
        raise RuntimeError("reply retrieval requires 626 completed packets and six unresolved records")
    query = _tokens(incoming_text)
    if not query:
        return []
    ranked: list[RetrievedEvidence] = []
    for quote_id, packet in packets.items():
        overlap = query & _tokens(_packet_text(packet))
        if not overlap:
            continue
        entity_tokens = _tokens(" ".join(str(value) for value in packet.get("entities", [])))
        score = float(len(overlap)) + 1.5 * len(query & entity_tokens)
        summary = str(packet.get("intended_argument") or packet.get("broader_principle") or "").strip()
        ranked.append(RetrievedEvidence(quote_id, score, str(packet["verification_status"]), summary, packet))
    ranked.sort(key=lambda item: (-item.score, item.quote_id))
    return ranked[:maximum]


def build_strategy_prompt_context(evidence: Iterable[RetrievedEvidence]) -> str:
    records = [item.prompt_record() for item in evidence]
    return json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def decision_schema_instruction() -> str:
    return (
        'Return exactly one JSON object with fields: '
        'mode, humour_tone, evidence_confidence, retrieved_quote_ids, evidence_summary, '
        'factual_claim_made, grounded, reply_text, no_reply_reason. '
        f'Mode must be one of {sorted(MODES)}. Humour tone must be one of {sorted(HUMOUR_TONES)}. '
        'Confidence must be high, medium, low, or none. Use reply_text="" for no_reply.'
    )


def strategy_mode_guidance() -> str:
    return (
        "Classification hierarchy: first identify any factual claim in the incoming post. "
        "If it is materially false or misleading and supplied evidence resolves it with high confidence, use historical_correction. "
        "Otherwise use historical_context when a grounded qualification materially improves a claim that is not clearly false. "
        "Use researched_principle for a concise evidence-backed summary of Thatcher's argument without pretending it is a direct quotation. "
        "If history adds no material value, choose the most fitting humour mode: wry_reply, playful_reply, deadpan_reply, or warm_reply. "
        "Use no_reply for weak evidence, unclear or unrelated posts, repetition, needless conflict, or when no useful response exists. "
        "Do not force history. Do not force humour."
    )


def parse_decision_json(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("reply decision must be an object")
    return value


def _sentence_count(text: str) -> int:
    return len(re.findall(r"[.!?](?:\s|$)", text.strip())) or (1 if text.strip() else 0)


def _contains_emoji(text: str) -> bool:
    return any(
        "\U0001f000" <= char <= "\U0001faff"
        or "\U00002600" <= char <= "\U000027bf"
        for char in text
    )


def _quotes_are_verified(text: str, evidence: Iterable[RetrievedEvidence]) -> bool:
    quoted = re.findall(r'[“"]([^”"]+)[”"]', text)
    quoted.extend(re.findall(r"‘([^\n]+?)’(?![A-Za-z0-9])", text))
    quoted.extend(re.findall(r"(?<![A-Za-z0-9])'([^\n]+?)'(?![A-Za-z0-9])", text))
    if not quoted:
        return True
    exact_texts = {
        " ".join(str(item.packet.get("verified_text") or item.packet.get("quote_text") or "").split()).lower()
        for item in evidence
        if item.verification_status == "exact"
    }
    return all(" ".join(value.split()).lower() in exact_texts for value in quoted)


def reply_repetition_reason(text: str, recent_replies: Iterable[str]) -> str | None:
    normalised = " ".join(text.lower().split())
    previous_values = [" ".join(str(previous).lower().split()) for previous in recent_replies]
    if normalised in previous_values:
        return "exact duplicate reply"
    if any(pattern in normalised for pattern in CANNED_PATTERNS):
        return "canned formulation"
    tokens = _tokens(normalised)
    for previous in previous_values:
        previous_tokens = _tokens(previous)
        union = tokens | previous_tokens
        if union and len(tokens & previous_tokens) / len(union) >= 0.8:
            return "highly similar reply"
    return None


def reply_is_repetitive(text: str, recent_replies: Iterable[str]) -> bool:
    return reply_repetition_reason(text, recent_replies) is not None


def validate_reply_decision(
    value: dict[str, Any],
    evidence: list[RetrievedEvidence],
    *,
    allowed_quote_ids: set[str],
    recent_replies: Iterable[str] = (),
    maximum_length: int = 270,
    allowed_modes: set[str] | None = None,
    allowed_humour_tones: set[str] | None = None,
    minimum_grounded_confidence: str = "medium",
) -> dict[str, Any]:
    required = {
        "mode", "humour_tone", "evidence_confidence", "retrieved_quote_ids",
        "evidence_summary", "factual_claim_made", "grounded", "reply_text", "no_reply_reason",
    }
    if set(value) != required:
        raise ValueError("reply decision fields mismatch")
    mode = value["mode"]
    tone = value["humour_tone"]
    confidence = value["evidence_confidence"]
    ids = value["retrieved_quote_ids"]
    if not all(isinstance(item, str) for item in (mode, tone, confidence, value["reply_text"])):
        raise ValueError("reply mode, tone, confidence, and text must be strings")
    reply = value["reply_text"].strip()
    if mode not in MODES or tone not in HUMOUR_TONES or confidence not in CONFIDENCE_LEVELS:
        raise ValueError("unsupported reply mode, tone, or confidence")
    if allowed_modes is not None and mode not in allowed_modes:
        raise ValueError("reply mode is disabled by configuration")
    if allowed_humour_tones is not None and tone != "none" and tone not in allowed_humour_tones:
        raise ValueError("humour tone is disabled by configuration")
    evidence_ids = {item.quote_id for item in evidence}
    if not isinstance(ids, list) or any(
        not isinstance(item, str) or item not in allowed_quote_ids or item not in evidence_ids
        for item in ids
    ):
        raise ValueError("reply cites a non-retrieved or unresolved quote ID")
    selected_evidence = [item for item in evidence if item.quote_id in set(ids)]
    if type(value["factual_claim_made"]) is not bool or type(value["grounded"]) is not bool:
        raise ValueError("reply factual and grounded flags must be boolean")
    if not isinstance(value["evidence_summary"], str) or not isinstance(value["no_reply_reason"], str):
        raise ValueError("reply evidence and no-reply reason must be strings")
    if mode == "no_reply" and (
        tone != "none" or ids or value["evidence_summary"].strip()
        or value["factual_claim_made"] or value["grounded"]
    ):
        raise ValueError("no_reply metadata must be empty")
    if mode in HISTORICAL_MODES and not value["factual_claim_made"]:
        raise ValueError("historical modes must identify a factual claim")
    if mode == "historical_correction" and confidence != "high":
        raise ValueError("historical_correction requires high confidence")
    if mode == "historical_context" and CONFIDENCE_LEVELS[confidence] < 2:
        raise ValueError("historical_context requires medium confidence")
    if mode == "researched_principle" and CONFIDENCE_LEVELS[confidence] < 2:
        raise ValueError("researched_principle requires medium confidence")
    if mode in HISTORICAL_MODES and (not ids or not value["grounded"]):
        raise ValueError("historical reply modes require retrieved grounded evidence")
    if mode in HISTORICAL_MODES and CONFIDENCE_LEVELS[confidence] < CONFIDENCE_LEVELS[minimum_grounded_confidence]:
        raise ValueError("historical reply is below configured grounded confidence")
    if value["factual_claim_made"]:
        packet_floor = "high" if mode == "historical_correction" else minimum_grounded_confidence
        if any(
            CONFIDENCE_LEVELS.get(str(item.packet.get("research_confidence") or "low"), 0)
            < CONFIDENCE_LEVELS[packet_floor]
            for item in selected_evidence
        ):
            raise ValueError("factual reply is below required packet confidence")
    if value["factual_claim_made"] and not value["grounded"]:
        raise ValueError("factual claims require grounding")
    if value["grounded"] and not ids:
        raise ValueError("grounded replies require selected evidence")
    if value["grounded"] and not value["evidence_summary"].strip():
        raise ValueError("grounded replies require an evidence summary")
    if value["factual_claim_made"] and CONFIDENCE_LEVELS[confidence] < CONFIDENCE_LEVELS[minimum_grounded_confidence]:
        raise ValueError("factual reply is below configured grounded confidence")
    if mode == "no_reply":
        if reply or not value["no_reply_reason"].strip():
            raise ValueError("no_reply requires an empty reply and a reason")
    else:
        if not reply or len(reply) > maximum_length or _sentence_count(reply) > 2:
            raise ValueError("reply must be one or two sentences within the configured limit")
        if "#" in reply:
            raise ValueError("hashtags are not allowed")
        if _contains_emoji(reply):
            raise ValueError("emoji are not allowed")
        if not _quotes_are_verified(reply, selected_evidence):
            raise ValueError("quotation marks require verified exact text")
        repetition_reason = reply_repetition_reason(reply, recent_replies)
        if repetition_reason:
            raise ValueError(repetition_reason)
    return {
        **value,
        "reply_text": reply,
        "retrieved_quote_ids": list(dict.fromkeys(ids)),
    }


def audit_digest(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    section_pattern = re.compile(
        r"^## (Mention replies|Hot-post replies|Quote-tweet replies)\n(.*?)(?=^## |\Z)",
        re.M | re.S,
    )
    rows: list[dict[str, Any]] = []
    for match in section_pattern.finditer(text):
        lane = match.group(1).lower().replace(" replies", "").replace("-", "_")
        lines = [line for line in match.group(2).splitlines() if line.startswith("|")]
        for line in lines[2:]:
            cells = [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", line.strip("|"))]
            if cells:
                rows.append({"lane": lane, "cells": cells})
    return {
        "schema_version": 1,
        "digest": str(path),
        "auditable_reply_count": len(rows),
        "items": rows,
        "finding": "no auditable replies in digest" if not rows else "manual/model audit required",
        "network_calls": 0,
    }


def audit_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline reply digest audit")
    parser.add_argument("--digest", required=True, type=Path)
    parser.add_argument("--research-run", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    packets, unresolved = load_and_validate_corpus(args.research_run)
    result = audit_digest(args.digest)
    result.update({"completed_packets": len(packets), "unresolved_packets": len(unresolved)})
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"Digest: {args.digest}")
        print(f"Auditable replies: {result['auditable_reply_count']}")
        print(f"Finding: {result['finding']}")
        print("No posts or network calls were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(audit_cli())
