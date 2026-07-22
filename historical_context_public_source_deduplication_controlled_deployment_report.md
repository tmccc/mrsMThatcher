# Historical-context public-source deduplication controlled deployment

## Executive summary

The historical-context public-source deduplication change received its first
controlled production activation on `big-nas-2` on 22 July 2026. The activated
tree is commit `98b01d3b1d421f3236862fb9b962fa6667aed0c2` on `master`, pushed to
`origin/master`. It contains the reviewed deduplication implementation from
`d0c6d73eff67a653fe668e266bdf7f0b99e8279c`, its merge on `master`, and a
deployment-safety correction made before activation.

The service was restarted exactly once. It is active with one wrapper and one
Python child, no crash restart, and the active files match the committed tree.
No post, reply, media upload, provider request, receipt deletion, state reset,
analytics action, or unrelated service restart was performed by the
deployment.

Production remains deliberately paused by the untracked, mode-0600 runtime
control file `mrsMThatcher.control.json`. A durable regular-post receipt records
a successful quote/image post whose historical-context reply has not yet been
reconciled. The receipt was preserved. Resuming production therefore requires
a separate, explicit decision either to allow the intended reply reconciliation
or to abandon it through a documented recovery procedure. This maintenance
pause is a safety condition, not a service-health failure.

## Deployment identity

- Date: 22 July 2026.
- Host: `big-nas-2`.
- Project: `/disks/disk1/etc/mrsMThatcher`.
- Branch and upstream: `master`, `origin/master`.
- Pre-change running commit: `491e81cffdd4f36fbc79b9948aedfa4b26f674e1`.
- Reviewed deduplication commit: `d0c6d73eff67a653fe668e266bdf7f0b99e8279c`.
- Merge commit on `master`: `46122789c0ea3e41ff4a27e4805ac2039eaf4bb5`.
- Activated source commit: `98b01d3b1d421f3236862fb9b962fa6667aed0c2`.
- Activated source parent: `46122789c0ea3e41ff4a27e4805ac2039eaf4bb5`.
- Reviewed source report:
  `historical_context_public_source_deduplication_report.md`.
- Reviewed audit:
  `historical_context_public_source_deduplication_audit.json`.

The running service had not previously loaded the deduplication change. Its
Python process predated the reviewed commits and was still executing commit
`491e81c`. The restart described here was therefore the first controlled
activation of this specific public-source change.

## Why the receipt did not clear by itself

At 01:42 BST, X accepted regular quote/image post `2079728698731745489`, after
which the bot correctly wrote `regular_post_receipt.json`. The old in-memory
historical-context formatter then rejected the newer on-disk schema-5
source-role audit. That failure happened after the external post but before its
context reply and receipt removal. Retaining the receipt was the durable
duplicate-prevention behavior; it was not a temporary sleep marker.

The same long-lived Python child retried the receipt on later due quote cycles
at approximately 04:04, 06:34, and 08:55 BST. Each retry encountered the same
version mismatch and advanced the quote schedule. Between those due cycles,
the bot's 60-second sleeps were its normal main-loop cadence, not a crash or a
receipt lock. Separately, equal-epoch receipt replay could replace a newer
next-post time with the stale time in the receipt. The version mismatch and
scheduled replay behavior explain why the receipt persisted.

The safety commit made before activation:

- accepts only the exact legitimate legacy source-role policy present in the
  production history, while continuing to reject unknown lookalikes;
- preserves a newer scheduled quote time during receipt replay;
- durably writes a future schedule before context replay and receipt removal;
- skips receipt reconciliation and every remote lane while globally paused;
- fails closed if runtime control becomes unreadable after a cached unpaused
  state; and
- repeats the pause check immediately at X, media, and external-provider write
  boundaries.

These changes preserve the receipt and prevent an unintended second quote or
remote action during recovery.

## Activated source changes

The reviewed deduplication commit contained exactly these twenty files:

