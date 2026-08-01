# MrsMThatcher defect ledger

This is the deterministic human-readable projection of
`defect_ledger.json`, which is the source of truth. Records are sorted by
`id`; commit references are rendered as their first eight hexadecimal
characters unless the JSON value is the literal `unknown`. No status,
chronology, test, fix, deployment claim, or residual-risk conclusion should be
maintained independently in this file.

Purpose: evidence-cut-off-bound inventory for the ten 28 July diagnosis
findings and directly related runtime and release-assurance gaps, including
independently reproduced candidate-lineage defects.

## Identity and evidence boundary

- Production baseline commit: `be882e8121a7b4348a57b61b1cf526401a36f5c0`
- Production baseline tree: `7965dbb935f2a9f993d14aa37d93283e16bc298a`
- Ledger evidence cut-off commit: `4e548b0a5723a1f0c75e9646953b3f92c7db89ad`
- Ledger evidence cut-off tree: `a751f4b488ee20d268a082c7daf835d5786e7e23`
- Evidence valid through: `2026-08-01`
- Baseline/cut-off relationship: The evidence cut-off advances beyond the recorded production baseline to the exact reviewed 4e548b0a application candidate. By that cut-off, DEF-0040 through DEF-0043 are repaired by 634fd6c, DEF-0046 through DEF-0048 by 5a11bbf, DEF-0049 and DEF-0050 by ce970f8, and DEF-0051 by fb8eb25; none of those repairs is deployed. DEF-0044 remains an assurance weakness and DEF-0045 remains an active fail-closed availability gap. Independent review of the exact 4e548b0a commit and a751f4b4 tree established DEF-0052 through DEF-0060. No later worktree repair is part of this ledger, and this ledger does not claim that 4e548b0a or any descendant was merged, deployed or loaded.
- Status-claim boundary: Every status and fix identity is an evidence claim valid only through this exact reviewed commit and tree. Candidate changes after 4e548b0a are external proposals and remain unresolved here until a later committed release base is merged and the ledger is regenerated.
- Candidate identity source: `external-release-attestation`; stored in ledger: `false`
- Candidate attestation fields: `base_commit`, `candidate_commit`, `candidate_tree`
- Candidate identity rule: The exact release base for this ledger is 4e548b0a5723a1f0c75e9646953b3f92c7db89ad. Any descendant candidate identity is supplied only by the frozen-candidate release attestation and is deliberately not embedded here. Repairs committed by that base are recorded with fix-bound evidence; worktree proposals after that base, including proposals for DEF-0052 through DEF-0060, remain external and unfixed in ledger truth until merge and post-merge regeneration.
- Observed production repository commit/tree: `be882e8121a7b4348a57b61b1cf526401a36f5c0` / `7965dbb935f2a9f993d14aa37d93283e16bc298a`
- Production observation time: `2026-07-28T23:40:14+01:00`
- Loaded-process identity: `installed-files-observed-process-commit-unattested` — Installed source and wrapper hashes matched the recorded repository commit, but the running child did not emit a cryptographically bound loaded commit or generated-artifact generation identity.
- Freshness warning: Production is mutable. Recheck the deployed commit, exact installed hashes and loaded child before relying operationally on any deployment status.
- Post-merge regeneration required: `true`
- Regeneration triggers: `production-baseline-advanced`, `defect-status-changed`, `invariant-status-changed`, `deployment-evidence-changed`
- Regeneration rule: After a merge or deployment changes any recorded defect, invariant or deployment status, regenerate and revalidate this ledger from the new exact release base before using it for another release attestation. No uncommitted or post-4e548b0a candidate implementation, test or proposed repair may change DEF-0044, DEF-0045 or DEF-0052 through DEF-0060 from their recorded status; closure requires an ancestor of the regenerated cut-off with fix-bound tests and chronology.

## Status taxonomy

- `active`: at the ledger evidence cut-off, the defect is present and its
  affected path or control is usable; no verified repair is recorded.
- `latent-disabled`: at the ledger evidence cut-off or recorded deployment
  observation, the unsafe implementation is present but the affected optional
  feature or enforcement mode is documented as disabled; enabling it requires
  closure first.
- `repaired-not-deployed`: the repair is present by the ledger evidence
  cut-off, but the latest recorded production deployment observation predates
  that repair.
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

