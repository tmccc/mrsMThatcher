# AI-first reply strategy canonical-resolution and qualification report

Date: 21 July 2026
Host: `big-nas-2`
Project: `/disks/disk1/etc/mrsMThatcher`

## Executive verdict

The repaired AI-first reply strategy is ready for a separately authorised activation procedure. The final source resolves canonical quotations without inherited-context contamination, restricts direct quotation answers to the correct research packet, independently audits claim-free prose, prevents revision laundering, and fails closed on unsupported facts or malformed model output.

The exact final prompt set was exercised with ten repeated civil-challenge cases. It produced the same approve/no-reply decision for both repetitions of all five quotations, with no malformed stage, unsupported factual approval, provider ambiguity, X call or production write. Six replies were approved and four conservatively became `no_reply`.

This task did not commit, push, deploy, restart the bot or alter production state.

## Architecture under review

The conversational path remains:

1. resolve any canonical quotation explicitly present in the incoming, quoted or bounded parent context;
2. obtain a structured proposer draft;
3. independently inventory factual claims in apparently claim-free opinion or humour;
4. retrieve and adjudicate local evidence only for factual claims;
5. obtain a fresh independent reviewer verdict;
6. permit posting only after explicit approval and deterministic operational checks.

Operational limits remain outside model control: receipt barriers, ambiguous-write handling, duplicate targets, terminal threads, clarification limits, author and global caps, length, links, mentions, exact quotation matching, evidence-reference integrity, schema validity and call limits.

## Confirmed defects repaired

### Canonical quotation resolution

`reply_evidence.py` now resolves quotations using evidence-bearing context precedence:

- a unique explicit quotation in the incoming contribution outranks conflicting inherited account text;
- a third-party quoted canonical quotation can be resolved;
- bounded quoted/account-parent fallback remains available;
- ambiguous or absent matches remain unresolved;
- all 610 eligible canonical quotations still resolve from their complete text.

For a resolved direct factual quotation question, evidence candidates are restricted to the target packet. An unrelated packet cannot support an answer merely through lexical overlap.

### Claim and reviewer integrity

`reply_strategy.py` now:

- uses a fresh claim auditor for ostensibly claim-free `opinion_or_principle` and `light_humour` drafts;
- requires reviewer sentence inventories and world-claim checks to agree;
- treats meaning and attribution as an explicit world-claim category;
- prevents a revision from retaining a previously identified factual clause while silently dropping it from the claim inventory;
- treats substantive auditor/reviewer contradictions as non-retryable safety outcomes;
- allows at most one proposer/reviewer revision;
- pins every prompt and persisted-draft protocol version.

Final versions are:

- strategy: `ai-first-reply-v3`;
- proposer: `ai-first-proposer-v13`;
- evidence: `claim-evidence-entailment-v6`;
- claim auditor: `claim-inventory-auditor-v5`;
- reviewer: `independent-reply-reviewer-v11`;
- matrix runner: `ai-first-quote-matrix-v13`;
- provider pilot: `ai-first-provider-pilot-v13`.

### Civil principle challenges

The initial qualification showed that the model often declined civil disagreement merely because it was non-factual, or rewrote a value judgement as an unsupported prediction. The final contract now requires a reply, when one is possible, to name the disputed principle and express a recommendation or standard without smuggling history, behaviour or outcomes into a claim-free sentence.

`repeated argument` is permitted only when bounded thread context explicitly shows that this account already answered the same challenge. It cannot be inferred from disagreement, a quoted passage or unrelated recent replies.

The claim auditor and reviewer now explicitly recognise:

- historical noun-phrase premises such as a nation's record of doing something;
- action-bearing relative clauses such as a programme that a conference endorsed;
- British spelling requirements, including `defence` rather than `defense`.

The auditor contract copies factual clauses verbatim. A pilot exposed and confirmed that paraphrasing an embedded clause conflicted with the strict validator; the instruction was corrected and the final run had zero malformed stages.

## Provider evidence

### Broad qualification before final prompt hardening

`provider_pilot_principle_qualification_20260721_v2` completed 80/80 cases using Grok 4.3:

