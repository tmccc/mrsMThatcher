# Generated Image Identity-Policy Shadow Scoring Deployment Report

## 1. Executive result

**Deployment successful; first live generated-identity observation pending.**

The reviewed generated-image identity-policy observer was committed, pushed, enabled in ignored local configuration, and loaded successfully by a wrapper-managed replacement Python child. The existing original-image editorial shadow scorer remains enabled and unchanged. The next regular post was scheduled for 2026-07-10 11:00:39 BST, about one hour after deployment, so no post was forced and live event/digest verification remains pending.

## 2. Pre-deployment state

- Capture time: `2026-07-10T09:56:00+01:00`
- Branch: `master`
- Previous commit: `aa8d86e Add original image editorial shadow scoring`
- Wrapper PID: `3631076`
- Python child PID: `1647511`
- Instance lock: `pid=1647511`
- Last main post ID: `2075489042678854090`
- Last regular post epoch: `1783670123`
- Next regular post epoch: `1783677639` (`2026-07-10 11:00:39 BST`)
- Last regular image: `t38.jpg`
- Original regular posts since generated image: `1`
- Generated pool: enabled
- Required originals between generated images: `2`
- Generated spacing eligibility: blocked until one further successful original regular post
- Original editorial shadow: enabled, weight `0.32`, cap `4.0`
- Generated identity shadow: not configured before deployment
- Existing production receipts: none

The worktree contained the reviewed modifications to `mrsMThatcher2.py` and `mrs_log_digest.py`, the new identity observer tests/report, the audit and its offline tooling, plus numerous unrelated untracked analysis files and prior backups. Those unrelated files were preserved and not staged.

## 3. Reviewed implementation verification

The current source was inspected directly before deployment. It confirms:

- `ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING` defaults to `False`.
- The audit is loaded only when the generated identity observer is enabled.
- Audit validation checks schema, analysis kind, generated basename form, configured-pool coverage, current SHA-256, origin hash, enums, finite numeric ranges, and booleans.
- Production calculates scores and selects `chosen = random.choice(tied)` before either observer runs.
- The generated identity observer receives the exact phase-specific production `scored` list.
- Originals retain their production score.
- Generated origin-quote candidates are unrestricted.
- Cross-quote `unrestricted`, `small_penalty`, `strong_penalty`, and `origin_quote_only` policies are applied only to the hypothetical ranking.
- `origin_quote_only` candidates are excluded from shadow ranking only.
- Candidate dictionaries and ordering are not mutated.
- Shadow tie handling consumes no randomness: the production winner is retained if tied at the maximum; otherwise basename ordering is deterministic.
- Runtime shadow errors are caught after production selection and cannot replace `chosen`.
- The original editorial observer remains a separate call with an independent score model.
- `GENERATED_IDENTITY_POLICY_SHADOW_RESULT` is parsed by the digest.
- The digest section is explicitly hypothetical, uses policy-relevant observations as its denominator, separates origin-only production winners, and identifies replacement source.

## 4. Audit verification

- Schema version: `1`
- Analysis kind: `generated_image_identity_dependence_audit`
- Audit items: `83`
- Configured generated pool items: `83`
- Missing audit entries: `0`
- Unexpected audit entries: `0`
- SHA-256 mismatches: `0`
- Policies:
  - `unrestricted`: `68`
  - `small_penalty`: `9`
  - `origin_quote_only`: `6`
  - `strong_penalty`: `0`

Diagnostic image:

- Basename: `tg_faf99f3030693b0a55f0116194551792f3c4ea51261d7310eca7fb4d33b667d5.png`
- Recommended policy: `origin_quote_only`
- Identity dependence: `high`
- Origin quote hash: `faf99f3030693b0a55f0116194551792f3c4ea51261d7310eca7fb4d33b667d5`
- Current/audited SHA-256: `008d93c308555106847e13e12be8d6bfa2b767ca026220949dffa03d09b64abf`

The production loaders were also run in the isolated test environment against current local files. They accepted `69` original editorial items and `83` generated identity audit items.

## 5. Pre-deployment validation

Test logging was checked before execution. Unit imports use `/tmp/mrsMThatcher-unit-import/unit-test.log`; integration tests use pytest temporary base directories. The production log had the same size and mtime before and after all tests: `1270768` bytes, mtime `2026-07-10 09:56:29.745975806 +0100`.

```text
python3 -m py_compile mrsMThatcher2.py mrs_log_digest.py tests/test_generated_identity_policy_shadow_scoring.py
PASS (no output)

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_generated_identity_policy_shadow_scoring.py
34 passed in 0.21s

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_original_editorial_shadow_scoring.py
41 passed in 0.22s

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_integration_harness.py -k digest
35 passed, 132 deselected in 7.42s

git diff --check
PASS (no output)
```

