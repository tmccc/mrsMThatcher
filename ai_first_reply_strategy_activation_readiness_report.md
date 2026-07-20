# AI-first reply strategy activation readiness

Date: 20 July 2026
Project: `/disks/disk1/etc/mrsMThatcher`
Candidate strategy: `ai-first-reply-v3`
Production activation performed: no

## Executive verdict

The AI-first conversational reply strategy is ready for a separately authorised,
controlled activation. I am satisfied that the previously identified safety,
runtime-integration, persistence and pilot-provenance blockers have been
corrected and independently rechecked.

The verdict does not authorise an ordinary restart of the current working tree.
The ignored live `mrsMThatcher.local.json` deliberately remains unchanged and
still contains the retired V1 `reply_strategy` configuration. The reviewed V3
configuration candidate must be installed atomically as part of the eventual
activation procedure. Until then, the current loader will fail closed rather
than start with an ambiguous mixed configuration.

No service was stopped, restarted, reloaded or signalled during this work. No X
post was made and no production state, receipt, history, ledger, analytics file
or live configuration was modified.

## Why the candidate is ready

The production candidate now has one conversational strategy rather than a V1
and V3 parallel path:

1. A proposer interprets the actual incoming contribution and creates an
   original reply in one of five modes.
2. Local retrieval runs only for factual claims declared in the proposed reply.
3. A claim-evidence call adjudicates exact local passages and semantic fields.
4. A fresh reviewer call independently checks relevance, factual support,
   direction, actors, relationships, dates, quantities, quotation attribution,
   tone and account suitability.
5. Only explicit reviewer approval can create a persisted approved draft.
6. Existing receipt, ambiguity, cap, terminal-thread and duplicate-target
   controls remain responsible for the eventual write transaction.

The deterministic shell does not trust model metadata. It independently
validates the schema, exact claim inventory, local evidence identifiers and
passages, evidence hashes, exact Thatcher wording, reply length, sentence count,
links, mentions, emoji, revision count, reviewer approval and persisted-draft
approval hash.

The remote pilot used separate proposer, evidence and reviewer calls. The
reviewer did not receive hidden proposer reasoning. No tool, search, media or X
posting capability was available to the pilot.

## Blockers corrected

The independent post-pilot review identified eight concrete issues. All are now
closed.

### Disabled-mode candidate consumption

When AI-first is disabled, mention, hot-post and quote-tweet lanes now stop
before discovery, media preparation, evidence loading, model calls or terminal
evaluation. Disabled mode cannot consume a candidate as a permanent
`no_reply`.

### Persisted evidence binding

`EvidencePassage.model_input_hash()` now binds every model-visible semantic
field, including actor, action or relationship, direction or polarity, date or
period and quantity. V3 persisted drafts retain these evidence-input hashes and
are rejected if evidence metadata changes after approval.

### Complete quotation declaration

The deterministic quotation check now detects authorised corpus wording outside
all explicitly declared exact spans. Declaring one exact Thatcher quotation can
no longer conceal a second undeclared corpus quotation in the same reply.

### Operational failure classification

Malformed or incomplete structured model output is now an
`operational_failure`, not an editorial `no_reply`. Runtime emits
`ai_reply_pipeline_failure`, raises the established retryable xAI error, and
does not advance the mention cursor or create a terminal reply evaluation.
Digest reporting separates these failures from deliberate editorial silence.

### Provider-backed revision coverage

The final provider qualification contains two forced full revision cases:

- one factual case, including revised evidence adjudication;
- one non-factual off-topic principle case.

Both traversed initial proposal, initial review, revised proposal and a fresh
final review. Both passed through the production pipeline with the one-revision
limit intact.

### Sentence counting

The sentence counter now treats `No.` as a number abbreviation only when the
following context supports that interpretation. Valid prose such as `No. 10`
does not receive a false sentence boundary, while a standalone `No.` remains a
sentence.

### Pilot provenance and crash recovery

The pilot uses a fixed fixture date, records all material source hashes, stores
the response cache atomically before marking a ledger operation complete, and
recovers the prior crash-window state safely. A resume cannot repay a completed
logical request.

