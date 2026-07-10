# Generated Identity-Policy Shadow Scoring Implementation Report

Date: 2026-07-10

## 1. Executive summary

Implemented a production-candidate generated-image identity-policy shadow
scorer. It is default-off, observational only, and independent of the existing
original-image editorial shadow scorer.

For every regular image selection when enabled, the observer receives the exact
phase-specific production `scored` list after production has selected its
winner. It applies the audited categorical policy to private copied rows, logs a
hypothetical winner, and cannot feed that result back into production.

The feature was not enabled or deployed. The live bot was not restarted or
signalled.

## 2. Files inspected

- `mrsMThatcher2.py`
- `mrs_log_digest.py`
- `mrsMThatcher.local.json`
- `generated_image_analysis.json`
- `generated_image_identity_dependence_audit.json`
- `generated_image_identity_dependence_audit_report.md`
- `generated_image_identity_dependence_audit_implementation_report.md`
- `tools/audit_generated_image_identity_dependence.py`
- `tests/test_generated_image_identity_dependence_audit.py`
- `tests/test_original_editorial_shadow_scoring.py`
- `original_image_editorial_analysis_experiment_v1.json`
- current production logs for the diagnostic post and recoverable candidates

## 3. Pre-existing Git and process state

Before this task:

- branch: `master`;
- commit: `aa8d86e Add original image editorial shadow scoring`;
- tracked working tree: clean;
- wrapper PID: `3631076`;
- Python child PID: `1647511`;
- numerous unrelated untracked audit, corpus, backup and review artifacts were
  present and preserved.

The generated identity audit/tool/test/report files from the preceding offline
task were already untracked and were used read-only by this implementation.

## 4. Actual audit schema and validation

The current audit has:

```text
schema_version = 1
analysis_kind  = generated_image_identity_dependence_audit
items          = object keyed by generated basename
input_count    = 83
failures       = 0
```

Each item stores the basename, current image SHA-256, origin quote hash and an
`analysis` object. The production loader is conditional: when the shadow feature
is disabled, the audit file is not opened or required.

When enabled, startup strictly validates:

- schema version and analysis kind;
- item object shape;
- generated `tg_<64hex>.<extension>` basename;
- exact current configured generated-pool membership and complete coverage;
- current image SHA-256;
- filename/origin quote hash agreement;
- policy enum: unrestricted, small_penalty, strong_penalty, origin_quote_only;
- identity-dependence enum: none, low, medium, high, essential;
- actual boolean type for `contains_specific_intended_person`;
- finite 0..10 recognisability, retention, suitability and penalty values;
- finite 0..1 confidence;
- rejection of booleans as numeric values, NaN and infinities.

Missing, stale or extra pool entries fail startup clearly when enabled. The
audit JSON is never modified.

## 5. Audit corpus policy counts

Read from the current JSON:

| policy | count |
|---|---:|
| unrestricted | 68 |
| small_penalty | 9 |
| strong_penalty | 0 |
| origin_quote_only | 6 |

Identity dependence:

| level | count |
|---|---:|
| none | 18 |
| low | 31 |
| medium | 28 |
| high | 6 |
| essential | 0 |

## 6. Diagnostic-image audit record

Image:

`tg_faf99f3030693b0a55f0116194551792f3c4ea51261d7310eca7fb4d33b667d5.png`

Verified current fields:

- origin quote hash:
  `faf99f3030693b0a55f0116194551792f3c4ea51261d7310eca7fb4d33b667d5`;
- image SHA-256:
  `008d93c308555106847e13e12be8d6bfa2b767ca026220949dffa03d09b64abf`;
- policy: `origin_quote_only`;
- identity dependence: `high`;
- intended person: Solzhenitsyn;
- typical-viewer recognisability: 7/10;
- politically interested viewer recognisability: 9/10;
- meaning retention without identity: 8/10;
- recommended penalty strength: 8/10;
- confidence: 0.85.

No basename-specific rule was added.

## 7. Production selection path used

The current production path is:

1. select a quote;
2. build current image paths from the original and optionally generated pools;
3. apply seasonal/stale metadata exclusion;
4. apply generated spacing filtering;
5. apply used-cycle and last-image boundary filtering for the active phase;
6. score each remaining candidate and reject strong visual mismatches;
7. apply the existing generated origin-quote boost to the production score;
8. choose the production winner with the existing `random.choice(tied)`;
9. log `REGULAR_IMAGE_SELECTED`;
10. run the existing original editorial observer;
11. run the new generated identity-policy observer;
12. return the already-selected production choice.

