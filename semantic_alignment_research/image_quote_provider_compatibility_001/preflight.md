# OpenAI/Claude compatibility preflight

- Images: 8 (2 calibration, 6 held out)
- Calls: 16 (8 per provider)
- Expected new spend: US$2.2341
- Conservative new-call maximum: US$4.2832
- Hard new-run ceiling: US$6.00
- Prior ambiguous maximum exposure: US$7.7857 (reported separately)
- Read timeout: 360 seconds

| Candidate | Partition | Prior-provider stratum | Prompt SHA-256 |
|---|---|---|---|
| 0b42b54c4148ee2562fb | evaluation | grok_only | `44b32a72ab64315b5076ceb88b6ffc3e547318b5d665e061d7b02980d1e4e99c` |
| 0c4634d8356f42dc4e0c | evaluation | mixed_disagreement | `d69b8e9eda99b31b6fbbe436cf3fc1693f191bbf68ff5384b2d9b6a2dc9c7247` |
| 168392f289b5df25f16b | calibration | gemini_only | `b80d6bb366f097acb8ff34af8636a813e538c92ab5473c578ceaad79be384d8b` |
| 253f9adb9e67db0a67d7 | calibration | no_match | `b9576934449afc6fdff685116b8bfe4234b16f96c5c3a4237025c6139d0a81c0` |
| 305d002e01745c0acd9b | evaluation | gemini_only | `0c8d36a1e49a1b7d497eb58433eec987443ebe3bd7bdfe3e0cfcb97e3babf5af` |
| 5202e374b764482c4cda | evaluation | no_match | `5bfb592270a6840515550f6dd1ded20fc0ccc4ed65bd6a799283519ea8bd538e` |
| 7c9a2b387a67c9bd858c | evaluation | shared_only | `7e68a42d9d01ef8003e67d449cf5788e2c232a0222ab63bff401311ea991472a` |
| 7cdcd67a8066a3b12d28 | evaluation | mixed_disagreement | `8bbb52716ec499f02f71ca67accdda08642c05cad7a8346c3c64208e8ec82c5f` |

No network call was made during preparation.
