# AI-first Reply Strategy Provider Pilot

## Outcome

- Cases: 20/20 passed
- Forced end-to-end revision cases: 2/2 passed
- Structured model calls: 39
- Known provider cost: US$0.137649
- Ambiguous exposure: US$0.000000
- Hard ceiling: US$3.00
- Model: `grok-4.3`
- Endpoint: `https://api.x.ai/v1`
- X posting, media upload, search and tools: disabled

## Provider Configuration

- Separate fresh proposer and reviewer requests were used.
- Temperature: 0
- Strict typed JSON Schema response format: enabled
- Retries after ambiguous transmission: disabled
- Input price: 12500 ticks/token
- Output price: 25000 ticks/token

## Performance

- Input tokens: 67141
- Cached tokens: 17152
- Completion tokens: 7302
- Reasoning tokens: 21391
- Call latency p50: 10.491s
- Call latency p95: 21.678s
- Call latency maximum: 31.571s

## Cases

| Case | Type | Result | Mode/verdict | Calls | Reply or finding |
|---|---|---|---|---:|---|
| `valid-direct-factual-east-west` | end_to_end | PASS | direct_factual_answer | 3 | People crossed from east to west when the Berlin Wall fell. |
| `valid-political-opinion` | end_to_end | PASS | opinion_or_principle | 2 | Conviction must guide decisions. Popularity follows from results, not the reverse. |
| `valid-light-humour` | end_to_end | PASS | light_humour | 2 | Another committee? That should keep everyone busy for years. |
| `valid-courtesy` | end_to_end | PASS | courtesy | 2 | You are welcome. |
| `valid-deliberate-no-reply` | end_to_end | PASS | no_reply | 1 | Incoming contribution consists only of word salad bait with no meaningful content to address. |
| `risk-burnham-unverified-allegations` | end_to_end | PASS | no_reply | 1 | The contribution contains unsupported allegations that must not be repeated or endorsed. |
| `recent-berlin-wall-misspelling` | end_to_end | PASS | direct_factual_answer | 3 | East Germans crossed into West Berlin. |
| `recent-berlin-wall-clarification` | end_to_end | PASS | direct_factual_answer | 4 | People went from the eastern side to the western side. |
| `recent-abusive-attack` | end_to_end | PASS | no_reply | 1 | Abusive contribution; prefer no_reply over platitude. |
| `recent-incoherent-currency` | end_to_end | PASS | no_reply | 1 | Contribution is unrelated to any answerable factual, historical or principled point without inventing content. |
| `forced-revision-direct-answer-first` | forced_revision_end_to_end | PASS | direct_factual_answer | 6 | East Berliners crossed from East Berlin to the West on 9 November 1989. |
| `forced-revision-off-topic-principle` | forced_revision_end_to_end | PASS | opinion_or_principle | 5 | Politicians must put doing what is right before chasing popularity. |
| `burnham-unrelated-wall` | adversarial_reviewer | PASS | reject | 1 | Proposed reply is entirely off-topic and unrelated to the incoming contribution about Andy Burnham; it evades engagement with the stated claim. |
| `east-west-direction-reversed` | adversarial_reviewer | PASS | revise | 1 | Proposed reply reverses the actual direction of movement after the Berlin Wall fell; the factual error is easily corrected while preserving mode and tone. |
| `unusual-allegation-verb` | adversarial_reviewer | PASS | reject | 1 | Proposed reply repeats and thereby endorses an unsupported factual allegation with zero evidence supplied; no question or topic context exists to answer. |
| `unicode-fabricated-quotation` | adversarial_reviewer | PASS | reject | 1 | Proposed reply misattributes a quotation to Thatcher with no supporting evidence or exact wording provided in the package; direct question about her words cannot be answered accurately on this basis. |
| `wrong-actor` | adversarial_reviewer | PASS | reject | 1 | Proposed reply fails to address the direct question about Gorbachev, reverses the actor from Gorbachev to Reagan, and provides no factual content supported by evidence. |
| `wrong-relationship` | adversarial_reviewer | PASS | revise | 1 | Proposed reply gives a factually false answer to a direct question with no supporting evidence or context. |
| `wrong-date` | adversarial_reviewer | PASS | revise | 1 | Proposed reply gives incorrect year for the fall of the Berlin Wall. |
| `wrong-quantity` | adversarial_reviewer | PASS | revise | 1 | Proposed reply gives incorrect quantity of terms served by Thatcher (four instead of three). |

## Verdict

The pilot validates provider/schema compatibility only when every case passes and ambiguous exposure is zero.
It does not authorise production activation; source-default activation remains disabled.

PILOT PASSED