The new observer is called only after production winner selection.

## 8. Exact phase-specific candidate capture

The observer receives the same local `scored` list used to compute the
production maximum and tie set. It does not enumerate the full pool or reload
discarded candidates.

Therefore it automatically respects:

- used-image history;
- generated spacing;
- seasonality;
- stale metadata;
- strong mismatch rejection;
- normal phase;
- forced-cycle-reset phase;
- final last-image fallback phase.

The event records the existing phase vocabulary: `normal`,
`forced_cycle_reset`, or `last_image_fallback`.

## 9. Exact policy algorithm

The observer copies each production candidate into a private row and starts from
the exact existing `score`.

| candidate | shadow behavior |
|---|---|
| original | unchanged production score |
| generated origin-quote match | unchanged production score, including existing boost |
| cross-quote unrestricted | unchanged |
| cross-quote small_penalty | production score minus 6.0 |
| cross-quote strong_penalty | production score minus 15.0 |
| cross-quote origin_quote_only | excluded from shadow ranking only |

The observer never recalculates baseline compatibility and never combines the
original editorial adjustment with the identity adjustment.

An origin-only exclusion uses `null` adjustment/score fields rather than an
invented extreme negative score.

## 10. Penalty defaults

```text
GENERATED_IDENTITY_SHADOW_SMALL_PENALTY  = 6.0
GENERATED_IDENTITY_SHADOW_STRONG_PENALTY = 15.0
```

Six points matches the completed audit's categorical offline experiment. The
audit contained no strong-penalty records, so the requested conservative
shadow-only default of 15 points was used instead of claiming empirical tuning.

Both values are explicit local-config allowlist entries and reject booleans,
malformed values, negatives, NaN and infinities.

## 11. Origin-quote handling

If a generated candidate's existing `origin_quote_match` is true, the identity
policy does nothing regardless of its cross-quote recommendation. Its exact
production score, including the existing +4 origin boost where configured,
enters shadow ranking unchanged.

This preserves the audit's purpose: constrain risky reuse, not the image's
intended use.

## 12. Origin-quote-only handling

For cross-quote use only, `origin_quote_only` removes the generated candidate
from the hypothetical ranking. Production still sees and may select it. The
event records:

```text
production_identity_action = generated_cross_quote_origin_only_excluded
production_identity_adjustment = null
production_identity_shadow_score = null
```

If no shadow-eligible candidate remains, the hypothetical winner fields are
null; production still proceeds normally.

## 13. Tie and RNG safety

The shadow scorer calls no random function and never seeds or reads RNG state.

It computes the maximum shadow score. If the actual production winner remains
eligible and tied at that maximum, it remains the shadow winner, avoiding a
false change caused only by tie policy. Otherwise the lexicographically first
basename wins deterministically.

Tests prove RNG state is unchanged, candidate order/content remain deeply equal,
and a production tie produces the same actual basename with both shadow systems
enabled or disabled under the same seed.

## 14. Interaction with original editorial shadow scoring

The existing observer remains intact and runs first. Its original-only
editorial scores are not supplied to the identity observer. The identity
observer leaves original candidates at production baseline.

Tests enable both systems simultaneously and prove the production winner and
score remain identical to both disabled. Events retain separate names and
schemas.

## 15. Runtime failure boundary

Startup validation is strict when enabled. After a production winner has been
selected, an unexpected observer exception is caught and logged with:

```text
Generated identity-policy shadow evaluation failed; production selection remains unchanged.
```

This prevents an observational runtime failure from suppressing an otherwise
valid post.

## 16. Machine-parseable event

Event prefix:

```text
GENERATED_IDENTITY_POLICY_SHADOW_RESULT {JSON}
```

The JSON includes quote hash/line, phase, production winner/source/score/origin
match/policy/action/adjustment/shadow score; hypothetical winner equivalents;
winner-changed flag; exact production candidate counts; generated origin/cross
counts; policy counts; capped excluded and penalised basename lists with
truncation flags; configured penalties; and shadow tie count.

Exactly one event is emitted per successful regular selection when enabled. It
does not dump full rankings or prose.

## 17. Digest support

