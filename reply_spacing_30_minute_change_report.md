# Reply Spacing 30-Minute Change Report

> Historical deployment record: this report documents the July 2026 change to
> 1,800 seconds. The current production policy supersedes it with a 900-second
> (15-minute) interval; historical values and observations below are retained.

## 1. Executive result

**Reasoning verdict: 30-minute global spacing is judged safe.**

Implementation and pre-deployment validation succeeded. Deployment details are appended below after commit, push, configuration, restart, and startup verification.

## 2. Current architecture and config precedence

Before this change, `MIN_SECONDS_BETWEEN_REPLIES` was defined as `3600` in `mrsMThatcher2.py`. `apply_local_config()` then read the ignored `mrsMThatcher.local.json`, validated allowed overrides, and replaced the source default. Production local config also contained `3600`, so both layers agreed and production's effective value was one hour.

Changing source alone would not change production because the local override would continue to win. Changing local config alone would deploy 1800 but leave a misleading durable repository default. The correct durable deployment therefore changes the committed default and, after commit, the ignored local override to 1800.

The same setting is read in three production control points:

1. `maybe_reply_to_mentions()` gates normal mentions and hot-post replies against persisted `last_reply_epoch`.
2. `maybe_reply_to_quote_tweets()` gates quote-tweet replies against the same timestamp.
3. `run_reply_lane_checks_for_tick()` prevents either due lane from running while shared spacing is closed and coordinates lane priority when it opens.

The digest reads `MIN_SECONDS_BETWEEN_REPLIES` dynamically from logged startup configuration. No hard-coded one-hour wording or calculation required a digest change.

## 3. Successful-reply timestamp semantics

`last_reply_epoch` advances only after a confirmed successful automatic reply or through confirmed-reply receipt reconciliation. Both mention and quote-tweet receipts carry `reply_epoch`; reconciliation uses the maximum of current and receipt time and is idempotent for counters/history. The timestamp is persisted in `bot_state.json`, so restart cannot bypass spacing.

Grok `SKIP`, direct spam/relevance skip, local candidate skip, fetch/check attempt, and failed/unconfirmed post do not create a successful-reply timestamp. Existing confirmed-post durability remains authoritative where X succeeded but local completion was interrupted.

## 4. Operational reasoning

### Daily caps

The total automatic-reply cap remains 24/day and the quote-reply cap remains 12/day. Although a 30-minute interval has a theoretical 48-per-day time capacity, the unchanged total cap limits actual automatic replies to 24, and all per-author and filtering rules remain active.

### Polling cadence

- Normal mention checks remain every 900 seconds.
- Quote-tweet checks remain every 3600 seconds.
- Quote spacing retries remain every 300 seconds when a quote check was blocked only by shared spacing.

At 1800 seconds, normal eligibility resumes on the first due poll at or after the boundary. Quote retries may wake earlier, but both scheduler and lane functions exit before candidate fetching/xAI while spacing is closed.

### Lane priority and fairness

Both lanes share the same timestamp. When both are due as spacing opens, existing `next_reply_lane_priority` chooses one. If that lane posts, it resets `last_reply_epoch`, immediately closing both lanes for another 1800 seconds and flipping priority as before. The second lane cannot burst immediately afterward.

### Burst risk

No new burst mechanism is introduced. The shortest successful-reply separation remains a hard 1800 seconds, including across lane boundaries and restarts. Daily caps, per-author caps, spam filters, Grok `SKIP`, lane cadence, and API cooldowns add further brakes. No retry or priority constant assumes 3600.

## 5. Exact implementation change

- `mrsMThatcher2.py`: durable default changed from `3600` to `1800`.
- `tests/test_integration_harness.py`: added exact 1799/1800 boundary coverage in both cross-lane orders and explicit assertions that Grok/spam skips do not change `last_reply_epoch`.
- `tests/test_unit_helpers.py`: updated the production-style config fixture to 1800 and added a durable-default assertion.
- `mrsMThatcher.local.json`: remains 3600 during implementation/testing; it will be changed to 1800 only during the deployment configuration phase and will not be committed.

