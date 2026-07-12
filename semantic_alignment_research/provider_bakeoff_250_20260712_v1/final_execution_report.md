# 250-case concurrent four-provider semantic critic experiment

## Executive result

Exactly 250 frozen cases were used, including the unchanged original 25 case IDs. Grok, OpenAI and Claude completed 250/250. Gemini completed 227 and exhausted 23 after one confirmed 429 retry per case. Four-provider comparisons and the meta-critic therefore use 227 complete cases; pairwise Grok/OpenAI/Claude comparisons retain 250-case denominators.

Known spend was **$16.873248**. Wall time was 65.4 minutes versus 195.4 sequential minutes, a 2.99× speed-up. No ambiguous billing exposure occurred.

## Run and selection

- Run ID: `provider_bakeoff_250_20260712_v1`
- Manifest: `semantic_alignment_research/provider_bakeoff_250_20260712_v1/cases.json`
- Selection: original 25 regression cases, existing canonical production/editorial/identity winner cases, and deterministic fingerprint-only semantic strata/fill.
- Active generated fingerprints only; no quarantined images.
- Prompt/schema: unchanged provider-neutral v1.

## Models and pricing

| Provider | Model | Input/M | Cached/M | Output/M | Status |
|---|---|---:|---:|---:|---|
| Grok | grok-4.5 | $2 | $0.50 | $6 | available |
| OpenAI | gpt-5.6-sol | $5 | $0.50 | $30 | available |
| Claude | claude-sonnet-4-6 | $3 | $0.30 | $15 | available |
| Gemini | gemini-3.1-pro-preview | $2 | $0.20 | $12 incl. thinking | preview |

## Concurrency and retry policy

Four independent workers started within milliseconds. Each held at most one active request. Prepared/sending/completed states were checkpointed per attempt. Confirmed transient or schema failures received at most one retry; attempt 3 is impossible. Worker failures did not cancel other providers.

## Preflight

Expected combined cost was $20.38; conservative known maximum with one immediate retry reserve was $31.54 against $45. Provider base maxima were below independent ceilings. Theoretical all-case ambiguous exposure was reported but hard per-attempt gates prevented known spend exceeding ceilings.

## Completion, cost and latency

| Provider | Complete | Exhausted | Attempts | First-attempt success | Retry success | Confirmed failures | Cost | Cost/complete | Mean latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| grok | 250 | 0 | 250 | 250 | 0 | 0 | $2.840154 | $0.0114 | 11.62s |
| openai | 250 | 0 | 250 | 250 | 0 | 0 | $7.252130 | $0.0290 | 12.32s |
| anthropic | 250 | 0 | 250 | 250 | 0 | 0 | $4.311918 | $0.0172 | 15.55s |
| gemini | 227 | 23 | 273 | 227 | 0 | 46 | $2.469046 | $0.0109 | 6.94s |

Gemini failures were 46 confirmed HTTP 429 responses across 23 cases. No provider had an ambiguous outcome.

## Operational distributions

| Provider | Keep | Replace | Unsure | Unrelated | Consequence |
|---|---:|---:|---:|---:|---:|
| grok | 16 | 223 | 11 | 97 | 20 |
| openai | 22 | 222 | 6 | 20 | 14 |
| anthropic | 7 | 206 | 37 | 30 | 13 |
| gemini | 48 | 173 | 6 | 41 | 2 |

## Six pairwise comparisons

| Pair | N | Exact primary | Primary/secondary overlap | Kappa | Decision agreement | Overall rho | Overall MAD |
|---|---:|---:|---:|---:|---:|---:|---:|
| anthropic_vs_gemini | 227 | 32.6% | 76.2% | 0.225 | 73.6% | 0.798 | 15.81 |
| grok_vs_anthropic | 250 | 40.8% | 84.0% | 0.257 | 83.6% | 0.822 | 8.16 |
| grok_vs_gemini | 227 | 35.7% | 85.9% | 0.247 | 80.6% | 0.852 | 14.12 |
| grok_vs_openai | 250 | 45.2% | 80.0% | 0.303 | 89.2% | 0.858 | 12.66 |
| openai_vs_anthropic | 250 | 47.6% | 86.8% | 0.281 | 83.2% | 0.771 | 12.28 |
| openai_vs_gemini | 227 | 27.8% | 67.8% | 0.176 | 81.1% | 0.817 | 14.60 |

## Four-way and meta-critic

