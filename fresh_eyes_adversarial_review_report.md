# Fresh-Eyes Adversarial Review

Date: 19 July 2026
Repository: `/disks/disk1/etc/mrsMThatcher`
Reviewed revision: `b267a330983c5db2aec2c5722ce9e994b43bbeda`

## Executive verdict

The live bot is healthy, but this review found seven actionable defects. Two deserve priority:

1. the offline selection harness does not provide the process-level isolation it claims and deserialises mutable checkpoints with `pickle.loads()`;
2. common concrete factual-question forms such as `which` and `how many` bypass the answer-first validator and can still receive a grounded but abstract non-answer.

The remaining findings concern long-horizon reply idempotence, semantic-veto reporting across manifest changes, digest event loss, stale shadow-manifest membership, and unnecessary remote work after an ambiguous post outcome.

No code was fixed. No service was restarted or signalled, no external model or X call was made, and no production state, receipt, history, analytics data, or configuration was written.

## Findings

### HIGH - The offline harness can execute checkpoint code and its network block is not process-level

The harness describes `block_outbound_network()` as applying to the whole process, but it replaces Python `socket` attributes only in the current interpreter (`quote_image_selection_harness.py:243-278`). A child process receives ordinary socket and DNS functions.

The same harness restores an operator-selected run directory's mutable SQLite checkpoint using `pickle.loads()` (`quote_image_selection_harness.py:1592-1628`). The imported production-parity simulator exposes the same unsafe decoder (`tools/simulate_regular_post_futures.py:800-805`). Pickle decoding can invoke arbitrary Python callables before the returned RNG state is passed to `random.setstate()`.

An isolated probe established both parts:

```text
checkpoint_executed_expression_result 42
parent_dns IsolationError
child_dns_returncode 0
child_dns_succeeded True
```

The expression was embedded in the checkpoint's base64 pickle. The DNS test used only `localhost`; it made no external request. It proves that a subprocess is outside the monkeypatch boundary.

The normal CLI wraps simulation dispatch in `OpenAudit` and `block_outbound_network()` (`quote_image_selection_harness.py:2885-2890`), but neither parent-process monkeypatch applies to a process started by a pickle gadget. A corrupt, transferred, or malicious run database can therefore execute commands as `tonym`, write outside the research directory, or make network calls. This contradicts the harness's hard-isolation guarantee.

Existing tests prove ordinary parent-interpreter socket calls fail and that a trusted pickle round-trips. They do not test hostile checkpoint data or child-process isolation (`tests/test_quote_image_selection_harness.py:79-93`, `496-505`).

Recommended correction:

- encode the RNG state as strictly validated JSON arrays and integers, not pickle;
- reject unknown checkpoint keys and malformed RNG-state structure before restoration;
- use an operating-system isolation boundary for offline commands, or accurately narrow the guarantee and prohibit subprocess creation;
- add hostile-checkpoint and child-process network/write regressions.

This does not affect the currently running bot unless the harness is invoked.

### HIGH - `which`, quantity, duration, and yes/no factual questions bypass answer-first validation

`CONCRETE_QUESTION_RE` recognises only leading `who`, `what`, `where`, `when`, and the known `were did` typo (`reply_strategy.py:37-42`). `concrete_factual_question_word()` controls both prompt guidance and the mandatory direct-answer post-validation (`reply_strategy.py:648-680`, `1069-1077`).

Common factual forms therefore return `None`, including:

```text
Which country signed the treaty?
How many seats did they win?
How long did the strike last?
Did Britain join the ERM?
Was she Prime Minister in 1990?
Whose government passed the Act?
```

Using the real 610-packet eligible research corpus, the full decision validator produced:

```text
'Which side did people move towards when the Berlin Wall fell?'
classifier=None  evidence=high  outcome=ACCEPTED

'How many years did the Berlin Wall stand?'
classifier=None  evidence=high  outcome=ACCEPTED

'Where did people move when the Berlin Wall fell?'
classifier=where evidence=high  outcome=REJECTED:
concrete factual questions cannot be answered with an abstract principle
```

