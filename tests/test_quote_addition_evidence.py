"""Exercise evidence admission with synthetic retained sources, never private inputs."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import fitz
import pytest

from tools.quote_addition_evidence import curated_source_for

DATE = "2031-04-09"
URL = "https://www.margaretthatcher.org/document/123456"
PASSAGE = "Freedom requires responsibility. This is the surrounding argument."
CONTEXT = "The setting concerns individual liberty and public responsibility."


@pytest.fixture
def reviewed_html(tmp_path: Path) -> dict:
    """Use the two retained archive check shapes found in September inputs."""
    document = f'''<html><body><article><time datetime="1991-03-08">1991 Mar 8 Fr</time>
    <div class="docauthor">Margaret Thatcher</div><h1>Speech about freedom</h1>
    <table><tr><td>Source:</td><td>Thatcher MSS (Churchill Archive Centre): THCR</td></tr></table>
    <p id="passage">{PASSAGE}</p><p id="context">{CONTEXT}</p></article></body></html>'''
    path = tmp_path / "source.html"
    path.write_text(document, encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "candidate_id": "synthetic", "primary_source_path": str(path),
        "primary_source_sha256": digest, "source_url": URL,
        "source_title": "Speech about freedom", "exact_supporting_passage": PASSAGE,
        "supporting_context": [{"exact_supporting_text": CONTEXT}],
        "packet": {"quote_text": "Freedom requires responsibility.",
                   "verified_text": "Freedom requires responsibility.", "verification_status": "excerpt",
                   "speaker": "Margaret Thatcher", "date": "1991-03-08",
                   "source_event": "Speech about freedom", "stable_locator": "Paragraph one",
                   "sources": [{"title": "Speech about freedom", "url": URL, "source_type": "primary_website_transcript"}]},
        "evidence_checks": [
            {"claim": "Selected wording appears in this paragraph", "method": "Read retained source paragraph",
             "kind": "wording_and_context", "source_path": str(path), "source_sha256": digest,
             "source_url": URL, "exact_supporting_text": PASSAGE, "location": "//p[@id='passage']"},
            {"claim": "Adjacent text establishes the immediate context", "inspection_method": "Inspected retained archive text",
             "evidence_origin": "saved_archive_html", "source_path": str(path), "source_sha256": digest,
             "url": URL, "supporting_text": CONTEXT, "locator": "Adjacent paragraph"},
        ],
    }


def test_realistic_archive_evidence_is_deterministic_and_date_supplied(reviewed_html: dict) -> None:
    """Admit retained text with explicit provenance without embedding a batch date."""
    result = curated_source_for(reviewed_html, recorded_at=DATE)
    assert result == curated_source_for(reviewed_html, recorded_at=DATE)
    assert result["recorded_at"] == DATE
    assert result["source_quality_class"] == "strong_primary_evidence"
    assert result["source_type"] == "official_primary_transcript"
    assert result["wording_match_kind"] == "exact"
    assert result["source_publisher"] == "Margaret Thatcher Foundation"
    assert result["page_independently_inspected"] is True
    assert result["exact_supporting_passage_sha256"] == hashlib.sha256(PASSAGE.encode()).hexdigest()


@pytest.mark.parametrize("url", ["https://example.com/document/123456", "https://www.margaretthatcher.org.evil.test/document/123456",
                                  "https://www.margaretthatcher.org@evil.test/document/123456", "http://www.margaretthatcher.org/document/123456"])
def test_arbitrary_urls_cannot_acquire_official_primary_status(reviewed_html: dict, url: str) -> None:
    """Presence of a URL is not evidence of an official transcript or publisher."""
    reviewed_html["source_url"] = url
    reviewed_html["packet"]["sources"][0]["url"] = url
    for check in reviewed_html["evidence_checks"]:
        check["source_url"] = url
        check.pop("url", None)
    with pytest.raises(ValueError, match="URL|source type"):
        curated_source_for(reviewed_html, recorded_at=DATE)


@pytest.mark.parametrize("checks", [True, "reviewed", [], [{}], [{"claim": "yes", "method": "checked", "supporting_text": "fine"}]])
def test_meaningless_or_malformed_checks_are_rejected(reviewed_html: dict, checks: object) -> None:
    """Truthy input cannot substitute for structured, substantive evidence."""
    reviewed_html["evidence_checks"] = checks
    with pytest.raises(ValueError, match="evidence_checks|Evidence"):
        curated_source_for(reviewed_html, recorded_at=DATE)


@pytest.mark.parametrize("field", ["exact_supporting_passage", "supporting_context", "check_text"])
def test_absent_passage_context_or_check_is_rejected(reviewed_html: dict, field: str) -> None:
    """Checks must correspond to real retained source text, not supplied claims."""
    invented = "This invented wording is not in the retained source."
    if field == "check_text":
        reviewed_html["evidence_checks"][0]["exact_supporting_text"] = invented
    else:
        reviewed_html[field] = [invented] if field == "supporting_context" else invented
    with pytest.raises(ValueError, match="absent"):
        curated_source_for(reviewed_html, recorded_at=DATE)


@pytest.mark.parametrize("classification", [
    {"source_quality_class": "reliable_secondary_evidence"},
    {"source_type": "secondary_history", "source_quality_class": "reliable_secondary_evidence"},
    {"source_publisher": "Unrelated publisher"},
    {"wording_match_kind": "historical_variant"},
])
def test_contradictory_classifications_are_rejected(reviewed_html: dict, classification: dict) -> None:
    """A classification cannot contradict source, publisher or packet status."""
    reviewed_html["reviewed_classification"] = dict(classification, rationale="Explicit reviewed source assessment")
    with pytest.raises(ValueError, match="contradict|compatible"):
        curated_source_for(reviewed_html, recorded_at=DATE)


def test_nonmatching_wording_cannot_acquire_exact_status(reviewed_html: dict) -> None:
    """The preparator verifies the complete proposed wording as well as the passage."""
    reviewed_html["packet"]["quote_text"] = "Freedom eliminates responsibility."
    with pytest.raises(ValueError, match="Exact wording"):
        curated_source_for(reviewed_html, recorded_at=DATE)


def test_invented_source_event_is_rejected(reviewed_html: dict) -> None:
    """Correct quote wording cannot lend authority to a contradictory occasion."""
    reviewed_html["packet"]["source_event"] = "Speech about freedom in Moscow"
    with pytest.raises(ValueError, match="source event contradicts"):
        curated_source_for(reviewed_html, recorded_at=DATE)


def test_mention_of_thatcher_does_not_make_someone_elses_speech_hers(reviewed_html: dict) -> None:
    """Require catalogue attribution, not merely her name somewhere on the page."""
    path = Path(reviewed_html["primary_source_path"])
    content = path.read_text().replace('<div class="docauthor">Margaret Thatcher</div>',
                                       '<div class="docauthor">Another speaker</div><p>This speech mentions Margaret Thatcher.</p>')
    path.write_text(content)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    reviewed_html["primary_source_sha256"] = digest
    for check in reviewed_html["evidence_checks"]:
        check["source_sha256"] = digest
    with pytest.raises(ValueError, match="claimed speaker"):
        curated_source_for(reviewed_html, recorded_at=DATE)


def test_reviewed_variant_retains_its_classification(reviewed_html: dict) -> None:
    """A reviewed variant can cite retained verified wording without upgrading it."""
    reviewed_html["packet"].update(quote_text="Freedom demands responsibility.", verification_status="variant")
    reviewed_html["reviewed_classification"] = {
        "wording_match_kind": "historical_variant", "rationale": "Reviewed lexical variation from the retained wording",
    }
    assert curated_source_for(reviewed_html, recorded_at=DATE)["wording_match_kind"] == "historical_variant"


def test_explicit_excerpt_classification_is_not_upgraded(reviewed_html: dict) -> None:
    """Retain the reviewer's weaker coverage class even when wording is present."""
    reviewed_html["reviewed_classification"] = {"wording_match_kind": "excerpt", "rationale": "Reviewed excerpt with scoped coverage"}
    assert curated_source_for(reviewed_html, recorded_at=DATE)["wording_match_kind"] == "excerpt"


