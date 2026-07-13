# First-Impression Validation Report 001

## Executive answer

The first-impression signal is valid: it catches Everest and free-trade failures and correlates with human replacement decisions. It also creates meaningful false replacements. A hard gate is too aggressive for ordinary acceptable indirect/symbolic images. Soft penalties are safer in principle, but exact winner effects cannot be measured because selector score margins and full candidate sets were not captured. Tone contributes some signal but does not dominate the combined formulas. Pairwise evidence is unavailable because no pairwise human reviews were recorded.

## Data integrity

- Intended/completed reviews: 50/50
- Decisions: keep=16, replace=28, unsure=6
- Match labels: {'no': 27, 'partly': 18, 'yes': 5}
- Pairwise human labels: 0
- Gemini first-impression denominator: 38/50; its one ambiguous request and unattempted cases remain explicit.
- Recovered semantic Gemini denominator: 250/250.
- Case IDs, quote keys, image hashes, schemas and image presence validated. Provider prompts are whitelist-built and contain no human-review fields.

## Provider validation

Provider predictions below are not native keep/replace outputs; they apply the predefined score<40 replacement rule.

| Provider/rule | Evaluated | Agreement (95% CI) | False keep | False replace | Replace precision | Replace recall | Match exact |
|---|---:|---:|---:|---:|---:|---:|---:|
| grok | 44 | 75.0% (60.6%–85.4%) | 6 | 5 | 81.5% | 78.6% | 60.0% |
| openai | 44 | 65.9% (51.1%–78.1%) | 14 | 1 | 93.3% | 50.0% | 66.0% |
| anthropic | 44 | 65.9% (51.1%–78.1%) | 11 | 4 | 81.0% | 60.7% | 58.0% |
| gemini | 33 | 63.6% (46.6%–77.8%) | 11 | 1 | 92.9% | 54.2% | 65.8% |
| majority_vote | 43 | 65.1% (50.2%–77.6%) | 11 | 4 | 80.0% | 59.3% | n/a |
| median_score | 44 | 63.6% (48.9%–76.2%) | 12 | 4 | 80.0% | 57.1% | n/a |
| median_cleaned | 41 | 61.0% (45.7%–74.3%) | 12 | 4 | 77.8% | 53.8% | n/a |

## Provider consistency

| Provider | Minor | Material |
|---|---:|---:|
| grok | 0 | 1 |
| openai | 0 | 0 |
| anthropic | 0 | 2 |
| gemini | 0 | 0 |

Raw outputs were not repaired. `provider_consistency_issues.csv` is the deterministic cleaned-view exclusion list. Claude Everest is a confirmed salience-scale inversion example.

## Hard gate

| Threshold | Rejected | Correct rejects | Wrong rejects | Rejection precision | Bad-image recall |
|---:|---:|---:|---:|---:|---:|
| 30 | 14 | 14 | 0 | 100.0% | 50.0% |
| 35 | 18 | 16 | 1 | 94.1% | 57.1% |
| 40 | 21 | 16 | 4 | 80.0% | 57.1% |
| 45 | 26 | 19 | 5 | 79.2% | 67.9% |
| 50 | 35 | 24 | 8 | 75.0% | 85.7% |

At 40, the gate wrongly replaces 4/16 human-approved images. This is too aggressive for production. No-candidate/exhaustion effects are unavailable without complete candidate sets.

## Soft penalties

| Curve | Evaluated | Bad corrected | Good damaged | Cases worse | Net correct-minus-errors |
|---|---:|---:|---:|---:|---:|
| soft_light | 43 | 17 | 1 | 11 | 21 |
| soft_moderate | 43 | 18 | 3 | 12 | 19 |
| soft_strong | 43 | 21 | 5 | 11 | 21 |

These are semantic-score-minus-penalty decision proxies, not measured selector winner changes. Soft-light is the least destructive predefined curve; no bespoke tuning is justified.

## Tone value

| Formula | Evaluated | Agreement | False keep | False replace | Spearman with keep |
|---|---:|---:|---:|---:|---:|
| first_impression | 44 | 63.6% | 12 | 4 | 0.47660437498759944 |
| tone | 44 | 50.0% | 22 | 0 | 0.5212676395172681 |
| first_plus_tone | 44 | 72.7% | 12 | 0 | 0.5302766736543144 |
| semantic | 43 | 67.4% | 14 | 0 | 0.5663439766131789 |
| semantic_plus_first | 43 | 72.1% | 12 | 0 | 0.5505572767032317 |
| semantic_first_tone | 43 | 69.8% | 13 | 0 | 0.6048147367590061 |

Tone provides incremental context for inspirational/ominous and reflective/confrontational mismatches, but vocabulary and scale inconsistencies produce false penalties. It should be a safeguard or small adjustment, not a gate.

