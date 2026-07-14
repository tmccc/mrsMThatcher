# Quote Research Retry Validation Report

## Manifest

The immutable validation manifest contained exactly 30 unique unresolved quotes: 10 transport failures, 18 missing-grounding cases, and 2 identity/structured-output cases. It had no overlap with completed, offline-recovered, or previously attempted retry-batch packets.

## Results

- Recovered: 11/30 (37%)
- Unresolved: 19/30
- Recovered by class: missing_grounding 7, transport_failures 4
- Unresolved by class: identity_and_structured_output 2, missing_grounding 11, transport_failures 6
- Developer completions: 1
- Vertex completions: 10
- Developer 429 responses: 3
- Vertex 429 responses: 31
- Run-wide Developer pause activated: True (two_429s_for_first_provider_wide_quota_case)
- Direct-to-Vertex cases after pause: 27
- Quote-identity rejections: 0
- Actual spend: $1.4479
- Cost per recovered quote: $0.1316
- Elapsed time: 19.1 minutes

All 11 accepted packets have at least one provider-linked grounding source and support. Search-entry HTML and model-written URLs were not accepted independently. The 19 unresolved cases comprise identity_and_structured_output 2, missing_grounding 11, transport_failures 6.

## Recommendation

Across the staged retry batches, 51/80 cases recovered (64%) at a cumulative cost of $8.2172. This batch should not be followed immediately: wait for Vertex capacity to recover, then reassess a bounded next batch. The untouched 90 candidates project to approximately $9.24 and about 57 recoveries. Preserve the same grounding, identity, timeout, pause and ceiling controls.

No production behavior was changed. No candidate outside the 20-item validation manifest was called.
