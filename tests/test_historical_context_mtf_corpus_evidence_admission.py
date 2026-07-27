"""Regression tests for the reviewed MTF corpus-evidence transition."""
from __future__ import annotations

import json
from pathlib import Path

from historical_context_formatter import load_and_validate_corpus
from historical_context_mtf_corpus_evidence_admission import SOURCE_RECORDS
from historical_context_reply_semantic_gate import (
    load_historical_context_semantic_gate,
)


ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "semantic_alignment_research" / "quote_research_full_001"
CURATED = RESEARCH / "historical_context_source_curated_evidence.json"
RUNTIME = (
    ROOT
    / "semantic_alignment_research"
    / "quote_attribution_cleanup_001"
    / "deployment_candidate"
    / "runtime_eligible_quote_manifest.json"
)
TRANSITION = (
    ROOT / "historical_context_v9_mtf_corpus_evidence_transition_manifest.json"
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_reviewed_mtf_bindings_are_exact_and_claim_scoped() -> None:
    """All ten reviewed bindings retain exact source and candidate identity."""
    packets, unresolved = load_and_validate_corpus(
        RESEARCH,
        require_source_role_audit=True,
    )
    curated = _load(CURATED)
    exact = 0
    variants = 0
    transition = _load(TRANSITION)
    for quote_id, record in SOURCE_RECORDS.items():
        packet = packets[quote_id]
        source_id = transition["items"][quote_id]["source_bindings"][0][
            "source_id"
        ]
        source = next(
            item
            for item in curated["items"][quote_id]["sources"]
            if item["source_id"] == source_id
        )
        expected_status = (
            "exact" if record["match_kind"] == "exact" else "variant"
        )
        exact += expected_status == "exact"
        variants += expected_status == "variant"
        assert packet["verification_status"] == expected_status
        assert record["candidate_id"] in {
            source["source_review_candidate_id"],
            source.get("prior_source_review_candidate_id"),
        }
        assert source["canonical_url"].endswith(
            f"/document/{record['document']}"
        )
        assert source["source_publisher"] == "Margaret Thatcher Foundation"
        assert source["source_quality_class"] == "strong_primary_evidence"
        assert source["source_type"] == "official_primary_transcript"
        assert source["wording_match_kind"] == record["match_kind"]
        assert {
            "wording_verification",
            "attribution_support",
            "source_event_support",
        } <= set(source["assigned_roles"])
        assert {
            "wording",
            "attribution",
            "source_event",
            "date",
        } <= set(source["claims_supported"])
        assert record["file_sha256"] in {
            source["page_sha256"],
            source.get("prior_page_sha256"),
        }
        assert record["text_sha256"] in {
            source["page_text_sha256"],
            source.get("prior_page_text_sha256"),
        }
        assert "/disks/" not in json.dumps(source)
    assert (exact, variants) == (4, 6)
    assert len(packets) == 627
    assert len(unresolved) == 5


def test_reviewed_variants_preserve_their_precise_differences() -> None:
    """Variant packets expose reviewed primary wording and difference notes."""
    packets, _unresolved = load_and_validate_corpus(
        RESEARCH,
        require_source_role_audit=True,
    )
    expected = {
        "5fbdcee710fe7e18425f4eeefe811890b3c8a03803f78b23685ad11309840679":
        ("re-imposed", "European level"),
        "677bda2ba3097d2452133f66a0eab9c9740a06a0be8d53bdd712f52b53ff7bab":
        ("The Russians", "single sentence"),
        "6cab54a1bfcbd9c79b72c39ff64eb7126436cde07c37d48aa6fcd9ddfec4f662":
        ("who is society?", "recasts"),
        "98000f36211d96c33ca0e033551ef2a7b24f56f5d624768c13abf666f5c61fe5":
        ("but just being", "transcription error"),
        "a8b53417a59ef215988e22c6c44d52e6a8401fec6ba89400001ba1e778b04855":
        ("organized", "organised"),
        "b301858e2ba14514c52ef64b217348cfceabe769c1530033761a2fef8c4304e8":
        ("to whom they want to", "In a free society"),
    }
    for quote_id, fragments in expected.items():
        packet = packets[quote_id]
        assert packet["verification_status"] == "variant"
        assert packet["verified_text"] != packet["quote_text"]
        assert all(
            fragment in packet["text_variation_notes"]
            for fragment in fragments
        )


def test_transition_preserves_runtime_and_uses_current_gate_partition() -> None:
    """The earlier transition remains valid under the reviewed 13-ID gate."""
    runtime = _load(RUNTIME)
    transition = _load(TRANSITION)
    gate = load_historical_context_semantic_gate(
        root=ROOT,
        eligible_quote_ids=runtime["resolved_manifest_quote_ids"],
    )
    assert transition["counts"] == {
        "public_field_changes": 8,
        "source_additions": 10,
        "transition_packets": 10,
    }
    assert set(transition["items"]) == set(SOURCE_RECORDS)
    assert runtime["runtime_eligible_quote_count"] == 611
    assert gate.available is True
    assert len(gate.blocked_dispositions) == 13
    assert not set(SOURCE_RECORDS) & set(gate.blocked_dispositions)


def test_admission_audit_records_no_meaning_or_quotation_change() -> None:
    """The operator transition changed source metadata, not public wording."""
    audit = _load(
        ROOT / "historical_context_mtf_corpus_evidence_admission_audit.json"
    )
    assert audit["counts"] == {
        "exact_primary": 4,
        "primary_variant": 6,
        "source_records_admitted": 10,
    }
    assert audit["meaning_fields_changed"] == 0
    assert audit["quotation_text_changed"] is False
    assert audit["network_requests"] == 0
    assert audit["provider_requests"] == 0
