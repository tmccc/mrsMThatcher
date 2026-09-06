from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import mrsMThatcher2 as bot
import mrs_bot_image_scoring as scoring


def test_scoring_import_needs_no_bot_environment_files_or_network():
    code = """
import builtins
import collections.abc
import io
import os
from pathlib import Path
import socket
import sys

def forbidden(*args, **kwargs):
    raise AssertionError('scoring import attempted runtime access')

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
import mrs_bot_image_scoring as scoring
assert 'mrsMThatcher2' not in sys.modules
assert scoring.as_string_list([None, 3]) == ['3']
assert scoring.visual_energy_score('high', 'low') == -8.0
"""
    # Conftest supplies the temporary HOME, dummy credentials and dead proxies.
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_tokens_use_current_stopwords_and_root_regex_operations(monkeypatch):
    stopwords = {"military"}
    monkeypatch.setattr(bot, "TOKEN_STOPWORDS", stopwords)
    assert bot.meaningful_tokens("military crowds") == {"crowds"}
    stopwords.add("crowds")
    assert bot.meaningful_tokens("military crowds") == set()
    monkeypatch.setattr(bot, "TOKEN_STOPWORDS", set())
    assert bot.meaningful_tokens("military crowds") == {"military", "crowds"}

    calls = []

    def sub(pattern, replacement, value):
        calls.append((pattern, replacement, value))
        return "_patched_"

    def findall(pattern, value):
        calls.append((pattern, value))
        return ["patched"]

    monkeypatch.setattr(bot, "re", SimpleNamespace(sub=sub, findall=findall))
    assert bot.normalise_tag("  Mixed Tag  ") == "patched"
    assert bot.meaningful_tokens("  MiXeD  ") == {"patched"}
    assert calls == [
        (r"[^a-z0-9]+", "_", "mixed tag"),
        (r"[a-z0-9]+", "  mixed  "),
    ]


def test_phrase_helpers_use_current_root_callbacks_and_keep_round_vs_ceil(monkeypatch):
    phrase, text = object(), object()
    calls = []

    def tokens(value):
        calls.append(value)
        if value is phrase:
            return {"amber", "cobalt", "ivory", "green", "violets"}
        assert value is text
        return {"amber", "cobalt", "ivory"}

    monkeypatch.setattr(bot, "meaningful_tokens", tokens)
    assert bot.phrase_matches_text(phrase, text) is True
    assert bot.hard_mismatch_tokens(phrase) == {
        "amber", "cobalt", "ivory", "green", "violet",
    }
    assert bot.hard_mismatch_phrase_matches_text(phrase, text) is False
    assert calls == [phrase, text, phrase, phrase, text]

    calls.clear()

    def replacement_tokens(value):
        calls.append(value)
        return {"replacement"}

    monkeypatch.setattr(bot, "hard_mismatch_tokens", replacement_tokens)
    assert bot.hard_mismatch_phrase_matches_text(phrase, text) is True
    assert calls == [phrase, text]


def test_idf_and_score_comprehensions_use_root_list_and_tag_helpers(monkeypatch):
    marker = object()
    original_list, original_tag = bot.as_string_list, bot.normalise_tag
    seen = []

    def as_list(value):
        if value is marker:
            seen.append("list")
            return [marker]
        return original_list(value)

    def tag(value):
        if value is marker:
            seen.append("tag")
            return "freedom"
        return original_tag(value)

    monkeypatch.setattr(bot, "as_string_list", as_list)
    monkeypatch.setattr(bot, "normalise_tag", tag)
    image = {"themes": marker}
    assert bot.build_image_topic_idf({"items": {"one": {"analysis": image}}}) == {
        "freedom": 1.0 + (1 / 2) ** 0.5,
    }
    assert seen == ["list", "tag"]
    seen.clear()
    # Keep this assertion about tag/list lookups separate from corpus joining.
    monkeypatch.setattr(bot, "image_text_corpus", lambda value: "")
    score, components, eligible = bot.score_image_for_quote(
        {"primary_topics": ["freedom"]}, image, {"freedom": 2.0},
    )
    assert (score, components["topics"], eligible) == (14.0, 14.0, True)
    assert seen == ["list", "tag"]


