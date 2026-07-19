# Shadow-functionality consolidation deployability report

Date: 19 July 2026
Project: `/disks/disk1/etc/mrsMThatcher`
Scope: deterministic preparation only; no activation, deployment or provider calls

## Executive verdict

The consolidation is ready for a separately controlled commit, push and deployment.

The 65 wording-uncertain quotations remain restored exactly as directed. The regular-post population is 610. Their uncertainty metadata was not rewritten or upgraded. Attribution exclusions and unresolved research records remain ineligible.

The semantic-veto coverage defect has been corrected. Runtime data now distinguishes:

- `allow`;
- `veto`;
- `adjudicated_unknown`;
- `not_adjudicated_missing`.

An incomplete quotation/image matrix can no longer produce a global `no safe image` conclusion. That conclusion now requires all 91 authorised image pairs to have resolved allow/veto decisions and zero allows.

The regenerated candidate remains shadow-only. Nothing in this work changes image eligibility, ranking, random selection, tie-breaking or posting.

## Restored quotation eligibility

| Measure | Result |
|---|---:|
| Canonical source records | 619 |
| Regular-post eligible quotations | 610 |
| Restored wording-uncertain quotations | 65 |
| Source-retained but ineligible records | 9 |
| Attribution tombstones absent from source | 13 |

The nine source-retained exclusions comprise six unresolved research records and these three evidence-backed misattributions rejected by the current attribution predicate:

| Quote ID | Canonical speaker |
|---|---|
| `7f75c4d086fb67b0e54d9d63dbe470dc6f9f929aee00ce4a02d01bbc9c8d4646` | Abi Morgan, spoken by Meryl Streep as Margaret Thatcher |
| `8c70978a89ef43e405dbc7eb0bb9751d9dbe631d63d9834ccf3dfde51a4a971c` | Alexander Dubcek, quoted by Margaret Thatcher |
| `cf7a03be1c6e34efbcfec0cc8010544e2deab777a05cb0193d237814244f5c8e` | Abi Morgan, spoken by Meryl Streep as Margaret Thatcher |

The exact 65 restored IDs are pinned in `tests/fixtures/restored_regular_post_quote_ids.json`. Their metadata remains:

| Wording classification | Count |
|---|---:|
| unverified | 41 |
| composite | 9 |
| variant | 6 |
| paraphrase | 6 |
| excerpt | 2 |
| normalised | 1 |

Research-confidence distribution: 36 high, 14 medium and 15 low. The deterministic digest of the 65 records' uncertainty fields is:

`6a42d5cfdb8f63e48273fdba3973a394217b7899cf626febaa96a47ecb356615`

The complete research packet artefact remained unchanged:

`307b01f0c854ad8e16a50ed399bfa0cbd5f4b8c3d00709bfe02100a289143611`

## Semantic-veto defect and correction

The previous candidate used the absence of a known allow as if it proved that no allowed historical image existed. That inference was invalid because most of the 610 x 91 pair universe had not been adjudicated.

`semantic_pair_coverage()` now reconciles every quotation against the complete authorised 91-image hash set. A quotation has a global no-safe-image result only when:

1. `not_adjudicated_count == 0`;
2. `adjudicated_unknown_count == 0`;
3. `allow_count == 0`.

Otherwise its global flag is either `true` when a known allow exists or `null` when no allow is observed but coverage is incomplete. It is never falsely set to `false` by missing data.

Runtime events and summaries retain the compatible `unknown_unjudged` status while adding explicit adjudication fields. The digest reports incomplete coverage separately and counts a global coverage gap only when `quote_pair_fully_resolved=true`.

## Gorbachev investigation

Quotation ID:

`67eacce6d9e102d4cf8a316451f9b8b9c095fdc6d0cffffb5a2d445e43b3d44d`

Quotation:

> Socialism is nationalisation of the total means of production, distribution and exchange and the planning of that by the central government - and that is what Gorbachev is saying has not worked.

The relevant source-captioned image is:

