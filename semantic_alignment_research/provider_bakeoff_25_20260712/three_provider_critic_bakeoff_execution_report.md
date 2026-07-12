# Three-provider semantic critic bake-off

## 1. Executive result

Claude Sonnet 4.6 completed the exact frozen 25-case critic set previously evaluated by Grok and OpenAI. The prior case manifest, Grok/OpenAI results, ledgers, comparison, sealed A/B mapping, and two saved A/B human preferences remained byte-for-byte unchanged.

The automatic evidence does not establish a winner. Claude agreed more often with Grok on exact taxonomy than OpenAI did, used `unrelated` least often, but identified fewer primary claimed-consequence relationships and was more cautious operationally. Three-way exact taxonomy agreement was 28% and three-way keep/replace agreement was 80%. **Provisional recommendation: Insufficient human evidence.**

## 2. Verified Anthropic model and pricing

Authenticated `GET /v1/models` returned:

- friendly name: Claude Sonnet 4.6;
- API model: `claude-sonnet-4-6`;
- created: `2026-02-17T00:00:00Z`;
- maximum input: 1,000,000 tokens;
- maximum output supported by model: 128,000 tokens;
- structured outputs: supported;
- low effort: supported.

This run capped output at 1,600 tokens and used low effort without extended thinking, tools, citations, files, search, or computer use. Pricing used the official $3.00/M input and $15.00/M output rates. Authenticated model metadata is preserved in `pricing/anthropic_models.json`; pricing verification is in `pricing/anthropic_pricing_verification.json`. See [Anthropic Models API](https://platform.claude.com/docs/en/api/models/list) and [structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs).

The credential was loaded from the ignored project `mrsMThatcher.env` through `ANTHROPIC_API_KEY`. It was never printed, persisted, logged, or placed in a command or report.

## 3. Frozen-case verification

`cases.json` remained unchanged at SHA-256 `74a796fc5d2d6d3f1a9b084b48a4b7b2820eaea7cee7f6c34ddc69da95345f7e`.

- 25 records;
- 25 unique case IDs;
- free-trade case `55f5cd6d633c143f5170` present;
- unchanged prompt version `provider-neutral-picture-editor-v1`;
- unchanged common output schema v1;
- every normalised prompt, quote record, and image record hashed in `input_parity_manifest_three_provider.json`;
- aggregate ordered input-row hash `22b6a29a945d4c4c2819acc35a383d76d1cec3dfd83decf29139f81c5d6e65a6`.

The old provider artifacts were checked against their pre-Claude hashes after execution. Grok/OpenAI result and ledger files, `comparison.json`, the original sealed A/B map, and `blinded_preferences.json` all remained unchanged.

## 4. Prompt and schema parity

All three providers receive the same `common_prompt()` string and the same allow-listed cached quote and image fingerprints. No request contained another provider result, automatic comparison, human label, production winner, generation prompt, raw image, origin relationship, engagement data, expected answer, or free-trade judgement.

Grok and OpenAI retained their existing strict-schema envelopes. Claude used Anthropic Messages `output_config.format` with the same fields, enums, and required-field set. Anthropic's grammar rejects numeric `minimum`/`maximum` and array/string maximum keywords; only those unsupported envelope keywords were removed. The shared local validator still strictly rejects missing/extra fields, invalid enums, booleans as scores, non-finite scores, and scores outside 0-100. No malformed response is repaired silently.

## 5. Cost preflight

| Metric | Claude estimate |
|---|---:|
| calls | 25 |
| input tokens | 51,900 |
| expected output tokens | 22,500 |
| maximum output allowance | 40,000 |
| expected cost | $0.493200 |
| conservative maximum | $0.755700 |
| provider ceiling | $1.500000 |
| combined new-work ceiling | $2.000000 |

The estimate used all 25 rendered prompts rather than a flat per-call rate. No prompt caching was requested or billed.

## 6. Claude execution

| Metric | Result |
|---|---:|
| unique cases completed | 25 |
| billed model calls | 25 |
| input tokens | 66,461 |
| cached input tokens | 0 |
| output tokens | 15,307 |
| exact calculated cost | $0.428988 |
| mean latency | 16.173 s |
| median latency | 15.845 s |
| total request latency | 404.325 s |
| inference retries | 0 |
| schema failures | 0 |
| ambiguous outcomes | 0 |

Two initial Anthropic requests were confirmed HTTP 400 pre-inference schema-compilation rejections, first for numeric range keywords and then for `maxItems`. They returned no model output and were not billed as critic calls. The first was initially classified too broadly by the old runner, then explicitly resolved as a confirmed non-billable validation failure with the evidence retained in the ledger. The lifecycle was fixed so confirmed 4xx responses are recorded separately from ambiguous transport outcomes. Exactly the same 25 unique cases, and no additional corpus cases, received completed Claude criticism.

## 7. Per-provider summaries

| Provider | Unrelated | Claimed consequence | Keep | Replace | Unsure | Overall mean / median | Cost | Mean latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Grok 4.5 | 7 | 4 | 4 | 20 | 1 | 32.48 / 25 | $0.318730 | 9.935 s |
| GPT-5.6 Sol | 2 | 5 | 4 | 20 | 1 | 44.16 / 44 | $0.724690 | 12.017 s |
| Claude Sonnet 4.6 | 1 | 2 | 1 | 20 | 4 | 31.64 / 30 | $0.428988 | 16.173 s |

Claude's primary taxonomy distribution was: ideological substitution 10, broader principle 6, contradictory 3, claimed consequence 2, secondary theme 2, mechanism 1, unrelated 1. Full per-score means and medians are machine-readable in `comparison_three_provider.json`.

## 8. Pairwise comparisons

| Pair | Exact primary | Primary/secondary overlap | Kappa | Decision agreement | Overall rho | Overall MAD | Consequence disagreements | Unrelated disagreements |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Grok / OpenAI | 36% | 84% | 0.223 | 100% | 0.867 | 12.08 | 3 | 7 |
| Grok / Claude | 56% | 84% | 0.476 | 80% | 0.808 | 9.08 | 2 | 6 |
| OpenAI / Claude | 36% | 80% | 0.202 | 80% | 0.711 | 15.08 | 3 | 1 |

All eight score fields, confusion matrices, Spearman correlations, mean absolute differences, and largest disagreements are retained for each pair. The original Grok/OpenAI comparison file was preserved rather than replaced.

## 9. Three-way agreement

- all-three exact primary relationship: 28%;
- all-three primary-or-secondary overlap: 68%;
- all-three keep/replace/unsure agreement: 80%;
- two-against-one primary classifications: 11 cases;
- all-three different primary classifications: 7 cases;
- only-one-provider unrelated: 7 cases;
- only-one-provider claimed consequence: 3 cases.

Operational disagreement occurred in five cases. In the other 20 all providers made the same keep/replace/unsure decision even where their rationales differed sharply.

## 10. Calibration diagnostics and evidence matrix

| Dimension | Grok | OpenAI | Claude | Interpretation |
|---|---|---|---|---|
| Consequence recognition | 4 primary | 5 primary | 2 primary | OpenAI most often makes consequence primary; Claude may route such cases to broader principle or substitution. |
| Unrelated restraint | 7 | 2 | 1 | Claude least likely to collapse related cases into unrelated, but lower is not automatically better. |
| Taxonomy consistency | strongest pairwise kappa with Claude | modest with both | strongest with Grok | No consensus taxonomy authority. |
| Score discrimination | broad, lower-centred | broad, higher-centred | broad, lower-centred | High rank correlations show broadly similar ordering; absolute calibration differs. |
| Operational usefulness | 20 replace | 20 replace | 20 replace | Same replace count; Claude used unsure more and keep less. |
| Schema reliability | 25/25 | 25/25 | 25/25 | Equal completed-output reliability. |
| Latency | fastest | middle | slowest | Claude approximately 63% slower than Grok on mean latency. |
| Cost | lowest | highest | middle | All remained economical and under ceiling. |
| Human evidence | 2 prior blinded A/B answers | 2 prior blinded A/B answers | none yet | Existing answers cannot be translated into A/B/C labels. |

Automatic checks show different strengths rather than a clear winner. Claude is less prone to `unrelated`, but it does not simply improve consequence recognition and is more cautious in keep/unsure decisions. This is inference from model outputs, not ground truth.

## 11. Free-trade case

| Provider | Primary / secondary | Relevance | Directness | Mechanism | Consequence | Principle | Overall | Decision |
|---|---|---:|---:|---:|---:|---:|---:|---|
| Grok | ideological substitution / claimed consequence | 38 | 18 | 8 | 52 | 35 | 28 | replace |
| OpenAI | claimed consequence / ideological substitution | 58 | 24 | 10 | 72 | 56 | 49 | replace |
| Claude | broader principle / secondary theme | 38 | 12 | 10 | 42 | 40 | 22 | replace |

All three recognised some prosperity/outcome relationship, none called the case unrelated, and all recommended replacement. Grok and OpenAI explicitly represented ideological substitution in the taxonomy. Claude described it explicitly in the explanation: the picture substitutes political leadership and communist contrast for trade liberalisation, while only vaguely echoing prosperity and consumer gains. All recommended ports, shipping, markets, goods, prices, or cross-border commerce as a stronger direction.

The principal difference is not the operational decision but how much consequence credit is granted and which relationship is made primary.

## 12. Three-way blinded review

The new sealed A/B/C mapping is independent of the old A/B mapping. The two existing A/B preferences remain untouched in `blinded_preferences.json`; they were not translated. New answers are stored separately in `blinded_preferences_three_way.json`.

Start the loopback-only page with:

```bash
python3 tools/semantic_alignment_bakeoff_review.py \
  --project-dir /disks/disk1/etc/mrsMThatcher \
  --source-run /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/runs/v2_20260711T111526Z \
  --bakeoff-dir /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/provider_bakeoff_25_20260712 \
  --host 127.0.0.1 --port 8768
```

The page displays Critic A/B/C, supports pairwise ties, all-equal, none and unsure, and separately asks which keep/replace decision is preferred. A real-data loopback smoke test rendered all three blinded results and the PNG without exposing model names; no three-way preference was saved.

## 13. Provisional recommendation

**Insufficient human evidence.** Claude is a credible third critic with complete schema reliability and moderate cost. It offers a materially different editorial decomposition, but automatic checks cannot establish that its lower unrelated rate or more cautious decisions are more human-calibrated. Complete blinded A/B/C review before expanding beyond these 25 cases.

## 14. Implementation and tests

Changed research-only files:

- `semantic_alignment/bakeoff.py`;
- `analyse_semantic_alignment_bakeoff.py`;
- `tools/semantic_alignment_bakeoff_review.py`;
- `tests/test_semantic_alignment_bakeoff.py`;
- new Anthropic results, ledger, pricing, parity manifest, comparison and reports under the existing bake-off directory.

Validation:

- Python compilation: passed;
- `git diff --check`: passed;
- semantic pipeline, calibration, bake-off and production-log-isolation tests: **85 passed in 2.00s**;
- APIs mocked in tests; no test made a paid call.

Tests cover three-provider prompt parity, Anthropic serialisation/parsing/cost, strict local schema validation, provider failure isolation, N-provider pairwise/three-way metrics, stable private A/B/C mapping, blinded preference storage, and explicit-call guards.

## 15. Limitations

- This is a deliberately difficult 25-case sample, not a corpus prevalence estimate.
- Only two historical A/B preferences exist and none yet includes Claude.
- Automatic unsupported-claim checks cannot replace human inspection of fingerprint evidence.
- Claude's provider grammar cannot encode all local numeric/list/string constraints; strict local validation remains authoritative.
- The two confirmed 400 schema-compilation requests are retained for operational transparency but were not model critic evaluations.

## 16. Safety and Git state

Cached fingerprints were reused; none was regenerated. Grok and OpenAI were not rerun and their artifacts were not modified. Exactly 25 unique Claude cases completed. No model tools or search were enabled. No production behaviour changed. The bot was not stopped, restarted, or signalled. No production state, config, history, receipt, log, or metadata was edited by the research process. The canonical simulator was not rerun. Nothing was staged, committed, pushed, or deployed.

`git diff --stat` continues to show only the pre-existing generated-image curation metadata changes and four quarantined PNG deletions because the research files are untracked. `git status --short` retains those unrelated changes and the existing untracked research/runtime artifacts.
