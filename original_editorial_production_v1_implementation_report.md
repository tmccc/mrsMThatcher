# Guarded original-editorial production v1 implementation

## Result

Implementation is complete, but the production policy is **not authorised**.
No threshold pair passed every training and grouped-cross-validation floor. The
deterministically selected diagnostic pair also failed the frozen hold-out
direct-mirrored-conflict gate (15/59, 25.4%; required no more than 15%). The
tracked configuration remains disabled and the policy has
`authorised_for_production: false`.

Base commit: `b32a0be4678364311a8d25385ccaa3a1f035d7c5`

Worktree: `/disks/disk1/research/mrsMThatcher-original-editorial-production-v1`

Branch: `original-editorial-production-v1`

## Review scope and architecture

The implementation was based directly on the verified current `origin/master`;
it had not advanced from the expected commit. Before editing, the following
task-specified files were inspected: `AGENTS.md`, `README.md`,
`mrsMThatcher2.py`, `mrs_log_digest.py`, `mrsMThatcher.local.example.json`,
`shadow_lifecycle.py`, `quote_image_selection_harness.py`,
`tools/simulate_regular_post_futures.py`, all seven named prior implementation
and deployment reports, and the six named selector/simulator/deployment test
modules. The nearest regular receipt, media hand-off, main-post hard-death,
confirmed reconciliation, image/quote history, generated spacing, schedule,
and rotated-log digest tests were also inspected.

`original_editorial_production.py` is a deliberately narrow feature module. It
contains strict v1 policy validation, canonical JSON hashing, legacy/canonical
mode resolution, a process-local circuit breaker, and a pure RNG-free guarded
decision. The existing scorer remains the only scorer and retains exactly:

- `ORIGINAL_EDITORIAL_SHADOW_WEIGHT = 0.32`
- `ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT = 4.0`

The ordinary selector still establishes the phase-specific candidate rows and
winner through its existing tie draw. The guarded wrapper runs afterward,
never modifies generated candidates, never displaces a generated baseline, and
can only return an object from those final rows. It reuses the generated-identity
policy output where enabled and does not alter semantic-veto shadow behaviour.

The canonical modes are `disabled`, `shadow`, and `production`. If the canonical
key is absent, the legacy boolean maps only to disabled/shadow. A canonical
production value combined with the legacy key is rejected rather than silently
interpreted. Missing, stale, malformed, or unauthorised policy data opens the
feature breaker and leaves ordinary posting available.

The existing regular-post receipt chain now begins with a schema-v1
`selection_pinned` document before media upload. It pins quote and candidate
identity, baseline/challenger scores, the final image hash, decision and policy
hashes, breaker state, schedule/history plan, and a stable attempt ID. The media
journal binds to the exact pin hash. Recovery resolves and SHA-256-verifies that
pinned image and never reruns selection. Current main-attempt schemas 7/8 carry
the pin; current confirmed receipt schemas retain it through source lineage,
while all legacy schemas keep their previous interpretation. A bounded
64-entry confirmed regular-image history supplies the restart-safe 12-post gap
guard and is updated idempotently only after confirmation.

`mrs_log_digest.py` preserves shadow labels and adds deduplicated authoritative
decision/confirmation, guard, breaker, policy-stratum, and attempted-versus-
confirmed summaries correlated by attempt/post IDs. The existing simulator has
a fourth independently evolving guarded branch; it calls the real production
wrapper over the exact final baseline candidate rows.

## Policy and frozen evidence

The policy schema contains the requested scorer parameters, thresholds,
confirmed gap, exact input SHA-256 map, explicit quote classifications, blocked
promotion hashes, conservative near-duplicate clusters, calibration record,
generation time, and authorisation bit. Its canonical SHA-256 is
`72c86ee45d5bb86e5a0051fbf74c0eb7d55c8a3352b37118bb58a368d70d9d4d`.
Whitespace and JSON key order do not affect this identity.

The offline importer validated and reconciled 344 case IDs, 274 differing-image
cases, all quote hashes and image identities, both reviewer orientations for
each model family, source strata, the historical-safety file, and current
scorer inputs. It recomputed the supplied Grok aggregate exactly: editorial
320/530, production 210/530; recovered-live 142/183 versus 41/183;
counterfactual-simulated 178/347 versus 169/347; 56/274 direct conflicts; and
zero spurious decisive choices in 140 identical-image orientations.

