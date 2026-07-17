# Direct Factual Reply and Clarification Repair Implementation Report

## Summary

Commit `f0e690ac5a310bd6910ddce0a0b9057ebfc71431` (`Answer factual
reply questions directly`) corrects the conversational failure in which a
concrete historical question could receive a related ideological observation
instead of an answer.

The implementation has two bounded behaviours:

1. A leading `who`, `what`, `where`, or `when` question must receive the
   requested fact directly in the first sentence.
2. If a user immediately points out in the same thread that the bot failed to
   answer such a question, one grounded clarification may bypass that user's
   normal daily cap. The clarification still counts against the global reply
   budget and permanently closes that root thread to further bot replies.

The production regression case is now expected to produce an answer equivalent
to:

> People moved from East Berlin and East Germany towards West Berlin and West Germany.

An abstract response such as `When free to choose, people choose freedom` is
rejected for that question.

## Direct-Answer Enforcement

`reply_strategy.py` detects concrete factual questions conservatively. The text
must contain a question mark and begin, after optional account handles and
ordinary introductory wording, with `who`, `what`, `where`, or `when`.

For a detected question, the model prompt requires:

- the requested fact in the first sentence;
- at most one short contextual sentence afterwards;
- `historical_context` or `historical_correction` mode;
- grounded factual evidence;
- `humour_tone=none`; and
- `no_reply` when the completed research corpus does not provide enough
  evidence.

The request uses temperature zero when this direct-answer guidance is active.
Local validation then rejects:

- a question in place of an answer;
- an abstract principle or rhetorical opening in place of the fact;
- a humorous or ungrounded factual response;
- a non-historical reply mode; and
- for the Berlin Wall regression, any first sentence that does not directly
  distinguish movement from East towards West Berlin or Germany.

This validation also applies to resumed pending drafts. A stale abstract draft
cannot be reused merely because it was generated before the direct-answer rule
was introduced.

## Clarification Eligibility

The cap exception is evaluated before rejecting a candidate under the normal
per-author daily cap. It is available only when all of the following are true:

- reply strategy is enabled;
- the candidate is in the same conversation as the original question;
- its immediate parent is a confirmed auto-reply authored by this bot;
- that bot reply's parent is a concrete factual question from the same user;
- the user explicitly says the answer failed to answer the question, or
  substantively restates the same factual question;
- no clarification has already completed for the thread; and
- the author has not used a clarification exception within the preceding 24
  hours.

An unrelated follow-up does not qualify. A correction in another thread does
not qualify. A second correction in the same thread is skipped before another
model call.

The exception bypasses only `MAX_REPLIES_PER_AUTHOR_PER_DAY`. Existing global
daily limits, safety checks, duplicate protection, grounding requirements,
posting receipts, and reply-spacing rules remain in force.

## Grounding and Reply Style

A clarification request is abandoned locally when no completed-corpus evidence
was retrieved. A successful clarification must be represented by structured
strategy metadata showing:

- mode `historical_context` or `historical_correction`;
- humour tone `none`;
- `factual_claim_made=true`;
- `grounded=true`; and
- a direct first-sentence answer to the original question.

No humour, rhetorical diversion, or ungrounded improvisation is permitted in
the repair path. Existing evidence-confidence validation remains unchanged and
continues to govern historical replies.

## Durability and Idempotence

Clarification provenance is included in the confirmed reply receipt using the
thread ID, preceding bot-reply ID, original-question ID, and trigger type. The
receipt validator rejects malformed or contradictory clarification metadata.

After confirmation, `clarification_reply_records` stores a durable record with:

- `status=repair_reply_completed`;
- `clarification_reply_used=true`;
- `thread_terminal=true`;
- author, target, original-question, and reply IDs; and
- completion time and trigger.

Receipt reconciliation is idempotent. A replay cannot create a second repair,
and a conflicting reply ID for an already completed clarification thread is
rejected. Completed terminal-thread records have no age or count eviction, so a
thread cannot become eligible again after a cap reset or long-running state
growth. The ledger is covered by normal atomic state persistence and recovery.