def test_reviewed_composite_requires_retained_parts_and_partial_classification(reviewed_html: dict) -> None:
    """Noncontiguous parts are explicit and cannot masquerade as an exact passage."""
    parts = ["Freedom requires responsibility.", CONTEXT]
    reviewed_html["supporting_passage_parts"] = parts
    reviewed_html["exact_supporting_passage"] = " ".join(parts)
    reviewed_html["packet"].update(quote_text=" … ".join(parts), verified_text=" ".join(parts), verification_status="composite")
    reviewed_html["reviewed_classification"] = {"wording_match_kind": "partial", "rationale": "Reviewed composite of two separately retained passages"}
    assert curated_source_for(reviewed_html, recorded_at=DATE)["wording_match_kind"] == "partial"
    reviewed_html["reviewed_classification"]["wording_match_kind"] = "exact"
    with pytest.raises(ValueError, match="Composite evidence"):
        curated_source_for(reviewed_html, recorded_at=DATE)


def test_secondary_review_never_acquires_primary_publisher_or_quality(reviewed_html: dict) -> None:
    """Explicit secondary classification remains claim-scoped and secondary."""
    url = "https://history.example.org/essay"
    reviewed_html["source_url"] = url
    reviewed_html["packet"]["sources"][0].update(url=url, source_type="secondary_history")
    for check in reviewed_html["evidence_checks"]:
        check["source_url"] = url
        check.pop("url", None)
    reviewed_html["reviewed_classification"] = {
        "source_type": "secondary_history", "source_quality_class": "reliable_secondary_evidence",
        "source_domain": "history.example.org", "source_publisher": "Example history publisher",
        "wording_match_kind": "excerpt", "claims_supported": ["wording", "attribution"],
        "rationale": "Reviewed secondary discussion quoting the attributed words",
        "page_independently_inspected": False,
    }
    result = curated_source_for(reviewed_html, recorded_at=DATE)
    assert result["source_quality_class"] == "reliable_secondary_evidence"
    assert result["source_publisher"] == "Example history publisher"
    assert result["claims_supported"] == ["wording", "attribution"]
    assert result["page_independently_inspected"] is False


