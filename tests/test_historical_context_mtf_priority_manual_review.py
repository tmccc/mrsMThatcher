import hashlib
import json
from pathlib import Path

from historical_context_mtf_priority_manual_review import build_review


ROOT = Path(__file__).resolve().parents[1]
TRUTH = ROOT / "historical_context_evidence_truth_audit.json"
MTF = ROOT / "historical_context_mtf_primary_review.json"
OUTPUT = ROOT / "historical_context_mtf_priority_manual_review.json"


def test_priority_review_is_complete_conservative_and_current():
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
    assert review["counts"]["priority_packet_count"] == 139
    assert review["counts"]["priority_comparison_count"] == 148
    assert review["counts"]["current_truth_priority_packet_count"] == 133
    assert (
        review["counts"]["reviewed_but_no_longer_current_priority_count"]
        == 6
    )
    assert review["counts"]["comparison_dispositions"] == {
        "mismatch": 29,
        "retrieval_failed": 0,
        "uncertain": 43,
        "verified": 76,
    }
    assert review["counts"]["packet_dispositions"] == {
        "mismatch": 28,
        "retrieval_failed": 0,
        "uncertain": 40,
        "verified": 71,
    }
    assert all(review["invariants"].values())
    assert review["scope"]["semantic_meaning_assessed"] is False
    assert review["scope"]["automatic_evidence_promotion_authorised"] is False
    assert review["scope"][
        "reviewed_but_no_longer_current_priority_quote_ids"
    ] == [
        "63a705d3b574f9294af663894a908053d17df31c1b177fca7997caa36299a5f5",
        "6cab54a1bfcbd9c79b72c39ff64eb7126436cde07c37d48aa6fcd9ddfec4f662",
        "6cc1934843f9e7ab1ee3baf477359078f1b17a45b630dc779ee5561b3b9128f7",
        "7066fdf6027a1cbdc45dad3ef5cd95d0ee67a6500e519480b3a0814a266dc428",
        "b301858e2ba14514c52ef64b217348cfceabe769c1530033761a2fef8c4304e8",
        "d9028da9c6518f578ea0840ab4ae6ed5e3a94028cfb0d4c924a476d19df838c9",
    ]
    assert review["input_hashes"][TRUTH.name] == hashlib.sha256(
        TRUTH.read_bytes()
    ).hexdigest()
    assert review["input_hashes"][MTF.name] == hashlib.sha256(
        MTF.read_bytes()
    ).hexdigest()


def test_priority_review_records_actionable_event_identity_defects():
    saved = json.loads(OUTPUT.read_text(encoding="utf-8"))
    review = build_review(
        TRUTH,
        MTF,
        hardened_mtf_reconciled=(
            saved.get("review_state")
            == "final_reconciled_with_hardened_mtf_review"
        ),
    )
    rows = {
        (row["quote_id"], row["document_number"]): row
        for row in review["comparisons"]
    }

    wrong_publication = rows[(
        "80aba68d430bac98105a1d22006902ce0107590985f70e5dcd5f2399fe44ad20",
        "104066",
    )]
    assert wrong_publication["disposition"] == "mismatch"
    assert wrong_publication["field_findings"]["source_event"] == "mismatch"
    assert "packet_source_event_conflicts_with_official_document" in (
        wrong_publication["actionable_findings"]
    )

    unsupported_specificity = rows[(
        "d238243726b4a6ea0bf7c6bbd3080ee59cb31f92a87ce19a36e3d789da425252",
        "108383",
    )]
    assert unsupported_specificity["disposition"] == "uncertain"
    assert unsupported_specificity["field_findings"]["source_event"] == "uncertain"

    packets = {row["quote_id"]: row for row in review["packets"]}
    mixed_verified_and_mismatch = packets[
        "19b4b12f040bed61c1804b573327e36f4cc485636d799602a476e0b89fc8e8b1"
    ]
    assert mixed_verified_and_mismatch["comparison_dispositions"] == {
        "mismatch": 2,
        "verified": 1,
    }
    assert mixed_verified_and_mismatch["disposition"] == "mismatch"

    assert all(
        row["field_findings"]["semantic_meaning"] == "not_assessed"
        for row in review["comparisons"]
    )
