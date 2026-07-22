#!/usr/bin/env python3
"""Persist the human review of priority MTF source-event/date comparisons.

This is a review projection, not an evidence-admission mechanism.  It records
source identity, event, date and quotation-linkage findings from the compact
official-page audit.  It never changes packets, evidence roles or public
rendering, and it deliberately makes no judgement about semantic Meaning.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_TRUTH_AUDIT = ROOT / "historical_context_evidence_truth_audit.json"
DEFAULT_MTF_REVIEW = ROOT / "historical_context_mtf_primary_review.json"
AUDIT_KIND = "historical_context_mtf_priority_manual_review"
SCHEMA_VERSION = 1
EXPECTED_PACKET_COUNT = 139
EXPECTED_COMPARISON_COUNT = 148
DISPOSITIONS = {"verified", "mismatch", "uncertain", "retrieval_failed"}

# These are the event-title judgements that cannot be obtained from a token
# score alone.  All other retrieved priority title pairs were read and found
# to describe the same event, unless their dates also conflict.
_EVENT_MISMATCHES: dict[tuple[str, str], str] = {
    (
        "80aba68d430bac98105a1d22006902ce0107590985f70e5dcd5f2399fe44ad20",
        "104066",
    ): "packet names the Daily Mail, while the official document is an article for The Sun",
    (
        "f229667039f3ec978efdde02a325e71652d24c9bbc2ad86aeea64835f30bc018",
        "108353",
    ): "packet calls the event a memorial conference, while the official document is the Keith Joseph Memorial Lecture",
}

_EVENT_UNCERTAINTIES: dict[tuple[str, str], str] = {
    (
        "283636fc2526ccd22c302f80f6c494c36ec1e612d16f9b5a145d4eca70b14e9b",
        "105799",
    ): "packet's generic Carlton Club label is less precise than the official Second Carlton Lecture title",
    (
        "94af98b20cfa5e97965a5157a776f0edd072717ce3897df6b4696a659fe2c934",
        "105799",
    ): "packet's generic Carlton Club label is less precise than the official Second Carlton Lecture title",
    (
        "d238243726b4a6ea0bf7c6bbd3080ee59cb31f92a87ce19a36e3d789da425252",
        "108383",
    ): "official title confirms a Conservative Party Conference speech but not the packet's narrower fringe-rally description",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"review input is not a JSON object: {path}")
    return value


def _review_comparison(record: dict[str, Any]) -> dict[str, Any]:
    quote_id = _clean(record.get("quote_id"))
    document_number = _clean(record.get("document_number"))
    key = (quote_id, document_number)
    status = _clean(record.get("status"))
    official_url = _clean(record.get("official_url"))
    official_title = _clean(record.get("official_title"))
    official_date = _clean(record.get("official_date"))
    packet_event = _clean(record.get("packet_source_event"))
    packet_date = _clean(record.get("packet_date"))
    coverage = record.get("quotation_longest_contiguous_token_coverage")

    if status == "page_retrieval_failed":
        return {
            "quote_id": quote_id,
            "document_number": document_number,
            "identity_provenance": sorted(set(record.get("identity_provenance", []))),
            "disposition": "retrieval_failed",
            "field_findings": {
                "archive_identity": "unavailable",
                "source_event": "unavailable",
                "date": "unavailable",
                "quotation_linkage": "unavailable",
                "semantic_meaning": "not_assessed",
            },
            "actionable_findings": ["retry_official_page_retrieval"],
            "evidence_basis": "Official page retrieval failed; no event, date or quotation-linkage judgement is made.",
        }

    archive_identity = (
        "verified"
        if record.get("canonical_url_matches") is True
        and record.get("author_matches_margaret_thatcher") is True
        else "mismatch"
    )
    date_finding = (
        "verified" if record.get("date_comparison") == "match"
        else "mismatch" if record.get("date_comparison") == "mismatch"
        else "uncertain"
    )
    if key in _EVENT_MISMATCHES:
        event_finding = "mismatch"
        event_note = _EVENT_MISMATCHES[key]
    elif key in _EVENT_UNCERTAINTIES:
        event_finding = "uncertain"
        event_note = _EVENT_UNCERTAINTIES[key]
    elif date_finding == "mismatch":
        event_finding = "mismatch"
        event_note = "official title and date identify a different event from the packet"
    else:
        event_finding = "verified"
        event_note = "packet and official titles describe the same event"

    if isinstance(coverage, (int, float)) and not isinstance(coverage, bool):
        linkage = (
            "verified" if coverage >= 0.9
            else "mismatch" if coverage == 0
            else "uncertain"
        )
        coverage_text = f"{coverage:.6f}"
    else:
        linkage = "unavailable"
        coverage_text = "unavailable"

    actionable: list[str] = []
    if archive_identity == "mismatch":
        actionable.append("archive_identity_conflict")
    if date_finding == "mismatch":
        actionable.append("packet_date_conflicts_with_official_document")
    if event_finding == "mismatch":
        actionable.append("packet_source_event_conflicts_with_official_document")
    elif event_finding == "uncertain":
        actionable.append("packet_source_event_requires_narrow_manual_check")
    if linkage in {"mismatch", "uncertain"}:
        actionable.append("quotation_linkage_requires_transcript_inspection")

    if (
        archive_identity == "mismatch"
        or date_finding == "mismatch"
        or event_finding == "mismatch"
    ):
        disposition = "mismatch"
    elif event_finding == "uncertain" or linkage != "verified":
        disposition = "uncertain"
    else:
        disposition = "verified"

    basis = (
        f"Canonical MTF document {document_number} attributes the page to "
        f"{_clean(record.get('official_author')) or 'an unavailable author'}; "
        f"packet date {packet_date or 'unavailable'} versus official date "
        f"{official_date or 'unavailable'}; packet event “{packet_event}” versus "
        f"official title “{official_title}”; contiguous quotation-token coverage "
        f"{coverage_text}. Event finding: {event_note}. This review does not "
        "assess the semantic Meaning paraphrase."
    )
    return {
        "quote_id": quote_id,
        "document_number": document_number,
        "identity_provenance": sorted(set(record.get("identity_provenance", []))),
        "official_url": official_url,
        "disposition": disposition,
        "field_findings": {
            "archive_identity": archive_identity,
            "source_event": event_finding,
            "date": date_finding,
            "quotation_linkage": linkage,
            "semantic_meaning": "not_assessed",
        },
        "actionable_findings": sorted(set(actionable)),
        "evidence_basis": basis,
    }


def build_review(
    truth_path: Path = DEFAULT_TRUTH_AUDIT,
    mtf_path: Path = DEFAULT_MTF_REVIEW,
    *,
    hardened_mtf_reconciled: bool = False,
) -> dict[str, Any]:
    """Build the complete priority-packet manual-review projection."""
    truth = _load(truth_path)
    mtf = _load(mtf_path)
    if truth.get("audit_kind") != "historical_context_evidence_truth_triage_audit":
        raise RuntimeError("unexpected evidence-truth audit kind")
    if mtf.get("audit_kind") != "historical_context_mtf_primary_page_review":
        raise RuntimeError("unexpected MTF primary-review kind")

    priority_truth = truth.get("records", {}).get(
        "eligible_same_document_event_or_date_priorities", []
    )
    priority_ids = {
        row.get("quote_id") for row in priority_truth if isinstance(row, dict)
    }
    source_records = sorted(
        (
            row for row in mtf.get("records", [])
            if isinstance(row, dict)
            and row.get("scope") == "priority_same_document_event_or_date"
        ),
        key=lambda row: (str(row.get("quote_id")), str(row.get("document_number"))),
    )
    comparisons = [_review_comparison(row) for row in source_records]
    comparison_keys = [
        (row["quote_id"], row["document_number"]) for row in comparisons
    ]
    manual_judgement_keys = set(_EVENT_MISMATCHES) | set(_EVENT_UNCERTAINTIES)
    if (
        len(priority_ids) != EXPECTED_PACKET_COUNT
        or len(comparisons) != EXPECTED_COMPARISON_COUNT
        or len(set(comparison_keys)) != len(comparison_keys)
        or {row["quote_id"] for row in comparisons} != priority_ids
        or any(row["disposition"] not in DISPOSITIONS for row in comparisons)
        or set(_EVENT_MISMATCHES) & set(_EVENT_UNCERTAINTIES)
        or not manual_judgement_keys <= set(comparison_keys)
    ):
        raise RuntimeError("priority MTF manual-review coverage differs")

    by_quote: dict[str, list[dict[str, Any]]] = {}
    for row in comparisons:
        by_quote.setdefault(row["quote_id"], []).append(row)
    packets: list[dict[str, Any]] = []
    for quote_id, rows in sorted(by_quote.items()):
        dispositions = {row["disposition"] for row in rows}
        # A packet aggregate is a safety status, not a best-source selector.
        # One conflicting or unavailable comparison must not be hidden by a
        # different comparison that happened to verify cleanly.
        packet_disposition = (
            "mismatch" if "mismatch" in dispositions
            else "retrieval_failed" if "retrieval_failed" in dispositions
            else "uncertain" if "uncertain" in dispositions
            else "verified"
        )
        packets.append({
            "quote_id": quote_id,
            "disposition": packet_disposition,
            "document_numbers": [row["document_number"] for row in rows],
            "comparison_dispositions": dict(sorted(Counter(
                row["disposition"] for row in rows
            ).items())),
            "field_findings": {
                field: dict(sorted(Counter(
                    row["field_findings"][field] for row in rows
                ).items()))
                for field in (
                    "archive_identity", "source_event", "date",
                    "quotation_linkage", "semantic_meaning",
                )
            },
            "actionable_findings": sorted({
                finding for row in rows
                for finding in row["actionable_findings"]
            }),
            "evidence_basis": (
                "Worst-case aggregate of "
                f"{len(rows)} document comparison(s): "
                + ", ".join(
                    f"{name}={count}"
                    for name, count in sorted(Counter(
                        row["disposition"] for row in rows
                    ).items())
                )
                + ". A clean comparison does not override a contradictory, "
                "unavailable or uncertain comparison."
            ),
            "has_actionable_mismatch": any(
                row["disposition"] == "mismatch" for row in rows
            ),
        })

    raw_comparison_counts = Counter(row["disposition"] for row in comparisons)
    comparison_counts = {
        disposition: raw_comparison_counts[disposition]
        for disposition in sorted(DISPOSITIONS)
    }
    raw_packet_counts = Counter(row["disposition"] for row in packets)
    packet_counts = {
        disposition: raw_packet_counts[disposition]
        for disposition in sorted(DISPOSITIONS)
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "review_kind": AUDIT_KIND,
        "reviewed_at": "2026-07-22",
        "review_state": (
            "final_reconciled_with_hardened_mtf_review"
            if hardened_mtf_reconciled
            else "complete_against_current_inputs_pending_hardened_mtf_reconciliation"
        ),
        "scope": {
            "assessment": "source identity, source event, date and quotation linkage",
            "semantic_meaning_assessed": False,
            "automatic_evidence_promotion_authorised": False,
            "packet_or_evidence_mutation_authorised": False,
        },
        "disposition_definitions": {
            "verified": (
                "official archive identity, source event, date and quotation "
                "linkage align; semantic Meaning is not assessed"
            ),
            "mismatch": (
                "official archive evidence directly conflicts with at least "
                "one packet source-identity, event or date claim"
            ),
            "uncertain": (
                "retrieved evidence is insufficient to bind every reviewed "
                "source field without further transcript inspection"
            ),
            "retrieval_failed": "official page was unavailable for adjudication",
        },
        "input_hashes": {
            truth_path.name: _sha256(truth_path),
            mtf_path.name: _sha256(mtf_path),
        },
        "counts": {
            "priority_packet_count": len(packets),
            "priority_comparison_count": len(comparisons),
            "comparison_dispositions": comparison_counts,
            "packet_dispositions": packet_counts,
            "comparisons_with_actionable_findings": sum(
                bool(row["actionable_findings"]) for row in comparisons
            ),
            "packets_with_actionable_mismatch": sum(
                row["has_actionable_mismatch"] for row in packets
            ),
            "date_mismatch_comparison_count": sum(
                row["field_findings"]["date"] == "mismatch"
                for row in comparisons
            ),
            "source_event_mismatch_comparison_count": sum(
                row["field_findings"]["source_event"] == "mismatch"
                for row in comparisons
            ),
            "source_event_uncertain_comparison_count": sum(
                row["field_findings"]["source_event"] == "uncertain"
                for row in comparisons
            ),
        },
        "manual_title_judgements": {
            "event_mismatch_keys": [f"{q}:{d}" for q, d in sorted(_EVENT_MISMATCHES)],
            "event_uncertain_keys": [f"{q}:{d}" for q, d in sorted(_EVENT_UNCERTAINTIES)],
        },
        "comparisons": comparisons,
        "packets": packets,
        "invariants": {
            "all_139_priority_packets_reviewed": len(packets) == EXPECTED_PACKET_COUNT,
            "all_148_priority_comparisons_reviewed": (
                len(comparisons) == EXPECTED_COMPARISON_COUNT
            ),
            "comparison_keys_unique": len(set(comparison_keys)) == len(comparison_keys),
            "truth_and_mtf_priority_packet_sets_equal": (
                {row["quote_id"] for row in comparisons} == priority_ids
            ),
            "manual_title_judgements_are_in_scope": (
                manual_judgement_keys <= set(comparison_keys)
                and not set(_EVENT_MISMATCHES) & set(_EVENT_UNCERTAINTIES)
            ),
            "semantic_meaning_not_assessed": all(
                row["field_findings"]["semantic_meaning"] == "not_assessed"
                for row in comparisons
            ),
            "verified_packets_have_only_verified_comparisons": all(
                row["disposition"] != "verified"
                or set(row["comparison_dispositions"]) == {"verified"}
                for row in packets
            ),
        },
    }


def _write(path: Path, value: dict[str, Any]) -> None:
    encoded = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    """Write a validated priority-packet manual-review artifact."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--truth", type=Path, default=DEFAULT_TRUTH_AUDIT)
    parser.add_argument("--mtf-review", type=Path, default=DEFAULT_MTF_REVIEW)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hardened-mtf-reconciled", action="store_true")
    args = parser.parse_args(argv)
    review = build_review(
        args.truth,
        args.mtf_review,
        hardened_mtf_reconciled=args.hardened_mtf_reconciled,
    )
    _write(args.output, review)
    print(json.dumps({"output": str(args.output), **review["counts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
