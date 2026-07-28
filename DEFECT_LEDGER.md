# MrsMThatcher defect ledger

This is the deterministic human-readable projection of
`defect_ledger.json`, which is the source of truth. Records are sorted by
`id`; commit references are rendered as their first eight hexadecimal
characters unless the JSON value is the literal `unknown`. No status,
chronology, test, fix, deployment claim, or residual-risk conclusion should be
maintained independently in this file.

Purpose: durable current-master inventory for the ten 28 July diagnosis
findings and directly related established gaps.

## Baseline

- Current master: `be882e8121a7b4348a57b61b1cf526401a36f5c0`
- Current master tree: `7965dbb935f2a9f993d14aa37d93283e16bc298a`
- Observed production: `be882e8121a7b4348a57b61b1cf526401a36f5c0`
- Production observation: `2026-07-28T23:40:14+01:00`
- Observation source: the authoritative
  `production_deployments/20260728T204234Z-production-hardening` validation and
  deployment report, plus a fresh read-only Git, installed-file hash and
  process preflight.
- Installed SHA-256: `mrsMThatcher2.py`
  `0f72fd3d5e63995d150e90d5875abedd65a1ee14cfe3467f7a555771dd23e775`;
  wrapper
  `524d28b24c76343147815c340f9d656f12d7ef2cba7cde18110a24e120e039dd`.
  Both match the `be882e81` production checkout.
- Process observation: wrapper PID 3945677 and child PID 3945679 have run
  since 21:46:22 with `NRestarts=0`.
- Freshness warning: production is mutable. Recheck the deployed commit, exact
  installed hashes and loaded child before relying on any deployment status.

## Status taxonomy

- `active`: the defect is present on current master and its affected path or
  control is currently usable; no verified repair is recorded.
- `latent-disabled`: the unsafe implementation is present in the observed
  deployed or current-master path, but the affected optional feature or
  enforcement mode is documented as disabled; enabling it requires closure
  first.
- `repaired-not-deployed`: current master contains a reviewed regression and
  repair, but the latest recorded production observation predates that repair.
- `deployed-unverified`: the repair is present in the observed production
  commit, but recorded post-deployment evidence does not exercise enough
  defect-specific behavior to call it verified.
- `deployed-verified`: the repair is present in the observed production commit
  and post-deployment evidence verifies the relevant behavior, not merely
  commit ancestry.
- `assurance-weakness`: the record concerns test, fixture, review, release or
  containment evidence rather than a demonstrated bot-runtime misbehavior.
- `superseded`: the historical umbrella or formulation is retained for
  traceability but replaced by the more precise records named in
  `superseded_by`.

The distinction between the two `deployed-verified` and seven
`deployed-unverified` repairs is intentional. Exact installed hashes and a
green deployment do not by themselves prove every defect-specific behavior.
Receipt replay, adversarial local-config rejection, live HTTP budgeting,
generated-pool classification, a two-window digest resume, installed-unit fast
failure and unknown pause-like control rejection were not exercised after
deployment.

## Diagnosis coverage

| Diagnosis finding | Ledger record |
|---|---|
| Ordinary quotation eligibility | `DEF-0001` |
| Historical-context gate | `DEF-0002` |
| Confirmed-post receipt recovery | `DEF-0003` |
| Launcher supervision | `DEF-0004` |
| Runtime configuration and control | `DEF-0005`, split into `DEF-0006` and `DEF-0007` |
| Request timeouts | `DEF-0008` |
| Generated image classification | `DEF-0009` |
| Digest accounting | `DEF-0010` |
| Test isolation | `DEF-0011` |
| Test fixtures | `DEF-0012` |

`DEF-0013` through `DEF-0017` are directly related, established gaps that
remain relevant at current master.

## Scope and incident classification

The explicit scope fields below project `defect_class`, `affected_files`, `runtime_lanes` and `incident` from each JSON record. “Incident unknown” means the defect or gap is established, but available production history neither proves nor disproves a live occurrence.

