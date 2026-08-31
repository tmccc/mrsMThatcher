# Proposition ledger v1 design

## Scope

`proposition-ledger-v1.0.0` represents the public conversational state after one
turn. It is an incremental, evidence-linked ledger rather than a summary or a
substitute transcript. A snapshot is valid only for its `as_of_turn_index` and
must be reconstructable from the preceding snapshot, the current transcript
turn, and the explicit `state_transitions` delta.

The schema is JSON Schema Draft 2020-12. JSON Schema enforces the structural
contract. `tools/build_proposition_ledger_phase1.py` enforces the graph,
evidence-span, lifecycle, incremental, and future-turn invariants that cannot be
expressed reliably as local JSON Schema constraints.

## Evidence and epistemic boundaries

Every non-synthetic proposition cites one or more exact character spans from a
turn at or before the snapshot boundary. `canonical_text` may repair grammar or
resolve an explicit reference, but may not silently strengthen, weaken, broaden,
or narrow the cited language. Unresolved references and attribution remain
unknown.

The ledger separates three different questions:

1. what the transcript says;
2. what a participant is committed to;
3. what a later evaluator diagnoses.

Quoted and reported claims use `quoted_claim` or `reported_claim`, carry a
non-endorsing commitment state unless the speaker separately endorses them, and
name the attributed participant where known. A question presupposition uses
`question_presupposition` and `presupposed_only`; it is never silently promoted
to an asserted fact. Rhetorical and expressive contributions may create a
`no_stable_issue` state without inventing a factual yes/no question.

## Compound structure

Independent claims in one turn receive independent proposition IDs. A
`proposition_group` preserves conjunction, disjunction, conditionals, causal
chains, premise/conclusion structure, and compound accusations. Member roles
allow conduct, cause, outcome, and motive to remain distinct. Decomposition is
therefore permitted without treating the parts as unrelated or collapsing them
into a stronger compound assertion.

## Issues, commitments, and obligations

Issue states preserve live alternatives and answer requirements independently
of proposition lifecycle. A counterfactual can remain `open` while evidence for
a sub-question is discussed. A later narrower issue does not supersede the
earlier issue unless an explicit state transition says so.

Commitments record only a participant's evidenced stance. An allegation by one
participant does not commit its target. Conversational obligations are separate
from both commitment and objective truth; they record questions, clarification
and evidence requests, promised follow-ups, corrections requiring
acknowledgement, and unresolved direct challenges.

## Answer-target repair

`answer_targets`, `rejected_answer_targets`, and `repair_records` retain both the
apparent target of an account reply and a contributor's correction. A “not X but
Y” repair can reject X, install Y as the replacement target, leave a related
proposition live, and later record acknowledgement or repetition of the rejected
target. Thus existence and security, desirability and feasibility, and similar
non-equivalent propositions cannot be silently substituted.

Relations with `substitutes_for`, `fails_to_address`, or comparable diagnostic
semantics use `machine_diagnostic` or `human_annotation` provenance with an
`evaluator_diagnosis` basis unless the cited speaker explicitly makes that
relation. Speaker-explicit metadiscourse instead uses `transcript_extraction`
with `speaker_explicit_metadiscourse`. Direct decomposition of a compound
allegation is not itself a substitution.

## Incremental and hashing rules

Turn indices are zero-based. `turn_refs` exactly cover the transcript prefix
through `target_turn_id`, whose index equals `as_of_turn_index`. Every turn,
evidence span, introduction,
resolution, update, answer target, and repair referenced by snapshot N must have
an index no greater than N. Parent turn references must point backward. The
snapshot contains exactly one current transition. It identifies the current
turn and enumerates additions and updates for propositions, proposition groups,
issues, commitments, obligations, relations, answer targets, rejected targets,
repair records, resolved items, and warnings.

