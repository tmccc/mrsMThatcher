# MrsMThatcher Adversarial Bugfix Controlled Deployment Report

Date: 19 July 2026
Host: `big-nas-2`
Project: `/disks/disk1/etc/mrsMThatcher`

## Verdict

**DEPLOYMENT SUCCESSFUL**

The reviewed bug fixes and regenerated v3 shadow artefacts were committed, pushed, and activated by restarting only `mrsMThatcher.service`. The service is stable with one wrapper and one Python child. No test post was sent to X, no receipt was pending, and semantic-veto enforcement remains disabled.

## Commit and deployment path

- Commit: `fb8dc6596882f4b6b5efaedd75e5215a8d6d2536`
- Parent: `3e45eebe06bb94b54006608908bc9cb07a22218b`
- Branch: `master`
- Remote: `origin/master`
- Push result: `3e45eeb..fb8dc65 master -> master`
- Local and remote commit hashes matched before activation.
- Working tree was clean before activation.

The service uses the repository directly:

```text
/usr/local/bin/runMrsMThatcher2 -> /disks/disk1/etc/mrsMThatcher/runMrsMThatcher2
/usr/local/bin/mrsMThatcher2.py -> /disks/disk1/etc/mrsMThatcher/mrsMThatcher2.py
WorkingDirectory=/disks/disk1/etc/mrsMThatcher
```

No separate installed copy was replaced. Restarting the existing service loaded the committed repository files and their normal imports.

## Validation

Full offline suite:

```text
1947 passed, 1 skipped, 3 warnings in 729.16s (0:12:09)
```

The warnings were dependency deprecations from Starlette/httpx and Beautiful Soup/lxml. No project test failed.

Additional gates:

- `python3 -m py_compile ...`: passed
- Ruff fatal checks `E9,F63,F7,F82`: passed
- `git diff --check`: passed
- `git diff --cached --check`: passed
- External model/API calls made by tests: zero

Commit summary:

```text
15 files changed, 1220 insertions(+), 90 deletions(-)
```

## Deployment barriers

Immediately before restart:

- `mrsMThatcher.service`: active/running
- wrapper PID: `3210338`
- Python child PID: `3210345`
- start time: `Sun 2026-07-19 07:34:41 BST`
- systemd restart count: `0`
- process topology: exactly one wrapper and one child in the service cgroup
- regular-post receipt: absent
- meme-post receipt: absent
- historical-context reply receipt: absent
- conversational confirmed-reply receipt: absent
- ambiguous-post outcome marker: absent
- engagement timer: active/waiting and enabled

The v3 manifest passed the network-free preflight before restart:

```text
configured mode: shadow
active enforcement: false
manifest valid: true
quotes: 610
images: 91
pairs: 22066
allow: 21938
veto: 128
production-selection-change failures: 0
network calls: 0
```

## Rollback backup

Previous runtime files from parent commit `3e45eeb` were archived outside Git at:

```text
/home/tonym/.local/state/mrsMThatcher/deployments/20260719T122753+0100-fb8dc65/
```

Archive:

```text
previous_source.tar
SHA-256 919203d140da1dd9fc5ee5c7befe7016c7f42f352cdb99226878a5b2b083a3dd
```

Runtime hashes:

| File | Previous SHA-256 | Activated SHA-256 |
|---|---|---|
| `historical_context_formatter.py` | `f20e67a9c6ab128e511a27f149f4e248f9d48b70e35a95f7898396b8e16adf11` | `c56473c8104139d2383e71f431198d1c81f3f2557fe1cdab8ade726590a0afe9` |
| `mrsMThatcher2.py` | `ecdf692c6d61c2ef788ddc36de4f9d3a8bb2621e140072f99b7b36f6e07dce92` | `404a6d966e0e107eaacb8baca8d3fc7487baf5482c751540a94e6a583e308d5f` |
| `reply_strategy.py` | `1863e61c342200a3fa161e8d8f0d4655a4b9a70c1df7005f8c9acb4d7211e0e6` | `aaffd9ac099fc63a705de8ba38a18fb203e3027ce0bed42eeadea585c09bd7a6` |
| `semantic_alignment/hybrid_reply_retrieval.py` | `1c165ed471012c6ec8147caf32945122c936f68f472b5bf5d37c7a11f28c7cd3` | `560a11ab7be6317ea18c485b747a0aaa572efa0d96d583320f7f2f326d759330` |
| `semantic_alignment/quote_image_semantic_veto.py` | `de733366718cbf9050dd024c28126d327d42e68470656405ea284e6a29a535e8` | `ac48e9a2a4ed1e965d1bdad1b14581c6869302975e33684eafef499ece9d6412` |
| `material_veto_v3_shadow_manifest.json` | `dd52144300f0c9dc5d5ed12034b9ee1b3af40650771a713776f0d1142a9f98bb` | `fd04f7500b5824811ea987dfbe885ce034dc8af7b1f6286e533cd78628e5c3f4` |
| `runtime_eligible_quote_manifest.json` | `ec05a86fcbd883c07d4c6a887913a7dda3f09162ef05a41d577eddfcca93b269` | `9cbd528481a41dc5d3bd11d7199b4bd0d1e0bb240caa702feeffce0bf4301a8c` |

The service entry point's installed-path hash matched the committed `mrsMThatcher2.py` hash after restart.

## Controlled activation

Command used:

```bash
systemctl --user restart mrsMThatcher.service
```

No other service was stopped, restarted, or reloaded.

After restart:

- state: active/running
- wrapper PID: `4025393`
- Python child PID: `4025394`
- start time: `Sun 2026-07-19 12:29:24 BST`
- systemd restart count: `0`
- cgroup: `/user.slice/user-1000.slice/user@1000.service/app.slice/mrsMThatcher.service`
- process topology: exactly one wrapper and one child
- child working directory: `/disks/disk1/etc/mrsMThatcher`
- Python executable: `/usr/bin/python3.10`

The same wrapper and child PIDs remained present through a subsequent main-loop tick.

## Startup findings

The startup log confirmed:

- instance lock acquired once;
- v3 semantic-veto manifest loaded successfully;
- manifest policy `affirmative-material-contradiction-rules-v3-runtime-eligible-610`;
- manifest SHA-256 `fd04f7500b5824811ea987dfbe885ce034dc8af7b1f6286e533cd78628e5c3f4`;
- `22066` pairs loaded;
- `active_enforcement=false`;
- existing quote and meme schedules retained;
- `Bot started successfully`;
- normal main-loop ticks at 12:29:27 and 12:30:27;
- no stale quotation-analysis/source-SHA warning;
- no traceback, error, crash, duplicate process, or restart loop;
- no unresolved receipt barrier after startup.

The bot performed its normal startup state load/normalisation and backup write. No state, history, ledger, or receipt was edited manually, and no test post was manufactured.

## Analytics and host health

`mrs-engagement-analytics.timer` was not touched. It remained enabled and waiting. Its independently scheduled 12:30 run completed successfully:

```text
Result=success
ExecMainStatus=0
start: Sun 2026-07-19 12:30:37 BST
exit:  Sun 2026-07-19 12:30:39 BST
next:  Sun 2026-07-19 12:45:21 BST
```

`systemctl --user --failed` reported zero failed units.

## Rollback procedure

If a later production defect requires rollback:

1. Verify all five receipt barriers are absent.
2. Create a normal revert commit for `fb8dc6596882f4b6b5efaedd75e5215a8d6d2536` and push it.
3. Restart only `mrsMThatcher.service`.
4. Confirm one wrapper, one child, clear receipts, and a clean startup.

The archived parent-commit files above are the offline recovery source if Git is unavailable. Do not restore them while the service is running; stop the service only after clearing receipt barriers, restore the complete archived runtime set, and start the service once.

## Explicit confirmations

- X test posts: none
- External AI/provider calls introduced by deployment: none
- Active semantic-veto enforcement: false
- Generated-image activation: none
- Hybrid retrieval promotion: none; it remains shadow-only
- Production selection changed by semantic-veto shadow: no
- Engagement service/timer control commands: none
- Further code changes after activation: none, apart from this report
- Further commit or push after activation: none

**DEPLOYMENT SUCCESSFUL**