| ID | Defect class | Affected files / control paths | Runtime lanes | Incident |
|---|---|---|---|---|
| `DEF-0001` | runtime-defect | `mrsMThatcher2.py`; `historical_context_formatter.py`; `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/runtime_eligible_quote_manifest.json` | `ordinary-post` | unknown — No complete production selection history proves or disproves that a drifted partition affected a live post. Evidence: The 2026-07-28 review reproduced the integrity failure against e5cd1ef3 fixtures. |
| `DEF-0002` | runtime-defect | `historical_context_reply_semantic_gate.py`; `historical_context_formatter.py`; `mrsMThatcher2.py` | `historical-context-reply` | unknown — The evidence establishes a reachable defect but does not establish whether a drifted allowed reply was published in production. Evidence: The review reproduced correction-byte and allowed-render drift against e5cd1ef3. |
| `DEF-0003` | runtime-defect | `mrsMThatcher2.py`; `tests/test_unit_helpers.py` | `ordinary-post-recovery` | unknown — No observed production replay exercised the defective cycle-reset case, so occurrence cannot be inferred. Evidence: A schema-v1 replay reproducer retained stale pre-reset histories; deployment validation recorded receipt_replay_observed=false. |
| `DEF-0004` | runtime-defect | `runMrsMThatcher2`; `deploy/systemd-user/mrsMThatcher.service`; `tests/test_integration_harness.py` | `cross-cutting-process-supervision` | unknown — The review did not recover historical service evidence proving that a hidden permanent child failure occurred. Evidence: The old wrapper behavior was reproduced with fast-failing fake children; the current deployment has NRestarts=0. |
| `DEF-0005` | historical-umbrella | `mrsMThatcher2.py`; `mrsMThatcher.local.json`; `mrsMThatcher.control.json` | `cross-cutting-configuration-and-control` | unknown — The umbrella has no independent incident evidence; occurrence is tracked, and remains unknown, in its child records. Evidence: DEF-0006 and DEF-0007 reproduce the two distinct misleading-key behaviors. |
| `DEF-0006` | runtime-defect | `mrsMThatcher2.py`; `mrsMThatcher.local.json` | `startup-configuration` | unknown — No complete archive of production local-config inputs establishes whether an operator typo triggered the defect. Evidence: Adversarial config tests show an unknown key could coexist with supported changes on e5cd1ef3. |
| `DEF-0007` | runtime-defect | `mrsMThatcher2.py`; `mrsMThatcher.control.json` | `cross-cutting-runtime-control` | unknown — The available production control evidence does not establish that an operator used a misleading typo. Evidence: The review reproduced disable_alll validating while the global pause remained false. |
| `DEF-0008` | runtime-defect | `mrsMThatcher2.py`; `deploy/systemd-user/mrsMThatcher.service` | `cross-cutting-http-clients` | unknown — No production request trace proves that the excessive aggregate wait occurred. Evidence: Boundary tests reproduce acceptance of scalar timeouts beyond the intended service budget. |
| `DEF-0009` | latent-runtime-defect | `mrsMThatcher2.py`; `generated_review_approved_images` | `ordinary-post-generated-image-selection` | unknown — The current observation excludes an active incident but does not establish the feature's complete historical activation state. Evidence: The unsafe fallback was reproduced, while the observed affected deployment had ENABLE_GENERATED_IMAGE_POOL=false. |
| `DEF-0010` | runtime-defect | `mrs_log_digest.py`; `tests/test_digest_safety_hardening.py`; `tests/test_digest_reply_observability.py` | `offline-digest-analysis` | unknown — No authoritative comparison against every historical provider invoice establishes whether a report understated a real charge. Evidence: A two-window resume test reproduces lost in-flight call attribution on e5cd1ef3. |
| `DEF-0011` | assurance-weakness | `tests/conftest.py`; `tests/test_pytest_safety_bootstrap.py` | `test-suite` | false — Unsafe inheritance was reproduced in test collection; that is an assurance failure, not a production incident. Evidence: The record is explicitly a development-assurance control weakness and does not assert a production bot action. |
| `DEF-0012` | assurance-weakness | `tests/test_integration_harness.py`; `tests/test_simulate_regular_post_futures.py`; `tools/simulate_regular_post_futures.py` | `test-fixtures-and-simulators` | false — Fixture drift is classified as an assurance weakness rather than a production incident. Evidence: The strengthened loader broke incomplete test fixtures; no production runtime misbehavior is asserted. |
| `DEF-0013` | assurance-weakness | `tests/conftest.py`; `tests/test_*.py` | `test-subprocess-containment` | false — The assurance gap exists, but the ledger has no test-egress incident claim. Evidence: This record documents an absent test-host containment layer; no production bot action is attributed to it. |
| `DEF-0014` | assurance-weakness | `docs/historical_context_evidence_release_runbook.md`; `semantic_alignment_research/**/deployment_candidate/*.json` | `release-and-deployment` | false — Mutable-candidate exposure is an assurance weakness; no specific mixed-tree deployment incident is claimed here. Evidence: The record identifies an absent release control and does not attribute a production failure to one release. |
| `DEF-0015` | runtime-defect | `mrsMThatcher2.py`; `runMrsMThatcher2`; `deploy/systemd-user/mrsMThatcher.service` | `cross-cutting-process-supervision` | unknown — The present bytes match; historical disk/process generation mismatches cannot be ruled in or out from current health evidence. Evidence: Fresh preflight matched current installed source and wrapper hashes, but the application still emits no loaded-generation identity. |
| `DEF-0016` | latent-runtime-defect | `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/material_veto_v3_shadow_manifest.json`; `historical_context_formatter.py`; `mrsMThatcher2.py` | `ordinary-post-semantic-veto` | unknown — Disabled enforcement excludes a current enforcement incident but does not establish the complete history of manifest invalidations. Evidence: The broad dependency pin is established, while production validation reports semantic_veto_enforcement_active=false. |
| `DEF-0017` | runtime-defect | `mrsMThatcher2.py`; `mrsMThatcher.local.json`; `mrsMThatcher.control.json`; `bot_state.json`; `regular_post_receipt.json`; `meme_post_receipt.json`; `confirmed_reply_receipt.json`; `historical_context_reply_receipt.json` | `cross-cutting-json-safety-documents` | unknown — No complete archive of production safety documents establishes whether a duplicate name altered live behavior. Evidence: Default json.load behavior reproduces last-wins parsing and remains present at be882e81. |

