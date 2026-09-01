# Phase 2E proposition-ledger contract retest result

The corrected contracts were committed and pushed at pre-call commit `df663b47cd74b52bf780745214c4664b36434b8d`. The targeted retest then ran once in `proposition-ledger-phase2e-contract-alignment-20260901T195350Z` using the wholly synthetic Phase 2D bridge conversation. It retained `reasoning_effort = low`, an 8,192-token output ceiling, exact-text/occurrence-index evidence selectors, no tools, no search, no streaming, no retries, and no fallback.

## Corrected offline recheck

The checker reprocessed all five saved Phase 2D responses with only their obsolete root schema-version declarations rebound in memory. Each response used the predecessor ledger it historically received. The Phase 2D run passed checksum verification and was not modified.

| Saved order | Model / turn | Corrected result |
| --- | --- | --- |
| 1 | Grok 4.3 / turn 0 | Failed the deterministic consistency gate: `speaker_committed_proposition_missing_commitment_record:new-proposition-1`. |
| 2 | Grok 4.6 / turn 0 | Passed. The proposition and matching commitment remained complete while the issue-level `no_stable_issue` abstention and sentinel were permitted. |
| 3 | Grok 4.3 / turn 1 | Failed at the derived transport-schema boundary: its `question_presupposition` required `epistemic_status = presupposed_only`. |
| 4 | Grok 4.6 / turn 1 | Passed. |
| 6 | Grok 4.6 / turn 2 | Passed. |

All five expected corrected classifications matched. Offline rechecking made zero provider calls.

## Targeted live result

| Order | Model / turn | Result | Latency | Tokens (prompt / reasoning / completion / total) |
| --- | --- | --- | ---: | ---: |
| 1 | Grok 4.6 / turn 0 | Passed every structural, materialisation, persistence, and semantic check. It extracted the bridge-closed proposition and matching asserted commitment, with a permitted issue-level `no_stable_issue` abstention and sentinel. | 16.242319 s | 10,379 / 435 / 664 / 11,478 |
| 2 | Grok 4.3 / turn 0 | Passed every structural, materialisation, persistence, and semantic check. It supplied the newly required asserted participant commitment and did not abstain. | 13.057023 s | 8,187 / 727 / 636 / 9,550 |
| 3 | Grok 4.3 / turn 1 | Provider transport and canonical schema validation passed, including the corrected question-presupposition contract. Semantic references passed. Deterministic consistency then rejected two `speaker_committed` propositions because neither had its asserted commitment add: `new-proposition-1` and `new-proposition-2`. | 18.904330 s | 10,038 / 865 / 1,218 / 12,121 |
| 4 | Grok 4.3 / turn 2 | Not attempted; correctly blocked because order 3 produced no materialised immediate predecessor. | — | — |

Three of four planned entries were eligible and attempted exactly once. Two completed, one failed, and one was blocked. All three provider responses were accepted, ended with `REASON_STOP`, passed transport resolution and canonical validation, and stayed below the output ceiling. Two passed deterministic materialisation, persisted-ledger validation, and the turn-specific semantic rubric.

Aggregate usage was 28,604 prompt tokens, 2,027 reasoning tokens, 2,518 completion tokens, and 33,149 total tokens, including 1,024 cached prompt-text tokens. Aggregate measured latency was 48.203672 seconds across the three attempted calls.

Retry calls: 0. Repair calls: 0. Fallback calls: 0.

## Validation and boundaries

The requested focused suite passed 130 tests. Both specified Python files compiled, the derived transport generator check passed, and `git diff --check` passed. Post-run `--verify` required no API key, made zero provider calls, reprocessed the three saved live responses and five historical offline responses, confirmed the call log was unchanged, checked 22 private files, and passed checksum verification.

The persisted-ledger schema remains `proposition-ledger-v1.0.0`; the evidence transport design remains exact text plus occurrence index. The Phase 2A pilot was not rerun. No real, held-out, unexposed, or production conversation data was used. Production files and services were untouched. No winning model was selected, and nothing was merged or deployed.
