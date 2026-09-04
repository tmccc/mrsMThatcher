# Single-call reply production implementation

## Result

The production conversational architecture is now:

```text
deterministic candidate eligibility and scheduling
  -> bounded verified conversation context and local trusted facts
  -> one OpenAI GPT-5.6 Sol Responses API call
  -> strict local mechanical validation
  -> existing durable reply receipt and X posting path
```

The implementation branch is based on semantic-veto removal commit
`f3418e63ab40b2ccb03f6fd67b72fc74b5e0eb80` and incorporates the completed
provider-trial source commit `b5f11ceda7b90f159f05b8e7e8cb65c2bcef3e89` (cherry-picked locally as
`c3ebf80148970995ab48350c3650b37231f5d7ff`). The observed remote master at
implementation time was `314cb1912ec428024bbbad33acb524b1c54f6ab6`.

There is one production configuration object:

```json
{
  "single_call_reply": {
    "enabled": false,
    "strategy_version": "single-sol-reply-20260904",
    "model": "gpt-5.6-sol",
    "timeout_seconds": 180
  }
}
```

The tracked default remains disabled. Deployment must atomically replace the
two retired local strategy objects with this exact object and set only
`enabled` to `true`.

## Frozen request contract

- Endpoint: `POST /v1/responses`
- Model: `gpt-5.6-sol`
- Reasoning: `{"effort":"high"}`
- Temperature: `1`
- Maximum output tokens: `8192`
- Storage: `false`
- Output: strict `json_schema` named `single_call_reply_decision`
- Tools: none
- Prompt cache key: `mrsMThatcher-single-sol-7bfa91fb2d9b1175`
- Prompt cache: explicit implicit-mode request with a 30-minute TTL
- Frozen prompt SHA-256:
  `7bfa91fb2d9b1175560abb33e43f2ced6910d8e63cadd1f8f04935b6dc2f2560`
- Frozen local schema SHA-256:
  `3b1e23015cebe3b75eacde04ebfd4344fa25117f047cdcf83241b0ce709872ce`

Only OpenAI's unsupported provider-side `uniqueItems` keyword is omitted from
the request schema; fact-ID uniqueness remains mandatory locally. Candidate
images are validated, held locally, converted to data URLs and supplied in the
same request. No base64 data is logged.

## Bounded context and validation

The model sees no more than 12 visible turns, 12,000 visible-text characters,
eight prior same-author interaction pairs, 30 recent confirmed conversational
replies, 32 compact facts drawn from no more than eight local evidence packets,
and two images. The verified visible path preserves the subject/root; the target
occurs once, at the end. Recent same-author pairs come from the existing bounded
`ai_reply_history`, which is populated only after remote confirmation and is
updated idempotently during confirmed-receipt recovery.

The response is parsed once with duplicate-key rejection and exact fields and
enums. Mechanical validation enforces decision consistency, grounding IDs for
direct factual replies, Unicode validity, two sentences, 270 weighted
characters, no line breaks, real links/domains/email/network addresses,
mentions, hashtags, emoji or genuine duplicate recent prose. It deliberately
does not impose a language or script restriction. CJK sentence punctuation is
not treated as a domain separator.

## Context and quarantine follow-up

The final context audit positively filters recent prose to confirmed
conversational lanes in `ai_reply_history`, excludes current-path and later
replies, takes the latest 30, and emits them oldest first. The same existing
history supplies same-author pairs; author, target, root/thread, reply identity
and confirmation time are checked, current-thread/later/duplicate rows are
excluded, and the latest eight are emitted chronologically. If the target time
cannot be established, both optional history collections stay empty rather than
risk admitting a later interaction. Confirmed-receipt reconciliation replaces
duplicates by either target or reply identity and retains the latest 1,000
age-bounded rows in confirmation order. No new state field was necessary.

Verified parent traversal is independently bounded at 64 ancestors before the
canonical path is reduced to 12 turns and 12,000 text characters, retaining the
root, target and nearest parents. Fact retrieval still sees only the bounded
current context and directly quoted subject, including quoted-subject text only
in the local retrieval query when it is not already visible; compact passages
deduplicate by normalised passage, source and locator without padding. Target
images are selected before directly quoted-post images, unrelated parent images
are not eligible, and at most two validated images enter the one Sol request.
Declared attachment metadata that cannot be resolved to a validated image is an
operational image-input failure rather than a silent text-only downgrade.

The existing mention-author quarantine remains ahead of context, evidence,
media and provider work. Only a valid editorial `no_reply` adds its normal
strike; provider, schema, local-validation and image-input failures do not.
Receipt recovery remains idempotent and no longer performs a redundant strike
mutation; the established validated-reply clearing point and exact expiry
boundary remain unchanged. Context telemetry adds visible character count and
names the history metric `recent_conversational_reply_count`; it logs counts
and hashes, never history prose, fact passages or image content.

