# Priority-0 cumulative transaction remediation — round-b5 rejection and replacement record

## Identity and conclusion boundary

This document records the cumulative transaction-safety implementation, three
rejected frozen candidates, and the five current uncommitted replacement
repairs before a new exact candidate is frozen. It is candidate-side evidence,
not an external release attestation or an independent review.

- release and defect-ledger evidence-cut-off base:
  `4e548b0a5723a1f0c75e9646953b3f92c7db89ad`;
- implementation parent:
  `5eb518b5e7790d6b74559fa5c24fe05c417ef0ce`;
- rejected round-1 candidate commit:
  `f61230fbad22f2b697ed62df325c0e8c17004687`;
- rejected round-1 candidate tree:
  `c73ec605ae168d56f8d11690fb4706c5eb6e7051`;
- rejected round-A candidate commit:
  `e0e6e8cf123c4887042eefc8f72ba7042b34e10b`;
- rejected round-A candidate tree:
  `f7ee598bdbb5065299e8e6efb6ae73cda1b2f131`;
- rejected round-b5 candidate commit:
  `b5e62c0458732c56e4851f010bfa27efb9514567`;
- rejected round-b5 candidate tree:
  `50a14a3c4e8ac423b1b8ecaefd3a3950ef623d84`;
- next replacement candidate commit and tree: pending freeze and deliberately not
  embedded in this self-referential document.

The ledger base fixes the evidence cut-off against which defect status is
interpreted. The implementation parent is the immediate committed tree on
which this cumulative work was built. The ledger therefore continues to mark
post-cut-off repairs as uncommitted and unfixed. Each rejected candidate
identity is historical review evidence only. None of these identities is a
claim that the pending replacement passed the complete suite, the external
gate, independent review, deployment or loaded-process verification.

## Cumulative remediation

### Remote-create authority and media transport

The four public-create lanes use durable, exact transaction authority rather
than process memory as permission to reach transport. Authority is bound to
the canonical source receipt and exact remote payload, consumed only once, and
kept restart-discoverable through unresolved and confirmed phases. Namespace
replacement, disappearance, unsafe types, conflicting bytes and post-bind ABA
fail closed.

Media upload no longer treats an unproved first-endpoint outcome as permission
to repeat the upload through another endpoint. Its exact receipt/fence and
transport handoff retain a durable barrier until the owning public-create
transaction has a proved outcome.

### Persistent exact-retirement ledger under supported writers

Each of the four fixed source-receipt basenames has a strict persistent ledger
and fixed ledger-exchange pathname. Terminal source retirement atomically
advances the ledger from the exact predecessor to a sequence- and hash-chained
completion generation bound to the retired receipt before the final transient
guard can be removed. Same-inode cleanup mutation and cleanup ABA cannot erase
that completion proof along supported lock-authorised writer paths.

The protocol-v2 schema-3 activation audit binds the immutable four-ledger
contract. A missing, malformed or unrelated ledger remains a global barrier
and prevents installation establishment. Under a current activation and the
verifier-bound instance-lock authority, startup may complete only a
structurally exact predecessor/successor crash-left ledger exchange before
establishment validation. Invalid activation never authorises recovery, and
recovering one ledger cannot mask damage to another.

This is a cooperative single-instance crash-safety protocol. It does not claim
protection against arbitrary out-of-protocol same-UID namespace mutation. In
particular, restoring or replacing the ledger with an earlier internally valid
record is not rollback-detected; monotonicity is the sequence/hash-chain
property maintained by supported lock-authorised writers, not a general
filesystem permanence claim. Unrelated journal, media and source-generation
exchanges remain deliberate fail-closed states which may require bounded
operator reconciliation.

### Pause linearisation

A pause observed before durable transport-authority consumption prevents the
request and permits only the exact definite-non-success cleanup. Successful
authority consumption is the one-transaction linearisation point. A pause
published after that point is prospective: it blocks later transactions but
cannot cancel or reclassify the already committed attempt. A later failure is
ambiguous unless a durable confirmation proves success. Scheduler quiescence
therefore means pause acknowledgement plus no durable transaction in flight,
not merely an updated control file.

### Marker, successor and hard-restart barriers

A restart-persistent successor is durably established before a legacy
ambiguity marker can cease to be the sole barrier. Marker observation and
uncertain durability latch the current process, and the durable successor
keeps later literal processes blocked after abrupt loss. Retirement is
ownership-bound and occurs last, after reconciled state is durable.

