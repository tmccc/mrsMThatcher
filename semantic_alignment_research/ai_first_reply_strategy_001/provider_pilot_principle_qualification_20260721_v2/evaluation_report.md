# AI-first Reply Strategy Corpus Matrix Evaluation

## Status

- Offline fixtures: 6100 across 610 quotations and 10 scenarios
- Paid cases completed: 80/80
- Valid quality cases: 80 (0 paid cases excluded for placeholder quote text)
- Distinct quotations exercised with valid text: 48/610
- Deterministic grades passed: 67/80
- Report-only grading corrections: 4
- Operational failures: 12
- Approved replies: 19
- Deliberate no-reply outcomes: 61
- Safe principle-response opportunities missed: 51
- Revisions: 33
- Structured model calls: 220
- Known provider cost: US$1.068299
- Ambiguous exposure: US$0.000000
- Hard ceiling: US$2.50
- X posting, media upload and production-state writes: zero

## Scenarios

| Scenario | Completed | Invalid fixture | Valid | Passed | Failed | Approved | No reply | Missed response | Revisions | Calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `direct_context_question` | 0/0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `direct_meaning_question` | 5/5 | 0 | 5 | 2 | 3 | 1 | 4 | 4 | 0 | 16 |
| `quotation_verification` | 0/0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `principle_agreement` | 30/30 | 0 | 30 | 26 | 4 | 13 | 17 | 17 | 22 | 108 |
| `principle_challenge` | 30/30 | 0 | 30 | 24 | 6 | 0 | 30 | 30 | 11 | 71 |
| `light_humour_invitation` | 0/0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `courtesy_acknowledgement` | 0/0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `unsupported_allegation` | 5/5 | 0 | 5 | 5 | 0 | 0 | 5 | 0 | 0 | 5 |
| `wrong_speaker_question` | 5/5 | 0 | 5 | 5 | 0 | 5 | 0 | 0 | 0 | 15 |
| `quoted_context_distraction` | 5/5 | 0 | 5 | 5 | 0 | 0 | 5 | 0 | 0 | 5 |

## Performance

- Case latency p50: 28.830s
- Case latency p95: 66.978s
- Case latency maximum: 83.195s

## Failures

| Case | Scenario | Outcome | Failure | Reply |
|---|---|---|---|---|
| `56c77eedf86a10a6748dc8e20d096e415708541735c07f5c88a338f49d29cc9c-direct_meaning_question-qualification-control` | `direct_meaning_question` | no_reply | operational failure: reviewer_invalid; invalid structured stage: reviewer:reviewer sentence world-claim checks contradict each other |  |
| `876bdb8ea63e16f1f17fa2e85a46a2bdee820cc80713b98bda78fa49c2a4d6eb-direct_meaning_question-qualification-control` | `direct_meaning_question` | no_reply | operational failure: reviewer_invalid; invalid structured stage: reviewer:reviewer sentence world-claim checks contradict each other |  |
| `fb81ffde7216facaedc1ef7bdfcda698fd65f2036952af87d3375d356b216c62-direct_meaning_question-qualification-control` | `direct_meaning_question` | no_reply | operational failure: reviewer_invalid; invalid structured stage: reviewer:reviewer sentence world-claim checks contradict each other |  |
| `2b6aa4dc33fc5e8ff1c9e1a4e90464d18bbd18c323bc525dd05f99b1a77d6594-principle_agreement-qualification-repeat-2` | `principle_agreement` | no_reply | operational failure: revision_proposer_invalid; invalid structured stage: revision_proposer:Expecting value: line 1 column 1 (char 0) |  |
| `bbef6a7437faac76049e2e2dd3b1a604990bf6ce820c7e27a25b8540da8772be-principle_agreement-qualification-repeat-2` | `principle_agreement` | no_reply | operational failure: claim_auditor_invalid; invalid structured stage: claim_auditor:claim auditor factual claim is not a verbatim sentence clause |  |
| `c47e16f9df9a49c7d0a6b632004dbed8e4fd4bfc2f313e23668ccfe30328fdc2-principle_agreement-qualification-repeat-2` | `principle_agreement` | no_reply | operational failure: revision_proposer_invalid; invalid structured stage: revision_proposer:Expecting value: line 1 column 1 (char 0) |  |
| `c5ee9e881dbcb9859791a380a382413c4d3ca01ca2b8ddc072e37ea5b1d2cf2b-principle_agreement-qualification-repeat-1` | `principle_agreement` | approved | mode light_humour not in ['opinion_or_principle'] | One can only wonder at such selective enthusiasm. |
| `3cd13a785eecfd49b729975a64f2ef7dddba26ee7045f879faf5aa19790a9b87-principle_challenge-qualification-repeat-2` | `principle_challenge` | no_reply | operational failure: revision_proposer_invalid; invalid structured stage: revision_proposer:direct factual question and mode contradict each other |  |
| `697ca41825ed6037eac03257d9e7940e826ce6bdb988c583c0e3e1c423701e1c-principle_challenge-qualification-repeat-1` | `principle_challenge` | no_reply | operational failure: reviewer_invalid; invalid structured stage: reviewer:reviewer sentence claim inventory is incomplete or out of order |  |
| `738f5f6c9aa58ebc32cdae4adbbf725cdb1fd2178d4ac3f8af725776c67a04c9-principle_challenge-qualification-repeat-2` | `principle_challenge` | no_reply | operational failure: reviewer_invalid; invalid structured stage: reviewer:reviewer sentence claim inventory is incomplete or out of order |  |
| `a19e13783f2b7b47348d1e95ece8445a69b5bb21b8312628c2cea66ccc2563ac-principle_challenge-qualification-repeat-2` | `principle_challenge` | no_reply | operational failure: revision_reviewer_invalid; invalid structured stage: revision_reviewer:reply pipeline model-call ceiling reached |  |
| `bbef6a7437faac76049e2e2dd3b1a604990bf6ce820c7e27a25b8540da8772be-principle_challenge-qualification-repeat-2` | `principle_challenge` | no_reply | operational failure: revision_proposer_invalid; invalid structured stage: revision_proposer:direct_factual_answer requires at least one factual claim |  |
| `f057d087704724241758726985631db68cd9d7c2d34722969eeca73585de4f6d-principle_challenge-qualification-repeat-2` | `principle_challenge` | no_reply | operational failure: reviewer_claim_inventory_mismatch; invalid structured stage: revision_reviewer:reviewer_claim_inventory_mismatch |  |

## Verdict

EVALUATION REQUIRES ANALYSIS
