"""Offline source-role auditing and runtime validation for context evidence.

The canonical quotation packets remain immutable.  This module builds and
validates a sidecar which says what each saved grounding record can actually
support.  Runtime formatters may use only the roles recorded in that sidecar.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus, urlsplit, urlunsplit

from historical_context_source_recovery import (
    RECOVERY_FILENAME,
    validate_recovery,
)
from historical_context_source_resolution import (
    RESOLUTION_FILENAME,
    validate_resolution,
)
from historical_context_source_research_manifest import (
    RESEARCH_FILENAME,
    validate_research_manifest,
)
from historical_context_source_openai_manifest import (
    OPENAI_RESEARCH_FILENAME,
    validate_openai_research_manifest,
)
from historical_context_source_independent_review import (
    REVIEW_FILENAME,
    REVIEW_POLICY_VERSION,
    validate_independent_review,
)
from historical_context_source_curated_evidence import (
    CURATED_EVIDENCE_FILENAME,
    validate_curated_evidence,
)


AUDIT_SCHEMA_VERSION = 5
POLICY_VERSION = (
    "historical-context-source-roles-v9-archive-provenance"
)
AUDIT_FILENAME = "historical_context_source_role_audit.json"

SOURCE_ROLES = frozenset({
    "wording_verification",
    "attribution_support",
    "source_event_support",
    "historical_context_support",
    "secondary_recollection",
    "discovery_only",
    "rejected_irrelevant",
})
QUALITY_CLASSES = frozenset({
    "strong_primary_evidence",
    "reliable_secondary_evidence",
    "secondary_recollection",
    "discovery_lead_only",
    "irrelevant_or_corrupt",
    "circular_attribution",
    "broken_or_non_verifying_url",
    "insufficiently_located_evidence",
})
ACTIONS = frozenset({
    "keep",
    "relabel",
    "downgrade",
    "remove_from_rendered_output",
    "reject_as_irrelevant",
    "requires_further_research",
})
CLAIM_FIELDS = (
    "wording",
    "attribution",
    "source_event",
    "date",
    "historical_context",
)
UNKNOWN_VALUES = frozenset({
    "", "n/a", "n.a.", "none", "not available", "unknown", "unavailable",
})

_PRIMARY_MARKERS = (
    "margaretthatcher.org",
    "margaret thatcher foundation",
    "hansard",
    "parliament.uk",
    "archives.gov",
    "reaganlibrary.gov",
    "gov.uk",
)
_AGGREGATOR_MARKERS = (
    "allgreatquotes", "azquotes", "brainyquote", "citaty", "goodreads",
    "keepinspiring", "libquotes", "magicalquote", "notable-quotes",
    "picturequotes", "quotefancy", "quotepark", "quotes.net", "quotetab",
    "quotery", "statush", "symphonyoflove", "wikiquote", "wisesayings",
    "wonderfulquote",
)
_UNRELIABLE_DISCOVERY_MARKERS = (
    "brainly", "blogspot", "dokumen.pub", "fandom", "medium.com",
    "pinterest", "quora.com", "reddit.com", "scribd.com", "sweetstudy",
    "thestudentroom", "twstalker", "weebly", "wordpress.com", "youtube.com",
    "wikipedia.org", "z-library",
)
_RELIABLE_SECONDARY_MARKERS = (
    "bbc.", "cam.ac.uk", "cambridge.org", "guardian.com", "independent.co.uk",
    "latimes.com", "newsweek.com", "nytimes.com", "oup.com", "ox.ac.uk",
    "researchgate.net", "spectator.", "theguardian.com", "time.com",
    "washingtonpost.com",
)
_THATCHER_BOOKS = (
    "the downing street years",
    "the path to power",
    "statecraft",
    "margaret thatcher: the autobiography",
)
_KNOWN_RECOLLECTION_TITLES = (
    "a balance of power",
    "one of us",
    "journals of woodrow wyatt",
)
_QUOTATION_COMPILATION_MARKERS = (
    "book of quotations",
    "dictionary of quotations",
    "dictionary of quotes",
)
_UNCERTAIN_WORDING = frozenset({
    "paraphrase", "composite", "misattributed", "unverified",
})

_MANDATORY_REJECTIONS = {
    (
        "eb1d2ebaac7e321e67174d2db5761d2bd008341ebb04b1abac4d4a927cd7a7d4",
        "nps.gov",
    ): "The National Park Service segment concerns Lyndon B. Johnson and the Potomac; it supports no Thatcher claim.",
}


def canonical_json(value: Any) -> bytes:
    """Return canonical UTF-8 JSON bytes for hashing."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha256_bytes(value: bytes) -> str:
    """Return a SHA-256 hexadecimal digest."""
    return hashlib.sha256(value).hexdigest()


