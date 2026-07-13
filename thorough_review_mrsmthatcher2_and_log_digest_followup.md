# MrsMThatcher Production and Digest Follow-up Review

Date: 2026-07-11
Scope: `mrsMThatcher2.py`, `mrs_log_digest.py`, and directly relevant tests
Review type: direct static/control-flow review plus isolated behavioral probes

## Executive Summary

The recent bootstrap, receipt, logging-isolation, metadata, and digest checkpoint
hardening is substantial and generally coherent. The deployed test suite is broad
and currently passes. However, this review found two production fail-safe defects
that should be addressed before treating startup/recovery controls as complete:

1. completely missing state and used-history files are silently treated as a new
   installation, which can reset schedules, caps, and quote/image cycles on an
   established production directory;
2. boolean and fractional numeric runtime-control timestamps are accepted and
   truncated, so a malformed intended pause can become inactive instead of
   making the control document fail closed.

The most important digest issue is that the documented explicit-log invocation
analyses only the named current log and omits sibling rotations. This materially
understates observed generated utilisation and rates while still presenting
them as the bounded available history.

No implementation change was made during this review.

## Findings

### High: Missing production state/history silently resets durable behavior

References:

- `mrsMThatcher2.py:1166-1195`
- `mrsMThatcher2.py:1693-1731`
- `mrsMThatcher2.py:8540-8575`
- `tests/test_unit_helpers.py:223-226`
- `tests/test_integration_harness.py:2950-2956`

When a used-history JSON file is absent and no legacy pickle exists,
`load_used_set()` returns an empty set. When `bot_state.json` and every configured
backup are absent, `load_state()` returns `default_state()`. Startup subsequently
persists that default and treats the quote schedule as immediately due.

On a genuinely new disposable installation this is convenient. On the fixed
production directory, deletion, mount failure, wrong path resolution, or an
incomplete restore can therefore reset:

- quote and image cycle histories;
- last-post and next-post scheduling;
- reply counters, watermarks, and cooldowns;
- recent/replied identifiers.

The consequence can be duplicate quote/image reuse, reprocessing old mentions,
or an immediate post. Corrupt existing files correctly fail closed; the all-files
missing case does not. Tests explicitly encode the unsafe ambiguity as expected
behavior.

Recommendation: distinguish explicit first-run initialization from established
production. A production bootstrap should require state plus both JSON histories,
or require a deliberate initialization flag/command that creates them. Add a
test where an established production marker/config exists but all state/history
files are missing; startup must stop before scheduler/API/posting activity.

### High: Malformed control timestamps can fail open

References:

- `mrsMThatcher2.py:822-846`
- `mrsMThatcher2.py:849-863`
- `tests/test_fail_safe_bootstrap_and_control.py:183-188`

`parse_control_time()` checks `isinstance(value, (int, float))`. Because `bool`
is an `int` subclass, `True` becomes epoch `1` and `False` becomes `0`.
Non-integral floats are truncated as well.

An operational document such as:

```json
{"disable_all_until": true}
```

passes validation and is interpreted as an expired pause. This contradicts the
intended rule that invalid pause timestamps invalidate the document and preserve
the last valid pause or enter `disable_all` fail-closed mode.

An isolated probe confirmed:

```text
parse_control_time(True)         -> 1
parse_control_time(1900000000.9) -> 1900000000
```

Current tests cover a malformed string but not booleans, fractional floats,
NaN/infinity, negative values, or unreasonable future epochs.

Recommendation: reject booleans explicitly; accept only actual integer epochs
or documented datetime strings; reject non-integral/non-finite floats and bound
epochs consistently with state/receipt validation. Parameterize tests over every
pause key and verify all posting lanes remain disabled.

### Medium: Explicit digest log paths omit rotated history

References:

- `mrs_log_digest.py:274-328`
- `mrs_log_digest.py:3801-3804`
- `tests/test_generated_image_pool_runway_digest.py:20-31`
- `tests/test_generated_image_utilisation_digest.py:15-20`

When positional log paths are supplied, `main()` uses exactly those paths.
Auto-discovery of `mrsMThatcher*.log*` happens only when no path is supplied.
The documented production command supplies `mrsMThatcher.log`, so runway and
utilisation ignore `mrsMThatcher.log.1` through `.5`.

The lower-level tests prove calculations are correct when rotations are passed,
but there is no CLI integration test proving the normal production invocation
includes them. An isolated probe produced:

```text
explicit current log:  1 regular post, 0 generated
current + rotation:    2 regular posts, 1 generated
```

This explains why the live digest can report zero observed generated posts while
current-cycle history shows several generated basenames.

