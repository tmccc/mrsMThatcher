# Improved Pairwise Pilot Execution Report

## Result

The improved provenance materially reduced human `neither` decisions from 44% to **24.0%** (20 percentage points; 45.5% relative). Challenger construction improved. Pairwise judging did not demonstrate safe selection improvement.

Tony chose the current winner 14/25, challenger 5/25, and neither 6/25. Majority vote agreed exactly in 48.0%, corrected 3/5 challenger cases, retained 7/14 current winners, and harmfully displaced/rejected 6/14. The unchanged current selector baseline agrees with 14/25 (56.0%), higher than pairwise majority.

All providers completed 25/25 with valid schemas. Vote patterns: {'3-1': 10, 'unanimous': 10, '2-1-1': 3, '2-2': 2}. All providers missed Tony in 9 cases; only one matched in 4.

## Provider table

| Provider | Agreement | Corrections | Retained | Harmful | Useful neither | Cost |
|---|---:|---:|---:|---:|---:|---:|
| anthropic | 40.0% | 4/5 | 6/14 | 8 | 0/6 | $0.3093 |
| gemini | 48.0% | 3/5 | 7/14 | 7 | 2/6 | $0.2126 |
| grok | 40.0% | 3/5 | 4/14 | 10 | 3/6 | $0.2049 |
| openai | 48.0% | 3/5 | 7/14 | 7 | 2/6 | $0.5065 |
| majority | 48.0% | 3/5 | 7/14 | 6 | 2/6 | $0.0000 |


## Regressions

Everest: Tony preferred the exact runner-up A; only Claude agreed. The communist-map current image therefore loses without a special rule, but majority vote selected neither rather than the publishable alternative.

Free trade: Tony selected neither. Grok alone agreed; the other three retained the current image. Neither exact selector candidate solved the mechanism/consequence/ideological-substitution problem.

## Strategy comparison

The current selector baseline outperformed majority pairwise exact agreement and fully preserves the 14 human-retained controls by definition. Pairwise majority damaged 6 and deferred 1. Soft-light cannot be fairly reconstructed because absolute quote-image first-impression alignment scores are not cached for every new candidate; no value is fabricated.

## Limitations

This is a deliberately selected 25-case pilot with shared images and no independent hold-out. Provider outputs preceded but were hidden from human review. Exact agreement is noisy at this sample size. The lower neither rate reflects challenger provenance and sample composition as well as any review effect.

## Recommendation

**Do not proceed.** The provenance repair succeeded, but pairwise majority failed ordinary-case protection and did not outperform leaving the current selector unchanged. Do not run the 150-200-case experiment or deploy a pairwise policy from this evidence.
