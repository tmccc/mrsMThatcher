# Prospective conversation extractor

## Purpose and boundary

`tools/extract_prospective_conversations.py` is a deterministic, read-only
collector for private research. It reconstructs actual user/account reply
chains whose observed activity reaches the prospective period beginning at the
frozen boundary:

```text
2026-08-24T15:08:39Z
```

The activation format is deliberately versioned as schema `3`, extractor
`prospective-conversation-extractor-v3`, and parser
`prospective-conversation-log-parser-v3`. State, cache entries, manifests,
status files, and canonical posts must match those versions exactly.

Collection is descriptive. A newly collected conversation is not thereby a
defective conversation, and a review-candidate signal is not a finding that a
reply was bad or that it should be repaired. The extractor never decides
`repair`, `pass`, `publish`, or `suppress`.

The collector is separate from production reply generation, posting, runtime
state, control files, and service operation. It does not import the bot or the
digest. It makes no model, API, provider, or X call, does not fetch missing
posts, and remains useful without network access or API credentials.

## Exact inputs and isolation

The only production inputs are regular, non-symlink files directly beneath
`/disks/disk1/etc/mrsMThatcher` with one of these exact names:

```text
mrsMThatcher.log
mrsMThatcher.log.1
...
mrsMThatcher.log.100
```

They are processed oldest rotation first (`.100` through `.1`, then the active
log). Missing rotation numbers are allowed. Test, smoke, temporary, copied, and
unrelated logs are not selected.

Each source is opened read-only and without following symlinks. The collector
records the initial size, reads no more than that size, parses only complete
newline-terminated records, and defers an incomplete tail. It does not lock,
rotate, rename, truncate, or modify a source. A rotation or replacement race
causes one inventory retry; an open inode can still be read safely after its
directory entry is renamed.

The extractor never reads or changes:

```text
/disks/disk1/etc/mrsMThatcher/.mrs_log_digest_state.json
/disks/disk1/etc/mrsMThatcher/mrsMThatcher.env
/disks/disk1/etc/mrsMThatcher/bot_state.json
/disks/disk1/etc/mrsMThatcher/mrsMThatcher.control.json
/disks/disk1/etc/mrsMThatcher/mrsMThatcher.local.json
```

## Private output and snapshots

The scheduled output root is fixed at:

```text
/disks/disk1/research/mrsMThatcher-prospective-conversations-v3
```

Its layout is:

```text
mrsMThatcher-prospective-conversations-v3/
├── state/
│   ├── extractor-state.json
│   ├── pseudonym-key
│   └── extractor.lock
├── batches/
│   └── <UTC timestamp>-<snapshot hash prefix>/
│       ├── manifest.json
│       ├── source-manifest.json
│       ├── canonical-posts.jsonl
│       ├── conversations.jsonl
│       ├── review-candidates.jsonl
│       ├── status.json
│       └── extraction-report.md
├── current -> batches/<latest complete batch>
└── review-packs/
    └── <manually frozen pack>/
```

The root and mutable directories are mode `0700`. The collector runs with umask
`0077`. The stable pseudonym key is 32 random bytes, created once at mode
`0600`. Contributor keys are HMAC-SHA256 values made with that key; they are not
plain hashes of contributor IDs. The key is never copied into state, a batch,
a report, a manifest, or a review pack. Raw contributor IDs are not written to
any generated output. Post, target, parent, root, thread, and conversation IDs
may be retained because they are needed to reconstruct reply identity.

Completed batch files are mode `0400` and their directory is mode `0500`.
Review packs receive the same immutable permissions. Conversation text remains
private beneath the mode-`0700` root and is never written to a separate
application log.

The state file contains only operational resume data: the frozen boundary,
scan times, latest batch and snapshot hash, counts, warnings, latest source
timestamp, and a content-hash source cache. It contains no conversation text,
raw contributor ID, credential, or pseudonym key. Content hashes allow a
renamed rotation with unchanged bytes to be skipped. A changed active prefix is
parsed again and canonical stable IDs remove duplicate observations.

The source cache is bounded to content hashes in the current retained log
inventory. Each entry records its parser version; an entry from another parser
is reparsed and is never mixed into the current canonical snapshot.

## Reconstruction and prospective status

