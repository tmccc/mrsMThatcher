# Generated Image Pool Runway Digest Report

## 1. Executive summary

`mrs_log_digest.py` now extends **Generated image pool health** with current-cycle usage, observed generated-post rates, schedule-based capacity, estimated cycle runway, and 7/30-day curation trends. The change is reporting-only and reads live files afresh on each invocation.

## 2. Files inspected

- `mrs_log_digest.py`
- `tests/test_generated_image_pool_health_digest.py`
- `tests/test_integration_harness.py`
- `generated_review_approved_images/`
- `generated_image_analysis.json`
- `generated_image_identity_dependence_audit.json`
- `images_used.json`
- `generated_image_quarantine/` transaction manifests
- `mrsMThatcher.local.json`
- `mrsMThatcher.log` and rotations `.1` through `.5`

## 3. Current-cycle vs all-time usage semantics

`images_used.json` is the current mixed image-cycle history, not an immutable all-time posting ledger. The digest therefore uses `used_in_current_cycle` and `unused_in_current_cycle`; it does not claim that unused images have never been posted historically. Historical post-rate calculations use structured successful production log events instead.

## 4. Historical log source and coverage

The runway helper scans the explicitly supplied current and rotated production logs once, with a 30-day logical cutoff. Existing record deduplication removes overlapping rotated records, and successful regular posts are deduplicated again by post ID. The available clean files currently cover 3.6 days, so the nominal 7-day and 30-day figures use the same 3.6-day evidence window and report that actual coverage.

## 5. Contamination filtering

Only structured `main_post_posted` events for the `quote_image` lane count. Memes, replies, failures, receipt-only records, and records without post IDs do not count. Seconds containing known test markers (`/tmp/pytest-`, `/tmp/pytest-of-`, `mrs_test_mode`, `dummy credentials`, or loopback endpoints) are excluded as a unit; 16 contaminated seconds were excluded from the real scan. Tests prove a synthetic burst is excluded while a legitimate production event remains counted.

## 6. Observed-rate methodology

For each trailing window, generated share is generated successful regular posts divided by all successful regular posts. Per-day rates divide those counts by actual clean log coverage, not the nominal window length. An observed runway basis requires at least one day of coverage and at least one regular and generated post. The 7-day basis is preferred, then 30-day, then the schedule model.

## 7. Schedule-based methodology

Current effective values are read dynamically. The model uses the midpoint of `POST_SLEEP_MIN=7200` and `POST_SLEEP_MAX=9000`, and `GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN=2`. This yields an ordinary maximum generated share of one in three, 10.67 regular posts/day, and 3.56 generated posts/day. It is a capacity model, not a forecast.

## 8. Runway formula

Observed regular posts to exhaustion are `remaining current-cycle generated images / observed generated share`; elapsed days are `remaining / observed generated posts per day`. Schedule runway uses the configured maximum generated cadence. Zero rates, disabled pools, malformed config, short coverage, and completed cycles produce explicit guarded results rather than infinity.

## 9. Curation trend methodology

Only completed quarantine and restore manifests count. Image basenames are deduplicated within each transaction. Failed or rolled-back transactions are excluded. Net active change is restored images minus quarantined images for the trailing 7 and 30 days. Current quarantine membership continues to come from committed transaction state and preserved image files.

## 10. Digest output design

The existing snapshot section remains compact and now adds: observed 7/30-day rates with coverage, current-cycle runway estimates, schedule-model comparison, and curation image-action totals. Estimates are explicitly labelled and separated from the selected digest log window.

## 11. Error handling

Missing or malformed logs, metadata, transaction timestamps, usage history, and schedule config are reported as unavailable or warnings without aborting the digest. Existing metadata-health warnings remain visible.

## 12. Performance impact

The real `--no-state` invocation over six known log files completed in 3.936 seconds. Each required log file is read once by the historical helper; no image hashing was added beyond existing pool-health validation.

## 13. Exact real active/quarantined counts

- Active generated images: **79**
- Currently quarantined images: **4**
- Total known generated images: **83**
- Active used in current cycle: **6**
- Active unused in current cycle: **73**
- Quarantined used in current cycle: **3**
- Quarantined unused in current cycle: **1**
- Metadata coverage: **complete**
- Hash validation: **79 / 79 valid**

## 14. Exact real recent regular/generated posting rates

Both nominal windows currently have 3.6 days of available clean history:

- Successful regular posts: **38**
- Successful generated regular posts: **9**
- Generated share: **23.7%**
- Regular posts/day: **10.55**
- Generated posts/day: **2.50**

## 15. Exact real runway estimates

- Primary basis: **trailing 7-day observed rate (3.6 days actual coverage)**
- Estimated regular posts to current generated-cycle exhaustion: **308**
- Estimated elapsed time: **29.2 days**
- Schedule-capacity comparison: **20.5 days** at 3.56 generated posts/day

These are estimates for the current cycle, not claims about all-time first use.

## 16. Exact real curation trend

- Quarantined in trailing 7/30 days: **4 / 4**
- Restored in trailing 7/30 days: **0 / 0**
- Net active change in trailing 7/30 days: **-4 / -4**
- Latest transaction: `20260710T215739Z_2f30ebcebd`, four images, completed at `2026-07-10T21:57:39.266739+00:00`

## 17. Tests added

`tests/test_generated_image_pool_runway_digest.py` covers observed windows, rotated overlap, window independence, contamination, normal and fallback runway calculations, disabled/complete/malformed cases, dynamic schedule values, curation and restoration trends, output wording, and single-read behaviour.

## 18. Focused test result

`15 passed in 0.11s` across the new runway tests and existing pool-health tests.

## 19. Existing digest test result

`35 passed, 134 deselected in 29.59s` for `tests/test_integration_harness.py -k digest`.

## 20. Other affected tests

Generated identity shadow/production metadata suites: `50 passed in 0.27s`.

## 21. py_compile result

PASS for `mrs_log_digest.py` and the focused digest test modules.

## 22. git diff --check result

PASS.

## 23. Production-log isolation result

The production log was not modified by tests. It remained under control of the live bot and contained no pytest, temporary-path, dummy-credential, loopback, or test-mode markers after the checks. It was not deleted, truncated, or rotated.

## 24. Real --no-state validation result

PASS. The read-only digest reported the counts, rates, runway, curation trend, complete coverage, valid hashes, and `health = OK` values above. Resume state was not advanced.

## 25. Production file safety

No real image, generated metadata, production state, configuration, receipt, or transaction was changed by this task. Existing working-tree metadata changes and four deleted active PNG paths are the owner's prior quarantine transaction and were deliberately left untouched.

## 26. Simulator confirmation

No simulator was run or rerun.

## 27. Commit hash

Pending at report preparation time; final commit is recorded in Git history.

## 28. Push result

Pending at report preparation time.

## 29. Final git status

Task files will be committed separately. Existing real quarantine changes and unrelated untracked files remain unstaged.

## 30. Unavailable estimates

No primary estimate is unavailable, but the nominal 7-day and 30-day observed estimates are based on only 3.6 days of available clean logs. The digest states this coverage and avoids implying a full 7- or 30-day sample.

## Explicit confirmations

- No X call, xAI call, or external API call was made.
- No quarantine or restore action occurred.
- No production state, config, metadata, receipt, or log was edited.
- No log was truncated or rotated.
- The live bot was not restarted or signalled; no restart is required for the standalone digest.
- No simulator was run.
- No force-push will be used.
