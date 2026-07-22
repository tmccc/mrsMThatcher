# Historical-context evidence truth review

## Executive summary

This change corrects future rendering of Margaret Thatcher Foundation document
`104653`, replaces broad source-role admission with claim-specific public-field
admission, and adds deterministic corpus, archive-page and published-reply
review artefacts.

The correction is prospective.  It does not edit the historical X reply, the
77-entry reply history, the canonical research packets, quotation text,
quotation identity, eligibility or cycle membership.

The completed-packet audit still covers 626 packets: 610 are
attribution-eligible, 16 are completed but ineligible, and six remain
unresolved.  All 626 render through the current public formatter without a
render, correlation or invariant failure.

The review does **not** claim that every historical assertion in the corpus has
now been semantically verified.  In particular:

- a machine comparison with an archive page is a diagnostic, not a semantic
  adjudication;
- failed archive retrievals remain unresolved;
- records marked `manual_review_required` require human judgement;
- 22 issues found in the 77 already-published replies remain open for future
  rendering or research;
- published X history remains untouched.

## Document 104653

### Root cause

The canonical packet already contained the correct event, date and stable
locator:

- event: `Speech to Conservative Women's Conference`;
- date: `20 May 1981`;
- locator: `Margaret Thatcher Foundation document 104653`.

Those facts were not admitted publicly because the only retained renderable
record was a virtual locator supporting wording and attribution.  The official
MTF URL existed only as an uninspected model-proposed discovery lead, so the
source-role policy correctly declined to use it as evidence for the event and
date.

Separately, the packet's generated `intended_argument` strengthened the
transcript.  It changed Thatcher's qualified phrase, "some of the aims", into
claims about inevitable shared core aims, inherent causation, suppression and
state power.  Those additions were not established by the primary passage.

This was therefore an evidence-admission and semantic-prose defect, not a
canonical-source deduplication defect.

### Evidence correction

The curated evidence now contains an independently inspected primary
transcript record for:

```text
https://www.margaretthatcher.org/document/104653
```

The record binds the exact supporting passage by SHA-256 and assigns only the
roles and claims the page establishes:

- wording;
- attribution;
- source event;
- date.

It deliberately does not assign `historical_context_support`.

The Meaning correction is stored separately in
`historical_context_packet_corrections.json`.  The validator binds it to:

- the quote ID and quotation-text hash;
- the hash of the original `intended_argument`;
- the hash of the corrected value;
- the inspected primary source ID.

Only `intended_argument` may be changed by this sidecar.  The canonical
`research_packets.json` file remains unchanged.

### Future public output

```text
Context — Speech to Conservative Women's Conference, 20 May 1981.

Meaning — Thatcher argued that socialism shares some aims with Marxism and that pursuing those aims subordinates individual rights to political doctrine.

Verification — Verified excerpt

Source — Margaret Thatcher Foundation, Speech to Conservative Women's Conference, Central Hall, Westminster, 20 May 1981 (Document 104653)
https://www.margaretthatcher.org/document/104653
```

The wording remains `Verified excerpt` because the corpus quotation omits the
transcript's leading "And".  The already-published reply is not rewritten.

## Claim-specific public-field admission

The prior implementation treated `source_event_support` as sufficient to
publish both `source_event` and `date`.  That allowed a date-only source to
admit an event, or an event-only source to admit a date.

The new policy admits each public field only when:

1. a renderable evidence row explicitly includes that exact field in
   `claims_supported`; and
2. the post-audit confidence for that field is not `unknown`.

The regenerated source-role audit records 68 changed public projections:

- 65 packets retain a supported date but no longer expose an unsupported
  event;
- two packets retain a supported event but no longer expose an unsupported
  date;
- document `104653` gains its newly inspected event and date.

No source record was deleted.  For the other 67 packets, internal source
records, roles, claims and confidence values are unchanged; only the public
projection changed.  Document `104653` adds one curated primary record and the
corresponding evidence-backed event/date confidence.

## Full-corpus evidence truth audit

The deterministic truth audit renders the actual current public formatter and
correlates it with the immutable published-reply history.

