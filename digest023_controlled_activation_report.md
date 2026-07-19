# Digest023 controlled activation report

Date: 18 July 2026 (Europe/London)
Host: `big-nas-2`
User: `tonym`
Project: `/disks/disk1/etc/mrsMThatcher`
Unit restarted: `mrsMThatcher.service` only

## Executive result

The independently reviewed main-bot changes were activated by one controlled
restart of `mrsMThatcher.service`. All preconditions passed. The replacement
service is healthy with exactly one wrapper and one Python child, no restart
loop, no pending receipt, no stale quotation-analysis warning, and no failed
user unit.

No test post was made. The engagement analytics service and timer were not
stopped, restarted, enabled, disabled or otherwise controlled by this task.

## Pre-restart checks

Service state before restart:

```text
ActiveState=active
SubState=running
MainPID=1805
ExecMainStartTimestamp=Sat 2026-07-18 12:16:59 BST
NRestarts=0
ControlGroup=/user.slice/user-1000.slice/user@1000.service/app.slice/mrsMThatcher.service
```

Process layout before restart:

```text
1805 /bin/bash /usr/local/bin/runMrsMThatcher2
1808 python3 /usr/local/bin/mrsMThatcher2.py
```

Reviewed source hashes before restart:

```text
ced125be1fc70d5c5ec3d7c42b248f4d599c45296266b78c0a7d9be04b35c25b  mrsMThatcher2.py
cf2479e1de80b47d1514a9142f502d7ed9e882378438950346b00c29222fb985  reply_strategy.py
```

The hashes exactly matched the versions approved in
`digest023_independent_diff_review_report.md`.

The receipt barrier was checked twice, including immediately before restart.
None of these files existed:

```text
regular_post_receipt.json
meme_post_receipt.json
confirmed_reply_receipt.json
historical_context_reply_receipt.json
ambiguous_post_outcome.json
```

`systemctl --user --failed` reported zero failed units. The analytics timer was
enabled and active/waiting before restart.

## Restart

The only control command used was:

```text
systemctl --user restart mrsMThatcher.service
```

The old child handled the stop as an expected `KeyboardInterrupt`. The wrapper
reported child exit status 0, systemd stopped the old cgroup, and one replacement
cgroup was started. No `SIGKILL`, broad process kill, host reboot or analytics
control command was used.

## Post-restart state

Final service state:

```text
ActiveState=active
SubState=running
MainPID=1075041
ExecMainStartTimestamp=Sat 2026-07-18 18:43:35 BST
NRestarts=0
ControlGroup=/user.slice/user-1000.slice/user@1000.service/app.slice/mrsMThatcher.service
```

The service cgroup contains exactly:

```text
1075041 /bin/bash /usr/local/bin/runMrsMThatcher2
1075042 python3 /usr/local/bin/mrsMThatcher2.py
```

`pgrep` found no wrapper or bot child outside that pair. A repeat check more
than one minute after startup showed the same PIDs and `NRestarts=0`, proving
there was no immediate restart loop.

Source hashes after restart remained:

```text
ced125be1fc70d5c5ec3d7c42b248f4d599c45296266b78c0a7d9be04b35c25b  mrsMThatcher2.py
cf2479e1de80b47d1514a9142f502d7ed9e882378438950346b00c29222fb985  reply_strategy.py
```

The child start time is later than those reviewed file versions, and the child
executes `/usr/local/bin/mrsMThatcher2.py`, whose established link targets the
project source. These are therefore the activated versions.

## Startup findings

Relevant startup log findings:

- local configuration loaded successfully;
- the v3 attribution-cleaned semantic-veto manifest loaded with policy
  `affirmative-material-contradiction-rules-v3-attribution-cleanup-candidate`,
  SHA-256 `8b202352ddf89af5860446f4dd20577832981fcf778316b2b01121f7c5550706`,
  and 22,157 pairs;
- semantic-veto `active_enforcement=false`;
- the instance lock was acquired by the sole replacement child;
- 69 original images and 90 meme candidates were found;
- original editorial shadow scoring and generated-identity production scoring
  loaded successfully;
- quote used history loaded as 401 quote hashes and image history as 54
  basenames;
- existing quote and meme schedules were retained;
- startup completed at 18:43:37 with `Bot started successfully`;
- the first loop found no quote, meme, mention or quote-tweet action due and
  entered the normal sleep;
- no traceback, crash, serious warning, receipt recovery error or source-SHA
  mismatch appeared;
- the only warning in the transition window was the expected
  `Bot stopped by KeyboardInterrupt` from the old child during the authorised
  restart.

The absence of `Quote source SHA differs from analysed source`, stale-analysis
warnings or override-staleness warnings confirms the cleaned quotation metadata
loaded without the former warning.

## Receipt and state integrity

The post-restart receipt check remained clear for all five receipt/ambiguous
outcome files. No posting receipt was lost or reconciled during activation.

No operator command edited production state, histories, ledgers or receipts.
The bot's established startup path performed its normal durable state save and
backup rotation; the resulting current state and its two newest backups are
byte-identical:

```text
f2a3c89b722fd6af9c7a531b7b83e835a4c017ca94fd53f437d4dbdc266c774e  bot_state.json
f2a3c89b722fd6af9c7a531b7b83e835a4c017ca94fd53f437d4dbdc266c774e  bot_state.json.bak1
f2a3c89b722fd6af9c7a531b7b83e835a4c017ca94fd53f437d4dbdc266c774e  bot_state.json.bak2
```

This confirms no semantic production-state change was introduced by startup.
`lines_used.json`, `images_used.json`, posting history and ledgers were not
manually written by this task.

## Analytics isolation

The analytics timer remained enabled and was never controlled by this task. It
reached its ordinary scheduled trigger at 18:45 while verification was in
progress. The existing oneshot collector exited normally with status 0 and the
timer returned to active/waiting:

```text
timer state: active (waiting)
last oneshot: exited 0/SUCCESS at 18:45:04
next trigger: 19:00:28 BST
```

This natural scheduled run is independent of the main-bot restart and confirms
the analytics repair remains healthy.

## Final system health

Final `systemctl --user --failed --no-pager` result:

```text
UNIT LOAD ACTIVE SUB DESCRIPTION
0 loaded units listed.
```

Final receipt barrier: clear.
Duplicate bot process: none.
Restart loop: none.
Traceback or serious startup warning: none.
Stale quotation-analysis warning: none.
Semantic-veto active enforcement: false.
Generated-image pool activation by this task: none.
Hybrid-retrieval activation by this task: none.

No X test content was posted or manufactured. No manual production-state,
receipt, ledger or posting-history change occurred. No source or environment
file was modified, and `mrsMThatcher.env` was neither displayed nor changed. No
commit or push occurred.

ACTIVATION SUCCESSFUL
