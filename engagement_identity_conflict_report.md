# Engagement analytics identity-conflict repair

Generated: 2026-07-18T08:03:55Z

## Outcome

The analytics failure was repaired without weakening `IdentityConflict`. The
collector now treats explicit durable quote IDs as authoritative over historical
source-file line positions, while disagreements between explicit identities
remain fatal. The affected X post exists once in the ledger, all of its engagement
history remains present, the timer-triggered verification run succeeded, and the
main bot remained active throughout.

Classification: `incorrect_derived_analytics_record`

## Exact cause

Post `2077121396186992800` was created from zero-based line 599 of the former
632-record corpus. At posting time that line contained:

> Capitalism is the moral way of running an economy.

Its canonical ID is:

`e28d24c49780a4d8a0c248097ee4962f941fdf1b2ec687a1bf52cddabc95995b`

The attribution cleanup removed 13 earlier logical records while preserving the
retained records byte-for-byte. In the current 619-record file, zero-based line
599 now contains:

> It is free enterprise, which creates wealth, not meddling governments.

Its valid but unrelated canonical ID is:

`a8de2cdcaa20182b2129e0292e4c98956c770132336c12c7130963d3796876e6`

The texts differ substantively, not by punctuation, whitespace, Unicode,
truncation, or formatting. There is no hash collision and no quote-text alias.
No durable source links `a8de2c...` to this X post; it was produced only by
re-reading the stale historical line number against the cleaned current file.

The same shift affected other recent historical `main_post_posted` events. Posts
with context history or cached text already had explicit identities. A few older
pre-context posts no longer had those retained sources, so their correct existing
post-pair-ledger identities also had to take precedence over current line lookup.

## Evidence and authority

| Source | Location / timestamp | Stored evidence | Recomputed result | Authority |
|---|---|---|---|---|
| Production selection log | `mrsMThatcher.log.5:25991-26005`, 2026-07-14 21:01:45 BST | line 599, exact text, `quote_hash=e28d24...` | exact text hashes to `e28d24...` | Posting-time primary evidence |
| X success response | `mrsMThatcher.log.5:26052`, 2026-07-14 21:01:46 BST | post ID and exact public text | quote portion hashes to `e28d24...` | Posting transport confirmation |
| Context event | `mrsMThatcher.log.5:26225`, 2026-07-14 21:01:47 BST | parent post and explicit `quote_id=e28d24...` | unchanged | Structured explicit identity |
| Main-post event | `mrsMThatcher.log.5:26227`, 2026-07-14 21:01:47 BST | post ID and `line_no=599`; no quote ID/text | current line incorrectly hashes to `a8de2c...` | Position metadata only |
| Durable context history | `historical_context_reply_history.json:3-15`, confirmed 2026-07-14T20:01:47Z | parent, reply and explicit `quote_id=e28d24...` | unchanged | Highest-precedence durable production record |
| Pre-cleanup source snapshot | `semantic_alignment_research/quote_attribution_cleanup_001/mrsMThatcher_before.txt:600` | `Capitalism is the moral way...` | `e28d24...` | Proves historical line contents |
| Current source | `mrsMThatcher.txt:600` | `It is free enterprise...` | `a8de2c...` | Proves the line shifted |
| Canonical research | `quote_analysis.json` and completed research packets | both IDs are separate exact quotations from separate source events | both hashes valid | Proves distinct identities |
| Analytics ledger before repair | pair 4 | post ID, `e28d24...`, canonical text, context post | unchanged | Existing correct durable analytics identity |

The canonical research identifies `e28d24...` as an exact, high-confidence quote
from the Fraser Institute speech. It identifies `a8de2c...` as a different exact,
high-confidence quote from the Bermuda luncheon speech.

The reconciled regular-post receipt was removed normally at
`mrsMThatcher.log.5:26226`; no historical receipt or production log was rewritten.

## Repair

- Added `engagement_analytics/quote_identity_corrections.json`, schema version 1.
  Its single audit record is scoped to the exact post ID, source type, historical
  line number, observed/canonical IDs, observed/canonical texts, classification,
  completed packet, reason, and evidence.
- Added strict correction validation. Any malformed scope, text/hash mismatch,
  duplicate, unsupported classification, unresolved/excluded canonical quote, or
  missing evidence fails closed.
