# Counterfactual Canonical Dataset Analysis Report

## 1. Executive summary

The existing large counterfactual dataset validates as complete and internally coherent. All 20 runs contain 250 shared-quote indices, all three branch records, one comparison record, and a committed checkpoint. The 5,000 matched indices and 15,000 branch selections were analysed without rerunning the simulation. No trajectory-invalidating defect was found.

## 2. Existing dataset located

Session `counterfactual_evidence_20x250_20260710` is the large run referenced by the implementation report.

## 3. Canonical dataset path

`simulation_runs/counterfactual_evidence_20x250_20260710/`

## 4. Dataset manifest and aggregate hash

`counterfactual_canonical_dataset_manifest.json` freezes 288 files totalling 175,734,554 bytes. Deterministic aggregate SHA-256:

`b304566af8fe0ad558f7ed6d54f87613a47cf89bb86aecad489574232a7b7ae8`

The stable name is `canonical_5000_shared_quote_counterfactual_v1`. Source records were not modified.

## 5. Simulator commit/tree identity

The input snapshot records commit `e711036983e334dbbdf8de35982885c3a26a5100`. The session records simulator source SHA-256 `fbbc370d51a60a54e280b3e578fe21b52011eb49f5b56cd24ad89f073007` and production source SHA-256 `95e7feb2d7c1a15753d958df8264b982e22db3659bc28b2165637dd86adfae40`.

## 6. Dataset completeness validation

- 20/20 runs present.
- 250/250 contiguous indices in every stream and run.
- 5,000 shared quotes, production rows, editorial rows, identity rows, and comparison rows.
- 15,000 total branch selections.
- Seeds are exactly 1000 through 1019.
- Shared quote hashes/timestamps agree across all records.
- Timestamps are monotonic; scores are finite; winners exist in the snapshot corpus.
- Candidate counts, source labels, spacing transitions, comparison flags, and final checkpoints are coherent.
- No duplicate, missing, or uncommitted index was accepted.

## 7. Recalculation of headline metrics

Independent raw-record recalculation produced:

| metric | recalculated |
|---|---:|
| Production/editorial agreement | 60.68% |
| Production/identity agreement | 62.70% |
| Editorial/identity agreement | 49.82% |
| All-three agreement | 44.36% |
| Editorial divergence | 1,966 / 5,000 = 39.32% |
| Identity divergence | 1,865 / 5,000 = 37.30% |

Production/editorial/identity generated counts are 1,437/1,432/1,359. Entropies are 6.96435/6.95998/6.92136 bits; top-10 shares are 12.06%/12.06%/12.20%.

## 8. Discrepancies found

No winner, count, agreement, diversity, phase, reset, or diagnostic discrepancy was found against the saved summary. The session records 939.071 seconds (15.65 minutes), not approximately 50 minutes; the latter is an external elapsed-time description rather than the dataset's accumulated runtime.

The canonical run used `candidate_detail=none`. Consequently exact winner-versus-runner-up margins, full candidate rankings, complete per-index history equality, and candidate-absence reasons cannot be reconstructed. Derived case-study score gaps are explicitly labelled as cross-branch selected-winner gaps, not runner-up margins.

## 9. Whether a large rerun was required

No. Validation found no defect affecting winners, branch state evolution, shared quotes, RNG results, checkpoints, or record completeness.

## 10. Why the run was not repeated

Every requested headline result is reconstructable from the existing raw JSONL. Formatting, charts, deeper aggregation, and evidence limitations are analysis concerns and do not justify regenerating 15,000 selections.

## 11. Analysis tool architecture

`tools/analyse_counterfactual_simulation_dataset.py` is a standalone, standard-library/Pillow reader. It does not import or invoke the simulator or production bot. It validates first, writes atomically to a designated output directory outside the source session, makes no network call, and supports validation-only operation.

## 12. Files and schemas consumed

The tool reads `session_manifest.json`, snapshot `manifest.json`, and, per run, `shared_quotes.jsonl`, three branch `selections.jsonl` files, `branch_comparison.jsonl`, and `counterfactual_checkpoint.json`. Schema version 1 and shared quote coupling are required.

## 13. Divergence findings

Production/editorial divergence consists of 941 episodes; median length is 1 post, mean 2.09, and maximum 22. Production/identity has 828 episodes; median length 1, mean 2.25, and maximum 17. Editorial/identity has 944 episodes; median length 2, mean 2.66, and maximum 26.

## 14. Reconvergence findings

