"""Check addition preparation against the bot's actual metadata consumers."""
from __future__ import annotations

from pathlib import Path

import jsonschema
import pytest

import mrsMThatcher2 as bot
from analyse_mrs_assets_xai_v4 import QUOTE_ANALYSIS_SCHEMA
from historical_context_formatter import x_weighted_length
from semantic_alignment.quote_research_schema import validate_packet
from tools.prepare_quote_additions import (
    RESEARCH, append_source, curated_source_for, read_json, update_analysis,
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
    analysis = update_analysis(original_analysis, source, rows, "anti_socialism_20260918")
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
        curated_source_for(row)
