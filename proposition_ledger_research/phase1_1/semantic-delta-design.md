# Semantic delta and deterministic materialisation boundary

## Contract boundary

`proposition-ledger-semantic-delta-v1.0.0` is the provider-facing response
contract for semantic analysis of one exact current transcript turn. It is not
a persisted ledger and cannot be replayed as storage state by itself.

The durable output remains `proposition-ledger-v1.0.0`. Three separately
identified objects therefore participate in ledger construction:

1. the provider response schema,
   `proposition-ledger-semantic-delta-v1.0.0`;
2. the pure deterministic materialiser,
   `tools/proposition_ledger_semantic_delta.py`; and
3. the persisted ledger schema, `proposition-ledger-v1.0.0`.

The provider response contains only the current turn's semantic additions,
semantic changes, abstentions, and warnings. It does not contain a ledger hash,
a predecessor hash, a state patch, source-file hashes, storage locations,
unchanged cumulative state, transcript turn references, participants, source
completeness, or provider-chosen permanent identifiers. `additionalProperties:
false` applies at the response boundary and within every semantic record.

`prior_ledger_reference` echoes only the supplied predecessor `ledger_id` and
turn index. It intentionally contains no persistence hash. The materialiser
binds that reference, `conversation_key`, `target_turn_id`, and
`as_of_turn_index` to the actual validated predecessor and exact current turn.

## References and identifiers

Existing items are referenced by the stable IDs present in the supplied prior
ledger. New current-turn items use bounded namespace-specific local references,
for example `new-proposition-1`, `new-issue-1`, or `new-relation-1`. A local
reference is not a durable ID.

Before constructing records, the materialiser inventories every local
reference and rejects duplicates. It then assigns a permanent ID from canonical
JSON containing the schema version, conversation binding, target turn,
namespace, and local reference. The assignment is deterministic and checked
against every predecessor ID in that namespace. Same-turn group members,
relations, commitments, obligations, answer targets, rejected targets, repairs,
and resolved items may refer to declared local references; an undeclared,
wrong-namespace, or ambiguous local reference is rejected.

Participants cannot be introduced by the semantic delta. Participant
references must already exist in the validated prior ledger. A provider cannot
set an introduction turn, update turn, resolution turn, reply turn, selection
turn, rejection turn, repair trigger turn, acknowledgement turn, ledger ID,
transition ID, or warning ID. Deterministic code supplies those values from the
exact current-turn binding.

## Evidence and epistemic boundaries

Every semantic record carries one or more exact evidence spans, including
updates and semantic changes whose persisted record type has no evidence-span
field. The materialiser requires every supplied span to name the current turn,
checks integer bounds against the exact current transcript text, and verifies
the exact substring. A prior-turn or future-turn span is rejected. Existing
prior evidence remains part of the validated predecessor and cannot be
silently rewritten.

The semantic schema preserves the Phase 1 distinctions between transcript
facts, participant commitments, and evaluator diagnoses. Proposition speech
acts, epistemic and commitment statuses, relation provenance, and analysis
basis remain separate fields. Unknown or ambiguous analysis is represented by
an extraction status, an enumerated abstention, an uncertainty reason, and
optionally an evidence-bound warning. `no_stable_issue` remains a valid issue
type, issue status, resolution type, and abstention.

## Materialisation sequence

The materialiser accepts exactly the validated prior full ledger, the exact
current transcript turn, and one semantic delta. Schema objects may be supplied
as keyword-only validation dependencies; by default the two tracked schemas are
loaded locally.

It performs these operations in order:

1. validate the semantic delta against its provider response schema;
2. validate the predecessor's persisted shape and self-hash;
3. bind the predecessor reference, conversation, target turn, turn index,
   parent, and speaker;
4. validate every exact current-turn evidence span;
5. inventory local references and assign deterministic permanent IDs;
6. validate and resolve every existing and same-turn reference;
7. apply semantic additions and explicit changes to a deep copy of the prior
   cumulative state;
8. apply only allow-listed lifecycle transitions and current-turn resolution
   fields;
9. append the deterministic current `turn_ref` and construct the new ledger
   envelope;
10. derive the complete authoritative `state_patch` with the existing Phase 1
    patch builder;
11. derive the typed transition projections and transition reasons from that
    patch, never from provider-supplied persistence data;
12. calculate the predecessor link and final ledger hash; and
13. run the incremental full-ledger validator against the validated immediate
    predecessor and exact current turn.

The incremental validation hook is necessary because the persisted predecessor
contains hashes and evidence spans, not the complete prior raw transcript text.
`validate_ledger_incremental(ledger, previous_ledger, current_turn, schema)`
treats the predecessor as already fully validated, requires its turn references
and old evidence to be preserved, checks newly introduced evidence only against
the exact current turn, and delegates the remaining schema, graph, lifecycle,
patch, predecessor-link, and hash invariants to the full Phase 1 validator.

The provider never supplies the authoritative `state_patch`, the typed durable
transition, the predecessor hash, or the ledger hash. No model-produced value
can override those fields.

## Typed failures

Materialisation returns either status `ok` with a full ledger, or one bounded
failure with no partial ledger. Error lists are capped in count and length.
The public failure statuses are:

- `semantic_delta_schema_invalid`: the provider response violates its schema,
  including any attempted hash, patch, cumulative collection, or permanent ID;
- `semantic_reference_invalid`: a conversation, turn, predecessor, participant,
  existing-item, or local-reference binding is invalid;
- `semantic_evidence_invalid`: a span names another turn, is out of bounds, or
  does not match the exact current transcript text;
- `semantic_transition_invalid`: a lifecycle transition or resolution repeats
  or violates the allow-list;
- `materialisation_invariant_failure`: the validated predecessor, deterministic
  ID assignment, patch construction, or required validator integration violates
  an internal invariant; and
- `persisted_ledger_validation_failure`: the constructed cumulative ledger is
  rejected by the full persisted-ledger validator.

These statuses prevent provider formatting failures, semantic reference errors,
unsupported evidence, illegal state changes, deterministic implementation
faults, and final persistence-contract failures from being collapsed into one
undifferentiated outcome.

## Execution boundary

This Phase 1.1 implementation contains no provider invocation, network client,
X integration, production-bot import, prompt, or response-generation path. Its
tests use only the checked-in wholly synthetic fixtures. It creates no ledger,
summary, annotation, or candidate reply for a real conversation and does not
authorize Phase 2 provider calls.