Conversation identity is established in this order:

1. explicit X conversation ID;
2. explicit root post ID;
3. stable parent/replied-to chains;
4. retained target or thread identity;
5. visible parent-thread telemetry.

Author identity is never a join key. Posts with different roots, incompatible
conversation IDs, separate parent chains, or separate target chains remain
separate even when the contributor pseudonym is the same. Sibling reply
branches can remain one conversation when their root, conversation, or parent
chain establishes that relationship. Missing or conflicting identity produces
partial, low-confidence reconstruction and an explicit warning rather than an
invented join.

Every post separates its X creation time from its retained-log observation
times. `created_at` comes first from an explicitly registered, timezone-aware
structured-event field and otherwise from a valid numeric X Snowflake ID. The
Snowflake calculation uses epoch `1288834974657` milliseconds and accepts only
decimal IDs 15 through 20 digits long. A decoded time must be representable,
must not precede the Snowflake epoch, and may be no more than five minutes later
than the first observation. Invalid or implausible IDs leave creation time
unavailable and produce a bounded warning. Log headers populate only
`first_observed_at` and `last_observed_at`; they never establish prospective
eligibility.

Repeated structured creation times within one second merge their provenance
without changing the canonical instant. Materially different structured times
are retained in `creation_time_conflicts` and never use last-event-wins
semantics. A plausible Snowflake then supplies the canonical time while the
conflict remains inspectable; without one, creation time becomes unavailable.
A conflicted structured time cannot by itself make a conversation prospective,
although an independent, reliable root or conversation identity may still date
the start.

Conversation start time is resolved from an observed root's `created_at`, a
numeric `root_post_id`, a numeric X conversation/root ID, or a created first
turn independently confirmed to have no parent, in that order. A reliably
dated start before the boundary is `pre_boundary`; a reliably dated start at or
after it is `eligible`; an undateable true start is `start_unknown`. A delayed
poll or backlog drain therefore cannot turn an older X post into a prospective
conversation. `pre_boundary` and `start_unknown` are excluded from default
review packs. Warnings state when retained source coverage does not span the
boundary.

`source_lag_seconds` is `max(0, scan cutoff - latest retained source
timestamp)` and is exposed in batch status, extractor state, `status`, and the
extraction report. Lag above six hours adds
`retained_source_stale_relative_to_scan_cutoff`. This is descriptive: it does
not guess why sources are stale and does not invalidate a batch.

Canonical log timestamps are interpreted as Europe/London production time and
converted to UTC. Human-readable output uses a trailing `Z`.

## Publication evidence and structured-event registry

A published account turn requires authoritative publication evidence. Version
3 recognises `account_root_posted` for confirmed `quote_image` and
`daily_meme` roots, and `historical_context_reply_posted` for the exact
confirmed historical-context reply ID and parent. Production emits these
descriptive events only after the existing transport confirmation and required
local persistence have completed. Confirmed recovery and already-completed
paths emit the same stable post identities. Logging neither authorises nor
repeats transport, changes a receipt, or changes a posting outcome.

An account-root event establishes an account-authored published root with no
parent and with its post ID as root and conversation ID. A historical-context
event establishes an account-authored reply with its declared parent, root and
conversation identity. `text` always means the best source-faithful visible
text for private review. `text_source`, `public_text`, and
`visible_media_text` distinguish public tweet text, quote text embedded in an
image, an already-available image summary, historical-context reply text,
mention observation, and unavailable text. An image summary is never labelled
as verbatim tweet text.

Authoritative account publication takes precedence over mention polling in
either log order. A later or earlier `Considering mention` observation of the
same proved post contributes bounded self-observation provenance but cannot
replace the account role, account pseudonym, publication state, or
authoritative text, and does not create a contributor pseudonym. Conversely,
surface text, a `Context —` prefix, Snowflake proximity, and account-looking
prose never promote a user post to the account role.

