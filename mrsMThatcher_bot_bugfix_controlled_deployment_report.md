# MrsMThatcher bug-fix controlled deployment

Date: 19 July 2026
Host: `big-nas-2`
Project: `/disks/disk1/etc/mrsMThatcher`
Service restarted: `mrsMThatcher.service` only

## Outcome

Commit `cce68dbfd575216de10249b2e64ed7321cca9b3f` was pushed to
`origin/master` and activated successfully. The service is healthy with one
wrapper and one Python child, no restart loop, no failed receipt barrier, no
startup traceback and no stale quotation-analysis warning.

The systemd wrapper and bot executable are symlinks into the repository, so no
separate installed file was replaced:

```text
/usr/local/bin/runMrsMThatcher2
  -> /disks/disk1/etc/mrsMThatcher/runMrsMThatcher2
/usr/local/bin/mrsMThatcher2.py
  -> /disks/disk1/etc/mrsMThatcher/mrsMThatcher2.py
```

## Validation

The clean full offline suite passed after two fixture-contract corrections:

```text
1849 passed, 1 skipped, 3 dependency deprecation warnings in 692.28s
```

The final static checks also passed:

```text
python3 -m py_compile: passed
ruff check --select F: passed
git diff --check: passed
```

The production attribution gate still requires exactly 610 eligible completed
quotation packets. Test mode applies the same attribution filter, fails closed
when none remain, and permits deliberately minimal attributed fixtures.

## Commit and hashes

```text
deployed commit: cce68dbfd575216de10249b2e64ed7321cca9b3f
upstream before restart: cce68dbfd575216de10249b2e64ed7321cca9b3f
historical_context_formatter.py: 66a45e3587705728422eb8cc57cc2350064693d6fac32e0e005a0c93c9a23941
mrsMThatcher2.py: 7223d1a54fa7642d2dceadfd34ac91369aa50b8f0ad926e2c46d6bb91ff2b600
reply_strategy.py: fbb7487c6e9b3dfe15731ed4ee931754baa20895cbae3213dd73d01feeb39ea2
semantic_alignment/hybrid_reply_retrieval.py: 1c165ed471012c6ec8147caf32945122c936f68f472b5bf5d37c7a11f28c7cd3
```

## Receipt barrier

The barrier was clear immediately before the restart and after startup:

```text
regular_post_receipt.json: absent
meme_post_receipt.json: absent
historical_context_reply_receipt.json: absent
confirmed_reply_receipt.json: absent
ambiguous_post_outcome.json: absent
```

No receipt was removed, reconciled or modified by the deployment commands.

## Service state

Before restart:

```text
ActiveState=active
SubState=running
wrapper/MainPID=1574852
child PID=1574853
ExecMainStartTimestamp=Sat 2026-07-18 21:40:11 BST
NRestarts=0
```

After restart and the delayed stability check:

```text
ActiveState=active
SubState=running
wrapper/MainPID=2285642
child PID=2285644
ExecMainStartTimestamp=Sun 2026-07-19 01:57:20 BST
NRestarts=0
ControlGroup=/user.slice/user-1000.slice/user@1000.service/app.slice/mrsMThatcher.service
```

The cgroup contained exactly the wrapper and child. The bot logged successful
startup, retained its existing quote and meme schedules, completed another
main-loop tick after 60 seconds, and returned to normal sleep.

The v3 quotation/image semantic-veto manifest loaded with 22,157 pairs and
`active_enforcement=false`. No hybrid or semantic-veto production promotion was
performed.

`systemctl --user --failed` reported zero failed units. The engagement analytics
timer remained enabled and waiting; its oneshot service retained its prior
successful result and was not stopped, started or restarted by this deployment.

## Rollback

The four previous runtime modules from parent commit `6979c6d` are stored at:

```text
/home/tonym/.local/state/mrsMThatcher/deployments/bug_hardening_20260719T005656Z
```

The backup hash file is `SHA256SUMS` in that directory. If rollback becomes
necessary, first require all five receipt files to be absent. Then atomically
restore the four files with their recorded ownership and modes and restart only
`mrsMThatcher.service`. Do not restore posting state, histories, receipts or
analytics data.

## Restrictions observed

- No test post was sent to X.
- No external model call was introduced by deployment.
- No production receipt, ledger or posting history was manually changed.
- The bot performed only its normal startup state load and durable save.
- `mrsMThatcher.env` was neither displayed nor modified.
- The engagement analytics service and timer were not controlled.
- No generated-image, hybrid-retrieval or semantic-veto enforcement setting was promoted.

DEPLOYMENT SUCCESSFUL
