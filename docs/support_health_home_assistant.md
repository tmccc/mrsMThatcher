# MrsMThatcher support health in Home Assistant

## Purpose

`mrs-support-health-monitor.service` is a read-only host-side oneshot. It asks a
separate question from foreground bot health: are the explicitly expected
scheduled support jobs and the `mrsmthatcher.org` downloader operating
normally? Its result never changes `sensor.mrs_m_thatcher_bot_health`, and it
never restarts, enables, disables, starts, or stops a unit or container.

The monitor normally runs every two minutes from
`mrs-support-health-monitor.timer`. It uses the local user-systemd manager and
Docker CLI over Unix sockets, then atomically publishes one compact document
for native Home Assistant entities and automations. Home Assistant has no
Docker-socket access.

## Explicit component configuration

The private host configuration is:

```text
~/.config/mrsMThatcher/support-health-monitor.json
```

It must be mode `0600`. Start from
`deploy/support-health-monitor.example.json`, then record only deliberately
expected timer/service pairs. An entry with `"expected": false` remains
visible as `ignored` and never alerts. Do not infer intent from every tracked
unit and do not enable a timer simply because it is listed.

The deployed host currently considers these support pairs:

- engagement analytics, every 15 minutes;
- OpenAI published-cost cache, every 30 minutes;
- semantic-veto shadow health, daily at 23:35 Europe/London;
- prospective-conversation extraction, hourly only when its separate rebuild
  and validation has deliberately activated it.

Timer-triggered `Type=oneshot` services are normally `inactive/dead` between
runs. That state alone is healthy. The monitor instead checks the timer's
loaded, enabled and active state; its last and next elapse; the latest service
result and exit status; current runtime; and the last observed success.
Systemd's actual next-trigger epoch takes precedence over the configured
nominal interval, preventing false stale results across 23-hour and 25-hour
British clock-change days. The monitor requests Unix-epoch timestamps from
`systemctl`; on the deployed systemd 249 host, which lacks that renderer, it
reads only timestamp properties as raw microseconds from the local systemd
D-Bus API instead of parsing ambiguous BST/GMT display strings.

Configuration is strictly bounded. Unit names and component IDs must be
plausible and unique, the downloader status path must be absolute, and its
selector must be exactly one Compose project/service pair or one stable exact
container name. Unknown fields and executable or command fields are rejected.

The private environment file is also mode `0600`:

```text
~/.config/mrsMThatcher/support-health-monitor.env
```

Its two settings are host paths, for example:

```text
MRS_SUPPORT_HEALTH_CONFIG=/home/USER/.config/mrsMThatcher/support-health-monitor.json
MRS_SUPPORT_HEALTH_OUTPUT=/HOST/HOME-ASSISTANT/CONFIG/.runtime/mrs_m_thatcher_support_health.json
```

No API credential belongs in either file.

## Downloader contract

Container state alone is insufficient. The downloader writes an atomic,
machine-readable status document from its real outer download pass. The
private host path is configured as `cycle_status_file`; it is not stored in a
served website document tree.

The schema is:

```json
{
  "schema_version": 1,
  "cycle_started_epoch": 1787848000,
  "cycle_completed_epoch": null,
  "last_success_epoch": 1787589000,
  "last_progress_epoch": 1787848060,
  "state": "running",
  "duration_seconds": null,
  "item_count": 286,
  "last_error_class": null
}
```

At the beginning of a whole pass, `state` becomes `running` while the previous
`last_success_epoch` is preserved. A validated document/PDF result advances
the non-content `last_progress_epoch` and modest item count. Only completion of
the entire current target set writes `success` and advances
`last_success_epoch`. A caught terminal cycle failure writes `failed` and a
bounded category, never response content, URLs, cookies, credentials, headers,
or a traceback. A killed or hung process leaves `running`, so progress and
runtime limits expose it. Status-write failure is fail-open for the downloader
and produces only a bounded local warning.

Cycle evidence is accepted only when its latest relevant timestamp belongs to
the current container generation, using Docker's container start time and a
small 30-second clock/order tolerance. A prior generation's document is
`awaiting_current_container_cycle` during startup grace and
`cycle_status_from_previous_container` afterwards. For a running cycle,
progress age starts at `last_progress_epoch` when present and otherwise at
`cycle_started_epoch`, so a hang before the first validated item still reaches
the configured progress deadline.