## Ordinary-case protection

- False replacements at threshold 40: 4
- Categories: {'threshold too strict': 3, 'acceptable indirectness': 1}
- These cases show that useful symbolism, acceptable indirectness and broader-principle imagery can survive human review despite low literal first-impression scores.

## Known bad-case correction

- Human-rejected cases caught at threshold 40: 16/28
- Categories: {'tone mismatch': 3, 'secondary symbol overwhelmed quote': 4, 'ideological substitution': 3, 'wrong dominant message': 3, 'semantic mismatch': 3}
- Everest-like wrong dominant messages are present, but they are not the only failure class.

## Pairwise ranking

- Model comparisons: 5
- Completed pairwise human reviews: 0
- Agreement and displacement claims are unavailable. Pairwise ranking remains promising conceptually but unvalidated.

## Strategy comparison

| Strategy | Evaluated | Agreement | False keep | False replace | Deferrals | Bad corrections | Good retention |
|---|---:|---:|---:|---:|---:|---:|---:|
| A | 44 | 36.4% | 28 | 0 | 0 | 0 | 16 |
| B | 44 | 63.6% | 12 | 4 | 0 | 16 | 12 |
| C | 43 | 74.4% | 10 | 1 | 1 | 17 | 15 |
| D | 43 | 72.1% | 9 | 3 | 1 | 18 | 13 |
| E | 43 | 74.4% | 6 | 5 | 1 | 21 | 11 |
| F | 44 | 65.9% | 11 | 4 | 0 | 17 | 12 |
| G | 43 | 72.1% | 12 | 0 | 1 | 15 | 16 |
| H | 0 | n/a | 0 | 0 | 50 | 0 | 0 |

A=current selected pair; B=hard gate; C/D/E=soft curves applied to semantic suitability; F=tone safeguard; G=semantic/first-impression composite; H=pairwise (deferred because no human labels). Rankings are multi-criterion and descriptive, not an optimized scalar.

## Regressions

See `everest_retrospective.md` and `free_trade_retrospective.md`. Everest is a clear but unusually severe example; it demonstrates validity, not prevalence.

## Statistical caution

Fifty cases yield wide confidence intervals, were deliberately enriched for difficult/rejected examples, and are not an independent hold-out. Six operational labels are unsure. Multiple thresholds/formulas were inspected. Gemini covers only 38 cases. Provider scale inconsistencies and zero pairwise human labels further limit inference. Rules must not be tuned aggressively on this sample.

## Recommendation

**Proceed only after fixing data-quality issues.** Abandon hard gating. Preserve the signal observationally and take soft-light forward as the least aggressive predefined candidate, with tone only as a small safeguard. Fix/clarify salience-interference direction and collect pairwise labels first. After that, a bounded 150–200-case offline experiment with an independent case selection and at least 150 completed human keep/replace reviews is justified. Do not proceed to shadow or production use from this evidence.

## Files generated

`validation_manifest.json`, `human_label_summary.json`, `provider_validation.csv`, `provider_consistency_issues.csv`, `hard_gate_thresholds.csv`, `soft_penalty_results.csv`, `tone_value_analysis.csv`, `ordinary_case_false_replaces.csv`, `known_bad_case_corrections.csv`, `pairwise_validation.csv`, `strategy_comparison.csv`, `everest_retrospective.md`, `free_trade_retrospective.md`, this report and `first_impression_validation_summary.json`.

## Tests

Python compilation and `git diff --check` passed. Affected semantic-alignment and production-log-isolation tests: **191 passed**.

## Git diff stat

Tracked pre-existing research/fallback worktree diff at report time:

```text
 analyse_semantic_alignment_large.py   | 56 +++++++++++++++++++++++++++++++----
 analyse_semantic_alignment_xai.py     | 25 ++++++++++++++++
 semantic_alignment/bakeoff.py         | 18 +++++------
 semantic_alignment/large_bakeoff.py   |  4 +--
 semantic_alignment/vertex_recovery.py |  8 +++--
 5 files changed, 92 insertions(+), 19 deletions(-)
```

The new validation analyser, tests and output directory are untracked and therefore absent from ordinary `git diff --stat`.

## Git status short

Task additions are `analyse_first_impression_validation.py`, `semantic_alignment/first_impression_validation.py`, `tests/test_first_impression_validation.py` and `semantic_alignment_research/first_impression_validation_001/`. Existing unrelated and preceding-task changes remain untouched.

## Reproducibility and safety

All tables are generated deterministically from immutable cached inputs. Source hashes are in `validation_manifest.json`. No external service is used. No fingerprints, critic results or human reviews are modified. No production file or behavior is changed. Nothing is staged, committed, pushed or deployed.
