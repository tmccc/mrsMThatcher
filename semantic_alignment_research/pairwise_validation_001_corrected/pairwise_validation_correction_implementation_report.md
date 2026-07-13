# Pairwise Validation Correction Implementation Report

## Summary

The original report renderer switched from an f-string to an ordinary triple-quoted append, leaving nine expressions unresolved. The corrected report is separately versioned, reconciles every repaired value to JSON source fields, and fails generation when suspicious template tokens remain.

## Corrected values

- Grok calibration agreement: 60.0%.
- Gemini calibration agreement: 53.8% on 13 valid cases.
- Combined Grok cost: $0.4445.
- Full-set Grok agreement: 33/50 (66.0%).
- Rejected-winner matches: 6/8 challenger and 12/20 neither.
- Strong controls: 13; retained 9; damaged 4.

All nine values match `pairwise_report_reconciliation.json`.

## Provider compatibility

The pairwise schema introduced `uniqueItems`, which neither provider's structured-output subset accepted. OpenAI now removes that transport keyword. Anthropic removes it alongside the bounds its serializer already normalized. Provider-neutral validation remains strict.

The original runner did not persist HTTP response bodies or provider error codes, so those cannot be recovered and were not invented. Future failures persist both. Fresh compatibility probes succeeded:

| Provider | Completed | Failed | Cost |
|---|---:|---:|---:|
| OpenAI | 2 | 0 | $0.046465 |
| Anthropic | 2 | 0 | $0.025785 |

## Challenger findings

The original alternatives were primarily global lexical/theme-overlap choices, not runner-ups from the same selector decision. Lexical cases had a 46.3% neither rate; the nine historical-candidate cases had a 33.3% neither rate. Semantic and first-impression gaps are explicitly unavailable where no cached pair score exists.

Canonical simulator traces contain candidate details for 19/50 exact quote/current-winner cases. Applying active-state, identity, fingerprint, SHA duplicate, editorial-quality, production-score, and topic floors yields 13 credible reconstructed challengers; 37 become `no_credible_challenger`, and 11 credible challengers change.

## Improved policy

The policy prefers exact real selector runner-ups, then identical-state cached runner-ups, eligible soft-light alternatives, semantic runner-ups above dual floors, and first-impression runner-ups above a semantic floor. Only exact trace runner-ups are currently enabled because later evidence classes are incomplete. Human labels are not inputs.

## Pilot readiness

A 25-case independent manifest is prepared but not executed. Everest and free trade appear only as regression controls. The appropriate next step is a bounded improved pilot, not a 150-200-case experiment.

## Tests

- 193 affected semantic-alignment and production-log-isolation tests passed.
- OpenAI and Anthropic live compatibility checks were limited to two cases each.
- Thirteen derived artefacts reproduced byte-for-byte.
- The original human review and provider ledgers retained their recorded hashes.
- `python3 -m py_compile ...` and `git diff --check` passed.

## Git state

The tracked diff stat at final audit covers five pre-existing modified files: 92 insertions and 19 deletions. Pairwise correction code and research artefacts are untracked, alongside numerous unrelated pre-existing research/runtime files. Nothing was staged, committed, pushed, or deployed.
