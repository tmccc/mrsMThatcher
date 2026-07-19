from __future__ import annotations

import copy
import json
import random
from pathlib import Path

import pytest

import mrs_log_digest as digest
from tests.test_generated_identity_policy_shadow_scoring import candidate, quote, valid_identity_analysis
from tests.test_unit_helpers import bot


def policies() -> tuple[dict[str, dict], dict[str, str]]:
    names = {policy: f"tg_{char * 64}.png" for policy, char in zip(sorted(bot.GENERATED_IDENTITY_POLICIES), "abcd")}
    return {name: valid_identity_analysis(policy) for policy, name in names.items()}, names


def applied_payload(
    scored: list[dict],
    audit: dict[str, dict],
    *,
    seed: int = 17,
) -> dict:
    rows, eligible = bot.generated_identity_policy_selection(scored, audit_by_basename=audit)
    best = max(float(row["score"]) for row in eligible)
    tied = [row for row in eligible if float(row["score"]) == best]
    rng_state = random.Random(seed).getstate()
    production = bot._choice_with_random_state(tied, rng_state)
    return bot.generated_identity_policy_applied_result(
        quote(),
        production,
        scored,
        rows,
        len(tied),
        selection_phase="normal",
        selection_rng_state=rng_state,
    )


def test_production_policy_defaults_disabled_and_is_distinct_from_shadow() -> None:
    assert bot.ENABLE_GENERATED_IDENTITY_POLICY_SCORING is False
    assert "ENABLE_GENERATED_IDENTITY_POLICY_SCORING" in bot.LOCAL_CONFIG_ALLOWED_KEYS
    assert bot.ENABLE_GENERATED_IDENTITY_POLICY_SCORING is not bot.ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING or not bot.ENABLE_GENERATED_IDENTITY_POLICY_SCORING


def test_disabled_production_and_shadow_do_not_require_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IDENTITY_POLICY_SCORING", False)
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING", False)
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_AUDIT_FILE", "/missing")
    bot.validate_generated_identity_shadow_startup()


def test_enabled_production_requires_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IDENTITY_POLICY_SCORING", True)
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING", False)
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_AUDIT_FILE", "/missing")
    monkeypatch.setattr(bot, "_GENERATED_IDENTITY_AUDIT_CACHE", {})
    with pytest.raises(FileNotFoundError):
        bot.validate_generated_identity_shadow_startup()


def test_disabled_generated_pool_suspends_enabled_identity_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", False)
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IDENTITY_POLICY_SCORING", True)
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING", True)
    monkeypatch.setattr(
        bot,
        "load_generated_identity_audit",
        lambda: pytest.fail("disabled generated pool must not load identity audit"),
    )

    bot.validate_generated_identity_shadow_startup()

    assert bot.generated_identity_policy_scoring_active() is False
    assert bot.generated_identity_policy_shadow_active() is False


def test_policy_selection_applies_all_categories_without_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    audit, names = policies()
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_SHADOW_SMALL_PENALTY", 6.0)
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_SHADOW_STRONG_PENALTY", 15.0)
    scored = [
        candidate("t01.jpg", 80, source="original"),
        candidate(names["unrestricted"], 81),
        candidate(names["small_penalty"], 90),
        candidate(names["strong_penalty"], 94),
        candidate(names["origin_quote_only"], 100),
    ]
    before = copy.deepcopy(scored)
    rows, eligible = bot.generated_identity_policy_selection(scored, audit_by_basename=audit)
    by_name = {row["basename"]: row for row in eligible}
    assert by_name["t01.jpg"]["score"] == 80
    assert by_name[names["unrestricted"]]["score"] == 81
    assert by_name[names["small_penalty"]]["score"] == 84
    assert by_name[names["strong_penalty"]]["score"] == 79
    assert names["origin_quote_only"] not in by_name
    assert next(row for row in rows if row["basename"] == names["origin_quote_only"])["identity_adjustment"] is None
    assert scored == before


