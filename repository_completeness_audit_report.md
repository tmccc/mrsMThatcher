# Repository Completeness Audit

Date: 19 July 2026
Host: `big-nas-2`
Project: `/disks/disk1/etc/mrsMThatcher`

## Executive verdict

The production repository was broadly complete, but four material reproducibility gaps were found:

1. the main user-systemd unit existed only under Tony's home directory and inside a historical report;
2. the live analytics units were tracked only as `.example` files rather than canonical installable units;
3. `mrsMThatcher.local.example.json` omitted 15 current generated-image and editorial-policy settings, retained the old v2 semantic-veto path, and documented obsolete reply spacing;
4. no root dependency manifest described the optional packages needed by the maintained research tools and complete offline test suite.

Those gaps have been corrected in the working tree. Four recent engineering/deployment reports that were unintentionally hidden by the broad `digest*.md` ignore rule have also been made eligible for versioning. No service, production state, receipt, history, log, database, secret, or live unit file was changed.

## Systemd unit audit

### Installed units

The user manager's complete MrsMThatcher-specific installation consists of:

| Unit | Live path | SHA-256 |
|---|---|---|
| Main bot | `/home/tonym/.config/systemd/user/mrsMThatcher.service` | `6ea88612b237cd7afff987d90473096cfa501ff09aebb039d42cd755a9c855ef` |
| Analytics collector | `/home/tonym/.config/systemd/user/mrs-engagement-analytics.service` | `3200db98963952cf64caa14cf4892d1eb5e1eae94cc9003d763527a1a789f0ae` |
| Analytics timer | `/home/tonym/.config/systemd/user/mrs-engagement-analytics.timer` | `1ffd7115d5603e4372e41d796f4911ad0b69fbc4683c02fac656d93f7c4a71da` |

No relevant unit exists under `/home/tonym/.local/share/systemd/user`. The enablement links under `default.target.wants` and `timers.target.wants` correctly point to the installed user units.

The two analytics unit hashes already matched their tracked `.example` files byte for byte. The main bot unit was not independently versioned.

### Can the live units be symlinks?

Technically, yes. The installed systemd 249 documentation explicitly supports loading units outside the normal search path through symlinks or `systemctl link`. A temporary same-name symlink passed:

```text
systemd-analyze --user verify .../mrsMThatcher.service
exit status 0
```

It is nevertheless the wrong reliability trade-off on this host. The repository is on `/disks/disk1`, a separately mounted ZFS dataset. Tony's lingering user manager starts at boot and may scan `default.target` before that dataset is available. If the live unit itself is a broken symlink at that moment, systemd cannot parse it; consequently, the unit's bounded `ExecStartPre` mount wait never gets an opportunity to run.

The chosen arrangement is therefore:

- canonical, secret-free units in `deploy/systemd-user/`;
- regular live files under `~/.config/systemd/user/`;
- `deploy/systemd-user/install.sh --check` for exact drift detection;
- `deploy/systemd-user/install.sh --install` for atomic per-file copying and `daemon-reload`;
- no implicit enable, start, stop, reload, or restart operation.

The check command currently reports all three installed units as `ok`.

## Other repository gaps

### Local configuration template

The private `mrsMThatcher.local.json` key set was compared with the tracked example without displaying values. The example lacked:

- generated-pool enablement, paths, glob, analysis path, spacing and origin boost;
- original-editorial shadow enablement, analysis path, weight and adjustment cap;
- generated-identity shadow and production switches, audit path and penalties.

The example now includes all 15 fields with source-default-safe disabled switches. Its semantic-veto path now names the current attribution-cleaned v3 manifest, and minimum reply spacing now matches the 1,800-second source default.

The private environment file was compared by variable name only. `mrsMThatcher.env.example` contains exactly the same variable names, so no environment-template change was needed.

### Full-suite and research dependencies

`requirements.txt` correctly covers the production bot and local integration harness, but not the complete maintained support-tool surface. `requirements-dev.txt` now composes the existing requirements and records the additional packages used by image analysis, Gemini research, JSON schemas, local ONNX retrieval, and reviewer applications.

The local E5 runtime remains intentionally outside Git because its ONNX model is approximately 470 MB. Its exact repository, revision, file sizes, SHA-256 hashes, package pins and local-files-only policy are already tracked in the hybrid retrieval model manifest, so it is reproducible without versioning model weights.

