# Fresh-eyes remediation, 19 September 2026

Starting HEAD and reviewed commit:
`47352f37faa0d8cf967c9179e9b9369ac92bc0ec`.

Implementation commit: `4181529b8b2d366410b4feb7390b28d9f6ff3e99`.
The following documentation commit records migrations and completed verification.

Implementation worktree:
`/disks/disk1/research/mrsMThatcher-fresh-eyes-remediation-20260919`, branch
`fix/fresh-eyes-remediation-20260919`. The original service checkout is
`/disks/disk1/etc/mrsMThatcher`; it was left in place because its services execute
that directory. The starting tracked worktree was clean. No unrelated user
changes were overwritten.

The implementation order was durable publication and retirement authority;
reply grounding/time/media/provider contracts; offline research and review-app
security; runtime/configuration/development fixes; then integrated tests and an
independent adversarial pass. Bounded work proceeded in parallel with explicit
file ownership. No finding was treated as established until checked against HEAD.
None of the thirteen findings had already been fixed at the starting HEAD.

## Confirmed causes and resolutions

| Item | Confirmed cause | Implemented resolution |
| --- | --- | --- |
| 1 | Primary and latest backup had no generation ordering; post-confirmation cleanup could discard recovery evidence after a backup failure. | Embedded monotonic sequence and content digest, reader-v5 fence, locked migration, newest complete generation selection/repair, exact fsynced commit proofs, composite emergency state/history result. Ambiguous legacy authority fails closed. |
| 2 | Writer accepted non-finite JSON, reader-incompatible schema and oversized output. | Canonical `allow_nan=False` UTF-8 bytes, complete reader validation and exact encoded-size checks before any durable change. Unbounded identity collections retain entries and fail before the 64 MiB ceiling; used histories have the same precommit ceiling. |
| 3 | Content-only proof followed symlinks, some durable authorities accepted writable files, and consumed media capabilities leaked. | Existing secure no-follow primitives, owned/protected directories, stable metadata and byte proofs at retirement transitions; private receipt readers; exact consumed-entry removal that cannot delete newer authority. |
| 4 | Model label and fact-ID membership were mistaken for evidence support. | Declared claim inventory, whole trusted-passage equality and draft-v4 evidence hashes. The follow-up removes the original closed grammar: natural conversation is allowed, while assertion identification and meaning remain the model’s responsibility. Frozen receipt recovery covers the prior schema-4 prompt too. |
| 5 | Payload construction discarded trusted time metadata. | Canonical UTC date/source timestamp in payload and draft hashes, explicit malformed-time rejection and date-boundary revalidation. |
| 6 | Ordinary barrier inspection or logging errors could exit a process with only a memory latch. | Throttled inspection retry, guarded logging, and preservation of controlled `BaseException` behaviour until durable authority is proved. |
| 7 | Redirects were followed before validation, Google endpoint recognition was permissive, and discovery pagination was unbounded. | Exact Google endpoint syntax, no automatic redirects, every-hop public-address validation, pinned IP connections, absolute DNS/socket/body deadlines, body/hop ceilings and discovery page/item ceilings. |
| 8 | Legacy LAN review routes exposed images and mutations without authentication/CSRF. | Compatibility wrapper onto shared hardened review security, existing database/workflows preserved, constant-time authentication, signed CSRF, actual listener plus Host/Origin checks, bounded bodies/security headers, POST export and TLS/trusted-proxy LAN requirements. |
| 9 | Magic prefixes and unverified declared lengths admitted incomplete images; per-image limits missed total provider encoding. Emoji detection rejected every `So` symbol. | Exact length checks with transient short-read classification, full Pillow container/frame decoding, dimension/pixel/bomb limits, 32 MiB complete encoded request cap, and emoji-specific ranges/sequence markers. |
| 10 | 429 delay metadata was ignored and a successful retry bypassed cooldown accounting. | At most one immediate retry within a one-second budget; longer/unknown delays defer with persisted cooldown. Recovered 429s persist health/cooldown before telemetry or another candidate. Ambiguous requests remain non-retryable. |
| 11 | Invalid retry spacing, future retry epochs, zero safety-stop exits, pre-lock health writes, unsafe reload and host-dependent naive deadlines. | Configuration bounds, one-time future-epoch repair, safety exit status 3 versus successful no-post zero, health initialization after singleton acquisition, lifecycle reload refusal, and required explicit timezone offsets. |
| 12 | Development collection lacked the OpenAI SDK; archive scanning included virtual environments. | Bounded `openai>=2,<3`, clean environment installation/collection, archive directory exclusions, documented Linux/CPython 3.10 locking facilities, retained executable modes. |
| 13 | Ambiguous billable POSTs were retried, failed attempts escaped the cost ceiling, and arbitrary/plaintext credential destinations were allowed. | No ambiguous POST retry, durable reservations for every attempt under a private process lock, exact committed-ledger proof, expected HTTPS provider origins, and HTTP loopback overrides only in test mode. |

