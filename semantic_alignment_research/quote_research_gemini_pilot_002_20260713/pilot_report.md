# Gemini grounded quote-research pilot

- Pilot records: 20
- Valid packets: 13
- Overall completion: 65.0%
- Unresolved quote IDs: 56c77eedf86a10a6748dc8e20d096e415708541735c07f5c88a338f49d29cc9c, 53699726287c4fed9ef2b086e53cc2938b1fd482a7b5541f4718df3439598215, a4f1d422097a48114bf30a587c04cf05859ff030d2df3d5d9051c6ca57a7943c, e0ec17fd0cdf45f02d1b9cedffa8bc626e10592203559baba18b7622feeee491, 9efcca12a991db11a019126881080676677742613499ab9623b2048120c23997, f9a7dfe4827a8da531aac6576b2a94e3e509cbaf93b17c96dc480ca4f16ac3c7, f67b4badcfd92e59baa0a91e247963365f3d09e942676a27fa88d25b60c227ee
- Developer completions: 13
- Vertex completions: 0
- Developer 429 trigger attempts: 0
- Developer paused: False (None)
- Direct-to-Vertex cases after pause: 0
- Open validation-failure records: 1
- Legacy malformed-response parse failures reclassified: 2
- Read timeouts after transmission: 3
- Operator-interruption ambiguous attempts: 4
- Grounded sources retained: 33
- Grounding-linked support segments: 94
- Source coverage among valid packets: 100.0%
- Source coverage across the 20 intended records: 65.0%
- Verification statuses: {'normalised': 1, 'excerpt': 2, 'composite': 1, 'exact': 6, 'unverified': 1, 'variant': 2}
- Combined conservatively reconstructed cost: $0.531889
- Maximum possible ambiguous billing exposure: $0.661102
- Known cost plus maximum possible exposure: $1.192991
- Cost per valid quote: $0.040915
- Projected cost for 632 distinct quotes: $25.86
- Exposure-adjusted upper projection for 632: $58.00
- Preflight expected cost for 20: $1.253462
- Recommendation: Do not run the full corpus yet; resolve completion reliability and manually audit verification/citation precision first.

## Method

One quote was submitted per ordinary Gemini generate-content request with Google Search grounding. This was not the Deep Research agent. Sources and support claims in normalised packets were reconstructed from returned grounding chunks/support metadata; unsupported model-written citation claims were discarded.

The Developer API was tried first. Two confirmed 429 responses for one quote pause Developer for the run, route that quote to Vertex, and route all later unfinished quotes directly to Vertex without a probe. Non-quota transient failures do not activate the pause.

No 429 occurred in this run, so the Vertex fallback was correctly not activated. Four requests interrupted while in flight were not repeated. Two malformed responses produced by the original adapter were deterministically reclassified without rewriting attempt history; their one permitted schema retry was then used.

Costs use official Gemini token rates plus a conservative $0.014 charge per returned grounding query. Developer API Search grounding may include monthly no-charge allowance, so this is an upper reconstruction rather than an authoritative invoice. Pricing reference: https://ai.google.dev/gemini-api/docs/pricing

## Limitations

Only 13 of 20 records produced valid packets. Grounding metadata establishes a link between retained sources and response segments, but a human still needs to verify that each source supports the historical claim attributed to it. The completion and qualitative audit gaps make extrapolation to all 632 premature.

## Safety

The run used only the immutable 20-record pilot manifest and did not alter production files or existing research artefacts. Nothing was staged, committed, pushed or deployed.