## Chronology projection

This table projects every `chronology` event from `defect_ledger.json`; it is generated rather than maintained as a second chronology.

| ID | Date | Commit | Event | Evidence |
|---|---|---|---|---|
| `DEF-0001` | 2026-07-18 | `fe8f7713` | Packet-derived ordinary eligibility entered the runtime. | Commit subject: Remove misattributed quotes and add offline selection audits. |
| `DEF-0001` | 2026-07-28 | `e5cd1ef3` | Independent hardening review reproduced stale and coherently rehashed drift. | why_code_reviews_continue_to_find_major_problems.md and the new runtime eligibility tests. |
| `DEF-0001` | 2026-07-28 | `be882e81` | Exact partition and input binding repair committed. | Harden production integrity and recovery paths. |
| `DEF-0001` | 2026-07-28 | `be882e81` | Repair deployed and verified against the production artifact generation. | EXT-DEPLOY-FINAL-VALIDATION records successful runtime bootstrap and 611 eligible quotations; fresh installed-file hashes match be882e81. |
| `DEF-0002` | 2026-07-22 | `f04a3c72` | Ledger/projection semantic gate introduced. | Commit subject: Gate unresolved historical-context replies. |
| `DEF-0002` | 2026-07-28 | `e5cd1ef3` | Review reproduced correction drift and allowed-render drift. | New adversarial semantic-gate regressions. |
| `DEF-0002` | 2026-07-28 | `be882e81` | Current rendering and correction hash binding committed. | Gate now loads hash-bound corrections and reproduces reviewed allowed renderings. |
| `DEF-0002` | 2026-07-28 | `be882e81` | Repair deployed and the production historical-context gate loaded successfully. | EXT-DEPLOY-FINAL-VALIDATION reports runtime bootstrap success, gate available and 13 blocked entries; installed source matches be882e81. |
| `DEF-0003` | 2026-07-06 | `9ae7e0f2` | Regular quote/image receipt recovery introduced with additive history replay. | Commit subject: Harden quote image receipt recovery. |
| `DEF-0003` | 2026-07-27 | `acfc4f69` | Confirmed-post durability repair made receipt/fallback recovery stricter without encoding exact cycle after-state. | Confirmed post fail-closed hardening retained schema_version 1. |
| `DEF-0003` | 2026-07-28 | `be882e81` | Schema v2 authoritative history repair committed. | Receipt now stores quote_history_after and image_history_after. |
| `DEF-0003` | 2026-07-28 | `be882e81` | Repair deployed without a post-deployment receipt-replay exercise. | EXT-DEPLOY-FINAL-VALIDATION identifies be882e81 and explicitly records receipt_replay_observed=false. |
| `DEF-0004` | 2026-07-05 | `2179d71e` | Restart-forever wrapper with child output redirected to /dev/null introduced. | Git -S history for "$BOT_SCRIPT" >/dev/null 2>&1. |
| `DEF-0004` | 2026-07-28 | `e5cd1ef3` | Independent runtime review found hidden crash-loop and env-file ordering defects. | Diagnosis and direct launcher subprocess reproductions. |
| `DEF-0004` | 2026-07-28 | `be882e81` | Visible child output, effective-value validation and crash-loop breaker committed. | runMrsMThatcher2 and launcher integration regressions. |
| `DEF-0004` | 2026-07-28 | `be882e81` | Launcher repair deployed; stable operation observed without exercising the repaired fast-failure behavior. | Installed wrapper matches be882e81; EXT-DEPLOY-FINAL-VALIDATION records stable PIDs for 316 seconds, NRestarts=0, no startup errors and no tracebacks, but no installed-unit fast-failure exercise. |
| `DEF-0005` | 2026-07-28 | `be882e81` | Umbrella diagnosis finding recorded. | why_code_reviews_continue_to_find_major_problems.md groups runtime configuration and control. |
| `DEF-0005` | 2026-07-28 | unknown | Umbrella superseded by atomic DEF-0006 and DEF-0007 records. | Defect ledger modelling decision; no code commit is applicable. |
| `DEF-0006` | 2026-07-01 | `36edf036` | Initial local-config implementation included ignore-unknown behavior. | Git -S history for def apply_local_config. |
| `DEF-0006` | 2026-07-28 | `be882e81` | Unknown-key atomicity regression and repair committed. | Unsupported keys now raise LocalConfigError before any proposal applies. |
| `DEF-0006` | 2026-07-28 | `be882e81` | Repair deployed but adversarial unknown-key rejection was not exercised post-deployment. | Installed source matches be882e81 and runtime bootstrap passed; deployment evidence contains no unknown-key trial. |
| `DEF-0007` | 2026-07-11 | `4a4afb4f` | Permissive runtime-control validation introduced. | Commit subject: Harden production safety and digest reporting. |
| `DEF-0007` | 2026-07-28 | `e5cd1ef3` | Typos disable_alll, pause_quote_post and disable_all_until_typo reproduced. | New parameterized fail-closed regression. |
| `DEF-0007` | 2026-07-28 | `be882e81` | Explicit control allowlist repair committed. | CONTROL_ALLOWED_KEYS and documented generation metadata. |
| `DEF-0007` | 2026-07-28 | `be882e81` | Repair deployed; a valid global pause was acknowledged without exercising rejection of an unknown pause-like key. | Deployment acknowledged a valid global pause, restored original control bytes and observed an idle scheduler cycle without an X write; it did not submit an unknown pause-like key. |
| `DEF-0008` | 2026-07-02 | `b0c4f823` | Configurable scalar request timeout introduced. | Commit subject: Validate runtime limits and quote errors. |
| `DEF-0008` | 2026-07-28 | `be882e81` | Service-budget comparison and combined-timeout tests added with repair. | Adds MAX_REQUEST_TIMEOUT_SECONDS=60 and urllib3 Timeout(total=..., connect<=10). |
| `DEF-0008` | 2026-07-28 | `be882e81` | Repair deployed without a post-deployment HTTP-budget exercise. | Installed source matches be882e81; EXT-DEPLOY-FINAL-VALIDATION records deployment_process_x_actions=0. |
| `DEF-0009` | 2026-07-08 | `d1026728` | Optional generated image pool admitted every in-directory glob match. | Commit subject: Add generated image pool observability. |
| `DEF-0009` | 2026-07-28 | `be882e81` | Unclassifiable reviewed_ai.png reproduction and fail-closed repair committed. | test_generated_image_pool_fails_closed_on_unclassifiable_basename. |
| `DEF-0009` | 2026-07-28 | `be882e81` | Repair deployed while the optional generated pool remained disabled. | Installed source matches be882e81; EXT-DEPLOY-FINAL-VALIDATION records generated_image_pool_changed=false. |
| `DEF-0010` | 2026-07-28 | `e5cd1ef3` | Conversational AI cost report introduced without resumable in-flight call attempts. | Commit subject: Report conversational AI reply costs clearly. |
| `DEF-0010` | 2026-07-28 | `be882e81` | Cross-window reproducer, unmatched-usage classification and resume repair committed. | Digest now persists last_active_xai_call_attempt. |
| `DEF-0010` | 2026-07-28 | `be882e81` | Repair deployed without an operational two-window provider-call observation. | Installed digest source is be882e81; the deployment record contains no digest resume/cost event. |
| `DEF-0011` | 2026-07-01 | `36edf036` | Initial bot and integration tests entered without a pre-collection global isolation bootstrap. | Initial bot source and integration harness. |
| `DEF-0011` | 2026-07-27 | `370aee9b` | Earlier suite review explicitly recommended a suite-wide non-loopback guard. | bot_and_test_suite_review_report.md#test-isolation-work-still-needed. |
| `DEF-0011` | 2026-07-28 | `be882e81` | Pre-collection environment bootstrap and in-process socket guard committed. | tests/conftest.py grew process-local defaults and socket interception. |
| `DEF-0012` | 2026-07-18 | `fe8f7713` | Production selection gained packet-derived eligibility while minimal fixtures remained partial. | Runtime and fixture histories diverged in represented integrity relationships. |
| `DEF-0012` | 2026-07-28 | `be882e81` | Stronger production loader initially broke partial fixtures, exposing the assurance gap. | Fixture builders gained corpus_manifest, final_research_status and runtime_eligible_quote_manifest. |
| `DEF-0013` | 2026-07-27 | `370aee9b` | Suite-wide network isolation recorded as unfinished work. | Bot and test-suite review. |
| `DEF-0013` | 2026-07-28 | `be882e81` | In-process guard landed; OS-level subprocess gap explicitly remained. | Diagnosis Priority 2 item 16. |
| `DEF-0014` | 2026-07-28 | `be882e81` | Diagnosis identified mutable shared candidate files and prose-only release checks as an assurance gap. | Priority 0 item 3 and worktree/deployment complexity section. |
| `DEF-0015` | 2026-07-05 | `2179d71e` | Production wrapper introduced without loaded generation identity. | Launcher history. |
| `DEF-0015` | 2026-07-28 | `be882e81` | Current diagnosis made loaded commit/hash generation a first-class process-health requirement. | Priority 2 item 15 and immutable deployment recommendations. |
| `DEF-0016` | 2026-07-19 | `2220df1e` | Material veto manifest began pinning historical_context_formatter.py as attribution_predicate. | Commit subject: Fix adversarial review findings. |
| `DEF-0016` | 2026-07-28 | `be882e81` | Diagnosis recorded repeated shadow-manifest pin refreshes as a dependency-boundary smell. | Generated artefacts act like code section. |
| `DEF-0017` | 2026-07-01 | `36edf036` | Initial safety-document parsers used the default duplicate-name behavior. | Initial bot source. |
| `DEF-0017` | 2026-07-27 | `370aee9b` | Independent bot/test review recorded duplicate JSON keys as an unresolved hardening gap. | bot_and_test_suite_review_report.md:190. |
| `DEF-0017` | 2026-07-28 | `be882e81` | be882e81 repaired unknown names but retained default JSON duplicate-name parsing. | apply_local_config and load_control still call json.load without an object_pairs_hook. |

