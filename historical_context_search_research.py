#!/usr/bin/env python3
"""Deterministic, no-LLM evidence discovery for historical quotations.

The live command uses only an explicitly configured structured search API and
ordinary public-document retrieval.  Search snippets are discovery metadata,
never evidence.  The tool does not modify canonical research or production
state and does not make any live request without ``--execute-search``.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import html
import http.client
import ipaddress
import json
import math
import os
import posixpath
import re
import socket
import ssl
import subprocess
import tempfile
import time
import unicodedata
import zlib
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence
from urllib.parse import parse_qsl, quote, unquote_to_bytes, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from historical_context_project_root import (
    PROJECT_ROOT_ENV,
    ProjectRootError,
    resolve_project_root,
)
from historical_context_local_archive import (
    LOCAL_ARCHIVE_POLICY_VERSION,
    LOCAL_ARCHIVE_ROOT_ENV,
    LocalArchiveError,
    LocalArchiveMirror,
    LocalMTFDocumentIndex,
    MARGARET_THATCHER_FOUNDATION_PUBLISHER,
)

CODE_ROOT = Path(__file__).resolve().parent
# Compatibility alias for library callers. The CLI resolves an optional
# environment override once in ``main`` and passes that root explicitly.
ROOT = CODE_ROOT
RESEARCH_RELATIVE = Path("semantic_alignment_research/quote_research_full_001")
DEFAULT_QUERY_MANIFEST = CODE_ROOT / "historical_context_search_query_manifest.json"
DEFAULT_RESULTS_LEDGER = CODE_ROOT / "historical_context_search_results.jsonl"
DEFAULT_EVIDENCE = CODE_ROOT / "historical_context_search_evidence_candidates.json"
DEFAULT_REPORT = CODE_ROOT / "historical_context_search_research_report.md"
DEFAULT_CODEX_REVIEW = CODE_ROOT / "historical_context_search_codex_evidence_review.json"
DEFAULT_CACHE = CODE_ROOT / ".cache/historical_context_search_research/discovery_engine"
DEFAULT_RUN_STATE = DEFAULT_CACHE / "run_state.json"

PROGRAMME_VERSION = "historical-context-search-research-v7"
QUERY_POLICY_VERSION = "historical-context-search-queries-v4"
FETCH_POLICY_VERSION = "historical-context-restricted-fetch-v7"
CLASSIFICATION_POLICY_VERSION = "historical-context-evidence-classification-v6"
GOOGLE_BACKEND_VERSION = "google-custom-search-json-v2"
BRAVE_BACKEND_VERSION = "brave-web-search-json-v2"
DISCOVERY_ENGINE_BACKEND_VERSION = "google-discovery-engine-v1"
DISCOVERY_PROJECT_ID = "spatial-motif-393119"
DISCOVERY_LOCATION = "global"
DISCOVERY_COLLECTION = "default_collection"
DISCOVERY_ENGINE_ID = "thatcher-searcher_1784752895392"
DISCOVERY_DATA_STORE_ID = "thatcher-search-store_1784752779138"
DISCOVERY_SERVING_CONFIG = "default_serving_config"
DISCOVERY_ENDPOINT = (
    "https://discoveryengine.googleapis.com/v1/projects/"
    f"{DISCOVERY_PROJECT_ID}/locations/{DISCOVERY_LOCATION}/collections/"
    f"{DISCOVERY_COLLECTION}/engines/{DISCOVERY_ENGINE_ID}/servingConfigs/"
    f"{DISCOVERY_SERVING_CONFIG}:search"
)
ADC_ACCESS_TOKEN_COMMAND = (
    "gcloud", "auth", "application-default", "print-access-token", "--quiet",
)
BRAVE_QUERY_MAXIMUM_CHARACTERS = 400
BRAVE_QUERY_MAXIMUM_WORDS = 50
BRAVE_QUERY_SAFE_EXCERPT_WORDS = 48

EXPECTED_BLOCKED_COUNT = 18
EXPECTED_UNRESOLVED_COUNT = 5
TOP_RESULTS = 10
MAX_QUERIES_PER_QUOTE = 6
PLANNED_QUERY_STAGES_PER_QUOTE = 5
PLANNED_SEARCH_REQUESTS = 150
ABSOLUTE_SEARCH_REQUESTS = 150
MAXIMUM_PAGE_FETCHES = 400
WARNING_COST_USD = Decimal("2")
HARD_COST_USD = Decimal("5")
MAXIMUM_REDIRECTS = 5
MAXIMUM_RESPONSE_BYTES = 5 * 1024 * 1024
MAXIMUM_ROBOTS_BYTES = 512 * 1024
ROBOTS_CACHE_SECONDS = 24 * 60 * 60
MAXIMUM_ROBOTS_CRAWL_DELAY_SECONDS = 60.0
MAXIMUM_PUBLIC_ADDRESS_ATTEMPTS = 4
MAXIMUM_PDF_PAGES = 500
MAXIMUM_EXTRACTED_TEXT_CHARS = 2_000_000
USER_AGENT = "MrsMThatcher-Historical-Evidence-Research/1.0 (+offline editorial audit)"
ACCEPT_LANGUAGE = "en-GB,en;q=0.9"
DOCUMENT_ACCEPT = (
    "text/html,application/xhtml+xml,text/plain,application/pdf;q=0.8,"
    "application/json;q=0.6"
)
TRANSIENT_FETCH_STATUSES = {408, 425, 429, 500, 502, 503, 504}
MAXIMUM_FETCH_ATTEMPTS = 3

SEARCH_API_KEY_NAMES = (
    "GOOGLE_CUSTOM_SEARCH_API_KEY",
    "GOOGLE_SEARCH_API_KEY",
)
SEARCH_ENGINE_ID_NAMES = (
    "GOOGLE_CUSTOM_SEARCH_ENGINE_ID",
    "GOOGLE_SEARCH_ENGINE_ID",
    "GOOGLE_CSE_ID",
    "GOOGLE_CX",
)
BRAVE_API_KEY_NAMES = (
    "BRAVE_API_KEY",
    "BRAVE_SEARCH_API_KEY",
)
SEARCH_PRICE_NAME = "HISTORICAL_SEARCH_USD_PER_1000_REQUESTS"
SEARCH_FREE_QUOTA_NAME = "HISTORICAL_SEARCH_FREE_REQUESTS_REMAINING"
SEARCH_BACKEND_NAME = "HISTORICAL_SEARCH_BACKEND"

TRACKING_QUERY_KEYS = {
    "fbclid", "gclid", "dclid", "msclkid", "ref_src", "ref_url",
    "spm", "yclid", "mc_cid", "mc_eid", "igshid",
}
AGGREGATOR_MARKERS = {
    "allgreatquotes.com", "azquotes.com", "brainyquote.com", "goodreads.com",
    "hoopoequotes.com", "imgflip.com", "internetpoem.com", "libquotes.com",
    "magicalquote.com", "meaningin.com", "mindzip.net", "notable-quotes.com",
    "picturequotes.com", "quotefancy.com", "quotepark.com", "quotes.net",
    "quote-coyote.com", "quotegeek.com", "quotationspage.com", "quotetab.com",
    "quotery.com", "wikiquote.org", "wisesayings.com", "wonderfulquote.com",
}
PRIMARY_HOSTS = {
    "margaretthatcher.org", "hansard.parliament.uk", "api.parliament.uk",
    "parliament.uk", "gov.uk", "nationalarchives.gov.uk", "conservatives.com",
}
RELIABLE_SECONDARY_HOST_MARKERS = {
    "bbc.co.uk", "bbc.com", "britannica.com", "jstor.org", "ox.ac.uk",
    "cam.ac.uk", "bl.uk", "theguardian.com", "independent.co.uk",
    "telegraph.co.uk", "times.com", "reuters.com", "apnews.com",
    "quoteinvestigator.com",
}
SEARCH_RESULT_ONLY_HOSTS = {
    "google.com", "www.google.com", "bing.com", "www.bing.com",
}
BOT_X_ACCOUNT = "mrsmthatcher"
ALLOWED_CONTENT_TYPES = {
    "text/html", "application/xhtml+xml", "text/plain", "application/pdf",
    "application/json", "application/xml", "text/xml",
}
OUTCOMES = {
    "exact_primary_wording_found",
    "primary_variant_found",
    "contemporary_secondary_attribution_found",
    "secondary_recollection_found",
    "contradictory_or_misattributed",
    "promising_but_insufficient",
    "no_reliable_evidence_found",
    "search_incomplete_due_to_access",
    "search_incomplete_due_to_request_cap",
}
CODEX_REVIEW_REQUIRED_CHECKS = (
    "actual_passage_verified", "source_identity_verified", "speaker_verified",
    "wording_and_semantics_verified", "actor_direction_polarity_verified",
    "dates_and_quantities_verified", "match_type_verified",
)
CODEX_QUOTATION_REVIEW_OUTCOMES = {
    "promising_but_insufficient",
    "no_reliable_evidence_found",
    "search_incomplete_due_to_access",
    "search_incomplete_due_to_request_cap",
}
SOURCE_CLASSIFICATIONS = {
    "strong_primary_evidence",
    "reliable_secondary_evidence",
    "contemporary_report",
    "secondary_recollection",
    "discovery_only",
    "circular_attribution",
    "quotation_aggregation",
    "similar_sentiment_only",
    "contradictory_evidence",
    "inaccessible",
    "no_support",
}

_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?")
_CONTROL_OR_SPACE = re.compile(r"[\x00-\x20\x7f]")
_DOCUMENT_NUMBER = re.compile(r"\b(?:document|doc\.?)[\s:#-]*(\d{4,9})\b", re.I)
_DATE = re.compile(
    r"\b(?:(?:0?[1-9]|[12]\d|3[01])(?:st|nd|rd|th)?\s+)?"
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"(?:\s+(?:0?[1-9]|[12]\d|3[01])(?:st|nd|rd|th)?,?)?\s+(?:18|19|20)\d{2}\b|"
    r"\b(?:18|19|20)\d{2}-\d{2}-\d{2}\b",
    re.I,
)
_ATTRIBUTION_RE = re.compile(r"\b(?:Margaret\s+Thatcher|Mrs\s+Thatcher|Lady\s+Thatcher|Thatcher)\b", re.I)
_RECOLLECTION_RE = re.compile(r"\b(?:memoir|recollection|recalled|remembered|diary|autobiograph)\w*\b", re.I)
_CONTEMPORARY_RE = re.compile(r"\b(?:reported|interview|correspondent|newspaper|dispatch)\w*\b", re.I)
_CONTRADICTORY_RE = re.compile(
    r"\b(?:misattributed|wrongly attributed|not said by|actually said by|attributed instead to)\b",
    re.I,
)

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by",
    "for", "from", "had", "has", "have", "he", "her", "hers", "him",
    "his", "i", "if", "in", "into", "is", "it", "its", "not", "of",
    "on", "or", "our", "she", "that", "the", "their", "them", "there",
    "they", "this", "to", "was", "we", "were", "what", "when", "where",
    "which", "who", "will", "with", "would", "you", "your",
}


class ResearchError(RuntimeError):
    """Base error for deterministic research failures."""


class SearchBackendNotConfigured(ResearchError):
    """The required structured search backend is unavailable."""


class SearchBudgetExceeded(ResearchError):
    """A request would exceed an authorised search limit."""


class AmbiguousSearchRequest(ResearchError):
    """A transmitted request has no confirmed outcome and must not repeat."""


class RetryableSearchError(ResearchError):
    """A search service confirmed a retryable HTTP response."""


class ConfirmedSearchError(ResearchError):
    """A search service confirmed a non-retryable error response."""


class RejectedSearchQuery(ConfirmedSearchError):
    """The provider definitively rejected one query without an ambiguous outcome."""


class SearchAuthenticationConfigurationError(ConfirmedSearchError):
    """The selected search resource or its non-secret authentication is unusable."""


class UnsafeURL(ResearchError):
    """A URL is not safe for the restricted public fetcher."""


class TransientDNSFailure(UnsafeURL):
    """A public-host DNS lookup failed transiently before safety validation."""


class FetchLimitExceeded(ResearchError):
    """The page-fetch hard limit has been reached."""


class SearchBackend(Protocol):
    """Interface for a bounded structured search backend."""

    name: str
    version: str
    engine_identity_hash: str

    def search(self, query: str, *, number: int = TOP_RESULTS) -> list[dict[str, str]]:
        """Return structured result dictionaries for one query."""


@dataclass(frozen=True)
class DiscoveryEngineDescriptor:
    """Validated, non-secret identity for one Discovery Engine search app."""

    project_id: str
    location: str
    collection: str
    engine_id: str
    display_name: str
    data_store_ids: tuple[str, ...]
    solution_type: str
    search_tier: str
    serving_config: str = DISCOVERY_SERVING_CONFIG

    @property
    def endpoint(self) -> str:
        """Return the non-generative search endpoint for this engine."""
        return (
            "https://discoveryengine.googleapis.com/v1/projects/"
            f"{self.project_id}/locations/{self.location}/collections/"
            f"{self.collection}/engines/{self.engine_id}/servingConfigs/"
            f"{self.serving_config}:search"
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a stable configuration record containing no credential."""
        return {
            "project_id": self.project_id,
            "location": self.location,
            "collection": self.collection,
            "engine_id": self.engine_id,
            "display_name": self.display_name,
            "data_store_ids": list(self.data_store_ids),
            "solution_type": self.solution_type,
            "search_tier": self.search_tier,
            "serving_config": self.serving_config,
            "endpoint": self.endpoint,
        }


def discovery_engine_descriptor_from_api(
    resource: Mapping[str, Any],
    *,
    project_id: str = DISCOVERY_PROJECT_ID,
    location: str = DISCOVERY_LOCATION,
    collection: str = DISCOVERY_COLLECTION,
    serving_config: str = DISCOVERY_SERVING_CONFIG,
) -> DiscoveryEngineDescriptor:
    """Parse one listed engine without guessing any resource identifier."""
    name = str(resource.get("name") or "")
    prefix = (
        f"projects/{project_id}/locations/{location}/collections/"
        f"{collection}/engines/"
    )
    if not name.startswith(prefix):
        raise ResearchError("Discovery Engine resource name is outside the configured parent")
    engine_id = name.removeprefix(prefix)
    identifiers = (project_id, location, collection, engine_id, serving_config)
    if (
        "/" in engine_id
        or any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value)
            for value in identifiers
        )
    ):
        raise ResearchError("Discovery Engine resource identity is invalid")
    raw_data_store_ids = resource.get("dataStoreIds")
    if not isinstance(raw_data_store_ids, list) or not all(
        isinstance(value, str) and value for value in raw_data_store_ids
    ):
        raise ResearchError("Discovery Engine data-store attachment list is invalid")
    search_config = resource.get("searchEngineConfig")
    if not isinstance(search_config, Mapping):
        search_config = {}
    return DiscoveryEngineDescriptor(
        project_id=project_id,
        location=location,
        collection=collection,
        engine_id=engine_id,
        display_name=str(resource.get("displayName") or ""),
        data_store_ids=tuple(sorted(set(raw_data_store_ids))),
        solution_type=str(resource.get("solutionType") or ""),
        search_tier=str(
            search_config.get("searchTier") or resource.get("searchTier") or ""
        ),
        serving_config=serving_config,
    )


def select_discovery_engine_for_data_store(
    resources: Sequence[Mapping[str, Any]],
    data_store_id: str,
    *,
    project_id: str = DISCOVERY_PROJECT_ID,
    location: str = DISCOVERY_LOCATION,
    collection: str = DISCOVERY_COLLECTION,
    serving_config: str = DISCOVERY_SERVING_CONFIG,
) -> DiscoveryEngineDescriptor:
    """Require one search engine explicitly attached to the requested data store."""
    candidates: list[DiscoveryEngineDescriptor] = []
    for resource in resources:
        descriptor = discovery_engine_descriptor_from_api(
            resource,
            project_id=project_id,
            location=location,
            collection=collection,
            serving_config=serving_config,
        )
        if data_store_id in descriptor.data_store_ids:
            candidates.append(descriptor)
    if not candidates:
        raise SearchBackendNotConfigured(
            "no Discovery Engine is attached to the requested data store"
        )
    if len(candidates) != 1:
        raise SearchBackendNotConfigured(
            "more than one Discovery Engine is attached to the requested data store"
        )
    selected = candidates[0]
    if selected.solution_type != "SOLUTION_TYPE_SEARCH":
        raise SearchBackendNotConfigured(
            "the Discovery Engine attached to the requested data store is not a search solution"
        )
    if data_store_id not in selected.data_store_ids:
        raise AssertionError("selected Discovery Engine lost its requested data-store attachment")
    return selected


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON bytes."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    """Return a SHA-256 hex digest."""
    return hashlib.sha256(value).hexdigest()


def file_sha256(path: Path) -> str:
    """Hash one file without loading it all into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_bytes(path: Path, value: bytes, *, mode: int = 0o644) -> None:
    """Durably replace one file with bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, value: Any, *, mode: int = 0o644) -> None:
    """Durably replace one JSON file."""
    atomic_write_bytes(path, json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False,
    ).encode("utf-8") + b"\n", mode=mode)


def atomic_write_jsonl(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    """Durably regenerate a JSONL ledger from authoritative records."""
    payload = b"".join(canonical_json_bytes(record) + b"\n" for record in records)
    atomic_write_bytes(path, payload)


def normalise_wording(value: str) -> str:
    """Normalise typography and whitespace without inventing wording."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    translation = str.maketrans({
        "‘": "'", "’": "'", "“": '"', "”": '"',
        "–": "-", "—": "-", "−": "-", " ": " ",
    })
    return re.sub(r"\s+", " ", text.translate(translation)).strip()


def search_wording(value: str) -> str:
    """Return typography-normalised wording safe inside a quoted query."""
    return normalise_wording(value).replace('"', "'")


