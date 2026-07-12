# Vertex AI Gemini post-run recovery

## Executive summary

Vertex AI recovered all **21** still-missing cases: 20 in the bounded recovery and the final case in one separately authorised supplemental attempt. Together with 2 earlier Developer-API recoveries, the recovered-completeness view contains **250/250** Gemini results. Total Vertex cost was **$0.292824**.

## Parent and recovery

- Parent run: `provider_bakeoff_250_20260712_v1` (immutable: 227 completed, 23 exhausted).
- Recovery run: `provider_bakeoff_250_20260712_v1_vertex_recovery`.
- Originally eligible: 23; skipped because already recovered: 2; Vertex targeted: 21.
- Vertex recovered: 21; still missing: 0. Case `378fbf96d18c2e10f5a3` succeeded in the separately recorded supplemental attempt after exhausting the initial Vertex recovery.

## Environment and model parity

- ADC verified without displaying a token; project `spatial-motif-393119`, location `global`.
- Exact model on both transports: `gemini-3.1-pro-preview`; Vertex metadata resolved `publishers/google/models/gemini-3.1-pro-preview`.
- Thinking budget 512; output cap 1600; JSON MIME and identical response schema.
- No temperature override, safety override, tools, search, grounding, retrieval or code execution.
- Prompt input hashes and common prompt/schema versions came directly from the frozen parent manifest.

## Attempts, failures, cost and latency

- Vertex attempts: 24; successful cases: 21; confirmed failures: 3; ambiguous outcomes: 0.
- Retry outcomes: one case recovered after an initial 429; one case exhausted both initial Vertex attempts and then succeeded in the separately authorised one-attempt supplement.
- Known Vertex cost: $0.292824; expected initial preflight $0.316448; ceilings $2.00 Vertex / $2.50 combined recovery. The supplemental attempt cost $0.014866 under its $0.10 ceiling.
- Mean/median successful-call latency: 21.01s / 9.02s.
- Traffic type: `{'TrafficType.ON_DEMAND': 21}`.

## Original versus recovered completeness

| View | Gemini complete | Missing | Keep/replace/unsure |
|---|---:|---:|---|
| Original immutable | 227 | 23 | {'replace': 173, 'keep': 48, 'unsure': 6} |
| Recovered completeness | 250 | 0 | {'replace': 192, 'keep': 52, 'unsure': 6} |

Vertex-only recovered decisions were `{'replace': 17, 'keep': 4}`; primary taxonomy was `{'illustrates_broader_principle': 5, 'contradictory': 3, 'ambiguous': 3, 'related_ideological_substitution': 2, 'direct_illustration': 1, 'secondary_theme_match': 3, 'unrelated': 4}`.

## Comparison effects

- Four-way denominator: 227 → 250.
- Operational patterns: `{'2_keep_2_replace': 3, '3_keep_1_replace': 1, '3_replace_1_keep': 14, '4_keep': 4, '4_replace': 158, 'incomplete_provider_set': 23, 'mixed_with_unsure': 47}` → `{'2_keep_2_replace': 4, '3_keep_1_replace': 1, '3_replace_1_keep': 15, '4_keep': 4, '4_replace': 177, 'mixed_with_unsure': 49}`.
- Gemini-only keeps: 14 → 15.
- Meta actions: `{'unsure': 37, 'replace': 181, 'keep': 9}` → `{'unsure': 40, 'replace': 200, 'keep': 10}`; changes on the original 227 shared cases: 0.
- Disagreement bands: `{'moderate': 160, 'low': 54, 'high': 13}` → `{'moderate': 174, 'low': 62, 'high': 14}`.
- Human-validated Gemini actionable accuracy in recovered view: 95.1% on 244 decisions.
- Meta-critic actionable accuracy: 95.7% at 84.0% coverage.

Six pairwise comparisons, four-way agreement, policies A–H, disagreement results and the prioritised queue were regenerated under `recovered_view/`, `validation/` and `provider_outliers/`. No provider ranking changed: Gemini retains the highest human actionable agreement on its available cases.

| Pair | N | Exact taxonomy | Operational agreement |
|---|---:|---:|---:|
| Claude vs Gemini | 250 | 33.2% | 74.4% |
| Grok vs Claude | 250 | 40.8% | 83.6% |
| Grok vs Gemini | 250 | 36.4% | 81.2% |
| Grok vs OpenAI | 250 | 45.2% | 89.2% |
| OpenAI vs Claude | 250 | 47.6% | 83.2% |
| OpenAI vs Gemini | 250 | 28.0% | 82.0% |

| Policy | Coverage | Accuracy | False keep | False replace |
|---|---:|---:|---:|---:|
| A | 72.4% | 99.4% | 0 | 1 |
| B | 85.6% | 95.3% | 0 | 10 |
| C | 84.0% | 95.7% | 0 | 9 |
| D | 94.4% | 92.4% | 0 | 18 |
| E | 78.0% | 97.4% | 0 | 5 |
| F | 85.6% | 95.3% | 0 | 10 |
| G | 71.6% | 99.4% | 0 | 1 |
| H | 85.6% | 95.3% | 0 | 10 |

## Free-trade regression

The free-trade case was already complete in the parent run and was not rerun. Its provider result, meta decision, disagreement result and lone-Gemini-keep classification are unchanged.

## Files changed

- `semantic_alignment/vertex_recovery.py`
- `recover_gemini_vertex.py`
- `recover_gemini_vertex_final_case.py`
- `tests/test_vertex_gemini_recovery.py`
- Recovery artefacts beneath `semantic_alignment_research/provider_bakeoff_250_20260712_v1_vertex_recovery/`.

## Tests

Python compilation and `git diff --check` passed. Focused Vertex, semantic-alignment, provider, meta-critic, disagreement, calibration and production-log-isolation tests: **129 passed**.

## Git state

`git diff --stat` contains only the pre-existing generated-image curation changes: 6 tracked files, 13 insertions and 777 deletions plus four binary removals. Recovery implementation, tests and research artefacts are untracked. Nothing is staged.

## Safety

Only the 21 cases still missing after prior recovery were called through Vertex; the supplemental call targeted only the one case still missing afterward. All 23 originally exhausted cases remain in the manifest. No completed Gemini case was rerun. Grok, OpenAI and Claude were not called. The original run remained immutable. ADC was used; no API key or credential was printed or stored. No tools/search/grounding were enabled. No production behaviour or files changed; the bot was not stopped, restarted or signalled. Nothing was staged, committed, pushed or deployed.
