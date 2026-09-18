# Quotation-addition repair validation — 2026-09-18

Repairs defects introduced or exposed by `e438a38cf28e6ddd903b17ba9931ff1c98da6b7e`.
Work was confined to the existing isolated `add-verified-quotes-20260918` worktree.
Tracked files were initially clean; the pre-existing untracked private
`quotation_additions/` directory is excluded from the commit.

## Verified causes and fixes

1. **Manifest occurrences:** preparation used current bot-file line count, although
   the manifest preserves the frozen original corpus's source coordinates. The
   original 632 records already occupy 633 occurrences. Allocation now starts
   after the canonical maximum: the eleven additions use 634–644. The manifest
   has 643 records and 644 occurrences. Validation checks uniqueness, identity,
   positive coordinates, duplicate counts, total counts and canonical hash.
   Regression coverage preserves every original record and tests collisions.
2. **Semantic audit:** preparation rebuilt the ledger and gate pin without
   rebuilding their audit. The audit is now part of the same transaction and
   derives its counts from the corpus. The digest rejects contradictory counts,
   input hashes, internal hashes, identity sets, decisions, ledger or gate pins.
   Tests cover both valid snapshots and deliberately stale or rebound audits.
   Current derived counts are 638 completed, 622 attribution-eligible, 16
   completed ineligible, 13 blocked, 609 eligible/allowed and five unresolved.
   All nine audit input hashes match their files; ledger and gate pins agree.
3. **Stale staging:** old files under a reused staging directory could enter the
   deployment allowlist. Each preparation now builds in a fresh temporary
   overlay, then publishes the complete output set with rollback on failure.
   Tests cover unexpected leftovers, repeatability, generation/publication
   failures and symlink protection.
4. **Unsupported evidence claims:** a file hash and truthy checks previously
   acquired authoritative primary/exact metadata. Validation now checks URL,
   source type, meaningful structured checks, retained passage/context and
   relevant source metadata using HTML/PDF extraction. Explicit reviewed
   variants, excerpts, composites and secondary sources retain their weaker
   classifications. Arbitrary URLs, missing passages, empty checks, false
   attribution and contradictory classifications fail. All eleven retained
   September records passed offline; private inputs were not added to tests.
   PDF layout normalisation preserves substantive hyphen differences.
5. **Git-history dependency:** historical tests archived an unavailable ancestor
   at runtime. A hash-pinned immutable 41-file tar.xz fixture now preserves those
   exact historical contracts without Git. It is 4,598,544 bytes compressed;
   maintenance and extraction protections are documented in
   `tests/fixtures/README.md`. Documentation checks also work without `.git`.
6. **Provenance dates:** reusable code embedded September/July dates, while
   rebuilt status/audit metadata retained old dates. Preparation now requires
   `--batch-timestamp`; supplied precision propagates consistently, with date
   portions used for existing date-only fields. The current authoritative batch
   date is `2026-09-18`. A full repeatable preparation test uses
   `2031-04-09T12:34:56Z`. Generation does not substitute wall-clock time.

The release runbook documents the changed invocation, coordinate system,
evidence requirements, fresh staging and coherent audit output set.

## Generated artifact review

Paths beginning with `corpus/` below refer to
`semantic_alignment_research/quote_research_full_001/`.

| Artifact | Reason for change |
| --- | --- |
| `corpus/corpus_manifest.json` | Eleven occurrence coordinates and dependent input hashes; recalculated manifest hash. |
| `corpus/final_unresolved/final_research_status.json` | Correct generation date only. |
| `corpus/historical_context_source_curated_evidence.json` | Eleven new-source rationales explain validated retained evidence. |
| `corpus/historical_context_source_role_audit.json` | Matching rationale copies, audit date and three dependent input hashes. |
| `historical_context_published_reply_semantic_review.json` | Updated source-role-audit binding only. |
| `historical_context_reply_semantic_gate.py` | Updated expected semantic-ledger hash only. |
| `historical_context_reply_semantic_gate_audit.json` | Current coverage, additions, input bindings, derived invariants and generation date. |