def file_sha256(path: Path) -> str:
    """Hash a file without changing it."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_fingerprint(source: dict[str, Any]) -> str:
    """Identify one immutable packet source record."""
    return sha256_bytes(canonical_json(source))


def quote_set_hash(quote_ids: set[str] | list[str]) -> str:
    """Hash a sorted quotation-ID set."""
    return sha256_bytes(("\n".join(sorted(quote_ids)) + "\n").encode())


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _known(value: Any) -> bool:
    return _clean(value).casefold() not in UNKNOWN_VALUES


def _mentions_thatcher(value: Any) -> bool:
    return bool(re.search(r"\b(?:margaret|mrs|lady)?\s*thatcher\b", _clean(value), re.I))


def _source_years(value: Any) -> set[str]:
    return set(re.findall(r"\b(?:18|19|20)\d{2}\b", str(value or "")))


def _primary_marker_is_contemporaneous(
    packet: dict[str, Any], url: Any, title: Any
) -> bool:
    host = urlsplit(_clean(url)).netloc.casefold().removeprefix("www.")
    if host == "parliament.uk" or host.endswith(".parliament.uk"):
        packet_years = _source_years(packet.get("date"))
        page_years = _source_years(f"{_clean(url)} {_clean(title)}")
        return bool(packet_years and packet_years & page_years)
    return True


def _source_text(source: dict[str, Any]) -> str:
    url = _clean(source.get("url"))
    host = urlsplit(url).netloc.casefold()
    return f"{_clean(source.get('title')).casefold()} {host} {_clean(source.get('source_type')).casefold()}"


def _is_google_search(source: dict[str, Any]) -> bool:
    for value in (source.get("title"), source.get("url")):
        parsed = urlsplit(_clean(value))
        if parsed.netloc.casefold().endswith("google.com") and parsed.path == "/search":
            return True
    return False


def _is_redirect(url: Any) -> bool:
    parsed = urlsplit(_clean(url))
    return (
        parsed.netloc.casefold() == "vertexaisearch.cloud.google.com"
        and parsed.path.startswith("/grounding-api-redirect/")
    )


def _is_http_url(url: Any) -> bool:
    parsed = urlsplit(_clean(url))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _word_tokens(value: Any) -> list[str]:
    text = _clean(value).casefold().replace("&", " and ")
    return re.findall(r"[a-z0-9]+", text)


def _wording_coverage(packet: dict[str, Any], cited_segment: Any) -> str:
    """Classify a provider-cited span without mistaking a fragment for exact proof."""
    cleaned_segment = _clean(cited_segment).replace("’", "'").replace("“", '"').replace("”", '"')
    segment = _word_tokens(cited_segment)
    candidates = [
        _word_tokens(packet.get("quote_text")),
        _word_tokens(packet.get("verified_text")),
    ]
    best_partial = False
    for field, wording in zip(("quote_text", "verified_text"), candidates):
        if not wording or not segment:
            continue
        cleaned_wording = _clean(packet.get(field)).replace("’", "'").replace("“", '"').replace("”", '"')
        if cleaned_wording and cleaned_wording.casefold() in cleaned_segment.casefold():
            return "full"
        if wording == segment:
            return "normalised"
        if len(segment) >= len(wording):
            for index in range(len(segment) - len(wording) + 1):
                if segment[index:index + len(wording)] == wording:
                    return "normalised"
        match = SequenceMatcher(a=wording, b=segment, autojunk=False).find_longest_match()
        if match.size >= 12 and match.size / min(len(wording), len(segment)) >= 0.75:
            best_partial = True
    return "partial" if best_partial else "none"


def _direct_url_is_precise(url: Any) -> bool:
    parsed = urlsplit(_clean(url))
    host = parsed.netloc.casefold().removeprefix("www.")
    path = parsed.path.rstrip("/")
    if host == "margaretthatcher.org":
        return bool(re.fullmatch(r"/document/\d{5,}", path))
    if host.endswith("parliament.uk") or host.endswith("gov.uk"):
        return len([part for part in path.split("/") if part]) >= 2
    return len([part for part in path.split("/") if part]) >= 2


def _has_marker(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _source_matches_locator(
    source: dict[str, Any],
    locator: Any,
    locator_kind: str | None,
) -> bool:
    """Return whether an opaque source label matches the packet locator."""
    source_text = _source_text(source)
    locator_text = _clean(locator).casefold()
    if locator_kind == "primary_archive":
        return any(marker in source_text for marker in ("margaretthatcher.org", "margaret thatcher foundation"))
    if locator_kind == "primary_official_record":
        return any(marker in source_text for marker in ("hansard", "parliament.uk"))
    if locator_kind == "thatcher_authored_book":
        return any(book in source_text for book in _THATCHER_BOOKS)
    if locator_kind == "located_secondary":
        publications = (
            "the times", "guardian", "observer", "telegraph", "independent",
            "newsweek", "time magazine",
        )
        return any(name in locator_text and name in source_text for name in publications)
    if locator_kind == "located_publication":
        locator_terms = {
            token for token in re.findall(r"[a-z]{4,}", locator_text)
            if token not in {"page", "published", "edition", "volume"}
        }
        return len(locator_terms & set(re.findall(r"[a-z]{4,}", source_text))) >= 2
    return False


def _is_recollection(packet: dict[str, Any], source: dict[str, Any] | None = None) -> bool:
    """Identify explicitly reported recollections without scanning model prose."""
    text = " ".join(
        _clean(packet.get(field)) for field in ("stable_locator", "source_event")
    )
    if source:
        text += " " + " ".join(
            _clean(source.get(field)) for field in ("title", "source_type")
        )
    text = text.casefold()
    if any(book in text for book in _THATCHER_BOOKS):
        return False
    return bool(
        any(title in text for title in _KNOWN_RECOLLECTION_TITLES)
        or re.search(
            r"\b(?:memoir|recollect(?:ion|ed)?|recalled|reported by|"
            r"diary|journals?|private conversation)\b",
            text,
        )
    )


def precise_locator_kind(locator: Any) -> str | None:
    """Classify a locally recorded locator only when it is independently usable."""
    value = _clean(locator)
    text = value.casefold()
    if not _known(value):
        return None
    if any(marker in text for marker in _QUOTATION_COMPILATION_MARKERS):
        return "quotation_compilation"
    if re.search(r"margaretthatcher\.org/(?:document/)?\d{5,}", text):
        return "primary_archive"
    if "margaret thatcher foundation" in text or "thatcher mss" in text:
        if re.search(r"\b(?:document|docid|thcr|ccopr)\s*[:#-]?\s*[a-z0-9/.-]*\d", text):
            return "primary_archive"
        if re.search(r"\b(?:speech|interview|remarks|statement|transcript|document)\b.*\b(?:19|20)\d{2}\b", text):
            return "primary_archive"
        if re.search(r"\b(?:19|20)\d{2}\b.*\b(?:speech|interview|remarks|statement|transcript|document)\b", text):
            return "primary_archive"
        if re.search(r"\b(?:18|19|20)\d{2}\b", text):
            return "primary_archive"
        return None
    if "hansard" in text and re.search(r"\b(?:18|19|20)\d{2}\b|\bcolumn\s+\d+", text):
        return "primary_official_record"
    if any(book in text for book in _THATCHER_BOOKS):
        if re.search(r"\b(?:p(?:age)?\.?|chapter|ch\.)\s*\d+", text):
            return "thatcher_authored_book"
        return None
    if re.search(r"\b(?:page|p\.)\s*\d+", text) and re.search(r"\b(?:isbn|volume|vol\.|edition|ed\.)\b", text):
        return "located_publication"
    if re.search(r"\b(?:18|19|20)\d{2}\b", text) and re.search(r"\b(?:page|p\.)\s*\d+", text):
        return "located_publication"
    if re.search(r"\b(?:the times|guardian|observer|telegraph|independent|newsweek|time magazine)\b", text) and re.search(r"\b(?:18|19|20)\d{2}\b", text):
        return "located_secondary"
    return None


def _support_claims(packet: dict[str, Any], source: dict[str, Any]) -> set[str]:
    supports = " ".join(_clean(value) for value in source.get("supports", []))
    folded = supports.casefold()
    claims: set[str] = set()
    quote = _clean(packet.get("quote_text"))
    verified = _clean(packet.get("verified_text"))
    if (
        '"verified_text"' in folded
        or "exact wording" in folded
        or "verified text" in folded
        or (len(quote) >= 24 and quote[:80].casefold() in folded)
        or (len(verified) >= 24 and verified[:80].casefold() in folded)
    ):
        claims.add("wording")
    if '"speaker"' in folded or "attribut" in folded or "margaret thatcher" in folded:
        claims.add("attribution")
    date = _clean(packet.get("date"))
    if '"date"' in folded or (_known(date) and date.casefold() in folded):
        claims.add("date")
    event = _clean(packet.get("source_event"))
    if '"source_event"' in folded or "source event" in folded or (
        _known(event) and len(event) >= 12 and event[:80].casefold() in folded
    ):
        claims.add("source_event")
    context = _clean(packet.get("historical_context"))
    if '"historical_context"' in folded or (
        _known(context) and len(context) >= 30 and context[:100].casefold() in folded
    ):
        claims.add("historical_context")
    return claims


def _roles_for_claims(claims: set[str]) -> list[str]:
    roles: list[str] = []
    if "wording" in claims:
        roles.append("wording_verification")
    if "attribution" in claims:
        roles.append("attribution_support")
    if claims & {"source_event", "date"}:
        roles.append("source_event_support")
    if "historical_context" in claims:
        roles.append("historical_context_support")
    return roles


def _locator_claims(packet: dict[str, Any], locator_kind: str | None) -> set[str]:
    if not locator_kind or locator_kind == "quotation_compilation":
        return set()
    claims = {"attribution"}
    if packet.get("verification_status") not in _UNCERTAIN_WORDING:
        claims.add("wording")
    locator = _clean(packet.get("stable_locator")).casefold()
    if _known(packet.get("source_event")) and (
        locator_kind in {"primary_official_record", "thatcher_authored_book"}
        or re.search(r"\b(?:speech|interview|remarks|statement|transcript|debate)\b", locator)
    ):
        claims.add("source_event")
    date = _clean(packet.get("date"))
    year = re.search(r"\b(?:18|19|20)\d{2}\b", date)
    if _known(date) and year and year.group(0) in locator:
        claims.add("date")
    return claims


def _source_passages(source: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "kind": "provider_grounded_response_segment",
            "text": _clean(value),
            "sha256": sha256_bytes(_clean(value).encode()),
        }
        for value in source.get("supports", [])
        if _clean(value)
    ]


def audit_source(
    quote_id: str,
    packet: dict[str, Any],
    index: int,
    source: dict[str, Any],
    *,
    locator_kind: str | None,
) -> dict[str, Any]:
    """Assign one conservative role and quality classification to a source."""
    title = _clean(source.get("title"))
    url = _clean(source.get("url"))
    text = _source_text(source)
    claims = _support_claims(packet, source)
    roles: list[str]
    public_title: str | None = None
    public_url: str | None = None

    mandatory_reason = _MANDATORY_REJECTIONS.get((quote_id, title.casefold()))
    indicates_recollection = _is_recollection(packet, source)
    if mandatory_reason:
        quality = "irrelevant_or_corrupt"
        roles = ["rejected_irrelevant"]
        action = "reject_as_irrelevant"
        rationale = mandatory_reason
        claims = set()
    elif _is_google_search(source):
        quality = "irrelevant_or_corrupt"
        roles = ["rejected_irrelevant"]
        action = "reject_as_irrelevant"
        rationale = "A Google search/time query is search machinery, not evidence for any packet claim."
        claims = set()
    elif not _is_http_url(url):
        quality = "broken_or_non_verifying_url"
        roles = ["rejected_irrelevant"]
        action = "remove_from_rendered_output"
        rationale = "The saved URL is not a valid HTTP(S) evidentiary locator."
        claims = set()
    elif _has_marker(text, _AGGREGATOR_MARKERS):
        quality = "circular_attribution"
        roles = ["discovery_only"]
        action = "remove_from_rendered_output"
        rationale = "A quotation aggregator can document circulation but cannot verify wording or attribution."
        claims = set()
    elif _has_marker(text, _UNRELIABLE_DISCOVERY_MARKERS):
        quality = "discovery_lead_only"
        roles = ["discovery_only"]
        action = "remove_from_rendered_output"
        rationale = "The source is useful only as a research lead and is not authoritative evidence."
        claims = set()
    else:
        primary_marker = _has_marker(text, _PRIMARY_MARKERS)
        primary = primary_marker and _primary_marker_is_contemporaneous(
            packet, url, title
        )
        secondary = (
            _has_marker(text, _RELIABLE_SECONDARY_MARKERS)
            or (primary_marker and not primary)
        )
        recollection = indicates_recollection
        locator_claims = _locator_claims(packet, locator_kind)
        source_matches_locator = _source_matches_locator(
            source,
            packet.get("stable_locator"),
            locator_kind,
        )
        can_locate = (
            locator_kind not in {None, "quotation_compilation"}
            and source_matches_locator
        )
        if _is_redirect(url):
            claims &= locator_claims
        if primary and can_locate and claims:
            quality = "strong_primary_evidence"
            roles = _roles_for_claims(claims)
            action = "relabel"
            rationale = "Provider-linked claims are backed by a specific canonical primary locator."
            public_title = _clean(packet.get("stable_locator"))
            public_url = "" if _is_redirect(url) else url
        elif recollection and can_locate and claims:
            quality = "secondary_recollection"
            roles = list(dict.fromkeys(_roles_for_claims(claims) + ["secondary_recollection"]))
            action = "relabel"
            rationale = "The located record is a later recollection, not a primary Thatcher transcript."
            public_title = _clean(packet.get("stable_locator"))
            public_url = "" if _is_redirect(url) else url
        elif secondary and can_locate and claims:
            quality = "reliable_secondary_evidence"
            roles = _roles_for_claims(claims)
            action = "relabel"
            rationale = "The source is reliable secondary evidence with a specific local locator."
            public_title = _clean(packet.get("stable_locator"))
            public_url = "" if _is_redirect(url) else url
        elif _is_redirect(url):
            quality = "insufficiently_located_evidence"
            roles = ["discovery_only"]
            action = "requires_further_research"
            rationale = (
                "The opaque provider record is not independently tied to a matching, claim-bearing public locator."
            )
            claims = set()
        else:
            quality = "insufficiently_located_evidence"
            roles = ["discovery_only"]
            action = "requires_further_research"
            rationale = "The saved record lacks a precise passage or locator tying it to a displayed claim."
            claims = set()

    claims_supported = [field for field in CLAIM_FIELDS if field in claims]
    return {
        "source_id": sha256_bytes(f"{quote_id}:{index}:{source_fingerprint(source)}".encode()),
        "source_index": index,
        "source_fingerprint": source_fingerprint(source),
        "source_title": title,
        "source_url": url,
        "source_type": _clean(source.get("source_type")),
        "assigned_roles": roles,
        "source_quality_class": quality,
        "claims_supported": claims_supported,
        "claims_not_supported": [field for field in CLAIM_FIELDS if field not in claims],
        "action": action,
        "confidence_before": _clean(packet.get("research_confidence")),
        "supporting_passages": _source_passages(source),
        "indicates_secondary_recollection": indicates_recollection,
        "public_title": public_title,
        "public_url": public_url,
        "rationale": rationale,
    }


def audit_recovered_citation(
    quote_id: str,
    packet: dict[str, Any],
    citation: dict[str, Any],
) -> dict[str, Any]:
    """Classify a direct URL retained in provider citation metadata."""
    title = _clean(citation.get("title"))
    url = _clean(citation.get("url"))
    segment = _clean(citation.get("cited_response_segment"))
    source = {"title": title, "url": url, "source_type": "provider_citation_metadata"}
    text = _source_text(source)
    coverage = _wording_coverage(packet, segment)
    claims: set[str] = set()
    public_title: str | None = None
    public_url: str | None = None

    host = urlsplit(url).netloc.casefold().removeprefix("www.")
    if quote_id == "eb1d2ebaac7e321e67174d2db5761d2bd008341ebb04b1abac4d4a927cd7a7d4" and host.endswith("nps.gov"):
        quality = "irrelevant_or_corrupt"
        roles = ["rejected_irrelevant"]
        action = "reject_as_irrelevant"
        rationale = _MANDATORY_REJECTIONS[(quote_id, "nps.gov")]
        coverage = "none"
    elif _is_google_search(source):
        quality = "irrelevant_or_corrupt"
        roles = ["rejected_irrelevant"]
        action = "reject_as_irrelevant"
        rationale = "A Google search/time query is search machinery, not evidence for any packet claim."
        coverage = "none"
    elif not _is_http_url(url):
        quality = "broken_or_non_verifying_url"
        roles = ["rejected_irrelevant"]
        action = "remove_from_rendered_output"
        rationale = "The recovered citation URL is not a valid HTTP(S) locator."
        coverage = "none"
    elif _has_marker(text, _AGGREGATOR_MARKERS):
        quality = "circular_attribution"
        roles = ["discovery_only"]
        action = "remove_from_rendered_output"
        rationale = "A citation to a quotation aggregator remains circular attribution evidence."
        coverage = "none"
    elif _has_marker(text, _UNRELIABLE_DISCOVERY_MARKERS):
        quality = "discovery_lead_only"
        roles = ["discovery_only"]
        action = "remove_from_rendered_output"
        rationale = "The recovered URL is not authoritative enough for public verification."
        coverage = "none"
    elif not _direct_url_is_precise(url) or coverage == "none":
        quality = "insufficiently_located_evidence"
        roles = ["discovery_only"]
        action = "requires_further_research"
        rationale = (
            "The direct citation lacks either a precise page locator or a material wording match."
        )
    else:
        primary_marker = _has_marker(text, _PRIMARY_MARKERS)
        primary = primary_marker and _primary_marker_is_contemporaneous(
            packet, url, title
        )
        secondary = (
            _has_marker(text, _RELIABLE_SECONDARY_MARKERS)
            or (primary_marker and not primary)
        )
        claims.add("wording")
        if primary or "margaret thatcher" in segment.casefold():
            claims.add("attribution")
        if primary:
            quality = "strong_primary_evidence"
            action = "keep"
        elif secondary:
            quality = "reliable_secondary_evidence"
            action = "keep"
        else:
            quality = "insufficiently_located_evidence"
            action = "requires_further_research"
            claims.clear()
        if claims:
            roles = _roles_for_claims(claims)
            public_title = title
            public_url = url
            rationale = (
                "Saved provider citation metadata supplies a direct, precise URL and a "
                f"{coverage} wording match. The retained span is not represented as a verbatim page excerpt."
            )
        else:
            roles = ["discovery_only"]
            rationale = "The direct URL is a useful lead but is not independently authoritative."

    return {
        "source_id": citation["citation_id"],
        "source_index": None,
        "source_fingerprint": sha256_bytes(canonical_json(citation)),
        "source_title": title,
        "source_url": url,
        "source_type": "recovered_provider_citation",
        "assigned_roles": roles,
        "source_quality_class": quality,
        "claims_supported": [field for field in CLAIM_FIELDS if field in claims],
        "claims_not_supported": [field for field in CLAIM_FIELDS if field not in claims],
        "claim_coverage": {"wording": coverage} if coverage != "none" else {},
        "action": action,
        "confidence_before": _clean(packet.get("research_confidence")),
        "supporting_passages": [{
            "kind": "provider_cited_response_segment_not_source_excerpt",
            "text": segment,
            "sha256": citation["cited_response_segment_sha256"],
        }],
        "public_title": public_title,
        "public_url": public_url,
        "rationale": rationale,
        "recovered_citation_record": True,
        "provenance": citation.get("provenance", {}),
    }


def audit_resolved_redirect(
    quote_id: str,
    packet: dict[str, Any],
    index: int,
    source: dict[str, Any],
    resolution: dict[str, Any],
) -> dict[str, Any]:
    """Audit a saved opaque source after its original public URL was resolved."""
    final_url = _clean(resolution.get("final_url"))
    title = _clean(resolution.get("page_title")) or _clean(source.get("title")) or final_url
    resolved_source = {
        "title": title,
        "url": final_url,
        "source_type": _clean(source.get("source_type")),
    }
    text = _source_text(resolved_source)
    match = resolution.get("wording_matches", {}).get(quote_id, {})
    match_kind = match.get("kind")
    coverage = "full" if match_kind == "full_text" else "normalised" if match_kind == "full_normalised_tokens" else (
        "partial" if match_kind == "partial_normalised_tokens" else "none"
    )
    passage = match.get("matched_passage")
    claims: set[str] = set()
    public_title: str | None = None
    public_url: str | None = None
    host = urlsplit(final_url).netloc.casefold().removeprefix("www.")

    if quote_id == "eb1d2ebaac7e321e67174d2db5761d2bd008341ebb04b1abac4d4a927cd7a7d4" and host.endswith("nps.gov"):
        quality = "irrelevant_or_corrupt"
        roles = ["rejected_irrelevant"]
        action = "reject_as_irrelevant"
        rationale = _MANDATORY_REJECTIONS[(quote_id, "nps.gov")]
        coverage = "none"
    elif resolution.get("status") != "resolved" or not _is_http_url(final_url):
        quality = "broken_or_non_verifying_url"
        roles = ["rejected_irrelevant"]
        action = "remove_from_rendered_output"
        rationale = "The saved grounding redirect did not resolve to a readable public page."
        coverage = "none"
    elif _is_google_search(resolved_source):
        quality = "irrelevant_or_corrupt"
        roles = ["rejected_irrelevant"]
        action = "reject_as_irrelevant"
        rationale = "A Google search/time query is search machinery, not evidence."
        coverage = "none"
    elif _has_marker(text, _AGGREGATOR_MARKERS):
        quality = "circular_attribution"
        roles = ["discovery_only"]
        action = "remove_from_rendered_output"
        rationale = "The resolved page is a quotation aggregator and cannot verify the quotation."
        coverage = "none"
    elif _has_marker(text, _UNRELIABLE_DISCOVERY_MARKERS):
        quality = "discovery_lead_only"
        roles = ["discovery_only"]
        action = "remove_from_rendered_output"
        rationale = "The resolved page is useful only as a research lead."
        coverage = "none"
    elif not _direct_url_is_precise(final_url) or coverage == "none":
        quality = "insufficiently_located_evidence"
        roles = ["discovery_only"]
        action = "requires_further_research"
        rationale = "The resolved page does not contain a sufficiently complete wording match."
    else:
        primary_marker = _has_marker(text, _PRIMARY_MARKERS)
        primary = primary_marker and _primary_marker_is_contemporaneous(
            packet, final_url, title
        )
        secondary = (
            _has_marker(text, _RELIABLE_SECONDARY_MARKERS)
            or (primary_marker and not primary)
        )
        attribution_established = bool(
            primary or _mentions_thatcher(title) or _mentions_thatcher(passage)
        )
        if primary or (secondary and attribution_established):
            claims.update({"wording", "attribution"})
            quality = "strong_primary_evidence" if primary else "reliable_secondary_evidence"
            roles = _roles_for_claims(claims)
            action = "keep"
            public_title = title
            public_url = final_url
            rationale = (
                "The saved redirect resolves to a precise public page whose fetched text contains "
                f"a {coverage} wording match."
            )
        else:
            quality = "insufficiently_located_evidence"
            roles = ["discovery_only"]
            action = "requires_further_research"
            claims.clear()
            rationale = (
                "The resolved page contains the wording but either lacks recognised authority "
                "or does not explicitly attribute it to Thatcher near the retained evidence."
            )

    return {
        "source_id": sha256_bytes(f"{quote_id}:{index}:{source_fingerprint(source)}".encode()),
        "source_index": index,
        "source_fingerprint": source_fingerprint(source),
        "source_title": _clean(source.get("title")),
        "source_url": _clean(source.get("url")),
        "source_type": _clean(source.get("source_type")),
        "assigned_roles": roles,
        "source_quality_class": quality,
        "claims_supported": [field for field in CLAIM_FIELDS if field in claims],
        "claims_not_supported": [field for field in CLAIM_FIELDS if field not in claims],
        "claim_coverage": {"wording": coverage} if coverage != "none" else {},
        "action": action,
        "confidence_before": _clean(packet.get("research_confidence")),
        "supporting_passages": ([{
            "kind": "fetched_public_page_passage",
            "text": passage,
            "sha256": match["matched_passage_sha256"],
        }] if passage else []),
        "public_title": public_title,
        "public_url": public_url,
        "rationale": rationale,
        "resolved_redirect_record": True,
        "resolved_url": final_url,
        "resolution_record_sha256": sha256_bytes(canonical_json(resolution)),
    }


def audit_model_source_lead(
    quote_id: str,
    lead: dict[str, Any],
) -> dict[str, Any]:
    """Retain model-proposed URLs as leads without treating them as evidence."""
    title = _clean(lead.get("title"))
    url = _clean(lead.get("url"))
    source = {"title": title, "url": url, "source_type": lead.get("source_type")}
    host = urlsplit(url).netloc.casefold().removeprefix("www.")
    if quote_id == "eb1d2ebaac7e321e67174d2db5761d2bd008341ebb04b1abac4d4a927cd7a7d4" and host.endswith("nps.gov"):
        quality = "irrelevant_or_corrupt"
        roles = ["rejected_irrelevant"]
        action = "reject_as_irrelevant"
        rationale = _MANDATORY_REJECTIONS[(quote_id, "nps.gov")]
    elif _is_google_search(source):
        quality = "irrelevant_or_corrupt"
        roles = ["rejected_irrelevant"]
        action = "reject_as_irrelevant"
        rationale = "A Google search/time query is not evidence."
    elif not _is_http_url(url):
        quality = "broken_or_non_verifying_url"
        roles = ["rejected_irrelevant"]
        action = "remove_from_rendered_output"
        rationale = "The proposed URL is not a valid HTTP(S) locator."
    else:
        quality = "discovery_lead_only"
        roles = ["discovery_only"]
        action = "requires_further_research"
        rationale = "A model-proposed URL is an unverified research lead, not source evidence."
    return {
        "source_id": lead["lead_id"],
        "source_title": title,
        "source_url": url,
        "source_type": _clean(lead.get("source_type")),
        "assigned_roles": roles,
        "source_quality_class": quality,
        "claims_supported": [],
        "claims_not_supported": list(CLAIM_FIELDS),
        "action": action,
        "supporting_passages": [],
        "public_title": None,
        "public_url": None,
        "rationale": rationale,
        "model_proposed_source_lead": True,
        "provenance": lead.get("provenance", {}),
    }


def audit_researched_source(
    packet: dict[str, Any],
    source: dict[str, Any],
    independent_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert a fetched and passage-verified research result into a public row."""
    provider = _clean(source.get("research_provider")) or "gemini"
    model = _clean(source.get("research_model")) or "gemini-3.1-pro-preview"
    roles = list(dict.fromkeys(
        (independent_review or {}).get(
            "final_assigned_roles", source["assigned_roles"]
        )
    ))
    claims = set((independent_review or {}).get(
        "final_claims_supported", source["claims_supported"]
    ))
    quality = (independent_review or {}).get(
        "final_source_quality_class", source["source_quality_class"]
    )
    action = (independent_review or {}).get("final_action", "keep")
    passage = source["exact_supporting_passage"]
    coverage = _wording_coverage(packet, passage) if "wording" in claims else "none"
    if (
        "wording" in claims
        and source.get("wording_match_kind") == "approximate_semantic_guarded"
    ):
        coverage = "partial"
    if "wording" in claims and coverage == "none":
        raise RuntimeError(
            f"researched wording passage does not match quotation: {packet['quote_id']}"
        )
    renderable = quality in {
        "strong_primary_evidence",
        "reliable_secondary_evidence",
        "secondary_recollection",
    } and bool(claims)
    row = {
        "source_id": source["source_id"],
        "source_index": None,
        "source_fingerprint": sha256_bytes(canonical_json(source)),
        "source_title": _clean(source["title"]),
        "source_url": _clean(source["url"]),
        "source_type": f"{provider}_researched_fetched_source",
        "assigned_roles": roles,
        "source_quality_class": quality,
        "claims_supported": [field for field in CLAIM_FIELDS if field in claims],
        "claims_not_supported": [field for field in CLAIM_FIELDS if field not in claims],
        "claim_coverage": {"wording": coverage} if coverage != "none" else {},
        "action": action,
        "confidence_before": _clean(packet.get("research_confidence")),
        "supporting_passages": [{
            "kind": "fetched_verbatim_public_page_passage",
            "text": passage,
            "sha256": source["exact_supporting_passage_sha256"],
        }],
        "public_title": _clean(source["title"]) if renderable else None,
        "public_url": _clean(source["url"]) if renderable else None,
        "rationale": (independent_review or {}).get(
            "rationale",
            f"A bounded {provider} search located this source; deterministic retrieval "
            "confirmed that the exact supporting passage occurs on the public page.",
        ),
        "researched_source": True,
        "research_provider": provider,
        "research_model": model,
        "source_validation_policy_version": source.get(
            "source_validation_policy_version"
        ),
        "wording_match_kind": source.get("wording_match_kind"),
        "wording_similarity": source.get("wording_similarity"),
        f"{provider}_researched_source": True,
        "fetched_body_sha256": source["fetched_body_sha256"],
    }
    if independent_review is not None:
        row.update({
            "independently_reviewed": True,
            "independent_review_policy_version": REVIEW_POLICY_VERSION,
            "independent_review_decision": independent_review["decision"],
            "independent_review_checked_at": independent_review["checked_at"],
            "independent_review_current_page_body_sha256": independent_review[
                "current_page_body_sha256"
            ],
            "independent_review_stable_locator": independent_review[
                "stable_locator"
            ],
            "approximate_match_review": independent_review.get(
                "approximate_match_review"
            ),
        })
    return row