`DEF-0013` through `DEF-0030` are directly related, established gaps recorded
at the ledger evidence cut-off. Their status must be regenerated before this
ledger is used against a later production baseline.

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
| `DEF-0017` | runtime-defect | `mrsMThatcher2.py`; `mrsMThatcher.local.json`; `mrsMThatcher.control.json` | `configuration-and-pause-controls` | unknown — No complete archive of production configuration or control documents establishes whether a duplicate name altered live behavior. Evidence: Default json.load behavior reproduces last-wins parsing for local configuration and runtime pause controls and remains present at 7ebcc096. |
| `DEF-0018` | assurance-weakness | `production_invariants.json`; `production_invariants.schema.json`; `defect_ledger.json`; `defect_ledger.schema.json`; `tools/release_gate.py`; `tools/priority0_registry.py`; `tools/defect_ledger.py` | `release-control-input-parsing` | false — This is a release-assurance parser weakness, distinct from DEF-0017's still-open production runtime parser defect. Evidence: The independent review used synthetic duplicated names; no production document or runtime incident is alleged. |
| `DEF-0019` | assurance-weakness | `tools/release_gate.py`; `tools/release_gate_pytest_plugin.py`; `production_invariants.json` | `release-attestation-trust-root` | false — The weakness concerns who controls the certifying implementation, not bot runtime behaviour. Evidence: No release or deployment used the reviewed candidate as an approved external trust root. |
| `DEF-0020` | assurance-weakness | `tools/release_gate.py` | `release-validation-containment` | false — No actual unrelated file or production secret was read or modified. Evidence: Only a synthetic host-side canary was used. |
| `DEF-0021` | assurance-weakness | `tools/release_gate.py`; `tools/release_gate_pytest_plugin.py` | `release-validation-python-environment` | false — No real unrelated project package was imported or modified. Evidence: The reproduction imported a synthetic undeclared sibling package. |
| `DEF-0022` | assurance-weakness | `production_invariants.json`; `production_invariants.schema.json`; `tools/release_gate.py` | `release-validation-command-policy` | false — No real host project or production path was targeted. Evidence: Only a synthetic shell command writing a canary was used. |
| `DEF-0023` | assurance-weakness | `tools/release_gate.py` | `release-generated-artifact-attestation` | false — It invalidates an assurance conclusion but does not establish a production runtime incident. Evidence: The finding was reproduced against synthetic and current candidate binding records. |
| `DEF-0024` | runtime-defect | `mrsMThatcher2.py` | `regular-quote-image-post` | false — This is a repeatable production-code defect, but the review evidence is a controlled offline reproduction rather than an observed live duplicate. Evidence: The failure was reproduced with a synthetic hard-process exit after simulated remote acceptance; no production duplicate has been established from this finding. |
| `DEF-0025` | runtime-defect | `mrsMThatcher2.py` | `daily-meme-post` | false — This is a repeatable production-code defect, but the review evidence is a controlled offline reproduction rather than an observed live duplicate. Evidence: The failure was reproduced with a synthetic hard-process exit after simulated remote acceptance; no production duplicate has been established from this finding. |
| `DEF-0026` | assurance-weakness | `defect_ledger.json`; `tools/defect_ledger.py` | `release-attestation-ledger-validation` | true — The incident invalidated that patch-local assurance conclusion; it did not change or activate production. Evidence: An archived external gate run qualified a candidate without executing ledger_validate and supplied a release base different from the ledger evidence cut-off. |
| `DEF-0027` | runtime-defect | `mrsMThatcher2.py` | `regular-quote-image-post`; `scheduler-recovery` | false — This is a repeatable production-code defect demonstrated offline rather than a confirmed production incident. Evidence: Source review and synthetic fault injection established the repeatable path; no live duplicate or out-of-bound schedule incident was established. |
| `DEF-0028` | runtime-defect | `mrsMThatcher2.py` | `daily-meme-post`; `scheduler-recovery` | false — The defect is repeatable offline, but the repository evidence does not establish that it caused a production duplicate. Evidence: Source review and a synthetic same-date schedule failure established the repeatable path; no second live meme was attributed to it. |
| `DEF-0029` | runtime-defect | `mrsMThatcher2.py`; `historical_context_outbox.py` | `regular-quote-image-post`; `daily-meme-post`; `conversational-reply`; `historical-context-reply` | false — This is a cross-lane production-code defect demonstrated offline rather than a confirmed live incident. Evidence: Controlled synthetic response classification established repeatable retry exposure across all four create lanes; no live duplicate caused by a generic 3xx or 4xx response was established. |
| `DEF-0030` | runtime-defect | `mrsMThatcher2.py` | `regular-quote-image-post`; `daily-meme-post`; `conversational-reply`; `cross-cutting-remote-write-barrier` | false — The defect is repeatable in the unactivated reviewed candidate, but it is not an observed live incident and does not prove that the confirmed main post itself would necessarily be recreated. Evidence: Independent synthetic fault injection reproduced the boundary in both main-post lanes without a network or X action; no production duplicate was established. |
| `DEF-0031` | runtime-defect | `mrsMThatcher2.py` | `regular-quote-image-post`; `daily-meme-post`; `cross-cutting-remote-write-barrier`; `controlled-service-stop` | false — The defect is repeatable in the unactivated dd8aa52 reviewed candidate. Remote writes remain blocked while that process lives, but its retained SIGINT and restart-safety transition cannot recover after the first paused tick. Evidence: Independent synthetic two-tick fault injection reproduced the delayed recovery without a network or X action; no production incident or duplicate post was established. |
| `DEF-0032` | runtime-defect | `mrsMThatcher2.py` | `regular-quote-image-post`; `daily-meme-post`; `conversational-reply`; `historical-context-reply`; `cross-cutting-remote-write-barrier`; `controlled-service-stop` | false — The defect is present in the observed production lineage and the unactivated ee7539c candidate, but is repaired by the unactivated 2ad0f79 evidence cut-off. The review proved a missing restart barrier rather than an actual live duplicate. Evidence: Independent local fault injection removed the marker inside the parent-directory fsync hook and reproduced false durability acknowledgement without a network, provider or X action; no production incident or duplicate post was established. |
| `DEF-0033` | runtime-defect | `mrsMThatcher2.py` | `process-bootstrap`; `regular-quote-image-post`; `daily-meme-post`; `conversational-reply`; `historical-context-reply`; `one-shot-operations`; `offline-marker-reconciliation` | false — This is a latent critical runtime defect present in the observed production lineage and the unactivated ee7539c candidate, repaired by the unactivated 2ad0f79 evidence cut-off. It is not evidence that two production processes actually posted concurrently. Evidence: The final local source audit established the path-following, namespace-replacement and same-descriptor re-acquisition gaps without a network, provider or X action; no production concurrency incident, duplicate post or state race was established. |
| `DEF-0034` | runtime-defect | `mrsMThatcher2.py` | `regular-quote-image-post`; `daily-meme-post`; `conversational-reply`; `historical-context-reply`; `media-upload`; `provider-request`; `cross-cutting-remote-write-barrier` | false — The defect was reproduced in the unactivated 2ad0f79 candidate. It establishes an open remote-write barrier after restart, not an observed production post or duplicate. Evidence: Independent fresh-process fault injection reproduced a path to the local historical-context remote boundary with every network/provider operation replaced by a sentinel. No production, provider or X action occurred. |
| `DEF-0035` | runtime-defect | `mrsMThatcher2.py`; `tools/reconcile_remote_write_safety_marker.py` | `regular-quote-image-post`; `daily-meme-post`; `conversational-reply`; `historical-context-reply`; `media-upload`; `provider-request`; `cross-cutting-remote-write-barrier` | false — The defect was reproduced in the unactivated debc079 candidate. It establishes loss of an unresolved-outcome barrier after a further hard process exit, not an observed production post or duplicate. Evidence: Independent review used two literal Python interpreters and a disposable state directory. The second interpreter reached local direct, shared and historical-context scheduler boundaries with all HTTP, X, media and provider operations replaced by sentinels. |
| `DEF-0036` | runtime-defect | `mrsMThatcher2.py`; `historical_context_formatter.py` | `regular-quote-image-post`; `daily-meme-post`; `conversational-reply`; `historical-context-reply`; `media-upload`; `provider-request`; `cross-cutting-remote-write-barrier` | false — This is a pre-freeze offline defect reproduction, not evidence of a live duplicate or production incident. Evidence: A disposable active-protocol state with a schema-valid historical-context sending receipt left shared and raw transport preflight open. Separate source inspection showed that prepared regular, meme and conversational records were accepted from memory when their durable receipt path was absent. No network, provider, X or production action occurred. |
| `DEF-0037` | runtime-defect | `mrsMThatcher2.py`; `remote_write_safety_protocol.py`; `tools/activate_remote_write_safety_protocol.py` | `regular-quote-image-post`; `daily-meme-post`; `conversational-reply`; `historical-context-reply`; `media-upload`; `provider-request`; `cross-cutting-remote-write-barrier` | false — These are pre-freeze adversarial reproductions, not production incidents. Evidence: A disposable established-state directory used the shipped unaudited new-install helper and opened all local remote-lane sentinels. A separate deterministic namespace race composed a previously read valid sentinel with a later valid audit even though no valid pair existed at return. No network, X, provider, service or production action occurred. |
| `DEF-0038` | runtime-defect | `mrsMThatcher2.py`; `historical_context_formatter.py`; `remote_write_transport_journal.py`; `ir_40ab83e_cross_lane_boundary_matrix.json` | `regular-quote-image-post`; `daily-meme-post`; `conversational-reply`; `historical-context-reply`; `cross-cutting-remote-write-barrier` | false — The transport/receipt interleaving was an offline adversarial reproducer, not a production incident. Evidence: IR-40AB83E-01 used local literal-process receipt replacement and hard-exit boundaries without making any network, X, provider, service or production request. |
| `DEF-0039` | runtime-defect | `mrsMThatcher2.py`; `remote_media_upload_receipt.py`; `ir_40ab83e_cross_lane_boundary_matrix.json` | `regular-quote-image-post`; `daily-meme-post`; `media-upload`; `cross-cutting-remote-write-barrier` | false — The possible double upload was established by offline boundary analysis and regression reproduction, not by a production incident. Evidence: The review used deterministic local v2 upload stubs, response exceptions and hard exits; it made no network, X, provider, service or production request. |
| `DEF-0040` | runtime-defect | `mrsMThatcher2.py`; `historical_context_formatter.py`; `remote_write_transport_journal.py`; `remote_media_upload_receipt.py` | `regular-quote-image-post`; `daily-meme-post`; `conversational-reply`; `historical-context-reply`; `media-upload`; `cross-cutting-remote-write-barrier` | false — This is a supported-process transaction-authority defect established by adversarial review, not evidence of a production duplicate. Evidence: The exact-cut-off review used local pathname replacement reasoning and byte-identical offline fixtures; it made no network, X, provider, service or production request. |
| `DEF-0041` | runtime-defect | `mrsMThatcher2.py`; `historical_context_formatter.py`; `remote_write_transport_journal.py`; `remote_media_upload_receipt.py` | `regular-quote-image-post`; `daily-meme-post`; `conversational-reply`; `historical-context-reply`; `media-upload`; `cross-cutting-remote-write-barrier` | false — The failure ordering is an offline adversarial transaction reproducer, not an observed production incident. Evidence: The cut-off review injected local parent-directory fsync failures after unlink boundaries and made no network, X, provider, service or production request. |
| `DEF-0042` | runtime-defect | `mrsMThatcher2.py`; `historical_context_formatter.py`; `remote_write_transport_journal.py`; `remote_media_upload_receipt.py` | `regular-quote-image-post`; `daily-meme-post`; `conversational-reply`; `historical-context-reply`; `media-upload`; `cross-cutting-remote-write-barrier` | false — This is an internal serialization and API-authority defect, not a claim of hostile production access. Evidence: The review invoked low-level mutation helpers from local supported-process fixtures without a current instance-lock proof; no network, X, provider, service or production request occurred. |
| `DEF-0043` | runtime-defect | `mrsMThatcher2.py`; `remote_write_transport_journal.py`; `remote_media_upload_receipt.py` | `regular-quote-image-post`; `daily-meme-post`; `conversational-reply`; `media-upload`; `cross-cutting-remote-write-barrier` | false — These are offline parser and namespace adversarial cases, not a production incident. Evidence: The exact-cut-off review used disposable symlink, directory, FIFO, duplicate-key, non-finite and non-canonical JSON fixtures and made no network, X, provider, service or production request. |
| `DEF-0044` | assurance-weakness | `tools/release_gate.py`; `tools/release_gate_pytest_plugin.py` | `release-assurance` | false — This is an assurance trust-root weakness, not evidence that a production release was actually forged. Evidence: The review constructed only local evidence-forgery scenarios against the exact candidate tree; it did not issue or rely on a real release attestation. |
| `DEF-0045` | runtime-defect | `remote_write_transport_journal.py`; `remote_media_upload_receipt.py` | `regular-quote-image-post`; `daily-meme-post`; `conversational-reply`; `historical-context-reply`; `media-upload`; `cross-cutting-remote-write-barrier` | false — This record is explicitly an availability and manual-recovery weakness in a fail-closed path; it does not classify safe blocking as a duplicate-safety failure. Evidence: The review considered hard exits before and after local RENAME_EXCHANGE staging transitions. No remote transport, production action or duplicate was observed. |
| `DEF-0046` | runtime-defect | `mrsMThatcher2.py`; `tests/test_unit_helpers.py` | `regular-quote-image-post`; `daily-meme-post`; `cross-cutting-remote-write-barrier` | false — The replay defect is reproducible offline; no duplicate live meme, quotation or image reuse is inferred. Evidence: A pre-freeze adversarial review used synthetic stale schema-v2/v3 receipts and newer in-memory state. It made no network, X, provider, service or production request. |
| `DEF-0047` | runtime-defect | `mrsMThatcher2.py`; `README.md`; `tests/test_integration_harness.py`; `tests/test_x_write_outcome_conservatism.py` | `regular-quote-image-post`; `daily-meme-post`; `conversational-reply`; `historical-context-reply`; `media-upload`; `cross-cutting-remote-write-barrier` | false — This is a cutoff-bound configuration and route-classification reproducer, not evidence that production used a path-bearing endpoint or bypassed an authority check. Evidence: The defect was reproduced with synthetic path-bearing X base values and a local transport sentinel. No network, X, provider, service or production action occurred. |
| `DEF-0048` | runtime-defect | `mrsMThatcher2.py`; `tests/test_unit_helpers.py` | `regular-quote-image-post`; `daily-meme-post`; `conversational-reply`; `provider-request`; `cross-cutting-remote-write-barrier` | false — This establishes an omitted global barrier state, not a live duplicate or provider incident. Evidence: The defect was reproduced with synthetic, schema-valid legacy regular, meme and confirmed reply receipts in an isolated state directory. No network, X, provider, service or production action occurred. |
| `DEF-0049` | runtime-defect | `mrsMThatcher2.py`; `README.md`; `tests/test_integration_harness.py`; `tests/test_x_write_outcome_conservatism.py` | `media-upload` | false — This is a cutoff-bound configuration-routing defect, not evidence that production configured or required a distinct upload origin. Evidence: The mismatch was reproduced with distinct synthetic loopback API and upload origins. No network, X, provider, service or production action occurred. |
| `DEF-0050` | runtime-defect | `mrsMThatcher2.py`; `remote_media_upload_receipt.py`; `tests/test_remote_media_upload_receipt.py`; `tests/test_transaction_mutation_authority.py`; `tests/test_x_write_outcome_conservatism.py` | `regular-quote-image-post`; `daily-meme-post`; `media-upload`; `global-pause-control` | false — The durable pair remained fail closed and therefore did not create a duplicate-write opening; the established defect is unnecessary manual recovery of a definitely untransmitted transaction. Evidence: The availability boundary was reproduced using a synthetic pause transition after durable receipt/fence publication and before a local transport sentinel. No HTTP, network, X, provider, service or production action occurred. |
| `DEF-0051` | runtime-defect | `mrsMThatcher2.py`; `remote_write_transport_journal.py`; `tests/test_media_upload_transaction_integration.py`; `tests/test_unit_helpers.py`; `tests/test_x_write_outcome_conservatism.py` | `regular-quote-image-post`; `daily-meme-post`; `media-upload-handoff`; `global-pause-control` | false — The source receipt remained fail closed in both pause phases. In the descendant handoff protocol under review, the initial create_post pause also left its still-prepared journal/fence pair, whereas the final x_request pause aborted that pair before escaping and left only the source unresolved. The established defect is unnecessary manual recovery after a tweet request proved untransmitted, not a duplicate-write opening. Evidence: The availability boundary was reproduced in isolated state directories with one local synthetic media-upload response and a tweet-transport sentinel. No tweet request, external network, X, provider, service or production action occurred. |
| `DEF-0052` | runtime-defect | `mrsMThatcher2.py`; `tests/test_unit_helpers.py` | `regular-quote-image-post`; `daily-meme-post`; `scheduler-state-recovery` | false — This establishes a deterministic restart rollback path; it does not establish that deployed production held a newer backup or emitted a duplicate post. Evidence: The ordering gap was reproduced with isolated valid primary and bak1 documents containing different state generations. No production state, network or remote service was accessed. |
| `DEF-0053` | runtime-defect | `historical_context_formatter.py`; `historical_context_outbox.py`; `mrsMThatcher2.py`; `tests/test_historical_context_outbox.py`; `tests/test_historical_context_reply.py`; `tests/test_production_consistency_incident.py` | `historical-context-reply`; `cross-cutting-remote-write-barrier` | false — The review establishes that recovery could not distinguish a definitely local interruption from a potentially transmitted one; it does not claim that a live historical-context reply was duplicated. Evidence: The phase gap was reproduced in isolated context stores by interrupting a claim before transport and by removing terminal history after a potentially transmitted claim. No X request, network or production state was used. |
| `DEF-0054` | runtime-defect | `mrsMThatcher2.py`; `historical_context_formatter.py`; `remote_write_transport_journal.py`; `tests/test_unit_helpers.py`; `tests/test_historical_context_reply.py`; `tests/test_remote_write_transport_journal.py` | `regular-quote-image-post`; `daily-meme-post`; `conversational-reply`; `historical-context-reply`; `cross-cutting-remote-write-barrier` | false — This establishes acceptance of under-specified local authority documents; it does not establish a live wrong-post or duplicate incident. Evidence: The gaps were reproduced with isolated receipt files using semantically equivalent noncanonical bytes, permissive modes, same-inode content mutation and disappearance at the final path check. Transport and all external access were forbidden. |
| `DEF-0055` | runtime-defect | `mrsMThatcher2.py`; `historical_context_formatter.py`; `remote_write_transport_journal.py`; `remote_media_upload_receipt.py`; `tests/test_unit_helpers.py`; `tests/test_historical_context_reply.py`; `tests/test_remote_write_transport_journal.py`; `tests/test_remote_media_upload_receipt.py` | `regular-quote-image-post`; `daily-meme-post`; `conversational-reply`; `historical-context-reply`; `media-upload`; `cross-cutting-remote-write-barrier` | false — The review establishes incomplete validation of nested proof fields, not a demonstrated live authority bypass. Evidence: The type/range gaps were reproduced with synthetic main, conversational, historical, journal and media documents in an isolated directory. No transport, network, provider, X or production operation occurred. |
| `DEF-0056` | runtime-defect | `mrsMThatcher2.py`; `historical_context_formatter.py`; `historical_context_outbox.py`; `tests/test_followup_fail_safe_hardening.py`; `tests/test_historical_context_reply.py`; `tests/test_historical_context_outbox.py` | `explicit-installation-command`; `production-bootstrap`; `scheduler-state-recovery`; `historical-context-reply` | false — This establishes that a partial first install could be mistaken for an established namespace; it does not establish that a deployed installation was created or restarted in that state. Evidence: The publication and rollback gaps were reproduced against isolated 4e548b0a installation state using injected write, parent-directory synchronisation and hard-exit boundaries. No production file, service, network, X or provider was accessed. |
| `DEF-0057` | runtime-defect | `mrsMThatcher2.py`; `historical_context_formatter.py`; `historical_context_outbox.py`; `tests/test_followup_fail_safe_hardening.py`; `tests/test_historical_context_reply.py`; `tests/test_historical_context_outbox.py` | `production-bootstrap`; `historical-context-reply`; `historical-context-recovery`; `cross-cutting-remote-write-barrier` | false — This establishes that durable authority loss could be hidden as an empty default; it does not establish that a deployed historical-context record was lost or repeated. Evidence: The loss and inspection boundaries were reproduced with isolated established history and outbox documents which were removed or made uninspectable before production-mode reads. No live history, outbox, network, X, provider or service was accessed. |
| `DEF-0058` | runtime-defect | `mrsMThatcher2.py`; `historical_context_outbox.py`; `tests/test_x_write_outcome_conservatism.py` | `historical-context-reply`; `regular-quote-image-post`; `daily-meme-post`; `conversational-reply`; `media-upload`; `cross-cutting-remote-write-barrier` | false — This establishes that an outbox-only possibly transmitted attempt could fail to block another lane; it does not establish that a deployed historical-context reply or unrelated remote write was repeated. Evidence: The barrier gap was reproduced with isolated outbox state after removing independent source-receipt and transport-journal authority and making the historical-context runtime unavailable. No remote transport, network, production state, X or provider was accessed. |
| `DEF-0059` | runtime-defect | `mrsMThatcher2.py`; `tests/test_followup_fail_safe_hardening.py` | `production-bootstrap`; `scheduler-state-recovery`; `ordinary-cycle-history`; `regular-quote-image-post`; `daily-meme-post` | false — This establishes acceptance or following of under-proved local filesystem authority; it does not establish that deployed state or history was substituted or corrupted. Evidence: The namespace gaps were reproduced with isolated symlink, multi-link, ownership and same-inode mutation fixtures for required installation state and used histories. No production file, service, network, X or provider was accessed. |
| `DEF-0060` | runtime-defect | `exact_receipt_retirement.py`; `mrsMThatcher2.py`; `remote_write_safety_protocol.py`; `tools/activate_remote_write_safety_protocol.py`; `tests/test_exact_receipt_retirement.py`; `tests/test_followup_fail_safe_hardening.py`; `tests/test_remote_write_safety_second_restart.py` | `regular-quote-image-post`; `daily-meme-post`; `conversational-reply`; `historical-context-reply`; `production-bootstrap`; `cross-cutting-remote-write-barrier` | false — This establishes a safety-proof gap at the evidence cut-off and a separate availability deadlock in the first post-cutoff ledger proposal; it does not establish that a deployed post was duplicated or that a production startup was stranded. Evidence: The missing permanent completion authority and the post-cutoff startup ordering problem were established with isolated receipt namespaces, crash injection and fresh-process fixtures. No production durable file, remote transport, network, X or provider was accessed. |

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
| `DEF-0011` | 2026-07-27 | unknown | Earlier suite review explicitly recommended a suite-wide non-loopback guard. | bot_and_test_suite_review_report.md#test-isolation-work-still-needed. |
| `DEF-0011` | 2026-07-28 | `be882e81` | Pre-collection environment bootstrap and in-process socket guard committed. | tests/conftest.py grew process-local defaults and socket interception. |
| `DEF-0012` | 2026-07-18 | `fe8f7713` | Production selection gained packet-derived eligibility while minimal fixtures remained partial. | Runtime and fixture histories diverged in represented integrity relationships. |
| `DEF-0012` | 2026-07-28 | `be882e81` | Stronger production loader initially broke partial fixtures, exposing the assurance gap. | Fixture builders gained corpus_manifest, final_research_status and runtime_eligible_quote_manifest. |
| `DEF-0013` | 2026-07-27 | unknown | Suite-wide network isolation recorded as unfinished work. | Bot and test-suite review. |
| `DEF-0013` | 2026-07-28 | `be882e81` | In-process guard landed; OS-level subprocess gap explicitly remained. | Diagnosis Priority 2 item 16. |
| `DEF-0014` | 2026-07-28 | `be882e81` | Diagnosis identified mutable shared candidate files and prose-only release checks as an assurance gap. | Priority 0 item 3 and worktree/deployment complexity section. |
| `DEF-0015` | 2026-07-05 | `2179d71e` | Production wrapper introduced without loaded generation identity. | Launcher history. |
| `DEF-0015` | 2026-07-28 | `be882e81` | Current diagnosis made loaded commit/hash generation a first-class process-health requirement. | Priority 2 item 15 and immutable deployment recommendations. |
| `DEF-0016` | 2026-07-19 | `2220df1e` | Material veto manifest began pinning historical_context_formatter.py as attribution_predicate. | Commit subject: Fix adversarial review findings. |
| `DEF-0016` | 2026-07-28 | `be882e81` | Diagnosis recorded repeated shadow-manifest pin refreshes as a dependency-boundary smell. | Generated artefacts act like code section. |
| `DEF-0017` | 2026-07-01 | `36edf036` | Initial safety-document parsers used the default duplicate-name behavior. | Initial bot source. |
| `DEF-0017` | 2026-07-27 | unknown | Independent bot/test review recorded duplicate JSON keys as an unresolved hardening gap. | bot_and_test_suite_review_report.md:190. |
| `DEF-0017` | 2026-07-28 | `be882e81` | be882e81 repaired unknown names but retained default JSON duplicate-name parsing. | apply_local_config and load_control still call json.load without an object_pairs_hook. |
| `DEF-0017` | 2026-07-31 | `7ebcc096` | The 7ebcc096 audit narrowed this record to configuration and pause controls and separated unsafe transaction receipt namespaces and strict transaction JSON into DEF-0043. | Cut-off source and IR-40AB follow-up review |
| `DEF-0018` | 2026-07-29 | `2d1b7a51` | The release line imported the immutable independent-review package which reproduced asymmetric duplicate-name handling in Priority-0 control documents. | Independent review package a5aea17cce49455f784ffed1c0f9896137991f1476e12fbef68b59ac1b64c978. |
| `DEF-0019` | 2026-07-29 | `2d1b7a51` | The release line imported the immutable independent-review package identifying candidate-controlled policy, execution and evidence as one trust domain. | Independent review package a5aea17cce49455f784ffed1c0f9896137991f1476e12fbef68b59ac1b64c978. |
| `DEF-0019` | 2026-07-31 | `7ebcc096` | Review of the 7ebcc096 release base retained the broader candidate-owned trust-root weakness and separated the concrete ability to forge pytest sidecars and outcomes into DEF-0044. | Cut-off release-gate and candidate-owned pytest-plugin inspection |
| `DEF-0020` | 2026-07-29 | `2d1b7a51` | The release line imported the immutable independent-review reproduction in which a disposable validation command read and modified a synthetic host canary outside protected roots. | independent_review_blocker_reproductions.json |
| `DEF-0021` | 2026-07-29 | `2d1b7a51` | The release line imported the immutable independent-review reproduction in which an undeclared synthetic package remained importable by a candidate Python child. | independent_review_blocker_reproductions.json |
| `DEF-0022` | 2026-07-29 | `2d1b7a51` | The release line imported the immutable independent-review reproduction in which the gate accepted sh -c with synthetic dynamic code. | independent_review_blocker_reproductions.json |
| `DEF-0023` | 2026-07-29 | `2d1b7a51` | The release line imported the immutable independent-review package reproducing missed static_path_composition consumption and the string-versus-tuple mismatch. | independent_review_blocker_reproductions.json |
| `DEF-0024` | 2026-07-30 | `02f9d2be` | Independent review reproduced two remote accepts across a hard process death and restart. | priority0_transaction_assurance_remediation_report.md |
| `DEF-0024` | 2026-07-30 | `6d5608f2` | A durable single-use regular-post attempt was committed before every X create boundary. | Commit source and hard-process-loss regressions |
| `DEF-0025` | 2026-07-30 | `02f9d2be` | Independent review reproduced two remote accepts across a meme hard process death and restart. | priority0_transaction_assurance_remediation_report.md |
| `DEF-0025` | 2026-07-30 | `6d5608f2` | A durable single-use meme-post attempt was committed before every X create boundary. | Commit source and hard-process-loss regressions |
| `DEF-0026` | 2026-07-30 | `02f9d2be` | Independent review reproduced the ledger/base mismatch and confirmed ledger_validate was absent from the archived external run. | priority0_transaction_assurance_remediation_report.md |
| `DEF-0027` | 2026-07-30 | `02f9d2be` | Independent source review and schedule-helper fault injection established loss of the selected post-confirmation schedule plan. | Direct current-master source review with focused synthetic regression design |
| `DEF-0027` | 2026-07-30 | `1b730410` | The exact pre-send regular and meme schedule plan was bound into the durable attempt and replayed locally after confirmation. | Commit source and schedule-finalisation regressions |
| `DEF-0028` | 2026-07-30 | `02f9d2be` | Independent source review reproduced a confirmed morning meme followed by same-date fallback eligibility for a different image. | Direct current-master source review with focused synthetic regression design |
| `DEF-0028` | 2026-07-30 | `1b730410` | Next-local-date recovery and an independent same-date meme-create barrier were committed. | Commit source and same-date regressions |
| `DEF-0029` | 2026-07-30 | `02f9d2be` | Independent cross-lane review injected generic client-error and redirect outcomes and found that sending barriers could be retired or treated as safely retryable. | Direct current-master source review with focused synthetic regression design |
| `DEF-0029` | 2026-07-30 | `1b730410` | All create lanes were changed to preserve the sending barrier for unproved post-transmission outcomes. | Commit source and cross-lane conservative-outcome regressions |
| `DEF-0030` | 2026-07-30 | `1b730410` | Independent review injected failure after pending-receipt replacement and reproduced the missing latch, marker, SIGINT restoration and cross-lane barrier in regular and meme transactions. | Separate read-only review of the tree-exact history-free package |
| `DEF-0030` | 2026-07-30 | `dd8aa52c` | The replacement candidate added exact-byte revalidation, explicit parent re-fsync, pre-recovery latching, cross-lane barriers and deferred-SIGINT recovery. | Committed source and 21 focused pending-receipt durability regressions |
| `DEF-0031` | 2026-07-30 | `dd8aa52c` | Independent review drove two real daemon-loop ticks: the first marker fsync failed, the second tick did not retry, and the retained guard and deferred SIGINT remained. | Independent IR-DD8AA52-01 reproduction |
| `DEF-0031` | 2026-07-30 | `30ca2b50` | The replacement moved the durability check outside one-shot logging and added a real-daemon two-tick regression proving retry, guard restoration and one deferred-signal delivery. | Committed source and test plus independent predecessor/candidate A/B reproduction |
| `DEF-0032` | 2026-07-11 | `7f76c113` | The ambiguity marker and an existing-marker early-return were introduced without a stable namespace identity check. | Git source history and parent comparison against db84eebf |
| `DEF-0032` | 2026-07-27 | `acfc4f69` | The fail-closed confirmed-post durability and deferred-SIGINT paths began relying on marker visibility without a stable post-synchronisation namespace identity check. | Git source history and parent comparison against c140532 |
| `DEF-0032` | 2026-07-30 | `dd8aa52c` | Parent-directory synchronisation was added to all three marker acknowledgement paths, but no post-synchronisation identity/content recheck was added. | Git source history and parent comparison against 1b730410 |
| `DEF-0032` | 2026-07-31 | `ee7539c2` | Independent review removed the marker during the real parent-directory fsync and reproduced false success in the durable helper, confirmed-post latch and ambiguous-post recorder. | Independent IR-EE7539C-01 reproduction |
| `DEF-0032` | 2026-07-31 | `2ad0f79f` | The replacement candidate centralised no-follow marker acknowledgement, required stable identity and exact bytes across synchronisation, retained process/SIGINT barriers on every mismatch and added lock-bound offline retirement. | Committed source, marker-identity regressions and offline-reconciliation tests |
| `DEF-0033` | 2026-07-04 | `f0be0b5d` | The process-lifetime instance lock was introduced without no-follow single-link acquisition identity or a separate-descriptor continuous-ownership proof. | Git source history and parent comparison against 4f268ed |
| `DEF-0033` | 2026-07-31 | `ee7539c2` | The final remediation audit established that path replacement and same-descriptor re-flocking could not prove one continuous lock namespace across daemon writes and offline reconciliation. | Independent final source audit of the ee7539c evidence cut-off |
| `DEF-0033` | 2026-07-31 | `2ad0f79f` | The replacement candidate bound the process to state-directory identity, a no-follow single-link lock, independent OFD ownership and a directory-identity singleton enforced before remote writes and by offline reconciliation. | Committed lock-ownership implementation and adversarial runtime/reconciler regressions |
| `DEF-0034` | 2026-07-31 | `2ad0f79f` | Independent review began from false globals and a valid marker, removed the marker during real parent-directory fsync, then reached the historical-context remote lane because the surviving uncertainty flag was not blocking. | Independent IR-2AD0F79-01 fresh-process reproduction |
| `DEF-0034` | 2026-07-31 | `debc0799` | The replacement candidate seeded both process-local barriers on marker observation, made either barrier block direct and scheduler paths, and retained them on every marker inspection or acknowledgement failure. | Committed source and same-process fresh-start regressions |
| `DEF-0035` | 2026-07-31 | `debc0799` | Independent review removed the legacy marker during real parent-directory fsync in one interpreter, terminated that correctly latched process through os._exit, then showed a literal second interpreter with false globals and no durable marker could enter the historical-context scheduler lane. | Independent IR-DEBC079-01 literal two-process reproduction |
| `DEF-0035` | 2026-07-31 | `78b5c5b3` | 78b5c5b published and revalidated a durable same-inode successor before legacy-marker acknowledgement and added literal independent-process regressions for successor survival and supported offline retirement. | Preserve remote-write barrier across second restart |
| `DEF-0036` | 2026-07-31 | `debc0799` | A fresh pre-freeze review created a valid historical-context sending receipt and proved that the shared predicate and raw transport/provider preflights remained open. | Disposable receipt-bound direct and scheduler reproduction |
| `DEF-0036` | 2026-07-31 | `debc0799` | Source review additionally proved that prepared main and conversational records were not required to match a currently present durable receipt. | block_if_ambiguous_remote_post control-flow inspection and red regression |
| `DEF-0036` | 2026-07-31 | `5b0b6108` | 5b0b610 closed the global historical-context barrier, exact prepared-receipt ownership, raw transport and historical strict-JSON paths with cross-lane regressions. | Preserve remote-write barriers across further restarts |
| `DEF-0037` | 2026-07-31 | `debc0799` | A final pre-freeze adversarial review activated an established fixture through the unaudited helper and separately reproduced a torn sentinel/audit read through the actual shared preflight. | Disposable activation-helper and namespace-generation reproductions |
| `DEF-0037` | 2026-07-31 | `5b0b6108` | 5b0b610 removed supported unaudited activation, required stopped external-attestation activation and cross-revalidated both activation pathname identities after both stable reads. | Preserve remote-write barriers across further restarts |
| `DEF-0038` | 2026-07-31 | `40ab83ef` | IR-40AB83E-01 reproduced source-receipt disappearance after authority validation and showed that the transport could proceed without an independently durable restart barrier. | Cross-lane transport boundary matrix and literal-process adversarial review |
| `DEF-0038` | 2026-07-31 | `7ebcc096` | 7ebcc096 added a payload-, source- and inode-bound transport journal plus a restart fence that is created and consumed at the final transport boundary. | Harden remote write transport boundaries |
| `DEF-0039` | 2026-07-31 | `40ab83ef` | IR-40AB83E-02 identified that an ambiguous v2 acceptance could be followed automatically by a second v1.1 upload and that media identity lacked a durable owner across restart. | Cross-lane transport boundary matrix and media-upload source review |
| `DEF-0039` | 2026-07-31 | `7ebcc096` | 7ebcc096 removed automatic legacy fallback, added an immutable media upload receipt/fence and handed the confirmed media ID into the main transaction journal before post transport. | Harden remote write transport boundaries |
| `DEF-0040` | 2026-07-31 | `7ebcc096` | Independent review of the exact 7ebcc096 tree identified byte-identical inode replacement windows across receipt validation, transition and retirement. | Exact-cut-off filesystem identity and transition review |
| `DEF-0040` | 2026-08-01 | `634fd6cd` | 634fd6cd committed the fix-bound implementation and regression evidence for DEF-0040. | Fix commit, exact affected-range parent and bound regression nodes recorded in this ledger |
| `DEF-0041` | 2026-07-31 | `7ebcc096` | Independent exact-cut-off review reproduced a final-barrier unlink followed by a failed parent-directory fsync and found no guaranteed latch before the destructive operation. | Receipt-retirement durability and latch review |
| `DEF-0041` | 2026-08-01 | `634fd6cd` | 634fd6cd committed the fix-bound implementation and regression evidence for DEF-0041. | Fix commit, exact affected-range parent and bound regression nodes recorded in this ledger |
| `DEF-0042` | 2026-07-31 | `7ebcc096` | Independent exact-cut-off API review found that destructive receipt and journal helpers trusted caller convention rather than verifying current instance-lock authority. | Transaction helper call graph and process-lock authority review |
| `DEF-0042` | 2026-08-01 | `634fd6cd` | 634fd6cd committed the fix-bound implementation and regression evidence for DEF-0042. | Fix commit, exact affected-range parent and bound regression nodes recorded in this ledger |
| `DEF-0043` | 2026-07-31 | `7ebcc096` | Independent review of 7ebcc096 separated the already repaired historical-context parser from unsafe namespace and permissive JSON handling still present in the other transaction receipt lanes. | Exact-cut-off receipt namespace and canonical-JSON review |
| `DEF-0043` | 2026-08-01 | `634fd6cd` | 634fd6cd committed the fix-bound implementation and regression evidence for DEF-0043. | Fix commit, exact affected-range parent and bound regression nodes recorded in this ledger |
| `DEF-0044` | 2026-07-31 | `7ebcc096` | Independent exact-cut-off review showed that the candidate controlled the pytest hook code and knew the evidence outputs whose internal consistency the gate later accepted. | Candidate-owned pytest evidence capability analysis |
| `DEF-0045` | 2026-07-31 | `7ebcc096` | Exact-cut-off review confirmed that hard exit can preserve an intentionally blocking transition staging entry for which no strict automatic resumer or complete operator procedure existed. | RENAME_EXCHANGE staging and restart-liveness review |
| `DEF-0046` | 2026-08-01 | `7ebcc096` | Pre-freeze schedule and recovery review reproduced stale regular-history erasure and stale meme-state rollback against the 7ebcc096 cut-off. | Synthetic stale-receipt replay with a strictly newer quote/meme state |
| `DEF-0046` | 2026-08-01 | `5a11bbf4` | 5a11bbf4 committed the fix-bound implementation and regression evidence for DEF-0046. | Fix commit, exact affected-range parent and bound regression nodes recorded in this ledger |
| `DEF-0047` | 2026-08-01 | `7ebcc096` | Post-cutoff adversarial review combined path-bearing X bases with protected create paths and established disagreement between prepared and literal route identity at 7ebcc096. | Synthetic origin/path configuration and pre-transport route-classification fixture |
| `DEF-0047` | 2026-08-01 | `5a11bbf4` | 5a11bbf4 committed the fix-bound implementation and regression evidence for DEF-0047. | Fix commit, exact affected-range parent and bound regression nodes recorded in this ledger |
| `DEF-0048` | 2026-08-01 | `7ebcc096` | Focused cutoff review installed simultaneous valid legacy regular, meme and confirmed conversational receipts and found the global auxiliary barrier open at 7ebcc096. | Synthetic simultaneous accepted-receipt preflight fixture with a clean-state negative control |
| `DEF-0048` | 2026-08-01 | `5a11bbf4` | 5a11bbf4 committed the fix-bound implementation and regression evidence for DEF-0048. | Fix commit, exact affected-range parent and bound regression nodes recorded in this ledger |
| `DEF-0049` | 2026-08-01 | `7ebcc096` | A four-way post-cutoff adversarial review used distinct local API and upload origins and established that media upload still selected X_BASE at the evidence cut-off. | Distinct loopback-origin routing fixture with separate media, tweet and read observations |
| `DEF-0049` | 2026-08-01 | `ce970f81` | ce970f81 committed the fix-bound implementation and regression evidence for DEF-0049. | Fix commit, exact affected-range parent and bound regression nodes recorded in this ledger |
| `DEF-0050` | 2026-08-01 | `7ebcc096` | A four-way transaction review paused the media lane after durable pair publication but before transport and established that the definitely untransmitted pair remained as a manual blocker. | Final-pretransport pause fixture with a transport sentinel and exact receipt/fence inspection |
| `DEF-0050` | 2026-08-01 | `ce970f81` | ce970f81 committed the fix-bound implementation and regression evidence for DEF-0050. | Fix commit, exact affected-range parent and bound regression nodes recorded in this ledger |
| `DEF-0051` | 2026-08-01 | `7ebcc096` | Focused application-boundary review exercised both main-post lanes after confirmed media handoff and established that locally proved tweet non-transmission did not retire the durable source transaction at the evidence cut-off. | Cutoff source review plus isolated initial- and final-preflight pause regressions with zero tweet transport |
| `DEF-0051` | 2026-08-01 | `fb8eb25f` | fb8eb25f committed the fix-bound implementation and regression evidence for DEF-0051. | Fix commit, exact affected-range parent and bound regression nodes recorded in this ledger |
| `DEF-0052` | 2026-08-01 | `4e548b0a` | Independent cutoff review supplied divergent but individually valid primary and bak1 state and observed the older primary win without an ordering proof. | Isolated state-loader divergence fixture and exact 4e548b0a source review |
| `DEF-0053` | 2026-08-01 | `4e548b0a` | Cutoff review showed that the same attempting-state shape represented both a definitely pre-remote interruption and a potentially transmitted claim, then removed terminal history and observed the latter become retryable instead of remaining blocked. | Isolated historical-context outbox phase and history-monotonicity fixtures |
| `DEF-0054` | 2026-08-01 | `4e548b0a` | Application-boundary review fed noncanonical and non-private receipt documents through each production reader and changed or removed receipt content at the stable-read and source-binding boundaries. | Offline common, historical and journal stable-source document matrix |
| `DEF-0055` | 2026-08-01 | `4e548b0a` | Cutoff review substituted numeric public identifiers, bool/negative/non-string nested source fields and the special basenames '.' and '..' in receipt, journal and media owner documents and found incomplete rejection. | Offline public receipt and journal/media nested-identity validation matrix |
| `DEF-0056` | 2026-08-01 | `4e548b0a` | Cutoff review injected failures before and after the first state, schedule, history and outbox publications and showed that 4e548b0a had no durable pre-write installation barrier or complete rollback inventory. | Isolated first-install ordering, hard-exit, rollback and marker-less restart matrix |
| `DEF-0057` | 2026-08-01 | `4e548b0a` | Cutoff review removed and made uninspectable established historical-context history and outbox authorities and found that their ordinary readers could return empty defaults rather than report durable-state loss. | Isolated production-factory disappearance and namespace-inspection matrix |
| `DEF-0058` | 2026-08-01 | `4e548b0a` | Cutoff review retained a remote-started or legacy attempting outbox row while removing its source receipt and journal and making the historical-context runtime unavailable, then observed that the remaining outbox authority was not a global remote-write barrier. | Isolated True, legacy-absent and explicit-False outbox-phase cross-lane matrix |
| `DEF-0059` | 2026-08-01 | `4e548b0a` | Cutoff review replaced required state and used-history paths with symlinks and exercised unstable or non-single-link authorities, establishing that discovery and loading did not share one strict no-follow file-generation proof. | Isolated core-state symlink, owner/link and same-inode mutation matrix |
| `DEF-0060` | 2026-08-01 | `4e548b0a` | Exact-cutoff source review established that the final cleanup path could unlink the last receipt-retirement auxiliary without publishing a permanent generation-bound completion record. | 4e548b0a exact_receipt_retirement.py final cleanup and all-absent inspection paths |
| `DEF-0060` | 2026-08-01 | unknown | Post-cutoff crash injection against the proposed permanent ledger found that an exact recoverable exchange could be rejected as an incomplete installation before the daemon's authorised startup resumer was called. | Uncommitted startup ledger-exchange ordering regression and fresh-process recovery fixtures |

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
| `DEF-0015` | active | medium | Process health does not expose the loaded commit and artifact generation identity | `INV-PROC-004`, `INV-ART-001` | `2179d71e`, bounded | unfixed | active at the ledger evidence cut-off; production presence not established |
| `DEF-0016` | latent-disabled | medium | Semantic-veto manifest pins a multi-purpose formatter as its attribution predicate | `INV-VETO-001`, `INV-ART-001` | `2220df1e` | unfixed | enforcement unsupported; shadow only |
| `DEF-0017` | active | high | Duplicate JSON object names remain last-wins in configuration and pause controls | `INV-CONFIG-001`, `INV-PAUSE-001` | `36edf036`, bounded | unfixed | active at the ledger evidence cut-off; production presence not established |
| `DEF-0018` | assurance-weakness | assurance | Priority-0 release-control JSON accepts ambiguous duplicate object names | `INV-REL-JSON-001` | `516b9b40`, bounded | unfixed | not applicable |
| `DEF-0019` | assurance-weakness | assurance | Candidate-owned gate can certify the candidate which supplies it | `INV-REL-TRUST-001` | `516b9b40` | unfixed | not applicable |
| `DEF-0020` | assurance-weakness | assurance | Validation containment exposes unrelated host-user files | `INV-REL-SANDBOX-001` | `516b9b40`, bounded | unfixed | not applicable |
| `DEF-0021` | assurance-weakness | assurance | Validation descendants can import undeclared host distributions | `INV-REL-IMPORT-001` | `516b9b40`, bounded | unfixed | not applicable |
| `DEF-0022` | assurance-weakness | assurance | Candidate registry can author arbitrary validation commands | `INV-REL-CMD-001` | `516b9b40` | unfixed | not applicable |
| `DEF-0023` | assurance-weakness | assurance | Runtime artefact-consumption detector misses real loader bindings | `INV-REL-ART-001` | `516b9b40` | unfixed | not applicable |
| `DEF-0024` | repaired-not-deployed | critical | Regular posts lacked a durable pre-send remote-write barrier | `INV-TXN-REG-001` | `36edf036`, bounded | `6d5608f2` | not-deployed; observed `be882e81` |
| `DEF-0025` | repaired-not-deployed | critical | Daily meme posts lacked a durable pre-send remote-write barrier | `INV-TXN-MEME-001` | `36edf036`, bounded | `6d5608f2` | not-deployed; observed `be882e81` |
| `DEF-0026` | assurance-weakness | assurance | External assurance omitted mandatory release-base-bound ledger validation | `INV-REL-001` | unknown | unfixed | not applicable |
| `DEF-0027` | repaired-not-deployed | critical | Confirmed regular posts could lose their bounded schedule plan | `INV-TXN-REG-001`, `INV-TXN-RECEIPT-001` | `9ae7e0f2` | `1b730410` | not-deployed; observed `be882e81` |
| `DEF-0028` | repaired-not-deployed | critical | Meme schedule fallback could permit a second meme on the same local date | `INV-TXN-MEME-001`, `INV-TXN-RECEIPT-001` | `9ae7e0f2` | `1b730410` | not-deployed; observed `be882e81` |
| `DEF-0029` | repaired-not-deployed | critical | Generic X create 3xx and 4xx outcomes were treated as definite non-success | `INV-API-001`, `INV-TXN-REG-001`, `INV-TXN-MEME-001`, `INV-TXN-REPLY-001`, `INV-TXN-HCTX-001`, `INV-TXN-RECEIPT-001` | `36edf036`, bounded | `1b730410` | not-deployed; observed `be882e81` |
| `DEF-0030` | repaired-not-deployed | critical | Post-replacement pending-receipt fsync failure did not establish a global barrier | `INV-TXN-REG-001`, `INV-TXN-MEME-001`, `INV-TXN-RECEIPT-001` | `1b730410` | `dd8aa52c` | not-deployed; observed `be882e81` |
| `DEF-0031` | repaired-not-deployed | critical | Daemon stopped rechecking delayed remote-write marker durability | `INV-TXN-REG-001`, `INV-TXN-MEME-001`, `INV-TXN-RECEIPT-001` | `dd8aa52c` | `30ca2b50` | not-deployed; observed `be882e81` |
| `DEF-0032` | repaired-not-deployed | critical | Remote-write marker disappearance was acknowledged as durable | `INV-TXN-REG-001`, `INV-TXN-MEME-001`, `INV-TXN-RECEIPT-001` | `7f76c113` | `2ad0f79f` | not-deployed; observed `be882e81` |
| `DEF-0033` | repaired-not-deployed | critical | Instance-lock namespace and continuous ownership were not proved | `INV-PROC-002` | `f0be0b5d` | `2ad0f79f` | not-deployed; observed `be882e81` |
| `DEF-0034` | repaired-not-deployed | critical | Fresh-process marker observation did not establish a blocking in-memory barrier | `INV-TXN-REG-001`, `INV-TXN-MEME-001`, `INV-TXN-RECEIPT-001` | `2ad0f79f`, bounded | `debc0799` | not-deployed; observed `be882e81` |
| `DEF-0035` | repaired-not-deployed | critical | A second restart could lose the sole remote-write barrier after marker disappearance | `INV-TXN-REG-001`, `INV-TXN-MEME-001`, `INV-TXN-RECEIPT-001` | `debc0799`, bounded | `78b5c5b3` | not-deployed; observed `be882e81` |
| `DEF-0036` | repaired-not-deployed | critical | Prepared receipt authority and historical-context receipts did not close the global preflight | `INV-TXN-REG-001`, `INV-TXN-MEME-001`, `INV-TXN-REPLY-001`, `INV-TXN-HCTX-001`, `INV-TXN-RECEIPT-001` | unknown | `5b0b6108` | not-deployed; observed `be882e81` |
| `DEF-0037` | repaired-not-deployed | critical | Unaudited activation and torn pair inspection could open remote-write permission | `INV-TXN-REG-001`, `INV-TXN-MEME-001`, `INV-TXN-REPLY-001`, `INV-TXN-HCTX-001`, `INV-TXN-RECEIPT-001` | unknown | `5b0b6108` | not-deployed; observed `be882e81` |
| `DEF-0038` | repaired-not-deployed | critical | Transport could outlive disappearance of the validated source receipt | `INV-TXN-REG-001`, `INV-TXN-MEME-001`, `INV-TXN-REPLY-001`, `INV-TXN-HCTX-001`, `INV-TXN-RECEIPT-001` | unknown | `7ebcc096` | not-deployed; observed `be882e81` |
| `DEF-0039` | repaired-not-deployed | critical | Ambiguous v2 media upload could fall back to a second v1.1 upload | `INV-TXN-REG-001`, `INV-TXN-MEME-001`, `INV-TXN-RECEIPT-001` | unknown | `7ebcc096` | not-deployed; observed `be882e81` |
| `DEF-0040` | repaired-not-deployed | critical | Byte-identical pathname replacement could retain stale transport authority | `INV-TXN-REG-001`, `INV-TXN-MEME-001`, `INV-TXN-REPLY-001`, `INV-TXN-HCTX-001`, `INV-TXN-RECEIPT-001` | unknown | `634fd6cd` | not-deployed; observed `be882e81` |
| `DEF-0041` | repaired-not-deployed | critical | Receipt retirement fsync failure could clear the last barrier without latching the daemon | `INV-TXN-REG-001`, `INV-TXN-MEME-001`, `INV-TXN-REPLY-001`, `INV-TXN-HCTX-001`, `INV-TXN-RECEIPT-001` | unknown | `634fd6cd` | not-deployed; observed `be882e81` |
| `DEF-0042` | repaired-not-deployed | critical | Destructive transaction helpers lacked verified caller lock authority | `INV-PROC-002`, `INV-TXN-RECEIPT-001` | unknown | `634fd6cd` | not-deployed; observed `be882e81` |
| `DEF-0043` | repaired-not-deployed | critical | Unsafe receipt namespaces and ambiguous JSON could be treated as absent or authoritative | `INV-TXN-REG-001`, `INV-TXN-MEME-001`, `INV-TXN-REPLY-001`, `INV-TXN-RECEIPT-001` | unknown | `634fd6cd` | not-deployed; observed `be882e81` |
| `DEF-0044` | assurance-weakness | assurance | Candidate-owned pytest hooks could forge internally consistent release evidence | `INV-REL-TRUST-001` | unknown | unfixed | not applicable |
| `DEF-0045` | active | medium | Exact exchange staging could require manual recovery after a safe hard exit | `INV-TXN-REG-001`, `INV-TXN-MEME-001`, `INV-TXN-REPLY-001`, `INV-TXN-HCTX-001`, `INV-TXN-RECEIPT-001` | unknown | unfixed | active at the ledger evidence cut-off; production presence not established |
| `DEF-0046` | repaired-not-deployed | critical | Stale confirmed receipt replay could regress newer post state | `INV-TXN-REG-001`, `INV-TXN-MEME-001`, `INV-TXN-RECEIPT-001` | unknown | `5a11bbf4` | not-deployed; observed `be882e81` |
| `DEF-0047` | repaired-not-deployed | critical | Path-bearing X bases could desynchronise prepared and literal create-route classification | `INV-API-001`, `INV-TXN-RECEIPT-001` | unknown | `5a11bbf4` | not-deployed; observed `be882e81` |
| `DEF-0048` | repaired-not-deployed | critical | Accepted legacy confirmed receipts could leave auxiliary provider work unblocked | `INV-TXN-REG-001`, `INV-TXN-MEME-001`, `INV-TXN-REPLY-001`, `INV-TXN-RECEIPT-001` | unknown | `5a11bbf4` | not-deployed; observed `be882e81` |
| `DEF-0049` | repaired-not-deployed | medium | Configured X upload origin was ignored by v2 media requests | `INV-API-001` | unknown | `ce970f81` | not-deployed; observed `be882e81` |
| `DEF-0050` | repaired-not-deployed | medium | Final pretransport pause could strand a definitely untransmitted media barrier pair | `INV-TXN-REG-001`, `INV-TXN-MEME-001`, `INV-TXN-RECEIPT-001` | unknown | `ce970f81` | not-deployed; observed `be882e81` |
| `DEF-0051` | repaired-not-deployed | medium | Post-media-handoff tweet pauses stranded definitely untransmitted main-post transactions | `INV-TXN-REG-001`, `INV-TXN-MEME-001`, `INV-TXN-RECEIPT-001`, `INV-PAUSE-001` | unknown | `fb8eb25f` | not-deployed; observed `be882e81` |
| `DEF-0052` | active | critical | A valid primary state file could silently outrank a newer valid backup | `INV-TXN-HIST-001` | unknown | unfixed | active at the ledger evidence cut-off; production presence not established |
| `DEF-0053` | active | critical | Historical-context recovery lacked a durable pre-remote versus remote-started phase | `INV-TXN-HCTX-001`, `INV-TXN-RECEIPT-001` | unknown | unfixed | active at the ledger evidence cut-off; production presence not established |
| `DEF-0054` | active | critical | Receipt readers and source binding did not prove one stable canonical private file generation | `INV-TXN-REG-001`, `INV-TXN-MEME-001`, `INV-TXN-REPLY-001`, `INV-TXN-HCTX-001`, `INV-TXN-RECEIPT-001` | unknown | unfixed | active at the ledger evidence cut-off; production presence not established |
| `DEF-0055` | active | medium | Durable public identifiers and nested source identities lacked exact type, range and basename constraints | `INV-TXN-REG-001`, `INV-TXN-MEME-001`, `INV-TXN-REPLY-001`, `INV-TXN-HCTX-001`, `INV-TXN-RECEIPT-001` | unknown | unfixed | active at the ledger evidence cut-off; production presence not established |
| `DEF-0056` | active | critical | First-install publication could expose partially established current-schema state without a durable in-progress barrier | `INV-TXN-HIST-001`, `INV-PROC-002` | unknown | unfixed | observed in production `be882e81` |
| `DEF-0057` | active | critical | Established historical-context history or outbox loss could be reinterpreted as empty state | `INV-TXN-HCTX-001`, `INV-TXN-HIST-001`, `INV-TXN-RECEIPT-001` | unknown | unfixed | observed in production `be882e81` |
| `DEF-0058` | active | critical | Remote-started historical-context outbox attempts were absent from the global remote-write barrier | `INV-TXN-HCTX-001`, `INV-TXN-RECEIPT-001` | unknown | unfixed | active at the ledger evidence cut-off; production presence not established |
| `DEF-0059` | active | critical | Core state and used-history readers followed or incompletely proved filesystem authorities | `INV-TXN-HIST-001`, `INV-PROC-002` | unknown | unfixed | active at the ledger evidence cut-off; production presence not established |
| `DEF-0060` | active | critical | Exact source-receipt retirement lacked a permanent completion proof and recoverable ledger exchanges could block startup | `INV-TXN-REG-001`, `INV-TXN-MEME-001`, `INV-TXN-REPLY-001`, `INV-TXN-HCTX-001`, `INV-TXN-RECEIPT-001` | unknown | unfixed | active at the ledger evidence cut-off; production presence not established |

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

