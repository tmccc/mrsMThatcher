#!/usr/bin/env python3
"""Deterministic helpers for targeted historical-evidence remediation.

This module deliberately contains no network client and no production-data
writer.  It separates three concepts which the legacy retrieval artefact used
one ``accepted_as_evidence`` flag to represent:

``usable_evidence_candidate_ids``
    Fetched material which legitimately informs the review.

``resolution_support_candidate_ids``
    Reviewed material which supports the final exact-primary or
    primary-variant resolution.

``sufficient_for_historical_context``
    A decision made only after the complete canonical evidence record has
    passed the historical-context admission policy.

Legacy ``accepted_as_evidence``, ``accepted_candidates`` and the nested
``accepted_candidate_ids`` are retained, when requested, with the first and
only meaning above: *usable evidence*.  They must never be used as a proxy for
resolution support or historical-context sufficiency.
"""
from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import unquote, urlsplit, urlunsplit
from historical_context_search_research import UnsafeURL, canonicalise_url


EVIDENCE_MODEL_VERSION = "historical-context-targeted-evidence-remediation-v1"

LEGACY_FIELD_SEMANTICS = {
    "accepted_as_evidence": "usable_evidence_candidate_ids membership",
    "accepted_candidates": "copies of usable evidence candidates",
    "codex_evidence_review.accepted_candidate_ids": (
        "usable_evidence_candidate_ids; not resolution support or admission"
    ),
}

