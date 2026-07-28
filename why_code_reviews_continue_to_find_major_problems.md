# Why repeated code reviews continue to find important problems

Date: 28 July 2026
Production baseline reviewed: `be882e8121a7b4348a57b61b1cf526401a36f5c0`

## Executive answer

It is normal for a later review to find something a previous review missed. It
is **not** satisfactory for successive reviews of the same system to keep
finding production-safety defects.

That is happening here because the project has been changing faster than its
architecture and assurance model can stabilise. After the initial import on
1 July, the baseline history contains 175 descendant commits on 26 active
commit dates, including one merge; 174 of those commits are non-merges. If the
initial import itself is included, the totals are 176 reachable commits on 27
dates. The peak was 21 commits on 2 July. These history counts include research
and generated-material commits rather than pretending that every commit changed
runtime code, but the operational core also expanded sharply:

- `mrsMThatcher2.py` grew from 4,088 to 14,028 physical lines: 11,089
  additions and 1,149 deletions, for net growth of 9,940 lines;
- `mrs_log_digest.py` grew from 1,368 to 8,759 physical lines: 7,598
  additions and 207 deletions, for net growth of 7,391 lines;
- the two largest test modules contain exactly 18,484 physical lines between
  them (`tests/test_unit_helpers.py`: 12,128;
  `tests/test_integration_harness.py`: 6,356);
- canonical evidence, projections, ledgers, runtime manifests and exact source
  hashes form a second, tightly coupled configuration system around the code.

The test suite is extensive—a fresh isolated four-worker run passed all 2,585
tests in 551.45 seconds, with 28 warnings—but it is principally a large
collection of regressions for failures already imagined or observed. It has
not been a complete executable specification of the interactions between
posting, recovery, generated artefacts, runtime configuration, process
supervision and deployment.

There has also been a review-process problem. Many earlier tasks were correctly
completed within their stated narrow scope—digest only, pagination only,
evidence admission only, receipt recovery only—but their successful validation
was sometimes described too broadly. Implementation and review were often done
in the same long-running context, against tests written alongside the change.
That creates confirmation bias. A green suite then demonstrated that the
implemented interpretation was internally consistent, not that an independent
reviewer had challenged the interpretation or searched every adjacent failure
boundary.

The latest review found more because it finally combined:

- a clean worktree from current production `master`;
- independent agents examining evidence integrity, runtime behaviour and test
  isolation separately;
- adversarial reasoning about state transitions and crash boundaries;
- production configuration and deployed-path checks;
- focused regression construction followed by the same complete suite.

## What the latest review actually found

Not every finding was an active incident or equally severe. The findings cover
ten areas, organised below into four groups.

| Group | Area | Nature of defect | Practical significance |
|---|---|---|---|
| Runtime integrity | Ordinary quotation eligibility | The runtime did not bind selection tightly enough to the complete authoritative eligibility manifest and its inputs. | A coherent but stale or drifted set of files could produce the wrong ordinary eligibility partition. |
| Runtime integrity | Historical-context gate | The gate trusted reviewed ledger/projection bindings without re-rendering every current reply under the actual formatter options; a correction sidecar could also lack an authoritative hash binding. | A previously reviewed reply could remain allowed after a relevant rendering or correction input changed. |
| Transaction and process safety | Confirmed-post receipt recovery | The receipt did not preserve the exact post-transaction quote and image histories needed to reproduce cycle-reset semantics. | Reconciliation was idempotent for the remote post but could restore the wrong local cycle state. |
| Transaction and process safety | Launcher supervision | Child output was hidden and repeated quick exits could be restarted forever; one final edge allowed env-file values to bypass crash-loop-setting validation. | systemd could report a healthy wrapper while the Python child repeatedly failed, with poor diagnostics. |
| Transaction and process safety | Runtime configuration and control | Unknown local configuration keys did not consistently fail atomically, and pause-like control keys could be misleading. | A typo in a safety setting could be ignored or appear valid without enforcing the intended behaviour. |
| Transaction and process safety | Request timeouts | Requests used scalar timeout values rather than a bounded total budget, including multi-attempt paths. | Shutdown or failure handling could exceed the assumed service stop window. |
| Transaction and process safety | Generated image classification | When the optional generated pool was enabled, an unclassifiable configured name could be treated through an unsafe fallback. | Latent because the pool is disabled, but unsafe if later enabled. |
| Observability | Digest accounting | An AI call attempt crossing the digest resume boundary could lose its matching usage event and make cost coverage appear complete. | Reporting defect: costs could be understated or their coverage overstated. |
| Test assurance | Test isolation | Tests inherited process-level endpoints and other environment state; collection was not uniformly protected against network access or production paths. | A passing test could exercise the wrong endpoint assumptions, and a poorly written future test could touch external or live resources. |
| Test assurance | Test fixtures | Several simulator and integration fixtures represented only the old subset of canonical/runtime files. | Stronger production validation initially broke tests because the fixtures were no longer faithful models of production. |

The highest-risk items were the eligibility/gate integrity boundaries, exact
receipt replay semantics, and launcher supervision. The generated-pool and
digest findings were important but were not active posting incidents. The test
fixture failures were assurance defects, not bot-runtime defects.

## Finding chronology and current status

This table is a projection of `defect_ledger.json`, not a second issue database. Commit abbreviations are the first eight hexadecimal characters. Affected ranges use explicit inclusive endpoints.

