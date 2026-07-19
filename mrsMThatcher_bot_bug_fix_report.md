# MrsMThatcher Bot Bug Fix Report

Date: 18 July 2026
Repository: `/disks/disk1/etc/mrsMThatcher`
Base revision: `6979c6d` (`Remove verified unused code`)

## Result

All five findings from `mrsMThatcher_bot_bug_hunt_report.md` were reproduced before implementation and fixed in the working tree. The initial regression run produced 15 expected failures and one passing control. The same regressions now pass.

No service was restarted or signalled. The running production process has not loaded these changes.

## Fixes

### Attribution boundary

A shared source-grounded predicate now accepts only packets whose principal canonical speaker is Margaret Thatcher and whose verification status is not `misattributed`. The two corpus records for her earlier career remain eligible because their source-grounded speaker field is `Margaret Thatcher (as Margaret Roberts)`; a bare `Margaret Roberts` label is not treated as sufficient identity evidence.

It rejects descriptions such as:

- `Abi Morgan (spoken by Meryl Streep as Margaret Thatcher)`;
- `Alexander Dubcek (quoted by Margaret Thatcher)`;
- `Unknown (Misattributed to Margaret Thatcher)`.

The corrected structured partition is:

- 610 attribution-eligible completed Thatcher packets;
- 12 completed non-Thatcher or misattributed packets;
- 4 completed packets with unavailable speaker attribution;
- 6 unresolved packets, still excluded separately.

The regular-post selector now exposes 610 eligible source quotations and excludes nine source-retained records: six unresolved records plus the three newly detected false-positive attributions. Historical-context lookup also refuses attribution-ineligible packets.

### Conversational evidence

Lexical reply retrieval now searches only the 610 attribution-eligible completed packets. Final structured-decision validation independently rejects any attribution-ineligible selected packet, even if a caller supplies it directly.

All 13 previous attribution tombstones remain ineligible. The three additional false-positive records are also excluded. Hybrid shadow semantic results are filtered through the same eligibility set, while remaining observational only.

### Factual questions

Concrete `who`, `what`, `where`, and `when` syntax is now recognised without requiring terminal question punctuation. Direct answers reject generic declarative evasions, enforce question-type answer shape, retain the Berlin Wall East-to-West regression, and require substantive support from selected evidence.

### Principle replies

Named-actor assertion detection now compares normalised incoming tokens case-insensitively and supports uppercase and Unicode word forms. The pattern is restricted to sentence-subject position so ordinary abstract principles are not misclassified.

### Persisted drafts

`principle_reply` drafts retain the bounded incoming contribution and remain bound to validation version 2; older drafts are invalidated and regenerated. Confirmed reply receipts carry the incoming context only for `principle_reply`, avoiding unrelated receipt changes. The current assertion guard independently rejects concrete actor assertions in this ungrounded mode rather than trusting the saved context to legitimise them.

The persistence bound is 100,000 characters. This accommodates current long-form X posts while retaining a fail-closed bound for corrupt local state, so a valid long-form principle reply cannot first post successfully and then fail receipt persistence solely because its incoming text exceeded the former 10,000-character limit.

## Support tooling

The offline quotation/image harness now derives the same 610-quotation active set and reconciles 16 total attribution exclusions: 13 historical tombstones plus the three corrected records. Generated images and semantic-veto behaviour were not changed.

Historical paid responses, research packets, the live quotation source, production state, receipts and the active semantic-veto manifest were not rewritten.

## Tests

Focused production and support suites:

```text
227 passed in 10.03s
44 passed in 3.31s
28 passed in 1.09s
25 passed in 61.60s
27 passed in 5.42s
21 passed, 368 deselected in 0.54s
```

These cover reply strategy, historical-context lookup, attribution cleanup boundaries, metadata remediation, hybrid shadow retrieval, the offline selection harness, regular-post futures, semantic-veto shadow isolation, pending drafts, confirmed reply receipts and clarification handling.

Targeted `py_compile` passed. `git diff --check` passed. The unrelated pre-existing `F541` warning in `quote_image_selection_harness.py` was removed during the final continuous audit; Ruff's undefined/import analysis is now clean for every modified implementation module.

