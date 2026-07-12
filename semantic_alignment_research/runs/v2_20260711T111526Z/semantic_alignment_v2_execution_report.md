# Semantic alignment v2 execution report

## Executive summary

Run `v2_20260711T111526Z` completed the approved scope only: 632 quote
fingerprints, 79 active generated-image fingerprints, and 150 validation-set
critic results. All 861 responses passed strict v2 validation. There were no
retries, failures, missing-cost responses, or ambiguous requests in the new
run. Exact new-run cost was **US$5.714168**.

The validation set is much more mismatch-heavy than a balanced calibration
set: 128/150 alignment scores were below 40 and 76 relationships were
`unrelated`. Only one case carries a human expected category. A 500-pair run is
therefore **not recommended yet**; first add independent human labels across a
balanced sample and review critic severity.

## Archived pilot

The v1 pilot is preserved read-only at:

`semantic_alignment_research/archive/pilot_20260711_blocked/`

Its manifest reports:

- status: `archived_blocked`
- resumable: `false`
- valid quote fingerprints: 136
- exact known cost: US$0.691074
- ambiguous item: `3dd04269c7014b5ab25d7fc9d7b2ea5195ed1e8a2b2c44278ed5cad73486a2d5`
- reason: interrupted inference with no authoritative returned cost

The archive contains the blocked ledger, fingerprints, 136 response caches,
pricing evidence, preflight, and pilot report. It was not resumed or migrated.
The ambiguous request's actual billing remains unknown and is not estimated.

## V2 schemas and prompts

All three schemas and prompts were bumped explicitly:

| analysis | schema | prompt |
|---|---:|---|
| quote fingerprint | 2 | `quote-semantic-v2-claims` |
| image implied message | 2 | `image-implied-message-v2-claims` |
| claim-aware critic | 2 | `semantic-editorial-critic-v2-claims` |

Quote records now require a `core_claim` and proposition-level `claims` with
primary/secondary importance and confidence. Image records require a
`core_implied_claim` and `implied_claims`; every image claim has salience,
confidence, and non-empty concrete visual support. V1 records are rejected by
the v2 loaders rather than silently enriched.

The quote prompt now asks for concrete human scenes, places, objects,
institutions, commerce, industry, and editorial symbolism, with charts only
where quantitative evidence is genuinely appropriate. The 25-record review
found this corrected the pilot's tendency towards abstract chart directions.

## Critic taxonomy and isolation

The v2 relationship taxonomy is:

- `direct_equivalence`
- `strong_support`
- `partial_support`
- `related_but_not_equivalent`
- `generic_ideological_substitution`
- `secondary_theme_only`
- `contradiction`
- `unrelated`
- `ambiguous`

The critic must report matched claims, unillustrated primary quote claims, and
image claims not required by the quote. It explicitly penalises broad ideology
substituted for specific policy and strong extraneous image arguments.

Prompt isolation was inspected and tested. Quote calls contained only quote
text and static instructions. Image calls contained only pixels and static
no-caption instructions. Critic calls contained allow-listed v2 fingerprints
only. Human labels, origin relationships, production status, scores, branch
identity, prompts, engagement, search tools, and model tools were absent.

## Pricing and preflight

Model: `grok-4.5`; `reasoning_effort=low`; strict JSON Schema output;
`max_tokens=1000`; no tools.

Authenticated model metadata and xAI cost documentation established:

- text/image input: US$2.00 per million tokens
- completion output: US$6.00 per million tokens
- cached input: US$0.50 per million tokens
- 1 US dollar = 10,000,000,000 cost ticks

The preflight included a conservative 2% retry allowance even though the tool
does not retry automatically:

| stage | calls | input tokens | image allowance | output tokens | estimate | ceiling |
|---|---:|---:|---:|---:|---:|---:|
| quotes | 632 + 13 | 580,500 | 0 | 419,250 | $3.676500 | $6 |
| images | 79 + 2 | 32,400 | 332,100 | 60,750 | $1.093500 | $3 |
| critic | 150 + 3 | 229,500 | 0 | 107,100 | $1.101600 | $3 |
| total | 861 + 18 | 842,400 | 332,100 | 587,100 | **$5.871600** | **$12** |

## Actual usage and cost

| stage | calls | input | cached | reasoning | completion | retries | exact cost |
|---|---:|---:|---:|---:|---:|---:|---:|
| quote | 632 | 530,512 | 452,992 | 239,711 | 337,129 | 0 | $3.842576 |
| image | 79 | 146,940 | 59,648 | 0 | 51,850 | 0 | $0.515508 |
| critic | 150 | 340,725 | 105,216 | 58,372 | 80,371 | 0 | $1.356084 |
| **new run** | **861** | **1,018,177** | **617,856** | **298,083** | **469,350** | **0** | **$5.714168** |

Archived pilot known spend was $0.691074. Combined exact known project spend is
**$6.405242**, plus one unquantified interrupted pilot request. Every new-run
call has authoritative `usage.cost_in_usd_ticks` persisted.

## Quality review