- Added the existing SQLite post-pair ledger as a read-only structured discovery
  source for recent identities still present in the completed canonical corpus.
- Historical line numbers now supply identity only when no explicit identity is
  available. A shifted line cannot override conflict-checked history, receipts,
  exact cached text, context events, or the durable pair ledger.
- Explicit source conflicts still call `_merge_identity()` and raise
  `IdentityConflict`. Corrections cannot cross post IDs.
- Each use of the exact audited correction logs the post ID and both IDs once.
  Other shifted lines produce one concise run summary and retain a discovery-source
  marker on each pair.
- No current quotation ID, historical production log, receipt, X post, or main-bot
  state was changed.

Correction manifest SHA-256:

`5d8ae2c819278ab1b843494debca2622a143981cfc1cbe036713d60e27c5b00d`

## Data backup

Before the successful collector run, analytics data was copied to:

`engagement_analytics/backups/identity_conflict_20260718T073134Z/`

- Exact database byte copy: `761a158536815b3df500a524e860f32693966e9550ba025a11423ed8d78b8d8e`
- Transactional SQLite backup: `fe427e2efaedc14ce62ace682e653a41f4d113fe233c2f0ebc37cfafdac81ee9`
- Collector state: `7df2cbf5956bc4dce39d4b654ab2fe707f8a621fc59d41ff4d52b508581f8284`
- Collector log: `60ff28b0ab15d20be6247c230dc2b6f1f2867042e9a193d558d295ba2ca9a4c7`

Both live and transactional backup databases passed `PRAGMA integrity_check`
before collection with 98 pairs and 480 snapshots. The backup directory is now
explicitly Git-ignored.

## Tests

- Focused analytics suite: `38 passed`.
- Full repository suite: `1763 passed, 1 skipped` in 707.35 seconds.
- `python3 -m py_compile mrs_engagement_analytics.py mrsMThatcher2.py`: passed.
- `git diff --check`: passed.

Regression coverage includes the exact post and IDs, exact correction scope,
canonical pair uniqueness, real snapshot preservation, repeated-run idempotence,
cross-post rejection, exclusion of noncanonical attribution records, durable-ledger
recovery after line shifts, ordinary substantive conflict rejection, and protected
production-file immutability.

## Operational verification

Immediate containment stopped only `mrs-engagement-analytics.timer` and reset the
analytics service failure. `mrsMThatcher.service` remained active.

The successful manual collector run at 08:49 BST:

- exited `0/SUCCESS`;
- produced no `IdentityConflict`;
- retained post `2077121396186992800` exactly once under `e28d24...`;
- retained all 12 snapshots for its main/context posts;
- collected 45 due snapshots and added four new post pairs;
- left the database integrity check at `ok` (102 pairs, 525 snapshots).

A second manual run had nothing due and made zero X requests. Starting the timer
then triggered a persistent catch-up run at 09:02 BST. It exited `0/SUCCESS`, made
zero requests, and reported:

```text
inserted=0 updated=0 unchanged=102 total_input=102
collection=nothing_due
```

Final timer state:

- timer: active and enabled;
- next trigger observed: 2026-07-18 09:15:09 BST;
- failed user units: 0;
- analytics oneshot last result: `0/SUCCESS`;
- main bot service: active throughout; it was not stopped, restarted, or signalled.

The analytics user unit is sandboxed with `ProtectSystem=strict` and only
`engagement_analytics/` in `ReadWritePaths`, so the collector cannot write main-bot
state, histories, receipts, logs, configuration, or source files.

## Files changed

- `.gitignore`
- `engagement_analytics/README.md`
- `engagement_analytics/quote_identity_corrections.json`
- `mrs_engagement_analytics.py`
- `tests/test_engagement_analytics.py`
- `engagement_identity_conflict_report.md`

An unrelated pre-existing untracked file, `systemd_user_service_report.md`, was
left unchanged.

## Git summary

`git diff --stat` before adding this untracked report:

```text
 .gitignore                         |   1 +
 engagement_analytics/README.md     |  15 ++-
 mrs_engagement_analytics.py        | 167 ++++++++++++++++++++++++++++++-
 tests/test_engagement_analytics.py | 197 +++++++++++++++++++++++++++++++++++++
 4 files changed, 375 insertions(+), 5 deletions(-)
```

No files were staged, committed, pushed, or deployed.
