# AI-first Conversational Reply Strategy Replacement

Date: 20 July 2026
Project: `/disks/disk1/etc/mrsMThatcher`
Strategy: `ai-first-reply-v2`

## Executive verdict

The quotation-driven conversational reply subsystem has been replaced in the
working tree by one AI-first proposer, claim-evidence and independent-reviewer
pipeline. Only an immutable, locally revalidated draft carrying an explicit
reviewer approval can reach the existing durable posting path. The old V1
reply modes and their production semantic heuristics are not retained as a
parallel or fallback runtime strategy.

The original complete offline suite passed 1,880 tests with one expected skip.
A subsequent runtime-isolation hardening pass exercised 1,888 collected tests:
1,886 passed, one was skipped, and the sole failure correctly detected that a
prepared shadow manifest still contained the old formatter source hash. The
manifest was rebuilt offline and that exact test, followed by its 118-test
focused group, passed. The twelve-minute suite was not repeated solely for an
already isolated provenance-hash update.

The running production process was not restarted and has not loaded these
changes. Activation is not yet authorised or safe: the ignored live
configuration still contains the retired V1 `reply_strategy` block, and no
real provider call was made during this task. An independent review, an
explicitly authorised non-posting provider pilot, and an atomic local-config
migration are required before a controlled deployment.

## Architecture and data flow

1. Existing deterministic lane, target, cap, terminal-thread, duplicate and
   receipt checks establish whether a candidate may be considered.
2. The proposer receives the incoming contribution as the primary field, with
   quoted-post text, bounded parent context and clarification context in
   separate labelled fields. It returns strict structured output.
3. When the draft contains factual claims, `reply_evidence.py` uses the local
   research corpus only to shortlist claim-specific passages. Lexical overlap
   never establishes support.
4. The evidence model adjudicates each declared claim against exact candidate
   passages, including actor, action or relationship, direction or polarity,
   date or period, and quantity.
5. A fresh reviewer call receives the visible contribution, bounded context,
   reply, mode and evidence package, but not the proposer's hidden
   interpretation or reasoning. It independently checks relevance, direct
   answers, allegations, claims, entities, direction, dates, quantities,
   quotation attribution, mode, tone and account suitability.
6. Only `approve`, with every required reviewer check true and every factual
   claim supported, creates an `AIReply` and an immutable V2 draft record.
7. At most one revision is permitted. Invalid JSON, schema failure, evidence
   failure, reviewer rejection, call exhaustion, timeout or partial failure
   returns `no_reply`.
8. The existing posting transaction, confirmed-reply receipt, ambiguity
   barrier, reconciliation, history and cap updates remain responsible for the
   remote write.

No reply is assembled from a selected Thatcher quotation packet. The research
corpus is consulted only for declared claims and exact Thatcher wording.

## Modes and schemas

The only V2 modes are:

- `direct_factual_answer`
- `opinion_or_principle`
- `light_humour`
- `courtesy`
- `no_reply`

Tone and confidence are metadata. The proposer schema requires mode,
interpretation, proposed reply, a complete factual-claim inventory, exact
Thatcher-wording declarations, tone, confidence and a no-reply reason. Every
claim carries its exact text plus actor, action or relationship, direction or
polarity, date or period, quantity and evidence requirement.

The evidence schema returns one result for every claim, a verdict of
`supports`, `contradicts` or `insufficient`, exact evidence IDs and passages,
and the same semantic dimensions. Evidence IDs are SHA-256-bound to quotation
ID, packet field, exact passage and source-record hash. References are rejected
unless the ID, source hash and exact passage exist locally.

The reviewer schema permits only `approve`, `reject` or `revise`. Approval
requires an exact claim-inventory match and affirmative checks for direct
answering, relevance, non-endorsement, factual support, actor/action/
relationship correctness, direction/polarity, dates/quantities, quotation
attribution, original-prose labelling, mode/tone and account suitability.

## Evidence corpus

`EvidenceRepository` validates the partition and exact packet-file hash declared
by the immutable corpus manifest and final status. The current artefacts declare
632 records, 626 completed packets and six unresolved records, from which 610
source-attributed Thatcher packets are admitted. It indexed 7,928 immutable
passages during final verification. Relevant source hashes are:

