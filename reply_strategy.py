#!/usr/bin/env python3
"""Offline retrieval, validation, and audit helpers for accuracy-first replies."""

from __future__ import annotations

import argparse
import html
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from historical_context_formatter import load_and_validate_corpus

MODES = {
    "historical_correction", "historical_context", "researched_principle",
    "principle_reply", "wry_reply", "playful_reply", "deadpan_reply", "warm_reply",
    "no_reply",
}
HUMOUR_TONES = {"dry", "wry", "playful", "deadpan", "warm", "none"}
CONFIDENCE_LEVELS = {"high": 3, "medium": 2, "low": 1, "none": 0}
HISTORICAL_MODES = {"historical_correction", "historical_context", "researched_principle"}
NO_REPLY_REASON_CATEGORIES = {
    "no_reply_due_to_unverifiable_claim",
    "no_reply_due_to_bait_or_abuse",
    "no_reply_due_to_incoherent",
}
REPLY_DECISION_FIELDS = frozenset({
    "mode", "humour_tone", "evidence_confidence", "retrieved_quote_ids",
    "evidence_summary", "factual_claim_made", "grounded", "reply_text",
    "no_reply_reason", "topical_basis",
})
CANNED_PATTERNS = (
    "the lesson remains unlearned", "socialism promised", "history has a habit",
    "one system",
)
CONCRETE_QUESTION_RE = re.compile(
    r"^(?:@[A-Za-z0-9_]+\s+)*(?:(?:please\s+)?(?:(?:(?:can|could|would)\s+you\s+)?"
    r"tell\s+me|do\s+you\s+know)\s+)?"
    r"(?P<word>who|what|where|when|were(?=\s+did\b))\b",
    re.IGNORECASE,
)
ABSTRACT_ANSWER_OPENINGS = (
    "when free to choose",
    "when people are free",
    "freedom is",
    "liberty is",
    "history shows",
    "the lesson is",
    "the principle is",
)
PRINCIPLE_REPLY_UNSUPPORTED_ASSERTION_RE = re.compile(
    r"(?:https?://|@[A-Za-z0-9_]+|\b\d+(?:[.,]\d+)?%?\b|"
    r"\b(?:the\s+)?(?:government|cabinet|prime minister|president|ministers?|"
    r"politicians?|officials?|civil service|courts?|media|they|he|she)\s+"
    r"(?:is|are|was|were|has|have|had|does|do|did|will|would|wants?|believes?|"
    r"knows?|understands?|refuses?|intends?|lied|lies|covered|conspired|betrayed|"
    r"failed|fails)\b)",
    re.IGNORECASE,
)
PRINCIPLE_REPLY_NAMED_ACTOR_ASSERTION_RE = re.compile(
    r"\b(?P<actor>[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){0,2})"
    r"(?:['\N{RIGHT SINGLE QUOTATION MARK}]s\b|\s+(?:is|are|was|were|has|have|had|"
    r"does|do|did|will|would|wants?|seeks?|craves?|believes?|knows?|understands?|"
    r"refuses?|intends?|lied|lies|betrayed|failed|fails)\b)"
)
RETRIEVAL_FIELDS = (
    "quote_text", "verified_text", "source_event", "historical_context",
    "immediate_subject", "intended_argument", "literal_meaning", "broader_principle",
    "mechanism", "claimed_consequence",
)
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'-]{2,}")
TOPICAL_TOKEN_RE = re.compile(r"[^\W_][\w'\N{RIGHT SINGLE QUOTATION MARK}-]{2,}", re.UNICODE)
STOPWORDS = {
    "and", "are", "but", "for", "from", "has", "have", "into", "its", "not",
    "that", "the", "their", "then", "there", "these", "they", "this", "was",
    "were", "what", "when", "where", "which", "who", "with", "would", "your",
}
TOPICAL_STOPWORDS = STOPWORDS | {
    "account", "after", "again", "also", "before", "being", "could", "first",
    "future", "just", "life", "margaret", "mrs", "much", "people", "since",
    "still", "than", "thatcher", "thing", "time", "towards", "very", "must",
}
GENERIC_POLITICAL_TOPIC_TOKENS = {
    "country", "economic", "economy", "freedom", "government", "leader",
    "leadership", "liberty", "nation", "national", "politic", "political",
    "politician", "policy", "socialism", "socialist", "state",
}
TOPICAL_SEGMENT_RE = re.compile(r"(?:\r?\n|\s+[|*•]\s+|(?<=[.!?;:])\s+)")
TOPICAL_CONCEPTS = {
    "accountability": {"accountable", "check", "democratic", "responsible"},
    "economic_policy": {"business", "capital", "economic", "enterprise", "market", "prosperity", "wealth"},
    "freedom_and_coercion": {"berlin", "choice", "coercion", "communism", "east", "freedom", "liberty", "wall", "west"},
    "institutions": {"defend", "institution", "purpose"},
    "leadership_and_popularity": {"conviction", "leader", "popular"},
    "public_case_and_action": {"act", "case", "courage", "explain", "make", "persuade", "understand"},
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
    "hybrid_retrieval": {
        "enabled": False,
        "mode": "shadow",
        "index_path": "semantic_alignment_research/hybrid_reply_retrieval_001",
        "maximum_results": 5,
        "semantic_candidate_count": 20,
        "lexical_candidate_count": 20,
        "query_timeout_ms": 1000,
        "maximum_shadow_history": 5000,
        "fail_open": True,
    },
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
    shadow = value.get("hybrid_retrieval")
    expected_shadow = {
        "enabled", "mode", "index_path", "maximum_results", "semantic_candidate_count",
        "lexical_candidate_count", "query_timeout_ms", "maximum_shadow_history", "fail_open",
    }
    if not isinstance(shadow, dict) or set(shadow) != expected_shadow:
        errors.append("reply_strategy.hybrid_retrieval fields mismatch")
    else:
        if type(shadow.get("enabled")) is not bool:
            errors.append("reply_strategy.hybrid_retrieval.enabled must be boolean")
        if shadow.get("mode") != "shadow":
            errors.append("reply_strategy.hybrid_retrieval.mode must be shadow")
        if not isinstance(shadow.get("index_path"), str) or not shadow["index_path"].strip():
            errors.append("reply_strategy.hybrid_retrieval.index_path must be non-empty")
        for key, low, high in (
            ("maximum_results", 1, 5), ("semantic_candidate_count", 5, 100),
            ("lexical_candidate_count", 5, 100), ("query_timeout_ms", 50, 10_000),
            ("maximum_shadow_history", 100, 100_000),
        ):
            number = shadow.get(key)
            if type(number) is not int or not low <= number <= high:
                errors.append(f"reply_strategy.hybrid_retrieval.{key} must be an integer from {low} to {high}")
        if shadow.get("fail_open") is not True:
            errors.append("reply_strategy.hybrid_retrieval.fail_open must remain true")
    if value.get("enabled"):
        if value.get("accuracy_first") is not True:
            errors.append("reply_strategy.accuracy_first must remain true when enabled")
        if value.get("completed_packets_only") is not True:
            errors.append("reply_strategy.completed_packets_only must remain true when enabled")
        if value.get("no_hashtags") is not True:
            errors.append("reply_strategy.no_hashtags must remain true when enabled")
    return errors


def allowed_modes_from_config(config: dict[str, Any]) -> set[str]:
    modes = {"principle_reply", "no_reply"}
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


def _topical_stem(token: str) -> str:
    aliases = {
        "accountability": "accountable", "checks": "accountable",
        "economy": "economic", "economies": "economic",
        "markets": "market", "popularity": "popular",
        "responsibility": "responsible", "responsibilities": "responsible",
    }
    value = aliases.get(token, token)
    if not value.isascii():
        return value
    if len(value) > 5 and value.endswith("ing"):
        value = value[:-3]
    elif len(value) > 4 and value.endswith("ed"):
        value = value[:-2]
    elif len(value) > 4 and value.endswith("s") and not value.endswith(("ss", "is", "us")):
        value = value[:-1]
    return value


def _topical_tokens(value: Any) -> set[str]:
    return {
        _topical_stem(token.casefold())
        for token in TOPICAL_TOKEN_RE.findall(str(value or ""))
        if token.casefold() not in TOPICAL_STOPWORDS
    }


def _normalise_topical_source(value: Any) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(str(value or "")))
    text = re.sub(r"https?://\S+|(?<![A-Za-z0-9_])@[A-Za-z0-9_]+", " ", text)
    return " ".join(text.split())


