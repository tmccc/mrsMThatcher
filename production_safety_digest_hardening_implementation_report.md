# Production Safety and Digest Hardening Implementation Report

## 1. Concise summary

Implemented all nine review priorities with targeted changes:

- existing invalid production local config now aborts startup;
- runtime controls retain the last valid document or fail closed;
- importing `mrsMThatcher2` no longer loads live local config or attaches the production file logger;
- digest resume state advances only after complete, flushed report delivery;
- digest project files resolve through `--project-dir`;
- used-history and generated metadata validation now match production more closely;
- bounded rate coverage reports gaps and suppresses unreliable observed runway;
- integer overrides require actual JSON integers;
- simulator tests establish feature flags explicitly.

Production posting, receipts, reconciliation, generated scoring, and selection policy were not redesigned.

## 2. Files changed

- `mrsMThatcher2.py`
- `mrs_log_digest.py`
- `README.md`
- `tests/test_unit_helpers.py`
- `tests/test_integration_harness.py`
- `tests/test_simulate_regular_post_futures.py`
- `tests/test_generated_image_pool_health_digest.py`
- `tests/test_generated_image_utilisation_digest.py` (pre-existing uncommitted utilisation work, minimally updated for valid empty metadata fixtures)
- `tests/test_fail_safe_bootstrap_and_control.py` (new)
- `tests/test_digest_safety_hardening.py` (new)
- this report

Unrelated generated metadata changes, four quarantined-image deletions, runtime artifacts, simulator outputs, and other untracked files were preserved.

## 3. Implementation details by finding

### Priority 1: production config fails closed

`LocalConfigError` now represents an unsafe existing local config. Missing config remains optional. Existing files fail startup on read/JSON/root/coercion/range/cross-field errors. No supported override is partially applied. Error messages identify the path and concise reasons without dumping the complete config.

### Priority 2: runtime control fails safe

Control documents are validated as complete objects before becoming current. Boolean controls and `*_until` timestamps are strict. The cache records the last valid document and a failure signature. Transient failures reuse the last valid document; without one, `disable_all` is synthesised. Identical failures log once, invalid results are never cached as valid, and repaired content is accepted on a later poll. A genuinely absent file clears controls under the existing optional-file semantics.

No repository control-file writer exists, so no writer was added.

### Priority 3: digest checkpoint is transactional

All parsing, current filesystem analysis, historical rates, runway/utilisation calculation, Markdown/JSON rendering, and report delivery complete before `save_resume_time()` runs. Stdout uses `write` plus `flush`. New optional `--output` writes through a temporary file, flushes/fsyncs it, and atomically replaces the destination. Any calculation, serialisation, render, stdout, interruption, or output-file error leaves resume state unchanged.

Same-timestamp fingerprints and overlap deduplication were left intact.

### Priority 4: deterministic import and explicit bootstrap

Import now establishes source defaults only. `production_bootstrap()` explicitly configures production file logging, loads/validates local config, and validates credentials once. Executable entry points call bootstrap before dispatch. Repeated calls are idempotent.

Import retains console-only safety diagnostics needed by test-mode endpoint guards, but no longer opens/writes `mrsMThatcher.log`. Self-test remains credential-checking internally and does not require bootstrap credential enforcement.

The observational simulator fixture no longer reads `mrsMThatcher.local.json`; all relevant generated-pool, shadow, policy, spacing, and penalty flags are explicit. This fixes the prior environment-dependent shadow test.

### Priority 5: used-history validation

Digest `images_used.json` accepts only a JSON list, matching production. Objects, strings, null, malformed JSON, and missing files yield warnings and no fabricated current-cycle use. Duplicate list items retain production set semantics.

### Priority 6: explicit project directory

Added `--project-dir`, defaulting to the directory containing `mrs_log_digest.py`. Auto-discovered logs, generated pool, metadata, quarantine history, used history, local config, and relative digest resume state resolve through it. The selected directory is printed in Markdown and included in JSON.

### Priority 7: coverage honesty

Each 7/30-day historical window now reports:

- calendar span days;
- observed logging days;
- coverage quality (`continuous`, `gapped`, or `unavailable`);
- largest detected gap;
- material gap count and threshold.

A 15-minute threshold is used because the live process normally emits frequent loop records. Observed logging time is the sum of inter-record intervals capped at that threshold. Material gaps make observed rate/runway bases unreliable; the digest warns and falls back to the schedule model where possible. Overlapping rotations remain deduplicated.

### Priority 8: metadata validation

Digest validation now distinguishes successful parsing from non-empty payloads and checks required schema versions/kinds, path/item maps, audit item object shape, SHA-256 format/content, basename/origin correspondence, required identity fields, enums, booleans, finite numeric ranges, and active-pool coverage. A valid empty active pool with valid empty metadata reports complete coverage and hash validation.

### Priority 9: strict integer overrides

All integer-valued local overrides require `type(value) is int`. Booleans, integral/non-integral floats, numeric strings, empty strings, and null are rejected. Existing non-negative, positive, range, and cross-field constraints still apply.

## 4. Exact fail-safe semantics

- Local config absent: source defaults are allowed.
- Local config existing but invalid/unreadable: process exits non-zero before lock acquisition, state loading, or `main()`.
- Control absent: no runtime pause.
- Control invalid with prior valid document: previous valid controls remain effective.
- Control invalid without prior valid document: all posting lanes are paused through `disable_all`.
- Repaired control: accepted automatically after successful validation.

## 5. Production bootstrap behaviour

