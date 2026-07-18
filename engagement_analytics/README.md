# Engagement analytics collector

`mrs_engagement_analytics.py` is a standalone, read-only X metrics collector for
main quotation posts and their historical-context replies. It never imports the
production bot and exposes no X write operation.

## Data sources

Pair discovery prefers the durable historical-context reply history, then any
confirmed context or regular-post receipt, the existing durable post-pair ledger,
the structured bot tweet cache, and finally validated structured `EVENT` records.
Historical `line_no` values are position references, not durable identities: an
already conflict-checked explicit quote ID takes precedence when later corpus
edits shift retained lines. X snowflake timestamps are checked against log
timestamps so copied test records are rejected. Contradictory explicit quote,
main-post, or context-post identities abort discovery.

Evidence-backed corrections for a specific historical line-derived mismatch live
in `quote_identity_corrections.json`. Each record is fail-closed against the exact
post ID, line number, observed and canonical hashes and texts, completed research
packet, classification, and evidence. This is not a general alias mechanism;
unregistered conflicts between explicit identities remain fatal.

## Snapshot policy

Every tracked post receives targets at 1, 6, 24, 72, and 168 hours. A due target
remains pending until a confirmed observation or a terminal deleted/forbidden
result exists. A late collection records both target and actual age; it is never
presented as an exact target-age measurement. One batched response may satisfy
multiple overdue targets for the same post, with identical collection time and
explicit actual age. This avoids losing observations after outages without
spending repeated requests on already-overdue targets.

## Safety

- Live reads require `--execute-read`.
- The client has one hard-coded `GET /2/tweets` operation.
- OAuth1 user-context metrics are requested in batches of at most 100 IDs.
- Missing metrics remain `null`; they are never changed to zero.
- One transient retry is allowed. A 429 persists a collector-only cooldown and
  stops the invocation without altering the bot's cooldown or state.
- SQLite transactions, foreign keys, WAL mode, unique schedules, append-only
  snapshot/attempt triggers, atomic files, and a nonblocking process lock protect
  recovery and audit history.
- Metric adapter corrections append linked snapshot revisions and a reasoned
  audit record; the original observation is never overwritten.
- Runtime database, lock, logs, raw responses, exports, reports, and state are
  Git-ignored.

## Commands

```bash
python3 mrs_engagement_analytics.py initialise --project-dir "$PWD"
python3 mrs_engagement_analytics.py discover --project-dir "$PWD" --since-days 14 --dry-run
python3 mrs_engagement_analytics.py discover --project-dir "$PWD" --since-days 14
python3 mrs_engagement_analytics.py status --project-dir "$PWD"
python3 mrs_engagement_analytics.py collect --project-dir "$PWD" --dry-run --max-api-requests 2

# Source the existing private environment before an explicitly authorised read.
set -a
source ./mrsMThatcher.env
set +a
python3 mrs_engagement_analytics.py collect \
  --project-dir "$PWD" --execute-read --max-api-requests 2 --resume

python3 mrs_engagement_analytics.py report --project-dir "$PWD"
python3 mrs_engagement_analytics.py export --project-dir "$PWD" --format json,csv,markdown
```

Bounded historical collection additionally requires a date bound or maximum pair
count, a request limit, `--execute-read`, and `--confirm-read-only`. There is no
unbounded backfill mode.

The service and timer templates in `deploy/` are user units. Install them under
`~/.config/systemd/user/`, then use `systemctl --user daemon-reload` and
`systemctl --user enable --now mrs-engagement-analytics.timer`. User lingering
must be enabled so the timer continues without an interactive login.
