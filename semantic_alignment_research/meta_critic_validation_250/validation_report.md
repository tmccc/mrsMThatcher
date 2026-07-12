# 250-case human validation

## Executive summary

Tony reviewed all 250 cases: {'replace': 205, 'keep': 45}. Four-provider and meta-critic validation covers 227 cases; the 23 cases missing Gemini remain separate.

The meta-critic resolved 190/227 complete cases and achieved 95.3% actionable accuracy (181 correct, 0 false keeps, 9 false replaces). Its 95% Wilson interval is 91.2%–97.5%.

Gemini was the lone keeper in 14 cases; Tony kept 8 and replaced 6. This directly measures whether Gemini rescued useful images.

## Systems

| System | Available | Coverage | Accuracy | Correct | False keep | False replace | Unsure/defer |
|---|---:|---:|---:|---:|---:|---:|---:|
| grok | 250 | 95.6% | 89.5% | 214 | 2 | 23 | 11 |
| openai | 250 | 97.6% | 88.9% | 217 | 3 | 24 | 6 |
| anthropic | 250 | 85.2% | 93.0% | 198 | 1 | 14 | 37 |
| gemini | 227 | 97.4% | 95.5% | 211 | 8 | 2 | 6 |
| majority_vote | 250 | 86.4% | 94.9% | 205 | 0 | 11 | 34 |
| meta_critic | 227 | 83.7% | 95.3% | 181 | 0 | 9 | 37 |

## Policies A-H

| Policy | Coverage | Accuracy | Correct | False keep | False replace | Deferred |
|---|---:|---:|---:|---:|---:|---:|
| A | 71.4% | 99.4% | 161 | 0 | 1 | 65 |
| B | 85.0% | 94.8% | 183 | 0 | 10 | 34 |
| C | 83.7% | 95.3% | 181 | 0 | 9 | 37 |
| D | 94.3% | 91.6% | 196 | 0 | 18 | 13 |
| E | 77.5% | 97.2% | 171 | 0 | 5 | 51 |
| F | 85.0% | 94.8% | 183 | 0 | 10 | 34 |
| G | 70.5% | 99.4% | 159 | 0 | 1 | 67 |
| H | 85.0% | 94.8% | 183 | 0 | 10 | 34 |

## Disagreement bands

| Band | Cases | Coverage | Accuracy | False keep | False replace |
|---|---:|---:|---:|---:|---:|
| low | 54 | 100.0% | 100.0% | 0 | 0 |
| moderate | 160 | 85.0% | 93.4% | 0 | 9 |
| high | 13 | 0.0% | unavailable | 0 | 0 |
| extreme | 0 | unavailable | unavailable | 0 | 0 |

## Interpretation

- Gemini-only keeps: 8/14 agreed with Tony; 6 were false keeps.
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