The accepted reply in both bypass cases was:

```text
When free to choose, people choose freedom.
```

It was represented as a grounded `historical_context` decision using genuine high-confidence evidence. The evidence metadata is authentic, but the response does not answer either requested fact. This is the same editorial failure class as the Berlin Wall production incident, reached through unhandled grammar rather than the already-fixed `where` path.

Existing tests cover several preambles and only the four intended interrogatives; there are no `which`, quantity, duration, or factual yes/no cases (`tests/test_reply_strategy.py:585-624`).

Recommended correction: classify the additional concrete forms and apply question-type answer-shape checks backed by selected evidence. `why` questions should be handled separately because they usually require an explanation rather than a short entity, place, time, or quantity.

### MEDIUM - Bounded reply ledgers eventually forget completed and terminal targets

Confirmed mention and hot-post reply targets are retained in `replied_to_ids` with a hard limit of 1,000 (`mrsMThatcher2.py:8602-8613`). Quote-tweet reply targets are capped at 2,000 (`mrsMThatcher2.py:9599-9611`). Terminal `no_reply` and `reply_not_permitted` records are trimmed to the newest 2,000 (`mrsMThatcher2.py:8331-8368`), while hot-post skip IDs and records are also bounded (`mrsMThatcher2.py:3651-3679`).

Candidate suppression consults those bounded structures before model work (`mrsMThatcher2.py:8840-8899`). The hot-post lane deliberately performs periodic full rescans, so old candidates can return after their durable-looking entries have been evicted.

An isolated state probe confirmed:

```text
completed_reply_target_retained_after_cap False
terminal_no_reply_retained_after_cap False
terminal_lookup_after_eviction None
```

After enough traffic, an old hot-post target can therefore be evaluated again, and a previously replied-to target can receive another public reply. Old `no_reply` candidates can incur another model call. The generic cap helper's eviction is tested, but no test reconciles that behaviour with the per-target idempotence requirement.

Current exposure is not imminent: the live state presently contains 123 normal replied targets, 53 quote-tweet replied targets, and 15 terminal evaluations. The defect is nevertheless a long-horizon durability failure.

Recommended correction: keep bounded presentation/cache lists, but move terminal and completed target identities to an append-only or compact durable ledger whose membership does not expire. Add a rescan test after more than the current cap.

### MEDIUM - Shadow status and digest totals mix different semantic-veto manifests

`summarise_events()` aggregates every retained event, then labels the combined totals with the most recent manifest version and hash (`semantic_alignment/quote_image_semantic_veto.py:813-858`). `shadow_status()` reads the entire history and uses its length for progress towards 100 and 200 observations (`semantic_alignment/quote_image_semantic_veto.py:1066-1100`).

The live history contains:

```text
15 events  v2  3e08320ffa8955e4bbad46444077ee0726e49dee90c29874a81cc872eb3751b0
 9 events  v3  8b202352ddf89af5860446f4dd20577832981fcf778316b2b01121f7c5550706
```

The command currently reports the v3 manifest alongside 24 observations, 4 vetoes, 2 unknowns, and 2 quotations with no safe image. In fact, all nine v3 observations are `allow`; those veto, unknown, and coverage-gap observations came from v2.

The digest has the same defect. Its summary aggregates all events in the report window but takes the last manifest identity (`mrs_log_digest.py:2002-2048`). A no-state digest from 18 July contained four v2 and nine v3 events, yet presented all thirteen under the v3 identity as twelve allows and one unknown.

This can materially mislead shadow-readiness assessment and reason distributions, although it does not alter production selection.

Recommended correction:

- make current status and observation progress apply only to the configured manifest hash;
- retain lifetime totals only when explicitly grouped by manifest hash and policy version;
- when a digest window crosses a manifest change, render separate strata or a clear mixed-policy warning;
- add mixed-version tests for both status and digest output.

### MEDIUM - Digest deduplication drops real same-file events and can lose resume-boundary events permanently

