"""Regression tests for hash-bound historical-context prose corrections."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from historical_context_formatter import (
    format_context_reply_public,
    load_and_validate_corpus,
)
from historical_context_packet_corrections import (
    PACKET_CORRECTIONS_FILENAME,
    apply_packet_corrections,
    validate_packet_corrections,
)
from historical_context_source_curated_evidence import (
    CURATED_EVIDENCE_FILENAME,
    validate_curated_evidence,
)


RESEARCH_DIR = Path("semantic_alignment_research/quote_research_full_001")
QUOTE_ID = "e1d78bc63369145f6cf7462d8ad5f15dceef929c0469aff64d3bde1e7a188f18"
SOURCE_ID = "729ef0ab1006098f0e0b1dbfb6fefc99410908a4d980638d48c024ee268c55f1"
CORRECTED_MEANING = (
    "Thatcher argued that socialism shares some aims with Marxism and that "
    "pursuing those aims subordinates individual rights to political doctrine."
)
LOCAL_BOOK_CORRECTION_IDS = {
    "4f5e783f4957dc615742df2b827214e539a5123af1b4863822ba2e52684a0d80",
    "cac5746ca684f9611a25dcfb6b024ed63bfb3d41b2fa4c5c3d6e44290378d4ea",
    "e259f9a77a234e4d03f415740045fb374b7c68eba06f857d7c79a73500dafe37",
    "f0d85c7301e8b27bc694ac030d7c5f6b1d15ff3bcdf31cbdfb03a1c05bbe83ea",
}


@pytest.fixture(scope="module")
def evidence_documents():
    packets = json.loads(
        (RESEARCH_DIR / "research_packets.json").read_text(encoding="utf-8")
    )["items"]
    curated = json.loads(
        (RESEARCH_DIR / CURATED_EVIDENCE_FILENAME).read_text(encoding="utf-8")
    )
    corrections = json.loads(
        (RESEARCH_DIR / PACKET_CORRECTIONS_FILENAME).read_text(encoding="utf-8")
    )
    return packets, curated, corrections


def test_document_104653_primary_evidence_is_claim_scoped(evidence_documents):
    packets, curated, _corrections = evidence_documents
    validate_curated_evidence(curated, packets)

    source = curated["items"][QUOTE_ID]["sources"][0]
    assert source["source_id"] == SOURCE_ID
    assert source["url"] == "https://www.margaretthatcher.org/document/104653"
    assert source["source_event"] == "Speech to Conservative Women's Conference"
    assert source["source_date"] == "20 May 1981"
    assert "Central Hall, Westminster" in source["title"]
    assert source["page_independently_inspected"] is True
    assert set(source["assigned_roles"]) == {
        "wording_verification",
        "attribution_support",
        "source_event_support",
    }
    assert set(source["claims_supported"]) == {
        "wording",
        "attribution",
        "source_event",
        "date",
    }
    assert "historical_context_support" not in source["assigned_roles"]
    assert "historical_context" not in source["claims_supported"]
    assert source["exact_supporting_passage"].startswith("And never forget")
    assert "No historical-context role is assigned" in source["rationale"]
    assert source["wording_match_kind"] == "normalised"


def test_document_104653_correction_changes_only_future_meaning_view(
    evidence_documents,
):
    packets, curated, corrections = evidence_documents
    original_packets = copy.deepcopy(packets)

    corrected = apply_packet_corrections(corrections, packets, curated)

    assert corrected[QUOTE_ID]["intended_argument"] == CORRECTED_MEANING
    assert packets == original_packets
    assert corrected[QUOTE_ID]["quote_id"] == packets[QUOTE_ID]["quote_id"]
    assert corrected[QUOTE_ID]["quote_text"] == packets[QUOTE_ID]["quote_text"]
    assert corrected[QUOTE_ID]["date"] == packets[QUOTE_ID]["date"]
    assert corrected[QUOTE_ID]["source_event"] == packets[QUOTE_ID]["source_event"]
    assert corrected[QUOTE_ID]["sources"] == packets[QUOTE_ID]["sources"]
    assert set(corrected) == set(packets)
    correction_ids = set(corrections["items"])
    assert correction_ids == {
        quote_id
        for quote_id, packet in packets.items()
        if corrected[quote_id] is not packet
    }
    assert len(correction_ids) == 15
    assert LOCAL_BOOK_CORRECTION_IDS <= correction_ids
    assert "inevitably" not in corrected[QUOTE_ID]["intended_argument"]
    assert "inherently" not in corrected[QUOTE_ID]["intended_argument"]
    assert "state power" not in corrected[QUOTE_ID]["intended_argument"]


def test_runtime_loader_applies_104653_correction_after_audit_attachment():
    packets, _unresolved = load_and_validate_corpus(
        RESEARCH_DIR,
        require_source_role_audit=True,
    )
    packet = packets[QUOTE_ID]
    rendered = format_context_reply_public(packet)

    assert packet["intended_argument"] == CORRECTED_MEANING
    assert packet["_source_role_audit"]["public_context_supported_fields"] == [
        "source_event",
        "date",
    ]
    assert rendered is not None
    assert rendered["text"] == (
        "Context — Speech to Conservative Women's Conference, 20 May 1981.\n\n"
        f"Meaning — {CORRECTED_MEANING}\n\n"
        "Verification — Verified excerpt\n\n"
        "Source — Margaret Thatcher Foundation, Speech to Conservative Women's "
        "Conference, Central Hall, Westminster, 20 May 1981 (Document 104653)\n"
        "https://www.margaretthatcher.org/document/104653"
    )
    assert "historical_context" not in packet["_source_role_audit"][
        "public_context_supported_fields"
    ]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda manifest: manifest["items"][QUOTE_ID].__setitem__(
                "field", "historical_context"
            ),
            "identity differs",
        ),
        (
            lambda manifest: manifest["items"][QUOTE_ID].__setitem__(
                "original_value_sha256", "0" * 64
            ),
            "identity differs",
        ),
        (
            lambda manifest: manifest["items"][QUOTE_ID].__setitem__(
                "corrected_value", "Unsupported replacement."
            ),
            "is invalid",
        ),
    ],
)
def test_packet_correction_fails_closed_on_broader_or_stale_change(
    evidence_documents, mutation, message
):
    packets, curated, corrections = evidence_documents
    altered = copy.deepcopy(corrections)
    mutation(altered)
    with pytest.raises(RuntimeError, match=message):
        validate_packet_corrections(altered, packets, curated)


def test_packet_correction_rejects_downgraded_evidence(evidence_documents):
    packets, curated, corrections = evidence_documents
    downgraded = copy.deepcopy(curated)
    source = downgraded["items"][QUOTE_ID]["sources"][0]
    source["claims_supported"].remove("wording")
    with pytest.raises(RuntimeError, match="curated source is invalid"):
        validate_packet_corrections(corrections, packets, downgraded)