The transition also carries one required, ordered `state_patch`. The typed
addition and update lists remain concise audit projections; `state_patch` is
the authoritative replay surface. Starting with the preceding snapshot's
state-bearing fields, applying every patch operation in order must reproduce
exactly the current `source_completeness`, `participants`, `turn_refs`,
`propositions`, `proposition_groups`, `issue_states`,
`participant_commitments`, `conversational_obligations`,
`proposition_relations`, `answer_targets`, `rejected_answer_targets`,
`repair_records`, `unresolved_items`, `resolved_items`, `extraction_status`, and
`warnings`. The top-level chain envelope is supplied by the current snapshot
and transcript turn; it is not patched as conversational state.

Each operation names a collection and stable `item_id`. Array records use their
native ID field: `participant_id`, `turn_id`, `proposition_id`,
`proposition_group_id`, `issue_id`, `commitment_id`, `obligation_id`,
`relation_id`, `answer_target_id`, `rejected_answer_target_id`, `repair_id`, or
`warning_id`. Unresolved and resolved records use the composite
`item_type:item_id`. The singleton objects use `source_completeness` and
`extraction_status` as their respective item IDs.

An `add` operation requires no previous-record hash and supplies the complete
new record. A `remove` operation requires the previous-record hash and supplies
no replacement. A `replace` operation requires the previous-record hash and
supplies the complete replacement record; it is not a merge patch. Record
hashes use canonical UTF-8 JSON with sorted keys and compact separators. A
remove or replace is valid only when its hash matches the record immediately
before the operation. At most one operation may address a given
`(collection, item_id)` in one transition; operations are ordered by the
schema's collection order and then by item ID for deterministic serialization.
The resulting records must validate as the named collection's record type, and
the typed audit projections must agree with the patch.

Only the turn-zero snapshot may be genesis. Its `from_ledger_sha256` is null and
its patch adds every record needed to construct state from empty collections.
For snapshot N greater than zero, the supplied history contains exactly one
snapshot for each index from zero through N-1. Every link advances one turn,
keeps the same schema version, conversation key, and root post identity, and
binds those identities to the transcript envelope. Each predecessor must itself
be valid, and its deterministic hash must equal both chain fields before replay
begins. A skipped turn, nonzero genesis, or rehashed cross-conversation
predecessor invalidates the chain.

`ledger_sha256` is deterministic: serialize the complete ledger as canonical
UTF-8 JSON with sorted keys and compact separators after replacing
`ledger_sha256` with 64 ASCII zeroes, then compute SHA-256. This avoids a
self-referential hash while binding every other field. The preceding snapshot's
actual hash is copied into both `previous_ledger_sha256` and the current
transition's `from_ledger_sha256`.

The validator requires every delta ID to exist in its typed namespace, every
updated record's final status to agree with the snapshot, and every resolved ID
to agree with `resolved_items`. One typed update field may name a logical item
only once, even if two records use different explanatory text. Native and
composite state keys must also be unique before projection, so replay cannot
hide two distinct records behind one key. The complete authenticated history,
preceding hashes, and replayed `state_patch` form the chain-validation boundary.
Synthetic fixtures supply the full turn-by-turn chain so Phase 1 can prove
reconstruction without fabricating missing historical state.

Lifecycle updates follow explicit allow-lists. A resolved, withdrawn,
superseded, or abandoned item cannot silently return to `live`; reopening
requires a new item linked by a relation. Orphan IDs, mismatched evidence spans,
non-deterministic hashes, and future references invalidate a snapshot.

## Known pilot failures covered

The contract directly addresses the earlier QUD pilot's diagnostic failures:

- rhetorical or expressive language can abstain with `no_stable_issue`;
- compound accusations preserve conduct, causation, and motive separately;
- still-live counterfactuals survive narrower supporting discussion;
- rejected and replacement answer targets remain distinct;
- diagnostic relations are separated from transcript facts;
- premise-neutral replies may address a compound allegation without accepting
  every premise; and
- decomposition is represented as structure, not automatically criticised as
  substitution.

This design is a research contract. It is not evidence that a ledger improves
reply reasoning, and it is not a production integration proposal.
