# Original Editorial Matching Backtest

## Executive verdict

This is an offline full-original-pool ranking experiment, not a historical replay of image-cycle availability. The original run had a critical scale bug: the experimental `dimension_scores` and `overall_editorial_utility` are 0..10, but the first backtest treated them as 0..100. This corrected run validates that scale explicitly. The editorial layer is useful as an analysis signal, but the conservative recommendation is still to keep it for analysis/narrow production-design experiments rather than deploy it directly.

## Corrected scale versus broken run

- Corrected assumption: `dimension_scores` are 0..10 and are normalised by dividing by 10.
- Corrected assumption: `overall_editorial_utility` is 0..10; 5.5 is neutral and 9.0 is strongly positive.
- Broken run assumption: both values were treated as if they were 0..100.

| metric | broken run | corrected run |
| --- | ---: | ---: |
| winner changes, low | 0 | 0 |
| winner changes, medium | 0 | 3 |
| winner changes, high | 3 | 8 |
| combined-medium winner entropy | 3.892 | 3.881 |

## Input validation

- Analysed quotes: 633
- Original images validated: 69
- Recent regular posts recovered: 30
- Broad sample size: 100
- Diagnostic freedom quote included: True
- Experimental analysis_kind: `original_editorial_experiment`

## Exact production scorer inspected/reused

The tool imports `mrsMThatcher2.py` for pure helpers only. Importing the module does not start the bot loop, contact APIs, mutate state, or post. Reused helpers include `score_image_for_quote`, `build_image_topic_idf`, `normalise_tag`, phrase matching helpers and visual-energy scoring.

Production components: topics, tone_mood, visual_energy, scene_activity_symbols, historical, mismatches, quality, with strong visual mismatch returning an ineligible image.

## Experimental scoring design

- Variant A: normalised affinity/editorial-function/best-quote-type matching, IDF-downweighted for common concepts, with avoid_quote_types as a negative signal.
- Variant B: deterministic quote dimension profile matched against 12 fixed image dimensions.
- Variant C: conservative combined adjunct layer, tested at low/medium/high weights; report tables use medium unless stated.

## Vocabulary normalisation statistics

- Broken-run raw unique terms: 895
- Broken-run normalised unique concepts: 866
- Raw unique terms: 895
- Normalised unique concepts: 839
- Most frequent normalised concepts:

  - duty: 197
  - leadership: 117
  - resolve: 110
  - principle: 110
  - conviction: 102
  - statesmanship: 86
  - authority: 83
  - patriotism: 60
  - public_service: 44
  - defiance: 30
  - freedom: 20
  - persuasion: 19
  - light_humour: 17
  - warning: 16
  - service: 14

- Rare normalised concepts:

abstract_philosophy_without_agency, adds_ceremonial_grace, adds_warmth_to_text, administrative_action, administrative_resolve, admonitions, affirmative_framing, aggressive_confrontation_without_reflection, aggressive_defiance, aggressive_warning, amplify_conviction, anchor_statesmanship, anchors_conviction_quotes, anchors_optimism_after_struggle, anchors_personal_conviction_quotes, anchors_principle, anchors_statesmanlike_identity, apolitical_lifestyle, apology, application, argument, argumentative_emphasis, arrival_or_departure, assert_authority, assert_confidence, assert_leadership, authority_establishment, balance_of_public_private, battle_rhetoric, battlefield_or_war_rhetoric

## Recent real-post results

