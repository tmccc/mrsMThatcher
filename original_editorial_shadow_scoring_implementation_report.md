# Original Editorial Shadow Scoring Implementation Report

Date: 2026-07-10
Repository: `/disks/disk1/etc/mrsMThatcher`

## 1. Executive summary

Implemented a production-candidate shadow scorer for original regular-post images only. It is disabled by default and observational only.

When enabled, the bot validates `original_image_editorial_analysis_experiment_v1.json`, observes the actual phase-specific regular-image candidate set, scores only the eligible original-image subset with a controlled editorial layer, and logs one `ORIGINAL_EDITORIAL_SHADOW_RESULT` event after the real production winner is already selected.

The production winner, quote selection, image history, generated-image spacing, recovery behaviour, receipts, scheduling and API behaviour are unchanged.

## 2. Files inspected

- `mrsMThatcher2.py`
- `mrs_log_digest.py`
- `tests/test_unit_helpers.py`
- `tests/test_integration_harness.py`
- `tools/backtest_original_editorial_matching.py`
- `tests/test_backtest_original_editorial_matching.py`
- `original_image_editorial_analysis_experiment_v1.json`
- `original_editorial_matching_backtest.json`
- `original_editorial_matching_backtest_report.md`

## 3. Pre-existing git state

Before the task, the tracked tree was clean. There were many pre-existing untracked experimental/report/runtime artifacts, including the offline backtest tool/output and generated-image review artifacts.

Important pre-existing untracked files included:

- `tools/backtest_original_editorial_matching.py`
- `tests/test_backtest_original_editorial_matching.py`
- `original_editorial_matching_backtest.json`
- `original_editorial_matching_backtest_report.md`
- `original_image_editorial_analysis_experiment_v1.json`
- `review/`
- generated-image corpus/review artifacts

## 4. Exact production selection path used

The shadow scorer is attached inside `choose_matched_unused_image(...)`, after:

- current image paths are resolved;
- image-used history is normalised;
- image metadata is loaded and verified;
- stale/seasonal images are excluded;
- generated-image spacing filtering is applied;
- current-cycle, forced-cycle-reset, or final last-image-fallback availability is applied;
- strong visual mismatch exclusions are applied;
- production baseline scores are calculated;
- generated origin-quote boost is applied where relevant;
- production winner is selected from the normal production scoring/tie path.

The shadow result is logged immediately after `REGULAR_IMAGE_SELECTED`. It does not feed back into selection.

## 5. How the real eligible original candidate set is captured

The existing `scored` candidate list is the authoritative candidate set for the active selection phase. It already contains only images that survived the actual production eligibility path for that phase.

The shadow scorer filters that list to:

- `image_source == "original"`;
- items with validated editorial metadata.

Generated candidates are ignored by the editorial competition. If production selects a generated image, the shadow original winner is still calculated from the eligible original subset, but the generated production winner is not assigned an editorial adjustment or shadow rank.

## 6. Experimental metadata validation

When `ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING` is false, the experimental file is not required.

When enabled, the loader validates:

- `schema_version == 1`;
- `analysis_kind == "original_editorial_experiment"`;
- `items` is an object;
- no item basename is a generated `tg_<64hex>.*` basename;
- every item points to a current image basename;
- current image SHA-256 matches the metadata SHA-256;
- every current original image has editorial metadata;
- `dimension_scores` is an object;
- all dimension values are numeric and in `0..10`;
- `overall_editorial_utility` is numeric and in `0..10`.

## 7. Final controlled affinity vocabulary

The production-candidate scorer deliberately does not import the full free-form 839-concept vocabulary.

Allow-listed affinity concepts:

- `ceremony`
- `defiance`
- `duty`
- `economic`
- `enterprise`
- `family`
- `freedom`
- `humour`
- `national_identity`
- `patriotism`
- `public_service`
- `responsibility`
- `socialism`
- `victory`
- `warning`