- `research_packets.json`: `307b01f0c854ad8e16a50ed399bfa0cbd5f4b8c3d00709bfe02100a289143611`
- `unresolved_quotes.json`: `6acb4d2dede398f74e488902c62c672437db8721f6f75c9adebdf323889feb4f`
- `corpus_manifest.json`: `81f6b2974c30d5810afc74c24704f5ee3d3868a6b2d94859cad2fa8cebce12da`

Exact historical wording is accepted only when an authorised corpus record
contains the same whitespace-normalised text, preserving source capitalisation
and internal punctuation, and that exact text occurs at a valid boundary in
the proposed reply. Original prose remains permitted, but undeclared, altered
or fabricated historical wording is rejected.

## Production and research dependency boundary

The production corpus validator was extracted to
`semantic_alignment/quote_research_schema.py`. It contains only packet schema
constants and pure validation logic; it imports no provider SDK, HTTP client or
research runner. The Gemini/Vertex research module imports and re-exports that
validator for its offline callers, while production imports the pure module
directly.

The defect was reproduced before correction by running `--self-test` from an
isolated installation with a deliberately unavailable `google` package. Startup
followed `production_bootstrap()` through `EvidenceRepository` into
`quote_research_gemini.py` and failed on the provider SDK import. After the
correction:

- `production_bootstrap()` does not construct conversational evidence;
- `--self-test` and `--initialise` do not load optional corpus or shadow
  resources;
- evidence is loaded on the first actual conversational-reply candidate;
- a load failure is cached and fails closed before media preparation or any AI
  or posting call;
- pending approved drafts are retained rather than destroyed by a temporary
  corpus failure;
- corpus counts are checked against internally consistent manifest/status
  declarations instead of embedding 632/626/6 in the production loader;
- the saved packet bytes must match the SHA-256 recorded in final status.

A conservative static import-closure check reduced the production entry-point
closure from 18 project modules to seven modules plus the package initializer.
Neither `quote_research_gemini`, `google.genai`, nor the Gemini/Vertex transport
is now in that closure. Policy-specific count checks in the separately versioned
semantic-veto manifest validator were deliberately left unchanged.

## Deterministic safety boundary

The local shell retains or adds fail-closed enforcement for:

- strict config, context, proposer, evidence, reviewer and persisted-draft
  schemas;
- maximum model calls (six), one revision, per-call timeouts and output caps;
- reply length, sentence count, control characters, links, mentions, hashtags
  and emoji limits;
- complete factual-claim inventory and complete claim adjudication;
- exact local evidence IDs, passages and source hashes;
- exact authorised Thatcher wording;
- duplicate/repetitive replies;
- reviewer approval and an approval hash over the complete persisted draft;
- target, thread, lane, contribution and full bounded-context identity;
- receipt barriers, ambiguous writes, duplicate targets, terminal threads,
  clarification limits, author caps and global budgets.

The final checks inspect reply text independently of proposer metadata. This
prevents a proposer from declaring a factual sentence non-factual, changing the
mode to evade checks, omitting claims from reviewer input, or altering approved
prose after review.

## Repeated adversarial hardening

Successive review passes confirmed and fixed 17 bypass or consistency defects:

1. malformed nested configuration values could raise an exception instead of
   producing a controlled validation error;
2. an unpunctuated trailing sentence could evade the sentence limit;
3. Arabic question marks and danda terminators could evade the sentence limit;
4. non-ASCII number signs could evade the hashtag prohibition;
5. zero-width and other format controls were not rejected consistently;
6. email addresses could evade the link prohibition;
7. X weighted length was not enforced consistently with the platform limit;
8. evidence rows could be returned in a different order from the claims;
9. a pending approved draft could be reused after its text became a recent
   duplicate;
10. reviewer-local item limits were not checked independently;
11. a proposer could return a blank interpretation;
12. the evidence model could rewrite actor, action, relationship, direction,
    date or quantity metadata supplied with a claim;
13. low-confidence evidence could be used as support;
14. exact quotation authorisation was too tolerant of punctuation changes;
15. the digest conflated V2 evidence references with legacy research packets;
16. IDN domains, IPv4 addresses, full-width commercial-at/number-sign symbols
    and combining-mark hashtags could evade local text checks; and
17. a reply could alter capitalisation or straight/curly apostrophes while
    declaring an exact authorised quotation.

