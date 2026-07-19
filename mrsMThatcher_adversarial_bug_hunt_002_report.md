# MrsMThatcher Adversarial Bug Hunt 002

Date: 19 July 2026
Project: `/disks/disk1/etc/mrsMThatcher`
Scope: `mrsMThatcher2.py`, `reply_strategy.py`, `historical_context_formatter.py`, and the live-imported semantic-alignment modules.

## Executive verdict

The review confirmed and fixed defects in reply classification, context construction, persisted-draft validation, quotation-cycle recovery, reply-lane idempotence, formatter dry-run isolation, pagination deduplication, and shadow telemetry.

No further reproducible defect was found after the final adversarial pass over the changed code and adjacent production paths. The changes are not active in the running bot because no restart or deployment occurred.

## Live import surface reviewed

The production bot directly or lazily reaches:

- `reply_strategy`
- `historical_context_formatter`
- `semantic_alignment.hybrid_reply_retrieval`
- `semantic_alignment.quote_image_semantic_veto`
- the canonical packet validator reached by `historical_context_formatter`

The review also covered the surrounding production paths for state normalisation, receipt reconciliation, mention and quote-tweet pagination, quotation and image-cycle selection, scheduler recovery, generated-image spacing, and semantic-veto observation.

## Confirmed defects and fixes

### 1. Hybrid lexical query inherited parent context

The shadow hybrid retriever used parent context as its lexical query whenever a parent existed. This could make the comparison diverge from production lexical retrieval and manufacture apparent relevance from inherited context.

Fix: the lexical leg now always uses the incoming contribution. Parent and older-thread context remain limited to the semantic shadow component, as designed.

### 2. Direct factual question classifier accepted non-answers

Confirmed failures included:

- `Which side/direction/way` accepting an abstract freedom statement;
- `What year` and `Which year` being treated as generic `what`/`which` questions;
- `whose` answers lacking any attribution;
- vague temporal phrases such as `During difficult times` passing a `when` question;
- ordinary economic yes/no facts such as `Did inflation fall?` bypassing answer-first validation.

Fixes add narrow answer-shape and evidence checks for direction, explicit temporal categories, attribution, concrete periods, and observable economic-change predicates.

### 3. Opinion, normative and hypothetical questions treated as settled facts

Examples reproduced locally included:

- `Who should lead Britain?`
- `Where should government invest?`
- `Who might win the election?`
- `Who was the best Prime Minister?`
- `Was Thatcher right about Europe?`

These incorrectly entered the grounded factual-answer path.

Fix: explicit normative, possibility, and evaluative syntax is excluded conservatively. A fresh pass also caught and prevented collisions with proper names and titles, including Theresa May, Right to Buy, and the Good Friday Agreement.

### 4. Long parent chains could remove the incoming contribution

The old context builder appended the incoming post after all parent material and then sliced the combined string. A sufficiently long chain could omit the text the bot was meant to answer.

Fix: the incoming contribution is reserved first. The builder retains the newest contiguous parent suffix that fits, in chronological order. Tiny configured budgets still preserve incoming text.

### 5. Context trimming exceeded its configured maximum

The previous implementation sliced to `max_chars` and then appended `...`, exceeding the limit by three characters.

Fix: ellipsis space is reserved inside the limit; zero and very small limits are handled explicitly.

### 6. Quote-tweet context could hide user commentary

Under a valid but smaller total context budget, the original account post consumed the budget before the user's quote-post commentary.

Fix: the user's commentary is always reserved and remains the contribution being answered. Original-post context is reduced first.

### 7. Persisted grounded drafts could outlive their evidence

Pending historical drafts were structurally checked but did not revalidate retrieved IDs against the current completed corpus. A stale or newly ineligible quote ID could therefore be reused after restart or a transient posting failure.

Fix:

- pending validation schema advanced to version 3;
- incoming text is retained for modes that need revalidation;
- historical evidence is re-retrieved locally before reuse;
- the complete current semantic validator is rerun;
- stale IDs, invalid attribution, corpus errors, and direct answers without current evidence fail closed;
- untrusted topical basis remains outside receipts and durable strategy metadata.

### 8. Ineligible reply targets could perform unnecessary retrieval

After the draft hardening, an X-ineligible mention with a pending historical draft could re-run local retrieval merely to produce an audit event.

Fix: eligibility failure now reads only structurally valid pending metadata for audit, then clears the draft and records the terminal outcome. It performs no context construction, media preparation, retrieval, model call, or transport call.

### 9. Quote-tweet terminal outcomes were not fully durable

Quote-tweet `no_reply` and deterministic reply-not-permitted outcomes relied on bounded seen/skipped lists. After eviction they could be evaluated or posted again.

Fix: quote tweets now use the durable per-target terminal evaluation ledger before model/media work, and persist both `no_reply` and `reply_not_permitted` outcomes.

### 10. Duplicate paginated tweet IDs were retained

Overlapping X pages could return the same immutable tweet more than once. The quote-tweet path could then process a rejected candidate repeatedly in one scan.

Fix: the shared sorted-tweet helper now deduplicates by numeric tweet ID before lane processing.

### 11. Quotation cycle could deadlock at true eligible exhaustion

The active source contains retained research-ineligible records. When every attribution-eligible quotation had been used, those ineligible source records kept the raw `available_lines` set non-empty, so the cycle did not reset and selection failed.

Fix: exhaustion is calculated from unused attribution-eligible researched quotations, not all source records. Seasonal and temporary pair-attempt exclusions retain their existing recovery behaviour.