- image ID: `discovered:62fe0c8d105bf2ad7c95`
- SHA-256: `f271019f2226396d8fdbc5297b968240d9654591b94f65778d7a84d2bc16a63a`
- source-grounded participants: Margaret Thatcher, Mikhail Gorbachev and Raisa Gorbacheva
- source context: meeting at the USSR embassy

The exact quotation/image pair has not been adjudicated. No allow or veto was invented.

| Gorbachev quotation coverage | Count |
|---|---:|
| Allow | 0 |
| Veto | 10 |
| Adjudicated unknown | 27 |
| Not adjudicated/missing | 54 |
| Total authorised images | 91 |

Therefore this is accurately reported as an incomplete coverage gap, not as a quotation with no safe historical image.

## Regenerated manifests

The affected manifests were rebuilt deterministically. The source candidate and runtime candidate hashes were unchanged across two consecutive rebuilds.

| Artefact | SHA-256 |
|---|---|
| `mrsMThatcher.txt` | `10310a9d62c03a87f2c1e55fa10286d1413216b8c0cb34cb0dbe4b3c12f19bee` |
| `active_quote_manifest.json` | `4b32a751e855c78f49d5f75bcb5099d50d74d3777671d535e05cb6fde0d9923d` |
| `runtime_eligible_quote_manifest.json` | `3f0ecdc4dd806edb1398e189fe225c9b7e8e303035c266970af77d3efc68b86b` |
| `attribution_exclusion_tombstones.json` | `8a739bb7fc5161b1e3bbd2b43f4d9196910cd18d2f98e2663d8bb11d183def5f` |
| Source semantic-veto candidate | `7e93a91bae57d2329afefb90f5f29bec42bffe0c2b5a75cbad533b6fdffebb92` |
| Runtime semantic-veto candidate | `594b5fc9fdc1599a5f03c53a09fbcdbccfaa4d2003c019a3aefc070aa13d11f6` |
| `shadow_feature_lifecycle.json` | `a1cb7d6d77680786b4eda21ee01cb1dff6e59398b62164b0d65aa895cb3f1f40` |

Runtime candidate accounting:

| Measure | Result |
|---|---:|
| Quotations | 610 |
| Historical images | 91 |
| Authorised pair universe | 55,510 |
| Resolved pair decisions | 22,066 |
| Allow | 21,938 |
| Veto | 128 |
| Adjudicated unknown | 167 |
| Not adjudicated/missing | 33,277 |
| Quotations with a known allow | 609 |
| Fully adjudicated all-veto quotations | 0 |
| Quotations with incomplete coverage | 610 |
| Incomplete quotation with no observed allow | 1 |

Strict manifest validation passed. Runtime preflight loaded the manifest successfully in shadow mode with an in-memory lookup footprint of approximately 34.7 MB. The checksums manifest reconciles all ten deployment-candidate files with no mismatch.

Existing validation evidence was preserved:

- current production-winner coverage: 100%;
- seasonal-boundary winner coverage: 100%;
- stateful weighted winner coverage: 99.7666%;
- critical relationship-regression failures: 0;
- production-selection invariant failures: 0;
- new AI calls and spend: 0.

## Selection-state compatibility

`lines_used.json` was opened read-only and tested through isolated copied state.

- file SHA-256: `cc16083a35059217c8f6681a537e3de7eb7ba4d0b6109ed9c2fa9307f11b5b09`;
- historical entries: 413;
- entries belonging to the current 610 pool: 398;
- historical/stale IDs: 15, ignored safely;
- current selectable remainder: 212;
- no in-memory or on-disk migration was required.

Temporary-state regressions cover partially completed, almost exhausted and exhausted old cycles. No false exhaustion, repeat, receipt mutation or state migration occurred.

## Shadow lifecycle preservation

