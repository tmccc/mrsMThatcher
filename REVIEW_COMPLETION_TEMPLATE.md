# Review completion record

Use this template for the exact frozen candidate named below. A review is not
complete merely because a patch-specific test passes. Delete no section; write
`not reviewed` with a reason where evidence is unavailable.

## Frozen identity

- Candidate commit:
- Candidate Git tree:
- Base commit:
- Final diff SHA-256:
- Invariant-registry SHA-256:
- Defect-ledger SHA-256:
- Semantic-attestation SHA-256:
- Worktree clean at start and end:
- Candidate file hashes unchanged during review:
- Ledger evidence cut-off and supplied-base equality:
- Previous candidate identity, if this is a follow-up:

## Deployed and runtime identity

- Production repository path and commit reviewed:
- Deployed entry-point path and SHA-256 reviewed:
- Imported runtime module paths and SHA-256 values reviewed:
- Runtime-consumed generated generation(s) reviewed:
- Loaded source/policy/manifest identities reviewed:
- Service process identity and start time observed:
- Difference between candidate, repository and loaded runtime:

## Scope

- Files included:
- Runtime states included:
- Changed paths:
- Affected invariant IDs:
- Explicit exclusions:
- Why each exclusion cannot hide a failure in the stated conclusion:
- Conclusion scope (choose exactly one):
  - patch-local;
  - subsystem-level;
  - system-wide.

Do not select `system-wide` unless every production invariant has current
executable evidence and the deployed paths and loaded generation were reviewed.

## State-machine reconstruction

- Entry points traced:
- Remote-write boundaries traced:
- Durable state transitions reconstructed:
- Resume/restart states reconstructed:
- Receipt creation/confirmation/retirement order:
- Interaction with optional auxiliary work:
- Pagination/cursor and exact-history preservation:

## Failure-boundary injection

Record the injected boundary, initial state, expected durable state, observed
state and external actions for each applicable point.

| Boundary | Initial state | Expected state | Observed state | External actions | Result |
|---|---|---|---|---:|---|
| Before remote request | | | | | |
| Request may have left, response absent | | | | | |
| Remote success before receipt write | | | | | |
| Receipt write before protected-state save | | | | | |
| Protected-state save before receipt retirement | | | | | |
| Optional work creation/processing | | | | | |
| Restart at each durable boundary | | | | | |
| Concurrent worker or stale lock | | | | | |

Additional failure boundaries injected:

## Validation evidence

### Focused validation

- Exact commands:
- Changed Python files compiled:
- `git diff --check` result:
- Passed/failed/skipped/error counts:
- Output SHA-256 values:
- Network-denial mechanism:
- Live path isolation:

### Complete isolated suite

- Exact command:
- Candidate checkout identity:
- Worker count:
- Passed:
- Failed:
- Errors:
- Skipped:
- Warnings:
- Duration:
- Output SHA-256:
- OS-level network isolation:
- Candidate unchanged after suite:
- Every skipped node/reason/invariant disposition reviewed:
- Warning categories, fingerprints and counts reviewed:
- Structured pytest sidecar SHA-256:
- Undeclared project import violations:
- Attested dependency origins and versions:

### Generated-artifact relationships

- Runtime loaders inspected:
- Input/source-code/policy hashes checked:
- Output hashes checked:
- Counts and projections reproduced:
- Relationship validators:
- Direct companion hash/count/policy/generation checks:
- Historical/build-time classifications and loader evidence:
- Advisory pin classifications and code evidence:
- Unreproduced or non-atomic relationships:

## Independent-review findings

- Reviewer/session identity:
- Reviewer had no implementation transcript:
- Manifest identity verified:
- Omitted invariants or failure boundaries:
- Findings:
- Disposition of each finding:

## Open risks and acceptance

| Risk | Severity | Evidence | Accepted by | Rationale | Expiry/follow-up |
|---|---|---|---|---|---|
| | | | | | |

## Rollback implications

- Files/state requiring rollback:
- Whether data written by the candidate is backward compatible:
- Receipt/history implications:
- Conditions which require rollback:
- Read-only checks proving rollback identity:

## Conclusion

- Scope:
- Evidence-backed conclusion:
- Items explicitly not established:
- Reviewer:
- Review date:

Avoid unqualified claims that the candidate or entire system is “safe” or
“complete”.
