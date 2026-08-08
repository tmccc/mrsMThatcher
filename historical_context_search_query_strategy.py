#!/usr/bin/env python3
"""Deterministic query planning for historical quotation source discovery.

The planner is deliberately separate from search and retrieval code.  It has no
network entry point, does not know guardrail source identities, and makes no
production changes.  Callers supply ordinary quotation text, documented
variants and authorised source metadata.  Corpus distinctiveness is calculated
from the authoritative 611-member eligible quotation set.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


POLICY_VERSION = "historical-context-query-strategy-v4"
QUERY_GENERATOR_VERSION = "historical-context-query-generator-v4"
MAXIMUM_QUERIES = 7
RESULTS_PER_QUERY = 10
EXPECTED_ELIGIBLE_CORPUS_SIZE = 611
MARRIAGE_DATE = dt.date(1951, 12, 13)

WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?")
CLAUSE_SPLIT_RE = re.compile(r"\s*(?:\.{3,}|…+|(?<=[.!?;:])\s+)\s*")
SENTENCE_SPLIT_RE = re.compile(r"\s*(?:\.{3,}|…+|(?<=[.!?])\s+)\s*")

# This is a fixed language-policy list.  Corpus rarity is calculated
# independently and is the principal distinctiveness signal.
COMMON_ENGLISH_WORDS = frozenset({
    "a", "about", "after", "again", "all", "also", "am", "an", "and",
    "any", "are", "as", "at", "back", "be", "because", "been", "before",
    "being", "believe", "but", "by", "can", "could", "country", "did",
    "do", "does", "done", "for", "from", "get", "give", "go", "good",
    "government", "had", "has", "have", "he", "her", "here", "hers", "him",
    "his", "how", "i", "if", "in", "into", "is", "it", "its", "just",
    "know", "less", "like", "make", "many", "may", "me", "might", "more",
    "most", "must", "my", "no", "not", "now", "of", "on", "one", "only",
    "or", "other", "our", "ours", "out", "over", "people", "ready", "said",
    "same", "say", "she", "should", "so", "some", "such", "than", "that",
    "the", "their", "theirs", "them", "then", "there", "these", "they",
    "think", "this", "those", "through", "to", "too", "under", "up", "us",
    "very", "was", "way", "we", "well", "were", "what", "when", "where",
    "which", "while", "who", "why", "will", "with", "would", "you", "your",
    "yours",
})
WEAK_ENDPOINTS = COMMON_ENGLISH_WORDS | frozenset({
    "although", "however", "rather", "shall", "unless", "until",
})
FIRST_PERSON_WORDS = frozenset({
    "i", "me", "mine", "my", "our", "ours", "us", "we",
})
POLITICAL_TERMS = frozenset({
    "cabinet", "capitalism", "communism", "communist", "conservative",
    "constitution", "democracy", "democratic", "election", "european",
    "freedom", "hansard", "ideology", "integration", "liberty", "marxism",
    "marxist", "minister", "parliament", "socialism", "socialist",
    "sovereignty", "taxation", "totalitarian", "trade", "union",
})
CONTEXT_ANCHORS = (
    ("dartford", "Dartford"),
    ("house of commons", "House of Commons"),
    ("house of lords", "House of Lords"),
    ("hansard", "Hansard"),
    ("parliament", "Hansard"),
    ("conference", "conference"),
    ("interview", "interview"),
    ("memoir", "memoir"),
    ("speech", "speech"),
)
FORMAL_ARCHIVE_MARKERS = (
    "archive catalogue", "archival catalogue", "authority record",
    "catalogue", "catalog", "finding aid", "fonds",
)
MRS_CONTEXT_MARKERS = (
    "article", "broadcast", "hansard", "house of commons", "house of lords",
    "interview", "newspaper", "parliament", "television",
)


class QueryStrategyError(RuntimeError):
    """A deterministic query plan or authoritative input is invalid."""


def canonical_json_bytes(value: Any) -> bytes:
    """Serialise a value as deterministic canonical JSON bytes."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    """Return the hexadecimal SHA-256 digest of bytes."""
    return hashlib.sha256(value).hexdigest()


def file_sha256(path: Path) -> str:
    """Return the hexadecimal SHA-256 digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    """Hash a value after canonical JSON serialisation."""
    return sha256_bytes(canonical_json_bytes(value))


