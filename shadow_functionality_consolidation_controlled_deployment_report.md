# Shadow-functionality consolidation controlled deployment report

Date: 19 July 2026
Host: `big-nas-2`
Project: `/disks/disk1/etc/mrsMThatcher`

## Result

The reviewed shadow-functionality consolidation was committed, pushed and activated successfully. Only `mrsMThatcher.service` was restarted.

Deployed source commit:

`3a4c69df32698ebb2eba4676a7c760c4064509f3`

Commit subject:

`Consolidate shadow features and fix veto coverage`

The commit was pushed to `origin/master`; local `HEAD` and `origin/master` matched before activation.

## Validation before activation

The complete offline suite had already passed against the exact deployed source:

```text
1959 passed, 1 skipped, 3 warnings in 726.16s (0:12:06)
```

An additional focused deployment run passed:

```text
python3 -m pytest -q \
  tests/test_shadow_functionality_consolidation.py \
  tests/test_quote_image_semantic_veto_shadow.py \
  tests/test_quote_attribution_cleanup.py

69 passed in 9.18s
```

Targeted `py_compile`, `git diff --check` and staged `git diff --cached --check` all passed. The only staged-check issue found was trailing Markdown whitespace in three new reports; it was removed before commit and did not affect code.

The strict network-free semantic-veto preflight reported:

- status: ready;
- active enforcement: false;
- policy: `affirmative-material-contradiction-rules-v3-runtime-eligible-610-coverage-v2`;
- quotation count: 610;
- historical-image count: 91;
- resolved pairs: 22,066;
- allow: 21,938;
- veto: 128;
- adjudicated unknown: 167;
- not adjudicated: 33,277;
- global no-safe-image quotations: 0;
- manifest SHA-256: `594b5fc9fdc1599a5f03c53a09fbcdbccfaa4d2003c019a3aefc070aa13d11f6`;
- provider/network calls: 0.

All five receipt barriers were clear before backup and again immediately before restart:

- regular quote/image post receipt;
- meme post receipt;
- historical-context reply receipt;
- conversational confirmed-reply receipt;
- ambiguous post outcome.

The bot was in a sleeping loop state with its next normal quote/image post more than one hour away.

## Deployment architecture

The systemd user unit runs:

`/usr/local/bin/runMrsMThatcher2`

The wrapper invokes:

`/usr/local/bin/mrsMThatcher2.py`

That entry point is a symlink to the committed repository file:

`/disks/disk1/etc/mrsMThatcher/mrsMThatcher2.py`

Consequently no second installed source tree was copied. Committing the reviewed repository fixed the source version; the controlled service restart loaded it and its project-local modules and manifests.

## Rollback archive

The parent-commit runtime sources and manifests were archived outside Git before restart:

`/home/tonym/.local/state/mrsMThatcher/deployments/20260719T230605+0100-3a4c69d/previous_runtime_source.tar`

Archive SHA-256:

`3706c4857ac60907b77680593ff5cf16ba61bdc152d2cab6b234e4e2bd0e7066`

The archive is owned by `tonym` with mode `0600`. Its source commit is:

`b8c66ec6afbcb8085cea5cac2168cb6956f76f53`

Key before/after hashes:

| Runtime input | Before | Activated |
|---|---|---|
| `mrsMThatcher2.py` | `d4fd4cea29439e7d7d517b8d2bb42f9cd0cb1d2c893b1a02d4280675e1c271da` | `c2c3e9dc8c0604d9a73eb9e08787976760699f3aad9cd87ba898bba5f53d38dd` |
| `semantic_alignment/quote_image_semantic_veto.py` | `7bbc859aacbcda0ca5c15633a0e25aff6656de353bec1577db0447e746b9dff1` | `7110ba9528ebc6cc705cc260156d70e228111dc9d836ceaea65b611f44d53b2f` |
| `semantic_alignment/hybrid_reply_retrieval.py` | `a53c44e8730d4258d0d973f9a5d155cee19994cfd161a3932957cd09427d1f6c` | `3e700d9d2e6324a8947b087948266943e9920590dd362198e64f7604c1dd22b1` |
| `material_veto_v3_shadow_manifest.json` | `fd04f7500b5824811ea987dfbe885ce034dc8af7b1f6286e533cd78628e5c3f4` | `594b5fc9fdc1599a5f03c53a09fbcdbccfaa4d2003c019a3aefc070aa13d11f6` |
| `runtime_eligible_quote_manifest.json` | `9cbd528481a41dc5d3bd11d7199b4bd0d1e0bb240caa702feeffce0bf4301a8c` | `3f0ecdc4dd806edb1398e189fe225c9b7e8e303035c266970af77d3efc68b86b` |

