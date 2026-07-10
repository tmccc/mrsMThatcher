# Accelerated Production-Parity Simulator Implementation Report

## Audit correction notice

A subsequent direct audit found that the first implementation's production-selection parity was sound, but two claims in this report required correction:

1. The original editorial entropy/top-10 comparison used different populations (all-source production winners versus best-original shadow preferences) and was not a valid matched comparison.
2. Resume persistence was not atomic across JSONL, state/history files, and progress; interruption between writes could duplicate an index or resume from mismatched state.

The hardened simulator now reports matched original-only distributions, explicitly labels repeated observational preferences along the production-controlled state trajectory, uses one authoritative atomic checkpoint with JSONL reconciliation, requires double-snapshot agreement, and supports named-image lifecycle tracing. Shadow preferences still do **not** evolve their own used-image state; this simulator does not forecast a policy-controlled counterfactual future.

The authoritative corrected findings and validation are in `accelerated_production_parity_simulator_audit_report.md`. Historical figures below are retained as a record of the first run and must be interpreted through that audit.

## 1. Executive summary

Implemented `tools/simulate_regular_post_futures.py`, a standalone offline simulator that runs the current production quote/image selectors against immutable input snapshots and independently evolving private state. It captures the exact phase-specific production candidate list seen by both existing shadow observers, applies successful-post transitions only to private state, advances a deterministic virtual clock, and never posts or calls a network API.

No production source, config, state, receipt, log, or process was changed. The final evidence run completed 5,000 hypothetical regular selections in 464.110 seconds (10.77 selections/second).

## 2. Architecture inspected

The current `mrsMThatcher2.py` paths inspected included quote loading and weighted selection, seasonality, quote-history migration/cycles, original/generated image construction, generated spacing, image-history cycles, last-image avoidance, mismatch and seasonal exclusions, compatibility scoring, generated origin matching/boost, random winner ties, three-phase recovery, successful regular-post persistence, scheduling, and both shadow observers.

## 3. Exact production logic reused

The simulator directly calls the current `choose_regular_quote_image_pair()` implementation. It uses the production `choose_unused_line_candidate()`, `choose_matched_unused_image()`, compatibility scorer, generated-source/origin logic, spacing helpers, history-cycle mutation, random module, and shadow log functions. Its recovery wrapper mirrors `post_random_quote()`:

1. normal selection;
2. forced image-cycle reset while retaining the last-image boundary;
3. final last-image-permitted fallback only for the established boundary failure.

The simulated production winner is the object selected by production code. The shadow hooks receive the same `scored` list object.

## 4. Production refactor

None. `mrsMThatcher2.py` and all other production files remain unchanged. Reuse is achieved by importing the existing module in test mode into the simulator process and redirecting all paths and side effects there.

## 5. Why parity is credible

Parity tests use current corpus metadata and prove that identical state, time, and seed produce identical quote hash, candidate basename ordering, candidate scores, winner, phase, origin match, and origin boost. The two shadow wrappers record the identity of the `scored` object passed by production selection. Shadow evaluation does not advance RNG state.

The simulator is production-parity selection simulation, not an exact forecast of future X activity. It cannot model future humans, new assets, external X behaviour, or future code/config changes.

## 6. Snapshot design

Each session has `input_snapshot/` containing private copies of:

- `bot_state.json`, `images_used.json`, and `lines_used.json`;
- local config, quote corpus, and all production/shadow analysis metadata;
- the current original and approved generated image pools;
- `manifest.json` with timestamp, branch, commit, Python version, source paths, SHA-256 values, and corpus counts.

Snapshot content is made read-only after creation. Every future in a session starts from the same snapshot.

## 7. Live-state snapshot consistency

The three mutable files are read as one stat-before/read/stat-after group. Any inode, size, mtime, or ctime change causes the complete group to be retried. Metadata and images use the same stable-window check individually. This avoids torn individual files and catches writes overlapping the grouped state snapshot without locking or pausing production.

The limitation is that the production files are separate atomic persistence units rather than one transactional multi-file object. The snapshot records its exact hashes so any later investigation has an unambiguous input set.

## 8. Private simulated state

Each `runs/run_NNNN/` contains only private `state.json`, image/quote histories, progress, and selection JSONL. Simulated IDs use a `sim-...` namespace. No active receipt is copied or created. State and histories are atomically persisted after each completed hypothetical success.

## 9. Virtual clock

`--start-time now` and explicit ISO-8601 timestamps are supported. Production `now_epoch()` is redirected to the current virtual epoch before each selection, so `current_datetime()` and seasonal quote/image logic see virtual time. No sleep occurs. The next post interval uses the production `POST_SLEEP_MIN`/`POST_SLEEP_MAX` random semantics and ordering.

## 10. RNG design