| Stable ID | Area | Description | Severity | Introduced / affected range | First review | Subsystem included | First detection | Reproducer / test | Fix | Deployment evidence | Current status | Residual risk | Evidence refs |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `DEF-0001` | ordinary quotation eligibility | Ordinary eligibility was not bound to the reviewed exact partition | high | exact; inclusive [`fe8f7713`, `e5cd1ef3`]; last good `23d5252b` | 2026-07-28 at `e5cd1ef3`; Independent current-production review of ordinary eligibility, canonical packet integrity and runtime manifest relationships. | `mrsMThatcher2.py`; `historical_context_formatter.py`; `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/runtime_eligible_quote_manifest.json` | 2026-07-28 at `e5cd1ef3`; Adversarial fixture mutation with both stale and coherently updated hashes. | Mutate a misattributed packet to Margaret Thatcher, update the packet status and then update the manifest source hash; the old runtime derived a changed partition.; `test_coherently_rehashed_packet_mutation_still_requires_exact_partition`; `test_core_partition_drift_fails_before_ordinary_selection` | fixed at `be882e81`: Validate the core corpus, source hashes, aliases and exact runtime eligibility partition while keeping context-only sidecars independent. | deployed-verified at `be882e81`; The deployment record identifies be882e81, the exact installed source hash matches its Git object, and post-deployment runtime bootstrap loaded 611 validated runtime-eligible quotations. | `deployed-verified` | The repair still depends on several generated files being promoted as one generation; candidate freeze and generation attestation remain open under DEF-0014 and DEF-0015. | `be882e81:mrsMThatcher2.py`; `be882e81:tests/test_runtime_ordinary_eligibility_integrity.py`; `EXT-DEPLOY-FINAL-VALIDATION` |
| `DEF-0002` | historical context gate | Historical-context gate did not bind current rendered replies and correction bytes | high | exact; inclusive [`f04a3c72`, `e5cd1ef3`]; last good `ff328350` | 2026-07-28 at `e5cd1ef3`; Independent review of historical-context semantic review, corrections, current formatter behavior and runtime options. | `historical_context_reply_semantic_gate.py`; `historical_context_formatter.py`; `mrsMThatcher2.py` | 2026-07-28 at `e5cd1ef3`; Coherently rehash a changed correction sidecar and separately alter a reviewed allowed rendering. | Keep the ledger/projection valid while changing correction or formatter inputs; the old gate remained available.; `test_gate_closes_on_coherently_rehashed_correction_drift`; `test_gate_closes_when_a_reviewed_allowed_current_render_drifts`; `test_bot_passes_live_context_formatter_options_to_the_gate` | fixed at `be882e81`: Bind correction bytes through the truth audit and reproduce every non-open reviewed reply under the actual runtime formatter options. | deployed-verified at `be882e81`; The deployment record identifies be882e81, installed source matches that commit, and post-deployment runtime bootstrap reported the historical-context gate available with 13 blocked entries. | `deployed-verified` | The gate still relies on a relationship-heavy generated generation; immutable generation promotion and loaded-generation identity remain open. | `e5cd1ef3:historical_context_reply_semantic_gate.py:90-193`; `be882e81:historical_context_reply_semantic_gate.py`; `EXT-DEPLOY-FINAL-VALIDATION` |
| `DEF-0003` | confirmed post receipt recovery | Regular-post receipt could not reproduce cycle-reset histories exactly | high | exact; inclusive [`9ae7e0f2`, `e5cd1ef3`]; last good `fb0e521a` | 2026-07-28 at `e5cd1ef3`; Crash/restart review of confirmed regular posts across quote and image cycle resets. | `mrsMThatcher2.py`; `tests/test_unit_helpers.py` | 2026-07-28 at `e5cd1ef3`; Replay a confirmed receipt into state containing stale pre-reset history. | Apply schema-v1 receipt after a cycle reset; additive reconciliation retains stale quote and image entries.; `test_regular_receipt_v2_restores_authoritative_post_cycle_histories`; `test_regular_receipt_v1_remains_backward_compatible` | fixed at `be882e81`: Write schema-v2 receipts with validated authoritative post-transaction histories and retain explicit v1 compatibility. | deployed-unverified at `be882e81`; The exact be882e81 source is installed and the deployment suite included the receipt regressions, but EXT-DEPLOY-FINAL-VALIDATION records receipt_replay_observed=false; no post-deployment replay verified schema-v2 after-state restoration. | `deployed-unverified` | Legacy v1 receipts remain additive by design; deployment preflight must inspect and reconcile any extant v1 receipt before relying on exact v2 semantics. | `9ae7e0f:mrsMThatcher2.py`; `be882e81:mrsMThatcher2.py` |
| `DEF-0004` | launcher supervision | Launcher could hide a permanently failing child behind a healthy wrapper | high | exact; inclusive [`2179d71e`, `e5cd1ef3`]; last good `39643a67` | 2026-07-28 at `e5cd1ef3`; Read-only process topology, launcher behavior, systemd unit and environment ordering. | `runMrsMThatcher2`; `deploy/systemd-user/mrsMThatcher.service`; `tests/test_integration_harness.py` | 2026-07-28 at `e5cd1ef3`; Repeated fast nonzero and clean-exit fake child subprocesses plus source/deployed-path inspection. | Run the old wrapper with a child that prints then immediately exits; output is discarded and the wrapper remains alive indefinitely.; `test_launcher_surfaces_child_output_and_exits_after_fast_failure_limit`; `test_launcher_treats_repeated_fast_clean_exits_as_unhealthy`; `test_launcher_validates_effective_settings_loaded_from_env_file` | fixed at `be882e81`: Surface child output, validate settings after env sourcing and exit the wrapper after bounded consecutive fast failures. | deployed-unverified at `be882e81`; The installed wrapper hash matches be882e81 and stable operation was observed, but no post-deployment synthetic fast failure or repeated clean exit exercised the repaired supervision behavior. | `deployed-unverified` | Tests invoke the wrapper directly, not the installed systemd unit. Loaded generation identity also remains absent under DEF-0015. | `e5cd1ef3:runMrsMThatcher2`; `production preflight 2026-07-28T21:41:12+01:00` |
| `DEF-0005` | runtime configuration and control | Runtime configuration and control accepted misleading unknown keys | high | unknown | 2026-07-28 at `e5cd1ef3`; Combined diagnosis summary of startup local configuration and live control-file safety keys. | `mrsMThatcher2.py`; `mrsMThatcher.local.json`; `mrsMThatcher.control.json` | 2026-07-28 at `e5cd1ef3`; Adversarial typo inputs in both configuration documents. | See DEF-0006 and DEF-0007. | not-applicable: Tracked through the two superseding atomic records rather than one umbrella fix. | not-applicable at `be882e81`; Deployment is tracked separately by DEF-0006 and DEF-0007. | `superseded` | None independent of the superseding records. | `why_code_reviews_continue_to_find_major_problems.md#what-the-latest-review-actually-found` |
| `DEF-0006` | runtime configuration and control | Unknown local configuration keys were ignored instead of rejecting the transaction | high | bounded; inclusive [`36edf036`, `e5cd1ef3`] | 2026-07-28 at `e5cd1ef3`; Startup local-config allowlist and atomic application semantics. | `mrsMThatcher2.py`; `mrsMThatcher.local.json` | 2026-07-28 at `e5cd1ef3`; Supply one plausible unknown safety key alongside valid supported changes and inspect whether any change applies. | Use ENABLE_AUTO_REPLY instead of ENABLE_AUTO_REPLIES with valid schedule overrides.; `test_local_config_unknown_key_rejects_whole_transaction`; `test_local_config_validation_rejects_bad_values_and_cannot_override_paths_or_urls` | fixed at `be882e81`: Reject the complete local-config transaction on any unsupported key. | deployed-unverified at `be882e81`; The exact be882e81 source is installed and runtime bootstrap passed, but no post-deployment adversarial unknown local-config key was applied; atomic rejection is not operationally exercised. | `deployed-unverified` | Duplicate JSON keys remain last-wins on current master under DEF-0017. | `e5cd1ef3:mrsMThatcher2.py:873-900`; `be882e81:mrsMThatcher2.py` |
| `DEF-0007` | runtime configuration and control | Pause-like runtime-control typos validated without enforcing the intended pause | high | exact; inclusive [`4a4afb4f`, `e5cd1ef3`]; last good `cbe4ca77` | 2026-07-28 at `e5cd1ef3`; Live runtime-control validation, caching, fail-closed behavior and exact lane enforcement. | `mrsMThatcher2.py`; `mrsMThatcher.control.json` | 2026-07-28 at `e5cd1ef3`; Compare permissive validation names with exact names consumed by global_remote_writes_paused and lane_paused. | Load {"disable_alll": true}; the old validator accepts it but global pause remains false.; `test_unknown_runtime_control_keys_fail_closed`; `test_documented_runtime_control_metadata_remains_valid` | fixed at `be882e81`: Use explicit boolean, time and metadata key allowlists; unknown keys fail closed. | deployed-unverified at `be882e81`; The exact be882e81 source is installed and a valid global pause was acknowledged, but no post-deployment unknown pause-like key exercised the repaired fail-closed rejection. | `deployed-unverified` | Operators must still use exact keys and observe a post-boundary acknowledgement; duplicate JSON keys remain open under DEF-0017. | `e5cd1ef3:mrsMThatcher2.py:1327-1342`; `be882e81:tests/test_fail_safe_bootstrap_and_control.py:212-247` |
| `DEF-0008` | request timeouts | HTTP timeouts were not one bounded total request budget | medium | bounded; inclusive [`b0c4f823`, `e5cd1ef3`]; last good `a9c45073` | 2026-07-28 at `e5cd1ef3`; X, bearer and media HTTP request budgets compared with systemd stop timeout and compatibility retry behavior. | `mrsMThatcher2.py`; `deploy/systemd-user/mrsMThatcher.service` | 2026-07-28 at `e5cd1ef3`; Boundary-value configuration tests and inspection of Requests timeout semantics. | Set MRS_REQUEST_TIMEOUT_SECONDS to 180 or 10000; the old parser accepted a finite positive value and passed it as a scalar.; `test_request_timeout_rejects_values_beyond_service_shutdown_budget`; `test_x_request_uses_one_combined_connect_and_read_budget` | fixed at `be882e81`: Cap the configured timeout at 60 seconds and pass one urllib3 total budget with a connect cap of 10 seconds. | deployed-unverified at `be882e81`; The exact be882e81 source is installed and the deployment suite covered timeout regressions, but deployment performed no X action and records no post-deployment request-budget exercise. | `deployed-unverified` | The bound is reasoned against the current 180-second service stop window; changes to retry counts or the unit timeout must re-evaluate INV-API-001. | `e5cd1ef3:mrsMThatcher2.py:1570-1590`; `be882e81:mrsMThatcher2.py` |
| `DEF-0009` | generated image classification | Generated image pool accepted an in-directory unclassifiable basename | medium | exact; inclusive [`d1026728`, `e5cd1ef3`]; last good `a4e06f18` | 2026-07-28 at `e5cd1ef3`; Generated-pool selection, path containment and provenance naming. | `mrsMThatcher2.py`; `generated_review_approved_images` | 2026-07-28 at `e5cd1ef3`; Place a regular file with an unclassifiable basename inside the configured generated directory. | Enable the generated pool with generated/reviewed_ai.png; the old path appended it despite lacking a known identity form.; `test_generated_image_pool_fails_closed_on_unclassifiable_basename` | fixed at `be882e81`: Resolve generated files through the provenance classifier and abort selection on any unsafe or unclassifiable file. | deployed-unverified at `be882e81`; The exact be882e81 source is installed, but EXT-DEPLOY-FINAL-VALIDATION records generated_image_pool_changed=false and the pool remained disabled; post-deployment classification was not exercised. | `deployed-unverified` | Do not enable the pool on the observed production commit. After deployment, activation still requires an inventory and classification preflight. | `e5cd1ef3:mrsMThatcher2.py:5496-5527`; `be882e81:mrsMThatcher2.py:208` |
| `DEF-0010` | digest accounting | Digest resume could lose an in-flight provider-call attempt | medium | exact; inclusive [`e5cd1ef3`, `e5cd1ef3`]; last good `39c659e6` | 2026-07-28 at `e5cd1ef3`; Incremental log-digest event attribution and provider cost coverage. | `mrs_log_digest.py`; `tests/test_digest_safety_hardening.py`; `tests/test_digest_reply_observability.py` | 2026-07-28 at `e5cd1ef3`; Split call-start and usage records across two digest invocations sharing a resume file. | First run ends after Calling AI-first reply; second begins with xAI usage and terminal decision.; `test_resume_preserves_in_flight_provider_call_until_usage_arrives`; `test_conversational_usage_without_matching_call_start_is_incomplete` | fixed at `be882e81`: Persist and restore one active provider-call attempt and mark successful usage without a matching start as incomplete. | deployed-unverified at `be882e81`; The exact be882e81 digest source is deployed and the complete suite covered resume accounting, but the deployment record contains no post-deployment two-window provider-call observation. | `deployed-unverified` | This repair models one active call attempt; future concurrent provider calls require an explicitly keyed resumable collection. | `e5cd1ef3:mrs_log_digest.py`; `be882e81:mrs_log_digest.py` |
| `DEF-0011` | test isolation | Pytest collection lacked process-local production-path and network defaults | assurance | bounded; inclusive [`36edf036`, `e5cd1ef3`] | 2026-07-27 at `f3feb9d7`; Test-suite isolation and safe serial/parallel execution. | `tests/conftest.py`; `tests/test_pytest_safety_bootstrap.py` | 2026-07-28 at `e5cd1ef3`; Inspect environment and imported runtime values captured during pytest collection, then directly exercise socket APIs. | Import mrsMThatcher2 during collection before per-test monkeypatches and inspect BASE_DIR, LOG_FILE and endpoints.; `test_collection_import_uses_process_local_test_environment`; `test_default_network_policy_denies_loopback_and_non_loopback` | fixed at `be882e81`: Install isolated runtime paths and dead endpoints at conftest import, then deny sockets by default in the pytest process. | not-applicable at `be882e81`; This is a development-assurance control, not a production deployment. | `assurance-weakness` | Python monkeypatching does not constrain arbitrary child processes; DEF-0013 remains open. | `bot_and_test_suite_review_report.md#test-isolation-work-still-needed`; `be882e81:tests/test_pytest_safety_bootstrap.py` |
| `DEF-0012` | test fixtures | Integration and simulator fixtures did not model the complete production integrity generation | assurance | bounded; inclusive [`fe8f7713`, `e5cd1ef3`]; last good `23d5252b` | 2026-07-28 at `e5cd1ef3`; Production-shape parity of integration and regular-post future simulator assets. | `tests/test_integration_harness.py`; `tests/test_simulate_regular_post_futures.py`; `tools/simulate_regular_post_futures.py` | 2026-07-28 at `be882e81`; Run strengthened loader against existing fixtures and inventory missing required files. | Use the pre-be882e81 write_minimal_asset_analysis fixture with the exact-partition loader; required manifest/status relationships are absent.; `test_current_runtime_eligibility_manifest_validates_without_context_sidecars` | fixed at `be882e81`: Expand current integration and simulator snapshots to include and validate core corpus/status/runtime eligibility relationships. | not-applicable at `be882e81`; Fixture parity is a test-assurance concern. | `assurance-weakness` | Hand-built fixtures can drift again. A single builder-generated minimal generation and release-gate parity check are still required. | `be882e81:tests/test_integration_harness.py`; `be882e81:tests/test_simulate_regular_post_futures.py` |

