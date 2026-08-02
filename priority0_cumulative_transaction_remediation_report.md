# Priority-0 cumulative transaction remediation — rejected-candidate and replacement record

## Identity and conclusion boundary

This document records the cumulative transaction-safety implementation, nine
rejected exact candidates, and the current twenty primary replacements plus
four pre-freeze closure refinements contained in the enclosing post-cut-off
candidate snapshot. It is candidate-side evidence, not an external release
attestation or an independent review.

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
- rejected `bec8355` candidate commit:
  `bec83553c29c42ccc37a2dc9b6e16516dc367efe`;
- rejected `bec8355` candidate tree:
  `0db6a0e679493ab4f8e6316f66e0c6d954739e6d`;
- rejected `baa0602` candidate commit:
  `baa0602bc832848c81a26548b7d0e0bf18ce1b3f`;
- rejected `baa0602` candidate tree:
  `6e050d2a55581504cdbe69cb95c607dda4b3e322`;
- rejected `af701aa` candidate commit:
  `af701aaf08d787e62ae51a72bf36e02f6676d096`;
- rejected `af701aa` candidate tree:
  `573151ab89f04cd268dd63014a31ec0da46f4358`;
- rejected `bb6875f` candidate commit:
  `bb6875fc4684ead6af1b7d2a7be61fe12d91ddf0`;
- rejected `bb6875f` candidate tree:
  `c5e507c69cd500b4a98c5c0b2cc7ef3c65782e83`;
- rejected `59215e9` candidate commit:
  `59215e9dc82584219f1875d5ddffe90cb9646f89`;
- rejected `59215e9` candidate tree:
  `92f520935248c8e649e0d7e98ebdd2e6ad7051f4`;
- rejected `d2ee28e` candidate commit:
  `d2ee28e3989457050e916a8bb79a50e118421798`;
- rejected `d2ee28e` candidate tree:
  `1a7ed2afb38192efe73f7e5ed7baeccd0e2b9c87`;
- enclosing post-cut-off candidate snapshot: status
  `implemented_post_cutoff_candidate`; its exact commit and tree are supplied
  externally and deliberately are not embedded in this self-referential
  document.

The ledger base fixes the evidence cut-off against which defect status is
interpreted. The implementation parent is the immediate committed tree on
which this cumulative work was built. The ledger may therefore continue to
mark a repair unfixed at that historical cut-off. That ledger status is
distinct from the repair's implementation in the enclosing post-cut-off
candidate snapshot. Each rejected candidate identity is historical review
evidence only. None of these identities claims that the enclosing replacement
passed the complete suite, the external gate, independent review, deployment
or loaded-process verification.

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
incorporated into the rejected round-b5 tree. They do not qualify the enclosing
post-cut-off candidate snapshot. The complete application suite and external
release gate remain deliberately deferred until an unchanged candidate
survives the adversarial review rounds below.

No exact identity for the enclosing replacement is anticipated in this report;
it is supplied externally so that this candidate-side document does not refer
to its own commit or tree.

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

## Five-finding repair batch incorporated in rejected bec8355

The following repairs were incorporated in rejected candidate
`bec83553c29c42ccc37a2dc9b6e16516dc367efe`:

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

The observed repair-batch validation was:

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

The two pytest totals are non-additive focused results. They do not carry
forward any adversarial lane result or qualify the enclosing replacement
snapshot.

## Rejected bec8355 exact-candidate review

Candidate `bec83553c29c42ccc37a2dc9b6e16516dc367efe`, tree
`0db6a0e679493ab4f8e6316f66e0c6d954739e6d`, completed two adversarial rounds.
Round 1 was clean in all four lanes: root cross-cutting review,
control/configuration/CLI, historical-context outbox lineage and
reconciliation, and transaction crash/restart/retirement/ABA.

In Round 2:

- the root/claims lane was clean;
- the transaction fault matrix was clean across 58 dynamic probes: 13
  retirement events exercised at the before/after hard-exit positions, for 26
  probes, plus 16 `fsync` sites exercised with before/after errors, for 32
  probes;
