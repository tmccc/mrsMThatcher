# Generated Image Identity-Dependence Audit

Generated: `2026-07-10T07:02:21.546587+00:00`

## Executive verdict

The audit completed 83 generated images. 65 contain a grounded specific intended person; 6 are recommended for strong cross-quote restriction. The audit supports a narrow metadata-driven cross-quote policy experiment rather than a global generated-image penalty.

## Input validation

- Approved generated images: `83`
- Successfully audited: `83`
- Failed: `0`
- Every basename, origin directory, manifest hash, current image SHA-256 and existing generated-analysis hash was validated.

## Existing metadata reviewed

The audit used the origin quote, quote analysis, generation prompt, manifest and existing visual analysis. Existing metadata grounds intended people but does not judge likeness, identity dependence, meaning retention or cross-quote safety.

## Whether new xAI analysis was necessary

Yes. Local metadata establishes intent, but only a vision judgement can assess whether the rendered person is recognisable and whether the image still communicates its meaning without that recognition.

## Corpus distributions

Specific intended people: **65 / 83**

### Identity dependence

| value | count |
|---|---:|
| high | 6 |
| low | 31 |
| medium | 28 |
| none | 18 |

### Cross-quote reuse safety

| value | count |
|---|---:|
| contextual | 1 |
| mostly_safe | 58 |
| origin_quote_only | 4 |
| risky | 1 |
| safe | 19 |

### Recommended policy

| value | count |
|---|---:|
| origin_quote_only | 6 |
| small_penalty | 9 |
| unrestricted | 68 |

### Numeric distributions

| measure | mean | minimum | maximum |
|---|---:|---:|---:|
| Typical-viewer recognisability | 6.34 | 0.0 | 9.0 |
| Meaning retention without identity | 8.06 | 5.0 | 9.0 |

## Highest-risk images

| image | identity dependence | recognisability | meaning retention | reuse safety | policy |
|---|---|---:|---:|---|---|
| `tg_684fb4425801aebe8e8c52211921567da1cf4bd04cee430a3b30bed03d3276f5.png` | high | 7.0 | 5.0 | risky | origin_quote_only |
| `tg_faf99f3030693b0a55f0116194551792f3c4ea51261d7310eca7fb4d33b667d5.png` | high | 7.0 | 8.0 | origin_quote_only | origin_quote_only |
| `tg_912547decff638d587339e61584c8a4954e955087ca86c4fad6b8977507ed5c7.png` | high | 8.0 | 6.0 | origin_quote_only | origin_quote_only |
| `tg_ae1ce8a812279c8dd97b71cfcd5efc578ba06dea161ef2de4a2091205eba279e.png` | high | 9.0 | 6.0 | origin_quote_only | origin_quote_only |
| `tg_b09c4175a50f081e3807a29428f16f7644ef38c809f7f4ac8b20ea3c72aa3785.png` | high | 9.0 | 6.0 | origin_quote_only | origin_quote_only |
| `tg_e0770fc3d6619db33fb692301a29fbf15f0e8e2306bd568064e557a108f69ec3.png` | high | 9.0 | 8.0 | mostly_safe | unrestricted |
| `tg_8312afd41d8a4871f7c62f21e6bab0840e48aef15d48e311fc152cc1e1d03e1d.png` | medium | 7.0 | 8.0 | mostly_safe | small_penalty |
| `tg_a9426dce186893768be1d61ea3ca82d90d05667d085c5a3d217e3a08059eba5b.png` | medium | 7.0 | 8.0 | mostly_safe | unrestricted |
| `tg_15bd818b86c205c6364eac5614bdda8a9daa968bc6336d9605cf05dacc6a7712.png` | medium | 8.0 | 7.0 | mostly_safe | small_penalty |
| `tg_2b351e489fc2b20f68c7f7867b3b17fb1636bb3ed2e7b6593e3345e10b09d5a2.png` | medium | 8.0 | 7.0 | mostly_safe | small_penalty |
| `tg_4c9b5b2d2dd30ba5dc20d850646ca3e897d782d8d27634866f85e63cb06b8d98.png` | medium | 8.0 | 7.0 | mostly_safe | unrestricted |
| `tg_59b18840cbf67ac4a14a64f3858e7fcee40dfb483db36ec5b911fc22592dffaa.png` | medium | 8.0 | 7.0 | contextual | small_penalty |
| `tg_04d26fbb2aa5ad3824e1f452699b6e9265298b84a61458f524e19db273a70550.png` | medium | 8.0 | 8.0 | mostly_safe | unrestricted |
| `tg_063b2c3fd5a61ea4fd955bed8d11b523ee5634086c647e378aaf33e3784abcf7.png` | medium | 8.0 | 8.0 | mostly_safe | unrestricted |
| `tg_1a1d47475ade7da4e7e32a3dfef2e1aadb479df288d6a502a7d5f93f96e11500.png` | medium | 8.0 | 8.0 | mostly_safe | small_penalty |
| `tg_1a73e33d64ecdbdcce0ced1691e91457efd50720116168b28a2371d6b8a8eec6.png` | medium | 8.0 | 8.0 | mostly_safe | unrestricted |
| `tg_204869c6d40d2610b92dbcbff883303e72a7d1ae972b74d8c25d94b0ec61994d.png` | medium | 8.0 | 8.0 | mostly_safe | origin_quote_only |
| `tg_3fb0e6e6f45d742323a00b0d45fc0a0a524bc0ed0ba11a2c84b6c54c86fb2a0f.png` | medium | 8.0 | 8.0 | mostly_safe | unrestricted |
| `tg_6ed99b617e093add13e311291c0bf176dceccaf7978a98885476d932ec0c4493.png` | medium | 8.0 | 8.0 | mostly_safe | unrestricted |
| `tg_6f70887295eff72d62a86e86c3983e7d960cafd35784e0f5d6a4d790558cf37b.png` | medium | 8.0 | 8.0 | mostly_safe | unrestricted |

