# Quote Research Recovery Stage Meta Report

## Outcome

- Unique paid-retry candidates: 170
- Recovered: 164 (96.5%)
- Still unresolved: 6
- Final completed packet count: 626/632
- Known retry spend: $14.2084
- Possible ambiguous exposure: $0.1460
- Developer completions across run records: 116
- Vertex completions across run records: 48
- Developer / Vertex 429 responses: 8 / 31

## Batches

| Manifest | Targeted | Recovered | Unresolved | Developer | Vertex | Known spend |
|---|---:|---:|---:|---:|---:|---:|
| retry_analysis/retry_manifest_20.json | 20 | 16 | 4 | 2 | 14 | $2.9017 |
| retry_batches/retry_batch_030_001.json | 30 | 24 | 6 | 0 | 24 | $3.8675 |
| retry_batches/retry_batch_030_002.json | 30 | 11 | 19 | 1 | 10 | $1.4479 |
| retry_batches/retry_batch_030_002_recovery_19.json | 19 | 17 | 2 | 17 | 0 | $0.7519 |
| retry_batches/retry_batch_030_003.json | 30 | 27 | 3 | 27 | 0 | $1.2079 |
| retry_batches/retry_batch_030_004.json | 30 | 24 | 6 | 24 | 0 | $1.4764 |
| retry_batches/retry_batch_030_005.json | 30 | 29 | 1 | 29 | 0 | $1.3380 |
| retry_batches/retry_batch_recovery_remaining_020.json | 20 | 16 | 4 | 16 | 0 | $1.2170 |

## Stop Decision

All 170 original paid-retry candidates received a staged retry. The final 6 unresolved quotations have also exhausted one separate bounded recovery cycle, so another paid retry is not justified without a new method or explicit exception. The final unresolved IDs are recorded in the machine-readable report.

No production behavior was changed. Original provider attempts and raw responses remain preserved. Nothing was staged, committed, pushed, or deployed.
