"""Check addition preparation against the bot's actual metadata consumers."""
from __future__ import annotations

from pathlib import Path
import copy
import hashlib
import html
import json
import shutil

import jsonschema
import pytest

import mrsMThatcher2 as bot
from analyse_mrs_assets_xai_v4 import QUOTE_ANALYSIS_SCHEMA
from historical_context_formatter import x_weighted_length
from semantic_alignment.quote_research_schema import validate_packet
from tools.prepare_quote_additions import (
    RESEARCH, append_source, curated_source_for, read_json, update_analysis,
    prepare, write_json, file_hash, validate_batch_timestamp,
)

from tests.helpers.historical_corpus import historical_corpus_root

ROOT = Path(__file__).resolve().parents[1]
# Identities of the approved September addition, not a limit on corpus size.
ADDED_IDS = (
    "bb6575b417f97b79775369e23e40bb90dc9f703eeed7164f88ccff1515f068a9",
    "3451254a76a4e6f4d4e76d7ab0b05713b3568338ba6534251804d9c5632533ba",
    "1690ee1907a79d8388bc1e9afefec155122a11933e611c659a243efe98a1930b",
    "ae60e330e5d9ee1d250ccf5ce6735a55b8c472e9e2df590561d65a91fc26cf9f",
    "20261fbbf96a0965781b24545408120b8a447106849c0879194d6d653a9d0ba4",
    "ac97f0f2c96bc2da3d05d666a328a13c65e3479ad1e51bdf8ffc1b25f9a7ce21",
    "005fa8c8a8507c65218db36d6f4d5f27bcb7f3b3c76d020f43f67ecccae2f5aa",
    "22bf6d38d735ee82a3103d2dca4c2750df751c6303cbd37e034a57a98f0e11b2",
    "e875cd25a45a7b70d44e7b6785eeb8f8cc96f3caaf59fb3cfd9ff877c387828b",
    "6fa969435d7e1ae1567c7134499b7d3c1fb6dbf7a08cb192b9a38c3a15b838d5",
    "7b80b46a6b0b1bfa59f8acc9858cb2baa984f25675daa3e1da23e06a6397ce5c",
)


def test_additions_are_complete_candidates_and_preserve_existing_metadata(historical_corpus_root: Path) -> None:
    """Every researched addition must survive real metadata lookup and selection."""
    packets = read_json(ROOT / RESEARCH / "research_packets.json")["items"]
    rows = [{"packet": packets[qid], "proposed_quote": packets[qid]["quote_text"]} for qid in ADDED_IDS]
    original = (historical_corpus_root / "mrsMThatcher.txt").read_bytes()
    original_analysis = read_json(historical_corpus_root / "quote_analysis.json")
    texts = [row["proposed_quote"] for row in rows]
    source = append_source(original, texts)
    analysis = update_analysis(original_analysis, source, rows, "anti_socialism_20260918", batch_timestamp="2026-09-18")
    assert source.startswith(original)
    assert source[len(original):].decode().splitlines() == texts
    assert all(analysis["items"][qid] == item for qid, item in original_analysis["items"].items())
    for row in rows:
        validate_packet(row["packet"])
        assert bot.quote_text_hash(row["proposed_quote"]) == row["packet"]["quote_id"]
        assert x_weighted_length(row["proposed_quote"]) <= 280
        jsonschema.validate(analysis["items"][row["packet"]["quote_id"]]["analysis"], QUOTE_ANALYSIS_SCHEMA)
    lines = source.decode().splitlines(keepends=True)
    candidates, excluded, count = bot.build_quote_candidates(
        lines, list(range(len(original.splitlines()), len(lines))), analysis, "09-18",
    )
    assert excluded == 0
    assert count == len(rows)
    assert {item["quote_hash"] for item in candidates} == {row["packet"]["quote_id"] for row in rows}


def test_append_rejects_repeated_and_multiline_quotes() -> None:
    """An already extended source cannot silently receive the same batch again."""
    source = append_source(b"Existing quotation\n", ["New quotation"])
    with pytest.raises(ValueError, match="already present"):
        append_source(source, ["New quotation"])
    with pytest.raises(ValueError, match="single lines"):
        append_source(source, ["Two\nlines"])