### 12. Historical formatter dry-run mutated transactional state

`HistoricalContextReplyStore.post(..., dry_run=True)` reconciled a confirmed receipt before returning. A dry run could therefore write history and unlink a receipt.

Fix: validated dry-run requests now return before receipt reconciliation or history access.

### 13. Clarification window had an inclusive boundary error

The one-per-author clarification exemption remained blocked at exactly 24 hours.

Fix: a completed clarification is recent only while its age is strictly less than 24 hours. The completed root thread remains permanently terminal.

### 14. Shadow history rotation could overwrite audit files

Two rotations in one second chose the same archive name in both hybrid retrieval and semantic-veto telemetry. Later rotation could overwrite earlier audit history. Summaries also read only the active file and reset after rotation.

Fix:

- archive names gain deterministic collision suffixes;
- readers combine the bounded newest tail across archives and the active file;
- status summaries survive rotation;
- hybrid event-ID deduplication is rebuilt from archives even when no active file exists.

### 15. Hybrid queue overflow blocked the production caller

Although the shadow worker is documented as non-blocking, a full queue performed synchronous history persistence, `fsync`, and summary reconstruction on the production reply thread.

Fix: overflow is a true fail-open drop. A regression proves that synchronous persistence is not called.

### 16. Prepared v3 manifest became stale after formatter repair

The broad suite correctly rejected the prepared v3 shadow manifest after `historical_context_formatter.py` changed because the manifest pins the attribution predicate SHA-256.

Fix: the repository's deterministic `prepare-v3-shadow` path rebuilt provenance and runtime eligibility. No model or network call was made.

Rebuilt candidate:

- quotations: 610
- images: 91
- pairs: 22,066
- allow: 21,938
- veto: 128
- manifest SHA-256: `fd04f7500b5824811ea987dfbe885ce034dc8af7b1f6286e533cd78628e5c3f4`
- active enforcement: false

## Adversarial checks that found no further defect

- Regular-post and meme receipt barriers remain fail closed.
- Confirmed-reply receipt replay remains idempotent.
- Quote/image posting rolls in-memory histories back on pre-receipt failure.
- Image-cycle and last-image recovery preserve existing selection behaviour.
- Semantic-veto observation preserves candidates, scores, RNG state, and production winner.
- Generated images remain out of scope for the historical-photo veto.
- Formatter source selection and all 610 attribution-eligible packet renderings remained valid.
- Terminal clarification threads are skipped before context, media, retrieval, or model work after restart and after cap reset.
- Pagination loops are bounded and retain their existing watermark safety.

## Tests and validation

Focused regression runs were performed after each reproduced failure.

Final focused production-import suite:

```text
757 passed in 33.28s
```

Selection, recovery, generated-policy, and isolation group:

```text
166 passed in 292.20s
```

Broad offline suite before deterministic manifest refresh:

```text
1 failed, 1943 passed, 1 skipped in 703.64s
```

The sole failure was the expected stale source hash described above. After rebuilding, the failing test passed individually and the complete affected manifest/veto group reported:

```text
55 passed in 7.82s
```

The broad suite was then repeated after the deterministic manifest refresh:

```text
1947 passed, 1 skipped, 3 warnings in 729.16s (0:12:09)
```

The warnings were dependency deprecations from Starlette/httpx and Beautiful Soup/lxml; no project test failed.

Additional validation:

- `python3 -m py_compile ...`: passed
- Ruff fatal/runtime checks `E9,F63,F7,F82`: passed
- `git diff --check`: passed
- external model/API calls: zero

## Production isolation

Baseline and final service identity were identical:

```text
ActiveState=active
SubState=running
MainPID=3210338
child PID=3210345
ExecMainStartTimestamp=Sun 2026-07-19 07:34:41 BST
NRestarts=0
ControlGroup=/user.slice/user-1000.slice/user@1000.service/app.slice/mrsMThatcher.service
```

The live wrapper executes `/usr/local/bin/mrsMThatcher2.py`; workspace source is not dynamically imported by that process. No service command, signal, restart, deployment, X post, production-state write, receipt write, production-log write, external API call, commit, or push was performed.

## Files changed

- `historical_context_formatter.py`
- `mrsMThatcher2.py`
- `reply_strategy.py`
- `semantic_alignment/hybrid_reply_retrieval.py`
- `semantic_alignment/quote_image_semantic_veto.py`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/material_veto_v3_shadow_manifest.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/runtime_eligible_quote_manifest.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/v3_shadow_manifest_audit.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/v3_shadow_manifest_audit.md`
- `tests/test_historical_context_reply.py`
- `tests/test_hybrid_reply_retrieval.py`
- `tests/test_quote_image_semantic_veto_shadow.py`
- `tests/test_reply_strategy.py`
- `tests/test_unit_helpers.py`
- `mrsMThatcher_adversarial_bug_hunt_002_report.md`

## Residual risks

- Concrete-question detection remains deliberately conservative natural-language heuristics rather than a general parser. The expanded adversarial matrix protects the known factual, opinion, modal, proper-name, typo, and answer-shape boundaries.
- A local semantic inference already running in the shadow worker cannot be pre-empted at its time limit; the result is marked timeout after completion and never affects production.
- Any future attribution-predicate edit must rebuild the pinned v3 candidate manifest before deployment. Runtime validation correctly fails closed when this is omitted.

No active deployment is implied by this report.
