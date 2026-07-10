# Accelerated Production-Parity Simulator Audit Report

## 1. Executive verdict

The simulator's normal-execution production-selection parity is credible and directly verified. It calls the current production selector, uses the selector's exact phase-specific candidate list, and applies all state transitions that affect later regular quote/image selection.

Three defects were confirmed and corrected:

1. The original editorial entropy comparison used unmatched populations and was conceptually invalid.
2. Resume state was not transactionally aligned with JSONL progress; a crash between writes could duplicate an index or resume from state ahead of progress.
3. The process network guard did not cover `socket.connect_ex()` or UDP `sendto()`.

Snapshot consistency was also hardened from one stable grouped read to two identical grouped snapshots across a quiescence window, receipt exclusion, and logical relationship validation.

The 17.79% editorial winner-change rate and 10.09% identity-policy change rate remain valid for their stated observational denominators. Neither predicts the future produced by deploying a shadow policy because state continues to evolve using production winners.

## 2. Files inspected

- `mrsMThatcher2.py`
- `tools/simulate_regular_post_futures.py`
- `tests/test_simulate_regular_post_futures.py`
- both existing shadow test suites and relevant selector/cycle tests
- `accelerated_production_parity_simulator_implementation_report.md`
- all session manifests, JSONL records, summaries, reports, checkpoints, and private state under `simulation_runs/`
- live state/history/config filenames, receipt presence, lock, process list, and production-log intervals

The exact first 5,000-selection run was `simulation_runs/evidence_20x250_20260710/`. The corrected run is `simulation_runs/audit_evidence_20x250_20260710/`.

## 3. Exact simulator architecture

The standalone tool imports `mrsMThatcher2.py` in test mode with dummy loopback credentials and a `/tmp` import base, redirects all read paths to an immutable snapshot, redirects mutable paths to a private run directory, installs hard failures for posting/network/lock/receipt operations, and redirects logging to the session.

For each post it sets production `now_epoch()` to the virtual epoch, calls the real production pairing function through a recovery wrapper, captures both real shadow events, records the result, applies a private hypothetical-success transition, advances virtual time using production scheduling RNG, and atomically checkpoints private state.

## 4. Production code reuse findings

Yes, the simulator calls the current `choose_regular_quote_image_pair()` directly. No production selector or scorer was copied into the simulator. No production code was refactored or modified by either implementation pass.

A corrected old-snapshot 250-post run exactly matched the old run. The complete corrected 5,000-run canonical core also exactly matched the first run:

```text
old: 068b67ed6c079198effecb621c6548a92246bd4b09d718433f54896190983379
new: 068b67ed6c079198effecb621c6548a92246bd4b09d718433f54896190983379
```

The canonical core covers quote, virtual time, candidate counts, scores, production winner, phase, origins, cycle/spacing fields, and both shadow payloads.

## 5. Quote-selection parity findings

Production `quote_candidates_for_current_cycle()` and `choose_unused_line_candidate()` are called unchanged. Therefore the simulation uses current quote-analysis validation, SHA-256 history, weighted random selection, quality weighting, seasonal multipliers/exclusions, duplicate suppression, attempted-quote exclusion, cycle exhaustion/reset, and retry semantics.

`current_datetime()` delegates to the overridden production `now_epoch()`, so quote seasonal logic receives virtual time. Quote selection and scheduling use the production module's `random` state.

## 6. Image-selection parity findings

Production `choose_matched_unused_image()` is called unchanged. Original/generated discovery, generated spacing, metadata/hash checks, seasonality, image-used cycles, previous-image boundary, strong mismatch exclusion, score components, generated origin matching/boost, and random tie selection are therefore production code.

The simulator does not reconstruct a full pool after selection. Candidate detail is reporting-only over the exact returned `scored` list.

## 7. Recovery-phase parity findings

`select_with_production_recovery()` mirrors `post_random_quote()`:

1. normal call;
2. on `NoViableQuoteImagePair`, one `force_image_cycle_reset=True` call;
3. only when the reset exception reports the last-image exclusion, one final forced reset with `avoid_last_image_at_cycle_boundary=False`.