def _topical_token_sequence(value: Any) -> list[str]:
    return [_topical_stem(token.casefold()) for token in TOPICAL_TOKEN_RE.findall(str(value or ""))]


def _contains_token_sequence(haystack: list[str], needle: list[str]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    return any(haystack[index:index + len(needle)] == needle for index in range(len(haystack) - len(needle) + 1))


def _substantive_topic_segments(incoming_text: str) -> list[tuple[list[str], set[str]]]:
    cleaned = _normalise_topical_source(incoming_text)
    segments = [
        (_topical_token_sequence(part), _topical_tokens(part))
        for part in TOPICAL_SEGMENT_RE.split(cleaned)
    ]
    segments = [(sequence, tokens) for sequence, tokens in segments if tokens]
    if not segments:
        return []
    # In a multi-point contribution, a short label or list fragment must not
    # become the sole justification for an otherwise unrelated researched reply.
    if max(len(tokens) for _sequence, tokens in segments) >= 4:
        substantive = [(sequence, tokens) for sequence, tokens in segments if len(tokens) >= 4]
        if substantive:
            return substantive
    return segments


def _topical_concepts(tokens: set[str]) -> set[str]:
    return {
        concept
        for concept, members in TOPICAL_CONCEPTS.items()
        if tokens & members
    }


def _specific_topical_tokens(tokens: set[str]) -> set[str]:
    return tokens - GENERIC_POLITICAL_TOPIC_TOKENS


def _topic_pair_aligned(left: set[str], right: set[str]) -> bool:
    left_specific = _specific_topical_tokens(left)
    right_specific = _specific_topical_tokens(right)
    if left_specific & right_specific:
        return True
    for concept in _topical_concepts(left) & _topical_concepts(right):
        specific_members = TOPICAL_CONCEPTS[concept] - GENERIC_POLITICAL_TOPIC_TOKENS
        if left_specific & specific_members and right_specific & specific_members:
            return True
    return False


def reply_topical_relevance_error(
    incoming_text: str,
    reply_text: str,
    selected_evidence: Iterable[RetrievedEvidence],
    *,
    mode: str,
    topical_basis: str,
) -> str | None:
    """Validate an untrusted model-selected issue against incoming text and the reply."""
    if mode not in {"principle_reply", "researched_principle"}:
        return None
    segments = _substantive_topic_segments(incoming_text)
    if not segments:
        return "topical_relevance:no_substantive_incoming_topic"
    basis_sequence = _topical_token_sequence(_normalise_topical_source(topical_basis))
    basis_tokens = _topical_tokens(topical_basis)
    if not basis_sequence or not basis_tokens:
        return "topical_relevance:missing_basis"
    incoming_sequence = _topical_token_sequence(_normalise_topical_source(incoming_text))
    if not _contains_token_sequence(incoming_sequence, basis_sequence):
        return "topical_relevance:basis_not_in_incoming"
    if not any(_contains_token_sequence(sequence, basis_sequence) for sequence, _tokens in segments):
        return "topical_relevance:basis_not_substantive"
    if not _specific_topical_tokens(basis_tokens):
        return "topical_relevance:basis_only_generic_political_vocabulary"
    reply_tokens = _topical_tokens(reply_text)
    if not reply_tokens:
        return "topical_relevance:reply_lacks_topic"

    if mode == "principle_reply":
        if _topic_pair_aligned(basis_tokens, reply_tokens):
            return None
        return "topical_relevance:incoming_reply_bridge_missing"

    evidence_tokens = _topical_tokens(
        " ".join(
            str(item.packet.get(field) or "")
            for item in selected_evidence
            for field in (
                "immediate_subject", "intended_argument", "literal_meaning",
                "broader_principle", "mechanism", "claimed_consequence",
            )
        )
    )
    if not evidence_tokens:
        return "topical_relevance:evidence_lacks_topic"
    if not _topic_pair_aligned(basis_tokens, reply_tokens):
        return "topical_relevance:incoming_reply_bridge_missing"
    if not _topic_pair_aligned(basis_tokens, evidence_tokens):
        return "topical_relevance:incoming_evidence_bridge_missing"
    if not _topic_pair_aligned(reply_tokens, evidence_tokens):
        return "topical_relevance:reply_evidence_bridge_missing"
    return None


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
    # Handles and URL components are incidental metadata, not the user's issue.
    # Keeping them in the lexical query can steer retrieval towards an unrelated
    # entity or page-title token even though parent/thread text is already absent.
    query = _tokens(_normalise_topical_source(incoming_text))
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


def reply_decision_json_schema(
    maximum_length: int = 270,
    maximum_retrieved_ids: int = 5,
) -> dict[str, Any]:
    """Return the provider schema; local validation remains authoritative."""
    if type(maximum_retrieved_ids) is not int or not 1 <= maximum_retrieved_ids <= 10:
        raise ValueError("maximum_retrieved_ids must be an integer from 1 to 10")
    properties: dict[str, Any] = {
        "mode": {"type": "string", "enum": sorted(MODES)},
        "humour_tone": {"type": "string", "enum": sorted(HUMOUR_TONES)},
        "evidence_confidence": {"type": "string", "enum": sorted(CONFIDENCE_LEVELS)},
        "retrieved_quote_ids": {
            "type": "array",
            "items": {"type": "string", "pattern": "[0-9a-f]{64}"},
            "maxItems": maximum_retrieved_ids,
            "description": "Selected completed-corpus evidence IDs; empty for principle_reply and no_reply.",
        },
        "evidence_summary": {
            "type": "string",
            "description": "Grounding summary for a posted factual reply; empty for principle_reply and no_reply.",
        },
        "factual_claim_made": {"type": "boolean"},
        "grounded": {"type": "boolean"},
        "reply_text": {"type": "string", "maxLength": maximum_length},
        "no_reply_reason": {
            "type": "string",
            "description": "Reason for no_reply; do not duplicate it in evidence_summary.",
        },
        "topical_basis": {
            "type": "string",
            "maxLength": 160,
            "description": (
                "For principle_reply or researched_principle, copy a short exact span from the "
                "incoming contribution identifying the issue answered. Never copy parent, quoted-post, "
                "profile, URL, evidence, or prior-reply text. Empty for every other mode."
            ),
        },
    }
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": properties,
        "required": sorted(REPLY_DECISION_FIELDS),
        "additionalProperties": False,
        "if": {
            "properties": {"mode": {"const": "no_reply"}},
            "required": ["mode"],
        },
        "then": {
            "properties": {
                "humour_tone": {"const": "none"},
                "evidence_confidence": {"const": "none"},
                "retrieved_quote_ids": {"maxItems": 0},
                "evidence_summary": {"maxLength": 0},
                "factual_claim_made": {"const": False},
                "grounded": {"const": False},
                "reply_text": {"maxLength": 0},
                "no_reply_reason": {"minLength": 1},
                "topical_basis": {"maxLength": 0},
            },
        },
        "allOf": [
            {
                "if": {
                    "properties": {"mode": {"enum": ["principle_reply", "researched_principle"]}},
                    "required": ["mode"],
                },
                "then": {"properties": {"topical_basis": {"minLength": 3}}},
                "else": {"properties": {"topical_basis": {"maxLength": 0}}},
            },
            {
                "if": {
                    "properties": {"mode": {"const": "principle_reply"}},
                    "required": ["mode"],
                },
                "then": {
                    "properties": {
                        "humour_tone": {"const": "none"},
                        "evidence_confidence": {"const": "none"},
                        "retrieved_quote_ids": {"maxItems": 0},
                        "evidence_summary": {"maxLength": 0},
                        "factual_claim_made": {"const": False},
                        "grounded": {"const": False},
                        "reply_text": {"minLength": 1},
                        "no_reply_reason": {"maxLength": 0},
                    },
                },
            },
        ],
    }


