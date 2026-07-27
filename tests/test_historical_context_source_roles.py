"""Regression coverage for historical-context evidence roles and recovery."""
from __future__ import annotations

import copy
import json
import hashlib
from pathlib import Path

import httpx
import pytest
import requests
from openai import APIStatusError

from historical_context_formatter import (
    format_context_reply_internal,
    format_context_reply_public,
    format_context_reply_v2,
    load_and_validate_corpus,
    packet_is_attributed_to_margaret_thatcher,
)
from historical_context_source_gemini import (
    HARD_LIMIT_USD,
    ResearchRunner,
    _semantic_similarity,
    build_preflight,
    residual_queue,
    validate_model_result,
    validate_search_result,
    verify_grounding_source,
    verify_source,
)
from historical_context_source_openai import (
    MAX_OUTPUT_TOKENS as OPENAI_MAX_OUTPUT_TOKENS,
    PilotRunner,
    actual_call_cost as openai_actual_call_cost,
    build_preflight as build_openai_preflight,
    extract_cited_sources,
    maximum_call_cost as openai_maximum_call_cost,
    research_prompt as openai_research_prompt,
    resumable_research_queue,
)
from historical_context_source_openai_manifest import (
    OPENAI_RESEARCH_FILENAME,
    validate_openai_research_manifest,
)
from historical_context_source_independent_review import (
    REVIEW_FILENAME,
    validate_independent_review,
)
from historical_context_source_curated_evidence import (
    CURATED_EVIDENCE_FILENAME,
    validate_curated_evidence,
)
from historical_context_source_recovery import validate_recovery
from historical_context_source_research_manifest import validate_research_manifest
from historical_context_source_resolution import validate_resolution
from historical_context_source_roles import (
    AUDIT_FILENAME,
    _public_verification,
    audit_packet,
    audit_researched_source,
    public_sources,
)


RESEARCH_DIR = Path("semantic_alignment_research/quote_research_full_001")
THAMES_ID = "eb1d2ebaac7e321e67174d2db5761d2bd008341ebb04b1abac4d4a927cd7a7d4"
PRIOR_ID = "573412501ec88441938dae368acf42712f3952fd31aeab5c85dcd10ec7c968a4"
ROLLBACK_SOCIALISM_ID = "9c84eb3fbb816db3d01e30e4ab2c09b4c57ade38214abaf4d9db4aedb94da8f4"
HUGO_YOUNG_ID = "52f9b9f99f66ff3bc786183803f3a8d68277604471cd411027441989337c9351"
WOODROW_WYATT_ID = "8143e19d5c4d4e159aa40941118a0aeadf1ea316ed4b0f4ba9f93345326fc407"
VISION_ID = "beefe96a0cd1204c79b2da4a8ed9fabe02dd07f9624a8ef0a6954e24b436f756"
PAUL_JOHNSON_ID = "e7f47c3d78e910d0639668eca12491cb6406ad22191d4acc33dfb45562a5f44b"
OUP_SOURCE_ID = "5daed0133a71b6a1cea243187f7ce47060c95362fc2375d8bbf54b8c90241e80"
MANDATORY_GOOGLE_IDS = {
    "313172d18e2d915e514e4a202a8b1bcbb077472c2504dee63fe98edaf60e0b3a",
    "5f14e6e600773cf394a3f3a3ae21aef10f108691eef50093002571765a5a4a82",
    "677bda2ba3097d2452133f66a0eab9c9740a06a0be8d53bdd712f52b53ff7bab",
    "7c29b290a8e1898c86c39b0cdd23c60161ca73cc068d4a888a19f1f7f3967d0d",
}


@pytest.fixture(scope="module")
def corpus():
    return load_and_validate_corpus(RESEARCH_DIR, require_source_role_audit=True)