- `historical_context_formatter.py`
- `historical_context_public_render_review.py`
- `historical_context_public_render_review.txt`
- `historical_context_public_source_audit.py`
- `historical_context_public_source_deduplication_audit.json`
- `historical_context_public_source_deduplication_report.md`
- `historical_context_reply_schema.json`
- `historical_context_source_roles.py`
- `mrs_log_digest.py`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/checksums.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/material_veto_v3_shadow_manifest.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/runtime_eligible_quote_manifest.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/v3_shadow_manifest_audit.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/v3_shadow_manifest_audit.md`
- `tests/test_digest_reply_observability.py`
- `tests/test_historical_context_public_render_review.py`
- `tests/test_historical_context_public_source_deduplication.py`
- `tests/test_historical_context_reply.py`
- `tests/test_historical_context_source_roles.py`
- `tests/test_unit_helpers.py`

The deployment-safety commit staged exactly these ten files:

- `historical_context_formatter.py`
- `mrsMThatcher2.py`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/checksums.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/material_veto_v3_shadow_manifest.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/runtime_eligible_quote_manifest.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/v3_shadow_manifest_audit.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/v3_shadow_manifest_audit.md`
- `tests/test_fail_safe_bootstrap_and_control.py`
- `tests/test_historical_context_reply.py`
- `tests/test_unit_helpers.py`

The inactive semantic-veto metadata was refreshed with the project's existing
offline preparation command. Active enforcement remains false. The semantic
manifest excluding source hashes was unchanged
(`ffa5c1e...` before and after), as was the eligibility manifest excluding
source hashes (`bd98dc...` before and after). The refresh still records 610
quotes and 22,066 pairs.

The pre-existing untracked source-compression pilot files were not staged. The
untracked production receipt and runtime-control file were not staged. No
environment or secret file was staged, modified, or committed. No environment
file contents were intentionally displayed; the rollback bundle records only
the environment file's hash and mode.

## Public behavior and document 107352

The former public output contained two labels for the same document:

```text
Source (wording, attribution, source event, date) — Margaret Thatcher Foundation Archive, Speech to Conservative Party Conference, October 14, 1988

Source (wording, attribution) — Margaret Thatcher Foundation document 107352
https://margaretthatcher.org/document/107352
```

The activated formatter renders it once:

```text
Source — Margaret Thatcher Foundation, Speech to Conservative Party Conference, 14 October 1988 (Document 107352)
https://www.margaretthatcher.org/document/107352
```

Post-activation isolated smoke tests confirmed that document `107352` has one
source entry, one document number, and one canonical raw URL; source-role
diagnostics, Markdown links, nested URLs, and the detailed public
`Confidence —` line are absent. The same smoke run covered the no-reliable-source
fallback, URL variants, a Thatcher-authored book with locator, a verified
variant, a secondary recollection, genuinely distinct documents, and
conservative ambiguity handling. Internal records, roles, evidence targets,
and confidence dimensions remain present.

## Validation results

Validation was performed against the exact source tree later committed and
activated, without live X or provider access:

- Focused formatter, reply, source-role, digest, helper, control, and receipt
  tests: 672 passed in the initial deployment gate.
- Inactive manifest tests: 77 passed.
- Final affected-test rerun after all safety corrections: 458 passed.
- Complete offline suite: **2,158 passed, 1 skipped, 12 warnings** in 969.59
  seconds. This exceeds the earlier 2,103-pass reviewed baseline with no
  missing tests, failures, or errors.
- Post-activation isolated formatter smoke tests: 10 passed.
- Targeted Python compilation: passed.
- `git diff --check`, cached-diff check, and staged-scope review: passed.
- Production-shaped runtime preflight: passed with 75 completed history
  records, a valid regular-post receipt, shadow metadata available, shadow
  mode selected, and active semantic-veto enforcement false.
- Two isolated full-corpus audit runs were byte-identical to the tracked audit.

No validation contacted X, uploaded media, called an external AI provider,
created a production receipt, changed posting history or analytics, or
restarted/signalled a service.