Recommendation: either auto-expand an explicitly named canonical current log to
its sibling rotations, add a separate `--history-glob`, or change the operational
invocation to omit positional logs. Report every file included. Add CLI tests for
rotation, overlap, relative paths, and explicit opt-out.

### Medium: Supported string config overrides accept arbitrary JSON types

References:

- `mrsMThatcher2.py:555-594`
- `mrsMThatcher2.py:710-774`
- `tests/test_fail_safe_bootstrap_and_control.py:151-165`

String-valued settings use `str(value)`. Consequently `null`, arrays, and objects
are converted into strings rather than rejected. For example, an isolated probe
confirmed `XAI_MODEL: null` becomes the literal model name `'None'`.

Path fields can similarly become strings such as `"{'bad': 1}"`. Some failures
surface later during metadata startup, but model/text/glob values can pass
bootstrap and fail only in live operation. That weakens the promise that an
existing invalid supported override aborts startup.

Recommendation: require actual JSON strings for string settings. Add optional
per-key constraints for non-empty model/path/glob values. Parameterize null,
array, object, numeric, empty, and whitespace-only values.

### Medium: Digest runway config validation is weaker than production

References:

- `mrs_log_digest.py:384-438`
- `mrs_log_digest.py:441-463`
- `tests/test_generated_image_pool_runway_digest.py:75-99`

The standalone digest correctly avoids importing production code, but it parses
schedule values using `float()` and `int()` and handles arbitrary pool-enable
values through Python truthiness. It therefore accepts numeric strings,
fractional spacing values by truncation, and non-boolean objects that production
bootstrap rejects.

This can make the digest publish a schedule runway from a configuration that the
bot would refuse at its next restart. Current tests cover malformed JSON and one
bad sleep string, not production-parity coercion.

Recommendation: add a small side-effect-free shared schema module, or mirror the
strict JSON type/range rules with parity tests against production validators.
Also pin digest defaults to production defaults in a cross-module test so the
duplicated constants cannot drift silently.

### Medium: Digest resume/output files have no inter-process serialization

References:

- `mrs_log_digest.py:484-558`
- `mrs_log_digest.py:3752-3772`
- `mrs_log_digest.py:3932-3943`
- `tests/test_digest_safety_hardening.py:25-83`

The transaction boundary is now correctly ordered for one process: render,
deliver, then advance resume state. But concurrent digest invocations share the
same fixed `.tmp` paths and have no lock or compare-and-swap checkpoint.

Two runs can race such that:

- one replaces or removes the other's report/state temp file;
- a later-finishing invocation writes an older resume timestamp over a newer one;
- both deliver duplicate windows before either checkpoint advances.

Existing failure-injection tests are single-process only.

Recommendation: add a dedicated digest lock or unique temp files plus a locked
checkpoint compare-and-swap that refuses timestamp regression. Test two
processes with deliberately interleaved output and state writes.

### Medium: Ambiguous remote success still permits duplicate posting

References:

- `mrsMThatcher2.py:6124-6141`
- `mrsMThatcher2.py:6143-6166`
- receipt/recovery tests in `tests/test_unit_helpers.py` and
  `tests/test_integration_harness.py`

Receipts provide strong recovery after a valid post ID is returned. If the POST
request succeeds remotely but the connection times out or the successful body
cannot be parsed, there is no post ID and no receipt. The call is treated as a
failure and a later retry can duplicate the post.

This is partly an API limitation, not a straightforward local bug, but it is the
largest remaining duplicate-post window. Tests heavily cover failures after a
confirmed ID; they cannot prove behavior for remote-success/local-timeout.

Recommendation: document this residual explicitly. Investigate X-supported
idempotency keys or a pre-post intent record plus read-back reconciliation. Do
not implement blind read-back matching without a carefully bounded design.

### Low: Control cache signature omits inode/device identity

Reference: `mrsMThatcher2.py:877-904`.

The control cache key uses resolved path, mtime nanoseconds, and size. An atomic
replacement preserving mtime and size can leave the old validated document in
memory. This is uncommon but avoidable for a safety mechanism.

Recommendation: include `st_dev`, `st_ino`, and preferably a content hash after
read. Add a same-size/same-mtime atomic-replacement test.

### Low: Schedule runway is an optimistic lower-bound, not an expected date

References: `mrs_log_digest.py:403-409` and rendered pool-health output.

The schedule model assumes generated images are selected at the maximum allowed
frequency (`1 / (spacing + 1)`). It does not include quote matching, candidate
scores, policy exclusions, SKIP-like selection failures, or downtime. Calling
the result `days_to_cycle_exhaustion` can be read as an expectation; mathematically
it is an optimistic minimum under maximum generated throughput.

