# 25-case semantic critic provider bake-off

## 1. Executive result

The blinded bake-off completed exactly 25 frozen cases with each provider. Both runs completed without schema failures, retries, ambiguous requests, or cost-limit breaches. The evidence does not yet justify naming a preferred provider: the critics agreed on the operational keep/replace decision in all 25 cases, but agreed on the exact primary relationship in only 36%, and no blinded human preferences have yet been recorded.

Provisional outcome: **Insufficient human evidence**.

## 2. Models and official prices

| Provider model | Input / 1M | Cached input / 1M | Output / 1M | Setting |
|---|---:|---:|---:|---|
| `grok-4.5` | $2.00 | $0.50 | $6.00 | low reasoning, 1,600 output-token cap |
| `gpt-5.6-sol` | $5.00 | $0.50 | $30.00 | low reasoning, 1,600 output-token cap |

The authenticated OpenAI model list exposed `gpt-5.6-sol` as a newer generally available Responses API model with structured outputs, so it was selected under the task rule. Its official model page was recorded as `https://developers.openai.com/api/docs/models/gpt-5.6-sol`. Authenticated xAI metadata confirmed the Grok token-price ticks. Model-list responses and pricing verification are retained under `pricing/`; credentials were not persisted.

No tools, web search, X search, code execution, or agent capabilities were supplied to either model.

## 3. Case selection

The frozen `cases.json` contains 25 unique pairs selected before either new provider run:

- the required free-trade case;
- eight prior unrelated classifications;
- four additional likely consequence-only cases;
- four ideological substitutions;
- four strong/high-alignment cases;
- two ambiguous-image cases;
- two contradictory or poor cases.

Selection was deterministic from the existing 150 validation cases. Existing human labels could affect coverage selection but were not included in either critic request. OpenAI results played no part in selection.

## 4. Shared prompt and schema

Both providers received the same rendered neutral critic instructions and the same allow-listed v2 quote and image fingerprints. Provider wrappers differed only in API envelope and strict-JSON configuration.

The provider-neutral schema records primary and optional secondary relationships; eight separate 0-100 scores; keep/replace/unsure; mechanism, consequence and principle decompositions; matched and missing elements; extraneous image arguments; explanation; and stronger visual direction.

Allowed relationships were:

`direct_illustration`, `illustrates_mechanism`, `illustrates_claimed_consequence`, `illustrates_broader_principle`, `related_ideological_substitution`, `secondary_theme_match`, `ambiguous`, `unrelated`, and `contradictory`.

## 5. Isolation and parity

Requests contained only the cached quote fingerprint, cached image fingerprint, and static critic instructions. They did not contain the old Grok critic answer, the competing answer, human labels, production winner status, origin relationship, generation prompt, raw image, engagement data, branch identity, or historical selection score.

The human page uses a run-stable sealed mapping stored separately with mode `0600`. Its case views expose only Critic A and Critic B. Model/provider provenance is stripped from both critic results and displayed fingerprint summaries. The automatic internal JSON retains provider keys for reproducible statistics; the human-facing Markdown keeps comparative case judgements blinded.

## 6. Cost preflight

| Provider | Calls | Estimated input | Expected output | Expected cost | Conservative maximum | Hard ceiling |
|---|---:|---:|---:|---:|---:|---:|
| Grok | 25 | 51,900 | 22,500 | $0.2388 | $0.3438 | $0.75 |
| OpenAI | 25 | 51,900 | 22,500 | $0.9345 | $1.4595 | $1.75 |

Combined expected cost was $1.1733; the conservative maximum was $1.8033 against the $3.00 ceiling. Estimates used the exact rendered prompts rather than a flat call rate.

## 7. Actual execution

| Model | Calls | Input | Cached | Reasoning | Output | Cost | Mean latency | Median latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `grok-4.5` | 25 | 59,837 | 4,736 | 16,939 | 17,421 | $0.318730 | 9.935 s | 9.686 s |
| `gpt-5.6-sol` | 25 | 43,526 | 0 | 2,550 | 16,902 | $0.724690 | 12.017 s | 12.040 s |

Combined billed/reconstructed cost was **$1.043420**. Grok used authoritative `usage.cost_in_usd_ticks`; OpenAI cost was reconstructed from authoritative response token usage and verified official unit prices. There were zero retries, zero ambiguous requests, zero transport failures, and zero schema failures for both providers.

## 8. Relationship agreement

- Exact primary-relationship agreement: **36.0%**.
- Primary-or-secondary overlap: **84.0%**.
- Cohen's kappa: **0.2233**, indicating only modest exact-category agreement on this deliberately difficult sample.
- Blinded unrelated counts: Critic A 7; Critic B 2.
- Blinded claimed-consequence counts: Critic A 4; Critic B 5.
- Blinded ideological-substitution counts: Critic A 6; Critic B 10.

The low exact agreement and much higher primary-or-secondary overlap show that the two critics often recognised similar relationships but ordered the primary and secondary editorial interpretation differently.

## 9. Score comparison

| Score | Grok mean | OpenAI mean | Mean absolute difference | Spearman rho |
|---|---:|---:|---:|---:|
| relevance | 38.60 | 52.88 | 14.60 | 0.889 |
| directness | 21.88 | 30.64 | 9.00 | 0.907 |
| mechanism alignment | 20.44 | 29.92 | 10.52 | 0.897 |
| consequence alignment | 30.56 | 32.76 | 8.60 | 0.858 |
| principle alignment | 43.16 | 51.08 | 11.92 | 0.817 |
| specificity | 25.44 | 34.52 | 9.96 | 0.840 |
| editorial power | 40.12 | 64.56 | 25.24 | 0.773 |
| overall suitability | 32.48 | 44.16 | 12.08 | 0.867 |