## Full-corpus rendering audit

The tracked audit SHA-256 is
`bf8fa8bf19a80482524e261449dd3af716e1c8061429fe61b63f580f941cbde2`.
The two temporary reproductions had the same hash.

Actual audit counts are:

- completed packets rendered: 626/626;
- attribution-eligible quotations and regular-cycle members: 610;
- completed but attribution-ineligible packets: 16;
- unresolved quotations: 6;
- internal renderable source records: 914;
- distinct canonical public sources: 563;
- former duplicate identity groups: 26;
- current duplicate identity groups: 0;
- current duplicate canonical URL groups: 0;
- current duplicate raw URL groups: 0;
- current repeated document-number groups: 0;
- public source-role leakage: 0;
- Markdown links: 0;
- malformed or nested URLs: 0;
- identity conflicts: 0;
- render failures: 0;
- invariant failures: 0; and
- conservative ambiguity diagnostics retained: 68 across 54 packets.

The earlier deployment brief's expected figures of 564 sources, 25 former
duplicate groups, and 40 ambiguity diagnostics were stale. The tracked,
byte-reproducible reviewed artifact contains 563, 26, and 68 respectively. No
ambiguity was guessed or merged merely to match the stale figures.

## Corpus and evidence invariants

The immutable inputs retained their reviewed hashes:

- `mrsMThatcher.txt`:
  `10310a9d62c03a87f2c1e55fa10286d1413216b8c0cb34cb0dbe4b3c12f19bee`;
- `quote_analysis.json`:
  `e53b6e1448335c060f941ddd90cfb8035d12014b691ac93036f606832408d39a`;
- `semantic_alignment_research/quote_research_full_001/research_packets.json`:
  `307b01f0c854ad8e16a50ed399bfa0cbd5f4b8c3d00709bfe02100a289143611`;
- `semantic_alignment_research/quote_research_full_001/corpus_manifest.json`:
  `81f6b2974c30d5810afc74c24704f5ee3d3868a6b2d94859cad2fa8cebce12da`;
- `semantic_alignment_research/quote_research_full_001/historical_context_source_role_audit.json`:
  `afda5152c18774bd2e2db39a001c4b7ea2a712f829e855dbe31579c769052053`;
- `semantic_alignment_research/quote_research_full_001/unresolved_quotes.json`:
  `6acb4d2dede398f74e488902c62c672437db8721f6f75c9adebdf323889feb4f`.

Tests and audit invariants prove that all 610 attribution-eligible quotation
IDs, texts, eligibility decisions, and cycle membership are unchanged. The 16
completed ineligible packets and six unresolved records remain excluded.
Research packets, source-role evidence, confidence metadata, semantic-veto
decisions, posting schedules, histories, ledgers, and analytics were not
rewritten by the source change. Semantic-veto enforcement remains disabled;
hybrid retrieval remains offline-only; generated-image processing was not
activated.

## Rollback material

The private rollback directory is:

`/home/tonym/.local/state/mrsMThatcher/deployments/20260722T100915Z-historical-context-recovery-safety-fix`

The directory is mode 0700 and each contained file is mode 0600:

- `pre_fix_head_46122789.tar.gz`:
  `be351721bed5e6f5221ccf500de6cbddc6b27901fda3479ac8aabebbc5b6e396`;
- `pre_activation_running_491e81cf.tar.gz`:
  `8e2f3e11c2462cd9726e1d9e141dd3abdf83dee2fc9e222a94c85f33b9fea34f`;
- `mrsMThatcher.service`:
  `6ea88612b237cd7afff987d90473096cfa501ff09aebb039d42cd755a9c855ef`;
- `rollback_manifest.md`:
  `32327699247a787340bc91ddc68742ac65a4f57ce91fd6e356f7ecb045f4aac9`.

The manifest records the service architecture, PIDs, modes, affected-file
hashes, production state hashes, untracked-file set, and only the hash/mode of
the environment file. It does not copy environment contents.

## Activation and service verification

