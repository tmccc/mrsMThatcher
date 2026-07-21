# AI-first reply strategy hardening controlled deployment

Date: 21 July 2026
Host: `big-nas-2`
Project: `/disks/disk1/etc/mrsMThatcher`
Service restarted: `mrsMThatcher.service` only

## Verdict

The reviewed AI-first reply hardening was curated, tested, committed, pushed and activated successfully. The replacement process completed normal startup with exactly one wrapper and one Python child, retained the same child throughout a 70-second restart-loop observation window, and reported no traceback, error, critical event, stale-analysis warning or unresolved receipt.

Semantic-veto enforcement remains false. Generated-image identity processing remains suspended because generated images are disabled. The original-editorial selector remains shadow-only. No X test post or provider call was manufactured.

## Repository curation

The commit retains:

- maintained reply strategy and evidence code;
- deterministic provider-pilot and corpus-matrix tools;
- regression and integration tests;
- synthetic fixtures;
- final samples, aggregate evaluations, validation records, source hashes and reports;
- the final Grok 4.3 qualification and the evidence supporting rejection of Grok 4.5.

The following remain local and ignored:

- raw provider responses;
- mutable cost ledgers and execution state;
- generated 6,100-case fixture files;
- per-case result directories;
- superseded corpus, repair, qualification and smoke runs;
- local secrets and ignored production configuration.

The curated aggregate research addition is below 1 MB. No `mrsMThatcher.env`, `mrsMThatcher.local.json`, raw response, cost ledger, production state, receipt, history or log file was staged.

## Tests and commit

The exact staged tree had Git tree hash:

`a7e3fdeef94d577b0dccedb06606cd71d2e28608`