| sample | line_no | actual | actual baseline rank | baseline winner | conservative combined winner | actual combined rank |
| --- | ---: | --- | ---: | --- | --- | ---: |
| recent | 542 | t12.jpg | 49 | t59.jpg | t59.jpg | 49 |
| recent | 411 | t39.jpg | 25 | t25.jpg | t25.jpg | 25 |
| recent | 495 | t57.jpg | 49 | t25.jpg | t25.jpg | 49 |
| recent | 427 | t40.jpg | 1 | t40.jpg | t40.jpg | 1 |
| recent | 268 | t11.jpg | 1 | t11.jpg | t11.jpg | 1 |
| recent | 42 | t29.jpg | 1 | t29.jpg | t29.jpg | 1 |
| recent | 112 | t24.jpg | 1 | t24.jpg | t24.jpg | 1 |
| recent | 470 | t15.jpg | 1 | t15.jpg | t15.jpg | 1 |
| recent | 388 | t08.jpg | 2 | t11.jpg | t11.jpg | 2 |
| recent | 304 | t48.jpg | 3 | t40.jpg | t40.jpg | 3 |
| recent | 287 | t25.jpg | 1 | t25.jpg | t25.jpg | 1 |
| recent | 267 | t43.jpg | 1 | t43.jpg | t43.jpg | 1 |
| recent | 543 | t06.jpg | 1 | t06.jpg | t06.jpg | 1 |
| recent | 480 | t33.jpg | 4 | t11.jpg | t11.jpg | 4 |
| recent | 249 | t07.jpg | 3 | t25.jpg | t25.jpg | 2 |
| recent | 335 | t64.jpg | 2 | t23.jpg | t23.jpg | 2 |
| recent | 409 | t44.jpg | 2 | t48.jpg | t48.jpg | 2 |
| recent | 4 | t28.jpg | 4 | t40.jpg | t40.jpg | 3 |
| recent | 16 | t34.jpg | 5 | t08.jpg | t08.jpg | 5 |
| recent | 48 | t69.jpg | 1 | t69.jpg | t69.jpg | 1 |
| recent | 499 | t39.jpg | 2 | t25.jpg | t25.jpg | 2 |
| recent | 36 | t36.jpg | 1 | t36.jpg | t48.jpg | 3 |
| recent | 515 | tg_3fb0e6e6f45d742323a00b0d45fc0a0a524bc0ed0ba11a2c84b6c54c86fb2a0f.png |  | t36.jpg | t36.jpg |  |
| recent | 147 | tg_6ed99b617e093add13e311291c0bf176dceccaf7978a98885476d932ec0c4493.png |  | t36.jpg | t36.jpg |  |
| recent | 524 | tg_a017d22c575c01e7eb9c8287e1b62f85d07c34a1a663d2eac974d995ace7f83c.png |  | t11.jpg | t11.jpg |  |
| recent | 78 | tg_04d26fbb2aa5ad3824e1f452699b6e9265298b84a61458f524e19db273a70550.png |  | t36.jpg | t36.jpg |  |
| recent | 168 | t59.jpg | 2 | t29.jpg | t29.jpg | 2 |
| recent | 509 | t05.jpg | 1 | t05.jpg | t05.jpg | 1 |
| recent | 502 | tg_3e1a0b3d5d9dede45d7780b3032297c02d710093bb26876608d34d5ccc160586.png |  | t25.jpg | t25.jpg |  |
| recent | 594 | t70.jpg | 3 | t25.jpg | t25.jpg | 3 |

## Broad quote-sample results

| sample | line_no | actual | actual baseline rank | baseline winner | conservative combined winner | actual combined rank |
| --- | ---: | --- | ---: | --- | --- | ---: |
| broad | 126 |  |  | t65.jpg | t65.jpg |  |
| broad | 387 |  |  | t25.jpg | t25.jpg |  |
| broad | 433 |  |  | t24.jpg | t24.jpg |  |
| broad | 5 |  |  | t25.jpg | t25.jpg |  |
| broad | 332 |  |  | t20.jpg | t20.jpg |  |
| broad | 209 |  |  | t40.jpg | t40.jpg |  |
| broad | 122 |  |  | t20.jpg | t20.jpg |  |
| broad | 119 |  |  | t20.jpg | t20.jpg |  |
| broad | 63 |  |  | t36.jpg | t36.jpg |  |
| broad | 232 |  |  | t36.jpg | t36.jpg |  |
| broad | 372 |  |  | t08.jpg | t08.jpg |  |
| broad | 161 |  |  | t16.jpg | t16.jpg |  |
| broad | 160 |  |  | t58.jpg | t58.jpg |  |
| broad | 630 |  |  | t11.jpg | t11.jpg |  |
| broad | 271 |  |  | t11.jpg | t11.jpg |  |
| broad | 319 |  |  | t38.jpg | t23.jpg |  |
| broad | 384 |  |  | t38.jpg | t38.jpg |  |
| broad | 40 |  |  | t59.jpg | t59.jpg |  |
| broad | 566 |  |  | t59.jpg | t59.jpg |  |
| broad | 604 |  |  | t54.jpg | t54.jpg |  |
| broad | 601 |  |  | t15.jpg | t15.jpg |  |
| broad | 620 |  |  | t36.jpg | t36.jpg |  |
| broad | 260 |  |  | t20.jpg | t20.jpg |  |
| broad | 262 |  |  | t25.jpg | t25.jpg |  |
| broad | 500 |  |  | t39.jpg | t39.jpg |  |
| broad | 109 |  |  | t58.jpg | t58.jpg |  |
| broad | 541 |  |  | t25.jpg | t25.jpg |  |
| broad | 616 |  |  | t25.jpg | t25.jpg |  |
| broad | 503 |  |  | t25.jpg | t25.jpg |  |
| broad | 450 |  |  | t48.jpg | t48.jpg |  |
| broad | 454 |  |  | t51.jpg | t51.jpg |  |
| broad | 183 |  |  | t20.jpg | t20.jpg |  |
| broad | 514 |  |  | t58.jpg | t58.jpg |  |
| broad | 560 |  |  | t20.jpg | t20.jpg |  |
| broad | 455 |  |  | t23.jpg | t23.jpg |  |
| broad | 632 |  |  | t36.jpg | t36.jpg |  |
| broad | 241 |  |  | t06.jpg | t06.jpg |  |
| broad | 178 |  |  | t11.jpg | t11.jpg |  |
| broad | 621 |  |  | t36.jpg | t36.jpg |  |
| broad | 52 |  |  | t36.jpg | t36.jpg |  |