`Record` already retains `path` and `ordinal` (`mrs_log_digest.py:766-775`), but `read_records()` deduplicates on only timestamp, level, source, source line, and message (`mrs_log_digest.py:821-849`). Two physically distinct identical records emitted in the same second in the same file are collapsed.

`record_fingerprint()` uses the same content-only identity (`mrs_log_digest.py:777-787`). If an identical event is appended at the saved second after a digest run, its fingerprint matches the earlier event and it is removed at the resume boundary (`mrs_log_digest.py:617-641`, `5135-5145`). There is no file offset, ordinal cardinality, or stable source occurrence to distinguish it.

Synthetic reproduction:

```text
physical records from iter_records = 2
records from read_records           = 1
distinct resume fingerprints        = 1
```

Across the six retained production logs, the current implementation collapses:

```text
same-file duplicate keys       1,259
same-file physical excess      2,181 records
cross-file overlap keys            2
non-DEBUG duplicate groups          3
```

The non-debug examples include a repeated write-cooldown warning at `2026-07-16 13:19:26`. Most collapsed records are debug events, but the algorithm is used for API, posting, warning, and structured-event counts as well.

Existing tests cover overlapping rotations and different same-second messages. They do not cover identical records within one file or an identical event appended at the resume timestamp.

Recommended correction: deduplicate rotation overlap by occurrence cardinality between files, not by a global content set. Resume should retain stable per-file identity and offset/ordinal information, with an explicit rotation migration rule.

### MEDIUM - The active v3 shadow manifest passes freshness checks with three now-ineligible quotations

Production now requires exactly 610 attribution-eligible completed packets (`mrsMThatcher2.py:6214-6247`). The active v3 shadow manifest validator still requires 613 quotations and fixed 22,157/22,028/129 pair counts (`semantic_alignment/quote_image_semantic_veto.py:438-506`). Runtime freshness checks verify only the source files listed inside the manifest (`semantic_alignment/quote_image_semantic_veto.py:651-667`); they do not compare the manifest quote set to the current production eligibility set or hash the attribution predicate.

After accounting for the five intentional runtime quote aliases, the manifest contains these three genuinely stale quote IDs:

```text
7f75c4d086fb67b0e54d9d63dbe470dc6f9f929aee00ce4a02d01bbc9c8d4646
8c70978a89ef43e405dbc7eb0bb9751d9dbe631d63d9834ccf3dfde51a4a971c
cf7a03be1c6e34efbcfec0cc8010544e2deab777a05cb0193d237814244f5c8e
```

They account for 91 pair rows: 90 allows and one veto. The current eligible set has no unaliased quote missing from the manifest. `shadow-status` nevertheless reports `valid=true` and `manifest_stale=0`.

The regular selector excludes these quotations, so this cannot cause a live post and active veto remains impossible. It does make corpus and pair totals stale, and demonstrates that source-hash freshness is insufficient when eligibility semantics change without changing the historical source file.

Recommended correction: rebuild the candidate shadow manifest for the 610 eligible IDs, include a versioned eligibility-manifest hash and attribution-rule version, and make startup compare the resolved manifest quote set with the runtime eligibility set.

### LOW - An ambiguity marker blocks the final post but not preceding media or model work

`create_post()` checks the durable ambiguity marker immediately before the X post (`mrsMThatcher2.py:3884-3930`). Regular and meme lanes upload media first (`mrsMThatcher2.py:6940-6946`, `7401-7408`). The reply lane prepares media context and may call xAI before reaching `create_post()` (`mrsMThatcher2.py:9017-9053`, `9164-9170`).

With an isolated temporary ambiguity marker, calling the meme lane produced:

```text
raised AmbiguousRemotePostOutcome
calls_before_block [('upload_media', '/tmp/.../meme.jpg')]
```

Public posting remains fail-closed, which limits severity. However, after an ambiguous outcome the still-running process can continue incurring model work and X media uploads on later lane checks, followed by deterministic failures at the final post boundary.

