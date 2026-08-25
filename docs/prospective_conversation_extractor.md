# Prospective conversation extractor

## Purpose and boundary

`tools/extract_prospective_conversations.py` is a deterministic, read-only
collector for private research. It reconstructs actual user/account reply
chains whose observed activity reaches the prospective period beginning at the
frozen boundary:

```text
2026-08-24T15:08:39Z
```

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
/disks/disk1/research/mrsMThatcher-prospective-conversations
```

Its layout is:

```text
mrsMThatcher-prospective-conversations/
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

An observed conversation is `eligible` only when its earliest reliably known
turn is at or after the frozen boundary. Any reliably reconstructed earlier
turn makes it `pre_boundary`; a post-boundary continuation does not move that
start forward. If retained evidence cannot establish the start, the status is
`start_unknown`. The latter two statuses are excluded from default review
packs. Warnings state when retained source coverage does not span the boundary.

Canonical log timestamps are interpreted as Europe/London production time and
converted to UTC. Human-readable output uses a trailing `Z`.

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

Prospective-eligible conversations receive high-recall descriptive signals
from these reason codes:

- `same_chain_user_continuation`
- `multiple_substantive_user_turns`
- `multiple_account_replies`
- `third_or_later_substantive_turn`
- `explicit_correction_cue`
- `post_clarification_continuation`
- `sibling_branch_activity`
- `partial_reconstruction`
- `ambiguous_parentage`

Correction phrases are surface cues only. A contributor's allegation of a
misunderstanding is not treated as proof that the account misunderstood them.
The collector does not assign proposition-substitution or repair labels.

## Command-line use

Run a scan manually with the production settings:

```bash
python3 tools/extract_prospective_conversations.py scan \
  --project-dir /disks/disk1/etc/mrsMThatcher \
  --output-root /disks/disk1/research/mrsMThatcher-prospective-conversations \
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
  --output-root /disks/disk1/research/mrsMThatcher-prospective-conversations
```

The command is read-only and returns valid JSON even before initialisation.
For direct read-only inspection, use the same command rather than opening the
state in an editor.

Validate state, permissions, the `current` symlink, every batch and review-pack
manifest, file hashes, JSON/JSONL syntax, ordering, boundary consistency, and
the absence of raw-author fields:

```bash
python3 tools/extract_prospective_conversations.py validate \
  --output-root /disks/disk1/research/mrsMThatcher-prospective-conversations
```

Validation exits non-zero on corruption and never repairs it implicitly.

Freeze a private review pack manually:

```bash
python3 tools/extract_prospective_conversations.py freeze-review-pack \
  --output-root /disks/disk1/research/mrsMThatcher-prospective-conversations \
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

## Atomicity, locking, and recovery

Scans take a non-blocking exclusive lock at `state/extractor.lock`. A second
scan reports lock contention and exits successfully without waiting or changing
state. Validation takes a shared lock; freezing a pack takes an exclusive lock.

A changed snapshot is first written to a `batches/.tmp-*` directory. Every file
is flushed, hashed, and validated before an atomic rename. The relative
`current` symlink is replaced atomically and extractor state is replaced last.
If state publication fails, the previous `current` link is restored and the new
unpublished batch is removed. An unchanged canonical snapshot creates no
duplicate batch; only private scan bookkeeping changes.

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

Installation verifies and copies the units, prepares the private output
directories, and reloads the user manager. It does not enable, disable, start,
stop, or restart any unit. Activate this collector explicitly after review:

```bash
systemctl --user enable --now mrs-prospective-conversations.timer
systemctl --user start mrs-prospective-conversations.service
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
