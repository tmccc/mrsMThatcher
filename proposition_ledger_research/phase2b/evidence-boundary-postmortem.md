# Proposition-ledger Phase 2B evidence-boundary post-mortem

## Scope and interpretation

This bounded post-mortem reprocessed all 21 attempted Phase 2A calls from the
completed, checksum-verified private run. It used only already selected and
directly exposed development material and made no provider calls. Phase 2A
remains immutable, and its severe operational attrition remains its real
experimental outcome:
`phase2a_development_pilot_completed_with_profile_attrition_review_pending`.
The diagnostic counts below are not corrected Phase 2A observations.

The Phase 2A prompt referred to a supplied Unicode character-offset convention,
but the request did not actually supply one. The post-mortem therefore tested a
single bounded question: what can be diagnosed by ignoring provider-supplied
turn IDs and coordinates and searching for the provider's exact evidence text
literally in the trusted current turn?

## Saved-response findings

- Attempted responses classified: 21.
- Strictly parsed responses: 20.
- Evidence spans examined: 112.
- Responses uniquely recoverable from literal exact text: 20 (Grok 4.3: 13;
  Grok 4.6: 7).
- Responses requiring occurrence disambiguation: 0.
- Responses with absent exact text: 0.
- Unparsed responses: 1 (Grok 4.6).

All 112 parsed evidence strings occurred exactly once in their trusted current
turn. Among their old spans, 109 had incorrect coordinates, 9 had an end beyond
the Python string length, 2 matched a code-point inclusive-end signature, 107
had no recognised bounded coordinate signature, and 15 carried the separate
whitespace-or-punctuation diagnostic signature. Three spans were already
canonical even though another span in the same response caused that response's
evidence validation to fail. There were no wrong-turn, non-integer, boolean,
negative-start, non-positive-length, repeated-text, duplicate-resolved-span,
UTF-16, UTF-8, normalisation, or multi-convention signatures. The one unparsed
response was recorded as not assessable for span diagnosis.

The strict-JSON failure was `truncated_json`: the saved bytes end within an
unterminated JSON string/container, the finish reason was `REASON_MAX_LEN`, and
the saved completion-token count was 4096. No partial parse or repair was used.

## Diagnostic counterfactual

For each of the 20 uniquely recoverable responses, deterministic code replaced
only the old evidence turn binding and coordinates with the sole literal match,
then reran canonical validation, binding, semantic-reference validation, the
unchanged materialiser, and persisted-ledger validation. Every output is
labelled `posthoc_unique_exact_text_resolution` and
`diagnostic_counterfactual_only`.

Canonical validation passed for 13 of 13 Grok 4.3 responses and 7 of 7 Grok
4.6 responses. Persisted diagnostic materialisation passed for 13 of 13 Grok
4.3 responses and 4 of 7 Grok 4.6 responses. The three downstream failures are
retained as deterministic diagnostic findings. None of these counts revises
the original seven materialised Phase 2A responses, supports a profile winner,
or establishes semantic correctness or ledger effectiveness.

## Original materialised-output audit

The seven original materialised ledgers were inspected mechanically. All seven
had `no_stable_issue` as an abstention, zero propositions, zero proposition
updates, zero commitments, zero issues, zero obligations, zero relations, zero
answer targets, and zero warnings. Accordingly:

- `no_stable_issue` with zero propositions: 7; with one or more: 0;
- `no_stable_issue` with zero commitments: 7; with one or more: 0;
- complete extraction with no semantic additions: 0; and
- abstained extraction with no semantic additions: 7.

That uniform mechanical pattern supports further investigation of
`no_stable_issue` in a later protocol decision, but it is not a human semantic
judgment. Phase 2B did not tune or revise those prompt instructions.

## Boundary conclusion

Exact evidence text belongs to model semantic selection. Current-turn identity
and character arithmetic belong to deterministic code. Literal exact-text
resolution must remain fail-closed: no normalisation, fuzzy matching, case
folding, whitespace repair, punctuation repair, or guessed occurrence is
permitted. Repeated text must be selected explicitly by zero-based occurrence
index.

The complete provider schema is also removed from the conversational user
payload and remains supplied once through structured `response_format`. Across
the 21 reconstructed Phase 2A calls, user-payload size fell from 625,491 bytes
to 44,652 bytes, a total reduction of 580,839 bytes. This is a byte comparison,
not a counterfactual token or monetary-savings claim.

The next possible gate is a small synthetic Unicode, repetition, and
overlapping-match live probe. This phase does not run or authorise that probe.
The 62-call development pilot must not be rerun until the future probe passes.
No held-out evaluation, profile selection, human scoring, downstream four-arm
experiment, production integration, merge, or deployment is authorised here.