Broad near-universal concepts such as `leadership`, `conviction`, `authority`, `resolve`, `statesmanship`, and `principle` do not receive direct free-form affinity boosts. They are handled through the fixed dimension layer where appropriate.

## 8. Quote dimension-profile mapping

The scorer uses the fixed dimensions:

- `leadership`
- `conviction`
- `authority`
- `defiance`
- `warning`
- `optimism`
- `patriotism`
- `statesmanship`
- `economic_seriousness`
- `human_warmth`
- `ceremony_formality`
- `historical_iconicity`

The quote profile is deterministic and based on existing quote-analysis fields only:

- primary/secondary topics;
- tone;
- visual energy;
- archive image preferences;
- historical context.

The mapping is conservative: a broad political quote does not automatically activate leadership, authority, or conviction. Activation requires grounded evidence such as freedom/duty, warning tone, defiance/confrontation, patriotism/national identity, economic/enterprise topics, family/warm tone, ceremony, historical specificity, or explicit scene preferences.

## 9. Exact editorial score formula

For each eligible original candidate:

```text
dimension_score =
    sum(active_quote_dimension * (image_dimension_0_to_1 - 0.45) * 5.0)
    plus small tension penalties for optimism-vs-warning and ceremony-vs-action

affinity_score =
    min(4.0, number_of_controlled_quote_image_affinity_matches)

utility_adjustment =
    clamp((overall_editorial_utility - 5.5) / 4.5, -0.5, 1.0)

penalty =
    1.25 * number_of_controlled avoid_quote_type matches

raw_layer =
    dimension_score + affinity_score + utility_adjustment - penalty

editorial_adjustment =
    clamp(raw_layer * ORIGINAL_EDITORIAL_SHADOW_WEIGHT,
          -ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT,
          +ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT)

shadow_combined_score =
    production_baseline_score + editorial_adjustment
```

The production baseline score is not modified.

## 10. Exact weight and cap chosen

- `ORIGINAL_EDITORIAL_SHADOW_WEIGHT = 0.32`
- `ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT = 4.0`

## 11. Why those values were chosen

`0.32` is the corrected medium-weight backtest setting. A cap of `4.0` keeps the editorial layer as an adjunct: it can break close semantic ties or surface an editorially better original image, but it cannot let a very poor baseline candidate leap over a strong production match solely because of editorial metadata.

## 12. How generated production winners are handled

Generated images never enter the original editorial shadow competition.

If production selects a generated image:

- `production_source` is logged as `generated`;
- `production_winner` is logged normally;
- `production_editorial_adjustment` is `null`;
- `production_shadow_score` is `null`;
- `production_shadow_rank` is `null`;
- `shadow_original_winner` still records the best eligible original-image shadow candidate if one existed.

The shadow result does not affect generated-image spacing.

## 13. Logging events added

One machine-parseable event is added:

```text
ORIGINAL_EDITORIAL_SHADOW_RESULT {"affinity_matches":["freedom"],"cap_hit":false,"dimension_matches":["conviction"],"eligible_original_count":2,"line_no":12,"max_abs_adjustment":4.0,"penalties":[],"production_baseline_score":10.0,"production_editorial_adjustment":0.0,"production_shadow_rank":2,"production_shadow_score":10.0,"production_source":"original","production_winner":"t01.jpg","quote_hash":"...","selection_phase":"normal","shadow_original_winner":"t02.jpg","shadow_winner_baseline_score":9.0,"shadow_winner_editorial_adjustment":2.5,"shadow_winner_score":11.5,"weight":0.32,"winner_changed":true}
```

Selection phases are:

- `normal`
- `forced_cycle_reset`
- `last_image_fallback`

## 14. Digest support added

`mrs_log_digest.py` now parses `ORIGINAL_EDITORIAL_SHADOW_RESULT` JSON events and renders:

