#!/usr/bin/env python3
"""Review historical-context MTF candidates against official archive pages.

The Margaret Thatcher Foundation currently places an anti-bot challenge in
front of direct command-line requests.  This diagnostic therefore retrieves
the Foundation's HTML through a configurable read-only text transport, then
checks the original page's canonical URL, author, date, title and transcript
text.  It records hashes and compact comparisons, never full page text, and
never changes evidence roles or production state.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable

from bs4 import BeautifulSoup

from historical_context_evidence_truth_audit import (
    DEFAULT_HISTORY_PATH,
    build_audit as build_truth_audit,
)
from historical_context_formatter import DEFAULT_RESEARCH_DIR, load_and_validate_corpus


AUDIT_KIND = "historical_context_mtf_primary_page_review"
AUDIT_SCHEMA_VERSION = 1
DEFAULT_WORKERS = 1
DEFAULT_REQUEST_DELAY = 1.25
DEFAULT_READER_BASE = (
    "https://r.jina.ai/http://www.margaretthatcher.org/document/"
)
OFFICIAL_BASE = "https://www.margaretthatcher.org/document/"
_DOCUMENT_NUMBER = re.compile(r"\d{5,9}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_TOKEN = re.compile(r"[a-z0-9]+")
_EVENT_STOPWORDS = {
    "a", "an", "and", "at", "by", "for", "from", "in", "of", "on",
    "speech", "statement", "the", "to", "with",
}
_UNKNOWN_EVENT = re.compile(
    r"^(?:unknown|not (?:known|established|located|available)|unresolved|"
    r"none|n/?a|undated)(?:\b|\s*\()",
    re.I,
)
_SUCCESS_STATUSES = {
    "no_machine_detected_discrepancy",
    "manual_review_required",
}
_NOT_FOUND_STATUS = "official_document_not_found"
_DATE_COMPARISONS = {
    "match",
    "mismatch",
    "packet_date_not_exact",
    "official_date_missing",
}
_IDENTITY_PROVENANCE = {
    "accepted", "packet_locator_candidate", "lead", "public",
}
_SCOPES = {
    "priority_same_document_event_or_date",
    "remaining_precise_mtf_candidate",
}
_COMPACT_PAGE_FIELDS = {
    "document_number",
    "official_url",
    "canonical_url",
    "canonical_url_matches",
    "author",
    "date",
    "title",
    "transported_page_sha256",
    "article_text_sha256",
    "article_text_character_count",
}


class OfficialDocumentNotFoundError(RuntimeError):
    """The transport succeeded but the official archive has no such document."""

    def __init__(
        self,
        document_number: str,
        *,
        canonical_url: str,
        transported_page_sha256: str,
    ) -> None:
        """Record the missing document's canonical identity and page hash."""
        super().__init__(
            f"official archive document does not exist: {document_number}"
        )
        self.document_number = document_number
        self.official_url = f"{OFFICIAL_BASE}{document_number}"
        self.canonical_url = canonical_url
        self.transported_page_sha256 = transported_page_sha256


def _clean(value: Any) -> str:
    """Return one whitespace-normalised scalar."""
    return " ".join(str(value or "").split()).strip()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _tokens(value: Any) -> list[str]:
    """Tokenise typography-normalised prose for conservative comparison."""
    text = unicodedata.normalize("NFKC", html.unescape(str(value or "")))
    return _TOKEN.findall(text.casefold())


def _sequence_coverage(needle: list[str], haystack: list[str]) -> float:
    """Return longest contiguous token coverage of the candidate quotation."""
    if not needle or not haystack:
        return 0.0
    match = SequenceMatcher(None, needle, haystack, autojunk=False).find_longest_match()
    return round(match.size / len(needle), 6)


def _event_similarity(left: Any, right: Any) -> float:
    """Return a diagnostic token overlap, not an evidence adjudication."""
    left_tokens = set(_tokens(left)) - _EVENT_STOPWORDS
    right_tokens = set(_tokens(right)) - _EVENT_STOPWORDS
    if not left_tokens or not right_tokens:
        return 0.0
    return round(len(left_tokens & right_tokens) / len(left_tokens | right_tokens), 6)


def _known_event(value: Any) -> bool:
    """Return whether a packet makes a substantive source-event claim."""
    text = _clean(value)
    return bool(text and not _UNKNOWN_EVENT.match(text))