- approved: 19;
- `no_reply`: 61;
- deterministic evaluation passes: 67;
- operational fail-closed outcomes: 12;
- known cost: US$1.06829860;
- ambiguous exposure: US$0.00;
- model calls: 220.

All five wrong-speaker controls correctly identified Thatcher and used only the target quotation packet. All five unsupported-allegation controls and all five quoted-context-distraction controls returned `no_reply`. Manual review of all 19 approvals found no unsafe reply.

### Grok 4.5 assessment

`provider_pilot_repair_20260721_grok45_v2` was not suitable for production:

- completed: 64/120;
- known cost: US$3.21184560;
- ambiguous exposure: US$1.58498600;
- latency p50/p95/max: 60.156 / 150.605 / 154.904 seconds.

Grok 4.3 remains configured because it completed the equivalent post-fix run 120/120 with materially lower latency and cost. No silent model substitution was made.

### Exact-source repeated challenge smoke

The same five quotation/challenge pairs were run twice after each correction. Input hash remained:

`2c12d693ba30781640122dc3df4c9b6a85f3a8d57256a5e7de69500786591833`

Progression:

| Run | Approved | No reply | Pair decision agreement | Operational failures | Known cost |
|---|---:|---:|---:|---:|---:|
| v1 | 3 | 7 | 2/5 | 0 | US$0.18171585 |
| v2 | 9 | 1 | 4/5 | 0 | US$0.14266085 |
| v3 | 6 | 4 | 5/5 | 2 | US$0.16258175 |
| v4 final | 6 | 4 | 5/5 | 0 | US$0.15673175 |

Final v4 also recorded:

- cases passed: 10/10;
- ambiguous exposure: US$0.00;
- model calls: 36;
- latency p50/p95/max: 35.115 / 59.249 / 68.941 seconds;
- X calls: 0;
- production-state writes: 0;
- evaluation SHA-256: `9868d67bdaccb4905921dc32eac559c1966723262b65f59611e37bf9548080af`;
- cost-ledger SHA-256: `abf8d4b8666ac3ef265e57c56cd542f0a20cedc8a71785a9b0595d6d8a561152`.

The approved wording varied naturally between repeated model calls; repeatability here means stable safe disposition and protocol behaviour, not byte-identical prose.

## Reproducible full qualification input

The final 80-case qualification was prepared twice without a provider call. All hashes were identical:

- run manifest: `75524231d7f286b452b20afdcb9617b2e4298c79911bd889959a4cd692981785`;
- paid sample: `d0e63854d1a9fe2d91612304bde1ee2b51b6e645b5f73bd008d542cb5093b2c8`;
- offline validation: `e6bb393306b1b4cd92cdccd3349007d49144f4bb1b58656c3c3c995f6574ffca`;
- cost preflight: `421cc35e862d054227be341e4e8a8e4b97c3bb7d92dd7298816bcc56c5db70e7`.

No paid execution of that final 80-case input was necessary after the focused repeated smoke. The last changes only clarified civil-challenge wording, embedded-premise auditing and British spelling; the broad safety controls had already been exercised, and the affected offline suites were rerun.

## Cost ledger

Across all AI-first provider-pilot directories in this run family:

- known spend: US$19.87352835;
- ambiguous exposure: US$2.17266600;
- combined conservative exposure: US$22.04619435.

The four focused smoke runs added US$0.64369020 known spend and no ambiguous exposure. No further provider call is planned.

## Runtime configuration reviewed

The ignored local configuration remains fail-closed with:

- proposer/evidence/reviewer model: `grok-4.3`;
- maximum model calls per logical reply: 6;
- maximum revisions: 1;
- maximum invalid-response retries: 1;
- proposer/evidence/reviewer timeout: 60 seconds per call;
- fail closed: true.

The final smoke exercised replies using three calls and conservative paths using up to six calls.

## Validation

Final commands and outcomes:

- focused reply and matrix tests: 179 passed;
- affected AI-first, runtime-isolation, historical-context and helper tests: 676 passed;
- integration harness: 171 passed, 1 expected skip;
- targeted `py_compile`: passed;
- Ruff on all changed Python source and tests, ignoring the repository's intentional `E402` bootstrap and existing `E702` style exceptions: passed;
- `git diff --check`: passed.