## Production Integration

The change is confined to conversational replies. It does not modify main quote
posts, historical-context replies beneath quote posts, image selection,
scheduling, ordinary reply caps, or posting receipt semantics.

The clarification path passes the original factual question separately into
the existing reply-generation function. It does not create a second provider
call for an already valid pending draft, and terminal clarification records are
checked before a further model request.

## Regression Coverage

Tests added in `tests/test_reply_strategy.py` and `tests/test_unit_helpers.py`
cover:

- recognition of concrete factual questions;
- a direct Berlin Wall answer;
- rejection of the abstract freedom response;
- direct-answer structured metadata and mode requirements;
- clarification prompt guidance;
- one same-thread clarification despite the author cap;
- global reply-budget accounting;
- unrelated follow-up rejection;
- second-follow-up rejection without another model call;
- one clarification per author per 24 hours;
- confirmed-receipt validation;
- durable restart and replay prevention;
- stale pending-draft rejection; and
- preservation of existing reply modes and cap behaviour.

Validation completed before deployment:

```text
Focused regression tests:       15 passed
Broader reply tests:            450 passed
Relevant integration tests:    621 passed, 1 skipped
Full repository test suite:  1,648 passed, 1 skipped, 3 warnings
py_compile:                     passed
git diff --check:               passed
```

One integration-parity failure discovered during review was confirmed as an
eager creation of an empty clarification ledger. The implementation was changed
to create that state only when a clarification completes, and the parity test
then passed. This fix is included in commit `f0e690a`.

## Deployment Record

The commit was pushed to `origin/master` and deployed through the established
production wrapper procedure on 17 July 2026.

- Production wrapper PID remained `3631076` and was not signalled.
- The old child alone was sent `SIGTERM` after receipt and ambiguity barriers
  were checked absent.
- The wrapper started replacement child PID `62152`.
- Exactly one production child was present after restart.
- The instance lock recorded PID `62152`.
- Startup self-test passed.
- Startup and subsequent main-loop ticks logged normally without an error,
  critical event, or traceback.

No receipt, pending outcome, production state, or posting history was manually
edited during deployment.

## Files Changed

Commit `f0e690a` changed:

- `mrsMThatcher2.py`;
- `reply_strategy.py`;
- `tests/test_reply_strategy.py`; and
- `tests/test_unit_helpers.py`.

This report was added afterwards as
`factual_reply_clarification_implementation_report.md`.

## Limitations

The direct-question detector is intentionally narrow. It does not attempt to
classify every possible factual question, and it does not treat a statement
without a question mark as a concrete question. The generic local validator
guards answer shape and known rhetorical substitutions; correctness still
depends on completed-corpus evidence and the existing historical confidence
rules. The Berlin Wall direction check is deliberately explicit because it is
the production regression that motivated this change.

The clarification exception is not a general conversation extension. It can be
used once per thread and once per author in 24 hours, after which the root
thread is terminal for all subsequent conversational replies.

## Post-Deployment Hardening Audit

The 17 July 2026 post-deployment audit added the exact observed production text
`Were did people ram towards when the Berlin Wall fell?` as a regression. The
question detector now treats the narrowly defined leading phrase `were did` as
the misspelling `where did`; it does not reinterpret ordinary questions that
correctly use `were` as an auxiliary verb. The misspelling `ram` does not affect
question type, while the existing Berlin Wall validator still requires the
answer's first sentence to state movement from East towards West Berlin or
Germany.

The audit also proved end to end that a completed clarification thread is
skipped before context construction, media preparation, retrieval, or xAI after
a restart and a 48-hour time advance. A different thread from the same author
remains eligible under ordinary caps. A further regression exposed that the old
2,000-record retention bound could eventually evict a terminal thread; that
bound was removed so completed clarification threads remain permanently
terminal.

Post-hardening validation completed with 10 focused tests and the full suite:

```text
Focused clarification tests:      10 passed
Full repository test suite:    1,651 passed, 1 skipped, 3 warnings
```