### Confirmed pending-receipt recovery

Post-confirmation receipt promotion is re-read and checked against exact
preconstructed bytes. Parent-directory synchronisation is retried rather than
inferred from a helper return. Uncertainty retains the process latch, deferred
signal guard and unrelated remote-write barrier. The daemon rechecks delayed
durability recovery on every blocked tick while logging the incident once.

### Historical-context exact-source phases

Historical-context work binds a claimed outbox attempt to exact published
source-receipt bytes, SHA-256 and source-receipt attempt number before
transport. Confirmed and proved-non-success callbacks record the owning outbox
disposition before completed history or source/transport retirement can remove
recovery authority.

Restart reconciliation distinguishes sending, attempting, confirmed and
proved-non-success phases. A source may be retired only when exact durable
outbox/history evidence authorises it. A remote-started or legacy attempting
row is independently a cross-lane barrier even when its source receipt or
journal is lost. Only an explicit pre-remote false phase is eligible for
bounded local-only recovery. Outbox retry and source-receipt attempt ordinals
remain separate domains while exact hash and source-attempt binding are still
required.

### New-install initialisation and state authority

New-install setup publishes a durable initialisation sentinel before its first
data-store write under the instance/initialisation lock ordering. Created
artefacts are pre-registered for rollback, including failures between file
replacement and parent-directory synchronisation. Interrupted setup therefore
remains recognisably fail closed.

Established core state, used history, context history and context outbox are
bounded stable, current-owner, single-link ordinary-file authorities read with
no-follow semantics. Missing established authorities are not silently treated
as empty. When primary state and the latest valid backup are semantically
divergent, restart refuses to guess which is newer; the current schema has no
monotonic generation identifier.

## Rejected round-1 exact-candidate review

The first frozen candidate, commit
`f61230fbad22f2b697ed62df325c0e8c17004687` and tree
`c73ec605ae168d56f8d11690fb4706c5eb6e7051`, was rejected during its first
four-lane adversarial review. The transaction, historical-context and
full-diff lanes reported no additional finding against that exact tree. The
activation/CLI lane found two defects:

1. unknown, duplicate and multiple command-line arguments could fall through
   to operational `main()` and acquire the instance lock instead of failing
   before bootstrap;
2. offline protocol activation did not refuse the interrupted-install sentinel
   `.mrsMThatcher.initialising.json`, so it could publish a false clean
   installation attestation even though runtime operation remained fail
   closed.

The candidate is rejected notwithstanding the three clean lane results. Those
results apply only to the immutable rejected tree and do not qualify its
replacement.

## Round-1 repairs incorporated in the rejected round-A candidate

The round-A candidate validates the complete argument vector before
`production_bootstrap`. A single parser/dispatcher permits only daemon mode
with no argument or exactly one documented CLI mode; positional, unknown,
duplicate and mixed forms return a usage failure without reaching bootstrap or
an operational boundary.

A repair-level adversarial review then found two further CLI boundaries before
the replacement was frozen. First, validation performed only inside the
callable dispatcher would still occur after third-party and application imports
and their possible configuration, logging or filesystem side effects. Second,
an explicit `run_cli` argument vector could disagree with the real process
arguments from which import-time mode and bootstrap state had been derived.
The round-A repair parses a directly executed script's actual argument
vector at the top of the file before those imports or side effects, and the
callable entry point refuses an explicit vector unless it exactly equals
`sys.argv[1:]`.

The round-A offline protocol activator treats
`.mrsMThatcher.initialising.json` as refused state. Four pre-mutation fixtures
cover first activation, pre-ledger activation, current activation and current
audit-only crash state, and prove that refusal leaves the activation audit and
all ledger/exchange artefacts byte-for-byte unchanged.
The repair-level adversarial review found no further activator defect.

Those repairs were incorporated in the now-rejected round-A candidate. Their
focused results remain historical evidence for that exact tree and do not
qualify the next replacement.

## Rejected round-A exact-candidate review

The replacement was frozen as commit
`e0e6e8cf123c4887042eefc8f72ba7042b34e10b` and tree
`f7ee598bdbb5065299e8e6efb6ae73cda1b2f131`, then rejected after all four
adversarial lanes completed:

- the root/full-diff lane found stale `last_verified` pins on the strengthened
  `INV-PROC-001` and `INV-PAUSE-001` registry records;
