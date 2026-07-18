# MrsMThatcher systemd User Service Report

## Outcome

The MrsMThatcher production bot was migrated from a manually launched wrapper
to a local systemd user service on `big-nas-2` as user `tonym`. The unit is
installed, enabled, active, and backed by `Linger=yes`, so the user manager can
start it at boot and keep it running after logout. The NAS was not rebooted.

## Host And User Preflight

- Host: `big-nas-2`
- User: `tonym` (`uid=1000`, `gid=1000`)
- systemd: `249 (249.11-0ubuntu3.21)`
- User manager: reachable at `/user.slice/user-1000.slice/user@1000.service`
- Initial lingering status: `yes`
- Final lingering status: `yes`
- Non-sudo linger attempt: not required and not made
- Project mount: ZFS `disks/disk1` mounted read-write at `/disks/disk1`
- Project directory: `/disks/disk1/etc/mrsMThatcher`

The user manager was already running. Its overall status was degraded because
the separate engagement analytics oneshot had a retained failed state; that did
not prevent unit installation or operation.

## Existing Architecture

The established executable `/usr/local/bin/runMrsMThatcher2` is a symlink to
the tracked wrapper `/disks/disk1/etc/mrsMThatcher/runMrsMThatcher2`. The
wrapper:

- changes to `/disks/disk1/etc/mrsMThatcher`;
- requires and sources the persistent
  `/disks/disk1/etc/mrsMThatcher/mrsMThatcher.env` file;
- launches `/usr/local/bin/mrsMThatcher2.py`;
- waits 60 seconds and restarts the child after a child exit.

The environment file is owned by `tonym`, mode `0600`, and contains the
persistent boot-safe credentials. No credential values were read into this
report, copied into the unit, or imported from an interactive session. The
ignored `mrsMThatcher.local.json` remains the persistent runtime configuration.

Before migration the manual processes were:

- wrapper PID `3631076`;
- child PID `3987356`;
- cgroup `/user.slice/user-1000.slice/session-1907.scope`.

The wrapper does not forward a group `SIGINT` as a complete tree shutdown: the
child exited cleanly, but the wrapper remained in its restart sleep. This was a
blocking stop-semantics defect for a plain `KillSignal=SIGINT` unit. The wrapper
was not modified. The unit instead uses `ExecStop` to interrupt the child,
wait, and then terminate the sleeping wrapper before systemd applies its
control-group fallback.

## Receipt Barrier

The following were absent before stopping the manual process tree, before the
first service start, before the stop-path test, and after final startup:

- `regular_post_receipt.json`;
- `meme_post_receipt.json`;
- `historical_context_reply_receipt.json`;
- `confirmed_reply_receipt.json`;
- `ambiguous_post_outcome.json`.

No receipt was lost or reconciled as part of the migration.

## Unit

- Unit name: `mrsMThatcher.service`
- Unit path: `/home/tonym/.config/systemd/user/mrsMThatcher.service`
- Owner and mode: `tonym:tonym`, `0644`
- SHA-256: `6ea88612b237cd7afff987d90473096cfa501ff09aebb039d42cd755a9c855ef`
- Static verification: passed
- Enablement: `enabled`

Complete unit contents:

```ini
[Unit]
Description=MrsMThatcher production bot
Documentation=file:///disks/disk1/etc/mrsMThatcher/
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory=/disks/disk1/etc/mrsMThatcher
ExecStartPre=/bin/bash -c 'for _ in {1..12}; do if [[ -d /disks/disk1/etc/mrsMThatcher && -x /usr/local/bin/runMrsMThatcher2 && -r /disks/disk1/etc/mrsMThatcher/mrsMThatcher2.py && -r /disks/disk1/etc/mrsMThatcher/mrsMThatcher.env ]]; then exit 0; fi; echo "MrsMThatcher project or wrapper not ready; retrying in 10 seconds" >&2; /bin/sleep 10; done; echo "MrsMThatcher project or wrapper unavailable after 120 seconds" >&2; exit 1'
ExecStart=/usr/local/bin/runMrsMThatcher2
ExecStop=/bin/bash -c 'child=$(/usr/bin/pgrep -P "$${MAINPID}" -f "^python3 /usr/local/bin/mrsMThatcher2.py$" || true); if [[ -n "$${child}" ]]; then /bin/kill -INT "$${child}"; for _ in {1..30}; do if ! /bin/kill -0 "$${child}" 2>/dev/null; then break; fi; /bin/sleep 1; done; fi; if /bin/kill -0 "$${MAINPID}" 2>/dev/null; then /bin/kill -TERM "$${MAINPID}"; fi'
Restart=on-failure
RestartSec=60
TimeoutStopSec=180
KillMode=control-group
KillSignal=SIGINT
Environment=PYTHONUNBUFFERED=1
Environment=PATH=/usr/local/bin:/usr/bin:/bin
StandardOutput=journal
StandardError=journal
SyslogIdentifier=mrsMThatcher

[Install]
WantedBy=default.target
```

