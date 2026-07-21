# AI-first Reply Strategy Corpus Matrix Evaluation

## Status

- Offline fixtures: 6100 across 610 quotations and 10 scenarios
- Paid cases completed: 810/810
- Valid quality cases: 802 (8 paid cases excluded for placeholder quote text)
- Distinct quotations exercised with valid text: 606/610
- Deterministic grades passed: 447/802
- Report-only grading corrections: 4
- Operational failures: 12
- Approved replies: 294
- Deliberate no-reply outcomes: 508
- Revisions: 32
- Structured model calls: 1273
- Known provider cost: US$4.254912
- Ambiguous exposure: US$0.131958
- Hard ceiling: US$20.00
- X posting, media upload and production-state writes: zero

## Scenarios

| Scenario | Completed | Invalid fixture | Valid | Passed | Failed | Approved | No reply | Revisions | Calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `direct_context_question` | 81/81 | 2 | 79 | 0 | 79 | 0 | 79 | 0 | 80 |
| `direct_meaning_question` | 81/81 | 1 | 80 | 11 | 69 | 11 | 69 | 1 | 109 |
| `quotation_verification` | 81/81 | 1 | 80 | 0 | 80 | 0 | 80 | 0 | 81 |
| `principle_agreement` | 81/81 | 1 | 80 | 74 | 6 | 74 | 6 | 6 | 180 |
| `principle_challenge` | 81/81 | 0 | 81 | 39 | 42 | 42 | 39 | 25 | 218 |
| `light_humour_invitation` | 81/81 | 1 | 80 | 79 | 1 | 78 | 2 | 0 | 163 |
| `courtesy_acknowledgement` | 81/81 | 2 | 79 | 79 | 0 | 79 | 0 | 0 | 161 |
| `unsupported_allegation` | 81/81 | 0 | 81 | 80 | 1 | 0 | 81 | 0 | 82 |
| `wrong_speaker_question` | 81/81 | 0 | 81 | 4 | 77 | 10 | 71 | 0 | 107 |
| `quoted_context_distraction` | 81/81 | 0 | 81 | 81 | 0 | 0 | 81 | 0 | 82 |

## Performance

- Case latency p50: 11.513s
- Case latency p95: 41.378s
- Case latency maximum: 81.218s

## Failures

