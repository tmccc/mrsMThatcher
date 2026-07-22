import hashlib
import json
from pathlib import Path

import pytest

import historical_context_evidence_truth_audit as truth_audit


ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "semantic_alignment_research" / "quote_research_full_001"
HISTORY = ROOT / "historical_context_reply_history.json"
KNOWN_104653_QUOTE_ID = (
    "e1d78bc63369145f6cf7462d8ad5f15dceef929c0469aff64d3bde1e7a188f18"
)
APPROXIMATE_PACKET_LOCATOR_QUOTE_ID = (
    "edbdfcd279a304940c6e1001ee013da4e45de9f75bfd9fbd26c863e7cfae1a02"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_section_parser_handles_legacy_and_current_public_styles():
    legacy = truth_audit._sections(
        "Context\nSpoken during: A speech.\nDate: 20 May 1981\n\n"
        "Meaning\nA restrained explanation.\n\nVerification: Exact wording"
    )
    current = truth_audit._sections(
        "Context — A speech, 20 May 1981.\n\n"
        "Meaning — A restrained explanation.\n\n"
        "Verification — Exact wording verified"
    )
    assert legacy == {
        "context": "Spoken during: A speech. Date: 20 May 1981",
        "meaning": "A restrained explanation.",
        "verification": "Exact wording",
    }
    assert current == {
        "context": "A speech, 20 May 1981.",
        "meaning": "A restrained explanation.",
        "verification": "Exact wording verified",
    }


def test_meaning_indicators_are_high_recall_leads_not_duplicate_quote_terms():
    quote = "Some aims result in the subjugation of rights to political theory."
    meaning = (
        "Socialism inevitably shares core aims and inherently causes the "
        "suppression of rights in favour of state power."
    )
    assert truth_audit._meaning_indicators(quote, meaning) == [
        "core_or_fundamental",
        "inevitability",
        "inherence",
        "state_power",
        "suppression_or_destruction",
    ]
    assert truth_audit._meaning_indicators(
        "The outcome is inevitable.", "The outcome is inevitably harmful."
    ) == []


def test_claim_field_comparison_does_not_treat_packet_prose_as_evidence():
    packet = {
        "source_event": "Conference speech",
        "date": "20 May 1981",
        "historical_context": "A model-generated contextual claim.",
    }
    audit = {
        "public_context_supported_fields": ["source_event", "date"],
        "renderable_sources": [{
            "assigned_roles": ["source_event_support"],
            "claims_supported": ["source_event"],
        }],
    }
    assert truth_audit._claim_field_findings(packet, audit) == [
        {
            "kind": "packet_claim_not_publicly_supported",
            "field": "historical_context",
        },
        {
            "kind": "public_field_without_explicit_source_claim",
            "field": "date",
        },
    ]


def test_exact_mtf_identity_uses_production_canonicalizer():
    assert truth_audit._mtf_documents({
        "source_title": "Margaret Thatcher Foundation document 104653",
    }) == ["104653"]
    assert truth_audit._mtf_documents({
        "source_url": "https://www.margaretthatcher.org/document/104653#speech",
    }) == ["104653"]
    assert truth_audit._mtf_documents({
        "source_title": "Unrelated publication document 104653",
    }) == []


def test_full_audit_correlates_history_and_corpus_without_mutating_inputs():
    protected = [
        HISTORY,
        RESEARCH / "research_packets.json",
        RESEARCH / "historical_context_source_role_audit.json",
        RESEARCH / "historical_context_packet_corrections.json",
        RESEARCH / "historical_context_source_curated_evidence.json",
        ROOT / "mrsMThatcher.txt",
        ROOT / "quote_analysis.json",
    ]
    before = {path: _sha256(path) for path in protected}
    audit = truth_audit.build_audit(RESEARCH, HISTORY, reference_root=ROOT)
    after = {path: _sha256(path) for path in protected}

    assert before == after
    assert audit["coverage"]["completed_packet_count"] == 626
    assert audit["coverage"]["published_history_entry_count"] >= 77
    assert audit["coverage"]["published_reply_review_record_count"] == audit[
        "coverage"
    ]["published_history_entry_count"]
    assert audit["coverage"]["current_public_render_record_count"] == 626
    assert len(audit["records"]["published_reply_reviews"]) == audit[
        "coverage"
    ]["published_history_entry_count"]
    assert len(audit["records"]["current_public_renderings"]) == 626
    assert audit["coverage"]["attribution_eligible_packet_count"] == 610
    assert audit["counts"]["correlation_error_count"] == 0
    assert audit["counts"]["current_render_failure_count"] == 0
    assert audit["counts"]["invariant_failure_count"] == 0
    assert all(audit["invariants"].values())

    same_documents = {
        row["quote_id"]: row
        for row in audit["records"][
            "accepted_and_lead_same_mtf_document"
        ]
    }
    known = same_documents[KNOWN_104653_QUOTE_ID]
    assert known["document_numbers"] == ["104653"]
    assert known["suppressed_event_or_date_fields"] == []
    assert known["source_event"] == "Speech to Conservative Women's Conference"
    assert known["date"] == "20 May 1981"
    assert {"date", "source_event"}.issubset(
        known["public_context_supported_fields"]
    )

    history_findings = {
        row["quote_id"]: row
        for row in audit["records"]["published_generic_or_no_date_contexts"]
    }
    assert "published_generic_context" in history_findings[
        KNOWN_104653_QUOTE_ID
    ]["flags"]
    meaning_findings = {
        row["quote_id"]: row
        for row in audit["records"]["meaning_strengthening_indicators"]
    }
    known_meaning = meaning_findings[KNOWN_104653_QUOTE_ID]
    future_indicators = {
        indicator
        for surface in known_meaning["surfaces"]
        if surface["surface"] != "published_public_meaning"
        for indicator in surface["indicators"]
    }
    published_indicators = {
        indicator
        for surface in known_meaning["surfaces"]
        if surface["surface"] == "published_public_meaning"
        for indicator in surface["indicators"]
    }
    assert {
        "inevitability",
        "inherence",
    }.isdisjoint(future_indicators)
    assert {"inevitability", "inherence"} <= published_indicators

    precise_identities = {
        row["quote_id"]: row
        for row in audit["records"]["precise_mtf_identities"]
    }
    known_identity = precise_identities[KNOWN_104653_QUOTE_ID]
    assert known_identity["accepted_document_numbers"] == ["104653"]
    assert known_identity["packet_locator_candidate_document_numbers"] == [
        "104653"
    ]

    approximate = precise_identities[APPROXIMATE_PACKET_LOCATOR_QUOTE_ID]
    assert approximate["accepted_document_numbers"] == ["104066"]
    assert approximate["packet_locator_candidate_document_numbers"] == [
        "104077"
    ]
    assert approximate["public_document_numbers"] == ["104066"]


def test_output_guard_and_deterministic_cli_artifact(tmp_path):
    with pytest.raises(ValueError, match="immutable production inputs"):
        truth_audit._validated_output_path(
            HISTORY,
            research_dir=RESEARCH,
            history_path=HISTORY,
            overwrite=False,
        )
    foreign = tmp_path / "foreign.json"
    foreign.write_text('{"kind":"not-this-audit"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="prior audit artifact"):
        truth_audit._validated_output_path(
            foreign,
            research_dir=RESEARCH,
            history_path=HISTORY,
            overwrite=True,
        )

    first = tmp_path / "truth-audit.json"
    assert truth_audit.main([
        "--research-dir", str(RESEARCH),
        "--history", str(HISTORY),
        "--output", str(first),
    ]) == 0
    first_bytes = first.read_bytes()
    first_value = json.loads(first_bytes)
    assert first_value["audit_kind"] == truth_audit.AUDIT_KIND
    assert first_value["interpretation"]["automatic_evidence_rewrite_authorised"] is False

    assert truth_audit.main([
        "--research-dir", str(RESEARCH),
        "--history", str(HISTORY),
        "--output", str(first),
        "--overwrite",
    ]) == 0
    assert first.read_bytes() == first_bytes