## New regressions versus older gaps

Two different populations of findings have been appearing, and treating them
as one would give the wrong diagnosis.

Some were introduced by very recent work:

- regular-post receipt schema v1 dates to 6 July; the 27 July crash-safety work
  increased reliance on it without preserving authoritative histories across
  a cycle reset;
- the AI cost report added immediately before the latest review did not carry
  an in-flight call across incremental digest windows;
- ordinary eligibility had recently been changed to derive dynamically from
  completed research packets rather than consume the exact reviewed runtime
  partition;
- the semantic gate and correction chain were only introduced on 22 July and
  had subsequently undergone several corpus admissions and pin updates without
  an end-to-end rendering binding.

Other findings were older:

- launcher output suppression and unconditional child restart dated to 5 July;
- unknown local-config handling dated to the initial implementation;
- permissive runtime-control key handling dated to 11 July;
- scalar HTTP timeouts dated to 2 July;
- generated-image provenance fallback and the lack of collection-wide test
  isolation had existed for much of the project.

This matters because a review cannot find a regression before it exists. It
also shows, however, that review closure is not surviving subsequent changes.
The safety contracts established by one repair are not yet expressed strongly
enough to constrain the next repair.

The recent sequence illustrates the pressure. From 20 to 28 July inclusive
there were 51 commits by committer date, including one merge (50 non-merges),
on eight active dates. On 27 July alone, successive commits changed crash-safe
posting, pagination transactionality, recovery lifecycle, one-shot
reconciliation and confirmation-time accounting. Two large digest changes
followed on 28 July before the hardening commit, which changed 2,273 text lines
(2,146 additions and 127 deletions). These changes touched overlapping state
machines rather than independent components.