The full suite was not rerun because the reviewed implementation had already passed it, the working implementation matched that reviewed code, and every required focused deployment check passed.

## 6. Backup

- Directory: `pre_generated_identity_shadow_deployment_20260710_095701/`
- Manifest: `pre_generated_identity_shadow_deployment_20260710_095701/MANIFEST.txt`
- Copied with metadata preserved:
  - `mrsMThatcher2.py`
  - `mrs_log_digest.py`
  - `mrsMThatcher.local.json`
  - `bot_state.json`
  - `images_used.json`
  - `lines_used.json`
  - `generated_image_identity_dependence_audit.json`
- Receipt files copied: none existed

## 7. Git state

- New commit: `8e850f7 Add generated identity-policy shadow scoring`
- Exact committed files:
  - `mrsMThatcher2.py`
  - `mrs_log_digest.py`
  - `tests/test_generated_identity_policy_shadow_scoring.py`
  - `generated_image_identity_policy_shadow_scoring_implementation_report.md`
  - `generated_image_identity_dependence_audit.json`
  - `generated_image_identity_dependence_audit_report.md`
  - `generated_image_identity_dependence_audit_implementation_report.md`
  - `tools/audit_generated_image_identity_dependence.py`
  - `tests/test_generated_image_identity_dependence_audit.py`
- Commit size: `9 files changed, 7097 insertions(+), 2 deletions(-)`
- Staged patch check: passed
- Push: successful, `aa8d86e..8e850f7 master -> master`
- Remote: `origin` (`https://github.com/tmccc/mrsMThatcher.git`)
- No force push occurred.
- `mrsMThatcher.local.json` was not staged or committed.

## 8. Configuration

The ignored local configuration now contains:

```json
{
  "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING": true,
  "ORIGINAL_EDITORIAL_ANALYSIS_FILE": "/disks/disk1/etc/mrsMThatcher/original_image_editorial_analysis_experiment_v1.json",
  "ORIGINAL_EDITORIAL_SHADOW_WEIGHT": 0.32,
  "ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT": 4.0,
  "ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING": true,
  "GENERATED_IDENTITY_AUDIT_FILE": "/disks/disk1/etc/mrsMThatcher/generated_image_identity_dependence_audit.json",
  "GENERATED_IDENTITY_SHADOW_SMALL_PENALTY": 6.0,
  "GENERATED_IDENTITY_SHADOW_STRONG_PENALTY": 15.0
}
```

JSON validation passed. Original shadow settings remained enabled and byte-for-value unchanged. Generated image pool and spacing settings were not altered.

## 9. Restart

- Restart request time: `2026-07-10T09:58:10+01:00`
- Signal: one `SIGTERM`
- Signal target: Python child PID `1647511` only
- Wrapper PID before: `3631076`
- Wrapper PID after: `3631076`
- Replacement child PID: `2111623`
- Replacement child start: `2026-07-10 09:59:09 BST`
- Startup success logged: `2026-07-10 09:59:11 BST`
- Approximate wrapper restart interval: 59 seconds
- New lock: `pid=2111623`

The wrapper was neither signalled nor restarted. No second signal was sent.

## 10. Startup verification

Relevant newly appended lines:

```text
2026-07-10 09:59:10 INFO acquire_instance_lock - Acquired instance lock /disks/disk1/etc/mrsMThatcher/mrsMThatcher.lock
2026-07-10 09:59:10 INFO validate_original_editorial_shadow_startup - Original editorial shadow scoring enabled. analysis_file=/disks/disk1/etc/mrsMThatcher/original_image_editorial_analysis_experiment_v1.json original_items=69 weight=0.32 max_abs_adjustment=4.0
2026-07-10 09:59:11 INFO validate_generated_identity_shadow_startup - Generated identity-policy shadow scoring enabled. audit_file=/disks/disk1/etc/mrsMThatcher/generated_image_identity_dependence_audit.json items=83 policies={'origin_quote_only': 6, 'small_penalty': 9, 'unrestricted': 68} small_penalty=6.0 strong_penalty=15.0
2026-07-10 09:59:11 INFO main - Existing next_quote_post_epoch=1783677639, human=2026-07-10 11:00:39
2026-07-10 09:59:11 INFO main - Bot started successfully
2026-07-10 10:00:11 DEBUG main - Main loop tick. epoch=1783674011
```

No hash mismatch, coverage mismatch, config error, traceback, receipt block, duplicate child, or unexpected cooldown appeared. One additional main-loop tick completed and the child remained alive.

## 11. First live generated identity event

**Pending.** The next regular post was scheduled for 11:00:39 BST, approximately one hour after deployment. No post was forced and no claim of live event validation is made.

## 12. Digest verification

