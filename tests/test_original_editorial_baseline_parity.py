from __future__ import annotations

import copy

from tools import verify_original_editorial_baseline_parity as parity


def record(index: int = 0) -> dict:
    return {
        "index": index,
        "scenario": parity.SCENARIOS[index % len(parity.SCENARIOS)],
        "quote_hash": "a" * 64,
        "selected": {"basename": "t01.jpg", "score": 1.0},
        "events": [],
    }


def test_parity_scenarios_cover_required_selector_edges() -> None:
    assert {
        "normal_original",
        "normal_mixed",
        "generated_spacing_blocked",
        "forced_cycle_reset",
        "last_image_fallback",
        "equal_baseline_tie",
        "used_cycle_transition",
        "seasonal_exclusion",
        "stale_exclusion",
        "visual_mismatch_exclusion",
        "origin_quote_match",
        "identity_policy_enabled",
        "no_candidate_outcome",
    } == set(parity.SCENARIOS)


def test_exact_comparison_reports_and_bounds_mismatches() -> None:
    expected = {"count": 8, "records": [record(index) for index in range(8)]}
    actual = copy.deepcopy(expected)
    for index in range(7):
        actual["records"][index]["selected"]["basename"] = "t02.jpg"
    result = parity.compare_pair(
        expected,
        actual,
        name="unit",
        include_shadow=False,
    )
    assert result["passed"] is False
    assert result["mismatch_count"] == 7
    assert result["exact_match_count"] == 1
    assert len(result["bounded_mismatch_examples"]) == 5


def test_new_breaker_observability_is_not_mistaken_for_baseline_output() -> None:
    base = record()
    production_rejected = copy.deepcopy(base)
    production_rejected["editorial_breaker_open"] = True
    result = parity.compare_pair(
        {"count": 1, "records": [base]},
        {"count": 1, "records": [production_rejected]},
        name="rejected",
        include_shadow=False,
    )
    assert result["passed"] is True


def test_shadow_event_remains_part_of_exact_shadow_comparison() -> None:
    base = record()
    current = copy.deepcopy(base)
    current["events"] = [
        {"event": "ORIGINAL_EDITORIAL_SHADOW_RESULT", "payload": {"winner": "t01.jpg"}}
    ]
    result = parity.compare_pair(
        {"count": 1, "records": [base]},
        {"count": 1, "records": [current]},
        name="shadow",
        include_shadow=True,
    )
    assert result["passed"] is False
