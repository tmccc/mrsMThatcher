#!/usr/bin/env python3
"""Source-grounded evidence records for conversational reply claims.

The production reply pipeline uses lexical matching only to assemble bounded
candidate passages.  Whether a passage entails a claim is decided separately
and is never inferred from token overlap.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from historical_context_formatter import (
    load_and_validate_corpus,
    packet_is_attributed_to_margaret_thatcher,
    select_primary_source,
)


EVIDENCE_REPOSITORY_VERSION = "claim-evidence-v2"
FACTUAL_EVIDENCE_SCHEMA_VERSION = 1
FACTUAL_EVIDENCE_SET_VERSION = "source-grounded-factual-evidence-v1"
PASSAGE_FIELDS = (
    "verified_text",
    "quote_text",
    "historical_context",
    "source_event",
    "immediate_subject",
    "intended_argument",
    "literal_meaning",
    "broader_principle",
    "mechanism",
    "claimed_consequence",
    "date",
    "speaker",
    "entities",
)
RETRIEVAL_FIELDS = (
    "quote_text",
    "verified_text",
    "source_event",
    "historical_context",
    "immediate_subject",
    "intended_argument",
    "literal_meaning",
    "broader_principle",
    "mechanism",
    "claimed_consequence",
)
AUTHORISED_QUOTATION_STATUSES = {"exact", "normalised", "excerpt", "variant"}
QUOTE_TEXT_SENTINELS = {"", "unknown", "unresolved", "no verified text available."}
RESOLVED_QUOTATION_FIELDS = (
    "quote_text",
    "verified_text",
    "verification_status",
    "research_confidence",
    "speaker",
    "source_event",
    "date",
    "historical_context",
    "literal_meaning",
    "intended_argument",
    "text_variation_notes",
    "stable_locator",
)
PREFERRED_PASSAGE_FIELDS = (
    "verified_text",
    "quote_text",
    "source_event",
    "date",
    "historical_context",
    "literal_meaning",
    "intended_argument",
    "immediate_subject",
    "broader_principle",
    "mechanism",
    "claimed_consequence",
    "speaker",
    "entities",
)
WORD_RE = re.compile(r"[^\W_]+(?:['\N{RIGHT SINGLE QUOTATION MARK}-][^\W_]+)*", re.UNICODE)
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "but", "by",
    "for", "from", "had", "has", "have", "he", "her", "hers", "him", "his",
    "i", "if", "in", "into", "is", "it", "its", "not", "of", "on", "or",
    "our", "she", "that", "the", "their", "them", "there", "they", "this", "to",
    "was", "we", "were", "what", "when", "where", "which", "who", "will", "with",
    "would", "you", "your",
}


def canonical_json(value: Any) -> bytes:
    """Return stable UTF-8 JSON bytes."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def value_hash(value: Any) -> str:
    """Return a stable SHA-256 digest for structured data."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def text_hash(value: str) -> str:
    """Return the SHA-256 digest of exact UTF-8 text."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def normalise_words(value: Any) -> tuple[str, ...]:
    """Return case-folded words for candidate retrieval and quote matching."""
    return tuple(word.casefold().replace("\N{RIGHT SINGLE QUOTATION MARK}", "'") for word in WORD_RE.findall(str(value or "")))


def retrieval_tokens(value: Any) -> set[str]:
    """Return non-trivial words used only to shortlist candidate passages."""
    return {word for word in normalise_words(value) if len(word) >= 3 and word not in STOPWORDS}


@dataclass(frozen=True)
class EvidencePassage:
    """A source-backed local passage that an evidence model may cite."""

    evidence_id: str
    source_hash: str
    quote_id: str
    field: str
    passage: str
    source_title: str
    source_url: str
    stable_locator: str
    verification_status: str
    research_confidence: str
    actor: str = ""
    action_or_relationship: str = ""
    direction_or_polarity: str = ""
    date_or_period: str = ""
    quantity: str = ""

    def prompt_record(self) -> dict[str, Any]:
        """Return the bounded record supplied to the evidence model."""
        return {
            "evidence_id": self.evidence_id,
            "quote_id": self.quote_id,
            "field": self.field,
            "passage": self.passage,
            "source_title": self.source_title,
            "source_url": self.source_url,
            "stable_locator": self.stable_locator,
            "verification_status": self.verification_status,
            "research_confidence": self.research_confidence,
            "actor": self.actor,
            "action_or_relationship": self.action_or_relationship,
            "direction_or_polarity": self.direction_or_polarity,
            "date_or_period": self.date_or_period,
            "quantity": self.quantity,
        }

    def model_input_hash(self) -> str:
        """Bind persisted approval to every evidence field supplied to a model."""
        return value_hash(self.prompt_record())


