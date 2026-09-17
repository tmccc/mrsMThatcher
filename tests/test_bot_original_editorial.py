from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import mrs_bot_original_editorial as editorial
from tests.helpers.bot_fixtures import _basic_quote
from tests.helpers.bot_runtime import bot


def forbidden(*args, **kwargs):
    pytest.fail("unexpected editorial work")


def test_editorial_import_needs_no_bot_environment_files_or_network():
    code = """
import builtins
import collections.abc
import io
import json
import logging
import math
import os
from pathlib import Path
import socket
import sys

def forbidden(*args, **kwargs):
    raise AssertionError('editorial import attempted runtime access')

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
import mrs_bot_original_editorial as editorial
assert 'mrsMThatcher2' not in sys.modules
assert editorial.original_editorial_numeric('5.5', key='utility') == 5.5
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert bot.original_editorial_numeric is editorial.original_editorial_numeric


def test_concepts_use_current_mutable_vocabulary_and_recursive_root_helper(monkeypatch):
    synonyms, affinity = {"alias": "freedom"}, {"freedom"}
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_SYNONYM_TO_CONCEPT", synonyms)
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_AFFINITY_CONCEPTS", affinity)
    assert bot.original_editorial_concepts(" Alias ") == {"freedom"}
    synonyms["alias"] = "family"
    assert bot.original_editorial_concepts("alias") == set()
    affinity.add("family")
    assert bot.original_editorial_concepts("alias") == {"family"}
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_SYNONYM_TO_CONCEPT", {"alias": "economic"})
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_AFFINITY_CONCEPTS", {"economic"})
    recurse = bot.original_editorial_concepts
    normalise = bot.normalise_tag
    calls = []

    def recursive(value):
        calls.append(("recursive", value))
        return recurse(value)

    def normalised(value):
        calls.append(("normalise", value))
        return normalise(value)

    monkeypatch.setattr(bot, "original_editorial_concepts", recursive)
    monkeypatch.setattr(bot, "normalise_tag", normalised)
    assert recurse("Alias AND alias") == {"economic"}
    assert calls == [
        ("normalise", "Alias AND alias"), ("recursive", "alias"),
        ("normalise", "alias"), ("recursive", "alias"), ("normalise", "alias"),
    ]


def test_concept_collectors_use_current_list_and_concept_helpers_in_order(monkeypatch):
    fields = ["primary_topics", "secondary_topics", "tone"]
    prefs = ["preferred_subject_moods", "preferred_scenes", "preferred_activities",
             "preferred_visible_symbols", "visual_affinities"]
    history = ["referenced_events", "referenced_people", "referenced_places", "specificity"]
    quote = {key: key for key in fields}
    quote["archive_image_preferences"] = {key: key for key in prefs}
    quote["historical_context"] = {key: key for key in history}
    calls = []
    monkeypatch.setattr(bot, "as_string_list", lambda value: calls.append(("list", value)) or [value])
    monkeypatch.setattr(bot, "original_editorial_concepts", lambda value: calls.append(("concept", value)) or {value})
    ordered = fields + prefs + history
    assert bot.original_editorial_quote_concepts(quote) == set(ordered)
    assert calls == [("list", key) for key in ordered] + [("concept", key) for key in ordered]

    calls.clear()
    image_fields = ["abstract_quote_affinities", "editorial_functions", "best_quote_types"]
    assert bot.original_editorial_image_concepts({key: key for key in image_fields}) == set(image_fields)
    assert calls == [event for key in image_fields for event in (("list", key), ("concept", key))]
    calls.clear()
    assert bot.original_editorial_avoid_concepts({"avoid_quote_types": "avoid"}) == {"avoid"}
    assert calls == [("list", "avoid"), ("concept", "avoid")]


def test_profile_uses_current_dimensions_and_helpers_inside_comprehensions(monkeypatch):
    dimensions = list(reversed(bot.ORIGINAL_EDITORIAL_DIMENSIONS))
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_DIMENSIONS", dimensions)
    assert list(bot.original_editorial_quote_dimension_profile(None)) == dimensions
    dimensions.append("extra")
    assert bot.original_editorial_quote_dimension_profile(None)["extra"] == 0.0
    calls = []
    quote = {"primary_topics": ["topic"], "tone": ["tone"],
             "archive_image_preferences": {"preferred_scenes": ["scene"]}}
    original_list = bot.as_string_list
    monkeypatch.setattr(bot, "as_string_list", lambda value: calls.append(("list", value)) or original_list(value))
    monkeypatch.setattr(bot, "normalise_tag", lambda value: calls.append(("tag", value)) or {"topic": "government", "tone": "grave", "scene": "parliament"}[value])
    monkeypatch.setattr(bot, "original_editorial_quote_concepts", lambda value: calls.append(("controlled", value)) or {"freedom"})
    result = bot.original_editorial_quote_dimension_profile(quote)
    assert list(result) == dimensions
    assert (result["conviction"], result["warning"], result["authority"], result["statesmanship"]) == (0.65, 0.7, 0.45, 0.45)
    assert calls == [
        ("list", ["topic"]), ("list", None), ("tag", "topic"),
        ("controlled", quote), ("list", ["tone"]), ("tag", "tone"),
        ("list", ["scene"]), ("tag", "scene"),
    ]
    assert calls[3][1] is quote
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_DIMENSIONS", ["replacement"])
    assert bot.original_editorial_quote_dimension_profile(None) == {"replacement": 0.0}


def test_score_keeps_lazy_conversion_current_defaults_and_callback_references(monkeypatch):
    class NoFloat:
        def __float__(self):
            forbidden()

    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_SHADOW_WEIGHT", NoFloat())
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT", NoFloat())
    with monkeypatch.context() as lazy:
        lazy.setattr(bot, "original_editorial_quote_dimension_profile", forbidden)
        assert bot.original_editorial_shadow_score(None, None, weight=NoFloat(), max_abs_adjustment=NoFloat()) == (
            0.0, {"dimension_score": 0.0, "affinity_score": 0.0, "utility_adjustment": 0.0, "penalty": 0.0, "cap_hit": False},
        )
    quote, analysis, calls = {}, {"dimension_scores": {"conviction": 10}}, []
    monkeypatch.setattr(bot, "original_editorial_quote_dimension_profile", lambda value: calls.append(("profile", value)) or {"conviction": 1.0})
    numeric = bot.original_editorial_numeric

    def number(value, *, key):
        calls.append((key, value))
        return numeric(value, key=key)

    monkeypatch.setattr(bot, "original_editorial_numeric", number)
    monkeypatch.setattr(bot, "original_editorial_quote_concepts", lambda value: calls.append(("quote", value)) or {"freedom"})
    monkeypatch.setattr(bot, "original_editorial_image_concepts", lambda value: calls.append(("image", value)) or {"freedom"})
    monkeypatch.setattr(bot, "original_editorial_avoid_concepts", lambda value: calls.append(("avoid", value)) or set())
    score, detail = bot.original_editorial_shadow_score(quote, analysis, weight="2", max_abs_adjustment="7")
    assert score == 7.0 and detail["weighted_adjustment"] == 7.5 and detail["cap_hit"] is True
    assert calls == [("profile", quote), ("dimension_scores.conviction", 10), ("quote", quote), ("image", analysis), ("avoid", analysis), ("overall_editorial_utility", 5.5)]
    assert calls[0][1] is quote and calls[3][1] is calls[4][1] is analysis
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_SHADOW_WEIGHT", 1.0)
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT", 5.0)
    assert bot.original_editorial_shadow_score(quote, analysis)[0] == 3.75


def test_loader_cache_hit_preserves_path_key_and_return_identity_before_io(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cached = {}
    cache = {str(tmp_path / "editorial.json"): cached}
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_ANALYSIS_FILE", "~/editorial.json")
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_ANALYSIS_CACHE", cache)
    monkeypatch.setattr(bot, "current_image_paths", forbidden)
    monkeypatch.setattr(bot, "validate_original_editorial_item", forbidden)
    monkeypatch.setattr(editorial, "open", forbidden, raising=False)
    assert bot.load_original_editorial_analysis() is cached
    assert bot._ORIGINAL_EDITORIAL_ANALYSIS_CACHE is cache
    # The existing key expands home but does not resolve '..' or require a file.
    path = tmp_path / "missing" / ".." / "editorial.json"
    other = {"new": {}}
    replacement_cache = {str(path): other}
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_ANALYSIS_FILE", path)
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_ANALYSIS_CACHE", replacement_cache)
    assert bot.load_original_editorial_analysis() is other
    assert bot._ORIGINAL_EDITORIAL_ANALYSIS_CACHE is replacement_cache


def test_loader_uses_current_callbacks_dimensions_and_cache_without_failure_insertion(tmp_path, monkeypatch):
    image = str(tmp_path / "t01.jpg")
    generated = str(tmp_path / "tg_synthetic.png")
    path = tmp_path / "editorial.json"
    dimensions, cache, calls, validated = ["conviction"], {}, [], []
    data = {"analysis_kind": "synthetic", "schema_version": 3, "items": {
        "t01.jpg": {"sha256": "fresh", "analysis": {"dimension_scores": {"conviction": 5}}},
    }}
    path.write_text(json.dumps(data))
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_ANALYSIS_FILE", path)
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_ANALYSIS_KIND", "synthetic")
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_SCHEMA_VERSION", 3)
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_DIMENSIONS", dimensions)
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_ANALYSIS_CACHE", cache)
    monkeypatch.setattr(bot, "current_image_paths", lambda: calls.append("discover") or [image, generated])
    monkeypatch.setattr(bot, "generated_image_origin_quote_hash", lambda name: calls.append(("origin", name)) or ("generated" if name.startswith("tg_") else None))
    monkeypatch.setattr(bot, "current_image_sha256", lambda value: calls.append(("sha", value)) or "fresh")
    numeric, validate = bot.original_editorial_numeric, bot.validate_original_editorial_item

    def number(value, *, key):
        calls.append(("number", key, value))
        return numeric(value, key=key)

    def validator(name, entry, image_by_name):
        result = validate(name, entry, image_by_name)
        assert result is entry["analysis"]
        validated.append(result)
        return result

    monkeypatch.setattr(bot, "original_editorial_numeric", number)
    monkeypatch.setattr(bot, "validate_original_editorial_item", validator)
    result = bot.load_original_editorial_analysis()
    assert result is cache[str(path)] and result["t01.jpg"] is validated[0]
    assert calls == ["discover", ("origin", "t01.jpg"), ("sha", image),
                     ("number", "t01.jpg.dimension_scores.conviction", 5),
                     ("number", "t01.jpg.overall_editorial_utility", 5.5),
                     ("origin", "t01.jpg"), ("origin", "tg_synthetic.png")]
    calls.clear()
    assert bot.load_original_editorial_analysis() is result and calls == []
    cache.clear()
    dimensions.append("warning")
    with pytest.raises(RuntimeError, match="missing dimensions: warning") as error:
        bot.load_original_editorial_analysis()
    assert type(error.value.__cause__) is ValueError and cache == {}
    dimensions.pop()
    monkeypatch.setattr(bot, "current_image_sha256", lambda value: "changed")
    with pytest.raises(RuntimeError, match="stale SHA-256") as error:
        bot.load_original_editorial_analysis()
    assert type(error.value.__cause__) is ValueError and cache == {}


def test_disabled_empty_and_selected_paths_keep_lazy_work_and_copy_boundaries(monkeypatch):
    quote = _basic_quote()
    baseline = {"basename": "t02.jpg", "image_source": "original", "score": 10.0}
    shared = {"nested": []}
    replacement = {"basename": "t01.jpg", "components": {"topic": 9.0}, "analysis": shared}
    candidates = [baseline, replacement]
    monkeypatch.setattr(bot, "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING", False)
    monkeypatch.setattr(bot, "load_original_editorial_analysis", forbidden)
    monkeypatch.setattr(bot, "original_editorial_shadow_score", forbidden)
    monkeypatch.setattr(bot, "original_editorial_shadow_result", forbidden)
    monkeypatch.setattr(bot, "log", SimpleNamespace(info=forbidden))
    assert bot.validate_original_editorial_shadow_startup() is None
    assert bot.log_original_editorial_shadow_result(quote, baseline, candidates, selection_phase="normal") is None
    assert bot.apply_original_editorial_selection(quote, baseline, candidates, selection_phase="normal") is baseline

    monkeypatch.setattr(bot, "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING", True)
    monkeypatch.setattr(bot, "original_editorial_shadow_result", lambda *args, **kwargs: (None, None))
    assert bot.apply_original_editorial_selection(quote, baseline, candidates, selection_phase="normal") is baseline
    assert bot.log_original_editorial_shadow_result(quote, baseline, candidates, selection_phase="normal") is None
    payload = {"production_source": "original", "production_shadow_rank": 2}
    winner = {"basename": "t01.jpg", "baseline_score": 9, "editorial_adjustment": 2, "shadow_score": 11}
    logs = []

    def result(q, choice, rows, *, selection_phase):
        assert q is quote and choice is baseline and rows is candidates
        assert selection_phase == "normal"
        return payload, winner

    monkeypatch.setattr(bot, "original_editorial_shadow_result", result)
    monkeypatch.setattr(bot, "log", SimpleNamespace(info=lambda *args: logs.append(args)))
    selected = bot.apply_original_editorial_selection(quote, baseline, candidates, selection_phase="normal")
    assert selected is not replacement and selected["components"] is not replacement["components"]
    assert selected["analysis"] is shared and replacement["components"] == {"topic": 9.0}
    assert selected["score"] == 11.0 and type(selected["score"]) is float
    assert selected["components"]["original_editorial"] == 2.0
    assert payload == {"production_source": "original", "production_shadow_rank": 2}
    selection_payload = {**payload, "selection_applied": True, "selected_winner": "t01.jpg"}
    assert logs == [("ORIGINAL_EDITORIAL_SELECTION_RESULT %s", json.dumps(selection_payload, sort_keys=True, separators=(",", ":")))]
    payload["production_source"] = "generated"
    assert bot.apply_original_editorial_selection(quote, baseline, candidates, selection_phase="normal") is baseline
    assert json.loads(logs[-1][1]) == {**payload, "selection_applied": False, "selected_winner": "t02.jpg"}
    assert payload == {"production_source": "generated", "production_shadow_rank": 2}
