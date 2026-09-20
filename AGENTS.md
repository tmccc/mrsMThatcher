# Project instructions

## Proportionate engineering

This is a small, privately operated bot, not a medical trial, a regulated
safety-critical system, or a nuclear power station. Use proportionate
engineering judgement.

Prefer the smallest coherent change that addresses the demonstrated problem.
Do not introduce elaborate phase gates, review packs, attestations, duplicate
environments, immutable manifests, multi-person adjudication, broad audits,
exhaustive test matrices, large new frameworks, or long procedural reports
unless the user explicitly requests them or a specific, concrete risk makes
them necessary.

For experiments and investigations, begin with the smallest test capable of
answering the actual question. Do not impose publication-grade methodology on
ordinary development decisions. Reuse existing code, evidence, fixtures and
test machinery. Avoid speculative redesign. Negative or inconclusive results
are acceptable. Stop when the question has been answered.

Do not carry procedural machinery forward from an earlier task merely because
it already exists. Do not create new phases, schemas, runners, worktrees or
reports solely for organisational tidiness. Process must remain subordinate to
solving the user's problem.

Retain the safeguards that materially matter:

- preserve production state and durable bot data;
- protect credentials and private material;
- prevent unintended remote writes, deployments and provider calls;
- test the behaviour actually changed;
- use a separate worktree where code changes genuinely need isolation.

Validation should normally consist of focused tests for the affected behaviour
plus the nearest relevant regressions. Run broad historical suites only when a
shared change gives a concrete reason to do so.

For multi-change rounds, run focused tests per change and the broad suite once
at the end, unless a substantial change warrants earlier broad coverage. Run
the broad suite in a detached session so command-session time limits cannot
interrupt it. Capture its output and final exit status, monitor completion,
and retain the README's test isolation and single-suite-at-a-time rules.

Before proposing substantial additional process, identify the specific risk
each extra step reduces. Omit any step without a clear practical benefit.