- the hostile-input lane was clean across 22 tests; and
- the mutation/test-quality lane found four P2 assurance defects.

The four P2 defects were:

1. the nested duplicate-name fixture passed for the wrong reason: its nested
   object was already schema-invalid, so the test did not prove that strict
   decoding rejected the duplicate name before schema validation;
2. activator duplicate-option coverage was incomplete relative to the report's
   claim that all critical destinations and alias forms were covered;
3. historical registry validation stripped parameter case IDs, so a selector
   could be accepted on the strength of a different historical pytest case;
4. the candidate reports incorrectly described repairs already contained in
   the exact candidate as uncommitted and unfrozen.

The candidate is rejected. Because the four repairs change the candidate
snapshot, the consecutive-clean-round counter is reset to zero; no Round-1 or
Round-2 clean lane is carried forward.

## Rejected baa0602 exact-candidate review

The four replacements above were frozen as candidate
`baa0602bc832848c81a26548b7d0e0bf18ce1b3f`, tree
`6e050d2a55581504cdbe69cb95c607dda4b3e322`, and reviewed concurrently in all
four lanes. The review found eight P2 correctness or assurance defects:

1. confirmed regular and meme schedule recovery depended on the ambient
   process timezone instead of a durable explicit schedule timezone;
2. self-test used a weaker runtime-control reader than production and could
   report success for control input which production rejects;
3. finite-spelling numeric overflow in activation evidence escaped as an
   unclassified numeric error instead of an inactive/refused protocol result;
4. historical defect-ledger selectors discarded parameter case identifiers;
5. historical parameter-specific test evidence references were not held to
   the same exact-selector rule as enforcement tests;
6. the two critical activation-related invariants omitted the comprehensive
   duplicate-option regressions;
7. the configuration invariant did not bind the direct nested duplicate-name
   regression; and
8. two invariant explanations still described the frozen candidate as
   unfrozen.

The transaction lane's non-additive focused batches passed 369, 243 and 10
tests. The hostile-input lane passed 71 tests, and the mutation/test-quality
lane passed the 286-test focused set plus 19 directly selected nodes. Those
results apply only to the rejected tree. Because the repair batch changes the
candidate snapshot, all four lanes and the consecutive-clean-round counter are
again reset to zero.

## Rejected af701aa exact-candidate review

Candidate `af701aaf08d787e62ae51a72bf36e02f6676d096`, tree
`573151ab89f04cd268dd63014a31ec0da46f4358`, completed the first formal
four-lane round. The root/claims lane and mutation/test-quality lane were clean.
The hostile-input lane passed 54 focused tests and found two P2 defects: self-test
did not exercise the complete production local-configuration contract, and
bounded deeply nested activation JSON escaped both activation parsers without a
protocol-specific refusal. The transaction lane passed 1,428 focused tests with
one failure, plus 76 cross-lane tests and 20 new schedule/lifecycle tests; its
sole P2 finding was a stale low-level mutation test fixture which omitted the
now-mandatory permanent retirement ledger.

The candidate is rejected. The three corrections change the candidate snapshot,
so all four lanes and the consecutive-clean-round counter are reset to zero.
No result from `af701aa` is carried forward.

## Rejected bb6875f exact-candidate review

Candidate `bb6875fc4684ead6af1b7d2a7be61fe12d91ddf0`, tree
`c5e507c69cd500b4a98c5c0b2cc7ef3c65782e83`, completed the first formal
four-lane round. The root/claims lane was clean. The transaction lane was clean
across 1,433 focused tests. The hostile-input lane passed 58 targeted tests and
found one P2 defect: production and self-test did not share one bounded stable
local-config namespace reader. The mutation/test-quality lane's six direct
checks passed and found one P2 defect: self-test could validate changed config
bytes against already-applied runtime values instead of immutable source
defaults.