def normalise_wording(value: str) -> str:
    """Normalise quotation typography and whitespace deterministically."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.translate(str.maketrans({
        "‘": "'", "’": "'", "“": '"', "”": '"',
        "–": "-", "—": "-", "−": "-", "\u00a0": " ",
    }))
    return re.sub(r"\s+", " ", text).strip()


def search_wording(value: str) -> str:
    """Return normalised wording safe inside a quoted search query."""
    return normalise_wording(value).replace('"', "'")


def fragment_source_wording(value: str) -> str:
    """Remove deterministic editorial marks before fragment selection."""
    text = search_wording(value)
    text = re.sub(r"\[[^\[\]]{1,120}\]", " ", text)
    text = re.sub(r"(?:\.\s*){3,}|…+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def word_tokens(value: str) -> list[str]:
    """Return case-folded lexical tokens."""
    return [
        match.group(0).casefold().replace("’", "'")
        for match in WORD_RE.finditer(value)
    ]


def source_tokens(value: str) -> list[str]:
    """Return source-cased lexical tokens after editorial normalisation."""
    return [
        match.group(0).replace("’", "'")
        for match in WORD_RE.finditer(fragment_source_wording(value))
    ]


def normalise_query(value: str) -> str:
    """Normalise a query for deterministic duplicate detection."""
    return re.sub(r"\s+", " ", normalise_wording(value).casefold()).strip()


def _ngrams(tokens: Sequence[str], minimum: int, maximum: int) -> Iterable[tuple[str, ...]]:
    for size in range(minimum, min(maximum, len(tokens)) + 1):
        for start in range(0, len(tokens) - size + 1):
            yield tuple(tokens[start:start + size])


@dataclass(frozen=True)
class CorpusDistinctivenessIndex:
    """Document frequencies for the exact eligible quotation corpus."""

    corpus_size: int
    token_document_frequency: Mapping[str, int]
    phrase_document_frequency: Mapping[str, int]
    source_text_sha256: str
    source_metadata: Mapping[str, Any]

    @classmethod
    def from_texts(
        cls,
        texts: Sequence[str],
        *,
        expected_count: int | None = EXPECTED_ELIGIBLE_CORPUS_SIZE,
        source_metadata: Mapping[str, Any] | None = None,
    ) -> "CorpusDistinctivenessIndex":
        """Build document-frequency indexes from an exact quotation sequence."""
        values = [normalise_wording(value) for value in texts]
        if expected_count is not None and len(values) != expected_count:
            raise QueryStrategyError(
                f"eligible corpus count differs: {len(values)} != {expected_count}"
            )
        if not values or any(not value for value in values):
            raise QueryStrategyError("eligible corpus contains an empty quotation")
        token_df: Counter[str] = Counter()
        phrase_df: Counter[str] = Counter()
        for value in values:
            tokens = word_tokens(value)
            token_df.update(set(tokens))
            phrase_df.update(
                " ".join(phrase)
                for phrase in set(_ngrams(tokens, 2, 6))
            )
        ordered_text_hash = sha256_bytes(canonical_json_bytes(values))
        return cls(
            corpus_size=len(values),
            token_document_frequency=dict(sorted(token_df.items())),
            phrase_document_frequency=dict(sorted(phrase_df.items())),
            source_text_sha256=ordered_text_hash,
            source_metadata=copy.deepcopy(dict(source_metadata or {})),
        )

    def token_idf_milli(self, token: str) -> int:
        """Return smoothed token inverse-document frequency in thousandths."""
        frequency = int(self.token_document_frequency.get(token.casefold(), 0))
        return int(round(
            1000.0 * (math.log((self.corpus_size + 1) / (frequency + 1)) + 1.0)
        ))

    def phrase_frequency(self, tokens: Sequence[str]) -> int:
        """Return the eligible-corpus document frequency of a short phrase."""
        folded = [token.casefold() for token in tokens]
        if not 2 <= len(folded) <= 6:
            return 0
        return int(self.phrase_document_frequency.get(" ".join(folded), 0))

    def summary(self) -> dict[str, Any]:
        """Return hash-bound metadata describing this corpus index."""
        body = {
            "corpus_size": self.corpus_size,
            "source_text_sha256": self.source_text_sha256,
            "source_metadata": copy.deepcopy(dict(self.source_metadata)),
            "token_document_frequency_sha256": canonical_hash(
                self.token_document_frequency
            ),
            "phrase_document_frequency_sha256": canonical_hash(
                self.phrase_document_frequency
            ),
        }
        return {**body, "index_sha256": canonical_hash(body)}


def load_eligible_corpus_index(
    root: Path | str,
    *,
    expected_count: int = EXPECTED_ELIGIBLE_CORPUS_SIZE,
) -> CorpusDistinctivenessIndex:
    """Load the exact runtime-eligible IDs and map them to canonical quote text."""
    root_path = Path(root).resolve()
    eligible_path = (
        root_path
        / "semantic_alignment_research"
        / "quote_attribution_cleanup_001"
        / "deployment_candidate"
        / "runtime_eligible_quote_manifest.json"
    )
    corpus_path = (
        root_path
        / "semantic_alignment_research"
        / "quote_research_full_001"
        / "corpus_manifest.json"
    )
    eligible_document = json.loads(eligible_path.read_text(encoding="utf-8"))
    corpus_document = json.loads(corpus_path.read_text(encoding="utf-8"))
    eligible_ids = eligible_document.get("runtime_eligible_quote_ids")
    eligible_count = eligible_document.get("runtime_eligible_quote_count")
    aliases = eligible_document.get("runtime_quote_aliases")
    resolved_ids = eligible_document.get("resolved_manifest_quote_ids")
    if (
        not isinstance(eligible_ids, list)
        or type(eligible_count) is not int
        or eligible_count != len(eligible_ids)
        or eligible_count != expected_count
        or len(set(eligible_ids)) != len(eligible_ids)
        or not all(re.fullmatch(r"[0-9a-f]{64}", str(value or "")) for value in eligible_ids)
        or not isinstance(aliases, Mapping)
        or not isinstance(resolved_ids, list)
        or len(resolved_ids) != expected_count
    ):
        raise QueryStrategyError("runtime-eligible quotation manifest is invalid")
    records = corpus_document.get("records")
    if not isinstance(records, list):
        raise QueryStrategyError("canonical corpus manifest is invalid")
    indexed: dict[str, str] = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise QueryStrategyError("canonical corpus record is invalid")
        quote_id = str(record.get("quote_id") or "")
        quote_text = str(record.get("quote_text") or "")
        if not quote_id or quote_id in indexed:
            raise QueryStrategyError("canonical corpus quote identity is invalid")
        if sha256_bytes(quote_text.encode("utf-8")) != quote_id:
            raise QueryStrategyError(
                f"canonical quotation text hash differs: {quote_id}"
            )
        indexed[quote_id] = quote_text
    canonical_ids = [
        str(aliases.get(str(quote_id), quote_id))
        for quote_id in eligible_ids
    ]
    if (
        len(set(canonical_ids)) != expected_count
        or set(canonical_ids) != set(str(value) for value in resolved_ids)
    ):
        raise QueryStrategyError(
            "runtime aliases do not resolve to the reviewed manifest set"
        )
    missing = sorted(set(canonical_ids) - set(indexed))
    if missing:
        raise QueryStrategyError(
            f"eligible quotations are absent from the corpus manifest: {missing}"
        )
    # Runtime manifest ordering is authoritative and deliberately retained.
    texts = [indexed[quote_id] for quote_id in canonical_ids]
    return CorpusDistinctivenessIndex.from_texts(
        texts,
        expected_count=expected_count,
        source_metadata={
            "runtime_eligible_manifest": str(eligible_path.relative_to(root_path)),
            "runtime_eligible_manifest_sha256": file_sha256(eligible_path),
            "corpus_manifest": str(corpus_path.relative_to(root_path)),
            "corpus_manifest_sha256": file_sha256(corpus_path),
            "eligible_quote_ids_sha256": canonical_hash(eligible_ids),
            "resolved_quote_ids_sha256": canonical_hash(canonical_ids),
            "runtime_quote_aliases_sha256": canonical_hash(dict(aliases)),
        },
    )


def parse_source_date(value: str) -> dict[str, Any]:
    """Parse an authorised source date without inventing precision."""
    raw = str(value or "").strip()
    if not raw or raw.casefold() in {"unknown", "n/a", "none"}:
        return {
            "raw": raw,
            "known": False,
            "iso_date": "",
            "year": None,
            "precision": "unknown",
        }
    for pattern, precision in (
        ("%Y-%m-%d", "day"),
        ("%B %d, %Y", "day"),
        ("%d %B %Y", "day"),
        ("%Y", "year"),
    ):
        try:
            parsed = dt.datetime.strptime(raw, pattern)
        except ValueError:
            continue
        return {
            "raw": raw,
            "known": True,
            "iso_date": parsed.date().isoformat() if precision == "day" else "",
            "year": parsed.year,
            "precision": precision,
        }
    match = re.search(r"(?<!\d)(18|19|20)\d{2}(?!\d)", raw)
    if match:
        return {
            "raw": raw,
            "known": True,
            "iso_date": "",
            "year": int(match.group(0)),
            "precision": "year",
        }
    return {
        "raw": raw,
        "known": False,
        "iso_date": "",
        "year": None,
        "precision": "unparsed",
    }


def date_relation(parsed: Mapping[str, Any]) -> str:
    """Classify a parsed date relative to Thatcher's marriage date."""
    if not parsed.get("known"):
        return "unknown"
    iso_date = str(parsed.get("iso_date") or "")
    if iso_date:
        return (
            "pre_marriage"
            if dt.date.fromisoformat(iso_date) < MARRIAGE_DATE
            else "post_marriage"
        )
    year = parsed.get("year")
    if isinstance(year, int):
        if year < 1951:
            return "pre_marriage"
        if year > 1951:
            return "post_marriage"
    return "unknown"


def transformation_record(stored_text: str, variant_text: str) -> tuple[str, bool]:
    """Classify a documented wording transformation and its substance."""
    if word_tokens(stored_text) == word_tokens(variant_text):
        return "typography_or_punctuation_normalisation", False
    without_brackets = re.sub(r"\[[^\[\]]{1,120}\]", "", stored_text)
    if word_tokens(without_brackets) == word_tokens(variant_text):
        return "editorial_bracket_removal", False
    return "authoritative_verified_text_difference", True