def test_origin_only_origin_quote_and_origin_boost_remain_eligible() -> None:
    audit, names = policies()
    scored = [candidate(names["origin_quote_only"], 104, origin_match=True, origin_boost=4)]
    _, eligible = bot.generated_identity_policy_selection(scored, audit_by_basename=audit)
    assert eligible[0]["score"] == 104
    assert eligible[0]["origin_quote_boost"] == 4
    assert eligible[0]["identity_action"] == "generated_origin_quote_unrestricted"


def test_real_policy_winner_changes_and_only_actual_winner_updates_state(monkeypatch: pytest.MonkeyPatch) -> None:
    audit, names = policies()
    scored = [candidate(names["origin_quote_only"], 100), candidate("t01.jpg", 95, source="original")]
    _, eligible = bot.generated_identity_policy_selection(scored, audit_by_basename=audit)
    winner = max(eligible, key=lambda row: row["score"])
    state = {"original_regular_posts_since_generated_image": 0}
    used = set()
    used.add(winner["basename"])
    bot.update_regular_generated_image_spacing_state(state, winner["basename"])
    assert winner["basename"] == "t01.jpg"
    assert used == {"t01.jpg"}
    assert names["origin_quote_only"] not in used
    assert state["original_regular_posts_since_generated_image"] == 1


def test_all_excluded_has_no_policy_candidate_and_original_prevents_exhaustion() -> None:
    audit, names = policies()
    _, none = bot.generated_identity_policy_selection([candidate(names["origin_quote_only"], 100)], audit_by_basename=audit)
    _, with_original = bot.generated_identity_policy_selection([candidate(names["origin_quote_only"], 100), candidate("t01.jpg", 1, source="original")], audit_by_basename=audit)
    assert none == []
    assert [row["basename"] for row in with_original] == ["t01.jpg"]


def test_baseline_comparator_is_deterministic_and_does_not_consume_rng() -> None:
    audit, names = policies()
    scored = [candidate(names["origin_quote_only"], 100), candidate("t02.jpg", 95, source="original"), candidate("t01.jpg", 95, source="original")]
    rows, eligible = bot.generated_identity_policy_selection(scored, audit_by_basename=audit)
    state_before = random.getstate()
    rng_state = random.Random(29).getstate()
    production = bot._choice_with_random_state(eligible, rng_state)
    payload = bot.generated_identity_policy_applied_result(
        quote(), production, scored, rows, 2,
        selection_phase="forced_cycle_reset", selection_rng_state=rng_state,
    )
    assert random.getstate() == state_before
    assert payload["winner_changed_by_policy"] is True
    assert payload["counterfactual_comparison_valid"] is True
    assert payload["counterfactual_comparison_version"] == "generated_identity_counterfactual_v1"
    assert payload["baseline_identity_action"] == "generated_cross_quote_origin_only_excluded"
    assert payload["replacement_source_transition"] == "generated->original"
    assert payload["recovery_effect"] == "forced_cycle_reset"


def test_actual_tie_uses_one_existing_random_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(bot.random, "choice", lambda rows: calls.append([row["basename"] for row in rows]) or rows[0])
    rows = [candidate("t01.jpg", 10, source="original"), candidate("t02.jpg", 10, source="original")]
    best = max(row["score"] for row in rows)
    winner = bot.random.choice([row for row in rows if row["score"] == best])
    assert winner["basename"] == "t01.jpg"
    assert calls == [["t01.jpg", "t02.jpg"]]


def test_original_editorial_shadow_does_not_receive_identity_adjusted_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    audit, names = policies()
    baseline = [candidate(names["small_penalty"], 90), candidate("t01.jpg", 86, source="original")]
    before = copy.deepcopy(baseline)
    bot.generated_identity_policy_selection(baseline, audit_by_basename=audit)
    assert baseline == before
    assert baseline[0]["score"] == 90


