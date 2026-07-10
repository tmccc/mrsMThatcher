# Original Editorial Shadow Scoring Deployment Report

Date: 2026-07-10

## 1. Executive result

Deployment successful; first live shadow observation pending.

The feature is enabled in observational shadow mode. The replacement Python
child started cleanly, validated all 69 original editorial records, retained the
existing schedule and generated-image spacing state, and remained healthy
through a second main-loop tick. The next regular post was scheduled for
2026-07-10 08:54:53 BST, so no post was forced and no live shadow event was
available during this deployment session.

## 2. Pre-deployment state

- Capture time: `2026-07-10T07:08:19+01:00`
- Branch: `master`
- Reviewed commit: `aa8d86e Add original image editorial shadow scoring`
- Wrapper PID: `3631076`
- Python child PID: `2714474`
- Instance lock: `mrsMThatcher.lock`, containing `pid=2714474`
- Last main post ID: `2075454587549471027`
- Last regular quote post epoch: `1783661908`
- Next regular quote post epoch: `1783670093`
- Next regular quote post: `2026-07-10T08:54:53+01:00`
- `original_regular_posts_since_generated_image`: `0`
- Last regular image: `tg_faf99f3030693b0a55f0116194551792f3c4ea51261d7310eca7fb4d33b667d5.png`
- Generated-image pool: enabled
- Required original posts between generated images: `2`
- Generated-image eligibility at capture: blocked by spacing (`0 < 2`)

The four shadow config keys were absent before deployment, so script defaults
left shadow mode disabled.

The tracked working tree was clean. Numerous pre-existing untracked analysis,
backup, corpus, lock and report artifacts were present and were not altered or
staged.

## 3. Reviewed implementation verification

The deployed source was inspected directly, rather than relying only on the
implementation report. It confirmed:

- shadow mode defaults to disabled;
- the metadata loader runs at startup only when shadow mode is enabled;
- exact 12-dimension validation and finite numeric validation are present;
- production chooses `chosen = random.choice(tied)` before shadow evaluation;
- shadow evaluation receives the actual phase-specific production `scored` list;
- only candidates with `image_source == "original"` enter shadow competition;
- generated production winners have null production editorial score/rank fields;
- `ORIGINAL_EDITORIAL_SHADOW_RESULT` is logging-only;
- the digest uses comparable production-original observations as its percentage denominator;
- digest headings use `line_no` and `positive shadow-winner dimensions`.

The installed `/usr/local/bin/mrsMThatcher2.py` SHA-256 matched the reviewed
repository file before restart:

```text
32cdeec1906e4157ec637d41cb6b69ecdf4d73d8153741b3e98980c94a812b08
```

## 4. Pre-deployment validation

Compilation:

```text
python3 -m py_compile mrsMThatcher2.py mrs_log_digest.py tests/test_original_editorial_shadow_scoring.py
passed
```

Dedicated tests:

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_original_editorial_shadow_scoring.py
41 passed in 0.22s
```

Directly affected digest tests:

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_integration_harness.py -k digest
35 passed, 132 deselected in 7.39s
```

Whitespace validation:

```text
git diff --check
passed
```

The dedicated tests import the bot through the isolated unit-test harness,
which sets a temporary base directory and test log. No synthetic test events
were written to the production log.

An isolated loader probe used dummy credentials and loopback-only fake endpoints.
It made no network call and reported:

```text
validated_original_editorial_items=69
```

This validated all current original image hashes, exact dimension schemas and
numeric ranges before restart.

## 5. Backup

Backup directory:

```text
/disks/disk1/etc/mrsMThatcher/pre_original_editorial_shadow_deployment_20260710_070852/
```

Copied with metadata preserved:

- `mrsMThatcher2.py`
- `mrs_log_digest.py`
- `mrsMThatcher.local.json`
- `bot_state.json`
- `images_used.json`
- `lines_used.json`

No production receipt files existed at backup time, so none were fabricated.

Manifest:

```text
/disks/disk1/etc/mrsMThatcher/pre_original_editorial_shadow_deployment_20260710_070852/MANIFEST.txt
```

## 6. Git deployment commit

The implementation had already been intentionally committed and pushed before
this deployment task:

```text
aa8d86e Add original image editorial shadow scoring
```

That reviewed commit contains exactly these feature files:

- `mrsMThatcher2.py`
- `mrs_log_digest.py`
- `tests/test_original_editorial_shadow_scoring.py`
- `original_editorial_shadow_scoring_implementation_report.md`
- `original_image_editorial_analysis_experiment_v1.json`

No duplicate deployment commit was created. Nothing was staged, committed or
pushed during deployment.

## 7. Local configuration

Only these local, ignored settings were added:

```json
{
  "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING": true,
  "ORIGINAL_EDITORIAL_ANALYSIS_FILE": "/disks/disk1/etc/mrsMThatcher/original_image_editorial_analysis_experiment_v1.json",
  "ORIGINAL_EDITORIAL_SHADOW_WEIGHT": 0.32,
  "ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT": 4.0
}
```

`mrsMThatcher.local.json` parsed successfully and is excluded by the tracked
`.gitignore`. It was not staged or committed. Existing generated-image settings,
schedules, reply budgets and API settings were preserved.

## 8. Child restart

- Wrapper PID before: `3631076`
- Old child PID: `2714474`
- Signal: one `SIGTERM` sent with `kill -TERM 2714474`
- Signal timestamp: `2026-07-10T07:09:55+01:00`
- Wrapper restart delay: its configured 60 seconds
- New child PID: `1647511`
- New child start: `2026-07-10 07:10:54 BST`
- Wrapper PID after: `3631076`
- New lock contents: `pid=1647511`

The old child exited, the wrapper remained running, and exactly one replacement
child appeared. No second signal was sent.

## 9. Startup verification

Relevant new log lines:

```text
2026-07-10 07:10:55 INFO apply_local_config - Applied 19 local config override(s) from /disks/disk1/etc/mrsMThatcher/mrsMThatcher.local.json
2026-07-10 07:10:55 INFO acquire_instance_lock - Acquired instance lock /disks/disk1/etc/mrsMThatcher/mrsMThatcher.lock
2026-07-10 07:10:55 INFO main - Bot starting
2026-07-10 07:10:55 INFO main - Images found at startup=69
2026-07-10 07:10:55 INFO validate_original_editorial_shadow_startup - Original editorial shadow scoring enabled. analysis_file=/disks/disk1/etc/mrsMThatcher/original_image_editorial_analysis_experiment_v1.json original_items=69 weight=0.32 max_abs_adjustment=4.0
2026-07-10 07:10:55 INFO main - Existing next_quote_post_epoch=1783670093, human=2026-07-10 08:54:53
2026-07-10 07:10:55 INFO main - Bot started successfully
```

No hash mismatch, dimension-schema error, config validation error, traceback,
receipt block, duplicate process or API cooldown appeared. A second main-loop
tick at `07:11:55` completed normally, and the child remained alive.

## 10. First live shadow observation

Pending. No regular quote/image selection occurred during the deployment window.
The next regular post was about 1 hour 43 minutes away, and no post was forced.

Consequently, no claim is made yet about a first live
`ORIGINAL_EDITORIAL_SHADOW_RESULT` payload.

## 11. Digest verification

Live event-window digest verification is pending because no live shadow event
occurred. Pre-deployment digest tests passed and verify that:

- the section is titled `## Original editorial shadow scoring`;
- it explicitly labels results shadow-only;
- it does not imply the shadow image was posted;
- it uses the comparable production-original denominator;
- generated production winners are counted separately.

## 12. Production safety verification

- The production winner is fixed before shadow scoring runs.
- Shadow scoring does not replace or feed back into `chosen`.
- No additional posting or API call was introduced.
- No shadow-specific state file exists or is written.
- Shadow scoring does not mutate image history, quote history, spacing state,
  schedules or receipts.