## Migration and compatibility

- State minimum reader version is **5**. Existing unsealed state migrates only
  under the instance/state lock when authority is unambiguous. Matching legacy
  canonical/latest copies can establish authority; otherwise differing valid
  legacy documents are not ordered by filename. Equal modern sequence conflicts
  are also refused. `_confirmed_receipt_commits` binds exact source identities to
  confirmed effects and supports safe interrupted-retirement recovery.
- Pending reply drafts are **schema 4**. Older pending drafts are rejected;
  frozen validation is retained solely for already-started/confirmed receipts,
  including schema-4 drafts made under the pre-follow-up prompt, preserving
  duplicate suppression without approving obsolete unsent drafts.
- Existing review decisions remain in the same SQLite database. Both entry
  points use hardened request security; non-loopback deployments need explicit
  credentials and protected transport.
- The image-request cost reservation ledger gains a permanent private lock file.
  Existing well-formed reservation ledgers are recovered under that lock;
  malformed, missing-after-observation or substituted authority stops requests.
- Declared factual validation is conservative: unsupported factual paraphrases
  cannot pass the retained exact-support check. Natural
  non-factual conversation has no closed grammar after the follow-up. Local
  validation checks declared evidence; it cannot ensure inventory completeness
  or arbitrary semantic entailment. No second billable model call was added.

Detailed owners, regressions and commands:

- [State, writer contracts and local authority](fresh-eyes-state-remediation.md)
- [Grounding, UTC context, images and rate limits](fresh-eyes-reply-remediation.md)
- [Research fetching, LAN review, endpoints and cost](fresh-eyes-research-remediation.md)
- [Runtime, automation and clean development setup](fresh-eyes-runtime-remediation.md)
- [Independent final adversarial audit](fresh-eyes-final-audit.md)

## Verification

Verification used CPython **3.10.12** on Linux x86-64, including the real OFD-lock
and AF_UNIX integration tests. The clean virtual environment is
`/tmp/mrs-remediation-clean-20260919`; it does not inherit system packages.
The documented four-worker fast path runs at normal priority, with no concurrent
serial suite. Pytest installs temporary home/state/cache directories, dummy
credentials, dead-loopback provider origins/proxies and the existing socket guard.

Clean dependency and collection smoke:

```bash
python3 -m pip --isolated download --index-url https://pypi.org/simple \
  --only-binary=:all: -r requirements-dev.txt \
  --dest /tmp/mrs-remediation-wheelhouse-20260919
python3 -m venv /tmp/mrs-remediation-clean-20260919
source /tmp/mrs-remediation-clean-20260919/bin/activate
python3 -m pip --isolated install --no-index \
  --find-links /tmp/mrs-remediation-wheelhouse-20260919 -r requirements-dev.txt
python3 -m pip check
MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest --collect-only -q
```

Installation and dependency checks passed. Clean collection at that point passed:
**8,568 tests collected in 5.62s**. Subsequent regressions are included in the final
full run below. Installed packages include OpenAI **2.54.0**, Pillow **12.3.0** and
pytest-xdist **3.8.0**. Because this is an isolated virtual environment, the README's
`PYTHONUSERBASE` workaround for user-site packages is unnecessary.

Repository-wide commands (same activated environment):

```bash
python3 -m compileall -q .
python3 tools/check_python_documentation.py
MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
MRS_TEST_MODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  -p xdist.plugin -n 4 --dist=worksteal --max-worker-restart=0
git diff --check
```

