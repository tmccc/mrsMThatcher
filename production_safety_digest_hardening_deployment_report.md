# Production Safety and Digest Hardening Deployment Report

## Executive Result

**Deployment blocked before restart.** Compilation, configuration validation,
metadata validation, focused tests, and the full suite passed, but the test run
appended pytest fixture records to the production log. This violated the
production-log isolation gate and the instruction not to edit production logs.
The deployment was stopped immediately after confirming the contamination.

The production bot was not stopped, restarted, or signalled. The existing child
remains running under the existing supervisor.

## Repository State

Pre-deployment HEAD:

```text
2ab48160d5d4277bd170c3034759b4d06a4ffeff
```

Relevant committed changes were already present on `master` and
`origin/master`:

```text
2ab4816 Require bootstrap for operational commands
4a4afb4 Harden production safety and digest reporting
cbe4ca7 Finalise generated pool runway report
e3150e8 Add generated pool runway metrics
88c8691 Finalise generated pool health report
```

There was no uncommitted source or test change to package. No deployment commit
was created.

Deliberately excluded working-tree changes:

- `generated_image_analysis.json`
- `generated_image_identity_dependence_audit.json`
- four deleted generated PNGs corresponding to the completed quarantine action
- untracked analysis scripts, backups, reports, locks, review data, and
  simulation output

The active pool and modified metadata were internally consistent, but these
curation changes were not mixed into a deployment commit.

## Configuration And Metadata Validation

Production bootstrap validation completed in an isolated process without API
calls or production file logging. Effective settings included:

```text
generated identity production scoring = enabled
generated identity shadow scoring     = disabled
original editorial shadow scoring     = enabled
small penalty                         = 6.0
strong penalty                        = 15.0
```

Strict production metadata loaders reported:

```text
active generated pool = 79
identity audit items  = 79
unrestricted          = 64
small_penalty         = 9
strong_penalty        = 0
origin_quote_only     = 6
```

The read-only digest (`--project-dir ... --no-state`) reported:

```text
active generated images      = 79
quarantined generated images = 4
total known                  = 83
metadata coverage            = complete
hash validation              = 79 / 79 valid
health                       = OK
```

The utilisation history was explicitly bounded by the available structured
logs. Its observed window was short and contained no generated successful post,
so observed runway was unavailable. The digest displayed that limitation rather
than manufacturing an estimate. Schedule runway was also unavailable because
the digest did not resolve schedule keys from the current local config; this was
not a metadata/startup failure but remains an operational reporting limitation.

## Tests

Compilation:

```text
python3 -m py_compile mrsMThatcher2.py mrs_log_digest.py
PASS
```

Focused safety, digest, receipt, utilisation, and selector tests:

```text
176 passed, 275 deselected in 13.49s
```

Full suite:

```text
794 passed, 1 skipped, 1 warning in 397.75s
```

`git diff --check`: PASS.

## Blocking Validation Failure

Before tests:

```text
mrsMThatcher.log size = 345066 bytes
```

After tests:

```text
mrsMThatcher.log size = 1137723 bytes
appended interval     = 792657 bytes
pytest marker lines   = 203
```

The appended interval contains clear synthetic markers such as
`/tmp/pytest-of-tonym/pytest-553/...`, dummy fixture post events, receipt tests,
and loopback API endpoints. Genuine live main-loop ticks also occur in the same
interval. The log was not truncated, rotated, rewritten, or otherwise repaired,
because preserving production evidence was a non-negotiable requirement.

This is a test-isolation defect. Passing test results are not sufficient to
override the failed deployment gate. The test logging setup must be corrected
and validated without altering the existing production log before deployment is
retried.

## Process Arrangement

No restart was attempted.

```text
supervisor PID = 3631076  /bin/bash /usr/local/bin/runMrsMThatcher2
child PID      = 72310    python3 /usr/local/bin/mrsMThatcher2.py
lock contents  = pid=72310
```

The launcher is a persistent restart loop with a 60-second delay. The supported
future restart method remains one SIGTERM to the Python child only, allowing the
unchanged supervisor to restart it. That method was not used in this attempt.

No main-post, meme-post, or confirmed-reply receipt was present when deployment
was stopped.

## Rollback Status

No rollback was required because the running process and code loaded in that
process were never changed. Critical file hashes were recorded for a future
retry; no state, history, config, receipt, or metadata file was manually edited.

## Final Git State

```text
2ab4816 Require bootstrap for operational commands
4a4afb4 Harden production safety and digest reporting
cbe4ca7 Finalise generated pool runway report
e3150e8 Add generated pool runway metrics
88c8691 Finalise generated pool health report
```

`git status --short` still contains only the pre-existing generated metadata,
four quarantined image deletions, unrelated untracked artifacts, and this report.

## Explicit Confirmations

- Nothing was pushed during this deployment attempt.
- No external X, xAI, or other external API call was deliberately made.
- No test post or reply was created on X.
- Production state, configuration, history, and receipts were not edited.
- The production log was unintentionally appended by tests; it was not deleted,
  truncated, rotated, or repaired.
- The production bot was not stopped, restarted, or signalled.
- The deployment did not complete.
- The existing bot process is currently running normally.