The candidate is rejected. The two corrections change the candidate snapshot,
so all four lanes and the consecutive-clean-round counter are reset to zero.
No result from `bb6875f` is carried forward.

## Rejected 59215e9 exact-candidate review

Candidate `59215e9dc82584219f1875d5ddffe90cb9646f89`, tree
`92f520935248c8e649e0d7e98ebdd2e6ad7051f4`, completed the first formal
four-lane round. The root/claims lane was clean. The transaction lane was clean
across 1,567 distinct focused tests. The hostile-input lane was clean across
173 focused tests. The mutation/test-quality lane found one P2 assurance gap:
the tracked pathname-replacement test stopped at initial descriptor binding and
did not reach the later pathname, repeated-byte and exact-size checks cited by
the configuration invariant. Separate read-only probes showed the implementation
behaved correctly, so this finding concerns durable test evidence rather than a
demonstrated runtime escape.

The candidate is rejected. The added regressions change the candidate snapshot,
so all four lanes and the consecutive-clean-round counter are reset to zero.
No result from `59215e9` is carried forward.

## Rejected d2ee28e exact-candidate review

Candidate `d2ee28e3989457050e916a8bb79a50e118421798`, tree
`1a7ed2afb38192efe73f7e5ed7baeccd0e2b9c87`, completed a clean first formal
round across all four lanes. The unchanged tree's second formal round then
found two issues:

1. exact fractional runtime-control timestamp spellings could be altered by
   binary floating-point conversion before integrality validation, allowing a
   malformed pause value to be interpreted as an expired integer timestamp
   (P1, hostile-input lane); and
2. the production subprocess integration fixture omitted the now-mandatory
   canonical private historical-context history and outbox authorities, so
   five ordinary-lane integration scenarios failed before reaching their
   assertions (P2, transaction lane).

The root/claims and mutation/test-quality lanes were clean in Round 2. The
hostile-input lane passed 167 focused tests before reporting its finding. The
transaction lane passed its 1,571-test standard matrix, while its additional
production-parity cross-section passed two and failed five scenarios. Because
both repairs change the candidate snapshot, the clean first round and every
second-round lane are discarded; the consecutive-clean-round counter is reset
to zero. No result from `d2ee28e` is carried forward.

## Current post-cut-off candidate replacements

The enclosing candidate snapshot retains the four replacements incorporated in
the rejected `baa0602` tree and adds eight further primary replacements. Each
has status `implemented_post_cutoff_candidate`:

- the nested-duplicate regression uses a complete, otherwise schema-valid
  nested object and directly proves strict duplicate-name rejection before
  schema validation;
- activator coverage exercises every value option, both confirmation flags and
  mixed aliases which address one destination;
- historical parameter-specific selectors retain their case identifiers and
  fail closed without exact historical collection evidence; and
- the cumulative report distinguishes evidence-cutoff status from the
  externally identified candidate snapshot;

- current schedule attempts bind their calendar interpretation explicitly,
  rather than inheriting the timezone of a later process;
- self-test uses the same stable, schema-validating runtime-control authority
  as production;
- activation evidence rejects finite-spelling numeric overflow through its
  protocol-specific fail-closed result;
- historical parameter-specific defect-ledger selectors require exact
  collection evidence and retain their case identifiers;
- the same rule applies to historical parameter-specific test evidence in the
  invariant registry;
- the critical activation-related invariants bind the complete duplicate value,
  flag and mixed-alias regression set;
- the configuration invariant binds the direct nested duplicate-name
  regression; and
- invariant explanations describe the enclosing post-cutoff snapshot without
  falsely calling an already frozen reviewed predecessor unfrozen.

The replacement snapshot adds three corrections from the rejected `af701aa`
round:

- self-test now reuses the production local-configuration allowlist, coercion
  and whole-snapshot validator through a pure non-mutating helper;
- both activation parsers impose a deterministic structure-depth bound and
  convert excessive nesting into the same protocol-specific refusal as other
  invalid activation input; and
- the low-level transaction mutation test establishes the permanent retirement
  ledger required by the current protocol before exercising retirement.

