# 250-case human validation

## Executive summary

Tony reviewed all 250 cases: {'replace': 205, 'keep': 45}. Four-provider and meta-critic validation covers 250 cases; the 0 cases missing Gemini remain separate.

The meta-critic resolved 210/250 complete cases and achieved 95.7% actionable accuracy (201 correct, 0 false keeps, 9 false replaces). Its 95% Wilson interval is 92.1%–97.7%.

Gemini was the lone keeper in 15 cases; Tony kept 8 and replaced 7. This directly measures whether Gemini rescued useful images.

## Systems

| System | Available | Coverage | Accuracy | Correct | False keep | False replace | Unsure/defer |
|---|---:|---:|---:|---:|---:|---:|---:|
| grok | 250 | 95.6% | 89.5% | 214 | 2 | 23 | 11 |
| openai | 250 | 97.6% | 88.9% | 217 | 3 | 24 | 6 |
| anthropic | 250 | 85.2% | 93.0% | 198 | 1 | 14 | 37 |
| gemini | 250 | 97.6% | 95.1% | 232 | 10 | 2 | 6 |
| majority_vote | 250 | 85.6% | 95.3% | 204 | 0 | 10 | 36 |
| meta_critic | 250 | 84.0% | 95.7% | 201 | 0 | 9 | 40 |

## Policies A-H

| Policy | Coverage | Accuracy | Correct | False keep | False replace | Deferred |
|---|---:|---:|---:|---:|---:|---:|
| A | 72.4% | 99.4% | 180 | 0 | 1 | 69 |
| B | 85.6% | 95.3% | 204 | 0 | 10 | 36 |
| C | 84.0% | 95.7% | 201 | 0 | 9 | 40 |
| D | 94.4% | 92.4% | 218 | 0 | 18 | 14 |
| E | 78.0% | 97.4% | 190 | 0 | 5 | 55 |
| F | 85.6% | 95.3% | 204 | 0 | 10 | 36 |
| G | 71.6% | 99.4% | 178 | 0 | 1 | 71 |
| H | 85.6% | 95.3% | 204 | 0 | 10 | 36 |

## Disagreement bands

| Band | Cases | Coverage | Accuracy | False keep | False replace |
|---|---:|---:|---:|---:|---:|
| low | 62 | 98.4% | 100.0% | 0 | 0 |
| moderate | 174 | 85.6% | 94.0% | 0 | 9 |
| high | 14 | 0.0% | unavailable | 0 | 0 |
| extreme | 0 | unavailable | unavailable | 0 | 0 |

## Interpretation

- Gemini-only keeps: 8/15 agreed with Tony; 7 were false keeps.
- Meta acceptability entered directly by Tony: {'yes': 221, 'no': 11, 'uncertain': 18}.
- Provider metrics use each provider’s available denominator; Gemini uses 227, the others 250.
- Majority vote uses all available providers and returns unsure on ties.
- No weights or rules were changed.

## Outputs

- `validation_summary.json`
- `system_accuracy.csv`
- `policy_accuracy.csv`
- `validation_report.md`

All calculations are offline and deterministic. No provider calls were made and no raw results were modified.
