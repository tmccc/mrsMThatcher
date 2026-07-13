# Pairwise Editorial Validation Report 001 - Corrected

## Correction notice

The original derived Markdown is preserved and contains unresolved template expressions because later report sections were appended as an ordinary string rather than an f-string. This canonical correction is deterministically rendered and rejects unresolved tokens.

## Corrected results

- Grok calibration agreement: **60.0%**.
- Gemini calibration agreement: **53.8%** on 13 valid cases.
- Combined Grok cost: **$0.4445**.
- Full Grok agreement: **33/50 (66.0%)**.
- Previously rejected winners: matched **6/8** challenger choices and **12/20** neither choices.
- Strong retained controls: **9/13** retained; **4** damaged.

## Infrastructure diagnosis

OpenAI and Anthropic failures were structured-output infrastructure defects, not editorial judgements. The unsupported `uniqueItems` keyword was added by the new pairwise schema. The response bodies were not persisted by the original runner, a separate observability limitation. Provider-specific serializers now remove unsupported transport keywords and retain strict normalized validation. Minimal compatibility probes are recorded separately.

## Challenger analysis

The original constructor used global lexical/theme overlap plus image quality and tone, not a same-decision selector runner-up. Challenger source counts and neither rates are in `challenger_source_analysis.csv`. Tony chose neither in 22/50 cases (44%), so this run cannot establish pairwise ranking quality independently of challenger quality.

Exact selector traces exist for **19/50** cases. Applying active-state, identity, fingerprint, duplicate, quality, production-score and topic floors produces **13** credible reconstructed pairs; **37** correctly become `no_credible_challenger`. **11** challengers change.

## Interpretation

The 44% neither result invalidates expansion of the original construction. It does **not** disprove pairwise ranking: it primarily demonstrates that globally selected lexical alternatives are poor controls. The improved policy starts from exact selector evidence and permits no challenger when evidence is absent.

## Bounded-pilot readiness

The independent improved pilot contains **25** cases and has not been executed. A pilot is justified only if both 2+2 provider compatibility checks succeed and at least 20 credible independent pairs exist. This report does not recommend a 150-200-case run.

## Recommendation

**Repair complete; run bounded improved pilot** if the compatibility output reports 2/2 success for both providers and this manifest remains at least 20 cases. Otherwise the status is **challenger construction still inadequate**.
