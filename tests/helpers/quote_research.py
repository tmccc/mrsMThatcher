"""Shared synthetic quotation research records and fake provider clients."""

from __future__ import annotations

import hashlib
import json

from semantic_alignment.quote_research_gemini import DeveloperResearchClient, VertexResearchClient

def row(number: int) -> dict:
    """Build a synthetic quotation with stable source and content identities."""
    text = f"Research quotation {number} about liberty and responsibility."
    digest = hashlib.sha256(text.encode()).hexdigest()
    return {"quote_id": digest, "quote_hash": digest, "quote_text": text,
            "source_occurrences": [{"line_number": number}], "research_status": "pending",
            "input_hash": hashlib.sha256(f"input:{number}".encode()).hexdigest()}


def manifest(count: int = 3) -> dict:
    """Build a deterministic research manifest for the requested row count."""
    payload = {"schema_version": 1, "record_kind": "test", "record_count": count,
               "source_occurrence_count": count, "records": [row(i) for i in range(1, count+1)]}
    payload["manifest_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return payload


def packet(record: dict) -> dict:
    """Supply a complete synthetic research packet for a manifest record."""
    return {"quote_id": record["quote_id"], "quote_text": record["quote_text"],
        "verification_status": "exact", "verified_text": record["quote_text"],
        "text_variation_notes": "No material variation.", "speaker": "Margaret Thatcher",
        "date": "unknown", "source_event": "unknown", "stable_locator": "unknown",
        "historical_context": "Context.", "immediate_subject": "Liberty.",
        "intended_argument": "Responsibility supports liberty.", "literal_meaning": "Liberty matters.",
        "broader_principle": "Responsibility.", "mechanism": "Limited government.",
        "claimed_consequence": "Liberty.", "entities": ["Margaret Thatcher"],
        "editorial_guidance": {"desired_first_impression": "Responsible liberty.",
            "historical_requirements": [], "must_be_visually_dominant": ["individual agency"],
            "must_not_dominate": ["generic conflict"], "common_visual_mistakes": ["irrelevant symbols"]},
        "research_confidence": "medium", "unresolved_questions": [],
        "sources": [{"title": "Transcript", "url": "https://example.test/transcript",
                     "source_type": "official", "supports": ["Context."]}]}


def response(record: dict, transport: str, malformed: bool = False) -> dict:
    """Wrap a packet in a provider response, optionally with malformed JSON."""
    return {"raw": {"candidates": [{"content": {"parts": [{"text": "{broken" if malformed else json.dumps(packet(record))}]}}]},
        "content": None if malformed else packet(record),
        "parse_error": "JSONDecodeError: broken" if malformed else None,
        "grounding": {"queries": ["query"], "supports": [{"chunk_indices": [0], "text": "Context."}],
            "sources": [{"index": 0, "title": "Transcript", "url": "https://example.test/transcript",
                         "supports": ["Context."]}], "search_entry_point": None},
        "usage": {"input_tokens": 100, "cached_tokens": 0, "reasoning_tokens": 10, "output_tokens": 200},
        "token_cost_usd": .003, "search_cost_usd_conservative": .014, "cost_usd": .017,
        "latency_seconds": .01, "request_id": f"{transport}-request"}


class FakeDeveloper(DeveloperResearchClient):
    """Consume scripted outcomes while retaining provider call accounting."""

    def __init__(self, outcomes, tracker=None):
        super().__init__("fake", request=lambda *a, **k: None)
        self.outcomes, self.calls, self.tracker = list(outcomes), 0, tracker

    def call(self, prompt):
        self.calls += 1
        if self.tracker: self.tracker.enter()
        try:
            value = self.outcomes.pop(0)
            if callable(value): value = value(prompt)
            if isinstance(value, BaseException): raise value
            return value
        finally:
            if self.tracker: self.tracker.leave()


class FakeVertex(VertexResearchClient):
    """Consume scripted Vertex outcomes without contacting a provider."""

    def __init__(self, outcomes, tracker=None):
        super().__init__("project", client=object())
        self.outcomes, self.calls, self.tracker = list(outcomes), 0, tracker

    def call(self, prompt):
        self.calls += 1
        if self.tracker: self.tracker.enter()
        try:
            value = self.outcomes.pop(0)
            if callable(value): value = value(prompt)
            if isinstance(value, BaseException): raise value
            return value
        finally:
            if self.tracker: self.tracker.leave()