Added `## Generated identity-policy shadow scoring`, explicitly labelled
shadow-only and hypothetical.

The primary winner-change denominator is observations in which at least one
cross-quote generated candidate was penalised or excluded. Unaffected
observations cannot dilute the percentage; zero relevant observations safely
produce 0.0%.

The digest reports:

- total/original/generated production observations;
- generated origin-quote and cross-quote production winners;
- policy-relevant observations and winner changes;
- production winners excluded or penalised;
- total excluded/penalised cross-quote candidates;
- frequent excluded/penalised images, production policies, shadow winners and phases;
- changed-winner table including replacement source;
- dedicated production-origin-only-cross-quote table.

No event is carried across digest windows.

## Diagnostic post simulation

### Originating quote

For the image's Solzhenitsyn origin quote, `origin_quote_match=true`; the audit's
origin-only recommendation is bypassed and the exact production score, including
any existing origin boost, remains eligible unchanged.

### Recent self-reliance/free-society cross-quote

The logged production winner was the diagnostic image at 90.41531202375181 with
`origin_quote_match=false`. The general policy classifies it as an origin-only
cross-quote exclusion in shadow while leaving production untouched.

The production log preserves a recoverable top-five candidate set, not the full
exact historical candidate list. On that limited set, the hypothetical winner
would be unrestricted generated image:

`tg_0f3c7da5b7971b89c68fb0a5a07b1b23c563c32a94ee6b3ae5a77fce11d95bcc.png`

at 86.63. The recoverable best original was `t19.jpg` at 64.03. This is not
claimed as exact full historical replay.

## 18. Test logging isolation

The dedicated tests import through `tests.test_unit_helpers`, which sets
`MRS_TEST_MODE=1`, a temporary base directory, temporary log and loopback-only
fake endpoints before importing the bot.

The production log grew during the full suite because the live bot continued
normal operation. Inspection of only the appended bytes found no pytest paths,
temporary unit-test paths, loopback endpoints, test-mode markers or generated
identity shadow events. No test pollution occurred.

## 19. Dedicated test result

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_generated_identity_policy_shadow_scoring.py
34 passed in 0.21s
```

## 20. Existing original-shadow result

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_original_editorial_shadow_scoring.py
41 passed in 0.22s
```

## 21. Digest test result

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_integration_harness.py -k digest
35 passed, 132 deselected in 7.63s
```

## 22. Full-suite result

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
625 passed, 1 skipped, 1 warning in 188.55s (0:03:08)
```

The warning is the existing Starlette/httpx deprecation warning.

## 23. Compilation

```text
python3 -m py_compile mrsMThatcher2.py mrs_log_digest.py tests/test_generated_identity_policy_shadow_scoring.py
passed
```

## 24. Whitespace validation

```text
git diff --check
passed
```

`ruff check` also passed for both changed production files and the new test.

## 25. Files created

- `tests/test_generated_identity_policy_shadow_scoring.py`
- `generated_image_identity_policy_shadow_scoring_implementation_report.md`

## 26. Files modified

- `mrsMThatcher2.py`
- `mrs_log_digest.py`

No existing audit output, audit tooling, original shadow metadata or production
runtime file was modified.

## 27. Git diff stat

Tracked diff:

```text
mrsMThatcher2.py  | 278 +++++++++++++++++++++++++++++++++++++++++++++++++++++-
mrs_log_digest.py | 126 +++++++++++++++++++++++++
2 files changed, 402 insertions(+), 2 deletions(-)
```

The untracked new test/report are not included by plain `git diff --stat`.

## 28. Final Git status

Task-specific entries:

```text
 M mrsMThatcher2.py
 M mrs_log_digest.py
?? tests/test_generated_identity_policy_shadow_scoring.py
?? generated_image_identity_policy_shadow_scoring_implementation_report.md
```

Other untracked entries pre-date this task or belong to the completed offline
audit and remain untouched.

## Explicit safety confirmations

- No X call occurred.
- No xAI call occurred.
- No external API call occurred.
- No post was created.
- No production state or history was manually changed.
- No production receipt changed.
- No production config changed.
- The live bot was not restarted or signalled.
- The original editorial shadow feature was not redesigned or enabled/disabled.
- Generated identity-policy shadow mode remains disabled; its local-config keys
  are absent.
- Nothing was staged.
- Nothing was committed.
- Nothing was pushed.