- the activation/CLI lane found that mutation of `sys.argv` after import could
  desynchronise `run_cli` dispatch from the import-time mode globals;
- the historical-context lane found no duplicate path, but found assurance
  wording which overclaimed rollback-detecting monotonic
  permanence. The actual property is persistent completion evidence with
  sequence/hash-chain monotonicity under supported lock-authorised writers;
  arbitrary out-of-protocol same-UID mutation is outside the threat model and
  restoration of an earlier internally valid record is not detected;
- the transaction lane found no safety escape, but identified one media test
  which was sensitive to immediate inode/ctime identity reuse after raw
  unlink.

The focused review runs were deliberately non-additive: the transaction lane
ran batches of 76, 78 and 22 passing tests; the historical-context lane ran 19
passing tests; and the activation lane ran batches of 173 and 11 passing tests.
These results qualify neither the rejected candidate nor its future
replacement.

## Round-A consolidation incorporated in rejected round-b5

The prior replacement consolidation was incorporated in rejected round-b5
candidate `b5e62c0458732c56e4851f010bfa27efb9514567`:

- CLI mode authority is one immutable `sys.argv[1:]` snapshot captured before
  application imports. `run_cli` rejects both current-process drift and an
  explicit vector which differs from that snapshot, while `argv[0]` cannot
  impersonate a documented mode;
- the interrupted-install activation regression now covers six states: first
  activation, legacy-v1 pair, legacy audit-only, pre-ledger, current and
  current audit-only, requiring pre-mutation refusal in each;
- strengthened `INV-PROC-001` and `INV-PAUSE-001` verification identities are
  `unknown` until an external frozen-candidate gate supplies the exact commit
  and tree. The registry validator now reconstructs known commit/tree objects
  and checks their historical enforcement selectors rather than attributing
  current-worktree evidence to a stale pin;
- ledger and historical-context wording now describes a persistent,
  sequence/hash-chained property only for supported lock-authorised writers.
  It explicitly is not an externally anchored tamper-evident record and does
  not detect arbitrary out-of-protocol rollback or replacement; and
- the allocation-sensitive media regression now retires the first receipt via
  its exact authority instead of raw unlink, avoiding accidental immediate
  inode/ctime identity reuse without weakening the production assertion.

Focused consolidation results are deliberately non-additive:

- combined CLI and activation modules: 179 passed;
- allocation-sensitive media target: 25 of 25 repeated runs passed;
- complete media-receipt module: 107 passed;
- Priority-0 registry module: 33 passed, with registry validation clean;
- defect-ledger module: 33 passed, with defect-ledger validation clean;
- the CLI/activation-scoped Python compilation and diff check passed at their
  repair point;
- all six changed Python files compiled successfully; and
- the global `git diff --check` passed.

These focused results are historical evidence for the consolidation which was
incorporated into the rejected round-b5 tree. They do not qualify the current
uncommitted replacement work. The complete application suite and external
release gate remain deliberately deferred until an unchanged candidate
survives the adversarial review rounds below.

No future replacement commit or tree is anticipated in this report.

## Rejected round-b5 exact-candidate review

The consolidated replacement was frozen as commit
`b5e62c0458732c56e4851f010bfa27efb9514567` and tree
`50a14a3c4e8ac423b1b8ecaefd3a3950ef623d84`. Its exact-tree review found five
defects:

1. when historical-context source receipt paths had disappeared, a sole exact
   source-bound completed outcome could still be locally reconcilable, but the
   global barrier ran before that existing reconciliation path and made it
   unreachable;
2. the runtime-control cache treated metadata as authority, so a same-inode,
   same-size content rewrite with restored `mtime` could remain unseen instead
   of being evaluated as a newly read, content-bound stable snapshot;
3. `MRS_TEST_MODE` could change test-mode authority after import, and the
   operational test entry-point guards were not consistently enforced before
   bootstrap and side effects;
4. configuration and runtime-control JSON parsing accepted duplicate object
   names and non-finite numeric constants rather than rejecting ambiguous
   input; and
5. the offline protocol activator accepted abbreviated option names and
   repeated critical options rather than one exact, unambiguous invocation.

The transaction lane and full-diff lane reported no additional blocker against
that exact tree. Those results, together with the results from the two lanes
which found defects, were all reset when the worktree changed. Zero completed
lane result is carried into the next candidate.

