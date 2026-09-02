# Frozen minimal four-arm proposition-ledger experiment

## Boundary

This is a supplemental, post-specification development experiment based on Phase 2E commit `7c6cdd6d4db71c96789a120ed39420e9714e5236`. It does not revise the Phase 2A–2E samples, results, counts, prompts, schemas, or materialiser. It selects a pragmatic profile for this experiment only and does not declare a production winner.

Setup, case validation, preparation, verification, and human-pack construction make zero provider calls. Live execution remains blocked until the genuine Arm D procedure is complete and hash-locked.

The compact live adapter is implemented and offline-tested but deliberately not live-verified in this zero-call setup stage. It is constructed only after the human, Git, exact-budget, pristine-ledger, and frozen-policy gates pass.

## Current contracts

- Canonical semantic delta: `proposition-ledger-semantic-delta-v1.1.1`.
- xAI transport delta: `proposition-ledger-xai-transport-delta-v2.0.1`.
- Persisted ledger: `proposition-ledger-v1.0.0`.
- Evidence selectors: exact text plus zero-based occurrence index.
- Ledger prompt: unchanged Phase 2E prompt v4.
- Canonical postvalidation, selector resolution, deterministic materialisation, matching commitment records for new speaker commitments, question-presupposition constraints, and the issue-level meaning of `no_stable_issue` remain mandatory.

## Arms

Every downstream evaluation receives identical instructions, parameters, exact transcript bytes, reply target, and target boundary. Only the added representation differs.

| Arm | Input |
|---|---|
| A | Exact transcript context only. |
| B | The same transcript context plus a neutral ordinary-prose summary. |
| C | The same transcript context plus the machine-generated incremental proposition-ledger snapshot. |
| D | The same transcript context plus an independently transcript-first human-annotated, evidence-backed and adjudicated proposition-ledger snapshot. |

Arm B uses `grok-4.6`, low reasoning, and an 8,192-token output ceiling. Arm C uses `grok-4.6`, low reasoning, the unchanged Phase 2E prompt v4, and an 8,192-token output ceiling. Actual B and C sizes are recorded; neither is padded or regenerated solely to equalise length.

The downstream writer uses OpenAI `gpt-5.6-sol` with medium reasoning, the frozen research prompt and schema in this directory, a 900-token output ceiling, a 180-second timeout, temperature 1, no tools, no streaming, no storage, and zero retries, repairs, fallbacks, or replacement calls. These settings are identical for A–D.

## Case and replay policy

The first case is `market-planning-live-20260901`, marked `supplemental`, `post_specification`, `live_challenge`, and `excluded_from_prespecified_counts`.

Its three accepted targets use neutral aliases `evaluation-01`, `evaluation-02`, and `evaluation-03`. Exact production prompts and payload bytes were not retained. The observed-production records are therefore frozen with a null payload and are not replay-eligible. Retained ancestry is stored separately as evidence and must never be presented as exact production context.

The frozen ledger source-completeness record is grade B: exact text and chronology are complete for the replay transcript, but the parent graph and referenced-tweet evidence are incomplete. Arms C and D receive the same neutral record. The missing exact production payload remains case-level metadata and is not injected into either ledger arm.

Chronology-aware contexts include only relevant turns created before the corresponding production generation cutoff. Earlier bot replies on the retained path remain present. The three evaluated sibling replies to the accepted targets are withheld from blind inputs, as are later model outputs. The separate later-topic cap casualty is operational evidence only and is never part of the market ledger.

Two evaluation targets share one byte-identical chronology-aware transcript snapshot. The third adds one pre-cutoff market contribution. Summary generation and incremental ledger construction reuse those two unique snapshots. Reply-target-specific downstream inputs remain distinct, so the prospective downstream count is three runnable targets times four arms: 12 evaluations. The runner derives these values from bytes and never hard-codes semantic equivalence.

Arm C uses one chronological turn chain and takes snapshots at the two unique transcript cutoffs. It never rebuilds the full ledger for each target. A contract-invalid output is retained as an experimental outcome; there is no tuning or repair.

## Human Arm D gate

The transcript-first pack contains exact frozen transcript bytes and blank current-schema semantic-delta sections. It contains no model output, model identity, arm label, working diagnosis, P1–P7 map, published evaluated reply, or suggested answer.

Exactly two human raters must independently annotate the exact transcript while blinded to the machine ledger, one another's work, production outcomes, evaluated historical replies, arm identity, and provider identity. A distinct blinded human adjudicator resolves the two annotations. Deterministic code resolves exact evidence, materialises the persisted ledgers, validates every incremental transition, and locks the adjudicated gold SHA-256 before any machine artefact is revealed or scored. Model- or Codex-authored material cannot satisfy this gate.

The working diagnosis and diagnostic questions live outside the transcript-first pack and may be opened only after the gold lock and blind arm artefacts are frozen.

## Privacy and immutability

Verbatim transcripts, raw source and author identifiers, source-record bytes, complete inputs and outputs, and human annotations remain in a mode-0700 external private run with mode-0600 files, no symlinks, and a complete checksum inventory. Git tracks only sanitized aliases, timestamps, hashes, status flags, protocol assets, runner code, and synthetic tests.

The experiment makes no production write, imports or executes no production module, changes no service, and performs no merge or deployment.

## Causal boundary

Ledger fidelity, downstream writer behaviour, reviewer behaviour, context construction, candidate freshness, and author-cap truncation are reported separately. A proposition ledger cannot repair a scheduler which supplies a stale fragment while newer parts of the same argument are already available.
