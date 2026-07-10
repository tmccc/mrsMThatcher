# Counterfactual Policy Branch Simulator Implementation Report

## 1. Executive summary

The audited observational simulator baseline was committed and pushed first as commit `e711036`. The simulator was then extended, without modifying production code, with an uncommitted `--mode counterfactual` experiment containing three independently evolving image-state branches:

- production control;
- original editorial policy;
- generated identity policy.

Shared-quote mode supplies one production-selected quote and virtual timestamp to all branches at each index. Each branch independently rebuilds its exact candidate set from its own image history, generated spacing, previous-image boundary, cycle state, and policy winner. The 5,000-index evidence run made 15,000 branch selections in 939.071 seconds with zero X/xAI/network calls.

Headline result: editorial branch divergence from production was 39.32%, but `t10.jpg` and `t18.jpg` were each actually selected only 60 times, exactly their production counts, once editorial winners consumed their own history. Identity divergence was 37.30%; its generated share fell from 28.74% to 27.18%.

## 2. Baseline observational commit

- Commit: `e711036`
- Subject: `Add audited production-parity simulator`
- Push: `229e3ff..e711036 master -> master` to `https://github.com/tmccc/mrsMThatcher.git`
- Upstream after push: `master...origin/master`
- Files:
  - `tools/simulate_regular_post_futures.py`
  - `tests/test_simulate_regular_post_futures.py`
  - `accelerated_production_parity_simulator_implementation_report.md`
  - `accelerated_production_parity_simulator_audit_report.md`

The cached baseline patch contained exactly those four additions: 2,761 lines. No simulation output or unrelated file was committed.

## 3. Files inspected

- current `mrsMThatcher2.py` production selector, recovery, scoring, shadow, spacing, schedule, and receipt-transition helpers;
- audited simulator source/tests/reports;
- current editorial metadata and generated identity audit through production loaders;
- observational evidence/control outputs;
- all new counterfactual JSONL, checkpoint, summary, and report outputs;
- selector, spacing, cycle-recovery, and both shadow test suites.

## 4. Existing simulator architecture

The existing observational mode remains the default. It imports the production module in test mode, snapshots immutable inputs, redirects mutable paths, blocks network/posting/locks/receipts, runs the real selector, captures exact scored lists, advances private production-controlled state, and atomically checkpoints JSONL/RNG/state/history.

Its core records and reporting schema remain unchanged. New CLI options are additive.

## 5. Counterfactual architecture

`--mode counterfactual --quote-coupling shared` creates three deep-copied branch states from one snapshot. At each post index:

1. production branch runs the audited full quote/image selector and establishes the shared quote/time;
2. editorial and identity branches independently call production image discovery/filtering/scoring for that exact quote using their own state;
3. each policy ranks its own exact scored list;
4. each branch consumes only its own winner;
5. all branch JSONL records and one comparison record are flushed;
6. one atomic checkpoint commits all three states at the same index.

No branch borrows a candidate set from another after divergence.

## 6. Quote-coupling design

Only `shared` coupling is implemented in this task. The production control runs the full production pair selector; its selected quote is copied to the two policy branches. Quote-used history is synchronised from the production/shared quote trajectory, then stored independently in each checkpoint.

This isolates image-policy consequences while preserving an exact production control. A rare production quote retry could include production-side image-cycle effects before the chosen shared quote; no quote retries occurred in either audited 5,000-index control. Independent quote futures remain future work.

## 7. RNG design

The smallest robust design is independent deterministic branch streams:

- production uses the unmodified run seed and exact production RNG ordering;
- editorial and identity use 64-bit seeds derived by SHA-256 from run seed plus branch name;
- policy selection consumes one branch-local random choice only for an actual maximum-score tie;
- the production branch samples shared quote/meme schedule delays; those exact values update all branch schedule fields without consuming policy tie streams;
- candidate detail, reporting, and policy scoring consume no RNG;
- every branch RNG state is checkpointed.

