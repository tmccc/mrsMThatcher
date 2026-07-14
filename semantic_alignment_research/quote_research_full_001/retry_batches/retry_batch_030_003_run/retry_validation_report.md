# Quote Research Retry Validation Report

## Manifest

The immutable validation manifest contained exactly 30 unique unresolved quotes: 10 transport failures, 18 missing-grounding cases, and 2 identity/structured-output cases. It had no overlap with completed, offline-recovered, or previously attempted retry-batch packets.

## Results

- Recovered: 27/30 (90%)
- Unresolved: 3/30
- Recovered by class: identity_and_structured_output 1, missing_grounding 17, transport_failures 9
- Unresolved by class: identity_and_structured_output 1, missing_grounding 1, transport_failures 1
- Developer completions: 27
- Vertex completions: 0
- Developer 429 responses: 0
- Vertex 429 responses: 0
- Run-wide Developer pause activated: False (None)
- Direct-to-Vertex cases after pause: 0
- Quote-identity rejections: 0
- Actual spend: $1.2079
- Possible ambiguous exposure: $0.0000
- Cost per recovered quote: $0.0447
- Elapsed time: 12.4 minutes

All 27 accepted packets have at least one provider-linked grounding source and support. Search-entry HTML and model-written URLs were not accepted independently. The 3 unresolved cases comprise identity_and_structured_output 1, missing_grounding 1, transport_failures 1.

## Recommendation

Across the staged retry batches, 106/110 cases recovered (96%) at a cumulative cost of $10.1770. This supports another bounded 30-item batch, not an unchecked remainder run. The untouched 60 candidates project to approximately $5.55 and about 58 recoveries. Preserve the same grounding, identity, timeout, pause and ceiling controls.

No production behavior was changed. No candidate outside this 30-item manifest was called.
