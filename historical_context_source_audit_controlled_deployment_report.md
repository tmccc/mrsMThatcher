# Historical-context source audit controlled deployment

Date: 21 July 2026
Host: `big-nas-2`
User: `tonym`

## Result

The independently reviewed historical-context source-role audit was committed,
pushed and activated successfully. The main bot is healthy under its existing
systemd user unit. Historical-context evidence now requires the frozen
independent review of all 34 AI-located sources.

No test post, reply or media upload was made. Semantic-veto enforcement remains
disabled. The engagement analytics units were not stopped, started, restarted,
reloaded or modified.

## Repository

Reviewed implementation commit:

`9672b5c014f82af2478739b891c1bc95e4316c16`

Commit subject:

`Independently verify historical context sources`

Branch and remote:

- local branch: `master`;
- upstream: `origin/master`;
- local, upstream and remote branch hashes all matched the commit above before
  activation;
- the worktree was clean before restart.

The commit added the source-review builder, its 34-source frozen review
manifest and the independent-review report. It also included the tested source
role, formatter, audit-summary, test and regenerated shadow-manifest changes.
No raw provider-response cache, transient ledger, credential file or temporary
fetch output was committed.

## Validation

The complete offline suite from the reviewed source/data tree passed:

`2063 passed, 1 skipped, 12 warnings in 783.30s (0:13:03)`

The focused deployment validation passed:

`175 passed, 9 warnings in 107.10s (0:01:47)`

Focused coverage included source roles, public context rendering, formatter
trial behaviour and the attribution-cleaned v3 manifest.

Additional checks:

- changed modules compiled with `py_compile`;
- `git diff --check` and staged `git diff --cached --check` passed;
- direct runtime corpus loading returned 626 completed packets and 6 unresolved
  records;
- the audited attribution-eligible count remained 610;
- the local configuration validated;
- the production self-test passed when invoked with the established persistent
  environment;
- no X or model call is made by self-test mode.

An initial self-test invocation without sourcing the persistent environment
failed only its six credential-presence checks. It still validated the local
configuration. The check was immediately repeated through the established
environment source and passed. No credential value is reproduced in this
report.

## Preflight barriers

Immediately before restart:

- Git worktree clean: yes;
- local HEAD equals upstream: yes;
- remote `master` equals local HEAD: yes;
- legacy V1 persisted reply drafts: 0;
- V1 deployment blocked: false;
- regular-post receipt: absent;
- meme-post receipt: absent;
- historical-context reply receipt: absent;
- conversational-reply receipt: absent;
- ambiguous remote-write marker: absent;
- wrapper count: 1;
- Python child count: 1;
- local configuration unchanged from its rollback copy: yes;
- next regular quote time: 21 July 2026 21:16:30 BST.

No receipt was deleted, reset, reconciled or altered by deployment commands.

## Runtime path

The user unit starts `/usr/local/bin/runMrsMThatcher2` with the project as its
working directory. `/usr/local/bin/mrsMThatcher2.py` is a symbolic link to:

`/disks/disk1/etc/mrsMThatcher/mrsMThatcher2.py`

The historical-context modules and manifests load directly from the repository.
No production file copy or local-configuration migration was required.

## Source hashes

| Runtime artefact | SHA-256 |
|---|---|
| `mrsMThatcher2.py` | `34601b559cd85256ed67a72f22d41d2837d7a545fec88d5a346ad65545bfad04` |
| `historical_context_formatter.py` | `c699184ac1d96ab3c800436552db07429eb41a88d125caea181cf3ddad2d668f` |
| `historical_context_source_roles.py` | `733a8cafa956bee00875108c2b2482b0a21fdfe633d85051b90ed897315e270e` |
| `historical_context_source_independent_review.py` | `8f4cfbc3b43607b8cdeef2da895373fba1842ad99dff545ed474107016560363` |
| source-role audit | `ebab8591365d341b9968a6964d3b33b98d9de5a51eab1c29268d6206eb5dfa5c` |
| independent source review | `6251250f9ec102fa65ac2791b9945145df16135545034b5023f327bc7b24d6df` |
| v3 shadow manifest | `55f571255e2570ec9584a77204ac23d6062873171da384c3079df36eea2e9b75` |
| ignored local configuration | `69231e462b50a08056db0536e4564c75b9befa27f7f1f250eabe3d330e247f83` |

The local configuration hash matched its pre-deployment backup. The file
remained mode `0600` and was not edited.

## Controlled restart

Only this command was used:

```text
systemctl --user restart mrsMThatcher.service
```

Before restart:

| Property | Value |
|---|---|
| Active state | `active/running` |
| Wrapper PID | `3936954` |
| Python child PID | `3936955` |
| Start time | `Tue 2026-07-21 14:40:36 BST` |
| Restart count | `0` |

The old child had loaded runtime revision
`1e5731b09cb146de5b22b090bec3332e10a9746d`. This predates both source-audit
commits, so rollback was deliberately based on that active revision rather than
only on `HEAD^`.

After restart and two additional loop ticks:

| Property | Value |
|---|---|
| Active state | `active/running` |
| Wrapper PID | `771839` |
| Python child PID | `771840` |
| Start time | `Tue 2026-07-21 20:51:45 BST` |
| Restart count | `0` |
| Cgroup | `/user.slice/user-1000.slice/user@1000.service/app.slice/mrsMThatcher.service` |

Exactly one wrapper and one Python child were running, both in the service
cgroup. No production process existed outside it.

## Startup findings

The new child:

- loaded the used-image history and durable bot state;
- preserved the existing quote and meme schedules;
- completed startup successfully;
- completed loop ticks at 20:51:47, 20:52:47 and 20:53:47;
- remained outside every posting window during observation;
- emitted no traceback, error, critical event, stale quotation/source-SHA
  warning, unresolved-receipt warning or semantic-veto-unavailable warning;
- did not enter a wrapper restart loop.

Startup performs the bot's established state normalisation and durable backup
write. `lines_used.json` and `images_used.json` were byte-identical before and
after restart:

- lines history: `fb7fb299cc4220b12ea8be959c5fa37948ecf2d70bd776a8b2e22a1ccfb4cffd`;
- image history: `1ab550d72d985145896843e643fdc742f8a62cfece9811670ed7a7a12495d5ef`.

The state hash changed between the 20:51:29 preflight snapshot and startup. The
old child performed its normally scheduled reply-discovery state update in that
brief interval, and the new child then performed its normal canonical state save
and backup rotation. No posting receipt appeared and no deployment command
edited state or history.

## Semantic-veto status

The local, network-free `shadow-status` command reported:

- feature enabled: true;
- configured mode: `shadow`;
- active enforcement: false;
- manifest valid and unstale: true;
- quotations: 610;
- images: 91;
- known pairs: 22,066;
- allow: 21,938;
- veto: 128;
- adjudicated unknown: 167;
- current-manifest production-selection failures: 0;
- feature network calls: 0.

No semantic-veto, editorial or generated-image enforcement was activated.

## Analytics isolation

The engagement analytics timer remained `active/waiting`. Its last trigger and
next trigger were unchanged through the main-service restart. The oneshot
service retained `Result=success`, `ExecMainStatus=0` and `NRestarts=0`.

`systemctl --user --failed` returned no failed units.

## Rollback

External rollback bundle:

`/home/tonym/.local/state/mrsMThatcher/deployments/historical_context_source_review_20260721T204801+0100_9672b5c`

Important bundle hashes:

| File | SHA-256 |
|---|---|
| active runtime source archive (`1e5731b`) | `d170fb5b377771b7ceead0bd5e3a43f79020640f0b8f3d7b3229651292ee9165` |
| immediate parent source archive (`f0127c3`) | `2e2f006cba6b24408c331fc034ef21dab9c0c52102c67e78b162ab812a3f9b07` |
| local configuration backup | `69231e462b50a08056db0536e4564c75b9befa27f7f1f250eabe3d330e247f83` |
| user unit backup | `6ea88612b237cd7afff987d90473096cfa501ff09aebb039d42cd755a9c855ef` |
| wrapper backup | `fafd91ad1e95687b36e3fb8805ccc7f57625d460031cfbb242d2a7253c87784f` |

Rollback was not required. If a production defect is later confirmed:

1. require all five receipt/ambiguity barriers to be clear;
2. revert commits `9672b5c` and `f0127c3` in that order, test and push the
   resulting revert commit;
3. restart only `mrsMThatcher.service`;
4. verify one wrapper, one child, the previous source hashes, clear receipts and
   normal loop ticks.

The active-runtime archive is the offline fallback if Git is unavailable. Do not
restore production state, histories or receipts from the rollback bundle.

## Explicit confirmations

- X test post or reply: none;
- media upload: none;
- provider/model call introduced by deployment: none;
- production receipt mutation: none;
- manual production-state or history mutation: none;
- semantic-veto enforcement activated: no;
- generated-image pool activated: no;
- hybrid retrieval activated: no;
- analytics unit changed or restarted: no;
- rollback required: no.

DEPLOYMENT SUCCESSFUL