def _normalise_date(value: Any) -> str:
    """Normalise the date forms present in packet metadata to ISO where exact."""
    text = _clean(value)
    for pattern in ("%Y-%m-%d", "%d %B %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            pass
    return ""


def parse_official_page(document_number: str, page_bytes: bytes) -> dict[str, Any]:
    """Extract compact, hash-bound metadata from one transported official page."""
    if not _DOCUMENT_NUMBER.fullmatch(document_number):
        raise ValueError("invalid Margaret Thatcher Foundation document number")
    soup = BeautifulSoup(page_bytes, "lxml")
    canonical_node = soup.find("link", rel="canonical")
    canonical = _clean(canonical_node.get("href") if canonical_node else "")
    generic_heading = soup.find("h1")
    html_title = soup.find("title")
    not_found_labels = {
        _clean(node.get_text(" ", strip=True)).casefold()
        for node in (generic_heading, html_title)
        if node is not None
    }
    if any(
        label == "page not found" or label.startswith("page not found |")
        for label in not_found_labels
    ):
        raise OfficialDocumentNotFoundError(
            document_number,
            canonical_url=canonical,
            transported_page_sha256=_sha256(page_bytes),
        )
    time_node = soup.select_one(".docdate time")
    author_node = soup.select_one(".docauthor")
    title_node = soup.select_one("h1.doctitle")
    article_node = soup.select_one("article.node-archive-document")
    article_text = _clean(
        article_node.get_text(" ", strip=True) if article_node is not None else ""
    )
    expected_url = f"{OFFICIAL_BASE}{document_number}"
    if not article_text or not title_node:
        raise RuntimeError(f"official archive document was not extracted: {document_number}")
    return {
        "document_number": document_number,
        "official_url": expected_url,
        "canonical_url": canonical,
        "canonical_url_matches": canonical.rstrip("/") == expected_url,
        "author": _clean(author_node.get_text(" ", strip=True) if author_node else ""),
        "date": _clean(time_node.get("datetime") if time_node else ""),
        "title": _clean(title_node.get_text(" ", strip=True)),
        "transported_page_sha256": _sha256(page_bytes),
        "article_text_sha256": _sha256(article_text.encode("utf-8")),
        "article_text_character_count": len(article_text),
        "_article_text": article_text,
    }


def fetch_official_page(
    document_number: str,
    *,
    reader_base: str = DEFAULT_READER_BASE,
    timeout: float = 30.0,
    attempts: int = 3,
) -> dict[str, Any]:
    """Fetch one page through the read-only transport with bounded retries."""
    if not _DOCUMENT_NUMBER.fullmatch(document_number):
        raise ValueError("invalid Margaret Thatcher Foundation document number")
    last_error: BaseException | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(
            f"{reader_base}{document_number}",
            headers={
                "Accept": "text/html",
                "Connection": "close",
                "User-Agent": "mrsMThatcher-offline-source-review/1",
                "X-Return-Format": "html",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                page = response.read()
            return parse_official_page(document_number, page)
        except OfficialDocumentNotFoundError:
            # A successful transport of the archive's deterministic 404 page
            # is evidence about identity, not a transient condition.
            raise
        except urllib.error.HTTPError as exc:
            last_error = exc
            retry_after = (exc.headers or {}).get("Retry-After")
            exc.close()
            if attempt + 1 < attempts:
                try:
                    delay = float(retry_after) if retry_after is not None else 0.0
                except ValueError:
                    delay = 0.0
                time.sleep(min(60.0, max(delay, 2.0 ** attempt)))
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2.0 ** attempt)
    raise RuntimeError(
        f"failed to retrieve official archive document {document_number}: {last_error}"
    )


def _document_sets(row: dict[str, Any]) -> dict[str, set[str]]:
    return {
        "accepted": set(row.get("accepted_document_numbers", [])),
        "packet_locator_candidate": set(
            row.get("packet_locator_candidate_document_numbers", [])
        ),
        "lead": set(row.get("lead_document_numbers", [])),
        "public": set(row.get("public_document_numbers", [])),
    }


def _validate_prior_review(
    prior_review: dict[str, Any],
    *,
    truth: dict[str, Any],
    reader_base: str,
    association_keys: set[tuple[str, str]],
    document_numbers: set[str],
) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    """Validate a resume artifact before trusting its hash-bound comparisons."""
    if (
        not isinstance(prior_review, dict)
        or prior_review.get("schema_version") != AUDIT_SCHEMA_VERSION
        or prior_review.get("audit_kind") != AUDIT_KIND
        or prior_review.get("input_hashes") != truth.get("input_hashes")
        or not isinstance(prior_review.get("interpretation"), dict)
        or prior_review["interpretation"].get("transport") != reader_base
        or not isinstance(prior_review.get("official_pages"), dict)
        or not isinstance(prior_review.get("records"), list)
    ):
        raise RuntimeError("prior MTF review is incompatible with current inputs")

    pages = prior_review["official_pages"]
    if set(pages) - document_numbers:
        raise RuntimeError("prior MTF review contains out-of-scope official pages")
    for number, page in pages.items():
        if (
            not _DOCUMENT_NUMBER.fullmatch(str(number or ""))
            or not isinstance(page, dict)
            or set(page) != _COMPACT_PAGE_FIELDS
            or page.get("document_number") != number
            or page.get("official_url") != f"{OFFICIAL_BASE}{number}"
            or type(page.get("canonical_url_matches")) is not bool
            or not isinstance(page.get("canonical_url"), str)
            or not isinstance(page.get("author"), str)
            or not isinstance(page.get("date"), str)
            or not str(page.get("title") or "").strip()
            or not _HEX64.fullmatch(str(page.get("transported_page_sha256") or ""))
            or not _HEX64.fullmatch(str(page.get("article_text_sha256") or ""))
            or type(page.get("article_text_character_count")) is not int
            or page["article_text_character_count"] <= 0
        ):
            raise RuntimeError(f"prior MTF review page is invalid: {number}")

    records: dict[tuple[str, str], dict[str, Any]] = {}
    for record in prior_review["records"]:
        if not isinstance(record, dict):
            raise RuntimeError("prior MTF review record is invalid")
        key = (record.get("quote_id"), record.get("document_number"))
        status = record.get("status")
        provenance = record.get("identity_provenance")
        if (
            key not in association_keys
            or key in records
            or record.get("scope") not in _SCOPES
            or not isinstance(provenance, list)
            or not provenance
            or not set(provenance) <= _IDENTITY_PROVENANCE
            or len(provenance) != len(set(provenance))
            or status not in _SUCCESS_STATUSES | {
                "page_retrieval_failed", _NOT_FOUND_STATUS,
            }
        ):
            raise RuntimeError("prior MTF review record is invalid")
        number = str(key[1])
        page = pages.get(number)
        if status == "page_retrieval_failed":
            if page is not None or not str(record.get("error") or "").strip():
                raise RuntimeError("prior MTF review failed record is inconsistent")
        elif status == _NOT_FOUND_STATUS:
            if (
                page is not None
                or record.get("official_url") != f"{OFFICIAL_BASE}{number}"
                or not isinstance(record.get("canonical_url"), str)
                or type(record.get("canonical_url_matches")) is not bool
                or record.get("official_title") != "Page not found"
                or not _HEX64.fullmatch(
                    str(record.get("transported_page_sha256") or "")
                )
                or not str(record.get("error") or "").strip()
            ):
                raise RuntimeError(
                    "prior MTF review not-found record is inconsistent"
                )
        elif (
            page is None
            or record.get("official_url") != page["official_url"]
            or record.get("canonical_url") != page["canonical_url"]
            or record.get("canonical_url_matches")
            is not page["canonical_url_matches"]
            or record.get("official_author") != page["author"]
            or record.get("official_title") != page["title"]
            or record.get("official_date") != page["date"]
            or record.get("transported_page_sha256")
            != page["transported_page_sha256"]
            or record.get("article_text_sha256") != page["article_text_sha256"]
            or record.get("date_comparison") not in _DATE_COMPARISONS
            or record.get("event_comparison") not in {
                "packet_event_not_known", "overlap", "no_token_overlap",
            }
            or type(record.get("author_matches_margaret_thatcher")) is not bool
            or type(record.get("event_title_token_similarity")) not in {int, float}
            or not 0.0 <= record["event_title_token_similarity"] <= 1.0
            or type(record.get("quotation_longest_contiguous_token_coverage"))
            not in {int, float}
            or not 0.0
            <= record["quotation_longest_contiguous_token_coverage"]
            <= 1.0
        ):
            raise RuntimeError("prior MTF review successful record is inconsistent")
        elif status in _SUCCESS_STATUSES:
            event_comparison = record["event_comparison"]
            event_similarity = record["event_title_token_similarity"]
            expected_status = (
                "no_machine_detected_discrepancy"
                if record["quotation_longest_contiguous_token_coverage"] >= 0.9
                and record["canonical_url_matches"]
                and record["author_matches_margaret_thatcher"]
                and record["date_comparison"] in {
                    "match", "packet_date_not_exact",
                }
                and event_comparison != "no_token_overlap"
                else "manual_review_required"
            )
            if (
                status != expected_status
                or (event_comparison == "overlap" and event_similarity <= 0.0)
                or (event_comparison == "no_token_overlap" and event_similarity != 0.0)
            ):
                raise RuntimeError(
                    "prior MTF review successful record status is inconsistent"
                )
        records[key] = record
    if set(records) != association_keys:
        raise RuntimeError("prior MTF review candidate identities differ")
    return pages, {
        key: record
        for key, record in records.items()
        if record["status"] in _SUCCESS_STATUSES | {_NOT_FOUND_STATUS}
    }


def build_review(
    research_dir: Path = DEFAULT_RESEARCH_DIR,
    history_path: Path = DEFAULT_HISTORY_PATH,
    *,
    reader_base: str = DEFAULT_READER_BASE,
    workers: int = DEFAULT_WORKERS,
    request_delay: float = DEFAULT_REQUEST_DELAY,
    prior_review: dict[str, Any] | None = None,
    page_fetcher: Callable[..., dict[str, Any]] = fetch_official_page,
) -> dict[str, Any]:
    """Check every precise MTF candidate without changing evidence admission."""
    if workers not in {1, 2, 3, 4}:
        raise ValueError("workers must be from 1 to 4")
    if not 0.0 <= request_delay <= 10.0:
        raise ValueError("request_delay must be from 0 to 10 seconds")
    truth = build_truth_audit(research_dir, history_path)
    packets, _unresolved = load_and_validate_corpus(
        research_dir,
        require_source_role_audit=True,
    )
    candidate_rows = truth["records"]["precise_mtf_identities"]
    priority_ids = {
        row["quote_id"]
        for row in truth["records"][
            "eligible_same_document_event_or_date_priorities"
        ]
    }
    document_numbers = sorted({
        number
        for row in candidate_rows
        for numbers in _document_sets(row).values()
        for number in numbers
    })
    association_keys = {
        (row["quote_id"], number)
        for row in candidate_rows
        for numbers in _document_sets(row).values()
        for number in numbers
    }
    if prior_review is None:
        prior_pages: dict[str, dict[str, Any]] = {}
        prior_records: dict[tuple[str, str], dict[str, Any]] = {}
    else:
        prior_pages, prior_records = _validate_prior_review(
            prior_review,
            truth=truth,
            reader_base=reader_base,
            association_keys=association_keys,
            document_numbers=set(document_numbers),
        )
    pages: dict[str, dict[str, Any]] = {}
    page_errors: dict[str, str] = {}
    new_not_found_pages: dict[str, dict[str, Any]] = {}

    def retrieve(number: str) -> dict[str, Any]:
        if request_delay:
            time.sleep(request_delay)
        return page_fetcher(number, reader_base=reader_base)

    prior_not_found_numbers = {
        number
        for (_quote_id, number), record in prior_records.items()
        if record["status"] == _NOT_FOUND_STATUS
    }
    missing_document_numbers = [
        number for number in document_numbers
        if number not in prior_pages and number not in prior_not_found_numbers
    ]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = {
            executor.submit(retrieve, number): number
            for number in missing_document_numbers
        }
        for future in as_completed(pending):
            number = pending[future]
            try:
                pages[number] = future.result()
            except OfficialDocumentNotFoundError as exc:
                new_not_found_pages[number] = {
                    "document_number": number,
                    "official_url": exc.official_url,
                    "canonical_url": exc.canonical_url,
                    "canonical_url_matches": (
                        exc.canonical_url.rstrip("/") == exc.official_url
                    ),
                    "official_title": "Page not found",
                    "transported_page_sha256": exc.transported_page_sha256,
                    "error": _clean(exc),
                }
            except BaseException as exc:
                page_errors[number] = f"{type(exc).__name__}: {_clean(exc)}"

    records: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    for row in candidate_rows:
        packet = packets[row["quote_id"]]
        document_sets = _document_sets(row)
        for number in sorted(set().union(*document_sets.values())):
            page = pages.get(number)
            prior_record = prior_records.get((row["quote_id"], number))
            provenance = sorted(
                name for name, numbers in document_sets.items() if number in numbers
            )
            scope = (
                "priority_same_document_event_or_date"
                if row["quote_id"] in priority_ids
                else "remaining_precise_mtf_candidate"
            )
            if (
                page is None
                and prior_record is not None
                and prior_record["status"] == _NOT_FOUND_STATUS
            ):
                record = dict(prior_record)
                record.update({
                    "quote_id": row["quote_id"],
                    "document_number": number,
                    "scope": scope,
                    "identity_provenance": provenance,
                    "status": _NOT_FOUND_STATUS,
                })
            elif page is None and number in new_not_found_pages:
                not_found = new_not_found_pages[number]
                record = {
                    "quote_id": row["quote_id"],
                    "document_number": number,
                    "scope": scope,
                    "identity_provenance": provenance,
                    "status": _NOT_FOUND_STATUS,
                    "official_url": not_found["official_url"],
                    "canonical_url": not_found["canonical_url"],
                    "canonical_url_matches": not_found["canonical_url_matches"],
                    "official_title": not_found["official_title"],
                    "transported_page_sha256": not_found[
                        "transported_page_sha256"
                    ],
                    "error": not_found["error"],
                }
            elif page is None and prior_record is not None:
                record = dict(prior_record)
                title_similarity = _event_similarity(
                    packet.get("source_event"), record.get("official_title")
                )
                event_comparison = (
                    "packet_event_not_known"
                    if not _known_event(packet.get("source_event"))
                    else "overlap"
                    if title_similarity > 0.0
                    else "no_token_overlap"
                )
                packet_date = _normalise_date(packet.get("date"))
                official_date = _normalise_date(record.get("official_date"))
                date_comparison = (
                    "match" if packet_date and packet_date == official_date
                    else "mismatch" if packet_date and official_date
                    else "packet_date_not_exact" if not packet_date
                    else "official_date_missing"
                )
                record.update({
                    "quote_text": packet["quote_text"],
                    "scope": scope,
                    "identity_provenance": provenance,
                    "packet_source_event": _clean(packet.get("source_event")),
                    "event_title_token_similarity": title_similarity,
                    "event_comparison": event_comparison,
                    "packet_date": _clean(packet.get("date")),
                    "date_comparison": date_comparison,
                })
                if (
                    event_comparison == "no_token_overlap"
                    or date_comparison not in {"match", "packet_date_not_exact"}
                ):
                    record["status"] = "manual_review_required"
            elif page is None:
                record = {
                    "quote_id": row["quote_id"],
                    "document_number": number,
                    "scope": scope,
                    "identity_provenance": provenance,
                    "status": "page_retrieval_failed",
                    "error": page_errors.get(number, "page unavailable"),
                }
            else:
                quote_tokens = _tokens(packet["quote_text"])
                page_tokens = _tokens(page["_article_text"])
                coverage = _sequence_coverage(quote_tokens, page_tokens)
                packet_date = _normalise_date(packet.get("date"))
                official_date = _normalise_date(page.get("date"))
                date_comparison = (
                    "match" if packet_date and packet_date == official_date
                    else "mismatch" if packet_date and official_date
                    else "packet_date_not_exact" if not packet_date
                    else "official_date_missing"
                )
                title_similarity = _event_similarity(
                    packet.get("source_event"), page.get("title")
                )
                event_comparison = (
                    "packet_event_not_known"
                    if not _known_event(packet.get("source_event"))
                    else "overlap"
                    if title_similarity > 0.0
                    else "no_token_overlap"
                )
                author_matches = page["author"].casefold() == "margaret thatcher"
                strong_quote_match = coverage >= 0.9
                metadata_consistent = (
                    page["canonical_url_matches"]
                    and author_matches
                    and date_comparison in {"match", "packet_date_not_exact"}
                    and event_comparison != "no_token_overlap"
                )
                status = (
                    "no_machine_detected_discrepancy"
                    if strong_quote_match and metadata_consistent
                    else "manual_review_required"
                )
                record = {
                    "quote_id": row["quote_id"],
                    "quote_text": packet["quote_text"],
                    "document_number": number,
                    "scope": scope,
                    "identity_provenance": provenance,
                    "status": status,
                    "official_url": page["official_url"],
                    "canonical_url": page["canonical_url"],
                    "canonical_url_matches": page["canonical_url_matches"],
                    "official_author": page["author"],
                    "author_matches_margaret_thatcher": author_matches,
                    "official_title": page["title"],
                    "packet_source_event": _clean(packet.get("source_event")),
                    "event_title_token_similarity": title_similarity,
                    "event_comparison": event_comparison,
                    "official_date": page["date"],
                    "packet_date": _clean(packet.get("date")),
                    "date_comparison": date_comparison,
                    "quotation_longest_contiguous_token_coverage": coverage,
                    "transported_page_sha256": page["transported_page_sha256"],
                    "article_text_sha256": page["article_text_sha256"],
                }
            status = record["status"]
            status_counts[status] = status_counts.get(status, 0) + 1
            records.append(record)

    compact_pages = dict(prior_pages)
    compact_pages.update({
        number: {key: value for key, value in page.items() if key != "_article_text"}
        for number, page in sorted(pages.items())
    })
    all_not_found_pages = {
        number: {
            key: record[key]
            for key in (
                "document_number", "official_url", "canonical_url",
                "canonical_url_matches", "official_title",
                "transported_page_sha256", "error",
            )
        }
        for (_quote_id, number), record in sorted(prior_records.items())
        if record["status"] == _NOT_FOUND_STATUS
    }
    all_not_found_pages.update(new_not_found_pages)
    priority_records = [
        record for record in records
        if record["scope"] == "priority_same_document_event_or_date"
    ]
    remaining_records = [
        record for record in records
        if record["scope"] == "remaining_precise_mtf_candidate"
    ]
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "audit_kind": AUDIT_KIND,
        "interpretation": {
            "status": "diagnostic_primary_page_comparison",
            "transport": reader_base,
            "official_canonical_urls_required": True,
            "automatic_evidence_promotion_authorised": False,
            "machine_status_is_not_a_semantic_adjudication": True,
        },
        "input_hashes": truth["input_hashes"],
        "counts": {
            "candidate_packet_count": len(candidate_rows),
            "priority_packet_count": len(priority_ids),
            "unique_document_count": len(document_numbers),
            "page_retrieval_success_count": len(compact_pages),
            "page_retrieval_reused_count": len(prior_pages),
            "page_retrieval_new_success_count": len(pages),
            "page_retrieval_failure_count": len(page_errors),
            "official_document_not_found_count": len(all_not_found_pages),
            "official_document_not_found_reused_count": len(
                prior_not_found_numbers
            ),
            "official_document_not_found_new_count": len(new_not_found_pages),
            "packet_document_comparison_count": len(records),
            "priority_comparison_count": len(priority_records),
            "remaining_comparison_count": len(remaining_records),
            "status_counts": dict(sorted(status_counts.items())),
        },
        "page_errors": dict(sorted(page_errors.items())),
        "official_documents_not_found": dict(sorted(all_not_found_pages.items())),
        "official_pages": compact_pages,
        "records": records,
    }


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _validated_output_path(output: Path, research_dir: Path, overwrite: bool) -> Path:
    if output.is_symlink():
        raise ValueError("--output must not be a symbolic link")
    resolved = output.resolve(strict=False)
    research = research_dir.resolve()
    protected = {
        (Path(__file__).resolve().parent / "mrsMThatcher.control.json").resolve(),
        (Path(__file__).resolve().parent / "historical_context_reply_history.json").resolve(),
    }
    if resolved in protected or resolved == research or research in resolved.parents:
        raise ValueError("--output must be outside production and immutable inputs")
    if resolved.exists():
        if not overwrite:
            raise FileExistsError("--output already exists; pass --overwrite")
        try:
            current = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("--overwrite may replace only a prior review artifact") from exc
        if not isinstance(current, dict) or current.get("audit_kind") != AUDIT_KIND:
            raise ValueError("--overwrite may replace only a prior review artifact")
    return resolved


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical_json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    """Run the guarded MTF primary-page review command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-dir", type=Path, default=DEFAULT_RESEARCH_DIR)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY_PATH)
    parser.add_argument("--reader-base", default=DEFAULT_READER_BASE)
    parser.add_argument(
        "--workers", type=int, choices=(1, 2, 3, 4), default=DEFAULT_WORKERS
    )
    parser.add_argument(
        "--request-delay", type=float, default=DEFAULT_REQUEST_DELAY
    )
    parser.add_argument("--resume-from", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    output = _validated_output_path(args.output, args.research_dir, args.overwrite)
    prior_review = None
    if args.resume_from is not None:
        prior_review = json.loads(args.resume_from.read_text(encoding="utf-8"))
    review = build_review(
        args.research_dir,
        args.history,
        reader_base=args.reader_base,
        workers=args.workers,
        request_delay=args.request_delay,
        prior_review=prior_review,
    )
    _atomic_write(output, review)
    print(json.dumps({"output": str(output), **review["counts"]}, sort_keys=True))
    return 0 if review["counts"]["page_retrieval_failure_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