The bounded pre-start check retries for no more than 120 seconds and checks the
actual project, wrapper, bot entry point, and persistent environment file. A
failure prevents startup; systemd's 60-second restart delay prevents a tight
loop.

## Migration And Stop Test

The old child received `SIGINT` and logged `Bot stopped by KeyboardInterrupt`.
The wrapper was then terminated during its restart sleep. Both old PIDs exited
before the service was started, and no production process remained outside the
new unit cgroup.

The first systemd instance used wrapper PID `4055590` and child PID `4055592`.
A controlled `systemctl --user stop` test then proved the hardened unit stop
path: both exited in about one second, the unit became `inactive/dead`, and zero
production processes remained. It was then started normally.

Final runtime state:

- MainPID / wrapper: `4063576`
- Python child: `4063578`
- Active state: `active`
- Substate: `running`
- systemd restarts: `0`
- Control group:
  `/user.slice/user-1000.slice/user@1000.service/app.slice/mrsMThatcher.service`
- Wrapper count: 1
- Child count: 1
- Production processes outside the service cgroup: 0

Both final processes are owned by `tonym`. The production lock contains child
PID `4063578`.

## Runtime Health

The journal records one initial start, the controlled stop-path test, and the
final start. The wrapper deliberately redirects child stdout/stderr to
`/dev/null`, so detailed bot startup remains in the established
`mrsMThatcher.log`; the journal retains systemd and wrapper lifecycle events.

The application log and local runtime validation confirm:

- cleaned quotation source: 619 canonical records;
- eligible confirmed Thatcher quotations: 613;
- unresolved and ineligible research records: 6;
- quote analysis source SHA-256:
  `10310a9d62c03a87f2c1e55fa10286d1413216b8c0cb34cb0dbe4b3c12f19bee`;
- v3 shadow manifest SHA-256:
  `8b202352ddf89af5860446f4dd20577832981fcf778316b2b01121f7c5550706`;
- v3 known pair count: 22,157;
- `active_enforcement=false`;
- `Bot started successfully`;
- multiple normal 60-second main-loop ticks;
- no stale source warning, traceback, crash, or restart loop.

`principle_reply` remains present in the loaded production source. Systemd
starts the unchanged wrapper and bot code, so it introduces no X, xAI, Gemini,
Vertex, OpenAI, Anthropic, or other provider call.

## Boot Autostart

Final proof:

```text
Linger=yes
mrsMThatcher.service enabled
mrsMThatcher.service active
```

Boot autostart is configured and operational at the systemd level. No manual
command is required from Tony. The actual next boot can be verified without
changing the service using:

```bash
systemctl --user status mrsMThatcher.service --no-pager
journalctl --user -u mrsMThatcher.service -b --no-pager
```

The NAS was not rebooted during this task.

## Rollback

Do not disable lingering automatically because other user services rely on the
same user manager. To remove the unit:

```bash
systemctl --user disable --now mrsMThatcher.service
rm -f /home/tonym/.config/systemd/user/mrsMThatcher.service
systemctl --user daemon-reload
```

Confirm the cgroup and production processes are gone before restoring manual
operation:

```bash
systemctl --user show mrsMThatcher.service -p ControlGroup -p ActiveState -p SubState
pgrep -af 'runMrsMThatcher2|mrsMThatcher2.py'
```

If manual wrapper operation must be restored after removal, first confirm all
receipt barriers are clear and no production process exists, then run:

```bash
cd /disks/disk1/etc/mrsMThatcher
nohup /usr/local/bin/runMrsMThatcher2 >/dev/null 2>&1 &
```

Finally confirm exactly one wrapper and one child. The ignored timestamped
pre-`ExecStop` unit copy may be retained for audit, but is not needed for
rollback and is not versioned.
