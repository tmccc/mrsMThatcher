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
A final pre-commit fetch verified `origin/master` and the implementation branch
at `8d09bdb1147e841293bc781d14c0e8362e4f38bc`, the semantic-veto branch at
`f3418e63ab40b2ccb03f6fd67b72fc74b5e0eb80`, and the completed provider-trial
branch at `b5f11ceda7b90f159f05b8e7e8cb65c2bcef3e89`.

## Change inventory

The implementation adds `single_call_reply.py`, the retained offline provider
trial runner/report and tests, offline claim diagnostics, this report, and
narrowly scoped state/receipt fixtures and regression tests. It deletes
`tested_reply_pipeline.py` and its production-pipeline test, and renames the
fake xAI failure scenario to its OpenAI equivalent. Current production,
configuration and operational changes are concentrated in `mrsMThatcher2.py`,
`reply_evidence.py`, `mrs_log_digest.py`, the prospective extractor and its
systemd unit/runbook, `README.md`, `docs/python_api.md`, and the two example
configuration files. The remaining modified files are focused offline tests,
test infrastructure and the three offline simulation/extraction tools that
consume current reply events.

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
two retired local strategy objects with this exact object and, within it, set
only `enabled` to `true`.

Production no longer selects or invokes the xAI gate, reply-necessity vote,
group-hostility, allegation or authentication reviews, multi-model writer,
claim audit/cleanup, duplicate repair, direct-answer repair, reviewer approval,
or separate visual-description stages. There is no shadow, comparison,
fallback, reviewer or secondary conversational provider path.

## Frozen request contract

- Endpoint: `POST /v1/responses`
- Model: `gpt-5.6-sol`
- Reasoning: `{"effort":"high"}`
- Temperature: `1`
- Maximum output tokens: `8192`
- Storage: `false`
- Output: strict `json_schema` named `single_call_reply_decision`
- Tools: none
- Prompt cache key: `mrsMThatcher-single-sol-a0a124490144f2ed`
- Prompt cache: options explicitly supplied in implicit mode with a 30-minute TTL
- Frozen prompt SHA-256:
  `a0a124490144f2ed2bfff85362d96d5b9853204554752758d29ababbe0fbdbdd`
- Frozen local schema SHA-256:
  `6ddc2a1d5af7b3c66af2a3e8d9c357c7fc86198553b8be1db2f263751842cbd4`

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
enums. The completed Responses envelope must also contain one identified,
completed assistant message with exactly one output-text item. Mechanical
validation enforces decision consistency, grounding IDs for direct factual
replies, Unicode validity, two sentences, 270 weighted characters, no line
breaks, real links/domains/email/network addresses, mentions, hashtags, emoji
or genuine duplicate recent prose. It deliberately does not impose a language
or script restriction. Natural CJK prose remains valid with either native or
ASCII sentence stops, while recognisable internationalised domains remain
blocked.

## Context and quarantine follow-up

The final context audit positively filters recent prose to confirmed
conversational lanes in `ai_reply_history`, excludes current-path and later
replies, takes the latest 30, and emits them oldest first. The same existing
history supplies same-author pairs; author, target, root/thread, quoted-subject,
reply identity and confirmation time are checked, current-subject, later and
duplicate rows are excluded, and the latest eight are emitted chronologically.
If the target time cannot be established, both optional history collections
stay empty rather than risk admitting a later interaction. Confirmed-receipt reconciliation replaces
duplicates by either target or reply identity and retains the latest 1,000
age-bounded rows in confirmation order. No new state field was necessary.

Verified parent traversal is independently bounded at 64 ancestors before the
canonical path is reduced to 12 turns and 12,000 text characters, retaining the
root when reached, the target and the nearest parents. Cached ancestors may
extend the path to that ceiling, while new X lookups remain capped at the prior
three per candidate; the richer context does not perform a 64-request search.
If an unavailable older ancestor prevents reaching the root, the longest
verified contiguous suffix is used instead of imposing the former three-parent
ceiling. A directly quoted post is represented once as a separately labelled quoted subject; it no
longer replaces the chronological reply path. Image-only quoted subjects retain
the same verified subject identity without inventing text. A permanently
unbuildable context is recorded as an operational terminal outcome and retired,
allowing later backlog candidates to proceed without creating a no-reply
strike.

Fact retrieval sees only that bounded visible conversation and the separately
labelled current quoted subject. Production `trusted_facts` admit only
high/medium-confidence passages whose specific field and selected source are
supported by the local source audit; unaudited packet-level variants,
unverified, low-confidence and interpretive research fields are excluded.
Compact passages deduplicate by normalised passage, source and locator without
padding. Their private durable bindings include the selected audited source
identity, fingerprint and audit policy as well as display metadata. Target
images are selected before directly quoted-post images, unrelated parent images are not
eligible, and at most two validated images enter the one Sol request. A
text-only cached direct quote is refreshed with X media expansions. Declared
attachment metadata that cannot be resolved to a validated image is an
operational image-input failure rather than a silent text-only downgrade.