Recommendation: label it `optimistic_schedule_minimum_days` or state the maximum-
throughput assumption on the same output line.

### Low: Log-isolation guard has untested rotation/disappearance branches

References:

- `tests/conftest.py:31-51`
- `tests/test_logging_isolation.py`

The new isolation guard is valuable, but `appended_bytes()` is not directly
tested for rotation. If the original inode disappears, the current file can be
read twice. This does not hide markers, but can consume unnecessary memory and
produce confusing diagnostics. The guard is also hard-coded to one production
path, reducing portability.

Recommendation: unit-test unchanged inode, one rotation, multiple rotations,
missing original inode, and absent production path. Avoid duplicate current-file
reads and derive the production root from one explicit test configuration value.

## Additional Test Gaps

1. No production-mode test distinguishes explicit first-run initialization from
   accidental total loss of state/history.
2. Runtime-control tests do not cover every accepted JSON scalar type or all
   lane aliases.
3. String local-config fields lack strict type and non-empty validation tests.
4. Digest CLI tests do not use the documented explicit current-log command with
   real sibling rotations.
5. Digest config parity is not checked against production source defaults and
   coercion rules.
6. No multi-process digest checkpoint/output race test exists.
7. Receipt semantic validators accept some coercible numeric forms; fuzz/property
   tests over JSON types would expose unintended truncation or coercion.
8. State normalization accepts non-integral floats through `int(value)`; tests
   cover numeric strings but not fractional JSON numbers.
9. Generated audit/analysis caches are path-keyed and tests do not broadly cover
   same-path replacement during one process. Production currently relies on a
   controlled restart after curation, which should remain explicit.
10. Full-suite isolation checks detect known markers, but a short-lived direct
    write that contains none of those markers could escape after closing its file
    descriptor. Architectural injection is the primary defense; retain it.

## Strengths Confirmed

- Explicit production bootstrap and operational entry guards are correctly
  ordered before locks/state/API behavior.
- Existing invalid local config fails closed and bootstrap completion is marked
  only after validation.
- Managed logging handlers are closed/replaced and logger propagation is disabled.
- Existing malformed state/history and unresolved receipts fail closed.
- Confirmed-post and confirmed-reply receipts are durable and reconciliation is
  extensively fault-injected.
- Generated identity audit coverage/hashes are validated at startup.
- Digest output precedes checkpoint advancement and failure injection covers
  render, serialization, stdout, and output-file failures.
- Pool health distinguishes active/quarantined assets and current-cycle/bounded
  historical semantics.
- Production-log test isolation now has both handler injection and session-level
  marker/file-descriptor checks.

## Validation Performed

Isolated probes confirmed:

- boolean/fractional control timestamps are accepted and truncated;
- `XAI_MODEL: null` coerces to `'None'`;
- absent used history returns an empty set;
- explicit current-log digest analysis omits a generated post in a rotation.

Commands run:

```text
python3 -m py_compile mrsMThatcher2.py mrs_log_digest.py
PASS

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_fail_safe_bootstrap_and_control.py \
  tests/test_logging_isolation.py \
  tests/test_digest_safety_hardening.py \
  tests/test_generated_image_pool_health_digest.py \
  tests/test_generated_image_pool_runway_digest.py \
  tests/test_generated_image_utilisation_digest.py
93 passed in 1.46s
```

The most recent full suite from the immediately preceding deployment validation
was `807 passed, 1 skipped, 1 warning`. It was not rerun for this read-only review.

## Prioritized Remediation

1. Introduce explicit production initialization and fail closed on completely
   missing state/history in established production.
2. Make control timestamps strict and add scalar/fail-closed tests.
3. Include rotations in the documented digest invocation and add CLI coverage.
4. Make all supported config coercion type-strict, including strings; align
   digest runway validation.
5. Serialize digest checkpoint/output writers and prevent checkpoint regression.
6. Clarify optimistic schedule runway language.
7. Harden low-level receipt/state numeric coercion and isolation-guard edge tests.

## Repository State And Safety

No source, test, production state, config, history, receipt, metadata, or log file
was modified by this review. Only this Markdown report was created.

Pre-existing working-tree state remains:

- modified generated analysis and identity-audit JSON;
- four deleted/quarantined generated PNGs;
- untracked runtime locks, reports, backups, research tools, review data, and
  simulation outputs.

The production bot was not stopped, restarted, or signalled. No live X, xAI, or
external API call was made. Nothing was staged, committed, or pushed.