It adds two corrections from the rejected `bb6875f` round:

- production bootstrap and self-test now use one bounded, nonblocking,
  non-symlink local-config reader which revalidates descriptor, bytes and final
  pathname identity, classifies low-level read failures consistently, and
  distinguishes clean initial absence from a changed or unsafe namespace; and
- coercion and whole-snapshot validation use the immutable source-default
  configuration rather than already-applied runtime globals, so repeated
  self-test inspection models a fresh production bootstrap without mutation.

It adds one test-assurance correction from the rejected `59215e9` round:

- tracked configuration regressions now reach late pathname replacement and
  disappearance, repeated-read same-inode byte mutation, and the exact byte-cap
  boundary, and those exact nodes are bound to `INV-CONFIG-001`.

It adds two corrections from the rejected `d2ee28e` round:

- runtime-control numeric timestamp lexemes retain exact decimal semantics
  through integrality and range validation, while ordinary local-configuration
  fractional values retain their established floating-point type; and
- the subprocess integration fixture now creates canonical mode-0600 empty
  historical-context history/outbox authorities, and its explicit regular
  receipt fixture uses the same canonical private-file contract.

The pre-freeze review then added four closure refinements to the first schedule
replacement rather than counting them as separate originating findings:

- the canonical current-attempt builder refuses to emit an unbound legacy
  generation;
- legacy schedule records are interpreted in the documented historical
  production calendar, so their validity does not vary with ambient process
  timezone; and
- valid legacy-shaped input cannot be durably introduced through the current
  writer or advanced through the current transport path, while loaders retain
  old generations as fail-closed compatibility records; and
- the live writer accepts only a current `sending` record, and transport
  preparation always performs the exact single-use `sending` to `attempting`
  transition rather than accepting a caller-supplied pre-promoted record.

The enclosing snapshot's exact commit and tree are supplied externally and are
not embedded here. This avoids a self-reference while still making the current
implementation state explicit.

## Replacement exact-candidate adversarial review gate

The current clean-round counter is zero. The same externally identified,
unchanged candidate snapshot must complete two consecutive clean rounds across
four concurrent read-only lanes:

1. root and claims;
2. transaction fault matrix;
3. hostile inputs;
4. mutation and test quality.

If any lane finds a defect and the candidate changes, every lane and the
clean-round counter restart from zero. No clean result from a rejected
candidate qualifies the replacement. Two consecutive clean four-lane rounds
are required before the expensive external gate and packaging work, and no
earlier partial audit is called final-candidate approval here.

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

For the four Round-2 assurance corrections in the enclosing snapshot:

- the three directly affected focused modules passed together: 286 passed;
- the production-invariant registry and defect ledger validated;
- the invariant Markdown projection is synchronised with its JSON registry;
- changed-Python compilation passed; and
- `git diff --check` passed.

For the eight `baa0602` Round-1 corrections and the four calendar/write-boundary
refinements found by the pre-freeze read-only review:

- the complete directly affected transaction, schedule and receipt modules
  passed 808 tests in 63.74 seconds;
- the directly affected remote-outcome and media modules passed 108 tests in
  23.14 seconds;
- the directly affected control, activation, invariant-registry and
  defect-ledger modules passed 331 tests in 172.28 seconds;
- the production-invariant registry and defect ledger validated;
- the invariant Markdown projection is synchronised with its JSON registry;
- changed-Python compilation passed; and
- `git diff --check` passed.

For the three `af701aa` corrections and their enclosing post-repair snapshot:

- the complete directly affected transaction, schedule, receipt and mutation
  authority modules passed 825 tests in 60.70 seconds;
- the directly affected media and remote-outcome modules passed 108 tests in
  34.39 seconds;
- the directly affected control, activation, invariant-registry and
  defect-ledger modules passed 336 tests in 150.68 seconds;
- a separate read-only pre-freeze diff review found no repeatable P1 or P2
  issue;