## Why previous reviews missed them

### 1. The codebase is moving under the reviewer

A review establishes facts about a particular revision. In this project,
another subsystem or generated artefact often changed immediately afterwards.
The source-role v8/v9 incident is a concrete example: the process retained one
policy generation in memory while disk files moved to another, and later lazy
loading crossed the two generations.

Review conclusions were not always invalid when written; some became obsolete
because a neighbouring change altered an assumption. The absence of a
machine-readable invariant registry made those invalidations difficult to
detect.

### 2. A monolith hides cross-lane coupling

`mrsMThatcher2.py` owns configuration, scheduling, API calls, receipts,
reconciliation, quote selection, image selection, replies, memes, historical
context and startup. A local-looking change can therefore affect several
state machines.

For example, a context-reply exception once propagated through a main-post
transaction and left a confirmed receipt blocking the meme lane. Every
individual function could appear reasonable while the composed lifecycle was
wrong. Reviewing by function name or feature section is insufficient for this
kind of coupling.

### 3. Green tests encode known assumptions

The full suite passing is valuable but was given too much evidential weight.
Tests cannot reveal a failure mode they do not model.

Before the latest changes, there were tests for receipts, eligibility and
semantic gates, but not for all of these stronger properties:

- exact after-state restoration across a cycle reset;
- coherent re-hashing of a drifted manifest;
- current rendering under every deployed formatter option;
- failures between each pair of durable writes;
- a wrapper remaining alive while its child repeatedly dies;
- network denial beginning before test-module collection;
- production-equivalent fixture completeness.