No model reviewer or semantic claim-veto call was reintroduced. The local
boundary checks declared claim spans, supplied fact IDs, exact whole-passage
support and durable evidence hashes. Natural non-factual prose has no closed
vocabulary. The single Sol decision remains responsible for identifying every
factual assertion regardless of reply kind and assessing meaning; deterministic
checks cannot detect every omitted assertion or prove arbitrary semantic
entailment. These checks do not guarantee hallucination-free replies. The
retained claim-risk detector is telemetry only.

The existing mention-author quarantine remains ahead of context, evidence,
media and provider work. Only a mechanically valid editorial `no_reply` with
reason `spam_or_abuse` is a qualifying strike; ordinary completed, irrelevant
or otherwise declined exchanges do not strike. Provider, schema,
local-validation, context and image-input failures never strike. Records from
both the reviewed semantic-veto policy and the actual immediately preceding
`single_sol_editorial_no_reply_v1` writer are accepted and migrated during
state loading, so cut-over cannot invalidate an otherwise sound runtime state.
The latter policy's broad editorial timestamps are not reinterpreted as spam
strikes: only its explicitly retained spam/abuse event is carried into the
current policy while it is still represented in the old live window; an aged
explicit marker is safely discarded rather than rejecting the state. Receipt
recovery remains idempotent and does not mutate strikes; the established
validated-reply clearing point and exact expiry boundary remain unchanged. Context telemetry adds visible character count and
names the history metric `recent_conversational_reply_count`; it logs counts
and hashes, never history prose, fact passages or image content.

The current draft schema is version 4 and binds the target author as well as target,
root, parent and lane; the outer receipt author must equal the context author.
This prevents corrupt recovery from assigning confirmed prose or quota effects
to the wrong contributor. It also binds the declared factual inventory and UTC
context. A changed prompt invalidates obsolete unsent drafts; frozen validation
retains prior schema-4 receipts from `a4639e7` and older supported lifecycles for
local reconciliation only, without regenerating or reposting started work. The digest treats duplicate provider-usage events or
more than the one explicitly authorised transport retry as a one-call
violation, without simultaneously counting that candidate as compliant. A
physical attempt count of two is compliant only for the bounded pre-execution
transport retry; the logical model-call count remains one. Prospective
extraction schema version 5 parses the actual single-call context log, and the
README coherent deployment set includes the mandatory `single_call_reply.py`
module and audited evidence sidecars.

## Decisions, failures and persistence

A valid `no_reply` is a terminal editorial outcome and follows the qualifying
strike policy above. All provider, schema, image, context, draft and
local-output validation failures remain operational: they do not become
editorial `no_reply`, add an author strike, invoke repair, or fall back to
another model. Genuine OpenAI request/provider failures remain retryable under
the existing provider cooldown mechanism. A single immediate transport retry
is permitted only for a definite pre-transmission connection failure or HTTP
429. A 5xx, ambiguous timeout, completed invalid response, incomplete response
or refusal is never retried immediately.

## Operational-failure routing amendment

OpenAI service health is now updated only for explicit provider categories:
transport or ambiguous-timeout failures, provider HTTP status failures
(including authentication, authorisation and rate limiting), genuinely
malformed or unknown incomplete provider envelopes, and violations of the
strict provider-side structured-output envelope. Candidate-specific refusals
and known content-filter/output-limit incompletes are terminal local outcomes.
Unknown local exceptions and the candidate-local `image_input`,
`context_validation`, `local_validation`, `draft_validation` and
`configuration` categories never add `openai_error_epochs` or activate the
global OpenAI cooldown.

Permanent candidate-local image/context failures and completed responses that
fail local prose or durable-draft validation are recorded as terminal
operational skips. They are not published, retried through Sol, counted as
editorial declines, charged to reply quotas or used as quarantine strikes.
Mentions are retired through the existing durable pending-candidate/watermark
mechanism; hot-post and quote-tweet candidates use their existing skipped-ID
records. Processing continues to a later eligible candidate. A genuinely
transient X lookup error remains retryable where the existing X error
classification proves that distinction. No new retry state or digest schema
change was required.