The evidence prompt is now versioned as `claim-evidence-entailment-v2`.
Mutation checks covered all authorised wording records, case and apostrophe
changes, all Unicode characters canonically named NUMBER SIGN or COMMERCIAL
AT, IDN domains, IPv4 addresses, decimals and abbreviation controls. No bypass
remained in those sets after the final pass.

## Legacy V1 treatment

The new runtime uses `pending_ai_reply_drafts`; it does not load, reinterpret,
migrate or post `pending_reply_drafts`. Startup accepts the old key only when it
is empty, removes that empty container during state normalisation, and fails
closed if any V1 draft exists.

The final stable-read audit found zero V1 drafts and did not modify state:

- audit: `semantic_alignment_research/ai_first_reply_strategy_001/legacy_v1_draft_audit.json`
- audit SHA-256: `098c5860863d824560a7284628a5807eb31034eeb30fc9fe8dd05a686539944d`
- `bot_state.json` SHA-256 observed by the final audit:
  `149d43723eca6bf1dc63740bcf6eadf2883b8ae6c7b5a6c9bcff284f84fcedcb`
- legacy drafts: 0
- deployment blocked by drafts: false

The audit uses a stat/read/stat stability protocol because the live bot may
legitimately update its state. If any V1 draft appears before deployment, it
must be archived and hashed, classified safely and invalidated atomically. The
deployment must fail if classification is not conclusive.

Posting receipts are deliberately separate. No receipt compatibility was
removed. Final inspection found no pending regular-post, meme, historical-
context, conversational-reply or ambiguous-outcome receipt. Receipt validators
bind approved V2 drafts to the target, thread, lane, contribution, context,
quoted original, evidence and approval hash; unresolved outcomes still block
posting and require normal reconciliation.

## Old production code removed

Removed production dependencies include the V1 `ReplyDecision`, quotation-
first conversational retrieval and prompt assembly, V1 parsing and mode
normalisation, mode-specific validators, factual-question phrase detection,
political concept maps, token relevance bridges, allegation-verb checks,
delimiter-specific quotation checks, the Berlin Wall sentence patch, V1 draft
load/store/retry helpers, and V1 audit/report helpers.

The retired production modes are absent from the V2 strategy. Remaining names
occur only in historical reports/log compatibility, old-log digest tests,
offline hybrid-retrieval benchmarking, and tests proving V1 drafts fail closed.
Repository-wide searches also covered `/usr/local/bin`, `/home/tonym` and
`/disks/disk1/etc`; no active external caller of a deleted V1 interface was
found. No historical audit artefact was rewritten.

## Adversarial validation

The durable adversarial corpus contains 14 cases covering the unrelated
Burnham/Berlin-Wall reply, reversed East/West direction, unrelated evidence,
unusual allegation verbs, Unicode quotation delimiters, employment versus
historical record, wrong actor, relationship, date, quantity and polarity,
parent and quote-tweet context hiding the contribution, and proposer claim
omission. All 14 expected rejections are exercised through scripted proposer,
evidence and reviewer paths.

Additional tests cover factual yes/no, `which`, `whose`, `how many`, `how
long`, metadata/reply contradictions, sentence/newline/keycap/emoji/domain/IP
bypasses, Unicode symbol variants, exact-quotation case and apostrophe changes,
invalid evidence IDs and passages, revision exhaustion, persisted-draft
tampering, receipt recovery and terminal-thread behaviour.

The valid corpus exercises all five modes through the real pipeline:

- factual: `The flow was overwhelmingly from East to West once the option existed.`
- opinion: `Conviction should lead; popularity may follow if the case is sound.`
- principle: `Institutions endure only when people defend their purpose.`
- light humour: `A committee can postpone a decision with admirable punctuality.`
- courtesy: `Thank you for taking the trouble to read them.`
- deliberate incoherent bait: `no_reply`

## Offline replay and cost

The network-free evaluator inspected 199,772 retained structured-log lines,
covering 16 July 2026 14:04:32 through 20 July 2026 10:07:37, and found 93
unique recent candidate IDs. It also inspected 25 saved V1 reply texts: 24 fit
the V2 deterministic envelope and one was correctly rejected for undeclared
Thatcher wording. No production telemetry or state was written.

Five representative local evidence queries had latency of 2.000 ms p50 and
3.589 ms p95/max. This measures only local retrieval, not provider latency.