## Decisions, failures and persistence

A valid `no_reply` is a terminal editorial outcome and retains existing
no-reply strike/quarantine accounting. Provider, timeout, envelope, schema,
context, image and local-validation failures remain operational: they do not
terminalise the candidate, add an author strike, invoke repair, or fall back to
another model. A single immediate transport retry is bounded to a definite
pre-transmission connection failure or transient HTTP 429/5xx response.

Only a valid reply creates the new compact draft. It binds the target/root/
parent/lane identities, exact contribution and visible-context hashes, complete
payload hash, frozen prompt/schema, model settings, reply and kind, compact fact
IDs and complete used-source hashes, image identities/hashes, one model call,
creation time and deterministic `validated_draft_hash`. A valid pending draft
survives restart and is reused with zero provider calls. Old drafts are not
reinterpreted. X target revalidation, receipts, journals, ambiguous-write
barriers, confirmation recovery, quotas, spacing and posting remain in the
existing path.

## Observability and compatibility

The concise `single_call_reply_decision`, provider-usage, draft-recovery and
posting-outcome events replace production stage telemetry. Digest JSON schema
version is 3. It reports candidate, decision, posting, operational-failure,
one-call, context, validation, usage, cache, latency and published-cost totals.
Old logs remain parseable and receive only a compact legacy multi-stage count;
the former large stage report is not emitted.

Prospective conversation extraction recognises the single-call decision
without requiring a legacy stage-summary event. The retired production module
was deleted; the older strategy module and claim diagnostic remain only in
explicitly offline research and trial code. The production import-closure test
proves neither old orchestration module is reachable from the bot.

## Validation evidence

No provider or X call was made. All network-bearing integration checks used the
local fake server under `MRS_TEST_MODE=1`.

The private completed trial output was checked once, without copying private
text or responses into the repository. All 60 primary OpenAI holdout outputs
were mechanically accepted by the production parser and validator: **60/60**.
The prompt and response-schema hashes matched the frozen values above.

Focused validation covered the module contract, production configuration,
mention/hot-post/quote-tweet lanes, text and multimodal requests, target
eligibility/revalidation, editorial versus operational outcomes, durable drafts,
confirmed receipts and recovery, same-author history, author caps/quarantine,
mention backlog, digest v3, prospective extraction, remote-write guards and
production import isolation. The final focused set passed 1,100 tests; the full
integration harness passed 311 tests. Compilation of all 28 changed Python
files, documentation coverage for 179 modules and `git diff --check` passed.
The exact four-worker README suite then passed 5,407 tests in 485.78 seconds
with 873 deprecation warnings and no worker restart.

The requested retired-symbol audit found no match in the production import or
call closure. Remaining matches are limited to historical reports, explicitly
offline research/trial code, old-log compatibility parsers/fixtures, and offline
tests; they are not selectable runtime strategies.

## Controlled deployment checklist

1. Confirm the implementation branch contains both the semantic-veto removal
   commit and the single-call implementation.
2. Pause the bot and confirm it reports paused.
3. Stop the bot.
4. Back up, preserving ownership and modes,
   `/disks/disk1/etc/mrsMThatcher/mrsMThatcher.local.json`,
   `/disks/disk1/etc/mrsMThatcher/bot_state.json`,
   `/disks/disk1/etc/mrsMThatcher/confirmed_reply_receipt.json`, and
   `/disks/disk1/etc/mrsMThatcher/mrsMThatcher.log`. A missing
   `confirmed_reply_receipt.json` is the expected clear state, not an error.
5. Confirm there is no active ambiguous X-write marker, unresolved confirmed
   reply receipt, or non-empty old pending AI draft.
6. Atomically replace the old local configuration: remove both retired strategy
   objects, add the exact `single_call_reply` configuration above with
   `enabled=true`, and retain every unrelated setting unchanged.
7. Perform the previously documented semantic-veto host cleanup because this
   branch is based on the reviewed semantic-veto removal.
8. Fast-forward the production checkout to the reviewed merged commit.
9. Start the bot while it remains globally paused.
10. Run the production self-test and inspect startup logs for one conversational
    strategy, `gpt-5.6-sol`, reasoning `high`, no xAI conversational pipeline,
    no semantic-veto runtime, no pending receipt/draft error, and ready
    remote-write safety.
11. Unpause.
12. Observe the first few real decisions. Verify one Sol call per candidate and
    that valid replies use the existing confirmed-reply receipt path.

This checklist is direct activation. It contains no shadow step. This task does
not deploy, pause, stop, reload or signal the production bot.
