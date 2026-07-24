"""Validate operator-curated historical-context source evidence.

This sidecar records evidence supplied outside provider research without
rewriting the immutable quotation research packets.  Runtime consumers use
it only after deterministic identity, passage-hash and source-role checks.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlsplit


CURATED_EVIDENCE_SCHEMA_VERSION = 3
CURATED_EVIDENCE_POLICY_VERSION = (
    "historical-context-curated-evidence-v3-archive-provenance"
)
CURATED_EVIDENCE_FILENAME = "historical_context_source_curated_evidence.json"

_ROLES = {
    "wording_verification",
    "attribution_support",
    "source_event_support",
    "historical_context_support",
    "secondary_recollection",
}
_CLAIMS = {"wording", "attribution", "source_event", "date", "historical_context"}
_QUALITIES = {
    "strong_primary_evidence",
    "reliable_secondary_evidence",
    "secondary_recollection",
}
_MATCH_KINDS = {
    "exact",
    "normalised",
    "historical_variant",
    "excerpt",
    "partial",
}
_EVIDENCE_ORIGINS = {
    "operator_supplied_bibliographic_citation",
    "independently_reviewed_archival_retrieval",
    "independently_reviewed_public_retrieval",
}
_ADJUDICATION_DECISIONS = {"promote_partial_to_normalised"}
_HEX64 = re.compile(r"[0-9a-f]{64}")
_WAYBACK_CAPTURE = re.compile(
    r"https://web\.archive\.org/web/(?P<timestamp>\d{14})id_/"
)
_ARCHIVE_DIGEST = re.compile(r"[A-Z2-7]{32}")
_ARCHIVE_PROVENANCE_FIELDS = {
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
}
_FETCH_POLICY_VERSIONS = {
    "historical-context-restricted-fetch-v5",
    "historical-context-restricted-fetch-v6",
}


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def curated_source_id(quote_id: str, source: dict[str, Any]) -> str:
    """Return the stable identity of one curated bibliographic record."""
    identity = "\n".join((
        quote_id,
        str(source.get("stable_locator") or ""),
        str(source.get("source_event") or ""),
        str(source.get("source_date") or ""),
        str(source.get("exact_supporting_passage") or ""),
    ))
    return _sha256(identity.encode("utf-8"))


def curated_source_adjudication_id(adjudication: dict[str, Any]) -> str:
    """Return the stable identity of one correction to an existing source row."""
    identity = "\n".join(
        str(adjudication.get(field) or "")
        for field in (
            "quote_id",
            "source_id",
            "resolution_record_sha256",
            "matched_passage_sha256",
            "page_text_sha256",
            "public_url",
            "decision",
            "wording_coverage",
        )
    )
    return _sha256(identity.encode("utf-8"))


def _validate_archive_provenance(source: dict[str, Any]) -> bool:
    """Validate source identity separately from its archival transport."""
    present = _ARCHIVE_PROVENANCE_FIELDS.intersection(source)
    retrieval_archive = source.get("retrieval_archive")
    if not present:
        return True

    canonical_url = str(source.get("canonical_url") or "")
    canonical = urlsplit(canonical_url)
    if (
        not str(source.get("source_publisher") or "").strip()
        or canonical.scheme not in {"http", "https"}
        or not canonical.netloc
        or canonical_url != str(source.get("url") or "")
    ):
        return False

    # Directly fetched sources may carry explicit publisher/canonical identity
    # without pretending that an archive supplied the representation.
    if not retrieval_archive:
        archive_only = {
            "transport_url",
            "archive_capture_timestamp",
            "archive_capture_digest",
        }
        return not archive_only.intersection(source)

    transport_url = str(source.get("transport_url") or "")
    transport = urlsplit(transport_url)
    capture = _WAYBACK_CAPTURE.match(transport_url.split("?", 1)[0])
    if (
        retrieval_archive != "Internet Archive Wayback Machine"
        or transport.scheme != "https"
        or transport.hostname != "web.archive.org"
        or capture is None
        or source.get("archive_capture_timestamp") != capture.group("timestamp")
        or not _ARCHIVE_DIGEST.fullmatch(
            str(source.get("archive_capture_digest") or "")
        )
        or source.get("fetch_policy_version") not in _FETCH_POLICY_VERSIONS
        or not _HEX64.fullmatch(str(source.get("page_sha256") or ""))
        or not _HEX64.fullmatch(str(source.get("page_text_sha256") or ""))
        or not str(source.get("source_date_raw") or "").strip()
        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(source.get("date") or ""))
    ):
        return False
    if canonical.hostname not in {
        "margaretthatcher.org",
        "www.margaretthatcher.org",
    }:
        return False
    return source.get("source_publisher") == "Margaret Thatcher Foundation"


def _validate_optional_supporting_context(source: dict[str, Any]) -> bool:
    """Bind optional reviewed context without requiring it on legacy rows."""
    present = {
        "supporting_context",
        "supporting_context_sha256",
    }.intersection(source)
    if not present:
        return True
    context = source.get("supporting_context")
    return (
        present
        == {"supporting_context", "supporting_context_sha256"}
        and isinstance(context, str)
        and bool(context.strip())
        and source.get("supporting_context_sha256")
        == _sha256(context.encode("utf-8"))
    )


def validate_curated_evidence(
    manifest: dict[str, Any],
    packets: dict[str, dict[str, Any]],
) -> None:
    """Fail closed on stale, malformed or over-claimed curated evidence."""
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != CURATED_EVIDENCE_SCHEMA_VERSION
        or manifest.get("policy_version") != CURATED_EVIDENCE_POLICY_VERSION
        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(manifest.get("created_at") or ""))
    ):
        raise RuntimeError("historical-context curated evidence manifest is incompatible")
    items = manifest.get("items")
    if not isinstance(items, dict):
        raise RuntimeError("historical-context curated evidence items are invalid")
    counted = 0
    source_ids: set[str] = set()
    for quote_id, item in items.items():
        packet = packets.get(quote_id)
        sources = item.get("sources") if isinstance(item, dict) else None
        if (
            packet is None
            or item.get("quote_id") != quote_id
            or item.get("quote_text") != packet["quote_text"]
            or item.get("quote_text_sha256")
            != _sha256(packet["quote_text"].encode("utf-8"))
            or not isinstance(sources, list)
            or not sources
        ):
            raise RuntimeError(
                f"historical-context curated evidence changed quotation identity: {quote_id}"
            )
        for source in sources:
            passage = source.get("exact_supporting_passage")
            roles = source.get("assigned_roles")
            claims = source.get("claims_supported")
            parsed = urlsplit(str(source.get("url") or ""))
            if (
                not isinstance(passage, str)
                or not passage.strip()
                or source.get("exact_supporting_passage_sha256")
                != _sha256(passage.encode("utf-8"))
                or not isinstance(roles, list)
                or not roles
                or not set(roles) <= _ROLES
                or not isinstance(claims, list)
                or not claims
                or not set(claims) <= _CLAIMS
                or source.get("source_quality_class") not in _QUALITIES
                or source.get("wording_match_kind") not in _MATCH_KINDS
                or not str(source.get("title") or "").strip()
                or not str(source.get("stable_locator") or "").strip()
                or not str(source.get("source_type") or "").strip()
                or not str(source.get("rationale") or "").strip()
                or source.get("evidence_origin") not in _EVIDENCE_ORIGINS
                or type(source.get("page_independently_inspected")) is not bool
                or not _validate_archive_provenance(source)
                or not _validate_optional_supporting_context(source)
                or not re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}", str(source.get("recorded_at") or "")
                )
                or (source.get("url") and (parsed.scheme not in {"http", "https"} or not parsed.netloc))
                or source.get("source_id") != curated_source_id(quote_id, source)
                or source["source_id"] in source_ids
            ):
                raise RuntimeError(
                    f"historical-context curated source is invalid: {quote_id}"
                )
            if (
                ("source_event" in claims and source.get("source_event") != packet["source_event"])
                or ("date" in claims and source.get("source_date") != packet["date"])
            ):
                raise RuntimeError(
                    f"historical-context curated source claim metadata differs: {quote_id}"
                )
            if source["source_quality_class"] == "secondary_recollection" and (
                "secondary_recollection" not in roles
                or "wording_verification" not in roles
                or "attribution_support" not in roles
            ):
                raise RuntimeError(
                    f"historical-context recollection roles are incomplete: {quote_id}"
                )
            source_ids.add(source["source_id"])
            counted += 1
    adjudications = manifest.get("source_adjudications")
    if not isinstance(adjudications, list):
        raise RuntimeError(
            "historical-context curated source adjudications are invalid"
        )
    adjudication_ids: set[str] = set()
    adjudication_targets: set[tuple[str, str]] = set()
    for adjudication in adjudications:
        if not isinstance(adjudication, dict):
            raise RuntimeError(
                "historical-context curated source adjudication is invalid"
            )
        quote_id = adjudication.get("quote_id")
        packet = packets.get(str(quote_id or ""))
        parsed = urlsplit(str(adjudication.get("public_url") or ""))
        adjudication_id = adjudication.get("adjudication_id")
        target = (str(quote_id or ""), str(adjudication.get("source_id") or ""))
        if (
            packet is None
            or adjudication.get("quote_text_sha256")
            != _sha256(packet["quote_text"].encode("utf-8"))
            or packet.get("verification_status") != "normalised"
            or not _HEX64.fullmatch(str(adjudication.get("source_id") or ""))
            or not _HEX64.fullmatch(
                str(adjudication.get("resolution_record_sha256") or "")
            )
            or not _HEX64.fullmatch(
                str(adjudication.get("matched_passage_sha256") or "")
            )
            or not _HEX64.fullmatch(
                str(adjudication.get("page_text_sha256") or "")
            )
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or adjudication.get("decision") not in _ADJUDICATION_DECISIONS
            or adjudication.get("wording_coverage") != "normalised"
            or not str(adjudication.get("rationale") or "").strip()
            or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}",
                str(adjudication.get("reviewed_at") or ""),
            )
            or adjudication_id != curated_source_adjudication_id(adjudication)
            or adjudication_id in adjudication_ids
            or target in adjudication_targets
        ):
            raise RuntimeError(
                f"historical-context curated source adjudication is invalid: {quote_id}"
            )
        adjudication_ids.add(adjudication_id)
        adjudication_targets.add(target)
    if (
        manifest.get("quote_count") != len(items)
        or manifest.get("source_count") != counted
        or manifest.get("source_adjudication_count") != len(adjudications)
    ):
        raise RuntimeError("historical-context curated evidence counts differ")
