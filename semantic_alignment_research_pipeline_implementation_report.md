# Semantic alignment research pipeline implementation report

## 1. Architecture

Implemented a standalone offline package, `semantic_alignment`, with a thin CLI
in `analyse_semantic_alignment_xai.py`. It is not imported by production and has
no production feature flag or selector integration.

The three analysis stages are deliberately independent:

1. quote semantic fingerprint from quote text only;
2. image implied-message fingerprint from image pixels and a static prompt only;
3. editorial critic from the two validated fingerprints only.

Supporting modules provide atomic research writes, strict schemas, deterministic
shortlisting, cost-gated xAI execution, validation-set construction, cached-score
formula replay, and reporting.

## 2. Files created

- `analyse_semantic_alignment_xai.py`
- `semantic_alignment/__init__.py`
- `semantic_alignment/io.py`
- `semantic_alignment/pipeline.py`
- `semantic_alignment/prompts.py`
- `semantic_alignment/replay.py`
- `semantic_alignment/schemas.py`
- `semantic_alignment/shortlist.py`
- `tests/test_semantic_alignment_pipeline.py`
- `semantic_alignment_research/manual_validation_cases.json`
- `semantic_alignment_research/dry_run_summary.json`
- `semantic_alignment_research/semantic_alignment_report.md`
- `semantic_alignment_research/critic_results.csv`
- `semantic_alignment_research/runs/synthetic_replay.json`
- this report

No existing production source or metadata file was modified by this task.

## 3. Schemas

Version 1 validators cover:

- `quote_semantic_fingerprint` keyed by authoritative quote SHA-256;
- `image_implied_message` keyed by basename and image SHA-256;
- `quote_image_semantic_alignment` keyed by quote hash plus basename.

Validators enforce headers, 64-character lowercase hashes, required text/list
fields, finite confidence, finite 0-100 critic scores, and enumerated mismatch
types. Database envelopes contain schema/kind/prompt version, timestamps, items,
and retryable failure records. Writes use temporary files, fsync, and atomic
replace after small batches.

## 4. Prompt isolation guarantees

The quote prompt receives only quote text and explicitly forbids image reasoning.
The image prompt is static and receives image pixels separately; it receives no
quote, origin quote, generation prompt, filename interpretation, or existing
labels. The critic prompt allow-lists fingerprint fields and strips model,
prompt, origin, branch, winner, engagement, and baseline-score metadata. The
critic does not receive raw pixels.

Tests prove the forbidden fields and sentinel values do not enter critic input.

## 5. Shortlist method

The deterministic pre-score combines exact primary-issue match, primary-theme
overlap, secondary/concept overlap, desired-visual-evidence overlap, minimum
fingerprint confidence, and a `not_about` conflict penalty. Tokenisation,
weights, ordering, and basename tie-breaking are fixed and tested.

The default shortlist size is 15 active generated images per quote. Forced cases
are appended even outside the top 15 with provenance such as canonical branch
winner or recent production free-trade pairing. Duplicate quote/image pairs are
suppressed.

## 6. Validation-set construction

The generated validation file contains 150 unique generated-image pairings drawn
from saved production/editorial/identity winners across the canonical dataset.
Original winners are excluded from this generated-first phase.

The first case is the recent production free-trade pairing:

```text
quote_hash = 1ae9443573e42259af54c30a0ec90a6a8746e640b09e53ae1c28a4c0a2d0ed6b
image      = tg_661b01c39a8d223df51cd0365e79ffe7e3c4f86ac81fa0af114ce95be49cb831.png
expected   = related_but_indirect
```

Human fields support expected category, notes, and later preferred alternatives.
Real critic execution defaults to validation cases; full shortlist execution
requires explicit `--all-shortlists`.

## 7. Critic semantics

`semantic_alignment_score` remains separate from directness, editorial power,
visual specificity, identity dependence, and production score:

- 90-100: exceptional direct illustration;
- 75-89: strong and specific;
- 60-74: broadly relevant but indirect;
- 40-59: weak or secondary relationship;
- 0-39: misleading, unrelated, or contradictory.

Mismatch classes distinguish direct, related-but-indirect, generic ideological
overlap, visually strong but semantically wrong, contradictory, and ambiguous
image cases.

The free-trade verdict is intentionally pending real independent fingerprints;
the report does not fabricate an xAI result. Its expected human-review category
records the motivating hypothesis, not model evidence.

## 8. Canonical replay reuse

The canonical session was read only:

```text
simulation_runs/counterfactual_evidence_20x250_20260710
runs                 = 20
matched post indices = 5000
branch winner rows   = 15000
```

It was not regenerated. Inspection found that every saved selection has an empty
`candidate_detail` array. The dataset preserves winners and branch summaries but
not the full phase-specific candidates needed to evolve a fourth policy branch.
Therefore the tool does not pretend to reconstruct such a branch from winners.

Replay accepts a separately derived candidate-cache JSON and cached critic
results. A synthetic cache smoke test exercised reselection, exhaustion, entropy,
top-10 concentration, churn, and alignment improvement without invoking the
simulator or xAI. Producing a trustworthy canonical fourth branch requires a
future candidate-cache extraction/replay experiment.