- The startup process performed its normal state/history reconciliation and
  backup writes; no production state was manually edited.
- Generated-image spacing remained `0` originals since the last generated image,
  with requirement `2`.
- No active receipt existed before or after restart.

No actual post occurred during this deployment session, so actual-image versus
shadow-image live confirmation remains tied to the first pending observation.

## 13. Final process and schedule state

Captured at `2026-07-10T07:12:42+01:00`:

- Wrapper PID: `3631076`
- Python child PID: `1647511`
- Instance lock: `pid=1647511`
- Last main post ID: `2075454587549471027`
- Last regular quote post epoch: `1783661908`
- Next regular quote post epoch: `1783670093`
- Next regular quote post: `2026-07-10T08:54:53+01:00`
- `original_regular_posts_since_generated_image`: `0`
- Last regular image: `tg_faf99f3030693b0a55f0116194551792f3c4ea51261d7310eca7fb4d33b667d5.png`
- Generated images remain blocked by the existing 2-original-post spacing rule.

## 14. Final Git state

```text
aa8d86e Add original image editorial shadow scoring
```

Plain `git diff --stat` is empty because tracked files are unchanged. `git
status --short` contains only pre-existing untracked artifacts plus:

```text
?? pre_original_editorial_shadow_deployment_20260710_070852/
?? original_editorial_shadow_scoring_deployment_report.md
```

Nothing was staged, committed or pushed during deployment.

## 15. Explicit confirmations

- No manual X call occurred.
- No manual xAI call occurred.
- No manual external API call occurred.
- No test post was created.
- The wrapper was not restarted or signalled.
- Only the Python child was restarted.
- Production state was not manually edited.
- Receipts were not manually edited, created or removed.
- Logs were not deleted or rotated.
- Shadow mode is observational only.
- Nothing was pushed.

## Rollback instructions

### Quick disable

1. Edit only `mrsMThatcher.local.json` and set:

   ```json
   "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING": false
   ```

2. Validate the JSON:

   ```bash
   python3 -m json.tool mrsMThatcher.local.json >/dev/null
   ```

3. Identify the wrapper and current child without relying on stale PIDs:

   ```bash
   ps -ef | grep '[r]unMrsMThatcher2'
   ps -ef | grep '[m]rsMThatcher2.py'
   ```

4. Send one SIGTERM to the current Python child only:

   ```bash
   kill -TERM <current-python-child-pid>
   ```

5. Do not signal the wrapper. Allow its configured 60-second delay to start a
   replacement child.

6. Verify the wrapper PID is unchanged, a new child PID appears, the lock names
   the new child, and startup succeeds without the shadow-enabled message.

### Restore from the deployment backup if ever required

Quick disable should be sufficient because the feature is default-off and
observational. If code/config restoration is nevertheless required:

1. Disable shadow mode and stop only the current Python child as above.
2. Before the wrapper's next child starts, restore the reviewed files needed by
   the incident response from:

   ```text
   /disks/disk1/etc/mrsMThatcher/pre_original_editorial_shadow_deployment_20260710_070852/
   ```

3. Restore config with metadata preserved:

   ```bash
   cp -p pre_original_editorial_shadow_deployment_20260710_070852/mrsMThatcher.local.json ./mrsMThatcher.local.json
   ```

4. Restore code only if explicitly required by the diagnosed fault:

   ```bash
   cp -p pre_original_editorial_shadow_deployment_20260710_070852/mrsMThatcher2.py ./mrsMThatcher2.py
   cp -p pre_original_editorial_shadow_deployment_20260710_070852/mrs_log_digest.py ./mrs_log_digest.py
   ```

5. Do not restore `bot_state.json`, `images_used.json` or `lines_used.json`
   merely to disable shadow mode. Those snapshots are emergency evidence and
   restoring them later could discard legitimate production activity.
6. Verify config, process ownership, lock, startup logs, schedule and receipts.