def test_changed_reviewed_source_is_rejected(tmp_path: Path) -> None:
    """A source edit invalidates the saved inspection before evidence is admitted."""
    row = {"candidate_id": "changed-source", "packet": {}, "primary_source_sha256": "0" * 64}
    source = tmp_path / "changed-source.html"
    source.write_text("Different source content", encoding="utf-8")
    row["primary_source_path"] = str(source)
    with pytest.raises(ValueError, match="Reviewed source has changed"):
        curated_source_for(row, recorded_at="2031-04-09")


@pytest.fixture
def synthetic_addition(tmp_path: Path, historical_corpus_root: Path):
    """Exercise the real preparator with synthetic HTML and committed packet text."""
    project = tmp_path / "project"
    shutil.copytree(historical_corpus_root, project)
    batch = project / "quotation_additions" / "synthetic"
    batch.mkdir(parents=True)
    packet = read_json(ROOT / RESEARCH / "research_packets.json")["items"][ADDED_IDS[0]]
    source = read_json(ROOT / RESEARCH / "historical_context_source_curated_evidence.json")["items"][ADDED_IDS[0]]["sources"][0]
    retained = batch / "synthetic.html"
    passage, context = source["exact_supporting_passage"], source["supporting_context"]
    retained.write_text(
        '<html><head><meta charset="utf-8"></head><body><time datetime="' + html.escape(packet["date"]) + '">Date</time>'
        '<div class="docauthor">Margaret Thatcher</div><h1>' + html.escape(source["title"]) + '</h1>'
        '<table><tr><td>Source:</td><td>Thatcher MSS (Churchill Archive Centre)</td></tr><tr><td>Editorial comments:</td><td>' + html.escape(packet["source_event"]) + '</td></tr></table>'
        '<p id="passage">' + html.escape(passage) + '</p><p id="context">' + html.escape(context) + '</p></body></html>',
        encoding="utf-8",
    )
    digest = file_hash(retained)
    checks = [{"claim": "Retained passage establishes wording and surrounding context",
               "method": "Read retained synthetic source", "kind": kind,
               "source_path": str(retained), "source_sha256": digest,
               "source_url": source["url"], "exact_supporting_text": text,
               "location": "//p[@id='" + location + "']"}
              for kind, text, location in (("wording_and_context", passage, "passage"),
                                           ("supporting_context", context, "context"))]
    row = {"candidate_id": "synthetic", "packet": packet,
           "proposed_quote": packet["quote_text"], "original_quote": packet["quote_text"],
           "primary_source_path": str(retained), "primary_source_sha256": digest,
           "source_url": source["url"], "source_title": source["title"],
           "exact_supporting_passage": passage, "supporting_context": [context], "evidence_checks": checks}
    write_json(batch / "html_records.json", [row])
    write_json(batch / "book_records.json", [])
    return project, batch


def _tree_hashes(path: Path) -> dict[str, str]:
    """Compare complete trees without relying on directory iteration order."""
    return {str(p.relative_to(path)): file_hash(p) for p in path.rglob("*") if p.is_file()}


