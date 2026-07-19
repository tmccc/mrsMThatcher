# Shadow Functionality Consolidation Report

> **Superseded on 19 July 2026:** the operator withdrew the unattended-post
> wording gate. All 610 attribution-eligible quotations are restored to regular
> posting eligibility. The narrower counts and candidate manifests below are
> retained only as historical audit evidence and must not be deployed.

Date: 19 July 2026
Project: `/disks/disk1/etc/mrsMThatcher`
Reviewed commit: `b8c66ec6afbcb8085cea5cac2168cb6956f76f53`

## Executive summary

The consolidation is complete in the working tree and is ready for independent review.

- Hybrid reply retrieval has been removed from the production reply execution path. The unchanged lexical retriever remains authoritative.
- Hybrid index building, deterministic evaluation and local-log replay remain available as explicit offline commands.
- Generated-image identity-policy startup validation, scoring and telemetry now require the generated pool to be enabled. With the pool disabled, the work is suspended.
- Semantic-veto observation remains fail-open, shadow-only and non-enforcing. Its pair decisions and selection behaviour were not changed.
- The original-editorial image shadow implementation was not changed.
- A strict versioned lifecycle register now describes all four shadow features, and the digest renders one compact lifecycle line.
- A deterministic wording-verification gate reduces unattended regular-post eligibility from 610 attribution-eligible quotations to 535. The other 75 remain in the research corpus.
- A wording-filtered, non-live semantic-veto candidate was generated for the same 535 quotations without an AI or network call.
- The exact final tree passed 1,960 tests with one expected skip.

Nothing was activated. The currently running bot still has the previous Python code loaded and was not restarted or signalled.

## Live paths inspected

- `mrsMThatcher2.py`: conversational retrieval, quote eligibility, image scoring and all shadow integration points.
- `reply_strategy.py`: authoritative completed-packet lexical retrieval.
- `semantic_alignment/hybrid_reply_retrieval.py` and `hybrid_reply_retrieval.py`: offline retriever and command-line tooling.
- `semantic_alignment/quote_image_semantic_veto.py`: local manifest validation and shadow lookup.
- `quote_image_selection_harness.py`: isolated selector snapshot and simulation corpus.
- `mrs_log_digest.py`: local shadow and corpus reporting.
- `historical_context_formatter.py`: attribution and uncertainty rendering.
- `semantic_alignment_research/quote_research_full_001/research_packets.json`: 626 completed research packets.
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/`: current 610-quote attribution-cleaned v3 inputs.
- `hybrid_reply_retrieval_runtime/shadow_status.json`, retained structured logs and `digest.md`: historical hybrid evidence.
- `mrsMThatcher.local.json`: only the relevant non-secret feature flags and manifest path were inspected; the file was not modified.
- `/usr/local/bin/runMrsMThatcher2`, the user systemd service and its process tree: service identity and activation status only.

No secret environment file was read, printed or modified.

## Hybrid retrieval evidence

The retained runtime summary contains 50 completed comparisons and no failures:

| Metric | Result |
|---|---:|
| Events / completed / failed | 50 / 50 / 0 |
| Evidence sets changed | 47 |
| Hybrid-only evidence | 0 |
| Lexical-only evidence | 7 |
| No-evidence disagreements | 7 |
| Top-five overlap | 53.06% |
| Latency p50 / p95 / maximum | 297.142 / 396.715 / 639.848 ms |

Recent structured logs and prior digests independently showed the same material result: no hybrid-only evidence rescue. The logs did show lexical-only evidence cases. There is therefore no live evidence that recurring embedding work added unique useful evidence to production decisions.

### Removed from live execution

- The live `reply_strategy.hybrid_retrieval` source-default block and validator.
- The import and call that submitted each conversational retrieval to the hybrid worker.
- Parent/thread shadow-only arguments and retry plumbing.
- The in-process worker thread, queue, lazy live index loader, event writer and bounded live-history writer.
- Routine hybrid status collection and the digest's recurring hybrid telemetry section.

An obsolete ignored local hybrid block is explicitly stripped during local configuration loading. It cannot reintroduce the live feature. A startup warning points to the offline benchmark.

The production path still calls `reply_strategy.retrieve_completed_evidence` with the same incoming contribution and limits. No lexical ranking, threshold, evidence schema, safety validation, reply budget, receipt or posting behaviour was changed.

### Retained offline functionality

The following remain available:

- pinned local E5 model preparation;
- index construction and validation;
- deterministic lexical, semantic and hybrid evaluation;
- local structured-log replay;
- historical review and reporting artefacts;
- corpus/index/status inspection;
- tests for fusion, deduplication, thresholds and deterministic output.

Manual instructions are in `semantic_alignment_research/hybrid_reply_retrieval_001/OFFLINE_BENCHMARK.md`. The principal commands are:

```bash
python3 hybrid_reply_retrieval.py build-index \
  --research-run semantic_alignment_research/quote_research_full_001 \
  --output semantic_alignment_research/hybrid_reply_retrieval_001

