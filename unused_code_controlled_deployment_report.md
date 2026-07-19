# Unused-code cleanup controlled deployment

Date: 18 July 2026
Host: `big-nas-2`
User: `tonym`
Project: `/disks/disk1/etc/mrsMThatcher`
Unit restarted: `mrsMThatcher.service` only

## Outcome

The committed and pushed unused-code cleanup was activated successfully by one
controlled restart of `mrsMThatcher.service`. The service is healthy with one
wrapper and one Python child, no restart loop, no pending receipt, no startup
traceback, and no stale quotation-analysis warning.

No separate file installation was needed. The systemd unit invokes symlinks
whose targets are the committed project files. The restart therefore loaded the
committed source directly.

## Commit and repository preflight

```text
branch: master
commit: 6979c6d66e4535ad4ca14a60adfca607017a4813
upstream: origin/master
upstream commit: 6979c6d66e4535ad4ca14a60adfca607017a4813
working tree before deployment: clean
git diff --check: passed
```

The repository had no unpushed or unstaged source change before activation.

## Test treatment

The full 12-minute suite was deliberately **not** rerun, following the
operator's explicit instruction overriding that part of the supplied deployment
prompt.

The exact committed source had already completed the independent final review:

```text
collection: 1,784 tests
full suite: 1,783 passed, 1 expected skip
git diff --check: passed
verdict: READY FOR SEPARATELY AUTHORISED DEPLOYMENT
```

Fresh deployment-specific checks were limited to:

```text
python3 -m py_compile mrsMThatcher2.py reply_strategy.py: passed
git diff --check: passed
reply_strategy import resolution: /disks/disk1/etc/mrsMThatcher/reply_strategy.py
```

No pytest process was started during deployment.

## Active deployment paths

The user unit is:

`/home/tonym/.config/systemd/user/mrsMThatcher.service`

Its relevant paths are:

```text
WorkingDirectory=/disks/disk1/etc/mrsMThatcher
ExecStart=/usr/local/bin/runMrsMThatcher2
```

Resolved links:

```text
/usr/local/bin/runMrsMThatcher2
  -> /disks/disk1/etc/mrsMThatcher/runMrsMThatcher2

/usr/local/bin/mrsMThatcher2.py
  -> /disks/disk1/etc/mrsMThatcher/mrsMThatcher2.py
```

The wrapper changes to the project directory and invokes
`/usr/local/bin/mrsMThatcher2.py`. Lazy `reply_strategy` imports resolve to the
project's committed `reply_strategy.py`. No `/usr/local/bin` file or symlink was
replaced.

Only these changed files are part of the production bot execution path:

- `mrsMThatcher2.py`;
- `reply_strategy.py`.

The other cleanup changes affect offline support, research and test utilities.

## Hashes

Committed and active source:

```text
5938ae9329ca533471ea5a02fbb262127574e22432fe6d622c55528c33de9225  mrsMThatcher2.py
5938ae9329ca533471ea5a02fbb262127574e22432fe6d622c55528c33de9225  /usr/local/bin/mrsMThatcher2.py
7a6bc66e905a6ba8dccd8798450f6392e6161c4533fc9ab0eba053ab78f0ac49  reply_strategy.py
fafd91ad1e95687b36e3fb8805ccc7f57625d460031cfbb242d2a7253c87784f  runMrsMThatcher2
fafd91ad1e95687b36e3fb8805ccc7f57625d460031cfbb242d2a7253c87784f  /usr/local/bin/runMrsMThatcher2
```

Previously active parent-commit source:

```text
ced125be1fc70d5c5ec3d7c42b248f4d599c45296266b78c0a7d9be04b35c25b  mrsMThatcher2.py
cf2479e1de80b47d1514a9142f502d7ed9e882378438950346b00c29222fb985  reply_strategy.py
```

The deployed symlink hash matches the committed `mrsMThatcher2.py` hash.
`reply_strategy.py` is loaded from the same clean committed working tree.

## Rollback backup

The external rollback bundle is:

`/home/tonym/.local/state/mrsMThatcher/deployments/unused_code_cleanup_20260718T203940Z`

It contains:

- the previous committed `mrsMThatcher2.py`;
- the previous committed `reply_strategy.py`;
- copies of both installed symlinks;
- commit and link-target identity;
- `SHA256SUMS` for old and new files.

The old source copies use the same project modes as the active files:

```text
mrsMThatcher2.py  tonym:tonym  775
reply_strategy.py tonym:tonym  664
```

`sha256sum -c SHA256SUMS` passed after the backup was finalised.

## Receipt barrier

The receipt barrier was checked before backup, immediately before the restart,
and after startup. All checks were clear:

```text
regular_post_receipt.json: absent
meme_post_receipt.json: absent
historical_context_reply_receipt.json: absent
confirmed_reply_receipt.json: absent
ambiguous_post_outcome.json: absent
```

