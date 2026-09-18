"""Regression coverage for the reviewed Statecraft primary-source admission."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from historical_context_formatter import (
    load_and_validate_corpus,
    packet_is_attributed_to_margaret_thatcher,
    quote_text_hash,
)
from historical_context_reply_semantic_gate import (
    load_historical_context_semantic_gate,
)


ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "semantic_alignment_research" / "quote_research_full_001"
PDF_SHA256 = (
    "6619eb3401336585ef5d27c46fdd2dbc101a66c484620b57c8cda62ab1140872"
)
OCR_SHA256 = (
    "24582e795eec4a7e46b674563ab53c05e1dbdc2c500f688fd7393a01f6490fe1"
)
EXPECTED = {
    "0a67f403a7ac02347e43791d2daf3057aabdcfd64b62edbe1b3484a3a4b66729": (
        "75fd4da816b92425452a48ce4ffd8dab2c538f857c8de0de5c18d085bd329456",
        "fa4a0ad84a0b41b9494093de1d0e8a2db725e04b2263079db8a68f50f812a5b5",
        "427",
        "exact",
    ),
    "685ddfab242fe45cafc203a937769a4fe925423baf80e022b6e2e4411dd3ce90": (
        "192e8d138fa01dbf160ede438db59ca5c1f26321df2d3b04ffeb60d11b396b95",
        "9eae0aa97a4bb82e5a518e29d01c1706be4924e0bd3f765773ef49e5994f7f54",
        "449",
        "exact",
    ),
    "928a6686bc6bb6d35cd1ec139373cb73b85ba9fa40807098d5572ae153dab144": (
        "1541cd79b74e97ede1bfd089ad8e0fe73f3096463d3c44edc5425ac8be9d7947",
        "81f316c816a06cff0bd3bb02f1ad5420a406efba74e80723815fe409bdd63079",
        "432",
        "exact",
    ),
    "a4f1d422097a48114bf30a587c04cf05859ff030d2df3d5d9051c6ca57a7943c": (
        "67610f7a0dcd4474e811f05f0b5e0a1e8fbc6632afa85553acd43cc0281466ad",
        "add06ddd94e0829f5263b44e7ceb5f11136d8b789cc2e0d5b88ff53e2d93636f",
        "433",
        "variant",
    ),
    "a97e6dd2f444ecfbba67977a34be91db40d17eb09c8566fe714e48bffddb11f7": (
        "9016f29ef388fe954005aa090add8fe86bd9fd8b1b1db4997548cd200257adf4",
        "fb04f2faf8f1297768446e9ccdd4014564f3fd34bb92ef0b3924f0e326895bbc",
        "425",
        "variant",
    ),
    "db46e7519946d4312907a8b7c7337eea0daaf3689850c2ef35035a6bda062173": (
        "38efb1e0e9571009544c592a48385902e02d0d4d3fcb453eb31f073bcb1f7416",
        "202db856ef09a53e672eb76d13d1c6f61463af2f8bce4ce4e2a8848850be5f3e",
        "256",
        "exact",
    ),
    "f4323817daee5cef16fa5d83879823f2da5506152fcb7b1b1ce5c777ac036d4d": (
        "66276ffca1cca9b2b00344a329a7cc850652b700d26e033ca43ea659f25eb897",
        "076e7b6c6babc17543676607ca7cd4289378ff7d0143334cbf1c4e0372601934",
        "327",
        "variant",
    ),
}


def _load(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_reviewed_statecraft_bindings_are_exact_and_claim_scoped() -> None:
    curated = _load(
        "semantic_alignment_research/quote_research_full_001/"
        "historical_context_source_curated_evidence.json"
    )
    packets, unresolved = load_and_validate_corpus(
        RESEARCH,
        require_source_role_audit=True,
    )

    for quote_id, (candidate_id, source_id, page, status) in EXPECTED.items():
        packet = packets[quote_id]
        item = curated["items"][quote_id]
        assert item["quote_text"] == packet["quote_text"]
        source = item["sources"][0]
        assert source["source_review_candidate_id"] == candidate_id
        assert source["source_id"] == source_id
        assert source["printed_page"] == page
        assert source["pdf_sha256"] == PDF_SHA256
        assert source["ocr_text_sha256"] == OCR_SHA256
        assert source["source_quality_class"] == "strong_primary_evidence"
        assert source["source_type"] == "thatcher_authored_primary_book"
        assert source["page_independently_inspected"] is True
        assert source["printed_page_visually_verified"] is True
        assert source["assigned_roles"] == [
            "wording_verification",
            "attribution_support",
            "source_event_support",
        ]
        assert source["claims_supported"] == [
            "wording",
            "attribution",
            "source_event",
            "date",
        ]
        assert "historical_context_support" not in source["assigned_roles"]
        assert "historical_context" not in source["claims_supported"]
        assert packet["verification_status"] == status
        assert hashlib.sha256(
            packet["quote_text"].encode("utf-8")
        ).hexdigest() == quote_id

    manifest = _load(
        "semantic_alignment_research/quote_research_full_001/corpus_manifest.json"
    )
    assert set(packets) == {row["quote_id"] for row in manifest["records"]} - unresolved
    assert len(unresolved) == 5
    assert not set(EXPECTED) & unresolved


def test_statecraft_variants_preserve_public_text_and_precise_differences() -> None:
    packets, _unresolved = load_and_validate_corpus(
        RESEARCH,
        require_source_role_audit=True,
    )
    expected_notes = {
        "a4f1d422097a48114bf30a587c04cf05859ff030d2df3d5d9051c6ca57a7943c": (
            "stored quotation begins 'When all the objectives'",
            "source begins 'When the objectives'",
        ),
        "a97e6dd2f444ecfbba67977a34be91db40d17eb09c8566fe714e48bffddb11f7": (
            "leading editorial ellipsis",
            "primary sentence does not",
        ),
        "f4323817daee5cef16fa5d83879823f2da5506152fcb7b1b1ce5c777ac036d4d": (
            "shortens the opening",
            "omits 'the' and 'grand'",
        ),
    }
    for quote_id, fragments in expected_notes.items():
        packet = packets[quote_id]
        assert packet["verification_status"] == "variant"
        assert packet["verified_text"] != packet["quote_text"]
        assert all(
            fragment in packet["text_variation_notes"]
            for fragment in fragments
        )


def test_runtime_partition_and_context_gate_are_fail_closed() -> None:
    packets, unresolved = load_and_validate_corpus(
        RESEARCH,
        require_source_role_audit=True,
    )
    runtime = _load(
        "semantic_alignment_research/quote_attribution_cleanup_001/"
        "deployment_candidate/runtime_eligible_quote_manifest.json"
    )
    runtime_ids = set(runtime["resolved_manifest_quote_ids"])
    gate = load_historical_context_semantic_gate(
        root=ROOT,
        eligible_quote_ids=runtime_ids,
    )

    manifest = _load(
        "semantic_alignment_research/quote_research_full_001/corpus_manifest.json"
    )
    assert set(packets) == {row["quote_id"] for row in manifest["records"]} - unresolved
    assert len(unresolved) == 5
    expected_ids = {
        quote_id for quote_id, packet in packets.items()
        if packet_is_attributed_to_margaret_thatcher(packet)
    }
    assert runtime_ids == expected_ids
    assert len(runtime["resolved_manifest_quote_ids"]) == len(expected_ids)
    assert runtime["runtime_eligible_quote_count"] == len(expected_ids)
    assert len(runtime["runtime_eligible_quote_ids"]) == len(expected_ids)
    assert set(runtime["runtime_eligible_quote_ids"]) == {
        quote_text_hash(packets[quote_id]["quote_text"]) for quote_id in expected_ids
    }
    assert set(EXPECTED) <= runtime_ids
    assert gate.available is True
    assert len(gate.blocked_dispositions) == 13
    for quote_id in {
        "a4f1d422097a48114bf30a587c04cf05859ff030d2df3d5d9051c6ca57a7943c",
        "a97e6dd2f444ecfbba67977a34be91db40d17eb09c8566fe714e48bffddb11f7",
        "db46e7519946d4312907a8b7c7337eea0daaf3689850c2ef35035a6bda062173",
        "f4323817daee5cef16fa5d83879823f2da5506152fcb7b1b1ce5c777ac036d4d",
        "928a6686bc6bb6d35cd1ec139373cb73b85ba9fa40807098d5572ae153dab144",
    }:
        assert gate.blocks(quote_id) is True
    for quote_id in {
        "0a67f403a7ac02347e43791d2daf3057aabdcfd64b62edbe1b3484a3a4b66729",
        "685ddfab242fe45cafc203a937769a4fe925423baf80e022b6e2e4411dd3ce90",
    }:
        assert gate.blocks(quote_id) is False