python3 hybrid_reply_retrieval.py evaluate \
  --retrieval-dir semantic_alignment_research/hybrid_reply_retrieval_001 \
  --research-run semantic_alignment_research/quote_research_full_001

python3 hybrid_reply_retrieval.py replay \
  --project-dir /disks/disk1/etc/mrsMThatcher \
  --retrieval-dir semantic_alignment_research/hybrid_reply_retrieval_001 \
  --research-run semantic_alignment_research/quote_research_full_001 \
  --since-days 30
```

These commands are manual and offline. They do not feed evidence into the bot.

## Generated identity suspension

The live configuration remains `generated_pool_enabled=false`. The code now treats both identity-policy production scoring and identity-policy shadow scoring as active only when:

```text
generated image pool enabled
AND the respective identity-policy flag enabled
```

Consequences while the pool is disabled:

- no identity manifest startup load or audit;
- no routine policy score calculation;
- no policy shadow event;
- no generated-image eligibility or production change.

When the pool is separately enabled in future, the existing production scoring, identity manifests, simulator profiles and explicit scoring/shadow flags remain available. The change adds no new activation path and does not enable the pool.

## Other image shadows

### Semantic veto

The current v3 historical-photo shadow remains non-enforcing and fail-open. No allow/veto pair decision changed. A pre-existing source-hash mismatch caused by a prior documentation-only formatter edit was repaired deterministically; a before/after comparison proved the pair map and quote flags identical. The refreshed current manifest still contains 610 quotes, 91 images, 22,066 pairs, 21,938 allows and 128 vetoes.

The startup resolver now selects the expected quote-ID set from the configured manifest policy:

- current attribution-cleaned v3 policy: 610 expected IDs;
- new wording-filtered candidate policy: 535 expected IDs.

This permits a later separately authorised manifest switch without weakening validation. It does not switch the current manifest or enforce a veto.

### Original editorial selector

No original-editorial scoring, ranking, telemetry or configuration code was changed. Its lifecycle entry records the already existing `active_shadow` state. It remains observational and non-enforcing.

## Lifecycle register

`shadow_feature_lifecycle.json` is schema version 1 and has SHA-256:

`a1cb7d6d77680786b4eda21ee01cb1dff6e59398b62164b0d65aa895cb3f1f40`

Each entry requires the feature name, purpose, current state, start date, evidence metric, target observation count, promotion criteria, retirement criteria, next decision date and reason. `shadow_lifecycle.py` rejects unknown fields, duplicate features, malformed dates, negative observation targets and states outside:

```text
active_shadow, offline_only, suspended, promoted, retired
```

Initial entries are:

| Feature | State | Evidence basis |
|---|---|---|
| hybrid reply retrieval | `offline_only` | 50 comparisons, zero hybrid-only evidence |
| generated-image identity policy | `suspended` | generated pool disabled |
| quotation/image semantic veto | `active_shadow` | corrected v3 non-enforcing observations |
| original-editorial selector | `active_shadow` | existing observational scoring |

The digest reads and strictly validates this local file, then renders only one compact lifecycle line. Invalid or missing data is reported as unavailable; no provider call is possible.

## Wording-verification policy

Policy version: `regular-post-wording-verification-v1`.

Regular unattended posting eligibility is:

| Research wording status | Regular-post treatment |
|---|---|
| exact | eligible |
| normalised, excerpt or historically verified variant | eligible only with high research confidence and reliable primary evidence |
| unverified | ineligible |
| paraphrase | ineligible |
| composite | ineligible |
| unresolved or unknown wording | ineligible |

Reliable primary evidence is established deterministically from the packet's source class and locator. The policy does not rewrite or upgrade any packet.

### Corpus accounting

`mrsMThatcher.txt` has 620 physical non-empty records and 619 distinct canonical quotations because one pre-existing duplicate is deliberately preserved. Research and eligibility counts are:

| Layer | Count |
|---|---:|
| Completed research packets | 626 |
| Attribution-eligible source quotations | 610 |
| Regular-post wording-eligible quotations | 535 |
| Retained research records excluded only by wording policy | 75 |

The 610 attribution-eligible records comprise 337 exact, 39 normalised, 67 excerpt, 111 variant, 9 composite, 6 paraphrase and 41 unverified records. The gate excludes:

- 41 unverified records;
- 9 composite records;
- 6 paraphrases;
- 19 normalised, excerpt or variant records lacking high-confidence reliable primary evidence.

Five whitespace/punctuation-preserving runtime-to-canonical aliases are retained explicitly.

The regular selector uses the 535-ID set in its initial pool, cycle reset, seasonal fallback and no-repeat paths. Receipt and state keys remain the existing quote hashes. `completed_research_quote_hashes()` remains the 610-record attribution layer for research and historical-context replies.

Uncertain records remain usable by the historical-context formatter. Existing explicit wording such as `Verification: Exact wording not verified` is preserved and covered by regression tests.

## Derived manifests

The isolated candidate directory is:

`semantic_alignment_research/quote_wording_eligibility_001/deployment_candidate/`

| File | Counts | SHA-256 |
|---|---|---|
| `runtime_regular_post_eligibility_manifest.json` | 610 attribution eligible; 535 regular eligible; 75 excluded | `6341e5da6d728bcff91314861ea663c5a4d0e84ff20c90c80a20ec95291483c6` |
| `material_veto_v3_wording_verified_shadow_manifest.json` | 535 quotes; 91 images; 19,399 pairs; 19,307 allow; 92 veto | `71db41180f73a1482c37602bff10c418719db16e5bd194bc5b090ea97e783ffb` |

The filtered veto candidate contains no unknown pair and has at least one allowed image for 534 of 535 quotations. One quotation has no known allowed historical image. It remains non-enforcing (`live_production_enabled=false`). The build reused existing decisions only, made zero AI calls and made zero network calls.

The source 610-quote v3 manifest was refreshed only to align immutable source hashes. Its new SHA-256 is `7016458cf516487f987188c73331b7d9af2f3e2518988c3b995785745cfd0170`; all 22,066 pair decisions are byte-equivalent as structured values to the prior manifest.

## Simulator and alignment changes

The offline harness now records both layers:

- 610 attribution-eligible researched quotations for historical research accounting;
- 535 wording-eligible quotations for simulated regular posting.

Its snapshots include the wording policy code and filtered manifest hashes. The same production selector function supplies candidate eligibility, so sweep and Monte Carlo modes cannot silently reintroduce the 75 excluded records. Generated-image profiles remain separate and generated images remain outside the historical-photo semantic-veto corpus.

The semantic-manifest validator recognises only the existing 610-quote v3 policy and the new strict 535-quote wording-filtered policy, each with exact counts and source provenance. Invalid counts, quote IDs, decisions or policy names fail closed.

## Tests and checks

Final commands and results:

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_shadow_functionality_consolidation.py \
  tests/test_quote_image_semantic_veto_shadow.py
50 passed in 5.62s

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
1960 passed, 1 skipped, 3 warnings in 770.96s

python3 -m py_compile quote_eligibility.py shadow_lifecycle.py \
  mrsMThatcher2.py mrs_log_digest.py quote_image_selection_harness.py \
  semantic_alignment/*.py tools/*.py
passed

ruff check --select F quote_eligibility.py shadow_lifecycle.py \
  mrsMThatcher2.py mrs_log_digest.py quote_image_selection_harness.py \
  semantic_alignment/hybrid_reply_retrieval.py \
  semantic_alignment/quote_image_semantic_veto.py \
  tests/test_shadow_functionality_consolidation.py
passed

git diff --check
passed
```