No receipt was removed, reconciled or modified by deployment commands.

## Before restart

```text
ActiveState=active
SubState=running
wrapper/MainPID=1075041
child PID=1075042
ExecMainStartTimestamp=Sat 2026-07-18 18:43:35 BST
NRestarts=0
ControlGroup=/user.slice/user-1000.slice/user@1000.service/app.slice/mrsMThatcher.service
```

There was exactly one wrapper and one child.

## Deployment command

Because the executable links already targeted the committed source, activation
required only:

```bash
systemctl --user restart mrsMThatcher.service
```

The receipt barrier was included in the same bounded shell operation
immediately before that command. No wrapper, analytics or host-wide control
command was used.

## After restart

```text
ActiveState=active
SubState=running
wrapper/MainPID=1574852
child PID=1574853
ExecMainStartTimestamp=Sat 2026-07-18 21:40:11 BST
NRestarts=0
ControlGroup=/user.slice/user-1000.slice/user@1000.service/app.slice/mrsMThatcher.service
```

The process layout was checked immediately and again after the wrapper's
60-second retry interval. The same two PIDs remained, proving there was no
child crash or restart loop. No duplicate wrapper or child was found.

## Startup log findings

Startup completed at `21:40:13 BST` with `Bot started successfully`.

Confirmed startup facts:

- 27 local configuration overrides loaded successfully;
- the instance lock was acquired;
- the attribution-cleaned v3 semantic-veto manifest loaded with 22,157 pairs;
- semantic-veto active enforcement remained false;
- 69 original images and 90 meme candidates were found;
- original editorial and existing generated-identity production configuration
  loaded successfully without being changed by this deployment;
- current quote and image histories loaded normally;
- existing quote and meme schedules were retained;
- the first loop found no quote, meme or reply action due and slept normally;
- no traceback, `ERROR` or `CRITICAL` event appeared;
- no unresolved/invalid receipt warning appeared;
- no `Quote source SHA differs from analysed source` warning appeared;
- no stale analysis or stale override warning appeared.

The only transition warning was the expected `Bot stopped by KeyboardInterrupt`
from the old child during the authorised systemd stop.

## State integrity

Deployment tooling did not write posting state, histories, receipts or ledgers.
The bot performed its normal startup state save. Current state and the two
newest durable backups are byte-identical:

```text
59e7c59f9095aae8526637709d5e1c0477b4ac860aae3cdffe3792aee74c9f0d  bot_state.json
59e7c59f9095aae8526637709d5e1c0477b4ac860aae3cdffe3792aee74c9f0d  bot_state.json.bak1
59e7c59f9095aae8526637709d5e1c0477b4ac860aae3cdffe3792aee74c9f0d  bot_state.json.bak2
```

This shows that startup did not introduce a semantic state change.

## Analytics isolation and system health

The engagement analytics service and timer were inspected but never controlled.
Their final state was:

```text
mrs-engagement-analytics.timer: active/waiting, enabled
mrs-engagement-analytics.service: inactive/dead, last result success, status 0
analytics NRestarts: 0
```

The timer's last and next trigger values were unchanged during the deployment
window. `systemctl --user --failed --no-pager` reported zero failed units.

## Rollback procedure

Rollback was not required. If a later blocking startup defect is traced to this
cleanup, first require all five receipt barriers to be clear, then atomically
restore the two old files and restart only the bot unit:

```bash
cd /disks/disk1/etc/mrsMThatcher
backup=/home/tonym/.local/state/mrsMThatcher/deployments/unused_code_cleanup_20260718T203940Z

for f in regular_post_receipt.json meme_post_receipt.json \
  historical_context_reply_receipt.json confirmed_reply_receipt.json \
  ambiguous_post_outcome.json; do
  test ! -e "$f" || exit 1
done

cp -p "$backup/mrsMThatcher2.py" .mrsMThatcher2.py.rollback
cp -p "$backup/reply_strategy.py" .reply_strategy.py.rollback
sync -f .mrsMThatcher2.py.rollback
sync -f .reply_strategy.py.rollback
mv .mrsMThatcher2.py.rollback mrsMThatcher2.py
mv .reply_strategy.py.rollback reply_strategy.py
systemctl --user restart mrsMThatcher.service
```

After rollback, verify the old hashes, one-wrapper/one-child topology, startup
log and receipt barriers. Do not restore any state or history file.

## Restrictions observed

- No X test post or manufactured input was created.
- `mrsMThatcher.env` was neither displayed nor modified.
- No production receipt, ledger, history or posting state was manually altered.
- The engagement analytics unit and timer were untouched.
- No generated-image, hybrid-retrieval or semantic-veto setting was changed.
- No code change, staging, commit, push or deployment-time source edit occurred.
- The report is intentionally untracked.

DEPLOYMENT SUCCESSFUL
