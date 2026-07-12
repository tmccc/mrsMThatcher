# Semantic alignment critic calibration report

## Executive result

The completed 632 quote fingerprints, 79 active-image fingerprints, and 150
critic results were reused without regeneration. A new picture-editor
calibration layer, a stratified 100-case human review set, and a loopback review
application are ready.

No calibration critic call was made. Genuine human labels do not yet exist, and
the runner correctly refuses paid execution until all 25 cases in the requested
split have valid labels. Consequently, 25-case calibration agreement and
25-case hold-out agreement are **pending**, not fabricated.

## 1. Critic failure analysis

Existing relationship counts:

| old relationship | count |
|---|---:|
| unrelated | 76 |
| related_but_not_equivalent | 43 |
| generic_ideological_substitution | 17 |
| secondary_theme_only | 8 |
| contradiction | 3 |
| partial_support | 2 |
| strong_support | 1 |

The old critic produced 128 scores below 40, 21 from 40-59, and one from
60-74. Five cases combined editorial power of at least 60 with alignment below
40.

Recurring failure modes are:

1. **Consequence blindness.** It treats absence of a causal mechanism as absence
   of relevance even when the claimed outcome is visibly central.
2. **Single-label compression.** A picture can illustrate prosperity while also
   substituting a broad ideological argument, but v2 forced one verdict.
3. **Specific-policy literalism.** Policy quotations are judged against literal
   policy objects rather than the full mechanism-to-outcome editorial chain.
4. **Extraneous-argument over-penalty.** A strong extra claim can erase a real
   secondary match instead of being reported alongside it.
5. **Calibration-set imbalance.** The 150 cases contain almost no old high-scoring
   matches and only one human expected label. Existing agreement is not a usable
   calibration statistic.

## 2. Revised taxonomy

The calibration critic supports separate primary and secondary relationships:

- `direct_illustration`
- `illustrates_mechanism`
- `illustrates_claimed_consequence`
- `illustrates_broader_principle`
- `related_ideological_substitution`
- `secondary_theme_match`
- `ambiguous`
- `unrelated`
- `contradictory`

This permits, for example, primary `illustrates_claimed_consequence` with
secondary `related_ideological_substitution`.

## 3. Mechanism/consequence/principle model

Existing v2 fingerprints are deterministically decomposed. The derivation is
explicitly labelled `deterministic_keyword_derivation_from_v2_fingerprint`; it
is not presented as new AI analysis.

Quote decomposition records:

- core mechanism concepts;
- claimed consequences;
- broader principles;
- source proposition-level claims.

Image decomposition records:

- depicted subject/evidence;
- implied mechanism;
- depicted consequence;
- broader ideological framing;
- otherwise unclassified implied claims.

The fingerprints remain authoritative inputs. The local decomposition is an
explainable prompt aid and can be corrected later without regenerating them.

## 4. Scoring model

The new critic returns eight independent 0-100 scores:

- **Relevance:** whether the picture connects materially to what the quotation
  argues, including mechanism, outcome or principle.
- **Directness:** how immediately a reader can understand the connection.
- **Mechanism alignment:** whether the picture depicts the causal/policy process.
- **Consequence alignment:** whether it depicts an asserted outcome.
- **Principle alignment:** whether it depicts the broader value or doctrine.
- **Specificity:** whether the visual connection is specific rather than generic.
- **Editorial power:** visual force independently of correctness.
- **Overall suitability:** an editorial judgement, deliberately not a numerical
  average.

It also reports mechanism, consequence and principle matches, extraneous
arguments, missing primary claims, explanation and preferred visual direction.

## 5. Human calibration dataset

`human_review_cases.json` contains 100 deterministic, unique cases selected by
round-robin stratification over the old relationship classes. Rare classes are
included before common classes. The free-trade case is first.

Fixed splits:

- calibration: 25
- hold-out: 25
- reserve: 50

The calibration split contains all seven old relationship classes present in
the completed data. The hold-out is fixed before prompt editing. Scarce old
strong/partial/contradiction classes cannot appear in both splits without
duplicating cases; human labels may reveal additional positive cases.

All 100 `human_label` values are currently `null`. Required labels are primary
and optional secondary relationship, relevance/directness bands, posting
acceptability, replacement preference and notes.

## 6. Review workflow

The standard-library web application is loopback-only, shows one case at a
time, displays the actual active image, quotation, both fingerprints, local
decompositions and old critic explanation, and atomically saves each label.
It has a per-process CSRF token and serves only known active image basenames.

Launch command:

```bash
python3 tools/semantic_alignment_calibration_app.py \
  --project-dir /disks/disk1/etc/mrsMThatcher \
  --dataset /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/runs/v2_20260711T111526Z/calibration/human_review_cases.json \
  --host 127.0.0.1 --port 8766
```

## 7. Calibration prompt

Prompt version `picture-editor-critic-v3-calibration-1` receives only the
existing quote/image fingerprints and deterministic decompositions. It does not
receive human labels, old critic output, production status, origin relationship,
generation prompt, branch identity or engagement data.

It explicitly states that illustrating a claimed outcome can be relevant even
when the mechanism is absent, while preserving penalties for broad ideological
substitution and extraneous arguments.

## 8. Calibration and hold-out agreement

Pending human review:

- calibration labels complete: 0/25
- hold-out labels complete: 0/25
- calibration critic calls: 0
- hold-out critic calls: 0
- paid cost in this task: $0

`run-calibration` and `run-holdout` both validate every human label before
constructing an API client. The calibration command was exercised without
labels and refused execution with `no paid execution permitted`.

This is the required stopping point. Calling the model first would turn its own
judgements into the supposed human reference and invalidate agreement results.

## 9. Free-trade case study

**Quote mechanism:** freedom of trade and payments, freer trade, competition.

**Claimed consequences:** post-war/faster growth, lower prices, consumer
benefits.

**Broader principle:** open-market competition/economic freedom.

**Image depicted subject:** a female political leader at a podium between
industrial decay and clean modern prosperity.

**Image implied mechanism:** strong leadership bridges decline and prosperity.

**Image depicted consequence:** prosperous, clean, modern commerce and urban
life.

**Image ideological framing:** an implied historical/ideological transformation,
with red flags and deprivation contrasted against affluence.

**Mechanism match:** low; trade, payments and competition are absent.

**Consequence match:** meaningful; prosperity and modern commerce depict part
of the quotation's asserted outcome.

**Principle match:** moderate but generic; the visual suggests economic reform
or market prosperity without establishing open international trade.

**Expected editorial primary relationship:**
`illustrates_claimed_consequence`.

**Expected secondary relationship:**
`related_ideological_substitution`.

This is not `unrelated` because the quotation expressly claims economic growth
and consumer benefit, and the image visibly depicts prosperity. It remains
indirect because it substitutes political leadership and an ideological
before/after contrast for the trade mechanism. Better replacement imagery would
show ports, cargo, cross-border goods, competitive consumer choice and prices.
These are calibration hypotheses for owner review, not pre-filled human labels.

## 10. Cost estimate

The observed v2 critic mean was $0.00904056 per call. Fifty calls plus a 10%
allowance estimate to **$0.497231**, below the $1 target and $1.50 hard stop.
Each 25-case split is approximately $0.248616 with allowance.

No call will be permitted before its split has 25 valid human labels. Exact
cost-ledger, no-retry and ambiguous-cost blocking semantics are reused.

## 11. Recommendation

Do not run a 500-case evaluation yet. Complete at least the 25 calibration and
25 hold-out labels, run the guarded batches, and inspect relationship-level and
band-level agreement. One prompt revision may use only the calibration split;
the hold-out must remain unseen during editing.

The architecture is ready for that experiment, but critic readiness is not yet
established.

## 12. Files and tests

Created or changed offline research tooling:

- `calibrate_semantic_alignment.py`
- `semantic_alignment/calibration.py`
- `semantic_alignment/calibration_prompt.py`
- `tools/semantic_alignment_calibration_app.py`
- `tests/test_semantic_alignment_calibration.py`
- calibration files under the completed v2 run

Validation:

- `python3 -m py_compile ...`: passed
- semantic pipeline and calibration tests: **33 passed in 0.79s**
- `git diff --check`: passed

The full repository suite was not run because only isolated, untracked research
tools changed and no production module is imported or modified.

Research files are untracked, so `git diff --stat` continues to show only the
pre-existing generated-image curation changes. `git status --short` likewise
retains those unrelated modifications/deletions and the existing untracked
artefacts. Exact snapshots are saved beside this report.

## 13. Confirmations

- Existing fingerprints were reused.
- No quote or image fingerprint was regenerated.
- No additional corpus item was analysed.
- No xAI or other external API call was made in this calibration task.
- No production behaviour changed.
- The production bot was not stopped, restarted or signalled.
- No production state, configuration, history, receipt, log or metadata was
  edited.
- The canonical simulator was not rerun.
- No full-shortlist evaluation or replay occurred.
- Nothing was staged, committed, pushed or deployed.
