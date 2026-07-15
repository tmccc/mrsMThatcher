# Four-AI Blind Retrieval Review

The four models reviewed the same 100 context-complete cases, the same blind A/B assignments, and byte-identical substantive prompts. No model received retriever identity, human labels, external tools, or Search grounding.

Prompt parity was verified against all 20 immutable Gemini batch hashes. The human-review file was read only and its SHA-256 is recorded in the machine-readable comparison.

## Provider results

| Provider | Model | Direct | Offline-normalised | Evidence desirable | No historical evidence | Hybrid wins when desirable | Lexical wins when desirable | Cost |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| anthropic | `claude-fable-5` | 100 | 0 | 25 | 60 | 7 | 4 | $5.3309 |
| gemini | `gemini-3.1-pro-preview` | 90 | 10 | 20 | 42 | 4 | 2 | $1.2038 |
| grok | `grok-4.5` | 65 | 35 | 26 | 54 | 4 | 1 | $1.0020 |
| openai | `gpt-5.6-sol` | 95 | 5 | 28 | 38 | 12 | 4 | $2.4625 |

## Consensus

- three_to_one: 33
- two_or_less: 35
- unanimous: 32

Evidence-desirable votes per case:

- 0 of 4: 62
- 1 of 4: 12
- 2 of 4: 4
- 3 of 4: 9
- 4 of 4: 13

Among cases where at least three providers found historical evidence desirable:

- hybrid: 11
- lexical: 4
- no_decisive_vote: 7

## Packet assessments

| Provider | Retriever | Relevant | Partial | Irrelevant | Unsafe |
|---|---|---:|---:|---:|---:|
| anthropic | lexical | 65 | 76 | 139 | 20 |
| anthropic | hybrid | 70 | 64 | 84 | 8 |
| gemini | lexical | 39 | 42 | 219 | 0 |
| gemini | hybrid | 49 | 28 | 149 | 0 |
| grok | lexical | 62 | 83 | 147 | 8 |
| grok | hybrid | 74 | 62 | 88 | 2 |
| openai | lexical | 46 | 60 | 167 | 27 |
| openai | hybrid | 58 | 53 | 105 | 10 |

## Pairwise exact agreement

| Providers | Set choice | Evidence desirability | Intervention |
|---|---:|---:|---:|
| anthropic / gemini | 59% | 83% | 57% |
| anthropic / grok | 75% | 83% | 65% |
| anthropic / openai | 54% | 75% | 62% |
| gemini / grok | 60% | 82% | 64% |
| gemini / openai | 46% | 78% | 72% |
| grok / openai | 44% | 79% | 64% |

## Schema reliability

- anthropic: 0 offline-normalised reviews; 0 preserved logical inconsistencies; 0 unrecognised reason tags preserved separately.
- gemini: 10 offline-normalised reviews; 0 preserved logical inconsistencies; 0 unrecognised reason tags preserved separately.
- grok: 35 offline-normalised reviews; 0 preserved logical inconsistencies; 79 unrecognised reason tags preserved separately.
- openai: 5 offline-normalised reviews; 1 preserved logical inconsistencies; 0 unrecognised reason tags preserved separately.

## Interpretation

The providers agree much more consistently on whether history is desirable than on the exact retrieval-set choice. In the 22 cases where at least three providers wanted historical evidence, the decisive votes favoured hybrid retrieval in 11 cases and lexical retrieval in 4; 7 had no decisive retriever vote. Packet-level assessments also favour hybrid retrieval, but these correlated model judgements are not independent ground truth. Hybrid retrieval should remain shadow-only pending stronger validation of the high-value disagreement cases.

Known four-provider spend: $9.9992.
