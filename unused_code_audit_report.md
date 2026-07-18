# Unused Code Audit

Date: 18 July 2026

## Verdict

The audit removed high-confidence dead code without changing an active production
algorithm or command. The working tree is 506 lines smaller overall: 557 lines
were deleted and 51 were added or split while cleaning existing statements.

No whole standalone research command was removed merely because it is not
imported. Those files are explicit CLI entry points and may be needed to reproduce
historical research runs.

## Scope And Method

The audit covered 182 tracked Python files, initially containing 104,057 lines.
It used:

- repository-wide import, symbol, documentation and test references;
- AST parsing of every tracked Python module;
- Ruff `F401`, `F811`, `F821` and `F841` checks;
- direct inspection of production entry points and dynamic CLI factories;
- Git history for apparently orphaned safety and migration functions;
- focused and full offline test suites.

An unimported module was not considered dead when it had a `__main__` entry point,
documented server factory, migration purpose or research-reproduction role.

## Production Code Removed

The following uncalled compatibility paths were removed from `mrsMThatcher2.py`:

- `quote_metadata_for_line`: obsolete line-index lookup superseded by stable
  quote-hash lookup;
- `choose_unused_line` and `choose_unused_image`: tuple-returning wrappers used
  only by their tests;
- `choose_random_unused_image` and `available_image_basenames`: the abandoned
  random image path superseded by the scored, currently-eligible candidate path;
- `block_if_unresolved_main_post_receipt` and
  `block_if_unresolved_meme_post_receipt`: uncalled wrappers superseded by the
  active reconciliation barrier and regular-post pre-write barrier.

The active functions for hash-based quotation metadata, structured quotation
selection, scored image selection, receipt reconciliation, duplicate protection
and durable state remain unchanged.

The following were removed from `reply_strategy.py`:

- the duplicate `DEFAULT_REPLY_STRATEGY` structure;
- `validate_reply_strategy_config`, a second validator referenced only by its own
  test while production uses the stricter validator in `mrsMThatcher2.py`;
- `reply_is_repetitive`, a Boolean wrapper referenced only by a test. Production
  continues to use `reply_repetition_reason`, which preserves diagnostic detail.

## Support Code Removed

Uncalled helpers removed from offline support modules were:

- `file_identity` and `markdown_escape` from metadata remediation;
- `mark_interrupted_sending_ambiguous` from the old Gemini fallback workflow;
- `write_retry_validation_report`, an unwired 117-line legacy report generator;
- `_sha256_directory` and `_normal_terms` from relation-aware veto research;
- `_title_key` from image discovery.

Ruff-backed mechanical cleanup also removed:

- 106 unused imports;
- 15 unused local assignments.

The assignment cleanup retained any expression with an active side effect. No
provider request, state mutation or selector call was removed as an unused local.

## Deliberately Retained

Two top-level functions have no ordinary in-repository caller but are not dead:

- `tools.generated_image_review.app.configured_app` is the documented Uvicorn
  factory used by the local review command;
- `mrs_engagement_analytics.revise_snapshots_from_preserved_raw` is a tested,
  append-only repair primitive for preserved metric responses. Removing it would
  discard a data-recovery capability rather than routine dead code.

Thirty standalone research and recovery scripts are not imported by another
module or mentioned in the main README. They were retained because each has an
executable entry point and many correspond to immutable research artefacts. A
future removal should begin with an explicit deprecation manifest mapping each
script to its completed run and replacement, rather than deleting them from a
static-import result alone.

## Validation

- Focused affected suites: `750 passed`.
- Full repository suite: `1780 passed, 1 skipped` in 691.50 seconds.
- Warnings: one existing Starlette/httpx deprecation and two existing Beautiful
  Soup/lxml deprecations.
- `py_compile` for every modified Python file: passed.
- Ruff unused import/name/local checks for every tracked Python file: passed.
- `git diff --check`: passed.
- Removed-symbol repository search: no remaining references.

The reduced suite count reflects deletion of six tests whose only purpose was to
exercise the deleted compatibility wrappers and duplicate validator. Coverage of
the current structured selectors, current eligible-image cycle, hash-based quote
metadata and production configuration validation remains in place.

## Production Isolation

The bot service was not restarted, reloaded or signalled. Its state remained:

- active/running;
- MainPID `1075041`;
- start time `Sat 2026-07-18 18:43:35 BST`;
- systemd restart count `0`.

No X call, provider call, production-state write, receipt change, commit, push or
deployment was performed. The running Python process therefore continues using
the code it loaded before this audit. These source changes would require a
separately authorised commit and controlled restart to become active.

## Diff Summary

```text
68 files changed, 51 insertions(+), 557 deletions(-)
Tracked Python lines before: 104,057
Tracked Python lines after:  103,551
Net reduction:                  506
```

Most of the 68 touched files contain only removal of an unused import or local
assignment. The substantive deletions are concentrated in `mrsMThatcher2.py`,
`reply_strategy.py`, `quote_image_metadata_remediation.py`,
`semantic_alignment/gemini_fallback.py`,
`semantic_alignment/quote_research_retry_execution.py`,
`semantic_alignment/relation_aware_veto.py` and
`semantic_alignment/thatcher_image_hunt.py`.
