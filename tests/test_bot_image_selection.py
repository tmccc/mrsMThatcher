from __future__ import annotations

from datetime import datetime
import json
import inspect
from pathlib import Path
import random
import subprocess
import sys
from unittest.mock import Mock, call

import pytest

import mrs_bot_image_selection as selection
from tests.helpers.bot_runtime import bot

def test_concise_components_keeps_root_alias_and_formatting():
    from mrs_bot_image_scoring import concise_components

    assert bot.concise_components is selection.concise_components is concise_components
    assert bot.concise_components({"z": 2.25, "a": -1.0}) == "a=-1.0, z=2.2"



def forbidden(*args, **kwargs):
    pytest.fail("unexpected image selection work")


def test_import_needs_no_runtime_access_and_keeps_shared_rng():
    code = """
import builtins, collections.abc, dataclasses, io, logging, os, random, socket, sys
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('selection import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai'}:
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
before = random.getstate()
import mrs_bot_image_selection
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert selection.random is bot.random is random


SELECTION_METHODS = {'available_currently_eligible_image_basenames': 'available_basenames',
 'log_regular_image_selection': 'log_choice',
 'choose_matched_unused_image': 'choose_matched'}
SELECTION_INPUTS = {'NoEligibleImageForQuote': 'NoEligibleImageForQuote',
 'log': 'log',
 'current_image_paths': 'current_image_paths',
 'load_image_analysis': 'load_image_analysis',
 'normalise_image_used_basenames': 'normalise_image_used_basenames',
 'save_image_used_basenames': 'save_image_used_basenames',
 'image_used_history_has_legacy_indices': 'image_used_history_has_legacy_indices',
 'current_datetime': 'current_datetime',
 'image_metadata_for_basename': 'image_metadata_for_basename',
 'score_image_for_quote': 'score_image_for_quote',
 'original_editorial_enabled': 'ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING',
 'original_editorial_shadow_result': 'original_editorial_shadow_result',
 'apply_original_editorial_selection': 'apply_original_editorial_selection',
 'log_original_editorial_shadow_result': 'log_original_editorial_shadow_result',
 'image_glob': 'IMAGE_GLOB',
 'images_used_file': 'IMAGES_USED_FILE',
 'UnsafeImageHistoryMigration': 'UnsafeImageHistoryMigration',
 'GlobalImageUnavailable': 'GlobalImageUnavailable',
 'StaleImageMetadata': 'StaleImageMetadata',
 'QuoteSpecificImageMismatch': 'QuoteSpecificImageMismatch'}


def patch_selection(monkeypatch, name, callback):
    monkeypatch.setattr(selection.ImageSelection, name, lambda _owner, *args, **kwargs: callback(*args, **kwargs))


def test_selection_owner_binds_current_inputs_without_reading(monkeypatch):
    from dataclasses import FrozenInstanceError

    previous = None
    for _ in range(2):
        current = {name: Mock(side_effect=AssertionError("construction read runtime inputs")) for name in SELECTION_INPUTS}
        for name, value in current.items():
            monkeypatch.setattr(bot, SELECTION_INPUTS[name], value)
        owner = bot._image_selection_owner()
        assert owner is not previous and vars(owner).keys() == current.keys()
        assert all(getattr(owner, name) is value for name, value in current.items())
        assert all(not value.called for value in current.values())
        with pytest.raises(FrozenInstanceError):
            owner.image_glob = "elsewhere"
        previous = owner


def test_selection_adapters_preserve_signatures_references_and_errors(monkeypatch):
    for root_name, method in SELECTION_METHODS.items():
        adapter = getattr(bot, root_name)
        public = inspect.signature(adapter).parameters
        owned = inspect.signature(getattr(selection.ImageSelection, method)).parameters
        assert list(public) == list(owned)[1:]
        assert [(p.kind, p.default) for p in public.values()] == [(p.kind, p.default) for p in list(owned.values())[1:]]
        args = tuple(object() for p in public.values() if p.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD)
        options = {key: object() for key, p in public.items() if p.kind == inspect.Parameter.KEYWORD_ONLY}
        result = object()
        callback = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch_selection(patch, method, callback)
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


def test_eligible_cycle_keeps_unrelated_history_and_current_exception_and_logger(monkeypatch):
    class CurrentUnavailable(ValueError):
        pass

    log = Mock()
    monkeypatch.setattr(bot, "log", log)
    monkeypatch.setattr(bot, "NoEligibleImageForQuote", CurrentUnavailable)
    used = {"a", "b", "seasonal"}
    eligible = {"a", "b"}
    assert bot.available_currently_eligible_image_basenames(eligible, used, {"last_regular_image_filename": "a"}) == (["b"], True)
    assert used == {"seasonal"} and eligible == {"a", "b"}
    assert log.method_calls == [
        call.info("All currently eligible regular-post images used; resetting eligible image cycle"),
        call.info("Temporarily excluded last regular image at eligible-cycle boundary: %s", "a"),
    ]
    with pytest.raises(CurrentUnavailable) as exc:
        bot.available_currently_eligible_image_basenames(set(), used)
    assert exc.value.args == ("No currently eligible regular-post images are available",)
    assert used == {"seasonal"}


@pytest.mark.parametrize("save_fails", [False, True])
def test_legacy_normalization_mutates_and_saves_before_remaining_index_check(monkeypatch, save_fails):
    class CurrentUnsafe(ValueError):
        pass

    paths, used, normalized = ["b.jpg", "a.jpg"], {0, 1}, {"b.jpg", 1}
    save_path, events = object(), []
    failure = OSError("save failed")
    monkeypatch.setattr(bot, "current_image_paths", lambda: paths)
    monkeypatch.setattr(bot, "load_image_analysis", lambda: None)
    normalize = Mock(return_value=(normalized, True))
    monkeypatch.setattr(bot, "normalise_image_used_basenames", normalize)
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", save_path)
    monkeypatch.setattr(bot, "UnsafeImageHistoryMigration", CurrentUnsafe)
    monkeypatch.setattr(bot, "current_datetime", forbidden)

    def save(path, names):
        assert path is save_path and names is normalized
        assert used == normalized and used is not normalized
        events.append("save")
        if save_fails:
            raise failure

    def legacy(names):
        assert names is used
        events.append("legacy")
        return True

    monkeypatch.setattr(bot, "save_image_used_basenames", save)
    monkeypatch.setattr(bot, "image_used_history_has_legacy_indices", legacy)
    with pytest.raises(OSError if save_fails else CurrentUnsafe) as exc:
        bot.choose_matched_unused_image(used, {}, {})
    if save_fails:
        assert exc.value is failure and events == ["save"]
    else:
        assert events == ["save", "legacy"]
        assert exc.value.args == ("Image used-history still contains legacy integer entries; refusing regular image posting until full analysed corpus is visible",)
    assert normalize.call_args.args[0] is used
    assert normalize.call_args.args[1] is paths
    assert normalize.call_args.args[2] is None
    assert used == normalized


def test_selector_preserves_original_scores_editorial_callbacks_and_one_random_draw(monkeypatch):
    paths = ["z.jpg", "b.jpg", "g.jpg", "a.jpg", "d.jpg", "c.jpg"]
    corpus = {name: {"name": name} for name in paths}
    components = {name: {"base": score} for score, name in enumerate(sorted(paths), 1)}
    quote = {"quote_hash": "ABC", "analysis": {"quote": True}}
    idf, used, state, events, captured = {}, set(), {}, [], {}
    log = Mock()
    monkeypatch.setattr(bot, "log", log)
    monkeypatch.setattr(bot, "current_image_paths", lambda: paths)
    monkeypatch.setattr(bot, "load_image_analysis", lambda: corpus)
    monkeypatch.setattr(bot, "normalise_image_used_basenames", lambda *args: (used, False))
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 9, 6))
    build_idf = Mock(return_value=idf)
    metadata = Mock(side_effect=lambda data, name, path: ("hash:" + name, data[name]))
    seasonal = Mock(return_value=False)
    monkeypatch.setattr(selection, "build_image_topic_idf", build_idf)
    monkeypatch.setattr(bot, "image_metadata_for_basename", metadata)
    monkeypatch.setattr(selection, "image_is_out_of_season", seasonal)
    monkeypatch.setattr(bot, "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING", False)
    monkeypatch.setattr(bot, "original_editorial_shadow_result", forbidden)

    def score(quote_analysis, analysis, weights):
        assert quote_analysis is quote["analysis"] and weights is idf
        detail = components[analysis["name"]]
        return float(detail["base"]), detail, True

    def editorial(current_quote, baseline, candidates, **kwargs):
        events.append("editorial")
        assert current_quote is quote and baseline["basename"] == "z.jpg"
        assert kwargs == {"selection_phase": "forced_cycle_reset", "comparison": None}
        captured.update(scored=candidates, baseline=baseline)
        return candidates[0]

    def editorial_shadow(current_quote, baseline, scored, **kwargs):
        events.append("editorial_shadow")
        assert current_quote is quote and baseline is captured["baseline"]
        assert scored is captured["scored"] and kwargs == {"selection_phase": "forced_cycle_reset", "comparison": None}

    regular = Mock(side_effect=lambda choice: events.append("regular"))
    concise = Mock(side_effect=lambda detail: str(detail["base"]))
    monkeypatch.setattr(bot, "score_image_for_quote", score)
    monkeypatch.setattr(bot, "apply_original_editorial_selection", editorial)
    patch_selection(monkeypatch, "log_choice", regular)
    monkeypatch.setattr(bot, "log_original_editorial_shadow_result", editorial_shadow)
    monkeypatch.setattr(selection, "concise_components", concise)
    before, choose = random.getstate(), random.choice
    monkeypatch.setattr(random, "choice", lambda tied: events.append("rng_choice") or choose(tied))
    try:
        random.seed(0)
        chosen = bot.choose_matched_unused_image(used, quote, state, selection_phase="forced_cycle_reset")
        expected = random.Random(0)
        expected.choice([0])
        assert random.getstate() == expected.getstate()
    finally:
        random.setstate(before)
    assert events == ["rng_choice", "editorial", "regular", "editorial_shadow"]
    assert chosen is captured["scored"][0]
    assert regular.call_args.args[0] is chosen
    assert build_idf.call_args.args[0] is corpus
    assert [entry.args[1] for entry in metadata.call_args_list] == paths + sorted(paths)
    assert all(entry.args[0] is corpus and entry.args[1] == entry.args[2] for entry in metadata.call_args_list)
    assert all(entry.args[0] is corpus[name] and entry.args[1] == "09-06" for entry, name in zip(seasonal.call_args_list, paths))
    for item in captured["scored"]:
        name = item["basename"]
        assert item["image_no"] == paths.index(name)
        assert item["components"] is components[name]
        assert item["score"] == components[name]["base"]
        assert item["image_source"] == "original"
        assert item["origin_quote_hash"] is None and item["origin_quote_boost"] == 0.0
    assert [entry.args[1] for entry in log.debug.call_args_list[1:]] == ["z.jpg", "g.jpg", "d.jpg", "c.jpg", "b.jpg"]
    assert concise.call_args_list[0].args[0] is chosen["components"]
    assert used == set() and state == {}


@pytest.mark.parametrize("enabled, metadata_present", [(False, True), (True, False), (True, True)])
def test_selector_reuses_comparison_and_preserves_duplicate_path_numbers(monkeypatch, enabled, metadata_present):
    paths = ["first/a.jpg", "other/b.jpg", "last/a.jpg", "last/a.jpg"]
    corpus = {"a.jpg": {"score": 10.0}, "b.jpg": {"score": 9.0}}
    baseline_components = {name: {"base": value["score"]} for name, value in corpus.items()}
    metadata = Mock(side_effect=lambda data, name, path: (name, data[name]))
    comparison = Mock(wraps=bot.original_editorial_shadow_result)
    editorial_metadata = {name: {"adjustment": adjustment} for name, adjustment in (("a.jpg", 0.0), ("b.jpg", 2.0))}
    loader = Mock(return_value=editorial_metadata if metadata_present else {})
    scorer = Mock(side_effect=lambda quote, row: (row["adjustment"], {}))
    log = Mock()
    monkeypatch.setattr(bot, "log", log)
    monkeypatch.setattr(bot, "current_image_paths", lambda: paths)
    monkeypatch.setattr(bot, "load_image_analysis", lambda: corpus)
    monkeypatch.setattr(bot, "normalise_image_used_basenames", lambda *args: (set(), False))
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 9, 17))
    monkeypatch.setattr(selection, "build_image_topic_idf", lambda data: {})
    monkeypatch.setattr(bot, "image_metadata_for_basename", metadata)
    monkeypatch.setattr(selection, "image_is_out_of_season", lambda *args: False)
    monkeypatch.setattr(bot, "score_image_for_quote", lambda quote, analysis, idf: (
        analysis["score"], baseline_components["a.jpg" if analysis is corpus["a.jpg"] else "b.jpg"], True,
    ))
    monkeypatch.setattr(bot, "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING", enabled)
    monkeypatch.setattr(bot, "original_editorial_shadow_result", comparison)
    monkeypatch.setattr(bot._original_editorial.OriginalEditorial, "load", lambda _owner: loader())
    monkeypatch.setattr(bot._original_editorial.OriginalEditorial, "score", lambda _owner, *args, **kwargs: scorer(*args, **kwargs))

    chosen = bot.choose_matched_unused_image(set(), {"quote_hash": "fixture", "analysis": {}}, {})

    assert comparison.call_count == loader.call_count == int(enabled)
    assert scorer.call_count == (2 if enabled and metadata_present else 0)
    assert metadata.call_args_list == [
        call(corpus, "a.jpg", "last/a.jpg"), call(corpus, "b.jpg", "other/b.jpg"),
    ] * 2
    if enabled:
        candidates = comparison.call_args.args[2]
        assert [(item["basename"], item["image_no"]) for item in candidates] == [("a.jpg", 2), ("b.jpg", 1)]
        assert [item["score"] for item in candidates] == [10.0, 9.0]
    assert baseline_components == {"a.jpg": {"base": 10.0}, "b.jpg": {"base": 9.0}}
    events = [entry.args for entry in log.info.call_args_list]
    if enabled and metadata_present:
        assert (chosen["basename"], chosen["image_no"], chosen["score"]) == ("b.jpg", 1, 11.0)
        assert [args[0].split()[0] for args in events[-4:]] == [
            "ORIGINAL_EDITORIAL_SELECTION_RESULT", "Selected", "REGULAR_IMAGE_SELECTED", "ORIGINAL_EDITORIAL_SHADOW_RESULT",
        ]
        selected_payload = json.loads(events[-4][1])
        shadow_payload = json.loads(events[-1][1])
        assert selected_payload == {**shadow_payload, "selection_applied": True, "selected_winner": "b.jpg"}
        assert "selection_applied" not in shadow_payload and "selected_winner" not in shadow_payload
    else:
        assert (chosen["path"], chosen["image_no"], chosen["score"]) == ("last/a.jpg", 2, 10.0)
        assert not any(args[0].startswith("ORIGINAL_EDITORIAL_") for args in events)


@pytest.mark.parametrize("stale_on_recheck", [False, True])
def test_metadata_rechecks_keep_current_stale_and_exhaustion_exception_boundaries(monkeypatch, stale_on_recheck):
    class CurrentStale(ValueError):
        pass

    class CurrentGlobal(ValueError):
        pass

    class CurrentMismatch(ValueError):
        pass

    corpus, analysis, used = {}, {}, {"a.jpg", "unavailable.jpg"}
    monkeypatch.setattr(bot, "current_image_paths", lambda: ["a.jpg"])
    monkeypatch.setattr(bot, "load_image_analysis", lambda: corpus)
    monkeypatch.setattr(bot, "normalise_image_used_basenames", lambda *args: (used, False))
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 9, 6))
    monkeypatch.setattr(selection, "build_image_topic_idf", lambda data: {})
    monkeypatch.setattr(selection, "image_is_out_of_season", lambda *args: False)
    monkeypatch.setattr(bot, "score_image_for_quote", forbidden)
    monkeypatch.setattr(bot, "StaleImageMetadata", CurrentStale)
    monkeypatch.setattr(bot, "GlobalImageUnavailable", CurrentGlobal)
    monkeypatch.setattr(bot, "QuoteSpecificImageMismatch", CurrentMismatch)
    metadata = Mock(side_effect=([("hash", analysis)] if stale_on_recheck else []) + [CurrentStale("changed")])
    monkeypatch.setattr(bot, "image_metadata_for_basename", metadata)
    with pytest.raises(CurrentMismatch if stale_on_recheck else CurrentGlobal) as exc:
        bot.choose_matched_unused_image(used, {}, {})
    assert exc.value.args == (("No metadata-eligible regular-post images matched the selected quote" if stale_on_recheck else "No analysed currently eligible regular-post images are available"),)
    assert metadata.call_args_list == [call(corpus, "a.jpg", "a.jpg")] * (2 if stale_on_recheck else 1)
    assert used == ({"unavailable.jpg"} if stale_on_recheck else {"a.jpg", "unavailable.jpg"})


def test_pair_retries_keep_current_exceptions_limit_exclusions_and_one_time_reset(monkeypatch):
    class CurrentMismatch(ValueError):
        pass

    class CurrentExhausted(ValueError):
        pass

    lines, images, state, excluded = set(), set(), {}, {"reserved"}
    seen, boundaries = [], []
    quotes = [{"quote_hash": "first", "line_no": 1}, {"quote_hash": "second", "line_no": 2}]
    monkeypatch.setattr(bot, "QuoteSpecificImageMismatch", CurrentMismatch)
    monkeypatch.setattr(bot, "NoViableQuoteImagePair", CurrentExhausted)
    monkeypatch.setattr(bot, "MAX_QUOTE_IMAGE_PAIR_ATTEMPTS", 2)

    def quote(used, *, excluded_quote_hashes, allow_cycle_reset):
        assert allow_cycle_reset is (len(seen) == 0)
        assert used is lines and excluded_quote_hashes is not excluded
        seen.append(set(excluded_quote_hashes))
        return quotes[len(seen) - 1]

    def image(used, selected_quote, current_state, **kwargs):
        assert used is images and current_state is state and selected_quote is quotes[len(seen) - 1]
        boundary = kwargs.pop("cycle_boundary_exclusions")
        boundaries.append(boundary)
        assert kwargs == {"force_cycle_reset": len(seen) == 1, "avoid_last_image_at_cycle_boundary": True, "selection_phase": "forced_cycle_reset"}
        if len(seen) == 1:
            boundary.update({"z.jpg", "a.jpg"})
        else:
            assert boundary is boundaries[0] and boundary == {"z.jpg", "a.jpg"}
        raise CurrentMismatch("no pair")

    monkeypatch.setattr(bot, "choose_unused_line_candidate", quote)
    patch_selection(monkeypatch, "choose_matched", image)
    with pytest.raises(CurrentExhausted) as exc:
        bot.choose_regular_quote_image_pair(lines, images, state, force_image_cycle_reset=True, excluded_quote_hashes=excluded)
    assert exc.value.args == ("No eligible regular quote/image pair found after 2 attempt(s); used histories unchanged", 2, "a.jpg")
    assert seen == [{"reserved"}, {"reserved", "first"}] and excluded == {"reserved"}
    failure = RuntimeError("ordinary exclusions exhausted")
    monkeypatch.setattr(bot, "choose_unused_line_candidate", Mock(side_effect=failure))
    with pytest.raises(RuntimeError) as exc:
        bot.choose_regular_quote_image_pair(lines, images, state, excluded_quote_hashes=excluded)
    assert exc.value is failure


def test_pair_binds_matched_image_policy_after_each_quote_selection(monkeypatch):
    quotes = [{"quote_hash": "first"}, {"quote_hash": "second"}]
    policies = [object(), object()]
    selected, observed = [], []
    result = {"basename": "chosen.jpg"}
    lines, images, state = set(), set(), {}
    monkeypatch.setattr(bot, "MAX_QUOTE_IMAGE_PAIR_ATTEMPTS", 2)

    def quote(*args, **kwargs):
        index = len(selected)
        monkeypatch.setattr(bot, "IMAGE_GLOB", f"attempt-{index}")
        monkeypatch.setattr(bot, "score_image_for_quote", policies[index])
        selected.append(quotes[index])
        return quotes[index]

    def matched(owner, used, chosen_quote, current_state, **kwargs):
        assert used is images and current_state is state
        index = len(observed)
        observed.append((owner.image_glob, owner.score_image_for_quote))
        assert chosen_quote is quotes[index]
        if index == 0:
            raise bot.QuoteSpecificImageMismatch("retry another quotation")
        return result

    monkeypatch.setattr(bot, "choose_unused_line_candidate", quote)
    monkeypatch.setattr(selection.ImageSelection, "choose_matched", matched)
    chosen_quote, chosen_image, attempts = bot.choose_regular_quote_image_pair(lines, images, state)
    assert chosen_quote is quotes[1] and chosen_image is result and attempts == 2
    assert observed == [("attempt-0", policies[0]), ("attempt-1", policies[1])]
