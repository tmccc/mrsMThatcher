#!/usr/bin/env python3
"""Audit public historical-context source rendering without changing evidence.

The audit reads the validated canonical corpus and source-role sidecar, renders
every completed packet through the real public formatter, and records both the
internal evidence identities and the concise public result.  It performs no
network access and writes only to the path explicitly supplied with
``--output``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from historical_context_formatter import (
    DEFAULT_RESEARCH_DIR,
    atomic_write_json,
    format_context_reply_public,
    load_and_validate_corpus,
    packet_is_attributed_to_margaret_thatcher,
    quote_text_hash,
)
from historical_context_packet_corrections import PACKET_CORRECTIONS_FILENAME
from historical_context_source_curated_evidence import CURATED_EVIDENCE_FILENAME
from historical_context_source_roles import (
    AUDIT_FILENAME,
    canonical_source_identity,
    canonical_source_url,
    public_source_identity_diagnostics,
)


AUDIT_SCHEMA_VERSION = 1
AUDIT_KIND = "historical_context_public_source_deduplication_audit"
EXPECTED_COMPLETED_PACKET_COUNT = 627
EXPECTED_UNRESOLVED_QUOTE_COUNT = 5
EXPECTED_ATTRIBUTION_ELIGIBLE_COUNT = 611

_DISPLAY_ROLE_EXCLUSIONS = frozenset({
    "secondary_recollection", "discovery_only", "rejected_irrelevant",
})
_EVIDENCE_RECORD_SOURCE_TYPES = frozenset({
    "canonical_stable_locator",
    "grounded_web_source",
    "recovered_provider_citation",
})
_ROLE_PRIORITY = {
    "wording_verification": 0,
    "attribution_support": 1,
    "source_event_support": 2,
    "historical_context_support": 3,
}
_MARKDOWN_LINK = re.compile(r"\[[^\]\n]+\]\(https?://[^)\n]+\)", re.I)
_ROLE_LEAKAGE = re.compile(
    r"(?im)^(?:source|sources|secondary recollection)\s+\([^\n)]*\)\s*[—-]|"
    r"\b(?:wording[_ ]verification|attribution[_ ]support|"
    r"source[_ ]event[_ ]support|historical[_ ]context[_ ]support)\b"
)
_NESTED_URL = re.compile(
    r"https?://[^\s]*\[|https?://(?:https?://)|https?://[^\s]*/https?://",
    re.I,
)
_URL_SCHEME = re.compile(r"https?://", re.I)
_RAW_URL_LINE = re.compile(r"https?://\S+", re.I)
_MTF_DOCUMENT = re.compile(
    r"(?:margaretthatcher\.org/document/|\bdocument\s+)(\d{5,9})\b",
    re.I,
)


def _clean(value: Any) -> str:
    """Return one whitespace-normalised string."""
    return " ".join(str(value or "").split()).strip()


def _canonical_json(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON bytes for hashing."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _json_sha256(value: Any) -> str:
    """Hash a JSON-compatible value deterministically."""
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    """Hash one file without modifying it."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _identity_excess(values: list[str]) -> tuple[list[str], int]:
    """Return duplicated non-empty values and their excess-record count."""
    counts = Counter(value for value in values if value)
    duplicated = sorted(value for value, count in counts.items() if count > 1)
    return duplicated, sum(counts[value] - 1 for value in duplicated)


def _document_numbers(source: dict[str, Any]) -> list[str]:
    """Extract MTF document numbers from one public source projection."""
    text = " ".join((
        _clean(source.get("title")),
        _clean(source.get("url")),
        _clean(source.get("canonical_url")),
    ))
    return sorted(set(_MTF_DOCUMENT.findall(text)))