This prevents one branch from perturbing another. It does mean an exact policy-score tie can resolve differently across branch streams; this is documented stochastic variation, not hidden cross-branch consumption.

## 8. Production branch design

Production calls `select_with_production_recovery(..., evaluate_shadows=False)`. Disabling observational event calculation avoids unnecessary work; it does not alter candidates, scores, RNG, or the winner. The exact production `scored` object is still captured through no-op observer hooks.

The control uses production quote/image retries, ties, spacing, histories, cycle reset, last-image fallback, and schedule RNG unchanged.

## 9. Editorial branch design

For every exact branch-local candidate:

- original: `policy_score = production baseline + current editorial adjustment`;
- generated: `policy_score = production baseline`.

The mixed pool remains intact. The current production analysis loader, 0.32 weight, 4.0 cap, dimensions, affinities, utility, and penalties are reused through `original_editorial_shadow_score()`. The winner then updates editorial branch history/state.

## 10. Identity branch design

The branch calls the current categorical production shadow helper:

- originals unchanged;
- generated origin match unchanged, including origin boost;
- unrestricted cross-quote unchanged;
- small/strong penalties subtract configured 6/15 points;
- `origin_quote_only` cross-quote candidates are excluded only from identity branch competition.

The winner updates identity branch state. No audit classification or production score was changed.

## 11. Branch-state isolation

State dictionaries, image histories, quote histories, and RNG states are deep-independent. Tests mutate nested editorial state/history and prove production/identity copies remain unchanged. At the first real divergence, each branch contains its own winner and not the other branch's unconsumed winner.

## 12. Branch state-transition parity

Every branch uses the audited `apply_simulated_success()` transition:

- winner quote/image marked used;
- last regular image updated;
- generated spacing reset/incremented by the production helper;
- last quote epoch and next quote schedule updated;
- shared production-sampled scheduling fields applied;
- only branch-local objects mutated.

Tests explicitly prove generated spacing can be blocked in production while remaining allowed in editorial/identity.

## 13. Recovery behaviour

Production retains its audited normal / forced reset / last-image fallback sequence.

For a shared quote, policy branches try:

1. normal branch-local image selection;
2. branch-local forced eligible-cycle reset;
3. last-image fallback only when forced reset recorded a boundary exclusion.

If policy exclusions plus production eligibility leave no candidate after all phases, the simulated index fails loudly; it never silently skips. In the large run, identity used forced reset eight times and last-image fallback zero times. All 5,000 indices completed, so `origin_quote_only` never exhausted all recovery.

## 14. Tie handling

Actual policy branches use branch RNG to choose among exactly tied maximum policy scores. They do not retain the production winner as the observational logger does. Same seed reproduces the tie result; different seeds can select different tied winners; branch RNG states are isolated.

## 15. Snapshot design

Counterfactual mode reuses the audited immutable snapshot architecture: two matching stable grouped reads across a quiescence window, receipt exclusion, state/history relationship validation, copied-file SHA-256 verification, read-only snapshot, source hashes, and no production lock.

Smoke created the snapshot; medium, large, and observational control copied that exact snapshot.

## 16. Resume/checkpoint design

Each run has one authoritative atomic counterfactual checkpoint containing the same completed index for production, editorial, and identity, plus every state/history/RNG value and next virtual epoch.

All five JSONLs (shared quote, three branches, comparison) are fsynced before checkpoint commit. Resume truncates uncommitted trailing rows to the checkpoint and rejects missing committed rows or non-contiguous indices.

Tests interrupt index 37:

- after editorial computed but before any committed index;
- after comparison append but before checkpoint;
- immediately after checkpoint commit.

All resumed 100-index outputs and final branch states are byte-equivalent to uninterrupted execution, with no duplicate index.

## 17. Safety guards

The audited hard failures remain active for high-level quote/meme/mention/quote-reply functions, `create_post`, upload, Grok, lock acquisition, receipt writes/removals, requests methods, TCP connect/create/connect_ex, and UDP sendto. `PrivateWriter` confines all mutable paths to the marked session. Production import/test logs never target `mrsMThatcher.log`.

