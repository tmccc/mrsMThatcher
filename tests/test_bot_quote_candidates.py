from __future__ import annotations

from datetime import datetime
import inspect
from pathlib import Path
import random
import subprocess
import sys
from unittest.mock import Mock, call

import pytest

import historical_context_formatter as formatter
import mrs_bot_quote_candidates as candidates
from tests.helpers.bot_runtime import bot


def forbidden(*args, **kwargs):
    pytest.fail("unexpected quotation candidate work")


def test_import_needs_no_runtime_access_or_research_import():
    code = """
import builtins, collections.abc, dataclasses, datetime, io, logging, os, random, re, socket, sys
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('candidate import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'historical_context_formatter', 'requests', 'openai'}:
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
before = random.getstate()
import mrs_bot_quote_candidates as candidates
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'historical_context_formatter' not in sys.modules
assert candidates.mm_dd_in_window('01-01', '12-01', '02-01')
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert bot.mm_dd_in_window is candidates.mm_dd_in_window
    assert bot.weighted_random_choice is candidates.weighted_random_choice
    assert candidates.random is bot.random is random


CANDIDATE_METHODS = {'any_window_matches_today': 'any_window',
 'quote_season_status': 'season_status',
 'quote_candidate_weight': 'weight',
 'current_quote_hashes_by_line': 'hashes_by_line',
 'build_quote_candidates': 'build',
 'load_quote_lines_and_analysis': 'load_source',
 'completed_research_quote_hashes': 'completed',
 'quote_candidates_for_current_cycle': 'for_cycle',
 'select_quote_candidate': 'select',
 'choose_unused_line_candidate': 'choose'}
CANDIDATE_INPUTS = {'season_date_specific_weight': 'QUOTE_SEASON_DATE_SPECIFIC_WEIGHT',
 'season_strong_weight': 'QUOTE_SEASON_STRONG_WEIGHT',
 'season_soft_weight': 'QUOTE_SEASON_SOFT_WEIGHT',
 'quality_weight_max_multiplier': 'QUOTE_QUALITY_WEIGHT_MAX_MULTIPLIER',
 'quote_text_hash': 'quote_text_hash',
 'metadata_for_hash': 'quote_metadata_for_hash',
 'log': 'log',
 'lines_file': 'LINES_FILE',
 'load_quote_analysis': 'load_quote_analysis',
 'validate_analysis': 'validate_quote_analysis_against_lines',
 'current_datetime': 'current_datetime',
 'research_dir': 'HISTORICAL_CONTEXT_RESEARCH_DIR',
 'eligible_manifest_file': 'RUNTIME_ELIGIBLE_QUOTE_MANIFEST_FILE',
 'research_file': 'COMPLETED_QUOTE_RESEARCH_FILE',
 'load_json_object': 'load_json_object',
 'file_sha256': 'file_sha256'}


def patch_candidates(monkeypatch, name, callback):
    monkeypatch.setattr(candidates.QuoteCandidates, name, lambda _owner, *args, **kwargs: callback(*args, **kwargs))


def test_candidate_owner_binds_current_inputs_without_reading(monkeypatch):
    from dataclasses import FrozenInstanceError

    previous = None
    for _ in range(2):
        current = {name: Mock(side_effect=AssertionError("construction read runtime inputs")) for name in CANDIDATE_INPUTS}
        for name, value in current.items():
            monkeypatch.setattr(bot, CANDIDATE_INPUTS[name], value)
        owner = bot._quote_candidates_owner()
        assert owner is not previous and vars(owner).keys() == current.keys()
        assert all(getattr(owner, name) is value for name, value in current.items())
        assert all(not value.called for value in current.values())
        with pytest.raises(FrozenInstanceError):
            owner.lines_file = "elsewhere"
        previous = owner


def test_candidate_adapters_preserve_signatures_references_and_errors(monkeypatch):
    for root_name, method in CANDIDATE_METHODS.items():
        adapter = getattr(bot, root_name)
        public = inspect.signature(adapter).parameters
        owned = inspect.signature(getattr(candidates.QuoteCandidates, method)).parameters
        assert list(public) == list(owned)[1:]
        assert [(p.kind, p.default) for p in public.values()] == [(p.kind, p.default) for p in list(owned.values())[1:]]
        args = tuple(object() for p in public.values() if p.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD)
        options = {key: object() for key, p in public.items() if p.kind == inspect.Parameter.KEYWORD_ONLY}
        result = object()
        callback = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch_candidates(patch, method, callback)
            assert adapter(*args, **options) is result
            actual_args, actual_kwargs = callback.call_args
            assert len(actual_args) == len(args)
            assert all(actual is expected for actual, expected in zip(actual_args, args))
            assert actual_kwargs.keys() == options.keys()
            assert all(actual_kwargs[key] is value for key, value in options.items())
            failure = KeyboardInterrupt(root_name)
            callback.side_effect = failure
            with pytest.raises(KeyboardInterrupt) as caught:
                adapter(*args, **options)
            assert caught.value is failure


def test_season_helpers_use_current_callbacks_settings_and_status_reference(monkeypatch):
    windows = [{"start_mm_dd": "12-01", "end_mm_dd": "02-01"}]
    matches = Mock(return_value=True)
    monkeypatch.setattr(candidates, "mm_dd_in_window", matches)
    assert bot.any_window_matches_today(windows, "01-01") is True
    matches.assert_called_once_with("01-01", "12-01", "02-01")

    any_match = Mock(return_value=True)
    patch_candidates(monkeypatch, "any_window", any_match)
    status = bot.quote_season_status({"seasonality": {"preferred_windows": windows}}, today_mm_dd="01-01")
    assert any_match.call_args.args[0] is windows
    status_callback = Mock(return_value=status)
    patch_candidates(monkeypatch, "season_status", status_callback)
    monkeypatch.setattr(bot, "QUOTE_SEASON_DATE_SPECIFIC_WEIGHT", 7.0)
    monkeypatch.setattr(bot, "QUOTE_SEASON_STRONG_WEIGHT", 5.0)
    monkeypatch.setattr(bot, "QUOTE_SEASON_SOFT_WEIGHT", 3.0)
    monkeypatch.setattr(bot, "QUOTE_QUALITY_WEIGHT_MAX_MULTIPLIER", 2.5)
    analysis = {"scores": dict.fromkeys(
        ("general_post_suitability", "standalone_clarity", "visual_matchability"), 100,
    )}
    for relevance, expected in (("date_specific", 17.5), ("strong", 12.5), ("soft", 7.5)):
        status["relevance"] = relevance
        weight, returned_status = bot.quote_candidate_weight(analysis, today_mm_dd="01-01")
        assert weight == expected and returned_status is status
        assert status_callback.call_args.args[0] is analysis
        assert status_callback.call_args.kwargs == {"today_mm_dd": "01-01"}
    status["hard_excluded"] = True
    assert bot.quote_candidate_weight(analysis, today_mm_dd="01-01") == (0.0, status)


def test_weighted_choice_preserves_rng_reference_boundaries_and_native_errors(monkeypatch):
    before = random.getstate()
    pool = [{"weight": 2.0}, {"weight": 3.0}]
    try:
        expected_rng = random.Random(0)
        random.seed(0)
        assert expected_rng.uniform(0.0, 5.0) > 2.0
        assert bot.weighted_random_choice(pool) is pool[1]
        assert random.getstate() == expected_rng.getstate()
        for fallback in ([{"weight": 0.0}, {"weight": 0.0}], [{"weight": -2.0}, {"weight": 1.0}]):
            expected = expected_rng.choice(fallback)
            assert bot.weighted_random_choice(fallback) is expected
            assert random.getstate() == expected_rng.getstate()

        uniform = Mock(return_value=2.0)
        monkeypatch.setattr(random, "uniform", uniform)
        assert bot.weighted_random_choice(pool) is pool[0]
        uniform.assert_called_once_with(0.0, 5.0)
        uniform.return_value = float("nan")
        assert bot.weighted_random_choice(pool) is pool[-1]
        uniform.reset_mock()
        with pytest.raises(IndexError) as native:
            random.choice([])
        with pytest.raises(IndexError) as empty:
            bot.weighted_random_choice([])
        assert empty.value.args == native.value.args
        with pytest.raises(ValueError, match="could not convert string to float"):
            bot.weighted_random_choice([{"weight": "invalid"}])
        with pytest.raises(AttributeError, match="has no attribute 'get'"):
            bot.weighted_random_choice([None])
        uniform.assert_not_called()
        assert random.getstate() == expected_rng.getstate()
    finally:
        random.setstate(before)


def test_source_hash_and_candidate_callbacks_preserve_order_counters_and_references(monkeypatch):
    lines = ["Duplicate \t\n", "\t\n", "Other\n", "Duplicate\n", "Excluded\n", "Unanalysed\n", "Seasonal\n"]
    hash_text = Mock(side_effect=lambda text: text.rstrip())
    monkeypatch.setattr(bot, "quote_text_hash", hash_text)
    assert bot.current_quote_hashes_by_line(lines) == {
        0: "Duplicate", 2: "Other", 3: "Duplicate", 4: "Excluded", 5: "Unanalysed", 6: "Seasonal",
    }
    assert hash_text.call_args_list == [call(lines[i]) for i in (0, 2, 3, 4, 5, 6)]
    hash_text.reset_mock()
    analysis = {name: {"name": name} for name in ("Duplicate", "Other", "Seasonal")}
    season_status = {}
    metadata = Mock(side_effect=lambda corpus, quote_hash, text: analysis.get(quote_hash))
    weight = Mock(side_effect=lambda item, **kwargs: (0 if item["name"] == "Seasonal" else 1, season_status))
    log = Mock()
    monkeypatch.setattr(bot, "quote_metadata_for_hash", metadata)
    patch_candidates(monkeypatch, "weight", weight)
    monkeypatch.setattr(bot, "log", log)
    corpus, excluded = {}, {"Excluded"}
    pool, hard_excluded, non_empty = bot.build_quote_candidates(
        lines, [3, 1, 4, 0, 5, 6, 2], corpus, "07-05", excluded_quote_hashes=excluded,
    )
    assert (hard_excluded, non_empty) == (1, 4)
    assert [item["line_no"] for item in pool] == [3, 2]
    assert [c.args[1] for c in metadata.call_args_list] == ["Duplicate", "Unanalysed", "Seasonal", "Other"]
    assert all(c.args[0] is corpus for c in metadata.call_args_list)
    assert [c.args[0] for c in hash_text.call_args_list] == ["Duplicate", "Excluded", "Duplicate", "Unanalysed", "Seasonal", "Other"]
    assert pool[0]["analysis"] is analysis["Duplicate"]
    assert pool[1]["analysis"] is analysis["Other"]
    assert all(item["season_status"] is season_status for item in pool)
    assert weight.call_args.args[0] is analysis["Other"]
    assert weight.call_args.kwargs == {"today_mm_dd": "07-05"}
    assert log.method_calls == [
        call.debug("Skipping empty line_no=%d", 1),
        call.debug("Skipping duplicate quote line_no=%d quote_hash=%s", 0, "Duplicate"),
        call.warning("Skipping unanalysed current quote line_no=%d quote_hash=%s until quote analysis is refreshed", 5, "Unanalysed"),
    ]
    assert excluded == {"Excluded"}


def test_source_load_keeps_default_encoding_references_and_error_order(tmp_path, monkeypatch):
    path = tmp_path / "quotes.txt"
    path.write_text("")
    open_source = Mock(wraps=open)
    monkeypatch.setattr(candidates, "open", open_source, raising=False)
    monkeypatch.setattr(bot, "LINES_FILE", path)
    monkeypatch.setattr(bot, "load_quote_analysis", forbidden)
    monkeypatch.setattr(bot, "validate_quote_analysis_against_lines", forbidden)
    monkeypatch.setattr(bot, "current_datetime", forbidden)
    with pytest.raises(RuntimeError) as exc:
        bot.load_quote_lines_and_analysis()
    assert exc.value.args == (f"No lines found in {path}",)
    open_source.assert_called_once_with(path)

    path.write_text("Quotation.\n")
    analysis, events = {}, []
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: events.append("load") or analysis)
    validate = Mock(side_effect=lambda *args: events.append("validate"))
    monkeypatch.setattr(bot, "validate_quote_analysis_against_lines", validate)
    monkeypatch.setattr(bot, "current_datetime", lambda: events.append("clock") or datetime(2026, 7, 5))
    lines, returned_analysis, today = bot.load_quote_lines_and_analysis()
    assert events == ["load", "validate", "clock"]
    assert lines == ["Quotation.\n"] and today == "07-05"
    assert returned_analysis is analysis
    assert validate.call_args.args[0] is analysis and validate.call_args.args[1] is lines
    failure = ValueError("analysis validation failed")
    validate.side_effect = failure
    events.clear()
    with pytest.raises(ValueError) as exc:
        bot.load_quote_lines_and_analysis()
    assert exc.value is failure and events == ["load"]


def test_research_loader_uses_current_callbacks_and_validates_core_before_manifest(monkeypatch):
    research, manifest_path = Path("current-research"), Path("current-manifest")
    failure = RuntimeError("canonical core invalid")
    core = Mock(side_effect=failure)
    monkeypatch.setattr(formatter, "load_and_validate_corpus_core", core)
    monkeypatch.setattr(bot, "HISTORICAL_CONTEXT_RESEARCH_DIR", research)
    monkeypatch.setattr(bot, "RUNTIME_ELIGIBLE_QUOTE_MANIFEST_FILE", manifest_path)
    monkeypatch.setattr(bot, "load_json_object", forbidden)
    monkeypatch.setattr(bot, "file_sha256", forbidden)
    monkeypatch.setattr(bot, "quote_text_hash", forbidden)
    with pytest.raises(RuntimeError) as exc:
        bot.load_completed_research_quote_hashes()
    assert exc.value is failure
    core.assert_called_once_with(research)
    core.side_effect, core.return_value = None, ({}, set())
    manifest = Mock(return_value=None)
    monkeypatch.setattr(bot, "load_json_object", manifest)
    with pytest.raises(RuntimeError) as exc:
        bot.load_completed_research_quote_hashes()
    assert exc.value.args == (
        f"Runtime eligible quotation manifest unavailable; refusing regular quote posting: {manifest_path}",
    )
    manifest.assert_called_once_with(manifest_path, label="runtime eligible quotation manifest")


def test_cycle_and_selection_keep_current_callbacks_set_and_candidate_identity(monkeypatch):
    events, lines, analysis = [], ["A", "R", "B"], {}

    class Used(set):
        def clear(self):
            events.append("clear")
            super().clear()

    used, excluded = Used({"a"}), {"b"}
    pool = [{"line_no": 0, "quote_hash": "a", "text": "A", "weight": 1.0}]
    patch_candidates(monkeypatch, "load_source", lambda: events.append("load") or (lines, analysis, "07-05"))
    hashes = Mock(side_effect=lambda source: events.append("hashes") or {2: "b", 0: "a", 1: "r"})
    patch_candidates(monkeypatch, "hashes_by_line", hashes)
    patch_candidates(monkeypatch, "completed", lambda: events.append("research") or {"a", "b"})

    def build(source, available, metadata, today, *, excluded_quote_hashes):
        events.append("build")
        assert source is lines and metadata is analysis and today == "07-05"
        assert available == [2, 0, 1] and used == set()
        assert excluded_quote_hashes == {"b", "r"} and excluded_quote_hashes is not excluded
        return pool, 0, 1

    patch_candidates(monkeypatch, "build", build)
    assert bot.quote_candidates_for_current_cycle(used, excluded_quote_hashes=excluded) is pool
    assert events == ["load", "hashes", "research", "clear", "build"]
    assert hashes.call_args.args[0] is lines and excluded == {"b"}

    current_pool = Mock(return_value=pool)
    log = Mock()
    monkeypatch.setattr(bot, "log", log)
    select = Mock(wraps=bot._quote_candidates_owner().select)
    weighted = Mock(return_value=pool[0])
    patch_candidates(monkeypatch, "for_cycle", current_pool)
    patch_candidates(monkeypatch, "select", select)
    monkeypatch.setattr(candidates, "weighted_random_choice", weighted)
    for exclusion in (None, set(), excluded):
        assert bot.choose_unused_line_candidate(used, excluded_quote_hashes=exclusion) is pool[0]
        assert current_pool.call_args.args[0] is used
        assert current_pool.call_args.kwargs["excluded_quote_hashes"] is exclusion
        assert select.call_args.args[0] is weighted.call_args.args[0] is pool
    assert bot.choose_unused_line_candidate(used) is pool[0]
    assert current_pool.call_args.kwargs == {"excluded_quote_hashes": None, "allow_cycle_reset": True}
    assert bot.choose_unused_line_candidate(used, allow_cycle_reset=False) is pool[0]
    assert current_pool.call_args.kwargs == {"excluded_quote_hashes": None, "allow_cycle_reset": False}
    log.info.assert_called_with(
        "Selected quote line_no=%d quote_hash=%s weight=%.2f seasonal_boost=%s", 0, "a", 1.0, False,
    )
    log.debug.assert_called_with("Selected quote text=%r", "A")
