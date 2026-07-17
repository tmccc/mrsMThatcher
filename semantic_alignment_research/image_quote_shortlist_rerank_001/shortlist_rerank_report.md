# Focused image-to-quote shortlist rerank

## Design

- Images: 24
- Canonical quote corpus used for local retrieval: 626
- Shortlist size per image: 25
- Providers: Grok, OpenAI, Claude and Gemini.
- All provider prompts were byte-identical and contained no held-out human decisions.
- Human calibration and held-out evaluation were split by image.

## Transport outcomes

| Provider | Model | Completed batches | Known spend | Ambiguous maximum exposure |
|---|---|---:|---:|---:|
| Grok | grok-4.5 | 4/4 | US$0.8970 | US$0.0000 |
| Openai | gpt-5.6-sol | 0/4 | US$0.0000 | US$4.9385 |
| Anthropic | claude-sonnet-4-6 | 0/4 | US$0.0000 | US$2.8472 |
| Gemini | gemini-3.1-pro-preview | 4/4 | US$1.6772 | US$0.0000 |

- Gemini completed all batches through the Developer API without retry or Vertex fallback.
- Grok completed all paid calls; an over-constrained local cross-field rule was corrected and all four saved responses were recovered offline without another call.
- OpenAI transmitted all four requests but each exceeded the 180-second read timeout. Their outcomes and billing are ambiguous, so they were not retried or scored.
- Claude's first attempts were rejected unbilled because its wire-schema subset does not support array minimums above one. After a provider-specific serializer correction, all four requests exceeded the 180-second read timeout; they are ambiguous and unscored.

## Results

| Provider | Images | Suitable | Unsure | Held-out precision | Held-out recall | Harmful rate |
|---|---:|---:|---:|---:|---:|---:|
| Grok | 24 | 30 | 0 | 25.0% | 8.3% | 4.7% |
| Openai | 0 | 0 | 0 | unavailable | unavailable | unavailable |
| Anthropic | 0 | 0 | 0 | unavailable | unavailable | unavailable |
| Gemini | 24 | 53 | 0 | 55.6% | 41.7% | 6.2% |

## Combined decision

- Complete providers: grok, gemini.
- Three-of-four majority pairs: None.
- Unanimous pairs: None.
- Majority held-out precision: unavailable.
- Majority held-out recall: unavailable.
- Majority harmful eligibility rate: unavailable.
- Available-provider majority threshold: 2.
- Available-provider majority pairs: 20.
- Available-provider held-out precision: 33.3%.
- Available-provider held-out recall: 8.3%.
- Known spend: US$2.5742.
- Ambiguous maximum exposure: US$7.7857.

## Interpretation

Gemini was materially more useful than Grok on the held-out human-reviewed pairs, but its 55.6% precision and 41.7% recall are not strong enough to define production eligibility. Grok's held-out recall was 8.3%.

The two available providers' intersection was more conservative but not safer: it recovered one of twelve held-out positives and included two held-out false positives. This does not support using provider agreement as the eligibility rule.

OpenAI and Claude remain methodologically useful independent judges, but this run did not measure their editorial quality. A future bounded compatibility run should use smaller response batches or provider-supported asynchronous polling, while preserving identical per-case content and the same held-out split.

No production selector or image metadata was changed. Provider votes remain research evidence only.