All 632 quote core claims were unique. The 25-case stratified review covered
trade, capitalism/socialism, taxation, defence, sovereignty, liberty, unions,
consumers, inflation, and political philosophy. Claims were specific,
non-redundant, supported by quote text, and useful for visual comparison.

All 79 active image records had unique core implied claims and hashes matching
the active files. The 25-case review found concrete pixel evidence and sensible
use of `speculative` salience. No routine origin-quote leakage was observed.
No original or quarantined image was analysed.

## Validation results

Score distribution:

- 0-39 mismatch: 128
- 40-59 weak: 21
- 60-74 indirect: 1

Relationship distribution:

| relationship | count |
|---|---:|
| unrelated | 76 |
| related_but_not_equivalent | 43 |
| generic_ideological_substitution | 17 |
| secondary_theme_only | 8 |
| contradiction | 3 |
| partial_support | 2 |
| strong_support | 1 |

There were no `direct_equivalence` or `ambiguous` verdicts. The only human-
labelled case was the free-trade case, so the nominal human agreement is 0/1
and cannot be treated as a meaningful rate. There were no measurable false
positive/negative rates because the validation file lacks a broad labelled
reference set. No critic result had confidence below 0.7.

Theme-only versus claim-aware critic comparison is unavailable: the archived
pilot stopped before critic execution. No comparison is fabricated.

## Free-trade case

**Quote core claim:** Trade has been a great engine of post-war growth from
which all, especially every consumer, have benefited through freer trade and
payments that produced lower prices, more competition and faster growth.

**Primary quote claims:** trade drives post-war growth; all gain from freer
trade and payments; freer trade produces lower prices, competition and faster
growth; every consumer benefits.

**Image core implied claim:** Strong leadership from the woman at the podium is
presented as bridging and enabling the shift from industrial decay and hardship
to clean, affluent urban life.

**Dominant image claim:** the woman at the microphones is the central agent
connecting a grim past to a thriving future.

**Matched claims:** none.

**Unillustrated quote claims:** all four primary trade/consumer claims.

**Extraneous image claims:** female political leadership as agent of change,
industrial decline, modern urban prosperity, podium speechmaking, and urban
renewal.

**Relationship:** `unrelated`; alignment 12; directness 8;
`mismatch_type=unrelated`; confidence 0.93.

**Critic explanation:** No trade, shipping, markets, prices, competition or
consumer-purchase evidence appears; leadership and urban renewal dominate.

**Preferred direction:** ports, cargo ships and containers, cross-border goods,
retail choice and prices, export production, and international payments.

**Human judgement:** `related_but_indirect`. The model disagreed by treating
general prosperity as insufficient to establish even an indirect relationship.
This severity should be calibrated with more human-labelled borderline cases.

## Cost and run safety

The ledger records status `complete`, 861 exact-cost calls, no ambiguous
requests, and no retries. A missing cost or transport ambiguity blocks the
ledger permanently and records item, model, timestamp, transport error,
response-received state and request ID where available. `run-status` reports
active, blocked, complete, and archived runs. A fresh run requires an explicit
new-run operation and separate directory.

## Tests

- `python3 -m py_compile analyse_semantic_alignment_xai.py semantic_alignment/*.py`: passed
- focused semantic pipeline tests: **26 passed in 0.22s**
- `git diff --check`: passed

No full repository suite was run because changes are confined to untracked,
offline research tooling and do not import or modify production modules. Tests
used mocked transports and made no xAI call.

## Production safety

Pre/post hashes were recorded in this run directory. Local config,
`images_used.json`, `lines_used.json`, generated analysis, and identity audit
were unchanged. `bot_state.json` changed during the long run through normal
live-bot scheduler activity; the research process never opened it for writing.
No research process remained after completion.

The bot was not stopped, restarted, or signalled. No X post or reply was
created. No search tool was used. The research process did not edit production
state, config, history, receipts, logs, or metadata. The canonical simulator was
not rerun. No full-shortlist critic run or branch replay occurred.

## Files and Git state

Research source changed in untracked files:

- `analyse_semantic_alignment_xai.py`
- `semantic_alignment/__init__.py`
- `semantic_alignment/pipeline.py`
- `semantic_alignment/prompts.py`
- `semantic_alignment/reporting.py`
- `semantic_alignment/schemas.py`
- `tests/test_semantic_alignment_pipeline.py`

New outputs are confined to `semantic_alignment_research/archive/` and
`semantic_alignment_research/runs/v2_20260711T111526Z/`.

Because research files are untracked, `git diff --stat` lists only pre-existing
generated-image curation changes: 6 tracked paths, 13 insertions and 777
deletions. Those changes were preserved and not modified by this work. The
working tree remains unstaged. Nothing was committed, pushed, or deployed.

## Recommendation

Do not run 500 pairs yet. First label a balanced validation set covering direct,
strong, partial, indirect, ideological-substitution, contradiction and unrelated
cases. Review whether `unrelated` is too severe when an image depicts a claimed
outcome without the quote's causal mechanism. The v2 claim fields themselves
are specific and should be retained.