def normalise_reply_decision(value: dict[str, Any]) -> dict[str, Any]:
    """Canonicalise only information-free no_reply representations."""
    normalised = dict(value)
    if normalised.get("mode") != "no_reply":
        return normalised

    for field in ("humour_tone", "evidence_confidence"):
        if field not in normalised:
            continue
        item = normalised[field]
        if item is None or (isinstance(item, str) and not item.strip()):
            normalised[field] = "none"
    if "retrieved_quote_ids" in normalised and normalised["retrieved_quote_ids"] is None:
        normalised["retrieved_quote_ids"] = []
    for field in ("evidence_summary", "reply_text", "topical_basis"):
        if field not in normalised:
            continue
        item = normalised[field]
        if item is None or (isinstance(item, str) and not item.strip()):
            normalised[field] = ""
    for field in ("factual_claim_made", "grounded"):
        if field in normalised and normalised[field] is None:
            normalised[field] = False
    if isinstance(normalised.get("no_reply_reason"), str):
        normalised["no_reply_reason"] = normalised["no_reply_reason"].strip()
    return normalised


def decision_schema_instruction() -> str:
    no_reply_example = {
        "mode": "no_reply",
        "humour_tone": "none",
        "evidence_confidence": "none",
        "retrieved_quote_ids": [],
        "evidence_summary": "",
        "factual_claim_made": False,
        "grounded": False,
        "reply_text": "",
        "no_reply_reason": "No useful response.",
        "topical_basis": "",
    }
    principle_reply_example = {
        "mode": "principle_reply",
        "humour_tone": "none",
        "evidence_confidence": "none",
        "retrieved_quote_ids": [],
        "evidence_summary": "",
        "factual_claim_made": False,
        "grounded": False,
        "reply_text": "Institutions endure only when people are prepared to defend their purpose.",
        "no_reply_reason": "",
        "topical_basis": "institutions have failed",
    }
    return (
        'Return exactly one JSON object with fields: '
        'mode, humour_tone, evidence_confidence, retrieved_quote_ids, evidence_summary, '
        'factual_claim_made, grounded, reply_text, no_reply_reason, topical_basis. '
        f'Mode must be one of {sorted(MODES)}. Humour tone must be one of {sorted(HUMOUR_TONES)}. '
        'Confidence must be high, medium, low, or none. For no_reply, put the explanation only in '
        'no_reply_reason and use humour_tone="none", evidence_confidence="none", '
        'retrieved_quote_ids=[], evidence_summary="", factual_claim_made=false, grounded=false, '
        'reply_text="", and topical_basis="". Use the exact no_reply_reason values '
        'no_reply_due_to_unverifiable_claim, no_reply_due_to_bait_or_abuse, or '
        'no_reply_due_to_incoherent when they apply. Never return bare SKIP. Valid no_reply example: '
        + json.dumps(no_reply_example, ensure_ascii=False, separators=(",", ":"))
        + '. For principle_reply, use humour_tone="none", evidence_confidence="none", '
        'retrieved_quote_ids=[], evidence_summary="", factual_claim_made=false, grounded=false, '
        'and no_reply_reason="". Set topical_basis to a short exact span copied only from the incoming '
        'contribution that identifies the issue answered; never copy inherited thread, quoted-post, profile, '
        'URL, evidence, or prior-reply text. Valid principle_reply example: '
        + json.dumps(principle_reply_example, ensure_ascii=False, separators=(",", ":"))
        + "."
    )