Adding those properties produced new tests; the old suite had passed because
it never asked those questions.

Test count is therefore not the same as state-space coverage. There is no
current evidence of comprehensive branch coverage, mutation coverage or
systematic crash-point coverage for the critical state machines.

### 4. Generated artefacts act like code but were reviewed like data

The runtime depends on exact relationships among:

- canonical packets;
- packet corrections;
- source-role and evidence-truth audits;
- public projections and semantic-review ledgers;
- runtime eligibility and semantic-veto manifests;
- source-file hashes pinned inside generated JSON.

A single file can be well formed while the relationship between files is
stale. Some earlier checks validated shape, counts or a top-level hash but did
not recompute the meaning of the relationship. The latest gate repair had to
re-render replies and compare them with reviewed hashes rather than merely
trusting the presence of an apparently valid ledger.

One concrete design smell is that the semantic-veto manifest pins the whole
multi-purpose `historical_context_formatter.py` as an attribution predicate.
An unrelated formatter or loader edit can therefore invalidate a 22,157-pair
manifest even when every pair is unchanged. Several “refresh shadow manifest”
commits have consequently changed provenance pins without changing the
adjudications. Hashes are present, but the dependency boundary is too broad.

### 5. Reviews were often scoped as implementation validation

Many prior requests explicitly prohibited broader changes or asked for a
single component to be fixed. Respecting that scope was correct. The mistake
was allowing “the requested patch passed” to sound like “the surrounding
system has been thoroughly reviewed.”

A pagination repair does not review receipt replay. A digest repair does not
review the events that produce the digest. An evidence admission does not
review process supervision. Each can be correct and still leave a serious
defect elsewhere.

### 6. The same agent often implemented and judged the result

When the implementer writes both the code and its regression tests, the tests
naturally reflect the implementer’s model. This is useful but not independent
assurance. In the latest work, a separate read-only review caught the launcher
env-file ordering defect after the main repair appeared complete. That is a
direct demonstration of the value of an independent pass.

### 7. Worktree and deployment complexity obscured the current truth

There have been numerous research branches, completed candidates, temporary
worktrees, deployment copies and generated reports. At several points it was
necessary to establish whether a proposed fix was already on `master`, existed
only in a worktree, or had been superseded.