## Summary

| ID | Status | Severity | Finding | Invariants | Introduced | Fix | Deployment |
|---|---|---|---|---|---|---|---|
| `DEF-0001` | deployed-verified | high | Ordinary eligibility was not bound to the reviewed exact partition | `INV-ELIG-001`, `INV-ELIG-002`, `INV-ELIG-003`, `INV-ART-001` | `fe8f7713` | `be882e81` | deployed-verified; observed `be882e81` |
| `DEF-0002` | deployed-verified | high | Historical-context gate did not bind current rendered replies and correction bytes | `INV-HCTX-001`, `INV-ELIG-002`, `INV-ART-001`, `INV-TXN-HCTX-001` | `f04a3c72` | `be882e81` | deployed-verified; observed `be882e81` |
| `DEF-0003` | deployed-unverified | high | Regular-post receipt could not reproduce cycle-reset histories exactly | `INV-TXN-REG-001`, `INV-TXN-HIST-001`, `INV-TXN-RECEIPT-001` | `9ae7e0f2` | `be882e81` | deployed-unverified; observed `be882e81` |
| `DEF-0004` | deployed-unverified | high | Launcher could hide a permanently failing child behind a healthy wrapper | `INV-PROC-001`, `INV-PROC-002`, `INV-PROC-003` | `2179d71e` | `be882e81` | deployed-unverified; observed `be882e81` |
| `DEF-0005` | superseded | high | Runtime configuration and control accepted misleading unknown keys | `INV-CONFIG-001`, `INV-PAUSE-001` | unknown | not-applicable | tracked by `DEF-0006` and `DEF-0007` |
| `DEF-0006` | deployed-unverified | high | Unknown local configuration keys were ignored instead of rejecting the transaction | `INV-CONFIG-001` | `36edf036` | `be882e81` | deployed-unverified; observed `be882e81` |
| `DEF-0007` | deployed-unverified | high | Pause-like runtime-control typos validated without enforcing the intended pause | `INV-PAUSE-001` | `4a4afb4f` | `be882e81` | deployed-unverified; observed `be882e81` |
| `DEF-0008` | deployed-unverified | medium | HTTP timeouts were not one bounded total request budget | `INV-API-001` | `b0c4f823`, bounded | `be882e81` | deployed-unverified; observed `be882e81` |
| `DEF-0009` | deployed-unverified | medium | Generated image pool accepted an in-directory unclassifiable basename | `INV-ART-001` | `d1026728` | `be882e81` | deployed-unverified; observed `be882e81` |
| `DEF-0010` | deployed-unverified | medium | Digest resume could lose an in-flight provider-call attempt | `INV-TXN-AUX-001` | `e5cd1ef3` | `be882e81` | deployed-unverified; observed `be882e81` |
| `DEF-0011` | assurance-weakness | assurance | Pytest collection lacked process-local production-path and network defaults | `INV-TEST-001`, `INV-TEST-002` | `36edf036` or earlier environment, bounded | `be882e81` | not applicable |
| `DEF-0012` | assurance-weakness | assurance | Integration and simulator fixtures did not model the complete production integrity generation | `INV-TEST-004`, `INV-ART-001`, `INV-ELIG-001` | `fe8f7713`, bounded | `be882e81` | not applicable |
| `DEF-0013` | assurance-weakness | assurance | Subprocess network egress is not denied at the operating-system boundary | `INV-TEST-003` | unknown | unfixed | not applicable |
| `DEF-0014` | assurance-weakness | assurance | Final validation is not bound to a frozen candidate tree and artifact inventory | `INV-REL-001`, `INV-ART-001` | unknown | unfixed | not applicable |
| `DEF-0015` | active | medium | Process health does not expose the loaded commit and artifact generation identity | `INV-PROC-004`, `INV-ART-001` | `2179d71e`, bounded | unfixed | observed in production `be882e81` |
| `DEF-0016` | latent-disabled | medium | Semantic-veto manifest pins a multi-purpose formatter as its attribution predicate | `INV-VETO-001`, `INV-ART-001` | `2220df1e` | unfixed | enforcement unsupported; shadow only |
| `DEF-0017` | active | high | Duplicate JSON object names remain last-wins in safety documents | `INV-CONFIG-001`, `INV-PAUSE-001`, `INV-TXN-RECEIPT-001` | `36edf036`, bounded | unfixed | observed in production `be882e81` |

