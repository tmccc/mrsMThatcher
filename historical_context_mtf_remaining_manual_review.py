#!/usr/bin/env python3
"""Persist the human review of the remaining precise MTF comparisons.

This is a review projection only.  It records archive identity, event, date,
and quotation-linkage findings without changing research packets or evidence.
Semantic Meaning is deliberately not assessed.
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
AUDIT_KIND = "historical_context_mtf_remaining_manual_review"
SCHEMA_VERSION = 1
EXPECTED_PACKET_COUNT = 296
EXPECTED_COMPARISON_COUNT = 382
DISPOSITIONS = {"verified", "mismatch", "uncertain", "retrieval_failed"}

# Every retrieved title pair was read.  These keys are the cases where titles
# deterministically identify different events even without a date conflict.
_EVENT_MISMATCH_KEYS = {
    ("3865a461dd280b2b0f00d7339e8bb5c9e18fbead71aeee83da52c57bb94cd01e", "102838"),
    ("5375f62e3ac4d5c44f8f691b94a9cfc52eb3f2e020e9fcc8c2fd2f4475e960b4", "108284"),
    ("54bcfa6d526fe5de3d3f6db6ee2b1707030258c07c941f3a2c1dcfb9dde87dc1", "102427"),
    ("5e5c7e9155b845150fe903c5f9951af17530a54d930af8706a438ca49b2f2fc1", "102729"),
    ("60fee941218846ecd24f012339a9bdfd922d00130ee006c181ee46ab5bcb416a", "108285"),
    ("6f70887295eff72d62a86e86c3983e7d960cafd35784e0f5d6a4d790558cf37b", "102601"),
    ("6f7c8f9564809394dc5cf22cf9ed10e2e8067d08f2fd2bc92c9bbf8d283af54b", "108334"),
    ("78f07e83a9b7e3fb3429839d32b4dec32d3ffd8dc89bb777b62c6229d3bccadf", "102838"),
    ("7a92b09f320e1b0628eaf18ac5c7c758564d1934a9ed74935323fe94d2f6ba59", "103329"),
    ("91fc151a519c51105a185875ff1897d00796c29279de667c0fa047fbdfb8a31f", "103329"),
    ("9de4ca7d21925f4bb57ef9418d7b91b7ec5d791be1a7b90aeb0477486cf9a101", "104064"),
    ("ab81f26fbbdea5b50b5f95a4b89e91f2491d707ee3d98b06e1b2a8f203e5e659", "103683"),
    ("b32d8cdf5977dee436857e8060d3a83ebfe54de9f6dabb20ffc65a0796338b5c", "105648"),
    ("cf0f6b4c44d88fd3ebefd7ddbdbf9f01c2f895b774c43ad6c5f390db5c067e0c", "108374"),
    ("d6bb2861e084d37cb5109423252d79319e9a55f6dbc3d705839f1ad054c10922", "106445"),
    ("d6ce2b7b10e28e0b801ad84890f229365fea41a8b494bfdf41adb9cc360373fd", "110687"),
    ("de4e8c509af0ec25b4ee05d41f70b3eef1ce22d79f7a88371db90317c3b511c6", "108330"),
    ("e0ec17fd0cdf45f02d1b9cedffa8bc626e10592203559baba18b7622feeee491", "102613"),
    ("e901673a2a91f9dadb8b896ef8232aa5d7e6f20c0602f12721d74810db8233f0", "101456"),
    ("e9d8f7dff356e2364bd2250fe6a4f4b5aecfde205cf7e66c316e0850a20bf316", "100857"),
    ("edbdfcd279a304940c6e1001ee013da4e45de9f75bfd9fbd26c863e7cfae1a02", "104066"),
    ("f1edcae11f5476a82acc3279eb1e75db01bc9673337edf893ccde19a1abbe022", "102613"),
    ("fe6283757bdf4159303c428a7b5e961b4c02a7abac61a25d4c38fada9fe5df01", "108374"),
}

# These title pairs plausibly refer to the same occasion, but the labels are
# too generic, composite, or differently specific for a deterministic bind.
_EVENT_UNCERTAIN_KEYS = {
    ("1dde0bb1ec4fa393163170fa18ed57b08981e3f4f5fe36d343021331a85be8fe", "103101"),
    ("209e48a0ae48769dc2830d6963615fcc526d95ccd3b12442f6106dcd206ea14e", "104088"),
    ("4c135c053d61ff9441b6aed5b77213967f43489263ee9f80b49a4155ef93737e", "104032"),
    ("5e5c7e9155b845150fe903c5f9951af17530a54d930af8706a438ca49b2f2fc1", "102728"),
    ("5f14e6e600773cf394a3f3a3ae21aef10f108691eef50093002571765a5a4a82", "108183"),
    ("7d6ced255a74d563b6ba9811429970c51f605647aac25819e14104f836d93434", "108337"),
    ("81c14e48d945dfadbcc2e54d5e0b2da6194abdafe88511a6c40aeeef05ff384d", "105764"),
    ("8422560a549eb0c99719e6335e4cfc4bc09e74a2bbb54c31b9e4854b21152a16", "108325"),
    ("92e3b501f3e59eb4be98daa8addd6711649d56e8d31e3c7b8582a5f0f4fd740e", "104072"),
    ("9238f8fd1375c7adf1ba8b92c4f2330383a6faeaa5210fa20e858032be1e2223", "103336"),
    ("c03ace3ba53344857a05296564bcfddc1946ddbcbd2b3084bbf60ba80cd3e323", "103336"),
    ("9cff99eb9acab35c114c4ea26018a11fa1764abf5b5a653c4b07bb4271d415d9", "108365"),
    ("a49cdc77db5c8a3c2253709ae3dec945d7b5a89ab99eb87b5268ae96349667bd", "103268"),
    ("abcd58e8e8ff8fa5fa8e3c37fcefb55702beb9f07029fec7c3cc38b966acc5cc", "108364"),
    ("d355d90eae090636786249aea072fb32b51b8555d39c423e855d3947a7a937b5", "108304"),
    ("d6ba4775ac47ca6aef09ec4913e591d30ad298af12dfacaa5c18fc235d53eb9d", "102963"),
    ("f3f3f8b295a3b26821fdb651bc4a120087c3787f5e007c783176257d60f89673", "103494"),
    ("f6b4fb6ced9724260c1d6d4ac3ce40bc1ad4bb872aa7e6a73a7a322880cab632", "108330"),
    ("f9f4251f60bf1f7ea5fb270975e8b5904f308504200a18e1b5b371fc38a57445", "103411"),
    ("fc54ab7da4cdb6a19fff71c1c126f5af2113f29fba35916e24e1ec5daa9f5c82", "108324"),
}

# Archive catalogue authors are not always literal page authors.  Two titles
# explicitly identify Thatcher material; two catalogue records attribute the
# source to somebody else and therefore cannot verify Thatcher authorship.
_IDENTITY_VERIFIED_KEYS = {
    ("486ac8259439d7b2857b5accce1e57ee83bce66c0408024af831510649a4ef5e", "210253"),
    ("e0770fc3d6619db33fb692301a29fbf15f0e8e2306bd568064e557a108f69ec3", "109284"),
}
_IDENTITY_MISMATCH_KEYS = {
    ("a7f8b7c8dcea6f7ecddea2624f81c169e69f893bee4d5158d53ce35cdec1987a", "109439"),
    ("f629116f926f3fb041cd75c05b006f924a89f09160a5982932732fb06fe624f0", "101830"),
}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"review input is not a JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _aggregate_packet_disposition(values: set[str]) -> str:
    """Return the worst comparison result without masking unavailable rows."""
    return "mismatch" if "mismatch" in values else (
        "retrieval_failed" if "retrieval_failed" in values else (
            "uncertain" if "uncertain" in values else "verified"
        )
    )


def _review(record: dict[str, Any]) -> dict[str, Any]:
    quote_id = _clean(record.get("quote_id"))
    document_number = _clean(record.get("document_number"))
    key = (quote_id, document_number)
    status = _clean(record.get("status"))
    if status == "official_document_not_found":
        return {
            "quote_id": quote_id,
            "document_number": document_number,
            "identity_provenance": sorted(set(record.get("identity_provenance", []))),
            "official_url": _clean(record.get("official_url")),
            "disposition": "mismatch",
            "field_findings": {
                "archive_identity": "mismatch",
                "source_event": "unavailable",
                "date": "unavailable",
                "quotation_linkage": "unavailable",
                "semantic_meaning": "not_assessed",
            },
            "actionable_findings": [
                "official_archive_document_not_found",
                "replace_or_remove_nonexistent_document_identity",
            ],
            "evidence_basis": (
                f"The official Margaret Thatcher Foundation endpoint for document "
                f"{document_number} deterministically returned ‘Page not found’. "
                "Archive identity conflicts; event, date, quotation linkage, and "
                "semantic Meaning cannot be assessed from that nonexistent page."
            ),
        }
    if status == "page_retrieval_failed":
        return {
            "quote_id": quote_id,
            "document_number": document_number,
            "identity_provenance": sorted(set(record.get("identity_provenance", []))),
            "disposition": "retrieval_failed",
            "field_findings": {field: "unavailable" for field in (
                "archive_identity", "source_event", "date", "quotation_linkage"
            )} | {"semantic_meaning": "not_assessed"},
            "actionable_findings": ["retry_official_page_retrieval"],
            "evidence_basis": "Official page retrieval failed; no source-field judgement is made.",
        }

    if key in _IDENTITY_VERIFIED_KEYS:
        identity = "verified"
        identity_note = "catalogue label differs, but the official title explicitly identifies Thatcher material"
    elif key in _IDENTITY_MISMATCH_KEYS:
        identity = "mismatch"
        identity_note = "official catalogue authorship does not verify Thatcher as author"
    else:
        identity = "verified" if (
            record.get("canonical_url_matches") is True
            and record.get("author_matches_margaret_thatcher") is True
        ) else "mismatch"
        identity_note = "canonical document and archive author align" if identity == "verified" else "canonical identity or archive author conflicts"

    date = "verified" if record.get("date_comparison") == "match" else (
        "mismatch" if record.get("date_comparison") == "mismatch" else "uncertain"
    )
    packet_event = _clean(record.get("packet_source_event"))
    if not packet_event:
        event = "not_claimed"
        event_note = "packet makes no source-event claim"
    elif key in _EVENT_MISMATCH_KEYS:
        event = "mismatch"
        event_note = "manual title comparison identifies a different event"
    elif key in _EVENT_UNCERTAIN_KEYS:
        event = "uncertain"
        event_note = "title labels are too generic, composite, or differently specific to bind deterministically"
    elif date == "mismatch":
        event = "mismatch"
        event_note = "the exact contradictory dates identify different occasions"
    else:
        event = "verified"
        event_note = "manual title comparison identifies the same event"

    coverage = record.get("quotation_longest_contiguous_token_coverage")
    if isinstance(coverage, (int, float)) and not isinstance(coverage, bool):
        linkage = "verified" if coverage >= 0.9 else ("mismatch" if coverage == 0 else "uncertain")
        coverage_text = f"{coverage:.6f}"
    else:
        linkage = "unavailable"
        coverage_text = "unavailable"

    actionable = []
    if identity == "mismatch": actionable.append("archive_identity_conflict")
    if date == "mismatch": actionable.append("packet_date_conflicts_with_official_document")
    if event == "mismatch": actionable.append("packet_source_event_conflicts_with_official_document")
    elif event == "uncertain": actionable.append("packet_source_event_requires_narrow_manual_check")
    if linkage in {"mismatch", "uncertain"}: actionable.append("quotation_linkage_requires_transcript_inspection")
    disposition = "mismatch" if "mismatch" in {identity, date, event} else (
        "uncertain" if "uncertain" in {identity, date, event, linkage} or linkage != "verified" else "verified"
    )
    return {
        "quote_id": quote_id,
        "document_number": document_number,
        "identity_provenance": sorted(set(record.get("identity_provenance", []))),
        "official_url": _clean(record.get("official_url")),
        "disposition": disposition,
        "field_findings": {
            "archive_identity": identity, "source_event": event, "date": date,
            "quotation_linkage": linkage, "semantic_meaning": "not_assessed",
        },
        "actionable_findings": sorted(set(actionable)),
        "evidence_basis": (
            f"Canonical MTF document {document_number}; {identity_note}; packet date "
            f"{_clean(record.get('packet_date')) or 'unavailable'} versus official date "
            f"{_clean(record.get('official_date')) or 'unavailable'}; packet event “{packet_event}” "
            f"versus official title “{_clean(record.get('official_title'))}”; contiguous "
            f"quotation-token coverage {coverage_text}. Event finding: {event_note}. "
            "Semantic Meaning is not assessed."
        ),
    }


def build_review(truth_path: Path = DEFAULT_TRUTH_AUDIT, mtf_path: Path = DEFAULT_MTF_REVIEW, *, hardened_mtf_reconciled: bool = False) -> dict[str, Any]:
    """Build the complete remaining-candidate manual-review projection."""
    truth, mtf = _load(truth_path), _load(mtf_path)
    if truth.get("audit_kind") != "historical_context_evidence_truth_triage_audit":
        raise RuntimeError("unexpected evidence-truth audit kind")
    if mtf.get("audit_kind") != "historical_context_mtf_primary_page_review":
        raise RuntimeError("unexpected MTF primary-review kind")
    source = sorted((r for r in mtf.get("records", []) if isinstance(r, dict) and r.get("scope") == "remaining_precise_mtf_candidate"), key=lambda r: (str(r.get("quote_id")), str(r.get("document_number"))))
    comparisons = [_review(row) for row in source]
    keys = [(r["quote_id"], r["document_number"]) for r in comparisons]
    manual = _EVENT_MISMATCH_KEYS | _EVENT_UNCERTAIN_KEYS | _IDENTITY_VERIFIED_KEYS | _IDENTITY_MISMATCH_KEYS
    if len(comparisons) != EXPECTED_COMPARISON_COUNT or len(set(keys)) != len(keys) or len({r["quote_id"] for r in comparisons}) != EXPECTED_PACKET_COUNT or not manual <= set(keys):
        raise RuntimeError("remaining MTF manual-review coverage differs")

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in comparisons: grouped.setdefault(row["quote_id"], []).append(row)
    packets = []
    for quote_id, rows in sorted(grouped.items()):
        values = {row["disposition"] for row in rows}
        # Packet aggregation is deliberately worst-case: a sound comparison
        # must never hide a conflicting, uncertain, or unavailable sibling.
        disposition = _aggregate_packet_disposition(values)
        packets.append({
            "quote_id": quote_id, "disposition": disposition,
            "document_numbers": [row["document_number"] for row in rows],
            "comparison_dispositions": dict(sorted(Counter(row["disposition"] for row in rows).items())),
            "field_findings": {field: dict(sorted(Counter(row["field_findings"][field] for row in rows).items())) for field in ("archive_identity", "source_event", "date", "quotation_linkage", "semantic_meaning")},
            "actionable_findings": sorted({finding for row in rows for finding in row["actionable_findings"]}),
            "has_actionable_mismatch": any(row["disposition"] == "mismatch" for row in rows),
        })
    return {
        "schema_version": SCHEMA_VERSION, "review_kind": AUDIT_KIND, "reviewed_at": "2026-07-22",
        "review_state": "final_reconciled_with_hardened_mtf_review" if hardened_mtf_reconciled else "complete_against_current_inputs_pending_hardened_mtf_reconciliation",
        "scope": {"assessment": "source identity, source event, date and quotation linkage", "semantic_meaning_assessed": False, "automatic_evidence_promotion_authorised": False, "packet_or_evidence_mutation_authorised": False},
        "input_hashes": {truth_path.name: _sha256(truth_path), mtf_path.name: _sha256(mtf_path)},
        "counts": {
            "remaining_packet_count": len(packets), "remaining_comparison_count": len(comparisons),
            "comparison_dispositions": dict(sorted(Counter(r["disposition"] for r in comparisons).items())),
            "packet_dispositions": dict(sorted(Counter(r["disposition"] for r in packets).items())),
            "comparisons_with_actionable_findings": sum(bool(r["actionable_findings"]) for r in comparisons),
            "packets_with_actionable_mismatch": sum(r["has_actionable_mismatch"] for r in packets),
        },
        "manual_title_judgements": {
            "event_mismatch_keys": [f"{q}:{d}" for q, d in sorted(_EVENT_MISMATCH_KEYS)],
            "event_uncertain_keys": [f"{q}:{d}" for q, d in sorted(_EVENT_UNCERTAIN_KEYS)],
            "archive_identity_override_keys": [f"{q}:{d}" for q, d in sorted(_IDENTITY_VERIFIED_KEYS | _IDENTITY_MISMATCH_KEYS)],
        },
        "comparisons": comparisons, "packets": packets,
        "invariants": {
            "all_296_remaining_packets_reviewed": len(packets) == EXPECTED_PACKET_COUNT,
            "all_382_remaining_comparisons_reviewed": len(comparisons) == EXPECTED_COMPARISON_COUNT,
            "comparison_keys_unique": len(set(keys)) == len(keys),
            "manual_judgements_are_in_scope": manual <= set(keys),
            "semantic_meaning_not_assessed": all(r["field_findings"]["semantic_meaning"] == "not_assessed" for r in comparisons),
        },
    }


def _write(path: Path, value: dict[str, Any]) -> None:
    encoded = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
        raise


def main(argv: list[str] | None = None) -> int:
    """Write a validated remaining-candidate manual-review artifact."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--truth", type=Path, default=DEFAULT_TRUTH_AUDIT)
    parser.add_argument("--mtf-review", type=Path, default=DEFAULT_MTF_REVIEW)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hardened-mtf-reconciled", action="store_true")
    args = parser.parse_args(argv)
    review = build_review(args.truth, args.mtf_review, hardened_mtf_reconciled=args.hardened_mtf_reconciled)
    _write(args.output, review)
    print(json.dumps({"output": str(args.output), **review["counts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
