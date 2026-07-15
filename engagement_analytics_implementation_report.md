# Engagement Analytics Implementation Report

Generated: 2026-07-15

## Architecture

`mrs_engagement_analytics.py` is a standalone process. It does not import the
production bot and its X client exposes one operation: OAuth1 user-context
`GET /2/tweets`. Runtime state is isolated under `engagement_analytics/`; no
posting receipt, production history, bot state, or posting cooldown is opened
for writing.

SQLite is used for the durable ledger. WAL mode, foreign keys, explicit
transactions, unique schedules, append-only snapshot and attempt triggers,
atomic sidecar writes, and a nonblocking process lock provide crash recovery
and overlap prevention.

## Discovery Sources

Post-pair discovery uses local structured evidence in this order:

1. historical-context reply history;
2. confirmed historical-context receipt;
3. regular-post receipt;
4. structured quote entries in the bot state tweet cache;
5. structured production `EVENT` records.

Canonical quote packets provide immutable quote identity and formatter
metadata. X snowflake timestamps are checked against log timestamps to reject
copied test events. Contradictory quote IDs or post relationships abort
discovery. A context reply discovered after an earlier missing state clears the
live missing reason while retaining the old record in the revision history.

## Snapshot Schedule

Each tracked post has fixed targets at 1, 6, 24, 72, and 168 hours. Both target
and actual collection age are retained. An overdue target remains pending and
is captured once on a later successful run. Multiple overdue targets may share
one current observation; their actual age makes that lateness explicit.

The default on-time tolerance is 20 minutes and is configurable. It changes
only the overdue status label, not whether a due target is eligible.

## Database Schema

- `post_pairs`: canonical main/context relationships and editorial metadata.
- `post_pair_revisions`: audited discovery changes.
- `posts`: post role and immutable pair linkage.
- `snapshot_schedule`: one row per post and target age.
- `metric_snapshots`: append-only observations and derived rates.
- `metric_snapshot_revision_audit`: reason and superseded row for corrections.
- `collection_attempts`: append-only request, retry, and response audit.
- `account_or_capability_state`: collector-only cooldown and capability state.
- `schema_migrations`: local schema version history.

Sanitised raw responses are stored separately with SHA-256 hashes. Credentials
and authorisation headers are not retained.

## Batching, Retry, and Cooldown

Main and context IDs are batched together, up to 100 IDs. The collector exits
without constructing an X client when no target is due. Live reads require
`--execute-read`, and each invocation has an explicit request ceiling.

One retry is allowed for a connection error, timeout, or 5xx. Provider
`Retry-After` metadata is honoured. A 429 is not probed repeatedly: the
collector records a private cooldown, stops cleanly, and leaves targets pending.
It does not read or alter the production bot's cooldown.

## Dry Run and Bounded Smoke Test

The offline preflight selected 10 recent pairs, comprising 17 post IDs and one
planned request batch. Three main posts predated the historical-context feature;
seven had completed context replies. The dry run made zero requests.

One bounded read-only smoke test was then run with the existing OAuth client:

- pairs: 10;
- requested post IDs: 17;
- API requests: 1;
- returned posts: 17;
- due snapshots completed: 28;
- terminal snapshots: 0;
- cooldown activated: no.

No backfill beyond those ten pairs was performed.

## Metric Availability

The smoke response supplied:

- impressions;
- likes;
- replies;
- reposts;
- quote posts;
- bookmarks;
- user-profile clicks;
- provider-reported total engagements.

It did not supply URL-link clicks, so that field is stored as null with an
availability reason. The smoke response exposed X's current
`non_public_metrics.engagements` spelling for provider-total engagement. A
regression test was added and the adapter corrected. The 28 original snapshots
were preserved; corrected revision-2 snapshots and an append-only revision audit
were derived from the hash-validated raw response. A second offline pass made no
changes, confirming idempotence.

## Initial Sample Output

This first sample is prospective but small: 10 main posts and seven context
replies. At the latest available snapshot the report showed:

- main-post median engagement rate: 2.62%;
- context median engagement rate: 0.87%;
- median context view ratio: 53.06%;
- median context bookmark rate: 0.00%;
- source-link click rate: metric unavailable.

These are observational associations only. The generated reports prominently
mark insufficient groups and do not attribute effects to context replies.

## Digest Integration

`mrs_log_digest.py` opens the analytics database in SQLite read-only/query-only
mode and never invokes the collector or X client. Its optional **Historical
context engagement** section includes tracked pairs, due-snapshot coverage,
headline medians, unavailable metrics, and sample-size warnings. Missing or
malformed databases degrade to an unavailable message without breaking older
digests.

## Reports and Exports

Reports are produced for trailing 7, 28, and 90 days and all observed data.
They include target-age coverage, medians and interquartile ranges, context-pair
ratios, missing fields, and groups by formatter, verification, source,
confidence, topic, image source, and image-score band. JSON, CSV, and Markdown
exports are atomic local files.

## Scheduling

User-level systemd service and timer templates are present under `deploy/`.
They run every 15 minutes, acquire the collector lock, discover structured
pairs, collect only due snapshots, and write the collector's own log. The unit
is intended for the lingering `tonym` user manager and does not interact with
the production bot service. The reviewed units were installed under
`~/.config/systemd/user/` and the timer was enabled on 2026-07-15. Its first run
performed one bounded read for 65 due post IDs discovered in the configured
14-day window; later runs make no request when nothing is due.

## Verification

Mocked coverage includes discovery precedence and conflicts, missing context
handling, idempotence, fixed and late scheduling, batching, transient retries,
rate-limit cooldown, outage recovery, terminal posts, null metrics, derived
denominators, append-only data, locking, sanitisation, report warnings, digest
compatibility, bounded backfill, explicit read flags, and production-file
isolation.

Final results:

- relevant collector, X-client, digest, context-reply, receipt, reply-strategy,
  integration, and production-isolation suites: **724 passed, 1 skipped**;
- required `py_compile`: passed;
- Ruff fatal/undefined-name checks: passed;
- `git diff --check`: passed;
- SQLite `PRAGMA integrity_check`: `ok`;
- SQLite foreign-key check: no violations;
- live ledger: 10 pairs, one read attempt, 28 original snapshots, 28 audited
  metric-adapter revisions.

## Files Changed

- `.gitignore`
- `README.md`
- `mrs_engagement_analytics.py`
- `mrs_log_digest.py`
- `engagement_analytics/README.md`
- `engagement_analytics/config.example.json`
- `engagement_analytics/schema.sql`
- `deploy/mrs-engagement-analytics.service.example`
- `deploy/mrs-engagement-analytics.timer.example`
- `tests/test_engagement_analytics.py`
- `engagement_analytics_implementation_report.md`

Runtime SQLite, lock, log, raw-response, report, export, and state files are
Git-ignored. Unrelated pre-existing untracked research files were not changed.

## Safety Confirmation

The smoke and scheduled collections used only `GET /2/tweets`. No X write
request was made. No post, reply, like, repost, delete, or remote mutation
occurred. The production bot was not stopped, restarted, signalled, or
reconfigured. The independent user-level analytics timer is enabled; no
production-bot unit was changed. Nothing was staged, committed, pushed, or
deployed to the production bot.
