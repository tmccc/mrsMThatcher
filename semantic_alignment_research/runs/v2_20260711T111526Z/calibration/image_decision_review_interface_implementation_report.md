# Image-decision review interface implementation report

## 1. Summary

The human semantic-alignment review now requires an explicit operational image
decision in addition to relationships, relevance/directness bands and the two
1-5 ratings. Reviewers can keep the current image, ask the selector to try a
different generated image, or mark the case unsure.

The real 100-case dataset was migrated to schema v3 without prepopulating any
decision. It remains 0 complete / 100 incomplete before human review.

## 2. Files changed

- `semantic_alignment/calibration.py`
- `calibrate_semantic_alignment.py`
- `tools/semantic_alignment_calibration_app.py`
- `tests/test_semantic_alignment_calibration.py`
- `semantic_alignment_research/runs/v2_20260711T111526Z/calibration/human_review_cases.json`
- `semantic_alignment_research/runs/v2_20260711T111526Z/calibration/REVIEW_INSTRUCTIONS.md`
- generated calibration analysis/report files in the same research directory

No production source file was changed.

## 3. Schema change

The review schema was bumped from 2 to 3. Completed labels now include exactly
one of:

```text
keep_current_image
prefer_different_generated_image
unsure
```

The value must be an actual string and exact enum member. Empty or unknown
strings, booleans, integers, floats, lists, objects and null are rejected.

The free-trade case remains first in the calibration split with no human label
or decision prepopulated.

## 4. Completion rule

A completed review now requires:

- primary relationship;
- relevance band;
- directness band;
- appropriateness rating 1-5;
- publish-likelihood rating 1-5;
- image decision.

Secondary relationship and notes remain optional. Progress, paid-run guards,
reporting and threshold analysis all call the same strict validator.

## 5. Interface changes

The case page asks:

> If this were today’s scheduled post, what would you do?

Accessible radio controls provide:

- Keep this image
- Ask the bot to choose a different generated image
- Unsure

The page states that asking for a different candidate does not guarantee a
better alternative. Selected values are restored when revisiting a review.
Save-and-next, previous/next navigation, revision and schema-v3 progress all
remain available.

A visible mindset note says:

> Judge the pairing as it would appear in the published post, with the
> quotation shown alongside the image. Do not infer the generation prompt or
> intended design brief.

## 6. Keyboard shortcuts

- `K`: keep current image
- `D`: prefer a different generated image
- `U`: unsure
- `1`-`5`: appropriateness
- `Shift+1`-`Shift+5`: publish likelihood
- `S`: save and next
- `N`: next
- `P`: previous

Shortcuts do not fire while focus is in an input, select or textarea. Every
choice is usable through standard form controls without shortcuts.

## 7. Migration behaviour

Schema-v1 and schema-v2 files remain readable. Migration:

- preserves case IDs, relationships, ratings, notes and timestamps;
- does not invent `image_decision`;
- marks any legacy record without a decision incomplete;
- preserves incomplete records for revision;
- merges labels by deterministic case ID when rebuilding the review set.

No existing human label was present in the real file, so its migration produced
100 explicitly incomplete schema-v3 records.

## 8. Reporting changes

Human-review reports now include counts and percentages for:

- overall image decisions;
- decision by primary relationship;
- decision by appropriateness rating;
- decision by publish-likelihood rating;
- decision by critic overall-suitability band.

Anomaly lists identify:

- high critic score but prefer different;
- low critic score but keep current;
- appropriateness at least 4 but prefer different;
- publish likelihood at most 2 but keep current;
- unsure cases requiring later review.

Outputs remain marked preliminary for small samples. Current distributions are
empty because human review has not begun.

## 9. Threshold analysis

The read-only helper now evaluates two independent targets:

1. publish likelihood 4-5 versus 1-2;
2. `keep_current_image` versus `prefer_different_generated_image`.

For image decisions, keep is the positive class and prefer-different is the
negative class. `unsure` is excluded from binary fitting and its excluded count
is reported.

Each candidate threshold reports TP, FP, TN, FN, precision, recall, specificity,
false-positive rate and false-negative rate. The exploratory recommendation
maximises recall minus false-positive rate, then precision, then prefers the
lower threshold. It includes a mandatory untouched-hold-out caveat and refuses
analysis before at least 20 matched cases include both classes.

No production threshold was changed.

## 10. Tests

Commands:

```bash
python3 -m py_compile \
  calibrate_semantic_alignment.py \
  semantic_alignment/*.py \
  tools/semantic_alignment_calibration_app.py

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_semantic_alignment_pipeline.py \
  tests/test_semantic_alignment_calibration.py
```

Result: **64 passed in 0.90s**.

Coverage includes all three valid decisions; invalid/empty/type-confused values;
legacy incompleteness; required decision completion; atomic save/reload;
identity-preserving revision; progress; decision distributions and cross-tabs;
all requested anomaly lists; unsure exclusion; synthetic threshold confusion
matrices and specificity; prompt isolation; no paid execution; and temporary-
directory write isolation.

`git diff --check`: passed.

## 11. Smoke test

A loopback server on `127.0.0.1:8767` used a temporary copy of the review and
fingerprint files while reading the active image pool.

Verified:

- case loaded;
- image returned HTTP 200;
- decision controls rendered;
- K/D/U shortcut mapping rendered;
- save-and-next returned HTTP 303;
- reopening preserved `prefer_different_generated_image`;
- the case became complete;
- progress changed to 1/100;
- the real review file remained unchanged;
- the test server was stopped.

## 12. Limitations

- Reports and threshold tables remain unavailable until sufficient human labels
  and matched calibration-critic outputs exist.
- `unsure` is intentionally excluded from binary threshold fitting.
- A request for another image does not establish that an eligible alternative
  will be better.
- The review service remains loopback-only.

## 13. Start command

```bash
python3 tools/semantic_alignment_calibration_app.py \
  --project-dir /disks/disk1/etc/mrsMThatcher \
  --dataset /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/runs/v2_20260711T111526Z/calibration/human_review_cases.json \
  --host 127.0.0.1 \
  --port 8766
```

Open `http://127.0.0.1:8766/` locally or through an SSH tunnel. Do not expose
the write-capable review service publicly.

## 14. Git state

Research tooling remains untracked, so `git diff --stat` reports only the
pre-existing generated-image curation modifications and approved-pool
deletions. Exact final snapshots are stored beside this report as:

- `image_decision_git_diff_stat.txt`
- `image_decision_git_status_short.txt`

No unrelated working-tree change was altered, staged or discarded.

## 15. Confirmations

- No paid or external API call was made.
- No quote or image fingerprint was regenerated.
- No critic calibration or hold-out batch ran.
- No canonical simulation or replay ran.
- No production behaviour changed.
- The production bot was not stopped, restarted or signalled.
- No production state, config, history, receipt, log or metadata was edited by
  this work.
- Nothing was staged, committed, pushed or deployed.