| Case | Scenario | Outcome | Failure | Reply |
|---|---|---|---|---|
| `00426881d2746c35657e8bb3103febb15cf13f2342bb65604a23a278b608064d-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `0056972ab9debcb840c36ac23ad0387e715cb22fc4ed49dbcf35159f83592aa5-direct_meaning_question` | `direct_meaning_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `0058570c2c7302e3816cfe8deb9603a35f5475f4f6331e93dfee347dfc82a20a-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `03085800060bfadc419235ff61318aeda096f6b476904d979bc174845834fd68-wrong_speaker_question` | `wrong_speaker_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `03e473b3f85ab6b046d01953b567b453c33d9d8f080a8bf6e8b50dd04fb63573-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `042a3bbf52636cae18382ef8afc924960e7b323d0355effeac65948918bd534c-direct_meaning_question` | `direct_meaning_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `04cdf43ebd3729f0c4069ee808ba55613191533d2b12cff8753f55e8ad31c3c6-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `053f974c97ba37b7a77862c1df8e02cc676ae7607f9c52387abc21a1e93b2e02-principle_challenge` | `principle_challenge` | no_reply | outcome no_reply not in ['approved'] |  |
| `07e74019b377eb2cb486051caa942b37f4623d394b9ced2e00b3894ed29d445e-wrong_speaker_question` | `wrong_speaker_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `0827a4126cc47bd563b75be766edeffcd44f7027125f190887ffb7a70c5bb015-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `08be01c1fe5fc197294f854d99c293c1d85039b4a0576a9f50db5943d9071560-direct_meaning_question` | `direct_meaning_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `08c1ba31940172fa194727ff2bf5878ada3b3dd926625ba11e71786e275f993b-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `093fe18b5e8a685a1ec1a38813fa2dfddce5ca84853723c13d1dfc2a6ac4a429-principle_challenge` | `principle_challenge` | no_reply | outcome no_reply not in ['approved'] |  |
| `0acb6cd5c5ec0ed58b882f3f4077b6f65f04b100ebfde6ace075c1993736b51a-wrong_speaker_question` | `wrong_speaker_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `0dc2d266b05435062cb2766173439781441788afc8fa54f94d2e59076fdc873d-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `0dece118f1c2b0a5179b4dfca4b897c5b6828965aff371f1c2b2f8d5fd413a9c-direct_meaning_question` | `direct_meaning_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `0e54df9b0adb2337f436d7346964965c56669c997386d75f2d58104ec88e1766-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `0eb571f7580b1f2e701ff718292f97c71db2afef3a061ab35fa6f254272301a5-principle_challenge` | `principle_challenge` | no_reply | outcome no_reply not in ['approved'] |  |
| `0f81923e54293261a3cc8318d9ac5959008002c0a9e17354ae1e8ea881f28e29-wrong_speaker_question` | `wrong_speaker_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `102462fc508a75a80c8c56230e613a4902387945107df418ed8d5cce1ecf67aa-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `10adcfb75228e14d096125b1bccbedd4630a08b1cdaa3d00c6b3aabcbecbcb11-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `115f2b69f05bd8ad48114ece66c3c31d0149f85335b9a9c884a4d790d3268fa0-principle_challenge` | `principle_challenge` | no_reply | outcome no_reply not in ['approved'] |  |
| `1393eb73be2367fb280b57d3040e93f6ca7f569a77ec5e0a47f72940b9508637-wrong_speaker_question` | `wrong_speaker_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `141d2c98445a18f2becfa8034eaf9c7aa8ce863f1d754a43d64852d716588eb5-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `15bd818b86c205c6364eac5614bdda8a9daa968bc6336d9605cf05dacc6a7712-direct_meaning_question` | `direct_meaning_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `1646e92be4587393785ee01e84676a6dd575cc0e7e6a2b4ddab458464a378b6d-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `16c1d05225fe1d45d7ab950a5c54d39b70ef9453718a8678f405195d4af6969f-principle_challenge` | `principle_challenge` | no_reply | outcome no_reply not in ['approved'] |  |
| `17dccede6209dea44954c2f3d55e61aa9fd05ccf632afb3950904ab6f1e6acc1-wrong_speaker_question` | `wrong_speaker_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `18f6d7b4000e0f1fd74e0aa373bbc1644285b90624857b65b4f0c5ca651be672-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `1948eccad1dcd6bff2e25fef1bf600b5912093154fd62284035e82da05e65c55-direct_meaning_question` | `direct_meaning_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `1993fc02d1ec12c56b6b181dcb12f2c2145b3a373861a365088fe2b1137a9192-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `19cb0b567f5dd9f7f3a15dd38c8c0455112573afe574fc3fbce0151783e5f296-principle_challenge` | `principle_challenge` | no_reply | outcome no_reply not in ['approved'] |  |
| `1b123947f471dc4dc78ded36d47a51406cbf17df0163828f4d540d196be43315-wrong_speaker_question` | `wrong_speaker_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `1bac978f8c38d97a48ee09d4244af57592f1a26a088562954ae24ea56943e662-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `1d1229a96b9fe617c4668ca22ca2d8eb89bd3f1f530213577e230304fda42087-direct_meaning_question` | `direct_meaning_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `1dde0bb1ec4fa393163170fa18ed57b08981e3f4f5fe36d343021331a85be8fe-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `2153109c0460218da977cc16beb058c75a1975ed7d674d303c4cccbf0a1df651-wrong_speaker_question` | `wrong_speaker_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `22735d4de0d689151ee67f14a1e49943cdb2824246108fd34ece9a28261da78a-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `230b8d71f541acfc6a18d0a29f508eddf33b90d0f25beb736ba83ac1fb1cfc1a-direct_meaning_question` | `direct_meaning_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `23a7a16a048e2a3a2884fdf31d53c4b6149367fa9420be7ca734a3af75845a7f-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `25b16b1294599728b25379c35a6c142b676e02bb6392c307060c9d8567c05cb9-principle_challenge` | `principle_challenge` | no_reply | outcome no_reply not in ['approved'] |  |
| `27b9bc245abb7d5e022924fd6a02b346fc4a3dfb7837c974cb569461412be069-wrong_speaker_question` | `wrong_speaker_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `283636fc2526ccd22c302f80f6c494c36ec1e612d16f9b5a145d4eca70b14e9b-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `2a4c080a88934c20c64f8c8a793f2aa39fbc653108387d1af27dca8f885a9f14-direct_meaning_question` | `direct_meaning_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `2a50d19f02e311797bac4b1b6945ca1d5348acf92a35cd079676deca68d475f4-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `2b94eab6f6a3cd9a4ac5f110a47d2ea2b8726f08622cb49db1a4c89d6d364884-wrong_speaker_question` | `wrong_speaker_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `2e651c6188d03434522b5efd70d19bb1c6f80b556f0fb7d194813699100e37e1-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `2ed1607a04af289eb77f39654d09d1e68a12d31762f82fc5a6233d7a27bcd53a-direct_meaning_question` | `direct_meaning_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `2f5df9185bdf06e8ffe184a0e65edfe238a7dcea2a3b482103f763947aa05467-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `32219e403c5ba59e985bbfa5dbf6834892e1abfb41d25effd29a617eeb936e17-wrong_speaker_question` | `wrong_speaker_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `34114f8f8fa580a2cb413c481408094ad2a8675ebb59955cf8c7d665897d8381-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `3430990175a042a15b32ecbab9f142e149a53f1bb36bf8237c56747a4a163f39-direct_meaning_question` | `direct_meaning_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `34372b4bc6ba46c34b4943069658ff7c1a605a71f095e2d4386dff93cf072152-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `36d8caf8b8ae9fbd20473e1ea33dc37aa74045e0e39efeb241c36113da822027-wrong_speaker_question` | `wrong_speaker_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `37e7a04cad7f1ec91e4204814d7e05be4c31cfc0440a43fa616e11d5c9832d99-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `3865a461dd280b2b0f00d7339e8bb5c9e18fbead71aeee83da52c57bb94cd01e-direct_meaning_question` | `direct_meaning_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `38805634a94b830357ca31de921357af37ce17c69a295c26dfc4c091979549ad-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `3cced21d7f9bc45fd5479288c7b413bad5e0e48fcf71f103251b6284c8528f12-wrong_speaker_question` | `wrong_speaker_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `3ceb27465a268a65848bfeaf1eb0171a691bbc27b42f74ea43131e5fe96e6eea-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `3cfd627fe66d0f83539b77f4e94caeb3588a01c3bf1e5043686a7980019ea87b-direct_meaning_question` | `direct_meaning_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `3d5cd232b20fc3d9d7ae69aaee66bb27eb9aa38af9e0d3d7edf46fd5ae8f2c20-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `3dd04269c7014b5ab25d7fc9d7b2ea5195ed1e8a2b2c44278ed5cad73486a2d5-principle_agreement` | `principle_agreement` | no_reply | operational failure: revision_proposer_invalid; invalid structured stage: reviewer:reviewer_claim_inventory_mismatch, revision_proposer:Expecting value: line 1 column 1 (char 0); outcome no_reply not in ['approved'] |  |
| `3f9b818c85efdf923c422517ba06b46ee280258c68b74cebb02f68a54851b40a-wrong_speaker_question` | `wrong_speaker_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `3faefc589e861823bd4f1f30807535defbadc6177404872bf326e1297c8fb13d-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `3fb0e6e6f45d742323a00b0d45fc0a0a524bc0ed0ba11a2c84b6c54c86fb2a0f-direct_meaning_question` | `direct_meaning_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `3fca53c7e514fc68ab6ce08d5ea1ce0be2ce03bafc1df3663f0677d1121cebe2-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `41278618713255420e6676088c07312c0d90d6e688f7c1f58d9dfd33ec3c494f-principle_challenge` | `principle_challenge` | no_reply | outcome no_reply not in ['approved'] |  |
| `44600fa8684fa688c505d5a265cafbe98a996f71cac9cc4e0de0c99e25406a58-wrong_speaker_question` | `wrong_speaker_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `451d455b4ed75603a46b4d2590d73f54ffc078981ecb7fd9c9e8c2bfa7a684d2-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `4566b8fa144de0e512c43478beeac43f44f80ca9c90acf018506c1829c9cbfff-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `45ade4f552f16009693739be5107d0d304da8d0d6c6e7a2948573d55fb9157a9-principle_challenge` | `principle_challenge` | no_reply | outcome no_reply not in ['approved'] |  |
| `49f8640fda9f890389e594dfdda488f012367be1bb0e61c5bcb06556f2d49a5f-wrong_speaker_question` | `wrong_speaker_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `4ac9c0148408ada0192ebedefab8a0910fbce8bbbb5c6a37288dbc1d7ad410f9-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `4b3a72437e90fda155fde2ce95e49a0def5faaed7dcb08eaa11636fd77f37bb2-direct_meaning_question` | `direct_meaning_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `4b53af75f4bf9c618f8705d104bebb7ea47088877fcf9f31e742299b411bf119-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `4c9b51eb69875a896e83f9e4a07655ad8c1de12056089090f235152d46f67b61-light_humour_invitation` | `light_humour_invitation` | no_reply | operational failure: reviewer_invalid; invalid structured stage: reviewer:reviewer cannot approve an evasive direct answer |  |
| `4d226cdd3321371650fcf839f8920a27c3acd2836b2d57bdb5a235217224fcf6-wrong_speaker_question` | `wrong_speaker_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `4d86b60909c026a4a30be158310945742a1c25477ced27f76cf805151054568f-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `4e08964fca7fabd939c7be1842bcc25133d0a5d8eccaaf78d4c36689350daa9b-direct_meaning_question` | `direct_meaning_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `4e255992099e6fbb8794ae0e3e044a12c9f38c263b58015ef9e318a7227a5a21-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `4e92014dc14fd9cce562099b03ccfdf480fd5909414483edf4a3e28dc6e85c80-principle_challenge` | `principle_challenge` | no_reply | outcome no_reply not in ['approved'] |  |
| `4ffa3d1db4201e7a9302c3e90098a6142747fc65113158ce62ab8e0aaf0b53d0-wrong_speaker_question` | `wrong_speaker_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `5112489e69abb6b54d5804a680fa1d2a2d3951558caea1c939b96a42e8e23d05-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `51346064e36d2597e3cbe635dbe9ff8258b529b5f2c324d2114c7bdb6dc1c3d6-direct_meaning_question` | `direct_meaning_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `5189f2683ec11a28cc0507226cff34c24e7341a489395bf5e472cc437179499d-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `52f9b9f99f66ff3bc786183803f3a8d68277604471cd411027441989337c9351-principle_challenge` | `principle_challenge` | no_reply | outcome no_reply not in ['approved'] |  |
| `5440b194ff60f3b728644d36cb483d0c23448616b4c79588e798177e1ffa0483-wrong_speaker_question` | `wrong_speaker_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `545aee361007f6d7f39394fab1bafb1fd05bc42f7a820bd34b17a75814a109b3-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `54bcfa6d526fe5de3d3f6db6ee2b1707030258c07c941f3a2c1dcfb9dde87dc1-direct_meaning_question` | `direct_meaning_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `54d5a420549526c6b9bc6af7665519325c278239549273303ffede952b729789-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `56ad83fce3f67cb04eaa9bf4baaf858d776971e7e52f29e9c97f58a1b964e2f7-principle_challenge` | `principle_challenge` | no_reply | outcome no_reply not in ['approved'] |  |
| `5846d1f5edc27201f80031ef49263e6d7299b74267da9b7c77ba34cf6b4e0e46-wrong_speaker_question` | `wrong_speaker_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `5956332c9ec11e840051d9693954ea80ecc6d805b915d68b2f5bc1d01e2ce04a-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `596607c93f9aca406d8543ecc792e9a87fad06354303a69663df183d7417a764-direct_meaning_question` | `direct_meaning_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `59b18840cbf67ac4a14a64f3858e7fcee40dfb483db36ec5b911fc22592dffaa-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `5a7d71ca8c8a80423f1e3c5aabbc27479228e4eddff8082ad04184bad624f153-principle_challenge` | `principle_challenge` | no_reply | operational failure: revision_reviewer_invalid; invalid structured stage: revision_reviewer:reviewer cannot approve an evasive direct answer; outcome no_reply not in ['approved'] |  |
| `5d2584695aac1654d9f19e9ff1fded6121c7079dabf6aa65dda4d2b58485fc67-wrong_speaker_question` | `wrong_speaker_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `5dd9b4dca874178c620c4b0a6029af01f15e42573f320abcd1a99fdaf4904dc9-direct_context_question` | `direct_context_question` | no_reply | outcome no_reply not in ['approved'] |  |
| `5e65bf83aadb295f5e136fb021a89eedfc2fad67ecc616133005e81012b2328b-quotation_verification` | `quotation_verification` | no_reply | outcome no_reply not in ['approved'] |  |
| `5f58655af3683767213b37bb561575c7e2c39f60d21a9111c1cfbfca02ef7d27-wrong_speaker_question` | `wrong_speaker_question` | no_reply | outcome no_reply not in ['approved'] |  |

Only the first 100 of 355 failures are shown here; all are retained in `evaluation_results.json`.

## Verdict

EVALUATION REQUIRES ANALYSIS
