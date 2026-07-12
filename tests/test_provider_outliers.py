from semantic_alignment.provider_outliers import (
    classify_votes, compare_regression, human_metrics, score_profile,
    sensitivity, wilson_interval,
)


def votes(*values):
    return dict(zip(("grok", "openai", "anthropic", "gemini"), values))


def test_vote_patterns_and_outliers():
    assert classify_votes(votes("keep", "keep", "keep", "keep"))["pattern"] == "4_keep"
    assert classify_votes(votes("replace", "replace", "replace", "replace"))["pattern"] == "4_replace"
    lone = classify_votes(votes("replace", "replace", "replace", "keep"))
    assert (lone["pattern"], lone["outlier_provider"], lone["outlier_vote"]) == (
        "3_replace_1_keep", "gemini", "keep")
    lone = classify_votes(votes("keep", "replace", "keep", "keep"))
    assert (lone["pattern"], lone["outlier_provider"]) == ("3_keep_1_replace", "openai")
    assert classify_votes(votes("keep", "replace", "keep", "replace"))["pattern"] == "2_keep_2_replace"


def test_mixed_unsure_and_incomplete_are_explicit():
    assert classify_votes(votes("keep", "replace", "unsure", "replace"))["pattern"] == "mixed_with_unsure"
    result = classify_votes(votes("keep", "replace", "replace", None))
    assert result["pattern"] == "incomplete_provider_set"
    assert result["available"] == 3


def test_score_profile_uses_other_provider_median():
    outlier = {"relevance_score": 80}
    majority = [{"relevance_score": 20}, {"relevance_score": 30}, {"relevance_score": 40}]
    from semantic_alignment import provider_outliers as module
    old = module.SCORES
    module.SCORES = ("relevance_score",)
    try:
        result = score_profile(outlier, majority)["relevance_score"]
    finally:
        module.SCORES = old
    assert result == {"outlier": 80.0, "majority_median": 30.0, "difference": 50.0, "absolute_difference": 50.0}


def test_human_metrics_counts_false_keep_and_replace():
    results = {"a": {"keep_or_replace": "keep"}, "b": {"keep_or_replace": "replace"}, "c": {"keep_or_replace": "replace"}}
    labels = {"a": {"human_action": "replace"}, "b": {"human_action": "keep"}, "c": {"human_action": "replace"}}
    result = human_metrics(results, labels)
    assert (result["correct"], result["false_keep"], result["false_replace"]) == (1, 1, 1)
    assert wilson_interval(1, 3) is not None


def test_regression_comparison_and_missing_result():
    base = {"keep_or_replace": "replace", "primary_relationship": "unrelated", "model": "m"}
    score_fields = {
        "relevance_score": 10, "directness_score": 10, "mechanism_alignment_score": 10,
        "consequence_alignment_score": 10, "principle_alignment_score": 10,
        "specificity_score": 10, "editorial_power_score": 10, "overall_suitability_score": 10,
    }
    old = {"a": base | score_fields}
    new = {"a": (base | score_fields) | {"keep_or_replace": "keep", "relevance_score": 20}}
    rows = compare_regression(old, new, {"a", "b"})
    assert rows[0]["decision_change"] == "replace_to_keep"
    assert rows[0]["relevance_score_shift"] == 10
    assert rows[1] == {"case_id": "b", "missing": True}


def test_missing_gemini_sensitivity_scenarios():
    rows = {x["scenario"]: x for x in sensitivity(48, 227, 23)}
    assert rows["all_missing_keep"]["aggregate_keeps"] == 71
    assert rows["all_missing_replace"]["aggregate_keeps"] == 48
    assert 48 <= rows["observed_rate"]["aggregate_keeps"] <= 71