940/941 production/editorial episodes, 822/828 production/identity episodes, and 937/944 editorial/identity episodes later return to the same visible winner. Reconvergence does not imply equal internal state.

## 15. Same-winner/different-state findings

There are 2,154 pairwise same-winner cases where compact-record observable signatures differ: prior winner, generated-spacing counter, or reconstructed cycle position. Full per-index image-history equality is unavailable in compact records, so this is a conservative observable-state result rather than a complete state proof.

## 16. Source-transition findings

Across all matched indices, production to editorial source transitions are 3,301 original-to-original, 262 original-to-generated, 267 generated-to-original, and 1,170 generated-to-generated. Production to identity transitions are 3,301, 262, 340, and 1,097 respectively. These matrices include agreements; changed-only basename transitions are in `detailed_metrics.json`.

## 17. Score-margin findings

Exact policy winner margins over branch-local runners-up are unavailable because candidate detail was disabled. `case_studies.json` ranks only the absolute score gap between independently selected branch winners and states this limitation. A new simulation with candidate detail would be required for genuine runner-up margin analysis.

## 18. Editorial policy findings

Editorial changes 1,966 matched winners. Replacements are 1,588 originals and 378 generated images. Selected-original adjustment mean is 1.08455, median 1.08409, range -1.688 to 3.18489; no selected adjustment hit the 4.0 cap. Net whole-run counts are tightly constrained by branch-local consumption: the largest net gainer is `t25.jpg` at +3, then `t36.jpg` at +2. This is materially different from repeated observational preference counts.

## 19. Identity policy findings

Identity changes 1,865 matched winners. Replacement winners are 1,231 originals and 634 generated images. Of changed production winners, 89 were `origin_quote_only` exclusions and 64 received the small penalty; there were no strong-penalty corpus records. Another 1,263 production winners were absent from the identity branch's already-diverged candidate set, illustrating trajectory effects. There were 11,271 excluded candidate appearances and no no-candidate failure.

## 20. Image diversity and concentration

| branch | unique | entropy | top-5 | top-10 | max image count |
|---|---:|---:|---:|---:|---:|
| Production | 143 | 6.96435 | 6.06% | 12.06% | 62 |
| Editorial | 141 | 6.95998 | 6.06% | 12.06% | 62 |
| Identity | 142 | 6.92136 | 6.16% | 12.20% | 64 |

Editorial preserves production-like concentration once it consumes its own winners. Identity modestly lowers generated share and entropy, but does not collapse diversity.

## 21. Reuse interval findings

Production reuse interval median/mean/min/max is 95/94.57/10/245 posts. Editorial is 95/94.02/6/175. Identity is 93/92.87/10/243. No branch has an interval below three posts. The one editorial interval of six is short but still compatible with cycle recovery and is exposed for review rather than labelled a violation.

## 22. Cycle/reset findings

Every branch records 60 image-cycle resets. Production and editorial use normal phase for all 5,000 indices. Identity uses 4,992 normal and eight forced-cycle-reset phases; no last-image fallback occurs. Generated-spacing transitions validate exactly, with no two-original-spacing violation.

## 23. Quote-level findings

Quotes are matched by construction at every index. `quote_metrics.csv` identifies all-three agreement, editorial-only divergence, identity-only divergence, both-policy divergence, and 273 cases where both policies independently select the same non-production alternative. Existing local records do not contain a trustworthy compact theme field, so no new semantic theme classification was invented.

## 24. Run-level variability

Editorial divergence ranges 24.4%-56.8% (median 39.2%, standard deviation 8.71 percentage points). Identity ranges 26.0%-61.2% (median 34.0%, standard deviation 9.42 points). All-three agreement ranges 30.8%-57.6% (median 43.2%). This is meaningful seed variability and argues against treating one trajectory as representative.

## 25. `t10.jpg` findings

`t10.jpg` is selected 60 times in each branch. It has zero editorial net gain relative to production and therefore is not dominant under branch-controlled state. Exact candidate appearances and gained/lost quote lists require candidate detail; its selection indices and reuse sequence are retained in `analysis_summary.json`.

## 26. `t18.jpg` findings

`t18.jpg` is also selected 60 times in each branch with zero editorial net gain. Like `t10.jpg`, its earlier observational prominence was repeated preference against production-controlled state, not repeated branch-controlled posting.

## 27. Diagnostic generated image findings

`tg_faf99f3030693b0a55f0116194551792f3c4ea51261d7310eca7fb4d33b667d5.png` appears in 7 production, 1 editorial, and 24 identity final candidate sets. Every appearance is cross-quote. Identity excludes all 24; no branch selects it. Since production never selected it in this dataset, there is no direct production-winning replacement case. Absence reasons cannot be reconstructed without trace data.