Run `i` uses exactly `seed_base + i`. The production module's random state is initialised from that seed, stored after each post, and restored on resume. Quote weighting, production ties, quote scheduling, and meme scheduling consume randomness in production order. Shadow evaluation consumes none. The three medium-run core hashes differed across seeds, while overwrite reruns with the same snapshot/seeds were byte-identical.

## 11. State transitions

After a hypothetical success only, the simulator marks the selected quote hash and image basename used, updates last main post and regular image fields, calls the production generated-spacing update, and applies production quote/meme schedule-field helpers. Generated successes reset spacing; original successes increment it to the configured cap. Failures do not commit a record or state transition.

The 5,000-selection run recorded 60 image-cycle resets. All selections completed in the normal outer recovery phase; no forced-reset or last-image fallback was needed for those particular alternate futures.

## 12. Network and posting guards

The core mode permits zero network calls. Import and simulation execute with socket connection guards. `requests` entry points, X media upload, X post creation, Grok reply generation, reply paths reached through post creation, and instance-lock acquisition are replaced by functions that raise `SimulationSafetyError`. Dummy loopback credentials are supplied only to satisfy import-time validation.

## 13. Path-write guards

`PrivateWriter` resolves every mutable destination and rejects anything outside the marked session directory. The simulator rejects the project root and live state/config/log/lock paths as output. Project-local output is accepted only under `simulation_runs/`. Overwrite requires the simulator marker; resume also requires it.

## 14. Production-log isolation

Production import initially logs only to a process-specific `/tmp` directory and is then reconfigured to a simulation-local warning log. `LOG_LEVEL=CRITICAL` suppresses import console diagnostics. Tests use the established test-mode module and temporary paths. No simulator or pytest contamination marker was found in `mrsMThatcher.log`; the log was not truncated, deleted, or rotated.

## 15. Original editorial shadow integration

The real `log_original_editorial_shadow_result()` implementation evaluates the exact production `scored` list. The simulator records the emitted structured event plus per-candidate adjustment detail at the requested detail level. It does not combine editorial scores with production or identity-policy scores.

## 16. Generated identity-policy shadow integration

The real `log_generated_identity_policy_shadow_result()` implementation receives the same list. Original candidates retain baseline scores; generated origin uses remain unrestricted; cross-quote penalties/exclusions use current audit/config semantics. This experiment remains independent of the original editorial shadow.

## 17. CLI

Supported options are `--runs`, `--posts-per-run`, `--seed-base`, `--start-time`, `--output-dir`, `--candidate-detail none|top10|full`, `--snapshot-dir`, `--resume`, and `--overwrite`. Help explicitly states that the tool is offline and never posts or calls network APIs.

## 18. Outputs and schemas

Each run writes `selections.jsonl`, private state/history, and `progress.json`. Records include session/run/seed/index, virtual time, quote/hash/weight/seasonal multiplier, phase, winner/source/score/components/origin data, candidate counts, spacing before/after, cycle reset flags, both structured shadow results, diagnostic-image presence, and optional candidate detail.

Session outputs are `simulation_summary.json` and `simulation_report.md`. Aggregates include source rates, phase/cycle counts, score distributions, winner concentration, shadow denominators/change rates, rank/adjustment statistics, transitions, per-run variation, and diagnostic-image behaviour.

## 19. Resume behaviour

Progress, RNG state, virtual epoch, and private state are atomically saved after each selection. `--resume` requires matching run count, posts per run, and seed base, and never rereads live state. A completed 3 x 100 session resumed without adding or changing selection records. Runtime accumulation is retained in the final implementation.

## 20. Tests

`tests/test_simulate_regular_post_futures.py` covers path confinement, unsafe output rejection, socket/network guards, post/upload/reply/lock hard failures, exact real-selector and shadow candidate parity, RNG neutrality, generated-spacing transitions, RNG-state restoration, stable snapshot reads, denominator correctness, and safety-oriented CLI help.

The first full-suite attempt exposed a simulator-test cleanup defect: the safety test left private write guards installed on the shared imported test module, blocking later persistence tests. The test now explicitly restores every changed helper. An in-process regression invocation then passed the complete simulator suite followed by representative persistence and receipt tests (`17 passed`). This was test-only leakage; production and simulation processes were unaffected.

## 21. Py-compile result

PASS:

```text
python3 -m py_compile tools/simulate_regular_post_futures.py tests/test_simulate_regular_post_futures.py
```

## 22. Focused test results

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_simulate_regular_post_futures.py
15 passed in 4.31s

tests/test_original_editorial_shadow_scoring.py
41 passed in 0.39s

tests/test_generated_identity_policy_shadow_scoring.py
34 passed in 0.35s

focused production selection subset
34 passed, 297 deselected in 0.63s
```

## 23. Full-suite result

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
645 passed, 1 skipped, 1 warning in 192.28s
```

