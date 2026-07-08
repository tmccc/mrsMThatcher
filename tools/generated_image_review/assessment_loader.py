from __future__ import annotations

import csv
import json
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

QUOTE_HASH_RE = re.compile(r"^[A-Fa-f0-9]{64}$")


@dataclass(frozen=True)
class ReviewItem:
    quote_hash: str
    grade: str
    overall_score: str
    flags: list[str]
    assessment_text: str
    quote_text: str
    image_path: Path
    order: int


@dataclass
class LoadSummary:
    rows_loaded: int = 0
    grade_rows: int = 0
    reviewable: int = 0
    missing_images: int = 0
    malformed_items: int = 0
    skipped_other_grade: int = 0
    duplicate_hashes: int = 0
    warnings: list[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        LOGGER.warning(message)


def load_review_items(
    assessment_file: Path,
    corpus_root: Path,
    *,
    grade: str = "X",
) -> tuple[list[ReviewItem], LoadSummary]:
    assessment_file = Path(assessment_file)
    corpus_root = Path(corpus_root)
    if not assessment_file.is_file():
        raise FileNotFoundError(f"Assessment file does not exist: {assessment_file}")
    if not corpus_root.is_dir():
        raise FileNotFoundError(f"Generated-image corpus root does not exist: {corpus_root}")

    rows = _load_rows(assessment_file)
    summary = LoadSummary(rows_loaded=len(rows))
    items: list[ReviewItem] = []
    seen_hashes: set[str] = set()
    corpus_root_resolved = corpus_root.resolve()
    wanted_grade = str(grade).strip().upper()

    for order, raw_row in enumerate(rows, start=1):
        row = _normalise_row_keys(raw_row)
        row_grade = _string_value(_first(row, "grade", "final_grade", "assessment_grade", "image_grade")).upper()
        if row_grade != wanted_grade:
            summary.skipped_other_grade += 1
            continue
        summary.grade_rows += 1

        quote_hash = _string_value(
            _first(row, "quote_hash", "hash", "quote_id", "stable_quote_hash", "image_quote_hash")
        ).lower()
        if not QUOTE_HASH_RE.fullmatch(quote_hash):
            summary.malformed_items += 1
            summary.warn(f"Skipping row {order}: unusable quote_hash={quote_hash!r}")
            continue
        if quote_hash in seen_hashes:
            summary.duplicate_hashes += 1
            summary.warn(f"Skipping duplicate quote_hash={quote_hash}")
            continue

        image_path = corpus_root_resolved / "items" / quote_hash / "image_01.png"
        if not _is_relative_to(image_path.resolve(strict=False), corpus_root_resolved):
            summary.malformed_items += 1
            summary.warn(f"Skipping {quote_hash}: image path would escape corpus root")
            continue
        if not image_path.is_file():
            summary.missing_images += 1
            summary.warn(f"Skipping {quote_hash}: missing image {image_path}")
            continue

        quote_text = _read_quote_text(corpus_root_resolved, quote_hash) or _string_value(
            _first(row, "quote", "quote_text", "text", "source_quote")
        )
        if not quote_text:
            summary.malformed_items += 1
            summary.warn(f"Skipping {quote_hash}: missing quote text")
            continue

        item = ReviewItem(
            quote_hash=quote_hash,
            grade=row_grade,
            overall_score=_string_value(
                _first(row, "overall_score", "score", "final_score", "image_score")
            ),
            flags=normalise_flags(_first(row, "flags", "flag", "issues", "tags")),
            assessment_text=_string_value(
                _first(row, "assessment", "assessment_text", "notes", "rationale", "explanation", "comment")
            ),
            quote_text=quote_text,
            image_path=image_path,
            order=order,
        )
        seen_hashes.add(quote_hash)
        items.append(item)

    summary.reviewable = len(items)
    return items, summary


def load_corpus_review_items(corpus_root: Path) -> tuple[list[ReviewItem], LoadSummary]:
    corpus_root = Path(corpus_root)
    if not corpus_root.is_dir():
        raise FileNotFoundError(f"Generated-image corpus root does not exist: {corpus_root}")
    index_path = corpus_root / "corpus_index.json"
    if not index_path.is_file():
        raise FileNotFoundError(f"Corpus index does not exist: {index_path}")

    data = json.loads(index_path.read_text(encoding="utf-8"))
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise ValueError(f"Corpus index has no entries list: {index_path}")

    summary = LoadSummary(rows_loaded=len(entries))
    items: list[ReviewItem] = []
    seen_hashes: set[str] = set()
    corpus_root_resolved = corpus_root.resolve()
    for order, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            summary.malformed_items += 1
            summary.warn(f"Skipping corpus entry {order}: not an object")
            continue
        quote_hash = _string_value(entry.get("quote_hash")).lower()
        if not QUOTE_HASH_RE.fullmatch(quote_hash):
            summary.malformed_items += 1
            summary.warn(f"Skipping corpus entry {order}: unusable quote_hash={quote_hash!r}")
            continue
        if quote_hash in seen_hashes:
            summary.duplicate_hashes += 1
            summary.warn(f"Skipping duplicate quote_hash={quote_hash}")
            continue
        image_path = corpus_root_resolved / "items" / quote_hash / "image_01.png"
        if not _is_relative_to(image_path.resolve(strict=False), corpus_root_resolved):
            summary.malformed_items += 1
            summary.warn(f"Skipping {quote_hash}: image path would escape corpus root")
            continue
        if not image_path.is_file():
            summary.missing_images += 1
            summary.warn(f"Skipping {quote_hash}: missing image {image_path}")
            continue
        quote_text = _read_quote_text(corpus_root_resolved, quote_hash) or _string_value(entry.get("text"))
        if not quote_text:
            summary.malformed_items += 1
            summary.warn(f"Skipping {quote_hash}: missing quote text")
            continue
        items.append(
            ReviewItem(
                quote_hash=quote_hash,
                grade="unreviewed",
                overall_score="",
                flags=[],
                assessment_text="",
                quote_text=quote_text,
                image_path=image_path,
                order=order,
            )
        )
        seen_hashes.add(quote_hash)

    summary.grade_rows = len(entries)
    summary.reviewable = len(items)
    return items, summary


def normalise_flags(value: Any) -> list[str]:
    if _is_empty(value):
        return []
    values: list[Any]
    if isinstance(value, list):
        values = value
    elif isinstance(value, tuple):
        values = list(value)
    else:
        values = [value]

    flags: list[str] = []
    for part in values:
        if _is_empty(part):
            continue
        for token in str(part).split(","):
            cleaned = token.strip()
            if cleaned and cleaned.lower() != "nan":
                flags.append(cleaned)
    return flags


def _load_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda _value: None)
        return _rows_from_json(data)
    raise ValueError(f"Unsupported assessment file type: {path.suffix}")


def _rows_from_json(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("items", "rows", "records", "assessments"):
        value = data.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            rows = []
            for item_key, item_value in value.items():
                if isinstance(item_value, dict):
                    row = dict(item_value)
                    row.setdefault("quote_hash", item_key)
                    rows.append(row)
            return rows
    return [data]


def _normalise_row_keys(row: dict[str, Any]) -> dict[str, Any]:
    normalised: dict[str, Any] = {}
    for key, value in row.items():
        normalised[str(key).strip().lower()] = value
    return normalised


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row:
            return row[key]
    return None


def _string_value(value: Any) -> str:
    if _is_empty(value):
        return ""
    return str(value).strip()


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and value.strip().lower() in {"", "nan", "none", "null"}:
        return True
    return False


def _read_quote_text(corpus_root: Path, quote_hash: str) -> str:
    quote_path = corpus_root / "items" / quote_hash / "quote.txt"
    try:
        if quote_path.is_file():
            return quote_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        LOGGER.warning("Could not read quote text for %s: %s", quote_hash, exc)
    return ""


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
