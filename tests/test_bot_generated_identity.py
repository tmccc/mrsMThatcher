from __future__ import annotations

import copy
import json
import random
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import mrs_bot_generated_identity as identity
from tests.test_generated_identity_policy_shadow_scoring import (
    candidate, quote, valid_identity_analysis,
)
from tests.test_unit_helpers import bot


def forbidden(*args, **kwargs):
    pytest.fail("unexpected generated identity work")


class NoFloat:
    def __float__(self):
        forbidden()


def test_identity_import_needs_no_bot_environment_files_or_network():
    code = """
import builtins
import collections.abc
import io
import json
import logging
import math
import os
from pathlib import Path
import random
import socket
import sys

def forbidden(*args, **kwargs):
    raise AssertionError('identity import attempted runtime access')

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
before = random.getstate()
import mrs_bot_generated_identity as identity
assert 'mrsMThatcher2' not in sys.modules
assert identity.generated_identity_numeric(5.5, key='strength') == 5.5
assert random.getstate() == before
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert bot.generated_identity_numeric is identity.generated_identity_numeric
    assert bot._choice_with_random_state is identity._choice_with_random_state
    for value in (True, "5.5", NoFloat()):
        with pytest.raises(ValueError, match="strength must be a number"):
            bot.generated_identity_numeric(value, key="strength")


def test_loader_cache_hit_preserves_expanded_key_and_current_cache_before_io(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cached = {}
    cache = {str(tmp_path / "audit.json"): cached}
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_AUDIT_FILE", "~/audit.json")
    monkeypatch.setattr(bot, "_GENERATED_IDENTITY_AUDIT_CACHE", cache)
    monkeypatch.setattr(bot, "configured_generated_image_paths", forbidden)
    monkeypatch.setattr(bot, "validate_generated_identity_audit_item", forbidden)
    monkeypatch.setattr(identity, "open", forbidden, raising=False)
    assert bot.load_generated_identity_audit() is cached
    assert bot._GENERATED_IDENTITY_AUDIT_CACHE is cache
    path = tmp_path / "missing" / ".." / "audit.json"
    replacement = {str(path): {"new": {}}}
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_AUDIT_FILE", path)
    monkeypatch.setattr(bot, "_GENERATED_IDENTITY_AUDIT_CACHE", replacement)
    assert bot.load_generated_identity_audit() is replacement[str(path)]


def test_loader_uses_current_schema_sets_callbacks_and_cache_without_failure_insertion(tmp_path, monkeypatch):
    path, image = tmp_path / "audit.json", tmp_path / "tg_synthetic.png"
    pool, cache, calls, validated = {image.name: image}, {}, [], []
    policies, dependence = {"custom"}, {"custom"}
    data = {"analysis_kind": "synthetic", "schema_version": 3, "items": {
        image.name: {"image_sha256": "fresh", "origin_quote_hash": "origin",
                     "analysis": valid_identity_analysis("custom", identity_dependence="custom")},
    }}
    path.write_text(json.dumps(data))
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_AUDIT_FILE", path)
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_AUDIT_KIND", "synthetic")
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_AUDIT_SCHEMA_VERSION", 3)
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_POLICIES", policies)
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_DEPENDENCE_VALUES", dependence)
    monkeypatch.setattr(bot, "_GENERATED_IDENTITY_AUDIT_CACHE", cache)
    monkeypatch.setattr(bot, "configured_generated_image_paths", lambda: calls.append("discover") or pool)
    monkeypatch.setattr(bot, "generated_image_origin_quote_hash", lambda name: calls.append(("origin", name)) or "origin")
    monkeypatch.setattr(bot, "file_sha256", lambda value: calls.append(("sha", value)) or "fresh")
    numeric, validate = bot.generated_identity_numeric, bot.validate_generated_identity_audit_item

    def number(value, *, key, maximum=10.0):
        calls.append(("number", key, value, maximum))
        return numeric(value, key=key, maximum=maximum)

    def validator(name, entry, image_by_name):
        assert image_by_name is pool
        result = validate(name, entry, image_by_name)
        assert result is entry["analysis"]
        validated.append(result)
        return result

    monkeypatch.setattr(bot, "generated_identity_numeric", number)
    monkeypatch.setattr(bot, "validate_generated_identity_audit_item", validator)
    result = bot.load_generated_identity_audit()
    assert result is cache[str(path)] and result[image.name] is validated[0]
    fields = ["recognisability_to_typical_viewer", "recognisability_to_politically_interested_viewer",
              "meaning_retention_without_identity", "origin_quote_suitability",
              "recommended_penalty_strength", "confidence"]
    assert calls == ["discover", ("origin", image.name), ("sha", image), ("origin", image.name)] + [
        ("number", f"{image.name}.{key}", data["items"][image.name]["analysis"][key],
         1.0 if key == "confidence" else 10.0) for key in fields
    ]
    calls.clear()
    assert bot.load_generated_identity_audit() is result and calls == []
    cache.clear()
    policies.clear()
    with pytest.raises(RuntimeError, match="invalid generated identity policy") as error:
        bot.load_generated_identity_audit()
    assert type(error.value.__cause__) is ValueError and cache == {}
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_POLICIES", {"custom"})
    dependence.clear()
    with pytest.raises(RuntimeError, match="invalid identity dependence"):
        bot.load_generated_identity_audit()
    assert cache == {}
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_DEPENDENCE_VALUES", {"custom"})
    monkeypatch.setattr(bot, "file_sha256", lambda value: "changed")
    with pytest.raises(RuntimeError, match="stale identity audit SHA-256") as error:
        bot.load_generated_identity_audit()
    assert type(error.value.__cause__) is ValueError and cache == {}


def test_startup_keeps_current_flags_raw_penalties_and_log_distinctions(monkeypatch):
    small, strong, logs, loads = NoFloat(), NoFloat(), [], []
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_SHADOW_SMALL_PENALTY", small)
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_SHADOW_STRONG_PENALTY", strong)
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_AUDIT_FILE", "configured-audit")
    monkeypatch.setattr(bot, "log", SimpleNamespace(info=lambda *args: logs.append(args)))
    monkeypatch.setattr(bot, "load_generated_identity_audit", forbidden)
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", False)
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING", True)
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IDENTITY_POLICY_SCORING", True)
    bot.validate_generated_identity_shadow_startup()
    assert logs == [("Generated identity-policy processing suspended because the generated image pool is disabled",)]
    logs.clear()
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING", False)
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IDENTITY_POLICY_SCORING", False)
    bot.validate_generated_identity_shadow_startup()
    assert logs == []
    monkeypatch.setattr(bot, "load_generated_identity_audit", lambda: loads.append(True) or {
        "a": valid_identity_analysis("unrestricted"), "b": valid_identity_analysis("small_penalty"),
    })
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING", True)
    bot.validate_generated_identity_shadow_startup()
    shadow_message = "Generated identity-policy shadow scoring enabled. audit_file=%s items=%d policies=%s small_penalty=%s strong_penalty=%s"
    production_message = "Generated identity policy production scoring enabled. audit_file=%s items=%d policies=%s small_penalty=%s strong_penalty=%s"
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING", False)
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IDENTITY_POLICY_SCORING", True)
    bot.validate_generated_identity_shadow_startup()
    assert [entry[0] for entry in logs] == [shadow_message, production_message]
    assert loads == [True, True]
    for entry in logs:
        assert entry[1:4] == ("configured-audit", 2, {"small_penalty": 1, "unrestricted": 1})
        assert list(entry[3]) == ["small_penalty", "unrestricted"]
        assert entry[4] is small and entry[5] is strong


def test_candidate_penalties_are_converted_only_in_their_original_branches(monkeypatch):
    name = "tg_synthetic.png"
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_SHADOW_SMALL_PENALTY", NoFloat())
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_SHADOW_STRONG_PENALTY", NoFloat())
    assert bot.generated_identity_candidate_shadow_row(candidate("t01.jpg", 9, source="original"), {})["identity_shadow_score"] == 9.0
    for policy in ("unrestricted", "origin_quote_only"):
        audit = {name: valid_identity_analysis(policy)}
        origin = bot.generated_identity_candidate_shadow_row(candidate(name, 9, origin_match=True), audit)
        assert origin["identity_adjustment"] == 0.0
        cross = bot.generated_identity_candidate_shadow_row(candidate(name, 9), audit)
        assert cross["identity_shadow_score"] == (9.0 if policy == "unrestricted" else None)
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_SHADOW_SMALL_PENALTY", "2.5")
    small = bot.generated_identity_candidate_shadow_row(candidate(name, 9), {name: valid_identity_analysis("small_penalty")})
    assert small["identity_shadow_score"] == 6.5 and small["identity_adjustment"] == -2.5
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_SHADOW_SMALL_PENALTY", NoFloat())
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_SHADOW_STRONG_PENALTY", "4.5")
    strong = bot.generated_identity_candidate_shadow_row(candidate(name, 9), {name: valid_identity_analysis("strong_penalty")})
    assert strong["identity_shadow_score"] == 4.5 and strong["identity_adjustment"] == -4.5


def test_selection_uses_current_callbacks_lazy_audit_and_shallow_candidate_copies(monkeypatch):
    scored = [candidate("t02.jpg", 10, source="original"), candidate("t01.jpg", 9, source="original")]
    for item in scored:
        item.update(components={"topics": item["score"]}, analysis={"nested": []})
    before, audit, calls, produced = copy.deepcopy(scored), {}, [], []
    original_row = bot.generated_identity_candidate_shadow_row

    def row(item, passed_audit):
        assert passed_audit is audit
        calls.append(item)
        result = original_row(item, passed_audit)
        produced.append(result)
        return result

    monkeypatch.setattr(bot, "generated_identity_candidate_shadow_row", row)
    monkeypatch.setattr(bot, "load_generated_identity_audit", forbidden)
    rows, eligible = bot.generated_identity_policy_selection(scored, audit_by_basename=audit)
    assert calls == scored and all(actual is expected for actual, expected in zip(calls, scored))
    assert all(actual is expected for actual, expected in zip(rows, produced))
    assert [item["basename"] for item in eligible] == ["t02.jpg", "t01.jpg"]
    for selected, original in zip(eligible, scored):
        assert selected is not original
        assert selected["components"] is original["components"]
        assert selected["analysis"] is original["analysis"]
        assert type(selected["score"]) is type(selected["baseline_score"]) is float
    assert scored == before
    calls.clear()
    monkeypatch.setattr(bot, "load_generated_identity_audit", lambda: calls.append("load") or audit)
    bot.generated_identity_policy_selection([], audit_by_basename=audit)
    assert calls == []
    assert bot.generated_identity_policy_selection([]) == ([], [])
    assert calls == ["load"]


def test_counterfactuals_use_current_row_and_choice_callbacks_in_order_without_rng_changes(monkeypatch):
    scored = [candidate("tg_restricted.png", 100), candidate("t02.jpg", 95, source="original"),
              candidate("t01.jpg", 95, source="original")]
    audit = {"tg_restricted.png": valid_identity_analysis("origin_quote_only")}
    saved, before, calls, produced = random.Random(29).getstate(), random.getstate(), [], []
    original_row, original_choice = bot.generated_identity_candidate_shadow_row, bot._choice_with_random_state

    def row(item, passed_audit):
        assert passed_audit is audit
        calls.append(("row", item))
        result = original_row(item, passed_audit)
        produced.append(result)
        return result

    def choose(rows, state):
        assert state is saved
        calls.append(("choice", rows))
        chosen = original_choice(rows, state)
        assert any(chosen is item for item in rows)
        return chosen

    monkeypatch.setattr(bot, "generated_identity_candidate_shadow_row", row)
    monkeypatch.setattr(bot, "_choice_with_random_state", choose)
    monkeypatch.setattr(bot, "load_generated_identity_audit", lambda: calls.append(("load",)) or audit)
    payload = bot.generated_identity_policy_shadow_result(
        quote(), scored[0], scored, selection_phase="normal", selection_rng_state=saved,
    )
    assert [call[0] for call in calls] == ["load", "row", "row", "row", "choice", "choice"]
    assert all(calls[i + 1][1] is item for i, item in enumerate(scored))
    assert calls[-2][1][0] is produced[0]
    assert all(actual is expected for actual, expected in zip(calls[-1][1], produced[1:]))
    assert payload["baseline_tie_count"] == 1 and payload["shadow_tie_count"] == 2
    assert payload["counterfactual_comparison_valid"] is payload["winner_changed_by_policy"] is True
    winner = original_choice(scored[1:], saved)
    calls.clear()
    applied = bot.generated_identity_policy_applied_result(
        quote(), winner, scored, produced, 2, selection_phase="normal", selection_rng_state=saved,
    )
    assert [call[0] for call in calls] == ["choice", "choice"]
    assert calls[0][1][0] is produced[0]
    assert all(actual is expected for actual, expected in zip(calls[1][1], produced[1:]))
    assert applied["counterfactual_comparison_valid"] is applied["winner_changed_by_policy"] is True
    mismatch = bot.generated_identity_policy_applied_result(
        quote(), winner, scored, produced, 1, selection_phase="normal", selection_rng_state=saved,
    )
    assert mismatch["counterfactual_comparison_valid"] is mismatch["winner_changed_by_policy"] is False
    assert mismatch["policy_effect"] == "counterfactual_mismatch"
    assert random.getstate() == before


def test_loggers_use_current_predicate_result_and_logger_with_exact_serialization(monkeypatch):
    q, chosen, scored, saved = quote(), candidate("t01.jpg", 10, source="original"), [], object()
    payload, logs, calls = {"z": "café", "a": [None, 1.5]}, [], []
    monkeypatch.setattr(bot, "log", SimpleNamespace(info=lambda *args: logs.append(args), exception=lambda *args: logs.append(args)))
    monkeypatch.setattr(bot, "generated_identity_policy_shadow_active", lambda: False)
    monkeypatch.setattr(bot, "generated_identity_policy_shadow_result", forbidden)
    bot.log_generated_identity_policy_shadow_result(q, chosen, scored, selection_phase="normal")
    assert logs == []

    def result(quote_choice, production_choice, candidates, **kwargs):
        assert quote_choice is q and production_choice is chosen and candidates is scored
        assert kwargs["selection_rng_state"] is saved and kwargs["selection_phase"] == "normal"
        calls.append(True)
        return payload

    monkeypatch.setattr(bot, "generated_identity_policy_shadow_active", lambda: True)
    monkeypatch.setattr(bot, "generated_identity_policy_shadow_result", result)
    bot.log_generated_identity_policy_shadow_result(q, chosen, scored, selection_phase="normal", selection_rng_state=saved)
    bot.log_generated_identity_policy_applied_result(payload)
    assert logs == [
        ("GENERATED_IDENTITY_POLICY_SHADOW_RESULT %s", '{"a":[null,1.5],"z":"caf\\u00e9"}'),
        ("GENERATED_IDENTITY_POLICY_APPLIED %s", '{"a":[null,1.5],"z":"caf\\u00e9"}'),
    ]
    assert calls == [True]
    payload["bad"] = object()
    bot.log_generated_identity_policy_shadow_result(q, chosen, scored, selection_phase="normal", selection_rng_state=saved)
    assert logs[-1] == (
        "Generated identity-policy shadow evaluation failed; production selection remains unchanged. line_no=%s image=%s phase=%s",
        q["line_no"], chosen["basename"], "normal",
    )
    with pytest.raises(TypeError):
        bot.log_generated_identity_policy_applied_result(payload)
