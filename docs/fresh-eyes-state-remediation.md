# Durable state and local authority remediation

Items 1–3 were confirmed against starting HEAD
`47352f37faa0d8cf967c9179e9b9369ac92bc0ec`, which was also the reviewed commit.
None had already been fixed. Work and tests use the isolated remediation
worktree; the service checkout and production state were not modified.

## Root causes and publication protocol

The old primary-then-`.bak1` sequence gave two different valid JSON documents no
ordering authority. Emergency confirmation could accept visible primary content,
remove the receipt, and leave the next startup unable to choose a generation.
The writer also accepted non-finite JSON, oversized documents and schemas which
the reader rejected. Its validation occurred too late to protect every backup.

`mrs_bot_state_generation.py` now owns an embedded `_state_generation` envelope:
a bounded monotonic integer sequence and SHA-256 digest covering the complete
canonical document, including its sequence. State reader and minimum-reader
versions are **5**; the previous version-4 fence remains recognised for migration.

The writer serialises a detached document with `allow_nan=False`, runs the
complete persisted-state validator, chooses a successor under the instance/state
lock, builds the envelope, enforces the exact reader byte limit, and validates
the final bytes before creating a temporary file or rotating a backup. These
same canonical UTF-8 bytes are hashed and written. Files are mode 0600, flushed
and fsynced before replacement; the containing directory is fsynced after
replacement, even for ordinary periodic state saves.

A backup is a replica, not a commit record. Failure to publish `.bak1` raises
`StateBackupWriteError` carrying the exact already-durable commit proof. The
primary remains restart-loadable. Startup validates all complete generations,
rejects equal-sequence conflicts, chooses the highest sequence, and repairs the
canonical/latest replica through the same writer. A backup error during repair
is tolerated only if its exact commit proof remains valid. Unknown future reader
authority fails closed on both loading and saving.

## Migration and bounds

Existing unsealed state migrates under the existing singleton/state lock.
Matching primary/latest legacy authority permits migration while preserving older
historical backups. Where that authority is absent, differing valid legacy
candidates cannot be ordered from backup filenames: a prior failed `.bak1`
publication can leave `.bak2` newer. Those cases stop without overwriting any
candidate. A single usable legacy authority or equivalent candidates can migrate.

The complete state and generic durable-history writer enforce the reader's
64 MiB encoded-byte ceiling. Identity sets, posted-meme identities and the new
`_confirmed_receipt_commits` map are deliberately not truncated: growth past the
ceiling fails before replacement. The latter binds each retired source receipt's
exact canonical-byte digest to the committed confirmed effect and regular-post
quote/image identities. Previously interrupted retirement markers without this
binding remain fail-closed; missing authority is not fabricated during migration.

## Exact retirement authority

Ordinary `open()`/JSON equality was insufficient proof and followed symlinks.
State, backup and history readers now reject group/world-writable files and
verify owned, non-writable containing directories through the existing secure
path-walking primitive. Receipts retain the stricter exact-0600 requirement.
Transport journals and media receipts receive the same directory protection.
The sending-attempt and conversational-retirement readers now reuse the secure
receipt loader rather than ordinary `open()`.

`StateCommitProof` records directory identity, file device/inode/owner/mode/link
count/size/timestamps, exact-byte digest, generation and bound receipt digests.
Regular posts attach independently fsynced used-history proofs. Both root
retirement adapters and the low-level mutation authority recheck those proofs;
byte-identical inode substitutions, symlinks, hard links, permission changes and
content changes cannot authorise cleanup.

Normal completion, reconciliation, and emergency confirmation carry the actual
proof through journal and receipt retirement. The regular emergency owner
returns a typed `RegularPostPersistenceResult` with component failures and the
composite proof. A failed component or missing proof retains suppression
evidence. Fresh-process retirement recovery checks the source marker's exact
receipt digest, validates the complete used-history representation using its
owning reader contract, then creates and verifies fresh durable authority before
continuing local cleanup. Historical-context retirement retains its separate
outbox authority.

The successful-media-upload `_consumed_authorities` leak is closed by deleting
only the exact consumed entry after stable confirmation. An older operation
cannot remove a newer entry issued under the same key.

## Regression and independent review evidence

The initial generation/validation/security regression group reproduced eleven
failures before the implementation. Coverage now includes:

- Failure before primary replacement, after replacement, before/after directory
  fsync, during backup creation and after reported backup failure; each reloads
  from a fresh interpreter.
- Non-finite numbers, invalid schemas, exact byte-limit acceptance and one-byte
  overflow; rejected writes leave all existing generations unchanged.
- Current-reader fencing, legacy ambiguity, equal-sequence conflict, newest
  generation repair and prevention of rollback after a rejected save.
- Symlinks, hard links, writable files/directories, equal-content inode changes,
  substitution between validation and destructive retirement, and exact receipt
  identity mismatch.
- Real process interruption/resumption across source-retirement boundaries for
  all posting lanes, and retained barriers after uncertain final directory fsync.
- Exact media-capability retirement and protection of newer capabilities.

Independent review added attacks in
`tests/test_independent_state_authority_regressions.py` and
`tests/test_independent_emergency_state_security.py`. It found and verified fixes
for proof capture accepting a newly substituted inode, Python numeric equality
hiding legacy differences, malformed history membership during restart cleanup,
and ambiguous legacy backup filename ordering.

Focused commands and the final full-suite results are recorded in
`docs/fresh-eyes-remediation-20260919.md`. The principal implementation files are
`mrs_bot_state_generation.py`, `mrs_bot_state_persistence.py`,
`mrs_bot_state_loading.py`, `mrs_bot_state_candidate_validation.py`,
`mrs_bot_durable_json_io.py`, `exact_receipt_retirement.py`,
`remote_write_transport_journal.py`, `remote_media_upload_receipt.py`, the main
post/reply persistence and reconciliation owners, and their explicit root
adapters in `mrsMThatcher2.py`.

## Remaining decomposition opportunities

Further decomposition is intentionally separate from this safety work. Useful
future ownership moves include a typed transaction-retirement context replacing
large callback bundles, a state lifecycle owner containing root recovery adapters,
and shared typed reply-media/provider transport contexts. Each requires dedicated
adapter and process-restart coverage. This task introduced only the generation,
proof, emergency-result and security owners needed for the fixes; it did not
attempt a wholesale rewrite of the root module.