At the ledger evidence cut-off, liveness is improved but startup and health
evidence still do not identify the loaded Git tree and generated generation
cryptographically.

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

### DEF-0017 — Duplicate JSON names in configuration and pause controls

Python's default JSON decoder keeps the final duplicate name. At the ledger
evidence cut-off it is still used for local configuration and runtime pause
control. Transaction receipt namespace and strict-JSON defects are tracked
separately by `DEF-0043`; this record does not claim that broader scope.

Chronology:

- 2026-07-01, `36edf036`: the initial config/state implementation used default
  JSON parsing.
- 2026-07-27, `370aee9`: the independent review explicitly recorded duplicate
  names as an unresolved gap.
- 2026-07-28, `be882e81`: unknown-name allowlists improved, but duplicate-name
  rejection did not land.

A minimal reproducer is
`{"disable_all":true,"disable_all":false}`. This remained active in the
recorded production observation and at the ledger evidence cut-off. Closure
requires a strict decoder and focused mutation tests for configuration and
pause-control documents.

### DEF-0018 — Strict release-control JSON

The reviewed candidate accepted duplicate names in registry input while the
ledger used a different parser. Release-control JSON therefore had ambiguous,
document-class-dependent semantics. This assurance finding is separate from
the still-open production-runtime parser defect in `DEF-0017`.