## 18. Output schema

Counterfactual sessions contain:

```text
counterfactual/runs/run_NNNN/
  shared_quotes.jsonl
  production/selections.jsonl
  editorial/selections.jsonl
  identity/selections.jsonl
  branch_comparison.jsonl
  counterfactual_checkpoint.json
counterfactual_simulation_summary.json
counterfactual_simulation_report.md
```

Branch rows record phase, winner/source, baseline/policy score, editorial/identity detail, candidate counts, spacing before/after, cycle reset, diagnostic image, and optional candidate detail. Comparison rows record every winner/source, pairwise/all agreement, first-divergence flags, cumulative divergence counts, and matched observational preferences.

## 19. Aggregate metrics

Summary/report includes agreement, per-run first divergence, source mix, origin/cross-quote counts, phases/cycles, unique images, Shannon entropy, top-5/top-10 share, maximum count, reuse-interval histogram/minimum/median, policy effects, diagnostic image, five-number across-run variation, and observational-versus-counterfactual rates.

These diversity metrics are now meaningful policy-trajectory metrics because each branch consumes its own winners.

## 20. Tests added or changed

Created `tests/test_counterfactual_policy_branches.py`, covering:

- stable/separate seed derivation;
- editorial full mixed-pool semantics;
- all identity policy categories and origin unrestricted behaviour;
- actual tie RNG;
- identity recovery through final fallback;
- deep branch isolation;
- production/observational control parity;
- shared quote/time;
- post-1 divergence effects on later candidate sets;
- winner-only branch history mutation;
- independent generated spacing;
- `none/top10/full` invariance;
- three 100-index resume interruption points;
- branch-based summary calculations.

The existing observational tests remain unchanged and green.

## 21. Exact focused-test results

```text
tests/test_counterfactual_policy_branches.py
19 passed in 174.73s

tests/test_simulate_regular_post_futures.py
24 passed in 48.09s

tests/test_original_editorial_shadow_scoring.py
41 passed in 0.40s

tests/test_generated_identity_policy_shadow_scoring.py
34 passed in 0.40s

selector/spacing/recovery subset
33 passed, 298 deselected in 0.73s
```