### Reports

The broad `digest*.md` rule correctly ignores repeatedly generated digest output, but it also hid three permanent engineering reports:

- `digest023_followup_and_analytics_verification_report.md`;
- `digest023_independent_diff_review_report.md`;
- `digest023_controlled_activation_report.md`.

The ignore rules now exempt `digest*_report.md`. The most recent controlled-deployment report, `mrsMThatcher_adversarial_bugfix_controlled_deployment_report.md`, was already the sole ordinary untracked file and is also suitable for versioning.

Routine digests such as `digest.md`, numbered digest outputs, incident input snapshots and ad hoc digest analyses remain ignored.

## Production asset coverage

The important production assets are already in Git:

- 69 original `images/t*.jpg` files;
- 90 daily-meme image files;
- 79 approved generated-image PNG files;
- canonical quotation source and quote analysis;
- original and generated image analyses;
- reply research corpus and hybrid retrieval index;
- attribution-cleaned v3 semantic-veto manifest;
- generated-identity and editorial-analysis metadata;
- main wrapper and production entry point;
- environment and local-configuration examples;
- engagement identity correction manifest and analytics schema.

The active `/usr/local/bin/runMrsMThatcher2` and `/usr/local/bin/mrsMThatcher2.py` paths are symlinks to their tracked repository files. Older `/usr/local/bin/mrsMThatcher*` files refer to the superseded pre-v2 bot and are not active dependencies of the current service.

## Intentionally unversioned material

The following should remain outside Git:

- `mrsMThatcher.env` and all live credentials;
- `mrsMThatcher.local.json` and host-specific operational switches;
- runtime watch lists;
- bot state, used-quote/image histories, receipts, locks and logs;
- engagement SQLite data, exports, raw responses, locks and backups;
- semantic-veto and hybrid shadow runtime histories;
- provider raw responses and cost-recovery runtime state;
- downloaded model weights and caches;
- source snapshots, simulator databases and write-ahead logs;
- pre-deployment backups and production-state recovery payloads;
- rejected, rights-pending and superseded image-discovery payloads;
- macOS resource-fork files and cache directories.

These are secrets, mutable operational records, large reproducible payloads, or historical recovery data. Their schemas, authoritative summaries, manifests and audit reports are versioned where useful.

## Validation

Completed checks:

```text
systemd-analyze --user verify <three canonical units>       passed
deploy/systemd-user/install.sh --check                      3 units ok
bash -n deploy/systemd-user/install.sh                      passed
focused deployment/config tests                            11 passed
optional support dependency imports                        passed
environment variable-name parity                           exact match
secret-pattern scan                                        only documented dummy key
```

`python3 -m pip check` reported one unrelated host-level issue: Ubuntu's `pygobject` package advertises an absent `pycairo` dependency. No maintained MrsMThatcher module imports either package, so it was not added to project requirements.

## Production isolation

Before and after this audit:

```text
MainPID=4025393
child PID=4025394
ExecMainStartTimestamp=Sun 2026-07-19 12:29:24 BST
NRestarts=0
ActiveState=active
SubState=running
```

Exactly one wrapper and one Python child remain in `mrsMThatcher.service`. No `systemctl daemon-reload`, install, enable, start, stop or restart command was run against the live user manager. The analytics timer was not touched.

## Working-tree changes

- `.gitignore`
- `README.md`
- `deploy/systemd-user/install.sh`
- `deploy/systemd-user/mrsMThatcher.service`
- `deploy/systemd-user/mrs-engagement-analytics.service` (renamed from `.example`)
- `deploy/systemd-user/mrs-engagement-analytics.timer` (renamed from `.example`)
- `engagement_analytics/README.md`
- `mrsMThatcher.local.example.json`
- `requirements-dev.txt`
- `tests/test_deployment_assets.py`
- `repository_completeness_audit_report.md`
- four existing reports now eligible for versioning

No commit, push, service activation or live installation was performed.

## Recommendation

Version the current working-tree additions. Keep the live units as regular files, and make `deploy/systemd-user/install.sh --check` a standard deployment preflight. Use `--install` only when the check reports drift, followed by the normal receipt-barrier and controlled-restart procedure when activation is separately authorised.
