# 250-case provider bake-off preflight

- Cases: 250
- Combined expected cost: $20.38
- Combined conservative known cost: $31.54
- Combined ceiling: $45.00

| Provider | Model | Input tokens | Expected output | Expected cost | Base maximum | Single retry reserve | All-retry exposure | Ceiling |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| grok | grok-4.5 | 517241 | 225000 | $2.38 | $3.43 | $0.01 | $3.43 | $10.00 |
| openai | gpt-5.6-sol | 517241 | 225000 | $9.34 | $14.59 | $0.06 | $14.59 | $15.00 |
| anthropic | claude-sonnet-4-6 | 517241 | 225000 | $4.93 | $7.55 | $0.03 | $7.55 | $10.00 |
| gemini | gemini-3.1-pro-preview | 517241 | 225000 | $3.73 | $5.83 | $0.02 | $5.83 | $10.00 |

All tools, search, grounding, retrieval and code execution are disabled. Four workers run concurrently, one active request per provider. Every next attempt is independently ceiling-gated. The theoretical all-case retry exposure cannot be incurred beyond the hard ceilings.

Projected sequential duration: approximately 100–170 minutes from 25-case observed latencies. Projected concurrent duration: approximately 30–50 minutes.