## t70 freedom-quote case study

- Quote line 594: There is an increasing belief that freedom is divisible. No myth is more dangerous. Freedom is indivisible.
  - live logged components for actual post: historical=0.0, mismatches=0.0, quality=1.8, scene_activity_symbols=16.0, tone_mood=14.0, topics=0.0, visual_energy=8.0
  - broken-run t70 Variant A score/rank: 2.50 / 17
  - broken-run t70 Variant B score/rank: -4.37 / 12
  - broken-run t70 combined-medium rank/score: 3 / 39.16
  - t70 baseline rank/score: 3 / 39.76
  - t70 Variant A score/rank: 3.44 / 15
  - t70 Variant B score/rank: 3.90 / 12
  - t70 combined-medium rank/score: 3 / 42.11
  - Largest affinity matches: authority, freedom, resolve

## Winner-concentration analysis

- Baseline top-1 entropy: 3.892
- Combined-medium top-1 entropy: 3.881
- Baseline most frequent winners:
  - t25.jpg: 24
  - t36.jpg: 21
  - t11.jpg: 11
  - t20.jpg: 9
  - t40.jpg: 8
- Combined most frequent winners:
  - t25.jpg: 25
  - t36.jpg: 20
  - t11.jpg: 11
  - t20.jpg: 9
  - t40.jpg: 8

## Examples where the editorial layer clearly improves a match

- line 36: baseline `t36.jpg` -> combined `t48.jpg`
  - quote: It is always important in matters of high politics to know what you do not know. Those who think that they know, but are mistaken, and act upon their mistakes, are the most dangero
  - editorial signal: authority, leadership, resolve, statesmanship, warning
- line 319: baseline `t38.jpg` -> combined `t23.jpg`
  - quote: Never have our basic values, the Christian values which rest on Hebrew and Hellenic foundations, been so menaced as they are today. Family life, the innocence of children, public d
  - editorial signal: family
- line 588: baseline `t23.jpg` -> combined `t25.jpg`
  - quote: We must never forget that it is in fact capitalism which has the moral quality in society. Not socialism, which is the elevation of the power of the government over the people.
  - editorial signal: authority, resolve

## Examples where it clearly makes a match worse

No sampled case showed the conservative layer replacing the baseline winner with an image more than 3 production-score points worse.

## Examples with no meaningful change

- line 542: winner remains `t59.jpg`
  - quote: We intend freedom and justice to conquer. Yes, we do have a creed and we wish others to share it. But it is not part of our policy to impose our beliefs by force or threat of force
- line 411: winner remains `t25.jpg`
  - quote: Within our universities we must uphold freedom of thought and of discussion. We must debate the burning issues. We must fiercely fight the battle of ideas. And we must do all this
- line 495: winner remains `t25.jpg`
  - quote: A free way of life gives both dignity and prosperity that you do not get in Communism.
- line 427: winner remains `t40.jpg`
  - quote: Splits and disagreements over important issues never did a Party so much harm as the absence of honest, principled debate.
- line 268: winner remains `t11.jpg`
  - quote: Qualities that made Britain what she is haven't changed. Decency, fair play, honest dealing, respect for other's rights - even the rights of people you dislike, especially the righ
- line 42: winner remains `t29.jpg`
  - quote: I usually make up my mind about a man in ten seconds, and I very rarely change it.

## Sensitivity to editorial weight

| weight | winner changes vs baseline |
| --- | ---: |
| low | 0 |
| medium | 3 |
| high | 8 |

## Recommendation

Keep the new editorial data for analysis and use it in a narrow production-design experiment only. It appears capable of surfacing meaningful rhetorical matches, but the portrait-concentration risk remains real enough that it should not replace or dominate the existing production matcher.

## Output notes

- `original_editorial_matching_backtest.json` contains full rankings.
- `review/original_editorial_backtest/` contains a small set of contact sheets for visual inspection.
