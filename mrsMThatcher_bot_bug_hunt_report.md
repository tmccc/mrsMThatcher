# MrsMThatcher Bot Bug Hunt

Date: 18 July 2026
Repository: `/disks/disk1/etc/mrsMThatcher`
Reviewed revision: `6979c6d` (`Remove verified unused code`)

## Executive verdict

The review found five actionable defects. The most serious is an attribution classifier that treats any speaker description containing the word `Thatcher` as confirmation that Margaret Thatcher was the speaker. It has left three source-grounded non-Thatcher quotations in the live 619-record source and in the 613-item eligible set. A second corpus-boundary defect leaves all 13 recently excluded quotations available to the conversational reply retriever; nine have high research confidence and can pass the current grounded-reply validator.

The recent factual-answer and `principle_reply` work also has three narrower validation gaps. Existing focused tests all pass because they test the intended examples and aggregate counts, not these counterexamples.

No implementation was changed during this review.

## Findings

### CRITICAL - Speaker attribution uses substring matching and leaves three non-Thatcher quotations active

`speaker_attribution_status()` classifies a packet as `confirmed_thatcher` whenever its speaker string contains `thatcher`, unless that same string contains `misattributed`:

- `quote_image_metadata_remediation.py:463-470`

This confuses a named role or relationship with the actual speaker. The following structured v3 contracts are marked `confirmed_thatcher` even though their own canonical-speaker fields say otherwise:

| Quote ID | Active source line | Canonical speaker in v3 contract | Verification status |
|---|---:|---|---|
| `7f75c4d086fb67b0e54d9d63dbe470dc6f9f929aee00ce4a02d01bbc9c8d4646` | 14 | Abi Morgan, spoken by Meryl Streep as Margaret Thatcher | `misattributed` |
| `cf7a03be1c6e34efbcfec0cc8010544e2deab777a05cb0193d237814244f5c8e` | 41 | Abi Morgan, spoken by Meryl Streep as Margaret Thatcher | `misattributed` |
| `8c70978a89ef43e405dbc7eb0bb9751d9dbe631d63d9834ccf3dfde51a4a971c` | 279 | Alexander Dubcek, quoted by Margaret Thatcher | `misattributed` |

The affected texts are:

1. `Do you know that one of the great problems of our age is that we are governed by people who care more about feelings than they do about thoughts and ideas.`
2. `It used to be about trying to do something. Now it's about trying to be someone.`
3. `The negative aspects of equality are that lazy people, passive individuals, and irresponsible employees profit at the expense of dedicated and diligent employees, and unskilled workers profit at the expense of skilled ones.`

All three IDs are present in:

- `mrsMThatcher.txt`;
- `quote_analysis.json`;
- the deployment candidate's `active_quote_manifest.json`;
- the 613-quote semantic-veto candidate.

The production selector does not independently check canonical speaker. `completed_research_quote_hashes()` treats every completed packet as eligible, and `quote_candidates_for_current_cycle()` only excludes source records absent from that completed-packet set (`mrsMThatcher2.py:6214-6247`). The bot can therefore select and publicly post these records as Thatcher quotations.

The cleanup tests assert the expected counts of 9 removed, 4 ungrounded, and 613 active, but do not assert that `canonical_speaker`, `verification_status`, and `thatcher_attribution_status` agree (`tests/test_quote_attribution_cleanup.py:12-17`, `99-115`, `142-156`). The incorrect classifier generated the expected counts, so all count-based gates passed.

Recommended correction: classify the speaker from structured, source-grounded identity semantics, not substring presence. Add explicit regressions for `spoken by ... as Margaret Thatcher`, `quoted by Margaret Thatcher`, `not Margaret Thatcher`, and Margaret Thatcher's own earlier name. Rebuild the attribution partition and all active derived artefacts after correction.

### HIGH - Conversational retrieval still exposes all 13 attribution-excluded packets

The regular-post source was reduced, but `retrieve_research_packets()` still requires and searches exactly 626 completed packets (`reply_strategy.py:302-327`). It does not read the active manifest or attribution tombstones. `ask_grok_for_reply()` passes those results directly to the model and constructs `allowed_quote_ids` from the same unfiltered result set (`mrsMThatcher2.py:7907-7917`, `8148-8158`).

Read-only probes using each tombstone's exact text showed that every one of the 13 excluded IDs is retrievable, normally as the highest-ranked packet. Of those:

- nine are high-confidence packets and can satisfy the configured `medium` evidence floor;
- four ungrounded-speaker packets are low confidence and are rejected if selected, but are still sent to the model as approved completed-corpus evidence.

The validator checks `research_confidence`, not whether the packet is attributed to Thatcher. In one direct reproduction, excluded ID `69a1c2be...` (canonical speaker not Thatcher) passed as a `researched_principle` with `verification_status=exact`, `research_confidence=high`, and `canonical_speaker=null`.

This means an excluded quotation can no longer appear as a regular main post, but can still supply the purported Thatcher principle or historical evidence behind a public conversational reply.

The reply tests reinforce the stale 626/6 corpus invariant and contain no tombstone exclusion test. The hybrid reply shadow index also retains the 626-packet assumption; it is shadow-only, but its comparison telemetry is consequently based on the wrong active evidence corpus.

Recommended correction: define one authoritative reply-evidence eligibility set derived from the active attribution manifest and completed research state. Fail startup if excluded IDs enter the model prompt or validator allow-list. Keep historical packets immutable but mark them ineligible for future public use.

### HIGH - The general factual-question guard accepts declarative non-answers

`concrete_factual_question_word()` recognises only leading `who`, `what`, `where`, or `when` questions containing a literal question mark. `direct_factual_answer_error()` then rejects:

- an empty or interrogative first sentence;
- seven hard-coded abstract openings;
- a Berlin Wall answer that omits the East/West direction.

It does not verify that an ordinary declarative sentence answers the requested fact (`reply_strategy.py:553-585`). Reproductions:

- `Who was Prime Minister in 1979?` plus `That deserves serious consideration.` returns no error.
- The complete `validate_reply_decision()` path accepts that reply as `historical_context` when metadata claims it is factual and grounded, even with unrelated evidence.
- `Who was Prime Minister in 1979` without `?` is not recognised as a factual question at all.

The existing tests cover the Berlin Wall example and its known ideological non-answer, but no general `who`, `what`, `where`, or `when` counterexample (`tests/test_reply_strategy.py:522-605`). This leaves the original failure class fixed for one topic rather than for the stated general rule.

Recommended correction: require an evidence-backed answer relation for all detected factual questions, and treat clear question syntax without terminal punctuation as a question. Do not rely on a blacklist of rhetorical openings.

### MEDIUM - Lower-case and non-title-case actor names bypass `principle_reply` allegation protection

Named actors are extracted from incoming text only when written as title-case Latin words. The reply-side assertion pattern has the same title-case assumption (`reply_strategy.py:49-63`, `524-550`).

The full validator accepted this pair:

- incoming: `andy burnham only wants public popularity.`
- reply: `Burnham craves popularity rather than responsibility.`

The reply is topically aligned, so the topical guard does not compensate for the missed actor assertion. All-uppercase names, initials, apostrophised names, and non-Latin names are also not covered by the present extraction design, although those variants were not exhaustively executed in this review.

The existing regression uses `Andy Burnham` in the incoming text and therefore passes (`tests/test_reply_strategy.py:713-729`).

Recommended correction: derive actor references with case-insensitive token spans and available handle/entity metadata, then compare normalised forms. Continue to reject only actor-specific assertions, not general principles.

### MEDIUM - Persisted reply validation cannot reapply incoming-text safety checks

Initial model output is validated with `incoming_text`, but pending drafts persist only reply text and strategy metadata (`mrsMThatcher2.py:7779-7809`). `strategy_metadata_is_semantically_valid()` later calls `principle_reply_assertion_error()` without the incoming contribution (`mrsMThatcher2.py:8327-8394`). Confirmed-reply receipt validation uses the same weaker helper (`mrsMThatcher2.py:8397-8431`).

Consequently, even title-case `Burnham craves popularity rather than responsibility.` is accepted by the durable metadata validator because it cannot establish that Burnham is the actor from the incoming post.

Normal new output still passes the stronger initial validator, so this is primarily a stale-draft, recovery-boundary, or future-rule-upgrade risk rather than the normal path by which the previous finding is reached. It nevertheless means persisted drafts do not retain the safety boundary claimed by `test_principle_reply_receipt_metadata_keeps_the_same_safety_boundary()` (`tests/test_reply_strategy.py:773-784`).

Recommended correction: persist a bounded hash-bound safety context or a durable validation result/version sufficient to revalidate the same target. Invalidate drafts created under older safety versions.

## Areas reviewed without a confirmed defect

The following paths were inspected and had focused tests run where available. No comparable bug was established in this pass:

- regular quote/image and meme confirmed-post receipts;
- ambiguous remote X-post barriers;
- conversational reply receipts and idempotent reconciliation;
- clarification one-per-thread terminal handling and pre-model skip ordering;
- image and quote used-cycle recovery;
- generated-image spacing state;
- semantic-veto shadow lookup isolation and RNG preservation;
- startup state normalisation, scheduler epochs, and backup recovery.

This is not proof that those paths are defect-free. The main transactional code is large and has many broad exception boundaries, but the observed handling is generally fail-closed and extensively tested.

## Static analysis

Command:

```text
ruff check --select F,B,PLE,PLW,SIM mrsMThatcher2.py reply_strategy.py semantic_quote_image_veto.py semantic_alignment/quote_image_semantic_veto.py historical_context_formatter.py
```

Result: 31 findings. No undefined-name error was found. Most findings were broad exception handling, exception-chaining, global declarations, and simplification/style notices. They were used as audit leads and were not treated as bugs without behavioural evidence.

## Focused validation

```text
python3 -m pytest -q \
  tests/test_reply_strategy.py \
  tests/test_quote_attribution_cleanup.py \
  tests/test_quote_image_semantic_veto_shadow.py
```

Result: `138 passed in 12.75s`.

```text
python3 -m pytest -q tests/test_unit_helpers.py \
  -k 'confirmed_reply_receipt or pending_strategy_reply or clarification or main_post_receipt or meme_post_receipt or ambiguous_remote_post'
```

Result: `16 passed, 370 deselected in 0.54s`.

```text
python3 -m py_compile \
  mrsMThatcher2.py reply_strategy.py historical_context_formatter.py \
  semantic_quote_image_veto.py mrs_log_digest.py \
  semantic_alignment/quote_image_semantic_veto.py
```

Result: passed.

```text
git diff --check
```

Result: passed.

The full offline suite was not rerun. Focused tests were sufficient to establish that current tests pass while missing the reproduced defects, and avoided another unnecessary long run.

## Production isolation

Before and after the review:

```text
ActiveState=active
SubState=running
MainPID=1574852
child PID=1574853
ExecMainStartTimestamp=Sat 2026-07-18 21:40:11 BST
NRestarts=0
```

No service was stopped, restarted, reloaded, or signalled. No X or model request was made. No production state, receipt, history, configuration, or analytics file was written.

## Working tree

Pre-existing untracked file:

```text
?? unused_code_controlled_deployment_report.md
```

This review adds only:

```text
?? mrsMThatcher_bot_bug_hunt_report.md
```

No code was fixed, staged, committed, pushed, or deployed.