def test_image_corpus_uses_current_root_list_helper(monkeypatch):
    marker = object()
    calls = []

    def as_list(value):
        calls.append(value)
        return ["patched"] if value is marker else []

    monkeypatch.setattr(bot, "as_string_list", as_list)
    assert bot.image_text_corpus({
        "description": "description",
        "historical_context": {"visible_symbols": marker},
    }) == "description patched"
    assert calls == [None, None, None, marker]


def test_score_uses_root_callbacks_including_historical_generator_and_penalty(monkeypatch):
    corpus = object()
    image = {}
    calls = []
    quote = {
        "archive_image_preferences": {
            "preferred_scenes": ["scene"],
            "strong_visual_mismatches": ["strong"],
            "weak_visual_mismatches": ["weak"],
        },
        "historical_context": {"referenced_events": ["event", "unused"]},
    }

    def image_corpus(value):
        assert value is image
        return corpus

    def matches(phrase, text):
        assert text is corpus
        calls.append(phrase)
        return True

    monkeypatch.setattr(bot, "image_text_corpus", image_corpus)
    monkeypatch.setattr(bot, "visual_energy_score", lambda *args: 11.0)
    monkeypatch.setattr(bot, "phrase_matches_text", matches)
    monkeypatch.setattr(bot, "hard_mismatch_phrase_matches_text", lambda *args: False)
    score, components, eligible = bot.score_image_for_quote(quote, image)
    assert (score, list(components.items()), eligible) == (22.0, [
        ("topics", 0.0), ("tone_mood", 0.0), ("visual_energy", 11.0),
        ("scene_activity_symbols", 2.0), ("historical", 14.0),
        ("mismatches", -5.0), ("quality", 0.0),
    ], True)
    assert calls == ["scene", "event", "weak"]

    monkeypatch.setattr(bot, "hard_mismatch_phrase_matches_text", matches)
    for penalty in (-17.5, -27.5):
        calls.clear()
        monkeypatch.setattr(bot, "IMAGE_STRONG_MISMATCH_PENALTY", penalty)
        score, components, eligible = bot.score_image_for_quote(quote, image)
        assert (score, eligible) == (penalty, False)
        assert list(components.items())[-1] == ("strong_mismatch", penalty)
        assert "mismatches" not in components and "quality" not in components
        assert calls == ["scene", "event", "strong"]


def test_season_uses_current_root_helpers_and_christmas_precedence(monkeypatch):
    marker = object()
    calls = []
    image = {"seasonality": {
        "avoid_outside_season_or_occasion": True,
        "occasions": marker,
        "visible_season": "winter",
    }}

    def as_list(value):
        assert value is marker
        return ["holiday"]

    def tag(value):
        return "christmas" if value == "holiday" else value

    def window(*args):
        calls.append(args)
        return True

    monkeypatch.setattr(bot, "as_string_list", as_list)
    monkeypatch.setattr(bot, "normalise_tag", tag)
    monkeypatch.setattr(bot, "mm_dd_in_window", window)
    assert bot.image_is_out_of_season(image, "12-20") is False
    assert calls == [("12-20", "12-10", "12-28")]
    monkeypatch.setattr(bot, "mm_dd_in_window", lambda *args: False)
    assert bot.image_is_out_of_season(image, "12-20") is True


def test_score_wrapper_preserves_argument_and_return_references(monkeypatch):
    quote, image, idf = {}, {}, {}
    result = (12.0, {"sentinel": 12.0}, True)

    def score(passed_quote, passed_image, passed_idf, **dependencies):
        assert passed_quote is quote
        assert passed_image is image
        assert passed_idf is idf
        assert dependencies["image_text_corpus"] is bot.image_text_corpus
        return result

    monkeypatch.setattr(scoring, "score_image_for_quote", score)
    assert bot.score_image_for_quote(quote, image, idf) is result
