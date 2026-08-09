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
quotations, or inventory the whole archive. It reruns classification only for
candidates already present in the source ledger. Every candidate selected for
positive, admission, or advisory consideration must still resolve to the
recorded MTF document and exact local file SHA-256. Changed or missing files are
marked stale and cannot support a positive conclusion. Exact or variant
positive evidence also requires a valid supporting-passage SHA-256 and a
normalised passage still present in the current document text. The source
inventory may remain unstable: unchanged candidate identities retain positive
evidential value, while negative/no-hit conclusions remain provisional. The
existing package is read only and is resnapshotted before any output is
published; a source mutation aborts publication, and a failed output write
removes the incomplete package. The new ledger records the source ledger's
SHA-256 without recording either private path.

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
MTF documents remain provenance only.
