"""Focused fail-closed tests for operator-curated evidence contracts."""
from __future__ import annotations

import copy
import hashlib

import pytest

from historical_context_source_curated_evidence import (
    CURATED_EVIDENCE_POLICY_VERSION,
    CURATED_EVIDENCE_SCHEMA_VERSION,
    curated_source_id,
    curated_wording_coverage,
    validate_curated_evidence,
)
from historical_context_source_roles import audit_packet


QUOTE_ID = "a" * 64


def _packet() -> dict:
    quote = (
        "Freedom of speech is freedom to say what other people disagree with, "
        "not merely things which are anodyne."
    )
    return {
        "quote_id": QUOTE_ID,
        "quote_text": quote,
        "verified_text": quote,
        "verification_status": "exact",
        "speaker": "Margaret Thatcher",
        "source_event": "Synthetic speech",
        "date": "1981-05-20",
        "historical_context": "Synthetic context used only by deterministic tests.",
        "stable_locator": "Unknown",
        "research_confidence": "high",
        "sources": [],
    }


def _source(packet: dict) -> dict:
    passage = (
        "She said: “FREEDOM of speech is freedom to say what other people "
        "disagree with, not merely things which are anodyne”."
    )
    source = {
        "title": "Synthetic primary transcript",
        "url": "https://example.test/archive/document/1",
        "source_type": "primary_transcript",
        "source_event": packet["source_event"],
        "source_date": packet["date"],
        "assigned_roles": [
            "wording_verification",
            "attribution_support",
            "source_event_support",
        ],
        "source_quality_class": "strong_primary_evidence",
        "claims_supported": [
            "wording",
            "attribution",
            "source_event",
            "date",
        ],
        "wording_match_kind": "exact",
        "exact_supporting_passage": passage,
        "exact_supporting_passage_sha256": hashlib.sha256(
            passage.encode("utf-8")
        ).hexdigest(),
        "stable_locator": "Synthetic document 1",
        "rationale": "Synthetic claim-scoped evidence.",
        "evidence_origin": "independently_reviewed_public_retrieval",
        "recorded_at": "2026-07-28",
        "page_independently_inspected": True,
    }
    source["source_id"] = curated_source_id(QUOTE_ID, source)
    return source


def _manifest(packet: dict, source: dict) -> dict:
    quote = packet["quote_text"]
    return {
        "schema_version": CURATED_EVIDENCE_SCHEMA_VERSION,
        "policy_version": CURATED_EVIDENCE_POLICY_VERSION,
        "created_at": "2026-07-28",
        "quote_count": 1,
        "source_count": 1,
        "source_adjudication_count": 0,
        "source_adjudications": [],
        "items": {
            QUOTE_ID: {
                "quote_id": QUOTE_ID,
                "quote_text": quote,
                "quote_text_sha256": hashlib.sha256(
                    quote.encode("utf-8")
                ).hexdigest(),
                "sources": [source],
            }
        },
    }


def test_exact_curated_wording_requires_complete_typographic_match():
    packet = _packet()
    source = _source(packet)
    manifest = _manifest(packet, source)

    validate_curated_evidence(manifest, {QUOTE_ID: packet})

    assert curated_wording_coverage(packet, source) == "full"


def test_exact_curated_wording_rejects_a_lexical_difference():
    packet = _packet()
    source = _source(packet)
    source["exact_supporting_passage"] = source[
        "exact_supporting_passage"
    ].replace("disagree", "agree")
    source["exact_supporting_passage_sha256"] = hashlib.sha256(
        source["exact_supporting_passage"].encode("utf-8")
    ).hexdigest()
    source["source_id"] = curated_source_id(QUOTE_ID, source)
    manifest = _manifest(packet, source)

    with pytest.raises(RuntimeError, match="exact wording does not match"):
        validate_curated_evidence(manifest, {QUOTE_ID: packet})


def test_exact_curated_wording_rejects_an_incomplete_excerpt():
    packet = _packet()
    source = _source(packet)
    source["exact_supporting_passage"] = (
        "Freedom of speech is freedom to say what other people disagree with."
    )
    source["exact_supporting_passage_sha256"] = hashlib.sha256(
        source["exact_supporting_passage"].encode("utf-8")
    ).hexdigest()
    source["source_id"] = curated_source_id(QUOTE_ID, source)
    manifest = _manifest(packet, source)

    with pytest.raises(RuntimeError, match="exact wording does not match"):
        validate_curated_evidence(manifest, {QUOTE_ID: packet})