The established launch paths resolve directly into the repository:

- `/usr/local/bin/runMrsMThatcher2` resolves to
  `/disks/disk1/etc/mrsMThatcher/runMrsMThatcher2`;
- `/usr/local/bin/mrsMThatcher2.py` resolves to
  `/disks/disk1/etc/mrsMThatcher/mrsMThatcher2.py`.

No secondary source tree was copied. The single restart loaded the pushed
commit directly.

Pre-restart state:

- wrapper PID: 1033185;
- Python child PID: 1033186;
- start: 21 July 2026 22:25:43 BST;
- automatic restart count: 0.

Post-restart state:

- wrapper PID: 3168099;
- Python child PID: 3168100;
- start: 22 July 2026 11:12:19 BST;
- automatic restart count: 0;
- service: active/running;
- process cardinality: one wrapper and one Python child.

Startup recorded the active global control and explicitly left main-post
receipts untouched. It then logged a successful start and completed multiple
paused loop ticks without entering a remote lane. There was no traceback,
import error, schema error, stale-fingerprint failure, source-role failure,
receipt deletion, ambiguous-outcome warning, duplicate process, or restart
loop. Active runtime file hashes matched the activated commit.

The engagement-analytics timer remained active/waiting and was neither
restarted nor signalled. `systemctl --user --failed` reported no failed units.
Its independently scheduled analytics runs were not suppressed or modified.

## Production state and remote-write barriers

The mode-0600 control file has SHA-256
`66efff3be480a8074e837e4f95dd3828e865a63b2767e90e836a7a4708b9d6b4`
and keeps all bot lanes paused. It is untracked and was not committed.

The outstanding regular receipt remains byte-for-byte intact with SHA-256
`576fba844e98d8540eb677e249f5e1d3fa6bdf3897481703d608c7c42f00d223`.
Historical-context reply history remained
`1a22472c0546fa3f98799b52d465d1db063e73f52ae5c14adbf53b4912a9e92c`;
the quote and image usage files remained
`ff2ce53a82e792fd7a919890d48e91497c4c5b376b8fe1ea229df5481c0172ae`
and
`9252e5c50c9c28ceb82201594597c58f01583eee913f474eee7c4e3903e114f1`.
No meme, historical-context, confirmed-reply, or ambiguous-outcome receipt was
present, and there was no pending AI draft.

While the old process was already paused, it followed its existing behavior of
moving an overdue quote schedule forward in five-minute increments; the next
quote time moved from 11:03:44 to 11:14:25 BST. It did not post. The new code
prevents receipt reconciliation and remote lane entry while the global pause
is active. On startup it also normalized a missing `pending_ai_reply_drafts`
state value to an empty object; no schedules, IDs, counters, posting histories,
or receipt contents changed as part of that normalization.

No X post or reply, media upload, external AI/provider request, receipt
deletion, state reset, analytics mutation by the deployment, or unrelated
service restart occurred.

## Remaining risks and required operator decision

The source deployment and activation are healthy. Normal posting is not yet
resumed because the durable receipt represents an external post already made
and an intended context reply not yet made. Automatically deleting it could
lose reconciliation evidence; automatically processing it would make an X
write that this deployment was explicitly forbidden to perform.

Before removing the global pause, an operator must explicitly choose one of:

1. authorize one controlled reconciliation of the intended historical-context
   reply while other posting lanes remain disabled; or
2. explicitly abandon and record that pending reply through a documented
   recovery path, preserving evidence that the main post already succeeded.

A separate, nonblocking code risk remains for future operations: if global
pause is activated during an already-running lane, the immediate boundary
check prevents the remote write but the raised pause exception can restart the
Python child once. That case did not occur here because pause was active before
startup, and it does not weaken the remote-write barrier.

The documentation commit containing this report is intentionally separate from
the activated runtime commit. It changes no runtime file and does not require a
second restart.

HISTORICAL-CONTEXT PUBLIC-SOURCE DEPLOYMENT SUCCESSFUL
