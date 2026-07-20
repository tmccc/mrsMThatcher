# AI-first Reply Strategy Offline Evaluation

Strategy: `ai-first-reply-v3`
Network/model calls: **0 / 0**

## Fixtures

- Adversarial cases: 14 (14 expected rejects)
- Valid cases: 6 across 5 modes
- All valid fixture modes recognised: True

## Saved History

- V1 history records: 28
- Reply texts within the current deterministic envelope: 27
- Reply texts rejected by the current deterministic envelope: 1
- Semantic replay: `not_run_without_an_offline_model_or_remote_call`

## Local Evidence

- Retrieval queries: 5
- Latency p50/p95/max: 2.021 / 4.258 / 4.258 ms

## Cost Estimate

- Estimated non-factual reply: 0.0123587 USD
- Estimated factual reply: 0.01853805 USD
- Six-call revision envelope: 0.0370761 USD
- Caveat: The new prompts and structured outputs were not billed offline; these are historical-call extrapolations, not quotations.

## Scope

Deterministic and scripted safety behaviour was exercised offline. Natural-language quality on recent live candidates requires a separately authorised non-posting model pilot; no model was contacted by this evaluation.
