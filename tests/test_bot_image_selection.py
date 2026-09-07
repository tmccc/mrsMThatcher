from __future__ import annotations

from datetime import datetime
from pathlib import Path
import random
import subprocess
import sys
from unittest.mock import Mock, call

import pytest

import mrs_bot_image_selection as selection
from tests.test_unit_helpers import bot


def forbidden(*args, **kwargs):
    pytest.fail("unexpected image selection work")


def test_import_needs_no_runtime_access_and_keeps_shared_rng():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys
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


def test_spacing_uses_current_callbacks_and_keeps_default_failure_and_set_identity(monkeypatch):
    state = {"original_regular_posts_since_generated_image": 2}
    required = Mock(return_value=3)
    monkeypatch.setattr(bot, "generated_image_spacing_required", required)
    assert bot.original_posts_since_generated_image(state) == 2
    required.assert_called_once_with()  # The default is evaluated even with a counter.
    failure = ValueError("current spacing invalid")
    required.side_effect = failure
    assert bot.original_posts_since_generated_image(state) == 0
    with pytest.raises(ValueError) as exc:
        bot.original_posts_since_generated_image({})
    assert exc.value is failure

    required.side_effect = None
    required.return_value = 0
    count = Mock(return_value=2)
    monkeypatch.setattr(bot, "original_posts_since_generated_image", count)
    assert bot.generated_images_allowed_by_spacing(state) is True
    count.assert_not_called()
    required.return_value = 3
    assert bot.generated_images_allowed_by_spacing(state) is False
    assert count.call_args.args[0] is state

    pool = {"custom-generated", "original"}
    allowed = Mock(return_value=True)
    origin = Mock(side_effect=lambda name: "abc" if name == "custom-generated" else None)
    monkeypatch.setattr(bot, "generated_images_allowed_by_spacing", allowed)
    monkeypatch.setattr(bot, "generated_image_origin_quote_hash", origin)
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", False)
    assert bot.filter_generated_images_by_spacing(pool, state) is pool
    allowed.assert_not_called()
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    assert bot.filter_generated_images_by_spacing(pool, state) is pool
    origin.assert_not_called()
    allowed.return_value = False
    assert bot.filter_generated_images_by_spacing(pool, state) == {"original"}
    assert allowed.call_args.args[0] is state
    assert pool == {"custom-generated", "original"}
    assert bot.image_selection_observability("custom-generated", "ABC", 2)["origin_quote_boost"] == 2.0
    assert bot.image_selection_observability("custom-generated", "other", object())["origin_quote_boost"] == 0.0


def test_spacing_log_and_counter_callbacks_keep_order_and_mutated_state_reference(monkeypatch):
    calls = Mock()
    calls.required.return_value = 3
    calls.count.return_value = 1
    calls.allowed.return_value = False
    calls.origin.return_value = None
    for name, callback in (
        ("generated_image_spacing_required", calls.required),
        ("original_posts_since_generated_image", calls.count),
        ("generated_images_allowed_by_spacing", calls.allowed),
        ("generated_image_origin_quote_hash", calls.origin),
        ("log", calls.log),
    ):
        monkeypatch.setattr(bot, name, callback)
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    state = {"original_regular_posts_since_generated_image": 1}
    assert bot.log_generated_image_spacing_status(state) is False
    assert calls.mock_calls == [
        call.required(), call.count(state), call.allowed(state),
        call.log.info("GENERATED_IMAGE_SPACING_STATUS pool_enabled=%s allowed=%s original_posts_since_generated=%d required=%d", "true", "false", 1, 3),
        call.log.info("GENERATED_IMAGE_POOL_BLOCKED_BY_SPACING original_posts_since_generated=%d required=%d", 1, 3),
    ]
    calls.reset_mock()
    bot.log_generated_image_spacing_state_updated(state, "original")
    assert calls.mock_calls == [
        call.required(), call.count(state), call.allowed(state), call.origin("original"),
        call.log.info("GENERATED_IMAGE_SPACING_STATE_UPDATED pool_enabled=%s allowed=%s original_posts_since_generated=%d required=%d image_source=%s image=%s", "true", "false", 1, 3, "original", "original"),
    ]
    calls.reset_mock()
    monkeypatch.setattr(bot, "log_generated_image_spacing_state_updated", calls.updated)
    bot.update_regular_generated_image_spacing_state(state, "original")
    assert state["original_regular_posts_since_generated_image"] == 2
    assert calls.mock_calls == [call.required(), call.origin("original"), call.count(state), call.updated(state, "original")]
    assert calls.updated.call_args.args[0] is state
    calls.reset_mock()
    assert bot.regular_generated_image_spacing_already_reflected({}, "generated") is False
    assert calls.mock_calls == []
    calls.origin.return_value = "abc"
    calls.count.return_value = 0
    assert bot.regular_generated_image_spacing_already_reflected(state, "generated") is True
    assert calls.mock_calls == [call.origin("generated"), call.count(state)]
    calls.reset_mock()
    bot.log_regular_image_selection({"basename": "original", "score": 2})
    assert calls.mock_calls == [
        call.log.info("REGULAR_IMAGE_SELECTED source=%s basename=%s score=%s origin_quote_hash=%s origin_quote_match=%s origin_quote_boost=%s", "original", "original", 2, "", "false", 0),
    ]


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


