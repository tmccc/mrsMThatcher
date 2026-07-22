# Historical-context public source deduplication

## Executive summary

Public historical-context replies now group evidence records by deterministic
canonical source identity before rendering. The known 14 October 1988
conference-speech packet renders Margaret Thatcher Foundation document
`107352` once, with one readable title and one canonical raw URL. Public output
does not show source-role diagnostics or Markdown links. Internal rendering
retains every source collection, role, evidence target, supporting passage,
quality classification, confidence dimension, provenance record and
deduplication diagnostic.

A separate deterministic review program now writes the exact public reply for
all 626 completed packets, together with the internal-record-to-canonical-group
mapping and compact editorial queues. Review of that artifact found and fixed
three public title-cleanup defects. It also found 28 plausible same-document
cases that lack enough identity evidence to merge safely; they remain separate
and now carry a non-blocking internal ambiguity warning.

This was a formatter and source-identity change only. No quotation, quotation
ID, research conclusion, evidence record, role, confidence value, semantic
veto, eligibility decision or production posting state was changed. The full
corpus rendering audit and all affected focused tests pass. At the user's
request, the roughly 12-minute complete offline suite was not rerun after the
final adversarial hardening. The live service remained on the same wrapper and
Python processes throughout.

## Root cause

The old `public_sources()` implementation grouped only exact `(title, URL)`
pairs and then truncated the result to two entries. It could not recognise that
an informative source-event locator and a direct archive-document record named
the same underlying work. Separately, the public and internal formatters shared
the diagnostic source-line renderer, which printed claim-role parentheses and
passed saved URLs through verbatim.

For document `107352`, the source-role sidecar correctly retained two evidence
records:

- a recovered direct Margaret Thatcher Foundation document record supporting
  wording and attribution; and
- a dated Conservative Party Conference locator supporting wording,
  attribution, source event and date.

Those records remain separate internally. Only their public presentation is
combined.

## Canonical source-identity algorithm

Identity is selected in this order:

1. Margaret Thatcher Foundation document identity. Document numbers are
   extracted from canonical URLs, titles, direct identifier fields, stable
   locators and nested evidence metadata. Only exact Foundation authority
   signals are accepted; lookalike subdomains and conflicting publishers do
   not qualify. Valid host aliases resolve to
   `margaret_thatcher_foundation:document:<number>`.
2. Other immutable repository identifiers. Publisher/archive identity is
   normalised and combined with the strongest available document, speech,
   record, catalogue or archive reference. Meaningful edition, volume, chapter,
   page and folio distinctions are retained, including `THCR` and `CCOPR`
   locators.
3. Canonical authoritative URL. Hostnames are lower-cased; the Foundation's
   `www` and bare hosts are unified; fragments and tracking-only query
   parameters are removed; meaningful query parameters and internal path
   structure are preserved; redundant trailing slashes are removed. An
   original URL is preferred to `t.co`, and malformed, nested, credentialed and
   provider-redirect URLs are not emitted.
4. Conservative bibliographic identity. This requires a named publisher or
   archive plus an exact normalised title and date/locator. Similar titles alone
   never merge.
5. Unique source-record identity. If none of the stronger identities is
   available, the record stays separate.

The one metadata bridge used by the known case is deliberately strict. A
model-proposed lead can supply identity only when its exact normalised subject,
document number and saved raw-response provenance match an explicit renderable
document record in the same packet. It contributes no evidence role or claim.
Uncorroborated or conflicting leads remain separate and are reported.

## Conservative merge rules

Public records merge only when a shared immutable document identity, explicit
record identity, canonical URL alias or sufficiently complete bibliographic
identity establishes that they are the same work. The implementation preserves
separate groups for:

- different Margaret Thatcher Foundation document numbers;
- conflicting repository identifiers;
- a primary transcript and a memoir;
- distinct publications or editions;
- identical titles backed by different authoritative document identities;
- composite archive locators; and
- any metadata lead whose identity or provenance is uncertain.

When one URL-only record and one explicit-ID record share the same canonical
URL, the URL-only record aliases to the stronger identity. If two conflicting
explicit IDs share a URL, both sources remain separate, the ambiguity is
recorded, and the URL is printed only once.

Within a valid merge group, roles and evidence targets are unioned for internal
use. Public display chooses the most informative verified title and the most
precise supported date, adds a useful document number, and emits one canonical
URL. The role union is never printed publicly.