def strategy_mode_guidance() -> str:
    return (
        "Classification hierarchy: first identify any factual claim in the incoming post. "
        "If it is materially false or misleading and supplied evidence resolves it with high confidence, use historical_correction. "
        "Otherwise use historical_context when a grounded qualification materially improves a claim that is not clearly false. "
        "Use researched_principle only for a concise evidence-backed summary of Thatcher's argument that directly addresses a substantive issue in the incoming contribution, without pretending it is a direct quotation. "
        "Evidence confidence and an incidental shared word, name, achievement, or list fragment do not establish topical relevance. An unrelated authentic principle is worse than no_reply. "
        "Set factual_claim_made=true whenever the final reply states a historical or policy fact; every historical mode must set it true. "
        "When uncertain or unsupported details are not needed to answer the broader political or moral point, use principle_reply: "
        "write one concise general sentence that addresses a substantive issue actually present in the incoming contribution and stands independently without repeating, endorsing, or implying those details, "
        "without speculating about anyone's motives, and with no factual claim, evidence, or humour metadata. "
        "For example, 'Why doesn't anyone in government understand this?' may be answered with "
        "'Understanding is not always the same as having the courage to act.' "
        "If history and principle_reply add no material value, choose the most fitting humour mode: wry_reply, playful_reply, deadpan_reply, or warm_reply. "
        "Use no_reply_due_to_unverifiable_claim when any meaningful answer would endorse an unsupported claim; "
        "use no_reply_due_to_bait_or_abuse for abuse or bait that would prolong conflict; use "
        "no_reply_due_to_incoherent for gibberish or an incoherent post. Use no_reply for other weak evidence, "
        "unclear or unrelated posts, repetition, needless conflict, or when no useful response exists. "
        "Do not force history. Do not force humour."
    )