Compilation passed; documentation coverage passed for **287 modules**; dependency
and whitespace checks passed. The first completed clean serial run reported
**25 failed, 8,543 passed, 2 warnings in 1,194.81s**. Those failures were resolved;
rerunning its exact 25 node IDs passed **25/25 in 2.45s**. The first four-worker
run then reported **1 failed, 8,620 passed, 8 warnings in 524.21s**. Its remaining
legacy-backup fixture assumed ambiguous recovery; the fixture was migrated to
provable generation authority while retaining its no-network and page-ownership
assertions; its owning and independent state suites then passed **118 tests in
7.73s**. The final four-worker run passed **8,622 tests, zero failures or skips,
8 warnings in 528.14s (8m48s)**. The eight warnings are the Starlette/httpx and
AnyIO deprecations repeated across four workers. Final log:
`/tmp/mrs-remediation-full-suite-verified.log`.

Focused owning-module, root-adapter and real-process coverage passed throughout
implementation. Final focused checks included **133 passed** for mention-backlog
and emergency-state authority, **107 passed** for consistency/replay, **150 passed**
for X outcome conservatism, **99 passed** for provider-origin adapters, and
**30 passed** for generation fault boundaries. Topic reports record the other
commands and results. No required suite was excluded or skipped for platform
limitations.

Independent review found additional intermediate defects in legacy ordering,
exact inode authority, history validation, relative/pronominal fact context,
telemetry ordering, LAN listener checks and fetch deadlines. Each received a
regression and a verified fix. The final read-only integration audit found no
remaining concrete concern, no added credentials or live-network exemption, and
**zero executable-mode mismatches across 2,284 baseline tracked files**, including
all eleven originally executable files. No unresolved implementation blocker
remains; deliberately conservative factual rejection and ambiguous-legacy refusal
are described above.

All runtime testing used temporary directories, fake transports or permitted
loopback mock servers, with `MRS_TEST_MODE=1` and the project's network guard.
The clean environment was assembled from public PyPI wheels, then installed
with `--no-index`; no X/OpenAI/Gemini provider request was made. No production
state, deployment, push or chargeable operation was used.

## Changed files

### Runtime and research implementation

- `exact_receipt_retirement.py`
- `generate_all_openai_quote_images.py`
- `historical_context_search_research.py`
- `historical_context_source_gemini.py`
- `historical_context_source_resolution.py`
- `mrsMThatcher2.py`
- `mrs_bot_cli_execution.py`
- `mrs_bot_daily_meme.py`
- `mrs_bot_durable_json_io.py`
- `mrs_bot_legacy_reply_validation.py`
- `mrs_bot_main_post_confirmation_persistence.py`
- `mrs_bot_main_post_receipt_storage.py`
- `mrs_bot_main_post_reconciliation.py`
- `mrs_bot_quote_posting.py`
- `mrs_bot_quote_reply_cycle.py`
- `mrs_bot_receipt_retirement.py`
- `mrs_bot_regular_post_completion.py`
- `mrs_bot_reply_context.py`
- `mrs_bot_reply_delivery.py`
- `mrs_bot_reply_evaluation_state.py`
- `mrs_bot_reply_generation.py`
- `mrs_bot_reply_reconciliation.py`
- `mrs_bot_request_route_values.py`
- `mrs_bot_runtime_configuration.py`
- `mrs_bot_runtime_state_helpers.py`
- `mrs_bot_state_candidate_validation.py`
- `mrs_bot_state_generation.py`
- `mrs_bot_state_loading.py`
- `mrs_bot_state_persistence.py`
- `provider_endpoint_policy.py`
- `public_source_deadline.py`
- `public_source_fetch.py`
- `remote_media_upload_receipt.py`
- `remote_write_transport_journal.py`
- `runtime_control_contract.py`
- `semantic_alignment/thatcher_image_hunt.py`
- `single_call_reply.py`
- `single_call_reply_grounding.py`
- `single_call_reply_images.py`
- `single_call_reply_validation.py`

### Tools and review applications

- `tools/check_python_documentation.py`
- `tools/generated_image_review/app.py`
- `tools/generated_image_review/static/app.js`
- `tools/generated_image_review_app/app.py`
- `tools/generated_image_review_app/security.py`
- `tools/generated_image_review_app/swipe.py`

### Regression coverage and offline fixtures

