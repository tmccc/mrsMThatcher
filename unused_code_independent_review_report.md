# Unused-Code Cleanup Independent Review

Date: 18 July 2026

## Executive Verdict

The cleanup is safe for separately authorised activation. I found no deleted
production call, dynamic dispatch target, operational entry point, or receipt
barrier that needs restoring. The production quote and meme paths still fail
closed around confirmed-post receipts, and the current quotation and image
selectors retain their no-repeat, eligibility, seasonal, generated-image and
semantic-veto behaviour.

One test-quality defect was found and corrected. The deleted
`validate_reply_strategy_config` test exercised a dead duplicate validator rather
than the active production validator. Three parameterised regression cases now
prove that `mrsMThatcher2.validate_runtime_config_values()` rejects disabled
`accuracy_first`, `completed_packets_only`, and `no_hashtags` safety flags while
the reply strategy is enabled. No production function was changed by this
review.

## Initial State

Before the independent edit:

- tracked diff: 68 files, 51 insertions, 557 deletions;
- untracked prior report: `unused_code_audit_report.md`;
- `git diff --check`: passed;
- service: `active/running`;
- wrapper/MainPID: `1075041`;
- Python child: `1075042`;
- start time: `Sat 2026-07-18 18:43:35 BST`;
- systemd restart count: `0`;
- cgroup: `/user.slice/user-1000.slice/user@1000.service/app.slice/mrsMThatcher.service`.

The pre-review hashes equal the final hashes in the appendix except for
`tests/test_unit_helpers.py`: pre-review
`883db0895ddb56fc257ef6dcc23544947374ab50b35f62bd6ebc424aeffaaae3`,
final `c6fc64fded75e45de08e76900f4dedf09c57ea52e9a537e45e7090d12ceb5456`.

## Exact Removed Symbols

| File | Removed symbol | Assessment |
|---|---|---|
| `mrsMThatcher2.py` | `quote_metadata_for_line` | Obsolete line-index adapter; active metadata lookup uses stable quote hashes. |
| `mrsMThatcher2.py` | `block_if_unresolved_meme_post_receipt` | Uncalled wrapper; `post_next_meme` directly detects dual receipts, reconciles a meme receipt, and blocks a regular receipt before media work. |
| `mrsMThatcher2.py` | `block_if_unresolved_main_post_receipt` | Uncalled aggregate wrapper; active startup and regular-post paths call `reconcile_main_post_receipts`. |
| `mrsMThatcher2.py` | `choose_unused_line` | Test-only tuple adapter over `choose_unused_line_candidate`. |
| `mrsMThatcher2.py` | `available_image_basenames` | Superseded unscored all-image cycle; production uses the currently eligible image cycle. |
| `mrsMThatcher2.py` | `choose_random_unused_image` | Abandoned random image selector; production uses scored quote/image matching. |
| `mrsMThatcher2.py` | `choose_unused_image` | Test-only tuple adapter over the abandoned random selector. |
| `reply_strategy.py` | `DEFAULT_REPLY_STRATEGY` | Duplicate of the production-owned configuration in `mrsMThatcher2.py`. |
| `reply_strategy.py` | `validate_reply_strategy_config` | Dead duplicate validator; active validation remains in `mrsMThatcher2.py`. |
| `reply_strategy.py` | `reply_is_repetitive` | Boolean adapter; production retains diagnostic `reply_repetition_reason`. |
| `quote_image_metadata_remediation.py` | `file_identity`, `markdown_escape` | Uncalled offline helpers. |
| `semantic_alignment/gemini_fallback.py` | `mark_interrupted_sending_ambiguous` | Superseded by the worker `_load()` resume path, which performs the same fail-closed transition before transport selection. |
| `semantic_alignment/quote_research_retry_execution.py` | `write_retry_validation_report` | Unwired historical report generator with no CLI, dispatch, wrapper, test, or external caller. |
| `semantic_alignment/relation_aware_veto.py` | `_sha256_directory`, `_normal_terms` | Uncalled research helpers. |
| `semantic_alignment/thatcher_image_hunt.py` | `_title_key` | Uncalled discovery helper. |

