# Candidate-side release-assurance helper

`tools/release_gate.py` is application-owned development tooling. It cannot
issue an authoritative release attestation for the repository which supplies
it. It does not deploy, restart a service or mutate bot state.

An authoritative run requires a separately committed, hash-pinned external
release-assurance implementation. That external implementation must treat this
module, the registry, schemas, defect ledger, pytest plugin and candidate
reports as untrusted input. Until that external implementation is independently
reviewed, it is a **release-assurance bootstrap candidate**, not a trusted gate.

## Strict control documents

Priority-0 validators share `tools/strict_json.py`. Safety-relevant control and
evidence input rejects:

- duplicate object names at every depth;
- `NaN`, `Infinity` and `-Infinity`;
- malformed JSON before schema validation.

Canonical output also refuses non-finite values. This release-control boundary
does not repair production runtime JSON readers; `DEF-0017` continues to track
that separate defect.

## Candidate validation requests

The registry contains structured requests:

```json
{
  "validation_id": "pytest",
  "selectors": ["tests/test_release_gate.py"]
}
```

Candidate input cannot supply an executable, environment prefix or shell
fragment. The candidate helper recognises only `pytest` and
`registry_validate`, validates pytest selectors under `tests/`, and constructs
fixed argument vectors with `shell=False`. The external assurance repository
must own the authoritative validation-ID policy and may reject any weakening
proposed by the candidate.

## Development-only use

```bash
python3 tools/release_gate.py run \
  --repo . \
  --base <explicit-base-commit> \
  --development-dry-run \
  --output-dir /path/outside/the/worktree/development-evidence
```

Development mode may exercise candidate-owned checks, but its output is
advisory. It always records:

- `authoritative_release_attestation: false`;
- `release_candidate_validation_passed: false`;
- `conclusion_scope: candidate-owned advisory`.

Calling `run` without `--development-dry-run` fails closed and directs the
operator to the external gate. Candidate-owned evidence must never be relabelled
as an externally qualified frozen-candidate result.

## Artefact discovery compatibility

The candidate helper uses typed `ArtifactBinding` records and this exact
resolution vocabulary:

- `static_path_composition`;
- `loader_runtime_root`;
- `unique_tracked_basename`;
- `validator_backed_offline_literal`;
- `ambiguous_not_claimed_runtime`.

Any valid `resolved_path` denotes runtime consumption. Runtime inventories are
normalised `set[str]` values rather than `(path, hash)` tuples. Ambiguous
bindings fail closed. A validator-backed offline literal is non-runtime only
when its named validator exists. If no direct companion relationship exists,
the report states count `0` and status `not_applicable`; it does not claim that
vacuous relationships are all valid.

## Authoritative external boundary

The external bootstrap gate is responsible for:

- private-root filesystem containment;
- a private, declared Python distribution closure for controller, xdist workers
  and subprocesses;
- trusted validation-ID command templates;
- candidate/base Git identity and drift checks;
- trusted invariant-policy comparison;
- generated-artefact relationship validation;
- focused and complete-suite execution;
- authoritative semantic attestation and run receipt.

The application candidate may retain this helper for compatibility and focused
regression coverage, but release qualification must not import or execute its
gate code.