`production_bootstrap()` is called only by executable dispatch. It configures production logging first, applies config atomically, validates the resulting runtime config, validates credentials for non-self-test execution, and marks itself complete. Imports from tests, simulators, audits, or foreign CWDs do not load deployment-local config.

## 6. Digest resume transaction boundary

The durable boundary is now:

```text
parse -> analyse -> derive -> render/serialise -> deliver+flush -> save resume state
```

Failure before the final step leaves the old resume file byte-for-byte unchanged. If checkpoint saving itself fails after output, the report may be repeated on the next run, which is safer than silently losing a window.

## 7. Digest project-directory resolution

Default: repository/script directory.

Explicit example:

```bash
python3 mrs_log_digest.py --project-dir /disks/disk1/etc/mrsMThatcher --no-state /absolute/path/mrsMThatcher.log
```

Relative `--state-file` paths resolve beneath the selected project directory. `--output` remains an explicit user-selected destination.

## 8. Coverage-gap methodology

Records are deduplicated before coverage analysis. For each trailing window, clean record timestamps plus the observed start/end boundaries are ordered. Gaps over 900 seconds are material. Calendar-based post rates remain visible but are not accepted as observed runway input when material gaps exist. This detects missing rotations, stopped processes, and sparse files without claiming statistical confidence.

## 9. Compatibility considerations

- Valid production local config behaviour and current effective values are unchanged.
- Posting, receipt, reconciliation, cooldown, spacing, and selection algorithms are unchanged.
- Invalid config/control tests were updated to assert the new fail-safe contract.
- Self-test validates the bootstrapped runtime config dynamically.
- Import-time test endpoint guards still emit console diagnostics.
- Digest output gains project/coverage fields and may report schedule-only runway where old output gave a misleading observed estimate.
- “Ever used” remains explicitly bounded by available structured logs.

## 10. Tests run and results

Focused hardening/compatibility runs included:

- bootstrap/config and control safety;
- digest failure injection and project resolution;
- generated pool health/runway/utilisation;
- simulator selector parity/shadow capture;
- existing config/control/digest/receipt/recovery integration slices;
- self-test and endpoint safety.

Latest focused integration slice:

```text
88 passed, 177 deselected in 34.78s
```

Self-test compatibility rerun:

```text
4 passed, 165 deselected in 1.45s
```

Compilation and `git diff --check`: PASS.

## 11. Full-suite result

Final run after all implementation and import-logging changes:

```text
786 passed, 1 skipped, 1 warning in 402.56s
```

The warning is the existing Starlette/httpx deprecation warning. The former live-config-dependent simulator failure is resolved.

## 12. Read-only smoke-test results

PASS:

- imported `mrsMThatcher2` from `/tmp`; `_PRODUCTION_BOOTSTRAPPED` remained false;
- ran digest from repository with `--no-state` and atomic `--output`;
- ran digest from foreign CWD with absolute log path and explicit `--project-dir`;
- both reports resolved `/disks/disk1/etc/mrsMThatcher`, reported complete metadata coverage, and reported continuous current log coverage;
- local config, bot state, image/quote histories, generated analysis/audit metadata, and receipts had identical before/after hashes or manifests.

The production log grew from 261833 to 262399 bytes during smoke tests solely from a normal live-bot main-loop tick at 04:44:42. No smoke-test/import marker appeared in that appended interval.

## 13. Limitations and deferred work

- Coverage continuity is heuristic; a 15-minute threshold is operationally useful but is not proof that every rotated byte is present.
- Report delivery and resume checkpoint are ordered safely but are not one filesystem transaction; a crash after report delivery and before checkpoint can repeat a window.
- Tests earlier in this task, before import-time file logging was fixed, appended synthetic records to the production log. Those records are identifiable by `/tmp/pytest-*` paths and credential/debug markers. The log was not deleted, truncated, rotated, or edited to hide them. Existing digest contamination filtering excludes known pytest bursts from generated-rate calculations. Future imports/tests no longer attach the production file handler.

## 14. Git diff --stat

Task-related tracked diff (including the preserved uncommitted utilisation additions already present in `mrs_log_digest.py`):

```text
README.md                                        |  25 +++
mrsMThatcher2.py                                 | 170 +++++++++------
mrs_log_digest.py                                | 267 ++++++++++++++++++++---
tests/test_generated_image_pool_health_digest.py |   7 +-
tests/test_integration_harness.py                |  33 ++-
tests/test_simulate_regular_post_futures.py      |  18 +-
tests/test_unit_helpers.py                       |  11 +-
7 files changed, 416 insertions(+), 115 deletions(-)
```

New untracked focused tests contain 138 and 191 lines. The repository-wide stat additionally includes pre-existing quarantine metadata/image changes and therefore is not a task-only measure.

## 15. Git status --short

All task changes remain unstaged and uncommitted. Pre-existing generated metadata changes, quarantined PNG deletions, utilisation work, reports, simulation directories, backups, and runtime artifacts remain present and unstaged.

## Explicit confirmations

- The production bot was **not** restarted, stopped, or signalled.
- No X, xAI, or other external API was called.
- No test post or reply was created.
- No production state, local config, metadata, image, or receipt was edited by the implementation or smoke tests.
- The production log was not deleted, truncated, rotated, or manually edited. Synthetic test records were appended before the import-file-logging defect was fixed, as disclosed above; subsequent smoke imports were isolated.
- The canonical simulator was not run.
- Nothing was staged, committed, or pushed.
