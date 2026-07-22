#!/usr/bin/env python3
"""Render every completed historical-context packet for offline human review.

The program reads only the validated research corpus, uses the same public
formatter as the X posting path, and writes a deterministic UTF-8 text file to
the explicit ``--output`` path.  It does not instantiate the bot, a reply
store, an external provider, or any network client.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from historical_context_formatter import (
    DEFAULT_MAXIMUM_LENGTH,
    DEFAULT_RESEARCH_DIR,
    format_context_reply_public,
    load_and_validate_corpus,
    packet_is_attributed_to_margaret_thatcher,
)
from historical_context_public_source_audit import build_audit


EXPECTED_COMPLETED_PACKET_COUNT = 626
EXPECTED_UNRESOLVED_QUOTE_COUNT = 6
EXPECTED_ATTRIBUTION_ELIGIBLE_COUNT = 610
LONG_REPLY_RAW_CHARACTER_THRESHOLD = 700
REVIEW_KIND = "historical_context_public_render_review"
REVIEW_SCHEMA_VERSION = 1
REVIEW_HEADER = "HISTORICAL CONTEXT PUBLIC RENDER REVIEW"
PRODUCTION_FORMATTER_OPTIONS = {
    "maximum_length": DEFAULT_MAXIMUM_LENGTH,
    "include_meaning": True,
    "include_source": True,
    "include_verification": True,
}
_IMPLEMENTATION_FILENAMES = (
    "historical_context_formatter.py",
    "historical_context_source_roles.py",
    "historical_context_public_source_audit.py",
    "historical_context_public_render_review.py",
)
_REVIEW_OUTPUT_NAME = re.compile(
    r"^historical_context(?:[._-][a-z0-9]+)*[._-]render[._-]review"
    r"(?:[._-][a-z0-9]+)*\.txt$",
    re.I,
)

_BLOCKING_DIAGNOSTIC_FIELDS = (
    "duplicate_canonical_identities",
    "duplicate_canonical_urls",
    "duplicate_raw_public_urls",
    "repeated_archive_document_numbers",
    "public_source_role_leakage",
    "markdown_links",
    "malformed_or_nested_urls",
    "urls_not_on_own_line",
    "identical_sources_under_different_labels",
    "identical_public_entries_with_distinct_identities",
    "identity_conflicts",
    "formatter_projection_mismatches",
)
_GENERIC_CONTEXT = (
    "Context — The surviving attribution does not establish an occasion, "
    "date or immediate historical issue."
)
_GENERIC_MTF_TITLE = re.compile(
    r"^Margaret Thatcher Foundation, Document \d{5,9}$", re.I,
)
_URL_LIKE_PUBLIC_TITLE = re.compile(
    r"^(?:www\.)?[a-z0-9.-]+\.[a-z]{2,}/\S+$", re.I,
)
_MTF_PAGE_TITLE_CHROME = re.compile(
    r"\|\s*Margaret Thatcher Foundation(?:\s*\(Document \d+\))?$", re.I,
)
_ABBREVIATED_PUBLIC_DATE = re.compile(
    r"(?:\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"\.?,?\s+(?:18|19|20)\d{2}\b|\b(?:18|19|20)\d{2}\s+"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\.?\s+\d{1,2}\b)",
    re.I,
)
_EDITORIAL_QUEUE_FLAGS = (
    "IDENTITY_AMBIGUITIES",
    "NO_RELIABLE_SOURCE",
    "MULTIPLE_PUBLIC_SOURCES",
    "URLLESS_PUBLIC_SOURCE",
    "GENERIC_MTF_DOCUMENT_TITLE",
    "REUSED_IDENTITY_DISPLAY_VARIANT",
    "GENERIC_CONTEXT_WITH_PUBLIC_SOURCE",
    "DUPLICATE_FULL_PUBLIC_REPLY",
    "LONG_REPLY",
)


def _clean(value: Any) -> str:
    """Return one stable, whitespace-normalised display scalar."""
    return " ".join(str(value or "").split()).strip()


def _json(value: Any) -> str:
    """Return deterministic one-line JSON for internal diagnostics."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )


def _file_sha256(path: Path) -> str:
    """Return the SHA-256 digest of one file without modifying it."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write_text(path: Path, text: str) -> None:
    """Write one UTF-8/LF review artifact atomically and durably."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
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


def _validate_output_path(
    research_dir: Path,
    output: Path,
    *,
    reference_root: Path | None = None,
    overwrite: bool = False,
) -> Path:
    """Permit only a deliberately named, safely replaceable review artifact."""
    if output.is_symlink():
        raise ValueError("--output must not be a symbolic link")
    research = research_dir.resolve()
    resolved = output.resolve(strict=False)
    if resolved == research or research in resolved.parents:
        raise ValueError("--output must be outside the immutable research directory")
    if not _REVIEW_OUTPUT_NAME.fullmatch(resolved.name):
        raise ValueError(
            "--output filename must identify a historical-context render review"
        )
    root = (
        Path(__file__).resolve().parent
        if reference_root is None else reference_root.resolve()
    )
    protected = {
        (root / "mrsMThatcher.txt").resolve(strict=False),
        (root / "quote_analysis.json").resolve(strict=False),
        *((root / name).resolve(strict=False) for name in _IMPLEMENTATION_FILENAMES),
    }
    if resolved in protected:
        raise ValueError("--output resolves to a protected input or implementation file")
    if resolved.exists():
        if not resolved.is_file():
            raise ValueError("--output must be a regular file")
        if not overwrite:
            raise FileExistsError("--output already exists; pass --overwrite to replace it")
        with resolved.open("r", encoding="utf-8") as handle:
            first_line = handle.readline().rstrip("\r\n")
        if first_line != REVIEW_HEADER:
            raise ValueError(
                "--overwrite may replace only a prior render-review artifact"
            )
    return resolved


def _item_blockers(item: dict[str, Any]) -> list[str]:
    """Return the machine-audit blockers associated with one quote."""
    diagnostics = item["diagnostics"]
    blockers: list[str] = []
    if diagnostics["render_failure"]:
        blockers.append("render_failure")
    for field in _BLOCKING_DIAGNOSTIC_FIELDS:
        if diagnostics[field]:
            blockers.append(field)
    return blockers


def _source_pairs(formatted: dict[str, Any] | None) -> list[tuple[str, str]]:
    """Return the formatter's public title/URL pairs in rendered order."""
    if formatted is None:
        return []
    return [
        (_clean(source.get("title")), _clean(source.get("url")))
        for source in formatted.get("sources", [])
    ]


def _reply_structure_blockers(
    formatted: dict[str, Any] | None,
) -> list[str]:
    """Independently validate the complete public reply envelope."""
    if formatted is None:
        return ["render_failure"]
    text = str(formatted.get("text") or "")
    sections = text.split("\n\n") if text else []
    blockers: list[str] = []
    expected_labels = ["Context"]
    if formatted.get("meaning_included"):
        expected_labels.append("Meaning")
    expected_labels.append("Verification")
    source_count = len(formatted.get("sources", []))
    expected_labels.extend(["Source"] * max(1, source_count))
    actual_labels = [
        section.splitlines()[0].partition(" —")[0]
        for section in sections if section.splitlines()
    ]
    if actual_labels != expected_labels:
        blockers.append("public_section_order_or_count")
    if any(not section.partition(" — ")[2].strip() for section in sections):
        blockers.append("empty_public_section")
    if "\r" in text:
        blockers.append("non_lf_public_text")
    if "\nConfidence —" in f"\n{text}":
        blockers.append("public_confidence_leakage")
    if int(formatted.get("raw_character_count") or -1) != len(text):
        blockers.append("raw_character_count_mismatch")
    if formatted.get("character_count") != formatted.get(
        "weighted_character_count"
    ):
        blockers.append("weighted_character_count_mismatch")
    if int(formatted.get("weighted_character_count") or 0) > int(
        formatted.get("maximum_length") or 0
    ):
        blockers.append("maximum_length_exceeded")
    if not source_count and not text.endswith(
        "Source — No reliable source located"
    ):
        blockers.append("missing_no_reliable_source_fallback")
    if source_count and "Source — No reliable source located" in text:
        blockers.append("fallback_with_real_source")
    return blockers