Transient media transport failures (HTTP 408/425/429/5xx and request
exceptions) are also left retryable and do not contaminate OpenAI health;
permanent metadata/content failures retire only that candidate. A zero-call
mention failure releases its reserved per-cycle model-evaluation slot, so a run
of bad images cannot defer the next healthy candidate. OpenAI HTTP status,
retry/reset metadata and physical attempt count survive the pipeline boundary,
so a real 429 still activates the established immediate cooldown and genuine
provider failures remain retryable.

When a retry begins with a real 429, its status, reset/retry metadata and
attempt count remain attached even if the second response is locally invalid,
malformed, incomplete or succeeds. A 429-derived cooldown is honoured inside
the active mention, hot-post and quote-tweet candidate loops, so the same cycle
cannot spend on a later candidate after the breaker activates.

Only a valid reply creates the new compact draft. It binds the target author,
target/root/parent/lane identities, exact contribution and visible-context
hashes, complete payload hash, frozen prompt/schema, model settings, reply and
kind, compact fact IDs and complete used-source hashes, image identities/hashes,
one model call, creation time and deterministic `validated_draft_hash`. A valid
pending draft survives restart and is reused with zero provider calls. Recovery
also re-runs mechanical validation against the current confirmed-reply set, so
prose that became a duplicate while the bot was stopped is retired locally
without another Sol request. Old drafts are not reinterpreted. X target
revalidation, receipts, journals, ambiguous-write barriers, confirmation recovery, quotas, spacing and posting
remain in the existing path.

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

The extractor's current schema is 5. Its user service writes to the separate
version-5 root; the documented first deployment quiesces only that optional
timer, rebuilds from the read-only version-4 root, validates the result, runs a
manual oneshot and inspects the corpus before re-enabling the timer. The main
digest remains directly usable at JSON schema version 3 and tolerates retained
legacy multi-stage log events.

## Validation evidence

No provider or X call was made. All network-bearing integration checks used the
local fake server under `MRS_TEST_MODE=1`.

The final uncommitted parity check used the private completed trial output
without copying private text or responses into the repository. All 60 primary
OpenAI holdout outputs were mechanically accepted by the production parser and
validator: **60/60**. The prompt and response-schema hashes matched the frozen
values above.

Focused validation covered the module contract, production configuration,
mention/hot-post/quote-tweet lanes, text and multimodal requests, target
eligibility/revalidation, editorial versus operational outcomes, durable drafts,
confirmed receipts and recovery, same-author history, author caps/quarantine,
mention backlog, digest v3, prospective extraction, remote-write guards and
production import isolation. The final parser/failure-routing subset passed
**211 tests in 11.88 seconds**, and the broader changed-area set passed **1,775
tests in 155.96 seconds**. The only failures in the first complete run were
eight assertions sharing one stale incident-test fixture: it declared the old
engagement-only reader version rather than the real current writer version.
The fixture-only correction passed its complete **50-test** file without any
production or incident-tool change.

Three isolated read-only reviews found no remaining release blocker. Their
final bounded checks covered provider/local failure routing, state migration,
context and image provenance, quarantine, receipts/journals, remote-write
revalidation, the mechanical validators, production import isolation, digest
v3, extractor v5 and its user unit. The last independent pass ran 249 focused,
327 deployment/digest/extractor/quarantine, 43 selected unit and 17 selected
integration tests, and independently reproduced the **60/60** private parity
result.

All changed Python files compile, documentation coverage passes for 179
modules, and `git diff --check` is clean. The exact final four-worker README
suite passed **5,663 tests in 461.83 seconds**, with 873 deprecation warnings,
no failures and no worker restart.

The requested retired-symbol audit found no match in the production import or
call closure. Remaining matches are limited to historical reports, explicitly
offline research/trial code, old-log compatibility parsers/fixtures, and offline
tests; they are not selectable runtime strategies.

Specifically, dated Markdown reports and frozen research JSON retain historical
names; `reply_strategy.py` and the pilot/evaluation/trial tools remain offline;
their tests remain offline evidence; `mrs_log_digest.py` and the prospective
extractor recognise old events without presenting the old operational report;
and `mrsMThatcher2.py` retains narrowly validated lifecycle-only recovery for a
reply already remotely confirmed under an older receipt. That compatibility
cannot generate, select or post an obsolete draft.

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
   `enabled=true`, replace the retired `MAX_XAI_ERRORS_PER_WINDOW` key with
   `"MAX_OPENAI_ERRORS_PER_WINDOW": 3`, and retain every unrelated setting
   unchanged.
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

For the first deployment containing prospective extractor schema 5, disable
and stop only its timer before the checkout update and verify no oneshot is
active. After installing the reviewed unit, rebuild from the read-only v4 root
into the new v5 root, validate it, run one manual oneshot and inspect the corpus
before enabling that timer again. This support-service migration does not alter
the bot activation sequence above.