- shadow observations;
- production original/generated winners;
- winner changes and percentage;
- average production-winner shadow rank;
- rank-1 and rank-2/3 counts;
- most frequent shadow winners;
- most frequent affinity concepts;
- most frequent active dimensions;
- average/max editorial adjustment;
- cap-hit count;
- compact changed-winner table.

The digest explicitly labels the section as shadow-only and says it does not imply that the shadow image was posted.

## 15. Test logging isolation status

The new dedicated tests import the bot via `tests.test_unit_helpers.bot`, which sets:

- `MRS_TEST_MODE=1`
- `MRS_BASE_DIR=/tmp/mrsMThatcher-unit-import`
- `MRS_LOG_FILE=/tmp/mrsMThatcher-unit-import/unit-test.log`
- fake local endpoint/credential values

This avoids production-log writes during test import. Existing integration subprocess tests also create temporary base directories.

## 16. Dedicated test results

Command:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_original_editorial_shadow_scoring.py
```

Result:

```text
41 passed in 0.41s
```

## 17. Full-suite test result

Command:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
```

Result:

```text
574 passed, 1 skipped, 1 warning in 186.50s (0:03:06)
```

## 18. py_compile result

Command:

```bash
python3 -m py_compile mrsMThatcher2.py mrs_log_digest.py tests/test_original_editorial_shadow_scoring.py
```

Result: passed.

## 19. git diff --check result

Command:

```bash
git diff --check
```

Result: passed.

## 20. Files created

- `tests/test_original_editorial_shadow_scoring.py`
- `original_editorial_shadow_scoring_implementation_report.md`

## 21. Files modified

- `mrsMThatcher2.py`
- `mrs_log_digest.py`

## 22. git diff --stat

Tracked diff before adding this report:

```text
mrsMThatcher2.py  | 424 ++++++++++++++++++++++++++++++++++++++++++++++++++++++
mrs_log_digest.py | 130 +++++++++++++++++
2 files changed, 554 insertions(+)
```

The new test file is untracked, so it is not included in plain `git diff --stat`.

## 23. final git status --short

At report-writing time, expected task-specific entries are:

```text
 M mrsMThatcher2.py
 M mrs_log_digest.py
?? tests/test_original_editorial_shadow_scoring.py
?? original_editorial_shadow_scoring_implementation_report.md
```

The working tree also contains pre-existing untracked experimental/report/runtime artifacts from earlier work.

## 24. Explicit safety confirmations

- No X call occurred.
- No xAI call occurred.
- No external API call occurred.
- No post was created.
- No production state file was intentionally changed.
- No production receipt was intentionally changed.
- The live bot was not restarted or signalled.
- Production winner selection behaviour remains unchanged.
- Shadow results are observational only.
- Nothing was staged.
- Nothing was committed.
- Nothing was pushed.

## Deployment checklist

Do not perform these steps until the code has been reviewed and intentionally deployed.

1. Review `mrsMThatcher2.py`, `mrs_log_digest.py`, and `tests/test_original_editorial_shadow_scoring.py`.
2. Review this report and the diff.
3. Commit the implementation intentionally.
4. Add a local config override in `mrsMThatcher.local.json`:

```json
{
  "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING": true,
  "ORIGINAL_EDITORIAL_ANALYSIS_FILE": "/disks/disk1/etc/mrsMThatcher/original_image_editorial_analysis_experiment_v1.json",
  "ORIGINAL_EDITORIAL_SHADOW_WEIGHT": 0.32,
  "ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT": 4.0
}
```

5. Restart only the Python child safely using the established production process.
6. Verify startup logs include:

```text
Original editorial shadow scoring enabled.
```

7. Wait for the next real regular quote/image selection.
8. Verify the first `ORIGINAL_EDITORIAL_SHADOW_RESULT` log event appears.
9. Run `mrs_log_digest.py` on the relevant window and verify `## Original editorial shadow scoring`.
10. If needed, disable quickly by setting:

```json
{
  "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING": false
}
```

