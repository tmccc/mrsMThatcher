# Pagination transactionality repair

Generated: 2026-07-27T16:51:23+01:00

## Scope

This repair was developed on the isolated branch
`fix/pagination-transactionality-20260727T151500Z`, based on production commit
`051f70cb3f547cd93008de38da31b9e7788071b9`.

It changes only the bot's bounded X collection pagination and directly related
offline tests. It does not alter quotation data, evidence, schedules, receipts,
live state, semantic policy, images, services, or X.

## Confirmed defects

1. A successful reply selected from a truncated mention batch was recorded
   without its pagination provenance. Applying the confirmed receipt advanced
   `last_seen_mention_id`, invalidated the saved cursor's original
   `base_since_id`, and could skip the unprocessed tail.
2. A saved invalid or expired X pagination token was retried indefinitely on
   later scheduler cycles because none of the mention, hot-post, or quote
   lookup lanes cleared it.
3. The generic paginator did not detect repeated tokens such as `A → A` or
   `A → B → A`.
4. A resumed final hot-post page containing raw but unusable rows could advance
   its safe watermark while retaining the now-obsolete continuation token.
5. A confirmed receipt written by the pre-repair code could reproduce the
   mention-tail loss during an upgrade if it was reconciled while a matching
   active cursor remained in state.

## Repair

- New schema-v3 mention receipts bind an exact
  `{base_since_id, next_token}` continuation when their candidate came from a
  truncated batch.
- Receipt creation fails before the remote request if that provenance is
  missing, malformed, or no longer matches durable state.
- Normal application and restart reconciliation preserve the bound cursor and
  do not advance the mention watermark.
- Legacy schema-v2/v3 receipts remain readable. When canonical state contains a
  strictly valid active continuation whose base equals the current watermark,
  legacy receipt reconciliation conservatively preserves it.
- Cursor state is canonicalised strictly; the legitimate empty first-scan base
  is retained explicitly.
- A cursor-specific X HTTP 400 is recognised from the structured error message,
  not from an unrelated error which merely echoes a pagination parameter.
- The caller durably clears its saved cursor before the single permitted retry
  from the collection head. The original query and `since_id` are preserved.
- A second cursor failure propagates. Unrelated HTTP 400 responses are never
  retried.
- Tokens are tracked across the complete traversal, including recovery, and a
  repeated token is rejected before a duplicate request or persisted
  continuation.
- Hot-post watermark advancement and completed-pagination cursor removal are
  independent operations.

## Regression coverage

The new coverage proves:

- a successful truncated mention reply retains its original cursor, drains the
  tail on the next cycle, and is never posted twice;
- a confirmed reply followed by a state-save failure is reconciled after
  restart without another X write and without losing the continuation;
- exact receipt provenance validation, malformed and cross-lane rejection, and
  legacy v2/v3 compatibility;
- one bounded invalid-cursor recovery in each of the mention, hot-post, and
  quote lookup lanes;
- durable cursor clearing even when the head retry then fails;
- unrelated 400 responses are not retried;
- `A → A`, `A → B → A`, and a rejected cursor reissued after head recovery
  cannot be requested twice;
- a completed resumed hot-post page clears its token while advancing a safe
  `since_id`;
- a completed resumed quote lookup clears its token.

## Validation

- Pagination-focused integration tests: `8 passed`.
- Final unit and fail-safe focused suites: `566 passed`.
- Full coverage-equivalent three-worker suite: `2461 passed`, `0 failed`,
  `0 errors`, in `546.07s`. This ran before the final additive support for a
  top-level structured cursor-error `message`; the directly affected
  566-test set was rerun after that one-line classifier extension.
- Compilation: passed.
- `git diff --check`: passed.

The full suite made no live X or provider calls.

## Isolation

- Production remained at
  `051f70cb3f547cd93008de38da31b9e7788071b9`.
- The live service was not stopped, restarted, reloaded, or signalled.
- No X action, provider call, push, merge, or deployment was performed.

## Recommendation

The isolated commit is suitable for a separately authorised deployment review.
Deployment should retain the existing global pause and receipt preflight
procedures because the change affects restart-time reconciliation.
