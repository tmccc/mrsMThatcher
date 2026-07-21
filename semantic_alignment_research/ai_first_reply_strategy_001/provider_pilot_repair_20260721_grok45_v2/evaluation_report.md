# AI-first Reply Strategy Corpus Matrix Evaluation

## Status

- Offline fixtures: 6100 across 610 quotations and 10 scenarios
- Paid cases completed: 64/120
- Valid quality cases: 64 (0 paid cases excluded for placeholder quote text)
- Distinct quotations exercised with valid text: 64/610
- Deterministic grades passed: 27/64
- Report-only grading corrections: 0
- Operational failures: 12
- Approved replies: 27
- Deliberate no-reply outcomes: 37
- Revisions: 3
- Structured model calls: 159
- Known provider cost: US$3.211846
- Ambiguous exposure: US$1.584986
- Hard ceiling: US$5.00
- X posting, media upload and production-state writes: zero

## Scenarios

| Scenario | Completed | Invalid fixture | Valid | Passed | Failed | Approved | No reply | Revisions | Calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `direct_context_question` | 20/20 | 0 | 20 | 2 | 18 | 2 | 18 | 1 | 40 |
| `direct_meaning_question` | 20/20 | 0 | 20 | 18 | 2 | 18 | 2 | 1 | 63 |
| `quotation_verification` | 20/20 | 0 | 20 | 4 | 16 | 4 | 16 | 1 | 51 |
| `principle_agreement` | 0/15 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `principle_challenge` | 0/15 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `light_humour_invitation` | 0/0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `courtesy_acknowledgement` | 0/0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `unsupported_allegation` | 0/5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `wrong_speaker_question` | 4/20 | 0 | 4 | 3 | 1 | 3 | 1 | 0 | 11 |
| `quoted_context_distraction` | 0/5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## Performance

- Case latency p50: 60.156s
- Case latency p95: 150.605s
- Case latency maximum: 154.904s

## Failures

