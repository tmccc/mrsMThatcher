# Principle Reply Implementation Report

Generated: 18 July 2026

## Status

At report creation, the change was implemented and tested locally but had not
yet been committed, pushed, deployed, or activated by restarting the production
bot. Any later deployment is a separate controlled operational step.

## Objective

Reduce unnecessary `no_reply` decisions when an incoming post contains an
unsupported political allegation but also presents a clear political or moral
point that can be answered without accepting the allegation as true.

The implementation preserves the governing order:

1. factual accuracy;
2. relevance;
3. wit or humour.

## Reply-mode architecture

The structured reply strategy now supports `principle_reply` in addition to the
existing historical, humorous, warm, and `no_reply` modes.

`principle_reply` is deliberately non-factual. Its required metadata is:

```json
{
  "mode": "principle_reply",
  "humour_tone": "none",
  "evidence_confidence": "none",
  "retrieved_quote_ids": [],
  "evidence_summary": "",
  "factual_claim_made": false,
  "grounded": false,
  "reply_text": "Institutions endure only when people are prepared to defend their purpose.",
  "no_reply_reason": ""
}
```

The mode is available independently of the humour configuration. It therefore
does not require humour to be enabled and cannot be used to disguise a humorous
or evidence-backed reply.

## Selection guidance

The model prompt now distinguishes two cases:

- If replying would require endorsing, repeating, or implying an unsupported
  allegation, select `no_reply`.
- If the broader political or moral point can be answered independently, select
  `principle_reply` and write a concise general response that does not depend on
  the allegation being true.

The prompt explicitly prohibits speculation about private motives and directs
serious principle replies to use no humour. One sentence is preferred; the
existing maximum of two sentences and 270 characters remains enforced.

Example:

```text
Incoming: Why doesn't anyone in government understand this?
Reply: Understanding is not always the same as having the courage to act.
```

For longer allegations of institutional failure or bad faith, a response such
as the following is valid because it does not repeat or validate the allegation:

```text
Institutions endure only when people are prepared to defend their purpose.
```

## Safety controls

The provider JSON Schema conditionally requires principle replies to contain:

- no humour tone;
- no evidence confidence;
- no retrieved quotation IDs;
- no evidence summary;
- no factual or grounded flags;
- no `no_reply` reason;
- non-empty reply text.

The local validator independently enforces the same constraints. It also rejects
obvious actor-specific allegations, dates, statistics, handles, and URLs in text
presented as an ungrounded principle reply. For example, this is rejected:

```text
The government is deliberately concealing the truth.
```

The confirmed-receipt validator applies the same checks to persisted drafts and
resume processing. A stored reply therefore cannot bypass the fresh-response
validation boundary.

Concrete `who`, `what`, `where`, and `when` questions remain outside this mode.
They still require a direct grounded answer through `historical_correction` or
`historical_context`. Existing clarification limits, terminal-thread handling,
caps, spacing, budgets, receipts, and duplicate protection are unchanged.

The reply records no selected historical evidence when `principle_reply` is
used. The existing local pre-decision retrieval architecture remains unchanged,
so historical modes can still be selected in the same single model decision.

## No-reply reporting

The prompt now requests stable reasons when applicable:

- `no_reply_due_to_unverifiable_claim`;
- `no_reply_due_to_bait_or_abuse`;
- `no_reply_due_to_incoherent`.

Other existing `no_reply` reasons remain valid. The digest preserves the raw
reason and also aggregates these three categories. Its conversational strategy
section now reports `principle_reply` as a separate mode and includes a compact
`No-reply categories` line.

Older logs remain compatible. Recognisable legacy prose such as "incoherent
gibberish" or "abusive bait" is mapped to the corresponding reporting category
without rewriting historical records.

## Deterministic skips

Existing deterministic spam filtering still occurs before context construction,
media preparation, local research retrieval, or an xAI request. Regression
coverage proves that this path makes no model or posting call and consumes no
reply budget.

## Tests

Coverage added for:

- the government-understanding regression case;
- a longer unsupported institutional allegation;
- valid non-factual `principle_reply` metadata;
- schema-level principle constraints;
- rejection of unsupported actor-specific assertions;
- prevention of factual or research metadata smuggling;
- persisted receipt metadata validation;
- all three stable editorial `no_reply` categories;
- digest mode and category aggregation;
- Markdown digest output;
- deterministic skip before context, media, retrieval, or xAI;
- unchanged historical and humorous reply modes;
- unchanged clarification and cap behaviour.

Results:

```text
Focused reply/digest/unit tests: 488 passed
Full test suite:                1754 passed, 1 skipped
Warnings:                       3 pre-existing dependency deprecations
Full-suite runtime:             703.58 seconds
python3 -m py_compile:          passed
git diff --check:               passed
```

## Files changed

- `reply_strategy.py`
- `mrsMThatcher2.py`
- `mrs_log_digest.py`
- `tests/test_reply_strategy.py`
- `tests/test_digest_reply_observability.py`
- `tests/test_integration_harness.py`
- `tests/test_unit_helpers.py`
- `principle_reply_implementation_report.md`

## Diff summary

Before adding this report, the implementation diff was:

```text
7 files changed, 376 insertions(+), 12 deletions(-)
```

No production configuration, reply budget, author cap, lane schedule, receipt
format, or posting behaviour outside structured reply selection was changed.

## Deployment record

The implementation was committed as `377e67f` (`Add safe principle replies for
uncertain claims`) and pushed normally to `origin/master` on 18 July 2026.

Deployment used the established wrapper-managed procedure:

- the installed `/usr/local/bin/mrsMThatcher2.py` remained a symlink to the
  reviewed repository script;
- the installed script and imported reply/digest modules passed `py_compile`;
- the production self-test completed successfully without an X or xAI call;
- regular-post, meme-post, historical-context, conversational-reply, and
  ambiguous-outcome barriers were absent at the restart gate;
- wrapper PID `3631076` remained running and was not signalled;
- exactly one `SIGTERM` was sent to old Python child PID `387455`;
- the wrapper started replacement child PID `2905742` after its configured
  delay;
- exactly one replacement child was running after restart;
- `mrsMThatcher.lock` recorded `pid=2905742`;
- startup loaded the existing local configuration and semantic-veto shadow
  manifest successfully;
- active semantic-veto enforcement remained false;
- existing quote and meme schedules were preserved;
- the bot logged `Bot started successfully` and completed a subsequent normal
  loop tick;
- no startup error, traceback, lock conflict, receipt barrier, or restart loop
  was observed.

No test post or reply was created. No production state, history, receipt, local
configuration, or posting schedule was manually edited. The replacement process
performed only its normal startup state maintenance.
