from __future__ import annotations

import csv
import json
from pathlib import Path

from tools.generated_image_review.assessment_loader import load_corpus_review_items, load_review_items, normalise_flags

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def make_item(root: Path, quote_hash: str, quote: str = "A quote.") -> None:
    item_dir = root / "items" / quote_hash
    item_dir.mkdir(parents=True)
    (item_dir / "image_01.png").write_bytes(b"png")
    (item_dir / "quote.txt").write_text(quote, encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fields = ["quote_hash", "grade", "overall_score", "flags", "assessment"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_loads_only_grade_x_and_normalises_flags(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    make_item(corpus, HASH_A, "Quote A")
    make_item(corpus, HASH_B, "Quote B")
    assessment = tmp_path / "assessment.csv"
    write_csv(
        assessment,
        [
            {"quote_hash": HASH_A, "grade": "X", "overall_score": "9", "flags": "visible_text, odd", "assessment": "Needs review"},
            {"quote_hash": HASH_B, "grade": "A", "overall_score": "2", "flags": "", "assessment": "Fine"},
        ],
    )

    items, summary = load_review_items(assessment, corpus, grade="X")

    assert [item.quote_hash for item in items] == [HASH_A]
    assert items[0].flags == ["visible_text", "odd"]
    assert items[0].quote_text == "Quote A"
    assert summary.rows_loaded == 2
    assert summary.grade_rows == 1
    assert summary.reviewable == 1


def test_nan_and_empty_flags_become_empty_lists() -> None:
    assert normalise_flags(float("nan")) == []
    assert normalise_flags("") == []
    assert normalise_flags("NaN") == []
    assert normalise_flags(["wrong_subject", " visible_text, odd "]) == ["wrong_subject", "visible_text", "odd"]


def test_missing_images_and_malformed_hashes_are_excluded(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    make_item(corpus, HASH_A)
    assessment = tmp_path / "assessment.csv"
    write_csv(
        assessment,
        [
            {"quote_hash": HASH_A, "grade": "X", "overall_score": "1", "flags": "", "assessment": ""},
            {"quote_hash": HASH_C, "grade": "X", "overall_score": "1", "flags": "", "assessment": ""},
            {"quote_hash": "../bad", "grade": "X", "overall_score": "1", "flags": "", "assessment": ""},
        ],
    )

    items, summary = load_review_items(assessment, corpus)

    assert [item.quote_hash for item in items] == [HASH_A]
    assert summary.missing_images == 1
    assert summary.malformed_items == 1


def test_json_items_object_and_bare_nan_are_supported(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    make_item(corpus, HASH_D)
    assessment = tmp_path / "assessment.json"
    assessment.write_text(
        '{"items": {"%s": {"grade": "X", "overall_score": 7, "flags": NaN, "assessment": "JSON row"}}}'
        % HASH_D,
        encoding="utf-8",
    )

    items, summary = load_review_items(assessment, corpus)

    assert len(items) == 1
    assert items[0].overall_score == "7"
    assert items[0].flags == []
    assert summary.reviewable == 1


def test_missing_quote_text_is_malformed(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    item_dir = corpus / "items" / HASH_A
    item_dir.mkdir(parents=True)
    (item_dir / "image_01.png").write_bytes(b"png")
    assessment = tmp_path / "assessment.json"
    assessment.write_text(json.dumps([{"quote_hash": HASH_A, "grade": "X"}]), encoding="utf-8")

    items, summary = load_review_items(assessment, corpus)

    assert items == []
    assert summary.malformed_items == 1


def test_loads_review_items_directly_from_corpus_index(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    make_item(corpus, HASH_A, "Quote A")
    make_item(corpus, HASH_B, "Quote B")
    (corpus / "corpus_index.json").write_text(
        json.dumps(
            {
                "entries": [
                    {"quote_hash": HASH_A, "text": "Index quote A"},
                    {"quote_hash": HASH_B, "text": "Index quote B"},
                    {"quote_hash": HASH_C, "text": "Missing image"},
                    {"quote_hash": "../bad", "text": "Traversal"},
                ]
            }
        ),
        encoding="utf-8",
    )

    items, summary = load_corpus_review_items(corpus)

    assert [item.quote_hash for item in items] == [HASH_A, HASH_B]
    assert items[0].grade == "unreviewed"
    assert items[0].quote_text == "Quote A"
    assert summary.rows_loaded == 4
    assert summary.reviewable == 2
    assert summary.missing_images == 1
    assert summary.malformed_items == 1