def _legacy_public_candidates(
    packet: dict[str, Any], identity_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reproduce the former exact-title/URL public selection for comparison."""
    audit = packet.get("_source_role_audit")
    if not isinstance(audit, dict):
        return []
    identity_by_source_id = {
        str(record["source_id"]): record for record in identity_records
    }
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for index, row in enumerate(audit.get("renderable_sources", [])):
        roles = sorted(
            role for role in row.get("assigned_roles", [])
            if role not in _DISPLAY_ROLE_EXCLUSIONS
        )
        title = _clean(row.get("public_title"))
        url = _clean(row.get("public_url"))
        if not title or not roles:
            continue
        source_id = _clean(row.get("source_id")) or f"renderable:{index}"
        identity = identity_by_source_id.get(source_id, {})
        key = (title, url)
        current = merged.setdefault(key, {
            "title": title,
            "url": url,
            "source_ids": [],
            "roles": [],
            "canonical_identities": [],
            "canonical_urls": [],
            "document_numbers": [],
        })
        current["source_ids"].append(source_id)
        current["roles"] = sorted(set(current["roles"]) | set(roles))
        for field, value in (
            ("canonical_identities", identity.get("canonical_identity")),
            ("canonical_urls", identity.get("canonical_url")),
        ):
            if value:
                current[field] = sorted(set(current[field]) | {str(value)})
        current["document_numbers"] = sorted(
            set(current["document_numbers"])
            | set(identity.get("document_numbers", []))
        )
    candidates = sorted(merged.values(), key=lambda row: (
        min((_ROLE_PRIORITY.get(role, 9) for role in row["roles"]), default=9),
        row["title"],
        row["url"],
    ))[:2]
    for candidate in candidates:
        candidate["canonical_identity"] = (
            candidate["canonical_identities"][0]
            if len(candidate["canonical_identities"]) == 1 else ""
        )
        candidate["canonical_url"] = (
            candidate["canonical_urls"][0]
            if len(candidate["canonical_urls"]) == 1 else ""
        )
        candidate["document_number"] = (
            candidate["document_numbers"][0]
            if len(candidate["document_numbers"]) == 1 else None
        )
    return candidates


def _source_sections(text: str) -> list[str]:
    """Return complete public source sections from formatted reply text."""
    return [
        section for section in text.split("\n\n")
        if section.startswith(("Source —", "Sources —", "Secondary recollection —"))
    ]


def _public_urls(sections: list[str]) -> list[str]:
    """Extract raw URLs in their public order from source sections."""
    return [
        line.strip()
        for section in sections
        for line in section.splitlines()[1:]
        if line.strip().casefold().startswith(("http://", "https://"))
    ]


def _raw_public_url_is_safe(url: str) -> bool:
    """Validate emitted URL syntax independently of the canonicalizer."""
    if (
        not url
        or any(character.isspace() or ord(character) < 32 or ord(character) == 127
               for character in url)
        or "\\" in url
        or len(_URL_SCHEME.findall(url)) != 1
        or _NESTED_URL.search(url)
        or any(marker in url for marker in ("[http", "](http"))
        or any(url.count(opening) != url.count(closing) for opening, closing in (
            ("(", ")"), ("[", "]"), ("{", "}"),
        ))
    ):
        return False
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme.casefold() in {"http", "https"}
        and host
        and not parsed.username
        and not parsed.password
        and not (
            host.casefold() == "vertexaisearch.cloud.google.com"
            and parsed.path.startswith("/grounding-api-redirect/")
        )
    )


def _url_placement_violations(sections: list[str]) -> list[str]:
    """Return source-section lines containing URLs that are not raw URL lines."""
    return sorted({
        line
        for section in sections
        for line in section.splitlines()
        if _URL_SCHEME.search(line)
        and _RAW_URL_LINE.fullmatch(line.strip()) is None
    })


def _identical_public_entry_conflicts(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Detect indistinguishable public entries assigned different identities."""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        key = (_clean(record.get("title")), _clean(record.get("url")))
        grouped.setdefault(key, []).append(record)
    conflicts: list[dict[str, Any]] = []
    for (title, url), members in sorted(grouped.items()):
        identities = sorted({
            _clean(member.get("canonical_identity")) for member in members
            if _clean(member.get("canonical_identity"))
        })
        if len(members) > 1 and len(identities) > 1:
            conflicts.append({
                "title": title,
                "url": url,
                "canonical_identities": identities,
                "public_entry_count": len(members),
            })
    return conflicts


def _group_identity_conflicts(
    groups: list[dict[str, Any]], records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Detect evidence distinctions that a public group must not collapse."""
    by_source_id = {str(record["source_id"]): record for record in records}
    conflicts: list[dict[str, Any]] = []
    for group in groups:
        members = [
            by_source_id[source_id] for source_id in group.get("source_ids", [])
            if source_id in by_source_id
        ]
        document_numbers = sorted({
            number for member in members
            for number in member.get("document_numbers", [])
        })
        qualities = sorted({
            str(member.get("source_quality_class") or "") for member in members
        })
        locator_sets = {
            tuple(sorted(member.get("locator_components", [])))
            for member in members if member.get("locator_components")
        }
        date_sets = [
            set(member.get("date_identities", []))
            for member in members if member.get("date_identities")
        ]
        identifier_sets = {
            tuple(sorted(member.get("explicit_identifiers", [])))
            for member in members if member.get("explicit_identifiers")
        }
        member_identifier_sets = [
            set(member.get("explicit_identifiers", [])) for member in members
        ]
        shared_identifiers = (
            set.intersection(*member_identifier_sets)
            if member_identifier_sets and all(member_identifier_sets) else set()
        )
        member_document_sets = [
            set(member.get("document_numbers", [])) for member in members
        ]
        shared_document_numbers = (
            set.intersection(*member_document_sets)
            if member_document_sets and all(member_document_sets) else set()
        )
        original_urls = sorted({
            url for member in members
            for url in member.get("authoritative_urls", [])
            if (urlsplit(url).hostname or "").casefold() not in {"t.co", "www.t.co"}
        })
        publishers = sorted({
            _clean(member.get("publisher")).casefold()
            for member in members
            if _clean(member.get("publisher")).casefold() not in {"", "unknown"}
        })
        source_types = sorted({
            _clean(member.get("source_type")).casefold()
            for member in members if _clean(member.get("source_type"))
        })
        semantic_source_types = sorted(
            set(source_types).difference(_EVIDENCE_RECORD_SOURCE_TYPES)
        )
        reviewed_primary_sources = [
            member
            for member in members
            if (
                _clean(member.get("source_type")).casefold()
                == "official_primary_transcript"
                and member.get("source_quality_class")
                == "strong_primary_evidence"
                and int(member.get("supporting_passage_count", 0)) > 0
                and member.get("authoritative_urls")
                and member.get("document_numbers")
                and member.get("date_identities")
                and member.get("locator_components")
                and {
                    "wording", "attribution", "historical_context",
                }.issubset(set(member.get("claims_supported", [])))
            )
        ]
        reviewed_primary_consolidation = bool(
            len(reviewed_primary_sources) == 1
            and all(
                set(member.get("claims_supported", []))
                <= set(reviewed_primary_sources[0].get("claims_supported", []))
                for member in members
            )
        )
        reasons: list[str] = []
        if len(document_numbers) > 1:
            reasons.append("different_document_numbers")
        if len(locator_sets) > 1:
            reasons.append("different_stable_locators_or_pages")
        if len(identifier_sets) > 1:
            reasons.append("different_explicit_identifiers")
        if any(
            not any(
                one == two
                or one.startswith(f"{two}-")
                or two.startswith(f"{one}-")
                for one in left for two in right
            )
            for index, left in enumerate(date_sets)
            for right in date_sets[index + 1:]
        ):
            reasons.append("conflicting_verified_dates")
        if any(member.get("composite_locator") for member in members) and len(members) > 1:
            reasons.append("composite_locator_merged")
        if str(group.get("canonical_identity") or "").startswith((
            "unknown:", "unscoped_identifier:",
        )) and len(members) > 1:
            reasons.append("unscoped_identifier_merged")
        if len(original_urls) > 1 and not (
            shared_identifiers or shared_document_numbers
        ):
            reasons.append("different_authoritative_urls_without_shared_identifier")
        if len(publishers) > 1:
            reasons.append("different_publishers")
        if (
            len(semantic_source_types) > 1
            and not reviewed_primary_consolidation
        ):
            reasons.append("different_semantic_source_types")
        recollection_states = {
            bool(member.get("secondary_recollection")) for member in members
        }
        if len(recollection_states) > 1:
            recollection_members = [
                member for member in members
                if member.get("secondary_recollection")
            ]
            same_document_locator_projection = bool(
                len({_clean(member.get("title")) for member in members}) == 1
                and recollection_members
                and all(
                    _clean(member.get("source_type")).casefold()
                    == "canonical_stable_locator"
                    for member in recollection_members
                )
            )
            if (
                not same_document_locator_projection
                and not reviewed_primary_consolidation
            ):
                reasons.append("primary_and_recollection_documents_merged")
        if reasons:
            conflicts.append({
                "canonical_identity": group.get("canonical_identity"),
                "source_ids": list(group.get("source_ids", [])),
                "document_numbers": document_numbers,
                "source_quality_classes": qualities,
                "locator_component_sets": [list(item) for item in sorted(locator_sets)],
                "date_identity_sets": [sorted(item) for item in date_sets],
                "explicit_identifier_sets": [list(item) for item in sorted(identifier_sets)],
                "authoritative_urls": original_urls,
                "publishers": publishers,
                "source_types": source_types,
                "reasons": reasons,
            })
    return conflicts


def _public_record_projection(
    sources: list[dict[str, Any]], groups: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Bind actual formatter sources back to canonical diagnostic groups."""
    projected_groups = [group.get("public_source") for group in groups]
    mismatches: list[dict[str, Any]] = []
    if sources != projected_groups:
        mismatches.append({
            "kind": "formatter_source_projection_differs_from_identity_groups",
            "formatter_source_count": len(sources),
            "identity_group_count": len(groups),
        })
    records: list[dict[str, Any]] = []
    for index, source in enumerate(sources):
        group = groups[index] if index < len(groups) else None
        records.append({
            "title": _clean(source.get("title")),
            "url": _clean(source.get("url")),
            "canonical_identity": (
                group.get("canonical_identity") if group
                else canonical_source_identity(source)
            ),
            "canonical_url": (
                group.get("canonical_url") if group
                else canonical_source_url(source.get("url"))
            ),
            "document_numbers": (
                [group["document_number"]]
                if group and group.get("document_number")
                else _document_numbers(source)
            ),
        })
    return records, mismatches


def _audit_item(quote_id: str, packet: dict[str, Any]) -> dict[str, Any]:
    """Render and audit one completed historical-context packet."""
    identity = public_source_identity_diagnostics(packet)
    records = list(identity.get("records", []))
    raw_renderable = {
        _clean(row.get("source_id")) or f"renderable:{index}": row
        for index, row in enumerate(
            packet.get("_source_role_audit", {}).get("renderable_sources", [])
        )
    }
    for record in records:
        raw = raw_renderable.get(str(record.get("source_id") or ""), {})
        record["internal_roles"] = sorted(set(raw.get("assigned_roles", [])))
        record["evidence_targets"] = list(raw.get("claims_supported", []))
        record["supporting_passage_count"] = len(raw.get("supporting_passages", []))
    groups = list(identity.get("groups", []))
    ambiguities = list(identity.get("identity_ambiguities", []))
    legacy = _legacy_public_candidates(packet, records)
    formatted = format_context_reply_public(packet)
    render_failure = formatted is None
    text = "" if formatted is None else str(formatted["text"])
    formatter_sources = [] if formatted is None else list(formatted.get("sources", []))
    public_records, projection_mismatches = _public_record_projection(
        formatter_sources, groups,
    )
    sections = _source_sections(text)
    source_lines = [section.splitlines()[0] for section in sections]
    urls = _public_urls(sections)

    before_identities, before_identity_excess = _identity_excess([
        str(row.get("canonical_identity") or "") for row in legacy
    ])
    before_urls, before_url_excess = _identity_excess([
        str(row.get("canonical_url") or "") for row in legacy
    ])
    before_documents, before_document_excess = _identity_excess([
        str(row.get("document_number") or "") for row in legacy
    ])
    after_identities, after_identity_excess = _identity_excess([
        str(row.get("canonical_identity") or "") for row in public_records
    ])
    after_urls, after_url_excess = _identity_excess([
        str(row.get("canonical_url") or "") for row in public_records
    ])
    after_documents, after_document_excess = _identity_excess([
        number for row in public_records for number in row.get("document_numbers", [])
    ])
    raw_url_duplicates, raw_url_excess = _identity_excess([
        canonical_source_url(url) or url for url in urls
    ])
    source_line_role_leakage = sorted(set(_ROLE_LEAKAGE.findall(text)))
    markdown_links = sorted(set(_MARKDOWN_LINK.findall(text)))
    malformed_urls = sorted({
        url for url in urls
        if not _raw_public_url_is_safe(url)
    })
    url_not_on_own_line = _url_placement_violations(sections)
    differently_labelled = []
    for canonical_identity in after_identities:
        titles = sorted({
            row["title"] for row in public_records
            if row["canonical_identity"] == canonical_identity
        })
        if len(titles) > 1:
            differently_labelled.append({
                "canonical_identity": canonical_identity,
                "titles": titles,
            })
    group_conflicts = _group_identity_conflicts(groups, records)
    identical_public_entry_conflicts = _identical_public_entry_conflicts(
        public_records,
    )
    legacy_conflicts = [
        {
            "title": row["title"],
            "url": row["url"],
            "source_ids": row["source_ids"],
            "canonical_identities": row["canonical_identities"],
        }
        for row in legacy if len(row["canonical_identities"]) > 1
    ]
    merged_groups = [
        group for group in groups if int(group.get("source_record_count", 0)) > 1
    ]
    no_reliable_source = bool(
        not formatter_sources
        and text.endswith("Source — No reliable source located")
    )
    diagnostics = {
        "render_failure": render_failure,
        "duplicate_canonical_identities": after_identities,
        "duplicate_canonical_identity_excess_records": after_identity_excess,
        "duplicate_canonical_urls": after_urls,
        "duplicate_canonical_url_excess_records": after_url_excess,
        "duplicate_raw_public_urls": raw_url_duplicates,
        "duplicate_raw_public_url_excess_records": raw_url_excess,
        "repeated_archive_document_numbers": after_documents,
        "repeated_archive_document_number_excess_records": after_document_excess,
        "public_source_role_leakage": source_line_role_leakage,
        "markdown_links": markdown_links,
        "malformed_or_nested_urls": malformed_urls,
        "urls_not_on_own_line": url_not_on_own_line,
        "identical_sources_under_different_labels": differently_labelled,
        "identical_public_entries_with_distinct_identities": (
            identical_public_entry_conflicts
        ),
        "identity_conflicts": group_conflicts,
        "identity_ambiguities": ambiguities,
        "formatter_projection_mismatches": projection_mismatches,
    }
    return {
        "quote_id": quote_id,
        "quote_text": packet["quote_text"],
        "attribution_eligible": packet_is_attributed_to_margaret_thatcher(packet),
        "formatter_version": (
            None if formatted is None else formatted.get("formatter_version")
        ),
        "internal_source_record_count": len(records),
        "internal_source_records": records,
        "distinct_canonical_source_count": len(groups),
        "canonical_source_groups": groups,
        "source_records_merged_for_public_display": merged_groups,
        "merged_internal_source_record_count": sum(
            int(group["source_record_count"]) - 1 for group in merged_groups
        ),
        "before_deduplication": {
            "legacy_public_source_count": len(legacy),
            "legacy_public_sources": legacy,
            "duplicate_canonical_identities": before_identities,
            "duplicate_canonical_identity_excess_records": before_identity_excess,
            "duplicate_canonical_urls": before_urls,
            "duplicate_canonical_url_excess_records": before_url_excess,
            "repeated_archive_document_numbers": before_documents,
            "repeated_archive_document_number_excess_records": before_document_excess,
            "legacy_exact_key_identity_conflicts": legacy_conflicts,
        },
        "after_deduplication": {
            "public_source_count": len(public_records),
            "public_sources": public_records,
        },
        "public_source_sections": sections,
        "public_source_lines": source_lines,
        "public_urls": urls,
        "no_reliable_source": no_reliable_source,
        "diagnostics": diagnostics,
    }


def _blocking_item_violation_count(item: dict[str, Any]) -> int:
    """Count post-render defects that prevent a clean audit."""
    diagnostics = item["diagnostics"]
    return sum((
        bool(diagnostics["render_failure"]),
        len(diagnostics["duplicate_canonical_identities"]),
        len(diagnostics["duplicate_canonical_urls"]),
        len(diagnostics["duplicate_raw_public_urls"]),
        len(diagnostics["repeated_archive_document_numbers"]),
        len(diagnostics["public_source_role_leakage"]),
        len(diagnostics["markdown_links"]),
        len(diagnostics["malformed_or_nested_urls"]),
        len(diagnostics["urls_not_on_own_line"]),
        len(diagnostics["identical_sources_under_different_labels"]),
        len(diagnostics["identical_public_entries_with_distinct_identities"]),
        len(diagnostics["identity_conflicts"]),
        len(diagnostics["formatter_projection_mismatches"]),
    ))


def build_audit(
    research_dir: Path = DEFAULT_RESEARCH_DIR,
    *,
    reference_root: Path | None = None,
) -> dict[str, Any]:
    """Build the deterministic full-corpus public-source rendering audit."""
    research_dir = research_dir.resolve()
    packets, unresolved = load_and_validate_corpus(
        research_dir,
        require_source_role_audit=True,
    )
    items = {
        quote_id: _audit_item(quote_id, packets[quote_id])
        for quote_id in sorted(packets)
    }
    eligible_ids = sorted(
        quote_id for quote_id, packet in packets.items()
        if packet_is_attributed_to_margaret_thatcher(packet)
    )
    ineligible_ids = sorted(set(packets) - set(eligible_ids))
    quote_id_text = [
        {"quote_id": quote_id, "quote_text": packets[quote_id]["quote_text"]}
        for quote_id in sorted(packets)
    ]
    eligible_id_text = [
        {"quote_id": quote_id, "quote_text": packets[quote_id]["quote_text"]}
        for quote_id in eligible_ids
    ]
    confidence_metadata = [
        {
            "quote_id": quote_id,
            "research_confidence": packets[quote_id].get("research_confidence"),
            "confidence_after": packets[quote_id].get(
                "_source_role_audit", {}
            ).get("confidence_after"),
        }
        for quote_id in sorted(packets)
    ]
    root = (
        Path(__file__).resolve().parent
        if reference_root is None else reference_root.resolve()
    )
    quote_lines_path = root / "mrsMThatcher.txt"
    quote_analysis_path = root / "quote_analysis.json"
    quote_analysis = json.loads(quote_analysis_path.read_text(encoding="utf-8"))
    quote_analysis_items = quote_analysis.get("items", {})
    if not isinstance(quote_analysis_items, dict):
        raise RuntimeError("quote analysis items are invalid")
    quote_line_ids = {
        quote_text_hash(line) for line in quote_lines_path.read_text(
            encoding="utf-8"
        ).splitlines() if line.strip()
    }
    runtime_eligible_hashes = sorted({
        quote_text_hash(packets[quote_id]["quote_text"])
        for quote_id in eligible_ids
    })
    cycle_membership = sorted(
        set(runtime_eligible_hashes) & quote_line_ids & set(quote_analysis_items)
    )
    source_role_path = research_dir / AUDIT_FILENAME
    source_file_hashes = {
        "corpus_manifest.json": _file_sha256(research_dir / "corpus_manifest.json"),
        "final_unresolved/final_research_status.json": _file_sha256(
            research_dir / "final_unresolved" / "final_research_status.json"
        ),
        AUDIT_FILENAME: _file_sha256(source_role_path),
        "research_packets.json": _file_sha256(research_dir / "research_packets.json"),
        "mrsMThatcher.txt": _file_sha256(quote_lines_path),
        "quote_analysis.json": _file_sha256(quote_analysis_path),
    }
    for optional_input_name in (
        PACKET_CORRECTIONS_FILENAME,
        CURATED_EVIDENCE_FILENAME,
    ):
        optional_input_path = research_dir / optional_input_name
        if optional_input_path.exists():
            source_file_hashes[optional_input_name] = _file_sha256(
                optional_input_path
            )
    rendered_count = sum(not item["diagnostics"]["render_failure"] for item in items.values())
    before_duplicate_packets = sum(bool(
        item["before_deduplication"]["duplicate_canonical_identities"]
        or item["before_deduplication"]["duplicate_canonical_urls"]
        or item["before_deduplication"]["repeated_archive_document_numbers"]
    ) for item in items.values())
    after_duplicate_packets = sum(bool(
        item["diagnostics"]["duplicate_canonical_identities"]
        or item["diagnostics"]["duplicate_canonical_urls"]
        or item["diagnostics"]["duplicate_raw_public_urls"]
        or item["diagnostics"]["repeated_archive_document_numbers"]
    ) for item in items.values())
    invariant_checks = {
        "completed_packet_count_is_627": len(packets) == EXPECTED_COMPLETED_PACKET_COUNT,
        "unresolved_quote_count_is_5": len(unresolved) == EXPECTED_UNRESOLVED_QUOTE_COUNT,
        "attribution_eligible_count_is_611": (
            len(eligible_ids) == EXPECTED_ATTRIBUTION_ELIGIBLE_COUNT
        ),
        "every_completed_packet_rendered": rendered_count == len(packets),
        "source_role_audit_covers_every_completed_packet": all(
            isinstance(packet.get("_source_role_audit"), dict)
            for packet in packets.values()
        ),
        "cycle_membership_equals_attribution_eligible_set": (
            cycle_membership == runtime_eligible_hashes
        ),
    }
    blocking_item_violations = sum(
        _blocking_item_violation_count(item) for item in items.values()
    )
    invariant_violation_count = sum(not value for value in invariant_checks.values())
    counts = {
        "completed_packet_count": len(packets),
        "unresolved_quote_count": len(unresolved),
        "attribution_eligible_quote_count": len(eligible_ids),
        "attribution_ineligible_completed_quote_count": len(ineligible_ids),
        "rendered_packet_count": rendered_count,
        "render_failure_count": len(packets) - rendered_count,
        "internal_source_record_count": sum(
            item["internal_source_record_count"] for item in items.values()
        ),
        "distinct_canonical_source_count": sum(
            item["distinct_canonical_source_count"] for item in items.values()
        ),
        "packets_with_public_source_merges": sum(
            bool(item["source_records_merged_for_public_display"])
            for item in items.values()
        ),
        "internal_source_records_merged_for_public_display": sum(
            item["merged_internal_source_record_count"] for item in items.values()
        ),
        "identity_ambiguity_count": sum(
            len(item["diagnostics"]["identity_ambiguities"])
            for item in items.values()
        ),
        "packets_with_no_reliable_public_source": sum(
            item["no_reliable_source"] for item in items.values()
        ),
        "blocking_item_violation_count": blocking_item_violations,
        "invariant_violation_count": invariant_violation_count,
    }
    before = {
        "public_source_record_count": sum(
            item["before_deduplication"]["legacy_public_source_count"]
            for item in items.values()
        ),
        "packets_with_duplicate_source_identity": before_duplicate_packets,
        "duplicate_canonical_identity_group_count": sum(
            len(item["before_deduplication"]["duplicate_canonical_identities"])
            for item in items.values()
        ),
        "duplicate_canonical_identity_excess_record_count": sum(
            item["before_deduplication"]["duplicate_canonical_identity_excess_records"]
            for item in items.values()
        ),
        "duplicate_canonical_url_group_count": sum(
            len(item["before_deduplication"]["duplicate_canonical_urls"])
            for item in items.values()
        ),
        "duplicate_canonical_url_excess_record_count": sum(
            item["before_deduplication"]["duplicate_canonical_url_excess_records"]
            for item in items.values()
        ),
        "repeated_archive_document_number_group_count": sum(
            len(item["before_deduplication"]["repeated_archive_document_numbers"])
            for item in items.values()
        ),
        "repeated_archive_document_number_excess_record_count": sum(
            item["before_deduplication"]["repeated_archive_document_number_excess_records"]
            for item in items.values()
        ),
        "legacy_exact_key_identity_conflict_count": sum(
            len(item["before_deduplication"]["legacy_exact_key_identity_conflicts"])
            for item in items.values()
        ),
    }
    after = {
        "public_source_record_count": sum(
            item["after_deduplication"]["public_source_count"]
            for item in items.values()
        ),
        "packets_with_duplicate_source_identity": after_duplicate_packets,
        "duplicate_canonical_identity_group_count": sum(
            len(item["diagnostics"]["duplicate_canonical_identities"])
            for item in items.values()
        ),
        "duplicate_canonical_url_group_count": sum(
            len(item["diagnostics"]["duplicate_canonical_urls"])
            for item in items.values()
        ),
        "duplicate_raw_public_url_group_count": sum(
            len(item["diagnostics"]["duplicate_raw_public_urls"])
            for item in items.values()
        ),
        "repeated_archive_document_number_group_count": sum(
            len(item["diagnostics"]["repeated_archive_document_numbers"])
            for item in items.values()
        ),
        "public_source_role_leakage_count": sum(
            len(item["diagnostics"]["public_source_role_leakage"])
            for item in items.values()
        ),
        "markdown_link_count": sum(
            len(item["diagnostics"]["markdown_links"])
            for item in items.values()
        ),
        "malformed_or_nested_url_count": sum(
            len(item["diagnostics"]["malformed_or_nested_urls"])
            for item in items.values()
        ),
        "identity_conflict_count": sum(
            len(item["diagnostics"]["identity_conflicts"])
            for item in items.values()
        ),
        "identical_public_entry_conflict_count": sum(
            len(item["diagnostics"][
                "identical_public_entries_with_distinct_identities"
            ])
            for item in items.values()
        ),
    }
    ready = blocking_item_violations == 0 and invariant_violation_count == 0
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "audit_kind": AUDIT_KIND,
        "formatter_version": next((
            item["formatter_version"] for item in items.values()
            if item["formatter_version"] is not None
        ), None),
        "source_role_policy_version": next(iter(packets.values()))[
            "_source_role_audit"
        ]["policy_version"],
        "source_file_hashes": source_file_hashes,
        "counts": counts,
        "before_deduplication": before,
        "after_deduplication": after,
        "invariants": {
            "completed_quote_id_text_sha256": _json_sha256(quote_id_text),
            "attribution_eligible_quote_ids_sha256": hashlib.sha256(
                ("\n".join(eligible_ids) + "\n").encode("utf-8")
            ).hexdigest(),
            "attribution_eligible_quote_id_text_sha256": _json_sha256(
                eligible_id_text
            ),
            "attribution_ineligible_completed_quote_ids_sha256": hashlib.sha256(
                ("\n".join(ineligible_ids) + "\n").encode("utf-8")
            ).hexdigest(),
            "unresolved_quote_ids_sha256": hashlib.sha256(
                ("\n".join(sorted(unresolved)) + "\n").encode("utf-8")
            ).hexdigest(),
            "confidence_metadata_sha256": _json_sha256(confidence_metadata),
            "runtime_attribution_eligible_quote_hashes_sha256": hashlib.sha256(
                ("\n".join(runtime_eligible_hashes) + "\n").encode("utf-8")
            ).hexdigest(),
            "cycle_membership_quote_ids_sha256": hashlib.sha256(
                ("\n".join(cycle_membership) + "\n").encode("utf-8")
            ).hexdigest(),
            "cycle_membership_count": len(cycle_membership),
            "quote_analysis_record_count": len(quote_analysis_items),
            "source_role_audit_record_count": len(packets),
            "checks": invariant_checks,
        },
        "ready": ready,
        "items": items,
    }


def _validate_output_path(research_dir: Path, output: Path) -> Path:
    """Refuse to write an audit into the immutable research corpus."""
    research = research_dir.resolve()
    resolved = output.resolve()
    if resolved == research or research in resolved.parents:
        raise ValueError("--output must be outside the immutable research directory")
    return resolved


def main(argv: list[str] | None = None) -> int:
    """Run the offline public-source audit CLI."""
    parser = argparse.ArgumentParser(
        description="Audit full-corpus public historical-context source rendering",
    )
    parser.add_argument(
        "--research-dir", type=Path, default=DEFAULT_RESEARCH_DIR,
    )
    parser.add_argument(
        "--reference-root",
        type=Path,
        help="directory containing copied mrsMThatcher.txt and quote_analysis.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = _validate_output_path(args.research_dir, args.output)
    audit = build_audit(args.research_dir, reference_root=args.reference_root)
    atomic_write_json(output, audit)
    print(json.dumps({
        "output": str(output),
        "ready": audit["ready"],
        "counts": audit["counts"],
        "before_deduplication": audit["before_deduplication"],
        "after_deduplication": audit["after_deduplication"],
    }, ensure_ascii=False, sort_keys=True))
    return 0 if audit["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