**Live generated-identity digest verification pending** until the first real `GENERATED_IDENTITY_POLICY_SHADOW_RESULT` event occurs.

Static parsing/rendering was verified by the 34 dedicated tests and 35 digest-focused integration tests. The existing original editorial digest section was unchanged and its 41-test suite passed.

## 13. Production safety

- Actual production selection still assigns `chosen` before either shadow observer runs.
- Neither observer returns a value into the production selection path.
- Generated identity evaluation works on derived rows and does not mutate candidate dictionaries or order.
- Generated identity tie-breaking uses no RNG.
- No posting or API call is introduced by either observer.
- No shadow-specific state file exists.
- No image history, quote history, spacing counter, receipt, or scheduling mutation is performed by the observer.
- Both shadow systems remain independent; their hypothetical scores are not combined.
- No receipt issue was present before or after restart.

## 14. Final process state

At `2026-07-10T10:00:25+01:00`:

- Wrapper PID: `3631076`
- Python child PID: `2111623`
- Lock: `pid=2111623`
- Child health: alive after an additional main-loop tick
- Last main post ID: `2075489042678854090`
- Last regular post epoch: `1783670123`
- Next regular post epoch: `1783677639` (`2026-07-10 11:00:39 BST`)
- Last regular image: `t38.jpg`
- Original regular posts since generated image: `1`
- Generated spacing state: coherent and unchanged
- Production receipts: none

## 15. Final Git state

- HEAD: `8e850f7 Add generated identity-policy shadow scoring`
- Upstream push: complete
- Tracked deployment implementation: clean after commit
- Ignored local config: modified intentionally, not versioned
- Deployment backup: untracked
- This deployment report: untracked at creation
- Numerous pre-existing unrelated untracked analysis files/backups remain untouched.

No unrelated file was staged or committed. Final `git diff --check` passed, current tracked `git diff --stat` is empty, and `origin/master...master` reports `0 0` (fully synchronized).

Final `git status --short`:

```text
?? .generated_image_analysis.lock
?? .mrs_asset_analysis.lock
?? analyse_mrs_assets_xai.py
?? analyse_mrs_assets_xai_v2.py
?? analyse_mrs_assets_xai_v3.py
?? analyse_original_images_editorial_xai.py
?? check_and_fix_thatcher_faces_xai_revised.py
?? digest063_analysis.txt
?? digest_latest_state_window_boundary_fix_report.txt
?? generate_all_openai_quote_images.py
?? generate_first10_from_quote_analysis.py
?? generate_openai_low_first10.py
?? generated_image_analysis.json.before_face_corrections
?? generated_image_identity_policy_shadow_scoring_deployment_report.md
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
?? original_editorial_matching_backtest_report.md
?? original_editorial_shadow_scoring_deployment_report.md
?? package_openai_corpus_for_upload.py
?? pre_generated_identity_shadow_deployment_20260710_095701/
?? pre_original_editorial_shadow_deployment_20260710_070852/
?? review/
?? tests/test_backtest_original_editorial_matching.py
?? tools/backtest_original_editorial_matching.py
```

There are no modified or staged tracked files. The new deployment backup and deployment report are the only untracked entries created by this deployment; all other entries pre-dated it.

## 16. Explicit confirmations

- No manual X call occurred.
- No manual xAI call occurred.
- No manual external API call occurred.
- No test post was created.
- The wrapper was not restarted or signalled.
- Only the Python child was restarted, using one SIGTERM.
- Production state was not manually edited.
- Receipts were not manually edited.
- Logs were not deleted, truncated, or rotated.
- The original editorial shadow implementation and settings were not altered.
- The generated identity observer remains observational only.
- No force push occurred.

## Rollback instructions

To disable only the generated identity observer while retaining the original editorial observer:

1. Create a fresh timestamped copy of `mrsMThatcher.local.json`.
2. Set only `ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING` to `false`.
3. Validate with `python3 -m json.tool mrsMThatcher.local.json >/dev/null`.
4. Confirm `ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING` remains `true` and its weight/cap remain `0.32/4.0`.
5. Identify the current Python child using `ps -o pid,ppid,cmd --ppid 3631076` rather than relying on the PID recorded here.
6. Send one `SIGTERM` to that Python child only: `kill -TERM <current-child-pid>`.
7. Do not signal the wrapper. Wait for its normal restart delay.
8. Verify the wrapper PID is unchanged, one new child appears, the lock matches, startup succeeds, original editorial shadow logs enabled, and generated identity shadow does not log enabled.

Code restoration is not required for normal feature disablement. If code/config restoration is ever necessary, use `pre_generated_identity_shadow_deployment_20260710_095701/` as evidence, review differences before copying, restore only the required code/config files, validate them, and restart only the Python child as above. Runtime state/history files in the backup must not be restored casually because they become stale after subsequent live activity.