No AI or network call was made. Using the retained historical median xAI call
cost of US$0.00603185 as a rough extrapolation gives:

- non-factual two-call reply: US$0.01206370;
- factual three-call reply: US$0.01809555;
- maximum six-call revision envelope: US$0.03619110.

These are estimates, not provider quotations or measured V2 costs. Natural-
language quality, structured-schema compatibility and real latency were not
measured because doing so required a remote model call. They must be tested in
a separately authorised, non-posting pilot before activation.

## Test results

Development exposed and fixed six failures in the first broad run. They were
test/corpus bootstrap assumptions and a scheduler parity fixture that modelled
the old single-call strategy, not live failures left unresolved.

- Initial broad run: 1,840 passed, 1 skipped, 6 failed in 1,086.51 seconds.
- Final broad offline command:
  `nice -n 5 ionice -c2 -n6 env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q`
- Final broad result: 1,880 passed, 1 skipped, 3 deprecation warnings in
  1,172.50 seconds.
- Runtime-isolation broad command: the same offline command above.
- Runtime-isolation broad result: 1,886 passed, 1 skipped and one stale-source-
  hash failure in 747.02 seconds. No behavioural assertion failed.
- The v3 shadow deployment candidate was rebuilt with the existing deterministic
  offline command. It remains non-enforcing and contains 610 quotations, 91
  images and 22,066 judged pairs; SHA-256
  `9db4c553b4ffde9ff51c214b1b6932966edfbbd16a96d4ab0cf67444fe9bdd73`.
- The exact formerly failing manifest test then passed, followed by 118 focused
  corpus, bootstrap, attribution-cleanup and runtime-isolation tests in 5.37
  seconds.
- Additional runtime-isolation groups passed: 234 schema/research/reply tests,
  668 directly affected tests, nine isolation tests, and two production-path
  integration tests.
- A final 36-test research/schema group exposed one timing-sensitive provider-
  concurrency fixture failure (`valid_packets=0`) on its first run. The exact
  test passed in isolation and the unchanged 36-test group then passed in 4.54
  seconds; no production code was changed for that transient result.
- Final focused command:
  `env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_reply_strategy.py tests/test_ai_first_reply_offline_evaluation.py tests/test_digest_reply_observability.py`
- Final focused result: 114 passed in 2.32 seconds.
- Receipt, recovery, cap and configuration focus: 46 passed, 362 deselected in
  3.13 seconds.
- A pre-final integration group found one stale digest-column assertion after
  the V2 evidence-reference metric was separated from legacy packet counts.
  The assertion was corrected and its isolated rerun passed; the complete final
  suite above includes that test.
- `python3 -m py_compile reply_strategy.py reply_evidence.py mrsMThatcher2.py mrs_log_digest.py historical_context_formatter.py semantic_alignment/quote_research_schema.py semantic_alignment/quote_research_gemini.py semantic_alignment/image_quote_eligibility.py semantic_alignment/openai_quality_trial.py tools/evaluate_ai_first_reply_strategy.py`: passed.
- `git diff --check`: passed.

The three warnings are one Starlette and two BeautifulSoup/lxml deprecations;
none is introduced by the reply pipeline.

## Files changed

Production and documentation:

- `README.md`
- `mrsMThatcher.local.example.json`
- `mrsMThatcher2.py`
- `mrs_log_digest.py`
- `reply_strategy.py`
- `reply_evidence.py` (new)
- `historical_context_formatter.py`
- `semantic_alignment/hybrid_reply_retrieval.py`
- `semantic_alignment/image_quote_eligibility.py`
- `semantic_alignment/openai_quality_trial.py`
- `semantic_alignment/quote_research_gemini.py`
- `semantic_alignment/quote_research_schema.py` (new)
- `tools/simulate_regular_post_futures.py`
- `tools/evaluate_ai_first_reply_strategy.py` (new)

Tests and fixtures:

- `tests/fake_api_server.py`
- `tests/fixtures/ai_first_reply_adversarial_cases.json` (new)
- `tests/fixtures/ai_first_reply_valid_cases.json` (new)
- `tests/test_ai_first_reply_offline_evaluation.py` (new)
- `tests/test_digest_reply_observability.py`
- `tests/test_fail_safe_bootstrap_and_control.py`
- `tests/test_hybrid_reply_retrieval.py`
- `tests/test_historical_context_reply.py`
- `tests/test_integration_harness.py`
- `tests/test_logging_isolation.py`
- `tests/test_reply_strategy.py`
- `tests/test_reply_runtime_isolation.py` (new)
- `tests/test_shadow_functionality_consolidation.py`
- `tests/test_simulate_regular_post_futures.py`
- `tests/test_unit_helpers.py`

Generated, reviewable work product:

- `semantic_alignment_research/ai_first_reply_strategy_001/legacy_v1_draft_audit.json`
- `semantic_alignment_research/ai_first_reply_strategy_001/offline_evaluation.json`
- `semantic_alignment_research/ai_first_reply_strategy_001/offline_evaluation.md`
- `ai_first_reply_strategy_replacement_report.md`

Final generated-artifact hashes:

- legacy audit: `098c5860863d824560a7284628a5807eb31034eeb30fc9fe8dd05a686539944d`
- offline evaluation JSON: `625ac58dc9e2016b7ce2204db47d5bf48e9dc0ff1eacd65539330ff34aedabf8`
- offline evaluation Markdown: `383f36a6c9151e73a8900c90bae10179ee3361133185b5601a6ca85887479452`

No file was deleted wholesale. A pre-existing untracked report,
`reply_strategy_adversarial_review_2026-07-20.md`, was not modified and is not
part of this implementation.

## Activation and rollback requirements

Before any commit or activation:

1. Perform an independent review of this cumulative diff and repeat focused
   safety, receipt and recovery tests.
2. Run a separately authorised non-posting xAI pilot against the adversarial,
   valid and representative recent-candidate sets. Verify strict schema output,
   reviewer independence, quality, latency and actual cost. Do not post or
   consume production budgets.
3. Repeat the stable V1-draft audit and all receipt barriers immediately before
   deployment. Fail closed on any V1 draft or unresolved receipt.
4. Review and include the new source, fixtures, evaluator and generated audit
   artefacts intentionally in the eventual commit; keep runtime state/logs out.
5. Back up and hash the ignored live local configuration. Atomically remove the
   retired `reply_strategy` block and install the fully reviewed
   `ai_first_reply_strategy` block. The current local config has the former and
   lacks the latter, so a restart in its present state is deliberately blocked.
6. Install only the committed files used by the established wrapper, verify
   hashes, then use the separately authorised controlled systemd restart with
   clear receipt barriers and exactly one wrapper/child verification.
7. Confirm startup loads V2, the 610-packet evidence repository and no V1
   strategy; inspect for schema/provider errors without manufacturing a post.

Rollback is restoration of the previous tested Git revision plus the backed-up
local configuration, followed by the normal controlled restart. V1 must not be
kept as a live parallel rollback path.

## Remaining risks

- Real proposer/reviewer quality and provider structured-output behaviour have
  not yet been exercised; this is the principal activation blocker.
- Proposer and reviewer presently use the same configured model in separate
  fresh calls. Independence is contextual, not model-vendor diversity.
- A factual reply normally costs three calls and a revision may reach six,
  increasing conversational latency and cost relative to V1.
- Local lexical retrieval can fail to shortlist evidence expressed with very
  different vocabulary. The result is conservative `no_reply`, not unsupported
  posting, but useful replies may be missed.
- The ignored local config must be migrated atomically; restarting before that
  migration will fail startup rather than silently use V1.

## Production isolation proof

Before and after implementation and testing, `mrsMThatcher.service` remained:

- `ActiveState=active`, `SubState=running`;
- wrapper PID `1595442`;
- Python child PID `1595443`;
- start time `Sun 2026-07-19 23:07:45 BST`;
- `NRestarts=0`.

Exactly one wrapper and one child were present. No service was stopped,
restarted, reloaded or signalled. The ignored local shadow configuration names
the tracked v3 deployment candidate; that file was deterministically rebuilt to
refresh the formatter source hash after the dependency extraction. The running
process loads it once and was not reloaded, so it retains its prior in-memory
lookup and production selection was unchanged. Active enforcement remains
false.

No X post, media upload, provider call, production-state mutation, receipt
creation, analytics mutation, deployment, commit or push occurred.

READY FOR INDEPENDENT REVIEW
