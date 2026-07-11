# Follow-up fail-safe and digest consistency implementation report

## 1. Summary

Implemented the justified follow-up findings without deploying or touching live
state. Operational startup now refuses an incomplete durable state/history set;
a deliberate `--initialise` command creates a new set without posting. Runtime
control timestamps and string local-config overrides are strictly typed. The
digest expands canonical numeric log rotations, serialises stateful runs with a
separate lock, and labels schedule runway as a maximum-throughput minimum.

Ambiguous X POST transport outcomes now create a durable reconciliation barrier
and block further posting rather than being treated as confirmed failures that
may be retried.

## 2. State/history ambiguity and explicit initialisation

Normal operational entry points call `require_established_installation()` after
successful bootstrap and before lock acquisition. `lines_used.json` and
`images_used.json` must be regular files. State requires `bot_state.json` or one
of the configured state backups, preserving the existing backup-recovery path.

`python3 mrsMThatcher2.py --initialise` is the only supported empty-installation
path. It refuses any existing marker, primary durable file, configured state
backup, receipt, or ambiguous-outcome marker. It creates valid state and empty
histories durably, initialises quote/meme schedules, writes the installation
marker last, rolls back files created by a failed attempt, makes no API call,
and does not enter an operational command.

Missing-state matrix:

| Condition | Result |
|---|---|
| Complete primary set | accepted |
| Primary state absent, configured backup present | existing recovery path allowed |
| State and all configured backups absent | startup refused |
| Either history absent or not a regular file | startup refused |
| Partial primary set | startup refused |
| Marker present but durable set incomplete | startup refused |
| Empty directory under normal startup | startup refused |
| Empty directory under explicit initialisation | durable set created |

## 3. Control validation and cache identity

Control timestamps reject booleans, null, numeric strings, fractional floats,
NaN/infinity, negative epochs, and epochs after `4102531200`. Integer epochs,
integral finite floats, and the already documented local/ISO date strings remain
supported. Any invalid timestamp invalidates the complete document, retaining
the prior valid control or failing closed with `disable_all`.

The cache identity is now resolved path, device, inode, size, and nanosecond
mtime. Atomic replacement and same-mtime inode changes are detected while an
unchanged malformed file remains log-suppressed.

## 4. String local-config validation

String defaults now accept only actual JSON strings. Non-text JSON values are
rejected. Non-`MEME_POST_TEXT` strings must be non-empty and free of leading or
trailing whitespace. Unsafe control characters are rejected. Config application
remains atomic: any coercion or cross-field error prevents all overrides.

## 5. Digest log discovery and default parity

An explicit canonical `mrsMThatcher.log` resolves relative to `--project-dir`
and automatically adds only existing numeric siblings (`.1`, `.2`, etc.) in
numeric order. Explicit duplicates are removed. Self-test and arbitrary sibling
files are excluded. Explicit non-canonical files retain exact-file semantics.

A parity test imports production source defaults without bootstrap and compares
the four standalone runway defaults: pool enablement, post minimum/maximum, and
generated spacing.

## 6. Digest locking

Stateful runs hold a dedicated nonblocking `flock` on the resume-state lock from
resume read through report delivery and checkpoint advancement. Lock diagnostics
record PID and acquisition time. OS lock ownership makes stale lock-file content
safe. Locks release on success and exceptions. `--no-state` stdout runs remain
concurrent; `--no-state --output` locks the output destination to prevent
colliding atomic replacements. The production bot lock is never used.

## 7. Runway terminology

The machine key `schedule_model_days_to_cycle_exhaustion` is unchanged. Markdown
now describes it as a “maximum-throughput minimum” and states that it assumes a
generated image is selected whenever spacing permits.

## 8. Ambiguous POST outcomes

Transport failure while creating `/2/tweets` raises
`AmbiguousRemotePostOutcome`, distinct from confirmed HTTP failure. The payload
is represented by non-secret reconciliation metadata and a text SHA-256 in
`ambiguous_post_outcome.json`. The barrier is written durably and all operational
entry points and `create_post()` refuse further posting while it exists. This
does not claim exactly-once posting; an operator must determine the remote result
before removing the barrier. Confirmed HTTP failures and confirmed successes
retain their existing behavior.

## 9. Files changed

- `mrsMThatcher2.py`
- `mrs_log_digest.py`
- `README.md`
- `tests/test_followup_fail_safe_hardening.py` (new)
- `tests/test_fail_safe_bootstrap_and_control.py`
- `tests/test_integration_harness.py`
- `tests/test_unit_helpers.py`
- this report

Unrelated generated-image metadata edits, four quarantined PNG deletions, and
all runtime/research artefacts were preserved unchanged.

## 10. Tests

- Focused fail-safe/digest set: `110 passed in 1.12s`.
- New follow-up module after final changes: `30 passed in 0.44s`.
- First full-suite diagnostic: `827 passed, 1 skipped`; eight isolated unit
  fixtures needed to mock the new installation precondition before testing
  deeper mocked operational behavior.
- Final full suite after all changes: `837 passed, 1 skipped, 1 warning in 405.45s`.
- Warning: pre-existing Starlette `TestClient` deprecation warning.
- `python3 -m py_compile mrsMThatcher2.py mrs_log_digest.py
  tests/test_followup_fail_safe_hardening.py`: passed.
- `git diff --check`: passed.

## 11. Production-log isolation

Before the final validation runs the live log was inode `467655`, size
`1479594`; before the last full rerun it was `1484688`, and afterwards it was
`1494160`. The live supervisor continued normal logging. Inspection of both
appended intervals found zero `pytest`, `/tmp/pytest-`, fixture, dummy, or
loopback-test markers. The existing suite-wide file-descriptor isolation guard
also passed.

## 12. Read-only smoke tests

- Foreign-CWD import reported `_PRODUCTION_BOOTSTRAPPED == False`.
- The first combined smoke shell changed CWD for subsequent commands and those
  commands failed harmlessly before touching the project; the corrected runs
  used explicit paths/workdir.
- Explicit canonical log input discovered current plus rotations `.1` through
  `.5` and excluded unrelated files.
- Digest `--no-state` rendered the clarified schedule label and did not advance
  resume state.
- SHA-256 values for local config, both histories, generated analysis, and
  identity audit were identical before and after validation. `bot_state.json`
  remained identical through the first validation and later changed while the
  live bot continued normal operation during the final full-suite rerun; no test
  process wrote the live state, and the appended log interval contained no test
  markers.

## 13. Limitations and deferred work

- A valid state backup is treated as recoverable evidence when the primary state
  is absent; semantic validation still occurs in `load_state()`.
- Reconciliation of an ambiguous remote POST remains a manual operational task
  because the current API integration has no reliable idempotency key or remote
  lookup that proves the outcome.
- The digest lock uses POSIX `flock`, appropriate for this Linux deployment.

## 14. Git diff/stat and status

Task files account for the source, documentation, and test changes listed above.
The working tree also retains pre-existing generated metadata modifications,
four quarantined PNG deletions, and numerous untracked artefacts. Nothing was
staged.

Final status is recorded by `git status --short` after this report. No commit or
push was made.

## 15. Safety confirmations

- The production bot was not stopped, restarted, or signalled.
- No X, xAI, or other external API was called.
- No test post or reply was created.
- No production state, config, history, receipt, metadata, or log was edited.
- The production log was not truncated, rotated, repaired, or deleted.
- No production initialisation command was run.
- The canonical counterfactual simulator was not run.
- Nothing was staged, committed, pushed, or deployed.
