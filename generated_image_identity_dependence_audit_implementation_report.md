# Generated Image Identity-Dependence Audit Implementation Report

Date: 2026-07-10

## Executive summary

A standalone, resumable xAI vision audit was implemented and run against all 83
currently approved generated regular-post images. Production code, config,
state, histories, receipts and existing generated analysis were not modified.

The final calibrated audit found:

- 65 images contain a locally grounded specific intended person;
- 18 contain no grounded specific intended identity;
- 59 of the 65 person-containing images remain `safe` or `mostly_safe` for
  cross-quote reuse;
- 6 images are recommended `origin_quote_only`;
- 9 receive `small_penalty` recommendations;
- 68 remain `unrestricted`.

This supports a narrow hybrid policy experiment rather than a blanket penalty
for people or generated images.

## Files inspected

- `generated_image_analysis.json`
- all 83 files in `generated_review_approved_images/`
- `generated_review_approved_images/manifest.json`
- `generated_review_approved_images/grok_face_correction_manifest_v2.json`
- corrected/rejected face candidate directories and the pre-correction backup
- all matching `openai_generated_quote_images/items/<quote_hash>/` directories
- origin `quote.txt`, `quote_analysis.json`, `generation_prompt.txt`,
  `manifest.json`, `match_info.json` and response metadata
- `analyse_mrs_assets_xai_v4.py`
- `analyse_original_images_editorial_xai.py`
- `check_and_fix_thatcher_faces_xai.py`
- current production logs containing generated regular-image selections

## Existing metadata findings

All 83 approved basenames are valid `tg_<64hex>.png` names and all 83 map to a
complete origin directory. The origin data provides quote text, structured
semantic intent, named referenced people where applicable, and the original
generation prompt.

`generated_image_analysis.json` describes visible people, scenes and broad
reusability, but has no fields for intended identity, likeness recognisability,
meaning retention without identity, misidentification risk or safe cross-quote
reuse.

Thirty-nine approved files differ from the content hash in the existing
generated analysis because of the later face-correction workflow. Validation
was not weakened: 38 current files match accepted corrected outputs, and one
matches a retained correction candidate; in each case the pre-correction backup
matches the analysed hash. Arbitrary unexplained hash drift still fails.

The diagnostic image is grounded as Solzhenitsyn by its origin quote analysis
and generation prompt. Existing visual analysis describes only an elderly
bearded writer in a Soviet setting, demonstrating why a new vision judgement
was needed.

## Tool created

`tools/audit_generated_image_identity_dependence.py`

The tool supports:

- all-image or selected-basename processing;
- `--limit`;
- resume from current image SHA, origin hash, model and prompt version;
- `--overwrite`;
- atomic JSON replacement with file and parent-directory fsync after every item;
- bounded retry for transport, 408, 429 and 5xx failures;
- strict response schema and semantic validation;
- grounded-person enforcement that rejects invented identities;
- explicit multi-person grounding validation;
- deterministic fixed/hybrid and continuous offline penalties;
- log-derived recent candidate-set simulation;
- report and contact-sheet generation.

The tool does not import production bot code and has no production-state write
path.

## xAI analysis run

Final retained run:

- model: `grok-4.3`
- API: xAI Responses API, `https://api.x.ai/v1/responses`
- image mode: base64 data URL, high detail
- prompt version: `identity-dependence-2026-07-10-v2`
- successful images: 83
- failed images: 0
- retained usage events: 83
- retained raw `cost_in_usd_ticks`: `5,345,663,500`
- retained cost using the repository's established 1e10 tick conversion:
  approximately `$0.53456635`

Audit development also incurred discarded calibration calls. The complete v1
calibration aggregate was `4,604,376,000` ticks (`$0.46043760`), plus an earlier
interrupted 16-image preliminary pass and two discarded semantic-validation
retries whose usage was not retained. Therefore total development-run cost is
greater than `$0.99500395`; an exact total is unavailable and is not fabricated.

No server-side source/tool use was requested by the audit.

## Calibration corrections made during the run

Two audit-tool defects were found and corrected before accepting results:

1. Generic generator boilerplate saying an image *may* depict Thatcher was
   initially treated as image-specific intent. Grounding now uses only
   `historical_context.referenced_people` plus the existing per-image face audit
   when it says Thatcher is actually present.
2. The first complete rubric assigned every grounded person
   `origin_quote_only`. That was rejected as a non-discriminating person/no-person
   rule. Prompt v2 explicitly treats Thatcher as normal account context and
   reserves origin-only for genuine loss of meaning, factual misattribution or
   anachronism.

The final output contains only v2 records. A multi-person label edge case was
also fixed so a composite label is valid only when every named component is
independently grounded.

## Audit outputs

- `generated_image_identity_dependence_audit.json`
- `generated_image_identity_dependence_audit_report.md`
- `review/generated_identity_dependence/highest_identity_dependence.jpg`
- `review/generated_identity_dependence/lowest_recognisability_identity_dependent.jpg`
- `review/generated_identity_dependence/origin_quote_only.jpg`
- `review/generated_identity_dependence/safe_person_comparison.jpg`

## Corpus findings

### Specific intended person

| value | count |
|---|---:|
| true | 65 |
| false | 18 |