Version 3 also recovers retained legacy publications only from complete,
unambiguous chains. A main root requires one account-owned lane and attempt,
its attempting transition, one root/no-parent transport transaction, one exact
remote post ID, promotion to the confirmed pending-schedule receipt,
finalisation, and the matching `main_post_posted` marker. Historical context
requires one `historical_context_reply` transaction with exact parent and
text, one exact remote reply ID, a completed publication marker, and the
matching completed confirmed outbox obligation. A missing or conflicting link
leaves the post unresolved. If a complete chain proves identity but lacks
recoverable text, the graph edge is retained as a partial turn with a precise
warning. A generic `Created X post successfully` line is corroboration within
such a bound chain and is never sufficient by itself.

Each account turn records `publication_authority` and inspectable,
hashed-record publication evidence. Existing conversational reply confirmations
remain supported without changing the public reply pipeline.

Conversational transport attempts are keyed by their 64-character transaction
ID and target, retained on the target post across scans and rotations, and
bounded to the newest five attempts. A create line from another lane clears the
transient generic-success association. Confirmed receipt evidence can recover
the final attempted text later. An exact observed remote reply ID can bind an
attempt in any later local status. Without that exact proof, only one uniquely
eligible `started` attempt with no observed remote ID may supply text. An
attempt carrying a different known remote ID, or an unmatched `failed`,
`retired`, or `remote_success_observed` attempt, is deliberately excluded and
is not mutated by confirmation. Zero or multiple eligible attempts leave the
confirmed account turn's text null, preserve the graph edge and publication
evidence, and add a precise binding warning.

Structured events use a small event-kind registry defining permitted target,
text, author, identity, parent, creation-time, and publication fields. A bare
generic `id` is not a target. Unknown events are represented in
`source-manifest.json` by at most 64 rows containing `event_kind`, `count`, and
`target_like_count`, ordered by descending count and then name. Separate
`ignored_structured_event_other_count` and
`ignored_target_like_event_other_count` fields aggregate the remainder. The
collector never emits one warning per ignored event.

## Open and quiescent conversations

The scheduled scan uses a 48-hour quiescence period. A conversation is `open`
when its last activity is less than 48 hours before the scan cut-off and
`quiescent` at 48 hours or more. This is a review status, not permanent closure.
A late turn extends the same stable conversation, creates a changed snapshot,
and can reopen a previously quiescent conversation. Older immutable snapshots
remain unchanged.

## Substantive turns and review candidates

Deterministic rules exclude only narrow routine forms such as whitespace, a
bare handle or URL, one punctuation mark, an isolated emoji, and a routine
one-word greeting or thanks. Questions, criticism, disagreement, corrections,
distress, positive expressive messages, and short meaningful distinctions are
retained as substantive.

The complete root conversation remains in `conversations.jsonl`, but a review
candidate is one principal external author on one maximal exact parent path.
Its stable `branch_key` binds the conversation key, principal pseudonym and
branch-tip post ID. Strict path prefixes for the same author are suppressed;
two genuinely divergent branches remain separate. The root account post starts
every complete eligible path. Historical context is a path turn only when it
is an ancestor; otherwise it appears in bounded, deterministically ordered
`sibling_context_refs` with no effect on path counts.

`same_author_user_turn_count` counts only the principal author's user turns on
that path. `account_turn_count_on_path` and
`substantive_turn_count_on_path` likewise exclude siblings. The deterministic
same-author continuation depth is the number of substantive principal-author
turns after their first substantive contribution for which an account turn
occurred after the preceding principal-author turn and before the current one.

Prospective-eligible paths receive high-recall descriptive signals from these
reason codes:

- `same_author_path_continuation`
- `multiple_account_replies_on_path`
- `third_or_later_substantive_path_turn`
- `explicit_correction_cue`
- `post_clarification_continuation`
- `sibling_branch_context`
- `partial_path_reconstruction`
- `ambiguous_parentage`

Correction phrases are surface cues only. A contributor's allegation of a
misunderstanding is not treated as proof that the account misunderstood them.
The collector does not assign proposition-substitution or repair labels.
Clarification detection also recognises bounded evidential forms such as
"Which unemployment measure and period are you using?", "What source are you
relying on?", and "Which law do you mean?" It does not treat general questions
such as "Which party will win?" as clarification requests.

## Retention and free-space safety