### Activation configuration

Source and example defaults remain disabled. A separate reviewed candidate
contains V3 only and is enabled specifically for the eventual controlled
activation. The live ignored configuration was not changed in advance.

## Provider qualification

The authoritative qualification is:

`semantic_alignment_research/ai_first_reply_strategy_001/provider_pilot_20260720_v13/`

| Measure | Result |
|---|---:|
| Model | `grok-4.3` |
| Strategy | `ai-first-reply-v3` |
| Pilot protocol | `ai-first-provider-pilot-v8` |
| Temperature | 0 |
| Fixed fixture date | 2026-07-20 |
| Cases | 20 |
| Cases passed | 20 |
| Ordinary end-to-end cases | 10 |
| Forced full revision cases | 2/2 passed |
| Adversarial reviewer challenges | 8/8 blocked |
| Model calls | 39 |
| Completed ledger operations | 39 |
| Cached raw responses | 39 |
| Ambiguous operations | 0 |
| Known and conservative exposure | US$0.13764915 |
| Input tokens | 67,141 |
| Completion tokens | 7,302 |
| Reasoning tokens | 21,391 |
| Call latency p50 | 10.491 seconds |
| Call latency p95 | 21.678 seconds |
| Call latency maximum | 31.571 seconds |

Four first responses were malformed or empty. Each was handled by the single
bounded same-stage retry and the case then passed. A second malformed response
would fail closed. This is an availability and latency risk, not a path to an
unsafe approval.

The v13 resume check left the cost ledger byte-identical and made no additional
provider request. Every one of the 39 logical-call IDs is unique, completed and
paired with one cached response.

### Pilot hashes

| Artefact | SHA-256 |
|---|---|
| `run_manifest.json` | `05256200f57ef1a12f311ede5cfecd5889fd0593abe3bff12971f9c62d1752ff` |
| `cost_ledger.json` | `e38da607e297fd4ad226485ad97e7bcc9012c93d6f77c72edba34480d44a6ea4` |
| `pilot_results.json` | `ee3c6093e8c74fb8c281ca879a5643e27fe9d546a5868f006b1e3d57024bd270` |
| `pilot_report.md` | `5f62f3662adf94f2727ca376f13f46a019f613f9f79fd8456a3f1f448ab10277` |

All twelve source hashes in the v13 run manifest were recomputed after the
pilot and matched exactly. This includes the strategy, evidence repository,
factual evidence, pure research schema, corpus inputs, all provider fixtures
and the pilot runner.

Total recorded spend across all thirteen development pilot directories is
US$1.32145405 with US$0.00 ambiguous exposure. The final readiness-remediation
runs v12 and v13 cost US$0.27342640 combined. This is well within the expanded
authority and no further spend was needed merely to consume available budget.

## Independent review

The detailed independent review initially returned `NOT READY FOR ACTIVATION`
and identified the issues listed above. After they were fixed, its only
remaining blocker was that the successful v12 pilot predated docstring-only
changes to the hashed pilot runner.

A fresh v13 qualification was therefore executed from the exact current source.
A separate narrow read-only review then recomputed the v13 source hashes,
ledger and result invariants and returned:

```text
The sole blocker is closed.

PROVENANCE BLOCKER CLOSED
```

No defect from the independent review remains open.

## Offline and automated validation

The complete broad suite was run after the functional corrections:

```text
1 failed, 1,934 passed, 1 skipped in 762.98 seconds
```

The sole failure was the repository documentation policy detecting seven new
pilot helper methods without docstrings. No functional test failed. Succinct
docstrings were added, then the exact documentation and pilot group passed:

```text
14 passed
```

Because that final edit changed documentation strings only, the twelve-minute
suite was not repeated. Subsequent affected-surface checks passed:

```text
AI-first reply, runtime, provider, evaluator and digest group: 159 passed
Final integration-focused selection: 36 passed, 298 deselected in 17.92s
Targeted documentation and pilot group: 14 passed
```