The critics ranked cases similarly but used materially different score levels. Higher means are not treated as proof of better calibration.

## 10. Consequence recognition and unrelated classifications

Five targeted consequence-recognition disagreements were detected. One critic used `unrelated` seven times and the other twice, while claimed-consequence classifications were close at four and five. The less-severe critic more often represented broad ideological overlap as a relationship rather than collapsing it into `unrelated`; blinded human review is needed to establish whether that is better calibrated or merely more permissive.

## 11. Keep/replace comparison

Operational decisions agreed in all 25 cases. Each critic returned:

- replace: 20;
- keep: 4;
- unsure: 1.

This bake-off therefore finds no provider difference in the final binary operational recommendation, despite substantial taxonomy and score differences.

## 12. Explanation-quality checks

Both providers produced 25/25 schema-complete, unique explanations and no detected classification/score contradictions. Mean explanation length was 491 characters for Grok and 423 for OpenAI. Grok omitted quote consequences in two records and quote mechanism in one; OpenAI omitted quote consequences in one and quote mechanism in none. These are deterministic completeness checks, not a final qualitative adjudication.

## 13. Free-trade case study

Critic A classified the pairing as `related_ideological_substitution` with secondary `illustrates_claimed_consequence`; relevance/directness were 38/18 and mechanism/consequence/principle scores were 8/52/35. It recommended replacement.

Critic B classified it as `illustrates_claimed_consequence` with secondary `related_ideological_substitution`; relevance/directness were 58/24 and mechanism/consequence/principle scores were 10/72/56. It also recommended replacement.

Both critics recognised prosperity as a claimed consequence, both recognised the substituted ideological/leadership framing, neither called it unrelated, and both recommended a more direct image showing ports, cargo, imported goods, consumer choice, prices, or cross-border commerce. The material disagreement is which valid relationship should be primary and how much relevance credit the consequence illustration deserves.

## 14. Blinded human review

No human A/B preference has yet been stored. Start the loopback-only page with:

```bash
python3 tools/semantic_alignment_bakeoff_review.py \
  --project-dir /disks/disk1/etc/mrsMThatcher \
  --source-run /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/runs/v2_20260711T111526Z \
  --bakeoff-dir /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/provider_bakeoff_25_20260712 \
  --host 127.0.0.1 \
  --port 8768
```

The page asks only which critique is closer and which image decision is preferred. Preferences are stored separately without provider names.

## 15. Provisional recommendation

**Insufficient human evidence.** One critic is less inclined to label difficult cases unrelated and gives higher relevance/editorial-power scores, but both critics made identical keep/replace decisions. Human blinded preferences are required to distinguish better consequence calibration from general score inflation. No remaining 125 cases, 500-case batch, or shortlist run should be purchased before this review.

## 16. Tests and smoke validation

- `python3 -m py_compile analyse_semantic_alignment_bakeoff.py semantic_alignment/*.py tools/semantic_alignment_bakeoff_review.py`: passed.
- Focused semantic pipeline, calibration, bake-off, and production-log-isolation tests: **84 passed in 2.20s**.
- `git diff --check`: passed.
- Loopback real-data smoke: gallery progress loaded, free-trade case loaded, PNG rendered, Critic A/B rendered, provider/model strings absent, and server stopped without saving a preference.

Tests used mocked provider responses. No paid calls occurred during tests.

## 17. Research files

Created or updated under `semantic_alignment_research/provider_bakeoff_25_20260712/`:

- frozen cases and preflight;
- pricing metadata and verification;
- provider ledgers and result files;
- internal machine-readable comparison and execution summary;
- blinded Markdown comparison;
- sealed provider mapping;
- this execution report;
- pre-execution safety hashes and log stat.

Research code and tests are untracked working-tree files: `semantic_alignment/bakeoff.py`, `analyse_semantic_alignment_bakeoff.py`, `tools/semantic_alignment_bakeoff_review.py`, and `tests/test_semantic_alignment_bakeoff.py`.

## 18. Safety verification

The production config, image histories, lines history, generated analysis, and identity audit hashes remained unchanged. `bot_state.json` and the production log changed only through the already-running bot's normal minute scheduler ticks; the appended log interval contains no pytest, bake-off, semantic-alignment, temporary-test, dummy, or loopback markers. The research process did not open production state, history, receipts, metadata, or logs for writing.

Cached fingerprints were reused and none were regenerated. Exactly 25 critic cases per provider were run. No model tools or search were used. No production behaviour changed. The bot was not stopped, restarted, or signalled. No production state, config, history, receipt, log, or metadata was edited by the research process. The canonical simulator was not rerun. Nothing was staged, committed, pushed, or deployed.

## 19. Limitations

- The 25 cases were deliberately enriched for difficult and disputed examples, so category frequencies are not corpus prevalence estimates.
- There are no blinded human preferences yet.
- Cost accounting differs by provider: Grok supplied exact billed ticks, while OpenAI supplied authoritative tokens priced from official rates.
- Automatic explanation checks measure structure and consistency, not editorial truth.
- Provider identities remain sealed in the human workflow until review is complete.

## 20. Git state

`git diff --stat` continues to show only pre-existing generated-image curation changes: two metadata files and four quarantined PNG deletions. Research implementation and outputs are untracked and therefore are not represented by that stat.

`git status --short` contains those preserved curation changes plus pre-existing and research untracked files. Nothing was staged.
