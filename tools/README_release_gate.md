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
available, external routes denied, and a private `/proc`. The production root,
candidate checkout, shared Git metadata and every resolved validation-
dependency root are bind-mounted read-only before all effective capabilities
are dropped and `no_new_privs` is set. The preflight proves that neither a
normal write nor a remount to read-write succeeds after that drop. The same
containment encloses pytest and every descendant. A Python socket monkeypatch
is not accepted as release-candidate isolation.

Validation uses a credential/proxy-free environment. The gate content-hashes
the Python executable and every existing import root in the active validation
environment, including resolved content behind package symlinks. It starts
Python with `-I -S`, excludes the mutable implementation worktree from the
toolchain, supplies only the attested roots plus the detached candidate, and
checks the exact origins of pytest, xdist and the registry schema backend.
The installed user-service unit directory is read-only and the user D-Bus and
systemd-private sockets are masked inside validation. The complete toolchain
identity is recomputed after validation.

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
2. accepts the invariant registry and defect ledger only as tracked,
   non-symlink files inside the candidate, captures their bytes once and
   rejects any later identity change;
3. rejects modified/untracked files and non-default Git index flags;
4. maps the exact base-to-candidate diff to the invariant registry;
5. rejects uncovered code/control paths (unmapped documentation and non-code
   data are the only documented exceptions);
6. records every registry artifact declaration (including absent ephemeral
   state), discovered runtime/generated-artifact hashes, recomputed source-file
   pins, schema and policy hashes;
7. creates a clean detached checkout of the exact commit;
8. runs de-duplicated focused and relationship validation in that checkout,
   reverifying its Git state, relevant hashes and loader relationships after
   every command;
9. runs the complete parallel suite once in the same route-isolated,
   immutable-candidate/toolchain, production-read-only, PID-isolated
   containment;
10. stages untrusted JUnit output outside the attestation, mounts the
    identity-bound attestation output read-only inside validation, and copies
    verified ordinary files into it through pre-opened directory descriptors;
11. seals each completed command's output and JUnit evidence read-only against
    later commands and rechecks all earlier evidence hashes;
12. preserves and rechecks the exact authoritative diagnosis bytes and
    installed service-unit identity;
13. verifies both the detached checkout and source worktree identities again;
    and
14. writes deterministic semantic evidence separately from volatile run
    metadata.

Outputs are written outside the candidate:

- `semantic_attestation.json`;
- `release_gate_run_receipt.json`;
- `release_gate_report.md`;
- `independent_review_manifest.json`;
- `source_diagnosis_original.md`;
- `attestation_sha256_inventory.json`;
- focused/full command output and JUnit XML under `validation/`.

If validation blocks after creating a new output directory, the gate writes a
bounded `release_gate_failure_receipt.json` and hashes any completed partial
validation evidence. Output and scratch locations are validated against the
candidate, Git common directory, production and toolchain before receipt
writing is authorised. The output root and validation-evidence directory are
bound to device/inode identities; parent writes use directory descriptors and
do not follow replaced descendant paths. It never writes this receipt into an
unvalidated path or into a pre-existing output directory containing unrelated
material.

The semantic attestation deliberately excludes timestamps, host identity,
duration and raw test-output hashes. The run receipt binds raw output and JUnit
hashes, and the final inventory hashes those evidence files. A conclusion is
always labelled development-only, patch-local release candidate, or
subsystem-level; the result is not deployment authorization. Activation-time
deployed checks remain recorded as unmet.
