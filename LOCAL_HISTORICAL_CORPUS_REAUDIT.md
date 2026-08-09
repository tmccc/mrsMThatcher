# Local historical-context corpus re-audit

`historical_context_local_corpus_reaudit.py` performs a bounded advisory search
of the exact current 611-member eligible quotation corpus against an
operator-owned Thatcher-archive-2 mirror. It reuses the maintained eligible
corpus validator, query-fragment policy, local mirror/index, MTF document
validator, passage matcher, source classifier, semantic gate, and wording
comparison helpers.

Prepare a new empty directory outside every checkout, set it to mode `0700`,
and export these private environment variables without adding them to tracked
configuration:

```bash
export MRS_MTHATCHER_LOCAL_ARCHIVE_ROOT=/operator/private/archive-root
export MRS_HISTORICAL_REAUDIT_RUN_DIR=/operator/private/new-empty-run
python3 historical_context_local_corpus_reaudit.py --execute
```

The process denies socket/DNS entry points, does not construct a cloud search
backend, and writes only the seven private advisory outputs named by the
runner. It records numeric-document inventories before and after the search;
an inventory change makes the package non-reproducible and incomplete. The
outputs never contain the private archive or run-directory path and must not be
added to Git.

## Reclassify an existing package without discovery

To reuse an existing candidate ledger after an unstable source-run inventory,
prepare another empty mode-`0700` private directory and run:

```bash
export MRS_MTHATCHER_LOCAL_ARCHIVE_ROOT=/operator/private/archive-root
python3 historical_context_local_corpus_reaudit.py \
  --reclassify-existing /operator/private/existing-run \
  --output-dir /operator/private/new-empty-run
```

This mode reads current authoritative historical inputs, but does not prepare
search queries, build an index, run archive discovery across the 611
quotations, or inventory the whole archive. For every candidate selected by the
existing positive/admission/advisory predicate, it reopens only the recorded
local MTF document, verifies the document identity and exact local-file
SHA-256, extracts the current text, and freshly reruns contribution-aware
matching and source classification. The previous match type, passage, speaker,
classification, acceptance, and confidence are retained only as recorded
discovery history and cannot support a new proposal. An unchanged file remains
identity-valid even when the fresh rematch rejects its wording or speaker;
changed or missing files alone are marked stale. Unselected candidates do not
retain positive proposals from their old semantic fields.

Optional historical metadata uses conservative missing-value semantics.
Case-, spacing-, and punctuation-normalised forms such as `unknown`, `not
known`, `unavailable`, `not available in primary sources`, `none`, `null`,
`n/a`, `unspecified`, and `undated` are excluded from authorised variants and
treated as missing dates, events, and locators. The stored quotation itself is
never discarded by this filter. A recorded variant must share meaningful
lexical wording with the stored quotation and have positive wording similarity
before it can become primary evidence. Original packet values remain available
in provenance.

Transcript segmentation recognises conservative standalone labels, including
`Question`, `Interviewer`, `Prime Minister`, `Mrs Thatcher`, titled personal
names, and names with an optional outlet. Any explicit non-Thatcher name starts
an `other` contribution; an ambiguous heading starts an unverified
contribution. This prevents interviewer repetitions from inheriting a Thatcher
speaker label, while a Thatcher-authored speech or article with no transcript
labels can still rely on explicit document authorship.

The source inventory may remain unstable: only freshly accepted candidates
retain positive evidential value, while negative/no-hit conclusions remain
provisional. The existing package is read only and is resnapshotted before any
output is published; a source mutation aborts publication, and a failed output
write removes the incomplete package. The new ledger records the source
ledger's SHA-256 without recording either private path.

The semantic advisory categories include `additional_primary_occurrence` for
another valid date/event that does not disprove the current occurrence,
`exact_excerpt_confirmation` when the stored quotation is an exact excerpt of
a longer authoritative passage, and
`attribution_or_noncontiguous_match_review` for attribution-sensitive or
assembled wording. Only `contradictory_evidence` represents a genuine conflict
with an existing canonical claim. Date, event, or verified-text corrections
require evidence about the current occurrence or transcription; they are not
inferred merely from another occurrence or a longer source passage.
Current-occurrence identity comes from the packet stable locator or a
source-role record directly bound to the packet's date and event; other known
MTF documents remain provenance only. Same-occurrence source refinements are
also recognised when the candidate is the already-known direct MTF document
and its exact date agrees, or when exact-day event descriptions share the same
substantive subject after generic House of Commons, HC, speech, debate,
statement, report, and motion wrappers are removed. Clearly different
dates/events remain additional occurrences; ambiguous same-day identities are
neutral manual-review cases.

Reclassification summaries preserve the identity counters and additionally
record how many candidates required and completed fresh semantic
reverification, how many were freshly accepted or rejected, recorded versus
reverified positive counts, placeholder-variant rejections, and the quote IDs
whose evidence is complete but whose maintained gate explicitly requires a
separate semantic/Meaning review. That advisory flag never changes or bypasses
the gate.