The installed entry-point hash after restart exactly matched the committed `mrsMThatcher2.py` hash.

## Service transition

Before restart:

| Field | Value |
|---|---|
| Active state | `active/running` |
| Wrapper/MainPID | `4025393` |
| Python child | `4025394` |
| Start time | `Sun 2026-07-19 12:29:24 BST` |
| Systemd restart count | `0` |

Command used:

```text
systemctl --user restart mrsMThatcher.service
```

After restart:

| Field | Value |
|---|---|
| Active state | `active/running` |
| Wrapper/MainPID | `1595442` |
| Python child | `1595443` |
| Start time | `Sun 2026-07-19 23:07:45 BST` |
| Systemd restart count | `0` |
| Cgroup | `/user.slice/user-1000.slice/user@1000.service/app.slice/mrsMThatcher.service` |

Exactly one wrapper and one Python child exist, both in the service cgroup. No production bot process exists outside that cgroup.

## Startup verification

The production log confirmed:

- logging initialised once;
- 27 local overrides loaded without exposing secret values;
- the retired live hybrid configuration was ignored and no hybrid worker was created;
- semantic-veto coverage-v2 manifest loaded with SHA-256 `594b5fc9fdc1599a5f03c53a09fbcdbccfaa4d2003c019a3aefc070aa13d11f6`;
- semantic-veto active enforcement remained false;
- original-editorial scoring remained shadow-only;
- generated identity-policy processing was suspended because the generated pool is disabled;
- instance lock acquired once;
- 69 original images and 90 meme candidates loaded;
- existing quotation and meme schedules retained;
- `Bot started successfully`;
- the first loop tick and a second tick 60 seconds later completed normally;
- no traceback, error, critical event, crash or restart loop;
- no stale quotation-analysis/source-SHA warning;
- no unresolved receipt warning.

The only warning was the deliberate compatibility notice that the obsolete local hybrid-retrieval block is ignored. It does not initialise embeddings, queues, workers or telemetry; `ps -T` showed only the main Python thread.

`shadow-status` after activation confirmed:

- configured mode: shadow;
- active enforcement: false;
- current manifest valid and unstale;
- current-manifest events: 0, as no new quote/image selection was manufactured or awaited;
- historical events from older manifest versions excluded from current-manifest totals: 32;
- production-selection-change failures: 0;
- network calls added by the feature: 0.

No X test post was made. The bot performed only its normal startup state load and durable normalisation; no production state, receipt, history or ledger was edited manually.

## Other services and host health

`mrs-engagement-analytics.service` and `mrs-engagement-analytics.timer` were not stopped, started, restarted or reloaded. The timer remained:

- enabled;
- active/waiting;
- next scheduled run: `Sun 2026-07-19 23:15:19 BST`.

`systemctl --user --failed` reported zero failed units.

## Rollback procedure

If a production defect is later established:

1. Verify all five receipt barriers are clear.
2. Revert commit `3a4c69df32698ebb2eba4676a7c760c4064509f3` in Git and push the revert.
3. Restart only `mrsMThatcher.service`.
4. Confirm one wrapper, one child, clear receipts, the parent manifest hash and a normal loop tick.

If Git is unavailable, the secured archive is the fallback source. Stop only the main bot service after clearing receipts, restore the complete archived runtime set, then start the service once and verify the same invariants. Do not restore production state or history files.

## Explicit confirmations

- Main bot restart: one controlled restart
- Engagement analytics touched: no
- X test posting: none
- AI or provider call introduced by deployment: none
- Hybrid retrieval active in production: no
- Generated image pool activated: no
- Generated identity shadow active: no
- Semantic-veto enforcement active: no
- Original-editorial enforcement active: no
- Manual production-state or receipt mutation: none
- Rollback required: no

DEPLOYMENT SUCCESSFUL