Transient state counts same-container Docker restart deltas separately from
distinct container IDs. One planned replacement is allowed; reaching the
configured generation threshold within the restart window reports
`rapid_container_replacements`. Old generations are pruned and repeated polls
of one ID do not increase the count.

The live downloader refreshes its source sitemap on a persisted three-day
schedule. A normal sitemap refresh takes roughly 18 minutes before any new
targets. The initial PDF catch-up is much longer, so the host can use a long
initial whole-pass runtime limit together with a much shorter progress-age
limit (30 minutes on the discovered host, allowing margin around the roughly
10-minute local manifest scan).
After initial catch-up completes, reduce `maximum_cycle_seconds` to a generous
steady-state limit while retaining a success freshness limit of about two
missed three-day cycles.

## Output and status meanings

The host atomically writes the file mounted in Home Assistant as:

```text
/config/.runtime/mrs_m_thatcher_support_health.json
```

The support sensor has four states:

- `starting`: all structure is sound, but an expected component is still
  within its finite first-run or Docker-health grace;
- `healthy`: every expected component is scheduled and its latest/current work
  is normal;
- `degraded`: a recoverable run, deadline, freshness, runtime, progress, or
  restart problem exists;
- `failed`: an expected unit/container is missing, disabled, masked, inactive,
  structurally broken, hard-failed, stopped, dead, OOM-killed, or Docker
  unhealthy.

Aggregate precedence is `failed`, `degraded`, `starting`, then `healthy`.
Ignored entries do not participate. The problem signature is a deterministic
hash of sorted component-ID/reason pairs, so a changed incident is debounced
and updates the same ordinary device notification even if the aggregate state
does not change.

Home Assistant provides:

- `sensor.mrs_m_thatcher_support_health`;
- `binary_sensor.mrs_m_thatcher_support_problem`;
- `sensor.mrs_m_thatcher_support_incident`.

Home Assistant independently treats output older than six minutes as a
problem using `now()`. `starting` is not a problem while the source timestamp
is valid. Alerts use `notify.millie_powerwall_alert_devices`, wait three
minutes, use the stable tag `mrs_m_thatcher_support_health`, and are ordinary
notifications. A `monitor_stale` alert uses dedicated wording and the last
valid checked time/age; it never repeats an old aggregate summary or claims
that components remain healthy. Recovery waits for the primary sensor to be
`healthy`, then requires two more stable minutes and rechecks both entities.

## Operation

Install or validate the tracked user units without activating them:

```bash
deploy/systemd-user/install.sh --check
deploy/systemd-user/install.sh --install
```

Enable this monitor explicitly:

```bash
systemctl --user enable --now mrs-support-health-monitor.timer
```

Run it now and inspect its schedule and logs:

```bash
systemctl --user start mrs-support-health-monitor.service
systemctl --user status mrs-support-health-monitor.timer
journalctl --user -u mrs-support-health-monitor.service --since today
```

Inspect raw source documents using the host paths configured in the private
environment/config files:

```bash
jq . /HOST/HOME-ASSISTANT/CONFIG/.runtime/mrs_m_thatcher_support_health.json
jq . /PRIVATE/DOWNLOADER/STATE/downloader-health.json
```

Inspect last/next timer elapses and the latest service result:

```bash
systemctl --user list-timers --all \
  mrs-engagement-analytics.timer \
  mrs-openai-cost-cache.timer \
  mrs-semantic-veto-shadow-health.timer \
  mrs-prospective-conversations.timer
systemctl --user show TIMER.timer --property=LastTriggerUSec,NextElapseUSecRealtime
systemctl --user show SERVICE.service \
  --property=Result,ExecMainCode,ExecMainStatus,ExecMainStartTimestamp,ExecMainExitTimestamp
```

Disable or re-enable only the support monitor:

```bash
systemctl --user disable --now mrs-support-health-monitor.timer
systemctl --user enable --now mrs-support-health-monitor.timer
```

To add or remove a support job, edit the private JSON declaratively, keep the
ID and unit pair unique, set `expected` to the operator's real intent, run the
oneshot, and inspect the raw JSON. Removing an expectation is preferably an
explicit `expected: false` transition first so the observed `ignored` state is
visible. None of these monitor operations controls the monitored job.
