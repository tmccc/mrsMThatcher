# Unused-Code Second-Pass Report

## Executive summary

This second pass inspected the complete maintained Python tree rather than only
reviewing the earlier cleanup. It removed a further **234 lines**, added 34
replacement lines, and reduced the maintained Python line count by **200**
lines, from 103,572 to 103,372. The cumulative uncommitted cleanup is now 106
insertions and 791 deletions across 76 tracked files, a net reduction of 685
lines.

The principal finding was 149 lines of unreachable paid-execution code below
an unconditional retirement exception in
`semantic_alignment/relation_aware_veto.py`. The public
`production-top5` command and function remain present and fail closed with
the same error. The rest of this pass removed unreferenced constants, unused
parameters from private helpers, and unused local bindings.

No production posting, receipt, recovery, selector, semantic-safety or
engagement-analytics path was changed in this second pass.

## Scope and tools

The maintained corpus comprised 182 tracked Python files and 103,572 lines at
the start of this pass. The audit used:

- Ruff `F401,F811,F821,F822,F823,F841`;
- Pylint `unused-variable`, `unused-argument`, `unreachable` and
  `unused-private-member`;
- custom AST definition/load and duplicate-body inventories;
- repository-wide `rg` searches for each candidate symbol;
- call-site and import searches across production code and tests;
- searches for `getattr`, `importlib`, callback methods, dispatch tables,
  argparse commands and monkeypatch targets;
- external-consumer searches in `/home/tonym`, `/disks/disk1/etc`,
  `/usr/local/bin/runMrsMThatcher2`, and the systemd user unit;
- focused tests and the complete offline pytest suite.

No linter result was accepted on its own. A candidate was removed only after
its file-qualified name, call sites, dynamic use and external entry points had
been checked.

## Removed code

### Retired unreachable implementation

`semantic_alignment.relation_aware_veto.run_production_top5` previously
raised an unconditional `RuntimeError`, then retained 149 unreachable lines
which could never execute. Those lines included obsolete transport setup,
batch submission, result persistence and report generation from the failed v1
contract experiment.

The function, signature and `production-top5` CLI command were retained for
operational compatibility. The direct regression test still proves that the
command fails closed with the retirement error. No historical run artefact was
modified.

### Unreferenced module constants

The following file-qualified symbols had no executable read, import,
monkeypatch target, persisted-field dependency or external consumer:

| File | Removed symbols | Evidence |
|---|---|---|
| `quote_image_metadata_remediation.py` | `EXPECTED_DISCOVERED`, `MUTABLE_PRODUCTION_PATTERNS`, `CLAIM_TYPES`, `GENERAL_PORTRAIT_CLAIMS`, `DEFECT_CODES` | Definition-only; active count checks, strict transitions and literal defect strings remain elsewhere. |
| `reply_strategy.py` | `NO_REPLY_REASON_CATEGORIES` | Definition-only; the three persisted reason strings remain in prompts, validation, telemetry and digest tests. |
| `semantic_alignment/gemini_fallback.py` | `DEFAULT_VERTEX_FALLBACK_LIMIT` | Definition-only; explicit per-run limits remain. |
| `semantic_alignment/hybrid_reply_retrieval.py` | `DOCUMENT_FIELDS` | Definition-only; document construction uses its explicit active field logic. |
| `semantic_alignment/schemas.py` | `MATCH_TYPES` | Unused alias of the active `RELATIONSHIPS` set. |
| `semantic_alignment/thatcher_image_hunt.py` | `PROMPT_VERSION`, `TRIAGE_SCHEMA_VERSION`, `RIGHTS_STATUSES` | Definition-only; active discovery schema, response schema and review validation remain. |
| `tools/backtest_original_editorial_matching.py` | `CONSERVATIVE_WEIGHT` | Definition-only; active weight map remains. |
| `analyse_improved_pairwise_readiness.py` | `SOURCE` | Obsolete default path with no read. |
| `quote_attribution_cleanup.py` | `EXPECTED_COMPLETED` | Stale pre-cleanup count with no read; active 619/613/6 gates remain. |
| `semantic_alignment/discovered_image_preparation.py` | `SCHEMA_VERSION` | Definition-only; the active canonical and source-grounded schema versions remain. |
| `semantic_alignment/image_quote_shortlist_rerank.py` | `EXPECTED_IMAGES` | Definition-only; active quote and batch invariants remain. |
| `semantic_alignment/scene_grammar_pilot.py` | `SCHEMA_VERSION`, `MAX_CASES`, `MAX_ATTEMPTS` | Definition-only; active prompt version, image limit and hard cost ceiling remain. |