## Current uncommitted five-finding repair batch

The following repairs are implemented in the worktree but remain uncommitted
and have no frozen candidate identity:

- pre-barrier historical-context reconciliation now performs only the existing
  local recovery for one exact source-bound completed outcome; multiple risky
  rows remain fail closed and no remote work is repeated;
- every runtime-control poll now obtains a bounded, no-follow, repeatedly read
  stable byte snapshot, binds its content hash, and treats the cache only as
  the isolated last-known state for fail-closed fallback;
- test-mode authority is captured once before application imports, and both
  direct and callable operational test entry points enforce that immutable
  authority before bootstrap;
- configuration and runtime-control documents now require strict UTF-8 JSON,
  reject duplicate object names at every depth, and reject non-finite or
  overflowing numeric values; and
- the activator now disables argument abbreviation and rejects repeated
  option destinations, including duplicates expressed through aliases.

The observed replacement-batch validation is:

- `python3 -m pytest -q -n 3
  tests/test_fail_safe_bootstrap_and_control.py
  tests/test_production_consistency_incident.py
  tests/test_remote_write_safety_second_restart.py` — 344 passed in 12.00
  seconds;
- `python3 -m pytest -q -n 4 tests/test_priority0_registry.py
  tests/test_defect_ledger.py` — 66 passed in 64.40 seconds;
- registry and defect-ledger validation and render checks: passed;
- changed-Python `py_compile`: passed;
- `git diff --check`: passed; and
- Ruff: passed with only the explicitly excluded pre-existing `E402` and
  `F401` classes.

The two pytest totals are non-additive focused worktree results. They do not
assign a commit or tree identity, carry forward any adversarial lane result or
qualify the still-unfrozen replacement candidate.

## Replacement exact-candidate adversarial review gate

The next replacement application candidate has not yet been frozen. Four
concurrent read-only review lanes must restart from zero and inspect its exact
committed tree after all report, registry and test changes are present:

1. public-create transaction phases, crash/restart, exact retirement and ABA;
2. historical-context outbox, source lineage and reconciliation;
3. activation, installation, pause, recovery and rollback;
4. full-diff claims, registry/ledger bindings and omitted failure boundaries.

If any lane finds a defect and the candidate changes, every lane must restart
against the new exact commit. No clean or no-escape result from any rejected
candidate qualifies the replacement. Two consecutive clean four-lane rounds
on the same exact unchanged tree are required before the expensive external
gate and packaging work, and no earlier partial or pre-report audit is called
final-candidate approval here.

## Focused validation checkpoint

The following non-additive focused batches passed before the rejected candidate
was frozen:

- ledger, journal, media and source retirement: 265 passed;
- restart, bootstrap, pause and remote-outcome safety: 320 passed;
- unit, media, pending-receipt and cross-lane integration: 839 passed;
- historical-context outbox/reply/consistency: 296 passed;
- historical-context semantic gate and source lineage: 44 passed;
- registry and defect-ledger modules: 61 tests passed after the final wording
  correction (60 passed in the full run and the corrected failed selector then
  passed independently);
- changed-Python compilation: passed;
- `git diff --check`: passed;
- registry and ledger JSON/Markdown synchronisation: passed.

The two round-1 repair modules also pass their full focused modules at the
rejected round-A candidate:

- CLI/bootstrap/control validation:
  `tests/test_fail_safe_bootstrap_and_control.py` — 74 passed;
- activation/restart-safety validation:
  `tests/test_remote_write_safety_second_restart.py` — 99 passed;
- changed-Python compilation: passed;
- `git diff --check`: passed.

These suites overlap and their counts must not be summed as a unique total.
The complete isolated application suite has deliberately not run yet. No
external assurance run or packaging run has occurred for the replacement.

## Outstanding qualification work

- freeze and commit the replacement application candidate;
- complete all four adversarial review lanes against that exact commit, and
  repeat all four after any change until all report no finding, without
  carrying forward a clean result from the rejected candidate;
- update and freeze the separately owned release-assurance policy and runner;
- run the complete isolated suite once through the exact external gate;
- produce deterministic attestations and a portable package;
- obtain a separate independent review before any activation decision.

## Isolation record

This remediation, the rejected review rounds and the current uncommitted
five-finding repair batch performed zero production-file or live-state
mutation, service action, X action, provider action, merge, push, deployment,
external-assurance execution or packaging.