- the production-invariant registry and defect ledger validated;
- the invariant Markdown projection is synchronised with its JSON registry;
- changed-Python compilation passed; and
- `git diff --check` passed.

These results apply to the current post-repair snapshot only. Earlier counts
from the rejected candidate are not carried forward.

For the two `bb6875f` corrections and the final pre-freeze read-error
classification refinement:

- the local-config bootstrap, self-test and deployment-asset modules passed 138
  tests in 10.69 seconds;
- the complete unit-helper module passed 683 tests in 35.39 seconds;
- the production-invariant and defect-ledger modules passed 71 tests in 117.54
  seconds;
- a separate read-only pre-freeze review found no repeatable P1 or P2 issue
  across nine direct checks and additional bounded-read probes;
- the current production local configuration was inspected read-only and is a
  compatible regular mode-0600 file;
- the production-invariant registry and defect ledger validated;
- the invariant Markdown projection is synchronised with its JSON registry;
- changed-Python compilation passed; and
- `git diff --check` passed.

No clean lane from the rejected candidate is carried forward.

For the `59215e9` test-assurance correction:

- the complete configuration/bootstrap module passed 138 tests in 11.18
  seconds;
- all four new boundary regressions reached the intended late or size-bound
  branch;
- the production-invariant and defect-ledger modules passed 71 tests in 117.37
  seconds;
- a separate read-only pre-freeze review found no repeatable P1 or P2 issue;
- both registries validated and their Markdown projections are synchronised;
- changed-Python compilation passed; and
- `git diff --check` passed.

No clean lane from the rejected candidate is carried forward.

For the two `d2ee28e` corrections:

- the complete control/bootstrap module passed 143 tests in 10.50 seconds;
- the five integration scenarios which exposed the incomplete fixture all
  passed after correction;
- the complete 181-test subprocess integration module passed with four workers
  in 274.77 seconds;
- the production-invariant and defect-ledger modules passed 71 tests in 57.06
  seconds;
- a final read-only pre-freeze review found no repeatable P1 or P2 issue;
- both registries validated and their Markdown projections are synchronised;
- changed-Python compilation passed; and
- `git diff --check` passed.

These are pre-freeze focused results for the replacement snapshot. Both clean
four-lane rounds remain separate gates.
No clean lane from `d2ee28e` is carried forward.

The three earlier focused results are bound to these exact commands, each
with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`:

```text
python3 -m pytest -q tests/test_unit_helpers.py tests/test_confirmed_source_lineage.py tests/test_source_receipt_retirement_integration.py tests/test_pending_receipt_directory_fsync.py
python3 -m pytest -q tests/test_media_upload_transaction_integration.py tests/test_x_write_outcome_conservatism.py
python3 -m pytest -q tests/test_fail_safe_bootstrap_and_control.py tests/test_remote_write_safety_second_restart.py tests/test_priority0_registry.py tests/test_defect_ledger.py
```

The first command was run with
`tests/test_transaction_mutation_authority.py` prepended for the current
post-repair checkpoint, producing the recorded 825-test result.

The `d2ee28e` replacement validation used these additional exact commands:

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_fail_safe_bootstrap_and_control.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q -p xdist.plugin -n 4 tests/test_integration_harness.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q -p xdist.plugin -n 4 tests/test_priority0_registry.py tests/test_defect_ledger.py
```

These focused totals overlap and must not be summed as a unique application
test count. The replacement has not yet completed either exact-candidate
four-lane round.

## Outstanding qualification work

- complete two consecutive clean four-lane rounds against the externally
  identified enclosing candidate snapshot, restarting every lane and the
  clean-round counter after any candidate change;
- qualify the separately owned release-assurance policy and runner;
- run the complete isolated suite once through the exact external gate;
- produce deterministic attestations and a portable package;
- obtain a separate independent review before any activation decision.

## Isolation record

This remediation, the rejected review rounds and the current replacement
batches performed zero production-file or live-state mutation, service action,
X action, provider action, merge, push, deployment, external-assurance
execution or packaging.
