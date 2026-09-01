# Phase 2D offline diagnosis

This diagnosis used only the saved Phase 2A development run and the corrected Phase 2B post-mortem named for this stage. It did not read the complete corpus, moving prospective data, or held-out material. The summaries below intentionally omit transcript text.

## Empty `no_stable_issue` responses

All seven Grok 4.3 responses returned `extraction_status=abstained`, one `no_stable_issue` abstention, no propositions, no commitments, and no other semantic additions. All seven nevertheless passed the saved strict-JSON, schema, evidence, reference, materialisation, and persisted-ledger checks.

| Current turn | Stable surface content | Issue-level conclusion |
| --- | --- | --- |
| `dev-turn-001-000` | Rejection/correction, normative assertions, and evaluation | A stable issue was discernible. |
| `dev-turn-002-000` | Elliptical evaluation/attribution and an expressive speech act | `no_stable_issue` was reasonable only for issue detection. |
| `dev-turn-002-001` | Explicit assertion and clarification question | A stable issue was explicit. |
| `dev-turn-002-002` | Challenge/contradiction, counterexample, definitional correction, and evaluation | Stable disputed issues were discernible. |
| `dev-turn-006-000` | Explicit question and evaluative presupposition | A stable issue was explicit. |
| `dev-turn-006-001` | Correction/attribution, assertion, directive, and normative criteria | A stable evaluative issue remained. |
| `dev-turn-008-000` | Expressive speech act and explicit attribution | `no_stable_issue` was reasonable only for issue detection. |

Aggregate: 7/7 turns contained explicit stable semantic content; 2/7 could reasonably use `no_stable_issue` only as an issue-level conclusion; and 5/7 contained a discernible issue or explicit question. The existing prompt and schema-valid abstention path therefore allowed `no_stable_issue` to become an excuse for omitting all semantic records. The narrow correction is to scope the code to issue identification while requiring independent extraction of supported propositions, speech acts, attributions, corrections, and commitments.

## Residual Grok 4.6 failures

All three coordinate-corrected deltas passed binding, canonical-delta validation, and semantic-reference validation before failing materialisation.

| Recovery / turn | Exact downstream error | Object and reference classification | Diagnosis |
| --- | --- | --- | --- |
| `recovery-003` / `dev-turn-002-000` | `resolved_state_missing_item:issue:issue-86a49c37a70fc49b9ae502e81fd2b5f25af9a3c588b14a03fd2aeb583d39835a` | A same-turn local `new_issue_states` sentinel with `issue_type`, `status`, and `resolution_type` all `no_stable_issue`; not stale, previously resolved, or wrong-namespace, and `resolved_items` was empty. | The provider delta was valid under the canonical contract. The deterministic materialiser persisted the terminal sentinel issue but incorrectly omitted its matching `resolved_items` registry entry. This clean case justifies one focused regression and materialiser fix. |
| `recovery-011` / `dev-turn-006-000` | `schema:$.propositions[0].commitment_status:'speaker_committed' is not one of ['speaker_not_committed', 'attributed_to_another', 'questioned_only', 'left_uncertain', 'withdrawn', 'not_applicable']` | A `question_presupposition` proposition with `epistemic_status=presupposed_only`; its local references were present and namespace-compatible. | The provider emitted incompatible semantic state and also asserted a commitment to it. The materialiser correctly rejected the delta. |
| `recovery-013` / `dev-turn-008-000` | The same `resolved_state_missing_item:issue:issue-afdf75d4698965a54abcd70594ce5b64d96c34ec94dbed1cd9ee47474fb8e71a` error, plus the same persisted proposition-schema error as `recovery-011`. | One same-turn local `no_stable_issue` sentinel and one `presupposed_only`/`speaker_committed` proposition; neither involved a missing prior ID, stale ID, or wrong namespace. | Mixed failure: the sentinel exposes the materialiser defect, while the proposition state is provider-invalid. `recovery-003` is the isolated regression case. |

The prompt correction therefore also forbids treating merely presupposed content as an asserted speaker commitment. It does not relax the deterministic rejection of invalid provider state.

## Truncated response

The saved Grok 4.6 response for `dev-turn-007-000` used a 4,096-token output limit, consumed 4,096 completion tokens, and ended with `REASON_MAX_LEN`. Strict parsing failed with `JSONDecodeError:Unterminated string starting at:line=455:column=7`; the post-mortem also records an unclosed container. The response was structurally truncated, so no later validation stage ran. The Phase 2D probe raises only the output ceiling to 8,192 tokens; no transport or schema change follows from this failure.

## Offline evidence boundary

The aggregate audit came from the corrected Phase 2B `materialised-no-stable-issue-audit.json`. Residual and truncation findings came from its `diagnostic-recovery/recovery-{003,011,013}.json`, `per-call-diagnostics/call-{007,017,019,020}.json`, and `strict-json-failure-diagnostic.json`, with the corresponding already-exposed Phase 2A validation and request metadata. Neither source run was modified.