## Independent double-check

An additional review on 19 July reproduced three edge defects in the first implementation and corrected them before activation:

1. A synthetic packet labelled only `Margaret Roberts` was accepted without the source-grounded Thatcher identity form used by the real corpus. The gate now requires principal speaker `Margaret Thatcher` exactly.
2. A `who` answer could repeat a person already named in the question rather than identify the requested counterpart. Evidence must now identify a different requested person; evidence that names no counterpart fails closed.
3. A `principle_reply` to a post longer than 10,000 characters could be posted remotely and then fail confirmed-receipt validation. The bounded persisted context now accepts long-form posts.

The hybrid shadow search also now over-fetches by the number of attribution-excluded index rows before filtering, then restores the configured result limit. Excluded high-scoring rows therefore cannot silently reduce the requested semantic candidate count. This remains shadow-only and does not affect production lexical retrieval.

Double-check results:

```text
209 passed in 8.27s   reply strategy, context and metadata remediation
73 passed in 4.01s    hybrid retrieval and selection harness
14 passed, 376 deselected in 0.52s   reply draft and receipt persistence
```

The corpus was independently recounted as 626 completed, 6 unresolved, 610 attribution-eligible and 16 attribution-excluded completed packets. The runtime hash gate returned 610 IDs, and every result from a broad lexical retrieval probe belonged to that eligible set.

## Second adversarial double-check

A further independent pass found and closed two additional classes of validator bypass:

1. An ungrounded `principle_reply` could introduce a different named actor not present in the incoming post, for example an unsupported assertion about Rishi Sunak or Donald Trump. Assertive sentence subjects are now rejected unless they are recognised abstract/general subjects. Explicit controls preserve the intended `Understanding...`, `Institutions...`, `The case still...`, and `Truth...` forms. Boundary tests also prevent names ending in an abstract word and words beginning `case...` from exploiting the allow-list.
2. Some concrete `when`, `where`, and `what happened` questions could accept thematic placeholders through weak prepositions or shared political vocabulary. A `when` answer now needs an actual date/time expression or an evidence-supported relative event; a `where` answer needs an evidence-supported place/direction after a location preposition; event questions reject vague assessments and generic occurrences.

Examples now rejected include:

```text
When was it signed? -> In government, responsibility matters.
When was it signed? -> During political arguments, responsibility matters.
Where was it signed? -> In politics, freedom matters.
What happened? -> Something happened in 1982.
Rishi Sunak wants popularity rather than responsibility.  [principle_reply]
```

Positive controls still accept `on 14 June 1982`, `in London`, a concrete treaty event, and the approved general-principle forms.

Second-pass focused results:

```text
111 passed in 6.01s   complete reply-strategy module
128 passed in 6.04s   context, metadata and attribution cleanup
73 passed in 4.21s    hybrid retrieval and selection harness
19 passed, 371 deselected in 0.58s   reply persistence and clarification paths
```

## Third adversarial double-check

A third pass exercised grammar forms and predicates not used by the earlier fixtures. It found and corrected two further boundary gaps:

1. Addressed questions containing natural punctuation or discourse words, such as `@MrsMThatcher, where...`, `Please, where...`, and `Seriously, when...`, were not entering the concrete factual path. Prefix normalisation now recognises those forms while leaving rhetorical `What a mess?` outside the factual path.
2. The actor-assertion guard's verb list did not cover predicates such as `champions`, `promotes`, or `undermines`. Multiword proper names are now independently prohibited in ungrounded `principle_reply` output, and the assertion predicate set covers the reproduced single-name variants. Boundary controls preserve abstract subjects and prevent the `case...` and abstract-surname allow-list bypasses found during the second pass.

The pass also tightened time expressions so generic political periods require an evidence-supported event, and prevents temporal units or abstract political concepts from being accepted as locations.

Third-pass focused results:

```text
118 passed in 8.23s   complete reply-strategy module
128 passed in 7.59s   context, metadata and attribution cleanup
73 passed in 5.35s    hybrid retrieval and selection harness
19 passed, 371 deselected in 0.73s   reply persistence and clarification paths
```

