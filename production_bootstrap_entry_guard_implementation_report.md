# Production Bootstrap Entry Guard Implementation Report

## Summary

Added an explicit fail-closed boundary for operational commands. Importing
`mrsMThatcher2` leaves `_PRODUCTION_BOOTSTRAPPED` false, and direct calls to an
operational entry point now fail immediately unless `production_bootstrap()` has
completed successfully.

## Implementation

`require_production_bootstrap()` raises `RuntimeError` with:

> Production bootstrap has not completed; call production_bootstrap() before entering an operational command

The guard is the first executable statement in:

- `main()`
- `run_self_test()`
- `run_test_cycle()`
- `run_test_main_tick()`
- `run_test_post_quote()`
- `run_test_post_meme()`

This places it before RNG seeding, test-mode dispatch checks, instance-lock
acquisition, state loading, receipt reconciliation, API helpers, posting paths,
and scheduler-loop entry. `run_self_test()` is included because it reads
production-local state and configuration even though it does not acquire the
instance lock or call APIs.

The existing executable flow remains unchanged:

```text
production_bootstrap() -> selected operational entry point
```

`production_bootstrap()` still sets `_PRODUCTION_BOOTSTRAPPED` only after local
configuration, runtime validation, and credential validation succeed. A failed
bootstrap therefore leaves the guard closed. Its existing early return preserves
idempotence after a successful bootstrap.

## Files Changed

- `mrsMThatcher2.py`: added and applied the operational guard.
- `tests/test_fail_safe_bootstrap_and_control.py`: added fail-closed, ordering,
  successful-dispatch, and failed-bootstrap tests.
- `tests/test_unit_helpers.py`: explicitly models the supported post-bootstrap
  state for existing direct operational-command unit tests.
- `README.md`: documented the explicit-bootstrap invariant.
- `production_bootstrap_entry_guard_implementation_report.md`: this report.

## Tests

- `python3 -m py_compile mrsMThatcher2.py`: PASS.
- Focused bootstrap/operational tests: `37 passed, 324 deselected`.
- Full suite: `794 passed, 1 skipped, 1 warning` in 399.52 seconds.
- `git diff --check`: PASS.

The focused tests verify all six entry points reject pre-bootstrap calls before
lock, state, receipt, API, upload, quote-post, or meme-post helpers are reached.
They also verify successful bootstrap permits dispatch, failed bootstrap leaves
the guard closed, and repeated successful bootstrap remains idempotent. Existing
subprocess integration coverage exercises the normal executable bootstrap and
test-command dispatch path.

## Read-Only Smoke Test

Imported the module from `/tmp` with the repository on `PYTHONPATH` and confirmed:

```text
_PRODUCTION_BOOTSTRAPPED == False
```

Before/after size and mtime checks for `mrsMThatcher.log`, `bot_state.json`,
`images_used.json`, `lines_used.json`, and `mrsMThatcher.local.json` were
identical. No operational entry point was called.

## Diff And Status

Task-specific diff before adding this report:

```text
README.md                                     |  1 +
mrsMThatcher2.py                              | 15 +
tests/test_fail_safe_bootstrap_and_control.py | 78 +
tests/test_unit_helpers.py                    |  2 +
4 files changed, 96 insertions(+)
```

The working tree also contains pre-existing generated metadata changes, four
quarantined generated-image deletions, and unrelated untracked runtime/research
artifacts. They were not modified, discarded, or staged by this task.

## Safety Confirmation

- The production bot was not restarted, stopped, or signalled.
- No X, xAI, or other external API call was made.
- No production state, configuration, log, receipt, history, or metadata file
  was edited by this task.
- No file was staged, committed, or pushed during the implementation phase;
  publication was deferred until the owner's subsequent explicit request.
