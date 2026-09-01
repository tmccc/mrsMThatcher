# Phase 2D synthetic semantic probe result

The probe ran once from pre-call commit `f452fae1791664017b6881dc3ffabd4f7d353e0a` in the single private output directory created for this stage. It used the wholly synthetic three-turn conversation, the unchanged Phase 2B evidence transport and schemas, `reasoning_effort=low`, and a maximum output ceiling of 8,192 tokens. It made no repair, retry, fallback, search, code-execution, or tool call.

## Per-call result

| Order | Model / turn | Provider and structural result | Semantic expectation result |
| --- | --- | --- | --- |
| 1 | Grok 4.3 / turn 0 | Accepted; strict JSON, transport schema, evidence resolution, canonical schema, references, deterministic materialisation, and persisted-ledger validation passed. | Failed only because the bridge-closed proposition had `commitment_status=speaker_committed` but no participant `commitment_changes` add record. The response did not abstain and did not create an issue. Its structurally valid ledger was supplied to this model's turn 1. |
| 2 | Grok 4.6 / turn 0 | Accepted; every structural stage passed. | The proposition and explicit participant commitment were present, so `no_stable_issue` did not suppress semantic extraction. The response nevertheless included a `no_stable_issue` abstention and sentinel issue, contrary to this turn's no-abstention expectation. Its structurally valid ledger was supplied to this model's turn 1. |
| 3 | Grok 4.3 / turn 1 | Accepted; strict JSON, transport, evidence, canonical, and reference validation passed. Persisted materialisation failed with `schema:$.propositions[3].epistemic_status:'presupposed_only' was expected`. | Not run after structural failure. The delta represented separate bridge-open and east-entrance-blocked propositions, a contradiction of the existing bridge-closed proposition, and an open entrance question, but represented its `question_presupposition` proposition with `epistemic_status=questioned`. |
| 4 | Grok 4.6 / turn 1 | Accepted; every structural stage passed. | Passed: separate bridge-open and east-entrance-blocked propositions, correction against the existing bridge-closed proposition, and an open entrance question were all represented without abstention. |
| 5 | Grok 4.3 / turn 2 | Not attempted. | Correctly blocked because order 3 produced no valid materialised immediate predecessor for this model. |
| 6 | Grok 4.6 / turn 2 | Accepted; every structural stage passed. | Passed: the west-entrance proposition, withdrawal of the contributor's existing commitment, and answer/resolution of the existing entrance issue all used valid existing identifiers without abstention. |

## Aggregate

- Planned entries: 6.
- Provider calls attempted: 5; accepted responses: 5; blocked entries: 1.
- Strict transport and canonical validation successes: 5.
- Semantic-reference validation successes: 5.
- Deterministic materialisation and persisted-ledger successes: 4.
- Turn-specific semantic expectation successes: 2.
- Completed entries: 2; failed entries: 3; blocked entries: 1.
- Grok 4.6 completed a structurally valid three-turn chain; Grok 4.3 did not because turn 1 failed persisted materialisation.
- Neither model passed all three turn-specific expectations: Grok 4.6 missed only turn 0, while Grok 4.3 missed turn 0, failed structurally at turn 1, and was blocked at turn 2. No winning model was selected.
- No response reached the 8,192-token ceiling. All five returned responses ended with `REASON_STOP`; the largest completion contained 3,321 tokens.
- Retry calls: 0. Repair calls: 0. Fallback calls: 0.

The planned sixth provider call was not made because the specified fail-closed dependency rule blocks a later turn when that model lacks a valid predecessor ledger. The run was not repeated.

## Verification and boundaries

Credential-free `--verify` reprocessed all five saved raw responses, confirmed the saved parsed transports, resolved canonical deltas, validations, and materialised ledgers, checked 33 private files, and left the call log unchanged. Checksum verification passed and verification made zero provider calls.

The live probe read no real conversation record. The preceding offline diagnosis was limited to the already exposed Phase 2A cases and corrected Phase 2B post-mortem named for this stage. No complete corpus, moving prospective data, or held-out material was read. The Phase 2A development pilot was not rerun. Production was untouched, and nothing was merged or deployed.
