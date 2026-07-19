# Fresh-Eyes Adversarial Review Remediation

Date: 19 July 2026
Repository: `/disks/disk1/etc/mrsMThatcher`
Starting revision: `b267a330983c5db2aec2c5722ce9e994b43bbeda`

## Executive result

The seven findings in `fresh_eyes_adversarial_review_report.md` were fixed in the recommended order. Each defect has focused regression coverage. The corrected v3 semantic-veto candidate is valid, offline, and still shadow-only. No provider or X call was made.

These changes have not been committed, deployed, or loaded by the production process. The live service remained on wrapper PID 2285642 and child PID 2285644, started at 01:57:20 BST with `NRestarts=0`.

## Corrections

### 1. Offline harness safety

- Replaced pickle-based RNG checkpoints with a strict, bounded JSON representation shared by the harness and production-parity simulator.
- Rejects legacy pickle data, unknown structures, invalid integer counts, and invalid RNG states before restoration.
- Blocks subprocess and ordinary OS process-launch routes while offline harness isolation is active, preventing a child process from escaping the Python socket guard.
- Added hostile-checkpoint and child-process regressions.

### 2. Direct factual answers

- Extended concrete-question recognition to `which`, `whose`, `how many`, `how long`, and factual yes/no forms.
- Added answer-shape and selected-evidence checks for quantities, durations, directions, and explicit yes/no answers.
- Normative or subjective questions such as `Is freedom important?`, `Do you think leadership matters?`, and `Would socialism work?` do not enter the factual-answer path.
- The existing Berlin Wall answer-first and clarification behaviour remains intact.

### 3. Durable reply idempotence

- Completed mention/hot-post targets, completed quote-tweet targets, and terminal evaluation records no longer lose membership through presentation-cache caps.
- Writes remain idempotent; transient scan/history caches remain bounded.
- Added regression coverage beyond the former 1,000 and 2,000 record limits.

### 4. Semantic-veto telemetry

- Status and digest headline metrics now apply to one manifest hash/policy stratum.
- Older manifest observations are retained as explicitly excluded historical strata rather than attributed to the current policy.
- Observation progress now counts only events for the configured manifest.

### 5. Digest occurrence preservation

- Identical physical events in one log are retained by occurrence cardinality.
- Overlapping rotations retain the maximum occurrence count from one source instead of globally collapsing all matching content.
- Resume state records fingerprint counts, preserving identical records appended at the saved timestamp while remaining compatible with the old fingerprint-list state.

### 6. Runtime-eligible v3 manifest

- Removed the three now-ineligible quotation IDs and their 91 pair rows.
- Added a versioned attribution eligibility rule and a deterministic runtime-eligible quote manifest.
- Startup validation compares the resolved manifest quote set to the current runtime-eligible set and fails stale manifests open in shadow mode.
- Corrected candidate counts: 610 quotations, 91 images, 22,066 pairs, 21,938 allow, and 128 veto.
- Candidate manifest SHA-256: `dd52144300f0c9dc5d5ed12034b9ee1b3af40650771a713776f0d1142a9f98bb`.
- Runtime eligibility manifest SHA-256: `ec05a86fcbd883c07d4c6a887913a7dda3f09162ef05a41d577eddfcca93b269`.
- Strict offline preflight reports `valid=true`, `network_calls=0`, and `active_enforcement=false`.

### 7. Early ambiguity barriers

- Every posting and reply lane now checks the durable ambiguous-remote-outcome marker before selection, retrieval, media preparation, model work, or upload.
- If a lane creates the marker during a tick, later lanes are suppressed immediately.
- The long-running process pauses remote work without entering a crash/restart loop; test-cycle execution stops cleanly.
- Final pre-write ambiguity checks remain in place as defence in depth.

## Validation

Focused and affected-suite results:

```text
reply strategy                                      150 passed
simulator and harness safety                         59 passed
semantic veto, cleanup, digest and harness group    193 passed
unit, fail-safe and reply group                     590 passed
production-path integration                           8 passed
scheduler and fail-safe                              54 passed
logging and historical-context group                 69 passed, 1 deselected
```

The groups overlap; they are reported per command and must not be summed as a unique-test total.

Final checks:

```text
python3 -m py_compile ...                            passed
git diff --check                                    passed
semantic_quote_image_veto.py shadow-preflight       valid, zero network calls
```

The twelve-minute full repository suite was not rerun. The modified paths were covered by the focused, integration, and affected broad groups above.

## Production state

```text
ActiveState=active
SubState=running
MainPID=2285642
child PID=2285644
ExecMainStartTimestamp=Sun 2026-07-19 01:57:20 BST
NRestarts=0
```

The live child runs `/usr/local/bin/mrsMThatcher2.py`. It therefore has neither these repository Python changes nor the rebuilt manifest loaded. A separate tested commit/deployment/restart authorisation is required before activation.

No commit, push, deployment, restart, signal, X call, paid-model call, production-state write, receipt write, history write, or active semantic-veto enforcement occurred during remediation.