Additional integration work had previously run 615 tests with one expected
skip. Two expectations still modelled malformed responses as editorial silence;
after correction, the exact five affected tests passed and confirm that
operational failures do not advance cursors or create terminal records.

Final checks:

- targeted `py_compile`: passed;
- Ruff fatal, undefined-name and import checks: passed;
- `git diff --check`: passed;
- network-free offline evaluation: passed;
- adversarial fixtures: 14/14 expected blocks;
- valid-mode fixtures: 6/6 expected outcomes.

The current offline evaluation is under:

`semantic_alignment_research/ai_first_reply_strategy_001/offline_evaluation_v3/`

It made zero model and network calls. It also examined 27 retained historical
reply examples: 27 satisfied the deterministic V3 envelope and one was
correctly rejected for undeclared historical wording.

## Evidence and corpus boundary

Production no longer imports the Gemini research SDK merely to validate the
local quotation corpus. Pure packet constants and validation live in
`semantic_alignment/quote_research_schema.py`; offline research code imports
that module rather than the production process importing provider clients.

Evidence construction is lazy. The production process does not pay the corpus
load cost at bootstrap or during pure self-test paths. A real eligible reply
attempt loads and validates local evidence before media or provider work and
fails closed if that evidence is unavailable.

The isolated candidate bootstrap validated:

- 626 completed research packets;
- six unresolved packets excluded;
- 610 attribution-eligible Thatcher packets;
- 7,930 indexed passages, including two factual Berlin Wall records;
- the v3 semantic-veto shadow at 22,066 pairs;
- semantic-veto active enforcement false.

## Legacy drafts and receipts

The activation audit is:

`semantic_alignment_research/ai_first_reply_strategy_001/activation_candidate/legacy_draft_audit.json`

Its SHA-256 is
`e02ae3c31f14e27a3d5f5640b4a3da48796fef4aa4cdeae327bce3fc87e47c3e`.
It found zero V1 drafts and reported `deployment_blocked=false`. V1 drafts are
not migrated or interpreted by V3. Any V1 draft appearing before activation is
a deployment blocker and must be separately archived and classified.

At final readiness inspection these safety files were absent:

- `regular_post_receipt.json`;
- `meme_post_receipt.json`;
- `historical_context_reply_receipt.json`;
- `confirmed_reply_receipt.json`;
- `ambiguous_post_outcome.json`.

This is a point-in-time observation only. The established receipt barrier and
legacy-draft audit must run again immediately before any controlled restart.

## Activation candidate

The reviewed ignored configuration candidate is:

`semantic_alignment_research/ai_first_reply_strategy_001/activation_candidate/mrsMThatcher.local.json`

SHA-256:
`69231e462b50a08056db0536e4564c75b9befa27f7f1f250eabe3d330e247f83`

It has mode `0600`, removes the retired `reply_strategy` key, enables only
`ai-first-reply-v3`, uses `grok-4.3` for proposer, evidence and reviewer calls,
and retains fail-closed behaviour. It passed isolated configuration and
bootstrap validation.

The source example remains disabled. The live ignored configuration remains
unchanged, contains V1 configuration and does not contain V3. This deliberate
state prevents accidental activation and means the service must not be
restarted before the controlled configuration cut-over.

## Production isolation proof

Before and after implementation, testing, pilot execution and final review:

| Property | Value |
|---|---|
| Service | `mrsMThatcher.service` |
| Wrapper PID | 1595442 |
| Python child PID | 1595443 |
| Start time | Sun 2026-07-19 23:07:45 BST |
| Restart count | 0 |
| State | active/running |
| Cgroup | `/user.slice/user-1000.slice/user@1000.service/app.slice/mrsMThatcher.service` |

No stop, restart, reload, signal, X post, media upload, production-state write,
receipt write, history write, analytics write, commit, push or deployment was
performed.

## Source hashes