def _public_verification(packet: dict[str, Any], sources: list[dict[str, Any]]) -> str:
    verification_labels = {
        "exact": "Exact wording verified",
        "normalised": "Normalised wording verified",
        "excerpt": "Verified excerpt",
        "variant": "Historically verified variant",
        "paraphrase": "Paraphrase; exact wording not verified",
        "composite": "Composite wording assembled from related material",
        "misattributed": "Historically misattributed; not Thatcher wording",
        "unverified": "Exact wording not verified",
    }
    status = packet["verification_status"]
    def complete_wording(row: dict[str, Any]) -> bool:
        coverage = row.get("claim_coverage", {}).get("wording", "full")
        return coverage == "full" if status == "exact" else coverage in {"full", "normalised"}

    primary = any(
        "wording_verification" in row["assigned_roles"]
        and row["source_quality_class"] == "strong_primary_evidence"
        and complete_wording(row)
        for row in sources
    )
    secondary = next((
        row for row in sources
        if "wording_verification" in row["assigned_roles"]
        and row["source_quality_class"] in {"reliable_secondary_evidence", "secondary_recollection"}
        and complete_wording(row)
    ), None)
    partial = any(
        "wording_verification" in row["assigned_roles"]
        and row.get("claim_coverage", {}).get("wording") in {"normalised", "partial"}
        for row in sources
    )
    if status in _UNCERTAIN_WORDING:
        return verification_labels[status]
    if primary:
        return verification_labels[status]
    if secondary and secondary["source_quality_class"] == "secondary_recollection":
        return f"Reported in {secondary['public_title']}; no primary Thatcher transcript located"
    if secondary:
        return "Reported wording; no primary Thatcher transcript located"
    if partial:
        return "Wording partially supported by a retained source citation; exact wording not independently verified"
    if status == "variant":
        return "Historical variant not independently verified by the retained evidence"
    return "Exact wording not independently verified by the retained evidence"


def _packet_roles(packet: dict[str, Any], source_rows: list[dict[str, Any]], locator_kind: str | None) -> list[dict[str, Any]]:
    """Add a virtual canonical-locator record when the packet has a precise locator."""
    claims = _locator_claims(packet, locator_kind)
    if not claims:
        return source_rows
    if _is_recollection(packet) or any(
        row.get("indicates_secondary_recollection") for row in source_rows
    ):
        quality = "secondary_recollection"
    else:
        quality = "strong_primary_evidence" if locator_kind in {
            "primary_archive", "primary_official_record", "thatcher_authored_book",
        } else "reliable_secondary_evidence"
    roles = _roles_for_claims(claims)
    if quality == "secondary_recollection":
        roles.append("secondary_recollection")
    locator = _clean(packet.get("stable_locator"))
    virtual = {
        "source_id": sha256_bytes(f"{packet['quote_id']}:stable_locator:{locator}".encode()),
        "source_index": None,
        "source_fingerprint": sha256_bytes(locator.encode()),
        "source_title": locator,
        "source_url": "",
        "source_type": "canonical_stable_locator",
        "assigned_roles": roles,
        "source_quality_class": quality,
        "claims_supported": [field for field in CLAIM_FIELDS if field in claims],
        "claims_not_supported": [field for field in CLAIM_FIELDS if field not in claims],
        "action": "keep",
        "confidence_before": _clean(packet.get("research_confidence")),
        "supporting_passages": [],
        "public_title": locator,
        "public_url": "",
        "rationale": "The canonical packet contains a specific, independently usable locator.",
        "virtual_locator_record": True,
    }
    return source_rows + [virtual]


def _apply_curated_source_adjudications(
    packet: dict[str, Any],
    source_rows: list[dict[str, Any]],
    adjudications: list[dict[str, Any]],
    resolution_page_text_hashes: dict[str, str],
) -> list[dict[str, Any]]:
    """Apply hash-bound corrections to existing rows without adding sources."""
    rows = [dict(row) for row in source_rows]
    for adjudication in adjudications:
        matches = [
            (index, row)
            for index, row in enumerate(rows)
            if row.get("source_id") == adjudication["source_id"]
        ]
        if len(matches) != 1:
            raise RuntimeError(
                "historical-context curated adjudication target differs: "
                f"{packet['quote_id']}"
            )
        index, row = matches[0]
        passage_hashes = {
            passage.get("sha256")
            for passage in row.get("supporting_passages", [])
        }
        if (
            packet.get("verification_status") != "normalised"
            or adjudication["decision"] != "promote_partial_to_normalised"
            or adjudication["wording_coverage"] != "normalised"
            or row.get("source_quality_class") != "strong_primary_evidence"
            or row.get("claim_coverage", {}).get("wording") != "partial"
            or row.get("resolved_redirect_record") is not True
            or row.get("resolution_record_sha256")
            != adjudication["resolution_record_sha256"]
            or resolution_page_text_hashes.get(adjudication["source_id"])
            != adjudication["page_text_sha256"]
            or adjudication["matched_passage_sha256"] not in passage_hashes
            or row.get("public_url") != adjudication["public_url"]
        ):
            raise RuntimeError(
                "historical-context curated adjudication evidence differs: "
                f"{packet['quote_id']}"
            )
        updated = {
            **row,
            "claim_coverage": {
                **row.get("claim_coverage", {}),
                "wording": adjudication["wording_coverage"],
            },
            "rationale": adjudication["rationale"],
            "curated_source_adjudication": {
                key: adjudication[key]
                for key in (
                    "adjudication_id",
                    "decision",
                    "page_text_sha256",
                    "reviewed_at",
                )
            },
        }
        rows[index] = updated
    return rows