## Person-containing images that remain safe for reuse

| image | identity dependence | recognisability | meaning retention | reuse safety | policy |
|---|---|---:|---:|---|---|
| `tg_0056972ab9debcb840c36ac23ad0387e715cb22fc4ed49dbcf35159f83592aa5.png` | medium | 9.0 | 8.0 | mostly_safe | unrestricted |
| `tg_04d26fbb2aa5ad3824e1f452699b6e9265298b84a61458f524e19db273a70550.png` | medium | 8.0 | 8.0 | mostly_safe | unrestricted |
| `tg_063b2c3fd5a61ea4fd955bed8d11b523ee5634086c647e378aaf33e3784abcf7.png` | medium | 8.0 | 8.0 | mostly_safe | unrestricted |
| `tg_093fe18b5e8a685a1ec1a38813fa2dfddce5ca84853723c13d1dfc2a6ac4a429.png` | low | 9.0 | 8.0 | mostly_safe | unrestricted |
| `tg_0f3c7da5b7971b89c68fb0a5a07b1b23c563c32a94ee6b3ae5a77fce11d95bcc.png` | low | 8.0 | 8.0 | mostly_safe | unrestricted |
| `tg_15bd818b86c205c6364eac5614bdda8a9daa968bc6336d9605cf05dacc6a7712.png` | medium | 8.0 | 7.0 | mostly_safe | small_penalty |
| `tg_184d3bea3fe23bc3fb8460164de9e8535a8f6a7e43b472c1528d8729c659ac13.png` | low | 8.0 | 8.0 | mostly_safe | unrestricted |
| `tg_1a1d47475ade7da4e7e32a3dfef2e1aadb479df288d6a502a7d5f93f96e11500.png` | medium | 8.0 | 8.0 | mostly_safe | small_penalty |
| `tg_1a73e33d64ecdbdcce0ced1691e91457efd50720116168b28a2371d6b8a8eec6.png` | medium | 8.0 | 8.0 | mostly_safe | unrestricted |
| `tg_204869c6d40d2610b92dbcbff883303e72a7d1ae972b74d8c25d94b0ec61994d.png` | medium | 8.0 | 8.0 | mostly_safe | origin_quote_only |
| `tg_2051b1ac566abdb8335f8967f32f8f23bd71b4fb2cb0231659869045868b14c9.png` | low | 9.0 | 8.0 | mostly_safe | unrestricted |
| `tg_25b16b1294599728b25379c35a6c142b676e02bb6392c307060c9d8567c05cb9.png` | low | 8.0 | 8.0 | mostly_safe | unrestricted |
| `tg_2a4c080a88934c20c64f8c8a793f2aa39fbc653108387d1af27dca8f885a9f14.png` | low | 8.0 | 8.0 | mostly_safe | unrestricted |
| `tg_2b351e489fc2b20f68c7f7867b3b17fb1636bb3ed2e7b6593e3345e10b09d5a2.png` | medium | 8.0 | 7.0 | mostly_safe | small_penalty |
| `tg_33eef87a7e4bd7c159bdb35f0378709b1c0324477b62d344e3b3ceec9e7f19a3.png` | low | 8.0 | 8.0 | safe | unrestricted |
| `tg_3e1a0b3d5d9dede45d7780b3032297c02d710093bb26876608d34d5ccc160586.png` | low | 9.0 | 8.0 | mostly_safe | unrestricted |
| `tg_3fb0e6e6f45d742323a00b0d45fc0a0a524bc0ed0ba11a2c84b6c54c86fb2a0f.png` | medium | 8.0 | 8.0 | mostly_safe | unrestricted |
| `tg_41278618713255420e6676088c07312c0d90d6e688f7c1f58d9dfd33ec3c494f.png` | low | 8.0 | 8.0 | mostly_safe | unrestricted |
| `tg_4c9b5b2d2dd30ba5dc20d850646ca3e897d782d8d27634866f85e63cb06b8d98.png` | medium | 8.0 | 7.0 | mostly_safe | unrestricted |
| `tg_52f9b9f99f66ff3bc786183803f3a8d68277604471cd411027441989337c9351.png` | low | 9.0 | 8.0 | mostly_safe | unrestricted |

