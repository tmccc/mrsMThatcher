# OpenAI/Claude image-quote compatibility pilot

## Design

- Images: 8 (2 calibration, 6 held out).
- One image and 25 unchanged quote candidates per request.
- OpenAI and Claude received byte-identical substantive prompts.
- Selection used the prior Grok/Gemini disagreement pattern, not held-out human labels.
- No tools, search, image generation or production writes were enabled.

## Outcomes

| Provider | Model | Completed images | Suitable pairs | Held-out precision | Held-out recall | Known spend | Ambiguous exposure |
|---|---|---:|---:|---:|---:|---:|---:|
| Grok | grok-4.5 | 8/8 | 10 | 100.0% | 11.1% | prior trial | none |
| Gemini | gemini-3.1-pro-preview | 8/8 | 25 | 66.7% | 44.4% | prior trial | none |
| OpenAI | gpt-5.6-sol | 8/8 | 12 | 100.0% | 22.2% | US$1.2183 | US$0.0000 |
| Claude | claude-sonnet-4-6 | 8/8 | 23 | 75.0% | 33.3% | US$0.7217 | US$0.0000 |

Three-of-four majority pairs: 11.
Held-out majority precision/recall: 100.0% / 22.2%.
Two-of-four pairs: 17; held-out precision/recall: 100.0% / 22.2%.
OpenAI or Claude uniquely recovered 1 of 9 held-out human-positive pairs.

## Interpretation

The one-image request design resolves the earlier OpenAI and Claude compatibility failure.
On this small held-out sample, adding those providers supplied only one unique true-positive rescue and did not improve consensus recall beyond 22.2%.
The result supports preserving these providers for bounded comparison, but does not justify a larger four-provider run or a production policy.

This bounded compatibility pilot is not a production selection policy.