## Document 107352 before and after

Former public output:

```text
Context — Conservative Party Conference, 14 October 1988.

Meaning — Government exists to serve the people and foster an environment for free enterprise, rather than to control individuals or dominate the economy.

Verification — Exact wording verified

Source (wording, attribution, source event, date) — Margaret Thatcher Foundation Archive, Speech to Conservative Party Conference, October 14, 1988

Source (wording, attribution) — Margaret Thatcher Foundation document 107352
https://margaretthatcher.org/document/107352
```

Current public output:

```text
Context — Conservative Party Conference, 14 October 1988.

Meaning — Government exists to serve the people and foster an environment for free enterprise, rather than to control individuals or dominate the economy.

Verification — Exact wording verified

Source — Margaret Thatcher Foundation, Speech to Conservative Party Conference, 14 October 1988 (Document 107352)
https://www.margaretthatcher.org/document/107352
```

The public formatter returns one source group and one URL. The internal
formatter returns both renderable records, their original roles and passages,
and a diagnostic group containing both source IDs under
`margaret_thatcher_foundation:document:107352`.

## Full-corpus public-render audit

`historical_context_public_source_deduplication_audit.json` was produced by the
actual public formatter over all 626 completed packets. The audit is
deterministic: two isolated runs were byte-identical, and the final artifact has
SHA-256
`bf8fa8bf19a80482524e261449dd3af716e1c8061429fe61b63f580f941cbde2`
(4,688,101 bytes).

Key results:

- completed packets rendered: 626/626;
- attribution-eligible quotations: 610;
- attribution-ineligible completed packets: 16;
- unresolved records: 6;
- internal renderable source records: 914;
- distinct public canonical sources: 563;
- packets with no reliable source: 107 overall, 96 among eligible quotations;
- former public duplicate identity groups: 26;
- former duplicate canonical URL groups: 26;
- former repeated document-number groups: 25;
- current duplicate identity groups: 0;
- current duplicate canonical or raw URLs: 0;
- current repeated document-number groups: 0;
- public role leakage: 0;
- Markdown links: 0;
- malformed or nested URLs: 0;
- identical public entries under different canonical identities: 0;
- invalid merge conflicts: 0;
- render or invariant failures: 0.

The old two-source cap produced 588 public records. Canonical rendering produces
563: 26 duplicate records disappear, while one genuinely distinct third source
that the cap previously hid is now retained.

## All-quote public-render review

`historical_context_public_render_review.py` loads the validated corpus,
passes every completed packet through `format_context_reply_public()` with the
same `4000/true/true/true` formatter options as the posting path, and writes
`historical_context_public_render_review.txt`. It neither imports the bot nor
opens a reply store or network client. Its tests block socket operations and
verify all input hashes before and after two independent builds.

The 1,781,340-byte final review artifact has SHA-256
`ba7c809f73e53932690277fce41cb104238b1f000a36c3b13fb9dc11286cf7bf`.
Two independent CLI runs were byte-identical. It contains all 626 quotations,
all 626 exact public replies, provenance hashes for the input and implementation
files, canonical identity mappings, automated blockers, and compact review
queues. Automated validation passed with zero item or global blockers.

The review found and corrected these presentation defects without changing any
internal source record:

- `June 05 1987` now renders publicly as `5 June 1987`;
- `July 04 1977` now renders publicly as `4 July 1977`;
- `1983 Jun 5` now renders publicly as `5 June 1983`;
- a duplicated terminal `| The Independent | The Independent` suffix now
  renders once; and
- the exact virtual `HC Deb 22 November 1990` locator and its one compatible
  official Commons transcript URL now render as one Hansard source while all
  three internal records remain available.

The remaining review queues are data/audit limitations rather than formatter
failures: 107 packets have no reliable renderable source (96 are eligible), 165
packets contain a URL-less source, 187 use a generic Foundation document title,
and 205 reuse an identity whose available display title varies between packets.
The 468 generic contexts are also evidence-scope limitations: none has source
event or date evidence available to the formatter. One identical three-reply
group consists solely of the correct no-source/research-incomplete fallback.

## Ambiguities deliberately left unmerged

The audit records 68 ambiguity diagnostics across 54 packets without treating
them as rendering failures:

- 26 uncorroborated metadata-locator records across 15 packets lack matching
  saved provenance;
- 6 metadata/document conflicts across 3 packets include the known
  `107201`/`107200`, `108284`/`108285` and `103103`/`103105` disagreements;