The remaining changes remove 106 unused imports and 15 unused local assignments.
I reviewed every non-import deletion. Removed assignments did not contain a
provider call, mutation, selector invocation, file write, or other required side
effect. The `match` to `match_labels` rename in
`semantic_alignment/first_impression_validation.py` is behaviour-preserving.

## Receipt Barrier Evidence

The active call graph is:

```text
main startup
  -> reconcile_main_post_receipts
       -> dual-receipt conflict: raise
       -> reconcile_regular_post_receipt
       -> reconcile_meme_post_receipt

post_random_quote / --test-post-quote
  -> reconcile_main_post_receipts before selection or upload
  -> confirmed X post
  -> write_regular_post_receipt (durable, self-validating, refuses either existing receipt)
  -> durable quote/image/state persistence
  -> remove receipt only after persistence

post_next_meme / --test-post-meme
  -> dual-receipt conflict: raise
  -> reconcile_meme_post_receipt before selection or upload; return without reposting
  -> block_if_unresolved_regular_post_receipt
  -> confirmed X post
  -> write_meme_post_receipt (durable, self-validating, refuses either existing receipt)
  -> durable state persistence
  -> remove receipt only after persistence
```

This covers normal scheduling, startup recovery, retries, direct test commands,
interrupted receipt writes, failed state persistence, failed receipt removal,
malformed receipts, simultaneous receipts, and replay without a second X post.
Receipt writers remain a second fail-closed precondition after remote
confirmation. All tests use temporary paths and fake local APIs.

## Selector And Reply Safety

- Quote selection still uses `choose_unused_line_candidate`, completed-research
  exclusions, stable hashes, seasonal weighting, cycle reset, and duplicate-text
  deduplication. The 13 attribution removals and six unresolved packets remain
  excluded by the active research gate.
- Image selection still uses `available_currently_eligible_image_basenames`,
  production scoring, cycle-boundary avoidance, generated-image spacing and
  identity policy, and semantic-veto shadow observation. No scoring body or
  tie-break was changed.
- Current tests directly cover no-repeat until exhaustion, cycle reset,
  state persistence, missing-image history, seasonal eligibility, generated
  spacing, semantic-veto non-enforcement, and failure rollback.
- Reply validation still calls `reply_repetition_reason` and retains diagnostic
  exact, highly-similar, and canned-response rejection. All factual,
  clarification, terminal-thread, grounding, budget, and cap suites passed.
- Active runtime configuration remains fail closed. The new test is direct
  coverage of production validation, not a replacement helper.

## Dynamic And External Consumer Audit

An exact-name search for every removed symbol covered the complete repository,
hidden tracked sources, string literals, tests, documentation, shell/Python
files under `/home/tonym`, relevant shell/Python files under
`/disks/disk1/etc`, `/usr/local/bin/runMrsMThatcher2`, and the systemd user unit.
Secret and environment files were excluded. The only remaining matches were the
prior audit report describing the removals.

No removed name appears in `getattr`, callback registration, a dispatch table,
string import, CLI command, monkeypatch target, systemd configuration, or shell
wrapper. `/usr/local/bin/runMrsMThatcher2` invokes the symlinked
`/usr/local/bin/mrsMThatcher2.py`; the user unit supervises only that wrapper.
No external operational compatibility defect was found.

## Deleted-Test Assessment

The five deleted selector tests exercised only deleted compatibility wrappers.
Their production invariants have stronger direct coverage through the structured
selectors and posting path. The deleted `reply_is_repetitive` assertion was
redundant with exact/similar/canned rejection tests against
`validate_reply_decision` and `reply_repetition_reason`.

The deleted duplicate-config test was not valid evidence for production. This
review added `test_active_reply_strategy_validator_requires_fail_closed_safety_flags`
with three cases against the active validator. No other missing direct safety
coverage was identified.

## Activation Risk

| Modified production file | Current process loading | Reload behaviour | Activation risk |
|---|---|---|---|
| `mrsMThatcher2.py` | Loaded at process start; running process retains the old module | No source reload or file watcher | Next restart removes seven unreachable helpers; active receipt and selector bodies are unchanged. Low risk. |
| `reply_strategy.py` | Imported from local call sites and then cached in `sys.modules`; whether every lazy path imported it before this review cannot be observed without process injection | No explicit reload | A previously unvisited first import could read the edited file before restart, but only three unreferenced symbols were removed. Active behaviour is unchanged. Low risk. |