An earlier full run exposed one load-sensitive test timeout in `test_completed_worker_slot_refills_while_other_call_is_slow`: the test waited only one second for a deliberately blocked worker during heavy filesystem activity. The exact test passed alone. Its bounded wait was increased to five seconds without changing production code. The complete research-corpus file then passed, and two subsequent complete runs were clean. The final run above tests the exact final tree.

Regression coverage proves:

- the production reply path imports and calls no hybrid worker, embedder or queue;
- the offline hybrid fusion and evaluation remain deterministic;
- lexical production evidence is unchanged;
- generated identity work is skipped when the pool is disabled and behaves as before when enabled;
- semantic veto and original-editorial observation cannot change a selection;
- lifecycle validation fails closed;
- exact and qualifying verified variants remain eligible;
- unverified, paraphrase and unsupported variants cannot enter any regular-post selection phase;
- uncertain historical-context rendering remains available and explicit;
- the runtime and semantic manifests contain exactly 535 eligible quote IDs;
- no-repeat, seasonal, receipt, attribution and stateful-selection invariants pass.

## Modified files

Tracked modifications:

- `README.md`
- `docs/python_api.md`
- `mrsMThatcher.local.example.json`
- `mrsMThatcher2.py`
- `mrs_log_digest.py`
- `quote_image_selection_harness.py`
- `semantic_alignment/hybrid_reply_retrieval.py`
- `semantic_alignment/quote_image_semantic_veto.py`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/material_veto_v3_shadow_manifest.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/runtime_eligible_quote_manifest.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/v3_shadow_manifest_audit.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/v3_shadow_manifest_audit.md`
- `tests/test_generated_identity_policy_production_scoring.py`
- `tests/test_generated_identity_policy_shadow_scoring.py`
- `tests/test_hybrid_reply_retrieval.py`
- `tests/test_quote_image_selection_harness.py`
- `tests/test_quote_research_corpus.py`
- `tests/test_reply_strategy.py`
- `tests/test_unit_helpers.py`

New files:

- `quote_eligibility.py`
- `shadow_lifecycle.py`
- `shadow_feature_lifecycle.json`
- `semantic_alignment_research/hybrid_reply_retrieval_001/OFFLINE_BENCHMARK.md`
- `semantic_alignment_research/quote_wording_eligibility_001/deployment_candidate/manifest_audit.json`
- `semantic_alignment_research/quote_wording_eligibility_001/deployment_candidate/manifest_audit.md`
- `semantic_alignment_research/quote_wording_eligibility_001/deployment_candidate/material_veto_v3_wording_verified_shadow_manifest.json`
- `semantic_alignment_research/quote_wording_eligibility_001/deployment_candidate/runtime_regular_post_eligibility_manifest.json`
- `tests/test_shadow_functionality_consolidation.py`
- `shadow_functionality_consolidation_report.md`

Before adding untracked files, the tracked diff was 19 files with 478 insertions and 743 deletions. No file was staged.

## Production-isolation proof

Before work and after all tests, `mrsMThatcher.service` reported exactly:

```text
MainPID=4025393
child PID=4025394
ExecMainStartTimestamp=Sun 2026-07-19 12:29:24 BST
NRestarts=0
ActiveState=active
SubState=running
```

The process tree remained one wrapper (`/usr/local/bin/runMrsMThatcher2`) and one Python child (`/usr/local/bin/mrsMThatcher2.py`) in the same service cgroup. There was no restart, signal or dynamic source reload.

All stateful tests used temporary paths or fixtures. No command invoked the bot, X, a provider, a network service, a posting command or a production-state migration. No production state, receipt, ledger, posting history, ignored local configuration or secret was written.

## Remaining risks and activation boundary

1. The running Python process predates these changes. Until a separately authorised controlled restart, it may continue the old live hybrid shadow work and does not enforce the new wording gate.
2. The ignored local configuration still contains a legacy hybrid block. New code ignores it deliberately; removing it can be part of a later controlled deployment, but it was not mutated here.
3. The currently configured semantic-veto policy remains the 610-quote v3 manifest. The 535-quote wording-filtered file is an isolated deployment candidate and was not selected or loaded live.
4. One of the 535 wording-eligible quotations has no allowed historical-image pair in the candidate semantic manifest. Because semantic veto remains shadow-only, this is an observation, not a posting failure or selection change.
5. Generated identity processing will resume only if the generated pool and its respective scoring/shadow flag are both separately enabled. Such activation requires its own review.

## Explicit confirmation

There was no posting, service restart or signal, production-state mutation, generated-image activation, semantic-veto enforcement, original-editorial activation, hybrid-retrieval activation, AI/provider call, commit, push or deployment.

READY FOR INDEPENDENT REVIEW