- 7 packets legitimately contain more than one explicit archive document;
- 1 composite Foundation/Hansard locator remains a distinct source; and
- 28 packets contain exactly one explicit Foundation document plus a same-date,
  strong-primary, URL-less Foundation locator that may describe the same work,
  but lacks the deterministic document/provenance link required for merging.

No ambiguous identity was guessed. Different document numbers remain separate,
and uncertain sources are visible in internal diagnostics.

## Public versus internal behaviour

Public/X rendering now:

- canonicalises and deduplicates source identity;
- prints concise repeated `Source —` entries for genuinely distinct sources;
- suppresses roles, evidence IDs, source IDs and audit classifications;
- emits each raw canonical URL on its own line at most once;
- retains the existing concise public verification labels; and
- continues to omit the detailed public `Confidence —` line.

Internal rendering now:

- keeps every visible renderable evidence record and its complete role set;
- returns deep copies of the original, recovered, researched, curated, virtual,
  model-lead and renderable source collections;
- retains evidence targets, supporting passages, quality, rationale and
  provenance;
- returns confidence dimensions unchanged; and
- exposes canonical identities, merge groups and ambiguity diagnostics.

Raw provider redirects are retained in that structured internal audit payload,
but safe canonical URLs are used in visible internal text so internal previews
also remain renderable.

## Raw URL verification

Regression coverage includes Foundation `www`/bare-host variants, trailing
slashes, fragments, tracking parameters, meaningful query parameters,
`t.co`/original preference, multiple-original and multiple-redirect ambiguity,
Markdown-shaped links, nested URLs, duplicated schemes, whitespace/control
characters, inline URLs and meaningful double path separators. The corpus audit
found no Markdown link syntax, inline or nested URL, duplicated scheme,
provider redirect, unsafe URL or duplicate canonical URL in public output.

## Corpus and evidence preservation

The following tracked inputs are byte-for-byte unchanged from the pre-change
baseline:

- `mrsMThatcher.txt`:
  `10310a9d62c03a87f2c1e55fa10286d1413216b8c0cb34cb0dbe4b3c12f19bee`;
- `quote_analysis.json`:
  `e53b6e1448335c060f941ddd90cfb8035d12014b691ac93036f606832408d39a`;
- `research_packets.json`:
  `307b01f0c854ad8e16a50ed399bfa0cbd5f4b8c3d00709bfe02100a289143611`;
- `corpus_manifest.json`:
  `81f6b2974c30d5810afc74c24704f5ee3d3868a6b2d94859cad2fa8cebce12da`;
- source-role audit:
  `afda5152c18774bd2e2db39a001c4b7ea2a712f829e855dbe31579c769052053`;
- `unresolved_quotes.json`:
  `6acb4d2dede398f74e488902c62c672437db8721f6f75c9adebdf323889feb4f`;
- final unresolved status:
  `2045bdee4dc90ebab4720125cf0b442c033feb81a940130537fdfd1d3622347f`.

The completed quote ID/text signature is
`235eb63076bbc791c933e951525fbcde09537872241019e06d27fef6f86445eb`.
The eligible quote ID/text signature is
`e6bac37c9eac80134089896a84447ea48b621069379011acc07a94d0b4763c40`.
The 610-member runtime/cycle signature is
`9ad73fda8a0863cd1ba99fea65d1ceccf506f77d4b39742214e5934efd12e7ed`.
All 610 eligible IDs and texts remain eligible and in the cycle; all 16
ineligible completed packets and all 6 unresolved records remain excluded.

The inactive v3 semantic-veto candidate fingerprints the whole formatter file.
The formatter edit correctly made that derived fingerprint stale, so the
existing offline `prepare-v3-shadow` command refreshed its five derived
metadata files. It made no network or AI call and did not reload the runtime.
Before/after semantic signatures are identical:

- 22,066 pair decisions:
  `4556a6de623339a234156558b639c6b519d8a27992df42f20a127c37b0196894`;
- 610 quote texts:
  `523bf2314f7c2b5ebced81658768af357ade74a54c0388a99bd8df44fd4a5c0f`;
- runtime IDs:
  `806ebccf3abfb7a528e5fc3150bb271232c966ff8616ddcc8815dd2fc0faab65`;
- allow/veto coverage:
  `570df47ae8a47c0445ec393c3bee9f6b80567563d32dc07754c8d41726e4b479`;