The phase is assigned inside production selection as `normal`, `forced_cycle_reset`, or `last_image_fallback`. Existing production tests exercise forced reset and final fallback. The audited large RNG futures needed no outer recovery pass, although production performed 60 normal eligible-cycle resets.

## 8. State-transition parity table

| state/history effect | production | simulator | exact selection parity? |
|---|---|---|---|
| selected quote used | adds quote SHA after confirmed post | adds same SHA after hypothetical success | yes |
| selected image used | adds basename after confirmed post | adds same basename | yes |
| quote cycle reset | selector mutates live set | same selector mutates private set | yes |
| image cycle reset | selector mutates live set | same selector mutates private set | yes |
| `last_regular_image_filename` | selected basename | selected basename | yes |
| generated spacing | production helper | same helper | yes |
| `last_quote_post_epoch` | confirmation epoch | virtual successful-post epoch | yes |
| `next_quote_post_epoch` | production helper and sampled delay | same helper, delay, and RNG ordering | yes |
| quote/meme schedule RNG | sampled after winner | sampled after winner in same order | yes |
| `last_main_post_id` | X post ID | deterministic synthetic ID | equivalent; ID is not read by regular selection |
| cache/recent own posts | updated | omitted | operational only; not read by regular selection |
| receipts/fsync/backups | durable external transaction | one private checkpoint | intentionally different; no external side effect exists |

Tests compare the simulator transition against `apply_regular_post_receipt()` for original and generated images. Histories and every selection-relevant field match.

## 9. Exact shadow candidate-set proof

Production chooses its real winner before calling either observer. The simulator temporarily wraps the two production shadow log functions and records the Python `id()` of each `scored` argument. It fails if the IDs differ. The selected image object must also be a member of that same list. Dedicated tests verify identity, candidate order, scores, winner, phase, origin match, and origin boost.

## 10. Counterfactual-state limitation

The initial interpretation concern is correct. The simulator advances only the production-controlled trajectory.

If production selects `t49.jpg` and editorial shadow prefers `t10.jpg`, production state marks `t49.jpg` used. `t10.jpg` remains available and may be preferred repeatedly. Consequently:

- observational preference counts are not hypothetical post counts;
- observational shadow entropy is not deployed-policy diversity;
- net gains are not the distribution of an editorial-controlled future.

The generated identity shadow has the same limitation whenever its winner differs.

## Why counterfactual evolving branches are a separate experiment

A production branch must consume production winners. An editorial branch would need to consume editorial winners and then rebuild its own future candidate sets. An identity branch would need to do the same with identity-policy winners. Those branches diverge immediately in used history, spacing, previous-image boundaries, future scores, and sometimes cycle timing. They were deliberately not implemented in this audit.

## 11. Original entropy audit

The first report compared all 5,000 all-source production winners with 4,981 best-original shadow preferences. That was not a valid shared-population comparison. The reported 6.971 production entropy versus 5.909 editorial entropy and 12.04% versus 28.45% top-10 share must not be presented as a matched policy comparison.

The generator now reports whole-production distribution separately and labels every matched denominator.

## 12. Corrected matched original metrics

On the 3,547 observations where production selected an original and an original shadow rank therefore exists:

| matched distribution | entropy | top-10 share |
|---|---:|---:|
| production original winner | 6.0641 bits | 16.97% |
| editorial shadow preference | 5.9181 bits | 28.25% |

Winner changes remain 631 / 3,547 = **17.7897%**; unchanged observations are 2,916.

An optional second matched population covers 4,981 observations with an original candidate:

| best-original distribution | entropy | top-10 share |
|---|---:|---:|
| baseline best original | 6.0400 bits | 20.58% |
| editorial-adjusted best original | 5.9090 bits | 28.45% |

These are valid opportunity-level comparisons, still not policy-controlled futures.

## 13. Repeated shadow-preference analysis

The audit counts a repeat when an image is preferred again before production itself consumes that image. There were:

- 777 repeated observational preferences across all original-candidate opportunities;
- 184 repeated preferences within comparable production-original observations.

Top repeated all-opportunity preferences were `t10.jpg` 133, `t18.jpg` 107, `t66.jpg` 56, `t37.jpg` 44, and `t68.jpg` 30.