def enrich_target_from_packet(
    target: Mapping[str, Any],
    packet: Mapping[str, Any] | None,
    *,
    packet_locator: str,
) -> dict[str, Any]:
    """Bind authorised packet metadata and documented wording to one target."""
    result = copy.deepcopy(dict(target))
    packet_value = dict(packet or {})
    quote_text = str(result.get("quotation_text") or "")
    if not quote_text:
        raise QueryStrategyError("target quotation text is absent")
    if packet_value and str(packet_value.get("quote_text") or "") != quote_text:
        raise QueryStrategyError("packet quotation text differs from target")
    parsed_date = parse_source_date(str(packet_value.get("date") or ""))
    metadata = {
        "packet_available": bool(packet_value),
        "packet_locator": packet_locator if packet_value else "",
        "date": parsed_date,
        "date_relation_to_marriage": date_relation(parsed_date),
        "source_event": str(packet_value.get("source_event") or ""),
        "stable_locator": str(packet_value.get("stable_locator") or ""),
        "source_type": str(packet_value.get("source_type") or ""),
        "entities": [
            str(value)
            for value in packet_value.get("entities", [])
            if isinstance(value, str) and value
        ],
        "verification_status": str(
            packet_value.get("verification_status") or ""
        ),
        "text_variation_notes": str(
            packet_value.get("text_variation_notes") or ""
        ),
    }
    variants: list[dict[str, Any]] = []
    verified_text = normalise_wording(str(packet_value.get("verified_text") or ""))
    if verified_text and verified_text != normalise_wording(quote_text):
        transformation, substantive = transformation_record(
            quote_text, verified_text,
        )
        variants.append({
            "original_stored_text": quote_text,
            "search_variant": verified_text,
            "variant_provenance": {
                "kind": "authoritative_research_packet_field",
                "path": f"{packet_locator}/verified_text",
                "stable_locator": metadata["stable_locator"],
                "verification_status": metadata["verification_status"],
                "text_variation_notes": metadata["text_variation_notes"],
            },
            "transformation_type": transformation,
            "substantive": substantive,
        })
    result["source_metadata"] = metadata
    result["documented_variant_records"] = variants
    return result


def _metadata_text(target: Mapping[str, Any]) -> str:
    metadata = target.get("source_metadata")
    if not isinstance(metadata, Mapping):
        return ""
    return " ".join([
        str(metadata.get("source_event") or ""),
        str(metadata.get("stable_locator") or ""),
        str(metadata.get("source_type") or ""),
        " ".join(str(value) for value in metadata.get("entities", [])),
        str(metadata.get("text_variation_notes") or ""),
    ]).casefold()


def alias_eligibility(target: Mapping[str, Any]) -> dict[str, Any]:
    """Derive deterministic identity-alias eligibility from authorised metadata."""
    metadata = target.get("source_metadata")
    if not isinstance(metadata, Mapping):
        metadata = {}
    parsed_date_value = metadata.get("date")
    if isinstance(parsed_date_value, Mapping):
        parsed_date = dict(parsed_date_value)
    else:
        parsed_date = parse_source_date(str(parsed_date_value or ""))
    relation = str(
        metadata.get("date_relation_to_marriage")
        or date_relation(parsed_date)
    )
    searchable = _metadata_text(target)
    explicit_roberts = (
        "margaret roberts" in searchable
        or "miss margaret roberts" in searchable
    )
    pre_marriage_context = "dartford" in searchable or "pre-marriage" in searchable
    roberts_allowed = (
        relation == "pre_marriage"
        or (
            relation == "unknown"
            and (explicit_roberts or pre_marriage_context)
        )
    )
    formal_archival = any(marker in searchable for marker in FORMAL_ARCHIVE_MARKERS)
    event = str(metadata.get("source_event") or "")
    mrs_allowed = (
        relation == "post_marriage"
        and any(marker in event.casefold() for marker in MRS_CONTEXT_MARKERS)
    )
    roberts_alias = (
        "Miss Margaret Roberts"
        if "miss margaret roberts" in searchable
        else "Margaret Roberts"
    )
    return {
        "Margaret Thatcher": {
            "eligible": True,
            "condition": "ordinary_attribution_lane",
            "reason": "default identity anchor",
        },
        "Margaret Hilda Thatcher": {
            "eligible": formal_archival,
            "runtime_fallback_eligible": True,
            "condition": (
                "archival_or_formal_authority_metadata"
                if formal_archival
                else "only_after_ordinary_stages_return_no_useful_result"
            ),
            "reason": (
                "authorised metadata indicates an archival or authority record"
                if formal_archival
                else "formal alias is not justified during ordinary stages"
            ),
        },
        "Margaret Roberts": {
            "eligible": roberts_allowed and roberts_alias == "Margaret Roberts",
            "condition": "pre_marriage_or_authorised_unknown_date_context",
            "reason": (
                "date or authorised metadata supports the pre-marriage name"
                if roberts_allowed
                else "no pre-marriage or Roberts/Dartford basis"
            ),
        },
        "Miss Margaret Roberts": {
            "eligible": roberts_allowed and roberts_alias == "Miss Margaret Roberts",
            "condition": "authorised_metadata_uses_miss_roberts",
            "reason": (
                "authorised metadata explicitly supports the Miss Roberts form"
                if roberts_allowed and roberts_alias == "Miss Margaret Roberts"
                else "authorised metadata does not support the Miss Roberts form"
            ),
        },
        "Mrs Margaret Thatcher": {
            "eligible": mrs_allowed,
            "condition": "post_marriage_contemporary_record_context",
            "reason": (
                "source metadata supports contemporary Mrs Thatcher usage"
                if mrs_allowed
                else "source metadata does not justify the Mrs Thatcher form"
            ),
        },
        "date_relation_to_marriage": relation,
    }


def context_anchor(target: Mapping[str, Any]) -> dict[str, str] | None:
    """Return one metadata-justified source-context search anchor."""
    metadata = target.get("source_metadata")
    if not isinstance(metadata, Mapping):
        return None
    event = str(metadata.get("source_event") or "")
    locator = str(metadata.get("stable_locator") or "")
    combined = f"{event} {locator}".casefold()
    for marker, anchor in CONTEXT_ANCHORS:
        if marker in combined:
            return {
                "anchor": anchor,
                "rationale": (
                    "authorised source-event or locator metadata contains "
                    f"{marker!r}"
                ),
            }
    return None


def _named_entity_proxy(tokens: Sequence[str]) -> int:
    count = 0
    for index, token in enumerate(tokens):
        folded = token.casefold()
        if (
            index > 0
            and token[:1].isupper()
            and folded not in COMMON_ENGLISH_WORDS
        ):
            count += 1
    return count