| Case | Scenario | Outcome | Failure | Reply |
|---|---|---|---|---|
| `5f14e6e600773cf394a3f3a3ae21aef10f108691eef50093002571765a5a4a82-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `3ba6cfba4bbf0673ce1cf563fb9de4821aded8422c64cbcd4218bf8da951f24d-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `5189f2683ec11a28cc0507226cff34c24e7341a489395bf5e472cc437179499d-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `5d1b1cb2460a02dd8fb0b61823926b330716189913eeea2c75c03d5d0ada5cf2-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `1005a705248e9a619623b60321cda394a7507124823e6ac6926857339fad48fc-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `d9acc52446c0e8c7b1ba16c04ffd21a9cf2329f8b64078f531aa132547445dba-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `53699726287c4fed9ef2b086e53cc2938b1fd482a7b5541f4718df3439598215-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `4c135c053d61ff9441b6aed5b77213967f43489263ee9f80b49a4155ef93737e-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `1085ff6a77a0bf02ea5c2621cc8d546e47637f2e363979687c62ba73f16504fa-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `e61f14550883eef5e138633ec95c5dcfe63a5834732fa31807ea33a7e936a2f2-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `cd6a642bb6539c6b79098f1d599f9dba9c12fc5d9a190caf74cd452f7f9d10d5-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `f900baf560999c350070e4600757cfa22f640c82e510e2f9f5e2c35059263155-direct_context_question` | `direct_context_question` | no_reply | ambiguous provider transmission was abandoned and not retried |  |
| `8c839e92d3961147ef0070f049a2caa7f0c070bffafe4988153659825d9fa50b-direct_context_question` | `direct_context_question` | no_reply | operational failure: proposer_invalid; invalid structured stage: proposer:a proposed reply requires at least medium proposer confidence; outcome no_reply not in ['approved'] |  |
| `3ec9ed7c0f35b414ee6570182d1e65874cc7f5c659cdb31ecdbf8fe3ee492319-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `272c9af2e31064654a8ab74480de7d7357795514e12d947587eb155f31cca996-direct_context_question` | `direct_context_question` | no_reply | operational failure: evidence_invalid; invalid structured stage: evidence:evidence reference is duplicated or was not supplied; outcome no_reply not in ['approved'] |  |
| `d42784e808ec3ab021052a96662182a47acd53ba97b412e0657858fdd1e848f9-direct_context_question` | `direct_context_question` | no_reply | operational failure: revision_proposer_invalid; invalid structured stage: revision_proposer:proposer proposed_reply is invalid; outcome no_reply not in ['approved'] |  |
| `d8af9aa2831786794c4dbc7cfa5b406f3d1206dfab27e8af5e867ae014ac64d3-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `90a6e7991136e4ca46e618b5ff3cc570de1d16f1f47f69bfdc11e7004b44ee1d-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `eca9b542f2b3b84e3d917eb4dda8b4539505b74db73cfc3b5c0278bdf96593e4-direct_meaning_question` | `direct_meaning_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `fbb0cc71bd83a6594060aa55c4c283bbddb4860c6f42a3c45e1be358c528accf-direct_meaning_question` | `direct_meaning_question` | no_reply | ambiguous provider transmission was abandoned and not retried |  |
| `bf7b29c68ebee5c40813db3cb6970b29e4c6cb2ef06f80cea782cf2a6c07362f-quotation_verification` | `quotation_verification` | no_reply | operational failure: reviewer_invalid; invalid structured stage: reviewer:reviewer cannot approve an evasive direct answer; outcome no_reply not in ['approved'] |  |
| `6fa3eaaeb0a2c38a2a6f9a3b9e336ca96c269ec3c01b4a613427da70b4b5e649-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `c2c09852800ada888e6e618e70a1316b813e4e462fef5db17ff6e71037e5349f-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `2f8ac7d9bbeae27faa9d2ad74d1cbd8864005464c00d02bd88938232ac09bdf2-quotation_verification` | `quotation_verification` | no_reply | ambiguous provider transmission was abandoned and not retried |  |
| `de520cbe8c9d2b854073a3eb6c0dea4c5259da702dfbbf90e61e20516823d8ac-quotation_verification` | `quotation_verification` | no_reply | ambiguous provider transmission was abandoned and not retried |  |
| `2153109c0460218da977cc16beb058c75a1975ed7d674d303c4cccbf0a1df651-quotation_verification` | `quotation_verification` | no_reply | ambiguous provider transmission was abandoned and not retried |  |
| `cfe7448552ce1f14db2382ac4a56e86220db74742a887e06242f8b8acb48c2f7-quotation_verification` | `quotation_verification` | no_reply | ambiguous provider transmission was abandoned and not retried |  |
| `be000cd396c5e19e4154b8d54e0db02205a4700294946a86db750ea578d55430-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `586df70d0ca762e0c657a22adfee9caa7946da42acf90ca013bc3ddfb867b033-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `596607c93f9aca406d8543ecc792e9a87fad06354303a69663df183d7417a764-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `eb1d2ebaac7e321e67174d2db5761d2bd008341ebb04b1abac4d4a927cd7a7d4-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `a7510dd49389e910c983d6d18e1b57a96b02b8c1d02e5e660aa80f1021226224-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `8e2cf333d3528f1802f9bcae69bf7f698a8ae4dd71dbe1f0f8db4ae3ed22369b-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `f9f4251f60bf1f7ea5fb270975e8b5904f308504200a18e1b5b371fc38a57445-quotation_verification` | `quotation_verification` | no_reply | ambiguous provider transmission was abandoned and not retried |  |
| `760a25127dfb9c39cd24af73cf86cbbb76b89d31f6ecdc1eba90b09b0923bbf2-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `f30769b32809ece4155829a8ee676b04941d7409287ccc1c6226337b161438b8-quotation_verification` | `quotation_verification` | no_reply | operational failure: reviewer_invalid; invalid structured stage: reviewer:reviewer cannot approve an evasive direct answer; outcome no_reply not in ['approved'] |  |
| `51346064e36d2597e3cbe635dbe9ff8258b529b5f2c324d2114c7bdb6dc1c3d6-wrong_speaker_question` | `wrong_speaker_question` | no_reply | outcome no_reply not in ['approved'] |  |

## Verdict

EVALUATION REQUIRES ANALYSIS
