from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

import mrs_bot_asset_metadata as metadata
from tests.helpers.bot_runtime import bot


def forbidden(*args, **kwargs):
    pytest.fail("unexpected asset metadata work")


class NoString:
    def __str__(self):
        forbidden()


def test_metadata_import_needs_no_bot_environment_files_or_network():
    code = """
import builtins
import collections.abc
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import socket
import sys

def forbidden(*args, **kwargs):
    raise AssertionError('metadata import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai'}:
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = forbidden
os.getenv = forbidden
os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
import mrs_bot_asset_metadata as metadata
assert 'mrsMThatcher2' not in sys.modules
assert metadata.collapse_quote_whitespace(' a\\n b ') == 'a b'
assert metadata.generated_image_origin_quote_hash('tg_' + 'A' * 64 + '.PNG') == 'a' * 64
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert bot.collapse_quote_whitespace is metadata.collapse_quote_whitespace
    assert bot.generated_image_origin_quote_hash is metadata.generated_image_origin_quote_hash


def test_hash_and_quote_lookup_use_current_helpers_logger_and_analysis_reference(monkeypatch):
    normalise = Mock(return_value="é — normalised")
    monkeypatch.setattr(bot, "collapse_quote_whitespace", normalise)
    text = object()
    assert bot.quote_text_hash(text) == hashlib.sha256("é — normalised".encode("utf-8")).hexdigest()
    normalise.assert_called_once_with(text)

    analysis = {"nested": []}
    quote = {"items": {"current": {"text": 123, "analysis": analysis}, "missing": None}}
    hash_text = Mock(return_value="current")
    log = Mock()
    monkeypatch.setattr(bot, "quote_text_hash", hash_text)
    monkeypatch.setattr(bot, "log", log)
    assert bot.quote_metadata_for_hash(quote, "current") is analysis
    hash_text.assert_called_once_with("123")
    assert bot.quote_metadata_for_hash(quote, "missing", text) is None
    normalise.assert_called_with(text)
    log.warning.assert_called_once_with(
        "Quote metadata missing for current quote hash=%s text=%r", "missing", "é — normalised",
    )


def test_json_loader_uses_current_logger_and_preserves_read_failure_boundaries(tmp_path, monkeypatch):
    path = tmp_path / "metadata.json"
    log = Mock()
    monkeypatch.setattr(bot, "log", log)
    assert bot.load_json_object(path, label="test") is None
    log.warning.assert_called_once_with("%s file missing: %s", "test", path)
    path.write_text('{"text": "é"}', encoding="utf-8")
    assert bot.load_json_object(path, label="test") == {"text": "é"}
    path.write_text("[]", encoding="utf-8")
    assert bot.load_json_object(path, label="test") is None
    log.warning.assert_called_with("%s file is not a JSON object: %s", "test", path)
    path.write_text("{", encoding="utf-8")
    assert bot.load_json_object(path, label="test") is None
    log.exception.assert_called_once_with("Failed loading %s file: %s", "test", path)


def test_overrides_and_recursive_json_merge_use_current_root_callback(monkeypatch):
    raw = {"items": {"hash": {"text": "Exact  text", "line_numbers": [1],
                             "analysis": {"nested": {"keep": (1, 2)}}}}}
    patch = {"nested": {"new": (3, 4)}}
    overrides = {"quote_overrides": {"hash": {
        "expected_text": "Exact  text", "expected_line_numbers": ["1"], "analysis_patch": patch,
    }}}
    merge = bot.deep_merge_dict
    calls = []

    def current_merge(base, change):
        calls.append((base, change))
        return merge(base, change)

    log = Mock()
    monkeypatch.setattr(bot, "deep_merge_dict", current_merge)
    monkeypatch.setattr(bot, "log", log)
    assert bot.apply_quote_analysis_overrides(raw, None) is raw
    assert bot.apply_quote_analysis_overrides(raw, {}) is raw
    result = bot.apply_quote_analysis_overrides(raw, overrides)
    assert len(calls) == 2
    assert calls[0][1] is patch and calls[1][1] is patch["nested"]
    assert calls[0][0] is not raw["items"]["hash"]["analysis"]
    assert result["items"]["hash"]["analysis"] == {"nested": {"keep": [1, 2], "new": [3, 4]}}
    assert raw["items"]["hash"]["analysis"] == {"nested": {"keep": (1, 2)}}
    log.info.assert_called_once_with("Applied quote analysis override for hash=%s reason=%s", "hash", None)


def test_quote_loader_uses_current_paths_callbacks_and_original_validation_order(monkeypatch):
    raw = {"analysis_kind": "wrong", "schema_version": "wrong", "items": []}
    override, result = {}, {}
    quote_path, override_path = object(), object()
    calls = []

    def load(path, *, label):
        calls.append((path, label))
        return raw if path is quote_path else override

    apply = Mock(return_value=result)
    log = Mock()
    monkeypatch.setattr(bot, "QUOTE_ANALYSIS_FILE", quote_path)
    monkeypatch.setattr(bot, "QUOTE_ANALYSIS_OVERRIDES_FILE", override_path)
    monkeypatch.setattr(bot, "load_json_object", load)
    monkeypatch.setattr(bot, "apply_quote_analysis_overrides", apply)
    monkeypatch.setattr(bot, "log", log)
    assert bot.load_quote_analysis() is None
    assert calls == [(quote_path, "quote analysis")]
    apply.assert_not_called()
    log.error.assert_called_once_with("Quote analysis file has unsupported analysis_kind=%r", "wrong")
    raw.update(analysis_kind="quotes", schema_version=2.0, items={})
    assert bot.load_quote_analysis() is result
    assert calls[-2:] == [(quote_path, "quote analysis"), (override_path, "quote analysis override")]
    assert apply.call_args.args[0] is raw and apply.call_args.args[1] is override


def test_image_loaders_keep_current_callbacks_lazy_paths_and_fallback_identity(tmp_path, monkeypatch):
    primary = {"analysis_kind": "images", "schema_version": 3.0, "items": {}, "path_index": {}}
    loader = Mock(return_value=primary)
    monkeypatch.setattr(bot, "load_json_object", loader)
    path = tmp_path / "primary.json"
    assert bot.load_image_analysis_file(path, label="test") is primary
    loader.assert_called_once_with(path, label="test")
    primary["schema_version"] = "3"
    log = Mock()
    monkeypatch.setattr(bot, "log", log)
    assert bot.load_image_analysis_file(path, label="test") is None
    log.error.assert_called_once_with("%s file has unsupported schema_version=%r", "test", "3")

    loader = Mock(return_value=primary)
    merge = Mock(return_value={"merged": True})
    monkeypatch.setattr(bot, "load_image_analysis_file", loader)
    monkeypatch.setattr(bot, "merge_image_analysis", merge)
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", path)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ANALYSIS_FILE", NoString())
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", False)
    assert bot.load_image_analysis() is primary
    loader.assert_called_once_with(path, label="image analysis")
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    loader.return_value = None
    assert bot.load_image_analysis() is None
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ANALYSIS_FILE", "~/generated.json")
    loader.side_effect = [primary, None]
    assert bot.load_image_analysis() is primary
    loader.assert_called_with(tmp_path / "generated.json", label="generated image analysis")
    log.warning.assert_called_once_with(
        "Generated image pool enabled but generated image analysis is unavailable; using original image pool only",
    )
    merge.assert_not_called()
    generated = {}
    loader.side_effect = [primary, generated]
    assert bot.load_image_analysis() is merge.return_value
    assert merge.call_args.args[0] is primary and merge.call_args.args[1] is generated


def test_image_merge_keeps_nested_references_sorted_warnings_and_primary_hash_item(monkeypatch):
    original_item, generated_item = {"analysis": {}}, {"analysis": {}}
    primary = {"path_index": {"b.png": "shared"}, "items": {"shared": original_item}, "source": {}}
    generated = {"path_index": {"d.png": "shared", "c.png": "new", "b.png": "collision", "a.png": "missing"},
                 "items": {"new": generated_item, "shared": {"analysis": {"ignored": True}}}}
    log = Mock()
    monkeypatch.setattr(bot, "log", log)
    assert bot.merge_image_analysis(primary, None) is primary
    result = bot.merge_image_analysis(primary, generated)
    assert result is not primary and result["source"] is primary["source"]
    assert result["path_index"] is not primary["path_index"] and result["items"] is not primary["items"]
    assert list(result["path_index"]) == ["b.png", "c.png", "d.png"]
    assert result["items"]["shared"] is original_item
    assert result["items"]["new"] is generated_item
    assert primary["path_index"] == {"b.png": "shared"}
    assert [call.args for call in log.warning.call_args_list] == [
        ("Skipping generated image metadata with missing item hash=%s basename=%s", "missing", "a.png"),
        ("Skipping generated image metadata with basename collision: %s", "b.png"),
    ]


def test_configured_catalog_uses_current_glob_boundary_and_origin_callbacks(tmp_path, monkeypatch):
    first, second, outside, missing = [tmp_path / name for name in ("a.png", "b.png", "outside.png", "missing.png")]
    for path in (first, second, outside):
        path.write_bytes(b"synthetic")
    matches = list(map(str, (second, outside, missing, first)))
    glob = Mock(return_value=matches)
    calls = []

    def boundary(path, parent):
        calls.append((path, parent))
        return path != outside

    origin = Mock(return_value="origin")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", "~/configured")
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "../*.png")
    monkeypatch.setattr(bot, "glob", glob)
    monkeypatch.setattr(bot, "path_is_same_or_child", boundary)
    monkeypatch.setattr(bot, "generated_image_origin_quote_hash", origin)
    assert bot.configured_generated_image_paths() == {first.name: first, second.name: second}
    glob.assert_called_once_with(str(tmp_path / "configured" / "../*.png"))
    assert matches == list(map(str, (second, outside, missing, first)))
    assert calls == [(p, tmp_path / "configured") for p in (first, second, outside)]
    assert [call.args for call in origin.call_args_list] == [(first.name,), (second.name,)]
    glob.return_value = [str(first), str(first)]
    with pytest.raises(ValueError, match="duplicate generated image basename"):
        bot.configured_generated_image_paths()


def test_current_catalog_sorts_original_list_in_place_and_uses_current_discovery(tmp_path, monkeypatch):
    first, second, missing = [tmp_path / name for name in ("a.png", "b.png", "missing.png")]
    first.write_bytes(b"synthetic")
    second.write_bytes(b"synthetic")
    matches = list(map(str, (second, missing, first)))
    glob = Mock(return_value=matches)
    discover = Mock(side_effect=forbidden)
    monkeypatch.setattr(bot, "glob", glob)
    monkeypatch.setattr(bot, "IMAGE_GLOB", "original-pattern")
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", NoString())
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", False)
    monkeypatch.setattr(bot, "configured_generated_image_paths", discover)
    assert bot.current_image_paths() == [str(first), str(second)]
    assert matches == sorted(matches)
    glob.assert_called_once_with("original-pattern")
    discover.assert_not_called()

    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", tmp_path)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "*.png")
    log = Mock()
    monkeypatch.setattr(bot, "log", log)
    discover.side_effect = None
    discover.return_value = {"collision": tmp_path / "generated" / first.name, "new": missing}
    assert bot.current_image_paths() == [str(first), str(second), str(missing)]
    log.warning.assert_called_once_with(
        "Skipping generated image with basename collision: %s path=%s", first.name,
        str(tmp_path / "generated" / first.name),
    )
    failure = ValueError("bad catalog")
    discover.side_effect = failure
    with pytest.raises(RuntimeError, match="unsafe or unclassifiable") as caught:
        bot.current_image_paths()
    assert caught.value.__cause__ is failure


def test_image_lookup_keeps_current_hash_exception_logger_and_nested_reference(monkeypatch):
    class CurrentStale(bot.StaleImageMetadata):
        pass

    analysis = {"nested": []}
    image = {"path_index": {"a.png": 42}, "items": {"42": {"analysis": analysis}}}
    current_hash = Mock(return_value="42")
    log = Mock()
    monkeypatch.setattr(bot, "current_image_sha256", current_hash)
    monkeypatch.setattr(bot, "StaleImageMetadata", CurrentStale)
    monkeypatch.setattr(bot, "log", log)
    assert bot.image_metadata_for_basename(image, "a.png")[1] is analysis
    current_hash.assert_not_called()
    result = bot.image_metadata_for_basename(image, "a.png", "path")
    assert result[0] == "42" and result[1] is analysis
    current_hash.assert_called_once_with("path")
    failure = OSError("read failed")
    current_hash.side_effect = failure
    with pytest.raises(CurrentStale) as caught:
        bot.image_metadata_for_basename(image, "a.png", "path")
    assert type(caught.value) is CurrentStale
    assert caught.value.args == ("Image content could not be verified for a.png",)
    assert caught.value.__context__ is failure and caught.value.__cause__ is None
    log.exception.assert_called_once_with("Could not hash current image for metadata validation: %s", "path")


def test_meme_loader_keeps_default_encoding_item_references_and_exception_boundary(monkeypatch):
    document = {"results": [
        {"filename": "a", "path": "/synthetic/b", "output_filename": "c"},
        {"filename": "c", "path": "/synthetic/a", "output_filename": "d"},
    ]}
    opener = Mock(side_effect=lambda *args, **kwargs: io.StringIO(json.dumps(document)))
    loaded = []
    json_load = json.load

    def read(stream):
        result = json_load(stream)
        loaded.append(result)
        return result

    path = object()
    log = Mock()
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", path)
    monkeypatch.setattr(bot, "log", log)
    monkeypatch.setattr(metadata, "open", opener, raising=False)
    monkeypatch.setattr(metadata.json, "load", read)
    result = bot.load_meme_analysis_index()
    opener.assert_called_once_with(path, "r")
    assert list(result) == ["a", "b", "c", "d"]
    assert result["b"] is loaded[0]["results"][0]
    assert all(result[name] is loaded[0]["results"][1] for name in ("a", "c", "d"))
    log.info.assert_called_once_with("Loaded meme analysis entries=%d", 4)
    document = []
    with pytest.raises(AttributeError, match="has no attribute 'get'"):
        bot.load_meme_analysis_index()
    log.exception.assert_not_called()
    opener.side_effect = OSError("read failed")
    assert bot.load_meme_analysis_index() == {}
    log.exception.assert_called_once_with("Failed loading meme analysis file: %s", path)
