# Blinded 25-case critic provider bake-off

> Provider identities remain sealed for human review. Comparative statistics and case judgements use Critic A/B labels.

## Models and pricing

- Models used (mapping sealed): `grok-4.5` and `gpt-5.6-sol`.
- Unit prices: Grok $2 input / $0.50 cached / $6 output; OpenAI $5 input / $0.50 cached / $30 output per million tokens.
- Both: low reasoning, 1,600 output-token cap, strict schema, no tools.

## Usage by model (not linked to Critic A/B)

- grok-4.5: calls=25, input=59837, cached=4736, reasoning=16939, output=17421, cost=$0.318730, mean latency=9.935s, retries=0, ambiguous=0
- gpt-5.6-sol: calls=25, input=43526, cached=0, reasoning=2550, output=16902, cost=$0.724690, mean latency=12.017s, retries=0, ambiguous=0

## Agreement

- Exact primary relationship: 36.0%
- Primary-or-secondary overlap: 84.0%
- Cohen's kappa: 0.2233009708737864
- Unrelated: {'Critic A': 7, 'Critic B': 2}
- Claimed consequence: {'Critic A': 4, 'Critic B': 5}
- Ideological substitution: {'Critic A': 6, 'Critic B': 10}
- Keep/replace: {'agreement': 1.0, 'Critic A': {'replace': 20, 'keep': 4, 'unsure': 1}, 'Critic B': {'replace': 20, 'keep': 4, 'unsure': 1}}

## Free-trade case

### Critic A
- Relationships: related_ideological_substitution / illustrates_claimed_consequence
- Relevance/directness: 38 / 18
- Mechanism/consequence/principle: 8 / 52 / 35
- Decision: replace
- Explanation: The quote credits freer post-war trade for growth and consumer gains; the image instead credits a female leader’s oratory for a left-to-right shift from industrial poverty to modern prosperity. Shared consequence of affluence is present but mechanism, specificity and framing are substituted, making the pairing ideologically mismatched and weak for editorial use.
- Stronger direction: ['busy commercial ports with cargo ships and cranes', 'shipping containers being loaded', 'crowded supermarkets or markets with imported goods and price tags', 'shoppers benefiting from lower prices', 'factories producing export goods', 'simple charts of rising trade or GDP']

### Critic B
- Relationships: illustrates_claimed_consequence / related_ideological_substitution
- Relevance/directness: 58 / 24
- Mechanism/consequence/principle: 10 / 72 / 56
- Decision: replace
- Explanation: The image strongly visualizes prosperity as an outcome, but it does not show the quotation’s causal mechanism of freer trade and payments. Instead, it assigns the transformation to forceful political leadership and an implied ideological transition. Its before-and-after contrast has editorial impact, yet the missing trade, price and competition evidence makes the pairing materially indirect and potentially misleading.
- Stronger direction: ['A busy commercial port with cargo ships, cranes and containers', 'A retail scene featuring varied imported goods and visible prices', 'A split image linking cross-border trade activity to consumer choice and lower prices', 'A post-war trade-growth graphic combined with commerce or manufacturing imagery']

## Provisional recommendation

Insufficient human evidence

Human preference review is required before preferring a provider. Higher average scores alone are not treated as better calibration.

## Blinded review

Run the loopback review command documented in the execution report. Provider mapping is stored separately with mode 0600.
