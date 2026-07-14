# Quote Research Retry Validation Report

## Manifest

The immutable validation manifest contained exactly 20 unique unresolved quotes: 7 transport failures, 10 missing-grounding cases, and 3 identity/structured-output cases. It had no overlap with completed or offline-recovered packets.

## Results

- Recovered: 16/20 (80%)
- Unresolved: 4/20
- Recovered by class: identity_and_structured_output 3, missing_grounding 8, transport_failures 5
- Unresolved by class: missing_grounding 2, transport_failures 2
- Developer completions: 2
- Vertex completions: 14
- Developer 429 responses: 3
- Run-wide Developer pause activated: True (two_429s_for_first_provider_wide_quota_case)
- Direct-to-Vertex cases after pause: 17
- Quote-identity rejections: 0
- Actual spend: $2.9017
- Cost per recovered quote: $0.1814
- Elapsed time: 18.0 minutes

All 16 accepted packets have at least one provider-linked grounding source and support. Search-entry HTML and model-written URLs were not accepted independently. The four unresolved cases comprise missing_grounding 2, transport_failures 2.

## Recommendation

The observed 80% recovery rate and complete recovery of the three identity/structured-output validation cases justify continuing, but operationally in staged batches rather than one unchecked 150-case command. At the observed batch cost, the untouched 150 candidates project to approximately $21.76 and about 120 recoveries. Preserve the same grounding, identity, timeout, pause and ceiling controls, and review each stage before the next.

No production behavior was changed. No candidate outside the 20-item validation manifest was called.