11. Restart only the Python child safely again.

## Corrective pre-deployment review pass

This focused pass retained the shadow architecture, formula, affinity vocabulary,
quote-dimension mapping, default weight (`0.32`), and adjustment cap (`4.0`).

### Complete 12-dimension validation

Every loaded image item must now contain exactly the 12 fixed dimensions. The
validator computes missing and unexpected key sets before validating values and
raises an error naming the image basename and both sets. Missing dimensions are
not treated as zero.

Unknown dimensions are rejected. This strict policy prevents a typo or future
schema drift from being silently ignored by the production-candidate scorer.

Tests cover one missing dimension, multiple missing dimensions, a complete valid
schema, and an unexpected dimension.

### Non-finite numeric rejection

Both local-config coercion and runtime validation now reject non-finite values
for:

- `ORIGINAL_EDITORIAL_SHADOW_WEIGHT`
- `ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT`

The metadata numeric validator also rejects NaN and positive/negative infinity
for every dimension score and `overall_editorial_utility`. Existing boolean,
malformed-string, negative-value, and 0..10 range checks remain in force.

### Corrected digest comparison denominator

The primary winner-change percentage now uses only comparable observations whose
production winner was original:

```text
original winner changes / comparable production-original observations
```

Generated-production observations remain visible as a separate count but cannot
dilute the original-vs-original percentage. A 10-observation fixture containing
5 original winners, 5 generated winners, and 2 original winner changes now
reports `40.0%`; generated-only and unchanged-original fixtures safely report
`0.0%`.

The rendered summary now includes `comparable_original_observations` and labels
the numerator `original_winner_changes`.

### Revised digest headings

- The changed-winner table column is now `line_no`, matching the value rendered.
- `Most frequent active quote dimensions` is now `Most frequent positive
  shadow-winner dimensions`, matching the positive `dimension_matches` payload.

The changed-winner table also defensively restricts rows to production-original
observations.

### Observational-safety tests

Additional tests prove that:

- the shadow logger leaves its candidate list deeply equal and in the same order;
- a controlled production-score tie selects the identical basename with shadow
  mode disabled and enabled when the random seed is reset;
- the production score remains exactly unchanged;
- the shadow logger consumes no selection randomness because production
  `random.choice` occurs before shadow evaluation;
- existing candidate/history assertions remain unchanged.

No scoring formula, production selection rule, history mutation, spacing rule,
or receipt behavior changed in this pass.

### Test logging isolation

The dedicated test imports the bot through `tests.test_unit_helpers`, which sets:

```text
MRS_TEST_MODE=1
MRS_BASE_DIR=<temporary unit-test directory>
MRS_LOG_FILE=<temporary unit-test log>
```

The production log changed size/mtime during the three-minute full-suite run
because the live bot continued normal operation. Inspection of the newly appended
tail found normal live scheduling/state-backup records only and no pytest paths,
temporary test paths, fake endpoints, test-mode markers, or synthetic shadow
events. The tests did not pollute the production log.

### Corrective-pass validation

Dedicated tests:

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_original_editorial_shadow_scoring.py
41 passed in 0.41s
```

Directly affected digest tests:

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_integration_harness.py -k digest
35 passed, 132 deselected in 7.44s
```

Full suite:

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
574 passed, 1 skipped, 1 warning in 186.50s (0:03:06)
```

Compilation:

```text
python3 -m py_compile mrsMThatcher2.py mrs_log_digest.py tests/test_original_editorial_shadow_scoring.py
passed
```

Whitespace validation:

```text
git diff --check
passed
```

Final tracked diff stat:

```text
mrsMThatcher2.py  | 424 ++++++++++++++++++++++++++++++++++++++++++++++++++++++
mrs_log_digest.py | 130 +++++++++++++++++
2 files changed, 554 insertions(+)
```

The untracked dedicated test and this untracked report are not represented by
plain `git diff --stat`.
