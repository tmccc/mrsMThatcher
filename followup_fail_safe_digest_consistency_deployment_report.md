# Follow-up fail-safe and digest consistency deployment report

## Executive result

Deployment completed successfully on 11 July 2026. Commit `7f76c11` was pushed
to `origin/master`, and the existing supervisor restarted the Python child after
one child-only `SIGTERM`. The new process bootstrapped successfully, loaded the
valid production configuration and generated identity audit, restored existing
schedules, and entered the normal loop. No rollback was required.

## Repository and commits

Pre-deployment commit: `db84eebf17247236f0dee85007ad928109875143`.

Deployment commit:

```text
7f76c11325c79682d45382009295bcb5628ecdd8
7f76c11 Harden production state and digest consistency
```

Files in the deployment commit:

- `README.md`
- `followup_fail_safe_digest_consistency_implementation_report.md`
- `mrsMThatcher2.py`
- `mrs_log_digest.py`
- `tests/test_fail_safe_bootstrap_and_control.py`
- `tests/test_followup_fail_safe_hardening.py`
- `tests/test_integration_harness.py`
- `tests/test_unit_helpers.py`

Push result: `db84eeb..7f76c11 master -> master`.

Generated-image curation metadata, four quarantined PNG deletions, locks, logs,
state/history/configuration, receipts, backups, review data, simulator output,
and unrelated scripts/reports were deliberately excluded.

## Pre-deployment validation

Production configuration was loaded with file logging disabled and no API
operation. Bootstrap and the durable state/history invariant passed. Effective
policy remained: generated identity production scoring enabled, generated
identity shadow disabled, original editorial shadow enabled, penalties 6.0 and
15.0, and generated spacing of two originals.

Strict generated identity audit validation found 79 valid items. The read-only
digest reported 79 active and 4 quarantined images, complete metadata coverage,
79/79 valid hashes, continuous 7-day and 30-day log coverage, and a 20.2-day
maximum-throughput minimum runway.

No regular, meme, confirmed-reply, or ambiguous-outcome receipt/barrier existed
at the restart gate. SHA-256 values for local configuration, state, histories,
generated analysis, and identity audit were recorded without displaying file
contents.

## Tests

Compilation and `git diff --check` passed. Pre-deployment focused safety/digest
tests reported `112 passed in 1.20s`. The final full suite immediately before
deployment reported:

```text
837 passed, 1 skipped, 1 warning in 405.45s
```

The warning was the existing Starlette `TestClient` deprecation warning. The
full-suite production-log isolation guard passed, and appended live-log regions
contained no pytest or fixture markers.

## Process arrangement and restart

The installed bot and launcher paths are symlinks to this repository. Before
restart, supervisor PID was `3631076`, Python child PID was `1585506`, and the
lock contained `pid=1585506`.

Restart procedure:

1. Rechecked all receipt/barrier files were absent.
2. Re-read the lock and confirmed PID `1585506` belonged to the supervisor.
3. Sent exactly one `SIGTERM` to Python child `1585506`.
4. Did not signal or stop supervisor `3631076`.
5. Allowed the supervisor's normal 60-second restart delay.

After restart, supervisor PID remained `3631076`, the sole Python child was
`1802779`, and the lock contained `pid=1802779`.

## Startup findings

New log entries showed production logging initialised, 24 local overrides
applied, one instance lock acquired, and state/history loaded. Generated identity
production scoring loaded 79 audit items: 64 unrestricted, 9 small-penalty,
0 strong-penalty, and 6 origin-quote-only.

The existing quote schedule was restored for `2026-07-11 09:55:00` and the meme
fallback for `2026-07-11 16:00:00`. `Bot started successfully` was followed by a
normal loop tick and sleep. There was no traceback, bootstrap exception,
metadata mismatch, receipt block, lock conflict, restart loop, or restart-induced
post.

The bot performed its normal startup state save/backup maintenance. No state,
history, config, metadata, receipt, or log was manually edited.

## Post-restart digest

The fresh `--no-state` digest again reported 79 active and 4 quarantined images,
complete metadata coverage, 79/79 valid hashes, continuous 7/30-day coverage,
and the 20.2-day maximum-throughput minimum runway. Resume state was not
advanced.

## Rollback readiness

No rollback was needed. Code rollback, if required, is to return to known-good
commit `db84eeb`, preserve current durable state, and use the same child-only
restart. State/history/configuration should not be restored without demonstrated
corruption.

## Final repository state

```text
7f76c11 Harden production state and digest consistency
db84eeb Document production safety deployment
0c057d9 Isolate test logging from production
2ab4816 Require bootstrap for operational commands
4a4afb4 Harden production safety and digest reporting
```

The working tree retains the pre-existing generated-image curation changes and
unrelated untracked artefacts, plus this report pending its documentation commit.

## Confirmations

- Deployment completed successfully.
- Exactly one bot child is running under the unchanged supervisor.
- Commit `7f76c11` was pushed normally; no force-push was used.
- No external API call was deliberately made during deployment validation.
- No test post or reply was created.
- No production state, config, history, metadata, receipt, or log was manually
  edited, deleted, truncated, rotated, or repaired.
- The production process alone performed normal startup state/backup writes.
- The canonical counterfactual simulation was not run.
- No unrelated working-tree file was staged or committed.