Principal frozen inputs and SHA-256 values:

- case manifest: `/disks/disk1/research/private-runs/quote-image-feature-evaluation-20260902T121019Z/original-editorial/blinded-review-manifest.jsonl` — `58e22db9aba22de70679b35546703f10546daf21ee99d478d5efc3c8f16675c1`
- unblinding key: `/disks/disk1/research/private-runs/quote-image-feature-evaluation-20260902T121019Z/original-editorial/unblinding-key.json` — `42fa7ae7b5c1091401a89aebdabf773cb4cd6a7c7745aefe339acc912c8e33e0`
- frozen GPT results: `/disks/disk1/research/editorial-blind-dual-review-20260902T191700Z/work/editorial-unblinding-20260902T202143Z/unblinded-case-results.jsonl` — `165ca0b71f6cad5bdce532472db77de03ad1da24ebbc3d603673defaf2c04d06`
- frozen Grok results: `/disks/disk1/research/private-runs/grok46-medium-full-dual-mirrored-20260903T055916Z/analysis/grok46-medium-full-dual-mirrored-results.jsonl` — `27a2ae4938e487b55ab9c9857bd4bf270930b48253b3214543d6a140fce969c1`
- historical safety: `/disks/disk1/research/private-runs/grok46-medium-full-dual-mirrored-20260903T055916Z/analysis/historical-safety-results.json` — `36c1f02caad7e539cc6228c73159f5bd664b3f320c02fb3908e2d6e447e3e7ba`

The other policy-pinned current-input hashes are recorded verbatim in the
tracked policy/calibration JSON. The bounded search also found and inspected
the frozen Grok report
(`5b15484ee92e640fcea14387340b151b09183878fbc5fff2efd63e98ed73575d`),
unblinded selector report
(`cf91a8a02643f87ff98432b988c7606e0726eb559c0de7aaff4ab691918519a6`),
policy-difference results
(`b038f03fea8941bba3b4f2d01a3a56a25e14d0f5bd76ff3d8c3285007c919d1e`),
and cross-model comparison
(`2092b6c954310fc204e10531b07bfca41e43a44bf40bb25124e53480bc316255`).
The specifically named
`original_editorial_matching_backtest.json` was not present in the worktree or
bounded research search; its tracked Markdown report and all structured
case-level evaluation inputs required for calibration were present.

Opaque identities were resolved through the frozen unblinding key and then
matched uniquely to current original bytes:

- `IMG-3B765D6BF48F9676` → `t34.jpg` → `3f446ece7bcc1727e81b7301a8eca188f38c7a5ea0c0ddcdb5d92866a4f0beae`
- `IMG-A0A526421FD73A51` → `t14.jpg` → `eb250a4534412da58fbbf2247c7c041041972ccde4f77ce86888523d86e648c3`

Both hashes occur only in `blocked_promotion_image_sha256`; the images remain
ordinary baseline candidates. No near-duplicate cluster was populated: there
was no byte-identical current original pair and no frozen validated strict
perceptual-equivalence mapping. The empty derived input is itself pinned as
canonical SHA-256 `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`.

## Calibration

Seed `730921119` deterministically split by quotation hash into 70% training
(189 cases, 149 quote clusters) and 30% optimiser-frozen hold-out (85 cases,
65 quote clusters). Five-fold grouped CV remained inside training. Bootstrap
used seed `730921120` and 10,000 quotation-cluster resamples.

The exact grid requested in the task was searched. No pair passed all training
and CV floors. Applying the fixed ranking rule for diagnostic reporting chose:

- minimum policy margin: `0.0`
- maximum baseline-score loss: `2.0`

This diagnostic pair accepted 59 hold-out differing-image cases. Hold-out
pooled editorial decisive share was 70.69%; GPT 72.65%; Grok 68.70%; clustered
bootstrap lower 95% bound 61.78%. It had zero historical, blocked-image,
false-specific, or designated safety-concern acceptances. The conflict gate
failed at 15/59 (25.4%), and training/CV had already failed, so authorisation
remains false. Thresholds were not weakened or retuned after hold-out review.

Gate states: accepted-count PASS; pooled share PASS; GPT share PASS; Grok share
PASS; bootstrap PASS; mirrored conflict **FAIL**; zero historical PASS; zero
blocked-image PASS; zero unanimous-strong false-specific failures PASS; zero
designated historical-safety concerns PASS.

## Verification