@pytest.mark.parametrize(
    "mutate",
    [
        lambda source: source["claims_supported"].remove("wording"),
        lambda source: source["assigned_roles"].remove("wording_verification"),
        lambda source: (
            source["claims_supported"].remove("source_event"),
            source["claims_supported"].remove("date"),
        ),
        lambda source: (
            source["claims_supported"].remove("source_event"),
            source["assigned_roles"].remove("source_event_support"),
        ),
    ],
)
def test_curated_roles_and_claims_must_be_cross_consistent(mutate):
    packet = _packet()
    source = _source(packet)
    mutate(source)
    manifest = _manifest(packet, source)

    with pytest.raises(RuntimeError, match="curated source is invalid"):
        validate_curated_evidence(manifest, {QUOTE_ID: packet})


def test_date_only_corroboration_does_not_require_source_event_role():
    packet = _packet()
    source = _source(packet)
    source["source_quality_class"] = "reliable_secondary_evidence"
    source["claims_supported"].remove("source_event")
    source["assigned_roles"].remove("source_event_support")
    manifest = _manifest(packet, source)

    validate_curated_evidence(manifest, {QUOTE_ID: packet})


def test_source_role_audit_cannot_blindly_promote_mismatched_exact_wording():
    packet = _packet()
    source = _source(packet)
    source["exact_supporting_passage"] = source[
        "exact_supporting_passage"
    ].replace("disagree", "agree")

    with pytest.raises(RuntimeError, match="exact wording does not match"):
        audit_packet(
            packet,
            attribution_eligible=True,
            curated_evidence_item={"sources": [copy.deepcopy(source)]},
        )


def _archived_source(packet: dict, policy: object) -> dict:
    """Create otherwise valid archival evidence carrying a fetch-policy version."""
    source = _source(packet)
    canonical = 'https://www.margaretthatcher.org/document/103384'
    source.update(
        url=canonical, canonical_url=canonical,
        source_publisher='Margaret Thatcher Foundation',
        retrieval_archive='Internet Archive Wayback Machine',
        transport_url='https://web.archive.org/web/20200102030405id_/' + canonical,
        archive_capture_timestamp='20200102030405', archive_capture_digest='A' * 32,
        fetch_policy_version=policy, page_sha256='b' * 64, page_text_sha256='c' * 64,
        source_date_raw=packet['date'], date=packet['date'],
    )
    return source


@pytest.mark.parametrize('policy', [
    'historical-context-restricted-fetch-v5', 'historical-context-restricted-fetch-v6',
])
def test_public_curated_validator_retains_compatible_fetch_policy_versions(policy):
    """Previously admitted archival retrievals remain valid without corpus rewrites."""
    packet = _packet()
    validate_curated_evidence(_manifest(packet, _archived_source(packet, policy)), {QUOTE_ID: packet})


def test_public_curated_validator_accepts_current_research_fetch_policy():
    """The producer's current explicit version is understood by its runtime consumer."""
    from historical_context_search_research import FETCH_POLICY_VERSION
    packet = _packet()
    validate_curated_evidence(
        _manifest(packet, _archived_source(packet, FETCH_POLICY_VERSION)), {QUOTE_ID: packet},
    )


@pytest.mark.parametrize('policy', [
    'historical-context-restricted-fetch-v8', 'historical-context-restricted-fetch-v7-extra',
    'historical-context-restricted-fetch-v7 ', '', None, 7, [], {},
])
def test_public_curated_validator_rejects_unknown_or_malformed_fetch_policy(policy):
    """Compatibility stays an explicit allow-list with fail-closed malformed metadata."""
    packet = _packet()
    with pytest.raises(RuntimeError, match='curated source is invalid'):
        validate_curated_evidence(_manifest(packet, _archived_source(packet, policy)), {QUOTE_ID: packet})


@pytest.mark.parametrize('field,value', [
    ('page_sha256', 'not-a-hash'), ('archive_capture_timestamp', '20200102030406'),
    ('canonical_url', 'https://example.test/another-document'),
])
def test_v7_fetch_policy_does_not_relax_archival_provenance(field, value):
    """Recognising v7 leaves document, capture and content-hash identity checks intact."""
    from historical_context_search_research import FETCH_POLICY_VERSION
    packet = _packet()
    source = _archived_source(packet, FETCH_POLICY_VERSION)
    source[field] = value
    with pytest.raises(RuntimeError, match='curated source is invalid'):
        validate_curated_evidence(_manifest(packet, source), {QUOTE_ID: packet})