These seven artifacts were regenerated through repository preparation/builders,
using the immutable original base and the retained private batch locally.
Existing private batch inputs and their staging tree were left untouched; that
old staging tree is not a prepared release of these repairs.

Adversarial comparison confirmed that all original 632 manifest records, all
638 research packets and renderings, all historical semantic-review decisions,
the 627 pre-existing gate-audit decisions, and all five unresolved cases are
preserved. `mrsMThatcher.txt`, `quote_analysis.json` and the runtime eligibility
manifest did not change. Private input hashes still match their earlier record.

## Test commands and results

Run in the isolated worktree unless specified otherwise. No tests were skipped
or left unrun in the final complete suite.

Complete suite:

```bash
PYTHONUSERBASE=/home/tonym/.local MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -B -m pytest -q -p xdist.plugin -n 4 --dist=worksteal --max-worker-restart=0
```

Result: **8340 passed, 893 warnings in 715.75s (0:11:55)**.

Affected historical tests in a separate source copy with no `.git` directory
(`/tmp/mrsMThatcher-source-no-git-20260918-ftdcrfd_`), containing the final changes:

```bash
PYTHONUSERBASE=/home/tonym/.local MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -B -m pytest -q -p xdist.plugin -n 4 --dist=worksteal --max-worker-restart=0 tests/test_historical_context_evidence_truth_audit.py tests/test_historical_context_formatter_trial.py tests/test_historical_context_local_corpus_reaudit.py tests/test_historical_context_public_projection_review.py tests/test_historical_context_public_render_review.py tests/test_historical_context_public_source_deduplication.py tests/test_historical_context_reply_semantic_gate_audit.py tests/test_historical_context_transport_url_redaction_transition.py tests/test_historical_corpus_fixture.py tests/test_hybrid_reply_retrieval.py tests/test_image_quote_eligibility.py tests/test_image_quote_shortlist_rerank.py tests/test_openai_quality_trial.py tests/test_prepare_quote_additions.py tests/test_python_documentation.py tests/test_quote_image_metadata_remediation.py tests/test_quote_image_selection_harness.py tests/test_quote_research_closure.py tests/test_relation_aware_veto.py tests/test_visualisability_audit.py
```

Result: **710 passed, 814 warnings in 415.35s (0:06:55)**.

Focused regression run before the final complete suite:

```bash
PYTHONUSERBASE=/home/tonym/.local MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -B -m pytest -q -p xdist.plugin -n 4 --dist=worksteal --max-worker-restart=0 tests/test_prepare_quote_additions.py tests/test_quote_addition_evidence.py tests/test_quote_research_corpus.py tests/test_historical_corpus_fixture.py tests/test_python_documentation.py tests/test_digest_corpus_and_generated_pool.py tests/test_historical_context_reply_semantic_gate_audit.py
```

Result: **117 passed, 20 warnings in 93.10s**. The final three internal-hash
regressions were then included in the digest run below and the complete suite:

```bash
PYTHONUSERBASE=/home/tonym/.local MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -B -m pytest -q tests/test_digest_corpus_and_generated_pool.py
```

Result: **39 passed in 0.48s**.

Static/documentation checks:

```bash
python3 -B tools/check_python_documentation.py
git diff --cached --check
python3 -B - <<'PYTHON'
import subprocess
from pathlib import Path
paths = subprocess.check_output(
    ['git', 'diff', '--cached', '--name-only'], text=True
).splitlines()
for name in paths:
    if name.endswith('.py'):
        compile(Path(name).read_bytes(), name, 'exec')
assert not any(name.startswith('quotation_additions/') for name in paths)
PYTHON
```

Results: documentation coverage passed for **279 modules**; whitespace check
passed; all **19 changed Python files** compiled. Test warnings are dependency
deprecations from PyMuPDF/SWIG and BeautifulSoup/lxml, not failures.

## Operational boundaries

No production files changed; no deployment, push, publication, posting or
external research/model-provider action occurred. No private quotation-addition
inputs or cached evidence are committed. Validation used retained local evidence
and synthetic test inputs. Requested account-usage checks were read-only Codex
`/status` calls; credits remained unchanged during validation.