@pytest.fixture(scope="module")
def audit():
    return json.loads((RESEARCH_DIR / AUDIT_FILENAME).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def independent_review():
    return json.loads((RESEARCH_DIR / REVIEW_FILENAME).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def curated_evidence():
    return json.loads(
        (RESEARCH_DIR / CURATED_EVIDENCE_FILENAME).read_text(encoding="utf-8")
    )


def test_all_ai_located_sources_have_frozen_independent_page_review(
    audit, independent_review
):
    assert independent_review["reviewed_source_count"] == 34
    assert independent_review["unique_page_count"] == 28
    assert independent_review["approximate_match_count"] == 9
    reviewed = independent_review["items"]
    audited = {
        row["source_id"]: row
        for item in audit["items"].values()
        for row in item["researched_sources"]
    }
    assert set(reviewed) == set(audited)
    for source_id, review in reviewed.items():
        assert review["http_status"] == 200
        assert review["final_url"].startswith("https://")
        assert review["stable_locator"]
        assert review["page_title"]
        assert hashlib.sha256(
            review["exact_supporting_passage"].encode()
        ).hexdigest() == review["exact_supporting_passage_sha256"]
        assert audited[source_id]["independently_reviewed"] is True
        assert audited[source_id]["independent_review_decision"] == review["decision"]


def test_independent_review_fails_closed_on_missing_or_promoted_approximate_source(
    corpus, independent_review
):
    gemini = json.loads(
        (RESEARCH_DIR / "historical_context_source_research.json").read_text()
    )
    openai = json.loads(
        (RESEARCH_DIR / "historical_context_source_openai_research.json").read_text()
    )
    missing = copy.deepcopy(independent_review)
    missing["items"].pop(next(iter(missing["items"])))
    missing["reviewed_source_count"] -= 1
    with pytest.raises(RuntimeError, match="coverage differs"):
        validate_independent_review(missing, corpus[0], gemini, openai)

    promoted = copy.deepcopy(independent_review)
    approximate = next(
        item for item in promoted["items"].values()
        if item["approximate_match_review"] is not None
    )
    approximate["approximate_match_review"]["exact_wording_verified"] = True
    with pytest.raises(RuntimeError, match="invalid approximate source review"):
        validate_independent_review(promoted, corpus[0], gemini, openai)


def test_unsourced_oup_quotation_roundup_is_rejected_not_public(audit):
    item = audit["items"][
        "3fb0e6e6f45d742323a00b0d45fc0a0a524bc0ed0ba11a2c84b6c54c86fb2a0f"
    ]
    source = next(row for row in item["researched_sources"]
                  if row["source_id"] == OUP_SOURCE_ID)
    assert source["source_quality_class"] == "circular_attribution"
    assert source["assigned_roles"] == ["discovery_only"]
    assert source["claims_supported"] == []
    assert source["public_url"] is None
    assert all("blog.oup.com" not in str(row.get("public_url") or "")
               for row in item["renderable_sources"])
    assert any(str(row.get("public_url") or "").endswith(
                   "margaretthatcher.org/document/101374")
               for row in item["renderable_sources"])


def test_nine_approximate_matches_are_semantically_reviewed_and_never_exact(
    audit, independent_review
):
    approximate = [
        item for item in independent_review["items"].values()
        if item["approximate_match_review"] is not None
    ]
    assert len(approximate) == 9
    assert sum(
        item["approximate_match_review"]["historically_defensible"]
        for item in approximate
    ) == 7
    assert all(
        item["approximate_match_review"]["exact_wording_verified"] is False
        for item in approximate
    )
    for review in approximate:
        item = audit["items"][review["quote_id"]]
        assert item["public_verification_wording"] != "Exact wording verified"
        row = next(source for source in item["researched_sources"]
                   if source["source_id"] == review["source_id"])
        if not review["approximate_match_review"]["historically_defensible"]:
            assert "wording" not in row["claims_supported"]
            assert "wording_verification" not in row["assigned_roles"]


def test_recollections_are_counted_and_never_promoted_to_primary(corpus, audit):
    for quote_id in (HUGO_YOUNG_ID, PRIOR_ID, PAUL_JOHNSON_ID):
        item = audit["items"][quote_id]
        recollections = [
            row for row in item["renderable_sources"]
            if row["source_quality_class"] == "secondary_recollection"
        ]
        assert recollections
        assert all("secondary_recollection" in row["assigned_roles"]
                   for row in recollections)
        public = format_context_reply_public(
            corpus[0][quote_id], maximum_length=25_000
        )
        internal = format_context_reply_internal(
            corpus[0][quote_id], maximum_length=25_000
        )
        assert public is not None and internal is not None
        assert "Secondary recollection" not in public["text"]
        assert "Source (" not in public["text"]
        assert "Secondary recollection" in internal["text"]
        assert "Verification — Exact wording verified" not in public["text"]

    woodrow = audit["items"][WOODROW_WYATT_ID]
    assert woodrow["locator_audit"]["precise"] is False
    assert len(woodrow["curated_sources"]) == 1
    source = woodrow["curated_sources"][0]
    assert source["source_quality_class"] == "secondary_recollection"
    assert source["page_independently_inspected"] is False
    assert source["stable_locator"].endswith("p. 166")
    assert "secondary_recollection" in source["assigned_roles"]
    assert source in woodrow["renderable_sources"]
    assert woodrow["requires_further_research"] is True
    assert "Exact wording verified" not in woodrow["public_verification_wording"]
    assert "no primary Thatcher transcript located" in woodrow[
        "public_verification_wording"
    ]
    public = format_context_reply_public(
        corpus[0][WOODROW_WYATT_ID], maximum_length=25_000
    )
    internal = format_context_reply_internal(
        corpus[0][WOODROW_WYATT_ID], maximum_length=25_000
    )
    assert public is not None and internal is not None
    assert "Secondary recollection" not in public["text"]
    assert "Source (" not in public["text"]
    assert "Secondary recollection" in internal["text"]
    assert "Vol. 3, p. 166" in public["text"]
    assert "Exact wording verified" not in public["text"]
    assert audit["summary"]["all_evidentiary_source_quality_counts"][
        "secondary_recollection"
    ] == 5


def test_transcript_heading_does_not_downgrade_complete_normalised_wording(
    corpus, audit
):
    item = audit["items"][VISION_ID]
    assert audit["curated_source_adjudication_count"] == 1
    assert item["curated_source_count"] == 0
    assert item["curated_source_adjudication_count"] == 1
    source = next(
        row for row in item["renderable_sources"]
        if row.get("public_url")
        == "https://www.margaretthatcher.org/document/102777"
    )
    assert source["source_quality_class"] == "strong_primary_evidence"
    assert source["claim_coverage"]["wording"] == "normalised"
    assert source["curated_source_adjudication"]["decision"] == (
        "promote_partial_to_normalised"
    )
    assert item["confidence_after"]["wording"] == "high"
    assert item["public_verification_wording"] == "Normalised wording verified"
    assert item["requires_further_research"] is False
    assert [
        row["url"] for row in public_sources(corpus[0][VISION_ID])
        if row["url"] == source["public_url"]
    ] == [source["public_url"]]


def test_curated_evidence_is_identity_bound_and_fails_closed(
    corpus, curated_evidence
):
    packets, _ = corpus
    validate_curated_evidence(curated_evidence, packets)

    altered_passage = copy.deepcopy(curated_evidence)
    source = altered_passage["items"][WOODROW_WYATT_ID]["sources"][0]
    source["exact_supporting_passage"] += " altered"
    with pytest.raises(RuntimeError, match="curated source is invalid"):
        validate_curated_evidence(altered_passage, packets)

    altered_identity = copy.deepcopy(curated_evidence)
    altered_identity["items"][WOODROW_WYATT_ID]["quote_text"] += " altered"
    with pytest.raises(RuntimeError, match="changed quotation identity"):
        validate_curated_evidence(altered_identity, packets)

    altered_adjudication = copy.deepcopy(curated_evidence)
    altered_adjudication["source_adjudications"][0][
        "resolution_record_sha256"
    ] = "0" * 64
    with pytest.raises(RuntimeError, match="source adjudication is invalid"):
        validate_curated_evidence(altered_adjudication, packets)


def test_targeted_curated_sources_preserve_publisher_and_archive_transport(
    corpus,
):
    packets, _unresolved = corpus
    mtf_ids = {
        "34114f8f8fa580a2cb413c481408094ad2a8675ebb59955cf8c7d665897d8381",
        "38805634a94b830357ca31de921357af37ce17c69a295c26dfc4c091979549ad",
        "3f2d87ee2926c027067089062d8a9ef5e28291258ca0cc7632f0796c0e927631",
        "5ccc6c754f38343430f3ebc49a7903cce40b9ce86482dc1a4a66b05e13f71202",
        "63a705d3b574f9294af663894a908053d17df31c1b177fca7997caa36299a5f5",
        "6cc1934843f9e7ab1ee3baf477359078f1b17a45b630dc779ee5561b3b9128f7",
        "abcd58e8e8ff8fa5fa8e3c37fcefb55702beb9f07029fec7c3cc38b966acc5cc",
        "b572fbd2723c05ecef8f4e3eaa3a897834f612b7965ac72a62676c09dde6fdcd",
        "d9028da9c6518f578ea0840ab4ae6ed5e3a94028cfb0d4c924a476d19df838c9",
        "dd317acd2b79a2e22aa2c73507484f512ad9646e10fc570590fc074f30d5e374",
        "dff8aab30bb4fd825ead87707ce7bf811989926bce9afdeab7b1030a29293d09",
    }
    for quote_id in mtf_ids:
        source = packets[quote_id]["_source_role_audit"]["curated_sources"][0]
        assert source["source_publisher"] == "Margaret Thatcher Foundation"
        assert source["retrieval_archive"] == "Internet Archive Wayback Machine"
        assert source["canonical_url"].startswith(
            "https://www.margaretthatcher.org/document/"
        )
        assert source["public_url"] == source["canonical_url"]
        assert source["transport_url"].startswith(
            "https://web.archive.org/web/"
        )
        assert source["fetch_policy_version"].endswith("-v5")
        assert len(source["page_sha256"]) == 64
        assert len(source["page_text_sha256"]) == 64
        assert len(source["date"]) == 10
        assert source["source_date_raw"]
        assert source["supporting_context"]
        assert len(source["supporting_context_sha256"]) == 64

    hansard = packets[
        "9efcca12a991db11a019126881080676677742613499ab9623b2048120c23997"
    ]["_source_role_audit"]["curated_sources"][0]
    assert hansard["source_publisher"] == "UK Parliament"
    assert "retrieval_archive" not in hansard
    assert hansard["public_url"] == hansard["canonical_url"]


def test_targeted_excerpt_and_variant_statuses_are_publicly_distinct(corpus):
    packets, _unresolved = corpus
    hayek = packets[
        "9efcca12a991db11a019126881080676677742613499ab9623b2048120c23997"
    ]
    lord_mayor = packets[
        "d9028da9c6518f578ea0840ab4ae6ed5e3a94028cfb0d4c924a476d19df838c9"
    ]
    callaghan = packets[
        "dd317acd2b79a2e22aa2c73507484f512ad9646e10fc570590fc074f30d5e374"
    ]

    assert hayek["verification_status"] == "excerpt"
    assert lord_mayor["verification_status"] == "excerpt"
    assert callaghan["verification_status"] == "variant"
    assert hayek["_source_role_audit"]["public_verification_wording"] == (
        "Verified excerpt"
    )
    assert lord_mayor["_source_role_audit"]["public_verification_wording"] == (
        "Verified excerpt"
    )
    assert callaghan["_source_role_audit"]["public_verification_wording"] == (
        "Historically verified variant"
    )


def test_headline_source_counts_are_mutually_exclusive_and_balanced(audit):
    summary = audit["summary"]
    provenance = summary["headline_observed_source_provenance_counts"]
    dispositions = summary["headline_observed_source_disposition_counts"]
    assert sum(value for key, value in provenance.items() if key != "total") == provenance["total"]
    assert sum(value for key, value in dispositions.items() if key != "total") == dispositions["total"]
    assert provenance["total"] == dispositions["total"] == audit["audited_source_record_count"]
    assert sum(summary["all_evidentiary_source_quality_counts"].values()) == audit[
        "all_evidentiary_source_record_count"
    ]
    assert sum(summary["all_evidentiary_source_role_counts"].values()) == summary[
        "all_evidentiary_source_role_assignment_count"
    ]
    assert summary["all_evidentiary_source_role_assignment_count"] > audit[
        "all_evidentiary_source_record_count"
    ]


@pytest.mark.parametrize(
    ("quote_id", "required", "forbidden"),
    [
        (
            "00a61fc4f76648e2ccbf07fbdadec99afb0000789e85390bae28f11cb3f230ae",
            "Verification — Exact wording verified",
            "No reliable source located",
        ),
        (
            "3cced21d7f9bc45fd5479288c7b413bad5e0e48fcf71f103251b6284c8528f12",
            "The Downing Street Years, p. 513",
            "No reliable source located",
        ),
        (
            "0827a4126cc47bd563b75be766edeffcd44f7027125f190887ffb7a70c5bb015",
            "Verification — Historically verified variant",
            "Exact wording verified",
        ),
        (HUGO_YOUNG_ID, "Source — Hugo Young", "Exact wording verified"),
        (THAMES_ID, "Source — No reliable source located", "nps.gov"),
        (
            "0195075998545ab93834a331f35b4ac0d8c76543495757aa72874ad8e9fb3448",
            "Source — Margaret Thatcher Foundation",
            "Verification — Exact wording verified",
        ),
        (
            "0056972ab9debcb840c36ac23ad0387e715cb22fc4ed49dbcf35159f83592aa5",
            "Source — No reliable source located",
            "http",
        ),
    ],
)
def test_representative_public_outputs_are_evidence_accurate_without_diagnostics(
    corpus, quote_id, required, forbidden
):
    formatted = format_context_reply_v2(
        corpus[0][quote_id], maximum_length=25_000
    )
    assert formatted is not None
    assert required in formatted["text"]
    assert forbidden not in formatted["text"]
    assert "Source (" not in formatted["text"]
    assert "Secondary recollection" not in formatted["text"]


def test_complete_audit_preserves_all_quote_identities_and_eligibility(corpus, audit):
    packets, unresolved = corpus
    eligible = {q for q, packet in packets.items() if packet_is_attributed_to_margaret_thatcher(packet)}
    assert len(packets) == 627
    assert len(unresolved) == 5
    assert len(eligible) == 611
    assert audit["packet_count"] == 627
    assert audit["attribution_eligible_quote_count"] == 611
    assert set(audit["items"]) == set(packets)
    assert all(audit["items"][q]["quote_text"] == packet["quote_text"]
               for q, packet in packets.items())


def test_audited_and_audit_free_corpora_have_identical_eligible_quotes(corpus):
    audited, audited_unresolved = corpus
    raw, raw_unresolved = load_and_validate_corpus(
        RESEARCH_DIR, load_source_role_audit=False
    )
    audited_eligible = {
        quote_id for quote_id, packet in audited.items()
        if packet_is_attributed_to_margaret_thatcher(packet)
    }
    raw_eligible = {
        quote_id for quote_id, packet in raw.items()
        if packet_is_attributed_to_margaret_thatcher(packet)
    }

    assert audited_unresolved == raw_unresolved
    assert audited_eligible == raw_eligible
    assert len(audited_eligible) == 611
    assert {
        quote_id: packet["quote_text"] for quote_id, packet in audited.items()
    } == {
        quote_id: packet["quote_text"] for quote_id, packet in raw.items()
    }


def test_thames_nps_source_is_rejected_and_never_rendered(audit):
    item = audit["items"][THAMES_ID]
    rows = item["sources"] + item["model_proposed_source_leads"]
    nps = [row for row in rows if "nps.gov" in row["source_url"]]
    assert nps
    assert all(row["assigned_roles"] == ["rejected_irrelevant"] for row in nps)
    assert all("nps.gov" not in row.get("public_url", "") for row in item["renderable_sources"])
    assert item["public_verification_wording"] == "Exact wording not verified"


def test_google_time_searches_are_rejected_and_never_rendered(audit):
    for quote_id in MANDATORY_GOOGLE_IDS:
        item = audit["items"][quote_id]
        google = [row for row in item["sources"] if "google.com/search" in row["source_url"]]
        assert google
        assert all(row["assigned_roles"] == ["rejected_irrelevant"] for row in google)
        assert all("google.com/search" not in row.get("public_url", "")
                   for row in item["renderable_sources"])


def test_discovery_only_sources_never_reach_public_source_selection(corpus):
    packets, _ = corpus
    for packet in packets.values():
        rendered = public_sources(packet)
        assert all("discovery_only" not in source["roles"] for source in rendered)
        assert all("rejected_irrelevant" not in source["roles"] for source in rendered)


def test_secondary_recollection_is_internal_only_and_cannot_claim_exact_wording(
    corpus, audit
):
    packet = corpus[0][PRIOR_ID]
    item = audit["items"][PRIOR_ID]
    assert any(row["source_quality_class"] == "secondary_recollection"
               for row in item["renderable_sources"])
    assert "no primary Thatcher transcript located" in item["public_verification_wording"]
    public = format_context_reply_public(packet)
    internal = format_context_reply_internal(packet)
    assert public is not None and internal is not None
    assert "Secondary recollection" not in public["text"]
    assert "Source (" not in public["text"]
    assert "Source — Jim Prior" in public["text"]
    assert "Secondary recollection (wording, attribution)" in internal["text"]
    assert "Exact wording verified" not in public["text"]
    assert public["verification_label"] == (
        "Attributed, but exact wording not independently verified"
    )
    assert "Confidence —" not in public["text"]


def test_no_reliable_source_uses_explicit_safe_wording(corpus):
    packet = corpus[0][THAMES_ID]
    formatted = format_context_reply_public(packet)
    assert formatted is not None
    assert formatted["text"].endswith("Source — No reliable source located")
    assert "nps.gov" not in formatted["text"]
    assert "Confidence —" not in formatted["text"]


def test_confidence_dimensions_remain_separate(corpus):
    public = format_context_reply_public(corpus[0][THAMES_ID])
    internal = format_context_reply_internal(corpus[0][THAMES_ID])
    assert public is not None and internal is not None
    dimensions = public["confidence_dimensions"]
    assert set(dimensions) == {
        "attribution", "wording", "source_event", "date",
        "historical_context", "interpretation",
    }
    assert internal["confidence_dimensions"] == dimensions
    assert "Confidence —" not in public["text"]
    assert "Confidence — Attribution:" in internal["text"]
    for label in (
        "wording:", "source event:", "date:",
        "historical context:", "interpretation:",
    ):
        assert label in internal["text"]
    assert public["rendering_mode"] == "public"
    assert internal["rendering_mode"] == "internal"


def test_every_eligible_public_rendering_omits_detailed_confidence(corpus):
    packets, _ = corpus
    eligible = [
        packet for packet in packets.values()
        if packet_is_attributed_to_margaret_thatcher(packet)
    ]
    assert len(eligible) == 611
    allowed_labels = {
        "Exact wording verified",
        "Historically verified variant",
        "Verified excerpt",
        "Attributed, but exact wording not independently verified",
        "Research incomplete",
    }
    for packet in eligible:
        formatted = format_context_reply_public(packet)
        assert formatted is not None
        assert "Confidence —" not in formatted["text"]
        assert "Source (" not in formatted["text"]
        assert "Secondary recollection" not in formatted["text"]
        assert formatted["verification_label"] in allowed_labels
        if not formatted["sources"]:
            assert formatted["text"].endswith("Source — No reliable source located")
        assert set(formatted["confidence_dimensions"]) == {
            "attribution", "wording", "source_event", "date",
            "historical_context", "interpretation",
        }


def test_rolling_back_socialism_regression_uses_public_sections_without_confidence(corpus):
    packet = corpus[0][ROLLBACK_SOCIALISM_ID]
    assert packet["quote_text"] == (
        "We were the first country to attempt and to succeed in rolling back the "
        "frontiers of socialism, which is the first cousin to communism."
    )

    formatted = format_context_reply_public(packet)

    assert formatted is not None
    assert formatted["text"].startswith("Meaning —")
    assert (
        "Context — The surviving attribution does not establish an occasion, "
        "date or immediate historical issue."
    ) not in formatted["text"]
    assert "\n\nVerification — Research incomplete" in formatted["text"]
    assert "\n\nSource — No reliable source located" in formatted["text"]
    assert "Confidence —" not in formatted["text"]
    assert set(formatted["confidence_dimensions"]) == {
        "attribution", "wording", "source_event", "date",
        "historical_context", "interpretation",
    }


def test_strong_mtf_locator_still_renders_exact_wording(corpus):
    quote_id = "00a61fc4f76648e2ccbf07fbdadec99afb0000789e85390bae28f11cb3f230ae"
    formatted = format_context_reply_public(corpus[0][quote_id])
    assert formatted is not None
    assert "Verification — Exact wording verified" in formatted["text"]
    assert "Margaret Thatcher Foundation" in formatted["text"]
    assert "Confidence —" not in formatted["text"]


def test_thatcher_authored_book_with_page_locator_still_renders(corpus):
    quote_id = "3cced21d7f9bc45fd5479288c7b413bad5e0e48fcf71f103251b6284c8528f12"
    formatted = format_context_reply_public(corpus[0][quote_id])

    assert formatted is not None
    assert "Verification — Exact wording verified" in formatted["text"]
    assert "The Downing Street Years, p. 513" in formatted["text"]
    assert "Confidence —" not in formatted["text"]


def test_historically_verified_variant_still_renders_as_variant(corpus):
    quote_id = "0827a4126cc47bd563b75be766edeffcd44f7027125f190887ffb7a70c5bb015"
    formatted = format_context_reply_public(corpus[0][quote_id])

    assert formatted is not None
    assert "Verification — Historically verified variant" in formatted["text"]
    assert "margaretthatcher.org/document/103522" in formatted["text"]
    assert "Confidence —" not in formatted["text"]


def test_every_public_source_supports_at_least_one_displayed_claim(corpus):
    packets, _ = corpus
    for packet in packets.values():
        for source in public_sources(packet):
            assert source["claims_supported"]
            assert set(source["claims_supported"]) <= {
                "wording", "attribution", "source_event", "date",
                "historical_context",
            }


def _context_admission_packet(
    claims_supported: list[str],
    *,
    source_quality_class: str = "strong_primary_evidence",
) -> dict:
    quote_text = "A synthetic quotation used only for source-role admission tests."
    source_roles = []
    if "wording" in claims_supported:
        source_roles.append("wording_verification")
    if "attribution" in claims_supported:
        source_roles.append("attribution_support")
    if set(claims_supported) & {"source_event", "date"}:
        source_roles.append("source_event_support")
    if "historical_context" in claims_supported:
        source_roles.append("historical_context_support")
    packet = {
        "quote_id": "a" * 64,
        "quote_text": quote_text,
        "verified_text": quote_text,
        "verification_status": "exact",
        "speaker": "Margaret Thatcher",
        "source_event": "Synthetic speech",
        "date": "1981-05-20",
        "historical_context": "Synthetic historical context used to test claim admission.",
        "stable_locator": "Unknown",
        "research_confidence": "high",
        "sources": [],
    }
    curated_source = {
        "source_id": "b" * 64,
        "title": "Synthetic primary transcript",
        "url": "https://example.test/archive/document/1",
        "source_type": "primary_transcript",
        "source_event": packet["source_event"],
        "source_date": packet["date"],
        "assigned_roles": source_roles,
        "source_quality_class": source_quality_class,
        "claims_supported": claims_supported,
        "wording_match_kind": "exact",
        "exact_supporting_passage": quote_text,
        "exact_supporting_passage_sha256": hashlib.sha256(
            quote_text.encode()
        ).hexdigest(),
        "stable_locator": "Synthetic document 1",
        "rationale": "Synthetic claim-scoped evidence.",
        "recorded_at": "2026-07-22T00:00:00Z",
        "page_independently_inspected": True,
    }
    return audit_packet(
        packet,
        attribution_eligible=True,
        curated_evidence_item={"sources": [curated_source]},
    )


@pytest.mark.parametrize(
    ("claims_supported", "expected_fields"),
    [
        ([], []),
        (["date"], ["date"]),
        (["source_event"], ["source_event"]),
        (
            ["source_event", "date"],
            ["source_event", "date"],
        ),
        (["historical_context"], ["historical_context"]),
    ],
)
def test_public_context_admission_is_claim_specific(
    claims_supported, expected_fields
):
    item = _context_admission_packet(claims_supported)

    assert item["public_context_supported_fields"] == expected_fields


def test_public_context_admission_requires_non_unknown_claim_confidence():
    item = _context_admission_packet(
        ["source_event"],
        source_quality_class="insufficiently_located_evidence",
    )

    assert item["confidence_after"]["source_event"] == "unknown"
    assert item["public_context_supported_fields"] == []


def test_curated_exact_primary_wording_has_full_coverage():
    item = _context_admission_packet(["wording", "attribution"])

    assert item["curated_sources"][0]["claim_coverage"]["wording"] == "full"
    assert item["confidence_after"]["wording"] == "high"
    assert item["public_verification_wording"] == "Exact wording verified"
    assert item["public_context_supported_fields"] == []


def test_b32_public_context_admits_event_but_not_date(audit):
    quote_id = "b32d8cdf5977dee436857e8060d3a83ebfe54de9f6dabb20ffc65a0796338b5c"
    item = audit["items"][quote_id]

    assert item["confidence_after"]["source_event"] == "high"
    assert item["confidence_after"]["date"] == "high"
    assert item["confidence_after"]["historical_context"] == "high"
    assert item["public_context_supported_fields"] == [
        "source_event", "date", "historical_context",
    ]


def test_secondary_wording_requires_explicit_thatcher_attribution(audit):
    cambridge_id = (
        "2b7d9239193767ff1d0e7d16ea6589f1c5084bb8de36375806e6a00afc71b9b4"
    )
    cambridge_sources = audit["items"][cambridge_id]["renderable_sources"]
    assert any(
        source["source_quality_class"] == "reliable_secondary_evidence"
        and {"wording", "attribution"} <= set(source["claims_supported"])
        for source in cambridge_sources
    )

    misattributed_id = (
        "9fd092ecc3b3693cecc5058d3c41366a83c3bc4459e8efd8762d37b1ad67ce21"
    )
    time_sources = [
        source for source in audit["items"][misattributed_id]["renderable_sources"]
        if "time.com" in str(source.get("public_url") or "")
    ]
    assert time_sources == []


def test_saved_recovery_and_resolution_manifests_are_identity_valid(corpus):
    packets, _ = corpus
    recovery = json.loads((RESEARCH_DIR / "historical_context_source_recovery.json").read_text())
    resolution = json.loads((RESEARCH_DIR / "historical_context_source_resolution.json").read_text())
    validate_recovery(recovery, packets)
    validate_resolution(resolution, packets)
    assert recovery["citation_quote_count"] == 185
    assert resolution["complete"] is True
    assert resolution["completed_url_count"] == 864


def test_gemini_queue_and_cost_preflight_cover_only_true_no_source_residual(corpus, audit):
    packets, _ = corpus
    queue = residual_queue(audit)
    preflight = build_preflight(packets, audit, queue)
    assert len(queue) == audit["summary"]["packets_with_no_reliable_source"] == 91
    assert len(set(queue)) == len(queue)
    assert queue[0] == THAMES_ID
    assert "313172d18e2d915e514e4a202a8b1bcbb077472c2504dee63fe98edaf60e0b3a" not in queue
    assert MANDATORY_GOOGLE_IDS & set(queue) == {
        "7c29b290a8e1898c86c39b0cdd23c60161ca73cc068d4a888a19f1f7f3967d0d",
    }
    assert preflight["maximum_single_call_exposure_usd"] < HARD_LIMIT_USD
    assert preflight["conservative_one_attempt_exposure_usd"] > HARD_LIMIT_USD
    assert preflight["full_queue_guaranteed_within_hard_limit"] is False
    assert preflight["hard_limit_usd"] == HARD_LIMIT_USD


def test_openai_forced_search_preflight_preserves_combined_hard_stop(corpus, audit):
    packets, _ = corpus
    queue = residual_queue(audit)[:5]
    prior = 1.0564138
    preflight = build_openai_preflight(
        packets, queue, prior_known_spend_usd=prior,
        prior_ambiguous_exposure_usd=0.5,
    )

    assert preflight["tool_choice"] == "forced_web_search_preview"
    assert preflight["maximum_tool_calls_per_request"] == 1
    assert preflight["maximum_combined_exposure_usd"] < HARD_LIMIT_USD
    assert preflight["prior_ambiguous_exposure_usd"] == 0.5
    assert preflight["within_hard_limit"] is True
    for quote_id in queue:
        prompt = openai_research_prompt(packets[quote_id])
        assert openai_actual_call_cost(500, OPENAI_MAX_OUTPUT_TOKENS, 1) <= (
            openai_maximum_call_cost(prompt)
        )


def test_openai_resumable_queue_retains_completed_source_bearing_work(audit):
    original = residual_queue(audit)
    completed = original[0]
    reduced = json.loads(json.dumps(audit))
    reduced["summary"]["no_reliable_source_quote_ids"] = original[1:]

    resumed = resumable_research_queue(reduced, {completed})

    assert set(resumed) == set(original)
    assert resumed[0] == completed


def test_openai_native_search_sources_are_deduplicated_and_search_is_counted():
    sources, search_calls = extract_cited_sources({
        "output": [
            {
                "type": "web_search_call",
                "action": {
                    "sources": [
                        {"type": "url", "url": "https://example.test/a", "title": "A"},
                    ]
                },
            },
            {
                "type": "message",
                "content": [{
                    "type": "output_text",
                    "annotations": [{
                        "type": "url_citation",
                        "url": "https://example.test/a",
                        "title": "A duplicate",
                    }, {
                        "type": "url_citation",
                        "url": "https://example.test/b",
                        "title": "B",
                    }],
                }],
            },
        ]
    })

    assert search_calls == 1
    assert [source["url"] for source in sources] == [
        "https://example.test/a", "https://example.test/b",
    ]


def test_openai_pilot_limit_counts_every_request_not_only_successes(
    tmp_path, corpus, audit
):
    packets, _ = corpus
    queue = residual_queue(audit)[:4]

    class Client:
        calls = 0

        def call(self, _prompt):
            self.calls += 1
            return {
                "raw": {"id": f"response-{self.calls}", "output": []},
                "sources": [],
                "search_calls": 1,
                "input_tokens": 200,
                "output_tokens": 50,
                "known_cost_usd": openai_actual_call_cost(200, 50, 1),
                "request_id": f"response-{self.calls}",
                "latency_seconds": 0.01,
            }

    client = Client()
    outcome = PilotRunner(
        tmp_path, packets, queue, client, prior_known_spend_usd=1.0564138
    ).run(maximum_new_calls=2)

    assert client.calls == 2
    assert len(outcome["results"]["items"]) == 2
    assert len(outcome["ledger"]["attempts"]) == 2


def test_openai_known_spend_retains_completed_unusable_responses(
    tmp_path, corpus, audit
):
    packets, _ = corpus
    queue = residual_queue(audit)[:2]
    costs = [openai_actual_call_cost(200, 50, 0), openai_actual_call_cost(200, 50, 1)]

    class Client:
        calls = 0

        def call(self, _prompt):
            index = self.calls
            self.calls += 1
            return {
                "raw": {"id": f"response-{self.calls}", "output": []},
                "sources": [],
                "search_calls": index,
                "input_tokens": 200,
                "output_tokens": 50,
                "known_cost_usd": costs[index],
                "request_id": f"response-{self.calls}",
                "latency_seconds": 0.01,
            }

    outcome = PilotRunner(
        tmp_path, packets, queue, Client(), prior_known_spend_usd=1.0
    ).run(maximum_new_calls=2)

    assert [row["status"] for row in outcome["ledger"]["attempts"]] == [
        "completed_unusable", "completed",
    ]
    assert outcome["ledger"]["known_spend_usd"] == pytest.approx(
        1.0 + sum(costs)
    )


def test_openai_resume_refuses_unresolved_sending_attempt(tmp_path, corpus, audit):
    packets, _ = corpus
    quote_id = residual_queue(audit)[0]
    (tmp_path / "api_cost_ledger.json").write_text(json.dumps({
        "schema_version": 1,
        "hard_limit_usd": HARD_LIMIT_USD,
        "prior_known_spend_usd": 1.0,
        "prior_ambiguous_exposure_usd": 0.0,
        "known_spend_usd": 1.0,
        "ambiguous_exposure_usd": 0.0,
        "attempts": [{"quote_id": quote_id, "status": "sending"}],
    }), encoding="utf-8")
    (tmp_path / "research_results.json").write_text(json.dumps({
        "schema_version": 1, "items": {}, "failures": {},
    }), encoding="utf-8")

    with pytest.raises(RuntimeError, match="potentially billed request"):
        PilotRunner(
            tmp_path, packets, [quote_id], object(), prior_known_spend_usd=1.0
        ).run(maximum_new_calls=1)


def test_openai_http_5xx_is_ambiguous_exposure_and_stops(
    tmp_path, corpus, audit
):
    packets, _ = corpus
    queue = residual_queue(audit)[:2]

    class Client:
        calls = 0

        def call(self, _prompt):
            self.calls += 1
            response = httpx.Response(
                500, request=httpx.Request("POST", "https://api.openai.com/v1/responses")
            )
            raise APIStatusError("server error", response=response, body=None)

    client = Client()
    outcome = PilotRunner(
        tmp_path, packets, queue, client, prior_known_spend_usd=1.0
    ).run(maximum_new_calls=2)

    assert client.calls == 1
    assert len(outcome["results"]["failures"]) == 1
    assert outcome["ledger"]["attempts"][0]["status"] == "ambiguous"
    assert outcome["ledger"]["ambiguous_exposure_usd"] == pytest.approx(
        openai_maximum_call_cost(openai_research_prompt(packets[queue[0]]))
    )


def test_saved_openai_research_manifest_is_complete_and_source_grounded(corpus):
    packets, _ = corpus
    path = RESEARCH_DIR / OPENAI_RESEARCH_FILENAME
    if not path.exists():
        pytest.skip("bounded OpenAI research has not completed yet")
    manifest = json.loads(path.read_text(encoding="utf-8"))

    validate_openai_research_manifest(manifest, packets)
    assert set(manifest["items"]) | set(manifest["failures"]) == set(
        manifest["queue_quote_ids"]
    )
    assert all(
        source["research_provider"] == "openai"
        for item in manifest["items"].values()
        for source in item["validated_sources"]
    )


def test_model_result_cannot_change_quote_identity(corpus):
    packet = corpus[0][THAMES_ID]
    value = {
        "quote_id": THAMES_ID,
        "quote_text": packet["quote_text"],
        "outcome": "no_reliable_source_located",
        "sources": [],
        "research_note": "No defensible source found.",
    }
    assert validate_model_result(value, packet)["outcome"] == "no_reliable_source_located"
    value["quote_text"] += " changed"
    with pytest.raises(ValueError, match="identity"):
        validate_model_result(value, packet)


def test_search_only_result_cannot_claim_completion_without_identity(corpus):
    packet = corpus[0][THAMES_ID]
    value = {
        "quote_id": THAMES_ID,
        "quote_text": packet["quote_text"],
        "search_completed": True,
        "current_top_result_title": "Current result",
        "search_note": "Exact wording searched.",
    }
    assert validate_search_result(value, packet)["search_completed"] is True
    value["search_completed"] = False
    with pytest.raises(ValueError, match="completion"):
        validate_search_result(value, packet)


class _FakeResponse:
    ok = True
    status_code = 200
    url = "https://www.margaretthatcher.org/document/123456"
    history = []
    headers = {"content-type": "text/html; charset=utf-8"}

    def __init__(self, html: str):
        self.content = html.encode()

    def iter_content(self, _size):
        yield self.content


def test_researched_source_requires_passage_to_exist_on_fetched_page(corpus):
    packet = corpus[0][THAMES_ID]
    quote = packet["quote_text"]
    source = {
        "title": "Archive document",
        "url": "https://www.margaretthatcher.org/document/123456",
        "publisher": "Margaret Thatcher Foundation",
        "stable_locator": "Document 123456",
        "assigned_roles": ["wording_verification", "attribution_support"],
        "source_quality_class": "strong_primary_evidence",
        "exact_supporting_passage": quote,
        "claims_supported": ["wording", "attribution"],
        "support_explanation": "The passage contains the wording.",
    }
    verified, reason = verify_source(
        source, packet,
        request=lambda *args, **kwargs: _FakeResponse(f"<html><title>Archive</title><body>{quote}</body></html>"),
    )
    assert reason is None
    assert verified is not None
    assert verified["exact_supporting_passage"] == quote
    source["exact_supporting_passage"] = "A passage that is not on the page."
    verified, reason = verify_source(
        source, packet,
        request=lambda *args, **kwargs: _FakeResponse(f"<html><body>{quote}</body></html>"),
    )
    assert verified is None
    assert reason == "supporting_passage_not_found"


def test_researched_source_accepts_token_identical_wording_without_final_punctuation(
    corpus,
):
    packet = corpus[0][
        "1244294a90af385fe940a432fab296f43725644d318cb011c7080482115a0236"
    ]
    passage = packet["quote_text"].rstrip(".")
    source = {
        "title": "Hansard",
        "url": (
            "https://hansard.parliament.uk/commons/2011-10-21/debates/"
            "11102146000003/EqualityAndDiversity%28Reform%29Bill"
        ),
        "publisher": "UK Parliament",
        "stable_locator": "Hansard, 21 October 2011",
        "assigned_roles": ["wording_verification", "attribution_support"],
        "source_quality_class": "reliable_secondary_evidence",
        "exact_supporting_passage": passage,
        "claims_supported": ["wording", "attribution"],
        "support_explanation": "The later debate attributes the wording.",
    }

    class HansardResponse(_FakeResponse):
        url = source["url"]

    verified, reason = verify_source(
        source,
        packet,
        request=lambda *args, **kwargs: HansardResponse(
            f"<html><title>Hansard 2011</title><body>{passage}</body></html>"
        ),
    )

    assert reason is None
    assert verified is not None


def test_grounded_result_resolves_to_direct_page_and_requires_attribution(corpus):
    packet = corpus[0][
        "313172d18e2d915e514e4a202a8b1bcbb077472c2504dee63fe98edaf60e0b3a"
    ]

    class GroundedResponse(_FakeResponse):
        url = "https://www.heraldscotland.com/news/12345/article/"

    quote = packet["quote_text"]
    source = {
        "title": "heraldscotland.com",
        "url": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/token",
    }
    verified, reason = verify_grounding_source(
        source, packet,
        request=lambda *args, **kwargs: GroundedResponse(
            f"<html><title>Thatcher letter report</title><body>"
            f"Lady Thatcher wrote: {quote}</body></html>"
        ),
    )
    assert reason is None
    assert verified is not None
    assert verified["url"] == GroundedResponse.url
    assert verified["source_quality_class"] == "reliable_secondary_evidence"

    verified, reason = verify_grounding_source(
        source, packet,
        request=lambda *args, **kwargs: GroundedResponse(
            f"<html><title>Anonymous saying</title><body>{quote}</body></html>"
        ),
    )
    assert verified is None
    assert reason == "attribution_not_present_near_wording"


def test_grounded_result_removes_tracking_parameters_before_source_identity(corpus):
    packet = corpus[0][THAMES_ID]

    class TrackingResponse(_FakeResponse):
        url = "https://www.margaretthatcher.org/document/123456?utm_source=openai"

    verified, reason = verify_grounding_source(
        {
            "title": "Transcript",
            "url": (
                "https://vertexaisearch.cloud.google.com/"
                "grounding-api-redirect/token"
            ),
        },
        packet,
        request=lambda *args, **kwargs: TrackingResponse(
            f"<html><title>Margaret Thatcher transcript</title><body>"
            f"Margaret Thatcher said: {packet['quote_text']}</body></html>"
        ),
    )

    assert reason is None
    assert verified["url"] == "https://www.margaretthatcher.org/document/123456"


def test_later_hansard_quotation_is_secondary_not_primary(corpus):
    packet = copy.deepcopy(corpus[0][
        "a97e6dd2f444ecfbba67977a34be91db40d17eb09c8566fe714e48bffddb11f7"
    ])
    packet["verification_status"] = "exact"

    class LaterHansardResponse(_FakeResponse):
        url = (
            "https://publications.parliament.uk/pa/cm201415/cmhansrd/"
            "cm141015/debtext/141015-0004.htm"
        )

    verified, reason = verify_grounding_source(
        {
            "title": "Hansard",
            "url": (
                "https://vertexaisearch.cloud.google.com/"
                "grounding-api-redirect/token"
            ),
        },
        packet,
        request=lambda *args, **kwargs: LaterHansardResponse(
            f"<html><title>House of Commons Hansard Debates for 15 Oct 2014</title>"
            f"<body>Margaret Thatcher wrote: {packet['quote_text']}</body></html>"
        ),
    )

    assert reason is None
    assert verified is not None
    assert verified["source_quality_class"] == "reliable_secondary_evidence"


def test_generic_university_quote_page_is_not_automatically_reliable(corpus):
    packet = corpus[0][
        "56c77eedf86a10a6748dc8e20d096e415708541735c07f5c88a338f49d29cc9c"
    ]

    class PersonalUniversityPage(_FakeResponse):
        url = "https://faculty.example.edu/personal/margaret-thatcher-quotes/"

    verified, reason = verify_grounding_source(
        {"title": "Personal quotation page", "url": PersonalUniversityPage.url},
        packet,
        request=lambda *args, **kwargs: PersonalUniversityPage(
            f"<html><title>Margaret Thatcher quotations</title>"
            f"<body>{packet['quote_text']}</body></html>"
        ),
    )

    assert verified is None
    assert reason == "unrecognised_source_authority"


def test_guarded_approximate_wording_accepts_semantically_equivalent_variant():
    candidate = (
        "The larger the slice taken by government, the smaller the cake "
        "available for everyone."
    )
    passage = (
        "The greater the slice taken by government, the smaller the cake "
        "available to everyone."
    )

    similarity = _semantic_similarity(candidate, passage)

    assert similarity is not None
    assert similarity >= 0.80


@pytest.mark.parametrize(
    ("candidate", "passage"),
    [
        (
            "The government must never abandon the freedom of ordinary people today.",
            "The government must always abandon the freedom of ordinary people today.",
        ),
        (
            "Margaret Thatcher told Parliament that economic freedom protects families.",
            "Ronald Reagan told Parliament that economic freedom protects families.",
        ),
        (
            "Inflation rose by 10 per cent before the government changed course.",
            "Inflation rose by 15 per cent before the government changed course.",
        ),
        (
            "People moved from East Berlin towards West Berlin when the wall opened.",
            "People moved from West Berlin towards East Berlin when the wall opened.",
        ),
        (
            "An enemy may eventually be turned into a trusted friend through negotiation.",
            "A friend may eventually be turned into a trusted enemy through negotiation.",
        ),
    ],
)
def test_guarded_approximate_wording_rejects_material_semantic_changes(
    candidate, passage
):
    assert _semantic_similarity(candidate, passage) is None


def test_approximate_source_cannot_verify_exact_packet_wording(corpus):
    packet = copy.deepcopy(corpus[0][
        "a97e6dd2f444ecfbba67977a34be91db40d17eb09c8566fe714e48bffddb11f7"
    ])
    packet["verification_status"] = "exact"
    variant = packet["verified_text"].replace("larger", "greater")

    verified, reason = verify_grounding_source(
        {
            "title": "Margaret Thatcher speech transcript",
            "url": "https://www.margaretthatcher.org/document/123456",
        },
        packet,
        request=lambda *args, **kwargs: _FakeResponse(
            f"<html><title>Margaret Thatcher speech transcript</title>"
            f"<body>{variant}</body></html>"
        ),
    )

    assert reason is None
    assert verified is not None
    assert verified["wording_match_kind"] == "approximate_semantic_guarded"
    assert verified["wording_similarity"] >= 0.80
    assert verified["assigned_roles"] == ["attribution_support"]
    assert verified["claims_supported"] == ["attribution"]
    audited = audit_researched_source(packet, verified)
    assert _public_verification(packet, [audited]) == (
        "Exact wording not independently verified by the retained evidence"
    )


def test_approximate_source_can_support_labelled_uncertain_wording(corpus):
    packet = corpus[0][THAMES_ID]
    variant = packet["quote_text"].replace("critics", "opponents").replace(
        "would say", "would claim"
    )

    verified, reason = verify_grounding_source(
        {
            "title": "Margaret Thatcher interview recollection",
            "url": "https://www.margaretthatcher.org/document/123456",
        },
        packet,
        request=lambda *args, **kwargs: _FakeResponse(
            f"<html><title>Margaret Thatcher interview recollection</title>"
            f"<body>{variant}</body></html>"
        ),
    )

    assert reason is None
    assert verified is not None
    assert verified["wording_match_kind"] == "approximate_semantic_guarded"
    assert verified["wording_similarity"] >= 0.80
    assert "wording_verification" in verified["assigned_roles"]
    audited = audit_researched_source(packet, verified)
    assert audited["claim_coverage"]["wording"] == "partial"
    assert _public_verification(packet, [audited]) == "Exact wording not verified"


def test_research_manifest_validation_preserves_identity(corpus):
    packets, _ = corpus
    manifest_path = RESEARCH_DIR / "historical_context_source_research.json"
    if not manifest_path.exists():
        pytest.skip("bounded Gemini research has not run yet")
    manifest = json.loads(manifest_path.read_text())
    validate_research_manifest(manifest, packets)


class _FakeResearchClient:
    def __init__(self, transport, outcomes):
        self.transport = transport
        self.outcomes = list(outcomes)
        self.calls = 0

    def call(self, _prompt):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _no_source_response(packet, transport="developer_api"):
    return {
        "raw": {"response": "saved"},
        "parsed": {
            "quote_id": packet["quote_id"],
            "quote_text": packet["quote_text"],
            "search_completed": True,
            "current_top_result_title": "Current result",
            "search_note": "Exact wording searched.",
        },
        "parse_repairs": [],
        "parse_error": None,
        "grounding": {"queries": ["query"]},
        "usage": {"input_tokens": 10, "output_tokens": 20, "thinking_tokens": 5},
        "cost_usd": 0.01,
        "latency_seconds": 0.1,
        "request_id": "request-1",
        "transport": transport,
    }


def _runner_fixture(tmp_path, corpus, audit, developer, vertex=None):
    packet = corpus[0][THAMES_ID]
    return ResearchRunner(
        tmp_path / "run", tmp_path / "research", {THAMES_ID: packet},
        {"items": {THAMES_ID: audit["items"][THAMES_ID]}}, [THAMES_ID],
        developer, vertex, hard_limit_usd=HARD_LIMIT_USD, sleep=lambda _seconds: None,
    )


def test_research_runner_persists_cost_before_accepting_result(tmp_path, corpus, audit):
    packet = corpus[0][THAMES_ID]
    developer = _FakeResearchClient("developer_api", [_no_source_response(packet)])
    runner = _runner_fixture(tmp_path, corpus, audit, developer)
    manifest = runner.run()
    ledger = json.loads((tmp_path / "run" / "api_cost_ledger.json").read_text())
    assert developer.calls == 1
    assert ledger["known_spend_usd"] == pytest.approx(0.01)
    assert len(ledger["calls"]) == 1
    assert manifest["items"][THAMES_ID]["final_outcome"] == "no_reliable_source_located"


def test_research_runner_carries_prior_family_spend_into_hard_stop(
    tmp_path, corpus, audit
):
    packet = corpus[0][THAMES_ID]
    developer = _FakeResearchClient("developer_api", [_no_source_response(packet)])
    runner = ResearchRunner(
        tmp_path / "run", tmp_path / "research", {THAMES_ID: packet},
        {"items": {THAMES_ID: audit["items"][THAMES_ID]}}, [THAMES_ID],
        developer, None, hard_limit_usd=HARD_LIMIT_USD,
        prior_known_spend_usd=HARD_LIMIT_USD - 0.1,
        sleep=lambda _seconds: None,
    )
    with pytest.raises(RuntimeError, match="hard cost limit"):
        runner.run()
    assert developer.calls == 0


def test_explicit_interactions_404_is_not_ambiguous_exposure(
    tmp_path, corpus, audit
):
    class NotFoundError(Exception):
        status_code = 404

    developer = _FakeResearchClient("developer_api", [NotFoundError("missing")])
    runner = _runner_fixture(tmp_path, corpus, audit, developer)
    manifest = runner.run()
    ledger = json.loads((tmp_path / "run" / "api_cost_ledger.json").read_text())
    assert manifest["failure_count"] == 1
    assert ledger["known_spend_usd"] == 0
    assert ledger["ambiguous_exposure_usd"] == 0


def test_two_developer_429s_fail_over_once_to_vertex(tmp_path, corpus, audit):
    packet = corpus[0][THAMES_ID]
    response = requests.Response()
    response.status_code = 429
    response._content = b'{"error":{"code":429,"status":"RESOURCE_EXHAUSTED"}}'
    failure = requests.HTTPError("429", response=response)
    developer = _FakeResearchClient("developer_api", [failure, failure])
    vertex = _FakeResearchClient("vertex_ai", [_no_source_response(packet, "vertex_ai")])
    runner = _runner_fixture(tmp_path, corpus, audit, developer, vertex)
    manifest = runner.run()
    route = json.loads((tmp_path / "run" / "provider_route_state.json").read_text())
    assert developer.calls == 2
    assert vertex.calls == 1
    assert route["developer_unavailable"] is True
    assert manifest["items"][THAMES_ID]["transport"] == "vertex_ai"


def test_successful_developer_call_resets_429_sequence(tmp_path, corpus, audit):
    packet = corpus[0][THAMES_ID]
    developer = _FakeResearchClient("developer_api", [_no_source_response(packet)])
    runner = _runner_fixture(tmp_path, corpus, audit, developer)
    runner.run_dir.mkdir(parents=True)
    (runner.run_dir / "provider_route_state.json").write_text(json.dumps({
        "schema_version": 1,
        "developer_unavailable": False,
        "consecutive_developer_429s": 1,
    }))
    runner.run()
    route = json.loads((runner.run_dir / "provider_route_state.json").read_text())
    assert route["consecutive_developer_429s"] == 0


def test_ungrounded_developer_response_uses_vertex_capability_fallback(
    tmp_path, corpus, audit
):
    packet = corpus[0][THAMES_ID]
    ungrounded = _no_source_response(packet)
    ungrounded["grounding"] = {"queries": []}
    developer = _FakeResearchClient("developer_api", [ungrounded])
    vertex = _FakeResearchClient("vertex_ai", [_no_source_response(packet, "vertex_ai")])
    runner = _runner_fixture(tmp_path, corpus, audit, developer, vertex)
    manifest = runner.run()
    assert developer.calls == 1
    assert vertex.calls == 1
    assert manifest["developer_grounding_failure_count"] == 1
    assert manifest["items"][THAMES_ID]["transport"] == "vertex_ai"


def test_developer_only_retries_one_ungrounded_response(tmp_path, corpus, audit):
    packet = corpus[0][THAMES_ID]
    ungrounded = _no_source_response(packet)
    ungrounded["grounding"] = {"queries": []}
    grounded = _no_source_response(packet)
    developer = _FakeResearchClient("developer_api", [ungrounded, grounded])
    runner = _runner_fixture(tmp_path, corpus, audit, developer)
    manifest = runner.run()
    assert developer.calls == 2
    assert manifest["developer_grounding_failure_count"] == 0
    assert manifest["items"][THAMES_ID]["transport"] == "developer_api"


def test_developer_only_two_ungrounded_responses_fail_one_item_closed(
    tmp_path, corpus, audit
):
    packet = corpus[0][THAMES_ID]
    first = _no_source_response(packet)
    second = _no_source_response(packet)
    first["grounding"] = {"queries": [], "sources": []}
    second["grounding"] = {"queries": [], "sources": []}
    developer = _FakeResearchClient("developer_api", [first, second])
    runner = _runner_fixture(tmp_path, corpus, audit, developer)
    manifest = runner.run()
    assert developer.calls == 2
    assert manifest["completed_quote_count"] == 0
    assert manifest["failures"][THAMES_ID]["outcome"] == "ungrounded"
    assert manifest["developer_grounding_unavailable"] is False


def test_reviewed_local_book_admission_is_exactly_scoped(corpus, audit):
    corrections = json.loads((
        RESEARCH_DIR / "historical_context_packet_corrections.json"
    ).read_text(encoding="utf-8"))["items"]
    meanings = {
        "f0d85c7301e8b27bc694ac030d7c5f6b1d15ff3bcdf31cbdfb03a1c05bbe83ea":
            "Thatcher argued that Britain's long post-war experiment with "
            "democratic socialism had failed in practice.",
        "cac5746ca684f9611a25dcfb6b024ed63bfb3d41b2fa4c5c3d6e44290378d4ea":
            "Thatcher described Nazism and communism as closely related "
            "forms of socialism.",
        "4f5e783f4957dc615742df2b827214e539a5123af1b4863822ba2e52684a0d80":
            "Thatcher argued that, other things being equal, excessive "
            "trade-union power and wage demands unsupported by output "
            "contributed to unemployment and made British goods uncompetitive.",
        "e259f9a77a234e4d03f415740045fb374b7c68eba06f857d7c79a73500dafe37":
            "Thatcher contrasted treating people as numbers with recognising "
            "them as distinct individuals: unequal, but equally important.",
    }
    for quote_id, meaning in meanings.items():
        assert corrections[quote_id]["corrected_value"] == meaning
        assert corpus[0][quote_id]["intended_argument"] == meaning

    exact_ids = {
        "f0d85c7301e8b27bc694ac030d7c5f6b1d15ff3bcdf31cbdfb03a1c05bbe83ea",
        "cac5746ca684f9611a25dcfb6b024ed63bfb3d41b2fa4c5c3d6e44290378d4ea",
    }
    variant_ids = {
        "4f5e783f4957dc615742df2b827214e539a5123af1b4863822ba2e52684a0d80",
        "e259f9a77a234e4d03f415740045fb374b7c68eba06f857d7c79a73500dafe37",
    }
    for quote_id in exact_ids:
        item = audit["items"][quote_id]
        assert item["current_wording_status"] == "exact"
        assert item["public_verification_wording"] == "Exact wording verified"
        assert item["curated_sources"][0]["claim_coverage"]["wording"] == "full"
    for quote_id in variant_ids:
        item = audit["items"][quote_id]
        assert item["current_wording_status"] == "variant"
        assert item["public_verification_wording"] == (
            "Historically verified variant"
        )
        assert item["curated_sources"][0]["claim_coverage"]["wording"] == (
            "normalised"
        )

    trade_union_passage = audit["items"][next(
        quote_id for quote_id in variant_ids if quote_id.startswith("4f5e")
    )]["curated_sources"][0]["supporting_passages"][0]["text"]
    assert "other things being equal" in trade_union_passage
    assert "British goods uncompetitive" in trade_union_passage
    people_passage = next(
        source
        for source in audit["items"][next(
            quote_id for quote_id in variant_ids if quote_id.startswith("e259")
        )]["curated_sources"]
        if source["source_id"]
        == "7f9ca47a606967d657418caa780c4c77dcec706af339cf4fb90b791690d08efa"
    )["supporting_passages"][0]["text"]
    assert "numbers in a state computer" in people_passage
    assert "We are all unequal" in people_passage
    assert "is quite like anyone else" in people_passage

    quislings = audit["items"][
        "52f9b9f99f66ff3bc786183803f3a8d68277604471cd411027441989337c9351"
    ]
    source = quislings["curated_sources"][0]
    assert source["source_quality_class"] == "reliable_secondary_evidence"
    assert source["assigned_roles"] == [
        "wording_verification", "attribution_support",
    ]
    assert "source_event" in source["claims_not_supported"]
    assert quislings["confidence_after"]["source_event"] == "unknown"
    assert quislings["public_context_supported_fields"] == ["date"]
    assert (
        "52f9b9f99f66ff3bc786183803f3a8d68277604471cd411027441989337c9351"
        not in corrections
    )