Existing ambiguity tests exercise marker creation and `create_post()` blocking, but not a whole lane with an already-present marker.

Recommended correction: check the ambiguity barrier at the start of every posting/reply lane, before context retrieval, media preparation, model calls, image upload, or selection side effects. Add one test for each lane.

## Areas reviewed without a confirmed defect

No additional defect was established in these areas:

- regular quote/image and meme confirmed-post receipt reconciliation;
- confirmed conversational-reply and historical-context receipt recovery;
- quote and image cycle rollback after failed posting;
- clarification thread permanence and pre-model terminal skipping;
- API cooldown separation for terminal target-specific 403 responses;
- analytics identity correction and database integrity;
- generated-identity counterfactual selection reporting within a single policy version;
- semantic-veto lookup mutation and RNG invariants;
- current startup and systemd process layout.

The analytics SQLite database returned `integrity_check=ok`. Post `2077121396186992800` has exactly one `posts` row and one `post_pairs` row. No analytics mutation was performed.

## Static analysis

`pylint --enable=E,F` and Bandit were run over the principal production and support modules.

Pylint's reported mapping/optional-type errors were inference false positives. Its `log_event()` warning at `mrsMThatcher2.py:6585` is also false: the `event` key is popped immediately before `**event` is passed.

Bandit's dynamic-SQL warnings use fixed internal column sets with parameterised values and were not reproduced as injection. Its random-number warnings are not relevant because selection randomness is not a security primitive. The unsafe pickle warning in the harness was reproduced and is finding 1.

## Validation

Focused suites, deliberately excluding the twelve-minute full run:

```text
python3 -m pytest -q \
  tests/test_reply_strategy.py \
  tests/test_digest_safety_hardening.py \
  tests/test_digest_reply_observability.py \
  tests/test_quote_image_semantic_veto_shadow.py \
  tests/test_engagement_analytics.py

258 passed in 12.61s
```

```text
python3 -m pytest -q tests/test_quote_image_selection_harness.py \
  -k 'network_guard or checkpoint_round_trip or open_audit_blocks_production_write or import_has_no_production_side_effects'

4 passed, 24 deselected in 0.16s
```

```text
python3 -m pytest -q tests/test_followup_fail_safe_hardening.py tests/test_unit_helpers.py \
  -k 'ambiguous_remote_post or truncated_pagination_no_reply or clarification_threads_are_not_evicted or reply_not_permitted'

7 passed, 428 deselected in 0.59s
```

These passing results show that the existing regressions remain green while omitting the reproduced adversarial cases.

`git diff --check` passed before this report was added.

## Production isolation

Before and after the review:

```text
ActiveState=active
SubState=running
MainPID=2285642
wrapper PID=2285642
child PID=2285644
ExecMainStartTimestamp=Sun 2026-07-19 01:57:20 BST
NRestarts=0
```

The engagement analytics timer remained active and waiting. All production receipt and ambiguity-marker barriers were absent when checked.

The deployed bot path is a symlink to the reviewed repository source, and the source/deployed `mrsMThatcher2.py` SHA-256 was identical:

```text
7223d1a54fa7642d2dceadfd34ac91369aa50b8f0ad926e2c46d6bb91ff2b600
```

No X post, X read, external DNS lookup, external model call, service restart, signal, production-state write, receipt write, analytics write, configuration change, commit, push, or deployment occurred. The only review output in the repository is this report.

## Recommended order

1. Replace unsafe checkpoint pickle and make harness isolation truthful and enforceable.
2. Extend direct factual-question handling beyond the current four interrogatives.
3. Make completed and terminal reply-target membership durable beyond cache limits.
4. Separate semantic-veto telemetry by manifest hash.
5. Correct digest occurrence tracking and resume identity.
6. Rebuild and freshness-bind the v3 shadow manifest to the 610-quote eligibility set.
7. Move ambiguity checks ahead of all remote preparation work.

VERIFIED DEFECTS FOUND - NO FIXES APPLIED