| Measure | Result |
|---|---:|
| Completed packets | 626 |
| Current public renderings | 626 |
| Attribution-eligible packets | 610 |
| Completed but attribution-ineligible packets | 16 |
| Unresolved quotations | 6 |
| Published history entries | 77 |
| Unique published-history quote IDs | 77 |
| Render failures | 0 |
| History/corpus correlation errors | 0 |
| Invariant failures | 0 |
| Precise MTF-identity packets | 435 |
| Same accepted/lead MTF-document packets | 144 |
| Eligible event/date priority packets | 139 |

The audit also reports 1,539 packet claims across 625 packets that are not
currently supported for public display.  These are a high-recall research and
triage queue, not 1,539 proven factual errors.  Packet prose is never treated
as evidence for itself.

Current truth-audit SHA-256:

```text
8e8d968d580a7fb9bf5cf84b3b4b607e3e73a0ffffaeca0d68a44cfb700ce1f6
```

## Public-source rendering audit

| Measure | Result |
|---|---:|
| Internal renderable source records | 915 |
| Distinct canonical public sources | 563 |
| Packets with a public source merge | 243 |
| Internal records merged for display | 352 |
| Packets with no reliable public source | 107 |
| Duplicate canonical identity groups after rendering | 0 |
| Duplicate canonical/raw URL groups after rendering | 0 |
| Repeated archive-document groups after rendering | 0 |
| Public source-role leakage | 0 |
| Markdown links | 0 |
| Malformed or nested URLs | 0 |
| Identity conflicts | 0 |
| Conservatively retained identity ambiguities | 68 |

The 68 identity ambiguities in this table are distinct from the 68 changed
context-field projections above.  Ambiguous identities remain separate rather
than being guessed into a merge.

Current public-source audit SHA-256:

```text
fcba7e6f3532a6ae2fbc4eb78f30745e073032da3c138ccac9cd8c28d13ec79f
```

The audit's recorded input hashes include the packet-correction sidecar and
curated-evidence file as well as the canonical corpus and source-role audit.
The all-render review propagates the same bindings.  Two isolated
regenerations were byte-identical.

## Published-reply semantic ledger

The former aggregate-only 77-case review is now a deterministic per-record
ledger.  Every record contains:

- history position, quote ID and quotation-text hash;
- parent-post and reply-post IDs;
- SHA-256 hashes of the published and current reply text;
- the manual disposition and current follow-up status;
- a concise claim-specific reason;
- retained renderable source IDs and source-quality classes;
- public context fields and wording confidence;
- successful MTF document comparisons, where available.

The builder/validator joins those editorial judgements to the truth audit and
source-role audit and fails closed if a record, hash, reason, source binding,
aggregate or worklist changes unexpectedly.

### Original 77-case dispositions

| Disposition | Count |
|---|---:|
| Supported as published | 41 |
| Future correction needed | 30 |
| Insufficient evidence to assess | 6 |
| Total | 77 |

### Current follow-up

| Follow-up | Count |
|---|---:|
| Non-supported or uncertain cases reviewed | 36 |
| Resolved for future rendering | 14 |
| Still open | 22 |
| Open original correction cases | 18 |
| Open original insufficient-evidence cases | 4 |

The two tables reconcile: 12 of the original correction cases and two of the
original insufficient-evidence cases are now resolved for future rendering.
The 22 open records remain individually listed in the artefact.  No disposition
causes an automatic evidence promotion.

The first hash below records the pre-hardening draft.  The final ledger was
regenerated against the final MTF artefact and its per-record document checks;
its input metadata binds the final MTF SHA-256 and the 262 successful and two
not-found document outcomes.

```text
DRAFT_SEMANTIC_LEDGER_SHA256: bd33ad60d8f89b808d6737fefeae579db44245d9bc862d5669b2479234d4a15a
FINAL_SEMANTIC_LEDGER_SHA256: 55e59e448a94446b94b6f30b310d81880eb414503aee949e84ea9d34da0c9abe
```

## Margaret Thatcher Foundation primary-page review

### Scope

The diagnostic review queue covers:

- 435 packets with a precise MTF identity;
- 264 unique MTF document numbers;
- 530 packet/document comparisons;
- 139 priority packets, representing 148 comparisons;
- 296 remaining candidate packets, representing 382 comparisons.

The priority queue contains attribution-eligible packets where accepted
evidence and a discovery lead identify the same MTF document and a known event
or date is not yet fully admitted.

### Method and limitations

The tool retrieves compact read-only representations of official archive pages
and checks:

- canonical URL and document number;
- stated author;
- official date;
- title/event token similarity;
- longest contiguous quotation-token coverage;
- transported-page and article-text hashes.

It stores compact metadata and hashes, not full page text.  It does not contact
X or invoke a generative-AI/provider inference API, and it cannot alter
evidence roles or production state.  Archive retrieval did use the report's
disclosed read-only `r.jina.ai` text transport because direct command-line
requests to the official site encountered its anti-bot challenge.

`no_machine_detected_discrepancy` means only that the configured mechanical
checks found no discrepancy.  It is not a human semantic verdict.  Likewise,
`manual_review_required` is a queue entry, not proof that the packet is wrong.
Failed retrievals remain unresolved and must not be treated as negative
evidence.

### Final hardened MTF run

The final hardened review values must be inserted only after the isolated run
has completed and its artefact has passed validation:

```text
FINAL_HARDENED_MTF_SHA256: f93a27e1e5bdcb658430bf03c08ca2c5cac1c34121087bd4290fa77e5f543148
FINAL_HARDENED_MTF_UNIQUE_DOCUMENTS: 264
FINAL_HARDENED_MTF_RETRIEVAL_SUCCESSES: 262
FINAL_HARDENED_MTF_TRANSPORT_FAILURES: 0
FINAL_HARDENED_MTF_OFFICIAL_DOCUMENTS_NOT_FOUND: 2
FINAL_HARDENED_MTF_NO_MACHINE_DISCREPANCY_COMPARISONS: 242
FINAL_HARDENED_MTF_MANUAL_REVIEW_COMPARISONS: 286
FINAL_HARDENED_MTF_OFFICIAL_DOCUMENT_NOT_FOUND_COMPARISONS: 2
```

The two unresolved identities are discovery-lead document numbers `109236`
and `111346`.  The archive returned stable canonical not-found pages for both;
they are recorded as `official_document_not_found`, not conflated with a
network or transport failure and not used as negative evidence about the
quotation itself.  The final artefact was byte-identical across two cache-only
reconciliations, avoiding transport-page variation in the persisted result.
These values must not be described as counts of semantically verified quotes.

## Source-field manual-review projections

The 139-packet, 148-comparison priority subset has a separate deterministic
manual-review projection.  It records archive identity, source-event, date and
quotation-linkage findings for each packet/document comparison.  It explicitly
does not assess the semantic Meaning, authorise evidence promotion, or mutate a
packet or source role.

The remaining precise-MTF subset has the same deliberately limited projection
for its 296 packets and 382 packet/document comparisons.  Together the two
artefacts account for all 435 precise-identity packets and all 530 comparisons;
they remain source-field review ledgers, not semantic verification of the
Meaning prose.

Both projections were regenerated and revalidated against the final hardened
MTF artefact.  Their final hashes and aggregate dispositions are:

```text
FINAL_PRIORITY_REVIEW_SHA256: b21725f0c64b8dfee315218763c557f60f9dace4a289aa2bc199d61f45a2ffa6
FINAL_PRIORITY_COMPARISON_DISPOSITIONS: {"mismatch":29,"retrieval_failed":0,"uncertain":43,"verified":76}
FINAL_PRIORITY_PACKET_DISPOSITIONS: {"mismatch":28,"retrieval_failed":0,"uncertain":40,"verified":71}
FINAL_REMAINING_REVIEW_SHA256: 8f1101fd514d4892dcc0404d301c0d6d2f8262f95cfa52ca88d5c6363405b6b2
FINAL_REMAINING_COMPARISON_DISPOSITIONS: {"mismatch":137,"uncertain":86,"verified":159}
FINAL_REMAINING_PACKET_DISPOSITIONS: {"mismatch":129,"uncertain":56,"verified":111}
```