| Feature | State | Production effect |
|---|---|---|
| Hybrid reply retrieval | `offline_only` | No production import, worker, queue, embedding call or telemetry |
| Generated-image identity policy | `suspended` | No routine shadow work while generated pool is disabled |
| Quote/image semantic veto | `active_shadow` | Observational and fail-open; enforcement unavailable |
| Original-editorial selector | `active_shadow` | Observational and non-enforcing |

The offline hybrid benchmark and generated-image data/tools remain available. Lifecycle validation and digest reporting remain active. No configuration or production process was reloaded.

## Tests and checks

Focused tests:

```text
python3 -m pytest -q \
  tests/test_quote_attribution_cleanup.py \
  tests/test_quote_image_semantic_veto_shadow.py \
  tests/test_shadow_functionality_consolidation.py \
  tests/test_quote_image_selection_harness.py \
  tests/test_digest_safety_hardening.py

142 passed in 12.05s
```

Complete offline suite:

```text
python3 -m pytest -q
1959 passed, 1 skipped, 3 warnings in 726.16s (0:12:06)
```

The warnings were pre-existing dependency deprecations from Starlette TestClient and BeautifulSoup/lxml. There were no failures.

Compilation passed for:

- `mrsMThatcher2.py`;
- `mrs_log_digest.py`;
- `quote_attribution_cleanup.py`;
- `quote_image_selection_harness.py`;
- `semantic_alignment/quote_image_semantic_veto.py`;
- `semantic_alignment/hybrid_reply_retrieval.py`;
- `shadow_lifecycle.py`.

`git diff --check` passed.

## Production isolation

Before and after implementation and testing:

- systemd wrapper/MainPID: `4025393`;
- Python child PID: `4025394`;
- start time: `Sun 2026-07-19 12:29:24 BST`;
- restart count: `0`;
- state: `active/running`;
- process arrangement: exactly one wrapper and one child.

No service was stopped, restarted, reloaded or signalled. No receipt was pending at the final check. Tests used temporary or read-only state and made no X, AI or provider call.

The independently running production bot completed an ordinary scheduled post during the long test run and consequently updated its own `bot_state.json`. Its PID, start time and restart count did not change. That live-service activity was not caused by the task; this work did not open production state, histories, ledgers or receipts for writing.

No semantic-veto enforcement, generated-image processing, hybrid retrieval or editorial replacement was activated. No commit, push or deployment occurred.

## Modified files

The cumulative consolidation working tree contains changes to:

- `README.md`
- `docs/python_api.md`
- `mrsMThatcher.local.example.json`
- `mrsMThatcher2.py`
- `mrs_log_digest.py`
- `quote_attribution_cleanup.py`
- `quote_image_selection_harness.py`
- `semantic_alignment/hybrid_reply_retrieval.py`
- `semantic_alignment/quote_image_semantic_veto.py`
- `shadow_feature_lifecycle.json`
- `shadow_lifecycle.py`
- `semantic_alignment_research/hybrid_reply_retrieval_001/OFFLINE_BENCHMARK.md`
- deployment-candidate active, eligibility, tombstone, semantic-veto, audit and checksum manifests under `semantic_alignment_research/quote_attribution_cleanup_001/`
- corresponding derived rebuild reports
- focused hybrid, generated-identity, attribution-cleanup, simulator, semantic-veto, research-corpus, reply-strategy and lifecycle tests
- `tests/fixtures/restored_regular_post_quote_ids.json`
- the consolidation and independent-review reports

Before this report, the tracked cumulative diff was 27 files, 917 insertions and 804 deletions. All changes remain unstaged.

## Remaining risk

The semantic-veto matrix is intentionally incomplete: 33,277 historical-photo pairs are not adjudicated and 167 are explicitly unknown. Shadow reporting now exposes that limitation rather than converting it into a false safety conclusion. The sole quotation without an observed allowed pair is therefore observationally unknown, not unsafe.

The candidate must still be installed and activated only through a separately authorised controlled deployment. Active semantic-veto enforcement must remain unavailable.

READY FOR COMMIT, PUSH AND CONTROLLED DEPLOYMENT
