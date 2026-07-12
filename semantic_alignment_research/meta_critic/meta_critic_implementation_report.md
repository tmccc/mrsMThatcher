# Deterministic semantic meta-critic implementation report

## 1. Architecture

The implementation is fully offline and has three layers:

1. `semantic_alignment/meta_critic.py` performs deterministic consensus, consistency, confidence, weighting and policy calculations.
2. `analyse_semantic_meta_critic.py` reads the immutable four-provider bake-off and writes `meta_results.json`, `review_queue.json`, `provider_weights.json`, `policy_comparison.json` and `meta_critic_report.md`.
3. `tools/semantic_meta_critic_review.py` provides a loopback-only streamlined human review page using the sealed A/B/C/D mapping.

No LLM judges another LLM. Consensus is presented as evidence and prioritisation, never truth.

## 2. Consensus taxonomy

Taxonomy and operational votes are classified independently as `unanimous`, `three_of_four`, `two_two_split`, `plurality_only` or `complete_disagreement`. Numerical consensus is independently `compact` or `wide` based on overall-suitability IQR.

The result preserves raw relationship and operational vote maps, dominant and secondary interpretations, score medians/IQR, provider outliers and weighted and unweighted recommendations.

## 3. Recommendation rules

- 4/4 keep or replace: automatic action, high confidence.
- 3/4 keep or replace: automatic action, moderate confidence.
- 2/2, plurality involving unsure, or complete operational disagreement: defer with `unsure` and human review.
- Wide score spread or majority/median contradictions reduce confidence and can force review.
- Scores never override a split operational vote.

On the real 25 cases the safeguarded baseline recommends 19 replace, 4 keep and 2 unsure; 23 cases are automatically covered and 2 require review.

## 4. Score-consistency safeguards

Configurable defaults detect:

- overall suitability IQR above 25;
- provider overall score more than 30 from median;
- majority replace with median suitability at least 65;
- majority keep with median suitability at most 35;
- unrelated classification with directness at least 65;
- consequence alignment at least 65 without primary/secondary consequence recognition.

Flags are explicit in `review_reasons` and cannot silently change raw provider data.

## 5. Provider weighting

Equal weights of 1.0 are authoritative by default. Optional weights live only in `provider_weights.json`, report source and sample size, require at least 20 compatible human-labelled cases, and are capped to `[1/1.5, 1.5]`. Raw unweighted votes and weighted recommendations coexist.

The two legacy A/B preferences are insufficient and incompatible with four-provider operational calibration, so no learned weights were inferred.

## 6. Confidence model

Confidence bands are transparent:

- high: unanimous actionable vote without forcing anomaly;
- moderate: 3/4 actionable vote without forcing anomaly;
- low: split vote or consistency safeguard;
- insufficient: reserved for incomplete provider evidence.

Real distribution: high 16, moderate 7, low 2.

## 7. Review prioritisation

Queue order is urgent, high, medium, low, control. Reasons prioritise operational 2/2 splits, complete taxonomy disagreement, score-supported outliers, consequence disputes, unrelated disputes, wide dispersion, free trade, rationale conflict and unanimous controls.

Real queue: urgent 2, high 14, low 3, control 6. The free-trade case is explicitly marked and its three-way rationale conflict raises review value despite unanimous replacement.

## 8. Streamlined interface

The page displays quote, image, fingerprints, meta recommendation/confidence/reasons and four blinded critiques. It asks only:

- keep, replace or unsure;
- any closest critics A/B/C/D, none or unsure;
- one optional reason and note.

It writes to an explicitly supplied review file. Existing A/B and detailed calibration labels are not translated or overwritten.

## 9. Policy A-E results

| Policy | Rule | Coverage | Review burden | Human accuracy |
|---|---|---:|---:|---|
| A | unanimous only | 68% | 8 | unavailable |
| B | accept 3/4 | 96% | 1 | unavailable |
| C | 3/4 unless consistency safeguard | 92% | 2 | unavailable |
| D | weighted vote, currently equal | 96% | 1 | unavailable |
| E | majority plus consequence safeguard | 80% | 5 | unavailable |

Human false-keep/false-replace and accuracy metrics are implemented but unavailable until compatible human actions exist. Coverage means the fraction resolved automatically. Rules have not been tuned against these cases.

## 10. Free-trade case

Relationship votes are one ideological substitution, one claimed consequence and two broader principle. Operational vote is 4/4 replace. Median relevance is 48, directness 21 and suitability 38.5; suitability IQR is 24.

The meta-critic recommends replace with high operational confidence. It does not claim taxonomy consensus: relationship consensus is plurality-only and the case is prioritised for its conflicting rationale and research importance. This preserves the distinction between consequence recognition and weak mechanism alignment without hard-coding an answer.

## 11. Files changed

- `semantic_alignment/meta_critic.py` (new)
- `analyse_semantic_meta_critic.py` (new)
- `tools/semantic_meta_critic_review.py` (new)
- `tests/test_semantic_meta_critic.py` (new)
- outputs under `semantic_alignment_research/meta_critic/`

Raw Grok, OpenAI, Claude and Gemini result hashes were captured before work and remained unchanged.

## 12. Tests and smoke result

- Python compilation: passed.
- `git diff --check`: passed.
- Focused meta-critic tests plus semantic-alignment and production-log-isolation suites: **99 passed**.
- Isolated loopback smoke: urgent case, image, meta summary and A/B/C/D critiques rendered; temporary review saved and reloaded outside the real dataset; server stopped.

Tests cover unanimous and 3/1 actions, 2/2 and unsure deferral, complete disagreement, outliers, score safeguards, consequence/ideology split, equal/weighted votes, minimum sample and caps, confidence, priority, policies, human accuracy/coverage, input immutability, no API path and no production write.

## 13. Exact reviewer command

```bash
python3 tools/semantic_meta_critic_review.py \
  --project-dir /disks/disk1/etc/mrsMThatcher \
  --source-run /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/runs/v2_20260711T111526Z \
  --bakeoff-dir /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/provider_bakeoff_25_20260712 \
  --meta-dir /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/meta_critic \
  --review-file /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/meta_critic/human_reviews.json \
  --host 127.0.0.1 --port 8769
```

## 14. Limitations

- Four provider votes are not independent measurements of truth.
- The sample is deliberately difficult and only 25 cases.
- No compatible four-way human decisions exist yet.
- Thresholds are operational defaults, not calibrated production policy.
- Explanation factual support is surfaced through deterministic consistency flags, not semantically adjudicated by another model.

## 15. Safety and Git state

No external API call was made. No fingerprint or critic result was regenerated. Raw Grok/OpenAI/Claude/Gemini outputs were preserved. No production behaviour changed. The bot was not stopped, restarted or signalled. No production state, config, history, receipt, log or metadata was edited. Nothing was staged, committed, pushed or deployed.

`git diff --stat` continues to show only pre-existing generated-image curation changes because this research work is untracked. `git status --short` retains those unrelated changes and existing untracked research/runtime artifacts.
