# Production Log Test Isolation Implementation Report

## Summary

Fixed the test logging path that contaminated `mrsMThatcher.log`, hardened
handler ownership and lifecycle, added suite-wide production-log isolation
checks, and corrected standalone digest schedule-runway resolution. The full
suite now completes without any pytest process opening the production log or any
test marker appearing in the concurrently appended live-log interval.

## Root Cause

`mrsMThatcher2` correctly avoided file logging at import, but
`test_bootstrap_is_explicit_valid_and_idempotent()` called
`production_bootstrap()` without specifying a logging destination. Because that
test module imported the bot with its production-default `BASE_DIR`, bootstrap
attached a `RotatingFileHandler` for the live `mrsMThatcher.log`.

Pytest restored `_PRODUCTION_BOOTSTRAPPED` after the test but did not restore the
process-global logger. The production file handler therefore remained attached
while later unit tests emitted synthetic posting, receipt, loopback, and
`/tmp/pytest-*` records. This explains both the initial bootstrap/config lines
and the thousands of subsequent fixture events in the contaminated interval.

Two lifecycle weaknesses amplified the risk:

- `logger.handlers.clear()` detached handlers without closing them.
- module reload added another import console handler without removing handlers
  installed by the previous module instance.

The subprocess integration helpers were not the contamination source: their
normal command paths already set temporary `MRS_BASE_DIR` and `MRS_LOG_FILE`
values. The malformed-config subprocess also used a temporary base directory,
so its default log was temporary.

## Logging Design

`setup_logging()` now accepts:

```python
setup_logging(
    *,
    log_path: Path | None = None,
    configure_file_logging: bool = True,
)
```

`production_bootstrap()` exposes the same two keyword-only controls. Normal
executable dispatch still calls `production_bootstrap()` with no arguments and
therefore retains the existing rotating production file log, console format,
rotation size, backup count, and startup-failure diagnostics.

Tests explicitly choose no file logging or a temporary path. A final defensive
check rejects a pytest process that asks to attach a file handler anywhere under
the production project path. This check supplements, rather than replaces, the
explicit injectable design.

## Handler Lifecycle

Handlers installed by this module carry private ownership and role markers.
Reconfiguration removes and closes only module-owned handlers, preserving
external capture handlers. File and console handlers are distinguishable. The
named logger has propagation disabled.

Consequences:

- repeated successful bootstrap remains idempotent;
- repeated setup closes the previous temporary file handler;
- two tests can use different temporary logs without cross-contamination;
- reload removes stale module-owned handlers before installing one import
  console handler;
- records do not propagate to root handlers;
- failed production bootstrap intentionally retains its configured handler so
  startup configuration errors remain recorded;
- failed isolated bootstrap writes only to its explicitly supplied temporary
  destination.

The explicit production-bootstrap guard and fail-closed configuration semantics
were not weakened.

## Suite-Wide Isolation Guard

`tests/conftest.py` adds two autouse checks:

1. Before and after every test, `/proc/self/fd` is inspected and the test fails
   if the pytest process holds the live production log open.
2. At session start, the live log inode and byte offset are recorded. At session
   teardown, only newly appended bytes are inspected, with rotation-aware inode
   handling. The test session fails on pytest temporary paths, loopback test
   endpoints, or dummy credential markers.

The guard allows the real bot to continue appending legitimate scheduler and
production records during tests.

## Regression Tests

`tests/test_logging_isolation.py` covers:

- plain import without a production file descriptor;
- bootstrap with file logging disabled;
- bootstrap to a temporary file;
- explicit pytest/live-log refusal;
- idempotent repeated bootstrap and exactly-once records;
- failed bootstrap isolated to a temporary log;
- two independent temporary destinations;
- disabled logger propagation;
- deliberate pytest marker confinement;
- reload handler cleanup;
- subprocess bootstrap logging to a temporary path.

Existing operational-entry, receipt/recovery, posting helper, and subprocess
tests are covered by the autouse file-descriptor and marker guards.

## Digest Schedule Runway