The live service also executes symlinks into the production checkout. Disk
content can therefore change before a controlled restart while the old process
retains imported code in memory. That makes “what version is production?”
more complicated than a Git commit alone.

The written release runbook contains many of the right safeguards, but prose is
advisory. Stale-artifact and mixed-generation failures demonstrate that the
runbook has not yet been converted into one executable release gate bound to a
specific candidate tree. During collaborative work, agents also share a mutable
candidate filesystem; a test result can cease to describe the final tree if
another edit lands afterwards unless the tested tree hash is frozen.

### 8. Long sessions are not a reliable issue database

The conversation contains many tasks, reversals, worktree changes and partial
investigations. Relying on conversational memory to track every invariant and
previous conclusion is unsafe. Context compaction and task switching make it
possible to repeat work, overlook a prior constraint or treat an old report as
current.

This needs a durable, current-master issue and invariant ledger in the
repository, not better memory.

## Where my earlier review practice fell short

The repeated findings are not adequately explained by saying that software is
complex. My own process contributed:

1. I sometimes treated passing tests as the conclusion rather than one item of
   evidence.
2. I did not always distinguish strongly enough between a scoped patch review
   and a whole-system safety review.
3. I sometimes reviewed code I had just written without a separate
   adversarial pass.
4. I did not maintain one durable list of assumptions invalidated by each
   change.
5. I allowed long task chains to accumulate before returning to architectural
   consolidation.
6. I did not consistently demand fault-injection tests at every remote/durable
   state boundary until incidents made that necessity obvious.
7. I should have used current-production deployed-path and configuration checks
   earlier, rather than relying mainly on repository fixtures and prior
   reports.

Future reports should not say “safe” or “complete” without stating the exact
review scope, exclusions, tested invariants and residual risks.

## What should change now

### Priority 0 — stabilise before adding more features

1. **Create a production invariant registry.**
   Store one reviewed document and machine-readable companion covering:
   eligibility partitions, every durable transaction state, generated-artifact
   dependencies, runtime policy versions, process layout and fail-closed
   behaviour. Each production commit must name the invariants it can affect.

2. **Create one current-master defect ledger.**
   Record finding, severity, affected commit range, reproducer, test, fix
   commit and deployment status. Never use old worktree state as an implicit
   backlog.

3. **Freeze and attest the candidate under test.**
   Final validation should acquire an integration lock and record the Git tree
   hash. The committed tree, generated artefact inventory and deployed tree
   must match that attestation. No agent or tool should edit the candidate
   during the final suite.

4. **Temporarily favour consolidation over features.**
   Do not add another posting/reply/evidence mode until the critical paths have
   explicit state diagrams and fault-injection coverage.

5. **Make the complete isolated suite a routine gate.**
   The measured 2,585-test run completed in 551.45 seconds (9 minutes
   11 seconds) with four workers. Run it in a clean checkout with default-deny
   network protection before every production merge. Focused tests remain the
   fast development loop.

6. **Require an independent read-only review before deployment.**
   The reviewer must not receive expected conclusions as implementation hints.
   It should inspect the final diff, current `master`, deployed configuration,
   generated pins and rollback implications.

### Priority 1 — reduce architectural risk

7. **Extract explicit modules from the 14,000-line bot.**
   The first candidates are runtime configuration/control, API client and
   timeout policy, ordinary eligibility, main-post transactions, reply
   transactions, scheduler orchestration and process bootstrap. Extraction
   should preserve behaviour and proceed one subsystem at a time.

8. **Use typed, versioned transaction records.**
   Main posts, memes, conversational replies and historical-context replies
   should share a small, explicit durability vocabulary: prepared, possibly
   sent, remotely confirmed, locally committed, auxiliary pending, terminal.
   Backward compatibility must have expiry evidence, not remain indefinitely.

9. **Generate dependent artefacts through one transaction.**
   A single builder should emit an inventory containing every input hash,
   output hash, policy/schema version and semantic count. Runtime loaders should
   accept one complete generation, never a mixture of individually current
   files.

10. **Stop deploying by mutating files beneath a running process.**
   Prefer immutable release directories with an atomic `current` switch, or at
   minimum require the existing global pause and service stop before any Git
   fast-forward that changes runtime-consumed files.

### Priority 2 — improve defect detection

11. **Add systematic crash-point testing.**
    Inject termination before and after every network request, receipt write,
    state write, history write and retirement step. Assert remote actions are
    at-most-once and required obligations are not lost.

12. **Add property and mutation tests to safety modules.**
    Examples include arbitrary punctuation-preserving quote identity changes,
    manifest drift, unknown config keys, malformed version transitions,
    pagination cycles and receipt schema mutations. A mutation surviving in a
    safety branch is evidence of missing coverage.

13. **Measure meaningful coverage.**
    Track branch coverage for critical modules and a separate matrix of
    transaction states/failure boundaries. Do not use raw test count as the
    primary assurance metric.

14. **Use committed minimal fixtures with production parity.**
    Every runtime loader should have one builder-generated fixture containing
    all required files and bindings. Tests should not construct partial
    approximations unless the test explicitly verifies rejection of partial
    state.

15. **Make process health first-class.**
   Health checks must prove one stable wrapper and one stable child, loaded
   commit/hash generation, no crash loop and visible child logs. Unit-active
   alone is not sufficient.

