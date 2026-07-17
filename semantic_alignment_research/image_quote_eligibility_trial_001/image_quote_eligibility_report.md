# Image-to-quote eligibility trial

## Scope

- Eligible quotation packets: 626
- Excluded unresolved quotations: 6
- Source-grounded images: 24
- Providers: Grok and Gemini, with byte-identical substantive prompts
- Human pairing labels were not included in provider inputs.
- The providers received source-grounded image metadata and canonical visual analysis, not human pairing decisions.
- This is an isolated research result; it does not alter production selection.

## Provider results

| Provider | Completed images | Suitable pairs | No-match images | Precision on reviewed pairs | Recall | Harmful eligibility rate |
|---|---:|---:|---:|---:|---:|---:|
| Grok | 24 | 0 | 24 | unavailable | 0.0% | 0.0% |
| Gemini | 23 | 26 | 5 | unavailable | 0.0% | 0.0% |

## Agreement and calibration

- Provider intersection: 0 pairs.
- Provider union: 26 pairs.
- Provider Jaccard agreement: 0.000.
- Consensus precision on the coherent human-reviewed subset: unavailable.
- Consensus recall on that subset: 0.0%.
- Consensus harmful eligibility rate: 0.0%.
- Current selector precision on the same subset: 7.9%.
- One contradictory human decision/note record was excluded: 1.
- Provider pairs outside the reviewed subset remain unvalidated and are not treated as correct.

## Transport and cost

- Gemini model: `gemini-3.1-pro-preview`.
- Grok model: `grok-4.3`.
- Gemini completed attempts: 4; Developer 429s: 0; direct-to-Vertex count: 0.
- Grok completed attempts: 4.
- Gemini known spend: US$2.7343.
- Grok known spend: US$2.8847.
- Known provider spend: US$5.6190.

## Recommendation

- Do not use cross-provider agreement as an eligibility gate from this run: the intersection is empty.
- Grok's all-empty result is too conservative and misses every coherent human-approved calibration pair.
- Gemini's suggestions are a useful 26-pair editorial review queue, but none overlap the 16 coherent human-approved calibration pairs; they are not validated positives.
- The metadata-plus-entire-corpus one-pass method is therefore not supported as a production replacement for the selector.
- A better next experiment would use candidate generation followed by a focused per-image rerank over a locally retrieved shortlist, with explicit negative calibration and no production changes.
