import hashlib
import json
from pathlib import Path

from historical_context_mtf_remaining_manual_review import (
    _aggregate_packet_disposition,
    _review,
    build_review,
)


ROOT = Path(__file__).resolve().parents[1]
TRUTH = ROOT / "historical_context_evidence_truth_audit.json"
MTF = ROOT / "historical_context_mtf_primary_review.json"
OUTPUT = ROOT / "historical_context_mtf_remaining_manual_review.json"


def test_remaining_review_is_complete_conservative_and_current():
    saved = json.loads(OUTPUT.read_text(encoding="utf-8"))
    review = build_review(
        TRUTH,
        MTF,
        hardened_mtf_reconciled=(
            saved.get("review_state")
            == "final_reconciled_with_hardened_mtf_review"
        ),
    )

    assert saved == review
    assert review["counts"]["remaining_packet_count"] == 296
    assert review["counts"]["remaining_comparison_count"] == 382
    assert review["counts"]["comparison_dispositions"] == {
        "mismatch": 137,
        "uncertain": 86,
        "verified": 159,
    }
    assert review["counts"]["packet_dispositions"] == {
        "mismatch": 129,
        "uncertain": 56,
        "verified": 111,
    }
    assert all(review["invariants"].values())
    assert review["review_state"] == "final_reconciled_with_hardened_mtf_review"
    assert review["scope"]["semantic_meaning_assessed"] is False
    assert review["scope"]["packet_or_evidence_mutation_authorised"] is False
    assert review["input_hashes"][TRUTH.name] == hashlib.sha256(
        TRUTH.read_bytes()
    ).hexdigest()
    assert review["input_hashes"][MTF.name] == hashlib.sha256(
        MTF.read_bytes()
    ).hexdigest()


def test_remaining_review_packet_aggregation_is_worst_case():
    review = build_review(TRUTH, MTF)
    packet = next(
        row
        for row in review["packets"]
        if row["quote_id"]
        == "00426881d2746c35657e8bb3103febb15cf13f2342bb65604a23a278b608064d"
    )

    assert packet["comparison_dispositions"] == {
        "mismatch": 1,
        "verified": 1,
    }
    assert packet["disposition"] == "mismatch"

    assert _aggregate_packet_disposition({
        "retrieval_failed", "uncertain",
    }) == "retrieval_failed"

    assert all(
        row["field_findings"]["semantic_meaning"] == "not_assessed"
        for row in review["comparisons"]
    )


def test_not_found_document_is_identity_mismatch_not_transport_failure():
    row = _review({
        "quote_id": "a" * 64,
        "document_number": "109236",
        "identity_provenance": ["packet_locator_candidate"],
        "status": "official_document_not_found",
        "official_url": "https://www.margaretthatcher.org/document/109236",
    })

    assert row["disposition"] == "mismatch"
    assert row["field_findings"] == {
        "archive_identity": "mismatch",
        "source_event": "unavailable",
        "date": "unavailable",
        "quotation_linkage": "unavailable",
        "semantic_meaning": "not_assessed",
    }
    assert "official_archive_document_not_found" in row["actionable_findings"]