def test_preparation_is_clean_deterministic_and_propagates_future_timestamp(synthetic_addition):
    """An old deployment file cannot enter a new, date-bound preparation."""
    from semantic_alignment.quote_research_corpus import verify_corpus_manifest
    project, batch = synthetic_addition
    staged = batch / "staged"
    staged.mkdir()
    (staged / "unreviewed-leftover.json").write_text('{"unexpected":true}')
    stamp = "2031-04-09T12:34:56Z"
    original = read_json(project / RESEARCH / "corpus_manifest.json")
    result = prepare(batch, project, batch_timestamp=stamp)
    first = _tree_hashes(staged)
    assert "unreviewed-leftover.json" not in first
    assert all(row["path"] != "unreviewed-leftover.json" for row in result["changed_files"])
    assert all(first[row["path"]] == row["prepared_sha256"] for row in result["changed_files"])
    manifest = read_json(staged / RESEARCH / "corpus_manifest.json")
    verify_corpus_manifest(manifest)
    assert manifest["records"][:-1] == original["records"]
    assert manifest["records"][-1]["source_occurrences"] == [{"line_number": 634}]
    assert manifest["record_count"] == original["record_count"] + 1
    assert manifest["source_occurrence_count"] == original["source_occurrence_count"] + 1
    copy_without_hash = dict(manifest)
    expected_hash = copy_without_hash.pop("manifest_sha256")
    assert hashlib.sha256(json.dumps(copy_without_hash, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest() == expected_hash
    assert read_json(staged / RESEARCH / "final_unresolved/final_research_status.json")["generated_timestamp"] == stamp
    assert read_json(staged / RESEARCH / "historical_context_source_role_audit.json")["audit_date"] == stamp[:10]
    assert read_json(staged / "historical_context_reply_semantic_gate_audit.json")["generated_at"] == stamp
    analysis = read_json(staged / "quote_analysis.json")
    assert analysis["updated_at"] == stamp
    assert analysis["items"][ADDED_IDS[0]]["analysed_at"] == stamp
    curated = read_json(staged / RESEARCH / "historical_context_source_curated_evidence.json")
    assert curated["items"][ADDED_IDS[0]]["sources"][0]["recorded_at"] == stamp[:10]
    assert prepare(batch, project, batch_timestamp=stamp) == result
    assert _tree_hashes(staged) == first


def test_failed_preparation_preserves_previous_completed_outputs(synthetic_addition):
    """Invalid evidence cannot replace an earlier successful staging transaction."""
    project, batch = synthetic_addition
    prepare(batch, project, batch_timestamp="2031-04-09")
    before = {name: _tree_hashes(batch / name) if name == "staged" else file_hash(batch / name)
              for name in ("staged", "previews.json", "PREVIEW.md", "validation.json")}
    rows = read_json(batch / "html_records.json")
    rows[0]["exact_supporting_passage"] = "Invented words absent from retained evidence"
    write_json(batch / "html_records.json", rows)
    with pytest.raises(ValueError, match="absent"):
        prepare(batch, project, batch_timestamp="2031-04-10")
    assert {name: _tree_hashes(batch / name) if name == "staged" else file_hash(batch / name)
            for name in before} == before
    assert not list(batch.glob(".prepare-*"))


def test_preparation_refuses_staging_symlinks(synthetic_addition, tmp_path):
    """A symlink cannot smuggle a destination outside the batch into publication."""
    project, batch = synthetic_addition
    outside = tmp_path / "outside"
    outside.mkdir()
    (batch / "staged").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic links"):
        prepare(batch, project, batch_timestamp="2031-04-09")
    assert not list(outside.iterdir())


@pytest.mark.parametrize("stamp", [None, "today", "2031-02-30", "2031-04-09T12:00:00", "2031-04-09T12:00:00+01:00"])
def test_batch_timestamp_is_explicit_and_valid(stamp):
    """Reject ambiguous, missing or impossible provenance without a clock fallback."""
    with pytest.raises((ValueError, TypeError)):
        validate_batch_timestamp(stamp)


def test_committed_manifest_has_unique_ordered_additions(historical_corpus_root):
    """All original manifest rows retain their immutable research coordinates."""
    from semantic_alignment.quote_research_corpus import verify_corpus_manifest
    original = read_json(historical_corpus_root / RESEARCH / "corpus_manifest.json")
    current = read_json(ROOT / RESEARCH / "corpus_manifest.json")
    verify_corpus_manifest(current)
    assert current["records"][:len(original["records"])] == original["records"]
    added = current["records"][len(original["records"]):]
    assert {row["quote_id"] for row in added} == set(ADDED_IDS)
    start = max(o["line_number"] for row in original["records"] for o in row["source_occurrences"]) + 1
    assert [row["source_occurrences"] for row in added] == [[{"line_number": i}] for i in range(start, start + len(ADDED_IDS))]
    assert current["record_count"] == len(current["records"])
    assert current["source_occurrence_count"] == sum(len(row["source_occurrences"]) for row in current["records"])


def test_publication_failure_restores_all_previous_outputs(tmp_path, monkeypatch):
    """A filesystem failure during publication rolls back the completed output set."""
    import tools.prepare_quote_additions as preparation
    batch, work = tmp_path / "batch", tmp_path / "work"
    for root, value in ((batch, "previous"), (work, "new")):
        (root / "staged").mkdir(parents=True)
        (root / "staged" / "artifact.json").write_text(value)
        for name in ("previews.json", "PREVIEW.md", "validation.json"):
            (root / name).write_text(value)
    before = _tree_hashes(batch)
    replace = preparation.os.replace
    def fail_preview(source, target):
        """Simulate failed publication after the staging tree has been replaced."""
        if Path(source) == work / "previews.json":
            raise OSError("simulated publication failure")
        return replace(source, target)
    monkeypatch.setattr(preparation.os, "replace", fail_preview)
    with pytest.raises(OSError, match="simulated"):
        preparation.publish_preparation(work, batch)
    assert _tree_hashes(batch) == before