def _context_line(text: str) -> str:
    """Return the exact Context line from one rendered reply."""
    return next(
        (line for line in text.splitlines() if line.startswith("Context — ")),
        "",
    )


def _has_flag(entry: dict[str, Any], flag: str) -> bool:
    """Match either an exact flag or its deterministic ``FLAG:value`` form."""
    return any(
        value == flag or value.startswith(f"{flag}:")
        for value in entry["flags"]
    )


def _editorial_queue_rows(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarise human-review queues without duplicating hundreds of records."""
    rows: list[dict[str, Any]] = []
    for flag in _EDITORIAL_QUEUE_FLAGS:
        quote_ids = [
            entry["quote_id"] for entry in entries if _has_flag(entry, flag)
        ]
        if quote_ids:
            rows.append({
                "flag": flag,
                "packet_count": len(quote_ids),
                "sample_quote_ids": quote_ids[:20],
                "additional_quote_id_count": max(0, len(quote_ids) - 20),
            })
    return rows


def _record_entry(
    quote_id: str,
    packet: dict[str, Any],
    item: dict[str, Any],
    formatted: dict[str, Any] | None,
) -> dict[str, Any]:
    """Combine exact public output with its internal identity diagnostics."""
    text = "" if formatted is None else str(formatted["text"])
    public_pairs = _source_pairs(formatted)
    audit_pairs = [
        (_clean(source.get("title")), _clean(source.get("url")))
        for source in item["after_deduplication"]["public_sources"]
    ]
    blockers = _item_blockers(item) + _reply_structure_blockers(formatted)
    if formatted is None:
        if "render_failure" not in blockers:
            blockers.append("render_failure")
    elif public_pairs != audit_pairs:
        blockers.append("review_formatter_projection_mismatch")
    for index, group in enumerate(item["canonical_source_groups"]):
        document_number = _clean(group.get("document_number"))
        if not document_number or index >= len(public_pairs):
            continue
        title, url = public_pairs[index]
        if title.count(document_number) != 1:
            blockers.append("public_document_number_label_mismatch")
        if url != (
            f"https://www.margaretthatcher.org/document/{document_number}"
        ):
            blockers.append("public_document_number_url_mismatch")

    flags: list[str] = []
    eligible = packet_is_attributed_to_margaret_thatcher(packet)
    if not eligible:
        flags.append("INELIGIBLE")
    if item["no_reliable_source"]:
        flags.append("NO_RELIABLE_SOURCE")
    if len(public_pairs) > 1:
        flags.append(f"MULTIPLE_PUBLIC_SOURCES:{len(public_pairs)}")
    merged_count = int(item["merged_internal_source_record_count"])
    if merged_count:
        flags.append(f"MERGED_INTERNAL_RECORDS:{merged_count}")
    ambiguities = item["diagnostics"]["identity_ambiguities"]
    if ambiguities:
        flags.append(f"IDENTITY_AMBIGUITIES:{len(ambiguities)}")
    generic_context = _context_line(text) == _GENERIC_CONTEXT
    if generic_context:
        flags.append("GENERIC_CONTEXT")
        if public_pairs:
            flags.append("GENERIC_CONTEXT_WITH_PUBLIC_SOURCE")
        if any(
            {"source_event", "date"}.intersection(
                group.get("public_source", {}).get("claims_supported", [])
            )
            for group in item["canonical_source_groups"]
        ):
            flags.append("GENERIC_CONTEXT_WITH_EVENT_OR_DATE_EVIDENCE")
        if formatted is not None and formatted.get("verification_label") == (
            "Exact wording verified"
        ):
            flags.append("GENERIC_CONTEXT_WITH_EXACT_VERIFICATION")
    if formatted is not None and not formatted.get("meaning_included"):
        flags.append("MEANING_OMITTED")
    if len(text) >= LONG_REPLY_RAW_CHARACTER_THRESHOLD:
        flags.append(f"LONG_REPLY:{len(text)}")
    if any(not url for _title, url in public_pairs):
        flags.append("URLLESS_PUBLIC_SOURCE")
    if any(_GENERIC_MTF_TITLE.fullmatch(title) for title, _url in public_pairs):
        flags.append("GENERIC_MTF_DOCUMENT_TITLE")
    if any(_URL_LIKE_PUBLIC_TITLE.fullmatch(title) for title, _url in public_pairs):
        flags.append("URL_LIKE_PUBLIC_TITLE")
    if any(_MTF_PAGE_TITLE_CHROME.search(title) for title, _url in public_pairs):
        flags.append("MTF_PAGE_TITLE_CHROME")
    if any(_ABBREVIATED_PUBLIC_DATE.search(title) for title, _url in public_pairs):
        flags.append("ABBREVIATED_PUBLIC_DATE")
    if blockers:
        flags.append(f"BLOCKERS:{len(blockers)}")

    return {
        "quote_id": quote_id,
        "quote_text": str(packet["quote_text"]),
        "attribution_eligible": eligible,
        "speaker": _clean(packet.get("speaker")),
        "verification_status": _clean(packet.get("verification_status")),
        "public_text": text,
        "raw_character_count": 0 if formatted is None else int(
            formatted["raw_character_count"]
        ),
        "weighted_character_count": 0 if formatted is None else int(
            formatted["weighted_character_count"]
        ),
        "maximum_length": None if formatted is None else int(
            formatted["maximum_length"]
        ),
        "formatter_version": None if formatted is None else formatted.get(
            "formatter_version"
        ),
        "template_variant": None if formatted is None else formatted.get(
            "template_variant"
        ),
        "meaning_included": None if formatted is None else formatted.get(
            "meaning_included"
        ),
        "meaning_decision_reason": None if formatted is None else formatted.get(
            "meaning_decision_reason"
        ),
        "verification_label": None if formatted is None else formatted.get(
            "verification_label"
        ),
        "internal_source_record_count": item["internal_source_record_count"],
        "internal_source_records": item["internal_source_records"],
        "canonical_source_groups": item["canonical_source_groups"],
        "public_source_count": len(public_pairs),
        "public_sources": [] if formatted is None else formatted.get("sources", []),
        "merged_internal_source_record_count": merged_count,
        "identity_ambiguities": ambiguities,
        "blockers": sorted(set(blockers)),
        "flags": flags,
    }


def build_render_review(
    research_dir: Path = DEFAULT_RESEARCH_DIR,
    *,
    reference_root: Path | None = None,
) -> dict[str, Any]:
    """Build the deterministic all-quote public-render review model."""
    research_dir = research_dir.resolve()
    audit = build_audit(research_dir, reference_root=reference_root)
    packets, unresolved = load_and_validate_corpus(
        research_dir, require_source_role_audit=True,
    )
    entries = [
        _record_entry(
            quote_id,
            packets[quote_id],
            audit["items"][quote_id],
            format_context_reply_public(
                packets[quote_id], **PRODUCTION_FORMATTER_OPTIONS,
            ),
        )
        for quote_id in sorted(packets)
    ]

    output_groups: dict[str, list[str]] = defaultdict(list)
    quote_text_groups: dict[str, list[str]] = defaultdict(list)
    identity_displays: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for entry in entries:
        output_groups[entry["public_text"]].append(entry["quote_id"])
        quote_text_groups[_clean(entry["quote_text"])].append(entry["quote_id"])
        item = audit["items"][entry["quote_id"]]
        for index, group in enumerate(entry["canonical_source_groups"]):
            source_line = (
                item["public_source_lines"][index]
                if index < len(item["public_source_lines"])
                else _clean(group["public_source"].get("title"))
            )
            identity_displays[group["canonical_identity"]].add((
                source_line,
                _clean(group["public_source"].get("url")),
            ))

    duplicate_outputs = [
        {"quote_ids": sorted(quote_ids), "public_text": public_text}
        for public_text, quote_ids in sorted(output_groups.items())
        if len(quote_ids) > 1
    ]
    duplicate_quote_texts = [
        {"quote_ids": sorted(quote_ids), "quote_text": quote_text}
        for quote_text, quote_ids in sorted(quote_text_groups.items())
        if len(quote_ids) > 1
    ]
    identity_display_variants = [
        {
            "canonical_identity": identity,
            "public_displays": [
                {"source_line": source_line, "url": url}
                for source_line, url in sorted(displays)
            ],
        }
        for identity, displays in sorted(identity_displays.items())
        if len(displays) > 1
    ]

    duplicate_output_ids = {
        quote_id for group in duplicate_outputs for quote_id in group["quote_ids"]
    }
    variant_identities = {
        row["canonical_identity"] for row in identity_display_variants
    }
    for entry in entries:
        if entry["quote_id"] in duplicate_output_ids:
            entry["flags"].append("DUPLICATE_FULL_PUBLIC_REPLY")
        if any(
            group["canonical_identity"] in variant_identities
            for group in entry["canonical_source_groups"]
        ):
            entry["flags"].append("REUSED_IDENTITY_DISPLAY_VARIANT")

    editorial_queues = _editorial_queue_rows(entries)

    source_distribution = Counter(entry["public_source_count"] for entry in entries)
    raw_lengths = [entry["raw_character_count"] for entry in entries]
    weighted_lengths = [entry["weighted_character_count"] for entry in entries]
    global_blockers: list[str] = []
    if len(entries) != EXPECTED_COMPLETED_PACKET_COUNT:
        global_blockers.append("completed_packet_count")
    if len(unresolved) != EXPECTED_UNRESOLVED_QUOTE_COUNT:
        global_blockers.append("unresolved_quote_count")
    eligible_count = sum(entry["attribution_eligible"] for entry in entries)
    if eligible_count != EXPECTED_ATTRIBUTION_ELIGIBLE_COUNT:
        global_blockers.append("attribution_eligible_quote_count")
    if not audit["ready"]:
        global_blockers.append("public_source_audit_not_ready")
    item_blocker_count = sum(len(entry["blockers"]) for entry in entries)
    ready = not global_blockers and item_blocker_count == 0

    summary = {
        "review_ready": ready,
        "audit_ready": audit["ready"],
        "completed_packet_count": len(entries),
        "unresolved_quote_count": len(unresolved),
        "attribution_eligible_quote_count": eligible_count,
        "attribution_ineligible_completed_quote_count": len(entries) - eligible_count,
        "render_failure_count": sum(not entry["public_text"] for entry in entries),
        "item_blocker_count": item_blocker_count,
        "global_blockers": global_blockers,
        "editorial_queues_present": bool(editorial_queues),
        "editorial_queue_category_count": len(editorial_queues),
        "source_distribution": {
            str(count): source_distribution[count]
            for count in sorted(source_distribution)
        },
        "packets_with_source_merges": sum(
            bool(entry["merged_internal_source_record_count"]) for entry in entries
        ),
        "merged_internal_source_record_count": sum(
            entry["merged_internal_source_record_count"] for entry in entries
        ),
        "identity_ambiguity_count": sum(
            len(entry["identity_ambiguities"]) for entry in entries
        ),
        "packets_with_identity_ambiguities": sum(
            bool(entry["identity_ambiguities"]) for entry in entries
        ),
        "packets_with_no_reliable_source": sum(
            "NO_RELIABLE_SOURCE" in entry["flags"] for entry in entries
        ),
        "eligible_packets_with_no_reliable_source": sum(
            entry["attribution_eligible"]
            and "NO_RELIABLE_SOURCE" in entry["flags"]
            for entry in entries
        ),
        "packets_with_multiple_public_sources": sum(
            entry["public_source_count"] > 1 for entry in entries
        ),
        "packets_with_generic_context": sum(
            "GENERIC_CONTEXT" in entry["flags"] for entry in entries
        ),
        "packets_with_generic_context_and_public_source": sum(
            "GENERIC_CONTEXT_WITH_PUBLIC_SOURCE" in entry["flags"]
            for entry in entries
        ),
        "packets_with_generic_context_and_event_or_date_evidence": sum(
            "GENERIC_CONTEXT_WITH_EVENT_OR_DATE_EVIDENCE" in entry["flags"]
            for entry in entries
        ),
        "packets_with_generic_context_and_exact_verification": sum(
            "GENERIC_CONTEXT_WITH_EXACT_VERIFICATION" in entry["flags"]
            for entry in entries
        ),
        "packets_with_meaning_omitted": sum(
            "MEANING_OMITTED" in entry["flags"] for entry in entries
        ),
        "packets_with_long_replies": sum(
            entry["raw_character_count"] >= LONG_REPLY_RAW_CHARACTER_THRESHOLD
            for entry in entries
        ),
        "duplicate_full_public_reply_group_count": len(duplicate_outputs),
        "duplicate_quote_text_group_count": len(duplicate_quote_texts),
        "canonical_identity_display_variant_count": len(
            identity_display_variants
        ),
        "packets_with_reused_identity_display_variants": sum(
            "REUSED_IDENTITY_DISPLAY_VARIANT" in entry["flags"]
            for entry in entries
        ),
        "packets_with_urlless_public_sources": sum(
            "URLLESS_PUBLIC_SOURCE" in entry["flags"] for entry in entries
        ),
        "urlless_public_source_entry_count": sum(
            sum(not _clean(source.get("url")) for source in entry["public_sources"])
            for entry in entries
        ),
        "packets_with_generic_mtf_document_titles": sum(
            "GENERIC_MTF_DOCUMENT_TITLE" in entry["flags"] for entry in entries
        ),
        "generic_mtf_document_title_entry_count": sum(
            sum(
                bool(_GENERIC_MTF_TITLE.fullmatch(_clean(source.get("title"))))
                for source in entry["public_sources"]
            )
            for entry in entries
        ),
        "url_like_public_title_count": sum(
            "URL_LIKE_PUBLIC_TITLE" in entry["flags"] for entry in entries
        ),
        "mtf_page_title_chrome_count": sum(
            "MTF_PAGE_TITLE_CHROME" in entry["flags"] for entry in entries
        ),
        "abbreviated_public_date_count": sum(
            "ABBREVIATED_PUBLIC_DATE" in entry["flags"] for entry in entries
        ),
        "raw_character_length": {
            "minimum": min(raw_lengths, default=0),
            "median": statistics.median(raw_lengths) if raw_lengths else 0,
            "maximum": max(raw_lengths, default=0),
        },
        "weighted_character_length": {
            "minimum": min(weighted_lengths, default=0),
            "median": statistics.median(weighted_lengths) if weighted_lengths else 0,
            "maximum": max(weighted_lengths, default=0),
        },
    }
    implementation_root = Path(__file__).resolve().parent
    implementation_file_hashes = {
        name: _file_sha256(implementation_root / name)
        for name in _IMPLEMENTATION_FILENAMES
    }
    source_file_hashes = dict(audit["source_file_hashes"])
    unresolved_path = research_dir / "unresolved_quotes.json"
    source_file_hashes["unresolved_quotes.json"] = _file_sha256(unresolved_path)
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "review_kind": REVIEW_KIND,
        "formatter_options": dict(PRODUCTION_FORMATTER_OPTIONS),
        "summary": summary,
        "source_file_hashes": source_file_hashes,
        "implementation_file_hashes": implementation_file_hashes,
        "editorial_review_queues": editorial_queues,
        "duplicate_full_public_replies": duplicate_outputs,
        "duplicate_quote_texts": duplicate_quote_texts,
        "canonical_identity_display_variants": identity_display_variants,
        "entries": entries,
    }


def render_review_text(review: dict[str, Any]) -> str:
    """Render the review model as stable, scan-friendly plain text."""
    summary = review["summary"]
    entries = review["entries"]
    lines = [
        REVIEW_HEADER,
        "=======================================",
        "",
        f"Review kind: {review['review_kind']}",
        f"Schema version: {review['schema_version']}",
        "Automated validation status: "
        f"{'PASS' if summary['review_ready'] else 'BLOCKED'}",
        "Human editorial review status: PENDING",
        f"Editorial review queues present: "
        f"{'yes' if summary['editorial_queues_present'] else 'no'}",
        "",
        "SUMMARY",
        "-------",
    ]
    for key in (
        "completed_packet_count", "unresolved_quote_count",
        "attribution_eligible_quote_count",
        "attribution_ineligible_completed_quote_count", "render_failure_count",
        "item_blocker_count", "packets_with_source_merges",
        "merged_internal_source_record_count", "identity_ambiguity_count",
        "packets_with_identity_ambiguities",
        "packets_with_no_reliable_source",
        "eligible_packets_with_no_reliable_source",
        "packets_with_multiple_public_sources",
        "packets_with_generic_context",
        "packets_with_generic_context_and_public_source",
        "packets_with_generic_context_and_event_or_date_evidence",
        "packets_with_generic_context_and_exact_verification",
        "packets_with_meaning_omitted",
        "packets_with_long_replies",
        "duplicate_full_public_reply_group_count",
        "duplicate_quote_text_group_count",
        "canonical_identity_display_variant_count",
        "packets_with_reused_identity_display_variants",
        "packets_with_urlless_public_sources",
        "urlless_public_source_entry_count",
        "packets_with_generic_mtf_document_titles",
        "generic_mtf_document_title_entry_count",
        "editorial_queue_category_count",
        "url_like_public_title_count", "mtf_page_title_chrome_count",
        "abbreviated_public_date_count",
    ):
        lines.append(f"{key}: {_json(summary[key])}")
    lines.extend((
        f"source_distribution: {_json(summary['source_distribution'])}",
        f"raw_character_length: {_json(summary['raw_character_length'])}",
        f"weighted_character_length: {_json(summary['weighted_character_length'])}",
        f"formatter_options: {_json(review['formatter_options'])}",
        f"source_file_hashes: {_json(review['source_file_hashes'])}",
        f"implementation_file_hashes: "
        f"{_json(review['implementation_file_hashes'])}",
        "",
        "AUTOMATED BLOCKERS",
        "------------------",
    ))
    blockers = [
        {"quote_id": entry["quote_id"], "blockers": entry["blockers"]}
        for entry in entries if entry["blockers"]
    ]
    if summary["global_blockers"]:
        lines.append(f"global: {_json(summary['global_blockers'])}")
    for blocker in blockers:
        lines.append(_json(blocker))
    if not summary["global_blockers"] and not blockers:
        lines.append("None.")

    lines.extend(("", "EDITORIAL REVIEW QUEUES", "-----------------------"))
    if not review["editorial_review_queues"]:
        lines.append("None.")
    for row in review["editorial_review_queues"]:
        lines.append(_json(row))
    lines.append(
        "Queue members are marked by the same flag in ALL RENDERED QUOTATIONS."
    )

    lines.extend(("", "IDENTITY AMBIGUITY INDEX", "------------------------"))
    ambiguous = [entry for entry in entries if entry["identity_ambiguities"]]
    if not ambiguous:
        lines.append("None.")
    for entry in ambiguous:
        lines.append(_json({
            "quote_id": entry["quote_id"],
            "kinds": [row.get("kind") for row in entry["identity_ambiguities"]],
        }))

    lines.extend(("", "DUPLICATE FULL PUBLIC REPLIES", "-----------------------------"))
    duplicates = review["duplicate_full_public_replies"]
    if not duplicates:
        lines.append("None.")
    for group in duplicates:
        lines.append(_json({"quote_ids": group["quote_ids"]}))

    lines.extend(("", "CANONICAL IDENTITY DISPLAY VARIANTS", "-----------------------------------"))
    variants = review["canonical_identity_display_variants"]
    if not variants:
        lines.append("None.")
    for row in variants:
        lines.append(_json(row))

    lines.extend(("", "LONGEST PUBLIC REPLIES", "----------------------"))
    for entry in sorted(
        entries,
        key=lambda row: (-row["raw_character_count"], row["quote_id"]),
    )[:20]:
        lines.append(_json({
            "quote_id": entry["quote_id"],
            "raw_character_count": entry["raw_character_count"],
            "weighted_character_count": entry["weighted_character_count"],
        }))

    lines.extend(("", "ALL RENDERED QUOTATIONS", "======================="))
    for index, entry in enumerate(entries, start=1):
        lines.extend((
            "",
            "=" * 80,
            f"RECORD {index:04d} OF {len(entries):04d}",
            f"Quote ID: {entry['quote_id']}",
            f"Quotation: {_json(entry['quote_text'])}",
            f"Speaker: {_json(entry['speaker'])}",
            f"Attribution eligible: {_json(entry['attribution_eligible'])}",
            f"Verification status: {_json(entry['verification_status'])}",
            f"Formatter version: {_json(entry['formatter_version'])}",
            f"Template variant: {_json(entry['template_variant'])}",
            f"Meaning included: {_json(entry['meaning_included'])}",
            f"Meaning decision: {_json(entry['meaning_decision_reason'])}",
            f"Verification label: {_json(entry['verification_label'])}",
            f"Raw/weighted/max characters: {entry['raw_character_count']}/"
            f"{entry['weighted_character_count']}/{entry['maximum_length']}",
            f"Internal records -> canonical groups -> public sources: "
            f"{entry['internal_source_record_count']} -> "
            f"{len(entry['canonical_source_groups'])} -> "
            f"{entry['public_source_count']}",
            f"Flags: {_json(entry['flags'])}",
            f"Blockers: {_json(entry['blockers'])}",
            "",
            "INTERNAL SOURCE RECORDS",
        ))
        if not entry["internal_source_records"]:
            lines.append("None.")
        for record in entry["internal_source_records"]:
            lines.append(_json({
                "source_id": record.get("source_id"),
                "canonical_identity": record.get("canonical_identity"),
                "identity_basis": record.get("identity_basis"),
                "title": record.get("title"),
                "raw_url": record.get("url"),
                "canonical_url": record.get("canonical_url"),
                "document_numbers": record.get("document_numbers", []),
                "roles": record.get("internal_roles", []),
                "evidence_targets": record.get("evidence_targets", []),
            }))
        lines.extend(("", "CANONICAL PUBLIC SOURCE GROUPS"))
        if not entry["canonical_source_groups"]:
            lines.append("None.")
        for group in entry["canonical_source_groups"]:
            lines.append(_json({
                "canonical_identity": group.get("canonical_identity"),
                "identity_basis": group.get("identity_basis"),
                "source_ids": group.get("source_ids", []),
                "source_record_count": group.get("source_record_count"),
                "public_title": group.get("public_source", {}).get("title"),
                "public_url": group.get("public_source", {}).get("url"),
            }))
        lines.extend(("", "IDENTITY AMBIGUITIES"))
        if not entry["identity_ambiguities"]:
            lines.append("None.")
        for ambiguity in entry["identity_ambiguities"]:
            lines.append(_json(ambiguity))
        lines.extend(("", "PUBLIC REPLY (VERBATIM)", "-----------------------"))
        lines.extend(entry["public_text"].splitlines())
        lines.append("END PUBLIC REPLY")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Write the deterministic offline all-quote review artifact."""
    parser = argparse.ArgumentParser(
        description="Render every public historical-context reply for review",
    )
    parser.add_argument("--research-dir", type=Path, default=DEFAULT_RESEARCH_DIR)
    parser.add_argument(
        "--reference-root", type=Path,
        help="directory containing copied mrsMThatcher.txt and quote_analysis.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--overwrite", action="store_true",
        help="replace an existing artifact only when it has the expected header",
    )
    args = parser.parse_args(argv)
    output = _validate_output_path(
        args.research_dir,
        args.output,
        reference_root=args.reference_root,
        overwrite=args.overwrite,
    )
    review = build_render_review(
        args.research_dir, reference_root=args.reference_root,
    )
    text = render_review_text(review)
    _atomic_write_text(output, text)
    print(_json({
        "output": str(output),
        "review_ready": review["summary"]["review_ready"],
        "summary": review["summary"],
    }))
    return 0 if review["summary"]["review_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
