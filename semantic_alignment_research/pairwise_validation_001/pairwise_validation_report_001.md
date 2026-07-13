# Pairwise Editorial Validation Report 001

## Executive conclusion

Pairwise ranking is **not yet supported for a larger experiment**. Human review completed all 50 blinded pairs, but **22 of 50** pairs selected neither image, showing that deterministic challenger construction did not reliably produce a publishable alternative. Grok achieved **66.0%** full-set agreement, but damaged **4 of 13** strong controls that Tony retained.

## Manifest and human review

- 50 deterministic cases; Everest and free trade included.
- Current-winner position independently randomised and sealed.
- Human outcomes: current winner 17, challenger 11, neither 22, unsure 0.
- Preference strength: slight 9, moderate 28, strong 13.
- Previously rejected winners: 28; challenger preferred 8; neither acceptable 20.
- Previously approved controls: 16; current retained 13; challenger preferred 2; neither acceptable 1.

## Provider calibration

| Provider | Valid / 15 | Exact agreement on valid results | Failures | Known cost |
|---|---:|---:|---:|---:|
| anthropic | 0 |  | 15 | $0.0000 |
| gemini | 13 | 53.8% | 2 | $0.1073 |
| grok | 15 | 60.0% | 0 | $0.1526 |
| openai | 0 |  | 15 | $0.0000 |


OpenAI and Anthropic returned repeated HTTP 400 responses to the structured schema. Grok completed 15/15 at {metrics['providers']['grok']['exact_agreement']:.1%} agreement. Gemini completed 13/15 at {metrics['providers']['gemini']['exact_agreement']:.1%}; Developer API quota failures invoked Vertex and two cases remained failed. Attempt histories are preserved and no case exceeded two Developer attempts.

## Safety decision

The predefined calibration gate selected Grok. Grok then completed the remaining 35 cases, producing 50/50 model judgements at a combined Grok cost of ${costs['grok']+costs['grok_remaining']:.4f}. Existing five earlier pairwise outputs were not overwritten.

## Method comparison

The evaluated pairwise method matched Tony in {exact}/50 cases. For previously rejected winners it matched {model_bad_challenger}/8 challenger choices and {model_bad_neither}/20 neither choices. Among {len(strong_current)} strong controls where Tony retained the current winner, it retained {model_strong_retained} and displaced or rejected {model_strong_damaged}. Methods requiring a known soft-light winner or selector score margin remain unavailable because those candidate traces were not present.

## Statistical limitations

The 50 cases reuse a deliberately enriched prior review set; candidates share images; challenger construction used lexical overlap over cached fingerprints; 44% neither choices reveal strong construction bias; the 15 calibration cases informed provider selection; and there is no independent hold-out. OpenAI/Anthropic compatibility failures also prevent a fair four-provider ranking.

## Recommendation

Do not proceed to 150-200 cases yet. Pairwise judgement shows useful correction ability and fixes Everest without a special rule, but ordinary-control damage and the 44% neither rate fail the stated safety criteria. First build challengers from real selector traces or cached per-quote scores, repair provider structured-output compatibility, and validate on an independent bounded set.