Validation from that exact tree:

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q`:
  - 1,999 passed;
  - one expected skip;
  - three dependency deprecation warnings;
  - elapsed time 718.15 seconds;
- `git diff --cached --check`: passed;
- staged secret/config scan: passed;
- local branch matched `origin/master` before the commit.

Source commit:

`1e5731b09cb146de5b22b090bec3332e10a9746d` (`Harden and qualify AI-first reply strategy`)

Push result:

- branch: `master`;
- remote: `origin`;
- local and upstream commit after fetch: `1e5731b09cb146de5b22b090bec3332e10a9746d`;
- source working tree clean before deployment.

## Deployment architecture

The systemd user service has:

- working directory `/disks/disk1/etc/mrsMThatcher`;
- wrapper `/usr/local/bin/runMrsMThatcher2`;
- bot entry point `/usr/local/bin/mrsMThatcher2.py`.

The bot entry point is a symlink to the committed project file:

`/usr/local/bin/mrsMThatcher2.py -> /disks/disk1/etc/mrsMThatcher/mrsMThatcher2.py`

Imported `reply_strategy.py` and `reply_evidence.py` are loaded from the project working directory. No source copy or configuration migration was required; restarting the service loaded the committed modules.

Recorded deployed hashes:

| File | SHA-256 |
|---|---|
| `mrsMThatcher2.py` | `aa25463121607ffe5147312aa2ddb8bf91912a9db6fa996d0639d2d444d690a6` |
| `reply_strategy.py` | `9faba7f64b0d88b849f32d23e882bfae8e5f154ecc59ccea1b465d80cf0ed290` |
| `reply_evidence.py` | `d970feebcccb19acc734f807b2d46941bfbe364540f77a298bd21f3eee578ec9` |
| ignored local configuration | `69231e462b50a08056db0536e4564c75b9befa27f7f1f250eabe3d330e247f83` |
| wrapper | `fafd91ad1e95687b36e3fb8805ccc7f57625d460031cfbb242d2a7253c87784f` |
| user unit | `6ea88612b237cd7afff987d90473096cfa501ff09aebb039d42cd755a9c855ef` |

The ignored local configuration remained mode `0600`, owned by `tonym:tonym`, and was not modified.

## Preflight

The read-only production bootstrap passed before restart:

- strategy enabled: true;
- strategy version: `ai-first-reply-v3`;
- proposer/evidence/reviewer: `grok-4.3`;
- maximum model calls: 6;
- maximum revisions: 1;
- fail closed: true;
- eligible evidence packets: 610;
- semantic-veto manifest pairs: 22,066;
- semantic-veto active enforcement: false.

Immediately before restart:

| Barrier | Result |
|---|---|
| Regular-post receipt | absent |
| Meme-post receipt | absent |
| Historical-context receipt | absent |
| Conversational confirmed-reply receipt | absent |
| Ambiguous post outcome | absent |
| Pending AI-first drafts | 0 |
| Legacy V1 drafts | 0 |

The same barriers were clear after startup and after the observation window. No receipt or draft was removed, reset, migrated or reconciled manually.

## Rollback bundle

Bundle directory:

`/home/tonym/.local/state/mrsMThatcher/rollback/ai_first_reply_hardening_20260721T133824Z`

The directory is mode `0700`; contained files are mode `0600`. `SHA256SUMS` validated every item:

| Item | SHA-256 |
|---|---|
| deployed revision record | `e02a3514942d54e7e02ba74f0eaa09975c2ce4494c28e8f8344287663cae46aa` |
| local configuration backup | `69231e462b50a08056db0536e4564c75b9befa27f7f1f250eabe3d330e247f83` |
| user unit backup | `6ea88612b237cd7afff987d90473096cfa501ff09aebb039d42cd755a9c855ef` |
| previous revision record | `4cc79bfa0411198f9daf0bfffe2c9df5d9965a83068aaf8ab15e0d26fc47ed91` |
| previous runtime source archive | `dec489cac0a1528f3cd1e7041713656fc8ff9aa12108ccc7e6a07ec99416debd` |
| wrapper backup | `fafd91ad1e95687b36e3fb8805ccc7f57625d460031cfbb242d2a7253c87784f` |

The previous source revision is `b752e80d3e9232201665dc37dd7daf7d81fe53d8`. Rollback was not required. If later required, first clear all five receipt/ambiguity barriers, stop only `mrsMThatcher.service`, atomically restore the three runtime files from the archive and the backed-up local configuration, then start the service once and verify the old hashes and one-wrapper/one-child layout. Analytics must remain untouched.

## Service state

Before restart:

| Property | Value |
|---|---|
| Active state | active/running |
| Wrapper PID | 695911 |
| Python child PID | 695912 |
| Start time | 20 July 2026 19:01:40 BST |
| Restart count | 0 |

After restart and the 70-second observation:

| Property | Value |
|---|---|
| Active state | active/running |
| Wrapper PID | 3936954 |
| Python child PID | 3936955 |
| Start time | 21 July 2026 14:40:36 BST |
| Restart count | 0 |
| Cgroup | `/user.slice/user-1000.slice/user@1000.service/app.slice/mrsMThatcher.service` |

Exactly one wrapper and one child were present, both in the service cgroup. Neither PID changed during the wrapper's 60-second restart interval.

## Startup findings

The new child logged:

- 27 local configuration overrides applied;
- corrected V3 semantic-veto shadow loaded with 22,066 pairs;
- `active_enforcement=false`;
- original-editorial shadow enabled and non-enforcing;
- generated identity-policy processing suspended while the generated pool is disabled;
- instance lock acquired;
- existing quotation, image and scheduling state loaded;
- `Bot started successfully`.

The only warning in the restart window was the expected `KeyboardInterrupt` used to stop the old child gracefully. There was no traceback, crash, error, critical event, stale source-SHA warning, unresolved receipt warning, duplicate process or restart loop. `systemctl --user --failed` was empty.

## Analytics isolation

No analytics unit was stopped, started, restarted, reloaded or modified. After deployment:

- `mrs-engagement-analytics.timer`: enabled, active/waiting;
- last trigger: 21 July 2026 14:30:37 BST;
- next scheduled trigger: 21 July 2026 14:45:02 BST;
- oneshot result: success;
- oneshot exit status: 0;
- analytics restart count: 0.

## Explicit confirmations

- No X test post or reply was made.
- No media was uploaded.
- No model/provider call was made during deployment.
- No production state, history, receipt, ledger or schedule was manually altered.
- No secret-bearing file was staged, committed or modified.
- No generated-image feature was activated.
- No semantic-veto enforcement was activated.
- No hybrid retrieval was activated.
- Analytics remained untouched.

DEPLOYMENT SUCCESSFUL
