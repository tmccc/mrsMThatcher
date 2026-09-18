"""Regression tests for canonical public historical-context sources."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import socket
from pathlib import Path
from typing import Any

import pytest
from jsonschema import validate as validate_json_schema

from historical_context_formatter import (
    format_context_reply_internal,
    format_context_reply_public,
    load_and_validate_corpus,
    packet_is_attributed_to_margaret_thatcher,
    quote_text_hash,
)
from historical_context_public_source_audit import (
    _group_identity_conflicts,
    build_audit,
    main as audit_main,
)
from historical_context_source_roles import (
    canonical_source_identity,
    canonical_source_url,
    public_source_identity_diagnostics,
    public_sources,
)
from tests.helpers.historical_corpus import historical_corpus_root


ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "semantic_alignment_research/quote_research_full_001"
KNOWN_107352_ID = "27b9bc245abb7d5e022924fd6a02b346fc4a3dfb7837c974cb569461412be069"
KNOWN_HANSARD_BRIDGE_ID = (
    "2a50d19f02e311797bac4b1b6945ca1d5348acf92a35cd079676deca68d475f4"
)
INTERNAL_COLLECTIONS = (
    "sources",
    "recovered_sources",
    "researched_sources",
    "curated_sources",
    "virtual_locator_sources",
    "model_proposed_source_leads",
    "renderable_sources",
)
PROTECTED_INPUT_PATHS = (
    ROOT / "mrsMThatcher.txt",
    ROOT / "quote_analysis.json",
    RESEARCH / "research_packets.json",
    RESEARCH / "corpus_manifest.json",
    RESEARCH / "historical_context_source_role_audit.json",
    RESEARCH / "unresolved_quotes.json",
    RESEARCH / "final_unresolved/final_research_status.json",
)


@pytest.fixture(scope="module")
def corpus() -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Load the attached source-role corpus once."""
    return load_and_validate_corpus(RESEARCH, require_source_role_audit=True)


@pytest.fixture(scope="module")
def raw_corpus() -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Load the same packets without attaching the source-role sidecar."""
    return load_and_validate_corpus(RESEARCH, load_source_role_audit=False)


def _row(
    source_id: str,
    title: str,
    url: str = "",
    *,
    roles: tuple[str, ...] = ("attribution_support",),
    claims: tuple[str, ...] = ("attribution",),
    quality: str = "strong_primary_evidence",
    source_type: str = "test_source",
    **extra: Any,
) -> dict[str, Any]:
    """Build one minimal validated renderable-source-shaped record."""
    return {
        "source_id": source_id,
        "public_title": title,
        "source_title": title,
        "public_url": url,
        "source_url": url,
        "assigned_roles": list(roles),
        "claims_supported": list(claims),
        "source_quality_class": quality,
        "source_type": source_type,
        "supporting_passages": [],
        **extra,
    }


def _with_sources(
    template: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    leads: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Replace only transient attached audit rows in a copied packet."""
    packet = copy.deepcopy(template)
    packet["_source_role_audit"]["renderable_sources"] = copy.deepcopy(rows)
    packet["_source_role_audit"]["model_proposed_source_leads"] = copy.deepcopy(
        leads or []
    )
    return packet


