# Provider operational-outlier analysis

## Executive summary

Gemini is clearly more permissive, but it also rescued useful pairings: Tony kept 8 of its 14 lone keeps and rejected 6. This is too mixed for an automatic Gemini veto, but strong enough to justify routing lone-Gemini keeps to human review. Gemini's keep rate is the highest; in lone-keep cases it scores every major dimension substantially above the other three. The original regression still shows threshold instability, including the free-trade false keep.

## Datasets and integrity

- 250 intended frozen cases; 227 have all four results.
- Gemini completed 227 and exhausted 23; missing cases remain explicit.
- Human decisions loaded: 250; the original 25 remain the fixed regression subset.
- SHA-256 hashes of all raw 25-run and 250-run provider outputs were checked before and after analysis.

## Operational vote patterns

| Pattern | Count |
|---|---:|
| 2_keep_2_replace | 3 |
| 3_keep_1_replace | 1 |
| 3_replace_1_keep | 14 |
| 4_keep | 4 |
| 4_replace | 158 |
| incomplete_provider_set | 23 |
| mixed_with_unsure | 47 |

## Provider operational outliers

| Provider | Only keep | Only replace | Keep rate | Replace rate |
|---|---:|---:|---:|---:|
| grok | 0 | 1 | 6.4% | 89.2% |
| openai | 0 | 0 | 8.8% | 88.8% |
| anthropic | 0 | 0 | 2.8% | 82.4% |
| gemini | 14 | 0 | 21.1% | 76.2% |

## Gemini-only keeps

There are **14** Gemini-only keeps. Gemini's primary relationships in these cases are `{'illustrates_broader_principle': 11, 'related_ideological_substitution': 1, 'direct_illustration': 1, 'illustrates_mechanism': 1}`.

Median Gemini-minus-other-provider score differences:

- `relevance_score`: +23.0 (mean absolute gap 24.1).
- `directness_score`: +35.0 (mean absolute gap 36.3).
- `mechanism_alignment_score`: +27.5 (mean absolute gap 29.0).
- `consequence_alignment_score`: +30.0 (mean absolute gap 25.0).
- `principle_alignment_score`: +26.0 (mean absolute gap 27.2).
- `specificity_score`: +35.0 (mean absolute gap 30.9).
- `editorial_power_score`: +25.0 (mean absolute gap 28.9).
- `overall_suitability_score`: +34.5 (mean absolute gap 35.9).

## Human agreement

Human decisions are available for 250 cases. Provider denominators remain provider-specific because Gemini is missing 23 results.

| Provider | Labelled | Accuracy | Keep precision/recall | Replace precision/recall | False keep | False replace |
|---|---:|---:|---:|---:|---:|---:|
| grok | 250 | 85.6% | 87.5%/37.8% | 89.7%/99.0% | 2 | 23 |
| openai | 250 | 86.8% | 86.4%/44.2% | 89.2%/98.5% | 3 | 24 |
| anthropic | 250 | 79.2% | 85.7%/30.0% | 93.2%/99.5% | 1 | 14 |
| gemini | 227 | 93.0% | 83.3%/95.2% | 98.8%/95.5% | 8 | 2 |

Outlier-subset evidence:

- grok only replace: 0/1 agreed with Tony; 95% Wilson interval 0.0%–79.3%.
- gemini only keep: 8/14 agreed with Tony; 95% Wilson interval 32.6%–78.6%.

## Meta-critic interaction

Only 15 operational-outlier cases currently have human decisions. The review queue preserves meta decisions, deferrals and disagreement bands for every outlier; no meta rule was changed.
For Gemini-only keeps, meta decisions were `{'unsure': 2, 'replace': 12}`; 2 were deferred. Disagreement bands were `{'moderate': 14}`. Of 14 human-labelled examples, Tony agreed with Gemini 8 time(s) and with the replace majority 6 time(s).

| Case | Outlier | Pattern | Meta | Human | Correct party |
|---|---|---|---|---|---|
| 55f5cd6d633c143f5170 | gemini=keep | 3_replace_1_keep | unsure | replace | majority (replace) |
| e64eaa98b9e825be7e66 | gemini=keep | 3_replace_1_keep | replace | keep | outlier (gemini) |
| 6eb60852343c65542ab6 | gemini=keep | 3_replace_1_keep | unsure | keep | outlier (gemini) |
| e05e08ebeb6230dec78c | grok=replace | 3_keep_1_replace | keep | keep | majority (keep) |
| 11f5347c5573e7bf13ff | gemini=keep | 3_replace_1_keep | replace | keep | outlier (gemini) |
| f69bcc250b4db9ff9da1 | gemini=keep | 3_replace_1_keep | replace | keep | outlier (gemini) |
| ad4d6bef1aa22714ea65 | gemini=keep | 3_replace_1_keep | replace | replace | majority (replace) |
| adb9e872893aed6b780a | gemini=keep | 3_replace_1_keep | replace | replace | majority (replace) |
| 2d50c332f50101ae629b | gemini=keep | 3_replace_1_keep | replace | keep | outlier (gemini) |
| 84808fa8739e7f217f1e | gemini=keep | 3_replace_1_keep | replace | keep | outlier (gemini) |
| 86e1f9e0b2235c4b0de4 | gemini=keep | 3_replace_1_keep | replace | replace | majority (replace) |
| 63742f5c4a3756c90a14 | gemini=keep | 3_replace_1_keep | replace | keep | outlier (gemini) |
| d2627464761d24dd2b05 | gemini=keep | 3_replace_1_keep | replace | replace | majority (replace) |
| 611ea755e21c318fac42 | gemini=keep | 3_replace_1_keep | replace | replace | majority (replace) |
| e9d77bd5b5da13fd9683 | gemini=keep | 3_replace_1_keep | replace | keep | outlier (gemini) |