### DEF-0019 — External trust separation

The candidate owned the gate, registry, policy interpretation and pytest
evidence plugin that issued its release result. Candidate-owned checks cannot
be the authoritative trust root for that candidate; a separately committed,
hash-pinned external assurance implementation is required. `DEF-0044` records
the narrower concrete mechanism by which candidate-owned pytest hooks can
forge mutually consistent evidence; this record retains the broader trust-root
boundary.

### DEF-0020 — Private-root containment

The selected-path namespace protected named roots but left unrelated host-user
paths visible. A synthetic canary demonstrated read/write reach outside the
candidate and output roots. Closure requires a private root with only declared
read-only inputs and one bounded writable output.

### DEF-0021 — Descendant import isolation

Sanitising `PYTHONPATH` did not remove shared distribution roots from the
filesystem or interpreter defaults. A synthetic undeclared package remained
importable by a child. Controller, xdist worker and subprocess imports must be
confined by a materialised private dependency closure.

### DEF-0022 — Trusted validation IDs

The untrusted candidate registry could author free-form validation commands,
including a shell interpreter with dynamic `-c` code. External policy must own
executable templates; candidate input may name only stable validation IDs and
bounded test selectors.

### DEF-0023 — Runtime artefact-consumption detection

The reviewed detector checked a resolution value discovery never emitted and
compared string paths with `(path, hash)` tuples. A real resolved artefact
could consequently be labelled historical. Typed resolution records and a
normalised `set[str]` runtime inventory are required.

