# Semantic alignment v2 validation execution report

Exact cumulative billed cost: `$5.714168`

## Stage usage

- **quote**: calls=632, successes=632, failures=0, input=530512, cached=452992, reasoning=239711, completion=337129, cost=$3.842576
- **image**: calls=79, successes=79, failures=0, input=146940, cached=59648, reasoning=0, completion=51850, cost=$0.515508
- **critic**: calls=150, successes=150, failures=0, input=340725, cached=105216, reasoning=58372, completion=80371, cost=$1.356084

## Validation

Score distribution: `{'0-39 mismatch': 128, '40-59 weak': 21, '60-74 indirect': 1}`
Claim relationships: `{'contradiction': 3, 'generic_ideological_substitution': 17, 'partial_support': 2, 'related_but_not_equivalent': 43, 'secondary_theme_only': 8, 'strong_support': 1, 'unrelated': 76}`
Human expected-category agreement: `0.0`
False positives: `0`
False negatives: `0`
Uncertain cases: `0`

## Free-trade case

- Quote core claim: Trade has been a great engine of post-war growth from which all, especially every consumer, have benefited through freer trade and payments that produced lower prices, more competition and faster growth.
- Quote primary claims: ['Trade has been a great engine of post-war growth.', 'All have gained from the greater freedom of trade and payments.', 'Freer trade has meant lower prices, more competition and faster growth.', 'Every consumer has benefited.']
- Image core implied claim: Strong leadership from the woman at the podium is presented as bridging and enabling the shift from industrial decay and hardship to clean, affluent urban life.
- Image dominant implied claims: ['The woman at the microphones is the central agent connecting a grim past to a thriving future.']
- Matched claims: []
- Unillustrated quote claims: ['Trade has been a great engine of post-war growth.', 'All have gained from the greater freedom of trade and payments.', 'Freer trade has meant lower prices, more competition and faster growth.', 'Every consumer has benefited.']
- Extraneous image claims: ['A resolute female political leader is the central agent of societal transformation.', 'Left side shows industrial pollution, rundown housing, and economic hardship.', 'Right side shows clean modern prosperity with skyscrapers and cafés.', 'Formal podium speechmaking bridges past decay to future affluence.', 'Authoritative female leadership drives urban and economic renewal.']
- Claim relationship: unrelated
- Alignment/directness: 12 / 8
- Category: unrelated
- Explanation: The quote asserts freer post-war trade and payments as the engine of growth, lower prices, competition, and universal consumer gains. The image instead centers a female political leader at a podium as the agent of a before-and-after shift from polluted industrial poverty to modern urban prosperity. No trade, shipping, markets, prices, competition, or consumer-purchase visuals appear; leadership and urban renewal dominate, introducing claims the quote never requires and leaving every primary trade claim unillustrated.
- Stronger direction: ['Busy commercial ports with cargo ships and cranes', 'Shipping containers being loaded or unloaded', 'Crowded retail markets or supermarkets with diverse imported goods', 'Shoppers examining price tags and products', 'Factories and assembly lines producing export goods', 'Currency exchange or banking counters handling international payments', 'Simple line charts of rising post-war trade volumes or GDP growth', 'Editorial symbols of open borders or broken trade barriers']
- Human expected category: related_but_indirect