def policy_event(**changes: object) -> dict:
    value = {
        "line_no": 10, "selection_phase": "normal", "baseline_winner": "restricted.png",
        "baseline_winner_source": "generated", "baseline_winner_score": 90,
        "baseline_identity_action": "generated_cross_quote_origin_only_excluded",
        "production_winner": "t01.jpg", "production_winner_source": "original",
        "production_policy_score": 88, "winner_changed_by_policy": True,
        "baseline_winner_differs": True,
        "counterfactual_comparison_version": "generated_identity_counterfactual_v1",
        "counterfactual_comparison_valid": True,
        "counterfactual_policy_winner": "t01.jpg",
        "policy_effect": "winner_changed",
        "origin_quote_only_excluded_count": 1, "small_penalty_count": 0, "strong_penalty_count": 0,
        "excluded_generated_basenames": ["restricted.png"], "replacement_source_transition": "generated->original",
        "recovery_effect": "none",
    }
    value.update(changes)
    return value


def test_digest_policy_denominator_and_zero_safe() -> None:
    assert digest.generated_identity_policy_summary([])["winner_change_percent"] == 0
    summary = digest.generated_identity_policy_summary([policy_event(), policy_event(origin_quote_only_excluded_count=0, winner_changed_by_policy=False)])
    assert summary["policy_relevant_observations"] == 1
    assert summary["winner_changes"] == 1
    assert summary["winner_change_percent"] == 100
    assert summary["replacement_source_transitions"] == [("generated->original", 1)]


def test_policy_neutral_equal_score_tie_is_not_reported_as_policy_change() -> None:
    event = policy_event(
        baseline_winner="t34.jpg",
        baseline_winner_source="original",
        baseline_winner_score=31.8,
        baseline_identity_action="original_unchanged",
        production_winner="t45.jpg",
        production_winner_source="original",
        production_policy_score=31.8,
        production_identity_action="original_unchanged",
        winner_changed_by_policy=True,  # Legacy event emitted before telemetry correction.
        origin_quote_only_excluded_count=0,
        excluded_generated_basenames=[],
        replacement_source_transition="original->original",
    )
    for key in (
        "counterfactual_comparison_version", "counterfactual_comparison_valid",
        "counterfactual_policy_winner", "policy_effect", "baseline_winner_differs",
    ):
        event.pop(key, None)
    summary = digest.generated_identity_policy_summary([event])
    assert summary["policy_relevant_observations"] == 0
    assert summary["identity_policy_winner_changes"] == summary["winner_changes"] == 0
    assert summary["policy_neutral_baseline_differences"] == 1
    assert summary["policy_neutral_equal_score_tie_resolutions"] == 1

    record = digest.Record(
        ts=digest.parse_dt("2026-07-18 09:04:04"), level="INFO", src="test", line=1,
        msg="GENERATED_IDENTITY_POLICY_APPLIED " + json.dumps(event), path="test.log", ordinal=1,
    )
    rendered = digest.render_markdown(digest.analyse([record]))
    assert "identity_policy_winner_changed         = 0 (0.0%)" in rendered
    assert "Baseline winners changed by the generated-identity policy:" not in rendered
    assert "Policy-neutral baseline differences:" in rendered
    assert "equal-score production tie resolution" in rendered
    assert "original_unchanged" not in rendered


def test_future_policy_event_marks_policy_neutral_tie_without_changing_selection() -> None:
    scored = [candidate("t34.jpg", 31.8, source="original"), candidate("t45.jpg", 31.8, source="original")]
    payload = applied_payload(scored, {})
    assert payload["baseline_winner_differs"] is False
    assert payload["winner_changed_by_policy"] is False
    assert payload["policy_effect"] == "none"
    assert payload["replacement_source_transition"] == "unchanged"


def test_penalty_to_non_winning_candidate_is_not_a_policy_caused_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit, names = policies()
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_SHADOW_SMALL_PENALTY", 6.0)
    scored = [
        candidate("t01.jpg", 100, source="original"),
        candidate(names["small_penalty"], 80),
        candidate("t02.jpg", 70, source="original"),
    ]
    payload = applied_payload(scored, audit)
    assert payload["policy_effect"] == "scores_or_eligibility_only"
    assert payload["baseline_winner"] == payload["production_winner"] == "t01.jpg"
    assert payload["winner_changed_by_policy"] is False