## Formatter-trial regression

The prospective `104653` Meaning correction intentionally changed one output
relative to the frozen 15 July formatter-v2 trial snapshot.  Rewriting that
snapshot would falsify the historical trial and make its corpus, pairwise,
length and provenance artefacts internally inconsistent.

The parity regression now:

- requires byte-for-byte parity for every quote without a reviewed
  post-promotion correction;
- requires the correction manifest's original Meaning hash to match the frozen
  candidate;
- requires the corrected Meaning hash to match the sidecar;
- requires the current output to equal one exact Meaning substitution;
- leaves every frozen trial artefact unchanged.

## Corpus preservation

The following immutable inputs remain unchanged:

```text
mrsMThatcher.txt
10310a9d62c03a87f2c1e55fa10286d1413216b8c0cb34cb0dbe4b3c12f19bee

quote_analysis.json
e53b6e1448335c060f941ddd90cfb8035d12014b691ac93036f606832408d39a

research_packets.json
307b01f0c854ad8e16a50ed399bfa0cbd5f4b8c3d00709bfe02100a289143611

historical_context_reply_history.json
2cb281c2c60b7067c8c0cfc05422c78544990b49f9dfdd0eabf8b548bf69497d
```

The source-role audit still contains 626 records and the attribution-eligible
set remains exactly 610.  Quote IDs, quotation text and cycle membership are
unchanged.  Semantic-veto enforcement remains disabled; its inactive derived
metadata was refreshed only because the source/audit fingerprint changed.

## Files modified or added

### Runtime and validation

- `historical_context_formatter.py`
- `historical_context_source_roles.py`
- `mrs_log_digest.py`
- `historical_context_packet_corrections.py`
- `historical_context_published_reply_semantic_review.py`

### Evidence and audit artefacts

- `semantic_alignment_research/quote_research_full_001/historical_context_packet_corrections.json`
- `semantic_alignment_research/quote_research_full_001/historical_context_source_curated_evidence.json`
- `semantic_alignment_research/quote_research_full_001/historical_context_source_role_audit.json`
- `semantic_alignment_research/historical_context_source_audit_001/audit_summary.json`
- `semantic_alignment_research/historical_context_source_audit_001/source_snapshot_manifest.json`
- `historical_context_evidence_truth_audit.py`
- `historical_context_evidence_truth_audit.json`
- `historical_context_public_source_audit.py`
- `historical_context_mtf_primary_review.py`
- `historical_context_mtf_primary_review.json`
- `historical_context_mtf_priority_manual_review.py`
- `historical_context_mtf_priority_manual_review.json`
- `historical_context_mtf_remaining_manual_review.py`
- `historical_context_mtf_remaining_manual_review.json`
- `historical_context_published_reply_semantic_review.json`
- `historical_context_public_source_deduplication_audit.json`
- `historical_context_public_render_review.txt`

### Tests

- `tests/test_historical_context_packet_corrections.py`
- `tests/test_historical_context_evidence_truth_audit.py`
- `tests/test_historical_context_mtf_primary_review.py`
- `tests/test_historical_context_mtf_priority_manual_review.py`
- `tests/test_historical_context_mtf_remaining_manual_review.py`
- `tests/test_historical_context_published_reply_semantic_review.py`
- `tests/test_historical_context_source_roles.py`
- `tests/test_historical_context_public_source_deduplication.py`
- `tests/test_historical_context_public_render_review.py`
- `tests/test_historical_context_formatter_trial.py`
- `tests/test_digest_reply_observability.py`

### Refreshed inactive derived metadata

- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/checksums.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/material_veto_v3_shadow_manifest.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/runtime_eligible_quote_manifest.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/v3_shadow_manifest_audit.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/v3_shadow_manifest_audit.md`

Pre-existing source-compression pilot files and `mrsMThatcher.control.json` are
unrelated and must remain untracked and unstaged.

## Validation

Completed focused checks include:

- claim-specific source-role admission regressions: passed;
- document `104653` correction and future-render regression: passed;
- frozen formatter-trial correction regression: `1 passed`;
- 77-record semantic-ledger validator tests: `6 passed`;
- final MTF and source-field/semantic-ledger validator group: `29 passed`;
- targeted Python compilation: passed;
- `git diff --check`: passed.

```text
FINAL_FOCUSED_TEST_RESULT: 720 passed, 22 warnings in 195.89s
FINAL_COMPLETE_OFFLINE_SUITE_RESULT: 2205 passed, 1 skipped, 25 warnings in 990.52s
FINAL_PYTHON_COMPILATION_RESULT: passed for every modified or added Python file
FINAL_GIT_DIFF_CHECK_RESULT: passed before staging and for the staged source diff
POST_ACTIVATION_FORMATTER_SMOKE_RESULT: 9 passed in 4.94s
```

## Production safety

At 13:37 BST on 22 July 2026, before any activation:

- `mrsMThatcher.service` was `active/running`;
- wrapper PID: `3361043`;
- Python child PID: `3361044`;
- service start: `22 July 2026 12:22:29 BST`;
- crash restart count: `0`;
- the bot remained unpaused;
- the published-reply history hash remained unchanged.

No X post or reply was created, edited or deleted by this work.  No media was
uploaded, no generative-AI/provider inference API was called, no receipt was
cleared, no posting state or analytics was changed, and no service was
restarted while the review and implementation were being prepared.  The
read-only archive transport disclosed above was the only external retrieval
used by the MTF page diagnostic.

```text
SOURCE_COMMIT: 1a7d8f8eaab8ae6359a494b7c159bddd7c7035aa
SOURCE_PARENT_COMMIT: 7209fe2d00ec5c2cd7e7c458c551f015f1f80819
PUSH_RESULT: master pushed successfully; local HEAD and origin/master matched
ACTIVATION_TIME: 22 July 2026 14:38:32 BST
PRE_ACTIVATION_WRAPPER_PID: 3361043
PRE_ACTIVATION_CHILD_PID: 3361044
POST_ACTIVATION_WRAPPER_PID: 3745199
POST_ACTIVATION_CHILD_PID: 3745200
POST_ACTIVATION_SERVICE_STATE: active/running, NRestarts=0
ROLLBACK_BUNDLE: /home/tonym/.local/state/mrsMThatcher/deployments/20260722T133425Z-historical-context-evidence-truth
ROLLBACK_ARCHIVE_SHA256: dee338fbf5fdc375ae7ee12e828585c45ae408899149ba5db405c42743639101
```

Immediately before activation, all regular-post, meme-post,
historical-context-reply and confirmed-reply receipts were absent, the global
ambiguous-outcome marker was absent, and `pending_ai_reply_drafts` was empty.
The next quote remained scheduled for 14:53:29 BST.  The controlled restart
loaded that same schedule and the same last-post epochs; it did not create a
receipt or perform a remote write.

Startup verification found exactly one wrapper and one Python child, no failed
user units, and an unchanged healthy analytics timer.  The runtime log reports
`active_enforcement=false` for semantic veto, confirms generated-image
processing remains suspended, and reaches `Bot started successfully` without
a traceback, import failure, schema failure or receipt warning.  No test post
or live historical-context reply was manufactured.

## Remaining risks and work

1. Twenty-two published-reply findings remain open for future correction or
   additional evidence.  This change does not silently rewrite them.
2. The two official archive identities that resolve to stable not-found pages,
   and all manual-review cases, must remain conservative until separately
   adjudicated; machine comparison must not promote evidence.
3. Archive text transported through a read-only intermediary must be checked
   against official canonical identity and stored hashes; the transport itself
   is not an additional historical authority.
4. The semantic ledger makes the 77 judgements auditable, but it remains an
   editorial review, not proof that every unflagged interpretation is uniquely
   correct.
5. The already-published `104653` reply remains visible with its original text;
   only future rendering is corrected, as requested.
6. The next scheduled quote is the first normal production opportunity to use
   the activated formatter; no live post was created merely to test it.

The reviewed source tree is committed, pushed and active.  This report is the
separate documentation-only record of that controlled activation.