- Four-way complete denominator: 227.
- Exact primary agreement: 10.1%.
- Primary/secondary overlap across all four: 43.2%.
- Operational agreement across all four: 71.4%.
- 3/1 taxonomy cases: 76; 2/2: 37; complete disagreement: 12; operational outliers: 32.
- Meta automatic coverage: 190/227 (83.7%).
- Meta actions: {'unsure': 37, 'replace': 181, 'keep': 9}.
- Confidence: {'low': 37, 'high': 161, 'moderate': 29}.
- Disagreement: {'moderate': 160, 'low': 54, 'high': 13}.

Policy A-H coverage is stored in `semantic_alignment_research/provider_bakeoff_250_20260712_v1/policy_comparison.json`. Rules and equal provider weights were not changed.

## Original 25 regression subset

- grok: primary taxonomy unchanged 19/25; operational decision unchanged 24/25.
- openai: primary taxonomy unchanged 19/25; operational decision unchanged 20/25.
- anthropic: primary taxonomy unchanged 22/25; operational decision unchanged 25/25.
- gemini: primary taxonomy unchanged 18/25; operational decision unchanged 22/25.

The model slugs were unchanged, so these differences demonstrate response variability rather than an intentional model migration. The old human labels remain a regression subset and are not treated as fresh validation.

## Free-trade regression

- grok: illustrates_claimed_consequence / related_ideological_substitution; consequence=55; decision=replace.
- openai: illustrates_claimed_consequence / related_ideological_substitution; consequence=76; decision=replace.
- anthropic: illustrates_broader_principle / secondary_theme_match; consequence=45; decision=replace.
- gemini: illustrates_broader_principle / related_ideological_substitution; consequence=80; decision=keep.
- Meta: unsure, confidence low, reasons ['score_outlier:gemini', 'unrecognised_consequence:gemini', 'wide_score_spread'].
- Disagreement: 35 (moderate).

Gemini changed from replace in the 25-case run to keep here; the other three still replace. The unchanged meta rules now defer due to score spread and the Gemini outlier. Consequence recognition remains present, while direct trade mechanism evidence remains weak.

## Human review queue

The queue contains all 250 cases. The 23 Gemini-exhausted cases appear first, followed by operational splits, disagreement/outliers, consequence and unrelated disputes, regression cases and controls. Original 25 labels are attached only as regression labels.

```bash
python3 tools/semantic_meta_critic_review.py --project-dir /disks/disk1/etc/mrsMThatcher --source-run /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/runs/v2_20260711T111526Z --bakeoff-dir /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/provider_bakeoff_250_20260712_v1 --meta-dir /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/provider_bakeoff_250_20260712_v1 --review-file /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/provider_bakeoff_250_20260712_v1/human_validation.json --host 127.0.0.1 --port 8771
```

## Files changed

- Research implementation: `semantic_alignment/large_bakeoff.py`, `analyse_semantic_alignment_large.py`, `finalize_semantic_large_bakeoff.py`.
- Focused tests: `tests/test_semantic_large_bakeoff.py`.
- Reviewer compatibility: `tools/semantic_meta_critic_review.py`.
- All run data and reports: `semantic_alignment_research/provider_bakeoff_250_20260712_v1/`.
- Existing raw provider results and fingerprint stores were not modified.

## Limitations

- Four-provider meta analysis covers 227 cases because Gemini exhausted 23 after rate limiting.
- New 225 cases remain unlabelled; automated consensus cannot rank provider quality.
- The selection is informative and stratified, not a random production sample.
- Same-slug regression variability limits strict reproducibility of individual model judgements.
- No provider weights were learned or changed.

## Tests and safety

- Final compile and `git diff --check`: passed.
- Focused semantic, concurrency, meta-critic, disagreement, calibration and production-log-isolation tests: **117 passed in 2.94s**.
- Loopback reviewer smoke test: 250 cases loaded; a Gemini-exhausted case rendered its unavailable-result state; the server was stopped; the temporary review path was outside the project.
- Pre/post SHA-256 values for both fingerprint stores and all original 25-case raw provider result files matched exactly.
- `git diff --stat` contains only the pre-existing generated-image curation changes (6 tracked files, 13 insertions, 777 deletions); task files remain untracked and nothing was staged.

Fingerprints were reused; none was regenerated. Providers ran concurrently and no case exceeded two attempts. No provider or combined ceiling was exceeded. No tools/search were enabled. No production behaviour or files were changed. The bot was not stopped, restarted or signalled. The canonical simulator was not rerun. Nothing was staged, committed, pushed or deployed.
