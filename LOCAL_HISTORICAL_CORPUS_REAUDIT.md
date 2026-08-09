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
quotations, or inventory the whole archive. It selects candidates through the
existing positive/admission/advisory predicate and also selects an old
exact/recorded-variant primary-wording candidate when it retains a numeric MTF
document ID, an agreeing canonical MTF URL, a valid recorded local-file SHA-256,
and a non-empty supporting passage. For each selected candidate it reopens only
the recorded local MTF document, verifies the document identity and exact
local-file SHA-256, extracts the current text, and freshly reruns
contribution-aware matching and source classification. The previous match type,
passage, speaker, classification, acceptance, and confidence are retained only
as recorded discovery history and cannot decide the fresh result. In
particular, an old negative speaker verdict does not veto fresh rematching. An
unchanged file remains identity-valid even when the fresh rematch rejects its
wording or speaker; changed or missing files alone are marked stale. Unselected
candidates do not retain positive proposals from their old semantic fields.

Optional historical metadata uses conservative missing-value semantics.
Case-, spacing-, and punctuation-normalised forms such as `unknown`, `not
known`, `unavailable`, `not available in primary sources`, `none`, `null`,
`n/a`, `unspecified`, and `undated` are excluded from authorised variants and
treated as missing dates, events, and locators. The stored quotation itself is
never discarded by this filter. After word-token and punctuation/typography
normalisation, identical wording is accepted. Otherwise an ordinary variant
must share at least two distinct content tokens, cover at least two thirds of
the larger content-token set for wordings of at most four content tokens (40%
for longer wording), and have SequenceMatcher word-token similarity of at least
0.65 for short wording (0.50 for longer wording). A provenance-bound,
explicitly non-substantive typography/punctuation or editorial-bracket
transformation may pass only when its maintained transformation reproduces the
variant deterministically and similarity is at least 0.50. Each decision
records overlap, similarity, thresholds, shared tokens, and a reason. The same
rule filters variants, admits fresh variant matches, and determines strong
variant evidence. Original packet values remain available in provenance.

Transcript segmentation retains each block's source tag. Explicit role labels
such as `Question`, `Interviewer`, `Prime Minister`, and `Mrs Thatcher` remain
speaker boundaries. A titled personal name or name-plus-outlet remains
recognisable in paragraph/colon transcript structure. An untitled two-to-four
token personal name, optionally followed by a parenthesised outlet, becomes an
interviewer boundary only at a paragraph/list/blockquote contribution boundary
when recognised transcript roles and label/text alternation establish the
context; repeated contextual labels are supporting evidence. Short uppercase
interviewer initials require alternation with an explicit Thatcher label.
Arbitrary title-case phrases, policy headings, acronyms, and short all-capital
text are not person labels. Ordinary `h2`/`h3` section headings never change the
active speaker, and a personal name in `h2`/`h3` does not establish authorship.
This prevents interviewer repetitions from inheriting a Thatcher speaker label
while preserving explicit document authorship across article and speech
section headings. Fresh matching first selects exact or authorised-variant
support inside a verified Thatcher contribution; only when none exists does a
stronger interviewer or unverified occurrence become the diagnostic result.
Cross-speaker clause assembly remains rejected.

The reclassification runner also retains each contribution block's exact CSS
class tokens and any recognised attribution tokens on ancestors through the
selected article node. Matching is case-insensitive but exact-token only:
`mt` marks Margaret Thatcher contribution text, `intmt` marks a Thatcher
speaker/contribution label, `nonmt` marks non-Thatcher contribution text,
narration, or reporting, and `intnonmt` marks a non-Thatcher
speaker/contribution label. Similar strings such as `notmt`, `mt-note`, and
`nonmtish` have no attribution meaning. Simultaneous MT and non-MT polarity in
the applicable ancestry is a conflict and fails closed. `h2` and `h3` remain
structural rather than speaker elements.

Archive attribution has first precedence: an explicit `mt` or `nonmt` content
class overrides active speaker state, document authorship, and textual label
heuristics. An `intmt` or `intnonmt` label controls immediately following
unmarked contribution blocks until another explicit archive boundary; its
visible wording is not guessed. Maintained transcript labels and contextual
speaker heuristics are used next. Verified whole-document authorship is used
only when no recognised archive attribution applies to contribution text.
Consequently `mt` is direct Thatcher wording even under generic or
non-Thatcher document metadata, while `nonmt` is never direct Thatcher wording
even in a Thatcher-authored document. Documents without any of the four exact
tokens retain the previous transcript-label and document-author fallbacks
unchanged.

Text in `nonmt` is not removed. An exact or authorised-variant occurrence is
retained with its supporting passage and context as reported/secondary
diagnostic evidence, but it cannot set `accepted_as_primary_evidence` or
verified speaker attribution. A stronger `nonmt` exact occurrence also cannot
displace a valid Thatcher variant in `mt`, and clauses are never assembled
across `mt` and `nonmt` contributions. A candidate whose best direct match is
only in `nonmt` receives `reverified_rejected_archive_nonmt`; its document
identity remains valid rather than stale.

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
Direct MTF identity is recorded in two sets. The narrow
`current_occurrence_direct_mtf_document_ids` set comes only from the packet
stable locator and trustworthy packet/public source-role rows explicitly bound
by locator, exact day, or matching date and event. The broad
`known_evidence_direct_mtf_document_ids` set retains direct identities from all
evidence lanes for provenance, including research and model-proposed leads, but
never authorises current-occurrence replacement. Same-occurrence source
refinements may use only the narrow set; exact-day event descriptions may also
establish the same occurrence when their substantive subjects agree after
generic House of Commons, HC, speech, debate, statement, report, and motion
wrappers are removed. Clearly different dates/events remain additional
occurrences; ambiguous same-day identities are neutral manual-review cases.

Reclassification summaries preserve the identity counters and distinguish
three positive totals. `recorded_positive_candidates_remaining_valid` counts
only previously positive candidates that are identity-valid and freshly
accepted. `reverified_positive_candidate_count` counts every selected candidate
freshly accepted. `positive_candidates_remaining_valid` counts every
identity-valid, freshly reverified and accepted candidate regardless of its old
ledger status. Summaries also retain placeholder-variant rejections and the
quote IDs whose evidence is complete but whose maintained gate explicitly
requires a separate semantic/Meaning review. That advisory flag never changes
or bypasses the gate. Five candidate-level markup counters report only
documents opened by the bounded reclassification: candidates containing
recognised archive attribution markup; accepted candidates whose selected
wording is attributed through `mt` or `intmt`; rejected candidates whose direct
match is in `nonmt`; candidates retaining any reported/secondary `nonmt` match
(including one alongside an accepted `mt` match); and candidates with a
matching contribution whose applicable archive ancestry has conflicting
polarity. These counters never inventory or count all markup elements in the
archive.
