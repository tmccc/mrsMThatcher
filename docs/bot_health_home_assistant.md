# MrsMThatcher same-host health monitoring

This read-only facility distinguishes a quiet healthy bot from a stopped,
restarting, stalled, repeatedly failing, or remote-write-blocked bot. It does
not infer health from mentions, replies, posts, or work being due, and it never
restarts, stops, pauses, or otherwise controls the bot.

The foreground Python process records genuine execution progress in
`$XDG_RUNTIME_DIR/mrsMThatcher/bot-health.json`. The file is transient,
private (`0600`), and atomically replaced. `MRS_BOT_HEALTH_FILE` is available
for isolated tests; test mode refuses paths outside its explicit temporary
roots. There is no heartbeat thread, socket, API, token, durable bot-state
field, or provider call.

`mrs-bot-health-monitor.service` evaluates that progress file, the explicit
`systemctl --user show mrsMThatcher.service` properties, and the recorded
Python child under `/proc`. `mrs-bot-health-monitor.timer` invokes the one-shot
evaluator about once per minute. Its finite child-unavailable grace, recent
child-instance history, and observed `NRestarts` increases are transient under
`$XDG_RUNTIME_DIR/mrsMThatcher/` and disappear at reboot.

The host-specific Home Assistant destination is kept only in the private file
`~/.config/mrsMThatcher/bot-health-monitor.env` (mode `0600`):

```text
MRS_BOT_HEALTH_MONITOR_OUTPUT=/path/to/home-assistant/config/.runtime/mrs_m_thatcher_bot_health.json
```

The corresponding in-container path is always
`/config/.runtime/mrs_m_thatcher_bot_health.json`. The canonical native Home
Assistant YAML is `deploy/home-assistant/mrs_m_thatcher_health.yaml`. It uses a
built-in command-line sensor, template binary sensors, and ordinary YAML
automations; it is not a custom integration.

Statuses are:

- `starting`: the active/activating service has been continuously without a
  valid matching child for no more than three minutes. A fresh snapshot or a
  replacement `instance_id` does not renew this grace.
- `healthy`: the service and expected child are live and genuine progress is
  fresh.
- `paused`: the live child reports the intentional global pause, or an
  intentionally paused service was stopped less than ten minutes after its
  last fresh progress update.
- `degraded`: progress continues, but at least three errors occurred in the
  rolling 30-minute window, three distinct child instances or three observed
  automatic wrapper restarts occurred in 15 minutes, or the remote-write
  safety barrier is active.
- `stalled`: the service and expected child remain live but genuine progress
  is more than ten minutes old.
- `failed`: the service is inactive outside paused deployment grace, the valid
  snapshot/expected child did not appear before finite child grace expired, or
  rapid child/wrapper restarts currently leave no matching child running.

The genuine-progress stale threshold defaults to 600 seconds and may be
overridden locally with `MRS_BOT_HEALTH_STALE_SECONDS`. Home Assistant
independently treats the monitor as critical when `checked_epoch` is absent,
invalid, or more than 180 seconds old. A single transient bot error is healthy.
Recovery notifications wait for the primary status to become `healthy`, then
require it to remain healthy with the problem sensor off for two minutes.

Run and inspect the evaluator with:

```bash
systemctl --user start mrs-bot-health-monitor.service
systemctl --user status mrs-bot-health-monitor.service
cat "$XDG_RUNTIME_DIR/mrsMThatcher/bot-health.json"
```

Read the published Home Assistant file using the host path configured in the
private environment file. Recent evaluator logs and timer state are available
with:

```bash
journalctl --user -u mrs-bot-health-monitor.service -n 50 --no-pager
systemctl --user status mrs-bot-health-monitor.timer
```

Disable or re-enable only this timer with:

```bash
systemctl --user disable --now mrs-bot-health-monitor.timer
systemctl --user enable --now mrs-bot-health-monitor.timer
```
