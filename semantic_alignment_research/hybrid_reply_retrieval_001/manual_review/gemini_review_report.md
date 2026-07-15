# Gemini Blind Retrieval Review

- Model: `gemini-3.1-pro-preview`
- Completed: 100/100
- Developer API: 100
- Vertex fallback: 0
- Known spend: $1.2038
- Developer paused: false

## Set-level decisions

- A_better: 2
- B_better: 5
- insufficient_context: 2
- neither_useful: 19
- no_historical_evidence: 42
- roughly_equal: 30

## Historical evidence desirability

- desirable: 20
- not_desirable: 77
- unclear: 3

## Justified response

- historical_context: 5
- historical_correction: 1
- humour_preferable: 9
- no_reply_preferable: 70
- researched_principle: 15

## Deblinded decisive preferences

- hybrid: 4
- lexical: 3

## Packet-level relevance

- hybrid: irrelevant 149, partially_relevant 28, relevant 49
- lexical: irrelevant 219, partially_relevant 42, relevant 39

## Interpretation

The evidence does not support promoting hybrid retrieval. Most conversations did not justify historical evidence, and only seven cases produced a decisive A/B preference. Hybrid remains suitable for shadow evaluation only.

Offline-normalised reviews: 10; omitted packet reasons normalised from Gemini's case-level rationale: 69.

The model reviewed the complete local conversational context and the same deterministic blind A/B evidence sets shown by the web reviewer. It made no Search-grounding or tool call. Human review files were not modified.