- `tests/conftest.py`
- `tests/fake_api_server.py`
- `tests/fixtures/scenarios/duplicate_mention_hot_post.json`
- `tests/fixtures/scenarios/made_with_ai_retry.json`
- `tests/fixtures/scenarios/normal_mention_reply.json`
- `tests/fixtures/scenarios/quote_tweet_reply.json`
- `tests/fixtures/scenarios/reply_not_allowed_403.json`
- `tests/helpers/integration_harness.py`
- `tests/helpers/reply_fixtures.py`
- `tests/helpers/single_call_fixtures.py`
- `tests/test_bot_asset_selection_regressions.py`
- `tests/test_bot_cli_execution.py`
- `tests/test_bot_combined_review_regressions.py`
- `tests/test_bot_conversational_receipts_regressions.py`
- `tests/test_bot_durable_json_io.py`
- `tests/test_bot_historical_context_regressions.py`
- `tests/test_bot_image_cycle_regressions.py`
- `tests/test_bot_main_post_confirmation_persistence.py`
- `tests/test_bot_main_post_receipt_storage.py`
- `tests/test_bot_main_post_receipts_regressions.py`
- `tests/test_bot_main_post_reconciliation.py`
- `tests/test_bot_main_post_scheduling_regressions.py`
- `tests/test_bot_meme_post_regressions.py`
- `tests/test_bot_mention_discovery.py`
- `tests/test_bot_pending_reply_drafts_regressions.py`
- `tests/test_bot_post_transport_integrity_regressions.py`
- `tests/test_bot_quote_reply_cycle.py`
- `tests/test_bot_receipt_retirement.py`
- `tests/test_bot_regular_post_completion.py`
- `tests/test_bot_regular_post_regressions.py`
- `tests/test_bot_reply_context.py`
- `tests/test_bot_reply_context_regressions.py`
- `tests/test_bot_reply_delivery.py`
- `tests/test_bot_reply_evaluation_state.py`
- `tests/test_bot_reply_generation.py`
- `tests/test_bot_reply_reconciliation.py`
- `tests/test_bot_request_route_values.py`
- `tests/test_bot_runtime_configuration.py`
- `tests/test_bot_runtime_safety_regressions.py`
- `tests/test_bot_state_loading.py`
- `tests/test_bot_state_persistence.py`
- `tests/test_bot_state_storage_regressions.py`
- `tests/test_bot_tick_coordination.py`
- `tests/test_confirmed_source_lineage.py`
- `tests/test_digest_generated_identity.py`
- `tests/test_digest_original_editorial.py`
- `tests/test_discovered_image_preparation.py`
- `tests/test_fail_safe_bootstrap_and_control.py`
- `tests/test_followup_fail_safe_hardening.py`
- `tests/test_generated_image_review_app.py`
- `tests/test_historical_context_reply_semantic_gate.py`
- `tests/test_historical_context_source_roles.py`
- `tests/test_image_attempt_budget.py`
- `tests/test_independent_emergency_state_security.py`
- `tests/test_independent_reply_security.py`
- `tests/test_independent_state_authority_regressions.py`
- `tests/test_integration_harness.py`
- `tests/test_logging_isolation.py`
- `tests/test_main_post_recovery_progress.py`
- `tests/test_mention_backlog_author_quarantine.py`
- `tests/test_pending_receipt_directory_fsync.py`
- `tests/test_production_consistency_incident.py`
- `tests/test_public_source_deadline.py`
- `tests/test_remote_media_upload_receipt.py`
- `tests/test_research_fetch_security.py`
- `tests/test_review_fresh_eyes_security.py`
- `tests/test_review_request_security.py`
- `tests/test_runtime_control_contract.py`
- `tests/test_runtime_review_remediation.py`
- `tests/test_single_call_failure_routing.py`
- `tests/test_single_call_reply.py`
- `tests/test_single_call_safety_contract.py`
- `tests/test_single_call_transport_safety.py`
- `tests/test_source_receipt_retirement_integration.py`
- `tests/test_state_generation_protocol.py`
- `tests/test_state_reader_compatibility.py`
- `tests/test_thatcher_image_hunt.py`
- `tests/test_x_write_outcome_conservatism.py`
- `tools/generated_image_review/tests/test_app_api.py`

### Dependencies and documentation

- `README.md`
- `docs/fresh-eyes-final-audit.md`
- `docs/fresh-eyes-remediation-20260919.md`
- `docs/fresh-eyes-reply-remediation.md`
- `docs/fresh-eyes-research-remediation.md`
- `docs/fresh-eyes-runtime-remediation.md`
- `docs/fresh-eyes-state-remediation.md`
- `generated_image_review_web_app_implementation_report.md`
- `requirements-dev.txt`
- `requirements.txt`
- `tools/generated_image_review/README.md`
- `tools/generated_image_review_app/README.md`