def _incoming_named_actor_terms(incoming_text: str) -> tuple[set[str], set[str]]:
    handles = {
        value.casefold()
        for value in re.findall(r"(?<![A-Za-z0-9_])@([A-Za-z0-9_]+)", str(incoming_text or ""))
    }
    names: set[str] = set()
    for value in re.findall(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})+\b", str(incoming_text or "")):
        parts = value.casefold().split()
        names.add(" ".join(parts))
        names.update(parts)
    return handles, names


def principle_reply_assertion_error(text: str, incoming_text: str = "") -> str | None:
    """Reject obvious concrete allegations disguised as an ungrounded principle."""
    value = str(text or "")
    if PRINCIPLE_REPLY_UNSUPPORTED_ASSERTION_RE.search(value):
        return "principle_reply cannot contain a specific unsupported factual assertion"
    handles, names = _incoming_named_actor_terms(incoming_text)
    for match in PRINCIPLE_REPLY_NAMED_ACTOR_ASSERTION_RE.finditer(value):
        actor = match.group("actor").casefold()
        parts = actor.split()
        if actor in names or any(part in names for part in parts):
            return "principle_reply cannot contain a specific unsupported factual assertion"
        if any(len(part) >= 4 and part in handle for part in parts for handle in handles):
            return "principle_reply cannot contain a specific unsupported factual assertion"
    return None


