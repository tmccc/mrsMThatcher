# Four-provider semantic critic bake-off

## Executive result

Gemini completed the same frozen 25 cases previously evaluated by Grok, OpenAI and Claude. Existing provider artifacts remained byte-for-byte unchanged. The four-provider comparison and prioritised blinded A/B/C/D review are complete.

**Provisional recommendation: Insufficient human evidence.** Gemini is a useful distinct critic, but automatic consensus is not ground truth and no four-way human preferences exist yet.

## Gemini model, credential and pricing

The authenticated model listing exposed stable `gemini-2.5-pro`, but generation returned a confirmed 404 stating that model is unavailable to new users. The run therefore used the strongest accessible suitable replacement:

- model: `gemini-3.1-pro-preview`;
- version: `3.1-pro-preview-01-2026`;
- status: preview;
- structured output and thinking supported;
- 1,048,576 input and 65,536 output-token model limits;
- $2/M input, $0.20/M cached input, $12/M output including thinking for prompts below 200k.

The run used a 1,600-token output cap and 512-token thinking budget. Google reported zero thinking tokens separately. No search, grounding, URL context, code execution, tools or retrieval configuration was supplied.

`GEMINI_API_KEY`, loaded through ignored `mrsMThatcher.env`, is canonical; `GOOGLE_API_KEY` is a compatibility alias. The credential was never printed or persisted.

