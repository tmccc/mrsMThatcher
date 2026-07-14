# Offline Quote Research Recovery

- Run: `quote_research_full_001`
- Original completed packets: 439
- Original non-completed records audited: 193
- Recoverable from preserved raw evidence: 23
- Still requiring parser/schema work only: 0
- Lacking usable response content: 46
- Paid retry candidates: 170
- Genuine confirmed historical failures: 0
- Source coverage among recovered packets: 100.0%
- Applied to main packet collection: True

## Failure Taxonomy

- missing_grounded_source: 101
- vertex_grounding_metadata_extraction_incompatibility: 0
- malformed_or_truncated_json: 2
- schema_validation_error: 3
- quote_identity_changed: 28
- timeout_or_transport_failure: 53
- no_response_received: 3
- cancelled_before_send: 3
- genuine_historical_verification_failure: 0
- other: 0

Recovery accepts citations only where preserved provider grounding chunks are linked by grounding-support metadata. Model-written URLs and search-entry HTML alone are insufficient. Raw responses and the original attempt ledger remain immutable.