## Original 25 stability

| Provider | Unchanged | Keep→replace | Replace→keep | Taxonomy changes |
|---|---:|---:|---:|---:|
| grok | 24 | 1 | 0 | 6 |
| openai | 20 | 2 | 1 | 6 |
| anthropic | 25 | 0 | 0 | 3 |
| gemini | 22 | 1 | 2 | 7 |

Gemini changed three operational decisions, including free trade from replace to keep. The model slug did not change. This supports response variability around borderline judgements but does not identify which decision is correct.

### Free-trade regression

Gemini changed from `replace` to `keep` while its primary taxonomy was `illustrates_broader_principle` in the larger run. Overall suitability shifted by +23, consequence alignment by +20, and directness by +20. Tony's fixed decision is `replace`. The other three providers also chose replace, while the unchanged meta-critic deferred. On this case Gemini's changed decision was a false keep, not a successful rescue.

## Gemini missing-case sensitivity

| Scenario | Aggregate keeps | Keep rate over intended 250 |
|---|---:|---:|
| all_missing_keep | 71 | 28.4% |
| all_missing_replace | 48 | 19.2% |
| observed_rate | 53 | 21.2% |

The other three providers' vote combinations on missing cases were `{'replace/replace/replace': 20, 'keep/keep/unsure': 1, 'keep/replace/replace': 1, 'replace/replace/unsure': 1}`. Selection reasons among missing cases were `{'deterministic_fill': 16, 'weak_or_unrelated': 7}`. Twenty of 23 missing cases were unanimous replace among the other providers, which suggests the missing set is not enriched for obvious keeps, but this cannot reveal Gemini's counterfactual vote. The failures were HTTP-rate-limit events, so primary statistics do not impute outcomes.

## Provider profiles

| Provider | Keep | Replace | Unsure | Unrelated | Consequence | Principle | Ideological | Mean suitability | Mean latency | Cost/success |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| grok | 6.4% | 89.2% | 4.4% | 38.8% | 8.0% | 8.4% | 28.0% | 27.2 | 11.75s | $0.0114 |
| openai | 8.8% | 88.8% | 2.4% | 8.0% | 5.6% | 8.0% | 51.2% | 38.5 | 12.45s | $0.0290 |
| anthropic | 2.8% | 82.4% | 14.8% | 12.0% | 5.2% | 24.0% | 44.4% | 28.0 | 15.69s | $0.0172 |
| gemini | 21.1% | 76.2% | 2.6% | 18.1% | 0.9% | 26.4% | 5.3% | 36.9 | 7.73s | $0.0109 |

Gemini has the highest keep rate; OpenAI has the highest mean overall-suitability score. Grok has the highest unrelated frequency; Claude has the lowest keep rate. These are measured output tendencies, not quality judgements.

## Hypotheses

- **H1_more_permissive: partially supported.**
- **H2_better_indirect_matches: partially supported.**
- **H3_borderline_instability: partially supported.**
- **H4_majority_too_strict: supported.**

## Recommendation

**Use a lone-Gemini keep as a human-review trigger, not an automatic keep.** This would have surfaced 8 Tony-approved rescues while still allowing rejection of 6 false keeps. Seven of the 12 meta-critic auto-replaces in this subset disagreed with Tony. Do not change provider weights or production rules from this stratified sample. The 23 still-missing Gemini outputs remain excluded from four-provider correctness metrics.

## Review queue

The generated queue contains 42 informative cases and now carries all available human labels.

## Files generated

- `provider_outlier_summary.json`
- `operational_vote_patterns.csv`
- `provider_outlier_cases.csv`
- `gemini_only_keep_cases.csv`
- `regression_stability_25.csv`
- `human_agreement_by_outlier.csv`
- `gemini_missing_case_sensitivity.csv`
- `provider_outlier_review_queue.json`
- `provider_outlier_analysis_report.md`

## Tests and repository status

- Python compilation passed.
- Focused semantic-alignment and production-log-isolation tests passed.
- `git diff --check` passed.
- The tracked diff remains the pre-existing generated-image curation work; this analyzer, tests and research outputs are untracked and unstaged.

## Statistical limitations

- The 250 cases are stratified, not a random production sample.
- The lone-Gemini subset has only 14 cases; its 57.1% acceptance has a wide 95% Wilson interval of 32.6%–78.6%.
- Multiple provider, taxonomy and score comparisons increase false-discovery risk.
- Consensus is not ground truth, and score gaps do not establish causation.
- The 23 missing Gemini outputs can shift its intended-case keep rate between the sensitivity bounds above.

## Safety

All analysis was offline. No API call was made, no exhausted case was retried, no fingerprints or critic outputs were regenerated, and raw provider files retained their hashes. No production behaviour or files were changed; the bot was not stopped, restarted or signalled. Nothing was staged, committed, pushed or deployed.