## Records

### DEF-0001 — Ordinary eligibility exact partition

The affected loader derived eligibility from completed packets and a predicate
instead of validating the complete reviewed runtime partition. A coherent
packet/manifest drift could therefore alter ordinary eligibility.

Chronology:

- 2026-07-18, `fe8f7713`: packet-derived eligibility entered runtime.
- 2026-07-28, `e5cd1ef3`: independent review reproduced stale and coherently
  rehashed drift.
- 2026-07-28, `be882e81`: exact partition, alias and source-hash binding landed.
- 2026-07-28, `be882e81`: the exact installed source was deployed; production
  bootstrap loaded 611 validated eligible quotations.

Detection used both stale-hash and coherently rehashed mutations. Regressions
are
`tests/test_runtime_ordinary_eligibility_integrity.py::test_coherently_rehashed_packet_mutation_still_requires_exact_partition`
and
`::test_core_partition_drift_fails_before_ordinary_selection`.

This repair is `deployed-verified` for production bootstrap and exact
eligibility loading. Residual risk remains in atomic generation promotion and
release/process attestation (`DEF-0014`, `DEF-0015`).

### DEF-0002 — Historical-context rendered-reply binding

The original gate bound its ledger and blocked projection but not the current
allowed rendering under deployed formatter options, nor correction sidecar
bytes through the authoritative truth audit.