EXACT_PRIMARY_OUTCOME = "exact_primary_wording_found"
PRIMARY_VARIANT_OUTCOME = "primary_variant_found"
PRIMARY_RESOLUTION_OUTCOMES = {
    EXACT_PRIMARY_OUTCOME,
    PRIMARY_VARIANT_OUTCOME,
}
INCOMPLETE_OUTCOMES = {
    "promising_but_insufficient",
    "search_incomplete_due_to_access",
    "search_incomplete_due_to_request_cap",
}
PRIMARY_CLASSIFICATION = "strong_primary_evidence"
FETCHED_STATUSES = {"fetched"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
WAYBACK_CAPTURE_RE = re.compile(
    r"^/web/(?P<timestamp>\d{14})(?P<modifier>[a-z_]{0,4})?/(?P<original>.+)$",
    re.IGNORECASE,
)
CONTENT_RANGE_RE = re.compile(
    r"^bytes\s+(?P<start>\d+)-(?P<end>\d+)/(?P<total>\d+|\*)$",
    re.IGNORECASE,
)
COMMON_CRAWL_TIMESTAMP_RE = re.compile(r"^\d{14}$")

PROHIBITED_ARCHIVE_OR_PAYWALL_HOSTS = {
    "1ft.io",
    "12ft.io",
    "12ftladder.net",
    "archive.ph",
    "archive.today",
    "archive.is",
    "archive.li",
    "archive.md",
    "archive.vn",
    "freedium.cfd",
    "outline.com",
    "removepaywall.com",
    "removepaywall.org",
    "textise.net",
    "txtify.it",
}
PROHIBITED_HOST_MARKERS = (
    "paywall-bypass",
    "paywallproxy",
    "google-cache",
    "googlecache",
)


class EvidenceValidationError(ValueError):
    """The corrected advisory evidence model is inconsistent."""


def sha256_bytes(payload: bytes) -> str:
    """Return a lower-case SHA-256 digest."""

    return hashlib.sha256(payload).hexdigest()


def payload_hashes(payload: bytes, extracted_text: str | None = None) -> dict[str, str]:
    """Hash an archive payload and, when supplied, its extracted Unicode text."""

    result = {"payload_sha256": sha256_bytes(payload)}
    if extracted_text is not None:
        result["extracted_text_sha256"] = sha256_bytes(
            extracted_text.encode("utf-8")
        )
    return result


def _candidate_index(case: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    index: dict[str, Mapping[str, Any]] = {}
    for candidate in case.get("candidate_sources", []):
        if not isinstance(candidate, Mapping):
            continue
        candidate_id = candidate.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            continue
        if candidate_id in index:
            raise EvidenceValidationError(f"duplicate candidate ID: {candidate_id}")
        index[candidate_id] = candidate
    return index


def _candidate_is_fetched(candidate: Mapping[str, Any]) -> bool:
    status = candidate.get("fetch_status")
    http_status = candidate.get("http_status")
    page_sha256 = candidate.get("page_sha256")
    return (
        status in FETCHED_STATUSES
        and isinstance(http_status, int)
        and 200 <= http_status < 300
        and isinstance(page_sha256, str)
        and bool(SHA256_RE.fullmatch(page_sha256))
    )


def _candidate_review(
    case: Mapping[str, Any], candidate: Mapping[str, Any]
) -> Mapping[str, Any]:
    candidate_id = candidate.get("candidate_id")
    top_review = case.get("codex_evidence_review", {})
    if isinstance(top_review, Mapping):
        candidate_reviews = top_review.get("candidate_reviews", {})
        if isinstance(candidate_reviews, Mapping):
            review = candidate_reviews.get(candidate_id)
            if isinstance(review, Mapping):
                return review
    review = candidate.get("codex_review")
    if isinstance(review, Mapping):
        return review
    return {}


def _candidate_is_rejected(
    case: Mapping[str, Any], candidate: Mapping[str, Any]
) -> bool:
    review = _candidate_review(case, candidate)
    if str(review.get("resolution_support_decision", "")).lower() in {
        "reject",
        "rejected",
    }:
        return True
    if review.get("accepted_as_evidence") is False:
        return True
    if str(review.get("decision", "")).lower() in {"reject", "rejected"}:
        return True
    override = candidate.get("codex_review_override")
    return isinstance(override, Mapping) and override.get("accepted_as_evidence") is False


def _case_outcomes(case: Mapping[str, Any]) -> set[str]:
    outcomes: set[str] = set()
    for key in ("recommended_outcome", "outcome"):
        value = case.get(key)
        if isinstance(value, str) and value:
            outcomes.add(value)
    for key in ("codex_evidence_review", "final_review", "review_decision"):
        nested = case.get(key)
        if isinstance(nested, Mapping):
            value = nested.get("outcome")
            if isinstance(value, str) and value:
                outcomes.add(value)
    return outcomes


def _has_precise_variant_difference(case: Mapping[str, Any]) -> bool:
    value = case.get("precise_textual_difference")
    if isinstance(value, str) and value.strip():
        return True
    if isinstance(value, Mapping) and value:
        return True
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and value:
        return True
    comparison = case.get("wording_comparison")
    if isinstance(comparison, Mapping):
        differences = comparison.get("differences")
        return (
            isinstance(differences, Sequence)
            and not isinstance(differences, (str, bytes))
            and bool(differences)
        )
    return False


def _reviewed_primary_available(
    case: Mapping[str, Any],
    support_candidates: Iterable[Mapping[str, Any]],
    *,
    expected_match_type: str,
) -> bool:
    top_review = case.get("codex_evidence_review", {})
    checks = top_review.get("review_checks", {}) if isinstance(top_review, Mapping) else {}
    review_is_complete = (
        isinstance(checks, Mapping)
        and checks.get("actual_passage_verified") is True
        and checks.get("source_identity_verified") is True
        and (
            checks.get("speaker_or_author_verified") is True
            or checks.get("speaker_verified") is True
        )
        and checks.get("match_type_verified") is True
    )
    for candidate in support_candidates:
        classification = candidate.get(
            "effective_classification", candidate.get("classification")
        )
        match_type = str(candidate.get("match_type", ""))
        match_ok = (
            match_type == "exact_quotation"
            if expected_match_type == "exact"
            else match_type in {
                "recorded_variant",
                "near_exact_variant",
                "normalised_variant",
                "exact_quotation",
            }
        )
        if (
            _candidate_is_fetched(candidate)
            and classification == PRIMARY_CLASSIFICATION
            and match_ok
            and bool(candidate.get("supporting_passage"))
            and review_is_complete
        ):
            return True
    return False


def validate_evidence_case(case: Mapping[str, Any]) -> list[str]:
    """Return deterministic validation errors for one corrected quotation case."""

    errors: list[str] = []
    quote_id = str(case.get("quote_id", "<missing quote ID>"))
    try:
        candidates = _candidate_index(case)
    except EvidenceValidationError as exc:
        return [f"{quote_id}: {exc}"]

    usable = case.get("usable_evidence_candidate_ids")
    support = case.get("resolution_support_candidate_ids")
    sufficient = case.get("sufficient_for_historical_context")
    if not isinstance(usable, list) or not all(isinstance(item, str) for item in usable):
        errors.append(f"{quote_id}: usable_evidence_candidate_ids must be a string list")
        usable = []
    if not isinstance(support, list) or not all(isinstance(item, str) for item in support):
        errors.append(f"{quote_id}: resolution_support_candidate_ids must be a string list")
        support = []
    if not isinstance(sufficient, bool):
        errors.append(f"{quote_id}: sufficient_for_historical_context must be boolean")
        sufficient = False
    if len(usable) != len(set(usable)):
        errors.append(f"{quote_id}: duplicate usable evidence candidate ID")
    if len(support) != len(set(support)):
        errors.append(f"{quote_id}: duplicate resolution-support candidate ID")

    for label, selected in (("usable", usable), ("resolution-support", support)):
        for candidate_id in selected:
            candidate = candidates.get(candidate_id)
            if candidate is None:
                errors.append(f"{quote_id}: nonexistent {label} candidate {candidate_id}")
                continue
            if not _candidate_is_fetched(candidate):
                errors.append(f"{quote_id}: unfetched {label} candidate {candidate_id}")
            if candidate.get("snippet_used_as_evidence") is not False:
                errors.append(f"{quote_id}: snippet used as {label} evidence {candidate_id}")
    for candidate_id in support:
        if candidate_id not in usable:
            errors.append(
                f"{quote_id}: resolution-support candidate is not usable {candidate_id}"
            )
        candidate = candidates.get(candidate_id)
        if candidate is not None and _candidate_is_rejected(case, candidate):
            errors.append(
                f"{quote_id}: rejected candidate used as resolution support {candidate_id}"
            )

    outcomes = _case_outcomes(case)
    if len(outcomes) != 1:
        errors.append(
            f"{quote_id}: conflicting or missing review outcomes {sorted(outcomes)!r}"
        )
        outcome = ""
    else:
        outcome = next(iter(outcomes))
    if outcome in INCOMPLETE_OUTCOMES and sufficient:
        errors.append(
            f"{quote_id}: incomplete outcome cannot be sufficient for historical context"
        )

    support_candidates = [candidates[cid] for cid in support if cid in candidates]
    if outcome == EXACT_PRIMARY_OUTCOME:
        if not support:
            errors.append(f"{quote_id}: exact-primary outcome has no resolution support")
        elif not _reviewed_primary_available(
            case, support_candidates, expected_match_type="exact"
        ):
            errors.append(
                f"{quote_id}: exact-primary outcome lacks fetched reviewed primary evidence"
            )
    if outcome == PRIMARY_VARIANT_OUTCOME:
        if not support:
            errors.append(f"{quote_id}: primary-variant outcome has no resolution support")
        elif not _reviewed_primary_available(
            case, support_candidates, expected_match_type="variant"
        ):
            errors.append(
                f"{quote_id}: primary-variant outcome lacks fetched reviewed primary evidence"
            )
        if not _has_precise_variant_difference(case):
            errors.append(
                f"{quote_id}: primary-variant outcome lacks a precise textual difference"
            )
    return errors


def evidence_outcome_counts(document: Mapping[str, Any]) -> dict[str, int]:
    """Count top-level advisory outcomes in a corrected evidence document."""

    return dict(
        sorted(
            Counter(
                case.get("recommended_outcome")
                for case in document.get("quotations", [])
                if isinstance(case, Mapping)
            ).items()
        )
    )


def validate_quote_cycle_invariants(
    *,
    before_quotations: Mapping[str, str],
    after_quotations: Mapping[str, str],
    before_cycle_quote_ids: Sequence[str],
    after_cycle_quote_ids: Sequence[str],
    expected_cycle_count: int = 610,
) -> list[str]:
    """Check that remediation changed neither quotation corpus nor normal cycle."""

    errors: list[str] = []
    if dict(before_quotations) != dict(after_quotations):
        before_ids = set(before_quotations)
        after_ids = set(after_quotations)
        if before_ids != after_ids:
            errors.append("quotation IDs changed")
        changed_text_ids = sorted(
            quote_id
            for quote_id in before_ids & after_ids
            if before_quotations[quote_id] != after_quotations[quote_id]
        )
        if changed_text_ids:
            errors.append(
                "quotation text changed for IDs: " + ", ".join(changed_text_ids)
            )
    if len(before_cycle_quote_ids) != expected_cycle_count:
        errors.append(
            f"before cycle count is not {expected_cycle_count}: "
            f"{len(before_cycle_quote_ids)}"
        )
    if len(after_cycle_quote_ids) != expected_cycle_count:
        errors.append(
            f"after cycle count is not {expected_cycle_count}: "
            f"{len(after_cycle_quote_ids)}"
        )
    if len(set(before_cycle_quote_ids)) != len(before_cycle_quote_ids):
        errors.append("before cycle contains duplicate quotation IDs")
    if len(set(after_cycle_quote_ids)) != len(after_cycle_quote_ids):
        errors.append("after cycle contains duplicate quotation IDs")
    if set(before_cycle_quote_ids) != set(after_cycle_quote_ids):
        errors.append("regular-post cycle membership changed")
    return errors


def validate_evidence_document(
    document: Mapping[str, Any],
    *,
    report_counts: Mapping[str, int] | None = None,
    raise_on_error: bool = False,
) -> dict[str, Any]:
    """Validate corrected cases and evidence/report count agreement."""

    errors: list[str] = []
    quotations = document.get("quotations")
    if not isinstance(quotations, list):
        errors.append("document quotations must be a list")
        quotations = []
    quote_ids = [
        case.get("quote_id")
        for case in quotations
        if isinstance(case, Mapping)
    ]
    if len(quote_ids) != len(set(quote_ids)):
        errors.append("duplicate quotation ID")
    for case in quotations:
        if isinstance(case, Mapping):
            errors.extend(validate_evidence_case(case))
        else:
            errors.append("quotation case is not an object")

    counts = evidence_outcome_counts({"quotations": quotations})
    declared = document.get("counts", {})
    if isinstance(declared, Mapping):
        declared_outcomes = declared.get("recommended_outcomes")
        if declared_outcomes is not None:
            if not isinstance(declared_outcomes, Mapping):
                errors.append("document recommended-outcome counts must be an object")
            elif dict(declared_outcomes) != counts:
                errors.append(
                    "document recommended-outcome counts do not match quotations"
                )
        declared_total = declared.get("targeted_quotation_count")
        if declared_total is not None and declared_total != len(quotations):
            errors.append("document targeted quotation count does not match quotations")
    if report_counts is not None and dict(report_counts) != counts:
        errors.append("report outcome counts do not match evidence JSON")

    result = {
        "valid": not errors,
        "errors": sorted(errors),
        "targeted_quotation_count": len(quotations),
        "recommended_outcomes": counts,
    }
    if errors and raise_on_error:
        raise EvidenceValidationError("; ".join(sorted(errors)))
    return result


def apply_corrected_evidence_semantics(
    case: Mapping[str, Any],
    *,
    usable_evidence_candidate_ids: Sequence[str],
    resolution_support_candidate_ids: Sequence[str],
    sufficient_for_historical_context: bool,
    precise_textual_difference: Any | None = None,
    retain_legacy_fields: bool = True,
) -> dict[str, Any]:
    """Return a corrected copy with explicit, non-conflated evidence decisions."""

    corrected = copy.deepcopy(dict(case))
    usable = list(dict.fromkeys(usable_evidence_candidate_ids))
    support = list(dict.fromkeys(resolution_support_candidate_ids))
    corrected["usable_evidence_candidate_ids"] = usable
    corrected["resolution_support_candidate_ids"] = support
    corrected["sufficient_for_historical_context"] = bool(
        sufficient_for_historical_context
    )
    if precise_textual_difference is not None:
        corrected["precise_textual_difference"] = copy.deepcopy(
            precise_textual_difference
        )
    if retain_legacy_fields:
        for candidate in corrected.get("candidate_sources", []):
            if isinstance(candidate, dict):
                candidate["accepted_as_evidence"] = (
                    candidate.get("candidate_id") in usable
                )
        corrected["accepted_candidates"] = [
            copy.deepcopy(candidate)
            for candidate in corrected.get("candidate_sources", [])
            if isinstance(candidate, Mapping)
            and candidate.get("candidate_id") in usable
        ]
        review = corrected.setdefault("codex_evidence_review", {})
        if isinstance(review, dict):
            review["accepted_candidate_ids"] = list(usable)
            candidate_reviews = review.get("candidate_reviews")
            if isinstance(candidate_reviews, dict):
                for candidate_id, candidate_review in candidate_reviews.items():
                    if not isinstance(candidate_review, dict):
                        continue
                    old_value = candidate_review.get("accepted_as_evidence")
                    if isinstance(old_value, bool):
                        candidate_review[
                            "pre_correction_accepted_as_evidence"
                        ] = old_value
                    candidate_review["accepted_as_evidence"] = candidate_id in usable
                    candidate_review["resolution_support_decision"] = (
                        "accept" if candidate_id in support else "reject"
                    )
        corrected["legacy_field_semantics"] = copy.deepcopy(LEGACY_FIELD_SEMANTICS)
    return corrected


def normalise_source_date(raw_date: str) -> str:
    """Convert known archive source dates to ISO without inventing precision."""

    value = " ".join(str(raw_date).split())
    if not value:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
        except ValueError:
            return ""
    formats = (
        "%Y %b %d %a",
        "%Y %B %d %a",
        "%Y %b %d",
        "%Y %B %d",
        "%d %b %Y",
        "%d %B %Y",
    )
    # MTF uses two-letter weekday abbreviations (Su, Mo, Tu, We, Th, Fr, Sa).
    without_weekday = re.sub(r"\s+(?:Su|Mo|Tu|We|Th|Fr|Sa)$", "", value)
    for candidate in (value, without_weekday):
        for date_format in formats:
            try:
                return datetime.strptime(candidate, date_format).date().isoformat()
            except ValueError:
                continue
    return ""


def parse_wayback_capture_url(url: str) -> dict[str, str]:
    """Parse an exact Wayback transport URL without confusing it with publisher."""

    parts = urlsplit(url)
    if parts.scheme.lower() != "https" or parts.hostname not in {
        "web.archive.org",
        "www.web.archive.org",
    }:
        raise EvidenceValidationError("not an HTTPS Internet Archive Wayback URL")
    match = WAYBACK_CAPTURE_RE.fullmatch(parts.path)
    if match is None:
        raise EvidenceValidationError("Wayback URL lacks an exact capture timestamp")
    timestamp = match.group("timestamp")
    captured_at = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(
        tzinfo=timezone.utc
    )
    original = unquote(match.group("original"))
    original_parts = urlsplit(original)
    if original_parts.scheme.lower() not in {"http", "https"} or not original_parts.hostname:
        raise EvidenceValidationError("Wayback URL contains an invalid original URL")
    return {
        "archive_capture_timestamp": timestamp,
        "archive_capture_datetime": captured_at.isoformat().replace("+00:00", "Z"),
        "wayback_modifier": match.group("modifier") or "",
        "original_url": original,
        "transport_url": url,
    }


def normalise_mtf_document_url(value: str) -> str:
    """Normalise a Margaret Thatcher Foundation document URL for identity binding."""
    try:
        canonical = canonicalise_url(value)
    except (UnsafeURL, ValueError, TypeError) as exc:
        raise EvidenceValidationError("canonical URL is invalid for document binding") from exc
    if not re.fullmatch(
        r"https://www\.margaretthatcher\.org/document/\d+",
        canonical,
    ):
        raise EvidenceValidationError("canonical URL is not an MTF document URL")
    return canonical


def parse_cdx_response(payload: Any) -> list[dict[str, Any]]:
    """Parse Wayback CDX JSON returned either as objects or header/value rows."""

    if isinstance(payload, Mapping):
        records = payload.get("records", payload.get("items", []))
        if not isinstance(records, list):
            raise EvidenceValidationError("CDX object has no record list")
        return [dict(record) for record in records if isinstance(record, Mapping)]
    if not isinstance(payload, list) or not payload:
        raise EvidenceValidationError("CDX response must be a non-empty JSON list")
    if all(isinstance(item, Mapping) for item in payload):
        return [dict(item) for item in payload]
    header = payload[0]
    if not isinstance(header, list) or not all(isinstance(key, str) for key in header):
        raise EvidenceValidationError("CDX response header is invalid")
    records: list[dict[str, Any]] = []
    for row in payload[1:]:
        if not isinstance(row, list) or len(row) != len(header):
            raise EvidenceValidationError("CDX response row length does not match header")
        records.append(dict(zip(header, row)))
    return records


def normalise_mtf_wayback_provenance(
    candidate: Mapping[str, Any],
    *,
    cdx_record: Mapping[str, Any] | None = None,
    page_text_sha256: str | None = None,
) -> dict[str, Any]:
    """Separate MTF source identity from its Wayback retrieval transport."""

    corrected = copy.deepcopy(dict(candidate))
    canonical_url = normalise_mtf_document_url(str(corrected.get("canonical_url", "")))
    canonical = urlsplit(canonical_url)
    host = (canonical.hostname or "").lower()
    if host not in {"margaretthatcher.org", "www.margaretthatcher.org"}:
        raise EvidenceValidationError("candidate is not a Margaret Thatcher Foundation URL")
    transport_url = str(corrected.get("transport_url", ""))
    capture = parse_wayback_capture_url(transport_url)
    original_host = (urlsplit(capture["original_url"]).hostname or "").lower()
    if original_host not in {"margaretthatcher.org", "www.margaretthatcher.org"}:
        raise EvidenceValidationError("Wayback capture is not of an MTF source")

    canonical_capture_original = normalise_mtf_document_url(capture["original_url"])
    if canonical_capture_original != canonical_url:
        raise EvidenceValidationError(
            "Wayback capture original URL does not match candidate document identity"
        )
    corrected["source_publisher"] = "Margaret Thatcher Foundation"
    corrected["canonical_url"] = urlunsplit(canonical)
    corrected["retrieval_archive"] = "Internet Archive Wayback Machine"
    corrected["transport_url"] = transport_url
    corrected["archive_capture_timestamp"] = capture["archive_capture_timestamp"]
    corrected["publisher_or_archive"] = "Margaret Thatcher Foundation"
    raw_date = str(corrected.get("source_date_raw") or corrected.get("date") or "")
    corrected["source_date_raw"] = raw_date
    corrected["date"] = normalise_source_date(raw_date)
    if cdx_record is not None:
        cdx_original = str(cdx_record.get("original", "")).strip()
        if cdx_original:
            cdx_capture_original = normalise_mtf_document_url(cdx_original)
            if cdx_capture_original != canonical_url:
                raise EvidenceValidationError(
                    "CDX original URL does not match candidate document identity"
                )
        cdx_timestamp = str(cdx_record.get("timestamp", ""))
        if cdx_timestamp and cdx_timestamp != capture["archive_capture_timestamp"]:
            raise EvidenceValidationError("CDX timestamp conflicts with transport URL")
        digest = cdx_record.get("digest")
        if digest:
            corrected["archive_capture_digest"] = str(digest)
    text_digest = page_text_sha256 or corrected.get("page_text_sha256")
    if text_digest:
        if not isinstance(text_digest, str) or not SHA256_RE.fullmatch(text_digest):
            raise EvidenceValidationError("page text SHA-256 is invalid")
        corrected["page_text_sha256"] = text_digest
    page_digest = corrected.get("page_sha256")
    if not isinstance(page_digest, str) or not SHA256_RE.fullmatch(page_digest):
        raise EvidenceValidationError("page SHA-256 is invalid")
    if not corrected.get("fetch_policy_version"):
        raise EvidenceValidationError("fetch policy version must preserve actual provenance")
    return corrected


def parse_common_crawl_warc_metadata(
    record: Mapping[str, Any], *, crawl_index: str
) -> dict[str, Any]:
    """Validate and normalise one Common Crawl index/WARC record."""

    original_url = str(record.get("url", ""))
    parts = urlsplit(original_url)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise EvidenceValidationError("Common Crawl record has invalid target URL")
    timestamp = str(record.get("timestamp", ""))
    if not COMMON_CRAWL_TIMESTAMP_RE.fullmatch(timestamp):
        raise EvidenceValidationError("Common Crawl record timestamp is invalid")
    filename = str(record.get("filename", ""))
    if not filename or filename.startswith("/") or ".." in filename.split("/"):
        raise EvidenceValidationError("Common Crawl WARC filename is invalid")
    try:
        offset = int(record.get("offset"))
        length = int(record.get("length"))
        http_status = int(record.get("status"))
    except (TypeError, ValueError) as exc:
        raise EvidenceValidationError("Common Crawl numeric metadata is invalid") from exc
    if offset < 0 or length <= 0:
        raise EvidenceValidationError("Common Crawl byte range is invalid")
    captured_at = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(
        tzinfo=timezone.utc
    )
    return {
        "original_target_url": original_url,
        "crawl_collection_index": crawl_index,
        "crawl_timestamp": timestamp,
        "crawl_datetime": captured_at.isoformat().replace("+00:00", "Z"),
        "warc_filename": filename,
        "warc_record_offset": offset,
        "warc_record_length": length,
        "http_status": http_status,
        "mime_type": str(record.get("mime", record.get("mime-detected", ""))),
        "archive_digest": str(record.get("digest", "")),
    }


def validate_range_fetch_response(
    *,
    status_code: int,
    headers: Mapping[str, str],
    body: bytes,
    requested_start: int,
    requested_length: int,
) -> dict[str, Any]:
    """Validate an exact, uncompressed HTTP byte-range response."""

    if requested_start < 0 or requested_length <= 0:
        raise EvidenceValidationError("requested range is invalid")
    if status_code != 206:
        raise EvidenceValidationError("range request did not return HTTP 206")
    normalised_headers = {str(key).lower(): str(value) for key, value in headers.items()}
    if normalised_headers.get("content-encoding", "identity").lower() not in {
        "",
        "identity",
    }:
        raise EvidenceValidationError("compressed range response cannot be byte-validated")
    content_range = normalised_headers.get("content-range", "")
    match = CONTENT_RANGE_RE.fullmatch(content_range.strip())
    if match is None:
        raise EvidenceValidationError("missing or malformed Content-Range")
    actual_start = int(match.group("start"))
    actual_end = int(match.group("end"))
    expected_end = requested_start + requested_length - 1
    if actual_start != requested_start or actual_end != expected_end:
        raise EvidenceValidationError("returned byte range does not match request")
    if len(body) != requested_length:
        raise EvidenceValidationError("range body length does not match Content-Range")
    content_length = normalised_headers.get("content-length")
    if content_length is not None:
        try:
            parsed_content_length = int(content_length)
        except ValueError as exc:
            raise EvidenceValidationError("Content-Length is invalid") from exc
        if parsed_content_length != requested_length:
            raise EvidenceValidationError("Content-Length does not match range body")
    return {
        "range_start": actual_start,
        "range_end": actual_end,
        "range_length": requested_length,
        "resource_length": (
            None if match.group("total") == "*" else int(match.group("total"))
        ),
        "payload_sha256": sha256_bytes(body),
    }


def validate_common_crawl_range_response(
    metadata: Mapping[str, Any],
    *,
    status_code: int,
    headers: Mapping[str, str],
    body: bytes,
) -> dict[str, Any]:
    """Validate a WARC byte-range response against parsed index metadata."""

    return validate_range_fetch_response(
        status_code=status_code,
        headers=headers,
        body=body,
        requested_start=int(metadata["warc_record_offset"]),
        requested_length=int(metadata["warc_record_length"]),
    )


def sanitise_inaccessible_resource(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Retain only identity/access diagnostics for a body that was not fetched."""

    return {
        key: copy.deepcopy(candidate[key])
        for key in (
            "candidate_id",
            "quote_id",
            "canonical_url",
            "fetch_status",
            "http_status",
            "classification",
            "decision_reason",
            "snippet_used_as_evidence",
        )
        if key in candidate
    }


def is_prohibited_retrieval_url(url: str) -> bool:
    """Return true for prohibited paywall/cache/proxy retrieval services."""

    try:
        parts = urlsplit(url)
    except ValueError:
        return True
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return True
    try:
        ipaddress.ip_address(parts.hostname)
    except ValueError:
        pass
    host = parts.hostname.lower().rstrip(".")
    if any(host == banned or host.endswith(f".{banned}") for banned in PROHIBITED_ARCHIVE_OR_PAYWALL_HOSTS):
        return True
    return any(marker in host for marker in PROHIBITED_HOST_MARKERS)


def _normalise_word_tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(
        str.maketrans(
            {
                "\u2018": "'",
                "\u2019": "'",
                "\u201c": '"',
                "\u201d": '"',
                "\u2013": "-",
                "\u2014": "-",
                "\u2212": "-",
            }
        )
    )
    return re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", text.casefold())


def compare_primary_wording(source_text: str, stored_text: str) -> dict[str, Any]:
    """Classify exact normalisation versus a precisely recorded primary variant."""

    source_tokens = _normalise_word_tokens(source_text)
    stored_tokens = _normalise_word_tokens(stored_text)
    if source_tokens == stored_tokens:
        return {
            "classification": "exact_primary_wording",
            "differences": [],
            "source_tokens": source_tokens,
            "stored_tokens": stored_tokens,
        }

    # A small deterministic LCS implementation is sufficient for quotation text
    # and avoids treating editorial bracket insertions as punctuation.
    rows = len(source_tokens) + 1
    cols = len(stored_tokens) + 1
    table = [[0] * cols for _ in range(rows)]
    for i in range(len(source_tokens) - 1, -1, -1):
        for j in range(len(stored_tokens) - 1, -1, -1):
            if source_tokens[i] == stored_tokens[j]:
                table[i][j] = table[i + 1][j + 1] + 1
            else:
                table[i][j] = max(table[i + 1][j], table[i][j + 1])
    differences: list[dict[str, Any]] = []
    i = j = 0
    source_pending: list[str] = []
    stored_pending: list[str] = []

    def flush() -> None:
        nonlocal source_pending, stored_pending
        if source_pending or stored_pending:
            differences.append(
                {
                    "source_tokens": source_pending,
                    "stored_tokens": stored_pending,
                    "kind": (
                        "replacement"
                        if source_pending and stored_pending
                        else "source_only"
                        if source_pending
                        else "stored_editorial_addition"
                    ),
                }
            )
            source_pending = []
            stored_pending = []

    while i < len(source_tokens) and j < len(stored_tokens):
        if source_tokens[i] == stored_tokens[j]:
            flush()
            i += 1
            j += 1
        elif table[i + 1][j] >= table[i][j + 1]:
            source_pending.append(source_tokens[i])
            i += 1
        else:
            stored_pending.append(stored_tokens[j])
            j += 1
    source_pending.extend(source_tokens[i:])
    stored_pending.extend(stored_tokens[j:])
    flush()
    return {
        "classification": "primary_variant",
        "differences": differences,
        "source_tokens": source_tokens,
        "stored_tokens": stored_tokens,
    }


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON for overlay fingerprints."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
