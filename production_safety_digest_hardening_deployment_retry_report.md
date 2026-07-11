# Production Safety and Digest Hardening Deployment Retry Report

## Status

**Deployment completed successfully.** The committed code was pushed, the
existing Python child alone was restarted through the established supervisor,
startup validation passed, a subsequent loop tick completed, and the read-only
digest reported healthy production state and metadata.

## Pre-Deployment Record

```text
timestamp             = 2026-07-11T08:06:25+01:00
commit                = 0c057d932513b689fcf238d0062c691917e9837a
branch/upstream       = master / origin/master
supervisor_pid        = 3631076
python_child_pid      = 72310
lock                  = pid=72310
last_main_post_id     = 2075831736206786642
last_quote_post_epoch = 1783751827
next_quote_post_epoch = 1783760100
last_meme_post_epoch  = 1783688644
next_meme_post_epoch  = 1783782000
next_meme_mode        = fallback
generated_spacing     = 2 original successes
pending_receipts      = none
```

Critical file hashes:

```text
e3c1485637436a2b1b60e8561f157cf60faf0ff2dfef21a002c886cab2760a73  mrsMThatcher.local.json
b04f64d5ccbf989b673c1abb5821d796a8f531af1826d583fce40d0df041501e  bot_state.json
f2a0e2e8dc396257fabcbd37c300b0efa7afe606721c68818da50b428cab5b14  images_used.json
d49699c97159c3f0b72c5a98adae2f1db6688f31505cc31169ac150c6a2541ec  lines_used.json
d2caccb23d3ff93f25510cde2920250c4c0f48bc87b3a768a5eee0490ef50413  generated_image_analysis.json
b751026b985e3afbae61066652b6f4fb98f8c04f5f2a444c1f60c5840106b059  generated_image_identity_dependence_audit.json
```

## Intended Runtime Files

- `mrsMThatcher2.py`
- `mrs_log_digest.py` (standalone digest; not imported by the bot)

Tests and documentation were committed but are not runtime dependencies.

## Validation

- Compilation: PASS.
- Focused representative logging/bootstrap/runway tests: `50 passed`.
- Focused safety, receipt, digest, integration, and selector tests:
  `258 passed, 358 deselected`.
- Full suite: `807 passed, 1 skipped, 1 warning`.
- Full-suite production-log appended interval: 3,396 bytes of genuine live
  scheduler ticks; zero pytest/test-fixture markers.
- Pytest production-log file descriptors: zero in repeated `/proc` checks.
- `git diff --check`: PASS.
- Production bootstrap/config validation: PASS.
- Active generated pool/audit: 79/79, complete coverage, valid hashes.
- Identity policy: enabled; penalties 6.0/15.0; policies 64 unrestricted,
  9 small penalty, 6 origin-only.

## Git

```text
commit = 0c057d9 Isolate test logging from production
push   = 2ab4816..0c057d9 master -> master
```

The commit contains only logging isolation, digest runway fallback, tests,
README, and the implementation report. Existing four-image curation changes,
runtime files, backups, and research/simulation artifacts were excluded.

## Restart

The established launcher is a persistent shell supervisor with a 60-second
restart delay. No new service mechanism was introduced.

```text
signal_timestamp = 2026-07-11T08:07:00+01:00
signal           = one SIGTERM to Python child only
wrapper_before   = 3631076
wrapper_after    = 3631076
old_child        = 72310
new_child        = 1585506
startup_time     = 2026-07-11 08:08:00
```

The old child exited, its file lock was verified released, and no process was
started manually. The unchanged supervisor started exactly one replacement
child. The lock now contains `pid=1585506`.

## Startup Verification

Relevant startup findings:

```text
Logging initialised: production rotating log path
Applied 24 local config overrides
Acquired instance lock
Original editorial shadow: enabled, unchanged (weight 0.32, cap 4.0)
Generated identity production policy: enabled
Generated identity audit: 79 items
Identity policies: 64 unrestricted, 9 small_penalty, 6 origin_quote_only
Identity penalties: 6.0 / 15.0
Quote used history: 323 entries
Image used history: 44 entries
Next quote restored: 2026-07-11 09:55:00
Next meme restored: 2026-07-11 16:00:00, fallback
Bot started successfully
```

The post-restart interval contained zero errors, critical records, tracebacks,
bootstrap-required failures, lock conflicts, or post-success events. The child
completed its startup tick and an additional tick at 08:09, then returned to
normal 60-second sleep. No post was emitted because of restart.

## Post-Restart Digest

The digest was run with explicit `--project-dir` and `--no-state`; resume state
was not advanced.

```text
active_generated_images       = 79
quarantined_generated_images  = 4
total_known_generated_images  = 83
active_analysis_records       = 79
active_identity_records       = 79
metadata_coverage             = complete
hash_validation               = 79 / 79 valid
health                        = OK
active_used_in_current_cycle  = 7
active_unused_in_current_cycle = 72
schedule runway basis         = schedule_model
schedule runway               = 20.2 days
```

The observed utilisation/rate history remains explicitly bounded and presently
contains insufficient clean generated-post history for an observed runway. The
schedule model is now available and clearly labelled.

## State Preservation

No receipt existed before or after restart. Hashes of local config, bot state,
used quote/image histories, generated analysis, and identity audit were identical
before and after restart. Startup performed its normal atomic state-save path,
but the authoritative state content did not change.

No production state, history, config, receipt, or metadata was manually edited.

## Final Process State

```text
supervisor_pid = 3631076
python_child   = 1585506
child_count    = 1
lock           = pid=1585506
status         = running normally
```

## Rollback Readiness

Rollback was not required. If an operational regression appears, code can be
returned to `2ab4816` without restoring or modifying durable production state,
then the current Python child alone can receive one SIGTERM for supervisor-led
restart. The pre-deployment hashes above remain the rollback evidence.

## Files Included And Excluded

Runtime code included in `0c057d9`:

- `mrsMThatcher2.py`
- `mrs_log_digest.py`

Supporting committed files:

- `README.md`
- `tests/conftest.py`
- `tests/test_logging_isolation.py`
- `tests/test_fail_safe_bootstrap_and_control.py`
- `tests/test_generated_image_pool_runway_digest.py`
- `production_log_test_isolation_implementation_report.md`

Deliberately excluded:

- production logs, config, state, histories, receipts, locks, and backups;
- generated analysis/audit working-tree changes and four quarantined PNG
  deletions;
- simulator outputs, review data, caches, and unrelated research scripts;
- the earlier blocked-attempt report.

## Explicit Confirmations

- Push performed normally: `2ab4816..0c057d9 master -> master`; no force-push.
- No external API call was deliberately made during deployment validation.
- No test post or reply was created.
- No production state/config/history/receipt/metadata was manually edited.
- Production logs were not deleted, truncated, rotated, repaired, or edited.
- The wrapper was not signalled or restarted.
- Only the Python child received one SIGTERM.
- Exactly one bot child is currently running normally.
- Deployment completed successfully; rollback was not needed.