Chronology:

- 2026-07-22, `f04a3c72`: ledger/projection gate introduced.
- 2026-07-28, `e5cd1ef3`: correction drift and allowed-render drift reproduced.
- 2026-07-28, `be882e81`: correction hash and current rendering binding landed.
- 2026-07-28, `be882e81`: production bootstrap loaded the gate successfully
  with 13 blocked entries.

Regression evidence includes
`test_gate_closes_on_coherently_rehashed_correction_drift`,
`test_gate_closes_when_a_reviewed_allowed_current_render_drifts`, and
`test_bot_passes_live_context_formatter_options_to_the_gate` in
`tests/test_historical_context_reply_semantic_gate.py`.

This repair is `deployed-verified` for the live production gate load.
Complete-generation promotion and loaded-generation identity remain residual
risks.

### DEF-0003 — Exact regular-post history replay

Schema-v1 receipts replayed quote and image history additively and could retain
pre-reset entries. The remote post remained idempotent, but later selection
state could differ from the post-transaction state.

Chronology:

- 2026-07-06, `9ae7e0f2`: schema-v1 regular receipt and additive replay
  introduced.
- 2026-07-27, `acfc4f69`: confirmed-post durability increased reliance on the
  receipt but retained schema v1.
- 2026-07-28, `be882e81`: schema v2 added authoritative
  `quote_history_after` and `image_history_after`.
- 2026-07-28, `be882e81`: schema v2 was deployed, but deployment recorded
  `receipt_replay_observed=false`.

The primary regression is
`tests/test_unit_helpers.py::test_regular_receipt_v2_restores_authoritative_post_cycle_histories`;
v1 compatibility remains explicit. This repair is `deployed-unverified`
because no post-deployment replay exercised authoritative after-state
restoration; inspect any extant legacy receipt before relying on v2 semantics.

### DEF-0004 — Wrapper/child supervision