def fragment_metrics(
    value: str,
    index: CorpusDistinctivenessIndex,
) -> dict[str, Any]:
    """Score a fragment for anchored and source-neutral search use."""
    source = source_tokens(value)
    tokens = [token.casefold() for token in source]
    content = [token for token in tokens if token not in COMMON_ENGLISH_WORDS]
    idf_values = [index.token_idf_milli(token) for token in content]
    # A term occurring in only one of 611 quotations is still exceptionally
    # rare.  The margin includes document-frequency one while excluding terms
    # repeated across a material portion of the corpus.
    rare_threshold = index.token_idf_milli("__term_absent_from_corpus__") - 1000
    rare = [
        token for token in content
        if index.token_idf_milli(token) >= rare_threshold
    ]
    stopword_count = sum(token in COMMON_ENGLISH_WORDS for token in tokens)
    stopword_ratio = stopword_count / len(tokens) if tokens else 1.0
    first_person_count = sum(token in FIRST_PERSON_WORDS for token in tokens)
    first_person_only = (
        first_person_count > 0
        and len(content) <= 1
    )
    named_entities = _named_entity_proxy(source)
    political_term_count = sum(token in POLITICAL_TERMS for token in content)
    phrase_frequency = index.phrase_frequency(tokens) if 2 <= len(tokens) <= 6 else 0
    min_ngram_frequency = 0
    if len(tokens) >= 2:
        frequencies = [
            index.phrase_frequency(ngram)
            for ngram in _ngrams(tokens, 2, min(6, len(tokens)))
        ]
        present = [value for value in frequencies if value > 0]
        min_ngram_frequency = min(present) if present else 0
    average_idf = int(round(sum(idf_values) / len(idf_values))) if idf_values else 0
    weak_initial = bool(tokens and tokens[0] in WEAK_ENDPOINTS)
    weak_terminal = bool(tokens and tokens[-1] in WEAK_ENDPOINTS)
    generic_conversational = (
        first_person_only
        or (
            len(tokens) <= 6
            and len(content) <= 1
            and stopword_ratio >= 0.60
        )
    )
    score = (
        sum(idf_values)
        + 900 * len(rare)
        + 450 * named_entities
        + 300 * political_term_count
        + 120 * min(len(tokens), 14)
        - 450 * stopword_count
        - 900 * int(weak_initial)
        - 900 * int(weak_terminal)
        - 2500 * int(generic_conversational)
        - 120 * max(0, phrase_frequency - 1)
    )
    anchored_reasons: list[str] = []
    if len(tokens) < 4:
        anchored_reasons.append("below_four_words")
    if len(tokens) > 14:
        anchored_reasons.append("above_fourteen_words")
    if len(content) < 2:
        anchored_reasons.append("fewer_than_two_content_words")
    if weak_initial:
        anchored_reasons.append("weak_initial_word")
    if weak_terminal:
        anchored_reasons.append("weak_terminal_word")
    if generic_conversational:
        anchored_reasons.append("generic_conversational_language")

    source_neutral_reasons: list[str] = []
    if anchored_reasons:
        source_neutral_reasons.extend(anchored_reasons)
    if len(tokens) >= 7:
        if len(content) < 3:
            source_neutral_reasons.append("fewer_than_three_content_words")
        if len(rare) < 2 and average_idf < 1900:
            source_neutral_reasons.append("insufficient_corpus_rarity")
        if stopword_ratio > 0.60:
            source_neutral_reasons.append("stopword_ratio_above_0_60")
    elif len(tokens) >= 4:
        if len(rare) < 2:
            source_neutral_reasons.append(
                "short_phrase_lacks_two_exceptionally_rare_terms"
            )
        if average_idf < 2200:
            source_neutral_reasons.append(
                "short_phrase_average_rarity_too_low"
            )
    else:
        source_neutral_reasons.append("too_short_for_source_neutral_use")
    return {
        "text": " ".join(source),
        "word_count": len(tokens),
        "content_word_count": len(content),
        "content_words": content,
        "rare_content_word_count": len(rare),
        "rare_content_words": rare,
        "stopword_count": stopword_count,
        "stopword_ratio": round(stopword_ratio, 6),
        "first_person_word_count": first_person_count,
        "first_person_only": first_person_only,
        "named_entity_proxy_count": named_entities,
        "political_term_count": political_term_count,
        "average_content_idf_milli": average_idf,
        "phrase_document_frequency": phrase_frequency,
        "minimum_ngram_document_frequency": min_ngram_frequency,
        "distinctiveness_score": score,
        "weak_initial_word": weak_initial,
        "weak_terminal_word": weak_terminal,
        "generic_conversational_phrase": generic_conversational,
        "anchored_fragment_acceptable": not anchored_reasons,
        "anchored_rejection_reasons": anchored_reasons,
        "source_neutral_fragment_acceptable": not source_neutral_reasons,
        "source_neutral_rejection_reasons": source_neutral_reasons,
    }


def _clause_token_ranges(value: str) -> list[dict[str, Any]]:
    normalised = search_wording(value)
    raw_clauses = [
        clause.strip(" \t\"'")
        for clause in CLAUSE_SPLIT_RE.split(normalised)
        if clause.strip(" \t\"'")
    ]
    ranges: list[dict[str, Any]] = []
    global_start = 0
    for clause_index, clause in enumerate(raw_clauses):
        tokens = source_tokens(clause)
        if not tokens:
            continue
        ranges.append({
            "clause_index": clause_index,
            "source_clause": clause,
            "tokens": tokens,
            "global_start": global_start,
            "global_end": global_start + len(tokens),
        })
        global_start += len(tokens)
    return ranges


def fragment_candidates(
    value: str,
    index: CorpusDistinctivenessIndex,
    *,
    minimum_words: int = 4,
    maximum_words: int = 14,
) -> list[dict[str, Any]]:
    """Return ranked contiguous fragments within documentary clause boundaries."""
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for clause in _clause_token_ranges(value):
        tokens = clause["tokens"]
        for size in range(
            minimum_words,
            min(maximum_words, len(tokens)) + 1,
        ):
            for start in range(0, len(tokens) - size + 1):
                phrase_tokens = tokens[start:start + size]
                phrase = " ".join(phrase_tokens)
                key = normalise_query(phrase)
                if key in seen:
                    continue
                seen.add(key)
                metrics = fragment_metrics(phrase, index)
                if not metrics["anchored_fragment_acceptable"]:
                    continue
                candidates.append({
                    "fragment_text": metrics["text"],
                    "source_text": value,
                    "source_clause": clause["source_clause"],
                    "clause_index": clause["clause_index"],
                    "token_start": clause["global_start"] + start,
                    "token_end": clause["global_start"] + start + size,
                    "clause_token_start": start,
                    "metrics": metrics,
                    "selection_score": [
                        metrics["distinctiveness_score"],
                        metrics["rare_content_word_count"],
                        metrics["average_content_idf_milli"],
                        -abs(size - 9),
                        -(clause["global_start"] + start),
                    ],
                })
    return sorted(
        candidates,
        key=lambda row: tuple(row["selection_score"]),
        reverse=True,
    )


def select_anchored_fragments(
    value: str,
    index: CorpusDistinctivenessIndex,
    *,
    maximum: int = 2,
) -> list[dict[str, Any]]:
    """Select non-overlapping fragments suitable for identity anchoring."""
    candidates = fragment_candidates(value, index)
    selected: list[dict[str, Any]] = []
    for candidate in candidates:
        if len(selected) >= maximum:
            break
        overlap = False
        for existing in selected:
            shared = max(
                0,
                min(existing["token_end"], candidate["token_end"])
                - max(existing["token_start"], candidate["token_start"]),
            )
            smaller = min(
                existing["token_end"] - existing["token_start"],
                candidate["token_end"] - candidate["token_start"],
            )
            if smaller and shared / smaller > 0.50:
                overlap = True
                break
        if not overlap:
            selected.append(copy.deepcopy(candidate))
    # Identity anchoring makes a conservative long-quotation fallback safe even
    # when every candidate has weak conversational endpoints.
    if not selected and len(source_tokens(value)) >= 10:
        tokens = source_tokens(value)
        size = min(10, len(tokens))
        best: dict[str, Any] | None = None
        for start in range(0, len(tokens) - size + 1):
            phrase = " ".join(tokens[start:start + size])
            metrics = fragment_metrics(phrase, index)
            score = metrics["distinctiveness_score"]
            if best is None or score > best["metrics"]["distinctiveness_score"]:
                metrics["anchored_fragment_acceptable"] = True
                metrics["anchored_rejection_reasons"] = []
                metrics["anchored_acceptance_override"] = (
                    "long_quotation_identity_anchored_fallback"
                )
                best = {
                    "fragment_text": metrics["text"],
                    "source_text": value,
                    "source_clause": fragment_source_wording(value),
                    "clause_index": 0,
                    "token_start": start,
                    "token_end": start + size,
                    "metrics": metrics,
                    "selection_score": [score, 0, 0, -abs(size - 9), -start],
                }
        if best is not None:
            selected.append(best)
    return selected


