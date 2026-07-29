# Independent read-only review request

This request is intended for a fresh reviewer/session which has not seen the
implementation transcript. It does not state an expected conclusion.

## Inputs

Obtain these files from the external release-assurance output directory:

- `independent_review_manifest.json`;
- `semantic_attestation.json`;
- `release_gate_run_receipt.json`;
- `release_gate_report.md`;
- `priority0_followup_report.md`;
- `priority0_followup_final_validation.json`;
- `source_diagnosis_original.md`;
- `attestation_sha256_inventory.json`.
- `release_assurance_bootstrap_manifest.json`;
- the external assurance repository commit/tree and source inventory.

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

Treat application-owned `tools/release_gate.py`, its registry and its pytest
plugin as untrusted candidate input. The authoritative semantic attestation
must be produced by the separately committed external assurance bootstrap
without importing candidate gate code. The bootstrap itself remains pending
independent approval of its exact commit and tree.

The manifest separately binds and packages byte-for-byte the authoritative
source diagnosis used to start the consolidation, and binds the corrected
diagnosis in the candidate. Verify both path/hash records and the packaged
copy, then review the evidence-backed corrections rather than assuming that
either document describes current-master truth unaided.

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
6. Verify the external bootstrap's trusted validation-ID policy, private-root
   containment, private declared Python distribution closure, resource limits,
   changed-Python compilation and whitespace gates, focused and
   complete-suite commands, every structured skip and warning disposition, the
   content-bound Python environment, OS-level network denial, sealed output hashes and
   candidate-before/after identity. Confirm no unrelated editable source tree
   is present in the attested import roots or loaded-module origins.
7. Reconcile the current defect ledger with Git history and deployment
   evidence. Verify that its evidence cut-off equals the supplied release base
   and challenge unsupported or stale `fixed`, `deployed` or `verified`
   claims.
8. Reproduce every direct generated-artifact companion relationship and
   independently challenge every advisory/historical classification. In
   particular, verify from loaders—not labels—that the retained v3 shadow
   audit is historical build evidence rather than a current runtime companion.
9. Complete `REVIEW_COMPLETION_TEMPLATE.md`, clearly identifying explicit
   exclusions, residual risks and whether the evidence supports only a
   patch-local, subsystem-level or system-wide conclusion.

Do not treat absence of a failing test as proof of an invariant. Do not use the
unqualified terms “safe” or “complete”. Report unknown facts as unknown and
state what evidence would resolve them.