@dataclass(frozen=True)
class RetrievedEvidence:
    """A lexical packet result retained for the offline hybrid benchmark."""

    quote_id: str
    score: float
    verification_status: str
    summary: str
    packet: dict[str, Any]

    def prompt_record(self) -> dict[str, Any]:
        """Return a compact packet record for offline comparison tools."""
        return {
            "quote_id": self.quote_id,
            "verification_status": self.verification_status,
            "research_confidence": self.packet.get("research_confidence", "low"),
            "source_event": self.packet.get("source_event", ""),
            "date": self.packet.get("date", ""),
            "immediate_subject": self.packet.get("immediate_subject", ""),
            "intended_argument": self.packet.get("intended_argument", ""),
            "broader_principle": self.packet.get("broader_principle", ""),
            "mechanism": self.packet.get("mechanism", ""),
            "claimed_consequence": self.packet.get("claimed_consequence", ""),
            "verified_text": self.packet.get("verified_text", ""),
        }


class EvidenceRepository:
    """Validated local research corpus indexed as immutable evidence passages."""

    def __init__(
        self,
        research_dir: Path,
        *,
        factual_evidence_path: Path | None = None,
    ):
        """Load and index the complete validated local quotation research corpus."""
        self.research_dir = Path(research_dir)
        self.factual_evidence_path = (
            Path(factual_evidence_path) if factual_evidence_path is not None else None
        )
        packets, unresolved = load_and_validate_corpus(self.research_dir)
        if not packets:
            raise RuntimeError("reply evidence requires at least one completed packet")
        self.packets = {
            quote_id: packet
            for quote_id, packet in packets.items()
            if packet_is_attributed_to_margaret_thatcher(packet)
        }
        if not self.packets:
            raise RuntimeError("reply evidence has no attribution-eligible completed packets")
        self.completed_packet_count = len(packets)
        self.unresolved_packet_count = len(unresolved)
        self.attribution_eligible_packet_count = len(self.packets)
        self.passages: dict[str, EvidencePassage] = {}
        self._passage_tokens: dict[str, set[str]] = {}
        self._authorised_quote_texts: dict[str, str] = {}
        self._authorised_quote_words: dict[str, tuple[str, ...]] = {}
        self._quote_match_texts: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {}
        self._passages_by_quote: dict[str, list[EvidencePassage]] = {}
        self.factual_evidence_count = 0
        self._build_indexes()
        if self.factual_evidence_path is not None:
            self._build_factual_indexes(self.factual_evidence_path)

    def _build_indexes(self) -> None:
        for quote_id, packet in sorted(self.packets.items()):
            primary = select_primary_source(packet) or {}
            source_record = {
                "quote_id": quote_id,
                "source_title": str(primary.get("title") or ""),
                "source_url": str(primary.get("url") or ""),
                "stable_locator": str(packet.get("stable_locator") or ""),
                "verification_status": str(packet.get("verification_status") or ""),
                "research_confidence": str(packet.get("research_confidence") or "low"),
                "sources": packet.get("sources", []),
            }
            source_hash = value_hash(source_record)
            for field in PASSAGE_FIELDS:
                raw = packet.get(field)
                if isinstance(raw, list):
                    text = "; ".join(str(item).strip() for item in raw if str(item).strip())
                else:
                    text = " ".join(str(raw or "").split())
                if not text:
                    continue
                evidence_id = value_hash({
                    "version": EVIDENCE_REPOSITORY_VERSION,
                    "quote_id": quote_id,
                    "field": field,
                    "passage": text,
                    "source_hash": source_hash,
                })
                passage = EvidencePassage(
                    evidence_id=evidence_id,
                    source_hash=source_hash,
                    quote_id=quote_id,
                    field=field,
                    passage=text,
                    source_title=str(primary.get("title") or ""),
                    source_url=str(primary.get("url") or ""),
                    stable_locator=str(packet.get("stable_locator") or ""),
                    verification_status=str(packet.get("verification_status") or ""),
                    research_confidence=str(packet.get("research_confidence") or "low"),
                )
                self.passages[evidence_id] = passage
                self._passage_tokens[evidence_id] = retrieval_tokens(text)
                self._passages_by_quote.setdefault(quote_id, []).append(passage)

            match_texts: list[tuple[str, tuple[str, ...]]] = []
            seen_match_words: set[tuple[str, ...]] = set()
            for field in ("verified_text", "quote_text"):
                text = " ".join(str(packet.get(field) or "").split())
                words = normalise_words(text)
                if text.casefold() in QUOTE_TEXT_SENTINELS or not words or words in seen_match_words:
                    continue
                seen_match_words.add(words)
                match_texts.append((field, words))
            self._quote_match_texts[quote_id] = tuple(match_texts)

            status = str(packet.get("verification_status") or "")
            verified = " ".join(str(packet.get("verified_text") or "").split())
            if status in AUTHORISED_QUOTATION_STATUSES and verified:
                self._authorised_quote_texts[quote_id] = verified
                self._authorised_quote_words[quote_id] = normalise_words(verified)

    def _build_factual_indexes(self, path: Path) -> None:
        """Load strict source-grounded factual passages from a versioned file."""
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"invalid factual reply evidence {path}: {exc}") from exc
        if not isinstance(document, dict) or set(document) != {
            "schema_version", "evidence_set_version", "records",
        }:
            raise RuntimeError("factual reply evidence document fields mismatch")
        if document["schema_version"] != FACTUAL_EVIDENCE_SCHEMA_VERSION:
            raise RuntimeError("factual reply evidence schema version mismatch")
        if document["evidence_set_version"] != FACTUAL_EVIDENCE_SET_VERSION:
            raise RuntimeError("factual reply evidence set version mismatch")
        records = document["records"]
        if not isinstance(records, list) or not records:
            raise RuntimeError("factual reply evidence must contain at least one record")

        expected = {
            "fact_id", "passage", "source_title", "publisher", "source_url",
            "stable_locator", "verification_status", "research_confidence",
            "retrieval_terms", "actor", "action_or_relationship",
            "direction_or_polarity", "date_or_period", "quantity",
        }
        seen_fact_ids: set[str] = set()
        for record in records:
            if not isinstance(record, dict) or set(record) != expected:
                raise RuntimeError("factual reply evidence record fields mismatch")
            fact_id = record.get("fact_id")
            if (
                not isinstance(fact_id, str)
                or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", fact_id)
                or fact_id in seen_fact_ids
            ):
                raise RuntimeError("factual reply evidence fact_id is invalid or duplicated")
            seen_fact_ids.add(fact_id)
            for field in expected - {"retrieval_terms"}:
                value = record.get(field)
                if not isinstance(value, str) or not value.strip() or value != value.strip():
                    raise RuntimeError(f"factual reply evidence {field} must be non-empty and trimmed")
            if record["verification_status"] != "official_source_exact":
                raise RuntimeError("factual reply evidence must use official_source_exact verification")
            if record["research_confidence"] != "high":
                raise RuntimeError("factual reply evidence must have high research confidence")
            parsed_url = urlsplit(record["source_url"])
            if parsed_url.scheme != "https" or not parsed_url.hostname:
                raise RuntimeError("factual reply evidence source URL must be HTTPS")
            terms = record["retrieval_terms"]
            if (
                not isinstance(terms, list)
                or not terms
                or any(
                    not isinstance(term, str) or not term.strip() or term != term.strip()
                    for term in terms
                )
            ):
                raise RuntimeError("factual reply evidence retrieval_terms are invalid")

            source_record = {
                "fact_id": fact_id,
                "source_title": record["source_title"],
                "publisher": record["publisher"],
                "source_url": record["source_url"],
                "stable_locator": record["stable_locator"],
                "verification_status": record["verification_status"],
                "research_confidence": record["research_confidence"],
            }
            source_hash = value_hash(source_record)
            quote_id = value_hash({"factual_record": fact_id})
            evidence_id = value_hash({
                "version": EVIDENCE_REPOSITORY_VERSION,
                "fact_id": fact_id,
                "passage": record["passage"],
                "source_hash": source_hash,
            })
            if evidence_id in self.passages:
                raise RuntimeError("factual reply evidence ID collides with an existing passage")
            passage = EvidencePassage(
                evidence_id=evidence_id,
                source_hash=source_hash,
                quote_id=quote_id,
                field="factual_evidence",
                passage=record["passage"],
                source_title=record["source_title"],
                source_url=record["source_url"],
                stable_locator=record["stable_locator"],
                verification_status=record["verification_status"],
                research_confidence=record["research_confidence"],
                actor=record["actor"],
                action_or_relationship=record["action_or_relationship"],
                direction_or_polarity=record["direction_or_polarity"],
                date_or_period=record["date_or_period"],
                quantity=record["quantity"],
            )
            self.passages[evidence_id] = passage
            retrieval_text = " ".join([record["passage"], *terms])
            self._passage_tokens[evidence_id] = retrieval_tokens(retrieval_text)
            self.factual_evidence_count += 1

    def candidate_passages(
        self,
        claim_text: str,
        *,
        maximum_packets: int = 6,
        maximum_passages: int = 24,
        preferred_quote_id: str | None = None,
        restrict_to_preferred_quote: bool = False,
    ) -> list[EvidencePassage]:
        """Return lexical candidates without asserting that they support the claim."""
        if restrict_to_preferred_quote and preferred_quote_id not in self.packets:
            raise ValueError("restricted evidence retrieval requires a known preferred quotation")
        query = retrieval_tokens(claim_text)
        if not query:
            return []
        ranked: list[tuple[int, int, str, EvidencePassage]] = []
        for evidence_id, passage in self.passages.items():
            overlap = query & self._passage_tokens[evidence_id]
            if not overlap:
                continue
            if passage.field == "factual_evidence":
                field_priority = -1
            elif passage.field in {"verified_text", "historical_context", "literal_meaning"}:
                field_priority = 0
            else:
                field_priority = 1
            ranked.append((-len(overlap), field_priority, evidence_id, passage))
        ranked.sort(key=lambda row: row[:3])
        selected: list[EvidencePassage] = []
        quote_ids: set[str] = set()
        if preferred_quote_id in self.packets:
            field_order = {
                field: index for index, field in enumerate(PREFERRED_PASSAGE_FIELDS)
            }
            preferred = sorted(
                self._passages_by_quote.get(str(preferred_quote_id), []),
                key=lambda passage: (
                    field_order.get(passage.field, len(field_order)),
                    passage.evidence_id,
                ),
            )
            selected.extend(preferred[:maximum_passages])
            if selected:
                quote_ids.add(str(preferred_quote_id))
        if restrict_to_preferred_quote:
            return selected
        selected_ids = {passage.evidence_id for passage in selected}
        for _score, _priority, _identifier, passage in ranked:
            if passage.evidence_id in selected_ids:
                continue
            if passage.quote_id not in quote_ids and len(quote_ids) >= maximum_packets:
                continue
            quote_ids.add(passage.quote_id)
            selected.append(passage)
            selected_ids.add(passage.evidence_id)
            if len(selected) >= maximum_passages:
                break
        return selected

    def resolve_context_quotation(self, context: dict[str, Any]) -> dict[str, Any] | None:
        """Resolve one source-grounded quotation from separated reply context."""
        contextual_sources: list[tuple[str, str]] = []
        quoted = context.get("quoted_post")
        if isinstance(quoted, dict):
            contextual_sources.append(("quoted_post", str(quoted.get("text") or "")))
        parents = context.get("parent_thread")
        if isinstance(parents, list):
            for parent in reversed(parents):
                if isinstance(parent, dict) and parent.get("author_role") == "account":
                    contextual_sources.append(
                        ("parent_thread", str(parent.get("text") or ""))
                    )

        incoming_match, incoming_ambiguous = self._strongest_quotation_match(
            str(context.get("incoming_contribution") or "")
        )
        if incoming_ambiguous:
            return None

        contextual_matches: list[tuple[str, tuple[str, str]]] = []
        for section, text in contextual_sources:
            match, ambiguous = self._strongest_quotation_match(text)
            if ambiguous:
                if incoming_match is None:
                    return None
                continue
            if match is not None:
                contextual_matches.append((section, match))

        if incoming_match is not None:
            incoming_quote_id, incoming_basis = incoming_match
            if contextual_matches and all(
                match[0] == incoming_quote_id for _section, match in contextual_matches
            ):
                section, (_quote_id, basis) = contextual_matches[0]
                return self._resolved_quotation_record(incoming_quote_id, section, basis)
            return self._resolved_quotation_record(
                incoming_quote_id,
                "incoming_contribution",
                incoming_basis,
            )

        if contextual_matches:
            section, (quote_id, basis) = contextual_matches[0]
            return self._resolved_quotation_record(quote_id, section, basis)
        return None

    def _strongest_quotation_match(
        self,
        text: str,
    ) -> tuple[tuple[str, str] | None, bool]:
        """Return a unique strongest match and whether the result was ambiguous."""
        matches = self._quotation_matches(text)
        if not matches:
            return None, False
        strongest = max(score for score, _quote_id, _basis in matches)
        strongest_matches = {
            (quote_id, basis)
            for score, quote_id, basis in matches
            if score == strongest
        }
        quote_ids = {quote_id for quote_id, _basis in strongest_matches}
        if len(quote_ids) != 1:
            return None, True
        quote_id = next(iter(quote_ids))
        basis = sorted(
            basis for candidate_id, basis in strongest_matches if candidate_id == quote_id
        )[0]
        return (quote_id, basis), False

    def _resolved_quotation_record(
        self,
        quote_id: str,
        section: str,
        basis: str,
    ) -> dict[str, Any]:
        """Build the source-bound resolved quotation record used by the pipeline."""
        packet = self.packets[quote_id]
        primary = select_primary_source(packet) or {}
        fields = {
            field: packet.get(field)
            for field in RESOLVED_QUOTATION_FIELDS
        }
        record = {
            "quote_id": quote_id,
            "matched_context_section": section,
            "match_basis": basis,
            **fields,
            "primary_source": {
                "title": str(primary.get("title") or ""),
                "url": str(primary.get("url") or ""),
            },
            "evidence_ids": [
                passage.evidence_id
                for passage in sorted(
                    self._passages_by_quote.get(quote_id, []),
                    key=lambda passage: (passage.field, passage.evidence_id),
                )
            ],
        }
        record["resolved_context_hash"] = value_hash(record)
        return record

    def _quotation_matches(self, text: str) -> list[tuple[int, str, str]]:
        """Return unique full or substantial exact-word quotation matches."""
        source_words = normalise_words(text)
        if not source_words:
            return []
        source_windows = {
            source_words[index:index + 12]
            for index in range(max(0, len(source_words) - 11))
        }
        matches: list[tuple[int, str, str]] = []
        for quote_id, candidates in self._quote_match_texts.items():
            best: tuple[int, str] | None = None
            for _field, candidate_words in candidates:
                if source_words == candidate_words:
                    candidate = (3, "exact_text")
                elif _contains_words(source_words, candidate_words):
                    candidate = (2, "full_text")
                elif len(candidate_words) >= 12 and any(
                    candidate_words[index:index + 12] in source_windows
                    for index in range(len(candidate_words) - 11)
                ):
                    candidate = (1, "unique_contiguous_excerpt")
                else:
                    continue
                if best is None or candidate[0] > best[0]:
                    best = candidate
            if best is not None:
                matches.append((best[0], quote_id, best[1]))
        return matches

    def validate_reference(self, evidence_id: str, exact_passage: str) -> EvidencePassage:
        """Resolve an evidence reference only when its saved passage is exact."""
        passage = self.passages.get(str(evidence_id))
        if passage is None:
            raise ValueError(f"unknown evidence_id: {evidence_id}")
        if exact_passage != passage.passage:
            raise ValueError(f"evidence passage mismatch: {evidence_id}")
        return passage

    def exact_quote_is_authorised(self, text: str) -> bool:
        """Return whether text is an exact contiguous authorised Thatcher excerpt."""
        candidate = " ".join(str(text or "").split())
        words = normalise_words(candidate)
        if len(words) < 4:
            return False
        for authorised in self._authorised_quote_texts.values():
            if _contains_exact_text(authorised, candidate):
                return True
        return False

    def exact_quote_occurs_in_reply(self, reply_text: str, exact_text: str) -> bool:
        """Return whether authorised wording occurs exactly at word boundaries."""
        reply = " ".join(str(reply_text or "").split())
        exact = " ".join(str(exact_text or "").split())
        return bool(exact and _contains_exact_text(reply, exact))

    def detected_authorised_quote_ids(self, reply_text: str) -> set[str]:
        """Find substantial corpus wording without depending on quote delimiters."""
        reply_words = normalise_words(reply_text)
        return {
            quote_id
            for quote_id, _start, _end in self._authorised_quote_match_spans(reply_words)
        }

    def detected_authorised_quote_ids_outside_exact(
        self,
        reply_text: str,
        declared_exact_text: str,
    ) -> set[str]:
        """Return corpus wording whose matched span is outside the declared quotation."""
        reply_words = normalise_words(reply_text)
        declared_words = normalise_words(declared_exact_text)
        declared_spans = _word_match_spans(reply_words, declared_words)
        if not declared_spans:
            return self.detected_authorised_quote_ids(reply_text)
        return {
            quote_id
            for quote_id, start, end in self._authorised_quote_match_spans(reply_words)
            if not any(
                declared_start <= start and end <= declared_end
                for declared_start, declared_end in declared_spans
            )
        }

    def _authorised_quote_match_spans(
        self,
        reply_words: tuple[str, ...],
    ) -> list[tuple[str, int, int]]:
        """Return substantial authorised-quotation matches as word spans."""
        if len(reply_words) < 4:
            return []
        matches: list[tuple[str, int, int]] = []
        for quote_id, authorised in self._authorised_quote_words.items():
            if 4 <= len(authorised) <= 12:
                matches.extend(
                    (quote_id, start, end)
                    for start, end in _word_match_spans(reply_words, authorised)
                )
                continue
            if len(reply_words) >= 8:
                for index in range(len(reply_words) - 7):
                    if _contains_words(authorised, reply_words[index:index + 8]):
                        matches.append((quote_id, index, index + 8))
        return matches