16. **Enforce subprocess egress denial outside Python.**
    The new pytest socket guard protects the pytest process, but a child process
    can bypass Python monkeypatching and proxy conventions. Critical CI should
    run inside a network namespace or equivalent OS-level default-deny
    environment.

## A better definition of “review complete”

A future review should finish with a table stating:

- exact commit and deployed paths reviewed;
- files and runtime states included;
- files and systems explicitly excluded;
- invariants exercised;
- failure boundaries injected;
- focused and complete-suite results;
- generated-artifact relationship checks;
- independent-review findings;
- open risks and why they are accepted;
- whether the conclusion is patch-local or system-wide.

“No findings” should mean no findings within that declared scope, not a claim
that the entire bot is defect-free.

## Measurement appendix

The numerical claims in this report were recalculated from committed Git
objects at the stated production baseline. The machine-readable companion is
`diagnosis_measurements.json`.

### Scope and counting rules

- Baseline commit: `be882e8121a7b4348a57b61b1cf526401a36f5c0`
  (`Harden production integrity and recovery paths`), tree
  `7965dbb935f2a9f993d14aa37d93283e16bc298a`.
- Initial import: `36edf0369aae7ffe3b6b4b63089d26de71f59537`
  (`Initial bot source and integration harness`), tree
  `c92acab4b525df48f0d320f14b98d11a70fcfd5a`.
- “Post-initial history” means the Git range
  `36edf0369aae7ffe3b6b4b63089d26de71f59537..be882e8121a7b4348a57b61b1cf526401a36f5c0`.
  It deliberately excludes the initial import itself. “Full reachable history”
  includes that root commit.
- Daily counts use committer dates formatted as `YYYY-MM-DD` in the repository
  timezone (`Europe/London`). Merge commits are included unless a result is
  explicitly labelled non-merge.
- Commit counts have no path filter. They therefore include runtime, tests,
  documentation, research, generated JSON, generated images and mixed commits.
  No attempt was made to label a mixed commit as “code” or “generated.”
- Physical-line measurements read the named blobs with `git show` and count
  newline-terminated lines with `wc -l`. They include blank and comment lines.
  They exclude every other path, including generated artefacts, binaries,
  reports and the uncommitted diagnosis files in this worktree.
- Additions and deletions come from `git diff --numstat`; “net growth” is final
  physical lines minus initial physical lines, while “churn” is additions plus
  deletions. Gross additions are not described as lines “gained.”
- Test-file inventory includes tracked `tests/**/*.py` blobs. The pytest count
  includes every collected test, including tests that validate generated
  artefacts, but does not count the generated artefacts themselves as tests.

### Reproduction commands

Run these commands from a clean checkout containing the baseline commit:

```bash
HEAD_COMMIT=be882e8121a7b4348a57b61b1cf526401a36f5c0
ROOT_COMMIT=$(git rev-list --max-parents=0 "$HEAD_COMMIT")

git rev-list --count "$HEAD_COMMIT"
git rev-list --count --no-merges "$HEAD_COMMIT"
git rev-list --count "$ROOT_COMMIT..$HEAD_COMMIT"
git rev-list --count --no-merges "$ROOT_COMMIT..$HEAD_COMMIT"

git log --format=%cd --date=format:%F "$HEAD_COMMIT" | sort | uniq -c
git log --format=%cd --date=format:%F \
  "$ROOT_COMMIT..$HEAD_COMMIT" | sort | uniq -c
git log --no-merges --format=%cd --date=format:%F \
  "$ROOT_COMMIT..$HEAD_COMMIT" | sort | uniq -c

git rev-list --count \
  --since=2026-07-20T00:00:00+01:00 \
  --until=2026-07-28T23:59:59+01:00 \
  "$HEAD_COMMIT"
git rev-list --count --no-merges \
  --since=2026-07-20T00:00:00+01:00 \
  --until=2026-07-28T23:59:59+01:00 \
  "$HEAD_COMMIT"

git show "$ROOT_COMMIT:mrsMThatcher2.py" | wc -l
git show "$HEAD_COMMIT:mrsMThatcher2.py" | wc -l
git show "$ROOT_COMMIT:mrs_log_digest.py" | wc -l
git show "$HEAD_COMMIT:mrs_log_digest.py" | wc -l
git diff --numstat "$ROOT_COMMIT" "$HEAD_COMMIT" -- \
  mrsMThatcher2.py mrs_log_digest.py \
  tests/test_unit_helpers.py tests/test_integration_harness.py

git ls-tree -r --name-only "$HEAD_COMMIT" |
  rg '^tests/.*\.py$' |
  while IFS= read -r path; do
    lines=$(git show "$HEAD_COMMIT:$path" | wc -l)
    printf '%8d %s\n' "$lines" "$path"
  done | sort -nr

PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python3 -m pytest --collect-only -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python3 -m pytest -q -p no:cacheprovider -p xdist.plugin -n 4

git ls-tree -rl "$ROOT_COMMIT"
git ls-tree -rl "$HEAD_COMMIT"
git diff --numstat "$ROOT_COMMIT" "$HEAD_COMMIT"
```

The last three commands establish how much generated and research material is
included in unfiltered repository-wide measurements. The `ls-tree` output is
summed by blob size; the final `numstat` output is summed separately for text
and binary paths.

### Commit and day results