Automatic snapshots are pruned under the exclusive extractor lock. The
collector always retains `current`, every batch referenced by a valid review
pack, all batches from the newest 72 hours, and the newest batch for each UTC
calendar day in the newest 90 days. Older unreferenced batches are deleted only
after all deletion candidates pass exhaustive validation. Protected batches
are inventoried from directory and canonical manifest metadata only; routine
retention does not hash or load their corpus JSONL files. Review-pack protection
likewise uses a strict, canonical provenance manifest without loading the pack's
conversation data. If any deletion candidate is corrupt, none is deleted. A
malformed or unreadable review-pack manifest blocks pruning; review packs
themselves are never pruned or rewritten.

Changed and unchanged scans apply retention exactly once. Automatic batches
have a 10 GiB byte budget. After time-based pruning, additional oldest
unreferenced, non-current batches are removed until retained automatic bytes
plus a projected new snapshot fit. Current and review-referenced batches are
never removed; an unchanged scan preserves them even when they alone prevent
meeting the nominal budget. A changed scan fails before creating a temporary
batch when protected bytes plus the projection exceed the budget.

After retention and before temporary-batch creation, a changed scan requires
the projected bytes plus a 10 GiB post-publication filesystem reserve. Errors
and status expose total, used and free filesystem bytes, projected and required
bytes, retained and protected automatic bytes, the configured budget and
reserve, review-pack bytes, and `last_retention_error`.

Status takes a shared lock and measures storage from the filesystem without
mutating state. The validator also reports actual storage. Stored byte counters
are last-known operational telemetry: a mismatch adds
`stored_storage_telemetry_stale` but does not invalidate an otherwise consistent
state/current/immutable-batch transaction.

## Command-line use

Run a scan manually with the production settings:

```bash
python3 tools/extract_prospective_conversations.py scan \
  --project-dir /disks/disk1/etc/mrsMThatcher \
  --output-root /disks/disk1/research/mrsMThatcher-prospective-conversations-v3 \
  --prospective-start 2026-08-24T15:08:39Z \
  --quiescence-hours 48
```

`--until` accepts a timezone-aware ISO-8601 cut-off for deterministic tests or
manual replay. It defaults to the scan-start time. The first successful scan
records the boundary. Later scans must supply exactly the same normalised UTC
boundary and may not silently replace it.

Inspect operational status without editing it:

```bash
python3 tools/extract_prospective_conversations.py status \
  --output-root /disks/disk1/research/mrsMThatcher-prospective-conversations-v3
```

The command is read-only and returns valid JSON even before initialisation,
including `last_retention_error: null`. For direct read-only inspection, use
the same command rather than opening the state in an editor.

Validate state, permissions, the `current` symlink, every batch and review-pack
manifest, file hashes, JSON/JSONL syntax, ordering, boundary consistency, and
the absence of raw-author fields:

```bash
python3 tools/extract_prospective_conversations.py validate \
  --output-root /disks/disk1/research/mrsMThatcher-prospective-conversations-v3
```

Validation exits non-zero on corruption and never repairs it implicitly.

Freeze a private review pack manually:

```bash
python3 tools/extract_prospective_conversations.py freeze-review-pack \
  --output-root /disks/disk1/research/mrsMThatcher-prospective-conversations-v3 \
  --pack-name prospective-review-2026-09-01 \
  --since 2026-08-24T15:08:39Z \
  --until 2026-09-01T00:00:00Z
```

By default the pack includes prospective-eligible, quiescent, substantive
review candidates whose conversation start is in `[since, until)`. Add
`--include-open` to retain open candidates. An existing pack is never
overwritten. A pack contains `manifest.json`, `conversations.jsonl`,
`review-candidates.jsonl`, and `review-pack.md`; no label, model judgement, or
repair decision is added.

The pack manifest records the actual UTC freeze time separately from
`source_batch_creation_timestamp`, while retaining the source batch ID and
snapshot hash.

## Registered version-2 to version-3 rebuild

An extractor or parser version mismatch fails before prior canonical posts or
cache entries are reused. In particular, normal version-3 `scan` rejects a
version-2 state rather than silently upgrading it. The only registered rebuild
source tuple is schema 2,
`prospective-conversation-extractor-v2`, and
`prospective-conversation-log-parser-v2`. Rebuild into a separate nonexistent
destination:

```bash
python3 tools/extract_prospective_conversations.py rebuild-to-new-root \
  --project-dir /disks/disk1/etc/mrsMThatcher \
  --source-output-root /disks/disk1/research/mrsMThatcher-prospective-conversations \
  --new-output-root /disks/disk1/research/mrsMThatcher-prospective-conversations-v3 \
  --until 2026-09-01T00:00:00Z
```

The command opens the old root read-only under its shared lock, verifies the
registered tuple, reads its frozen boundary and quiescence policy, and copies
the 32-byte pseudonym key without displaying it. The destination must not
exist. Current retained production logs must span the boundary and are parsed
from scratch by parser v3; no v2 canonical post or source cache is reused. The
command validates the complete new root and never changes, switches to, or
deletes the old root. It does not alter the installed service output path.

A later controlled deployment must perform these steps in order:

1. Disable and stop only `mrs-prospective-conversations.timer`.
2. Update the production checkout to the reviewed version-3 commit.
3. Install the updated user units without enabling them.
4. Run the registered rebuild from the exact v2 root into the v3 root.
5. Run `validate` against the v3 root.
6. Run one manual `mrs-prospective-conversations.service` oneshot.
7. Inspect the first v3 corpus and its warnings.
8. Only then enable the hourly prospective extractor timer.

Do not point version-3 code at the version-2 root, reuse v2 batches, or switch
the timer before validation and inspection.

## Atomicity, locking, and recovery

Scans take a non-blocking exclusive lock at `state/extractor.lock`. A second
scan reports lock contention and exits successfully without waiting or changing
state. Validation takes a shared lock; freezing a pack takes an exclusive lock.

A changed snapshot is first written to a `batches/.tmp-*` directory. Every file
is flushed, hashed, and validated before an atomic rename. Before that write,
retention runs once against the old committed `current`, followed by the byte
budget and free-space checks. The relative `current` symlink is then replaced
atomically and extractor state is replaced last. No deletion occurs between
those two commits. If state publication fails, the still-retained previous
`current` link is restored and the new unpublished batch is removed. The former
current may therefore survive one extra hourly cycle. An unchanged canonical
snapshot creates no duplicate batch; its single retention pass and private
scan bookkeeping still run.

On a failure, inspect the journal, then run `status` and `validate`. Do not edit
immutable batches or state to make validation pass. Correct the external cause
(for example permissions, a persistently unstable source inventory, or disk
space) and start the oneshot again. A `.tmp-*` directory left by an abrupt
process or host failure is reported by validation and should be investigated
before any manual removal. Earlier completed batches and the prior `current`
snapshot remain authoritative.

## User-level systemd installation and activation

The tracked user units are:

```text
mrs-prospective-conversations.service
mrs-prospective-conversations.timer
```

The oneshot has no network address family beyond `AF_UNIX`, does not load an
environment file, and has only the private research root in `ReadWritePaths`.
The timer is hourly, persistent, accurate to one minute, and uses up to five
minutes of random delay.

Check and install tracked user units with:

```bash
cd /disks/disk1/etc/mrsMThatcher
deploy/systemd-user/install.sh --check
deploy/systemd-user/install.sh --install
```

Installation verifies and copies the units, prepares unrelated scheduled-task
state, and reloads the user manager. For a first version-3 deployment it
deliberately leaves the v3 root nonexistent so the registered rebuild can
create it. On later upgrades it verifies and prepares an existing real v3
directory. It does not enable, disable, start, stop, or restart any unit.
Activate this collector only after completing the controlled rebuild,
validation, manual oneshot, and corpus inspection described above:

```bash
systemctl --user enable --now mrs-prospective-conversations.timer
```

Inspect it with:

```bash
systemctl --user status mrs-prospective-conversations.service
systemctl --user status mrs-prospective-conversations.timer
systemctl --user list-timers mrs-prospective-conversations.timer
journalctl --user \
  -u mrs-prospective-conversations.service \
  --since today
```

Boot-before-login scheduling may require lingering. Inspect, but do not change,
its status with:

```bash
loginctl show-user "$USER" -p Linger
```

Disable only this research timer with:

```bash
systemctl --user disable --now mrs-prospective-conversations.timer
```

That command does not stop, restart, reconfigure, or otherwise alter
`mrsMThatcher.service`.