## 22. Exact full-suite result

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
673 passed, 1 skipped, 1 warning in 416.41s
```

The warning is the existing Starlette/httpx deprecation warning.

## 23. Py-compile result

PASS for:

```text
tools/simulate_regular_post_futures.py
tests/test_simulate_regular_post_futures.py
tests/test_counterfactual_policy_branches.py
```

## 24. Git diff check

`git diff --check`: PASS, no output.

## 25. Production-log isolation

The production-log size/mtime was recorded before tests/runs. Newly appended content contains ordinary live process records only. Searches found no pytest path, simulator/counterfactual marker, synthetic ID, dummy credential, or loopback endpoint. The log was not deleted, truncated, or rotated.

## 26. Smoke run

- 1 run x 20 matched indices
- 60 branch selections
- 5.287 seconds, 11.35 branch selections/s
- output: `simulation_runs/counterfactual_smoke_1x20_20260710/`
- first editorial/identity divergence from production: indices 4 / 6

## 27. 3 x 100 run

- 300 matched indices
- 900 branch selections
- 61.220 seconds, 14.70 branch selections/s
- output: `simulation_runs/counterfactual_medium_3x100_20260710/`
- production/editorial/identity generated share: 32.0% / 32.33% / 31.67%
- editorial/identity divergence: 24.67% / 14.0%

## 28. 20 x 250 run

- 5,000 matched indices
- 15,000 branch selections
- 939.071 seconds
- output: `simulation_runs/counterfactual_evidence_20x250_20260710/`
- all 20 runs completed without policy exhaustion

## 29. Performance

- throughput: 15.97 branch selections/second
- approximately 5.32 complete three-branch matched indices/second
- no sleep and zero xAI calls

## 30. Production branch parity

A separate same-snapshot 20 x 250 observational control completed 5,000 selections in 508.556 seconds. All 5,000 production-branch records matched quote, virtual timestamp, phase, candidate counts, score, winner, origin, spacing, and cycle fields exactly.

Canonical control/branch hash:

```text
a8f3a33b3c48f3b5c4115d589e34d2356e85e4b4d7fff4e24e99458abca3c3a4
```

## 31. Editorial branch findings

- production agreement: 60.68%; divergence: 1,966 / 5,000 = 39.32%
- observational mixed-pool preference change on production trajectory: 13.62%
- observational comparable-original change: 17.46% of 3,563
- replacement source on divergent indices: 1,588 original, 378 generated
- generated share: 28.64%, versus production 28.74%
- generated origin/cross-quote: 22 / 1,410
- entropy: 6.9600 bits, versus production 6.9643
- top-10 share: 12.06%, equal to production
- unique images: 141, versus production 143
- image-cycle resets: 60; all selections completed in normal outer phase
- selected-original adjustment mean/median: 1.0846 / 1.0841
- cap hits: 0

The much larger counterfactual divergence than one-step observational preference is the downstream effect of consuming different winners and presenting different future candidate sets.

## 32. Identity branch findings

- production agreement: 62.70%; divergence: 1,865 / 5,000 = 37.30%
- observational mixed-pool change on production trajectory: 4.0%
- observational policy-relevant change: 11.29% of 1,771
- replacement source on divergent indices: 1,231 original, 634 generated
- generated share: 27.18%, versus production 28.74%
- generated origin/cross-quote: 24 / 1,335
- entropy: 6.9214 bits, versus production 6.9643
- top-10 share: 12.20%, versus production 12.06%
- unique images: 142, versus production 143
- production candidates excluded as origin-only in identity state: 89
- excluded identity candidate appearances: 11,271
- phases: 4,992 normal, 8 forced-cycle-reset, 0 final fallback
- image-cycle resets: 60

Under its own trajectory, identity divergence usually replaces the production winner with an original (1,231 cases), not another generated image. This differs from the observational result because histories and candidate sets diverge cumulatively.

## 33. `t10.jpg` / `t18.jpg` concentration

Production selected each 60 times. Editorial also selected each exactly 60 times. Neither exceeds the editorial maximum of 62 selections (`t51.jpg`).

Therefore the observational repeated-preference dominance disappears when editorial winners consume their own state. Editorial entropy/top-10 share remain nearly identical to production rather than collapsing toward a few portraits.

## 34. Diagnostic `tg_faf99...`

| branch | candidate appearances | cross-quote | exclusions | wins |
|---|---:|---:|---:|---:|
| production | 7 | 7 | 0 | 0 |
| editorial | 1 | 1 | 0 | 0 |
| identity | 24 | 24 | 24 | 0 |

There were no origin-quote appearances and no branch selected the image. Identity policy correctly excluded every one of its 24 branch-local cross-quote appearances.

## 35. Observational vs counterfactual

Observational scoring asks for the immediate preference on production state. Counterfactual divergence includes that immediate policy effect plus all later eligibility changes caused by earlier branch winners.

- editorial: 17.46% comparable observational original changes, but 39.32% actual branch divergence;
- identity: 11.29% policy-relevant observational changes, but 37.30% actual branch divergence over all matched indices.

The denominators differ deliberately and are printed explicitly. Counterfactual rates are trajectory comparisons, not stronger versions of the observational denominator.

## 36. Across-run variability

- first production/editorial divergence: index 1 min, 11.5 median, 25 max
- first production/identity divergence: index 3 min, 12 median, 36 max
- editorial divergence rate: 24.4% min, 32.1% lower quartile, 39.2% median, 46.2% upper quartile, 56.8% max
- identity divergence rate: 26.0% min, 29.5% lower quartile, 34.0% median, 45.5% upper quartile, 61.2% max
- editorial generated share: 28.0%-29.2%, median 28.8%
- identity generated share: 26.4%-28.0%, median 26.8%

The direction of the diversity/source findings is stable, while exact divergence rates vary materially by seed as expected from alternate futures.

## 37. Caveats

Shared quotes isolate image policy but are selected by the production control. Policy branches use independent tie streams; exact-score ties can therefore add legitimate branch stochastic variation. Independent quote futures are not implemented.

These are modelled futures under current corpus, metadata, code, state, schedules, and policy. They do not predict users, engagement, new assets, future code/config, or X behaviour.

## 38. Files created

- `tests/test_counterfactual_policy_branches.py`
- `counterfactual_policy_branch_simulator_implementation_report.md`
- counterfactual and same-snapshot control outputs under `simulation_runs/`

## 39. Files modified

- `tools/simulate_regular_post_futures.py`

No production source, test outside the new dedicated file, config, state, history, receipt, or log was modified.

## 40. Git diff stat

At final validation before this report:

```text
tools/simulate_regular_post_futures.py | 910 lines changed (885 insertions, 25 deletions)
```

The new dedicated test/report are untracked and therefore not included by ordinary `git diff --stat`.

## 41. Git status and explicit confirmations

Baseline branch is synchronised at `e711036` on `origin/master`. New counterfactual work remains unstaged and uncommitted:

```text
 M tools/simulate_regular_post_futures.py
