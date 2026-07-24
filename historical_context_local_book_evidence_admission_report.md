# Local-book historical-context evidence admission report

## Outcome

A clean admission candidate has been built from production commit
`aa0906d7773d6741cd2b0e6b6cf5ddae30386ceb`. It admits exactly five
reviewed local-book source records and exactly four approved Meaning
corrections. The candidate implements the three required fail-closed code
prerequisites, regenerates all affected audits, passes an independent semantic
diff, and produces byte-identical output in two consecutive builds.

The final complete-suite rerun passed with 2,313 tests and zero failures or
errors. No production file, live service, schedule, receipt, history or X
account has been touched by this admission build.

## Editorial authority and source verification

The authoritative editorial decision is
`historical_context_five_sources_four_meaning_corrections_review_updated_20260724T120644Z.md`,
SHA-256
`cff04a652f580b43f411378bac4ac6195d583a90c447fceea02c48cfe1cb79a5`.
All five PDF hashes, corrected-text hashes, source identities, printed pages,
candidate IDs and source IDs were independently rechecked without rescanning
the books or regenerating OCR.

| Quote ID | Admission decision | Inspected source and locator |
|---|---|---|
| `f0d85c…` | exact primary; wording, attribution, publication event, date and historical context | Margaret Thatcher, *The Downing Street Years*, first HarperPerennial edition (1995), printed p. 7 |
| `cac574…` | exact primary; wording, attribution, publication event and date | Margaret Thatcher, *The Path to Power*, HarperCollins (1995), printed p. 29 |
| `4f5e78…` | primary variant; wording variant, attribution, publication event and date | Margaret Thatcher, *The Downing Street Years*, first HarperPerennial edition (1995), printed p. 272 |
| `e259f9…` | primary variant; wording variant, attribution, original speech event and date | *The Collected Speeches of Margaret Thatcher*, ed. Robin Harris, first U.S. edition (1998), printed p. 36 |
| `52f9b9…` | usable reliable secondary evidence only | *Margaret Thatcher: In Her Own Words*, ed. Iain Dale, Biteback (2010), printed p. 79 |

The Collected Speeches title and copyright pages establish the inspected book
as the first U.S. edition of 1998, originally published in Britain and
copyright 1997. For *The Path to Power*, the inspected 1995-edition p. 29
locator is added without overwriting the retained p. 31 citation, which may
refer to another edition.

No PDF, OCR text, page image, private path or extended copyrighted excerpt is
included in the production candidate.

## Approved Meaning corrections

The candidate applies these four values exactly:

1. `f0d85c…` — “Thatcher argued that Britain's long post-war experiment
   with democratic socialism had failed in practice.”
2. `cac574…` — “Thatcher described Nazism and communism as closely related
   forms of socialism.”
3. `4f5e78…` — “Thatcher argued that, other things being equal, excessive
   trade-union power and wage demands unsupported by output contributed to
   unemployment and made British goods uncompetitive.”
4. `e259f9…` — “Thatcher contrasted treating people as numbers with
   recognising them as distinct individuals: unequal, but equally important.”

The Quislings Meaning is unchanged.

## Exact and variant handling

The democratic-socialism and Nazism/communism records are exact primary
wording under authorised typographical normalisation.

The trade-union record remains explicitly a primary variant. The primary text
adds “other things being equal” and says “British goods uncompetitive” where
the stored quotation says “goods uncompetitive”.

The people-as-numbers record remains explicitly a primary variant. The
inspected text differs in “numbers in a state computer”, “We are all unequal”,
and the retained word “quite”. The public quotation corpus was not rewritten.

The Quislings anthology supports secondary wording, attribution and a broad
1978 date only. It has no source-event role, does not establish the original
1978 occasion, and is not primary or resolution-support evidence.

## Required code transition

The candidate makes the smallest reviewed changes:

- `historical_context_source_roles.py` maps curated `exact` matches to `full`
  coverage for the wording claim only. It does not broaden date, attribution,
  event or historical-context coverage.
- `historical_context_public_projection_review.py` accepts a separate
  hash-bound post-v9 transition only when the exact five quote/source/candidate
  bindings and all pinned inputs match. The historical v8/v9 transition
  manifest remains byte-identical.
- `historical_context_reply_semantic_gate.py` pins the reviewed 86-row ledger,
  its 15 blocked records, exact disposition counts and blocked projection.
  Unknown or stale inputs continue to fail closed.

The semantic-review builder records the four primary remediations while
leaving Quislings open. The gate-audit builder consumes the same exact
fail-closed disposition pins instead of maintaining a second stale count.

An independent review found eight legacy exact-curated records whose internal
`claim_coverage.wording` representation also changes from `exact` to `full`
under the general mapper rule. This is a representation normalisation only:
their confidence, roles, public fields, verification wording, rendering and
gate status are unchanged.

## Semantic and gate diff

- Curated source records: 14 → 19.
- Packet corrections: 11 → 15.
- Effective packet semantic changes: exactly four `intended_argument` fields.
- Public reply changes: exactly the five reviewed records.
- Unrelated public semantic changes: zero.
- Historical-context gate: 19 blocked / 591 allowed → 15 blocked / 595
  allowed.
- Gate removals: `4f5e78…`, `cac574…`, `e259f9…`, `f0d85c…`.
- Gate additions: none.
- Quislings remains blocked as `future_correction_needed`, with
  `follow_up_status: remains_open`.
- Gate invariant failures: zero.

Gate membership was recomputed from the completed records; it was not changed
mechanically.

## Corpus and production invariants

- Completed packets: 626 → 626.
- Completed attribution-ineligible packets: 16 → 16.
- Unresolved partition: 6 → 6.
- Ordinary-post cycle: 610 → 610, identical membership and order.
- Quotation text changes: zero.
- Quotation-ID changes: zero.
- Research packets, corpus manifest, runtime-eligible manifest, unresolved
  state and source-recovery sidecar remain byte-identical.
- Semantic-veto enforcement and generated-image-pool configuration are
  unchanged.

The semantic gate remains context-only and does not alter ordinary quote/image
eligibility.

## Deterministic regeneration and rendering

The complete affected builder chain was run twice from identical reviewed
inputs. All 13 generated artefacts were byte-identical, including the
source-role audit, evidence-truth audit, public projection, public source
audit, public render review, semantic ledger and gate audit.

All 626 public renderings completed with zero render failures and zero
blocking item violations. The five reviewed outputs use the approved Meaning,
the correct exact/variant labels, human-readable bibliography and printed-page
locators. Quislings uses safe date-only context and does not invent an
occasion. No private path, PDF hash, OCR path, confidence line or internal
evidence-role diagnostic is exposed.

## Tests

- Clean production baseline: 2,300 passed, zero failures, zero errors, 25
  warnings.
- Focused admission tests: 179 passed.
- Additional exact-scope test: 1 passed.
- First complete candidate pass: 2,309 passed and four stale expectation
  failures.
- The four failures were repaired with scope-bound assertions; their targeted
  rerun passed 40/40.
- Final complete candidate rerun: 2,313 passed, zero failures, zero errors, 25
  warnings.
- Changed Python files compile successfully.
- `git diff --check` passes.

The candidate has passed every offline admission gate and is ready for the
authorised controlled production deployment.

ADMISSION CANDIDATE VALIDATED — READY FOR CONTROLLED DEPLOYMENT
