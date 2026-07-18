# Unused-code cleanup: independent final review

Date: 18 July 2026
Project: `/disks/disk1/etc/mrsMThatcher`
Reviewed branch/HEAD: `master` / `87336736dffb38fbbe45a3f0c1c58d6bc1c7ccd7`

## Executive verdict

The cumulative cleanup is ready for separately authorised deployment after one
proven defect was corrected during this review. The cleanup had removed the
read of `policy_comparison.json` from `finalize_semantic_large_bakeoff.py`
because its assigned value was unused. The read itself was a required
fail-fast input-integrity check and the generated report explicitly refers to
that file. The read has therefore been restored without restoring the unused
local variable or changing report output.

No unsafe deletion was found in the live posting, receipt, reconciliation,
selection, recovery or reply-safety paths. The current exact suite collects
1,784 tests and the final full run passed 1,783 with one expected skip. The
production service retained the same wrapper PID, child PID, start timestamp
and restart count throughout.

## Test-count reconciliation

The apparent reduction from 1,786 passed to 1,783 passed is exactly explained:

```text
Pre-cleanup collected                         1,787
Tests of deleted dead interfaces                 -6
New parameterised tests of active validator      +3
Current collected                             1,784
Current expected skip                            -1
Current passed                                1,783
```

This is a net reduction of three passing test cases, not three unexplained or
uncollected tests.

### Commands and results

| Stage | Exact command evidenced by report/run | Collected | Passed | Skipped | Deselected |
|---|---|---:|---:|---:|---:|
| Pre-cleanup digest023 review | `python3 -m pytest -q` | 1,787 inferred from result and confirmed against an isolated `git archive HEAD` snapshot | 1,786 | 1 | 0 |
| First cleanup audit | The report records only "Full repository suite" and does not preserve the exact invocation | 1,781 inferred | 1,780 | 1 | not reported |
| First independent cleanup review | `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q` | 1,784 inferred | 1,783 | 1 | 0 |
| Second cleanup pass | `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q` | 1,784 inferred | 1,783 | 1 | 0 |
| This review, required collection | `python3 -m pytest --collect-only -q` | 1,784 | n/a | n/a | 0 |
| This review, required full run, first attempt | `python3 -m pytest -q` | 1,784 | 1,782 | 1 | 0 |
| This review, required full run, confirmation | `python3 -m pytest -q` | 1,784 | 1,783 | 1 | 0 |