Within matched production-original observations, `t10.jpg` had 60 production wins and 160 shadow preferences (net +100); `t18.jpg` had 60 and 159 (net +99). They are genuinely strong observational preferences under the editorial layer, but much of their repeated prominence is enabled by production-controlled state. The data cannot say they would be posted 160/159 times or remain equally dominant in an editorial-controlled branch.

## 14. Generated identity metrics audit

The policy-relevant denominator is valid: an observation is included only when at least one cross-quote generated candidate receives a penalty or exclusion. Winner changes remain 187 / 1,854 = **10.0863%**. Replacements were 159 generated images and 28 originals.

Production and identity-shadow concentration now use the same complete 5,000-observation population:

| matched distribution | entropy | top-10 share |
|---|---:|---:|
| production winner | 6.9711 bits | 12.04% |
| identity shadow preference | 6.9202 bits | 12.24% |

This comparison is mathematically matched and shows only a small observational concentration change. It remains non-counterfactual because shadow winners do not consume their own histories.

## 15. Diagnostic `tg_faf99...` eligibility investigation

The new `--trace-image BASENAME` lifecycle diagnostic records one mutually exclusive final reason per simulated post. Across 5,000 posts:

- image discovered: 5,000;
- generated spacing allowed: 2,097;
- spacing blocked: 2,903;
- absent because still used in current image cycle: 2,094;
- stale metadata: 0;
- seasonal exclusion: 0;
- strong visual mismatch at a phase where otherwise available: 0;
- final scored candidate set: 3;
- origin-quote opportunities: 16;
- cross-quote opportunities: 4,984;
- scored origin/cross-quote: 0 / 3;
- production wins: 0;
- identity-shadow exclusions: 3.

The rarity is therefore explained by starting history and generated spacing, not stale metadata or mismatch. The image had recently been used in real production and remained used until eligible image-cycle resets. The three times it became available for the selected quote, it reached the exact scored list as a cross-quote candidate and was excluded only in the identity shadow.

## 16. Snapshot consistency audit

The first grouped stat-before/read/stat-after method prevented torn individual reads and detected changes during the complete grouped read. A real residual window existed because regular-post histories and state are persisted sequentially.

Production writes a recovery receipt before protected regular-post persistence and removes it only after quote history, image history, and state are durable. That provides a useful consistency signal.

## 17. Snapshot changes made

Snapshot capture now:

1. rejects capture while any production regular, meme, or confirmed-reply receipt exists;
2. reads all three mutable files as a stable group twice across a 100ms quiescence window;
3. requires byte-identical grouped snapshots;
4. rejects a last regular image absent from image history;
5. rejects a next quote epoch not later than the last quote epoch;
6. checks again that no receipt appeared during capture;
7. verifies all manifest hashes after copying a prior immutable snapshot.

This is adequate without pausing or locking production. It greatly narrows but cannot mathematically create a transaction across unrelated files. A pathological non-regular external writer could still violate an unmodelled relationship.

The fresh audit snapshot hashes exactly matched the pre-capture hashes recorded for all four live files.

## 18. Resume audit

The original write order was record append, three independent state/history writes, then progress. This was a confirmed correctness defect under interruption.

The authoritative checkpoint now atomically contains completed index, virtual time, RNG state, state, and both histories. JSONL is fsynced before checkpoint commit. On resume, JSONL is reconciled to the checkpoint: an uncommitted trailing record is removed, a missing committed record is a hard error, and indices must be contiguous.

Human-readable private state/history mirrors are written after checkpoint and are not authoritative for resume.

## 19. Interrupted-vs-uninterrupted result

Tests ran 100 posts uninterrupted and separately interrupted after record append at post 37, then resumed to 100. `selections.jsonl` and final checkpoint were byte-equivalent.

A second test interrupted immediately after checkpoint commit at post 17, resumed to 40, and matched uninterrupted output with exactly indices 1..40 and no duplicate.

## 20. RNG audit

RNG consumers are production weighted quote selection, production image tie selection, quote interval sampling, and optional meme interval sampling. Shadow tie logic is deterministic and consumes no RNG. Reporting and lifecycle tracing consume none.

The complete RNG state is in the authoritative checkpoint. Tests prove shadow neutrality and state round-trip.