Completed results at report finalisation:

- syntax compilation: PASS for every modified Python module
- repository documentation coverage: PASS, 181 modules
- focused selector/policy/calibration/digest/receipt and compatibility slice:
  PASS, 374 tests in 326.11 seconds
- dedicated editorial receipt/restart module: PASS, 23 tests; existing
  hard-death plus delayed-schedule receipt nodes: PASS, 11 tests
- strict base selector parity: PASS, 15,000 exact comparisons (5,000 each for
  disabled, shadow, and production with intervention rejected), zero mismatch,
  zero network calls, and zero production writes in 134.14 seconds
- pure guarded-decision performance: PASS; Python 3.10.12 on
  Linux 5.15.0-190-generic x86_64, 2,000 warm-up plus 30,000 measured
  eight-candidate calls, p50 `0.107 ms`, p95 `0.141 ms`, p99 `0.201 ms`
  (target below 25 ms)
- guarded 20 × 250 simulation: PASS, detailed below
- complete repository suite: PASS, 5,683 tests, zero skips/failures, and 873
  dependency/deprecation warnings in 650.39 seconds

The strict parity matrix covered normal, cycle-reset and last-image fallback,
original/mixed pools, generated spacing, exact ties, used-cycle transitions,
seasonal/stale/mismatch exclusions, origin-quote matches, generated-identity
enabled/disabled paths, and no-candidate outcomes. It compared quote and image
identity, candidate ordering/component scores, ordinary/final winners, phase,
selection and quote-schedule RNG state, histories, spacing, fallback outcomes,
exceptions, and pre-existing structured selector events.

Fault tests cover calculation and pre-pin failures, crash after the durable
pin, pre-upload hash change, upload initiation/confirmation and hand-off,
pre-create/ambiguous/confirmed publication phases, protected quote/image/recent
history and state persistence, schedule finalisation, and receipt retirement.
They use only fake/local transports under `MRS_TEST_MODE=1`. Recovery retains
the exact pin, performs at most one upload/create where confirmation permits,
updates only the actual image and quote once, and preserves legacy receipts.

The final guarded simulation used seeds `46000` through `46019`, 250 confirmed
selections per seed, and virtual start time
`2026-09-03T12:00:00+01:00`. It completed 5,000 post indices / 20,000 branch
selections in 2,316.12 seconds. Production-to-guarded agreement was exactly
100%; the independently evolved branch summaries were identical. The guarded
branch recorded 5,000 opportunities, zero eligible/accepted/confirmed
promotions, and 5,000 `circuit_breaker_open` retentions whose startup cause was
`policy_unauthorised`. This is the required honest result for the unauthorised
evidence-derived policy, rather than a simulated authorisation override. It
also recorded zero historical interventions, blocked promotions,
generated-winner displacements, recent-gap or near-duplicate violations,
candidate exhaustion, non-finite scores, integrity failures, unexpected
breaker openings, state-model inconsistencies, baseline-control divergences,
or additional RNG consumption. Totals reconciled exactly. Production and the
guarded branch each selected 68 distinct original images, had median reuse
interval 67 posts, and made 80 image-cycle resets. The 50 × 250 optional run
was not proportionate after the required 20 × 250 run took 38 minutes 37
seconds; deterministic repeatability is covered by the simulator test suite.

Final validation commands (all exit status 0) were:

```text
env MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 tools/check_python_documentation.py
env MRS_TEST_MODE=1 python3 -m py_compile mrsMThatcher2.py mrs_log_digest.py original_editorial_production.py tools/calibrate_original_editorial_production.py tools/simulate_regular_post_futures.py tools/verify_original_editorial_baseline_parity.py tests/test_calibrate_original_editorial_production.py tests/test_counterfactual_policy_branches.py tests/test_deployment_assets.py tests/test_engagement_question_bot_review.py tests/test_engagement_question_experiment.py tests/test_original_editorial_baseline_parity.py tests/test_original_editorial_production_digest.py tests/test_original_editorial_production_receipt.py tests/test_original_editorial_production_scoring.py tests/test_original_editorial_shadow_scoring.py tests/test_unit_helpers.py
env MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_original_editorial_production_scoring.py tests/test_original_editorial_production_receipt.py tests/test_original_editorial_production_digest.py tests/test_calibrate_original_editorial_production.py tests/test_original_editorial_baseline_parity.py tests/test_original_editorial_shadow_scoring.py tests/test_generated_identity_policy_production_scoring.py tests/test_generated_identity_policy_shadow_scoring.py tests/test_quote_image_selection_harness.py tests/test_counterfactual_policy_branches.py tests/test_simulate_regular_post_futures.py tests/test_deployment_assets.py tests/test_mrs_log_digest.py
env MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_unit_helpers.py::test_main_post_hard_death_boundaries_never_recreate_remote_post tests/test_unit_helpers.py::test_delayed_schedule_followed_by_regular_quote_produces_self_validating_receipt
env MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 tools/verify_original_editorial_baseline_parity.py --base-root /disks/disk1/research/mrsMThatcher-original-editorial-parity-base-final-validation --candidate-root /disks/disk1/research/mrsMThatcher-original-editorial-production-v1 --base-commit b32a0be4678364311a8d25385ccaa3a1f035d7c5 --count 5000 --output /tmp/original-editorial-parity-5000-final-validation.json
env MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 tools/simulate_regular_post_futures.py --mode counterfactual --runs 20 --posts-per-run 250 --seed-base 46000 --start-time 2026-09-03T12:00:00+01:00 --candidate-detail none --snapshot-dir SNAPSHOT --original-editorial-policy-file original_editorial_production_policy_v1.json --output-dir simulation_runs/guarded-editorial-production-v1-final2-20x250
env MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q -p xdist.plugin -n 4 --dist=worksteal --max-worker-restart=0
git diff --check
git status --short
```

The named detached parity worktree and `/tmp` result above were removed after
the successful final rerun. `SNAPSHOT` in the simulation command denotes the
immutable pre-existing simulator snapshot copied into the retained session;
the retained copy is
`simulation_runs/guarded-editorial-production-v1-final2-20x250/input_snapshot`
and its manifest SHA-256 is
`1d2bbd6a392dd5868f24693161d6a72efdac82ba4bd6def884a01cf511d0dbc3`.

During development, an initial simulator diagnostic exposed 22 false
production/guarded divergences in 250 selections because the new branch had
been given a separate tie RNG stream. That run was stopped and not used as
evidence; the branch was corrected to reuse the exact captured pre-tie state
without an additional draw. A focused run then exposed six observational
capture regressions (368 passed), and the first complete suite exposed 20
optional-call/test-fixture compatibility failures (5,663 passed). The common
causes were corrected, all failed nodes passed on targeted reruns, and the
clean consolidated and complete results above were then obtained. No failing
run is represented as final validation.

## Changed files

- `README.md`
- `mrsMThatcher.local.example.json`
- `mrsMThatcher2.py`
- `mrs_log_digest.py`
- `original_editorial_production.py`
- `original_editorial_production_policy_v1.json`
- `original_editorial_production_calibration_v1.json`
- `original_editorial_production_calibration_v1.md`
- `original_editorial_production_v1_implementation_report.md`
- `tools/calibrate_original_editorial_production.py`
- `tools/simulate_regular_post_futures.py`
- `tools/verify_original_editorial_baseline_parity.py`
- `tests/fixtures/original_editorial_production_regression_v1.json`
- `tests/test_calibrate_original_editorial_production.py`
- `tests/test_counterfactual_policy_branches.py`
- `tests/test_deployment_assets.py`
- `tests/test_engagement_question_bot_review.py`
- `tests/test_engagement_question_experiment.py`
- `tests/test_original_editorial_baseline_parity.py`
- `tests/test_original_editorial_production_digest.py`
- `tests/test_original_editorial_production_receipt.py`
- `tests/test_original_editorial_production_scoring.py`
- `tests/test_original_editorial_shadow_scoring.py`
- `tests/test_unit_helpers.py`

## Production and future action

No production configuration, state, history, receipt, log, credential, service,
process, provider, API, or X state was changed or contacted. The production
checkout was used only to read `AGENTS.md`, fetch `origin`, and verify its clean
tracked status. The bot was not run, signalled, paused, stopped, or restarted.

There is currently no reviewed deployment action because the policy is not
authorised. If a future independently reviewed regeneration passes every gate,
the exact local activation would be to remove the legacy
`ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING` key, set
`ORIGINAL_EDITORIAL_MODE` to `production`, and point
`ORIGINAL_EDITORIAL_PRODUCTION_POLICY_FILE` at that authorised policy. That
configuration change, service restart, deployment, and any merge were **not
performed**.
