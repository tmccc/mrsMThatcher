# Reply Spacing 30-Minute Change Report

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

Pending commit, push, local configuration update, child-only restart, startup verification, and bounded live observation.