Official references: [Gemini 3.1 Pro Preview](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-pro-preview) and [pricing](https://ai.google.dev/gemini-api/docs/pricing).

## Frozen input and prompt parity

- `cases.json` SHA-256: `74a796fc5d2d6d3f1a9b084b48a4b7b2820eaea7cee7f6c34ddc69da95345f7e`;
- 25 unique cases and the free-trade case present;
- prompt version `provider-neutral-picture-editor-v1` unchanged;
- output schema v1 unchanged;
- all normalised prompt hashes recorded in `input_parity_manifest_four_provider.json`;
- Grok, OpenAI and Claude results, ledgers, prior comparisons, mappings and preferences re-hashed unchanged after Gemini.

Gemini received the identical semantic prompt and allow-listed cached fingerprints. It received no competing result, human label, production context, origin prompt, raw image, expected answer or comparison statistic.

## Preflight and execution

| Metric | Preflight | Actual |
|---|---:|---:|
| cases | 25 | 25 |
| input tokens | 51,900 | 34,516 |
| output/thinking allowance | 22,500 expected | 16,818 output |
| thinking tokens | bounded at 512/call | 0 reported |
| cost | $0.373800 expected | **$0.270848** |
| conservative maximum | $0.583800 | n/a |
| hard ceiling | $1.500000 | passed |
| mean latency | n/a | 7.828 s |
| median latency | n/a | 7.808 s |

There were 25 completed billed calls, zero inference retries, zero schema failures and zero ambiguous outcomes. Before successful execution, one confirmed 429 exposed the then-unlinked zero quota, and one confirmed 404 established that stable 2.5 Pro was unavailable to new users. Both returned no model output and remain preserved as confirmed failures, not billed critic results.

## Provider summaries

| Provider | Unrelated | Consequence | Keep | Replace | Unsure | Overall mean / median | Cost | Mean latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Grok | 7 | 4 | 4 | 20 | 1 | 32.48 / 25 | $0.318730 | 9.935 s |
| OpenAI | 2 | 5 | 4 | 20 | 1 | 44.16 / 44 | $0.724690 | 12.017 s |
| Claude | 1 | 2 | 1 | 20 | 4 | 31.64 / 30 | $0.428988 | 16.173 s |
| Gemini | 4 | 2 | 8 | 17 | 0 | 43.44 / 50 | $0.270848 | 7.828 s |

Gemini primary relationships were: contradictory 5, broader principle 6, claimed consequence 2, ideological substitution 4, secondary theme 3, unrelated 4 and direct illustration 1.

## Six pairwise comparisons

| Pair | Exact primary | Primary/secondary overlap | Kappa | Decision agreement | Overall rho | Overall MAD |
|---|---:|---:|---:|---:|---:|---:|
| Grok / OpenAI | 36% | 84% | 0.22 | 100% | 0.87 | 12.08 |
| Grok / Claude | 56% | 84% | 0.48 | 80% | 0.81 | 9.08 |
| Grok / Gemini | 40% | 88% | 0.30 | 84% | 0.87 | 14.88 |
| OpenAI / Claude | 36% | 80% | 0.20 | 80% | 0.71 | 15.08 |
| OpenAI / Gemini | 40% | 80% | 0.32 | 84% | 0.83 | 11.84 |
| Claude / Gemini | 52% | 84% | 0.42 | 68% | 0.80 | 18.28 |

Full confusion matrices, eight score correlations/MADs, largest disagreements and consequence/unrelated disputes are in `comparison_four_provider.json`.

## Four-way consensus and outliers

- all-four exact primary agreement: 24%;
- all-four primary-or-secondary overlap: 60%;
- all-four operational-decision agreement: 68%;
- 3/1 primary splits: 4;
- 2/2 primary splits: 4;
- complete four-way disagreements: 2;
- operational-decision outliers: 7;
- only-one-provider unrelated: 6;
- only-one-provider claimed consequence: 3.

Each case includes modal relationship/decision, median relevance/directness/overall score and provider distance from those medians. Consensus is used only for review prioritisation, not treated as truth.

The prioritised queue contains 24 cases: complete disagreements first, then 2/2 splits, 3/1 outliers, consequence disputes, unrelated disputes, operational disagreements, free trade and consensus controls.

## Calibration evidence

Gemini sits between the prior critics on unrelated use, but ties Claude for the fewest primary consequence classifications. It recommends keeping eight cases, twice Grok/OpenAI and eight times Claude, while still replacing 17. Its score ordering correlates strongly with Grok and OpenAI, but its absolute scores differ most from Claude. Gemini was fastest and cheapest in this run, with complete schema reliability.

These are automatic observations, not proof of superior calibration. Gemini's lower cost, speed and distinct operational choices make it useful for blinded review; they do not establish it as the best editor.

## Free-trade case

| Provider | Primary / secondary | Rel. | Direct | Mech. | Conseq. | Principle | Overall | Decision |
|---|---|---:|---:|---:|---:|---:|---:|---|
| Grok | ideological substitution / consequence | 38 | 18 | 8 | 52 | 35 | 28 | replace |
| OpenAI | consequence / ideological substitution | 58 | 24 | 10 | 72 | 56 | 49 | replace |
| Claude | broader principle / secondary theme | 38 | 12 | 10 | 42 | 40 | 22 | replace |
| Gemini | broader principle / ambiguous | 60 | 40 | 30 | 60 | 75 | 55 | replace |

All recognise prosperity at least partly as a consequence; none calls the pairing unrelated; all recommend replacement. Gemini gives the pairing the highest principle score and overall suitability while explicitly noting that socialist/capitalist political transition substitutes for trade liberalisation and that ports, markets and commerce are absent.

## Blinded human review

A new sealed A/B/C/D mapping is independent of earlier mappings. Two A/B answers remain untouched; no A/B/C answers existed; no four-way answer was created during smoke testing.

```bash
python3 tools/semantic_alignment_bakeoff_review.py \
  --project-dir /disks/disk1/etc/mrsMThatcher \
  --source-run /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/runs/v2_20260711T111526Z \
  --bakeoff-dir /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/provider_bakeoff_25_20260712 \
  --host 127.0.0.1 --port 8768
```

The page follows `prioritised_four_provider_review_queue.json`, asks for best and optional second-best critique, and separately asks which operational decision is preferred. A loopback smoke test rendered A/B/C/D without provider leakage and saved no preference.

## Tests, files and safety

- Python compilation: passed.
- `git diff --check`: passed.
- semantic pipeline, calibration, bake-off and production-log-isolation tests: **86 passed in 2.11s**.
- API responses were mocked in tests.

Research-only code changed in `semantic_alignment/bakeoff.py`, `analyse_semantic_alignment_bakeoff.py`, `tools/semantic_alignment_bakeoff_review.py` and `tests/test_semantic_alignment_bakeoff.py`. Gemini metadata, ledger, results, four-provider comparison, mapping, queue and reports were added under the existing bake-off directory.

Cached fingerprints were reused; none was regenerated. Grok, OpenAI and Claude were not rerun or altered. Exactly 25 Gemini cases completed. No search, grounding or tools were enabled. No production behaviour changed. The bot was not stopped, restarted or signalled. No production state, config, history, receipt, log or metadata was edited by the research process. The canonical simulator was not rerun. Nothing was staged, committed, pushed or deployed.

`git diff --stat` continues to show only the pre-existing generated-image curation changes because research files are untracked. `git status --short` retains those unrelated changes and existing research/runtime artifacts.
