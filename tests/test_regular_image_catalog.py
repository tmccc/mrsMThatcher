"""Offline regular-image discovery, validation and history behaviour."""

from __future__ import annotations

from datetime import datetime
from glob import glob
import hashlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mrs_bot_asset_metadata import AssetMetadata
from mrs_bot_image_selection import ImageSelection
from mrs_bot_used_history import UsedHistory


class StaleImage(Exception):
    """Mark an asset whose metadata does not verify."""


def _analysis(paths: list[Path]) -> dict:
    items = {}
    path_index = {}
    for path in paths:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        path_index[path.name] = digest
        items[digest] = {"analysis": {"rank": 1, "seasonality": {"avoid_outside_season_or_occasion": False}}}
    return {"analysis_kind": "images", "schema_version": 3, "items": items, "path_index": path_index}


def _metadata(tmp_path: Path, paths: list[Path], *, pattern: str = "t*") -> AssetMetadata:
    analysis_file = tmp_path / "image_analysis.json"
    analysis_file.write_text(json.dumps(_analysis(paths)), encoding="utf-8")
    return AssetMetadata(
        log=logging.getLogger(__name__),
        quote_file=tmp_path / "quotes.json",
        quote_overrides_file=tmp_path / "overrides.json",
        image_file=analysis_file,
        image_glob=str(tmp_path / "images" / pattern),
        glob=glob,
        image_sha256=lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        stale_image_metadata=StaleImage,
        meme_file=tmp_path / "memes.json",
    )


def _selection(tmp_path: Path, metadata: AssetMetadata) -> ImageSelection:
    history = UsedHistory(
        corrupt_error=RuntimeError, unsafe_namespace=OSError,
        log=logging.getLogger(__name__), read_stable_bytes=Mock(), write_json=Mock(),
        quote_candidates=Mock(), metadata=metadata,
        quote_history_file=tmp_path / "quotes_used.json", legacy_quote_file=tmp_path / "quotes.pkl",
        image_history_file=tmp_path / "images_used.json", legacy_image_file=tmp_path / "images.pkl",
    )
    editorial = SimpleNamespace(
        enabled=False,
        apply_selection=lambda _quote, baseline, _scored, **_kwargs: baseline,
        log_comparison=lambda *_args, **_kwargs: None,
    )
    return ImageSelection(
        NoEligibleImageForQuote=RuntimeError, log=logging.getLogger(__name__),
        metadata=metadata, used_history=history, editorial=editorial,
        quote_candidates=Mock(), current_datetime=lambda: datetime(2026, 7, 5),
        score_image_for_quote=lambda _quote, image, _idf: (
            float(image["rank"]), {"rank": float(image["rank"])}, True,
        ),
        image_glob=metadata.image_glob, images_used_file=tmp_path / "images_used.json",
        max_quote_image_pair_attempts=1, UnsafeImageHistoryMigration=ValueError,
        GlobalImageUnavailable=RuntimeError, StaleImageMetadata=StaleImage,
        QuoteSpecificImageMismatch=RuntimeError, NoViableQuoteImagePair=RuntimeError,
    )


