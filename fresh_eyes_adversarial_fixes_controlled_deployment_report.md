# Fresh-Eyes Fixes Controlled Deployment

Date: 19 July 2026
Host: `big-nas-2`
User: `tonym`
Project: `/disks/disk1/etc/mrsMThatcher`
Service restarted: `mrsMThatcher.service` only

## Verdict

The seven adversarial-review fixes were activated successfully. The complete offline suite passed before activation, the corrected 610-quotation semantic-veto manifest loaded in shadow mode, and the service remained stable through a wrapper retry interval and a normal empty mention poll.

No test content was posted. No test failure required a further code change. At the deployment stop point, the changes remained uncommitted; version-control action was separately authorised afterwards.

## Test result

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
1881 passed, 1 skipped, 3 warnings in 695.18s (11m35s)
```

The skip is the repository's expected optional test. The warnings were one Starlette/httpx deprecation and two BeautifulSoup/lxml deprecations. There were no failures.

Additional deployment checks:

```text
python3 -m py_compile ...        passed
git diff --check                passed
semantic-veto shadow preflight  valid
network calls from preflight    0
active veto enforcement         false
```

## Deployment architecture

The user unit uses:

```text
WorkingDirectory=/disks/disk1/etc/mrsMThatcher
ExecStart=/usr/local/bin/runMrsMThatcher2
```

Both executable paths are symlinks into the working tree:

```text
/usr/local/bin/runMrsMThatcher2
  -> /disks/disk1/etc/mrsMThatcher/runMrsMThatcher2

/usr/local/bin/mrsMThatcher2.py
  -> /disks/disk1/etc/mrsMThatcher/mrsMThatcher2.py
```

No installation copy was required. Restarting the service loaded the tested working-tree versions of:

- `mrsMThatcher2.py`;
- `reply_strategy.py`;
- `historical_context_formatter.py`;
- `semantic_alignment/quote_image_semantic_veto.py`;
- the corrected v3 shadow manifest;
- the runtime-eligible quotation manifest.

Changes to the digest, cleanup tool, simulator and harness are support-tool changes and do not require process activation.

## Source hashes

Previously active source from Git `HEAD b267a330983c5db2aec2c5722ce9e994b43bbeda`:

```text
7223d1a54fa7642d2dceadfd34ac91369aa50b8f0ad926e2c46d6bb91ff2b600  mrsMThatcher2.py
fbb7487c6e9b3dfe15731ed4ee931754baa20895cbae3213dd73d01feeb39ea2  reply_strategy.py
66a45e3587705728422eb8cc57cc2350064693d6fac32e0e005a0c93c9a23941  historical_context_formatter.py
3f371a98e2a39898d4297890e78e50ce7ee67b9fe08182f2c47bcc3687a869cb  semantic_alignment/quote_image_semantic_veto.py
8b202352ddf89af5860446f4dd20577832981fcf778316b2b01121f7c5550706  material_veto_v3_shadow_manifest.json
```

Activated tested source:

```text
ecdf692c6d61c2ef788ddc36de4f9d3a8bb2621e140072f99b7b36f6e07dce92  mrsMThatcher2.py
1863e61c342200a3fa161e8d8f0d4655a4b9a70c1df7005f8c9acb4d7211e0e6  reply_strategy.py
f20e67a9c6ab128e511a27f149f4e248f9d48b70e35a95f7898396b8e16adf11  historical_context_formatter.py
de733366718cbf9050dd024c28126d327d42e68470656405ea284e6a29a535e8  semantic_alignment/quote_image_semantic_veto.py
dd52144300f0c9dc5d5ed12034b9ee1b3af40650771a713776f0d1142a9f98bb  material_veto_v3_shadow_manifest.json
ec05a86fcbd883c07d4c6a887913a7dda3f09162ef05a41d577eddfcca93b269  runtime_eligible_quote_manifest.json
```

`/usr/local/bin/mrsMThatcher2.py` resolved to and matched the activated main-file hash.

## Receipt barrier

Immediately before the restart, all applicable barriers were clear:

```text
regular_post_receipt.json: absent
meme_post_receipt.json: absent
historical_context_reply_receipt.json: absent
confirmed_reply_receipt.json: absent
ambiguous_post_outcome.json: absent
```

They remained absent after startup and the stability interval.

## Service state

Before:

```text
ActiveState=active
SubState=running
wrapper/MainPID=2285642
child PID=2285644
ExecMainStartTimestamp=Sun 2026-07-19 01:57:20 BST
NRestarts=0
```

After:

```text
ActiveState=active
SubState=running
wrapper/MainPID=3210338
child PID=3210345
ExecMainStartTimestamp=Sun 2026-07-19 07:34:41 BST
NRestarts=0
ControlGroup=/user.slice/user-1000.slice/user@1000.service/app.slice/mrsMThatcher.service
```

The same new PIDs remained after more than 70 seconds. There is exactly one wrapper and one Python child, both in the service cgroup. `systemctl --user --failed` returned no failed units.

## Startup validation

Startup completed at 07:34:44 BST with `Bot started successfully`.

Confirmed:

- the corrected policy `affirmative-material-contradiction-rules-v3-runtime-eligible-610` loaded;
- manifest SHA-256 is `dd52144300f0c9dc5d5ed12034b9ee1b3af40650771a713776f0d1142a9f98bb`;
- manifest pair count is 22,066;
- manifest quotation count is 610;
- manifest allow/veto counts are 21,938/128;
- active semantic-veto enforcement is false;
- there was no stale quotation-analysis warning;
- there was no traceback, error, critical event, unresolved receipt, or restart loop;
- existing quote and meme schedules were retained;
- the bot entered its normal sleep cycle.

At 07:36:44 the ordinary scheduled mention poll returned zero candidates. It made no reply, model request, or post and returned to sleep. No test post was manufactured.

The engagement analytics timer remained `active/waiting` with its existing schedule and was not stopped, restarted, or changed.

## Rollback

Rollback bundle:

`/home/tonym/.local/state/mrsMThatcher/deployments/adversarial_bugfix_20260719T063312Z`

It contains:

- `previous_source/`: the Git `HEAD` versions that were active before restart;
- `tested_source/`: the activated versions;
- `previous_source.tar`;
- `SHA256SUMS.previous`;
- `SHA256SUMS.tested`.

Both checksum manifests passed `sha256sum -c`.

Rollback procedure:

1. Confirm the five receipt/ambiguity barriers are absent.
2. Stop only `mrsMThatcher.service`.
3. Restore each file from `previous_source/` through a same-directory temporary file followed by an atomic rename.
4. Leave `runtime_eligible_quote_manifest.json` in place; the previous manifest does not reference it.
5. Start `mrsMThatcher.service`.
6. Confirm the old hashes, one wrapper/child pair, clean startup, and no pending receipt.

The activated source remains recoverable from `tested_source/`.

## Repository state

The deployment intentionally activated tested, then-uncommitted working-tree changes. No commit or push was performed as part of deployment. Production state was not manually edited; normal startup and the ordinary empty mention poll performed their established state writes.

No X test post, provider test call, analytics service change, active semantic-veto enforcement, generated-image activation, hybrid-retrieval activation, commit, or push occurred during deployment.

DEPLOYMENT SUCCESSFUL
