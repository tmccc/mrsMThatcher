# Quote Research Retry Validation Report

## Manifest

The immutable validation manifest contained exactly 19 unique unresolved quotes: 6 transport failures, 11 missing-grounding cases, and 2 identity/structured-output cases. It had no overlap with completed, offline-recovered, or previously attempted retry-batch packets.

## Results

- Recovered: 17/19 (89%)
- Unresolved: 2/19
- Recovered by class: identity_and_structured_output 2, missing_grounding 9, transport_failures 6
- Unresolved by class: missing_grounding 2
- Developer completions: 17
- Vertex completions: 0
- Developer 429 responses: 0
- Vertex 429 responses: 0
- Run-wide Developer pause activated: False (None)
- Direct-to-Vertex cases after pause: 0
- Quote-identity rejections: 0
- Actual spend: $0.7519
- Possible ambiguous exposure: $0.0730
- Cost per recovered quote: $0.0442
- Elapsed time: 17.0 minutes

All 17 accepted packets have at least one provider-linked grounding source and support. Search-entry HTML and model-written URLs were not accepted independently. The 2 unresolved cases comprise missing_grounding 2.

## Recommendation

Across the staged retry batches, 68/80 cases recovered (85%) at a cumulative cost of $8.9691. This supports another bounded 30-item batch, not an unchecked remainder run. The untouched 90 candidates project to approximately $10.09 and about 76 recoveries. Preserve the same grounding, identity, timeout, pause and ceiling controls.

No production behavior was changed. No candidate outside the 20-item validation manifest was called.
