# Semantic meta-critic disagreement index implementation report

## 1. Architecture

`semantic_alignment/disagreement.py` is a side-effect-free offline layer over immutable four-provider critic records. It calculates components, a 0-100 disagreement score, explanatory bands, safeguarded policies F-H and human-evaluation metrics. `analyse_semantic_meta_critic.py` joins these results to existing meta outputs and regenerates the review queue. `tools/semantic_meta_critic_review.py` supplies the loopback validation workflow.

Disagreement supplements, and does not replace, `recommended_action`, `confidence` or `requires_human_review`.

## 2. Components and formula

The index is an explicitly provisional weighted sum:

```text
0.35 * operational disagreement
+ 0.25 * taxonomy distance
+ 0.15 * robust score dispersion
+ 0.15 * structured-rationale divergence
+ 0.10 * provider-internal inconsistency
```

Bands are low 0-19, moderate 20-44, high 45-69 and extreme 70-100. Real distribution is 4 low, 20 moderate, 1 high and 0 extreme; mean score is 26.3. These weights are operational assumptions, not empirically calibrated probabilities.

Operational component values are 0 for 4/4, 25 for 3/1, 55 for 2/1/1, 80 for 2/2 and 100 for fully material mixing.

## 3. Taxonomy distance

An explicit symmetric distance table treats direct/mechanism, consequence/principle, consequence/ideological substitution and principle/secondary-theme relationships as adjacent at different distances. `unrelated`, `contradictory` and otherwise incompatible pairs receive substantially larger distances.

For every provider pair, overlap between either primary or secondary relationships reduces primary-label distance by 40%. Thus reversing consequence and ideological substitution is mild ordering disagreement, not conceptual incompatibility. The reported taxonomy component is mean pairwise distance on a 0-100 scale.

## 4. Score and rationale analysis

All eight score fields retain median, inclusive IQR and median absolute deviation. The component is twice mean field IQR, capped at 100. Provider/field values more than 30 points from median are listed explicitly.

Rationale comparison uses deterministic lowercase token sets and pairwise Jaccard overlap across quote mechanism, consequences, principles, depicted consequences, ideological framing, matched elements, missing primary elements and extraneous arguments. No embeddings or model calls are used.

Internal flags cover unrelated/high relevance, high mechanism without mechanism classification, high consequence without consequence classification, keep/very-low suitability and unexplained replace/very-high suitability.

## 5. Confidence independence

Confidence expresses strength of the operational recommendation. Disagreement expresses operator review risk. The free-trade case demonstrates independence: all four vote replace, producing high recommendation confidence, while disagreement is moderate because the taxonomy, scores and rationale differ. Conversely, providers could agree on ambiguous/unsure with low disagreement but still provide insufficient actionable confidence.

## 6. Review priority

Queue fields now include disagreement score/band/reasons, recommendation, confidence, automatic/deferred status and four blinded operational decisions. Ordering is priority band, then descending disagreement score.

Priority logic puts 2/2 operational splits first, then extreme disagreement, high disagreement attached to an automatic recommendation, internal inconsistencies, consequence/unrelated disputes, rationale conflict, free trade and unanimous controls. Strong operational consensus is not automatically deferred merely because taxonomy differs.

The queue contains all 25 cases: 23 automatic and 2 deferred. Priority distribution is urgent 2, high 14, low 3 and control 6.

## 7. Reviewer changes

The tablet-friendly loopback page shows quote, image, compact meta recommendation, confidence, disagreement score/band, explanations and blinded A/B/C/D decisions. Full fingerprints and critiques are expandable.

Required questions are only keep/replace/unsure and whether the meta recommendation is acceptable (yes/no/uncertain). Closest critics, reason and notes are optional. It supports save/next, previous/next, K/R/U/S shortcuts, progress, disagreement-band filtering, automatic/deferred filtering and highest-disagreement-first ordering.

Real human data remains in an explicitly supplied file; smoke testing used an isolated temporary path.

## 8. Validation metrics

Implemented evaluation reports coverage, accuracy, false keep, false replace, deferral and review efficiency. The schema supports grouping by confidence, disagreement band, operational margin, provider and policy once human decisions exist. Queue-order comparison can use disagreement, confidence, existing priority or deterministic seeded random order.

No compatible real operational human labels exist yet, so all accuracy/error metrics correctly remain unavailable. The two legacy A/B critic preferences are preserved but not treated as keep/replace truth.

## 9. Policies F-H

| Policy | Rule | Coverage | Deferred | Human accuracy |
|---|---|---:|---:|---|
| F | accept 3/4 only at low/moderate disagreement | 96% | 1 | unavailable |
| G | accept unanimous unless internal inconsistency is high | 68% | 8 | unavailable |
| H | accept majority unless disagreement is high/extreme | 96% | 1 | unavailable |

Existing policies remain: A 68%, B 96%, C 92%, D 96%, E 80%. No policy is proposed for production.

## 10. Free-trade case

- operational votes: 4 replace;
- primary taxonomy: one ideological substitution, one consequence, two broader principle;
- median relevance/directness/overall: 48 / 21 / 38.5;
- overall IQR: 24;
- components: operational 0, taxonomy 26.17, score dispersion 33.88, rationale 74.14, internal inconsistency 0;
- disagreement: 23, moderate;
- confidence/action: high / replace;
- queue priority: high due to rationale conflict and explicit case-study value.

The index therefore preserves unanimous operational confidence while surfacing meaningful disagreement about consequence, principle and ideological substitution.

## 11. Files changed

- `semantic_alignment/disagreement.py` (new)
- `analyse_semantic_meta_critic.py`
- `tools/semantic_meta_critic_review.py`
- `tests/test_semantic_disagreement.py` (new)
- regenerated research outputs under `semantic_alignment_research/meta_critic/`

Raw provider result files were not modified.

## 12. Tests and smoke tests

- Python compilation: passed.
- `git diff --check`: passed.
- disagreement, meta-critic, semantic-alignment and production-log-isolation tests: **108 passed**.
- loopback smoke: moderate/automatic filter worked; highest-priority case loaded; compact meta and four blinded critiques rendered; isolated review saved/reloaded; real review data remained absent; server stopped.
- synthetic evaluation test verified coverage, accuracy, false-keep and review-efficiency calculations.

## 13. Limitations

- Category distances and component weights require human calibration.
- Token overlap understates synonymous rationales and can overstate wording divergence.
- Twenty-five difficult cases are insufficient for learned thresholds or provider weights.
- No real operational labels exist yet, so queue efficiency and accuracy cannot be evaluated.
- Disagreement detects review risk, not correctness.

## 14. Reviewer command

```bash
python3 tools/semantic_meta_critic_review.py \
  --project-dir /disks/disk1/etc/mrsMThatcher \
  --source-run /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/runs/v2_20260711T111526Z \
  --bakeoff-dir /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/provider_bakeoff_25_20260712 \
  --meta-dir /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/meta_critic \
  --review-file /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/meta_critic/human_validation.json \
  --host 127.0.0.1 --port 8769
```

## 15. Safety and Git state

No external API call was made. No fingerprint or critic output was regenerated. Raw Grok/OpenAI/Claude/Gemini files were preserved. No production behaviour changed. The bot was not stopped, restarted or signalled. No production state, config, history, receipt, log or metadata was edited. Nothing was staged, committed, pushed or deployed.

`git diff --stat` continues to show only the pre-existing generated-image curation changes because research files are untracked. `git status --short` retains those unrelated changes and existing research/runtime artifacts.
