# Quote Research Retry Validation Report

## Manifest

The immutable validation manifest contained exactly 30 unique unresolved quotes: 10 transport failures, 18 missing-grounding cases, and 2 identity/structured-output cases. It had no overlap with completed, offline-recovered, or previously attempted retry-batch packets.

## Results

- Recovered: 24/30 (80%)
- Unresolved: 6/30
- Recovered by class: identity_and_structured_output 1, missing_grounding 17, transport_failures 6
- Unresolved by class: identity_and_structured_output 1, missing_grounding 1, transport_failures 4
- Developer completions: 0
- Vertex completions: 24
- Developer 429 responses: 2
- Run-wide Developer pause activated: True (two_429s_for_first_provider_wide_quota_case)
- Direct-to-Vertex cases after pause: 29
- Quote-identity rejections: 0
- Actual spend: $3.8675
- Cost per recovered quote: $0.1611
- Elapsed time: 132.8 minutes

All 24 accepted packets have at least one provider-linked grounding source and support. Search-entry HTML and model-written URLs were not accepted independently. The 6 unresolved cases comprise identity_and_structured_output 1, missing_grounding 1, transport_failures 4.

## Recommendation

Across the staged retry batches, 40/50 cases recovered (80%) at a cumulative cost of $6.7692. This justifies another bounded 30-item batch, not an unchecked remainder run. The untouched 120 candidates project to approximately $16.25 and about 96 recoveries. Preserve the same grounding, identity, timeout, pause and ceiling controls, and review the next stage before proceeding again.

No production behavior was changed. No candidate outside the 20-item validation manifest was called.