### DEF-0024 — Regular-post hard-crash window

An independent separate-process reproduction showed that a hard death after
simulated remote acceptance could leave restart state eligible for another
create. Commit `6d5608f2` repaired that original window by writing and fsyncing
an exact single-use attempt before X and retaining uncertainty across restart.
The repair is present at the `1b730410` evidence cut-off but is not deployed.
The distinct post-replacement fsync defect is recorded as `DEF-0030`.

### DEF-0025 — Meme-post hard-crash window

The daily-meme lane had the same pre-receipt hard-death window, and the
independent reproduction admitted two simulated remote IDs across restart.
Commit `6d5608f2` repaired that original window with the same pre-send durable
attempt protocol. The repair is present at the evidence cut-off but is not
deployed; `DEF-0030` records the later pending-receipt fsync boundary.

### DEF-0026 — Release-base-bound ledger validation

The archived external assurance run did not execute `ledger_validate`, and its
command policy did not supply `--release-base`. It therefore qualified a
candidate whose ledger evidence cut-off differed from the supplied release
base. The application ledger cannot certify the external-gate repair; closure
requires a separately frozen assurance commit, an exact base-bound result and
new independent review.

### DEF-0027 — Confirmed regular-post schedule finalisation

A confirmed regular post could persist protected post state without retaining
its already-selected bounded meme delay and next-quote schedule. Commit
`1b730410` repaired the plan-loss defect by binding those values before X and
replaying them locally. It remains unactivated. The directory-durability
boundary found in that repair is narrower and separately tracked as
`DEF-0030`.

