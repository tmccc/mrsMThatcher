# Phase 2E proposition-ledger contract alignment

This correction starts from `1735a92b30dc74c91f1a84e83695ec5f7e65dc30` and is limited to the three contract mismatches exposed by the Phase 2D synthetic semantic probe. It does not change the persisted-ledger schema, evidence transport design, synthetic conversation, model settings, or provider-call policy.

## Contract corrections

The canonical semantic-delta schema is patch-bumped from `proposition-ledger-semantic-delta-v1.1.0` to `proposition-ledger-semantic-delta-v1.1.1`. A complete `newProposition` now mirrors the existing persisted-ledger constraints:

- `question_presupposition` requires `epistemic_status = presupposed_only` and a non-committal `commitment_status`;
- `presupposed_only` requires `proposition_kind = question_presupposition`.

The conditionals are deliberately absent from the partial `proposition_updates[].changes` schema. Compatibility after an update remains a deterministic merge-and-persist validation responsibility.

The exact canonical schema SHA-256 is `986500bcf237b25c4fd7f9828060fd1879c2ed8b2746bb6154fc3d3ecd315bad`. The existing generator produced transport schema `proposition-ledger-xai-transport-delta-v2.0.1`, SHA-256 `36076c48150e6bb8d6368736a75b977fb22197de6f57c78e65e8a09b56745bf9`. The normal xAI transform produced provider-schema value SHA-256 `5d0fb9d22e0d51885ec16af630268fea09a3b62a1d1efc1ac1f333b587f06a41`. The persisted schema remains `proposition-ledger-v1.0.0`.

The materialisation path now checks same-turn new propositions against same-turn commitment additions. A current-speaker proposition marked `speaker_committed` requires exactly one asserted add for that speaker and proposition local reference. Conversely, an asserted add to a same-turn new proposition must agree with a speaker attribution, the participant, and `speaker_committed` status. Failures are bounded as:

- `speaker_committed_proposition_missing_commitment_record`;
- `duplicate_commitment_record_for_new_proposition`;
- `asserted_commitment_proposition_status_mismatch`.

Updates to existing commitments are unchanged. The Phase 2D same-turn `no_stable_issue` materialiser fix is unchanged.

The semantic rubric now treats `no_stable_issue` as an issue-level conclusion. On the assertion-only bridge turn, that conclusion may coexist with complete extraction, the bridge-closed proposition, and its participant commitment. It cannot excuse missing semantic content. The explicit correction/question turn still requires separate corrected propositions, a relation to the prior proposition, and a live entrance issue; the answer turn still requires the west-entrance proposition, withdrawal, and issue answer/resolution lifecycle changes.

Prompt v4 differs from v3 only by stating the question-presupposition pairing, the required stable commitment record for a new current-speaker `speaker_committed` proposition, and the issue-level scope of `no_stable_issue`.

## Corrected offline interpretation

The single completed Phase 2D private run, `proposition-ledger-phase2d-semantic-hardening-20260901T185456Z`, passed its checksum manifest before reprocessing. Its five raw responses declare the superseded schema versions, so the offline checker replaced only the two root version declarations in memory and retained the original raw bytes and historically supplied predecessor ledgers. It made zero provider calls and did not modify the source run.

| Saved order | Model / turn | Corrected result |
| --- | --- | --- |
| 1 | Grok 4.3 / turn 0 | Rejected deterministically with `speaker_committed_proposition_missing_commitment_record:new-proposition-1`. |
| 2 | Grok 4.6 / turn 0 | Passed structure, materialisation, persistence, and the corrected semantic rubric. Its issue-level `no_stable_issue` result coexists with the extracted proposition and commitment. |
| 3 | Grok 4.3 / turn 1 | Rejected at the derived semantic-delta transport-schema boundary because its `question_presupposition` used `epistemic_status = questioned` instead of `presupposed_only`. |
| 4 | Grok 4.6 / turn 1 | Continued to pass. |
| 6 | Grok 4.6 / turn 2 | Continued to pass. |

All five corrected classifications matched the specified interpretation. The blocked historical Grok 4.3 turn 2 had no saved response and was not reconstructed.

## Targeted live plan

Only four entries are planned, in this order:

1. Grok 4.6, turn 0;
2. Grok 4.3, turn 0;
3. Grok 4.3, turn 1, dependent on order 2 producing a valid materialised ledger;
4. Grok 4.3, turn 2, dependent on order 3 producing a valid materialised ledger.

Each eligible entry may be attempted once. A failed Grok 4.3 entry blocks only its later dependent entries. There are no retries, repair calls, fallbacks, tools, search, or streaming. Requests retain `reasoning_effort = low`, an 8,192-token output ceiling, and the exact-text/occurrence-index evidence transport. The run reads no real, held-out, or production conversation data and makes no model-selection decision.
