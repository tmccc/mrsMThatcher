# Historical-context semantic safety gate and projection review

Date: 22 July 2026
Host: `big-nas-2`
Project: `/disks/disk1/etc/mrsMThatcher`
Reviewed base commit: `ff328350a21fb963bf8c9c3d40e02bdf29b842df` (`master`, equal to `origin/master`)

## Executive summary

This work implements the follow-up safety actions arising from the independent
review of `historical_context_evidence_truth_review_report.md`. It has not been
committed, pushed, deployed or loaded by the running bot.

The result is a historical-context-only fail-closed gate. The 22 cases left
open by the original 77-reply review are blocked from future public historical-
context replies. While this work and its deployment gate were running, the
undeployed bot published two additional historical-context replies, at
`2026-07-22T13:53:58Z` and `2026-07-22T16:03:24Z`. Both were explicitly
reviewed and found to contain semantic expansion, so they are recorded as
post-baseline correction cases. The final gate therefore blocks 24 quote IDs:
20 future-correction cases and four insufficient-evidence cases.

All 24 quotations remain eligible for ordinary quote/image posts. The complete
610-member regular-post cycle is unchanged. If the semantic ledger is missing,
stale or inconsistent, only the optional historical-context lane closes.

The 67 downgraded event/date projections were rendered through the real public
formatter and reviewed deterministically. Sixty-five are safe date-only
projections. Two are event-only projections: one was already clean, and one
contained a composite diagnostic label with embedded years and a slash. The
public formatter now replaces only that unsafe event-only projection with:

```text
Context — The surviving record identifies an occasion, but does not establish a reliable date.
```

The raw composite remains available internally.

The broad figure of 1,539 unsupported packet claims is now decomposed by field,
eligibility and actual public reachability. It contains no `intended_argument`
or Meaning claims. It is a source-event/date/historical-context research queue,
not a count of 1,539 false public statements.

Final focused validation: **706 passed**, zero failures, with nine expected
BeautifulSoup/lxml deprecation warnings. The complete offline suite passed
**2,242 tests with one expected skip**. Three independent audit families were
regenerated twice and were byte-identical to their tracked review artefacts.

## Scope and behaviour

The implementation changes only public historical-context admission and one
unsafe public event-only presentation. It does not change:

- quotation text or quote IDs;
- attribution or regular-post eligibility;
- the 610-member runtime cycle;
- research packets or source-role records;
- source roles, evidence targets or supporting passages;
- confidence metadata;
- unresolved or excluded quotation decisions;
- semantic-veto enforcement;
- posting schedules, histories, ledgers, receipts or analytics.

Any existing historical-context receipt is reconciled before configuration,
packet-availability or policy exits. This ordering prevents a new policy
decision, a disabled lane or a missing packet from hiding an ambiguous prior
remote outcome or stranding an already confirmed reply. A confirmed receipt
for another parent post cannot bypass the gate.

Gate startup state and per-reply semantic dispositions/hashes are retained in
both the machine digest and its default Markdown report. When the gate is
unavailable, the offline audit models all 610 eligible historical-context
replies as blocked, matching runtime behaviour, rather than labelling them
allowed.

Internal evidence remains complete. The gate suppresses only the future public
historical-context reply for an open quote ID; it does not delete packet data or
alter the ordinary quote/image post.

## Published-reply semantic ledger

The original manual baseline contained 77 unique published replies. Its exact
quote-ID set is bound by SHA-256:

```text
f23372c0227e63b89d2c4634b8a17dfbdc409f59d987a7c326fb0400d5991976
```

The first post-baseline reply is quote ID:

```text
a97e6dd2f444ecfbba67977a34be91db40d17eb09c8566fe714e48bffddb11f7
```

Quotation:

```text
...The larger the slice taken by government, the smaller the cake available for everyone.
```

The published Meaning added government intervention, stifled growth and reduced
total wealth. The quotation and retained secondary wording/attribution evidence
establish only that a larger government share leaves less available to others.
It is therefore explicitly classified `future_correction_needed`; it is not
silently accepted by a default rule.

The second post-baseline reply is quote ID:

```text
34114f8f8fa580a2cb413c481408094ad2a8675ebb59955cf8c7d665897d8381
```

Quotation:

```text
Free enterprise has a universal truth at its heart: to create a genuine market in a state you have to take the state out of the market.
```

Its published Meaning added state ownership, stifled economic freedom and
efficiency, and a specific government-divestment mechanism. The quotation and
retained primary passage establish the narrower claim that creating a genuine
market requires taking the state out of the market. Independent read-only
review therefore also classified it `future_correction_needed`.

The ledger now rejects any later published-history row unless its quote ID is
added to the explicit review scope. This prevents future audit refreshes from
automatically labelling unreviewed replies `supported_as_published`.

| Semantic-review result | Count |
| --- | ---: |
| Reviewed published replies | 79 |
| Supported as published | 41 |
| Future correction needed | 32 |
| Insufficient to assess | 6 |
| Previously open cases resolved by current rendering | 14 |
| Open original correction cases | 18 |
| Open original insufficient-evidence cases | 4 |
| Open post-baseline correction cases | 2 |
| Total blocked for future historical-context rendering | 24 |

Semantic-ledger SHA-256:

```text
b6bef0282fda2b2476bf2ab5b84b928afa09c04b7a953faae068d8c3c7db73c7
```

Blocked-projection SHA-256:

```text
5c166e3fd2c31a01ca132a8b046e8cc02e36ab787e7060dd0fa952d719afc1b2
```

## Gate audit

The offline gate audit processed every completed packet through the current
formatter without contacting X or a provider.

| Gate-audit measure | Count |
| --- | ---: |
| Completed packets | 626 |
| Attribution-eligible packets | 610 |
| Completed attribution-ineligible packets | 16 |
| Unresolved quotations | 6 |
| Historical-context replies blocked | 24 |
| Eligible historical-context replies allowed | 586 |
| Ineligible packets kept out of the regular-post set | 16 |
| Invariant failures | 0 |

The runtime-cycle invariant resolves runtime aliases and binds the manifest to
the current `mrsMThatcher.txt` and `research_packets.json` hashes. A manifest
with a false active-source hash now fails the invariant.

Gate-audit SHA-256:

```text
2e9a90c3988cf4be6c90f3e80644d521ac4d32b474e2a514f7d1b800a9ac6add
```

## Review of the 67 downgraded projections

The projection reviewer reconstructs the exact v7-to-v8 field-admission change
and renders each affected packet through the public formatter.

| Projection-review measure | Count |
| --- | ---: |
| Total projection changes, including the 104653 upgrade | 68 |
| Downgraded projections | 67 |
| Date-only downgrades | 65 |
| Day-precision date-only projections | 60 |
| Month-precision date-only projections | 3 |
| Year-precision date-only projections | 2 |
| Event-only downgrades | 2 |
| Already clean event-only projection | 1 |
| Event-only projection using safe fallback | 1 |
| Duplicate full public replies caused by the downgrade | 0 |
| Source-title/event tensions retained as manual hints | 9 |

Ten Context-only duplicate groups, covering 26 records, remain because generic
evidence-conservative sentences are intentionally reusable. Their complete
public replies are distinct. The nine source-title tensions are triage hints;
they do not promote packet prose into evidence.

Projection-review SHA-256:

```text
c82ddd0d98a48e647caf3e81c89138fe73689acf5baea2cd4d06370dbb0dd455
```

### Unsafe event-only case corrected

Quote ID:

```text
b32d8cdf5977dee436857e8060d3a83ebfe54de9f6dabb20ffc65a0796338b5c
```

Previous public Context:

```text
Context — Pre-election statements (1979) / Recalled in BBC1 Panorama Interview (1984).
```

Current prospective public Context:

```text
Context — The surviving record identifies an occasion, but does not establish a reliable date.
```

Internal rendering continues to retain the complete composite label and its
evidential diagnostics.

## Decomposition of the 1,539 unsupported packet claims

The truth audit is schema version 2 and preserves all previous records while
adding a flat, deterministically sorted decomposition covering 1,539 findings
across 625 packets.

### By field

| Packet field | Count |
| --- | ---: |
| Source event | 489 |
| Date | 425 |
| Historical context | 625 |
| Intended argument / Meaning | 0 |
| Total | 1,539 |

### By attribution eligibility

| Eligibility | Count |
| --- | ---: |
| Attribution eligible | 1,504 |
| Attribution ineligible | 35 |

