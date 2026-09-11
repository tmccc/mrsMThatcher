"""Historical generated-identity log payloads, independent of retired bot APIs."""


def identity_event(**changes: object) -> dict:
    result = {
        "selection_phase": "normal", "production_source": "generated", "production_winner": "tg_a.png",
        "production_score": 100.0, "production_origin_quote_match": False,
        "production_identity_policy": "origin_quote_only",
        "production_identity_action": "generated_cross_quote_origin_only_excluded",
        "shadow_winner_source": "original", "shadow_winner": "t01.jpg", "shadow_winner_score": 90.0,
        "baseline_winner": "tg_a.png", "counterfactual_policy_winner": "t01.jpg",
        "counterfactual_comparison_version": "generated_identity_counterfactual_v1",
        "counterfactual_comparison_valid": True, "policy_effect": "winner_changed",
        "winner_changed_by_policy": True, "winner_changed": True,
        "small_penalty_count": 0, "strong_penalty_count": 0,
        "origin_quote_only_excluded_count": 1, "excluded_generated_basenames": ["tg_a.png"],
        "penalised_generated_basenames": [],
    }
    result.update(changes)
    return result


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