## 9. Score-integration experiments

Five offline formulas are implemented:

- semantic alignment only;
- existing score plus small semantic/directness adjustment;
- existing score plus moderate adjustment;
- semantic gate below 45, then existing score;
- direct penalty for generic ideological overlap.

None is connected to production. Formula replay reports selections, winner
changes, candidate exhaustion, unique images, Shannon entropy, top-10 share, and
mean semantic improvement where matched scores exist.

## 10. Resumability and failures

Quote and image resume checks validate schema and compare quote text or current
image SHA-256. Completed unchanged records are skipped; missing, invalid, or
hash-changed items are retried. Critic records are similarly key- and
schema-validated. Checkpoints occur every five completions and at command end.
Image failures are also written separately to `image_analysis_failures.json`.
Permanent content/schema failures are marked non-retryable rather than retried
inside an unbounded loop.

Active and quarantined image modes use separate inventories and databases.

## 11. Cost controls

All normal commands are offline. Network code is instantiated only after both:

- `--execute-xai`;
- `--confirm-cost-limit-usd N` at or above the preflight estimate.

Dry-run inventory:

```text
quotes total / needing analysis       = 632 / 632
active generated / needing analysis   = 79 / 79
validation critic pairs               = 150
projected 15-per-quote shortlist pairs= 9480
conservative projected critic calls   = 9630
total projected calls                 = 10341
estimated tokens                      = 11272400
estimated cost                        = $206.82
validation-only critic estimate       = 150 calls / 165000 tokens / $3.00
```

The $0.02-per-call cost model is an explicit planning placeholder, not provider
billing truth. A real run must recheck current pricing and use a conservative
limit. No xAI calls were made in this task.

## 12. Tests and smoke results

Focused tests:

```text
13 passed in 0.30s
```

Full suite:

```text
850 passed, 1 skipped, 1 warning in 409.02s
```

The warning is the existing Starlette `TestClient` deprecation warning.

`py_compile` passed for the CLI, all package modules, and tests.
`git diff --check` passed. Production-log appended-interval inspection found no
pytest, fixture, dummy, or loopback markers.

Safe smoke tests completed:

- quote fingerprint dry-run count;
- active and quarantined image inventory paths;
- critic validation-only estimate;
- 20-run/5000-index canonical inventory without simulation;
- synthetic cached-score replay;
- Markdown/JSON/CSV report generation.

Production configuration and both generated metadata hashes were unchanged.
The healthy live bot naturally posted during the lengthy task and therefore
updated its own state/history/log; no research or test code opened those files
for writing.

## 13. Limitations

- No semantic fingerprints or critic judgements exist until an explicitly
  approved xAI run occurs.
- Existing quote/image metadata can seed inventories and provenance but is not
  relabelled as independent semantic fingerprint output.
- Canonical candidate sets were not saved, preventing a valid fourth evolving
  branch from the existing raw records alone.
- Original-image implied-message analysis is deferred and not required for the
  generated-first validation.
- Cost estimates require current price verification before execution.

## 14. First safe validation commands

Review estimates first:

```bash
python3 analyse_semantic_alignment_xai.py quote-fingerprints --dry-run
python3 analyse_semantic_alignment_xai.py image-fingerprints --dry-run
python3 analyse_semantic_alignment_xai.py critic --validation-only --dry-run
```

After explicit human approval and current price verification, the staged run is:

```bash
python3 analyse_semantic_alignment_xai.py quote-fingerprints --resume --execute-xai --confirm-cost-limit-usd 13
python3 analyse_semantic_alignment_xai.py image-fingerprints --resume --execute-xai --confirm-cost-limit-usd 2
python3 analyse_semantic_alignment_xai.py validation-set --limit 150
python3 analyse_semantic_alignment_xai.py critic --validation-only --resume --execute-xai --confirm-cost-limit-usd 3.50
python3 analyse_semantic_alignment_xai.py report
```

Do not run `--all-shortlists` until validation results and prompt quality have
been reviewed.

## 15. Git diff/status

All implementation files and research outputs are currently untracked. Existing
generated-image metadata modifications, four quarantined-image deletions, and
unrelated runtime/research artefacts remain untouched. Nothing was staged.

`git diff --stat` is empty for the task implementation because Git does not
include untracked files in that command. The task-specific untracked source,
test, and report files contain 1,129 lines in total; generated dry-run research
outputs are under `semantic_alignment_research/`. `git status --short` lists
`analyse_semantic_alignment_xai.py`, `semantic_alignment/`,
`tests/test_semantic_alignment_pipeline.py`, the research directory, and this
report as untracked, alongside the explicitly preserved pre-existing changes.

## 16. Confirmations

- No production behavior or production source was changed.
- No live shadow branch was added.
- The bot was not stopped, restarted, or signalled.
- No X, xAI, or other external API call was made by this task.
- No test post or reply was created.
- No production state, config, history, receipt, log, or metadata was edited by
  the research pipeline or tests.
- The canonical 20 x 250 trajectory dataset was not regenerated or overwritten.
- Nothing was staged, committed, pushed, or deployed.
