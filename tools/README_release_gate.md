# Frozen candidate release gate

`tools/release_gate.py` is a read-only validation and attestation tool. It
cannot deploy, restart a service or mutate bot state.

## Network-isolation preflight

```bash
python3 tools/release_gate.py network-preflight \
  --repo . \
  --production-root /disks/disk1/etc/mrsMThatcher
```

This must report Linux user, network, mount and PID namespaces with loopback
available, external routes denied, a private `/proc`, and the production root
bind-mounted read-only. The same containment encloses pytest and every
descendant. A Python socket monkeypatch is not accepted as release-candidate
isolation.

## Development validation

Development mode can inspect an uncommitted candidate, but cannot run the
complete release suite or pass qualified release-candidate validation:

```bash
python3 tools/release_gate.py run \
  --repo . \
  --base <explicit-base-commit> \
  --development-dry-run \
  --output-dir /path/outside/the/worktree/development-attestation
```

## Frozen candidate gate

Commit the candidate first, then run:

```bash
python3 tools/release_gate.py run \
  --repo /path/to/clean-candidate-worktree \
  --base <explicit-base-commit> \
  --candidate <exact-candidate-commit> \
  --production-root /disks/disk1/etc/mrsMThatcher \
  --source-diagnosis-path /absolute/path/to/authoritative-diagnosis.md \
  --source-diagnosis-sha256 <verified-sha256> \
  --output-dir /path/outside/the/worktree/release-attestation \
  --scratch-root /disks/disk1/research \
  --workers 4 \
  --full-suite
```

Repeat `--procedural-note "..."` when a run receipt must preserve an earlier
preflight-only failure or another non-semantic execution fact. These notes do
not enter the deterministic semantic attestation.

The gate:

1. holds an exclusive lock in the shared Git common directory;
2. rejects modified and untracked candidate files;
3. maps the exact base-to-candidate diff to the invariant registry;
4. rejects uncovered code/control paths (unmapped documentation and non-code
   data are the only documented exceptions);
5. records discovered and registry-declared runtime/generated-artifact,
   schema and policy hashes;
6. creates a clean detached checkout of the exact commit;
7. runs de-duplicated focused and relationship validation in that checkout;
8. runs the complete parallel suite once in the same route-isolated,
   production-read-only, PID-isolated containment;
9. verifies the candidate tree and all relevant hashes again; and
10. writes deterministic semantic evidence separately from volatile run
    metadata.

Outputs are written outside the candidate:

- `semantic_attestation.json`;
- `release_gate_run_receipt.json`;
- `release_gate_report.md`;
- `independent_review_manifest.json`;
- `attestation_sha256_inventory.json`;
- focused/full command output and JUnit XML under `validation/`.

The semantic attestation deliberately excludes timestamps, host identity,
duration and raw test-output hashes. The run receipt binds raw output and JUnit
hashes, and the final inventory hashes those evidence files. A conclusion is
always labelled development-only, patch-local release candidate, or
subsystem-level; the result is not deployment authorization. Activation-time
deployed checks remain recorded as unmet.
