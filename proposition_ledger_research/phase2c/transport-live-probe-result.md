# Proposition-ledger Phase 2C transport live-probe result

The wholly synthetic Phase 2C smoke test completed on 2026-09-01 from pre-call commit `870ab06a8006b7beb13802e9067607f3a29dbfbe`.

## Aggregate outcome

All six planned xAI calls were attempted exactly once and completed. Server acceptance, transport-to-canonical resolution, canonical validation, deterministic materialisation, persisted-ledger validation, and the hidden case expectations each passed 6/6. There were zero retries, repair calls, fallback calls, failed calls, or blocked calls.

| Result | `grok-4.3` | `grok-4.6` |
| --- | ---: | ---: |
| Calls completed | 3/3 | 3/3 |
| Two-turn formatting chain completed | yes | yes |
| Exact Unicode/CRLF/tab/two-space evidence passed | yes | yes |
| Repeated phrase, occurrence index 1, passed | yes | yes |
| Three overlapping `aa` matches observed and index 1 resolved to the middle match | yes | yes |

The repeated phrase resolved to Unicode code-point offsets `[46, 62)`. The overlapping `aa` selector observed three matches and resolved occurrence index 1 to `[8, 10)`. The complete formatting turn round-tripped exactly and resolved to `[0, 42)`.

## Aggregate usage

The six calls used 57,112 prompt tokens, 3,382 completion tokens, 4,102 reasoning tokens, and 64,596 total tokens. The recorded aggregate latency was 91.643502 seconds.

## Deterministic verification

Offline `--verify` strictly reprocessed all six saved raw responses, reproduced the canonical deltas and materialised ledgers, left the call log unchanged, and passed the 38-file checksum inventory. A separate `sha256sum -c SHA256SUMS` check also passed every entry.

Only invented conversations were used. No real or held-out conversation was read, no model-quality comparison or winner selection was performed, the Phase 2A pilot was not rerun, production was untouched, and nothing was merged or deployed.