## 28. Ranked case studies

`case_studies.json` contains 10 largest and 10 smallest editorial selected-score gaps, 10 largest identity gaps, all 89 production `origin_quote_only` cases, 340 identity generated-to-original changes, 372 generated-to-generated changes, 1,321 editorial original-to-original changes, and 273 shared non-production alternatives. Explanations are restricted to recorded scores, policies, phases, counts, and source transitions.

## 29. Charts and contact sheets

Seven deterministic Pillow charts cover source share, entropy, top-10 share, divergence by run, episode lengths, cycle resets, and editorial net gainers. Three contact sheets cover editorial gainers, losers, and the three diagnostic images. They are under `analysis/counterfactual_canonical_v1/` and do not alter source images.

## 30. Tests added

`tests/test_analyse_counterfactual_simulation_dataset.py` covers complete data, missing/duplicate indices, mismatched quotes, incomplete checkpoints, invalid branches, non-finite scores, manifest hash rejection, deterministic output, transitions, episodes/reconvergence, same-winner/different-state detection, entropy, top-N share, reuse intervals, source immutability, no simulator/network call, and output confinement.

## 31. Focused test result

Analysis plus observational/counterfactual simulator tests: `55 passed in 187.46s`.

Dedicated analysis tests alone: `12 passed in 0.13s`.

## 32. Simulator test result

Included in the 55-test focused run: 19 counterfactual tests and 24 observational simulator tests, all passed.

## 33. Full-suite result

`685 passed, 1 skipped, 1 warning in 375.99s`. The warning is the existing Starlette/httpx deprecation warning.

## 34. Py-compile result

PASS for the analyzer, simulator, and all three dedicated analyzer/simulator test files.

## 35. Git diff check result

`git diff --check`: PASS, no output.

## 36. Production-log isolation

The production log grew by 15,135 bytes through ordinary live activity. The appended interval contains zero `pytest`, `/tmp/pytest-`, simulator, counterfactual, dummy, loopback, or `127.0.0.1` markers. It was not deleted, truncated, or rotated.

## 37. xAI calls, tokens, and cost

0 calls, 0 tokens, cost 0. No X call occurred.

## 38. Files created

- `tools/analyse_counterfactual_simulation_dataset.py`
- `tests/test_analyse_counterfactual_simulation_dataset.py`
- `counterfactual_canonical_dataset_manifest.json`
- `counterfactual_canonical_dataset_analysis_report.md`
- derived files under `analysis/counterfactual_canonical_v1/`

## 39. Files modified

No production file was modified. The pre-existing uncommitted counterfactual simulator modification remains in `tools/simulate_regular_post_futures.py`; this analysis pass did not change it.

## 40. Git commits

Two separate commits were made:

- `b32498a` - `Add counterfactual policy branch simulator`
- `0715a21` - `Add counterfactual simulation analysis tooling` (amended only to finalise this handover; final hash recorded below after amendment)

## 41. Push result

The simulator commit pushed normally as `e711036..b32498a master -> master`. The analysis commit was then pushed normally to the same upstream. No force-push occurred.

## 42. Final Git status

Task-specific source, tests, manifest, reports, charts, and derived tables are committed. Raw `simulation_runs/` and unrelated pre-existing operational artifacts remain untracked. Nothing is staged.

## 43. Recommendations supported by this dataset

- Treat editorial's 39.32% matched divergence as frequent ranking change, but not as a diversity collapse: branch-local entropy and top-10 share remain essentially production-like.
- Treat identity's 37.30% matched divergence primarily as trajectory divergence; only 153 changed production winners were directly origin-only or small-penalty cases at the same index.
- The identity policy modestly reduces generated share from 28.74% to 27.18% and preserves broad image diversity.
- `t10.jpg` and `t18.jpg` are not branch-controlled dominant images.
- Review the 89 direct origin-only interventions and 273 both-policy alternative cases before considering any live policy.

## 44. Questions requiring a new simulation

Exact runner-up margins, full candidate rankings, per-index complete history equality, detailed reasons for image absence, and semantic candidate-level signals require a new run with candidate detail or trace-image recording. They do not invalidate this dataset and are not grounds to repeat it casually.

## Explicit confirmations

The 20 x 250 simulation was not rerun. No X call, X media upload, X post, X reply, xAI call, production state edit, receipt edit, config edit, production lock acquisition, production-log pollution, live bot restart, or signal occurred. No force-push occurred.