def audit_packet(
    packet: dict[str, Any],
    *,
    attribution_eligible: bool,
    recovery_item: dict[str, Any] | None = None,
    source_resolution: dict[str, Any] | None = None,
    research_item: dict[str, Any] | None = None,
    openai_research_item: dict[str, Any] | None = None,
    independent_review_items: dict[str, dict[str, Any]] | None = None,
    curated_evidence_item: dict[str, Any] | None = None,
    curated_source_adjudications: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Audit every source and confidence dimension for one packet."""
    locator_kind = precise_locator_kind(packet.get("stable_locator"))
    locator_is_precise = locator_kind not in {None, "quotation_compilation"}
    resolution_items = (source_resolution or {}).get("items", {})
    physical_rows = []
    resolution_page_text_hashes: dict[str, str] = {}
    for index, source in enumerate(packet.get("sources", [])):
        resolution = resolution_items.get(sha256_bytes(_clean(source.get("url")).encode()))
        if resolution is not None and packet["quote_id"] in resolution.get("quote_ids", []):
            row = audit_resolved_redirect(packet["quote_id"], packet, index, source, resolution)
        else:
            row = audit_source(
                packet["quote_id"], packet, index, source, locator_kind=locator_kind
            )
        physical_rows.append(row)
        if resolution is not None:
            resolution_page_text_hashes[row["source_id"]] = resolution.get(
                "page_text_sha256"
            )
    physical_rows = _apply_curated_source_adjudications(
        packet,
        physical_rows,
        curated_source_adjudications or [],
        resolution_page_text_hashes,
    )
    recovery_item = recovery_item or {}
    recovered_rows = [
        audit_recovered_citation(packet["quote_id"], packet, citation)
        for citation in recovery_item.get("citation_sources", [])
    ]
    lead_rows = [
        audit_model_source_lead(packet["quote_id"], lead)
        for lead in recovery_item.get("model_proposed_source_leads", [])
    ]
    researched_rows = [
        audit_researched_source(
            packet,
            source,
            (independent_review_items or {}).get(source["source_id"]),
        )
        for item in (research_item, openai_research_item)
        for source in (item or {}).get("validated_sources", [])
    ]
    curated_rows = [
        {
            "source_id": source["source_id"],
            "source_index": None,
            "source_fingerprint": source["source_id"],
            "source_title": source["title"],
            "source_url": source["url"],
            "source_type": source["source_type"],
            "source_event": source["source_event"],
            "source_date": source["source_date"],
            "assigned_roles": list(source["assigned_roles"]),
            "source_quality_class": source["source_quality_class"],
            "claims_supported": [
                field for field in CLAIM_FIELDS
                if field in source["claims_supported"]
            ],
            "claims_not_supported": [
                field for field in CLAIM_FIELDS
                if field not in source["claims_supported"]
            ],
            "claim_coverage": {
                "wording": (
                    "full"
                    if source["wording_match_kind"] == "exact"
                    else
                    "normalised"
                    if source["wording_match_kind"]
                    in {"historical_variant", "excerpt"}
                    else source["wording_match_kind"]
                )
            },
            "action": "keep",
            "confidence_before": _clean(packet.get("research_confidence")),
            "supporting_passages": [{
                "kind": (
                    "independently_reviewed_archival_passage"
                    if source.get("evidence_origin")
                    == "independently_reviewed_archival_retrieval"
                    else "independently_reviewed_public_source_passage"
                    if source.get("evidence_origin")
                    == "independently_reviewed_public_retrieval"
                    else "operator_supplied_book_passage"
                ),
                "text": source["exact_supporting_passage"],
                "sha256": source["exact_supporting_passage_sha256"],
            }],
            "public_title": source["title"],
            "public_url": source["url"],
            "stable_locator": source["stable_locator"],
            "rationale": source["rationale"],
            "curated_evidence_record": True,
            "recorded_at": source["recorded_at"],
            "page_independently_inspected": source[
                "page_independently_inspected"
            ],
            **{
                key: source[key]
                for key in (
                    "author_or_speaker",
                    "source_publisher",
                    "canonical_url",
                    "retrieval_archive",
                    "transport_url",
                    "archive_capture_timestamp",
                    "archive_capture_digest",
                    "fetch_policy_version",
                    "page_sha256",
                    "page_text_sha256",
                    "source_date_raw",
                    "date",
                    "supporting_context",
                    "supporting_context_sha256",
                )
                if key in source
            },
        }
        for source in (curated_evidence_item or {}).get("sources", [])
    ]
    rows = _packet_roles(
        packet,
        physical_rows + recovered_rows + researched_rows + curated_rows,
        locator_kind,
    )
    def claim_confidence(claim: str) -> str:
        ranking = {
            "strong_primary_evidence": 3,
            "reliable_secondary_evidence": 2,
            "secondary_recollection": 1,
        }
        scores = []
        for row in rows:
            if claim not in row.get("claims_supported", []):
                continue
            score = ranking.get(row["source_quality_class"], 0)
            if claim == "wording":
                coverage = row.get("claim_coverage", {}).get("wording", "full")
                if coverage == "partial" or (
                    packet["verification_status"] == "exact" and coverage != "full"
                ):
                    score = min(score, 2)
            scores.append(score)
        score = max(scores, default=0)
        return {3: "high", 2: "medium", 1: "low", 0: "unknown"}[score]

    confidence = {
        "attribution": claim_confidence("attribution"),
        "wording": claim_confidence("wording"),
        "source_event": claim_confidence("source_event"),
        "date": claim_confidence("date") if _known(packet.get("date")) else "unknown",
        "historical_context": claim_confidence("historical_context"),
        "interpretation": _clean(packet.get("research_confidence")) or "unknown",
    }
    renderable = [
        row for row in rows
        if row.get("public_title")
        and any(role in row["assigned_roles"] for role in {
            "wording_verification", "attribution_support", "source_event_support",
            "historical_context_support", "secondary_recollection",
        })
    ]
    roles = {role for row in renderable for role in row["assigned_roles"]}
    requires_research = (
        not renderable
        or confidence["wording"] != "high"
        or confidence["attribution"] == "unknown"
    )
    material_change_reasons: list[str] = []
    if any(row["action"] == "reject_as_irrelevant" for row in physical_rows):
        material_change_reasons.append("irrelevant source suppressed")
    if any(row["assigned_roles"] == ["discovery_only"] for row in physical_rows):
        material_change_reasons.append("discovery-only source suppressed")
    if not renderable:
        material_change_reasons.append("no reliable public source")
    if packet["verification_status"] not in _UNCERTAIN_WORDING and confidence["wording"] != "high":
        material_change_reasons.append("verification wording made source-conservative")
    return {
        "quote_id": packet["quote_id"],
        "quote_text": packet["quote_text"],
        "quote_text_sha256": sha256_bytes(packet["quote_text"].encode()),
        "attribution_eligible": attribution_eligible,
        "current_wording_status": packet["verification_status"],
        "wording_status_after": packet["verification_status"],
        "speaker": packet["speaker"],
        "source_event": packet["source_event"],
        "date": packet["date"],
        "stable_locator": packet["stable_locator"],
        "locator_audit": {
            "kind": locator_kind,
            "precise": locator_is_precise,
            "rationale": (
                "Specific canonical locator retained for role-scoped rendering."
                if locator_is_precise else
                "Quotation compilations are circular attribution evidence and are not public verification."
                if locator_kind == "quotation_compilation" else
                "Locator is absent, placeholder, generic, or insufficiently precise for public verification."
            ),
        },
        "source_count": len(physical_rows),
        "sources": physical_rows,
        "recovered_citation_count": len(recovered_rows),
        "recovered_sources": recovered_rows,
        "model_proposed_source_lead_count": len(lead_rows),
        "model_proposed_source_leads": lead_rows,
        "researched_source_count": len(researched_rows),
        "researched_source_provider_counts": dict(sorted(Counter(
            row["research_provider"] for row in researched_rows
        ).items())),
        "researched_sources": researched_rows,
        "curated_source_count": len(curated_rows),
        "curated_sources": curated_rows,
        "curated_source_adjudication_count": len(
            curated_source_adjudications or []
        ),
        "curated_source_adjudications": list(
            curated_source_adjudications or []
        ),
        "bounded_research_outcomes": {
            provider: item.get("final_outcome")
            for provider, item in (
                ("gemini", research_item), ("openai", openai_research_item)
            )
            if item is not None
        },
        "virtual_locator_sources": [row for row in rows if row.get("virtual_locator_record")],
        "renderable_sources": renderable,
        "supported_public_roles": sorted(roles & SOURCE_ROLES),
        "confidence_before": packet["research_confidence"],
        "confidence_after": confidence,
        "public_verification_wording": _public_verification(packet, rows),
        "public_context_supported_fields": [
            field for field in (
                "source_event", "date", "historical_context",
            )
            if confidence[field] != "unknown"
            and any(
                field in row.get("claims_supported", [])
                for row in renderable
            )
        ],
        "requires_further_research": requires_research,
        "public_output_changes": sorted(set(material_change_reasons)),
    }


def build_audit(
    packets: dict[str, dict[str, Any]],
    unresolved: set[str],
    *,
    research_dir: Path,
    attribution_eligible_ids: set[str],
    recovered_evidence: dict[str, Any] | None = None,
    source_resolution: dict[str, Any] | None = None,
    researched_evidence: dict[str, Any] | None = None,
    openai_researched_evidence: dict[str, Any] | None = None,
    independent_review: dict[str, Any] | None = None,
    curated_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the complete deterministic source-role sidecar."""
    if recovered_evidence is not None:
        validate_recovery(recovered_evidence, packets)
    if source_resolution is not None:
        validate_resolution(source_resolution, packets)
    if researched_evidence is not None:
        validate_research_manifest(researched_evidence, packets)
    if openai_researched_evidence is not None:
        validate_openai_research_manifest(openai_researched_evidence, packets)
    researched_source_total = sum(
        len(item.get("validated_sources", []))
        for manifest in (researched_evidence, openai_researched_evidence)
        for item in (manifest or {}).get("items", {}).values()
    )
    if researched_source_total and independent_review is None:
        raise RuntimeError(
            "independent review is required for every AI-located source"
        )
    if independent_review is not None:
        validate_independent_review(
            independent_review,
            packets,
            researched_evidence,
            openai_researched_evidence,
        )
    if curated_evidence is not None:
        validate_curated_evidence(curated_evidence, packets)
    curated_adjudications_by_quote: dict[str, list[dict[str, Any]]] = {}
    for adjudication in (curated_evidence or {}).get(
        "source_adjudications", []
    ):
        curated_adjudications_by_quote.setdefault(
            adjudication["quote_id"], []
        ).append(adjudication)
    items = {
        quote_id: audit_packet(
            packet,
            attribution_eligible=quote_id in attribution_eligible_ids,
            recovery_item=(recovered_evidence or {}).get("items", {}).get(quote_id),
            source_resolution=source_resolution,
            research_item=(researched_evidence or {}).get("items", {}).get(quote_id),
            openai_research_item=(openai_researched_evidence or {}).get(
                "items", {}
            ).get(quote_id),
            independent_review_items=(independent_review or {}).get("items", {}),
            curated_evidence_item=(curated_evidence or {}).get("items", {}).get(
                quote_id
            ),
            curated_source_adjudications=curated_adjudications_by_quote.get(
                quote_id, []
            ),
        )
        for quote_id, packet in sorted(packets.items())
    }
    physical_sources = [source for item in items.values() for source in item["sources"]]
    recovered_sources = [
        source for item in items.values() for source in item["recovered_sources"]
    ]
    lead_sources = [
        source for item in items.values() for source in item["model_proposed_source_leads"]
    ]
    researched_sources = [
        source for item in items.values() for source in item["researched_sources"]
    ]
    curated_sources = [
        source for item in items.values() for source in item["curated_sources"]
    ]
    curated_source_adjudication_count = sum(
        item["curated_source_adjudication_count"] for item in items.values()
    )
    virtual_locator_sources = [
        source
        for item in items.values()
        for source in item["virtual_locator_sources"]
    ]
    all_sources = (
        physical_sources + recovered_sources + lead_sources
        + researched_sources + curated_sources
    )
    all_evidentiary_sources = all_sources + virtual_locator_sources
    quality_counts = Counter(source["source_quality_class"] for source in all_sources)
    role_counts = Counter(role for source in all_sources for role in source["assigned_roles"])
    evidentiary_quality_counts = Counter(
        source["source_quality_class"] for source in all_evidentiary_sources
    )
    evidentiary_role_counts = Counter(
        role
        for source in all_evidentiary_sources
        for role in source["assigned_roles"]
    )
    changed = [quote_id for quote_id, item in items.items() if item["public_output_changes"]]
    needs_research = [quote_id for quote_id, item in items.items() if item["requires_further_research"]]
    no_reliable = [quote_id for quote_id, item in items.items() if not item["renderable_sources"]]
    source_paths = (
        "research_packets.json",
        "grounding_sources.json",
        "corpus_manifest.json",
        "final_unresolved/final_research_status.json",
    )
    source_file_hashes = {
        relative: file_sha256(research_dir / relative) for relative in source_paths
    }
    if recovered_evidence is not None:
        source_file_hashes[RECOVERY_FILENAME] = file_sha256(research_dir / RECOVERY_FILENAME)
    if source_resolution is not None:
        source_file_hashes[RESOLUTION_FILENAME] = file_sha256(research_dir / RESOLUTION_FILENAME)
    if researched_evidence is not None:
        source_file_hashes[RESEARCH_FILENAME] = file_sha256(research_dir / RESEARCH_FILENAME)
    if openai_researched_evidence is not None:
        source_file_hashes[OPENAI_RESEARCH_FILENAME] = file_sha256(
            research_dir / OPENAI_RESEARCH_FILENAME
        )
    if independent_review is not None:
        source_file_hashes[REVIEW_FILENAME] = file_sha256(
            research_dir / REVIEW_FILENAME
        )
    if curated_evidence is not None:
        source_file_hashes[CURATED_EVIDENCE_FILENAME] = file_sha256(
            research_dir / CURATED_EVIDENCE_FILENAME
        )
    gemini_researched_sources = [
        source for source in researched_sources
        if source.get("research_provider") == "gemini"
    ]
    openai_researched_sources = [
        source for source in researched_sources
        if source.get("research_provider") == "openai"
    ]
    accepted_qualities = {
        "strong_primary_evidence",
        "reliable_secondary_evidence",
        "secondary_recollection",
    }
    discovery_qualities = {
        "discovery_lead_only",
        "insufficiently_located_evidence",
    }
    rejected_qualities = {
        "irrelevant_or_corrupt",
        "circular_attribution",
        "broken_or_non_verifying_url",
    }
    headline_dispositions = {
        "accepted_evidence": sum(
            source["source_quality_class"] in accepted_qualities
            for source in all_sources
        ),
        "discovery_or_insufficient": sum(
            source["source_quality_class"] in discovery_qualities
            for source in all_sources
        ),
        "rejected_or_non_verifying": sum(
            source["source_quality_class"] in rejected_qualities
            for source in all_sources
        ),
        "total": len(all_sources),
    }
    if sum(
        headline_dispositions[key]
        for key in (
            "accepted_evidence", "discovery_or_insufficient",
            "rejected_or_non_verifying",
        )
    ) != headline_dispositions["total"]:
        raise RuntimeError("exclusive source disposition counts do not balance")
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "audit_date": "2026-07-22",
        "source_file_hashes": source_file_hashes,
        "packet_count": len(items),
        "source_count": len(physical_sources),
        "recovered_citation_source_count": len(recovered_sources),
        "model_proposed_source_lead_count": len(lead_sources),
        "gemini_researched_source_count": len(gemini_researched_sources),
        "openai_researched_source_count": len(openai_researched_sources),
        "researched_source_count": len(researched_sources),
        "curated_source_count": len(curated_sources),
        "curated_source_adjudication_count": curated_source_adjudication_count,
        "audited_source_record_count": len(all_sources),
        "virtual_locator_source_count": len(virtual_locator_sources),
        "all_evidentiary_source_record_count": len(all_evidentiary_sources),
        "unresolved_quote_count": len(unresolved),
        "unresolved_quote_ids": sorted(unresolved),
        "attribution_eligible_quote_count": len(attribution_eligible_ids),
        "attribution_eligible_quote_ids_sha256": quote_set_hash(attribution_eligible_ids),
        "summary": {
            "count_semantics": {
                "headline_observed_source_provenance_counts": (
                    "Mutually exclusive provenance categories for physical, recovered, "
                    "model-lead, independently reviewed AI-located and curated records."
                ),
                "headline_observed_source_disposition_counts": (
                    "Mutually exclusive accepted, discovery/insufficient and rejected "
                    "dispositions for observed source records."
                ),
                "source_quality_counts": (
                    "One mutually exclusive quality class per observed source record; "
                    "canonical virtual locator records are excluded."
                ),
                "source_role_counts": (
                    "Overlapping claim roles for observed source records; one source may "
                    "contribute more than one role assignment."
                ),
                "all_evidentiary_source_quality_counts": (
                    "One mutually exclusive quality class per observed or canonical "
                    "virtual-locator evidence record."
                ),
                "all_evidentiary_source_role_counts": (
                    "Overlapping claim roles across observed and canonical virtual-locator "
                    "evidence records."
                ),
            },
            "headline_observed_source_provenance_counts": {
                "physical_packet_sources": len(physical_sources),
                "recovered_citation_sources": len(recovered_sources),
                "model_proposed_source_leads": len(lead_sources),
                "ai_located_independently_reviewed_sources": len(researched_sources),
                "operator_curated_sources": len(curated_sources),
                "total": len(all_sources),
            },
            "headline_observed_source_disposition_counts": headline_dispositions,
            "additional_virtual_locator_source_count": len(virtual_locator_sources),
            "all_evidentiary_source_record_count": len(all_evidentiary_sources),
            "source_quality_counts": dict(sorted(quality_counts.items())),
            "source_role_counts": dict(sorted(role_counts.items())),
            "all_evidentiary_source_quality_counts": dict(
                sorted(evidentiary_quality_counts.items())
            ),
            "all_evidentiary_source_role_counts": dict(
                sorted(evidentiary_role_counts.items())
            ),
            "source_role_assignment_count": sum(role_counts.values()),
            "all_evidentiary_source_role_assignment_count": sum(
                evidentiary_role_counts.values()
            ),
            "physical_source_quality_counts": dict(sorted(Counter(
                source["source_quality_class"] for source in physical_sources
            ).items())),
            "recovered_source_quality_counts": dict(sorted(Counter(
                source["source_quality_class"] for source in recovered_sources
            ).items())),
            "model_proposed_lead_quality_counts": dict(sorted(Counter(
                source["source_quality_class"] for source in lead_sources
            ).items())),
            "gemini_researched_source_quality_counts": dict(sorted(Counter(
                source["source_quality_class"] for source in gemini_researched_sources
            ).items())),
            "openai_researched_source_quality_counts": dict(sorted(Counter(
                source["source_quality_class"] for source in openai_researched_sources
            ).items())),
            "curated_source_quality_counts": dict(sorted(Counter(
                source["source_quality_class"] for source in curated_sources
            ).items())),
            "curated_source_adjudication_count": (
                curated_source_adjudication_count
            ),
            "packets_with_no_reliable_source": len(no_reliable),
            "packets_whose_public_output_changes": len(changed),
            "packets_requiring_new_historical_research": len(needs_research),
            "changed_quote_ids": changed,
            "no_reliable_source_quote_ids": no_reliable,
            "requires_research_quote_ids": needs_research,
        },
        "items": items,
    }


def validate_and_attach_audit(
    packets: dict[str, dict[str, Any]],
    unresolved: set[str],
    *,
    research_dir: Path,
    attribution_eligible_ids: set[str],
    required: bool,
) -> dict[str, dict[str, Any]]:
    """Validate the sidecar against immutable packets, then attach packet rows."""
    path = research_dir / AUDIT_FILENAME
    if not path.exists():
        if required:
            raise RuntimeError(f"required historical-context source-role audit is missing: {path}")
        return packets
    audit = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(audit, dict) or audit.get("schema_version") != AUDIT_SCHEMA_VERSION:
        raise RuntimeError("historical-context source-role audit schema is incompatible")
    if audit.get("policy_version") != POLICY_VERSION:
        raise RuntimeError("historical-context source-role audit policy is incompatible")
    if audit.get("packet_count") != len(packets) or set(audit.get("items", {})) != set(packets):
        raise RuntimeError("historical-context source-role audit packet coverage is incomplete")
    if audit.get("unresolved_quote_ids") != sorted(unresolved):
        raise RuntimeError("historical-context source-role audit unresolved partition differs")
    if audit.get("attribution_eligible_quote_count") != len(attribution_eligible_ids):
        raise RuntimeError("historical-context source-role audit eligible count differs")
    if audit.get("attribution_eligible_quote_ids_sha256") != quote_set_hash(attribution_eligible_ids):
        raise RuntimeError("historical-context source-role audit eligible identity set differs")
    expected_packet_hash = file_sha256(research_dir / "research_packets.json")
    if audit.get("source_file_hashes", {}).get("research_packets.json") != expected_packet_hash:
        raise RuntimeError("historical-context source-role audit is stale for research packets")
    recovery_path = research_dir / RECOVERY_FILENAME
    if not recovery_path.exists():
        raise RuntimeError(
            f"historical-context source recovery manifest is missing: {recovery_path}"
        )
    recovery = json.loads(recovery_path.read_text(encoding="utf-8"))
    validate_recovery(recovery, packets)
    if audit.get("source_file_hashes", {}).get(RECOVERY_FILENAME) != file_sha256(recovery_path):
        raise RuntimeError("historical-context source-role audit is stale for recovered citations")
    resolution = None
    if RESOLUTION_FILENAME in audit.get("source_file_hashes", {}):
        resolution_path = research_dir / RESOLUTION_FILENAME
        if not resolution_path.exists():
            raise RuntimeError(
                f"historical-context source resolution manifest is missing: {resolution_path}"
            )
        resolution = json.loads(resolution_path.read_text(encoding="utf-8"))
        validate_resolution(resolution, packets)
        if audit["source_file_hashes"][RESOLUTION_FILENAME] != file_sha256(resolution_path):
            raise RuntimeError("historical-context source-role audit is stale for resolved URLs")
    researched = None
    if RESEARCH_FILENAME in audit.get("source_file_hashes", {}):
        research_path = research_dir / RESEARCH_FILENAME
        if not research_path.exists():
            raise RuntimeError(
                f"historical-context source research manifest is missing: {research_path}"
            )
        researched = json.loads(research_path.read_text(encoding="utf-8"))
        validate_research_manifest(researched, packets)
        if audit["source_file_hashes"][RESEARCH_FILENAME] != file_sha256(research_path):
            raise RuntimeError("historical-context source-role audit is stale for researched evidence")
    openai_researched = None
    if OPENAI_RESEARCH_FILENAME in audit.get("source_file_hashes", {}):
        openai_path = research_dir / OPENAI_RESEARCH_FILENAME
        if not openai_path.exists():
            raise RuntimeError(
                f"OpenAI historical-context source manifest is missing: {openai_path}"
            )
        openai_researched = json.loads(openai_path.read_text(encoding="utf-8"))
        validate_openai_research_manifest(openai_researched, packets)
        if (
            audit["source_file_hashes"][OPENAI_RESEARCH_FILENAME]
            != file_sha256(openai_path)
        ):
            raise RuntimeError(
                "historical-context source-role audit is stale for OpenAI evidence"
            )
    independent_review = None
    if REVIEW_FILENAME in audit.get("source_file_hashes", {}):
        review_path = research_dir / REVIEW_FILENAME
        if not review_path.exists():
            raise RuntimeError(
                f"historical-context independent source review is missing: {review_path}"
            )
        independent_review = json.loads(review_path.read_text(encoding="utf-8"))
        validate_independent_review(
            independent_review,
            packets,
            researched,
            openai_researched,
        )
        if audit["source_file_hashes"][REVIEW_FILENAME] != file_sha256(review_path):
            raise RuntimeError(
                "historical-context source-role audit is stale for independent review"
            )
    elif researched is not None or openai_researched is not None:
        raise RuntimeError(
            "historical-context AI-located sources lack independent review"
        )
    curated_evidence = None
    if CURATED_EVIDENCE_FILENAME in audit.get("source_file_hashes", {}):
        curated_path = research_dir / CURATED_EVIDENCE_FILENAME
        if not curated_path.exists():
            raise RuntimeError(
                f"historical-context curated evidence is missing: {curated_path}"
            )
        curated_evidence = json.loads(curated_path.read_text(encoding="utf-8"))
        validate_curated_evidence(curated_evidence, packets)
        if (
            audit["source_file_hashes"][CURATED_EVIDENCE_FILENAME]
            != file_sha256(curated_path)
        ):
            raise RuntimeError(
                "historical-context source-role audit is stale for curated evidence"
            )
    expected = build_audit(
        packets,
        unresolved,
        research_dir=research_dir,
        attribution_eligible_ids=attribution_eligible_ids,
        recovered_evidence=recovery,
        source_resolution=resolution,
        researched_evidence=researched,
        openai_researched_evidence=openai_researched,
        independent_review=independent_review,
        curated_evidence=curated_evidence,
    )
    if canonical_json(audit) != canonical_json(expected):
        raise RuntimeError("historical-context source-role audit differs from deterministic policy output")
    attached: dict[str, dict[str, Any]] = {}
    for quote_id, packet in packets.items():
        row = audit["items"][quote_id]
        if row.get("quote_text") != packet["quote_text"] or row.get("wording_status_after") != packet["verification_status"]:
            raise RuntimeError(f"historical-context source-role audit changed quotation identity: {quote_id}")
        audited_sources = row.get("sources")
        if not isinstance(audited_sources, list) or len(audited_sources) != len(packet["sources"]):
            raise RuntimeError(f"historical-context source-role source coverage differs: {quote_id}")
        for index, (source, source_row) in enumerate(zip(packet["sources"], audited_sources)):
            if source_row.get("source_index") != index or source_row.get("source_fingerprint") != source_fingerprint(source):
                raise RuntimeError(f"historical-context source-role source identity differs: {quote_id}:{index}")
            if not set(source_row.get("assigned_roles", [])) <= SOURCE_ROLES:
                raise RuntimeError(f"historical-context source-role role is invalid: {quote_id}:{index}")
            if source_row.get("source_quality_class") not in QUALITY_CLASSES:
                raise RuntimeError(f"historical-context source quality is invalid: {quote_id}:{index}")
            if source_row.get("action") not in ACTIONS:
                raise RuntimeError(f"historical-context source action is invalid: {quote_id}:{index}")
        if row.get("recovered_citation_count") != len(
            recovery["items"][quote_id]["citation_sources"]
        ):
            raise RuntimeError(f"historical-context recovered source coverage differs: {quote_id}")
        expected_researched = len((researched or {}).get("items", {}).get(quote_id, {}).get(
            "validated_sources", []
        )) + len((openai_researched or {}).get("items", {}).get(quote_id, {}).get(
            "validated_sources", []
        ))
        if row.get("researched_source_count") != expected_researched:
            raise RuntimeError(f"historical-context researched source coverage differs: {quote_id}")
        expected_curated = len(
            (curated_evidence or {}).get("items", {}).get(quote_id, {}).get(
                "sources", []
            )
        )
        if row.get("curated_source_count") != expected_curated:
            raise RuntimeError(
                f"historical-context curated source coverage differs: {quote_id}"
            )
        expected_adjudications = sum(
            adjudication.get("quote_id") == quote_id
            for adjudication in (curated_evidence or {}).get(
                "source_adjudications", []
            )
        )
        if (
            row.get("curated_source_adjudication_count")
            != expected_adjudications
        ):
            raise RuntimeError(
                "historical-context curated source adjudication coverage "
                f"differs: {quote_id}"
            )
        attached[quote_id] = {
            **packet,
            "_source_role_audit": {**row, "policy_version": audit["policy_version"]},
        }
    return attached