def concrete_factual_question_word(text: str) -> str | None:
    """Return the leading who/what/where/when word for a concrete question."""
    value = " ".join(str(text or "").split())
    if "?" not in value:
        return None
    match = CONCRETE_QUESTION_RE.match(value)
    if not match:
        return None
    word = match.group("word").lower()
    return "where" if word == "were" else word


def direct_factual_answer_error(question: str, reply: str) -> str | None:
    """Conservatively reject rhetoric in place of a concrete first-sentence answer."""
    question_word = concrete_factual_question_word(question)
    if question_word is None:
        return None
    first_sentence = re.split(r"(?<=[.!?])\s+", str(reply or "").strip(), maxsplit=1)[0]
    lowered = " ".join(first_sentence.lower().split())
    if not lowered or first_sentence.endswith("?"):
        return "concrete factual questions require a direct answer in the first sentence"
    if any(lowered.startswith(opening) for opening in ABSTRACT_ANSWER_OPENINGS):
        return "concrete factual questions cannot be answered with an abstract principle"

    question_lower = question.lower()
    if "berlin wall" in question_lower and question_word == "where":
        if not (
            "east" in lowered
            and "west" in lowered
            and ("berlin" in lowered or "germany" in lowered)
        ):
            return "the Berlin Wall direction question must directly distinguish East from West"
    return None


def direct_question_prompt_guidance(question: str, *, clarification: bool = False) -> str:
    """Build deterministic provider guidance for a concrete factual question."""
    if concrete_factual_question_word(question) is None:
        return ""
    prefix = (
        "This is the one permitted clarification repair. "
        if clarification else
        "The incoming post asks a concrete factual question. "
    )
    return (
        prefix
        + "Answer the requested who, what, where, or when fact directly in the first sentence. "
        "Do not substitute an ideological summary, researched principle, joke, or rhetorical flourish. "
        "Use historical_context or historical_correction with grounded evidence, humour_tone=none, "
        "and at most one brief contextual sentence after the answer. If the supplied evidence is "
        "insufficient, select no_reply. For the question 'Where did people run towards when the "
        "Berlin Wall fell?', a valid direct answer is 'People moved from East Berlin and East Germany "
        "towards West Berlin and West Germany.'"
    )


