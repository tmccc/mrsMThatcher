# AI-first reply strategy controlled deployment

Date: 20 July 2026
Host: `big-nas-2`
User: `tonym`
Project: `/disks/disk1/etc/mrsMThatcher`

## Executive result

The reviewed AI-first conversational reply strategy was curated, tested from
the exact committed tree, committed, pushed, configured and activated through a
controlled restart of `mrsMThatcher.service`.

The replacement service is healthy with exactly one wrapper and one Python
child. It loaded the V3 configuration and the corrected semantic-veto manifest
in non-enforcing shadow mode. No V1 configuration or V1 execution path is
active. There was no restart loop, traceback, serious warning, stale quotation
analysis warning, unresolved receipt or ambiguity marker.

No test post, X reply, media upload or provider call was manufactured. The
engagement-analytics service and timer were not stopped, restarted, reloaded or
modified.

## Revision

| Item | Value |
|---|---|
| Branch | `master` |
| Previous revision | `d4c4b3f44514054503434db1431c4e3b12b3dcd6` |
| Deployed revision | `c93eac3e444d58d83b76f207a08507b1621ee495` |
| Commit subject | `Replace conversational replies with AI-first strategy` |
| Tested and committed tree | `340416e29c94352cc1bd4e9008bf04b1197b8ab6` |
| Upstream | `origin/master` |
| Push result | `d4c4b3f..c93eac3 master -> master` |

After the push, local `HEAD`, `@{upstream}` and the remote
`refs/heads/master` all resolved to
`c93eac3e444d58d83b76f207a08507b1621ee495`.

## Working-tree curation

The commit contains 53 files. It excludes:

- all raw provider responses;
- provider pricing caches and syscall traces;
- all transient provider cost ledgers;
- superseded pilot runs 1 through 12;
- the ignored live-style activation candidate;
- the superseded V1 offline evaluation;
- production state, histories, receipts and logs;
- `mrsMThatcher.env` and `mrsMThatcher.local.json`;
- model caches and unrelated generated artefacts.

The retained evidence consists of reviewed source, schemas, synthetic
regression fixtures, tests, reports, v3 offline aggregates, and the
authoritative v13 aggregate pilot result, report and source-hash manifest.

The four AI-first fixture files contain short synthetic regression cases. They
contain no username, URL or post ID copied from a real contribution. The
Burnham and Berlin Wall wording is retained because it is necessary regression
evidence for confirmed production failures.

The 53 changed paths were compared against eleven persistent environment values
after correctly sourcing the shell-format environment file. Exact matches in
the commit: zero. `mrsMThatcher.env` was never staged.

### Curated file list

Configuration, documentation and reports:

- `.gitignore`
- `README.md`
- `mrsMThatcher.local.example.json`
- `ai_first_reply_strategy_activation_readiness_report.md`
- `ai_first_reply_strategy_independent_codex_review.md`
- `ai_first_reply_strategy_post_pilot_independent_review.md`
- `ai_first_reply_strategy_provider_pilot_report.md`
- `ai_first_reply_strategy_replacement_report.md`
- `gemini_reply_strategy_deep_dive_followup_report.md`
- `reply_strategy_adversarial_review_2026-07-20.md`

Production and support source:

- `mrsMThatcher2.py`
- `reply_strategy.py`
- `reply_evidence.py`
- `reply_factual_evidence.json`
- `historical_context_formatter.py`
- `mrs_log_digest.py`
- `semantic_alignment/quote_research_schema.py`
- `semantic_alignment/quote_research_gemini.py`
- `semantic_alignment/hybrid_reply_retrieval.py`
- `semantic_alignment/image_quote_eligibility.py`
- `semantic_alignment/openai_quality_trial.py`
- `tools/evaluate_ai_first_reply_strategy.py`
- `tools/pilot_ai_first_reply_strategy.py`
- `tools/simulate_regular_post_futures.py`

Aggregate research and deployment-candidate artefacts:

- `semantic_alignment_research/ai_first_reply_strategy_001/legacy_v1_draft_audit.json`
- `semantic_alignment_research/ai_first_reply_strategy_001/offline_evaluation_v3/offline_evaluation.json`
- `semantic_alignment_research/ai_first_reply_strategy_001/offline_evaluation_v3/offline_evaluation.md`
- `semantic_alignment_research/ai_first_reply_strategy_001/provider_pilot_20260720_v13/pilot_report.md`
- `semantic_alignment_research/ai_first_reply_strategy_001/provider_pilot_20260720_v13/pilot_results.json`
- `semantic_alignment_research/ai_first_reply_strategy_001/provider_pilot_20260720_v13/run_manifest.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/checksums.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/material_veto_v3_shadow_manifest.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/runtime_eligible_quote_manifest.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/v3_shadow_manifest_audit.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/v3_shadow_manifest_audit.md`

Tests and fixtures:

- `tests/fake_api_server.py`
- `tests/fixtures/ai_first_reply_adversarial_cases.json`
- `tests/fixtures/ai_first_reply_provider_pilot_cases.json`
- `tests/fixtures/ai_first_reply_provider_revision_cases.json`
- `tests/fixtures/ai_first_reply_valid_cases.json`
- `tests/test_ai_first_reply_offline_evaluation.py`
- `tests/test_ai_first_reply_provider_pilot.py`
- `tests/test_digest_reply_observability.py`
- `tests/test_fail_safe_bootstrap_and_control.py`
- `tests/test_historical_context_reply.py`
- `tests/test_hybrid_reply_retrieval.py`
- `tests/test_integration_harness.py`
- `tests/test_logging_isolation.py`
- `tests/test_reply_runtime_isolation.py`
- `tests/test_reply_strategy.py`
- `tests/test_shadow_functionality_consolidation.py`
- `tests/test_simulate_regular_post_futures.py`
- `tests/test_unit_helpers.py`

## Validation

The full suite was run from staged tree
`340416e29c94352cc1bd4e9008bf04b1197b8ab6`:

```text
python3 -m pytest -q
1,935 passed, 1 skipped, 3 warnings in 766.68 seconds
```

The skip was expected. The warnings were one Starlette/httpx deprecation and
two BeautifulSoup/lxml deprecations. There were no test failures.

Both before and after the suite:

```text
git write-tree
340416e29c94352cc1bd4e9008bf04b1197b8ab6
```

This proves the committed tree is byte-identical to the tested tree. No source,
fixture, schema, aggregate result or pre-deployment report was modified after
the successful suite.

`git diff --check` and `git diff --cached --check` passed before commit. The
repository was clean and exactly equal to upstream before production
configuration was changed.

## Deployment architecture

The systemd user unit executes `/usr/local/bin/runMrsMThatcher2`. Both installed
entry points are symlinks into this repository:

```text
/usr/local/bin/runMrsMThatcher2 -> /disks/disk1/etc/mrsMThatcher/runMrsMThatcher2
/usr/local/bin/mrsMThatcher2.py -> /disks/disk1/etc/mrsMThatcher/mrsMThatcher2.py
```

No separate source installation was therefore required. All 27 changed Python
files were compared with their committed `HEAD` blobs immediately after
restart; mismatches: zero.

Principal deployed hashes:

| File | SHA-256 |
|---|---|
| Previous revision `mrsMThatcher2.py` | `22ef6a290220005d59c97f896db5966f9e457de2cc5b11d7e3a54046d3f2cc48` |
| Deployed `mrsMThatcher2.py` | `aa25463121607ffe5147312aa2ddb8bf91912a9db6fa996d0639d2d444d690a6` |
| `runMrsMThatcher2` | `fafd91ad1e95687b36e3fb8805ccc7f57625d460031cfbb242d2a7253c87784f` |
| `reply_strategy.py` | `5dfab3f98b39a7875bd76563e541e02910118d6febfcb944faabc777e2dee463` |
| `reply_evidence.py` | `57f6c6fa7830baab6965e75a0aa349ed5dde51960c186ce98872df2e945ef0f4` |
| `historical_context_formatter.py` | `0948e0dba9755af63a92e376b83335656b88047ea68aaaff09993f0be2b284db` |
| `semantic_alignment/quote_research_schema.py` | `032206958da4b55073fc2f0a9345d1ca73de915d41448c987f5e372ab4bb68c4` |

## Configuration migration

The ignored local configuration was backed up before modification. The old
configuration contained V1 `reply_strategy` and no V3 block. The reviewed
candidate retained every non-reply setting byte-for-byte at the JSON data level,
removed V1, and added only `ai_first_reply_strategy`.

| Item | Value |
|---|---|
| Old configuration SHA-256 | `4cbf336f80da0977d41b195d485e91a58cefb0b9c2f489c0e5ccfc4603881a8c` |
| New configuration SHA-256 | `69231e462b50a08056db0536e4564c75b9befa27f7f1f250eabe3d330e247f83` |
| New mode | `0600` |
| New owner | `tonym:tonym` |
| Strategy | `ai-first-reply-v3` |
| Enabled | true |
| Proposer model | `grok-4.3` |
| Evidence model | `grok-4.3` |
| Reviewer model | `grok-4.3` |
| Fail closed | true |

The candidate first passed isolated schema, corpus and semantic-veto validation.
After atomic installation, the exact copied file passed a fresh production
bootstrap with the persistent credentials before the old service was stopped:

- 626 completed research packets;
- six unresolved packets excluded;
- 610 attribution-eligible Thatcher packets;
- two factual evidence records;
- 7,930 indexed passages;
- V3 semantic-veto manifest loaded with 22,066 pairs;
- semantic-veto active enforcement false.

The file was installed through a same-directory temporary file, file `fsync`,
atomic `os.replace`, and parent-directory `fsync`. No credential value was
printed or copied into the configuration.

## Receipt and draft audit

Immediately before configuration installation and again immediately before
restart:

| Barrier | Result |
|---|---|
| Regular-post receipt | absent |
| Meme-post receipt | absent |
| Historical-context receipt | absent |
| Conversational confirmed-reply receipt | absent |
| Ambiguous post outcome | absent |
| Legacy V1 persisted drafts | 0 |
| Pending V3 drafts | 0 |