def _image(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_bytes(name.encode("utf-8"))
    return path


def test_regular_catalog_discovers_only_images_in_its_own_directory(tmp_path: Path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    original = _image(images, "t01.jpg")
    lower = _image(images, "any-name.png")
    upper = _image(images, "OTHER.PNG")
    mixed = _image(images, "Mixed.PnG")
    overlap = _image(images, "t_overlapping.png")
    _image(images, "t_notes.txt")
    _image(images, "readme.txt")
    _image(images, "tg_" + "a" * 64 + ".PNG")
    (images / "directory.png").mkdir()
    nested = images / "nested"
    nested.mkdir()
    _image(nested, "nested.png")
    _image(tmp_path, "outside.png")
    metadata = _metadata(tmp_path, [original, lower, upper, mixed, overlap])

    expected = sorted(str(path) for path in (original, lower, upper, mixed, overlap))
    assert metadata.image_paths() == expected
    assert metadata.image_paths() == expected
    assert metadata.image_paths().count(str(overlap)) == 1
    assert metadata.original_image_paths() == sorted([str(original), str(overlap)])


def test_original_only_catalog_order_is_stable(tmp_path: Path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    second = _image(images, "t02.jpg")
    first = _image(images, "t01.jpg")
    metadata = _metadata(tmp_path, [second, first])
    assert metadata.image_paths() == [str(first), str(second)]


def test_missing_and_stale_metadata_cannot_be_selected(tmp_path: Path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    valid = _image(images, "valid.png")
    missing = _image(images, "missing.PNG")
    stale = _image(images, "stale.PnG")
    metadata = _metadata(tmp_path, [valid, stale])
    stale.write_bytes(b"changed after analysis")
    choice = _selection(tmp_path, metadata).choose_matched(set(), {"analysis": {}}, {})
    assert choice["basename"] == valid.name
    assert choice["image_source"] == "generated"
    assert missing.name not in metadata.load_image()["path_index"]


def test_png_uses_the_same_score_and_seasonal_filter_as_original(tmp_path: Path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    original = _image(images, "t01.jpg")
    generated = _image(images, "other.PNG")
    metadata = _metadata(tmp_path, [original, generated])
    document = metadata.load_image()
    assert document is not None
    original_analysis = document["items"][document["path_index"][original.name]]["analysis"]
    generated_analysis = document["items"][document["path_index"][generated.name]]["analysis"]
    original_analysis["rank"] = 2
    generated_analysis["rank"] = 1
    metadata.image_file.write_text(json.dumps(document), encoding="utf-8")
    selection = _selection(tmp_path, metadata)
    assert selection.choose_matched(set(), {"analysis": {}}, {})["basename"] == original.name

    generated_analysis["rank"] = 3
    generated_analysis["seasonality"] = {
        "avoid_outside_season_or_occasion": True, "visible_season": "winter",
    }
    metadata.image_file.write_text(json.dumps(document), encoding="utf-8")
    assert selection.choose_matched(set(), {"analysis": {}}, {})["basename"] == original.name

    generated_analysis["seasonality"]["avoid_outside_season_or_occasion"] = False
    metadata.image_file.write_text(json.dumps(document), encoding="utf-8")
    assert selection.choose_matched(set(), {"analysis": {}}, {})["basename"] == generated.name


def test_basename_history_cycle_avoidance_and_png_source(tmp_path: Path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    original = _image(images, "t01.jpg")
    generated = _image(images, "z.PnG")
    selection = _selection(tmp_path, _metadata(tmp_path, [original, generated]))

    used = {original.name}
    choice = selection.choose_matched(used, {"analysis": {}}, {})
    assert choice["basename"] == generated.name
    assert choice["image_source"] == "generated"
    assert used == {original.name}

    used = {original.name, generated.name}
    choice = selection.choose_matched(
        used, {"analysis": {}}, {"last_regular_image_filename": generated.name},
    )
    assert choice["basename"] == original.name
    assert choice["image_source"] == "original"
    assert used == set()


def test_expanded_catalog_never_remaps_legacy_numeric_history(tmp_path: Path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    original = _image(images, "t01.jpg")
    generated = _image(images, "a.png")
    metadata = _metadata(tmp_path, [original, generated])
    selection = _selection(tmp_path, metadata)
    paths = metadata.image_paths()
    assert paths == [str(generated), str(original)]
    assert selection.used_history.image_corpus_verified_for_legacy_migration(paths, metadata.load_image()) is False
    assert selection.used_history.normalise_image_used_basenames({0}, paths, metadata.load_image()) == ({0}, False)
    with pytest.raises(ValueError, match="legacy integer"):
        selection.choose_matched({0}, {"analysis": {}}, {})