?? tests/test_counterfactual_policy_branches.py
?? counterfactual_policy_branch_simulator_implementation_report.md
?? simulation_runs/  (contains pre-existing and new offline outputs)
```

Other untracked analysis/backup artifacts pre-dated this task and remain untouched.

Full final `git status --short`:

```text
 M tools/simulate_regular_post_futures.py
?? .generated_image_analysis.lock
?? .mrs_asset_analysis.lock
?? analyse_mrs_assets_xai.py
?? analyse_mrs_assets_xai_v2.py
?? analyse_mrs_assets_xai_v3.py
?? check_and_fix_thatcher_faces_xai_revised.py
?? counterfactual_policy_branch_simulator_implementation_report.md
?? digest063_analysis.txt
?? digest_latest_state_window_boundary_fix_report.txt
?? generate_first10_from_quote_analysis.py
?? generate_openai_low_first10.py
?? generated_image_analysis.json.before_face_corrections
?? generated_image_observability_report.txt
?? generated_image_pool_review_report.txt
?? generated_review_approved_images/grok_face_corrected/
?? generated_review_approved_images/grok_face_correction_manifest_v2.json
?? generated_review_approved_images/grok_face_rejected_candidates/
?? generated_review_approved_images/pre_face_correction_backup_20260709_033026/
?? grok_image_test_first10/
?? images.tar
?? images_used.json.pre-basename-migration
?? images_used.pickle.pre-deployment.
?? lines_used.json.pre-hash-migration
?? lines_used.pickle.pre-deployment.
?? mrsMThatcher.local.json.before_generated_pool_20260709_034909
?? mrsMThatcher.local.json.before_generated_spacing_20260709_115742
?? openai_generated_quote_images/
?? openai_image_test_first10/
?? original_editorial_matching_backtest.json
?? pre_generated_identity_shadow_deployment_20260710_095701/
?? pre_original_editorial_shadow_deployment_20260710_070852/
?? pre_reply_humour_prompt_deployment_20260710_112259/
?? pre_reply_spacing_30min_deployment_20260710_153705/
?? review/
?? simulation_runs/
?? tests/test_counterfactual_policy_branches.py
```

Explicit confirmations:

- no X call, media upload, post, or reply occurred;
- xAI call count: 0;
- no external API call occurred;
- no production state, history, receipt, config, lock, or log was modified by the experiment;
- the production lock was not acquired;
- no production-log pollution was found;
- the live bot/wrapper were not restarted or signalled;
- no production policy was changed or deployed;
- no counterfactual implementation/test/report file is staged;
- no counterfactual commit was made;
- no counterfactual work was pushed.
