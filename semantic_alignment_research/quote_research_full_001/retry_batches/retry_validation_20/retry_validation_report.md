# Quote Research Retry Validation Report

## Manifest

The immutable validation manifest contained exactly 20 unique unresolved quotes: 10 transport failures, 7 missing-grounding cases, and 3 identity/structured-output cases. It had no overlap with completed, offline-recovered, or previously attempted retry-batch packets.

## Results

- Recovered: 16/20 (80%)
- Unresolved: 4/20
- Recovered by class: identity_and_structured_output 3, missing_grounding 6, transport_failures 7
- Unresolved by class: missing_grounding 1, transport_failures 3
- Developer completions: 16
- Vertex completions: 0
- Developer 429 responses: 0
- Vertex 429 responses: 0
- Run-wide Developer pause activated: False (None)
- Direct-to-Vertex cases after pause: 0
- Quote-identity rejections: 0
- Actual spend: $1.2170
- Possible ambiguous exposure: $0.0000
- Cost per recovered quote: $0.0761
- Elapsed time: 32.9 minutes

All 16 accepted packets have at least one provider-linked grounding source and support. Search-entry HTML and model-written URLs were not accepted independently. The 4 unresolved cases comprise missing_grounding 1, transport_failures 3.

## Recommendation

Across the staged retry batches, 164/170 cases recovered (96%) at a cumulative cost of $14.2084. This supports another bounded 30-item batch, not an unchecked remainder run. The untouched 0 candidates project to approximately $0.00 and about 0 recoveries. Preserve the same grounding, identity, timeout, pause and ceiling controls.

No production behavior was changed. No candidate outside this 20-item manifest was called.