The unrelated full repository suite was not rerun during this final prompt-only correction cycle. A full-suite run from the exact curated commit remains an appropriate commit/deployment precondition.

## Production isolation

Before and after all work:

- service state: active/running;
- wrapper PID: 695911;
- Python child PID: 695912;
- start time: 20 July 2026 19:01:40 BST;
- restart count: 0;
- cgroup: `/user.slice/user-1000.slice/user@1000.service/app.slice/mrsMThatcher.service`.

Read-only barrier audit at completion:

- pending AI-first drafts: 0;
- legacy pending reply drafts: 0;
- regular-post receipt: absent;
- meme-post receipt: absent;
- historical-context reply receipt: absent;
- confirmed conversational-reply receipt: absent.

There was no X post, media upload, service signal, restart, production-state write, receipt mutation, analytics change, commit, push or deployment.

## Source hashes

- `reply_strategy.py`: `9faba7f64b0d88b849f32d23e882bfae8e5f154ecc59ccea1b465d80cf0ed290`;
- `reply_evidence.py`: `d970feebcccb19acc734f807b2d46941bfbe364540f77a298bd21f3eee578ec9`;
- `tools/pilot_ai_first_reply_strategy.py`: `7c677d236b76263f8c783f1208ca3613953a36519a11ae632050539cd2ecb0fc`;
- `tools/evaluate_ai_first_reply_quote_matrix.py`: `3c77d696c15e191891188e4e27f64b20966ce6c24b275508e7e5810252346d93`;
- `tools/run_ai_first_reply_principle_qualification.py`: `de0d4df5fef6ba58c94444a20e0020fad26e980744d0c77c6e7c9a3e94267c9e`;
- `tools/run_ai_first_reply_repair_pilot.py`: `f1b72a10f458398fac1058aa72dcb14ced51e463bb53b4677312af2998ceedae`.

## Working-tree scope

Modified tracked files:

- `reply_evidence.py`;
- `reply_strategy.py`;
- `tests/fake_api_server.py`;
- `tests/fixtures/ai_first_reply_provider_revision_cases.json`;
- `tests/test_ai_first_reply_provider_pilot.py`;
- `tests/test_integration_harness.py`;
- `tests/test_reply_strategy.py`;
- `tests/test_unit_helpers.py`;
- `tools/pilot_ai_first_reply_strategy.py`.

New maintained source/test/report candidates:

- `tools/evaluate_ai_first_reply_quote_matrix.py`;
- `tools/run_ai_first_reply_principle_qualification.py`;
- `tools/run_ai_first_reply_repair_pilot.py`;
- `tests/test_ai_first_reply_quote_matrix.py`;
- `ai_first_reply_quote_matrix_evaluation_report.md`;
- this report;
- reviewed aggregate manifests, samples, evaluations, hashes and reports under `semantic_alignment_research/ai_first_reply_strategy_001/`.

Raw provider responses, transient execution state, superseded pilot directories and mutable ledgers should be curated according to repository policy before commit. They are audit evidence, but they should not be staged indiscriminately or allowed to expose unnecessary real-user content.

## Remaining risks and activation requirements

Residual risks are bounded rather than absent:

- provider wording is inherently non-deterministic, although the final duplicate decisions agreed 5/5;
- fail-closed provider or schema failures can reduce reply volume;
- a six-call conservative path can be slower than the usual three-call path;
- the final exact prompt set has a focused ten-case paid smoke rather than another paid 80-case run;
- raw pilot artefacts require deliberate curation before version control.

Before activation:

1. curate the commit and exclude secrets, raw caches and transient ledgers;
2. run the complete offline suite and `git diff --check` from the exact tree to be committed;
3. commit and push, then verify local HEAD equals upstream;
4. recheck all posting receipts, ambiguity barriers and legacy drafts;
5. preserve a rollback revision and local-configuration backup;
6. deploy atomically and restart only `mrsMThatcher.service` under a separately authorised controlled procedure;
7. verify one wrapper/child pair, the configured six-call cap, V3-only execution and healthy startup logs without manufacturing an X post.

READY FOR SEPARATELY AUTHORISED ACTIVATION