| File | SHA-256 |
|---|---|
| `mrsMThatcher2.py` | `aa25463121607ffe5147312aa2ddb8bf91912a9db6fa996d0639d2d444d690a6` |
| `mrs_log_digest.py` | `9d4982b202433cadf81d85c962f0c82060dbf6d0beb43e5d885d75c0beaaa6c7` |
| `reply_strategy.py` | `5dfab3f98b39a7875bd76563e541e02910118d6febfcb944faabc777e2dee463` |
| `reply_evidence.py` | `57f6c6fa7830baab6965e75a0aa349ed5dde51960c186ce98872df2e945ef0f4` |
| `reply_factual_evidence.json` | `56e5e121b3ed089b0c9e095292d47432e1dc2e7ff9625e6fb0ec59fa1010b069` |
| `historical_context_formatter.py` | `0948e0dba9755af63a92e376b83335656b88047ea68aaaff09993f0be2b284db` |
| `semantic_alignment/quote_research_schema.py` | `032206958da4b55073fc2f0a9345d1ca73de915d41448c987f5e372ab4bb68c4` |
| `tools/pilot_ai_first_reply_strategy.py` | `fb75f13e2920c325d43b24e6606b9d247577b3cf7a6d617bbcdcd314ddfea78d` |
| `tools/evaluate_ai_first_reply_strategy.py` | `63dd23d6d9e6ee3827faf36b5bd9fbc03e99c2b921e43206524eda83b803f088` |
| `mrsMThatcher.local.example.json` | `b0040a29cbd3486d42b305e76c6bb35a93b1dadfc6d4f49f0b15405c1974e464` |

## Working-tree scope

The cumulative tracked diff currently contains 27 modified files, with 5,338
insertions and 4,909 deletions. It includes the AI-first cut-over, its tests and
the previously prepared shadow/corpus consolidation changes. New files include
the evidence repository, pure research schema, factual evidence, provider and
offline runners, fixtures, tests, reports and isolated research artefacts.

The working tree is intentionally uncommitted. A deployment must use a reviewed
commit, not an arbitrary dirty-tree restart. Raw response caches and superseded
development pilot artefacts should be assessed against repository ignore and
retention policy before committing; they are audit data, not production source.

## Remaining risks

1. The provider qualification is deliberately small. It establishes schema
   compatibility and the known safety regressions, not every possible style or
   topic encountered on X.
2. Four of 39 initial provider responses needed bounded JSON recovery. This can
   reduce reply availability or increase latency. It cannot bypass review.
3. A maximum six-call revision attempt has no unlimited repair budget. Any
   exhausted or malformed stage fails closed and is available for a later
   ordinary operational retry rather than posting unchecked output.
4. The model remains probabilistic despite temperature zero. Deterministic
   validation, fresh review and durable approval binding are therefore required
   production controls and must not be weakened.
5. Receipt and draft barriers are mutable operational preconditions. Their
   current clear state is not a substitute for checking them immediately before
   activation.

These are managed residual risks rather than activation blockers.

## Required controlled activation

Activation must be a separate, explicitly authorised operation:

1. Review the final cumulative diff, exclude inappropriate raw/transient pilot
   data, commit the intended source and audit artefacts, and push the commit.
2. Record source, installed and live-configuration hashes and create rollback
   copies with ownership and modes preserved.
3. Re-run focused tests, compilation and `git diff --check` from that commit.
4. Re-run the receipt barrier and stable V1-draft audit. Abort if any outcome is
   unresolved.
5. Confirm exactly one wrapper and one production child.
6. Atomically install the reviewed `0600` V3 local configuration candidate.
7. Restart only `mrsMThatcher.service` through its systemd user unit.
8. Confirm exactly one replacement wrapper and child, no restart loop, V3 corpus
   loading, no stale warning, and no traceback.
9. Verify the first naturally occurring reply pipeline event. Do not manufacture
   an X test post.

If startup fails, restore the previous committed revision and original ignored
local configuration atomically, restart once through the same systemd unit, and
confirm the prior single wrapper/child arrangement. V1 is not retained as an
in-process fallback.

## Final decision

The implementation, fail-closed integration, persisted approval binding,
provider behaviour, revision path, provenance and operational cut-over plan are
sufficiently demonstrated for a separately authorised controlled activation.

READY FOR SEPARATELY AUTHORISED ACTIVATION