_MTF_HOSTS = frozenset({"margaretthatcher.org", "www.margaretthatcher.org"})
_TRACKING_QUERY_KEYS = frozenset({
    "_hsenc", "_hsmi", "dclid", "fbclid", "gad_campaignid", "gad_source",
    "gclid", "gclsrc", "gbraid", "igshid", "mc_cid", "mc_eid", "mkt_tok",
    "msclkid", "ref_src", "srsltid", "twclid", "wbraid", "yclid",
})
_IDENTIFIER_FIELD_KINDS = {
    "document_identifier": "document",
    "document_id": "document",
    "document_number": "document",
    "speech_identifier": "speech",
    "speech_id": "speech",
    "speech_number": "speech",
    "volume_identifier": "volume",
    "volume_id": "volume",
    "volume_number": "volume",
    "page_identifier": "page",
    "page_id": "page",
    "page_number": "page",
    "chapter_identifier": "chapter",
    "chapter_id": "chapter",
    "chapter_number": "chapter",
    "edition_identifier": "edition",
    "edition_id": "edition",
    "edition_number": "edition",
    "record_identifier": "record",
    "record_id": "record",
    "record_number": "record",
    "archive_reference": "archive",
    "catalogue_reference": "catalogue",
    "catalog_reference": "catalogue",
}
_EXPLICIT_IDENTIFIER_FIELDS = tuple(_IDENTIFIER_FIELD_KINDS)
_LOCATOR_FIELDS = (
    "stable_locator", "volume", "page", "folio", "chapter", "edition",
)
_IDENTITY_TEXT_FIELDS = (
    "public_url", "source_url", "url", "canonical_url", "stable_locator",
    "public_title", "source_title", "title", "publisher", "archive",
    "repository", "source_date", "date", "source_event",
) + _EXPLICIT_IDENTIFIER_FIELDS
_DISPLAY_ROLE_EXCLUSIONS = frozenset({
    "secondary_recollection", "discovery_only", "rejected_irrelevant",
})
_ROLE_PRIORITY = {
    "wording_verification": 0,
    "attribution_support": 1,
    "source_event_support": 2,
    "historical_context_support": 3,
}
_QUALITY_PRIORITY = {
    "strong_primary_evidence": 0,
    "reliable_secondary_evidence": 1,
    "secondary_recollection": 2,
}
_IDENTITY_BASIS_PRIORITY = {
    "margaret_thatcher_foundation_document": 0,
    "corroborated_metadata_locator": 1,
    "corroborated_hansard_locator_url": 2,
    "explicit_repository_identifier": 3,
    "explicit_archive_reference": 4,
    "canonical_url_alias_to_explicit_identifier": 5,
    "canonical_url": 6,
    "bibliographic_identity": 7,
    "distinct_source_record": 8,
}
_MTF_DOCUMENT_PATH = re.compile(r"^/document/(\d{5,9})/?$", re.I)
_DOCUMENT_TEXT = re.compile(
    r"\bdoc(?:ument)?(?:\s*(?:id|number|no\.?))?\s*[#:]?\s*(\d{5,9})\b",
    re.I,
)
_MTF_TEXT_DOCUMENT_PATH = re.compile(
    r"(?<![0-9A-Za-z.-])(?:www\.)?margaretthatcher\.org/document/(\d{5,9})\b",
    re.I,
)
_ARCHIVE_REFERENCE = re.compile(
    r"\b(THCR|CCOPR)\s*([0-9A-Za-z][0-9A-Za-z/.-]*)"
    r"(?:(?:\s*[,;:]\s*|\s+)(f(?:olio)?|p(?:age)?\.?)\s*(\d+))?\b",
    re.I,
)
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", re.I)
_GENERIC_URL = re.compile(r"https?://[^\s<>\"']+", re.I)
_MARKDOWN_URL_START = re.compile(r"\]\(\s*(https?://)", re.I)
_MTF_REPOSITORY_IDENTITIES = frozenset({
    "margaret_thatcher_foundation",
    "margaret_thatcher_foundation_archive",
    "margaretthatcher_org",
})
_MONTH_NUMBERS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2,
    "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5,
    "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8,
    "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_PUBLIC_MONTH_NAMES = (
    "", "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
)
_MONTH_PATTERN = "|".join(sorted(_MONTH_NUMBERS, key=len, reverse=True))
_DATE_DMY = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_PATTERN})\.?,?\s+((?:18|19|20)\d{{2}})\b",
    re.I,
)
_DATE_MDY = re.compile(
    rf"\b({_MONTH_PATTERN})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+((?:18|19|20)\d{{2}})\b",
    re.I,
)
_DATE_YMD_TEXT = re.compile(
    rf"\b((?:18|19|20)\d{{2}})[,\s]+({_MONTH_PATTERN})\.?\s+"
    rf"(\d{{1,2}})(?:st|nd|rd|th)?\b",
    re.I,
)
_DATE_ISO = re.compile(r"\b((?:18|19|20)\d{2})-(\d{2})-(\d{2})\b")
_DATE_YEAR_MONTH = re.compile(
    r"\b((?:18|19|20)\d{2})-(0[1-9]|1[0-2])\b(?!-\d{2})"
)
_DATE_MONTH_YEAR = re.compile(
    rf"\b({_MONTH_PATTERN})\.?\s+((?:18|19|20)\d{{2}})\b",
    re.I,
)
_DATE_ABBREVIATED_DMY = re.compile(
    r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?,?\s+"
    r"((?:18|19|20)\d{2})\b",
    re.I,
)
_HANSARD_HC_STABLE_LOCATOR = re.compile(
    r"^Hansard, HC Deb (?P<day>\d{1,2}) "
    r"(?P<month>January|February|March|April|May|June|July|August|"
    r"September|October|November|December) (?P<year>(?:18|19|20)\d{2}) "
    r"vol (?P<volume>\d+) cc(?P<first_column>\d+)-(?P<last_column>\d+)$",
    re.I,
)
_HANSARD_PUBLICATIONS_COMMONS_PATH = re.compile(
    r"^/pa/cm\d{6}/cmhansrd/(?P<date>(?:18|19|20)\d{2}-\d{2}-\d{2})/"
    r"Debate-\d+\.html$",
    re.I,
)
_HANSARD_MODERN_COMMONS_PATH = re.compile(
    r"^/Commons/(?P<date>(?:18|19|20)\d{2}-\d{2}-\d{2})/debates/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/"
    r"[^/]+$",
    re.I,
)
_PRIMARY_IDENTIFIER_KINDS = (
    "document", "speech", "record", "archive", "catalogue",
    "volume", "chapter", "page", "edition",
)
_SUBORDINATE_IDENTIFIER_KINDS = ("edition", "volume", "chapter", "page")


def _normalise_identity_component(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _clean(value).casefold()).strip("_")


def _metadata_identity_scalars(
    value: Any, *, key_path: str = "",
) -> list[tuple[str, str]]:
    scalars: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key in sorted(value, key=lambda item: str(item)):
            item = value[key]
            child_path = "_".join(filter(None, (
                key_path, _normalise_identity_component(key),
            )))
            scalars.extend(_metadata_identity_scalars(item, key_path=child_path))
    elif isinstance(value, list):
        for item in value:
            scalars.extend(_metadata_identity_scalars(item, key_path=key_path))
    elif isinstance(value, (str, int)) and _clean(value):
        scalars.append((key_path, _clean(value)))
    return scalars


def _identity_values(source: dict[str, Any]) -> list[str]:
    values = [
        _clean(source.get(field)) for field in _IDENTITY_TEXT_FIELDS
        if _clean(source.get(field))
    ]
    for container_name in ("metadata", "evidence_metadata", "provenance"):
        container = source.get(container_name)
        if not isinstance(container, dict):
            continue
        for key_path, value in _metadata_identity_scalars(container):
            if any(marker in key_path for marker in (
                "url", "title", "document", "record", "locator", "archive",
                "publisher", "repository", "catalog",
            )):
                values.append(value)
    return list(dict.fromkeys(values))


def _trim_url_candidate(value: str) -> str:
    """Strip prose punctuation without truncating balanced URL delimiters."""
    candidate = value.rstrip(".,;:")
    for closing, opening in ((")", "("), ("]", "["), ("}", "{")):
        while candidate.endswith(closing) and candidate.count(closing) > candidate.count(opening):
            candidate = candidate[:-1]
    return candidate


def _markdown_url_candidates(text: str) -> list[str]:
    """Extract Markdown targets while preserving balanced path parentheses."""
    candidates: list[str] = []
    for match in _MARKDOWN_URL_START.finditer(text):
        start = match.start(1)
        depth = 1
        end = start
        while end < len(text):
            character = text[end]
            if character.isspace():
                break
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:end])
                    break
            end += 1
    return candidates


def _url_candidates(value: Any) -> list[str]:
    raw_text = str(value or "").strip().strip("<>")
    if any(ord(character) < 32 or ord(character) == 127 for character in raw_text):
        return []
    text = _clean(raw_text)
    if not text:
        return []
    candidates = _markdown_url_candidates(text)
    candidates.extend(match.group(0) for match in _GENERIC_URL.finditer(text))
    normalised_candidates = list(dict.fromkeys(
        candidate for raw in candidates
        if (candidate := _trim_url_candidate(raw))
    ))
    if (
        re.match(r"^https?://", raw_text, re.I)
        and any(character.isspace() for character in raw_text)
        and len(normalised_candidates) < 2
    ):
        return []
    return normalised_candidates


def _valid_hostname(host: str) -> bool:
    """Return whether a parsed hostname is safe to reconstruct publicly."""
    if ":" in host:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return False
        return True
    if not re.fullmatch(r"[a-z0-9.-]+", host) or ".." in host:
        return False
    return all(
        label and not label.startswith("-") and not label.endswith("-")
        for label in host.split(".")
    )


def _tracking_query_component(component: str) -> bool:
    raw_key = component.partition("=")[0]
    key = unquote_plus(raw_key).casefold()
    return key.startswith("utm_") or key in _TRACKING_QUERY_KEYS


def _normalise_one_url(candidate: str) -> tuple[bool, str] | None:
    """Normalise one URL, returning its redirect status and public form."""
    if "\\" in candidate or len(re.findall(r"https?://", candidate, re.I)) != 1:
        return None
    try:
        parsed = urlsplit(candidate)
        host = (parsed.hostname or "").casefold().rstrip(".")
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.casefold()
    if (
        scheme not in {"http", "https"}
        or not host
        or parsed.username
        or parsed.password
        or not _valid_hostname(host)
    ):
        return None
    if (
        host == "vertexaisearch.cloud.google.com"
        and parsed.path.startswith("/grounding-api-redirect/")
    ):
        return None
    default_port = port is None or (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    )
    official_mtf = host in _MTF_HOSTS and default_port
    if official_mtf:
        host = "www.margaretthatcher.org"
    host_for_netloc = f"[{host}]" if ":" in host else host
    netloc = host_for_netloc
    if not default_port:
        netloc = f"{host_for_netloc}:{port}"
    path = (parsed.path or "").rstrip("/")
    query = "&".join(
        component for component in parsed.query.split("&")
        if component and not _tracking_query_component(component)
    )
    if official_mtf:
        document = _MTF_DOCUMENT_PATH.fullmatch(path or "/")
        if document:
            document_url = f"https://{host}/document/{document.group(1)}"
            return False, f"{document_url}?{query}" if query else document_url
    canonical = urlunsplit((scheme, netloc, path, query, ""))
    return host in {"t.co", "www.t.co"}, canonical


def _choose_canonical_url(candidates: list[str]) -> str:
    """Choose one URL conservatively, preferring an available original."""
    valid = list(dict.fromkeys(
        normalised for candidate in candidates
        if (row := _normalise_one_url(candidate)) is not None
        for is_redirect, normalised in (row,)
        if not is_redirect
    ))
    if valid:
        return valid[0] if len(valid) == 1 else ""
    redirects = list(dict.fromkeys(
        normalised for candidate in candidates
        if (row := _normalise_one_url(candidate)) is not None
        for is_redirect, normalised in (row,)
        if is_redirect
    ))
    return redirects[0] if len(redirects) == 1 else ""


def canonical_source_url(value: Any) -> str:
    """Return one unambiguous safe public URL or an empty string."""
    return _choose_canonical_url(_url_candidates(value))


def _is_official_mtf_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    return bool(
        (parsed.hostname or "").casefold() in _MTF_HOSTS
        and (
            port is None
            or (parsed.scheme.casefold() == "http" and port == 80)
            or (parsed.scheme.casefold() == "https" and port == 443)
        )
    )