def _short_anchor_candidates(
    value: str,
    index: CorpusDistinctivenessIndex,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    sentences = [
        sentence.strip(" \t\"'")
        for sentence in SENTENCE_SPLIT_RE.split(search_wording(value))
        if sentence.strip(" \t\"'")
    ]
    global_start = 0
    for sentence_index, sentence in enumerate(sentences):
        tokens = source_tokens(sentence)
        for size in range(2, min(6, len(tokens)) + 1):
            for start in range(0, len(tokens) - size + 1):
                phrase = " ".join(tokens[start:start + size])
                key = normalise_query(phrase)
                if key in seen:
                    continue
                seen.add(key)
                metrics = fragment_metrics(phrase, index)
                content = metrics["content_word_count"]
                if (
                    content < 1
                    or metrics["weak_initial_word"]
                    or metrics["weak_terminal_word"]
                    or metrics["generic_conversational_phrase"]
                ):
                    continue
                score = (
                    metrics["distinctiveness_score"]
                    + 700 * metrics["rare_content_word_count"]
                    + 300 * int(metrics["phrase_document_frequency"] <= 1)
                )
                candidates.append({
                    "phrase": metrics["text"],
                    "token_start": global_start + start,
                    "token_end": global_start + start + size,
                    "source_sentence": sentence,
                    "sentence_index": sentence_index,
                    "metrics": metrics,
                    "selection_score": score,
                })
        global_start += len(tokens)
    return sorted(
        candidates,
        key=lambda row: (
            row["selection_score"],
            row["metrics"]["rare_content_word_count"],
            -row["token_start"],
        ),
        reverse=True,
    )


def select_multi_anchor(
    value: str,
    index: CorpusDistinctivenessIndex,
) -> dict[str, Any] | None:
    """Select two non-overlapping rare phrases for source-neutral discovery."""
    candidates = _short_anchor_candidates(value, index)
    best: dict[str, Any] | None = None
    for left_index, left in enumerate(candidates):
        for right in candidates[left_index + 1:]:
            if left["sentence_index"] != right["sentence_index"]:
                continue
            if not (
                left["token_end"] <= right["token_start"]
                or right["token_end"] <= left["token_start"]
            ):
                continue
            ordered = sorted([left, right], key=lambda row: row["token_start"])
            combined_content = sum(
                row["metrics"]["content_word_count"] for row in ordered
            )
            combined_rare = sum(
                row["metrics"]["rare_content_word_count"] for row in ordered
            )
            combined_words = sum(
                row["metrics"]["word_count"] for row in ordered
            )
            combined_stopwords = sum(
                row["metrics"]["stopword_count"] for row in ordered
            )
            stopword_ratio = combined_stopwords / combined_words
            if combined_content < 3 or combined_rare < 2 or stopword_ratio > 0.50:
                continue
            separation = max(
                ordered[1]["token_start"] - ordered[0]["token_end"], 0
            )
            score = (
                sum(row["selection_score"] for row in ordered)
                - 250 * min(separation, 10)
                - 4000 * int(ordered[0]["sentence_index"])
            )
            candidate = {
                "anchor_phrases": [row["phrase"] for row in ordered],
                "anchor_details": copy.deepcopy(ordered),
                "source_text": value,
                "source_neutral_fragment_acceptable": True,
                "source_neutral_rejection_reasons": [],
                "combined_content_word_count": combined_content,
                "combined_rare_content_word_count": combined_rare,
                "combined_stopword_ratio": round(stopword_ratio, 6),
                "distinctiveness_score": score,
            }
            if (
                best is None
                or candidate["distinctiveness_score"]
                > best["distinctiveness_score"]
            ):
                best = candidate
    return best


def documented_variant_records(target: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Validate and return provenance-bound documented wording variants."""
    rows = target.get("documented_variant_records")
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise QueryStrategyError("documented variant records are malformed")
    validated: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise QueryStrategyError("documented variant record is malformed")
        variant = copy.deepcopy(dict(row))
        provenance = variant.get("variant_provenance")
        if (
            not str(variant.get("search_variant") or "").strip()
            or not isinstance(provenance, Mapping)
            or not str(provenance.get("path") or "").strip()
            or not str(variant.get("transformation_type") or "").strip()
        ):
            raise QueryStrategyError(
                "documented variant lacks wording, provenance or transformation"
            )
        validated.append(variant)
    return validated


def select_source_neutral_query(
    target: Mapping[str, Any],
    index: CorpusDistinctivenessIndex,
) -> dict[str, Any] | None:
    """Select at most one guarded source-neutral clause or multi-anchor query."""
    source_rows: list[tuple[str, str, dict[str, Any] | None]] = [(
        str(target.get("quotation_text") or ""),
        "stored_quotation",
        None,
    )]
    for variant in documented_variant_records(target):
        source_rows.append((
            str(variant["search_variant"]),
            "documented_variant",
            variant,
        ))
    candidates: list[dict[str, Any]] = []
    for source_text, source_kind, variant in source_rows:
        multi = select_multi_anchor(source_text, index)
        if multi is not None:
            query = " ".join(
                f'"{search_wording(value)}"'
                for value in multi["anchor_phrases"]
            )
            candidates.append({
                "query": query,
                "query_category": "source_neutral_multi_anchor",
                "source_text": source_text,
                "source_text_kind": source_kind,
                "variant_provenance": copy.deepcopy(variant),
                "fragment_text": "",
                "multi_anchor": multi,
                "distinctiveness": {
                    "distinctiveness_score": multi["distinctiveness_score"],
                    "anchored_fragment_acceptable": True,
                    "source_neutral_fragment_acceptable": True,
                    "source_neutral_rejection_reasons": [],
                },
                "selection_score": multi["distinctiveness_score"] + 1200,
                "reason": (
                    "two non-overlapping rare phrase anchors preserve "
                    "source-neutral and misattribution recall"
                ),
            })
        singles = [
            row for row in fragment_candidates(source_text, index)
            if row["metrics"]["source_neutral_fragment_acceptable"]
        ]
        if singles:
            def single_score(row: Mapping[str, Any]) -> tuple[int, int, int]:
                """Rank concise rare clauses above long over-constrained spans."""
                metrics = row["metrics"]
                words = max(1, int(metrics["word_count"]))
                score = (
                    int(metrics["distinctiveness_score"]) // words
                    + 2000 * int(metrics["rare_content_word_count"])
                    + 1200 * int(metrics["named_entity_proxy_count"])
                    - 500 * abs(words - 9)
                    + 3500 * int(int(row.get("clause_token_start") or 0) <= 1)
                    - 40 * int(row.get("token_start") or 0)
                )
                return (
                    score,
                    int(metrics["rare_content_word_count"]),
                    -int(row.get("token_start") or 0),
                )

            best = max(singles, key=single_score)
            candidates.append({
                "query": f'"{search_wording(best["fragment_text"])}"',
                "query_category": "source_neutral_distinctive_clause",
                "source_text": source_text,
                "source_text_kind": source_kind,
                "variant_provenance": copy.deepcopy(variant),
                "fragment_text": best["fragment_text"],
                "multi_anchor": None,
                "distinctiveness": copy.deepcopy(best["metrics"]),
                "selection_score": single_score(best)[0],
                "reason": (
                    "exceptionally distinctive contiguous clause preserves "
                    "source-neutral and misattribution recall"
                ),
            })
    if not candidates:
        return None
    single_candidates = [
        row for row in candidates
        if row["query_category"] == "source_neutral_distinctive_clause"
    ]
    pool = single_candidates or candidates
    return max(
        pool,
        key=lambda row: (
            row["selection_score"],
            normalise_query(row["query"]),
        ),
    )


def _query_row(
    *,
    query: str,
    execution_round: int,
    lane: str,
    category: str,
    reason: str,
    alias: str | None = None,
    source_text: str,
    source_text_kind: str = "stored_quotation",
    fragment: str = "",
    distinctiveness: Mapping[str, Any] | None = None,
    multi_anchor: Mapping[str, Any] | None = None,
    variant: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
    alias_decision: Mapping[str, Any] | None = None,
    transmission_condition: str = "unless_decisive_primary_validated",
) -> dict[str, Any]:
    body = {
        "query": query,
        "execution_round": execution_round,
        "query_lane": lane,
        "query_category": category,
        "identity_alias": alias,
        "source_text": source_text,
        "source_text_kind": source_text_kind,
        "fragment_text": fragment,
        "fragment_distinctiveness": (
            copy.deepcopy(dict(distinctiveness))
            if distinctiveness is not None else None
        ),
        "multi_anchor": (
            copy.deepcopy(dict(multi_anchor))
            if multi_anchor is not None else None
        ),
        "variant_provenance": (
            copy.deepcopy(dict(variant)) if variant is not None else None
        ),
        "context_anchor": str(context.get("anchor") or "") if context else "",
        "context_anchor_rationale": (
            str(context.get("rationale") or "") if context else ""
        ),
        "alias_eligibility_decision": (
            copy.deepcopy(dict(alias_decision))
            if alias_decision is not None else None
        ),
        "reason": reason,
        "selection_or_rejection_reason": reason,
        "planned": True,
        "transmitted": False,
        "transmission_condition": transmission_condition,
        "early_stop_reason": "",
        "maximum_results": RESULTS_PER_QUERY,
    }
    body["query_id"] = canonical_hash({
        key: body[key]
        for key in (
            "query", "execution_round", "query_lane", "query_category",
            "identity_alias", "source_text_kind", "fragment_text",
            "multi_anchor", "variant_provenance", "context_anchor",
            "transmission_condition",
        )
    })
    return body


def _alias_fallback_row(
    target: Mapping[str, Any],
    index: CorpusDistinctivenessIndex,
    decisions: Mapping[str, Any],
) -> dict[str, Any] | None:
    fragments = select_anchored_fragments(
        str(target.get("quotation_text") or ""), index, maximum=1,
    )
    if not fragments:
        return None
    fragment = fragments[0]
    chosen_alias = ""
    for alias in (
        "Miss Margaret Roberts",
        "Margaret Roberts",
        "Mrs Margaret Thatcher",
        "Margaret Hilda Thatcher",
    ):
        decision = decisions.get(alias)
        if isinstance(decision, Mapping) and decision.get("eligible"):
            chosen_alias = alias
            break
    if not chosen_alias:
        return None
    decision = dict(decisions[chosen_alias])
    return _query_row(
        query=f'"{chosen_alias}" "{search_wording(fragment["fragment_text"])}"',
        execution_round=4,
        lane="attribution_discovery",
        category="conditional_identity_alias_fragment",
        reason=(
            "authorised date or source metadata justifies a conditional "
            "identity-alias fallback"
        ),
        alias=chosen_alias,
        source_text=str(target.get("quotation_text") or ""),
        fragment=fragment["fragment_text"],
        distinctiveness=fragment["metrics"],
        alias_decision=decision,
        transmission_condition=(
            "only_if_still_unresolved_and_no_useful_result_after_round_3"
        ),
    )


def build_query_plan(
    target: Mapping[str, Any],
    index: CorpusDistinctivenessIndex,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build and validate one staged, two-lane deterministic query plan."""
    quote_text = str(target.get("quotation_text") or "")
    if not quote_text:
        raise QueryStrategyError("target quotation text is absent")
    full = search_wording(quote_text)
    decisions = alias_eligibility(target)
    fragments = select_anchored_fragments(quote_text, index, maximum=2)
    source_neutral = select_source_neutral_query(target, index)
    variants = documented_variant_records(target)
    context = context_anchor(target)

    rows: list[dict[str, Any]] = [
        _query_row(
            query=f'"{full}"',
            execution_round=1,
            lane="source_and_misattribution_discovery",
            category="complete_quotation_unqualified",
            reason="complete stored quotation for transcript and source discovery",
            source_text=quote_text,
        ),
        _query_row(
            query=f'"Margaret Thatcher" "{full}"',
            execution_round=1,
            lane="attribution_discovery",
            category="complete_quotation_margaret_thatcher",
            reason="complete stored quotation with the default identity anchor",
            alias="Margaret Thatcher",
            source_text=quote_text,
            alias_decision=decisions["Margaret Thatcher"],
        ),
    ]
    if fragments:
        best = fragments[0]
        rows.append(_query_row(
            query=f'"Margaret Thatcher" "{search_wording(best["fragment_text"])}"',
            execution_round=2,
            lane="attribution_discovery",
            category="anchored_distinctive_fragment",
            reason=(
                "best corpus-distinctive fragment with the default identity anchor"
            ),
            alias="Margaret Thatcher",
            source_text=quote_text,
            fragment=best["fragment_text"],
            distinctiveness=best["metrics"],
            alias_decision=decisions["Margaret Thatcher"],
        ))
    if source_neutral is not None:
        rows.append(_query_row(
            query=source_neutral["query"],
            execution_round=2,
            lane="source_and_misattribution_discovery",
            category=source_neutral["query_category"],
            reason=source_neutral["reason"],
            source_text=source_neutral["source_text"],
            source_text_kind=source_neutral["source_text_kind"],
            fragment=source_neutral["fragment_text"],
            distinctiveness=source_neutral["distinctiveness"],
            multi_anchor=source_neutral["multi_anchor"],
            variant=source_neutral["variant_provenance"],
        ))

    tail_candidates: list[tuple[int, dict[str, Any]]] = []
    if variants:
        variant = variants[0]
        variant_text = search_wording(str(variant["search_variant"]))
        tail_candidates.append((100, _query_row(
            query=f'"Margaret Thatcher" "{variant_text}"',
            execution_round=3,
            lane="attribution_discovery",
            category="documented_variant_margaret_thatcher",
            reason="provenance-bound documented wording variant with identity anchor",
            alias="Margaret Thatcher",
            source_text=str(variant["search_variant"]),
            source_text_kind="documented_variant",
            variant=variant,
            alias_decision=decisions["Margaret Thatcher"],
        )))
    alias_row = _alias_fallback_row(target, index, decisions)
    if alias_row is not None:
        tail_candidates.append((90, alias_row))
    if len(fragments) > 1:
        second = fragments[1]
        tail_candidates.append((80, _query_row(
            query=(
                f'"Margaret Thatcher" '
                f'"{search_wording(second["fragment_text"])}"'
            ),
            execution_round=3,
            lane="attribution_discovery",
            category="second_anchored_distinctive_fragment",
            reason=(
                "second non-overlapping corpus-distinctive identity-qualified fragment"
            ),
            alias="Margaret Thatcher",
            source_text=quote_text,
            fragment=second["fragment_text"],
            distinctiveness=second["metrics"],
            alias_decision=decisions["Margaret Thatcher"],
        )))
    if context and fragments:
        best = fragments[0]
        tail_candidates.append((70, _query_row(
            query=(
                f'"Margaret Thatcher" "{search_wording(best["fragment_text"])}" '
                f'{context["anchor"]}'
            ),
            execution_round=3,
            lane="attribution_discovery",
            category="metadata_justified_source_context",
            reason="authorised source metadata justifies one context-qualified query",
            alias="Margaret Thatcher",
            source_text=quote_text,
            fragment=best["fragment_text"],
            distinctiveness=best["metrics"],
            context=context,
            alias_decision=decisions["Margaret Thatcher"],
        )))
    slots = MAXIMUM_QUERIES - len(rows)
    selected_tail = [
        row for _priority, row in sorted(
            tail_candidates,
            key=lambda item: (
                item[0],
                -item[1]["execution_round"],
                item[1]["query"],
            ),
            reverse=True,
        )[:max(slots, 0)]
    ]
    rows.extend(selected_tail)
    rows.sort(key=lambda row: (
        row["execution_round"],
        {
            "complete_quotation_unqualified": 0,
            "complete_quotation_margaret_thatcher": 1,
            "anchored_distinctive_fragment": 0,
            "source_neutral_multi_anchor": 1,
            "source_neutral_distinctive_clause": 1,
            "second_anchored_distinctive_fragment": 0,
            "documented_variant_margaret_thatcher": 1,
            "metadata_justified_source_context": 2,
            "conditional_identity_alias_fragment": 0,
        }.get(row["query_category"], 9),
        row["query"],
    ))
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        key = normalise_query(str(row["query"]))
        if key in seen:
            continue
        seen.add(key)
        item = copy.deepcopy(row)
        item["request_order"] = len(unique) + 1
        unique.append(item)
    diagnostics = {
        "query_generator_version": QUERY_GENERATOR_VERSION,
        "corpus_index": index.summary(),
        "alias_eligibility": copy.deepcopy(decisions),
        "anchored_fragment_candidates_considered": len(
            fragment_candidates(quote_text, index)
        ),
        "selected_anchored_fragments": copy.deepcopy(fragments),
        "source_neutral_query_selected": copy.deepcopy(source_neutral),
        "source_neutral_query_count": sum(
            row["query_lane"] == "source_and_misattribution_discovery"
            and row["query_category"] != "complete_quotation_unqualified"
            for row in unique
        ),
        "documented_variant_count": len(variants),
        "context_anchor": copy.deepcopy(context),
        "queries_before_deduplication_and_cap": len(rows),
        "queries_after_deduplication_and_cap": len(unique),
        "formal_alias_runtime_fallback_available": True,
    }
    validate_query_plan(target, unique, diagnostics, index)
    return unique, diagnostics


def append_runtime_formal_alias_fallback(
    target: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    index: CorpusDistinctivenessIndex,
    *,
    earlier_useful_result: bool,
    decisive_primary_validated: bool,
) -> list[dict[str, Any]]:
    """Add a formal-name round-four fallback only after unsuccessful stages."""
    result = [copy.deepcopy(dict(row)) for row in rows]
    if earlier_useful_result or decisive_primary_validated or len(result) >= MAXIMUM_QUERIES:
        return result
    if any(row.get("identity_alias") == "Margaret Hilda Thatcher" for row in result):
        return result
    fragments = select_anchored_fragments(
        str(target.get("quotation_text") or ""), index, maximum=1,
    )
    if not fragments:
        return result
    decisions = alias_eligibility(target)
    decision = copy.deepcopy(decisions["Margaret Hilda Thatcher"])
    decision["eligible"] = True
    decision["condition"] = "ordinary_stages_returned_no_useful_result"
    decision["reason"] = "formal alias admitted only as a failed-recall fallback"
    fragment = fragments[0]
    row = _query_row(
        query=(
            f'"Margaret Hilda Thatcher" '
            f'"{search_wording(fragment["fragment_text"])}"'
        ),
        execution_round=4,
        lane="attribution_discovery",
        category="conditional_formal_alias_fallback",
        reason="ordinary exact and fragment stages returned no useful result",
        alias="Margaret Hilda Thatcher",
        source_text=str(target.get("quotation_text") or ""),
        fragment=fragment["fragment_text"],
        distinctiveness=fragment["metrics"],
        alias_decision=decision,
        transmission_condition=(
            "only_if_still_unresolved_and_no_useful_result_after_round_3"
        ),
    )
    row["request_order"] = len(result) + 1
    result.append(row)
    validate_query_plan(target, result, None, index)
    return result


def decisive_primary_review(review: Mapping[str, Any] | None) -> bool:
    """Return true only for fetched, inspected and validated primary evidence."""
    if not isinstance(review, Mapping):
        return False
    return (
        review.get("fetched") is True
        and review.get("inspected") is True
        and review.get("validated") is True
        and str(review.get("source_classification") or "")
        == "strong_primary_evidence"
        and str(review.get("outcome") or "") in {
            "exact_primary_wording_found",
            "primary_variant_found",
        }
        and review.get("snippet_only") is not True
    )


def queries_for_execution_round(
    rows: Sequence[Mapping[str, Any]],
    execution_round: int,
    *,
    decisive_primary_validated: bool,
    useful_result_found: bool,
) -> list[dict[str, Any]]:
    """Select one round without allowing a title or snippet to stop execution."""
    if decisive_primary_validated:
        return []
    selected: list[dict[str, Any]] = []
    for raw in rows:
        row = copy.deepcopy(dict(raw))
        if int(row.get("execution_round") or 0) != execution_round:
            continue
        condition = str(row.get("transmission_condition") or "")
        if (
            execution_round == 4
            and "no_useful_result" in condition
            and useful_result_found
        ):
            continue
        row["transmitted"] = True
        selected.append(row)
    return selected


def plan_with_transmission_state(
    rows: Sequence[Mapping[str, Any]],
    transmitted_query_ids: Iterable[str],
    *,
    early_stop_reason: str = "",
) -> list[dict[str, Any]]:
    """Return a query plan annotated with durable transmission state."""
    transmitted = set(transmitted_query_ids)
    result: list[dict[str, Any]] = []
    for raw in rows:
        row = copy.deepcopy(dict(raw))
        row["transmitted"] = str(row.get("query_id") or "") in transmitted
        if not row["transmitted"] and early_stop_reason:
            row["early_stop_reason"] = early_stop_reason
        result.append(row)
    return result


def validate_query_plan(
    target: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    diagnostics: Mapping[str, Any] | None,
    index: CorpusDistinctivenessIndex,
) -> None:
    """Reject unsafe, inconsistent or non-deterministic query plans."""
    errors: list[str] = []
    quote_text = str(target.get("quotation_text") or "")
    if not rows:
        errors.append("query plan is empty")
    if len(rows) > MAXIMUM_QUERIES:
        errors.append("query plan exceeds seven logical queries")
    if [row.get("request_order") for row in rows] != list(range(1, len(rows) + 1)):
        errors.append("request order is not contiguous")
    normalised = [normalise_query(str(row.get("query") or "")) for row in rows]
    if len(normalised) != len(set(normalised)):
        errors.append("duplicate normalised queries")
    rounds = [int(row.get("execution_round") or 0) for row in rows]
    if rounds != sorted(rounds) or any(value not in {1, 2, 3, 4} for value in rounds):
        errors.append("execution rounds are invalid or unordered")
    full = search_wording(quote_text)
    expected_first = [
        f'"{full}"',
        f'"Margaret Thatcher" "{full}"',
    ]
    if [str(row.get("query") or "") for row in rows[:2]] != expected_first:
        errors.append("round-one complete quotation queries differ")
    identity_fragments = [
        row for row in rows
        if row.get("identity_alias") == "Margaret Thatcher"
        and row.get("fragment_text")
    ]
    if len(word_tokens(quote_text)) >= 10 and not identity_fragments:
        errors.append("long quotation lacks an identity-qualified fragment")
    source_neutral_fragments = [
        row for row in rows
        if row.get("query_lane") == "source_and_misattribution_discovery"
        and row.get("query_category") != "complete_quotation_unqualified"
    ]
    if len(source_neutral_fragments) > 1:
        errors.append("more than one source-neutral fragment query")
    decisions = alias_eligibility(target)
    context_count = 0
    for row in rows:
        query = str(row.get("query") or "")
        category = str(row.get("query_category") or "")
        lane = str(row.get("query_lane") or "")
        alias = row.get("identity_alias")
        if row.get("planned") is not True:
            errors.append(f"query is not marked planned: {query}")
        if row.get("transmitted") not in {True, False}:
            errors.append(f"query transmission state is invalid: {query}")
        if alias and not query.startswith(f'"{alias}" '):
            errors.append(f"identity alias/query mismatch: {query}")
        if alias in {"Margaret Roberts", "Miss Margaret Roberts", "Mrs Margaret Thatcher"}:
            decision = decisions.get(alias)
            if not isinstance(decision, Mapping) or not decision.get("eligible"):
                errors.append(f"identity alias is not eligible: {alias}")
        if alias == "Margaret Hilda Thatcher":
            decision = row.get("alias_eligibility_decision")
            if not isinstance(decision, Mapping) or not decision.get("eligible"):
                errors.append("formal alias lacks an eligible conditional decision")
        fragment = str(row.get("fragment_text") or "")
        metrics = row.get("fragment_distinctiveness")
        if fragment:
            if not isinstance(metrics, Mapping):
                errors.append(f"fragment metrics are absent: {query}")
            elif not metrics.get("anchored_fragment_acceptable"):
                errors.append(f"unacceptable fragment emitted: {query}")
        if lane == "source_and_misattribution_discovery" and category != (
            "complete_quotation_unqualified"
        ):
            multi = row.get("multi_anchor")
            if multi:
                if not multi.get("source_neutral_fragment_acceptable"):
                    errors.append(f"multi-anchor is not source-neutral: {query}")
                phrases = multi.get("anchor_phrases")
                if not isinstance(phrases, list) or len(phrases) != 2:
                    errors.append(f"multi-anchor phrase count differs: {query}")
            elif (
                not isinstance(metrics, Mapping)
                or not metrics.get("source_neutral_fragment_acceptable")
            ):
                errors.append(f"weak source-neutral fragment emitted: {query}")
            if alias:
                errors.append(f"source-neutral query has an identity alias: {query}")
        if category == "documented_variant_margaret_thatcher":
            variant = row.get("variant_provenance")
            if not isinstance(variant, Mapping):
                errors.append("documented variant lacks provenance")
            else:
                authorised = documented_variant_records(target)
                if not any(canonical_hash(item) == canonical_hash(variant) for item in authorised):
                    errors.append("documented variant differs from authorised metadata")
        if category == "metadata_justified_source_context":
            context_count += 1
            expected = context_anchor(target)
            if (
                not expected
                or row.get("context_anchor") != expected["anchor"]
                or row.get("context_anchor_rationale") != expected["rationale"]
            ):
                errors.append("source-context query lacks authorised justification")
    if context_count > 1:
        errors.append("more than one source-context query")
    if diagnostics is not None:
        if diagnostics.get("queries_after_deduplication_and_cap") != len(rows):
            errors.append("diagnostic query count differs")
        if diagnostics.get("corpus_index") != index.summary():
            errors.append("diagnostic corpus index differs")
    if errors:
        raise QueryStrategyError("; ".join(sorted(set(errors))))


def policy_document(index: CorpusDistinctivenessIndex) -> dict[str, Any]:
    """Return the hash-bound public description of the query policy."""
    body = {
        "document_kind": "historical_context_query_strategy_policy",
        "policy_version": POLICY_VERSION,
        "query_generator_version": QUERY_GENERATOR_VERSION,
        "maximum_logical_queries_per_quotation": MAXIMUM_QUERIES,
        "results_per_query": RESULTS_PER_QUERY,
        "corpus_index": index.summary(),
        "query_lanes": [
            "attribution_discovery",
            "source_and_misattribution_discovery",
        ],
        "execution_rounds": {
            "1": [
                "complete quotation unqualified",
                "Margaret Thatcher plus complete quotation",
            ],
            "2": [
                "Margaret Thatcher plus best corpus-distinctive fragment",
                "one guarded source-neutral clause or multi-anchor",
            ],
            "3": [
                "second identity-qualified fragment where useful",
                "one provenance-bound documented variant",
                "one metadata-justified source-context query",
            ],
            "4": [
                "one conditional metadata/date-qualified identity alias",
                "formal alias only after ordinary stages return no useful result",
            ],
        },
        "distinctiveness_signals": [
            "inverse document frequency across exactly 611 eligible quotations",
            "fixed common-English word list",
            "phrase length",
            "rare content words and phrase document frequency",
            "named-entity and political-term proxies",
            "stopword ratio and weak endpoints",
            "first-person conversational language",
            "duplication across the eligible quotation corpus",
        ],
        "separate_thresholds": {
            "anchored_fragment_acceptable": (
                "permissive because Margaret Thatcher supplies identity precision"
            ),
            "source_neutral_fragment_acceptable": (
                "requires strong corpus rarity or two independently rare anchors"
            ),
        },
        "multi_anchor_policy": {
            "phrase_count": 2,
            "words_per_phrase": [2, 6],
            "non_overlapping": True,
            "boolean_or_used": False,
        },
        "alias_policy": alias_policy_document(),
        "documented_variant_provenance_required": True,
        "early_stop_policy": (
            "only fetched, inspected and validated exact or variant primary "
            "evidence may stop later rounds; titles and snippets may not"
        ),
        "network_capability": False,
        "guardrail_source_identities_available_to_generator": False,
    }
    return {**body, "sha256": canonical_hash(body)}


def alias_policy_document() -> dict[str, Any]:
    """Return the deterministic identity-alias policy description."""
    return {
        "default": "Margaret Thatcher",
        "formal": (
            "Margaret Hilda Thatcher only for formal/archival metadata or "
            "after ordinary stages return no useful result"
        ),
        "roberts_before": MARRIAGE_DATE.isoformat(),
        "unknown_date_roberts_requires_authorised_metadata": True,
        "mrs_requires_post_marriage_contemporary_metadata": True,
        "prohibited": ["Margaret Hilda Roberts", "The Prime Minister"],
    }


def query_plan_document(
    target: Mapping[str, Any],
    index: CorpusDistinctivenessIndex,
) -> dict[str, Any]:
    """Return a hash-bound manifest for one staged quotation query plan."""
    rows, diagnostics = build_query_plan(target, index)
    body = {
        "schema_version": 1,
        "document_kind": "historical_context_staged_query_plan",
        "query_generator_version": QUERY_GENERATOR_VERSION,
        "quote_id": str(target.get("quote_id") or ""),
        "quotation_text": str(target.get("quotation_text") or ""),
        "queries": rows,
        "diagnostics": diagnostics,
    }
    return {**body, "manifest_hash": canonical_hash(body)}


__all__ = [
    "CorpusDistinctivenessIndex",
    "EXPECTED_ELIGIBLE_CORPUS_SIZE",
    "MAXIMUM_QUERIES",
    "POLICY_VERSION",
    "QUERY_GENERATOR_VERSION",
    "QueryStrategyError",
    "alias_eligibility",
    "append_runtime_formal_alias_fallback",
    "build_query_plan",
    "context_anchor",
    "decisive_primary_review",
    "documented_variant_records",
    "enrich_target_from_packet",
    "fragment_candidates",
    "fragment_metrics",
    "load_eligible_corpus_index",
    "normalise_query",
    "plan_with_transmission_state",
    "policy_document",
    "queries_for_execution_round",
    "query_plan_document",
    "select_anchored_fragments",
    "select_multi_anchor",
    "select_source_neutral_query",
]
