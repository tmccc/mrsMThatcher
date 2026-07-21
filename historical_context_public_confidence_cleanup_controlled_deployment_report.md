# Historical-context public confidence cleanup controlled deployment

Date: 21 July 2026

Host: `big-nas-2`

Project: `/disks/disk1/etc/mrsMThatcher`

## Verdict

The public/internal historical-context rendering split was committed, pushed and activated successfully. Public X context replies no longer include the six-dimensional confidence line; the dimensions remain in internal formatter results and persisted metadata.

Only `mrsMThatcher.service` was restarted. No test post, reply, media upload or provider call was made. Engagement analytics was not stopped, started, restarted, reloaded or modified.

## Repository

Implementation commit:

`491e81cffdd4f36fbc79b9948aedfa4b26f674e1` (`Simplify public historical context output`)

The commit was pushed to `origin/master`. Local `HEAD` and `origin/master` matched exactly before activation, and the worktree was clean.

The curated commit includes:

- the public/internal formatter and schema-v4 metadata;
- the explicit public renderer call in `mrsMThatcher2.py`;
- focused regressions and receipt compatibility coverage;
- the refreshed, inactive v3 semantic-veto candidate fingerprint;
- the implementation report;
- the deterministic 25-record ChatGPT Deep Research pack and its builder.

No provider response cache, execution ledger, secret, ignored local configuration, runtime state, receipt, history, analytics file or log was committed.

## Validation

Validation before the commit and restart:

- complete offline suite: **2,067 passed, 1 skipped, 12 warnings** in 781.93 seconds;
- exact staged focused suite: **131 passed, 9 warnings** in 11.82 seconds;
- targeted `py_compile`: passed;
- `git diff --check` and staged diff check: passed;
- deterministic research-pack rebuild: byte-for-byte identical;
- production self-test with the persistent environment: passed;
- self-test explicitly made no X or xAI call.

The self-test read but did not change the ignored local configuration. Credential values are not reproduced here.

## Runtime path and hashes

The user service runs `/usr/local/bin/runMrsMThatcher2`. Its Python entry point remains:

`/usr/local/bin/mrsMThatcher2.py -> /disks/disk1/etc/mrsMThatcher/mrsMThatcher2.py`

No source installation or copy was necessary. Restarting the service loaded the committed repository files.

| Runtime item | SHA-256 |
|---|---|
| `mrsMThatcher2.py` | `1301886981e80b0130e82170921c983472d5951f0693a7c2a8c27a215b9d68ee` |
| `historical_context_formatter.py` | `f624b95c4bc19c244fa5421cc99af441f69ba7eec1e35f4bb07cf02e52664029` |
| `historical_context_reply_schema.json` | `3e13fb35cc1f4dba28aafa02a0284f450a50230c081c2eeae23028d4ea871d91` |
| v3 shadow candidate | `2f7913f5b86b3b3a192255e769336086c0179db7c08ddfdfcff484dc7ad70ecd` |
| ignored local configuration | `69231e462b50a08056db0536e4564c75b9befa27f7f1f250eabe3d330e247f83` |
| wrapper | `fafd91ad1e95687b36e3fb8805ccc7f57625d460031cfbb242d2a7253c87784f` |
| user unit | `6ea88612b237cd7afff987d90473096cfa501ff09aebb039d42cd755a9c855ef` |

The runtime hashes matched the committed Git blobs after activation. The local configuration retained mode `0600` and was not edited.

## Safety preflight

The following barriers were clear both before and after restart:

- regular-post receipt: absent;
- meme-post receipt: absent;
- historical-context reply receipt: absent;
- conversational confirmed-reply receipt: absent;
- ambiguous post outcome: absent;
- legacy V1 drafts: 0;
- pending V3 drafts: 0.

No receipt or draft was deleted, reset, reconciled or migrated by deployment commands.

## Controlled restart

The only service-control command was:

```text
systemctl --user restart mrsMThatcher.service
```

Before restart:

| Property | Value |
|---|---|
| Active state | `active/running` |
| Wrapper PID | `771839` |
| Python child PID | `771840` |
| Start time | `2026-07-21 20:51:45 BST` |
| Restart count | `0` |

After restart and observation beyond the wrapper's 60-second interval:

| Property | Value |
|---|---|
| Active state | `active/running` |
| Wrapper PID | `1033185` |
| Python child PID | `1033186` |
| Start time | `2026-07-21 22:25:43 BST` |
| Restart count | `0` |
| Cgroup | `/user.slice/user-1000.slice/user@1000.service/app.slice/mrsMThatcher.service` |

The cgroup contained exactly those two processes. Both old PIDs had exited, no duplicate process existed, and the replacement child remained unchanged through the next loop tick.

## Startup findings

Startup completed at 22:25:45 BST and reported `Bot started successfully`. The replacement child:

- loaded the refreshed v3 semantic-veto manifest with 610 quotations, 91 images and 22,066 known pairs;
- reported manifest SHA-256 `2f7913f5b86b3b3a192255e769336086c0179db7c08ddfdfcff484dc7ad70ecd`;
- reported `active_enforcement=false`;
- retained original-editorial scoring as shadow-only;
- kept generated identity-policy processing suspended because generated images are disabled;
- retained the existing quote and meme schedules;
- scheduled the next quote for 23:25:13 BST and made no deployment-time post;
- emitted no traceback, error, critical event, stale-source warning or unresolved-receipt warning.

The sole warning was the expected `KeyboardInterrupt` used for the graceful stop of the old child. `systemctl --user --failed` returned no units.

Network-free `shadow-status` confirmed a valid, unstale manifest, zero production-selection-change failures and zero feature network calls.

## Analytics isolation

The analytics timer remained `active/waiting` with its pre-existing 22:15:37 last trigger and 22:30:10 next trigger. The oneshot service retained `Result=success`, exit status 0 and restart count 0. No analytics command or data mutation was performed.

## Rollback

Protected rollback bundle:

`/home/tonym/.local/state/mrsMThatcher/deployments/historical_context_public_cleanup_20260721T212505Z_491e81c`

The directory is mode `0700`; its files are mode `0600`. `sha256sum -c SHA256SUMS` passed.

| Bundle item | SHA-256 |
|---|---|
| legacy V1 draft audit | `75428bb5639f3cf0ebed162e5c9b51aabb2ee7d4c408ebc4fcb1a6b0e6def105` |
| local configuration backup | `69231e462b50a08056db0536e4564c75b9befa27f7f1f250eabe3d330e247f83` |
| user unit backup | `6ea88612b237cd7afff987d90473096cfa501ff09aebb039d42cd755a9c855ef` |
| previous runtime archive | `fc6194464db0ca6d38ba6b6bf633e3b4bb2ef2b0cc150619ea124c072deda011` |
| wrapper backup | `fafd91ad1e95687b36e3fb8805ccc7f57625d460031cfbb242d2a7253c87784f` |

Rollback was not required. If a defect is later confirmed, first require all receipt and ambiguity barriers to be clear. Stop only `mrsMThatcher.service`, restore the archived parent-revision runtime files into the repository, start the service once, and verify the archived hashes and one-wrapper/one-child layout. Do not restore production state, histories, receipts or analytics data.

## Confirmations

- No X test content was posted.
- No media was uploaded.
- No provider/model call was made by deployment.
- No production state, history, receipt or ledger was manually altered. Normal startup performed its established state save and backup rotation.
- No generated-image feature was activated.
- No semantic-veto or editorial enforcement was activated.
- No hybrid retrieval was activated.
- Engagement analytics was untouched.

DEPLOYMENT SUCCESSFUL
