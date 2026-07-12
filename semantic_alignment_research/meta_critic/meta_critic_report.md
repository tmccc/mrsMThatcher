# Deterministic semantic meta-critic report

## Summary

- Cases: 25
- Recommended actions: {'replace': 19, 'keep': 4, 'unsure': 2}
- Confidence: {'high': 16, 'moderate': 7, 'low': 2}
- Operational consensus: {'unanimous': 17, 'three_of_four': 7, 'plurality_only': 1}
- Human review required: 2
- Queue priorities: {'urgent': 2, 'high': 14, 'low': 3, 'control': 6}

## Policy coverage

| policy | resolved | coverage | review burden | human accuracy |
|---|---:|---:|---:|---|
| A | 17 | 68% | 8 | unavailable |
| B | 24 | 96% | 1 | unavailable |
| C | 23 | 92% | 2 | unavailable |
| D | 24 | 96% | 1 | unavailable |
| E | 20 | 80% | 5 | unavailable |
| F | 24 | 96% | 1 | unavailable |
| G | 17 | 68% | 8 | unavailable |
| H | 24 | 96% | 1 | unavailable |

Human accuracy is unavailable: existing A/B preferences are not direct four-provider keep/replace labels.

## Free-trade case

- Relationship votes: {'related_ideological_substitution': 1, 'illustrates_claimed_consequence': 1, 'illustrates_broader_principle': 2}
- Operational votes: {'replace': 4}
- Scores: {'relevance_median': 48.0, 'directness_median': 21.0, 'overall_suitability_median': 38.5, 'overall_suitability_iqr': 24.0}
- Recommendation: replace
- Confidence: high
- Human review: False
- Reasons: []

## Limitations

Consensus is not ground truth. Equal provider weights are used until at least 20 compatible human-labelled cases exist. This is offline research and has no production effect.