No full-suite run was performed during any double-check. Each pass used direct counterexamples followed by the bounded suites covering the modified production boundaries.

## Continuous adversarial audit

At the operator's request, the review continued until a complete static pass and repeated affected-module runs found no further reproducible defect. This pass found and fixed five additional boundary problems:

1. `store_pending_strategy_reply()` could reject a draft context without telling its caller. Both the mention and quote-tweet lanes would then continue to `create_post()`, allowing an X write before the confirmed receipt discovered that the reply could not be persisted safely. The helper now returns a validated success result, validates the complete durable metadata contract, and both lanes stop locally before any X write when persistence validation fails.
2. The evidence-overlap guard initially treated `Eastern`/`East` and `Western`/`West` as unrelated, rejecting the required grounded Berlin Wall answer. Directional variants now use the same conservative normalisation as topical matching. The guard still rejects an answer which merely copies the year or subject from the question.
3. Common explicit question preambles such as `I wonder where...`, `Quick question: who...`, `Can I ask when...`, and `Could you explain what...` could bypass factual answer-first handling. They now enter the concrete-question path. Absolute time answers must also contain a time marker supported by selected evidence; an invented year and vague `last year` answer are rejected.
4. Actor-specific assertions after commas or colons, inside introductory clauses, or using all-capital text could evade the ungrounded `principle_reply` guard. Clause boundaries and inline Unicode actor assertions are now checked case-insensitively. Safe abstract subjects remain explicit controls.
5. A posted reply mode could carry a contradictory non-empty `no_reply_reason`. Provider schema validation, local model-output validation, pending-draft validation and receipt validation now all reserve that field exclusively for `no_reply`.

The static warning about a loop-local replacement key in the hybrid review migration was investigated and not changed: the closure is created and consumed synchronously within the same iteration, so later loop values cannot affect its ordering.

Final bounded validation:

```text
340 passed in 12.03s   all changed reply, context, attribution, remediation, harness and hybrid modules
23 passed, 370 deselected in 2.04s   production reply persistence, receipt, clarification and route tests
33 passed, 106 deselected             principle-reply adversarial matrix
17 passed, 122 deselected             concrete-question and factual-answer matrix
```

`ruff check --select F`, targeted `py_compile`, and `git diff --check` all passed.

## Full-suite validation

The subsequently authorised broad run exposed a test-fixture contract that the focused suites had not exercised. Eleven integration posting tests created a synthetic completed research packet without a source-grounded `speaker`, so the new production attribution gate correctly rejected the fixture. After adding `speaker: Margaret Thatcher`, eight simulator tests then exposed that the synthetic-corpus test rule was too strict when pytest shared a test-mode bot import with a complete historical snapshot.

The final rule preserves the production invariant exactly:

- production requires exactly 610 attribution-eligible completed packets;
- test mode still applies the same attribution filter and fails closed when no eligible packet remains, but permits deliberately minimal synthetic corpora.

An explicit regression now proves both test-mode outcomes. The affected integration and simulator paths passed together before the complete rerun:

```text
20 passed, 171 deselected in 292.03s
```

The clean full offline suite then passed from a fresh invocation:

```text
1849 passed, 1 skipped, 3 dependency deprecation warnings in 692.28s
```

Final `ruff check --select F`, targeted `py_compile`, and `git diff --check` also passed.

## Production isolation

Before and after implementation:

```text
ActiveState=active
SubState=running
MainPID=1574852
child PID=1574853
ExecMainStartTimestamp=Sat 2026-07-18 21:40:11 BST
NRestarts=0
```

No X post, external model call, production-state write, receipt write, analytics change, restart, commit, push or deployment occurred.

## Activation caveat

The current process still has the pre-fix Python loaded. A separately authorised controlled deployment/restart is required to activate the runtime gates. The three source records remain physically present in `mrsMThatcher.txt`, but the corrected selector excludes them after activation. A later versioned data cleanup may remove and rebuild their derived active artefacts; that is not required for the runtime safety gate to work.