def _contains_words(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    width = len(needle)
    return any(haystack[index:index + width] == needle for index in range(len(haystack) - width + 1))


def _word_match_spans(
    haystack: tuple[str, ...],
    needle: tuple[str, ...],
) -> list[tuple[int, int]]:
    """Return all exact contiguous word spans as half-open indexes."""
    if not needle or len(needle) > len(haystack):
        return []
    width = len(needle)
    return [
        (index, index + width)
        for index in range(len(haystack) - width + 1)
        if haystack[index:index + width] == needle
    ]


def _word_continuation(character: str) -> bool:
    return bool(
        character.isalnum()
        or character in {"_", "-", "'", "\N{RIGHT SINGLE QUOTATION MARK}"}
        or unicodedata.category(character).startswith("M")
    )


def _contains_exact_text(haystack: str, needle: str) -> bool:
    """Return whether exact text occurs without entering or leaving a word."""
    start = haystack.find(needle)
    while start >= 0:
        end = start + len(needle)
        left_ok = (
            start == 0
            or not (
                _word_continuation(haystack[start - 1])
                and _word_continuation(needle[0])
            )
        )
        right_ok = (
            end == len(haystack)
            or not (
                _word_continuation(needle[-1])
                and _word_continuation(haystack[end])
            )
        )
        if left_ok and right_ok:
            return True
        start = haystack.find(needle, start + 1)
    return False


def _packet_text(packet: dict[str, Any]) -> str:
    values = [str(packet.get(field) or "") for field in RETRIEVAL_FIELDS]
    entities = packet.get("entities")
    if isinstance(entities, list):
        values.extend(str(item) for item in entities)
    return " ".join(values)


def retrieve_research_packets(
    incoming_text: str,
    research_dir: Path,
    *,
    maximum: int = 5,
) -> list[RetrievedEvidence]:
    """Return lexical packet candidates for the offline hybrid benchmark."""
    repository = EvidenceRepository(research_dir)
    query = retrieval_tokens(incoming_text)
    if not query:
        return []
    ranked: list[RetrievedEvidence] = []
    for quote_id, packet in repository.packets.items():
        overlap = query & retrieval_tokens(_packet_text(packet))
        if not overlap:
            continue
        entity_tokens = retrieval_tokens(" ".join(str(value) for value in packet.get("entities", [])))
        score = float(len(overlap)) + 1.5 * len(query & entity_tokens)
        summary = str(packet.get("intended_argument") or packet.get("broader_principle") or "").strip()
        ranked.append(
            RetrievedEvidence(
                quote_id,
                score,
                str(packet.get("verification_status") or ""),
                summary,
                packet,
            )
        )
    ranked.sort(key=lambda item: (-item.score, item.quote_id))
    return ranked[:maximum]