Existing integration fixtures using 3600 to test arbitrary spacing/restart behavior remain unchanged because they test parameterized mechanics, not the production policy value.

## 6. Test coverage

The new parameterized integration regression proves both sequences:

- normal reply at T -> quote lane blocked at T+1799 -> quote reply allowed at T+1800;
- quote reply at T -> normal lane blocked at T+1799 -> normal reply allowed at T+1800.

It verifies no request occurs while blocked, the shared timestamp remains T, the second successful reply changes it to T+1800, lane order is preserved, and total/quote counters remain correct. Existing restart, cap, priority, receipt, media, and prompt tests were rerun.

## 7. Focused test results

Initial boundary/default/skip tests:

```text
6 passed in 5.12s
```

Broader integration spacing/restart/priority/cap coverage:

```text
27 passed, 142 deselected in 16.25s
```

Receipt/prompt/media unit coverage:

```text
11 passed, 320 deselected in 3.08s
```

## 8. Full-suite result

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
630 passed, 1 skipped, 1 warning in 191.10s
```

The warning is the existing Starlette `TestClient` deprecation warning.

## 9. py_compile

```text
python3 -m py_compile mrsMThatcher2.py
PASS (no output)
```

## 10. git diff --check

```text
git diff --check
PASS (no output)
```

## 11. Production-log isolation

- Before full suite: `1873495` bytes, mtime `2026-07-10 15:32:21.000351600 +0100`
- After full suite: `1877182` bytes
- Natural live append: `3687` bytes
- Appended bytes contained no pytest path, pytest temporary path, unit-import path, dummy credential, loopback endpoint, fake-API marker, test-main-tick marker, synthetic parity reply, or test post ID.
- The production log was not deleted, truncated, rotated, or edited.

## 12. Intended production value

After deployment:

```text
MIN_SECONDS_BETWEEN_REPLIES = 1800
```

This permits eligibility after 30 minutes; it does not force a reply.

## 13. Files modified before commit

- `mrsMThatcher2.py`
- `tests/test_integration_harness.py`
- `tests/test_unit_helpers.py`
- `reply_spacing_30_minute_change_report.md` (this report)

No digest change is required.

## Deployment record

## 14. Final deployment result

**Deployment successful; live 30-minute cadence observation pending.**

The implementation was committed and pushed, the ignored production override was changed to 1800, one Python child was restarted through the unchanged wrapper, startup logged the effective value, and a subsequent main-loop tick was healthy. No suitable genuine two-reply timing sequence occurred during bounded monitoring.

## 15. Pre-deployment production state

- Capture time: `2026-07-10T15:36:58+01:00`
- Branch: `master`
- Commit before deployment: `e60e491 Improve humour handling in reply prompts`
- Wrapper PID: `3631076`
- Python child PID: `2350386`
- Lock: `pid=2350386`
- Last main post ID: `2075584406429864258`
- Last regular post epoch: `1783692859`
- Next regular post epoch: `1783701192` (`2026-07-10 17:33:12 BST`)
- Daily reply count: `4`
- Daily quote-reply count: `1`
- Next reply lane priority: `normal`
- Last successful reply epoch: `1783692371`
- Last seen mention ID: `2075522429074510038`
- Read cooldown: none
- Write/xAI/quote cooldown epochs: `0`
- Generated spacing count: `1`
- Last regular image: `t49.jpg`
- Original editorial shadow: enabled
- Generated identity-policy shadow: enabled
- Production receipts: none

Pre-change effective reply configuration:

```text
MIN_SECONDS_BETWEEN_REPLIES=3600
REPLY_CHECK_EVERY_SECONDS=900
QUOTE_CHECK_EVERY_SECONDS=3600
QUOTE_CHECK_SPACING_RETRY_SECONDS=300
MAX_AUTO_REPLIES_PER_DAY=24
MAX_QUOTE_REPLIES_PER_DAY=12
```

## 16. Backup

- Directory: `pre_reply_spacing_30min_deployment_20260710_153705/`
- Manifest: `pre_reply_spacing_30min_deployment_20260710_153705/MANIFEST.txt`
- Effective pre-change spacing recorded in manifest: `3600`
- Files copied with metadata preserved:
  - `mrsMThatcher2.py`
  - `mrs_log_digest.py`
  - `mrsMThatcher.local.json`
  - `bot_state.json`
  - `images_used.json`
  - `lines_used.json`
- Receipt files copied: none existed

## 17. Commit and push

- Implementation commit: `988b0be Reduce minimum reply spacing to 30 minutes`
- Files committed:
  - `mrsMThatcher2.py`
  - `tests/test_integration_harness.py`
  - `tests/test_unit_helpers.py`
  - `reply_spacing_30_minute_change_report.md`
- Commit size: `4 files changed, 234 insertions(+), 3 deletions(-)`
- Push: `e60e491..988b0be master -> master`
- Upstream: `origin/master`
- No unrelated files were staged or committed.
- No force push occurred.

## 18. Local production configuration

After the commit and push, only this ignored local value changed:

```json
"MIN_SECONDS_BETWEEN_REPLIES": 1800
```

JSON validation passed. The local config was not committed. These related values remained unchanged:

```text
REPLY_CHECK_EVERY_SECONDS=900
QUOTE_CHECK_EVERY_SECONDS=3600
QUOTE_CHECK_SPACING_RETRY_SECONDS=300
MAX_AUTO_REPLIES_PER_DAY=24
MAX_QUOTE_REPLIES_PER_DAY=12
ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING=true
ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING=true
ENABLE_GENERATED_IMAGE_POOL=true
GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN=2
```

## 19. Restart

- Signal time: `2026-07-10T15:38:13+01:00`
- Signal: one `SIGTERM`
- Old Python child: `2350386`
- New Python child: `3046050`
- Wrapper before/after: `3631076`
- New child start: `2026-07-10 15:39:13 BST`
- Startup success: `2026-07-10 15:39:14 BST`
- Approximate wrapper restart interval: 60 seconds
- New lock: `pid=3046050`

The wrapper was neither signalled nor restarted. No repeated signal was sent, and exactly one replacement child appeared.

## 20. Startup verification

Relevant post-restart lines:

```text
2026-07-10 15:39:14 INFO acquire_instance_lock - Acquired instance lock /disks/disk1/etc/mrsMThatcher/mrsMThatcher.lock
2026-07-10 15:39:14 INFO main - Bot starting
2026-07-10 15:39:14 INFO main - Config: REPLY_CHECK_EVERY_SECONDS=900
2026-07-10 15:39:14 INFO main - Config: MAX_AUTO_REPLIES_PER_DAY=24
2026-07-10 15:39:14 INFO main - Config: MIN_SECONDS_BETWEEN_REPLIES=1800
2026-07-10 15:39:14 INFO main - Config: QUOTE_CHECK_EVERY_SECONDS=3600
2026-07-10 15:39:14 INFO main - Config: QUOTE_CHECK_SPACING_RETRY_SECONDS=300
2026-07-10 15:39:14 INFO main - Config: MAX_QUOTE_REPLIES_PER_DAY=12
2026-07-10 15:39:14 INFO validate_original_editorial_shadow_startup - Original editorial shadow scoring enabled. original_items=69 weight=0.32 max_abs_adjustment=4.0
2026-07-10 15:39:14 INFO validate_generated_identity_shadow_startup - Generated identity-policy shadow scoring enabled. items=83 policies={'origin_quote_only': 6, 'small_penalty': 9, 'unrestricted': 68} small_penalty=6.0 strong_penalty=15.0
2026-07-10 15:39:14 INFO main - Existing next_quote_post_epoch=1783701192, human=2026-07-10 17:33:12
2026-07-10 15:39:14 INFO main - Bot started successfully
2026-07-10 15:40:15 DEBUG main - Main loop tick. epoch=1783694415
```

No traceback, config error, receipt block, duplicate process, or unexpected API cooldown appeared. State and schedule restored normally, and the child remained alive after a further tick.

## 21. Post-deployment effective value

The source default, ignored local config, and startup log all agree:

```text
MIN_SECONDS_BETWEEN_REPLIES = 1800
```

Unchanged behavior:

- total daily reply cap: 24;
- quote daily reply cap: 12;
- normal check cadence: 900 seconds;
- quote check cadence: 3600 seconds;
- quote spacing retry: 300 seconds;
- shared lane priority and global timestamp;
- Grok `SKIP` handling;
- media and multimodal fallback handling;
- confirmed-reply receipts and reconciliation;
- original editorial shadow;
- generated identity-policy shadow;
- generated-image spacing.

## 22. Live spacing observation

**Pending.** No genuine reply sequence suitable for proving both the blocked pre-1800 period and reopened post-1800 eligibility occurred during the bounded deployment session. No mention or reply was forced, and no manual X/xAI request was made.

The first post-restart tick occurred normally. The next normal mention check was still about 14 minutes away, so waiting for a complete live cadence sequence would not have been reasonable. Automated tests provide deterministic 1799/1800 cross-lane proof in both lane orders.

## 23. Final process and state

At `2026-07-10T15:40:43+01:00`:

- Wrapper PID: `3631076`
- Python child PID: `3046050`
- Lock: `pid=3046050`
- Last main post ID: `2075584406429864258`
- Last regular post epoch: `1783692859`
- Next regular post epoch: `1783701192`
- Daily reply count: `4`
- Daily quote-reply count: `1`
- Next reply lane priority: `normal`
- Last successful reply epoch: `1783692371`
- Last seen mention ID: `2075522429074510038`
- Read cooldown: none
- Write/xAI/quote cooldown epochs: `0`
- Generated spacing count: `1`
- Last regular image: `t49.jpg`
- Production receipts: none

## 24. Production safety confirmations

- No manual X call occurred.
- No manual xAI call occurred.
- No manual external API call occurred.
- No test post was created.
- Production state was not manually edited.
- Receipts were not manually edited.
- Logs were not deleted, truncated, or rotated.
- The wrapper was not restarted or signalled.
- Only the Python child was restarted, using one SIGTERM.
- Original editorial shadow was unchanged.
- Generated identity-policy shadow was unchanged.
- No force push occurred.

The running bot naturally read and durably saved its existing state during startup; this is normal runtime behavior, not manual state alteration.

## 25. Final Git state

Implementation HEAD after deployment:

```text
988b0be Reduce minimum reply spacing to 30 minutes
## master...origin/master
```

At that point `origin/master...master` was `0 0`, no tracked implementation diff remained, and only pre-existing unrelated untracked artifacts plus the deployment backup remained. This report was then finalized with deployment facts.

## 26. Rollback instructions

Rollback is appropriate only for a genuine spacing regression such as a reply before 1800 seconds, duplicate/burst behavior, broken persistence/lane logic, unexpected extra xAI calls, or production instability. Two replies within one clock hour are now expected and are not alone a rollback reason.

1. Record current process, state, receipt, config, and relevant log details.
2. Change only ignored local `MIN_SECONDS_BETWEEN_REPLIES` from `1800` back to `3600` and validate JSON.
3. If source rollback is also required, review and restore `mrsMThatcher2.py` from `pre_reply_spacing_30min_deployment_20260710_153705/`; do not restore stale runtime state/history.
4. Leave both shadow features and all caps/cadences unchanged.
5. Identify the current Python child freshly with `ps -o pid,ppid,cmd --ppid 3631076`.
6. Send one `SIGTERM` to that child only; do not signal the wrapper.
7. Wait for the normal replacement delay and verify one child, matching lock, effective 3600 startup log, restored schedule/state, both shadows, and a further healthy loop tick.
8. Commit/push any source rollback only after separate review. Never force-push.
