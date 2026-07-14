# Quote Research Retry Validation Report

## Manifest

The immutable validation manifest contained exactly 30 unique unresolved quotes: 10 transport failures, 18 missing-grounding cases, and 2 identity/structured-output cases. It had no overlap with completed, offline-recovered, or previously attempted retry-batch packets.

## Results

- Recovered: 24/30 (80%)
- Unresolved: 6/30
- Recovered by class: identity_and_structured_output 1, missing_grounding 15, transport_failures 8
- Unresolved by class: identity_and_structured_output 1, missing_grounding 3, transport_failures 2
- Developer completions: 24
- Vertex completions: 0
- Developer 429 responses: 0
- Vertex 429 responses: 0
- Run-wide Developer pause activated: False (None)
- Direct-to-Vertex cases after pause: 0
- Quote-identity rejections: 0
- Actual spend: $1.4764
- Possible ambiguous exposure: $0.0730
- Cost per recovered quote: $0.0615
- Elapsed time: 36.2 minutes

All 24 accepted packets have at least one provider-linked grounding source and support. Search-entry HTML and model-written URLs were not accepted independently. The 6 unresolved cases comprise identity_and_structured_output 1, missing_grounding 3, transport_failures 2.

## Recommendation

Across the staged retry batches, 135/140 cases recovered (96%) at a cumulative cost of $11.6534. This supports another bounded 30-item batch, not an unchecked remainder run. The untouched 30 candidates project to approximately $2.50 and about 29 recoveries. Preserve the same grounding, identity, timeout, pause and ceiling controls.

No production behavior was changed. No candidate outside this 30-item manifest was called.