def _source_url_candidates(source: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for field in (
        "public_url", "source_url", "url", "canonical_url", "stable_locator",
    ):
        candidates.extend(
            normalised for raw in _url_candidates(source.get(field))
            if (row := _normalise_one_url(raw)) is not None
            for _is_redirect, normalised in (row,)
        )
    for container_name in ("metadata", "evidence_metadata", "provenance"):
        for key_path, value in _metadata_identity_scalars(source.get(container_name)):
            if "url" not in key_path and "locator" not in key_path:
                continue
            candidates.extend(
                normalised for raw in _url_candidates(value)
                if (row := _normalise_one_url(raw)) is not None
                for _is_redirect, normalised in (row,)
            )
    unique = list(dict.fromkeys(candidates))
    return [
        url for url in unique
        if (urlsplit(url).hostname or "").casefold() not in {"t.co", "www.t.co"}
    ] + [
        url for url in unique
        if (urlsplit(url).hostname or "").casefold() in {"t.co", "www.t.co"}
    ]


def _metadata_leaf_scalars(value: Any) -> list[tuple[str, str]]:
    """Return exact leaf keys and scalar values from nested metadata."""
    scalars: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            leaf = _normalise_identity_component(key)
            if isinstance(item, (dict, list)):
                scalars.extend(_metadata_leaf_scalars(item))
            elif isinstance(item, (str, int)) and _clean(item):
                scalars.append((leaf, _clean(item)))
    elif isinstance(value, list):
        for item in value:
            scalars.extend(_metadata_leaf_scalars(item))
    return scalars


def _structured_values(source: dict[str, Any], fields: set[str]) -> list[tuple[str, str]]:
    values = [
        (field, _clean(source.get(field))) for field in sorted(fields)
        if _clean(source.get(field))
    ]
    for container_name in ("metadata", "evidence_metadata", "provenance"):
        values.extend(
            (key, value) for key, value in _metadata_leaf_scalars(
                source.get(container_name)
            ) if key in fields
        )
    return list(dict.fromkeys(values))


def _archive_reference_components(value: Any) -> set[str]:
    components: set[str] = set()
    for repository, locator, marker, number in _ARCHIVE_REFERENCE.findall(_clean(value)):
        component = (
            f"{repository.casefold()}_"
            f"{_normalise_identity_component(locator)}"
        )
        if marker and number:
            kind = "f" if marker.casefold().startswith("f") else "p"
            component += f"_{kind}{int(number)}"
        components.add(component)
    return components


def _normalise_locator_value(kind: str, value: Any) -> str:
    """Normalise labelled bibliographic locator values by semantic kind."""
    text = _clean(value).strip(" ,;:()[]{}")
    patterns = {
        "page": r"^(?:(?:p(?:age)?s?|pp)\.?\s*)?(.+)$",
        "volume": r"^(?:(?:vol(?:ume)?)\.?\s*)?(.+)$",
        "chapter": r"^(?:(?:ch(?:apter)?)\.?\s*)?(.+)$",
        "edition": r"^(?:(?:ed(?:ition)?)\.?\s*)?(.+?)(?:\s+edition)?$",
    }
    pattern = patterns.get(kind)
    if pattern and (match := re.fullmatch(pattern, text, re.I)):
        text = match.group(1)
    return _normalise_identity_component(text)


def _normalise_explicit_identifier(kind: str, value: Any) -> str:
    text = _clean(value)
    if kind == "document":
        if re.fullmatch(r"\d{5,9}", text):
            return text
        matches = _DOCUMENT_TEXT.findall(text)
        if len(matches) == 1 and _DOCUMENT_TEXT.fullmatch(text.strip(" ,;:()")):
            return matches[0]
    if kind == "archive":
        references = _archive_reference_components(text)
        if len(references) == 1:
            return next(iter(references))
    if kind in _SUBORDINATE_IDENTIFIER_KINDS:
        return _normalise_locator_value(kind, text)
    return _normalise_identity_component(text)


def _explicit_identifier_items(source: dict[str, Any]) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for field, value in _structured_values(source, set(_EXPLICIT_IDENTIFIER_FIELDS)):
        identifier = _normalise_explicit_identifier(
            _IDENTIFIER_FIELD_KINDS[field], value
        )
        if identifier:
            items.append((_IDENTIFIER_FIELD_KINDS[field], identifier))
    return list(dict.fromkeys(items))


def _locator_identity_components(source: dict[str, Any]) -> list[str]:
    components: list[str] = []
    for field, value in _structured_values(source, set(_LOCATOR_FIELDS)):
        archive_references = _archive_reference_components(value)
        normalised = (
            "_".join(sorted(archive_references))
            if archive_references else _normalise_locator_value(field, value)
        )
        if normalised:
            components.append(f"{field}:{normalised}")
    return list(dict.fromkeys(components))


def _repository_values(source: dict[str, Any]) -> list[str]:
    return [
        _normalise_identity_component(value)
        for _field, value in _structured_values(
            source, {"publisher", "archive", "repository"}
        )
        if _normalise_identity_component(value)
    ]


def _direct_source_urls(source: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for field in ("public_url", "canonical_url", "source_url", "url"):
        urls.extend(
            normalised for raw in _url_candidates(source.get(field))
            if (row := _normalise_one_url(raw)) is not None
            for _is_redirect, normalised in (row,)
        )
    return list(dict.fromkeys(urls))


def _official_mtf_document_numbers_in_text(value: Any) -> set[str]:
    """Extract only document paths whose hostname is exactly the MTF host."""
    numbers: set[str] = set()
    text = _clean(value)
    for raw in _url_candidates(value):
        row = _normalise_one_url(raw)
        if row is None:
            continue
        _is_redirect, url = row
        if not _is_official_mtf_url(url):
            continue
        if match := _MTF_DOCUMENT_PATH.fullmatch(urlsplit(url).path):
            numbers.add(match.group(1))
    if "://" not in text:
        numbers.update(_MTF_TEXT_DOCUMENT_PATH.findall(text))
    return numbers


def _mtf_title_claim(source: dict[str, Any]) -> bool:
    for field in ("public_title", "source_title", "title"):
        title = _clean(source.get(field))
        if not title:
            continue
        if _official_mtf_document_numbers_in_text(title):
            return True
        if re.match(
            r"^(?:margaret\s+thatcher\s+foundation(?:\s+archive)?|"
            r"(?:www\.)?margaretthatcher\.org)(?:\b|\s*[,;:—-])",
            title,
            re.I,
        ):
            return True
        if _DOCUMENT_TEXT.fullmatch(title.strip(" ,;:()")):
            return True
    return False


def _is_mtf_source(source: dict[str, Any]) -> bool:
    """Use exact authoritative signals, never a domain/title substring."""
    repositories = _repository_values(source)
    direct_urls = _direct_source_urls(source)
    if repositories and any(
        repository not in _MTF_REPOSITORY_IDENTITIES
        for repository in repositories
    ):
        return False
    if direct_urls and any(
        not _is_official_mtf_url(url)
        for url in direct_urls
    ):
        return False
    if repositories or direct_urls:
        return bool(
            any(item in _MTF_REPOSITORY_IDENTITIES for item in repositories)
            or any(_is_official_mtf_url(url) for url in direct_urls)
        )
    metadata_urls = _source_url_candidates(source)
    if metadata_urls:
        return all(_is_official_mtf_url(url) for url in metadata_urls)
    if _mtf_title_claim(source):
        return True
    return any(
        _mtf_title_claim({"title": locator})
        for _field, locator in _structured_values(source, {"stable_locator"})
    )


def _mtf_document_numbers(source: dict[str, Any]) -> set[str]:
    numbers: set[str] = set()
    if not _is_mtf_source(source):
        return numbers
    for url in _source_url_candidates(source):
        parsed = urlsplit(url)
        if _is_official_mtf_url(url):
            match = _MTF_DOCUMENT_PATH.fullmatch(parsed.path)
            if match:
                numbers.add(match.group(1))
    for kind, identifier in _explicit_identifier_items(source):
        if kind == "document" and re.fullmatch(r"\d{5,9}", identifier):
            numbers.add(identifier)
    for field, value in _structured_values(source, {"stable_locator"}):
        del field
        if re.fullmatch(r"\d{5,9}", value):
            numbers.add(value)
        if _DOCUMENT_TEXT.fullmatch(value.strip(" ,;:()")):
            numbers.update(_DOCUMENT_TEXT.findall(value))
        numbers.update(_official_mtf_document_numbers_in_text(value))
        if _mtf_title_claim({"title": value}):
            numbers.update(_DOCUMENT_TEXT.findall(value))
    for field in ("public_title", "source_title", "title"):
        title = _clean(source.get(field))
        if not title:
            continue
        if _mtf_title_claim({field: title}):
            numbers.update(_DOCUMENT_TEXT.findall(title))
            numbers.update(_official_mtf_document_numbers_in_text(title))
    for container_name in ("metadata", "evidence_metadata", "provenance"):
        for key, title in _metadata_leaf_scalars(source.get(container_name)):
            if key in {"public_title", "source_title", "title"} and _mtf_title_claim({
                "title": title,
            }):
                numbers.update(_DOCUMENT_TEXT.findall(title))
                numbers.update(_official_mtf_document_numbers_in_text(title))
    return numbers


def _publisher_identity(source: dict[str, Any], urls: list[str]) -> str:
    if _is_mtf_source(source):
        return "margaret_thatcher_foundation"
    for field in ("publisher", "archive", "repository"):
        value = _normalise_identity_component(source.get(field))
        if value:
            return value
    if urls:
        return (urlsplit(urls[0]).hostname or "unknown").casefold().removeprefix("www.")
    title = _clean(source.get("public_title") or source.get("source_title") or source.get("title"))
    title_lower = title.casefold()
    if "hansard" in title_lower:
        return "hansard"
    if "uk parliament" in title_lower or "parliament.uk" in title_lower:
        return "uk_parliament"
    if "reagan presidential library" in title_lower:
        return "reagan_presidential_library"
    if "theguardian.com" in title_lower or "guardian.com" in title_lower or re.search(
        r"\b(?:the\s+)?guardian\b", title_lower
    ):
        return "the_guardian"
    if "bbc." in title_lower or re.search(r"\bbbc\b", title_lower):
        return "bbc"
    if "nytimes.com" in title_lower or "new york times" in title_lower:
        return "new_york_times"
    if "washingtonpost.com" in title_lower or "washington post" in title_lower:
        return "washington_post"
    if "itv news" in title_lower and "the independent" in title_lower:
        return "itv_news_and_the_independent"
    if "independent.co.uk" in title_lower or "the independent" in title_lower:
        return "the_independent"
    if _ARCHIVE_REFERENCE.search(title):
        return "margaret_thatcher_archive"
    return "unknown"


def _has_authoritative_publisher_scope(
    source: dict[str, Any], canonical_url: str,
) -> bool:
    """Exclude publisher names inferred only from incidental title text."""
    if _repository_values(source) or _is_mtf_source(source):
        return True
    if canonical_url and (urlsplit(canonical_url).hostname or "").casefold() not in {
        "t.co", "www.t.co",
    }:
        return True
    return any(
        _archive_reference_components(value)
        for value in _identity_values(source)
    )


def _bibliographic_evidence_class(source: dict[str, Any]) -> str:
    source_type = _normalise_identity_component(source.get("source_type"))
    if source_type == "canonical_stable_locator":
        return "non_recollection"
    if any(marker in source_type for marker in (
        "memoir", "recollection", "autobiograph",
    )):
        return "secondary_recollection"
    return "non_recollection"


def _source_is_composite_locator(source: dict[str, Any]) -> bool:
    text = " ".join(_identity_values(source)).casefold()
    return _is_mtf_source(source) and "hansard" in text


def _validated_date(year: int, month: int, day: int) -> str | None:
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return None


def _canonicalise_dates_in_text(value: Any) -> str:
    text = _clean(value)

    def iso(match: re.Match[str]) -> str:
        date = _validated_date(*(int(item) for item in match.groups()))
        return date or match.group(0)

    def dmy(match: re.Match[str]) -> str:
        date = _validated_date(
            int(match.group(3)), _MONTH_NUMBERS[match.group(2).casefold()],
            int(match.group(1)),
        )
        return date or match.group(0)

    def mdy(match: re.Match[str]) -> str:
        date = _validated_date(
            int(match.group(3)), _MONTH_NUMBERS[match.group(1).casefold()],
            int(match.group(2)),
        )
        return date or match.group(0)

    def ymd_text(match: re.Match[str]) -> str:
        date = _validated_date(
            int(match.group(1)), _MONTH_NUMBERS[match.group(2).casefold()],
            int(match.group(3)),
        )
        return date or match.group(0)

    def year_month(match: re.Match[str]) -> str:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}"

    def month_year(match: re.Match[str]) -> str:
        return (
            f"{int(match.group(2)):04d}-"
            f"{_MONTH_NUMBERS[match.group(1).casefold()]:02d}"
        )

    text = _DATE_ISO.sub(iso, text)
    text = _DATE_DMY.sub(dmy, text)
    text = _DATE_MDY.sub(mdy, text)
    text = _DATE_YMD_TEXT.sub(ymd_text, text)
    text = _DATE_YEAR_MONTH.sub(year_month, text)
    return _DATE_MONTH_YEAR.sub(month_year, text)


def _date_identities(value: Any) -> set[str]:
    text = _clean(value)
    dates: set[str] = set()
    for year, month, day in _DATE_ISO.findall(text):
        if date := _validated_date(int(year), int(month), int(day)):
            dates.add(date)
    for day, month, year in _DATE_DMY.findall(text):
        if date := _validated_date(
            int(year), _MONTH_NUMBERS[month.casefold()], int(day)
        ):
            dates.add(date)
    for month, day, year in _DATE_MDY.findall(text):
        if date := _validated_date(
            int(year), _MONTH_NUMBERS[month.casefold()], int(day)
        ):
            dates.add(date)
    for year, month, day in _DATE_YMD_TEXT.findall(text):
        if date := _validated_date(
            int(year), _MONTH_NUMBERS[month.casefold()], int(day)
        ):
            dates.add(date)
    if dates:
        return dates
    month_dates = {
        f"{int(year):04d}-{int(month):02d}"
        for year, month in _DATE_YEAR_MONTH.findall(text)
    }
    month_dates.update(
        f"{int(year):04d}-{_MONTH_NUMBERS[month.casefold()]:02d}"
        for month, year in _DATE_MONTH_YEAR.findall(text)
    )
    if month_dates:
        return month_dates
    return set(re.findall(r"\b(?:18|19|20)\d{2}\b", text))


def _source_date_identities(source: dict[str, Any]) -> set[str]:
    return set().union(*(
        _date_identities(source.get(field)) for field in (
            "source_date", "date", "public_title", "source_title", "title",
            "stable_locator",
        )
    ))


def _dates_compatible(left: set[str], right: set[str]) -> bool:
    return any(
        one == two or one.startswith(f"{two}-") or two.startswith(f"{one}-")
        for one in left for two in right
    )


def _exact_hansard_hc_locator_date(value: Any) -> str:
    """Return the date from the canonical HC Deb locator form only."""
    match = _HANSARD_HC_STABLE_LOCATOR.fullmatch(_clean(value))
    if not match:
        return ""
    return _validated_date(
        int(match.group("year")),
        _MONTH_NUMBERS[match.group("month").casefold()],
        int(match.group("day")),
    ) or ""


def _authoritative_hansard_commons_url_date(value: Any) -> str:
    """Return the date from one exact official Commons Hansard URL."""
    url = canonical_source_url(value)
    if not url:
        return ""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return ""
    if parsed.scheme.casefold() != "https" or port not in {None, 443}:
        return ""
    host = (parsed.hostname or "").casefold()
    pattern = {
        "publications.parliament.uk": _HANSARD_PUBLICATIONS_COMMONS_PATH,
        "hansard.parliament.uk": _HANSARD_MODERN_COMMONS_PATH,
    }.get(host)
    if pattern is None or not (match := pattern.fullmatch(parsed.path)):
        return ""
    year, month, day = (int(item) for item in match.group("date").split("-"))
    return _validated_date(year, month, day) or ""


def _only_compatible_dates(reference: str, values: list[str] | set[str]) -> bool:
    dates = set(values)
    return bool(dates) and all(
        _dates_compatible({reference}, {candidate}) for candidate in dates
    )


def _bridge_hansard_locator_to_authoritative_url(
    packet: dict[str, Any], records: list[dict[str, Any]],
) -> None:
    """Join one exact virtual HC Deb locator to one official transcript URL.

    This packet-level bridge deliberately requires complementary strong-primary
    evidence, an exact canonical virtual locator, one official Commons URL, and
    compatible dates throughout.  It changes transient public identities only;
    the attached internal evidence records and their roles remain untouched.
    """
    locator = _clean(packet.get("stable_locator"))
    locator_date = _exact_hansard_hc_locator_date(locator)
    source_event = _clean(packet.get("source_event"))
    packet_dates = _source_date_identities(packet)
    if (
        not locator_date
        or not re.fullmatch(r"House of Commons Debate(?:\s*:\s*.+)?", source_event, re.I)
        or not _only_compatible_dates(locator_date, packet_dates)
    ):
        return

    by_identity: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_identity.setdefault(record["canonical_identity"], []).append(record)
    locator_groups = [
        members for members in by_identity.values()
        if sum(
            member["_row"].get("virtual_locator_record") is True
            and member["source_type"].casefold() == "canonical_stable_locator"
            and _clean(member["title"]).casefold() == locator.casefold()
            for member in members
        ) == 1
    ]
    if len(locator_groups) != 1:
        return
    locator_members = locator_groups[0]
    locator_claims = {
        claim for member in locator_members for claim in member["claims_supported"]
    }
    locator_roles = {role for member in locator_members for role in member["roles"]}
    if (
        not {"attribution", "source_event", "date"}.issubset(locator_claims)
        or "source_event_support" not in locator_roles
        or any(
            member["identity_basis"] != "bibliographic_identity"
            or member["publisher"] != "hansard"
            or member["canonical_url"]
            or member["authoritative_urls"]
            or member["source_quality_class"] != "strong_primary_evidence"
            or member["secondary_recollection"]
            or member["composite_locator"]
            or member["document_numbers"]
            or member["explicit_identifiers"]
            or _clean(member["title"]).casefold() != locator.casefold()
            or not _only_compatible_dates(locator_date, member["date_identities"])
            for member in locator_members
        )
    ):
        return

    official = [
        (record, url, date)
        for record in records
        for url in record["authoritative_urls"]
        if (date := _authoritative_hansard_commons_url_date(url))
    ]
    if len(official) != 1:
        return
    url_record, canonical_url, url_date = official[0]
    if (
        url_date != locator_date
        or url_record in locator_members
        or url_record["authoritative_urls"] != [canonical_url]
        or url_record["canonical_url"] != canonical_url
        or url_record["canonical_identity"] != f"url:{canonical_url}"
        or url_record["identity_basis"] != "canonical_url"
        or url_record["source_quality_class"] != "strong_primary_evidence"
        or url_record["secondary_recollection"]
        or url_record["composite_locator"]
        or url_record["document_numbers"]
        or url_record["explicit_identifiers"]
        or not {"wording", "attribution"}.issubset(url_record["claims_supported"])
        or not {"wording_verification", "attribution_support"}.issubset(
            url_record["roles"]
        )
        or not _only_compatible_dates(locator_date, url_record["date_identities"])
    ):
        return

    identity = f"url:{canonical_url}"
    for member in [*locator_members, url_record]:
        member["publisher"] = "uk_parliament_hansard"
    for member in locator_members:
        member["canonical_identity"] = identity
        member["identity_basis"] = "corroborated_hansard_locator_url"
        member["canonical_url"] = canonical_url


def _canonical_source_details(
    source: dict[str, Any], *, fallback_token: str = "",
) -> dict[str, Any]:
    urls = _source_url_candidates(source)
    numbers = _mtf_document_numbers(source)
    source_id = _clean(source.get("source_id")) or fallback_token
    publisher = _publisher_identity(source, urls)
    canonical_url = _choose_canonical_url(urls)

    def details(
        identity: str, basis: str, *, url: str = canonical_url,
        document_number: str | None = None,
        document_numbers: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "canonical_identity": identity,
            "identity_basis": basis,
            "canonical_url": url,
            "document_number": document_number,
            "document_numbers": (
                sorted(numbers) if document_numbers is None else document_numbers
            ),
            "publisher": publisher,
        }

    if len(numbers) > 1:
        key = f"identity_conflict:{source_id or sha256_bytes(canonical_json(source))}"
        return details(
            key, "conflicting_document_identifiers", url="",
            document_numbers=sorted(numbers),
        )
    if _source_is_composite_locator(source):
        key = f"composite_locator:{source_id or sha256_bytes(canonical_json(source))}"
        return details(key, "composite_locator", url="")
    if numbers:
        number = next(iter(numbers))
        return details(
            f"margaret_thatcher_foundation:document:{number}",
            "margaret_thatcher_foundation_document",
            url=f"https://www.margaretthatcher.org/document/{number}",
            document_number=number,
            document_numbers=[number],
        )

    identifier_items = _explicit_identifier_items(source)
    for _field, locator in _structured_values(source, {"stable_locator"}):
        if _DOCUMENT_TEXT.fullmatch(locator.strip(" ,;:()")):
            identifier_items.append(("document", _DOCUMENT_TEXT.findall(locator)[0]))
    identifiers_by_kind: dict[str, set[str]] = {}
    for kind, identifier in identifier_items:
        identifiers_by_kind.setdefault(kind, set()).add(identifier)
    locator_components = _locator_identity_components(source)
    if identifiers_by_kind:
        primary_kind = next(
            kind for kind in _PRIMARY_IDENTIFIER_KINDS
            if kind in identifiers_by_kind
        )
        typed_locators: dict[str, set[str]] = {}
        other_locators: list[str] = []
        for component in locator_components:
            kind, separator, value = component.partition(":")
            if separator and kind in _SUBORDINATE_IDENTIFIER_KINDS:
                typed_locators.setdefault(kind, set()).add(value)
            else:
                other_locators.append(component)
        relevant_values = {
            primary_kind: set(identifiers_by_kind[primary_kind]),
        }
        for kind in _SUBORDINATE_IDENTIFIER_KINDS:
            values = set(typed_locators.get(kind, set()))
            if kind in identifiers_by_kind:
                values.update(identifiers_by_kind[kind])
            if values:
                relevant_values[kind] = values
        if any(len(values) > 1 for values in relevant_values.values()):
            key = f"identity_conflict:{source_id or sha256_bytes(canonical_json(source))}"
            return details(key, "conflicting_repository_identifiers", url="")
        record_fingerprint = sha256_bytes(canonical_json(source))
        if (
            publisher == "unknown"
            or not _has_authoritative_publisher_scope(source, canonical_url)
        ):
            key = f"unscoped_identifier:{source_id or 'record'}:{record_fingerprint}"
            return details(key, "unscoped_repository_identifier", url="")
        primary_value = next(iter(relevant_values[primary_kind]))
        identity_components = [f"{primary_kind}:{primary_value}"]
        for kind in _SUBORDINATE_IDENTIFIER_KINDS:
            if kind == primary_kind or kind not in relevant_values:
                continue
            identity_components.append(
                f"{kind}:{next(iter(relevant_values[kind]))}"
            )
        if primary_kind == "archive":
            other_locators = [
                item for item in other_locators
                if not item.startswith("stable_locator:")
            ]
        identity_components.extend(sorted(other_locators))
        return details(
            f"{publisher}:{':'.join(identity_components)}",
            "explicit_repository_identifier",
        )

    archive_values = [
        value for _field, value in _structured_values(source, {"stable_locator"})
    ]
    if publisher in {
        "margaret_thatcher_archive", "margaret_thatcher_foundation",
    }:
        archive_values.extend(
            _clean(source.get(field)) for field in (
                "public_title", "source_title", "title",
            ) if _clean(source.get(field))
        )
    archive_references = set().union(*(
        _archive_reference_components(value) for value in archive_values
    )) if archive_values else set()
    archive_references = {
        reference for reference in archive_references
        if not any(
            other != reference and other.startswith(f"{reference}_")
            for other in archive_references
        )
    }
    if len(archive_references) > 1:
        key = f"identity_conflict:{source_id or sha256_bytes(canonical_json(source))}"
        return details(
            key, "conflicting_archive_references", url="", document_numbers=[]
        )
    if len(archive_references) == 1:
        identifier = next(iter(archive_references))
        archive_locator_components = [
            item for item in locator_components
            if not item.startswith("stable_locator:")
        ]
        locator_key = "".join(
            f":{item}" for item in sorted(archive_locator_components)
        )
        return details(
            f"{publisher}:record:{identifier}{locator_key}",
            "explicit_archive_reference",
        )

    original_urls = [
        url for url in urls
        if (urlsplit(url).hostname or "").casefold() not in {"t.co", "www.t.co"}
    ]
    if len(original_urls) > 1:
        key = f"identity_conflict:{source_id or sha256_bytes(canonical_json(source))}"
        return details(key, "conflicting_authoritative_urls", url="")
    redirect_urls = [
        url for url in urls
        if (urlsplit(url).hostname or "").casefold() in {"t.co", "www.t.co"}
    ]
    if not original_urls and len(set(redirect_urls)) > 1:
        key = f"identity_conflict:{source_id or sha256_bytes(canonical_json(source))}"
        return details(key, "conflicting_redirect_urls", url="")
    if canonical_url:
        return details(f"url:{canonical_url}", "canonical_url", url=canonical_url)

    title = _clean(source.get("public_title") or source.get("source_title") or source.get("title"))
    date = _clean(source.get("source_date") or source.get("date"))
    if not date:
        date = ",".join(sorted(_date_identities(title)))
    normalised_date = _normalise_identity_component(
        _canonicalise_dates_in_text(date)
    )
    bibliographic_locator = ":".join(sorted(locator_components))
    if publisher != "unknown" and title and (bibliographic_locator or date):
        fingerprint = sha256_bytes(canonical_json({
            "publisher": publisher,
            "title": _normalise_identity_component(
                _canonicalise_dates_in_text(title)
            ),
            "date": normalised_date,
            "locator": bibliographic_locator,
            "evidence_class": _bibliographic_evidence_class(source),
        }))
        return details(
            f"bibliographic:{publisher}:{fingerprint}",
            "bibliographic_identity", url="",
        )
    fallback_fingerprint = sha256_bytes(canonical_json({
        "title": title,
        "source_type": _clean(source.get("source_type")),
        "quality": _clean(source.get("source_quality_class")),
        "identifiers": sorted(identifier_items),
        "locators": sorted(locator_components),
        "urls": urls,
    }))
    fallback = f"{source_id or 'record'}:{fallback_fingerprint}"
    return details(f"source_record:{fallback}", "distinct_source_record", url="")


def canonical_source_identity(source: dict[str, Any]) -> str:
    """Return the strongest deterministic identity available for one source."""
    return _canonical_source_details(source)["canonical_identity"]


def _canonical_subject(value: Any) -> str:
    text = _clean(value).casefold()
    text = _MARKDOWN_LINK.sub(lambda match: match.group(1), text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"margaret\s+thatcher\s+foundation(?:\s+archive)?", " ", text)
    text = _DOCUMENT_TEXT.sub(" ", text)
    months = (
        "january|february|march|april|may|june|july|august|september|"
        "october|november|december"
    )
    text = re.sub(rf"\b(?:{months})\s+\d{{1,2}}(?:st|nd|rd|th)?[,]?\s+\d{{4}}\b", " ", text)
    text = re.sub(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{months})\s+\d{{4}}\b", " ", text)
    text = re.sub(r"\b(?:18|19|20)\d{2}\b", " ", text)
    return " ".join(re.findall(r"[a-z0-9]+", text))


def _provenance_identity(source: dict[str, Any]) -> tuple[str, str]:
    provenance = source.get("provenance")
    if not isinstance(provenance, dict):
        return "", ""
    raw_sha = _clean(provenance.get("raw_response_sha256"))
    raw_path = _clean(provenance.get("raw_response_path"))
    return (
        raw_sha if re.fullmatch(r"[0-9a-f]{64}", raw_sha) else "",
        raw_path,
    )


def _provenance_matches(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_sha, left_path = _provenance_identity(left)
    right_sha, right_path = _provenance_identity(right)
    if left_sha or right_sha:
        return bool(left_sha and right_sha and left_sha == right_sha)
    return bool(left_path and right_path and left_path == right_path)


def _public_roles(row: dict[str, Any]) -> list[str]:
    return sorted(
        role for role in row.get("assigned_roles", [])
        if role not in _DISPLAY_ROLE_EXCLUSIONS
    )


def _plain_public_title(value: Any, document_number: str | None) -> str:
    title = _clean(value)
    title = _MARKDOWN_LINK.sub(lambda match: match.group(1), title)
    title = re.sub(
        r"\(\s*https?://[^()\s]+(?:\([^()\s]*\)[^()\s]*)*\s*\)",
        "",
        title,
        flags=re.I,
    )
    title = re.sub(r"https?://\S+", "", title)
    title = re.sub(r"\(\s*\)|\[\s*\]|\{\s*\}", "", title)
    title = title.strip(" ,;:-")
    title = re.sub(
        r"Margaret\s+Thatcher\s+Foundation\s+Archive",
        "Margaret Thatcher Foundation",
        title,
        flags=re.I,
    )
    title = re.sub(
        r"\s*\|\s*([^|]+?)\s*\|\s*\1\s*$",
        lambda match: f" | {match.group(1).strip()}",
        title,
        flags=re.I,
    )
    foundation_page_title = re.fullmatch(
        r"(.+?)\s*\|\s*Margaret Thatcher Foundation", title, re.I,
    )
    if foundation_page_title:
        title = f"Margaret Thatcher Foundation, {foundation_page_title.group(1).strip()}"

    url_like_title = re.fullmatch(
        r"((?:www\.)?[a-z0-9.-]+\.[a-z]{2,})(/\S*)?", title, re.I,
    )
    if url_like_title:
        host = url_like_title.group(1).casefold().removeprefix("www.")
        path = url_like_title.group(2) or ""
        if document_number and host == "margaretthatcher.org" and re.fullmatch(
            rf"/document/{re.escape(document_number)}/?", path, re.I,
        ):
            title = f"Margaret Thatcher Foundation, Document {document_number}"
        elif host in {"hansard.parliament.uk", "publications.parliament.uk"}:
            parts = ["UK Parliament Hansard"]
            if "/commons/" in path.casefold() or "cmhansrd" in path.casefold():
                parts.append("House of Commons")
            if date_match := re.search(
                r"/((?:18|19|20)\d{2})-(\d{2})-(\d{2})(?:/|$)", path,
            ):
                year, month, day = (int(item) for item in date_match.groups())
                if _validated_date(year, month, day):
                    parts.append(
                        f"{day} {_PUBLIC_MONTH_NAMES[month]} {year}"
                    )
            topic = path.rstrip("/").rsplit("/", 1)[-1]
            topic = re.sub(r"\.html?$", "", topic, flags=re.I)
            if topic and not re.fullmatch(r"[0-9a-f-]{24,}", topic, re.I):
                parts.append(re.sub(r"[-_]+", " ", topic).strip())
            title = ", ".join(parts)

    def expand_abbreviated_date(match: re.Match[str]) -> str:
        month = _MONTH_NUMBERS[match.group(2).casefold()]
        return f"{int(match.group(1))} {_PUBLIC_MONTH_NAMES[month]} {match.group(3)}"

    def normalise_month_first_date(match: re.Match[str]) -> str:
        month = _MONTH_NUMBERS[match.group(1).casefold()]
        day = int(match.group(2))
        year = int(match.group(3))
        if not _validated_date(year, month, day):
            return match.group(0)
        return f"{day} {_PUBLIC_MONTH_NAMES[month]} {year}"

    def normalise_year_first_date(match: re.Match[str]) -> str:
        year = int(match.group(1))
        month = _MONTH_NUMBERS[match.group(2).casefold()]
        day = int(match.group(3))
        if not _validated_date(year, month, day):
            return match.group(0)
        return f"{day} {_PUBLIC_MONTH_NAMES[month]} {year}"

    title = _DATE_MDY.sub(normalise_month_first_date, title)
    title = _DATE_YMD_TEXT.sub(normalise_year_first_date, title)
    title = _DATE_ABBREVIATED_DMY.sub(expand_abbreviated_date, title)
    if document_number:
        if not title:
            return f"Margaret Thatcher Foundation, Document {document_number}"
        if re.fullmatch(
            r"Margaret Thatcher Foundation(?:\s*\((?:MTF|THCR)\))?",
            title,
            re.I,
        ):
            return f"Margaret Thatcher Foundation, Document {document_number}"
        generic = re.fullmatch(
            rf"(?:Margaret Thatcher Foundation(?:\s*\((?:MTF|THCR)\))?|MTF)"
            rf"(?:[,]?\s*doc(?:ument)?"
            rf"(?:\s*(?:id|number|no\.?))?\s*[#:]?\s*{re.escape(document_number)}"
            rf"|\s*\(\s*doc(?:ument)?"
            rf"(?:\s*(?:id|number|no\.?))?\s*[#:]?\s*{re.escape(document_number)}\s*\))",
            title,
            re.I,
        )
        if generic:
            return f"Margaret Thatcher Foundation, Document {document_number}"
        if document_number not in _DOCUMENT_TEXT.findall(title):
            title = f"{title} (Document {document_number})"
    return title or "Historical source"


def _display_title_rank(row: dict[str, Any]) -> tuple[Any, ...]:
    title = _clean(row.get("public_title") or row.get("source_title"))
    claims = set(row.get("claims_supported", []))
    date_text = " ".join(filter(None, (
        _clean(row.get("source_date")), _clean(row.get("date")), title,
    )))
    date_precision = 0
    if "date" in claims:
        months = (
            "january|february|march|april|may|june|july|august|september|"
            "october|november|december"
        )
        if (
            re.search(r"\b(?:18|19|20)\d{2}-\d{2}-\d{2}\b", date_text)
            or re.search(
                rf"\b(?:\d{{1,2}}\s+(?:{months})|(?:{months})\s+\d{{1,2}})[, ]+"
                r"(?:18|19|20)\d{2}\b",
                date_text,
                re.I,
            )
        ):
            date_precision = 3
        elif re.search(rf"\b(?:{months})\s+(?:18|19|20)\d{{2}}\b", date_text, re.I):
            date_precision = 2
        elif re.search(r"\b(?:18|19|20)\d{2}\b", date_text):
            date_precision = 1
    generic_document_title = bool(re.fullmatch(
        r"Margaret Thatcher Foundation[,]?\s*(?:Archive[,]?\s*)?document\s+\d{5,9}",
        title,
        re.I,
    ))
    return (
        "source_event" in claims,
        date_precision,
        not generic_document_title,
        bool(row.get("virtual_locator_record")),
        len(title.split()),
        title.casefold(),
    )


def _source_projection(row: dict[str, Any], *, internal: bool) -> dict[str, Any] | None:
    title = _clean(row.get("public_title") or row.get("source_title"))
    roles = sorted(set(row.get("assigned_roles", []))) if internal else _public_roles(row)
    if not title or not roles:
        return None
    url = canonical_source_url(row.get("public_url") or row.get("source_url"))
    return {
        "title": title,
        "url": url,
        "source_type": row["source_quality_class"],
        "roles": roles,
        "claims_supported": [
            field for field in CLAIM_FIELDS if field in set(row.get("claims_supported", []))
        ],
        "secondary_recollection": "secondary_recollection" in row.get("assigned_roles", []),
    }


def internal_sources(packet: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every renderable evidence record with its internal roles intact."""
    audit = packet.get("_source_role_audit")
    if not isinstance(audit, dict):
        return []
    return [
        projected for row in audit.get("renderable_sources", [])
        if (projected := _source_projection(row, internal=True)) is not None
    ]


def public_source_identity_diagnostics(packet: dict[str, Any]) -> dict[str, Any]:
    """Group renderable records conservatively for public display."""
    audit = packet.get("_source_role_audit")
    if not isinstance(audit, dict):
        return {"records": [], "groups": [], "identity_ambiguities": []}
    rows = [
        row for row in audit.get("renderable_sources", [])
        if _source_projection(row, internal=False) is not None
    ]
    initial_details = [
        _canonical_source_details(row, fallback_token=f"renderable:{index}")
        for index, row in enumerate(rows)
    ]
    explicit_renderable_documents = {
        details["canonical_identity"] for details in initial_details
        if details["identity_basis"] == "margaret_thatcher_foundation_document"
    }
    explicit_document_rows: dict[str, list[dict[str, Any]]] = {}
    for row, details in zip(rows, initial_details):
        if details["identity_basis"] == "margaret_thatcher_foundation_document":
            explicit_document_rows.setdefault(
                details["canonical_identity"], []
            ).append(row)
    lead_hints: dict[str, list[dict[str, Any]]] = {}
    for index, lead in enumerate(audit.get("model_proposed_source_leads", [])):
        details = _canonical_source_details(lead, fallback_token=f"lead:{index}")
        subject = _canonical_subject(lead.get("source_title") or lead.get("public_title"))
        if subject and details["identity_basis"] == "margaret_thatcher_foundation_document":
            identity = details["canonical_identity"]
            lead_hints.setdefault(subject, []).append({
                "identity": identity,
                "lead": lead,
                "corroborated": any(
                    _provenance_matches(lead, explicit)
                    for explicit in explicit_document_rows.get(identity, [])
                ),
            })
    records: list[dict[str, Any]] = []
    ambiguities: list[dict[str, Any]] = []
    if len(explicit_renderable_documents) > 1:
        ambiguities.append({
            "kind": "distinct_explicit_documents_preserved",
            "canonical_identities": sorted(explicit_renderable_documents),
        })
    for index, (row, initial) in enumerate(zip(rows, initial_details)):
        details = initial
        subject = _canonical_subject(row.get("public_title") or row.get("source_title"))
        matching_leads = (
            lead_hints.get(subject, []) if subject and _is_mtf_source(row) else []
        )
        hinted = {item["identity"] for item in matching_leads}
        trusted_rows = [item for item in matching_leads if item["corroborated"]]
        trusted_hints = {item["identity"] for item in trusted_rows}
        if details["identity_basis"] != "margaret_thatcher_foundation_document" and hinted:
            weak_locator = bool(
                details["identity_basis"] in {
                    "bibliographic_identity", "distinct_source_record",
                }
                and not details["canonical_url"]
                and not _source_is_composite_locator(row)
            )
            row_dates = _source_date_identities(row)
            packet_dates = _source_date_identities(packet)
            trusted_lead_dates = set().union(*(
                _source_date_identities(item["lead"]) for item in trusted_rows
            )) if trusted_rows else set()
            date_conflict = bool(
                row_dates and (
                    (packet_dates and not _dates_compatible(row_dates, packet_dates))
                    or (
                        trusted_lead_dates
                        and not _dates_compatible(row_dates, trusted_lead_dates)
                    )
                )
            )
            direct_provenance_link = any(
                _provenance_matches(row, item["lead"])
                and any(
                    _provenance_matches(row, explicit)
                    for explicit in explicit_document_rows.get(item["identity"], [])
                )
                for item in trusted_rows
            )
            row_title = _clean(row.get("public_title") or row.get("source_title"))
            packet_locator = _clean(packet.get("stable_locator"))
            row_has_provenance = any(_provenance_identity(row))
            virtual_locator_link = bool(
                row.get("virtual_locator_record") is True
                and _clean(row.get("source_type")) == "canonical_stable_locator"
                and not row_has_provenance
                and row_title.casefold() == packet_locator.casefold()
                and row_dates
                and packet_dates
                and _dates_compatible(row_dates, packet_dates)
            )
            bridge_allowed = bool(
                weak_locator
                and len(hinted) == 1
                and len(trusted_hints) == 1
                and not date_conflict
                and (direct_provenance_link or virtual_locator_link)
            )
            if bridge_allowed:
                key = next(iter(trusted_hints))
                number = key.rsplit(":", 1)[1]
                details = {
                    **details,
                    "canonical_identity": key,
                    "identity_basis": "corroborated_metadata_locator",
                    "canonical_url": f"https://www.margaretthatcher.org/document/{number}",
                    "document_number": number,
                    "document_numbers": [number],
                    "publisher": "margaret_thatcher_foundation",
                }
            else:
                if _source_is_composite_locator(row):
                    ambiguity_kind = "composite_metadata_locator_preserved"
                elif date_conflict:
                    ambiguity_kind = "metadata_locator_date_conflict"
                elif not weak_locator:
                    ambiguity_kind = "explicit_identity_metadata_locator_preserved"
                elif len(hinted) > 1:
                    ambiguity_kind = "ambiguous_metadata_locator"
                elif hinted.isdisjoint(explicit_renderable_documents) and explicit_renderable_documents:
                    ambiguity_kind = "metadata_locator_document_conflict"
                elif len(trusted_hints) == 1:
                    ambiguity_kind = "metadata_locator_row_link_unproven"
                else:
                    ambiguity_kind = "uncorroborated_metadata_locator_provenance"
                ambiguities.append({
                    "kind": ambiguity_kind,
                    "source_id": _clean(row.get("source_id")) or f"renderable:{index}",
                    "subject": subject,
                    "hinted_canonical_identities": sorted(hinted),
                    "provenance_corroborated_identities": sorted(trusted_hints),
                    "renderable_canonical_identities": sorted(explicit_renderable_documents),
                    "row_dates": sorted(row_dates),
                    "lead_dates": sorted(trusted_lead_dates),
                    "packet_dates": sorted(packet_dates),
                })
        authoritative_urls = _source_url_candidates(row)
        record = {
            **details,
            "source_id": _clean(row.get("source_id")) or f"renderable:{index}",
            "title": _clean(row.get("public_title") or row.get("source_title")),
            "url": _clean(row.get("public_url") or row.get("source_url")),
            "roles": _public_roles(row),
            "claims_supported": [
                field for field in CLAIM_FIELDS if field in set(row.get("claims_supported", []))
            ],
            "source_quality_class": row.get("source_quality_class"),
            "secondary_recollection": "secondary_recollection" in row.get("assigned_roles", []),
            "source_type": _clean(row.get("source_type")),
            "authoritative_urls": authoritative_urls,
            "locator_components": _locator_identity_components(row),
            "date_identities": sorted(_source_date_identities(row)),
            "explicit_identifiers": [
                f"{kind}:{identifier}"
                for kind, identifier in _explicit_identifier_items(row)
            ],
            "composite_locator": _source_is_composite_locator(row),
            "_row": row,
        }
        records.append(record)
        if details["identity_basis"] in {
            "conflicting_document_identifiers", "conflicting_archive_references",
            "conflicting_repository_identifiers",
            "conflicting_authoritative_urls", "conflicting_redirect_urls",
            "unscoped_repository_identifier",
            "composite_locator",
        } or _source_is_composite_locator(row):
            ambiguities.append({
                "kind": "composite_or_conflicting_locator_preserved",
                "source_id": record["source_id"],
                "canonical_identity": record["canonical_identity"],
                "document_numbers": record["document_numbers"],
            })
        original_urls = [
            url for url in authoritative_urls
            if (urlsplit(url).hostname or "").casefold() not in {"t.co", "www.t.co"}
        ]
        if len(original_urls) > 1:
            ambiguities.append({
                "kind": "multiple_authoritative_urls_preserved",
                "source_id": record["source_id"],
                "canonical_urls": original_urls,
            })
        redirect_urls = [
            url for url in authoritative_urls
            if (urlsplit(url).hostname or "").casefold() in {"t.co", "www.t.co"}
        ]
        if not original_urls and len(set(redirect_urls)) > 1:
            ambiguities.append({
                "kind": "multiple_redirect_urls_preserved",
                "source_id": record["source_id"],
                "canonical_urls": redirect_urls,
            })
    _bridge_hansard_locator_to_authoritative_url(packet, records)
    records_by_url: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if record["canonical_url"]:
            records_by_url.setdefault(record["canonical_url"], []).append(record)
    explicit_bases = {
        "margaret_thatcher_foundation_document",
        "explicit_repository_identifier",
        "explicit_archive_reference",
        "corroborated_metadata_locator",
    }
    for canonical_url, url_records in records_by_url.items():
        identities = {record["canonical_identity"] for record in url_records}
        if len(identities) < 2:
            continue
        strong_identities = {
            record["canonical_identity"] for record in url_records
            if record["identity_basis"] in explicit_bases
        }
        aliases = [
            record for record in url_records
            if record["identity_basis"] == "canonical_url"
            and not _source_is_composite_locator(record["_row"])
        ]
        if len(strong_identities) == 1 and len(aliases) == len(url_records) - sum(
            record["canonical_identity"] in strong_identities for record in url_records
        ):
            target = next(iter(strong_identities))
            for alias in aliases:
                alias["canonical_identity"] = target
                alias["identity_basis"] = "canonical_url_alias_to_explicit_identifier"
        else:
            ambiguities.append({
                "kind": "canonical_url_identifier_conflict",
                "canonical_url": canonical_url,
                "canonical_identities": sorted(identities),
                "source_ids": sorted(record["source_id"] for record in url_records),
            })
    groups_by_key: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        key = record["canonical_identity"]
        groups_by_key.setdefault(key, []).append(record)
    groups: list[dict[str, Any]] = []
    for key, members in groups_by_key.items():
        selected = max(members, key=lambda item: _display_title_rank(item["_row"]))
        document_number = next(
            (item["document_number"] for item in members if item["document_number"]),
            None,
        )
        canonical_urls = list(dict.fromkeys(
            item["canonical_url"] for item in members if item["canonical_url"]
        ))
        canonical_url = (
            f"https://www.margaretthatcher.org/document/{document_number}"
            if document_number else _choose_canonical_url(canonical_urls)
        )
        original_group_urls = [
            url for url in canonical_urls
            if (urlsplit(url).hostname or "").casefold() not in {"t.co", "www.t.co"}
        ]
        if not document_number and len(original_group_urls) > 1:
            ambiguities.append({
                "kind": "multiple_canonical_urls_for_identity",
                "canonical_identity": key,
                "canonical_urls": original_group_urls,
                "source_ids": [item["source_id"] for item in members],
            })
        redirect_group_urls = [
            url for url in canonical_urls
            if (urlsplit(url).hostname or "").casefold() in {"t.co", "www.t.co"}
        ]
        if (
            not document_number
            and not original_group_urls
            and len(set(redirect_group_urls)) > 1
        ):
            ambiguities.append({
                "kind": "multiple_redirect_urls_for_identity",
                "canonical_identity": key,
                "canonical_urls": redirect_group_urls,
                "source_ids": [item["source_id"] for item in members],
            })
        roles = sorted({role for item in members for role in item["roles"]})
        claims = [
            field for field in CLAIM_FIELDS
            if any(field in item["claims_supported"] for item in members)
        ]
        quality = min(
            (str(item["source_quality_class"]) for item in members),
            key=lambda value: (_QUALITY_PRIORITY.get(value, 9), value),
        )
        public_source = {
            "title": _plain_public_title(selected["title"], document_number),
            "url": canonical_url,
            "source_type": quality,
            "roles": roles,
            "claims_supported": claims,
            "secondary_recollection": all(item["secondary_recollection"] for item in members),
        }
        identity_basis = min(
            (item["identity_basis"] for item in members),
            key=lambda value: (_IDENTITY_BASIS_PRIORITY.get(value, 99), value),
        )
        groups.append({
            "canonical_identity": key,
            "identity_basis": identity_basis,
            "canonical_url": canonical_url,
            "document_number": document_number,
            "source_ids": [item["source_id"] for item in members],
            "source_record_count": len(members),
            "public_source": public_source,
        })

    # Report a deliberately narrow unresolved MTF identity possibility without
    # changing either canonical keys or public grouping.  These rows are an
    # audit prompt only: the evidence is insufficient for an automatic merge.
    explicit_mtf_groups = [
        group for group in groups
        if group["identity_basis"] == "margaret_thatcher_foundation_document"
    ]
    already_diagnosed_source_ids = {
        _clean(ambiguity.get("source_id"))
        for ambiguity in ambiguities
        if _clean(ambiguity.get("source_id"))
    }
    already_diagnosed_source_ids.update(
        _clean(source_id)
        for ambiguity in ambiguities
        for source_id in ambiguity.get("source_ids", [])
        if _clean(source_id)
    )
    packet_dates = _source_date_identities(packet)
    packet_has_exact_mtf_marker = bool(re.search(
        r"\bMargaret Thatcher Foundation(?: Archive)?\b",
        _clean(packet.get("stable_locator")),
        re.I,
    ))
    if len(explicit_mtf_groups) == 1:
        explicit_mtf_group = explicit_mtf_groups[0]
        for candidate in groups:
            if (
                candidate["identity_basis"]
                not in {"bibliographic_identity", "distinct_source_record"}
                or candidate["canonical_url"]
            ):
                continue
            members = groups_by_key[candidate["canonical_identity"]]
            source_ids = {member["source_id"] for member in members}
            candidate_dates = {
                date
                for member in members
                for date in member["date_identities"]
            }
            has_virtual_canonical_locator = any(
                member["_row"].get("virtual_locator_record") is True
                and member["source_type"].casefold() == "canonical_stable_locator"
                for member in members
            )
            all_strong_primary = all(
                member["source_quality_class"] == "strong_primary_evidence"
                for member in members
            )
            has_disqualifying_identity_evidence = any(
                member["secondary_recollection"]
                or member["composite_locator"]
                or member["document_numbers"]
                or member["explicit_identifiers"]
                for member in members
            )
            has_exact_mtf_authority = packet_has_exact_mtf_marker or any(
                member["publisher"] == "margaret_thatcher_foundation"
                for member in members
            )
            if not (
                has_virtual_canonical_locator
                and all_strong_primary
                and not has_disqualifying_identity_evidence
                and has_exact_mtf_authority
                and candidate_dates
                and packet_dates
                and _dates_compatible(candidate_dates, packet_dates)
                and source_ids.isdisjoint(already_diagnosed_source_ids)
            ):
                continue
            ambiguities.append({
                "kind": "possible_same_mtf_document_identity_unresolved",
                "explicit_canonical_identity": explicit_mtf_group[
                    "canonical_identity"
                ],
                "weak_canonical_identity": candidate["canonical_identity"],
                "source_ids": sorted(source_ids),
                "weak_dates": sorted(candidate_dates),
                "packet_dates": sorted(packet_dates),
            })
    groups.sort(key=lambda group: (
        min((_ROLE_PRIORITY.get(role, 9) for role in group["public_source"]["roles"]), default=9),
        group["public_source"]["title"].casefold(),
        group["canonical_identity"],
    ))
    public_url_owner: dict[str, str] = {}
    for group in groups:
        canonical_url = group["canonical_url"]
        if not canonical_url:
            continue
        owner = public_url_owner.setdefault(canonical_url, group["canonical_identity"])
        if owner != group["canonical_identity"]:
            ambiguities.append({
                "kind": "duplicate_public_url_suppressed",
                "canonical_url": canonical_url,
                "rendered_by_canonical_identity": owner,
                "suppressed_for_canonical_identity": group["canonical_identity"],
            })
            group["canonical_url"] = ""
            group["public_source"]["url"] = ""
    public_records = [{key: value for key, value in record.items() if key != "_row"} for record in records]
    return {
        "records": public_records,
        "groups": groups,
        "identity_ambiguities": ambiguities,
    }


def public_sources(packet: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one concise role-bearing record per canonical public source."""
    return [
        group["public_source"]
        for group in public_source_identity_diagnostics(packet)["groups"]
    ]
