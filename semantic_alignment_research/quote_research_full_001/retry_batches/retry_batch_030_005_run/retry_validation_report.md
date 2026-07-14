# Quote Research Retry Validation Report

## Manifest

The immutable validation manifest contained exactly 30 unique unresolved quotes: 12 transport failures, 17 missing-grounding cases, and 1 identity/structured-output cases. It had no overlap with completed, offline-recovered, or previously attempted retry-batch packets.

## Results

- Recovered: 29/30 (97%)
- Unresolved: 1/30
- Recovered by class: identity_and_structured_output 1, missing_grounding 17, transport_failures 11
- Unresolved by class: transport_failures 1
- Developer completions: 29
- Vertex completions: 0
- Developer 429 responses: 0
- Vertex 429 responses: 0
- Run-wide Developer pause activated: False (None)
- Direct-to-Vertex cases after pause: 0
- Quote-identity rejections: 0
- Actual spend: $1.3380
- Possible ambiguous exposure: $0.0000
- Cost per recovered quote: $0.0461
- Elapsed time: 13.8 minutes

All 29 accepted packets have at least one provider-linked grounding source and support. Search-entry HTML and model-written URLs were not accepted independently. The 1 unresolved cases comprise transport_failures 1.

## Recommendation

Across the staged retry batches, 164/170 cases recovered (96%) at a cumulative cost of $12.9914. This supports another bounded 30-item batch, not an unchecked remainder run. The untouched 0 candidates project to approximately $0.00 and about 0 recoveries. Preserve the same grounding, identity, timeout, pause and ceiling controls.

No production behavior was changed. No candidate outside this 30-item manifest was called.
