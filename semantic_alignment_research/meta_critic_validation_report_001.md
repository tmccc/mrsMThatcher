# Semantic alignment human validation report 001

## Executive Summary

**1. Can the current meta-critic be trusted automatically?** Provisionally, for this frozen sample: it resolved 23/25 cases and agreed with Tony on 21/23 automatic decisions (91.3%; Wilson 95% interval 73.2%–97.6%). Both automatic errors were false replaces on 3-to-1 votes where Gemini alone voted keep. This is promising but not sufficient for production trust.

**2. Closest provider to Tony:** Gemini at 96.0% exact agreement. The ranking is descriptive over only 25 enriched cases.

**3. Highest-value next improvement:** review another independently selected batch, enriched for 3-to-1 replace decisions with a single keep outlier, before changing rules or provider weights. This directly tests the only repeated automatic failure mode.

## Dataset and method

- 25 frozen cases; human labels: {'replace': 18, 'keep': 7}.
- Provider, meta, disagreement and queue IDs match exactly.
- All metrics are deterministic and computed from cached files.
- Root causes are structural inferences because reviewer notes are empty and the optional reason is uniformly `direct_fit`.

## Overall comparison

| System | Exact agreement | Coverage | Actionable accuracy | False keep | False replace | Unsure |
|---|---:|---:|---:|---:|---:|---:|
| grok | 88.0% | 96.0% | 91.7% | 0 | 2 | 1 |
| openai | 88.0% | 96.0% | 91.7% | 0 | 2 | 1 |
| anthropic | 72.0% | 84.0% | 85.7% | 0 | 3 | 4 |
| gemini | 96.0% | 100.0% | 96.0% | 1 | 0 | 0 |
| meta_critic | 84.0% | 92.0% | 91.3% | 0 | 2 | 2 |
| majority_vote | 88.0% | 96.0% | 91.7% | 0 | 2 | 1 |
| weighted_vote | 88.0% | 96.0% | 91.7% | 0 | 2 | 1 |

Meta uncertainty matched no human `unsure` label (Tony used none), but one of two deferrals avoided a false replace and the other deferred a correct replace.

## Policy A–H

| Policy | Coverage | Human reviews | Correct | Incorrect | False keep | False replace | Accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| A | 68.0% | 8 | 17 | 0 | 0 | 0 | 100.0% |
| G | 68.0% | 8 | 17 | 0 | 0 | 0 | 100.0% |
| E | 80.0% | 5 | 19 | 1 | 0 | 1 | 95.0% |
| B | 96.0% | 1 | 22 | 2 | 0 | 2 | 91.7% |
| D | 96.0% | 1 | 22 | 2 | 0 | 2 | 91.7% |
| F | 96.0% | 1 | 22 | 2 | 0 | 2 | 91.7% |
| H | 96.0% | 1 | 22 | 2 | 0 | 2 | 91.7% |
| C | 92.0% | 2 | 21 | 2 | 0 | 2 | 91.3% |

Ranking prioritises observed accuracy then coverage, but is in-sample and must not be used to tune rules aggressively.

## Provider measurements

| Provider | Human agreement | Keep/replace/unsure | Unrelated | Consequence | Mean suitability | False keep | False replace |
|---|---:|---|---:|---:|---:|---:|---:|
| grok | 88.0% | 4/20/1 | 7 | 4 | 32.5 | 0 | 2 |
| openai | 88.0% | 4/20/1 | 2 | 5 | 44.2 | 0 | 2 |
| anthropic | 72.0% | 1/20/4 | 1 | 2 | 31.6 | 0 | 3 |
| gemini | 96.0% | 8/17/0 | 4 | 2 | 43.4 | 1 | 0 |

Measured personalities: Grok used `unrelated` most (7) and scored suitability low (32.5); OpenAI recognised consequence most (5) and scored highest (44.2); Claude kept least (1) and used unsure most (4); Gemini kept most (8), was the sole correct keep vote in both meta automatic errors, and also produced one false keep.

## Disagreement index validation

| Band | Cases | Coverage | Actionable accuracy | False keep | False replace |
|---|---:|---:|---:|---:|---:|
| low | 4 | 100.0% | 100.0% | 0 | 0 |
| moderate | 20 | 95.0% | 89.5% | 0 | 2 |
| high | 1 | 0.0% | unavailable | 0 | 0 |
| extreme | 0 | unavailable | unavailable | 0 | 0 |

Only one high-disagreement case exists and it was deferred, so the index cannot yet be statistically validated. Both automatic errors occurred in the moderate band. Review-efficiency comparisons are not meaningful after reviewing all 25 cases; prospective queue-order validation is required.

## Meta disagreements and inferred root causes