Grounded identities include Margaret Thatcher, Karl Marx, Solzhenitsyn, Stalin,
Enoch Powell and Ronald Reagan. No identity was invented where origin/face
metadata supplied none.

### Identity dependence

| level | count |
|---|---:|
| none | 18 |
| low | 31 |
| medium | 28 |
| high | 6 |
| essential | 0 |

### Cross-quote reuse safety

| safety | count |
|---|---:|
| safe | 19 |
| mostly_safe | 58 |
| contextual | 1 |
| risky | 1 |
| origin_quote_only | 4 |

### Recommended policy

| policy | count |
|---|---:|
| unrestricted | 68 |
| small_penalty | 9 |
| strong_penalty | 0 |
| origin_quote_only | 6 |

### Numeric summaries

- Typical-viewer recognisability: mean 6.34, range 0..9.
- Politically interested viewer recognisability: mean 7.41, range 0..10.
- Meaning retention without identity: mean 8.06, range 5..9.
- Origin-quote suitability: mean 8.64, range 6..9.
- Recommended penalty strength: mean 1.82, range 0..8.
- Confidence: mean 0.86, range 0.75..0.92.
- The final corpus retains a broad safe comparison group rather than collapsing
  on person presence.
- Six origin-only recommendations are visually represented in the dedicated
  contact sheet.

## Diagnostic image result

Image:

`tg_faf99f3030693b0a55f0116194551792f3c4ea51261d7310eca7fb4d33b667d5.png`

Origin quote:

> Through bitter experience Solzhenitsyn has realised the dangers posed by an
> over-mighty state. The more power that accrues to the state - the less freedom;
> freedom to make decisions, freedom to make one's own choices, freedom of ideas
> - remains for the individual citizen.

Recent cross-quote:

> I place a profound belief - indeed a fervent faith - in the virtues of self
> reliance and personal independence. On these is founded the whole case for the
> free society.

Production score: `90.41531202375181`, cross-quote, no origin boost.

Final audit:

- intended person: Solzhenitsyn, grounded by generation/origin metadata;
- person prominence: dominant;
- identity dependence: high;
- typical-viewer recognisability: 7/10;
- politically interested viewer recognisability: 9/10;
- meaning without identity: a writer reflecting on oppression under a
  communist regime, expressing state power versus individual liberty;
- meaning retention without identity: 8/10;
- cross-quote safety: origin-quote-only;
- recommended policy: origin-quote-only;
- recommended penalty strength: 8/10;
- confidence: 0.85.

The generic reading remains meaningful, but the highly specific pseudo-historical
scene risks misattribution or anachronism under unrelated quotes.

## Offline policy experiment

Only two recent generated selections had recoverable logged top-candidate sets,
so this is not exact historical replay.

- observations: 2
- hybrid fixed-policy winner changes: 1
- continuous-policy winner changes: 0

For the diagnostic selection:

- actual winner: `tg_faf99f...`, logged score 90.42;
- best recoverable original candidate: `t19.jpg`;
- hybrid fixed policy excludes the origin-only diagnostic image and selects
  `tg_0f3c7d...` at 86.63;
- continuous penalty reduces the diagnostic to 89.268 but does not change the
  winner.

This small sample suggests the continuous formula is too weak for the observed
failure, while the hybrid categorical policy directly prevents it. The sample
is too small to justify production deployment.

## Recommendation

Do not change production yet. Review the four contact sheets, then implement an
offline/shadow-only production-candidate experiment using the categorical
metadata:

- unrestricted: no change;
- small_penalty: modest cross-quote penalty;
- strong_penalty: substantial cross-quote penalty;
- origin_quote_only: exclude only for cross-quote competition;
- preserve normal origin-quote eligibility and boost.

Do not add a blanket person penalty or generated-image penalty. Collect more
real candidate-set observations before choosing numeric penalties.

## Tests and validation

Compilation:

```text
python3 -m py_compile tools/audit_generated_image_identity_dependence.py tests/test_generated_image_identity_dependence_audit.py
passed
```

Dedicated tests:

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_generated_image_identity_dependence_audit.py
17 passed in 0.15s
```

Final output validation:

```text
validated_final_items 83 failures 0
```

`git diff --check`: passed.

No production test suite was run because no production code changed.

## Git status

Task-created untracked files/directories include:

- `tools/audit_generated_image_identity_dependence.py`
- `tests/test_generated_image_identity_dependence_audit.py`
- `generated_image_identity_dependence_audit.json`
- `generated_image_identity_dependence_audit_report.md`
- `generated_image_identity_dependence_audit_implementation_report.md`
- `review/generated_identity_dependence/`

The repository also retains unrelated pre-existing untracked artifacts. Nothing
was staged, committed or pushed.

## Safety confirmations

- `mrsMThatcher2.py` was not modified.
- `mrs_log_digest.py` was not modified.
- `mrsMThatcher.local.json` was not modified.
- `generated_image_analysis.json` was not modified.
- Production state and history files were not modified by the audit tooling.
- Production receipts were not created, removed or modified.
- The live bot was not restarted or signalled during this task.
- No X call occurred.
- No post was made.
- xAI was called only by the standalone offline audit as documented above.
- Nothing was staged, committed or pushed.
