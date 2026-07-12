# Human review rating interface implementation report

## 1. Summary

The semantic-alignment review workflow now captures two direct editorial
judgements in addition to the structured relationship and band labels:

- image/quotation appropriateness, 1-5;
- likelihood of publishing the pairing, 1-5.

The real 100-case review dataset was migrated to schema v2 without inventing
ratings. It currently contains 0 complete and 100 incomplete reviews. No paid
critic operation ran and no calibration cost ledger was created.

## 2. Files changed

- `semantic_alignment/calibration.py`
- `calibrate_semantic_alignment.py`
- `tools/semantic_alignment_calibration_app.py`
- `tests/test_semantic_alignment_calibration.py`
- `semantic_alignment_research/runs/v2_20260711T111526Z/calibration/human_review_cases.json`
- `semantic_alignment_research/runs/v2_20260711T111526Z/calibration/REVIEW_INSTRUCTIONS.md`
- generated read-only calibration analysis/report files under that directory

All are offline research files. No production module was changed.

## 3. Schema changes

The review dataset schema is now version 2. Completed `human_label` objects
require:

```json
{
  "primary_relationship": "illustrates_claimed_consequence",
  "secondary_relationship": null,
  "relevance": "moderate",
  "directness": "low",
  "appropriateness_rating": 3,
  "publish_likelihood_rating": 2,
  "notes": "optional",
  "updated_at": "UTC timestamp"
}
```

Ratings use strict `type(value) is int` validation. Values 1-5 are accepted;
zero, six, booleans, floats, strings and null are rejected for completed
reviews. The HTTP form explicitly converts only the exact strings `1` through
`5` before validation.

Case IDs remain deterministic and unchanged. Image binaries are not stored in
review records.

## 4. Rating definitions

Appropriateness asks how well the image fits the quotation:

1. Clearly inappropriate
2. Weak fit
3. Acceptable but indirect
4. Strong fit
5. Exceptional fit

Publish likelihood asks whether the reviewer would actually choose to post the
pairing:

1. Definitely would not publish
2. Unlikely to publish
3. Might publish
4. Likely to publish
5. Definitely would publish

An image can be moderately appropriate but still have low publish likelihood
because it is confusing, generic, unattractive or editorially risky.

## 5. Completion rules

A review is complete only when all of these validate:

- primary relationship;
- relevance band;
- directness band;
- appropriateness rating;
- publish-likelihood rating.

Secondary relationship and note are optional. Progress, paid-run guards,
reporting and threshold analysis all use this same validator. Presence of a
`human_label` object alone is not sufficient.

## 6. Interface changes

Each case page shows:

- quote text;
- active image with alt text;
- concise quote fingerprint/decomposition summary;
- concise image fingerprint/decomposition summary;
- expandable old critic result;
- relationship and band controls;
- two accessible 1-5 radio groups with value descriptions;
- optional reviewer note;
- visible complete/incomplete state and overall progress;
- previous, progress and next navigation;
- save-and-next;
- restored values when revisiting a completed case.

The interface uses Python's standard-library loopback HTTP server. Flask is not
installed in the project environment, so no new dependency was introduced.
The server refuses non-loopback binding, uses a per-process CSRF token, serves
only known active-image basenames, and writes only the selected research review
file.

## 7. Keyboard shortcuts

- `1`-`5`: appropriateness
- `Shift+1`-`Shift+5`: publish likelihood
- `S`: save and next
- `N`: next without saving
- `P`: previous without saving

Shortcuts are disabled while focus is in an input, select or textarea. Every
operation remains available through ordinary accessible controls.

## 8. Backwards compatibility

Schema-v1 review files remain readable. Migration:

- retains deterministic case IDs;
- preserves relationship labels, bands, notes and optional legacy booleans;
- never fabricates either rating;
- marks legacy labels without ratings incomplete;
- adds `review_complete` consistently;
- writes an `updated_at` timestamp on revision.

Rebuilding the 100-case selection now merges existing labels by case ID. This
fixes the previous risk that `build-review` could overwrite completed work.

## 9. Reporting changes

The read-only `report` command produces JSON and Markdown with:

- appropriateness distribution;
- publish-likelihood distribution;
- mean and median ratings by human primary relationship;
- mean and median ratings by old critic score band;
- high-critic/low-publish cases;
- low-critic/high-publish cases;
- Spearman correlations for critic relevance vs appropriateness, critic
  directness vs appropriateness, and critic overall suitability vs publish
  likelihood.

Results are marked preliminary below 50 complete reviews. Correlations remain
`null` until matched calibration-critic results exist. Current real output is
correctly empty because no human review has begun.

## 10. Threshold helper

`threshold-analysis` evaluates candidate overall-suitability thresholds against:

- positive: publish likelihood 4-5;
- negative: publish likelihood 1-2;
- excluded as indeterminate: publish likelihood 3.

For every threshold it calculates TP, FP, TN, FN, precision, recall, false-
positive rate and false-negative rate. It proposes an exploratory threshold by
Youden-style separation, with a mandatory hold-out caveat.

It refuses analysis until at least 20 matched complete cases contain both
positive and negative classes. Current status is unavailable with 0 cases.
No production threshold is changed.

## 11. Tests

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

Result: **50 passed in 0.82s**.

Coverage includes all valid ratings, zero/six, booleans, floats, strings and
null rejection; legacy incompleteness; completed validation; atomic save;
identity-preserving revision; progress; distributions; Spearman correlation;
threshold confusion matrices; UI controls; save/reopen; prompt isolation; paid-
run guarding; and mocked-only API paths.

`git diff --check`: passed.

## 12. Smoke test

A loopback server was started on `127.0.0.1:8767` against a temporary copy of
the review and fingerprint files. The active image pool was read-only.

Results:

- case page loaded;
- image returned HTTP 200;
- both rating controls were present;
- save-and-next returned HTTP 303;
- reopened case displayed `Complete`;
- appropriateness 3 and publish likelihood 2 were restored;
- only the temporary review copy changed;
- test server was stopped after verification.

The real review file remains 0/100 complete with no labels.

## 13. Limitations

- Statistical outputs remain empty until human review begins.
- Correlations require both human labels and new calibration-critic scores.
- Threshold recommendations are exploratory and require untouched hold-out
  validation before any operational interpretation.
- The review service is deliberately loopback-only and has no LAN authentication
  mode.

## 14. Start command

```bash
python3 tools/semantic_alignment_calibration_app.py \
  --project-dir /disks/disk1/etc/mrsMThatcher \
  --dataset /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/runs/v2_20260711T111526Z/calibration/human_review_cases.json \
  --host 127.0.0.1 \
  --port 8766
```

Open `http://127.0.0.1:8766/` on the server, or use an SSH tunnel from the
owner's workstation. Do not expose this write-capable review service publicly.

## 15. Git state

Research tooling and outputs are untracked, so normal `git diff --stat` lists
only the pre-existing generated-image curation modifications and four approved-
pool deletions. Exact snapshots are saved as:

- `rating_interface_git_diff_stat.txt`
- `rating_interface_git_status_short.txt`

No unrelated working-tree change was discarded, overwritten, staged or altered.

## 16. Confirmations

- No paid API or external API call was made.
- Existing fingerprints were reused and not regenerated.
- No critic calibration or hold-out run occurred.
- No canonical simulation or replay ran.
- No production behaviour changed.
- The production bot was not stopped, restarted or signalled.
- No production state, config, history, receipt, log or metadata was edited by
  the review tooling.
- Nothing was staged, committed, pushed or deployed.