Names such as `CLAIM_TYPES` and `EXPECTED_DISCOVERED_IMAGES` in
`relation_aware_veto.py` are different, actively used symbols and were not
removed.

### Unused private parameters and local bindings

The following private/internal interfaces were narrowed and all call sites
updated:

- `analyse_semantic_alignment_bakeoff.write_report`: removed unused
  `quotes` and `images`;
- `recover_gemini_exhausted.merge`: removed unused `cases`;
- `run_improved_pairwise_pilot.preflight`: removed unused `manifest`;
- `image_quote_shortlist_rerank._load_batch`: removed unused `manifest`;
- `quote_research_retry_execution._retry_run_dir`: removed unused
  `parent_run`;
- `first_impression_validation.classify_bad`: removed unused `score`.

Unused named bindings were also removed from tuple unpacking and loops in:

- `analyse_first_impression.py`;
- `analyse_pairwise_correction.py`;
- `analyse_pairwise_validation.py`;
- `analyse_semantic_alignment_bakeoff.py`;
- `analyse_semantic_alignment_xai.py`;
- `quote_image_metadata_remediation.py`;
- `quote_image_selection_harness.py`;
- `recover_gemini_exhausted.py`;
- `recover_gemini_vertex.py`;
- `semantic_alignment/first_impression_validation.py`;
- `semantic_alignment/thatcher_image_hunt.py`;
- `tools/backtest_original_editorial_matching.py`.

These changes preserve each operation and remove only values that were never
read.

## Dynamic and external-consumer checks

- No removed symbol is referenced by a dynamic import, `getattr`, callback
  registration, dispatch table, argparse command, monkeypatch target or test
  fixture.
- `/usr/local/bin/runMrsMThatcher2` and
  `mrsMThatcher.service` invoke only the production entry point and contain
  no removed research/helper name.
- Searches under `/home/tonym` and `/disks/disk1/etc` found no external
  caller for the removed file-qualified interfaces.
- No persisted JSON key or report field was renamed or removed. Constants
  which merely duplicated persisted string values were deleted; the strings
  and their consumers remain.
- The retired `production-top5` CLI remains registered, so scripts receive a
  clear fail-closed retirement error rather than an unknown-command failure.
- No receipt barrier, reconciliation function, selector wrapper, recovery
  helper, posting command or systemd entry point was removed in this pass.

## Tests

One test was changed:

- `tests/test_first_impression_validation.py` now calls
  `classify_bad` without the removed, behaviourally unused score argument.

No test was deleted in this pass.

Results:

| Check | Result |
|---|---|
| Focused affected-module suite | 467 passed in 54.84s |
| Complete offline pytest suite | 1,783 passed, 1 expected skip in 689.36s |
| Compile every tracked Python file | passed |
| Ruff undefined/unused import and local checks | passed |
| `git diff --check` | passed |

The focused suite included relation-aware veto, metadata remediation,
reply strategy, Gemini fallback, hybrid retrieval, image discovery,
discovered-image preparation, shortlist reranking, scene grammar,
attribution cleanup, simulator harness, first-impression validation and the
original-image backtest.

## Reduction and files

Second-pass delta:

- lines inserted or replaced: 34;
- lines deleted: 234;
- net reduction: 200 lines;
- maintained Python lines: 103,572 to 103,372.

Files changed specifically by this pass:

`analyse_first_impression.py`,
`analyse_improved_pairwise_readiness.py`,
`analyse_pairwise_correction.py`,
`analyse_pairwise_validation.py`,
`analyse_semantic_alignment_bakeoff.py`,
`analyse_semantic_alignment_xai.py`,
`quote_attribution_cleanup.py`,
`quote_image_metadata_remediation.py`,
`quote_image_selection_harness.py`,
`recover_gemini_exhausted.py`,
`recover_gemini_vertex.py`,
`reply_strategy.py`,
`run_improved_pairwise_pilot.py`,
`semantic_alignment/discovered_image_preparation.py`,
`semantic_alignment/first_impression_validation.py`,
`semantic_alignment/gemini_fallback.py`,
`semantic_alignment/hybrid_reply_retrieval.py`,
`semantic_alignment/image_quote_shortlist_rerank.py`,
`semantic_alignment/quote_research_retry_execution.py`,
`semantic_alignment/relation_aware_veto.py`,
`semantic_alignment/scene_grammar_pilot.py`,
`semantic_alignment/schemas.py`,
`semantic_alignment/thatcher_image_hunt.py`,
`tools/backtest_original_editorial_matching.py`, and
`tests/test_first_impression_validation.py`.

The cumulative working-tree diff, including the earlier cleanup, contains 76
tracked files, 106 insertions and 791 deletions. Its aggregate
filename-and-content SHA-256 is
`93ccceda91d0468e4eb50b1b31400884ad43198077fe1e2d40dab6dc79529dcd`.

## Deliberately retained candidates

The following were investigated and retained:

- `relation_aware_veto.pair_response_schema(count)`: the count is part of a
  widely used schema-builder interface; exact cardinality is intentionally
  validated locally because Gemini rejects the larger schema constraint.
- provider/router callback parameters such as schema, attempt and grounding
  in `thatcher_image_hunt.py`: retained for transport parity and callback
  signatures.
- HTTP handler `do_GET`, `do_POST` and `log_message` methods: invoked by
  `BaseHTTPRequestHandler`, not by explicit Python calls.
- `tools.generated_image_review.app.configured_app`: a Uvicorn factory
  loaded by import string.
- `mrs_engagement_analytics.revise_snapshots_from_preserved_raw`: a tested,
  operational repair primitive; engagement analytics was explicitly outside
  this task.
- repeated `sha256_file`, atomic-write and small HTTP response helpers in
  standalone tools: similar bodies do not prove dead code, and consolidating
  them would be unrelated refactoring with extra coupling.
- CLI filter parameters in diagnostic simulation tools: externally visible
  command behaviour was retained even where a single implementation path does
  not currently consume every option.

## Production-service proof

Before and after this pass:

| Field | Before | After |
|---|---|---|
| State | active/running | active/running |
| Wrapper PID | 1075041 | 1075041 |
| Child PID | 1075042 | 1075042 |
| Start time | Sat 2026-07-18 18:43:35 BST | Sat 2026-07-18 18:43:35 BST |
| Restart count | 0 | 0 |
| Cgroup | `/user.slice/user-1000.slice/user@1000.service/app.slice/mrsMThatcher.service` | unchanged |

The final cgroup contained exactly the wrapper
`/usr/local/bin/runMrsMThatcher2` and one
`python3 /usr/local/bin/mrsMThatcher2.py` child. The running process loads
the installed `/usr/local/bin` copy, not these uncommitted working-tree
changes.

No command in this task posted to X, restarted or signalled the service,
opened production state/receipt/ledger/history files for writing, touched
engagement analytics, exposed or modified `mrsMThatcher.env`, committed,
pushed or deployed anything.

READY FOR INDEPENDENT REVIEW
