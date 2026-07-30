# Priority-0 transaction and ledger-assurance remediation

Date: 2026-07-30

## Scope and identity boundary

This is a replacement, unactivated candidate built from the exact base:

- base commit: `02f9d2be49b9a3f5b5502481a995c8ececc80896`;
- base tree: `d81ce4cf2675e283ba6e67f0e5418054d3a557dd`;
- recorded production commit: `be882e8121a7b4348a57b61b1cf526401a36f5c0`;
- production activation: not performed;
- deployment, merge and push: not performed.

The final application candidate commit and tree are deliberately supplied by
the external frozen-candidate attestation. Embedding the final commit in a file
inside that commit would be self-referential.

## Independent-review findings reproduced

The separate independent review withheld approval for two reasons.

1. The regular quote/image lane and the daily-meme lane first persisted a
   durable post receipt only after simulated X acceptance. Separate-process
   hard-death tests consequently admitted regular IDs `950001` then `950002`,
   and meme IDs `970001` then `970002`, across restart.
2. The archived external assurance run omitted `ledger_validate`. Its release
   base differed from the ledger evidence cut-off, while its command policy
   also omitted the required `--release-base`.

No live duplicate post is inferred from the synthetic reproductions. The
archived patch-local qualification is treated as invalid; production was not
activated by it.

## Runtime remediation

`mrsMThatcher2.py` now uses one durable single-use main-post attempt for both
regular and meme transactions:

1. `sending` is written and fsynced before an X create can be transmitted;
2. `sending` is atomically consumed into `attempting`;
3. the record binds the exact payload, selected identity and attempt digest;
4. a regular attempt also binds the exact post-cycle quotation/image histories
   and recovery delays;
5. only a matching confirmed response promotes the same record to the
   established backward-compatible confirmed receipt;
6. an uncertain outcome remains durable and blocks every remote-write lane
   pending manual reconciliation;
7. only an explicit non-successful `4xx` response from `POST /2/tweets` is
   treated as definitely not sent;
8. confirmed recovery persists protected state and the context disposition
   before retiring the active main receipt.

Startup with an uncertain attempt remains alive and paused. It does not create
another main post and does not enter a wrapper crash loop.

## Hard-process-loss coverage

The focused tests cover both lanes at these boundaries:

- before the remote request;
- after possible remote acceptance but before a local response;
- after response but before confirmed-receipt promotion;
- after confirmed promotion but before protected-state save;
- after protected-state save but before context-disposition durability;
- after context-disposition durability but before receipt retirement;
- before and after atomic replacement;
- repeated restart from every durable state.

The resulting safety property is at-most-once remote creation. The deliberate
availability trade-off is that a genuinely uncertain attempt requires manual
reconciliation; it is never retried automatically.

## Registry and ledger semantics

The two documents intentionally answer different identity-bound questions:

- `production_invariants.json` describes the frozen remediation candidate and
  marks the regular and meme transaction invariants implemented and verified
  by candidate tests.
- `defect_ledger.json` is evidence-cut-off-bound to `02f9d2be…`. It records
  `DEF-0024` and `DEF-0025` as active and unfixed at that base. A later
  candidate cannot silently rewrite base truth.

The ledger must be regenerated after a merge changes either defect status. It
also records `DEF-0026`: the application ledger cannot itself certify the
external assurance repair.

## External assurance remediation

The separately committed release-assurance bootstrap candidate:

- injects `registry_validate` and `ledger_validate` into every validation plan;
- supplies the exact application release base to
  `ledger_validate --release-base`;
- independently verifies that the ledger evidence cut-off equals that base;
- binds every completed attempt record to the application base/tree,
  candidate/tree, assurance commit/tree, canonical diff, trusted policy,
  registry, ledger and expected test inventory;
- rejects result shopping or completed-record tampering;
- includes the assurance commit closure in the tree-exact review package.

That gate remains a bootstrap candidate pending another independent review. A
successful full run is patch-local release evidence, not production activation
or loaded-process identity.

## Focused validation completed before final freeze

- transaction unit helpers: 605 passed;
- production consistency and fail-safe tests: 100 passed;
- focused hard-death boundary set: 33 passed;
- Priority-0 CLI import regression tests: 3 passed;
- application registry, ledger and release-gate focused tests: 192 passed;
- external assurance repository tests: 203 passed;
- Python compilation: passed;
- whitespace validation: passed.

The complete application suite is intentionally deferred to one exact run from
the final frozen application and assurance commits. Its authoritative totals
belong in the external run receipt rather than this self-contained candidate
document.

## Residual blockers

- A new separate reviewer must review both frozen trees and the exact external
  attestation before any merge or deployment decision.
- Production remains on the recorded baseline and therefore remains exposed to
  the original hard-death window until a separately authorised deployment.
- An ambiguous attempt after deployment intentionally pauses posting until an
  operator determines the remote outcome.
- Loaded-process identity and production activation are outside this candidate.

No production file, live state, service, X account or provider was mutated.
