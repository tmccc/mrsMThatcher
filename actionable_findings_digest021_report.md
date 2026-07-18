# Digest021 Actionable Findings

## Outcome

All four requested areas were completed offline. No paid AI or provider call was made, the bot was not restarted or signalled, and the live v2 semantic-veto manifest was not replaced.

## Quotation-derived metadata

Two active artefacts were stale after the 13-quotation cleanup:

- `quote_analysis.json` still declared source SHA-256 `4a3eec7b...`, contained 632 analysis records, and retained the 13 removed quote IDs.
- `quote_analysis_overrides.json` retained pre-cleanup physical line references for its two active overrides.

The active analysis index was migrated to the cleaned source SHA-256 `10310a9d62c03a87f2c1e55fa10286d1413216b8c0cb34cb0dbe4b3c12f19bee`. It now has 619 distinct analysis records and 620 physical line mappings. All 619 retained `analysis` payloads were preserved; no quotation was re-analysed. Override line references moved from 175 to 169 and from 190 to 184.

Production-parity loading now reports:

- source records: 619 distinct / 620 physical;
- completed and eligible: 613;
- unresolved and ineligible: 6;
- source-SHA warning: absent;
- override-staleness warnings: absent.

Remaining references to the old SHA occur only in immutable research manifests, pre-cleanup snapshots, counterfactual datasets and cleanup audit records. They were deliberately preserved as historical provenance.

## Principle replies

`principle_reply` is present in the deployed code path and remains constrained to non-factual, ungrounded, non-humorous replies with empty evidence metadata. Unsupported allegations can receive a concise answer to the broader principle only when the answer neither repeats nor endorses the allegation.

Concrete factual questions still take the direct grounded factual path; an abstract principle is rejected as a factual answer. Abuse and bait remain eligible for `no_reply`, and the existing incoherence behaviour was not changed. Digest aggregation distinguishes `principle_reply` and the three requested no-reply categories.

No cap, spacing, clarification, budget, receipt or persistence code was changed by this work.

## V3 shadow candidate

Prepared candidate:

`semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/material_veto_v3_shadow_manifest.json`

- SHA-256: `8b202352ddf89af5860446f4dd20577832981fcf778316b2b01121f7c5550706`
- policy: `affirmative-material-contradiction-rules-v3-attribution-cleanup-candidate`
- quotations: 613;
- authorised images: 91;
- known pairs: 22,157 (22,028 allow; 129 veto);
- unknown pairs excluded from enforcing lookup: 167;
- quotations with an allowed candidate: 612; without: 1;
- current production-winner coverage: 100%;
- seasonal-boundary winner coverage: 100%;
- stateful weighted winner coverage: 99.7666%;
- stateful unknown-winner rate: 0.2334%;
- critical relationship regressions: 0 failures;
- production-selection invariant failures: 0.

All 13 removed and all six unresolved IDs are absent. Unknown remains non-enforcing. Runtime configuration still accepts only `disabled` and `shadow`; active enforcement cannot be selected. Strict runtime preflight passed with a 274 ms load and approximately 32.8 MiB lookup footprint.

The preflight found 98 reused veto rows whose structured contradiction codes had not been copied into the compact candidate. Those existing codes were restored from the byte-identical prior judgements without changing any decision or mislabelling them as deterministic rules.

The live v2 manifest remains unchanged at SHA-256 `3e08320ffa8955e4bbad46444077ee0726e49dee90c29874a81cc872eb3751b0`.

## t56.jpg investigation

The quotation was Thatcher's 2002 *Statecraft* criticism of climate policy as a pretext for supra-national socialism. Its analysis calls for a serious, forceful podium or parliamentary image.

The live top five were:

| Rank | Image | Score |
|---:|---|---:|
| 1 | `t56.jpg` | 22.96 |
| 2 | `t28.jpg` | 21.70 |
| 3 | `t63.jpg` | 15.50 |
| 4 | `t29.jpg` | 13.60 |
| 5 | `t49.jpg` | 13.60 |

For `t56.jpg`, the components were historical 0, mismatches 0, quality 1.96, scene/activity/symbols 14, tone/mood 5, topics 0 and visual energy 2. The offline current-snapshot profile reproduced the same winner and score. The editorial shadow also ranked `t56.jpg` first at 23.8969; `t28.jpg` remained second at 23.1107. V3 classifies `t56.jpg` as `allow` under the neutral-portrait policy.

No clearly stronger image existed in the eligible set. The modest absolute score reflects a historical-photo coverage and metadata-specificity gap: every eligible candidate received zero historical and topic points. No general selector defect was confirmed, so scoring was not changed.

Full ranking and metadata are in `t56_selection_investigation.json` and `t56_selection_investigation.md` beside the cleanup artefacts.

## Validation

- Focused reply/metadata/veto/digest tests: 148 passed.
- Final attribution/veto regression pass after provenance correction: 47 passed.
- Full suite on final code: 1,759 passed, 1 skipped, 3 dependency deprecation warnings.
- Required `py_compile`: passed.
- `git diff --check`: passed.
- New AI/provider calls: 0.

## Files changed

- `quote_analysis.json`
- `quote_analysis_overrides.json`
- `quote_attribution_cleanup.py`
- `semantic_alignment/quote_image_semantic_veto.py`
- `tests/test_quote_attribution_cleanup.py`
- `tests/test_quote_image_semantic_veto_shadow.py`
- attribution-cleanup migration, v3 candidate/audit and t56 investigation artefacts
- this report

No commit, push, deployment, restart or process signal was performed.
