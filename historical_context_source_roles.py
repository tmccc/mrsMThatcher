"""Offline source-role auditing and runtime validation for context evidence.

The canonical quotation packets remain immutable.  This module builds and
validates a sidecar which says what each saved grounding record can actually
support.  Runtime formatters may use only the roles recorded in that sidecar.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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


AUDIT_SCHEMA_VERSION = 4
POLICY_VERSION = (
    "historical-context-source-roles-v5-independent-review-and-exclusive-counts"
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


def audit_packet(
    packet: dict[str, Any],
    *,
    attribution_eligible: bool,
    recovery_item: dict[str, Any] | None = None,
    source_resolution: dict[str, Any] | None = None,
    research_item: dict[str, Any] | None = None,
    openai_research_item: dict[str, Any] | None = None,
    independent_review_items: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Audit every source and confidence dimension for one packet."""
    locator_kind = precise_locator_kind(packet.get("stable_locator"))
    locator_is_precise = locator_kind not in {None, "quotation_compilation"}
    resolution_items = (source_resolution or {}).get("items", {})
    physical_rows = []
    for index, source in enumerate(packet.get("sources", [])):
        resolution = resolution_items.get(sha256_bytes(_clean(source.get("url")).encode()))
        if resolution is not None and packet["quote_id"] in resolution.get("quote_ids", []):
            row = audit_resolved_redirect(packet["quote_id"], packet, index, source, resolution)
        else:
            row = audit_source(
                packet["quote_id"], packet, index, source, locator_kind=locator_kind
            )
        physical_rows.append(row)
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
    rows = _packet_roles(
        packet, physical_rows + recovered_rows + researched_rows, locator_kind
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
            field for field, role in (
                ("source_event", "source_event_support"),
                ("date", "source_event_support"),
                ("historical_context", "historical_context_support"),
            ) if role in roles and (field != "date" or _known(packet.get("date")))
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
    virtual_locator_sources = [
        source
        for item in items.values()
        for source in item["virtual_locator_sources"]
    ]
    all_sources = physical_sources + recovered_sources + lead_sources + researched_sources
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
        "audit_date": "2026-07-21",
        "source_file_hashes": source_file_hashes,
        "packet_count": len(items),
        "source_count": len(physical_sources),
        "recovered_citation_source_count": len(recovered_sources),
        "model_proposed_source_lead_count": len(lead_sources),
        "gemini_researched_source_count": len(gemini_researched_sources),
        "openai_researched_source_count": len(openai_researched_sources),
        "researched_source_count": len(researched_sources),
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
                    "model-lead and independently reviewed AI-located records."
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
        attached[quote_id] = {
            **packet,
            "_source_role_audit": {**row, "policy_version": audit["policy_version"]},
        }
    return attached


def public_sources(packet: dict[str, Any]) -> list[dict[str, Any]]:
    """Return deduplicated, role-bearing sources from an attached audit row."""
    audit = packet.get("_source_role_audit")
    if not isinstance(audit, dict):
        return []
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for row in audit.get("renderable_sources", []):
        roles = tuple(sorted(
            role for role in row.get("assigned_roles", [])
            if role not in {"secondary_recollection", "discovery_only", "rejected_irrelevant"}
        ))
        title = _clean(row.get("public_title"))
        url = _clean(row.get("public_url"))
        if not title or not roles:
            continue
        key = (title, url)
        current = merged.setdefault(key, {
            "title": title,
            "url": url,
            "source_type": row["source_quality_class"],
            "roles": [],
            "claims_supported": [],
            "secondary_recollection": False,
        })
        current["roles"] = sorted(set(current["roles"]) | set(roles))
        current["claims_supported"] = [
            field for field in CLAIM_FIELDS
            if field in set(current["claims_supported"]) | set(row.get("claims_supported", []))
        ]
        current["secondary_recollection"] = bool(
            current["secondary_recollection"]
            or "secondary_recollection" in row.get("assigned_roles", [])
        )
        if row["source_quality_class"] == "strong_primary_evidence":
            current["source_type"] = "strong_primary_evidence"
    role_priority = {
        "wording_verification": 0,
        "attribution_support": 1,
        "source_event_support": 2,
        "historical_context_support": 3,
    }
    return sorted(
        merged.values(),
        key=lambda row: (
            min((role_priority.get(role, 9) for role in row["roles"]), default=9),
            row["title"],
            row["url"],
        ),
    )[:2]