### DEF-0028 — Daily-meme same-date barrier

A post-confirmation meme schedule failure could persist a one-hour fallback on
the same Europe/London date. Commit `1b730410` repaired that defect by binding
a next-local-date schedule and independently checking durable current-cycle
history before create. It remains unactivated; no live duplicate is asserted.

### DEF-0029 — Conservative X create outcomes

The four X create lanes treated selected generic HTTP errors as proof that the
remote write did not succeed. A status without a confirmed post identity does
not establish non-success after transmission. Commit `1b730410` applies one
fail-closed ambiguous-outcome rule, disables redirect following for creates
and retains the relevant sending barrier across regular, meme,
conversational-reply and historical-context lanes. It remains unactivated.

### DEF-0030 — Pending-receipt directory-fsync boundary

Commit `1b730410` could successfully replace an attempting receipt with an
exact confirmed pending-schedule receipt and then raise while synchronising
the parent directory. The caller rejected the visible pending state before
setting a global latch or durable marker, did not restore SIGINT and permitted
an unrelated reply preflight to reach its X-create boundary. Independent
synthetic tests reproduced this in both regular and meme lanes without a real
network action. Commit `dd8aa52` repairs this boundary by exact-byte
revalidation and explicit parent re-fsync, with pre-recovery latching,
cross-lane barriers and deferred-SIGINT recovery. It remains unactivated. Its
distinct delayed daemon-loop recheck defect is recorded as `DEF-0031`.