### By actual public reachability

| Reachability | Count |
| --- | ---: |
| Exact projection currently occurs in public output | 113 |
| Formatter-reachable but currently suppressed | 765 |
| Internal-only or unreachable | 661 |

All 113 current occurrences are in public source titles: 112 source-event
strings and one date string. None occurs in public Context, Meaning or source
URL. A bibliographic title occurrence is recorded as an editorial exposure
risk; it is not treated as proof that the source supports the packet claim.

At the Context-slot level, 113 occurrences are current, 1,391 are eligible but
not currently exposed, and 35 are production-ineligible. All 1,539 findings
have `no_audited_source_claim`; packet prose and literal title occurrence are
not allowed to prove themselves.

Truth-audit SHA-256:

```text
762c8a17d2c6da2e79c419acd287380f6604ff4f0aa3a4f6acf48a6d614d6974
```

## Public-source and raw-render checks

The existing full-corpus source-deduplication audit regenerated byte-for-byte
unchanged:

```text
fcba7e6f3532a6ae2fbc4eb78f30745e073032da3c138ccac9cd8c28d13ec79f
```

It reports 626 successful renders, 610 eligible quotations, zero current
duplicate canonical identities, zero duplicate canonical/raw URLs, zero
repeated archive document numbers, zero Markdown links, zero malformed/nested
URLs, zero public source-role leakage and zero invariant failures.

The raw all-packet render review changed only its formatter hash, the b32
character count and the b32 Context line. Its SHA-256 is:

```text
5034853f6227dad14d2c7bc7e138fa54ad7df052377579ddf41718c6b1c84f28
```

## Immutable corpus proofs

The corpus inputs remain unchanged:

| File | SHA-256 |
| --- | --- |
| `mrsMThatcher.txt` | `10310a9d62c03a87f2c1e55fa10286d1413216b8c0cb34cb0dbe4b3c12f19bee` |
| `quote_analysis.json` | `e53b6e1448335c060f941ddd90cfb8035d12014b691ac93036f606832408d39a` |
| `research_packets.json` | `307b01f0c854ad8e16a50ed399bfa0cbd5f4b8c3d00709bfe02100a289143611` |
| `corpus_manifest.json` | `81f6b2974c30d5810afc74c24704f5ee3d3868a6b2d94859cad2fa8cebce12da` |
| source-role audit | `431793e66427d1d35da42858d9cc6b516f31667a07a21c5fc5cbe705441193a2` |
| `unresolved_quotes.json` | `6acb4d2dede398f74e488902c62c672437db8721f6f75c9adebdf323889feb4f` |

The audit and focused tests prove:

- 626 completed packets remain present;
- exactly 610 remain attribution eligible and in the regular-post cycle;
- 16 completed packets remain attribution ineligible;
- six unresolved quotations remain excluded;
- quote IDs and quotation text are unchanged;
- source-role records and confidence metadata are unchanged.

## Validation performed

Final focused command covered:

- truth-audit tests;
- priority and remaining MTF manual-review projections;
- the 67-projection public-render review;
- full public-render and public-source audits;
- published semantic-ledger validation;
- historical-context formatter and reply behaviour;
- semantic-gate runtime and audit behaviour;
- source-role admission;
- digest observability;
- bot unit helpers, including regular-receipt replay and context receipt
  reconciliation.

Result:

```text
706 passed, 9 warnings in 135.56s
```

After the independent review identified the final unavailable-gate invariant
labelling issue, the affected audit tests were rerun on the corrected tree:

```text
6 passed in 12.33s
```

Additional checks:

- all modified/new Python files compiled with `python3 -m py_compile`;
- `git diff --check` passed;
- semantic-ledger `--check` passed;
- truth audit: two isolated runs byte-identical to each other and the tracked artefact;
- projection review: two isolated runs byte-identical to each other and the tracked artefact;
- gate audit: two isolated runs byte-identical to each other and the tracked artefact.

The first complete-suite run found one stale formatter fingerprint in inactive
semantic-veto metadata:

```text
1 failed, 2240 passed, 1 skipped, 25 warnings in 1034.27s
```

After the established offline metadata refresh, the exact failing regression
passed and the complete suite was rerun from the beginning against the final
tree:

```text
2242 passed, 1 skipped, 25 warnings in 1027.35s
```

## Files modified or added

Runtime and formatter:

- `historical_context_formatter.py`
- `historical_context_reply_semantic_gate.py` (new)
- `historical_context_published_reply_semantic_review.py`
- `mrsMThatcher2.py`
- `mrs_log_digest.py`

Audit programmes and artefacts:

- `historical_context_evidence_truth_audit.py`
- `historical_context_evidence_truth_audit.json`
- `historical_context_public_projection_review.py` (new)
- `historical_context_public_projection_review.json` (new)
- `historical_context_reply_semantic_gate_audit.py` (new)
- `historical_context_reply_semantic_gate_audit.json` (new)
- `historical_context_published_reply_semantic_review.json`
- `historical_context_mtf_priority_manual_review.json`
- `historical_context_mtf_remaining_manual_review.json`
- `historical_context_public_render_review.txt`
- `historical_context_semantic_safety_gate_report.md` (new)

Deterministically refreshed inactive semantic-veto metadata:

- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/checksums.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/material_veto_v3_shadow_manifest.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/runtime_eligible_quote_manifest.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/v3_shadow_manifest_audit.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/v3_shadow_manifest_audit.md`

Tests:

- `tests/test_digest_reply_observability.py`
- `tests/test_historical_context_evidence_truth_audit.py`
- `tests/test_historical_context_public_projection_review.py` (new)
- `tests/test_historical_context_published_reply_semantic_review.py`
- `tests/test_historical_context_reply.py`
- `tests/test_historical_context_reply_semantic_gate.py` (new)
- `tests/test_historical_context_reply_semantic_gate_audit.py` (new)
- `tests/test_historical_context_source_roles.py`
- `tests/test_unit_helpers.py`

The pre-existing source-compression pilot files, control file and production
state/receipts were excluded from the intended change and left untouched.

The first complete-suite run exposed a stale inactive shadow-manifest
fingerprint for the intentionally changed formatter. The established offline
`prepare-v3-shadow` builder refreshed only the formatter fingerprint and its
propagated manifest/audit checksums. Independent comparison confirmed that all
610 IDs, 22,066 pair decisions, policy values and coverage stayed unchanged;
active enforcement remains false and the running shadow was not reloaded.

## Production isolation

No service mutation, restart, reload or signal; X call; provider call; media
upload; receipt mutation; posting-state mutation; analytics mutation; commit;
push; or deployment was performed for this tranche. Service inspection was
read-only.

The independently running pre-change service completed its already scheduled
17:03 BST regular post and historical-context reply while the deployment gate
was in progress. That normal production action increased the immutable reply
history from 78 to 79 rows. The new row was reviewed explicitly and added to
the future-only gate; no history row was edited or deleted.

The running service remains the previously deployed version:

| State | Value |
| --- | --- |
| Active/substate | `active` / `running` |
| Wrapper PID | `3745199` |
| Python child PID | `3745200` |
| Start time | `Wed 2026-07-22 14:38:32 BST` |
| Crash restart count | `0` |

These match the process IDs and start time recorded after the preceding
controlled evidence-truth deployment. The prospective gate and b32 formatter
change are not loaded by this process.

## Remaining risks and next work

1. The 24 blocked cases still require case-by-case semantic correction or
   evidence work. The gate prevents repetition; it does not resolve them.
2. The 1,539-claim decomposition is triage, not semantic adjudication. Its 765
   directly reachable but suppressed fields should be prioritised by source
   quality and likely editorial value.
3. The 113 source-title occurrences warrant bibliographic review but must not
   be promoted automatically.
4. The public-render review still contains editorial queues and conservative
   identity ambiguities that were outside this narrow safety task. Its
   whole-corpus queue includes one duplicate-full-reply group across three
   quote IDs; this is distinct from the zero duplicate full replies in the
   67-row downgraded-projection scope.
5. A separate Google-search acquisition pilot may be useful, but it should be
   designed and approved independently. No Google, OpenAI or other provider
   call was made during this work.
6. Before any deployment, the exact reviewed tree still needs an authorised
   commit/push and controlled deployment gate, including the complete offline
   suite if required for production activation.

READY FOR REVIEW — NOT COMMITTED OR DEPLOYED