The old launcher discarded child output and restarted quick failures or clean
exits forever. systemd could observe a live wrapper while the Python child was
not stable.

Chronology:

- 2026-07-05, `2179d71e`: restart-forever wrapper and `/dev/null` redirection
  introduced.
- 2026-07-28, `e5cd1ef3`: hidden crash-loop and env-file ordering defects found.
- 2026-07-28, `be882e81`: output propagation, post-env validation and a bounded
  fast-failure breaker landed.
- 2026-07-28, `be882e81`: the matching installed wrapper ran with a stable
  child for 316 seconds, `NRestarts=0`, and no startup errors or tracebacks.

Integration regressions cover nonzero failure, repeated clean exit and invalid
effective env-file settings. The repair is `deployed-unverified`: stable
operation proves neither the bounded fast-failure path nor repeated-clean-exit
handling in the installed unit. `DEF-0015` tracks the missing loaded-generation
identity.

### DEF-0005 — Configuration/control umbrella

This diagnosis row is intentionally `superseded`. Local configuration began in
`36edf036`, while runtime control validation began in `4a4afb4f`; one
introduction or deployment status would be misleading. `DEF-0006` and
`DEF-0007` are the authoritative atomic records. Duplicate object names are a
separate active defect in `DEF-0017`.

### DEF-0006 — Local-config unknown-key atomicity

The old loader warned about unsupported keys and could apply supported peers.
A typo such as `ENABLE_AUTO_REPLY` could therefore coexist with applied
schedule changes.

Chronology:

- 2026-07-01, `36edf036`: affected behavior is present in the initial commit;
  no earlier last-known-good revision exists.
- 2026-07-28, `be882e81`: unsupported keys began rejecting the whole
  transaction.
- 2026-07-28, `be882e81`: exact source was deployed and bootstrap passed; no
  adversarial unknown key was applied to production.

Regressions are
`tests/test_unit_helpers.py::test_local_config_unknown_key_rejects_whole_transaction`
and
`tests/test_integration_harness.py::test_local_config_validation_rejects_bad_values_and_cannot_override_paths_or_urls`.
The fix is `deployed-unverified`: installed bytes match, but defect-specific
post-deployment rejection was not exercised. Duplicate JSON names remain open.

### DEF-0007 — Runtime-control typo semantics

The old validator accepted arbitrary `disable_*`, `pause_*` and `*_until`
names, while enforcement consumed a fixed set. Thus `disable_alll` could
validate without pausing anything.

Chronology:

- 2026-07-11, `4a4afb4f`: permissive control validation introduced.
- 2026-07-28, `e5cd1ef3`: multiple plausible typos reproduced.
- 2026-07-28, `be882e81`: explicit boolean/time/metadata allowlists landed.
- 2026-07-28, `be882e81`: deployment acknowledged a valid global pause,
  restored the original control bytes and observed an idle no-write cycle, but
  did not submit an unknown pause-like key.

The parameterized regression is
`tests/test_fail_safe_bootstrap_and_control.py::test_unknown_runtime_control_keys_fail_closed`.
The fix is `deployed-unverified`: the valid-pause exercise did not verify
fail-closed rejection of a typo in the deployed runtime. Operational
acknowledgement remains mandatory, and duplicate JSON names remain open.

### DEF-0008 — Bounded total request budget

The configurable timeout was a positive scalar without an upper bound tied to
the service stop window.

Chronology:

- 2026-07-02, `b0c4f823`: configurable scalar timeout introduced.
- 2026-07-28, `be882e81`: 60-second maximum and one urllib3 total budget with a
  ten-second connect cap landed.
- 2026-07-28, `be882e81`: exact source was deployed, but deployment performed
  no X action or post-deployment request-budget exercise.

Regressions reject values over the service budget and inspect the actual
timeout object passed to X requests. The repair is `deployed-unverified`
because no live request budget was exercised. Any future retry-count or
systemd timeout change must re-evaluate `INV-API-001`.

### DEF-0009 — Generated image classification

The optional pool appended any contained glob match without proving a known
generated identity. The repaired implementation is deployed, but verification
remains blocked because `ENABLE_GENERATED_IMAGE_POOL` is false.

Chronology:

- 2026-07-08, `d1026728`: generated pool append loop introduced.
- 2026-07-28, `be882e81`: all generated files began passing through the
  provenance classifier and unclassifiable names fail closed.
- 2026-07-28, `be882e81`: repaired source was deployed while the optional pool
  remained disabled and unchanged.

The regression uses `reviewed_ai.png`. Status is `deployed-unverified`; keep
the pool disabled until a controlled classification/inventory activation
probe verifies the deployed behavior.

### DEF-0010 — Digest call-attempt resume

The cost report could split a call start and usage event across two incremental
windows without carrying the in-flight attempt.