The schedule model was unavailable because `mrsMThatcher.local.json` contains
only explicit overrides, while `POST_SLEEP_MIN`, `POST_SLEEP_MAX`, and generated
spacing currently use source defaults. The digest merged log-derived values and
local overrides but had no standalone source-default baseline. Rotation caused
the latest startup config lines to be unavailable in the explicit single-log
read, exposing the gap.

The digest now defines only the four standalone runway defaults it needs, then
applies observed startup config and local overrides in production precedence
order. It does not import or bootstrap production code and does not expose
unrelated config values. An existing malformed local config makes schedule and
observed runway unavailable with an explicit reason rather than silently using
defaults.

Real read-only validation now reports:

```text
primary_basis                           = schedule_model
schedule_model_generated_share_max     = 33.3%
schedule_model_generated_posts_per_day = 3.56
schedule_model_days_to_cycle_exhaustion = 20.2
```

The observed rate remains unavailable because the clean structured-history
window is insufficient; that bounded-history limitation is unchanged.

## Files Changed

- `mrsMThatcher2.py`
- `mrs_log_digest.py`
- `README.md`
- `tests/conftest.py`
- `tests/test_logging_isolation.py`
- `tests/test_fail_safe_bootstrap_and_control.py`
- `tests/test_generated_image_pool_runway_digest.py`
- `production_log_test_isolation_implementation_report.md`

Unrelated generated metadata changes, four quarantined image deletions,
runtime artifacts, backups, and simulation output were preserved unchanged.

## Validation Results

Compilation and formatting:

```text
python3 -m py_compile mrsMThatcher2.py mrs_log_digest.py
PASS

git diff --check
PASS
```

Representative formerly contaminating tests plus logging/runway regressions:

```text
50 passed in 1.19s
```

Focused bootstrap, controls, guards, receipts, posting, digest, pool, runway,
utilisation, integration, and selector tests:

```text
258 passed, 358 deselected in 42.77s
```

Full suite:

```text
807 passed, 1 skipped, 1 warning in 400.52s
```

The warning is the existing Starlette `TestClient` deprecation warning.

## Production Log Proof

Full-suite baseline:

```text
size  = 1370892
inode = 467655
```

After the suite:

```text
size           = 1374288
inode          = 467655
appended bytes = 3396
```

The appended interval consisted only of genuine live-bot main-loop scheduler
ticks. Marker counts were:

```text
/tmp/pytest-          = 0
/pytest-of-           = 0
127.0.0.1:            = 0
X_CONSUMER_KEY=dummy  = 0
```

Two independent `/proc` inspections while the full suite was running found zero
pytest file descriptors resolving to `mrsMThatcher.log`. The per-test autouse
guard also completed without a file-descriptor failure.

The previously contaminated bytes remain in the production log. They were not
hidden, deleted, truncated, rotated, edited, or repaired.

## Limitations

- `/proc/self/fd` inspection is active on Linux; the helper degrades to marker
  inspection on platforms without `/proc`.
- The schedule defaults are intentionally duplicated in the standalone digest.
  Tests pin them to current production policy, so future schedule-default changes
  must update both files.
- Existing log contamination remains historical evidence and is handled by the
  digest's contamination filtering.

## Diff And Status

Tracked implementation diff before this report:

```text
README.md                                        |  1 +
mrsMThatcher2.py                                 | 71 ++++++++++++++++++------
mrs_log_digest.py                                | 42 ++++++++++++--
tests/test_fail_safe_bootstrap_and_control.py    |  8 +--
tests/test_generated_image_pool_runway_digest.py | 22 ++++++++
5 files changed, 117 insertions(+), 27 deletions(-)
```

New untracked task files are `tests/conftest.py`,
`tests/test_logging_isolation.py`, and this report. The working tree also retains
all pre-existing curation and unrelated untracked files.

## Safety Confirmation

- The production bot was not stopped, restarted, or signalled.
- No live X, xAI, or other external API was called.
- No test post or reply was created on X.
- No production state, configuration, history, receipt, or metadata file was
  edited.
- The production log was not truncated, edited, rotated, repaired, or deleted;
  normal live-bot appends continued.
- Nothing was staged, committed, or pushed during the implementation phase;
  publication was deferred until the owner's subsequent explicit request.