def test_selector_preserves_catalog_components_and_policy_callback_references(monkeypatch):
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
    monkeypatch.setattr(bot, "build_image_topic_idf", build_idf)
    monkeypatch.setattr(bot, "image_metadata_for_basename", metadata)
    monkeypatch.setattr(bot, "image_is_out_of_season", seasonal)
    monkeypatch.setattr(bot, "generated_image_origin_quote_hash", lambda name: "abc" if name == "g.jpg" else None)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST", 2)

    def score(quote_analysis, analysis, weights):
        assert quote_analysis is quote["analysis"] and weights is idf
        detail = components[analysis["name"]]
        return float(detail["base"]), detail, True

    def policy(scored):
        events.append("policy")
        captured["scored"] = scored
        candidates = [dict(scored[1], score=10.0), dict(scored[-1], score=10.0)]
        captured["candidates"] = candidates
        captured["rows"] = []
        return captured["rows"], candidates

    def editorial(current_quote, baseline, candidates, **kwargs):
        events.append("editorial")
        assert current_quote is quote and candidates is captured["candidates"]
        assert baseline is candidates[1] and kwargs == {"selection_phase": "forced_cycle_reset"}
        return candidates[0]

    def editorial_shadow(current_quote, baseline, scored, **kwargs):
        events.append("editorial_shadow")
        assert current_quote is quote and baseline is captured["candidates"][1]
        assert scored is captured["scored"] and kwargs == {"selection_phase": "forced_cycle_reset"}

    def applied(current_quote, baseline, scored, rows, ties, **kwargs):
        events.append("applied")
        assert current_quote is quote and baseline is captured["candidates"][1]
        assert scored is captured["scored"] and rows is captured["rows"] and ties == 2
        assert kwargs == {"selection_phase": "forced_cycle_reset", "selection_rng_state": random.Random(0).getstate()}
        return captured

    regular = Mock(side_effect=lambda choice: events.append("regular"))
    applied_log = Mock(side_effect=lambda payload: events.append("applied_log"))
    concise = Mock(side_effect=lambda detail: str(detail["base"]))
    monkeypatch.setattr(bot, "score_image_for_quote", score)
    monkeypatch.setattr(bot, "generated_identity_policy_scoring_active", lambda: events.append("active") or True)
    monkeypatch.setattr(bot, "generated_identity_policy_selection", policy)
    monkeypatch.setattr(bot, "apply_original_editorial_selection", editorial)
    monkeypatch.setattr(bot, "log_regular_image_selection", regular)
    monkeypatch.setattr(bot, "log_original_editorial_shadow_result", editorial_shadow)
    monkeypatch.setattr(bot, "generated_identity_policy_applied_result", applied)
    monkeypatch.setattr(bot, "log_generated_identity_policy_applied_result", applied_log)
    monkeypatch.setattr(bot, "log_generated_identity_policy_shadow_result", forbidden)
    monkeypatch.setattr(bot, "concise_components", concise)
    before, getstate, choose = random.getstate(), random.getstate, random.choice
    monkeypatch.setattr(random, "getstate", lambda: events.append("rng_state") or getstate())
    monkeypatch.setattr(random, "choice", lambda tied: events.append("rng_choice") or choose(tied))
    try:
        random.seed(0)
        chosen = bot.choose_matched_unused_image(used, quote, state, generated_images_allowed=True, selection_phase="forced_cycle_reset")
        expected = random.Random(0)
        expected.choice([0, 1])
        assert getstate() == expected.getstate()
    finally:
        random.setstate(before)
    assert events == ["active", "policy", "rng_state", "rng_choice", "editorial", "regular", "editorial_shadow", "active", "applied", "applied_log"]
    assert chosen is captured["candidates"][0]
    assert regular.call_args.args[0] is chosen and applied_log.call_args.args[0] is captured
    assert build_idf.call_args.args[0] is corpus
    assert [entry.args[1] for entry in metadata.call_args_list] == paths + sorted(paths)
    assert all(entry.args[0] is corpus and entry.args[1] == entry.args[2] for entry in metadata.call_args_list)
    assert all(entry.args[0] is corpus[name] and entry.args[1] == "09-06" for entry, name in zip(seasonal.call_args_list, paths))
    for item in captured["scored"]:
        name = item["basename"]
        assert item["image_no"] == paths.index(name)
        if name == "g.jpg":
            assert item["components"] == {"base": 5, "generated_origin_quote": 2.0}
            assert item["components"] is not components[name] and components[name] == {"base": 5}
        else:
            assert item["components"] is components[name]
    assert [entry.args[1] for entry in log.debug.call_args_list[1:]] == ["g.jpg", "z.jpg", "d.jpg", "c.jpg", "b.jpg"]
    assert concise.call_args_list[0].args[0] is chosen["components"]
    assert used == set() and state == {}


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
    monkeypatch.setattr(bot, "build_image_topic_idf", lambda data: {})
    monkeypatch.setattr(bot, "image_is_out_of_season", lambda *args: False)
    monkeypatch.setattr(bot, "score_image_for_quote", forbidden)
    monkeypatch.setattr(bot, "StaleImageMetadata", CurrentStale)
    monkeypatch.setattr(bot, "GlobalImageUnavailable", CurrentGlobal)
    monkeypatch.setattr(bot, "QuoteSpecificImageMismatch", CurrentMismatch)
    metadata = Mock(side_effect=([("hash", analysis)] if stale_on_recheck else []) + [CurrentStale("changed")])
    monkeypatch.setattr(bot, "image_metadata_for_basename", metadata)
    with pytest.raises(CurrentMismatch if stale_on_recheck else CurrentGlobal) as exc:
        bot.choose_matched_unused_image(used, {}, {}, generated_images_allowed=True)
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
    spacing = Mock(return_value=False)
    monkeypatch.setattr(bot, "log_generated_image_spacing_status", spacing)

    def quote(used, *, excluded_quote_hashes, allow_cycle_reset):
        assert allow_cycle_reset is (len(seen) == 0)
        assert used is lines and excluded_quote_hashes is not excluded
        seen.append(set(excluded_quote_hashes))
        return quotes[len(seen) - 1]

    def image(used, selected_quote, current_state, **kwargs):
        assert used is images and current_state is state and selected_quote is quotes[len(seen) - 1]
        boundary = kwargs.pop("cycle_boundary_exclusions")
        boundaries.append(boundary)
        assert kwargs == {"force_cycle_reset": len(seen) == 1, "avoid_last_image_at_cycle_boundary": True, "generated_images_allowed": False, "selection_phase": "forced_cycle_reset"}
        if len(seen) == 1:
            boundary.update({"z.jpg", "a.jpg"})
        else:
            assert boundary is boundaries[0] and boundary == {"z.jpg", "a.jpg"}
        raise CurrentMismatch("no pair")

    monkeypatch.setattr(bot, "choose_unused_line_candidate", quote)
    monkeypatch.setattr(bot, "choose_matched_unused_image", image)
    with pytest.raises(CurrentExhausted) as exc:
        bot.choose_regular_quote_image_pair(lines, images, state, force_image_cycle_reset=True, excluded_quote_hashes=excluded)
    assert exc.value.args == ("No eligible regular quote/image pair found after 2 attempt(s); used histories unchanged", 2, "a.jpg")
    assert seen == [{"reserved"}, {"reserved", "first"}] and excluded == {"reserved"}
    assert spacing.call_args.args[0] is state
    failure = RuntimeError("ordinary exclusions exhausted")
    monkeypatch.setattr(bot, "choose_unused_line_candidate", Mock(side_effect=failure))
    with pytest.raises(RuntimeError) as exc:
        bot.choose_regular_quote_image_pair(lines, images, state, excluded_quote_hashes=excluded)
    assert exc.value is failure


def test_fixed_quote_global_failure_preserves_mutation_and_original_exception(monkeypatch):
    used, quote, state = {"old.jpg"}, {"quote_hash": "fixed"}, {}
    failure = bot.GlobalImageUnavailable("missing metadata")
    log = Mock()
    monkeypatch.setattr(bot, "log", log)
    monkeypatch.setattr(bot, "log_generated_image_spacing_status", lambda current: False)

    def choose(names, current_quote, current_state, **kwargs):
        assert names is used and current_quote is quote and current_state is state
        assert kwargs == {"force_cycle_reset": False, "avoid_last_image_at_cycle_boundary": True, "cycle_boundary_exclusions": None, "generated_images_allowed": False, "selection_phase": "normal"}
        names.clear()
        names.add("normalized.jpg")
        raise failure

    monkeypatch.setattr(bot, "choose_matched_unused_image", choose)
    with pytest.raises(bot.GlobalImageUnavailable) as exc:
        bot.choose_engagement_question_image(used, quote, state)
    assert exc.value is failure and used == {"normalized.jpg"}
    log.warning.assert_not_called()