- eligibility decisions and aliases:
  `2079e67e29d0059e05b355433ed11acace58554eea0a996051cef78f6253f02e`.

## Files modified

Formatter and identity implementation:

- `historical_context_source_roles.py`
- `historical_context_formatter.py`
- `historical_context_reply_schema.json`
- `mrs_log_digest.py`

Audit and report artifacts:

- `historical_context_public_source_audit.py`
- `historical_context_public_source_deduplication_audit.json`
- `historical_context_public_render_review.py`
- `historical_context_public_render_review.txt`
- `historical_context_public_source_deduplication_report.md`

Tests:

- `tests/test_historical_context_public_source_deduplication.py`
- `tests/test_historical_context_public_render_review.py`
- `tests/test_historical_context_source_roles.py`
- `tests/test_historical_context_reply.py`
- `tests/test_digest_reply_observability.py`
- `tests/test_unit_helpers.py`

Derived inactive-candidate metadata refreshed only for source fingerprints and
audit timing:

- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/checksums.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/material_veto_v3_shadow_manifest.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/runtime_eligible_quote_manifest.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/v3_shadow_manifest_audit.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/v3_shadow_manifest_audit.md`

The pre-existing untracked source-compression pilot script, test and research
directory were not modified. The live bot's pre-existing untracked
`regular_post_receipt.json` also remained byte-for-byte unchanged at SHA-256
`576fba844e98d8540eb677e249f5e1d3fa6bdf3897481703d608c7c42f00d223`.

## Commands and validation results

- Canonical-source regression module, including adversarial identities, URL
  safety, warning-only uncertain identity cases, corpus preservation and the
  isolated deterministic audit: **53 passed in 21.04 seconds**.
- Independent all-quote review-program builds, deterministic text rendering,
  provenance/configuration recording, output-path safety, network blocking and
  immutable-input checks: **3 passed in 19.30 seconds**.
- Affected source-role, historical-context reply and digest modules, plus the
  targeted production-path and inactive-manifest validation cases:
  **165 passed**, 9 warnings, in 17.62 seconds.
- Isolated full-corpus public-render audit: **626/626 rendered**, ready true,
  zero blocking, invariant, render, duplicate-display or source-identity
  violations; two runs byte-identical.
- Independent all-quote text artifacts: **626/626 exact public replies**, zero
  blockers, two CLI runs byte-identical.
- The complete offline suite had passed before the final adversarial hardening,
  but it was deliberately not rerun afterward in accordance with the user's
  instruction to avoid the roughly 12-minute suite. The focused runs above
  cover every changed runtime module and the full 626-packet renderer.
- Targeted Python compilation: passed.
- `git diff --check`: passed.

Validation audit runs used temporary/copied state. The isolated audit test
blocked socket connection/send methods and verified that every copied input
hash remained unchanged; the final report artifact was then regenerated from
the same read-only canonical inputs. No test contacted X or an external AI
provider, uploaded media, created a production receipt, or changed analytics.
Final copies of the audit JSON, this report and the all-quote review text were
written to `/home/tonym/Dropbox/new`; each copy matched its workspace SHA-256.

## Production isolation proof

Before work and after all tests/report preparation, `mrsMThatcher.service`
retained:

- active state: `active/running`;
- wrapper/MainPID: `1033185`;
- Python child PID: `1033186`;
- start time: `Tue 2026-07-21 22:25:43 BST`;
- restart count: `0`;
- control group:
  `/user.slice/user-1000.slice/user@1000.service/app.slice/mrsMThatcher.service`.

No service was stopped, restarted, reloaded or signalled. No X post was made.
No environment file, production receipt, posting history, ledger, schedule,
analytics file, source-role evidence or research packet was altered. No commit,
push or deployment occurred.

## Remaining risks

- New archives with identifier conventions not represented here will fall back
  to URL, conservative bibliography or unique-record identity until a specific
  parser is added.
- The 68 recorded ambiguity diagnostics remain intentionally unresolved; this
  includes the 28 newly surfaced possible-same-document cases and favours a
  duplicate public entry over a false merge when identity evidence is
  insufficient.
- The inactive v3 candidate continues to fingerprint the whole formatter, so a
  future formatter-only change will require the same deterministic metadata
  refresh before its strict runtime-validation test can pass.

READY FOR COMMIT, PUSH AND CONTROLLED DEPLOYMENT