| Measure | Result |
|---|---:|
| Full reachable history, including initial import and merge | 176 commits |
| Full reachable non-merge history | 175 commits |
| Post-initial range, including merge | 175 commits |
| Post-initial non-merge range | 174 commits |
| Full-history active commit dates | 27 |
| Post-initial active commit dates | 26 |
| Peak | 21 commits on 2026-07-02 |
| 2026-07-20 through 2026-07-28 inclusive | 51 commits on 8 active dates |
| Same recent window, non-merges | 50 commits |

| Date | Full reachable | Post-initial | Post-initial non-merge |
|---|---:|---:|---:|
| 2026-07-01 | 1 | 0 | 0 |
| 2026-07-02 | 21 | 21 | 21 |
| 2026-07-03 | 8 | 8 | 8 |
| 2026-07-04 | 11 | 11 | 11 |
| 2026-07-05 | 3 | 3 | 3 |
| 2026-07-06 | 6 | 6 | 6 |
| 2026-07-07 | 3 | 3 | 3 |
| 2026-07-08 | 5 | 5 | 5 |
| 2026-07-09 | 4 | 4 | 4 |
| 2026-07-10 | 17 | 17 | 17 |
| 2026-07-11 | 6 | 6 | 6 |
| 2026-07-12 | 3 | 3 | 3 |
| 2026-07-13 | 2 | 2 | 2 |
| 2026-07-14 | 6 | 6 | 6 |
| 2026-07-15 | 7 | 7 | 7 |
| 2026-07-16 | 2 | 2 | 2 |
| 2026-07-17 | 3 | 3 | 3 |
| 2026-07-18 | 7 | 7 | 7 |
| 2026-07-19 | 10 | 10 | 10 |
| 2026-07-20 | 3 | 3 | 3 |
| 2026-07-21 | 9 | 9 | 9 |
| 2026-07-22 | 9 | 9 | 8 |
| 2026-07-23 | 0 | 0 | 0 |
| 2026-07-24 | 4 | 4 | 4 |
| 2026-07-25 | 7 | 7 | 7 |
| 2026-07-26 | 2 | 2 | 2 |
| 2026-07-27 | 14 | 14 | 14 |
| 2026-07-28 | 3 | 3 | 3 |
| **Total** | **176** | **175** | **174** |

### Source and test results

| Path | Initial lines | Baseline lines | Additions | Deletions | Net growth | Gross churn |
|---|---:|---:|---:|---:|---:|---:|
| `mrsMThatcher2.py` | 4,088 | 14,028 | 11,089 | 1,149 | 9,940 | 12,238 |
| `mrs_log_digest.py` | 1,368 | 8,759 | 7,598 | 207 | 7,391 | 7,805 |
| `tests/test_unit_helpers.py` | 0 (absent) | 12,128 | 12,128 | 0 | 12,128 | 12,128 |
| `tests/test_integration_harness.py` | 1,503 | 6,356 | 5,232 | 379 | 4,853 | 5,611 |

The two largest baseline test modules are therefore 18,484 lines together.
Across all tracked Python files below `tests/`, the inventory grew from 3 files
and 1,808 physical lines at the initial import to 93 files and 54,115 physical
lines at the baseline.

Recent churn, using the inclusive 20–28 July window above:

| Path | Commits touching path | Additions | Deletions | Gross churn |
|---|---:|---:|---:|---:|
| `mrsMThatcher2.py` | 14 | 4,215 | 1,466 | 5,681 |
| `mrs_log_digest.py` | 19 | 3,324 | 242 | 3,566 |
| `tests/test_unit_helpers.py` | 17 | 5,093 | 800 | 5,893 |
| `tests/test_integration_harness.py` | 9 | 563 | 371 | 934 |

Pytest collection at the baseline found exactly 2,585 tests in 8.39 seconds.
The complete isolated four-worker run passed all 2,585 tests with 28 warnings
in 551.45 seconds (`0:09:11`). Collection count and execution result are kept
separate because collection alone does not prove that tests pass.

### Generated-material inclusion

The baseline contains 1,946 tracked blobs totalling 577,012,202 bytes, compared
with 26 blobs and 399,881 bytes at the initial import. The unfiltered diff spans
1,928 paths. Its text paths contain 5,437,018 additions and 1,774 deletions, and
271 changed paths are binary. Those figures intentionally include generated
research JSON and images; they demonstrate why repository-wide additions must
not be presented as source-code growth.

Conversely, the named source/test tables above include only the four listed
Python paths. Generated artefacts are excluded from their line counts. Commit
and daily counts retain generated-material commits because many commits mix
code, tests, manifests and generated outputs, and an undocumented subjective
commit classification would be less reproducible than the unfiltered count.

## Conclusion

The continued discovery of important defects is mainly a consequence of rapid
growth, monolithic cross-lane coupling, relationship-heavy generated data,
regression-oriented tests, and reviews whose conclusions were broader than
their actual scope. The review process itself also lacked enough independence
and durable tracking.

The answer is not to request an endless sequence of increasingly broad reviews.
The answer is to slow the rate of feature change, make invariants and
transactions explicit, split the operational core into reviewable units,
generate artefacts atomically, add failure-injection and mutation testing, and
require an independent final review against current production before each
deployment.

The latest hardening commit materially improves the system, but it should be
treated as the start of that consolidation process, not proof that no defects
remain.