def test_pdf_evidence_uses_actual_page_text_and_rejects_missing_context(tmp_path: Path) -> None:
    """Read a synthetic PDF using the September bibliographic evidence shape."""
    path = tmp_path / "book.pdf"
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((50, 50), "Margaret Thatcher\nAn authored book\n2002\nFreedom requires responsibility.\nThe surrounding argument concerns liberty.")
        document.save(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    passage = "Freedom requires responsibility."
    context = "The surrounding argument concerns liberty."
    row = {"primary_source_path": str(path), "primary_source_sha256": digest,
           "exact_supporting_passage": passage, "context_support_strings": [context],
           "primary_source_title": "Margaret Thatcher, An authored book (2002)",
           "packet": {"quote_text": passage, "verified_text": passage, "verification_status": "excerpt",
                      "speaker": "Margaret Thatcher", "date": "2002", "source_event": "An authored book",
                      "stable_locator": "Printed page one", "sources": [{"url": "", "source_type": "operator_supplied_bibliographic_citation"}]},
           "evidence_checks": [{"claim": "Book text supports the quotation and context", "inspection_method": "Inspected retained book page",
                                "supporting_text": f"Margaret Thatcher\n2002\n{passage}\n{context}", "source_path": str(path),
                                "source_sha256": digest, "pdf_page_number": 1, "url": ""}]}
    result = curated_source_for(row, recorded_at=DATE)
    assert result["source_type"] == "thatcher_authored_primary_book"
    assert "source_publisher" not in result
    row["context_support_strings"] = ["Absent context cannot be asserted."]
    with pytest.raises(ValueError, match="context is absent"):
        curated_source_for(row, recorded_at=DATE)


def test_primary_source_symlink_and_changed_check_hash_are_rejected(reviewed_html: dict, tmp_path: Path) -> None:
    """Hash and ordinary-file protections remain fail closed."""
    bad = copy.deepcopy(reviewed_html)
    bad["evidence_checks"][0]["source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash contradicts"):
        curated_source_for(bad, recorded_at=DATE)
    link = tmp_path / "link.html"
    link.symlink_to(reviewed_html["primary_source_path"])
    reviewed_html["primary_source_path"] = str(link)
    with pytest.raises(ValueError, match="non-symlink"):
        curated_source_for(reviewed_html, recorded_at=DATE)


def test_pdf_layout_normalisation_preserves_real_hyphen_differences(tmp_path):
    """Only an observed extraction line break permits hyphen removal."""
    from tools.quote_addition_evidence import _RetainedSource
    source = _RetainedSource(tmp_path / "synthetic.pdf")
    assert source.contains("quasi-authoritarian", "quasi-\nauthoritarian")
    assert source.contains("authoritarian", "authori-\ntarian")
    assert not source.contains("re-sign the agreement", "resign the agreement")
    assert not source.contains("resign the agreement", "re-sign the agreement")