### DEF-0031 — Delayed marker-durability daemon recheck

The `dd8aa52` candidate retained SIGINT and an in-process write latch when an
ambiguity marker's parent-directory fsync failed, but the real daemon invoked
the durability helper only on the first one-shot pause-log iteration. A
transient first failure was therefore never retried on later blocked ticks;
the retained guard remained installed and a deferred controlled stop was not
delivered. Commit `30ca2b5` repairs the loop by rechecking durability on every
blocked tick while keeping the pause-status log one-shot. The real-loop
regression proves first-fail, second-success recovery and exactly one deferred
signal delivery without reaching any remote-action lane. The repair remains
unactivated; its distinct marker-identity defect is tracked as `DEF-0032`.

### DEF-0032 — Marker disappearance during durability acknowledgement

The marker helper and the existing-marker paths used by ambiguity recording
and confirmed-post persistence latching checked the marker only before
synchronising its parent directory. Independent fault injection removed the
marker inside that synchronisation call and durably committed an absent
namespace entry. All three paths could nevertheless report success; the main
helper cleared uncertainty and released deferred SIGINT, leaving a fresh
process with no marker barrier. The same gap covers inode replacement, content
mutation and type changes. This defect began in `7f76c113`, was extended to
confirmed-post durability in `acfc4f69`, and is ancestral to
observed production `be882e81`. Commit `2ad0f79` repairs the identity/content
acknowledgement and adds an offline-only, exact-hash reconciliation protocol
under the daemon's process-lifetime instance lock. The repair remains
unactivated. Independent review of that candidate found the distinct
fresh-process latch/uncertainty defect tracked as `DEF-0034`; no live incident
or duplicate post is asserted.

### DEF-0033 — Instance-lock namespace and continuous ownership

The process-lifetime lock followed its pathname during acquisition, did not
require one ordinary filesystem link and did not bind the acquired descriptor
to a stable path identity. Later checks re-applied `flock` to that same open
file description, which can reacquire an accidentally lost lock instead of
proving uninterrupted ownership. Normal remote-operation preflight therefore
did not independently exclude a second lock namespace or concurrent offline
reconciliation. The defect was introduced in `f0be0b5`, whose parent
`4f268ed` contains no instance-lock implementation; it is ancestral to
observed production `be882e81` and remained active through the independently
reviewed `ee7539c` predecessor. Commit `2ad0f79` repairs the defect with
state-directory identity,
no-follow single-link acquisition, independent OFD ownership and a
directory-identity singleton enforced before every non-read remote operation
and by offline reconciliation. The repair remains unactivated. No live
concurrency incident or duplicate post is asserted.

### DEF-0034 — Fresh-process marker observation and uncertainty barrier

The unactivated `2ad0f79` candidate correctly rejected a marker which
disappeared during parent-directory synchronisation, but a restarted process
could begin with both process globals false. Marker discovery did not seed the
incident latch before fallible inspection, and marker-durability uncertainty
was not itself a blocking predicate. Independent fault injection began with a
valid pre-existing marker, removed it during the real parent-directory
`fsync`, and then reached a sentinel historical-context remote boundary.
No network, provider, X or production action occurred. Commit `debc079`
repairs that same-process defect: observation seeds both process barriers,
either barrier blocks direct and scheduler paths, and inspection or
acknowledgement failure retains them. The repair is not deployed. Independent
literal-process review then showed that those memory-only barriers do not
survive an additional abrupt process loss; that distinct defect is
`DEF-0035`.

### DEF-0035 — Second-restart durable-barrier loss

The unactivated `debc079` candidate blocked the first process which observed a
legacy ambiguity marker disappear, but a hard exit followed by a literal
second interpreter could begin with false process globals and no durable
barrier. Commit `78b5c5b` repairs the defect by publishing and revalidating a
same-inode `ambiguous_post_outcome.restart_barrier.json` successor before
legacy-marker acknowledgement and retiring it last under supported offline
reconciliation. The affected range ends at `debc079`, the fix parent's exact
commit. The repair is committed by the evidence cut-off but is not deployed.

### DEF-0036 — Prepared receipt and historical-context global-barrier gaps

The pre-freeze line treated an in-memory prepared regular, meme or
conversational-reply record as authority after its owning durable receipt
disappeared and omitted historical-context receipts from the global barrier.
Commit `5b0b610` requires the exact durable owner, blocks unrelated lanes on
every historical-context receipt namespace entry, closes raw transport
bypasses and strictly parses historical-context receipts. Its affected range
ends at parent `a65c91b`. The repair is committed by the evidence cut-off but
is not deployed. Remaining receipt namespace and strict-JSON problems in the
other lanes are recorded separately as `DEF-0043`.

### DEF-0037 — Unaudited activation and torn pair inspection

The pre-freeze line exposed an unaudited `new_install` helper that could open an
established state directory and could compose an activation sentinel and audit
from different namespace generations. Commit `5b0b610` makes `--initialise`
leave remote writes disabled, requires stopped external-attestation activation
and cross-revalidates both activation pathname identities after both reads.
Its affected range ends at parent `a65c91b`. The repair is committed by the
evidence cut-off but is not deployed.

### DEF-0038 — Receipt disappearance after transport validation

At `40ab83e`, a lane could validate its durable source receipt and then lose
that pathname before remote transport. A hard exit after possible acceptance
could therefore leave the next process without an independent barrier. Commit
`7ebcc096` adds a payload-, source- and inode-bound transport journal and
restart fence at the final transport boundary. The repair is committed at the
earlier `7ebcc096` point in the current evidence-cutoff lineage, tested there,
and not deployed.

### DEF-0039 — Ambiguous media fallback

At `40ab83e`, broad v2 media-upload exception handling could automatically
attempt v1.1 after the v2 service might already have accepted the bytes.
Commit `7ebcc096` removes that automatic fallback, adds an immutable durable
media receipt/fence and hands the confirmed media ID into the main journal
before post transport. The repair is committed earlier in the current
evidence-cutoff lineage, tested at `7ebcc096`, and not deployed.

### DEF-0040 — Byte-identical pathname replacement

Review of exact cut-off `7ebcc096` found transaction decisions that retained
byte or digest authority without one filesystem identity across every later
exchange, transport and retirement boundary. A cooperating process could
replace a pathname with a byte-identical inode and preserve stale authority.
Commit `634fd6c` binds source, journal, fence and retirement decisions to stable
no-follow filesystem identities and rejects byte-identical replacement
generations. Its affected range ends at `7ebcc096`; the fix-bound regressions
are committed, but the repair is not deployed.

### DEF-0041 — Retirement fsync and latch ordering

At `7ebcc096`, a destructive receipt or fence unlink could remove the final
visible barrier before the following parent-directory `fsync` failed, without
a guaranteed process-lifetime latch already set. This creates uncertainty for
both later ticks and crash restart. Commit `634fd6c` latches before destructive
retirement and leaves interrupted exact-retirement states restart-visible. Its
affected range ends at `7ebcc096`; the repair is committed and not deployed.

### DEF-0042 — Caller lock authority

At `7ebcc096`, destructive transaction helpers relied on call-site convention
instead of requiring proof that the current process still owned the daemon's
instance lock. This is a cooperative-process serialization defect, not an OS
security promise against arbitrary same-UID mutation. Commit `634fd6c` requires
a process-bound verifier-backed mutation authority at destructive entry points.
The fix is committed with bound tests and is not deployed.