- `5adcb6f1fbaad21d5ae0` — Conservatives know that the British are at their best in a society where individual people - not the state - make the ru: human `keep`, meta `unsure`; ideological_substitution, taxonomy_ambiguity, reviewer_preference.
- `b7cd43663a96e63b5b69` — Peace, freedom and justice are only to be found where people are prepared to defend them.: human `replace`, meta `unsure`; too_strict, score_dispersion, uncertainty_mismatch.
- `b98adf9e78161b670ad5` — Through bitter experience Solzhenitsyn has realised the dangers posed by an over-mighty state. The more power that accru: human `keep`, meta `replace`; mechanism_vs_consequence, too_strict, reviewer_preference.
- `e64eaa98b9e825be7e66` — All collectivism is always conducive to oppression: it is only the victims who differ.: human `keep`, meta `replace`; broader_principle, too_strict, reviewer_preference.

Frequencies: reviewer_preference=3, too_strict=3, ideological_substitution=1, taxonomy_ambiguity=1, score_dispersion=1, uncertainty_mismatch=1, mechanism_vs_consequence=1, broader_principle=1.

## Interesting cases

- **biggest disagreement**: `5adcb6f1fbaad21d5ae0` — Conservatives know that the British are at their best in a society where individual people - not the state - make the ru.
- **lowest disagreement**: `45aff3c1240a3010165b` — You and I come by road or rail, but economists travel on infrastructure..
- **strongest consensus**: `45aff3c1240a3010165b` — You and I come by road or rail, but economists travel on infrastructure..
- **unanimous mistake**: none observed.
- **unanimous success**: `0117c8919c5fdb98d8ff` — It is our job to go about telling everybody to obey the law..
- **only one provider matched human**: `5adcb6f1fbaad21d5ae0` — Conservatives know that the British are at their best in a society where individual people - not the state - make the ru, `b98adf9e78161b670ad5` — Through bitter experience Solzhenitsyn has realised the dangers posed by an over-mighty state. The more power that accru, `e64eaa98b9e825be7e66` — All collectivism is always conducive to oppression: it is only the victims who differ.
- **meta corrected all providers**: none observed.
- **biggest surprise**: `5adcb6f1fbaad21d5ae0` — Conservatives know that the British are at their best in a society where individual people - not the state - make the ru.
- **free trade**: `55f5cd6d633c143f5170` — Trade has been a great engine of post-war growth. All have gained from the greater freedom of trade and payments. Freer .
- **highest index**: `5adcb6f1fbaad21d5ae0` — Conservatives know that the British are at their best in a society where individual people - not the state - make the ru.

No case was observed where every provider was wrong and the meta-critic corrected them. No unanimous provider mistake was observed.

## Free-trade retrospective

All four providers voted replace; Tony voted `replace` and accepted the meta recommendation. Taxonomy split across ideological substitution (Grok), consequence (OpenAI), and broader principle (Claude/Gemini). The meta-critic retained high operational confidence while the disagreement index marked moderate rationale risk. The present pipeline still recommends replacement because trade mechanism evidence is absent even though prosperity is recognised as a consequence. On this case the system behaved appropriately.

## Evidence for future weighting

If this sample were representative, a simple accuracy-relative illustration would be {'grok': 1.023, 'openai': 1.023, 'anthropic': 0.837, 'gemini': 1.116}. These values were not applied. With n=25, enriched case selection and overlapping confidence intervals, production weighting would be premature.

## Statistical caution

The meta actionable estimate is based on 23 decisions; its Wilson 95% interval is wide. Provider estimates use only 25 correlated cases, selected for difficulty rather than randomly sampled from production. Comparing many providers, policies, bands and metrics creates multiple-comparison risk. Tuning thresholds or weights on these same labels would overfit. Another independent 25-case batch is worthwhile; approximately 100 reviewed cases would materially narrow uncertainty and permit held-out calibration. No provider can yet be ruled out.

## Recommendations

- Keep fingerprints, four-provider raw outputs, equal weights and production policy unchanged.
- Next validate the minority-keep/majority-replace failure mode on a fresh held-out batch.
- Preserve disagreement ordering prospectively to test review efficiency.
- Accumulate toward 100 reviewed cases before learning provider weights or changing automatic thresholds.

## Files generated

- `semantic_alignment_research/meta_critic_validation_report_001.md`
- `semantic_alignment_research/meta_critic_validation_summary.json`
- `semantic_alignment_research/provider_accuracy_summary.csv`
- `semantic_alignment_research/policy_accuracy_summary.csv`
- `semantic_alignment_research/disagreement_validation.csv`

## Tests and reproducibility

- Python compilation passed.
- Semantic-alignment, disagreement, meta-critic and production-log-isolation suites: 108 passed.
- Two consecutive analysis runs produced identical SHA-256 hashes for all five outputs.
- Provider/fingerprint hashes remained unchanged.

## Git state

`git diff --stat` continues to show only the pre-existing generated-image curation changes: two metadata files and four quarantined PNG deletions (6 files, 13 insertions, 777 deletions plus binary removals). Research analysis files are untracked and therefore absent from that stat.

`git status --short` retains those six unrelated tracked changes and the existing untracked research/runtime artefacts. Nothing is staged.

## Safety and reproducibility

No external API calls were made. No fingerprints or critic outputs were regenerated. All analysis used cached data and completed human reviews. No production behaviour changed. The bot was not stopped, restarted or signalled. No production state, config, history, receipt, log or metadata was edited. Nothing was staged, committed, pushed or deployed.