All other modified non-test files are offline research, analysis, recovery, or
diagnostic commands and are not imported by the production bot. Tests are not
loaded in production. No configuration or data file changed.

## Validation

Commands and results:

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_unit_helpers.py tests/test_integration_harness.py \
  tests/test_reply_strategy.py tests/test_fail_safe_bootstrap_and_control.py \
  tests/test_quote_attribution_cleanup.py \
  tests/test_generated_identity_policy_production_scoring.py \
  tests/test_original_editorial_shadow_scoring.py \
  tests/test_quote_image_semantic_veto_shadow.py \
  tests/test_simulate_regular_post_futures.py
=> 816 passed, 1 skipped in 301.82s

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
=> 1783 passed, 1 skipped, 3 pre-existing deprecation warnings in 689.74s

python3 -m py_compile $(git diff --name-only -- '*.py')
=> passed

ruff check --select F401,F811,F821,F841 $(git ls-files '*.py')
=> all checks passed

git diff --check
=> passed
```

The single skip is expected. Warnings are the existing Starlette/httpx warning
and two Beautiful Soup/lxml `strip_cdata` warnings.

## Final Diff And Files

Final tracked diff: **68 files changed, 72 insertions, 557 deletions**. The
21 additional insertions are the independent active-validator regression test.
No production code was added or altered during this review.

Final modified tracked file hashes and complete file list:

```text
fa3832a6e1792fdee27e46f3bb854ef8e01452f83b1b3e6be6a83418d778c5a8  analyse_first_impression.py
cef9113bed50f9ed165d20e33fd70d030efccee9b3bd35940e6d783012f7b3c4  analyse_first_impression_validation.py
d98f101590cc3c2f871820985a4e2af95fb9fd5763cdcd7a10d8492ddc917426  analyse_generation_prompt_pilot.py
96a036d19f9d2182c494dedcd0f7f709c4a0ec10d6dc239cdabb9332e29f3b37  analyse_improved_pairwise_readiness.py
96940db6717b7a2c1e0978942f85644650b0b69df35917a925606003821588de  analyse_meta_critic_validation.py
6a052d5b4934d27be00d107ec1a8eb26a9860d41b87fd74275d24f1c2f1862bf  analyse_mrs_assets_xai_v4.py
fc98bdf2e1fd9099d6f2603d90a861baf65ec03f36f22699c16bed58a5578f52  analyse_pairwise_correction.py
de1691e90d5713c2227cdc9eacfa04da1b0927ddf777f7b76f46bc9d5e4f7e33  analyse_pairwise_validation_results.py
8e421225f0a02cd0f35c7ad222996c0625c5599ac4b9e8764251e23a1656798f  analyse_provider_outliers.py
afa204c13164f471c122fa8d622ee241fcf818e528ed67b4dea447e13770289e  analyse_semantic_alignment_large.py
486cf82dc88a3a05913e05147faac6f0862e1a12f0f4c9478beae5907363e41e  analyse_semantic_alignment_xai.py
d152a3024145337df322cd1842b3fa9ecce1eb78aa750526c93ea0cffa1b6ab3  calibrate_semantic_alignment.py
b44a98398c514bbba2f62ddb8ab26bd2e4d3f70dbbb9a119773c28e872cc8da3  check_and_fix_thatcher_faces_xai.py
dc79d907e5fc0311dc45834eddd84234bdcd69b1f37a7bfa0b74b81cdcd9508c  finalize_semantic_large_bakeoff.py
5938ae9329ca533471ea5a02fbb262127574e22432fe6d622c55528c33de9225  mrsMThatcher2.py
689ba9ddd6179b39fd1c01d00139f2c7f908282a57798487fcd447b1a0ae9615  package_openai_corpus_for_upload.py
0dda8aa58d45d5568afa31ead65638847016ba485368ed1d706deaa7331e22f1  quote_image_metadata_remediation.py
38ae145955de99893bd5e8947bab68d9368532c1ff4f7a2f35afb2578740ebf1  quote_image_selection_harness.py
e5faad115e4e88fc465dca900fc04d176c58f630cfa647c6cb777903d8572d38  recover_gemini_exhausted.py
785d76df79f09b9fe33c03eaa45223f24490351c5ecffd81e6a0c1f7692b692e  recover_gemini_vertex.py
1cfb9e00d57e4544f6dae342c7ba433ccb219aa499d13639c9e96473358b034c  reply_strategy.py
7d354733d04de05b831a0cc2cb7acc3f236d2e1f6b9e84a4f87ae9006044ddec  run_improved_pairwise_pilot.py
1fc51e200b39de31f22e283317f18aa6452bfb4241eb3ce2228b20b06165acb0  run_pairwise_calibration.py
f33e4bf8be673e3180164ba87bd7aa494dc0a119cd463b6e9cca8551efc9384f  semantic_alignment/bakeoff.py
2bb805935c5392ef5d1b4afeb87d8f4cabdec402611bca981ca3be40c74dafb5  semantic_alignment/discovered_image_preparation.py
c02a06bbaf10f6a27a50936e6c41f10dd96ecdcab1ecb41805b4ad52cb1bdeda  semantic_alignment/first_impression.py
85fcb094e58ebf447acea040cbf7db5fe42afe867cdf008a90458257cabdb8d7  semantic_alignment/first_impression_validation.py
8aedd517f685a57f3a1cc4f2f41e1924bf623e2e6cecb45db1e60dbc7fb8f813  semantic_alignment/gemini_fallback.py
a25b7244f5fe30d73e8d9384ebe78c20cfd0bb4b3b78902ba1437e61c012e4eb  semantic_alignment/generation_prompt_pilot.py
cf25a058e4fb2b7357b067686fa602c5dc881d78a9e83c363e97f924b854c54d  semantic_alignment/image_quote_provider_compatibility.py
d1dbede79022febe82c726397070701d95388afa597a6e4be723f3168497cd76  semantic_alignment/large_bakeoff.py
e0890b5296460b802aa9f31a5a2c49e970e3589b87069401402cc098be51db67  semantic_alignment/openai_quality_trial.py
487106c6bb40c83acaae9b6cc723dfea0d8c377c060acd2ca828ef7783fefe7b  semantic_alignment/pairwise_validation.py
e3e86993576e00e6c8be8fceebc66d0400ab4567e845841c72897934cf5f54d8  semantic_alignment/pipeline.py
1dee8ff386e9c5cfae9c7d165cfd57ed2ca8281de7c6c2c5b86311cb28620f6d  semantic_alignment/provider_outliers.py
2e3ef666cd376908d2e13c5a42b7d3f06f0ca30a23c51544bf0ea12a01e6fb58  semantic_alignment/quote_research_closure.py
402fe2cc7e4b164a52a150a8490f3fe9c799f476c3b2ca122ce596f6caaab9e1  semantic_alignment/quote_research_corpus.py
53b63df7b7ef7f021eef620bb8b755282109b2b7944793f071a039b4686f3eae  semantic_alignment/quote_research_gemini.py
a443384a1e9ddb83a0f687e5ae16eaf38cc3aa80e369e7e93e589ae34993ffc8  semantic_alignment/quote_research_retry_analysis.py
7818be9c33d2e51516a314c91d69a4d5306cc0c3075996072efa0cb7bb40025e  semantic_alignment/quote_research_retry_execution.py
de493c27578d863624f6ac977ffcadd838c78646362a974568d410511404995c  semantic_alignment/relation_aware_veto.py
1dcc409a47bc26ce7d6b2e0f5ffcc50e023d89468aa6677a156db5ee1b01b461  semantic_alignment/thatcher_image_hunt.py
007ce07fc01b849e0e1fa83d0036e2c6c8ea18745725e5ce2f89341e525489d1  semantic_alignment/vertex_recovery.py
151d04dbd3f552867984c410c3d0bc5fe9e50c36530d78fdc4cd8c414cd5b790  semantic_alignment/visualisability_audit.py
87466cb362cce2a3602c72710d8a58ee999498f755c613448b8d12d11bf2194d  tests/test_analyse_counterfactual_simulation_dataset.py
ede194b5de7c1a532a83c8ed9a50e1f86cc26ce1ba0e33e5513778162fcf87f1  tests/test_first_impression.py
3080389c71a9f0e99617fce7bce4c29ed0503813ac4838db96c901d10835d1ad  tests/test_gemini_transport_fallback.py
9d98bf1f8db58527e7e80a688bc922b9770f4b43d99f1419dcd1530583a2dcfa  tests/test_generated_image_pool_runway_digest.py
d0bdac62831af9d0a5a538b59ed1fc9b4a6d29678244a0758ddf9e788bd81b55  tests/test_generated_image_review_app.py
bfd4913810a8ddbf00fe3b4cb10aa0ab9a1c6520f3baedc8f447e980f605a0d0  tests/test_generation_prompt_pilot.py
7ab9982bb64adaef1a3c7b1f580ecb01fadddffd365f293d83bffe32ff691ffd  tests/test_image_provider_trial.py
e4791e8ce7808b157ce182188e4075534991253dd0a0da24823796a6b7962dcd  tests/test_quote_attribution_cleanup.py
e29111101814d38e2ed7454662c12df0c01d047c362d6560732c4c320ee5cc4f  tests/test_quote_image_selection_harness.py
1f25666c1bfbfcd7b8006f43f37a84526aa03aec63b1b3be0cccd3aafe5f95ea  tests/test_quote_research_corpus.py
0c51207194efd53045f2a913b106c707f910b7a06ef8df97736c8f6aceb2aeed  tests/test_relation_aware_veto.py
600814a03ff728b9d6f43df582d181cdd3afdc9c1d785cd982b2092848bed1df  tests/test_reply_strategy.py
60ea9c2d759acdf076b6ab96d4561125191901a0cfecf6428ce5d137807c5abd  tests/test_semantic_alignment_bakeoff.py
364769a1b6415f9343c25755982100852c0ac0a75444aa4d8638657cc316a1f3  tests/test_semantic_alignment_calibration.py
1d00434ca2c2f10beffd96009d3b23ddca94da4918d22b49d7445e3f99de9578  tests/test_semantic_alignment_pipeline.py
e2d929cbb8c084d554c5487a1e755dd44950c10f97e96d9e9fd41d733ad9c803  tests/test_semantic_large_bakeoff.py
d76d56f2806d9f23e3a85ccbca0b9563465c7bc1b759752966adf9815d93b237  tests/test_semantic_meta_critic.py
c6fc64fded75e45de08e76900f4dedf09c57ea52e9a537e45e7090d12ceb5456  tests/test_unit_helpers.py
b54dfcca9aafbb465f16a0e3ca3e75e13a5547dc12ca072b070820e3ad87f738  tests/test_vertex_gemini_recovery.py
566008cc168e02b4c3b9fa6a5252daa4a41c78da36eab6c772ce09f3c63d2ac2  tools/analyse_counterfactual_simulation_dataset.py
0a9f5993590de603375951ff8b88926edc5812b8b233a1a92e090aa4cf0f6f1e  tools/backtest_original_editorial_matching.py
20e6a58ed17256fd2c6b7037c5a5261ac1ec149c0a64260de0582617ca069dc1  tools/generated_image_review_app/app.py
ab67d14366df207bda7344c4f74dc35593bbdc5389418945610c1acc642b9a3b  tools/generation_prompt_pilot_review.py
572060a4f120d105b14c1b05dd2b339f596e89d3009564c86172398d8f6d7ecf  tools/semantic_alignment_calibration_app.py
```

Untracked reports are `unused_code_audit_report.md` and this independent report.

## Production Isolation Proof

After all searches and tests, the service remained:

- `active/running`;
- wrapper/MainPID `1075041`;
- Python child `1075042`;
- start time `Sat 2026-07-18 18:43:35 BST`;
- restart count `0`;
- same systemd cgroup.

No X post or provider call was made. No production state, posting history,
receipt, durable ledger, environment file, systemd unit, analytics service, or
timer was read for mutation or changed. The main service was not stopped,
restarted, reloaded, or signalled. No generated-image, hybrid-retrieval, or
semantic-veto enforcement setting was activated. No commit, push, deployment,
or production activation occurred.

READY FOR SEPARATELY AUTHORISED ACTIVATION
