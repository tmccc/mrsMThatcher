# Priority-0 cumulative transaction remediation — pre-freeze record

## Identity and conclusion boundary

This document records the cumulative transaction-safety implementation before
the exact candidate is frozen. It is candidate-side evidence, not an external
release attestation or an independent review.

- release and defect-ledger evidence-cut-off base:
  `4e548b0a5723a1f0c75e9646953b3f92c7db89ad`;
- implementation parent:
  `5eb518b5e7790d6b74559fa5c24fe05c417ef0ce`;
- final candidate commit and tree: supplied externally after the freeze and
  deliberately not embedded in this self-referential document.

The ledger base fixes the evidence cut-off against which defect status is
interpreted. The implementation parent is the immediate committed tree on
which this cumulative working diff was built. The ledger therefore continues
to mark post-cut-off repairs as uncommitted and unfixed. Neither identity is a
claim that the pending candidate passed the complete suite, the external gate,
independent review, deployment or loaded-process verification.

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

### Permanent exact-retirement ledger

Each of the four fixed source-receipt basenames has a strict permanent ledger
and fixed ledger-exchange pathname. Terminal source retirement atomically
advances the ledger from the exact predecessor to a monotonic, hash-chained
completion generation bound to the retired receipt before the final transient
guard can be removed. Same-inode cleanup mutation and cleanup ABA cannot erase
that completion proof.

The protocol-v2 schema-3 activation audit binds the immutable four-ledger
contract. A missing, malformed or unrelated ledger remains a global barrier
and prevents installation establishment. Under a current activation and the
verifier-bound instance-lock authority, startup may complete only a
structurally exact predecessor/successor crash-left ledger exchange before
establishment validation. Invalid activation never authorises recovery, and
recovering one ledger cannot mask damage to another.

This is a cooperative single-instance crash-safety protocol. It does not claim
protection against arbitrary hostile same-UID namespace mutation. Unrelated
journal, media and source-generation exchanges remain deliberate fail-closed
states which may require bounded operator reconciliation.

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

## Exact-candidate adversarial review gate

The final application candidate has not yet been frozen. Four concurrent
read-only review lanes must inspect the exact committed tree after all report,
registry and test changes are present:

1. public-create transaction phases, crash/restart, exact retirement and ABA;
2. historical-context outbox, source lineage and reconciliation;
3. activation, installation, pause, recovery and rollback;
4. full-diff claims, registry/ledger bindings and omitted failure boundaries.

If any lane finds a defect and the candidate changes, every lane must restart
against the new exact commit. No earlier partial or pre-report audit is called
final-candidate approval here.

## Focused validation checkpoint

The following non-additive focused batches pass on the current pre-freeze tree:

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

These suites overlap and their counts must not be summed as a unique total.
The complete isolated application suite has deliberately not run yet.

## Outstanding qualification work

- freeze and commit the exact application candidate;
- complete all four adversarial review lanes against that exact commit, and
  repeat all four after any change until all report no finding;
- update and freeze the separately owned release-assurance policy and runner;
- run the complete isolated suite once through the exact external gate;
- produce deterministic attestations and a portable package;
- obtain a separate independent review before any activation decision.

## Isolation record

This pre-freeze remediation performed zero production-file or live-state
mutation, service action, X action, provider action, merge, push or deployment.
