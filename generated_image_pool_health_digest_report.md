# Generated Image Pool Health Digest Report

## 1. Executive summary

Added a current-filesystem `## Generated image pool health` digest section. It validates active files, analysis/audit coverage and hashes, reconstructs currently quarantined assets from committed transaction manifests/files, reports policy and historical-use counts, and degrades to compact warnings without crashing the digest.

## 2. Files inspected

Inspected `mrs_log_digest.py`, review-app transaction/index implementation, active generated directory, generated analysis, identity audit, used history, actual quarantine transaction tree, local config path conventions, and live process state.

## 3. Active-pool architecture

Active files are lowercase `tg_<64 hex>.png`. Generated analysis schema 3 maps basename to hash and hash to analysis item. Identity audit schema 1 maps basename to hash/origin/policy record. Used history is a basename list and remains read-only historical evidence.

## 4. Quarantine architecture

The review app creates `generated_image_quarantine/transactions/<id>/manifest.json`, `images/`, and metadata backups. Current quarantine membership is determined from completed quarantine manifests whose preserved image files still exist. Failed/rolled-back transactions and files moved back by restoration are excluded. Completed restore manifests are counted separately.

## 5. Current active count

79 active generated PNG files.

## 6. Current quarantine count

4 unique currently quarantined images. Total known active plus current quarantine: 83.

## 7. Current active policy counts

- unrestricted: 64
- small_penalty: 9
- strong_penalty: 0
- origin_quote_only: 6

Quarantined records are excluded from these active counts.

## 8. Metadata and hash validation

79 active analysis path records and 79 active identity records exactly cover the 79 active files. All 79 active hashes agree across file content, generated analysis and identity audit; filename-derived origin hashes agree; all policies are recognised. Health is `OK` with zero warnings.

## 9. Used-history accounting

Nine generated basenames remain in historical used history. Six are active/previously used; 73 active images have never been used. Three quarantined images were previously used; one quarantined image was never used. Original `tNN.jpg` history is ignored.

## 10. Latest quarantine

Transaction `20260710T215739Z_2f30ebcebd`, timestamp `2026-07-10T21:57:39.266739+00:00`, completed with four images. One completed quarantine transaction and zero restore transactions currently exist.

## 11. Digest design

The compact text section reports counts, coverage, hash ratio, active policy distribution, historical use, latest quarantine/restore, transaction totals and overall health. Inconsistencies render in a three-column warning table capped at 20 rows with an omitted count.

## 12. Snapshot semantics

The section explicitly states that it is a current filesystem snapshot at digest generation time, independent of the selected log window. It is recomputed on every invocation and is not stored in `.mrs_log_digest_state.json`.

## 13. Error handling

Missing directories/files, malformed JSON, missing/unexpected records, stale hashes, origin mismatch, invalid policies, malformed transactions and incomplete preserved records become warnings. Benign absence of quarantine remains healthy with count zero. The remainder of the digest continues.

## 14. Files changed

- `mrs_log_digest.py`
- `tests/test_generated_image_pool_health_digest.py`
- this report

The real quarantine metadata and four tracked image deletions pre-existed this task and are intentionally not staged or modified.

## 15. Tests added

Seven synthetic tests cover healthy active-only, four-image quarantine, restoration, failed rollback, policy/usage accounting, missing/unexpected/stale/invalid metadata, malformed JSON, incomplete quarantine, output title/snapshot semantics, healthy rendering and warning capping.

## 16. Focused result

`7 passed in 0.07s`.

## 17. Existing digest result

`35 passed, 134 deselected in 29.55s`.

## 18. Other affected tests

Generated identity audit/shadow/production-policy tests: `67 passed in 0.30s`.

## 19. Py-compile

PASS for `mrs_log_digest.py` and the new test file.

## 20. Git diff check

PASS, no output.

## 21. Production-log isolation

The production log grew naturally by 566 bytes after the recorded pre-test point. The appended interval contains no pytest path, temporary test path, test filename, dummy marker or loopback marker. The log was not deleted, truncated or rotated.

## 22. Real no-state validation

`python3 mrs_log_digest.py --since '2026-07-10 23:00:00' --no-state mrsMThatcher.log` rendered 79 active, 4 quarantined, 83 total, complete 79-record coverage, 79/79 valid hashes, active policies 64/9/0/6, usage 6/73/3/1 and the four-image transaction, with `health = OK`. Resume state was not advanced.

## 23. Real files unchanged

No real image, generated metadata, identity audit, quarantine file or used history was changed by this task.

## 24. Live process

The live bot was not restarted or signalled. `mrs_log_digest.py` is standalone and is not imported by the production bot.

## 25. Commit

Implementation commit `ec304cd` with subject `Add generated pool health to log digest`; exactly the digest, focused tests and this report were included. Real curation changes were not staged.

## 26. Push

Normal push succeeded: `dc96998..ec304cd master -> master`. No force-push.

## 27. Final Git state

Task-specific digest/test/report will be committed separately. Real quarantine-generated tracked changes and unrelated untracked artifacts remain unstaged.

## Explicit confirmations

No X, xAI or external API call; no real quarantine/restore; no production state, config, metadata or receipt edit; no production-log truncation; no bot restart; no force-push.