The fresh stable-read V1 audit reported `deployment_blocked=false`. Because no
V1 draft existed, no draft needed archival or invalidation. No receipt was
deleted, reset or rewritten.

At startup, the normal state-schema migration added only two empty keys,
`ai_reply_history` and `pending_ai_reply_drafts`. Comparison with the automatic
pre-start backup found no other semantic state difference. No posting history,
cap, target, receipt, schedule or used-item value changed. The bot's normal
startup persistence wrote its state and backups; there was no manual or
unrelated state mutation.

## Rollback bundle

Location:

`/home/tonym/.local/state/mrsMThatcher/rollback/ai_first_v3_20260720T175926Z`

The directory is mode `0700`. Contents and hashes:

| File | SHA-256 |
|---|---|
| `legacy_v1_draft_audit.json` | `5b361017be865258317bc6e47f458377e4d7424f7ce2cc6554192671aea8321e` |
| `mrsMThatcher.local.json.before` | `4cbf336f80da0977d41b195d485e91a58cefb0b9c2f489c0e5ccfc4603881a8c` |
| `mrsMThatcher.service.before` | `6ea88612b237cd7afff987d90473096cfa501ff09aebb039d42cd755a9c855ef` |
| `previous_revision_d4c4b3f44514054503434db1431c4e3b12b3dcd6.tar.gz` | `9c58b6a4e78dcbf1994f7e8dcdd55f80b9df75fe54e7dbe7a99be9600c971f63` |
| `runMrsMThatcher2.before` | `fafd91ad1e95687b36e3fb8805ccc7f57625d460031cfbb242d2a7253c87784f` |

The previous-revision archive contains the prior `mrsMThatcher2.py`,
`reply_strategy.py`, `mrs_log_digest.py`, wrapper and example configuration.

Rollback was not needed. If subsequently required, the controlled procedure is
to clear the receipt and ambiguity barriers, stop only the main user service,
restore revision `d4c4b3f44514054503434db1431c4e3b12b3dcd6` and the backed-up
local configuration, verify hashes, then start the main service once and confirm
the old single wrapper/child arrangement. The analytics units must remain
untouched.

## Service state

Before activation:

| Property | Value |
|---|---|
| State | active/running |
| Wrapper PID | 1595442 |
| Python child PID | 1595443 |
| Start time | Sun 2026-07-19 23:07:45 BST |
| Restart count | 0 |

After activation:

| Property | Value |
|---|---|
| State | active/running |
| Wrapper PID | 695911 |
| Python child PID | 695912 |
| Start time | Mon 2026-07-20 19:01:40 BST |
| Restart count | 0 |
| Cgroup | `/user.slice/user-1000.slice/user@1000.service/app.slice/mrsMThatcher.service` |

Exactly one wrapper and one Python child were present in the service cgroup and
none existed outside it. Subsequent checks retained the same PIDs and zero
restarts.

## Startup findings

The bot log records:

- graceful old-child exit through `KeyboardInterrupt`;
- 27 local configuration overrides applied;
- corrected V3 semantic-veto shadow loaded, 22,066 pairs;
- `active_enforcement=false`;
- original-editorial shadow enabled and unchanged;
- generated identity processing suspended because generated images are
  disabled;
- 69 original images and 90 meme candidates loaded;
- 422 used quotation hashes and 75 used image basenames loaded;
- existing quote and meme schedules retained;
- instance lock acquired;
- `Bot started successfully`;
- normal idle loop with no post due during verification.

There was no traceback, error, critical event, stale source-SHA warning, retired
configuration warning, duplicate process or restart loop. The five receipt and
ambiguity paths remained absent after startup.

The live configuration contains `ai-first-reply-v3`, is enabled and contains no
retired `reply_strategy` key. The committed production strategy module declares
only V3. Historical V1 names remain solely where needed to reject or audit old
state; no V1 reply pipeline can execute.

`systemctl --user --failed` reported zero failed units.

## Analytics isolation

The engagement timer remained enabled and `active/waiting`. It ran naturally on
its existing 15-minute schedule during the preflight and its service returned
`Result=success`, `ExecMainStatus=0`, `NRestarts=0`. Neither the timer nor the
oneshot service was stopped, started, restarted, reloaded or modified by this
deployment.

## Explicit confirmations

- No X test post or reply was made.
- No media was uploaded.
- No synthetic mention or quote-tweet was created.
- No provider call was added to startup or manually invoked during activation.
- No receipt or ambiguous outcome was deleted or reset.
- No posting history, used-item history, cap, target or schedule was manually
  changed.
- No secret was printed, staged or committed.
- Semantic-veto enforcement remains disabled.
- Generated images remain disabled.
- Hybrid retrieval remains offline-only.
- Analytics remained untouched and healthy.
- The deployment report is the only post-deployment worktree addition; committed
  source remains exactly the tested and pushed revision.

AI-FIRST REPLY STRATEGY DEPLOYMENT SUCCESSFUL
