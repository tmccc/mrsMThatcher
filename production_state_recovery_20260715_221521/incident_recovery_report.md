# Production state and digest recovery - 15 July 2026

## Outcome

The production state and digest resume state were recovered from self-test fixture contamination.
The bot restarted under its existing wrapper with one child process and successfully published the
overdue quotation and historical-context reply. No posting receipt remains pending.

## Root causes

1. `mrsMThatcher.selftest.log` matched the digest's production log glob and could supply the latest
   state, configuration and lifecycle records.
2. Tests imported a cached production module and could bypass import-time path isolation, allowing
   fixture state to be persisted to `bot_state.json`.
3. The contaminated state reset durable reply histories and replaced real scheduling/main-post
   fields with fixture values such as post ID `950001` and January 2027 epochs.
4. During truncated mention pagination, a valid model `no_reply` outcome was not durable. The same
   target could therefore be evaluated by xAI again even though duplicate posting remained blocked.
5. The structured reply prompt simultaneously required JSON and instructed the model to emit bare
   `SKIP`. Historical-mode guidance also did not define `factual_claim_made` precisely enough.

## Code corrections

- Self-test logs are excluded from automatic discovery and cannot supply digest state/config even
  when passed explicitly.
- Test and self-test processes are blocked from writing production bot state.
- Valid `no_reply` evaluations are persisted in a bounded per-target ledger and skipped before xAI.
  Malformed or locally rejected model output remains retryable.
- The structured prompt now represents `no_reply` in the required JSON schema and never asks for
  bare `SKIP` in strategy mode.
- Historical modes explicitly require `factual_claim_made=true` when asserting a historical or
  policy fact.
- Digest `--reset-state` no longer carries fields from the old resume cache.
- Latest-state output now includes the last meme epoch and human-readable timestamp.

## Recovery method

- Original contaminated state, five backups and digest resume state were copied into this directory
  before intervention.
- Durable collections were based on the last complete archived production state and extended only
  from structured production events and confirmed reply receipts.
- Complete scalar state was taken from the pre-contamination 18:17 production snapshot.
- The candidate was accepted unchanged by `mrsMThatcher2.normalise_state_candidate` and rejected all
  known fixture values before application.
- Child PID `2319836` received one `SIGTERM`. The wrapper was not signalled.
- State was replaced atomically while no bot child was running, with file and directory `fsync`.
- The wrapper restarted child PID `2972836` after its normal delay.

Hash and count details are in `recovery_audit.json` and `recovery_apply_record.json`. The immediate
pre-apply state is retained as `bot_state.pre_recovery_apply.json`.

## Recovered state

- Posted meme filenames: 29
- Recent main-post IDs: 20
- Confirmed automatic reply IDs: 150
- Mention reply targets: 108
- Quote-tweet reply targets: 50
- Strategy history records: 3
- Terminal no-reply evaluations: 7
- Daily conversational replies: 3
- Daily quote-tweet replies: 1

The first post after recovery was main post `2077504708474704163`; its historical-context reply also
completed and the regular-post receipt reconciled.

## Digest verification

The digest resume cache was regenerated from production logs only. The resulting latest state uses
`bot_state.json` as its authoritative source and reports:

- latest main post: `2077504708474704163`
- last quote epoch: `1784150695`
- last meme epoch: `1784119355`
- next quote epoch: `1784157977`
- next meme epoch: `1784214000`
- posted meme count: 29

No self-test input or fixture value remains in the regenerated digest JSON or resume cache.

## Verification

- Broad regression suite: 739 passed, 1 skipped
- Focused digest suite: 208 passed
- Python compilation: passed
- `git diff --check`: passed
- Production child count: exactly one
- Pending posting/reply receipts: zero
- Recent production `ERROR`/`CRITICAL` entries after restart: zero

No changes were staged, committed or pushed during this recovery.