## 21. Candidate-detail invariance

For identical snapshot, seed, start time, and 20-post future, `none`, `top10`, and `full` produced canonically identical records after excluding only the requested `candidate_detail` payload. Final state and RNG-driven future were unchanged.

## 22. Safety audit

Confirmed safeguards:

- high-level quote, meme, mention, and quote-reply paths fail immediately;
- `create_post`, media upload, Grok reply generation, lock acquisition, receipt writes/removals, and `requests` methods fail immediately;
- TCP `connect`, `connect_ex`, `create_connection`, and UDP `sendto` are process-blocked;
- all production persistence helpers route through `PrivateWriter` and reject paths outside the session;
- output in the project is allowed only below `simulation_runs/`;
- overwrite/resume require a simulator marker;
- production import log goes to `/tmp`, then simulation logging goes to the session;
- no production receipt is copied or activated;
- no X/xAI credentials are required.

The missing `connect_ex`/`sendto` coverage was a real defence-in-depth gap and is fixed. No code path in the first run was found to have used it.

## 23. Production side-effect evidence

Retrospective proof is necessarily bounded because the live bot legitimately writes state/logs concurrently.

- First and audit snapshots record exact input hashes and session-local paths.
- Image history, quote history, and local config hashes remained unchanged across the audit activity.
- `bot_state.json` changed while the live bot ran; production logs show ordinary main-loop/reply-state activity, so this cannot be attributed solely by hash. Simulator architecture never targets that path.
- No production receipt existed at final inspection.
- Production lock still contained live child PID `3046050`; wrapper PID remained `3631076`.
- No `pytest`, temporary test path, simulator marker, synthetic ID, dummy credential, or loopback endpoint appeared in newly appended production-log intervals.
- Logs contain ordinary live loop ticks only. They were not deleted, truncated, or rotated.

No evidence of a simulator/test production side effect was found.

## 24. Tests added or changed

Added or strengthened tests for:

- production receipt versus simulated-success state parity for original/generated images;
- double-snapshot agreement and logical inconsistency rejection;
- crash after JSONL append and crash after checkpoint;
- uninterrupted/resumed byte equivalence and contiguous indices;
- candidate-detail invariance;
- JSONL reconciliation;
- matched metric denominators;
- repeated-preference reset semantics;
- TCP `connect_ex` and UDP `sendto` blocking;
- expanded high-level post/reply/receipt safety guards.

## 25. Exact focused test results

```text
tests/test_simulate_regular_post_futures.py
24 passed in 41.83s

tests/test_original_editorial_shadow_scoring.py
41 passed in 0.46s

tests/test_generated_identity_policy_shadow_scoring.py
34 passed in 0.47s

focused selector/spacing/cycle subset
33 passed, 298 deselected in 0.67s
```