def test_policy_exclusion_of_counterfactual_winner_is_causal() -> None:
    audit, names = policies()
    scored = [
        candidate(names["origin_quote_only"], 100),
        candidate("t01.jpg", 90, source="original"),
    ]
    payload = applied_payload(scored, audit)
    assert payload["baseline_winner"] == names["origin_quote_only"]
    assert payload["production_winner"] == "t01.jpg"
    assert payload["winner_changed_by_policy"] is True
    assert payload["policy_effect"] == "winner_changed"


def test_policy_penalty_that_lowers_counterfactual_winner_is_causal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit, names = policies()
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_SHADOW_STRONG_PENALTY", 15.0)
    scored = [
        candidate(names["strong_penalty"], 100),
        candidate("t01.jpg", 90, source="original"),
    ]
    payload = applied_payload(scored, audit)
    assert payload["baseline_winner"] == names["strong_penalty"]
    assert payload["production_winner"] == "t01.jpg"
    assert payload["winner_changed_by_policy"] is True


def test_same_saved_rng_state_controls_both_counterfactual_tie_breaks() -> None:
    audit, names = policies()
    scored = [
        candidate("t02.jpg", 100, source="original"),
        candidate("t01.jpg", 100, source="original"),
        candidate(names["small_penalty"], 50),
    ]
    first = applied_payload(scored, audit, seed=73)
    second = applied_payload(scored, audit, seed=73)
    assert first == second
    assert first["baseline_winner"] == first["production_winner"]
    assert first["winner_changed_by_policy"] is False


def test_legacy_policy_difference_is_not_claimed_as_counterfactual_causation() -> None:
    event = policy_event()
    for key in (
        "counterfactual_comparison_version", "counterfactual_comparison_valid",
        "counterfactual_policy_winner", "policy_effect", "baseline_winner_differs",
    ):
        event.pop(key, None)
    summary = digest.generated_identity_policy_summary([event])
    assert summary["identity_policy_winner_changes"] == 0
    assert summary["legacy_policy_causation_unverified"] == 1


def test_policy_digest_is_deterministic_for_identical_events() -> None:
    event = policy_event()
    record = digest.Record(
        ts=digest.parse_dt("2026-07-18 09:04:04"), level="INFO", src="test", line=1,
        msg="GENERATED_IDENTITY_POLICY_APPLIED " + json.dumps(event), path="test.log", ordinal=1,
    )
    assert digest.render_markdown(digest.analyse([record])) == digest.render_markdown(digest.analyse([record]))


def test_digest_parses_policy_event_and_never_describes_baseline_as_posted() -> None:
    record = digest.Record(ts=digest.parse_dt("2026-07-10 12:00:00"), level="INFO", src="test", line=1, msg="GENERATED_IDENTITY_POLICY_APPLIED " + json.dumps(policy_event()), path="test.log", ordinal=1)
    report = digest.analyse([record])
    assert report["generated_identity_policy"]["summary"]["observations"] == 1
    rendered = digest.render_markdown(report)
    assert "## Generated identity policy" in rendered
    assert "active in real production" in rendered
    assert "counterfactual baseline image was not posted" in rendered


@pytest.mark.parametrize("phase", ["normal", "forced_cycle_reset", "last_image_fallback"])
def test_event_preserves_selection_phase(phase: str) -> None:
    summary = digest.generated_identity_policy_summary([policy_event(selection_phase=phase, recovery_effect=phase if phase != "normal" else "none")])
    assert (phase, 1) in summary["selection_phases"]


def test_real_diagnostic_audit_record_remains_origin_only() -> None:
    payload = json.loads(Path("generated_image_identity_dependence_audit.json").read_text())
    diagnostic = "tg_faf99f3030693b0a55f0116194551792f3c4ea51261d7310eca7fb4d33b667d5.png"
    assert payload["items"][diagnostic]["analysis"]["recommended_cross_quote_policy"] == "origin_quote_only"