def fragment_source_wording(value: str) -> str:
    """Remove only deterministic editorial marks before fragment selection."""
    text = search_wording(value)
    text = re.sub(r"\[[^\[\]]{1,120}\]", " ", text)
    text = re.sub(r"(?:\.\s*){3,}|…", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def word_tokens(value: str) -> list[str]:
    """Return case-folded lexical tokens."""
    return [match.group(0).replace("’", "'").casefold() for match in _WORD_RE.finditer(value)]


def distinctive_fragments(value: str, *, count: int = 2) -> list[str]:
    """Choose stable, content-rich contiguous fragments of about ten words."""
    words = _WORD_RE.findall(fragment_source_wording(value))
    if not words:
        return []
    size = min(10, len(words))
    if size < 8:
        return [" ".join(words)]
    candidates: list[tuple[tuple[int, int, int, int], int, str]] = []
    for index in range(0, len(words) - size + 1):
        window = words[index:index + size]
        folded = [word.casefold() for word in window]
        content = [word for word in folded if word not in _STOPWORDS]
        score = (
            len(content), len(set(content)), sum(len(word) for word in content), -index,
        )
        candidates.append((score, index, " ".join(window)))
    chosen: list[tuple[int, str]] = []
    for _score, index, text in sorted(candidates, reverse=True):
        if any(abs(index - other) < max(4, size // 2) for other, _ in chosen):
            continue
        chosen.append((index, text))
        if len(chosen) >= count:
            break
    return [text for _index, text in chosen]


def deterministic_quote_clauses(value: str) -> list[str]:
    """Split only explicit editorial ellipses or substantial sentence boundaries."""
    text = search_wording(value)
    parts = re.split(r"\s*(?:\.{3,}|…+|\[\s*(?:\.{3}|…)?\s*\]|(?<=[.!?])\s+)\s*", text)
    cleaned = [re.sub(r"\[[^\[\]]{1,120}\]", " ", part) for part in parts]
    clauses = [
        re.sub(r"\s+", " ", part).strip(" \t\"'")
        for part in cleaned if len(word_tokens(part)) >= 3
    ]
    return clauses if len(clauses) >= 2 and any(len(word_tokens(part)) >= 6 for part in clauses) else []


def _manifest_records(path: Path) -> tuple[dict[str, dict[str, Any]], str]:
    raw = path.read_bytes()
    document = json.loads(raw)
    records = document.get("records") if isinstance(document, dict) else None
    count = document.get("record_count") if isinstance(document, dict) else None
    if not isinstance(records, list) or type(count) is not int or len(records) != count:
        raise ResearchError("canonical corpus manifest is invalid")
    indexed: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise ResearchError("canonical corpus manifest record is invalid")
        quote_id = str(record.get("quote_id") or "")
        quote_text = str(record.get("quote_text") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", quote_id) or quote_id in indexed:
            raise ResearchError("canonical corpus manifest quote identity is invalid")
        if sha256_bytes(quote_text.encode("utf-8")) != quote_id:
            raise ResearchError(f"canonical quote text hash differs: {quote_id}")
        indexed[quote_id] = record
    return indexed, sha256_bytes(raw)


def derive_target_set(root: Path = ROOT) -> dict[str, Any]:
    """Derive blocked and unresolved targets through authoritative validators."""
    root = root.resolve()
    research_dir = root / RESEARCH_RELATIVE
    from historical_context_formatter import (
        load_and_validate_corpus,
        packet_is_attributed_to_margaret_thatcher,
    )
    from historical_context_reply_semantic_gate import (
        load_historical_context_semantic_gate,
    )

    packets, unresolved = load_and_validate_corpus(
        research_dir, require_source_role_audit=True,
    )
    eligible = {
        quote_id for quote_id, packet in packets.items()
        if packet_is_attributed_to_margaret_thatcher(packet)
    }
    gate = load_historical_context_semantic_gate(
        root=root, eligible_quote_ids=eligible,
    )
    if not gate.available:
        raise ResearchError(f"authoritative semantic gate unavailable: {gate.reason}")
    manifest, manifest_sha = _manifest_records(research_dir / "corpus_manifest.json")
    blocked = dict(gate.blocked_dispositions)
    overlap = set(blocked) & set(unresolved)
    target_ids = sorted(set(blocked) | set(unresolved))
    if set(target_ids) - set(manifest):
        raise ResearchError("target quotation is absent from canonical manifest")

    targets: list[dict[str, Any]] = []
    for quote_id in target_ids:
        quote_text = str(manifest[quote_id]["quote_text"])
        packet = packets.get(quote_id)
        if packet is not None and packet.get("quote_text") != quote_text:
            raise ResearchError(f"completed packet quote text differs: {quote_id}")
        variants: list[str] = []
        if isinstance(packet, dict):
            for field in ("verified_text",):
                variant = normalise_wording(str(packet.get(field) or ""))
                if variant and variant != normalise_wording(quote_text) and variant not in variants:
                    variants.append(variant)
        origins = []
        if quote_id in blocked:
            origins.append("historical_context_semantic_gate")
        if quote_id in unresolved:
            origins.append("final_unresolved_status")
        clauses = deterministic_quote_clauses(quote_text)
        if clauses:
            fragments = [
                fragment
                for clause in clauses
                for fragment in distinctive_fragments(clause, count=1)
            ][:2]
        else:
            fragments = distinctive_fragments(quote_text)
        targets.append({
            "quote_id": quote_id,
            "quotation_text": quote_text,
            "target_origins": origins,
            "gate_disposition": blocked.get(quote_id),
            "recorded_variants": variants,
            "distinctive_fragments": fragments,
            "deterministic_clauses": clauses,
        })

    counts = {
        "completed_packet_count": len(packets),
        "attribution_eligible_count": len(eligible),
        "blocked_count": len(blocked),
        "blocked_expected_count": EXPECTED_BLOCKED_COUNT,
        "blocked_count_discrepancy": len(blocked) - EXPECTED_BLOCKED_COUNT,
        "unresolved_count": len(unresolved),
        "unresolved_expected_count": EXPECTED_UNRESOLVED_COUNT,
        "unresolved_count_discrepancy": len(unresolved) - EXPECTED_UNRESOLVED_COUNT,
        "overlap_count": len(overlap),
        "deduplicated_target_count": len(target_ids),
    }
    input_paths = {
        "production_quotation_corpus": root / "mrsMThatcher.txt",
        "production_quote_analysis": root / "quote_analysis.json",
        "corpus_manifest": research_dir / "corpus_manifest.json",
        "research_packets": research_dir / "research_packets.json",
        "final_research_status": research_dir / "final_unresolved/final_research_status.json",
        "unresolved_cases": research_dir / "final_unresolved/unresolved_cases.json",
        "semantic_review_ledger": root / "historical_context_published_reply_semantic_review.json",
        "source_role_audit": research_dir / "historical_context_source_role_audit.json",
        "historical_unresolved_state": research_dir / "unresolved_quotes.json",
        "runtime_eligible_quote_manifest": root / (
            "semantic_alignment_research/quote_attribution_cleanup_001/"
            "deployment_candidate/runtime_eligible_quote_manifest.json"
        ),
    }
    input_hashes = {name: file_sha256(path) for name, path in input_paths.items()}
    if input_hashes["corpus_manifest"] != manifest_sha:
        raise AssertionError("manifest hash calculation differs")
    return {
        "counts": counts,
        "gate": {
            "available": gate.available,
            "policy_version": getattr(__import__(
                "historical_context_reply_semantic_gate"
            ), "POLICY_VERSION"),
            "ledger_sha256": gate.ledger_sha256,
            "projection_sha256": gate.projection_sha256,
            "disposition_counts": dict(sorted(Counter(blocked.values()).items())),
        },
        "input_hashes": input_hashes,
        "targets": targets,
    }


def queries_for_target(target: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Generate only the permitted deterministic wording and attribution queries."""
    full = search_wording(str(target["quotation_text"]))
    fragments = list(target.get("distinctive_fragments") or distinctive_fragments(full))
    fragment = fragments[0] if fragments else full
    variants = [
        search_wording(str(value))
        for value in target.get("recorded_variants", [])
        if search_wording(str(value)) and search_wording(str(value)) != full
    ]
    second_fragment = fragments[1] if len(fragments) > 1 else ""
    query_rows = [
        (f'"{full}"', "exact_full_wording"),
        (f'"Margaret Thatcher" "{full}"', "margaret_thatcher_with_exact_full_wording"),
        (f'"{fragment}"', "distinctive_contiguous_fragment"),
        (f'"Margaret Thatcher" "{fragment}"', "margaret_thatcher_with_distinctive_fragment"),
    ]
    if variants:
        query_rows.append((f'"{variants[0]}"', "existing_documented_variant"))
    if second_fragment:
        query_rows.append((f'"{second_fragment}"', "second_distinctive_contiguous_fragment"))
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query, reason in query_rows:
        if query in seen:
            continue
        seen.add(query)
        row = {
            "request_order": len(unique) + 1,
            "query": query,
            "reason": reason,
            "maximum_results": TOP_RESULTS,
        }
        unique.append(row)
        if len(unique) == MAX_QUERIES_PER_QUOTE:
            break
    return unique


def build_query_manifest(root: Path = ROOT) -> dict[str, Any]:
    """Build a hash-bound deterministic query manifest."""
    derived = derive_target_set(root)
    targets: list[dict[str, Any]] = []
    for target in derived["targets"]:
        targets.append({**target, "queries": queries_for_target(target)})
    global_order: list[dict[str, Any]] = []
    maximum_depth = min(
        PLANNED_QUERY_STAGES_PER_QUOTE,
        max((len(target["queries"]) for target in targets), default=0),
    )
    for query_index in range(maximum_depth):
        for target in targets:
            if query_index >= len(target["queries"]):
                continue
            global_order.append({
                "global_request_order": len(global_order) + 1,
                "quote_id": target["quote_id"],
                "quote_request_order": query_index + 1,
            })
    body = {
        "schema_version": 1,
        "manifest_kind": "historical_context_search_query_manifest",
        "programme_version": PROGRAMME_VERSION,
        "query_policy_version": QUERY_POLICY_VERSION,
        "target_derivation": derived["counts"],
        "gate": derived["gate"],
        "input_hashes": derived["input_hashes"],
        "limits": {
            "top_results_per_query": TOP_RESULTS,
            "maximum_queries_per_quotation": MAX_QUERIES_PER_QUOTE,
            "planned_query_stages_per_quotation": PLANNED_QUERY_STAGES_PER_QUOTE,
            "maximum_planned_search_requests": PLANNED_SEARCH_REQUESTS,
            "scheduled_search_requests": len(global_order),
            "absolute_search_request_hard_stop": ABSOLUTE_SEARCH_REQUESTS,
            "maximum_unique_page_fetches": MAXIMUM_PAGE_FETCHES,
        },
        "stop_conditions": [
            "provisional exact or recorded-variant primary match pauses that quotation for Codex review",
            "review acceptance completes the quotation; review rejection resumes remaining stages",
            "150-request hard limit reached",
            "unique page-fetch limit reached",
            "Discovery Engine authentication or resource configuration unavailable",
        ],
        "request_scheduling": "breadth_first_by_query_stage_then_quote_id",
        "scheduled_query_policy": (
            "schedule at most five deterministic queries per quotation while enforcing "
            "the 150-request absolute hard cap"
        ),
        "targets": targets,
        "global_request_order": global_order,
    }
    return {**body, "manifest_hash": sha256_bytes(canonical_json_bytes(body))}


def validate_query_manifest(manifest: Mapping[str, Any]) -> None:
    """Validate a manifest's deterministic self-hash and hard limits."""
    value = dict(manifest)
    claimed = str(value.pop("manifest_hash", ""))
    actual = sha256_bytes(canonical_json_bytes(value))
    if not re.fullmatch(r"[0-9a-f]{64}", claimed) or claimed != actual:
        raise ResearchError("query manifest hash differs")
    targets = value.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ResearchError("query manifest has no targets")
    if any(len(target.get("queries") or []) > MAX_QUERIES_PER_QUOTE for target in targets):
        raise ResearchError("query manifest exceeds per-quotation query cap")
    quote_ids: list[str] = []
    for target in targets:
        if not isinstance(target, dict) or not target.get("quote_id") or not target.get("quotation_text"):
            raise ResearchError("query manifest target is malformed")
        quote_ids.append(str(target["quote_id"]))
        if target.get("queries") != queries_for_target(target):
            raise ResearchError("query manifest differs from deterministic query generation")
    if len(quote_ids) != len(set(quote_ids)):
        raise ResearchError("query manifest repeats a target quote ID")
    expected_order: list[dict[str, Any]] = []
    depth = min(
        PLANNED_QUERY_STAGES_PER_QUOTE,
        max((len(target["queries"]) for target in targets), default=0),
    )
    for index in range(depth):
        for target in targets:
            if index < len(target["queries"]):
                expected_order.append({
                    "global_request_order": len(expected_order) + 1,
                    "quote_id": target["quote_id"],
                    "quote_request_order": index + 1,
                })
    if value.get("global_request_order") != expected_order:
        raise ResearchError("query manifest global schedule is not deterministic")
    limits = value.get("limits")
    if limits is not None and limits != {
        "top_results_per_query": TOP_RESULTS,
        "maximum_queries_per_quotation": MAX_QUERIES_PER_QUOTE,
        "planned_query_stages_per_quotation": PLANNED_QUERY_STAGES_PER_QUOTE,
        "maximum_planned_search_requests": PLANNED_SEARCH_REQUESTS,
        "scheduled_search_requests": len(expected_order),
        "absolute_search_request_hard_stop": ABSOLUTE_SEARCH_REQUESTS,
        "maximum_unique_page_fetches": MAXIMUM_PAGE_FETCHES,
    }:
        raise ResearchError("query manifest hard limits differ from the programme")


def canonicalise_url(value: str) -> str:
    """Canonicalise one HTTP(S) result URL while preserving meaningful query data."""
    raw = str(value or "").strip()
    if not raw or _CONTROL_OR_SPACE.search(raw) or "\\" in raw:
        raise UnsafeURL("URL contains whitespace, control characters or backslashes")
    if re.search(r"%(?![0-9A-Fa-f]{2})", raw):
        raise UnsafeURL("URL contains a malformed percent escape")
    try:
        parsed = urlsplit(raw)
        parsed_hostname = parsed.hostname
    except (ValueError, UnicodeError) as exc:
        raise UnsafeURL("URL structure is invalid") from exc
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed_hostname:
        raise UnsafeURL("only absolute HTTP and HTTPS URLs are supported")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeURL("embedded URL credentials are forbidden")
    try:
        unquote_to_bytes(parsed.path or "/").decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise UnsafeURL("URL path contains invalid UTF-8") from exc
    scheme = parsed.scheme.casefold()
    try:
        host = parsed_hostname.encode("idna").decode("ascii").casefold().rstrip(".")
    except UnicodeError as exc:
        raise UnsafeURL("URL hostname is invalid") from exc
    if not host:
        raise UnsafeURL("URL hostname is empty")
    if host in {"margaretthatcher.org", "www.margaretthatcher.org"}:
        host = "www.margaretthatcher.org"
    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeURL("URL port is invalid") from exc
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = f"[{host}]" if ":" in host else host
    if port is not None and not default_port:
        netloc += f":{port}"
    unreserved = frozenset(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    )

    def decode_unreserved_escape(match: re.Match[str]) -> str:
        character = chr(int(match.group(1), 16))
        return character if character in unreserved else f"%{match.group(1).upper()}"

    preserved_path = re.sub(
        r"%([0-9A-Fa-f]{2})", decode_unreserved_escape, parsed.path or "/",
    )
    path = posixpath.normpath(re.sub(r"/{2,}", "/", preserved_path))
    if preserved_path.endswith("/") and path != "/":
        path += "/"
    if not path.startswith("/"):
        path = "/" + path
    if path != "/":
        path = path.rstrip("/")
    path = quote(path, safe="/%:@!$&'()*+,;=-._~")
    query_pairs = []
    try:
        parsed_query = parse_qsl(
            parsed.query, keep_blank_values=True, encoding="utf-8", errors="strict",
        )
    except UnicodeDecodeError as exc:
        raise UnsafeURL("URL query contains invalid UTF-8") from exc
    for key, item in parsed_query:
        folded = key.casefold()
        if folded.startswith("utm_") or folded in TRACKING_QUERY_KEYS:
            continue
        query_pairs.append((key, item))
    query_pairs.sort(key=lambda pair: (pair[0], pair[1]))
    query = urlencode(query_pairs, doseq=True)
    canonical = urlunsplit((scheme, netloc, path, query, ""))
    mtf_match = re.fullmatch(r"/document/(\d+)", path, re.I)
    if host == "www.margaretthatcher.org" and mtf_match:
        canonical = f"https://www.margaretthatcher.org/document/{mtf_match.group(1)}"
    return canonical


def _resolved_addresses(
    hostname: str,
    port: int,
    resolver: Callable[..., Any] = socket.getaddrinfo,
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Resolve and validate all addresses for one hostname."""
    try:
        literal = ipaddress.ip_address(hostname.strip("[]"))
        return [literal]
    except ValueError:
        pass
    try:
        answers = resolver(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise TransientDNSFailure(f"DNS resolution failed for {hostname}") from exc
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for answer in answers:
        try:
            address = ipaddress.ip_address(answer[4][0])
        except (IndexError, ValueError):
            continue
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise UnsafeURL(f"DNS resolution returned no addresses for {hostname}")
    return addresses


def validate_public_url(
    value: str,
    *,
    resolver: Callable[..., Any] = socket.getaddrinfo,
) -> str:
    """Return a canonical URL only when every resolved address is globally routable."""
    canonical = canonicalise_url(value)
    parsed = urlsplit(canonical)
    host = parsed.hostname or ""
    if host.casefold() in {"localhost", "localhost.localdomain"}:
        raise UnsafeURL("localhost URL rejected")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = _resolved_addresses(host, port, resolver)
    if any(
        not address.is_global
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or address.is_loopback
        or address.is_private
        or address.is_link_local
        for address in addresses
    ):
        raise UnsafeURL("URL resolves to a non-public address")
    return canonical


def deduplicate_results(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Annotate result rows with canonical identities and deterministic decisions."""
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for result in results:
        row = dict(result)
        try:
            canonical = canonicalise_url(str(row.get("result_url") or row.get("url") or ""))
        except UnsafeURL as exc:
            row.update({
                "canonical_url": "", "decision": "rejected_unsafe_result_url",
                "decision_reason": str(exc),
            })
        else:
            duplicate = canonical in seen
            seen.add(canonical)
            row.update({
                "canonical_url": canonical,
                "decision": "duplicate_result_url" if duplicate else "candidate_for_fetch",
                "decision_reason": "canonical URL already seen" if duplicate else "",
            })
        output.append(row)
    return output


def normalise_target_site_pattern(value: str) -> str:
    """Normalise only insignificant target-site spelling differences."""
    text = re.sub(r"^https?://", "", str(value or "").strip(), flags=re.I)
    host, separator, remainder = text.partition("/")
    if not host:
        raise ResearchError("target-site pattern has no host")
    return host.casefold() + (separator + remainder if separator else "")


def build_target_site_manifest(
    data_store_id: str,
    target_sites: Sequence[Mapping[str, Any]],
    *,
    required_include_count: int = 50,
) -> dict[str, Any]:
    """Validate and hash one data store's current INCLUDE target-site set."""
    include_rows: list[dict[str, Any]] = []
    for row in target_sites:
        if str(row.get("type") or "") != "INCLUDE":
            continue
        provided = str(row.get("providedUriPattern") or "")
        generated = str(row.get("generatedUriPattern") or "")
        identity_pattern = provided or generated
        if not identity_pattern:
            raise ResearchError("INCLUDE target site has no URI pattern")
        include_rows.append({
            "resource_name": str(row.get("name") or ""),
            "provided_uri_pattern": provided,
            "generated_uri_pattern": generated,
            "normalised_pattern": normalise_target_site_pattern(identity_pattern),
            "indexing_status": str(row.get("indexingStatus") or ""),
            "failure_reason": str(
                row.get("failureReason")
                or row.get("indexingFailureReason")
                or ""
            ),
        })
    normalised = [row["normalised_pattern"] for row in include_rows]
    if len(include_rows) != required_include_count:
        raise ResearchError(
            f"data store {data_store_id} has {len(include_rows)} INCLUDE target sites; "
            f"expected {required_include_count}"
        )
    if len(set(normalised)) != len(normalised):
        raise ResearchError(f"data store {data_store_id} has duplicate target-site patterns")
    ordered_rows = sorted(include_rows, key=lambda row: (
        row["normalised_pattern"], row["resource_name"],
    ))
    hash_body = {
        "data_store_id": data_store_id,
        "include_target_sites": ordered_rows,
    }
    return {
        **hash_body,
        "include_count": len(ordered_rows),
        "normalised_patterns": sorted(normalised),
        "explicit_indexing_failures": [
            row for row in ordered_rows
            if row["indexing_status"].casefold() == "failed"
        ],
        "manifest_sha256": sha256_bytes(canonical_json_bytes(hash_body)),
    }


def validate_dual_target_site_manifests(
    first: Mapping[str, Any],
    second: Mapping[str, Any],
    *,
    required_include_count: int = 50,
) -> dict[str, Any]:
    """Require two disjoint, complete website-search target-site collections."""
    manifests = (first, second)
    for manifest in manifests:
        if int(manifest.get("include_count") or 0) != required_include_count:
            raise ResearchError("target-site manifest INCLUDE count differs")
        claimed_hash = str(manifest.get("manifest_sha256") or "")
        body = {
            "data_store_id": manifest.get("data_store_id"),
            "include_target_sites": manifest.get("include_target_sites"),
        }
        if (
            not re.fullmatch(r"[0-9a-f]{64}", claimed_hash)
            or claimed_hash != sha256_bytes(canonical_json_bytes(body))
        ):
            raise ResearchError("target-site manifest hash differs")
    first_patterns = set(str(value) for value in first.get("normalised_patterns", []))
    second_patterns = set(str(value) for value in second.get("normalised_patterns", []))
    overlap = sorted(first_patterns & second_patterns)
    if overlap:
        raise ResearchError("target-site patterns overlap across the two data stores")
    return {
        "valid": True,
        "first_manifest_sha256": first["manifest_sha256"],
        "second_manifest_sha256": second["manifest_sha256"],
        "cross_store_normalised_overlap": [],
        "combined_manifest_sha256": sha256_bytes(canonical_json_bytes({
            "first": first["manifest_sha256"],
            "second": second["manifest_sha256"],
        })),
    }


def merge_dual_engine_results(
    logical_query: str,
    engine_batches: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Merge equivalent URLs while retaining each engine's original rank."""
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    engine_ids = {
        str(batch.get("engine_id") or "") for batch in engine_batches
        if str(batch.get("engine_id") or "")
    }
    if len(engine_ids) != len(engine_batches):
        raise ResearchError("dual-engine result batches must have distinct engine IDs")
    for batch in engine_batches:
        engine_id = str(batch.get("engine_id") or "")
        data_store_id = str(batch.get("data_store_id") or "")
        results = batch.get("results")
        if not engine_id or not data_store_id or not isinstance(results, list):
            raise ResearchError("dual-engine result batch is malformed")
        for rank, raw in enumerate(results[:TOP_RESULTS], start=1):
            if not isinstance(raw, Mapping):
                continue
            link = str(raw.get("result_url") or raw.get("link") or "")
            try:
                canonical = canonicalise_url(link)
            except UnsafeURL:
                continue
            per_engine = grouped.setdefault(canonical, {})
            if engine_id in per_engine:
                continue
            per_engine[engine_id] = {
                "engine_id": engine_id,
                "data_store_id": data_store_id,
                "rank": rank,
                "title": str(raw.get("title") or ""),
                "link": link,
                "snippet": str(raw.get("snippet") or ""),
                "mime": str(raw.get("mime") or ""),
            }
    merged: list[dict[str, Any]] = []
    for canonical, per_engine in grouped.items():
        engine_results = sorted(
            per_engine.values(), key=lambda row: (row["engine_id"], row["rank"]),
        )
        merged.append({
            "logical_query": logical_query,
            "engine_results": engine_results,
            "canonical_url": canonical,
            "appeared_in_both_engines": len(per_engine) == len(engine_ids) == 2,
            "best_rank": min(row["rank"] for row in engine_results),
        })
    return sorted(merged, key=lambda row: (row["best_rank"], row["canonical_url"]))


def execute_dual_engine_query(
    logical_query: str,
    backends: Sequence[SearchBackend],
    *,
    number: int = TOP_RESULTS,
) -> dict[str, Any]:
    """Execute one logical query once per engine and retain partial failures.

    This small orchestration primitive deliberately performs no retry, caching or
    evidence inference; callers supply those policies around it.  A valid empty
    result is distinct from a failed engine request, and a failure on one engine
    does not erase the other engine's response.
    """
    if len(backends) != 2:
        raise ResearchError("dual-engine execution requires exactly two backends")
    engine_ids = [str(getattr(backend, "engine_id", "") or "") for backend in backends]
    if not all(engine_ids) or len(set(engine_ids)) != 2:
        raise ResearchError("dual-engine execution requires two distinct engine IDs")
    batches: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for backend in backends:
        engine_id = str(getattr(backend, "engine_id"))
        data_store_id = str(getattr(backend, "data_store_id", "") or "")
        if not data_store_id:
            raise ResearchError("dual-engine backend has no unambiguous data-store ID")
        try:
            results = backend.search(logical_query, number=number)
        except (
            ConfirmedSearchError,
            RetryableSearchError,
            RejectedSearchQuery,
        ) as exc:
            results = []
            failures.append({
                "engine_id": engine_id,
                "data_store_id": data_store_id,
                "error_kind": type(exc).__name__,
                "error": str(exc),
            })
        batches.append({
            "engine_id": engine_id,
            "data_store_id": data_store_id,
            "results": [dict(row) for row in results],
        })
    return {
        "logical_query": logical_query,
        "engine_batches": batches,
        "merged_results": merge_dual_engine_results(logical_query, batches),
        "engine_failures": failures,
        "complete_two_engine_response": not failures,
    }


def decimal_value(value: Any, *, label: str) -> Decimal:
    """Parse one finite non-negative decimal."""
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SearchBackendNotConfigured(f"{label} must be a decimal") from exc
    if not result.is_finite() or result < 0:
        raise SearchBackendNotConfigured(f"{label} must be finite and non-negative")
    return result


@dataclass
class SearchBudget:
    """Durable accounting for potentially billed structured searches."""

    price_per_1000: Decimal
    pricing_known: bool = True
    free_quota_remaining_at_start: int = 0
    planned_maximum: int = PLANNED_SEARCH_REQUESTS
    absolute_maximum: int = ABSOLUTE_SEARCH_REQUESTS
    hard_cost_usd: Decimal = HARD_COST_USD
    unique_queries_started: int = 0
    requests_started: int = 0
    requests_completed: int = 0
    requests_ambiguous: int = 0
    retries: int = 0
    cache_hits: int = 0

    def _paid_request_count(self, total_started: int | None = None) -> int:
        total = self.requests_started if total_started is None else total_started
        return max(0, total - self.free_quota_remaining_at_start)

    def projected_cost(self, total_started: int | None = None) -> Decimal:
        """Return conservative cost for every request that may have transmitted."""
        paid = self._paid_request_count(total_started)
        return Decimal(paid) * self.price_per_1000 / Decimal(1000)

    def reserve_request(self, *, retry: bool = False) -> dict[str, Any]:
        """Reserve one request before transmission or refuse it."""
        projected_count = self.requests_started + 1
        projected_unique = self.unique_queries_started + (0 if retry else 1)
        if projected_count > self.absolute_maximum:
            raise SearchBudgetExceeded("absolute search-request hard stop reached")
        if projected_unique > self.planned_maximum:
            raise SearchBudgetExceeded("planned search-request maximum reached")
        projected_cost = self.projected_cost(projected_count)
        if projected_cost > self.hard_cost_usd:
            raise SearchBudgetExceeded("absolute search-cost hard stop would be exceeded")
        self.requests_started = projected_count
        if retry:
            self.retries += 1
        else:
            self.unique_queries_started = projected_unique
        return {
            "request_number": projected_count,
            "unique_query_number": projected_unique,
            "projected_cost_usd": str(projected_cost) if self.pricing_known else None,
            "remaining_authorised_budget_usd": (
                str(self.hard_cost_usd - projected_cost) if self.pricing_known else None
            ),
        }

    def complete_request(self) -> None:
        """Mark the latest reserved request confirmed complete."""
        self.requests_completed += 1

    def mark_ambiguous(self) -> None:
        """Count an unconfirmed transmitted request without making it repeatable."""
        self.requests_ambiguous += 1

    def as_dict(self) -> dict[str, Any]:
        """Return persisted accounting with no secret values."""
        cost = self.projected_cost()
        free_used = min(self.requests_started, self.free_quota_remaining_at_start)
        return {
            "price_per_1000_searches_usd": (
                str(self.price_per_1000) if self.pricing_known else None
            ),
            "search_cost_estimate_available": self.pricing_known,
            "free_quota_requests_known_at_start": self.free_quota_remaining_at_start,
            "free_quota_requests_used": free_used,
            "paid_or_conservatively_priced_requests": (
                self._paid_request_count() if self.pricing_known else None
            ),
            "unique_queries_started": self.unique_queries_started,
            "requests_started": self.requests_started,
            "requests_completed": self.requests_completed,
            "requests_ambiguous": self.requests_ambiguous,
            "retries": self.retries,
            "cache_hits": self.cache_hits,
            "estimated_search_cost_usd": str(cost) if self.pricing_known else None,
            "warning_threshold_usd": str(WARNING_COST_USD) if self.pricing_known else None,
            "hard_stop_usd": str(self.hard_cost_usd) if self.pricing_known else None,
            "warning_threshold_reached": cost >= WARNING_COST_USD if self.pricing_known else False,
            "remaining_authorised_budget_usd": (
                str(self.hard_cost_usd - cost) if self.pricing_known else None
            ),
            "planned_request_limit": self.planned_maximum,
            "absolute_request_hard_stop": self.absolute_maximum,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SearchBudget":
        """Restore accounting from a run-state dictionary."""
        price_value = value.get("price_per_1000_searches_usd")
        pricing_known = bool(value.get(
            "search_cost_estimate_available", price_value is not None,
        ))
        restored = cls(
            price_per_1000=(
                decimal_value(price_value, label="price")
                if price_value is not None else Decimal("0")
            ),
            pricing_known=pricing_known,
            free_quota_remaining_at_start=int(value.get("free_quota_requests_known_at_start", 0)),
            planned_maximum=PLANNED_SEARCH_REQUESTS,
            absolute_maximum=ABSOLUTE_SEARCH_REQUESTS,
            hard_cost_usd=HARD_COST_USD,
            unique_queries_started=int(
                value.get("unique_queries_started", value.get("requests_started", 0))
            ),
            requests_started=int(value.get("requests_started", 0)),
            requests_completed=int(value.get("requests_completed", 0)),
            requests_ambiguous=int(value.get("requests_ambiguous", 0)),
            retries=int(value.get("retries", 0)),
            cache_hits=int(value.get("cache_hits", 0)),
        )
        counters = (
            restored.free_quota_remaining_at_start,
            restored.unique_queries_started,
            restored.requests_started,
            restored.requests_completed,
            restored.requests_ambiguous,
            restored.retries,
            restored.cache_hits,
        )
        if any(counter < 0 for counter in counters):
            raise ResearchError("persisted search accounting contains a negative counter")
        if restored.unique_queries_started > PLANNED_SEARCH_REQUESTS:
            raise ResearchError("persisted unique-query count exceeds the fixed planned limit")
        if restored.requests_started > ABSOLUTE_SEARCH_REQUESTS:
            raise ResearchError("persisted request count exceeds the fixed absolute limit")
        if restored.requests_completed + restored.requests_ambiguous > restored.requests_started:
            raise ResearchError("persisted request outcomes exceed transmitted requests")
        if restored.retries > restored.requests_started:
            raise ResearchError("persisted retry count exceeds transmitted requests")
        if restored.projected_cost() > HARD_COST_USD:
            raise ResearchError("persisted conservative search cost exceeds the hard cap")
        return restored


class GoogleCustomSearchBackend:
    """Minimal Google Custom Search JSON API client with no AI dependency."""

    name = "google_custom_search_json"
    version = GOOGLE_BACKEND_VERSION
    endpoint = "https://www.googleapis.com/customsearch/v1"

    def __init__(
        self,
        api_key: str,
        engine_id: str,
        *,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (10.0, 30.0),
    ) -> None:
        """Configure a credential-bound Google Custom Search client."""
        if not api_key or not engine_id:
            raise SearchBackendNotConfigured("Google Custom Search key and engine ID are required")
        self._api_key = api_key
        self._engine_id = engine_id
        self.engine_identity_hash = sha256_bytes(engine_id.encode("utf-8"))
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.timeout = timeout

    def search(self, query: str, *, number: int = TOP_RESULTS) -> list[dict[str, str]]:
        """Make one structured API request and return at most ten results."""
        if number < 1 or number > TOP_RESULTS:
            raise ValueError("Google structured search result count must be 1..10")
        try:
            response = self.session.get(
                self.endpoint,
                params={"key": self._api_key, "cx": self._engine_id, "q": query, "num": number},
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise AmbiguousSearchRequest(
                f"Google structured search transport outcome is ambiguous: {type(exc).__name__}"
            ) from exc
        if 300 <= response.status_code < 400:
            raise ConfirmedSearchError("Google structured search redirect refused")
        if response.status_code >= 500 or response.status_code == 429:
            raise RetryableSearchError(
                f"Google structured search retryable HTTP {response.status_code}"
            )
        if response.status_code == 422:
            raise RejectedSearchQuery("Google structured search HTTP 422")
        if not response.ok:
            raise ConfirmedSearchError(
                f"Google structured search HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ResearchError("Google structured search returned invalid JSON") from exc
        items = payload.get("items", []) if isinstance(payload, dict) else []
        if not isinstance(items, list):
            raise ResearchError("Google structured search items are invalid")
        results: list[dict[str, str]] = []
        for item in items[:number]:
            if not isinstance(item, dict):
                continue
            results.append({
                "title": str(item.get("title") or ""),
                "result_url": str(item.get("link") or ""),
                "snippet": str(item.get("snippet") or ""),
            })
        return results


class GcloudADCAccessTokenProvider:
    """Obtain and briefly cache a local ADC access token without persisting it."""

    def __init__(
        self,
        *,
        command_runner: Callable[..., Any] = subprocess.run,
        cache_seconds: float = 40 * 60,
    ) -> None:
        """Configure the local ADC command and in-memory token lifetime."""
        self._command_runner = command_runner
        self._cache_seconds = cache_seconds
        self._token = ""
        self._refresh_before = 0.0

    def __call__(self) -> str:
        """Return a current token from ``gcloud auth application-default``."""
        now = time.monotonic()
        if self._token and now < self._refresh_before:
            return self._token
        command_environment = dict(os.environ)
        command_environment.pop("CLOUDSDK_CORE_LOG_HTTP", None)
        command_environment.pop("CLOUDSDK_LOG_HTTP", None)
        command_environment["CLOUDSDK_CORE_DISABLE_PROMPTS"] = "1"
        command_environment["CLOUDSDK_CORE_VERBOSITY"] = "error"
        try:
            completed = self._command_runner(
                list(ADC_ACCESS_TOKEN_COMMAND),
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
                stdin=subprocess.DEVNULL,
                env=command_environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SearchBackendNotConfigured(
                f"Discovery Engine ADC token command failed: {type(exc).__name__}"
            ) from None
        if int(getattr(completed, "returncode", 1)) != 0:
            raise SearchBackendNotConfigured(
                "Discovery Engine ADC token command returned a non-zero status"
            )
        token = str(getattr(completed, "stdout", "") or "").strip()
        if not token or len(token) > 8192 or any(character.isspace() for character in token):
            raise SearchBackendNotConfigured(
                "Discovery Engine ADC token command returned an invalid token"
            )
        self._token = token
        self._refresh_before = now + self._cache_seconds
        return token


class GoogleDiscoveryEngineResourceClient:
    """Read-only Discovery Engine resource discovery using local ADC."""

    api_root = "https://discoveryengine.googleapis.com/v1"

    def __init__(
        self,
        *,
        project_id: str = DISCOVERY_PROJECT_ID,
        location: str = DISCOVERY_LOCATION,
        collection: str = DISCOVERY_COLLECTION,
        access_token_provider: Callable[[], str] | None = None,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (10.0, 30.0),
    ) -> None:
        """Configure read-only Discovery Engine resource enumeration."""
        self.project_id = project_id
        self.location = location
        self.collection = collection
        self._access_token_provider = (
            access_token_provider or GcloudADCAccessTokenProvider()
        )
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.timeout = timeout

    @property
    def parent(self) -> str:
        """Return the fully qualified Discovery Engine collection parent."""
        return (
            f"projects/{self.project_id}/locations/{self.location}/collections/"
            f"{self.collection}"
        )

    def _get_paginated(
        self,
        url: str,
        *,
        collection_field: str,
        page_size: int,
    ) -> list[dict[str, Any]]:
        """Read every page while refusing redirects and opaque response shapes."""
        rows: list[dict[str, Any]] = []
        page_token = ""
        seen_tokens: set[str] = set()
        while True:
            parameters: dict[str, str | int] = {"pageSize": page_size}
            if page_token:
                parameters["pageToken"] = page_token
            try:
                response = self.session.get(
                    url,
                    params=parameters,
                    headers={
                        "Authorization": f"Bearer {self._access_token_provider()}",
                        "X-Goog-User-Project": self.project_id,
                        "User-Agent": USER_AGENT,
                        "Accept": "application/json",
                    },
                    timeout=self.timeout,
                    allow_redirects=False,
                )
            except requests.RequestException as exc:
                raise SearchBackendNotConfigured(
                    f"Discovery Engine resource-list request failed: {type(exc).__name__}"
                ) from None
            if 300 <= response.status_code < 400:
                raise SearchBackendNotConfigured(
                    "Discovery Engine resource-list redirect refused"
                )
            if response.status_code != 200:
                raise SearchBackendNotConfigured(
                    f"Discovery Engine resource-list HTTP {response.status_code}"
                )
            try:
                payload = response.json()
            except ValueError:
                raise SearchBackendNotConfigured(
                    "Discovery Engine resource-list response was invalid JSON"
                ) from None
            if not isinstance(payload, dict):
                raise SearchBackendNotConfigured(
                    "Discovery Engine resource-list response was not an object"
                )
            page_rows = payload.get(collection_field, [])
            if not isinstance(page_rows, list) or any(
                not isinstance(row, dict) for row in page_rows
            ):
                raise SearchBackendNotConfigured(
                    "Discovery Engine resource-list rows were invalid"
                )
            rows.extend(dict(row) for row in page_rows)
            next_token = str(payload.get("nextPageToken") or "")
            if not next_token:
                return rows
            if next_token in seen_tokens:
                raise SearchBackendNotConfigured(
                    "Discovery Engine resource-list pagination repeated a token"
                )
            seen_tokens.add(next_token)
            page_token = next_token

    def list_engines(self) -> list[dict[str, Any]]:
        """List all engines beneath the configured collection."""
        return self._get_paginated(
            f"{self.api_root}/{self.parent}/engines",
            collection_field="engines",
            page_size=100,
        )

    def discover_engine(
        self,
        data_store_id: str,
        *,
        serving_config: str = DISCOVERY_SERVING_CONFIG,
    ) -> DiscoveryEngineDescriptor:
        """Discover exactly one attached search app from current API state."""
        return select_discovery_engine_for_data_store(
            self.list_engines(),
            data_store_id,
            project_id=self.project_id,
            location=self.location,
            collection=self.collection,
            serving_config=serving_config,
        )

    def list_target_sites(self, data_store_id: str) -> list[dict[str, Any]]:
        """List every target site for one website data store."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", data_store_id):
            raise ResearchError("Discovery Engine data-store ID is invalid")
        return self._get_paginated(
            (
                f"{self.api_root}/{self.parent}/dataStores/{data_store_id}/"
                "siteSearchEngine/targetSites"
            ),
            collection_field="targetSites",
            page_size=1000,
        )


class GoogleDiscoveryEngineBackend:
    """Non-generative Discovery Engine website-collection search via local ADC."""

    name = "google_discovery_engine"
    version = DISCOVERY_ENGINE_BACKEND_VERSION
    endpoint = DISCOVERY_ENDPOINT
    fixed_request_policy = {
        "query_expansion": "disabled",
        "spell_correction": "suggestion_only",
        "return_snippet": True,
        "maximum_snippet_count": 1,
        "generative_features": False,
    }

    def __init__(
        self,
        *,
        descriptor: DiscoveryEngineDescriptor | None = None,
        target_site_manifest_hash: str = "",
        access_token_provider: Callable[[], str] | None = None,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (10.0, 30.0),
    ) -> None:
        """Configure non-generative Discovery Engine search and its identity."""
        if descriptor is None:
            descriptor = DiscoveryEngineDescriptor(
                project_id=DISCOVERY_PROJECT_ID,
                location=DISCOVERY_LOCATION,
                collection=DISCOVERY_COLLECTION,
                engine_id=DISCOVERY_ENGINE_ID,
                display_name="",
                data_store_ids=(DISCOVERY_DATA_STORE_ID,),
                solution_type="SOLUTION_TYPE_SEARCH",
                search_tier="",
                serving_config=DISCOVERY_SERVING_CONFIG,
            )
        if (
            target_site_manifest_hash
            and not re.fullmatch(r"[0-9a-f]{64}", target_site_manifest_hash)
        ):
            raise ResearchError("target-site manifest hash is invalid")
        self.descriptor = descriptor
        self.endpoint = descriptor.endpoint
        self.project_id = descriptor.project_id
        self.engine_id = descriptor.engine_id
        self.data_store_id = (
            descriptor.data_store_ids[0]
            if len(descriptor.data_store_ids) == 1 else ""
        )
        self.target_site_manifest_hash = target_site_manifest_hash
        self._access_token_provider = (
            access_token_provider or GcloudADCAccessTokenProvider()
        )
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.timeout = timeout
        self._prepared_access_token = ""
        self.engine_identity_hash = sha256_bytes(canonical_json_bytes({
            "backend": self.name,
            "version": self.version,
            "endpoint": self.endpoint,
            "project_id": descriptor.project_id,
            "location": descriptor.location,
            "collection": descriptor.collection,
            "engine_id": descriptor.engine_id,
            "data_store_ids": list(descriptor.data_store_ids),
            "serving_config": descriptor.serving_config,
            "target_site_manifest_hash": target_site_manifest_hash,
            "query_policy_version": QUERY_POLICY_VERSION,
            "programme_version": PROGRAMME_VERSION,
            "quota_project": descriptor.project_id,
            "authentication": "application_default_credentials",
            "request_policy": self.fixed_request_policy,
        }))

    def ensure_authenticated(self) -> None:
        """Fail before a search lifecycle starts when local ADC is unavailable."""
        if not self._prepared_access_token:
            self._prepared_access_token = self._access_token_provider()

    @staticmethod
    def _plain_discovery_text(value: Any, *, maximum: int) -> str:
        text = html.unescape(re.sub(r"<[^>]{0,500}>", " ", str(value or "")))
        return re.sub(r"\s+", " ", text).strip()[:maximum]

    def search(self, query: str, *, number: int = TOP_RESULTS) -> list[dict[str, str]]:
        """POST one non-generative search and map at most ten website results."""
        if number < 1 or number > TOP_RESULTS:
            raise ValueError("Discovery Engine result count must be 1..10")
        token = self._prepared_access_token
        self._prepared_access_token = ""
        if not token:
            try:
                token = self._access_token_provider()
            except SearchBackendNotConfigured:
                raise SearchAuthenticationConfigurationError(
                    "Discovery Engine ADC token is unavailable"
                ) from None
        body = {
            "query": query,
            "pageSize": number,
            "queryExpansionSpec": {"condition": "DISABLED"},
            "spellCorrectionSpec": {"mode": "SUGGESTION_ONLY"},
            "contentSearchSpec": {
                "snippetSpec": {"returnSnippet": True, "maxSnippetCount": 1},
            },
        }
        try:
            response = self.session.post(
                self.endpoint,
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Goog-User-Project": self.project_id,
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise AmbiguousSearchRequest(
                f"Discovery Engine transport outcome is ambiguous: {type(exc).__name__}"
            ) from None
        if 300 <= response.status_code < 400:
            raise ConfirmedSearchError("Discovery Engine redirect refused")
        if response.status_code in {408, 429} or response.status_code >= 500:
            raise RetryableSearchError(
                f"Discovery Engine retryable HTTP {response.status_code}"
            )
        if response.status_code in {401, 403, 404}:
            raise SearchAuthenticationConfigurationError(
                f"Discovery Engine authentication or resource HTTP {response.status_code}"
            )
        if response.status_code == 400:
            try:
                error_payload = response.json()
            except ValueError:
                error_payload = {}
            error = error_payload.get("error") if isinstance(error_payload, dict) else {}
            details = error.get("details") if isinstance(error, dict) else []
            invalid_query = False
            if isinstance(details, list):
                for detail in details:
                    violations = (
                        detail.get("fieldViolations", [])
                        if isinstance(detail, dict) else []
                    )
                    if isinstance(violations, list) and any(
                        isinstance(violation, dict)
                        and str(violation.get("field") or "").casefold() == "query"
                        for violation in violations
                    ):
                        invalid_query = True
                        break
            if (
                isinstance(error, dict)
                and error.get("status") == "INVALID_ARGUMENT"
                and invalid_query
            ):
                raise RejectedSearchQuery("Discovery Engine rejected the search query")
            raise ConfirmedSearchError("Discovery Engine HTTP 400")
        if response.status_code == 422:
            raise RejectedSearchQuery("Discovery Engine rejected the search query")
        if not response.ok:
            raise ConfirmedSearchError(f"Discovery Engine HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            raise ResearchError("Discovery Engine returned invalid JSON") from None
        if not isinstance(payload, dict):
            raise ResearchError("Discovery Engine response is not an object")
        raw_results = payload.get("results", [])
        if not isinstance(raw_results, list):
            raise ResearchError("Discovery Engine results are invalid")
        results: list[dict[str, str]] = []
        for raw_result in raw_results:
            if not isinstance(raw_result, dict):
                continue
            document = raw_result.get("document")
            if not isinstance(document, dict):
                continue
            derived_data = document.get("derivedStructData")
            struct_data = document.get("structData")
            if not isinstance(derived_data, dict):
                derived_data = {}
            if not isinstance(struct_data, dict):
                struct_data = {}
            data = {**struct_data, **derived_data}
            if not data:
                continue
            result_url = str(data.get("link") or "").strip()
            if not result_url:
                continue
            snippets = data.get("snippets")
            snippet_parts: list[str] = []
            if isinstance(snippets, list):
                for snippet in snippets[:3]:
                    if isinstance(snippet, dict) and snippet.get("snippet"):
                        snippet_parts.append(str(snippet["snippet"]))
            results.append({
                "title": self._plain_discovery_text(
                    data.get("title") or data.get("htmlTitle"), maximum=500,
                ),
                "result_url": result_url,
                "snippet": self._plain_discovery_text(" ".join(snippet_parts), maximum=2000),
                "display_link": self._plain_discovery_text(
                    data.get("displayLink"), maximum=500,
                ),
                "mime": self._plain_discovery_text(data.get("mime"), maximum=200),
                "document_id": str(document.get("id") or "")[:500],
            })
            if len(results) == number:
                break
        return results


class BraveWebSearchBackend:
    """Brave's ordinary structured Web Search API, with no AI endpoint."""

    name = "brave_web_search_json"
    version = BRAVE_BACKEND_VERSION
    endpoint = "https://api.search.brave.com/res/v1/web/search"
    fixed_parameters = {
        "country": "gb",
        "search_lang": "en",
        "safesearch": "moderate",
        "spellcheck": "0",
    }

    def __init__(
        self,
        api_key: str,
        *,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (10.0, 30.0),
    ) -> None:
        """Configure an ordinary Brave Web Search API client."""
        if not api_key:
            raise SearchBackendNotConfigured("Brave Search API key is required")
        self._api_key = api_key
        self.engine_identity_hash = sha256_bytes(canonical_json_bytes({
            "endpoint": self.endpoint,
            "fixed_parameters": self.fixed_parameters,
            "ordinary_web_search_only": True,
        }))
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.timeout = timeout

    def search(self, query: str, *, number: int = TOP_RESULTS) -> list[dict[str, str]]:
        """Make one ordinary Brave Web Search request and map its structured results."""
        if number < 1 or number > TOP_RESULTS:
            raise ValueError("Brave structured search result count must be 1..10")
        try:
            response = self.session.get(
                self.endpoint,
                params={"q": query, "count": number, **self.fixed_parameters},
                headers={
                    "X-Subscription-Token": self._api_key,
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise AmbiguousSearchRequest(
                f"Brave structured search transport outcome is ambiguous: {type(exc).__name__}"
            ) from exc
        if 300 <= response.status_code < 400:
            raise ConfirmedSearchError("Brave structured search redirect refused")
        if response.status_code >= 500 or response.status_code == 429:
            raise RetryableSearchError(
                f"Brave structured search retryable HTTP {response.status_code}"
            )
        if response.status_code == 422:
            raise RejectedSearchQuery("Brave structured search HTTP 422")
        if not response.ok:
            raise ConfirmedSearchError(
                f"Brave structured search HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ResearchError("Brave structured search returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ResearchError("Brave structured search response is invalid")
        web = payload.get("web")
        if web is None:
            items: Any = []
        elif isinstance(web, dict):
            items = web.get("results", [])
        else:
            raise ResearchError("Brave structured search web results are invalid")
        if not isinstance(items, list):
            raise ResearchError("Brave structured search items are invalid")
        results: list[dict[str, str]] = []
        for item in items[:number]:
            if not isinstance(item, dict):
                continue
            results.append({
                "title": str(item.get("title") or ""),
                "result_url": str(item.get("url") or ""),
                "snippet": str(item.get("description") or ""),
            })
        return results

    def prepare_query(self, query: str) -> str:
        """Bound an over-limit exact phrase to a deterministic contiguous excerpt."""
        value = str(query).strip()
        if (
            len(value) <= BRAVE_QUERY_MAXIMUM_CHARACTERS
            and len(value.split()) <= BRAVE_QUERY_MAXIMUM_WORDS
        ):
            return value
        quoted = len(value) >= 2 and value.startswith('"') and value.endswith('"')
        content = value[1:-1].strip() if quoted else value
        tokens = list(re.finditer(r"\S+", content))
        if not tokens:
            raise RejectedSearchQuery("Brave query is empty after deterministic preparation")
        maximum_words = min(BRAVE_QUERY_SAFE_EXCERPT_WORDS, len(tokens))
        while maximum_words:
            excerpt = content[:tokens[maximum_words - 1].end()].strip()
            prepared = f'"{excerpt}"' if quoted else excerpt
            if (
                len(prepared) <= BRAVE_QUERY_MAXIMUM_CHARACTERS
                and len(prepared.split()) <= BRAVE_QUERY_MAXIMUM_WORDS
            ):
                return prepared
            maximum_words -= 1
        raise RejectedSearchQuery("Brave query cannot be bounded safely")


def first_present_environment(names: Sequence[str], environment: Mapping[str, str]) -> tuple[str, str]:
    """Return the first configured name/value pair without logging its value."""
    for name in names:
        value = str(environment.get(name) or "").strip()
        if value:
            return name, value
    return "", ""


def load_project_environment(path: Path, environment: dict[str, str] | None = None) -> None:
    """Load simple ignored KEY=value settings without printing or replacing values."""
    if environment is None:
        environment = os.environ
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8", errors="strict").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        environment.setdefault(name, value)


def configured_google_backend(
    root: Path = ROOT,
    *,
    environment: dict[str, str] | None = None,
    session: requests.Session | None = None,
) -> tuple[GoogleCustomSearchBackend, SearchBudget, dict[str, Any]]:
    """Return an explicitly priced Google backend or fail without using AI aliases."""
    env = os.environ if environment is None else environment
    load_project_environment(root / "mrsMThatcher.env", env)
    key_name, key = first_present_environment(SEARCH_API_KEY_NAMES, env)
    engine_name, engine_id = first_present_environment(SEARCH_ENGINE_ID_NAMES, env)
    price_raw = str(env.get(SEARCH_PRICE_NAME) or "").strip()
    if not key or not engine_id or not price_raw:
        raise SearchBackendNotConfigured(
            "Google Custom Search key, engine ID and explicit per-1,000 price are required"
        )
    price = decimal_value(price_raw, label=SEARCH_PRICE_NAME)
    if price <= 0:
        raise SearchBackendNotConfigured(
            f"{SEARCH_PRICE_NAME} must be the positive paid-request price; account for free quota separately"
        )
    try:
        free_quota = int(str(env.get(SEARCH_FREE_QUOTA_NAME, "0")).strip() or "0")
    except ValueError as exc:
        raise SearchBackendNotConfigured(f"{SEARCH_FREE_QUOTA_NAME} must be an integer") from exc
    if free_quota < 0:
        raise SearchBackendNotConfigured(f"{SEARCH_FREE_QUOTA_NAME} must be non-negative")
    backend = GoogleCustomSearchBackend(key, engine_id, session=session)
    budget = SearchBudget(price_per_1000=price, free_quota_remaining_at_start=free_quota)
    config = {
        "backend": backend.name,
        "backend_version": backend.version,
        "api_key_variable": key_name,
        "engine_id_variable": engine_name,
        "engine_identity_sha256": backend.engine_identity_hash,
        "pricing_variable": SEARCH_PRICE_NAME,
        "free_quota_variable": SEARCH_FREE_QUOTA_NAME,
        "secrets_persisted": False,
    }
    return backend, budget, config


def configured_discovery_engine_backend(
    root: Path = ROOT,
    *,
    environment: dict[str, str] | None = None,
    session: requests.Session | None = None,
    access_token_provider: Callable[[], str] | None = None,
) -> tuple[GoogleDiscoveryEngineBackend, SearchBudget, dict[str, Any]]:
    """Return the fixed, explicitly authorised Discovery Engine search backend."""
    del root, environment
    identifiers = {
        "project_id": DISCOVERY_PROJECT_ID,
        "location": DISCOVERY_LOCATION,
        "collection": DISCOVERY_COLLECTION,
        "engine_id": DISCOVERY_ENGINE_ID,
        "data_store_id": DISCOVERY_DATA_STORE_ID,
        "serving_config": DISCOVERY_SERVING_CONFIG,
    }
    if any(
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value)
        for value in identifiers.values()
    ):
        raise SearchBackendNotConfigured(
            "Discovery Engine resource configuration is invalid"
        )
    backend = GoogleDiscoveryEngineBackend(
        access_token_provider=access_token_provider,
        session=session,
    )
    budget = SearchBudget(
        price_per_1000=Decimal("0"),
        pricing_known=False,
        planned_maximum=PLANNED_SEARCH_REQUESTS,
        absolute_maximum=ABSOLUTE_SEARCH_REQUESTS,
    )
    config = {
        "backend": "google_discovery_engine",
        "backend_version": backend.version,
        "search_scope": "configured_website_data_store",
        "full_web_search": False,
        "project_id": DISCOVERY_PROJECT_ID,
        "location": DISCOVERY_LOCATION,
        "collection": DISCOVERY_COLLECTION,
        "engine_id": DISCOVERY_ENGINE_ID,
        "data_store_id": DISCOVERY_DATA_STORE_ID,
        "serving_config": DISCOVERY_SERVING_CONFIG,
        "quota_project_id": DISCOVERY_PROJECT_ID,
        "engine_identity_sha256": backend.engine_identity_hash,
        "authentication": "application_default_credentials_via_gcloud",
        "access_tokens_persisted": False,
        "generative_features_requested": False,
        "search_cost_estimate_available": False,
        "secrets_persisted": False,
    }
    return backend, budget, config


def configured_brave_backend(
    root: Path = ROOT,
    *,
    environment: dict[str, str] | None = None,
    session: requests.Session | None = None,
) -> tuple[BraveWebSearchBackend, SearchBudget, dict[str, Any]]:
    """Return an explicitly selected and priced ordinary Brave Web Search backend."""
    env = os.environ if environment is None else environment
    load_project_environment(root / "mrsMThatcher.env", env)
    key_name, key = first_present_environment(BRAVE_API_KEY_NAMES, env)
    price_raw = str(env.get(SEARCH_PRICE_NAME) or "").strip()
    if not key or not price_raw:
        raise SearchBackendNotConfigured(
            "Brave Search API key and explicit per-1,000 price are required"
        )
    price = decimal_value(price_raw, label=SEARCH_PRICE_NAME)
    if price <= 0:
        raise SearchBackendNotConfigured(
            f"{SEARCH_PRICE_NAME} must be the positive paid-request price; account for free quota separately"
        )
    try:
        free_quota = int(str(env.get(SEARCH_FREE_QUOTA_NAME, "0")).strip() or "0")
    except ValueError as exc:
        raise SearchBackendNotConfigured(f"{SEARCH_FREE_QUOTA_NAME} must be an integer") from exc
    if free_quota < 0:
        raise SearchBackendNotConfigured(f"{SEARCH_FREE_QUOTA_NAME} must be non-negative")
    backend = BraveWebSearchBackend(key, session=session)
    budget = SearchBudget(price_per_1000=price, free_quota_remaining_at_start=free_quota)
    config = {
        "backend": backend.name,
        "backend_version": backend.version,
        "api_key_variable": key_name,
        "engine_identity_sha256": backend.engine_identity_hash,
        "backend_selection_variable": SEARCH_BACKEND_NAME,
        "pricing_variable": SEARCH_PRICE_NAME,
        "free_quota_variable": SEARCH_FREE_QUOTA_NAME,
        "ordinary_web_search_only": True,
        "query_limit_characters": BRAVE_QUERY_MAXIMUM_CHARACTERS,
        "query_limit_words": BRAVE_QUERY_MAXIMUM_WORDS,
        "over_limit_query_policy": "exact_contiguous_prefix_up_to_48_words",
        "secrets_persisted": False,
    }
    return backend, budget, config


def configured_search_backend(
    root: Path = ROOT,
    *,
    environment: dict[str, str] | None = None,
    session: requests.Session | None = None,
) -> tuple[SearchBackend, SearchBudget, dict[str, Any]]:
    """Select only the user-authorised fixed Discovery Engine backend."""
    return configured_discovery_engine_backend(
        root,
        environment=environment,
        session=session,
    )


@dataclass
class PageFetchBudget:
    """Track unique network URLs, including robots and redirect hops."""

    maximum: int = MAXIMUM_PAGE_FETCHES
    requested_urls: set[str] | None = None

    def __post_init__(self) -> None:
        if self.maximum > MAXIMUM_PAGE_FETCHES:
            self.maximum = MAXIMUM_PAGE_FETCHES
        if self.maximum < 0:
            raise ResearchError("page-fetch maximum cannot be negative")
        if self.requested_urls is None:
            self.requested_urls = set()
        if len(self.requested_urls) > self.maximum:
            raise ResearchError("persisted page-fetch URLs exceed the hard limit")

    def reserve(self, url: str) -> None:
        """Reserve one unique network URL before the request."""
        assert self.requested_urls is not None
        if url in self.requested_urls:
            return
        if len(self.requested_urls) >= self.maximum:
            raise FetchLimitExceeded("maximum unique page fetches reached")
        self.requested_urls.add(url)

    def as_dict(self) -> dict[str, Any]:
        """Return persisted page-fetch accounting."""
        assert self.requested_urls is not None
        return {
            "maximum_unique_page_fetches": self.maximum,
            "unique_network_urls_requested": len(self.requested_urls),
            "requested_url_hashes": [sha256_bytes(url.encode("utf-8")) for url in sorted(self.requested_urls)],
            "requested_urls": sorted(self.requested_urls),
            "remaining_unique_page_fetches": self.maximum - len(self.requested_urls),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PageFetchBudget":
        """Restore page-fetch accounting."""
        return cls(
            maximum=MAXIMUM_PAGE_FETCHES,
            requested_urls=set(str(url) for url in value.get("requested_urls", [])),
        )


class ResearchCache:
    """Ignored, hash-keyed search and page cache."""

    def __init__(self, root: Path = DEFAULT_CACHE) -> None:
        """Bind hash-keyed cache directories beneath the supplied root."""
        self.root = root
        self.search_dir = root / "search"
        self.page_dir = root / "pages"
        self.robots_dir = root / "robots"

    def search_key(self, backend: SearchBackend, query: str, number: int) -> str:
        """Return a non-secret stable search cache identity."""
        return sha256_bytes(canonical_json_bytes({
            "backend": backend.name,
            "version": backend.version,
            "engine_identity_hash": backend.engine_identity_hash,
            "engine_id": str(getattr(backend, "engine_id", "")),
            "data_store_id": str(getattr(backend, "data_store_id", "")),
            "target_site_manifest_hash": str(
                getattr(backend, "target_site_manifest_hash", "")
            ),
            "query_policy_version": QUERY_POLICY_VERSION,
            "programme_version": PROGRAMME_VERSION,
            "query": query,
            "number": number,
        }))

    def search_path(self, key: str) -> Path:
        """Return one search-response cache path."""
        return self.search_dir / f"{key}.json"

    def load_search(self, key: str) -> dict[str, Any] | None:
        """Load a completed cached search response."""
        path = self.search_path(key)
        if not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("cache_key") != key:
            raise ResearchError("search cache record is invalid")
        return value

    def save_search(self, key: str, value: Mapping[str, Any]) -> None:
        """Atomically cache one structured response with no credentials."""
        atomic_write_json(self.search_path(key), {"cache_key": key, **dict(value)}, mode=0o600)

    def page_paths(self, url: str) -> tuple[Path, Path]:
        """Return metadata and body cache paths for one canonical URL."""
        key = sha256_bytes(url.encode("utf-8"))
        return self.page_dir / f"{key}.json", self.page_dir / f"{key}.body"

    def load_page(self, url: str) -> tuple[dict[str, Any], bytes] | None:
        """Load a previously fetched page without a new network request."""
        metadata_path, body_path = self.page_paths(url)
        if not metadata_path.is_file() or not body_path.is_file():
            return None
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if (
            isinstance(metadata, dict)
            and metadata.get("fetch_policy_version") != FETCH_POLICY_VERSION
        ):
            return None
        body = body_path.read_bytes()
        if (
            not isinstance(metadata, dict)
            or metadata.get("requested_url") != url
            or metadata.get("body_sha256") != sha256_bytes(body)
            or len(body) > MAXIMUM_RESPONSE_BYTES
            or str(metadata.get("content_type") or "").casefold() not in ALLOWED_CONTENT_TYPES
        ):
            raise ResearchError("page cache record is invalid")
        return metadata, body

    def save_page(self, url: str, metadata: Mapping[str, Any], body: bytes) -> None:
        """Cache response bytes and whitelisted metadata privately."""
        metadata_path, body_path = self.page_paths(url)
        atomic_write_bytes(body_path, body, mode=0o600)
        atomic_write_json(metadata_path, {
            **dict(metadata), "requested_url": url, "body_sha256": sha256_bytes(body),
        }, mode=0o600)

    def robots_path(self, origin: str) -> Path:
        """Return a robots policy cache path."""
        return self.robots_dir / f"{sha256_bytes(origin.encode('utf-8'))}.json"

    def load_robots(self, origin: str) -> dict[str, Any] | None:
        """Load a cached robots policy."""
        path = self.robots_path(origin)
        if not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("origin") != origin:
            raise ResearchError("robots cache record is invalid")
        if value.get("fetch_policy_version") != FETCH_POLICY_VERSION:
            return None
        try:
            fetched_at = datetime.fromisoformat(
                str(value.get("fetched_at") or "").replace("Z", "+00:00")
            )
        except ValueError:
            return None
        if (datetime.now(timezone.utc) - fetched_at).total_seconds() > ROBOTS_CACHE_SECONDS:
            return None
        return value

    def save_robots(self, origin: str, value: Mapping[str, Any]) -> None:
        """Cache a robots policy privately."""
        atomic_write_json(
            self.robots_path(origin),
            {
                "origin": origin,
                **dict(value),
                "fetch_policy_version": FETCH_POLICY_VERSION,
            },
            mode=0o600,
        )


def _content_type(response: Any) -> str:
    """Return a lower-cased media type without parameters."""
    return str(response.headers.get("content-type") or "").split(";", 1)[0].strip().casefold()


def _document_request_headers() -> dict[str, str]:
    """Return honest, deterministic public-document navigation headers."""
    return {
        "User-Agent": USER_AGENT,
        "Accept": DOCUMENT_ACCEPT,
        "Accept-Language": ACCEPT_LANGUAGE,
        # The pinned transport can bound gzip expansion incrementally.  The
        # installed Brotli binding cannot cap decoder output, so it is not
        # advertised by this security-sensitive fetcher.
        "Accept-Encoding": "gzip",
        "Connection": "close",
    }


def _selected_response_headers(headers: Mapping[str, Any]) -> dict[str, str]:
    """Retain only non-secret response metadata needed for fetch diagnostics."""
    selected: dict[str, str] = {}
    for name in (
        "content-type", "content-length", "content-encoding", "location",
        "content-range", "retry-after", "server", "vary", "etag", "last-modified",
        "cf-mitigated",
    ):
        value = str(headers.get(name) or "").strip()
        if value:
            selected[name] = value[:1000]
    return selected


def _challenge_page_diagnostic(
    http_status: int,
    headers: Mapping[str, Any],
    content_type: str,
    body: bytes,
) -> dict[str, Any] | None:
    """Recognise high-confidence denial/challenge pages without broad prose matching."""
    cf_mitigated = str(headers.get("cf-mitigated") or "").casefold().strip()
    if cf_mitigated == "challenge":
        return {
            "kind": "access_challenge",
            "http_status": int(http_status),
            "title": "",
            "markers": ["cf_mitigated"],
            "body_sha256": sha256_bytes(body),
        }
    if content_type not in {"text/html", "application/xhtml+xml"} or not body:
        return None
    sample = body[:512 * 1024].decode("utf-8", errors="replace")
    lowered = sample.casefold()
    title_match = re.search(r"<title[^>]*>(.*?)</title>", sample, re.I | re.S)
    title = " ".join(
        BeautifulSoup(title_match.group(1), "lxml").get_text(" ", strip=True).split()
    ) if title_match else ""
    title_key = title.casefold().rstrip(". …")
    vendor_markers = {
        "cf_challenge": bool(re.search(r"\bcf-chl(?:-|_)|challenge-platform", lowered)),
        "checking_browser": "checking your browser" in lowered,
        "javascript_cookies": "enable javascript and cookies to continue" in lowered,
        "human_verification": "verify you are human" in lowered,
        "captcha": bool(re.search(r"(?:hcaptcha|g-recaptcha|captcha-container)", lowered)),
    }
    challenge_title_family = bool(re.fullmatch(
        r"(?:just a moment|attention required|access denied|security check|"
        r"checking your browser|human verification|verify you are human)"
        r"(?:[.!?…]+\s*)?\s*(?:[|:\-–—]\s*.+)?",
        title_key,
    ))
    active_markers = sorted(name for name, present in vendor_markers.items() if present)
    if challenge_title_family and active_markers:
        return {
            "kind": "access_challenge",
            "http_status": int(http_status),
            "title": title[:200],
            "markers": active_markers,
            "body_sha256": sha256_bytes(body),
        }
    return None


def inspect_mtf_document(
    expected_url: str,
    content_type: str,
    body: bytes,
) -> dict[str, Any]:
    """Fail-closed validation for modern or legacy MTF archive-document HTML."""
    expected = canonicalise_url(expected_url)
    parsed = urlsplit(expected)
    match = re.fullmatch(r"/document/(\d+)", parsed.path.rstrip("/"), re.I)
    if _host(expected) != "margaretthatcher.org" or match is None:
        return {"valid": False, "status": "invalid_official_document", "reason": "invalid_expected_mtf_identity"}
    document_number = match.group(1)
    if content_type.casefold() not in {"text/html", "application/xhtml+xml"}:
        return {
            "valid": False, "status": "invalid_official_document",
            "reason": "official_document_is_not_html", "document_number": document_number,
        }
    soup = BeautifulSoup(body, "lxml")
    title_node = soup.find("title")
    first_heading = soup.find("h1")
    labels = {
        " ".join(node.get_text(" ", strip=True).split()).casefold()
        for node in (title_node, first_heading) if node is not None
    }
    if any(label == "page not found" or label.startswith("page not found |") for label in labels):
        return {
            "valid": False, "status": "official_document_not_found",
            "reason": "official_page_not_found", "document_number": document_number,
        }
    canonical_node = soup.find("link", rel=lambda value: value and "canonical" in value)
    declared = str(canonical_node.get("href") or "").strip() if canonical_node else ""
    if not declared:
        return {
            "valid": False, "status": "invalid_official_document",
            "reason": "official_document_missing_canonical_url", "document_number": document_number,
        }
    try:
        declared_canonical = canonicalise_url(urljoin(expected, declared))
    except (UnsafeURL, ValueError):
        return {
            "valid": False, "status": "document_identity_mismatch",
            "reason": "official_document_canonical_url_is_invalid", "document_number": document_number,
        }
    declared_parsed = urlsplit(declared_canonical)
    try:
        decoded_declared_path = unquote_to_bytes(
            declared_parsed.path or "/"
        ).decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        decoded_declared_path = ""
    declared_identity_match = re.fullmatch(
        r"/document/(\d+)", decoded_declared_path.rstrip("/"), re.I,
    )
    declared_identity = (
        f"https://www.margaretthatcher.org/document/{declared_identity_match.group(1)}"
        if (
            _host(declared_canonical) == "margaretthatcher.org"
            and not declared_parsed.query
            and declared_identity_match is not None
        )
        else declared_canonical
    )
    if declared_identity != expected:
        return {
            "valid": False, "status": "document_identity_mismatch",
            "reason": "official_document_canonical_url_mismatch",
            "document_number": document_number,
            "declared_canonical_url": declared_canonical,
        }
    layouts = [
        (
            "modern",
            soup.select_one("article.node-archive-document"),
            soup.select_one("h1.doctitle"),
            soup.select_one(".docauthor"),
            soup.select_one(".docdate time"),
        ),
        (
            "current_mirror",
            soup.select_one(".document-body"),
            soup.select_one(".document-header h1"),
            soup.select_one(".document-header .docauthor"),
            soup.select_one(".document-header .docdate time"),
        ),
        (
            "legacy",
            soup.select_one("#documentbody"),
            soup.select_one("#documentheader h1"),
            soup.select_one("#docauthor"),
            soup.select_one("#docdate"),
        ),
    ]
    selector_kind, article, heading, author, date_node = max(
        layouts,
        key=lambda layout: len(
            " ".join(layout[1].get_text(" ", strip=True).split())
        ) if layout[1] is not None else -1,
    )
    article_text = " ".join(article.get_text(" ", strip=True).split()) if article else ""
    title = " ".join(heading.get_text(" ", strip=True).split()) if heading else ""
    author_text = " ".join(author.get_text(" ", strip=True).split()) if author else ""
    date_text = ""
    if date_node is not None:
        date_text = " ".join(str(date_node.get("datetime") or date_node.get_text(" ", strip=True)).split())
    if not article_text or not title or len(article_text) < 100:
        return {
            "valid": False, "status": "invalid_official_document",
            "reason": "official_document_lacks_title_or_transcript",
            "document_number": document_number,
        }
    return {
        "valid": True,
        "status": "validated_official_document",
        "reason": "matching canonical MTF document with title, metadata and transcript",
        "document_number": document_number,
        "canonical_url": expected,
        "declared_canonical_url": declared_identity,
        "declared_canonical_transport_url": declared_canonical,
        "title": title[:500],
        "author": author_text[:500],
        "date": date_text[:200],
        "selector_kind": selector_kind,
        "article_text_sha256": sha256_bytes(article_text.encode("utf-8")),
        "article_text_character_count": len(article_text),
    }


class _PinnedHTTPResponse:
    """Small requests-compatible wrapper around one address-pinned HTTP response."""

    def __init__(self, response: http.client.HTTPResponse, connection: http.client.HTTPConnection) -> None:
        self._response = response
        self._connection = connection
        self.status_code = int(response.status)
        self.headers = response.headers
        self.ok = 200 <= self.status_code < 400

    def iter_content(self, chunk_size: int) -> Iterable[bytes]:
        while True:
            block = self._response.read(chunk_size)
            if not block:
                return
            yield block

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._connection.close()


def _address_is_public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Apply the explicit public-address policy used at validation and connection time."""
    return not (
        not address.is_global
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or address.is_loopback
        or address.is_private
        or address.is_link_local
    )


def _http_host_header(host: str, port: int, scheme: str) -> str:
    """Return a syntactically valid Host header for DNS names or IPv6 literals."""
    rendered = f"[{host}]" if ":" in host else host
    default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    return rendered if default_port else f"{rendered}:{port}"


def _pinned_public_get(
    url: str,
    *,
    headers: Mapping[str, str],
    timeout: tuple[float, float],
    resolver: Callable[..., Any] = socket.getaddrinfo,
) -> _PinnedHTTPResponse:
    """Resolve, validate and connect to the same public address, retaining Host/SNI."""
    canonical = canonicalise_url(url)
    parsed = urlsplit(canonical)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = _resolved_addresses(host, port, resolver)
    if any(not _address_is_public(address) for address in addresses):
        raise UnsafeURL("URL resolves to a non-public address at connection time")
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    request_headers = dict(headers)
    request_headers["Host"] = _http_host_header(host, port, parsed.scheme)
    last_error: BaseException | None = None
    for address in addresses[:MAXIMUM_PUBLIC_ADDRESS_ATTEMPTS]:
        raw_socket: socket.socket | None = None
        connection: http.client.HTTPConnection | None = None
        try:
            raw_socket = socket.create_connection(
                (str(address), port), timeout=float(timeout[0]),
            )
            raw_socket.settimeout(float(timeout[1]))
            if parsed.scheme == "https":
                raw_socket = ssl.create_default_context().wrap_socket(
                    raw_socket, server_hostname=host,
                )
                raw_socket.settimeout(float(timeout[1]))
            connection = http.client.HTTPConnection(host, port, timeout=float(timeout[1]))
            connection.sock = raw_socket
            connection.request("GET", target, headers=request_headers)
            return _PinnedHTTPResponse(connection.getresponse(), connection)
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            last_error = exc
            if connection is not None:
                connection.close()
            elif raw_socket is not None:
                raw_socket.close()
    raise OSError(f"all validated public addresses failed: {type(last_error).__name__}")


class SafeFetcher:
    """Restricted public HTTP fetcher with manual redirect and robots validation."""

    def __init__(
        self,
        *,
        cache: ResearchCache,
        budget: PageFetchBudget,
        session: requests.Session | Any | None = None,
        url_validator: Callable[[str], str] = validate_public_url,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        minimum_delay_seconds: float = 1.0,
        timeout: tuple[float, float] = (10.0, 30.0),
        on_budget_change: Callable[[], None] | None = None,
        test_only_allow_unpinned_session: bool = False,
        local_archive: LocalArchiveMirror | None = None,
    ) -> None:
        """Configure bounded fetching, cache use, URL validation and pacing."""
        self.cache = cache
        self.budget = budget
        if session is not None and not test_only_allow_unpinned_session:
            raise ResearchError("an unpinned HTTP session is allowed only by explicit local-test injection")
        self.session = session
        if self.session is not None and hasattr(self.session, "trust_env"):
            self.session.trust_env = False
        if self.session is not None and hasattr(self.session, "cookies"):
            self.session.cookies.clear()
        self.url_validator = url_validator
        self.sleep = sleep
        self.monotonic = monotonic
        configured_delay = float(minimum_delay_seconds)
        if (
            not math.isfinite(configured_delay)
            or configured_delay < 0
            or configured_delay > MAXIMUM_ROBOTS_CRAWL_DELAY_SECONDS
        ):
            raise ResearchError("fetcher minimum delay is outside the bounded safe range")
        self.minimum_delay_seconds = configured_delay
        self.timeout = timeout
        self.on_budget_change = on_budget_change
        self._last_request_by_host: dict[str, float] = {}
        self.local_archive = local_archive
        self.local_archive_inventory = (
            local_archive.inventory() if local_archive is not None else None
        )

    def _fetch_from_local_archive(self, url: str) -> dict[str, Any] | None:
        """Use the configured mirror before DNS, without weakening document checks."""
        if self.local_archive is None:
            return None
        try:
            syntactic = canonicalise_url(url)
        except UnsafeURL as exc:
            return {"status": "unsafe_url", "error": str(exc), "requested_url": str(url)}
        if _host(syntactic) not in {
            "margaretthatcher.org", "archive.margaretthatcher.org",
        }:
            return None
        try:
            local = self.local_archive.read(syntactic)
        except LocalArchiveError as exc:
            return {
                "status": "local_archive_unsafe_or_invalid",
                "error": str(exc),
                "requested_url": syntactic,
                "cache_hit": False,
                "local_archive_hit": False,
                "network_attempt_count": 0,
                "retry_count": 0,
            }
        if local is None:
            return None
        body = bytes(local.get("body") or b"")
        mtf_validation: dict[str, Any] | None = None
        if (
            local.get("status") == "fetched"
            and _host(syntactic) == "margaretthatcher.org"
            and re.fullmatch(r"/document/\d+", urlsplit(syntactic).path.rstrip("/"), re.I)
        ):
            mtf_validation = inspect_mtf_document(
                syntactic, str(local.get("content_type") or ""), body,
            )
            if not mtf_validation.get("valid"):
                local["status"] = str(
                    mtf_validation.get("status") or "invalid_official_document"
                )
                local["error"] = str(
                    mtf_validation.get("reason")
                    or "official document validation failed"
                )
        inventory = self.local_archive_inventory
        return {
            **local,
            "fetched_at": utc_now(),
            "fetch_policy_version": FETCH_POLICY_VERSION,
            "body_sha256": sha256_bytes(body) if body else "",
            "captured_body_bytes": len(body),
            "mtf_document_validation": mtf_validation or {},
            "local_archive_inventory_sha256": (
                inventory.sha256 if inventory is not None else ""
            ),
            "source_publisher": (
                MARGARET_THATCHER_FOUNDATION_PUBLISHER
                if _host(syntactic) == "margaretthatcher.org"
                else ""
            ),
            "body": body,
        }

    def _validate_url_with_retries(self, value: str) -> str:
        """Retry only transient DNS failures while preserving unsafe-URL decisions."""
        for attempt in range(1, MAXIMUM_FETCH_ATTEMPTS + 1):
            try:
                return self.url_validator(value)
            except TransientDNSFailure:
                if attempt >= MAXIMUM_FETCH_ATTEMPTS:
                    raise
                self.sleep(self._retry_delay_seconds({}, attempt))
        raise AssertionError("DNS validation retry loop did not terminate")

    def _bounded_crawl_delay(self, value: Any) -> float | None:
        """Return a usable crawl delay or None when respecting it is impractical."""
        try:
            delay = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if (
            not math.isfinite(delay)
            or delay < 0
            or delay > MAXIMUM_ROBOTS_CRAWL_DELAY_SECONDS
        ):
            return None
        return max(self.minimum_delay_seconds, delay)

    def _polite_wait(self, url: str, extra_delay: float = 0.0) -> None:
        """Apply a conservative per-host delay."""
        host = (urlsplit(url).hostname or "").casefold()
        required = max(self.minimum_delay_seconds, extra_delay)
        if (
            not math.isfinite(required)
            or required > MAXIMUM_ROBOTS_CRAWL_DELAY_SECONDS
        ):
            raise ResearchError("remote crawl delay exceeds the bounded safe fetch window")
        previous = self._last_request_by_host.get(host)
        now = self.monotonic()
        if previous is not None and now - previous < required:
            self.sleep(required - (now - previous))
        self._last_request_by_host[host] = self.monotonic()

    def _retry_delay_seconds(self, headers: Mapping[str, Any], attempt: int) -> float:
        """Return a bounded Retry-After or deterministic exponential delay."""
        value = str(headers.get("retry-after") or "").strip()
        delay: float | None = None
        if value:
            try:
                delay = float(value)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(value)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    delay = None
        if delay is None:
            delay = 2.0 ** max(0, attempt - 1)
        return min(60.0, max(0.0, delay))

    def _wire_blocks(self, response: Any) -> Iterable[bytes]:
        """Yield undecoded response bytes from production or test transports."""
        raw = getattr(response, "raw", None)
        if raw is not None and hasattr(raw, "stream"):
            yield from raw.stream(64 * 1024, decode_content=False)
            return
        yield from response.iter_content(64 * 1024)

    def _read_response(self, response: Any, maximum_bytes: int) -> tuple[bytes, str | None]:
        """Read and decode a response with independent wire/decoded limits."""
        length = str(response.headers.get("content-length") or "").strip()
        if length:
            try:
                if int(length) > maximum_bytes:
                    return b"", "response_size_limit_exceeded"
            except ValueError:
                pass
        content_encoding = str(response.headers.get("content-encoding") or "").strip().casefold()
        if content_encoding in {"", "identity"}:
            decompressor = None
        elif content_encoding == "gzip":
            decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        else:
            return b"", "unsupported_content_encoding"
        content = bytearray()
        wire_bytes = 0
        for block in self._wire_blocks(response):
            if not block:
                continue
            wire_bytes += len(block)
            if wire_bytes > maximum_bytes:
                return b"", "response_size_limit_exceeded"
            if decompressor is None:
                if len(content) + len(block) > maximum_bytes:
                    return b"", "response_size_limit_exceeded"
                content.extend(block)
                continue
            pending = block
            while pending:
                remaining = maximum_bytes - len(content)
                try:
                    decoded = decompressor.decompress(pending, remaining + 1)
                except zlib.error:
                    return b"", "invalid_content_encoding"
                content.extend(decoded)
                if len(content) > maximum_bytes:
                    return b"", "response_size_limit_exceeded"
                pending = decompressor.unconsumed_tail
                if pending and len(content) >= maximum_bytes:
                    return b"", "response_size_limit_exceeded"
        if decompressor is not None:
            try:
                tail = decompressor.flush(maximum_bytes - len(content) + 1)
            except zlib.error:
                return b"", "invalid_content_encoding"
            content.extend(tail)
            if len(content) > maximum_bytes:
                return b"", "response_size_limit_exceeded"
            if not decompressor.eof or decompressor.unused_data:
                return b"", "invalid_content_encoding"
        return bytes(content), None

    def _raw_fetch(
        self,
        initial_url: str,
        *,
        maximum_bytes: int,
        extra_delay: float = 0.0,
        enforce_robots: bool = False,
    ) -> dict[str, Any]:
        """Fetch bytes while validating every DNS resolution and redirect hop."""
        current = self._validate_url_with_retries(initial_url)
        chain: list[str] = []
        robots_chain: list[dict[str, Any]] = []
        attempts: list[dict[str, Any]] = []
        for redirect_count in range(MAXIMUM_REDIRECTS + 1):
            current = self._validate_url_with_retries(current)
            hop_delay = extra_delay
            if enforce_robots:
                robots = self.robots_policy(current)
                robots_chain.append({"url": current, **robots})
                if not robots.get("allowed"):
                    return {
                        "status": "robots_disallowed", "final_url": current,
                        "redirect_chain": chain, "robots_policies": robots_chain,
                    }
                hop_delay = max(
                    hop_delay, float(robots.get("crawl_delay_seconds") or 0),
                )
            self.budget.reserve(current)
            if self.on_budget_change is not None:
                self.on_budget_change()
            redirected = False
            for attempt in range(1, MAXIMUM_FETCH_ATTEMPTS + 1):
                self._polite_wait(current, hop_delay)
                response = None
                try:
                    request_headers = _document_request_headers()
                    if self.session is None:
                        response = _pinned_public_get(
                            current, headers=request_headers, timeout=self.timeout,
                        )
                    else:
                        response = self.session.get(
                            current, allow_redirects=False, stream=True,
                            timeout=self.timeout, headers=request_headers,
                        )
                    if self.session is not None and hasattr(self.session, "cookies"):
                        self.session.cookies.clear()
                    status_code = int(response.status_code)
                    selected_headers = _selected_response_headers(response.headers)
                    attempt_record = {
                        "url": current,
                        "attempt": attempt,
                        "http_status": status_code,
                        "selected_response_headers": selected_headers,
                    }
                    if status_code in {301, 302, 303, 307, 308}:
                        attempts.append(attempt_record)
                        chain.append(current)
                        location = str(response.headers.get("location") or "").strip()
                        if not location:
                            return {
                                "status": "redirect_missing_location", "redirect_chain": chain,
                                "robots_policies": robots_chain, "network_attempts": attempts,
                            }
                        if redirect_count >= MAXIMUM_REDIRECTS:
                            return {
                                "status": "redirect_limit_exceeded", "redirect_chain": chain,
                                "robots_policies": robots_chain, "network_attempts": attempts,
                            }
                        try:
                            joined = urljoin(current, location)
                            current = self._validate_url_with_retries(joined)
                        except TransientDNSFailure as exc:
                            attempt_record["error"] = type(exc).__name__
                            return {
                                "status": "request_failed", "error": type(exc).__name__,
                                "redirect_chain": chain, "robots_policies": robots_chain,
                                "network_attempts": attempts,
                            }
                        except (UnsafeURL, ValueError) as exc:
                            attempt_record["error"] = type(exc).__name__
                            return {
                                "status": "unsafe_redirect", "error": str(exc),
                                "redirect_chain": chain, "robots_policies": robots_chain,
                                "network_attempts": attempts,
                            }
                        redirected = True
                        break
                    content_type = _content_type(response)
                    attempt_record["content_type"] = content_type
                    if status_code == 206:
                        attempt_record["error"] = "unexpected_partial_content"
                        attempts.append(attempt_record)
                        return {
                            "status": "unexpected_partial_content",
                            "http_status": status_code, "content_type": content_type,
                            "final_url": current, "redirect_chain": chain,
                            "robots_policies": robots_chain, "network_attempts": attempts,
                        }
                    if (
                        status_code in TRANSIENT_FETCH_STATUSES
                        and content_type not in ALLOWED_CONTENT_TYPES
                        and maximum_bytes != MAXIMUM_ROBOTS_BYTES
                    ):
                        attempt_record["error"] = "transient_http_status"
                        attempts.append(attempt_record)
                        if attempt < MAXIMUM_FETCH_ATTEMPTS:
                            self.sleep(self._retry_delay_seconds(response.headers, attempt))
                            continue
                        chain.append(current)
                        return {
                            "status": "http_error", "http_status": status_code,
                            "content_type": content_type, "final_url": current,
                            "redirect_chain": chain, "robots_policies": robots_chain,
                            "network_attempts": attempts,
                        }
                    if content_type not in ALLOWED_CONTENT_TYPES and maximum_bytes != MAXIMUM_ROBOTS_BYTES:
                        attempts.append(attempt_record)
                        return {
                            "status": "unsupported_content_type", "http_status": status_code,
                            "content_type": content_type, "final_url": current,
                            "redirect_chain": chain, "robots_policies": robots_chain,
                            "network_attempts": attempts,
                        }
                    body, size_error = self._read_response(response, maximum_bytes)
                    attempt_record["captured_body_bytes"] = len(body)
                    attempt_record["body_sha256"] = sha256_bytes(body) if body else ""
                    if size_error:
                        attempt_record["error"] = size_error
                        attempts.append(attempt_record)
                        return {
                            "status": size_error, "http_status": status_code,
                            "content_type": content_type, "final_url": current,
                            "redirect_chain": chain, "robots_policies": robots_chain,
                            "network_attempts": attempts,
                        }
                    challenge = _challenge_page_diagnostic(
                        status_code, response.headers, content_type, body,
                    )
                    if challenge is not None:
                        attempt_record["error"] = "access_challenge"
                        attempts.append(attempt_record)
                        return {
                            "status": "access_challenge", "http_status": status_code,
                            "content_type": content_type, "final_url": current,
                            "redirect_chain": chain, "robots_policies": robots_chain,
                            "network_attempts": attempts, "challenge": challenge, "body": body,
                            "content_encoding": str(response.headers.get("content-encoding") or ""),
                        }
                    if status_code in TRANSIENT_FETCH_STATUSES:
                        attempt_record["error"] = "transient_http_status"
                        attempts.append(attempt_record)
                        if attempt < MAXIMUM_FETCH_ATTEMPTS:
                            self.sleep(self._retry_delay_seconds(response.headers, attempt))
                            continue
                    else:
                        attempts.append(attempt_record)
                    chain.append(current)
                    return {
                        "status": "fetched" if 200 <= status_code < 300 else "http_error",
                        "http_status": status_code,
                        "content_type": content_type,
                        "content_encoding": str(response.headers.get("content-encoding") or ""),
                        "final_url": current,
                        "redirect_chain": chain, "robots_policies": robots_chain,
                        "network_attempts": attempts,
                        "body": body,
                        "etag": str(response.headers.get("etag") or ""),
                        "last_modified": str(response.headers.get("last-modified") or ""),
                    }
                except (
                    requests.RequestException, OSError, http.client.HTTPException,
                    TransientDNSFailure,
                ) as exc:
                    attempts.append({
                        "url": current, "attempt": attempt,
                        "error": type(exc).__name__,
                    })
                    if attempt < MAXIMUM_FETCH_ATTEMPTS:
                        self.sleep(self._retry_delay_seconds({}, attempt))
                        continue
                    return {
                        "status": "request_failed", "error": type(exc).__name__,
                        "final_url": current, "redirect_chain": chain,
                        "robots_policies": robots_chain, "network_attempts": attempts,
                    }
                finally:
                    if response is not None:
                        response.close()
            if redirected:
                continue
            return {
                "status": "request_failed", "error": "retry_loop_exhausted",
                "final_url": current, "redirect_chain": chain,
                "robots_policies": robots_chain, "network_attempts": attempts,
            }
        return {"status": "redirect_limit_exceeded", "redirect_chain": chain,
                "robots_policies": robots_chain, "network_attempts": attempts}

    def robots_policy(self, url: str) -> dict[str, Any]:
        """Return a cached or freshly fetched fail-closed robots decision."""
        canonical = self._validate_url_with_retries(url)
        parsed = urlsplit(canonical)
        origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        cached = self.cache.load_robots(origin)
        if cached is not None:
            return self._evaluate_robots_record(cached, canonical, cache_hit=True)
        robots_url = origin + "/robots.txt"
        raw = self._raw_fetch(
            robots_url, maximum_bytes=MAXIMUM_ROBOTS_BYTES, enforce_robots=False,
        )
        status = raw.get("status")
        http_status = raw.get("http_status")
        crawl_delay = self.minimum_delay_seconds
        if status == "fetched" and http_status == 200:
            text = bytes(raw.get("body") or b"").decode("utf-8", errors="replace")
            parser = RobotFileParser()
            parser.set_url(robots_url)
            policy_kind = ""
            try:
                parser.parse(text.splitlines())
                declared_delay = (
                    parser.crawl_delay(USER_AGENT)
                    or parser.crawl_delay("*")
                    or crawl_delay
                )
                bounded_delay = self._bounded_crawl_delay(declared_delay)
            except (ValueError, TypeError, ArithmeticError):
                bounded_delay = None
                policy_kind = "disallow_all_unparseable_robots"
            if bounded_delay is None and policy_kind != "disallow_all_unparseable_robots":
                crawl_delay = self.minimum_delay_seconds
                policy_kind = "disallow_all_unusable_crawl_delay"
            elif bounded_delay is not None:
                crawl_delay = bounded_delay
                policy_kind = "rules"
        elif http_status in {404, 410}:
            text, policy_kind = "", "allow_all_not_found"
        elif http_status in {401, 403}:
            text, policy_kind = "", "disallow_all_access_denied"
        else:
            text, policy_kind = "", "disallow_all_unavailable"
        record = {
            "policy_kind": policy_kind,
            "robots_text": text,
            "crawl_delay_seconds": crawl_delay,
            "robots_url": robots_url,
            "robots_http_status": http_status,
            "fetched_at": utc_now(),
        }
        self.cache.save_robots(origin, record)
        return self._evaluate_robots_record(record, canonical, cache_hit=False)

    def _evaluate_robots_record(
        self, record: Mapping[str, Any], canonical_url: str, *, cache_hit: bool,
    ) -> dict[str, Any]:
        """Evaluate cached origin-wide robots rules for the current destination path."""
        kind = str(record.get("policy_kind") or "")
        bounded_delay = self._bounded_crawl_delay(record.get("crawl_delay_seconds"))
        if bounded_delay is None:
            allowed, reason = False, "robots_crawl_delay_unusable_fail_closed"
        elif kind == "rules":
            parser = RobotFileParser()
            parser.set_url(str(record.get("robots_url") or ""))
            try:
                parser.parse(str(record.get("robots_text") or "").splitlines())
                allowed = parser.can_fetch(USER_AGENT, canonical_url)
                reason = "robots_allowed" if allowed else "robots_disallowed"
            except (ValueError, TypeError, ArithmeticError):
                allowed, reason = False, "robots_unparseable_fail_closed"
        elif kind == "allow_all_not_found":
            allowed, reason = True, "robots_not_found"
        elif kind == "disallow_all_access_denied":
            allowed, reason = False, "robots_access_denied"
        elif kind == "disallow_all_unusable_crawl_delay":
            allowed, reason = False, "robots_crawl_delay_unusable_fail_closed"
        elif kind == "disallow_all_unparseable_robots":
            allowed, reason = False, "robots_unparseable_fail_closed"
        else:
            allowed, reason = False, "robots_unavailable_fail_closed"
        return {
            "allowed": allowed,
            "reason": reason,
            "crawl_delay_seconds": bounded_delay if bounded_delay is not None else self.minimum_delay_seconds,
            "robots_url": str(record.get("robots_url") or ""),
            "robots_http_status": record.get("robots_http_status"),
            "fetched_at": record.get("fetched_at"),
            "cache_hit": cache_hit,
        }

    def fetch(self, url: str) -> dict[str, Any]:
        """Fetch one public page, respecting robots and private cache."""
        local = self._fetch_from_local_archive(url)
        if local is not None:
            return local
        try:
            canonical = self._validate_url_with_retries(url)
        except TransientDNSFailure as exc:
            return {
                "status": "request_failed", "error": type(exc).__name__,
                "requested_url": str(url), "cache_hit": False,
            }
        except UnsafeURL as exc:
            return {"status": "unsafe_url", "error": str(exc), "requested_url": str(url)}
        cached = self.cache.load_page(canonical)
        if cached is not None:
            metadata, body = cached
            return {**metadata, "body": body, "cache_hit": True}
        try:
            robots = self.robots_policy(canonical)
        except FetchLimitExceeded:
            raise
        except (UnsafeURL, ResearchError) as exc:
            return {
                "status": "robots_check_failed", "error": f"{type(exc).__name__}: {exc}",
                "requested_url": canonical, "cache_hit": False,
            }
        if not robots.get("allowed"):
            return {
                "status": "robots_disallowed", "robots": robots,
                "requested_url": canonical, "cache_hit": False,
            }
        try:
            raw = self._raw_fetch(
                canonical,
                maximum_bytes=MAXIMUM_RESPONSE_BYTES,
                extra_delay=float(robots.get("crawl_delay_seconds") or 0),
                enforce_robots=True,
            )
        except FetchLimitExceeded:
            raise
        except TransientDNSFailure as exc:
            return {
                "status": "request_failed", "error": type(exc).__name__,
                "requested_url": canonical, "cache_hit": False,
            }
        except UnsafeURL as exc:
            return {
                "status": "unsafe_redirect",
                "error": str(exc), "requested_url": canonical, "cache_hit": False,
            }
        body = bytes(raw.pop("body", b""))
        final_url_value = str(raw.get("final_url") or canonical)
        mtf_validation: dict[str, Any] | None = None
        if (
            raw.get("status") == "fetched"
            and _host(final_url_value) == "margaretthatcher.org"
            and re.fullmatch(r"/document/\d+", urlsplit(final_url_value).path.rstrip("/"), re.I)
        ):
            mtf_validation = inspect_mtf_document(
                final_url_value, str(raw.get("content_type") or ""), body,
            )
            if not mtf_validation.get("valid"):
                raw["status"] = str(mtf_validation.get("status") or "invalid_official_document")
                raw["error"] = str(mtf_validation.get("reason") or "official document validation failed")
        network_attempts = raw.get("network_attempts")
        network_attempt_count = len(network_attempts) if isinstance(network_attempts, list) else 0
        retry_count = sum(
            1 for attempt in network_attempts or []
            if int(attempt.get("attempt") or 1) > 1
        )
        metadata = {
            **raw,
            "requested_url": canonical,
            "robots": robots,
            "fetched_at": utc_now(),
            "captured_body_bytes": len(body),
            "body_sha256": sha256_bytes(body) if body else "",
            "cache_hit": False,
            "fetch_policy_version": FETCH_POLICY_VERSION,
            "network_attempt_count": network_attempt_count,
            "retry_count": retry_count,
            "mtf_document_validation": mtf_validation or {},
        }
        if raw.get("status") == "fetched":
            self.cache.save_page(canonical, metadata, body)
            final_url = canonicalise_url(str(raw.get("final_url") or canonical))
            if final_url != canonical and self.cache.load_page(final_url) is None:
                self.cache.save_page(
                    final_url,
                    {**metadata, "cache_alias_of": canonical, "requested_url": final_url},
                    body,
                )
        return {**metadata, "body": body}


def _html_metadata(body: bytes) -> tuple[str, dict[str, str]]:
    """Extract readable HTML and concise bibliographic metadata."""
    soup = BeautifulSoup(body, "lxml")
    for node in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
        node.decompose()
    title = " ".join(soup.title.get_text(" ", strip=True).split()) if soup.title else ""
    metadata: dict[str, str] = {"title": title}
    meta_names = {
        "author": "author", "article:author": "author", "citation_author": "author",
        "og:site_name": "publisher", "application-name": "publisher",
        "article:published_time": "date", "date": "date", "citation_date": "date",
        "dc.date": "date", "dc.creator": "author", "citation_title": "title",
    }
    for tag in soup.find_all("meta"):
        name = str(tag.get("name") or tag.get("property") or "").casefold()
        field = meta_names.get(name)
        value = " ".join(str(tag.get("content") or "").split())
        if field and value and not metadata.get(field):
            metadata[field] = value[:500]
    canonical_tag = soup.find("link", attrs={"rel": lambda value: value and "canonical" in value})
    if canonical_tag and canonical_tag.get("href"):
        metadata["declared_canonical_url"] = str(canonical_tag["href"])[:2000]
    heading = soup.find("h1")
    if heading:
        metadata["heading"] = " ".join(heading.get_text(" ", strip=True).split())[:500]
    text = " ".join(soup.get_text(" ", strip=True).split())
    return text[:MAXIMUM_EXTRACTED_TEXT_CHARS], metadata


def _mtf_html_metadata(
    body: bytes,
    validation: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Extract only the validated MTF transcript/article rather than page chrome."""
    soup = BeautifulSoup(body, "lxml")
    selector_kind = str(validation.get("selector_kind") or "")
    if selector_kind == "legacy":
        article = soup.select_one("#documentbody")
    elif selector_kind == "current_mirror":
        article = soup.select_one(".document-body")
    else:
        article = soup.select_one("article.node-archive-document")
    text = " ".join(article.get_text(" ", strip=True).split()) if article else ""
    metadata: dict[str, Any] = {
        "title": str(validation.get("title") or ""),
        "author": str(validation.get("author") or ""),
        "date": str(validation.get("date") or ""),
        "publisher": MARGARET_THATCHER_FOUNDATION_PUBLISHER,
        "declared_canonical_url": str(validation.get("declared_canonical_url") or ""),
        "mtf_document_validated": True,
        "mtf_document_number": str(validation.get("document_number") or ""),
        "mtf_canonical_url": str(validation.get("canonical_url") or ""),
        "mtf_selector_kind": selector_kind,
        "mtf_article_text_sha256": str(validation.get("article_text_sha256") or ""),
    }
    return text[:MAXIMUM_EXTRACTED_TEXT_CHARS], metadata


def _plain_text(body: bytes) -> tuple[str, dict[str, str]]:
    """Decode a bounded plain-text response."""
    return " ".join(body.decode("utf-8", errors="replace").split())[:MAXIMUM_EXTRACTED_TEXT_CHARS], {}


def _pdf_text(body: bytes) -> tuple[str, dict[str, str]]:
    """Fail closed until a resource-isolated PDF extractor is explicitly configured."""
    del body
    raise ResearchError("pdf_text_extraction_not_safely_configured")


def extract_page_text(fetch: Mapping[str, Any]) -> dict[str, Any]:
    """Turn a fetched supported document into readable text and metadata."""
    if fetch.get("status") != "fetched":
        return {"status": "inaccessible", "text": "", "metadata": {}, "error": fetch.get("status")}
    body = bytes(fetch.get("body") or b"")
    content_type = str(fetch.get("content_type") or "").casefold()
    try:
        if content_type in {"text/html", "application/xhtml+xml"}:
            validation = fetch.get("mtf_document_validation")
            if isinstance(validation, dict) and validation.get("valid"):
                text, metadata = _mtf_html_metadata(body, validation)
            else:
                text, metadata = _html_metadata(body)
        elif content_type == "text/plain":
            text, metadata = _plain_text(body)
        elif content_type in {"application/xml", "text/xml"}:
            text, metadata = _plain_text(body)
        elif content_type == "application/pdf":
            text, metadata = _pdf_text(body)
        else:
            return {
                "status": "inaccessible", "text": "", "metadata": {},
                "error": "unsupported_content_type",
            }
    except ResearchError as exc:
        return {"status": "inaccessible", "text": "", "metadata": {}, "error": str(exc)}
    return {
        "status": "extracted",
        "text": text,
        "text_sha256": sha256_bytes(text.encode("utf-8")),
        "metadata": metadata,
        "error": "",
    }


def parse_hansard_json(body: bytes) -> dict[str, Any]:
    """Parse official Hansard JSON without joining unrelated contributions."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {
            "status": "inaccessible",
            "metadata": {},
            "contributions": [],
            "error": "invalid_hansard_json",
        }
    if not isinstance(payload, dict):
        return {
            "status": "inaccessible",
            "metadata": {},
            "contributions": [],
            "error": "invalid_hansard_json_shape",
        }
    overview = payload.get("Overview")
    items = payload.get("Items")
    if not isinstance(overview, dict) or not isinstance(items, list):
        return {
            "status": "inaccessible",
            "metadata": {},
            "contributions": [],
            "error": "missing_hansard_overview_or_items",
        }
    metadata = {
        "title": str(overview.get("Title") or ""),
        "date": str(overview.get("Date") or ""),
        "chamber": str(overview.get("House") or ""),
        "location": str(overview.get("Location") or ""),
        "volume": overview.get("VolumeNo"),
        "debate_id": str(overview.get("ExtId") or overview.get("Id") or ""),
        "publisher": "UK Parliament",
    }
    contributions: list[dict[str, Any]] = []

    def walk_debate(
        debate: Mapping[str, Any],
        inherited: Mapping[str, Any],
        *,
        depth: int,
    ) -> None:
        if depth > 32:
            raise ResearchError("Hansard JSON child-debate nesting is excessive")
        local_overview = debate.get("Overview")
        local_items = debate.get("Items")
        local_children = debate.get("ChildDebates", [])
        if not isinstance(local_overview, Mapping):
            local_overview = {}
        if not isinstance(local_items, list) or not isinstance(local_children, list):
            raise ResearchError("Hansard JSON child debate has invalid collections")
        local = {
            "title": str(local_overview.get("Title") or inherited.get("title") or ""),
            "date": str(local_overview.get("Date") or inherited.get("date") or ""),
            "chamber": str(local_overview.get("House") or inherited.get("chamber") or ""),
            "location": str(
                local_overview.get("Location") or inherited.get("location") or ""
            ),
            "volume": (
                local_overview.get("VolumeNo")
                if local_overview.get("VolumeNo") is not None
                else inherited.get("volume")
            ),
            "debate_id": str(
                local_overview.get("ExtId")
                or local_overview.get("Id")
                or inherited.get("debate_id")
                or ""
            ),
        }
        current_column = ""
        for raw_item in local_items:
            if not isinstance(raw_item, Mapping) or raw_item.get("ItemType") != "Contribution":
                continue
            html_value = str(raw_item.get("Value") or "")
            text = " ".join(
                BeautifulSoup(html_value, "lxml").get_text(" ", strip=True).split()
            )
            if str(raw_item.get("HRSTag") or "").casefold() == "hs_columnnumber":
                numbers = re.findall(r"\b\d{1,5}\b", text)
                if numbers:
                    current_column = numbers[-1]
                continue
            speaker = str(raw_item.get("AttributedTo") or "").strip()
            if not text or not speaker:
                continue
            bounded_text = text[:MAXIMUM_EXTRACTED_TEXT_CHARS]
            contribution_id = str(
                raw_item.get("ExternalId") or raw_item.get("ItemId") or ""
            )
            contributions.append({
                "contribution_id": contribution_id,
                "speaker": speaker,
                "text": bounded_text,
                "text_sha256": sha256_bytes(bounded_text.encode("utf-8")),
                "order_in_section": raw_item.get("OrderInSection"),
                "column": current_column,
                "debate_title": local["title"],
                "chamber": local["chamber"],
                "date": local["date"],
                "volume": local["volume"],
                "debate_id": local["debate_id"],
                "stable_locator": (
                    f"debate {local['debate_id']}, contribution {contribution_id}"
                    + (f", column {current_column}" if current_column else "")
                ),
            })
        for child in local_children:
            if not isinstance(child, Mapping):
                raise ResearchError("Hansard JSON child debate is not an object")
            walk_debate(child, local, depth=depth + 1)

    walk_debate(payload, metadata, depth=0)
    return {
        "status": "parsed",
        "metadata": metadata,
        "contributions": contributions,
        "payload_sha256": sha256_bytes(body),
        "error": "",
    }


def _token_spans(value: str) -> tuple[list[str], list[tuple[int, int]]]:
    """Return tokens and original character spans."""
    matches = list(_WORD_RE.finditer(value))
    return (
        [match.group(0).replace("’", "'").casefold() for match in matches],
        [(match.start(), match.end()) for match in matches],
    )


def _sequence_index(haystack: Sequence[str], needle: Sequence[str]) -> int | None:
    """Return the first contiguous token-sequence position."""
    if not needle or len(needle) > len(haystack):
        return None
    first = needle[0]
    for index, value in enumerate(haystack[:len(haystack) - len(needle) + 1]):
        if value == first and list(haystack[index:index + len(needle)]) == list(needle):
            return index
    return None


def _concise_context(text: str, start: int, end: int, *, radius: int = 450) -> str:
    """Return bounded surrounding context without excessive page reproduction."""
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    context = text[left:right].strip()
    if left:
        context = "…" + context
    if right < len(text):
        context += "…"
    return context[:1200]


def _near_match(
    page_tokens: Sequence[str],
    spans: Sequence[tuple[int, int]],
    page_text: str,
    wording: str,
) -> dict[str, Any] | None:
    """Find a guarded lexical variant near distinctive anchors."""
    wanted = word_tokens(wording)
    if len(wanted) < 8 or not page_tokens:
        return None
    anchor_positions = [
        index for index, token in enumerate(wanted)
        if token not in _STOPWORDS and len(token) >= 5
    ][:3]
    starts: set[int] = set()
    for anchor_index in anchor_positions:
        token = wanted[anchor_index]
        for page_index, page_token in enumerate(page_tokens):
            if page_token == token:
                starts.update(max(0, page_index - anchor_index + offset) for offset in range(-2, 3))
                if len(starts) > 500:
                    break
    if not starts:
        return None
    minimum = max(8, math.floor(len(wanted) * 0.80))
    maximum = min(len(page_tokens), math.ceil(len(wanted) * 1.20))
    wanted_numbers = {token for token in wanted if token.isdigit()}
    wanted_negation = bool({"no", "not", "never", "nor", "without"} & set(wanted))
    best: tuple[float, int, int] | None = None
    for start in sorted(starts):
        for length in range(minimum, maximum + 1):
            end = start + length
            if end > len(page_tokens):
                continue
            candidate = list(page_tokens[start:end])
            if {token for token in candidate if token.isdigit()} != wanted_numbers:
                continue
            candidate_negation = bool({"no", "not", "never", "nor", "without"} & set(candidate))
            if candidate_negation != wanted_negation:
                continue
            score = SequenceMatcher(a=wanted, b=candidate, autojunk=False).ratio()
            if score >= 0.82 and (best is None or score > best[0]):
                best = (score, start, end)
    if best is None:
        return None
    score, start, end = best
    char_start, char_end = spans[start][0], spans[end - 1][1]
    return {
        "match_type": "near_exact_variant",
        "wording_similarity": round(score, 6),
        "supporting_passage": page_text[char_start:char_end].strip()[:1000],
        "surrounding_context": _concise_context(page_text, char_start, char_end),
        "span": [char_start, char_end],
    }


def extract_supporting_passage(
    target: Mapping[str, Any],
    page_text: str,
) -> dict[str, Any]:
    """Locate exact wording, recorded variants, fragments, or a guarded near match."""
    page_tokens, spans = _token_spans(page_text)
    candidates: list[tuple[str, str]] = [
        ("exact_quotation", str(target.get("quotation_text") or "")),
    ]
    candidates.extend(
        ("recorded_variant", str(value)) for value in target.get("recorded_variants", [])
    )
    for kind, wording in candidates:
        wanted = word_tokens(wording)
        index = _sequence_index(page_tokens, wanted)
        if index is None:
            continue
        char_start, char_end = spans[index][0], spans[index + len(wanted) - 1][1]
        return {
            "match_type": kind,
            "wording_similarity": 1.0 if kind == "exact_quotation" else round(
                SequenceMatcher(
                    a=word_tokens(str(target.get("quotation_text") or "")),
                    b=wanted,
                    autojunk=False,
                ).ratio(), 6,
            ),
            "supporting_passage": page_text[char_start:char_end].strip()[:1000],
            "surrounding_context": _concise_context(page_text, char_start, char_end),
            "span": [char_start, char_end],
            "matched_recorded_wording": wording,
        }
    clauses = [
        str(clause) for clause in target.get("deterministic_clauses", [])
        if len(word_tokens(str(clause))) >= 3
    ]
    clause_matches: list[tuple[int, int, str]] = []
    for clause in clauses:
        wanted = word_tokens(clause)
        index = _sequence_index(page_tokens, wanted)
        if index is None:
            clause_matches = []
            break
        start, end = spans[index][0], spans[index + len(wanted) - 1][1]
        clause_matches.append((start, end, page_text[start:end].strip()))
    if len(clause_matches) >= 2:
        ordered = sorted(clause_matches)
        gaps = [max(0, ordered[index + 1][0] - ordered[index][1])
                for index in range(len(ordered) - 1)]
        return {
            "match_type": "assembled_clauses",
            "wording_similarity": round(
                sum(len(word_tokens(item[2])) for item in clause_matches)
                / max(1, len(word_tokens(str(target.get("quotation_text") or "")))),
                6,
            ),
            "supporting_passage": " […] ".join(
                item[2][:450] for item in clause_matches
            )[:1000],
            "surrounding_context": " | ".join(
                _concise_context(page_text, item[0], item[1], radius=220)
                for item in clause_matches
            )[:1200],
            "span": [min(item[0] for item in clause_matches), max(item[1] for item in clause_matches)],
            "component_spans": [[item[0], item[1]] for item in clause_matches],
            "assembled_gap_characters": gaps,
            "matched_clauses": clauses,
        }
    near = _near_match(
        page_tokens, spans, page_text, str(target.get("quotation_text") or ""),
    )
    if near is not None:
        return near
    for fragment in target.get("distinctive_fragments", []):
        wanted = word_tokens(str(fragment))
        index = _sequence_index(page_tokens, wanted)
        if index is None:
            continue
        char_start, char_end = spans[index][0], spans[index + len(wanted) - 1][1]
        return {
            "match_type": "distinctive_fragment_only",
            "wording_similarity": round(
                len(wanted) / max(1, len(word_tokens(str(target.get("quotation_text") or "")))), 6,
            ),
            "supporting_passage": page_text[char_start:char_end].strip()[:1000],
            "surrounding_context": _concise_context(page_text, char_start, char_end),
            "span": [char_start, char_end],
            "matched_fragment": fragment,
        }
    return {
        "match_type": "none",
        "wording_similarity": 0.0,
        "supporting_passage": "",
        "surrounding_context": "",
        "span": None,
    }


def _host(url: str) -> str:
    """Return a normalised hostname."""
    return (urlsplit(url).hostname or "").casefold().removeprefix("www.")


def _host_matches(host: str, markers: Iterable[str]) -> bool:
    """Return whether a hostname equals or is beneath a marker."""
    return any(host == marker or host.endswith("." + marker) for marker in markers)


def _is_margaret_thatcher_author(value: str) -> bool:
    """Require an explicit Margaret/Lady/Baroness Thatcher author identity."""
    return bool(re.search(
        r"\b(?:Margaret(?:\s+Hilda)?|Lady|Baroness)\s+Thatcher\b|"
        r"\bThatcher,\s*Margaret(?:\s+Hilda)?\b",
        str(value or ""),
        re.I,
    ))


def _strong_primary_locator(host: str, url: str, locator: str) -> bool:
    """Recognise only deterministic official-document URL shapes."""
    path = urlsplit(url).path.rstrip("/")
    if host == "margaretthatcher.org":
        return bool(re.fullmatch(r"/document/\d+", path, re.I))
    if host == "hansard.parliament.uk":
        return bool(re.search(r"/(?:commons|lords)/.+/(?:debates|writtenanswers)/", path, re.I))
    if host == "api.parliament.uk":
        return bool(re.search(r"/(?:historic-hansard|hansard|documents?)/", path, re.I))
    if host.endswith("nationalarchives.gov.uk"):
        return bool(re.search(r"/(?:record|catalogue|webarchive)/", path, re.I)) and bool(locator)
    return False


def _precise_publication_locator(host: str, url: str, text: str) -> str:
    """Return a book/item identity plus a page marker, never a collection path alone."""
    parsed = urlsplit(url)
    parameters = dict(parse_qsl(parsed.query, keep_blank_values=False))
    extracted_page = re.search(r"\[PDF page (\d+)\]", text)
    if host == "books.google.com":
        book_id = str(parameters.get("id") or "").strip()
        page = str(parameters.get("pg") or parameters.get("page") or "").strip()
        if not page and extracted_page:
            page = extracted_page.group(1)
        if book_id and page:
            return f"Google Books {book_id}, page {page}"
        return ""
    if host == "archive.org":
        item_match = re.search(r"/details/([^/]+)", parsed.path, re.I)
        page_match = re.search(r"/page/([^/]+)", parsed.path, re.I)
        page = str(parameters.get("page") or parameters.get("pg") or "").strip()
        if not page and page_match:
            page = page_match.group(1)
        if not page and extracted_page:
            page = extracted_page.group(1)
        if item_match and page:
            return f"Internet Archive {item_match.group(1)}, page {page}"
        return ""
    return ""


def _stable_locator(url: str, text: str, metadata: Mapping[str, Any]) -> str:
    """Extract a concise stable locator when one is actually present."""
    parsed = urlsplit(url)
    host = _host(url)
    publication_locator = _precise_publication_locator(host, url, text)
    if publication_locator:
        return publication_locator
    mtf = re.fullmatch(r"/document/(\d+)", parsed.path.rstrip("/"), re.I)
    if mtf:
        return f"Document {mtf.group(1)}"
    page = re.search(r"\[PDF page (\d+)\]", text)
    if page:
        return f"Page {page.group(1)}"
    document = _DOCUMENT_NUMBER.search(" ".join((text[:1500], str(metadata.get("title") or ""))))
    if document:
        return f"Document {document.group(1)}"
    if parsed.path and parsed.path != "/":
        return parsed.path
    return ""


def classify_candidate(
    target: Mapping[str, Any],
    *,
    url: str,
    extraction: Mapping[str, Any],
    match: Mapping[str, Any],
    fetch_status: str = "fetched",
) -> dict[str, Any]:
    """Classify fetched evidence deterministically; domain alone never promotes it."""
    if fetch_status != "fetched" or extraction.get("status") != "extracted":
        return {
            "classification": "inaccessible",
            "accepted_as_evidence": False,
            "evidence_roles": [],
            "decision_reason": str(extraction.get("error") or fetch_status),
        }
    text = str(extraction.get("text") or "")
    metadata = extraction.get("metadata") if isinstance(extraction.get("metadata"), dict) else {}
    context = " ".join((
        str(match.get("surrounding_context") or ""),
        str(metadata.get("title") or ""),
        str(metadata.get("heading") or ""),
        str(metadata.get("author") or ""),
        str(metadata.get("publisher") or ""),
    ))
    host = _host(url)
    match_type = str(match.get("match_type") or "none")
    has_wording = match_type in {"exact_quotation", "recorded_variant"}
    is_assembled = match_type == "assembled_clauses"
    nearby_context = str(match.get("surrounding_context") or "")
    has_attribution = bool(_ATTRIBUTION_RE.search(nearby_context))
    metadata_attribution = bool(_ATTRIBUTION_RE.search(" ".join((
        str(metadata.get("title") or ""), str(metadata.get("heading") or ""),
        str(metadata.get("author") or ""),
    ))))
    locator = _stable_locator(url, str(match.get("supporting_passage") or ""), metadata)
    precise_publication_locator = _precise_publication_locator(
        host, url, str(match.get("supporting_passage") or ""),
    )
    is_thatcher_authored_publication = bool(
        host in {"archive.org", "books.google.com"}
        and _is_margaret_thatcher_author(str(metadata.get("author") or ""))
        and precise_publication_locator
    )
    strong_primary_locator = _strong_primary_locator(host, url, locator)
    if host == "margaretthatcher.org":
        expected_number = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
        strong_primary_locator = bool(
            strong_primary_locator
            and metadata.get("mtf_document_validated") is True
            and str(metadata.get("mtf_document_number") or "") == expected_number
            and str(metadata.get("mtf_canonical_url") or "") == canonicalise_url(url)
        )
    roles: list[str] = []
    if has_wording:
        roles.append("wording_verification")
    if is_assembled:
        roles.append("composite_wording_diagnosis")
    if has_attribution:
        roles.append("attribution_support")
    if _DATE.search(context):
        roles.append("date")
    if any(word in context.casefold() for word in ("speech", "interview", "conference", "debate")):
        roles.append("source_event")

    trusted_secondary = _host_matches(host, RELIABLE_SECONDARY_HOST_MARKERS)
    trusted_source = trusted_secondary or _host_matches(host, PRIMARY_HOSTS) or host in {
        "archive.org", "books.google.com",
    }

    if _host_matches(host, AGGREGATOR_MARKERS):
        classification = "quotation_aggregation"
        accepted = False
        reason = "quotation aggregation is discovery-only and cannot establish attribution"
    elif has_wording and re.search(
        r"\b(?:according to|source:)\s+(?:Wikiquote|BrainyQuote|AZ Quotes|Goodreads)\b",
        context, re.I,
    ):
        classification = "circular_attribution"
        accepted = False
        reason = "page attribution is circular to another quotation aggregation"
    elif (
        is_assembled
        and (
            (
                _host_matches(host, PRIMARY_HOSTS)
                and (has_attribution or metadata_attribution)
                and strong_primary_locator
            )
            or is_thatcher_authored_publication
        )
    ):
        classification = "contradictory_evidence"
        accepted = True
        reason = "primary document contains the stored clauses separately rather than as one contiguous quotation"
    elif trusted_source and has_wording and _CONTRADICTORY_RE.search(context):
        classification = "contradictory_evidence"
        accepted = True
        reason = "a recognised source explicitly signals contrary or misattributed authorship"
    elif (
        _host_matches(host, PRIMARY_HOSTS)
        and has_wording
        and (has_attribution or metadata_attribution)
        and strong_primary_locator
    ):
        classification = "strong_primary_evidence"
        accepted = True
        reason = "fetched primary document contains wording, Thatcher attribution and locator"
    elif (
        is_thatcher_authored_publication
        and has_wording and (has_attribution or metadata_attribution)
    ):
        classification = "strong_primary_evidence"
        accepted = True
        reason = "fetched Thatcher-authored publication contains wording and precise locator"
    elif has_wording and has_attribution and trusted_secondary and _RECOLLECTION_RE.search(context):
        classification = "secondary_recollection"
        accepted = True
        reason = "later recollection contains wording and explicit Thatcher attribution"
    elif (
        has_wording and has_attribution and trusted_secondary
        and _CONTEMPORARY_RE.search(context) and _DATE.search(context)
    ):
        classification = "contemporary_report"
        accepted = True
        reason = "dated report contains wording and explicit Thatcher attribution"
    elif has_wording and has_attribution and trusted_secondary:
        classification = "reliable_secondary_evidence"
        accepted = True
        reason = "reliable secondary page contains wording and explicit Thatcher attribution"
    elif match_type in {"near_exact_variant", "distinctive_fragment_only"}:
        classification = "similar_sentiment_only"
        accepted = False
        reason = "only a guarded variant or fragment was found without sufficient source proof"
    elif has_wording:
        classification = "discovery_only"
        accepted = False
        reason = "wording appears but source identity or attribution is not reliable enough"
    else:
        classification = "no_support"
        accepted = False
        reason = "fetched page does not contain the quotation or an authorised variant"
    if classification not in SOURCE_CLASSIFICATIONS:
        raise AssertionError("unsupported deterministic source classification")
    return {
        "classification": classification,
        "accepted_as_evidence": accepted,
        "evidence_roles": list(dict.fromkeys(roles)),
        "decision_reason": reason,
        "speaker_attribution_present": has_attribution or metadata_attribution,
        "stable_locator": locator,
    }


def outcome_for_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    search_complete: bool,
    budget_limited: bool = False,
) -> tuple[str, str]:
    """Choose one advisory evidence outcome from fetched candidates."""
    accepted = [candidate for candidate in candidates if candidate.get("accepted_as_evidence")]
    for candidate in accepted:
        if candidate.get("classification") == "contradictory_evidence":
            return "contradictory_or_misattributed", "fetched evidence explicitly contradicts the stored attribution"
    if any(
        candidate.get("classification") == "strong_primary_evidence"
        and candidate.get("match_type") == "exact_quotation"
        for candidate in accepted
    ):
        return "exact_primary_wording_found", "exact wording was fetched from a primary document with attribution and locator"
    if any(
        candidate.get("classification") == "strong_primary_evidence"
        and candidate.get("match_type") == "recorded_variant"
        for candidate in accepted
    ):
        return "primary_variant_found", "a recorded wording variant was fetched from a primary document"
    if any(candidate.get("classification") == "contemporary_report" for candidate in accepted):
        return "contemporary_secondary_attribution_found", "a dated contemporary report directly attributes the wording to Thatcher"
    if any(candidate.get("classification") == "secondary_recollection" for candidate in accepted):
        return "secondary_recollection_found", "a later recollection attributes the wording to Thatcher"
    if budget_limited:
        return (
            "search_incomplete_due_to_request_cap",
            "the authorised search-request or page-fetch cap was reached before every scheduled query completed",
        )
    if any(candidate.get("classification") == "inaccessible" for candidate in candidates):
        return "search_incomplete_due_to_access", "one or more promising result pages could not be inspected"
    if candidates and any(candidate.get("classification") not in {"no_support", "quotation_aggregation"} for candidate in candidates):
        return "promising_but_insufficient", "discovery leads were found but none meets the evidence standard"
    if search_complete:
        return (
            "no_reliable_evidence_found",
            "No reliable evidence found within the configured Discovery Engine website collection.",
        )
    return "search_incomplete_due_to_access", "search was not executed to completion"


def suggested_action(target: Mapping[str, Any], outcome: str) -> str:
    """Return an advisory-only future action."""
    if outcome in {"exact_primary_wording_found", "primary_variant_found"}:
        return "consider_unblocking"
    if outcome == "contradictory_or_misattributed":
        return "contradictory_evidence_review"
    if "final_unresolved_status" in target.get("target_origins", []):
        return "retain_unresolved"
    if outcome in {
        "promising_but_insufficient", "search_incomplete_due_to_access",
        "search_incomplete_due_to_request_cap",
    }:
        return "further_archive_research"
    return "keep_blocked"


def _promising_score(result: Mapping[str, Any]) -> tuple[int, int]:
    """Rank discovery leads for bounded fetching without using snippets as evidence."""
    url = str(result.get("canonical_url") or "")
    host = _host(url)
    title = str(result.get("title") or "").casefold()
    score = 0
    if _host_matches(host, PRIMARY_HOSTS):
        score += 100
    if host in {"archive.org", "books.google.com"}:
        score += 80
    if _host_matches(host, RELIABLE_SECONDARY_HOST_MARKERS):
        score += 50
    if _host_matches(host, AGGREGATOR_MARKERS):
        score -= 60
    if any(word in title for word in ("speech", "interview", "transcript", "memoir", "hansard")):
        score += 20
    return score, -int(result.get("result_rank") or 999)


def _target_index(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Index validated manifest targets."""
    return {
        str(target["quote_id"]): dict(target)
        for target in manifest.get("targets", [])
        if isinstance(target, dict)
    }


def scheduled_query_count(target: Mapping[str, Any]) -> int:
    """Return this target's deterministic scheduled-query count."""
    return min(PLANNED_QUERY_STAGES_PER_QUOTE, len(target.get("queries") or []))


def empty_search_accounting() -> dict[str, Any]:
    """Return request-only accounting for a run that made no request."""
    return {
        "price_per_1000_searches_usd": None,
        "search_cost_estimate_available": False,
        "free_quota_requests_known_at_start": 0,
        "free_quota_requests_used": 0,
        "paid_or_conservatively_priced_requests": None,
        "requests_started": 0,
        "requests_completed": 0,
        "requests_ambiguous": 0,
        "retries": 0,
        "cache_hits": 0,
        "estimated_search_cost_usd": None,
        "warning_threshold_usd": None,
        "hard_stop_usd": None,
        "warning_threshold_reached": False,
        "remaining_authorised_budget_usd": None,
        "planned_request_limit": PLANNED_SEARCH_REQUESTS,
        "absolute_request_hard_stop": ABSOLUTE_SEARCH_REQUESTS,
        "llm_or_generative_provider_cost_usd": "0",
    }


def initialise_run_state(
    manifest: Mapping[str, Any],
    *,
    status: str,
    backend_config: Mapping[str, Any] | None = None,
    budget: SearchBudget | None = None,
) -> dict[str, Any]:
    """Create a manifest-bound resumable state without production data writes."""
    validate_query_manifest(manifest)
    cases = {}
    for target in manifest["targets"]:
        quote_id = str(target["quote_id"])
        cases[quote_id] = {
            "quote_id": quote_id,
            "status": "pending",
            "queries_attempted": [],
            "result_count": 0,
            "candidate_sources": [],
            "query_runs": {},
            "evaluated_canonical_urls": {},
            "recommended_outcome": None,
            "recommendation_rationale": "",
            "completed_at": None,
        }
    accounting = budget.as_dict() if budget is not None else empty_search_accounting()
    accounting["llm_or_generative_provider_cost_usd"] = "0"
    return {
        "schema_version": 1,
        "run_kind": "historical_context_search_research_state",
        "programme_version": PROGRAMME_VERSION,
        "manifest_hash": manifest["manifest_hash"],
        "input_hashes": dict(manifest["input_hashes"]),
        "run_status": status,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "backend_config": dict(backend_config or {}),
        "search_accounting": accounting,
        "page_fetch_accounting": PageFetchBudget().as_dict(),
        "search_operations": [],
        "ledger_records": [],
        "cases": cases,
        "failure_reason": "",
    }


def validate_resume_state(state: Mapping[str, Any], manifest: Mapping[str, Any]) -> None:
    """Refuse stale, corrupted or ambiguous resume state."""
    if state.get("programme_version") != PROGRAMME_VERSION:
        raise ResearchError("run-state programme version differs")
    if state.get("manifest_hash") != manifest.get("manifest_hash"):
        raise ResearchError("run-state query manifest hash differs")
    if state.get("input_hashes") != manifest.get("input_hashes"):
        raise ResearchError("run-state immutable input hashes differ")
    operations = state.get("search_operations")
    if not isinstance(operations, list):
        raise ResearchError("run-state search operations are invalid")
    accounting = state.get("search_accounting")
    if not isinstance(accounting, dict):
        raise ResearchError("run-state search accounting is invalid")
    restored = SearchBudget.from_dict(accounting)
    if restored.requests_started != len(operations):
        raise ResearchError("run-state request accounting differs from operation ledger")
    cases = state.get("cases")
    if not isinstance(cases, dict) or set(cases) != set(_target_index(manifest)):
        raise ResearchError("run-state quotation cases differ from the manifest")
    ambiguous = [
        operation for operation in operations
        if isinstance(operation, dict) and operation.get("status") in {"sending", "ambiguous"}
    ]
    if ambiguous:
        raise AmbiguousSearchRequest(
            "run state contains a possibly transmitted search request; automatic repeat refused"
        )


def current_input_hashes_match(manifest: Mapping[str, Any], root: Path = ROOT) -> tuple[bool, dict[str, str]]:
    """Re-derive and compare authoritative input hashes."""
    current = derive_target_set(root)["input_hashes"]
    return current == manifest.get("input_hashes"), current


def codex_review_is_complete(review: Mapping[str, Any] | None) -> bool:
    """Validate one explicit manual accept/reject decision."""
    if not isinstance(review, Mapping) or review.get("decision") not in {"accept", "reject"}:
        return False
    if not str(review.get("rationale") or "").strip():
        return False
    return review.get("decision") == "reject" or all(
        review.get(name) is True for name in CODEX_REVIEW_REQUIRED_CHECKS
    )


def load_codex_reviews(
    path: Path, manifest: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Load a manifest-bound manual review sidecar without modifying it."""
    if not path.is_file():
        return {}
    review_document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(review_document, dict):
        raise ResearchError("Codex evidence-review sidecar is not an object")
    if review_document.get("manifest_hash") != manifest.get("manifest_hash"):
        raise ResearchError("Codex evidence-review sidecar is bound to another manifest")
    raw_reviews = review_document.get("candidate_reviews", {})
    if not isinstance(raw_reviews, dict):
        raise ResearchError("Codex evidence-review candidate map is invalid")
    reviews = {
        str(candidate_id): dict(review)
        for candidate_id, review in raw_reviews.items()
        if isinstance(review, dict)
    }
    raw_quotation_reviews = review_document.get("quotation_reviews", {})
    if not isinstance(raw_quotation_reviews, dict):
        raise ResearchError("Codex evidence-review quotation map is invalid")
    target_ids = set(_target_index(manifest))
    for quote_id, review in raw_quotation_reviews.items():
        quote_id = str(quote_id)
        if quote_id not in target_ids:
            raise ResearchError("Codex evidence-review quotation is outside the manifest")
        if not isinstance(review, dict):
            raise ResearchError("Codex evidence-review quotation entry is invalid")
        outcome = str(review.get("recommended_outcome") or "")
        if outcome not in CODEX_QUOTATION_REVIEW_OUTCOMES:
            raise ResearchError(
                "Codex quotation review may only select a non-resolution outcome"
            )
        if not str(review.get("rationale") or "").strip():
            raise ResearchError("Codex evidence-review quotation rationale is missing")
        reviews[f"quotation:{quote_id}"] = dict(review)
    return reviews


class SearchResearchRunner:
    """Breadth-first, bounded and resumable deterministic search orchestrator."""

    def __init__(
        self,
        *,
        manifest: Mapping[str, Any],
        state: dict[str, Any],
        backend: SearchBackend,
        budget: SearchBudget,
        fetcher: SafeFetcher,
        cache: ResearchCache,
        state_path: Path,
        ledger_path: Path,
        maximum_retries: int = 2,
        codex_reviews: Mapping[str, Mapping[str, Any]] | None = None,
        local_archive_index: LocalMTFDocumentIndex | None = None,
    ) -> None:
        """Bind validated inputs and resumable state for bounded research."""
        validate_query_manifest(manifest)
        validate_resume_state(state, manifest)
        self.manifest = dict(manifest)
        self.targets = _target_index(manifest)
        self.state = state
        self.backend = backend
        self.budget = budget
        self.fetcher = fetcher
        self.cache = cache
        self.state_path = state_path
        self.ledger_path = ledger_path
        self.maximum_retries = maximum_retries
        self.local_archive_index = local_archive_index
        self.codex_reviews = {
            str(candidate_id): dict(review)
            for candidate_id, review in (codex_reviews or {}).items()
        }
        self.fetcher.on_budget_change = self.checkpoint
        self._reconcile_candidate_taxonomy()
        self._reconcile_provisional_cases()

    def _process_local_archive_discovery(self) -> None:
        """Discover and validate local MTF mirror documents before paid searching."""
        if self.local_archive_index is None:
            return
        inventory = self.local_archive_index.inventory
        self.state["local_archive_discovery"] = {
            "enabled": True,
            "policy_version": LOCAL_ARCHIVE_POLICY_VERSION,
            "inventory_sha256": inventory.sha256,
            "document_count": inventory.document_count,
            "private_root_not_recorded": True,
            "snippets_are_evidence": False,
        }
        for quote_id in sorted(self.targets):
            target = self.targets[quote_id]
            case = self.state["cases"][quote_id]
            previous = case.get("local_archive_discovery")
            if (
                isinstance(previous, Mapping)
                and previous.get("status") == "complete"
                and previous.get("inventory_sha256") == inventory.sha256
            ):
                continue
            discovery = self.local_archive_index.discover(
                str(target["quotation_text"]),
                variants=tuple(target.get("recorded_variants") or ()),
            )
            results = list(discovery.get("results") or [])
            rows: list[dict[str, Any]] = []
            for rank, result in enumerate(results[:10], 1):
                result_url = str(result.get("result_url") or "")
                rows.append({
                    "record_type": "local_archive_discovery_result",
                    "result_record_id": sha256_bytes(canonical_json_bytes({
                        "manifest": self.manifest["manifest_hash"],
                        "inventory": inventory.sha256,
                        "quote_id": quote_id,
                        "rank": rank,
                        "result_url": result_url,
                    })),
                    "target_quote_id": quote_id,
                    "quotation_text": target["quotation_text"],
                    "query": "",
                    "planned_query": "",
                    "query_adapted_for_backend": False,
                    "query_reason": "operator_owned_local_mtf_full_text_discovery",
                    "query_request_order": 0,
                    "result_rank": rank,
                    "title": str(result.get("title") or ""),
                    "result_url": result_url,
                    "canonical_url": "",
                    "snippet": "",
                    "display_link": str(result.get("display_link") or ""),
                    "mime": str(result.get("mime") or ""),
                    "discovery_document_id": str(result.get("document_id") or ""),
                    "snippet_is_evidence": False,
                    "backend": "operator_owned_local_mtf_archive_index",
                    "search_timestamp": utc_now(),
                    "search_cache_hit": False,
                    "local_archive_inventory_sha256": inventory.sha256,
                    "local_archive_selected_anchors": list(
                        discovery.get("selected_anchors") or []
                    ),
                    "local_archive_matched_anchor_tokens": list(
                        result.get("matched_anchor_tokens") or []
                    ),
                    "local_archive_exact_phrase_indexes": list(
                        result.get("exact_phrase_indexes") or []
                    ),
                    "fetch_status": "not_selected_for_fetch",
                    "classification": "discovery_only",
                    "decision": "",
                    "decision_reason": "",
                })
            rows = deduplicate_results(rows)
            evaluated = case.setdefault("evaluated_canonical_urls", {})
            processed = 0
            for row in rows:
                canonical = str(row.get("canonical_url") or "")
                self.state["ledger_records"].append(row)
                if row.get("decision") != "candidate_for_fetch":
                    continue
                if canonical in evaluated:
                    row.update({
                        "decision": "already_evaluated_for_quote",
                        "decision_reason": "canonical URL was evaluated earlier for this quotation",
                    })
                    continue
                candidate = self._fetch_result(target, row)
                if not any(
                    item.get("candidate_id") == candidate.get("candidate_id")
                    for item in case["candidate_sources"]
                ):
                    case["candidate_sources"].append(candidate)
                evaluated[canonical] = candidate.get("candidate_id")
                final_canonical = str(candidate.get("canonical_url") or "")
                if final_canonical:
                    evaluated[final_canonical] = candidate.get("candidate_id")
                processed += 1
            outcome, rationale = outcome_for_candidates(
                case["candidate_sources"], search_complete=False,
            )
            case["automatic_interim_outcome"] = outcome
            case["automatic_interim_rationale"] = rationale
            provisional_ids = [
                str(candidate.get("candidate_id"))
                for candidate in case["candidate_sources"]
                if candidate.get("classification") == "strong_primary_evidence"
                and candidate.get("match_type") in {"exact_quotation", "recorded_variant"}
                and candidate.get("accepted_as_evidence")
                and candidate.get("candidate_id") not in set(
                    case.get("provisional_rejected_candidate_ids", [])
                )
            ]
            if provisional_ids:
                provisional_candidates = [
                    candidate for candidate in case["candidate_sources"]
                    if str(candidate.get("candidate_id") or "") in set(provisional_ids)
                ]
                provisional_outcome, provisional_rationale = outcome_for_candidates(
                    provisional_candidates, search_complete=False,
                )
                case.update({
                    "status": "awaiting_codex_review",
                    "provisional_candidate_ids": provisional_ids,
                    "recommended_outcome": provisional_outcome,
                    "recommendation_rationale": (
                        "A locally mirrored, fetched and validated decisive-primary "
                        "candidate paused paid searches pending Codex review: "
                        f"{provisional_rationale}"
                    ),
                    "completed_at": None,
                })
            case["local_archive_discovery"] = {
                "status": "complete",
                "policy_version": LOCAL_ARCHIVE_POLICY_VERSION,
                "inventory_sha256": inventory.sha256,
                "indexed_document_count": discovery.get("indexed_document_count"),
                "selected_anchors": discovery.get("selected_anchors", []),
                "candidate_count_before_cap": discovery.get(
                    "candidate_count_before_cap", 0
                ),
                "candidate_cap_reached": bool(
                    discovery.get("candidate_cap_reached")
                ),
                "result_count": len(rows),
                "fetched_candidate_count": processed,
                "completed_at": utc_now(),
                "snippets_are_evidence": False,
            }
            self.checkpoint()

    def _reconcile_candidate_taxonomy(self) -> None:
        """Apply tightened quote-aggregation taxonomy to cached advisory candidates."""
        changed_ids: set[str] = set()
        for case in self.state.get("cases", {}).values():
            for candidate in case.get("candidate_sources", []):
                if (
                    candidate.get("fetch_status") == "fetched"
                    and _host_matches(
                        _host(str(candidate.get("canonical_url") or "")),
                        AGGREGATOR_MARKERS,
                    )
                    and candidate.get("classification") != "quotation_aggregation"
                ):
                    candidate.update({
                        "classification": "quotation_aggregation",
                        "accepted_as_evidence": False,
                        "decision_reason": (
                            "quotation aggregation is discovery-only and cannot establish attribution"
                        ),
                        "classification_policy_version": CLASSIFICATION_POLICY_VERSION,
                    })
                    changed_ids.add(str(candidate.get("candidate_id") or ""))
        if not changed_ids:
            return
        for row in self.state.get("ledger_records", []):
            if str(row.get("candidate_id") or "") in changed_ids:
                row.update({
                    "classification": "quotation_aggregation",
                    "decision": "rejected_or_discovery_candidate",
                    "decision_reason": (
                        "quotation aggregation is discovery-only and cannot establish attribution"
                    ),
                })

    def _reconcile_provisional_cases(self) -> None:
        """Resume rejected provisional matches or complete reviewed acceptances."""
        for case in self.state.get("cases", {}).values():
            if case.get("status") != "awaiting_codex_review":
                continue
            candidate_ids = [str(value) for value in case.get("provisional_candidate_ids", [])]
            decisions = [self.codex_reviews.get(candidate_id) for candidate_id in candidate_ids]
            accepted = [
                review for review in decisions
                if codex_review_is_complete(review) and review.get("decision") == "accept"
            ]
            if accepted:
                accepted_candidate_ids = {
                    candidate_id
                    for candidate_id, review in zip(candidate_ids, decisions)
                    if codex_review_is_complete(review)
                    and review.get("decision") == "accept"
                }
                reviewed_candidates = [
                    candidate for candidate in case.get("candidate_sources", [])
                    if str(candidate.get("candidate_id") or "")
                    in accepted_candidate_ids
                ]
                reviewed_outcome, reviewed_rationale = outcome_for_candidates(
                    reviewed_candidates, search_complete=False,
                )
                case.update({
                    "status": "complete_decisive_primary_reviewed",
                    "recommended_outcome": reviewed_outcome,
                    "recommendation_rationale": (
                        "Codex accepted decisive fetched primary evidence after all required "
                        f"checks: {reviewed_rationale}"
                    ),
                    "completed_at": utc_now(),
                })
            elif decisions and all(
                codex_review_is_complete(review) and review.get("decision") == "reject"
                for review in decisions
            ):
                prior_rejections = set(case.get("provisional_rejected_candidate_ids", []))
                case.update({
                    "status": "pending",
                    "provisional_rejected_candidate_ids": sorted(
                        prior_rejections | set(candidate_ids)
                    ),
                    "provisional_candidate_ids": [],
                    "completed_at": None,
                })

    def checkpoint(self) -> None:
        """Atomically checkpoint state and regenerate JSONL after each case action."""
        self.state["updated_at"] = utc_now()
        accounting = self.budget.as_dict()
        accounting["llm_or_generative_provider_cost_usd"] = "0"
        self.state["search_accounting"] = accounting
        self.state["page_fetch_accounting"] = self.fetcher.budget.as_dict()
        atomic_write_json(self.state_path, self.state, mode=0o600)
        atomic_write_jsonl(self.ledger_path, self.state["ledger_records"])

    def _provider_query(self, query: str) -> str:
        """Apply a backend's deterministic documented query constraints."""
        prepare = getattr(self.backend, "prepare_query", None)
        prepared = str(prepare(query) if callable(prepare) else query).strip()
        if not prepared:
            raise RejectedSearchQuery("search query is empty after provider preparation")
        return prepared

    def _operation(
        self,
        cache_key: str,
        quote_id: str,
        query_row: Mapping[str, Any],
        provider_query: str,
        retry: int,
        replacement_operation_id: str = "",
    ) -> dict[str, Any]:
        """Reserve and persist a search lifecycle record before transmission."""
        reservation = self.budget.reserve_request(retry=retry > 0)
        operation = {
            "operation_id": sha256_bytes(canonical_json_bytes({
                "manifest_hash": self.manifest["manifest_hash"],
                "cache_key": cache_key,
                "attempt": retry + 1,
            })),
            "cache_key": cache_key,
            "quote_id": quote_id,
            "query_sha256": sha256_bytes(str(query_row["query"]).encode("utf-8")),
            "provider_query_sha256": sha256_bytes(provider_query.encode("utf-8")),
            "provider_query_adapted": provider_query != str(query_row["query"]),
            "provider_query_characters": len(provider_query),
            "provider_query_words": len(provider_query.split()),
            "replacement_for_rejected_query_operation_id": replacement_operation_id,
            "query_request_order": query_row["request_order"],
            "attempt_number": retry + 1,
            "status": "prepared",
            "prepared_at": utc_now(),
            **reservation,
        }
        self.state["search_operations"].append(operation)
        self.checkpoint()
        operation["status"] = "sending"
        operation["sending_at"] = utc_now()
        self.checkpoint()
        return operation

    def _structured_search(
        self, quote_id: str, query_row: Mapping[str, Any],
    ) -> tuple[list[dict[str, str]], bool]:
        """Use cache or make one safely accounted structured search."""
        query = str(query_row["query"])
        provider_query = self._provider_query(query)
        key = self.cache.search_key(self.backend, provider_query, TOP_RESULTS)
        planned_query_sha256 = sha256_bytes(query.encode("utf-8"))
        rejected_planned_operations = [
            operation for operation in self.state["search_operations"]
            if operation.get("quote_id") == quote_id
            and int(operation.get("query_request_order") or 0)
            == int(query_row["request_order"])
            and operation.get("query_sha256") == planned_query_sha256
            and operation.get("status") == "confirmed_error"
            and (
                operation.get("error_kind") == "rejected_search_query"
                or str(operation.get("error") or "").endswith(
                    "structured search HTTP 422"
                )
            )
        ]
        replacement_operation_id = (
            str(rejected_planned_operations[-1].get("operation_id") or "")
            if provider_query != query and rejected_planned_operations
            else ""
        )
        cached = self.cache.load_search(key)
        if cached is not None:
            cached_results = cached.get("results")
            if (
                cached.get("backend") != self.backend.name
                or cached.get("backend_version") != self.backend.version
                or cached.get("query") != provider_query
                or not isinstance(cached_results, list)
                or len(cached_results) > TOP_RESULTS
                or any(
                    not isinstance(result, dict)
                    or not isinstance(result.get("result_url", ""), str)
                    or not isinstance(result.get("title", ""), str)
                    or not isinstance(result.get("snippet", ""), str)
                    for result in cached_results
                )
            ):
                raise ResearchError("completed search cache payload is invalid")
            self.budget.cache_hits += 1
            return list(cached_results), True
        prior = [
            operation for operation in self.state["search_operations"]
            if operation.get("cache_key") == key
        ]
        if any(operation.get("status") in {"sending", "ambiguous"} for operation in prior):
            raise AmbiguousSearchRequest("possibly billed cached query may not be repeated")
        if any(operation.get("status") == "completed" for operation in prior):
            raise AmbiguousSearchRequest(
                "completed paid search has no valid response cache; rebilling refused"
            )
        confirmed_errors = [
            operation for operation in prior
            if operation.get("status") == "confirmed_error"
        ]
        if any(
            operation.get("error_kind") == "rejected_search_query"
            or str(operation.get("error") or "").endswith("structured search HTTP 422")
            for operation in confirmed_errors
        ):
            raise RejectedSearchQuery(
                "provider previously rejected this query; automatic retransmission refused"
            )
        if confirmed_errors:
            raise ConfirmedSearchError(
                "a confirmed non-retryable search error is recorded; automatic retransmission refused"
            )
        cancelled_prepared = False
        for operation in prior:
            if operation.get("status") == "prepared":
                operation.update({
                    "status": "cancelled_before_send",
                    "completed_at": utc_now(),
                })
                cancelled_prepared = True
        if cancelled_prepared:
            self.checkpoint()
        ensure_authenticated = getattr(self.backend, "ensure_authenticated", None)
        if callable(ensure_authenticated):
            ensure_authenticated()
        confirmed_retries = sum(
            operation.get("status") == "confirmed_retryable_error" for operation in prior
        )
        if confirmed_retries > self.maximum_retries:
            raise RetryableSearchError("configured search retry limit was already exhausted")
        prior_attempts = len(prior)
        for retry in range(confirmed_retries, self.maximum_retries + 1):
            attempt_index = (
                prior_attempts + (retry - confirmed_retries)
                + (1 if replacement_operation_id else 0)
            )
            operation = self._operation(
                key, quote_id, query_row, provider_query, attempt_index,
                replacement_operation_id,
            )
            try:
                results = self.backend.search(provider_query, number=TOP_RESULTS)
            except RetryableSearchError as exc:
                self.budget.complete_request()
                operation.update({
                    "status": "confirmed_retryable_error",
                    "completed_at": utc_now(),
                    "error": str(exc),
                })
                self.checkpoint()
                if retry >= self.maximum_retries:
                    raise
                continue
            except ConfirmedSearchError as exc:
                self.budget.complete_request()
                operation.update({
                    "status": "confirmed_error", "completed_at": utc_now(), "error": str(exc),
                    "error_kind": (
                        "rejected_search_query"
                        if isinstance(exc, RejectedSearchQuery)
                        else "backend_error"
                    ),
                })
                self.checkpoint()
                raise
            except AmbiguousSearchRequest as exc:
                self.budget.mark_ambiguous()
                operation.update({
                    "status": "ambiguous", "failed_at": utc_now(), "error": str(exc),
                })
                self.state["run_status"] = "unsafe_ambiguous_search_request"
                self.state["failure_reason"] = str(exc)
                self.checkpoint()
                raise
            except Exception as exc:
                self.budget.mark_ambiguous()
                operation.update({
                    "status": "ambiguous", "failed_at": utc_now(),
                    "error": f"unexpected backend failure: {type(exc).__name__}",
                })
                self.state["run_status"] = "unsafe_ambiguous_search_request"
                self.state["failure_reason"] = f"unexpected backend failure: {type(exc).__name__}"
                self.checkpoint()
                raise AmbiguousSearchRequest(self.state["failure_reason"]) from exc
            else:
                self.cache.save_search(key, {
                    "backend": self.backend.name,
                    "backend_version": self.backend.version,
                    "query": provider_query,
                    "planned_query_sha256": sha256_bytes(query.encode("utf-8")),
                    "query_adapted": provider_query != query,
                    "result_count": len(results),
                    "results": results,
                    "searched_at": utc_now(),
                })
                self.budget.complete_request()
                operation.update({
                    "status": "completed", "completed_at": utc_now(),
                    "result_count": len(results),
                })
                self.checkpoint()
                return results, False
        raise AssertionError("search retry loop did not terminate")

    def _fetch_result(
        self,
        target: Mapping[str, Any],
        result_record: dict[str, Any],
    ) -> dict[str, Any]:
        """Fetch, extract and classify one search lead without trusting its snippet."""
        canonical = str(result_record.get("canonical_url") or "")
        parsed = urlsplit(canonical)
        if (
            (parsed.hostname or "").casefold() in SEARCH_RESULT_ONLY_HOSTS
            and parsed.path.rstrip("/") == "/search"
        ):
            result_record.update({
                "fetch_status": "not_fetched_search_result_page",
                "classification": "discovery_only",
                "decision": "search_result_page_is_not_evidence",
            })
            return {
                "candidate_id": sha256_bytes(canonical_json_bytes({
                    "quote_id": target["quote_id"], "url": canonical,
                })),
                "canonical_url": canonical,
                "classification": "discovery_only",
                "accepted_as_evidence": False,
                "decision_reason": "ordinary search result pages are never evidence",
                "match_type": "none",
                "supporting_passage": "",
                "surrounding_context": "",
                "evidence_roles": [],
            }
        fetch = self.fetcher.fetch(canonical)
        body = bytes(fetch.pop("body", b""))
        extraction = extract_page_text({**fetch, "body": body})
        if extraction.get("status") == "extracted":
            match = extract_supporting_passage(target, str(extraction.get("text") or ""))
        else:
            match = {
                "match_type": "none", "wording_similarity": 0.0,
                "supporting_passage": "", "surrounding_context": "", "span": None,
            }
        final_url = str(fetch.get("final_url") or canonical)
        try:
            final_url = canonicalise_url(final_url)
        except UnsafeURL:
            final_url = canonical
        classified = classify_candidate(
            target,
            url=final_url,
            extraction=extraction,
            match=match,
            fetch_status=str(fetch.get("status") or "inaccessible"),
        )
        metadata = extraction.get("metadata") if isinstance(extraction.get("metadata"), dict) else {}
        passage = str(match.get("supporting_passage") or "")
        candidate = {
            "candidate_id": sha256_bytes(canonical_json_bytes({
                "quote_id": target["quote_id"], "url": final_url,
                "page_hash": fetch.get("body_sha256"), "passage": passage,
            })),
            "title": str(metadata.get("title") or result_record.get("title") or "")[:500],
            "author_or_speaker": str(
                metadata.get("author")
                or ("Margaret Thatcher" if classified.get("speaker_attribution_present") else "")
            )[:500],
            "publisher_or_archive": str(metadata.get("publisher") or _host(final_url))[:500],
            "source_publisher": str(
                fetch.get("source_publisher") or metadata.get("publisher") or ""
            )[:500],
            "retrieval_transport": str(fetch.get("retrieval_transport") or "public_http")[:200],
            "transport_url": final_url,
            "local_archive_hit": bool(fetch.get("local_archive_hit")),
            "local_archive_policy_version": str(
                fetch.get("local_archive_policy_version") or ""
            ),
            "local_archive_relative_path": str(
                fetch.get("local_archive_relative_path") or ""
            )[:2000],
            "local_archive_file_sha256": str(
                fetch.get("local_archive_file_sha256") or ""
            ),
            "local_archive_inventory_sha256": str(
                fetch.get("local_archive_inventory_sha256") or ""
            ),
            "network_attempt_count": int(fetch.get("network_attempt_count") or 0),
            "date": str(
                metadata.get("date")
                or ((_DATE.search(str(match.get("surrounding_context") or "")) or [""])[0])
            )[:200],
            "stable_locator": classified.get("stable_locator", ""),
            "canonical_url": final_url,
            "page_sha256": str(fetch.get("body_sha256") or ""),
            "page_text_sha256": str(extraction.get("text_sha256") or ""),
            "http_status": fetch.get("http_status"),
            "content_type": fetch.get("content_type"),
            "fetch_status": fetch.get("status"),
            "fetch_cache_hit": bool(fetch.get("cache_hit")),
            "redirect_chain": fetch.get("redirect_chain", []),
            "match_type": match.get("match_type"),
            "contiguous_or_assembled": (
                "assembled" if match.get("match_type") == "assembled_clauses"
                else "contiguous" if match.get("match_type") not in {None, "none"}
                else "not_matched"
            ),
            "component_spans": match.get("component_spans", []),
            "assembled_gap_characters": match.get("assembled_gap_characters", []),
            "matched_clauses": match.get("matched_clauses", []),
            "wording_similarity": match.get("wording_similarity"),
            "supporting_passage": passage,
            "supporting_passage_sha256": sha256_bytes(passage.encode("utf-8")) if passage else "",
            "surrounding_context": str(match.get("surrounding_context") or "")[:1200],
            **classified,
            "classification_policy_version": CLASSIFICATION_POLICY_VERSION,
            "automatic_score_is_not_final_evidence_review": True,
        }
        result_record.update({
            "fetch_status": fetch.get("status"),
            "fetched_final_url": final_url,
            "page_sha256": fetch.get("body_sha256", ""),
            "classification": candidate["classification"],
            "decision": "accepted_candidate" if candidate["accepted_as_evidence"] else "rejected_or_discovery_candidate",
            "candidate_id": candidate["candidate_id"],
        })
        return candidate

    def _process_query(
        self, quote_id: str, query_row: Mapping[str, Any], *, scheduled_stage: int | None = None,
    ) -> None:
        """Resume one query/result pipeline without duplicating billing or ledger rows."""
        target = self.targets[quote_id]
        case = self.state["cases"][quote_id]
        stage = int(scheduled_stage or query_row["request_order"])
        query_order = int(query_row["request_order"])
        planned_query = str(query_row["query"])
        provider_query = self._provider_query(planned_query)
        order_key = (
            str(query_order) if stage == query_order
            else f"stage-{stage}-query-{query_order}"
        )
        query_runs = case.setdefault("query_runs", {})
        evaluated = case.setdefault("evaluated_canonical_urls", {})
        query_run = query_runs.get(order_key)
        if query_run is None:
            results, cache_hit = self._structured_search(quote_id, query_row)
            rows = []
            for rank, result in enumerate(results[:TOP_RESULTS], 1):
                result_url = str(result.get("result_url") or "")
                row = {
                    "record_type": "search_result",
                    "result_record_id": sha256_bytes(canonical_json_bytes({
                        "manifest": self.manifest["manifest_hash"], "quote_id": quote_id,
                        "query_order": int(query_row["request_order"]), "rank": rank,
                        "result_url": result_url,
                    })),
                    "target_quote_id": quote_id,
                    "quotation_text": target["quotation_text"],
                    "query": provider_query,
                    "planned_query": planned_query,
                    "query_adapted_for_backend": provider_query != planned_query,
                    "query_reason": query_row["reason"],
                    "query_request_order": query_row["request_order"],
                    "result_rank": rank,
                    "title": str(result.get("title") or ""),
                    "result_url": result_url,
                    "canonical_url": "",
                    "snippet": str(result.get("snippet") or ""),
                    "display_link": str(result.get("display_link") or ""),
                    "mime": str(result.get("mime") or ""),
                    "discovery_document_id": str(result.get("document_id") or ""),
                    "snippet_is_evidence": False,
                    "backend": self.backend.name,
                    "search_timestamp": utc_now(),
                    "search_cache_hit": cache_hit,
                    "fetch_status": "not_selected_for_fetch",
                    "classification": "discovery_only",
                    "decision": "",
                    "decision_reason": "",
                }
                rows.append(row)
            rows = deduplicate_results(rows)
            for row in rows:
                canonical = str(row.get("canonical_url") or "")
                parsed = urlsplit(canonical) if canonical else None
                path_head = (parsed.path.strip("/").split("/", 1)[0].casefold()
                             if parsed else "")
                if (
                    parsed and (parsed.hostname or "").casefold().removeprefix("www.")
                    in {"x.com", "twitter.com"}
                    and path_head == BOT_X_ACCOUNT
                ):
                    row.update({
                        "decision": "excluded_bot_x_account",
                        "decision_reason": "the bot's own X account is excluded from discovery",
                    })
                elif row.get("decision") == "candidate_for_fetch" and canonical in evaluated:
                    row.update({
                        "decision": "already_evaluated_for_quote",
                        "decision_reason": "this quotation already evaluated the canonical URL",
                    })
            ranked = sorted(
                (row for row in rows if row.get("decision") == "candidate_for_fetch"),
                key=_promising_score, reverse=True,
            )
            selected = {row["result_record_id"] for row in ranked[:3]}
            for row in rows:
                if row.get("decision") == "candidate_for_fetch" and row["result_record_id"] not in selected:
                    row.update({
                        "decision": "not_selected_for_fetch",
                        "decision_reason": "lower-ranked lead retained for a later query occurrence",
                    })
                self.state["ledger_records"].append(row)
            query_run = {
                "status": "results_recorded",
                "request_order": int(query_row["request_order"]),
                "query": provider_query,
                "planned_query": planned_query,
                "query_adapted_for_backend": provider_query != planned_query,
                "result_count": len(rows),
                "search_cache_hit": cache_hit,
                "ledger_record_ids": [row["result_record_id"] for row in rows],
                "selected_record_ids": [
                    row["result_record_id"] for row in ranked[:3]
                ],
                "processed_record_ids": [],
            }
            query_runs[order_key] = query_run
            self.checkpoint()

        ledger_by_id = {
            row.get("result_record_id"): row for row in self.state["ledger_records"]
            if isinstance(row, dict) and row.get("result_record_id")
        }
        for record_id in query_run.get("selected_record_ids", []):
            if record_id in query_run.get("processed_record_ids", []):
                continue
            row = ledger_by_id.get(record_id)
            if row is None:
                raise ResearchError("query progress references a missing ledger record")
            canonical = str(row.get("canonical_url") or "")
            if canonical in evaluated:
                row.update({
                    "decision": "already_evaluated_for_quote",
                    "decision_reason": "canonical URL was evaluated by an earlier result",
                })
            else:
                candidate = self._fetch_result(target, row)
                if not any(
                    item.get("candidate_id") == candidate.get("candidate_id")
                    for item in case["candidate_sources"]
                ):
                    case["candidate_sources"].append(candidate)
                evaluated[canonical] = candidate.get("candidate_id")
                final_canonical = str(candidate.get("canonical_url") or "")
                if final_canonical:
                    evaluated[final_canonical] = candidate.get("candidate_id")
            query_run.setdefault("processed_record_ids", []).append(record_id)
            self.checkpoint()

        if query_run.get("status") != "complete":
            query_run["status"] = "complete"
            if not any(
                int(item["request_order"]) == int(query_row["request_order"])
                for item in case["queries_attempted"]
            ):
                case["queries_attempted"].append({
                    "request_order": query_row["request_order"],
                    "scheduled_stage": stage,
                    "query": provider_query,
                    "planned_query": planned_query,
                    "query_adapted_for_backend": provider_query != planned_query,
                    "result_count": query_run["result_count"],
                    "search_cache_hit": query_run["search_cache_hit"],
                })
                case["result_count"] += int(query_run["result_count"])
        outcome, rationale = outcome_for_candidates(case["candidate_sources"], search_complete=False)
        case["automatic_interim_outcome"] = outcome
        case["automatic_interim_rationale"] = rationale
        rejected_ids = set(case.get("provisional_rejected_candidate_ids", []))
        provisional_ids = [
            str(candidate.get("candidate_id"))
            for candidate in case["candidate_sources"]
            if candidate.get("classification") == "strong_primary_evidence"
            and candidate.get("match_type") in {"exact_quotation", "recorded_variant"}
            and candidate.get("accepted_as_evidence")
            and candidate.get("candidate_id") not in rejected_ids
        ]
        if provisional_ids:
            provisional_candidates = [
                candidate for candidate in case["candidate_sources"]
                if str(candidate.get("candidate_id") or "") in set(provisional_ids)
            ]
            provisional_outcome, provisional_rationale = outcome_for_candidates(
                provisional_candidates, search_complete=False,
            )
            case.update({
                "status": "awaiting_codex_review",
                "provisional_candidate_ids": provisional_ids,
                "recommended_outcome": provisional_outcome,
                "recommendation_rationale": (
                    "A deterministic decisive-primary candidate paused further queries pending "
                    f"Codex review: {provisional_rationale}"
                ),
                "completed_at": None,
            })
        self.checkpoint()

    def _query_order_for_stage(self, quote_id: str, stage: int) -> int:
        """Map a scheduled stage directly to its deterministic query order."""
        del quote_id
        return stage

    def run(self) -> dict[str, Any]:
        """Execute breadth-first queries until evidence or a hard boundary stops them."""
        self.state["run_status"] = "running"
        self.checkpoint()
        self._process_local_archive_discovery()
        query_lookup = {
            (str(target["quote_id"]), int(query["request_order"])): query
            for target in self.manifest["targets"]
            for query in target["queries"]
        }
        budget_limited = False
        try:
            for scheduled in self.manifest["global_request_order"]:
                quote_id = str(scheduled["quote_id"])
                case = self.state["cases"][quote_id]
                if str(case.get("status", "")).startswith("complete_"):
                    continue
                if case.get("status") == "awaiting_codex_review":
                    continue
                stage = int(scheduled["quote_request_order"])
                if any(
                    int(item.get("scheduled_stage", item["request_order"])) == stage
                    for item in case["queries_attempted"]
                ):
                    continue
                order = self._query_order_for_stage(quote_id, stage)
                try:
                    self._process_query(
                        quote_id, query_lookup[(quote_id, order)], scheduled_stage=stage,
                    )
                except RejectedSearchQuery as exc:
                    error_record = {
                        "scheduled_stage": stage,
                        "request_order": order,
                        "query_sha256": sha256_bytes(
                            str(query_lookup[(quote_id, order)]["query"]).encode("utf-8")
                        ),
                        "error_kind": "provider_rejected_search_query",
                        "error": str(exc),
                        "recorded_at": utc_now(),
                    }
                    errors = case.setdefault("search_access_errors", [])
                    if not any(
                        int(item.get("scheduled_stage") or 0) == stage
                        for item in errors
                    ):
                        errors.append(error_record)
                    case["queries_attempted"].append({
                        "request_order": order,
                        "scheduled_stage": stage,
                        "query": query_lookup[(quote_id, order)]["query"],
                        "result_count": 0,
                        "search_cache_hit": False,
                        "search_error": "provider_rejected_search_query",
                    })
                    self.checkpoint()
                except (SearchBudgetExceeded, FetchLimitExceeded) as exc:
                    budget_limited = True
                    self.state["run_status"] = "request_cap_reached"
                    self.state["failure_reason"] = str(exc)
                    self.checkpoint()
                    break
        except SearchBackendNotConfigured as exc:
            self.state["run_status"] = (
                "discovery_engine_authentication_or_configuration_failed"
            )
            self.state["failure_reason"] = str(exc)
            self.checkpoint()
        except AmbiguousSearchRequest:
            raise
        except SearchAuthenticationConfigurationError as exc:
            self.state["run_status"] = (
                "discovery_engine_authentication_or_configuration_failed"
            )
            self.state["failure_reason"] = str(exc)
            self.checkpoint()
        except ConfirmedSearchError as exc:
            self.state["run_status"] = "search_backend_error"
            self.state["failure_reason"] = str(exc)
            self.checkpoint()
        except RetryableSearchError as exc:
            self.state["run_status"] = "search_backend_error"
            self.state["failure_reason"] = str(exc)
            self.checkpoint()

        for quote_id, case in self.state["cases"].items():
            if str(case.get("status", "")).startswith("complete_"):
                continue
            if case.get("status") == "awaiting_codex_review":
                continue
            complete = len({
                int(item.get("scheduled_stage", item["request_order"]))
                for item in case["queries_attempted"]
            }) >= scheduled_query_count(self.targets[quote_id])
            has_access_errors = bool(case.get("search_access_errors"))
            outcome, rationale = outcome_for_candidates(
                case["candidate_sources"],
                search_complete=complete and not has_access_errors,
                budget_limited=budget_limited and not complete,
            )
            case.update({
                "status": (
                    "complete_search_with_access_errors"
                    if complete and has_access_errors
                    else "complete_search" if complete
                    else "incomplete_search"
                ),
                "recommended_outcome": outcome,
                "recommendation_rationale": rationale,
                "completed_at": utc_now() if complete else None,
            })
        if self.state["run_status"] == "running":
            self.state["run_status"] = (
                "awaiting_codex_review"
                if any(
                    case.get("status") == "awaiting_codex_review"
                    for case in self.state["cases"].values()
                )
                else "complete"
            )
        self.checkpoint()
        return self.state


def advisory_evidence_document(
    manifest: Mapping[str, Any],
    state: Mapping[str, Any],
    codex_reviews: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the advisory-only evidence decision document."""
    codex_reviews = codex_reviews or {}
    targets = _target_index(manifest)
    quotations = []
    for quote_id in sorted(targets):
        target = targets[quote_id]
        case = state.get("cases", {}).get(quote_id, {})
        quotation_review = codex_reviews.get(f"quotation:{quote_id}")
        candidates = [dict(candidate) for candidate in case.get("candidate_sources") or []]
        for candidate in candidates:
            candidate_id = str(candidate.get("candidate_id") or "")
            if candidate_id in codex_reviews:
                candidate["codex_review"] = dict(codex_reviews[candidate_id])
                candidate["automatic_classification"] = candidate.get("classification")
                candidate["effective_classification"] = candidate["codex_review"].get(
                    "reviewed_classification", candidate.get("classification")
                )
                candidate["automatic_evidence_roles"] = list(
                    candidate.get("evidence_roles") or []
                )
                candidate["effective_evidence_roles"] = list(
                    candidate["codex_review"].get(
                        "reviewed_evidence_roles", candidate.get("evidence_roles") or []
                    )
                )
            else:
                candidate["effective_classification"] = candidate.get("classification")
                candidate["effective_evidence_roles"] = list(
                    candidate.get("evidence_roles") or []
                )
        outcome = str(case.get("recommended_outcome") or "")
        rationale = str(case.get("recommendation_rationale") or "")
        if state.get("run_status") in {
            "search_backend_not_configured",
            "discovery_engine_authentication_or_configuration_failed",
        } and not outcome:
            outcome = "search_incomplete_due_to_access"
            rationale = (
                "Discovery Engine authentication or resource configuration failed; "
                "no inference was made from search metadata."
            )
        if outcome not in OUTCOMES:
            outcome = "search_incomplete_due_to_access"
            rationale = rationale or "Research execution is incomplete."
        automatic_outcome = outcome
        automatic_rationale = rationale
        automatic_accepted = [
            candidate for candidate in candidates if candidate.get("accepted_as_evidence")
        ]
        automatic_rejected = [
            candidate for candidate in candidates if not candidate.get("accepted_as_evidence")
        ]
        all_automatic_positives_reviewed = bool(automatic_accepted) and all(
            codex_review_is_complete(candidate.get("codex_review"))
            for candidate in automatic_accepted
        )
        reviewed_accepted = [
            candidate for candidate in automatic_accepted
            if codex_review_is_complete(candidate.get("codex_review"))
            and candidate.get("codex_review", {}).get("decision") == "accept"
        ]
        manually_rejected = [
            candidate for candidate in automatic_accepted
            if codex_review_is_complete(candidate.get("codex_review"))
            and candidate.get("codex_review", {}).get("decision") == "reject"
        ]
        if all_automatic_positives_reviewed:
            if reviewed_accepted:
                outcome, rationale = outcome_for_candidates(
                    reviewed_accepted,
                    search_complete=case.get("status") == "complete_search",
                )
                rationale = "Codex-reviewed evidence: " + rationale
            elif any(
                candidate.get("classification") == "inaccessible"
                for candidate in automatic_rejected
            ):
                outcome = "search_incomplete_due_to_access"
                rationale = "Codex rejected every automatic positive; inaccessible leads remain."
            elif case.get("status") == "complete_search":
                outcome = "no_reliable_evidence_found"
                rationale = "Codex rejected every automatic positive after the completed search."
            else:
                outcome = "promising_but_insufficient"
                rationale = "Codex rejected every automatic positive before search completion."

        if quotation_review is not None:
            reviewed_outcome = str(quotation_review.get("recommended_outcome") or "")
            if reviewed_outcome not in CODEX_QUOTATION_REVIEW_OUTCOMES:
                raise ResearchError(
                    "Codex quotation review may only select a non-resolution outcome"
                )
            reviewed_rationale = str(quotation_review.get("rationale") or "").strip()
            if not reviewed_rationale:
                raise ResearchError("Codex evidence-review quotation rationale is missing")
            outcome = reviewed_outcome
            rationale = reviewed_rationale

        if not candidates:
            review_summary = {
                "status": "not_applicable_no_fetched_candidates",
                "automatic_score_accepted_without_review": False,
                "rationale": "No fetched passage exists to review.",
            }
        elif not automatic_accepted:
            review_summary = {
                "status": "not_applicable_no_automatic_positive",
                "automatic_score_accepted_without_review": False,
                "rationale": "No candidate met the deterministic evidence threshold.",
            }
        elif all_automatic_positives_reviewed and reviewed_accepted:
            review_summary = {
                "status": "completed_manual_codex_review",
                "source_identity_verified": True,
                "speaker_verified": True,
                "wording_and_semantics_verified": True,
                "reviewed_candidate_count": len(automatic_accepted),
                "reviewed_accepted_candidate_count": len(reviewed_accepted),
                "reviewed_rejected_candidate_count": len(manually_rejected),
                "reviewed_outcome": outcome,
                "automatic_score_accepted_without_review": False,
                "rationale": "Every automatic positive was reviewed; accepted evidence alone determines the outcome.",
            }
        elif all_automatic_positives_reviewed:
            review_summary = {
                "status": "completed_manual_codex_review_no_accepted_resolution",
                "reviewed_candidate_count": len(automatic_accepted),
                "reviewed_accepted_candidate_count": 0,
                "reviewed_rejected_candidate_count": len(manually_rejected),
                "reviewed_outcome": outcome,
                "automatic_score_accepted_without_review": False,
                "rationale": "Every automatic positive was reviewed and rejected.",
            }
        else:
            review_summary = {
                "status": "pending_manual_codex_review",
                "automatic_score_accepted_without_review": False,
                "rationale": "Fetched passages require source, speaker, wording, actor, direction, polarity, date, quantity and match-type review.",
            }
        quotations.append({
            "quote_id": quote_id,
            "quotation_text": target["quotation_text"],
            "target_origins": target["target_origins"],
            "gate_disposition": target.get("gate_disposition"),
            "queries_planned": target.get("queries", []),
            "scheduled_query_stage_count": scheduled_query_count(target),
            "queries_attempted": case.get("queries_attempted", []),
            "search_access_errors": case.get("search_access_errors", []),
            "candidate_sources": candidates,
            "automatic_accepted_candidates": automatic_accepted,
            "accepted_candidates": reviewed_accepted,
            "rejected_candidates": automatic_rejected + manually_rejected,
            "automatic_recommended_outcome": automatic_outcome,
            "automatic_recommendation_rationale": automatic_rationale,
            "recommended_outcome": outcome,
            "recommendation_rationale": rationale,
            "suggested_future_action": suggested_action(target, outcome),
            "advisory_only": True,
            "codex_evidence_review": review_summary,
            "codex_quotation_review": (
                dict(quotation_review) if quotation_review is not None else None
            ),
        })
    automatic_classifications = Counter(
        candidate.get("classification")
        for quotation in quotations for candidate in quotation["candidate_sources"]
    )
    effective_classifications = Counter(
        candidate.get("effective_classification")
        for quotation in quotations for candidate in quotation["candidate_sources"]
    )
    outcomes = Counter(quotation["recommended_outcome"] for quotation in quotations)
    return {
        "schema_version": 1,
        "document_kind": "historical_context_search_evidence_candidates",
        "programme_version": PROGRAMME_VERSION,
        "classification_policy_version": CLASSIFICATION_POLICY_VERSION,
        "manifest_hash": manifest["manifest_hash"],
        "run_status": state.get("run_status"),
        "advisory_only": True,
        "automatic_production_mutation_authorised": False,
        "counts": {
            "quotation_count": len(quotations),
            "candidate_source_count": sum(len(item["candidate_sources"]) for item in quotations),
            "accepted_candidate_count": sum(len(item["accepted_candidates"]) for item in quotations),
            "rejected_candidate_count": sum(len(item["rejected_candidates"]) for item in quotations),
            "provider_rejected_query_count": sum(
                len(item["search_access_errors"]) for item in quotations
            ),
            "source_classifications": dict(
                sorted((str(k), v) for k, v in effective_classifications.items() if k)
            ),
            "automatic_source_classifications": dict(
                sorted((str(k), v) for k, v in automatic_classifications.items() if k)
            ),
            "recommended_outcomes": dict(sorted(outcomes.items())),
        },
        "search_accounting": state.get("search_accounting", empty_search_accounting()),
        "page_fetch_accounting": state.get("page_fetch_accounting", PageFetchBudget().as_dict()),
        "local_archive_discovery": dict(
            state.get("local_archive_discovery") or {
                "enabled": False,
                "policy_version": LOCAL_ARCHIVE_POLICY_VERSION,
            }
        ),
        "quotations": quotations,
    }


def apply_explicit_evidence_semantics(
    case: Mapping[str, Any],
    *,
    usable_evidence_candidate_ids: Sequence[str],
    resolution_support_candidate_ids: Sequence[str],
    sufficient_for_historical_context: bool,
    precise_textual_difference: Any | None = None,
) -> dict[str, Any]:
    """Apply the non-conflated advisory evidence model to one case copy."""
    from historical_context_targeted_evidence_remediation import (
        apply_corrected_evidence_semantics,
    )

    return apply_corrected_evidence_semantics(
        case,
        usable_evidence_candidate_ids=usable_evidence_candidate_ids,
        resolution_support_candidate_ids=resolution_support_candidate_ids,
        sufficient_for_historical_context=sufficient_for_historical_context,
        precise_textual_difference=precise_textual_difference,
        retain_legacy_fields=True,
    )


def validate_explicit_evidence_document(
    document: Mapping[str, Any],
    *,
    report_counts: Mapping[str, int] | None = None,
    raise_on_error: bool = False,
) -> dict[str, Any]:
    """Validate explicit evidence semantics using the shared fail-closed model."""
    from historical_context_targeted_evidence_remediation import (
        validate_evidence_document,
    )

    return validate_evidence_document(
        document,
        report_counts=report_counts,
        raise_on_error=raise_on_error,
    )


def final_report_status(evidence: Mapping[str, Any], state: Mapping[str, Any]) -> str:
    """Choose exactly one required terminal report status."""
    run_status = str(state.get("run_status") or "")
    if run_status in {
        "search_backend_not_configured",
        "discovery_engine_authentication_or_configuration_failed",
    }:
        return "DISCOVERY ENGINE AUTHENTICATION OR CONFIGURATION FAILED"
    if run_status == "request_cap_reached":
        return "RESEARCH INCOMPLETE — REQUEST CAP REACHED"
    if run_status != "complete":
        return "RESEARCH TOOL INCOMPLETE OR UNSAFE"
    if any(
        quotation.get("codex_evidence_review", {}).get("status")
        == "pending_manual_codex_review"
        for quotation in evidence.get("quotations", [])
    ):
        return "RESEARCH TOOL INCOMPLETE OR UNSAFE"
    outcomes = evidence.get("counts", {}).get("recommended_outcomes", {})
    reliable = sum(int(outcomes.get(name, 0)) for name in (
        "exact_primary_wording_found", "primary_variant_found",
        "contemporary_secondary_attribution_found", "secondary_recollection_found",
        "contradictory_or_misattributed",
    ))
    if reliable:
        reliable_names = {
            "exact_primary_wording_found", "primary_variant_found",
            "contemporary_secondary_attribution_found", "secondary_recollection_found",
            "contradictory_or_misattributed",
        }
        proposed = [
            quotation for quotation in evidence.get("quotations", [])
            if quotation.get("recommended_outcome") in reliable_names
        ]
        required_review_fields = (
            "source_identity_verified", "speaker_verified",
            "wording_and_semantics_verified",
        )
        if not proposed or any(
            quotation.get("codex_evidence_review", {}).get("status")
            != "completed_manual_codex_review"
            or quotation.get("codex_evidence_review", {}).get("reviewed_outcome")
            != quotation.get("recommended_outcome")
            or not all(
                quotation.get("codex_evidence_review", {}).get(field) is True
                for field in required_review_fields
            )
            for quotation in proposed
        ):
            return "RESEARCH TOOL INCOMPLETE OR UNSAFE"
        return (
            "AUTOMATED DISCOVERY-ENGINE RESEARCH COMPLETE — "
            "READY FOR EVIDENCE REMEDIATION REVIEW"
        )
    return (
        "AUTOMATED DISCOVERY-ENGINE RESEARCH COMPLETE — "
        "NO NEW RELIABLE EVIDENCE FOUND"
    )


def render_report(
    manifest: Mapping[str, Any],
    state: Mapping[str, Any],
    evidence: Mapping[str, Any],
    *,
    root: Path = ROOT,
) -> str:
    """Render the required concise evidence-led Markdown report."""
    accounting = state.get("search_accounting", empty_search_accounting())
    page_accounting = state.get("page_fetch_accounting", PageFetchBudget().as_dict())
    classifications = evidence.get("counts", {}).get("source_classifications", {})
    outcomes = evidence.get("counts", {}).get("recommended_outcomes", {})
    input_match, current_hashes = current_input_hashes_match(manifest, root)
    backend = state.get("backend_config") or {}
    local_archive = (
        backend.get("local_archive")
        if isinstance(backend.get("local_archive"), Mapping)
        else {"enabled": False}
    )
    quotations = list(evidence.get("quotations", []))
    all_candidates = [
        candidate for quotation in quotations
        for candidate in quotation.get("candidate_sources", [])
    ]
    fetched_pages = {
        candidate.get("page_sha256") for candidate in all_candidates
        if candidate.get("fetch_status") == "fetched" and candidate.get("page_sha256")
    }
    requests_started = int(accounting.get("requests_started", 0) or 0)
    network_urls = int(page_accounting.get("unique_network_urls_requested", 0) or 0)
    total_results = sum(
        int(query.get("result_count", 0) or 0)
        for quotation in quotations
        for query in quotation.get("queries_attempted", [])
    )
    unique_result_urls = {
        str(row.get("canonical_url") or "")
        for row in state.get("ledger_records", [])
        if isinstance(row, dict) and row.get("canonical_url")
    }
    adapted_queries = sum(
        bool(query.get("query_adapted_for_backend"))
        for quotation in quotations
        for query in quotation.get("queries_attempted", [])
    )
    mtf_access_failures = sum(
        candidate.get("fetch_status") == "http_error"
        and int(candidate.get("http_status") or 0) == 403
        and _host(str(candidate.get("canonical_url") or ""))
        == "margaretthatcher.org"
        for candidate in all_candidates
    )
    local_archive_candidates = [
        candidate for candidate in all_candidates
        if candidate.get("local_archive_hit")
    ]
    backend_limit_note = (
        "- Brave's documented `q` limit is 400 characters and 50 words; "
        "over-limit exact phrases use a recorded 48-word contiguous excerpt. "
        "API reference: https://api-dashboard.search.brave.com/api-reference/web/search/get"
        if backend.get("backend") == "brave_web_search_json"
        else "- No backend-specific query adaptation was required."
    )
    if accounting.get("search_cost_estimate_available"):
        cost_lines = [
            f"- Actual or conservatively estimated search cost: US${accounting.get('estimated_search_cost_usd', '0')}.",
            f"- Remaining authorised search budget: US${accounting.get('remaining_authorised_budget_usd', HARD_COST_USD)}.",
        ]
    else:
        cost_lines = [
            "- Discovery Engine search pricing was not supplied, so monetary cost was not estimated; the 150-request hard cap was enforced.",
        ]
    lines = [
        "# Automated historical-context quotation evidence discovery",
        "",
        "## Executive summary",
        "",
    ]
    if state.get("run_status") in {
        "search_backend_not_configured",
        "discovery_engine_authentication_or_configuration_failed",
    }:
        if requests_started == 0 and network_urls == 0:
            lines.append(
                "The deterministic research programme and query plan are complete, but Discovery Engine authentication or resource configuration failed. No live search result or page fetch was treated as evidence, and no production decision was changed."
            )
        else:
            lines.append(
                "A resume attempt could not authenticate to the configured Discovery Engine resource. Previously checkpointed accounting and evidence were retained; this attempt made no additional search request."
            )
    else:
        reliable_count = sum(int(outcomes.get(name, 0)) for name in (
            "exact_primary_wording_found", "primary_variant_found",
            "contemporary_secondary_attribution_found", "secondary_recollection_found",
            "contradictory_or_misattributed",
        ))
        lines.extend([
            f"The programme processed {len(evidence.get('quotations', []))} target quotations using non-generative Google Discovery Engine search over the configured website data store and restricted public-page retrieval. All decisions in the candidate file are advisory and require evidence remediation authorisation before any corpus change.",
            f"Reliable or contradictory findings after evidence review: {reliable_count}. {int(outcomes.get('no_reliable_evidence_found', 0))} quotations completed with no reliable evidence within the configured collection, {int(outcomes.get('promising_but_insufficient', 0))} retained promising but insufficient leads, and {int(outcomes.get('search_incomplete_due_to_access', 0))} retained access limitations; {mtf_access_failures} Margaret Thatcher Foundation leads returned HTTP 403 and were not inferred from snippets.",
        ])
    counts = manifest["target_derivation"]
    lines.extend([
        "",
        "## Target-set derivation",
        "",
        f"- Semantic-gate blocked IDs: {counts['blocked_count']} (expected {counts['blocked_expected_count']}; discrepancy {counts['blocked_count_discrepancy']}).",
        f"- Final unresolved IDs: {counts['unresolved_count']} (expected {counts['unresolved_expected_count']}; discrepancy {counts['unresolved_count_discrepancy']}).",
        f"- Overlap: {counts['overlap_count']}.",
        f"- Deduplicated targets: {counts['deduplicated_target_count']}.",
        "- Targets were derived through the canonical corpus loader and SHA-pinned semantic-gate loader; no conversational count was hardcoded as authority.",
        "",
        "## Search backend, requests and cost",
        "",
        f"- Backend: {backend.get('backend', 'not configured')}.",
        f"- Search scope: {backend.get('search_scope', 'configured_website_data_store')}; full web search: {str(bool(backend.get('full_web_search', False))).lower()}.",
        f"- Google Cloud project: {backend.get('project_id', DISCOVERY_PROJECT_ID)}; location: {backend.get('location', DISCOVERY_LOCATION)}.",
        f"- Discovery Engine ID: {backend.get('engine_id', DISCOVERY_ENGINE_ID)}; data store ID: {backend.get('data_store_id', DISCOVERY_DATA_STORE_ID)}; serving config: {backend.get('serving_config', DISCOVERY_SERVING_CONFIG)}.",
        backend_limit_note,
        f"- Queries adapted to documented backend limits: {adapted_queries}.",
        f"- Provider-rejected queries retained as access diagnostics: {evidence.get('counts', {}).get('provider_rejected_query_count', 0)}.",
        f"- Requests started/completed: {accounting.get('requests_started', 0)}/{accounting.get('requests_completed', 0)}.",
        f"- Unique planned queries transmitted: {accounting.get('unique_queries_started', 0)} of {accounting.get('planned_request_limit', PLANNED_SEARCH_REQUESTS)}.",
        f"- Retries: {accounting.get('retries', 0)}; cache hits: {accounting.get('cache_hits', 0)}; ambiguous requests: {accounting.get('requests_ambiguous', 0)}.",
        *cost_lines,
        "- LLM and generative-model requests: 0; LLM and generative-model cost: US$0.",
        f"- Search results returned: {total_results}; distinct canonical result URLs: {len(unique_result_urls)}.",
        f"- Unique network page URLs requested: {page_accounting.get('unique_network_urls_requested', 0)} of {page_accounting.get('maximum_unique_page_fetches', MAXIMUM_PAGE_FETCHES)}.",
        f"- Distinct successfully fetched evidence documents: {len(fetched_pages)}.",
        "",
        "## Operator-owned local archive",
        "",
        f"- Enabled: {str(bool(local_archive.get('enabled'))).lower()}; policy: `{local_archive.get('policy_version', LOCAL_ARCHIVE_POLICY_VERSION)}`.",
        (
            f"- Numeric MTF documents in the configured inventory: "
            f"{local_archive.get('inventory', {}).get('document_count', 0)}; "
            f"inventory SHA-256: `{local_archive.get('inventory', {}).get('sha256', '')}`."
            if local_archive.get("enabled")
            else "- No local mirror was configured for this run."
        ),
        f"- Locally mirrored fetched candidates: {len(local_archive_candidates)}; these reads made no HTTP, Discovery Engine, provider or LLM request.",
        "- Local files are transport copies only. Every MTF page must still pass document-number, canonical identity, title, metadata and transcript validation before classification.",
        "- Candidate provenance retains the public canonical URL, publisher, mirror policy, inventory hash, safe relative path and file hash; the private absolute mirror path is not written to advisory outputs.",
        "",
        "## Queries per quotation",
        "",
        "Each quotation has at most six permitted deterministic templates and at most five scheduled stages, keeping the complete plan within the 150-request hard cap. A documented variant is preferred as the fifth query when available; otherwise the second distinctive fragment is used. An exact primary candidate pauses only that quotation for reversible Codex review.",
        "",
        "| Quote ID | Origins | Templates | Scheduled | Attempted | Outcome |",
        "|---|---|---:|---:|---:|---|",
    ])
    for quotation in evidence.get("quotations", []):
        lines.append(
            f"| `{quotation['quote_id']}` | {', '.join(quotation['target_origins'])} | "
            f"{len(quotation['queries_planned'])} | {quotation['scheduled_query_stage_count']} | "
            f"{len(quotation['queries_attempted'])} | "
            f"`{quotation['recommended_outcome']}` |"
        )
    lines.extend([
        "",
        "## Source classifications",
        "",
        "These counts use Codex-reviewed classifications where a manual correction exists and retain each automatic classification separately in the machine-readable candidate file.",
        "",
    ])
    if classifications:
        lines.extend(f"- `{name}`: {count}" for name, count in sorted(classifications.items()))
    else:
        lines.append("No pages were fetched or classified.")
    lines.extend([
        "",
        "## Evidence outcomes",
        "",
    ])
    for name in sorted(OUTCOMES):
        lines.append(f"- `{name}`: {int(outcomes.get(name, 0))}")
    reliable = [
        quotation for quotation in evidence.get("quotations", [])
        if quotation["recommended_outcome"] in {
            "exact_primary_wording_found", "primary_variant_found",
            "contemporary_secondary_attribution_found", "secondary_recollection_found",
            "contradictory_or_misattributed",
        }
    ]
    lines.extend([
        "",
        "## Strong new evidence, variants, recollections and contradictions",
        "",
    ])
    if not reliable:
        lines.append(
            "No fetched passage was sufficient for a proposed resolution. The manually reviewed partial and secondary leads remain advisory archive-research pointers only."
        )
    for quotation in reliable:
        lines.extend([
            f"### {quotation['quote_id']}",
            "",
            f"Outcome: `{quotation['recommended_outcome']}`. Suggested future action: {quotation['suggested_future_action']}.",
            f"Codex review: `{quotation['codex_evidence_review']['status']}`.",
            "",
        ])
        if not quotation["accepted_candidates"]:
            lines.append("- No candidate is accepted for remediation until the manual review is complete.")
        for candidate in quotation["accepted_candidates"]:
            lines.extend([
                f"- {candidate.get('title') or candidate.get('canonical_url')}",
                f"  - Locator: {candidate.get('stable_locator') or 'none established'}",
                f"  - URL: {candidate.get('canonical_url')}",
                f"  - Passage: {candidate.get('supporting_passage')}",
            ])
    reviewed_rejections = [
        (quotation, candidate)
        for quotation in quotations
        for candidate in quotation.get("candidate_sources", [])
        if candidate.get("codex_review", {}).get("decision") == "reject"
    ]
    lines.extend([
        "",
        "## Codex evidence review",
        "",
    ])
    if not reviewed_rejections:
        lines.append("No candidate required a manual rejection or classification correction.")
    for quotation, candidate in reviewed_rejections:
        review = candidate.get("codex_review", {})
        automatic_classification = candidate.get("automatic_classification", candidate.get("classification"))
        effective_classification = candidate.get("effective_classification", automatic_classification)
        lines.extend([
            f"- `{quotation['quote_id']}` — rejected as a complete resolution: {candidate.get('title') or candidate.get('canonical_url')}",
            f"  - Automatic classification: `{automatic_classification}`; Codex-reviewed classification: `{effective_classification}`.",
            f"  - URL: {candidate.get('canonical_url')}",
            f"  - Passage: {candidate.get('supporting_passage') or 'no fetched supporting passage'}",
            f"  - Review rationale: {review.get('rationale')}",
        ])
        if review.get("primary_locator_discovered"):
            lines.append(
                f"  - Primary locator to inspect: {review['primary_locator_discovered']}."
            )
        if review.get("reviewed_publication"):
            lines.append(f"  - Reviewed publication identity: {review['reviewed_publication']}.")
    unresolved_rows = [
        quotation for quotation in quotations
        if quotation.get("recommended_outcome") in {
            "promising_but_insufficient", "no_reliable_evidence_found",
            "search_incomplete_due_to_access", "search_incomplete_due_to_request_cap",
        }
    ]
    lines.extend([
        "",
        "## Quotations still unresolved or incomplete",
        "",
    ])
    if unresolved_rows:
        for quotation in unresolved_rows:
            lines.append(
                f"- `{quotation['quote_id']}` — `{quotation['recommended_outcome']}`: "
                f"{quotation['recommendation_rationale']}"
            )
    else:
        lines.append("None.")
    lines.extend([
        "",
        "## Inaccessible, circular and low-quality leads",
        "",
        f"- Rejected/discovery candidates: {evidence.get('counts', {}).get('rejected_candidate_count', 0)}.",
        f"- Inaccessible candidates: {int(classifications.get('inaccessible', 0))}.",
        f"- Circular attributions: {int(classifications.get('circular_attribution', 0))}.",
        f"- Quotation aggregations: {int(classifications.get('quotation_aggregation', 0))}.",
        "- Search result titles, rankings and snippets were retained only as discovery metadata and never promoted as evidence.",
    ])
    rejected_detail_rows = [
        (quotation["quote_id"], candidate)
        for quotation in quotations
        for candidate in quotation.get("candidate_sources", [])
        if candidate.get("effective_classification", candidate.get("classification")) in {
            "inaccessible", "circular_attribution", "quotation_aggregation",
            "discovery_only", "similar_sentiment_only", "no_support",
        }
    ]
    if rejected_detail_rows:
        lines.extend(["", "Rejected/inaccessible candidate details:", ""])
        for quote_id, candidate in rejected_detail_rows:
            title = candidate.get("title") or "unnamed source"
            candidate_url = candidate.get("canonical_url") or "no canonical URL"
            effective_classification = candidate.get(
                "effective_classification", candidate.get("classification")
            )
            review_rationale = candidate.get("codex_review", {}).get("rationale")
            lines.append(
                f"- `{quote_id}` — `{effective_classification}` — {title}; "
                f"URL: {candidate_url}; "
                f"{review_rationale or candidate.get('decision_reason') or candidate.get('fetch_status') or 'no reliable support'}"
            )
    else:
        lines.extend(["", "No inaccessible, circular or low-quality candidate page was recorded."])
    lines.extend([
        "",
        "## Recommended next remediation steps",
        "",
    ])
    if state.get("run_status") in {
        "search_backend_not_configured",
        "discovery_engine_authentication_or_configuration_failed",
    }:
        lines.append(
            "1. Repair local ADC or the fixed Discovery Engine resource/IAM configuration, then use `resume --execute-search`."
        )
    else:
        lines.append(
            "1. Preserve the completed run ledger and review only the checkpointed candidate passages."
        )
    lines.extend([
        "2. Codex must verify the actual passage, source identity, speaker, wording, actor, direction, polarity, dates, quantities and exact/variant/composite status before any proposed resolution is accepted.",
        "3. Handle any eligibility, semantic-gate or research-packet change only in a separately authorised remediation task.",
        "",
        "## Production-isolation proof",
        "",
        f"- Immutable authoritative input hashes still match the query manifest: {'yes' if input_match else 'NO'}.",
        f"- Input hash count rechecked: {len(current_hashes)}.",
        "- Canonical packets, source-role evidence, quotation text/IDs, eligibility, cycle membership, semantic gates, live state, receipts, schedules and analytics were not write targets of this programme.",
        "- Raw fetched pages are confined to the ignored private cache; no downloaded page is committed by the programme.",
        "- No X post, media upload, browser automation, CAPTCHA bypass, paywall bypass, ordinary Google HTML scraping, LLM call or generative-model call occurred.",
        "",
        final_report_status(evidence, state),
    ])
    return "\n".join(lines) + "\n"


def write_advisory_outputs(
    manifest: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    evidence_path: Path = DEFAULT_EVIDENCE,
    report_path: Path = DEFAULT_REPORT,
    ledger_path: Path = DEFAULT_RESULTS_LEDGER,
    review_path: Path = DEFAULT_CODEX_REVIEW,
    root: Path = ROOT,
) -> tuple[dict[str, Any], str]:
    """Write the machine-readable advisory file, ledger and report."""
    records = state.get("ledger_records", [])
    if not isinstance(records, list):
        raise ResearchError("run-state ledger records are invalid")
    atomic_write_jsonl(ledger_path, records)
    reviews = load_codex_reviews(review_path, manifest)
    evidence = advisory_evidence_document(manifest, state, reviews)
    atomic_write_json(evidence_path, evidence)
    report = render_report(manifest, state, evidence, root=root)
    atomic_write_bytes(report_path, report.encode("utf-8"))
    return evidence, report


def load_manifest(path: Path) -> dict[str, Any]:
    """Load and validate a query manifest."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResearchError("query manifest is not a JSON object")
    validate_query_manifest(value)
    return value


def load_run_state(path: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Load and validate resumable state."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResearchError("run state is not a JSON object")
    validate_resume_state(value, manifest)
    return value


def backend_presence(environment: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Report the fixed Discovery Engine selection without inspecting credentials."""
    del environment
    return {
        "backend": "google_discovery_engine",
        "search_scope": "configured_website_data_store",
        "full_web_search": False,
        "project_id": DISCOVERY_PROJECT_ID,
        "location": DISCOVERY_LOCATION,
        "engine_id": DISCOVERY_ENGINE_ID,
        "data_store_id": DISCOVERY_DATA_STORE_ID,
        "serving_config": DISCOVERY_SERVING_CONFIG,
        "authentication": "application_default_credentials_via_gcloud",
        "access_tokens_persisted": False,
        "generative_features_requested": False,
        "secrets_persisted": False,
    }


@contextlib.contextmanager
def exclusive_run_lock(state_path: Path) -> Iterable[None]:
    """Prevent concurrent paid runs sharing one durable state ledger."""
    lock_path = Path(state_path).resolve().with_name(Path(state_path).name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ResearchError("another research run already holds the exclusive state lock") from exc
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _is_relative_to(path: Path, parent: Path) -> bool:
    """Return whether canonical ``path`` is inside canonical ``parent``."""

    try:
        return path.is_relative_to(parent)
    except AttributeError:  # pragma: no cover - Python 3.9 compatibility
        return parent == path or parent in path.parents


def _paths_from_args(
    args: argparse.Namespace,
    *,
    project_root: Path = ROOT,
) -> dict[str, Path]:
    """Resolve output paths and refuse aliases to any project data/state path."""
    paths = {
        "manifest": Path(args.manifest).resolve(),
        "state": Path(args.state).resolve(),
        "ledger": Path(args.ledger).resolve(),
        "evidence": Path(args.evidence).resolve(),
        "report": Path(args.report).resolve(),
        "cache": Path(args.cache).resolve(),
        "review": Path(getattr(args, "review", DEFAULT_CODEX_REVIEW)).resolve(),
    }
    allowed_inside_root = {
        "manifest": DEFAULT_QUERY_MANIFEST.resolve(),
        "state": DEFAULT_RUN_STATE.resolve(),
        "ledger": DEFAULT_RESULTS_LEDGER.resolve(),
        "evidence": DEFAULT_EVIDENCE.resolve(),
        "report": DEFAULT_REPORT.resolve(),
        "cache": DEFAULT_CACHE.resolve(),
        "review": DEFAULT_CODEX_REVIEW.resolve(),
    }
    code_root_resolved = CODE_ROOT.resolve()
    project_root_resolved = Path(project_root).resolve()
    temporary_root = Path(tempfile.gettempdir()).resolve()
    for name, path in paths.items():
        inside_code_root = _is_relative_to(path, code_root_resolved)
        inside_project_root = _is_relative_to(path, project_root_resolved)
        inside_temporary_root = _is_relative_to(path, temporary_root)
        # Explicit temporary outputs remain available to isolated tests and
        # dry-run fixtures, including fixtures whose synthetic ``root`` is the
        # same temporary directory.
        if inside_temporary_root:
            continue
        if (
            project_root_resolved != code_root_resolved
            and inside_project_root
        ):
            raise ResearchError(
                f"authoritative project output path refused for {name}"
            )
        if path == allowed_inside_root[name]:
            continue
        if inside_code_root:
            raise ResearchError(
                f"protected project output path refused for {name}: only the dedicated research path is allowed"
            )
        raise ResearchError(
            f"output path refused for {name}: non-project overrides must be in the temporary directory"
        )
    isolated = [
        paths[name]
        for name in ("manifest", "state", "ledger", "evidence", "report", "cache", "review")
    ]
    if len(isolated) != len(set(isolated)):
        raise ResearchError("research output paths must be distinct")
    review = paths["review"]
    if any(
        review in path.parents or path in review.parents
        for name, path in paths.items()
        if name != "review"
    ):
        raise ResearchError("Codex review sidecar must not contain or be contained by another output")
    return paths


def _add_paths(parser: argparse.ArgumentParser) -> None:
    """Add common, explicit path options."""
    parser.add_argument("--manifest", default=str(DEFAULT_QUERY_MANIFEST))
    parser.add_argument("--state", default=str(DEFAULT_RUN_STATE))
    parser.add_argument("--ledger", default=str(DEFAULT_RESULTS_LEDGER))
    parser.add_argument("--evidence", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--cache", default=str(DEFAULT_CACHE))
    parser.add_argument("--review", default=str(DEFAULT_CODEX_REVIEW))
    parser.add_argument(
        "--local-archive-root",
        default="",
        help=(
            "absolute operator-owned mirror root; otherwise use "
            f"{LOCAL_ARCHIVE_ROOT_ENV}"
        ),
    )


def build_argument_parser() -> argparse.ArgumentParser:
    """Return the explicit plan/run/resume/report CLI."""
    parser = argparse.ArgumentParser(
        description="No-LLM deterministic historical quotation evidence discovery",
        epilog=(
            f"Set {PROJECT_ROOT_ENV} to an absolute registered worktree path "
            "to read authoritative inputs from a project root other than the "
            "tool's code directory."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan", help="derive targets and write queries; no network")
    _add_paths(plan)
    run = subparsers.add_parser("run", help="start a new paid structured-search run")
    _add_paths(run)
    run.add_argument("--execute-search", action="store_true")
    resume = subparsers.add_parser("resume", help="resume a manifest-bound run")
    _add_paths(resume)
    resume.add_argument("--execute-search", action="store_true")
    report = subparsers.add_parser("report", help="regenerate advisory outputs; no network")
    _add_paths(report)
    return parser


def _manifest_for_command(paths: Mapping[str, Path], *, root: Path = ROOT) -> dict[str, Any]:
    """Load an existing manifest or create it deterministically."""
    if paths["manifest"].is_file():
        manifest = load_manifest(paths["manifest"])
    else:
        manifest = build_query_manifest(root)
        atomic_write_json(paths["manifest"], manifest)
    matches, _current = current_input_hashes_match(manifest, root)
    if not matches:
        raise ResearchError("authoritative inputs changed after query-manifest creation")
    expected = build_query_manifest(root)
    if manifest.get("manifest_hash") != expected.get("manifest_hash"):
        raise ResearchError("query manifest differs from fresh deterministic authoritative derivation")
    return manifest


def _backend_or_unconfigured(
    root: Path,
) -> tuple[SearchBackend | None, SearchBudget | None, dict[str, Any]]:
    """Build the fixed Discovery backend or return a presence-only failure record."""
    try:
        backend, budget, config = configured_search_backend(root)
    except SearchBackendNotConfigured as exc:
        return None, None, {**backend_presence(), "configuration_error": str(exc)}
    return backend, budget, config


def _configured_local_archive(
    args: argparse.Namespace,
) -> LocalArchiveMirror | None:
    """Resolve an optional private mirror without recording its absolute path."""
    raw = str(
        getattr(args, "local_archive_root", "")
        or os.environ.get(LOCAL_ARCHIVE_ROOT_ENV, "")
    ).strip()
    if not raw:
        return None
    try:
        return LocalArchiveMirror(Path(raw), maximum_bytes=MAXIMUM_RESPONSE_BYTES)
    except LocalArchiveError as exc:
        raise ResearchError(f"local archive configuration failed: {exc}") from exc


def _command_plan_unlocked(args: argparse.Namespace, *, root: Path = ROOT) -> int:
    """Write the deterministic query manifest without network access."""
    paths = _paths_from_args(args, project_root=root)
    manifest = build_query_manifest(root)
    if paths["state"].is_file():
        existing = json.loads(paths["state"].read_text(encoding="utf-8"))
        if not isinstance(existing, dict) or existing.get("manifest_hash") != manifest["manifest_hash"]:
            raise ResearchError("existing run state is bound to a different manifest; plan will not overwrite it")
    atomic_write_json(paths["manifest"], manifest)
    counts = manifest["target_derivation"]
    print(
        f"planned targets={counts['deduplicated_target_count']} "
        f"blocked={counts['blocked_count']} unresolved={counts['unresolved_count']} "
        f"manifest_hash={manifest['manifest_hash']}"
    )
    return 0


def command_plan(args: argparse.Namespace, *, root: Path = ROOT) -> int:
    """Create the plan while excluding concurrent run/report output mutation."""
    _paths_from_args(args, project_root=root)
    with exclusive_run_lock(DEFAULT_CACHE / "paid_research_run"):
        return _command_plan_unlocked(args, root=root)


def _write_unconfigured(
    manifest: Mapping[str, Any],
    paths: Mapping[str, Path],
    config: Mapping[str, Any],
    *,
    root: Path,
    existing_state: dict[str, Any] | None = None,
) -> int:
    """Persist an honest zero-request stop and all required advisory outputs."""
    state = existing_state or initialise_run_state(
        manifest,
        status="discovery_engine_authentication_or_configuration_failed",
        backend_config=config,
    )
    prior_status = state.get("run_status")
    if existing_state is not None:
        state["prior_run_status_before_unconfigured_resume"] = prior_status
        state["last_unconfigured_backend_presence"] = dict(config)
    else:
        state["backend_config"] = dict(config)
    state["run_status"] = "discovery_engine_authentication_or_configuration_failed"
    state["failure_reason"] = str(config.get("configuration_error") or "search backend unavailable")
    state["updated_at"] = utc_now()
    atomic_write_json(paths["state"], state, mode=0o600)
    write_advisory_outputs(
        manifest, state, evidence_path=paths["evidence"], report_path=paths["report"],
        ledger_path=paths["ledger"], review_path=paths["review"], root=root,
    )
    print("DISCOVERY ENGINE AUTHENTICATION OR CONFIGURATION FAILED")
    return 3


def _command_run_or_resume_unlocked(
    args: argparse.Namespace,
    *,
    resume: bool,
    root: Path = ROOT,
) -> int:
    """Run or resume only behind the explicit paid-search switch."""
    if not args.execute_search:
        raise ResearchError("live search requires the explicit --execute-search option")
    paths = _paths_from_args(args, project_root=root)
    manifest = _manifest_for_command(paths, root=root)
    if resume:
        if not paths["state"].is_file():
            raise ResearchError("resume requested but no run state exists")
        state = json.loads(paths["state"].read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ResearchError("run state is not an object")
        try:
            validate_resume_state(state, manifest)
        except AmbiguousSearchRequest:
            state["run_status"] = "unsafe_ambiguous_search_request"
            atomic_write_json(paths["state"], state, mode=0o600)
            write_advisory_outputs(
                manifest, state, evidence_path=paths["evidence"], report_path=paths["report"],
                ledger_path=paths["ledger"], review_path=paths["review"], root=root,
            )
            raise
    else:
        if paths["state"].exists():
            raise ResearchError("run state already exists; use resume so completed work is not overwritten")
        state = {}
    local_archive = _configured_local_archive(args)
    backend, initial_budget, config = _backend_or_unconfigured(root)
    config = {
        **config,
        "local_archive": (
            local_archive.configuration()
            if local_archive is not None
            else {
                "enabled": False,
                "policy_version": LOCAL_ARCHIVE_POLICY_VERSION,
            }
        ),
    }
    if backend is None or initial_budget is None:
        return _write_unconfigured(
            manifest, paths, config, root=root,
            existing_state=state if state else None,
        )
    if not state:
        state = initialise_run_state(
            manifest, status="ready", backend_config=config, budget=initial_budget,
        )
        budget = initial_budget
        page_budget = PageFetchBudget()
    else:
        previous_config = state.get("backend_config") or {}
        previous_backend = previous_config.get("backend")
        previous_version = previous_config.get("backend_version")
        previous_engine = previous_config.get("engine_identity_sha256")
        prior_requests = int(
            state.get("search_accounting", {}).get("requests_started") or 0
        )
        backend_was_bound = bool(previous_engine or prior_requests)
        if backend_was_bound and previous_backend and previous_backend != backend.name:
            raise ResearchError("configured search backend differs from resumed run")
        if backend_was_bound and previous_version and previous_version != backend.version:
            raise ResearchError("configured search backend version differs from resumed run")
        if backend_was_bound and previous_engine and previous_engine != backend.engine_identity_hash:
            raise ResearchError("configured search engine differs from resumed run")
        previous_local = (
            previous_config.get("local_archive")
            if isinstance(previous_config.get("local_archive"), Mapping)
            else {"enabled": False}
        )
        current_local = config["local_archive"]
        if (
            bool(previous_local.get("enabled")) != bool(current_local.get("enabled"))
            or (
                previous_local.get("enabled")
                and (
                    previous_local.get("policy_version")
                    != current_local.get("policy_version")
                    or previous_local.get("root_identity_sha256")
                    != current_local.get("root_identity_sha256")
                )
            )
        ):
            raise ResearchError(
                "local archive configuration differs from the resumed run"
            )
        state["backend_config"] = config
        budget = SearchBudget.from_dict(state["search_accounting"])
        if budget.pricing_known != initial_budget.pricing_known:
            raise ResearchError("configured search-pricing mode differs from resumed run")
        if budget.price_per_1000 != initial_budget.price_per_1000:
            raise ResearchError("configured search price differs from resumed run")
        if (
            budget.free_quota_remaining_at_start
            != initial_budget.free_quota_remaining_at_start
        ):
            raise ResearchError("configured free-quota assumption differs from resumed run")
        page_budget = PageFetchBudget.from_dict(state.get("page_fetch_accounting", {}))
    ensure_authenticated = getattr(backend, "ensure_authenticated", None)
    if callable(ensure_authenticated):
        try:
            ensure_authenticated()
        except SearchBackendNotConfigured as exc:
            return _write_unconfigured(
                manifest,
                paths,
                {**config, "configuration_error": str(exc)},
                root=root,
                existing_state=state,
            )
    cache = ResearchCache(paths["cache"])
    local_archive_index = (
        LocalMTFDocumentIndex(local_archive)
        if local_archive is not None
        else None
    )
    fetcher = SafeFetcher(
        cache=cache, budget=page_budget, local_archive=local_archive,
    )
    codex_reviews = load_codex_reviews(paths["review"], manifest)
    runner = SearchResearchRunner(
        manifest=manifest,
        state=state,
        backend=backend,
        budget=budget,
        fetcher=fetcher,
        cache=cache,
        state_path=paths["state"],
        ledger_path=paths["ledger"],
        codex_reviews=codex_reviews,
        local_archive_index=local_archive_index,
    )
    final_state = runner.run()
    evidence, report = write_advisory_outputs(
        manifest, final_state, evidence_path=paths["evidence"], report_path=paths["report"],
        ledger_path=paths["ledger"], review_path=paths["review"], root=root,
    )
    print(final_report_status(evidence, final_state))
    return 0 if final_state.get("run_status") == "complete" else 4


def command_run_or_resume(
    args: argparse.Namespace,
    *,
    resume: bool,
    root: Path = ROOT,
) -> int:
    """Hold an exclusive durable-state lock for the entire paid-run lifecycle."""
    if not args.execute_search:
        raise ResearchError("live search requires the explicit --execute-search option")
    paths = _paths_from_args(args, project_root=root)
    with exclusive_run_lock(DEFAULT_CACHE / "paid_research_run"):
        return _command_run_or_resume_unlocked(args, resume=resume, root=root)


def _command_report_unlocked(args: argparse.Namespace, *, root: Path = ROOT) -> int:
    """Regenerate advisory outputs from durable local state without network."""
    paths = _paths_from_args(args, project_root=root)
    manifest = _manifest_for_command(paths, root=root)
    if not paths["state"].is_file():
        raise ResearchError("report requested but no run state exists")
    state = json.loads(paths["state"].read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise ResearchError("run state is not an object")
    try:
        validate_resume_state(state, manifest)
    except AmbiguousSearchRequest:
        if state.get("run_status") != "unsafe_ambiguous_search_request":
            raise
    evidence, _report = write_advisory_outputs(
        manifest, state, evidence_path=paths["evidence"], report_path=paths["report"],
        ledger_path=paths["ledger"], review_path=paths["review"], root=root,
    )
    print(final_report_status(evidence, state))
    return 0


def command_report(args: argparse.Namespace, *, root: Path = ROOT) -> int:
    """Regenerate outputs while excluding a concurrent paid run."""
    _paths_from_args(args, project_root=root)
    with exclusive_run_lock(DEFAULT_CACHE / "paid_research_run"):
        return _command_report_unlocked(args, root=root)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the no-LLM research command line."""
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    try:
        project_root = resolve_project_root(CODE_ROOT)
        if args.command == "plan":
            return command_plan(args, root=project_root)
        if args.command == "run":
            return command_run_or_resume(args, resume=False, root=project_root)
        if args.command == "resume":
            return command_run_or_resume(args, resume=True, root=project_root)
        if args.command == "report":
            return command_report(args, root=project_root)
        raise AssertionError("argparse accepted an unknown command")
    except SearchBackendNotConfigured:
        print("DISCOVERY ENGINE AUTHENTICATION OR CONFIGURATION FAILED")
        return 3
    except SearchBudgetExceeded as exc:
        print(f"RESEARCH INCOMPLETE — REQUEST CAP REACHED: {exc}")
        return 4
    except (
        ProjectRootError,
        ResearchError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"RESEARCH TOOL INCOMPLETE OR UNSAFE: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