def _sha256(path: Path) -> str:
    """Return one file's SHA-256."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_hashes(root: Path) -> dict[str, str]:
    """Return deterministic hashes for every copied regular file."""
    return {
        str(path.relative_to(root)): _sha256(path)
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    }


def test_known_document_107352_is_one_clean_public_source(corpus):
    packet = corpus[0][KNOWN_107352_ID]
    before = copy.deepcopy(packet["_source_role_audit"])

    public = format_context_reply_public(packet)
    internal = format_context_reply_internal(packet)

    assert public is not None and internal is not None
    assert len(public["sources"]) == 1
    assert len(internal["sources"]) == 3
    assert public["sources"][0]["url"] == (
        "https://www.margaretthatcher.org/document/107352"
    )
    assert public["text"].count("https://www.margaretthatcher.org/document/107352") == 1
    assert public["text"].count("Source —") == 1
    assert (
        "Source — Margaret Thatcher Foundation, Speech to Conservative Party "
        "Conference, 1988-10-14 (Document 107352)"
    ) in public["text"]
    assert (
        "Context — Speech to Conservative Party Conference, 14 October 1988:"
        in public["text"]
    )
    assert "Verification — Exact wording verified" in public["text"]
    assert "Source (" not in public["text"]
    assert "Secondary recollection" not in public["text"]
    assert "Confidence —" not in public["text"]
    assert not re.search(r"\[[^\]]+\]\(https?://", public["text"])
    assert packet["_source_role_audit"] == before


def test_known_document_107352_keeps_lossless_internal_records_and_diagnostics(corpus):
    packet = corpus[0][KNOWN_107352_ID]
    internal = format_context_reply_internal(packet)

    assert internal is not None
    for collection in INTERNAL_COLLECTIONS:
        assert internal["internal_source_records"][collection] == packet[
            "_source_role_audit"
        ][collection]
    assert "Source (wording, attribution)" in internal["text"]
    assert (
        "Source (wording, attribution, source event, date, historical context)"
        in internal["text"]
    )
    assert "Confidence —" in internal["text"]
    diagnostics = internal["source_identity_diagnostics"]
    assert len(diagnostics["records"]) == 3
    assert len(diagnostics["groups"]) == 1
    group = diagnostics["groups"][0]
    assert group["canonical_identity"] == (
        "margaret_thatcher_foundation:document:107352"
    )
    assert group["source_record_count"] == 3
    assert set(group["public_source"]["roles"]) == {
        "attribution_support", "historical_context_support",
        "source_event_support", "wording_verification",
    }


def test_known_hansard_locator_and_official_transcript_render_once(corpus):
    packet = corpus[0][KNOWN_HANSARD_BRIDGE_ID]
    before = copy.deepcopy(packet["_source_role_audit"])

    diagnostics = public_source_identity_diagnostics(packet)
    public = format_context_reply_public(packet)
    internal = format_context_reply_internal(packet)

    assert public is not None and internal is not None
    assert len(diagnostics["records"]) == 3
    assert len(diagnostics["groups"]) == 2
    group = next(
        row for row in diagnostics["groups"]
        if row["canonical_url"].startswith("https://publications.parliament.uk/")
    )
    assert group["source_record_count"] == 1
    assert group["canonical_url"] == (
        "https://publications.parliament.uk/pa/cm199091/cmhansrd/"
        "1990-11-22/Debate-3.html"
    )
    assert len(public["sources"]) == 2
    assert next(
        source for source in public["sources"]
        if source["url"] == group["canonical_url"]
    )["title"] == (
        "UK Parliament Hansard, House of Commons, 22 November 1990, Debate 3"
    )
    assert public["text"].count("Source —") == 2
    assert public["text"].count(group["canonical_url"]) == 1
    assert len(internal["sources"]) == 3
    assert packet["_source_role_audit"] == before


def _synthetic_hansard_bridge_packet(
    template: dict[str, Any], *, url_date: str = "1990-11-22",
    secondary_url: bool = False, second_url: bool = False,
) -> dict[str, Any]:
    locator = "Hansard, HC Deb 22 November 1990 vol 181 cc439-518"
    rows = [
        _row(
            "locator", locator,
            roles=(
                "wording_verification", "attribution_support",
                "source_event_support",
            ),
            claims=("wording", "attribution", "source_event", "date"),
            source_type="canonical_stable_locator",
            virtual_locator_record=True,
        ),
        _row(
            "official",
            "publications.parliament.uk/pa/cm199091/cmhansrd/"
            f"{url_date}/Debate-3.html",
            "https://publications.parliament.uk/pa/cm199091/cmhansrd/"
            f"{url_date}/Debate-3.html",
            roles=(
                "wording_verification", "attribution_support",
                *(("secondary_recollection",) if secondary_url else ()),
            ),
            claims=("wording", "attribution"),
            quality=(
                "secondary_recollection" if secondary_url
                else "strong_primary_evidence"
            ),
            source_type=("memoir" if secondary_url else "official_transcript"),
        ),
    ]
    if second_url:
        rows.append(_row(
            "other-official",
            "publications.parliament.uk/pa/cm199091/cmhansrd/"
            "1990-11-22/Debate-4.html",
            "https://publications.parliament.uk/pa/cm199091/cmhansrd/"
            "1990-11-22/Debate-4.html",
            roles=("wording_verification", "attribution_support"),
            claims=("wording", "attribution"),
            source_type="official_transcript",
        ))
    packet = _with_sources(template, rows)
    packet["stable_locator"] = locator
    packet["date"] = "November 22, 1990"
    packet["source_event"] = "House of Commons Debate: Synthetic motion"
    return packet


def test_synthetic_hansard_bridge_requires_one_compatible_primary_url(corpus):
    template = corpus[0][KNOWN_HANSARD_BRIDGE_ID]

    positive = public_source_identity_diagnostics(
        _synthetic_hansard_bridge_packet(template)
    )
    distinct_date = public_source_identity_diagnostics(
        _synthetic_hansard_bridge_packet(template, url_date="1990-11-23")
    )
    secondary = public_source_identity_diagnostics(
        _synthetic_hansard_bridge_packet(template, secondary_url=True)
    )
    multiple_urls = public_source_identity_diagnostics(
        _synthetic_hansard_bridge_packet(template, second_url=True)
    )

    assert len(positive["groups"]) == 1
    assert positive["groups"][0]["identity_basis"] == (
        "corroborated_hansard_locator_url"
    )
    assert len(distinct_date["groups"]) == 2
    assert len(secondary["groups"]) == 2
    assert len(multiple_urls["groups"]) == 3


def test_public_and_internal_v5_results_both_match_schema(corpus):
    schema = json.loads((ROOT / "historical_context_reply_schema.json").read_text())
    packet = corpus[0][KNOWN_107352_ID]
    public = format_context_reply_public(packet)
    internal = format_context_reply_internal(packet)

    validate_json_schema(public, schema)
    validate_json_schema(internal, schema)
    assert "internal_source_records" not in public
    assert "source_identity_diagnostics" not in public
    assert "internal_source_records" in internal
    assert "source_identity_diagnostics" in internal


def test_equivalent_mtf_url_and_title_variants_share_one_identity(corpus):
    template = corpus[0][KNOWN_107352_ID]
    variants = [
        _row("www", "Margaret Thatcher Foundation document 107352",
             "https://www.margaretthatcher.org/document/107352"),
        _row("bare", "Document 107352",
             "https://margaretthatcher.org/document/107352/"),
        _row("fragment", "Margaret Thatcher Foundation source",
             "https://margaretthatcher.org/document/107352/#speech"),
        _row("tracking", "Margaret Thatcher Foundation source",
             "https://margaretthatcher.org/document/107352/?utm_source=test&gclid=x"),
    ]
    packet = _with_sources(template, variants)
    diagnostics = public_source_identity_diagnostics(packet)

    assert {canonical_source_identity(row) for row in variants} == {
        "margaret_thatcher_foundation:document:107352"
    }
    assert len(diagnostics["records"]) == 4
    assert len(diagnostics["groups"]) == 1
    assert diagnostics["groups"][0]["source_record_count"] == 4
    assert public_sources(packet)[0]["url"] == (
        "https://www.margaretthatcher.org/document/107352"
    )


def test_url_normalisation_preserves_meaningful_path_and_query_components():
    assert canonical_source_url(
        "https://margaretthatcher.org/document/107352/"
        "?utm_campaign=x&section=2#fragment"
    ) == "https://www.margaretthatcher.org/document/107352?section=2"
    assert canonical_source_url("https://Archive.Example/a//b/#fragment") == (
        "https://archive.example/a//b"
    )
    assert canonical_source_identity({
        "public_url": "https://t.co/redirect",
        "source_url": "https://archive.example/document/5",
    }) == "url:https://archive.example/document/5"


def test_url_normalisation_is_unambiguous_and_syntax_safe():
    assert canonical_source_url(
        "See (https://archive.example/wiki/Speech_(1988))."
    ) == "https://archive.example/wiki/Speech_(1988)"
    assert canonical_source_url(
        "[Speech](https://archive.example/wiki/Speech_(1988))"
    ) == "https://archive.example/wiki/Speech_(1988)"
    assert canonical_source_url(
        "https://[archive.example/speech](https://archive.example/speech)"
    ) == "https://archive.example/speech"
    assert canonical_source_url(
        "https://a.example/one https://b.example/two"
    ) == ""
    assert canonical_source_url("https://[2001:db8::1]/speech/") == (
        "https://[2001:db8::1]/speech"
    )
    assert canonical_source_url("https://archive.example\\evil/speech") == ""
    assert canonical_source_url("https://archive.example/bad path") == ""
    assert canonical_source_url("https://archive.example/bad\tpath") == ""
    assert canonical_source_url("https://archive.example/bad\npath") == ""


def test_url_query_filter_preserves_meaningful_order_flags_and_encoding():
    assert canonical_source_url(
        "https://archive.example/speech?sig=A%2FB%20C&flag&x=2"
        "&utm_source=test&gbraid=tracking&x=1"
    ) == (
        "https://archive.example/speech?sig=A%2FB%20C&flag&x=2&x=1"
    )
    assert canonical_source_url(
        "https://archive.example/speech?dclid=x&wbraid=y&section=2"
    ) == "https://archive.example/speech?section=2"
    assert canonical_source_url(
        "https://archive.example/speech?srsltid=a&gad_source=b"
        "&gad_campaignid=c&gclsrc=d&section=2"
    ) == "https://archive.example/speech?section=2"


def test_multiple_tco_only_urls_are_omitted_and_flagged(corpus):
    assert canonical_source_url("https://t.co/one https://t.co/two") == ""
    packet = _with_sources(corpus[0][KNOWN_107352_ID], [
        _row(
            "one", "Archive record A", "https://t.co/one",
            publisher="Archive Example", record_id="A",
        ),
        _row(
            "two", "Archive record A", "https://t.co/two",
            publisher="Archive Example", record_id="A",
        ),
    ])
    diagnostics = public_source_identity_diagnostics(packet)

    assert len(diagnostics["groups"]) == 1
    assert diagnostics["groups"][0]["canonical_url"] == ""
    assert "multiple_redirect_urls_for_identity" in {
        row["kind"] for row in diagnostics["identity_ambiguities"]
    }


def test_multiple_urls_at_start_of_one_field_are_omitted_and_flagged(corpus):
    packet = _with_sources(corpus[0][KNOWN_107352_ID], [
        _row(
            "ambiguous", "Archive record",
            "https://a.example/record https://b.example/record",
        ),
    ])
    diagnostics = public_source_identity_diagnostics(packet)

    assert len(diagnostics["groups"]) == 1
    assert diagnostics["groups"][0]["canonical_url"] == ""
    assert "multiple_authoritative_urls_preserved" in {
        row["kind"] for row in diagnostics["identity_ambiguities"]
    }


def test_explicit_and_nested_mtf_document_identifiers_are_equivalent():
    identities = {
        canonical_source_identity({
            "publisher": "Margaret Thatcher Foundation", "document_id": "107352",
        }),
        canonical_source_identity({
            "publisher": "Margaret Thatcher Foundation",
            "evidence_metadata": {"source": {"document_id": "107352"}},
        }),
        canonical_source_identity({"public_title": "Document 107352"}),
    }
    assert identities == {"margaret_thatcher_foundation:document:107352"}
    assert canonical_source_identity({
        "publisher": "Other Archive", "document_id": "107352",
    }) == "other_archive:document:107352"
    assert canonical_source_identity({
        "publisher": "Other Archive", "public_title": "Document 107352",
    }) != "other_archive:document:107352"


def test_document_mentions_and_lookalike_hosts_do_not_impersonate_mtf():
    primary = canonical_source_identity({
        "public_title": "Margaret Thatcher Foundation document 107352",
        "public_url": "https://www.margaretthatcher.org/document/107352",
    })
    guardian = canonical_source_identity({
        "publisher": "The Guardian",
        "public_title": (
            "Guardian report discussing Margaret Thatcher Foundation document 107352"
        ),
        "public_url": "https://www.theguardian.com/politics/report-a",
    })
    lookalike = canonical_source_identity({
        "public_title": "Archive source",
        "public_url": "https://evil.margaretthatcher.org/document/107352",
    })
    nondefault_port = canonical_source_identity({
        "public_title": "Archive source",
        "public_url": "https://margaretthatcher.org:444/document/107352",
    })

    assert primary == "margaret_thatcher_foundation:document:107352"
    assert guardian == "url:https://www.theguardian.com/politics/report-a"
    assert lookalike == "url:https://evil.margaretthatcher.org/document/107352"
    assert nondefault_port == "url:https://margaretthatcher.org:444/document/107352"
    assert len({primary, guardian, lookalike, nondefault_port}) == 4


def test_mtf_title_and_stable_locator_authority_uses_exact_hosts_and_fields():
    expected = "margaret_thatcher_foundation:document:107352"
    assert canonical_source_identity({
        "public_title": (
            "Source: https://www.margaretthatcher.org/document/107352"
        ),
    }) == expected
    assert canonical_source_identity({
        "stable_locator": "Margaret Thatcher Foundation document 107352",
    }) == expected
    assert canonical_source_identity({
        "evidence_metadata": {
            "stable_locator": "Margaret Thatcher Foundation document 107352",
        },
    }) == expected
    assert canonical_source_identity({
        "publisher": "Margaret Thatcher Foundation",
        "stable_locator": "https://evil.margaretthatcher.org/document/107352",
    }) == "url:https://evil.margaretthatcher.org/document/107352"


def test_generic_document_mentions_preserve_distinct_article_urls():
    articles = [
        {
            "publisher": "The Guardian",
            "public_title": "Report about Document 123456",
            "public_url": "https://www.theguardian.com/politics/a",
        },
        {
            "publisher": "The Guardian",
            "public_title": "Another report about Document 123456",
            "public_url": "https://www.theguardian.com/politics/b",
        },
    ]
    assert [canonical_source_identity(row) for row in articles] == [
        "url:https://www.theguardian.com/politics/a",
        "url:https://www.theguardian.com/politics/b",
    ]


def test_scoped_identifiers_and_meaningful_locators_remain_distinct(corpus):
    common = {
        "publisher": "Archive Example",
        "public_title": "Proceedings, 14 October 1988",
        "source_date": "1988-10-14",
    }
    assert canonical_source_identity({**common, "record_id": "R", "page": 41}) != (
        canonical_source_identity({**common, "record_id": "R", "page": 42})
    )
    assert canonical_source_identity({**common, "speech_id": "A"}) != (
        canonical_source_identity({**common, "speech_id": "B"})
    )
    assert canonical_source_identity({**common, "edition": "First"}) != (
        canonical_source_identity({**common, "edition": "Second"})
    )
    assert canonical_source_identity({
        **common, "record_id": "R", "page": 42,
    }) == canonical_source_identity({
        **common, "record_id": "R", "page": "p. 42",
    })
    assert canonical_source_identity({
        **common, "record_id": "R", "volume": "Vol. II", "chapter": "Chapter 3",
    }) == canonical_source_identity({
        **common, "record_id": "R", "volume": "II", "chapter": "ch. 3",
    })
    assert canonical_source_identity({
        **common, "record_id": "R", "volume": "II", "chapter": "3",
    }) != canonical_source_identity({
        **common, "record_id": "R", "volume": "III", "chapter": "3",
    })
    assert canonical_source_identity({
        "public_title": "Margaret Thatcher Archive, THCR 5/1/4/65",
        "stable_locator": "THCR 5/1/4/65 p. 1",
    }) != canonical_source_identity({
        "public_title": "Margaret Thatcher Archive, THCR 5/1/4/65",
        "stable_locator": "THCR 5/1/4/65 p. 2",
    })
    assert canonical_source_identity({
        "public_title": "Margaret Thatcher Archive, THCR 5/1/4/65",
        "stable_locator": "THCR 5/1/4/65 f82",
    }) == canonical_source_identity({
        "public_title": "Margaret Thatcher Archive, THCR 5/1/4/65",
        "stable_locator": "THCR 5/1/4/65 folio 82",
    })
    assert canonical_source_identity({
        "public_title": "Margaret Thatcher Archive, THCR 5/1/4/65",
        "stable_locator": "THCR 5/1/4/65, p. 1",
    }) != canonical_source_identity({
        "public_title": "Margaret Thatcher Archive, THCR 5/1/4/65",
        "stable_locator": "THCR 5/1/4/65, p. 2",
    })

    packet = _with_sources(corpus[0][KNOWN_107352_ID], [
        _row("one", "Unscoped record one", record_id="12345"),
        _row("two", "Unscoped record two", record_id="12345"),
    ])
    diagnostics = public_source_identity_diagnostics(packet)
    assert len(diagnostics["groups"]) == 2
    assert all(
        record["identity_basis"] == "unscoped_repository_identifier"
        for record in diagnostics["records"]
    )
    assert canonical_source_identity({
        "source_id": "same", "public_title": "One", "record_id": "A",
    }) != canonical_source_identity({
        "source_id": "same", "public_title": "Two", "record_id": "B",
    })
    assert canonical_source_identity({
        "publisher": "Archive Example", "document_id": "D1",
    }) == canonical_source_identity({
        "publisher": "Archive Example", "document_id": "D1", "record_id": "R1",
    })
    assert canonical_source_identity({
        "source_id": "one", "public_title": "Article discussing The Guardian",
        "record_id": "123",
    }) != canonical_source_identity({
        "source_id": "two", "public_title": "Different article discussing The Guardian",
        "record_id": "123",
    })


def test_numeric_mtf_locator_and_bibliographic_date_variants_normalise():
    assert canonical_source_identity({
        "publisher": "Margaret Thatcher Foundation",
        "public_title": "Conference speech",
        "stable_locator": "107352",
    }) == "margaret_thatcher_foundation:document:107352"
    assert canonical_source_identity({
        "publisher": "Margaret Thatcher Foundation",
        "public_title": "Conference speech",
        "metadata": {"stable_locator": "107352"},
    }) == "margaret_thatcher_foundation:document:107352"
    assert canonical_source_identity({
        "publisher": "Archive Example",
        "public_title": "Speech, 14 October 1988",
        "date": "14 October 1988",
    }) == canonical_source_identity({
        "publisher": "Archive Example",
        "public_title": "Speech, October 14, 1988",
        "date": "1988-10-14",
    })
    assert canonical_source_identity({
        "publisher": "Archive Example",
        "public_title": "Speech, October 1988",
        "date": "October 1988",
    }) == canonical_source_identity({
        "publisher": "Archive Example",
        "public_title": "Speech, 1988-10",
        "date": "1988-10",
    })
    assert canonical_source_identity({
        "publisher": "Archive Example",
        "public_title": "Speech, 1983 Jun 5",
        "date": "1983 Jun 5",
    }) == canonical_source_identity({
        "publisher": "Archive Example",
        "public_title": "Speech, 5 June 1983",
        "date": "1983-06-05",
    })


def test_distinct_mtf_documents_and_same_title_different_documents_do_not_merge(corpus):
    template = corpus[0][KNOWN_107352_ID]
    packet = _with_sources(template, [
        _row("one", "Speech to Conference",
             "https://www.margaretthatcher.org/document/107352"),
        _row("two", "Speech to Conference",
             "https://www.margaretthatcher.org/document/107353"),
    ])
    diagnostics = public_source_identity_diagnostics(packet)

    assert len(diagnostics["groups"]) == 2
    assert {group["document_number"] for group in diagnostics["groups"]} == {
        "107352", "107353",
    }
    rendered = format_context_reply_public(packet)
    assert rendered is not None and len(rendered["sources"]) == 2
    assert rendered["text"].count("Source —") == 2
    assert rendered["text"].count("https://www.margaretthatcher.org/document/") == 2


def test_primary_transcript_and_genuine_memoir_remain_separate(corpus):
    template = corpus[0][KNOWN_107352_ID]
    packet = _with_sources(template, [
        _row("speech", "Conference speech transcript, 1988",
             "https://archive.example/speeches/1988-10-14"),
        _row(
            "memoir",
            "A Balance of Power, 1989, p. 42",
            "https://publisher.example/books/balance-of-power?page=42",
            roles=("attribution_support", "secondary_recollection"),
            quality="secondary_recollection",
            source_type="memoir",
        ),
    ])
    diagnostics = public_source_identity_diagnostics(packet)

    assert len(diagnostics["groups"]) == 2
    assert len(public_sources(packet)) == 2
    assert {group["public_source"]["source_type"] for group in diagnostics["groups"]} == {
        "strong_primary_evidence", "secondary_recollection",
    }


def test_bibliographic_fallback_separates_memoir_but_not_packet_locator_projection(
    corpus,
):
    template = corpus[0][KNOWN_107352_ID]
    common = {
        "publisher": "Archive Example",
        "source_date": "1984-04-09",
    }
    primary = _row(
        "primary", "Panorama interview, 9 April 1984",
        source_type="broadcast_transcript", **common,
    )
    memoir = _row(
        "memoir", "Panorama interview, 9 April 1984",
        roles=("attribution_support", "secondary_recollection"),
        quality="secondary_recollection", source_type="memoir", **common,
    )
    locator = _row(
        "locator", "Panorama interview, 9 April 1984",
        roles=("attribution_support", "secondary_recollection"),
        quality="secondary_recollection", source_type="canonical_stable_locator",
        virtual_locator_record=True, **common,
    )

    assert canonical_source_identity(primary) != canonical_source_identity(memoir)
    assert canonical_source_identity(primary) == canonical_source_identity(locator)
    diagnostics = public_source_identity_diagnostics(
        _with_sources(template, [primary, locator])
    )
    assert len(diagnostics["groups"]) == 1


def test_lead_identity_bridge_requires_matching_provenance(corpus):
    template = copy.deepcopy(corpus[0][KNOWN_107352_ID])
    template["stable_locator"] = (
        "Margaret Thatcher Foundation Archive, Speech Foo, 14 October 1988"
    )
    direct = _row(
        "direct", "Margaret Thatcher Foundation document 107352",
        "https://www.margaretthatcher.org/document/107352",
        provenance={"raw_response_sha256": "a" * 64},
    )
    locator = _row(
        "locator", "Margaret Thatcher Foundation Archive, Speech Foo, 14 October 1988",
        claims=("attribution", "source_event", "date"),
        source_type="canonical_stable_locator",
        virtual_locator_record=True,
    )

    def lead(token: str) -> dict[str, Any]:
        return {
            "source_id": "lead",
            "source_title": "Speech Foo",
            "source_url": "https://www.margaretthatcher.org/document/107352",
            "provenance": {"raw_response_sha256": token * 64},
        }

    matched = public_source_identity_diagnostics(
        _with_sources(template, [direct, locator], leads=[lead("a")])
    )
    mismatched = public_source_identity_diagnostics(
        _with_sources(template, [direct, locator], leads=[lead("b")])
    )
    assert len(matched["groups"]) == 1
    assert len(mismatched["groups"]) == 2
    assert {row["kind"] for row in mismatched["identity_ambiguities"]} == {
        "uncorroborated_metadata_locator_provenance"
    }


def test_possible_same_mtf_document_identity_is_diagnostic_only(corpus):
    template = copy.deepcopy(corpus[0][KNOWN_107352_ID])
    template["date"] = "1988-10-14"
    template["stable_locator"] = (
        "Margaret Thatcher Foundation Archive, Speech Foo, 14 October 1988"
    )
    direct = _row(
        "direct", "Margaret Thatcher Foundation document 107352",
        "https://www.margaretthatcher.org/document/107352",
    )
    locator = _row(
        "locator",
        "Margaret Thatcher Foundation Archive, Speech Foo, 14 October 1988",
        claims=("attribution", "source_event", "date"),
        source_type="canonical_stable_locator",
        virtual_locator_record=True,
    )

    diagnostics = public_source_identity_diagnostics(
        _with_sources(template, [direct, locator])
    )
    warning = next(
        row for row in diagnostics["identity_ambiguities"]
        if row["kind"] == "possible_same_mtf_document_identity_unresolved"
    )

    assert len(diagnostics["groups"]) == 2
    assert len(public_sources(_with_sources(template, [direct, locator]))) == 2
    assert warning == {
        "kind": "possible_same_mtf_document_identity_unresolved",
        "explicit_canonical_identity": (
            "margaret_thatcher_foundation:document:107352"
        ),
        "weak_canonical_identity": next(
            group["canonical_identity"] for group in diagnostics["groups"]
            if group["identity_basis"] == "bibliographic_identity"
        ),
        "source_ids": ["locator"],
        "weak_dates": ["1988-10-14"],
        "packet_dates": ["1988-10-14"],
    }


def test_independently_reviewed_curated_mtf_document_absorbs_weak_locator_group(
    corpus,
):
    template = copy.deepcopy(corpus[0][KNOWN_107352_ID])
    locator_title = (
        "Margaret Thatcher Foundation Archive, Speech Foo, 14 October 1988"
    )
    template["date"] = "1988-10-14"
    template["stable_locator"] = locator_title
    direct = _row(
        "direct",
        "Margaret Thatcher Foundation, Speech Foo, 1988-10-14 "
        "(Document 107352)",
        "https://www.margaretthatcher.org/document/107352",
        roles=(
            "wording_verification",
            "attribution_support",
            "source_event_support",
        ),
        claims=("wording", "attribution", "source_event", "date"),
        source_type="official_primary_transcript",
        source_date="1988-10-14",
        source_publisher="Margaret Thatcher Foundation",
        curated_evidence_record=True,
        page_independently_inspected=True,
    )
    locator = _row(
        "locator",
        locator_title,
        claims=("wording", "attribution", "date"),
        roles=(
            "wording_verification",
            "attribution_support",
            "source_event_support",
        ),
        source_type="canonical_stable_locator",
        virtual_locator_record=True,
    )
    provider = _row(
        "provider",
        locator_title,
        claims=("attribution",),
        roles=("attribution_support",),
        source_type="grounded_web_source",
    )

    diagnostics = public_source_identity_diagnostics(
        _with_sources(template, [provider, direct, locator])
    )

    assert len(diagnostics["records"]) == 3
    assert len(diagnostics["groups"]) == 1
    group = diagnostics["groups"][0]
    assert group["canonical_identity"] == (
        "margaret_thatcher_foundation:document:107352"
    )
    assert group["source_record_count"] == 3
    assert group["canonical_url"] == (
        "https://www.margaretthatcher.org/document/107352"
    )
    assert diagnostics["identity_ambiguities"] == []


def test_possible_same_mtf_warning_excludes_distinct_documents_and_recollection(
    corpus,
):
    template = copy.deepcopy(corpus[0][KNOWN_107352_ID])
    template["date"] = "1988-10-14"
    template["stable_locator"] = (
        "Margaret Thatcher Foundation Archive, Speech Foo, 14 October 1988"
    )

    def direct(source_id: str, document_number: str) -> dict[str, Any]:
        return _row(
            source_id, f"Margaret Thatcher Foundation document {document_number}",
            f"https://www.margaretthatcher.org/document/{document_number}",
        )

    locator = _row(
        "locator",
        "Margaret Thatcher Foundation Archive, Speech Foo, 14 October 1988",
        claims=("attribution", "source_event", "date"),
        source_type="canonical_stable_locator",
        virtual_locator_record=True,
    )
    distinct = public_source_identity_diagnostics(_with_sources(
        template, [direct("one", "107352"), direct("two", "107353"), locator]
    ))
    recollection = copy.deepcopy(locator)
    recollection["source_id"] = "recollection"
    recollection["assigned_roles"].append("secondary_recollection")
    recollection["source_quality_class"] = "secondary_recollection"
    primary_and_secondary = public_source_identity_diagnostics(_with_sources(
        template, [direct("one", "107352"), recollection]
    ))

    assert "possible_same_mtf_document_identity_unresolved" not in {
        row["kind"] for row in distinct["identity_ambiguities"]
    }
    assert "possible_same_mtf_document_identity_unresolved" not in {
        row["kind"] for row in primary_and_secondary["identity_ambiguities"]
    }


def test_possible_same_mtf_document_warning_corpus_count_is_stable(corpus):
    warnings = [
        (quote_id, row)
        for quote_id, packet in corpus[0].items()
        for row in public_source_identity_diagnostics(packet)["identity_ambiguities"]
        if row["kind"] == "possible_same_mtf_document_identity_unresolved"
    ]

    assert len(warnings) == 3
    assert len({quote_id for quote_id, _row in warnings}) == 3


def test_lead_bridge_rejects_conflicting_dates_and_same_path_different_hash(corpus):
    template = copy.deepcopy(corpus[0][KNOWN_107352_ID])
    wrong_title = (
        "Margaret Thatcher Foundation Archive, Speech Foo, 10 October 1980"
    )
    template["stable_locator"] = wrong_title
    template["date"] = "1988-10-14"
    direct = _row(
        "direct", "Margaret Thatcher Foundation document 107352",
        "https://www.margaretthatcher.org/document/107352",
        provenance={"raw_response_sha256": "a" * 64, "raw_response_path": "same.json"},
    )
    locator = _row(
        "locator", wrong_title,
        claims=("attribution", "source_event", "date"),
        source_type="canonical_stable_locator",
        virtual_locator_record=True,
    )
    lead = {
        "source_id": "lead",
        "source_title": "Speech Foo, 14 October 1988",
        "source_url": "https://www.margaretthatcher.org/document/107352",
        "provenance": {
            "raw_response_sha256": "a" * 64,
            "raw_response_path": "same.json",
        },
    }
    dated = public_source_identity_diagnostics(
        _with_sources(template, [direct, locator], leads=[lead])
    )
    assert len(dated["groups"]) == 2
    assert "metadata_locator_date_conflict" in {
        row["kind"] for row in dated["identity_ambiguities"]
    }

    template["date"] = "1980-10-10"
    lead["source_title"] = "Speech Foo, 10 October 1980"
    lead["provenance"]["raw_response_sha256"] = "b" * 64
    provenance = public_source_identity_diagnostics(
        _with_sources(template, [direct, locator], leads=[lead])
    )
    assert len(provenance["groups"]) == 2
    assert "uncorroborated_metadata_locator_provenance" in {
        row["kind"] for row in provenance["identity_ambiguities"]
    }


def test_virtual_locator_bridge_cannot_bypass_its_mismatching_sha(corpus):
    template = copy.deepcopy(corpus[0][KNOWN_107352_ID])
    locator_title = (
        "Margaret Thatcher Foundation Archive, Speech Foo, 14 October 1988"
    )
    template["stable_locator"] = locator_title
    template["date"] = "1988-10-14"
    direct = _row(
        "direct", "Margaret Thatcher Foundation document 107352",
        "https://www.margaretthatcher.org/document/107352",
        provenance={"raw_response_sha256": "a" * 64},
    )
    locator = _row(
        "locator", locator_title,
        claims=("attribution", "source_event", "date"),
        source_type="canonical_stable_locator", virtual_locator_record=True,
        provenance={"raw_response_sha256": "b" * 64},
    )
    lead = {
        "source_id": "lead",
        "source_title": "Speech Foo, 14 October 1988",
        "source_url": "https://www.margaretthatcher.org/document/107352",
        "provenance": {"raw_response_sha256": "a" * 64},
    }

    diagnostics = public_source_identity_diagnostics(
        _with_sources(template, [direct, locator], leads=[lead])
    )
    assert len(diagnostics["groups"]) == 2
    assert "metadata_locator_row_link_unproven" in {
        row["kind"] for row in diagnostics["identity_ambiguities"]
    }


def test_composite_mtf_hansard_locator_never_bridges(corpus):
    template = copy.deepcopy(corpus[0][KNOWN_107352_ID])
    template["stable_locator"] = (
        "Margaret Thatcher Foundation Archive, Speech Foo, 14 October 1988"
    )
    direct = _row(
        "direct", "Margaret Thatcher Foundation document 107352",
        "https://www.margaretthatcher.org/document/107352",
        provenance={"raw_response_sha256": "a" * 64},
    )
    composite = _row(
        "composite",
        "Margaret Thatcher Foundation Archive, Speech Foo, 14 October 1988",
        stable_locator="Margaret Thatcher Foundation Archive and Hansard",
        provenance={"raw_response_sha256": "a" * 64},
    )
    lead = {
        "source_id": "lead",
        "source_title": "Speech Foo, 14 October 1988",
        "source_url": "https://www.margaretthatcher.org/document/107352",
        "provenance": {"raw_response_sha256": "a" * 64},
    }
    diagnostics = public_source_identity_diagnostics(
        _with_sources(template, [direct, composite], leads=[lead])
    )
    assert len(diagnostics["groups"]) == 2
    assert any(
        record["identity_basis"] == "composite_locator"
        for record in diagnostics["records"]
    )


def test_same_url_aliases_to_one_explicit_identifier_but_conflicting_ids_stay_separate(corpus):
    template = corpus[0][KNOWN_107352_ID]
    explicit = _row(
        "explicit", "Archive record A", "https://archive.example/records/A",
        publisher="Archive Example", record_id="A",
    )
    url_only = _row(
        "url-only", "Informative archive title, 14 October 1988",
        "https://archive.example/records/A",
    )
    aliased = public_source_identity_diagnostics(
        _with_sources(template, [explicit, url_only])
    )
    assert len(aliased["groups"]) == 1
    assert aliased["groups"][0]["source_record_count"] == 2

    conflict = public_source_identity_diagnostics(_with_sources(template, [
        explicit,
        _row(
            "conflict", "Archive record B", "https://archive.example/records/A",
            publisher="Archive Example", record_id="B",
        ),
    ]))
    assert len(conflict["groups"]) == 2
    assert sum(bool(group["public_source"]["url"]) for group in conflict["groups"]) == 1
    assert {row["kind"] for row in conflict["identity_ambiguities"]} == {
        "canonical_url_identifier_conflict", "duplicate_public_url_suppressed",
    }


def test_merged_group_prefers_original_url_and_flags_multiple_originals(corpus):
    template = corpus[0][KNOWN_107352_ID]
    redirect_and_original = public_source_identity_diagnostics(_with_sources(template, [
        _row(
            "redirect", "Archive record A", "https://t.co/redirect",
            publisher="Archive Example", record_id="A",
        ),
        _row(
            "original", "Archive record A", "https://z.example/records/A",
            publisher="Archive Example", record_id="A",
        ),
    ]))
    assert len(redirect_and_original["groups"]) == 1
    assert redirect_and_original["groups"][0]["canonical_url"] == (
        "https://z.example/records/A"
    )

    multiple = public_source_identity_diagnostics(_with_sources(template, [
        _row(
            "one", "Archive record A", "https://a.example/records/A",
            publisher="Archive Example", record_id="A",
        ),
        _row(
            "two", "Archive record A", "https://b.example/records/A",
            publisher="Archive Example", record_id="A",
        ),
    ]))
    assert len(multiple["groups"]) == 1
    assert multiple["groups"][0]["canonical_url"] == ""
    assert "multiple_canonical_urls_for_identity" in {
        row["kind"] for row in multiple["identity_ambiguities"]
    }


def test_most_precise_verified_date_wins_within_one_document(corpus):
    template = corpus[0][KNOWN_107352_ID]
    packet = _with_sources(template, [
        _row(
            "year", "Margaret Thatcher Foundation, Conference speech, 1988",
            "https://www.margaretthatcher.org/document/107352",
            claims=("attribution", "source_event", "date"),
        ),
        _row(
            "day", "Margaret Thatcher Foundation, Conference speech, 14 October 1988",
            "https://www.margaretthatcher.org/document/107352",
            claims=("attribution", "source_event", "date"),
        ),
    ])
    source = public_sources(packet)[0]

    assert "14 October 1988" in source["title"]
    assert source["title"].endswith("(Document 107352)")


@pytest.mark.parametrize("label", ("Document No.", "Document number", "Document ID"))
def test_existing_document_label_is_not_duplicated(corpus, label):
    packet = _with_sources(corpus[0][KNOWN_107352_ID], [
        _row(
            "labelled",
            f"Margaret Thatcher Foundation, {label} 107352",
            "https://www.margaretthatcher.org/document/107352",
        )
    ])
    source = public_sources(packet)[0]

    assert source["title"] == "Margaret Thatcher Foundation, Document 107352"
    assert source["title"].count("107352") == 1


def test_non_generic_document_title_preserves_balanced_closing_delimiter(corpus):
    title = (
        "Margaret Thatcher Foundation, Conference speech "
        "(Document No. 107352)"
    )
    packet = _with_sources(corpus[0][KNOWN_107352_ID], [
        _row(
            "labelled", title,
            "https://www.margaretthatcher.org/document/107352",
        )
    ])

    assert public_sources(packet)[0]["title"] == title


@pytest.mark.parametrize(
    ("title", "url", "expected"),
    [
        (
            "Speech to Conference | Margaret Thatcher Foundation",
            "https://www.margaretthatcher.org/document/107352",
            "Margaret Thatcher Foundation, Speech to Conference (Document 107352)",
        ),
        (
            "margaretthatcher.org/document/107352",
            "https://www.margaretthatcher.org/document/107352",
            "Margaret Thatcher Foundation, Document 107352",
        ),
        (
            "Margaret Thatcher Foundation (THCR), Document 107352",
            "https://www.margaretthatcher.org/document/107352",
            "Margaret Thatcher Foundation, Document 107352",
        ),
        (
            "Margaret Thatcher Foundation (Document 107352)",
            "https://www.margaretthatcher.org/document/107352",
            "Margaret Thatcher Foundation, Document 107352",
        ),
        (
            "hansard.parliament.uk/Commons/1983-04-19/debates/"
            "2422d96c-7078-4211-9eb7-e899513a14ac/Engagements",
            "http://hansard.parliament.uk/Commons/1983-04-19/debates/"
            "2422d96c-7078-4211-9eb7-e899513a14ac/Engagements",
            "UK Parliament Hansard, House of Commons, 19 April 1983, Engagements",
        ),
        (
            "publications.parliament.uk/pa/cm199091/cmhansrd/"
            "1990-11-22/Debate-3.html",
            "https://publications.parliament.uk/pa/cm199091/cmhansrd/"
            "1990-11-22/Debate-3.html",
            "UK Parliament Hansard, House of Commons, 22 November 1990, Debate 3",
        ),
    ],
)
def test_public_titles_remove_archive_page_chrome(corpus, title, url, expected):
    packet = _with_sources(corpus[0][KNOWN_107352_ID], [
        _row("clean-title", title, url),
    ])

    assert public_sources(packet)[0]["title"] == expected


def test_public_source_title_expands_abbreviated_british_date(corpus):
    packet = _with_sources(corpus[0][KNOWN_107352_ID], [
        _row(
            "dated-title",
            "Margaret Thatcher Foundation, Speech at Lord Mayor's Banquet, "
            "13 Nov 1989",
            publisher="Margaret Thatcher Foundation",
            source_date="1989-11-13",
        ),
    ])

    assert public_sources(packet)[0]["title"].endswith("13 November 1989")


@pytest.mark.parametrize(
    ("raw_date", "expected_date"),
    [
        ("June 05 1987", "5 June 1987"),
        ("July 04 1977", "4 July 1977"),
        ("1983 Jun 5", "5 June 1983"),
    ],
)
def test_public_source_title_normalises_month_first_date(
    corpus, raw_date, expected_date,
):
    packet = _with_sources(corpus[0][KNOWN_107352_ID], [
        _row(
            "month-first-date",
            f"The Independent, interview, {raw_date}",
            "https://www.independent.co.uk/archive/interview",
        ),
    ])

    assert public_sources(packet)[0]["title"].endswith(expected_date)


def test_public_source_title_collapses_duplicated_terminal_page_chrome(corpus):
    packet = _with_sources(corpus[0][KNOWN_107352_ID], [
        _row(
            "duplicate-page-chrome",
            "Interview with Margaret Thatcher | The Independent | The Independent",
            "https://www.independent.co.uk/archive/interview",
        ),
    ])

    assert public_sources(packet)[0]["title"] == (
        "Interview with Margaret Thatcher | The Independent"
    )


def test_no_reliable_source_fallback_remains_explicit(corpus):
    packet = next(
        packet for packet in corpus[0].values() if not public_sources(packet)
    )
    rendered = format_context_reply_public(packet)

    assert rendered is not None
    assert rendered["sources"] == []
    assert rendered["text"].endswith("Source — No reliable source located")
    assert "Source — \n" not in rendered["text"]


@pytest.mark.parametrize(
    ("title", "url", "expected_url"),
    [
        (
            "[Archive speech](https://archive.example/speech/1)",
            "[Archive speech](https://archive.example/speech/1)",
            "https://archive.example/speech/1",
        ),
        (
            "Archive speech",
            "https://[archive.example/speech/1](https://archive.example/speech/1)",
            "https://archive.example/speech/1",
        ),
        ("Archive speech", "https://https://archive.example/speech/1", ""),
    ],
)
def test_public_urls_are_raw_safe_and_never_nested(corpus, title, url, expected_url):
    packet = _with_sources(corpus[0][KNOWN_107352_ID], [_row("safe", title, url)])
    rendered = format_context_reply_public(packet)

    assert rendered is not None
    assert rendered["sources"][0]["url"] == expected_url
    assert "[" not in rendered["text"] and "](" not in rendered["text"]
    assert "https://https://" not in rendered["text"]
    assert "https://[" not in rendered["text"]
    if expected_url:
        lines = rendered["text"].splitlines()
        assert lines.count(expected_url) == 1
        source_index = next(
            index for index, line in enumerate(lines) if line.startswith("Source —")
        )
        assert expected_url not in lines[source_index]
        assert lines[source_index + 1] == expected_url


def test_full_corpus_public_render_has_no_source_defects(corpus, raw_corpus):
    assert corpus[0]
    assert set(corpus[0]) == set(raw_corpus[0])
    assert corpus[1] == raw_corpus[1]
    for quote_id, packet in corpus[0].items():
        rendered = format_context_reply_public(packet)
        diagnostics = public_source_identity_diagnostics(packet)
        assert rendered is not None, quote_id
        assert rendered["formatter_version"] == "historical_context_reply_schema_v5"
        assert "Confidence —" not in rendered["text"]
        assert not re.search(
            r"(?m)^(?:Source|Sources|Secondary recollection) \([^\n]+\) —",
            rendered["text"],
        )
        assert not re.search(r"\[[^\]]+\]\(https?://", rendered["text"])
        assert "https://https://" not in rendered["text"]
        identities = [group["canonical_identity"] for group in diagnostics["groups"]]
        urls = [
            group["public_source"]["url"] for group in diagnostics["groups"]
            if group["public_source"]["url"]
        ]
        documents = [
            group["document_number"] for group in diagnostics["groups"]
            if group["document_number"]
        ]
        display_entries = [
            (
                group["public_source"]["title"].casefold(),
                group["public_source"]["url"],
            )
            for group in diagnostics["groups"]
        ]
        assert len(identities) == len(set(identities)), quote_id
        assert len(urls) == len(set(urls)), quote_id
        assert len(documents) == len(set(documents)), quote_id
        assert len(display_entries) == len(set(display_entries)), quote_id
        assert rendered["sources"] == [
            group["public_source"] for group in diagnostics["groups"]
        ], quote_id


def test_audit_conflict_guard_detects_distinctions_before_ready_status():
    group = {
        "canonical_identity": "synthetic:merged",
        "source_ids": ["primary", "secondary"],
    }
    records = [
        {
            "source_id": "primary",
            "document_numbers": [],
            "source_quality_class": "strong_primary_evidence",
            "locator_components": ["page:1"],
            "date_identities": ["1988-10-14"],
            "explicit_identifiers": ["record:A"],
            "authoritative_urls": ["https://archive.example/speech"],
            "composite_locator": False,
            "secondary_recollection": False,
            "title": "Primary transcript",
        },
        {
            "source_id": "secondary",
            "document_numbers": [],
            "source_quality_class": "secondary_recollection",
            "locator_components": ["page:2"],
            "date_identities": ["1990-01-01"],
            "explicit_identifiers": ["record:B"],
            "authoritative_urls": ["https://publisher.example/memoir"],
            "composite_locator": True,
            "secondary_recollection": True,
            "title": "Later memoir",
        },
    ]

    conflicts = _group_identity_conflicts([group], records)
    assert len(conflicts) == 1
    assert set(conflicts[0]["reasons"]) >= {
        "different_stable_locators_or_pages",
        "different_explicit_identifiers",
        "conflicting_verified_dates",
        "composite_locator_merged",
        "primary_and_recollection_documents_merged",
    }


def test_audit_allows_one_reviewed_primary_to_consolidate_same_document():
    group = {
        "canonical_identity": "margaret_thatcher_foundation:document:123456",
        "source_ids": ["reviewed", "legacy"],
    }
    reviewed = {
        "source_id": "reviewed",
        "document_numbers": ["123456"],
        "source_quality_class": "strong_primary_evidence",
        "source_type": "official_primary_transcript",
        "locator_components": [
            "stable_locator:margaret_thatcher_foundation_document_123456"
        ],
        "date_identities": ["1988-10-14"],
        "explicit_identifiers": [],
        "authoritative_urls": [
            "https://www.margaretthatcher.org/document/123456"
        ],
        "publisher": "margaret_thatcher_foundation",
        "composite_locator": False,
        "secondary_recollection": False,
        "title": "Reviewed transcript",
        "supporting_passage_count": 1,
        "claims_supported": [
            "wording", "attribution", "source_event", "date",
            "historical_context",
        ],
    }
    legacy = {
        **reviewed,
        "source_id": "legacy",
        "source_type": "grounded_web_source",
        "locator_components": [],
        "date_identities": [],
        "secondary_recollection": True,
        "title": "Legacy locator",
        "supporting_passage_count": 0,
        "claims_supported": ["wording", "attribution"],
    }

    assert _group_identity_conflicts([group], [reviewed, legacy]) == []


def test_corpus_quote_eligibility_cycle_confidence_and_evidence_are_unchanged(
    corpus, raw_corpus
):
    before_hashes = {path: _sha256(path) for path in PROTECTED_INPUT_PATHS}
    packets, unresolved = corpus
    raw_packets, raw_unresolved = raw_corpus

    assert packets
    assert unresolved == raw_unresolved
    assert set(packets) == set(raw_packets)
    for quote_id, packet in packets.items():
        stripped = {key: value for key, value in packet.items() if key != "_source_role_audit"}
        assert stripped == raw_packets[quote_id]
        assert packet["quote_text"] == raw_packets[quote_id]["quote_text"]
        assert packet["research_confidence"] == raw_packets[quote_id]["research_confidence"]

    eligible_ids = {
        quote_id for quote_id, packet in packets.items()
        if packet_is_attributed_to_margaret_thatcher(packet)
    }
    cycle_ids = {
        quote_text_hash(line)
        for line in (ROOT / "mrsMThatcher.txt").read_text().splitlines()
        if line.strip()
    }
    quote_analysis = json.loads((ROOT / "quote_analysis.json").read_text())["items"]
    runtime_manifest = json.loads((
        ROOT
        / "semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate"
        / "runtime_eligible_quote_manifest.json"
    ).read_text())
    runtime_ids = set(runtime_manifest["runtime_eligible_quote_ids"])
    resolved_runtime_ids = {
        runtime_manifest["runtime_quote_aliases"].get(quote_id, quote_id)
        for quote_id in runtime_ids
    }
    expected_eligible_ids = {
        quote_id for quote_id, packet in raw_packets.items()
        if packet_is_attributed_to_margaret_thatcher(packet)
    }
    assert eligible_ids == expected_eligible_ids
    assert len(runtime_ids) == runtime_manifest["runtime_eligible_quote_count"]
    assert runtime_ids <= cycle_ids
    assert runtime_ids <= set(quote_analysis)
    assert resolved_runtime_ids == eligible_ids
    assert set(runtime_manifest["resolved_manifest_quote_ids"]) == eligible_ids
    assert not eligible_ids & unresolved
    assert {path: _sha256(path) for path in PROTECTED_INPUT_PATHS} == before_hashes


def test_isolated_full_corpus_audit_is_deterministic_and_offline(
    tmp_path, monkeypatch, capsys, historical_corpus_root
):
    # This CLI's coverage contract belongs to the original research release.
    copied_research = tmp_path / "research"
    reference_root = tmp_path / "reference"
    shutil.copytree(historical_corpus_root / RESEARCH.relative_to(ROOT), copied_research)
    reference_root.mkdir()
    shutil.copy2(historical_corpus_root / "mrsMThatcher.txt", reference_root / "mrsMThatcher.txt")
    shutil.copy2(historical_corpus_root / "quote_analysis.json", reference_root / "quote_analysis.json")
    before = {
        "research": _tree_hashes(copied_research),
        "reference": _tree_hashes(reference_root),
    }

    def blocked(*_args, **_kwargs):
        raise AssertionError("full-corpus rendering audit must remain offline")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket.socket, "sendto", blocked)

    built = build_audit(copied_research, reference_root=reference_root)
    output = tmp_path / "audit.json"
    assert audit_main([
        "--research-dir", str(copied_research),
        "--reference-root", str(reference_root),
        "--output", str(output),
    ]) == 0
    capsys.readouterr()
    written = json.loads(output.read_text())
    current_packets, current_unresolved = load_and_validate_corpus(
        copied_research, require_source_role_audit=True
    )
    expected_eligible_ids = {
        quote_id for quote_id, packet in current_packets.items()
        if packet_is_attributed_to_margaret_thatcher(packet)
    }
    expected_public_source_count = sum(
        len(public_sources(packet)) for packet in current_packets.values()
    )

    assert written == built
    assert set(written["items"]) == set(current_packets)
    assert written["ready"] is True
    assert written["counts"]["completed_packet_count"] == len(current_packets)
    assert written["counts"]["unresolved_quote_count"] == len(current_unresolved)
    assert written["counts"]["attribution_eligible_quote_count"] == len(expected_eligible_ids)
    assert written["counts"]["rendered_packet_count"] == len(current_packets)
    assert written["counts"]["blocking_item_violation_count"] == 0
    assert written["counts"]["invariant_violation_count"] == 0
    assert written["source_file_hashes"][
        "historical_context_packet_corrections.json"
    ] == _sha256(
        copied_research / "historical_context_packet_corrections.json"
    )
    assert written["source_file_hashes"][
        "historical_context_source_curated_evidence.json"
    ] == _sha256(
        copied_research / "historical_context_source_curated_evidence.json"
    )
    assert all(written["invariants"]["checks"].values())
    assert written["before_deduplication"][
        "duplicate_canonical_identity_group_count"
    ] == 423
    assert written["after_deduplication"] == {
        "public_source_record_count": expected_public_source_count,
        "packets_with_duplicate_source_identity": 0,
        "duplicate_canonical_identity_group_count": 0,
        "duplicate_canonical_url_group_count": 0,
        "duplicate_raw_public_url_group_count": 0,
        "repeated_archive_document_number_group_count": 0,
        "public_source_role_leakage_count": 0,
        "markdown_link_count": 0,
        "malformed_or_nested_url_count": 0,
        "identity_conflict_count": 0,
        "identical_public_entry_conflict_count": 0,
    }
    known = written["items"][KNOWN_107352_ID]
    assert known["internal_source_record_count"] == 3
    assert known["distinct_canonical_source_count"] == 1
    assert known["public_source_lines"] == [
        "Source — Margaret Thatcher Foundation, Speech to Conservative Party "
        "Conference, 1988-10-14 (Document 107352)"
    ]
    assert known["public_urls"] == [
        "https://www.margaretthatcher.org/document/107352"
    ]
    assert before == {
        "research": _tree_hashes(copied_research),
        "reference": _tree_hashes(reference_root),
    }

    with pytest.raises(ValueError, match="outside the immutable research directory"):
        audit_main([
            "--research-dir", str(copied_research),
            "--reference-root", str(reference_root),
            "--output", str(copied_research / "forbidden.json"),
        ])