The warning is the pre-existing Starlette/httpx deprecation warning.

## 24. Git diff check

`git diff --check`: PASS, no output.

## 25. Smoke simulation

Final tool run: 1 run x 20 posts, seed 1000, 20 selections in 3.338 seconds (5.99/s). Output: `simulation_runs/final_smoke_1x20_20260710/`.

## 26. 3 x 100 simulation

Final tool run: 3 runs x 100 posts, seeds 2000-2002, 300 selections in 31.737 seconds (9.45/s). Output: `simulation_runs/final_medium_3x100_20260710/`. Different run seeds produced different deterministic core hashes.

## 27. Large simulation

20 runs x 250 posts, seeds 1000-1019, 5,000 selections. Output: `simulation_runs/evidence_20x250_20260710/`. The selection run preceded a reporting-only aggregate enhancement; its immutable detailed records were re-aggregated with the final summary implementation. Selection logic was unchanged.

## 28. Performance

- Selections: 5,000
- Runtime: 464.110 seconds
- Throughput: 10.77 selections/second

## 29. Original editorial aggregate findings

- Observations: 4,981 (19 selections had no eligible original shadow candidate)
- Comparable production-original observations: 3,547
- Winner changes: 631, or 17.79% of comparable observations
- Per-run change rate: 14.61% minimum, 18.03% median, 22.03% maximum
- Production-winner editorial adjustment: mean 1.0681, median 1.0671
- Cap hits: 0
- Most frequent observational preference gains: `t10.jpg` (101), `t18.jpg` (100), `t66.jpg` (53); these are not hypothetical post counts.
- Corrected matched production-original/editorial-preference entropy: 6.064 / 5.918 bits.
- Corrected matched production-original/editorial-preference top-10 share: 16.97% / 28.25%.
- Repeated observational preferences: 777 across all opportunities and 184 within comparable production-original observations.

The original 6.971-versus-5.909 entropy comparison used unmatched populations and was invalid. The corrected matched comparison still shows observational concentration, but it is not a forecast of policy-controlled diversity.

## 30. Generated identity-policy aggregate findings

- Observations: 5,000
- Policy-relevant observations: 1,854
- Winner changes: 187, or 10.09% of policy-relevant observations
- Per-run change rate: 6.73% minimum, 10.14% median, 14.12% maximum
- Production origin-only winners excluded in shadow: 99
- Production winners receiving small/strong penalties: 177 / 0
- Affected candidate appearances: 12,474 (3,521 exclusions and 8,953 penalties)
- Changed-winner replacements: 159 generated and 28 original
- Identity-shadow entropy: 6.920 bits; top-10 share 12.24%

## 31. Diagnostic image findings

`tg_faf99f3030693b0a55f0116194551792f3c4ea51261d7310eca7fb4d33b667d5.png` entered three exact candidate sets, all as cross-quote candidates. The identity shadow excluded it all three times. Production did not select it in those cases, so there was no replacement winner attributable to it. No scoring special case exists for this basename.

## 32. Across-run variability

Generated production share ranged from 28.0% to 29.6%, median 29.2%. Editorial and identity change-rate ranges are reported above. These are alternate RNG futures from one fixed snapshot, not independent observations of future human activity.

## 33. Important caveats

The simulator models current local quote/image pools, state transitions, production scoring, randomness, seasonal time, and shadow policies. It does not model future mentions, quote-posts, media, asset additions, code/config changes, API behaviour, post failures, or audience response. Hypothetical posts are treated as successful by design.

## 34. Optional xAI work

None. xAI call count: 0. X call count: 0. Token use and API cost: zero.

## 35. Files created

- `tools/simulate_regular_post_futures.py`
- `tests/test_simulate_regular_post_futures.py`
- `accelerated_production_parity_simulator_implementation_report.md`
- private outputs under `simulation_runs/`

## 36. Files modified

No pre-existing tracked or production file was modified.

## 37. Git diff stat

`git diff --stat` is empty because all task files are deliberately untracked. New source/test size before this report: 1,429 lines. No file is staged.

## 38. Final Git state and safety confirmation

Branch `master`, commit `229e3ff5e4b63733e08bbc9466fdb02f09e8481d`. The working tree retains numerous pre-existing untracked analysis, backup, review, and corpus artifacts. Task-specific untracked entries are the simulator, its tests, this report, and `simulation_runs/`.

Final `git status --short`:

```text
?? .generated_image_analysis.lock
?? .mrs_asset_analysis.lock
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

- no X post, media upload, or reply occurred;
- no X or xAI call occurred;
- no external API call occurred;
- no production state, history, receipt, config, lock, or log was modified by the simulator;
- no production lock was acquired;
- the live bot and wrapper were not restarted or signalled;
- no production mode was enabled;
- nothing was staged;
- no commit was made;
- nothing was pushed.