Plugin autoload is not the cause. A current
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest --collect-only -q` run also
collected 1,784 tests, and its node-ID list was byte-identical to the ordinary
collection.

The only skip is:

`tests/test_integration_harness.py::test_launcher_matches_master_on_promotion_branch`

It intentionally skips on `master`, where the promotion-branch launcher parity
check is not meaningful. Neither required command used a selection expression,
so neither had deselected tests.

## Exact node-ID changes

An isolated archive of committed `HEAD` was collected with the same ordinary
pytest command and compared by exact node ID with the working tree.

Six committed tests removed by the cleanup:

1. `tests/test_reply_strategy.py::test_enabled_strategy_requires_accuracy_and_completed_packets_only`
2. `tests/test_unit_helpers.py::test_choose_unused_image_resets_when_all_images_used`
3. `tests/test_unit_helpers.py::test_choose_unused_line_resets_when_all_lines_used`
4. `tests/test_unit_helpers.py::test_choose_unused_line_returns_non_empty_line_and_marks_empty_lines`
5. `tests/test_unit_helpers.py::test_image_cycle_remaining_set_prevents_repeat_until_unused_exhausted`
6. `tests/test_unit_helpers.py::test_image_cycle_resets_only_after_all_images_used_and_avoids_boundary_duplicate`

Three current parameterised tests added against the active production validator:

1. `tests/test_unit_helpers.py::test_active_reply_strategy_validator_requires_fail_closed_safety_flags[accuracy_first-...]`
2. `tests/test_unit_helpers.py::test_active_reply_strategy_validator_requires_fail_closed_safety_flags[completed_packets_only-...]`
3. `tests/test_unit_helpers.py::test_active_reply_strategy_validator_requires_fail_closed_safety_flags[no_hashtags-...]`

No test file was deleted or renamed. No other committed node ID disappeared.
The six deletions exercised only the deleted compatibility wrappers or duplicate
reply-strategy validator. Current selector-cycle tests and the three new active
configuration-validator cases retain direct coverage of the production
invariants.

## Full-suite timing failure

The first exact full run had one failure:

`tests/test_quote_research_corpus.py::test_completed_worker_slot_refills_while_other_call_is_slow`

Its fake slow worker waits only one second for the third worker to be scheduled.
Under full-suite load that event missed the deadline once, causing all three fake
packets to be invalidated. The cleanup changed only unused imports in the test
and implementation files; it did not change `CorpusRunner`, worker scheduling,
the test timeout or the assertion.

Evidence that this was transient:

- immediate isolated rerun: 1 passed;
- ten consecutive isolated reruns: 10 passed;
- second exact full-suite run: 1,783 passed, 1 skipped;
- the second run passed the same test under full-suite load.

The initial failure is retained here rather than hidden. The one-second test
deadline remains a pre-existing source of timing flakiness, but it is not a
cleanup regression and was not changed under this task's restrictions.

## Cumulative safety audit

### Receipt and recovery paths

The deleted receipt helpers were uncalled aggregate wrappers:

- `block_if_unresolved_main_post_receipt`
- `block_if_unresolved_meme_post_receipt`

Active fail-closed protection remains in the paths actually used:

- startup calls `reconcile_main_post_receipts`;
- regular posting reconciles receipts before selection;
- meme posting reconciles the meme receipt and blocks on an unresolved regular receipt;
- receipt creation refuses conflicting regular/meme receipts;
- invalid or simultaneous receipts raise rather than being ignored;
- confirmed-write reconciliation, interrupted-write replay and durable state
  persistence remain tested.

Repository and external searches found no dynamic import, `getattr`, callback,
dispatch-table, CLI, shell, systemd, wrapper or monkeypatch reference to either
deleted aggregate wrapper.

### Quotation and image selectors

Deleted compatibility or random-selection wrappers:

- `choose_unused_line`
- `choose_unused_image`
- `choose_random_unused_image`
- `available_image_basenames`
- `quote_metadata_for_line`

Production continues to use:

- `choose_unused_line_candidate` and `select_quote_candidate`;
- `quote_candidates_for_current_cycle`;
- `choose_matched_unused_image`;
- `available_currently_eligible_image_basenames`;
- hash-based `quote_metadata_for_hash`.

These active functions retain unresolved/removed quotation exclusion, seasonal
weighting, no-repeat cycles, current image eligibility, generated-image policy,
semantic-veto shadow observation and deterministic production tie handling.
The deleted wrappers had only tests or wrapper-to-wrapper callers.

### Reply strategy

Deleted reply symbols:

- duplicate `DEFAULT_REPLY_STRATEGY`;
- duplicate `validate_reply_strategy_config`;
- convenience boolean `reply_is_repetitive`;
- unused `NO_REPLY_REASON_CATEGORIES` constant.

The bot's active runtime configuration and validator remain in
`mrsMThatcher2.py`. Active repetition handling uses
`reply_repetition_reason`, preserving diagnostic reasons rather than the deleted
boolean wrapper. Direct tests now exercise the active fail-closed validator.

### Offline and research utilities

The second-pass removals were checked by AST comparison, repository-wide `rg`,
import/call-site inspection, CLI registration, shell/systemd inspection and
searches under `/home/tonym`, `/usr/local/bin/runMrsMThatcher2` and relevant
scripts under `/disks/disk1/etc`.

Notable safe removals include:

- unused report/helper functions `file_identity`, `markdown_escape`,
  `mark_interrupted_sending_ambiguous`, `write_retry_validation_report`,
  `_normal_terms`, `_sha256_directory` and `_title_key`;
- unused constants in analysis, schema, image-discovery and research tools;
- unused parameters from directly verified internal call sites;
- 149 lines after the unconditional retirement exception in
  `run_production_top5`.

The retired `production-top5` function, signature and CLI registration remain.
It still fails closed with the deliberate v1-retirement exception. Only the
statically unreachable implementation following that unconditional exception
was removed.

No persisted JSON field name, externally used CLI name, systemd entry point or
production wrapper contract was removed.

## Defect found and restored

`finalize_semantic_large_bakeoff.main()` previously loaded
`policy_comparison.json` into an otherwise unused `pol` variable. The cleanup
removed the complete expression. This was unsafe because loading and parsing the
file is a precondition for finalisation, and the resulting report explicitly
states that policy A-H coverage is stored in that file.

The final implementation performs:

```python
json.load(open(R / "policy_comparison.json"))
```

It therefore preserves missing/malformed-input failure without retaining a dead
assignment or changing output. This is an offline research finaliser and does
not affect the production bot.

Pre-review hash of that file:
`dc79d907e5fc0311dc45834eddd84234bdcd69b1f37a7bfa0b74b81cdcd9508c`

Final hash:
`90c127d1668cea83e6a899c1766de58e6e3714957812dc628aaa62a373d4b7e4`

No other code was changed during this review.

## Validation results

| Command | Result |
|---|---|
| `git diff --check` | passed before and after review |
| `python3 -m pytest --collect-only -q` | 1,784 collected in 2.23s |
| `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest --collect-only -q` | 1,784; identical node IDs |
| isolated committed-HEAD collection | 1,787 collected |
| first `python3 -m pytest -q` | 1,782 passed, 1 failed, 1 skipped in 689.39s; transient one-second scheduler test |
| isolated failing test | 1 passed |
| ten repeated isolated runs | 10 passed |
| final `python3 -m pytest -q` | 1,783 passed, 1 skipped, 3 pre-existing deprecation warnings in 685.78s |
| `python3 -m py_compile finalize_semantic_large_bakeoff.py` | passed |

The warnings are the existing Starlette/httpx warning and two Beautiful
Soup/lxml warnings.

## Diff summary

```text
76 tracked files changed, 106 insertions(+), 791 deletions(-)
Net reduction: 685 lines
```

The current report is untracked, as are the three earlier cleanup reports.
Nothing is staged.

### Complete modified tracked-file list

```text
analyse_first_impression.py
analyse_first_impression_validation.py
analyse_generation_prompt_pilot.py
analyse_improved_pairwise_readiness.py
analyse_meta_critic_validation.py
analyse_mrs_assets_xai_v4.py
analyse_pairwise_correction.py
analyse_pairwise_validation.py
analyse_pairwise_validation_results.py
analyse_provider_outliers.py
analyse_semantic_alignment_bakeoff.py
analyse_semantic_alignment_large.py
analyse_semantic_alignment_xai.py
calibrate_semantic_alignment.py
check_and_fix_thatcher_faces_xai.py
finalize_semantic_large_bakeoff.py
mrsMThatcher2.py
package_openai_corpus_for_upload.py
quote_attribution_cleanup.py
quote_image_metadata_remediation.py
quote_image_selection_harness.py
recover_gemini_exhausted.py
recover_gemini_vertex.py
reply_strategy.py
run_improved_pairwise_pilot.py
run_pairwise_calibration.py
semantic_alignment/bakeoff.py
semantic_alignment/discovered_image_preparation.py
semantic_alignment/first_impression.py
semantic_alignment/first_impression_validation.py
semantic_alignment/gemini_fallback.py
semantic_alignment/generation_prompt_pilot.py
semantic_alignment/hybrid_reply_retrieval.py
semantic_alignment/image_quote_provider_compatibility.py
semantic_alignment/image_quote_shortlist_rerank.py
semantic_alignment/large_bakeoff.py
semantic_alignment/openai_quality_trial.py
semantic_alignment/pairwise_validation.py
semantic_alignment/pipeline.py
semantic_alignment/provider_outliers.py
semantic_alignment/quote_research_closure.py
semantic_alignment/quote_research_corpus.py
semantic_alignment/quote_research_gemini.py
semantic_alignment/quote_research_retry_analysis.py
semantic_alignment/quote_research_retry_execution.py
semantic_alignment/relation_aware_veto.py
semantic_alignment/scene_grammar_pilot.py
semantic_alignment/schemas.py
semantic_alignment/thatcher_image_hunt.py
semantic_alignment/vertex_recovery.py
semantic_alignment/visualisability_audit.py
tests/test_analyse_counterfactual_simulation_dataset.py
tests/test_first_impression.py
tests/test_first_impression_validation.py
tests/test_gemini_transport_fallback.py
tests/test_generated_image_pool_runway_digest.py
tests/test_generated_image_review_app.py
tests/test_generation_prompt_pilot.py
tests/test_image_provider_trial.py
tests/test_quote_attribution_cleanup.py
tests/test_quote_image_selection_harness.py
tests/test_quote_research_corpus.py
tests/test_relation_aware_veto.py
tests/test_reply_strategy.py
tests/test_semantic_alignment_bakeoff.py
tests/test_semantic_alignment_calibration.py
tests/test_semantic_alignment_pipeline.py
tests/test_semantic_large_bakeoff.py
tests/test_semantic_meta_critic.py
tests/test_unit_helpers.py
tests/test_vertex_gemini_recovery.py
tools/analyse_counterfactual_simulation_dataset.py
tools/backtest_original_editorial_matching.py
tools/generated_image_review_app/app.py
tools/generation_prompt_pilot_review.py
tools/semantic_alignment_calibration_app.py
```

All 76 final file hashes were captured with:

```bash
sha256sum $(git diff --name-only --diff-filter=ACMRTUXB)
```

Key production-source hashes are:

```text
mrsMThatcher2.py  5938ae9329ca533471ea5a02fbb262127574e22432fe6d622c55528c33de9225
reply_strategy.py 7a6bc66e905a6ba8dccd8798450f6392e6161c4533fc9ab0eba053ab78f0ac49
```

## Production-service isolation proof

Before review:

```text
ActiveState=active
SubState=running
MainPID=1075041
child PID=1075042
ExecMainStartTimestamp=Sat 2026-07-18 18:43:35 BST
NRestarts=0
```

After tests and report preparation:

```text
ActiveState=active
SubState=running
MainPID=1075041
child PID=1075042
ExecMainStartTimestamp=Sat 2026-07-18 18:43:35 BST
NRestarts=0
ControlGroup=/user.slice/user-1000.slice/user@1000.service/app.slice/mrsMThatcher.service
```

The cgroup contains exactly the established wrapper and Python child. The
working-tree source is not dynamically reloaded by that installed process.

No X post, service restart or signal, production-state or secret mutation,
engagement-analytics change, deployment, staging, commit or push occurred.

READY FOR SEPARATELY AUTHORISED DEPLOYMENT