def parse_decision_json(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.upper() == "SKIP":
        raise ValueError("bare SKIP is not a valid structured reply decision")
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
    incoming_text: str | None = None,
    direct_question_text: str | None = None,
    clarification_reply: bool = False,
) -> dict[str, Any]:
    value = normalise_reply_decision(value)
    if set(value) != REPLY_DECISION_FIELDS:
        raise ValueError("reply decision fields mismatch")
    mode = value["mode"]
    tone = value["humour_tone"]
    confidence = value["evidence_confidence"]
    ids = value["retrieved_quote_ids"]
    if not all(
        isinstance(item, str)
        for item in (mode, tone, confidence, value["reply_text"], value["topical_basis"])
    ):
        raise ValueError("reply mode, tone, confidence, text, and topical basis must be strings")
    reply = value["reply_text"].strip()
    if mode not in MODES or tone not in HUMOUR_TONES or confidence not in CONFIDENCE_LEVELS:
        raise ValueError("unsupported reply mode, tone, or confidence")
    if allowed_modes is not None and mode not in allowed_modes:
        raise ValueError("reply mode is disabled by configuration")
    if allowed_humour_tones is not None and tone != "none" and tone not in allowed_humour_tones:
        raise ValueError("humour tone is disabled by configuration")
    if type(value["factual_claim_made"]) is not bool or type(value["grounded"]) is not bool:
        raise ValueError("reply factual and grounded flags must be boolean")
    if not isinstance(value["evidence_summary"], str) or not isinstance(value["no_reply_reason"], str):
        raise ValueError("reply evidence and no-reply reason must be strings")
    if mode == "no_reply" and (
        tone != "none" or confidence != "none" or ids != []
        or value["evidence_summary"] != "" or value["factual_claim_made"]
        or value["grounded"] or reply != "" or value["topical_basis"] != ""
    ):
        raise ValueError(
            "no_reply requires tone/confidence none, empty evidence and reply text, and false flags"
        )
    if mode == "principle_reply" and (
        tone != "none" or confidence != "none" or ids != []
        or value["evidence_summary"] != "" or value["factual_claim_made"]
        or value["grounded"] or value["no_reply_reason"] != ""
    ):
        raise ValueError(
            "principle_reply requires tone/confidence none, empty evidence and no-reply reason, and false flags"
        )
    if mode not in {"principle_reply", "researched_principle"} and value["topical_basis"] != "":
        raise ValueError("topical basis is allowed only for principle reply modes")
    evidence_ids = {item.quote_id for item in evidence}
    if not isinstance(ids, list) or any(
        not isinstance(item, str) or item not in allowed_quote_ids or item not in evidence_ids
        for item in ids
    ):
        raise ValueError("reply cites a non-retrieved or unresolved quote ID")
    selected_evidence = [item for item in evidence if item.quote_id in set(ids)]
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
        if mode == "principle_reply":
            assertion_error = principle_reply_assertion_error(reply, str(incoming_text or ""))
            if assertion_error:
                raise ValueError(assertion_error)
        if not _quotes_are_verified(reply, selected_evidence):
            raise ValueError("quotation marks require verified exact text")
        if mode in {"principle_reply", "researched_principle"}:
            if incoming_text is None:
                raise ValueError("topical_relevance:incoming_text_unavailable")
            relevance_error = reply_topical_relevance_error(
                incoming_text,
                reply,
                selected_evidence,
                mode=mode,
                topical_basis=value["topical_basis"],
            )
            if relevance_error:
                raise ValueError(relevance_error)
        repetition_reason = reply_repetition_reason(reply, recent_replies)
        if repetition_reason:
            raise ValueError(repetition_reason)
        question = str(direct_question_text or "")
        if concrete_factual_question_word(question) is not None:
            if mode not in {"historical_correction", "historical_context"}:
                raise ValueError("concrete factual questions require a direct grounded historical answer")
            if tone != "none" or not value["factual_claim_made"] or not value["grounded"]:
                raise ValueError("direct factual answers require grounded factual metadata and no humour")
            direct_error = direct_factual_answer_error(question, reply)
            if direct_error:
                raise ValueError(direct_error)
        if clarification_reply and concrete_factual_question_word(question) is None:
            raise ValueError("clarification replies require the original concrete factual question")
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
