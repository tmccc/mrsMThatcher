# Proposition-ledger evidence transport v2

## Boundary and status

Phase 2B corrects the provider transport boundary without changing the
canonical semantic-delta or persisted-ledger contracts. The completed Phase 2A
run, its raw and parsed responses, its validation records, and its seven
materialised ledgers remain immutable experimental evidence. Phase 2A's severe
attrition was operationally real; this diagnostic work does not revise its
result or turn post-hoc recovery into a provider observation.

The Phase 2A system prompt told the provider to use a supplied Unicode
character-offset convention, but the conversational request supplied no such
convention. Exact evidence text is a semantic-selection decision. Current-turn
binding and character-offset arithmetic are deterministic transport work and
must not be delegated to the model.

## Two-schema contract

The provider returns
`proposition-ledger-xai-transport-delta-v2.0.0`. Deterministic local code maps
that object into the unchanged
`proposition-ledger-semantic-delta-v1.1.0`, validates it under the canonical
intended-pattern semantics, and passes it to the unchanged
`proposition-ledger-semantic-delta-materialiser-v2`.

The transport schema is derived mechanically from the canonical schema. Its
only semantic transformations are:

1. replace the top-level schema-version constant;
2. add the required canonical-schema-version constant;
3. replace the canonical evidence-span definition with the exact-text
   occurrence selector; and
4. update descriptions that specifically assign offset calculation to the
   provider.

All other properties, requirements, enums, constants, types, references,
combinators, numeric limits, and lifecycle rules remain structurally
equivalent. The tracked generator's check mode requires byte equality between
regeneration and the tracked schema. The established xAI transformation then
expands omitted `additionalProperties` defaults explicitly and removes only
redundant outer regex anchors for which the restricted proof succeeds. That
provider schema is a private generated artefact, not a new canonical schema.

## Evidence selector and resolver

A provider-visible evidence selector contains exactly:

```json
{
  "exact_text": "a non-empty literal current-turn substring",
  "occurrence_index": 0
}
```

`occurrence_index` is zero-based. Matches use ordinary Python `str` equality,
include overlaps, and are ordered by increasing Unicode-code-point start
position. There is no normalisation, case folding, whitespace collapsing,
punctuation repair, fuzzy matching, or quotation-mark substitution. A whole
turn may be cited at occurrence zero. Missing text and out-of-range occurrences
fail closed.

Schema-guided traversal resolves only locations derived from the canonical
evidence-span definition. For each selector, deterministic code inserts the
exact current turn ID and computes zero-based, Python-Unicode-code-point,
end-exclusive `start_char` and `end_char` values. It verifies the resulting
slice, rejects duplicate canonical spans, rewrites only the two schema-version
fields, and preserves every other semantic field. No partial canonical delta
is returned on failure.

The private resolution manifest stores JSON pointers, hashes and lengths of
evidence text, occurrence counts, selected indexes, calculated boundaries, and
resolved-span hashes. It does not need to reproduce evidence text in aggregate
reports.

## Corrected request boundary

Request revision `phase2b-exact-evidence-selector-v1` retains the two frozen
model identities, low reasoning effort, a 4096-token visible-output ceiling,
no tools, omitted `tool_choice`, no search or code execution, no streaming,
no persistence, no retry, and no fallback. The two genesis request
representations differ only by model identity.

The conversational user payload contains the current-turn inputs and a bounded
response-contract manifest of schema identities and hashes. It does not contain
the complete transport or provider schema. The complete xAI provider schema is
present once, and only once, in `response_format.schema`.

## Scope and next gate

The seven original `no_stable_issue` ledgers are audited mechanically in the
separate evidence-boundary post-mortem. Phase 2B makes no semantic judgment and
does not alter the `no_stable_issue` prompt instructions. That observation is
reserved for a later protocol decision.

No profile winner is selected and no effectiveness result is established. The
next possible step is a small synthetic Unicode, repetition, and
overlapping-match live probe. The 62-call development pilot must not be rerun
unless that probe passes. Phase 2B does not authorise that probe, a development
rerun, held-out use, the downstream four-arm experiment, production
integration, merge, or deployment.
