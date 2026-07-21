# AI-first Reply Strategy Corpus Matrix Evaluation

## Status

- Offline fixtures: 6100 across 610 quotations and 10 scenarios
- Paid cases completed: 120/120
- Valid quality cases: 120 (0 paid cases excluded for placeholder quote text)
- Distinct quotations exercised with valid text: 117/610
- Deterministic grades passed: 97/120
- Report-only grading corrections: 0
- Operational failures: 11
- Approved replies: 64
- Deliberate no-reply outcomes: 56
- Safe principle-response opportunities missed: 14
- Revisions: 9
- Structured model calls: 304
- Known provider cost: US$1.714561
- Ambiguous exposure: US$0.088974
- Hard ceiling: US$3.00
- X posting, media upload and production-state writes: zero

## Scenarios

| Scenario | Completed | Invalid fixture | Valid | Passed | Failed | Approved | No reply | Missed response | Revisions | Calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `direct_context_question` | 20/20 | 0 | 20 | 13 | 7 | 0 | 20 | 0 | 0 | 21 |
| `direct_meaning_question` | 20/20 | 0 | 20 | 15 | 5 | 15 | 5 | 0 | 0 | 62 |
| `quotation_verification` | 20/20 | 0 | 20 | 18 | 2 | 18 | 2 | 0 | 2 | 72 |
| `principle_agreement` | 15/15 | 0 | 15 | 13 | 2 | 12 | 3 | 3 | 2 | 38 |
| `principle_challenge` | 15/15 | 0 | 15 | 13 | 2 | 4 | 11 | 11 | 2 | 33 |
| `light_humour_invitation` | 0/0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `courtesy_acknowledgement` | 0/0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `unsupported_allegation` | 5/5 | 0 | 5 | 5 | 0 | 0 | 5 | 0 | 0 | 5 |
| `wrong_speaker_question` | 20/20 | 0 | 20 | 15 | 5 | 15 | 5 | 0 | 3 | 69 |
| `quoted_context_distraction` | 5/5 | 0 | 5 | 5 | 0 | 0 | 5 | 0 | 0 | 5 |

## Performance

- Case latency p50: 28.968s
- Case latency p95: 65.512s
- Case latency maximum: 114.745s

## Failures

| Case | Scenario | Outcome | Failure | Reply |
|---|---|---|---|---|
| `5f14e6e600773cf394a3f3a3ae21aef10f108691eef50093002571765a5a4a82-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `5d1b1cb2460a02dd8fb0b61823926b330716189913eeea2c75c03d5d0ada5cf2-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `4c135c053d61ff9441b6aed5b77213967f43489263ee9f80b49a4155ef93737e-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `8c839e92d3961147ef0070f049a2caa7f0c070bffafe4988153659825d9fa50b-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `d42784e808ec3ab021052a96662182a47acd53ba97b412e0657858fdd1e848f9-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `a8afc3ebabfea07d2de701cc086c1119649217548f8e662cb09d062ea0bb5d54-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `d8af9aa2831786794c4dbc7cfa5b406f3d1206dfab27e8af5e867ae014ac64d3-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `eca9b542f2b3b84e3d917eb4dda8b4539505b74db73cfc3b5c0278bdf96593e4-direct_meaning_question` | `direct_meaning_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `56c77eedf86a10a6748dc8e20d096e415708541735c07f5c88a338f49d29cc9c-direct_meaning_question` | `direct_meaning_question` | no_reply | operational failure: proposer_invalid; invalid structured stage: proposer:Expecting value: line 1 column 1 (char 0); outcome no_reply not in ['approved'] |  |
| `74539c04c492f1b8147b584a781b2f32c7f4f68e8c2f6773ff9415ff31c1a2b3-direct_meaning_question` | `direct_meaning_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `8ad833d18c206e9f31e4e45e8479c99ccefd3ecf2c9cc6d5b7e2ef92c3242dc9-direct_meaning_question` | `direct_meaning_question` | no_reply | operational failure: proposer_invalid; invalid structured stage: proposer:direct_factual_answer requires at least one factual claim; outcome no_reply not in ['approved'] |  |
| `fbb0cc71bd83a6594060aa55c4c283bbddb4860c6f42a3c45e1be358c528accf-direct_meaning_question` | `direct_meaning_question` | no_reply | operational failure: proposer_invalid; invalid structured stage: proposer:direct_factual_answer requires at least one factual claim; outcome no_reply not in ['approved'] |  |
| `bf7b29c68ebee5c40813db3cb6970b29e4c6cb2ef06f80cea782cf2a6c07362f-quotation_verification` | `quotation_verification` | no_reply | operational failure: revision_reviewer_invalid; invalid structured stage: revision_reviewer:reply pipeline model-call ceiling reached; outcome no_reply not in ['approved'] |  |
| `a7510dd49389e910c983d6d18e1b57a96b02b8c1d02e5e660aa80f1021226224-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `da6727d2a7d9173dc1086e98aa04142e4e9488ac0d7243f7b5ba8a17d29a9ccc-wrong_speaker_question` | `wrong_speaker_question` | no_reply | ambiguous provider transmission was abandoned and not retried |  |
| `141d2c98445a18f2becfa8034eaf9c7aa8ce863f1d754a43d64852d716588eb5-wrong_speaker_question` | `wrong_speaker_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `2e651c6188d03434522b5efd70d19bb1c6f80b556f0fb7d194813699100e37e1-wrong_speaker_question` | `wrong_speaker_question` | no_reply | operational failure: revision_reviewer_invalid; invalid structured stage: reviewer:reviewer_claim_inventory_mismatch, revision_reviewer:reply pipeline model-call ceiling reached; outcome no_reply not in ['approved'] |  |
| `7e0f684b28271738f463570fe9e28feb0332bb7e12f7f29dd2ac2d772f0a5a62-wrong_speaker_question` | `wrong_speaker_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `8d9fa1bf449aa2112a5e80faa2999485caf0d3d00dc7963cde47ef3387eec003-wrong_speaker_question` | `wrong_speaker_question` | no_reply | operational failure: direct_answer_missing_resolved_actor; invalid structured stage: reviewer:direct_answer_missing_resolved_actor, revision_reviewer:direct_answer_missing_resolved_actor; outcome no_reply not in ['approved'] |  |
| `c5ee9e881dbcb9859791a380a382413c4d3ca01ca2b8ddc072e37ea5b1d2cf2b-principle_agreement` | `principle_agreement` | no_reply | operational failure: reviewer_invalid; invalid structured stage: reviewer:reviewer sentence assessment does not cover the exact reply |  |
| `f898abc6e133d50485c751bc2f30ca71ab4c34f3f1000b29c9142a2d518e086d-principle_agreement` | `principle_agreement` | no_reply | operational failure: reviewer_invalid; invalid structured stage: reviewer:reviewer sentence claim inventory is incomplete or out of order |  |
| `f057d087704724241758726985631db68cd9d7c2d34722969eeca73585de4f6d-principle_challenge` | `principle_challenge` | no_reply | operational failure: revision_reviewer_invalid; invalid structured stage: revision_reviewer:reply pipeline model-call ceiling reached |  |
| `3cd13a785eecfd49b729975a64f2ef7dddba26ee7045f879faf5aa19790a9b87-principle_challenge` | `principle_challenge` | no_reply | operational failure: reviewer_invalid; invalid structured stage: reviewer:reviewer sentence classification contradicts its non-factual basis |  |

## Verdict

EVALUATION REQUIRES ANALYSIS
