from __future__ import annotations

import ast
import hashlib
import socket
from pathlib import Path

import pytest

import historical_context_public_render_review as review_module
from tests.helpers.historical_corpus import (
    RESEARCH_RELATIVE,
    historical_corpus_root,
)


ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "semantic_alignment_research/quote_research_full_001"
KNOWN_107352_ID = "27b9bc245abb7d5e022924fd6a02b346fc4a3dfb7837c974cb569461412be069"
KNOWN_HANSARD_ID = "2a50d19f02e311797bac4b1b6945ca1d5348acf92a35cd079676deca68d475f4"
KNOWN_YEAR_FIRST_DATE_ID = (
    "4d0ee2952c87bc6d0ef3b344bf5bba3d1b37c48888576887232760ed0a8b9651"
)
IMMUTABLE_INPUTS = (
    ROOT / "mrsMThatcher.txt",
    ROOT / "quote_analysis.json",
    RESEARCH / "corpus_manifest.json",
    RESEARCH / "research_packets.json",
    RESEARCH / "historical_context_source_role_audit.json",
    RESEARCH / "final_unresolved/final_research_status.json",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _historical_context_reply_literal() -> dict:
    """Read the posting-path defaults without importing the bot module."""
    tree = ast.parse((ROOT / "mrsMThatcher2.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name)
            and target.id == "historical_context_reply"
            for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            assert isinstance(value, dict)
            return value
    raise AssertionError("historical_context_reply defaults not found")


@pytest.fixture(scope="module")
def review(historical_corpus_root: Path) -> dict:
    research = historical_corpus_root / RESEARCH_RELATIVE
    protected = tuple(
        historical_corpus_root / path.relative_to(ROOT)
        for path in IMMUTABLE_INPUTS
    )
    before = {path: _sha256(path) for path in protected}
    monkeypatch = pytest.MonkeyPatch()

    def blocked(*_args, **_kwargs):
        raise AssertionError("all-quote render review attempted network access")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket.socket, "sendto", blocked)
    try:
        result = review_module.build_render_review(
            research, reference_root=historical_corpus_root,
        )
        independent_result = review_module.build_render_review(
            research, reference_root=historical_corpus_root,
        )
    finally:
        monkeypatch.undo()
    assert {path: _sha256(path) for path in protected} == before
    assert independent_result == result
    assert review_module.render_review_text(independent_result) == (
        review_module.render_review_text(result)
    )
    return result


def test_all_quote_review_uses_public_formatter_and_surfaces_review_queues(
    review, historical_corpus_root: Path,
):
    # This review deliberately reports blockers outside its original 627/611/5
    # batch. Its detailed distribution checks are that batch's review baseline.
    research = historical_corpus_root / RESEARCH_RELATIVE
    summary = review["summary"]
    assert summary["review_ready"] is True
    assert summary["audit_ready"] is True
    assert summary["completed_packet_count"] == 627
    assert summary["unresolved_quote_count"] == 5
    assert summary["attribution_eligible_quote_count"] == 611
    assert summary["attribution_ineligible_completed_quote_count"] == 16
    assert summary["render_failure_count"] == 0
    assert summary["item_blocker_count"] == 0
    assert summary["source_distribution"] == {
        "0": 91, "1": 507, "2": 26, "3": 3,
    }
    assert summary["packets_with_source_merges"] == 453
    assert summary["merged_internal_source_record_count"] == 1035
    assert summary["identity_ambiguity_count"] == 19
    assert summary["packets_with_identity_ambiguities"] == 17
    assert summary["packets_with_no_reliable_source"] == 91
    assert summary["eligible_packets_with_no_reliable_source"] == 80
    assert summary["packets_with_multiple_public_sources"] == 29
    assert summary["packets_with_generic_context"] == 145
    assert summary["packets_with_generic_context_and_public_source"] == 54
    assert summary[
        "packets_with_generic_context_and_event_or_date_evidence"
    ] == 0
    assert summary["packets_with_generic_context_and_exact_verification"] == 5
    assert summary["duplicate_full_public_reply_group_count"] == 1
    assert summary["packets_with_urlless_public_sources"] == 59
    assert summary["urlless_public_source_entry_count"] == 67
    assert summary["packets_with_generic_mtf_document_titles"] == 25
    assert summary["generic_mtf_document_title_entry_count"] == 25
    assert summary["packets_with_reused_identity_display_variants"] == 135
    assert summary["editorial_queue_category_count"] == 9
    assert summary["url_like_public_title_count"] == 0
    assert summary["mtf_page_title_chrome_count"] == 0
    assert summary["abbreviated_public_date_count"] == 0
    assert summary["editorial_queues_present"] is True
    assert review["formatter_options"] == {
        "maximum_length": 4000,
        "include_meaning": True,
        "include_source": True,
        "include_verification": True,
    }
    assert set(review["implementation_file_hashes"]) == {
        "historical_context_formatter.py",
        "historical_context_source_roles.py",
        "historical_context_public_source_audit.py",
        "historical_context_public_render_review.py",
    }
    assert "unresolved_quotes.json" in review["source_file_hashes"]
    assert review["source_file_hashes"][
        "historical_context_packet_corrections.json"
    ] == _sha256(research / "historical_context_packet_corrections.json")
    assert review["source_file_hashes"][
        "historical_context_source_curated_evidence.json"
    ] == _sha256(research / "historical_context_source_curated_evidence.json")
    posting_defaults = _historical_context_reply_literal()
    assert review["formatter_options"] == {
        key: posting_defaults[key] for key in review["formatter_options"]
    }

    entries = {entry["quote_id"]: entry for entry in review["entries"]}
    assert len(entries) == 627
    generic_omissions = [
        entry
        for entry in entries.values()
        if entry["template_variant"] == "compact_generic_context_omitted"
    ]
    assert len(generic_omissions) == 145
    assert all(
        "Context — The surviving attribution does not establish an occasion, "
        "date or immediate historical issue."
        not in entry["public_text"]
        for entry in generic_omissions
    )
    known = entries[KNOWN_107352_ID]
    assert known["internal_source_record_count"] == 3
    assert len(known["canonical_source_groups"]) == 1
    assert known["public_source_count"] == 1
    assert known["public_text"].count("Source —") == 1
    assert known["public_text"].count("Document 107352") == 1
    assert known["public_text"].count(
        "https://www.margaretthatcher.org/document/107352"
    ) == 1
    assert "Source (" not in known["public_text"]
    assert "](" not in known["public_text"]

    hansard = entries[KNOWN_HANSARD_ID]
    assert hansard["internal_source_record_count"] == 3
    assert len(hansard["canonical_source_groups"]) == 2
    assert hansard["public_source_count"] == 2
    assert hansard["public_text"].count("Source —") == 2
    assert hansard["public_text"].count(
        "https://publications.parliament.uk/pa/cm199091/cmhansrd/"
        "1990-11-22/Debate-3.html"
    ) == 1

    year_first_date = entries[KNOWN_YEAR_FIRST_DATE_ID]
    assert "Source — Margaret Thatcher Foundation, 5 June 1983" in (
        year_first_date["public_text"]
    )
    assert "1983 Jun 5" not in year_first_date["public_text"]


def test_review_text_is_deterministic_complete_and_scan_friendly(review):
    first = review_module.render_review_text(review)
    second = review_module.render_review_text(review)
    assert first == second
    assert first.endswith("\n")
    assert first.count("\nRECORD ") == 627
    assert first.count("\nPUBLIC REPLY (VERBATIM)\n") == 627
    assert first.count("\nEND PUBLIC REPLY\n") == 627
    assert "Automated validation status: PASS" in first
    assert "Human editorial review status: PENDING" in first
    assert "EDITORIAL REVIEW QUEUES\n-----------------------" in first
    assert "AUTOMATED BLOCKERS\n------------------\nNone." in first
    for entry in review["entries"]:
        assert first.count(f"Quote ID: {entry['quote_id']}\n") == 1


def test_review_cli_writes_only_explicit_safe_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, review: dict,
    historical_corpus_root: Path,
):
    research = historical_corpus_root / RESEARCH_RELATIVE
    monkeypatch.setattr(
        review_module, "build_render_review", lambda *_args, **_kwargs: review,
    )
    output = tmp_path / "historical_context_all_quotes_render_review.txt"
    assert review_module.main([
        "--research-dir", str(research),
        "--reference-root", str(historical_corpus_root),
        "--output", str(output),
    ]) == 0
    assert output.read_text(encoding="utf-8") == review_module.render_review_text(
        review
    )

    with pytest.raises(FileExistsError, match="already exists"):
        review_module.main([
            "--research-dir", str(research),
            "--reference-root", str(historical_corpus_root),
            "--output", str(output),
        ])
    assert review_module.main([
        "--research-dir", str(research),
        "--reference-root", str(historical_corpus_root),
        "--output", str(output),
        "--overwrite",
    ]) == 0

    unknown = tmp_path / "historical_context_unknown_render_review.txt"
    unknown.write_text("not a review artifact\n", encoding="utf-8")
    with pytest.raises(ValueError, match="prior render-review artifact"):
        review_module.main([
            "--research-dir", str(research),
            "--reference-root", str(historical_corpus_root),
            "--output", str(unknown),
            "--overwrite",
        ])

    with pytest.raises(ValueError, match="filename must identify"):
        review_module.main([
            "--research-dir", str(research),
            "--reference-root", str(historical_corpus_root),
            "--output", str(historical_corpus_root / "mrsMThatcher.txt"),
        ])

    symlink = tmp_path / "historical_context_symlink_render_review.txt"
    symlink.symlink_to(output)
    with pytest.raises(ValueError, match="symbolic link"):
        review_module.main([
            "--research-dir", str(research),
            "--reference-root", str(historical_corpus_root),
            "--output", str(symlink),
            "--overwrite",
        ])

    with pytest.raises(ValueError, match="outside the immutable research"):
        review_module.main([
            "--research-dir", str(research),
            "--reference-root", str(historical_corpus_root),
            "--output", str(
                research / "historical_context_must_not_render_review.txt"
            ),
        ])
    assert not (
        research / "historical_context_must_not_render_review.txt"
    ).exists()
