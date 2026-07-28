# Independent read-only review request

This request is intended for a fresh reviewer/session which has not seen the
implementation transcript. It does not state an expected conclusion.

## Inputs

Obtain these files from the release-gate output directory:

- `independent_review_manifest.json`;
- `semantic_attestation.json`;
- `release_gate_run_receipt.json`;
- `release_gate_report.md`;
- `attestation_sha256_inventory.json`.

Obtain these files from the exact candidate commit:

- `production_invariants.json`;
- `production_invariants.schema.json`;
- `PRODUCTION_INVARIANTS.md`;
- `defect_ledger.json`;
- `defect_ledger.schema.json`;
- `DEFECT_LEDGER.md`;
- `REVIEW_COMPLETION_TEMPLATE.md`;
- the changed files listed by the manifest.

First verify every commit, tree, diff and file hash in
`independent_review_manifest.json`. Stop and report an identity failure if the
package, candidate or deployed path differs.

## Review task

Perform a read-only review. Do not modify the candidate or production, deploy,
restart or signal a service, contact a provider, or make an X action.

1. Reconstruct every affected runtime state machine from entry point to remote
   action, durable receipt/state/history transitions, restart handling and
   terminal state. Do not infer behaviour from function names.
2. Challenge the changed-path-to-invariant mapping. Identify affected
   invariants omitted by the package, overly broad mappings and critical
   invariants whose validation is not executable.
3. Inspect the current production repository, deployed entry point/import
   paths, runtime configuration and loaded generated-artifact identities
   read-only. Distinguish candidate commit, production repository commit and
   loaded runtime truth.
4. Reproduce the generated-artifact relationship checks. Identify mixed,
   stale, ambiguous or unbound inputs/outputs which the present architecture
   cannot attest.
5. Identify omitted failure boundaries, especially uncertain remote outcomes,
   crashes between durable transitions, concurrent workers, stale locks,
   pagination tails, cursor provenance and optional-work failures.
6. Verify focused and complete-suite commands, OS-level network denial,
   output hashes and candidate-before/after identity.
7. Reconcile the current defect ledger with Git history and deployment
   evidence. Challenge unsupported `fixed`, `deployed` or `verified` claims.
8. Complete `REVIEW_COMPLETION_TEMPLATE.md`, clearly identifying explicit
   exclusions, residual risks and whether the evidence supports only a
   patch-local, subsystem-level or system-wide conclusion.

Do not treat absence of a failing test as proof of an invariant. Do not use the
unqualified terms “safe” or “complete”. Report unknown facts as unknown and
state what evidence would resolve them.