### DEF-0043 — Unsafe receipt namespaces and strict transaction JSON

At `7ebcc096`, remaining regular, meme and conversational receipt paths still
used ordinary existence, open and JSON decoding operations. Unsafe namespace
types and duplicate, non-finite or non-canonical JSON could be treated
inconsistently, accepted as authority or overwritten. Historical-context
strict parsing is already repaired under `DEF-0036`, and configuration/pause
JSON remains `DEF-0017`. Commit `634fd6c` adds stable no-follow, owned ordinary
single-link and canonical JSON enforcement across the remaining receipt
surface. Its fix is committed and not deployed; narrower gaps found at
`4e548b0a` are recorded as `DEF-0054` and `DEF-0055`.

### DEF-0044 — Candidate-owned pytest evidence forgery

At `7ebcc096`, candidate-owned code selected the loaded pytest evidence plugin
and knew the JUnit and structured-event destinations consumed by the gate. A
candidate could therefore produce internally consistent results without an
independent observer proving the declared tests ran. This is the concrete
mechanism beneath the broader `DEF-0019` trust-root weakness. Only a separately
pinned external runner/plugin can close it; it remains an assurance weakness.

### DEF-0045 — Fail-closed exact-transition staging liveness

At `7ebcc096`, hard exit around `RENAME_EXCHANGE` could leave a random staging
entry which correctly blocked the next process but had no strict
self-describing resumer or complete operator procedure. This is an
availability/manual-recovery weakness: the observed state is fail-closed, and
no duplicate-safety failure is claimed. It remains active at `4e548b0a`; no
committed automatic resumer or complete bounded operator procedure exists.

### DEF-0046 — Stale confirmed receipt replay

At `7ebcc096`, stale current-schema regular receipts could erase newer
quote/image cycle histories, while stale confirmed meme receipts could move
the current main identity, meme epoch and future schedule backward. The latter
could clear the effective same-day guard and make another meme appear due.
Synthetic replay reproduced both behaviours without any remote operation.
Commit `5a11bbf` makes replay monotonic and binds both regressions to the fix.
The repair is committed and not deployed.

### DEF-0047 — Path-bearing X base route disagreement

At `7ebcc096`, the X API and upload base normaliser accepted path-bearing
values and Requests classified protected create routes from the concatenated
prepared URL while other authority checks retained the caller's literal path.
A path prefix could therefore make tweet or media create-route classifications
disagree. Commit `5a11bbf` requires origin-only X configuration and exact
prepared/literal protected-create agreement. The repair is committed with
fix-bound tests and is not deployed.

### DEF-0048 — Accepted legacy receipt global-barrier gap

At `7ebcc096`, the global main and conversational receipt predicates blocked
only sending or invalid states. Accepted legacy full regular/meme receipts and
confirmed conversational receipts could therefore remain pending local
reconciliation while unrelated auxiliary provider or scheduler work was not
globally blocked. Commit `5a11bbf` treats every non-absent source receipt as a
global barrier, with only the exact sole-owner reconciliation path permitted.
The repair is committed and not deployed.

### DEF-0049 — Configured upload origin ignored

At `7ebcc096`, `X_UPLOAD_BASE_URL` was parsed, normalised and displayed by
configuration checks, but every X request URL was still constructed from
`X_BASE`. A distinct configured v2 media-upload origin therefore had no
effect. Local distinct-origin review reproduced the routing mismatch without
contacting a remote endpoint. Commit `ce970f8` routes only the exact media
create to the upload origin, keeps reads and tweets on the API origin, and
binds distinct-origin regressions. The repair is committed and not deployed.

### DEF-0050 — Final pretransport pause media-barrier recovery

At `7ebcc096`, a supported pause becoming active after durable media
receipt/fence publication but before final transport correctly prevented the
request, but left the definitely untransmitted pair as a global manual
recovery barrier. The pair preserved duplicate safety; the defect is bounded
to availability and avoidable operator recovery. Commit `ce970f8` issues an
exact process-local, single-use cleanup authority after durable publication;
reconstructed, consumed, unsafe and interrupted states remain fail-closed. The
repair is committed and not deployed.

### DEF-0051 — Post-media-handoff tweet pause recovery

At `7ebcc096`, the regular quote and daily meme lanes handed confirmed media
to a durable main-post source before `create_post`, but the definite
non-success predicate returned false even for a local pretransport pause. The
source therefore remained a global manual-recovery barrier although no tweet
request was transmitted. In the descendant handoff protocol under review, an
initial `create_post` pause left both the prepublished prepared pair and its
source; at the final `x_request` pause the existing catch aborted the
journal/fence pair, but the false caller predicate still left the source.
Commit `fb8eb25` classifies only the local pretransport pause as definite
non-success, aborts the exact prepared pair and retires the exact main source.
The repair is committed with lane-level regressions and is not deployed.

### DEF-0052 — Valid primary versus newer valid backup ordering

At `4e548b0a`, `load_state` returned a schema-valid primary immediately and
consulted `bak1` only when the primary was absent or invalid. A valid but older
primary could therefore silently outrank divergent newer recovery state and
roll protected post identity, histories or schedules backward. The post-cutoff
candidate refuses valid primary/latest-backup divergence, but no durable
monotonic generation order yet permits safe automatic selection. The defect is
active at the cut-off and no fix commit is recorded.

### DEF-0053 — Historical interrupted-attempt monotonicity

At `4e548b0a`, an interrupted historical-context claim could become retryable
when the terminal history entry that had proved completion disappeared. If the
original create succeeded remotely, local history loss could therefore expose
the same obligation to another create. The post-cutoff regression preserves
attempted status and keeps the worker blocked, but the proposal remains outside
the evidence boundary. The defect is active and was never deployed in this
candidate transaction shape.

### DEF-0054 — Canonical private receipt sources and exact identifiers

Review of `4e548b0a` found common, conversational and historical receipt readers
and journal source binding that did not uniformly require canonical durable-
writer bytes, current-owner private files and exact JSON string identifiers.
Semantically valid alternate bytes, permissive modes or numeric IDs could be
accepted as local recovery authority. Post-cutoff tests cover each boundary,
but their repair has no committed identity in this ledger; the defect remains
active and was not deployed.

### DEF-0055 — Nested journal and media source identity constraints

At `4e548b0a`, nested journal source identities and media handoff owners did not
uniformly reject boolean, negative, oversized or non-string proof fields.
Most mismatches remain blocking, but the implementation could not claim that
every accepted nested proof was an exact canonical writer-produced object.
Post-cutoff regressions require exact non-boolean bounded integers and string
digests; no fixing commit is inside the evidence boundary, so the medium-
severity defect remains active and was not deployed.

### DEF-0056 — First-install publication and rollback atomicity

At `4e548b0a`, explicit initialisation did not acquire the process-lifetime
instance lock before trusting namespace absence, did not publish a durable
in-progress sentinel before its first data write and added some cleanup paths
only after fallible publication. A hard exit or post-replacement error could
therefore leave marker-less current-schema state which the narrower
established-installation predicate accepted as complete. Post-cutoff candidate
tests require lock-first inspection, sentinel-first publication, complete
pre-registration of rollback paths, explicit history/outbox creation and
fail-closed treatment of interrupted new installs. No fixing commit is inside
this ledger boundary.

### DEF-0057 — Established historical-context authority loss

At `4e548b0a`, an apparently absent historical-context history or outbox was
read as an empty default, production factories did not require an established
authority, and the installation completeness check omitted both files.
Disappearance or failed namespace inspection could therefore hide durable
history or obligation loss as a valid empty store. Post-cutoff candidate tests
separate explicit one-time creation from require-existing production readers
and require both authorities in an established installation. No fixing commit
is inside the evidence boundary, so the defect remains active.

### DEF-0058 — Remote-started historical-context outbox global barrier

At `4e548b0a`, a `context_reply_attempting` outbox row was not itself part of
the global remote-write barrier. If its source receipt and transport journal
were absent and the historical-context runtime was unavailable, an explicit
remote-started or legacy phase-absent attempt could remain durable while an
unrelated remote-write lane saw no blocker. Post-cutoff candidate tests require
the true and legacy phase forms to block globally while preserving bounded
local recovery for an explicit false phase. No fixing commit is inside the
evidence boundary, so the defect remains active.

### DEF-0059 — Stable no-follow core state authority

At `4e548b0a`, required-installation checks, `load_state` and `load_used_set`
did not uniformly prove that their source was one current-owner, single-link
ordinary file opened without following links and stable through the final
pathname check. A symlink, multi-link or mutating core state or used-history
authority could therefore supply bootstrap, schedule or duplicate-suppression
state without one exact file-generation proof. Post-cutoff candidate tests use
a shared stable no-follow reader and cover symlink and same-inode mutation
boundaries. No fixing commit is inside the evidence boundary, so the defect
remains active.

### DEF-0060 — Permanent exact-retirement proof and startup recovery

At `4e548b0a`, final source-receipt retirement could remove its last transient
completion artefact without publishing a permanent generation-bound record.
Later code could therefore lose the durable evidence needed to distinguish
exact completion from lost authority. The first post-cutoff permanent-ledger
proposal closed that safety-proof gap but introduced a separate fail-closed
availability problem: establishment validation rejected an exact crash-left
ledger exchange before the authorised startup resumer could complete it. The
current uncommitted proposal publishes a monotonic exact completion ledger
before final cleanup and recovers only a structurally exact exchange beneath a
current schema-3 activation and the verifier-bound instance lock. Missing,
malformed or unrelated ledger states remain blocking. No fixing commit is
inside the evidence boundary, so the defect remains active; arbitrary hostile
same-UID namespace mutation is outside the cooperative/crash safety claim.

## Unknown-value policy

The following are intentionally unknown rather than inferred:

- `DEF-0005`: no single umbrella introduction or fix commit; child records are
  authoritative.
- `DEF-0006`, `DEF-0011`, `DEF-0015`, `DEF-0017`: no earlier repository or
  audited last-known-good implementation exists for the stated property.
- `DEF-0013`, `DEF-0014`: an absent environment/release control has no
  defensible introducing Git commit.
- `DEF-0040` through `DEF-0043` and `DEF-0046` through `DEF-0051`: introduction
  bounds remain unknown, but their exact fixing commits, fix parents and bound
  regressions are recorded rather than inferred.
- `DEF-0044`, `DEF-0045`, and `DEF-0052` through `DEF-0059`: introduction and
  fix commits remain unknown. Their explicit unfixed ranges end at exact
  evidence cut-off `4e548b0a` until post-merge regeneration records a fix.

These explanations are recorded per field in `defect_ledger.json`; consumers
must not replace them with guessed commits or deployment claims.