Chronology:

- 2026-07-28, `e5cd1ef3`: cost summary introduced with the incomplete resume
  model.
- 2026-07-28, `be882e81`: active call attempt persistence and unmatched-success
  classification landed.
- 2026-07-28, `be882e81`: repaired digest source was deployed, but no
  operational two-window provider call was observed.

The two regressions exercise a two-run resume sequence and a usage record with
no matching start. The fix is `deployed-unverified`; the current model carries
one active call, and future concurrent calls need keyed resumable state.

### DEF-0011 — Pre-collection test isolation

This is an assurance weakness, not a production bot incident. Before
`be882e81`, test-module imports were not uniformly preceded by process-local
paths, dead endpoints and a default-deny socket policy.

Chronology:

- 2026-07-01, `36edf036`: initial tests had no global pre-collection bootstrap.
- 2026-07-27, `370aee9`: independent suite review recorded the missing global
  network guard.
- 2026-07-28, `be882e81`: isolated environment and in-process socket guard
  landed.

Meta-tests verify collection-time runtime values and socket denial. The
subprocess boundary remains open as `DEF-0013`.

### DEF-0012 — Production-parity fixtures

Several integration and simulator fixtures represented only the packet file or
other selected inputs, configuring away the full manifest/status/eligibility
relationship.

Chronology:

- 2026-07-18, `fe8f7713`: packet-derived production eligibility made the
  fixture mismatch safety-relevant; the exact boundary is bounded because
  later artifact commits added further relationships.
- 2026-07-28, `be882e81`: strengthened validation exposed the mismatch and
  fixture builders gained the core manifest, status and runtime partition.

Hand-built fixtures can drift again. A single builder-generated minimal
generation remains the desired closure.

### DEF-0013 — OS-level subprocess egress denial

The current socket guard is process-local. Child programs can ignore Python
monkeypatches and proxy conventions. There is no defensible first-bad or repair
commit because this is an absent CI/host control.

The gap was recorded in the 27 July test-suite review and remained explicit
after `be882e81`. Closure requires a network namespace or equivalent OS-level
default-deny gate that covers parent and children.

### DEF-0014 — Frozen-tree release attestation

No executable gate currently binds final tests to a locked Git tree, complete
generated inventory and deployed tree. The origin commit is `unknown` because
the missing control spans Git, collaborative work and deployment.

The 28 July diagnosis made this a Priority 0 requirement. Closure requires any
post-test edit to invalidate the attestation and deployment to verify the same
attested release.

### DEF-0015 — Loaded generation identity

Current master improves liveness but startup and health evidence still do not
identify the loaded Git tree and generated generation cryptographically.

The wrapper has lacked that identity since at least `2179d71e`; no earlier
audited last-known-good version is known. A read-only production preflight
could resolve PIDs and paths but could not prove loaded module bytes from
current disk HEAD. This remains active and unfixed.

### DEF-0016 — Semantic-veto dependency boundary

Since `2220df1e`, the material-veto manifest has pinned the whole
`historical_context_formatter.py` as `attribution_predicate`. Unrelated
formatter or loader changes can therefore invalidate a large reviewed pair
generation.

The status is `latent-disabled`: current runtime permits veto only in disabled
or shadow mode, not enforcement. A narrow versioned predicate, pair
reconstruction and one generation transaction are prerequisites for any
enforcement design.

### DEF-0017 — Duplicate JSON names

Python's default JSON decoder keeps the final duplicate name. Current master
still uses it for local config and control, and related state/receipt readers
share the risk.

Chronology:

- 2026-07-01, `36edf036`: the initial config/state implementation used default
  JSON parsing.
- 2026-07-27, `370aee9`: the independent review explicitly recorded duplicate
  names as an unresolved gap.
- 2026-07-28, `be882e81`: unknown-name allowlists improved, but duplicate-name
  rejection did not land.

A minimal reproducer is
`{"disable_all":true,"disable_all":false}`. This remains active in observed
production and current master. Closure requires one shared strict decoder plus
mutation tests for all safety documents.

## Unknown-value policy

The following are intentionally unknown rather than inferred:

- `DEF-0005`: no single umbrella introduction or fix commit; child records are
  authoritative.
- `DEF-0006`, `DEF-0011`, `DEF-0015`, `DEF-0017`: no earlier repository or
  audited last-known-good implementation exists for the stated property.
- `DEF-0013`, `DEF-0014`: an absent environment/release control has no
  defensible introducing Git commit.
- `DEF-0013` through `DEF-0017`: no repair commit exists where the JSON records
  `fix.state=unfixed`.

These explanations are recorded per field in `defect_ledger.json`; consumers
must not replace them with guessed commits or deployment claims.