## Origin-quote-only recommendations

| image | identity dependence | recognisability | meaning retention | reuse safety | policy |
|---|---|---:|---:|---|---|
| `tg_204869c6d40d2610b92dbcbff883303e72a7d1ae972b74d8c25d94b0ec61994d.png` | medium | 8.0 | 8.0 | mostly_safe | origin_quote_only |
| `tg_684fb4425801aebe8e8c52211921567da1cf4bd04cee430a3b30bed03d3276f5.png` | high | 7.0 | 5.0 | risky | origin_quote_only |
| `tg_912547decff638d587339e61584c8a4954e955087ca86c4fad6b8977507ed5c7.png` | high | 8.0 | 6.0 | origin_quote_only | origin_quote_only |
| `tg_ae1ce8a812279c8dd97b71cfcd5efc578ba06dea161ef2de4a2091205eba279e.png` | high | 9.0 | 6.0 | origin_quote_only | origin_quote_only |
| `tg_b09c4175a50f081e3807a29428f16f7644ef38c809f7f4ac8b20ea3c72aa3785.png` | high | 9.0 | 6.0 | origin_quote_only | origin_quote_only |
| `tg_faf99f3030693b0a55f0116194551792f3c4ea51261d7310eca7fb4d33b667d5.png` | high | 7.0 | 8.0 | origin_quote_only | origin_quote_only |

## Diagnostic case study

Image: `tg_faf99f3030693b0a55f0116194551792f3c4ea51261d7310eca7fb4d33b667d5.png`

Origin quote: Through bitter experience Solzhenitsyn has realised the dangers posed by an over-mighty state. The more power that accrues to the state - the less freedom; freedom to make decisions, freedom to make one's own choices, freedom of ideas - remains for the individual citizen.

Recent cross-quote: I place a profound belief - indeed a fervent faith - in the virtues of self reliance and personal independence. On these is founded the whole case for the free society.

Logged production score: `90.41531202375181`

- Intended person: `solzhenitsyn` (source `generation_prompt`)
- Identity dependence: `high`
- Typical-viewer recognisability: `7` / 10
- Meaning without identity: A writer reflecting on oppression under a communist regime, symbolizing the tension between state power and individual liberty.
- Meaning retention without identity: `8` / 10
- Reuse safety: `origin_quote_only`
- Recommended policy: `origin_quote_only`
- Reason: The image's meaning is closely tied to Solzhenitsyn's personal experience with Soviet oppression as described in the origin quote. Reusing it with unrelated quotes risks misattribution or anachronism, making it suitable primarily for the original context.

## Offline production-scoring experiment

Scope: recoverable logged top-candidate sets; not exact historical availability replay

- Generated selections with recoverable logged candidate sets: `2`
- Fixed-policy winner changes: `1`
- Continuous-policy winner changes: `0`
- These are logged top-candidate comparisons, not exact historical replay of image-cycle availability.

Diagnostic simulated result:

```json
{
  "time": "2026-07-10 06:38:27",
  "actual_winner": "tg_faf99f3030693b0a55f0116194551792f3c4ea51261d7310eca7fb4d33b667d5.png",
  "actual_score": 90.42,
  "line_no": 105,
  "quote_hash": "893d33ddc59977611c2c195ba41afb787f27c954484bc9dea4ba28652921e8ce",
  "candidate_count_recoverable": 5,
  "best_original_recoverable": "t19.jpg",
  "fixed_winner": "tg_0f3c7da5b7971b89c68fb0a5a07b1b23c563c32a94ee6b3ae5a77fce11d95bcc.png",
  "fixed_winner_score": 86.63,
  "fixed_changed": true,
  "continuous_winner": "tg_faf99f3030693b0a55f0116194551792f3c4ea51261d7310eca7fb4d33b667d5.png",
  "continuous_winner_score": 89.268,
  "continuous_changed": false
}
```

## Recommended next production experiment

Keep production unchanged while reviewing the highest-risk contact sheets. If the classifications hold up, test the hybrid fixed policy offline and then in shadow logs: unrestricted unchanged, small/strong penalties only for cross-quote reuse, and origin_quote_only excluded only outside its origin quote. Do not apply a blanket person penalty.

## Contact sheets

- `/disks/disk1/etc/mrsMThatcher/review/generated_identity_dependence/highest_identity_dependence.jpg`
- `/disks/disk1/etc/mrsMThatcher/review/generated_identity_dependence/lowest_recognisability_identity_dependent.jpg`
- `/disks/disk1/etc/mrsMThatcher/review/generated_identity_dependence/origin_quote_only.jpg`
- `/disks/disk1/etc/mrsMThatcher/review/generated_identity_dependence/safe_person_comparison.jpg`
