# Generation Prompt Pilot 001

## Executive result

- Cases: 20
- Images: 60 (20 per deterministic prompt style)
- Human publishable winner: 5/20
- Human none: 15/20
- Human unsure: 0/20
- Immediate-message judgements: {'yes': 0, 'partly': 7, 'no': 13}
- Leading style: first_impression_first (2 wins)
- Recommendation: **repeat another bounded generation pilot**

The redesigned prompts did not solve candidate quality in this deliberately difficult set. Automated scores were materially more favorable than the human publish decision, so they must not be used as a substitute for review.

## Architecture and isolation

Briefs were deterministic projections of cached quote semantic and visual-intent fingerprints. Three prompts used the same brief with mechanism-first, first-impression-first, or balanced-editorial ordering. Human labels and prior winner identities were absent. Generated files and outputs exist only in this versioned research directory.

## Execution and cost

- Generation: 60 completed, 3 confirmed transient attempts retried once, no third attempt.
- Estimated generation spend: $0.78 using the project-observed per-request rate.
- Automated analysis: 240 calls, known spend $2.114494.
- Approximate combined spend: $2.89; preflight ceiling: $20.
- No search, grounding, tools, simulator, production write or production behavior change.

## Style comparison

| Style | Human wins | First impression | Semantic | Tone | Editorial power | Cost/publishable win |
|---|---:|---:|---:|---:|---:|---:|
| mechanism_first | 2 | 74.6 | 65.0 | 70.8 | 61.7 | $0.13 |
| first_impression_first | 2 | 76.8 | 64.7 | 75.0 | 61.7 | $0.13 |
| balanced_editorial | 1 | 76.2 | 63.5 | 73.5 | 62.6 | $0.26 |

## Identity and quality

The briefs requested no specific non-Thatcher person. Identity checks are therefore recorded as not required rather than inventing intended identities. Existing semantic editorial-power and first-impression focal/salience fields provide the bounded editorial-quality view.

## Regression cases

- Everest: Tony selected none; immediate message partly matched. The note requires a UK flag and a composition where Everest is visibly taller than all surrounding peaks.
- Free trade: Tony selected Candidate B, the first-impression-first variant; immediate message partly matched.
- Detailed candidate scores are in `everest_generation_case.md` and `free_trade_generation_case.md`.

## Existing-image comparison

The pilot tested whether newly generated variants were publishable. It did not blindly expose production-winner identity in the review and therefore does not claim a controlled head-to-head win against the existing image. The historical Everest/free-trade outcomes remain regression context only.

## Limitations

This is a 20-case, difficulty-enriched sample with one human editor and one stochastic image per style. Prompt style is confounded with random generation variance. Fifteen none decisions leave only five style wins, far too few for choosing a production prompt. Automated scores substantially overestimated publishability.

## Decision

**repeat another bounded generation pilot**

A repeat should first improve brief-to-scene specificity and rendering constraints, especially concrete national symbols, physical scale relationships, and avoidance of generic staged political imagery. Production adoption or a limited trial is not justified by this run.