## 26. Exact full-suite result

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
654 passed, 1 skipped, 1 warning in 230.44s
```

The warning is the existing Starlette/httpx deprecation warning.

## 27. Py-compile result

PASS:

```text
python3 -m py_compile tools/simulate_regular_post_futures.py tests/test_simulate_regular_post_futures.py
```

## 28. Git diff check

`git diff --check`: PASS, no output.

## 29. Smoke rerun

`1 x 20`, seed 1000, fixed start time, fresh double-agreement snapshot: 20 selections in 3.489 seconds (5.73/s). Output: `simulation_runs/audit_smoke_1x20_20260710/`.

## 30. 3 x 100 rerun

`3 x 100`, seeds 1000-1002, same snapshot/start: 300 selections in 33.805 seconds (8.87/s). Output: `simulation_runs/audit_medium_3x100_20260710/`.

## 31. 20 x 250 rerun

`20 x 250`, seeds 1000-1019, same snapshot/start, top-10 detail and diagnostic tracing: 5,000 selections in 550.970 seconds. Output: `simulation_runs/audit_evidence_20x250_20260710/`.

All 5,000 canonical core records match the first large run exactly despite a later live-state snapshot, because the selection-relevant histories/config were unchanged.

## 32. Corrected performance

- selections: 5,000
- runtime: 550.970 seconds
- throughput: 9.07 selections/second

The hardened run is slower than 464.110 seconds because it writes a larger authoritative checkpoint per post and records lifecycle diagnostics. It remains more than 1,000 times faster than a roughly two-hour live cadence.

## 33. Corrected original editorial findings

- observations with an original candidate: 4,981
- comparable production-original observations: 3,547
- changes: 631 (17.79%)
- unchanged: 2,916
- matched production/editorial entropy: 6.0641 / 5.9181 bits
- matched production/editorial top-10 share: 16.97% / 28.25%
- repeated all-opportunity/comparable preferences: 777 / 184
- adjustment mean/median: 1.0681 / 1.0671
- cap hits: 0
- per-run change rate: 14.61% min, 18.03% median, 22.03% max

The editorial layer shows a real observational concentration tendency. Its deployed diversity effect remains unknown.

## 34. Corrected generated identity findings

- observations: 5,000
- policy-relevant: 1,854
- changes: 187 (10.09%)
- production winner origin-only exclusions: 99
- production winner small/strong penalties: 177 / 0
- affected candidate appearances: 12,474
- replacement sources: 159 generated, 28 original
- matched production/shadow entropy: 6.9711 / 6.9202 bits
- matched production/shadow top-10 share: 12.04% / 12.24%
- per-run change rate: 6.73% min, 10.14% median, 14.12% max

The change-rate and concentration calculations are valid observational metrics.

## 35. What the current simulator can validly answer

It can answer: “What would each shadow observer prefer at each exact opportunity along alternate production-controlled state trajectories?” It can measure exact current candidate sets, production winners, observational shadow changes, penalties/exclusions, repeated preferences, and variation across seeded production futures.

## 36. What requires counterfactual evolving branches

It cannot answer what production share, image reuse, cycle timing, winner concentration, entropy, or long-run diversity would result if editorial or identity policy controlled posting. Those questions require separate branches that consume each policy's own winners and evolve independent histories/spacing/boundaries.

## 37. Files created

- `accelerated_production_parity_simulator_audit_report.md`
- corrected audit outputs under `simulation_runs/audit_*`

## 38. Files modified

- `tools/simulate_regular_post_futures.py` (untracked before and after task)
- `tests/test_simulate_regular_post_futures.py` (untracked before and after task)
- `accelerated_production_parity_simulator_implementation_report.md` (untracked; correction notice added)

No production code, config, state, history, receipt, or log file was edited.

## 39. Git diff stat

`git diff --stat` is empty because all simulator task files remain untracked. Task-specific pre/post comparison:

```text
tools/simulate_regular_post_futures.py | 425 lines changed (375 insertions, 50 deletions)
tests/test_simulate_regular_post_futures.py | 260 lines changed (259 insertions, 1 deletion)
```

## 40. Git status and explicit confirmations

Branch: `master`; HEAD: `229e3ff5e4b63733e08bbc9466fdb02f09e8481d`.

Task-specific untracked paths are:

```text
?? accelerated_production_parity_simulator_audit_report.md
?? accelerated_production_parity_simulator_implementation_report.md
?? simulation_runs/
?? tests/test_simulate_regular_post_futures.py
?? tools/simulate_regular_post_futures.py
```

The remaining untracked paths shown by `git status --short` pre-dated this audit and were preserved.

Full final `git status --short`:

```text
?? .generated_image_analysis.lock
?? .mrs_asset_analysis.lock
?? accelerated_production_parity_simulator_audit_report.md
?? accelerated_production_parity_simulator_implementation_report.md
?? analyse_mrs_assets_xai.py
?? analyse_mrs_assets_xai_v2.py
?? analyse_mrs_assets_xai_v3.py
?? check_and_fix_thatcher_faces_xai_revised.py
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
?? tests/test_simulate_regular_post_futures.py
?? tools/simulate_regular_post_futures.py
```

Explicit confirmations:

- no X call, media upload, post, or reply occurred;
- xAI call count was 0;
- no external API call occurred;
- no production state, history, receipt, or config was edited by this task;
- no production lock was acquired;
- no production-log pollution was found;
- the live bot and wrapper were not restarted or signalled;
- no counterfactual evolving branch was implemented;
- no file was staged;
- no commit was made;
- nothing was pushed.
